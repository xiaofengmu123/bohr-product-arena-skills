#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI entry point for the sci-data-cleaning skill.

Standard library only; deterministic; always exits with code 0 even on bad
input. Usage:

    python scripts/main.py --phase bootstrap --input /input --output /app/output --work /app/work
    python scripts/main.py --phase full      --input /input --output /app/output --work /app/work
    python scripts/main.py --phase selfcheck --output /app/output

Guarantees:
  * bootstrap writes a valid result.json skeleton (status=partial) within
    seconds, so any later failure leaves a loadable, honest partial deliverable.
  * full discovers materials, profiles/cleans the main tabular source with
    conservative defaults (no imputation, no silent edits), and writes
    cleaned_data.csv / cell_audit.csv / data_profile.json / quality_report.md /
    evidence_map.json / rules.json, then finalizes result.json.
  * selfcheck validates result.json and artifacts and recomputes the
    report-vs-table consistency without inventing scores.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as impl


def main():
    parser = argparse.ArgumentParser(
        prog="sci-data-cleaning",
        description="Verifiable scientific aggregate-table cleaning pipeline.")
    parser.add_argument("--phase", choices=("bootstrap", "full", "selfcheck"),
                        default="full")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--work", default="/app/work")
    args = parser.parse_args()

    summary = None
    try:
        if args.phase == "bootstrap":
            summary = impl.bootstrap(args.input, args.output, args.work)
        elif args.phase == "full":
            summary = impl.run_full(args.input, args.output, args.work)
        else:
            summary = impl.run_selfcheck(args.input, args.output, args.work)
    except Exception as exc:  # noqa: BLE001 - guarantee exit 0, honest record
        impl.emergency_result(args.input, args.output, args.work, exc)
        summary = {"phase": args.phase, "status": "partial",
                   "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
