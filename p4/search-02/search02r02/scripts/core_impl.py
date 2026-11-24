"""core_impl.py — deterministic implementation for the research-question-driven-search skill.

Design intent
-------------
The script is a *deterministic assistant*: it discovers and parses the fixed input
snapshot, assembles whatever the agent decides, and enforces the output contract.
It never judges relevance itself and never invents literature, metadata, scores or
sources. Every mutable decision (decomposition, query plan, term expansion, record
screening, evidence log, stop decision) is supplied by the agent through `--file`
inputs and merged into a single work state at /app/work/state.json.

Determinism: timestamps are taken from the *source file mtime* of the fixed snapshot,
not from the wall clock, so the same input produces the same result on repeated runs.
Only standard library modules are used.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

MAX_FILE_BYTES = 2_000_000
STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
CONTRACT_KEYS = ("output_contract", "output", "schema", "required_fields", "contract", "result_schema")

SCORING_DOC = "score = 0.5*relevance + 0.3*metadata_completeness + 0.2*source_reliability"


# --------------------------------------------------------------------------- helpers

def _mtime_iso(path):
    try:
        st = os.path.getmtime(path)
        return datetime.fromtimestamp(st, tz=timezone.utc).isoformat()
    except Exception:
        return "unknown"


def read_text(path):
    """Best-effort text read: utf-8-sig -> utf-8 -> latin-1."""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except UnicodeDecodeError:
            continue
        except Exception:
            return None
    return None


def load_json(path):
    text = read_text(path)
    if text is None:
        return None, "unreadable"
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def _is_list_like(x):
    return isinstance(x, (list, tuple))


# --------------------------------------------------------------------------- input discovery

def discover_input(inp):
    summary = {
        "input_dir": inp,
        "exists": False,
        "files": [],
        "skipped": [],
        "case_json": None,
        "task_md": None,
        "materials": [],
    }
    if not inp or not os.path.isdir(inp):
        return summary
    summary["exists"] = True
    json_candidates = []
    md_candidates = []
    for root, _dirs, names in os.walk(inp):
        for name in sorted(names):
            path = os.path.join(root, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                summary["skipped"].append(
                    {"path": path, "reason": "size>2MB", "bytes": size}
                )
                continue
            entry = {"path": path, "name": name, "bytes": size, "mtime": _mtime_iso(path)}
            summary["files"].append(entry)
            lower = name.lower()
            if lower.endswith(".json"):
                json_candidates.append(path)
            elif lower.endswith(".md") or lower.endswith(".markdown"):
                md_candidates.append(path)
            else:
                summary["materials"].append(entry)
    # case.json first by exact basename, else first json; same for task.md
    case = next((p for p in json_candidates if os.path.basename(p).lower() == "case.json"), None)
    summary["case_json"] = case or (json_candidates[0] if json_candidates else None)
    task = next((p for p in md_candidates if os.path.basename(p).lower() == "task.md"), None)
    summary["task_md"] = task or (md_candidates[0] if md_candidates else None)
    # materials should not include the case/task files themselves
    summary["materials"] = [
        e for e in summary["materials"]
        if e["path"] not in (summary["case_json"], summary["task_md"])
    ]
    return summary


def extract_case_id(case_obj, task_text):
    if isinstance(case_obj, dict):
        for key in ("case_id", "id", "challenge_id", "task_id"):
            val = case_obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, (int, float)):
                return str(val)
    if task_text:
        m = re.search(r"(?:case_id|challenge[-_ ]?id)\s*[:：]\s*`([^`]+)`", task_text, re.I)
        if m:
            return m.group(1).strip()
        m = re.search(r"challenge_id:\s*([^\s]+)", task_text)
        if m:
            return m.group(1).strip()
    return "not_reported"


def extract_research_question(case_obj, task_text):
    if isinstance(case_obj, dict):
        for key in ("research_question", "question", "task", "instruction", "prompt", "query", "title"):
            val = case_obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    if task_text:
        m = re.search(r"#+\s*(?:科研问题|研究问题|Research Question)\s*\n+(.+?)(?=\n#|\n##|\Z)", task_text, re.S)
        if m:
            return m.group(1).strip()
    return None


def find_contract(case_obj):
    if isinstance(case_obj, dict):
        for key in CONTRACT_KEYS:
            if key in case_obj and isinstance(case_obj[key], dict):
                return key, case_obj[key]
    return None, None


# --------------------------------------------------------------------------- skeleton

def default_skeleton():
    return {
        "case_id": "not_reported",
        "status": "partial",
        "research_question": None,
        "sub_questions": [],
        "query_plan": [],
        "term_expansion": [],
        "stop_decision": {
            "status": "pending",
            "reason": None,
            "coverage": {},
            "marginal_gain": None,
        },
        "results": [],
        "screening": {
            "total_records": 0,
            "included": [],
            "excluded": [],
            "uncertain": [],
            "merged_duplicates": [],
        },
        "evidence": [],
        "sources": [],
        "uncertainty": {
            "missing_metadata": [],
            "conflicts": [],
            "coverage_gaps": [],
            "notes": [],
        },
        "change_log": [],
        "status_notes": None,
        "limitations": [],
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "runtime": {
            "budget_seconds": 900,
            "budget_requests": 64,
            "elapsed_seconds": None,
            "model_requests": None,
        },
        "scoring": SCORING_DOC,
        "output_contract": {},
    }


def default_state():
    return {
        "case_summary": {},
        "sub_questions": [],
        "query_plan": [],
        "term_expansion": [],
        "stop_decision": default_skeleton()["stop_decision"],
        "records": [],
        "evidence": [],
        "change_log": [],
    }


def build_skeleton(summary, case_obj, task_text):
    result = default_skeleton()
    errors = []
    degradations = []

    if not summary["exists"] or not summary["files"]:
        result["status"] = "insufficient_evidence"
        result["status_notes"] = "输入目录不存在或为空，无可解析材料。"
        result["limitations"].append("无输入文件；未进行检索。")
        return result, errors, degradations, summary

    case_id = extract_case_id(case_obj, task_text)
    if case_id == "not_reported":
        errors.append("case_id 未在输入中找到，回填 not_reported。")
    result["case_id"] = case_id
    result["research_question"] = extract_research_question(case_obj, task_text)

    # degrade bad case JSON but keep going
    if summary["case_json"] and case_obj is None:
        degradations.append({"path": summary["case_json"], "reason": "case.json 解析失败，按空对象处理。"})
        errors.append("case.json 无法解析为 JSON，已降级为 input_error。")
        result["status"] = "input_error"
    if not summary["case_json"] and not summary["task_md"]:
        result["status"] = "insufficient_evidence"
        result["status_notes"] = "未发现 case.json 或 task.md。"

    # echo output contract verbatim + flatten its fields with null/[] defaults
    contract_key, contract = find_contract(case_obj)
    result["output_contract"] = contract if contract is not None else {}
    if contract_key and contract_key != "output_contract":
        result[contract_key] = contract
    if isinstance(contract, dict):
        for field, val in contract.items():
            if field not in result:
                result[field] = [] if _is_list_like(val) else None

    for entry in summary["skipped"]:
        degradations.append({"path": entry["path"], "reason": entry["reason"]})

    # sources from discovered files
    for entry in summary["files"]:
        kind = "case" if entry["path"] == summary["case_json"] else (
            "task" if entry["path"] == summary["task_md"] else "material")
        result["sources"].append(
            {"path": entry["path"], "kind": kind, "bytes": entry["bytes"], "mtime": entry["mtime"]}
        )
    return result, errors, degradations, summary


def _append_change(state, action, reason, status, time_stamp):
    state["change_log"].append(
        {"time": time_stamp, "action": action, "reason": reason, "status": status}
    )


# --------------------------------------------------------------------------- commands

def cmd_scaffold(args):
    inp, out, work = args["input"], args["output"], args["work"]
    ensure_dir(out)
    ensure_dir(work)

    summary = discover_input(inp)
    case_obj = None
    parse_error = None
    if summary["case_json"]:
        case_obj, parse_error = load_json(summary["case_json"])
    task_text = None
    if summary["task_md"]:
        task_text = read_text(summary["task_md"])

    result, errors, degradations, summary = build_skeleton(summary, case_obj, task_text)

    state = default_state()
    state["case_summary"] = {
        "input_dir": summary["input_dir"],
        "case_json": summary["case_json"],
        "task_md": summary["task_md"],
        "materials": [e["path"] for e in summary["materials"]],
        "file_count": len(summary["files"]),
        "skipped": summary["skipped"],
    }
    anchor = summary["case_json"] or summary["task_md"]
    anchor_mtime = _mtime_iso(anchor) if anchor else "unknown"
    _append_change(state, "scaffold", "落盘 partial 结果骨架（第 0 步，≤3 分钟）", "partial", anchor_mtime)

    if errors:
        result["errors"].extend(errors)
    if degradations:
        result["degradations"].extend(degradations)
    if parse_error and summary["case_json"]:
        result["errors"].append(f"case.json 解析错误: {parse_error}")

    write_json(os.path.join(work, "state.json"), state)
    write_json(os.path.join(work, "case_summary.json"), summary)
    write_json(os.path.join(out, "result.json"), result)
    _write_plan_md(out, result)
    _write_screening_md(out, result)

    print(f"scaffold: status={result['status']} files={len(summary['files'])} "
          f"materials={len(summary['materials'])}")
    return 0


def _load_state(work):
    path = os.path.join(work, "state.json")
    state, err = load_json(path)
    if err or not isinstance(state, dict):
        state = default_state()
    # ensure keys exist
    for key, default in default_state().items():
        state.setdefault(key, default)
    return state


def cmd_set_plan(args):
    work = args["work"]
    payload, err = load_json(args["file"])
    if err:
        print(f"set-plan: 无法解析输入文件: {err}")
        return 2
    if not isinstance(payload, dict):
        print("set-plan: 输入必须是 JSON 对象")
        return 2
    state = _load_state(work)
    for key in ("sub_questions", "query_plan", "term_expansion", "stop_decision"):
        if key in payload and payload[key] is not None:
            state[key] = payload[key]
    ts = _mtime_iso(args["file"])
    _append_change(state, "set-plan",
                   "更新拆解/查询规划/术语扩展/停止判断", "partial", ts)
    write_json(os.path.join(work, "state.json"), state)
    print(f"set-plan: sub_questions={len(state['sub_questions'])} "
          f"query_plan={len(state['query_plan'])} term_expansion={len(state['term_expansion'])}")
    return 0


def cmd_import(args):
    work = args["work"]
    payload, err = load_json(args["file"])
    if err:
        print(f"import: 无法解析输入文件: {err}")
        return 2
    records = payload if _is_list_like(payload) else payload.get("records")
    if not _is_list_like(records):
        print("import: 输入必须是记录数组或含 records 数组的对象")
        return 2
    state = _load_state(work)
    state["records"] = [r for r in records if isinstance(r, dict)]
    ts = _mtime_iso(args["file"])
    _append_change(state, "import", f"替换候选记录账本（{len(state['records'])} 条）", "partial", ts)
    write_json(os.path.join(work, "state.json"), state)
    print(f"import: records={len(state['records'])}")
    return 0


def cmd_evidence(args):
    work = args["work"]
    payload, err = load_json(args["file"])
    if err:
        print(f"evidence: 无法解析输入文件: {err}")
        return 2
    entries = payload if _is_list_like(payload) else payload.get("evidence")
    if not _is_list_like(entries):
        print("evidence: 输入必须是条目数组或含 evidence 数组的对象")
        return 2
    state = _load_state(work)
    for e in entries:
        if isinstance(e, dict):
            e = dict(e)
            e.setdefault("timestamp", _mtime_iso(args["file"]))
            state["evidence"].append(e)
    ts = _mtime_iso(args["file"])
    _append_change(state, "evidence", f"追加 {len([e for e in entries if isinstance(e, dict)])} 条证据日志", "partial", ts)
    write_json(os.path.join(work, "state.json"), state)
    print(f"evidence: total={len(state['evidence'])}")
    return 0


# --------------------------------------------------------------------------- scoring

def _relevance(r):
    v = r.get("relevance")
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return max(0.0, min(1.0, float(v)))
    return None


def _metadata_completeness(r):
    weights = {"title": 0.30, "authors": 0.20, "year": 0.20, "journal": 0.15, "doi_or_url": 0.15}
    score = 0.0
    if r.get("title"):
        score += weights["title"]
    if r.get("authors"):
        score += weights["authors"]
    if r.get("year") is not None and r.get("year") != "":
        score += weights["year"]
    if r.get("journal"):
        score += weights["journal"]
    if r.get("doi") or r.get("url"):
        score += weights["doi_or_url"]
    return round(score, 3)


def _source_reliability(r):
    doi = r.get("doi")
    url = (r.get("url") or "").lower()
    db = (r.get("database") or "").lower()
    if doi:
        return 1.0
    if db in ("paper_search", "crossref", "openalex", "pubmed", "scopus", "wos", "semantic_scholar"):
        return 0.8
    if "arxiv" in url or db == "arxiv":
        return 0.8
    if db in ("web_search", "web"):
        return 0.5
    if url:
        return 0.6
    return 0.0


def _compute_scores(records):
    scored = []
    for r in records:
        r = dict(r)
        rel = _relevance(r)
        meta = _metadata_completeness(r)
        src = _source_reliability(r)
        if rel is None:
            score = None
            r["score_breakdown"] = {"relevance": None, "metadata_completeness": meta, "source_reliability": src}
        else:
            score = round(0.5 * rel + 0.3 * meta + 0.2 * src, 4)
            r["score_breakdown"] = {"relevance": round(rel, 3), "metadata_completeness": meta, "source_reliability": src}
        r["score"] = score
        scored.append(r)
    return scored


def _rank_key(r):
    score = r.get("score")
    rel = r.get("score_breakdown", {}).get("relevance")
    meta = r.get("score_breakdown", {}).get("metadata_completeness")
    return (
        1 if score is None else 0,           # None score goes last
        -(score if score is not None else 0),
        -(rel if rel is not None else 0),
        -(meta if meta is not None else 0),
        str(r.get("title") or "").lower(),
    )


def _screening_bucket(r):
    s = (r.get("screening") or "").strip().lower()
    if s in ("include", "included", "yes", "keep"):
        return "included"
    if s in ("exclude", "excluded", "no", "drop"):
        return "excluded"
    if s in ("duplicate", "dup", "merged"):
        return "merged_duplicates"
    return "uncertain"


# --------------------------------------------------------------------------- assemble

def cmd_assemble(args):
    inp, out, work = args["input"], args["output"], args["work"]
    ensure_dir(out)

    summary = discover_input(inp)
    case_obj = None
    if summary["case_json"]:
        case_obj, _ = load_json(summary["case_json"])
    task_text = read_text(summary["task_md"]) if summary["task_md"] else None
    result, errors, degradations, _ = build_skeleton(summary, case_obj, task_text)

    state = _load_state(work)

    # merge agent decisions
    result["sub_questions"] = state.get("sub_questions", [])
    result["query_plan"] = state.get("query_plan", [])
    result["term_expansion"] = state.get("term_expansion", [])
    result["stop_decision"] = state.get("stop_decision") or result["stop_decision"]
    result["evidence"] = state.get("evidence", [])
    result["change_log"] = state.get("change_log", [])

    # scoring + ranking
    records = _compute_scores(state.get("records", []))
    buckets = {"included": [], "excluded": [], "uncertain": [], "merged_duplicates": []}
    for r in records:
        buckets[_screening_bucket(r)].append(r)

    candidates = sorted(buckets["included"], key=_rank_key)
    candidates += sorted(buckets["uncertain"], key=_rank_key)
    for idx, r in enumerate(candidates, start=1):
        r["rank"] = idx
    result["results"] = candidates

    result["screening"] = {
        "total_records": len(records),
        "included": buckets["included"],
        "excluded": buckets["excluded"],
        "uncertain": buckets["uncertain"],
        "merged_duplicates": buckets["merged_duplicates"],
    }

    # coverage gaps: sub-questions without included evidence
    covered_ids = set()
    for r in buckets["included"] + buckets["uncertain"]:
        for sq in (r.get("sub_question_ids") or []):
            covered_ids.add(sq)
    gaps = []
    for sq in result["sub_questions"]:
        sid = sq.get("id")
        if sid and sid not in covered_ids:
            gaps.append({"sub_question_id": sid, "issue": "无 included 证据覆盖"})
    result["uncertainty"]["coverage_gaps"] = gaps

    # honest status inference
    if result["status"] != "input_error":
        if result["sub_questions"] and buckets["included"]:
            result["status"] = "ok" if not gaps else "partial"
        elif result["sub_questions"]:
            result["status"] = "insufficient_evidence" if not buckets["included"] else "partial"
        else:
            result["status"] = "partial"

    if errors:
        result["errors"].extend(errors)
    if degradations:
        result["degradations"].extend(degradations)

    # runtime budget echo
    if args.get("runtime_seconds") is not None:
        result["runtime"]["elapsed_seconds"] = args["runtime_seconds"]
    if args.get("runtime_requests") is not None:
        result["runtime"]["model_requests"] = args["runtime_requests"]

    if not result["status_notes"]:
        result["status_notes"] = _default_status_note(result)

    write_json(os.path.join(out, "result.json"), result)
    _write_plan_md(out, result)
    _write_screening_md(out, result)
    print(f"assemble: status={result['status']} results={len(result['results'])} "
          f"records={result['screening']['total_records']}")
    return 0


def _default_status_note(result):
    st = result["status"]
    if st == "ok":
        return "所有子问题均有 included 证据，检索按停止条件收敛。"
    if st == "partial":
        return "已完成部分子问题或部分检索步骤，存在未覆盖缺口，详见 uncertainty.coverage_gaps。"
    if st == "insufficient_evidence":
        return "未获得足以支撑任一子问题的 included 证据，候选为空或全部未纳入。"
    if st == "input_error":
        return "输入材料无法完整解析，已记录 errors/degradations。"
    return None


# --------------------------------------------------------------------------- markdown artifacts

def _md_table(rows, headers):
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        cells = ["" if c is None else str(c).replace("|", "\\|").replace("\n", " ") for c in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _write_plan_md(out, result):
    parts = ["# 检索计划与拆解（可编辑复核稿）", "",
             f"- case_id: {result.get('case_id')}",
             f"- status: {result.get('status')}",
             f"- 研究问题: {result.get('research_question') or '（未识别）'}", ""]
    parts.append("## 子问题拆解")
    if result.get("sub_questions"):
        rows, headers = [], ["id", "question", "object", "mechanism", "method", "time_range", "comparison", "evidence_requirement"]
        for sq in result["sub_questions"]:
            if isinstance(sq, dict):
                rows.append([sq.get(h) for h in headers])
            else:
                rows.append([str(sq)] + [""] * (len(headers) - 1))
        parts.append(_md_table(rows, headers))
    else:
        parts.append("（尚未提供）")
    parts.append("")
    parts.append("## 查询计划")
    if result.get("query_plan"):
        rows, headers = [], ["step", "query", "database", "depends_on", "parallel_with", "rationale"]
        for q in result["query_plan"]:
            if isinstance(q, dict):
                rows.append([q.get(h) for h in headers])
        parts.append(_md_table(rows, headers))
    else:
        parts.append("（尚未提供）")
    parts.append("")
    parts.append("## 术语扩展")
    if result.get("term_expansion"):
        for t in result["term_expansion"]:
            parts.append("- " + json.dumps(t, ensure_ascii=False))
    else:
        parts.append("（尚未提供）")
    parts.append("")
    parts.append("## 停止判断")
    parts.append("- " + json.dumps(result.get("stop_decision"), ensure_ascii=False))
    with open(os.path.join(out, "plan.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts) + "\n")


def _write_screening_md(out, result):
    parts = ["# 全量筛选账本（可编辑复核稿）", "",
             f"- case_id: {result.get('case_id')}",
             f"- total_records: {result.get('screening', {}).get('total_records')}", ""]
    scr = result.get("screening", {})
    for bucket, title in (("included", "纳入"), ("excluded", "排除"),
                          ("uncertain", "待定"), ("merged_duplicates", "合并重复")):
        parts.append(f"## {title}（{len(scr.get(bucket, []))} 条）")
        rows, headers = [], ["title", "authors", "year", "journal", "doi/url", "screening_reason", "evidence(locator)", "quote"]
        for r in scr.get(bucket, []):
            if isinstance(r, dict):
                ev = r.get("evidence") or {}
                rows.append([
                    r.get("title"), r.get("authors"), r.get("year"), r.get("journal"),
                    r.get("doi") or r.get("url"), r.get("screening_reason"),
                    ev.get("locator") if isinstance(ev, dict) else None,
                    ev.get("quote") if isinstance(ev, dict) else None,
                ])
        if rows:
            parts.append(_md_table(rows, headers))
        else:
            parts.append("（无）")
        parts.append("")
    with open(os.path.join(out, "screening.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts) + "\n")


# --------------------------------------------------------------------------- selfcheck

def _check(checks):
    failures = []
    for check in checks:
        cid, label, dim, ok = check
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {cid} {label}  ({dim})")
        if not ok:
            failures.append((cid, label))
    return failures


def cmd_selfcheck(args):
    out, work = args["output"], args["work"]
    result_path = os.path.join(out, "result.json")
    obj, err = load_json(result_path)
    checks = []
    checks.append(("result_json", "result.json 存在且可解析", "可靠体验",
                   err is None and isinstance(obj, dict)))
    if obj is None:
        obj = {}

    checks.append(("case_id", "case_id 非空", "实际效果·完整性",
                   bool(obj.get("case_id"))))
    checks.append(("status", "status 取值合法", "输出契约",
                   obj.get("status") in STATUSES))
    sub = obj.get("sub_questions")
    checks.append(("sub_questions", "sub_questions 非空", "实际效果·完成度",
                   isinstance(sub, list) and len(sub) > 0))
    checks.append(("sub_questions_6", "每条子问题含六要素字段", "实际效果·完成度",
                   isinstance(sub, list) and all(
                       isinstance(s, dict) and all(k in s for k in
                       ("object", "mechanism", "method", "time_range", "comparison", "evidence_requirement"))
                       for s in sub)))
    checks.append(("query_plan", "query_plan 非空", "实际效果·可核验性",
                   isinstance(obj.get("query_plan"), list) and len(obj.get("query_plan")) > 0))
    checks.append(("term_expansion", "term_expansion 非空", "创新增益·方法改进",
                   isinstance(obj.get("term_expansion"), list) and len(obj.get("term_expansion")) > 0))
    stop = obj.get("stop_decision")
    checks.append(("stop_decision", "stop_decision 含 reason", "实际效果·可核验性",
                   isinstance(stop, dict) and bool(stop.get("reason"))))

    results = obj.get("results")
    res_ok = isinstance(results, list)
    ev_ok = res_ok and all(
        isinstance(r, dict) and r.get("title") and isinstance(r.get("evidence"), dict)
        and r["evidence"].get("locator") and r["evidence"].get("quote")
        for r in results)
    checks.append(("results_evidence", "每条结果含 title + evidence.locator + quote", "实际效果·正确性",
                   res_ok and ev_ok))

    scr = obj.get("screening")
    scr_ok = isinstance(scr, dict)
    ledger = scr_ok and (len(scr.get("included", [])) + len(scr.get("excluded", []))
                         + len(scr.get("uncertain", [])) + len(scr.get("merged_duplicates", []))) >= scr.get("total_records", 0)
    excl_ok = scr_ok and all(isinstance(r, dict) and bool(r.get("screening_reason")) for r in scr.get("excluded", []))
    checks.append(("screening_full", "screening 覆盖全部记录", "实际效果·可核验性", ledger))
    checks.append(("excluded_reason", "排除项均有理由", "实际效果·可核验性", excl_ok))

    evd = obj.get("evidence")
    evd_ok = isinstance(evd, list) and len(evd) > 0 and all(
        isinstance(e, dict) and e.get("query") and e.get("source") and e.get("rationale") for e in evd)
    checks.append(("evidence_log", "evidence 每条含 query/source/rationale", "实际效果·可核验性", evd_ok))

    unc = obj.get("uncertainty")
    unc_ok = isinstance(unc, dict) and all(k in unc for k in ("missing_metadata", "conflicts", "coverage_gaps", "notes"))
    checks.append(("uncertainty", "uncertainty 四子字段齐全", "实际效果·完整性", unc_ok))

    cl = obj.get("change_log")
    cl_ok = isinstance(cl, list) and len(cl) > 0 and all(
        isinstance(e, dict) and all(k in e for k in ("time", "action", "reason", "status")) for e in cl)
    checks.append(("change_log", "change_log 每条含 time/action/reason/status", "可靠体验", cl_ok))

    checks.append(("sources", "sources 非空", "生态贡献", isinstance(obj.get("sources"), list) and len(obj.get("sources")) > 0))

    contract = obj.get("output_contract")
    contract_ok = isinstance(contract, dict) and all(k in obj for k in contract)
    checks.append(("contract_echo", "output_contract 字段全部回填", "输出契约", contract_ok))

    for key in ("status_notes", "limitations", "errors", "degradations", "pending_human", "runtime"):
        checks.append((f"key_{key}", f"字段 {key} 存在", "输出契约", key in obj))

    checks.append(("screening_md", "screening.md 存在", "生态贡献",
                   os.path.isfile(os.path.join(out, "screening.md"))))
    checks.append(("plan_md", "plan.md 存在", "生态贡献",
                   os.path.isfile(os.path.join(out, "plan.md"))))

    # honest-status guard: cannot claim ok with zero results
    honest = not (obj.get("status") == "ok" and not results)
    checks.append(("status_honest", "status=ok 时不得为空结果", "可靠体验", honest))

    failures = _check(checks)
    if failures:
        print(f"\nselfcheck: {len(failures)} FAIL")
        return 1
    print("\nselfcheck: ALL PASS")
    return 0


# --------------------------------------------------------------------------- dispatch

def parse_args(argv):
    args = {
        "command": None, "input": "/input", "output": "/app/output", "work": "/app/work",
        "file": None, "runtime_seconds": None, "runtime_requests": None,
    }
    i = 0
    argv = list(argv)
    if not argv:
        args["command"] = "help"
        return args
    args["command"] = argv[0]
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in ("--input", "-i") and i + 1 < len(argv):
            args["input"] = argv[i + 1]; i += 2
        elif a in ("--output", "-o") and i + 1 < len(argv):
            args["output"] = argv[i + 1]; i += 2
        elif a in ("--work", "-w") and i + 1 < len(argv):
            args["work"] = argv[i + 1]; i += 2
        elif a in ("--file", "-f") and i + 1 < len(argv):
            args["file"] = argv[i + 1]; i += 2
        elif a == "--runtime-seconds" and i + 1 < len(argv):
            try:
                args["runtime_seconds"] = float(argv[i + 1])
            except ValueError:
                args["runtime_seconds"] = None
            i += 2
        elif a == "--runtime-requests" and i + 1 < len(argv):
            try:
                args["runtime_requests"] = int(argv[i + 1])
            except ValueError:
                args["runtime_requests"] = None
            i += 2
        else:
            i += 1
    return args


def main(argv):
    args = parse_args(argv)
    cmd = args["command"]
    if cmd in ("--help", "-h", "help"):
        print("usage: main.py <scaffold|set-plan|import|evidence|assemble|selfcheck> [options]")
        return 0
    handlers = {
        "scaffold": cmd_scaffold,
        "set-plan": cmd_set_plan,
        "import": cmd_import,
        "evidence": cmd_evidence,
        "assemble": cmd_assemble,
        "selfcheck": cmd_selfcheck,
    }
    if cmd not in handlers:
        print(f"unknown command: {cmd}")
        return 2
    return handlers[cmd](args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
