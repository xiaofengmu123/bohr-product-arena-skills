#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI orchestrator for the academic-slide skill.

Phases (each idempotent, runnable repeatedly):

    init      bootstrap a complete result.json skeleton + input_inventory.json
    contract  print the discovered output contract (what fields must be filled)
    validate  check result.json against the contract, write contract_report.json
    finalize  set final status and append a change_log entry

Exit codes: 0 = success/ok, 1 = fatal usage error, 2 = contract violations found.

Only the standard library is used.  Paths default to the runtime contract and
can be overridden for local self-testing.
"""

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import contract  # noqa: E402


def _case_id(case_json, input_dir):
    if isinstance(case_json, dict):
        for key in ("case_id", "id", "caseId", "challenge_id"):
            value = case_json.get(key)
            if isinstance(value, str) and value:
                return value
        if isinstance(case_json.get("armiId"), str):
            return case_json["armiId"]
    return os.path.basename(os.path.normpath(input_dir)) or "unknown"


def cmd_init(args):
    input_dir = args.input
    output_dir = args.output
    work_dir = args.work
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    case_path = os.path.join(input_dir, "case.json")
    task_path = os.path.join(input_dir, "task.md")
    case_json = contract.load_json(case_path)
    task_text = contract.read_text(task_path)

    inventory = {
        "input_dir": input_dir,
        "case_json_exists": os.path.isfile(case_path),
        "task_md_exists": os.path.isfile(task_path),
        "files": [],
    }
    if os.path.isdir(input_dir):
        for root, _dirs, files in os.walk(input_dir):
            for name in sorted(files):
                full = os.path.join(root, name)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = -1
                inventory["files"].append({
                    "path": os.path.relpath(full, input_dir).replace(os.sep, "/"),
                    "bytes": size,
                })

    schema = contract.discover_contract(case_json, task_text or "")
    cid = _case_id(case_json, input_dir)

    if case_json is None:
        # Empty/missing input is a valid case: produce an honest skeleton.
        skeleton = contract.build_skeleton(cid, contract.DEFAULT_CONTRACT)
        skeleton["status"] = "input_error"
        skeleton.setdefault("errors", []).append(
            {"code": "missing_input", "detail": "case.json absent or unreadable"})
        contract.append_change(skeleton, "input_error", "case.json absent or unreadable")
    else:
        skeleton = contract.build_skeleton(cid, schema)

    # Idempotent merge: never overwrite work already done.
    existing_path = os.path.join(output_dir, "result.json")
    existing = contract.load_json(existing_path)
    if existing is not None:
        merged = contract.deep_merge(skeleton, existing)
        # Keep the earliest bootstrap entry and avoid duplicate bootstraps.
        merged["change_log"] = skeleton["change_log"] + [
            e for e in existing.get("change_log", [])
            if not (isinstance(e, dict) and e.get("code") == "bootstrap")]
        skeleton = merged

    contract.write_json(existing_path, skeleton)
    contract.write_json(os.path.join(work_dir, "input_inventory.json"), inventory)
    contract.write_json(os.path.join(work_dir, "output_contract.json"), schema)

    filled = sum(1 for _leaf, req, _typ in contract.walk_leaves(schema)
                 if req and not _is_empty_leaf(contract.get_path(skeleton, _leaf)))
    total = sum(1 for _leaf, _req, _typ in contract.walk_leaves(schema))
    print("case_id={} status={} contract_fields={} required_filled={}".format(
        cid, skeleton["status"], total, filled))
    print("wrote result.json -> {}".format(existing_path))
    return 0


def _is_empty_leaf(value):
    if value is None:
        return True
    if isinstance(value, (list, dict, str)) and len(value) == 0:
        return True
    return False


def cmd_contract(args):
    case_json = contract.load_json(os.path.join(args.input, "case.json"))
    schema = contract.discover_contract(case_json, "")
    print(json.dumps(schema, ensure_ascii=False, indent=2))
    return 0


def cmd_validate(args):
    result = contract.load_json(os.path.join(args.output, "result.json"))
    if result is None:
        print("result.json missing; run `init` first", file=sys.stderr)
        return 1
    case_json = contract.load_json(os.path.join(args.input, "case.json"))
    schema = contract.discover_contract(case_json, "")
    report, ok, errors = contract.validate_result(result, schema, base_dir=args.output)
    os.makedirs(args.work, exist_ok=True)
    contract.write_json(os.path.join(args.work, "contract_report.json"), report)
    if args.verbose:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    print("contract_ok={} errors={} fields={}".format(
        ok, errors, report["contract_fields"]))
    return 0 if ok else 2


def cmd_finalize(args):
    result_path = os.path.join(args.output, "result.json")
    result = contract.load_json(result_path)
    if result is None:
        print("result.json missing; run `init` first", file=sys.stderr)
        return 1
    status = args.status
    if not status:
        status = "ok" if args.force_ok else result.get("status", "partial")
    if status not in ("ok", "partial", "failed", "insufficient_evidence",
                      "input_error", "in_progress"):
        print("invalid status: {}".format(status), file=sys.stderr)
        return 1
    result["status"] = status
    contract.append_change(result, "finalize", "status set to {}".format(status))
    if args.note:
        contract.append_change(result, "note", args.note)
    result.setdefault("runtime", {})["finished_at"] = contract.utc_now()
    contract.write_json(result_path, result)
    print("finalized status={}".format(status))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="Academic-slide skill orchestrator")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="bootstrap complete result skeleton")
    sub.add_parser("contract", help="print discovered output contract")
    sub.add_parser("validate", help="validate result.json against contract")
    fin = sub.add_parser("finalize", help="set final status + change_log entry")
    fin.add_argument("--status", default="",
                     help="one of ok/partial/failed/insufficient_evidence/input_error")
    fin.add_argument("--note", default="")
    fin.add_argument("--force-ok", action="store_true",
                     help="shortcut to set status=ok")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    handlers = {
        "init": cmd_init,
        "contract": cmd_contract,
        "validate": cmd_validate,
        "finalize": cmd_finalize,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
