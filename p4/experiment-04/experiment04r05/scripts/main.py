"""main.py - thin CLI entry wrapper for the contract-first plan-comparison skill.

Dispatches subcommands to core.py. Standard library only. All paths are
resolved relative to this file's directory, so the skill remains relocatable
when installed under /skills/weekly-skill.

Subcommands:
    inspect     discover inputs + contract, persist a legal result skeleton
    assemble    merge analysis.json, compute scores/arithmetic, emit artifacts
    validate    audit a result.json against the contract and return exit code
    demo        end-to-end example run (marked as sample, not a real case)
"""

import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _usage():
    print("usage: python scripts/main.py <inspect|assemble|validate|demo>")
    print("       [--input DIR] [--output DIR] [--work DIR] [--help]")
    print("")
    print("defaults: --input /input  --output /app/output  --work /app/work")
    print("env override: INPUT_DIR, OUTPUT_DIR, WORK_DIR")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv or len(sys.argv) == 1:
        _usage()
        raise SystemExit(0)
    target = os.path.join(HERE, "core.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
