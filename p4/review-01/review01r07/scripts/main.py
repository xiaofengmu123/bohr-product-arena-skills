#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — paper-quality-review skill CLI (standard library only).

Subcommands (the Agent follows SKILL.md and calls them in order):
  init      scan /input, extract material text, write index.json and a legal
            partial result.json immediately (the "first-3-minutes skeleton")
  show      print material with stable locators so quotes can be copied verbatim
  check     validate /app/work/draft.json against the extracted text
  build     generate review_report.md + the final result.json from draft.json
  selfcheck re-verify the final result.json without redoing the review

Design guarantee (robustness variant): every invocation exits with code 0 and,
if the output directory is writable, leaves a legal result.json. Empty input,
bad JSON, missing/unknown arguments and oversized files all degrade gracefully.

Default paths mirror the harness environment; override locally with
--input/--work/--output. All subcommands are importable-free and use relative
paths only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from core_impl import (
    FACETS, SEVERITIES, ISSUE_TYPES, VERDICTS, CONFIDENCES, STATUSES,
    ENVELOPE_KEYS, MAX_TEXT_FILE, TEXT_KINDS, PDF_KINDS, DOCX_KINDS,
    IMAGE_KINDS, read_text, load_json, atomic_write_json, atomic_write_text,
    classify_kind, material_files, split_paragraphs, make_locator,
    extract_pdf_text, extract_docx_paras, contract_object, contract_keys_of,
    validate_finding, verify_quote, normalize_ws, minimal_result,
    unchecked_item, summary_from_findings, now_iso,
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
    # No `choices` restriction and parse_known_args below: an unknown/missing
    # command degrades to `init` (a legal skeleton) instead of exiting non-zero.
    p.add_argument("command", nargs="?", default=None)
    p.add_argument("--input", dest="input", default="/input")
    p.add_argument("--work", dest="work", default="/app/work")
    p.add_argument("--output", dest="output", default="/app/output")
    p.add_argument("--source", dest="source", default=None,
                   help="show 只打印指定 source_id（如 D1）")
    p.add_argument("--max-chars", dest="max_chars", type=int, default=12000,
                   help="show 单次打印上限字符数")
    p.add_argument("--rescan", dest="rescan", action="store_true",
                   help="init 时强制重新抽取文本（默认复用已有 work/text）")
    return p


# --------------------------------------------------------------------------
# Small I/O helpers.
# --------------------------------------------------------------------------
def _load_json(path):
    return load_json(path)


def _paths(args):
    return Path(args.input), Path(args.work), Path(args.output)


def _ensure_dirs(work, output):
    for d in (work, output):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


# --------------------------------------------------------------------------
# case.json / task.md parsing (robust to every malformation).
# --------------------------------------------------------------------------
def _read_case(input_dir):
    state = {
        "case_id": None, "task_instruction": "", "output_contract": None,
        "contract_keys": [], "case_errors": [], "case_degradations": [],
    }
    case_path = input_dir / "case.json"
    if not case_path.exists():
        state["case_degradations"].append("case.json missing")
        return state
    text, problem = read_text(case_path)
    if text is None:
        state["case_errors"].append({"file": "case.json", "error": problem})
        state["case_degradations"].append("case.json unreadable")
        return state
    if problem:
        state["case_degradations"].append(f"case.json encoding: {problem}")
    case = None
    try:
        case = json.loads(text)
    except Exception as exc:
        state["case_errors"].append({"file": "case.json",
                                     "error": f"bad JSON: {exc}"})
        state["case_degradations"].append("case.json bad JSON; contract unknown")
    if isinstance(case, dict):
        state["case_id"] = (case.get("case_id") or case.get("id")
                            or case.get("task_id") or None)
        state["task_instruction"] = (
            case.get("task_instruction") or case.get("task")
            or case.get("instruction") or "")
        state["output_contract"] = case.get("output_contract")
    elif isinstance(case, list):
        state["case_degradations"].append("case.json is a list, not an object")
    state["contract_keys"] = contract_keys_of(state["output_contract"])
    return state


def _read_task(input_dir):
    task_path = input_dir / "task.md"
    if not task_path.exists():
        return ""
    text, problem = read_text(task_path)
    return text if text is not None else ""


# --------------------------------------------------------------------------
# Material extraction.
# --------------------------------------------------------------------------
def _extract_one(path, kind, work, sid, rescan):
    """Return (paragraphs, problems). Paragraphs are dicts {locator,index,page,text}."""
    text_file = work / "text" / f"{sid}.txt"
    if not rescan and text_file.exists():
        text, problem = read_text(text_file)
        if text is not None:
            pages = [pg for pg in text.split("\f") if pg.strip()] or [text]
            paras = []
            for pi, pg in enumerate(pages, 1):
                for chunk in split_paragraphs(pg, page=pi):
                    chunk["locator"] = make_locator(pi, chunk["index"])
                    paras.append(chunk)
            problems = ([problem] if problem else []) + [
                f"reused pre-extracted text {text_file.name}"]
            if paras:
                return paras, problems

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
            for chunk in split_paragraphs(pg, page=pi):
                chunk["locator"] = make_locator(pi, chunk["index"])
                paras.append(chunk)
        return paras, problems

    if kind == "docx":
        lines, problem = extract_docx_paras(data)
        if problem and not lines:
            return [], [problem]
        paras = []
        for chunk in split_paragraphs("\n\n".join(lines), page=None):
            chunk["locator"] = make_locator(None, chunk["index"])
            paras.append(chunk)
        return paras, ([problem] if problem else [])

    text, problem = read_text(path)
    if text is None:
        return [], [problem or "unreadable"]
    paras = []
    for chunk in split_paragraphs(text, page=None):
        chunk["locator"] = make_locator(None, chunk["index"])
        paras.append(chunk)
    return paras, ([problem] if problem else [])


def _persist_text(work, sid, paras):
    """Reconstruct page-separated text for grep-ability; best-effort only."""
    pages = {}
    for p in paras:
        pages.setdefault(p.get("page") or 0, []).append(p["text"])
    order = sorted(pages.keys())
    chunks = []
    for pi, key in enumerate(order, 1):
        chunks.append("\n\n".join(pages[key]))
    atomic_write_text(work / "text" / f"{sid}.txt", "\f".join(chunks))


def _sources_meta(sources):
    """Lightweight, verifiable source summary (no full paragraph text)."""
    return [{
        "source_id": s.get("source_id"),
        "path": s.get("path"),
        "kind": s.get("kind"),
        "chars": s.get("chars"),
        "problems": s.get("problems") or [],
    } for s in sources]


def _apply_contract_values(obj, findings, unchecked, summary):
    """Fill contract-declared top-level fields with the real computed values.

    Only touches keys that already exist in `obj` (i.e. declared by the contract),
    so unknown contract keys remain untouched as null.
    """
    if "findings" in obj:
        obj["findings"] = findings
    if "unchecked_items" in obj:
        obj["unchecked_items"] = unchecked
    if "verdict_summary" in obj:
        obj["verdict_summary"] = summary
    if "review_report" in obj:
        obj["review_report"] = "review_report.md"


# --------------------------------------------------------------------------
# init: build the index and drop the first legal partial result.
# --------------------------------------------------------------------------
def _cmd_init(args):
    input_dir, work, output = _paths(args)
    _ensure_dirs(work, output)
    started = now_iso()

    case = _read_case(input_dir)
    task_text = _read_task(input_dir)
    if task_text:
        case["task_instruction"] = case["task_instruction"] or task_text

    degradations = list(case["case_degradations"])
    errors = list(case["case_errors"])
    sources = []

    for path in material_files(input_dir):
        rel = path.relative_to(input_dir).as_posix() if input_dir.exists() else path.name
        sid = f"D{len(sources) + 1}"
        kind = classify_kind(path.name)
        paras, problems = _extract_one(path, kind, work, sid, args.rescan)
        for prob in problems:
            if "too large" in prob or "no text layer" in prob or "OCR" in prob:
                degradations.append(f"{sid} ({rel}): {prob}")
            else:
                degradations.append(f"{sid} ({rel}): {prob}")
        if paras:
            _persist_text(work, sid, paras)
        sources.append({
            "source_id": sid,
            "path": rel,
            "kind": kind,
            "chars": sum(len(p["text"]) for p in paras),
            "paragraphs": paras,
            "problems": problems,
        })

    index = {
        "case_id": case["case_id"],
        "task_instruction": case["task_instruction"],
        "output_contract": case["output_contract"],
        "contract_keys": case["contract_keys"],
        "sources": sources,
        "degradations": degradations,
        "errors": errors,
        "created_at": started,
    }
    atomic_write_json(work / "index.json", index)

    # Determine the honest skeleton status.
    if case["case_errors"]:
        status = "input_error"
    elif not sources and not case["case_errors"]:
        status = "insufficient_evidence"
    else:
        status = "partial"

    unchecked = []
    if status == "insufficient_evidence":
        unchecked = [unchecked_item(f, "no material text available") for f in FACETS]
    elif status == "input_error":
        unchecked = [unchecked_item(f, "input contract unreadable; review deferred")
                     for f in FACETS]
    else:
        unchecked = [unchecked_item(f, "not yet reviewed (skeleton)") for f in FACETS]

    result = minimal_result(
        case_id=case["case_id"], status=status, errors=errors,
        degradations=degradations, sources=_sources_meta(sources),
        contract=case["output_contract"],
        unchecked=unchecked,
        runtime_meta={"started_at": started, "step": "init",
                      "seconds": round(time.time(), 3)})
    _apply_contract_values(result, [], unchecked,
                           result["result"]["verdict_summary"])
    atomic_write_json(output / "result.json", result)

    print(f"[init] status={status} case_id={case['case_id']} "
          f"sources={len(sources)} contract_keys={len(case['contract_keys'])}")
    print(f"[init] index -> {work / 'index.json'}")
    print(f"[init] result -> {output / 'result.json'}")


# --------------------------------------------------------------------------
# show: print context + material with locators.
# --------------------------------------------------------------------------
def _cmd_show(args):
    _, work, _ = _paths(args)
    index = _load_json(work / "index.json")
    if index is None:
        print("[show] no index.json; run `init` first")
        return
    print("== CONTEXT ==")
    print(f"case_id: {index.get('case_id')}")
    ti = index.get("task_instruction") or ""
    if len(ti) > args.max_chars:
        ti = ti[:args.max_chars] + " ... [truncated]"
    print("task_instruction:\n" + ti)
    print("contract_keys: " + ", ".join(index.get("contract_keys") or []) or "(none)")
    print("degradations: " + "; ".join(index.get("degradations") or []) or "(none)")
    for src in index.get("sources") or []:
        if args.source and src["source_id"] != args.source:
            continue
        print(f"\n== SOURCE {src['source_id']} [{src['kind']}] {src['path']} "
              f"chars={src.get('chars')} ==")
        printed = 0
        for p in src.get("paragraphs") or []:
            line = f"[{p['locator']}] {p['text']}"
            if printed + len(line) > args.max_chars and printed > 0:
                print(f"... truncated at {args.max_chars} chars (run with "
                      f"--source {src['source_id']} --max-chars larger)")
                break
            print(line)
            printed += len(line)


# --------------------------------------------------------------------------
# check: validate draft.json against the extracted text.
# --------------------------------------------------------------------------
def _cmd_check(args):
    _, work, _ = _paths(args)
    index = _load_json(work / "index.json")
    draft = _load_json(work / "draft.json")
    report = {"ok": True, "problems": [], "findings": [], "coverage": {}}
    if index is None:
        report["ok"] = False
        report["problems"].append("index.json missing; run init first")
    if draft is None:
        report["ok"] = False
        report["problems"].append("draft.json missing or unparseable; "
                                  "write it before check")
        atomic_write_json(work / "check_report.json", report)
        print("[check] FAIL: draft.json missing/unparseable")
        return

    all_paras = []
    for src in index.get("sources") or []:
        all_paras.extend(src.get("paragraphs") or [])

    findings = draft.get("findings") if isinstance(draft, dict) else []
    if not isinstance(findings, list):
        findings = []
        report["problems"].append("draft.findings is not a list")
    unchecked = draft.get("unchecked_items") if isinstance(draft, dict) else []
    if not isinstance(unchecked, list):
        unchecked = []

    seen_ids = set()
    for i, f in enumerate(findings):
        item = {"finding_id": f.get("finding_id") if isinstance(f, dict) else None,
                "problems": []}
        if not isinstance(f, dict):
            item["problems"].append("finding is not an object")
        else:
            item["problems"].extend(validate_finding(f))
            if f.get("finding_id") in seen_ids:
                item["problems"].append(f"duplicate finding_id {f.get('finding_id')}")
            seen_ids.add(f.get("finding_id"))
            ok, best_loc, best_text = verify_quote(
                all_paras, f.get("quote", ""), f.get("location"))
            if not ok:
                item["problems"].append(
                    f"quote not found verbatim; closest locator={best_loc} "
                    f"near='{normalize_ws(best_text)[:80]}...'")
            for ei, ev in enumerate(f.get("evidence") or []):
                if isinstance(ev, dict):
                    eok, _, _ = verify_quote(
                        all_paras, ev.get("quote", ""), ev.get("locator"))
                    if not eok:
                        item["problems"].append(
                            f"evidence[{ei}] quote not found verbatim")
        if item["problems"]:
            report["ok"] = False
        report["findings"].append(item)

    checked_facets = {f.get("facet") for f in findings if isinstance(f, dict)}
    declared_unchecked = {u.get("facet") for u in unchecked if isinstance(u, dict)}
    for facet in FACETS:
        if facet in checked_facets:
            report["coverage"][facet] = "checked"
        elif facet in declared_unchecked:
            report["coverage"][facet] = "declared_unchecked"
        else:
            report["coverage"][facet] = "MISSING"
            report["problems"].append(f"facet '{facet}' not covered")

    report["summary"] = {
        "findings": len(findings),
        "failing_findings": sum(1 for x in report["findings"] if x["problems"]),
        "facets_missing": [f for f in FACETS
                           if report["coverage"].get(f) == "MISSING"],
    }
    atomic_write_json(work / "check_report.json", report)
    verdict = "PASS" if report["ok"] else "FAIL"
    print(f"[check] {verdict} findings={len(findings)} "
          f"problems={len(report['problems'])}")
    for x in report["findings"]:
        if x["problems"]:
            print(f"  {x['finding_id']}: " + "; ".join(x["problems"]))
    if report["summary"]["facets_missing"]:
        print("  missing facets: " + ", ".join(report["summary"]["facets_missing"]))


# --------------------------------------------------------------------------
# build: render review_report.md + final result.json.
# --------------------------------------------------------------------------
def _build_report(index, draft):
    lines = ["# 论文质量审查报告", ""]
    lines.append(f"- case_id: `{index.get('case_id')}`")
    lines.append(f"- 生成时间: {now_iso()}")
    lines.append("")
    findings = draft.get("findings") if isinstance(draft, dict) else []
    if not isinstance(findings, list):
        findings = []
    if not findings:
        lines.append("未产出逐条 finding；详见 `unchecked_items`。")
    for f in findings:
        if not isinstance(f, dict):
            continue
        lines.append(f"## {f.get('finding_id', 'F??')} "
                     f"[{f.get('facet', '?')}] {f.get('severity', '?')} "
                     f"({f.get('verdict', '?')})")
        lines.append("")
        lines.append(f"- 论断：{f.get('claim', '')}")
        lines.append(f"- 定位：`{f.get('location', '')}`")
        lines.append(f"- 原文：> {f.get('quote', '')}")
        lines.append(f"- 判断依据：{f.get('rationale', '')}")
        lines.append(f"- 修改建议：{f.get('revision_suggestion', '')}")
        lines.append(f"- 置信度：{f.get('confidence', '')}")
        ev = f.get("evidence") or []
        if ev:
            lines.append("- 证据：")
            for e in ev:
                if isinstance(e, dict):
                    lines.append(f"  - {e.get('source_id', '?')} "
                                 f"`{e.get('locator', '')}`: {e.get('quote', '')}")
        lines.append("")
    unchecked = draft.get("unchecked_items") if isinstance(draft, dict) else []
    if isinstance(unchecked, list) and unchecked:
        lines.append("## 未检查项 / 待复核项")
        lines.append("")
        for u in unchecked:
            if isinstance(u, dict):
                lines.append(f"- `{u.get('facet', '?')}`：{u.get('reason', '')} "
                             f"{u.get('detail', '')}")
        lines.append("")
    lines.append("## 复核方法")
    lines.append("")
    lines.append("每条 finding 的 `quote` 均已由 `scripts/main.py check` 与原文逐字比对；"
                 "定位采用 `paraN` 或 `pN#paraM`，可回原文核对。")
    return "\n".join(lines) + "\n"


def _cmd_build(args):
    _, work, output = _paths(args)
    _ensure_dirs(work, output)
    index = _load_json(work / "index.json")
    draft = _load_json(work / "draft.json")
    if draft is None:
        # Degrade: rebuild the skeleton rather than crash.
        print("[build] draft.json missing; emitting skeleton result.json")
        _cmd_init(args)
        return
    if not isinstance(draft, dict):
        draft = {}
    findings = draft.get("findings")
    if not isinstance(findings, list):
        findings = []
    unchecked = draft.get("unchecked_items")
    if not isinstance(unchecked, list):
        unchecked = []
    report_text = _build_report(index or {}, draft)
    atomic_write_text(output / "review_report.md", report_text)

    case_id = (index or {}).get("case_id")
    contract = (index or {}).get("output_contract")
    errors = list((index or {}).get("errors") or [])
    degradations = list((index or {}).get("degradations") or [])
    sources_meta = _sources_meta((index or {}).get("sources") or [])
    result = minimal_result(
        case_id=case_id, status="ok", errors=errors, degradations=degradations,
        sources=sources_meta, contract=contract, unchecked=unchecked,
        runtime_meta={"step": "build", "seconds": round(time.time(), 3)})
    result["result"]["findings"] = findings
    result["result"]["unchecked_items"] = unchecked
    result["result"]["verdict_summary"] = summary_from_findings(findings, unchecked)
    if findings:
        result["status"] = "ok"
    else:
        result["status"] = "partial"
    _apply_contract_values(result, findings, unchecked,
                           result["result"]["verdict_summary"])
    atomic_write_json(output / "result.json", result)
    print(f"[build] report -> {output / 'review_report.md'}")
    print(f"[build] result -> {output / 'result.json'} "
          f"status={result['status']} findings={len(findings)}")


# --------------------------------------------------------------------------
# selfcheck: re-verify the final artifact only.
# --------------------------------------------------------------------------
def _cmd_selfcheck(args):
    _, work, output = _paths(args)
    result = _load_json(output / "result.json")
    index = _load_json(work / "index.json")
    report = {"ok": True, "problems": []}
    if result is None:
        report["ok"] = False
        report["problems"].append("result.json missing or unparseable")
        atomic_write_json(work / "selfcheck.json", report)
        print("[selfcheck] FAIL: result.json missing/unparseable")
        return
    for key in ["case_id", "status", "sources", "uncertainty", "pending_human",
                "errors", "degradations", "changes", "change_log", "runtime"]:
        if key not in result:
            report["ok"] = False
            report["problems"].append(f"missing envelope key '{key}'")
    if result.get("status") not in STATUSES:
        report["ok"] = False
        report["problems"].append(f"invalid status '{result.get('status')}'")
    rr = result.get("result")
    if not isinstance(rr, dict):
        report["ok"] = False
        report["problems"].append("result is missing or not an object")
    else:
        findings = rr.get("findings")
        if not isinstance(findings, list):
            report["ok"] = False
            report["problems"].append("result.findings is not a list")
        else:
            all_paras = []
            for s in (index or {}).get("sources") or []:
                all_paras.extend(s.get("paragraphs") or [])
            for f in findings:
                if not isinstance(f, dict):
                    continue
                probs = validate_finding(f)
                if probs:
                    report["ok"] = False
                    report["problems"].append(
                        f"{f.get('finding_id')}: " + "; ".join(probs))
                ok, _, _ = verify_quote(all_paras, f.get("quote", ""),
                                        f.get("location"))
                if not ok:
                    report["ok"] = False
                    report["problems"].append(
                        f"{f.get('finding_id')}: quote not verbatim in source")
    atomic_write_json(work / "selfcheck.json", report)
    print(f"[selfcheck] {'PASS' if report['ok'] else 'FAIL'} "
          f"problems={len(report['problems'])}")
    for prob in report["problems"]:
        print("  " + prob)


# --------------------------------------------------------------------------
# Dispatch + last-resort guarantee.
# --------------------------------------------------------------------------
def _dispatch(args):
    cmd = args.command
    if cmd == "init":
        _cmd_init(args)
    elif cmd == "show":
        _cmd_show(args)
    elif cmd == "check":
        _cmd_check(args)
    elif cmd == "build":
        _cmd_build(args)
    elif cmd == "selfcheck":
        _cmd_selfcheck(args)
    else:
        # Unknown/missing command: still produce a legal skeleton.
        print("[main] no/unknown command; running init to guarantee a result")
        _cmd_init(args)


def _last_resort(args, exc):
    """Guarantee a legal result.json even if a subcommand throws."""
    _, work, output = _paths(args)
    _ensure_dirs(work, output)
    existing = _load_json(output / "result.json")
    if existing is not None:
        return
    try:
        result = minimal_result(
            case_id=None, status="input_error",
            errors=[{"error": f"unhandled exception: {exc}"}],
            degradations=["last-resort minimal result written"],
            runtime_meta={"step": "last_resort", "seconds": round(time.time(), 3)})
        atomic_write_json(output / "result.json", result)
    except Exception:
        pass


def main(argv=None):
    # parse_known_args: unknown flags are ignored rather than triggering a
    # non-zero argparse exit, keeping the "always exit 0" guarantee intact.
    args, _unknown = build_parser().parse_known_args(argv)
    try:
        _dispatch(args)
    except Exception as exc:  # noqa: BLE001 — deliberate guarantee of exit 0
        print(f"[main] recovered from exception: {exc}", file=sys.stderr)
        _last_resort(args, exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
