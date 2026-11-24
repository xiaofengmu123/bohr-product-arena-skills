#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — CLI entry point for the deep-research skill runtime helper.

Subcommands:
  discover    print the input inventory as JSON
  skeleton    write an early legal result.json + /app/work/run_state.json
  merge       deep-merge a payload JSON into result.json (incremental update)
  finalize    assemble result.json, report.md, evidence_map.json and notes
  selfcheck   validate result.json and report coverage checks

Common options (defaults match the runtime contract):
  --input  INPUT_DIR    default /input
  --output OUTPUT_DIR   default /app/output
  --work   WORK_DIR     default /app/work
"""

import argparse
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as C  # noqa: E402


def add_common(parser):
    parser.add_argument("--input", default=os.environ.get("INPUT_DIR", C.DEFAULT_INPUT_DIR),
                        help="input directory (default /input)")
    parser.add_argument("--output", default=os.environ.get("OUTPUT_DIR", C.DEFAULT_OUTPUT_DIR),
                        help="output directory (default /app/output)")
    parser.add_argument("--work", default=os.environ.get("WORK_DIR", C.DEFAULT_WORK_DIR),
                        help="work directory (default /app/work)")


def out_path(args, name):
    return os.path.join(args.output, name)


def work_path(args, name):
    return os.path.join(args.work, name)


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_discover(args):
    emit(C.discover_input(args.input))


def cmd_skeleton(args):
    C.ensure_dir(args.output)
    C.ensure_dir(args.work)
    inv = C.discover_input(args.input)
    case_obj, errs, degs = None, [], []
    if inv.get("case_json"):
        case_obj, errs, degs = C.load_json(inv["case_json"])
    case_id = C.extract_case_id(case_obj) if isinstance(case_obj, dict) else ""
    contract = C.extract_contract(case_obj) if isinstance(case_obj, dict) else {}

    result_path = out_path(args, "result.json")
    summary = {"case_id": case_id, "contract_fields": sorted(contract.keys()),
               "wrote": False, "already_exists": False,
               "errors": list(errs), "degradations": list(degs)}

    if os.path.exists(result_path):
        existing, e2, _d2 = C.load_json(result_path)
        summary["already_exists"] = True
        summary["errors"].extend(e2)
        if not isinstance(existing, dict):
            result = C.new_skeleton(case_id, contract,
                                    note="repaired unreadable result.json")
            summary["errors"].extend(C.write_json(result_path, result))
            summary["wrote"] = True
    else:
        note = "skeleton before research begins"
        if not inv["exists"] or inv["total_files"] == 0:
            note = "empty input directory; no materials found"
        result = C.new_skeleton(case_id, contract, note=note)
        result["errors"] = list(errs)
        result["degradations"] = list(degs)
        if not inv["exists"] or inv["total_files"] == 0 or not inv.get("case_json"):
            result["status"] = C.STATUS_INSUFFICIENT
            result.setdefault("limitations", []).append(
                "no case.json/task.md/materials found under %s" % args.input)
        elif not isinstance(case_obj, dict):
            result["status"] = C.STATUS_INPUT_ERROR
            result.setdefault("limitations", []).append(
                "case.json present but unparseable; contract defaults applied")
        write_errs = C.write_json(result_path, result)
        summary["wrote"] = not bool(write_errs)
        summary["errors"].extend(write_errs)

    state = {"case_id": case_id, "contract": contract, "inventory": inv,
             "started_at": C.epoch_now()}
    summary["errors"].extend(C.write_json(work_path(args, "run_state.json"), state))
    emit(summary)


def cmd_merge(args):
    payload, perrs, pdegs = C.load_json(args.payload)
    if not isinstance(payload, dict):
        emit({"ok": False, "errors": perrs or ["payload is not a JSON object"],
              "degradations": pdegs})
        return
    result_path = out_path(args, "result.json")
    result, errs, degs = C.load_json(result_path)
    if not isinstance(result, dict):
        state = C.load_state(args.work) or {}
        contract = state.get("contract") or {}
        case_id = state.get("case_id") or C.extract_case_id(payload)
        result = C.new_skeleton(case_id, contract, note="created during merge")
    C.deep_merge(result, payload)
    result.setdefault("change_log", []).append({
        "ts": C.iso_now(), "action": "merge",
        "detail": "merged %s" % os.path.basename(args.payload),
    })
    errs.extend(C.write_json(result_path, result))
    emit({"ok": not errs, "errors": errs, "degradations": degs + pdegs})


def cmd_finalize(args):
    result_path = out_path(args, "result.json")
    result, errs, degs = C.load_json(result_path)
    state = C.load_state(args.work) or {}
    if not isinstance(result, dict):
        result = C.new_skeleton(state.get("case_id", ""),
                                state.get("contract") or {},
                                note="created during finalize")

    started = state.get("started_at") or C.epoch_now()
    result["runtime_seconds"] = round(C.epoch_now() - started, 1)

    cards, card_errs = C.read_jsonl(work_path(args, "evidence_cards.jsonl"))
    errs.extend(card_errs)
    if cards:
        result["evidence_matrix"] = C.merge_cards(
            result.get("evidence_matrix") or [], cards)

    slog, slog_errs = C.read_jsonl(work_path(args, "search_log.jsonl"))
    errs.extend(slog_errs)
    if slog:
        result.setdefault("search_log", []).extend(
            C.merge_search_log(result.get("search_log") or [], slog))

    if not isinstance(result.get("uncertainty"), dict):
        result["uncertainty"] = {}
    for key, value in C.STANDARD_FIELDS["uncertainty"].items():
        result["uncertainty"].setdefault(key, copy.deepcopy(value))

    errs.extend(C.write_json(result_path, result))

    notes = {}
    for name in ("decomposition.md", "search_plan.md", "synthesis.md"):
        text, _enc, _degs = C.read_text(work_path(args, name))
        notes[name] = text or ""

    errs.extend(C.write_text(out_path(args, "report.md"),
                             C.build_report_md(result, notes)))
    errs.extend(C.write_json(out_path(args, "evidence_map.json"),
                             C.build_evidence_map(result)))
    for name, text in notes.items():
        if text.strip():
            errs.extend(C.write_text(out_path(args, name), text))

    emit({"ok": not errs, "errors": errs, "degradations": degs,
          "evidence_count": len(result.get("evidence_matrix") or []),
          "conclusion_count": len((result.get("research_product") or {}).get("conclusions") or [])})


def cmd_selfcheck(args):
    result, errs, degs = C.load_json(out_path(args, "result.json"))
    checks = C.selfcheck(result, args.input, args.work)
    emit({"ok": C.selfcheck_summary(checks)["ok"],
          "summary": C.selfcheck_summary(checks),
          "checks": checks, "errors": errs, "degradations": degs})


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Deep-research skill runtime helper (standard library only)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("discover", help="print input inventory")
    add_common(p)
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("skeleton", help="write early result.json skeleton")
    add_common(p)
    p.set_defaults(func=cmd_skeleton)

    p = sub.add_parser("merge", help="deep-merge a payload JSON into result.json")
    add_common(p)
    p.add_argument("--payload", required=True, help="path to payload JSON")
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser("finalize", help="assemble final result + report + evidence map")
    add_common(p)
    p.set_defaults(func=cmd_finalize)

    p = sub.add_parser("selfcheck", help="validate result.json coverage")
    add_common(p)
    p.set_defaults(func=cmd_selfcheck)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except Exception as exc:  # never crash the harness
        emit({"ok": False, "errors": ["unhandled %s: %s" % (type(exc).__name__, exc)]})
        sys.exit(0)


if __name__ == "__main__":
    main()
