#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — orchestrator for the cross-lingual literature-search Skill.

Subcommands
  init      discover /input, extract case_id + output_contract, and atomically
            write a legal partial result.json to /app/output/. Run this FIRST
            so a valid partial deliverable is on disk within the first minutes.
  patch     deep-merge a JSON patch file into result.json (incremental update;
            lists are replaced wholesale, change_log is appended).
  validate  verify result.json is legal JSON, has case_id, echoes every
            output_contract field, and carries the standard fields.
  status    print a short progress summary.

Standard library only. Default paths follow the runtime contract:
  --input  /input   --output /app/output   --work /app/work
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crosslang as cl  # noqa: E402

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"


def _load_contract_from_state(args):
    state, _ = cl.safe_json_load(os.path.join(args.work, "state.json"))
    if isinstance(state, dict):
        return state.get("contract_fields") or []
    return []


def cmd_init(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    found, case, task_text, case_err = cl.load_case(args.input)
    contract = cl.find_contract(case) or cl.find_contract_from_text(task_text) or {}
    skeleton = cl.build_skeleton(case, task_text, case_err, contract)
    out_path = os.path.join(args.output, "result.json")
    cl.atomic_write_json(out_path, skeleton)
    state = {
        "case_id": skeleton["case_id"],
        "contract_fields": cl.contract_top_fields(contract),
        "input_found": {
            "case": bool(found["case"]),
            "task": bool(found["task"]),
            "materials": [os.path.basename(p) for p in found["materials"]],
        },
        "step": "init",
    }
    cl.atomic_write_json(os.path.join(args.work, "state.json"), state)
    print(json.dumps(
        {
            "case_id": skeleton["case_id"],
            "status": skeleton["status"],
            "contract_fields": cl.contract_top_fields(contract),
            "wrote": out_path,
        },
        ensure_ascii=False,
    ))


def cmd_patch(args):
    patch, err = cl.safe_json_load(args.patch)
    if patch is None or not isinstance(patch, dict):
        print(json.dumps({"error": "patch_not_loadable", "detail": err}, ensure_ascii=False))
        sys.exit(2)
    out_path = os.path.join(args.output, "result.json")
    current, err = cl.safe_json_load(out_path)
    if current is None:
        print(json.dumps(
            {"error": "result_missing", "detail": err, "hint": "run init first"},
            ensure_ascii=False,
        ))
        sys.exit(2)
    merged = cl.deep_merge(current, patch)
    cl.atomic_write_json(out_path, merged)
    print(json.dumps(
        {"status": merged.get("status"), "patched": out_path},
        ensure_ascii=False,
    ))


def cmd_validate(args):
    out_path = os.path.join(args.output, "result.json")
    obj, err = cl.safe_json_load(out_path)
    if obj is None:
        print(json.dumps({"ok": False, "issues": ["unreadable:%s" % err]}, ensure_ascii=False))
        sys.exit(1)
    contract_fields = _load_contract_from_state(args)
    issues = cl.validate_result(obj, contract_fields)
    print(json.dumps(
        {
            "ok": not issues,
            "issues": issues,
            "case_id": obj.get("case_id"),
            "status": obj.get("status"),
            "results": len(obj.get("results") or []),
        },
        ensure_ascii=False,
    ))
    sys.exit(0 if not issues else 1)


def cmd_status(args):
    out_path = os.path.join(args.output, "result.json")
    obj, err = cl.safe_json_load(out_path)
    if obj is None:
        print(json.dumps({"error": "unreadable:%s" % err}, ensure_ascii=False))
        sys.exit(1)
    print(json.dumps(
        {
            "case_id": obj.get("case_id"),
            "status": obj.get("status"),
            "results": len(obj.get("results") or []),
            "evidence_map": len(obj.get("evidence_map") or []),
            "uncertainty": len(obj.get("uncertainty") or []),
            "limitations": len(obj.get("limitations") or []),
            "pending_human": len(obj.get("pending_human") or []),
            "change_log": len(obj.get("change_log") or []),
        },
        ensure_ascii=False,
    ))


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=DEFAULT_INPUT)
    common.add_argument("--output", default=DEFAULT_OUTPUT)
    common.add_argument("--work", default=DEFAULT_WORK)
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="cross-lingual literature search orchestrator",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="discover inputs and write partial result skeleton", parents=[common])
    patch_p = sub.add_parser("patch", help="deep-merge a JSON patch into result.json", parents=[common])
    patch_p.add_argument("--patch", required=True, help="path to patch JSON file")
    sub.add_parser("validate", help="validate result.json against contract", parents=[common])
    sub.add_parser("status", help="print a short progress summary", parents=[common])
    return parser


COMMANDS = {
    "init": cmd_init,
    "patch": cmd_patch,
    "validate": cmd_validate,
    "status": cmd_status,
}


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    COMMANDS[args.command](args)


if __name__ == "__main__":
    main()
