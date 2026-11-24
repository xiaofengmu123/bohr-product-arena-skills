#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Orchestrator for the source-bound academic compliance audit skill.

Subcommands
-----------
run        Discover inputs, extract candidate observations, and write a valid
           *partial* result.json skeleton plus report.md, observations.json,
           coverage.json and plan.md. Designed to run in the first minutes so
           an interruption still leaves a parseable, honest deliverable.
merge      Merge an agent-written findings file into the existing result.json,
           recompute status, and regenerate report.md.
validate   Check result.json structure, quote traceability and coverage.
selftest   Run empty-input / bad-JSON / missing-input checks in a temp dir.

Standard library only. Import of extract.py from the same directory.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import extract

DEFAULT_INPUT = Path("/input")
DEFAULT_OUTPUT = Path("/app/output")
DEFAULT_WORK = Path("/app/work")

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
FINDING_TYPES = {"fact_error", "insufficient_evidence", "norm_violation", "suggestion"}
SEVERITIES = {"critical", "major", "minor", "info"}
CATEGORIES = {
    "academic_integrity", "citation", "privacy", "copyright", "ethics",
    "submission", "quality", "method", "statistics", "logic",
}
VERDICTS = {"confirmed", "suspected", "excluded", "pending_human"}
EVIDENCE_TIERS = {"verbatim", "inference", "external"}

CORE_KEYS = {
    "case_id", "status", "report", "sources", "uncertainty", "change_log",
    "errors", "degradations", "pending_human", "runtime", "output_contract",
}

FINDING_REQUIRED = {
    "id", "type", "severity", "category", "title", "file", "locator",
    "quote", "evidence", "basis", "revision",
}


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


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


# ---------------------------------------------------------------------------
# Skeleton building
# ---------------------------------------------------------------------------

def build_coverage():
    return {
        cat: {"status": "not_checked", "note": ""}
        for cat in sorted(CATEGORIES)
    }


