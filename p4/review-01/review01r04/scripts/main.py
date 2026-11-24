#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py —— 证据台账式论文质量审查 Skill 主入口（仅标准库）。

子命令（Agent 按 SKILL.md 顺序调用）：
  init      扫描 /input，建索引，立刻落盘 partial result.json（前 3 分钟内必须完成）
  show      打印带 locator 的原文，供 Agent 逐字引用
  check     校验 /app/work/draft.json（quote 命中 / 字段 / 枚举 / 覆盖）
  build     读取 draft.json，生成 review_report.md + 最终 result.json
  selfcheck 只复检 /app/output/result.json 与主产物，不重做

默认路径与评测环境一致：/input、/app/work、/app/output；
本地可用 --input/--work/--output 覆盖。所有子命令退出码恒为 0。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from core_impl import (
    FACETS, SEVERITIES, ISSUE_TYPES, VERDICTS, CONFIDENCES, STATUSES,
    OVERALL_VERDICTS, ANCHORS, SEVERITY_RANK, MAX_TEXT_FILE,
    TEXT_KINDS, PDF_KINDS, DOCX_KINDS, IMAGE_KINDS,
    read_text, norm_text, split_paragraphs, extract_pdf_text, extract_docx_paras,
    contract_keys_of, echo_contract, validate_finding,
    empty_coverage, empty_verdict_summary, compute_verdict_summary, build_result,
)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="证据台账式论文质量审查 Skill 助手（init/show/check/build/selfcheck）")
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


# ---------------- 基础工具 ----------------
def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _elapsed(start_iso):
    try:
        dt = datetime.strptime(start_iso, "%Y-%m-%dT%H:%M:%S%z")
        return max(0.0, (datetime.now(dt.tzinfo) - dt).total_seconds())
    except Exception:
        return 0.0


