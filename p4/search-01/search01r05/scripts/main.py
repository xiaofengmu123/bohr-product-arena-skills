#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学术文献检索 Skill 主入口与命令行（纯标准库）。

子命令：
  run        骨架流水线：skeleton -> parse -> plan -> assemble（不检索，
             前三分钟内落盘一版合法 partial 结果）。
  skeleton   仅写一版合法 partial/insufficient_evidence 骨架。
  parse      解析 /input -> /app/work/case_summary.json。
  plan       由 case_summary 生成查询计划 -> /app/work/plan.json。
  import     导入候选文献 -> /app/work/records.json（按 DOI/规范化标题 upsert）。
  evidence   追加查询/来源证据 -> /app/work/evidence.json。
  assemble   去重 + 打分 + 排序，写 /app/output/result.json 与 candidates.md。
  selfcheck  校验 result.json 是否满足输出契约，打印 PASS/FAIL。

所有实现位于 core.py；本文件只负责参数解析与子命令调度。
"""
import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import core as C  # noqa: E402


def _args(extra=True):
    p = argparse.ArgumentParser(
        prog="main.py",
        description="学术文献检索 Skill 主入口（契约优先 + 证据溯源 + 稳健降级）")
    p.add_argument("--input", default=C.DEFAULT_INPUT, help="输入目录（默认 /input）")
    p.add_argument("--output", default=C.DEFAULT_OUTPUT, help="输出目录（默认 /app/output）")
    p.add_argument("--work", default=C.DEFAULT_WORK, help="工作目录（默认 /app/work）")
    return p


def cmd_run(a):
    C.ensure_dir(a.output)
    C.ensure_dir(a.work)
    C.touch_run_start(a.work)
    C.skeleton(a.output, a.work)
    cs = C.parse_case(a.input)
    C.write_json(os.path.join(a.work, "case_summary.json"), cs)
    plan = C.build_query_plan(cs)
    C.write_json(os.path.join(a.work, "plan.json"), plan)
    result = C.assemble(a.input, a.output, a.work)
    print("run ok: case_id=%s status=%s results=%d contract_fields=%d" % (
        result.get("case_id"), result.get("status"),
        len(result.get("results", [])),
        len(result.get("contract_report", {}).get("fields", []))))
    return 0


def cmd_skeleton(a):
    C.ensure_dir(a.output)
    C.ensure_dir(a.work)
    C.touch_run_start(a.work)
    C.skeleton(a.output, a.work)
    print("skeleton ok")
    return 0


def cmd_parse(a):
    C.ensure_dir(a.work)
    cs = C.parse_case(a.input)
    C.write_json(os.path.join(a.work, "case_summary.json"), cs)
    print("parse ok: case_id=%s contract_fields=%d" % (
        cs.get("case_id"), len(cs.get("contract_spec") or [])))
    return 0


def cmd_plan(a):
    cs, _ = C.read_json(os.path.join(a.work, "case_summary.json"), {})
    if not cs:
        cs = C.parse_case(a.input)
        C.write_json(os.path.join(a.work, "case_summary.json"), cs)
    plan = C.build_query_plan(cs)
    C.write_json(os.path.join(a.work, "plan.json"), plan)
    print("plan ok: rounds=%d" % len(plan.get("rounds", [])))
    return 0


def _load_payload(args):
    """从 --inline JSON / --file 读取导入内容（list 或 dict）。"""
    if getattr(args, "inline", None):
        try:
            return json.loads(args.inline), []
        except Exception as e:
            return None, ["bad_inline_json:" + str(e)[:120]]
    if getattr(args, "file", None):
        return C.read_json(args.file, None)
    return None, ["no_input: 需要 --inline 或 --file"]


def cmd_import(a):
    payload, deg = _load_payload(a)
    if payload is None:
        print("import failed:", "; ".join(deg))
        return 1
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        print("import failed: payload 必须是 list 或 dict")
        return 1
    records, _ = C.read_json(os.path.join(a.work, "records.json"), [])
    if not isinstance(records, list):
        records = []
    for item in payload:
        item = C.normalize_record(item)
        if not item or not item.get("title"):
            print("import skip: 记录缺 title，已跳过（防止编造）:", item)
            continue
        key = C.dedup_key(item)
        replaced = False
        for i, existing in enumerate(records):
            if C.dedup_key(existing) == key and key is not None:
                records[i] = C.merge_records(existing, item)
                replaced = True
                break
        if not replaced:
            records.append(item)
    C.write_json(os.path.join(a.work, "records.json"), records)
    C.log_change(a.work, "import", "导入/合并候选 %d 条（去重后 %d 条）" % (
        len(payload), len(records)), "partial")
    print("import ok: total_records=%d" % len(records))
    return 0


def cmd_evidence(a):
    payload, deg = _load_payload(a)
    if payload is None:
        print("evidence failed:", "; ".join(deg))
        return 1
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        print("evidence failed: payload 必须是 list 或 dict")
        return 1
    entries, _ = C.read_json(os.path.join(a.work, "evidence.json"), [])
    if not isinstance(entries, list):
        entries = []
    for e in payload:
        if isinstance(e, dict):
            e.setdefault("timestamp", C.now_iso())
            entries.append(e)
    C.write_json(os.path.join(a.work, "evidence.json"), entries)
    C.log_change(a.work, "evidence", "追加证据 %d 条" % len(payload), "partial")
    print("evidence ok: total_entries=%d" % len(entries))
    return 0


def cmd_assemble(a):
    result = C.assemble(a.input, a.output, a.work)
    print("assemble ok: case_id=%s status=%s results=%d contract_fields=%d" % (
        result.get("case_id"), result.get("status"),
        len(result.get("results", [])),
        len(result.get("contract_report", {}).get("fields", []))))
    return 0


def cmd_selfcheck(a):
    ok, checks, deg = C.selfcheck(a.output, a.input, a.work)
    for mark, label in checks:
        print("[%s] %s" % (mark, label))
    if deg:
        print("[WARN] 降级记录:", "; ".join(str(x) for x in deg[:10]))
    print("selfcheck:", "PASS" if ok else "FAIL")
    return 0 if ok else 2


def main(argv=None):
    p = _args()
    sub = p.add_subparsers(dest="cmd")
    for name, fn in (
        ("run", cmd_run), ("skeleton", cmd_skeleton), ("parse", cmd_parse),
        ("plan", cmd_plan), ("import", cmd_import), ("evidence", cmd_evidence),
        ("assemble", cmd_assemble), ("selfcheck", cmd_selfcheck),
    ):
        sp = sub.add_parser(name)
        sp.add_argument("--inline", default=None)
        sp.add_argument("--file", default=None)
        sp.set_defaults(fn=fn)
    if argv is None:
        argv = sys.argv[1:]
    a = p.parse_args(argv)
    if not getattr(a, "cmd", None):
        p.print_help()
        return 0
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())