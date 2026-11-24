# -*- coding: utf-8 -*-
"""main.py — 论文逻辑与论证审查 Skill 入口（薄 CLI，逻辑全部在 reviewkit.py）。

模式：
  scaffold  发现输入、抽取/切分语料、落盘 result.json 骨架 + report.md 骨架 + work/corpus.md + work/ledger.json
  merge     读 work/ledger.json（审计台账），规范化、交叉引用校验后合并进 result.json，重建 report.md
  selfcheck 校验 result.json / report.md 合法性

调用示例（路径相对本 SKILL.md）：
  python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py merge    --output /app/output --work /app/work
  python3 -B scripts/main.py selfcheck --output /app/output
"""
from __future__ import annotations

import argparse
import os
import sys

try:
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import reviewkit as rk


def _argparse() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="论文逻辑与论证审查 Skill 脚本")
    sub = p.add_subparsers(dest="mode", required=True)

    s1 = sub.add_parser("scaffold", help="抽取语料并落盘结果骨架")
    s1.add_argument("--input", default="/input")
    s1.add_argument("--output", default="/app/output")
    s1.add_argument("--work", default="/app/work")

    s2 = sub.add_parser("merge", help="把审计台账合并进 result.json")
    s2.add_argument("--output", default="/app/output")
    s2.add_argument("--work", default="/app/work")
    s2.add_argument("--ledger", default=None, help="审计台账路径，默认 work/ledger.json")

    s3 = sub.add_parser("selfcheck", help="校验 result.json 与 report.md")
    s3.add_argument("--output", default="/app/output")
    return p


def _scaffold(args) -> int:
    try:
        records, sources, task_meta, case_id, contract = rk.build_corpus(args.input)
    except Exception as e:  # noqa: BLE001
        result = rk.minimal_result("unknown_case", "scaffold 失败: %s" % e)
        rk.write_json_atomic(os.path.join(args.output, "result.json"), result)
        rk.write_text_atomic(os.path.join(args.output, "report.md"), rk.render_report(result))
        rk.write_json_atomic(os.path.join(args.work, "ledger.json"), rk.empty_ledger())
        rk.write_text_atomic(os.path.join(args.work, "corpus.md"),
                             "# 语料库\n\n抽取失败：%s\n" % e)
        print("scaffold_error: %s" % e)
        return 2
    result = rk.make_result_skeleton(case_id, task_meta, contract, sources, records)
    rk.write_corpus_md(records, sources, os.path.join(args.work, "corpus.md"))
    rk.write_json_atomic(os.path.join(args.work, "ledger.json"), rk.empty_ledger())
    rk.write_json_atomic(os.path.join(args.output, "result.json"), result)
    rk.write_text_atomic(os.path.join(args.output, "report.md"), rk.render_report(result))
    print("scaffold ok: case_id=%s sources=%d paras=%d status=partial" % (
        case_id, len(sources), len(records)))
    return 0


def _merge(args) -> int:
    ledger_path = args.ledger or os.path.join(args.work, "ledger.json")
    result_path = os.path.join(args.output, "result.json")
    result, rerr = rk.read_json_tolerant(result_path)
    if rerr or not isinstance(result, dict) or not result.get("case_id"):
        result = rk.minimal_result("unknown_case", "merge 前 result.json 缺失或损坏")
    ledger, lerr = rk.read_json_tolerant(ledger_path)
    if lerr:
        rk.add_degradation(result, "ledger 解析失败: %s" % lerr)
        rk.write_json_atomic(result_path, result)
        rk.write_text_atomic(os.path.join(args.output, "report.md"), rk.render_report(result))
        print("merge_error: ledger 解析失败: %s" % lerr)
        return 2
    result = rk.merge(result, ledger)
    issues = rk.validate_result(result)
    rk.write_json_atomic(result_path, result)
    rk.write_text_atomic(os.path.join(args.output, "report.md"), rk.render_report(result))
    print("merge ok: status=%s findings=%d claims=%d issues=%d" % (
        result["status"], len(result["audit"]["findings"]),
        len(result["audit"]["claims"]), len(issues)))
    for i in issues:
        print("  [%s] %s" % (i.get("level", "warning"), i.get("msg", "")))
    return 0


def _selfcheck(args) -> int:
    result_path = os.path.join(args.output, "result.json")
    result, rerr = rk.read_json_tolerant(result_path)
    if rerr:
        print("selfcheck FAIL: result.json 无法解析: %s" % rerr)
        return 1
    issues = rk.validate_result(result)
    report_path = os.path.join(args.output, "report.md")
    if not os.path.isfile(report_path):
        issues.append({"level": "critical", "msg": "report.md 缺失"})
    crit = [i for i in issues if i.get("level") == "critical"]
    if crit:
        print("selfcheck FAIL (%d critical, %d warning)" % (len(crit), len(issues) - len(crit)))
        for i in issues:
            print("  [%s] %s" % (i.get("level", "warning"), i.get("msg", "")))
        return 1
    print("selfcheck OK (warnings=%d)" % len(issues))
    for i in issues:
        print("  [warning] %s" % i.get("msg", ""))
    return 0


def main() -> int:
    args = _argparse().parse_args()
    if args.mode == "scaffold":
        return _scaffold(args)
    if args.mode == "merge":
        return _merge(args)
    if args.mode == "selfcheck":
        return _selfcheck(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
