#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Orchestrator for the source-bound-audit skill.

Subcommands
-----------
run       discover inputs, extract the deterministic evidence index, and write
          an honest *partial* result.json plus report.md skeleton and work
          files. Safe to run in the first minutes; an interruption still
          leaves a parseable deliverable behind.
merge     merge an agent-written findings file (findings.json) into the
          existing result.json, verify quotes against the source index, sort
          deterministically, and regenerate report.md.
finalize  compute an honest status from findings/facets/errors, append a
          change_log entry, and write the final result.json.

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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract  # noqa: E402

DEFAULT_INPUT = Path("/input")
DEFAULT_OUTPUT = Path("/app/output")
DEFAULT_WORK = Path("/app/work")

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
FINDING_TYPES = {"fact_error", "insufficient_evidence", "norm_violation", "suggestion"}
SEVERITIES = {"critical", "major", "minor", "info"}
SEVERITY_RANK = {"critical": 0, "major": 1, "minor": 2, "info": 3}
FACETS = [
    "academic_integrity", "citation", "privacy", "copyright", "ethics",
    "submission", "quality", "method", "statistics", "logic",
]
FACET_VIA_CATEGORY = {
    "academic_integrity": "academic_integrity",
    "citation": "citation",
    "privacy": "privacy",
    "copyright": "copyright",
    "ethics": "ethics",
    "submission": "submission",
    "quality": "quality",
    "method": "method",
    "statistics": "statistics",
    "logic": "logic",
    "fact": "quality",
    "norm": "submission",
}
CATEGORIES = set(FACET_VIA_CATEGORY.keys())

CORE_KEYS = {
    "case_id", "status", "report", "sources", "uncertainty", "change_log",
    "errors", "degradations", "pending_human", "runtime", "output_contract",
    "contract_missing", "finalized",
}

FINDING_REQUIRED = {
    "id", "type", "severity", "category", "title", "file", "locator", "quote",
    "evidence", "basis", "revision",
}
FACET_VERDICTS = {"ok", "issues", "pending", "not_checked"}


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


def load_result(out: Path):
    path = out / "result.json"
    if path.exists():
        obj, err = extract.read_json_robust(path)
        if not err and isinstance(obj, dict):
            return obj
    return None


def source_index_for(root: Path, work: Path):
    """Rebuild the source index so quote verification is deterministic."""
    idx_path = work / "evidence.json"
    if idx_path.exists():
        obj, err = extract.read_json_robust(idx_path)
        if not err and isinstance(obj, dict) and "sources" in obj:
            return obj
    index = extract.build_index(root, work)
    _case, case_id, contract, errors = extract.load_case(root)
    index["case_id"] = case_id
    index["contract"] = contract
    index["contract_missing"] = not contract
    index["errors"] = errors
    return index


def resolve_quote(index, file_, locator, quote):
    """Check that the normalized quote is an exact substring of a source.

    Returns (verified: bool, note: str).
    """
    q = extract.normalize(quote or "")
    if not q:
        return False, "empty quote"
    for src in index.get("sources", []):
        if file_ and src["path"] != file_ and src["path"] != Path(file_).name:
            continue
        # If a locator is given, prefer that exact paragraph.
        target = None
        if locator:
            for p in src["paragraphs"]:
                if p["loc"] == locator or locator.endswith("#" + p["loc"]):
                    target = p["text"]
                    break
        if target is not None:
            if q in target:
                return True, ""
            continue
        # Fallback: search whole file.
        if q in " ".join(p["text"] for p in src["paragraphs"]):
            return True, ""
        # Try prefix match on any paragraph (quotes may be truncated).
        for p in src["paragraphs"]:
            if q[:40] in p["text"]:
                return True, "prefix match (quote may be truncated)"
    return False, "quote not found in source index"


def default_facets():
    return [
        {"facet": f, "verdict": "not_checked", "summary": "", "finding_ids": []}
        for f in FACETS
    ]


def build_skeleton(case_id, contract, index, errors, degradations, contract_missing):
    facets = default_facets()
    skeleton = {
        "case_id": case_id,
        "status": "partial",
        "report": {
            "summary": "",
            "findings": [],
            "facets": facets,
            "checked_items": [],
            "pending_review": [],
            "not_checked": [],
        },
        "sources": [
            {
                "path": s["path"],
                "role": s["role"],
                "size": s["size"],
                "parse": s.get("parse", "ok"),
                "paragraphs": len(s["paragraphs"]),
            }
            for s in index["sources"]
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
    }
    if contract:
        defaults = extract.default_for(contract)
        skeleton["output_contract"] = defaults
        for key, value in defaults.items():
            if key not in CORE_KEYS and key not in skeleton:
                skeleton[key] = value
        for leaf_name, _leaf_val in extract.flatten_leaves(defaults).items():
            if leaf_name not in CORE_KEYS and leaf_name not in skeleton:
                skeleton[leaf_name] = None
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
        "## 结论摘要\n\n（待填写：总体结论、已覆盖检查面、主要风险与建议。）\n\n"
        "## 问题清单\n\n"
        "（每条按：编号 | 类型 | 严重度 | 位置 | 逐字证据 | 判定依据 | 修改建议 填写。）\n\n"
        "## 检查面（facet）结论\n\n"
        "（对十项检查面逐项给出 ok / issues / pending / not_checked 结论。）\n\n"
        "## 待复核 / 未检查项\n\n"
        "## 依据与局限\n\n"
    )
    atomic_write_text(out / "report.md", text)


