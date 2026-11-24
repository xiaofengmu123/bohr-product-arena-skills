#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thin CLI dispatcher for the systematic-review Skill (standard library only).

Subcommands
-----------
  discover   print the input inventory as JSON
  skeleton   write an early valid result.json (partial) + run_state.json
  record     validate and write one typed record to the canonical work files
  slug       derive a deterministic short id from a title/text
  finalize   deterministically replay work files into result.json + review.md
             + screening_log.json + evidence_map.json
  selfcheck  validate the emitted result.json (and re-check contract completeness)

Common options (defaults match the runtime contract):
  --input  INPUT_DIR    default /input
  --output OUTPUT_DIR   default /app/output
  --work   WORK_DIR     default /app/work

Run from the Skill root directory:
  python -B scripts/main.py <command> [options]
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as R


def add_common(parser):
    parser.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"),
                        help="input directory (default /input)")
    parser.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"),
                        help="output directory (default /app/output)")
    parser.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"),
                        help="work directory (default /app/work)")


def out(args, name):
    return os.path.join(args.output, name)


def work(args, name):
    return os.path.join(args.work, name)


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
    contract = R.build_contract(case_obj)
    result_path = out(args, "result.json")
    summary = {"case_id": case_id, "contract_fields": sorted(contract.keys()),
               "wrote": False, "already_exists": False, "errors": [], "degradations": []}

    if os.path.exists(result_path):
        summary["already_exists"] = True
        existing, e2, _ = R.load_json(result_path)
        if not isinstance(existing, dict):
            R.write_json(result_path, R.new_skeleton(case_id, contract, note="repaired unreadable result.json"))
            summary["wrote"] = True
    else:
        result = R.new_skeleton(case_id, contract, note="skeleton before review begins")
        result["errors"] = list(errs)
        result["degradations"] = list(degs)
        state = R.input_state(inv, case_obj)
        if state == "insufficient":
            result["status"] = R.STATUS_INSUFFICIENT
            result.setdefault("limitations", []).append("no case.json/task.md/materials found under %s" % args.input)
        elif state == "input_error":
            result["status"] = R.STATUS_INPUT_ERROR
            result.setdefault("limitations", []).append("case.json present but unparseable")
        werrs = R.write_json(result_path, result)
        summary["wrote"] = not bool(werrs)
        summary["errors"].extend(werrs)

    state = {
        "case_id": case_id, "contract": contract, "inventory": inv,
        "started_at": R.epoch_now(), "input_dir": args.input,
        "output_dir": args.output, "work_dir": args.work,
        "input_errors": list(errs), "input_degradations": list(degs),
    }
    summary["errors"].extend(errs)
    summary["degradations"].extend(degs)
    summary["errors"].extend(R.write_json(work(args, "run_state.json"), state))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_record(args):
    if args.json is not None:
        try:
            obj = json.loads(args.json)
        except Exception as exc:
            print(json.dumps({"ok": False, "errors": ["bad --json: %s" % exc]}, ensure_ascii=False))
            return
    elif args.file:
        obj, errs, _ = R.load_json(args.file)
        if errs or not isinstance(obj, (dict, list)):
            print(json.dumps({"ok": False, "errors": errs or ["record file not a valid JSON object/array"]},
                             ensure_ascii=False))
            return
    else:
        print(json.dumps({"ok": False, "errors": ["record needs --json or --file"]}, ensure_ascii=False))
        return

    verr = R.validate_record(args.kind, obj)
    if verr:
        print(json.dumps({"ok": False, "kind": args.kind, "errors": verr}, ensure_ascii=False))
        return
    if args.kind in ("screening", "quality", "evidence"):
        if not isinstance(obj, dict):
            print(json.dumps({"ok": False, "errors": ["%s event must be an object" % args.kind]},
                             ensure_ascii=False))
            return
        werr = R.append_jsonl(work(args, args.kind + ".jsonl"), obj)
    else:
        werr = R.write_json(work(args, args.kind + ".json"), obj)
    print(json.dumps({"ok": not werr, "kind": args.kind, "errors": werr}, ensure_ascii=False))


def cmd_slug(args):
    print(R.slug(args.text))


