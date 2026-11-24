#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Orchestrator for the source-bound academic audit skill.

Subcommands
-----------
run       discover inputs, build the verbatim evidence index, and write a valid
          *partial* result.json skeleton plus report.md, plan.md and worklog.
          Designed to run within the first minutes so that any interruption
          still leaves a parseable, honest deliverable behind.
merge     upsert an agent-written findings.json into result.json (incremental,
          idempotent), update the checked/not-checked facet ledger and append
          a change_log entry, then regenerate report.md.
finalize  set the final status/summary and regenerate report.md.

Standard library only. The evidence index comes from extract.py in the same
directory, so this must be run from the Skill package root.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import extract

DEFAULT_INPUT = Path("/input")
DEFAULT_OUTPUT = Path("/app/output")
DEFAULT_WORK = Path("/app/work")

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
DEFAULT_TYPES = {"fact_error", "insufficient_evidence", "norm_violation", "suggestion"}
DEFAULT_SEVERITIES = {"critical", "major", "minor", "info"}
DEFAULT_CATEGORIES = {
    "academic_integrity", "citation", "privacy", "copyright", "ethics",
    "submission", "quality", "fact", "method", "statistics", "logic",
}

CORE_KEYS = {
    "case_id", "status", "report", "sources", "uncertainty", "change_log",
    "errors", "degradations", "pending_human", "runtime", "output_contract",
}

FINDING_REQUIRED = {
    "id", "type", "severity", "category", "title", "file", "locator", "quote",
    "evidence", "basis", "basis_kind", "revision",
}
BASIS_KINDS = {"material_quote", "normative_clause", "source_metadata",
               "pending_human", "not_reported"}
_LIST_HINT = re.compile(
    r"(list|items|findings|sources|entries|results|errors|warnings|"
    r"degradations|pending|checks|facets|types|fields|sections|records|"
    r"notes|tags|keywords|authors|references|citations|metrics|steps)$",
    re.IGNORECASE,
)


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


def _list_like(key: str) -> bool:
    return bool(_LIST_HINT.search(key))


