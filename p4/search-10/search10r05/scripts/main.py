#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py -- CLI orchestrator for the cross-lingual known-item search skill.

Subcommands
  init      discover /input, extract case_id + output_contract, and atomically
            write a legal partial skeleton to /app/output/result.json. Run this
            FIRST so a valid deliverable is on disk within the first 3 minutes.
  patch     deep-merge a JSON patch file into result.json and append change_log.
  validate  verify result.json is legal JSON and contains case_id, every
            output_contract field and the standard meta fields. Exit 0 when clean.
  status    print a compact progress summary.

Standard library only. Defaults follow the runtime contract:
  --input  /input    --output /app/output    --work /app/work
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl as impl  # noqa: E402


def cmd_init(args):
    found, case, task_text, case_err = impl.load_case(args.input)
    contract = impl.find_contract(case)
    has_contract = contract is not None
    if not has_contract:
        contract = impl.find_contract_from_text(task_text)
        has_contract = contract is not None
    case_id = impl.find_case_id(case, task_text)

    result = impl.build_skeleton(case_id, contract, has_contract)

    if case_err:
        result["errors"].append({"scope": "input", "detail": case_err})
        result.setdefault("degradations", []).append("input_degraded:" + case_err)

    if contract is None and not has_contract:
        result.setdefault("degradations", []).append(
            "no output_contract found in case.json/task.md; using default semantic fields")

    # Honest final status: unknown case_id or missing case.json => input_error;
    # degraded-but-recovered => partial.
    if case_id == "unknown_case" or "case.json_not_found" in (case_err or ""):
        result["status"] = "input_error"
    else:
        result["status"] = "partial"

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    impl.atomic_write_json(os.path.join(args.output, "result.json"), result)

    state = {
        "found": found,
        "has_contract": has_contract,
        "contract_fields": impl.contract_top_fields(contract) if has_contract else [],
        "case_error": case_err,
        "input_dir": os.path.abspath(args.input),
    }
    impl.atomic_write_json(os.path.join(args.work, "state.json"), state)

    print("init ok case_id=%s status=%s contract=%s files=%d" % (
        result["case_id"], result["status"], has_contract,
        len(found["materials"])))
    return 0


def cmd_patch(args):
    obj, path = impl.load_result(args.output)
    if obj is None:
        print("patch error: no result.json at %s; run init first" % path, file=sys.stderr)
        return 2
    patch, err = impl.safe_json_load(args.patch)
    if patch is None or not isinstance(patch, dict):
        print("patch error: bad patch %s" % err, file=sys.stderr)
        return 2
    impl.apply_patch(obj, patch)
    impl.atomic_write_json(path, obj)
    print("patch ok seq=%d fields=%s" % (
        obj["change_log"][-1]["seq"], ",".join(obj["change_log"][-1]["fields"])))
    return 0


def cmd_validate(args):
    found, case, task_text, _ = impl.load_case(args.input)
    contract = impl.find_contract(case)
    has_contract = contract is not None
    if not has_contract:
        contract = impl.find_contract_from_text(task_text)
        has_contract = contract is not None
    obj, path = impl.load_result(args.output)
    if obj is None:
        print("validate error: missing result.json at %s" % path)
        return 1
    issues = impl.validate_result(obj, contract, has_contract)
    if issues:
        print("validate FAIL %d issue(s):" % len(issues))
        for i in issues:
            print("  -", i)
        return 1
    s = impl.summarize(obj)
    print("validate OK case_id=%s status=%s results=%s sources=%s log=%s" % (
        s["case_id"], s["status"], s["results"], s["sources"], s["change_log_entries"]))
    return 0


def cmd_status(args):
    obj, path = impl.load_result(args.output)
    if obj is None:
        print("status: no result.json at %s" % path)
        return 1
    s = impl.summarize(obj)
    for k, v in s.items():
        print("%-20s %s" % (k, v))
    return 0


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=impl.DEFAULT_INPUT)
    common.add_argument("--output", default=impl.DEFAULT_OUTPUT)
    common.add_argument("--work", default=impl.DEFAULT_WORK)

    p = argparse.ArgumentParser(prog="main.py", parents=[common],
                                description="cross-lingual known-item search skill")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", parents=[common],
                   help="discover input and write partial skeleton")
    pp = sub.add_parser("patch", parents=[common],
                        help="deep-merge a JSON patch into result.json")
    pp.add_argument("--patch", required=True, help="patch JSON file path")
    sub.add_parser("validate", parents=[common],
                   help="verify contract alignment and JSON legality")
    sub.add_parser("status", parents=[common],
                   help="print progress summary")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "init":
        return cmd_init(args)
    if args.cmd == "patch":
        return cmd_patch(args)
    if args.cmd == "validate":
        return cmd_validate(args)
    if args.cmd == "status":
        return cmd_status(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
