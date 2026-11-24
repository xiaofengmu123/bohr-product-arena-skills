#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""论文质量审查 Skill 主入口（仅标准库）。

子命令（Agent 按 SKILL.md 顺序调用，所有子命令退出码恒为 0）：
  init      扫描 /input，建索引与 plan/draft 中间产物，立刻落盘 partial result.json
  show      打印带 locator 的原文，供 Agent 逐字引用
  plan      打印 / 重建 plan.json（分面适用性计划）
  draft     打印 / 重建 draft.json（分面审查工作稿）
  check     校验 draft.json + plan.json（quote 逐字命中 / 字段 / 枚举 / 覆盖）
  build     读取 draft.json，生成 review_report.md + evidence_map.json + 最终 result.json
  selfcheck 只复检最终 result.json 与主产物，不重做

默认路径与评测环境一致：/input、/app/work、/app/output；
本地可用 --input/--work/--output 覆盖。脚本只依赖 Python 标准库。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True  # 运行时不落盘 __pycache__/.pyc

from core_impl import (
    FACETS, OVERALL_VERDICTS, ENVELOPE_KEYS, MAX_TEXT_FILE, TEXT_KINDS,
    PDF_KINDS, DOCX_KINDS, IMAGE_KINDS, read_text, split_paragraphs,
    extract_pdf_text, extract_docx_paras, contract_keys_of,
    merge_contract_into, validate_finding, quote_hit, utcnow_iso,
    auto_facet_applicability, make_draft_template, make_partial_result,
    render_report,
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
        description="论文质量审查 Skill 助手（init/show/plan/draft/check/build/selfcheck）")
    p.add_argument("command", nargs="?", default=None,
                   choices=["init", "show", "plan", "draft", "check",
                            "build", "selfcheck"])
    p.add_argument("--input", dest="input", default="/input")
    p.add_argument("--work", dest="work", default="/app/work")
    p.add_argument("--output", dest="output", default="/app/output")
    p.add_argument("--source", dest="source", default=None,
                   help="show 只打印指定 source_id（如 D1）")
    p.add_argument("--max-chars", dest="max_chars", type=int, default=12000,
                   help="show 单次打印上限字符数")
    p.add_argument("--rescan", dest="rescan", action="store_true",
                   help="init 时强制重新抽取文本（默认复用 work/text）")
    p.add_argument("--new", dest="new", action="store_true",
                   help="plan/draft 命令重建模板")
    return p


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
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


def _log(change_log: list, step: str, detail: str):
    change_log.append({"ts": utcnow_iso(), "step": step, "detail": detail})
    return change_log


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


def _extract_material(path: Path, kind: str):
    if kind in ("image", "other"):
        return [], [f"{kind}: no text layer; needs OCR / external tool"]
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [], [f"read error: {exc}"]
    if len(data) > MAX_TEXT_FILE:
        return [], [f"file {len(data)} bytes > {MAX_TEXT_FILE} bytes; skipped"]
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
        paras = split_paragraphs(text, page=None)
        return paras, ([problem] if problem else [])
    text, problem = read_text(path)
    if text is None:
        return [], [problem or "unreadable"]
    paras = split_paragraphs(text, page=None)
    return paras, ([problem] if problem else [])


def _extract_or_reuse(path: Path, kind: str, work: Path, sid: str,
                      rescan: bool):
    pre = work / "text" / f"{sid}.txt"
    if not rescan and pre.exists():
        text, problem = read_text(pre)
        if text is not None:
            pages = [p for p in text.split("\f") if p.strip()] or [text]
            paras = []
            for pi, pg in enumerate(pages, 1):
                paras.extend(split_paragraphs(pg, page=pi))
            problems = ([problem] if problem else []) + \
                [f"reused pre-extracted text {pre.name}"]
            if paras:
                return paras, problems
    return _extract_material(path, kind)


