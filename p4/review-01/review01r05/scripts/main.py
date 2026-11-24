#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""论文质量审查 Skill 主入口（仅标准库）。

子命令（Agent 按 SKILL.md 顺序调用）：
  init      扫描 /input，建索引，立刻落盘一版合法的 partial result.json
  show      打印带 locator 的原文，供 Agent 逐字引用
  check     校验 /app/work/draft.json（quote 命中 / 字段 / 枚举 / 契约对齐）
  build     读取 draft.json，生成 review_report.md + 最终 result.json
  selfcheck 只复检最终 result.json 与契约，不重做

默认路径与评测环境一致：/input、/app/work、/app/output；
本地可用 --input/--work/--output 覆盖。所有子命令退出码恒为 0，
任何异常都被顶层捕获并尽力保留最小合法 result.json。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from review_schema import (
    FACETS, SEVERITIES, ISSUE_TYPES, VERDICTS, CONFIDENCES, STATUSES,
    OVERALL_VERDICTS, ENVELOPE_RESERVED, ENVELOPE_NS, MAX_TEXT_FILE,
    TEXT_KINDS, PDF_KINDS, DOCX_KINDS, IMAGE_KINDS,
    read_text, normalize_text, split_paragraphs, extract_pdf_text,
    extract_docx_paras, normalize_contract, skeleton_of, flatten_skeleton,
    audit_contract, validate_finding, locate_quote, json_dumps,
)

