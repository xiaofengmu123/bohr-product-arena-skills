#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI entry for the sci-data-cleaning skill.

All standard-library, deterministic, exit code 0 even on bad input.

    python scripts/main.py --phase bootstrap --input /input --output /app/output --work /app/work
    python scripts/main.py --phase full      --input /input --output /app/output --work /app/work
    python scripts/main.py --phase finalize  --input /input --output /app/output --work /app/work
    python scripts/main.py --phase selfcheck --output /app/output

Guarantees
----------
* bootstrap writes a valid result.json skeleton within seconds (status=partial)
  with every output_contract key already filled (null / [] / {}), so any later
  failure leaves a loadable, honest partial deliverable.
* full discovers materials without trusting filenames, profiles and
  conservatively cleans the main tabular source, then writes cleaned_data.csv,
  data_profile.json, quality_report.md, evidence_map.json, change_log.json and
  an updated result.json.
* finalize merges the Agent's judgement (agent_decisions.json) deterministically.
* selfcheck validates result.json and artifacts without inventing scores.
* Every phase exits with code 0; problems are recorded in result.json, never
  raised as a crash.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl as core  # noqa: E402

PHASES = ("bootstrap", "full", "finalize", "selfcheck", "all")


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Deterministic sci-data-cleaning pipeline (bootstrap/full/finalize/selfcheck).",
    )
    p.add_argument("--phase", choices=PHASES, default="all",
                   help="pipeline phase to run (default: all)")
    p.add_argument("--input", default="/input", help="input directory (default: /input)")
    p.add_argument("--output", default="/app/output", help="output directory (default: /app/output)")
    p.add_argument("--work", default="/app/work", help="work directory (default: /app/work)")
    return p


def run_phase(phase, args):
    if phase == "bootstrap":
        return core.run_bootstrap(args.input, args.output, args.work)
    if phase == "full":
        return core.run_full(args.input, args.output, args.work)
    if phase == "finalize":
        return core.run_finalize(args.input, args.output, args.work)
    if phase == "selfcheck":
        report = core.run_selfcheck(args.output)
        core.write_json(os.path.join(args.work, "selfcheck.json"), report)
        _print_selfcheck(report)
        return report
    raise ValueError(f"unknown phase: {phase}")


def _print_selfcheck(report):
    print("selfcheck:", "PASS" if report.get("ok") else "FAIL")
    for c in report.get("checks", []):
        mark = "[ok]" if c.get("ok") else "[!!]"
        detail = f" -- {c.get('detail')}" if c.get("detail") else ""
        print(f"  {mark} {c.get('item')}{detail}")


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.phase == "all":
            core.run_bootstrap(args.input, args.output, args.work)
            core.run_full(args.input, args.output, args.work)
            core.run_finalize(args.input, args.output, args.work)
        else:
            run_phase(args.phase, args)
    except Exception as exc:  # never crash the harness
        try:
            os.makedirs(args.work, exist_ok=True)
            os.makedirs(args.output, exist_ok=True)
            core.write_json(os.path.join(args.work, "fatal.json"),
                            {"phase": args.phase, "error": str(exc)})
        except Exception:
            pass
        sys.stderr.write(f"[skill] phase {args.phase} recovered from: {exc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
