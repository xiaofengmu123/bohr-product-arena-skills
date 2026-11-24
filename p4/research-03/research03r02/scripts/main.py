#!/usr/bin/env python3
"""CLI for the budgeted systematic-review Skill (standard library only).

Subcommands
-----------
  discover                 print the input inventory as JSON
  skeleton                 write the first valid partial result.json + run_state.json
  budget [tick]            show the time/request budget and phase verdict; "tick"
                           counts one model response into the request ledger
  commit                   checkpoint structured work into result.json
                           (--payload file.json  |  --field NAME --file F)
  finalize                 assemble result.json, review.md, screening_log.json,
                           evidence_map.json from /app/work
  selfcheck                validate result.json and print the check report

Common options (defaults match the runtime contract):
  --input INPUT_DIR   default /input
  --output OUTPUT_DIR default /app/output
  --work WORK_DIR     default /app/work

Run from the Skill root:
  python -B scripts/main.py skeleton
"""

import argparse
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as C


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


def load_result(args):
    result, errs, _ = C.load_json(out_path(args, "result.json"))
    state, serr, _ = C.load_json(work_path(args, "run_state.json"))
    contract = (state or {}).get("contract") or copy.deepcopy(C.DEFAULT_CONTRACT)
    if not isinstance(result, dict):
        result = C.new_skeleton((state or {}).get("case_id", ""), contract,
                                note="created on demand")
        result["status"] = C.STATUS_INSUFFICIENT
    return result, state, contract, errs, serr


def _checkpoint(args, phase):
    state, errs, _ = C.load_json(work_path(args, "run_state.json"))
    if isinstance(state, dict):
        state.setdefault("checkpoints", {})
        state["checkpoints"][phase] = C.iso_now()
        return C.write_json(work_path(args, "run_state.json"), state)
    return ["run_state.json missing; cannot record checkpoint %s" % phase]


# ----------------------------------------------------------------------------
# Subcommand implementations
# ----------------------------------------------------------------------------

def cmd_discover(args):
    print(json.dumps(C.discover_input(args.input), ensure_ascii=False, indent=2))


