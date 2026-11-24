#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""论文质量审查 Skill 主入口（仅标准库）。

子命令（Agent 按 SKILL.md 顺序调用）：
  init      扫描 /input，建索引，立刻落盘 partial result.json 骨架
  show      打印带 locator 的原文，供 Agent 逐字引用
  check     校验 /app/work/draft.json（quote 命中 / 字段 / 枚举 / 覆盖）
  build     读取 draft.json，生成 review_report.md + 最终 result.json
  selfcheck 只复检最终产物（含评分维度对齐报告），不重做审查

默认路径与评测环境一致：/input、/app/work、/app/output；
本地可用 --input/--work/--output 覆盖。所有子命令退出码恒为 0。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from core_impl import (
    FACETS, SEVERITIES, ISSUE_TYPES, VERDICTS, CONFIDENCES,
    STATUSES, OVERALL_VERDICTS, ENVELOPE_KEYS, MAX_TEXT_FILE,
    FINDING_KEYS, UNCHECKED_KEYS, TEXT_KINDS, PDF_KINDS, DOCX_KINDS, IMAGE_KINDS,
    read_text, split_paragraphs, extract_pdf_text, extract_docx_paras,
    contract_keys_of, default_for_key, validate_finding, quote_hit,
    SCORE_ALIGNMENT, run_score_checks,
)

SKILL_ROOT = Path(__file__).resolve().parent.parent
RESERVED_KEYS = set(ENVELOPE_KEYS) | {"case_id", "verdict_summary", "result",
                                      "coverage", "limitations"}
# output_contract 键名 → 信封内已有真实值的镜像（避免契约字段留空导致误判）
CONTRACT_MIRROR = {
    "findings": lambda r: r["result"]["findings"],
    "unchecked_items": lambda r: r["result"]["unchecked_items"],
    "review_notes": lambda r: r["verdict_summary"].get("notes", ""),
    "verdict": lambda r: r["verdict_summary"]["overall"],
}
FACET_RANK = {f: i for i, f in enumerate(FACETS)}
SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _ts():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
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
    if ext in IMAGE_KINDS:
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


def _store_text(work, sid, text):
    d = Path(work) / "text"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.txt").write_text(text, encoding="utf-8")


def _paras_from_stored(text, kind):
    if kind == "pdf":
        pages = [p for p in text.split("\f")]
        paras = []
        for pi, pg in enumerate(pages, 1):
            paras.extend(split_paragraphs(pg, page=pi))
        return paras
    return split_paragraphs(text, page=None)


def _extract_material(path, kind, work, sid):
    if kind in ("image", "other"):
        return [], [f"{kind}: 无文本层，需 OCR/外部工具，不猜内容"]
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [], [f"read error: {exc}"]
    if kind == "pdf":
        pages, problems = extract_pdf_text(data)
        if not pages:
            return [], problems
        store = "\f".join(pages)
    elif kind == "docx":
        lines, problem = extract_docx_paras(data)
        if not lines:
            return [], [problem] if problem else ["no text"]
        store = "\n\n".join(lines)
        problems = [problem] if problem else []
    else:
        text, problem = read_text(path)
        if text is None:
            return [], [problem or "unreadable"]
        store = text
        problems = [problem] if problem else []
    _store_text(work, sid, store)
    return _paras_from_stored(store, kind), problems


def _extract_or_reuse(path, kind, work, sid, rescan):
    pre = Path(work) / "text" / f"{sid}.txt"
    if not rescan and pre.exists():
        text, problem = read_text(pre)
        if text is not None:
            paras = _paras_from_stored(text, kind)
            if paras:
                return paras, ([f"reused pre-extracted text {pre.name}"]
                               + ([problem] if problem else []))
    return _extract_material(path, kind, work, sid)


