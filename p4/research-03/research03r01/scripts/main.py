#!/usr/bin/env python3
"""main.py — CLI for the evidence-centric systematic-review Skill.

Subcommands
-----------
  discover   print the /input inventory as JSON
  skeleton   write an early valid result.json + /app/work/run_state.json
  validate   validate one staged work file (protocol/screening/appraisal/
             evidence/synthesis) and print a JSON report
  merge      deep-merge a payload JSON into result.json (safe incremental update)
  finalize   deterministically assemble result.json, review.md,
             screening_log.json and evidence_map.json from /app/work
  selfcheck  run the full checklist and print a JSON report (always exit 0)

Common options (defaults match the runtime contract):
  --input  INPUT_DIR   default /input   (env INPUT_DIR)
  --output OUTPUT_DIR  default /app/output (env OUTPUT_DIR)
  --work   WORK_DIR    default /app/work   (env WORK_DIR)

Standard library only. The agent writes substantive content into /app/work;
the scripts validate and assemble it so JSON is never hand-edited.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resultlib as R          # noqa: E402
import selftest as E           # noqa: E402


def add_common(parser):
    parser.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"))
    parser.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    parser.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))


def _state_path(args):
    return os.path.join(args.work, "run_state.json")


def _load_state(args):
    obj, _, _ = R.load_json(_state_path(args))
    return obj if isinstance(obj, dict) else {}


def _load_result(args, state):
    result, errs, degs = R.load_json(os.path.join(args.output, "result.json"))
    if not isinstance(result, dict):
        extras = state.get("contract_extras") or {}
        result = R.new_skeleton(state.get("case_id") or "", extras,
                                "rebuilt during command")
        result["status"] = R.STATUS_INSUFFICIENT
    return result, errs, degs


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
    spec, extras = R.discover_contract_spec(case_obj if isinstance(case_obj, dict) else None)

    result_path = os.path.join(args.output, "result.json")
    summary = {"case_id": case_id, "contract_fields": sorted(extras.keys()),
               "wrote": False, "already_exists": False,
               "errors": [], "degradations": []}

    if os.path.exists(result_path):
        summary["already_exists"] = True
        existing, e2, _ = R.load_json(result_path)
        if not isinstance(existing, dict):
            result = R.new_skeleton(case_id, extras, "repaired unreadable result.json")
            summary["errors"].extend(R.write_json(result_path, result))
            summary["wrote"] = True
    else:
        result = R.new_skeleton(case_id, extras, "skeleton before review begins")
        result["errors"] = list(errs)
        result["degradations"] = list(degs)
        if not inv["exists"] or inv["total_files"] == 0:
            result["status"] = R.STATUS_INSUFFICIENT
            result.setdefault("limitations", []).append(
                "no input files found under %s" % args.input)
        elif not inv.get("case_json"):
            result["status"] = R.STATUS_PARTIAL
            result.setdefault("limitations", []).append(
                "case.json not found; review_question taken from task.md if readable")
        elif not isinstance(case_obj, dict):
            result["status"] = R.STATUS_INPUT_ERROR
            result.setdefault("limitations", []).append(
                "case.json present but unparseable")
        summary["errors"].extend(R.write_json(result_path, result))
        summary["wrote"] = not summary["errors"]

    state = {
        "case_id": case_id,
        "contract_spec": spec,
        "contract_extras": extras,
        "inventory": inv,
        "started_at": R.epoch_now(),
        "input_dir": args.input,
        "output_dir": args.output,
        "work_dir": args.work,
    }
    summary["errors"].extend(errs)
    summary["degradations"].extend(degs)
    summary["errors"].extend(R.write_json(_state_path(args), state))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_validate(args):
    report = None
    kind = args.kind
    if kind == "protocol":
        obj, errs, degs = E.load_protocol(args.work)
        report = E.validate_protocol(obj)
        report["errors"].extend(errs)
        report.setdefault("warnings", []).extend(degs)
    elif kind == "screening":
        records, errs, degs = E.load_screening(args.work)
        protocol, _, _ = E.load_protocol(args.work)
        report = E.validate_screening(records, E._criteria_ids(protocol))
        report["errors"].extend(errs)
    elif kind == "appraisal":
        records, errs, degs = E.load_appraisal(args.work)
        report = E.validate_appraisal(records)
        report["errors"].extend(errs)
    elif kind == "evidence":
        records, errs, degs = E.load_evidence(args.work)
        report = E.validate_evidence(records)
        report["errors"].extend(errs)
    elif kind == "synthesis":
        synthesis, errs, degs = E.load_synthesis(args.work)
        evidence, _, _ = E.load_evidence(args.work)
        report = E.validate_synthesis(synthesis, {"evidence": evidence})
        report["errors"].extend(errs)
    else:
        report = {"kind": kind, "errors": ["unknown kind %r" % kind], "warnings": []}
    print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_merge(args):
    payload, errs, degs = R.load_json(args.payload)
    if not isinstance(payload, dict):
        print(json.dumps({"ok": False, "errors": errs or ["payload not a JSON object"],
                          "degradations": degs}, ensure_ascii=False, indent=2))
        return
    state = _load_state(args)
    result, e2, d2 = _load_result(args, state)
    result = R.deep_merge(result, payload)
    result.setdefault("change_log", []).append({
        "ts": R.iso_now(), "action": "merge",
        "detail": "merged payload %s" % os.path.basename(args.payload),
    })
    errs.extend(R.write_json(os.path.join(args.output, "result.json"), result))
    print(json.dumps({"ok": not errs, "errors": errs,
                      "degradations": degs + d2}, ensure_ascii=False, indent=2))


def _sorted_work(records, key):
    return sorted(records, key=lambda r: str((r or {}).get(key) or ""))


def cmd_finalize(args):
    R.ensure_dir(args.output)
    R.ensure_dir(args.work)
    state = _load_state(args)
    result, errs, degs = _load_result(args, state)
    all_errs = list(errs)
    all_degs = list(degs)

    protocol, perrs, pdegs = E.load_protocol(args.work)
    all_errs.extend(perrs)
    all_degs.extend(pdegs)
    for key in ("review_question", "subquestions", "search_strategy",
                "inclusion_criteria", "exclusion_criteria"):
        if key in protocol:
            result[key] = protocol[key]

    if os.path.exists(E.work_file(args.work, "screening.jsonl")):
        records, serrs, sdegs = E.load_screening(args.work)
        all_errs.extend(serrs)
        all_degs.extend(sdegs)
        result["screening_records"] = _sorted_work(records, "source_id")
        result["prisma_flow"] = E.recompute_prisma(result["screening_records"])

    if os.path.exists(E.work_file(args.work, "quality_appraisal.jsonl")):
        appr, aerrs, adegs = E.load_appraisal(args.work)
        all_errs.extend(aerrs)
        all_degs.extend(adegs)
        result["quality_appraisal"] = _sorted_work(appr, "source_id")

    if os.path.exists(E.work_file(args.work, "evidence.jsonl")):
        evidence, everrs, evdegs = E.load_evidence(args.work)
        all_errs.extend(everrs)
        all_degs.extend(evdegs)
        result["evidence_map"]["evidence"] = _sorted_work(evidence, "evidence_id")

    quote_summary = E.verify_evidence_items(result["evidence_map"]["evidence"], args.input)
    all_degs.append("quote verification: %s" % json.dumps(quote_summary, ensure_ascii=False))

    if os.path.exists(E.work_file(args.work, "sources.jsonl")):
        sources, sserrs, ssdegs = E.load_sources(args.work)
        all_errs.extend(sserrs)
        all_degs.extend(ssdegs)
        result["sources"] = _sorted_work(sources, "source_id")
    elif result["evidence_map"]["evidence"]:
        ids = sorted({str(e.get("source_id")) for e in result["evidence_map"]["evidence"]
                      if isinstance(e, dict) and e.get("source_id")})
        result["sources"] = [{"source_id": sid, "file": None} for sid in ids]

    result["evidence_map"]["by_source"] = E.build_by_source(
        result["evidence_map"]["evidence"], result["sources"])

    if os.path.exists(E.work_file(args.work, "synthesis.json")):
        synthesis, synerrs, syndegs = E.load_synthesis(args.work)
        all_errs.extend(synerrs)
        all_degs.extend(syndegs)
        for key in ("conclusions", "conflicts", "gaps"):
            if key in synthesis:
                result["synthesis"][key] = synthesis[key]
        result["evidence_map"]["by_conclusion"] = E.build_by_conclusion(result["synthesis"])
        result["evidence_map"]["conflicts"] = list(result["synthesis"].get("conflicts") or [])
        for key in ("status", "uncertainty", "pending_human", "limitations"):
            if key in synthesis:
                result[key] = synthesis[key]
        if isinstance(synthesis.get("errors"), list):
            all_errs.extend(synthesis["errors"])
        if isinstance(synthesis.get("degradations"), list):
            all_degs.extend(synthesis["degradations"])

    result["errors"] = all_errs
    result["degradations"] = all_degs
    started = state.get("started_at")
    result["runtime_seconds"] = (round(R.epoch_now() - started, 1)
                                 if isinstance(started, (int, float)) else 0)
    result.setdefault("change_log", []).append({
        "ts": R.iso_now(), "action": "finalize",
        "detail": "deterministic assembly from /app/work",
    })

    write_errs = []
    write_errs.extend(R.write_json(os.path.join(args.output, "result.json"), result))
    write_errs.extend(R.write_json(os.path.join(args.output, "screening_log.json"),
                                   {"summary": result["prisma_flow"],
                                    "records": result["screening_records"]}))
    write_errs.extend(R.write_json(os.path.join(args.output, "evidence_map.json"),
                                   result["evidence_map"]))
    review = E.render_review(result)
    try:
        with open(os.path.join(args.output, "review.md"), "w", encoding="utf-8") as handle:
            handle.write(review)
    except Exception as exc:  # noqa: BLE001
        write_errs.append("write review.md failed: %s" % type(exc).__name__)

    print(json.dumps({
        "ok": not write_errs,
        "errors": write_errs + all_errs,
        "degradations": all_degs,
        "status": result["status"],
        "n_evidence": len(result["evidence_map"]["evidence"]),
        "n_screening": len(result["screening_records"]),
        "n_conclusions": len(result["synthesis"]["conclusions"]),
    }, ensure_ascii=False, indent=2))


def cmd_selfcheck(args):
    E.run_selfcheck(args.input, args.output, args.work)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_discover = sub.add_parser("discover", help="print input inventory")
    add_common(p_discover)
    p_discover.set_defaults(func=cmd_discover)

    p_skeleton = sub.add_parser("skeleton", help="write early valid result.json")
    add_common(p_skeleton)
    p_skeleton.set_defaults(func=cmd_skeleton)

    p_validate = sub.add_parser("validate", help="validate one work file")
    add_common(p_validate)
    p_validate.add_argument("--kind", required=True,
                            choices=("protocol", "screening", "appraisal",
                                     "evidence", "synthesis"))
    p_validate.set_defaults(func=cmd_validate)

    p_merge = sub.add_parser("merge", help="merge payload into result.json")
    add_common(p_merge)
    p_merge.add_argument("--payload", required=True)
    p_merge.set_defaults(func=cmd_merge)

    p_finalize = sub.add_parser("finalize", help="assemble final outputs")
    add_common(p_finalize)
    p_finalize.set_defaults(func=cmd_finalize)

    p_selfcheck = sub.add_parser("selfcheck", help="run checklist")
    add_common(p_selfcheck)
    p_selfcheck.set_defaults(func=cmd_selfcheck)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
