#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py: 入口。init -> (Agent 增量 update) -> check -> render。

用法（在 SKILL.md 所在 Skill 根目录执行，统一相对路径）：
  python scripts/main.py init --case /input/case.json --out /app/output/result.json
  python scripts/main.py update --out /app/output/result.json --fragment /app/work/f1.json --note "回填背景与缺口"
  python scripts/main.py check --case /input/case.json --result /app/output/result.json
  python scripts/main.py render --result /app/output/result.json --out /app/output

仅依赖 Python 标准库；幂等、确定性，无随机源。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl  # noqa: E402


if __name__ == "__main__":
    sys.exit(core_impl.main())
