#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Orchestrator for the source-bound academic audit skill.

Subcommands
-----------
run      discover inputs, extract evidence, write a valid *partial*
         result.json plus report.md / plan.md / evidence files. Safe to call
         in the first minutes; an interruption still leaves a parseable
         deliverable.
merge    merge an agent-written findings file into the existing result.json,
         assign stable IDs, verify every quote against the parsed source,
         deduplicate, recompute status, regenerate report.md, append change_log.
         Idempotent for the same findings file.
validate structural + evidence checks on result.json. Exit 0 on pass, 1 on FAIL.

Standard library only. Imports extract.py from the same directory.
"""
from __future__ import annotations

import argparse
import json
import os
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
CATEGORIES = {
    "quality", "citation", "fact", "method", "statistics", "logic", "norm",
    "academic_integrity", "privacy", "copyright", "ethics", "submission",
}
SCOPE = [
    "academic_integrity", "citation", "privacy", "copyright",
    "ethics", "submission", "quality", "statistics", "method", "logic",
]
CORE_KEYS = {
    "case_id", "status", "report", "sources", "uncertainty", "change_log",
    "errors", "degradations", "pending_human", "runtime", "output_contract",
}
FINDING_REQUIRED = {
    "id", "type", "severity", "category", "title", "file", "locator",
    "quote", "evidence", "basis", "revision",
}
CHECK_ITEM_STATUS = {"checked", "pending", "unchecked"}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_write_json(path: Path, obj) -> None:
    extract.atomic_write_json(path, obj)


def atomic_write_text(path: Path, text: str) -> None:
    extract.atomic_write_text(path, text)


# ---------------------------------------------------------------------------
# Skeleton / report rendering
# ---------------------------------------------------------------------------

def initial_report():
    return {
        "summary": "",
        "scope": list(SCOPE),
        "findings": [],
        "checked_items": [
            {"item": s, "status": "unchecked", "note": ""} for s in SCOPE
        ],
        "pending_review": [],
        "not_checked": [],
    }


def build_skeleton(case_id, contract, files, evidence, status="partial"):
    errors = list(evidence.get("errors", []))
    degradations = list(evidence.get("degradations", []))
    sources = list(evidence.get("sources", []))
    # register non-material artifacts too
    for it in files:
        if it["role"] not in ("material",):
            sources.append({
                "path": it["relpath"],
                "role": it["role"],
                "size": it["size"],
            })
    skeleton = {
        "case_id": case_id,
        "status": status,
        "report": initial_report(),
        "sources": sources,
        "uncertainty": [
            {
                "item": "initial",
                "note": "骨架阶段仅完成解析与候选提取，尚未完成逐条判定；"
                        "后续 merge 会以增量方式收紧结论。",
            }
        ],
        "change_log": [{"time": utcnow(), "event": "partial skeleton written"}],
        "errors": errors,
        "degradations": degradations,
        "pending_human": [],
        "runtime": {
            "started_at": utcnow(),
            "budget": {"model_requests": 64, "seconds": 900},
            "steps": [{"step": "skeleton", "at": utcnow()}],
        },
    }
    values = {
        "case_id": case_id,
        "status": status,
        "report": skeleton["report"],
        "sources": skeleton["sources"],
        "uncertainty": skeleton["uncertainty"],
        "change_log": skeleton["change_log"],
        "errors": skeleton["errors"],
        "degradations": skeleton["degradations"],
        "pending_human": skeleton["pending_human"],
        "runtime": skeleton["runtime"],
    }
    if contract:
        skeleton["output_contract"] = extract.fill_contract(contract, values)
        extract.flatten_leaves(skeleton["output_contract"], skeleton, CORE_KEYS)
    else:
        skeleton["output_contract"] = {}
        skeleton["contract_missing"] = True
    return skeleton


def render_report(result) -> str:
    rep = result.get("report", {})
    lines = [
        "# 学术规范与合规审查报告",
        "",
        f"- case_id: `{result.get('case_id', 'unknown')}`",
        f"- status: `{result.get('status', 'unknown')}`",
        f"- 生成时间: {utcnow()}",
        "",
        "## 结论摘要",
        "",
        rep.get("summary") or "（待填写：总体结论、覆盖检查项、主要风险与建议。）",
        "",
        "## 问题清单",
        "",
    ]
    findings = rep.get("findings", [])
    if not findings:
        lines.append("（暂无已判定问题；未完成判定的候选见 evidence.json。）")
    for f in findings:
        lines.append(
            f"- `{f.get('id')}` | {f.get('type')} | {f.get('severity')} | "
            f"{f.get('category')} | {f.get('title')}"
        )
        lines.append(
            f"  - 位置: `{f.get('file')}` `{f.get('locator')}`"
        )
        lines.append(f"  - 证据: {f.get('quote')}")
        lines.append(f"  - 依据: {f.get('basis')}")
        lines.append(f"  - 建议: {f.get('revision')}")
        lines.append(f"  - 说明: {f.get('evidence')}")
        lines.append("")
    lines += [
        "## 检查项状态",
        "",
        "| 方向 | 状态 | 备注 |",
        "|---|---|---|",
    ]
    for ci in rep.get("checked_items", []):
        lines.append(f"| {ci.get('item')} | {ci.get('status')} | {ci.get('note')} |")
    lines += [
        "",
        "## 待复核 / 未检查项",
        "",
    ]
    for item in rep.get("pending_review", []):
        lines.append(f"- {item}")
    for item in rep.get("not_checked", []):
        lines.append(f"- {item}")
    if not rep.get("pending_review") and not rep.get("not_checked"):
        lines.append("（暂无。）")
    lines += [
        "",
        "## 依据与局限",
        "",
        f"- 解析降级: {len(result.get('degradations', []))} 条",
        f"- 错误: {len(result.get('errors', []))} 条",
        f"- 待人工复核: {len(result.get('pending_human', []))} 条",
        "",
        "（列出所用规范条款、期刊要求、授权范围及其版本/日期；说明无法判断之处。）",
    ]
    return "\n".join(lines) + "\n"


def write_plan(work: Path, case_id: str, evidence) -> None:
    kinds = evidence.get("observation_kinds", {})
    kind_list = ", ".join(f"{k}={v}" for k, v in sorted(kinds.items()))
    text = (
        "# 执行计划（供审查 Agent 使用）\n\n"
        f"- case_id: `{case_id}`\n"
        f"- 已解析材料: {len(evidence.get('sources', []))} 个\n"
        f"- 候选观察: {len(evidence.get('observations', []))} 条；"
        f"分布: {kind_list or '无'}\n"
        f"- 解析降级: {len(evidence.get('degradations', []))} 条\n\n"
        "## 预算（总 900 秒 / 64 次模型请求）\n\n"
        "1. 0–3 分钟：确认契约 + 骨架落盘（已完成）。\n"
        "2. 3–20 分钟：逐条判定 evidence.json 候选 + 主动补查六个方向，"
        "每条写成 findings.json 后 merge 一次。\n"
        "3. 最后 2–3 分钟：validate 自检并修 FAIL，预算不足则如实 partial。\n\n"
        "## 判定口径\n\n"
        "候选观察不是结论：逐条确认是否成立、是否误报，四要素齐全才写入。"
        "风格差异、学科惯例、个人表述不算违规；无法确认写 pending_human，"
        "冲突信息同时记录并标 source_conflict。\n"
    )
    atomic_write_text(work / "plan.md", text)


def run_command(args) -> int:
    inp = Path(args.input)
    out = Path(args.output)
    work = Path(args.work)
    out.mkdir(parents=True, exist_ok=True)
    evidence = extract.run_extract(inp, work)
    files = evidence.get("files") or []
    if not files and work.joinpath("files.json").exists():
        files = extract.read_json_robust(work / "files.json")[0] or []
    case_id = evidence["case_id"]
    contract = evidence.get("contract", {})
    initial = "insufficient_evidence" if not evidence.get("parsed_files") else "partial"
    result = build_skeleton(case_id, contract, files, evidence, status=initial)
    atomic_write_json(out / "result.json", result)
    atomic_write_text(out / "report.md", render_report(result))
    write_plan(work, case_id, evidence)
    print(
        f"run: case_id={case_id} materials={len(evidence.get('sources', []))} "
        f"index={evidence.get('index_count', 0)} "
        f"observations={len(evidence.get('observations', []))} "
        f"degradations={len(evidence.get('degradations', []))}"
    )
    return 0


# ---------------------------------------------------------------------------
# Findings normalization + merge
# ---------------------------------------------------------------------------

def load_evidence(work: Path):
    ev_path = work / "evidence.json"
    if ev_path.exists():
        evidence, err = extract.read_json_robust(ev_path)
        if err is None and isinstance(evidence, dict):
            return evidence, None
    # rebuild deterministically if missing
    try:
        return extract.run_extract(Path("/input"), work), "rebuilt"
    except Exception as exc:  # noqa: BLE001 - deliver honest error
        return None, f"evidence unavailable: {exc}"


def normalize_finding(f, evidence, next_id, used_ids):
    """Validate and fill one finding. Returns (finding, problems, next_id)."""
    problems = []
    if not isinstance(f, dict):
        return None, ["finding is not an object"], next_id
    out = dict(f)
    for key in FINDING_REQUIRED:
        if key not in out or out[key] in (None, ""):
            # id/quote/file may be filled below; others are required by author
            if key not in ("id", "quote"):
                problems.append(f"missing field: {key}")
    tid = out.get("type", "")
    if tid not in FINDING_TYPES:
        problems.append(f"bad type: {tid!r}")
    sev = out.get("severity", "")
    if sev not in SEVERITIES:
        problems.append(f"bad severity: {sev!r}")
    cat = out.get("category", "")
    if cat not in CATEGORIES:
        problems.append(f"bad category: {cat!r}")

    # stable id assignment
    raw_id = str(out.get("id") or "").strip()
    if re.fullmatch(r"F\d{3}", raw_id) and raw_id not in used_ids:
        out["id"] = raw_id
    else:
        while f"F{next_id:03d}" in used_ids:
            next_id += 1
        out["id"] = f"F{next_id:03d}"
        next_id += 1
    used_ids.add(out["id"])

    # quote verification against parsed source
    qfile = out.get("file", "")
    quote = out.get("quote", "")
    norm_index = (evidence or {}).get("norm_index", {})
    parsed_files = set((evidence or {}).get("parsed_files", []))
    if qfile not in parsed_files:
        problems.append(
            f"file {qfile!r} not parsed (quote unverifiable) -> pending_human"
        )
        out.setdefault("_unverified", True)
    elif not quote:
        problems.append("quote empty; locator required without quote")
        out.setdefault("_unverified", True)
    else:
        blob_info = norm_index.get(qfile)
        nq = extract.normalize(quote)
        if blob_info and nq in blob_info["blob"]:
            loc = extract.find_locator(
                blob_info["blob"], blob_info["offsets"],
                blob_info["locators"], nq,
            )
            out.setdefault("_verified_locator", loc)
        else:
            problems.append(
                f"quote not found verbatim in {qfile!r} -> pending_human"
            )
            out.setdefault("_unverified", True)
    return out, problems, next_id


def dedupe_key(f):
    return (
        f.get("type"), f.get("category"), f.get("file"), f.get("locator"),
        extract.normalize(f.get("quote", "")),
    )


def merge_findings(result, payload, evidence):
    """Return (result, summary). Mutates a copy of *result*.

    *payload* may be a list of findings or a dict carrying ``findings`` plus
    optional ``checked_items`` / ``summary`` / ``pending_review`` /
    ``not_checked`` / ``pending_human`` blocks.
    """
    result = extract.deepcopy_obj(result)
    if isinstance(payload, dict):
        findings = payload.get("findings", payload.get("items", []))
        if not isinstance(findings, list):
            findings = []
    else:
        findings = payload
    seen = set()
    merged = []
    problems = []
    used_ids = set()
    next_id = 1
    for f in findings:
        norm, probs, next_id = normalize_finding(f, evidence, next_id, used_ids)
        if norm is None:
            problems.append({"item": f, "problems": probs})
            continue
        problems.extend({"id": norm.get("id"), "problem": p} for p in probs)
        key = dedupe_key(norm)
        if key in seen:
            problems.append({"id": norm.get("id"), "problem": "duplicate skipped"})
            continue
        seen.add(key)
        norm.pop("_verified_locator", None)
        norm.pop("_unverified", None)
        merged.append(norm)

    # preserve author ordering but sort deterministically for reproducibility
    merged.sort(key=lambda f: (
        str(f.get("file")), str(f.get("locator")),
        f.get("type"), f.get("category"), str(f.get("id")),
    ))

    report = result.get("report", {})
    if not isinstance(report, dict):
        report = initial_report()
    report["findings"] = merged

    # recompute pending_human deterministically: explicit payload entries plus
    # structural problems from this merge. Recomputation (not accumulation)
    # keeps merge idempotent across repeated runs.
    explicit_pending = []
    if isinstance(payload, dict) and isinstance(payload.get("pending_human"), list):
        explicit_pending = extract.deepcopy_obj(payload["pending_human"])
    result["pending_human"] = explicit_pending + problems

    # update checked_items from payload when the agent supplied its own
    # checked_items block (optional field on the findings payload).
    if isinstance(payload, dict):
        supplied = payload.get("checked_items")
        if isinstance(supplied, list):
            report["checked_items"] = [
                ci for ci in supplied
                if isinstance(ci, dict)
                and ci.get("item") in SCOPE
                and ci.get("status") in CHECK_ITEM_STATUS
            ]
        for k in ("summary", "pending_review", "not_checked"):
            if k in payload and payload[k] is not None:
                report[k] = extract.deepcopy_obj(payload[k])

    result["report"] = report
    result["status"] = compute_status(result, evidence)
    result["change_log"] = result.get("change_log", [])
    if not isinstance(result["change_log"], list):
        result["change_log"] = []
    result["change_log"].append({
        "time": utcnow(),
        "event": f"merged {len(merged)} findings; {len(problems)} warnings",
    })
    result["runtime"] = result.get("runtime", {})
    result["runtime"]["steps"] = result["runtime"].get("steps", [])
    result["runtime"]["steps"].append({
        "step": "merge", "at": utcnow(), "findings": len(merged),
    })
    return result, {"merged": len(merged), "problems": len(problems)}


def compute_status(result, evidence):
    parsed = bool((evidence or {}).get("parsed_files"))
    findings = result.get("report", {}).get("findings", [])
    if not parsed and not findings:
        return "insufficient_evidence"
    checked = result.get("report", {}).get("checked_items", [])
    all_checked = (
        {ci.get("item") for ci in checked if ci.get("status") == "checked"}
        >= set(SCOPE)
    )
    has_blocker = any(
        f.get("severity") in ("critical", "major") for f in findings
    )
    if all_checked and not has_blocker:
        return "ok"
    return "partial"


def merge_command(args) -> int:
    out = Path(args.output)
    work = Path(args.work)
    result_path = out / "result.json"
    result, err = extract.read_json_robust(result_path)
    if err or not isinstance(result, dict):
        print(f"FAIL: cannot read result.json: {err}", file=sys.stderr)
        return 2
    findings, ferr = extract.read_json_robust(Path(args.findings))
    if ferr:
        print(f"FAIL: findings file unreadable: {ferr}", file=sys.stderr)
        return 2
    evidence, _ = load_evidence(work)
    result, summary = merge_findings(result, findings, evidence)
    atomic_write_json(result_path, result)
    atomic_write_text(out / "report.md", render_report(result))
    print(f"merge: {summary}")
    return 0


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_command(args) -> int:
    out = Path(args.output)
    work = Path(args.work)
    errors = []
    warnings = []

    result_path = out / "result.json"
    result, err = extract.read_json_robust(result_path)
    if err or not isinstance(result, dict):
        errors.append(f"result.json unreadable or not an object: {err}")
        _print_validation(errors, warnings)
        return 1

    if not result.get("case_id"):
        errors.append("case_id missing/empty")
    if result.get("status") not in ALLOWED_STATUS:
        errors.append(f"status invalid: {result.get('status')!r}")
    if "output_contract" not in result:
        errors.append("output_contract key missing")
    elif not isinstance(result.get("output_contract"), dict):
        errors.append("output_contract is not an object")
    else:
        # contract keys present = keys of output_contract, unless contract_missing
        if result.get("contract_missing"):
            warnings.append("contract_missing=true (no output_contract in case.json)")
    for key in ("sources", "uncertainty", "change_log", "errors",
                "degradations", "pending_human"):
        val = result.get(key)
        if not isinstance(val, list):
            errors.append(f"{key} missing or not a list")
    if not isinstance(result.get("uncertainty") or [], list) or not result.get("uncertainty"):
        errors.append("uncertainty must be a non-empty list")
    if not isinstance(result.get("change_log") or [], list) or not result.get("change_log"):
        errors.append("change_log must be a non-empty list")
    report = result.get("report")
    if not isinstance(report, dict):
        errors.append("report missing or not an object")
    else:
        findings = report.get("findings")
        if not isinstance(findings, list):
            errors.append("report.findings missing or not a list")
        else:
            for f in findings:
                if not isinstance(f, dict):
                    errors.append("a finding is not an object")
                    continue
                for key in FINDING_REQUIRED:
                    if key not in f:
                        errors.append(f"finding {f.get('id')} missing {key}")
                if f.get("type") not in FINDING_TYPES:
                    errors.append(f"finding {f.get('id')} bad type")
                if f.get("severity") not in SEVERITIES:
                    errors.append(f"finding {f.get('id')} bad severity")
                if f.get("category") not in CATEGORIES:
                    errors.append(f"finding {f.get('id')} bad category")
        checked = report.get("checked_items")
        if not isinstance(checked, list) or not checked:
            errors.append("report.checked_items missing or empty")
    if not (out / "report.md").exists():
        errors.append("report.md missing")

    # quote verification against evidence
    evidence, ev_err = load_evidence(work)
    if ev_err and ev_err != "rebuilt":
        warnings.append(f"evidence unavailable for quote check: {ev_err}")
    elif isinstance(evidence, dict):
        norm_index = evidence.get("norm_index", {})
        parsed_files = set(evidence.get("parsed_files", []))
        for f in result.get("report", {}).get("findings", []):
            qfile = f.get("file")
            quote = f.get("quote", "")
            if qfile not in parsed_files:
                warnings.append(f"finding {f.get('id')}: file not parsed, quote not auto-verified")
                continue
            blob_info = norm_index.get(qfile, {})
            if quote and blob_info:
                if extract.normalize(quote) not in blob_info.get("blob", ""):
                    errors.append(f"finding {f.get('id')}: quote not found in {qfile}")
            elif not quote:
                warnings.append(f"finding {f.get('id')}: empty quote")

    _print_validation(errors, warnings)
    return 1 if errors else 0


def _print_validation(errors, warnings):
    for e in errors:
        print(f"FAIL: {e}")
    for w in warnings:
        print(f"WARN: {w}")
    if errors:
        print(f"validation FAILED: {len(errors)} error(s), {len(warnings)} warning(s)")
    else:
        print(f"validation PASSED ({len(warnings)} warning(s))")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="Source-bound academic audit orchestrator")
    sub = ap.add_subparsers(dest="command")

    def add_common(p):
        p.add_argument("--input", default=str(DEFAULT_INPUT))
        p.add_argument("--output", default=str(DEFAULT_OUTPUT))
        p.add_argument("--work", default=str(DEFAULT_WORK))

    p_run = sub.add_parser("run", help="discover + extract + write skeleton")
    add_common(p_run)

    p_merge = sub.add_parser("merge", help="merge findings into result.json")
    add_common(p_merge)
    p_merge.add_argument("--findings", required=True)

    p_val = sub.add_parser("validate", help="validate result.json")
    add_common(p_val)

    args = ap.parse_args(argv)
    if args.command == "run":
        return run_command(args)
    if args.command == "merge":
        return merge_command(args)
    if args.command == "validate":
        return validate_command(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
