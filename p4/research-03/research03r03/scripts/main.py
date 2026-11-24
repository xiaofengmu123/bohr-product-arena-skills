#!/usr/bin/env python3
"""Verifiable systematic literature review — runtime helper (standard library only).

Subcommands
-----------
  discover   print the input inventory as JSON
  skeleton   write an early valid result.json + /app/work/run_state.json
  merge      deep-merge a payload JSON into result.json (safe incremental update)
  finalize   deterministically assemble result.json, review.md, screening_log.json,
             evidence_map.json from /app/work artifacts
  selfcheck  validate result.json: contract, screening audit, PRISMA recount,
             verbatim-quote checks, determinism round-trip

Common options (defaults match the runtime contract):
  --input  INPUT_DIR   default /input
  --output OUTPUT_DIR  default /app/output
  --work   WORK_DIR    default /app/work
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as R


def add_common(parser):
    parser.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"))
    parser.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    parser.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))


def out_path(args, name):
    return os.path.join(args.output, name)


def work_path(args, name):
    return os.path.join(args.work, name)


def cmd_discover(args):
    print(json.dumps(R.discover_input(args.input), ensure_ascii=False, indent=2))


def cmd_skeleton(args):
    R.ensure_dir(args.output)
    R.ensure_dir(args.work)
    inv = R.discover_input(args.input)
    case_obj, errs, degs = None, [], []
    if inv.get("case_json"):
        case_obj, errs, degs = R.load_json(inv["case_json"])
    case_id = R.extract_case_id(case_obj) if isinstance(case_obj, dict) else ""
    contract = R.build_contract(case_obj)

    result_path = out_path(args, "result.json")
    summary = {"case_id": case_id, "contract_fields": sorted(contract.keys()),
               "wrote": False, "already_exists": False, "status": R.STATUS_PARTIAL,
               "errors": [], "degradations": []}

    if os.path.exists(result_path):
        summary["already_exists"] = True
        existing, e2, d2 = R.load_json(result_path)
        if not isinstance(existing, dict):
            result = R.new_skeleton(case_id, contract, R.STATUS_PARTIAL,
                                    note="repaired unreadable result.json")
            R.write_json(result_path, result)
            summary["wrote"] = True
        else:
            result = existing
            summary["status"] = result.get("status", R.STATUS_PARTIAL)
    else:
        result = R.new_skeleton(case_id, contract, R.STATUS_PARTIAL,
                                note="skeleton before systematic review begins")
        result["errors"] = list(errs)
        result["degradations"] = list(degs)
        if not inv["exists"] or inv["total_files"] == 0:
            result["status"] = R.STATUS_INSUFFICIENT
            result.setdefault("limitations", []).append(
                "no case.json/task.md/materials found under %s" % args.input)
        elif not inv.get("case_json"):
            result["status"] = R.STATUS_PARTIAL
            result.setdefault("limitations", []).append(
                "case.json not found; review_question taken from task.md if readable")
        elif case_obj is None:
            result["status"] = R.STATUS_INPUT_ERROR
            result.setdefault("limitations", []).append(
                "case.json present but unparseable; contract fields fall back to defaults")
        werrs = R.write_json(result_path, result)
        summary["wrote"] = not bool(werrs)
        summary["errors"].extend(werrs)

    summary["status"] = result.get("status", R.STATUS_PARTIAL)
    state = {
        "case_id": case_id, "contract": contract, "inventory": inv,
        "started_epoch": R.epoch_now(), "input_dir": args.input,
        "output_dir": args.output, "work_dir": args.work,
    }
    summary["errors"].extend(errs)
    summary["degradations"].extend(degs)
    summary["errors"].extend(R.write_json(work_path(args, "run_state.json"), state))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_merge(args):
    payload, errs, degs = R.load_json(args.payload)
    if not isinstance(payload, dict):
        print(json.dumps({"ok": False, "errors": errs or ["payload not a JSON object"],
                          "degradations": degs}, ensure_ascii=False, indent=2))
        return
    result_path = out_path(args, "result.json")
    result, e2, d2 = R.load_json(result_path)
    state, _, _ = R.load_json(work_path(args, "run_state.json"))
    contract = (state or {}).get("contract") or R.DEFAULT_CONTRACT
    if not isinstance(result, dict):
        result = R.new_skeleton(R.extract_case_id(state or {}), contract,
                                R.STATUS_PARTIAL, note="rebuilt for merge")
    result = R.deep_merge(result, payload)
    log = result.setdefault("change_log", [])
    log.append({"seq": len(log), "action": "merge",
                "note": "merged payload %s" % os.path.basename(args.payload)})
    errs.extend(R.write_json(result_path, result))
    print(json.dumps({"ok": not errs, "errors": errs, "degradations": degs + d2},
                     ensure_ascii=False, indent=2))


def cmd_finalize(args):
    R.ensure_dir(args.output)
    inv = R.discover_input(args.input)
    case_obj, errs, degs = None, [], []
    if inv.get("case_json"):
        case_obj, errs, degs = R.load_json(inv["case_json"])
    case_id = R.extract_case_id(case_obj) if isinstance(case_obj, dict) else ""
    contract = R.build_contract(case_obj)
    data, werrs, wdegs = R.read_work_data(args.work)
    material_count = len(inv.get("materials", [])) if inv.get("exists") else 0
    override = R.read_status_override(args.work)
    status = override if override in R.STATUSES else R.derive_status(data, material_count, errs)

    result = R.build_result(case_id, contract, data, status)
    result["errors"] = list(errs) + list(werrs)
    result["degradations"] = list(degs) + list(wdegs)

    werr = R.write_json(out_path(args, "result.json"), result)
    werr += R.write_text(out_path(args, "review.md"), R.build_review_md(result))
    werr += R.write_json(out_path(args, "screening_log.json"), result.get("screening_records", []))
    werr += R.write_json(out_path(args, "evidence_map.json"), result.get("evidence_map", []))
    print(json.dumps({"ok": not werr, "status": status, "case_id": result.get("case_id"),
                      "errors": result["errors"], "degradations": result["degradations"]},
                     ensure_ascii=False, indent=2))


def cmd_selfcheck(args):
    contract = None
    state, _, _ = R.load_json(work_path(args, "run_state.json"))
    if isinstance(state, dict):
        contract = state.get("contract")
    rep = R.run_selfcheck(out_path(args, "result.json"), args.input, args.work, contract)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        for item in rep["checks"]:
            print("[%s] %s%s" % ("OK" if item["ok"] else "!!", item["check"],
                                 (" — " + item["detail"]) if item["detail"] else ""))
        for line in rep["fatal"]:
            print("[FATAL] " + line)
        for line in rep["warnings"]:
            print("[WARN] " + line)
    raise SystemExit(0 if rep["ok"] else 1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")
    for name in ("discover", "skeleton", "merge", "finalize", "selfcheck"):
        p = sub.add_parser(name)
        add_common(p)
        if name == "merge":
            p.add_argument("--payload", required=True)
        if name == "selfcheck":
            p.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        raise SystemExit(0)
    handler = {
        "discover": cmd_discover,
        "skeleton": cmd_skeleton,
        "merge": cmd_merge,
        "finalize": cmd_finalize,
        "selfcheck": cmd_selfcheck,
    }[args.command]
    handler(args)


if __name__ == "__main__":
    main()
