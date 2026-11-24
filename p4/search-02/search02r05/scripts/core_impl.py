#!/usr/bin/env python3
"""core_impl.py — deterministic engine for the research-question-driven search Skill.

Standard library only. Idempotent, crash-tolerant state machine. Subcommands:
  run         scan /input and immediately write a *partial* result.json skeleton.
  set-plan    merge sub_questions / query_plan / term_expansion / stop_decision.
  import      append screening records (full include/exclude/uncertain ledger).
  evidence    append evidence-log entries.
  set-fields  merge arbitrary top-level result fields (output_contract mapping).
  assemble    rebuild result.json + screening.md + plan.md from work state.
  selfcheck   validate result.json against the checklist; always exits 0.

Design emphasis (variant "output_contract completeness"):
  * the discovered contract is echoed back verbatim into `output_contract`;
  * every contract field is guaranteed as a top-level key (null when unfilled);
  * the guaranteed field list is recorded in `output_contract_fields`;
  * `set-fields` lets the Agent map actual results into contract-named fields;
  * every mutation appends a `change_log` entry (time/action/reason/status).
"""

import argparse
import json
import os
import sys
import time
import traceback

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

VALID_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")
SCREENING_KINDS = ("include", "exclude", "uncertain", "merged_duplicates")
PLAN_KEYS = ("research_question", "sub_questions", "query_plan", "term_expansion", "stop_decision")
PLAN_EXTRA_KEYS = ("status_notes", "limitations", "pending_human", "uncertainty_notes")
SIZE_CAP = 2 * 1024 * 1024

CONTRACT_KEYS = (
    "output_contract", "output_schema", "required_fields", "result_fields",
    "output_fields", "contract", "schema", "output",
)

RELIABLE_SOURCES = {
    "paper_search", "pubmed", "crossref", "openalex", "arxiv", "biorxiv",
    "medrxiv", "local", "pdf", "materials", "doi", "scopus", "wos",
    "web_of_science", "semantic_scholar",
}

RELEVANCE_LEVELS = {
    "high": 1.0, "relevant": 1.0, "strong": 1.0,
    "medium": 0.6, "partial": 0.6, "moderate": 0.6,
    "low": 0.3, "marginal": 0.3, "weak": 0.3,
    "irrelevant": 0.0, "none": 0.0,
}


# --------------------------------------------------------------------------- #
# Low-level helpers
# --------------------------------------------------------------------------- #
def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def list_files(root):
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            out.append(os.path.join(dirpath, fn))
    out.sort()
    return out