def write_plan(work: Path, case_id: str, index) -> None:
    n_src = len(index["sources"])
    n_hits = sum(len(s["hits"]) for s in index["sources"])
    text = (
        "# 执行计划（供审查 Agent 使用）\n\n"
        f"- case_id: `{case_id}`\n"
        f"- 已解析材料: {n_src} 个；候选观察: {n_hits} 条\n\n"
        "## 执行顺序\n"
        "1. 读 evidence.json 掌握材料结构与候选观察。\n"
        "2. 逐检查面核对原文；每条 finding 写全 id/type/severity/category/facet/title/file/"
        "locator/quote/evidence/basis/basis_rule/revision。\n"
        "3. 把 findings 写入 /app/work/findings.json，运行 merge 落盘。\n"
        "4. 最后 2–3 分钟运行 validate.py 与 finalize。\n"
    )
    atomic_write_text(work / "plan.md", text)


def write_empty_findings(work: Path) -> None:
    atomic_write_json(work / "findings.json", {"findings": []})


def run(args):
    root = Path(args.input)
    out = Path(args.output)
    work = Path(args.work)
    for d in (out, work):
        d.mkdir(parents=True, exist_ok=True)

    _case, case_id, contract, errors = extract.load_case(root)
    contract_missing = not contract
    index = extract.build_index(root, work)
    index["case_id"] = case_id
    index["contract"] = contract
    index["contract_missing"] = contract_missing
    index["errors"] = errors
    atomic_write_json(work / "evidence.json", index)

    degradations = list(index["degradations"])
    skeleton = build_skeleton(case_id, contract, index, errors, degradations, contract_missing)
    # Honest initial status: unreadable contract is an input error; no parsed
    # material is insufficient evidence; otherwise keep the partial skeleton.
    if any("unreadable" in e for e in errors):
        skeleton["status"] = "input_error"
    elif not index["sources"] or all(s.get("parse") != "ok" for s in index["sources"]):
        skeleton["status"] = "insufficient_evidence"
    atomic_write_json(out / "result.json", skeleton)
    write_report_skeleton(out, case_id, skeleton["status"])
    write_plan(work, case_id, index)
    if not (work / "findings.json").exists():
        write_empty_findings(work)
    print(f"partial skeleton written: case_id={case_id}, sources={len(index['sources'])}, "
          f"hits={sum(len(s['hits']) for s in index['sources'])}")
    return 0


def normalize_finding(f, index):
    if not isinstance(f, dict):
        return None, "finding is not an object"
    # id is auto-assigned by the merge step in sorted order, so it is not
    # required from the agent; an agent-supplied id is recorded as a hint only.
    req = {k: f.get(k) for k in FINDING_REQUIRED if k != "id"}
    missing = [k for k, v in req.items() if v is None or (isinstance(v, str) and not v.strip())]
    fid = (f.get("id") or "").strip()
    cat = (f.get("category") or "quality").strip().lower()
    if cat not in CATEGORIES:
        cat = "quality"
    facet = f.get("facet") or FACET_VIA_CATEGORY.get(cat, "quality")
    quote = extract.normalize(f.get("quote") or "")
    verified, note = resolve_quote(
        index,
        f.get("file") or "",
        f.get("locator") or "",
        quote,
    )
    out = {
        "id": fid,
        "type": f.get("type") if f.get("type") in FINDING_TYPES else "suggestion",
        "severity": f.get("severity") if f.get("severity") in SEVERITIES else "info",
        "category": cat,
        "facet": facet,
        "title": (f.get("title") or "").strip(),
        "file": (f.get("file") or "").strip(),
        "locator": (f.get("locator") or "").strip(),
        "quote": quote,
        "evidence": (f.get("evidence") or "").strip(),
        "basis": (f.get("basis") or "").strip(),
        "basis_rule": (f.get("basis_rule") or "").strip(),
        "revision": (f.get("revision") or "").strip(),
        "quote_verified": verified,
    }
    if note:
        out["quote_note"] = note
    issues = []
    if missing:
        issues.append("missing fields: " + ", ".join(missing))
    if f.get("type") not in FINDING_TYPES:
        issues.append(f"invalid type {f.get('type')!r}")
    if f.get("severity") not in SEVERITIES:
        issues.append(f"invalid severity {f.get('severity')!r}")
    if not verified:
        issues.append("quote not verified: " + note)
    return out, ("; ".join(issues) if issues else "")


