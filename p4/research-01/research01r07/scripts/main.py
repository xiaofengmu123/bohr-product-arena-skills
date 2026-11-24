#!/usr/bin/env python3
"""CLI dispatcher for the deep-research skill (thin; logic lives in resultlib).

Subcommands:
  discover                 print the input inventory as JSON
  skeleton                 write an early valid result.json + run_state.json
  merge --payload FILE     deep-merge a payload into result.json (idempotent)
  finalize                 assemble result.json, report.md, evidence_map.json
  selfcheck                validate result.json and report six-point coverage

Common options (defaults match the runtime contract):
  --input  DIR   default /input
  --output DIR   default /app/output
  --work   DIR   default /app/work
"""

import argparse
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


def cmd_discover(args):
    inventory = R.discover_input(args.input)
    print(json.dumps(inventory, ensure_ascii=False, indent=2))


def _load_run_state(args):
    state, _, _ = R.load_json(work_path(args, "run_state.json"))
    return state if isinstance(state, dict) else {}


def cmd_skeleton(args):
    """Write an early partial result skeleton. Never raises, never exits nonzero."""
    R.ensure_dir(args.output)
    R.ensure_dir(args.work)
    inventory = R.discover_input(args.input)
    case_obj, errs, degs = (None, [], [])
    if inventory.get("case_json"):
        case_obj, errs, degs = R.load_json(inventory["case_json"])
    case_id = R.extract_case_id(case_obj) if isinstance(case_obj, dict) else ""
    contract = R.build_contract(case_obj if isinstance(case_obj, dict) else None)

    result_path = out_path(args, "result.json")
    summary = {
        "case_id": case_id,
        "contract_fields": sorted(contract.keys()),
        "wrote": False,
        "already_exists": False,
        "errors": [],
        "degradations": [],
    }

    if os.path.exists(result_path):
        summary["already_exists"] = True
        existing, e2, _ = R.load_json(result_path)
        if not isinstance(existing, dict):
            result = R.new_skeleton(case_id, contract, note="repaired unreadable result.json")
            summary["errors"].extend(R.write_json(result_path, result))
            summary["wrote"] = not summary["errors"]
    else:
        note = "skeleton before research begins"
        if not inventory["exists"] or inventory["total_files"] == 0:
            note = "empty input directory; no materials found"
        result = R.new_skeleton(case_id, contract, note=note)
        result["errors"] = list(errs)
        result["degradations"] = list(degs)
        if not inventory["exists"] or inventory["total_files"] == 0 \
                or not inventory.get("case_json"):
            result["status"] = R.STATUS_INSUFFICIENT
            result.setdefault("limitations", []).append(
                "no case.json/task.md/materials found under %s" % args.input)
        elif bool(inventory.get("case_json")) and not isinstance(case_obj, dict):
            result["status"] = R.STATUS_INPUT_ERROR
            result.setdefault("limitations", []).append(
                "case.json present but unparseable; contract fields fall back to defaults")
        summary["errors"].extend(R.write_json(result_path, result))
        summary["wrote"] = not summary["errors"]
        summary["errors"].extend(errs)
        summary["degradations"].extend(degs)

    state = {
        "case_id": case_id,
        "contract": contract,
        "inventory": inventory,
        "started_at": R.epoch_now(),
        "input_dir": args.input,
        "output_dir": args.output,
        "work_dir": args.work,
    }
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
        state = _load_run_state(args)
        contract = state.get("contract") or {}
        case_id = state.get("case_id") or R.extract_case_id(payload)
        result = R.new_skeleton(case_id, contract, note="created during merge")
    before = json.dumps(result, ensure_ascii=False, sort_keys=True)
    R.deep_merge(result, payload)
    R.append_change_log(result, "merge",
                        "merged payload %s" % os.path.basename(args.payload))
    after = json.dumps(result, ensure_ascii=False, sort_keys=True)
    errs.extend(R.write_json(result_path, result))
    print(json.dumps({"ok": not errs, "changed": before != after,
                      "errors": errs, "degradations": degs + d2},
                     ensure_ascii=False, indent=2))


def _read_work_notes(args):
    texts = {}
    for name in ("decomposition.md", "search_plan.md", "synthesis.md"):
        text, _, _ = R.read_text(work_path(args, name))
        texts[name] = text or ""
    cards, card_errs = [], []
    card_path = work_path(args, "evidence_cards.jsonl")
    if os.path.exists(card_path):
        text, errs, _ = R.read_text(card_path)
        card_errs.extend(errs)
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                cards.append(json.loads(line))
            except ValueError as exc:
                card_errs.append({"op": "jsonl", "path": "evidence_cards.jsonl",
                                  "error": str(exc)})
    return texts, cards, card_errs


