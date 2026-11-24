#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""论文质量审查 Skill 主入口（仅标准库）。

子命令（Agent 按 SKILL.md 的阶段预算顺序调用）：
  init      扫描 /input，建索引 + 机械预审，立即落盘合法 partial result.json 骨架
  show      打印带 locator 的原文段落，供 Agent 逐字抄录 quote
  plan      打印剩余时间、当前阶段、分面覆盖与下一步建议
  check     校验 /app/work/draft.json（quote 逐字命中 / 字段 / 枚举 / 覆盖）
  build     读取 draft.json，生成 review_report.md + 最终 result.json
  selfcheck 只复检最终 result.json 与主产物，不重做

默认路径与评测环境一致：/input、/app/work、/app/output；
本地可用 --input/--work/--output 覆盖。所有子命令退出码恒为 0。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import (  # noqa: E402
    FACETS, FACET_CN, STATUSES, SEVERITIES,
    read_text, split_paragraphs,
    build_index, mechanical_scan,
    validate_draft, compute_summary, compute_status,
    build_report_md, build_result, plan_text, _iso,
)

SKILL_ROOT = Path(__file__).resolve().parent.parent


def _ensure_utf8_streams():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _save_json(path: Path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def _load_json(path: Path):
    if not path.exists():
        return None
    text, _ = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _load_paras_for_source(work_dir: Path, sid: str):
    pre = work_dir / "text" / f"{sid}.txt"
    if not pre.exists():
        return []
    text, _ = read_text(pre)
    if text is None:
        return []
    pages = [pg for pg in text.split("\f") if pg.strip()] or [text]
    paras = []
    for pi, pg in enumerate(pages, 1):
        paras.extend(split_paragraphs(pg, page=pi if len(pages) > 1 else None))
    return paras


def _skeleton_draft(mech_findings, mech_unc):
    unchecked = []
    for f in FACETS:
        n = sum(1 for x in mech_findings if x.get("facet") == f)
        reason = (f"机械预审发现 {n} 条线索，尚未完成该分面的人工审查"
                  if n else "尚未进入 facet_audit 阶段或未命中可确认问题")
        unchecked.append({"facet": f, "reason": reason})
    return {
        "findings": mech_findings,
        "unchecked_items": unchecked,
        "coverage": {f: "not_checked" for f in FACETS},
        "uncertainty": mech_unc,
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "changes": ["init: 机械预审已应用，六类分面人工审查待完成"],
        "overall_verdict": "unable_to_judge",
    }


def do_init(input_dir, work_dir, output_dir, rescan):
    started_at = time.time()
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    index, paras = build_index(input_dir, work_dir, rescan)
    mech_findings, mech_unc = mechanical_scan(index, paras)
    draft = _skeleton_draft(mech_findings, mech_unc)
    _save_json(work_dir / "index.json", index)
    _save_json(work_dir / "draft.json", draft)
    summary = compute_summary(draft)
    status = compute_status(index, summary)
    change_log = [{"at": _iso(), "phase": "init",
                   "change": "init landed partial result.json skeleton + mechanical pre-audit"}]
    result = build_result(draft, index, summary, status, started_at, change_log)
    _save_json(output_dir / "result.json", result)
    report = build_report_md(draft, index, summary, status)
    (output_dir / "review_report.md").write_text(report, encoding="utf-8", newline="\n")
    _save_json(work_dir / "phase_log.json", {"started_at": started_at, "changes": change_log})
    print(f"case_id={index.get('case_id')}")
    print(f"status={status}")
    print(f"sources={len(index['sources'])}  findings={len(mech_findings)}  "
          f"unchecked={len(draft['unchecked_items'])}")
    print(f"index -> {work_dir / 'index.json'}")
    print(f"draft -> {work_dir / 'draft.json'}")
    print(f"partial result -> {output_dir / 'result.json'}")
    print("（骨架已落盘；下一步 show 读原文 + plan 看预算。）")
    return 0


def do_show(input_dir, work_dir, output_dir, source, max_chars):
    index = _load_json(work_dir / "index.json") or {}
    sources = index.get("sources", [])
    if source:
        sources = [s for s in sources if s.get("source_id") == source]
    used = 0
    for s in sources:
        sid = s.get("source_id")
        paras = _load_paras_for_source(work_dir, sid)
        print(f"===== {sid} {s.get('name')} ({s.get('kind')}, {len(paras)} paras) =====")
        for p in paras:
            line = f"[{p['locator']}] {p['text']}"
            if used + len(line) > max_chars:
                print(f"... 已达单次打印上限 {max_chars} 字符，其余请用 --source {sid} 分次读取 ...")
                print(f"（完整原文见 {work_dir / 'text' / (sid + '.txt')}）")
                return 0
            print(line)
            used += len(line) + 1
    if not sources:
        print("（无材料；先跑 init。）")
    return 0


def do_plan(input_dir, work_dir, output_dir, *_):
    index = _load_json(work_dir / "index.json") or {}
    draft = _load_json(work_dir / "draft.json") or {}
    phase_log = _load_json(work_dir / "phase_log.json") or {}
    started = phase_log.get("started_at", time.time())
    print(plan_text(index, draft, started))
    return 0


def do_check(input_dir, work_dir, output_dir, *_):
    index = _load_json(work_dir / "index.json") or {"sources": []}
    draft = _load_json(work_dir / "draft.json")
    if draft is None:
        draft = {}
    paras_by_source = {}
    for s in index.get("sources", []):
        paras_by_source[s.get("source_id")] = _load_paras_for_source(
            work_dir, s.get("source_id"))
    report = validate_draft(draft, index, paras_by_source)
    _save_json(work_dir / "check_report.json", report)
    print(f"errors={len(report['errors'])}  warnings={len(report['warnings'])}")
    for e in report["errors"]:
        print(f"  [ERROR] {e}")
    for w in report["warnings"]:
        print(f"  [WARN] {w}")
    print("counts:", json.dumps(report["counts"], ensure_ascii=False))
    print("coverage:", json.dumps(report["coverage"], ensure_ascii=False))
    print("checked:", report["checked_facets"], "unhandled:", report["unhandled_facets"],
          "n/a:", report["n_a_facets"])
    if report["errors"]:
        print("（存在 ERROR：回到原文重抄 quote / 修正字段枚举，直到无 ERROR。）")
    else:
        print("（无 ERROR，可 build。）")
    return 0


def do_build(input_dir, work_dir, output_dir, *_):
    started_at = time.time()
    phase_log = _load_json(work_dir / "phase_log.json") or {}
    if phase_log.get("started_at"):
        started_at = float(phase_log["started_at"])
    index = _load_json(work_dir / "index.json") or {"sources": []}
    draft = _load_json(work_dir / "draft.json")
    if draft is None:
        draft = _skeleton_draft([], [])
    paras_by_source = {}
    for s in index.get("sources", []):
        paras_by_source[s.get("source_id")] = _load_paras_for_source(
            work_dir, s.get("source_id"))
    report = validate_draft(draft, index, paras_by_source)
    if report["errors"]:
        draft = dict(draft)
        draft["errors"] = list(draft.get("errors", [])) + report["errors"]
    summary = compute_summary(draft)
    status = compute_status(index, summary)
    change_log = list(phase_log.get("changes", []))
    change_log.append({"at": _iso(), "phase": "build",
                       "change": "build final result.json + review_report.md"})
    result = build_result(draft, index, summary, status, started_at, change_log)
    output_dir.mkdir(parents=True, exist_ok=True)
    _save_json(output_dir / "result.json", result)
    report_md = build_report_md(draft, index, summary, status)
    (output_dir / "review_report.md").write_text(report_md, encoding="utf-8", newline="\n")
    _save_json(work_dir / "draft.json", draft)
    _save_json(work_dir / "phase_log.json", {"started_at": started_at, "changes": change_log})
    print(f"status={status}  findings={len(draft.get('findings', []))}  "
          f"checked={summary['checked_count']}  unhandled={summary['unhandled_facets']}")
    if report["errors"]:
        print(f"（注意：check 仍报 {len(report['errors'])} 个 ERROR，已写入 result.json 的 errors。）")
    print(f"最终产物 -> {output_dir / 'result.json'}")
    print(f"主报告 -> {output_dir / 'review_report.md'}")
    return 0


def do_selfcheck(input_dir, work_dir, output_dir, *_):
    items = []
    result = _load_json(output_dir / "result.json")
    if result is None:
        print("[FAIL] result.json 缺失或非法 JSON")
        return 0
    def chk(name, cond, detail=""):
        items.append((name, bool(cond), detail))

    chk("result.json 为对象", isinstance(result, dict))
    chk("case_id 存在", "case_id" in result)
    chk("status 合法", result.get("status") in STATUSES, str(result.get("status")))
    chk("sources 为列表", isinstance(result.get("sources"), list))
    chk("verdict_summary 存在", isinstance(result.get("verdict_summary"), dict))
    chk("result.findings 为列表", isinstance(result.get("result", {}).get("findings"), list))
    chk("uncertainty 为列表", isinstance(result.get("uncertainty"), list))
    chk("change_log 为列表", isinstance(result.get("change_log"), list))
    report_path = output_dir / "review_report.md"
    chk("review_report.md 存在", report_path.exists())
    if report_path.exists():
        try:
            chk("review_report.md 非空", report_path.stat().st_size > 0)
        except OSError:
            chk("review_report.md 非空", False)

    index = _load_json(work_dir / "index.json")
    draft = _load_json(work_dir / "draft.json")
    if index is not None and draft is not None:
        paras_by_source = {}
        for s in index.get("sources", []):
            paras_by_source[s.get("source_id")] = _load_paras_for_source(
                work_dir, s.get("source_id"))
        rep = validate_draft(draft, index, paras_by_source)
        chk("quote 逐字命中（0 ERROR）", len(rep["errors"]) == 0, f"{len(rep['errors'])} errors")

    n_pass = sum(1 for _, ok, _ in items if ok)
    for name, ok, detail in items:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" ({detail})" if detail and not ok else ""))
    print(f"selfcheck: {n_pass}/{len(items)} 项通过（仅复检，不重做）。")
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="论文质量审查 Skill 助手（init/show/plan/check/build/selfcheck）")
    p.add_argument("command", nargs="?", default=None)
    p.add_argument("--input", dest="input", default="/input")
    p.add_argument("--work", dest="work", default="/app/work")
    p.add_argument("--output", dest="output", default="/app/output")
    p.add_argument("--source", dest="source", default=None,
                   help="show 只打印指定 source_id（如 D1）")
    p.add_argument("--max-chars", dest="max_chars", type=int, default=12000,
                   help="show 单次打印上限字符数")
    p.add_argument("--rescan", dest="rescan", action="store_true",
                   help="init 时强制重新抽取文本（默认复用 work/text）")
    return p


