#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 学术论文写作 Skill 的统一入口（纯标准库）。

用法（在 Skill 根目录 /skills/weekly-skill 下用相对路径调用）：

  python3 scripts/main.py init      --input /input --output /app/output --work /app/work
  python3 scripts/main.py evidence  --draft /app/output/manuscript.md \
                                    --index /app/work/material_index.json --work /app/work
  python3 scripts/main.py check     --draft /app/output/manuscript.md \
                                    --index /app/work/material_index.json \
                                    --result /app/output/result.json --work /app/work
  python3 scripts/main.py selfcheck
  python3 scripts/main.py --help

各子命令的含义与在 SKILL.md 执行流程中的位置见 SKILL.md 与 references/。
本脚本不生成科研内容，只落盘骨架、校验证据与一致性、跑自检。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl  # noqa: E402


def _p(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="main.py",
        description="学术论文写作 Skill：骨架落盘 / 证据溯源校验 / 一致性检查 / 自检",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="前 3 分钟必做：落盘 result.json 骨架 + 材料索引")
    p_init.add_argument("--input", default="/input")
    p_init.add_argument("--output", default="/app/output")
    p_init.add_argument("--work", default="/app/work")

    p_ev = sub.add_parser("evidence", help="校验 [S:]/[C:] 标记与材料索引（locator+quote）")
    p_ev.add_argument("--draft", required=True)
    p_ev.add_argument("--index", required=True)
    p_ev.add_argument("--work", default="/app/work")

    p_chk = sub.add_parser("check", help="章节/图表/引用/待核标记一致性检查")
    p_chk.add_argument("--draft", required=True)
    p_chk.add_argument("--index", default=None)
    p_chk.add_argument("--result", default=None)
    p_chk.add_argument("--work", default="/app/work")

    p_merge = sub.add_parser("merge", help="Step 4：合并 evidence/check 报告到 result.json")
    p_merge.add_argument("--result", required=True)
    p_merge.add_argument("--work", default="/app/work")

    sub.add_parser("selfcheck", help="最小夹具自检，退出码 0 表示通过")

    args = ap.parse_args(argv)

    if args.cmd == "init":
        _p(core_impl.bootstrap_result(args.input, args.output, args.work))
    elif args.cmd == "evidence":
        _p(core_impl.validate_evidence(args.draft, args.index, args.work))
    elif args.cmd == "check":
        _p(core_impl.check_consistency(args.draft, args.index, args.work, result_path=args.result))
    elif args.cmd == "merge":
        _p(core_impl.merge_reports(args.result, args.work))
    elif args.cmd == "selfcheck":
        rep = core_impl.selfcheck()
        _p(rep)
        return 0 if rep.get("ok") else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
