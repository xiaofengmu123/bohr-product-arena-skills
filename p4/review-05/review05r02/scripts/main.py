#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Orchestrator for the evidence-anchored academic audit skill.

Subcommands
-----------
run     discover inputs, build the corpus, seed candidate observations, and
        write an honest *partial* result.json plus report.md and plan.md in
        the first minutes, so any interruption still leaves a valid
        deliverable behind.
merge   merge an agent-adjudicated findings file (work/findings.json) into
        result.json, recompute status, and rebuild report.md. Idempotent:
        running it twice with the same findings yields the same findings list.
report  rebuild report.md from the current result.json.

Standard library only. Imports extract.py from the same directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
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
    "submission", "quality", "statistics", "method", "logic",
}
VERDICTS = {"confirmed", "excluded", "downgraded", "pending"}
BASIS_KINDS = {
    "material_quote", "general_principle", "target_journal_guide",
    "dataset_license", "ethics_policy", "unavailable",
}
BASIS_STATUSES = {"resolved", "general_principle", "unavailable"}
FACETS = [
    "academic_integrity", "citation", "privacy", "copyright", "ethics",
    "submission", "quality", "statistics", "method", "logic",
]
COVERAGE_STATUSES = {"checked_clean", "finding", "insufficient_evidence", "not_applicable", "not_checked"}

CORE_KEYS = {
    "case_id", "status", "report", "sources", "uncertainty", "change_log",
    "errors", "degradations", "pending_human", "runtime", "output_contract",
    "verification",
}

FINDING_REQUIRED = {
    "type", "severity", "category", "title", "file", "locator", "quote",
    "evidence", "basis", "revision",
}

SEVERITY_RANK = {"critical": 0, "major": 1, "minor": 2, "info": 3}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load_case(inp: Path):
    """Return (case_obj, case_id, contract, errors)."""
    errors = []
    items = extract.discover(inp)
    case_item = next((it for it in items if it["role"] == "case"), None)
    if case_item is None:
        return None, "unknown", {}, ["/input/case.json not found"]
    case, err = extract.read_json_robust(Path(case_item["path"]))
    if err:
        return None, "unknown", {}, [f"case.json unreadable: {err}"]
    case_id = None
    for key in ("case_id", "id", "challenge_id", "task_id"):
        val = case.get(key)
        if isinstance(val, str) and val:
            case_id = val
            break
    if not case_id:
        case_id = Path(case_item["path"]).stem
    contract = case.get("output_contract") if isinstance(case.get("output_contract"), dict) else {}
    return case, case_id, contract, errors


def echo_contract(contract):
    """Deep-copy the contract so every original key survives verbatim."""
    if isinstance(contract, dict):
        return {k: echo_contract(v) for k, v in contract.items()}
    if isinstance(contract, list):
        return [echo_contract(v) for v in contract]
    return contract


