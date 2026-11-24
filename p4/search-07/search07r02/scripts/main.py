#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 薄入口包装（实现位于 core_impl.py）。

用法：python scripts/main.py <scaffold|inspect|run|merge> [选项]
      或  python scripts/main.py --help
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

if __name__ == "__main__":
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
