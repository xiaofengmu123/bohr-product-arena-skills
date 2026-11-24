#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科研数据可视化 Skill CLI（仅标准库）。子命令 init/profile/check。

职责：
  init    发现输入、提取 output_contract、立即落盘 result.json 骨架（status=partial
          或 insufficient_evidence/input_error）与 work/plan.md；此后任何崩溃都有合法产物。
  profile 解析 CSV/SQLite，产出 work/profile.json（数据画像）与 work/ir.json（FigureIR
          骨架 + 选图推荐）。
  check   按 L1 数据契约 -> L2 IR schema -> L3 渲染产物 -> L4 result.json 顺序自检。

退出码约定：任何命令默认退出码为 0；仅 check --fail 且存在 FAIL 项时退出 1，
保证官方 Harness 不被中断。
"""
import argparse
import json
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ir


def _p(obj):
    print(json.dumps(obj, ensure_ascii=False))


def cmd_init(args):
    try:
        files, deg = ir.discover_input(args.input)
    except Exception as e:  # noqa: BLE001
        ir.write_minimal_result(args.output, "input_error", [str(e)])
        _p({"ok": True, "status": "input_error", "note": str(e)})
        return 0
    case = {}
    if files.get("case_json"):
        case, err = ir.load_json_robust(files["case_json"])
        if err:
            deg.append("case.json: %s" % err)
    contract = ir.extract_contract(case, files.get("task_md"))
    case_id = ir.build_case_id(case, args.input)
    if not os.path.isdir(args.input):
        status, note = "input_error", "输入目录不存在: %s" % args.input
    elif not files.get("case_json") and not files.get("data") and not files.get("materials"):
        status, note = "insufficient_evidence", "输入目录无 case.json 与数据材料"
    else:
        status, note = "partial", ""
    result = ir.build_result_skeleton(case_id, contract)
    result["status"] = status
    if deg:
        result["degradations"] = list(dict.fromkeys(result["degradations"] + deg))
    if note:
        result["errors"].append(note)
    try:
        os.makedirs(args.output, exist_ok=True)
        os.makedirs(args.work, exist_ok=True)
        ir.atomic_write_json(os.path.join(args.output, "result.json"), result)
        ir.atomic_write_text(os.path.join(args.work, "plan.md"), ir.build_plan(result))
    except Exception as e:  # noqa: BLE001
        ir.write_minimal_result(args.output, "input_error", ["init 写盘失败: %s" % e])
        _p({"ok": True, "status": "input_error", "note": str(e)})
        return 0
    _p({"ok": True, "case_id": case_id, "status": status,
        "output": os.path.join(args.output, "result.json")})
    return 0


def cmd_profile(args):
    try:
        files, deg = ir.discover_input(args.input)
        profile = ir.build_profile(files)
    except Exception as e:  # noqa: BLE001
        _p({"ok": False, "error": str(e)})
        return 0
    result, _err = ir.load_json_robust(os.path.join(args.output, "result.json"))
    case_id = (result or {}).get("case_id") or ir.build_case_id({}, args.input)
    ir_obj = ir.build_ir(case_id, profile)
    try:
        os.makedirs(args.work, exist_ok=True)
        ir.atomic_write_json(os.path.join(args.work, "profile.json"), profile)
        ir.atomic_write_json(os.path.join(args.work, "ir.json"), ir_obj)
    except Exception as e:  # noqa: BLE001
        _p({"ok": False, "error": str(e)})
        return 0
    _p({"ok": True, "tables": len(profile.get("tables", [])),
        "recommendations": len(ir_obj.get("recommendations", []))})
    return 0


def cmd_check(args):
    try:
        result, err = ir.load_json_robust(os.path.join(args.output, "result.json"))
        if err:
            result = {}
        report = ir.run_checks(result, args.output, args.work,
                               require_contract=args.require_contract)
        ir.print_report(report)
        failed = any(c["status"] == "FAIL" for c in report["checks"])
        if args.fail:
            return 1 if failed else 0
        return 0
    except Exception as e:  # noqa: BLE001
        print("check error: %s" % e, file=sys.stderr)
        return 1 if args.fail else 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="main.py",
                                description="科研数据可视化 Skill CLI（init/profile/check）")
    sub = p.add_subparsers(dest="cmd")
    for name in ("init", "profile", "check"):
        sp = sub.add_parser(name)
        sp.add_argument("--input", default="/input")
        sp.add_argument("--output", default="/app/output")
        sp.add_argument("--work", default="/app/work")
        sp.add_argument("--fail", action="store_true",
                        help="仅 check：存在 FAIL 项时退出码 1")
        sp.add_argument("--require-contract", action="store_true",
                        help="仅 check：额外要求 sources/artifacts 非空")
        sp.set_defaults(func=globals().get("cmd_" + name))
    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        p.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
