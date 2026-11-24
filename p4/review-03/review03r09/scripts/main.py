#!/usr/bin/env python3
"""Thin entry point for the research-methods-stats-audit skill.

All subcommands (discover / scaffold / finalize / selftest) live in
core_impl.py so this wrapper stays small. Pure Python standard library.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402

USAGE = (
    "usage: python -B scripts/main.py COMMAND [--input DIR] "
    "[--output DIR] [--work DIR]\n"
    "\n"
    "commands:\n"
    "  discover   scan input, extract text, build locator index + manifest\n"
    "  scaffold   write a legal partial result.json skeleton early\n"
    "  finalize   merge findings, run numeric checks, render report.md\n"
    "  selftest   run built-in self-check on a synthetic input\n"
    "\n"
    "defaults: --input /input --output /app/output --work /app/work\n"
)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or "--help" in argv or "-h" in argv:
        print(USAGE)
        return 0
    return core_impl.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
