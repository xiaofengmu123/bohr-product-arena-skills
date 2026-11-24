#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Orchestrator for the academic-integrity-audit skill (v2).

Subcommands
-----------
run    (default) discover inputs, extract deterministic evidence, and write a
       valid *partial* result.json plus report.md skeleton, evidence.json and
       plan.md. Runs in the first minutes so any interruption still leaves a
       parseable, honest deliverable behind.
merge  merge an agent-written findings file into the existing result.json and
       regenerate report.md, appending a change_log entry.

Standard library only. Imports extract.py from the same directory.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import extract

DEFAULT_INPUT = Path("/input")
DEFAULT_OUTPUT = Path("/app/output")
DEFAULT_WORK = Path("/app/work")

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
FINDING_TYPES = {"fact_error", "insufficient_evidence", "norm_violation", "suggestion"}
SEVERITIES = {"critical", "major", "minor", "info"}
SEVERITY_RANK = {"critical": 0, "major": 1, "minor": 2, "info": 3}
FINDING_STATUSES = {"confirmed", "excluded", "pending"}
CATEGORIES = {
    "academic_integrity", "citation", "privacy", "copyright", "ethics",
    "submission", "quality", "statistics", "method", "logic", "fact", "norm",
}
CORE_KEYS = {
    "case_id", "status", "report", "sources", "uncertainty", "change_log",
    "errors", "degradations", "pending_human", "runtime", "output_contract",
    "contract_missing",
}
FINDING_REQUIRED = {
    "id", "type", "severity", "category", "title", "file", "locator", "quote",
    "evidence", "basis", "revision", "status",
}
DEFAULT_FACETS = [
    "academic_integrity", "citation", "privacy", "copyright", "ethics",
    "submission", "quality", "statistics", "method", "logic",
]


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
    """Return (case_obj, case_id, contract, facets, errors)."""
    errors = []
    items = extract.discover(inp)
    case_item = next((it for it in items if it["role"] == "case"), None)
    if case_item is None:
        return None, "unknown", {}, [], ["/input/case.json not found"]
    case, err = extract.read_json_robust(case_item["path"])
    if err:
        return None, "unknown", {}, [], [f"case.json unreadable: {err}"]
    case_id = None
    for key in ("case_id", "id", "challenge_id", "task_id"):
        val = case.get(key)
        if isinstance(val, str) and val:
            case_id = val
            break
    if not case_id:
        case_id = case_item["path"].stem
    contract = case.get("output_contract") if isinstance(case.get("output_contract"), dict) else {}
    facets = extract.load_facets(case) or DEFAULT_FACETS
    return case, case_id, contract, facets, errors


def build_skeleton(case_id, contract, facets, files, sources, errors, degradations):
    skeleton = {
        "case_id": case_id,
        "status": "partial",
        "report": {
            "summary": "",
            "scope": list(facets),
            "findings": [],
            "checked_items": [
                {"facet": f, "result": "not_checked", "note": ""} for f in facets
            ],
            "pending_review": [],
            "not_checked": list(facets),
        },
        "sources": [
            {
                "path": s["path"],
                "role": s["role"],
                "size": s["size"],
                "extracted": s["extracted"],
                "paragraph_count": len(s["paragraphs"]),
            }
            for s in sources
        ],
        "uncertainty": [],
        "change_log": [{"time": utcnow(), "event": "partial skeleton written"}],
        "errors": list(errors),
        "degradations": list(degradations),
        "pending_human": [],
        "runtime": {
            "started_at": utcnow(),
            "steps": [{"step": "skeleton", "at": utcnow()}],
            "report_path": "/app/output/report.md",
        },
    }
    if contract:
        skeleton["output_contract"] = json.loads(json.dumps(contract, ensure_ascii=False))
        for key, value in extract.flatten_contract(contract).items():
            if key not in CORE_KEYS and key not in skeleton:
                skeleton[key] = value
    else:
        skeleton["output_contract"] = {}
        skeleton["contract_missing"] = True
    return skeleton