def default_leaf(key, value):
    """Fill a missing leaf with null (or [] for list-like keys); keep schema shape."""
    if isinstance(value, dict):
        return {k: default_leaf(k, v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    if _list_like(str(key)):
        return []
    return None


def load_case(inp: Path):
    """Return (case_obj, case_id, contract, meta, errors)."""
    errors = []
    case_obj = None
    contract = {}
    meta = {"allowed_types": set(DEFAULT_TYPES),
            "allowed_severities": set(DEFAULT_SEVERITIES),
            "allowed_categories": set(DEFAULT_CATEGORIES)}
    case_id = "unknown"
    files = extract.discover(inp)
    case_item = next((it for it in files if it["role"] == "case"), None)
    if case_item is None:
        return None, case_id, contract, meta, ["/input/case.json not found"]
    case_obj, err = extract.read_json_robust(Path(case_item["path"]))
    if err:
        return None, case_id, contract, meta, [f"case.json unreadable: {err}"]
    if not isinstance(case_obj, dict):
        return case_obj, case_id, contract, meta, ["case.json is not an object"]

    for key in ("case_id", "id", "challenge_id", "task_id"):
        val = case_obj.get(key)
        if isinstance(val, str) and val:
            case_id = val
            break
    if case_id == "unknown":
        case_id = Path(case_item["path"]).stem

    contract = case_obj.get("output_contract")
    contract = contract if isinstance(contract, dict) else {}

    # Optional per-case "concrete source-bound audit" facet overrides.
    def _set_of(src, names):
        out = set()
        for n in names:
            v = src.get(n)
            if isinstance(v, list):
                out |= {str(x) for x in v if isinstance(x, str) and x}
        return out

    meta["allowed_types"] |= _set_of(case_obj, ("finding_types", "types"))
    meta["allowed_severities"] |= _set_of(case_obj, ("severities",))
    meta["allowed_categories"] |= _set_of(case_obj, ("categories", "facets", "review_facets"))
    meta["review_facets"] = case_obj.get("review_facets") or case_obj.get("facets") or []
    meta["evidence_requirements"] = case_obj.get("evidence_requirements") or {}

    return case_obj, case_id, contract, meta, errors


def flatten_leaves(obj, prefix=""):
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                out.update(flatten_leaves(v, key))
            else:
                out[key] = v
    elif isinstance(obj, list):
        out[prefix] = []
    return out


def build_skeleton(case_id, contract, meta, files, errors, degradations):
    status = "partial"
    if not any(it["role"] == "material" or it["role"] == "pdf" for it in files):
        status = "insufficient_evidence"
        degradations.append("no reviewable material found under /input")

    skeleton = {
        "case_id": case_id,
        "status": status,
        "report": {
            "summary": "",
            "scope": sorted(meta["allowed_categories"]),
            "findings": [],
            "checked_items": [],
            "pending_review": [],
            "not_checked": [],
        },
        "sources": [
            {
                "path": it["path"],
                "rel": it["rel"],
                "role": it["role"],
                "size": it["size"],
                "text_available": bool(it.get("text_available")),
            }
            for it in files
        ],
        "uncertainty": [
            {
                "item": "initial_skeleton",
                "note": "尚未进入逐条审查，各方向不确定性将在审查过程中逐项填写。",
            }
        ],
        "change_log": [{"time": utcnow(), "event": "partial skeleton written"}],
        "errors": list(errors),
        "degradations": list(degradations),
        "pending_human": [],
        "runtime": {
            "started_at": utcnow(),
            "steps": [{"step": "skeleton", "at": utcnow()}],
        },
        "allowed_types": sorted(meta["allowed_types"]),
        "allowed_severities": sorted(meta["allowed_severities"]),
        "allowed_categories": sorted(meta["allowed_categories"]),
    }
    if meta.get("review_facets"):
        skeleton["review_facets"] = meta["review_facets"]
    if meta.get("evidence_requirements"):
        skeleton["evidence_requirements"] = meta["evidence_requirements"]

    if contract:
        defaults = default_leaf("output_contract", contract)
        skeleton["output_contract"] = defaults
        for key, value in flatten_leaves(defaults).items():
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
        f"- 生成时间: {utcnow()}\n\n"
        "## 结论摘要\n\n（待填写：总体结论、已覆盖检查项、主要风险与建议。）\n\n"
        "## 问题清单\n\n"
        "（每条问题按：编号 | 类型 | 严重度 | 位置 | 逐字证据 | 判定依据 | 修改建议 填写。）\n\n"
        "## 待复核 / 未检查项\n\n"
        "（列出证据不足、材料缺失或未覆盖的检查项。）\n\n"
        "## 依据与局限\n\n"
        "（列出所用规范条款、期刊要求、授权范围及其版本/日期，并说明无法判断之处。）\n"
    )
    atomic_write_text(out / "report.md", text)


def write_plan(work: Path, case_id: str) -> None:
    text = (
        "# 执行计划（供审查 Agent 使用）\n\n"
        f"- case_id: `{case_id}`\n"
        "- 预算：900 秒 / 64 次模型请求；先落盘 partial 骨架，之后只做增量改进。\n\n"
        "## 步骤\n"
        "1. 读 /input/case.json 与 /input/task.md，确认 case_id、output_contract、review_facets。\n"
        "2. 运行 `python3 -B scripts/main.py --input /input --output /app/output --work /app/work` 落盘骨架。\n"
        "3. 若有 PDF，用 pdf-text-tool 解析到 /app/work/parsed/<原名>.txt，再运行 `python3 -B scripts/extract.py --root /input --work /app/work` 重建索引。\n"
        "4. 读 /app/work/evidence.json，按六个方向 + 质量/统计/方法/逻辑逐项核对原文。\n"
        "5. 把成立的问题写 /app/work/findings.json（每条含 12 个必需字段），merge 更新 result.json。\n"
        "6. 最后 `python3 -B scripts/validate.py --input /input --output /app/output --work /app/work` 修 FAIL。\n"
    )
    atomic_write_text(work / "plan.md", text)


def render_report(result) -> str:
    case_id = result.get("case_id", "unknown")
    status = result.get("status", "unknown")
    report = result.get("report", {})
    findings = report.get("findings", [])
    lines = [
        "# 学术规范与合规审查报告",
        "",
        f"- case_id: `{case_id}`",
        f"- status: `{status}`",
        f"- 生成时间: {utcnow()}",
        "",
        "## 结论摘要",
        "",
        report.get("summary") or "（待填写）",
        "",
        "## 问题清单",
        "",
    ]
    if not findings:
        lines.append("（无已确认问题，或尚未进入逐条审查。）")
    for f in findings:
        lines.append(
            f"- `{f.get('id')}` [{f.get('severity')}] {f.get('type')} "
            f"({f.get('category')}) — {f.get('title')}"
        )
        lines.append(f"  - 位置: `{f.get('file')}` @ `{f.get('locator')}`")
        lines.append(f"  - 证据: {f.get('quote') or '—'}")
        lines.append(f"  - 依据: {f.get('basis') or '—'}")
        lines.append(f"  - 建议: {f.get('revision') or '—'}")
        lines.append("")
    lines += [
        "## 待复核 / 未检查项",
        "",
    ]
    pending = report.get("pending_review") or []
    not_checked = report.get("not_checked") or []
    if pending:
        for p in pending:
            lines.append(f"- {p}")
    else:
        lines.append("（无。）")
    lines.append("")
    lines.append("## 未检查项")
    lines.append("")
    if not_checked:
        for n in not_checked:
            lines.append(f"- {n}")
    else:
        lines.append("（无。）")
    lines += [
        "",
        "## 依据与局限",
        "",
        "（依据条款、期刊要求、授权范围及其版本/日期；说明无法判断之处。）",
    ]
    return "\n".join(lines)


def write_worklog(work: Path, event: str) -> None:
    log_path = work / "worklog.json"
    entries = []
    if log_path.exists():
        entries, _ = extract.read_json_robust(log_path)
        entries = entries if isinstance(entries, list) else []
    entries.append({"time": utcnow(), "event": event})
    atomic_write_json(log_path, entries)


def read_result(out: Path):
    path = out / "result.json"
    if not path.exists():
        return None, "result.json not found"
    obj, err = extract.read_json_robust(path)
    if err:
        return None, err
    return obj, None


def run_cmd(args):
    inp = Path(args.input)
    out = Path(args.output)
    work = Path(args.work)
    for d in (out, work):
        d.mkdir(parents=True, exist_ok=True)

    case_obj, case_id, contract, meta, load_errors = load_case(inp)
    evidence = extract.build_index(inp, work)
    files = evidence["summary"]["files"]
    atomic_write_json(work / "evidence.json", evidence)

    errors = list(load_errors) + [f"[evidence] {e}" for e in evidence["summary"]["errors"]]
    degradations = list(evidence["summary"]["degradations"])

    skeleton = build_skeleton(case_id, contract, meta, files, errors, degradations)
    skeleton["runtime"]["steps"].append({"step": "evidence_index", "at": utcnow()})
    atomic_write_json(out / "result.json", skeleton)
    write_report_skeleton(out, case_id, skeleton["status"])
    write_plan(work, case_id)
    write_worklog(work, "run: skeleton + evidence index written")
    print(f"result.json written: {out / 'result.json'} (status={skeleton['status']})")
    print(f"report.md written: {out / 'report.md'}")
    return 0


def merge_cmd(args):
    inp = Path(args.input)
    out = Path(args.output)
    work = Path(args.work)

    result, err = read_result(out)
    if err:
        print(f"merge: {err}", file=__import__("sys").stderr)
        return 2
    findings_obj, ferr = extract.read_json_robust(Path(args.findings))
    if ferr:
        print(f"merge: findings unreadable: {ferr}", file=__import__("sys").stderr)
        return 2
    if isinstance(findings_obj, dict):
        findings = findings_obj.get("findings", findings_obj.get("items", []))
    else:
        findings = findings_obj
    if not isinstance(findings, list):
        print("merge: findings.json must be a list or {findings:[...]}", file=__import__("sys").stderr)
        return 2

    existing = result.setdefault("report", {}).get("findings", [])
    by_id = {str(f.get("id")): f for f in existing if isinstance(f, dict) and f.get("id")}
    for f in findings:
        if not isinstance(f, dict) or not f.get("id"):
            continue
        fid = str(f["id"])
        if fid in by_id:
            by_id[fid].update(f)
        else:
            by_id[fid] = dict(f)
    merged = [by_id[k] for k in sorted(by_id)]

    result["report"]["findings"] = merged
    covered = {str(f.get("category")) for f in merged if f.get("category")}
    scope = set(result.get("allowed_categories", DEFAULT_CATEGORIES))
    result["report"]["checked_items"] = sorted(covered)
    result["report"]["not_checked"] = sorted(scope - covered)
    result["report"]["pending_review"] = sorted(
        {str(f.get("category")) for f in merged if f.get("type") == "insufficient_evidence"}
    )

    if args.status and args.status in ALLOWED_STATUS:
        result["status"] = args.status
    result["change_log"] = result.get("change_log", [])
    result["change_log"].append({"time": utcnow(), "event": f"merge: {len(findings)} finding(s) upserted"})

    atomic_write_json(out / "result.json", result)
    atomic_write_text(out / "report.md", render_report(result))
    write_worklog(work, f"merge: upserted {len(findings)} finding(s)")
    print(f"merged {len(findings)} finding(s); total={len(merged)}; report.md regenerated")
    return 0


def finalize_cmd(args):
    out = Path(args.output)
    work = Path(args.work)
    result, err = read_result(out)
    if err:
        print(f"finalize: {err}", file=__import__("sys").stderr)
        return 2
    if args.status and args.status in ALLOWED_STATUS:
        result["status"] = args.status
    if args.summary is not None:
        result.setdefault("report", {})["summary"] = args.summary
    result["change_log"] = result.get("change_log", [])
    result["change_log"].append({"time": utcnow(), "event": "finalize"})
    atomic_write_json(out / "result.json", result)
    atomic_write_text(out / "report.md", render_report(result))
    write_worklog(work, "finalize")
    print(f"finalized: status={result['status']}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.set_defaults(func=run_cmd, cmd="run")
    ap.add_argument("--input", default=str(DEFAULT_INPUT))
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT))
    ap.add_argument("--work", default=str(DEFAULT_WORK))
    sub = ap.add_subparsers(dest="cmd")
    sub.required = False

    for name, fn in (("run", run_cmd), ("merge", merge_cmd), ("finalize", finalize_cmd)):
        sp = sub.add_parser(name)
        sp.add_argument("--input", default=str(DEFAULT_INPUT))
        sp.add_argument("--output", default=str(DEFAULT_OUTPUT))
        sp.add_argument("--work", default=str(DEFAULT_WORK))
        if name == "merge":
            sp.add_argument("--findings", required=True)
            sp.add_argument("--status", default=None)
        if name == "finalize":
            sp.add_argument("--status", default=None)
            sp.add_argument("--summary", default=None)
        sp.set_defaults(func=fn)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
