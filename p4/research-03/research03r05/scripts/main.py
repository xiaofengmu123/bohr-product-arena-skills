#!/usr/bin/env python3
"""Systematic literature review runtime helper (standard library only).

Subcommands
-----------
  discover   print the input inventory and discovered contract keys
  schema     print the normalized output contract (field -> type spec)
  skeleton   write an early valid result.json + /app/work/run_state.json
  merge      deep-merge a payload JSON into result.json (incremental update)
  finalize   assemble result.json + review.md + screening_log.json +
             evidence_map.json + contract_report.json
  selfcheck  validate result.json; report contract coverage, screening audit,
             evidence backlinks and recomputed PRISMA counts

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
import core_impl as R


def out_path(args, name):
    return os.path.join(args.output, name)


def work_path(args, name):
    return os.path.join(args.work, name)


def add_common(parser):
    parser.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"),
                        help="input directory (default /input)")
    parser.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"),
                        help="output directory (default /app/output)")
    parser.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"),
                        help="work directory (default /app/work)")


def load_run_state(args):
    obj, _, _ = R.load_json(work_path(args, "run_state.json"))
    return obj if isinstance(obj, dict) else None


def load_result(args):
    result, errs, degs = R.load_json(out_path(args, "result.json"))
    state = load_run_state(args) or {}
    contract = state.get("contract") or copy.deepcopy(R.DEFAULT_CONTRACT)
    if not isinstance(result, dict):
        result = R.new_skeleton(state.get("case_id", ""), contract,
                                note="repaired unreadable result.json")
        result["status"] = R.STATUS_INSUFFICIENT
        result["errors"] = list(errs)
    return result, state, contract, errs, degs


def cmd_discover(args):
    inv = R.discover_input(args.input)
    case_obj = None
    contract = copy.deepcopy(R.DEFAULT_CONTRACT)
    if inv.get("case_json"):
        case_obj, errs, degs = R.load_json(inv["case_json"])
        inv["errors"].extend(errs)
        inv["degradations"].extend(degs)
        if isinstance(case_obj, dict):
            contract = R.build_contract(case_obj)
    inv["contract_fields"] = sorted(contract.keys())
    inv["case_id"] = R.extract_case_id(case_obj) if isinstance(case_obj, dict) else ""
    return inv


def cmd_schema(args):
    inv = R.discover_input(args.input)
    case_obj = None
    if inv.get("case_json"):
        case_obj, errs, degs = R.load_json(inv["case_json"])
    contract = R.build_contract(case_obj if isinstance(case_obj, dict) else None)
    out = {"case_id": R.extract_case_id(case_obj) if isinstance(case_obj, dict) else "",
           "contract": contract,
           "honest_fields": dict(R.HONEST_FIELDS)}
    return out


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
            result["errors"] = list(errs)
            R.write_json(result_path, result)
            summary["wrote"] = True
    else:
        result = R.new_skeleton(case_id, contract, note="skeleton before systematic review")
        result["errors"] = sorted(set(errs))
        result["degradations"] = sorted(set(degs))
        if not inv["exists"] or inv["total_files"] == 0:
            result["status"] = R.STATUS_INSUFFICIENT
            result.setdefault("limitations", []).append("no files under %s" % args.input)
        elif not inv.get("case_json"):
            result["status"] = R.STATUS_PARTIAL
            result.setdefault("limitations", []).append(
                "case.json not found; contract falls back to defaults")
        elif bool(inv.get("case_json")) and not isinstance(case_obj, dict):
            result["status"] = R.STATUS_INPUT_ERROR
            result.setdefault("limitations", []).append("case.json unparseable")
        R.write_json(result_path, result)
        summary["wrote"] = True

    state = {
        "case_id": case_id, "contract": contract, "inventory": inv,
        "started_at": R.epoch_now(), "input_dir": args.input,
        "output_dir": args.output, "work_dir": args.work,
    }
    summary["errors"].extend(errs)
    summary["degradations"].extend(degs)
    summary["errors"].extend(R.write_json(work_path(args, "run_state.json"), state))
    return summary


def cmd_merge(args):
    payload, errs, degs = R.load_json(args.payload)
    if not isinstance(payload, dict):
        return {"ok": False, "errors": errs or ["payload not a JSON object"],
                "degradations": degs}
    result, _, _, e2, d2 = load_result(args)
    before = copy.deepcopy(result)
    result = R.deep_merge(result, payload)
    result["change_log"] = result.get("change_log") or []
    result["change_log"].append({
        "ts": R.iso_now(), "action": "merge",
        "detail": "merged payload %s" % os.path.basename(args.payload),
        "fields": R.changed_top_keys(before, result),
    })
    errs.extend(R.write_json(out_path(args, "result.json"), result))
    return {"ok": not errs, "errors": errs, "degradations": degs + d2}


def write_sidecars(args, result, contract):
    errs = []
    errs += R.write_json(out_path(args, "review.md"), R.format_review_md(result))
    screening_records = result.get("screening_records") or []
    errs += R.write_json(out_path(args, "screening_log.json"),
                         {"records": screening_records,
                          "counts": R.recompute_prisma(screening_records)})
    evidence = result.get("evidence_map") or []
    evidence_map = {"records": evidence, "index": {}}
    for i, card in enumerate(evidence):
        if isinstance(card, dict):
            eid = card.get("evidence_id") or card.get("id")
            evidence_map["index"][str(eid if eid is not None else i)] = card
    errs += R.write_json(out_path(args, "evidence_map.json"), evidence_map)
    errs += R.write_json(out_path(args, "contract_report.json"),
                         R.build_contract_report(result, contract))
    return errs


def cmd_finalize(args):
    R.ensure_dir(args.output)
    R.ensure_dir(args.work)
    result, state, contract, errs, degs = load_result(args)
    notes = []

    protocol, e, d = R.load_json(work_path(args, "protocol.json"))
    errs += e
    degs += d
    if isinstance(protocol, dict):
        for key in ("review_question", "subquestions", "search_strategy",
                    "inclusion_criteria", "exclusion_criteria"):
            if key in protocol:
                result[key] = protocol[key]
    else:
        notes.append("protocol.json missing or invalid; protocol fields left as-is")

    screening, e, d = R.read_jsonl(work_path(args, "screening.jsonl"))
    errs += e
    degs += d
    if screening:
        result["screening_records"] = screening
        counts = R.recompute_prisma(screening)
        pf = result.get("prisma_flow")
        if not isinstance(pf, dict):
            pf = {}
        for key in ("identified", "deduplicated", "screened_title_abstract",
                    "excluded_title_abstract", "screened_fulltext",
                    "excluded_fulltext", "included", "undetermined"):
            pf[key] = counts[key]
        pf.setdefault("notes", "recomputed by finalize from screening_records")
        result["prisma_flow"] = pf

    quality, e, d = R.read_jsonl(work_path(args, "quality_appraisal.jsonl"))
    errs += e
    degs += d
    if quality:
        result["quality_appraisal"] = quality

    evidence, e, d = R.read_jsonl(work_path(args, "evidence_cards.jsonl"))
    errs += e
    degs += d
    if evidence:
        result["evidence_map"] = evidence

    synthesis, e, d = R.load_json(work_path(args, "synthesis.json"))
    errs += e
    degs += d
    if isinstance(synthesis, dict):
        result["synthesis"] = synthesis

    result["errors"] = sorted(set(result.get("errors") or []) | set(errs))
    result["degradations"] = sorted(set(result.get("degradations") or []) | set(degs))

    # Honest status: never claim ok unless the five-stage chain is traceable.
    # Preserve insufficient_evidence / input_error set earlier by skeleton.
    original_status = result.get("status")
    if original_status not in (R.STATUS_INSUFFICIENT, R.STATUS_INPUT_ERROR):
        if not isinstance(result.get("review_question"), str) or not result["review_question"].strip():
            result["status"] = R.STATUS_PARTIAL
            notes.append("review_question empty")
        elif not result.get("screening_records"):
            result["status"] = R.STATUS_PARTIAL
            notes.append("no screening records")
        elif not result.get("evidence_map"):
            result["status"] = R.STATUS_PARTIAL
            notes.append("no evidence_map")
        elif not (result.get("synthesis") or {}).get("conclusions"):
            result["status"] = R.STATUS_PARTIAL
            notes.append("no synthesis conclusions")

    started = (state or {}).get("started_at")
    if isinstance(started, (int, float)):
        result["runtime_seconds"] = max(0, R.epoch_now() - int(started))
    else:
        result["runtime_seconds"] = None

    result["artifacts"] = [
        {"name": "result.json", "path": "result.json", "kind": "json"},
        {"name": "review.md", "path": "review.md", "kind": "markdown"},
        {"name": "screening_log.json", "path": "screening_log.json", "kind": "json"},
        {"name": "evidence_map.json", "path": "evidence_map.json", "kind": "json"},
        {"name": "contract_report.json", "path": "contract_report.json", "kind": "json"},
    ]

    result["change_log"] = result.get("change_log") or []
    result["change_log"].append({
        "ts": R.iso_now(), "action": "finalize",
        "detail": "; ".join(notes) if notes else "assembled",
        "fields": ["screening_records", "quality_appraisal", "evidence_map",
                   "synthesis", "prisma_flow", "status", "artifacts", "runtime_seconds"],
    })

    errs += R.write_json(out_path(args, "result.json"), result)
    errs += write_sidecars(args, result, contract)
    return {"ok": not errs, "status": result.get("status"), "notes": notes,
            "errors": errs, "degradations": degs}


def cmd_selfcheck(args):
    result, state, contract, errs, degs = load_result(args)
    report = {
        "json_parse": isinstance(result, dict),
        "case_id": bool(str(result.get("case_id") or "").strip()),
        "status": result.get("status") if isinstance(result, dict) else None,
        "status_valid": isinstance(result, dict)
                        and result.get("status") in R.STATUS_VALUES,
        "contract_problems": [],
        "honest_fields_missing": [],
        "screening_issues": [],
        "evidence_issues": [],
        "synthesis_issues": [],
        "prisma_mismatch": [],
        "warnings": list(degs),
    }
    if isinstance(result, dict):
        report["contract_problems"] = R.validate_contract(result, contract)
        for field in ("sources", "uncertainty", "change_log", "status", "case_id"):
            if field not in result:
                report["honest_fields_missing"].append(field)
        report["screening_issues"] = R.audit_screening(result.get("screening_records") or [])
        report["evidence_issues"] = R.audit_evidence(result.get("evidence_map") or [])
        report["synthesis_issues"] = R.audit_synthesis(
            result.get("synthesis") or {}, result.get("evidence_map") or [])
        declared = result.get("prisma_flow") or {}
        recomputed = R.recompute_prisma(result.get("screening_records") or [])
        report["prisma_mismatch"] = [
            key for key in recomputed
            if isinstance(declared.get(key), (int, float))
            and declared[key] != recomputed[key]
        ]
    report["passed"] = (
        report["json_parse"] and report["case_id"] and report["status_valid"]
        and not report["contract_problems"] and not report["honest_fields_missing"]
    )
    return report


def build_parser():
    parser = argparse.ArgumentParser(
        description="Systematic review skill runtime helper (std lib only)")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (("discover", cmd_discover), ("schema", cmd_schema),
                       ("skeleton", cmd_skeleton), ("finalize", cmd_finalize),
                       ("selfcheck", cmd_selfcheck)):
        p = sub.add_parser(name, help=func.__doc__ or "")
        add_common(p)
        p.set_defaults(func=func)
    p = sub.add_parser("merge", help="deep-merge a payload JSON into result.json")
    add_common(p)
    p.add_argument("--payload", required=True, help="path to payload JSON")
    p.set_defaults(func=cmd_merge)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    summary = args.func(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
