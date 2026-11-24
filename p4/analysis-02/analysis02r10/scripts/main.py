#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — thin entry wrapper for the analysis-02 skill.

The implementation lives in pipeline.py; this wrapper only normalises the
execution directory so that the skill can be invoked from any cwd:

    cd /skills/weekly-skill && python scripts/main.py all
    cd /skills/weekly-skill && python scripts/main.py probe | skeleton | infer | plan | compute | package | verify

Default paths follow the runtime contract (input=/input, output=/app/output,
work=/app/work) and can be overridden with --input / --output / --work.
"""

import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: main.py [stage] [--input DIR] [--output DIR] [--work DIR] [--help]")
    print("stages: probe skeleton infer plan compute package verify all")
    print("Runs the deterministic statistical-analysis pipeline (stdlib only).")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "pipeline.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
