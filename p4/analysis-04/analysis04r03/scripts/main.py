#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Entry point for the deterministic scientific data-analysis Skill.

Usage (relative paths, run from the package root ``/skills/weekly-skill``):

    python3 -B scripts/main.py --input /input --output /app/output --work /app/work --phase init
    python3 -B scripts/main.py --input /input --output /app/output --work /app/work --phase profile
    python3 -B scripts/main.py --input /input --output /app/output --work /app/work --phase analyze
    python3 -B scripts/main.py --input /input --output /app/output --work /app/work --phase validate
    python3 -B scripts/main.py --input /input --output /app/output --work /app/work --phase all

``--phase all`` is the default and runs init -> profile -> analyze -> validate.
Every phase is idempotent: it re-reads ``result.json`` and only adds to it, so
an interrupted run never loses earlier work.  This script always exits 0 on
handled failures and writes a minimal legal ``result.json`` if anything goes
wrong.
"""

import os
import sys
import argparse

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import core_impl as core  # noqa: E402


def build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Deterministic scientific data-analysis executor (stdlib only).",
    )
    parser.add_argument("--input", default=core.DEFAULT_INPUT,
                        help="input directory (default: %(default)s)")
    parser.add_argument("--output", default=core.DEFAULT_OUTPUT,
                        help="output directory for result.json + artifacts (default: %(default)s)")
    parser.add_argument("--work", default=core.DEFAULT_WORK,
                        help="scratch/working directory (default: %(default)s)")
    parser.add_argument("--phase", default="all",
                        choices=["all", "init", "profile", "analyze", "validate"],
                        help="pipeline phase to run (default: all)")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return core.run_pipeline(args.input, args.output, args.work, args.phase, argv=sys.argv)


if __name__ == "__main__":
    sys.exit(main())
