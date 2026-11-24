#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 薄入口分发器，实现见 core_impl.py。

用法：python -B scripts/main.py <discover|scaffold|finalize|selftest>
     [--input DIR] [--output DIR] [--work DIR] [--help]

所有管线逻辑均在 core_impl.py（纯标准库）；本文件仅做路径装配与分发，
保证 `python -B scripts/main.py --help` 退出码为 0。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(core_impl.main(sys.argv[1:]))
