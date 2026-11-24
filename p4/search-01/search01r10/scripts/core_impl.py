#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""source-bound literature search：核心实现（纯标准库，无网络访问）。

设计取舍（与常见“多散落 JSON 文件”实现不同）：
  * 单一版本化“账本”（ledger.json）作为唯一事实来源；
  * 每次导入/证据/规划都追加一条带单调 seq 的 change_log，只增不改；
  * result.json 与 candidates.md 是 ledger 的**纯函数派生结果**，重复运行确定一致。

检索动作不在此脚本内完成：真正的 paper_search / web_search 由调用方
Agent 执行，再把命中记录通过 `import` / `evidence` 写回账本。本模块只
负责解析、去重、打分、排序、组装与自检这些确定性工序。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import unicodedata

SCHEMA_VERSION = 1
MAX_FILE_BYTES = 2 * 1024 * 1024

# 数据库 -> 来源可靠性权重（用于确定性打分 0.2·source）
DB_RELIABILITY = {
    "crossref": 1.00,
    "publisher": 1.00,
    "europepmc": 0.95,
    "europe_pmc": 0.95,
    "pubmed": 0.95,
    "semantic_scholar": 0.90,
    "doi": 0.90,
    "arxiv": 0.85,
    "paper_search": 0.85,
    "scholar": 0.85,
    "web_search": 0.70,
    "web": 0.70,
    "local_pdf": 0.80,
    "material": 0.80,
    "unknown": 0.50,
}

# 停用词（仅用于启发式关键词抽取，不用于相关性判断）
_STOP = {
    "the", "a", "an", "of", "and", "or", "for", "to", "in", "on", "with",
    "by", "at", "from", "as", "is", "are", "was", "were", "be", "been",
    "this", "that", "these", "those", "it", "its", "we", "they", "their",
    "research", "study", "studies", "paper", "using", "based", "new",
}


# --------------------------------------------------------------------------- #
# 稳健 IO
# --------------------------------------------------------------------------- #
def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def read_text(path, max_bytes=MAX_FILE_BYTES):
    if not path or not os.path.isfile(path):
        return "", ["missing:" + str(path)]
    try:
        size = os.path.getsize(path)
    except OSError:
        return "", ["stat_failed:" + str(path)]
    if size > max_bytes:
        return "", ["size_exceeded:" + str(path) + ":" + str(size)]
    try:
        data = open(path, "rb").read()
    except OSError as exc:
        return "", ["read_failed:" + str(path) + ":" + str(exc)[:80]]
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc), []
        except (UnicodeDecodeError, ValueError):
            continue
    return data.decode("utf-8", errors="replace"), ["fallback_decode:" + str(path)]


def read_json(path, default=None, max_bytes=MAX_FILE_BYTES):
    if default is None:
        default = {}
    text, deg = read_text(path, max_bytes)
    if deg:
        return default, deg
    if not text.strip():
        return default, ["empty:" + str(path)]
    try:
        return json.loads(text), []
    except Exception as exc:  # noqa: BLE001
        return default, ["bad_json:" + str(path) + ":" + str(exc)[:120]]


def write_text(path, text):
    ensure_dir(os.path.dirname(path) or ".")
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
        return True
    except OSError as exc:
        print("write_text failed:", path, str(exc)[:200], file=sys.stderr)
        return False


def write_json(path, obj):
    ensure_dir(os.path.dirname(path) or ".")
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, path)
        return True
    except OSError as exc:
        print("write_json failed:", path, str(exc)[:200], file=sys.stderr)
        return False


# --------------------------------------------------------------------------- #
# 输入发现与解析
# --------------------------------------------------------------------------- #
def discover_input(input_dir):
    out = []
    if not input_dir or not os.path.isdir(input_dir):
        return out
    for root, _dirs, names in os.walk(input_dir):
        for name in sorted(names):
            out.append(os.path.join(root, name))
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


