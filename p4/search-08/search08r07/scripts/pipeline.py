#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pipeline.py — 系统性文献检索与纳排筛选的确定性账本管线。

角色边界：科学判断（纳入/排除、冲突仲裁、标准定版）由调用方模型负责；
本脚本只负责可复现的账本 —— 输入发现、题录解析、去重键、证据子串校验、
计数闭合与诚实状态。任何输入异常一律降级记录（degradations），不抛异常、
不编造数据、不伪造运行结果。

仅依赖 Python 标准库。所有排序均确定性（无随机种子依赖），重复运行结果一致。

子命令：
  skeleton   扫描输入目录，落盘最小合法 result.json 骨架与中间产物
  normalize  读取 records.json，去重并写 dedup.json + judgments_template.json
  finalize   合并判定、校验证据与计数闭合，写最终 result.json 与可编辑产物
  selfcheck  校验 result.json 是否满足输出契约（逐项打印 PASS/FAIL）

用法：
  python3 -B scripts/pipeline.py skeleton  [--input DIR] [--output DIR] [--work DIR]
  python3 -B scripts/pipeline.py normalize [--work DIR]
  python3 -B scripts/pipeline.py finalize  [--input DIR] [--output DIR] [--work DIR]
  python3 -B scripts/pipeline.py selfcheck [--output DIR] [--work DIR]
