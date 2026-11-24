#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 薄入口：解析参数后委托 core_impl.py（保持本文件 <30KB）。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

_HELP = """usage: main.py [command] [--input DIR] [--output DIR] [--work DIR]

commands:
  skeleton   扫描 /input，落盘 partial 结果骨架 + /app/work 账本
  normalize  对题录去重，生成 dedup.json 与 judgments_template.json
  finalize   合并判定、校验证据子串与计数闭合，写 result.json + 可编辑产物
  selfcheck  逐项打印 PASS/FAIL，校验 result.json 的契约/证据/计数/确定性

defaults: --input /input --output /app/output --work /app/work
仅依赖 Python 标准库。
"""


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(_HELP)
        return 0
    sys.path.insert(0, HERE)
    import core_impl  # noqa: E402
    return core_impl.main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