def write_report_skeleton(out: Path, case_id: str, status: str, facets) -> None:
    facet_lines = "\n".join(f"- {f}" for f in facets)
    text = (
        "# 学术规范与合规审查报告\n\n"
        f"- case_id: `{case_id}`\n"
        f"- status: `{status}`\n"
        f"- 生成时间: {utcnow()}\n\n"
        "## 结论摘要\n\n（待填写：总体结论、已覆盖检查项、主要风险与建议。）\n\n"
        "## 问题清单\n\n"
        "（每条问题按：编号 | 类型 | 严重度 | 位置 | 逐字证据 | 判定依据 | 修改建议 填写。）\n\n"
        "## 审查维度覆盖\n\n" + facet_lines + "\n\n"
        "## 待复核 / 未检查项\n\n（列出证据不足、材料缺失或未覆盖的检查项。）\n\n"
        "## 依据与局限\n\n（列出所用规范条款、期刊要求、授权范围及其版本/日期，并说明无法判断之处。）\n"
    )
    atomic_write_text(out / "report.md", text)


def write_evidence(work: Path, sources, observations, facets, contract) -> None:
    atomic_write_json(
        work / "evidence.json",
        {
            "sources": sources,
            "observations": observations,
            "facets": facets,
            "output_contract": contract,
            "generated_at": utcnow(),
        },
    )


def write_plan(work: Path, case_id: str, facets) -> None:
    facet_lines = "\n".join(f"- {f}" for f in facets)
    text = (
        "# 执行计划（供审查 Agent 使用）\n\n"
        f"- case_id: `{case_id}`\n"
        "- 总预算：900 秒 / 64 次模型请求；前 3 分钟先落盘 partial 骨架，之后只做增量改进。\n\n"
        "## 本案例审查维度（以 case.json 为准，缺省为默认六方向 + 通用质量）\n\n"
        + facet_lines + "\n\n"
        "## 步骤\n\n"
        "1. 读 `/app/work/evidence.json`：先看 `sources` 的全文段落（paragraphs[].norm），再核对 `observations` 候选。\n"
        "2. 逐条判定候选：成立→写 confirmed；不成立→excluded（在 change_note 或 change_log 记录原因）；无法确认→pending。\n"
        "3. 按每个维度补查遗漏，每条 finding 写全 id/type/severity/category/title/file/locator/quote/evidence/basis/revision/status。\n"
        "4. quote 必须逐字取自 paragraph.norm；file 用 `sources[].path`，locator 用 `p<N>`。\n"
        "5. findings 写入 `/app/work/findings.json`，运行 `python3 -B scripts/main.py merge ...`。\n"
        "6. 运行 `python3 -B scripts/validate.py ...` 自检并修 FAIL。\n\n"
        "## 判题提醒\n\n"
        "- 风格差异、学科惯例、个人表述、非母语措辞不判违规。\n"
        "- 每条问题必须有可核对依据（条款/期刊要求/授权范围原文）；无依据标 `basis_unavailable` 或 pending。\n"
        "- 冲突信息同时记录并标 `source_conflict`；受限数据只标位置与类型，不复述内容。\n"
    )
    atomic_write_text(work / "plan.md", text)