def cmd_skeleton(args):
    C.ensure_dir(args.output)
    C.ensure_dir(args.work)
    inv = C.discover_input(args.input)
    case_obj, errs, degs = None, [], []
    if inv.get("case_json"):
        case_obj, errs, degs = C.load_json(inv["case_json"])
    case_id = C.extract_case_id(case_obj) if isinstance(case_obj, dict) else ""
    contract = C.build_contract(case_obj if isinstance(case_obj, dict) else None)

    result_path = out_path(args, "result.json")
    summary = {"case_id": case_id, "contract_fields": sorted(contract.keys()),
               "wrote": False, "already_exists": False,
               "errors": list(errs), "degradations": list(degs)}

    if os.path.exists(result_path):
        summary["already_exists"] = True
        existing, e2, _ = C.load_json(result_path)
        if not isinstance(existing, dict):
            result = C.new_skeleton(case_id, contract,
                                    note="repaired unreadable result.json")
            C.write_json(result_path, result)
            summary["wrote"] = True
    else:
        note = "skeleton before systematic review begins"
        result = C.new_skeleton(case_id, contract, note=note)
        result["errors"] = list(errs)
        result["degradations"] = list(degs)
        if not inv["exists"] or inv["total_files"] == 0:
            result["status"] = C.STATUS_INSUFFICIENT
            result.setdefault("limitations", []).append(
                "no case.json/task.md/materials found under %s" % args.input)
        elif not inv.get("case_json"):
            result["status"] = C.STATUS_PARTIAL
            result.setdefault("limitations", []).append(
                "case.json missing; review_question must be taken from task.md")
        elif bool(inv.get("case_json")) and not isinstance(case_obj, dict):
            result["status"] = C.STATUS_INPUT_ERROR
            result.setdefault("limitations", []).append(
                "case.json present but unparseable; contract fell back to defaults")
        summary["wrote"] = not bool(C.write_json(result_path, result))
        summary["errors"].extend(errs)

    state = {
        "case_id": case_id, "contract": contract, "inventory": inv,
        "started_epoch": C.epoch_now(), "requests_used": 0,
        "checkpoints": {}, "phase_plan": C.PHASES,
        "input_dir": args.input, "output_dir": args.output, "work_dir": args.work,
    }
    summary["errors"].extend(C.write_json(work_path(args, "run_state.json"), state))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_budget(args):
    state, errs, _ = C.load_json(work_path(args, "run_state.json"))
    if not isinstance(state, dict):
        print(json.dumps({"verdict": "missing_state", "action":
                          "run_state.json missing; run skeleton first",
                          "errors": errs}, ensure_ascii=False, indent=2))
        return
    if getattr(args, "tick", False):
        state["requests_used"] = int(state.get("requests_used", 0) or 0) + 1
        C.write_json(work_path(args, "run_state.json"), state)
    report = C.budget_report(state)
    report["errors"] = errs
    print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_commit(args):
    result_path = out_path(args, "result.json")
    result, errs, _ = C.load_json(result_path)
    if not isinstance(result, dict):
        print(json.dumps({"ok": False, "applied": [],
                          "errors": ["result.json missing/unreadable; run skeleton first"] + errs},
                         ensure_ascii=False, indent=2))
        return
    report = {"ok": False, "applied": [], "errors": [], "degradations": []}

    payload_path = getattr(args, "payload", None)
    if payload_path:
        payload, perrs, pdegs = C.load_json(payload_path)
        if isinstance(payload, dict):
            result = C.deep_merge(result, payload)
            report["applied"].append("payload:%s" % os.path.basename(payload_path))
        else:
            report["errors"].append("payload %s is not a JSON object" % payload_path)
        report["errors"].extend(perrs)
        report["degradations"].extend(pdegs)

    field = getattr(args, "field", None)
    file_path = getattr(args, "file", None)
    if field and file_path:
        value, verr, vdegs = C.load_value(file_path)
        if verr:
            report["errors"].extend(verr)
        else:
            if isinstance(value, str):
                value = value.strip()
            result[field] = value
            report["applied"].append("field:%s" % field)
        report["degradations"].extend(vdegs)

    if report["applied"]:
        result.setdefault("change_log", []).append({
            "ts": C.iso_now(), "action": "commit",
            "detail": "; ".join(report["applied"]),
        })
        phase = getattr(args, "phase", None)
        if phase:
            report["errors"].extend(_checkpoint(args, phase))
        report["errors"].extend(C.write_json(result_path, result))
        report["ok"] = not report["errors"]
    else:
        report["errors"].append("nothing to commit: pass --payload or --field/--file")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_finalize(args):
    C.ensure_dir(args.output)
    state, _, _ = C.load_json(work_path(args, "run_state.json"))
    state = state if isinstance(state, dict) else {}
    case_id = state.get("case_id") or "unknown"
    contract = state.get("contract") or copy.deepcopy(C.DEFAULT_CONTRACT)

    result, errs, _ = C.load_json(out_path(args, "result.json"))
    if not isinstance(result, dict):
        result = C.new_skeleton(case_id, contract, note="created at finalize")

    # Protocol fields.
    proto, perrs, _ = C.load_json_optional(work_path(args, "protocol.json"))
    if isinstance(proto, dict):
        for key in ("review_question", "subquestions", "search_strategy",
                    "inclusion_criteria", "exclusion_criteria"):
            value = proto.get(key)
            if value not in (None, "", []):
                result[key] = copy.deepcopy(value)
        if proto.get("uncertainty"):
            result["uncertainty"] = list(result.get("uncertainty") or []) + \
                                    list(proto["uncertainty"])
    errs.extend(perrs)

    # Screening, quality, evidence records (JSONL in /app/work).
    screening, serr = C.read_jsonl_optional(work_path(args, "screening.jsonl"))
    if screening:
        result["screening_records"] = screening
    errs.extend(serr)

    appraisal, aerr = C.read_jsonl_optional(work_path(args, "quality_appraisal.jsonl"))
    if appraisal:
        result["quality_appraisal"] = appraisal
    errs.extend(aerr)

    evidence, eerr = C.read_jsonl_optional(work_path(args, "evidence_cards.jsonl"))
    if evidence:
        result["evidence_map"] = evidence
    errs.extend(eerr)

    # Synthesis object.
    synthesis, serr2, _ = C.load_json_optional(work_path(args, "synthesis.json"))
    if isinstance(synthesis, dict):
        result["synthesis"] = C.deep_merge(result.get("synthesis") or {}, synthesis)
    errs.extend(serr2)

    # PRISMA is always recomputed from screening records, never trusted as typed.
    result["prisma_flow"] = C.compute_prisma(result.get("screening_records") or [])

    result["sources"] = C.collect_sources(result)

    started = state.get("started_epoch")
    if isinstance(started, (int, float)):
        result["runtime_seconds"] = int(C.epoch_now() - started)

    result["status"] = C.derive_status(result, case_id)
    result["case_id"] = result.get("case_id") or case_id

    artifacts = ["result.json", "review.md", "screening_log.json", "evidence_map.json"]
    result["artifacts"] = list(artifacts)

    errs.extend(C.write_json(out_path(args, "result.json"), result))
    errs.extend(C.write_text(out_path(args, "review.md"), C.render_review_md(result)))
    errs.extend(C.write_json(out_path(args, "screening_log.json"), {
        "case_id": result.get("case_id"), "generated_at": C.iso_now(),
        "prisma_flow": result.get("prisma_flow"),
        "records": result.get("screening_records") or [],
    }))
    errs.extend(C.write_json(out_path(args, "evidence_map.json"), {
        "case_id": result.get("case_id"), "generated_at": C.iso_now(),
        "items": result.get("evidence_map") or [],
    }))
    _checkpoint(args, "finalize")

    print(json.dumps({"ok": not errs, "status": result["status"],
                      "case_id": result.get("case_id"),
                      "artifacts": artifacts, "errors": errs},
                     ensure_ascii=False, indent=2))


