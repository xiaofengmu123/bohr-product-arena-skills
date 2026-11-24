"""Thin entry point for the deterministic literature-screening Skill.

Dispatches subcommands to pipeline.py. Standard library only. All paths are
relative to the scripts/ directory, so the Skill is portable.

Usage:
    python3 -B scripts/main.py <skeleton|normalize|finalize|selfcheck> \
        [--input DIR] [--output DIR] [--work DIR]
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pipeline  # noqa: E402

SUBCOMMANDS = {"skeleton", "normalize", "finalize", "selfcheck"}


def _help():
    print(
        "usage: python3 -B scripts/main.py "
        "<skeleton|normalize|finalize|selfcheck> "
        "[--input DIR] [--output DIR] [--work DIR]"
    )
    print("defaults: --input /input --output /app/output --work /app/work")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        _help()
        raise SystemExit(0 if not args else 0)
    if args[0] not in SUBCOMMANDS:
        _help()
        raise SystemExit(1)
    pipeline.main(args)