def extract_case_id(case_data, task_text, case_path):
    for key in ("case_id", "caseId", "id", "challenge_id", "challengeId", "armiId"):
        val = case_data.get(key)
        if val not in (None, "", [], {}):
            return str(val)
    m = re.search(r"(?:case_id|challenge_id)\s*[:：]\s*`?([A-Za-z0-9_\-]+)", task_text)
    if m:
        return m.group(1)
    if case_path:
        return "case:" + os.path.splitext(os.path.basename(case_path))[0]
    return "unknown"


def extract_question(case_data, task_text):
    for key in ("research_question", "researchQuestion", "question", "query",
                "topic", "task", "problem", "research_topic", "research_problem",
                "title", "objective"):
        val = case_data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    m = re.search(r"(?:研究问题|科研问题|研究主题|Research question|Research topic)"
                  r"\s*[:：]\s*(.+)", task_text)
    if m:
        return m.group(1).strip()[:2000]
    for line in task_text.splitlines():
        s = line.strip()
        if s and not s.startswith(("#", "-", "*", "|", ">")):
            if len(s) > 12:
                return s[:2000]
    return None


def extract_contract(case_data):
    """从 case.json 尽力抽取输出契约，返回 (verbatim_dict, ordered_keys)。"""
    for key in ("output_contract", "outputContract", "output", "result_schema",
                "schema", "required_output", "required_fields",
                "expected_fields", "contract"):
        if key in case_data and case_data[key] not in (None, "", []):
            value = case_data[key]
            if isinstance(value, dict):
                return value, list(value.keys())
            if isinstance(value, list):
                keys = []
                verb = {}
                for item in value:
                    if isinstance(item, str):
                        keys.append(item)
                        verb[item] = None
                    elif isinstance(item, dict):
                        keys.append(item.get("name") or item.get("key"))
                        verb.update(item)
                return verb, [k for k in keys if k]
            if isinstance(value, str):
                return {value: None}, [value]
    return {}, []


# --------------------------------------------------------------------------- #
# 规范化（去重键 + 打分）
# --------------------------------------------------------------------------- #
def _norm_unicode(text):
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    return text


def normalize_title(title):
    # NFKC 统一全/半角，随后仅保留字母数字，折叠大小写与空白
    text = _norm_unicode(title).lower()
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_doi(doi):
    if not doi:
        return ""
    d = _norm_unicode(doi).strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    d = re.sub(r"^doi:\s*", "", d)
    return d.strip()


def tokenize_terms(text):
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]{2,}|[\u4e00-\u9fff]+", text or "")
    return [w for w in words if w.lower() not in _STOP]


def heuristic_plan(question):
    question = question or ""
    terms = tokenize_terms(question)
    queries = []
    if question.strip():
        queries.append(question.strip())
    for term in terms:
        queries.append(term)
    for i in range(len(terms) - 1):
        queries.append(terms[i] + " " + terms[i + 1])
    # 去重保序
    seen = set()
    uniq = []
    for q in queries:
        if q and q not in seen:
            seen.add(q)
            uniq.append(q)
    return {
        "research_question": question.strip() or None,
        "keywords": terms,
        "query_candidates": uniq[:24],
        "databases": ["crossref", "europepmc", "publisher", "paper_search"],
        "strategy": "single-concept -> AND/OR combination -> citation/reference graph",
    }


# --------------------------------------------------------------------------- #
# 账本（单一事实来源）
# --------------------------------------------------------------------------- #
def ledger_default():
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": None,
        "case": {"research_question": None, "constraints": None, "contract": None},
        "plan": None,
        "queries": [],
        "candidates": [],
        "evidence": [],
        "excluded": [],
        "sources": [],
        "uncertainty": {"missing_metadata": [], "conflicts": [],
                        "coverage_gaps": [], "notes": []},
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "runtime": {"started_at": None},
    }