def merge(args):
    root = Path(args.input)
    out = Path(args.output)
    work = Path(args.work)
    findings_path = Path(args.findings) if args.findings else (work / "findings.json")

    result = load_result(out) or build_skeleton(
        "unknown", {}, {"sources": [], "degradations": []}, [], [], True
    )
    index = source_index_for(root, work)

    findings_obj, err = extract.read_json_robust(findings_path)
    if err:
        result["errors"].append(f"findings.json unreadable: {err}")
        findings_obj = {"findings": []}
    if not isinstance(findings_obj, dict):
        findings_obj = {"findings": []}
    raw_findings = findings_obj.get("findings", []) if isinstance(findings_obj.get("findings"), list) else []

    # Optional state fields the agent may write alongside findings.
    if findings_obj.get("summary") is not None:
        result["report"]["summary"] = str(findings_obj["summary"]).strip()
    if isinstance(findings_obj.get("uncertainty"), list):
        result["uncertainty"] = findings_obj["uncertainty"]
    if isinstance(findings_obj.get("pending_human"), list):
        result["pending_human"] = findings_obj["pending_human"]
    if isinstance(findings_obj.get("errors"), list):
        for e in findings_obj["errors"]:
            if isinstance(e, str) and e not in result.setdefault("errors", []):
                result["errors"].append(e)
    if findings_obj.get("status") in ALLOWED_STATUS:
        result["status"] = findings_obj["status"]
    facet_updates = findings_obj.get("facets") if isinstance(findings_obj.get("facets"), dict) else {}

    existing_ids = {f.get("id") for f in result["report"]["findings"]}

    norm_findings = []
    merge_issues = []
    for f in raw_findings:
        if not isinstance(f, dict):
            merge_issues.append("skipped non-object finding")
            continue
        nf, issue = normalize_finding(f, index)
        if nf is not None:
            norm_findings.append(nf)
        if issue:
            merge_issues.append(issue)

    norm_findings.sort(key=lambda f: (
        SEVERITY_RANK.get(f["severity"], 9),
        f["facet"],
        f["file"],
        f["locator"],
    ))
    # Deterministic, history-independent numbering in sorted order.
    for i, nf in enumerate(norm_findings, 1):
        nf["id"] = "F%03d" % i

    result["report"]["findings"] = norm_findings
    result["report"]["merge_issues"] = merge_issues
    result["change_log"].append({
        "time": utcnow(),
        "event": f"merged {len(norm_findings)} findings; {len(merge_issues)} issues",
    })
    result["runtime"].setdefault("steps", []).append({"step": "merge", "at": utcnow()})
    result.setdefault("errors", [])
    if not result.get("status") or result["status"] == "ok":
        result["status"] = "partial"

    sync_facets(result, facet_updates)
    regenerate_report(result, out)
    atomic_write_json(out / "result.json", result)
    print(f"merged {len(norm_findings)} findings -> {out / 'result.json'}")
    return 0


def sync_facets(result, facet_updates=None):
    facet_updates = facet_updates or {}
    facets = {f["facet"]: f for f in result["report"].setdefault("facets", [])}
    for fname in FACETS:
        if fname not in facets:
            facets[fname] = {"facet": fname, "verdict": "not_checked", "summary": "", "finding_ids": []}
    # Apply agent-written verdicts/summaries first (findings below win when present).
    for fname, upd in facet_updates.items():
        if fname not in facets:
            continue
        if isinstance(upd, dict):
            if upd.get("verdict") in FACET_VERDICTS:
                facets[fname]["verdict"] = upd["verdict"]
            if upd.get("summary") is not None:
                facets[fname]["summary"] = str(upd["summary"]).strip()
        elif isinstance(upd, str) and upd in FACET_VERDICTS:
            facets[fname]["verdict"] = upd
    for f in facets.values():
        f["finding_ids"] = []
        f.setdefault("summary", "")
    for finding in result["report"]["findings"]:
        fname = finding.get("facet", "quality")
        if fname in facets:
            facets[fname]["finding_ids"].append(finding["id"])
            facets[fname]["verdict"] = "issues"  # a finding always implies issues
    result["report"]["facets"] = [facets[f] for f in FACETS]


def compute_status(result):
    findings = result["report"]["findings"]
    facets = result["report"].get("facets", [])
    errors = result.get("errors", [])
    if any("case.json unreadable" in e or "not found" in e and "case.json" in e for e in errors):
        return "input_error"
    sources = result.get("sources", [])
    if not sources or all(s.get("parse") != "ok" for s in sources):
        return "insufficient_evidence"
    non_info = [f for f in findings if f["severity"] != "info"]
    non_suggestion = [f for f in findings if f["type"] != "suggestion"]
    if non_info or non_suggestion:
        return "partial"
    if all(f["verdict"] in ("ok", "issues") for f in facets) and facets:
        return "ok"
    return "partial"