def _scan_input(input_dir):
    input_dir = Path(input_dir)
    state = {
        "case_id": None, "task_instruction": "", "output_contract": None,
        "contract_keys": [], "input_files": [], "degradations": [], "errors": [],
    }
    if input_dir.exists():
        state["input_files"] = [
            {"name": p.name, "size": p.stat().st_size}
            for p in sorted(input_dir.rglob("*")) if p.is_file()
        ]
    case_path = input_dir / "case.json"
    if case_path.exists():
        text, problem = read_text(case_path)
        if text is None:
            state["errors"].append({"file": "case.json", "error": problem})
            state["degradations"].append("case.json 不可读，契约未知")
        else:
            if problem:
                state["degradations"].append(f"case.json 编码降级: {problem}")
            try:
                case = json.loads(text)
            except Exception as exc:
                state["errors"].append({"file": "case.json", "error": f"bad JSON: {exc}"})
                state["degradations"].append("case.json 坏 JSON，契约未知")
                case = None
            if isinstance(case, dict):
                state["case_id"] = case.get("case_id") or case.get("id") or case.get("task_id")
                state["task_instruction"] = (
                    case.get("task_instruction") or case.get("task")
                    or case.get("instruction") or "")
                oc = case.get("output_contract")
                if oc is None:
                    oc = case.get("contract") or case.get("output")
                state["output_contract"] = oc
                state["contract_keys"] = contract_keys_of(oc)
    else:
        state["errors"].append({"file": "case.json", "error": "not found"})
        state["degradations"].append("缺少 case.json")
    return state


def _build_skeleton(state, started):
    result = {
        "case_id": state.get("case_id"),
        "status": "partial" if state.get("case_id") else "input_error",
        "verdict_summary": {"overall": "not_determinable",
                            "counts": {}, "facet_counts": {}},
        "result": {"findings": [], "unchecked_items": []},
        "coverage": {"facets": {}, "scoring_alignment": {}},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": ([{"phase": "init", "errors": state["errors"]}]
                   if state["errors"] else []),
        "degradations": list(state["degradations"]),
        "limitations": [],
        "changes": [],
        "change_log": [{
            "ts": _ts(), "phase": "init",
            "summary": "前 3 分钟内落盘 partial 结果骨架；此后只做增量改进",
        }],
        "runtime": {
            "started_at": started,
            "phases": [{"phase": "init", "at": _ts(), "notes": "skeleton written"}],
            "note": "runtime 字段为易变量，不参与确定性比对",
        },
    }
    collisions = []
    for key in state["contract_keys"]:
        if key in RESERVED_KEYS:
            collisions.append(key)
        else:
            result[key] = default_for_key(key)
    if collisions:
        result["change_log"].append({
            "ts": _ts(), "phase": "init",
            "summary": f"契约键与信封保留键冲突，保留信封字段: {collisions}",
        })
    return result


# ---------------------------------------------------------------- init


def cmd_init(args):
    input_dir, work, output = Path(args.input), Path(args.work), Path(args.output)
    started = _ts()
    (work / "text").mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    state = _scan_input(input_dir)
    sources, paragraphs = [], {}
    for path in _material_files(input_dir):
        sid = f"D{len(sources) + 1}"
        kind = _classify_kind(path.name)
        paras, problems = _extract_or_reuse(path, kind, work, sid, args.rescan)
        rel = os.path.relpath(path, input_dir) if input_dir.exists() else path.name
        chars = sum(len(p["text"]) for p in paras)
        sources.append({
            "source_id": sid, "path": rel, "kind": kind,
            "chars": chars, "paragraphs": len(paras), "problems": problems,
        })
        paragraphs[sid] = paras
        for prob in problems:
            state["degradations"].append(f"{sid} ({rel}): {prob}")
    if not sources:
        state["degradations"].append(
            "未发现科研材料文件（/input 下除 case.json/task.md 外无文件）")
    index = {
        "state": state, "sources": sources, "paragraphs": paragraphs,
        "generated_at": _ts(),
    }
    _write_json(work / "index.json", index)
    _write_json(output / "result.json", _build_skeleton(state, started))
    print(f"init ok: case_id={state.get('case_id')} sources={len(sources)} "
          f"contract_keys={state['contract_keys']} "
          f"degradations={len(state['degradations'])}")
    return 0


# ---------------------------------------------------------------- show


def cmd_show(args):
    index = _load_json(Path(args.work) / "index.json")
    if not index:
        print("show: 无 index.json，先运行 init")
        return 0
    paragraphs = index.get("paragraphs", {})
    budget = args.max_chars
    used = 0
    for sid, paras in paragraphs.items():
        if args.source and sid != args.source:
            continue
        for p in paras:
            line = f"=== {sid} [{p['loc']}] ===\n{p['text']}\n"
            if budget and used + len(line) > budget:
                print(f"[show: 已达 {budget} 字符上限，剩余从 index.json 继续读取]")
                return 0
            print(line)
            used += len(line)
    return 0


# ---------------------------------------------------------------- check


