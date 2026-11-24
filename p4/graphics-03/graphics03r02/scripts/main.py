#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill orchestrator for biological mechanism/pathway figure production.

Deterministic, stdlib-only pipeline that enforces the "early skeleton, then
incremental improvement" contract. The first command an Agent must run is
`bootstrap`, which writes a valid partial result.json and an empty spec.json
even when inputs are missing or malformed. Every later command appends to the
change_log instead of overwriting prior work.

Subcommands:
    bootstrap   Write early partial result.json + empty spec.json (run first).
    validate    Validate/normalize spec.json; write a validation report.
    build       Build layered SVG from spec.json via build_svg.py.
    merge       Merge spec content (entities/relations/legend/data_mappings/
                sources/uncertainty) into result.json, append change_log.
    finalize    Set status/limitations and append change_log (honest close-out).
    check       Validate result.json contract + artifact existence/magic bytes.
    pipeline    bootstrap -> build -> check in one deterministic pass.

Exit codes: 0 ok, 1 blocking error, 2 warnings only.
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_svg import SCHEMA_VERSION, build_svg, validate_spec  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ALLOWED_STATUS = ("success", "partial", "failed", "insufficient_evidence", "input_error")
REQUIRED_RESULT_FIELDS = (
    "case_id", "status", "editable_source_files", "export_artifacts",
    "entities", "relations", "legend", "data_mappings", "sources",
    "uncertainty", "pending_human", "change_log",
)
LIST_FIELDS = (
    "editable_source_files", "export_artifacts", "entities", "relations",
    "data_mappings", "sources", "uncertainty", "pending_human", "change_log",
    "limitations", "errors", "degradations",
)


def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path):
    """Tolerant JSON read (BOM, bad encoding, bad JSON). Returns a dict with an
    _error key instead of raising, so bootstrap never crashes."""
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        return {"_error": str(e), "_path": path}


def write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def find_case_id(case):
    if not isinstance(case, dict):
        return "unknown"
    for key in ("case_id", "caseId", "challenge_id", "challengeId", "id", "armiId"):
        v = case.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "unknown"


def find_title(case, task_md=None):
    if isinstance(case, dict):
        for key in ("title", "name"):
            v = case.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    if task_md and isinstance(task_md, str):
        for line in task_md.splitlines():
            line = line.strip().lstrip("#").strip()
            if line and not line.startswith(("-", "|", ">", "`")):
                return line[:120]
    return ""


def append_change(result, phase, action, detail=""):
    log = result.setdefault("change_log", [])
    if not isinstance(log, list):
        log = []
        result["change_log"] = log
    log.append({"at": iso_now(), "phase": phase, "action": action, "detail": detail})
    return result


# --------------------------------------------------------------------------- #
# bootstrap
# --------------------------------------------------------------------------- #
def bootstrap(input_dir, output_dir, work_dir):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    case_path = os.path.join(input_dir, "case.json")
    case = read_json(case_path) if os.path.exists(case_path) else {}
    task_md = ""
    task_path = os.path.join(input_dir, "task.md")
    if os.path.exists(task_path):
        try:
            with open(task_path, "r", encoding="utf-8-sig", errors="replace") as f:
                task_md = f.read()[:20000]
        except OSError:
            task_md = ""
    case_id = find_case_id(case)
    title = find_title(case, task_md)
    now = iso_now()

    spec = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "title": title,
        "entities": [],
        "relations": [],
        "legend": {},
        "data_mappings": [],
        "sources": [],
        "uncertainty": [],
    }
    write_json(os.path.join(work_dir, "spec.json"), spec)

    result = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "title": title,
        "status": "partial",
        "editable_source_files": [],
        "export_artifacts": [],
        "entities": [],
        "relations": [],
        "legend": {},
        "data_mappings": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "limitations": ["bootstrap skeleton only; scientific content pending incremental fill"],
        "errors": [],
        "degradations": [],
        "runtime": {"started_at": now, "seconds": 0},
    }
    if isinstance(case, dict) and case.get("_error"):
        result["degradations"].append("case.json unreadable: %s" % case["_error"])
    append_change(result, "bootstrap", "skeleton written",
                  "early partial deliverable; status=partial, case_id=%s" % case_id)
    result_path = os.path.join(output_dir, "result.json")
    write_json(result_path, result)
    return result_path


# --------------------------------------------------------------------------- #
# build / validate
# --------------------------------------------------------------------------- #
def load_spec(work_dir):
    path = os.path.join(work_dir, "spec.json")
    if not os.path.exists(path):
        return None, path
    return read_json(path), path


