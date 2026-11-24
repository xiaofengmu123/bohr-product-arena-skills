#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — cross-lingual literature search Skill orchestrator.

Subcommands
  init      discover /input, resolve case_id + output_contract, and atomically
            write a legal partial result skeleton to /app/output/result.json.
            Run this FIRST so a valid partial deliverable exists on disk early.
  patch     deep-merge a JSON patch file into result.json (incremental update);
            --log appends a timestamped change_log entry.
  validate  verify result.json is legal JSON and contains case_id, every
            contract field and the standard fields.
  status    print a short progress summary from result.json.

Standard library only. Default paths follow the runtime contract:
  --input  /input
  --output /app/output
  --work   /app/work
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core


def _argparse():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Cross-lingual literature search Skill orchestrator.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="write a partial result skeleton")
    p_init.add_argument("--input", default=core.DEFAULT_INPUT)
    p_init.add_argument("--output", default=core.DEFAULT_OUTPUT)
    p_init.add_argument("--work", default=core.DEFAULT_WORK)

    p_patch = sub.add_parser("patch", help="deep-merge a JSON patch file")
    p_patch.add_argument("--patch", required=True, help="path to patch JSON")
    p_patch.add_argument("--output", default=core.DEFAULT_OUTPUT)
    p_patch.add_argument("--work", default=core.DEFAULT_WORK)
    p_patch.add_argument("--log", default=None,
                         help="human-readable message appended to change_log")

    p_validate = sub.add_parser("validate", help="check result.json")
    p_validate.add_argument("--output", default=core.DEFAULT_OUTPUT)
    p_validate.add_argument("--work", default=core.DEFAULT_WORK)

    p_status = sub.add_parser("status", help="print progress summary")
    p_status.add_argument("--output", default=core.DEFAULT_OUTPUT)
    p_status.add_argument("--work", default=core.DEFAULT_WORK)
    return parser


def _load_state(work_dir):
    path = os.path.join(work_dir, "state.json")
    obj, err = core.safe_json_load(path)
    if err or not isinstance(obj, dict):
        return {}
    return obj


def _resolve_status(found, task_text, errors):
    """Return ``input_error`` only when neither a usable case nor a usable task
    exists; otherwise degrade to ``partial`` so the remaining material is used."""
    joined = " ".join(errors or [])
    usable_case = found.get("case") is not None and not (
        "bad_json" in joined or "case_not_object" in joined)
    usable_task = bool((task_text or "").strip())
    return "input_error" if (not usable_case and not usable_task) else "partial"


def cmd_init(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    started = core.now_iso()

    found, case, task_text, errors = core.load_case(args.input)
    contract = core.resolve_contract(case, task_text)
    case_id = core.find_case_id(case, task_text)

    result = core.build_skeleton(case_id, contract)
    result["runtime"]["started_at"] = started
    if errors:
        result["errors"] = list(errors)
        result["status"] = _resolve_status(found, task_text, errors)

    # Record discovered materials as provenance entries (basename + full path).
    for path in found["materials"]:
        result["sources"].append({
            "kind": "material",
            "name": os.path.basename(path),
            "path": path,
            "role": "input",
        })

    state = {
        "case_id": case_id,
        "contract_keys": sorted(contract.keys()),
        "contract_source": (
            "case.json" if core.find_contract(case) is not None
            else ("task.md" if core.find_contract_from_text(task_text) is not None
                  else "default")),
        "input_discovery": {
            "case": found["case"],
            "task": found["task"],
            "materials": found["materials"],
        },
        "started_at": started,
    }

    core.atomic_write_json(os.path.join(args.output, "result.json"), result)
    core.atomic_write_json(os.path.join(args.work, "state.json"), state)

    print(json.dumps({
        "ok": True,
        "case_id": case_id,
        "status": result["status"],
        "contract_fields": state["contract_keys"],
        "materials_found": len(found["materials"]),
        "result": os.path.join(args.output, "result.json"),
    }, ensure_ascii=False))


def cmd_patch(args):
    result_path = os.path.join(args.output, "result.json")
    result, err = core.read_result(result_path)
    if err:
        print(json.dumps({"ok": False, "error": err}, ensure_ascii=False))
        return 1
    patch, perr = core.safe_json_load(args.patch)
    if perr or not isinstance(patch, dict):
        print(json.dumps({"ok": False, "error": "bad_patch:%s" % (perr or "not_object")},
                         ensure_ascii=False))
        return 1

    merged = core.deep_merge(result, patch)
    merged["runtime"]["last_patch_at"] = core.now_iso()
    if args.log:
        log_entries = list(merged.get("change_log") or [])
        log_entries.append({
            "at": core.now_iso(),
            "action": "patch",
            "message": args.log,
        })
        merged["change_log"] = log_entries

    core.atomic_write_json(result_path, merged)
    print(json.dumps({"ok": True, "status": merged.get("status")}, ensure_ascii=False))
    return 0


def cmd_validate(args):
    result_path = os.path.join(args.output, "result.json")
    result, err = core.read_result(result_path)
    state = _load_state(args.work)
    contract_keys = state.get("contract_keys") or list(core.DEFAULT_CONTRACT.keys())
    issues = core.validate_result(result, contract_keys) if result is not None else ["unreadable"]
    if err:
        issues.append(err)
    print(json.dumps({
        "ok": not issues,
        "issues": issues,
        "status": (result or {}).get("status"),
    }, ensure_ascii=False))
    return 0


def cmd_status(args):
    result_path = os.path.join(args.output, "result.json")
    result, err = core.read_result(result_path)
    if err:
        print(json.dumps({"ok": False, "error": err}, ensure_ascii=False))
        return 1
    def count(key):
        value = result.get(key)
        return len(value) if isinstance(value, list) else 0
    change_log = result.get("change_log") or []
    last = change_log[-1]["message"] if change_log else None
    print(json.dumps({
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "counts": {
            "terminology_table": count("terminology_table"),
            "queries": count("queries"),
            "results": count("results"),
            "evidence_map": count("evidence_map"),
            "missing": count("missing"),
            "conflicts": count("conflicts"),
        },
        "last_change": last,
    }, ensure_ascii=False))
    return 0


def main(argv=None):
    parser = _argparse()
    args = parser.parse_args(argv)
    handlers = {
        "init": cmd_init,
        "patch": cmd_patch,
        "validate": cmd_validate,
        "status": cmd_status,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main() or 0)
