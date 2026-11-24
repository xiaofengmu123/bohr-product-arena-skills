#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — orchestrator for the source-bound audit skill.

Subcommands
-----------
run     (default) discover, extract and write a valid *partial* result.json
        skeleton plus evidence.json, plan.md and report.md. Designed to run in
        the first minutes so any interruption still leaves an honest deliverable.
merge   merge an Agent-adjudicated findings file into result.json; update
        report.md; append a change_log entry. Never deletes existing data.
report  regenerate report.md from result.json (idempotent).

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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract  # noqa: E402

DEFAULT_INPUT = Path("/input")
DEFAULT_OUTPUT = Path("/app/output")
DEFAULT_WORK = Path("/app/work")

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
FINDING_TYPES = {"fact_error", "insufficient_evidence", "norm_violation", "suggestion"}
SEVERITIES = {"critical", "major", "minor", "info"}
DIMENSIONS = [
    "academic_integrity", "citation", "privacy", "copyright", "ethics",
    "submission", "quality", "method", "statistics", "logic",
]
CORE_KEYS = {
    "case_id", "status", "report", "evidence_map", "sources", "uncertainty",
    "change_log", "errors", "degradations", "pending_human", "runtime",
    "output_contract",
}
FINDING_REQUIRED = {
    "id", "type", "severity", "category", "title", "file", "locator", "quote",
    "evidence", "basis", "revision",
}
REJECTION_REQUIRED = {"observation_id", "reason"}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def build_skeleton(evidence: dict):
    """Build a partial result.json from deterministic evidence."""
    case_id = evidence.get("case_id", "unknown")
    contract = evidence.get("contract", {})
    coverage = {}
    for dim in DIMENSIONS:
        coverage[dim] = {"status": "not_checked", "note": ""}
    result = {
        "case_id": case_id,
        "status": "partial",
        "report": {
            "summary": "",
            "findings": [],
            "coverage": coverage,
            "rejections": [],
            "pending_review": [],
            "not_checked": [],
        },
        "evidence_map": {},
        "sources": evidence.get("files", []),
        "uncertainty": [],
        "change_log": [{"time": utcnow(), "event": "partial skeleton written"}],
        "errors": list(evidence.get("errors", [])),
        "degradations": list(evidence.get("degradations", [])),
        "pending_human": [],
        "runtime": {
            "started_at": utcnow(),
            "steps": [{"step": "skeleton", "at": utcnow()}],
        },
    }
    if contract:
        result["output_contract"] = contract
        for key, value in contract.items():
            if key not in CORE_KEYS and key not in result:
                result[key] = value
    else:
        result["output_contract"] = {}
        result["contract_missing"] = True
    return result


def write_plan(work: Path, case_id: str, obs_count: int) -> None:
    text = (
        "# 审查执行计划\n\n"
        f"- case_id: `{case_id}`\n"
        f"- 候选观察数: `{obs_count}`（读 `evidence.json` 逐条判定）\n"
        f"- 预算: 900 秒 / 64 次模型请求；前 3 分钟已落盘 partial 骨架，此后只做增量。\n\n"
        "## 步骤\n\n"
        "1. 读 `/input/case.json` 与 `/input/task.md`，记下检查范围与材料清单。\n"
        "2. 读 `/app/work/evidence.json`，把每条 observation 判定为：确认 / 排除 / 改类型或严重度。\n"
        "3. 按六个方向（学术诚信、引用、隐私、版权、伦理、投稿要求）+ 质量/方法/统计/逻辑补查遗漏。\n"
        "4. 每条确认的问题写全：file / locator / quote / evidence / basis / revision。\n"
        "5. 把判定写入 `/app/work/findings.json`，运行 "
        "`python3 -B scripts/main.py merge --findings /app/work/findings.json`。\n"
        "6. 最后运行 `python3 -B scripts/validate.py` 修 FAIL。\n\n"
        "## findings.json 结构\n\n"
        "```json\n"
        '{"status": "partial",\n'
        ' "findings": [{"id": "F-001", "type": "...", "severity": "...", '
        '"category": "...", "title": "...", "file": "...", "locator": "...", '
        '"quote": "...", "evidence": "...", "basis": "...", "revision": "..."}],\n'
        ' "rejections": [{"observation_id": "OBS-001", "reason": "..."}],\n'
        ' "coverage": {"citation": {"status": "checked", "note": "..."}},\n'
        ' "pending_review": ["..."], "not_checked": ["..."],\n'
        ' "summary": "...", "uncertainty": ["..."], "pending_human": ["..."]}\n'
        "```\n"
    )
    _atomic_write_text(work / "plan.md", text)