def _scan_case(input_dir: Path):
    """读取 case.json，返回 (case_id, task_instruction, output_contract,
    task_md, case_error, case_missing)。

    仅当 case.json 存在但损坏/坏 JSON 时 case_error 非空（对应 input_error）；
    case.json 缺失只置 case_missing，由材料是否可读决定是否 insufficient_evidence。
    """
    case_error = None
    case_missing = False
    case_id, task_instruction, output_contract, task_md = \
        None, "", None, ""
    case_path = input_dir / "case.json"
    if case_path.exists():
        text, problem = read_text(case_path)
        if text is None:
            case_error = f"case.json unreadable: {problem}"
        else:
            case = None
            try:
                case = json.loads(text)
            except Exception as exc:
                case_error = f"case.json bad JSON: {exc}"
            if case_error is None and not isinstance(case, dict):
                case_error = "case.json is not a JSON object"
            if isinstance(case, dict):
                case_id = (case.get("case_id") or case.get("id")
                           or case.get("task_id"))
                task_instruction = (case.get("task_instruction")
                                    or case.get("task")
                                    or case.get("instruction") or "")
                output_contract = case.get("output_contract")
    else:
        case_missing = True
    task_path = input_dir / "task.md"
    if task_path.exists():
        ttext, _ = read_text(task_path)
        task_md = ttext or ""
    return (case_id, task_instruction, output_contract, task_md,
            case_error, case_missing)


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------
def cmd_init(args):
    input_dir = Path(args.input)
    work = Path(args.work)
    output = Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)

    started = time.time()
    case_id, task_instruction, output_contract, task_md, case_error, \
        case_missing = _scan_case(input_dir)
    contract_keys = contract_keys_of(output_contract)
    degradations = []
    errors = []
    if case_error:
        errors.append({"file": "case.json", "error": case_error})
        degradations.append(case_error)
    if case_missing:
        degradations.append("case.json missing")

    materials = []
    paras = []
    for idx, path in enumerate(_material_files(input_dir), 1):
        sid = f"D{idx}"
        kind = _classify_kind(path.name)
        mparas, problems = _extract_or_reuse(path, kind, work, sid,
                                             args.rescan)
        # 逐段赋 locator
        seq = 0
        mparas_located = []
        for para in mparas:
            seq += 1
            if para.get("page"):
                locator = f"p{para['page']}#para{seq}"
            else:
                locator = f"para{seq}"
            mparas_located.append({
                "source_id": sid, "locator": locator,
                "page": para.get("page"), "seq": seq,
                "text": para["text"],
            })
        paras.extend(mparas_located)
        # 保存规范化文本供后续复用
        if mparas:
            pages = {}
            for para in mparas:
                pg = para.get("page") or 1
                pages.setdefault(pg, []).append(para["text"])
            ordered = [pages[k] for k in sorted(pages)]
            norm = "\f".join("\n\n".join(chunk) for chunk in ordered)
            pre = work / "text" / f"{sid}.txt"
            pre.parent.mkdir(parents=True, exist_ok=True)
            try:
                pre.write_text(norm, encoding="utf-8", newline="\n")
            except OSError as exc:
                degradations.append(f"write {pre.name} failed: {exc}")
        readable = bool(mparas)
        problems = problems or []
        if not readable:
            degradations.append(f"{sid} ({path.name}): no text extracted")
        materials.append({
            "source_id": sid,
            "path": str(path),
            "rel_path": path.name,
            "kind": kind,
            "size": path.stat().st_size if path.exists() else None,
            "readable": readable,
            "paragraph_count": len(mparas_located),
            "char_count": sum(len(p["text"]) for p in mparas_located),
            "problems": problems,
        })

    index = {
        "case_id": case_id or "unknown",
        "task_instruction": task_instruction,
        "task_md_digest": (task_md[:4000] + ("…" if len(task_md) > 4000
                                             else "")) if task_md else "",
        "output_contract": output_contract,
        "contract_keys": contract_keys,
        "case_error": case_error,
        "case_missing": case_missing,
        "material_count": len(materials),
        "readable_material_count": sum(1 for m in materials if m["readable"]),
        "materials": materials,
        "paras": paras,
        "para_texts": [p["text"] for p in paras],
        "degradations": degradations,
        "errors": errors,
        "generated_at": utcnow_iso(),
    }
    _out_json(work / "index.json", index)

    task_text = (task_instruction or "") + "\n" + (task_md or "")
    plan = {
        "case_id": case_id or "unknown",
        "task_instruction_digest": (task_instruction[:1200] + "…"
                                    if len(task_instruction) > 1200
                                    else task_instruction),
        "contract_keys": contract_keys,
        "material_count": len(materials),
        "readable_material_count": index["readable_material_count"],
        "budget": {"total_seconds": 900, "model_requests": 64,
                   "finalize_at_seconds": 780, "first_drop_seconds": 180},
        "facets": auto_facet_applicability(task_text, paras),
        "scope_notes": "",
        "false_positive_guardrails": [
            "学科写作惯例/期刊格式差异/作者有意取舍不判为缺陷",
            "无法逐字定位的论断不写成 finding，列为 unchecked 或 uncertainty",
            "被引文献主张无法核验时只标 citation_support=not_checked，不臆断",
        ],
        "created_at": utcnow_iso(),
    }
    _out_json(work / "plan.json", plan)

    draft_path = work / "draft.json"
    if not draft_path.exists():
        _out_json(draft_path, make_draft_template(case_id or "unknown"))

    change_log = _log([], "init", "index + plan + draft template written")
    runstate = {
        "case_id": case_id or "unknown",
        "started_at": utcnow_iso(),
        "change_log": change_log,
    }
    _out_json(work / "runstate.json", runstate)

    partial = make_partial_result(case_id or "unknown", output_contract,
                                   contract_keys)
    partial["sources"] = [
        {"source_id": m["source_id"], "path": m["rel_path"], "kind": m["kind"],
         "readable": m["readable"], "paragraph_count": m["paragraph_count"],
         "char_count": m["char_count"], "problems": m["problems"]}
        for m in materials
    ]
    partial["degradations"] = list(degradations)
    partial["errors"] = list(errors)
    partial["change_log"] = list(change_log)
    partial["runtime"] = {
        "started_at": runstate["started_at"], "finished_at": utcnow_iso(),
        "seconds": round(time.time() - started, 2), "finalized": False,
    }
    if case_error:
        partial["status"] = "input_error"
    elif index["readable_material_count"] == 0:
        partial["status"] = "insufficient_evidence"
    _out_json(output / "result.json", partial)

    print(json.dumps({
        "case_id": case_id or "unknown",
        "case_error": case_error,
        "material_count": len(materials),
        "readable_material_count": index["readable_material_count"],
        "contract_keys": contract_keys,
        "wrote": ["work/index.json", "work/plan.json", "work/draft.json",
                  "output/result.json"],
        "next": "main.py show",
    }, ensure_ascii=False, indent=2))
    return 0


