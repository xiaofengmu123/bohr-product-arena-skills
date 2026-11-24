#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""论文质量审查 Skill 主入口（仅标准库）。

子命令（Agent 按 SKILL.md 顺序调用）：
  init      扫描 /input，建索引，立刻落盘 partial result.json 骨架
  show      打印带 locator 的原文，供 Agent 逐字抄录 quote
  check     校验 /app/work/draft.json（quote 命中 / 字段 / 枚举 / 覆盖）
  build     读取 draft.json，确定性生成 review_report.md + 最终 result.json
  selfcheck 只复检最终 result.json 与主产物，不重做

默认路径与评测环境一致：/input、/app/work、/app/output；
本地可用 --input/--work/--output 覆盖。所有子命令退出码恒为 0，
异常在顶层兜底为最小合法 result.json。
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
    OVERALL_VERDICTS, FINDING_KEYS, ENVELOPE_KEYS, RESERVED_KEYS, FACET_ORDER,
    MAX_TEXT_FILE, TEXT_KINDS, PDF_KINDS, DOCX_KINDS, IMAGE_KINDS,
    read_text, normalize_ws, split_paragraphs, paras_from_text,
    extract_pdf_text, extract_docx_paras, contract_keys_of, looks_list,
    validate_finding, find_quote_verbatim, finding_sort_key, canonical_dumps,
)

SKILL_ROOT = Path(__file__).resolve().parent.parent

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
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
                   help="init 时强制重新抽取文本（默认复用已有 work/text）")
    return p


def _out_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(canonical_dumps(obj))
    os.replace(tmp, path)
    return path


def _write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def _load_json(path):
    path = Path(path)
    if not path.exists():
        return None
    text, _ = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _classify_kind(name):
    ext = Path(name).suffix.lower()
    if ext in PDF_KINDS:
        return "pdf"
    if ext in DOCX_KINDS:
        return "docx"
    if ext in IMAGE_KINDS and ext not in PDF_KINDS:
        return "image"
    if ext in TEXT_KINDS:
        return "text"
    return "other"


def _material_files(input_dir):
    input_dir = Path(input_dir)
    if not input_dir.exists():
        return []
    skip = {"case.json", "task.md"}
    items = []
    for p in sorted(input_dir.rglob("*")):
        if not p.is_file() or p.name in skip:
            continue
        if any(part.startswith(".") for part in p.parts):
            continue
        items.append(p)
    return items


def _extract_material(path, kind):
    if kind in ("image", "other"):
        return "", False, ["%s: no text layer; needs OCR / external tool" % kind]
    try:
        data = path.read_bytes()
    except OSError as exc:
        return "", False, ["read error: %s" % exc]
    if kind == "pdf":
        pages, problems = extract_pdf_text(data)
        if not pages:
            return "", True, problems + ["no paragraphs extracted"]
        return "\f".join(pages), True, problems
    if kind == "docx":
        lines, problem = extract_docx_paras(data)
        if problem and not lines:
            return "", False, [problem]
        return "\n\n".join(lines), False, ([problem] if problem else [])
    text, problem = read_text(path)
    if text is None:
        return "", False, [problem or "unreadable"]
    return text, False, ([problem] if problem else [])


def _extract_or_reuse(path, kind, work, sid, rescan):
    work = Path(work)
    pre = work / "text" / (sid + ".txt")
    paged = (kind == "pdf")
    if not rescan and pre.exists():
        text, problem = read_text(pre)
        if text is not None and text.strip():
            paras = paras_from_text(text, paged)
            problems = [problem] if problem else []
            if paras:
                return text, paged, problems
    return _extract_material(path, kind)


