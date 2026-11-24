#!/usr/bin/env python3
"""traceable-figure-skill 主入口（CLI）。核心实现见 core_impl.py。

子命令（相对路径调用，例如 `python scripts/main.py init ...`）：
  init      --case /input/case.json --work /app/work --out /app/output
            落盘 result.json 骨架（status=in_progress）。必须在 180 秒内执行。
  build     --ir /app/work/ir.json --svg /app/work/figure.svg [--out /app/output] [--render]
            确定性布局 -> 分层可编辑 SVG；可选尽力渲染 PNG/PDF 预览。
  check     --ir /app/work/ir.json --svg /app/work/figure.svg [--out /app/work/checks.json]
            结构自检，输出 checks.json。
  finalize  --work /app/work --out /app/output --status complete|partial [--render-json ...]
            把 IR / checks / 渲染结果合并进 result.json 并收尾。
  selftest  运行 scripts/selftest.py 的入口（等价调用）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from core_impl import (
    attempt_render, build_svg, finalize, init_workspace,
    read_ir, read_json, validate, write_checks, write_json,
)


def cmd_init(args):
    p = init_workspace(args.case, args.work, args.out)
    print(json.dumps({"ok": True, "result": p}, ensure_ascii=False))


def cmd_build(args):
    ir = read_ir(args.ir)
    if ir is None:
        print(json.dumps({"ok": False, "error": "ir.json 不可读或非法"}, ensure_ascii=False))
        return 1
    build_svg(ir, args.svg)
    render = None
    if args.render:
        render = attempt_render(args.svg, args.out or os.path.dirname(args.svg))
        if render is not None:
            write_json(os.path.join(os.path.dirname(args.svg), "render.json"), render)
    print(json.dumps({"ok": True, "svg": args.svg, "render": render}, ensure_ascii=False))
    return 0


def cmd_check(args):
    ir = read_ir(args.ir)
    if ir is None:
        print(json.dumps({"ok": False, "error": "ir.json 不可读或非法"}, ensure_ascii=False))
        return 1
    checks = validate(ir, args.svg)
    write_checks(checks, args.out or os.path.join(args.work or ".", "checks.json"))
    crit = sum(1 for c in checks if c["severity"] == "critical" and not c["passed"])
    print(json.dumps({"ok": crit == 0, "critical_failed": crit,
                      "total": len(checks)}, ensure_ascii=False))
    return 0 if crit == 0 else 1


def cmd_finalize(args):
    render = None
    if args.render_json and os.path.exists(args.render_json):
        render = read_json(args.render_json)
    p = finalize(args.work, args.out, args.status, render)
    print(json.dumps({"ok": True, "result": p}, ensure_ascii=False))
    return 0


def cmd_selftest(_args):
    here = os.path.dirname(os.path.abspath(__file__))
    selftest_path = os.path.join(here, "selftest.py")
    if os.path.exists(selftest_path):
        return subprocess.call([sys.executable, selftest_path])
    print(json.dumps({"ok": False, "error": "selftest.py 不存在"}, ensure_ascii=False))
    return 1


def build_parser():
    ap = argparse.ArgumentParser(prog="main.py", description="traceable-figure-skill 主入口")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    p.add_argument("--case", default="/input/case.json")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--out", default="/app/output")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("build")
    p.add_argument("--ir", default="/app/work/ir.json")
    p.add_argument("--svg", default="/app/work/figure.svg")
    p.add_argument("--out", default=None)
    p.add_argument("--render", action="store_true")
    p.set_defaults(fn=cmd_build)

    p = sub.add_parser("check")
    p.add_argument("--ir", default="/app/work/ir.json")
    p.add_argument("--svg", default="/app/work/figure.svg")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--out", default=None)
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("finalize")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--out", default="/app/output")
    p.add_argument("--status", default="partial", choices=["complete", "partial", "failed"])
    p.add_argument("--render-json", default=None)
    p.set_defaults(fn=cmd_finalize)

    p = sub.add_parser("selftest")
    p.set_defaults(fn=cmd_selftest)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