def _show_case_summary(index):
    print("== case ==")
    print(f"case_id: {index.get('case_id')}")
    print(f"task_instruction: {index.get('task_instruction') or '(empty)'}")
    oc = index.get("output_contract")
    print(f"output_contract: {json.dumps(oc, ensure_ascii=False)[:800]}")
    print(f"contract_keys: {index.get('contract_keys')}")
    print(f"case_error: {index.get('case_error')}")
    print()


def cmd_show(args):
    work = Path(args.work)
    index = _load_json(work / "index.json")
    if index is None:
        print("index.json not found; run `main.py init` first.")
        return 0
    _show_case_summary(index)
    budget = 0
    for src in index.get("materials", []):
        if args.source and src["source_id"] != args.source:
            continue
        paras = [p for p in index.get("paras", [])
                 if p["source_id"] == src["source_id"]]
        print(f"== {src['source_id']} {src['rel_path']} "
              f"({src['kind']}, {len(paras)} paras, readable="
              f"{src['readable']}) ==")
        if src.get("problems"):
            print(f"  problems: {src['problems']}")
        if not src.get("readable"):
            print("  (no text layer; see degradations)")
            continue
        for p in paras:
            line = f"[{p['source_id']} {p['locator']}] {p['text']}"
            if budget + len(line) > args.max_chars:
                print(f"... truncated at {args.max_chars} chars; "
                      f"use --max-chars or --source to narrow ...")
                return 0
            print(line)
            budget += len(line) + 1
    print("\nNOTE: quote 必须从上方打印文本中逐字复制（可跨行不跨段、不跨源）。")
    return 0


