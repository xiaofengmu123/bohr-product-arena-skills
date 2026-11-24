#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py - thin entry wrapper for the deterministic library repair engine.

All implementation lives in core_impl.py so this file stays small and stable.
"""

import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: main.py [--input-root DIR] [--output-dir DIR] [--work-dir DIR]")
    print("                 [--skeleton-only] [--update patch.json --result result.json]")
    print("                 [--check result.json --case-json case.json] [--help]")
    print("")
    print("defaults: --input-root /input --output-dir /app/output --work-dir /app/work")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    namespace = runpy.run_path(target, run_name="__not_main__")
    raise SystemExit(namespace["main"](sys.argv[1:]))
