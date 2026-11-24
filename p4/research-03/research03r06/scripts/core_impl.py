#!/usr/bin/env python3
"""Core implementation for the systematic-review-ledger Skill.

Standard library only. Holds the deterministic, auditable helpers shared by
``scripts/main.py`` and ``scripts/selftest.py``:

  * input discovery that does not assume fixed file names
  * text/JSON reading with encoding fallback and size caps
  * output-contract discovery and early-skeleton construction
  * deep merge for incremental, non-destructive updates
  * stable content-derived identifiers
  * deterministic PRISMA recount and cross-validation

No function here depends on the current working directory; every path is
passed in explicitly so the CLI can run from any location.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"

STATUSES = (STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR)

# Encoding fallback order for plain-text reads. latin-1 can never fail on
# bytes, so it is the guaranteed final fallback and is marked as a degradation.
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")

TEXT_EXTS = {".md", ".txt", ".csv", ".json", ".jsonl", ".xml", ".html",
             ".tex", ".bib", ".yaml", ".yml", ".rst"}
DOC_EXTS = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls",
            ".odt", ".ods", ".odp"}
IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff",
            ".svg", ".bmp"}
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB; larger files are truncated + recorded

# Default topic contract used when case.json has no explicit output_contract.
DEFAULT_CONTRACT = {
    "review_question": None,
    "subquestions": [],
    "search_strategy": None,
    "inclusion_criteria": [],
    "exclusion_criteria": [],
    "screening_records": [],
    "prisma_flow": None,
    "quality_appraisal": [],
    "synthesis": None,
    "evidence_map": [],
    "uncertainty": None,
}

# Honest/audit fields always present in result.json, independent of contract.
HONEST_FIELDS = {
    "status": STATUS_PARTIAL,
    "sources": [],
    "uncertainty": None,
    "deliverables": [],
    "change_log": [],
    "limitations": [],
    "errors": [],
    "degradations": [],
    "pending_human": [],
    "runtime_seconds": 0.0,
}

# Preferred top-level key order (unknown contract keys are appended sorted).
PREFERRED_ORDER = [
    "case_id", "status", "review_question", "subquestions",
    "search_strategy", "inclusion_criteria", "exclusion_criteria",
    "screening_records", "prisma_flow", "quality_appraisal", "quality_rubric",
    "evidence_map", "synthesis", "uncertainty", "sources", "deliverables",
    "change_log", "limitations", "errors", "degradations", "pending_human",
    "runtime_seconds",
]


def epoch_now() -> float:
    return time.time()


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def stable_id(prefix: str, text: str) -> str:
    """Content-derived identifier; the same input always yields the same id."""
    digest = hashlib.sha1((text or "").encode("utf-8", "replace")).hexdigest()[:12]
    return "%s-%s" % (prefix, digest)


def ensure_dir(path: str) -> list:
    try:
        os.makedirs(path, exist_ok=True)
        return []
    except OSError as exc:
        return ["cannot create directory %s: %s" % (path, exc)]


def read_text(path: str):
    """Return ``(text, errors, degradations)``; ``text`` is None on hard I/O error."""
    errors, degradations = [], []
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, ["cannot stat %s: %s" % (path, exc)], []
    if size > MAX_FILE_BYTES:
        degradations.append("file truncated at 2MB (%.1f MB): %s"
                            % (size / 1048576.0, path))
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        return None, ["cannot read %s: %s" % (path, exc)], degradations
    truncated = len(raw) > MAX_FILE_BYTES
    raw = raw[:MAX_FILE_BYTES]
    last_exc = None
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), errors, degradations
        except UnicodeDecodeError as exc:
            last_exc = exc
    # latin-1 cannot fail; treat as a degradation.
    degradations.append("latin-1 fallback decode for %s (%s)" % (path, last_exc))
    return raw.decode("latin-1"), errors, degradations


def load_json(path: str):
    """Return ``(obj, errors, degradations)``; ``obj`` is None on failure."""
    if not os.path.exists(path):
        return None, ["missing: %s" % path], []
    text, errors, degradations = read_text(path)
    if text is None:
        return None, errors, degradations
    try:
        return json.loads(text), errors, degradations
    except ValueError as exc:
        return None, errors + ["bad JSON in %s: %s" % (path, exc)], degradations


def read_jsonl(path: str):
    """Return ``(records, errors, degradations)`` for a JSONL file."""
    if not os.path.exists(path):
        return [], ["missing: %s" % path], []
    text, errors, degradations = read_text(path)
    if text is None:
        return [], errors, degradations
    records = []
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except ValueError as exc:
            errors.append("bad JSONL line %d in %s: %s" % (lineno, path, exc))
    return records, errors, degradations


def write_json(path: str, obj) -> list:
    """Atomic JSON write; returns a list of error strings (empty on success)."""
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
        return []
    except OSError as exc:
        return ["cannot write %s: %s" % (path, exc)]


def write_text(path: str, text: str) -> list:
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return []
    except OSError as exc:
        return ["cannot write %s: %s" % (path, exc)]


def discover_input(input_dir: str) -> dict:
    """Inventory the input directory without assuming fixed names."""
    inv = {
        "input_dir": input_dir,
        "exists": False,
        "total_files": 0,
        "total_bytes": 0,
        "case_json": None,
        "task_md": None,
        "materials": [],
        "others": [],
    }
    if not os.path.isdir(input_dir):
        return inv
    inv["exists"] = True
    entries = []
    for root, dirs, files in os.walk(input_dir):
        dirs[:] = sorted(d for d in dirs if d not in (".git", "__pycache__", ".cache"))
        for fn in sorted(files):
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, input_dir).replace(os.sep, "/")
            entries.append((rel, full))
    for rel, full in entries:
        try:
            size = os.path.getsize(full)
        except OSError:
            size = 0
        inv["total_files"] += 1
        inv["total_bytes"] += size
        base = os.path.basename(rel)
        ext = os.path.splitext(base)[1].lower()
        if inv["case_json"] is None and (base == "case.json" or rel.endswith("case.json")):
            inv["case_json"] = full
        elif inv["task_md"] is None and (base == "task.md" or rel.endswith("task.md")):
            inv["task_md"] = full
        elif ext in TEXT_EXTS or ext in DOC_EXTS or ext in IMG_EXTS:
            inv["materials"].append(rel)
        else:
            inv["others"].append(rel)
    return inv


def extract_case_id(obj) -> str:
    if not isinstance(obj, dict):
        return ""
    for key in ("case_id", "caseId", "id", "armiId", "challenge_id", "challengeId"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, int):
            return str(val)
    return ""


def build_contract(case_obj) -> dict:
    """Return the output-contract field map to fill in result.json.

    Precedence: ``case.json["output_contract"]`` (keys and value shapes
    preserved verbatim, unknown keys kept) -> topic default contract.
    """
    if isinstance(case_obj, dict):
        oc = case_obj.get("output_contract")
        if isinstance(oc, dict) and oc:
            return {str(k): v for k, v in oc.items()}
    return copy.deepcopy(DEFAULT_CONTRACT)


def default_value(sample):
    if isinstance(sample, list):
        return []
    if isinstance(sample, dict):
        return {}
    if isinstance(sample, bool):
        return False
    if isinstance(sample, (int, float)):
        return 0
    return None


def new_skeleton(case_id: str, contract: dict, note: str = "") -> dict:
    result = {}
    result["case_id"] = case_id or "unknown"
    ordered = [k for k in PREFERRED_ORDER if k in contract]
    extras = sorted(k for k in contract if k not in PREFERRED_ORDER)
    for key in ordered + extras:
        result[key] = default_value(contract[key])
    for key, val in HONEST_FIELDS.items():
        result[key] = copy.deepcopy(val)
    if note:
        result["change_log"].append({"ts": iso_now(), "action": "skeleton",
                                     "detail": note})
    return result


def deep_merge(dst: dict, src: dict) -> dict:
    """Recursively merge ``src`` into ``dst`` in place.

    Dicts merge recursively; lists and scalars are replaced by the incoming
    value so incremental updates can overwrite a whole list atomically.
    """
    for key, val in src.items():
        if isinstance(val, dict) and isinstance(dst.get(key), dict):
            deep_merge(dst[key], val)
        else:
            dst[key] = copy.deepcopy(val)
    return dst


def recompute_prisma(screening_records) -> dict:
    """Deterministically recount the screening funnel from raw records.

    ``identified`` counts title/abstract records; fulltext counts derive from
    title/abstract "include" decisions, so the funnel can never be out of sync
    with the ledger of decisions.
    """
    recs = [r for r in (screening_records or []) if isinstance(r, dict)]
    t1 = [r for r in recs if r.get("round") == "title_abstract"]
    t2 = [r for r in recs if r.get("round") == "fulltext"]
    t1_incl = [r for r in t1 if r.get("decision") == "include"]
    t1_excl = [r for r in t1 if r.get("decision") == "exclude"]
    t2_incl = [r for r in t2 if r.get("decision") == "include"]
    t2_excl = [r for r in t2 if r.get("decision") == "exclude"]
    dup = [r for r in recs if r.get("duplicate_of")]
    return {
        "identified": len(t1),
        "duplicates_removed": len(dup),
        "title_abstract_screened": len(t1),
        "title_abstract_excluded": len(t1_excl),
        "fulltext_screened": len(t1_incl),
        "fulltext_excluded": len(t2_excl),
        "included": len(t2_incl),
        "recount": True,
        "method": "deterministic recount from screening_records",
    }


def collect_sources(screening_records, evidence_records, synthesis=None) -> list:
    """Derive the ``sources`` list from decision and evidence records."""
    sources, seen = [], set()

    def add(sid, label, role):
        if not sid:
            return
        key = (str(sid), role)
        if key in seen:
            return
        seen.add(key)
        sources.append({"source_id": str(sid), "label": label or str(sid),
                        "role": role})

    for r in (screening_records or []):
        if isinstance(r, dict):
            add(r.get("source_id"), r.get("source_label"), "screened")
    for e in (evidence_records or []):
        if isinstance(e, dict):
            add(e.get("source_id"), e.get("source_label"), "evidence")
    if isinstance(synthesis, dict):
        for x in (synthesis.get("conflicts") or []):
            add(x.get("source_a"), x.get("source_a_label"), "conflict_source")
            add(x.get("source_b"), x.get("source_b_label"), "conflict_source")
    return sources


def compute_status(result, screening, evidence, synthesis, errors) -> str:
    if result.get("status") == STATUS_INPUT_ERROR:
        return STATUS_INPUT_ERROR
    if not result.get("review_question"):
        return STATUS_INSUFFICIENT if not screening else STATUS_PARTIAL
    syn = synthesis if isinstance(synthesis, dict) else {}
    has_plan = bool(result.get("review_question")) and \
        isinstance(result.get("search_strategy"), dict)
    has_screening = bool(screening)
    has_evidence = bool(evidence)
    has_conclusions = bool(syn.get("conclusions"))
    has_quality = bool(result.get("quality_appraisal"))
    if has_plan and has_screening and has_quality and has_evidence and has_conclusions:
        return STATUS_OK
    return STATUS_PARTIAL


def validate_plan(plan: dict) -> list:
    """Structural checks for /app/work/plan.json (written before screening)."""
    issues = []
    if not isinstance(plan, dict):
        return [{"sev": "error", "msg": "plan.json is not a JSON object"}]
    if not plan.get("review_question"):
        issues.append({"sev": "error", "msg": "plan.review_question empty"})
    subs = plan.get("subquestions") or []
    if not subs:
        issues.append({"sev": "warn",
                       "msg": "plan.subquestions empty (>=1 required, >=2 recommended)"})
    for s in subs:
        if not isinstance(s, dict) or not s.get("id") or not s.get("question"):
            issues.append({"sev": "warn", "msg": "subquestion missing id/question"})
        if not s.get("depends_on"):
            issues.append({"sev": "info", "msg": "subquestion %r missing depends_on" % s.get("id")})
    ss = plan.get("search_strategy")
    if not isinstance(ss, dict) or not ss.get("query_blocks"):
        issues.append({"sev": "warn", "msg": "search_strategy.query_blocks empty"})
    else:
        for b in ss.get("query_blocks", []):
            for f in ("id", "database", "query", "intent"):
                if not b.get(f):
                    issues.append({"sev": "warn",
                                   "msg": "query block %r missing %s" % (b.get("id"), f)})
    for name in ("inclusion_criteria", "exclusion_criteria"):
        crits = plan.get(name) or []
        if not crits:
            issues.append({"sev": "warn", "msg": "%s empty" % name})
        for c in crits:
            if not isinstance(c, dict) or not c.get("id") or not c.get("statement"):
                issues.append({"sev": "warn",
                               "msg": "%s entry missing id/statement" % name})
            if "priority" not in c:
                issues.append({"sev": "warn",
                               "msg": "%s %r missing priority" % (name, c.get("id"))})
    return issues


def validate_result(result: dict, contract: dict, work_dir=None) -> list:
    """Return ``(severity, message)`` issues; "error" severity fails selfcheck."""
    issues = []
    if not isinstance(result, dict):
        return [("error", "result.json is not a JSON object")]

    if not result.get("case_id"):
        issues.append(("error", "case_id empty"))
    st = result.get("status")
    if st not in STATUSES:
        issues.append(("error", "unknown status %r" % st))

    for key in contract:
        if key not in result:
            issues.append(("error", "contract field missing: %s" % key))
    for key in ("status", "sources", "uncertainty", "change_log"):
        if key not in result:
            issues.append(("error", "honest field missing: %s" % key))

    if st in (STATUS_INSUFFICIENT, STATUS_INPUT_ERROR):
        return issues  # nothing further is required of a degraded result

    if not result.get("review_question"):
        issues.append(("error", "review_question empty (status=%s)" % st))
    subs = result.get("subquestions") or []
    if not subs:
        issues.append(("warn", "subquestions empty; >=1 required, >=2 recommended"))

    ss = result.get("search_strategy")
    if isinstance(ss, dict) and ss.get("query_blocks"):
        for b in ss.get("query_blocks", []):
            for f in ("id", "database", "query", "intent"):
                if not b.get(f):
                    issues.append(("warn", "query block %r missing %s" % (b.get("id"), f)))
    else:
        issues.append(("warn", "search_strategy.query_blocks empty"))

    crit_ids = set()
    for name in ("inclusion_criteria", "exclusion_criteria"):
        for c in (result.get(name) or []):
            if not isinstance(c, dict):
                continue
            if not c.get("id") or not c.get("statement"):
                issues.append(("warn", "%s entry missing id/statement" % name))
            if c.get("id"):
                crit_ids.add(c["id"])

    records = result.get("screening_records") or []
    if result.get("protocol_frozen_at") is None and records:
        issues.append(("warn", "criteria not marked frozen before screening"))

    for r in records:
        if not isinstance(r, dict):
            continue
        if r.get("decision") == "exclude":
            if not r.get("reason"):
                issues.append(("error", "exclude record %r missing reason" % r.get("record_id")))
            cids = r.get("criterion_id") or []
            if not cids:
                issues.append(("error", "exclude record %r missing criterion_id" % r.get("record_id")))
            for cid in cids:
                if crit_ids and cid not in crit_ids:
                    issues.append(("warn", "criterion_id %r not in criteria" % cid))
        if r.get("round") not in ("title_abstract", "fulltext"):
            issues.append(("warn", "record %r has unknown round" % r.get("record_id")))
        if r.get("disagreement") == "unresolved":
            issues.append(("warn", "unresolved dual-judge disagreement at %r" % r.get("record_id")))

    flow = result.get("prisma_flow")
    recounted = recompute_prisma(records)
    if isinstance(flow, dict):
        for k in ("identified", "title_abstract_screened", "title_abstract_excluded",
                  "fulltext_screened", "fulltext_excluded", "included"):
            if flow.get(k) != recounted.get(k):
                issues.append(("error", "prisma_flow.%s=%r != recounted %r"
                               % (k, flow.get(k), recounted.get(k))))
    else:
        issues.append(("warn", "prisma_flow absent"))

    for a in (result.get("quality_appraisal") or []):
        if not isinstance(a, dict):
            continue
        if not a.get("study_id"):
            issues.append(("warn", "appraisal entry missing study_id"))
        for d in (a.get("dimensions") or []):
            if not d.get("basis_locator") or not d.get("quote"):
                issues.append(("warn", "appraisal %r dimension missing locator/quote"
                               % a.get("study_id")))
        if not a.get("overall_grade"):
            issues.append(("warn", "appraisal %r missing overall_grade" % a.get("study_id")))

    evmap = result.get("evidence_map") or []
    ev_ids = set()
    for e in evmap:
        if not isinstance(e, dict) or not e.get("evidence_id"):
            issues.append(("error", "evidence entry missing evidence_id"))
            continue
        ev_ids.add(e["evidence_id"])
        if not e.get("locator") or not e.get("quote"):
            issues.append(("error", "evidence %s missing locator/quote" % e["evidence_id"]))

    syn = result.get("synthesis")
    if isinstance(syn, dict):
        for c in (syn.get("conclusions") or []):
            ids = c.get("evidence_ids") or []
            if not ids:
                issues.append(("warn", "conclusion %r has no evidence_ids" % c.get("id")))
            for eid in ids:
                if ev_ids and eid not in ev_ids:
                    issues.append(("error", "conclusion %r evidence_id %r not in evidence_map"
                                   % (c.get("id"), eid)))
        for x in (syn.get("conflicts") or []):
            if not (x.get("source_a") and x.get("source_b")):
                issues.append(("warn", "conflict %r should name two sources" % x.get("id")))
        for g in (syn.get("gaps") or []):
            if not g.get("description") or not g.get("location"):
                issues.append(("warn", "gap %r missing description/location" % g.get("id")))
    else:
        issues.append(("warn", "synthesis absent"))
    return issues


def coverage_report(result: dict) -> dict:
    """Boolean coverage over the six required aspects of the topic."""
    syn = result.get("synthesis") if isinstance(result.get("synthesis"), dict) else {}
    return {
        "problem_decomposition": bool(result.get("review_question") and result.get("subquestions")),
        "search_strategy": bool((result.get("search_strategy") or {}).get("query_blocks")),
        "inclusion_exclusion": bool(result.get("inclusion_criteria") and result.get("exclusion_criteria")),
        "screening_records": bool(result.get("screening_records")),
        "quality_appraisal": bool(result.get("quality_appraisal")),
        "evidence_map": bool(result.get("evidence_map")),
        "conclusions": bool(syn.get("conclusions")),
        "conflicts": bool(syn.get("conflicts")),
        "gaps": bool(syn.get("gaps")),
        "uncertainty": result.get("uncertainty") not in (None, {}, []),
    }