def build_skeleton(case_id, contract, files, errors, degradations, parsed_notes):
    scope = sorted(CATEGORIES)
    if not files:
        status = "insufficient_evidence"
    elif any("unreadable" in e for e in errors):
        status = "input_error"
    else:
        status = "partial"
    skeleton = {
        "case_id": case_id,
        "status": status,
        "report": {
            "summary": "",
            "scope": scope,
            "coverage": build_coverage(),
            "findings": [],
            "checked_items": [],
            "pending_review": [],
            "not_checked": [],
        },
        "sources": [
            {
                "path": it["path"].as_posix(),
                "relpath": it["relpath"],
                "role": it["role"],
                "size": it["size"],
            }
            for it in files
        ],
        "uncertainty": [],
        "change_log": [{"time": utcnow(), "event": "partial skeleton written"}],
        "errors": list(errors),
        "degradations": list(degradations),
        "pending_human": [],
        "runtime": {
            "started_at": utcnow(),
            "steps": [{"step": "skeleton", "at": utcnow()}],
            "parsed_notes": parsed_notes,
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
    lines = []
    lines.append("# 学术规范与合规审查报告")
    lines.append("")
    lines.append(f"- case_id: `{result.get('case_id')}`")
    lines.append(f"- status: `{result.get('status')}`")
    lines.append(f"- 生成时间: {utcnow()}")
    lines.append("")
    lines.append("## 结论摘要")
    lines.append("")
    lines.append(str(r.get("summary") or "（未填写）"))
    lines.append("")
    lines.append("## 问题清单")
    lines.append("")
    findings = r.get("findings", [])
    if not findings:
        lines.append("（暂无已确认问题。）")
    for f in findings:
        lines.append(f"### {f.get('id')} · {f.get('type')} · {f.get('severity')} · {f.get('category')}")
        lines.append("")
        lines.append(f"- 标题: {f.get('title', '')}")
        lines.append(f"- 位置: `{f.get('file', '')}` — `{f.get('locator', '')}`")
        lines.append(f"- 判定: `{f.get('verdict', 'confirmed')}`（证据层级: {f.get('evidence_tier', 'verbatim')}）")
        lines.append("")
        lines.append(f"> 逐字证据: {f.get('quote', '')}")
        lines.append("")
        lines.append(f"- 证据说明: {f.get('evidence', '')}")
        lines.append(f"- 判定依据: {f.get('basis', '')}")
        lines.append(f"- 修改建议: {f.get('revision', '')}")
        lines.append("")
    lines.append("## 覆盖检查")
    lines.append("")
    lines.append("| 方向 | 状态 | 备注 |")
    lines.append("|---|---|---|")
    for cat in sorted(CATEGORIES):
        cov = r.get("coverage", {}).get(cat, {})
        lines.append(f"| {cat} | {cov.get('status', 'not_checked')} | {cov.get('note', '')} |")
    lines.append("")
    lines.append("## 待复核 / 未检查")
    lines.append("")
    pending = result.get("pending_human", [])
    if pending:
        for p in pending:
            lines.append(f"- {json.dumps(p, ensure_ascii=False)}")
    else:
        lines.append("（无。）")
    lines.append("")
    lines.append("## 依据与局限")
    lines.append("")
    for u in result.get("uncertainty", []):
        lines.append(f"- {json.dumps(u, ensure_ascii=False)}")
    for e in result.get("errors", []):
        lines.append(f"- 错误: {e}")
    for d in result.get("degradations", []):
        lines.append(f"- 降级: {d}")
    lines.append("")
    return "\n".join(lines)


def write_plan(work, case_id):
    text = (
        "# 执行计划（供审查 Agent 使用）\n\n"
        f"- case_id: `{case_id}`\n"
        "- 总预算: 900 秒 / 64 次模型请求，单次响应 16384 token。\n"
        "- 铁律: 前 3 分钟先落盘 result.json 骨架；此后只做增量改进，任何时刻 result.json 都是合法 JSON。\n\n"
        "## 逐条落实（对应评分维度）\n"
        "1. 先跑 `python3 -B scripts/main.py run --input /input --output /app/output --work /app/work`。\n"
        "2. 读 `observations.json`（机器候选）+ 原文，逐条**证伪**：确认 / 排除 / 降级 / 待复核，绝不照单全收。\n"
        "3. 按六个方向逐项补查遗漏，更新 `coverage.json` 状态（checked/pending_review/not_checked）。\n"
        "4. 每条 finding 写全 11 个必填字段 + verdict + evidence_tier；quote 必须能回查原文。\n"
        "5. 增量 merge，追加 change_log；最后跑 validate 修 FAIL。\n\n"
        "## 严禁\n"
        "- 不把风格差异、学科惯例、个人表述判为违规。\n"
        "- 不编造条款、期刊要求、文献元数据、DOI、评分或运行结果。\n"
        "- 不凭材料标签猜内容；必须读实际文件。\n"
    )
    atomic_write_text(work / "plan.md", text)


# ---------------------------------------------------------------------------
# run subcommand
# ---------------------------------------------------------------------------

def cmd_run(inp, out, work):
    files = extract.discover(inp)
    case, case_id, contract, errors = load_case(inp)
    degradations = []
    parsed_notes = []

    # Parse PDFs to work/parsed/*.txt (best effort).
    parsed_dir = work / "parsed"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    text_items = []
    for it in files:
        if it["role"] == "pdf":
            txt = parsed_dir / (it["path"].stem + ".txt")
            text, err = extract.pdf_to_text(it["path"], txt)
            if err:
                degradations.append(f"PDF not parsed: {it['relpath']} — {err}")
            else:
                parsed_notes.append(f"parsed {it['relpath']} -> {txt.name}")
                text_items.append({
                    "relpath": txt.as_posix(),
                    "role": "material",
                    "text": text,
                })
        elif it["role"] in ("material", "task"):
            text, err = extract.read_text_robust(it["path"])
            if err:
                degradations.append(f"text not read: {it['relpath']} — {err}")
            else:
                text_items.append({
                    "relpath": it["relpath"],
                    "role": it["role"],
                    "text": text,
                })

    observations = extract.extract_observations(text_items)
    atomic_write_json(work / "observations.json", {
        "count": len(observations),
        "candidates": observations,
        "note": "候选观察，非判定结论；须逐条证伪后再提升为 finding。",
    })
    atomic_write_json(work / "coverage.json", build_coverage())

    skeleton = build_skeleton(case_id, contract, files, errors, degradations, parsed_notes)
    atomic_write_json(out / "result.json", skeleton)
    atomic_write_text(out / "report.md", build_report_markdown(skeleton))
    write_plan(work, case_id)
    print(json.dumps({
        "case_id": case_id,
        "status": skeleton["status"],
        "observations": len(observations),
        "files": len(files),
        "errors": len(errors),
        "degradations": len(degradations),
        "output": (out / "result.json").as_posix(),
    }, ensure_ascii=False))


# ---------------------------------------------------------------------------
# merge subcommand
# ---------------------------------------------------------------------------

def _clean_finding(f, idx):
    """Normalise a finding; drop unknown keys and fill required schema."""
    clean = {k: f.get(k) for k in FINDING_REQUIRED}
    clean["id"] = clean["id"] or f"finding-{idx:03d}"
    clean["type"] = clean["type"] if clean["type"] in FINDING_TYPES else "suggestion"
    clean["severity"] = clean["severity"] if clean["severity"] in SEVERITIES else "minor"
    clean["category"] = clean["category"] if clean["category"] in CATEGORIES else "quality"
    for key in FINDING_REQUIRED:
        if clean[key] is None:
            clean[key] = ""
    verdict = f.get("verdict")
    clean["verdict"] = verdict if verdict in VERDICTS else "confirmed"
    tier = f.get("evidence_tier")
    clean["evidence_tier"] = tier if tier in EVIDENCE_TIERS else "verbatim"
    return clean


def compute_status(result):
    if result.get("status") == "input_error":
        return "input_error"
    report = result.get("report", {})
    findings = report.get("findings", [])
    coverage = report.get("coverage", {})
    if not result.get("sources"):
        return "insufficient_evidence"
    if not findings and all(c.get("status") == "not_checked" for c in coverage.values()):
        return "insufficient_evidence"
    if result.get("pending_human") or any(
        c.get("status") in ("pending_review", "not_checked") for c in coverage.values()
    ):
        return "partial"
    return "ok"


def cmd_merge(inp, out, work, findings_path):
    findings_obj, err = extract.read_json_robust(findings_path)
    if err:
        print(f"error: cannot read findings: {err}", file=sys.stderr)
        return 1
    if not isinstance(findings_obj, dict):
        findings_obj = {}
    result_path = out / "result.json"
    result, err = extract.read_json_robust(result_path)
    if err:
        print(f"error: cannot read result.json: {err}", file=sys.stderr)
        return 1

    findings_list = findings_obj.get("findings") if isinstance(findings_obj, dict) else []
    if not isinstance(findings_list, list):
        findings_list = []
    cleaned = [_clean_finding(f, i) for i, f in enumerate(findings_list, start=1)]

    report = result.setdefault("report", {})
    report["findings"] = cleaned
    if findings_obj.get("summary"):
        report["summary"] = findings_obj["summary"]
    if isinstance(findings_obj.get("coverage"), dict):
        cov = report.setdefault("coverage", build_coverage())
        for cat, entry in findings_obj["coverage"].items():
            if cat in cov and isinstance(entry, dict):
                cov[cat] = {"status": entry.get("status", "not_checked"),
                            "note": entry.get("note", "")}
    # Rebuild derived coverage buckets.
    report["checked_items"] = [c for c, e in report.get("coverage", {}).items()
                               if e.get("status") == "checked"]
    report["pending_review"] = [c for c, e in report.get("coverage", {}).items()
                                if e.get("status") == "pending_review"]
    report["not_checked"] = [c for c, e in report.get("coverage", {}).items()
                             if e.get("status") == "not_checked"]

    if isinstance(findings_obj.get("pending_human"), list):
        result["pending_human"] = findings_obj["pending_human"]
    if isinstance(findings_obj.get("uncertainty"), list):
        result["uncertainty"] = findings_obj["uncertainty"]
    if isinstance(findings_obj.get("errors"), list):
        result["errors"] = list(result.get("errors", [])) + findings_obj["errors"]

    # change_log: append agent entries then an automatic merge marker.
    log = result.setdefault("change_log", [])
    if isinstance(findings_obj.get("change_log"), list):
        log.extend(findings_obj["change_log"])
    log.append({"time": utcnow(), "event": f"merge: {len(cleaned)} findings"})

    if findings_obj.get("status") in ALLOWED_STATUS:
        result["status"] = findings_obj["status"]
    else:
        result["status"] = compute_status(result)
    result["runtime"] = result.get("runtime") or {}
    result["runtime"]["last_merge_at"] = utcnow()

    atomic_write_json(result_path, result)
    atomic_write_text(out / "report.md", build_report_markdown(result))
    atomic_write_json(work / "coverage.json", report.get("coverage", {}))
    print(json.dumps({"status": result["status"], "findings": len(cleaned)}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# validate subcommand
# ---------------------------------------------------------------------------

def cmd_validate(inp, out, work):
    result_path = out / "result.json"
    result, err = extract.read_json_robust(result_path)
    problems = []
    if err:
        print(f"FAIL result.json unreadable: {err}")
        return 1
    if not result.get("case_id"):
        problems.append("case_id empty")
    if result.get("status") not in ALLOWED_STATUS:
        problems.append(f"status not in {ALLOWED_STATUS}: {result.get('status')}")
    for key in ("sources", "uncertainty", "change_log", "errors", "degradations", "pending_human"):
        if not isinstance(result.get(key), list):
            problems.append(f"{key} missing or not a list")
    if "output_contract" not in result:
        problems.append("output_contract missing")
    report = result.get("report", {})
    if not isinstance(report.get("findings"), list):
        problems.append("report.findings missing")
    if not isinstance(report.get("coverage"), dict):
        problems.append("report.coverage missing")
    for i, f in enumerate(report.get("findings", []), start=1):
        for key in FINDING_REQUIRED:
            if key not in f:
                problems.append(f"finding {i} missing field {key}")
        if f.get("type") not in FINDING_TYPES:
            problems.append(f"finding {i} bad type {f.get('type')}")
        if f.get("severity") not in SEVERITIES:
            problems.append(f"finding {i} bad severity {f.get('severity')}")
        if f.get("category") not in CATEGORIES:
            problems.append(f"finding {i} bad category {f.get('category')}")
    # Quote traceability against original files (skip for path-based locators
    # that reference parsed PDF text already recorded in work/parsed).
    quote_missing = 0
    for f in report.get("findings", []):
        if not f.get("quote"):
            quote_missing += 1
            continue
        fpath = f.get("file", "")
        candidates = [inp / fpath, work / fpath, work / "parsed" / fpath]
        if not any(Path(c).exists() for c in candidates):
            problems.append(f"finding {f.get('id')} file not found: {fpath}")
            continue
        located = None
        for c in candidates:
            if Path(c).exists():
                located = extract.locate_quote(Path(c), f["quote"])
                if located:
                    break
        if located is None:
            problems.append(f"finding {f.get('id')} quote not traceable in {fpath}")
    # Coverage completeness: report how many directions are untouched.
    cov = report.get("coverage", {})
    untouched = [c for c, e in cov.items() if e.get("status") == "not_checked"]
    if problems:
        print("FAIL")
        for p in problems:
            print(" -", p)
        return 1
    print(f"OK status={result.get('status')} findings={len(report.get('findings', []))} "
          f"quote_missing={quote_missing} untouched={len(untouched)}")
    return 0


# ---------------------------------------------------------------------------
# selftest subcommand
# ---------------------------------------------------------------------------

def cmd_selftest():
    """Run robustness checks in a temp dir; exit 0 only if all pass."""
    from shutil import rmtree
    base = Path(tempfile.mkdtemp(prefix="audit-selftest-"))
    try:
        # 1) empty input dir
        empty = base / "empty_in"; empty.mkdir()
        empty_out = base / "empty_out"; empty_work = base / "empty_work"
        cmd_run(empty, empty_out, empty_work)
        r1, _ = extract.read_json_robust(empty_out / "result.json")
        assert r1 and r1.get("status") in ("partial", "insufficient_evidence"), "empty input status"
        # 2) bad JSON case file
        bad = base / "bad_in"; bad.mkdir()
        (bad / "case.json").write_text("{not json", encoding="utf-8")
        (bad / "mat.txt").write_text("sample text with [1] citation", encoding="utf-8")
        cmd_run(bad, base / "bad_out", base / "bad_work")
        r2, _ = extract.read_json_robust(base / "bad_out" / "result.json")
        assert r2 and r2.get("status") in ("partial", "input_error"), "bad json status"
        assert r2.get("errors"), "bad json should record an error"
        # 3) normal input with a citation observation
        ok = base / "ok_in"; ok.mkdir()
        (ok / "case.json").write_text(
            json.dumps({"case_id": "demo-1", "output_contract": {"summary": "s", "items": []}}),
            encoding="utf-8")
        (ok / "mat.txt").write_text("The trial used n = 40 and p < 0.05 [12].", encoding="utf-8")
        ok_out = base / "ok_out"; ok_work = base / "ok_work"
        cmd_run(ok, ok_out, ok_work)
        r3, _ = extract.read_json_robust(ok_out / "result.json")
        assert r3.get("case_id") == "demo-1", "case_id preserved"
        assert r3.get("output_contract", {}).get("summary") is None, "contract default"
        assert "summary" in r3, "contract flattened to top level"
        obs, _ = extract.read_json_robust(ok_work / "observations.json")
        assert obs.get("count", 0) >= 1, "observations extracted"
        # 4) validate the skeleton passes
        assert cmd_validate(ok, ok_out, ok_work) == 0, "skeleton validates"
        print("selftest: OK (empty / bad-json / normal / validate)")
        return 0
    except AssertionError as exc:
        print(f"selftest: FAIL — {exc}")
        return 1
    finally:
        rmtree(base, ignore_errors=True)


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

    p_val = sub.add_parser("validate", help="validate result.json")
    p_val.add_argument("--input", default=DEFAULT_INPUT)
    p_val.add_argument("--output", default=DEFAULT_OUTPUT)
    p_val.add_argument("--work", default=DEFAULT_WORK)

    sub.add_parser("selftest", help="run robustness self-tests")

    args = parser.parse_args(argv)
    if args.command in (None, "run"):
        return cmd_run(Path(args.input), Path(args.output), Path(args.work))
    if args.command == "merge":
        return cmd_merge(Path(args.input), Path(args.output), Path(args.work), Path(args.findings))
    if args.command == "validate":
        return cmd_validate(Path(args.input), Path(args.output), Path(args.work))
    if args.command == "selftest":
        return cmd_selftest()
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
