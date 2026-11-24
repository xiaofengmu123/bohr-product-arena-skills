#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — search-coverage-timeboxed 实现（仅标准库）；入口见 main.py。

子命令（按时间预算逐阶段推进，阶段间用 /app/work/state.json 持久化墙钟预算）：
  scaffold  0–3 min   落盘最小合法 result.json 骨架 + 预算计划，保证任何中断不残留半截 JSON
  inspect   3–6 min   盘点 /input 文件，产出 /app/work/inspect.json（文件清单、记录数、解析错误）
  run       6–10 min  冻结参考集、逐条命中判定，产出 reference_set.json / coverage_core.json / hit_matrix.csv
  merge     13–15 min 合并人工裁决、漏检归因、补充检索更新，产出最终 result.json + coverage_report.md + evidence_map.json

命中规则：标识符精确匹配(doi/pmid) → 标题+第一作者+年份模糊匹配(title>=0.88, year_tolerance=1)
         → 标题近似候选(>=0.70, 转人工裁决) → 未命中。
确定性：同输入重复运行，coverage_rate 与命中状态一致（无随机性；仅时间戳字段不同）。
"""

import argparse
import csv
import difflib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from fnmatch import fnmatch

# ---------------------------------------------------------------------------
# 常量与阈值（与 references/coverage-method.md 保持一致）
# ---------------------------------------------------------------------------
DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2 * 1024 * 1024
TITLE_FUZZY = 0.88
TITLE_ADJUDICATION = 0.70
YEAR_TOLERANCE = 1
BUDGET_SECONDS = 900
BUDGET_REQUESTS = 64
MAX_TOKENS = 16384

HIT_RULE_TEXT = (
    "identifier_exact(doi/pmid) → title_author_year_fuzzy(title_sim>=0.88, "
    "year_tolerance=1) → title_fuzzy_candidate(title_sim>=0.70, 转人工裁决) → miss"
)

ALLOWED_OMISSION_CATEGORIES = [
    "query_terms",        # 查询词未覆盖同义表达
    "field_restriction",  # 检索字段限制过窄
    "time_window",        # 时间窗截断
    "source_coverage",    # 来源库未收录
    "identifier_anomaly", # 记录标识异常（DOI/PMID 缺失或冲突）
    "metadata_missing",   # 记录缺少标题/作者/年份，无法匹配
    "unattributable",     # 材料不足以归因
]

RESERVED_KEYS = {
    "case_id", "status", "research_question", "search_scope", "reference_set",
    "search_sessions", "coverage", "hit_matrix", "omissions",
    "supplementary_searches", "marginal_gain", "residual_omissions",
    "evidence_map", "sources", "uncertainty", "change_log", "limitations",
    "pending_human", "errors", "degradations", "runtime", "output_contract",
    "output_contract_keys",
}

TITLE_KEYS = ["title", "Title", "name", "Name", "article_title", "publication_title", "headline"]
DOI_KEYS = ["doi", "DOI"]
PMID_KEYS = ["pmid", "PMID", "pubmed_id", "pubmedId"]
AUTHOR_KEYS = ["authors", "author", "Author", "creator", "creators", "byline", "first_author"]
YEAR_KEYS = ["year", "Year", "published_year", "pub_year", "date", "publication_date", "issued"]
ID_KEYS = ["id", "ID", "record_id", "uid", "ref_id", "pmid", "PMID"]
SOURCE_KEYS = ["source", "Source", "database", "Database", "db", "origin", "provider"]
URL_KEYS = ["url", "URL", "link", "href"]
FLAG_HINTS = ["retracted", "retraction", "unavailable", "no full text", "full_text_unavailable"]


# ---------------------------------------------------------------------------
# 基础工具：文本/JSON/原子写盘
# ---------------------------------------------------------------------------
def read_text_file(path):
    """utf-8-sig 优先、多编码降级读取；超 2MB 返回错误。返回 (text|None, err|None)。"""
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_FILE_BYTES + 1)
    except OSError as e:
        return None, "read error: {0}".format(e)
    if len(data) > MAX_FILE_BYTES:
        return None, "exceeds 2MB, skipped"
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc), None
        except (UnicodeDecodeError, ValueError):
            continue
    return data.decode("utf-8", errors="replace"), None


def atomic_write_text(path, text, bom=False):
    """先写 .tmp 再 os.replace，保证任何中断都不残留半截文件。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    mode = "w"
    kw = {"newline": ""}
    if bom:
        kw["encoding"] = "utf-8-sig"
    else:
        kw["encoding"] = "utf-8"
    with open(tmp, mode, **kw) as f:
        f.write(text)
    os.replace(tmp, path)


def atomic_write_json(path, obj):
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))


def load_json(path):
    text, err = read_text_file(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except ValueError as e:
        return None, "invalid JSON: {0}".format(e)


def list_input_files(input_dir):
    out = []
    if not os.path.isdir(input_dir):
        return out
    for root, _dirs, files in os.walk(input_dir):
        for fn in sorted(files):
            out.append(os.path.join(root, fn))
    return out


# ---------------------------------------------------------------------------
# 归一化与字段提取
# ---------------------------------------------------------------------------
def normalize_text(s):
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s)).lower()
    s = re.sub(r"[^\w\u4e00-\u9fff]+", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def normalize_identifier(s):
    if s is None:
        return ""
    return re.sub(r"\s+", "", str(s).strip().lower())


def canonical_doi(s):
    if s is None:
        return ""
    s = str(s).strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.IGNORECASE)
    return s.lower()


