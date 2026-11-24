#!/usr/bin/env python3
"""CLI entry point for scholar-bilingual-translator.

Subcommands: discover, init, add, add-list, add-evidence, check, finalize.
All subcommands use relative-path-friendly defaults and exit 0 on success.
"""
import argparse
import json
import sys

import core_impl as core


def _parse_value(text):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text


def _store_result(store, data, err, action):
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "action": action, "status": data.get("status")}, ensure_ascii=False))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="scripts/main.py", description="scholar-bilingual-translator runtime")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("discover", help="scan input dir and write case_brief.json")
    p.add_argument("--input", default="/input")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--out", default=None)

    p = sub.add_parser("init", help="write result.json skeleton")
    p.add_argument("--case", default="/input/case.json")
    p.add_argument("--out", default="/app/output/result.json")

    p = sub.add_parser("add", help="set a dotted field")
    p.add_argument("--result", default="/app/output/result.json")
    p.add_argument("key")
    p.add_argument("value")

    p = sub.add_parser("add-list", help="append an item to a list field")
    p.add_argument("--result", default="/app/output/result.json")
    p.add_argument("key")
    p.add_argument("value")

    p = sub.add_parser("add-evidence", help="append a validated evidence item")
    p.add_argument("--result", default="/app/output/result.json")
    p.add_argument("kind", choices=sorted(core.EVIDENCE_KINDS))
    p.add_argument("value")

    p = sub.add_parser("check", help="run consistency checks")
    p.add_argument("--result", default="/app/output/result.json")
    p.add_argument("--glossary", default=None)
    p.add_argument("--out", default="/app/work/consistency_report.json")

    p = sub.add_parser("finalize", help="set final status with completeness gates")
    p.add_argument("--result", default="/app/output/result.json")
    p.add_argument("--status", required=True, choices=["success", "partial", "failed"])

    args = ap.parse_args(argv)

    if args.cmd == "discover":
        brief = core.discover_input(args.input)
        out = args.out or f"{args.work}/case_brief.json"
        core.atomic_write_json(out, brief)
        print(json.dumps(brief, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "init":
        data, err = core.load_json_safe(args.case)
        case_id, contract = None, None
        if err:
            print(f"warning: cannot read case ({err}); using UNKNOWN", file=sys.stderr)
        else:
            case_id = data.get("case_id") or data.get("id") or data.get("caseId")
            oc = data.get("output_contract")
            if isinstance(oc, list):
                contract = [str(x) for x in oc]
            elif isinstance(oc, dict):
                contract = [str(k) for k in oc.keys()]
        store = core.ResultStore(args.out)
        store.init(case_id or "UNKNOWN", contract)
        print(json.dumps({"ok": True, "case_id": case_id or "UNKNOWN", "out": args.out}, ensure_ascii=False))
        return 0

    if args.cmd == "add":
        store = core.ResultStore(args.result)
        data, err = store.set(args.key, _parse_value(args.value))
        return _store_result(store, data, err, "add")

    if args.cmd == "add-list":
        store = core.ResultStore(args.result)
        data, err = store.add_list(args.key, _parse_value(args.value))
        return _store_result(store, data, err, "add-list")

    if args.cmd == "add-evidence":
        store = core.ResultStore(args.result)
        data, err = store.add_evidence(args.kind, _parse_value(args.value))
        return _store_result(store, data, err, "add-evidence")

    if args.cmd == "check":
        report, err = core.check_consistency(args.result, args.glossary, args.out)
        if err:
            print(f"error: {err}", file=sys.stderr)
            return 1
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "finalize":
        store = core.ResultStore(args.result)
        data, err = store.finalize(args.status)
        if err:
            print(f"error: {err}", file=sys.stderr)
            return 1
        print(json.dumps({"ok": True, "status": data.get("status")}, ensure_ascii=False))
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