def load_ledger(work_dir):
    path = os.path.join(work_dir, "ledger.json")
    ledger, deg = read_json(path, ledger_default())
    if not isinstance(ledger, dict):
        ledger = ledger_default()
    merged = ledger_default()
    merged.update(ledger)
    merged["uncertainty"] = dict(ledger_default()["uncertainty"],
                                 ** (ledger.get("uncertainty") or {}))
    merged["degradations"] = list(merged.get("degradations") or [])
    # 首轮尚无账本属正常初始化，不记为降级；仅记录真实读写异常
    for d in deg:
        if not d.startswith("missing:"):
            merged["degradations"].append(d)
    return merged


def save_ledger(work_dir, ledger):
    return write_json(os.path.join(work_dir, "ledger.json"), ledger)


def append_change(ledger, action, reason, status="partial"):
    seq = len([e for e in ledger["change_log"]
               if isinstance(e, dict) and "seq" in e]) + 1
    ledger["change_log"].append({
        "seq": seq,
        "time": now_iso(),
        "action": action,
        "reason": reason,
        "status": status,
    })


# --------------------------------------------------------------------------- #
# 检索记录：导入 / 去重 / 打分
# --------------------------------------------------------------------------- #
def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def canonical_candidate(rec, idx):
    title = rec.get("title") or rec.get("label") or rec.get("name") or ""
    doi = rec.get("doi") or ""
    excluded = bool(rec.get("excluded") or rec.get("status") == "excluded")
    return {
        "cand_id": "c%06d" % idx,
        "title": str(title).strip() or None,
        "authors": _as_list(rec.get("authors") or rec.get("author")),
        "year": rec.get("year") or rec.get("publication_year"),
        "journal": rec.get("journal") or rec.get("container_title") or
                   rec.get("venue"),
        "doi": rec.get("doi"),
        "url": rec.get("url") or rec.get("link"),
        "database": (rec.get("database") or rec.get("source") or
                     rec.get("db") or "unknown").lower(),
        "relevance": rec.get("relevance") if isinstance(rec.get("relevance"),
                                                        (int, float)) else None,
        "relevance_reason": rec.get("relevance_reason") or
                            rec.get("relevanceExplain"),
        "evidence": _as_list(rec.get("evidence")),
        "source_conflict": _as_list(rec.get("source_conflict")),
        "excluded": excluded,
        "exclusion_reason": rec.get("exclusion_reason") or
                            (rec.get("relevance_reason") if excluded else None),
        "status": "excluded" if excluded else "candidate",
    }


def metadata_keys():
    return ("title", "authors", "year", "journal", "doi", "url")


def metadata_completeness(rec):
    total = len(metadata_keys())
    present = 0
    for k in metadata_keys():
        v = rec.get(k)
        if v not in (None, "", [], {}):
            present += 1
    return present / total


def source_reliability(database):
    db = (database or "unknown").lower().strip()
    for key, val in DB_RELIABILITY.items():
        if key in db:
            return val
    return DB_RELIABILITY["unknown"]


def score_candidate(rec):
    rel = rec.get("relevance")
    rel = 0.0 if rel is None else max(0.0, min(1.0, float(rel)))
    meta = metadata_completeness(rec)
    src = source_reliability(rec.get("database"))
    score = round(0.6 * rel + 0.2 * meta + 0.2 * src, 4)
    return score


def dedup_key(rec):
    key = normalize_doi(rec.get("doi"))
    if key:
        return "doi:" + key
    return "title:" + normalize_title(rec.get("title") or "")