def cmd_check(args):
    work = Path(args.work)
    draft = _load_json(work / "draft.json")
    index = _load_json(work / "index.json")
    if draft is None:
        _write_json(work / "check_report.json", {
            "ok": False, "summary": {"total_findings": 0, "fails": 1, "pass": 0},
            "fatal": "draft.json 缺失：先通读材料并写 /app/work/draft.json",
        })
        print("check FAIL: draft.json 缺失")
        return 0
    paragraphs = (index or {}).get("paragraphs", {})
    full_text = {sid: "\n".join(p["text"] for p in paras)
                 for sid, paras in paragraphs.items()}
    loc_text = {}
    for sid, paras in paragraphs.items():
        for p in paras:
            loc_text.setdefault(p["loc"], []).append((sid, p["text"]))
    report, findings = [], draft.get("findings") or []
    for i, f in enumerate(findings):
        field_errs = validate_finding(f, i)
        quote = f.get("quote", "")
        hits = [sid for sid, t in full_text.items() if quote_hit(quote, t)]
        loc_ok = any(quote_hit(quote, t) for _, t in loc_text.get(f.get("location"), []))
        ev_rows = []
        for ev in (f.get("evidence") or []):
            sid = ev.get("source_id")
            t = full_text.get(sid, "")
            ev_rows.append({"source_id": sid, "hit": bool(t) and quote_hit(ev.get("quote", ""), t)})
        ev_ok = all(e["hit"] for e in ev_rows) if ev_rows else (not (f.get("evidence") or []))
        report.append({
            "finding_id": f.get("finding_id") or f"#{i}",
            "field_errors": field_errs,
            "quote_ok": bool(hits),
            "quote_hit_sources": hits,
            "locator_ok": loc_ok,
            "evidence_ok": ev_ok,
            "evidence": ev_rows,
        })
    unchecked_rows = []
    for u in (draft.get("unchecked_items") or []):
        unchecked_rows.append({
            "facet": u.get("facet"),
            "missing_keys": [k for k in UNCHECKED_KEYS if k not in u],
        })
    fails = sum(1 for r in report
                if r["field_errors"] or not r["quote_ok"] or not r["locator_ok"]
                or not r["evidence_ok"])
    facets_seen = {f.get("facet") for f in findings} | {
        u.get("facet") for u in (draft.get("unchecked_items") or [])}
    missing_facets = [f for f in FACETS if f not in facets_seen]
    _write_json(work / "check_report.json", {
        "ok": fails == 0 and not missing_facets,
        "summary": {"total_findings": len(findings), "fails": fails,
                    "pass": len(findings) - fails, "missing_facets": missing_facets},
        "findings": report, "unchecked_items": unchecked_rows,
    })
    print(f"check: findings={len(findings)} fails={fails} "
          f"missing_facets={missing_facets} -> {'PASS' if fails == 0 and not missing_facets else 'REVIEW'}")
    return 0


# ---------------------------------------------------------------- build


def _sort_key(f):
    fid = str(f.get("finding_id") or "")
    digits = re.sub(r"\D", "", fid)
    return (FACET_RANK.get(f.get("facet"), 99),
            SEV_RANK.get(f.get("severity"), 99),
            int(digits) if digits.isdigit() else 0, fid)


def _compute_coverage(findings, unchecked):
    cov = {f: {"findings": 0, "unchecked": 0} for f in FACETS}
    for f in findings:
        if f.get("facet") in cov:
            cov[f["facet"]]["findings"] += 1
    for u in unchecked:
        if u.get("facet") in cov:
            cov[u["facet"]]["unchecked"] += 1
    return cov


