#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Orchestrator for the source-bound academic compliance audit skill.

Subcommands
-----------
run   (default) discover inputs, parse text, extract machine evidence, and
      write a valid *partial* result.json skeleton plus evidence.json,
      report.md and plan.md. Designed to run in the first minutes so an
      interruption still leaves a parseable, honest deliverable behind.
merge merge an agent-written findings file into result.json idempotently:
      upsert findings, recompute coverage, append change_log (skipped when the
      findings content is unchanged), and regenerate report.md.

Standard library only. Imports extract.py from the same directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import extract

DEFAULT_INPUT = Path("/input")
DEFAULT_OUTPUT = Path("/app/output")
DEFAULT_WORK = Path("/app/work")

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}

CORE_KEYS = {
    "case_id", "status", "report", "sources", "uncertainty", "change_log",
    "errors", "degradations", "pending_human", "runtime", "output_contract",
}

FINDING_REQUIRED = {
    "id", "type", "severity", "category", "title", "file", "locator",
    "quote", "evidence", "basis", "revision",
}

# Extra keys preserved verbatim when the agent supplies them. `basis_kind`
# and `basis_source` bind every judgment to a checkable authority.
FINDING_EXTRA = {
    "basis_kind", "basis_source", "evidence_status", "counter_check",
    "verdict", "evidence_tier", "confidence",
}


# ---------------------------------------------------------------------------
# Case loading
# ---------------------------------------------------------------------------

def load_case(inp):
    """Return (case_obj, case_id, contract, errors)."""
    errors = []
    files = extract.discover(inp)
    case_item = next((it for it in files if it["role"] == "case"), None)
    if case_item is None:
        return None, "unknown", {}, ["/input/case.json not found"]
    case, err = extract.read_json_robust(case_item["path"])
    if err:
        return None, "unknown", {}, [f"case.json unreadable: {err}"]
    case_id = None
    for key in ("case_id", "id", "challenge_id", "task_id"):
        val = case.get(key)
        if isinstance(val, str) and val:
            case_id = val
            break
    if not case_id:
        case_id = case_item["path"].stem
    contract = case.get("output_contract")
    if not isinstance(contract, dict):
        contract = {}
    return case, case_id, contract, errors


def build_coverage(taxonomy):
    return {name: {"status": "not_checked", "note": ""} for name in taxonomy["facet_names"]}


