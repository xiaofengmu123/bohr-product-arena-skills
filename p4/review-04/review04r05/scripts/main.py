# -*- coding: utf-8 -*-
"""main.py — 论文逻辑与论证审查 Skill 的命令行入口（纯标准库）。

子命令：
  scaffold  发现输入、抽取语料、回填 output_contract，落盘合法 partial 骨架
  update    把 Agent 产出的 findings/claims/summary 增量合并进 result.json
  selfcheck 校验 result.json 与 report.md 的合法性（对齐评分维度）
  selftest  在临时目录跑健壮性与确定性自测（空输入/坏 JSON/坏编码/嵌套契约）

调用示例（路径相对 SKILL.md 所在目录）：
  python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py update   --output /app/output --findings /app/work/findings.json \\
      --claims /app/work/claims.json --summary "总评" --status partial
  python3 -B scripts/main.py selfcheck --output /app/output
  python3 -B scripts/main.py selftest
"""
from __future__ import annotations

import argparse
import json
import sys

import audit

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py",
        description="论文逻辑与论证审查 Skill（输出契约完整版）")
    sub = p.add_subparsers(dest="mode", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--input", default="/input", help="输入目录")
        sp.add_argument("--output", default="/app/output", help="产物目录")
        sp.add_argument("--work", default="/app/work", help="中间工作目录")

    s1 = sub.add_parser("scaffold", help="落盘合法 partial 骨架")
    common(s1)
    s1.add_argument("--force", action="store_true", help="覆盖已存在的 result.json")

    s2 = sub.add_parser("update", help="增量合并 findings/claims/summary")
    s2.add_argument("--output", default="/app/output")
    s2.add_argument("--findings", default=None, help="findings JSON 数组文件")
    s2.add_argument("--claims", default=None, help="claims JSON 数组文件")
    s2.add_argument("--summary", default=None, help="总评文本或文件路径")
    s2.add_argument("--status", default=None, choices=audit.RESULT_STATUSES)

    s3 = sub.add_parser("selfcheck", help="校验产物合法性")
    s3.add_argument("--output", default="/app/output")

    sub.add_parser("selftest", help="健壮性与确定性自测")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode == "scaffold":
        res, err = audit.scaffold(args.input, args.output, args.work,
                                  force=getattr(args, "force", False))
        if err:
            print("WARN:", err)
        print("scaffold done: case_id=%s status=%s findings=%d" %
              (res.get("case_id"), res.get("status"),
               len(res.get("review", {}).get("findings", []))))
        return 0
    if args.mode == "update":
        res, err = audit.update(args.output, args.findings, args.claims,
                                args.summary, args.status)
        if err:
            print("ERROR:", err)
            return 0  # 不崩溃：错误已由调用方读取或已记录
        print("update done: status=%s findings=%d claims=%d" %
              (res.get("status"), len(res.get("review", {}).get("findings", [])),
               len(res.get("review", {}).get("claims", []))))
        return 0
    if args.mode == "selfcheck":
        return 0 if audit.selfcheck(args.output) else 1
    if args.mode == "selftest":
        out = audit.selftest()
        print(json.dumps(out, ensure_ascii=False, indent=2))
        ok = all(bool(v) for v in out["results"].values())
        print("SELFTEST", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
