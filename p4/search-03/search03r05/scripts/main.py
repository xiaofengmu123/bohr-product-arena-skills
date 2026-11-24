# -*- coding: utf-8 -*-
"""main.py - thin entry wrapper for the literature screening & ranking skill.

Subcommands: discover | screen | finalize | selftest
Implementation lives in core_impl.py (kept separate to keep this file small).

Usage:
    python3 -B scripts/main.py discover --input /input --work /app/work --output /app/output
    python3 -B scripts/main.py screen   --work /app/work --output /app/output
    python3 -B scripts/main.py finalize --output /app/output --work /app/work
    python3 -B scripts/main.py selftest
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


def _help():
    print("usage: main.py {discover|screen|finalize|selftest} "
          "[--input DIR] [--work DIR] [--output DIR] [--help]")


if __name__ == "__main__":
    if len(sys.argv) == 1 or sys.argv[1] in ("-h", "--help"):
        _help()
        raise SystemExit(0)
    raise SystemExit(core_impl.main(sys.argv[1:]))