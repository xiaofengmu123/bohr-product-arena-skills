# -*- coding: utf-8 -*-
"""Thin CLI entry point for the literature-delta Skill.

This module only parses arguments and routes to core.py. No domain logic
lives here; that keeps the entry point small (< 30KB) and the domain rules
isolated in core.py (the "split" architecture).

Usage (paths are relative to the skill root, e.g. /skills/weekly-skill):

    python3 scripts/main.py scaffold  --input /input --output /app/output --work /app/work
    python3 scripts/main.py run       --input /input --output /app/output --work /app/work
    python3 scripts/main.py finalize  --input /input --output /app/output --work /app/work \
                                      --findings /app/work/findings.json
    python3 scripts/main.py dedup     --records /app/work/records.json --out /app/work/dedup.json
    python3 scripts/main.py normalize --record '{"doi":"10.1000/XYZ","title":"A Paper"}'

`scaffold` must run within the first 3 minutes: it writes a valid partial
result.json. `run` is the deterministic fallback (scaffold + inventory +
finalize with empty findings). `finalize` merges the agent's findings and
enriches them with dedup / version classification / incremental diff.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"


def add_common(sp):
    sp.add_argument("--input", default=DEFAULT_INPUT)
    sp.add_argument("--output", default=DEFAULT_OUTPUT)
    sp.add_argument("--work", default=DEFAULT_WORK)


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="literature-delta Skill CLI (deterministic, stdlib-only)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("scaffold", help="write a partial result.json skeleton")
    add_common(sp)

    sp = sub.add_parser("inventory", help="write a deterministic input inventory")
    add_common(sp)

    sp = sub.add_parser("normalize", help="normalize a single JSON record")
    sp.add_argument("--record", required=True,
                    help="JSON string, or @path to a JSON file")

    sp = sub.add_parser("dedup", help="dedup a records.json file")
    sp.add_argument("--records", required=True)
    sp.add_argument("--out", required=True)

    sp = sub.add_parser("finalize", help="merge findings and write result.json + report.md")
    add_common(sp)
    sp.add_argument("--findings", default=None,
                    help="path to findings.json (default: <work>/findings.json)")

    sp = sub.add_parser("run", help="deterministic fallback: scaffold+inventory+finalize")
    add_common(sp)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    cmd = args.cmd
    if cmd == "scaffold":
        return core.cmd_scaffold(args.input, args.output, args.work)
    if cmd == "inventory":
        return core.cmd_inventory(args.input, args.output, args.work)
    if cmd == "normalize":
        return core.cmd_normalize(args.record)
    if cmd == "dedup":
        return core.cmd_dedup(args.records, args.out)
    if cmd == "finalize":
        return core.cmd_finalize(args.input, args.output, args.work, args.findings)
    if cmd == "run":
        return core.cmd_run(args.input, args.output, args.work)
    return 2


if __name__ == "__main__":
    sys.exit(main())
