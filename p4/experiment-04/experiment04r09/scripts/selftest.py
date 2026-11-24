#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检入口：等价于 `python scripts/main.py selftest`。仅标准库。

覆盖：脚本仅标准库、demo 跑通、result.json 结构合法、SVG 可解析、
PDF 头合法、空输入、坏 JSON、重复运行确定性。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as skill  # noqa: E402

if __name__ == "__main__":
    sys.exit(skill.cmd_selftest())
