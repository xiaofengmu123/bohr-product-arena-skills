#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — 确定性账本后端：输入发现、契约提取、去重、纳排合并、证据校验与审计。

本文件只做“可复现的账本”：
  规范化、去重键、证据子串校验、计数闭合、诚实状态与显式 limitations/uncertainty。
科学判断（每条记录纳入/排除/待定及其理由）由调用方（Agent/模型）完成，写入
`/app/work/judgments.json` 后由 `finalize` 校验；脚本不替代科学判断，也不编造文献与结论。

仅依赖 Python 标准库。重复运行结果一致（确定性命中顺序、稳定排序、固定键名）。
"""

from __future__ import annotations

import argparse
import csv
import difflib
import io
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone

VERSION = "1.1.0"

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_TEXT_BYTES = 2_000_000          # 单文本文件上限（字节），超限跳过并记录
NEAR_TITLE_THRESHOLD = 0.85         # 标题相似度 >= 该阈值标 possible_duplicate，不自动合并

STATUS_CHOICES = ("ok", "partial", "insufficient_evidence", "input_error")
DECISION_CHOICES = ("include", "exclude", "uncertain")

# 题目可能用来写输出契约/结果字段的键名（大小写/命名风格差异都要识别）
CONTRACT_KEY_CANDIDATES = (
    "output_contract", "outputContract", "result_schema",
    "result_fields", "resultFields", "output",
)
CASE_ID_CANDIDATES = ("case_id", "caseId", "challenge_id", "challengeId", "id")
RESEARCH_Q_CANDIDATES = (
    "research_question", "researchQuestion", "research_questions",
    "researchQuestions", "question",
)
INCLUSION_CANDIDATES = (
    "inclusion_criteria", "inclusionCriteria", "inclusion",
    "inclusion_criterion", "include_criteria",
)
EXCLUSION_CANDIDATES = (
    "exclusion_criteria", "exclusionCriteria", "exclusion",
    "exclusion_criterion", "exclude_criteria",
)
QUERY_CANDIDATES = (
    "query", "search_query", "searchQuery", "search_string",
    "searchString", "search_terms", "检索式", "检索策略",
)
CRITERIA_VERSION_CANDIDATES = ("criteria_version", "criteriaVersion", "criteria_revision")

BIB_LIKE_EXTS = (".csv", ".tsv", ".ris", ".bib", ".json", ".jsonl")


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def collapse_ws(s) -> str:
    if s is None:
        return ""
    return " ".join(str(s).split())


def norm_title(s) -> str:
    """用于去重的标题规范化键：NFKC、小写、去标点、合并空白，保留 CJK 与字母数字。"""
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = s.lower()
    s = re.sub(r"[^\w\u4e00-\u9fff]+", " ", s)
    return collapse_ws(s)


def norm_doi(s):
    """DOI 规范化键；无 DOI 返回 None。"""
    if s is None:
        return None
    s = str(s).strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = re.sub(r"^doi\s*:\s*", "", s)
    s = s.strip().strip(".")
    return s or None


def title_similarity(a, b) -> float:
    ta, tb = norm_title(a), norm_title(b)
    if not ta or not tb:
        return 0.0
    return difflib.SequenceMatcher(None, ta, tb).ratio()


def read_text_bytes(path):
    """容错读取文本：BOM/编码降级；超限返回 oversize。返回 (text|None, error|None)。"""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return None, "read_error:{}".format(e)
    if len(raw) > MAX_TEXT_BYTES:
        return None, "oversize:{}bytes".format(len(raw))
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), None
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("utf-8", errors="replace"), "replaced_invalid_bytes"


def load_json(path):
    txt, err = read_text_bytes(path)
    if err is not None:
        return None, err
    if txt is None:
        return None, "empty_or_unreadable"
    try:
        return json.loads(txt), None
    except json.JSONDecodeError as e:
        return None, "bad_json:{}".format(e)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def deep_find(obj, candidates):
    """在嵌套 dict 中按候选键名（含大小写变体）查找值；找不到返回 None。"""
    if isinstance(obj, dict):
        for k in candidates:
            if k in obj:
                return obj[k]
        for v in obj.values():
            r = deep_find(v, candidates)
            if r is not None:
                return r
    return None


def as_list_of_str(v):
    if v is None:
        return []
    if isinstance(v, str):
        s = collapse_ws(v)
        return [s] if s else []
    if isinstance(v, list):
        out = []
        for item in v:
            if isinstance(item, dict):
                # 常见形态：{"criterion": "...", "note": "..."}
                for k in ("criterion", "criteria", "text", "name", "label", "value"):
                    if item.get(k):
                        out.append(collapse_ws(item[k]))
                        break
            else:
                s = collapse_ws(item)
                if s:
                    out.append(s)
        return out
    if isinstance(v, dict):
        return as_list_of_str(list(v.values()))
    s = collapse_ws(v)
    return [s] if s else []


# --------------------------------------------------------------------------
# 输入发现
# --------------------------------------------------------------------------

def discover_input(inp_dir):
    inv = {
        "input_dir": inp_dir,
        "exists": os.path.isdir(inp_dir),
        "files": [],
        "case_file": None,
        "task_file": None,
        "bib_files": [],
        "material_files": [],
        "notes_files": [],
        "pdf_files": [],
        "skipped": [],
    }
    if not inv["exists"]:
        inv["skipped"].append({"path": inp_dir, "reason": "input_dir_missing"})
        return inv
    for root, dirs, files in os.walk(inp_dir):
        dirs.sort()
        for fn in sorted(files):
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, inp_dir).replace("\\", "/")
            low = fn.lower()
            size = os.path.getsize(p)
            entry = {"path": p, "rel": rel, "name": fn, "size": size}
            inv["files"].append(entry)
            if low == "case.json":
                inv["case_file"] = p
            elif low == "task.md":
                inv["task_file"] = p
            elif low.endswith(".pdf"):
                inv["pdf_files"].append(p)
            elif low.endswith(BIB_LIKE_EXTS) and low != "case.json":
                inv["bib_files"].append(p)
            elif low in ("notes.md", "notes.txt"):
                inv["notes_files"].append(p)
            elif low.endswith((".md", ".txt")):
                inv["material_files"].append(p)
            if size > MAX_TEXT_BYTES and not low.endswith(".pdf"):
                inv["skipped"].append({"path": p, "reason": "oversize:{}bytes".format(size)})
    return inv


# --------------------------------------------------------------------------
# 契约提取
# --------------------------------------------------------------------------

def extract_contract(case_data, task_text):
    contract = {
        "case_id": None,
        "output_contract": None,
        "output_contract_locator": None,
        "research_question": None,
        "inclusion_criteria": [],
        "exclusion_criteria": [],
        "query": None,
        "criteria_version": None,
    }
    if isinstance(case_data, dict):
        cid = deep_find(case_data, CASE_ID_CANDIDATES)
        contract["case_id"] = collapse_ws(cid) or None
        oc = deep_find(case_data, CONTRACT_KEY_CANDIDATES)
        contract["output_contract"] = oc if isinstance(oc, (dict, list)) else None
        contract["research_question"] = collapse_ws(deep_find(case_data, RESEARCH_Q_CANDIDATES)) or None
        contract["inclusion_criteria"] = as_list_of_str(deep_find(case_data, INCLUSION_CANDIDATES))
        contract["exclusion_criteria"] = as_list_of_str(deep_find(case_data, EXCLUSION_CANDIDATES))
        contract["query"] = collapse_ws(deep_find(case_data, QUERY_CANDIDATES)) or None
        contract["criteria_version"] = collapse_ws(deep_find(case_data, CRITERIA_VERSION_CANDIDATES)) or None
    # task.md 补充：契约文本常以字段名形式写在正文（本题目在运行时契约中说明）
    if contract["output_contract"] is None and task_text:
        contract["output_contract_locator"] = "task.md:runtime-contract"
    if contract["case_id"] is None and task_text:
        m = re.search(r"challenge[_\-]id[:\s`]*([A-Za-z0-9_\-]+)", task_text, re.I)
        if m:
            contract["case_id"] = m.group(1)
    return contract


# --------------------------------------------------------------------------
# 题录读取（尽力而为；失败逐文件记录到 degradations，不中断）
# --------------------------------------------------------------------------

def _sniff_delimiter(path):
    txt, err = read_text_bytes(path)
    if err is not None or txt is None:
        return None, None, err
    try:
        dialect = csv.Sniffer().sniff(txt[:4096], delimiters=",\t;|")
        return txt, dialect.delimiter, None
    except csv.Error:
        # 退化为按扩展名猜测
        if path.lower().endswith(".tsv"):
            return txt, "\t", None
        return txt, ",", None


def _rows_to_records(rows, source_label):
    records, degradations = [], []
    header = None
    for i, row in enumerate(rows):
        if not row:
            continue
        if header is None:
            header = [collapse_ws(c).lower() for c in row]
            continue
        d = {}
        for j, v in enumerate(row):
            if j < len(header):
                d[header[j]] = collapse_ws(v)
        rec = _normalize_record_dict(d, source_label, len(records))
        if rec.get("title") or rec.get("doi"):
            records.append(rec)
        else:
            degradations.append({"file": source_label, "row": i, "reason": "no_title_or_doi"})
    return records, degradations


def _normalize_record_dict(d, source_label, seq):
    def pick(*keys):
        for k in keys:
            if k in d and d[k]:
                return d[k]
        return None
    title = pick("title", "ti", "name", "article_title", "publication_title")
    doi = pick("doi", "do", "digital_object_identifier")
    abstract = pick("abstract", "ab", "summary", "description")
    authors = pick("authors", "author", "au", "creator")
    year = pick("year", "py", "date", "publication_year", "published")
    journal = pick("journal", "jo", "source", "publication", "venue")
    return {
        "record_id": None,  # 稍后统一赋稳定 id
        "title": title,
        "doi": doi,
        "abstract": abstract,
        "authors": authors,
        "year": year,
        "journal": journal,
        "source": source_label,
        "_seq": seq,
    }


def _parse_json_records(txt, source_label):
    records, degradations = [], []
    try:
        data = json.loads(txt)
    except json.JSONDecodeError:
        # 可能是 JSONL：逐行解析；仅当逐行也失败才记 degradations
        n_ok, n_bad = 0, 0
        for line in txt.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                n_bad += 1
                continue
            if isinstance(obj, dict):
                records.append(_normalize_record_dict(obj, source_label, len(records)))
                n_ok += 1
        if n_bad:
            degradations.append({"file": source_label, "reason": "jsonl_bad_lines:{}".format(n_bad)})
        if n_ok == 0 and n_bad > 0:
            degradations.append({"file": source_label, "reason": "no_json_or_jsonl_records"})
        return records, degradations
    items = data if isinstance(data, list) else [data]
    for obj in items:
        if isinstance(obj, dict):
            rec = _normalize_record_dict(obj, source_label, len(records))
            if rec.get("title") or rec.get("doi"):
                records.append(rec)
    return records, degradations


def _parse_ris(txt, source_label):
    records, degradations = [], []
    cur = {}
    for line in txt.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        if len(line) >= 2 and line[2] in (" ", "-"):
            tag, val = line[:2].upper(), line[6:].strip() if line[2:4] == "  " else line[6:].strip()
        elif line.startswith("TY  - "):
            cur = {}
            continue
        else:
            continue
        # RIS 记录结束
        if tag == "ER":
            rec = _normalize_record_dict({
                "ti": cur.get("TI"), "doi": cur.get("DO") or cur.get("AN"),
                "ab": cur.get("AB") or cur.get("N2"), "au": cur.get("AU"),
                "py": cur.get("PY"), "jo": cur.get("JO") or cur.get("T2"),
            }, source_label, len(records))
            if rec.get("title") or rec.get("doi"):
                records.append(rec)
            cur = {}
        elif tag == "TY":
            cur = {}
        else:
            cur[tag] = cur.get(tag, "") + ("" if not cur.get(tag) else "; ") + val
    return records, degradations


def _parse_bib(txt, source_label):
    records, degradations = [], []
    # 简单 BibTeX：@type{key, field={value}, ...}
    entry_re = re.compile(r"@\w+\s*\{[^,]*,", re.S)
    field_re = re.compile(r"(\w+)\s*=\s*[{\"](.*?)[}\"]", re.S)
    for m in entry_re.finditer(txt):
        block = txt[m.end():]
        nxt = re.search(r"@\w+\s*\{", block)
        block = block[:nxt.start()] if nxt else block
        d = {}
        for fm in field_re.finditer(block):
            d[fm.group(1).lower()] = collapse_ws(fm.group(2))
        rec = _normalize_record_dict({
            "ti": d.get("title"), "doi": d.get("doi"),
            "ab": d.get("abstract"), "au": d.get("author"),
            "py": d.get("year"), "jo": d.get("journal"),
        }, source_label, len(records))
        if rec.get("title") or rec.get("doi"):
            records.append(rec)
    return records, degradations


def load_records(bib_files):
    records, degradations = [], []
    for p in bib_files:
        rel = os.path.basename(p)
        low = p.lower()
        if low.endswith(".json") or low.endswith(".jsonl"):
            txt, err = read_text_bytes(p)
            if err is not None:
                degradations.append({"file": rel, "reason": err})
                continue
            recs, deg = _parse_json_records(txt, rel)
            records.extend(recs)
            degradations.extend(deg)
        elif low.endswith(".csv") or low.endswith(".tsv"):
            txt, delim, err = _sniff_delimiter(p)
            if err is not None:
                degradations.append({"file": rel, "reason": err})
                continue
            rows = list(csv.reader(io.StringIO(txt), delimiter=delim))
            recs, deg = _rows_to_records(rows, rel)
            records.extend(recs)
            degradations.extend(deg)
        elif low.endswith(".ris"):
            txt, err = read_text_bytes(p)
            if err is not None:
                degradations.append({"file": rel, "reason": err})
                continue
            recs, deg = _parse_ris(txt, rel)
            records.extend(recs)
            degradations.extend(deg)
        elif low.endswith(".bib"):
            txt, err = read_text_bytes(p)
            if err is not None:
                degradations.append({"file": rel, "reason": err})
                continue
            recs, deg = _parse_bib(txt, rel)
            records.extend(recs)
            degradations.extend(deg)
    # 稳定排序并赋 record_id（确定性）
    records.sort(key=lambda r: (norm_title(r.get("title")), norm_doi(r.get("doi")) or "", r.get("_seq", 0)))
    for i, r in enumerate(records):
        r["record_id"] = "rec-{:03d}".format(i + 1)
        r.pop("_seq", None)
    return records, degradations


# --------------------------------------------------------------------------
# 去重
# --------------------------------------------------------------------------

def build_dedup(records):
    """按 DOI 或规范化标题精确合并同一研究；标题相似度 >= 阈值仅标 possible_duplicate。"""
    groups = {}
    order = []
    for i, r in enumerate(records):
        d = norm_doi(r.get("doi"))
        key = ("doi:" + d) if d else ("title:" + norm_title(r.get("title")))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(i)

    clusters, merged_ids = [], set()
    rep_records = []
    rep_by_key = {}
    for key in order:
        idxs = groups[key]
        rep = records[idxs[0]]
        rep_by_key[key] = rep
        rep_records.append(rep)
        if len(idxs) > 1:
            reason = "same_doi" if key.startswith("doi:") else "same_normalized_title"
            clusters.append({
                "cluster_id": "dup-{:03d}".format(len(clusters) + 1),
                "merge_reason": reason,
                "representative_record_id": rep["record_id"],
                "member_record_ids": [records[i]["record_id"] for i in idxs],
                "evidence": [
                    {"record_id": records[i]["record_id"],
                     "title": records[i].get("title"),
                     "doi": records[i].get("doi")}
                    for i in idxs
                ],
            })
            for i in idxs[1:]:
                merged_ids.add(records[i]["record_id"])

    # possible_duplicate：不同精确键之间标题相似
    possibles = []
    keys = list(rep_by_key.keys())
    for a in range(len(keys)):
        for b in range(a + 1, len(keys)):
            ra, rb = rep_by_key[keys[a]], rep_by_key[keys[b]]
            sim = title_similarity(ra.get("title"), rb.get("title"))
            if sim >= NEAR_TITLE_THRESHOLD:
                possibles.append({
                    "pair_id": "poss-{:03d}".format(len(possibles) + 1),
                    "record_id_a": ra["record_id"],
                    "record_id_b": rb["record_id"],
                    "title_similarity": round(sim, 4),
                    "status": "possible_duplicate_not_merged",
                    "note": "标题相似但键不同；不自动合并，需人工/模型确认",
                })

    unique = len(rep_records)
    return {
        "method": "exact merge by normalized DOI or normalized title; "
                 "near-title similarity>={:.2f} flagged possible_duplicate (not auto-merged)".format(NEAR_TITLE_THRESHOLD),
        "clusters": clusters,
        "possible_duplicates": possibles,
        "summary": {
            "retrieved": len(records),
            "merged": len(merged_ids),
            "unique": unique,
        },
    }, rep_records


# --------------------------------------------------------------------------
# 骨架
# --------------------------------------------------------------------------

def build_skeleton(contract, inventory, degradations):
    oc = contract.get("output_contract")
    result = {
        "case_id": contract.get("case_id") or "unknown",
        "status": "partial",
    }
    # 题目 output_contract 的全部字段原样回填占位：缺值 null/[]，不改名、不删键
    if isinstance(oc, dict):
        for k, v in oc.items():
            if isinstance(v, list):
                result[k] = []
            elif isinstance(v, dict):
                result[k] = {kk: None for kk in v} if v else {}
            else:
                result[k] = None
    elif isinstance(oc, list):
        for k in oc:
            if isinstance(k, str):
                result[k] = None
    result.update({
        "research_question": contract.get("research_question"),
        "inclusion_criteria": contract.get("inclusion_criteria") or [],
        "exclusion_criteria": contract.get("exclusion_criteria") or [],
        "query": contract.get("query"),
        "criteria_version": contract.get("criteria_version"),
        "screening": {
            "records": [],
            "summary": {"unique": 0, "included": 0, "excluded": 0, "uncertain": 0},
        },
        "deduplication": {
            "method": None,
            "clusters": [],
            "possible_duplicates": [],
            "summary": {"retrieved": 0, "merged": 0, "unique": 0},
        },
        "conflicts": [],
        "audit": {"stages": [], "count_closed": False},
        "sources": [],
        "uncertainty": {"level": "high", "items": [], "summary": "尚未完成筛选，待增量填充"},
        "limitations": [],
        "pending_human": [],
        "errors": [],
        "degradations": list(degradations),
        "change_log": [
            {"time": now_iso(), "step": "skeleton", "action": "落盘 partial 结果骨架", "status": "partial"},
        ],
        "runtime": {
            "skill_version": VERSION,
            "started_at": now_iso(),
            "input_dir": DEFAULT_INPUT,
            "output_dir": DEFAULT_OUTPUT,
            "work_dir": DEFAULT_WORK,
        },
    })
    return result


# --------------------------------------------------------------------------
# finalize：合并判定、证据校验、计数闭合
# --------------------------------------------------------------------------

def check_quote(quote, record):
    """校验 evidence_quote 是否逐字命中 title/abstract。返回 (ok, match_kind, field)。"""
    if not quote:
        return False, "empty_quote", None
    q = quote.strip()
    texts = []
    for field in ("title", "abstract"):
        v = record.get(field)
        if isinstance(v, str) and v.strip():
            texts.append((field, v.strip()))
    for field, t in texts:
        if q in t:
            return True, "verbatim", field
    qn = collapse_ws(q)
    for field, t in texts:
        tn = collapse_ws(t)
        if qn and qn in tn:
            return True, "normalized_whitespace", field
    return False, "not_found", None


def finalize_records(records, dedup_result, rep_records, judgments, criteria_version):
    """合并判定并校验；返回 (screening, conflicts, audit, errors, degradations, needs_human)。"""
    unique_ids = [r["record_id"] for r in rep_records]
    rec_by_id = {r["record_id"]: r for r in rep_records}
    judg_map = {}
    for j in judgments:
        rid = j.get("record_id")
        if rid:
            judg_map[rid] = j

    screening, conflicts, errors, degradations, needs_human = [], [], [], [], []
    included = excluded = uncertain = 0

    for rid in unique_ids:
        rec = rec_by_id[rid]
        j = judg_map.get(rid)
        if j is None:
            errors.append({"record_id": rid, "type": "missing_judgment",
                           "detail": "唯一记录缺少判定；已标 uncertain/pending_human"})
            screening.append(_mk_uncertain_record(rec, reason_code="unc_missing_judgment",
                                                   needs_human=True))
            uncertain += 1
            needs_human.append({"record_id": rid, "reason": "missing_judgment"})
            continue

        decision = j.get("decision")
        reason_code = j.get("reason_code") or ""
        quote = j.get("evidence_quote")
        if decision not in DECISION_CHOICES:
            errors.append({"record_id": rid, "type": "invalid_decision",
                           "detail": "decision={!r}".format(decision)})
            decision = "uncertain"
            reason_code = reason_code or "unc_invalid_decision"

        evidence_verified = False
        evidence_match = None
        evidence_locator = j.get("evidence_locator") or "record:{}.title".format(rid)
        if decision != "uncertain":
            ok, match_kind, field = check_quote(quote, rec)
            evidence_verified = ok
            evidence_match = match_kind
            if field:
                evidence_locator = "record:{}.{}".format(rid, field)
            if not ok:
                degradations.append({"record_id": rid, "type": "evidence_mismatch",
                                     "detail": "evidence_quote 未在 title/abstract 逐字命中",
                                     "quote": quote})

        needs_h = bool(j.get("needs_human")) or decision == "uncertain"
        if decision == "uncertain":
            needs_human.append({"record_id": rid, "reason": reason_code or "uncertain"})

        conf = j.get("conflict") or {}
        if conf.get("is_conflict"):
            arb = j.get("arbitration") or {}
            conflicts.append({
                "record_id": rid,
                "conflict_type": conf.get("type"),
                "detail": conf.get("detail"),
                "reviewer_a": j.get("reviewer_a"),
                "reviewer_b": j.get("reviewer_b"),
                "arbitration": arb,
                "resolved": bool(arb and arb.get("decision")),
                "needs_human": needs_h,
            })

        item = {
            "record_id": rid,
            "title": rec.get("title"),
            "doi": rec.get("doi"),
            "decision": decision,
            "reason_code": reason_code,
            "evidence_quote": quote,
            "evidence_locator": evidence_locator,
            "evidence_verified": evidence_verified,
            "evidence_match": evidence_match,
            "reviewer_a": j.get("reviewer_a"),
            "reviewer_b": j.get("reviewer_b"),
            "conflict": conf,
            "arbitration": j.get("arbitration"),
            "needs_human": needs_h,
        }
        screening.append(item)
        if decision == "include":
            included += 1
        elif decision == "exclude":
            excluded += 1
        else:
            uncertain += 1

    unique = len(unique_ids)
    count_closed = (included + excluded + uncertain) == unique and \
                   unique == dedup_result["summary"]["unique"]
    if not count_closed:
        errors.append({"type": "count_not_closed",
                       "detail": "included({})+excluded({})+uncertain({}) != unique({})".format(
                           included, excluded, uncertain, unique)})

    audit = {
        "stages": [
            {"stage": "search", "input": None, "output": dedup_result["summary"]["retrieved"],
             "detail": "题录读取（含多来源导出）"},
            {"stage": "dedup", "input": dedup_result["summary"]["retrieved"],
             "output": dedup_result["summary"]["unique"],
             "detail": "merged={}".format(dedup_result["summary"]["merged"])},
            {"stage": "screening", "input": dedup_result["summary"]["unique"],
             "output": unique,
             "detail": "included={} excluded={} uncertain={}".format(included, excluded, uncertain)},
        ],
        "count_closed": count_closed,
        "criteria_version": criteria_version,
    }
    return {
        "screening": {"records": screening,
                      "summary": {"unique": unique, "included": included,
                                  "excluded": excluded, "uncertain": uncertain}},
        "conflicts": conflicts,
        "audit": audit,
        "errors": errors,
        "degradations": degradations,
        "needs_human": needs_human,
        "screening_missing": [r for r in unique_ids if r not in judg_map],
    }


def _mk_uncertain_record(rec, reason_code, needs_human=True):
    return {
        "record_id": rec.get("record_id"),
        "title": rec.get("title"),
        "doi": rec.get("doi"),
        "decision": "uncertain",
        "reason_code": reason_code,
        "evidence_quote": None,
        "evidence_locator": None,
        "evidence_verified": False,
        "evidence_match": None,
        "reviewer_a": None,
        "reviewer_b": None,
        "conflict": None,
        "arbitration": None,
        "needs_human": needs_human,
    }


def uncertainty_level(screening, limitations, needs_human, errors):
    uncertain_n = sum(1 for r in screening if r.get("decision") == "uncertain")
    if uncertain_n or needs_human or errors:
        return "high"
    if limitations:
        return "medium"
    return "low"


def _write_screening_csv(screening, path):
    fields = ["record_id", "title", "doi", "decision", "reason_code",
              "evidence_quote", "evidence_locator", "evidence_verified",
              "needs_human"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in screening:
            row = dict(r)
            row["needs_human"] = "true" if row.get("needs_human") else "false"
            row["evidence_verified"] = "true" if row.get("evidence_verified") else "false"
            w.writerow(row)


def _write_protocol_md(contract, dedup, screening, path):
    lines = ["# 检索与筛选协议（可编辑）", ""]
    lines.append("- case_id: `{}`".format(contract.get("case_id") or "unknown"))
    lines.append("- criteria_version: `{}`".format(contract.get("criteria_version") or "not_reported"))
    lines.append("- research_question: {}".format(contract.get("research_question") or "not_reported"))
    lines.append("")
    lines.append("## 纳入标准")
    for c in contract.get("inclusion_criteria") or []:
        lines.append("- {}".format(c))
    if not contract.get("inclusion_criteria"):
        lines.append("- （未从输入提取到，待人工确认）")
    lines.append("")
    lines.append("## 排除标准")
    for c in contract.get("exclusion_criteria") or []:
        lines.append("- {}".format(c))
    if not contract.get("exclusion_criteria"):
        lines.append("- （未从输入提取到，待人工确认）")
    lines.append("")
    lines.append("## 检索式")
    lines.append("```\n{}\n```".format(contract.get("query") or "not_reported"))
    lines.append("")
    lines.append("## 去重")
    lines.append("- 方法：{}".format(dedup["method"]))
    lines.append("- 计数：retrieved={} merged={} unique={}".format(
        dedup["summary"]["retrieved"], dedup["summary"]["merged"], dedup["summary"]["unique"]))
    for cl in dedup["clusters"]:
        lines.append("- {}（{}）：{} → {}".format(
            cl["cluster_id"], cl["merge_reason"],
            ", ".join(cl["member_record_ids"]), cl["representative_record_id"]))
    for p in dedup["possible_duplicates"]:
        lines.append("- {}(possible_duplicate, sim={}): {} ↔ {}".format(
            p["pair_id"], p["title_similarity"], p["record_id_a"], p["record_id_b"]))
    lines.append("")
    lines.append("## 纳排计数")
    s = screening["summary"]
    lines.append("- unique={} included={} excluded={} uncertain={}".format(
        s["unique"], s["included"], s["excluded"], s["uncertain"]))
    lines.append("")
    lines.append("> 本文件由 finalize 自动生成；依据为 /app/work/{records,judgments,protocol}.json。")
    write_text(path, "\n".join(lines) + "\n")


# --------------------------------------------------------------------------
# 命令入口
# --------------------------------------------------------------------------

def _cmd_skeleton(args):
    inventory = discover_input(args.input)
    contract = {"case_id": None, "output_contract": None, "research_question": None,
                "inclusion_criteria": [], "exclusion_criteria": [], "query": None,
                "criteria_version": None, "output_contract_locator": None}
    degradations = []
    task_text = None
    if inventory["case_file"]:
        data, err = load_json(inventory["case_file"])
        if err is not None:
            degradations.append({"file": "case.json", "reason": err})
            data = None
        if inventory["task_file"]:
            task_text, terr = read_text_bytes(inventory["task_file"])
            if terr is not None:
                degradations.append({"file": "task.md", "reason": terr})
        contract = extract_contract(data, task_text)
    elif inventory["task_file"]:
        task_text, terr = read_text_bytes(inventory["task_file"])
        if terr is not None:
            degradations.append({"file": "task.md", "reason": terr})
        contract = extract_contract(None, task_text)
    else:
        degradations.append({"file": "case.json/task.md", "reason": "both_missing"})

    records, rec_deg = load_records(inventory["bib_files"])
    degradations.extend(rec_deg)

    # 判空/缺契约：诚实降级，仍落骨架
    empty_input = not inventory["exists"] or not inventory["files"]
    missing_contract = contract["output_contract"] is None

    result = build_skeleton(contract, inventory, degradations)
    result["deduplication"]["method"] = \
        "exact merge by normalized DOI or normalized title; " \
        "near-title similarity>={:.2f} flagged possible_duplicate (not auto-merged)".format(NEAR_TITLE_THRESHOLD)
    result["deduplication"]["summary"]["retrieved"] = len(records)

    # 输入来源登记（sources：locator + 类型 + 大小）
    for f in inventory["files"]:
        result["sources"].append({
            "source_id": "src-{:03d}".format(len(result["sources"]) + 1),
            "rel": f["rel"],
            "type": os.path.splitext(f["name"])[1].lstrip(".").lower() or "unknown",
            "size_bytes": f["size"],
            "locator": f["rel"],
        })

    if empty_input:
        result["status"] = "insufficient_evidence"
        result["uncertainty"]["summary"] = "输入目录为空或不可读：无题录、无契约，无法筛选"
        result["limitations"].append({"scope": "global", "type": "empty_input",
                                      "detail": "输入目录为空或不可读", "impact": "无法进行任何筛选"})
        result["change_log"].append({"time": now_iso(), "step": "skeleton",
                                     "action": "empty_input -> insufficient_evidence", "status": "insufficient_evidence"})
    elif missing_contract and not records:
        result["status"] = "insufficient_evidence"
        result["uncertainty"]["summary"] = "未提取到 output_contract 且无题录，无法展开筛选"
        result["limitations"].append({"scope": "global", "type": "missing_contract",
                                      "detail": "case.json 未含 output_contract 且无题录", "impact": "输出字段未知"})
        result["change_log"].append({"time": now_iso(), "step": "skeleton",
                                     "action": "missing_contract -> insufficient_evidence", "status": "insufficient_evidence"})

    if missing_contract and not empty_input and records:
        result["limitations"].append({"scope": "global", "type": "missing_contract",
                                      "detail": "case.json 未含显式 output_contract；按默认 schema 回填",
                                      "impact": "契约字段可能不完整，需按 task.md 补齐"})

    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    write_json(os.path.join(args.output, "result.json"), result)
    write_json(os.path.join(args.work, "inventory.json"), inventory)
    write_json(os.path.join(args.work, "records.json"), records)
    write_json(os.path.join(args.work, "protocol.json"), contract)
    write_json(os.path.join(args.work, "degradations.json"), degradations)
    print("[skeleton] status={} records={} sources={} degradations={}".format(
        result["status"], len(records), len(result["sources"]), len(degradations)))
    return 0


def _cmd_normalize(args):
    records, err = load_json(os.path.join(args.work, "records.json"))
    if records is None:
        print("[normalize] records.json 不可读: {}".format(err))
        return 1
    dedup, rep_records = build_dedup(records)
    os.makedirs(args.work, exist_ok=True)
    write_json(os.path.join(args.work, "dedup.json"), dedup)
    write_json(os.path.join(args.work, "unique_records.json"), rep_records)
    # 判定模板：唯一记录 + 空判定占位（双人 + 仲裁字段），供 Agent 填写后存为 judgments.json
    template = {"criteria_version": None, "judgments": []}
    for r in rep_records:
        template["judgments"].append({
            "record_id": r["record_id"],
            "decision": "uncertain",
            "reason_code": "unc_pending_review",
            "evidence_quote": None,
            "evidence_locator": None,
            "reviewer_a": {"decision": None, "reason_code": None, "note": None},
            "reviewer_b": {"decision": None, "reason_code": None, "note": None},
            "conflict": {"is_conflict": False, "type": None, "detail": None},
            "arbitration": None,
            "needs_human": True,
        })
    write_json(os.path.join(args.work, "judgments_template.json"), template)
    print("[normalize] retrieved={} merged={} unique={} possible_duplicate={}".format(
        dedup["summary"]["retrieved"], dedup["summary"]["merged"],
        dedup["summary"]["unique"], len(dedup["possible_duplicates"])))
    return 0


def _cmd_finalize(args):
    records, err = load_json(os.path.join(args.work, "records.json"))
    if records is None:
        print("[finalize] records.json 不可读: {}".format(err))
        return 1
    dedup, rep_records = build_dedup(records)
    judg_data, jerr = load_json(os.path.join(args.work, "judgments.json"))
    if judg_data is None:
        print("[finalize] judgments.json 缺失或不可读: {}；全部标 uncertain/pending_human".format(jerr))
        judg_data = {"judgments": []}
    judgments = judg_data.get("judgments", []) if isinstance(judg_data, dict) else []
    criteria_version = (judg_data.get("criteria_version") if isinstance(judg_data, dict) else None)

    out = finalize_records(records, dedup, rep_records, judgments, criteria_version)
    screening = out["screening"]
    conflicts = out["conflicts"]
    audit = out["audit"]
    errors = out["errors"]
    degradations = out["degradations"]
    needs_human = out["needs_human"]

    # 读入骨架，回填契约字段与汇总，不改名不删键
    result, rerr = load_json(os.path.join(args.output, "result.json"))
    if result is None or not isinstance(result, dict):
        result = build_skeleton({"case_id": None, "output_contract": None}, {"files": []}, [])
    contract, perr = load_json(os.path.join(args.work, "protocol.json"))
    if contract is None or not isinstance(contract, dict):
        contract = {}

    result["screening"] = screening
    result["deduplication"] = dedup
    result["conflicts"] = conflicts
    result["audit"] = audit
    result["errors"] = list(errors)
    result["degradations"] = result.get("degradations", []) + degradations
    result["needs_human"] = list(needs_human)
    if "pending_human" in result:
        result["pending_human"] = list(result.get("pending_human", [])) + list(needs_human)
    result["criteria_version"] = criteria_version or contract.get("criteria_version")
    result["research_question"] = result.get("research_question") or contract.get("research_question")
    result["inclusion_criteria"] = result.get("inclusion_criteria") or contract.get("inclusion_criteria") or []
    result["exclusion_criteria"] = result.get("exclusion_criteria") or contract.get("exclusion_criteria") or []

    # 显式 limitations + uncertainty（本版重点：缺数据/不确定不编造，逐条落到结构字段）
    for r in screening["records"]:
        if not r.get("doi"):
            result["limitations"].append({"scope": r["record_id"], "type": "doi_missing",
                                          "detail": "DOI 缺失", "impact": "去重与溯源仅依赖规范化标题"})
        if not r.get("title"):
            result["limitations"].append({"scope": r["record_id"], "type": "title_missing",
                                          "detail": "标题缺失", "impact": "无法做标题级纳排"})
        if r.get("decision") == "uncertain":
            result["uncertainty"]["items"].append({
                "scope": r["record_id"], "type": r.get("reason_code") or "uncertain",
                "detail": r.get("reason_code") or "uncertain",
                "disposition": "pending_human" if r.get("needs_human") else "accepted_risk",
            })
    # 去重可能的重复：显式不确定性
    for p in dedup.get("possible_duplicates", []):
        result["uncertainty"]["items"].append({
            "scope": "{}|{}".format(p["record_id_a"], p["record_id_b"]),
            "type": "possible_duplicate", "detail": "标题相似 {:.4f}，未自动合并".format(p["title_similarity"]),
            "disposition": "pending_human",
        })

    result["uncertainty"]["level"] = uncertainty_level(
        screening["records"], result["limitations"], needs_human, errors)
    result["uncertainty"]["summary"] = "included={} excluded={} uncertain={} needs_human={} limitations={}".format(
        screening["summary"]["included"], screening["summary"]["excluded"],
        screening["summary"]["uncertain"], len(needs_human), len(result["limitations"]))

    status = "ok"
    if errors or needs_human or screening["summary"]["uncertain"]:
        status = "partial"
    if not screening["records"] and dedup["summary"]["retrieved"] == 0:
        status = "insufficient_evidence"
    result["status"] = status
    result["change_log"].append({"time": now_iso(), "step": "finalize",
                                 "action": "合并判定并校验证据/计数闭合", "status": status})

    os.makedirs(args.output, exist_ok=True)
    write_json(os.path.join(args.output, "result.json"), result)
    _write_screening_csv(screening["records"], os.path.join(args.output, "screening_table.csv"))
    write_json(os.path.join(args.output, "audit_log.json"),
               {"case_id": result.get("case_id"), "audit": audit,
                "conflicts": conflicts, "errors": errors, "degradations": result["degradations"],
                "generated_at": now_iso()})
    _write_protocol_md(contract, dedup, screening, os.path.join(args.output, "protocol.md"))
    print("[finalize] status={} unique={} included={} excluded={} uncertain={} count_closed={}".format(
        status, screening["summary"]["unique"], screening["summary"]["included"],
        screening["summary"]["excluded"], screening["summary"]["uncertain"], audit["count_closed"]))
    return 0


def _cmd_selfcheck(args):
    checks = []
    result, rerr = load_json(os.path.join(args.output, "result.json"))
    if result is None or not isinstance(result, dict):
        print("[selfcheck] FAIL result.json 缺失或不可读: {}".format(rerr))
        return 1
    checks.append(("result.json 可读且为 dict", isinstance(result, dict)))
    checks.append(("case_id 非空", bool(result.get("case_id"))))
    checks.append(("status 合法", result.get("status") in STATUS_CHOICES))

    contract, _ = load_json(os.path.join(args.work, "protocol.json"))
    oc = (contract or {}).get("output_contract")
    contract_keys = []
    if isinstance(oc, dict):
        contract_keys = list(oc.keys())
    elif isinstance(oc, list):
        contract_keys = [k for k in oc if isinstance(k, str)]
    checks.append(("output_contract 全部字段已回填", all(k in result for k in contract_keys)))
    if contract_keys:
        checks.append(("output_contract 字段非删改（keys 一致）", set(contract_keys) <= set(result.keys())))

    screening = result.get("screening") or {}
    records = screening.get("records") or []
    checks.append(("每条记录含 decision", all(r.get("decision") for r in records)))
    checks.append(("每条记录含 reason_code", all(r.get("reason_code") for r in records)))
    checks.append(("非 uncertain 记录含 evidence_quote",
                   all(r.get("evidence_quote") or r.get("decision") == "uncertain" for r in records)))
    checks.append(("evidence_quote 均 verified",
                   all(r.get("evidence_verified") or r.get("decision") == "uncertain" for r in records)))

    dedup = result.get("deduplication") or {}
    ds = dedup.get("summary") or {}
    audit = result.get("audit") or {}
    checks.append(("计数闭合", bool(audit.get("count_closed"))))
    checks.append(("检索数-合并数==唯一数",
                   (ds.get("retrieved", 0) - ds.get("merged", 0)) == ds.get("unique", -1)))
    ss = screening.get("summary") or {}
    checks.append(("纳入+排除+待定==唯一",
                   (ss.get("included", 0) + ss.get("excluded", 0) + ss.get("uncertain", 0)) == ss.get("unique", -1)))

    unresolved = [c for c in result.get("conflicts", []) if not c.get("resolved")]
    checks.append(("冲突均有仲裁或待人工", all(c.get("resolved") or c.get("needs_human") for c in result.get("conflicts", []))))

    # 确定性：用 records.json 重新去重，与 result 的 dedup summary 对比
    recs, _ = load_json(os.path.join(args.work, "records.json"))
    if recs is not None:
        dedup2, _ = build_dedup(recs)
        checks.append(("去重结果确定（重算一致）", dedup2["summary"] == ds))

    allpass = True
    for name, ok in checks:
        print("[selfcheck] {} {}".format("PASS" if ok else "FAIL", name))
        allpass = allpass and ok
    print("[selfcheck] {} / {} passed".format(sum(1 for _, ok in checks if ok), len(checks)))
    return 0 if allpass else 1


# --------------------------------------------------------------------------
# 参数解析与分发
# --------------------------------------------------------------------------

def _build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="系统性文献检索与纳排筛选 — 确定性账本后端（仅标准库）")
    p.add_argument("command", nargs="?", default="help",
                   choices=["skeleton", "normalize", "finalize", "selfcheck", "help"])
    p.add_argument("--input", default=DEFAULT_INPUT, help="输入目录（默认 /input）")
    p.add_argument("--output", default=DEFAULT_OUTPUT, help="产物目录（默认 /app/output）")
    p.add_argument("--work", default=DEFAULT_WORK, help="工作目录（默认 /app/work）")
    return p


def main(argv=None):
    args = _build_parser().parse_args(argv)
    if args.command == "skeleton":
        return _cmd_skeleton(args)
    if args.command == "normalize":
        return _cmd_normalize(args)
    if args.command == "finalize":
        return _cmd_finalize(args)
    if args.command == "selfcheck":
        return _cmd_selfcheck(args)
    _build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