"""

import argparse
import csv
import difflib
import hashlib
import io
import json
import os
import re
import sys
import time

MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文本文件上限 2MB，超限跳过并记录

# 题录文件扩展名（可解析为记录）；PDF 属于材料，不在此列
RECORD_EXTS = {".csv", ".tsv", ".ris", ".bib", ".bibtex", ".json", ".jsonl"}

# 理由码字典（与 references/reason_codes.md 保持一致）
INCLUDE_CODES = {"include_relevant", "include_borderline"}
EXCLUDE_CODES = {
    "ex_not_relevant", "ex_wrong_population", "ex_wrong_intervention",
    "ex_wrong_outcome", "ex_wrong_design", "ex_language",
    "ex_duplicate_merged", "ex_retracted", "ex_no_abstract",
    "ex_out_of_scope",
}
UNCERTAIN_CODES = {"uncertain", "pending_human", "not_reported", "source_conflict"}
ALL_REASON_CODES = INCLUDE_CODES | EXCLUDE_CODES | UNCERTAIN_CODES

VALID_DECISIONS = {"include", "exclude", "uncertain", "pending_human"}

# result.json 中由本管线维护的保留键；其余顶层键视为 output_contract 原样回填键
RESERVED_KEYS = {
    "case_id", "status", "sources", "uncertainty", "pending_human", "change_log",
    "research_question", "inclusion_criteria", "exclusion_criteria",
    "search_strategy", "deduplication", "screening", "conflicts", "audit",
    "errors", "degradations", "runtime", "output_contract_keys",
}


def utcnow():
    t = time.gmtime()
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", t)


def normalize_title(text):
    """规范化标题用于去重键：小写、去标点、空白折叠。"""
    if not text:
        return ""
    t = str(text).lower()
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def normalize_doi(text):
    """规范化 DOI：小写、去前缀、去空白。"""
    if not text:
        return ""
    d = str(text).strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    d = re.sub(r"^doi:\s*", "", d)
    return d.strip()


def title_similarity(a, b):
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def read_text_bytes(path):
    """按 utf-8-sig → utf-8 → latin-1 顺序解码，返回 (text, encoding, degraded)。

    latin-1 永不失败，因此必然返回内容；前两种编码失败时 degraded=True。
    """
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), enc, False
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1"), "latin-1", True


def load_json_file(path):
    """容错读取 JSON；坏 JSON 返回 None，不抛异常。"""
    try:
        text, _enc, _deg = read_text_bytes(path)
        return json.loads(text)
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def _walk_files(root):
    """确定性遍历输入目录（含一层 materials/ 子目录），返回 (rel_path, abs_path, size)。"""
    out = []
    if not os.path.isdir(root):
        return out
    entries = sorted(os.listdir(root))
    for name in entries:
        p = os.path.join(root, name)
        if os.path.isdir(p):
            for sub in sorted(os.listdir(p)):
                sp = os.path.join(p, sub)
                if os.path.isfile(sp):
                    out.append((os.path.join(name, sub), sp, os.path.getsize(sp)))
        elif os.path.isfile(p):
            out.append((name, p, os.path.getsize(p)))
    return out


def scan_inputs(input_dir):
    """扫描输入目录，产出 inventory（含文件清单、case、task、degradations）。"""
    inv = {
        "scanned_at": utcnow(),
        "input_dir": input_dir,
        "files": [],
        "case": None,
        "case_path": None,
        "task_md": None,
        "record_sources": [],
        "material_files": [],
        "degradations": [],
    }
    if not os.path.isdir(input_dir):
        inv["degradations"].append({
            "level": "error", "where": "scan_inputs",
            "detail": "input directory not found: " + str(input_dir),
            "action": "treated_as_empty_input",
        })
        return inv

    for rel, abspath, size in _walk_files(input_dir):
        entry = {
            "path": rel, "absolute": abspath, "size": size,
            "status": "ok", "kind": "other",
        }
        if size > MAX_FILE_BYTES:
            entry["status"] = "skipped_oversize"
            entry["kind"] = "skipped"
            inv["degradations"].append({
                "level": "warn", "file": rel,
                "detail": "size %d > %d bytes" % (size, MAX_FILE_BYTES),
                "action": "skipped, not parsed",
            })
            inv["files"].append(entry)
            continue

        lower = rel.lower()
        if lower == "case.json":
            entry["kind"] = "case"
            data = load_json_file(abspath)
            if data is None:
                entry["status"] = "bad_json"
                inv["degradations"].append({
                    "level": "error", "file": rel,
                    "detail": "case.json is not valid JSON",
                    "action": "treated_as_missing_case",
                })
            else:
                inv["case"] = data
                inv["case_path"] = rel
        elif lower in ("task.md", "task.txt"):
            entry["kind"] = "task"
            text, _enc, degraded = read_text_bytes(abspath)
            if degraded:
                inv["degradations"].append({
                    "level": "warn", "file": rel,
                    "detail": "bad encoding, read with latin-1 fallback",
                    "action": "best_effort_read",
                })
            inv["task_md"] = text
        elif lower.endswith(".pdf"):
            entry["kind"] = "material"
            inv["material_files"].append(entry)
        elif any(lower.endswith(e) for e in RECORD_EXTS):
            entry["kind"] = "records"
            inv["record_sources"].append(entry)
        else:
            entry["kind"] = "other"
        inv["files"].append(entry)

    return inv


def extract_case_id(case_data):
    if not isinstance(case_data, dict):
        return None
    for key in ("case_id", "caseId", "challenge_id", "challengeId", "armiId", "id"):
        if key in case_data and case_data[key] not in (None, ""):
            return str(case_data[key])
    return None


def extract_output_contract(case_data):
    """定位并原样返回 output_contract；未找到返回空 dict。"""
    if isinstance(case_data, dict):
        for key in ("output_contract", "outputContract", "result_schema",
                    "result_fields", "output"):
            if key in case_data and isinstance(case_data[key], dict):
                return dict(case_data[key])
    return {}


def extract_protocol(case_data, task_text):
    """从 case.json / task.md 尽力提取研究问题与纳排标准；缺失则留空并标待人工。"""
    proto = {
        "criteria_version": "v1",
        "research_question": None,
        "inclusion_criteria": [],
        "exclusion_criteria": [],
        "search_strategy": {},
        "frozen_at": None,
        "change_log": [],
        "output_contract": {},
        "output_contract_keys": [],
    }
    src = case_data if isinstance(case_data, dict) else {}
    for key in ("research_question", "question", "researchQuestion", "task"):
        if key in src and src[key]:
            proto["research_question"] = str(src[key])
            break
    if proto["research_question"] is None and task_text:
        m = re.search(r"研究问题[:：]\s*(.{0,300})", task_text)
        if m:
            proto["research_question"] = m.group(1).strip()
    for key in ("inclusion_criteria", "inclusionCriteria", "include_criteria"):
        if key in src and isinstance(src[key], (list, tuple)):
            proto["inclusion_criteria"] = [str(x) for x in src[key]]
            break
    for key in ("exclusion_criteria", "exclusionCriteria", "exclude_criteria"):
        if key in src and isinstance(src[key], (list, tuple)):
            proto["exclusion_criteria"] = [str(x) for x in src[key]]
            break
    if isinstance(src.get("search_strategy"), dict):
        proto["search_strategy"] = dict(src["search_strategy"])
    oc = extract_output_contract(case_data)
    proto["output_contract"] = oc
    proto["output_contract_keys"] = sorted(oc.keys())
    proto["frozen_at"] = utcnow()
    return proto


# ---------------------------------------------------------------------------
# 题录解析
# ---------------------------------------------------------------------------

_HEADER_ALIASES = {
    "title": ("title", "ti", "article title", "题名", "标题", "文章标题"),
    "doi": ("doi", "digital object identifier", "doi编号", "doi号"),
    "authors": ("authors", "author", "au", "作者", "作者列表"),
    "year": ("year", "py", "publication year", "date", "年份", "发表年份"),
    "abstract": ("abstract", "ab", "摘要", "内容摘要"),
    "journal": ("journal", "source", "jo", "期刊", "来源期刊"),
    "url": ("url", "link", "链接"),
}


def _map_header(raw_header):
    h = re.sub(r"[^\w\u4e00-\u9fff]", "", str(raw_header).lower())
    for field, aliases in _HEADER_ALIASES.items():
        for a in aliases:
            if h == re.sub(r"[^\w\u4e00-\u9fff]", "", a):
                return field
    return None


def _read_csv_records(path, delim):
    text, _enc, _degraded = read_text_bytes(path)
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delim)
        rows = list(reader)
    except csv.Error:
        return [], [{"level": "error", "file": os.path.basename(path),
                     "detail": "csv parse error", "action": "skipped"}]
    if not rows:
        return [], []
    header = rows[0]
    colmap = {i: _map_header(h) for i, h in enumerate(header)}
    records = []
    for ridx, row in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in row):
            continue
        rec = {"_raw": dict(zip(header, row)), "_line": ridx}
        for i, field in colmap.items():
            if field is None or i >= len(row):
                continue
            val = row[i].strip()
            if val:
                rec[field] = val
        records.append(rec)
    return records, []


_RIS_TAGS = {
    "TI": "title", "DO": "doi", "AU": "authors", "PY": "year",
    "AB": "abstract", "JO": "journal", "JF": "journal", "UR": "url",
    "TY": "_type", "ER": "_end",
}


def _read_ris_records(path):
    text, _enc, _degraded = read_text_bytes(path)
    records, current, authors = [], None, []
    for line in text.splitlines():
        line = line.rstrip("\r")
        if not line.strip():
            continue
        m = re.match(r"^([A-Z][A-Z0-9])\s{0,2}-\s?(.*)$", line)
        if not m:
            continue
        tag, val = m.group(1).upper(), m.group(2).strip()
        field = _RIS_TAGS.get(tag)
        if field == "_end" or tag == "TY":
            if tag == "TY" and current is not None:
                if authors:
                    current["authors"] = "; ".join(authors)
                records.append(current)
            if tag == "TY":
                current = {"_raw": {}, "_line": 0}
                authors = []
            continue
        if current is None:
            current = {"_raw": {}, "_line": 0}
            authors = []
        if field == "authors":
            authors.append(val)
        elif field is not None:
            if field in current and current[field]:
                current[field] += "; " + val
            else:
                current[field] = val
        current.setdefault("_raw", {})[tag] = val
    if current is not None:
        if authors:
            current["authors"] = "; ".join(authors)
        records.append(current)
    return records, []


def _read_bib_records(path):
    text, _enc, _degraded = read_text_bytes(path)
    records = []
    entry_re = re.compile(r"@\s*(\w+)\s*\{\s*([^,]+),", re.IGNORECASE)
    for em in entry_re.finditer(text):
        body_start = em.end()
        # 粗略匹配到条目结束：查找下一个 @ 或文件尾
        nxt = text.find("@", body_start)
        body = text[body_start: nxt if nxt != -1 else len(text)]
        rec = {"_raw": {"type": em.group(1), "key": em.group(2)}, "_line": 0}
        for fname, fld in (("title", "title"), ("doi", "doi"), ("author", "authors"),
                           ("year", "year"), ("abstract", "abstract"),
                           ("journal", "journal"), ("url", "url")):
            fm = re.search(r"%s\s*=\s*\{([^{}]*)\}" % fname, body, re.IGNORECASE)
            if fm:
                rec[fld] = fm.group(1).strip()
        records.append(rec)
    return records, []


def _read_json_records(path, jsonl=False):
    records = []
    if jsonl:
        text, _enc, _degraded = read_text_bytes(path)
        for ln in text.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                records.append(obj)
        return records, []
    data = load_json_file(path)
    if data is None:
        return [], [{"level": "error", "file": os.path.basename(path),
                     "detail": "bad json", "action": "skipped"}]
    if isinstance(data, list):
        for obj in data:
            if isinstance(obj, dict):
                records.append(obj)
    elif isinstance(data, dict):
        for key in ("records", "results", "items", "data"):
            if isinstance(data.get(key), list):
                for obj in data[key]:
                    if isinstance(obj, dict):
                        records.append(obj)
                break
        else:
            records.append(data)
    return records, []


def _normalize_record(rec, source_file, idx):
    """补全字段名、生成确定性 record_id。"""
    norm = {"_raw": rec.get("_raw", {}), "_line": rec.get("_line", 0),
            "source_file": source_file}
    alias = {
        "title": ("title", "ti", "Title", "题名", "标题"),
        "doi": ("doi", "DOI", "doi编号"),
        "authors": ("authors", "author", "au", "AU", "作者"),
        "year": ("year", "py", "PY", "date", "年份"),
        "abstract": ("abstract", "ab", "AB", "摘要"),
        "journal": ("journal", "source", "jo", "JO", "期刊"),
        "url": ("url", "link", "UR", "链接"),
    }
    for field, names in alias.items():
        for n in names:
            if rec.get(n) not in (None, ""):
                norm[field] = str(rec[n]).strip()
                break
        norm.setdefault(field, "")
    norm["record_id"] = make_record_id(norm.get("title", ""), norm.get("doi", ""),
                                       source_file, idx)
    return norm


def make_record_id(title, doi, source_file, idx):
    key = normalize_title(title) + "||" + normalize_doi(doi)
    if key.strip("|"):
        return "rec_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    return "rec_%s_%d" % (re.sub(r"[^\w]", "_", source_file)[:20], idx)


def parse_records_from_files(record_sources):
    """解析全部题录文件，返回 (records, degradations)。"""
    records, degradations = [], []
    for entry in record_sources:
        rel = entry["path"]
        lower = rel.lower()
        recs, degs = [], []
        if lower.endswith(".csv"):
            recs, degs = _read_csv_records(entry["absolute"], ",")
        elif lower.endswith(".tsv"):
            recs, degs = _read_csv_records(entry["absolute"], "\t")
        elif lower.endswith(".ris"):
            recs, degs = _read_ris_records(entry["absolute"])
        elif lower.endswith((".bib", ".bibtex")):
            recs, degs = _read_bib_records(entry["absolute"])
        elif lower.endswith(".jsonl"):
            recs, degs = _read_json_records(entry["absolute"], jsonl=True)
        elif lower.endswith(".json"):
            recs, degs = _read_json_records(entry["absolute"])
        else:
            degradations.append({"level": "warn", "file": rel,
                                 "detail": "unrecognized record source", "action": "skipped"})
            continue
        for d in degs:
            d.setdefault("file", rel)
            degradations.append(d)
        for i, r in enumerate(recs):
            records.append(_normalize_record(r, rel, i + 1))
    return records, degradations


# ---------------------------------------------------------------------------
# 去重
# ---------------------------------------------------------------------------

def build_dedup(records):
    """按 DOI / 规范化标题聚类；近似标题只标 possible_duplicate，不自动合并。"""
    clusters = []
    by_key = {}
    for r in records:
        doi = normalize_doi(r.get("doi", ""))
        key = ("doi", doi) if doi else ("title", normalize_title(r.get("title", "")))
        if not key[1]:
            key = ("unique", r["record_id"])
        by_key.setdefault(key, []).append(r)

    merged = []
    for key, members in sorted(by_key.items(), key=lambda kv: kv[0][1]):
        members = sorted(members, key=lambda r: r.get("record_id", ""))
        if len(members) == 1:
            merged.append({"cluster_id": "c_%s_%s" % (key[0], members[0]["record_id"]),
                           "representative": members[0],
                           "members": [members[0]],
                           "merge_reason": None,
                           "evidence": []})
            continue
        # 代表：优先有 DOI、有摘要、有年份者，否则取第一个
        def _score(r):
            return (1 if r.get("doi") else 0, 1 if r.get("abstract") else 0,
                    1 if r.get("year") else 0)
        rep = max(members, key=_score)
        ev = [{"type": "same_key", "key_kind": key[0], "key": key[1]}]
        merged.append({
            "cluster_id": "c_%s_%s" % (key[0], rep["record_id"]),
            "representative": rep,
            "members": members,
            "merge_reason": "same_doi" if key[0] == "doi" else "normalized_title_match",
            "evidence": ev,
        })

    # 近似标题（0.85–0.95）只标 possible_duplicate，留人工，不自动合并
    possible = []
    reps = [c["representative"] for c in merged]
    for i in range(len(reps)):
        for j in range(i + 1, len(reps)):
            a, b = reps[i], reps[j]
            if a["record_id"] == b["record_id"]:
                continue
            sim = title_similarity(a.get("title", ""), b.get("title", ""))
            if 0.85 <= sim <= 0.95:
                possible.append({
                    "a": a["record_id"], "b": b["record_id"],
                    "title_a": a.get("title", ""), "title_b": b.get("title", ""),
                    "similarity": round(sim, 4), "action": "pending_human",
                })

    unique_count = len(merged)
    return {"clusters": merged, "possible_duplicates": possible,
            "unique_count": unique_count,
            "generated_at": utcnow()}


# ---------------------------------------------------------------------------
# 骨架 / 汇总 / 自检
# ---------------------------------------------------------------------------

def _default_contract_value(v):
    if isinstance(v, list):
        return []
    if isinstance(v, dict):
        return {}
    return None


def _empty_ledger():
    return {
        "case_id": "unknown",
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "research_question": None,
        "inclusion_criteria": [],
        "exclusion_criteria": [],
        "search_strategy": {},
        "deduplication": {"clusters": [], "possible_duplicates": [], "unique_count": 0},
        "screening": {"records": [], "summary": {}},
        "conflicts": [],
        "audit": {"steps": [], "counts": {}},
        "errors": [],
        "degradations": [],
        "runtime": {},
        "output_contract_keys": [],
    }


def run_skeleton(input_dir, output_dir, work_dir):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    inv = scan_inputs(input_dir)
    case = inv.get("case") if isinstance(inv.get("case"), dict) else {}
    task_text = inv.get("task_md") or ""
    case_id = extract_case_id(case) or "unknown"
    proto = extract_protocol(case, task_text)

    records, rec_degs = parse_records_from_files(inv["record_sources"])
    records.sort(key=lambda r: r["record_id"])

    result = _empty_ledger()
    result["case_id"] = case_id
    result["status"] = "partial"
    result["research_question"] = proto["research_question"]
    result["inclusion_criteria"] = proto["inclusion_criteria"]
    result["exclusion_criteria"] = proto["exclusion_criteria"]
    result["search_strategy"] = proto["search_strategy"]
    result["output_contract_keys"] = proto["output_contract_keys"]
    # output_contract 字段原样回填（缺值 null/[]）
    for k in proto["output_contract_keys"]:
        result[k] = _default_contract_value(proto["output_contract"].get(k))
    result["runtime"] = {"started_at": utcnow(), "phase": "skeleton"}

    all_degs = list(inv["degradations"]) + list(rec_degs)
    result["degradations"] = all_degs
    result["change_log"] = [{
        "at": utcnow(), "actor": "pipeline.skeleton",
        "change": "skeleton written (status=partial)",
    }]
    if not inv["record_sources"] and not inv["material_files"]:
        result["status"] = "insufficient_evidence"
        result["pending_human"].append({
            "issue": "no_record_sources", "detail": "input contains no bibliographic records",
        })

    # 落盘中间产物与骨架
    _write_json(os.path.join(work_dir, "inventory.json"), inv)
    _write_json(os.path.join(work_dir, "protocol.json"), proto)
    _write_json(os.path.join(work_dir, "records.json"), {"records": records,
                                                          "degradations": all_degs})
    _write_json(os.path.join(output_dir, "result.json"), result)
    return result


def run_normalize(work_dir):
    records_path = os.path.join(work_dir, "records.json")
    data = load_json_file(records_path)
    if not data or not isinstance(data.get("records"), list):
        records = []
    else:
        records = data["records"]
    dedup = build_dedup(records)
    template = []
    for c in dedup["clusters"]:
        rep = c["representative"]
        template.append({
            "record_id": rep["record_id"],
            "title": rep.get("title", ""),
            "doi": rep.get("doi", ""),
            "decision": None,
            "reason_code": None,
            "evidence_quote": None,
            "reviewer_a": None,
            "reviewer_b": None,
            "conflict": None,
            "arbitration": None,
            "needs_human": False,
        })
    template.sort(key=lambda x: x["record_id"])
    _write_json(os.path.join(work_dir, "dedup.json"), dedup)
    _write_json(os.path.join(work_dir, "judgments_template.json"),
                {"judgments": template, "template_note":
                 "copy to judgments.json and fill decision/reason_code/evidence_quote"})
    return {"unique_count": dedup["unique_count"],
            "possible_duplicates": len(dedup["possible_duplicates"])}


def _verify_quote(record, quote):
    if quote in (None, ""):
        return {"verified": False, "reason": "empty_quote"}
    if quote == "not_reported":
        return {"verified": True, "reason": "explicit_not_reported"}
    title = record.get("title", "") or ""
    abstract = record.get("abstract", "") or ""
    if quote in title or quote in abstract:
        return {"verified": True, "reason": "exact_substring"}
    qn = normalize_title(quote)
    if qn and (qn in normalize_title(title) or qn in normalize_title(abstract)):
        return {"verified": True, "reason": "normalized_substring"}
    return {"verified": False, "reason": "mismatch"}


def run_finalize(input_dir, output_dir, work_dir):
    os.makedirs(output_dir, exist_ok=True)
    records_data = load_json_file(os.path.join(work_dir, "records.json")) or {}
    records = records_data.get("records", []) or []
    rec_by_id = {r["record_id"]: r for r in records}
    dedup = load_json_file(os.path.join(work_dir, "dedup.json")) or {}
    proto = load_json_file(os.path.join(work_dir, "protocol.json")) or {}
    judgments = load_json_file(os.path.join(work_dir, "judgments.json")) or {}

    # 读取现有骨架，保留 output_contract 已回填值（模型可能已填实际结果）
    existing = load_json_file(os.path.join(output_dir, "result.json")) or {}
    oc_keys = sorted(set(proto.get("output_contract_keys", []) or [])
                     | set(existing.get("output_contract_keys", []) or []))

    result = _empty_ledger()
    result["case_id"] = existing.get("case_id") or extract_case_id(
        load_json_file(os.path.join(input_dir, "case.json")) or {}) or "unknown"
    result["research_question"] = (proto.get("research_question")
                                   or existing.get("research_question"))
    result["inclusion_criteria"] = proto.get("inclusion_criteria") or existing.get("inclusion_criteria") or []
    result["exclusion_criteria"] = proto.get("exclusion_criteria") or existing.get("exclusion_criteria") or []
    result["search_strategy"] = proto.get("search_strategy") or existing.get("search_strategy") or {}
    result["output_contract_keys"] = oc_keys
    for k in oc_keys:
        result[k] = existing.get(k, _default_contract_value(
            (proto.get("output_contract") or {}).get(k)))

    result["deduplication"] = dedup if isinstance(dedup, dict) else {"clusters": []}

    # 合并判定
    jlist = judgments.get("judgments", []) if isinstance(judgments, dict) else []
    if not jlist:
        jlist = load_json_file(os.path.join(work_dir, "judgments_template.json")) or {}
        jlist = jlist.get("judgments", []) if isinstance(jlist, dict) else []
    screening_records = []
    degradations = list(records_data.get("degradations", []) or [])
    for j in jlist:
        rid = j.get("record_id")
        rec = rec_by_id.get(rid, {})
        quote = j.get("evidence_quote")
        vq = _verify_quote(rec, quote)
        j = dict(j)
        j["evidence_verified"] = vq.get("verified", False)
        j["evidence_check"] = vq.get("reason", "")
        j["locator"] = "%s#title|abstract" % rec.get("source_file", "?")
        decision = j.get("decision")
        if decision is not None and decision not in VALID_DECISIONS:
            degradations.append({"level": "error", "where": "finalize",
                                 "record_id": rid,
                                 "detail": "invalid decision: %r" % decision,
                                 "action": "marked_uncertain"})
            j["decision"] = "uncertain"
            j.setdefault("reason_code", "uncertain")
        if j.get("decision") not in (None, "uncertain", "pending_human") and not vq["verified"]:
            degradations.append({"level": "warn", "where": "finalize",
                                 "record_id": rid,
                                 "detail": "evidence_quote not found in title/abstract (%s)" % vq["reason"],
                                 "action": "recorded_not_masked"})
        rc = j.get("reason_code")
        if rc is not None and rc not in ALL_REASON_CODES:
            degradations.append({"level": "warn", "where": "finalize",
                                 "record_id": rid,
                                 "detail": "unknown reason_code: %r" % rc,
                                 "action": "kept_as_is"})
        screening_records.append(j)
    screening_records.sort(key=lambda x: x.get("record_id", ""))

    # 冲突记录
    conflicts = []
    for j in screening_records:
        c = j.get("conflict")
        if isinstance(c, dict) and c.get("is_conflict"):
            conflicts.append(c)
        elif isinstance(j.get("reviewer_a"), dict) and isinstance(j.get("reviewer_b"), dict):
            if j["reviewer_a"].get("decision") != j["reviewer_b"].get("decision"):
                conflicts.append({
                    "record_id": j["record_id"],
                    "type": "decision",
                    "reviewer_a": j["reviewer_a"],
                    "reviewer_b": j["reviewer_b"],
                    "arbitration": j.get("arbitration"),
                    "is_conflict": True,
                })

    # 计数闭合
    unique = len(dedup.get("clusters", []) or [])
    merged_duplicates = sum(max(0, len(c.get("members", [])) - 1)
                            for c in (dedup.get("clusters", []) or []))
    counts = {
        "retrieved": len(records),
        "merged_duplicates": merged_duplicates,
        "unique": unique,
        "included": sum(1 for j in screening_records if j.get("decision") == "include"),
        "excluded": sum(1 for j in screening_records if j.get("decision") == "exclude"),
        "uncertain": sum(1 for j in screening_records
                         if j.get("decision") in ("uncertain", "pending_human", None)),
    }
    judged = counts["included"] + counts["excluded"] + counts["uncertain"]
    closed = (counts["unique"] == judged)
    counts["closure"] = "closed" if closed else "open"
    if not closed:
        degradations.append({"level": "warn", "where": "finalize",
                             "detail": "count closure failed: unique=%d judged=%d" % (unique, judged),
                             "action": "status_kept_partial"})

    inventory = load_json_file(os.path.join(work_dir, "inventory.json")) or {}
    result["screening"] = {"records": screening_records,
                           "summary": {"counts": counts, "criteria_version":
                                       proto.get("criteria_version", "v1")}}
    result["conflicts"] = conflicts
    result["deduplication"]["unique_count"] = unique
    result["deduplication"]["merged_duplicates"] = merged_duplicates
    result["audit"] = {
        "steps": [
            {"step": "skeleton", "input_count": len(inventory.get("files", [])),
             "output_count": 1, "operator": "pipeline.skeleton",
             "at": utcnow(), "evidence": "inventory.json"},
            {"step": "normalize", "input_count": len(records),
             "output_count": unique, "operator": "pipeline.normalize",
             "at": utcnow(), "evidence": "dedup.json"},
            {"step": "finalize", "input_count": unique,
             "output_count": judged, "operator": "pipeline.finalize",
             "at": utcnow(), "evidence": "result.json"},
        ],
        "counts": counts,
    }
    result["sources"] = inventory.get("files", []) or existing.get("sources", [])
    result["uncertainty"] = existing.get("uncertainty", []) or []
    result["pending_human"] = (existing.get("pending_human", []) or []) + [
        {"record_id": j.get("record_id"), "title": j.get("title", ""),
         "reason": "needs_human", "detail": j.get("arbitration")}
        for j in screening_records if j.get("needs_human")
    ]
    result["errors"] = existing.get("errors", []) or []
    result["degradations"] = degradations
    result["change_log"] = (existing.get("change_log", []) or []) + [{
        "at": utcnow(), "actor": "pipeline.finalize",
        "change": "finalize merged judgments; closure=%s" % ("closed" if closed else "open"),
    }]
    result["runtime"] = {"started_at": (existing.get("runtime") or {}).get("started_at", utcnow()),
                         "finished_at": utcnow(), "phase": "finalize",
                         "python": sys.version.split()[0]}

    # 状态诚实推断
    if not records and not inventory.get("record_sources"):
        result["status"] = "insufficient_evidence"
    elif not closed or any(j.get("decision") in (None, "uncertain", "pending_human")
                           for j in screening_records):
        result["status"] = "partial"
    else:
        result["status"] = "ok"
    if result["status"] == "partial" and not records and not result["pending_human"]:
        result["status"] = "insufficient_evidence"

    # 写最终产物
    _write_json(os.path.join(output_dir, "result.json"), result)
    _write_screening_csv(os.path.join(output_dir, "screening_table.csv"),
                         screening_records, counts)
    _write_json(os.path.join(output_dir, "audit_log.json"), result["audit"])
    _write_protocol_md(os.path.join(output_dir, "protocol.md"), result)
    return result


def run_selfcheck(output_dir, work_dir):
    """逐项校验 result.json，打印 PASS/FAIL，返回 (ok, checks)。"""
    checks = []
    result = load_json_file(os.path.join(output_dir, "result.json"))
    checks.append(("result.json exists and valid JSON", result is not None))
    if result is None:
        for label, passed in checks:
            print("%s %s" % ("PASS" if passed else "FAIL", label))
        return False, checks

    checks.append(("case_id present", bool(result.get("case_id"))))
    checks.append(("status present", result.get("status") in
                   {"ok", "partial", "insufficient_evidence", "input_error"}))
    checks.append(("output_contract keys echoed",
                   isinstance(result.get("output_contract_keys"), list)))
    checks.append(("sources present", isinstance(result.get("sources"), list)))
    checks.append(("uncertainty present", isinstance(result.get("uncertainty"), list)))
    checks.append(("pending_human present", isinstance(result.get("pending_human"), list)))
    checks.append(("change_log present", isinstance(result.get("change_log"), list)))

    screening = result.get("screening") or {}
    srecs = screening.get("records", []) or []
    checks.append(("every record has decision",
                   all(isinstance(j.get("decision"), str) for j in srecs)))
    checks.append(("every record has reason_code",
                   all(isinstance(j.get("reason_code"), str) for j in srecs)))
    non_uncertain = [j for j in srecs if j.get("decision") not in ("uncertain", "pending_human")]
    checks.append(("non-uncertain records have evidence_quote",
                   all(j.get("evidence_quote") for j in non_uncertain)))
    checks.append(("evidence quotes verified",
                   all(j.get("evidence_verified") for j in non_uncertain)))

    counts = (screening.get("summary") or {}).get("counts", {})
    if counts:
        closed = (counts.get("unique", 0) ==
                  counts.get("included", 0) + counts.get("excluded", 0)
                  + counts.get("uncertain", 0))
        checks.append(("count closure closed", closed))
    else:
        checks.append(("count closure present", False))

    for label, passed in checks:
        print("%s %s" % ("PASS" if passed else "FAIL", label))
    return all(p for _, p in checks), checks


# ---------------------------------------------------------------------------
# 可编辑产物
# ---------------------------------------------------------------------------

def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=False)


def _write_screening_csv(path, records, counts):
    cols = ["record_id", "title", "doi", "decision", "reason_code",
            "evidence_quote", "evidence_verified", "reviewer_a", "reviewer_b",
            "arbitration", "needs_human"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in records:
            row = {k: r.get(k) for k in cols}
            for k in ("reviewer_a", "reviewer_b", "arbitration"):
                if isinstance(row[k], dict):
                    row[k] = json.dumps(row[k], ensure_ascii=False)
            w.writerow(row)


def _write_protocol_md(path, result):
    lines = ["# 筛选协议（protocol）", "",
             "## 研究问题", "", str(result.get("research_question") or "未明确（pending_human）"), ""]
    lines += ["## 纳入标准", ""]
    for c in result.get("inclusion_criteria") or []:
        lines.append("- " + str(c))
    lines += ["", "## 排除标准", ""]
    for c in result.get("exclusion_criteria") or []:
        lines.append("- " + str(c))
    lines += ["", "## 检索策略", ""]
    lines.append("```json")
    lines.append(json.dumps(result.get("search_strategy") or {}, ensure_ascii=False, indent=2))
    lines.append("```")
    lines += ["", "## 计数闭合", "",
              json.dumps((result.get("screening") or {}).get("summary", {}).get("counts", {}),
                         ensure_ascii=False, indent=2)]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(prog="pipeline.py", add_help=False)
    p.add_argument("command", nargs="?", default="selfcheck",
                   choices=["skeleton", "normalize", "finalize", "selfcheck"])
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.add_argument("-h", "--help", action="store_true")
    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.help:
        parser.print_help()
        return 0
    if args.command == "skeleton":
        run_skeleton(args.input, args.output, args.work)
    elif args.command == "normalize":
        run_normalize(args.work)
    elif args.command == "finalize":
        run_finalize(args.input, args.output, args.work)
    elif args.command == "selfcheck":
        ok, _ = run_selfcheck(args.output, args.work)
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