SKILL_ROOT = Path(__file__).resolve().parent.parent

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _out_json(path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def _load_json(path):
    if not path.exists():
        return None
    text, _ = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _classify_kind(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in PDF_KINDS:
        return "pdf"
    if ext in DOCX_KINDS:
        return "docx"
    if ext in IMAGE_KINDS:
        return "image"
    if ext in TEXT_KINDS:
        return "text"
    return "other"


def _material_files(input_dir: Path):
    if not input_dir.exists():
        return []
    skip = {"case.json", "task.md"}
    items = [p for p in sorted(input_dir.rglob("*")) if p.is_file()
             and p.name not in skip]
    items = [p for p in items
             if not any(part.startswith(".") for part in p.parts)]
    return items


# ---------------------------------------------------------------------------
# 材料抽取
# ---------------------------------------------------------------------------
def _extract_material(path: Path, kind: str):
    if kind in ("image", "other"):
        return [], [f"{kind}: no text layer; needs OCR / external tool"]
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [], [f"read error: {exc}"]
    if len(data) > MAX_TEXT_FILE:
        return [], [f"file too large ({len(data)} bytes), skipped"]
    if kind == "pdf":
        pages, problems = extract_pdf_text(data)
        paras = []
        for pi, pg in enumerate(pages, 1):
            paras.extend(split_paragraphs(pg, page=pi))
        if not paras:
            return [], problems + ["no paragraphs extracted from PDF"]
        return paras, problems
    if kind == "docx":
        lines, problem = extract_docx_paras(data)
        if problem and not lines:
            return [], [problem]
        paras = split_paragraphs("\n\n".join(lines), page=None)
        return paras, ([problem] if problem else [])
    text, problem = read_text(path)
    if text is None:
        return [], [problem or "unreadable"]
    paras = split_paragraphs(text, page=None)
    return paras, ([problem] if problem else [])


def _extract_or_reuse(path, kind, work, sid, rescan):
    pre = work / "text" / f"{sid}.txt"
    if not rescan and pre.exists():
        text, problem = read_text(pre)
        if text is not None:
            pages = [p for p in text.split("\f") if p.strip()] or [text]
            paras = []
            for pi, pg in enumerate(pages, 1):
                paras.extend(split_paragraphs(pg, page=pi))
            problems = ([problem] if problem else []) + [
                f"reused pre-extracted text {pre.name}"]
            if paras:
                return paras, problems
    return _extract_material(path, kind)


# ---------------------------------------------------------------------------
# 输入扫描与索引
# ---------------------------------------------------------------------------
def _scan_input(input_dir: Path):
    state = {
        "case_id": None, "task_instruction": "", "output_contract": None,
        "contract_normalized": {}, "degradations": [], "errors": [],
        "materials": [],
    }
    case_path = input_dir / "case.json"
    if not case_path.exists():
        state["errors"].append({"file": "case.json", "error": "missing"})
        state["degradations"].append("case.json missing")
        return state
    text, problem = read_text(case_path)
    if text is None:
        state["errors"].append({"file": "case.json", "error": problem})
        state["degradations"].append("case.json unreadable")
        return state
    if problem:
        state["degradations"].append(f"case.json encoding: {problem}")
    case = None
    try:
        case = json.loads(text)
    except Exception as exc:
        state["errors"].append({"file": "case.json", "error": f"bad JSON: {exc}"})
        state["degradations"].append("case.json bad JSON; contract unknown")
    if isinstance(case, dict):
        state["case_id"] = (case.get("case_id") or case.get("id")
                            or case.get("task_id"))
        state["task_instruction"] = (
            case.get("task_instruction") or case.get("task")
            or case.get("instruction") or "")
        state["output_contract"] = case.get("output_contract")
    state["contract_normalized"] = normalize_contract(state["output_contract"])
    return state


def _build_materials(input_dir: Path, work: Path, rescan: bool):
    materials = []
    for idx, path in enumerate(_material_files(input_dir), 1):
        kind = _classify_kind(path.name)
        sid = f"D{idx}"
        paras, problems = _extract_or_reuse(path, kind, work, sid, rescan)
        materials.append({
            "source_id": sid,
            "path": str(path),
            "kind": kind,
            "size": path.stat().st_size if path.exists() else None,
            "paras_count": len(paras),
            "paras": paras,
            "problems": problems,
        })
    return materials


def default_unchecked():
    return [
        {"facet": f, "status": "not_checked",
         "reason": "尚未完成该分面的逐条审查",
         "evidence_limits": []}
        for f in FACETS
    ]


def default_verdict_summary(contract):
    counts = {
        "findings": 0,
        "by_facet": {f: 0 for f in FACETS},
        "by_severity": {s: 0 for s in SEVERITIES},
        "by_verdict": {v: 0 for v in VERDICTS},
    }
    coverage = {f: {"checked": False, "findings": 0} for f in FACETS}
    return {
        "overall_verdict": "not_checked",
        "counts": counts,
        "coverage": coverage,
        "contract": {"total_fields": 0, "satisfied": 0,
                     "defaulted": 0, "absent": 0},
    }


# ---------------------------------------------------------------------------
# change_log（append-only）
# ---------------------------------------------------------------------------
def _append_change(work: Path, action: str, summary: str, files=None):
    log = work / "change_log.jsonl"
    seq = 0
    entries = []
    if log.exists():
        text, _ = read_text(log)
        if text:
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass
    if entries:
        seq = max((e.get("seq", 0) for e in entries), default=0)
    seq += 1
    entry = {
        "seq": seq, "ts": _now_ts(), "action": action, "summary": summary,
        "files": files or [],
    }
    entries.append(entry)
    with open(log, "w", encoding="utf-8", newline="\n") as f:
        for e in entries[-200:]:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return entries


def _read_change_log(work: Path):
    log = work / "change_log.jsonl"
    entries = []
    if log.exists():
        text, _ = read_text(log)
        if text:
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass
    return entries


# ---------------------------------------------------------------------------
# 结果组装（契约优先、冲突安全、逐字段审计）
# ---------------------------------------------------------------------------
def build_result(index, draft, work, started_at):
    contract = index.get("contract_normalized") or {}
    contract_values = draft.get("contract_values")
    if not isinstance(contract_values, dict):
        contract_values = skeleton_of(contract)

    result = {}
    collisions = []

    # 1) case_id 原样保留
    result["case_id"] = index.get("case_id")

    # 2) output_contract 全部顶层字段原样回填（不改名、不删除）
    for k in contract.keys():
        result[k] = contract_values.get(k) if isinstance(contract_values, dict) \
            else skeleton_of(contract.get(k))

    # 3) 信封字段；与契约同名时契约值占据顶层，信封值移入 _skill_envelope
    findings = draft.get("findings", [])
    unchecked = draft.get("unchecked_items", default_unchecked())
    verdict = draft.get("verdict_summary",
                        default_verdict_summary(contract))
    audit = audit_contract(contract, contract_values)
    # 用 finding 统计刷新 verdict 中的计数（保持确定性）
    verdict["counts"]["findings"] = len(findings)
    for f in findings:
        fac = f.get("facet")
        sev = f.get("severity")
        ver = f.get("verdict")
        if fac in verdict["counts"]["by_facet"]:
            verdict["counts"]["by_facet"][fac] += 1
            verdict["coverage"][fac]["checked"] = True
            verdict["coverage"][fac]["findings"] += 1
        if sev in verdict["counts"]["by_severity"]:
            verdict["counts"]["by_severity"][sev] += 1
        if ver in verdict["counts"]["by_verdict"]:
            verdict["counts"]["by_verdict"][ver] += 1
    verdict["contract"] = {
        "total_fields": audit["total_fields"],
        "satisfied": audit["satisfied"],
        "defaulted": audit["defaulted"],
        "absent": audit["absent"],
    }

    envelope = {
        "status": draft.get("status", "partial"),
        "sources": _sources_list(index),
        "uncertainty": draft.get("uncertainty", []),
        "pending_human": draft.get("pending_human", []),
        "errors": index.get("errors", []),
        "degradations": index.get("degradations", []),
        "changes": draft.get("changes", []),
        "change_log": _read_change_log(work),
        "runtime": _runtime(started_at),
        "contract_audit": {
            "alignment": audit,
            "collisions": collisions,
            "contract_spec": contract,
        },
        "result": {
            "findings": findings,
            "unchecked_items": unchecked,
            "verdict_summary": verdict,
        },
    }

    for key, value in envelope.items():
        if key in contract:
            collisions.append(key)
            ns = result.setdefault(ENVELOPE_NS, {})
            ns[key] = value
        else:
            result[key] = value
    if collisions:
        result["contract_audit"]["collisions"] = collisions

    return result


def _sources_list(index):
    out = []
    for m in index.get("materials", []):
        out.append({
            "source_id": m["source_id"],
            "path": m["path"],
            "kind": m["kind"],
            "paras_count": m["paras_count"],
            "problems": m.get("problems", []),
        })
    return out


def _runtime(started_at):
    return {
        "wall_seconds": round(time.time() - started_at, 3),
        "started_at": _now_ts(),
        "finalize_deadline_seconds": 780,
        "budget": {"seconds": 900, "requests": 64,
                   "tokens_per_response": 16384},
        "version": "1.0.0",
    }


def _write_minimal_result(index, work, output, started_at):
    draft = {
        "contract_values": skeleton_of(index.get("contract_normalized") or {}),
        "findings": [],
        "unchecked_items": default_unchecked(),
        "verdict_summary": default_verdict_summary(
            index.get("contract_normalized") or {}),
        "status": _initial_status(index),
        "uncertainty": [],
        "pending_human": [],
        "changes": [],
    }
    result = build_result(index, draft, work, started_at)
    _out_json(output / "result.json", result)
    return result


def _initial_status(index):
    errors = index.get("errors") or []
    if any("bad JSON" in (e.get("error") or "") for e in errors):
        return "input_error"
    if not index.get("materials"):
        return "insufficient_evidence"
    return "partial"


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------
def _cmd_init(args):
    started = time.time()
    input_dir = Path(args.input)
    work = Path(args.work)
    output = Path(args.output)
    for d in (work, work / "text", output):
        d.mkdir(parents=True, exist_ok=True)

    index = _scan_input(input_dir)
    index["materials"] = _build_materials(input_dir, work, args.rescan)
    _out_json(work / "index.json", index)

    # 落盘契约骨架 draft，供 Agent 增量填写
    draft = {
        "contract_values": skeleton_of(index.get("contract_normalized") or {}),
        "findings": [],
        "unchecked_items": default_unchecked(),
        "verdict_summary": default_verdict_summary(
            index.get("contract_normalized") or {}),
        "status": _initial_status(index),
        "uncertainty": [],
        "pending_human": [],
        "changes": [],
        "notes": "",
    }
    _out_json(work / "draft.json", draft)
    _append_change(work, "init",
                   f"scan input, build index, land partial skeleton "
                   f"(status={draft['status']})",
                   files=["index.json", "draft.json", "result.json"])
    _write_minimal_result(index, work, output, started)
    _print_init_summary(index, draft)


def _print_init_summary(index, draft):
    n = len(index.get("materials", []))
    total_paras = sum(m["paras_count"] for m in index.get("materials", []))
    c = index.get("contract_normalized") or {}
    print("init ok")
    print(f"  case_id: {index.get('case_id')}")
    print(f"  materials: {n} files, {total_paras} paragraphs")
    print(f"  contract top-level fields: {len(c)}")
    print(f"  initial status: {draft['status']}")
    for m in index.get("materials", []):
        print(f"    {m['source_id']} {m['kind']} {m['path']} "
              f"({m['paras_count']} paras)")
        for p in m.get("problems", [])[:3]:
            print(f"      - {p}")
    for e in index.get("errors", []):
        print(f"  error: {e}")


def _cmd_show(args):
    work = Path(args.work)
    index = _load_json(work / "index.json") or {}
    if not index:
        print("no index.json; run init first")
        return
    shown = 0
    for m in index.get("materials", []):
        if args.source and m["source_id"] != args.source:
            continue
        print(f"### {m['source_id']} {m['kind']} {m['path']}")
        for p in m.get("paras", []):
            if shown >= args.max_chars:
                print("... (truncated by --max-chars)")
                return
            line = f"{m['source_id']}::{p['locator']}\t{p['text']}"
            print(line[:500])
            shown += len(line)


def _cmd_check(args):
    work = Path(args.work)
    index = _load_json(work / "index.json") or {}
    draft = _load_json(work / "draft.json") or {}
    contract = index.get("contract_normalized") or {}
    cv = draft.get("contract_values", {})

    contract_problems = []
    if isinstance(cv, dict):
        missing = [k for k in contract.keys() if k not in cv]
        extra = [k for k in cv.keys() if k not in contract]
        if missing:
            contract_problems.append(f"contract keys missing: {missing}")
        if extra:
            contract_problems.append(f"extra keys not in contract: {extra}")
    else:
        contract_problems.append("contract_values is not an object")

    finding_reports = []
    for f in draft.get("findings", []):
        probs = validate_finding(f)
        hit, loc = locate_quote(index, f.get("quote", ""))
        if not hit and f.get("quote"):
            probs.append("quote not found verbatim in materials")
        finding_reports.append({
            "finding_id": f.get("finding_id"),
            "problems": probs,
            "quote_hit": hit,
            "quote_locator": loc,
        })

    fails = len(contract_problems)
    fails += sum(1 for r in finding_reports if r["problems"])
    report = {
        "ts": _now_ts(),
        "contract": {
            "problems": contract_problems,
            "audit": audit_contract(contract, cv),
        },
        "findings": finding_reports,
        "summary": {"total_findings": len(finding_reports), "fails": fails},
        "overall": "PASS" if fails == 0 else "FAIL",
    }
    _out_json(work / "check_report.json", report)
    print(f"check {report['overall']} ({len(finding_reports)} findings, "
          f"{fails} problem groups)")
    for p in contract_problems:
        print(f"  contract: {p}")
    for r in finding_reports:
        if r["problems"]:
            print(f"  {r['finding_id']}: {'; '.join(r['problems'])}")


def _cmd_build(args):
    started = time.time()
    work = Path(args.work)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    index = _load_json(work / "index.json") or {}
    draft = _load_json(work / "draft.json") or {}
    # 先追加 build 变更记录，再组装 result，使 change_log 包含本次动作
    _append_change(
        work, "build",
        f"finalize result.json + review_report.md "
        f"(status={draft.get('status')}, "
        f"findings={len(draft.get('findings', []))})",
        files=["result.json", "review_report.md"])
    result = build_result(index, draft, work, started)
    _out_json(output / "result.json", result)
    report_md = render_review_report(result)
    (output / "review_report.md").write_text(report_md, encoding="utf-8",
                                             newline="\n")
    audit = result.get("contract_audit", {}).get("alignment", {})
    print("build ok")
    print(f"  status: {result.get('status')}")
    print(f"  findings: {len(result.get('result', {}).get('findings', []))}")
    print(f"  contract: total={audit.get('total_fields')} "
          f"satisfied={audit.get('satisfied')} "
          f"defaulted={audit.get('defaulted')} absent={audit.get('absent')}")
    if result.get("contract_audit", {}).get("collisions"):
        print(f"  collisions (moved to {ENVELOPE_NS}): "
              f"{result['contract_audit']['collisions']}")


def render_review_report(result):
    lines = ["# 论文质量审查报告", ""]
    lines.append(f"- case_id: `{result.get('case_id')}`")
    lines.append(f"- status: `{result.get('status')}`")
    r = result.get("result", {})
    findings = r.get("findings", [])
    lines.append(f"- findings: {len(findings)}")
    lines.append("")
    lines.append("## 问题清单")
    lines.append("")
    if not findings:
        lines.append("（暂无逐条问题记录）")
    for f in findings:
        lines.append(f"### {f.get('finding_id')} — {f.get('facet')} / "
                     f"{f.get('severity')} / {f.get('verdict')}")
        lines.append(f"- 定位：`{f.get('location')}`")
        lines.append(f"- 原文：> {f.get('quote')}")
        lines.append(f"- 论断：{f.get('claim')}")
        lines.append(f"- 依据：{f.get('rationale')}")
        lines.append(f"- 建议：{f.get('revision_suggestion')}")
        if f.get("evidence"):
            for ev in f["evidence"]:
                lines.append(f"- 证据：`{ev.get('source_id')}` "
                             f"`{ev.get('locator')}` > {ev.get('quote')}")
        lines.append("")
    lines.append("## 未检查项")
    lines.append("")
    for u in r.get("unchecked_items", []):
        lines.append(f"- {u.get('facet')}: {u.get('reason')}")
    lines.append("")
    lines.append("## 结论摘要")
    vs = r.get("verdict_summary", {})
    lines.append(f"- overall_verdict: `{vs.get('overall_verdict')}`")
    lines.append("- counts: "
                 + json.dumps(vs.get("counts", {}), ensure_ascii=False))
    lines.append("")
    lines.append("## 契约对齐审计")
    audit = result.get("contract_audit", {}).get("alignment", {})
    lines.append(f"- total={audit.get('total_fields')} "
                 f"satisfied={audit.get('satisfied')} "
                 f"defaulted={audit.get('defaulted')} "
                 f"absent={audit.get('absent')}")
    return "\n".join(lines) + "\n"


def _cmd_selfcheck(args):
    work = Path(args.work)
    output = Path(args.output)
    result = _load_json(output / "result.json")
    index = _load_json(work / "index.json") or {}
    contract = index.get("contract_normalized") or {}
    if result is None:
        print("selfcheck: result.json missing")
        _out_json(output / "selfcheck.json",
                  {"overall": "FAIL", "reason": "result.json missing"})
        return

    problems = []
    if not result.get("case_id"):
        problems.append("case_id missing")
    if result.get("status") not in STATUSES:
        problems.append(f"bad status {result.get('status')!r}")
    for key in ("sources", "uncertainty", "change_log"):
        if key not in result and key not in (result.get(ENVELOPE_NS) or {}):
            problems.append(f"envelope key missing: {key}")
    for k in contract.keys():
        if k not in result:
            problems.append(f"contract key dropped: {k}")

    quote_misses = 0
    for f in result.get("result", {}).get("findings", []):
        if f.get("quote"):
            hit, _ = locate_quote(index, f.get("quote", ""))
            if not hit:
                quote_misses += 1
    if quote_misses:
        problems.append(f"{quote_misses} finding quotes not verbatim")

    report = {
        "ts": _now_ts(),
        "overall": "PASS" if not problems else "FAIL",
        "problems": problems,
        "quote_misses": quote_misses,
    }
    _out_json(output / "selfcheck.json", report)
    print(f"selfcheck {report['overall']}")
    for p in problems:
        print(f"  - {p}")


# ---------------------------------------------------------------------------
# 参数解析与容错入口
# ---------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="论文质量审查 Skill 助手 "
                    "(init/show/check/build/selfcheck)")
    p.add_argument("command", nargs="?", default=None,
                   choices=["init", "show", "check", "build", "selfcheck"])
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
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        # --help / 参数错误：打印帮助并保底落盘最小合法结果
        try:
            parser.print_help()
        except Exception:
            pass
        return 0

    started = time.time()
    handlers = {
        "init": _cmd_init,
        "show": _cmd_show,
        "check": _cmd_check,
        "build": _cmd_build,
        "selfcheck": _cmd_selfcheck,
    }
    if args.command is None:
        parser.print_help()
        return 0
    try:
        handlers[args.command](args)
    except Exception as exc:
        print(f"{args.command} failed: {exc}")
        # 尽力保留最小合法产物
        try:
            work = Path(args.work)
            output = Path(args.output)
            output.mkdir(parents=True, exist_ok=True)
            index = _load_json(work / "index.json") or {}
            _write_minimal_result(index, work, output, started)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
