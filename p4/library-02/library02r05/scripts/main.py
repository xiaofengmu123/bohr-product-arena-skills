# -*- coding: utf-8 -*-
"""main.py — 薄入口封装（实现见 core_impl.py）。"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: python3 -B scripts/main.py [--input DIR] [--output DIR] "
          "[--work DIR] [--skeleton-only] [--update PATCH --result R] "
          "[--check R --case-json C] [--help]")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
