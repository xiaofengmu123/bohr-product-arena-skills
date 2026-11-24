#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学术文献检索 Skill 的核心实现（纯标准库，无 argparse、无 import 副作用）。

职责拆分约定：main.py 只做命令行解析与子命令分发，本模块承载可复核的
确定性逻辑——稳健 IO、输入发现、输出契约发现与回填、候选记录规范化、
DOI/标题去重、透明打分、确定性排序、预算/请求台账、结果组装与逐项自检。

所有函数均为无状态纯函数或只读写显式传入的目录，重复运行对研究内容
（results 顺序、score、去重结果）保持确定一致；时间戳仅用于审计。
"""

import hashlib
import json
import os
import re
import time

MAX_FILE_BYTES = 2 * 1024 * 1024
FALLBACK_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")

# 输出契约的可能键名（题目表述不固定，先发现后假设）
CONTRACT_KEY_NAMES = (
    "output_contract", "outputContract", "output", "result_schema", "schema",
    "required_output", "required_fields", "expected_fields", "contract",
)

# 契约键名 -> 结构体内置字段名的映射（仅用于把契约字段回填到真实数据）
CONTRACT_FIELD_MAP = {
    "case_id": "case_id", "caseid": "case_id", "id": "case_id",
    "status": "status",
    "results": "results", "result": "results", "candidates": "results",
    "evidence": "evidence", "sources": "sources",
    "research_question": "research_question", "researchquestion": "research_question",
    "query": "research_question", "topic": "research_question",
    "change_log": "change_log", "changelog": "change_log",
    "uncertainty": "uncertainty", "limitations": "limitations",
    "pending_human": "pending_human", "notes": "status_notes",
}

# 来源可靠性档位（用于 provenance 打分，发布方越靠近一手越可靠）
PROVENANCE_TIER = {
    "crossref": 1.0, "europepmc": 1.0, "pubmed": 1.0, "publisher": 1.0,
    "doi": 1.0, "official": 1.0,
    "arxiv": 0.9, "semanticscholar": 0.9, "openalex": 0.9, "dblp": 0.9,
    "biorxiv": 0.9, "medrxiv": 0.9,
    "google_scholar": 0.7, "scholar": 0.7, "aggregator": 0.7,
    "web": 0.5, "generic": 0.5,
    "unknown": 0.3,
}


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _elapsed_since(iso):
    if not iso:
        return None
    try:
        ts = time.mktime(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))
        return max(0, int(time.time() - ts))
    except (ValueError, OverflowError):
        return None


def ensure_dir(path):
    if path:
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass


def _read_bytes(path):
    try:
        return open(path, "rb").read(), []
    except OSError as e:
        return None, ["read_failed:%s:%s" % (path, str(e)[:80])]


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """按多编码容错读取文本，超限/缺失返回空串与降级记录。"""
    if not path or not os.path.isfile(path):
        return "", ["missing:" + str(path)]
    try:
        size = os.path.getsize(path)
    except OSError:
        return "", ["stat_failed:" + str(path)]
    if size > max_bytes:
        return "", ["size_exceeded:%s:%s" % (path, size)]
    data, errs = _read_bytes(path)
    if errs:
        return "", errs
    for enc in FALLBACK_ENCODINGS:
        try:
            return data.decode(enc), []
        except (UnicodeDecodeError, ValueError):
            continue
    return data.decode("utf-8", errors="replace"), ["fallback_decode:" + str(path)]


def read_json(path, default=None, max_bytes=MAX_FILE_BYTES):
    if default is None:
        default = {}
    if not path or not os.path.isfile(path):
        return default, ["missing:" + str(path)]
    text, deg = read_text(path, max_bytes)
    if deg:
        return default, deg
    if not text.strip():
        return default, ["empty:" + str(path)]
    try:
        return json.loads(text), []
    except Exception as e:
        return default, ["bad_json:%s:%s" % (path, str(e)[:120])]


def write_text(path, text):
    ensure_dir(os.path.dirname(path) or ".")
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
        return True
    except OSError as e:
        return False


def write_json(path, obj):
    ensure_dir(os.path.dirname(path) or ".")
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
            f.write("\n")
        os.replace(tmp, path)
        return True
    except (OSError, TypeError) as e:
        # 兜底：即使含不可序列化对象，也写一版降级产物
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                json.dump({"error": "serialize_failed", "detail": str(e)[:200]},
                          f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp, path)
        except OSError:
            pass
        return False


def discover_input(input_dir):
    out = []
    if not input_dir or not os.path.isdir(input_dir):
        return out
    for root, _dirs, names in os.walk(input_dir):
        for n in sorted(names):
            out.append(os.path.join(root, n))
    return out


def find_case_file(files):
    for f in files:
        if os.path.basename(f).lower() == "case.json":
            return f
    for f in files:
        b = os.path.basename(f).lower()
        if b.startswith("case") and b.endswith(".json"):
            return f
    return None


def find_task_file(files):
    for f in files:
        if os.path.basename(f).lower() == "task.md":
            return f
    for f in files:
        b = os.path.basename(f).lower()
        if b.endswith(".md") and "task" in b:
            return f
    for f in files:
        if f.lower().endswith(".md"):
            return f
    return None


def parse_case(input_dir):
    """发现并解析 /input，返回 (case_summary_dict, degradations_list)。"""
    files = discover_input(input_dir)
    case_path = find_case_file(files)
    task_path = find_task_file(files)
    degradations = []

    case_data, d = read_json(case_path, {}) if case_path else ({}, ["no_case_json"])
    degradations += d
    task_text, d = read_text(task_path) if task_path else ("", ["no_task_md"])
    degradations += d

    case_id = None
    for k in ("case_id", "caseId", "id", "challenge_id", "challengeId", "armiId"):
        v = case_data.get(k) if isinstance(case_data, dict) else None
        if v not in (None, ""):
            case_id = str(v)
            break
    if not case_id:
        m = re.search(r"(?:case_id|challenge_id)\s*[:：]\s*`?([A-Za-z0-9_\-]+)", task_text)
        if m:
            case_id = m.group(1)
    if not case_id:
        case_id = ("case:" + os.path.splitext(os.path.basename(case_path))[0]) \
            if case_path else "unknown"

    research_question = None
    for k in ("research_question", "researchQuestion", "question", "query", "topic",
              "task", "problem", "research_topic", "research_problem", "title"):
        v = case_data.get(k) if isinstance(case_data, dict) else None
        if isinstance(v, str) and v.strip():
            research_question = v.strip()
            break
    if not research_question:
        m = re.search(
            r"(?:研究问题|科研问题|研究主题|Research question|Research topic)\s*[:：]\s*(.+)",
            task_text)
        if m:
            research_question = m.group(1).strip()[:2000]
    if research_question and len(research_question) > 2000:
        research_question = research_question[:2000]

    contract, contract_loc = find_contract(case_data, task_text)

    summary = {
        "case_path": case_path,
        "task_path": task_path,
        "case_id": case_id,
        "research_question": research_question or "",
        "input_files": [os.path.basename(p) for p in files],
        "contract": contract,
        "contract_locator": contract_loc,
        "has_materials": any(
            os.path.join(root, n) not in (case_path, task_path)
            for root, _dirs, names in os.walk(input_dir) for n in names),
    }
    return summary, degradations


def find_contract(case_data, task_text):
    """在 case.json 中定位 output_contract 对象，返回 (dict|None, locator|None)。"""
    if isinstance(case_data, dict):
        for k in CONTRACT_KEY_NAMES:
            v = case_data.get(k)
            if isinstance(v, dict):
                return dict(v), "case.json." + k
            if isinstance(v, list):
                return {"__list": v}, "case.json." + k
    # 题目说明里显式给了字段定义时，作为注释级契约保留（不臆造值）
    m = re.search(
        r"(?:output_contract|输出契约)\s*[:：]?\s*\{([^}]{0,4000})\}", task_text,
        flags=re.IGNORECASE)
    if m:
        return {"__declared_fragment": m.group(1).strip()[:4000]}, "task.md"
    return None, None


def _looks_listy(v):
    if isinstance(v, (list, tuple)):
        return True
    if isinstance(v, str):
        return v.strip().startswith("[")
    return False


_LIST_NAME_HINTS = (
    "results", "result", "candidates", "evidence", "sources", "notes", "authors",
    "tags", "list", "fields", "queries", "errors", "degradations", "limitations",
    "change_log", "changelog", "conflicts", "gaps", "missing_metadata", "pending",
    "items", "records", "literature", "entries", "logs",
)


def _name_looks_listy(name):
    n = str(name).lower()
    return any(h in n for h in _LIST_NAME_HINTS)


def apply_contract(result, contract):
    """把 output_contract 的全部字段回填进 result 根级：有真实值的用真实值
    （含空列表，绝不覆盖）；未获得的按字段类型提示回填 [] 或 null；不改名、
    不删未知键。"""
    if not isinstance(contract, dict):
        return
    for k, v in contract.items():
        if k in ("__list", "__declared_fragment"):
            continue
        kn = str(k)
        target = CONTRACT_FIELD_MAP.get(kn.lower())
        # 映射到已有真实字段（含空列表），直接引用真实值，不覆盖
        if target and target in result and result.get(target) is not None:
            result[kn] = result.get(target)
            continue
        # 已存在同名键且非 None，不覆盖
        if kn in result and result.get(kn) is not None:
            continue
        # 未获得：按契约示例值/字段名提示回填空值
        result[kn] = [] if (_looks_listy(v) or _name_looks_listy(kn)) else None


# ---------- 候选记录 ----------
def normalize_title(title):
    if not isinstance(title, str):
        return ""
    return "".join(ch for ch in title.lower() if ch.isalnum())


def normalize_doi(doi):
    if not isinstance(doi, str):
        return ""
    s = doi.strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = re.sub(r"^doi:\s*", "", s)
    return s.strip()


def record_key(rec):
    """去重键：优先 DOI，否则规范化标题哈希。"""
    doi = normalize_doi(rec.get("doi") or "") if isinstance(rec, dict) else ""
    if doi:
        return "doi:" + doi
    title = normalize_title(rec.get("title") or "") if isinstance(rec, dict) else ""
    if title:
        return "title:" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:16]
    return None


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def _first_str(v):
    for x in _as_list(v):
        if isinstance(x, str) and x.strip():
            return x.strip()
    return None


def _year(v):
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        m = re.search(r"(18|19|20)\d{2}", v)
        if m:
            return int(m.group())
    return None


def normalize_record(rec, defaults):
    """把来自查询工具/手工导出的任意键名收敛到统一候选结构。"""
    if not isinstance(rec, dict):
        return None
    title = _first_str(rec.get("title") or rec.get("name") or rec.get("paper_title"))
    if not title:
        return None
    authors = _as_list(rec.get("authors") or rec.get("author") or rec.get("creators"))
    authors = [a if isinstance(a, str) else str(a) for a in authors][:64]
    doi = _first_str(rec.get("doi") or rec.get("DOI")) or None
    url = _first_str(rec.get("url") or rec.get("link") or rec.get("href"))
    if not url and doi:
        url = "https://doi.org/" + normalize_doi(doi)
    database = _first_str(rec.get("database") or rec.get("source")) or "unknown"
    relevance = rec.get("relevance")
    if relevance is None:
        relevance = rec.get("score") if isinstance(rec.get("score"), (int, float)) else None
    try:
        relevance = float(relevance) if relevance is not None else None
        relevance = max(0.0, min(1.0, relevance))
    except (TypeError, ValueError):
        relevance = None
    evidence = rec.get("evidence") or defaults.get("evidence") or []
    if isinstance(evidence, dict):
        evidence = [evidence]

    return {
        "title": title,
        "authors": authors,
        "year": _year(rec.get("year") or rec.get("publication_year")),
        "journal": _first_str(rec.get("journal") or rec.get("venue") or
                              rec.get("container") or rec.get("publisher")),
        "doi": doi,
        "url": url,
        "database": database.lower(),
        "relevance": relevance,
        "relevance_reason": _first_str(rec.get("relevance_reason") or
                                       rec.get("relevance_reason_note")) or "",
        "evidence": [normalize_evidence(e) for e in evidence
                     if normalize_evidence(e)],
    }


def normalize_evidence(e):
    """证据必须含 locator + 逐字 quote，缺失则不采信。"""
    if not isinstance(e, dict):
        return None
    loc = str(e.get("locator") or e.get("location") or "").strip()
    quote = str(e.get("quote") or e.get("quotation") or e.get("text") or "").strip()
    if not quote:
        return None
    return {
        "locator": loc or "unspecified",
        "quote": quote[:2000],
        "source": str(e.get("source") or e.get("database") or "unknown"),
        "kind": str(e.get("kind") or "recall"),
    }


def import_records(work_dir, incoming, defaults=None):
    """按 DOI/标题 upsert 候选，返回 (records_list, log_lines)。"""
    defaults = defaults or {}
    rec_path = os.path.join(work_dir, "records.json")
    records, _ = read_json(rec_path, [])
    if not isinstance(records, list):
        records = []
    index = {}
    for i, r in enumerate(records):
        k = record_key(r)
        if k:
            index[k] = i
    log = []
    for raw in incoming or []:
        rec = normalize_record(raw, defaults)
        if not rec:
            log.append("skipped:empty_title")
            continue
        k = record_key(rec)
        if not k:
            log.append("skipped:no_key:%s" % rec["title"][:40])
            continue
        if k in index:
            records[index[k]] = merge_record(records[index[k]], rec)
            log.append("merged:%s" % k)
        else:
            index[k] = len(records)
            records.append(rec)
            log.append("added:%s" % k)
    write_json(rec_path, records)
    return records, log


def merge_record(old, new):
    """旧记录优先保留已有元数据，仅用新字段填补空缺。"""
    for f in ("title", "authors", "year", "journal", "doi", "url", "database",
              "relevance", "relevance_reason"):
        if old.get(f) in (None, "", [], 0):
            old[f] = new.get(f)
    if new.get("evidence"):
        seen = {json.dumps(e, sort_keys=True, default=str) for e in old.get("evidence", [])}
        for e in new["evidence"]:
            if json.dumps(e, sort_keys=True, default=str) not in seen:
                old.setdefault("evidence", []).append(e)
    return old


# ---------- 打分与排序 ----------
def completeness(rec):
    present = 0
    total = 5
    for f, weight in (("title", 1), ("authors", 1), ("year", 1),
                      ("journal", 1), ("doi", 1)):
        v = rec.get(f)
        if v not in (None, "", [], 0):
            present += weight
    return present / total


def provenance_score(database):
    db = (database or "unknown").lower()
    if not db:
        return PROVENANCE_TIER["unknown"]
    for k, tier in PROVENANCE_TIER.items():
        if k in db:
            return tier
    return PROVENANCE_TIER["unknown"]


def score_record(rec):
    """透明打分：0.6·相关度 + 0.2·元数据完整度 + 0.2·来源可靠度。"""
    rel = rec.get("relevance")
    if rel is None:
        rel = 0.5  # 未给出相关性判定时取中性，并在 uncertainty.notes 标注
    comp = completeness(rec)
    prov = provenance_score(rec.get("database"))
    score = round(0.6 * rel + 0.2 * comp + 0.2 * prov, 4)
    return score, {"relevance": round(rel, 4), "completeness": round(comp, 4),
                   "provenance": round(prov, 4)}


def rank_records(records):
    """确定性排序：score 降序 → year 降序 → 标题字典序 → DOI。"""
    scored = []
    for rec in records:
        score, breakdown = score_record(rec)
        r = dict(rec)
        r["score"] = score
        r["score_breakdown"] = breakdown
        scored.append(r)
    scored.sort(key=lambda r: (
        -r["score"],
        -(r.get("year") or 0),
        (r.get("title") or "").lower(),
        normalize_doi(r.get("doi") or ""),
    ))
    return scored


def annotate_uncertainty(records, uncertainty):
    """统计收录记录中由打分器自动补出的默认值，写入 uncertainty.notes。"""
    auto_relevance = [r.get("title") for r in records if r.get("relevance") is None]
    if auto_relevance:
        uncertainty.setdefault("notes", [])
        uncertainty["notes"].append(
            "relevance_default_0.5_for:%d_records" % len(auto_relevance))
    return uncertainty


# ---------- 预算与请求台账 ----------
PHASES = [
    {"id": "mint", "label": "骨架落盘+解析", "max_seconds": 180, "requests": 2,
     "checkpoint": True},
    {"id": "plan", "label": "多轮查询规划", "max_seconds": 90, "requests": 3,
     "checkpoint": True},
    {"id": "recall", "label": "候选召回", "max_seconds": 420, "requests": 40,
     "checkpoint": True},
    {"id": "verify", "label": "元数据核验+去重", "max_seconds": 120, "requests": 8,
     "checkpoint": True},
    {"id": "assemble", "label": "组装+自检", "max_seconds": 60, "requests": 1,
     "checkpoint": True},
    {"id": "buffer", "label": "缓冲", "max_seconds": 30, "requests": 10,
     "checkpoint": False},
]
TOTAL_SECONDS = 900
TOTAL_REQUESTS = 64


def _phase_table():
    return {p["id"]: dict(p) for p in PHASES}


def load_budget(work_dir):
    path = os.path.join(work_dir, "budget.json")
    b, _ = read_json(path, {})
    if not isinstance(b, dict):
        b = {}
    if not b.get("created"):
        b["created"] = now_iso()
        b["phases"] = _phase_table()
        b["total_seconds"] = TOTAL_SECONDS
        b["total_requests"] = TOTAL_REQUESTS
        b.setdefault("requests_used", 0)
        write_json(path, b)
    return b


def ledger_add_request(work_dir, meta):
    """Agent 在每次模型请求前调用，累计并检查 64 次预算。返回状态字符串。"""
    budget = load_budget(work_dir)
    used = int(budget.get("requests_used", 0)) + 1
    budget["requests_used"] = used
    over = used > TOTAL_REQUESTS
    bad = used > TOTAL_REQUESTS + 12
    entry = {
        "seq": used,
        "time": now_iso(),
        "phase": meta.get("phase", "unknown") if isinstance(meta, dict) else "unknown",
        "action": (meta.get("action") if isinstance(meta, dict) else None) or "request",
        "reason": (meta.get("reason") if isinstance(meta, dict) else None) or "",
        "over_budget": over,
    }
    budget.setdefault("ledger", []).append(entry)
    write_json(os.path.join(work_dir, "budget.json"), budget)
    if bad:
        return "STOP_BUDGET_EXHAUSTED"
    return "WARN_OVER_BUDGET" if over else "OK"


def budget_report(work_dir, elapsed=None):
    budget = load_budget(work_dir)
    if elapsed is None:
        elapsed = _elapsed_since(budget.get("created"))
    report = {
        "elapsed_seconds": elapsed,
        "total_seconds": TOTAL_SECONDS,
        "requests_used": budget.get("requests_used", 0),
        "requests_budget": TOTAL_REQUESTS,
        "phases": budget.get("phases", _phase_table()),
    }
    for p in report["phases"].values():
        p["max_seconds"] = p.get("max_seconds", 0)
    return report


# ---------- 变更日志 ----------
def append_change_log(work_dir, action, reason, status="partial"):
    path = os.path.join(work_dir, "change_log.json")
    entries, _ = read_json(path, [])
    if not isinstance(entries, list):
        entries = []
    entries.append({"time": now_iso(), "action": action, "reason": reason,
                    "status": status})
    write_json(path, entries)
    return entries


def load_change_log(work_dir):
    entries, _ = read_json(os.path.join(work_dir, "change_log.json"), [])
    return entries if isinstance(entries, list) else []


# ---------- 证据日志 ----------
def append_evidence(work_dir, item):
    if not isinstance(item, dict):
        return []
    path = os.path.join(work_dir, "evidence.json")
    entries, _ = read_json(path, [])
    if not isinstance(entries, list):
        entries = []
    entry = {
        "query": str(item.get("query") or "").strip(),
        "source": str(item.get("source") or "unknown"),
        "database": str(item.get("database") or ""),
        "timestamp": now_iso(),
        "rationale": str(item.get("rationale") or "").strip(),
        "kind": str(item.get("kind") or "query"),
    }
    if entry["query"]:
        entries.append(entry)
    else:
        entries.append(entry)
    write_json(path, entries)
    return entries


def load_evidence(work_dir):
    entries, _ = read_json(os.path.join(work_dir, "evidence.json"), [])
    return entries if isinstance(entries, list) else []


# ---------- 组装 ----------
def assemble_result(input_dir, work_dir):
    summary, degradations = parse_case(input_dir)
    records, _ = read_json(os.path.join(work_dir, "records.json"), [])
    if not isinstance(records, list):
        records = []
    evidence = load_evidence(work_dir)
    change_log = load_change_log(work_dir)
    budget = load_budget(work_dir)
    started = budget.get("created") or now_iso()

    scored = rank_records(records)
    uncertainty = _collect_uncertainty(records, change_log)
    uncertainty = annotate_uncertainty(records, uncertainty)

    status = _decide_status(records, degradations, evidence)
    status_notes = list(degradations)
    if not records:
        status_notes.append("no_candidate_records")
    if not summary.get("research_question"):
        status_notes.append("research_question_not_found")

    result = {
        "case_id": summary.get("case_id") or "unknown",
        "status": status,
        "results": [
            {"rank": i + 1, **{k: v for k, v in r.items()
                               if k not in ("score_breakdown",)},
             "score_breakdown": r.get("score_breakdown", {})}
            for i, r in enumerate(scored)
        ],
        "evidence": evidence,
        "status_notes": status_notes,
        "sources": _collect_sources(records, evidence),
        "uncertainty": uncertainty,
        "change_log": change_log,
        "research_question": summary.get("research_question") or "",
        "search_scope": _derive_scope(summary, evidence),
        "limitations": [],
        "errors": [],
        "degradations": [d for d in degradations if d.startswith("size_exceeded")],
        "pending_human": [],
        "runtime": {
            "started_at": started,
            "completed_at": now_iso(),
        },
        "budget": budget_report(work_dir),
    }
    if not summary.get("research_question"):
        result["pending_human"].append("research_question_must_be_confirmed")
    for d in degradations:
        if d.startswith("bad_json") or d.startswith("read_failed"):
            result["errors"].append(d)
    apply_contract(result, summary.get("contract") or {})

    return result


def _decide_status(records, degradations, evidence):
    if any(d.startswith("bad_json") or d.startswith("read_failed") for d in degradations):
        if not records:
            return "input_error"
    if not records:
        return "insufficient_evidence"
    if not evidence:
        return "partial"
    return "ok"


def _collect_sources(records, evidence):
    seen = {}
    out = []
    for r in records:
        src = (r.get("database") or "unknown").lower()
        if src not in seen and src != "unknown":
            seen[src] = True
            out.append({"source": "literature_record", "database": src,
                        "accessed": now_iso()})
    for e in evidence:
        key = (e.get("source"), e.get("database"))
        if key not in seen:
            seen[key] = True
            out.append({"source": e.get("source"), "database": e.get("database"),
                        "accessed": e.get("timestamp") or now_iso()})
    return out


def _collect_uncertainty(records, change_log):
    return {
        "missing_metadata": sorted(_missing_fields(records)),
        "conflicts": [],
        "coverage_gaps": [],
        "notes": [],
    }


def _missing_fields(records):
    out = set()
    for r in records:
        for f in ("authors", "year", "journal", "doi", "url"):
            if r.get(f) in (None, "", [], 0):
                out.add(f)
    return ["%s(%d)" % (f, sum(1 for r in records
                               if r.get(f) in (None, "", [], 0))) for f in
            ("authors", "year", "journal", "doi", "url")
            if any(r.get(f) in (None, "", [], 0) for r in records)]


def _derive_scope(summary, evidence):
    q = summary.get("research_question") or ""
    if not q:
        return ""
    concepts = [c for c in re.split(r"[，,;：;、\n]", q) if c.strip()]
    return "research_question=%s; concepts=%s; queries=%d" % (
        q[:200], ("|".join(c.strip()[:40] for c in concepts[:8]) or "_"),
        len(evidence))


# ---------- 渲染 ----------
def render_candidates_md(result):
    lines = ["# 候选文献清单（可编辑，供人工复核）", "",
             "- case_id: `%s`" % (result.get("case_id") or ""),
             "- status: `%s`" % (result.get("status") or ""),
             "- 生成时间: %s" % (result.get("runtime", {}).get("completed_at") or ""),
             ""]
    rs = result.get("results") or []
    if not rs:
        lines += ["_无候选记录。_", ""]
        return "\n".join(lines)
    lines += ["| rank | title | authors | year | journal | doi | score | relevance_reason |",
              "|-|-|-|-|-|-|-|-|"]
    for r in rs:
        auth = "; ".join(r.get("authors") or [])[:80] or "-"
        lines.append("| %d | %s | %s | %s | %s | %s | %.3f | %s |" % (
            r.get("rank"), (r.get("title") or "-").replace("|", "/")[:120],
            auth.replace("|", "/"), r.get("year") or "-",
            (r.get("journal") or "-").replace("|", "/")[:60],
            (r.get("doi") or "-")[:40], r.get("score") or 0,
            (r.get("relevance_reason") or "-").replace("|", "/")[:100]))
    lines += ["", "## 证据定位", ""]
    for r in rs:
        for e in r.get("evidence") or []:
            lines.append("- `%s` > %s" % (e.get("locator"), e.get("quote")[:160]))
        lines.append("")
    lines.append("_本清单由 result.json 确定性地渲染，属于可编辑产物，非正式案例答案。_")
    return "\n".join(lines)


# ---------- 自检 ----------
def selfcheck_result(result):
    checks = []
    def chk(name, ok, detail=""):
        checks.append({"item": name, "ok": bool(ok), "detail": detail})

    chk("case_id_present", bool(result.get("case_id")))
    chk("status_valid", result.get("status") in
        ("ok", "partial", "insufficient_evidence", "input_error"))
    chk("results_is_list", isinstance(result.get("results"), list))
    chk("evidence_is_list", isinstance(result.get("evidence"), list))
    chk("change_log_is_list", isinstance(result.get("change_log"), list))
    chk("sources_present", "sources" in result)
    chk("uncertainty_present", isinstance(result.get("uncertainty"), dict))
    chk("pending_human_present", "pending_human" in result)

    bad_evidence = []
    for i, r in enumerate(result.get("results") or []):
        if not r.get("title"):
            bad_evidence.append("rank%d_no_title" % (i + 1))
        ev = r.get("evidence") or []
        if not any(isinstance(e, dict) and e.get("quote") for e in ev):
            bad_evidence.append("rank%d_no_quote" % (i + 1))
        if r.get("score") is None:
            bad_evidence.append("rank%d_no_score" % (i + 1))
    chk("results_evidence_quoted", not bad_evidence, ";".join(bad_evidence[:6]))

    ranks = [r.get("rank") for r in result.get("results") or []]
    chk("ranks_sequential", ranks == list(range(1, len(ranks) + 1)))
    critical = [c for c in checks if not c["ok"] and c["item"] in
                ("case_id_present", "status_valid", "results_is_list")]
    return checks, critical


def checklist_text(checks):
    lines = ["# 自检结果", ""]
    for c in checks:
        lines.append("- [%s] %s%s" % ("x" if c["ok"] else " ", c["item"],
                                      (" — " + c["detail"]) if c["detail"] else ""))
    lines.append("")
    lines.append("PASS" if all(c["ok"] for c in checks) else "FAIL")
    return "\n".join(lines)