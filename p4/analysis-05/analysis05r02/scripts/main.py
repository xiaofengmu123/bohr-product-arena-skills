"""main.py - thin CLI entry for the budgeted tabular analysis skill.

Implementation lives in core_impl.py; this wrapper only routes subcommands so
that the entry script stays small (<30KB). Call from the Skill root with
relative paths, e.g.:

    python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402

USAGE = """usage: python3 -B scripts/main.py <command> [options]
commands:
  scaffold  write a valid partial result.json skeleton and start the budget clock
  profile   discover sources, build field dictionary, first-pass quality checks
  clean     dedup / missing / outlier handling; record rules and row impact
  analyze   descriptive stats + Welch t / ANOVA F / Mann-Whitney U / Kruskal-Wallis H / Pearson
  render    write editable SVG figures and report.md (numbers sourced from stats.json)
  finalize  merge stats into result.json, backfill output_contract, set status
  clock     print remaining budget and phase recommendation
  selftest  run the self-test suite (same as scripts/selftest.py)
"""


def main(argv):
    if not argv or argv[0] in ("--help", "-h", "help"):
        print(USAGE)
        return 0
    return core_impl.main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
