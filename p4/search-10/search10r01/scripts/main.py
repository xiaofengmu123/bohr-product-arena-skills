#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — cross-lingual literature-search skill orchestrator.

Subcommands
  init      discover /input, extract case_id + output_contract, and atomically
            write a legal partial result skeleton to /app/output/result.json.
            Run this FIRST so a valid partial deliverable is on disk early.
  patch     deep-merge a JSON patch file into result.json (incremental update).
  validate  verify result.json is legal JSON and contains case_id, all
            output_contract fields and the standard fields; report evidence-map
            issues (locator + verbatim quote required for every claim).
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
import time

try:
    import core_impl as cl
except ImportError:  # pragma: no cover
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import core_impl as cl

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"


def _ensure_dirs(output, work):
    os.makedirs(output, exist_ok=True)
    os.makedirs(work, exist_ok=True)


def _state_path(work):
    return os.path.join(work, "state.json")


def cmd_init(args):
    _ensure_dirs(args.output, args.work)
    started = time.time()
    found, case, task_text, case_err = cl.load_inputs(args.input)
    contract = cl.find_contract(case) or cl.find_contract_from_text(task_text)
    case_id = cl.find_case_id(case, task_text)
    result = cl.build_skeleton(case_id, contract)

    change = {
        "step": "init",
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "action": "partial skeleton written (contract fields + standard fields)",
        "case_error": case_err,
        "materials_found": [os.path.basename(p) for p in found["materials"]],
    }
    if case_err:
        result["errors"].append({"stage": "init", "message": case_err})
        if case_err.startswith("case.json_not_found"):
            result["status"] = "input_error"
    result["change_log"].append(change)
    result["runtime"]["init_seconds"] = round(time.time() - started, 3)
    result["_contract_keys"] = cl.contract_top_fields(contract)

    cl.atomic_write_json(os.path.join(args.output, "result.json"), result)
    state = {
        "input_dir": args.input,
        "output_dir": args.output,
        "work_dir": args.work,
        "case_id": case_id,
        "contract_keys": cl.contract_top_fields(contract),
        "materials": [os.path.basename(p) for p in found["materials"]],
        "initialized_at": change["at"],
    }
    cl.atomic_write_json(_state_path(args.work), state)
    print(json.dumps({"case_id": case_id, "status": result["status"],
                      "contract_keys": state["contract_keys"],
                      "materials": state["materials"],
                      "case_error": case_err}, ensure_ascii=False))


def cmd_patch(args):
    _ensure_dirs(args.output, args.work)
    result_path = os.path.join(args.output, "result.json")
    obj, err = cl.safe_json_load(result_path)
    if obj is None:
        print("error: cannot read result.json (%s); run init first" % err)
        sys.exit(2)
    patch, err = cl.safe_json_load(args.patch)
    if patch is None:
        print("error: cannot read patch file %s (%s)" % (args.patch, err))
        sys.exit(2)
    merged = cl.deep_merge(obj, patch)
    cl.atomic_write_json(result_path, merged)
    print(json.dumps({"status": merged.get("status"),
                      "results": len(merged.get("results") or []),
                      "evidence_map": len(merged.get("evidence_map") or [])},
                     ensure_ascii=False))


def cmd_validate(args):
    result_path = os.path.join(args.output, "result.json")
    obj, err = cl.safe_json_load(result_path)
    if obj is None:
        print("FATAL: result.json unreadable: %s" % err)
        sys.exit(1)
    issues, ok = cl.validate_result(obj)
    print(json.dumps({"ok": ok, "issues": issues,
                      "case_id": obj.get("case_id"),
                      "status": obj.get("status"),
                      "results": len(obj.get("results") or []),
                      "evidence_map": len(obj.get("evidence_map") or [])},
                     ensure_ascii=False, indent=2))
    sys.exit(0 if ok else 1)


def cmd_status(args):
    result_path = os.path.join(args.output, "result.json")
    obj, err = cl.safe_json_load(result_path)
    if obj is None:
        print("no result.json yet (%s)" % err)
        return
    log = obj.get("change_log") or []
    print("case_id     :", obj.get("case_id"))
    print("status      :", obj.get("status"))
    print("results     :", len(obj.get("results") or []))
    print("evidence_map:", len(obj.get("evidence_map") or []))
    print("dedup       :", len(obj.get("dedup") or []))
    print("pending     :", len(obj.get("pending_human") or []))
    print("change_log  :", len(log), "entries")
    if log:
        print("last change :", log[-1].get("step"), log[-1].get("action"))


def build_parser():
    parser = argparse.ArgumentParser(prog="main.py",
                                     description="cross-lingual literature-search skill orchestrator")
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=DEFAULT_INPUT)
    common.add_argument("--output", default=DEFAULT_OUTPUT)
    common.add_argument("--work", default=DEFAULT_WORK)

    sub.add_parser("init", parents=[common],
                   help="discover inputs and write a partial skeleton")
    p_patch = sub.add_parser("patch", parents=[common],
                             help="deep-merge a JSON patch into result.json")
    p_patch.add_argument("--patch", required=True, help="path to a JSON patch file")
    sub.add_parser("validate", parents=[common],
                   help="validate result.json and evidence map")
    sub.add_parser("status", parents=[common],
                   help="print a short progress summary")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "init":
        cmd_init(args)
    elif args.command == "patch":
        cmd_patch(args)
    elif args.command == "validate":
        cmd_validate(args)
    elif args.command == "status":
        cmd_status(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
