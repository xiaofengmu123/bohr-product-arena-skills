#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill 自检入口（仅标准库）。

用法：
    python3 scripts/selftest.py [--tmp DIR]

逐条覆盖评分维度的可自动化部分：
  空输入 / 坏 JSON / 契约回填 / 表格读取 / 图型推荐 / 缺数据不编造 /
  状态诚实性降级。任何一项失败退出码为 1，便于提交前自查。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main as controller  # noqa: E402


def run():
    argv = ["selftest"]
    # 允许外部传入 --tmp
    i = 1
    while i < len(sys.argv):
        argv.append(sys.argv[i])
        if sys.argv[i] == "--tmp" and i + 1 < len(sys.argv):
            argv.append(sys.argv[i + 1])
            i += 1
        i += 1
    code = controller.main(argv)
    return code


if __name__ == "__main__":
    sys.exit(run())