def finalize(args):
    root = Path(args.input)
    out = Path(args.output)
    work = Path(args.work)
    result = load_result(out)
    if result is None:
        print("result.json not found; run 'main.py run' first", file=sys.stderr)
        return 1
    result.setdefault("report", {}).setdefault("findings", [])
    result.setdefault("errors", [])
    sync_facets(result)
    if not result.get("case_id"):
        _case, case_id, _contract, _e = extract.load_case(root)
        result["case_id"] = case_id or "unknown"
    result["status"] = compute_status(result)
    result["finalized"] = True
    result["change_log"].append({"time": utcnow(), "event": f"finalized status={result['status']}"})
    result["runtime"].setdefault("steps", []).append({"step": "finalize", "at": utcnow()})
    regenerate_report(result, out)
    atomic_write_json(out / "result.json", result)
    print(f"finalized: status={result['status']}, findings={len(result['report']['findings'])}")
    return 0


def regenerate_report(result, out: Path):
    case_id = result.get("case_id", "unknown")
    status = result.get("status", "partial")
    findings = result["report"]["findings"]
    facets = result["report"].get("facets", [])
    pending = result.get("pending_human", [])
    errors = result.get("errors", [])
    degradations = result.get("degradations", [])
    uncertainty = result.get("uncertainty", [])
    merge_issues = result["report"].get("merge_issues", [])

    lines = [
        "# 学术规范与合规审查报告",
        "",
        f"- case_id: `{case_id}`",
        f"- status: `{status}`",
        f"- 生成时间: {utcnow()}",
        "",
        "## 结论摘要",
        "",
        (result["report"].get("summary") or "（未填写摘要。）").strip(),
        "",
        "## 问题清单",
        "",
    ]
    if findings:
        lines.append("| 编号 | 类型 | 严重度 | 检查面 | 位置 | 证据 | 修改建议 |")
        lines.append("|---|---|---|---|---|---|---|")
        for f in findings:
            lines.append(
                f"| {f['id']} | {f['type']} | {f['severity']} | {f.get('facet', '')} | "
                f"`{f['locator'] or f['file']}` | {_cell(f['quote'])} | {_cell(f['revision'])} |"
            )
    else:
        lines.append("（无 finding。）")
    lines += ["", "## 检查面结论", ""]
    if facets:
        lines.append("| 检查面 | 结论 | 说明 |")
        lines.append("|---|---|---|")
        for f in facets:
            lines.append(f"| {f['facet']} | {f['verdict']} | {_cell(f.get('summary', ''))} |")
    lines += ["", "## 待复核", ""]
    lines.extend([f"- {p}" for p in pending] or ["（无。）"])
    lines += ["", "## 不确定性与局限", ""]
    lines.extend([f"- {u}" for u in uncertainty] or ["（未记录。）"])
    lines += ["", "## 错误与降级", ""]
    lines.extend([f"- ERROR: {e}" for e in errors] or ["（无错误。）"])
    lines.extend([f"- DEGRADE: {d}" for d in degradations] or [])
    lines.extend([f"- MERGE: {m}" for m in merge_issues] or [])
    lines += ["", "## 依据与边界", ""]
    lines.append("（逐条 finding 的判定依据见 result.json 的 `basis` 与 `basis_rule` 字段；"
                 "规则库见 references/audit-rules.md。）")
    atomic_write_text(out / "report.md", "\n".join(lines) + "\n")


def _cell(text):
    t = (text or "").replace("|", "\\|").replace("\n", " ")
    return t[:120] + ("…" if len(t) > 120 else "")


def main(argv=None):
    parser = argparse.ArgumentParser(description="source-bound-audit orchestrator")
    sub = parser.add_subparsers(dest="cmd")

    def add_common(p):
        p.add_argument("--input", default=str(DEFAULT_INPUT))
        p.add_argument("--output", default=str(DEFAULT_OUTPUT))
        p.add_argument("--work", default=str(DEFAULT_WORK))

    p_run = sub.add_parser("run", help="extract evidence and write partial skeleton")
    add_common(p_run)

    p_merge = sub.add_parser("merge", help="merge findings.json into result.json")
    add_common(p_merge)
    p_merge.add_argument("--findings", default=None)

    p_fin = sub.add_parser("finalize", help="compute honest status and finalize")
    add_common(p_fin)

    args = parser.parse_args(argv)
    if args.cmd == "run":
        return run(args)
    if args.cmd == "merge":
        return merge(args)
    if args.cmd == "finalize":
        return finalize(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