def cmd_validate(work_dir, report_dir=None):
    spec, path = load_spec(work_dir)
    if spec is None:
        print("spec.json not found at %s" % path, file=sys.stderr)
        return 1
    errors, warnings = validate_spec(spec)
    report = {"errors": errors, "warnings": warnings, "spec": path}
    if report_dir is None:
        report_dir = work_dir
    os.makedirs(report_dir, exist_ok=True)
    write_json(os.path.join(report_dir, "spec.validation_report.json"), report)
    for e in errors:
        print("blocking: %s" % e, file=sys.stderr)
    for w in warnings:
        print("warning: %s" % w, file=sys.stderr)
    return 1 if errors else (2 if warnings else 0)


def cmd_build(work_dir, output_dir, size=None):
    spec, path = load_spec(work_dir)
    if spec is None:
        print("spec.json not found at %s" % path, file=sys.stderr)
        return 1
    svg_path = os.path.join(output_dir, "mechanism.svg")
    try:
        errors, warnings = build_svg(spec, svg_path, size=size)
    except ValueError as e:
        print("blocking spec error: %s" % e, file=sys.stderr)
        return 1
    for w in warnings:
        print("warning: %s" % w, file=sys.stderr)
    print("wrote %s" % svg_path)
    return 2 if warnings else 0


# --------------------------------------------------------------------------- #
# merge / finalize
# --------------------------------------------------------------------------- #
def cmd_merge(work_dir, output_dir):
    spec, spath = load_spec(work_dir)
    if spec is None:
        print("spec.json not found at %s" % spath, file=sys.stderr)
        return 1
    result_path = os.path.join(output_dir, "result.json")
    result = read_json(result_path) if os.path.exists(result_path) else {}
    if not isinstance(result, dict) or result.get("_error"):
        result = {"case_id": spec.get("case_id") or "unknown"}
    for key in ("case_id", "title"):
        if spec.get(key):
            result[key] = spec[key]
    for key in ("entities", "relations", "legend", "data_mappings", "sources", "uncertainty"):
        if key in spec:
            result[key] = spec[key]
    # mirror the freshly built SVG path if it exists
    svg_path = os.path.join(output_dir, "mechanism.svg")
    if os.path.exists(svg_path):
        result["editable_source_files"] = [
            {"path": "mechanism.svg", "format": "svg",
             "description": "layered editable source; see layers compartments/entities/relations/labels/legend"}
        ]
    append_change(result, "merge", "merged spec content into result.json",
                  "%d entities, %d relations" % (len(spec.get("entities") or []),
                                                 len(spec.get("relations") or [])))
    write_json(result_path, result)
    print("merged into %s" % result_path)
    return 0


def cmd_finalize(output_dir, status, limitations, pending=None):
    if status not in ALLOWED_STATUS:
        print("invalid status %r; allowed %s" % (status, ALLOWED_STATUS), file=sys.stderr)
        return 1
    result_path = os.path.join(output_dir, "result.json")
    result = read_json(result_path) if os.path.exists(result_path) else {}
    if not isinstance(result, dict):
        result = {}
    result["status"] = status
    if limitations:
        result["limitations"] = limitations
    elif status in ("partial", "failed") and not result.get("limitations"):
        result["limitations"] = ["status=%s; limitations not yet enumerated" % status]
    if pending:
        result["pending_human"] = pending
    append_change(result, "finalize", "status set to %s" % status,
                  "; ".join(limitations) if limitations else "")
    write_json(result_path, result)
    print("finalized %s -> %s" % (result_path, status))
    return 0


# --------------------------------------------------------------------------- #
# check
# --------------------------------------------------------------------------- #
def _check_artifact(item, output_dir, fmt, errors, warnings):
    if not isinstance(item, dict):
        errors.append("%s artifact must be an object" % fmt)
        return
    path = item.get("path")
    if not path:
        errors.append("%s artifact missing 'path'" % fmt)
        return
    full = path if os.path.isabs(path) else os.path.join(output_dir, path)
    if not os.path.exists(full):
        warnings.append("%s artifact not on disk: %s" % (fmt, path))
        return
    try:
        with open(full, "rb") as f:
            head = f.read(8)
    except OSError as e:
        errors.append("%s artifact unreadable: %s (%s)" % (fmt, path, e))
        return
    if fmt == "png" and not head.startswith(b"\x89PNG\r\n\x1a\n"):
        errors.append("PNG magic invalid: %s" % path)
    if fmt == "pdf" and not head.startswith(b"%PDF-"):
        errors.append("PDF magic invalid: %s" % path)
    if fmt == "svg":
        try:
            import xml.etree.ElementTree as ET
            ET.parse(full)
        except Exception as e:  # noqa: BLE001
            errors.append("SVG parse failed: %s (%s)" % (path, e))


