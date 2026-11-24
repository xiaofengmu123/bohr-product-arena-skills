#!/usr/bin/env python3
"""Orchestrator for the research-writing transform skill.

Subcommands (standard library only):
  init      Discover /input, build and write an early valid result.json skeleton.
            Run this within the first ~3 minutes so a partial result always exists.
  finalize  Load the current result.json, stamp finish time, validate the schema
            and rewrite /app/output/result.json. Never deletes filled content.
  dump      Print discovered inputs and case metadata for the agent's reference.

Default paths follow the runtime contract (/input, /app/output, /app/work) and
can be overridden for local testing. The script locates toolkit.py next to
itself, so it works from any current working directory.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import toolkit as tk  # noqa: E402


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"))
    parser.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    parser.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))


def _load_case(input_dir: str):
    """Return (case_dict_or_None, contract_or_None, case_id_or_None, errors)."""
    errors = []
    inputs = tk.discover_inputs(input_dir)
    case = None
    if inputs["case"]:
        case, err = tk.load_json(inputs["case"])
        if err:
            errors.append("case.json:{}".format(err))
            case = None
    contract = tk.extract_output_contract(case) if isinstance(case, dict) else None
    case_id = tk.extract_case_id(case) if isinstance(case, dict) else None
    return case, contract, case_id, errors, inputs


def cmd_init(args) -> int:
    os.makedirs(args.output, exist_ok=True)
    result_path = os.path.join(args.output, "result.json")

    # Idempotency: never overwrite an already-filled result.
    if os.path.isfile(result_path):
        prev, _ = tk.load_json(result_path)
        if isinstance(prev, dict) and prev.get("status") not in ("partial", "input_error", None):
            print(json.dumps({"skipped": True, "reason": "result already finalized",
                              "status": prev.get("status")}, ensure_ascii=False))
            return 0

    case, contract, case_id, errors, inputs = _load_case(args.input)
    skeleton = tk.build_skeleton(case_id, contract)
    for e in errors + inputs.get("errors", []):
        skeleton["errors"].append(e)
    for role, p in (("case", inputs["case"]), ("task", inputs["task"])):
        if p:
            skeleton["sources"].append({"path": p, "role": role})
    for p in inputs["materials"]:
        skeleton["sources"].append({"path": p, "role": "material"})
    if not case_id:
        skeleton["status"] = "input_error"
        skeleton["uncertainty"] = "无法确定 case_id，需人工检查 /input/case.json。"

    # Keep an editable plan in the work dir for the agent's reference.
    os.makedirs(args.work, exist_ok=True)
    plan_path = os.path.join(args.work, "plan.json")
    tk.write_json({"case_id": case_id, "contract": contract, "inputs": inputs}, plan_path)

    err = tk.write_json(skeleton, result_path)
    if err:
        print("ERROR {}".format(err), file=sys.stderr)
        return 1
    print(json.dumps({
        "case_id": case_id,
        "status": skeleton["status"],
        "result": result_path,
        "contract_keys": list(contract.keys()) if isinstance(contract, dict) else [],
        "materials": inputs["materials"],
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_finalize(args) -> int:
    os.makedirs(args.output, exist_ok=True)
    result_path = os.path.join(args.output, "result.json")
    data, err = tk.load_json(result_path)
    if err or not isinstance(data, dict):
        # Rebuild a skeleton instead of failing: always leave a valid result.
        case, contract, case_id, errors, _ = _load_case(args.input)
        data = tk.build_skeleton(case_id, contract)
        data["errors"] = data.get("errors", []) + [f"result_load:{err}"]
        for e in errors:
            data["errors"].append(e)
    runtime = data.get("runtime")
    if not isinstance(runtime, dict):
        runtime = {}
        data["runtime"] = runtime
    runtime["finished_at"] = tk.now_iso()

    problems = tk.validate_result(data)
    if problems:
        data["degradations"] = data.get("degradations", []) + [f"schema:{p}" for p in problems]
        if data.get("status") == "ok":
            data["status"] = "partial"

    err = tk.write_json(data, result_path)
    if err:
        print("ERROR {}".format(err), file=sys.stderr)
        return 1
    print(json.dumps({"status": data.get("status"), "schema_problems": problems},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_dump(args) -> int:
    case, contract, case_id, errors, inputs = _load_case(args.input)
    report = {
        "case_id": case_id,
        "contract_keys": list(contract.keys()) if isinstance(contract, dict) else [],
        "errors": errors + inputs.get("errors", []),
        "case_path": inputs["case"],
        "task_path": inputs["task"],
        "materials": inputs["materials"],
        "others": inputs["others"],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init", help="write an early valid result.json skeleton")
    _add_common(p_init)
    p_finalize = sub.add_parser("finalize", help="validate and finalize result.json")
    _add_common(p_finalize)
    p_dump = sub.add_parser("dump", help="print discovered inputs and metadata")
    _add_common(p_dump)

    args = parser.parse_args(argv)
    handlers = {"init": cmd_init, "finalize": cmd_finalize, "dump": cmd_dump}
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
