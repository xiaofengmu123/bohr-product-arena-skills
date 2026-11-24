#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文献综述合成 Skill 的命令行入口（薄封装，逻辑在 core_impl.py）。

用法：
  python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py render   --work /app/work --output /app/output
  python3 -B scripts/main.py check    --work /app/work --output /app/output
  python3 -B scripts/main.py extract  --pdf <in.pdf> --out <out.txt>

--input/--output/--work 缺省分别指向 /input、/app/output、/app/work。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core  # noqa: E402


def add_common(sp):
    sp.add_argument("--input", default="/input")
    sp.add_argument("--output", default="/app/output")
    sp.add_argument("--work", default="/app/work")


def main(argv=None):
    p = argparse.ArgumentParser(prog="main.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("scaffold", help="Phase 0：落盘 IR 骨架 + partial result.json")
    add_common(sp)

    sp = sub.add_parser("render", help="由 IR 片段渲染全部产物与 result.json")
    sp.add_argument("--output", default="/app/output")
    sp.add_argument("--work", default="/app/work")

    sp = sub.add_parser("check", help="只读自检，退出码 0=通过 / 1=有问题")
    sp.add_argument("--output", default="/app/output")
    sp.add_argument("--work", default="/app/work")

    sp = sub.add_parser("extract", help="单篇 PDF 重抽取")
    sp.add_argument("--pdf", required=True)
    sp.add_argument("--out", required=True)

    args = p.parse_args(argv)

    if args.cmd == "scaffold":
        result, problems = core.scaffold(args.input, args.output, args.work)
        core.log("scaffold 完成: status=%s, case_id=%s, 问题=%d"
                 % (result.get("status"), result.get("case_id"), len(problems)))
        for msg in problems:
            core.log("  [warn] " + msg)
        return 0

    if args.cmd == "render":
        result, problems = core.render_all(args.work, args.output)
        core.log("render 完成: status=%s, 问题=%d" % (result.get("status"), len(problems)))
        for msg in problems:
            core.log("  [warn] " + msg)
        return 0

    if args.cmd == "check":
        problems = core.check(args.work, args.output)
        if problems:
            core.log("check 失败：%d 个问题" % len(problems))
            for msg in problems:
                core.log("  [fail] " + msg)
            return 1
        core.log("check 通过：无问题")
        return 0

    if args.cmd == "extract":
        ok, msg = core.extract_pdf(args.pdf, args.out)
        if ok:
            core.log("extract 完成: " + args.out)
            return 0
        core.log("extract 失败: " + msg)
        return 1

    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