def cmd_plan(args):
    work = Path(args.work)
    path = work / "plan.json"
    if args.new or not path.exists():
        index = _load_json(work / "index.json") or {}
        paras = index.get("paras", [])
        task_text = (index.get("task_instruction") or "")
        plan = {
            "case_id": index.get("case_id", "unknown"),
            "task_instruction_digest": index.get("task_instruction", "")[:1200],
            "contract_keys": index.get("contract_keys", []),
            "material_count": index.get("material_count", 0),
            "readable_material_count": index.get("readable_material_count", 0),
            "budget": {"total_seconds": 900, "model_requests": 64,
                       "finalize_at_seconds": 780, "first_drop_seconds": 180},
            "facets": auto_facet_applicability(task_text, paras),
            "scope_notes": "",
            "false_positive_guardrails": [
                "学科写作惯例/期刊格式差异/作者有意取舍不判为缺陷",
                "无法逐字定位的论断不写成 finding，列为 unchecked 或 uncertainty",
                "被引文献主张无法核验时只标 citation_support=not_checked，不臆断",
            ],
            "created_at": utcnow_iso(),
        }
        _out_json(path, plan)
        print("plan.json template (re)written. 请审阅并定稿各 facet 的 "
              "applicable 与 rationale。")
    plan = _load_json(path)
    if plan is None:
        print("plan.json missing or invalid.")
        return 0
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


def cmd_draft(args):
    work = Path(args.work)
    path = work / "draft.json"
    if args.new or not path.exists():
        index = _load_json(work / "index.json") or {}
        _out_json(path, make_draft_template(index.get("case_id", "unknown")))
        print("draft.json template (re)written.")
    draft = _load_json(path)
    if draft is None:
        print("draft.json missing or invalid.")
        return 0
    print(json.dumps(draft, ensure_ascii=False, indent=2))
    return 0


def _coverage_of(draft, plan):
    findings = draft.get("findings", []) if isinstance(draft, dict) else []
    unchecked = draft.get("unchecked_items", []) if isinstance(draft, dict) else []
    unchecked_by_facet = {}
    for u in unchecked:
        if isinstance(u, dict) and u.get("facet"):
            unchecked_by_facet[u["facet"]] = u
    plan_facets = {}
    if isinstance(plan, dict):
        for f in plan.get("facets", []):
            plan_facets[f.get("facet")] = f
    coverage = {}
    gaps = []
    for facet in FACETS:
        applicable = "yes"
        if facet in plan_facets:
            applicable = plan_facets[facet].get("applicable", "yes")
        if applicable == "no":
            coverage[facet] = "na"
            if not plan_facets.get(facet, {}).get("rationale"):
                gaps.append(f"{facet}: applicable=no 但未给 rationale")
            continue
        has_finding = any(isinstance(f, dict) and f.get("facet") == facet
                          for f in findings)
        if has_finding:
            coverage[facet] = "checked"
        elif facet in unchecked_by_facet:
            st = unchecked_by_facet[facet].get("status", "unchecked")
            coverage[facet] = "na" if st == "na" else "unchecked"
            if not unchecked_by_facet[facet].get("reason"):
                gaps.append(f"{facet}: unchecked 但缺 reason")
        else:
            coverage[facet] = "unchecked"
            gaps.append(f"{facet}: applicable 但无 finding 且无 unchecked 说明")
    return coverage, gaps


