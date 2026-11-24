#!/usr/bin/env python3
"""Deep-research skill runtime helper (standard library only).

Subcommands
  discover   print the input inventory as JSON
  skeleton   write an early valid result.json + run_state.json
  merge      deep-merge a payload JSON into result.json (safe incremental update)
  finalize   assemble result.json, report.md, evidence_map.json and copy work notes
  selfcheck  validate result.json and report six-point coverage

Common options (defaults match the runtime contract):
  --input  INPUT_DIR   default /input
  --output OUTPUT_DIR  default /app/output
  --work   WORK_DIR    default /app/work
"""

import argparse
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resultlib as R


def add_common(parser):
    parser.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"),
                        help="input directory (default /input)")
    parser.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"),
                        help="output directory (default /app/output)")
    parser.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"),
                        help="work directory (default /app/work)")


def out_path(args, name):
    return os.path.join(args.output, name)


def work_path(args, name):
    return os.path.join(args.work, name)


def run_state(args):
    return {"case_id": "", "contract": {}, "inventory": {}, "started_at": R.epoch_now(),
            "input_dir": args.input, "output_dir": args.output, "work_dir": args.work}


def load_run_state(args):
    obj, _, _ = R.load_json(work_path(args, "run_state.json"))
    return obj if isinstance(obj, dict) else None


def cmd_discover(args):
    print(json.dumps(R.discover_input(args.input), ensure_ascii=False, indent=2))


def cmd_skeleton(args):
    R.ensure_dir(args.output)
    R.ensure_dir(args.work)
    inv = R.discover_input(args.input)
    case_obj, errs, degs = (None, [], [])
    if inv.get("case_json"):
        case_obj, errs, degs = R.load_json(inv["case_json"])
    case_id = R.extract_case_id(case_obj) if isinstance(case_obj, dict) else ""
    contract = R.build_contract(case_obj if isinstance(case_obj, dict) else None)

    result_path = out_path(args, "result.json")
    summary = {"case_id": case_id, "contract_fields": sorted(contract.keys()),
               "wrote": False, "already_exists": False, "errors": [], "degradations": []}

    if os.path.exists(result_path):
        summary["already_exists"] = True
        existing, e2, _ = R.load_json(result_path)
        if not isinstance(existing, dict):
            result = R.new_skeleton(case_id, contract, note="repaired unreadable result.json")
            summary["errors"].extend(R.write_json(result_path, result))
            summary["wrote"] = True
    else:
        note = "skeleton before research begins"
        if not inv["exists"] or inv["total_files"] == 0:
            note = "empty input directory; no materials found"
        result = R.new_skeleton(case_id, contract, note=note)
        result["errors"] = list(errs)
        result["degradations"] = list(degs)
        if not inv["exists"] or inv["total_files"] == 0 or not inv.get("case_json"):
            result["status"] = R.STATUS_INSUFFICIENT
            result.setdefault("limitations", []).append(
                "no case.json/task.md/materials found under %s" % args.input)
        elif not isinstance(case_obj, dict):
            result["status"] = R.STATUS_INPUT_ERROR
            result.setdefault("limitations", []).append(
                "case.json present but unparseable; contract fields fall back to defaults")
        summary["errors"].extend(R.write_json(result_path, result))
        summary["wrote"] = not summary["errors"]

    state = run_state(args)
    state.update({"case_id": case_id, "contract": contract, "inventory": inv,
                  "started_at": R.epoch_now()})
    summary["errors"].extend(errs)
    summary["degradations"].extend(degs)
    summary["errors"].extend(R.write_json(work_path(args, "run_state.json"), state))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_merge(args):
    result_path = out_path(args, "result.json")
    payload, errs, degs = R.load_json(args.payload)
    if not isinstance(payload, dict):
        print(json.dumps({"ok": False, "errors": errs or ["payload not a JSON object"],
                          "degradations": degs}, ensure_ascii=False, indent=2))
        return
    result, e2, d2 = R.load_json(result_path)
    if not isinstance(result, dict):
        state = load_run_state(args) or {}
        contract = state.get("contract") or copy.deepcopy(R.DEFAULT_CONTRACT)
        case_id = state.get("case_id") or R.extract_case_id(payload)
        result = R.new_skeleton(case_id, contract, note="created during merge")
    result = R.deep_merge(result, payload)
    result.setdefault("change_log", []).append({
        "ts": R.iso_now(), "action": "merge",
        "detail": "merged payload %s" % os.path.basename(args.payload),
    })
    errs.extend(R.write_json(result_path, result))
    print(json.dumps({"ok": not errs, "errors": errs, "degradations": degs + d2},
                     ensure_ascii=False, indent=2))


def cmd_finalize(args):
    result_path = out_path(args, "result.json")
    result, errs, degs = R.load_json(result_path)
    if not isinstance(result, dict):
        state = load_run_state(args) or {}
        result = R.new_skeleton(state.get("case_id", ""), state.get("contract") or R.DEFAULT_CONTRACT,
                                note="created during finalize")
    R.ensure_dir(args.output)
    errs.extend(R.finalize_artifacts(args, result))
    result.setdefault("change_log", []).append({
        "ts": R.iso_now(), "action": "finalize", "detail": "assembled report and evidence map"})
    errs.extend(R.write_json(result_path, result))
    print(json.dumps({"ok": not errs, "errors": errs, "degradations": degs,
                      "status": result.get("status")}, ensure_ascii=False, indent=2))


def cmd_selfcheck(args):
    result, errs, _ = R.load_json(out_path(args, "result.json"))
    if not isinstance(result, dict):
        print(json.dumps({"ok": False, "summary": "result.json missing or unparsable",
                          "errors": errs, "checks": []}, ensure_ascii=False, indent=2))
        return
    report = R.selfcheck(result, args)
    report["errors"] = errs
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("discover", "skeleton", "finalize", "selfcheck"):
        add_common(sub.add_parser(name))
    mp = sub.add_parser("merge")
    add_common(mp)
    mp.add_argument("--payload", required=True, help="path to a JSON object to deep-merge")
    args = parser.parse_args(argv)

    handlers = {"discover": cmd_discover, "skeleton": cmd_skeleton,
                "merge": cmd_merge, "finalize": cmd_finalize, "selfcheck": cmd_selfcheck}
    handlers[args.cmd](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