def _scan_input(input_dir):
    input_dir = Path(input_dir)
    state = {
        "case_id": None, "task_instruction": "", "output_contract": None,
        "contract_keys": [], "degradations": [], "errors": [],
    }
    case_path = input_dir / "case.json"
    if case_path.exists():
        text, problem = read_text(case_path)
        if text is None:
            state["errors"].append({"file": "case.json", "error": problem})
            state["degradations"].append("case.json unreadable")
        else:
            if problem:
                state["degradations"].append("case.json encoding: %s" % problem)
            try:
                case = json.loads(text)
            except Exception as exc:
                state["errors"].append({"file": "case.json",
                                        "error": "bad JSON: %s" % exc})
                state["degradations"].append("case.json bad JSON; contract unknown")
                case = None
            if isinstance(case, dict):
                state["case_id"] = (case.get("case_id") or case.get("id")
                                    or case.get("task_id"))
                state["task_instruction"] = (
                    case.get("task_instruction") or case.get("task")
                    or case.get("instruction") or "")
                state["output_contract"] = case.get("output_contract")
                state["contract_keys"] = contract_keys_of(state["output_contract"])
    task_path = input_dir / "task.md"
    if task_path.exists():
        text, problem = read_text(task_path)
        if text:
            if problem:
                state["degradations"].append("task.md encoding: %s" % problem)
            if not state["task_instruction"]:
                state["task_instruction"] = text[:2000]
    return state


# ---------------------------------------------------------------------------
# 结果组装（init 与 build 共用，保证确定性）
# ---------------------------------------------------------------------------
def _deep(obj):
    return json.loads(json.dumps(obj, ensure_ascii=False))


def _derive_status(state, materials, draft):
    if any(isinstance(e, dict) and "bad JSON" in str(e.get("error", ""))
           for e in state.get("errors", [])):
        return "input_error"
    if not materials:
        return "insufficient_evidence"
    findings = (draft or {}).get("findings") or []
    unchecked = (draft or {}).get("unchecked_items") or []
    verified = set((draft or {}).get("verified_facets") or [])
    checked = set(f.get("facet") for f in findings if f.get("facet") in FACETS)
    checked |= verified
    if unchecked:
        return "partial"
    if set(FACETS).issubset(checked):
        return "ok"
    return "partial"


def _overall_verdict(findings, status):
    if status in ("insufficient_evidence", "input_error"):
        return "incomplete"
    confirmed_sev = {f.get("severity") for f in findings
                     if f.get("verdict") == "confirmed"}
    if "critical" in confirmed_sev or "major" in confirmed_sev:
        return "issues"
    if findings:
        return "to_review"
    return "pass"


def _verdict_summary(findings, unchecked, verified, status):
    fac = {k: 0 for k in FACETS}
    sev = {k: 0 for k in SEVERITIES}
    ver = {k: 0 for k in VERDICTS}
    for f in findings:
        if f.get("facet") in fac:
            fac[f["facet"]] += 1
        if f.get("severity") in sev:
            sev[f["severity"]] += 1
        if f.get("verdict") in ver:
            ver[f["verdict"]] += 1
    with_findings = sorted(set(f.get("facet") for f in findings
                               if f.get("facet") in FACETS),
                           key=lambda x: FACET_ORDER.get(x, 99))
    checked_clean = sorted(set(verified), key=lambda x: FACET_ORDER.get(x, 99))
    not_checked = []
    for u in unchecked:
        if isinstance(u, dict) and u.get("facet") in FACETS \
                and u["facet"] not in not_checked:
            not_checked.append(u["facet"])
    not_checked = sorted(not_checked, key=lambda x: FACET_ORDER.get(x, 99))
    return {
        "overall_verdict": _overall_verdict(findings, status),
        "counts": {
            "findings_total": len(findings),
            "by_facet": fac,
            "by_severity": sev,
            "by_verdict": ver,
        },
        "coverage": {
            "facets_with_findings": with_findings,
            "facets_checked_clean": checked_clean,
            "facets_not_checked": not_checked,
        },
    }


def _sources(materials):
    out = []
    for m in materials:
        out.append({
            "source_id": m.get("source_id"),
            "path": m.get("relpath"),
            "kind": m.get("kind"),
            "chars": m.get("chars"),
            "paras": m.get("paras"),
            "pages": m.get("pages"),
            "text_file": m.get("text_file"),
            "skipped": bool(m.get("skipped")),
            "problems": list(m.get("problems") or []),
        })
    return out


def _default_change_log():
    return "single deterministic pass; no manual change records"


