# -*- coding: utf-8 -*-
"""Entry point for the literature-tracking Skill.

Usage (paths are relative to the skill root, e.g. /skills/weekly-skill):

    python3 scripts/main.py scaffold  --input /input --output /app/output --work /app/work
    python3 scripts/main.py run       --input /input --output /app/output --work /app/work
    python3 scripts/main.py finalize  --input /input --output /app/output --work /app/work \\
                                      --findings /app/work/findings.json
    python3 scripts/main.py dedup     --records /app/work/records.json --out /app/work/dedup.json
    python3 scripts/main.py normalize --record '{"doi":"10.1000/XYZ","title":"A Paper"}'
    python3 scripts/main.py phase     --work /app/work --phase 1 --event start

`scaffold` must run in the first minutes: it writes a valid partial result.json
plus a phase-budget ledger before any analysis. `run` is the no-op fallback
(scaffold + deterministic material inventory + finalize with empty findings).
`finalize` merges the agent's findings and enriches them with dedup / increment
classification, then writes result.json and report.md. `phase` records budget
events for the 900 s / 64 request schedule. The scripts never fabricate
literature content.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline  # noqa: E402

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"


def cmd_scaffold(args):
    case_id, contract, data, errors = pipeline.extract_case(args.input)
    status = pipeline._initial_status(data, errors)
    skeleton = pipeline.make_skeleton(case_id, contract, status)
    for e in errors:
        skeleton["errors"].append(e)
    out_ok = pipeline.write_json(str(Path(args.output) / "result.json"), skeleton)
    work_ok = pipeline.write_json(str(Path(args.work) / "skeleton.json"), skeleton)
    ledger_ok = pipeline.write_json(str(Path(args.work) / "ledger.json"), pipeline.build_ledger())
    if not (out_ok and work_ok and ledger_ok):
        print("warning: some writes failed; see stderr", file=sys.stderr)
    print(json.dumps({"case_id": case_id, "status": status,
                      "contract_fields": contract,
                      "materials": len(data["materials"]),
                      "errors": errors}, ensure_ascii=False))
    return 0


def cmd_run(args):
    cmd_scaffold(args)
    # deterministic inventory: read text materials only, never guess PDF content
    case_id, contract, data, errors = pipeline.extract_case(args.input)
    inventory = []
    for m in pipeline.text_materials([Path(p) for p in data["materials"]]):
        try:
            txt = pipeline.read_text(m)
            inventory.append({"path": m, "chars": len(txt)})
        except Exception as exc:
            errors.append("read failed: %s: %s" % (m, exc))
    findings = {
        "search_results": [],
        "ranking": [],
        "evidence": [],
        "status_notes": {"empty": None, "conflicts": [], "permission_errors": []},
        "sources": inventory,
        "errors": errors,
    }
    fpath = str(Path(args.work) / "findings.json")
    pipeline.write_json(fpath, findings)
    pipeline.finalize(args.input, args.output, args.work, fpath)
    print(json.dumps({"case_id": case_id, "status": "see result.json",
                      "inventory_items": len(inventory)}, ensure_ascii=False))
    return 0


def cmd_finalize(args):
    result = pipeline.finalize(args.input, args.output, args.work, args.findings,
                               self_check=not args.no_selfcheck)
    print(json.dumps({"case_id": result.get("case_id"),
                      "status": result.get("status")}, ensure_ascii=False))
    return 0


def cmd_dedup(args):
    records, errs = pipeline.load_json(args.records)
    if errs:
        print(json.dumps({"errors": errs}, ensure_ascii=False), file=sys.stderr)
        return 2
    if not isinstance(records, list):
        records = []
    out = pipeline.dedup_records(records)
    pipeline.write_json(args.out, out)
    print(json.dumps({"groups": out["groups"],
                      "dedup_key_rule": out["dedup_key_rule"]}, ensure_ascii=False))
    return 0


def cmd_normalize(args):
    try:
        rec = json.loads(args.record)
    except ValueError as exc:
        print(json.dumps({"error": "bad JSON record: %s" % exc}, ensure_ascii=False))
        return 2
    norm = pipeline.normalize_record(rec)
    print(json.dumps(norm, ensure_ascii=False, indent=2))
    return 0


def cmd_phase(args):
    ledger = pipeline.record_phase(args.work, args.phase, args.event, args.requests)
    summary = pipeline.summarize_budget(ledger)
    print(json.dumps({"phase": args.phase, "event": args.event,
                      "summary": summary}, ensure_ascii=False))
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="main.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("scaffold", help="write partial skeleton + budget ledger")
    _add_paths(sp)
    sp.set_defaults(func=cmd_scaffold)

    sp = sub.add_parser("run", help="scaffold + inventory + finalize (empty findings)")
    _add_paths(sp)
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("finalize", help="merge findings, write result.json + report.md")
    _add_paths(sp)
    sp.add_argument("--findings", default=None, help="path to findings.json")
    sp.add_argument("--no-selfcheck", action="store_true", help="skip self_check_passed log")
    sp.set_defaults(func=cmd_finalize)

    sp = sub.add_parser("dedup", help="dedup/version-classify a records list")
    sp.add_argument("--records", required=True)
    sp.add_argument("--out", required=True)
    sp.set_defaults(func=cmd_dedup)

    sp = sub.add_parser("normalize", help="normalize one record and show its canonical key")
    sp.add_argument("--record", required=True)
    sp.set_defaults(func=cmd_normalize)

    sp = sub.add_parser("phase", help="record a phase budget event (start/end)")
    sp.add_argument("--work", default=DEFAULT_WORK)
    sp.add_argument("--phase", required=True, type=int)
    sp.add_argument("--event", required=True, choices=["start", "end", "note"])
    sp.add_argument("--requests", type=int, default=None, help="requests used in this phase")
    sp.set_defaults(func=cmd_phase)

    return p


def _add_paths(sp):
    sp.add_argument("--input", default=DEFAULT_INPUT)
    sp.add_argument("--output", default=DEFAULT_OUTPUT)
    sp.add_argument("--work", default=DEFAULT_WORK)


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # never crash the harness on a script bug
        print(json.dumps({"fatal_error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