def main(argv=None):
    _ensure_utf8_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    cmd = args.command
    handlers = {
        "init": do_init, "show": do_show, "plan": do_plan,
        "check": do_check, "build": do_build, "selfcheck": do_selfcheck,
    }
    try:
        if cmd is None:
            parser.print_help()
            return 0
        if cmd not in handlers:
            print(f"未知子命令: {cmd}", file=sys.stderr)
            parser.print_help()
            return 0
        input_dir = Path(args.input)
        work_dir = Path(args.work)
        output_dir = Path(args.output)
        extra = [args.source, args.max_chars, args.rescan]
        if cmd == "init":
            return handlers[cmd](input_dir, work_dir, output_dir, args.rescan)
        if cmd == "show":
            return handlers[cmd](input_dir, work_dir, output_dir, args.source, args.max_chars)
        return handlers[cmd](input_dir, work_dir, output_dir)
    except Exception as exc:  # 顶层兜底：任何异常退出码恒 0，尽量保留最小合法产物
        print(f"[degraded] {exc}", file=sys.stderr)
        try:
            out = Path(args.output)
            out.mkdir(parents=True, exist_ok=True)
            minimal = {
                "case_id": None, "status": "partial",
                "sources": [], "uncertainty": [],
                "pending_human": [], "errors": [{"file": "main.py", "error": str(exc)}],
                "degradations": ["top-level exception; minimal result written"],
                "changes": [], "change_log": [{"at": _iso(), "phase": "degraded",
                                               "change": "top-level exception"}],
                "runtime": {"status": "partial", "elapsed_seconds": None},
                "verdict_summary": {"coverage": {}, "counts": {}, "overall_verdict": "unable_to_judge"},
                "result": {"findings": [], "unchecked_items": []},
            }
            _save_json(out / "result.json", minimal)
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