def _auto_summary(vs):
    c = vs.get("counts", {})
    parts = ["overall=%s" % vs.get("overall_verdict"),
             "findings=%d" % c.get("findings_total", 0)]
    not_checked = vs.get("coverage", {}).get("facets_not_checked", [])
    if not_checked:
        parts.append("not_checked=%s" % ",".join(not_checked))
    return "; ".join(parts)


def _assemble(state, materials, draft=None):
    skeleton = draft is None
    if draft is None:
        draft = {}
    contract = state.get("output_contract")
    keys = state.get("contract_keys") or []
    obj = {}
    obj["case_id"] = draft.get("case_id") or state.get("case_id")
    obj["status"] = draft.get("status") or _derive_status(state, materials, draft)
    if contract is not None:
        obj["output_contract"] = _deep(contract)
    findings = sorted(draft.get("findings") or [], key=finding_sort_key)
    unchecked = draft.get("unchecked_items") or []
    verified = draft.get("verified_facets") or []
    # 骨架落盘：若无任何已查信息，把六类 facet 全部标记为未查（明确缺口）
    if skeleton and not findings and not unchecked and not verified:
        reason = "骨架落盘：尚未开始分面审查"
        if obj["status"] == "insufficient_evidence":
            reason = "材料不可用，未检查"
        elif obj["status"] == "input_error":
            reason = "输入错误，未检查"
        unchecked = [{"facet": f, "reason": reason} for f in FACETS]
    vs = _verdict_summary(findings, unchecked, verified, obj["status"])
    obj["result"] = {
        "findings": findings,
        "unchecked_items": unchecked,
        "verdict_summary": vs,
    }
    # 契约键回填：已知键填计算值，draft 显式值次之，否则占位 null/[]
    known = {
        "findings": findings,
        "unchecked_items": unchecked,
        "summary": draft.get("summary") or _auto_summary(vs),
        "verdict": vs.get("overall_verdict"),
        "overall_verdict": vs.get("overall_verdict"),
    }
    for k in keys:
        if k in RESERVED_KEYS:
            continue
        if k in known:
            obj[k] = known[k]
        elif k in draft:
            obj[k] = draft[k]
        else:
            obj[k] = [] if looks_list(k) else None
    obj["sources"] = _sources(materials)
    obj["uncertainty"] = draft.get("uncertainty") or []
    obj["pending_human"] = draft.get("pending_human") or []
    obj["errors"] = list(state.get("errors") or [])
    if isinstance(draft.get("errors"), list):
        obj["errors"] = obj["errors"] + list(draft["errors"])
    obj["degradations"] = list(state.get("degradations") or [])
    if isinstance(draft.get("degradations"), list):
        obj["degradations"] = obj["degradations"] + list(draft["degradations"])
    obj["changes"] = draft.get("changes") or []
    obj["change_log"] = draft.get("change_log") or _default_change_log()
    obj["runtime"] = _runtime(state, materials)
    return obj