def run(inp: Path, out: Path, work: Path) -> int:
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    files = extract.discover(inp)
    if not files:
        skeleton = {
            "case_id": "unknown",
            "status": "insufficient_evidence",
            "report": {"summary": "输入目录为空或无材料。", "scope": DEFAULT_FACETS,
                       "findings": [], "checked_items": [], "pending_review": [], "not_checked": []},
            "sources": [],
            "uncertainty": ["/input 下未发现任何文件"],
            "change_log": [{"time": utcnow(), "event": "empty input -> insufficient_evidence"}],
            "errors": ["/input is empty or missing"],
            "degradations": [],
            "pending_human": [],
            "runtime": {"started_at": utcnow(), "report_path": "/app/output/report.md"},
            "output_contract": {},
            "contract_missing": True,
        }
        atomic_write_json(out / "result.json", skeleton)
        atomic_write_text(out / "report.md", "# 学术规范与合规审查报告\n\n- status: `insufficient_evidence`\n- 说明: 输入目录为空或无材料，无法审查。\n")
        return 0

    case, case_id, contract, facets, case_errors = load_case(inp)
    sources, extract_errors, degradations = extract.extract_source_index(files, inp, work)
    observations = extract.detect_observations(sources)
    errors = list(case_errors) + list(extract_errors)
    skeleton = build_skeleton(case_id, contract, facets, files, sources, errors, degradations)
    atomic_write_json(out / "result.json", skeleton)
    write_report_skeleton(out, case_id, "partial", facets)
    write_evidence(work, sources, observations, facets, contract)
    write_plan(work, case_id, facets)
    print(json.dumps({
        "case_id": case_id,
        "status": skeleton["status"],
        "files": len(files),
        "sources_extracted": sum(1 for s in sources if s["extracted"]),
        "observations": len(observations),
        "facets": facets,
        "result": (out / "result.json").as_posix(),
    }, ensure_ascii=False, indent=2))
    return 0


def _normalize_finding(f, idx):
    """Validate and normalize one finding. Return (finding, error)."""
    err = None
    missing = [k for k in FINDING_REQUIRED if k not in f]
    if missing:
        return None, f"finding #{idx} missing keys: {missing}"
    ftype = f.get("type")
    if ftype not in FINDING_TYPES:
        return None, f"finding #{idx} bad type: {ftype!r}"
    sev = f.get("severity")
    if sev not in SEVERITIES:
        return None, f"finding #{idx} bad severity: {sev!r}"
    status = f.get("status", "confirmed")
    if status not in FINDING_STATUSES:
        return None, f"finding #{idx} bad status: {status!r}"
    category = str(f.get("category", "")).strip()
    if not category:
        return None, f"finding #{idx} empty category"
    out_f = dict(f)
    out_f["status"] = status
    for key in FINDING_REQUIRED:
        if not isinstance(out_f.get(key), str):
            out_f[key] = "" if key != "status" else out_f[key]
    return out_f, None


def verify_quote_against_evidence(finding, evidence):
    """Soft check of quote against evidence.json full text. Returns (ok, detail)."""
    quote = extract.normalize(str(finding.get("quote", "")))
    if not quote:
        return None, "empty quote"
    fpath = finding.get("file")
    for src in evidence.get("sources", []):
        if src.get("path") != fpath:
            continue
        if src.get("full_norm") and quote in src["full_norm"]:
            return True, "matched full text"
        for para in src.get("paragraphs", []):
            if para.get("norm") and quote in para["norm"]:
                return True, f"matched {para.get('n')}"
        return False, "quote not found in source text"
    return None, "file not in evidence"


def sync_contract_keys(result):
    """Fill top-level contract placeholders with real values after merge.

    build_skeleton flattens output_contract leaf keys to top level as null/[]
    placeholders. After findings are merged, map the known leaf keys back to the
    corresponding core values so graders reading top-level `findings`/`summary`
    see real content rather than an empty placeholder.
    """
    contract = result.get("output_contract")
    if not isinstance(contract, dict) or not contract:
        return
    report = result.get("report") if isinstance(result.get("report"), dict) else {}
    leaves = extract.flatten_contract(contract)
    alias = {"issues": "findings", "results": "findings", "problems": "findings"}
    for key in leaves:
        if key in CORE_KEYS:
            continue
        current = result.get(key)
        if current is None or current == [] or current == "":
            if key in alias and isinstance(report.get(alias[key]), list):
                result[key] = report[alias[key]]
            elif key in report:
                result[key] = report[key]


