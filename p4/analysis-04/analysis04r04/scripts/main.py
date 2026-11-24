#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-analysis-exec 入口包装：仅标准库，负责调度核心实现。

子命令：
  init     尽快落盘一版合法的 partial result.json 骨架
  profile  扫描 /input 数据文件，产出 data_profile.json 与字段对齐表
  validate 核对 result.json 是否满足 output_contract 与证据规则
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import core_impl  # noqa: E402

if __name__ == "__main__":
    sys.exit(core_impl.main())