def _out_json(path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def _out_text(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)
    return path


def _load_json(path):
    p = Path(path)
    if not p.exists():
        return None
    text, _ = read_text(p)
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


# ---------------- 材料抽取 ----------------
def _extract_material(path: Path, kind: str):
    if kind in ("image", "other"):
        return [], [f"{kind}: no text layer; needs OCR / external tool"]
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [], [f"read error: {exc}"]
    if kind == "pdf":
        pages, problems = extract_pdf_text(data)
        paras = []
        if len(pages) == 1:
            paras = split_paragraphs(pages[0], page=None)
        else:
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


def _save_extracted(work: Path, sid: str, paras):
    """把段落还原成带 \f 分页的预抽取文本，供后续复用。"""
    by_page, order = {}, []
    for p in paras:
        pg = p.get("page")
        if pg not in by_page:
            by_page[pg] = []
            order.append(pg)
        by_page[pg].append(p["text"])
    parts = ["\n\n".join(by_page[pg]) for pg in order]
    text = "\f".join(parts) if len(order) > 1 else (parts[0] if parts else "")
    _out_text(work / "text" / f"{sid}.txt", text)


def _extract_or_reuse(path: Path, kind: str, work: Path, sid: str, rescan: bool):
    pre = work / "text" / f"{sid}.txt"
    if not rescan and pre.exists():
        text, problem = read_text(pre)
        if text is not None:
            pages = [pg for pg in text.split("\f") if pg.strip()] or \
                    ([text] if text.strip() else [])
            paras = []
            for pi, pg in enumerate(pages, 1):
                paras.extend(split_paragraphs(pg, page=pi))
            problems = [f"reused pre-extracted text {pre.name}"] + \
                       ([problem] if problem else [])
            if paras:
                return paras, problems
    paras, problems = _extract_material(path, kind)
    if paras:
        try:
            _save_extracted(work, sid, paras)
        except OSError as exc:
            problems.append(f"could not cache extracted text: {exc}")
    return paras, problems


# ---------------- 输入扫描 ----------------
def _scan_input(input_dir: Path):
    case_id, contract, instruction = None, None, ""
    errors, degradations = [], []
    case_path = input_dir / "case.json"
    if case_path.exists():
        text, problem = read_text(case_path)
        if text is None:
            errors.append({"file": "case.json", "error": problem or "unreadable"})
            degradations.append("case.json unreadable; contract unknown")
        else:
            if problem:
                degradations.append(f"case.json encoding: {problem}")
            try:
                case = json.loads(text)
            except Exception as exc:
                errors.append({"file": "case.json", "error": f"bad JSON: {exc}"})
                degradations.append("case.json bad JSON; contract unknown")
            else:
                if isinstance(case, dict):
                    case_id = (case.get("case_id") or case.get("id")
                               or case.get("task_id"))
                    instruction = (case.get("task_instruction")
                                   or case.get("task")
                                   or case.get("instruction") or "")
                    contract = case.get("output_contract")
    else:
        degradations.append("case.json missing")
    return case_id, contract, instruction, errors, degradations


def _scan_materials(input_dir: Path, work: Path, rescan: bool):
    sources, degradations = [], []
    for sid, path in enumerate(_material_files(input_dir), 1):
        source_id = f"D{sid}"
        kind = _classify_kind(path.name)
        rec = {"source_id": source_id, "path": str(path), "name": path.name,
               "kind": kind, "paragraphs": [], "problems": []}
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if size > MAX_TEXT_FILE:
            rec["problems"].append(f"file {size} bytes > {MAX_TEXT_FILE}; skipped")
            degradations.append(f"{source_id}: oversized file skipped")
            sources.append(rec)
            continue
        paras, problems = _extract_or_reuse(path, kind, work, source_id, rescan)
        rec["problems"] = problems
        for p in paras:
            p["source_id"] = source_id
        rec["paragraphs"] = paras
        if kind in ("image", "other"):
            degradations.append(f"{source_id}: {kind} no text layer; needs OCR / external tool")
        if not paras:
            degradations.append(f"{source_id}: no paragraphs extracted")
        sources.append(rec)
    return sources, degradations


def _status_after_init(sources, errors):
    if any(isinstance(e, dict) and "bad JSON" in str(e.get("error", "")) for e in errors):
        return "input_error"
    if not sources:
        return "insufficient_evidence"
    return "partial"


# ---------------- 子命令 ----------------
def cmd_init(input_dir, work_dir, output_dir, rescan=False):
    work, out = Path(work_dir), Path(output_dir)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    (work / "text").mkdir(parents=True, exist_ok=True)

    case_id, contract, instruction, scan_errors, scan_degrad = _scan_input(Path(input_dir))
    sources, mat_degrad = _scan_materials(Path(input_dir), work, rescan)
    errors = scan_errors
    degradations = scan_degrad + mat_degrad

    index = {
        "case_id": case_id,
        "task_instruction": instruction,
        "output_contract": contract,
        "contract_keys": contract_keys_of(contract),
        "sources": sources,
        "errors": errors,
        "degradations": degradations,
        "created_at": _now(),
    }
    _out_json(work / "index.json", index)

    status = _status_after_init(sources, errors)
    runtime = {"started_at": _now(), "finished_at": None, "elapsed_s": 0.0,
               "revision": 0, "command": "init",
               "note": "wall-clock estimate; platform records authoritative budgets"}
    result = build_result(
        case_id, status, contract, sources, [], [],
        empty_coverage(), empty_verdict_summary(),
        {"source_conflicts": [], "ambiguous": [], "unverifiable": [],
         "overall_confidence": "low"},
        [], errors, degradations,
        [{"id": "L01", "scope": "all",
          "limitation": "init skeleton only; no findings written yet",
          "impact": "coverage marked not_checked",
          "mitigation": "proceed to show/check/build"}],
        [{"time": _now(), "version": 0, "what": "init partial skeleton"}],
        runtime,
        {"review_report": "review_report.md"},
    )
    _out_json(out / "result.json", result)
    print(f"[init] status={status} sources={len(sources)} "
          f"degradations={len(degradations)} errors={len(errors)}")
    return 0


def cmd_show(work_dir, source=None, max_chars=12000):
    index = _load_json(Path(work_dir) / "index.json")
    if not index:
        print("[show] index.json missing; run init first")
        return 0
    print("case_id:", index.get("case_id"))
    print("contract_keys:", index.get("contract_keys"))
    print("task_instruction:", (index.get("task_instruction") or "")[:500])
    used = 0
    budget = max_chars
    for src in index.get("sources", []):
        if source and src.get("source_id") != source:
            continue
        paras = src.get("paragraphs", [])
        print(f"\n=== {src.get('source_id')} | {src.get('name')} | "
              f"kind={src.get('kind')} | paras={len(paras)}")
        for p in paras:
            line = f"[{p.get('source_id')}#{p.get('locator')}] {p.get('text')}"
            if used + len(line) > budget:
                print(f"... (truncated at {budget} chars; raise --max-chars)")
                return 0
            print(line)
            used += len(line) + 1
    return 0


def _check_draft(draft, paragraphs):
    if not isinstance(draft, dict):
        return {"status": "FAIL", "reason": "draft is not a JSON object"}
    findings = draft.get("findings") or []
    unchecked = draft.get("unchecked_items") or []
    cov = draft.get("coverage") or {}
    fails, warns, pass_cnt = [], [], 0
    seen = set()
    for f in findings:
        probs, _ = validate_finding(f, paragraphs)
        if probs:
            fails.append({"finding_id": f.get("finding_id", "?"), "problems": probs})
        else:
            pass_cnt += 1
        fid = f.get("finding_id")
        if fid in seen:
            warns.append(f"duplicate finding_id: {fid}")
        seen.add(fid)
    for facet in FACETS:
        fcnt = sum(1 for f in findings if f.get("facet") == facet)
        in_unchecked = any(u.get("facet") == facet for u in unchecked)
        c = cov.get(facet)
        if fcnt == 0 and not in_unchecked and \
                not (isinstance(c, dict) and c.get("status") in
                     ("not_applicable", "unverifiable")):
            fails.append({"facet": facet,
                          "problems": ["facet not covered: add finding, unchecked item, "
                                       "or coverage status not_applicable/unverifiable"]})
        elif fcnt == 0 and isinstance(c, dict) and c.get("status") == "checked":
            warns.append(f"facet {facet} marked checked but has no findings")
    return {"status": "PASS" if not fails else "FAIL",
            "findings_checked": len(findings),
            "findings_pass": pass_cnt,
            "failures": fails,
            "warnings": warns}


def cmd_check(work_dir, output_dir):
    work, out = Path(work_dir), Path(output_dir)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    index = _load_json(work / "index.json") or {}
    paragraphs = []
    for s in index.get("sources", []):
        paragraphs.extend(s.get("paragraphs", []))
    draft = _load_json(work / "draft.json")
    if not draft:
        report = {"status": "FAIL", "reason": "draft.json missing; write it first"}
        _out_json(work / "check_report.json", report)
        print("[check] FAIL: draft.json missing")
        return 0
    report = _check_draft(draft, paragraphs)
    _out_json(work / "check_report.json", report)
    for w in report.get("warnings", []):
        print("[check] WARN:", w)
    for f in report.get("failures", []):
        print("[check] FAIL:", json.dumps(f, ensure_ascii=False))
    print(f"[check] {report['status']}: {report['findings_pass']}/"
          f"{report['findings_checked']} findings pass")
    return 0


def _merge_coverage(draft_cov, findings_ok):
    anchor_ids = {}
    for f in findings_ok:
        a, fac = f.get("check_anchor"), f.get("facet")
        if fac in ANCHORS and a in ANCHORS[fac]:
            anchor_ids.setdefault((fac, a), []).append(f.get("finding_id"))
    cov = empty_coverage()
    for facet in FACETS:
        dc = draft_cov.get(facet) if isinstance(draft_cov, dict) else None
        has_finding = any(f.get("facet") == facet for f in findings_ok)
        cov[facet]["status"] = (dc.get("status") if isinstance(dc, dict) and dc.get("status")
                                else ("checked" if has_finding else "not_checked"))
        cov[facet]["reason"] = (dc.get("reason") if isinstance(dc, dict) else "")
        anchors = (dc.get("anchors") if isinstance(dc, dict) else None) or {}
        for a in ANCHORS[facet]:
            ids = list(anchor_ids.get((facet, a), []))
            av = anchors.get(a) if isinstance(anchors, dict) else None
            if isinstance(av, dict):
                merged = list(av.get("finding_ids") or [])
                for i in ids:
                    if i not in merged:
                        merged.append(i)
                cov[facet]["anchors"][a] = {
                    "status": av.get("status") or ("checked" if merged else "not_checked"),
                    "finding_ids": merged,
                    "reason": av.get("reason", "")}
            else:
                cov[facet]["anchors"][a] = {
                    "status": "checked" if ids else "not_checked",
                    "finding_ids": ids}
    return cov


def _facets_covered(cov, findings, unchecked):
    for facet in FACETS:
        has = any(f.get("facet") == facet for f in findings)
        has_u = any(u.get("facet") == facet for u in unchecked)
        st = (cov.get(facet) or {}).get("status")
        if not has and not has_u and st not in ("not_applicable", "unverifiable", "checked"):
            return False
    return True


def _render_report(result):
    L = ["# 论文质量审查报告", ""]
    L.append(f"- case_id: `{result.get('case_id')}`")
    L.append(f"- status: `{result.get('status')}`")
    vs = (result.get("result") or {}).get("verdict_summary") or {}
    L.append(f"- overall: `{vs.get('overall')}`")
    L.append(f"- counts: {json.dumps(vs.get('counts'), ensure_ascii=False)}")
    L.append("")
    findings = (result.get("result") or {}).get("findings") or []
    L.append(f"## Findings（{len(findings)} 条）")
    for f in findings:
        L.append("")
        L.append(f"### {f.get('finding_id')} — {f.get('facet')} / "
                 f"{f.get('severity')} / {f.get('issue_type')} / {f.get('verdict')}")
        L.append(f"- claim: {f.get('claim')}")
        L.append(f"- location: `{f.get('location')}`")
        L.append(f"- quote: > {(f.get('quote') or '').replace(chr(10), ' ')}")
        L.append(f"- rationale: {f.get('rationale')}")
        L.append(f"- revision: {f.get('revision_suggestion')}")
        L.append(f"- confidence: {f.get('confidence')}")
        unc = f.get("uncertainty")
        if unc:
            L.append(f"- uncertainty: {json.dumps(unc, ensure_ascii=False)}")
        ev = f.get("evidence") or []
        if ev:
            L.append("- evidence:")
            for e in ev:
                L.append(f"  - {e.get('source_id')} @ {e.get('locator')}: "
                         f"> {(e.get('quote') or '').replace(chr(10), ' ')}")
    unchecked = (result.get("result") or {}).get("unchecked_items") or []
    L.append("")
    L.append(f"## Unchecked items（{len(unchecked)} 条）")
    for u in unchecked:
        L.append(f"- facet={u.get('facet')}: {u.get('item')} ({u.get('reason')})")
    cov = (result.get("result") or {}).get("coverage") or {}
    L.append("")
    L.append("## Coverage")
    for facet in FACETS:
        c = cov.get(facet) or {}
        L.append(f"- {facet}: `{c.get('status')}` {c.get('reason')}")
    for section in ("uncertainty", "degradations", "limitations", "pending_human",
                    "errors", "change_log"):
        data = result.get(section)
        if data:
            L.append("")
            L.append(f"## {section}")
            L.append("```json")
            L.append(json.dumps(data, ensure_ascii=False, indent=2))
            L.append("```")
    L.append("")
    return "\n".join(L)


def cmd_build(work_dir, output_dir):
    work, out = Path(work_dir), Path(output_dir)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    index = _load_json(work / "index.json") or {}
    draft = _load_json(work / "draft.json")
    paragraphs = []
    for s in index.get("sources", []):
        paragraphs.extend(s.get("paragraphs", []))
    started = ((index.get("created_at") or _now()) if isinstance(index, dict)
               else _now())

    if not isinstance(draft, dict):
        result = build_result(
            index.get("case_id"), "partial", index.get("output_contract"),
            index.get("sources", []), [], [], empty_coverage(),
            empty_verdict_summary(),
            {"source_conflicts": [], "ambiguous": [], "unverifiable": [],
             "overall_confidence": "low"},
            [{"item": "no draft.json; review not performed",
              "facet": None, "reason": "draft.json missing"}],
            list(index.get("errors", [])),
            list(index.get("degradations", [])),
            [{"id": "L01", "scope": "all",
              "limitation": "no draft provided", "impact": "no findings",
              "mitigation": "write /app/work/draft.json and re-run build"}],
            [{"time": _now(), "version": 1, "what": "build without draft; partial result"}],
            {"started_at": started, "finished_at": _now(),
             "elapsed_s": round(_elapsed(started), 1), "revision": 1,
             "command": "build", "note": "wall-clock estimate"},
            {"review_report": "review_report.md"})
        _out_json(out / "result.json", result)
        _out_text(out / "review_report.md", _render_report(result))
        print("[build] no draft; wrote partial result.json")
        return 0

    findings_ok, findings_bad = [], []
    for f in (draft.get("findings") or []):
        probs, _ = validate_finding(f, paragraphs)
        if probs:
            findings_bad.append({"finding_id": f.get("finding_id", "?"),
                                 "problems": probs, "original": f})
        else:
            findings_ok.append(f)
    findings_ok.sort(key=lambda f: (
        SEVERITY_RANK.get(f.get("severity"), 99),
        FACETS.index(f.get("facet")) if f.get("facet") in FACETS else 99,
        f.get("finding_id", "")))

    unchecked = list(draft.get("unchecked_items") or [])
    for bad in findings_bad:
        orig = bad.get("original") or {}
        unchecked.append({
            "item": f"finding {bad.get('finding_id')} dropped: "
                    + "; ".join(bad.get("problems", [])),
            "facet": orig.get("facet"),
            "reason": "failed verbatim/schema check at build"})

    cov = _merge_coverage(draft.get("coverage") or {}, findings_ok)
    vs = compute_verdict_summary(
        findings_ok,
        overall=(draft.get("verdict_summary") or {}).get("overall"))

    status = draft.get("status")
    if status not in STATUSES:
        status = "ok" if _facets_covered(cov, findings_ok, unchecked) else "partial"

    change_log = list(draft.get("change_log") or [])
    change_log.append({"time": _now(), "version": len(change_log) + 1,
                       "what": f"build: {len(findings_ok)} findings, "
                               f"{len(unchecked)} unchecked, status={status}"})
    runtime = {"started_at": started, "finished_at": _now(),
               "elapsed_s": round(_elapsed(started), 1),
               "revision": len(change_log),
               "command": "build",
               "note": "wall-clock estimate; platform records authoritative budgets"}
    sources = [{"source_id": s.get("source_id"), "path": s.get("path"),
                "name": s.get("name"), "kind": s.get("kind"),
                "paragraphs": len(s.get("paragraphs", [])),
                "problems": s.get("problems", [])}
               for s in index.get("sources", [])]

    result = build_result(
        index.get("case_id"), status, index.get("output_contract"), sources,
        findings_ok, unchecked, cov, vs,
        draft.get("uncertainty") or {"source_conflicts": [], "ambiguous": [],
                                     "unverifiable": [], "overall_confidence": "low"},
        draft.get("pending_human") or [],
        list(index.get("errors", [])) + list(draft.get("errors") or []),
        list(index.get("degradations", [])) + list(draft.get("degradations") or []),
        draft.get("limitations") or [],
        change_log, runtime,
        {"review_report": "review_report.md"})
    _out_json(out / "result.json", result)
    _out_text(out / "review_report.md", _render_report(result))
    print(f"[build] status={status} findings={len(findings_ok)} "
          f"unchecked={len(unchecked)} dropped={len(findings_bad)}")
    return 0


def cmd_selfcheck(work_dir, output_dir):
    work, out = Path(work_dir), Path(output_dir)
    work.mkdir(parents=True, exist_ok=True)
    res = _load_json(out / "result.json")
    if not res:
        report = {"status": "FAIL", "reason": "result.json missing or invalid JSON"}
        _out_json(work / "selfcheck.json", report)
        print("[selfcheck] FAIL: result.json missing")
        return 0
    index = _load_json(work / "index.json") or {}
    paragraphs = []
    for s in index.get("sources", []):
        paragraphs.extend(s.get("paragraphs", []))
    checks = []
    for k in ("case_id", "status", "sources", "uncertainty", "pending_human",
              "change_log", "result"):
        checks.append({"check": f"envelope.{k}", "ok": k in res})
    checks.append({"check": "status_enum", "ok": res.get("status") in STATUSES})
    findings = ((res.get("result") or {}).get("findings") or [])
    for f in findings:
        probs, _ = validate_finding(f, paragraphs)
        checks.append({"check": f"finding.{f.get('finding_id', '?')}.valid",
                       "ok": not probs, "detail": probs})
    for k, v in ((res.get("artifacts") or {})).items():
        checks.append({"check": f"artifact.{k}", "ok": (out / v).exists()})
    report = {"status": "PASS" if all(c["ok"] for c in checks) else "FAIL",
              "checks": checks}
    _out_json(work / "selfcheck.json", report)
    for c in checks:
        if not c["ok"]:
            print("[selfcheck] FAIL:", json.dumps(c, ensure_ascii=False))
    print(f"[selfcheck] {report['status']} ({sum(1 for c in checks if c['ok'])}/"
          f"{len(checks)} checks pass)")
    return 0


# ---------------- 分发 ----------------
def run_command(command, input_dir, work_dir, output_dir,
                source=None, max_chars=12000, rescan=False):
    if command == "init":
        return cmd_init(input_dir, work_dir, output_dir, rescan=rescan)
    if command == "show":
        return cmd_show(work_dir, source=source, max_chars=max_chars)
    if command == "check":
        return cmd_check(work_dir, output_dir)
    if command == "build":
        return cmd_build(work_dir, output_dir)
    if command == "selfcheck":
        return cmd_selfcheck(work_dir, output_dir)
    print("usage: main.py {init|show|check|build|selfcheck} [--input ...] "
          "[--work ...] [--output ...]")
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.command:
        build_parser().print_help()
        return 0
    try:
        return run_command(args.command, args.input, args.work, args.output,
                           source=args.source, max_chars=args.max_chars,
                           rescan=args.rescan)
    except SystemExit:
        raise
    except Exception as exc:  # 顶层兜底：绝不因异常崩溃
        work, out = Path(args.work), Path(args.output)
        work.mkdir(parents=True, exist_ok=True)
        out.mkdir(parents=True, exist_ok=True)
        try:
            minimal = {
                "case_id": None, "status": "input_error",
                "sources": [], "uncertainty": {}, "pending_human": [],
                "errors": [{"stage": args.command, "error": str(exc)}],
                "degradations": [f"{args.command} crashed: {exc}"],
                "change_log": [{"time": _now(), "version": 0,
                                "what": "top-level fallback on exception"}],
                "runtime": {"started_at": _now(), "finished_at": _now(),
                            "elapsed_s": 0.0, "revision": 0,
                            "command": args.command},
                "result": {"findings": [], "unchecked_items": [],
                           "coverage": empty_coverage(),
                           "verdict_summary": empty_verdict_summary()},
            }
            _out_json(out / "result.json", minimal)
        except Exception:
            pass
        print(f"[{args.command}] handled exception: {exc}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
