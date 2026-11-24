#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学术文献检索 Skill 命令行入口（纯标准库）。

子命令（相对路径调用，先 cd 到 Skill 根目录）：
  run        骨架流水线：parse -> plan -> assemble（不检索，先落盘 partial）
  parse      解析 /input -> /app/work/case_summary.json
  plan       由 case_summary 生成查询计划 -> /app/work/plan.json
  import     导入候选文献 -> /app/work/records.json（按 DOI/规范化标题 upsert）
  evidence   追加查询/来源证据 -> /app/work/evidence.json
  assemble   去重+打分+排序，写 /app/output/result.json 与 candidates.md
  selfcheck  校验 result.json 是否满足输出契约
  manifest   生成中间产物内容指纹 -> /app/output/manifest.json
"""
import argparse
import json
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import core_impl as core

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=DEFAULT_INPUT, help="输入目录（默认 /input）")
    common.add_argument("--output", default=DEFAULT_OUTPUT, help="输出目录（默认 /app/output）")
    common.add_argument("--work", default=DEFAULT_WORK, help="工作目录（默认 /app/work）")

    p = argparse.ArgumentParser(
        prog="main.py",
        description="学术文献检索 Skill：可复核检索流水线（纯标准库）。",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp_run = sub.add_parser("run", parents=[common], help="骨架流水线：parse->plan->assemble")
    sp_parse = sub.add_parser("parse", parents=[common], help="解析输入 -> case_summary.json")
    sp_plan = sub.add_parser("plan", parents=[common], help="生成查询计划 -> plan.json")

    sp_import = sub.add_parser("import", parents=[common], help="导入候选文献 -> records.json")
    sp_import.add_argument("--file", help="候选记录 JSON 文件")
    sp_import.add_argument("--inline", help="内联 JSON（单条或数组）")
    sp_import.add_argument("--format", choices=["auto", "records", "crossref", "europepmc", "generic"],
                           default="auto", help="归一化格式提示")
    sp_import.add_argument("--database", help="来源数据库标签")
    sp_import.add_argument("--tool", default="import", help="产生记录的工具")
    sp_import.add_argument("--query-id", help="关联查询 ID")
    sp_import.add_argument("--label", default="manual", help="导入批次标签")

    sp_ev = sub.add_parser("evidence", parents=[common], help="追加证据 -> evidence.json")
    sp_ev.add_argument("--inline", required=True, help="内联 JSON 证据对象")
    sp_ev.add_argument("--file", help="从 JSON 文件读取证据")

    sub.add_parser("assemble", parents=[common], help="组装 result.json + candidates.md")
    sub.add_parser("selfcheck", parents=[common], help="自检 result.json")
    sub.add_parser("manifest", parents=[common], help="生成 manifest.json")

    args = p.parse_args(argv)

    core.ensure_dir(args.work)
    core.ensure_dir(args.output)

    if args.cmd == "run":
        result = core.run_skeleton(args.input, args.output, args.work)
        print("status:", result.get("status"), "| case_id:", result.get("case_id"),
              "| results:", len(result.get("results", [])))
    elif args.cmd == "parse":
        summary = core.parse_case(args.input, args.work)
        print("case_id:", summary.get("case_id"), "| rq:", summary.get("research_question"))
    elif args.cmd == "plan":
        summary, _ = core.read_json(os.path.join(args.work, "case_summary.json"), {})
        plan = core.build_plan(summary)
        core.write_json(os.path.join(args.work, "plan.json"), plan)
        print("plan_id:", plan.get("plan_id"), "| steps:", len(plan.get("query_steps", [])))
    elif args.cmd == "import":
        payload = _load_import_payload(args)
        records = core.unwrap_batch(payload)
        st = core.import_records(args.work, records, args.label,
                                 database=args.database, tool=args.tool,
                                 query_id=args.query_id)
        print("imported:", st["imported"], "updated:", st["updated"],
              "skipped:", st["skipped"], "total:", st["total"])
    elif args.cmd == "evidence":
        ev = _load_evidence(args)
        rec = core.append_evidence(args.work, ev)
        print("evidence_id:", rec.get("evidence_id"), "| kind:", rec.get("kind"))
    elif args.cmd == "assemble":
        result = core.assemble(args.input, args.output, args.work)
        print("status:", result.get("status"), "| results:", len(result.get("results", [])))
        core.write_manifest(args.work, args.output)
    elif args.cmd == "selfcheck":
        summary = core.selfcheck(args.output, args.work)
        print("selfcheck:", summary["conclusion"], f"({summary['passed']}/{summary['total']})")
        for c in summary["checks"]:
            if not c["pass"]:
                print("  FAIL:", c["check"], "-", c["detail"])
    elif args.cmd == "manifest":
        m = core.write_manifest(args.work, args.output)
        print("manifest_id:", m.get("manifest_id"), "| entries:", len(m.get("entries", [])))
    return 0


def _load_import_payload(args):
    if args.inline:
        try:
            return json.loads(args.inline)
        except Exception as e:
            print("inline JSON 解析失败:", str(e)[:160], file=sys.stderr)
            return []
    if args.file:
        obj, deg = core.read_json(args.file, [])
        if deg:
            print("import 文件读取降级:", "; ".join(deg), file=sys.stderr)
        return obj if isinstance(obj, (list, dict)) else []
    return []


def _load_evidence(args):
    if args.file:
        obj, deg = core.read_json(args.file, {})
        if deg:
            print("evidence 文件读取降级:", "; ".join(deg), file=sys.stderr)
        return obj if isinstance(obj, dict) else {}
    try:
        obj = json.loads(args.inline)
        return obj if isinstance(obj, dict) else {}
    except Exception as e:
        print("inline JSON 解析失败:", str(e)[:160], file=sys.stderr)
        return {"kind": "source", "rationale": "inline 解析失败: " + str(e)[:100]}


if __name__ == "__main__":
    sys.exit(main())
