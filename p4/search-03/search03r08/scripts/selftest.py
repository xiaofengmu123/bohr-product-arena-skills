#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 独立自检入口。

用合成夹具（明确标注，非正式案例）跑通 discover→screen→finalize，
并验证空输入与坏 JSON 的诚实降级。退出码 0 表示通过。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(core_impl.run_selftest())
