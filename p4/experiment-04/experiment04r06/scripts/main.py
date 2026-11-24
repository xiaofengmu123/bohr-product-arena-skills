# -*- coding: utf-8 -*-
"""main.py — 薄入口包装；实现位于 core_impl.py（仅标准库）。"""

import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: python scripts/main.py [inspect|init-models|assemble|validate|demo] "
          "[--input DIR] [--work DIR] [--output DIR]")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv or len(sys.argv) == 1:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
