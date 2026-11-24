#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Orchestrator for the academic compliance audit skill (facet-driven).

Subcommands
-----------
run       discover inputs, parse text, build deterministic evidence, and write
          a valid *partial* result.json plus report.md, evidence.json and
          plan.md. Designed to run within the first minutes so any interruption
          still leaves a parseable, honest deliverable behind.
merge     merge an agent-written findings file into result.json, verify every
          quote against the evidence index, rebuild report.md, append the
          change_log.
finalize  re-validate and mark runtime end; optionally set an honest status.

Standard library only. Relative import of extract.py in the same directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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
CORE_KEYS = {
    "case_id", "status", "report", "sources", "uncertainty", "change_log",
    "errors", "degradations", "pending_human", "runtime", "output_contract",
    "contract_missing", "result_path", "report_path", "input_fingerprint",
}
FINDING_REQUIRED = {
    "id", "type", "severity", "category", "title", "file", "locator", "quote",
    "evidence", "basis", "revision",
}
FINDING_OPTIONAL = {"facet", "facet_id", "source_conflict", "confidence", "note"}


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


# ---------------------------------------------------------------------------
# Input discovery
# ---------------------------------------------------------------------------
def discover(inp: Path):
    items = []
    if not inp.exists():
        return items
    for p in sorted(inp.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        name = p.name.lower()
        rel = str(p)
        try:
            rel = str(p.relative_to(inp))
        except ValueError:
            pass
        if name == "case.json" or (name.startswith("case") and name.endswith(".json")):
            role = "case"
        elif name == "task.md" or (name.startswith("task") and name.endswith(".md")):
            role = "task"
        elif p.suffix.lower() in extract.PDF_SUFFIXES:
            role = "pdf"
        elif p.suffix.lower() in extract.TEXT_SUFFIXES:
            role = "material"
        else:
            role = "artifact"
        items.append({"path": p, "name": p.name, "role": role, "size": size, "rel": rel})
    return items


def _slug(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "_", str(s)).strip("_").lower()
    return s or "facet"


def _normalize_facets(value):
    """Turn a case-provided facet structure into a canonical list of dicts.

    Preserves task-specific finding typing and evidence/revision requirements
    so the agent audits each facet the way the case defines it."""
    out = []
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, str):
                out.append({"id": _slug(k), "name": k, "definition": v, "hint_terms": []})
            elif isinstance(v, dict):
                out.append(_facet_from(v, fallback_id=k))
        return out
    if isinstance(value, list):
        for it in value:
            if isinstance(it, str):
                out.append({"id": _slug(it), "name": it, "definition": "", "hint_terms": []})
            elif isinstance(it, dict):
                out.append(_facet_from(it, fallback_id="facet"))
    return out


def _facet_from(v, fallback_id):
    fid = v.get("id") or v.get("name") or fallback_id
    f = {
        "id": _slug(fid),
        "name": str(v.get("name") or v.get("title") or fid),
        "definition": str(v.get("description") or v.get("definition") or ""),
        "hint_terms": list(v.get("hint_terms") or v.get("keywords") or []),
    }
    for key in ("finding_types", "evidence_required", "revision_required",
                "required_fields", "rubric", "checklist"):
        if v.get(key) is not None:
            f[key] = v[key]
    return f


def load_facets(case):
    if not isinstance(case, dict):
        return [dict(f) for f in extract.DEFAULT_FACETS]
    for key in ("review_facets", "facets", "audit_facets", "checklist", "review_dimensions"):
        v = case.get(key)
        if v:
            norm = _normalize_facets(v)
            if norm:
                return norm
    contract = case.get("output_contract")
    if isinstance(contract, dict):
        for key in ("review_facets", "facets", "checklist", "review_dimensions"):
            v = contract.get(key)
            if v:
                norm = _normalize_facets(v)
                if norm:
                    return norm
    return [dict(f) for f in extract.DEFAULT_FACETS]


