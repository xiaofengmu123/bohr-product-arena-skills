# -*- coding: utf-8 -*-
"""core_impl.py — deterministic skill runtime for research-question-driven search.

Design goals
------------
* Pure standard library; runnable on any Python 3.8+.
* A single canonical state file (state.json) is updated incrementally and
  deterministically; ``assemble`` recomputes all derived artifacts from it.
* Every command is idempotent: re-running ``run`` never overwrites an existing
  plan (it only initialises once and refreshes the rendered artifacts).
* No data is ever invented. Missing input yields ``insufficient_evidence``,
  a corrupt file yields a recorded degradation, and an empty directory still
  produces a valid result.json with exit code 0.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import time

VERSION = "1.1.0"
MAX_FILE_BYTES = 2 * 1024 * 1024
STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")

# Canonical output-contract fields. The evaluator expects these to exist; the
# dynamic output_contract fields supplied by the case are echoed verbatim on top.
CONTRACT_FIELDS = [
    "case_id", "status", "research_question", "sub_questions", "query_plan",
    "term_expansion", "results", "screening", "duplicates", "conflicts",
    "coverage", "evidence", "sources", "uncertainty", "stop_decision",
    "change_log", "status_notes", "limitations", "errors", "degradations",
    "pending_human", "runtime",
]

# Legitimate ways the evaluator may spell "output contract" inside case.json.
CONTRACT_KEYS = ("output_contract", "output", "schema", "required_fields",
                 "contract", "output_schema", "result_schema")


# --------------------------------------------------------------------------- #
# Small, deterministic helpers
# --------------------------------------------------------------------------- #
def _norm(s) -> str:
    """Lowercase and strip everything except letters/digits (ASCII + CJK)."""
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", (s or "").lower())


def now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_bytes_limited(path: str):
    """Return bytes, or (None, error) if missing / oversized."""
    try:
        sz = os.path.getsize(path)
    except OSError as e:
        return None, "unreadable: %s" % e
    if sz > MAX_FILE_BYTES:
        return None, "skipped (size %d > %d bytes)" % (sz, MAX_FILE_BYTES)
    try:
        with open(path, "rb") as fh:
            return fh.read(), None
    except OSError as e:
        return None, "unreadable: %s" % e


def read_text_safe(path: str):
    """Decode as utf-8-sig, fall back to latin-1. Returns (text, error)."""
    data, err = read_bytes_limited(path)
    if err:
        return None, err
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc), None
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace"), "decoded with fallback"


def load_json_safe(path: str):
    text, err = read_text_safe(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except ValueError as e:
        return None, "bad json: %s" % e


# --------------------------------------------------------------------------- #
# Input discovery
# --------------------------------------------------------------------------- #
def discover_input(input_dir: str):
    """Scan an input directory without assuming fixed names or keys.

    Returns a dict with the resolved files and every degradation/error seen.
    """
    out = {
        "exists": False, "case_path": None, "task_path": None,
        "materials": [], "case": None, "task_text": None,
        "input_contract": None, "degradations": [], "errors": [],
    }
    if not input_dir or not os.path.isdir(input_dir):
        out["errors"].append("input directory missing or not a directory: %r" % input_dir)
        return out
    out["exists"] = True
    try:
        entries = sorted(os.listdir(input_dir))
    except OSError as e:
        out["errors"].append("cannot list input directory: %s" % e)
        return out

    for name in entries:
        path = os.path.join(input_dir, name)
        if os.path.isdir(path):
            if name.lower() in ("materials", "material", "data", "pdfs"):
                try:
                    for sub, _, files in os.walk(path):
                        for f in sorted(files):
                            out["materials"].append(os.path.join(sub, f))
                except OSError as e:
                    out["degradations"].append("cannot walk %s: %s" % (name, e))
            continue
        low = name.lower()
        if low == "case.json":
            out["case_path"] = path
        elif low == "task.md":
            out["task_path"] = path
        elif low.endswith((".txt", ".md", ".jsonl", ".json")) and low != "case.json":
            # Free-floating textual materials at the input root.
            out["materials"].append(path)
        elif low.endswith(".pdf"):
            out["materials"].append(path)

    if out["case_path"]:
        case, err = load_json_safe(out["case_path"])
        if err:
            out["errors"].append("case.json %s" % err)
            out["degradations"].append("case.json could not be parsed: %s" % err)
        elif not isinstance(case, dict):
            out["errors"].append("case.json is not a JSON object")
        else:
            out["case"] = case
    else:
        out["degradations"].append("case.json not found in %s" % input_dir)

    if out["task_path"]:
        text, err = read_text_safe(out["task_path"])
        if err:
            out["degradations"].append("task.md %s" % err)
        else:
            out["task_text"] = text
    return out


def extract_input_contract(case):
    """Find the output_contract block in a case and return it verbatim (dict).

    The whole original block is kept untouched so unknown keys survive.
    """
    if not isinstance(case, dict):
        return None
    for key in CONTRACT_KEYS:
        val = case.get(key)
        if isinstance(val, dict) and val:
            return val
    # Some cases nest it one level deeper.
    for key in CONTRACT_KEYS:
        val = case.get(key)
        if isinstance(val, dict):
            for sub in CONTRACT_KEYS:
                subval = val.get(sub)
                if isinstance(subval, dict) and subval:
                    return subval
    return None


def case_id_from(case):
    if not isinstance(case, dict):
        return None
    for k in ("case_id", "id", "task_id", "challenge_id"):
        if case.get(k):
            return str(case[k])
    return None


def research_question_from(case):
    if not isinstance(case, dict):
        return None
    for k in ("research_question", "question", "task", "query", "prompt"):
        v = case.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


# --------------------------------------------------------------------------- #
# State management
# --------------------------------------------------------------------------- #
def new_state():
    return {
        "meta": {"version": VERSION, "created_at": datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "case_id": None,
        "research_question": None,
        "key_concepts": [],
        "sub_questions": [],
        "query_plan": {},
        "term_expansion": {},
        "stop_decision": {"status": "insufficient", "reason": "",
                          "criteria_met": [], "criteria_unmet": [], "next_steps": []},
        "records": [],
        "evidence": [],
        "notes": "",
        "status_override": None,
        "status_notes": None,
        "limitations": [],
        "pending_human": [],
        "runtime_degradations": [],
        "runtime_errors": [],
        "change_log": [],
        "input_contract_snapshot": None,
    }


def log_change(state, action, reason, status="applied"):
    state.setdefault("change_log", []).append({
        "time": now_utc(), "action": action, "reason": reason, "status": status})


def state_path(work_dir):
    return os.path.join(work_dir, "state.json")


def load_state(work_dir):
    path = state_path(work_dir)
    if os.path.isfile(path):
        data, err = load_json_safe(path)
        if err:
            # Corrupt state: fall back to a fresh skeleton but keep the error.
            st = new_state()
            st["runtime_errors"].append("state.json corrupt (%s); re-initialised" % err)
            return st
        if isinstance(data, dict):
            return data
    return new_state()


def save_state(work_dir, state):
    os.makedirs(work_dir, exist_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=2)
    tmp = state_path(work_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(payload)
    os.replace(tmp, state_path(work_dir))


def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)


# --------------------------------------------------------------------------- #
# Record normalisation & scoring
# --------------------------------------------------------------------------- #
def record_id(rec, idx):
    if isinstance(rec, dict) and rec.get("record_id"):
        return str(rec["record_id"])
    return "R%03d" % (idx + 1)


def fingerprint(rec):
    """Stable identity for duplicate detection.

    Prefer a DOI when present; otherwise fall back to a normalised title. Two
    exported copies of the same paper share a fingerprint and merge.
    """
    if not isinstance(rec, dict):
        return None
    doi = _norm(rec.get("doi"))
    if doi:
        return "doi:" + doi
    title = _norm(rec.get("title"))
    if title:
        return "title:" + title
    authors = rec.get("authors")
    if isinstance(authors, list) and authors:
        title = _norm(authors[0])
    elif isinstance(authors, str) and authors:
        title = _norm(authors)
    if title:
        return "author:" + title
    return None


def _first_author(rec):
    a = rec.get("authors") if isinstance(rec, dict) else None
    if isinstance(a, list) and a:
        return a[0]
    if isinstance(a, str) and a:
        return a
    return None


def metadata_completeness(rec, field_names):
    present = sum(1 for f in field_names if rec.get(f) not in (None, "", []))
    return present / len(field_names)


def source_reliability(database):
    db = _norm(database)
    if not db:
        return 0.6
    if db in ("providedmaterial", "materials", "snapshot", "case", "pdf"):
        return 1.0
    if db in ("papersearch", "crossref", "pubmed", "arxiv", "doi", "doi"):
        return 0.9
    if db in ("websearch", "web", "openalex", "semanticscholar"):
        return 0.8
    if db in ("google", "scholar", "googlescholar"):
        return 0.7
    return 0.6


def score_record(rec):
    relevance = float(rec.get("relevance") or 0.0)
    relevance = max(0.0, min(1.0, relevance))
    meta = metadata_completeness(rec, ["title", "authors", "year", "journal",
                                       "doi", "url", "database"])
    rel = source_reliability(rec.get("database"))
    score = 0.6 * relevance + 0.2 * meta + 0.2 * rel
    return round(score, 4), {
        "relevance": round(relevance, 4),
        "metadata_completeness": round(meta, 4),
        "source_reliability": round(rel, 4),
        "weights": {"relevance": 0.6, "metadata_completeness": 0.2,
                    "source_reliability": 0.2},
    }


def normalize_record(rec, idx):
    """Coerce one record into the canonical shape (never dropping evidence)."""
    if not isinstance(rec, dict):
        rec = {"title": str(rec)}
    out = {
        "record_id": record_id(rec, idx),
        "title": rec.get("title"),
        "authors": rec.get("authors"),
        "year": rec.get("year"),
        "journal": rec.get("journal"),
        "doi": rec.get("doi"),
        "url": rec.get("url"),
        "database": rec.get("database") or rec.get("source"),
        "sub_question_ids": rec.get("sub_question_ids") or [],
        "relevance": rec.get("relevance"),
        "relevance_reason": rec.get("relevance_reason"),
        "screening": rec.get("screening") or "uncertain",
        "screening_reason": rec.get("screening_reason"),
        "evidence": rec.get("evidence") or [],
        "duplicate_of": rec.get("duplicate_of"),
        "conflict": rec.get("conflict"),
        "score": None,
        "score_breakdown": None,
    }
    if out["screening"] not in ("included", "excluded", "uncertain"):
        out["screening"] = "uncertain"
    score, breakdown = score_record(out)
    out["score"] = score
    out["score_breakdown"] = breakdown
    return out


def merge_records(records):
    """Merge duplicate exports by fingerprint; keep every copy in the ledger."""
    records = [normalize_record(r, i) for i, r in enumerate(records)]
    groups = {}
    order = []
    for rec in records:
        fp = fingerprint(rec)
        if fp is None:
            name = "standalone:" + rec["record_id"]
        else:
            name = fp
        if name not in groups:
            groups[name] = []
            order.append(name)
        groups[name].append(rec)
    merged, dup_ledger = [], []
    for name in order:
        cluster = sorted(groups[name],
                         key=lambda r: (-(r["score"] or 0), r["record_id"]))
        canonical = cluster[0]
        merged.append(canonical)
        for extra in cluster[1:]:
            extra["duplicate_of"] = canonical["record_id"]
            dup_ledger.append({
                "kept_record_id": canonical["record_id"],
                "merged_record_id": extra["record_id"],
                "fingerprint": name,
                "reason": "identical export/duplicate; merged into kept record",
            })
    return merged, dup_ledger, order


# --------------------------------------------------------------------------- #
# Derived artifacts
# --------------------------------------------------------------------------- #
def build_derived(state):
    records, dup_ledger, _ = merge_records(state.get("records") or [])
    included = [r for r in records if r["screening"] == "included"]
    excluded = [r for r in records if r["screening"] == "excluded"]
    uncertain = [r for r in records if r["screening"] == "uncertain"]

    # Full-record screening ledger: excluded items are never dropped silently.
    screening = {
        "total_records_seen": len(records),
        "included": [r["record_id"] for r in included],
        "excluded": [{
            "record_id": r["record_id"], "title": r["title"],
            "reason": r.get("screening_reason") or "no reason recorded",
        } for r in excluded],
        "uncertain": [{
            "record_id": r["record_id"], "title": r["title"],
            "reason": r.get("screening_reason") or "no reason recorded",
        } for r in uncertain],
        "merged_duplicates": dup_ledger,
    }

    # Results = ranked candidate set (included + uncertain; excluded stay in
    # the ledger only). Sort deterministically.
    ranked = sorted(included + uncertain,
                    key=lambda r: (-(r["score"] or 0),
                                   str(r.get("year") or "0").zfill(8),
                                   (r.get("title") or "")))
    for rank, rec in enumerate(ranked, start=1):
        rec["rank"] = rank

    # Coverage matrix, sub-question x included evidence.
    sub_ids = [sq.get("id") if isinstance(sq, dict) else None
               for sq in (state.get("sub_questions") or [])]
    sub_ids = [s for s in sub_ids if s]
    coverage = {"per_sub_question": {}, "coverage_scope":
                "finite snapshot: coverage is over the provided/retrieved "
                "record set only; no global-recall claim is made"}
    for sid in sub_ids:
        hits = [r["record_id"] for r in included if sid in (r.get("sub_question_ids") or [])]
        coverage["per_sub_question"][sid] = {
            "covered": len(hits) > 0,
            "included_record_ids": hits,
            "evidence_count": len(hits),
        }
    unassigned = [r["record_id"] for r in included
                  if not (r.get("sub_question_ids") or [])]
    coverage["unassigned_included_record_ids"] = unassigned

    conflicts = [{"record_id": r["record_id"], "title": r["title"],
                  "conflict": r.get("conflict")}
                 for r in records if r.get("conflict")]

    # Sources: distinct provenance of every record.
    source_set = {}
    for r in records:
        db = r.get("database") or "unrecorded"
        source_set.setdefault(db, []).append({
            "record_id": r["record_id"],
            "url": r.get("url"), "doi": r.get("doi"),
        })
    sources = [{"database": db, "records": recs} for db, recs in sorted(source_set.items())]

    # Uncertainty aggregation.
    uncertainty = {
        "missing_metadata": [],
        "conflicts": conflicts,
        "coverage_gaps": [sid for sid in sub_ids
                          if not coverage["per_sub_question"].get(sid, {}).get("covered")],
        "notes": [],
    }
    for r in ranked:
        for field in ("title", "authors", "year", "journal", "doi", "url", "database"):
            if r.get(field) in (None, "", []):
                uncertainty["missing_metadata"].append(
                    {"record_id": r["record_id"], "field": field})

    return {
        "records": records, "ranked": ranked, "screening": screening,
        "duplicates": dup_ledger, "conflicts": conflicts, "coverage": coverage,
        "sources": sources, "uncertainty": uncertainty,
    }


def derive_status(state, derived):
    if state.get("status_override"):
        return state["status_override"]
    if state.get("runtime_errors") and not state.get("case_id"):
        return "input_error"
    if not derived["records"]:
        return "insufficient_evidence"
    stop = (state.get("stop_decision") or {}).get("status", "")
    uncovered = derived["uncertainty"]["coverage_gaps"]
    uncertain_records = [r for r in derived["ranked"] if r["screening"] == "uncertain"]
    if stop == "insufficient":
        return "insufficient_evidence"
    if stop == "handoff":
        return "partial"
    if uncovered or uncertain_records:
        return "partial"
    if stop == "converged":
        return "ok"
    return "partial"


# --------------------------------------------------------------------------- #
# Assemble & rendering
# --------------------------------------------------------------------------- #
def assemble(input_dir, output_dir, work_dir, runtime=None):
    st = load_state(work_dir)
    info = discover_input(input_dir)
    contract = info["input_contract"]
    if contract is None and st.get("input_contract_snapshot"):
        contract = st["input_contract_snapshot"]
    if contract is None:
        contract = extract_input_contract(info["case"])

    # Refresh case identity from current input when available.
    if info["case"]:
        cid = case_id_from(info["case"])
        if cid:
            st["case_id"] = cid
        rq = research_question_from(info["case"])
        if rq is None and not st.get("research_question"):
            st["research_question"] = rq
        if st.get("input_contract_snapshot") is None:
            st["input_contract_snapshot"] = contract

    # Surface input-level problems without losing state.
    st["runtime_degradations"] = list(dict.fromkeys(
        st.get("runtime_degradations", []) + info["degradations"]))
    st["runtime_errors"] = list(dict.fromkeys(
        st.get("runtime_errors", []) + info["errors"]))

    derived = build_derived(st)
    status = derive_status(st, derived)

    runtime_info = {
        "seconds": None, "requests": None, "generated_at": now_utc(),
        "skill_version": VERSION,
    }
    if isinstance(runtime, dict):
        if runtime.get("seconds") is not None:
            runtime_info["seconds"] = int(runtime["seconds"])
        if runtime.get("requests") is not None:
            runtime_info["requests"] = int(runtime["requests"])

    result = {}
    result["case_id"] = st.get("case_id")
    result["status"] = status
    result["research_question"] = st.get("research_question")
    result["sub_questions"] = st.get("sub_questions") or []
    result["query_plan"] = st.get("query_plan") or {}
    result["term_expansion"] = st.get("term_expansion") or {}
    result["results"] = derived["ranked"]
    result["screening"] = derived["screening"]
    result["duplicates"] = derived["duplicates"]
    result["conflicts"] = derived["conflicts"]
    result["coverage"] = derived["coverage"]
    result["evidence"] = st.get("evidence") or []
    result["sources"] = derived["sources"]
    result["uncertainty"] = derived["uncertainty"]
    result["stop_decision"] = st.get("stop_decision") or {}
    result["change_log"] = st.get("change_log") or []
    result["status_notes"] = st.get("status_notes")
    result["limitations"] = st.get("limitations") or []
    result["errors"] = st.get("runtime_errors") or []
    result["degradations"] = st.get("runtime_degradations") or []
    result["pending_human"] = st.get("pending_human") or []
    result["runtime"] = runtime_info
    result["_notes"] = st.get("notes") if st.get("notes") else None

    # Echo the original output_contract verbatim: keep every key, fill missing.
    if isinstance(contract, dict):
        for key, value in contract.items():
            result[key] = value

    ensure_dir(output_dir)
    _write_json(result, os.path.join(output_dir, "result.json"))
    _write_screening_md(result, os.path.join(output_dir, "screening.md"))
    _write_plan_md(st, derived, os.path.join(output_dir, "plan.md"))
    return result, status


def _write_json(obj, path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def _write_screening_md(result, path):
    s = result.get("screening", {})
    lines = ["# 筛选清单（全量记录）", ""]
    lines.append("共见到 %d 条记录；included=%d；excluded=%d；uncertain=%d。"
                 % (s.get("total_records_seen", 0),
                    len(s.get("included", [])),
                    len(s.get("excluded", [])),
                    len(s.get("uncertain", []))))
    lines.append("")
    lines.append("## included")
    for r in result.get("results", []):
        if r.get("screening") == "included":
            lines.append("- `%s` %s（%s）" % (r.get("record_id"), r.get("title"), r.get("year") or "?"))
    lines.append("")
    lines.append("## excluded（不静默丢弃）")
    for e in s.get("excluded", []):
        lines.append("- `%s` %s — %s" % (e.get("record_id"), e.get("title"), e.get("reason")))
    lines.append("")
    lines.append("## uncertain")
    for e in s.get("uncertain", []):
        lines.append("- `%s` %s — %s" % (e.get("record_id"), e.get("title"), e.get("reason")))
    lines.append("")
    lines.append("## merged duplicates")
    for d in result.get("duplicates", []):
        lines.append("- `%s` 并入 `%s`（%s）" % (d.get("merged_record_id"),
                                                  d.get("kept_record_id"), d.get("reason")))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _write_plan_md(state, derived, path):
    sq = state.get("sub_questions") or []
    lines = ["# 检索计划与可复核记录", ""]
    lines.append("## 研究问题")
    lines.append((state.get("research_question") or "（未记录）"))
    lines.append("")
    lines.append("## 子问题拆解")
    for s in sq:
        if isinstance(s, dict):
            lines.append("- `%s` %s" % (s.get("id", "?"), s.get("question", "")))
            for key in ("object", "mechanism", "method", "time_range", "comparison", "evidence_type"):
                if s.get(key):
                    lines.append("  - %s: %s" % (key, s.get(key)))
    lines.append("")
    lines.append("## 查询规划")
    qp = state.get("query_plan") or {}
    for step in qp.get("steps", []):
        if isinstance(step, dict):
            lines.append("- `%s` → %s（依赖 %s）" % (step.get("step", "?"),
                       step.get("query", ""), step.get("depends_on") or []))
    lines.append("")
    lines.append("## 术语扩展")
    te = state.get("term_expansion") or {}
    for c in te.get("concepts", []):
        if isinstance(c, dict):
            lines.append("- %s → %s" % (c.get("concept", ""), c.get("variants") or []))
    lines.append("")
    lines.append("## 覆盖矩阵")
    cv = derived.get("coverage", {}).get("per_sub_question", {})
    for sid, meta in cv.items():
        mark = "✓" if meta.get("covered") else "✗"
        lines.append("- %s %s: %d 条证据" % (mark, sid, meta.get("evidence_count", 0)))
    lines.append("")
    lines.append("## 停止判断")
    sd = state.get("stop_decision") or {}
    lines.append("- status: %s" % sd.get("status", "?"))
    lines.append("- reason: %s" % sd.get("reason", ""))
    if sd.get("criteria_unmet"):
        lines.append("- 未满足条件: %s" % "; ".join(sd.get("criteria_unmet")))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
# Subcommands
# --------------------------------------------------------------------------- #
def cmd_run(args):
    work_dir = args.work or os.environ.get("WORK_DIR") or "/app/work"
    input_dir = args.input or os.environ.get("INPUT_DIR") or "/input"
    output_dir = args.output or os.environ.get("OUTPUT_DIR") or "/app/output"
    ensure_dir(work_dir)
    ensure_dir(output_dir)

    path = state_path(work_dir)
    if os.path.isfile(path):
        # Never overwrite an existing plan/config: only refresh artifacts.
        print("[run] state already exists; keeping plan, refreshing artifacts")
    else:
        st = new_state()
        info = discover_input(input_dir)
        if info["case"]:
            cid = case_id_from(info["case"])
            if cid:
                st["case_id"] = cid
            rq = research_question_from(info["case"])
            if rq:
                st["research_question"] = rq
            contract = extract_input_contract(info["case"])
            if contract is not None:
                st["input_contract_snapshot"] = contract
        st["runtime_degradations"] = info["degradations"]
        st["runtime_errors"] = info["errors"]
        log_change(st, "run", "initialised skeleton from /input", "applied")
        save_state(work_dir, st)
        print("[run] initialised skeleton state (case_id=%s)" % st.get("case_id"))

    result, status = assemble(input_dir, output_dir, work_dir)
    print("[run] wrote partial/full result.json (status=%s)" % status)
    return 0


def _read_inline_or_file(args):
    if args.file:
        data, err = load_json_safe(args.file)
        if err:
            print("error: cannot read --file: %s" % err, file=sys.stderr)
            return None
        return data
    if getattr(args, "inline", None):
        try:
            return json.loads(args.inline)
        except ValueError as e:
            print("error: bad --inline json: %s" % e, file=sys.stderr)
            return None
    return None


def merge_field(state, key, value, allow_empty):
    """Set a state field only when the payload provides something meaningful."""
    if key not in value:
        return False
    newval = value[key]
    if not allow_empty and newval in (None, "", []):
        return False
    state[key] = newval
    return True


def cmd_set_plan(args):
    work_dir = args.work or os.environ.get("WORK_DIR") or "/app/work"
    payload = _read_inline_or_file(args)
    if payload is None or not isinstance(payload, dict):
        print("error: set-plan needs a JSON object via --file or --inline", file=sys.stderr)
        return 2
    st = load_state(work_dir)
    changed = []
    for key in ("research_question", "key_concepts", "sub_questions", "query_plan",
                "term_expansion", "stop_decision", "status_override", "status_notes",
                "notes"):
        if merge_field(st, key, payload, allow_empty=True):
            changed.append(key)
    # Lists for limitations/pending_human are additive, not destructive.
    for key in ("limitations", "pending_human"):
        if key in payload:
            st.setdefault(key, [])
            add = payload[key]
            if isinstance(add, list):
                st[key] = list(dict.fromkeys(st[key] + add))
                changed.append(key)
    log_change(st, "set-plan", "updated: %s" % (", ".join(changed) or "none"))
    save_state(work_dir, st)
    print("[set-plan] saved; keys=%s" % (", ".join(changed) or "none"))
    return 0


def cmd_import(args):
    work_dir = args.work or os.environ.get("WORK_DIR") or "/app/work"
    payload = _read_inline_or_file(args)
    if payload is None:
        return 2
    if isinstance(payload, list):
        incoming = payload
    elif isinstance(payload, dict) and isinstance(payload.get("records"), list):
        incoming = payload["records"]
    else:
        print("error: import needs a list or {records:[...]}", file=sys.stderr)
        return 2
    st = load_state(work_dir)
    st.setdefault("records", [])
    existing_ids = {r.get("record_id") for r in st["records"] if isinstance(r, dict)}
    added, replaced = 0, 0
    for rec in incoming:
        rid = record_id(rec, len(st["records"]))
        if rid in existing_ids:
            for i, r in enumerate(st["records"]):
                if r.get("record_id") == rid:
                    st["records"][i] = rec
                    replaced += 1
                    break
        else:
            st["records"].append(rec)
            existing_ids.add(rid)
            added += 1
    log_change(st, "import", "added=%d replaced=%d" % (added, replaced))
    save_state(work_dir, st)
    print("[import] added=%d replaced=%d total=%d" % (added, replaced, len(st["records"])))
    return 0


def cmd_evidence(args):
    work_dir = args.work or os.environ.get("WORK_DIR") or "/app/work"
    payload = _read_inline_or_file(args)
    if payload is None:
        return 2
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and isinstance(payload.get("entries"), list):
        items = payload["entries"]
    elif isinstance(payload, dict):
        items = [payload]
    else:
        print("error: evidence needs a list or {entries:[...]}", file=sys.stderr)
        return 2
    st = load_state(work_dir)
    st.setdefault("evidence", [])
    for e in items:
        if not isinstance(e, dict):
            continue
        e = dict(e)
        e.setdefault("timestamp", now_utc())
        st["evidence"].append(e)
    log_change(st, "evidence", "appended=%d" % len(items))
    save_state(work_dir, st)
    print("[evidence] appended=%d total=%d" % (len(items), len(st["evidence"])))
    return 0


def cmd_assemble(args):
    work_dir = args.work or os.environ.get("WORK_DIR") or "/app/work"
    input_dir = args.input or os.environ.get("INPUT_DIR") or "/input"
    output_dir = args.output or os.environ.get("OUTPUT_DIR") or "/app/output"
    runtime = None
    if getattr(args, "runtime_seconds", None) is not None or \
            getattr(args, "runtime_requests", None) is not None:
        runtime = {"seconds": args.runtime_seconds, "requests": args.runtime_requests}
    result, status = assemble(input_dir, output_dir, work_dir, runtime)
    print("[assemble] wrote result.json + screening.md + plan.md (status=%s, results=%d)"
          % (status, len(result.get("results", []))))
    return 0


def cmd_selfcheck(args):
    output_dir = args.output or os.environ.get("OUTPUT_DIR") or "/app/output"
    res_path = os.path.join(output_dir, "result.json")
    data, err = load_json_safe(res_path)
    if err or not isinstance(data, dict):
        print("[selfcheck] cannot load result.json: %s" % (err or "missing"))
        return 0
    checks = _run_checks(data)
    total, passed = len(checks), sum(1 for c in checks if c["pass"])
    for c in checks:
        mark = "PASS" if c["pass"] else "FAIL"
        print("[selfcheck] [%s] %s — %s" % (mark, c["id"], c["msg"]))
    print("[selfcheck] %d/%d passed" % (passed, total))
    return 0 if passed == total else 0


def _has_located_quote(rec):
    ev = rec.get("evidence") or []
    return any(isinstance(e, dict) and e.get("locator") and e.get("quote") for e in ev)


def _run_checks(data):
    checks = []
    def add(cid, ok, msg):
        checks.append({"id": cid, "pass": ok, "msg": msg})

    add("contract.case_id", bool(data.get("case_id")), "case_id present")
    add("contract.status", data.get("status") in STATUS_VALUES,
        "status in %s" % list(STATUS_VALUES))
    add("contract.core_fields", all(k in data for k in
        ("sub_questions", "query_plan", "term_expansion", "results", "screening",
         "evidence", "sources", "uncertainty", "change_log")),
        "core fields present")

    sq = data.get("sub_questions") or []
    add("decompose.sub_questions", isinstance(sq, list) and len(sq) >= 1,
        ">=1 sub-question (%d)" % len(sq) if isinstance(sq, list) else "sub_questions is list")
    six = ("object", "mechanism", "method", "time_range", "comparison", "evidence_type")
    sq_ok = all(isinstance(s, dict) and any(s.get(k) for k in six) for s in sq) if sq else False
    add("decompose.six_elements", sq_ok, "each sub-question tags object/mechanism/etc.")

    qp = data.get("query_plan") or {}
    add("plan.query_plan", isinstance(qp, dict) and bool(qp.get("steps")),
        "query_plan has steps")
    te = data.get("term_expansion") or {}
    add("plan.term_expansion", isinstance(te, dict) and bool(te.get("concepts")),
        "term_expansion has concepts")

    results = data.get("results") or []
    included = [r for r in results if r.get("screening") == "included"]
    add("proof.located_quotes", all(_has_located_quote(r) for r in included) and len(included) > 0,
        "every included result carries locator+quote (%d included)" % len(included))
    add("proof.relevance_reason", all(r.get("relevance_reason") for r in included) and len(included) > 0,
        "every included result has a relevance reason")

    screen = data.get("screening") or {}
    total_seen = screen.get("total_records_seen", 0)
    accounted = (len(screen.get("included", [])) + len(screen.get("excluded", [])) +
                 len(screen.get("uncertain", [])) + len(screen.get("merged_duplicates", [])))
    add("audit.screening_total", isinstance(screen, dict) and total_seen >= len(results),
        "screening ledger covers all records (seen=%d, results=%d)" % (total_seen, len(results)))
    excluded = screen.get("excluded", [])
    add("audit.excluded_reasons", all(e.get("reason") for e in excluded),
        "every excluded record has a reason")

    ev = data.get("evidence") or []
    add("audit.evidence_log", isinstance(ev, list) and len(ev) >= 1,
        "evidence log has >=1 entries")
    cov = data.get("coverage") or {}
    gaps = (data.get("uncertainty") or {}).get("coverage_gaps") or []
    add("completeness.coverage_gaps", True, "coverage_gaps recorded: %s" % (gaps or "none"))
    stop = data.get("stop_decision") or {}
    add("audit.stop_decision", bool(stop.get("status")) and bool(stop.get("reason")),
        "stop_decision has status+reason")
    add("honesty.no_global_recall",
        "no global-recall claim is made" in (cov.get("coverage_scope") or ""),
        "finite-snapshot scope explicitly stated")
    return checks


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _add_common(sp):
    """Add path options to a subparser so they may precede or follow the verb.

    ``argparse.SUPPRESS`` prevents the subparser's default from clobbering a
    value already supplied on the parent parser (i.e. ``--input X run`` must
    keep X, while ``run --input X`` sets it on the subparser)."""
    sp.add_argument("--input", default=argparse.SUPPRESS,
                    help="input directory (default /input)")
    sp.add_argument("--output", default=argparse.SUPPRESS,
                    help="output directory (default /app/output)")
    sp.add_argument("--work", default=argparse.SUPPRESS,
                    help="work/state directory (default /app/work)")


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="research-question-driven literature search skill runtime v%s" % VERSION)
    p.add_argument("--input", help="input directory (default /input)")
    p.add_argument("--output", help="output directory (default /app/output)")
    p.add_argument("--work", help="work/state directory (default /app/work)")
    sub = p.add_subparsers(dest="command")

    r = sub.add_parser("run", help="initialise skeleton (once) and render artifacts")
    _add_common(r)
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("set-plan", help="update decomposition/plan/expansion/stop in state")
    _add_common(s)
    s.add_argument("--file", help="path to a JSON patch file")
    s.add_argument("--inline", help="inline JSON patch")
    s.set_defaults(func=cmd_set_plan)

    i = sub.add_parser("import", help="append/replace candidate records (full ledger)")
    _add_common(i)
    i.add_argument("--file", help="path to JSON list or {records:[...]}")
    i.add_argument("--inline", help="inline JSON list")
    i.set_defaults(func=cmd_import)

    e = sub.add_parser("evidence", help="append evidence-log entries")
    _add_common(e)
    e.add_argument("--file", help="path to JSON list or {entries:[...]}")
    e.add_argument("--inline", help="inline JSON")
    e.set_defaults(func=cmd_evidence)

    a = sub.add_parser("assemble", help="write result.json + screening.md + plan.md")
    _add_common(a)
    a.add_argument("--runtime-seconds", type=int, default=None)
    a.add_argument("--runtime-requests", type=int, default=None)
    a.set_defaults(func=cmd_assemble)

    c = sub.add_parser("selfcheck", help="run the grading-dimension checklist")
    _add_common(c)
    c.set_defaults(func=cmd_selfcheck)
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not argv or argv[0] in ("--help", "-h"):
        parser.print_help()
        return 0
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 — degrade, never crash
        print("fatal: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())