#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Entry point for sci-figure-authoring.

Thin orchestrator: input discovery -> result.json skeleton -> render gate ->
validation -> export -> result merge. All logic lives in core_impl.py so
this file stays small and the skill remains auditable.

Usage:
    python scripts/main.py --input /input --output /app/output --work /app/work
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl as core


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="sci-figure-authoring",
        description="From source materials to a traceable FigureIR, layered SVG and compliant exports.",
    )
    ap.add_argument("--input", default="/input", help="input directory (case.json, task.md, materials)")
    ap.add_argument("--output", default="/app/output", help="output directory for result.json and figures")
    ap.add_argument("--work", default="/app/work", help="work directory for figure_ir.json and intermediate files")
    a = ap.parse_args(argv)

    os.makedirs(a.output, exist_ok=True)
    os.makedirs(a.work, exist_ok=True)

    summary = core.run_pipeline(a)
    print(core.VERSION, "|", summary.get("stage"), "|", summary.get("case_id"),
          "| result:", summary.get("result"))
    if summary.get("stage") == "rendered":
        print("errors=%d warnings=%d" % (summary.get("errors", 0), summary.get("warnings", 0)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
