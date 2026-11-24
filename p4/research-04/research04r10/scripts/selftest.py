#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone self-check runner for the research-frontier-trend skill.

Usage:
    python3 scripts/selftest.py [--input /input] [--output /app/output] [--work /app/work]

Exit code 0 means all hard checks passed; non-zero means at least one hard
check failed.  The full report is written to /app/work/selftest.json.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser(prog="selftest.py", description=__doc__)
    p.add_argument("--input", default=core_impl.DEFAULT_INPUT)
    p.add_argument("--output", default=core_impl.DEFAULT_OUTPUT)
    p.add_argument("--work", default=core_impl.DEFAULT_WORK)
    args = p.parse_args(argv)
    ctx = core_impl.Context(args.input, args.output, args.work)
    return core_impl.run_selftest(ctx)


if __name__ == "__main__":
    sys.exit(main())
