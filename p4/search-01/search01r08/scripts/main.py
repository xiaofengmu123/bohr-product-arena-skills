#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学术文献检索 Skill 主入口（纯标准库）。

子命令：
  run            骨架流水线：skeleton -> parse -> plan -> assemble（不检索，落盘 partial）
  parse          解析 /input -> /app/work/case_summary.json
  plan-update    增量更新 plan.json（aspects/queries 等）
  import         导入候选文献 -> /app/work/records.json（按 DOI/规范化标题 upsert）
  evidence       追加单条查询/来源证据 -> /app/work/evidence.json
  import-evidence 批量导入证据
  assemble       去重+打分+排序，写 /app/output/result.json 与 candidates.md
  selfcheck      校验 result.json 是否满足输出契约与自检维度
"""
import argparse
import json
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import core_impl as core


def _parse_args(argv):
    p = argparse.ArgumentParser(
        prog="scripts/main.py",
        description="学术文献检索 Skill（确定性辅助脚本，纯标准库）",
    )
    p.add_argument("--version", action="version",
                   version="%(prog)s " + core.SKILL_VERSION)
    sub = p.add_subparsers(dest="command")

    def common(sp):
        sp.add_argument("--input", default=core.DEFAULT_INPUT)
        sp.add_argument("--output", default=core.DEFAULT_OUTPUT)
        sp.add_argument("--work", default=core.DEFAULT_WORK)
        return sp

    common(sub.add_parser("run", help="骨架流水线"))
    common(sub.add_parser("parse", help="解析输入"))
    sp_pu = sub.add_parser("plan-update", help="更新查询计划")
    sp_pu.add_argument("--inline", required=True, help="JSON 对象字符串")
    sp_pu.add_argument("--work", default=core.DEFAULT_WORK)
    sp_im = sub.add_parser("import", help="导入候选文献")
    sp_im.add_argument("--file", required=True)
    sp_im.add_argument("--work", default=core.DEFAULT_WORK)
    sp_ev = sub.add_parser("evidence", help="追加单条证据")
    sp_ev.add_argument("--inline", required=True, help="JSON 对象字符串")
    sp_ev.add_argument("--work", default=core.DEFAULT_WORK)
    sp_ie = sub.add_parser("import-evidence", help="批量导入证据")
    sp_ie.add_argument("--file", required=True)
    sp_ie.add_argument("--work", default=core.DEFAULT_WORK)
    common(sub.add_parser("assemble", help="组装最终结果"))
    sp_sc = common(sub.add_parser("selfcheck", help="结果自检"))
    return p, p.parse_args(argv)


def _parse_inline(raw):
    try:
        return json.loads(raw), []
    except Exception as e:
        return {}, ["inline JSON 解析失败: " + str(e)[:120]]


def main(argv=None):
    p, args = _parse_args(argv if argv is not None else sys.argv[1:])
    cmd = args.command or "run"

    report = {}
    try:
        if cmd == "run":
            core.run_pipeline(args.input, args.output, args.work)
            report = {"status": "ok", "hint": "骨架已落盘；用 import/evidence 增量补充后 assemble"}
        elif cmd == "parse":
            summary = core.parse_case(args.input, args.work)
            report = {"status": "ok", "case_id": summary.get("case_id"),
                      "research_question": summary.get("research_question")}
        elif cmd == "plan-update":
            payload, errs = _parse_inline(args.inline)
            if errs:
                report = {"status": "error", "errors": errs}
            else:
                plan, perrs = core.update_plan_inline(payload, args.work)
                report = {"status": "ok", "aspects": len(plan.get("aspects") or []),
                          "queries": len(plan.get("queries") or []), "errors": perrs}
        elif cmd == "import":
            report = core.import_candidates(args.file, args.work)
            report["status"] = "ok"
        elif cmd == "evidence":
            payload, errs = _parse_inline(args.inline)
            if errs:
                report = {"status": "error", "errors": errs}
            else:
                report = core.add_evidence(payload, args.work)
                report["status"] = "ok"
        elif cmd == "import-evidence":
            report = core.import_evidence(args.file, args.work)
            report["status"] = "ok"
        elif cmd == "assemble":
            result = core.assemble(args.input, args.output, args.work)
            report = {"status": "ok", "result_status": result.get("status"),
                      "ranked": len(result.get("results") or [])}
        elif cmd == "selfcheck":
            report = core.selfcheck(args.input, args.output, args.work)
        else:
            p.print_help()
            return 0
    except Exception as e:  # noqa: BLE001 —— 任何异常都不允许进程崩溃
        report = {"status": "error", "exception": str(e)[:300]}
        print("main error:", str(e)[:300], file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=False, default=str))
    if cmd == "selfcheck":
        return 1 if (isinstance(report, dict) and report.get("all_pass") is False) else 0
    return 1 if report.get("status") == "error" else 0


if __name__ == "__main__":
    sys.exit(main())