def import_candidates(ledger, payload):
    """payload: 单条 dict、dict 的 list，或 {"candidates": [...]} / {"results": [...]}。

    记录可带 `"excluded": true`（或 `status == "excluded"`）进入排除清单，
    附 `exclusion_reason` 作为筛选依据。
    """
    raw = payload
    if isinstance(payload, dict):
        if "candidates" in payload:
            raw = payload["candidates"]
        elif "results" in payload:
            raw = payload["results"]
        elif "papers" in payload:
            raw = payload["papers"]
        else:
            raw = [payload]
    raw = _as_list(raw)

    def index_of(items):
        seen = {}
        for rec in items:
            key = dedup_key(rec)
            if key and rec.get("cand_id"):
                seen[key] = rec["cand_id"]
        return seen

    seen_in = index_of(ledger["candidates"])
    seen_ex = index_of(ledger["excluded"])

    base = len(ledger["candidates"]) + len(ledger["excluded"])
    added = merged = added_ex = 0
    for rec in raw:
        if not isinstance(rec, dict):
            continue
        if not (rec.get("title") or rec.get("doi")):
            continue
        can = canonical_candidate(rec, base + added + added_ex + 1)
        key = dedup_key(can)
        target = "excluded" if can["excluded"] else "candidates"
        seen = seen_ex if can["excluded"] else seen_in
        if key and key in seen:
            target_id = seen[key]
            for existing in ledger[target]:
                if existing.get("cand_id") == target_id:
                    _merge_record(existing, can)
                    merged += 1
                    break
            continue
        ledger[target].append(can)
        seen[key] = can["cand_id"]
        if can["excluded"]:
            added_ex += 1
        else:
            added += 1
    return added, merged, added_ex


def _merge_record(target, incoming):
    for k in ("title", "year", "journal", "doi", "url", "database",
              "relevance", "relevance_reason"):
        if target.get(k) in (None, "", [], {}):
            target[k] = incoming.get(k)
    # 元数据冲突：同一字段两个来源不同值 -> 记录 conflict，保留原值
    for k in ("title", "year", "journal", "doi", "url"):
        old = target.get(k)
        new = incoming.get(k)
        if old is not None and new is not None and str(old) != str(new):
            target.setdefault("source_conflict", [])
            entry = {"field": k, "values": [str(old), str(new)],
                     "note": "multiple sources differ; target value retained"}
            if entry not in target["source_conflict"]:
                target["source_conflict"].append(entry)
    for ev in incoming.get("evidence") or []:
        if ev not in (target.get("evidence") or []):
            target.setdefault("evidence", []).append(ev)


def add_evidence(ledger, entry):
    if not isinstance(entry, dict):
        return
    ev = {
        "query": entry.get("query"),
        "source": entry.get("source"),
        "database": entry.get("database"),
        "timestamp": entry.get("timestamp") or now_iso(),
        "rationale": entry.get("rationale"),
        "kind": entry.get("kind") or "search",
        "locator": entry.get("locator"),
        "quote": entry.get("quote"),
    }
    ledger["evidence"].append(ev)


# --------------------------------------------------------------------------- #
# sources 汇总
# --------------------------------------------------------------------------- #
def refresh_sources(ledger):
    seen = set()
    sources = []
    for rec in ledger["candidates"] + ledger["excluded"]:
        for src in (rec.get("url"), rec.get("doi")):
            if src and str(src) not in seen:
                seen.add(str(src))
                sources.append(str(src))
    for ev in ledger["evidence"]:
        src = ev.get("source")
        if src and str(src) not in seen:
            seen.add(str(src))
            sources.append(str(src))
    ledger["sources"] = sources


# --------------------------------------------------------------------------- #
# 组装
# --------------------------------------------------------------------------- #
def determine_status(ledger):
    if ledger["errors"]:
        # 输入/解析层面被阻断
        fatal = [e for e in ledger["errors"]
                 if isinstance(e, str) and ("bad_json" in e or "missing" in e or
                                            "size_exceeded" in e)]
        if fatal and not ledger["candidates"]:
            return "input_error"
    if not ledger["candidates"]:
        return "insufficient_evidence"
    missing = ledger["uncertainty"].get("missing_metadata") or []
    conflicts = ledger["uncertainty"].get("conflicts") or []
    if missing or conflicts or ledger["degradations"] or ledger["pending_human"]:
        return "partial"
    return "ok"


