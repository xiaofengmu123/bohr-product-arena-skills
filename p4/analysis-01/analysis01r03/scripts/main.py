#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI entry for sci-repro-clean (deterministic, stdlib-only).

Phases
  bootstrap   write a valid partial result.json skeleton under OUTPUT
  full        discover -> profile -> clean -> artifacts -> finalize result.json
  selfcheck   validate result.json + artifacts, recompute report numbers,
              print a content fingerprint (no invented scores)
  fingerprint print the canonical content fingerprint of result.json

Defaults: --input /input --output /app/output --work /app/work
Exit code is 0 even on degraded input; `--strict` turns selfcheck failure
into exit code 2 for CI use.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core


def parse_args(argv):
    p = argparse.ArgumentParser(
        prog="scripts/main.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--phase", required=True,
                   choices=["bootstrap", "full", "selfcheck", "fingerprint"],
                   help="which stage to run")
    p.add_argument("--input", default="/input", help="input directory (default /input)")
    p.add_argument("--output", default="/app/output", help="output directory (default /app/output)")
    p.add_argument("--work", default="/app/work", help="work directory (default /app/work)")
    p.add_argument("--strict", action="store_true",
                   help="selfcheck failure -> exit code 2")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    for d in (args.output, args.work):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as exc:
            print(f"warning: cannot create directory {d}: {exc}", file=sys.stderr)
    phase = args.phase
    if phase == "bootstrap":
        return core.cmd_bootstrap(args)
    if phase == "full":
        return core.cmd_full(args)
    if phase == "selfcheck":
        return core.cmd_selfcheck(args, strict=args.strict)
    if phase == "fingerprint":
        return core.cmd_fingerprint(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
