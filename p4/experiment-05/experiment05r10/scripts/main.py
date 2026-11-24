#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 实验结果解释 Skill 的薄 CLI 分发层。

职责只做参数解析与分发，实现全部在 core_impl.py。
相对路径调用（在 Skill 根目录下执行）：

  python scripts/main.py skeleton --input /input --output /app/output
  python scripts/main.py profile  --input /input --work /app/work --output /app/output
  python scripts/main.py report   --input /app/output/result.json --output /app/output
  python scripts/main.py validate --input /app/output/result.json
  python scripts/main.py finalize --input /app/output/result.json
"""

import argparse
import sys

import core_impl as ci


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="main.py",
        description="实验结果解释 Skill：骨架落盘 / 事实画像 / 报告 / 校验 / 定稿",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("skeleton", help="读 case.json/task.md，落盘合法 result.json 骨架")
    sp.add_argument("--input", default=ci.DEFAULT_INPUT)
    sp.add_argument("--output", default=ci.DEFAULT_OUTPUT)

    pp = sub.add_parser("profile", help="生成 facts.json（文件清单+逐列统计+候选异常）")
    pp.add_argument("--input", default=ci.DEFAULT_INPUT)
    pp.add_argument("--work", default=ci.DEFAULT_WORK)
    pp.add_argument("--output", default=ci.DEFAULT_OUTPUT)

    rp = sub.add_parser("report", help="由 result.json 生成 report.md 与 evidence_map.json")
    rp.add_argument("--input", required=True)
    rp.add_argument("--output", default=ci.DEFAULT_OUTPUT)

    vp = sub.add_parser("validate", help="校验 schema/契约与证据完整性")
    vp.add_argument("--input", required=True)

    fp = sub.add_parser("finalize", help="写入 runtime 与 finalize 变更记录")
    fp.add_argument("--input", required=True)

    args = p.parse_args(argv)
    if args.cmd == "skeleton":
        ci.build_skeleton(args.input, args.output)
        print("[skeleton] ok: %s/result.json 与 report.md 已落盘"
              % args.output.rstrip("/\\"))
        return 0
    if args.cmd == "profile":
        facts = ci.profile_input(args.input, args.work, args.output)
        print("[profile] ok: %d 个文件画像已写入 %s/facts.json"
              % (facts["totals"]["n_files"], args.output.rstrip("/\\")))
        return 0
    if args.cmd == "report":
        if ci.build_report(args.input, args.output) is None:
            print("[report] FATAL: 无法解析 %s" % args.input)
            return 1
        print("[report] ok: report.md 与 evidence_map.json 已生成")
        return 0
    if args.cmd == "validate":
        return ci.validate_result(args.input)
    if args.cmd == "finalize":
        return ci.finalize_result(args.input)
    return 1


if __name__ == "__main__":
    sys.exit(main())