def build_skeleton(case_id, contract, taxonomy, files, errors, degradations, fingerprint):
    scope = taxonomy["facet_names"]
    if not files:
        status = "insufficient_evidence"
    elif any("unreadable" in e for e in errors) or any("case.json" in e for e in errors):
        status = "input_error"
    else:
        status = "partial"
    skeleton = {
        "case_id": case_id,
        "status": status,
        "report": {
            "summary": "",
            "scope": scope,
            "coverage": build_coverage(taxonomy),
            "findings": [],
            "checked_items": [],
            "pending_review": [],
            "not_checked": list(scope),
        },
        "sources": [
            {"path": it["path"].as_posix(), "relpath": it["relpath"],
             "role": it["role"], "size": it["size"]}
            for it in files
        ],
        "uncertainty": [],
        "change_log": [{"time": extract.utcnow(), "event": "partial skeleton written"}],
        "errors": list(errors),
        "degradations": list(degradations),
        "pending_human": [],
        "runtime": {
            "started_at": extract.utcnow(),
            "steps": [{"step": "skeleton", "at": extract.utcnow()}],
            "input_fingerprint": fingerprint,
        },
    }
    if contract:
        defaults = extract.default_for(contract)
        skeleton["output_contract"] = defaults
        for key, value in extract.flatten_leafs(defaults).items():
            if key not in CORE_KEYS and key not in skeleton:
                skeleton[key] = value
    else:
        skeleton["output_contract"] = {}
        skeleton["contract_missing"] = True
    return skeleton


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def build_report_markdown(result):
    r = result.get("report", {})
    lines = [
        "# 学术规范与合规审查报告", "",
        f"- case_id: `{result.get('case_id')}`",
        f"- status: `{result.get('status')}`",
        f"- 生成时间: {extract.utcnow()}",
        "",
        "## 结论摘要", "",
        str(r.get("summary") or "（未填写）"), "",
        "## 问题清单", "",
    ]
    findings = r.get("findings", [])
    if not findings:
        lines.append("（暂无已确认问题。）")
    for f in findings:
        lines.append(f"### {f.get('id')} · {f.get('type')} · {f.get('severity')} · {f.get('category')}")
        lines.append("")
        lines.append(f"- 标题: {f.get('title', '')}")
        lines.append(f"- 位置: `{f.get('file', '')}` — `{f.get('locator', '')}`")
        if f.get("basis_kind"):
            lines.append(f"- 依据类型: `{f.get('basis_kind')}`" +
                         (f"（来源: {f.get('basis_source')}）" if f.get("basis_source") else ""))
        lines.append("")
        lines.append(f"> 逐字证据: {f.get('quote', '')}")
        lines.append("")
        lines.append(f"- 证据说明: {f.get('evidence', '')}")
        lines.append(f"- 判定依据: {f.get('basis', '')}")
        lines.append(f"- 修改建议: {f.get('revision', '')}")
        cc = f.get("counter_check")
        if isinstance(cc, dict) and cc.get("searched"):
            lines.append(f"- 反证检查: {cc.get('result', '?')}")
        lines.append("")
    lines += ["## 覆盖检查", "", "| 方向 | 状态 | 备注 |", "|---|---|---|"]
    for cat in r.get("scope", []):
        cov = r.get("coverage", {}).get(cat, {})
        lines.append(f"| {cat} | {cov.get('status', 'not_checked')} | {cov.get('note', '')} |")
    lines += ["", "## 待复核 / 未检查", ""]
    pending = result.get("pending_human", [])
    lines.append(json.dumps(pending, ensure_ascii=False) if pending else "（无。）")
    lines += ["", "## 依据与局限", ""]
    for u in result.get("uncertainty", []):
        lines.append(f"- {json.dumps(u, ensure_ascii=False)}")
    for e in result.get("errors", []):
        lines.append(f"- 错误: {e}")
    for d in result.get("degradations", []):
        lines.append(f"- 降级: {d}")
    lines.append("")
    return "\n".join(lines)


def write_plan(work, case_id, taxonomy):
    facets = ", ".join(taxonomy["facet_names"])
    text = (
        "# 执行计划（供审查 Agent 使用）\n\n"
        f"- case_id: `{case_id}`\n"
        f"- 检查面: {facets}\n"
        "- 总预算: 900 秒 / 64 次模型请求，单次响应 16384 token。\n"
        "- 铁律: 前 3 分钟先落盘 result.json 骨架；此后只做增量改进，任何时刻 result.json 都是合法 JSON。\n\n"
        "## 逐条落实（对应评分维度）\n"
        "1. 先跑 `python3 -B scripts/main.py run --input /input --output /app/output --work /app/work`。\n"
        "2. 读 evidence.json 的候选观察 + 反证提示 + 原文，逐条**证伪**：确认 / 排除 / 降级 / 待复核，绝不照单全收。\n"
        "3. 按全部检查面逐项补查遗漏，更新 coverage 状态（checked / pending_review / not_checked）。\n"
        "4. 每条 finding 写全 11 个必填字段 + basis_kind / basis_source；quote 必须能回查原文。\n"
        "5. 增量 merge（幂等），追加 change_log；最后跑 validate 修 FAIL。\n\n"
        "## 严禁\n"
        "- 不把风格差异、学科惯例、个人表述判为违规。\n"
        "- 不编造条款、期刊要求、文献元数据、DOI、评分或运行结果。\n"
        "- 不凭材料标签猜内容；必须读实际文件。\n"
    )
    extract.atomic_write_text(work / "plan.md", text)


# ---------------------------------------------------------------------------
# run subcommand
# ---------------------------------------------------------------------------