def cmd_finalize(args):
    R.ensure_dir(args.output)
    R.ensure_dir(args.work)
    state, _, _ = R.load_json(work(args, "run_state.json"))
    if not isinstance(state, dict):
        state = {}
    inv = state.get("inventory") or R.discover_input(args.input)
    case_obj = None
    if inv.get("case_json"):
        case_obj, _, _ = R.load_json(inv["case_json"])
    case_id = state.get("case_id") or (R.extract_case_id(case_obj) if isinstance(case_obj, dict) else "") or "unknown"
    contract = state.get("contract") or R.build_contract(case_obj)

    workdata, werr = R.load_work(args.work)
    state_ = R.input_state(inv, case_obj)
    report = R.validate_work(workdata, contract)
    status = R.assign_status(report, state_)

    started = state.get("started_at")
    runtime = int(R.epoch_now() - started) if isinstance(started, (int, float)) else 0
    errors = list(werr) + list(state.get("input_errors") or [])
    degradations = list(state.get("input_degradations") or [])
    limitations = []
    for s in workdata.get("sources") or []:
        for f in (s.get("flags") or []):
            degradations.append("source %s: %s" % (s.get("source_id"), f))
    if state_ == "insufficient":
        limitations.append("no usable input materials")
    if state_ == "input_error":
        limitations.append("input unparseable")
    if report.get("ok") and status != R.STATUS_OK:
        limitations.append("validation passed but status downgraded by input state")

    pending = R._build_pending(workdata, report, state_)
    result = R.assemble_result(workdata, case_id, contract, status, report, runtime,
                               errors, degradations, limitations, pending)

    werrs = R.write_json(out(args, "result.json"), result)
    werrs += R.write_json(out(args, "screening_log.json"), {
        "case_id": case_id, "prisma": report.get("prisma", {}),
        "records": R.build_screening_log(workdata.get("sources") or [], workdata.get("screening") or []),
    })
    werrs += R.write_json(out(args, "evidence_map.json"), R.build_evidence_map(workdata.get("evidence") or []))
    werrs += R.write_text(out(args, "review.md"), R.render_review_md(result))
    print(json.dumps({"ok": not werrs, "status": status, "checks": report["checks"],
                      "errors": werrs}, ensure_ascii=False, indent=2))


def cmd_selfcheck(args):
    result, errs, _ = R.load_json(out(args, "result.json"))
    if not isinstance(result, dict):
        print(json.dumps({"ok": False, "errors": errs or ["result.json missing or invalid"],
                          "checks": []}, ensure_ascii=False, indent=2))
        sys.exit(1 if args.strict else 0)
    state, _, _ = R.load_json(work(args, "run_state.json"))
    contract = (state or {}).get("contract") or R.DEFAULT_CONTRACT
    checks = R.selfcheck_result(result, contract)
    missing = [k for k in contract if k not in result]
    ok = bool(checks) and all(c["pass"] for c in checks) and not missing
    print(json.dumps({"ok": ok, "status": result.get("status"), "missing_contract": missing,
                      "checks": checks}, ensure_ascii=False, indent=2))
    sys.exit(0 if (ok or not args.strict) else 1)


def build_parser():
    parser = argparse.ArgumentParser(prog="scripts/main.py",
                                     description="Systematic literature review runtime helper")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("discover", help="print input inventory as JSON")
    add_common(p)
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("skeleton", help="write early valid result.json + run_state.json")
    add_common(p)
    p.set_defaults(func=cmd_skeleton)

    p = sub.add_parser("record", help="validate and write one typed record")
    add_common(p)
    p.add_argument("--kind", required=True,
                   choices=["protocol", "sources", "synthesis", "screening", "quality", "evidence"])
    p.add_argument("--json", default=None, help="inline JSON payload")
    p.add_argument("--file", default=None, help="JSON file to read the payload from")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("slug", help="derive a deterministic id from text")
    p.add_argument("text", help="title/text to slug")
    p.set_defaults(func=cmd_slug)

    p = sub.add_parser("finalize", help="replay work files into final artifacts")
    add_common(p)
    p.set_defaults(func=cmd_finalize)

    p = sub.add_parser("selfcheck", help="validate result.json")
    add_common(p)
    p.add_argument("--strict", action="store_true", help="exit 1 when any check fails")
    p.set_defaults(func=cmd_selfcheck)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    try:
        args.func(args)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:  # never crash the harness
        print(json.dumps({"ok": False, "command": args.command, "errors": [repr(exc)]},
                         ensure_ascii=False))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