def build_result(ledger):
    # 先按分数、年份、标题稳定排序
    cands = list(ledger["candidates"])
    for rec in cands:
        rec["score"] = score_candidate(rec)
    cands.sort(key=lambda r: (
        -float(r.get("score") or 0),
        -(r.get("year") if isinstance(r.get("year"), (int, float)) else 0),
        (r.get("title") or "").lower(),
        r.get("cand_id") or "",
    ))

    results = []
    uncertainty = dict(ledger["uncertainty"])
    uncertainty.setdefault("coverage_gaps", [])

    for rank, rec in enumerate(cands, 1):
        missing = [k for k in metadata_keys() if rec.get(k) in (None, "", [], {})]
        item = {
            "rank": rank,
            "title": rec.get("title"),
            "authors": rec.get("authors") or [],
            "year": rec.get("year"),
            "journal": rec.get("journal"),
            "doi": rec.get("doi"),
            "url": rec.get("url"),
            "database": rec.get("database"),
            "relevance": rec.get("relevance"),
            "relevance_reason": rec.get("relevance_reason"),
            "score": rec.get("score"),
            "evidence": rec.get("evidence") or [],
        }
        if rec.get("source_conflict"):
            item["source_conflict"] = rec["source_conflict"]
            for c in rec["source_conflict"]:
                conf = "field=%s values=%s" % (c.get("field"), c.get("values"))
                if conf not in uncertainty["conflicts"]:
                    uncertainty["conflicts"].append(conf)
        if missing:
            for m in missing:
                if m not in uncertainty["missing_metadata"]:
                    uncertainty["missing_metadata"].append(m)
        results.append(item)

    status = determine_status(ledger)
    result = {
        "case_id": ledger.get("case_id"),
        "status": status,
        "results": results,
        "evidence": ledger.get("evidence") or [],
        "status_notes": ledger.get("status_notes") or [],
        "sources": ledger.get("sources") or [],
        "uncertainty": uncertainty,
        "pending_human": ledger.get("pending_human") or [],
        "change_log": ledger.get("change_log") or [],
        "research_question": (ledger.get("case") or {}).get("research_question"),
        "search_scope": ledger.get("search_scope") or
                        (ledger.get("case") or {}).get("search_scope"),
        "limitations": ledger.get("limitations") or [],
        "errors": ledger.get("errors") or [],
        "degradations": ledger.get("degradations") or [],
        "runtime": ledger.get("runtime") or {},
    }

    # 输出契约回填：契约字段必须全部原样存在，未获得的给 null/[]
    contract = (ledger.get("case") or {}).get("contract") or {}
    if isinstance(contract, dict):
        for key, default_val in contract.items():
            if key not in result:
                result[key] = _contract_default(default_val)
    # 若契约还指定了嵌套字段名（列表形式），也保证键存在
    contract_keys = (ledger.get("case") or {}).get("contract_keys") or []
    for key in contract_keys:
        if key and key not in result:
            result[key] = None
    return result


def _contract_default(value):
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return {k: _contract_default(v) for k, v in value.items()}
    if value is None:
        return None
    # 契约里的示例值不作为事实；回填 null，避免“看起来像结果”的填充
    return None