def load_case(inp: Path):
    errors = []
    items = discover(inp)
    case_item = next((it for it in items if it["role"] == "case"), None)
    if case_item is None:
        return None, "unknown", {}, extract.DEFAULT_FACETS, ["/input/case.json not found"]
    case, err = extract.read_json_robust(case_item["path"])
    if err:
        return None, "unknown", {}, extract.DEFAULT_FACETS, [f"case.json unreadable: {err}"]
    case_id = None
    for key in ("case_id", "id", "challenge_id", "task_id"):
        val = case.get(key)
        if isinstance(val, str) and val:
            case_id = val
            break
    if not case_id:
        case_id = case_item["path"].stem
    contract = case.get("output_contract") if isinstance(case.get("output_contract"), dict) else {}
    facets = load_facets(case)
    return case, case_id, contract, facets, errors


# ---------------------------------------------------------------------------
# Contract handling
# ---------------------------------------------------------------------------
def default_for(value):
    """Fill missing contract leaves with null; preserve structure and keys."""
    if isinstance(value, dict):
        return {k: default_for(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    return None


def leaf_keys(value, prefix=""):
    keys = []
    if isinstance(value, dict):
        for k, v in value.items():
            keys.extend(leaf_keys(v, prefix + str(k) + "."))
    elif isinstance(value, (list, type(None))):
        if prefix:
            keys.append(prefix.rstrip("."))
    else:
        if prefix:
            keys.append(prefix.rstrip("."))
    return keys


# ---------------------------------------------------------------------------
# Skeleton
# ---------------------------------------------------------------------------
def build_skeleton(case_id, contract, facets, files, errors, degradations, fingerprint):
    scope = [f["id"] for f in facets]
    skeleton = {
        "case_id": case_id,
        "status": "partial",
        "input_fingerprint": fingerprint,
        "report": {
            "summary": "",
            "scope": scope,
            "facets": facets,
            "facet_coverage": {f["id"]: {"name": f["name"], "status": "pending", "findings": 0} for f in facets},
            "findings": [],
            "checked_items": [],
            "pending_review": [],
            "not_checked": [],
        },
        "sources": [
            {"path": it["path"].as_posix() if hasattr(it["path"], "as_posix") else str(it["path"]),
             "role": it["role"], "size": it["size"], "rel": it.get("rel", "")}
            for it in files
        ],
        "uncertainty": [
            "自动证据抽取仅产生候选观察（observations），未经 Agent 逐条判定的观察不构成结论。"
        ],
        "change_log": [{"time": utcnow(), "event": "partial skeleton written", "fingerprint": fingerprint}],
        "errors": list(errors),
        "degradations": list(degradations),
        "pending_human": [],
        "runtime": {
            "started_at": utcnow(),
            "steps": [{"step": "skeleton", "at": utcnow()}],
        },
    }
    if contract:
        defaults = default_for(contract)
        skeleton["output_contract"] = defaults
        for key, value in defaults.items():
            if key not in CORE_KEYS and key not in skeleton:
                skeleton[key] = value
    else:
        skeleton["output_contract"] = {}
        skeleton["contract_missing"] = True
    return skeleton


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def _status_cn(status: str) -> str:
    return {
        "ok": "通过/结论可靠",
        "partial": "部分完成/存在待复核项",
        "insufficient_evidence": "证据不足",
        "input_error": "输入异常",
    }.get(status, status)


def rebuild_report(result, out: Path) -> None:
    rep = result.get("report", {})
    lines = [
        "# 学术规范与合规审查报告",
        "",
        f"- case_id: `{result.get('case_id')}`",
        f"- status: `{result.get('status')}`（{_status_cn(result.get('status'))}）",
        f"- 生成时间: {utcnow()}",
        f"- 输入指纹: `{result.get('input_fingerprint', '')[:16]}`",
        "",
        "## 结论摘要",
        "",
        rep.get("summary") or "（待填写）",
        "",
        "## 覆盖检查项",
        "",
        "| 检查方向 | 状态 | 问题数 |",
        "|-|-|-|",
    ]
    for fid, info in (rep.get("facet_coverage") or {}).items():
        lines.append(f"| {info.get('name', fid)} | {info.get('status', 'pending')} | {info.get('findings', 0)} |")
    lines += [
        "",
        "## 问题清单",
        "",
    ]
    findings = rep.get("findings") or []
    if not findings:
        lines.append("（无已确认问题。）")
    for f in findings:
        lines.append("")
        lines.append(f"### {f.get('id')} · {f.get('severity')} · {f.get('type')} · {f.get('category')}")
        lines.append(f"- 标题: {f.get('title', '')}")
        lines.append(f"- 位置: `{f.get('file', '')}` · `{f.get('locator', '')}`")
        lines.append(f"- 证据(逐字): {f.get('quote', '')}")
        lines.append(f"- 证据说明: {f.get('evidence', '')}")
        lines.append(f"- 判定依据: {f.get('basis', '')}")
        lines.append(f"- 修改建议: {f.get('revision', '')}")
        if f.get("quote_verified") is False:
            lines.append("- ⚠ 引用片段未能回查到材料原文，需人工复核。")
    lines += [
        "",
        "## 待复核 / 未检查项",
        "",
    ]
    for p in (rep.get("pending_review") or []):
        lines.append(f"- {p}")
    for n in (rep.get("not_checked") or []):
        lines.append(f"- {n}")
    if not rep.get("pending_review") and not rep.get("not_checked"):
        lines.append("（无。）")
    lines += [
        "",
        "## 依据与局限",
        "",
        "**errors**:",
    ]
    for e in result.get("errors") or []:
        lines.append(f"- {e}")
    lines.append("")
    lines.append("**degradations**:")
    for d in result.get("degradations") or []:
        lines.append(f"- {d}")
    lines.append("")
    lines.append("**uncertainty**:")
    for u in result.get("uncertainty") or []:
        lines.append(f"- {u}")
    lines.append("")
    lines.append("**pending_human**:")
    for p in result.get("pending_human") or []:
        lines.append(f"- {p}")
    lines.append("")
    atomic_write_text(out / "report.md", "\n".join(lines) + "\n")


def write_plan(work: Path, case_id: str) -> None:
    text = (
        "# 执行计划（供审查 Agent 使用）\n\n"
        f"- case_id: {case_id}\n"
        "- 预算: 900 秒 / 64 次模型请求；前 3 分钟先落盘 result.json 骨架，此后只做增量改进。\n\n"
        "1. 读 `/input/case.json` + `/input/task.md`，确认 case_id、output_contract、review facets、材料清单。\n"
        "2. PDF 用平台 pdf-text-tool（或 pdftotext）解析到 `/app/work/parsed/<原名>.txt`，再运行本脚本。\n"
        "3. `python3 -B scripts/main.py run`：立即写 result.json 骨架 + evidence.json + plan.md + report.md。\n"
        "4. 读 evidence.json 候选观察，逐条判定：成立/排除/调严重度，补查遗漏；每条写全 evidence/basis/revision。\n"
        "5. 把确认后的 findings 写 `/app/work/findings.json`，`merge` 回填并同步 report.md。\n"
        "6. 最后 `python3 -B scripts/validate.py` 自检，修 FAIL；预算不足时如实 partial。\n"
    )
    atomic_write_text(work / "plan.md", text)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
def cmd_run(args) -> int:
    inp = Path(args.input)
    out = Path(args.output)
    work = Path(args.work)
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    parsed_dir = work / "parsed"

    items = discover(inp)
    case, case_id, contract, facets, errors = load_case(inp)

    degradations = []
    parsed_items = []
    parse_pending = []

    for it in items:
        if it["role"] in ("material",):
            text, err = extract.read_text_robust(it["path"])
            if err:
                degradations.append(f"{it['path'].name}: {err}")
                continue
            parsed_items.append({"path": str(it["path"]), "role": "material",
                                 "size": it["size"], "text": text})
        elif it["role"] == "pdf":
            candidate = parsed_dir / (Path(it["name"]).stem + ".txt")
            if candidate.exists():
                text, err = extract.read_text_robust(candidate)
                if err is None:
                    parsed_items.append({"path": str(it["path"]), "role": "pdf_parsed",
                                         "size": it["size"], "text": text,
                                         "parsed_from": str(candidate)})
                    continue
            parse_pending.append(str(it["path"]))

    if parse_pending:
        degradations.append(
            "PDF 尚未解析，需先运行 pdf-text-tool/pdftotext 到 /app/work/parsed/ 后重跑 run: "
            + "; ".join(parse_pending)
        )

    if not case:
        errors.append("case.json 缺失或不可读，按 unknown case_id 继续。")

    # deterministic fingerprint over all discovered input bytes
    h = hashlib.sha256()
    for it in sorted(items, key=lambda x: x["rel"]):
        if it["role"] in ("case", "task", "material", "pdf"):
            try:
                h.update(it["rel"].encode("utf-8"))
                h.update(it["path"].read_bytes())
            except OSError:
                pass
    fingerprint = h.hexdigest()

    evidence = extract.build_evidence(case_id, parsed_items, facets, degradations)
    result = build_skeleton(case_id, contract, facets, items, errors, degradations, fingerprint)

    # material availability drives an honest fallback status: empty/no material
    # -> insufficient_evidence; otherwise partial (errors are recorded, not fatal).
    if not inp.exists():
        result["status"] = "input_error"
        result["errors"] = list(result.get("errors") or []) + ["输入目录不存在"]
    elif not parsed_items:
        result["status"] = "insufficient_evidence"
        result["pending_human"].append("无可用解析文本：请补充材料或解析 PDF。")

    result["runtime"]["steps"].append({"step": "run", "at": utcnow()})
    result["runtime"]["evidence_observations"] = len(evidence.get("observations", []))
    result["result_path"] = str(out / "result.json")
    result["report_path"] = str(out / "report.md")

    atomic_write_json(out / "result.json", result)
    atomic_write_json(work / "evidence.json", evidence)
    rebuild_report(result, out)
    write_plan(work, case_id)
    print(json.dumps({"case_id": case_id, "status": result["status"],
                      "files": len(items), "parsed": len(parsed_items),
                      "observations": len(evidence.get("observations", [])),
                      "errors": len(errors), "degradations": len(degradations),
                      "result": str(out / "result.json")}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------
def _load_findings(path: Path):
    obj, err = extract.read_json_robust(path)
    if err:
        return None, [f"findings 文件不可读: {err}"]
    if isinstance(obj, dict):
        obj = obj.get("findings", [])
    if not isinstance(obj, list):
        return None, ["findings 顶层必须是数组或含 findings 键的对象"]
    return obj, []


def _verify_finding_quote(finding, evidence):
    text = ""
    parsed = evidence.get("parsed_map") or {}
    f = str(finding.get("file") or "")
    if f in parsed:
        text = parsed[f]
    else:
        stem = Path(f).stem.lower()
        for p, t in parsed.items():
            if Path(p).stem.lower() == stem:
                text = t
                break
    found, ratio, mode = extract.verify_quote(text, finding.get("quote") or "")
    return found, ratio, mode


def cmd_merge(args) -> int:
    inp = Path(args.input)
    out = Path(args.output)
    work = Path(args.work)

    result_path = out / "result.json"
    evidence_path = work / "evidence.json"
    if not result_path.exists():
        print("result.json 不存在；请先运行 run。", file=sys.stderr)
        return 2
    result, err = extract.read_json_robust(result_path)
    if err:
        print(f"result.json 不可读: {err}", file=sys.stderr)
        return 2
    if evidence_path.exists():
        evidence, _ = extract.read_json_robust(evidence_path)
    else:
        evidence = {}

    findings, errs = _load_findings(Path(args.findings))
    accepted, rejected = [], []

    for i, raw in enumerate(findings, 1):
        if not isinstance(raw, dict):
            rejected.append(f"#{i} 非对象，跳过")
            continue
        f = dict(raw)
        missing = [k for k in FINDING_REQUIRED if not f.get(k)]
        if missing:
            rejected.append(f"#{i} 缺字段 {missing}，跳过")
            continue
        if f.get("type") not in FINDING_TYPES:
            rejected.append(f"#{i} type={f.get('type')} 非法，跳过")
            continue
        if f.get("severity") not in SEVERITIES:
            f["severity"] = "minor"
        if "id" not in f or not str(f.get("id", "")).strip():
            f["id"] = "F-%04d" % (len(accepted) + 1)
        f["id"] = str(f["id"])
        found, ratio, mode = _verify_finding_quote(f, evidence)
        f["quote_verified"] = bool(found)
        f["quote_match_mode"] = mode
        f["quote_match_ratio"] = round(float(ratio), 4)
        if f.get("category") not in [x["id"] for x in evidence.get("facets", [])]:
            f["category"] = "quality" if not f.get("category") else str(f.get("category"))
        accepted.append(f)

    rep = result.setdefault("report", {})
    rep["findings"] = accepted
    rep.setdefault("facet_coverage", {})
    for fid, info in rep["facet_coverage"].items():
        info["findings"] = sum(1 for f in accepted if f.get("category") == fid or f.get("facet") == fid)
        info["status"] = "issue" if info["findings"] else info.get("status", "pending")

    if args.status:
        if args.status in ALLOWED_STATUS:
            result["status"] = args.status
        else:
            rejected.append(f"--status={args.status} 非法，忽略")

    if rejected:
        result["errors"] = list(result.get("errors") or []) + rejected
        result["degradations"] = list(result.get("degradations") or []) + ["部分 findings 未通过校验被拒绝"]

    result["change_log"] = list(result.get("change_log") or []) + [{
        "time": utcnow(),
        "event": f"merge: {len(accepted)} accepted, {len(rejected)} rejected",
        "status": result["status"],
    }]
    result["runtime"] = result.get("runtime") or {}
    result["runtime"]["steps"] = list(result.get("runtime", {}).get("steps", [])) + [{"step": "merge", "at": utcnow()}]

    atomic_write_json(result_path, result)
    rebuild_report(result, out)
    print(json.dumps({"accepted": len(accepted), "rejected": len(rejected),
                      "status": result["status"]}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------
def cmd_finalize(args) -> int:
    out = Path(args.output)
    result_path = out / "result.json"
    if not result_path.exists():
        print("result.json 不存在。", file=sys.stderr)
        return 2
    result, err = extract.read_json_robust(result_path)
    if err:
        print(f"result.json 不可读: {err}", file=sys.stderr)
        return 2
    if args.status:
        result["status"] = args.status if args.status in ALLOWED_STATUS else result["status"]
    result["runtime"] = result.get("runtime") or {}
    result["runtime"]["finished_at"] = utcnow()
    result["runtime"]["steps"] = list(result.get("runtime", {}).get("steps", [])) + [{"step": "finalize", "at": utcnow()}]
    atomic_write_json(result_path, result)
    rebuild_report(result, out)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Academic compliance audit orchestrator")
    sub = parser.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="discover, extract evidence, write partial skeleton")
    p_run.add_argument("--input", default=str(DEFAULT_INPUT))
    p_run.add_argument("--output", default=str(DEFAULT_OUTPUT))
    p_run.add_argument("--work", default=str(DEFAULT_WORK))

    p_merge = sub.add_parser("merge", help="merge agent findings into result.json")
    p_merge.add_argument("--findings", required=True)
    p_merge.add_argument("--input", default=str(DEFAULT_INPUT))
    p_merge.add_argument("--output", default=str(DEFAULT_OUTPUT))
    p_merge.add_argument("--work", default=str(DEFAULT_WORK))
    p_merge.add_argument("--status", default=None, choices=sorted(ALLOWED_STATUS))

    p_fin = sub.add_parser("finalize", help="mark end and optionally set status")
    p_fin.add_argument("--output", default=str(DEFAULT_OUTPUT))
    p_fin.add_argument("--status", default=None, choices=sorted(ALLOWED_STATUS))

    args = parser.parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "merge":
        return cmd_merge(args)
    if args.cmd == "finalize":
        return cmd_finalize(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
