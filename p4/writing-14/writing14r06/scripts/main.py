#!/usr/bin/env python3
"""入口：init / check / render 三个子命令（薄封装，逻辑在 core_impl.py）。

用法（在 SKILL.md 所在目录，即 Skill 根目录执行）：
  python scripts/main.py init    --case /input/case.json --out /app/output --work /app/work
  python scripts/main.py check   --case /input/case.json --out /app/output --strict
  python scripts/main.py render  --case /input/case.json --out /app/output

仅依赖 Python 标准库。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl as core


def main():
    ap = argparse.ArgumentParser(
        prog="main.py",
        description="开题报告与研究计划书 Skill 执行器（init/check/render）")
    ap.add_argument("command", choices=["init", "check", "render"],
                    help="init=落盘骨架 check=契约/证据/产物校验 render=从中间表示编译可编辑产物")
    ap.add_argument("--case", default="/input/case.json", help="case.json 路径")
    ap.add_argument("--out", default="/app/output", help="最终产物目录")
    ap.add_argument("--work", default="/app/work", help="工作目录")
    ap.add_argument("--strict", action="store_true", help="check 发现 FAIL 时返回非零退出码")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    os.makedirs(a.work, exist_ok=True)
    info = core.discover_case(a.case)

    if a.command == "init":
        r = core.init(a.case, a.out, a.work, info)
        print(json.dumps(r, ensure_ascii=False))
        return 0

    if a.command == "check":
        fails, warns = core.check(a.case, a.out, info)
        print("FAIL=%d WARN=%d" % (len(fails), len(warns)))
        for f in fails:
            print("FAIL:", f[0])
        for w in warns:
            print("WARN:", w[0])
        return 1 if (fails and a.strict) else 0

    if a.command == "render":
        r = core.render(a.case, a.out, info)
        print(json.dumps(r, ensure_ascii=False))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