def render_candidates_md(result):
    lines = ["# 候选文献清单（可编辑）", "",
             "> 本文件由 result.json 渲染，供人工复核；修改后需重新 `assemble`。", ""]
    lines.append("- case_id: `%s`" % (result.get("case_id") or "unknown"))
    lines.append("- status: `%s`" % (result.get("status") or "unknown"))
    lines.append("")
    if not result.get("results"):
        lines.append("（暂无候选文献；见 result.json 的 uncertainty 与 status_notes。）")
        return "\n".join(lines)
    for item in result["results"]:
        lines.append("## %d. %s" % (item.get("rank"), item.get("title") or "(无标题)"))
        meta = []
        if item.get("authors"):
            meta.append(", ".join(str(a) for a in item["authors"]))
        if item.get("year"):
            meta.append(str(item["year"]))
        if item.get("journal"):
            meta.append(str(item["journal"]))
        if meta:
            lines.append("- " + " · ".join(meta))
        if item.get("doi"):
            lines.append("- DOI: `%s`" % item["doi"])
        if item.get("url"):
            lines.append("- 来源: %s" % item["url"])
        lines.append("- 相关性理由: %s" % (item.get("relevance_reason") or "未说明"))
        lines.append("- 得分: %s (rel=%s)" % (item.get("score"), item.get("relevance")))
        for ev in item.get("evidence") or []:
            if isinstance(ev, dict):
                lines.append("  - [%s] “%s”" % (ev.get("locator") or "?",
                                              (ev.get("quote") or "")[:120]))
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 流水线步骤
# --------------------------------------------------------------------------- #
def step_skeleton(ledger, work_dir):
    if not ledger["runtime"].get("started_at"):
        ledger["runtime"]["started_at"] = now_iso()
    append_change(ledger, "skeleton", "先落盘一版合法 partial 骨架，后续只做增量改进",
                  "partial")
    save_ledger(work_dir, ledger)
    return ledger


def step_parse(ledger, input_dir, work_dir):
    files = discover_input(input_dir)
    case_path = find_case_file(files)
    task_path = find_task_file(files)

    case_data, d = read_json(case_path, {}) if case_path else ({}, ["no_case_json"])
    task_text, d2 = read_text(task_path) if task_path else ("", ["no_task_md"])
    ledger["degradations"] = list(ledger.get("degradations") or []) + d + d2
    if not case_path:
        ledger["errors"].append("no_case_json")
    if not task_path:
        ledger["errors"].append("no_task_md")

    case_id = extract_case_id(case_data, task_text, case_path)
    question = extract_question(case_data, task_text)

    # 尽量抽取约束与研究范围
    constraints = None
    for key in ("constraints", "constraint", "requirements", "scope", "limits"):
        if case_data.get(key) not in (None, "", []):
            constraints = case_data[key]
            break

    contract, contract_keys = extract_contract(case_data)

    ledger["case_id"] = case_id
    ledger["case"] = {
        "research_question": question,
        "constraints": constraints,
        "search_scope": None,
        "contract": contract,
        "contract_keys": contract_keys,
        "input_files": [os.path.basename(f) for f in files],
    }
    ledger["degradations"] = list(ledger.get("degradations") or [])
    append_change(ledger, "parse",
                  "解析 case.json/task.md，识别研究问题、约束与 output_contract",
                  "partial")
    save_ledger(work_dir, ledger)
    return ledger


def step_plan(ledger, work_dir, plan_payload=None):
    question = (ledger.get("case") or {}).get("research_question")
    if plan_payload and isinstance(plan_payload, dict):
        plan = dict(plan_payload)
    else:
        plan = heuristic_plan(question)
    plan.setdefault("research_question", question)
    ledger["plan"] = plan
    ledger["search_scope"] = plan.get("search_scope")
    append_change(ledger, "plan", "生成多轮查询规划与关键词扩展核验清单", "partial")
    save_ledger(work_dir, ledger)
    return ledger


def step_import(ledger, work_dir, payload, note=None):
    added, merged, added_ex = import_candidates(ledger, payload)
    refresh_sources(ledger)
    append_change(ledger, "import",
                  (note or "导入候选记录") +
                  "（新增 %d，合并 %d，排除 %d）" % (added, merged, added_ex),
                  "partial")
    save_ledger(work_dir, ledger)
    return ledger, added, merged


def step_evidence(ledger, work_dir, entry):
    add_evidence(ledger, entry)
    refresh_sources(ledger)
    append_change(ledger, "evidence", "记录查询/来源证据", "partial")
    save_ledger(work_dir, ledger)
    return ledger


