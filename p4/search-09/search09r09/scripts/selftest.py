#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检入口：等价于 `python3 -B scripts/main.py self-test`。

三种异常场景（空目录 / 坏 JSON / 缺输入目录）都必须退出码 0 并产出合法结果。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main.cmd_self_test(None))
