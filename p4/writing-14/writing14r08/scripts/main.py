#!/usr/bin/env python3
"""开题报告与研究计划书 Skill 入口：init / check / render。

仅依赖 Python 标准库；路径与命令均为相对 Skill 根目录的可重复调用。
"""
import argparse
import json
import os
import sys

from core_impl import check, init_skeleton, render

DEFAULT_CASE = "/input/case.json"
DEFAULT_OUT = "/app/output/result.json"


def cmd_init(a):
    result, contract, err = init_skeleton(a.case, a.out)
    print(
        json.dumps(
            {
                "ok": True,
                "case_id": result.get("case_id"),
                "status": result.get("status"),
                "contract_fields": contract,
                "read_warning": err,
            },
            ensure_ascii=False,
        )
    )
    return 0


def cmd_check(a):
    fails, warns, _ = check(a.case, a.out)
    report = {"fail": len(fails), "warn": len(warns), "fails": fails, "warns": warns}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not fails else 1


def cmd_render(a):
    ok, items = render(a.out, a.outdir or os.path.dirname(os.path.abspath(a.out)))
    print(json.dumps({"ok": ok, "files": items}, ensure_ascii=False))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(prog="main.py", description="开题报告 Skill：init/check/render")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="落盘 partial 骨架 result.json")
    p.add_argument("--case", default=DEFAULT_CASE)
    p.add_argument("--out", default=DEFAULT_OUT)
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("check", help="校验契约/七要素/可行性，FAIL=0 退出码为 0")
    p.add_argument("--case", default=DEFAULT_CASE)
    p.add_argument("--out", default=DEFAULT_OUT)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("render", help="生成 proposal.md/.mmd/.svg 并回写 artifacts")
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--outdir", default=None, help="产物目录，缺省取 result.json 所在目录")
    p.set_defaults(func=cmd_render)

    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
