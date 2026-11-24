#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 0 — write a legal partial result skeleton BEFORE any analysis.

Purpose
-------
Any failure later must never leave an empty /app/output.  This script writes a
valid result.json whose status is already honest (partial / input_error) and
whose contract fields are all present.  Later phases only deepen the content.

Pure Python standard library.  Run:

    python3 -B scripts/skeleton.py --input /input --output /app/output --work /app/work

Exit code is always 0 on a written skeleton; the status field carries the truth.
"""

import argparse
import hashlib
import json
import math
import os
import sys
import time

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
DELIVERABLES = ("research_question", "search_protocol", "prior_work",
                "novelty", "feasibility")
# Top-level fields that must always exist in result.json.
RESERVED_FIELDS = (
    "case_id", "status", "output_contract", "research_question",
    "search_protocol", "prior_work", "novelty", "feasibility",
    "evidence_map", "conflicts", "synthesis", "sources", "uncertainty",
    "change_log", "pending_human", "errors", "degradations", "runtime",
)


def utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _empty_deliverable():
    return {"status": "missing", "claims": []}


def _empty_synthesis():
    return {"conclusions": [], "research_gaps": [], "boundary_statement": ""}


def _empty_uncertainty():
    return {"gaps": [], "biases": [], "unresolved": []}


def read_text_tolerant(path):
    """Read a file as text, trying several encodings. Returns (text, ok, err)."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, False, "unreadable: %s" % exc
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), True, None
        except UnicodeDecodeError:
            continue
    return None, False, "undecodable (not utf-8/latin-1)"


def load_json_tolerant(path):
    """Load JSON tolerantly. Returns (data, state, error)."""
    text, ok, err = read_text_tolerant(path)
    if not ok:
        return None, "unreadable", err
    try:
        return json.loads(text), "ok", None
    except (json.JSONDecodeError, ValueError) as exc:
        return None, "bad_json", str(exc)


def resolve_case_id(data, input_dir):
    """Prefer explicit case identifiers; fall back to a content-derived hash."""
    if isinstance(data, dict):
        for key in ("case_id", "id", "armiId", "challenge_id", "caseId"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    digest = hashlib.sha256(input_dir.encode("utf-8")).hexdigest()[:12]
    return "unresolved-" + digest


def build_change_log(action, detail):
    return [{"time": utcnow(), "phase": "phase0", "action": action,
             "detail": detail}]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Write the partial result skeleton before analysis.")
    ap.add_argument("--input", default="/input",
                    help="input directory (default: /input)")
    ap.add_argument("--output", default="/app/output",
                    help="output directory (default: /app/output)")
    ap.add_argument("--work", default="/app/work",
                    help="work directory (default: /app/work)")
    args = ap.parse_args(argv)

    input_dir = os.path.abspath(args.input)
    output_dir = os.path.abspath(args.output)
    work_dir = os.path.abspath(args.work)
    case_json = os.path.join(input_dir, "case.json")
    task_md = os.path.join(input_dir, "task.md")

    errors = []
    degradations = []
    case_id = None
    contract = {}
    status = "partial"
    data = None
    input_missing = not os.path.isdir(input_dir)

    # ---- Read case.json (tolerant) -------------------------------------
    if input_missing:
        status = "input_error"
        errors.append("input directory missing: %s" % input_dir)
    elif not os.path.isfile(case_json):
        status = "input_error"
        errors.append("case.json missing under %s" % input_dir)
    else:
        data, state, err = load_json_tolerant(case_json)
        if state == "ok":
            if isinstance(data, dict):
                case_id = resolve_case_id(data, input_dir)
                contract = data.get("output_contract")
                if contract is None:
                    contract = {}
                elif not isinstance(contract, dict):
                    degradations.append(
                        "output_contract is not an object; treated as {}")
                    contract = {}
            else:
                degradations.append("case.json is not a JSON object")
                case_id = resolve_case_id({}, input_dir)
        elif state == "bad_json":
            errors.append("case.json unparseable: %s" % err)
            degradations.append("bad_json in case.json; contract unavailable")
            case_id = resolve_case_id({}, input_dir)
        else:
            errors.append("case.json unreadable: %s" % err)
            case_id = resolve_case_id({}, input_dir)

    # ---- Assemble the skeleton ------------------------------------------
    result = {
        "case_id": case_id,
        "status": status,
        "output_contract": contract,
        "research_question": _empty_deliverable(),
        "search_protocol": _empty_deliverable(),
        "prior_work": _empty_deliverable(),
        "novelty": _empty_deliverable(),
        "feasibility": _empty_deliverable(),
        "evidence_map": {"claims": []},
        "conflicts": [],
        "synthesis": _empty_synthesis(),
        "sources": [],
        "uncertainty": _empty_uncertainty(),
        "change_log": build_change_log(
            "skeleton_written",
            "status=%s; input=%s" % (status, input_dir)),
        "pending_human": [],
        "errors": errors,
        "degradations": degradations,
        "runtime": {
            "started_at": utcnow(),
            "phase0_at": utcnow(),
            "requests_estimate": 0,
            "notes": [],
        },
    }
    # Echo every output_contract key to the top level with a null/[] default;
    # never rename or drop unknown contract keys.
    for key, _val in contract.items():
        if key not in result:
            result[key] = None

    # ---- Write files ------------------------------------------------------
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    manifest = {
        "input_dir": input_dir,
        "input_exists": not input_missing,
        "case_json_path": case_json,
        "task_md_path": task_md,
        "case_json_state": "ok" if (data is not None and not input_missing
                                    and os.path.isfile(case_json)) else (
            "missing" if input_missing or not os.path.isfile(case_json)
            else "unreadable"),
        "case_id": case_id,
        "output_contract_keys": sorted(contract.keys()),
        "generated_at": utcnow(),
    }

    result_path = os.path.join(output_dir, "result.json")
    manifest_path = os.path.join(work_dir, "input_manifest.json")
    try:
        with open(result_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2,
                      allow_nan=False)
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2,
                      allow_nan=False)
    except (OSError, ValueError) as exc:
        # Last-resort: write a minimal legal result so the process never
        # leaves an empty output.  We still exit 0; the error is recorded.
        minimal = {
            "case_id": case_id,
            "status": "input_error",
            "output_contract": contract,
            "evidence_map": {"claims": []},
            "conflicts": [],
            "synthesis": _empty_synthesis(),
            "sources": [],
            "uncertainty": _empty_uncertainty(),
            "change_log": build_change_log(
                "write_failed", "could not write skeleton: %s" % exc),
            "pending_human": [],
            "errors": ["write_failed: %s" % exc],
            "degradations": [],
            "runtime": {"started_at": utcnow()},
        }
        try:
            with open(result_path, "w", encoding="utf-8") as fh:
                json.dump(minimal, fh, ensure_ascii=False, indent=2,
                          allow_nan=False)
        except OSError:
            pass
        print("SKELETON: write failed, minimal fallback written", file=sys.stderr)
        return 0

    print("SKELETON: wrote %s (status=%s)" % (result_path, result["status"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