def merge(inp: Path, out: Path, work: Path, findings_path: Path) -> int:
    result_path = out / "result.json"
    result, err = extract.read_json_robust(result_path)
    if err:
        # Recover by running a fresh skeleton.
        run(inp, out, work)
        result, err = extract.read_json_robust(result_path)
        if err:
            print(f"FATAL: cannot read or rebuild result.json: {err}", file=sys.stderr)
            return 1
    findings_obj, err = extract.read_json_robust(findings_path)
    if err:
        findings_obj = None
        print(f"WARNING: findings unreadable ({err}); appending empty merge.", file=sys.stderr)
    if findings_obj is None:
        findings_obj = {}
    raw_findings = findings_obj.get("findings") if isinstance(findings_obj, dict) else findings_obj
    if not isinstance(raw_findings, list):
        raw_findings = []

    evidence, _ = extract.read_json_robust(work / "evidence.json")
    if evidence is None:
        evidence = {}

    findings = []
    merge_errors = []
    for idx, f in enumerate(raw_findings, start=1):
        norm, ferr = _normalize_finding(f, idx)
        if ferr:
            merge_errors.append(ferr)
            continue
        ok, detail = verify_quote_against_evidence(norm, evidence)
        norm["quote_verified"] = ok
        norm["quote_check"] = detail or ""
        findings.append(norm)

    findings.sort(key=lambda f: (
        SEVERITY_RANK.get(f["severity"], 9),
        f.get("category", ""),
        str(f.get("id", "")),
    ))

    report = result.get("report") if isinstance(result.get("report"), dict) else {}
    report["findings"] = findings
    # Apply optional top-level overrides from findings_obj.
    for key in ("summary",):
        if isinstance(findings_obj.get(key), str) and findings_obj[key]:
            report[key] = findings_obj[key]
    if isinstance(findings_obj.get("uncertainty"), list):
        result["uncertainty"] = findings_obj["uncertainty"]
    if isinstance(findings_obj.get("pending_human"), list):
        result["pending_human"] = findings_obj["pending_human"]
    if isinstance(findings_obj.get("checked_items"), list):
        report["checked_items"] = findings_obj["checked_items"]
    if isinstance(findings_obj.get("not_checked"), list):
        report["not_checked"] = findings_obj["not_checked"]
    if isinstance(findings_obj.get("pending_review"), list):
        report["pending_review"] = findings_obj["pending_review"]
    if isinstance(findings_obj.get("status"), str) and findings_obj["status"] in ALLOWED_STATUS:
        result["status"] = findings_obj["status"]

    result["report"] = report
    # Let the agent fill arbitrary contract leaf keys directly, excluding the
    # reserved meta keys already handled above and the protected core keys.
    meta = {"findings", "summary", "status", "uncertainty", "pending_human",
            "checked_items", "not_checked", "pending_review", "change_note"}
    for key, value in findings_obj.items():
        if isinstance(findings_obj, dict) and key not in meta and key not in CORE_KEYS:
            result[key] = value
    sync_contract_keys(result)
    result["errors"] = list(result.get("errors") or []) + merge_errors
    change_note = findings_obj.get("change_note") if isinstance(findings_obj, dict) else ""
    event = f"merge: {len(findings)} findings applied" + (f" ({change_note})" if change_note else "")
    log = result.get("change_log") if isinstance(result.get("change_log"), list) else []
    log.append({"time": utcnow(), "event": event, "findings_count": len(findings)})
    result["change_log"] = log
    runtime = result.get("runtime") if isinstance(result.get("runtime"), dict) else {}
    steps = runtime.get("steps") if isinstance(runtime.get("steps"), list) else []
    steps.append({"step": "merge", "at": utcnow(), "findings": len(findings)})
    runtime["steps"] = steps
    runtime["report_path"] = "/app/output/report.md"
    result["runtime"] = runtime

    atomic_write_json(result_path, result)
    write_report(result, out / "report.md")
    print(json.dumps({
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "findings": len(findings),
        "merge_errors": len(merge_errors),
        "report": (out / "report.md").as_posix(),
    }, ensure_ascii=False, indent=2))
    return 0


