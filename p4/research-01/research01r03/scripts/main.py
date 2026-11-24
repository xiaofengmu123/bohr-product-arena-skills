#!/usr/bin/env python3
"""Deep-research skill runtime helper (standard library only, deterministic).

Subcommands:
  discover   print the input inventory as JSON
  skeleton   write an early valid result.json (status=partial) + run_state.json
  merge      deep-merge a model-authored payload JSON into result.json
  finalize   deterministically assemble result.json + report.md + evidence_map.json
  selfcheck  validate result.json and report six-point coverage
  selftest   run self-tests incl. a byte-identical determinism check

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
import resultlib as R  # noqa: E402


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


def load_run_state(args):
    state, _, _ = R.load_json(work_path(args, "run_state.json"))
    return state if isinstance(state, dict) else None


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
    summary = {"case_id": case_id, "wrote": False, "already_exists": False,
               "errors": [], "degradations": []}

    if os.path.exists(result_path):
        summary["already_exists"] = True
        existing, e2, _ = R.load_json(result_path)
        if not isinstance(existing, dict):
            existing = R.new_skeleton(case_id, contract, note="repaired unreadable result.json")
        else:
            # Idempotent: never discard an existing partial deliverable.
            for key in contract:
                existing.setdefault(key, R.default_value(key))
        summary["errors"].extend(R.write_json(result_path, existing))
        summary["wrote"] = not bool(summary["errors"])
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
        elif bool(inv.get("case_json")) and not isinstance(case_obj, dict):
            result["status"] = R.STATUS_INPUT_ERROR
            result.setdefault("limitations", []).append(
                "case.json present but unparseable; contract fields fall back to defaults")
        write_errs = R.write_json(result_path, result)
        summary["errors"].extend(write_errs)
        summary["wrote"] = not bool(write_errs)

    state = {"case_id": case_id, "contract": contract, "inventory": inv,
             "input_dir": args.input, "output_dir": args.output, "work_dir": args.work}
    summary["errors"].extend(errs)
    summary["degradations"].extend(degs)
    summary["errors"].extend(R.write_json(work_path(args, "run_state.json"), state))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_merge(args):
    result_path = out_path(args, "result.json")
    payload, errs, degs = R.load_json(args.payload)
    if not isinstance(payload, dict):
        print(json.dumps({"ok": False,
                          "errors": errs or ["payload is not a JSON object"],
                          "degradations": degs}, ensure_ascii=False, indent=2))
        return
    result, _, _ = R.load_json(result_path)
    if not isinstance(result, dict):
        state = load_run_state(args) or {}
        result = R.new_skeleton(
            state.get("case_id", ""),
            state.get("contract") or R.build_contract(None),
            note="created during merge",
        )
    result = R.deep_merge(result, payload)
    change_log = result.get("change_log")
    if isinstance(change_log, list):
        seq = 1
        for entry in change_log:
            if isinstance(entry, dict):
                try:
                    seq = max(seq, int(entry.get("seq", 0)) + 1)
                except (TypeError, ValueError):
                    pass
        change_log.append({"seq": seq, "phase": "merge", "action": "merge",
                           "detail": "merged payload %s" % os.path.basename(args.payload)})
    errs.extend(R.write_json(result_path, result))
    print(json.dumps({"ok": not errs, "errors": errs, "degradations": degs},
                     ensure_ascii=False, indent=2))


def cmd_finalize(args):
    result, errs, degs = R.load_json(out_path(args, "result.json"))
    if not isinstance(result, dict):
        state = load_run_state(args) or {}
        result = R.new_skeleton(
            state.get("case_id", ""),
            state.get("contract") or R.build_contract(None),
            note="created at finalize",
        )
    result = R.finalize_result(result, "finalize")
    errs.extend(R.write_json(out_path(args, "result.json"), result))
    errs.extend(R.write_text(out_path(args, "report.md"), R.render_report(result)))
    errs.extend(R.write_json(out_path(args, "evidence_map.json"), R.render_evidence_map(result)))
    errs.extend(R.write_json(work_path(args, "run_metadata.json"), R.run_metadata(result)))
    print(json.dumps({"ok": not errs, "errors": errs, "degradations": degs,
                      "fingerprint": R.content_fingerprint(result)},
                     ensure_ascii=False, indent=2))


def cmd_selfcheck(args):
    result, errs, degs = R.load_json(out_path(args, "result.json"))
    problems = []
    if not isinstance(result, dict):
        problems.append("result.json missing or unparseable")
        result = {}
    problems.extend(R.validate_result(result))
    print(json.dumps({
        "valid_json": isinstance(result, dict),
        "problems": problems,
        "read_errors": errs,
        "degradations": degs,
        "six_point": R.coverage_six(result, args.work),
    }, ensure_ascii=False, indent=2))


def cmd_selftest(args):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import selftest
    sys.exit(selftest.run_selftest())


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    add_common(sub.add_parser("discover", help="print input inventory"))
    add_common(sub.add_parser("skeleton", help="write early partial result skeleton"))
    m = sub.add_parser("merge", help="merge a payload into result.json")
    add_common(m)
    m.add_argument("--payload", required=True, help="payload JSON path")
    add_common(sub.add_parser("finalize", help="assemble deterministic final result"))
    add_common(sub.add_parser("selfcheck", help="validate result and six-point coverage"))
    add_common(sub.add_parser("selftest", help="run self-tests (SKILL.md, determinism, degradation)"))
    return parser


def main():
    args = build_parser().parse_args()
    handlers = {
        "discover": cmd_discover,
        "skeleton": cmd_skeleton,
        "merge": cmd_merge,
        "finalize": cmd_finalize,
        "selfcheck": cmd_selfcheck,
        "selftest": cmd_selftest,
    }
    handlers[args.cmd](args)


if __name__ == "__main__":
    main()
