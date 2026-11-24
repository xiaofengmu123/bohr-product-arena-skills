#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — thin entry wrapper for the document library repair skill.

The real implementation lives in ``core_impl.py``.  Keep this file small:
the wrapper only makes the sibling module importable regardless of the
current working directory and delegates all logic.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


def _print_help():
    print("usage: main.py [--input DIR] [--output DIR] [--work DIR]")
    print("                [--case-json PATH] [--task-path PATH]")
    print("                [--skeleton-only | --update PATCH | --check]")
    print()
    print("defaults: --input /input --output /app/output --work /app/work")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _print_help()
        raise SystemExit(0)
    sys.exit(core_impl.main(sys.argv[1:]))
