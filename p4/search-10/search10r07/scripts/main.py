#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — CLI orchestrator for the cross-lingual literature search skill.

Subcommands
  init      discover /input, extract case_id + output_contract, and atomically
            write a legal partial result skeleton to /app/output/result.json.
            Run this FIRST so a valid partial deliverable is on disk early.
  patch     deep-merge a JSON patch into result.json (incremental update).
  validate  verify result.json is legal JSON with case_id, all contract fields
            and the standard fields; print issues and set a non-zero exit.
  status    print a short progress summary.
  selftest  run the robustness self-check scenarios and report PASS/FAIL.

Standard library only. Defaults follow the runtime contract:
  --input  /input
  --output /app/output
  --work   /app/work
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=DEFAULT_INPUT,
                        help="input directory (default: /input)")
    common.add_argument("--output", default=DEFAULT_OUTPUT,
                        help="output directory (default: /app/output)")
    common.add_argument("--work", default=DEFAULT_WORK,
                        help="work directory (default: /app/work)")

    parser = argparse.ArgumentParser(
        prog="main.py",
        parents=[common],
        description="Cross-lingual literature search skill orchestrator.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", parents=[common],
                   help="discover input and write the partial skeleton")
    p_patch = sub.add_parser("patch", parents=[common],
                             help="deep-merge a JSON patch file")
    p_patch.add_argument("--patch", required=True, help="path to a JSON patch file")
    sub.add_parser("validate", parents=[common],
                   help="check result.json legality and completeness")
    sub.add_parser("status", parents=[common],
                   help="print a short progress summary")
    sub.add_parser("selftest", parents=[common],
                   help="run robustness self-check scenarios")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.command == "init":
        code, summary = core.run_init(args.input, args.output, args.work)
        print("status=%s case_id=%s contract_fields=%d errors=%d degradations=%d"
              % (summary["status"], summary["case_id"],
                 len(summary["contract_fields"]),
                 len(summary["errors"]), len(summary["degradations"])))
        return code

    if args.command == "patch":
        code, message = core.run_patch(args.output, args.patch)
        print(message)
        return code

    if args.command == "validate":
        code, message = core.run_validate(args.output, args.work)
        print(message)
        return code

    if args.command == "status":
        code, message = core.run_status(args.output, args.work)
        print(message)
        return code

    if args.command == "selftest":
        ok, report = core.self_test()
        print(report)
        return 0 if ok else 1

    print("unknown command", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
