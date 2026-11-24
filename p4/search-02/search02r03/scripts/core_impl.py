#!/usr/bin/env python3
"""Deterministic engine for the research-question-driven search Skill.

Design goals (this package's differentiator):
1. Determinism -- every assembled artifact is a pure function of (input files,
   state.json). Serialization uses sorted keys and fixed orderings; wall-clock
   timestamps live only under run_meta, never inside the analysis body.
2. Incremental state -- init writes a partial skeleton once, later commands only
   append/merge state; re-running init never resets existing work.
3. Robustness -- bad JSON, bad encoding, empty input and oversized files degrade
   gracefully instead of crashing.

Pure standard library. Runs under Python 3.8+.
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

SCHEMA_VERSION = 1
DEFAULT_MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB per input text file

CANONICAL_STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
SCREENING_STATUSES = ("included", "excluded", "uncertain", "merged_duplicate")

RECORD_FIELDS = (
    "rank", "title", "authors", "year", "journal", "doi", "url", "database",
    "sub_question_ids", "relevance", "relevance_reason",
    "screening", "screening_reason",
    "evidence", "score", "score_breakdown",
)

# Transparent, reproducible scoring rubric. The model supplies the four
# 0..1 components; the script computes the final score so it is reproducible.
SCORE_WEIGHTS = {
    "relevance": 0.55,
    "evidence": 0.20,
    "metadata": 0.15,
    "reliability": 0.10,
}

PLAN_KINDS = (
    "status", "research_question", "sub_questions", "query_plan",
    "term_expansion", "stop_decision", "output_contract_values",
    "status_notes", "limitations", "pending_human",
)

OUTPUT_CONTRACT_KEYS = ("output_contract", "output", "schema", "required_fields", "contract")


def _iso_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dumps(obj):
    """Stable serialization: sorted keys, no ASCII escaping, trailing newline."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def log(msg):
    print(msg, file=sys.stderr)


# --------------------------------------------------------------------------
# Input discovery
# --------------------------------------------------------------------------

