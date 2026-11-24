#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — assemble + validate + all. Standard library only.

The Agent writes the intellectual work as JSON files under /app/work/ (see SKILL.md
and references for the exact schema); this script merges them into a valid
/app/output/result.json, echoes every output_contract key, appends change_log,
and validates the final structure.
"""
import argparse
import json
import os
import shutil
import sys

from scaffold import (
    ALWAYS_KEYS,
    DEFAULT_CONTRACT_KEYS,
    KNOWN_DEFAULTS,
    build_skeleton,
    contract_keys,
    extract_case_id,
    extract_contract,
    load_json,
    write_json,
)

ALLOWED_STATUS = {
    "complete", "partial", "insufficient_evidence", "input_error", "failed", "in_progress",
}


def _utc_now():
    try:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return ""


def _load_list(work_dir, filename, key):
    """Load a work JSON file that is either a list or a dict with a named list key."""
    data = load_json(os.path.join(work_dir, filename))
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if key and isinstance(data.get(key), list):
            return data[key]
        for candidate in ("claims", "sources", "items"):
            if isinstance(data.get(candidate), list):
                return data[candidate]
    return []


def _append_change_log(result, action, detail):
    log = result.get("change_log")
    if not isinstance(log, list):
        log = []
        result["change_log"] = log
    log.append({"time": _utc_now(), "action": action, "detail": detail})


def _read_status_override(work_dir):
    text_path = os.path.join(work_dir, "status.txt")
    if os.path.exists(text_path):
        try:
            with open(text_path, "r", encoding="utf-8-sig") as fh:
                value = fh.read().strip().splitlines()[0].strip()
                if value in ALLOWED_STATUS - {"in_progress"}:
                    return value
        except OSError:
            pass
    return None


def compute_status(claims, sources, deliverable_exists):
    if not claims and not sources and not deliverable_exists:
        return "insufficient_evidence"
    if deliverable_exists and claims and sources:
        return "complete"
    return "partial"


def cmd_assemble(case_path, work_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    result_path = os.path.join(out_dir, "result.json")

    result = load_json(result_path)
    if result is None:
        data = load_json(case_path)
        keys = contract_keys(extract_contract(data)) if data else []
        result = build_skeleton(extract_case_id(data) if data else None, keys,
                                status="in_progress")

    claims = _load_list(work_dir, "claims.json", "claims")
    sources = _load_list(work_dir, "evidence.json", "sources")
    uncertainty = _load_list(work_dir, "uncertainty.json", None)
    pending = _load_list(work_dir, "pending_human.json", None)
    limitations = _load_list(work_dir, "limitations.json", None)
    errors = _load_list(work_dir, "errors.json", None)
    degradations = _load_list(work_dir, "degradations.json", None)

    # Materialize the deliverable into /app/output/ so it is an inspectable artifact.
    deliverable_src = os.path.join(work_dir, "deliverable.md")
    deliverable_dst = os.path.join(out_dir, "deliverable.md")
    deliverable_exists = os.path.exists(deliverable_src)
    if deliverable_exists:
        try:
            shutil.copyfile(deliverable_src, deliverable_dst)
        except OSError:
            deliverable_exists = False
            result.setdefault("degradations", []).append(
                "failed to copy deliverable.md to /app/output")

    result["claims"] = claims
    result["sources"] = sources
    result["uncertainty"] = uncertainty
    result["pending_human"] = pending
    result["limitations"] = limitations
    if errors:
        result["errors"] = errors
    if degradations:
        existing_deg = result.get("degradations")
        if not isinstance(existing_deg, list):
            existing_deg = []
            result["degradations"] = existing_deg
        existing_deg.extend(degradations)

    if deliverable_exists:
        result["deliverable"] = {
            "path": "/app/output/deliverable.md",
            "format": "markdown",
            "editable": True,
        }
    else:
        result.setdefault("deliverable", None)

    status = _read_status_override(work_dir) or compute_status(claims, sources, deliverable_exists)
    result["status"] = status
    _append_change_log(result, "assemble",
                       "claims=%d sources=%d deliverable=%s status=%s" % (
                           len(claims), len(sources), deliverable_exists, status))
    write_json(result_path, result)
    return {"ok": True, "status": status, "claims": len(claims),
            "sources": len(sources), "deliverable": deliverable_exists,
            "result": result_path}


def cmd_validate(out_dir):
    result_path = os.path.join(out_dir, "result.json")
    result = load_json(result_path)
    issues = []
    if result is None:
        issues.append("result.json missing or unparseable")
        return {"ok": False, "issues": issues, "result": result_path}

    if not isinstance(result.get("case_id"), str) or not result["case_id"].strip():
        issues.append("case_id missing or empty")
    if result.get("status") not in ALLOWED_STATUS:
        issues.append("status invalid: %r" % result.get("status"))
    if not isinstance(result.get("sources"), list):
        issues.append("sources must be a list")
    if not isinstance(result.get("change_log"), list):
        issues.append("change_log must be a list")
    for req in ("sources", "uncertainty", "pending_human", "change_log", "limitations"):
        if req not in result:
            issues.append("missing required field: %s" % req)

    claims = result.get("claims", [])
    if isinstance(claims, list):
        for i, claim in enumerate(claims):
            if not isinstance(claim, dict):
                issues.append("claims[%d] not an object" % i)
                continue
            if not claim.get("claim_id"):
                issues.append("claims[%d] missing claim_id" % i)
            if not claim.get("text"):
                issues.append("claims[%d] missing text" % i)
            if claim.get("evidence_strength") not in ("strong", "moderate", "weak", "insufficient"):
                issues.append("claims[%d] invalid/missing evidence_strength" % i)

    sources = result.get("sources", [])
    if isinstance(sources, list):
        for i, src in enumerate(sources):
            if not isinstance(src, dict):
                issues.append("sources[%d] not an object" % i)
                continue
            if not src.get("source_id"):
                issues.append("sources[%d] missing source_id" % i)
            if not src.get("quote"):
                issues.append("sources[%d] missing verbatim quote" % i)
            if not src.get("locator"):
                issues.append("sources[%d] missing locator" % i)

    return {"ok": not issues, "issues": issues, "result": result_path}


def cmd_all(case_path, work_dir, out_dir):
    from scaffold import cmd_scaffold
    scaffold_summary = cmd_scaffold(case_path, out_dir, work_dir)
    assemble_summary = cmd_assemble(case_path, work_dir, out_dir)
    validate_summary = cmd_validate(out_dir)
    return {"scaffold": scaffold_summary, "assemble": assemble_summary,
            "validate": validate_summary}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Assemble / validate / run-all for result.json")
    sub = ap.add_subparsers(dest="command")
    sub.add_parser("assemble", help="merge /app/work JSON files into result.json")
    sub.add_parser("validate", help="validate result.json structure")
    sub.add_parser("all", help="scaffold + assemble + validate")
    for p in (sub.choices["assemble"], sub.choices["all"]):
        p.add_argument("--case", default="/input/case.json")
        p.add_argument("--work", default="/app/work")
        p.add_argument("--out", default="/app/output")
    sub.choices["validate"].add_argument("--out", default="/app/output")
    args = ap.parse_args(argv)

    if args.command == "assemble":
        summary = cmd_assemble(args.case, args.work, args.out)
    elif args.command == "validate":
        summary = cmd_validate(args.out)
    elif args.command == "all":
        summary = cmd_all(args.case, args.work, args.out)
    else:
        ap.print_help()
        return 2

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    # validate failures exit non-zero so the harness can detect them.
    if isinstance(summary, dict) and "validate" in summary:
        return 0 if summary["validate"].get("ok") else 1
    return 0 if summary.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
