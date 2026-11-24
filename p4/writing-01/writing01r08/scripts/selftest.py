#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 自检入口（纯标准库）。

交付/提交前运行：

  python3 scripts/selftest.py     # 退出码 0 表示全部通过

该脚本在临时目录构建最小夹具，跑通“骨架落盘 -> 证据溯源校验(含 quote 子串核验与
错配检测) -> 一致性检查 -> 评分对齐报告 -> 空目录/坏 JSON 降级”全流程，并断言产物
合法。它不读取、不生成任何正式案例数据。
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl  # noqa: E402


def main() -> int:
    report = core_impl.selfcheck()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report.get("ok"):
        print("\n[SELFTEST FAILED]", file=sys.stderr)
        return 1
    print("\n[SELFTEST PASSED] all checks ok", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
