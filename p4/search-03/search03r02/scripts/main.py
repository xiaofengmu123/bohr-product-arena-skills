#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — thin entry wrapper; implementation lives in core_impl.py.

Kept small (< 30 KB) so the bulk of the logic stays in one auditable module.
Usage:
    python scripts/main.py [discover|screen|finalize|all]
                           [--input DIR] [--work DIR] [--output DIR]
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: main.py [discover|screen|finalize|all] "
          "[--input DIR] [--work DIR] [--output DIR]")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
