#!/usr/bin/env python3
"""Entry point for the fact-traceable academic-writing skill.

Subcommands (stdlib-only, idempotent, exit 0 on all expected conditions):
  init      Discover input, write result.json skeleton + structured intermediate
            representations (contract_meta / plan / material_index / figure_model).
  check     Cross-check [S:]/[C:] markers and run consistency checks on the draft.
  finalize  Timestamp, set an honest status, validate result.json.

Usage:
  python3 scripts/main.py init     --input /input --output /app/output --work /app/work
  python3 scripts/main.py check    --output /app/output --work /app/work
  python3 scripts/main.py finalize --output /app/output --work /app/work [--status complete|partial]

The agent orchestrates the three calls around its own reading/writing steps;
this entry point only persists state and runs deterministic checks.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl as ci  # noqa: E402


def _print(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def cmd_init(args):
    summary, err = ci.build_skeleton(args.input, args.output, args.work)
    summary["error"] = err
    _print(summary)
    return 0


def cmd_check(args):
    report, err = ci.build_evidence_map(
        os.path.join(args.output, "manuscript.md"),
        os.path.join(args.work, "material_index.json"),
        args.work,
    )
    if err:
        _print({"ok": False, "stage": "evidence_map", "error": err})
        return 1
    creport, cerr = ci.run_consistency_check(
        os.path.join(args.output, "result.json"),
        os.path.join(args.output, "manuscript.md"),
        os.path.join(args.work, "plan.json"),
        args.work,
    )
    if cerr:
        _print({"ok": False, "stage": "consistency", "error": cerr,
                "evidence_map": report})
        return 1
    _print({"ok": report.get("ok") and not creport["missing_sections"],
            "evidence_map": report, "consistency_report": creport})
    return 0


def cmd_finalize(args):
    summary, err = ci.finalize_result(args.output, args.work, status=args.status)
    if err:
        _print({"ok": False, "error": err})
        return 1
    summary["ok"] = True
    _print(summary)
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        description="Fact-traceable academic-writing skill entry point")
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("init", help="write skeleton + intermediate representations")
    a.add_argument("--input", default="/input")
    a.add_argument("--output", default="/app/output")
    a.add_argument("--work", default="/app/work")
    a.set_defaults(func=cmd_init)

    b = sub.add_parser("check", help="run evidence-map + consistency checks")
    b.add_argument("--output", default="/app/output")
    b.add_argument("--work", default="/app/work")
    b.set_defaults(func=cmd_check)

    c = sub.add_parser("finalize", help="finalize result.json with honest status")
    c.add_argument("--output", default="/app/output")
    c.add_argument("--work", default="/app/work")
    c.add_argument("--status", default=None,
                   choices=["complete", "partial", "failed",
                            "insufficient_evidence", "input_error"])
    c.set_defaults(func=cmd_finalize)

    return p


def main(argv=None):
    p = build_parser()
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
