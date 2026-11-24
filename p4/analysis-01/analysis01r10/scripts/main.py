#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI entry for sci-cleaning-pipeline.

Usage (all standard-library, deterministic, exit code 0 even on bad input):

    python scripts/main.py --phase bootstrap --input /input --output /app/output --work /app/work
    python scripts/main.py --phase full      --input /input --output /app/output --work /app/work
    python scripts/main.py --phase selfcheck --input /input --output /app/output --work /app/work

Guarantees:
  * bootstrap writes a valid partial result.json skeleton within seconds, so any
    later failure leaves a loadable, honest partial deliverable.
  * full discovers materials, parses the main tabular source, builds a
    per-cell intermediate representation, classifies missing values, detects
    anomalies and duplicates, resolves units, and writes cleaned_data.csv +
    data_profile.json + rules_resolved.json + evidence_map.json +
    quality_report.md, then finalizes result.json.
  * selfcheck runs the independent verifier (selftest.py), which recomputes the
    report numbers from the cleaned table rather than trusting the generator.
  * any unhandled exception falls back to a minimal legal skeleton and exits 0.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl
import selftest


def build_argparser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="sci-cleaning-pipeline: evidence-traceable cleaning of "
                    "scientific aggregate-table transcriptions",
    )
    parser.add_argument(
        "--phase", choices=["bootstrap", "full", "selfcheck"], default="full",
        help="pipeline phase (default: full)",
    )
    parser.add_argument("--input", default="/input", help="input directory")
    parser.add_argument("--output", default="/app/output", help="output directory")
    parser.add_argument("--work", default="/app/work", help="work directory")
    return parser


def main(argv=None):
    args = build_argparser().parse_args(argv)
    try:
        if args.phase == "bootstrap":
            core_impl.bootstrap(args.input, args.output, args.work)
            print("[sci-cleaning-pipeline] bootstrap: result.json skeleton written")
        elif args.phase == "full":
            result = core_impl.run_full(args.input, args.output, args.work)
            print(f"[sci-cleaning-pipeline] full: status={result.get('status')}")
        elif args.phase == "selfcheck":
            report = selftest.run(args.input, args.output, args.work)
            print(f"[sci-cleaning-pipeline] selfcheck: ok={report.get('ok')}, "
                  f"checks={report.get('total')}, failed={report.get('failed')}")
    except Exception as exc:  # never crash the harness
        try:
            core_impl.bootstrap(args.input, args.output, args.work)
            core_impl.append_error(args.output, f"{type(exc).__name__}: {exc}")
        except Exception:
            pass
        print(f"[sci-cleaning-pipeline] error: {type(exc).__name__}: {exc}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