def cmd_run(inp, out, work):
    inp, out, work = Path(inp), Path(out), Path(work)
    files = extract.discover(inp)
    case, case_id, contract, errors = load_case(inp)
    taxonomy = extract.resolve_taxonomy(case, contract)
    fingerprint = extract.input_fingerprint(inp)
    degradations = []

    parsed_dir = work / "parsed"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    text_items = []
    text_index = []
    for it in files:
        if it["role"] == "pdf":
            txt = parsed_dir / (it["path"].stem + ".txt")
            text = ""
            err = None
            # Prefer an already-parsed text layer (e.g. produced by the
            # platform pdf-text-tool/OCR into work/parsed/<stem>.txt); only
            # fall back to a local pdftotext attempt otherwise.
            if txt.exists() and txt.stat().st_size > 0:
                text, err = extract.read_text_robust(txt)
                if not text:
                    err = "existing parsed file empty"
            else:
                text, err = extract.pdf_to_text(it["path"], txt)
            if err:
                degradations.append(f"PDF not parsed: {it['relpath']} — {err}")
                text_index.append({
                    "file": it["relpath"], "role": "pdf", "text_path": None,
                    "size": it["size"], "has_text": False, "note": err})
            else:
                text_items.append({"relpath": it["relpath"], "role": "material", "text": text})
                text_index.append({
                    "file": it["relpath"], "role": "pdf",
                    "text_path": txt.as_posix(), "size": it["size"],
                    "has_text": bool(text.strip())})
        elif it["role"] in ("material", "task"):
            text, err = extract.read_text_robust(it["path"])
            if err:
                degradations.append(f"text not read: {it['relpath']} — {err}")
                text_index.append({
                    "file": it["relpath"], "role": it["role"], "text_path": None,
                    "size": it["size"], "has_text": False, "note": err})
            else:
                text_items.append({"relpath": it["relpath"], "role": it["role"], "text": text})
                text_index.append({
                    "file": it["relpath"], "role": it["role"],
                    "text_path": it["path"].as_posix(), "size": it["size"],
                    "has_text": bool(text.strip())})

    observations = extract.extract_observations(text_items)
    counter_hints = extract.extract_counter_hints(text_items)
    extract.atomic_write_json(work / "evidence.json", {
        "fingerprint": fingerprint,
        "case_id": case_id,
        "taxonomy": taxonomy,
        "text_index": text_index,
        "observations": observations,
        "counter_hints": counter_hints,
        "note": "候选观察与反证提示均为待查证据，非判定结论；须逐条证伪后再提升为 finding。",
    })

    skeleton = build_skeleton(case_id, contract, taxonomy, files, errors, degradations, fingerprint)
    extract.atomic_write_json(out / "result.json", skeleton)
    extract.atomic_write_text(out / "report.md", build_report_markdown(skeleton))
    write_plan(work, case_id, taxonomy)
    print(json.dumps({
        "case_id": case_id,
        "status": skeleton["status"],
        "observations": len(observations),
        "counter_hints": len(counter_hints),
        "files": len(files),
        "errors": len(errors),
        "degradations": len(degradations),
        "output": (out / "result.json").as_posix(),
    }, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# merge subcommand
# ---------------------------------------------------------------------------

def _clean_finding(f, idx, taxonomy):
    clean = {k: (f.get(k) if f.get(k) is not None else "") for k in FINDING_REQUIRED}
    clean["id"] = clean["id"] or f"finding-{idx:03d}"
    clean["type"] = clean["type"] if clean["type"] in taxonomy["finding_types"] else "suggestion"
    clean["severity"] = clean["severity"] if clean["severity"] in taxonomy["severities"] else "minor"
    clean["category"] = clean["category"] if clean["category"] in taxonomy["categories"] else "quality"
    for key in FINDING_EXTRA:
        if key in f and f[key] is not None:
            clean[key] = f[key]
    return clean


def _findings_fingerprint(findings):
    payload = json.dumps(findings, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_status(result, coverage):
    if result.get("status") == "input_error":
        return "input_error"
    if not result.get("sources"):
        return "insufficient_evidence"
    findings = result.get("report", {}).get("findings", [])
    if not findings and all(c.get("status") == "not_checked" for c in coverage.values()):
        return "insufficient_evidence"
    if result.get("pending_human") or any(
            c.get("status") in ("pending_review", "not_checked") for c in coverage.values()):
        return "partial"
    return "ok"


def cmd_merge(inp, out, work, findings_path):
    inp, out, work = Path(inp), Path(out), Path(work)
    findings_obj, err = extract.read_json_robust(findings_path)
    if err:
        print(f"error: cannot read findings: {err}", file=sys.stderr)
        return 1
    result_path = out / "result.json"
    result, err = extract.read_json_robust(result_path)
    if err:
        print(f"error: cannot read result.json: {err}", file=sys.stderr)
        return 1

    taxonomy, _ = extract.read_json_robust(work / "evidence.json")
    taxonomy = (taxonomy or {}).get("taxonomy") or extract.resolve_taxonomy({}, {})

    raw_findings = findings_obj.get("findings") if isinstance(findings_obj, dict) else None
    if not isinstance(raw_findings, list):
        raw_findings = []
    cleaned = [_clean_finding(f, i, taxonomy) for i, f in enumerate(raw_findings, start=1)]

    report = result.setdefault("report", {})
    report["scope"] = taxonomy["facet_names"]
    report.setdefault("coverage", build_coverage(taxonomy))
    old_fingerprint = result.get("runtime", {}).get("findings_fingerprint")
    new_fingerprint = _findings_fingerprint(cleaned)

    report["findings"] = cleaned
    if isinstance(findings_obj, dict) and findings_obj.get("summary"):
        report["summary"] = findings_obj["summary"]
    if isinstance(findings_obj, dict) and isinstance(findings_obj.get("coverage"), dict):
        for cat, entry in findings_obj["coverage"].items():
            if cat in report["coverage"] and isinstance(entry, dict):
                report["coverage"][cat] = {
                    "status": entry.get("status", "not_checked"),
                    "note": entry.get("note", ""),
                }

    report["checked_items"] = [c for c, e in report["coverage"].items()
                               if e.get("status") == "checked"]
    report["pending_review"] = [c for c, e in report["coverage"].items()
                                if e.get("status") == "pending_review"]
    report["not_checked"] = [c for c, e in report["coverage"].items()
                             if e.get("status") == "not_checked"]

    if isinstance(findings_obj, dict):
        for key in ("pending_human", "uncertainty"):
            if isinstance(findings_obj.get(key), list):
                result[key] = findings_obj[key]
        if isinstance(findings_obj.get("errors"), list):
            result["errors"] = list(result.get("errors", [])) + findings_obj["errors"]

    log = result.setdefault("change_log", [])
    if isinstance(findings_obj, dict) and isinstance(findings_obj.get("change_log"), list):
        log.extend(findings_obj["change_log"])
    if new_fingerprint != old_fingerprint:
        log.append({"time": extract.utcnow(), "event": f"merge: {len(cleaned)} findings"})
        changed = True
    else:
        changed = False

    if isinstance(findings_obj, dict) and findings_obj.get("status") in ALLOWED_STATUS:
        result["status"] = findings_obj["status"]
    else:
        result["status"] = compute_status(result, report["coverage"])

    result["runtime"] = result.get("runtime") or {}
    result["runtime"]["last_merge_at"] = extract.utcnow()
    result["runtime"]["findings_fingerprint"] = new_fingerprint

    extract.atomic_write_json(result_path, result)
    extract.atomic_write_text(out / "report.md", build_report_markdown(result))
    print(json.dumps({
        "status": result["status"],
        "findings": len(cleaned),
        "changed": changed,
    }, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Source-bound academic compliance audit orchestrator.")
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="discover inputs and write partial skeleton")
    p_run.add_argument("--input", default=DEFAULT_INPUT)
    p_run.add_argument("--output", default=DEFAULT_OUTPUT)
    p_run.add_argument("--work", default=DEFAULT_WORK)

    p_merge = sub.add_parser("merge", help="merge agent findings into result.json")
    p_merge.add_argument("--input", default=DEFAULT_INPUT)
    p_merge.add_argument("--output", default=DEFAULT_OUTPUT)
    p_merge.add_argument("--work", default=DEFAULT_WORK)
    p_merge.add_argument("--findings", required=True)

    args = parser.parse_args(argv)
    if args.command in (None, "run"):
        return cmd_run(Path(args.input), Path(args.output), Path(args.work))
    if args.command == "merge":
        return cmd_merge(Path(args.input), Path(args.output), Path(args.work), Path(args.findings))
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
