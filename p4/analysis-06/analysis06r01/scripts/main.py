#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main: deterministic read-only scientific DB query & analysis pipeline.

Usage:
  python3 -B scripts/main.py --help
  python3 -B scripts/main.py --input /input --output /app/output --work /app/work

Behaviour
---------
1. discover input (case.json, task.md, *.db/*.sqlite, *.csv/*.tsv, dictionary);
2. immediately write a legal result.json skeleton containing case_id, every
   output_contract key (null/[] when unknown) and status/sources/uncertainty/
   change_log, so a partial deliverable always exists within the first minutes;
3. open each data source read-only, inspect schema, profile every column,
   detect missing/duplicate/outlier values, compute descriptive statistics and
   Pearson correlations, infer candidate cross-table join paths, and render
   editable SVG charts;
4. execute tagged SELECT queries found in <work>/queries.json (read-only);
5. emit report.md, evidence_map.json, schema.json, profiles.json, CSV tables,
   query_log.csv, value_counts.json and figures/*.svg, then finalise result.json
   with an honest status.

Only the Python standard library is used. No prepared answers are produced.
"""

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core_impl as core


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Read-only scientific DB query & analysis pipeline.")
    p.add_argument("--input", default="/input", help="input directory (default /input)")
    p.add_argument("--output", default="/app/output", help="output directory (default /app/output)")
    p.add_argument("--work", default="/app/work", help="work directory (default /app/work)")
    p.add_argument("--queries", default=None,
                   help="optional queries.json path (default <work>/queries.json)")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    t0 = time.time()
    try:
        result = core.run_pipeline(args.input, args.output, args.work,
                                   queries_path=args.queries)
    except Exception as exc:  # noqa: BLE001 - last-resort legal skeleton
        # Never leave the caller without a parseable result.json.
        fallback = {
            "case_id": "unknown",
            "status": "input_error",
            "sources": [],
            "uncertainty": [{"item": "pipeline", "level": "high",
                             "reason": "unhandled exception: %r" % exc}],
            "pending_human": ["pipeline crashed; inspect traceback"],
            "change_log": [{"ts": core._iso_now(), "step": "fallback",
                            "change": "unhandled exception caught at top level",
                            "status": "input_error"}],
            "errors": ["unhandled exception: %r" % exc],
            "degradations": [],
            "limitations": ["pipeline did not complete"],
            "analysis": {}, "query_log": [], "answers": {}, "artifacts": {},
            "runtime": {"seconds": core._round6(time.time() - t0),
                        "version": core.VERSION},
        }
        try:
            os.makedirs(args.output, exist_ok=True)
            core._write_json(Path(args.output) / "result.json", fallback)
        except OSError:
            pass  # output directory unwritable: nothing more we can do
        print("fatal: %r" % exc, file=sys.stderr)
        return 2
    status = result.get("status", "input_error")
    print("pipeline complete: status=%s case_id=%s artifacts=%d"
          % (status, result.get("case_id"), len(result.get("artifacts", {}))))
    # exit 0 for every honest outcome, including insufficient_evidence
    return 0


if __name__ == "__main__":
    sys.exit(main())
