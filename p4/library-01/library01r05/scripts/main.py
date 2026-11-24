#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py - thin entry wrapper; implementation lives in core_impl.py.

Python standard library only. All paths in SKILL.md call this script via
relative path `scripts/main.py`; the evaluator runs it under
/skills/weekly-skill with /input mounted read-only.
"""

import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print(
        "usage: main.py [--case-json PATH] [--task-path PATH] [--input-root DIR] "
        "[--output-dir DIR] [--work-dir DIR] [--skeleton-only] [--update PATCH] "
        "[--result PATH] [--check RESULT.json] [--help]"
    )


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