def flatten_contract(contract):
    """Flatten scalar leaves to top level; first occurrence wins on collision."""
    out = {}

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, (dict, list)):
                    walk(v)
                else:
                    out.setdefault(k, v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(contract)
    return out


def build_skeleton(case_id, contract, corpus, degradations, errors):
    contract_echo = echo_contract(contract) if contract else {}
    files = corpus.get("files", [])
    skeleton = {
        "case_id": case_id,
        "status": "partial",
        "report": {
            "summary": "",
            "scope": list(FACETS),
            "findings": [],
            "coverage": [
                {"facet": f, "status": "not_checked", "findings": [], "note": ""}
                for f in FACETS
            ],
            "checked_items": [],
            "pending_review": [],
            "not_checked": [],
        },
        "sources": [
            {
                "path": it["path"],
                "name": it["name"],
                "role": it["role"],
                "size": it["size"],
                "chars": it.get("chars", 0),
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
        },
        "verification": {
            "quote_pass": 0,
            "quote_fail": 0,
            "locator_pass": 0,
            "locator_fail": 0,
            "coverage_checked": 0,
            "coverage_total": len(FACETS),
        },
    }
    if contract_echo:
        skeleton["output_contract"] = contract_echo
        for key, value in flatten_contract(contract_echo).items():
            if key not in CORE_KEYS and key not in skeleton:
                skeleton[key] = value
    else:
        skeleton["output_contract"] = {}
        skeleton["contract_missing"] = True
    return skeleton


def write_report_skeleton(out: Path, case_id: str, status: str) -> None:
    text = (
        "# 学术规范与合规审查报告\n\n"
        f"- case_id: `{case_id}`\n"
        f"- status: `{status}`\n"
        f"- 结果文件: `/app/output/result.json`\n"
        f"- 生成时间: {utcnow()}\n\n"
        "## 结论摘要\n\n（待填写：总体结论、已覆盖检查项、主要风险与建议。）\n\n"
        "## 覆盖矩阵\n\n（按十项检查面逐项给出：已查无问题 / 发现问题 / 证据不足 / 不适用。）\n\n"
        "## 问题清单\n\n（每条按：编号 | 类型 | 严重度 | 位置 | 逐字证据 | 判定依据 | 修改建议。）\n\n"
        "## 待复核 / 未检查项\n\n（列出证据不足、材料缺失或未覆盖的检查项。）\n\n"
        "## 依据与局限\n\n（列出每条依据的权威来源、版本与日期；无法取得权威依据之处必须标注，不得编造条款。）\n\n"
        "## 变更记录\n\n（记录每次增量改动。）\n"
    )
    atomic_write_text(out / "report.md", text)


def write_plan(work: Path, case_id: str, candidate_count: int) -> None:
    text = (
        "# 执行计划（供审查 Agent 使用）\n\n"
        f"case_id: `{case_id}`\n\n"
        "## 阶段预算（总 900 秒 / 64 次模型请求）\n"
        "1. 契约确认与骨架落盘（0–3 分钟）：已由 main.py run 完成，任何中断都有合法 partial 结果。\n"
        "2. 证据准备（3–8 分钟）：核对 `work/evidence.json` 中的候选观察，补查遗漏项；必要时用平台 pdf-text-tool/ocr-tool 补齐 PDF 文本后重跑 run。\n"
        "3. 逐条判定（8–14 分钟）：对每个候选观察读原文，判定 成立/排除/降级/待复核；每条写全四要素与依据绑定。\n"
        "4. 覆盖收敛与自检（14–15 分钟）：确认十个检查面都有结论，运行 validate.py 并修复 FAIL。\n\n"
        f"## 候选观察\n\n`work/evidence.json` 已写入 {candidate_count} 条确定性候选观察；这些是待判定假设，不是结论。\n\n"
        "## 判定规则（关键）\n"
        "- 每条 finding 必须写 `candidate`（初始观察）与 `verdict`（confirmed/excluded/downgraded/pending）以及 `adjudication` 理由。\n"
        "- `basis.kind` 只能取 material_quote / general_principle / target_journal_guide / dataset_license / ethics_policy / unavailable；拿不到权威依据时写 unavailable，严重度降为 minor/info，并标 pending_human。\n"
        "- 风格差异、学科惯例、个人表述不算违规；不能凭标签猜内容；同一指标两个来源冲突时两条都记并标 source_conflict。\n\n"
        "## 输出\n"
        "把判定后的 findings 写入 `work/findings.json`（含 findings、coverage、uncertainty、pending_human、status_override 可选），再执行 `python3 -B scripts/main.py merge`。\n"
    )
    atomic_write_text(work / "plan.md", text)


def run_main(inp: Path, out: Path, work: Path) -> None:
    case_obj, case_id, contract, case_errors = load_case(inp)
    corpus, degradations = extract.build_corpus(inp, work)
    candidates = extract.detect_candidates(corpus)

    atomic_write_json(work / "corpus.json", corpus)
    atomic_write_json(work / "evidence.json", {
        "generated_at": utcnow(),
        "case_id": case_id,
        "candidates": candidates,
    })

    skeleton = build_skeleton(case_id, contract, corpus, degradations, case_errors)
    # Honest initial status: no parseable text -> insufficient_evidence;
    # unreadable case.json -> input_error; otherwise partial until merged.
    if not any(s.get("chars", 0) > 0 for s in skeleton["sources"]):
        skeleton["status"] = "insufficient_evidence"
    elif any("case.json unreadable" in e for e in case_errors):
        skeleton["status"] = "input_error"
    out.mkdir(parents=True, exist_ok=True)
    atomic_write_json(out / "result.json", skeleton)
    write_report_skeleton(out, case_id, skeleton["status"])
    write_plan(work, case_id, len(candidates))

    print(json.dumps({
        "case_id": case_id,
        "status": skeleton["status"],
        "files": len(corpus["files"]),
        "segments": len(corpus["segments"]),
        "candidates": len(candidates),
        "errors": len(case_errors),
        "degradations": len(degradations),
    }, ensure_ascii=False))


def normalize_finding(raw):
    """Coerce an agent-written finding into the canonical schema."""
    f = {
        "type": raw.get("type", "suggestion"),
        "severity": raw.get("severity", "info"),
        "category": raw.get("category", "quality"),
        "title": str(raw.get("title", "")).strip(),
        "file": str(raw.get("file", "")).strip(),
        "locator": str(raw.get("locator", "")).strip(),
        "quote": str(raw.get("quote", "")).strip(),
        "candidate": str(raw.get("candidate", "")).strip(),
        "verdict": raw.get("verdict", "confirmed"),
        "adjudication": str(raw.get("adjudication", "")).strip(),
        "evidence": _normalize_evidence(raw.get("evidence", [])),
        "basis": _normalize_basis(raw.get("basis", {})),
        "revision": str(raw.get("revision", "")).strip(),
        "confidence": raw.get("confidence", "medium"),
        "pending": str(raw.get("pending", "none")).strip() or "none",
    }
    if f["type"] not in FINDING_TYPES:
        f["type"] = "suggestion"
    if f["severity"] not in SEVERITIES:
        f["severity"] = "info"
    if f["category"] not in CATEGORIES:
        f["category"] = "quality"
    if f["verdict"] not in VERDICTS:
        f["verdict"] = "confirmed"
    if f["confidence"] not in {"high", "medium", "low"}:
        f["confidence"] = "medium"
    # Id is derived from content for deterministic, idempotent merges.
    key = "|".join([
        f["category"], f["file"], f["locator"],
        extract.normalize(f["quote"])[:80],
    ])
    f["id"] = "F-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    return f


def _normalize_evidence(raw):
    if isinstance(raw, str):
        return [{"file": "", "locator": "", "quote": raw, "role": "evidence"}]
    out = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                out.append({"file": "", "locator": "", "quote": item, "role": "evidence"})
            elif isinstance(item, dict):
                out.append({
                    "file": str(item.get("file", "")).strip(),
                    "locator": str(item.get("locator", "")).strip(),
                    "quote": str(item.get("quote", "")).strip(),
                    "role": str(item.get("role", "evidence")),
                })
    return out


def _normalize_basis(raw):
    if isinstance(raw, str):
        raw = {"kind": "unavailable", "text": raw}
    if not isinstance(raw, dict):
        raw = {}
    kind = raw.get("kind", "unavailable")
    if kind not in BASIS_KINDS:
        kind = "unavailable"
    status = raw.get("status")
    if status not in BASIS_STATUSES:
        if kind in {"material_quote", "target_journal_guide", "dataset_license", "ethics_policy"}:
            status = "resolved"
        elif kind == "general_principle":
            status = "general_principle"
        else:
            status = "unavailable"
    basis = {
        "kind": kind,
        "status": status,
        "source": str(raw.get("source", "")).strip(),
        "version": str(raw.get("version", "")).strip(),
        "date": str(raw.get("date", "")).strip(),
        "text": str(raw.get("text", "")).strip(),
    }
    return basis


def basis_summary(basis):
    text = basis.get("text", "") or "(未提供依据原文)"
    prefix = {
        "material_quote": "材料原文",
        "general_principle": "通用审查惯例",
        "target_journal_guide": "目标期刊指南",
        "dataset_license": "数据/代码许可",
        "ethics_policy": "伦理政策",
        "unavailable": "依据缺失",
    }.get(basis.get("kind"), "依据")
    if basis.get("source"):
        prefix += f"·{basis['source']}"
    return f"{prefix}: {text}"


def validate_finding(f):
    """Return list of problems for a normalized finding (schema-level)."""
    problems = []
    missing = [k for k in FINDING_REQUIRED if not f.get(k) and f.get(k) != []]
    if missing:
        problems.append("missing: " + ",".join(missing))
    if f["basis"]["kind"] == "unavailable" and f["severity"] in {"critical", "major"}:
        problems.append("basis unavailable but severity is high; cap at minor/info")
    if f["verdict"] == "excluded" and f["severity"] != "info":
        problems.append("excluded findings should be severity info")
    return problems


def sort_findings(findings):
    return sorted(findings, key=lambda f: (
        SEVERITY_RANK.get(f["severity"], 9),
        f["category"],
        f["file"],
        f["locator"],
        f["id"],
    ))


def derive_status(result):
    errors = result.get("errors", [])
    if any("case.json" in e for e in errors):
        return "input_error"
    sources = result.get("sources", [])
    has_text = any(s.get("chars", 0) > 0 for s in sources)
    if not has_text:
        return "insufficient_evidence"
    coverage = result["report"].get("coverage", [])
    all_checked = len(coverage) == len(FACETS) and all(
        c.get("status") in {"checked_clean", "finding", "not_applicable"}
        for c in coverage
    )
    ver = result.get("verification", {})
    quotes_ok = ver.get("quote_fail", 0) == 0 and ver.get("locator_fail", 0) == 0
    if all_checked and quotes_ok and not errors:
        return "ok"
    return "partial"


def merge_findings(inp: Path, out: Path, work: Path, findings_path: Path) -> None:
    result_path = out / "result.json"
    if not result_path.exists():
        print("error: result.json not found; run `run` first", file=sys.stderr)
        sys.exit(1)
    result = extract.read_json_robust(result_path)[0]
    if result is None:
        print("error: result.json unreadable", file=sys.stderr)
        sys.exit(1)

    findings_obj = extract.read_json_robust(findings_path)[0]
    if findings_obj is None:
        print("error: findings file unreadable", file=sys.stderr)
        sys.exit(1)
    if isinstance(findings_obj, list):
        findings_obj = {"findings": findings_obj}

    raw_findings = findings_obj.get("findings", [])
    if not isinstance(raw_findings, list):
        raw_findings = []

    merged = []
    schema_errors = []
    for idx, raw in enumerate(raw_findings, 1):
        if not isinstance(raw, dict):
            schema_errors.append(f"finding #{idx} is not an object; skipped")
            continue
        f = normalize_finding(raw)
        problems = validate_finding(f)
        if not f["quote"] or not f["file"]:
            schema_errors.append(f"finding #{idx} lacks quote/file; skipped")
            continue
        if problems:
            schema_errors.append(f"{f['id']}: " + "; ".join(problems))
        merged.append(f)

    merged = sort_findings(merged)

    coverage_raw = findings_obj.get("coverage", [])
    coverage = result["report"].get("coverage", [])
    if isinstance(coverage_raw, list) and coverage_raw:
        new_coverage = []
        for c in coverage_raw:
            if not isinstance(c, dict):
                continue
            facet = c.get("facet")
            status = c.get("status")
            if facet not in FACETS or status not in COVERAGE_STATUSES:
                continue
            new_coverage.append({
                "facet": facet,
                "status": status,
                "findings": [str(x) for x in c.get("findings", [])],
                "note": str(c.get("note", "")).strip(),
            })
        # Ensure all facets remain present.
        have = {c["facet"] for c in new_coverage}
        for f in FACETS:
            if f not in have:
                new_coverage.append({"facet": f, "status": "not_checked", "findings": [], "note": ""})
        new_coverage = [c for c in new_coverage if c["facet"] in FACETS]
        new_coverage.sort(key=lambda c: FACETS.index(c["facet"]))
        coverage = new_coverage

    now = utcnow()
    result["report"]["findings"] = merged
    result["report"]["coverage"] = coverage
    if isinstance(findings_obj.get("uncertainty"), list):
        result["uncertainty"] = findings_obj["uncertainty"]
    if isinstance(findings_obj.get("pending_human"), list):
        result["pending_human"] = findings_obj["pending_human"]

    override = findings_obj.get("status_override")
    result["status"] = override if override in ALLOWED_STATUS else derive_status(result)

    checked = sum(1 for c in coverage if c["status"] in {"checked_clean", "finding", "not_applicable"})
    result["verification"]["coverage_checked"] = checked
    result["verification"]["coverage_total"] = len(FACETS)
    result["runtime"]["steps"].append({"step": "merge", "at": now, "findings": len(merged)})
    result["change_log"].append({
        "time": now,
        "event": f"merged {len(merged)} findings; coverage_checked={checked}/{len(FACETS)}; schema_errors={len(schema_errors)}",
    })
    if schema_errors:
        result["errors"].extend(schema_errors)

    atomic_write_json(result_path, result)
    write_report(out, result)
    print(json.dumps({
        "case_id": result["case_id"],
        "status": result["status"],
        "findings": len(merged),
        "coverage_checked": checked,
        "schema_errors": len(schema_errors),
    }, ensure_ascii=False))


def write_report(out: Path, result) -> None:
    report = result.get("report", {})
    lines = [
        "# 学术规范与合规审查报告",
        "",
        f"- case_id: `{result.get('case_id')}`",
        f"- status: `{result.get('status')}`",
        f"- 结果文件: `/app/output/result.json`",
        f"- 生成时间: {utcnow()}",
        "",
        "## 结论摘要",
        "",
        report.get("summary") or "（未填写）",
        "",
        "## 覆盖矩阵",
        "",
        "| 检查面 | 状态 | 关联问题 | 说明 |",
        "|---|---|---|---|",
    ]
    for c in report.get("coverage", []):
        lines.append(
            f"| {c.get('facet','')} | {c.get('status','')} | {', '.join(c.get('findings', []))} | {c.get('note','')} |"
        )
    lines += [
        "",
        "## 问题清单",
        "",
    ]
    findings = report.get("findings", [])
    if not findings:
        lines.append("（暂无已判定问题。）")
    groups = [
        ("critical", "严重"), ("major", "主要"), ("minor", "次要"), ("info", "提示"),
    ]
    for sev, label in groups:
        group = [f for f in findings if f.get("severity") == sev]
        if not group:
            continue
        lines.append(f"### {label}（{sev}）")
        lines.append("")
        for f in group:
            lines.append(f"#### {f['id']} — {f.get('title','')}")
            lines.append("")
            lines.append(f"- 类型: `{f['type']}`；严重度: `{f['severity']}`；类别: `{f['category']}`；判定: `{f['verdict']}`；置信度: `{f['confidence']}`")
            lines.append(f"- 位置: `{f['file']}` @ `{f['locator']}`")
            lines.append(f"- 逐字证据: `{f['quote']}`")
            if f.get("candidate"):
                lines.append(f"- 候选观察: {f['candidate']}")
            if f.get("adjudication"):
                lines.append(f"- 判定理由: {f['adjudication']}")
            for ev in f.get("evidence", []):
                lines.append(f"- 佐证: `{ev.get('file','')}` @ `{ev.get('locator','')}` `{ev.get('quote','')}`")
            lines.append(f"- 判定依据: {basis_summary(f['basis'])}")
            lines.append(f"- 修改建议: {f.get('revision','')}")
            if f.get("pending") and f.get("pending") != "none":
                lines.append(f"- 待复核: {f['pending']}")
            lines.append("")
    lines += [
        "## 待复核 / 未检查项",
        "",
    ]
    pending = report.get("pending_review", []) or []
    not_checked = report.get("not_checked", []) or []
    pending_human = result.get("pending_human", []) or []
    if pending_human:
        lines.append("- 待人工复核: " + "; ".join(str(x) for x in pending_human))
    if pending:
        lines.append("- 待复核: " + "; ".join(pending))
    if not_checked:
        lines.append("- 未检查: " + "; ".join(not_checked))
    if not pending_human and not pending and not not_checked:
        lines.append("（无。）")
    lines += [
        "",
        "## 依据与局限",
        "",
        "每条问题的判定依据均绑定权威来源；拿不到权威依据处一律标注 `unavailable`，不得编造条款、期刊要求或文献元数据。",
        "",
    ]
    for err in result.get("errors", []):
        lines.append(f"- 错误: {err}")
    for deg in result.get("degradations", []):
        lines.append(f"- 降级: {deg.get('file','')} / {deg.get('stage','')} / {deg.get('detail','')}")
    for u in result.get("uncertainty", []):
        lines.append(f"- 不确定: {u}")
    lines += [
        "",
        "## 变更记录",
        "",
    ]
    for ch in result.get("change_log", []):
        lines.append(f"- {ch.get('time','')}: {ch.get('event','')}")
    lines.append("")
    atomic_write_text(out / "report.md", "\n".join(lines))


def report_main(out: Path) -> None:
    result_path = out / "result.json"
    if not result_path.exists():
        print("error: result.json not found", file=sys.stderr)
        sys.exit(1)
    result = extract.read_json_robust(result_path)[0]
    if result is None:
        print("error: result.json unreadable", file=sys.stderr)
        sys.exit(1)
    write_report(out, result)
    print("report.md rebuilt")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Evidence-anchored academic audit orchestrator")
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="discover, extract, seed candidates, write partial skeleton")
    p_run.add_argument("--input", default=str(DEFAULT_INPUT))
    p_run.add_argument("--output", default=str(DEFAULT_OUTPUT))
    p_run.add_argument("--work", default=str(DEFAULT_WORK))

    p_merge = sub.add_parser("merge", help="merge agent findings into result.json")
    p_merge.add_argument("--input", default=str(DEFAULT_INPUT))
    p_merge.add_argument("--output", default=str(DEFAULT_OUTPUT))
    p_merge.add_argument("--work", default=str(DEFAULT_WORK))
    p_merge.add_argument("--findings", default=str(DEFAULT_WORK / "findings.json"))

    p_report = sub.add_parser("report", help="rebuild report.md from result.json")
    p_report.add_argument("--output", default=str(DEFAULT_OUTPUT))

    args = parser.parse_args(argv)
    command = args.command or "run"

    if command == "run":
        run_main(Path(args.input), Path(args.output), Path(args.work))
    elif command == "merge":
        merge_findings(Path(args.input), Path(args.output), Path(args.work), Path(args.findings))
    elif command == "report":
        report_main(Path(args.output))
    else:
        parser.print_help()
        sys.exit(2)


if __name__ == "__main__":
    main()