def _render_report(result, index):
    lines = ["# 论文质量审查报告", "",
             f"- case_id: `{result.get('case_id')}`",
             f"- status: `{result.get('status')}`",
             f"- 总体判定: `{result.get('verdict_summary', {}).get('overall')}`",
             f"- 生成时间: `{result.get('runtime', {}).get('started_at')}`", ""]
    cov = result.get("coverage", {}).get("facets", {})
    lines += ["## 覆盖概览", "", "| facet | findings | unchecked |", "|---|---|---|"]
    for f in FACETS:
        c = cov.get(f, {"findings": 0, "unchecked": 0})
        lines.append(f"| {f} | {c['findings']} | {c['unchecked']} |")
    lines += ["", "## 问题清单", ""]
    for f in result.get("result", {}).get("findings", []):
        lines += [
            f"### {f.get('finding_id')} · {f.get('facet')} · {f.get('severity')} · {f.get('verdict')}",
            f"- 定位: `{f.get('location')}`",
            f"- 论断: {f.get('claim')}",
            f"- 原文: > {f.get('quote')}",
            f"- 判断依据: {f.get('rationale')}",
            f"- 修改建议: {f.get('revision_suggestion')}",
            f"- 置信度: {f.get('confidence')}",
        ]
        for ev in (f.get("evidence") or []):
            lines.append(f"  - 证据 {ev.get('source_id')} [{ev.get('locator')}]: > {ev.get('quote')}")
        lines.append("")
    lines += ["## 未检查项", ""]
    for u in result.get("result", {}).get("unchecked_items", []):
        lines.append(f"- {u.get('facet')}: {u.get('reason')} (`{u.get('status')}`)")
    if not result.get("result", {}).get("unchecked_items"):
        lines.append("- （无）")
    lines += ["", "## 依据与边界", ""]
    for key, title in (("sources", "来源"), ("uncertainty", "不确定性"),
                       ("degradations", "降级"), ("limitations", "限制")):
        lines.append(f"### {title}")
        items = result.get(key) or []
        if not items:
            lines.append("- （无）")
        for it in items:
            if isinstance(it, str):
                lines.append(f"- {it}")
            else:
                lines.append(f"- {json.dumps(it, ensure_ascii=False)}")
        lines.append("")
    lines += ["## 复核指引", "",
              "逐条打开上方 finding 的 location，比对 quote 是否与原文逐字一致；",
              "证据列给出 source_id 与 locator，可回到 `/app/work/text/<source_id>.txt` 复核。", ""]
    return "\n".join(lines)


def cmd_build(args):
    work, output = Path(args.work), Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    draft = _load_json(work / "draft.json")
    index = _load_json(work / "index.json")
    if draft is None:
        result = {"case_id": (index or {}).get("state", {}).get("case_id"),
                  "status": "input_error",
                  "result": {"findings": [], "unchecked_items": []},
                  "coverage": {"facets": {}, "scoring_alignment": {}},
                  "sources": [], "uncertainty": [], "pending_human": [],
                  "errors": [{"phase": "build", "errors": ["draft.json 缺失，无法成稿"]}],
                  "degradations": ["draft.json 缺失"], "changes": [],
                  "change_log": [{"ts": _ts(), "phase": "build",
                                  "summary": "draft.json 缺失，落盘最小合法结果"}],
                  "runtime": {"started_at": _ts(), "phases": []}}
        _write_json(output / "result.json", result)
        _write_json(output / "review_report.md", _render_report(result, index or {}))
        print("build FAIL: draft.json 缺失，已落盘最小合法 result.json")
        return 0
    findings = sorted(draft.get("findings") or [], key=_sort_key)
    unchecked = draft.get("unchecked_items") or []
    status = draft.get("status") if draft.get("status") in STATUSES else "partial"
    overall = draft.get("verdict_summary", {}).get("overall")
    if overall not in OVERALL_VERDICTS:
        overall = "not_determinable"
    sev_counts = {s: 0 for s in SEVERITIES}
    for f in findings:
        if f.get("severity") in sev_counts:
            sev_counts[f["severity"]] += 1
    facet_counts = {f: 0 for f in FACETS}
    for f in findings:
        if f.get("facet") in facet_counts:
            facet_counts[f["facet"]] += 1
    coverage = _compute_coverage(findings, unchecked)
    state = (index or {}).get("state", {})
    case_id = draft.get("case_id") or state.get("case_id")
    prev = _load_json(output / "result.json")
    prev_runtime = (prev or {}).get("runtime", {})
    init_phases = [p for p in prev_runtime.get("phases", []) if p.get("phase") == "init"]
    started_at = prev_runtime.get("started_at") or _ts()
    result = {
        "case_id": case_id,
        "status": status,
        "verdict_summary": {
            "overall": overall,
            "notes": draft.get("verdict_summary", {}).get("notes", ""),
            "counts": {"total": len(findings), **sev_counts},
            "facet_counts": facet_counts,
        },
        "result": {"findings": findings, "unchecked_items": unchecked},
        "coverage": {"facets": coverage, "scoring_alignment": {}},
        "sources": (index or {}).get("sources", []),
        "uncertainty": draft.get("uncertainty") or [],
        "pending_human": draft.get("pending_human") or [],
        "errors": ([{"phase": "init", "errors": state.get("errors", [])}]
                   if state.get("errors") else []),
        "degradations": (index or {}).get("state", {}).get("degradations", [])
                        + (draft.get("degradations") or []),
        "limitations": draft.get("limitations") or [],
        "changes": draft.get("changes") or [],
        "change_log": (draft.get("change_log") or []) + [{
            "ts": _ts(), "phase": "build",
            "summary": f"成稿：findings={len(findings)} unchecked={len(unchecked)} status={status}",
        }],
        "runtime": {
            "started_at": started_at,
            "phases": init_phases + [{"phase": "build", "at": _ts(), "notes": "finalized"}],
            "note": "runtime 字段为易变量，不参与确定性比对",
        },
    }
    # 回填 output_contract 字段：优先镜像信封内真实值，其次 draft 显式值，最后 null/[]
    collisions = []
    for key in state.get("contract_keys", []):
        if key in RESERVED_KEYS:
            collisions.append(key)
            continue
        if key in CONTRACT_MIRROR:
            result[key] = CONTRACT_MIRROR[key](result)
        else:
            cv = (draft.get("contract_values") or {}).get(key)
            result[key] = cv if cv is not None else default_for_key(key)
    if collisions:
        result["change_log"].append({
            "ts": _ts(), "phase": "build",
            "summary": f"契约键与信封保留键冲突，保留信封字段: {collisions}",
        })
    _write_json(output / "result.json", result)
    _write_json(output / "review_report.md", _render_report(result, index or {}))
    print(f"build ok: findings={len(findings)} unchecked={len(unchecked)} "
          f"status={status} overall={overall}")
    return 0