def extract_year(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    m = re.search(r"(1[89]\d{2}|20\d{2})", str(v))
    return int(m.group(1)) if m else None


def first_author_surname(authors):
    if authors is None:
        return None
    if isinstance(authors, (list, tuple)):
        first = authors[0] if authors else None
    else:
        first = authors
    if first is None:
        return None
    if isinstance(first, dict):
        s = first.get("family") or first.get("surname") or first.get("last") or first.get("name") or first.get("given")
    else:
        s = first
    s = str(s).strip()
    if not s:
        return None
    if "," in s:
        s = s.split(",")[0]
    elif ";" in s:
        s = s.split(";")[0]
    else:
        s = s.split()[-1] if s.split() else s
    return normalize_text(s)


def pick(rec, keys):
    """按别名取字段；键缺失时向下探测一级常见包裹层。"""
    if not isinstance(rec, dict):
        return None
    for k in keys:
        v = rec.get(k)
        if v not in (None, "", [], {}):
            return v
    for subk in ("metadata", "record", "article", "item", "attributes", "message", "body"):
        sub = rec.get(subk)
        if isinstance(sub, dict):
            for k in keys:
                v = sub.get(k)
                if v not in (None, "", [], {}):
                    return v
    return None


def record_fields(rec):
    if not isinstance(rec, dict):
        return {}
    return {
        "title": pick(rec, TITLE_KEYS),
        "authors": pick(rec, AUTHOR_KEYS),
        "year": pick(rec, YEAR_KEYS),
        "doi": pick(rec, DOI_KEYS),
        "pmid": pick(rec, PMID_KEYS),
        "rid": pick(rec, ID_KEYS),
        "source": pick(rec, SOURCE_KEYS),
        "url": pick(rec, URL_KEYS),
    }


def normalize_authors(a):
    if a is None:
        return None
    if isinstance(a, list):
        parts = []
        for x in a:
            if isinstance(x, dict):
                parts.append(x.get("family") or x.get("surname") or x.get("name") or str(x))
            else:
                parts.append(str(x))
        return "; ".join(parts)
    return str(a)


def detect_record_flags(rec):
    if not isinstance(rec, dict):
        return ["not_a_record"]
    flags = []
    txt = json.dumps(rec, default=str).lower()
    if "retract" in txt:
        flags.append("retracted_or_unavailable")
    if any(h in txt for h in ("unavailable", "no full text", "full_text_unavailable")):
        flags.append("full_text_unavailable")
    f = record_fields(rec)
    if not f.get("doi") and not f.get("pmid"):
        flags.append("no_identifier")
    if not f.get("title"):
        flags.append("no_title")
    return flags or ["normal"]


def rec_source(rec):
    if isinstance(rec, dict):
        return rec.get("_source_locator")
    return None


# ---------------------------------------------------------------------------
# 记录提取与去重
# ---------------------------------------------------------------------------
def extract_records(obj):
    """从任意 JSON 结构递归提取文献记录（dict 且含 title/doi/pmid）。"""
    records = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if _looks_like_record(node):
                records.append(node)
            else:
                for v in node.values():
                    walk(v)

    walk(obj)
    return records


def _looks_like_record(d):
    f = record_fields(d)
    return bool(f.get("title") or f.get("doi") or f.get("pmid"))


def dedup_key(rec):
    f = record_fields(rec)
    doi = canonical_doi(f.get("doi") or "")
    if doi:
        return "doi:" + doi
    pmid = normalize_identifier(f.get("pmid") or "")
    if pmid:
        return "pmid:" + pmid
    t = normalize_text(f.get("title") or "")
    if t:
        a = first_author_surname(f.get("authors")) or ""
        y = extract_year(f.get("year"))
        return "tay:{0}|{1}|{2}".format(t, a, y if y is not None else "")
    return "hash:" + hashlib.sha1(json.dumps(rec, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def load_records_from_files(files, errors):
    records = []
    for p in files:
        obj, err = load_structured(p)
        if err:
            errors.append({"file": p, "detail": err})
            continue
        recs = extract_records(obj)
        for idx, rec in enumerate(recs):
            rec = dict(rec)
            rec["_source_locator"] = "{0}#record:{1}".format(p, idx + 1)
            records.append(rec)
    return records


def load_structured(path):
    text, err = read_text_file(path)
    if err:
        return None, err
    low = path.lower()
    if low.endswith(".json"):
        try:
            return json.loads(text), None
        except ValueError as e:
            return None, "invalid JSON: {0}".format(e)
    if low.endswith(".csv"):
        try:
            return parse_csv_text(text), None
        except Exception as e:
            return None, "csv error: {0}".format(e)
    try:
        return json.loads(text), None
    except ValueError:
        return {"_text": text}, None


def parse_csv_text(text):
    return [dict(r) for r in csv.DictReader(io.StringIO(text))]


def extract_queries(obj):
    queries = []

    def walk(n):
        if isinstance(n, dict):
            for k, v in n.items():
                if k.lower() in ("query", "search_term", "search_terms", "q", "query_string", "querystring"):
                    queries.append({"field": k, "value": v})
                walk(v)
        elif isinstance(n, list):
            for x in n:
                walk(x)

    walk(obj)
    return queries


def extract_pdf_text(path):
    for cmd in (["pdftotext", "-layout", path, "-"], ["pdftotext", path, "-"]):
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=60)
            if r.returncode == 0 and r.stdout:
                return r.stdout.decode("utf-8", errors="replace"), None
        except Exception:
            continue
    return None, "pdftotext unavailable or failed"


def classify(obj, recs):
    if not isinstance(obj, dict):
        return "list"
    key_text = " ".join(str(k) for k in obj.keys()).lower()
    if any(t in key_text for t in ("reference", "gold", "ground_truth", "expected", "known", "seed", "golden")):
        return "reference_set"
    if any(t in key_text for t in ("query", "search", "result", "hit", "response", "session")):
        return "search_results"
    if recs:
        return "records"
    return "other"


# ---------------------------------------------------------------------------
# 命中判定
# ---------------------------------------------------------------------------
def match_record(ref, res):
    r = record_fields(ref)
    s = record_fields(res)
    rdoi = canonical_doi(r.get("doi") or "")
    sdoi = canonical_doi(s.get("doi") or "")
    if rdoi and sdoi and rdoi == sdoi:
        return "hit", "identifier", 1.0
    rp = normalize_identifier(r.get("pmid") or "")
    sp = normalize_identifier(s.get("pmid") or "")
    if rp and sp and rp == sp:
        return "hit", "identifier", 1.0

    rt = normalize_text(r.get("title") or "")
    st = normalize_text(s.get("title") or "")
    sim = difflib.SequenceMatcher(None, rt, st).ratio() if (rt and st) else 0.0

    ra = first_author_surname(r.get("authors"))
    sa = first_author_surname(s.get("authors"))
    if ra and sa:
        author_state = "match" if ra == sa else "mismatch"
    elif not ra and not sa:
        author_state = "absent"
    else:
        author_state = "unknown"

    ry = extract_year(r.get("year"))
    sy = extract_year(s.get("year"))
    year_ok = True
    if ry is not None and sy is not None:
        year_ok = abs(ry - sy) <= YEAR_TOLERANCE

    if sim >= TITLE_FUZZY and author_state in ("match", "unknown", "absent") and year_ok:
        return "hit", "title_author_year", sim
    if sim >= TITLE_ADJUDICATION:
        return "adjudicate", "title_fuzzy_candidate", sim
    return "miss", "no_match", sim


def matrix_row(ref, status, method, sim, res):
    f = record_fields(res) if isinstance(res, dict) else {}
    return {
        "ref_id": ref.get("ref_id"),
        "status": status,
        "matched_by": method,
        "title_similarity": round(sim, 4) if sim is not None else None,
        "ref_title": ref.get("title"),
        "res_title": f.get("title") if res else None,
        "res_doi": canonical_doi(f.get("doi") or "") or None if res else None,
        "res_locator": rec_source(res) if res else None,
        "match_evidence": method if res else None,
    }


def build_omission(ref, reason, candidate):
    status = "miss" if reason == "no_candidate" else "adjudicate_pending"
    return {
        "ref_id": ref.get("ref_id"),
        "title": ref.get("title"),
        "doi": ref.get("doi"),
        "status": status,
        "reason": reason,
        "category": None,
        "signals": [],
        "evidence": None,
        "attribution_pending": True,
        "best_candidate_locator": rec_source(candidate) if candidate else None,
    }


def match_all(ref_records, res_records):
    hit_matrix = []
    omissions = []
    for ref in ref_records:
        hit = None
        adj = None
        for res in res_records:
            status, method, sim = match_record(ref, res)
            if status == "hit":
                hit = (res, method, sim)
                break
            if status == "adjudicate" and (adj is None or sim > adj[2]):
                adj = (res, method, sim)
        if hit is not None:
            res, method, sim = hit
            hit_matrix.append(matrix_row(ref, "hit", method, sim, res))
        elif adj is not None:
            res, method, sim = adj
            hit_matrix.append(matrix_row(ref, "adjudicate", method, sim, res))
            omissions.append(build_omission(ref, "adjudicate_pending", res))
        else:
            hit_matrix.append(matrix_row(ref, "miss", None, None, None))
            omissions.append(build_omission(ref, "no_candidate", None))
    return hit_matrix, omissions


# ---------------------------------------------------------------------------
# 参考集冻结
# ---------------------------------------------------------------------------
def freeze_reference_set(records, source_files):
    seen = {}
    canonicals = []
    duplicates = []
    for rec in records:
        key = dedup_key(rec)
        if key in seen:
            duplicates.append({"dedup_key": key, "duplicate_of": seen[key], "source_locator": rec_source(rec)})
            continue
        seen[key] = "R{0:03d}".format(len(canonicals) + 1)
        canonicals.append(rec)

    out_records = []
    for i, rec in enumerate(canonicals):
        rid = "R{0:03d}".format(i + 1)
        f = record_fields(rec)
        out_records.append({
            "ref_id": rid,
            "title": f.get("title"),
            "authors": normalize_authors(f.get("authors")),
            "year": extract_year(f.get("year")),
            "doi": canonical_doi(f.get("doi") or "") or None,
            "pmid": normalize_identifier(f.get("pmid") or "") or None,
            "source_locator": rec_source(rec),
            "url": f.get("url"),
            "record_flags": detect_record_flags(rec),
            "inclusion_reason": rec.get("inclusion_reason") if isinstance(rec, dict) else None,
        })

    payload = json.dumps([r for r in out_records], ensure_ascii=False, sort_keys=True, default=str)
    version = "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return {
        "version": version,
        "frozen_at_unix": time.time(),
        "source_files": source_files,
        "total_raw_records": len(records),
        "unique_count": len(canonicals),
        "duplicate_count": len(duplicates),
        "duplicates": duplicates,
        "records": out_records,
    }


# ---------------------------------------------------------------------------
# 状态与预算（跨命令持久化）
# ---------------------------------------------------------------------------
def new_state():
    now = time.time()
    return {
        "started_at": now,
        "deadline": now + BUDGET_SECONDS,
        "budget_seconds": BUDGET_SECONDS,
        "budget_requests": BUDGET_REQUESTS,
        "stages": {"scaffold": None, "inspect": None, "run": None, "merge": None},
    }


def load_state(work):
    obj, err = load_json(os.path.join(work, "state.json"))
    if err or not isinstance(obj, dict):
        return None
    return obj


def save_state(work, state):
    atomic_write_json(os.path.join(work, "state.json"), state)


def mark_stage(state, stage):
    now = time.time()
    start = state.get("started_at", now)
    state.setdefault("stages", {})[stage] = round(now - start, 2)


def runtime_block(state):
    now = time.time()
    start = state.get("started_at", now)
    elapsed = now - start
    remaining = max(0.0, state.get("deadline", now + BUDGET_SECONDS) - now)
    return {
        "budget_seconds": state.get("budget_seconds", BUDGET_SECONDS),
        "budget_requests": state.get("budget_requests", BUDGET_REQUESTS),
        "max_tokens_per_response": MAX_TOKENS,
        "started_at_unix": start,
        "elapsed_seconds": round(elapsed, 2),
        "budget_remaining_seconds": round(remaining, 2),
        "stage_elapsed_seconds": dict(state.get("stages", {})),
        "time_budget_exceeded": elapsed > BUDGET_SECONDS,
    }


def budget_plan():
    return {
        "total_seconds": BUDGET_SECONDS,
        "total_requests": BUDGET_REQUESTS,
        "max_tokens_per_response": MAX_TOKENS,
        "early_scaffold_rule": "前 3 分钟必须先落盘一版 partial result.json 骨架，之后只做增量改进",
        "stages": [
            {"stage": "scaffold", "window": "0-3 min", "budget_seconds": 180, "requests": 2},
            {"stage": "inspect", "window": "3-6 min", "budget_seconds": 180, "requests": 6},
            {"stage": "run", "window": "6-10 min", "budget_seconds": 240, "requests": 10},
            {"stage": "adjudicate", "window": "10-13 min", "budget_seconds": 180, "requests": 30},
            {"stage": "merge", "window": "13-15 min", "budget_seconds": 120, "requests": 10},
        ],
    }


def log_entry(stage, detail):
    return {"stage": stage, "detail": detail, "unix_time": time.time()}


# ---------------------------------------------------------------------------
# case 元数据与 result 骨架
# ---------------------------------------------------------------------------
def load_case_meta(input_dir):
    case_path = os.path.join(input_dir, "case.json")
    if not os.path.isfile(case_path):
        return None, {}, "case.json 不存在"
    obj, err = load_json(case_path)
    if err:
        return None, {}, "case.json 解析失败: {0}".format(err)
    if not isinstance(obj, dict):
        return None, {}, "case.json 不是 JSON 对象"
    case_id = obj.get("case_id") or obj.get("id") or obj.get("challenge_id")
    if not case_id:
        digest = hashlib.sha1(json.dumps(obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        case_id = "case-" + digest[:12]
    contract = obj.get("output_contract")
    if not isinstance(contract, dict):
        contract = {}
    return case_id, contract, None


def base_result(case_id, contract):
    result = {
        "case_id": case_id,
        "status": "partial",
        "research_question": None,
        "search_scope": None,
        "reference_set": {"version": None, "source_files": [], "records": []},
        "search_sessions": [],
        "coverage": {
            "denominator": 0, "numerator": 0, "coverage_rate": None, "omission_rate": None,
            "adjudicate_count": 0, "miss_count": 0, "hit_rule": HIT_RULE_TEXT,
            "thresholds": {"title_fuzzy": TITLE_FUZZY, "title_adjudication": TITLE_ADJUDICATION, "year_tolerance": YEAR_TOLERANCE},
        },
        "hit_matrix": [],
        "omissions": [],
        "supplementary_searches": [],
        "marginal_gain": {"pre_supplement_omissions": None, "new_unique_hits": 0, "gain_rate": None},
        "residual_omissions": [],
        "evidence_map": [],
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "limitations": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "runtime": None,
    }
    result["output_contract"] = contract
    result["output_contract_keys"] = sorted(contract.keys())
    for k in sorted(contract.keys()):
        if k not in RESERVED_KEYS:
            result[k] = contract.get(k)
    return result


def load_or_base(output, case_id, contract):
    p = os.path.join(output, "result.json")
    obj, err = load_json(p)
    if err or not isinstance(obj, dict):
        return base_result(case_id, contract)
    return obj


def write_result(output, result):
    atomic_write_json(os.path.join(output, "result.json"), result)


def sources_from_files(ref_files, res_files):
    sources = []
    for p in ref_files:
        sources.append({"path": p, "role": "reference_set", "record_type": "frozen known-relevant records"})
    for p in res_files:
        sources.append({"path": p, "role": "search_results", "record_type": "retrieved/query response records"})
    return sources


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------
def cmd_scaffold(args):
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    case_id, contract, case_err = load_case_meta(args.input)
    state = load_state(args.work) or new_state()

    result = base_result(case_id, contract)
    result["change_log"].append(log_entry("scaffold", "落盘 partial 结果骨架（原子写盘，任何中断不残留半截 JSON）"))
    if case_err:
        result["limitations"].append("case 元数据不可得：{0}".format(case_err))

    mark_stage(state, "scaffold")
    save_state(args.work, state)
    atomic_write_json(os.path.join(args.work, "budget_plan.json"), budget_plan())
    result["runtime"] = runtime_block(state)
    write_result(args.output, result)
    print("scaffold: result.json 骨架已落盘 (case_id={0}, status=partial)".format(case_id))
    return 0


def cmd_inspect(args):
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    state = load_state(args.work) or new_state()
    files = list_input_files(args.input)
    rows = []
    errors = []
    for p in files:
        rel = os.path.relpath(p, args.input)
        size = os.path.getsize(p)
        row = {"path": p, "rel": rel, "size_bytes": size, "type": _guess_type(p), "status": "ok", "parse": None}
        if size > MAX_FILE_BYTES:
            row["status"] = "skipped"
            row["parse"] = "exceeds 2MB"
            errors.append({"file": rel, "detail": "exceeds 2MB, skipped"})
            rows.append(row)
            continue
        text, err = read_text_file(p)
        if err:
            row["status"] = "error"
            row["parse"] = err
            errors.append({"file": rel, "detail": err})
            rows.append(row)
            continue
        obj = None
        low = p.lower()
        if low.endswith(".json"):
            try:
                obj = json.loads(text)
                row["parse"] = "json"
            except ValueError as e:
                row["status"] = "degraded"
                row["parse"] = "invalid JSON: {0}".format(e)
                errors.append({"file": rel, "detail": "invalid JSON, degraded"})
        elif low.endswith(".csv"):
            row["parse"] = "csv"
            try:
                obj = parse_csv_text(text)
            except Exception as e:
                row["status"] = "degraded"
                row["parse"] = "csv error: {0}".format(e)
        elif low.endswith(".pdf"):
            row["parse"] = "pdf"
            ptxt, perr = extract_pdf_text(p)
            if perr:
                row["status"] = "degraded"
                row["parse"] = perr
                errors.append({"file": rel, "detail": perr})
            else:
                row["pdf_text_chars"] = len(ptxt)
        else:
            row["parse"] = "text"
        if obj is not None:
            recs = extract_records(obj)
            row["record_count"] = len(recs)
            row["looks_like"] = classify(obj, recs)
            row["sample_keys"] = sorted(recs[0].keys())[:8] if recs else []
        rows.append(row)

    refs = [r["path"] for r in rows if r.get("looks_like") == "reference_set"]
    results = [r["path"] for r in rows if r.get("looks_like") in ("search_results", "records")]
    case_id, contract, _case_err = load_case_meta(args.input)
    inspect = {
        "input_dir": args.input,
        "files": rows,
        "errors": errors,
        "auto_refs": refs,
        "auto_results": results,
        "case_id": case_id,
        "has_output_contract": bool(contract),
    }
    atomic_write_json(os.path.join(args.work, "inspect.json"), inspect)

    # 更新 result.json 的 sources / degradations，不改动状态
    result = load_or_base(args.output, case_id, contract)
    result["degradations"].extend([{"stage": "inspect", "detail": e["detail"], "file": e.get("file")} for e in errors])
    result["sources"] = [{"path": r["path"], "role": "input", "status": r["status"]} for r in rows]
    mark_stage(state, "inspect")
    save_state(args.work, state)
    result["runtime"] = runtime_block(state)
    result["change_log"].append(log_entry("inspect", "盘点输入文件：{0} 个文件，{1} 条错误".format(len(rows), len(errors))))
    write_result(args.output, result)
    print("inspect: {0} files, {1} errors; auto_refs={2}, auto_results={3}".format(
        len(rows), len(errors), len(refs), len(results)))
    return 0


def _guess_type(path):
    low = path.lower()
    if low.endswith(".json"):
        return "json"
    if low.endswith(".csv"):
        return "csv"
    if low.endswith(".pdf"):
        return "pdf"
    if low.endswith((".md", ".txt")):
        return "text"
    return "other"


def _match_globs(path, rel, globs):
    if not globs:
        return False
    for g in globs:
        if fnmatch(rel, g) or fnmatch(os.path.basename(path), g) or fnmatch(path, g):
            return True
    return False


def resolve_files(input_dir, globs, inspect, kind):
    if globs:
        chosen = []
        for p in list_input_files(input_dir):
            rel = os.path.relpath(p, input_dir)
            if _match_globs(p, rel, globs):
                chosen.append(p)
        return chosen
    auto = inspect.get("auto_refs" if kind == "refs" else "auto_results", []) if inspect else []
    if auto:
        return auto
    # 兜底：全部可解析文件
    return list_input_files(input_dir)


def cmd_run(args):
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    state = load_state(args.work) or new_state()
    inspect, _ = load_json(os.path.join(args.work, "inspect.json"))
    case_id, contract, case_err = load_case_meta(args.input)
    errors = []

    ref_files = resolve_files(args.input, args.refs, inspect, "refs")
    res_files = resolve_files(args.input, args.results, inspect, "results")

    result = load_or_base(args.output, case_id, contract)
    if case_err:
        result["errors"].append({"stage": "run", "detail": case_err})

    if not ref_files:
        result["status"] = "insufficient_evidence"
        result["limitations"].append("未识别到参考集：请用 --refs 指定参考集文件（独立于检索结果）")
        result["change_log"].append(log_entry("run", "无参考集，标 insufficient_evidence"))
        mark_stage(state, "run")
        save_state(args.work, state)
        result["runtime"] = runtime_block(state)
        write_result(args.output, result)
        print("run: 未识别参考集 -> insufficient_evidence")
        return 0

    ref_records = load_records_from_files(ref_files, errors)
    res_records = load_records_from_files(res_files, errors)

    ref_set = freeze_reference_set(ref_records, ref_files)
    hit_matrix, omissions = match_all(ref_set["records"], res_records)

    denominator = ref_set["unique_count"]
    numerator = sum(1 for r in hit_matrix if r["status"] == "hit")
    coverage = {
        "denominator": denominator,
        "numerator": numerator,
        "coverage_rate": round(numerator / denominator, 6) if denominator else None,
        "omission_rate": round(1 - numerator / denominator, 6) if denominator else None,
        "adjudicate_count": sum(1 for r in hit_matrix if r["status"] == "adjudicate"),
        "miss_count": sum(1 for r in hit_matrix if r["status"] == "miss"),
        "hit_rule": HIT_RULE_TEXT,
        "thresholds": {"title_fuzzy": TITLE_FUZZY, "title_adjudication": TITLE_ADJUDICATION, "year_tolerance": YEAR_TOLERANCE},
    }

    # search_sessions
    sessions = []
    for i, p in enumerate(res_files):
        obj, err = load_structured(p)
        queries = extract_queries(obj) if obj else []
        n = sum(1 for r in res_records if rec_source(r) and rec_source(r).split("#")[0] == p)
        sessions.append({"session_id": "S{0:02d}".format(i + 1), "source_file": p, "record_count": n, "queries": queries, "time_window": None})

    result["reference_set"] = ref_set
    result["search_sessions"] = sessions
    result["coverage"] = coverage
    result["hit_matrix"] = hit_matrix
    result["omissions"] = omissions
    result["sources"] = sources_from_files(ref_files, res_files)
    result["errors"].extend([{"stage": "run", "detail": e["detail"], "file": e.get("file")} for e in errors])
    result["change_log"].append(log_entry(
        "run", "冻结参考集 {0} 条（重复 {1}），命中 {2}/{3}".format(
            denominator, ref_set["duplicate_count"], numerator, denominator)))

    # 写中间产物
    atomic_write_json(os.path.join(args.work, "reference_set.json"), ref_set)
    atomic_write_json(os.path.join(args.work, "coverage_core.json"), {"coverage": coverage, "omissions": omissions})
    write_hit_matrix_csv(os.path.join(args.output, "hit_matrix.csv"), hit_matrix)

    mark_stage(state, "run")
    save_state(args.work, state)
    result["runtime"] = runtime_block(state)
    write_result(args.output, result)
    print("run: 参考集 {0} 条, 命中 {1}, 覆盖率 {2}, 待裁决 {3}, 未命中 {4}".format(
        denominator, numerator, coverage["coverage_rate"], coverage["adjudicate_count"], coverage["miss_count"]))
    return 0


def write_hit_matrix_csv(path, hit_matrix):
    fieldnames = ["ref_id", "status", "matched_by", "title_similarity", "ref_title", "res_title", "res_doi", "res_locator", "match_evidence"]
    sio = io.StringIO()
    w = csv.DictWriter(sio, fieldnames=fieldnames, extrasaction="ignore")
    w.writeheader()
    for row in hit_matrix:
        w.writerow(row)
    atomic_write_text(path, sio.getvalue(), bom=True)


def cmd_merge(args):
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    state = load_state(args.work) or new_state()
    case_id, contract, case_err = load_case_meta(args.input)
    result = load_or_base(args.output, case_id, contract)

    updates = None
    if args.updates:
        uobj, uerr = load_json(args.updates)
        if uerr:
            result["errors"].append({"stage": "merge", "detail": "updates 文件解析失败: {0}".format(uerr)})
        elif not isinstance(uobj, dict):
            result["errors"].append({"stage": "merge", "detail": "updates 文件不是 JSON 对象"})
        else:
            updates = uobj

    if updates:
        apply_adjudications(result, updates)
        apply_attributions(result, updates, args.input)
        apply_supplementary(result, updates, args.input)

    finalize_coverage(result)
    result["status"] = final_status(result)
    if case_err:
        result["errors"].append({"stage": "merge", "detail": case_err})

    # 证据映射
    result["evidence_map"] = build_evidence_map(result)

    # 残余遗漏
    result["residual_omissions"] = [
        {"ref_id": o["ref_id"], "title": o.get("title"), "category": o.get("category"), "status": o.get("status")}
        for o in result.get("omissions", [])
        if o.get("status") == "miss" or (o.get("status") == "adjudicate_pending" and not _is_hit(result, o["ref_id"]))
    ]

    mark_stage(state, "merge")
    save_state(args.work, state)
    result["runtime"] = runtime_block(state)
    result["change_log"].append(log_entry("merge", "合并裁决/归因/补检，final status={0}".format(result["status"])))

    write_result(args.output, result)
    write_report(os.path.join(args.output, "coverage_report.md"), result)
    atomic_write_json(os.path.join(args.output, "evidence_map.json"), result["evidence_map"])
    print("merge: final status={0}, coverage_rate={1}".format(result["status"], result["coverage"].get("coverage_rate")))
    return 0


def _is_hit(result, ref_id):
    for h in result.get("hit_matrix", []):
        if h.get("ref_id") == ref_id:
            return h.get("status") in ("hit", "hit_supplement")
    return False


def apply_adjudications(result, updates):
    adjudications = updates.get("adjudications") or []
    matrix = result.get("hit_matrix", [])
    for a in adjudications:
        if not isinstance(a, dict):
            continue
        rid = a.get("ref_id")
        decision = a.get("decision")
        if decision not in ("hit", "miss"):
            result["errors"].append({"stage": "merge", "detail": "无效裁决 decision: {0}".format(a)})
            continue
        for row in matrix:
            if row.get("ref_id") == rid:
                row["status"] = decision
                row["matched_by"] = a.get("matched_by") or row.get("matched_by")
                row["match_evidence"] = a.get("reason") or row.get("match_evidence")
        for o in result.get("omissions", []):
            if o.get("ref_id") == rid:
                o["status"] = "miss" if decision == "miss" else "hit_adjudicated"
                o["attribution_pending"] = decision == "miss"


def apply_attributions(result, updates, input_dir):
    attributions = updates.get("attributions") or []
    omissions = {o["ref_id"]: o for o in result.get("omissions", [])}
    for a in attributions:
        if not isinstance(a, dict):
            continue
        rid = a.get("ref_id")
        o = omissions.get(rid)
        if o is None:
            result["errors"].append({"stage": "merge", "detail": "归因引用了不存在的 ref_id: {0}".format(rid)})
            continue
        category = a.get("category")
        if category not in ALLOWED_OMISSION_CATEGORIES:
            result["errors"].append({"stage": "merge", "detail": "无效漏检类别: {0}".format(category)})
            continue
        o["category"] = category
        o["signals"] = a.get("signals") or []
        ev = a.get("evidence") or {}
        loc = ev.get("locator")
        quote = ev.get("quote")
        ok, msg = verify_quote(loc, quote, input_dir)
        o["evidence"] = {"locator": loc, "quote": quote, "verification": msg}
        if not ok:
            result["degradations"].append({"stage": "merge", "detail": "归因证据未能逐字核验: {0}".format(msg), "ref_id": rid})
        o["attribution_pending"] = False


def apply_supplementary(result, updates, input_dir):
    supp = updates.get("supplementary_searches") or []
    pre = [o for o in result.get("omissions", []) if o.get("status") == "miss"]
    pre_ids = {o["ref_id"] for o in pre}
    new_hit_ids = []
    for s in supp:
        if not isinstance(s, dict):
            continue
        entry = {
            "id": s.get("id"),
            "query": s.get("query"),
            "field": s.get("field"),
            "source": s.get("source"),
            "time_window": s.get("time_window"),
            "new_hits": [],
        }
        for nh in s.get("new_hits") or []:
            rid = nh.get("ref_id")
            loc = nh.get("source_locator") or nh.get("locator")
            quote = nh.get("quote")
            ok, msg = verify_quote(loc, quote, input_dir)
            valid_ref = any(r.get("ref_id") == rid for r in result["reference_set"].get("records", []))
            if rid not in pre_ids:
                result["errors"].append({"stage": "merge", "detail": "补充命中非此前漏检: {0}".format(rid)})
                continue
            if not valid_ref:
                result["errors"].append({"stage": "merge", "detail": "补充命中不在参考集内: {0}".format(rid)})
                continue
            if not ok:
                result["degradations"].append({"stage": "merge", "detail": "补充命中证据未核验: {0}".format(msg), "ref_id": rid})
            entry["new_hits"].append({"ref_id": rid, "source_locator": loc, "quote": quote, "verification": msg})
            if rid not in new_hit_ids:
                new_hit_ids.append(rid)
            for row in result.get("hit_matrix", []):
                if row.get("ref_id") == rid:
                    row["status"] = "hit_supplement"
                    row["matched_by"] = "supplementary_search"
                    row["match_evidence"] = "supplemented by query '{0}'".format(s.get("query"))
            for o in result.get("omissions", []):
                if o.get("ref_id") == rid:
                    o["status"] = "hit_supplement"
                    o["attribution_pending"] = False
        result.setdefault("supplementary_searches", []).append(entry)

    n_pre = len(pre_ids)
    n_new = len(new_hit_ids)
    result["marginal_gain"] = {
        "pre_supplement_omissions": n_pre,
        "new_unique_hits": n_new,
        "gain_rate": round(n_new / n_pre, 6) if n_pre else None,
    }


def verify_quote(locator, quote, input_dir):
    if not quote:
        return False, "empty quote"
    if not locator:
        return False, "missing locator"
    path = str(locator).split("#")[0]
    if not os.path.isabs(path):
        cand = os.path.join(input_dir, path)
        if os.path.isfile(cand):
            path = cand
    if not os.path.isfile(path):
        return False, "locator file not found: {0}".format(path)
    text, err = _source_text(path)
    if err:
        return False, err
    return (str(quote) in text), ("verified" if str(quote) in text else "quote not found in source")


_source_text_cache = {}


def _source_text(path):
    if path in _source_text_cache:
        return _source_text_cache[path]
    if path.lower().endswith(".pdf"):
        text, err = extract_pdf_text(path)
    else:
        text, err = read_text_file(path)
    _source_text_cache[path] = (text or "", err)
    return _source_text_cache[path]


def finalize_coverage(result):
    matrix = result.get("hit_matrix", [])
    denom = result["reference_set"].get("unique_count", 0) if isinstance(result.get("reference_set"), dict) else 0
    if denom:
        numerator = sum(1 for r in matrix if r["status"] in ("hit", "hit_supplement", "hit_adjudicated"))
        result["coverage"]["denominator"] = denom
        result["coverage"]["numerator"] = numerator
        result["coverage"]["coverage_rate"] = round(numerator / denom, 6)
        result["coverage"]["omission_rate"] = round(1 - numerator / denom, 6)
        result["coverage"]["adjudicate_count"] = sum(1 for r in matrix if r["status"] == "adjudicate")
        result["coverage"]["miss_count"] = sum(1 for r in matrix if r["status"] == "miss")


def final_status(result):
    coverage = result.get("coverage") or {}
    if not (coverage.get("denominator")):
        return "insufficient_evidence"
    omissions = result.get("omissions") or []
    unattributed = [o for o in omissions if o.get("attribution_pending") or not o.get("category")]
    adjudications = [h for h in result.get("hit_matrix", []) if h.get("status") == "adjudicate"]
    if unattributed or adjudications:
        return "partial"
    if result.get("errors"):
        return "partial"
    return "ok"


def build_evidence_map(result):
    emap = []
    rs = result.get("reference_set") or {}
    if rs.get("version"):
        emap.append({"claim": "reference_set_frozen_version", "locator": "reference_set.json", "quote": rs.get("version"), "kind": "derived"})
    cov = result.get("coverage") or {}
    if cov.get("coverage_rate") is not None:
        emap.append({"claim": "coverage_rate", "locator": "coverage_core.json", "quote": str(cov.get("coverage_rate")), "kind": "derived"})
    for o in result.get("omissions", []):
        ev = o.get("evidence")
        if ev and ev.get("quote"):
            emap.append({"claim": "omission_attribution:{0}".format(o.get("ref_id")), "locator": ev.get("locator"), "quote": ev.get("quote"), "kind": "verbatim"})
    for s in result.get("supplementary_searches", []):
        for nh in s.get("new_hits", []):
            if nh.get("quote"):
                emap.append({"claim": "supplementary_hit:{0}".format(nh.get("ref_id")), "locator": nh.get("source_locator"), "quote": nh.get("quote"), "kind": "verbatim"})
    return emap


def write_report(path, result):
    cov = result.get("coverage") or {}
    lines = []
    lines.append("# 检索覆盖率与遗漏分析报告")
    lines.append("")
    lines.append("- case_id: `{0}`".format(result.get("case_id")))
    lines.append("- status: `{0}`".format(result.get("status")))
    lines.append("- 参考集版本: `{0}`".format((result.get("reference_set") or {}).get("version")))
    lines.append("- 覆盖率: {0}/{1} = {2}".format(cov.get("numerator"), cov.get("denominator"), cov.get("coverage_rate")))
    lines.append("- 遗漏率: {0}".format(cov.get("omission_rate")))
    lines.append("- 命中规则: {0}".format(cov.get("hit_rule")))
    lines.append("")
    lines.append("## 命中矩阵")
    lines.append("")
    lines.append("| ref_id | 状态 | 匹配方式 | 相似度 | 参考标题 | 候选标题 |")
    lines.append("|---|---|---|---|---|---|")
    for h in result.get("hit_matrix", []):
        lines.append("| {0} | {1} | {2} | {3} | {4} | {5} |".format(
            h.get("ref_id"), h.get("status"), h.get("matched_by"), h.get("title_similarity"),
            _md_escape(h.get("ref_title")), _md_escape(h.get("res_title"))))
    lines.append("")
    lines.append("## 漏检与归因")
    lines.append("")
    for o in result.get("omissions", []):
        lines.append("- {0} `{1}`: category={2}, evidence={3}".format(
            o.get("ref_id"), _md_escape(o.get("title")), o.get("category"),
            (o.get("evidence") or {}).get("verification")))
    lines.append("")
    lines.append("## 补充检索与边际收益")
    lines.append("")
    mg = result.get("marginal_gain") or {}
    lines.append("- 补检前遗漏: {0}; 新增唯一命中: {1}; 边际收益: {2}".format(
        mg.get("pre_supplement_omissions"), mg.get("new_unique_hits"), mg.get("gain_rate")))
    lines.append("")
    lines.append("## 残余遗漏")
    lines.append("")
    for r in result.get("residual_omissions", []):
        lines.append("- {0} `{1}`: {2}".format(r.get("ref_id"), _md_escape(r.get("title")), r.get("category")))
    lines.append("")
    lines.append("## 限制与待人工")
    lines.append("")
    for l in result.get("limitations", []):
        lines.append("- {0}".format(l))
    for p in result.get("pending_human", []):
        lines.append("- [待人工] {0}".format(p))
    lines.append("")
    atomic_write_text(path, "\n".join(lines), bom=False)


def _md_escape(s):
    if s is None:
        return ""
    return str(s).replace("|", "\\|").replace("\n", " ")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def _add_common(sp):
    sp.add_argument("--input", default=DEFAULT_INPUT, help="输入目录（默认 /input）")
    sp.add_argument("--output", default=DEFAULT_OUTPUT, help="输出目录（默认 /app/output）")
    sp.add_argument("--work", default=DEFAULT_WORK, help="工作目录（默认 /app/work）")


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="检索覆盖率与遗漏分析（时限型，仅标准库）。默认路径: /input → /app/output → /app/work。",
    )
    sub = p.add_subparsers(dest="command", required=True)
    sc = sub.add_parser("scaffold", help="0-3min: 落盘 partial 结果骨架 + 预算计划")
    _add_common(sc)
    ins = sub.add_parser("inspect", help="3-6min: 盘点输入文件，产出 inspect.json")
    _add_common(ins)
    rp = sub.add_parser("run", help="6-10min: 冻结参考集、命中判定、覆盖率")
    _add_common(rp)
    rp.add_argument("--refs", nargs="*", default=None, help="参考集文件 glob（缺省自动识别）")
    rp.add_argument("--results", nargs="*", default=None, help="检索结果文件 glob（缺省自动识别）")
    mp = sub.add_parser("merge", help="13-15min: 合并裁决/归因/补检，产出最终结果与报告")
    _add_common(mp)
    mp.add_argument("--updates", default=None, help="更新文件 JSON（裁决/归因/补充检索）")
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        cmd = args.command
        if cmd == "scaffold":
            return cmd_scaffold(args)
        if cmd == "inspect":
            return cmd_inspect(args)
        if cmd == "run":
            return cmd_run(args)
        if cmd == "merge":
            return cmd_merge(args)
        parser.error("unknown command: {0}".format(cmd))
    except Exception:
        import traceback
        traceback.print_exc()
        try:
            os.makedirs(args.output, exist_ok=True)
            minimal = {
                "case_id": None,
                "status": "partial",
                "errors": [{"stage": "fatal", "detail": traceback.format_exc(limit=3)}],
                "change_log": [{"stage": "fatal", "detail": "unexpected exception"}],
                "limitations": ["unexpected exception; partial result only"],
                "sources": [], "uncertainty": [], "pending_human": [],
            }
            atomic_write_json(os.path.join(args.output, "result.json"), minimal)
        except Exception:
            pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