def cmd_finalize(args):
    state = _load_run_state(args)
    contract = state.get("contract") if isinstance(state.get("contract"), dict) else {}
    case_id = state.get("case_id") or ""

    result_path = out_path(args, "result.json")
    result, errs, degs = R.load_json(result_path)
    if not isinstance(result, dict):
        result = R.new_skeleton(case_id, contract, note="created during finalize")

    # Merge evidence cards (jsonl) into the matrix if not already present.
    texts, cards, card_errs = _read_work_notes(args)
    existing_ids = {row.get("id") for row in result.get("evidence_matrix", [])
                    if isinstance(row, dict)}
    added = 0
    for card in cards:
        if isinstance(card, dict) and card.get("id") and card.get("id") not in existing_ids:
            result.setdefault("evidence_matrix", []).append(card)
            existing_ids.add(card.get("id"))
            added += 1

    R.ensure_contract_fields(result, contract)
    started = state.get("started_at") if isinstance(state, dict) else None
    if isinstance(started, (int, float)):
        result["runtime_seconds"] = round(R.epoch_now() - started, 2)
    R.append_change_log(result, "finalize",
                        "assembled report.md/evidence_map.json")

    errs.extend(R.write_json(result_path, result))
    errs.extend(R.write_text(out_path(args, "report.md"), R.render_report(result)))
    errs.extend(R.write_json(out_path(args, "evidence_map.json"),
                             R.render_evidence_map(result)))
    for name, text in texts.items():
        if text:
            errs.extend(R.write_text(out_path(args, name), text))
    errs.extend(card_errs)

    print(json.dumps({
        "ok": not errs,
        "case_id": result.get("case_id", ""),
        "status": result.get("status", ""),
        "evidence_count": len(result.get("evidence_matrix") or []),
        "conclusion_count": len((result.get("research_product") or {}).get("conclusions") or []),
        "cards_added": added,
        "errors": errs,
        "degradations": degs,
    }, ensure_ascii=False, indent=2))


def cmd_selfcheck(args):
    result, errs, degs = R.load_json(out_path(args, "result.json"))
    if not isinstance(result, dict):
        result = {}
    problems = R.validate(result)
    six, details = R.six_point_check(result, args.work)
    print(json.dumps({
        "valid": not problems and not errs,
        "problems": problems,
        "load_errors": errs,
        "six_point": six,
        "six_point_all": all(six.values()),
        "details": details,
    }, ensure_ascii=False, indent=2))


def build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Deep-research skill runtime helper (standard library only).")
    sub = parser.add_subparsers(dest="command")

    p_discover = sub.add_parser("discover", help="print input inventory as JSON")
    add_common(p_discover)

    p_skeleton = sub.add_parser("skeleton", help="write early partial result skeleton")
    add_common(p_skeleton)

    p_merge = sub.add_parser("merge", help="merge a payload JSON into result.json")
    add_common(p_merge)
    p_merge.add_argument("--payload", required=True, help="payload JSON file path")

    p_finalize = sub.add_parser("finalize", help="assemble final artifacts")
    add_common(p_finalize)

    p_selfcheck = sub.add_parser("selfcheck", help="validate result.json + six points")
    add_common(p_selfcheck)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    # Every command handles its own data errors; only unexpected exceptions
    # propagate as a non-zero exit to signal a genuine bug.
    try:
        {"discover": cmd_discover,
         "skeleton": cmd_skeleton,
         "merge": cmd_merge,
         "finalize": cmd_finalize,
         "selfcheck": cmd_selfcheck}[args.command](args)
        return 0
    except Exception as exc:  # last-resort guard: keep a minimal legal artifact
        try:
            if args.command in ("skeleton", "finalize", "merge"):
                R.ensure_dir(args.output)
                R.write_json(out_path(args, "result.json"), {
                    "case_id": "", "status": R.STATUS_INPUT_ERROR,
                    "research_product": None, "evidence_matrix": [],
                    "uncertainty": {"conflicts": [], "biases": [], "gaps": [],
                                    "undetermined": []},
                    "sources": [], "change_log": [], "limitations": [],
                    "errors": [{"op": "cli", "error": str(exc)}],
                    "degradations": [], "pending_human": [], "runtime_seconds": 0,
                })
        except Exception:
            pass
        print(json.dumps({"ok": False, "fatal": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
