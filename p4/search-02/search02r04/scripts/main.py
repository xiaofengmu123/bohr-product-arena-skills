# -*- coding: utf-8 -*-
"""main.py — thin entry wrapper for the research-search skill.

All logic lives in core_impl.py; this file only locates the implementation and
delegates so the skill can be invoked from any working directory with
``python3 -B scripts/main.py``.
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: python3 -B scripts/main.py [--input DIR] [--output DIR] "
          "[--work DIR] <run|set-plan|import|evidence|assemble|selfcheck>")
    print("       python3 -B scripts/main.py --help")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
