# -*- coding: utf-8 -*-
"""main.py —— 科研选题与创新性评估 Skill 的 CLI 入口（薄封装，逻辑在 core_impl.py）。

用法（在 Skill 包根目录下，用相对路径调用）：
  python3 -B scripts/main.py skeleton  --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py evidence  --input /input --output /app/output --work /app/work
  # …… Agent 依 SKILL.md 写入 /app/work/fragments/*.json ……
  python3 -B scripts/main.py finalize  --input /input --output /app/output --work /app/work

phase 缺省时依次执行 skeleton → evidence → finalize（便于单命令冒烟运行）。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl  # noqa: E402


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="科研选题与创新性评估：骨架 / 证据抽取 / 合并校验",
    )
    p.add_argument("phase", nargs="?", default=None,
                   choices=["skeleton", "evidence", "finalize"],
                   help="要执行的阶段；省略则依次执行全部")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    phases = [args.phase] if args.phase else ["skeleton", "evidence", "finalize"]
    for ph in phases:
        code = core_impl.run_phase(ph, args.input, args.output, args.work)
        if code != 0:
            return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
