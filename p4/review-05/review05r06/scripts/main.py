#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Orchestrator for the source-bound compliance audit skill.

Subcommands
-----------
run     discover inputs, build the evidence ledger, write a valid *partial*
        result.json plus report.md and plan.json. Designed to run within the
        first minutes so any later interruption still leaves a parseable,
        honest deliverable behind.
merge   merge an agent-written adjudication file (work/findings.json) into the
        existing result.json and regenerate report.md. Runs after each
        incremental edit.
report  regenerate report.md from the current result.json.

Standard library only. Imports extract.py from the same directory.
"""
from __future__ import annotations

import argparse
import json
import re
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
KNOWN_CATEGORIES = {rule["facet"] for rule in extract.FACET_RULES}
CORE_KEYS = {
    "case_id", "status", "report", "sources", "uncertainty", "change_log",
    "errors", "degradations", "pending_human", "runtime", "output_contract",
    "contract_missing",
}
FINDING_REQUIRED = {
    "id", "type", "severity", "category", "title", "file", "locator", "quote",
    "evidence", "basis", "revision",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_case(inp: Path):
    """Return (case_obj, case_id, contract, errors, files)."""
    files = extract.discover(inp)
    errors = []
    case_item = next((it for it in files if it["role"] == "case"), None)
    if case_item is None:
        return None, "unknown", {}, ["case.json not found"], files
    case, err = extract.read_json_robust(case_item["path"])
    if err:
        return None, "unknown", {}, [f"case.json unreadable: {err}"], files
    case_id = None
    for key in ("case_id", "id", "challenge_id", "task_id"):
        val = case.get(key)
        if isinstance(val, str) and val:
            case_id = val
            break
    if not case_id:
        case_id = case_item["path"].stem
    contract = case.get("output_contract")
    contract = contract if isinstance(contract, dict) else {}
    return case, case_id, contract, errors, files


def detect_facets(case):
    """Case-declared review facets (strings), if any."""
    if not isinstance(case, dict):
        return []
    for key in ("review_facets", "facets", "check_facets", "checklist", "review_scope"):
        val = case.get(key)
        if isinstance(val, list):
            names = [str(x) for x in val if isinstance(x, str) and str(x).strip()]
            if names:
                return names
    return []


def default_for(value):
    """Mirror an output_contract structure with null/[] leaf defaults."""
    if isinstance(value, dict):
        return {k: default_for(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    return None


def flatten_leaf(obj, prefix=""):
    """Flatten leaf keys to dotted top-level names."""
    out = {}
    if isinstance(obj, dict):
        for key, val in obj.items():
            full = f"{prefix}.{key}" if prefix else key
            out.update(flatten_leaf(val, full))
    else:
        out[prefix] = obj
    return out


def build_plan(case_id, declared_facets, evidence):
    facets = []
    for rule in extract.FACET_RULES:
        facet_evidence = next(
            (f for f in evidence["facets"] if f["facet"] == rule["facet"]), None
        )
        facets.append({
            "facet": rule["facet"],
            "label": rule["label"],
            "status": "unchecked",
            "triggers": [key for key, _ in rule["patterns"]],
            "observation_count": len(facet_evidence["observations"]) if facet_evidence else 0,
        })
    return {
        "case_id": case_id,
        "declared_facets": list(declared_facets),
        "facets": facets,
        "notes": (
            "若 case.json 声明了具体审查面（declared_facets），以其为权威清单并映射到"
            "下方 facet；否则以下方 11 个 facet 作为完整性底线，逐项给出 已查/待复核/未查。"
        ),
    }


def build_skeleton(case_id, contract, files, evidence, errors, degradations, status="partial"):
    skeleton = {
        "case_id": case_id,
        "status": status,
        "report": {
            "summary": "",
            "scope": [rule["label"] for rule in extract.FACET_RULES],
            "findings": [],
            "dismissed_observations": [],
            "checked_items": [],
            "pending_review": [],
            "not_checked": [rule["label"] for rule in extract.FACET_RULES],
        },
        "sources": [
            {
                "path": it["path"].as_posix(),
                "name": it["name"],
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
            "report_path": None,
        },
    }
    if contract:
        skeleton["output_contract"] = default_for(contract)
        for key, value in flatten_leaf(default_for(contract)).items():
            if key not in CORE_KEYS and key not in skeleton:
                skeleton[key] = value
    else:
        skeleton["output_contract"] = {}
        skeleton["contract_missing"] = True
    return skeleton


def write_report_skeleton(out: Path, case_id: str, status: str, plan) -> None:
    lines = [
        "# 学术规范与合规审查报告",
        "",
        f"- case_id: `{case_id}`",
        f"- status: `{status}`",
        f"- 生成时间: {utcnow()}",
        "",
        "## 结论摘要",
        "",
        "（待填写：总体结论、已覆盖检查面、主要风险与建议。此骨架仅供增量更新，不冒充正式结论。）",
        "",
        "## 覆盖检查面（每项必须给 已查/待复核/未查）",
        "",
        "| 检查面 | 状态 | 已确认问题 | 主要依据 |",
        "|---|---|---|---|",
    ]
    for facet in plan["facets"]:
        lines.append(f"| {facet['label']}（{facet['facet']}） | 未查 | 0 | — |")
    lines += [
        "",
        "## 问题清单",
        "",
        "（每条问题按：编号 | 类型 | 严重度 | 位置 | 逐字引文 | 判定依据 | 修改建议 填写。）",
        "",
        "## 已排除的观察（误报抑制留档）",
        "",
        "（逐条列出被判定为不成立的候选观察及排除理由。）",
        "",
        "## 待复核 / 未检查项",
        "",
        "（列出证据不足、材料缺失或未覆盖的检查项。）",
        "",
        "## 依据与局限",
        "",
        "（列出所用规范条款、期刊要求、授权范围及其版本/日期，并说明无法判断之处。）",
        "",
    ]
    extract.atomic_write_text(out / "report.md", "\n".join(lines) + "\n")


def verify_quote(quote, file_ref, paragraphs, parsed_names):
    """Match a quote against the evidence ledger. Returns (status, locator)."""
    q = extract.normalize(quote or "")
    if not q:
        return "empty_quote", None
    candidates = paragraphs
    if file_ref:
        base = Path(file_ref).name
        candidates = [p for p in paragraphs if Path(p["file"]).name == base or p["file"] == file_ref]
        if not candidates and base not in parsed_names:
            return "file_not_indexed_or_not_parsed", None
    for p in candidates:
        if q in extract.normalize(p["text"]):
            return "exact", p["locator"]
    qn = re.sub(r"\s+", "", q)
    for p in candidates:
        if qn and qn in re.sub(r"\s+", "", p["text"]):
            return "whitespace_insensitive", p["locator"]
    ql = qn.lower()
    for p in candidates:
        if ql and ql in re.sub(r"\s+", "", p["text"]).lower():
            return "case_insensitive", p["locator"]
    words = [w for w in re.findall(r"\w+", q.lower()) if len(w) > 2]
    if words:
        for p in candidates:
            pwords = set(re.findall(r"\w+", p["text"].lower()))
            hit = sum(1 for w in words if w in pwords) / len(words)
            if hit >= 0.8:
                return "token_overlap", p["locator"]
    return "not_found", None


def normalize_finding(item, index):
    finding = {k: item.get(k) if item.get(k) is not None else "" for k in FINDING_REQUIRED}
    finding["id"] = finding["id"] or f"F{index + 1:04d}"
    for extra in ("verdict", "evidence_status", "quote_locator", "confidence", "source_conflict"):
        if extra in item:
            finding[extra] = item[extra]
    return finding


def write_report(out: Path, result) -> str:
    report = result.get("report", {})
    lines = [
        "# 学术规范与合规审查报告",
        "",
        f"- case_id: `{result.get('case_id', '')}`",
        f"- status: `{result.get('status', '')}`",
        f"- 生成时间: {utcnow()}",
        "",
        "## 结论摘要",
        "",
        report.get("summary") or "（未填写总体结论）",
        "",
        "## 问题清单",
        "",
    ]
    findings = report.get("findings", [])
    if not findings:
        lines.append("（未发现成立的问题，或尚未完成判定。）")
        lines.append("")
    for finding in findings:
        lines += [
            f"### {finding.get('id', '')} · {finding.get('severity', '')} · "
            f"{finding.get('type', '')} · {finding.get('category', '')}",
            "",
            f"- 标题：{finding.get('title', '')}",
            f"- 位置：`{finding.get('file', '')}` @ `{finding.get('locator', '')}`",
            f"- 原文：> {finding.get('quote', '')}",
            f"- 证据：{finding.get('evidence', '')}",
            f"- 依据：{finding.get('basis', '')}",
            f"- 建议：{finding.get('revision', '')}",
            f"- 证据核验：{finding.get('evidence_status', '')}",
            "",
        ]
    lines += ["## 已排除的观察（误报抑制留档）", ""]
    dismissed = report.get("dismissed_observations", [])
    if not dismissed:
        lines.append("（无。）")
    for item in dismissed:
        lines.append(f"- {item.get('id', '')} → {item.get('reason', '')}")
    lines += ["", "## 待复核 / 未检查项", ""]
    for item in report.get("pending_review", []):
        lines.append(f"- 待复核：{item}")
    for item in report.get("not_checked", []):
        lines.append(f"- 未查：{item}")
    lines += ["", "## 依据与局限", ""]
    lines += ["", "## 不确定性与待人工确认", ""]
    for item in result.get("uncertainty", []):
        lines.append(f"- {item}")
    for item in result.get("pending_human", []):
        lines.append(f"- 待人工：{item}")
    text = "\n".join(lines) + "\n"
    extract.atomic_write_text(out / "report.md", text)
    return text


def cmd_run(args):
    inp = Path(args.input)
    out = Path(args.output)
    work = Path(args.work)
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    case, case_id, contract, errors, files = load_case(inp)
    evidence = extract.build_evidence(inp, work)
    extract.atomic_write_json(work / "evidence.json", evidence)
    declared = detect_facets(case)
    plan = build_plan(case_id, declared, evidence)
    extract.atomic_write_json(work / "plan.json", plan)
    extract.atomic_write_json(work / "findings.json", {})
    if case is None:
        status = "input_error"
    elif not evidence["paragraphs"]:
        status = "insufficient_evidence"
    else:
        status = "partial"
    skeleton = build_skeleton(case_id, contract, files, evidence, errors,
                              evidence["degradations"], status=status)
    skeleton["runtime"]["report_path"] = (out / "report.md").as_posix()
    extract.atomic_write_json(out / "result.json", skeleton)
    write_report_skeleton(out, case_id, skeleton["status"], plan)
    print(json.dumps({
        "case_id": case_id,
        "status": skeleton["status"],
        "files": len(files),
        "paragraphs": len(evidence["paragraphs"]),
        "facets": len(evidence["facets"]),
        "result": (out / "result.json").as_posix(),
        "report": (out / "report.md").as_posix(),
    }, ensure_ascii=False))
    return 0


def cmd_merge(args):
    inp = Path(args.input)
    out = Path(args.output)
    work = Path(args.work)
    findings_path = Path(args.findings)
    res_path = out / "result.json"
    if res_path.exists():
        result, err = extract.read_json_robust(res_path)
        if err or not isinstance(result, dict):
            case, case_id, contract, errors, files = load_case(inp)
            evidence = extract.build_evidence(inp, work)
            result = build_skeleton(case_id, contract, files, evidence, errors, evidence["degradations"])
    else:
        case, case_id, contract, errors, files = load_case(inp)
        evidence = extract.build_evidence(inp, work)
        result = build_skeleton(case_id, contract, files, evidence, errors, evidence["degradations"])

    raw, ferr = extract.read_json_robust(findings_path)
    payload = raw if isinstance(raw, dict) else {}
    if ferr:
        result.setdefault("errors", []).append(f"findings.json unreadable: {ferr}")
    if isinstance(raw, list):
        payload = {"findings": raw}

    evidence = None
    evidence_path = work / "evidence.json"
    if evidence_path.exists():
        evidence, _ = extract.read_json_robust(evidence_path)
    paragraphs = evidence["paragraphs"] if evidence else []
    parsed_names = {Path(f["path"]).name for f in evidence["files"] if f["parsed"]} if evidence else set()

    report = result.setdefault("report", {})
    accepted = []
    dismissed = []
    pending = []

    for index, item in enumerate(payload.get("findings", []) or []):
        if not isinstance(item, dict):
            continue
        verdict = item.get("verdict")
        if verdict == "dismissed":
            dismissed.append({
                "id": item.get("id") or f"D{len(dismissed) + 1:04d}",
                "observation_id": item.get("observation_id", ""),
                "reason": item.get("reason", ""),
            })
            continue
        if verdict == "pending":
            pending.append(item.get("reason") or item.get("id") or f"P{len(pending) + 1:04d}")
            continue
        finding = normalize_finding(item, len(accepted))
        finding["verdict"] = "finding"
        finding["evidence_status"], finding["quote_locator"] = verify_quote(
            finding.get("quote", ""), finding.get("file", ""), paragraphs, parsed_names
        )
        accepted.append(finding)

    for index, item in enumerate(payload.get("dismissed", []) or []):
        if isinstance(item, dict):
            dismissed.append({
                "id": item.get("id") or f"D{len(dismissed) + 1:04d}",
                "observation_id": item.get("observation_id", ""),
                "reason": item.get("reason", ""),
            })
    for index, item in enumerate(payload.get("pending", []) or []):
        if isinstance(item, dict):
            pending.append(item.get("reason") or item.get("id") or f"P{len(pending) + 1:04d}")

    report["findings"] = accepted
    report["dismissed_observations"] = dismissed
    report["pending_review"] = pending

    if payload.get("summary") is not None:
        report["summary"] = str(payload.get("summary", ""))
    for key in ("checked_items", "not_checked"):
        val = payload.get(key)
        if isinstance(val, list):
            report[key] = [str(x) for x in val]
    for key in ("pending_human", "uncertainty"):
        val = payload.get(key)
        if isinstance(val, list):
            result[key] = [str(x) for x in val]

    requested = payload.get("status")
    if requested in ALLOWED_STATUS:
        result["status"] = requested
    else:
        result["status"] = "ok" if (report.get("summary") and accepted) else "partial"

    result.setdefault("change_log", []).append({
        "time": utcnow(),
        "event": f"merge: {len(accepted)} findings, "
                 f"{len(report.get('dismissed_observations', []))} dismissed, "
                 f"{len(report.get('pending_review', []))} pending",
    })
    result["runtime"] = result.get("runtime", {})
    result["runtime"]["report_path"] = (out / "report.md").as_posix()
    result["runtime"].setdefault("steps", []).append({"step": "merge", "at": utcnow()})

    extract.atomic_write_json(res_path, result)
    write_report(out, result)
    print(json.dumps({
        "status": result["status"],
        "findings": len(accepted),
        "dismissed": len(report.get("dismissed_observations", [])),
        "pending": len(report.get("pending_review", [])),
    }, ensure_ascii=False))
    return 0


def cmd_report(args):
    out = Path(args.output)
    res_path = out / "result.json"
    if not res_path.exists():
        print("result.json not found", file=sys.stderr)
        return 1
    result, err = extract.read_json_robust(res_path)
    if err or not isinstance(result, dict):
        print(f"result.json unreadable: {err}", file=sys.stderr)
        return 1
    write_report(out, result)
    print((out / "report.md").as_posix())
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    p_run = sub.add_parser("run", help="skeleton-first initial run")
    p_run.add_argument("--input", default=str(DEFAULT_INPUT))
    p_run.add_argument("--output", default=str(DEFAULT_OUTPUT))
    p_run.add_argument("--work", default=str(DEFAULT_WORK))
    p_merge = sub.add_parser("merge", help="merge adjudication file incrementally")
    p_merge.add_argument("--input", default=str(DEFAULT_INPUT))
    p_merge.add_argument("--output", default=str(DEFAULT_OUTPUT))
    p_merge.add_argument("--work", default=str(DEFAULT_WORK))
    p_merge.add_argument("--findings", required=True)
    p_rep = sub.add_parser("report", help="regenerate report.md")
    p_rep.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    if args.command == "run":
        return cmd_run(args)
    if args.command == "merge":
        return cmd_merge(args)
    if args.command == "report":
        return cmd_report(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
