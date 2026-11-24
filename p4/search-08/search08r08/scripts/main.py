#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 薄入口，转发到 pipeline.py（实现见 pipeline.py）。"""

import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: main.py [--input DIR] [--output DIR] [--work DIR] "
          "<skeleton|normalize|finalize|criteria-diff|selfcheck> [--help]")
    print("defaults: input=/input output=/app/output work=/app/work")


if __name__ == "__main__":
    if not sys.argv[1:] or "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "pipeline.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