def cmd_check(args):
    work = Path(args.work)
    index = _load_json(work / "index.json")
    plan = _load_json(work / "plan.json")
    draft = _load_json(work / "draft.json")
    report = {"checked_at": utcnow_iso(), "errors": []}
    if index is None:
        report["errors"].append("index.json missing; run init first")
        _out_json(work / "check_report.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if draft is None:
        report["errors"].append("draft.json missing; run init/draft first")
        _out_json(work / "check_report.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    findings = draft.get("findings", []) if isinstance(draft, dict) else []
    if not isinstance(findings, list):
        report["errors"].append("draft.findings must be a list")
        findings = []

    seen_ids = set()
    quote_checks = []
    finding_errors = {}
    for f in findings:
        fid = f.get("finding_id") if isinstance(f, dict) else None
        if not isinstance(fid, str) or not fid:
            report["errors"].append("finding without finding_id")
            continue
        if fid in seen_ids:
            report["errors"].append(f"duplicate finding_id {fid}")
        seen_ids.add(fid)
        problems = validate_finding(f, index)
        if problems:
            finding_errors[fid] = problems
        # 逐条证据命中详情
        for ev in (f.get("evidence") or []) if isinstance(f, dict) else []:
            hit = quote_hit(index, ev.get("quote") or "") if isinstance(ev, dict) else False
            quote_checks.append({
                "finding_id": fid, "evidence_index": len(quote_checks),
                "source_id": ev.get("source_id") if isinstance(ev, dict) else None,
                "locator": ev.get("locator") if isinstance(ev, dict) else None,
                "hit": hit,
            })

    coverage, gaps = _coverage_of(draft, plan)
    vs = draft.get("verdict_summary") if isinstance(draft, dict) else None
    if vs is None or not isinstance(vs, dict):
        report["errors"].append("draft.verdict_summary missing")

    summary = {
        "findings": len(findings),
        "errors": len(report["errors"]) + sum(len(v) for v in finding_errors.values()),
        "quote_failures": sum(1 for c in quote_checks if not c["hit"]),
        "coverage_gaps": len(gaps),
    }
    report.update({
        "finding_errors": finding_errors,
        "quote_checks": quote_checks,
        "coverage": coverage,
        "coverage_gaps": gaps,
        "summary": summary,
    })
    _out_json(work / "check_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if summary["errors"] or summary["quote_failures"] or summary["coverage_gaps"]:
        print("\nFIX REQUIRED: 见上方 errors / quote_failures / coverage_gaps。")
    else:
        print("\nCHECK PASS: 可执行 `main.py build`。")
    return 0


def _final_status(index, draft, coverage):
    if index.get("case_error"):
        return "input_error"
    if index.get("readable_material_count", 0) == 0:
        return "insufficient_evidence"
    if all(v in ("checked", "na") for v in coverage.values()):
        return "ok"
    return "partial"


def cmd_build(args):
    work = Path(args.work)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    started = time.time()

    index = _load_json(work / "index.json") or {}
    plan = _load_json(work / "plan.json") or {}
    draft = _load_json(work / "draft.json") or {}
    runstate = _load_json(work / "runstate.json") or {}

    case_id = draft.get("case_id") or index.get("case_id") or "unknown"
    output_contract = index.get("output_contract")
    contract_keys = index.get("contract_keys", [])

    findings = draft.get("findings", []) if isinstance(draft, dict) else []
    unchecked = draft.get("unchecked_items", []) if isinstance(draft, dict) else []
    vs = draft.get("verdict_summary") if isinstance(draft, dict) else {}

    # 计算统计与覆盖
    counts = {"critical": 0, "major": 0, "minor": 0, "suggestion": 0,
              "total": len(findings)}
    for f in findings:
        if isinstance(f, dict) and f.get("severity") in counts:
            counts[f["severity"]] += 1
    coverage, gaps = _coverage_of(draft, plan)
    status = _final_status(index, draft, coverage)

    # 证据映射：evidence_id 固定为 E01.. 便于复核
    evidence_map = []
    eid = 0
    for f in findings:
        fid = f.get("finding_id") if isinstance(f, dict) else "F?"
        for ev in (f.get("evidence") or []) if isinstance(f, dict) else []:
            if not isinstance(ev, dict):
                continue
            eid += 1
            evidence_map.append({
                "evidence_id": f"E{eid:02d}",
                "finding_id": fid,
                "source_id": ev.get("source_id"),
                "locator": ev.get("locator"),
                "quote": ev.get("quote"),
                "hit": quote_hit(index, ev.get("quote") or ""),
            })

    # 汇总 verdict_summary
    overall = vs.get("overall_verdict") if isinstance(vs, dict) else None
    if overall not in OVERALL_VERDICTS:
        overall = "undetermined"
    summary_text = vs.get("summary") if isinstance(vs, dict) else ""
    verdict_summary = {
        "overall_verdict": overall,
        "summary": summary_text,
        "counts": counts,
        "coverage": coverage,
    }

    result = {"case_id": case_id}
    changes = merge_contract_into(result, output_contract)
    if not changes and isinstance(output_contract, list):
        for k in contract_keys:
            if k not in result and k not in ENVELOPE_KEYS:
                result[k] = None
    elif isinstance(output_contract, dict) is False and output_contract is not None:
        for k in contract_keys:
            result.setdefault(k, None)

    change_log = list(runstate.get("change_log", []))
    change_log = _log(change_log, "build", "review_report.md + result.json built")

    errors = list(index.get("errors", []))
    degradations = list(index.get("degradations", []))
    for fid, probs in _draft_problems(index, draft).items():
        for prob in probs:
            errors.append({"file": "draft.json", "finding": fid,
                           "error": prob})
            degradations.append(f"finding {fid}: {prob}")

    result.update({
        "status": status,
        "sources": index.get("materials", []),
        "uncertainty": (draft.get("uncertainty") or []) if isinstance(draft, dict) else [],
        "pending_human": (draft.get("pending_human") or []) if isinstance(draft, dict) else [],
        "errors": errors,
        "degradations": degradations,
        "changes": changes,
        "change_log": change_log,
        "runtime": {
            "started_at": runstate.get("started_at"),
            "finished_at": utcnow_iso(),
            "seconds": round(time.time() - started, 2),
            "finalized": True,
        },
        "result": {
            "verdict_summary": verdict_summary,
            "findings": findings,
            "unchecked_items": unchecked,
            "evidence_map": evidence_map,
        },
    })
    _out_json(output / "result.json", result)
    _out_json(work / "evidence_map.json",
              {"case_id": case_id, "evidence_map": evidence_map})

    report = render_report(case_id, verdict_summary, findings, unchecked,
                           status, degradations)
    (output / "review_report.md").write_text(report, encoding="utf-8",
                                             newline="\n")

    change_log = _log(change_log, "build", "done")
    runstate["change_log"] = change_log
    _out_json(work / "runstate.json", runstate)

    print(json.dumps({
        "status": status,
        "findings": counts["total"],
        "coverage": coverage,
        "coverage_gaps": gaps,
        "wrote": ["output/result.json", "output/review_report.md",
                  "work/evidence_map.json"],
    }, ensure_ascii=False, indent=2))
    return 0


def _draft_problems(index, draft):
    problems = {}
    findings = draft.get("findings", []) if isinstance(draft, dict) else []
    for f in findings:
        if not isinstance(f, dict):
            continue
        fid = f.get("finding_id", "?")
        probs = validate_finding(f, index)
        if probs:
            problems[fid] = probs
    return problems


def cmd_selfcheck(args):
    work = Path(args.work)
    output = Path(args.output)
    index = _load_json(work / "index.json")
    result = _load_json(output / "result.json")
    rep = {"checked_at": utcnow_iso(), "errors": [], "warnings": []}
    if result is None:
        rep["errors"].append("result.json missing or invalid JSON")
    else:
        for key in ENVELOPE_KEYS:
            if key not in result:
                rep["errors"].append(f"result.json missing envelope key {key}")
        if "case_id" not in result:
            rep["errors"].append("result.json missing case_id")
        r = result.get("result")
        if not isinstance(r, dict) or not isinstance(r.get("findings"), list):
            rep["errors"].append("result.result.findings missing")
        else:
            for f in r["findings"]:
                if index is not None:
                    probs = validate_finding(f, index)
                    if probs:
                        rep["errors"].append(
                            f"finding {f.get('finding_id')}: {probs}")
                else:
                    rep["warnings"].append(
                        "index.json missing; quote re-check skipped")
        report_path = output / "review_report.md"
        if not report_path.exists():
            rep["errors"].append("review_report.md missing")
    rep["summary"] = {"errors": len(rep["errors"]),
                      "warnings": len(rep["warnings"])}
    _out_json(work / "selfcheck.json", rep)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    if rep["errors"]:
        print("\nSELFCHECK FAIL")
    else:
        print("\nSELFCHECK PASS")
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    cmd = args.command or "init"
    try:
        if cmd == "init":
            return cmd_init(args)
        if cmd == "show":
            return cmd_show(args)
        if cmd == "plan":
            return cmd_plan(args)
        if cmd == "draft":
            return cmd_draft(args)
        if cmd == "check":
            return cmd_check(args)
        if cmd == "build":
            return cmd_build(args)
        if cmd == "selfcheck":
            return cmd_selfcheck(args)
    except Exception as exc:  # 顶层兜底：任何异常退出码恒 0
        print(f"ERROR (non-fatal): {exc}", file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
