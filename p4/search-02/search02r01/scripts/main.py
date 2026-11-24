#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 薄入口包装：把命令转发给 core_impl.py 执行。"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "core_impl.py")

USAGE = """科研问题驱动检索 Skill（证据溯源强化版）
调用示例（先 cd 到 Skill 根目录）：
  python3 -B scripts/main.py --help
  python3 -B scripts/main.py run --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py set-plan --file /app/work/plan_in.json --work /app/work
  python3 -B scripts/main.py import --file /app/work/records_in.json --work /app/work
  python3 -B scripts/main.py evidence --file /app/work/evidence_in.json --work /app/work
  python3 -B scripts/main.py set-stop --file /app/work/stop_in.json --work /app/work
  python3 -B scripts/main.py assemble --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py selfcheck --output /app/output --work /app/work
"""


def _should_forward(args):
    if not args:
        return False
    return args[0] not in ("--help", "-h", "help")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not _should_forward(args):
        print(USAGE)
        raise SystemExit(0)
    sys.argv[0] = TARGET
    runpy.run_path(TARGET, run_name="__main__")