def _runtime(state, materials):
    started = state.get("started_epoch")
    now = time.time()
    return {
        "started_at_epoch": started,
        "finished_at_epoch": now,
        "elapsed_seconds": (round(now - started, 3) if started else None),
        "python_version": sys.version.split()[0],
        "input_dir": state.get("input_dir"),
        "materials_found": len(materials),
        "note": "volatile fields; excluded from determinism comparison",
    }


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------
def cmd_init(args):
    input_dir = Path(args.input)
    work = Path(args.work)
    output = Path(args.output)
    for d in (work, work / "text", output):
        d.mkdir(parents=True, exist_ok=True)

    state = _scan_input(input_dir)
    state["input_dir"] = str(input_dir)
    state["started_epoch"] = time.time()

    materials = []
    for path in _material_files(input_dir):
        sid = "D%d" % (len(materials) + 1)
        kind = _classify_kind(path.name)
        size = path.stat().st_size
        if size > MAX_TEXT_FILE:
            materials.append({
                "source_id": sid, "relpath": _relpath(path, input_dir),
                "kind": kind, "chars": size, "paras": 0, "pages": 0,
                "text_file": None, "skipped": True,
                "problems": ["file >2MB skipped"],
            })
            state["degradations"].append("oversized skipped: %s" % path.name)
            continue
        text, paged, problems = _extract_or_reuse(path, kind, work, sid,
                                                  args.rescan)
        paras = paras_from_text(text, paged) if text else []
        text_file = None
        if text.strip():
            text_file = "text/%s.txt" % sid
            _write_text(work / text_file, text)
        pages = 0
        for p in paras:
            if p.get("page"):
                pages = max(pages, int(p["page"]))
        if paged and not pages:
            pages = 1 if paras else 0
        materials.append({
            "source_id": sid, "relpath": _relpath(path, input_dir),
            "kind": kind, "chars": len(text), "paras": len(paras),
            "pages": pages, "text_file": text_file, "skipped": False,
            "problems": list(problems or []),
        })
        if problems:
            state["degradations"].extend(
                "%s: %s" % (sid, pr) for pr in problems)

    index = {
        "case_id": state["case_id"],
        "task_instruction": state["task_instruction"],
        "output_contract": state["output_contract"],
        "contract_keys": state["contract_keys"],
        "materials": materials,
        "degradations": state["degradations"],
        "errors": state["errors"],
        "input_dir": state["input_dir"],
        "started_epoch": state["started_epoch"],
    }
    _out_json(work / "index.json", index)
    skeleton = _assemble(state, materials, draft=None)
    _out_json(output / "result.json", skeleton)
    print("init ok: case_id=%r materials=%d status=%s"
          % (state["case_id"], len(materials), skeleton["status"]))


