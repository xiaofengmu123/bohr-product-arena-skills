#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — thin entry wrapper (implementation in pipeline.py)."""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: main.py {skeleton|normalize|finalize|selfcheck} "
          "[--input DIR] [--output DIR] [--work DIR]")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "pipeline.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
