#!/usr/bin/env python3
"""Self-test entry: run deliverable checks without installing anything.

Usage:
    python scripts/selftest.py
Overrides: RESULT, OUTPUT_DIR, WORK_DIR environment variables.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core  # noqa: E402


def main(argv=None):
    result = os.environ.get("RESULT", "/app/output/result.json")
    outdir = os.environ.get("OUTPUT_DIR", "/app/output")
    work = os.environ.get("WORK_DIR", "/app/work")
    report = core.check_deliverables(result, work, outdir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    fails = [c for c in report.get("checks", []) if c.get("severity") == "fail"]
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