def cmd_selfcheck(args):
    result, errs, _ = C.load_json(out_path(args, "result.json"))
    state, serr, _ = C.load_json(work_path(args, "run_state.json"))
    contract = (state or {}).get("contract") or copy.deepcopy(C.DEFAULT_CONTRACT)
    if not isinstance(result, dict):
        report = {"ok": False, "errors": ["result.json missing/unreadable"] + errs,
                  "warnings": [], "error_count": 1, "warning_count": 0}
    else:
        report = C.validate_result(result, contract)
    report["case_id"] = (state or {}).get("case_id") or ""
    report["state_errors"] = serr
    print(json.dumps(report, ensure_ascii=False, indent=2))


# ----------------------------------------------------------------------------
# Argument parser
# ----------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(prog="main.py",
                                     description="budgeted systematic-review runtime helper")
    sub = parser.add_subparsers(dest="command")

    p_discover = sub.add_parser("discover", help="print input inventory")
    add_common(p_discover)

    p_skeleton = sub.add_parser("skeleton", help="write first valid partial result")
    add_common(p_skeleton)

    p_budget = sub.add_parser("budget", help="show time/request budget verdict")
    p_budget.add_argument("tick", nargs="?", choices=["tick"],
                          help="count one model response")
    add_common(p_budget)

    p_commit = sub.add_parser("commit", help="checkpoint work into result.json")
    p_commit.add_argument("--payload", help="JSON dict to deep-merge")
    p_commit.add_argument("--field", help="top-level field name to set")
    p_commit.add_argument("--file", help="file holding the value (json/jsonl/txt)")
    p_commit.add_argument("--phase", help="phase id to checkpoint, e.g. P2")
    add_common(p_commit)

    p_finalize = sub.add_parser("finalize", help="assemble final deliverables")
    add_common(p_finalize)

    p_selfcheck = sub.add_parser("selfcheck", help="validate result.json")
    add_common(p_selfcheck)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    handlers = {
        "discover": cmd_discover,
        "skeleton": cmd_skeleton,
        "budget": cmd_budget,
        "commit": cmd_commit,
        "finalize": cmd_finalize,
        "selfcheck": cmd_selfcheck,
    }
    handlers[args.command](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
