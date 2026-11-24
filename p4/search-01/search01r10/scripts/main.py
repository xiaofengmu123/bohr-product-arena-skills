#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""source-bound literature search：薄 CLI 入口（逻辑均在 core_impl）。

子命令：
  run         骨架流水线：skeleton -> parse -> plan -> assemble（不含外部检索）
  skeleton    初始化并落盘一版合法 partial 账本骨架
  parse       解析 /input -> 更新账本 case 信息
  plan        由研究问题生成查询规划（可用 --plan 传入人工规划）
  import      把候选记录导入账本（--file 或 --inline 均可；不超过 2MB）
  evidence    追加一条查询/来源证据（--inline JSON）
  assemble    去重+打分+排序，写 /app/output/result.json 与 candidates.md
  selfcheck   校验 /app/output/result.json 是否满足输出契约

检索动作（paper_search / web_search）由调用方 Agent 执行，本脚本只做确定性工序。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core  # noqa: E402

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"


def _load_inline_or_file(args):
    if args.inline:
        try:
            return json.loads(args.inline)
        except Exception as exc:  # noqa: BLE001
            print("inline JSON 解析失败:", str(exc)[:160], file=sys.stderr)
            return None
    if args.file:
        payload, deg = core.read_json(args.file, None)
        if deg:
            print("读取输入文件降级:", "; ".join(deg), file=sys.stderr)
        return payload
    return None


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=DEFAULT_INPUT)
    common.add_argument("--output", default=DEFAULT_OUTPUT)
    common.add_argument("--work", default=DEFAULT_WORK)

    p = argparse.ArgumentParser(prog="main.py", parents=[common],
                                description="source-bound literature search skill")

    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("run", parents=[common],
                   help="骨架流水线：skeleton->parse->plan->assemble")
    sub.add_parser("skeleton", parents=[common], help="只落盘合法 partial 骨架")
    sub.add_parser("parse", parents=[common], help="解析输入并写入账本")
    sp = sub.add_parser("plan", parents=[common], help="生成查询规划")
    sp.add_argument("--plan", dest="plan_json", default=None)

    im = sub.add_parser("import", parents=[common], help="导入候选记录")
    im.add_argument("--file", default=None)
    im.add_argument("--inline", default=None)

    ev = sub.add_parser("evidence", parents=[common], help="追加证据")
    ev.add_argument("--inline", required=True)
    ev.add_argument("--kind", default="search")

    nt = sub.add_parser("note", parents=[common],
                        help="补充状态说明/局限/覆盖缺口/待人工确认")
    nt.add_argument("--inline", required=True)

    sub.add_parser("assemble", parents=[common],
                   help="组装并写出 result.json/candidates.md")
    sub.add_parser("selfcheck", parents=[common], help="校验 result.json")

    args = p.parse_args(argv)

    work_dir = args.work
    core.ensure_dir(work_dir)
    ledger = core.load_ledger(work_dir)

    if args.cmd == "skeleton":
        core.step_skeleton(ledger, work_dir)
        print("skeleton 完成 ->", os.path.join(work_dir, "ledger.json"))
        return 0

    if args.cmd == "parse":
        core.step_parse(ledger, args.input, work_dir)
        print("parse 完成 case_id=", ledger.get("case_id"))
        return 0

    if args.cmd == "plan":
        payload = None
        if args.plan_json:
            try:
                payload = json.loads(args.plan_json)
            except Exception as exc:  # noqa: BLE001
                print("--plan JSON 解析失败:", str(exc)[:160], file=sys.stderr)
                return 2
        core.step_plan(ledger, work_dir, payload)
        print("plan 完成 queries=", len((ledger.get("plan") or {}).get(
            "query_candidates", [])))
        return 0

    if args.cmd == "import":
        payload = _load_inline_or_file(args)
        if payload is None:
            print("import 需要 --file 或 --inline", file=sys.stderr)
            return 2
        ledger, added, merged = core.step_import(ledger, work_dir, payload)
        print("import 完成 新增=%d 合并=%d 总候选=%d" %
              (added, merged, len(ledger["candidates"])))
        return 0

    if args.cmd == "evidence":
        obj = None
        try:
            obj = json.loads(args.inline)
        except Exception as exc:  # noqa: BLE001
            print("--inline JSON 解析失败:", str(exc)[:160], file=sys.stderr)
            return 2
        if args.kind and isinstance(obj, dict):
            obj.setdefault("kind", args.kind)
        core.step_evidence(ledger, work_dir, obj)
        print("evidence 完成 共", len(ledger["evidence"]), "条")
        return 0

    if args.cmd == "note":
        obj = None
        try:
            obj = json.loads(args.inline)
        except Exception as exc:  # noqa: BLE001
            print("--inline JSON 解析失败:", str(exc)[:160], file=sys.stderr)
            return 2
        core.step_note(ledger, work_dir, obj)
        print("note 完成")
        return 0

    if args.cmd == "assemble":
        result = core.step_assemble(ledger, work_dir, args.output)
        print("assemble 完成 status=", result.get("status"),
              " results=", len(result.get("results") or []))
        return 0

    if args.cmd == "selfcheck":
        return core.selfcheck(args.output)

    if args.cmd == "run":
        core.step_skeleton(ledger, work_dir)
        core.step_parse(ledger, args.input, work_dir)
        core.step_plan(ledger, work_dir, None)
        result = core.step_assemble(ledger, work_dir, args.output)
        print("run 完成 status=", result.get("status"),
              " results=", len(result.get("results") or []))
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())