def step_note(ledger, work_dir, note):
    """补充状态说明、局限、覆盖缺口、待人工确认项与检索范围（不触碰候选）。"""
    if not isinstance(note, dict):
        return ledger
    for key in ("status_notes", "limitations", "notes"):
        val = note.get(key)
        if isinstance(val, list):
            ledger.setdefault(key, []).extend(val)
        elif val not in (None, ""):
            ledger.setdefault(key, []).append(val)
    for key in ("coverage_gaps",):
        val = note.get(key)
        if isinstance(val, list):
            ledger["uncertainty"].setdefault("coverage_gaps", []).extend(val)
        elif val not in (None, ""):
            ledger["uncertainty"].setdefault("coverage_gaps", []).append(val)
    for key in ("pending_human",):
        val = note.get(key)
        if isinstance(val, list):
            ledger.setdefault(key, []).extend(val)
        elif val not in (None, ""):
            ledger.setdefault(key, []).append(val)
    u_notes = note.get("uncertainty_notes")
    if isinstance(u_notes, list):
        ledger["uncertainty"].setdefault("notes", []).extend(u_notes)
    elif u_notes not in (None, ""):
        ledger["uncertainty"].setdefault("notes", []).append(u_notes)
    if note.get("search_scope") is not None:
        ledger["search_scope"] = note["search_scope"]
    append_change(ledger, "note", note.get("reason") or "补充状态/局限/遗漏说明",
                  "partial")
    save_ledger(work_dir, ledger)
    return ledger


def step_assemble(ledger, work_dir, output_dir):
    # assemble 是纯派生：在账本深拷贝上工作，不追加 change_log、不回写账本，
    # 因此同一账本反复 assemble 得到完全一致的 result.json。
    local = json.loads(json.dumps(ledger))
    refresh_sources(local)
    result = build_result(local)
    ensure_dir(output_dir)
    write_json(os.path.join(output_dir, "result.json"), result)
    write_text(os.path.join(output_dir, "candidates.md"), render_candidates_md(result))
    return result


# --------------------------------------------------------------------------- #
# 自检
# --------------------------------------------------------------------------- #
REQUIRED_TOP = ("case_id", "status", "results", "evidence", "sources",
                "uncertainty", "change_log")


def selfcheck(output_dir, return_checks=False):
    res_path = os.path.join(output_dir, "result.json")
    result, deg = read_json(res_path, None)
    checks = []
    if result is None:
        checks.append(("result.json 存在且合法", False, str(deg)))
        if return_checks:
            return checks, 1
        _print_checks(checks)
        return 1
    if not isinstance(result, dict):
        checks.append(("result.json 是对象", False, "非 dict"))
        if return_checks:
            return checks, 1
        _print_checks(checks)
        return 1

    for key in REQUIRED_TOP:
        checks.append(("顶层字段 %s 存在" % key, key in result,
                       "缺失" if key not in result else ""))
    status = result.get("status")
    checks.append(("status 取值合法", status in
                   ("ok", "partial", "insufficient_evidence", "input_error"),
                   str(status)))
    results = result.get("results")
    checks.append(("results 是列表", isinstance(results, list), str(type(results))))
    if isinstance(results, list):
        each_ok = all(isinstance(r, dict) and "title" in r and
                      "score" in r for r in results)
        checks.append(("每条结果含 title 与 score", each_ok, ""))
    checks.append(("uncertainty 是对象", isinstance(result.get("uncertainty"), dict),
                   str(type(result.get("uncertainty")))))
    checks.append(("change_log 是列表", isinstance(result.get("change_log"), list),
                   str(type(result.get("change_log")))))
    passed = sum(1 for _, ok, _ in checks if ok)
    if return_checks:
        return checks, (0 if passed == len(checks) else 1)
    _print_checks(checks)
    return 0 if passed == len(checks) else 1


def _print_checks(checks):
    for name, ok, detail in checks:
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name,
                             ("  " + detail) if detail else ""))
    total = len(checks)
    passed = sum(1 for _, ok, _ in checks if ok)
    print("selfcheck: %d/%d PASS" % (passed, total))