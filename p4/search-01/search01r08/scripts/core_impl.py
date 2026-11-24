#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学术文献检索 Skill 的核心实现（纯标准库，无第三方依赖）。

职责：稳健 IO、输入发现与解析、问题拆解与查询规划、候选去重与导入、
证据账本、确定性打分排序、result.json 组装与自检。
本文件不直接执行；由同目录 main.py 与 selftest.py 调用。
"""
import json
import os
import re
import sys
import time

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2 * 1024 * 1024
SKILL_NAME = "academic-literature-search"
SKILL_VERSION = "2.0.0"

CONTRACT_KEY_NAMES = (
    "output_contract", "outputContract", "output", "result_schema", "schema",
    "required_output", "required_fields", "expected_fields", "contract",
)

# 打分权重：相关性 / 元数据完整度 / 来源可靠度，三者之和为 1。
W_RELEVANCE = 0.5
W_METADATA = 0.25
W_SOURCE = 0.25


# ---------------------------------------------------------------------------
# 基础 IO（容错、可降级、确定性）
# ---------------------------------------------------------------------------
def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)


def _encode_now(timestamp=None):
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(timestamp))


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """读取文本，返回 (text, degradations)。"""
    if not path or not os.path.isfile(path):
        return "", ["missing:" + str(path)]
    try:
        size = os.path.getsize(path)
    except OSError:
        return "", ["stat_failed:" + str(path)]
    if size > max_bytes:
        return "", ["size_exceeded:%s:%d" % (path, size)]
    try:
        data = open(path, "rb").read()
    except OSError as e:
        return "", ["read_failed:%s:%s" % (path, str(e)[:80])]
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
    except Exception as e:
        return default, ["bad_json:%s:%s" % (path, str(e)[:120])]


def write_text(path, text):
    ensure_dir(os.path.dirname(path) or ".")
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
        return True
    except OSError as e:
        print("write_text failed:", path, str(e)[:200], file=sys.stderr)
        return False


def write_json(path, obj):
    ensure_dir(os.path.dirname(path) or ".")
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, path)
        return True
    except OSError as e:
        print("write_json failed:", path, str(e)[:200], file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# 状态与变更日志
# ---------------------------------------------------------------------------
def get_state(work_dir):
    path = os.path.join(work_dir, "state.json")
    state, deg = read_json(path, {})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("started_at", _encode_now())
    state.setdefault("phases", [])
    state.setdefault("request_count", 0)
    for d in deg:
        state.setdefault("degradations", [])
        if d not in state["degradations"]:
            state["degradations"].append(d)
    return state


def save_state(work_dir, state):
    ensure_dir(work_dir)
    return write_json(os.path.join(work_dir, "state.json"), state)


def log_change(work_dir, action, reason, status="partial"):
    path = os.path.join(work_dir, "change_log.json")
    entries, _ = read_json(path, [])
    if not isinstance(entries, list):
        entries = []
    entries.append({
        "time": _encode_now(),
        "action": action,
        "reason": reason,
        "status": status,
    })
    write_json(path, entries)


def phase(work_dir, name, note=""):
    st = get_state(work_dir)
    st["phases"].append({"phase": name, "at": _encode_now(), "note": note})
    save_state(work_dir, st)


# ---------------------------------------------------------------------------
# 输入发现与解析
# ---------------------------------------------------------------------------
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


def _first_str(d, keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def parse_case(input_dir, work_dir):
    files = discover_input(input_dir)
    case_path = find_case_file(files)
    task_path = find_task_file(files)
    degradations = []

    case_data, d = read_json(case_path, {}) if case_path else ({}, ["no_case_json"])
    degradations += d
    task_text, d = read_text(task_path) if task_path else ("", ["no_task_md"])
    degradations += d

    # case_id
    case_id = _first_str(case_data, ("case_id", "caseId", "id", "challenge_id",
                                     "challengeId", "armiId"))
    if not case_id:
        m = re.search(r"(?:case_id|challenge_id)\s*[:：]\s*`?([A-Za-z0-9_\-]+)", task_text)
        if m:
            case_id = m.group(1)
    if not case_id:
        case_id = ("case:" + os.path.splitext(os.path.basename(case_path))[0]) if case_path else "unknown"

    # research_question
    research_question = _first_str(case_data, (
        "research_question", "researchQuestion", "question", "query", "topic",
        "task", "problem", "research_topic", "research_problem", "title"))
    if not research_question:
        m = re.search(r"(?:研究问题|科研问题|研究主题|Research question|Research topic)\s*[:：]\s*(.+)",
                      task_text, flags=re.I)
        if m:
            research_question = m.group(1).strip()[:2000]
    if not research_question:
        lines = [ln.strip() for ln in task_text.splitlines() if ln.strip()]
        research_question = (lines[0][:2000] if lines else None)

    # output_contract 回填
    contract = None
    for k in CONTRACT_KEY_NAMES:
        v = case_data.get(k)
        if isinstance(v, dict) and v:
            contract = dict(v)
            break
        if isinstance(v, list) and v:
            contract = dict.fromkeys(map(str, v), None)
            break
    if contract is None and isinstance(case_data.get("output_contract"), dict) is False:
        # 无显式契约时视为空契约，便于统一回填逻辑
        contract = {}

    summary = {
        "case_id": case_id,
        "research_question": research_question,
        "output_contract": contract,
        "input_files": [os.path.relpath(f, input_dir) if input_dir and os.path.isdir(input_dir)
                        else f for f in files],
        "case_path": case_path,
        "task_path": task_path,
        "degradations": sorted(set(degradations)),
    }
    write_json(os.path.join(work_dir, "case_summary.json"), summary)
    return summary


# ---------------------------------------------------------------------------
# 查询规划
# ---------------------------------------------------------------------------
def init_plan(summary, work_dir):
    plan = {
        "research_question": summary.get("research_question"),
        "case_id": summary.get("case_id"),
        "aspects": [],
        "queries": [],
        "sort": {"primary": "score", "tiebreak": ["year", "title"]},
        "notes": (
            "aspects 为研究问题的拆解角度（id/label/description）；queries 为计划查询"
            "（id/text/source/database/aspect_id/rationale/status）。请用 plan-update 或手改本文件。"
        ),
    }
    write_json(os.path.join(work_dir, "plan.json"), plan)
    return plan


def read_plan(work_dir):
    plan, _ = read_json(os.path.join(work_dir, "plan.json"), {})
    if not isinstance(plan, dict):
        plan = {}
    plan.setdefault("aspects", [])
    plan.setdefault("queries", [])
    return plan


def update_plan_inline(payload, work_dir):
    """增量更新 plan.json：提供 aspects/queries/research_question/sort 键即覆盖。"""
    plan = read_plan(work_dir)
    errors = []
    if not isinstance(payload, dict):
        errors.append("plan-update payload must be a JSON object")
    else:
        for k in ("aspects", "queries"):
            if k in payload:
                if isinstance(payload[k], list):
                    plan[k] = payload[k]
                else:
                    errors.append("%s must be a list" % k)
        for k in ("research_question", "case_id"):
            if k in payload and payload[k] not in (None, ""):
                plan[k] = str(payload[k])
        if "sort" in payload and isinstance(payload["sort"], dict):
            plan["sort"] = payload["sort"]
    write_json(os.path.join(work_dir, "plan.json"), plan)
    return plan, errors


# ---------------------------------------------------------------------------
# 规范化与去重
# ---------------------------------------------------------------------------
def norm_text(s):
    if not isinstance(s, str):
        return ""
    return re.sub(r"[^\w]+", "", s.lower())


def norm_doi(s):
    if not isinstance(s, str):
        return ""
    return s.strip().lower()


def first_author_token(authors):
    if isinstance(authors, list) and authors:
        a = authors[0]
    elif isinstance(authors, str) and authors.strip():
        a = authors.split(";")[0].split(",")[0].strip()
    else:
        return ""
    return norm_text(str(a))


def dedup_key(rec):
    doi = norm_doi(rec.get("doi") or "")
    if doi:
        return "doi:" + doi
    title = norm_text(rec.get("title") or "")
    author = first_author_token(rec.get("authors"))
    year = _first_year(rec.get("year"))
    return "t:%s|a:%s|y:%s" % (title, author, year)


def _first_year(v):
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v))
    if isinstance(v, str):
        m = re.search(r"(18|19|20|21)\d{2}", v)
        return m.group(0) if m else ""
    return ""


def _author_nonempty(rec):
    a = rec.get("authors")
    if isinstance(a, list):
        return any(str(x).strip() for x in a)
    return isinstance(a, str) and bool(a.strip())


def _field_nonempty(rec, field):
    v = rec.get(field)
    if field == "authors":
        return _author_nonempty(rec)
    if field == "year":
        return bool(_first_year(v))
    return isinstance(v, str) and bool(v.strip())


def metadata_completeness(rec):
    fields = ("title", "authors", "year", "journal", "doi")
    present = sum(1 for f in fields if _field_nonempty(rec, f))
    return present / len(fields)


def source_reliability(rec):
    doi = norm_doi(rec.get("doi") or "")
    url = str(rec.get("url") or "").strip()
    db = str(rec.get("database") or "").strip()
    if doi:
        return 1.0
    if url and db:
        return 0.8
    if url:
        return 0.6
    if db and _field_nonempty(rec, "title"):
        return 0.5
    return 0.3


def clamp01(x, default=0.5):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    if v < 0:
        return 0.0
    if v > 1:
        return 1.0
    return v


def compute_scores(rec):
    relevance = clamp01(rec.get("relevance", rec.get("relevance_score")),
                        default=0.5 if rec.get("relevance_reason") else 0.0)
    meta = metadata_completeness(rec)
    src = source_reliability(rec)
    score = round(W_RELEVANCE * relevance + W_METADATA * meta + W_SOURCE * src, 4)
    rec["relevance"] = round(relevance, 4)
    rec["score_components"] = {
        "relevance": round(relevance, 4),
        "metadata": round(meta, 4),
        "source": round(src, 4),
        "weights": {"relevance": W_RELEVANCE, "metadata": W_METADATA, "source": W_SOURCE},
    }
    rec["score"] = score
    return rec


def _first_author_display(authors):
    if isinstance(authors, list):
        return ", ".join(str(x) for x in authors)
    return str(authors) if authors else None


def merge_record(existing, incoming):
    """优先保留字段更完整的记录，合并来源/证据/查询 track。"""
    for f in ("title", "authors", "year", "journal", "doi", "url", "database",
              "relevance", "relevance_reason", "relevance_score"):
        if not _field_nonempty(existing, f) and _field_nonempty(incoming, f):
            existing[f] = incoming[f]
    if _field_nonempty(incoming, "authors") and not _author_nonempty(existing):
        existing["authors"] = incoming["authors"]

    merged = {}
    for key in ("query_ids", "sources", "evidence", "aspects"):
        vals = []
        for rec in (existing, incoming):
            v = rec.get(key)
            if isinstance(v, list):
                vals.extend(v)
            elif v not in (None, ""):
                vals.append(v)
        # 去重保序
        seen = set()
        out = []
        for v in vals:
            s = json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
            if s not in seen:
                seen.add(s)
                out.append(v)
        merged[key] = out
    for k, v in merged.items():
        if v:
            existing[k] = v
    return existing


def load_records(work_dir):
    path = os.path.join(work_dir, "records.json")
    if not os.path.isfile(path):
        return [], []
    records, deg = read_json(path, [])
    return records if isinstance(records, list) else [], deg


def save_records(work_dir, records):
    records.sort(key=lambda r: dedup_key(r))
    return write_json(os.path.join(work_dir, "records.json"), records)


def import_candidates(cand_file, work_dir):
    data, deg = read_json(cand_file, None)
    if data is None:
        return {"imported": 0, "errors": deg or ["unreadable:" + str(cand_file)]}
    items = data.get("records", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return {"imported": 0, "errors": ["candidates file must be a list or {records:[...]}"], "degradations": deg}
    records, _ = load_records(work_dir)
    index = {}
    for r in records:
        index[dedup_key(r)] = r
    imported = 0
    errors = []
    for item in items:
        if not isinstance(item, dict):
            errors.append("non-object entry skipped")
            continue
        key = dedup_key(item)
        item.setdefault("query_ids", [])
        item.setdefault("aspects", [])
        item.setdefault("sources", [])
        item.setdefault("evidence", [])
        if key in index:
            index[key] = merge_record(index[key], item)
        else:
            index[key] = item
        imported += 1
    save_records(work_dir, list(index.values()))
    return {"imported": imported, "total_after_dedup": len(index), "errors": errors,
            "degradations": deg}


# ---------------------------------------------------------------------------
# 证据账本
# ---------------------------------------------------------------------------
def _next_evidence_id(existing):
    used = set()
    for e in existing:
        if isinstance(e, dict) and e.get("id"):
            used.add(str(e["id"]))
    i = 1
    while ("q%d" % i) in used:
        i += 1
    return "q%d" % i


def add_evidence(entry, work_dir):
    path = os.path.join(work_dir, "evidence.json")
    entries, _ = read_json(path, [])
    if not isinstance(entries, list):
        entries = []
    if not isinstance(entry, dict):
        return {"added": 0, "errors": ["evidence entry must be a JSON object"]}
    entry.setdefault("timestamp", _encode_now())
    eid = entry.get("id")
    if not eid:
        entry["id"] = _next_evidence_id(entries)
    entry["kind"] = entry.get("kind", "query")
    entry.setdefault("result_ids", [])
    entry.setdefault("hits_found", None)
    entry.setdefault("hits_kept", None)
    # 同 id 覆盖（便于修正），否则追加
    replaced = False
    for i, e in enumerate(entries):
        if e.get("id") == entry["id"]:
            entries[i] = entry
            replaced = True
            break
    if not replaced:
        entries.append(entry)
    write_json(path, entries)
    return {"added": 0 if replaced else 1, "replaced": replaced, "id": entry["id"]}


def import_evidence(ev_file, work_dir):
    data, deg = read_json(ev_file, None)
    if data is None:
        return {"added": 0, "errors": deg or ["unreadable:" + str(ev_file)]}
    items = data.get("evidence", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return {"added": 0, "errors": ["evidence file must be a list or {evidence:[...]}"], "degradations": deg}
    added = 0
    errors = []
    for it in items:
        r = add_evidence(it, work_dir)
        added += r.get("added", 0)
        if r.get("errors"):
            errors.extend(r["errors"])
    return {"added": added, "errors": errors, "degradations": deg}


def load_evidence(work_dir):
    path = os.path.join(work_dir, "evidence.json")
    if not os.path.isfile(path):
        return [], []
    entries, deg = read_json(path, [])
    return (entries if isinstance(entries, list) else []), deg


# ---------------------------------------------------------------------------
# 覆盖矩阵与组装
# ---------------------------------------------------------------------------
def _list_str(v):
    if isinstance(v, list):
        return [str(x) for x in v]
    if v in (None, ""):
        return []
    return [str(v)]


def build_coverage_matrix(plan, evidence, records):
    aspects = plan.get("aspects") or []
    queries = plan.get("queries") or []
    ev_by_id = {}
    for e in evidence:
        if isinstance(e, dict) and e.get("id") is not None:
            ev_by_id[str(e["id"])] = e

    query_by_id = {}
    for q in queries:
        if isinstance(q, dict) and q.get("id") is not None:
            query_by_id[str(q["id"])] = q

    # 记录可归属到哪些 aspect
    record_aspects = {}
    for r in records:
        seen = set(_list_str(r.get("aspects")))
        for qid in _list_str(r.get("query_ids")):
            q = query_by_id.get(qid)
            if q and q.get("aspect_id") is not None:
                seen.add(str(q["aspect_id"]))
        record_aspects[json.dumps(r, ensure_ascii=False, sort_keys=True, default=str)] = seen

    matrix = []
    for aspect in aspects:
        aid = str(aspect.get("id") or aspect.get("label") or "")
        planned_q = [q for q in queries if q.get("aspect_id") is not None
                     and str(q.get("aspect_id")) == aid]
        executed_q = [q for q in planned_q if str(q.get("id")) in ev_by_id]
        hit_count = 0
        hit_ids = []
        for r in records:
            key = json.dumps(r, ensure_ascii=False, sort_keys=True, default=str)
            if aid in record_aspects.get(key, set()):
                hit_count += 1
                rid = r.get("doi") or r.get("title")
                if rid:
                    hit_ids.append(str(rid))
        if hit_count > 0:
            status = "covered"
        elif executed_q:
            status = "queried_no_hit"
        elif planned_q:
            status = "planned_not_executed"
        else:
            status = "gap"
        matrix.append({
            "aspect_id": aid,
            "label": aspect.get("label"),
            "planned_queries": len(planned_q),
            "executed_queries": len(executed_q),
            "hits": hit_count,
            "status": status,
            "hit_ids": hit_ids,
        })

    gaps = [
        {
            "aspect_id": m["aspect_id"],
            "label": m["label"],
            "suggested_next": "按该角度补一轮查询并记录证据（source/database/quote）。",
        }
        for m in matrix if m["status"] in ("gap", "planned_not_executed")
    ]
    return matrix, gaps


def sort_records(records, plan):
    sort_cfg = plan.get("sort") if isinstance(plan, dict) else None
    tiebreak = (sort_cfg or {}).get("tiebreak") or ["year", "title"]

    def key(r):
        score = r.get("score")
        score = score if isinstance(score, (int, float)) else 0.0
        year = 0
        try:
            year = int(_first_year(r.get("year")) or 0)
        except (TypeError, ValueError):
            year = 0
        title = norm_text(r.get("title") or "")
        parts = [-score]
        for tb in tiebreak:
            if tb == "year":
                parts.append(-year)
            elif tb == "title":
                parts.append(title)
        return tuple(parts)

    return sorted(records, key=key)


def assemble(input_dir, output_dir, work_dir):
    ensure_dir(output_dir)
    ensure_dir(work_dir)
    summary = parse_case(input_dir, work_dir)  # 重新解析以获得最新契约与问题
    plan = read_plan(work_dir)
    records, rdeg = load_records(work_dir)
    evidence, edeg = load_evidence(work_dir)

    for r in records:
        normalize_record(r)
    matrix, gaps = build_coverage_matrix(plan, evidence, records)

    # 打分 + 排序 + 排名
    ranked = []
    for r in records:
        r = compute_scores(dict(r))
        ranked.append(r)
    ranked = sort_records(ranked, plan)
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
        for e in r.get("evidence", []):
            if isinstance(e, dict):
                e.setdefault("locator", None)
                e.setdefault("quote", "not_reported")

    # 状态判断
    input_degs = summary.get("degradations") or []

    def _is_input_error(d):
        return any(d.startswith(p) for p in
                   ("bad_json", "size_exceeded", "read_failed", "stat_failed"))

    input_errors = [d for d in input_degs if _is_input_error(d)]
    work_errors = [d for d in (rdeg + edeg) if not d.startswith(("missing:", "empty:"))]

    if input_errors:
        status = "input_error"
    elif not ranked:
        status = "insufficient_evidence"
    elif gaps:
        status = "partial"
    else:
        status = "ok"

    errors = sorted(set(input_errors + work_errors))
    degradations = sorted(set(input_degs + work_errors))

    change_log, _ = read_json(os.path.join(work_dir, "change_log.json"), [])
    state = get_state(work_dir)
    started = state.get("started_at")
    runtime_info = {
        "started_at": started,
        "finished_at": _encode_now(),
        "phases": state.get("phases", []),
        "model_requests_budget": 64,
        "time_budget_seconds": 900,
    }

    # 契约回填：先取契约键，再叠加标准字段，绝不丢键
    contract = summary.get("output_contract") or {}
    if not isinstance(contract, dict):
        contract = {}
    result = {k: (None if not isinstance(v, (dict, list)) else v) for k, v in contract.items()}
    standard = {
        "case_id": summary.get("case_id"),
        "status": status,
        "research_question": summary.get("research_question"),
        "search_scope": summary.get("search_scope") or {
            "sources": ["paper_search", "web_search", "web_fetch"],
            "note": "开放学术数据库；单库命中不足时跨库补召回。",
        },
        "results": ranked,
        "evidence": evidence,
        "coverage_matrix": matrix,
        "coverage_gaps": gaps,
        "sources": build_sources(evidence, ranked),
        "uncertainty": {
            "missing_metadata": missing_metadata(ranked),
            "conflicts": list_conflicts(ranked),
            "coverage_gaps": [g["aspect_id"] for g in gaps],
            "notes": [],
        },
        "pending_human": pending_human(ranked, evidence),
        "change_log": change_log if isinstance(change_log, list) else [],
        "limitations": limitations(gaps, rdeg, edeg),
        "errors": errors,
        "degradations": degradations,
        "runtime": runtime_info,
        "meta": {
            "skill": SKILL_NAME,
            "version": SKILL_VERSION,
            "scoring_weights": {"relevance": W_RELEVANCE, "metadata": W_METADATA,
                                "source": W_SOURCE},
        },
    }
    result.update(standard)
    # 契约键若与我们标准键同名，以标准值为准补回（不影响契约键存在性）
    for k in standard:
        result[k] = standard[k]

    status_notes = []
    if not ranked:
        status_notes.append("无候选记录；请按 coverage_gaps 补检索，未补造文献。")
    if gaps:
        status_notes.append("覆盖缺口: " + ", ".join(g["aspect_id"] for g in gaps[:10]))
    if rdeg or edeg:
        status_notes.append("存在本地读写降级: " + "; ".join((rdeg + edeg)[:10]))
    result["status_notes"] = status_notes

    write_json(os.path.join(output_dir, "result.json"), result)
    candidates_md = render_candidates(result)
    write_text(os.path.join(output_dir, "candidates.md"), candidates_md)
    return result


def normalize_record(rec):
    if rec.get("authors") is None and _author_nonempty(rec):
        pass
    if isinstance(rec.get("authors"), str):
        parts = [p.strip() for p in rec["authors"].replace(";", ",").split(",") if p.strip()]
        rec["authors"] = parts or [rec["authors"]]
    if rec.get("year") is not None:
        y = _first_year(rec.get("year"))
        if y:
            try:
                rec["year"] = int(y)
            except (TypeError, ValueError):
                pass
    ev = rec.get("evidence")
    if isinstance(ev, list):
        norm = []
        for e in ev:
            if isinstance(e, str):
                norm.append({"quote": e, "locator": None})
            else:
                norm.append(e)
        rec["evidence"] = norm
    elif isinstance(ev, str):
        rec["evidence"] = [{"quote": ev, "locator": None}]
    return rec


def build_sources(evidence, records):
    sources = {}
    for r in records:
        db = str(r.get("database") or "unknown")
        url = str(r.get("url") or "").strip()
        doi = norm_doi(r.get("doi") or "")
        if db not in sources:
            sources[db] = {"database": db, "count": 0, "urls": [], "dois": []}
        sources[db]["count"] += 1
        if url and url not in sources[db]["urls"]:
            sources[db]["urls"].append(url)
        if doi and doi not in sources[db]["dois"]:
            sources[db]["dois"].append(doi)
    for e in evidence:
        db = str(e.get("database") or "")
        if db:
            sources.setdefault(db, {"database": db, "count": 0, "urls": [], "dois": []})
    return sorted(sources.values(), key=lambda s: s["database"])


def missing_metadata(records):
    fields = ("title", "authors", "year", "journal", "doi")
    missing = []
    for r in records:
        for f in fields:
            if not _field_nonempty(r, f):
                missing.append({"field": f, "result": r.get("doi") or r.get("title") or None})
    # 汇总缩略
    by_field = {}
    for m in missing:
        by_field.setdefault(m["field"], 0)
        by_field[m["field"]] += 1
    return {"total_missing": len(missing), "by_field": by_field}


def list_conflicts(records):
    seen = {}
    conflicts = []
    for r in records:
        doi = norm_doi(r.get("doi") or "")
        if doi:
            if doi in seen and seen[doi] != (r.get("title"), r.get("year")):
                conflicts.append({"doi": doi, "a": seen[doi],
                                  "b": (r.get("title"), r.get("year"))})
            seen[doi] = (r.get("title"), r.get("year"))
    return conflicts[:20]


def pending_human(records, evidence):
    pend = []
    if not records:
        pend.append("检查是否遗漏主题词/同义词/缩写后的补检索。")
    for r in records:
        if not _field_nonempty(r, "doi") or not _field_nonempty(r, "year"):
            pend.append(r.get("title") or "(无题)")
    if not evidence:
        pend.append("无证据记录，需补 query/source/rationale 账本。")
    return pend[:20]


def limitations(gaps, rdeg, edeg):
    lim = []
    if gaps:
        lim.append("覆盖矩阵存在未覆盖/未执行角度: " + ", ".join(g["aspect_id"] for g in gaps[:10]))
    if rdeg or edeg:
        lim.append("本地工作区读写存在降级，见 errors/degradations。")
    return lim


def render_candidates(result):
    lines = ["# 候选文献清单（可编辑，供人工复核）", "",
             "> 由 result.json 渲染；此文件非正式答案，仅便于核验。", ""]
    if not result.get("results"):
        lines += ["（无候选记录）", ""]
    for r in result.get("results", []):
        title = r.get("title") or "(无标题)"
        year = r.get("year") or "—"
        score = r.get("score")
        if isinstance(score, (int, float)):
            score = round(score, 4)
        lines += ["## %d. %s (%s)  score=%s" % (r.get("rank"), title, year, score)]
        authors = _first_author_display(r.get("authors"))
        if authors:
            lines += ["- 作者: %s" % authors]
        if r.get("journal"):
            lines += ["- 期刊: %s" % r.get("journal")]
        if r.get("doi"):
            lines += ["- DOI: %s" % r.get("doi")]
        if r.get("url"):
            lines += ["- URL: %s" % r.get("url")]
        if r.get("relevance_reason"):
            lines += ["- 相关性: %s" % r.get("relevance_reason")]
        for e in r.get("evidence") or []:
            if isinstance(e, dict):
                loc = e.get("locator")
                quote = e.get("quote")
                if loc or quote:
                    lines += ["- 依据: %s → %s" % (loc, (quote or "")[:160])]
        lines += [""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
def selfcheck(input_dir, output_dir, work_dir):
    path = os.path.join(output_dir, "result.json")
    result, deg = read_json(path, None)
    checks = []
    ok = lambda name, cond, detail="": checks.append({"check": name, "pass": bool(cond),
                                                      "detail": detail})

    ok("result.json 存在且可解析", result is not None, "; ".join(deg) if deg else "")
    if result is None:
        return _render_selfcheck(checks)

    ok("case_id 非空", bool(result.get("case_id")), str(result.get("case_id")))
    ok("status 合法", result.get("status") in
       ("ok", "partial", "insufficient_evidence", "input_error"), str(result.get("status")))
    ok("results 为列表", isinstance(result.get("results"), list))
    ok("evidence 为列表", isinstance(result.get("evidence"), list))
    ok("sources 非空或合法", isinstance(result.get("sources"), list))
    ok("uncertainty 存在", isinstance(result.get("uncertainty"), dict))
    ok("change_log 存在", isinstance(result.get("change_log"), list))
    ok("pending_human 存在", isinstance(result.get("pending_human"), list))

    if isinstance(result.get("results"), list):
        results = result["results"]
        all_id = all(("rank" in r and "title" in r) for r in results)
        ok("每条结果含 rank/title", all_id)
        has_score = all(isinstance(r.get("score"), (int, float)) for r in results)
        ok("每条结果含 score", has_score)
        has_ev = all(r.get("evidence") for r in results)
        ok("每条结果含证据", has_ev)
        has_source = all((r.get("doi") or r.get("url")) for r in results)
        ok("每条结果含 doi/url 来源", has_source)

    # 契约回填检查
    if input_dir and os.path.isdir(input_dir):
        summary = parse_case(input_dir, work_dir)
        contract = summary.get("output_contract") or {}
        if isinstance(contract, dict):
            missing_keys = [k for k in contract if k not in result]
            ok("output_contract 全部键已回填", not missing_keys,
               "漏键: " + ", ".join(missing_keys) if missing_keys else "")

    return _render_selfcheck(checks)


def _render_selfcheck(checks):
    passed = sum(1 for c in checks if c["pass"])
    for c in checks:
        mark = "PASS" if c["pass"] else "FAIL"
        detail = ("  " + c["detail"][:120]) if c["detail"] else ""
        print("[%s] %s%s" % (mark, c["check"], detail))
    print("selfcheck: %d/%d passed" % (passed, len(checks)))
    return {"passed": passed, "total": len(checks),
            "all_pass": passed == len(checks), "checks": checks}


# ---------------------------------------------------------------------------
# 骨架流水线（前 3 分钟落盘 partial）
# ---------------------------------------------------------------------------
def run_pipeline(input_dir, output_dir, work_dir):
    ensure_dir(work_dir)
    ensure_dir(output_dir)
    log_change(work_dir, "run", "pipeline 启动，先落盘骨架", "partial")
    phase(work_dir, "skeleton", "写入部分结果骨架")
    summary = parse_case(input_dir, work_dir)
    init_plan(summary, work_dir)
    # 立即产生一版合法 partial 结果（即使无候选）
    result = assemble(input_dir, output_dir, work_dir)
    log_change(work_dir, "run", "骨架完成，status=%s" % result.get("status"), result.get("status"))
    phase(work_dir, "skeleton_done", "status=" + result.get("status"))
    return result