def _relpath(path, base):
    try:
        return str(path.relative_to(base)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def cmd_show(args):
    work = Path(args.work)
    index = _load_json(work / "index.json")
    if index is None:
        print("index.json 缺失；请先运行 init")
        return
    print("== 索引 ==")
    print("case_id: %r" % index.get("case_id"))
    instr = (index.get("task_instruction") or "").strip()
    print("task_instruction: %s" % (instr[:500] if instr else "(空)"))
    print("contract_keys: %s" % (index.get("contract_keys") or []))
    print("degradations: %s" % (index.get("degradations") or []))
    print("errors: %s" % (index.get("errors") or []))
    budget = args.max_chars
    for m in index.get("materials") or []:
        if args.source and m.get("source_id") != args.source:
            continue
        print("\n== %s %s (kind=%s chars=%d paras=%d pages=%s)" % (
            m.get("source_id"), m.get("relpath"), m.get("kind"),
            m.get("chars"), m.get("paras"), m.get("pages")))
        if m.get("skipped"):
            print("  [skipped] %s" % m.get("problems"))
            continue
        if m.get("problems"):
            print("  [problems] %s" % m.get("problems"))
        tf = m.get("text_file")
        path = work / tf if tf else None
        if not path or not path.exists():
            print("  (无文本)")
            continue
        text, _ = read_text(path)
        if text is None:
            print("  (文本不可读)")
            continue
        for p in paras_from_text(text, m.get("kind") == "pdf"):
            chunk = "%s  %s" % (p["locator"], p["text"])
            if budget <= 0:
                print("  ... [达到 --max-chars 上限，其余未打印]")
                return
            print(chunk[:budget])
            budget -= len(chunk) + 1


def _text_lookup(index, work):
    work = Path(work)
    lookup = {}
    for m in index.get("materials") or []:
        if m.get("skipped"):
            continue
        tf = m.get("text_file")
        if not tf:
            continue
        path = work / tf
        if not path.exists():
            continue
        text, _ = read_text(path)
        if text is None:
            continue
        paras = paras_from_text(text, m.get("kind") == "pdf")
        lookup[m.get("source_id")] = [p["norm"] for p in paras]
    return lookup


def cmd_check(args):
    work = Path(args.work)
    index = _load_json(work / "index.json")
    draft = _load_json(work / "draft.json")
    if index is None:
        print("FAIL: index.json 缺失，先运行 init")
        _out_json(work / "check_report.json",
                  {"ok": False, "errors": ["index.json missing"]})
        return
    if draft is None:
        print("FAIL: draft.json 缺失")
        _out_json(work / "check_report.json",
                  {"ok": False, "errors": ["draft.json missing"]})
        return
    lookup = _text_lookup(index, work)
    findings = draft.get("findings") or []
    problems, warnings = [], []
    ids = set()
    for f in findings:
        fid = str(f.get("finding_id", ""))
        if fid in ids:
            problems.append("duplicate finding_id: %s" % fid)
        ids.add(fid)
        p, w = validate_finding(f)
        for item in p:
            problems.append("%s: %s" % (fid or "?", item))
        for item in w:
            warnings.append("%s: %s" % (fid or "?", item))
        quote = f.get("quote")
        if quote and not find_quote_verbatim(quote, lookup):
            problems.append("%s: quote not found verbatim: %s"
                            % (fid or "?", normalize_ws(str(quote))[:80]))
        for e in (f.get("evidence") or []):
            if not isinstance(e, dict):
                continue
            sid = e.get("source_id")
            if sid and sid not in lookup:
                problems.append("%s: evidence source_id not in index: %s"
                                % (fid or "?", sid))
            elif sid and e.get("quote") and \
                    not find_quote_verbatim(str(e["quote"]), {sid: lookup[sid]}):
                warnings.append("%s: evidence quote not verbatim in %s"
                                % (fid or "?", sid))
    # 覆盖检查：六类 facet 均需出现于 findings / unchecked_items / verified
    checked = set(f.get("facet") for f in findings if f.get("facet") in FACETS)
    unchecked = set(u.get("facet") for u in (draft.get("unchecked_items") or [])
                    if isinstance(u, dict) and u.get("facet") in FACETS)
    verified = set(draft.get("verified_facets") or [])
    missing = [x for x in FACETS if x not in (checked | unchecked | verified)]
    if missing:
        problems.append("facets not covered: %s" % ", ".join(missing))
    report = {
        "ok": not problems,
        "findings": len(findings),
        "problems": problems,
        "warnings": warnings,
        "coverage": {
            "facets_with_findings": sorted(checked),
            "facets_checked_clean": sorted(verified),
            "facets_not_checked": sorted(unchecked),
            "facets_missing": missing,
        },
    }
    _out_json(work / "check_report.json", report)
    print("check: %s findings=%d problems=%d warnings=%d"
          % ("PASS" if report["ok"] else "FAIL", len(findings),
             len(problems), len(warnings)))
    for item in problems:
        print("  FAIL %s" % item)
    for item in warnings:
        print("  WARN %s" % item)


def _render_report(obj):
    lines = []
    lines.append("# 论文质量审查报告")
    lines.append("")
    lines.append("- case_id: `%s`" % obj.get("case_id"))
    lines.append("- status: `%s`" % obj.get("status"))
    vs = obj.get("result", {}).get("verdict_summary", {})
    lines.append("- overall_verdict: `%s`" % vs.get("overall_verdict"))
    cnt = vs.get("counts", {})
    lines.append("- findings_total: %d" % cnt.get("findings_total", 0))
    lines.append("")
    lines.append("## 发现清单")
    lines.append("")
    findings = obj.get("result", {}).get("findings", [])
    if not findings:
        lines.append("（无 finding；见下方未检查项）")
        lines.append("")
    for f in findings:
        lines.append("### %s · %s · %s" % (f.get("finding_id"),
                                            f.get("facet"), f.get("severity")))
        lines.append("")
        lines.append("- **定位**: `%s`" % f.get("location"))
        lines.append("- **原文引用**: > %s" % f.get("quote"))
        lines.append("- **论断/问题**: %s" % f.get("claim"))
        lines.append("- **判定依据**: %s" % f.get("rationale"))
        lines.append("- **修改建议**: %s" % f.get("revision_suggestion"))
        lines.append("- **verdict**: `%s` · issue_type: `%s` · confidence: `%s`"
                     % (f.get("verdict"), f.get("issue_type"),
                        f.get("confidence")))
        for e in (f.get("evidence") or []):
            if isinstance(e, dict):
                lines.append("  - 证据 %s@%s: %s" % (e.get("source_id"),
                                                     e.get("locator"),
                                                     e.get("quote")))
        lines.append("")
    lines.append("## 未检查项")
    lines.append("")
    for u in obj.get("result", {}).get("unchecked_items", []):
        if isinstance(u, dict):
            lines.append("- `%s`: %s" % (u.get("facet"), u.get("reason")))
        else:
            lines.append("- %s" % u)
    if not obj.get("result", {}).get("unchecked_items"):
        lines.append("（无）")
    lines.append("")
    lines.append("## 不确定性与待人工复核")
    lines.append("")
    for u in obj.get("uncertainty", []):
        lines.append("- %s" % u)
    for p in obj.get("pending_human", []):
        lines.append("- [待复核] %s" % p)
    if not obj.get("uncertainty") and not obj.get("pending_human"):
        lines.append("（无）")
    lines.append("")
    lines.append("## 来源")
    lines.append("")
    for s in obj.get("sources", []):
        lines.append("- `%s` %s (kind=%s paras=%s pages=%s%s)" % (
            s.get("source_id"), s.get("path"), s.get("kind"),
            s.get("paras"), s.get("pages"),
            " [skipped]" if s.get("skipped") else ""))
    return "\n".join(lines)


def cmd_build(args):
    work = Path(args.work)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    index = _load_json(work / "index.json") or {}
    draft = _load_json(work / "draft.json") or {}
    materials = index.get("materials") or []
    obj = _assemble(index, materials, draft)
    _out_json(output / "result.json", obj)
    _write_text(output / "review_report.md", _render_report(obj))
    print("build ok: case_id=%r status=%s findings=%d"
          % (obj.get("case_id"), obj.get("status"),
             len(obj.get("result", {}).get("findings", []))))


def cmd_selfcheck(args):
    output = Path(args.output)
    result = _load_json(output / "result.json")
    report = {"ok": True, "checks": []}
    def check(name, ok, detail=""):
        report["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok:
            report["ok"] = False
    if result is None:
        check("result.json 存在且可解析", False, "missing/bad")
        _out_json(output / "selfcheck.json", report)
        print("selfcheck: FAIL result.json")
        return
    check("case_id 保留", bool(result.get("case_id")))
    check("status 合法", result.get("status") in STATUSES,
          str(result.get("status")))
    for k in ENVELOPE_KEYS:
        check("envelope 字段 %s" % k, k in result)
    r = result.get("result")
    check("result.findings 为列表", isinstance(r, dict)
          and isinstance(r.get("findings"), list))
    for f in (r or {}).get("findings") or []:
        p, w = validate_finding(f)
        check("finding %s 形状合法" % f.get("finding_id"), not p,
              "; ".join(p))
    check("review_report.md 存在", (output / "review_report.md").exists())
    _out_json(output / "selfcheck.json", report)
    print("selfcheck: %s" % ("PASS" if report["ok"] else "FAIL"))
    for c in report["checks"]:
        if not c["ok"]:
            print("  FAIL %s %s" % (c["name"], c["detail"]))


def _emergency_result(args, exc):
    try:
        output = Path(args.output)
        output.mkdir(parents=True, exist_ok=True)
        obj = {
            "case_id": None,
            "status": "input_error",
            "result": {"findings": [], "unchecked_items": [],
                       "verdict_summary": {"overall_verdict": "incomplete",
                                           "counts": {}, "coverage": {}}},
            "sources": [], "uncertainty": [], "pending_human": [],
            "errors": [{"error": "unhandled exception: %s" % exc}],
            "degradations": ["unhandled exception"], "changes": [],
            "change_log": "emergency minimal result",
            "runtime": {"note": "emergency"},
        }
        _out_json(output / "result.json", obj)
    except Exception:
        pass


def dispatch(args):
    if args.command == "init":
        cmd_init(args)
    elif args.command == "show":
        cmd_show(args)
    elif args.command == "check":
        cmd_check(args)
    elif args.command == "build":
        cmd_build(args)
    elif args.command == "selfcheck":
        cmd_selfcheck(args)


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command is None:
        build_parser().print_help()
        return 0
    try:
        dispatch(args)
    except SystemExit:
        raise
    except Exception as exc:  # 兜底：恒退出 0 且留最小合法产物
        try:
            _emergency_result(args, exc)
        finally:
            print("ERROR: %s" % exc, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
