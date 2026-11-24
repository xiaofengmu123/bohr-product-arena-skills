#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 入口包装器（实现见 core_impl.py）。

本地冒烟示例：
    python3 scripts/main.py all --input ./demo/input --output ./demo/output --work ./demo/work
运行时（平台）：
    cd /skills/weekly-skill
    python3 scripts/main.py all --input /input --output /app/output --work /app/work
"""

import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


if __name__ == "__main__":
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