# ---------------------------------------------------------------- selfcheck


def cmd_selfcheck(args):
    work, output, skill_root = Path(args.work), Path(args.output), SKILL_ROOT
    results = _load_json(output / "result.json")
    index = _load_json(work / "index.json")
    report_exists = (output / "review_report.md").exists()
    if results is None:
        rows = [("S12", False, "result.json 缺失")]
    else:
        rows = run_score_checks(results, index, skill_root)
    passed = sum(1 for _, ok, _ in rows if ok)
    alignment = {}
    for dim, sid, label in SCORE_ALIGNMENT:
        row = next((r for r in rows if r[0] == sid), None)
        alignment.setdefault(dim, {"items": [], "passed": 0, "total": 0})
        if row:
            alignment[dim]["items"].append({"id": sid, "label": label,
                                            "passed": row[1], "note": row[2]})
            alignment[dim]["passed"] += int(row[1])
            alignment[dim]["total"] += 1
    report = {
        "ok": passed == len(rows) and report_exists,
        "report_exists": report_exists,
        "summary": {"passed": passed, "total": len(rows)},
        "checks": [{"id": sid, "passed": ok, "note": note} for sid, ok, note in rows],
        "score_alignment": alignment,
    }
    _write_json(work / "selfcheck.json", report)
    print(f"selfcheck: passed={passed}/{len(rows)} report_exists={report_exists}")
    for dim in alignment:
        d = alignment[dim]
        print(f"  {dim}: {d['passed']}/{d['total']}")
    return 0


# ---------------------------------------------------------------- 入口


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
                   help="init 强制重新抽取文本（默认复用 work/text）")
    return p


def _write_minimal_result(args):
    try:
        out = Path(args.output)
        out.mkdir(parents=True, exist_ok=True)
        _write_json(out / "result.json", {
            "case_id": None, "status": "input_error",
            "verdict_summary": {"overall": "not_determinable"},
            "result": {"findings": [], "unchecked_items": []},
            "coverage": {"facets": {}, "scoring_alignment": {}},
            "sources": [], "uncertainty": [], "pending_human": [],
            "errors": [{"phase": "fatal", "errors": ["unexpected error, see stderr"]}],
            "degradations": ["unexpected error"], "changes": [],
            "change_log": [{"ts": _ts(), "phase": "fatal",
                            "summary": "unexpected error; minimal legal result written"}],
            "runtime": {"started_at": _ts(), "phases": []},
        })
    except Exception:
        pass


def dispatch(args):
    if args.command == "init":
        return cmd_init(args)
    if args.command == "show":
        return cmd_show(args)
    if args.command == "check":
        return cmd_check(args)
    if args.command == "build":
        return cmd_build(args)
    if args.command == "selfcheck":
        return cmd_selfcheck(args)
    build_parser().print_help()
    return 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    try:
        return dispatch(args)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[main] unexpected error: {exc}", file=sys.stderr)
        _write_minimal_result(args)
        return 0


if __name__ == "__main__":
    sys.exit(main())