def write_report_skeleton(out: Path, case_id: str, status: str) -> None:
    text = (
        "# 学术规范与合规审查报告\n\n"
        f"- case_id: `{case_id}`\n"
        f"- status: `{status}`\n"
        f"- 生成时间: {utcnow()}\n\n"
        "## 结论摘要\n\n（待填写：总体结论、覆盖的检查项、主要风险与可执行建议。）\n\n"
        "## 问题清单\n\n"
        "（每条按：编号 | 类型 | 严重度 | 位置 | 逐字证据 | 判定依据 | 修改建议。）\n\n"
        "## 已排除的候选观察\n\n"
        "（记录被判为误报/风格差异的 observation 及排除理由。）\n\n"
        "## 待复核 / 未检查项\n\n"
        "（列出证据不足、材料缺失或未覆盖的检查项。）\n\n"
        "## 依据与局限\n\n"
        "（列出所用规范条款、期刊要求、授权范围及其版本/日期，并说明无法判断之处。）\n"
    )
    _atomic_write_text(out / "report.md", text)


def cmd_run(args) -> int:
    inp = Path(args.input)
    out = Path(args.output)
    work = Path(args.work)
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    evidence = extract.run_extraction(inp, work)
    _atomic_write_json(work / "evidence.json", evidence)
    result = build_skeleton(evidence)
    _atomic_write_json(out / "result.json", result)
    write_plan(work, evidence.get("case_id", "unknown"),
               len(evidence.get("observations", [])))
    write_report_skeleton(out, evidence.get("case_id", "unknown"), "partial")
    print(f"run: case_id={evidence.get('case_id')} "
          f"observations={len(evidence.get('observations', []))} "
          f"status=partial")
    return 0


def _normalize_id(fid: str, existing: set) -> str:
    if fid and fid not in existing:
        return fid
    n = 1
    while f"F-{n:03d}" in existing:
        n += 1
    return f"F-{n:03d}"


def cmd_merge(args) -> int:
    inp = Path(args.input)
    out = Path(args.output)
    work = Path(args.work)
    result_path = out / "result.json"
    result = _load_json(result_path)
    if result is None:
        result_path = out / "result.json"
        result = _load_json(result_path) or {}
    if not result:
        print("merge: result.json missing; run `main.py run` first", file=sys.stderr)
        return 1
    payload = _load_json(Path(args.findings))
    if payload is None:
        print("merge: findings file unreadable", file=sys.stderr)
        return 1

    existing_ids = {f.get("id") for f in result.get("report", {}).get("findings", [])}
    new_findings = []
    problems = []
    for raw in payload.get("findings", []):
        missing = [k for k in FINDING_REQUIRED if not raw.get(k)]
        if raw.get("type") not in FINDING_TYPES:
            missing.append(f"type∉{sorted(FINDING_TYPES)}")
        if raw.get("severity") not in SEVERITIES:
            missing.append(f"severity∉{sorted(SEVERITIES)}")
        if missing:
            problems.append(f"{raw.get('id', '?')}: missing/invalid {missing}")
            continue
        fid = _normalize_id(raw.get("id"), existing_ids)
        existing_ids.add(fid)
        finding = dict(raw)
        finding["id"] = fid
        new_findings.append(finding)

    # evidence_map: one primary anchor per finding + optional extra anchors
    emap = dict(result.get("evidence_map", {}))
    for f in new_findings:
        anchors = [{"file": f["file"], "locator": f["locator"],
                    "quote": f["quote"], "role": "primary"}]
        for extra in payload.get("evidence_anchors", {}).get(f["id"], []):
            if isinstance(extra, dict):
                anchors.append(extra)
        emap[f["id"]] = anchors
    result["evidence_map"] = emap

    report = result.setdefault("report", {})
    report.setdefault("findings", [])
    report["findings"].extend(new_findings)

    # rejections
    rejections = report.setdefault("rejections", [])
    for rj in payload.get("rejections", []):
        if isinstance(rj, dict) and rj.get("observation_id") and rj.get("reason"):
            rejections.append(rj)

    # coverage
    cov = report.setdefault("coverage", {})
    for dim, entry in (payload.get("coverage") or {}).items():
        if dim in DIMENSIONS and isinstance(entry, dict):
            st = entry.get("status", "not_checked")
            if st not in ("checked", "pending", "not_checked"):
                st = "not_checked"
            cov[dim] = {"status": st, "note": entry.get("note", "")}

    # review queues
    for key in ("pending_review", "not_checked"):
        added = payload.get(key, [])
        if isinstance(added, list):
            report.setdefault(key, [])
            report[key].extend(added)

    # honest status override (validated)
    if payload.get("status") in ALLOWED_STATUS:
        result["status"] = payload["status"]

    if payload.get("summary"):
        report["summary"] = payload["summary"]
    result["uncertainty"] = result.get("uncertainty", []) + list(payload.get("uncertainty", []))
    result["pending_human"] = result.get("pending_human", []) + list(payload.get("pending_human", []))
    result["errors"] = result.get("errors", []) + list(payload.get("errors", []))
    result["degradations"] = result.get("degradations", []) + list(payload.get("degradations", []))

    log = result.setdefault("change_log", [])
    log.append({
        "time": utcnow(),
        "event": f"merged {len(new_findings)} findings, "
                 f"{len(rejections)} rejections; {len(problems)} skipped",
    })
    result["runtime"] = result.get("runtime", {"started_at": utcnow()})
    result["runtime"]["steps"] = result["runtime"].get("steps", []) + [
        {"step": "merge", "at": utcnow(), "findings": len(new_findings)}]

    _atomic_write_json(result_path, result)
    _write_report_from_result(out, result)
    if problems:
        _atomic_write_json(work / "merge_problems.json", {"problems": problems})
        print(f"merge: {len(new_findings)} findings, {len(problems)} skipped -> "
              f"{work / 'merge_problems.json'}")
    else:
        print(f"merge: {len(new_findings)} findings merged")
    return 0


