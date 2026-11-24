#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 鲁棒性自检入口（进程内运行，不依赖子进程管道）。

覆盖场景：--help 退出码、空输入、有 case 无材料、坏 JSON、坏编码、
超大文件、重复运行确定性。任一项失败则退出码非 0。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(core_impl.run_selftest())