def read_text_file(path, max_bytes=DEFAULT_MAX_FILE_BYTES):
    """Return (text, note). note describes encoding fallbacks / skipping."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, {"error": str(exc)}
    if size > max_bytes:
        return None, {"skipped": True, "reason": "oversize", "bytes": size}
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, {"error": str(exc)}
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    for enc in ("utf-8", "gbk", "cp1252", "latin-1"):
        try:
            return raw.decode(enc), {"encoding": enc}
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace"), {"encoding": "replace", "note": "undecodable bytes replaced"}


def parse_json_text(text, path):
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001 -- degrade, never crash
        return None, {"file": path, "error": "bad_json", "detail": str(exc)}


def scan_input(input_dir):
    """Discover every file under input_dir; never assume fixed names."""
    files = []
    errors = []
    degradations = []
    case = {}
    task_md = None
    materials = []

    if not os.path.isdir(input_dir):
        errors.append({"scope": "input", "error": "missing_input_dir", "path": input_dir})
        return {
            "files": files, "case": case, "task_md": task_md, "materials": materials,
            "output_contract": {}, "errors": errors, "degradations": degradations,
        }

    for root, _dirs, names in os.walk(input_dir):
        names = sorted(names)
        for name in names:
            path = os.path.join(root, name)
            rel = os.path.relpath(path, input_dir).replace("\\", "/")
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            files.append({"name": name, "rel": rel, "path": path, "size": size})
            if size > DEFAULT_MAX_FILE_BYTES:
                degradations.append({"file": rel, "reason": "oversize_skipped", "bytes": size})
                continue
            text, note = read_text_file(path)
            if note and note.get("skipped"):
                degradations.append({"file": rel, **note})
                continue
            if note and note.get("error"):
                errors.append({"file": rel, **note})
                continue

            if name.lower() in ("case.json", "case.jsonl", "input.json"):
                parsed, err = parse_json_text(text, rel)
                if err:
                    errors.append(err)
                elif isinstance(parsed, dict):
                    case = parsed
                else:
                    errors.append({"file": rel, "error": "unexpected_type", "detail": "expected object"})
            elif name.lower() == "task.md":
                task_md = text
            elif name.lower().endswith((".md", ".txt", ".json", ".jsonl", ".csv")):
                materials.append({"name": name, "rel": rel, "path": path, "text": text, "encoding": note})
            else:
                # PDFs and other binaries are left for the Agent to parse with the
                # platform PDF tools; we only record their presence and size.
                materials.append({"name": name, "rel": rel, "path": path, "text": None, "note": "binary_or_unknown"})

    return {
        "files": files, "case": case, "task_md": task_md, "materials": materials,
        "output_contract": extract_output_contract(case),
        "errors": errors, "degradations": degradations,
    }


def extract_output_contract(case):
    for key in OUTPUT_CONTRACT_KEYS:
        if key in case and isinstance(case[key], (dict, list)):
            return case[key]
    return {}


def extract_case_id(case, input_dir):
    for key in ("case_id", "id", "challenge_id", "task_id"):
        if isinstance(case.get(key), str) and case[key].strip():
            return case[key].strip()
    if isinstance(case.get("challenge_id"), str):
        return case["challenge_id"]
    return os.path.basename(os.path.abspath(input_dir)) or "unknown"


def extract_research_question(case, task_md):
    for key in ("research_question", "question", "query", "topic"):
        if isinstance(case.get(key), str) and case[key].strip():
            return case[key].strip()
    if task_md:
        for line in task_md.splitlines():
            stripped = line.strip().lstrip("#").strip()
            if stripped and not stripped.startswith(("challenge", "赛道", "学科")):
                return stripped
    return None


# --------------------------------------------------------------------------
# State (single source of truth under /app/work/state.json)
# --------------------------------------------------------------------------

EMPTY_STATE = {
    "schema_version": SCHEMA_VERSION,
    "research_question": None,
    "status": None,
    "sub_questions": [],
    "query_plan": [],
    "term_expansion": {},
    "stop_decision": {},
    "output_contract_values": {},
    "records": [],
    "evidence": [],
    "merged_duplicates": [],
    "status_notes": [],
    "limitations": [],
    "pending_human": [],
    "change_log": [],
}


def state_path(work_dir):
    return os.path.join(work_dir, "state.json")


def load_state(work_dir):
    if not os.path.isfile(state_path(work_dir)):
        return dict(EMPTY_STATE)
    text, note = read_text_file(state_path(work_dir))
    parsed, err = parse_json_text(text or "", state_path(work_dir))
    if err or not isinstance(parsed, dict):
        log("warning: state.json unreadable, starting from empty state (%s)" % (err or "bad type"))
        return dict(EMPTY_STATE)
    state = dict(EMPTY_STATE)
    state.update(parsed)
    for key in ("records", "evidence", "merged_duplicates", "status_notes",
                "limitations", "pending_human", "change_log", "sub_questions", "query_plan"):
        if not isinstance(state.get(key), list):
            state[key] = []
    if not isinstance(state.get("term_expansion"), dict):
        state["term_expansion"] = {}
    if not isinstance(state.get("stop_decision"), dict):
        state["stop_decision"] = {}
    if not isinstance(state.get("output_contract_values"), dict):
        state["output_contract_values"] = {}
    return state


def save_state(work_dir, state):
    os.makedirs(work_dir, exist_ok=True)
    state["schema_version"] = SCHEMA_VERSION
    with open(state_path(work_dir), "w", encoding="utf-8") as fh:
        fh.write(dumps(state))


def append_change(state, action, reason, status="done"):
    step = len(state["change_log"]) + 1
    state["change_log"].append({"step": step, "action": action, "reason": reason, "status": status})
    return step


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_init(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    summary = scan_input(args.input)
    state = load_state(args.work)

    # Never reset existing work: init only bootstraps a skeleton once.
    if os.path.isfile(state_path(args.work)):
        log("info: state already exists; refusing to reset, re-assembling only")
        cmd_assemble(args)
        return 0

    state["research_question"] = extract_research_question(summary["case"], summary["task_md"])
    if state.get("status") is None:
        state["status"] = "partial"
    append_change(state, "init", "input scanned and partial skeleton created", "partial")
    save_state(args.work, state)
    return cmd_assemble(args)


def _read_payload(args):
    """Return parsed JSON payload from --file or --inline; exit cleanly on error."""
    if args.file:
        text, note = read_text_file(args.file)
        if note and note.get("error"):
            log("error: cannot read payload %s: %s" % (args.file, note.get("error")))
            return None
        if note and note.get("skipped"):
            log("error: payload too large: %s" % args.file)
            return None
        payload, err = parse_json_text(text or "", args.file)
        if err:
            log("error: bad JSON in %s: %s" % (args.file, err.get("detail")))
            return None
        return payload
    if args.inline:
        payload, err = parse_json_text(args.inline, "<inline>")
        if err:
            log("error: bad inline JSON: %s" % err.get("detail"))
            return None
        return payload
    log("error: --file or --inline is required")
    return None


def cmd_set_plan(args):
    os.makedirs(args.work, exist_ok=True)
    if args.kind not in PLAN_KINDS:
        log("error: unknown kind %r (allowed: %s)" % (args.kind, ", ".join(PLAN_KINDS)))
        return 2
    payload = _read_payload(args)
    if payload is None:
        return 2
    state = load_state(args.work)
    if args.kind in ("sub_questions", "query_plan", "status_notes", "limitations", "pending_human"):
        if not isinstance(payload, list):
            payload = [payload]
        state[args.kind] = payload
    elif args.kind == "status":
        if payload in CANONICAL_STATUSES:
            state["status"] = payload
        else:
            log("error: status must be one of %s" % (CANONICAL_STATUSES,))
            return 2
    else:
        state[args.kind] = payload
    append_change(state, "set-plan:" + args.kind, "plan component updated via file" if args.file else "plan component updated inline", "done")
    save_state(args.work, state)
    return 0


def _dedup_key(rec):
    title = (rec.get("title") or "").strip().casefold()
    year = str(rec.get("year") or "").strip()
    return (title, year)


def _normalize_record(rec):
    if not isinstance(rec, dict):
        return None
    out = {}
    for key in RECORD_FIELDS:
        out[key] = rec.get(key)
    if out["screening"] not in SCREENING_STATUSES:
        out["screening"] = "included" if out.get("relevance") is not None else "uncertain"
    if not isinstance(out.get("sub_question_ids"), list):
        out["sub_question_ids"] = []
    if not isinstance(out.get("authors"), list):
        out["authors"] = [] if out.get("authors") is None else [out["authors"]]
    if out.get("evidence") is None:
        out["evidence"] = []
    if isinstance(out.get("evidence"), dict):
        out["evidence"] = [out["evidence"]]
    return out


def cmd_import(args):
    os.makedirs(args.work, exist_ok=True)
    payload = _read_payload(args)
    if payload is None:
        return 2
    if isinstance(payload, dict):
        payload = payload.get("records", payload.get("results", []))
    if not isinstance(payload, list):
        log("error: import payload must be a list of records")
        return 2
    state = load_state(args.work)
    seen = {}
    for existing in state["records"]:
        seen[_dedup_key(existing)] = existing
    added = 0
    for raw in payload:
        rec = _normalize_record(raw)
        if rec is None:
            continue
        key = _dedup_key(rec)
        if key in seen and key != ("", ""):
            state["merged_duplicates"].append({
                "kept": seen[key].get("title"),
                "dropped": rec.get("title"),
                "reason": "duplicate of already-imported record",
            })
            continue
        if key != ("", ""):
            seen[key] = rec
        state["records"].append(rec)
        added += 1
    append_change(state, "import", "imported %d candidate records (%d duplicates merged)" % (added, len(state["merged_duplicates"])), "done")
    save_state(args.work, state)
    return 0


def cmd_record_evidence(args):
    os.makedirs(args.work, exist_ok=True)
    payload = _read_payload(args)
    if payload is None:
        return 2
    if isinstance(payload, dict):
        payload = payload.get("evidence", payload.get("entries", []))
    if not isinstance(payload, list):
        payload = [payload]
    state = load_state(args.work)
    state["evidence"].extend(payload)
    append_change(state, "record-evidence", "appended %d evidence entries" % len(payload), "done")
    save_state(args.work, state)
    return 0


# --------------------------------------------------------------------------
# Assemble
# --------------------------------------------------------------------------

def _compute_score(rec):
    if isinstance(rec.get("score"), (int, float)):
        return float(rec["score"])
    sb = rec.get("score_breakdown") or {}
    if not isinstance(sb, dict):
        return None
    total = 0.0
    for key, weight in SCORE_WEIGHTS.items():
        value = sb.get(key)
        if not isinstance(value, (int, float)):
            return None
        total += weight * float(value)
    return round(total, 3)


def _sort_records(records):
    def key(rec):
        rank = rec.get("rank")
        try:
            rank_key = (0, float(rank))
        except (TypeError, ValueError):
            rank_key = (1, 0.0)
        return (rank_key, (rec.get("title") or "").casefold(), str(rec.get("year") or ""))
    return sorted(records, key=key)


def _build_screening(state):
    records = _sort_records(state["records"])
    counts = {"included": 0, "excluded": 0, "uncertain": 0, "merged_duplicates": 0, "total": 0}
    ledger = []
    for rec in records:
        screening = rec.get("screening")
        counts["total"] += 1
        if screening in counts:
            counts[screening] += 1
        ledger.append({
            "title": rec.get("title"),
            "year": rec.get("year"),
            "screening": screening,
            "screening_reason": rec.get("screening_reason"),
            "sub_question_ids": rec.get("sub_question_ids"),
        })
    counts["merged_duplicates"] = len(state.get("merged_duplicates", []))
    return {
        "counts": counts,
        "ledger": ledger,
        "merged_duplicates": state.get("merged_duplicates", []),
    }


def _build_results(records):
    included = [r for r in _sort_records(records) if r.get("screening") == "included"]
    out = []
    for i, rec in enumerate(included, 1):
        item = {k: rec.get(k) for k in RECORD_FIELDS}
        item["rank"] = rec.get("rank") if rec.get("rank") is not None else i
        item["score"] = _compute_score(rec)
        out.append(item)
    return out


def _build_sources(records, evidence):
    sources = []
    seen = set()
    for rec in records:
        db = rec.get("database")
        if db and db not in seen:
            seen.add(db)
            sources.append({"database": db, "records": len([r for r in records if r.get("database") == db])})
    for entry in evidence:
        src = entry.get("source") or entry.get("database")
        if src and src not in seen:
            seen.add(src)
            sources.append({"database": src, "records": 0})
    return sources


def _build_uncertainty(state, results):
    sub_ids = set()
    for sq in state.get("sub_questions", []):
        if isinstance(sq, dict) and sq.get("id"):
            sub_ids.add(sq["id"])
        elif isinstance(sq, str):
            sub_ids.add(sq)
    covered = set()
    for rec in results:
        for sid in rec.get("sub_question_ids", []):
            covered.add(sid)
    coverage_gaps = sorted(sub_ids - covered, key=lambda s: str(s))
    uncertain_records = [r.get("title") for r in state["records"] if r.get("screening") == "uncertain"]
    return {
        "missing_metadata": [
            r.get("title") for r in state["records"]
            if r.get("screening") == "included" and any(r.get(k) in (None, "", []) for k in ("doi", "journal", "year"))
        ],
        "conflicts": [],
        "coverage_gaps": coverage_gaps,
        "uncertain_records": uncertain_records,
        "notes": [],
    }


def _derive_status(state, input_summary, results, uncertainty):
    override = state.get("status")
    if override in CANONICAL_STATUSES and override != "partial":
        return override
    if input_summary.get("errors") and not input_summary["case"] and not input_summary["materials"]:
        return "input_error"
    if not state.get("sub_questions") and not results:
        return "insufficient_evidence"
    if state.get("stop_decision", {}).get("status") == "handoff":
        return "partial"
    if uncertainty["coverage_gaps"] and not results:
        return "insufficient_evidence"
    if uncertainty["coverage_gaps"]:
        return "partial"
    if results:
        return "ok"
    return "partial"


def _sha256_input(input_summary):
    h = hashlib.sha256()
    for f in input_summary["files"]:
        h.update(f["rel"].encode("utf-8", "replace"))
        h.update(b"\x00")
        try:
            with open(f["path"], "rb") as fh:
                h.update(fh.read())
        except OSError:
            h.update(b"<unreadable>")
        h.update(b"\x00")
    return h.hexdigest()


def cmd_assemble(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    summary = scan_input(args.input)
    state = load_state(args.work)

    contract = summary["output_contract"]
    results = _build_results(state["records"])
    screening = _build_screening(state)
    uncertainty = _build_uncertainty(state, results)
    status = _derive_status(state, summary, results, uncertainty)

    body = {
        "case_id": extract_case_id(summary["case"], args.input),
        "status": status,
        "research_question": state.get("research_question") or extract_research_question(summary["case"], summary["task_md"]),
        "sub_questions": state.get("sub_questions", []),
        "query_plan": state.get("query_plan", []),
        "term_expansion": state.get("term_expansion", {}),
        "stop_decision": state.get("stop_decision", {}),
        "results": results,
        "screening": screening,
        "evidence": state.get("evidence", []),
        "sources": _build_sources(state["records"], state.get("evidence", [])),
        "uncertainty": uncertainty,
        "change_log": state.get("change_log", []),
        "status_notes": state.get("status_notes", []),
        "limitations": state.get("limitations", []),
        "errors": summary.get("errors", []),
        "degradations": summary.get("degradations", []),
        "pending_human": state.get("pending_human", []),
    }

    # Echo every output_contract field verbatim; unfilled fields become null (or []).
    contract_values = state.get("output_contract_values", {}) or {}
    if isinstance(contract, dict):
        for key in contract:
            body[key] = contract_values.get(key, None)
    elif isinstance(contract, list):
        for key in contract:
            body[key] = contract_values.get(key, [])

    body_sha = sha256_bytes(dumps(body).encode("utf-8"))
    input_sha = _sha256_input(summary)
    state_sha = sha256_bytes(dumps(state).encode("utf-8"))

    started = _iso_now()
    run_meta = {
        "schema_version": SCHEMA_VERSION,
        "started_at": started,
        "finished_at": _iso_now(),
        "duration_seconds": getattr(args, "runtime_seconds", None),
        "model_requests_used": getattr(args, "runtime_requests", None),
        "input_sha256": input_sha,
        "state_sha256": state_sha,
        "deterministic_content_sha256": body_sha,
    }
    result = dict(body)
    result["run_meta"] = run_meta

    with open(os.path.join(args.output, "result.json"), "w", encoding="utf-8") as fh:
        fh.write(dumps(result))
    _write_markdown(args.output, body, screening, uncertainty)

    # Also persist the deterministic body hash for external verification.
    with open(os.path.join(args.output, "manifest.json"), "w", encoding="utf-8") as fh:
        fh.write(dumps({
            "deterministic_content_sha256": body_sha,
            "input_sha256": input_sha,
            "state_sha256": state_sha,
            "schema_version": SCHEMA_VERSION,
        }))
    return 0


def _write_markdown(output_dir, body, screening, uncertainty):
    lines = []
    lines.append("# 全量筛选清单（screening）")
    lines.append("")
    lines.append("总数 %d；included %d；excluded %d；uncertain %d；merged_duplicates %d"
                 % (screening["counts"]["total"], screening["counts"]["included"],
                    screening["counts"]["excluded"], screening["counts"]["uncertain"],
                    screening["counts"]["merged_duplicates"]))
    lines.append("")
    for item in screening["ledger"]:
        lines.append("- [%s] %s (%s)" % (item["screening"], item.get("title") or "?", item.get("year") or "?"))
        if item.get("screening_reason"):
            lines.append("  - 理由：%s" % item["screening_reason"])
    for dup in screening["merged_duplicates"]:
        lines.append("- [merged_duplicate] 保留 `%s`，丢弃 `%s`（%s）" % (dup.get("kept"), dup.get("dropped"), dup.get("reason")))
    lines.append("")
    lines.append("# 检索计划（plan）")
    lines.append("")
    lines.append("## 研究问题")
    lines.append("")
    lines.append(str(body.get("research_question") or "（未给出）"))
    lines.append("")
    lines.append("## 子问题")
    lines.append("")
    for sq in body.get("sub_questions", []):
        lines.append("- %s" % json.dumps(sq, ensure_ascii=False))
    lines.append("")
    lines.append("## 术语扩展")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(body.get("term_expansion", {}), ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    lines.append("## 停止判断")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(body.get("stop_decision", {}), ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    lines.append("## 未覆盖子问题")
    lines.append("")
    for gap in uncertainty.get("coverage_gaps", []):
        lines.append("- %s" % gap)
    if not uncertainty.get("coverage_gaps"):
        lines.append("- （无）")
    with open(os.path.join(output_dir, "screening.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    plan_lines = []
    plan_lines.append("# 检索计划与停止判断")
    plan_lines.append("")
    plan_lines.append("## 研究问题")
    plan_lines.append("")
    plan_lines.append(str(body.get("research_question") or "（未给出）"))
    plan_lines.append("")
    plan_lines.append("## 子问题（sub_questions）")
    plan_lines.append("")
    plan_lines.append("```json")
    plan_lines.append(json.dumps(body.get("sub_questions", []), ensure_ascii=False, indent=2))
    plan_lines.append("```")
    plan_lines.append("")
    plan_lines.append("## 查询规划（query_plan）")
    plan_lines.append("")
    plan_lines.append("```json")
    plan_lines.append(json.dumps(body.get("query_plan", []), ensure_ascii=False, indent=2))
    plan_lines.append("```")
    plan_lines.append("")
    plan_lines.append("## 术语扩展（term_expansion）")
    plan_lines.append("")
    plan_lines.append("```json")
    plan_lines.append(json.dumps(body.get("term_expansion", {}), ensure_ascii=False, indent=2))
    plan_lines.append("```")
    plan_lines.append("")
    plan_lines.append("## 停止判断（stop_decision）")
    plan_lines.append("")
    plan_lines.append("```json")
    plan_lines.append(json.dumps(body.get("stop_decision", {}), ensure_ascii=False, indent=2))
    plan_lines.append("```")
    with open(os.path.join(output_dir, "plan.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(plan_lines) + "\n")


# --------------------------------------------------------------------------
# Self-check
# --------------------------------------------------------------------------

def cmd_selfcheck(args):
    path = os.path.join(args.output, "result.json")
    failures = []
    if not os.path.isfile(path):
        log("FAIL: result.json missing at %s" % path)
        return 1
    text, note = read_text_file(path)
    result, err = parse_json_text(text or "", path)
    if err or not isinstance(result, dict):
        log("FAIL: result.json is not valid JSON")
        return 1

    def check(cond, message):
        if cond:
            log("PASS: " + message)
        else:
            log("FAIL: " + message)
            failures.append(message)

    check(isinstance(result.get("case_id"), str) and result["case_id"], "case_id non-empty")
    check(result.get("status") in CANONICAL_STATUSES, "status in canonical set")
    check(isinstance(result.get("results"), list), "results is a list")
    check(isinstance(result.get("screening"), dict), "screening ledger present")
    check(isinstance(result.get("evidence"), list), "evidence log present")
    check(isinstance(result.get("sources"), list), "sources present")
    check(isinstance(result.get("uncertainty"), dict), "uncertainty present")
    check(isinstance(result.get("change_log"), list), "change_log present")

    for rec in result.get("results", []):
        if not isinstance(rec, dict):
            check(False, "result entry is an object")
            continue
        if rec.get("screening") == "included":
            check(bool(rec.get("title")), "included result has title: %s" % (rec.get("title") or "?"))
            ev = rec.get("evidence") or []
            check(bool(ev), "included result has evidence: %s" % (rec.get("title") or "?"))
    for item in result.get("screening", {}).get("ledger", []):
        if item.get("screening") in ("excluded", "uncertain"):
            check(bool(item.get("screening_reason")), "excluded/uncertain record has reason: %s" % (item.get("title") or "?"))
    return 1 if failures else 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default="/input", help="input directory (default /input)")
    common.add_argument("--output", default="/app/output", help="output directory (default /app/output)")
    common.add_argument("--work", default="/app/work", help="work/state directory (default /app/work)")

    parser = argparse.ArgumentParser(prog="main.py", parents=[common],
                                     description="Deterministic research-question-driven search engine")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", parents=[common], help="scan input and write a partial result skeleton (run once, first)")
    p = sub.add_parser("set-plan", parents=[common], help="update one plan/state component")
    p.add_argument("--kind", required=True, choices=PLAN_KINDS)
    p.add_argument("--file", help="JSON file payload")
    p.add_argument("--inline", help="inline JSON payload (short, single line)")
    p = sub.add_parser("import", parents=[common], help="ingest candidate records (dedup + merge)")
    p.add_argument("--file", help="JSON file payload")
    p.add_argument("--inline", help="inline JSON payload")
    p = sub.add_parser("record-evidence", parents=[common], help="append evidence entries")
    p.add_argument("--file", help="JSON file payload")
    p.add_argument("--inline", help="inline JSON payload")
    p = sub.add_parser("assemble", parents=[common], help="rebuild result.json + screening.md + plan.md deterministically")
    p.add_argument("--runtime-seconds", type=float, default=None)
    p.add_argument("--runtime-requests", type=int, default=None)
    sub.add_parser("selfcheck", parents=[common], help="validate result.json against the schema")
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "init":
        return cmd_init(args)
    if args.command == "set-plan":
        return cmd_set_plan(args)
    if args.command == "import":
        return cmd_import(args)
    if args.command == "record-evidence":
        return cmd_record_evidence(args)
    if args.command == "assemble":
        return cmd_assemble(args)
    if args.command == "selfcheck":
        return cmd_selfcheck(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