def cmd_check(output_dir):
    result_path = os.path.join(output_dir, "result.json")
    if not os.path.exists(result_path):
        print("result.json not found at %s" % result_path, file=sys.stderr)
        return 1
    result = read_json(result_path)
    errors, warnings = [], []
    if not isinstance(result, dict) or result.get("_error"):
        errors.append("result.json is not a valid JSON object")
        result = {}
    for key in REQUIRED_RESULT_FIELDS:
        if key not in result:
            errors.append("missing required field: %s" % key)
    if not (isinstance(result.get("case_id"), str) and result.get("case_id", "").strip()):
        errors.append("case_id must be a non-empty string")
    if result.get("status") not in ALLOWED_STATUS:
        errors.append("status must be one of %s" % sorted(ALLOWED_STATUS))
    for key in LIST_FIELDS:
        if key in result and not isinstance(result[key], list):
            errors.append("%s must be a list" % key)
    change_log = result.get("change_log")
    if isinstance(change_log, list):
        for i, e in enumerate(change_log):
            if not isinstance(e, dict) or "at" not in e or "action" not in e:
                errors.append("change_log[%d] must have 'at' and 'action'" % i)
    if result.get("status") in ("partial", "failed", "insufficient_evidence"):
        lim = result.get("limitations")
        if not isinstance(lim, list) or not lim:
            warnings.append("status=%s should have non-empty limitations" % result.get("status"))
    for item in result.get("editable_source_files", []):
        _check_artifact(item, output_dir, "svg", errors, warnings)
    for item in result.get("export_artifacts", []):
        if not isinstance(item, dict):
            errors.append("export_artifacts entries must be objects")
            continue
        fmt = (item.get("format") or "").lower()
        if fmt in ("png", "pdf", "svg"):
            _check_artifact(item, output_dir, fmt, errors, warnings)
        else:
            warnings.append("unknown export format %r" % fmt)
    for e in errors:
        print("error: %s" % e, file=sys.stderr)
    for w in warnings:
        print("warning: %s" % w, file=sys.stderr)
    print("check: %d errors, %d warnings" % (len(errors), len(warnings)))
    return 1 if errors else (2 if warnings else 0)


# --------------------------------------------------------------------------- #
# pipeline / CLI
# --------------------------------------------------------------------------- #
def cmd_pipeline(input_dir, output_dir, work_dir, size=None):
    rc = 0
    bootstrap(input_dir, output_dir, work_dir)
    spec, spath = load_spec(work_dir)
    if spec is None or not (spec.get("entities") or spec.get("relations")):
        print("pipeline: no spec content yet; skipping build (skeleton intact)")
    else:
        rc = cmd_build(work_dir, output_dir, size=size)
        cmd_merge(work_dir, output_dir)
    rc = max(rc, cmd_check(output_dir))
    return rc


def main(argv=None):
    ap = argparse.ArgumentParser(description="Mechanism-figure Skill orchestrator")
    ap.add_argument("command", choices=["bootstrap", "validate", "build", "merge",
                                        "finalize", "check", "pipeline"])
    ap.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"))
    ap.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    ap.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))
    ap.add_argument("--size", default=None)
    ap.add_argument("--status", default="partial", choices=list(ALLOWED_STATUS))
    ap.add_argument("--limitation", action="append", default=None)
    ap.add_argument("--pending", action="append", default=None)
    a = ap.parse_args(argv)

    if a.command == "bootstrap":
        return bootstrap(a.input, a.output, a.work) and 0
    if a.command == "validate":
        return cmd_validate(a.work)
    if a.command == "build":
        return cmd_build(a.work, a.output, size=a.size)
    if a.command == "merge":
        return cmd_merge(a.work, a.output)
    if a.command == "finalize":
        return cmd_finalize(a.output, a.status, a.limitation, a.pending)
    if a.command == "check":
        return cmd_check(a.output)
    if a.command == "pipeline":
        return cmd_pipeline(a.input, a.output, a.work, size=a.size)
    return 1


if __name__ == "__main__":
    sys.exit(main())
