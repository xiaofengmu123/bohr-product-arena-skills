#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""论文质量审查 Skill 主入口（仅标准库）。

子命令（Agent 按 SKILL.md 顺序调用）：
  init      扫描 /input，建索引，立刻落盘一版合法 partial result.json 骨架
  show      打印带 locator 的原文，供 Agent 逐字抄录 quote
  check     校验 /app/work/draft.json（逐字命中 / 字段 / 枚举 / 六类覆盖）
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

from review_core import (
    FACETS, STATUSES, MAX_TEXT_FILE, read_text, norm, split_paragraphs,
    extract_pdf_text, extract_docx_paras, locator_index, contract_keys_of,
    merge_contract, validate_finding, check_quote_hits,
    derive_evidence_map, derive_verdict_summary,
    TEXT_KINDS, PDF_KINDS, DOCX_KINDS, IMAGE_KINDS,
)

SKILL_ROOT = Path(__file__).resolve().parent.parent

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="论文质量审查 Skill 助手（init/show/check/build/selfcheck）")
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
                   help="init 强制重新抽取文本（默认复用 work/text 下的预抽取文本）")
    return p


def _out_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
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
    items = [p for p in sorted(input_dir.rglob("*")) if p.is_file() and p.name not in skip]
    return [p for p in items if not any(part.startswith(".") for part in p.parts)]


def _extract_material(path: Path, kind: str):
    if kind in ("image", "other"):
        return [], [f"{kind}: no text layer; needs OCR / external tool"]
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [], [f"read error: {exc}"]
    if len(data) > MAX_TEXT_FILE:
        return [], [f"file too large ({len(data)} bytes > {MAX_TEXT_FILE}); skipped"]
    if kind == "pdf":
        pages, problems = extract_pdf_text(data)
        paras = []
        for pi, pg in enumerate(pages, 1):
            paras.extend(split_paragraphs(pg, page=pi))
        if not paras:
            return [], problems + ["no paragraphs extracted"]
        return paras, problems
    if kind == "docx":
        lines, problem = extract_docx_paras(data)
        if problem and not lines:
            return [], [problem]
        text = "\n\n".join(lines)
        return split_paragraphs(text, page=None), ([problem] if problem else [])
    text, problem = read_text(path)
    if text is None:
        return [], [problem or "unreadable"]
    return split_paragraphs(text, page=None), ([problem] if problem else [])


def _extract_or_reuse(path: Path, kind: str, work: Path, sid: str, rescan: bool):
    """优先复用 Agent 用平台工具预抽取的 work/text/<sid>.txt（含 \\f 页分隔）。"""
    pre = work / "text" / f"{sid}.txt"
    if not rescan and pre.exists():
        text, problem = read_text(pre)
        if text is not None:
            pages = [p for p in text.split("\f") if p.strip()] or [text]
            paras = []
            for pi, pg in enumerate(pages, 1):
                paras.extend(split_paragraphs(pg, page=pi))
            problems = ([problem] if problem else []) + [f"reused pre-extracted text {pre.name}"]
            if paras:
                return paras, problems
    return _extract_material(path, kind)


def _scan_input(input_dir: Path):
    state = {
        "case_id": None, "task_instruction": "", "output_contract": None,
        "degradations": [], "errors": [], "contract_keys": [],
    }
    case_path = input_dir / "case.json"
    if case_path.exists():
        text, problem = read_text(case_path)
        if text is None:
            state["errors"].append({"file": "case.json", "error": problem})
            state["degradations"].append("case.json unreadable")
        else:
            if problem:
                state["degradations"].append(f"case.json encoding: {problem}")
            try:
                case = json.loads(text)
            except Exception as exc:
                state["errors"].append({"file": "case.json", "error": f"bad JSON: {exc}"})
                state["degradations"].append("case.json bad JSON; contract unknown")
            else:
                if isinstance(case, dict):
                    state["case_id"] = case.get("case_id") or case.get("id") or case.get("task_id")
                    state["task_instruction"] = (
                        case.get("task_instruction") or case.get("task")
                        or case.get("instruction") or "")
                    state["output_contract"] = case.get("output_contract")
                    state["contract_keys"] = contract_keys_of(state["output_contract"])
    task_path = input_dir / "task.md"
    if task_path.exists():
        text, problem = read_text(task_path)
        if text is None:
            state["errors"].append({"file": "task.md", "error": problem})
            state["degradations"].append("task.md unreadable")
        elif not state["task_instruction"]:
            state["task_instruction"] = text[:2000]
    return state