def read_text(path, cap=SIZE_CAP):
    """Read a text file with encoding tolerance. Returns (text_or_None, degradation)."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, {"path": path, "kind": "unreadable", "reason": "stat failed: %s" % exc}
    if size > cap:
        return None, {"path": path, "kind": "too_large",
                      "reason": "%d bytes > %d cap" % (size, cap)}
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, {"path": path, "kind": "unreadable", "reason": str(exc)}
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1"), {"path": path, "kind": "encoding_lossy",
                                   "reason": "not UTF-8; decoded as latin-1"}


def load_json(path):
    text, deg = read_text(path)
    if text is None:
        return None, deg
    try:
        return json.loads(text), deg
    except (ValueError, TypeError) as exc:
        return None, {"path": path, "kind": "bad_json", "reason": str(exc)}


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def write_text(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# Input scanning and output_contract discovery
# --------------------------------------------------------------------------- #
def find_contracts(obj, path="$"):
    """Recursively locate dicts under contract-like keys; returns {location: dict}."""
    found = {}
    if isinstance(obj, dict):
        for key, val in obj.items():
            child = path + "." + str(key)
            if key in CONTRACT_KEYS and isinstance(val, dict) and val:
                found[child] = val
            else:
                found.update(find_contracts(val, child))
    elif isinstance(obj, list):
        for idx, val in enumerate(obj):
            found.update(find_contracts(val, path + "[" + str(idx) + "]"))
    return found


def contract_field_names(contract):
    """Field names a contract requires result.json to contain."""
    names = []
    if not isinstance(contract, dict):
        return names
    if isinstance(contract.get("properties"), dict):
        names.extend(str(k) for k in contract["properties"])
    if isinstance(contract.get("required"), list):
        names.extend(str(k) for k in contract["required"] if isinstance(k, str))
    for field_key in ("fields", "items"):
        if isinstance(contract.get(field_key), dict):
            names.extend(str(k) for k in contract[field_key])
        elif isinstance(contract.get(field_key), list):
            names.extend(str(k) for k in contract[field_key] if isinstance(k, str))
    skip = {"properties", "required", "fields", "items", "type", "title",
            "description", "$schema", "additionalProperties", "definitions"}
    for key, val in contract.items():
        if key in skip:
            continue
        names.append(str(key))
    return names


def extract_case_id(data):
    for key in ("case_id", "id", "caseId", "challenge_id", "challengeId"):
        val = data.get(key) if isinstance(data, dict) else None
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, (int, float)):
            return str(val)
    return "unknown"


def extract_research_question(data, task_text):
    for key in ("research_question", "question", "task", "query", "title"):
        val = data.get(key) if isinstance(data, dict) else None
        if isinstance(val, str) and val.strip():
            return val.strip()
    if task_text:
        for line in task_text.splitlines():
            s = line.strip().lstrip("#").strip()
            if s and not s.startswith(("-", "challenge_id", "赛道", "学科", "Runtime")):
                return s
    return None


def scan_input(input_dir):
    degradations = []
    files = list_files(input_dir)
    summary = {
        "has_case": False, "input_error": False, "case_id": "unknown",
        "research_question": None, "output_contract": None,
        "output_contract_fields": [],
        "input_files": [os.path.basename(f) for f in files],
        "materials": [], "task_text": None,
        "degradations": degradations, "errors": [],
    }
    if not files:
        degradations.append({"kind": "empty_input", "reason": "no files under input dir"})
        return summary

    for path in files:
        if os.path.basename(path).lower() == "task.md":
            text, deg = read_text(path)
            if deg:
                degradations.append(deg)
            if text is not None:
                summary["task_text"] = text
            break

    json_files = [p for p in files if p.lower().endswith(".json")]
    json_files.sort(key=lambda p: (0 if os.path.basename(p).lower() == "case.json" else 1, p))
    case_data = None
    case_path = None
    for path in json_files:
        data, deg = load_json(path)
        if deg:
            degradations.append(deg)
        if data is not None and case_data is None:
            case_data = data
            case_path = path
            break

    if case_data is not None:
        summary["has_case"] = True
        summary["case_id"] = extract_case_id(case_data)
        summary["research_question"] = extract_research_question(case_data, summary["task_text"])
        contracts = find_contracts(case_data)
        if contracts:
            fields = []
            for _loc, contract in contracts.items():
                fields.extend(contract_field_names(contract))
            seen = set()
            fields = [f for f in fields if not (f in seen or seen.add(f))]
            summary["output_contract"] = contracts
            summary["output_contract_fields"] = fields
    elif json_files:
        summary["input_error"] = True
        summary["errors"].append({"kind": "bad_case_json", "reason": "case JSON unparseable or empty"})

    for path in files:
        base = os.path.basename(path)
        if path == case_path or base.lower() == "task.md" or path.lower().endswith(".json"):
            continue
        summary["materials"].append(path)
        if path.lower().endswith((".md", ".txt")):
            _text, deg = read_text(path)
            if deg:
                degradations.append(deg)

    return summary


# --------------------------------------------------------------------------- #
# State management
# --------------------------------------------------------------------------- #
def empty_state():
    return {
        "plan": {
            "research_question": None,
            "sub_questions": [],
            "query_plan": [],
            "term_expansion": [],
            "stop_decision": {"status": "not_started", "reason": ""},
        },
        "records": [],
        "evidence": [],
        "change_log": [],
        "errors": [],
        "result_extra": {},
        "runtime": {
            "started_at": now_iso(),
            "started_epoch": time.time(),
            "model_requests": 0,
            "seconds_elapsed": 0.0,
        },
    }


def load_state(work):
    path = os.path.join(work, "state.json")
    data, _deg = load_json(path)
    base = empty_state()
    if isinstance(data, dict):
        for key, val in data.items():
            if key == "plan" and isinstance(val, dict):
                for pk, pv in val.items():
                    base["plan"][pk] = pv
            else:
                base[key] = val
    return base


def load_case_summary(work):
    path = os.path.join(work, "case_summary.json")
    data, _deg = load_json(path)
    if isinstance(data, dict):
        return data
    return {
        "has_case": False, "input_error": False, "case_id": "unknown",
        "research_question": None, "output_contract": None,
        "output_contract_fields": [], "input_files": [], "materials": [],
        "task_text": None, "degradations": [], "errors": [],
    }


def write_state(work, state):
    write_json(os.path.join(work, "state.json"), state)


def append_change(state, action, reason, status="ok"):
    state.setdefault("change_log", []).append({
        "time": now_iso(), "action": action, "reason": reason, "status": status,
    })


def record_error(state, reason):
    state.setdefault("errors", []).append({"time": now_iso(), "reason": reason})


def load_input_data(args):
    if getattr(args, "file", None):
        data, deg = load_json(args.file)
        if deg:
            print("warning: %s" % deg.get("reason", deg))
        return data
    if getattr(args, "inline", None):
        try:
            return json.loads(args.inline)
        except (ValueError, TypeError) as exc:
            print("error: bad inline JSON: %s" % exc)
            return None
    return None


def normalize_records(data):
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data["records"] if isinstance(data.get("records"), list) else [data]
    else:
        items = []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rec = dict(item)
        if rec.get("screening") not in SCREENING_KINDS:
            rec["screening"] = "uncertain"
        out.append(rec)
    return out


def normalize_entries(data):
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("entries", "evidence", "items"):
            if isinstance(data.get(key), list):
                return [x for x in data[key] if isinstance(x, dict)]
        return [data]
    return []


# --------------------------------------------------------------------------- #
# Result assembly
# --------------------------------------------------------------------------- #
def to_relevance(value):
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return RELEVANCE_LEVELS.get(value.strip().lower(), 0.0)
    return 0.0


def compute_score(record):
    rel = to_relevance(record.get("relevance"))
    if rel > 1.0:
        rel = rel / 10.0 if rel <= 10.0 else min(1.0, rel / 100.0)
    rel = max(0.0, min(1.0, rel))
    meta_fields = ("title", "authors", "year", "journal", "doi")
    present = sum(1 for f in meta_fields if record.get(f))
    meta_score = present / float(len(meta_fields))
    db = str(record.get("database") or "").lower()
    src_score = 1.0 if db in RELIABLE_SOURCES else (0.5 if db else 0.3)
    score = round(0.6 * rel + 0.2 * meta_score + 0.2 * src_score, 4)
    breakdown = {
        "relevance": round(rel, 4),
        "metadata_completeness": round(meta_score, 4),
        "source_reliability": round(src_score, 4),
        "weights": {"relevance": 0.6, "metadata_completeness": 0.2, "source_reliability": 0.2},
    }
    return score, breakdown


def compute_status(case_summary, state):
    if case_summary.get("input_error") and not case_summary.get("has_case"):
        return "input_error"
    if not case_summary.get("has_case"):
        return "insufficient_evidence"
    stop = state.get("plan", {}).get("stop_decision", {})
    if not isinstance(stop, dict):
        return "partial"
    s = stop.get("status")
    if s in ("done", "complete", "converged"):
        return "ok"
    if s in ("handoff", "blocked"):
        return "partial"
    if s in ("no_evidence", "exhausted"):
        return "insufficient_evidence"
    return "partial"


def build_screening(records):
    bucket_of = {"include": "included", "exclude": "excluded",
                 "uncertain": "uncertain", "merged_duplicates": "merged_duplicates"}
    ledger = {"included": [], "excluded": [], "uncertain": [], "merged_duplicates": []}
    for rec in records:
        bucket = bucket_of.get(rec.get("screening"), "uncertain")
        ledger[bucket].append(rec)
    ledger["totals"] = {k: len(ledger[k]) for k in ("included", "excluded", "uncertain", "merged_duplicates")}
    return ledger


def build_results(records):
    included = [r for r in records if r.get("screening") == "include"]
    scored = []
    for rec in included:
        score, breakdown = compute_score(rec)
        item = dict(rec)
        item["score"] = score
        item["score_breakdown"] = breakdown
        scored.append(item)
    scored.sort(key=lambda r: r.get("score", 0), reverse=True)
    for idx, rec in enumerate(scored, 1):
        rec["rank"] = idx
    return scored


def build_sources(records, evidence):
    seen = {}
    for rec in records:
        db = rec.get("database")
        url = rec.get("url")
        if db or url:
            key = str(db or "") + "|" + str(url or "")
            if key in seen:
                seen[key]["record_count"] += 1
            else:
                seen[key] = {"database": db, "url": url, "record_count": 1}
    for ent in evidence:
        src = ent.get("source")
        if src:
            key = str(ent.get("database") or "") + "|" + str(src)
            if key in seen:
                seen[key]["record_count"] += 1
            else:
                seen[key] = {"database": ent.get("database"), "url": src, "record_count": 1}
    return list(seen.values())


def build_uncertainty(case_summary, state, records):
    missing = []
    for rec in records:
        for field in ("title", "authors", "year", "journal", "doi"):
            if not rec.get(field):
                missing.append({"field": field,
                                "record": rec.get("title") or rec.get("rank") or "?",
                                "reason": "metadata missing"})
    included_ids = set()
    for rec in records:
        if rec.get("screening") == "include":
            for sid in (rec.get("sub_question_ids") or []):
                included_ids.add(sid)
    coverage = []
    for sq in (state.get("plan", {}).get("sub_questions") or []):
        sid = sq.get("id") if isinstance(sq, dict) else str(sq)
        if sid not in included_ids:
            coverage.append({"sub_question_id": sid, "status": "no_included_evidence"})
    conflicts = [e for e in state.get("evidence", [])
                 if "conflict" in str(e.get("kind", "")).lower()]
    return {
        "missing_metadata": missing,
        "conflicts": conflicts,
        "coverage_gaps": coverage,
        "notes": state.get("plan", {}).get("uncertainty_notes", []),
    }


def build_runtime(state, args):
    rt = state.get("runtime", {})
    started_epoch = rt.get("started_epoch")
    elapsed = getattr(args, "runtime_seconds", 0.0)
    if not elapsed:
        if started_epoch:
            elapsed = max(0.0, time.time() - float(started_epoch))
        else:
            elapsed = rt.get("seconds_elapsed", 0.0)
    requests = getattr(args, "runtime_requests", 0) or rt.get("model_requests", 0)
    return {
        "started_at": rt.get("started_at"),
        "seconds_elapsed": round(float(elapsed), 1),
        "model_requests": int(requests),
        "budget_seconds": 900,
        "budget_requests": 64,
    }


def echo_contract_fields(result, case_summary):
    for field in case_summary.get("output_contract_fields", []):
        result.setdefault(field, None)
    return result


def build_result(case_summary, state, args):
    plan = state.get("plan", {})
    records = state.get("records", [])
    result = {}
    result["case_id"] = case_summary.get("case_id", "unknown")
    result["status"] = compute_status(case_summary, state)
    result["research_question"] = plan.get("research_question", case_summary.get("research_question"))
    result["sub_questions"] = plan.get("sub_questions", [])
    result["query_plan"] = plan.get("query_plan", [])
    result["term_expansion"] = plan.get("term_expansion", [])
    result["stop_decision"] = plan.get("stop_decision", {"status": "not_started", "reason": ""})
    result["screening"] = build_screening(records)
    result["results"] = build_results(records)
    result["evidence"] = state.get("evidence", [])
    result["sources"] = build_sources(records, state.get("evidence", []))
    result["uncertainty"] = build_uncertainty(case_summary, state, records)
    result["change_log"] = state.get("change_log", [])
    result["status_notes"] = plan.get("status_notes", "")
    result["limitations"] = plan.get("limitations", [])
    result["errors"] = (case_summary.get("errors") or []) + (state.get("errors") or [])
    result["degradations"] = case_summary.get("degradations", [])
    result["pending_human"] = plan.get("pending_human", [])
    result["runtime"] = build_runtime(state, args)
    result["output_contract"] = case_summary.get("output_contract")
    result["output_contract_fields"] = case_summary.get("output_contract_fields", [])
    result = echo_contract_fields(result, case_summary)
    for key, val in state.get("result_extra", {}).items():
        result[key] = val
    result = echo_contract_fields(result, case_summary)
    return result


# --------------------------------------------------------------------------- #
# Markdown renders
# --------------------------------------------------------------------------- #
def render_plan(result):
    lines = ["# 检索计划与拆解（可编辑）", ""]
    lines.append("## 研究问题")
    lines.append("")
    lines.append(str(result.get("research_question") or "（未填写）"))
    lines.append("")
    lines.append("## 子问题拆解")
    lines.append("")
    subs = result.get("sub_questions") or []
    if not subs:
        lines.append("（空）")
    for sq in subs:
        if isinstance(sq, dict):
            lines.append("- **%s**：%s" % (sq.get("id", ""), sq.get("question", sq)))
        else:
            lines.append("- %s" % sq)
    lines.append("")
    lines.append("## 查询计划")
    lines.append("")
    lines.append(json.dumps(result.get("query_plan", []), ensure_ascii=False, indent=2))
    lines.append("")
    lines.append("## 术语扩展")
    lines.append("")
    lines.append(json.dumps(result.get("term_expansion", []), ensure_ascii=False, indent=2))
    lines.append("")
    lines.append("## 停止判断")
    lines.append("")
    lines.append(json.dumps(result.get("stop_decision", {}), ensure_ascii=False, indent=2))
    lines.append("")
    return "\n".join(lines)


def render_screening(result):
    lines = ["# 全量筛选清单（可编辑）", ""]
    ledger = result.get("screening", {})
    totals = ledger.get("totals", {})
    lines.append("合计：included=%s，excluded=%s，uncertain=%s，merged_duplicates=%s" % (
        totals.get("included", 0), totals.get("excluded", 0),
        totals.get("uncertain", 0), totals.get("merged_duplicates", 0)))
    lines.append("")
    for bucket, title in (("included", "纳入"), ("excluded", "排除"),
                          ("uncertain", "待定"), ("merged_duplicates", "合并去重")):
        lines.append("## %s" % title)
        lines.append("")
        recs = ledger.get(bucket, [])
        if not recs:
            lines.append("（空）")
            lines.append("")
            continue
        for rec in recs:
            lines.append("- %s" % (rec.get("title") or "（无标题）"))
            if rec.get("screening_reason"):
                lines.append("  - 理由：%s" % rec["screening_reason"])
            ev = rec.get("evidence")
            if isinstance(ev, dict):
                if ev.get("locator"):
                    lines.append("  - 定位：%s" % ev["locator"])
                if ev.get("quote"):
                    lines.append("  - 摘录：%s" % ev["quote"])
            lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def assemble(args):
    os.makedirs(args.output, exist_ok=True)
    case_summary = load_case_summary(args.work)
    state = load_state(args.work)
    result = build_result(case_summary, state, args)
    write_json(os.path.join(args.output, "result.json"), result)
    write_text(os.path.join(args.output, "screening.md"), render_screening(result))
    write_text(os.path.join(args.output, "plan.md"), render_plan(result))
    return result


def cmd_run(args):
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    state_path = os.path.join(args.work, "state.json")
    if os.path.exists(state_path) and not args.force:
        existing = load_state(args.work)
        has_work = bool(existing.get("records") or existing["plan"].get("sub_questions")
                        or existing.get("evidence"))
        if has_work:
            print("run: work state already exists; refusing to overwrite (use --force to reset).")
            assemble(args)
            return 0
    case_summary = scan_input(args.input)
    write_json(os.path.join(args.work, "case_summary.json"), case_summary)
    state = empty_state()
    state["plan"]["research_question"] = case_summary.get("research_question")
    append_change(state, "run", "skeleton written; status=partial", "ok")
    write_state(args.work, state)
    assemble(args)
    print("run: skeleton written to %s" % os.path.join(args.output, "result.json"))
    return 0


def cmd_set_plan(args):
    os.makedirs(args.work, exist_ok=True)
    state = load_state(args.work)
    data = load_input_data(args)
    merged = []
    if isinstance(data, dict):
        for key in PLAN_KEYS:
            if key in data:
                state["plan"][key] = data[key]
                merged.append(key)
        for key in PLAN_EXTRA_KEYS:
            if key in data:
                state["plan"][key] = data[key]
                merged.append(key)
        append_change(state, "set-plan", "merged: %s" % (", ".join(merged) or "none"), "ok")
    else:
        append_change(state, "set-plan", "expected a JSON object", "error")
        record_error(state, "set-plan: input was not a JSON object")
    write_state(args.work, state)
    assemble(args)
    return 0


def cmd_import(args):
    os.makedirs(args.work, exist_ok=True)
    state = load_state(args.work)
    data = load_input_data(args)
    if data is None:
        append_change(state, "import", "no valid input data", "error")
        record_error(state, "import: failed to parse input")
    else:
        records = normalize_records(data)
        state["records"].extend(records)
        append_change(state, "import", "imported %d record(s)" % len(records), "ok")
    write_state(args.work, state)
    assemble(args)
    return 0


def cmd_evidence(args):
    os.makedirs(args.work, exist_ok=True)
    state = load_state(args.work)
    data = load_input_data(args)
    if data is None:
        append_change(state, "evidence", "no valid input data", "error")
        record_error(state, "evidence: failed to parse input")
    else:
        entries = normalize_entries(data)
        state["evidence"].extend(entries)
        append_change(state, "evidence", "appended %d evidence entry(ies)" % len(entries), "ok")
    write_state(args.work, state)
    assemble(args)
    return 0


def cmd_set_fields(args):
    os.makedirs(args.work, exist_ok=True)
    state = load_state(args.work)
    data = load_input_data(args)
    if isinstance(data, dict):
        state["result_extra"].update(data)
        append_change(state, "set-fields", "merged %d top-level field(s)" % len(data), "ok")
    else:
        append_change(state, "set-fields", "expected a JSON object", "error")
        record_error(state, "set-fields: input was not a JSON object")
    write_state(args.work, state)
    assemble(args)
    return 0


def cmd_assemble(args):
    os.makedirs(args.work, exist_ok=True)
    assemble(args)
    return 0


def cmd_selfcheck(args):
    result, _deg = load_json(os.path.join(args.output, "result.json"))
    checks = []

    def add(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    if result is None:
        add("result.json exists and parses", False, "missing or invalid")
    else:
        add("result.json exists and parses", True)
        for field in ("case_id", "status", "sources", "uncertainty", "change_log"):
            add("required:%s" % field, field in result)
        add("status valid", result.get("status") in VALID_STATUS, str(result.get("status")))
        add("main artifacts present", all(
            k in result for k in ("results", "screening", "evidence", "sub_questions",
                                  "query_plan", "term_expansion", "stop_decision")))
        for field in result.get("output_contract_fields", []):
            add("contract:%s" % field, field in result)
        ledger = result.get("screening", {})
        totals = ledger.get("totals", {})
        add("screening ledger totals", all(k in totals for k in ("included", "excluded", "uncertain")))
        add("change_log non-empty", bool(result.get("change_log")))
        add("evidence is a list", isinstance(result.get("evidence"), list))

    n_ok = sum(1 for c in checks if c["ok"])
    for c in checks:
        print("%s  %s%s" % ("PASS" if c["ok"] else "FAIL", c["check"],
                            (" :: " + c["detail"]) if c["detail"] else ""))
    print("summary: %d/%d PASS" % (n_ok, len(checks)))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="research-question-driven search Skill engine (standard library only)")

    def add_io(sp):
        sp.add_argument("--file", default=None, help="JSON file to read")
        sp.add_argument("--inline", default=None, help="inline JSON (simple cases only)")
        sp.add_argument("--work", default=DEFAULT_WORK)
        sp.add_argument("--output", default=DEFAULT_OUTPUT)

    sub = parser.add_subparsers(dest="cmd")

    sp = sub.add_parser("run", help="scan input and write a partial result.json skeleton")
    sp.add_argument("--input", default=DEFAULT_INPUT)
    sp.add_argument("--output", default=DEFAULT_OUTPUT)
    sp.add_argument("--work", default=DEFAULT_WORK)
    sp.add_argument("--force", action="store_true", help="reset existing work state")

    for name in ("set-plan", "import", "evidence", "set-fields"):
        sp = sub.add_parser(name)
        add_io(sp)

    sp = sub.add_parser("assemble", help="rebuild result.json + screening.md + plan.md")
    sp.add_argument("--input", default=DEFAULT_INPUT)
    sp.add_argument("--output", default=DEFAULT_OUTPUT)
    sp.add_argument("--work", default=DEFAULT_WORK)
    sp.add_argument("--runtime-seconds", type=float, default=0.0)
    sp.add_argument("--runtime-requests", type=int, default=0)

    sp = sub.add_parser("selfcheck", help="validate result.json against checklist (exit 0)")
    sp.add_argument("--output", default=DEFAULT_OUTPUT)
    sp.add_argument("--work", default=DEFAULT_WORK)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "run": cmd_run,
        "set-plan": cmd_set_plan,
        "import": cmd_import,
        "evidence": cmd_evidence,
        "set-fields": cmd_set_fields,
        "assemble": cmd_assemble,
        "selfcheck": cmd_selfcheck,
    }
    fn = handlers.get(args.cmd)
    if fn is None:
        parser.print_help()
        return 0
    return fn(args)


def _emergency_result(exc):
    try:
        os.makedirs(DEFAULT_OUTPUT, exist_ok=True)
        result = {
            "case_id": "unknown",
            "status": "input_error",
            "research_question": None,
            "sub_questions": [], "query_plan": [], "term_expansion": [],
            "stop_decision": {"status": "handoff", "reason": "unexpected error"},
            "results": [],
            "screening": {"included": [], "excluded": [], "uncertain": [], "merged_duplicates": [],
                          "totals": {"included": 0, "excluded": 0, "uncertain": 0, "merged_duplicates": 0}},
            "evidence": [], "sources": [],
            "uncertainty": {"missing_metadata": [], "conflicts": [], "coverage_gaps": [], "notes": []},
            "change_log": [{"time": now_iso(), "action": "emergency",
                            "reason": "unexpected exception", "status": "error"}],
            "status_notes": "", "limitations": [],
            "errors": [{"kind": "unexpected_exception", "reason": str(exc)}],
            "degradations": [],
            "pending_human": ["inspect stack trace and re-run"],
            "runtime": {"started_at": now_iso(), "seconds_elapsed": 0, "model_requests": 0,
                        "budget_seconds": 900, "budget_requests": 64},
            "output_contract": None, "output_contract_fields": [],
        }
        write_json(os.path.join(DEFAULT_OUTPUT, "result.json"), result)
    except Exception:
        pass


if __name__ == "__main__":
    code = 0
    try:
        code = main() or 0
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        _emergency_result(exc)
        code = 0
    sys.exit(code)
