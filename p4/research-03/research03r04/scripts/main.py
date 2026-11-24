#!/usr/bin/env python3
"""CLI entry point for the systematic-review Skill (standard library only).

Run from the Skill root directory, for example:

    python -B scripts/main.py discover
    python -B scripts/main.py skeleton
    python -B scripts/main.py merge --payload /app/work/payload.json
    python -B scripts/main.py finalize
    python -B scripts/main.py selfcheck [--strict]

Defaults match the runtime contract:
    --input  /input
    --output /app/output
    --work   /app/work

Every command exits 0 on its own recoverable errors (a broken input is reported,
never made to crash the harness); only ``selfcheck --strict`` exits 1 when it
finds error-level findings.
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


def build_parser():
    p = argparse.ArgumentParser(prog="main.py",
                                description="systematic-review runtime helper")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", help="print the input inventory as JSON")
    add_common(d)

    sk = sub.add_parser("skeleton",
                        help="write an early valid result.json + run_state.json")
    add_common(sk)

    m = sub.add_parser("merge",
                       help="deep-merge a payload JSON into result.json")
    m.add_argument("--payload", required=True, help="JSON payload file to merge")
    add_common(m)

    f = sub.add_parser("finalize",
                       help="assemble result.json + review.md + screening_log.json + evidence_map.json")
    add_common(f)

    s = sub.add_parser("selfcheck",
                       help="validate result.json against the scoring contract")
    s.add_argument("--strict", action="store_true",
                   help="exit non-zero on error-level findings")
    add_common(s)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "discover":
            out, code = R.run_discover(args), 0
        elif args.cmd == "skeleton":
            out, code = R.run_skeleton(args), 0
        elif args.cmd == "merge":
            out = R.run_merge(args)
            code = 0
        elif args.cmd == "finalize":
            out = R.run_finalize(args)
            code = 0
        elif args.cmd == "selfcheck":
            out, code = R.run_selfcheck(args, strict=getattr(args, "strict", False))
        else:
            out, code = {"ok": False, "errors": ["unknown command %r" % args.cmd]}, 0
    except Exception as e:  # noqa: BLE001 - never crash the harness
        out = {"ok": False, "fatal": "%s: %s" % (type(e).__name__, e),
               "errors": ["%s: %s" % (type(e).__name__, e)]}
        code = 0
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