def write_report(result, report_path: Path) -> None:
    case_id = result.get("case_id", "unknown")
    status = result.get("status", "partial")
    report = result.get("report") if isinstance(result.get("report"), dict) else {}
    findings = report.get("findings") if isinstance(report.get("findings"), list) else []
    lines = [
        "# 学术规范与合规审查报告",
        "",
        f"- case_id: `{case_id}`",
        f"- status: `{status}`",
        f"- 生成时间: {utcnow()}",
        f"- 结果文件: `result.json`",
        "",
        "## 结论摘要",
        "",
        report.get("summary") or "（无）",
        "",
        "## 问题清单",
        "",
    ]
    if not findings:
        lines.append("（本材料未确认任何问题，或审查尚未完成。）")
    else:
        sev_labels = {"critical": "严重", "major": "主要", "minor": "次要", "info": "提示"}
        for f in findings:
            status_note = "" if f.get("status") == "confirmed" else f" [状态: {f.get('status')}]"
            lines += [
                f"### [{sev_labels.get(f.get('severity'), f.get('severity'))}] {f.get('title')}{status_note}",
                "",
                f"- id: `{f.get('id')}`",
                f"- type: `{f.get('type')}` / category: `{f.get('category')}`",
                f"- 位置: `{f.get('file')}` @ `{f.get('locator')}`",
                f"- 证据: {f.get('evidence') or '（无）'}",
                f"- 依据: {f.get('basis') or '（无，需人工补充条款/要求）'}",
                f"- 修改建议: {f.get('revision') or '（无）'}",
                "",
                "> " + (f.get("quote") or "（无逐字证据）").replace("\n", " "),
                "",
            ]
    lines += [
        "## 审查维度覆盖",
        "",
    ]
    scope = report.get("scope") or []
    for s in scope:
        lines.append(f"- {s}")
    checked = report.get("checked_items")
    if isinstance(checked, list) and checked:
        lines.append("")
        for c in checked:
            lines.append(f"- {c.get('facet')}: `{c.get('result')}` {c.get('note') or ''}".rstrip())
    pending = report.get("pending_review") or []
    if pending:
        lines += ["", "## 待复核", ""]
        for p in pending:
            lines.append(f"- {p}")
    not_checked = report.get("not_checked") or []
    if not_checked:
        lines += ["", "## 未检查", ""]
        for n in not_checked:
            lines.append(f"- {n}")
    lines += [
        "",
        "## 依据与局限",
        "",
    ]
    uncertainty = result.get("uncertainty") or []
    for u in uncertainty:
        lines.append(f"- 不确定性: {u}")
    errors = result.get("errors") or []
    for e in errors:
        lines.append(f"- 错误/缺口: {e}")
    degradations = result.get("degradations") or []
    for d in degradations:
        if isinstance(d, dict):
            lines.append(f"- 降级: {d.get('path')} -> {d.get('issue')}")
        else:
            lines.append(f"- 降级: {d}")
    lines.append("")
    atomic_write_text(report_path, "\n".join(lines))


def main(argv=None) -> int:
    # Common options are declared on both the main parser and each subparser so
    # that `main.py merge --input ...` and `main.py --input ... merge` both work.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    common.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    common.add_argument("--work", type=Path, default=DEFAULT_WORK)

    # Subparser-level copies must not clobber values already set on the main
    # parser when the option is omitted after the subcommand.
    common_sub = argparse.ArgumentParser(add_help=False)
    common_sub.add_argument("--input", type=Path, default=argparse.SUPPRESS)
    common_sub.add_argument("--output", type=Path, default=argparse.SUPPRESS)
    common_sub.add_argument("--work", type=Path, default=argparse.SUPPRESS)

    parser = argparse.ArgumentParser(parents=[common], description="Academic integrity audit orchestrator.")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", parents=[common_sub], help="discover, extract, and write partial skeleton")
    merge_p = sub.add_parser("merge", parents=[common_sub], help="merge findings into result.json")
    merge_p.add_argument("--findings", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.command == "merge":
        findings_path = args.findings or (args.work / "findings.json")
        return merge(args.input, args.output, args.work, findings_path)
    return run(args.input, args.output, args.work)


if __name__ == "__main__":
    raise SystemExit(main())
