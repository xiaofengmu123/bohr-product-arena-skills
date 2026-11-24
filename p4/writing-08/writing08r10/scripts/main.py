# -*- coding: utf-8 -*-
"""main.py -- CLI 入口与编排（薄壳，实现见 core_impl.py）。

用法：
  python3 scripts/main.py [--input /input] [--output /app/output] [--work /app/work] [step]
  step ∈ {preflight, inventory, render, validate, all}（缺省 all）
  无参时等价于 preflight + inventory + render + validate 串行执行。

纯标准库，确定性执行。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl  # noqa: E402


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="方法与实验章节写作 Skill 的确定性管线（契约优先、台账中间表示、证据后置校验）",
    )
    p.add_argument("--input", default=None, help="输入目录（缺省 /input）")
    p.add_argument("--output", default=None, help="输出目录（缺省 /app/output）")
    p.add_argument("--work", default=None, help="工作目录（缺省 /app/work）")
    p.add_argument("step", nargs="?", default="all",
                   choices=("preflight", "inventory", "render", "validate", "all"),
                   help="执行步骤")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return core_impl.run(args)


if __name__ == "__main__":
    sys.exit(main())
