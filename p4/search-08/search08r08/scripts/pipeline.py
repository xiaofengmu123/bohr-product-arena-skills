#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pipeline.py — 系统性文献筛选的可复现账本管线（仅标准库）。

本脚本只负责“可复核的账本”：输入发现、题录规范化、去重键、证据子串校验、
计数闭合、诚实状态与可编辑产物。科学判断（纳入/排除结论）由 Agent 负责，写入
/app/work/records.json、protocol.json、judgments.json 后再由本脚本合并审计。

子命令：
  skeleton      扫描 /input，定位 output_contract，落盘 partial 骨架与工作文件
  normalize     读 records.json，按 DOI/规范化标题去重，产出 dedup.json 与判定模板
  finalize      合并判定、校验证据子串、检查计数闭合，写 result.json 与可编辑产物
  criteria-diff 检测标准版本漂移（记录判定版本 vs 当前协议版本）
  selfcheck     校验 result.json 与合同字段，逐项打印 PASS/FAIL

约束：只用标准库；确定性（固定排序键、NFKC 归一化）；绝不编造 DOI/标题/数值。
"""

import argparse
import csv
import difflib
import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文本文件 2MB 上限

# output_contract 可能使用的键名（按优先级）
CONTRACT_KEYS = ("output_contract", "outputContract", "result_schema",
                 "result_fields", "output")

STATUS_SET = {"ok", "partial", "insufficient_evidence", "input_error"}
DECISION_SET = {"include", "exclude", "uncertain"}


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """容错读取文本：BOM/坏编码降级，超限返回 None。"""
    if not path or not os.path.exists(path) or not os.path.isfile(path):
        return None
    try:
        if os.path.getsize(path) > max_bytes:
            return None
    except OSError:
        return None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError, OSError):
            continue
    return None


def load_json(path):
    txt = read_text(path)
    if txt is None:
        return None, "unreadable_or_oversize"
    try:
        return json.loads(txt), None
    except (json.JSONDecodeError, ValueError) as e:
        return None, "bad_json: %s" % type(e).__name__


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def norm_text(s):
    """NFKC + 折叠空白 + 小写，用于键与证据比对。"""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def norm_doi(s):
    if not s:
        return ""
    d = re.sub(r"https?://(dx\.)?doi\.org/", "", str(s), flags=re.I)
    return norm_text(d)


def stable_id(rec, idx):
    if rec.get("record_id"):
        return str(rec["record_id"])
    return "r%03d" % idx


def ensure_record_ids(records):
    """为缺失 record_id 的记录分配稳定、无冲突的序号。"""
    used = {str(r.get("record_id")) for r in records if r.get("record_id")}
    i = 1
    for rec in records:
        if not rec.get("record_id"):
            while ("r%03d" % i) in used:
                i += 1
            rec["record_id"] = "r%03d" % i
            used.add(rec["record_id"])
            i += 1
    return records


def sorted_records(records):
    return sorted(records, key=lambda r: stable_id(r, 0))


# --------------------------------------------------------------------------- #
# 输入发现
# --------------------------------------------------------------------------- #
def scan_input(input_dir):
    """扫描输入目录，返回文件清单（不假设固定文件名）。"""
    items = []
    if not os.path.isdir(input_dir):
        return items
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for fn in sorted(files):
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, input_dir)
            try:
                size = os.path.getsize(p)
            except OSError:
                size = -1
            items.append({
                "path": p,
                "rel": rel.replace(os.sep, "/"),
                "size": size,
                "ext": os.path.splitext(fn)[1].lower(),
                "oversize": size > MAX_FILE_BYTES,
            })
    return items


def find_case_json(input_dir):
    cands = []
    for it in scan_input(input_dir):
        base = os.path.basename(it["rel"]).lower()
        if base == "case.json" or base.startswith("case") and base.endswith(".json"):
            cands.append(it["path"])
    return cands[0] if cands else None


def extract_case_id(case):
    if isinstance(case, dict):
        for k in ("case_id", "caseId", "id", "challenge_id", "challengeId"):
            v = case.get(k)
            if v and isinstance(v, (str, int)):
                return str(v)
    return "unknown"


def extract_contract(case):
    """返回 (contract_value, kind)；kind ∈ {dict,list,str,missing}。"""
    if isinstance(case, dict):
        for k in CONTRACT_KEYS:
            if k in case and case[k] is not None:
                v = case[k]
                if isinstance(v, dict):
                    return v, "dict"
                if isinstance(v, list):
                    return v, "list"
                if isinstance(v, str):
                    return v, "str"
    return None, "missing"


def _looks_like_array(spec):
    if isinstance(spec, dict):
        t = str(spec.get("type", "")).lower()
        if t in ("array", "list"):
            return True
        if spec.get("is_list") or spec.get("multiple"):
            return True
    if isinstance(spec, list):
        return True
    return False


def contract_placeholder(contract, kind):
    """把 output_contract 的全部键以 null/[] 占位（不改名、不删键）。"""
    ph = {}
    if kind == "dict":
        if isinstance(contract.get("fields"), dict) and contract["fields"]:
            for k, spec in contract["fields"].items():
                ph[str(k)] = [] if _looks_like_array(spec) else None
        elif isinstance(contract.get("fields"), list) and contract["fields"]:
            for k in contract["fields"]:
                ph[str(k)] = None
        for k, v in contract.items():
            if k == "fields":
                continue
            ph.setdefault(str(k), [] if _looks_like_array(v) else None)
    elif kind == "list":
        for x in contract:
            if isinstance(x, str):
                ph[x] = None
    elif kind == "str":
        ph[str(contract)] = None
    return ph


def contract_field_names(contract, kind):
    return list(contract_placeholder(contract, kind).keys())


# --------------------------------------------------------------------------- #
# 题录导入（CSV/TSV/JSON/JSONL/RIS/BibTeX，容错解析）
# --------------------------------------------------------------------------- #
COLUMN_ALIASES = {
    "title": ("title", "ti", "题目", "标题", "篇名"),
    "doi": ("doi", "do", "digitalobjectidentifier"),
    "authors": ("authors", "author", "au", "作者"),
    "abstract": ("abstract", "ab", "摘要"),
    "year": ("year", "py", "date", "pubyear", "年份", "出版年"),
    "journal": ("journal", "source", "jo", "journaltitle", "期刊", "来源"),
    "url": ("url", "link", "uri", "ur", "链接"),
    "language": ("language", "lang", "语种", "语言"),
}
FIELD_TO_ALIAS = {}
for _field, _aliases in COLUMN_ALIASES.items():
    for _a in _aliases:
        FIELD_TO_ALIAS[norm_text(_a)] = _field


def _map_columns(header):
    """把表头映射到标准字段；返回 dict: col_index -> field。"""
    m = {}
    for i, h in enumerate(header):
        f = FIELD_TO_ALIAS.get(norm_text(h))
        if f:
            m.setdefault(f, i)
    return m


def parse_delimited(text, ext, source_rel):
    """解析 CSV/TSV，返回记录列表。"""
    delim = "\t" if ext in (".tsv", ".tab") else ","
    reader = csv.reader(text.splitlines())
    rows = [r for r in reader if r]
    if not rows:
        return []
    colmap = _map_columns(rows[0])
    records = []
    for r in rows[1:]:
        if not r or all(not c.strip() for c in r):
            continue
        rec = {"source": source_rel}
        for f, i in colmap.items():
            if i < len(r) and r[i].strip():
                rec[f] = r[i].strip()
        if rec.get("title") or rec.get("doi"):
            records.append(rec)
    return records


def parse_json_records(data, source_rel):
    records = []
    if isinstance(data, list):
        for it in data:
            if isinstance(it, dict):
                it = dict(it)
                it["source"] = source_rel
                records.append(it)
    elif isinstance(data, dict):
        for k in ("records", "results", "items", "papers", "references"):
            if isinstance(data.get(k), list):
                for it in data[k]:
                    if isinstance(it, dict):
                        it = dict(it)
                        it["source"] = source_rel
                        records.append(it)
                break
    return records


def parse_jsonl(text, source_rel):
    records = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            it = json.loads(line)
        except ValueError:
            continue
        if isinstance(it, dict):
            it = dict(it)
            it["source"] = source_rel
            records.append(it)
    return records


RIS_TAG = {
    "TI": "title", "T1": "title", "AU": "authors", "A1": "authors",
    "AB": "abstract", "DO": "doi", "PY": "year", "JO": "journal",
    "T2": "journal", "UR": "url", "LA": "language", "DA": "date",
}


def parse_ris(text, source_rel):
    records, cur = [], None
    for line in text.splitlines():
        if line.startswith("TY  -"):
            if cur:
                records.append(cur)
            cur = {"source": source_rel}
        elif line.startswith("ER  -") or line.startswith("ER -"):
            if cur:
                records.append(cur)
            cur = None
        elif cur is not None and len(line) >= 6 and line[2:4] == "  -":
            tag = line[:2].strip().upper()
            val = line[6:].strip()
            f = RIS_TAG.get(tag)
            if f and val:
                cur[f] = (cur.get(f) + "; " + val).strip("; ") if f == "authors" else val
    if cur:
        records.append(cur)
    return [r for r in records if r.get("title") or r.get("doi")]


def parse_bibtex(text, source_rel):
    records = []
    # 粗切 entry，再取字段；括号成对匹配简化为：从 @ 到下一个 @ 或文末
    for m in re.finditer(r"@(\w+)\s*\{([^@]*)", text, flags=re.S):
        body = m.group(2)
        fld = {}
        for fm in re.finditer(r"(\w+)\s*=\s*[{\"]([^}\"]*)[}\"]", body):
            fld[fm.group(1).lower()] = fm.group(2).strip()
        rec = {"source": source_rel}
        alias = {"title": "title", "author": "authors", "authors": "authors",
                 "abstract": "abstract", "doi": "doi", "year": "year",
                 "journal": "journal", "url": "url", "language": "language"}
        for k, v in fld.items():
            if k in alias:
                rec[alias[k]] = v
        if rec.get("title") or rec.get("doi"):
            records.append(rec)
    return records


def ingest_records(input_dir):
    """从结构化导出文件导入题录；PDF 等不可自动解析的材料留给 Agent 读取。"""
    records = []
    for it in scan_input(input_dir):
        if it["oversize"] or it["ext"] not in (".csv", ".tsv", ".tab", ".json",
                                                ".jsonl", ".ris", ".bib", ".txt"):
            continue
        txt = read_text(it["path"])
        if txt is None:
            continue
        try:
            if it["ext"] in (".csv", ".tsv", ".tab"):
                records += parse_delimited(txt, it["ext"], it["rel"])
            elif it["ext"] == ".jsonl":
                records += parse_jsonl(txt, it["rel"])
            elif it["ext"] == ".json":
                data, err = load_json(it["path"])
                if data is not None:
                    records += parse_json_records(data, it["rel"])
            elif it["ext"] == ".ris":
                records += parse_ris(txt, it["rel"])
            elif it["ext"] == ".bib":
                records += parse_bibtex(txt, it["rel"])
        except Exception:  # noqa: BLE001 —— 单文件解析失败不阻断整体
            continue
    # 规范化常见字段并补 record_id
    out = []
    for r in records:
        rec = {k: (str(v) if v is not None else None) for k, v in r.items()}
        if rec.get("year") is not None and str(rec["year"]).strip():
            m = re.search(r"\d{4}", str(rec["year"]))
            rec["year"] = m.group(0) if m else None
        rec["source"] = rec.get("source", "unknown")
        # 跳过完全空记录
        if not rec.get("title") and not rec.get("doi"):
            continue
        out.append(rec)
    # 为缺失 record_id 的记录分配稳定序号（确定性）
    for i, rec in enumerate(out):
        if not rec.get("record_id"):
            rec["record_id"] = "r%03d" % (i + 1)
    return out


# --------------------------------------------------------------------------- #
# 去重
# --------------------------------------------------------------------------- #
def dedup(records):
    """按 DOI（优先）与规范化标题分组；相似标题只标 possible_duplicate，不自动合并。"""
    records = sorted_records(records)
    # 第一阶段：DOI 精确分组
    doi_groups = {}
    rest = []
    for r in records:
        d = norm_doi(r.get("doi"))
        if d:
            doi_groups.setdefault(d, []).append(r)
        else:
            rest.append(r)
    # 第二阶段：标题精确分组（处理跨来源副本、预印本与正式版）
    clusters = []
    for d in sorted(doi_groups):
        clusters.append({"key": "doi:" + d, "records": sorted_records(doi_groups[d])})
    title_groups = {}
    for r in rest:
        t = norm_text(r.get("title"))
        if t:
            title_groups.setdefault(t, []).append(r)
        else:
            # 无 DOI 无标题：单独成簇，绝不与其他记录合并
            clusters.append({"key": "fallback", "records": [r]})
    for t in sorted(title_groups):
        grp = sorted_records(title_groups[t])
        # 标题相同但 DOI 不同（均非空）→ 不自动合并，留人工
        dois = {norm_doi(r.get("doi")) for r in grp}
        dois.discard("")
        if len(dois) > 1:
            clusters.append({"key": "title:" + t, "records": grp,
                             "conflict": "title_match_conflicting_doi"})
        else:
            clusters.append({"key": "title:" + t, "records": grp})

    # 代表记录 + 合并理由
    unique, merge_log, possible = [], [], []
    for cl in clusters:
        grp = cl["records"]
        rep = grp[0]
        if len(grp) > 1:
            reason = cl.get("conflict") or ("doi_match" if cl["key"].startswith("doi:")
                                            else "title_match_preprint_or_export")
            if cl.get("conflict"):
                # 冲突情形不自动合并：全部保留为独立唯一记录并标记
                for g in grp:
                    unique.append(g)
                possible.append({
                    "record_ids": [stable_id(g, 0) for g in grp],
                    "reason": cl["conflict"],
                    "action": "manual",
                })
                continue
            unique.append(rep)
            merge_log.append({
                "cluster_id": "c%03d" % (len(merge_log) + 1),
                "representative": stable_id(rep, 0),
                "merged_record_ids": [stable_id(g, 0) for g in grp],
                "merge_reason": reason,
                "evidence": {
                    "doi": rep.get("doi"),
                    "title": rep.get("title"),
                },
            })
        else:
            unique.append(rep)

    # 相似标题 0.85–0.95 → possible_duplicate（不自动合并）；短标题只做精确匹配
    reps = sorted_records(unique)
    for i in range(len(reps)):
        for j in range(i + 1, len(reps)):
            ti, tj = norm_text(reps[i].get("title")), norm_text(reps[j].get("title"))
            if not ti or not tj:
                continue
            if len(ti) < 15 or len(tj) < 15:
                continue
            ratio = difflib.SequenceMatcher(None, ti, tj).ratio()
            if 0.85 <= ratio < 0.95:
                possible.append({
                    "record_ids": [stable_id(reps[i], 0), stable_id(reps[j], 0)],
                    "title_similarity": round(ratio, 3),
                    "reason": "title_similarity",
                    "action": "manual_review",
                })
    stats = {
        "retrieved": len(records),
        "merged": len(records) - len(unique),
        "unique": len(unique),
        "clusters": len(clusters),
        "possible_duplicates": len(possible),
    }
    return unique, merge_log, possible, stats


def build_records_map(records):
    return {stable_id(r, 0): r for r in records}


# --------------------------------------------------------------------------- #
# 证据校验与汇总
# --------------------------------------------------------------------------- #
def _haystacks(rec):
    """证据可命中于标题或摘要（按规范化文本）。"""
    return {
        "title": norm_text(rec.get("title")),
        "abstract": norm_text(rec.get("abstract")),
    }


def verify_evidence(quote, rec):
    """quote 逐字命中标题/摘要则 True；空 quote 视为不适用（返回 None）。"""
    if quote is None or not str(quote).strip():
        return None
    q = norm_text(quote)
    if not q:
        return None
    hs = _haystacks(rec)
    return q in hs["title"] or q in hs["abstract"]


def build_screening(unique, judgments_by_id):
    included, excluded, uncertain, screening = [], [], [], []
    for rec in sorted_records(unique):
        rid = stable_id(rec, 0)
        j = judgments_by_id.get(rid) or {}
        decision = j.get("decision") if j.get("decision") in DECISION_SET else None
        quote = j.get("evidence_quote")
        ver = verify_evidence(quote, rec) if decision and decision != "uncertain" else None
        entry = {
            "record_id": rid,
            "title": rec.get("title"),
            "doi": rec.get("doi"),
            "decision": decision or "unjudged",
            "reason_code": j.get("reason_code"),
            "evidence_quote": quote,
            "evidence_verified": ver,
            "reviewer_a": j.get("reviewer_a"),
            "reviewer_b": j.get("reviewer_b"),
            "conflict": j.get("conflict"),
            "arbitration": j.get("arbitration"),
            "needs_human": bool(j.get("needs_human") or decision is None),
        }
        screening.append(entry)
        if decision == "include":
            included.append(rid)
        elif decision == "exclude":
            excluded.append(rid)
        elif decision == "uncertain":
            uncertain.append(rid)
    return {
        "records": screening,
        "included": included,
        "excluded": excluded,
        "uncertain": uncertain,
        "unjudged": [s["record_id"] for s in screening if s["decision"] == "unjudged"],
    }


def build_audit(retrieved, merged, unique, screening, judgments):
    inc = len(screening["included"])
    exc = len(screening["excluded"])
    unc = len(screening["uncertain"])
    unj = len(screening["unjudged"])
    judged = inc + exc + unc
    return {
        "retrieval": {"in": retrieved, "out": retrieved},
        "deduplication": {"in": retrieved, "merged": merged, "out": unique},
        "screening": {"in": unique, "include": inc, "exclude": exc,
                      "uncertain": unc, "unjudged": unj},
        "closure": {
            "retrieved_minus_merged": retrieved - merged,
            "unique": unique,
            "sum_decisions": judged,
            "closed": (retrieved - merged == unique) and (unique == judged),
        },
        "judgments_count": len(judgments),
    }


def build_conflicts(screening):
    out = []
    for s in screening["records"]:
        c = s.get("conflict")
        if c and c.get("is_conflict"):
            out.append({
                "record_id": s["record_id"],
                "conflict_type": c.get("type"),
                "reviewer_a": s.get("reviewer_a"),
                "reviewer_b": s.get("reviewer_b"),
                "arbitration": s.get("arbitration"),
                "needs_human": s.get("needs_human"),
            })
    return out


def load_judgments(work_dir):
    data, err = load_json(os.path.join(work_dir, "judgments.json"))
    if data is None:
        return [], None
    jlist = data.get("judgments", data) if isinstance(data, dict) else data
    if not isinstance(jlist, list):
        return [], "judgments_not_list"
    by_id = {}
    for j in jlist:
        if isinstance(j, dict) and j.get("record_id"):
            by_id[str(j["record_id"])] = j
    return jlist, by_id


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #
def cmd_skeleton(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    errors, degradations = [], []
    inventory = scan_input(args.input)

    case_path = find_case_json(args.input)
    case_id, contract_fields, contract_ph = "unknown", [], {}
    case_data, contract, kind = None, None, "missing"
    if case_path is None:
        degradations.append({"kind": "missing_case_json",
                             "detail": "未在 /input 找到 case.json"})
    else:
        case_data, err = load_json(case_path)
        if case_data is None:
            degradations.append({"kind": "bad_case_json", "detail": err,
                                 "file": os.path.basename(case_path)})
        elif not isinstance(case_data, dict):
            degradations.append({"kind": "case_json_not_object",
                                 "file": os.path.basename(case_path)})
        else:
            case_id = extract_case_id(case_data)
            contract, kind = extract_contract(case_data)
            contract_ph = contract_placeholder(contract, kind)
            contract_fields = contract_field_names(contract, kind)

    # 题录：结构化文件可自动导入；PDF 材料留待 Agent 读取后回写 records.json
    records = ingest_records(args.input)
    if not records:
        degradations.append({"kind": "no_structured_records",
                             "detail": "未从结构化导出文件导入题录；若材料为 PDF，"
                                       "请 Agent 读取后把题录写入 /app/work/records.json"})
    ensure_record_ids(records)

    records_doc = {"retrieved_total": len(records), "records": records}
    protocol_doc = {
        "research_question": None,
        "criteria_version": "v1",
        "criteria": [],
        "query": {"string": None, "databases": [], "fields": [],
                  "date_range": [], "language": [], "search_date": None},
        "frozen_at": None,
        "contract_field_names": contract_fields,
        "contract_kind": kind,
        "contract_placeholder": contract_ph,
    }

    write_json(os.path.join(args.work, "inventory.json"), {"files": inventory,
                                                           "case_path": case_path})
    write_json(os.path.join(args.work, "records.json"), records_doc)
    write_json(os.path.join(args.work, "protocol.json"), protocol_doc)
    # 判定模板：骨架阶段先生成空模板，便于 Agent 直接填充
    unique, _, _, _ = dedup(records)
    template = {"criteria_version": "v1", "judgments": [
        {"record_id": stable_id(r, 0), "decision": None, "reason_code": None,
         "evidence_quote": None, "reviewer_a": None, "reviewer_b": None,
         "conflict": None, "arbitration": None, "needs_human": False}
        for r in sorted_records(unique)]}
    write_json(os.path.join(args.work, "judgments.json"), template)

    status = "insufficient_evidence" if (case_path is None or not records) else "partial"
    result = {"case_id": case_id, "status": status}
    result.update(contract_ph)
    result.update({
        "research_question": None,
        "inclusion_criteria": [],
        "exclusion_criteria": [],
        "query": None,
        "sources": [],
        "uncertainty": [],
        "deduplication": {"clusters": [], "possible_duplicates": [],
                          "stats": {"retrieved": len(records), "merged": 0,
                                    "unique": len(unique)}},
        "screening": {"records": [], "included": [], "excluded": [],
                      "uncertain": [], "unjudged": []},
        "conflicts": [],
        "audit": {"retrieval": {"in": 0, "out": 0},
                  "deduplication": {"in": 0, "merged": 0, "out": 0},
                  "screening": {"in": 0, "include": 0, "exclude": 0,
                                "uncertain": 0, "unjudged": 0},
                  "closure": {"retrieved_minus_merged": 0, "unique": 0,
                              "sum_decisions": 0, "closed": False}},
        "errors": errors,
        "degradations": degradations,
        "pending_human": [],
        "change_log": [{"at": now_iso(), "step": "skeleton",
                        "note": "落盘 partial 骨架；contract 键以 null/[] 占位，待 Agent 回填"}],
        "runtime": {"criteria_version": "v1"},
    })
    write_json(os.path.join(args.output, "result.json"), result)
    print("skeleton: case_id=%s contract_fields=%d records=%d status=%s"
          % (case_id, len(contract_fields), len(records), status))
    return 0


def cmd_normalize(args):
    os.makedirs(args.work, exist_ok=True)
    rec_doc, err = load_json(os.path.join(args.work, "records.json"))
    if rec_doc is None:
        print("normalize: records.json 不可读（%s）" % err)
        return 1
    records = rec_doc.get("records") if isinstance(rec_doc, dict) else []
    if not isinstance(records, list):
        records = []
    ensure_record_ids(records)
    unique, merge_log, possible, stats = dedup(records)

    # 若已存在 judgments.json，保留已填判定，否则重写模板
    existing, _ = load_json(os.path.join(args.work, "judgments.json"))
    kept = {}
    if isinstance(existing, dict) and isinstance(existing.get("judgments"), list):
        for j in existing["judgments"]:
            if isinstance(j, dict) and j.get("record_id"):
                kept[str(j["record_id"])] = j
    template_judgments = []
    for r in sorted_records(unique):
        rid = stable_id(r, 0)
        base = kept.get(rid) or {}
        template_judgments.append({
            "record_id": rid,
            "decision": base.get("decision"),
            "reason_code": base.get("reason_code"),
            "evidence_quote": base.get("evidence_quote"),
            "reviewer_a": base.get("reviewer_a"),
            "reviewer_b": base.get("reviewer_b"),
            "conflict": base.get("conflict"),
            "arbitration": base.get("arbitration"),
            "needs_human": bool(base.get("needs_human", False)),
        })
    proto, _ = load_json(os.path.join(args.work, "protocol.json"))
    cv = "v1"
    if isinstance(proto, dict) and proto.get("criteria_version"):
        cv = str(proto["criteria_version"])
    write_json(os.path.join(args.work, "judgments.json"),
               {"criteria_version": cv, "judgments": template_judgments})
    write_json(os.path.join(args.work, "dedup.json"),
               {"unique_records": unique, "clusters": merge_log,
                "possible_duplicates": possible, "stats": stats})
    print("normalize: retrieved=%d merged=%d unique=%d possible_dup=%d"
          % (stats["retrieved"], stats["merged"], stats["unique"],
             stats["possible_duplicates"]))
    return 0


def cmd_finalize(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    errors, degradations = [], []

    rec_doc, err = load_json(os.path.join(args.work, "records.json"))
    records = (rec_doc or {}).get("records", []) if isinstance(rec_doc, dict) else []
    if not isinstance(records, list):
        records = []
    unique, merge_log, possible, stats = dedup(records)
    rec_map = build_records_map(unique)

    jlist, judgments_by_id = load_judgments(args.work)
    if jlist is None:
        degradations.append({"kind": "judgments_missing",
                             "detail": "judgments.json 缺失或不可读，所有记录记为 unjudged"})
        judgments_by_id = {}

    screening = build_screening(unique, judgments_by_id)
    audit = build_audit(stats["retrieved"], stats["merged"], stats["unique"],
                        screening, jlist or [])
    conflicts = build_conflicts(screening)

    # 证据命中校验：非 uncertain 且带 quote 但未命中 → 记录退化
    for s in screening["records"]:
        if s["decision"] in ("include", "exclude") and s["evidence_quote"] \
                and s["evidence_verified"] is False:
            degradations.append({"kind": "evidence_mismatch",
                                 "record_id": s["record_id"],
                                 "detail": "evidence_quote 未在标题/摘要中逐字命中"})

    # 标准版本漂移检测
    proto, _ = load_json(os.path.join(args.work, "protocol.json"))
    cur_cv = "v1"
    if isinstance(proto, dict) and proto.get("criteria_version"):
        cur_cv = str(proto["criteria_version"])
    rejudge = [s["record_id"] for s in screening["records"]
               if (judgments_by_id.get(s["record_id"]) or {}).get("criteria_version")
               and str((judgments_by_id[s["record_id"]].get("criteria_version"))) != cur_cv]
    if rejudge:
        degradations.append({"kind": "criteria_drift",
                             "detail": "记录判定版本与当前协议不一致，需回溯重判",
                             "record_ids": rejudge})

    # 读回既有 result.json，保留 Agent 回填的 contract 键值
    result, _ = load_json(os.path.join(args.output, "result.json"))
    if not isinstance(result, dict):
        result = {}
    case_id = result.get("case_id", "unknown")
    result.setdefault("case_id", case_id)

    # contract 键：只补缺失键为 null/[] 占位，不覆盖 Agent 已回填的值
    ph = (proto or {}).get("contract_placeholder") or {}
    for k, v in ph.items():
        result.setdefault(k, v)

    pending_ids = [s["record_id"] for s in screening["records"] if s["needs_human"]]
    pending_ids += [c["record_id"] for c in conflicts if c.get("needs_human")]
    pending_human = sorted(set(pending_ids))

    if stats["retrieved"] == 0:
        status = "insufficient_evidence"
    elif audit["closure"]["closed"] and not degradations:
        status = "ok"
    else:
        status = "partial"
    result.update({
        "status": status,
        "research_question": (proto or {}).get("research_question"),
        "inclusion_criteria": (proto or {}).get("criteria") and [
            c for c in proto["criteria"] if c.get("type") == "inclusion"],
        "exclusion_criteria": (proto or {}).get("criteria") and [
            c for c in proto["criteria"] if c.get("type") == "exclusion"],
        "query": (proto or {}).get("query"),
        "deduplication": {"clusters": merge_log,
                          "possible_duplicates": possible,
                          "stats": stats},
        "screening": screening,
        "conflicts": conflicts,
        "audit": audit,
        "errors": errors,
        "degradations": degradations,
        "pending_human": pending_human,
        "runtime": {"criteria_version": cur_cv},
    })
    # 保留 sources/uncertainty 若 Agent 已写，否则给空
    result.setdefault("sources", [])
    result.setdefault("uncertainty", [])
    result.setdefault("change_log", [])
    result.setdefault("pending_human", [])
    result["change_log"].append({"at": now_iso(), "step": "finalize",
                                 "note": "合并判定并校验；closure=%s" %
                                         audit["closure"]["closed"]})
    # 去重（change_log 保留最后一个 finalize 记录即可，避免无限增长）
    seen = {}
    for e in result["change_log"]:
        seen[(e.get("step"), e.get("note"))] = e
    result["change_log"] = list(seen.values())
    write_json(os.path.join(args.output, "result.json"), result)

    # 可编辑产物
    write_screening_csv(os.path.join(args.output, "screening_table.csv"),
                        screening["records"])
    write_json(os.path.join(args.output, "audit_log.json"),
               {"case_id": case_id, "criteria_version": cur_cv, "audit": audit,
                "degradations": degradations, "change_log": result["change_log"]})
    write_protocol_md(os.path.join(args.output, "protocol.md"), proto, stats, audit)

    print("finalize: closure=%s included=%d excluded=%d uncertain=%d unjudged=%d"
          % (audit["closure"]["closed"], len(screening["included"]),
             len(screening["excluded"]), len(screening["uncertain"]),
             len(screening["unjudged"])))
    return 0


def write_screening_csv(path, records):
    cols = ["record_id", "decision", "reason_code", "title", "doi",
            "evidence_quote", "evidence_verified", "needs_human"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in records:
            w.writerow({k: r.get(k) for k in cols})


def write_protocol_md(path, proto, stats, audit):
    proto = proto or {}
    lines = ["# 筛选协议", ""]
    lines.append("- research_question: `%s`" % (proto.get("research_question") or "未填写"))
    lines.append("- criteria_version: `%s`" % (proto.get("criteria_version") or "v1"))
    lines.append("- frozen_at: `%s`" % (proto.get("frozen_at") or "未冻结"))
    lines.append("")
    lines.append("## 纳入/排除标准")
    for c in proto.get("criteria") or []:
        lines.append("- [%s] (%s) %s" % (c.get("id", "?"), c.get("type", "?"),
                                         c.get("clause", "")))
    lines.append("")
    lines.append("## 检索式")
    q = proto.get("query") or {}
    lines.append("- string: `%s`" % (q.get("string") or "未填写"))
    lines.append("- databases: %s" % (q.get("databases") or []))
    lines.append("- fields: %s" % (q.get("fields") or []))
    lines.append("- date_range: %s" % (q.get("date_range") or []))
    lines.append("- language: %s" % (q.get("language") or []))
    lines.append("- search_date: %s" % (q.get("search_date") or "未填写"))
    lines.append("")
    lines.append("## 计数闭合")
    lines.append("- retrieved=%d merged=%d unique=%d" %
                 (stats.get("retrieved", 0), stats.get("merged", 0),
                  stats.get("unique", 0)))
    lines.append("- include=%d exclude=%d uncertain=%d closed=%s" %
                 (audit["screening"]["include"], audit["screening"]["exclude"],
                  audit["screening"]["uncertain"], audit["closure"]["closed"]))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def cmd_criteria_diff(args):
    proto, _ = load_json(os.path.join(args.work, "protocol.json"))
    jlist, by_id = load_judgments(args.work)
    cur_cv = (proto or {}).get("criteria_version", "v1")
    drifted = []
    for j in (jlist or []):
        if j.get("criteria_version") and str(j["criteria_version"]) != str(cur_cv):
            drifted.append({"record_id": j.get("record_id"),
                            "judged_version": j.get("criteria_version"),
                            "current_version": cur_cv})
    print("criteria-diff: current=%s drifted_records=%d" % (cur_cv, len(drifted)))
    for d in drifted:
        print("  %s judged@%s -> %s" % (d["record_id"], d["judged_version"],
                                        d["current_version"]))
    return 0


def cmd_selfcheck(args):
    failures = 0

    def check(name, ok, detail=""):
        nonlocal failures
        print("[%s] %s %s" % ("PASS" if ok else "FAIL", name, detail))
        if not ok:
            failures += 1

    result, err = load_json(os.path.join(args.output, "result.json"))
    check("result.json 存在且合法", result is not None, err or "")
    if result is None:
        return 1
    check("case_id 存在且非空", bool(result.get("case_id")))
    check("status 合法", result.get("status") in STATUS_SET,
          str(result.get("status")))

    proto, _ = load_json(os.path.join(args.work, "protocol.json"))
    contract_fields = (proto or {}).get("contract_field_names") or []
    missing = [k for k in contract_fields if k not in result]
    check("contract 字段全部存在", not missing, "; ".join(missing) if missing else "")

    screening = result.get("screening") or {}
    records = screening.get("records") or []
    bad_decision = [r.get("record_id") for r in records
                    if r.get("decision") not in ("include", "exclude", "uncertain")]
    check("每条记录 decision 合法", not bad_decision, str(bad_decision[:5]))
    no_rc = [r.get("record_id") for r in records if not r.get("reason_code")]
    check("每条记录有 reason_code", not no_rc, str(no_rc[:5]))
    no_quote = [r.get("record_id") for r in records
                if r.get("decision") in ("include", "exclude")
                and not r.get("evidence_quote")]
    check("非 uncertain 有 evidence_quote", not no_quote, str(no_quote[:5]))
    mism = [d for d in (result.get("degradations") or [])
            if d.get("kind") == "evidence_mismatch"]
    check("无 evidence mismatch", not mism, "%d 条" % len(mism))

    audit = result.get("audit") or {}
    check("计数闭合", audit.get("closure", {}).get("closed") is True,
          str(audit.get("closure")))

    conflicts = result.get("conflicts") or []
    unarb = [c for c in conflicts if not c.get("arbitration")
             and not c.get("needs_human")]
    check("冲突均有仲裁或待人工", not unarb, str([c.get("record_id") for c in unarb[:5]]))

    dp = result.get("deduplication") or {}
    poss = dp.get("possible_duplicates") or []
    auto_merged_poss = [p for p in poss if p.get("action") not in ("manual", "manual_review")]
    check("possible_duplicate 未自动合并", not auto_merged_poss)
    print("selfcheck: %s" % ("ALL PASS" if failures == 0 else "%d FAIL" % failures))
    return 0 if failures == 0 else 2


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=os.environ.get("SKILL_INPUT", DEFAULT_INPUT))
    common.add_argument("--output", default=os.environ.get("SKILL_OUTPUT", DEFAULT_OUTPUT))
    common.add_argument("--work", default=os.environ.get("SKILL_WORK", DEFAULT_WORK))
    p = argparse.ArgumentParser(prog="pipeline.py",
                                description="系统性文献筛选的可复现账本管线")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("skeleton", parents=[common], help="落盘 partial 骨架与工作文件")
    sub.add_parser("normalize", parents=[common], help="去重并生成判定模板")
    sub.add_parser("finalize", parents=[common], help="合并判定、校验并写产物")
    sub.add_parser("criteria-diff", parents=[common], help="检测标准版本漂移")
    sub.add_parser("selfcheck", parents=[common], help="自检 result.json")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    fn = {"skeleton": cmd_skeleton, "normalize": cmd_normalize,
          "finalize": cmd_finalize, "criteria-diff": cmd_criteria_diff,
          "selfcheck": cmd_selfcheck}[args.command]
    return fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
