#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py —— 学术文献检索 Skill 的命令行入口（纯标准库）。

子命令：
  run         骨架流水线：skeleton -> parse -> plan -> assemble（不检索）
  skeleton    写一版合法 partial/insufficient_evidence 骨架 result.json
  parse       解析 /input -> /app/work/case_summary.json
  plan        由 case_summary 生成查询计划 -> /app/work/plan.json
  import      导入候选文献（JSON / JSONL / CSV）-> /app/work/records.json
  evidence    追加查询/来源证据 -> /app/work/evidence.json
  assemble    去重+打分+排序，写 /app/output/result.json 与 candidates.md
  selfcheck   校验 result.json 是否满足输出契约

缺省参数指向运行时约定：--input /input --output /app/output --work /app/work。
--input/--output/--work 允许出现在子命令之前或之后。
任何输入异常都会落盘合法 result.json，绝不因空输入/坏 JSON 崩溃。
"""
import argparse
import csv
import io
import json
import os
import sys
import time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import core  # noqa: E402


def _shared_parser():
    """共享 --input/--output/--work，允许其出现在子命令前后。"""
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--input", default=core.DEFAULT_INPUT, help="输入目录（默认 /input）")
    p.add_argument("--output", default=core.DEFAULT_OUTPUT, help="输出目录（默认 /app/output）")
    p.add_argument("--work", default=core.DEFAULT_WORK, help="工作目录（默认 /app/work）")
    return p


def build_parser():
    shared = _shared_parser()
    p = argparse.ArgumentParser(
        prog="main.py",
        parents=[shared],
        description="学术文献检索 Skill：稳健检索流水线与可复核 result.json。",
    )
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("run", parents=[shared], help="骨架流水线：skeleton->parse->plan->assemble")
    sub.add_parser("skeleton", parents=[shared], help="写合法 partial 骨架")
    sub.add_parser("parse", parents=[shared], help="解析输入为 case_summary.json")
    sub.add_parser("plan", parents=[shared], help="生成查询计划 plan.json")
    sp = sub.add_parser("import", parents=[shared], help="导入候选文献")
    sp.add_argument("--file", required=True, help="候选文件（JSON/JSONL/CSV）")
    sp.add_argument("--label", default=None, help="来源标签")
    ep = sub.add_parser("evidence", parents=[shared], help="追加证据记录")
    ep.add_argument("--inline", required=True, help="JSON 字符串证据条目")
    sub.add_parser("assemble", parents=[shared], help="组装 result.json + candidates.md")
    sub.add_parser("selfcheck", parents=[shared], help="校验 result.json 输出契约")
    return p


def ensure_workdirs(args):
    core.ensure_dir(args.work)
    core.ensure_dir(args.output)


def cmd_skeleton(args):
    ensure_workdirs(args)
    summary = core.parse_case(args.input, args.work)
    core.append_change(args.work, "skeleton", "落盘初始骨架（partial）", "partial")
    ok = core.write_skeleton(args.output, args.work, summary)
    print("skeleton:", "ok" if ok else "failed")
    return 0


def cmd_parse(args):
    ensure_workdirs(args)
    summary = core.parse_case(args.input, args.work)
    core.write_json(os.path.join(args.work, "case_summary.json"), summary)
    print("case_id:", summary.get("case_id"))
    print("research_question:", (summary.get("research_question") or "")[:160])
    print("files:", len(summary.get("files") or []))
    print("contract_key:", summary.get("contract_key"))
    return 0


def cmd_plan(args):
    ensure_workdirs(args)
    summary, _ = core.read_json(os.path.join(args.work, "case_summary.json"), {})
    if not summary:
        summary = core.parse_case(args.input, args.work)
    plan = core.build_plan(summary)
    core.write_json(os.path.join(args.work, "plan.json"), plan)
    core.append_change(args.work, "plan", "生成查询规划", "partial")
    print("plan core_terms:", plan.get("core_terms"))
    return 0


def _load_candidates_file(path, label):
    """读候选文件：JSON 对象/数组、JSONL、CSV。返回 (list, degradations)。"""
    text, deg = core.read_text(path)
    if deg:
        return [], deg
    label = label or os.path.basename(path)
    # CSV
    if path.lower().endswith(".csv"):
        rows = []
        try:
            reader = csv.DictReader(io.StringIO(text))
            for row in reader:
                rows.append({k: v for k, v in row.items() if k})
        except Exception as e:
            return [], ["bad_csv:%s:%s" % (path, str(e)[:80])]
        return rows, []
    # JSON 对象/数组
    if text.lstrip().startswith("{"):
        try:
            obj = json.loads(text)
            if isinstance(obj, list):
                return obj, []
            if isinstance(obj, dict):
                for key in ("results", "candidates", "items", "data", "records"):
                    v = obj.get(key)
                    if isinstance(v, list):
                        return v, []
                return [obj], []
        except Exception:
            pass
        # JSONL 兜底
        lines = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                lines.append(json.loads(line))
            except Exception:
                continue
        if lines:
            return lines, []
    # 顶层为 JSON 数组（以 [ 开头）
    try:
        obj = json.loads(text)
        if isinstance(obj, list):
            return obj, []
        if isinstance(obj, dict):
            for key in ("results", "candidates", "items", "data", "records"):
                v = obj.get(key)
                if isinstance(v, list):
                    return v, []
            return [obj], []
    except Exception:
        pass
    return [], ["unparseable_candidate_file:%s" % path]


def cmd_import(args):
    ensure_workdirs(args)
    incoming, deg = _load_candidates_file(args.file, args.label)
    records, added, merged, reasons = core.import_records(
        args.work, incoming, args.label or os.path.basename(args.file))
    core.append_change(
        args.work, "import",
        "导入候选：新增 %d 合并 %d 来源 %s" % (added, merged, args.label or args.file),
        "partial")
    print("import added=%d merged=%d total=%d" % (added, merged, len(records)))
    for d in deg:
        print("  degradation:", d)
    for r in reasons:
        print("  skip:", r)
    return 0


def cmd_evidence(args):
    ensure_workdirs(args)
    try:
        entry = json.loads(args.inline)
    except Exception as e:
        print("evidence 参数须为合法 JSON：", str(e)[:120], file=sys.stderr)
        return 2
    _, errs = core.append_evidence(args.work, entry)
    core.append_change(args.work, "evidence",
                       "记录证据：%s" % (entry.get("query") or entry.get("source") or "?"),
                       "partial")
    print("evidence appended:", len(core.load_evidence(args.work)))
    for e in errs:
        print("  warn:", e)
    return 0


def cmd_assemble(args):
    ensure_workdirs(args)
    summary, _ = core.read_json(os.path.join(args.work, "case_summary.json"), {})
    if not summary:
        summary = core.parse_case(args.input, args.work)
        core.write_json(os.path.join(args.work, "case_summary.json"), summary)
    core.append_change(args.work, "assemble", "组装最终 result.json", "partial")
    result = core.build_result(summary, args.work, status="partial")
    ok1 = core.write_json(os.path.join(args.output, "result.json"), result)
    ok2 = core.write_text(os.path.join(args.output, "candidates.md"),
                          core.render_candidates_md(result))
    print("assemble:", "ok" if (ok1 and ok2) else "failed")
    print("case_id:", result.get("case_id"))
    print("status:", result.get("status"))
    print("results:", len(result.get("results") or []))
    print("fingerprint:", (result.get("reproducibility") or {}).get("fingerprint"))
    return 0


def cmd_selfcheck(args):
    checks = core.selfcheck(args.output, args.work)
    failed = 0
    for name, ok in checks:
        mark = "PASS" if ok else "FAIL"
        print("[%s] %s" % (mark, name))
        if not ok:
            failed += 1
    print("selfcheck:", "PASS" if failed == 0 else ("%d FAIL" % failed))
    return 0 if failed == 0 else 1


def cmd_run(args):
    """骨架流水线：前 3 分钟内先落盘 partial，再增量改进。"""
    t0 = time.time()
    ensure_workdirs(args)
    summary = core.parse_case(args.input, args.work)
    core.write_json(os.path.join(args.work, "case_summary.json"), summary)
    plan = core.build_plan(summary)
    core.write_json(os.path.join(args.work, "plan.json"), plan)
    core.append_change(args.work, "run", "骨架流水线启动", "partial")
    ok = core.write_skeleton(args.output, args.work, summary)
    elapsed = round(time.time() - t0, 2)
    print("run:", "ok" if ok else "failed", "elapsed=%.2fs" % elapsed)
    print("case_id:", summary.get("case_id"))
    print("status:", "partial" if not (core.load_records(args.work)) else "ok")
    return 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 0
    handlers = {
        "run": cmd_run,
        "skeleton": cmd_skeleton,
        "parse": cmd_parse,
        "plan": cmd_plan,
        "import": cmd_import,
        "evidence": cmd_evidence,
        "assemble": cmd_assemble,
        "selfcheck": cmd_selfcheck,
    }
    try:
        return handlers[args.cmd](args)
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # 顶层兜底：任何异常都不让进程非正常退出。
        print("unexpected error:", str(e)[:300], file=sys.stderr)
        ensure_workdirs(args)
        try:
            summary = core.parse_case(args.input, args.work)
            result = core.build_result(summary, args.work, status="input_error")
            result["errors"] = list(result.get("errors") or []) + ["unexpected:" + str(e)[:200]]
            core.write_json(os.path.join(args.output, "result.json"), result)
            core.write_text(os.path.join(args.output, "candidates.md"),
                            core.render_candidates_md(result))
        except Exception:
            pass
        return 3


if __name__ == "__main__":
    sys.exit(main())
