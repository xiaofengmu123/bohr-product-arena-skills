#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学术文献检索 Skill 主入口（纯标准库）。

子命令：
  run        早期落盘流水线：skeleton -> parse -> plan -> assemble（不做检索）
  skeleton   写一版合法的 partial/insufficient_evidence 骨架 result.json
  parse      解析 /input -> /app/work/case_summary.json
  plan       由 case_summary 生成多轮查询计划 -> /app/work/plan.json
  import     导入候选文献（任意键名）-> /app/work/records.json（DOI/标题 upsert）
  evidence   追加一次查询/来源证据 -> /app/work/evidence.json
  ledger     登记一次模型请求并检查 64 次预算 -> /app/work/budget.json
  assemble   去重+透明打分+确定性排序，写 /app/output/result.json 与 candidates.md
  selfcheck  校验 result.json 是否满足输出契约，逐项输出 PASS/FAIL

设计要点：run 在启动后立即落盘一版合法 result.json（骨架），此后每个阶段通过
assemble 重读工作区并重写同一份 result.json，实现「任何时刻中断，产物都合法存在、
且始终反映当前已收录内容」；脚本只做确定性辅助，不消耗模型请求。
"""
import argparse
import json
import os
import re
import sys
import time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import core_impl as core

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"


def _add_common(sp):
    """把 --input/--output/--work 挂到每个子命令上（argparse 要求子命令选项在后）。"""
    sp.add_argument("--input", default=DEFAULT_INPUT, help="原始输入目录（默认 /input）")
    sp.add_argument("--output", default=DEFAULT_OUTPUT, help="最终产物目录（默认 /app/output）")
    sp.add_argument("--work", default=DEFAULT_WORK, help="工作区目录（默认 /app/work）")


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="学术文献检索 Skill：规划/召回/去重/打分/证据溯源/可复核交付。")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("run", help="早期落盘流水线：skeleton->parse->plan->assemble")
    _add_common(sp)
    sp = sub.add_parser("skeleton", help="仅写最小合法骨架 result.json")
    _add_common(sp)
    sp = sub.add_parser("parse", help="解析 /input -> case_summary.json")
    _add_common(sp)
    sp = sub.add_parser("plan", help="生成多轮查询计划 plan.json")
    _add_common(sp)
    imp = sub.add_parser("import", help="导入候选文献")
    _add_common(imp)
    imp.add_argument("--file", required=True, help="候选记录 JSON 文件路径")
    ev = sub.add_parser("evidence", help="追加查询/来源证据")
    _add_common(ev)
    ev.add_argument("--inline", required=True, help="单条证据 JSON 字符串")
    ld = sub.add_parser("ledger", help="登记一次模型请求")
    _add_common(ld)
    ld.add_argument("--inline", required=True, help="请求台账 JSON 字符串")
    sp = sub.add_parser("assemble", help="重写 result.json + candidates.md")
    _add_common(sp)
    sp = sub.add_parser("selfcheck", help="校验 result.json")
    _add_common(sp)
    return p


def _parse_inline(s):
    try:
        obj = json.loads(s)
    except Exception as e:
        print("inline JSON 解析失败: %s" % str(e)[:160], file=sys.stderr)
        sys.exit(2)
    return obj


def make_skeleton(input_dir, work_dir):
    summary, degradations = core.parse_case(input_dir)
    core.write_json(os.path.join(work_dir, "case_summary.json"), summary)
    core.load_budget(work_dir)
    result = core.assemble_result(input_dir, work_dir)
    core.append_change_log(work_dir, "skeleton", "早期落盘最小合法骨架",
                           "partial")
    return result


def make_plan(input_dir, work_dir):
    summary, _ = core.parse_case(input_dir)
    q = summary.get("research_question") or ""
    plan = {
        "research_question": q,
        "generated": core.now_iso(),
        "stages": _build_stages(q),
        "keyword_checklist": {
            "synonyms": [], "abbreviations": [], "translations": [],
            "related_concepts": [], "boolean_combinations": [],
        },
    }
    core.write_json(os.path.join(work_dir, "plan.json"), plan)
    core.append_change_log(work_dir, "plan", "生成多轮查询规划与关键词扩展核验清单",
                           "partial")
    return plan


def _build_stages(q):
    stages = []
    concepts = [c.strip() for c in re.split(r"[，,;；、\n]", q)
                if c.strip()]
    if concepts:
        stages.append({"round": 1, "type": "single_concept",
                       "queries": [c[:120] for c in concepts[:5]],
                       "goal": "单概念召回，确认每个核心词的基础命中"})
        stages.append({"round": 2, "type": "boolean_and",
                       "queries": [" + ".join(concepts[:4])],
                       "goal": "AND 组合收紧，提高 Precision"})
        stages.append({"round": 3, "type": "synonym_expansion",
                       "queries": [],
                       "goal": "同义词/缩写/跨语言表达扩展，提高 Recall"})
        stages.append({"round": 4, "type": "citation_chase",
                       "queries": [],
                       "goal": "高相关记录的引用与被引追踪"})
    else:
        stages.append({"round": 1, "type": "pending",
                       "queries": [],
                       "goal": "研究问题缺失，待人工确认后再构造查询"})
    return stages


def cmd_run(args):
    t0 = time.time()
    core.ensure_dir(args.work)
    core.ensure_dir(args.output)
    result = make_skeleton(args.input, args.work)
    make_plan(args.input, args.work)
    result = core.assemble_result(args.input, args.work)
    core.write_json(os.path.join(args.output, "result.json"), result)
    core.write_text(os.path.join(args.output, "candidates.md"),
                    core.render_candidates_md(result))
    core.append_change_log(args.work, "run", "骨架流水线完成（未检索）", result["status"])
    print("run ok status=%s case=%s elapsed=%.1fs" %
          (result["status"], result["case_id"], time.time() - t0))
    return 0


def cmd_skeleton(args):
    core.ensure_dir(args.work)
    core.ensure_dir(args.output)
    result = make_skeleton(args.input, args.work)
    core.write_json(os.path.join(args.output, "result.json"), result)
    core.write_text(os.path.join(args.output, "candidates.md"),
                    core.render_candidates_md(result))
    print("skeleton ok status=%s case=%s" % (result["status"], result["case_id"]))
    return 0


def cmd_parse(args):
    core.ensure_dir(args.work)
    summary, degradations = core.parse_case(args.input)
    core.write_json(os.path.join(args.work, "case_summary.json"), summary)
    print(json.dumps({"case_id": summary["case_id"],
                      "research_question": summary["research_question"][:120],
                      "degradations": degradations}, ensure_ascii=False))
    return 0


def cmd_plan(args):
    core.ensure_dir(args.work)
    plan = make_plan(args.input, args.work)
    print(json.dumps(plan, ensure_ascii=False)[:400])
    return 0


def cmd_import(args):
    core.ensure_dir(args.work)
    incoming, _ = core.read_json(args.file, [])
    if not isinstance(incoming, list):
        incoming = [incoming]
    records, log = core.import_records(args.work, incoming)
    core.append_change_log(args.work, "import",
                           "导入候选 %d 条，log=%s" % (len(log), ";".join(log[:8])),
                           "partial")
    print("import ok records=%d log=%s" % (len(records), ";".join(log[:8])))
    return 0


def cmd_evidence(args):
    core.ensure_dir(args.work)
    item = _parse_inline(args.inline)
    entries = core.append_evidence(args.work, item)
    core.append_change_log(args.work, "evidence",
                           "记录查询证据 source=%s" % item.get("source", "unknown"),
                           "partial")
    print("evidence ok total=%d" % len(entries))
    return 0


def cmd_ledger(args):
    core.ensure_dir(args.work)
    item = _parse_inline(args.inline)
    state = core.ledger_add_request(args.work, item)
    print("ledger %s" % state)
    return 0 if state != "STOP_BUDGET_EXHAUSTED" else 3


def cmd_assemble(args):
    core.ensure_dir(args.work)
    core.ensure_dir(args.output)
    result = core.assemble_result(args.input, args.work)
    core.write_json(os.path.join(args.output, "result.json"), result)
    core.write_text(os.path.join(args.output, "candidates.md"),
                    core.render_candidates_md(result))
    print("assemble ok status=%s results=%d" %
          (result["status"], len(result["results"])))
    return 0


def cmd_selfcheck(args):
    result, deg = core.read_json(os.path.join(args.output, "result.json"), {})
    checks, critical = core.selfcheck_result(result)
    print(core.checklist_text(checks))
    return 0 if not critical else 1


def main(argv=None):
    args = build_parser().parse_args(argv)
    return {
        "run": cmd_run,
        "skeleton": cmd_skeleton,
        "parse": cmd_parse,
        "plan": cmd_plan,
        "import": cmd_import,
        "evidence": cmd_evidence,
        "ledger": cmd_ledger,
        "assemble": cmd_assemble,
        "selfcheck": cmd_selfcheck,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