def _finding_line(f) -> str:
    return (
        f"### {f['id']} · [{f['severity']}] {f['type']} · {f.get('category', '')}\n\n"
        f"- 标题：{f['title']}\n"
        f"- 位置：`{f['file']}` {f['locator']}\n"
        f"- 逐字证据：> {f['quote']}\n"
        f"- 事实依据：{f['evidence']}\n"
        f"- 判定依据：{f['basis']}\n"
        f"- 修改建议：{f['revision']}\n"
    )


def _write_report_from_result(out: Path, result: dict) -> None:
    rep = result.get("report", {})
    findings = rep.get("findings", [])
    rejections = rep.get("rejections", [])
    cov = rep.get("coverage", {})
    lines = [
        "# 学术规范与合规审查报告\n",
        f"- case_id: `{result.get('case_id', 'unknown')}`",
        f"- status: `{result.get('status', 'partial')}`",
        f"- 生成时间: {utcnow()}\n",
        "## 结论摘要\n",
        rep.get("summary", "（未填写）") + "\n",
        "## 问题清单\n",
    ]
    if findings:
        for f in findings:
            lines.append(_finding_line(f))
    else:
        lines.append("（尚无确认问题。）\n")
    lines.append("## 已排除的候选观察\n")
    if rejections:
        for rj in rejections:
            lines.append(f"- `{rj.get('observation_id')}`: {rj.get('reason')}")
        lines.append("")
    else:
        lines.append("（无。）\n")
    lines.append("## 覆盖矩阵\n")
    for dim in DIMENSIONS:
        entry = cov.get(dim, {})
        lines.append(f"- {dim}: `{entry.get('status', 'not_checked')}` {entry.get('note', '')}")
    lines.append("\n## 待复核 / 未检查项\n")
    for item in rep.get("pending_review", []) + rep.get("not_checked", []):
        lines.append(f"- {item}")
    lines.append("\n## 不确定性\n")
    for u in result.get("uncertainty", []):
        lines.append(f"- {u}")
    lines.append("\n## 依据与局限\n")
    for d in result.get("degradations", []) + result.get("errors", []):
        lines.append(f"- {d}")
    _atomic_write_text(out / "report.md", "\n".join(lines))


def cmd_report(args) -> int:
    out = Path(args.output)
    result = _load_json(out / "result.json")
    if result is None:
        print("report: result.json missing", file=sys.stderr)
        return 1
    _write_report_from_result(out, result)
    print("report: report.md regenerated")
    return 0


def _add_io_args(p: argparse.ArgumentParser, suppress_defaults: bool = False) -> None:
    for flag, default in (("--input", DEFAULT_INPUT), ("--output", DEFAULT_OUTPUT),
                          ("--work", DEFAULT_WORK)):
        p.add_argument(flag, default=(argparse.SUPPRESS if suppress_defaults
                                      else str(default)))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="main.py",
                                 description="Source-bound academic audit orchestrator")
    _add_io_args(ap)
    sub = ap.add_subparsers(dest="command")
    rp = sub.add_parser("run", help="discover + extract + write partial skeleton")
    _add_io_args(rp, suppress_defaults=True)
    mp = sub.add_parser("merge", help="merge adjudicated findings")
    _add_io_args(mp, suppress_defaults=True)
    mp.add_argument("--findings", required=True)
    vp = sub.add_parser("report", help="regenerate report.md from result.json")
    _add_io_args(vp, suppress_defaults=True)
    return ap


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "merge":
        return cmd_merge(args)
    if args.command == "report":
        return cmd_report(args)
    return cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
