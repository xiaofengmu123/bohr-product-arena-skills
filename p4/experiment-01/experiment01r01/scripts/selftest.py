#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — scix-plan-evidence 的自检脚本（合成输入，非正式案例）。

在临时目录生成三组合成输入并跑通 probe→scaffold→validate：
  1) 正常合成案例；2) 空输入目录；3) 坏 JSON。
所有合成材料均带 __synthetic__ 标记，不冒充正式案例结果；不预写任何
正式测试产物。退出码 0=全部通过，1=存在失败。

用法：python3 scripts/selftest.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(core_impl.cmd_selftest(None))
