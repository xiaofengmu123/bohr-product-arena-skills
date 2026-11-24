#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Command-line entry point for the research-proposal skill.

Usage (run from the SKILL root directory):

    python scripts/main.py init   --case /input/case.json --out /app/output/result.json
    python scripts/main.py check  --result /app/output/result.json --case /input/case.json --out /app/output
    python scripts/main.py build  --result /app/output/result.json --out /app/output
    python scripts/main.py selftest

All subcommands use only the Python standard library and resolve their own
directory from __file__, so they work from any working directory. The workflow
is: init (early partial skeleton) -> agent fills content -> check (FAIL=0) ->
build (editable artifacts) -> selftest.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core  # noqa: E402


def cmd_init(args):
    case = core.load_json(args.case, default={})
    result = core.init_result(case, source_path=args.case)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    core.write_json(args.out, result)
    contract = core.find_contract(case)
    print(json.dumps({
        "ok": True,
        "case_id": result["case_id"],
        "status": result["status"],
        "contract_fields": sorted(contract.keys()),
        "out": os.path.abspath(args.out),
    }, ensure_ascii=False))
    return 0


def cmd_check(args):
    if not os.path.exists(args.result):
        print(json.dumps({"ok": False, "error": "result.json 不存在: %s" % args.result},
                         ensure_ascii=False))
        return 2
    result = core.load_json(args.result, default={})
    case = core.load_json(args.case, default={}) if args.case else None
    fails, warns = core.check_result(result, output_dir=args.out, case=case)
    print(json.dumps({
        "ok": len(fails) == 0,
        "fails": len(fails),
        "warns": len(warns),
        "fail_items": fails,
        "warn_items": warns,
    }, ensure_ascii=False, indent=2))
    return 0 if len(fails) == 0 else 1


def cmd_build(args):
    if not os.path.exists(args.result):
        print(json.dumps({"ok": False, "error": "result.json 不存在: %s" % args.result},
                         ensure_ascii=False))
        return 2
    result = core.load_json(args.result, default={})
    os.makedirs(args.out, exist_ok=True)
    artifacts = core.build_artifacts(result, output_dir=args.out,
                                     work_dir=getattr(args, "work", None))
    core.write_json(args.result, result)
    print(json.dumps({
        "ok": True,
        "artifacts": [a if isinstance(a, str) else a.get("path") for a in artifacts],
        "out": os.path.abspath(args.out),
    }, ensure_ascii=False))
    return 0


def cmd_selftest(_args):
    import selftest
    return selftest.run()


def build_parser():
    ap = argparse.ArgumentParser(
        prog="scripts/main.py",
        description="开题报告与研究计划书 Skill 的运行脚本（init/check/build/selftest）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="读取 case.json 并写入合法 partial 骨架")
    p.add_argument("--case", default="/input/case.json")
    p.add_argument("--out", default="/app/output/result.json")

    p = sub.add_parser("check", help="校验 result.json 契约与七要素/四问/证据")
    p.add_argument("--result", default="/app/output/result.json")
    p.add_argument("--case", default=None, help="可选，用于校验 output_contract 字段")
    p.add_argument("--out", default="/app/output")

    p = sub.add_parser("build", help="渲染 proposal.md/evidence_map.md/checks.md/proposal.docx")
    p.add_argument("--result", default="/app/output/result.json")
    p.add_argument("--out", default="/app/output")
    p.add_argument("--work", default="/app/work")

    p = sub.add_parser("render", help="build 的别名")
    p.add_argument("--result", default="/app/output/result.json")
    p.add_argument("--out", default="/app/output")
    p.add_argument("--work", default="/app/work")

    p = sub.add_parser("selftest", help="对脚本自身做标准库自检")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "init":
        return cmd_init(args)
    if args.cmd == "check":
        return cmd_check(args)
    if args.cmd in ("build", "render"):
        return cmd_build(args)
    if args.cmd == "selftest":
        return cmd_selftest(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
