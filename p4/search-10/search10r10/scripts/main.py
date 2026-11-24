#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — cross-lingual literature search skill CLI.

Subcommands
  init      discover /input, extract case_id + output_contract, initialize the
            append-only evidence log and atomically write a legal partial
            result.json. Run this FIRST (within 3 minutes) so a valid skeleton
            is on disk before any analysis.
  record    append / upsert evidence records (JSON array, JSONL file, or
            --inline JSON) and deterministically re-render result.json.
  validate  check result.json (plus contract fields in state.json) and report
            issues; exit 0 normally, exit 1 only with --strict when issues exist.
  status    print a progress summary: record counts by kind and current status.

Defaults: --input /input  --output /app/output  --work /app/work
Standard library only.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as c  # noqa: E402


def _result_path(args):
    return os.path.join(args.output, "result.json")


def cmd_init(args):
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    found, case, task_text, case_err = c.load_inputs(args.input)
    case_id = c.find_case_id(case, task_text)
    contract = c.find_contract(case) or c.find_contract_from_text(task_text)
    contract_fields = c.normalize_contract(contract) if contract else []
    state = {
        "case_id": case_id,
        "contract": contract,
        "contract_fields": contract_fields,
        "inputs": found,
        "case_error": case_err,
        "started_at": __import__("time").time(),
        "version": c.SKILL_VERSION,
    }
    c.save_state(args.work, state)
    store = c.EvidenceStore(args.work)
    if not os.path.exists(store.path):
        store._write([])
    records = store.load()
    result = c.render(state, records)
    c.atomic_write_json(_result_path(args), result)
    print("init ok")
    print("  case_id        : %s" % case_id)
    print("  status         : %s" % result["status"])
    print("  contract fields: %d" % len(contract_fields))
    print("  inputs         : case=%s task=%s materials=%d" % (
        bool(found["case"]), bool(found["task"]), len(found["materials"])))
    print("  result.json    : %s" % _result_path(args))
    if case_err:
        print("  case_error     : %s" % case_err)
    return 0


def _parse_records(text):
    text = (text or "").strip()
    if not text:
        return []
    try:
        obj = json.loads(text)
    except Exception:  # noqa: BLE001
        recs = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
        return recs
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        return [obj]
    return []


def cmd_record(args):
    state = c.load_state(args.work)
    if state is None:
        print("error: no state.json in %s; run init first" % args.work, file=sys.stderr)
        return 2
    if args.patch:
        text, _trunc = c.read_text(args.patch, max_bytes=10 * 1024 * 1024)
        records = _parse_records(text)
    else:
        records = _parse_records(args.inline)
    for rec in records:
        c.ensure_id(rec)
    store = c.EvidenceStore(args.work)
    store.append(records)
    if args.status:
        state["status_override"] = args.status
        c.save_state(args.work, state)
    total = store.load()
    result = c.render(state, total)
    c.atomic_write_json(_result_path(args), result)
    print("record ok")
    print("  appended/upserted: %d" % len(records))
    print("  total records    : %d" % len(total))
    print("  status           : %s" % result["status"])
    return 0


def cmd_validate(args):
    result, err = c.safe_json_load(_result_path(args))
    if result is None:
        print("result.json not readable: %s" % err)
        return 1 if args.strict else 0
    issues = c.validate(result)
    state = c.load_state(args.work)
    if state and state.get("contract_fields"):
        for f in state["contract_fields"]:
            if f["name"] not in result:
                issues.append("missing contract field: %s" % f["name"])
    if issues:
        print("issues: %d" % len(issues))
        for issue in issues:
            print("  - " + issue)
    else:
        print("validate ok: no issues")
    return 1 if (args.strict and issues) else 0


def cmd_status(args):
    state = c.load_state(args.work)
    store = c.EvidenceStore(args.work)
    records = store.load()
    result, _err = c.safe_json_load(_result_path(args))
    counts = {}
    for rec in records:
        kind = rec.get("kind", "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    print("case_id: %s" % (state.get("case_id") if state else "unknown"))
    print("status : %s" % (result.get("status") if isinstance(result, dict) else "no result.json"))
    print("records: %d" % len(records))
    for kind in sorted(counts):
        print("  %-18s %d" % (kind, counts[kind]))
    return 0


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=c.DEFAULT_INPUT)
    common.add_argument("--output", default=c.DEFAULT_OUTPUT)
    common.add_argument("--work", default=c.DEFAULT_WORK)

    parser = argparse.ArgumentParser(
        prog="main.py",
        description="cross-lingual literature search skill orchestrator",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="discover input, write state + legal partial skeleton",
                   parents=[common])

    p_rec = sub.add_parser("record", help="append evidence records and re-render result.json",
                           parents=[common])
    p_rec.add_argument("--patch", help="JSON array / JSONL file of evidence records")
    p_rec.add_argument("--inline", help="inline JSON record or array")
    p_rec.add_argument("--status", choices=list(c.STATUSES),
                       help="optional explicit status override (else derived)")

    p_val = sub.add_parser("validate", help="check result.json + contract fields",
                           parents=[common])
    p_val.add_argument("--strict", action="store_true",
                       help="exit 1 when issues are found")

    sub.add_parser("status", help="print progress summary", parents=[common])
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "init":
        return cmd_init(args)
    if args.command == "record":
        return cmd_record(args)
    if args.command == "validate":
        return cmd_validate(args)
    if args.command == "status":
        return cmd_status(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
