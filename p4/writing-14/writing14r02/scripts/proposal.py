#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""proposal Skill 入口：init / check / render。仅依赖标准库。

用法（在 SKILL.md 所在目录执行，统一相对路径）：
  python scripts/proposal.py init   --case /input/case.json --result /app/output/result.json
  python scripts/proposal.py check  --case /input/case.json --result /app/output/result.json
  python scripts/proposal.py render --result /app/output/result.json --out /app/output
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import core_impl as core


def main(argv=None):
    ap = argparse.ArgumentParser(prog="proposal.py", description="开题报告与研究计划书 Skill 引擎")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="早落盘 partial 骨架（前 3 分钟内先写一版）")
    p.add_argument("--case", default="/input/case.json")
    p.add_argument("--result", default="/app/output/result.json")

    p = sub.add_parser("check", help="校验 result.json：契约齐全 + 证据回溯 + 要素对齐 + 计划可行性")
    p.add_argument("--case", default="/input/case.json")
    p.add_argument("--result", default="/app/output/result.json")

    p = sub.add_parser("render", help="生成 proposal.md / evidence_map.md / schedule.mmd / preview.svg")
    p.add_argument("--result", default="/app/output/result.json")
    p.add_argument("--out", default="/app/output")

    args = ap.parse_args(argv)

    if args.cmd == "init":
        out_dir = os.path.dirname(args.result) or "."
        os.makedirs(out_dir, exist_ok=True)
        errs = []
        skeleton = core.build_skeleton(args.case, errs)
        with open(args.result, "w", encoding="utf-8") as f:
            json.dump(skeleton, f, ensure_ascii=False, indent=2)
        print("init ok; case_id=%s; fields=%d" % (skeleton.get("case_id"), len(skeleton)))
        for e in errs:
            print("WARN %s" % e, file=sys.stderr)
        return 0

    if args.cmd == "check":
        findings = core.validate(args.case, args.result)
        fails = 0
        for level, code, msg in findings:
            print("%s\t%s\t%s" % (level, code, msg))
            if level == "FAIL":
                fails += 1
        print("check done; FAIL=%d WARN=%d" % (fails, len(findings) - fails))
        return 0 if fails == 0 else 1

    if args.cmd == "render":
        arts = core.render(args.result, args.out)
        for name, path, kind in arts:
            print("artifact %s -> %s (%s)" % (name, path, kind))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