def cmd_init(args):
    work = Path(args.work)
    out = Path(args.output)
    inp = Path(args.input)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    (work / "text").mkdir(parents=True, exist_ok=True)

    state = _scan_input(inp)
    materials = []
    paragraphs = {}
    files = _material_files(inp)
    for i, path in enumerate(files, 1):
        sid = f"D{i}"
        kind = _classify_kind(path.name)
        paras, problems = _extract_or_reuse(path, kind, work, sid, args.rescan)
        rel = str(path.relative_to(inp)) if path.is_relative_to(inp) else path.name
        materials.append({
            "source_id": sid, "name": path.name, "path": rel, "kind": kind,
            "locator_count": len(paras), "char_count": sum(len(p["text"]) for p in paras),
            "problems": problems,
        })
        if paras:
            paragraphs[sid] = paras
        for pb in problems:
            state["degradations"].append(f"{sid} {path.name}: {pb}")

    index = {
        "case_id": state["case_id"],
        "task_instruction": state["task_instruction"],
        "output_contract": state["output_contract"],
        "contract_keys": state["contract_keys"],
        "input_dir": str(inp),
        "materials": materials,
        "paragraphs": paragraphs,
        "degradations": state["degradations"],
        "errors": state["errors"],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _out_json(work / "index.json", index)

    # 步骤 0 关键：立刻落盘一版合法 partial 结果骨架，此后只做增量改进。
    status = "partial"
    if not files:
        status = "insufficient_evidence"
    elif state["errors"] and not materials:
        status = "input_error"
    skeleton = {
        "case_id": state["case_id"],
        "status": status,
        "result": {
            "findings": [],
            "unchecked_items": [],
            "verdict_summary": derive_verdict_summary([], []),
        },
        "evidence_map": {},
        "sources": [
            {"source_id": m["source_id"], "name": m["name"], "path": m["path"],
             "kind": m["kind"], "locator_count": m["locator_count"],
             "problems": m["problems"]}
            for m in materials
        ],
        "uncertainty": state["degradations"] + state["errors"],
        "pending_human": [],
        "errors": state["errors"],
        "degradations": state["degradations"],
        "changes": [],
        "change_log": [{"step": "init", "at": index["generated_at"],
                        "note": "initial partial skeleton"}],
        "runtime": {"seconds": 0.0, "generated_at": index["generated_at"]},
    }
    merge_contract(skeleton, state["output_contract"])
    _out_json(out / "result.json", skeleton)

    print(f"[init] case_id={state['case_id']} materials={len(materials)} "
          f"paragraphs={sum(len(v) for v in paragraphs.values())} "
          f"status={status} contract_keys={state['contract_keys']}")
    for pb in state["degradations"]:
        print(f"[degradation] {pb}")
    print(f"[init] wrote {work/'index.json'} and {out/'result.json'}")


def cmd_show(args):
    work = Path(args.work)
    index = _load_json(work / "index.json")
    if not index:
        print("[show] no index.json; run init first")
        return
    paras_by_sid = index.get("paragraphs", {})
    materials = {m["source_id"]: m for m in index.get("materials", [])}
    sids = [args.source] if args.source else sorted(paras_by_sid.keys())
    budget = args.max_chars
    used = 0
    for sid in sids:
        if sid not in paras_by_sid:
            print(f"[show] source_id {sid} not found")
            continue
        mat = materials.get(sid, {})
        print(f"\n=== {sid} | {mat.get('name')} | kind={mat.get('kind')} "
              f"| paragraphs={len(paras_by_sid[sid])} ===")
        for p in paras_by_sid[sid]:
            line = f"[{p['locator']}] {p['text']}"
            if used + len(line) > budget:
                print(f"...(truncated at {budget} chars; use --max-chars to raise or --source to narrow)")
                return
            print(line)
            used += len(line) + 1
    if not sids:
        print("[show] no paragraphs; materials may be images/unreadable — see index.materials[].problems")


def _load_draft(work: Path):
    draft = _load_json(work / "draft.json")
    if not draft:
        return None
    return draft


def cmd_check(args):
    work = Path(args.work)
    out = Path(args.output)
    index = _load_json(work / "index.json")
    draft = _load_draft(work)
    report = {"ok": True, "errors": [], "warnings": [],
              "findings_checked": 0, "quote_summary": []}
    if not index:
        report["ok"] = False
        report["errors"].append("missing index.json; run init")
    if not draft:
        report["ok"] = False
        report["errors"].append("missing draft.json at /app/work/draft.json")
    if report["ok"]:
        findings = draft.get("findings", [])
        unchecked = draft.get("unchecked_items", [])
        all_paras = index.get("paragraphs", {})
        for f in findings:
            report["findings_checked"] += 1
            validate_finding(f, report["errors"])
            hit = check_quote_hits(f, all_paras)
            report["quote_summary"].append({"finding_id": f.get("finding_id"), **hit})
            if not hit["ok"]:
                report["errors"].append(f"{f.get('finding_id','?')}: {hit['detail']}")
        covered = {f.get("facet") for f in findings if f.get("facet") in FACETS}
        uncovered = {u.get("facet") for u in unchecked if u.get("facet") in FACETS}
        missing = [fc for fc in FACETS if fc not in covered and fc not in uncovered]
        if missing:
            report["warnings"].append(f"facets without finding AND without unchecked entry: {missing}")
        if not findings and not unchecked:
            report["warnings"].append("no findings and no unchecked_items; review is empty")
        # 证据溯源完整性：每条 finding 至少 1 条 evidence，否则提示补证据
        for f in findings:
            if not f.get("evidence"):
                report["warnings"].append(f"{f.get('finding_id','?')}: no evidence[]; every key claim needs locator+quote")
        report["ok"] = report["ok"] and not report["errors"]
    _out_json(work / "check_report.json", report)
    for e in report["errors"]:
        print(f"[check][FAIL] {e}")
    for w in report["warnings"]:
        print(f"[check][WARN] {w}")
    print(f"[check] ok={report['ok']} findings_checked={report['findings_checked']}")


def cmd_build(args):
    work = Path(args.work)
    out = Path(args.output)
    index = _load_json(work / "index.json")
    draft = _load_draft(work)
    if not index or not draft:
        print("[build] missing index.json or draft.json; run init then write draft.json")
        # 保留既有骨架，不覆盖为错误产物
        return
    findings = draft.get("findings", [])
    unchecked = draft.get("unchecked_items", [])
    for f in findings:
        validate_finding(f, [])
    verdict_summary = draft.get("verdict_summary") or derive_verdict_summary(findings, unchecked)

    result = {
        "case_id": index.get("case_id"),
        "status": draft.get("status", "partial"),
        "result": {
            "findings": findings,
            "unchecked_items": unchecked,
            "verdict_summary": verdict_summary,
        },
        "evidence_map": derive_evidence_map(findings),
        "sources": index.get("sources", []),
        "uncertainty": draft.get("uncertainty", []) or [],
        "pending_human": draft.get("pending_human", []) or [],
        "errors": index.get("errors", []) + (draft.get("errors", []) or []),
        "degradations": index.get("degradations", []) + (draft.get("degradations", []) or []),
        "changes": draft.get("changes", []) or [],
        "change_log": (draft.get("change_log", []) or []) + [{
            "step": "build", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "note": f"built from draft.json with {len(findings)} findings, {len(unchecked)} unchecked",
        }],
        "runtime": {
            "seconds": round(draft.get("runtime", {}).get("seconds", 0.0), 3),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    }
    # draft 顶层含 output_contract 同名字段时原样并入，其余写 null/[]
    for k, v in draft.items():
        if k not in result and k not in ("findings", "unchecked_items", "verdict_summary",
                                         "status", "uncertainty", "pending_human", "errors",
                                         "degradations", "changes", "change_log", "runtime"):
            result[k] = v
    merge_contract(result, index.get("output_contract"))
    _out_json(out / "result.json", result)

    report = _render_report(result)
    (out / "review_report.md").write_text(report, encoding="utf-8", newline="\n")
    print(f"[build] wrote {out/'result.json'} and {out/'review_report.md'} "
          f"status={result['status']} findings={len(findings)}")


def _render_report(result):
    lines = []
    lines.append("# 论文质量审查报告")
    lines.append("")
    lines.append(f"- case_id: `{result.get('case_id')}`")
    lines.append(f"- status: `{result.get('status')}`")
    lines.append("")
    vs = result["result"].get("verdict_summary", {})
    lines.append(f"- 结论计数：{vs.get('totals', {})}")
    lines.append(f"- 六类覆盖（已查）：{vs.get('coverage', {}).get('checked_facets', [])}")
    lines.append(f"- 六类覆盖（未查）：{vs.get('coverage', {}).get('unchecked_facets', [])}")
    lines.append("")
    lines.append("## 逐条问题（findings）")
    lines.append("")
    for f in result["result"].get("findings", []):
        lines.append(f"### {f.get('finding_id')} · {f.get('facet')} · {f.get('severity')} · {f.get('verdict')}")
        lines.append("")
        lines.append(f"- 论断：{f.get('claim')}")
        lines.append(f"- 定位：`{f.get('location')}`")
        lines.append(f"- 原文引用：> {f.get('quote')}")
        lines.append(f"- 判定依据：{f.get('rationale')}")
        lines.append(f"- 修改建议：{f.get('revision_suggestion')}")
        lines.append(f"- 置信度：`{f.get('confidence')}`")
        ev = f.get("evidence", [])
        if ev:
            lines.append("- 证据：")
            for e in ev:
                lines.append(f"  - [{e.get('evid_id')}] `{e.get('role')}` {e.get('source_id')}@{e.get('locator')} `{e.get('relation')}`")
                lines.append(f"    > {e.get('quote')}")
        lines.append("")
    lines.append("## 未检查项（unchecked_items）")
    lines.append("")
    for u in result["result"].get("unchecked_items", []):
        lines.append(f"- `{u.get('facet')}`：{u.get('reason')}")
    lines.append("")
    if result.get("pending_human"):
        lines.append("## 待人工复核")
        lines.append("")
        for p in result.get("pending_human", []):
            lines.append(f"- {p}")
        lines.append("")
    if result.get("uncertainty"):
        lines.append("## 不确定性与冲突")
        lines.append("")
        for u in result.get("uncertainty", []):
            lines.append(f"- {u}")
        lines.append("")
    return "\n".join(lines)


def cmd_selfcheck(args):
    work = Path(args.work)
    out = Path(args.output)
    index = _load_json(work / "index.json")
    result = _load_json(out / "result.json")
    report = {"ok": True, "errors": [], "warnings": []}
    if not result:
        report["ok"] = False
        report["errors"].append("missing /app/output/result.json")
    else:
        for k in ("case_id", "status", "sources", "uncertainty", "pending_human",
                  "errors", "degradations", "changes", "change_log", "runtime"):
            if k not in result:
                report["errors"].append(f"result.json missing envelope key '{k}'")
        if result.get("status") not in STATUSES:
            report["errors"].append(f"status '{result.get('status')}' not in {STATUSES}")
        if "result" not in result or "findings" not in result.get("result", {}):
            report["errors"].append("result.result.findings missing")
        if "evidence_map" not in result:
            report["warnings"].append("evidence_map missing")
        all_paras = (index or {}).get("paragraphs", {})
        for f in result.get("result", {}).get("findings", []):
            hit = check_quote_hits(f, all_paras)
            if not hit["ok"]:
                report["errors"].append(f"{f.get('finding_id','?')}: {hit['detail']}")
        # evidence_map 与 finding.evidence 一致性
        em = result.get("evidence_map", {})
        for f in result.get("result", {}).get("findings", []):
            fid = f.get("finding_id")
            if fid in em and em[fid].get("evidence_count") != len(f.get("evidence", [])):
                report["errors"].append(f"{fid}: evidence_map count mismatch")
    report_path = out / "review_report.md"
    if not report_path.exists():
        report["errors"].append("missing /app/output/review_report.md")
    report["ok"] = report["ok"] and not report["errors"]
    _out_json(work / "selfcheck.json", report)
    for e in report["errors"]:
        print(f"[selfcheck][FAIL] {e}")
    for w in report["warnings"]:
        print(f"[selfcheck][WARN] {w}")
    print(f"[selfcheck] ok={report['ok']} (read-only; does not rewrite result.json)")


def main(argv=None):
    args = build_parser().parse_args(argv)
    cmd = args.command
    t0 = time.time()
    try:
        if cmd == "init":
            cmd_init(args)
        elif cmd == "show":
            cmd_show(args)
        elif cmd == "check":
            cmd_check(args)
        elif cmd == "build":
            cmd_build(args)
        elif cmd == "selfcheck":
            cmd_selfcheck(args)
        else:
            build_parser().print_help()
    except Exception as exc:  # 顶层捕获：退出码恒 0，不留下崩溃
        print(f"[error] {type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        print(f"[done] {time.time() - t0:.3f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
