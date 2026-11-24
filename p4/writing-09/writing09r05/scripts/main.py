#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Entry point for the results-discussion-writing skill.

Subcommands:
  build     Normalize input, extract the output contract, and write an honest
            partial result.json skeleton (all contract fields present) plus
            /app/work/input_summary.json and /app/work/contract.json.
            This is the "落骨架" step the agent runs in the first minutes.
  validate  Check an existing result.json against the contract + guaranteed
            fields + change_log schema + over-claim scan. Exit 0 only when no
            structural errors remain (warnings are reported but do not fail).
  selftest  Run the bundled self-test (delegates to selftest.py).

All paths accept arguments with documented runtime defaults so the same code
runs unchanged on the platform and in a developer's checkout.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl as core  # noqa: E402


def cmd_build(args):
    case, case_errors = core.load_json_robust(args.case)
    task_text, _enc, _err = core.read_text_robust(args.task) if args.task else (None, None, "no task path")
    inputs = core.scan_inputs(args.input_dir)
    contract_keys, contract_def, notes = core.extract_contract(case)

    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)

    started = core.now_iso()
    skeleton = core.build_skeleton(case, contract_keys, started_at=started)
    summary = core.summarize_case(case, task_text, inputs, contract_keys,
                                  contract_def, notes)

    core.write_json(os.path.join(args.work, "input_summary.json"), summary)
    core.write_json(os.path.join(args.work, "contract.json"), {
        "fields": contract_keys,
        "definition": contract_def,
        "notes": notes,
        "guaranteed_extras": core.EXTRAS,
    })
    out_result = os.path.join(args.output, "result.json")
    core.write_json(out_result, skeleton)

    errors, warnings = core.validate_result(skeleton, contract_keys)
    report = {
        "ok": len(errors) == 0,
        "case_id": skeleton["case_id"],
        "output_contract_fields": contract_keys,
        "materials_found": sorted(inputs.keys()),
        "case_json_errors": case_errors,
        "result_json": out_result,
        "status": "in_progress",
        "errors": errors,
        "warnings": warnings,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


def cmd_validate(args):
    result, rerrs = core.load_json_robust(args.result)
    if rerrs and not result:
        print(json.dumps({"ok": False, "errors": rerrs}, ensure_ascii=False, indent=2))
        return 2
    contract_keys = [args.field] if args.field else None
    if contract_keys is None:
        if os.path.isfile(args.contract):
            cobj, _ = core.load_json_robust(args.contract)
            contract_keys = cobj.get("fields") or list(core.DEFAULT_CONTRACT)
        else:
            case, _ = core.load_json_robust(args.case) if args.case else ({}, [])
            contract_keys, _def, _n = core.extract_contract(case)
    errors, warnings = core.validate_result(result, contract_keys)
    print(json.dumps({
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


def main(argv=None):
    ap = argparse.ArgumentParser(prog="main.py",
                                 description="Results & Discussion writing skill orchestrator")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--task", default="/input/task.md")
    ap.add_argument("--input-dir", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--contract", default="/app/work/contract.json")
    ap.add_argument("--field", default=None,
                    help="validate a single contract field name")
    ap.add_argument("command", nargs="?", default="build",
                    choices=["build", "validate", "selftest"])
    args = ap.parse_args(argv)

    if args.command == "build":
        return cmd_build(args)
    if args.command == "validate":
        return cmd_validate(args)
    if args.command == "selftest":
        selftest = os.path.join(HERE, "selftest.py")
        if not os.path.isfile(selftest):
            print("selftest.py not found")
            return 2
        import subprocess
        rc = subprocess.call([sys.executable, selftest])
        return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
