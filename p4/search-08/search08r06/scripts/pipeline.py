#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pipeline.py — 系统性文献检索与纳排筛选的确定性账本（仅标准库）。

本脚本只做“账本”工作：发现输入、抽取记录、去重、校验证据子串、闭合计数、
生成可编辑产物。**科学判断（decision / reason_code / 双人意见 / 仲裁）由
Agent 依据题面完成并写入 judgments.json**；脚本绝不编造决策、DOI、标题或数值。

子命令：
  skeleton   扫描 /input，落盘 partial 骨架 result.json + plan.json / records.json / inventory.json
  normalize  对 records.json 去重，产出 dedup.json + judgments_template.json
  finalize   合并 judgments.json，校验证据、闭合计数，写 result.json + 可编辑产物
  selfcheck  校验 result.json 与各中间产物，逐项打印 PASS/FAIL

所有路径默认可通过 --input / --output / --work 覆盖；缺省为 /input、/app/output、/app/work。
"""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_TEXT_FILE = 2 * 1024 * 1024  # 单文本文件 2MB 上限，超限跳过并记录

CONTRACT_KEYS = ("output_contract", "outputContract", "result_schema", "result_fields", "output")
LIST_HINT = re.compile(r"(s$|list|records|entries|items|results|candidates|criteria|fields|outputs|tags|authors|sources|errors|degradations|pending|conflicts|notes|queries)", re.I)


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(s):
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = s.replace("\u00a0", " ").replace("\ufeff", "")
    return re.sub(r"\s+", " ", s).strip()


def norm_key(s):
    return _norm(s).lower()


def first_nonempty(obj, keys):
    if not isinstance(obj, dict):
        return None
    for k in keys:
        if k in obj and obj[k] not in (None, "", [], {}):
            return obj[k]
    return None


def read_text(path):
    """utf-8-sig / utf-8 / latin-1 容错读取；返回 (text, encoding) 或 (None, None)。"""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None, None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    return None, None


def load_json(path):
    txt, _ = read_text(path)
    if txt is None:
        return None
    try:
        return json.loads(txt)
    except Exception:
        return None


def write_json(path, obj, sort=True):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=sort)
        f.write("\n")


def walk_files(root):
    """返回 root 下所有常规文件（绝对路径）；超限记录到 out_big。"""
    files, big = [], []
    if not os.path.isdir(root):
        return files, big
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            if size > MAX_TEXT_FILE and p.lower().endswith((".txt", ".md", ".json", ".jsonl", ".csv", ".tsv", ".ris", ".bib", ".xml", ".yaml", ".yml")):
                big.append({"path": p, "size": size, "reason": "text file exceeds 2MB, skipped"})
                continue
            files.append(p)
    files.sort()
    big.sort(key=lambda x: x["path"])
    return files, big


# --------------------------------------------------------------------------- #
# 记录抽取
# --------------------------------------------------------------------------- #
RECORD_KEYS_TITLE = ("title", "ti", "标题", "题名", "article_title", "paper_title")
RECORD_KEYS_ABS = ("abstract", "ab", "摘要", "abstract_text", "summary")
RECORD_KEYS_DOI = ("doi", "pubmed_doi", "pmid_doi")
RECORD_KEYS_AUTHORS = ("authors", "author", "au", "authors_list", "creator")
RECORD_KEYS_YEAR = ("year", "py", "publication_year", "date", "pub_year")


def _is_record_like(d):
    if not isinstance(d, dict):
        return False
    keys = {str(k).lower() for k in d.keys()}
    has_title = any(k in keys for k in RECORD_KEYS_TITLE)
    has_doi = any(k in keys for k in RECORD_KEYS_DOI)
    has_abs = any(k in keys for k in RECORD_KEYS_ABS)
    return (has_title or has_doi) and (has_title or has_abs or has_doi)


def _collect_records(obj, path, out, depth=0):
    if depth > 8:
        return
    if isinstance(obj, dict):
        if _is_record_like(obj):
            out.append((obj, path))
            return
        for k, v in obj.items():
            _collect_records(v, path + "." + str(k), out, depth + 1)
    elif isinstance(obj, list):
        rec_items = [(i, x) for i, x in enumerate(obj) if isinstance(x, dict) and _is_record_like(x)]
        dict_items = [x for x in obj if isinstance(x, dict)]
        if rec_items and len(rec_items) >= max(1, len(dict_items) // 2):
            for i, x in rec_items:
                out.append((x, f"{path}[{i}]"))
            return
        for i, x in enumerate(obj):
            _collect_records(x, f"{path}[{i}]", out, depth + 1)


def _parse_csv(text):
    """自动嗅探分隔符的 CSV/TSV；首行为表头。"""
    first_line = text.split("\n", 1)[0] if text else ""
    delim = "\t" if first_line.count("\t") > first_line.count(",") else ","
    try:
        rows = list(csv.DictReader(io.StringIO(text), delimiter=delim))
    except Exception:
        return []
    return rows


def _parse_jsonl(text):
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            out.append(obj)
        elif isinstance(obj, list):
            out.extend([x for x in obj if isinstance(x, dict)])
    return out


def _parse_ris(text):
    """轻量 RIS：TI/AB/DO/AU/PY 字段；以 ER 分条。"""
    recs, cur = [], None
    field_map = {"TI": "title", "T1": "title", "AB": "abstract", "N2": "abstract",
                 "DO": "doi", "AU": "authors", "A1": "authors", "PY": "year", "Y1": "year"}
    for raw in text.splitlines():
        if len(raw) >= 2 and raw[:2] == "ER":
            if cur:
                recs.append(cur)
            cur = None
            continue
        m = re.match(r"^([A-Z0-9]{2})\s*[-:]\s?(.*)$", raw)
        if m and cur is not None:
            tag, val = m.group(1).upper(), m.group(2).strip()
            if tag in field_map:
                key = field_map[tag]
                if key == "authors":
                    cur.setdefault("authors", []).append(val)
                else:
                    cur[key] = (cur.get(key, "") + " " + val).strip()
        elif m and cur is None:
            cur = {}
            tag, val = m.group(1).upper(), m.group(2).strip()
            if tag in field_map:
                key = field_map[tag]
                if key == "authors":
                    cur.setdefault("authors", []).append(val)
                else:
                    cur[key] = val
    if cur:
        recs.append(cur)
    return recs


def _parse_bib(text):
    """轻量 BibTeX：@type{key, field={...}}。"""
    recs = []
    for m in re.finditer(r"@\w+\s*\{[^,]*,(.*?)\}", text, re.S):
        body = m.group(1)
        rec = {}
        for fm in re.finditer(r"(\w+)\s*=\s*[\{|\"](.*?)[\}|\"]\s*,?", body, re.S):
            key, val = fm.group(1).lower(), fm.group(2).strip()
            key = {"title": "title", "abstract": "abstract", "doi": "doi",
                   "author": "authors", "year": "year"}.get(key, key)
            if key == "authors":
                rec.setdefault("authors", val.replace(" and ", "; ").split("; "))
            else:
                rec[key] = val
        if rec.get("title") or rec.get("doi"):
            recs.append(rec)
    return recs


def parse_material_file(path):
    """按扩展名解析题录文件，返回 dict 列表。"""
    ext = os.path.splitext(path)[1].lower()
    txt, enc = read_text(path)
    if txt is None:
        return [], f"unreadable ({os.path.basename(path)})"
    if ext == ".json":
        obj = load_json(path)
        found = []
        if obj is not None:
            _collect_records(obj, os.path.basename(path), found)
        return [x[0] for x in found], None
    if ext == ".jsonl":
        return _parse_jsonl(txt), None
    if ext in (".csv", ".tsv"):
        return _parse_csv(txt), None
    if ext == ".ris":
        return _parse_ris(txt), None
    if ext == ".bib":
        return _parse_bib(txt), None
    if ext in (".md", ".txt"):
        # 标题/摘要嵌入纯文本时无法自动可靠切分，交给 Agent 阅读；这里仅提示。
        return [], "plain text not auto-parsed (agent should read it)"
    return [], f"unsupported extension {ext}"


def normalize_record(raw, record_id, locator):
    title = first_nonempty(raw, RECORD_KEYS_TITLE)
    abstract = first_nonempty(raw, RECORD_KEYS_ABS)
    doi = first_nonempty(raw, RECORD_KEYS_DOI)
    authors = first_nonempty(raw, RECORD_KEYS_AUTHORS)
    year = first_nonempty(raw, RECORD_KEYS_YEAR)

    if isinstance(authors, str):
        authors = [a.strip() for a in re.split(r"[;,]", authors) if a.strip()]
    elif isinstance(authors, (list, tuple)):
        authors = [str(a).strip() for a in authors if str(a).strip()]
    else:
        authors = []

    return {
        "record_id": record_id,
        "title": _norm(title) if title is not None else None,
        "abstract": _norm(abstract) if abstract is not None else None,
        "doi": _norm(doi) if doi is not None else None,
        "authors": authors,
        "year": _norm(year) if year is not None else None,
        "source_file": locator,
        "locator": locator,
        "raw": raw,
    }


def content_hash(rec):
    base = norm_key(str(rec.get("doi") or "")) + "|" + norm_key(str(rec.get("title") or ""))
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def _sim(a, b):
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio()


# --------------------------------------------------------------------------- #
# skeleton
# --------------------------------------------------------------------------- #
def cmd_skeleton(args):
    work, output, input_dir = args.work, args.output, args.input
    os.makedirs(work, exist_ok=True)
    os.makedirs(output, exist_ok=True)

    errors, degradations, sources = [], [], []
    files, big = walk_files(input_dir)
    degradations.extend(big)

    case_path = None
    task_path = None
    for p in files:
        b = os.path.basename(p).lower()
        if b == "case.json":
            case_path = p
        elif b in ("task.md", "task.txt"):
            task_path = p
    if case_path is None:
        # 退而求其次：找含 case_id 或 output_contract 的 json
        for p in files:
            if p.lower().endswith(".json"):
                obj = load_json(p)
                if obj and (isinstance(obj, dict) and any(k in obj for k in CONTRACT_KEYS + ("case_id", "caseId", "id"))):
                    case_path = p
                    break
    if task_path is None:
        for p in files:
            if p.lower().endswith(".md"):
                task_path = p
                break

    case_obj = load_json(case_path) if case_path else None
    task_text = read_text(task_path)[0] if task_path else None

    # case_id
    case_id = None
    if isinstance(case_obj, dict):
        case_id = first_nonempty(case_obj, ("case_id", "caseId", "id", "challenge_id"))
    if not case_id and task_text:
        m = re.search(r"challenge_id[:：]\s*[`\"]?([A-Za-z0-9\-_]+)", task_text)
        if m:
            case_id = m.group(1)
    if not case_id and case_path and isinstance(case_obj, dict):
        case_id = os.path.splitext(os.path.basename(case_path))[0]
    if not case_id:
        case_id = "unknown"

    # 坏 JSON / 不可读的 case.json 要记录，不静默
    if case_path and case_obj is None:
        degradations.append({"file": case_path, "note": "case.json 无法解析为 JSON（坏 JSON/坏编码），已降级"})

    # research question
    rq = None
    if isinstance(case_obj, dict):
        rq = first_nonempty(case_obj, ("research_question", "question", "researchQuestion", "topic", "title"))
    if not rq and task_text:
        m = re.search(r"(?:研究问题|Research question)[：:]\s*(.{4,200})", task_text, re.I | re.S)
        if m:
            rq = m.group(1).strip()

    # output_contract
    contract = {}
    if isinstance(case_obj, dict):
        for k in CONTRACT_KEYS:
            if k in case_obj and isinstance(case_obj[k], dict):
                contract = case_obj[k]
                break
    contract_defaults = {}
    for k, v in contract.items():
        contract_defaults[k] = [] if LIST_HINT.search(str(k)) else None

    # records from case.json + materials
    raw_records = []
    if isinstance(case_obj, dict):
        _collect_records(case_obj, os.path.basename(case_path) if case_path else "case.json", raw_records)
    material_files = [p for p in files if p not in (case_path, task_path)]
    for p in material_files:
        try:
            recs, note = parse_material_file(p)
        except Exception as e:  # noqa: BLE001
            recs, note = [], f"parse error: {e}"
            errors.append({"file": p, "error": str(e)})
        if note:
            degradations.append({"file": p, "note": note})
        for r in recs:
            raw_records.append((r, os.path.basename(p)))

    # 去重（按内容哈希），保留首次出现；同一内容不同 locator 视为同一来源副本。
    seen_hashes = {}
    records = []
    for i, (raw, src) in enumerate(raw_records, 1):
        rec = normalize_record(raw, f"R{i:04d}", src)
        h = content_hash(rec)
        if h in seen_hashes:
            continue
        seen_hashes[h] = rec["record_id"]
        records.append(rec)

    if isinstance(case_obj, dict) and "case_id" not in case_obj:
        sources.append({"kind": "case_json", "path": case_path})
    else:
        sources.append({"kind": "case_json", "path": case_path})
    if task_path:
        sources.append({"kind": "task_md", "path": task_path})
    sources.extend({"kind": "material", "path": p} for p in material_files)

    inventory = {
        "generated_at": utcnow(),
        "case_id": case_id,
        "input_dir": input_dir,
        "case_path": case_path,
        "task_path": task_path,
        "files": [{"path": p, "size": os.path.getsize(p) if os.path.exists(p) else None} for p in files],
        "material_files": material_files,
        "sources": sources,
    }

    plan = {
        "generated_at": utcnow(),
        "case_id": case_id,
        "research_question": rq,
        "criteria_version": "v1",
        "criteria": {"inclusion": [], "exclusion": []},
        "criterion_judgeability": [],
        "search_protocol": {
            "databases": [],
            "fields": [],
            "date_range": None,
            "language": None,
            "search_date": None,
            "query_strings": [],
        },
        "protocol_notes": "",
        "frozen": False,
    }

    records_doc = {
        "generated_at": utcnow(),
        "case_id": case_id,
        "count": len(records),
        "records": records,
    }

    write_json(os.path.join(work, "inventory.json"), inventory)
    write_json(os.path.join(work, "plan.json"), plan)
    write_json(os.path.join(work, "records.json"), records_doc)

    change_log = [{
        "step": "skeleton",
        "time": utcnow(),
        "action": "落盘 partial 骨架；抽取 %d 条唯一记录（含 case.json 与 %d 个材料文件）" % (len(records), len(material_files)),
        "note": "output_contract 字段以 null/[] 占位；判据由 Agent 后续填写",
    }]

    result_skeleton = {
        "case_id": case_id,
        "status": "partial",
        "research_question": rq,
        "criteria_version": None,
        "criteria": {"inclusion": [], "exclusion": []},
        "search_protocol": None,
        "deduplication": None,
        "screening": None,
        "conflicts": [],
        "audit": None,
        "evidence_map": [],
        "sources": sources,
        "uncertainty": {"unresolved": [], "notes": []},
        "pending_human": [],
        "change_log": change_log,
        "errors": errors,
        "degradations": degradations,
        "runtime": {"started_at": utcnow()},
    }
    # 回填 output_contract 全部字段（null/[] 占位），不改名不删键
    for k, v in contract_defaults.items():
        result_skeleton.setdefault(k, v)

    write_json(os.path.join(output, "result.json"), result_skeleton)

    print(f"[skeleton] case_id={case_id} records={len(records)} contract_keys={len(contract_defaults)} "
          f"materials={len(material_files)} degradations={len(degradations)}")
    print(f"[skeleton] wrote {os.path.join(output, 'result.json')}")
    return 0


# --------------------------------------------------------------------------- #
# normalize (dedup)
# --------------------------------------------------------------------------- #
def cmd_normalize(args):
    work = args.work
    records_doc = load_json(os.path.join(work, "records.json"))
    if not records_doc or not isinstance(records_doc.get("records"), list):
        print("[normalize] records.json missing or empty; run skeleton first", file=sys.stderr)
        return 1
    records = records_doc["records"]

    # 建立规范化键分组：DOI 完全相同 或 规范化标题完全相同 → 自动合并为同一研究
    by_doi, by_title = {}, {}
    for r in records:
        d = norm_key(str(r.get("doi") or ""))
        t = norm_key(str(r.get("title") or ""))
        if d:
            by_doi.setdefault(d, []).append(r["record_id"])
        if t:
            by_title.setdefault(t, []).append(r["record_id"])

    parent = {}
    for grp in list(by_doi.values()) + list(by_title.values()):
        if len(grp) < 2:
            continue
        root = min(grp)
        for x in grp:
            parent[x] = root

    # 按 record_id 排序，确定簇
    clusters = {}
    for r in sorted(records, key=lambda x: x["record_id"]):
        rid = r["record_id"]
        root = parent.get(rid, rid)
        clusters.setdefault(root, []).append(r)

    dedup_clusters, possible = [], []
    merge_log = []
    for root in sorted(clusters):
        members = sorted(clusters[root], key=lambda x: x["record_id"])
        canon = members[0]
        evidence = {"doi": canon.get("doi"), "title": canon.get("title")}
        if len(members) > 1:
            reason = []
            if canon.get("doi"):
                reason.append("same_doi")
            titles = [m.get("title") for m in members]
            if all(t and norm_key(t) == norm_key(canon.get("title") or "") for t in titles):
                reason.append("normalized_title_match")
            if not reason:
                reason.append("title_high_similarity")
            merge_reason = "+".join(reason)
            merge_log.append({
                "canonical": canon["record_id"],
                "merged": [m["record_id"] for m in members[1:]],
                "reason": merge_reason,
            })
        else:
            merge_reason = None
        dedup_clusters.append({
            "cluster_id": "C%03d" % (len(dedup_clusters) + 1),
            "canonical_record_id": canon["record_id"],
            "member_record_ids": [m["record_id"] for m in members],
            "merge_reason": merge_reason,
            "title_similarity": 1.0 if len(members) == 1 else None,
            "evidence": evidence,
            "auto_merged": len(members) > 1,
        })

    # 标题近似 0.85–0.95：仅标 possible_duplicate，不自动合并
    canon_by_id = {c["canonical_record_id"]: c for c in dedup_clusters}
    ids = sorted(canon_by_id)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a = canon_by_id[ids[i]]
            b = canon_by_id[ids[j]]
            ta = norm_key(a.get("evidence", {}).get("title") or "")
            tb = norm_key(b.get("evidence", {}).get("title") or "")
            if not ta or not tb:
                continue
            sim = _sim(ta, tb)
            if 0.85 <= sim <= 0.95:
                possible.append({"a": ids[i], "b": ids[j], "similarity": round(sim, 4),
                                 "note": "标题近似（0.85–0.95），标 possible_duplicate，留人工，未合并"})

    merged_count = sum(len(c["member_record_ids"]) - 1 for c in dedup_clusters)
    unique_count = len(dedup_clusters)

    dedup_doc = {
        "generated_at": utcnow(),
        "input_count": len(records),
        "merged_count": merged_count,
        "unique_count": unique_count,
        "clusters": dedup_clusters,
        "possible_duplicates": possible,
        "merge_log": merge_log,
        "closed": (len(records) - merged_count) == unique_count,
    }
    write_json(os.path.join(work, "dedup.json"), dedup_doc)

    # 每个唯一记录一份判定模板（record_id 取 canonical）
    template = {
        "generated_at": utcnow(),
        "criteria_version": "v1",
        "screened_record_count": unique_count,
        "judgments": [],
    }
    for c in dedup_clusters:
        rid = c["canonical_record_id"]
        template["judgments"].append({
            "record_id": rid,
            "decision": None,
            "reason_code": None,
            "evidence_quote": None,
            "evidence_locator": None,
            "reviewer_a": {"decision": None, "reason_code": None, "note": ""},
            "reviewer_b": {"decision": None, "reason_code": None, "note": ""},
            "conflict": {"is_conflict": False, "type": None, "detail": None},
            "arbitration": {"decision": None, "reason_code": None, "rationale": None, "arbiter": None},
            "needs_human": False,
            "notes": "",
        })
    write_json(os.path.join(work, "judgments_template.json"), template)

    print(f"[normalize] input={len(records)} merged={merged_count} unique={unique_count} "
          f"possible_duplicates={len(possible)} closed={dedup_doc['closed']}")
    return 0


# --------------------------------------------------------------------------- #
# finalize
# --------------------------------------------------------------------------- #
def _verify_quote(rec, quote, locator):
    if not quote:
        return False
    if locator and re.search(r"(pdf|fulltext|body|full.?text)", str(locator), re.I):
        return None  # 全文证据：脚本不校验，需 Agent 注明出处
    hay = (rec.get("title") or "") + " " + (rec.get("abstract") or "")
    return norm_key(quote) in norm_key(hay)


def cmd_finalize(args):
    work, output = args.work, args.output
    records_doc = load_json(os.path.join(work, "records.json"))
    dedup_doc = load_json(os.path.join(work, "dedup.json"))
    judgments_doc = load_json(os.path.join(work, "judgments.json"))
    plan_doc = load_json(os.path.join(work, "plan.json"))
    inventory_doc = load_json(os.path.join(work, "inventory.json"))

    case_id = None
    for doc in (inventory_doc, records_doc, plan_doc):
        if isinstance(doc, dict) and doc.get("case_id"):
            case_id = doc["case_id"]
            break
    if not case_id:
        case_id = "unknown"

    records = (records_doc or {}).get("records", []) if records_doc else []
    rec_by_id = {r["record_id"]: r for r in records}
    clusters = (dedup_doc or {}).get("clusters", []) if dedup_doc else []
    canon_ids = [c["canonical_record_id"] for c in clusters]
    judgments = (judgments_doc or {}).get("judgments", []) if judgments_doc else []

    errors, degradations = [], []
    evidence_map = []

    # 判定对齐到唯一记录
    j_by_id = {}
    for j in judgments:
        rid = j.get("record_id")
        if rid:
            j_by_id[rid] = j
    missing = [rid for rid in canon_ids if rid not in j_by_id]
    if missing:
        degradations.append({"issue": "missing_judgments", "record_ids": missing,
                             "note": "这些唯一记录没有判定，按 pending_human 处理"})

    screened_rows = []
    conflicts_out = []
    pending_human = []
    include_n = exclude_n = uncertain_n = 0
    evidence_mismatch = []

    for c in sorted(clusters, key=lambda x: x["canonical_record_id"]):
        rid = c["canonical_record_id"]
        rec = rec_by_id.get(rid, {})
        j = j_by_id.get(rid, {
            "record_id": rid, "decision": "uncertain", "reason_code": "uncertain_missing_meta",
            "evidence_quote": None, "evidence_locator": None,
            "reviewer_a": {}, "reviewer_b": {},
            "conflict": {}, "arbitration": {}, "needs_human": True,
            "notes": "缺少判定，按 pending_human 处理",
        })

        decision = j.get("decision") or "uncertain"
        reason_code = j.get("reason_code") or None
        quote = j.get("evidence_quote")
        locator = j.get("evidence_locator")
        verified = _verify_quote(rec, quote, locator) if decision != "uncertain" else True

        if decision == "include":
            include_n += 1
        elif decision == "exclude":
            exclude_n += 1
        else:
            uncertain_n += 1

        if decision != "uncertain" and not quote:
            evidence_mismatch.append({"record_id": rid, "issue": "no evidence_quote for non-uncertain decision"})
            j.setdefault("needs_human", True)
        elif decision != "uncertain" and verified is False:
            evidence_mismatch.append({"record_id": rid, "issue": "evidence_quote not found in title/abstract"})
            j.setdefault("needs_human", True)

        if decision != "uncertain":
            evidence_map.append({
                "record_id": rid,
                "locator": locator or ("record:%s.title/abstract" % rid),
                "quote": quote,
                "verified": verified,
                "reason_code": reason_code,
            })

        conf = j.get("conflict") or {}
        if conf.get("is_conflict"):
            conflicts_out.append({
                "record_id": rid,
                "type": conf.get("type"),
                "detail": conf.get("detail"),
                "reviewer_a": j.get("reviewer_a"),
                "reviewer_b": j.get("reviewer_b"),
                "arbitration": j.get("arbitration"),
                "resolved": bool((j.get("arbitration") or {}).get("decision")),
            })

        if j.get("needs_human") or decision == "uncertain":
            pending_human.append({
                "record_id": rid,
                "reason_code": reason_code,
                "note": j.get("notes") or "",
            })

        screened_rows.append({
            "record_id": rid,
            "title": rec.get("title"),
            "doi": rec.get("doi"),
            "year": rec.get("year"),
            "decision": decision,
            "reason_code": reason_code,
            "evidence_quote": quote,
            "evidence_locator": locator,
            "evidence_verified": verified,
            "reviewer_a_decision": (j.get("reviewer_a") or {}).get("decision"),
            "reviewer_b_decision": (j.get("reviewer_b") or {}).get("decision"),
            "conflict": bool((j.get("conflict") or {}).get("is_conflict")),
            "arbitrated": bool((j.get("arbitration") or {}).get("decision")),
            "needs_human": bool(j.get("needs_human") or decision == "uncertain"),
        })

    total = len(canon_ids)
    screened = include_n + exclude_n + uncertain_n
    closed_counts = (total == screened)

    dedup_doc = dedup_doc or {}
    audit = {
        "retrieval": {"input": (records_doc or {}).get("count"), "output": (records_doc or {}).get("count"),
                      "closed": True, "note": "题录为给定快照，不做联网检索"},
        "deduplication": {
            "input": dedup_doc.get("input_count"),
            "merged": dedup_doc.get("merged_count"),
            "unique": dedup_doc.get("unique_count"),
            "closed": bool(dedup_doc.get("closed")),
        },
        "screening": {
            "input": total,
            "include": include_n,
            "exclude": exclude_n,
            "uncertain": uncertain_n,
            "closed": closed_counts,
        },
        "conflicts": {
            "total": len(conflicts_out),
            "resolved": sum(1 for c in conflicts_out if c["resolved"]),
            "unresolved": sum(1 for c in conflicts_out if not c["resolved"]),
            "closed": all(c["resolved"] for c in conflicts_out),
        },
        "closed": closed_counts and bool(dedup_doc.get("closed")) and all(c["resolved"] for c in conflicts_out),
    }

    # 状态
    if errors:
        status = "input_error"
    elif total == 0:
        status = "insufficient_evidence"
    elif closed_counts and not evidence_mismatch and all(c["resolved"] for c in conflicts_out) and not pending_human:
        status = "ok"
    else:
        status = "partial"

    sources = (inventory_doc or {}).get("sources", []) if inventory_doc else []
    research_question = (plan_doc or {}).get("research_question") if plan_doc else None
    criteria = (plan_doc or {}).get("criteria", {"inclusion": [], "exclusion": []}) if plan_doc else {"inclusion": [], "exclusion": []}
    search_protocol = (plan_doc or {}).get("search_protocol") if plan_doc else None
    criteria_version = (judgments_doc or {}).get("criteria_version") or ((plan_doc or {}).get("criteria_version"))

    # criteria 漂移检查
    if judgments_doc:
        jv = judgments_doc.get("criteria_version")
        pv = (plan_doc or {}).get("criteria_version")
        if jv and pv and jv != pv:
            degradations.append({"issue": "criteria_drift",
                                 "plan_version": pv, "judgments_version": jv,
                                 "note": "判定与协议的标准版本不一致，需回改或冻结"})

    result = {
        "case_id": case_id,
        "status": status,
        "research_question": research_question,
        "criteria_version": criteria_version,
        "criteria": criteria,
        "search_protocol": search_protocol,
        "deduplication": dedup_doc if dedup_doc else None,
        "screening": {
            "records": screened_rows,
            "summary": {"unique": total, "include": include_n, "exclude": exclude_n, "uncertain": uncertain_n},
        },
        "conflicts": conflicts_out,
        "audit": audit,
        "evidence_map": evidence_map,
        "sources": sources,
        "uncertainty": {
            "unresolved": pending_human,
            "evidence_mismatches": evidence_mismatch,
            "notes": [],
        },
        "pending_human": pending_human,
        "change_log": [
            {"step": "finalize", "time": utcnow(),
             "action": "合并判定并校验证据；include=%d exclude=%d uncertain=%d closed=%s" % (
                 include_n, exclude_n, uncertain_n, closed_counts)},
        ],
        "errors": errors,
        "degradations": degradations,
        "runtime": {"finished_at": utcnow()},
    }

    # 回填 output_contract 字段（若 case.json 提供了契约；否则保持上一版骨架的键）
    prev = load_json(os.path.join(output, "result.json")) if os.path.exists(os.path.join(output, "result.json")) else {}
    contract_keys = set()
    for k in CONTRACT_KEYS:
        if k in prev and isinstance(prev[k], (dict, list)):
            contract_keys = set(prev[k].keys())
            break
    if not contract_keys:
        # 契约键可能直接平铺在 result 顶层
        reserved = set(result.keys())
        for k, v in prev.items():
            if k not in reserved and not k.startswith("_"):
                contract_keys.add(k)
    for k in sorted(contract_keys):
        result.setdefault(k, prev.get(k, None))

    write_json(os.path.join(output, "result.json"), result)

    # 可编辑产物
    _write_artifacts(output, work, result, screened_rows, audit, evidence_map, dedup_doc, plan_doc, records_doc)

    print(f"[finalize] unique={total} include={include_n} exclude={exclude_n} uncertain={uncertain_n} "
          f"closed={closed_counts} evidence_mismatches={len(evidence_mismatch)} status={status}")
    if evidence_mismatch:
        for m in evidence_mismatch:
            print("  - mismatch:", m)
    return 0


def _write_artifacts(output, work, result, rows, audit, evidence_map, dedup_doc, plan_doc, records_doc):
    # screening_table.csv（utf-8-sig，逐条结构化）
    csv_path = os.path.join(output, "screening_table.csv")
    os.makedirs(output, exist_ok=True)
    cols = ["record_id", "title", "doi", "year", "decision", "reason_code", "evidence_quote",
            "evidence_locator", "evidence_verified", "reviewer_a_decision", "reviewer_b_decision",
            "conflict", "arbitrated", "needs_human"]
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c) for c in cols})

    # audit_log.json
    write_json(os.path.join(output, "audit_log.json"), {
        "generated_at": utcnow(),
        "audit": audit,
        "deduplication": dedup_doc,
        "evidence_map": evidence_map,
    })

    # plan.json 最终副本（中间表示 → 可复核产物）
    if plan_doc:
        write_json(os.path.join(output, "plan.json"), plan_doc)

    # protocol.md
    proto = ["# 检索协议与筛选协议", "", "> 本文件由 finalize 生成，供第三人复跑同一批题录。",
             "", "## 研究问题", "", str((plan_doc or {}).get("research_question") or "not_reported"),
             "", "## 纳入/排除标准", ""]
    crit = (plan_doc or {}).get("criteria", {}) if plan_doc else {}
    proto.append("### 纳入")
    for x in crit.get("inclusion", []) or []:
        proto.append("- %s" % x)
    proto.append("### 排除")
    for x in crit.get("exclusion", []) or []:
        proto.append("- %s" % x)
    proto += ["", "## 检索协议", "", "```json", json.dumps((plan_doc or {}).get("search_protocol") or {}, ensure_ascii=False, indent=2), "```",
              "", "## 计数闭合", "", "```json", json.dumps(audit, ensure_ascii=False, indent=2), "```", ""]
    with open(os.path.join(output, "protocol.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(proto))

    # evidence_map.json（locator + 逐字 quote）
    write_json(os.path.join(output, "evidence_map.json"), {
        "generated_at": utcnow(),
        "evidence": evidence_map,
        "note": "每条关键结论都给出 locator 与逐字 quote；evidence_verified=false 表示脚本未在标题/摘要中命中，需人工复核",
    })


# --------------------------------------------------------------------------- #
# selfcheck
# --------------------------------------------------------------------------- #
def cmd_selfcheck(args):
    output, work = args.output, args.work
    result = load_json(os.path.join(output, "result.json"))
    checks = []
    def check(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    check("result.json exists", result is not None)
    if result is None:
        for name, ok, _ in checks:
            print("FAIL" if not ok else "PASS", "-", name)
        return 1

    check("case_id present", bool(result.get("case_id")))
    check("status valid", result.get("status") in ("ok", "partial", "insufficient_evidence", "input_error"))
    check("change_log present", isinstance(result.get("change_log"), list))
    check("sources present", isinstance(result.get("sources"), list))
    check("uncertainty present", isinstance(result.get("uncertainty"), dict))
    check("pending_human present", isinstance(result.get("pending_human"), list))

    screening = result.get("screening") or {}
    rows = screening.get("records", [])
    check("screening.records non-empty", isinstance(rows, list) and len(rows) > 0)
    check("every row has decision", all(r.get("decision") for r in rows))
    check("every non-uncertain row has reason_code",
          all(r.get("decision") == "uncertain" or r.get("reason_code") for r in rows))
    check("no evidence mismatch", not (result.get("uncertainty") or {}).get("evidence_mismatches"))

    audit = result.get("audit") or {}
    scr = audit.get("screening") or {}
    check("count closure", bool(scr.get("closed")), f"include+exclude+uncertain={scr.get('include',0)+scr.get('exclude',0)+scr.get('uncertain',0)} vs input={scr.get('input')}")

    dedup = result.get("deduplication") or {}
    check("dedup closure", bool(dedup.get("closed")), f"merged={dedup.get('merged')} unique={dedup.get('unique')} input={dedup.get('input_count')}")

    # 证据命中复验
    records_doc = load_json(os.path.join(work, "records.json"))
    rec_by_id = {r["record_id"]: r for r in (records_doc or {}).get("records", [])}
    bad_evidence = []
    for r in rows:
        if r.get("decision") == "uncertain":
            continue
        if r.get("evidence_verified") is False:
            bad_evidence.append(r["record_id"])
    check("evidence verified for all non-uncertain", not bad_evidence, f"unverified={bad_evidence}")

    all_ok = all(ok for _, ok, _ in checks)
    for name, ok, detail in checks:
        print(("PASS" if ok else "FAIL"), "-", name, (("  [" + detail + "]") if detail and not ok else ""))
    print("[selfcheck]", "ALL PASS" if all_ok else "SOME FAILED")
    return 0 if all_ok else 1


# --------------------------------------------------------------------------- #
# dispatcher
# --------------------------------------------------------------------------- #
def build_parser():
    p = argparse.ArgumentParser(prog="pipeline.py", description="systematic literature screening ledger")
    p.add_argument("command", choices=["skeleton", "normalize", "finalize", "selfcheck"])
    p.add_argument("--input", default=DEFAULT_INPUT)
    p.add_argument("--output", default=DEFAULT_OUTPUT)
    p.add_argument("--work", default=DEFAULT_WORK)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    fn = {"skeleton": cmd_skeleton, "normalize": cmd_normalize,
          "finalize": cmd_finalize, "selfcheck": cmd_selfcheck}[args.command]
    try:
        return fn(args)
    except Exception as e:  # noqa: BLE001
        print(f"[{args.command}] error: {type(e).__name__}: {e}", file=sys.stderr)
        # 尽力落盘最小骨架，不让进程崩溃
        try:
            os.makedirs(args.output, exist_ok=True)
            result_path = os.path.join(args.output, "result.json")
            prev = load_json(result_path) or {}
            prev.setdefault("case_id", "unknown")
            prev.setdefault("status", "input_error")
            prev.setdefault("errors", []).append({"step": args.command, "error": str(e)})
            prev.setdefault("change_log", []).append({"step": args.command, "time": utcnow(), "action": "异常降级", "note": str(e)})
            write_json(result_path, prev)
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
