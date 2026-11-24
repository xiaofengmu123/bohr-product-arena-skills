# -*- coding: utf-8 -*-
"""core_impl.py — contract-first implementation for citation-evidence-audit.

This module is intentionally self-contained and deterministic:

* standard library only (no third-party dependencies);
* no randomness, no network access, no reliance on external services;
* stable sort orders and canonical JSON so that repeated runs of the same
  input produce the same findings, summary and change history (timestamps are
  the only intentionally varying fields and are excluded from the content
  hash used to decide whether a change_log entry is appended);
* every emitted result.json is validated against an executable contract before
  it is written, and any contract violation is recorded in ``errors`` while the
  file is still written so a partial deliverable is never lost.

The agent (LLM) is responsible for reading the source material and writing the
actual findings; the scripts only discover input, land an early valid skeleton,
merge findings, validate the contract and render the report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_VERSION = "1.0.0"
MAX_TEXT_BYTES = 2_000_000  # skip source files larger than this, record degradation

STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")
VERDICT_VALUES = ("pass", "issues_found", "needs_review", "unchecked", "insufficient")
SEVERITY_VALUES = ("critical", "major", "minor", "suggestion")
CATEGORY_VALUES = (
    "citation_support",
    "fact_source",
    "evidence_location",
    "numerical_statistical",
    "argumentation",
    "academic_norm",
    "method_design",
)
FINDING_REQUIRED = ("id", "category", "locator", "quote", "verdict", "severity", "suggestion")
FINDING_KEYS = FINDING_REQUIRED + (
    "finding_type", "claim", "basis", "source_ref", "confidence", "review_status",
)
TOP_REQUIRED = (
    "case_id", "status", "summary", "review_report", "evidence",
    "sources", "uncertainty", "pending_human", "limitations",
    "errors", "degradations", "change_log", "runtime",
)

TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml", ".tex", ".bib", ".rst"}
CONTROL_NAMES = {"case.json", "task.md"}


# --------------------------------------------------------------------------- #
# small, deterministic helpers
# --------------------------------------------------------------------------- #
def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_text(path: Path, limit: int = MAX_TEXT_BYTES) -> str:
    """Read a text file with BOM/encoding tolerance; bounded read."""
    size = path.stat().st_size if path.exists() else 0
    if size > limit:
        raise OSError(f"file too large: {size} bytes > {limit}")
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(read_text(path))
    except (OSError, json.JSONDecodeError, ValueError):
        return default


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


def canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(obj) -> str:
    """Stable hash of the meaningful content (timestamps/log excluded)."""
    clone = json.loads(json.dumps(obj))
    for key in ("runtime", "change_log"):
        clone.pop(key, None)
    return hashlib.sha256(canonical_json(clone).encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# contract: default shapes and empty values
# --------------------------------------------------------------------------- #
def empty_of(value):
    """Type-appropriate empty echo for an output_contract field."""
    if isinstance(value, dict):
        return {}
    if isinstance(value, list):
        return []
    if isinstance(value, str):
        return ""
    if isinstance(value, bool):
        return False
    return None


def empty_finding() -> dict:
    return {
        "id": "",
        "category": "",
        "finding_type": "suggestion",
        "severity": "suggestion",
        "verdict": "undeterminable",
        "locator": "",
        "quote": "",
        "claim": "",
        "basis": "",
        "source_ref": "not_verified",
        "suggestion": "",
        "confidence": "low",
        "review_status": "needs_review",
    }


def empty_result(case_id: str) -> dict:
    return {
        "case_id": case_id,
        "status": "partial",
        "summary": {
            "verdict": "unchecked",
            "counts": {
                "total": 0,
                "critical": 0,
                "major": 0,
                "minor": 0,
                "suggestion": 0,
                "by_category": {c: 0 for c in CATEGORY_VALUES},
            },
        },
        "review_report": {"findings": []},
        "evidence": {"evidence_map": []},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "runtime": {
            "started_at": utc_now(),
            "finished_at": None,
            "duration_seconds": None,
            "script_version": SCRIPT_VERSION,
        },
    }


def merge_output_contract(result: dict, contract) -> dict:
    """Echo every output_contract key into the top level without renaming or
    dropping unknown keys. Standard computed fields take priority; unknown or
    missing values fall back to null / [] / "" by value type."""
    if not isinstance(contract, dict):
        return result
    for key, val in contract.items():
        if key in result and result[key] not in (None, "", [], {}):
            continue
        result[key] = empty_of(val)
    return result


def validate_result(obj) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for a result object."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(obj, dict):
        return ["result is not a JSON object"], []
    for key in TOP_REQUIRED:
        if key not in obj:
            errors.append(f"missing top-level field: {key}")
    if "status" in obj and obj["status"] not in STATUS_VALUES:
        errors.append(f"invalid status: {obj['status']!r}")
    summary = obj.get("summary")
    if not isinstance(summary, dict):
        errors.append("summary must be an object")
    else:
        if summary.get("verdict") not in VERDICT_VALUES:
            errors.append(f"invalid summary.verdict: {summary.get('verdict')!r}")
        counts = summary.get("counts")
        if not isinstance(counts, dict):
            errors.append("summary.counts must be an object")
        else:
            for k in ("total", "critical", "major", "minor", "suggestion"):
                if not isinstance(counts.get(k), int):
                    errors.append(f"summary.counts.{k} must be an integer")
            bc = counts.get("by_category")
            if not isinstance(bc, dict):
                errors.append("summary.counts.by_category must be an object")
    report = obj.get("review_report")
    if not isinstance(report, dict) or not isinstance(report.get("findings"), list):
        errors.append("review_report.findings must be a list")
        report = {}
    findings = report.get("findings", [])
    for i, f in enumerate(findings):
        if not isinstance(f, dict):
            errors.append(f"finding[{i}] is not an object")
            continue
        for key in FINDING_REQUIRED:
            if key not in f or f[key] in (None, ""):
                errors.append(f"finding[{i}] missing required field: {key}")
        if f.get("category") not in CATEGORY_VALUES:
            errors.append(f"finding[{i}] invalid category: {f.get('category')!r}")
        if f.get("severity") not in SEVERITY_VALUES:
            errors.append(f"finding[{i}] invalid severity: {f.get('severity')!r}")
        if f.get("review_status", "confirmed") not in ("confirmed", "needs_review"):
            warnings.append(f"finding[{i}] review_status should be confirmed|needs_review")
    evidence = obj.get("evidence")
    if not isinstance(evidence, dict) or not isinstance(evidence.get("evidence_map"), list):
        errors.append("evidence.evidence_map must be a list")
    for key in ("sources", "uncertainty", "pending_human", "limitations", "errors", "degradations", "change_log"):
        if key in obj and not isinstance(obj[key], list):
            errors.append(f"{key} must be a list")
    if "runtime" in obj and not isinstance(obj.get("runtime"), dict):
        errors.append("runtime must be an object")
    if not findings:
        warnings.append("findings is empty (partial skeleton; review not yet performed)")
    return errors, warnings


# --------------------------------------------------------------------------- #
# input discovery and PDF extraction
# --------------------------------------------------------------------------- #
def derive_case_id(files) -> str:
    payload = "|".join(sorted(str(f) for f in files))
    return "case-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def read_case_meta(input_dir: Path) -> dict:
    case_path = input_dir / "case.json"
    if not case_path.exists():
        return {"case_id": None, "output_contract": None, "task_note": None}
    data = read_json(case_path, {})
    if not isinstance(data, dict):
        data = {}
    case_id = data.get("case_id") or data.get("id") or data.get("caseId")
    contract = data.get("output_contract")
    if not isinstance(contract, dict):
        contract = None
    task_note = data.get("task") or data.get("description") or data.get("task_note")
    return {"case_id": case_id, "output_contract": contract, "task_note": task_note}


def extract_pdf_text(src: Path, dest_txt: Path) -> tuple[bool, int, str]:
    """Extract page-marked text from a PDF via pdftotext if available.

    Returns (has_text, page_count, status). status is 'ok', 'empty_text' or a
    diagnostic such as 'pdftotext_missing'."""
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        return False, 0, "pdftotext_missing"
    try:
        cp = subprocess.run(
            [pdftotext, "-layout", str(src), "-"],
            capture_output=True, text=True, timeout=120,
        )
    except Exception as exc:  # noqa: BLE001 — diagnostic must never crash the run
        return False, 0, f"pdftotext_error:{type(exc).__name__}"
    if cp.returncode != 0:
        return False, 0, f"pdftotext_exit:{cp.returncode}"
    raw = cp.stdout or ""
    if raw.endswith("\x0c"):
        raw = raw[:-1]
    chunks = raw.split("\x0c")
    total = len(chunks)
    nonempty = sum(1 for c in chunks if c.strip())
    lines = []
    for i, chunk in enumerate(chunks, 1):
        body = chunk.strip()
        lines.append(f"----- PAGE {i} / {total} -----")
        lines.append(body if body else "[no extractable text]")
        lines.append("")
    dest_txt.parent.mkdir(parents=True, exist_ok=True)
    dest_txt.write_text("\n".join(lines), encoding="utf-8")
    if nonempty == 0:
        return False, total, "empty_text"
    return True, total, "ok"


def discover(input_dir: Path, work_dir: Path) -> dict:
    work_dir.mkdir(parents=True, exist_ok=True)
    texts_dir = work_dir / "texts"
    texts_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "input_dir": str(input_dir),
        "files": [],
        "texts": [],
        "degradations": [],
        "errors": [],
        "generated_at": utc_now(),
    }
    meta = read_case_meta(input_dir)
    manifest["case_id"] = meta["case_id"]
    manifest["output_contract"] = meta["output_contract"]

    if not input_dir.exists():
        manifest["errors"].append("input_dir_missing")
        return manifest

    entries = sorted([p for p in input_dir.iterdir() if p.is_file()], key=lambda p: p.name)
    manifest["files"] = [{"name": p.name, "path": str(p), "size": p.stat().st_size} for p in entries]

    for p in entries:
        ext = p.suffix.lower()
        if p.name in CONTROL_NAMES:
            continue
        if p.stat().st_size > MAX_TEXT_BYTES:
            manifest["degradations"].append({"file": p.name, "reason": "too_large", "size": p.stat().st_size})
            continue
        if ext == ".pdf":
            dest = texts_dir / (p.stem + ".txt")
            has_text, pages, status = extract_pdf_text(p, dest)
            record = {"source": p.name, "text": str(dest), "pages": pages, "status": status}
            manifest["texts"].append(record)
            if not has_text:
                manifest["degradations"].append(
                    {"file": p.name, "reason": "needs_ocr", "detail": status}
                )
        elif ext in TEXT_EXTENSIONS:
            try:
                text = read_text(p)
                dest = texts_dir / (p.stem + ".txt")
                dest.write_text(text, encoding="utf-8")
                manifest["texts"].append({"source": p.name, "text": str(dest), "pages": None, "status": "ok"})
            except OSError as exc:
                manifest["errors"].append({"file": p.name, "reason": str(exc)})
        else:
            manifest["degradations"].append({"file": p.name, "reason": "unsupported_extension", "ext": ext})

    manifest["case_id"] = meta["case_id"] or derive_case_id(entries)
    return manifest


# --------------------------------------------------------------------------- #
# scaffold / finalize / check / render
# --------------------------------------------------------------------------- #
def scaffold(input_dir: Path, output_dir: Path, work_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    # Protect in-progress findings: never clobber a result that already has any.
    existing = read_json(output_dir / "result.json")
    if isinstance(existing, dict) and existing.get("review_report", {}).get("findings"):
        print("scaffold skipped: result.json already contains findings; run finalize instead",
              file=sys.stderr)
        return existing
    meta = read_case_meta(input_dir)
    case_id = meta["case_id"] or derive_case_id(
        sorted([p for p in input_dir.iterdir() if p.is_file()], key=lambda p: p.name)
        if input_dir.exists() else []
    )
    result = empty_result(case_id)
    merge_output_contract(result, meta["output_contract"])

    # Early status: no input files at all -> insufficient_evidence, else partial.
    input_files = sorted([p for p in input_dir.iterdir() if p.is_file()], key=lambda p: p.name) if input_dir.exists() else []
    if not input_files:
        result["status"] = "insufficient_evidence"
        result["summary"]["verdict"] = "insufficient"
        result["limitations"].append("no input files under /input; cannot audit any material")
    else:
        result["status"] = "partial"
        result["summary"]["verdict"] = "unchecked"

    state = {
        "case_id": case_id,
        "output_contract_keys": sorted(meta["output_contract"].keys()) if isinstance(meta["output_contract"], dict) else [],
        "started_at": result["runtime"]["started_at"],
        "phase": "scaffolded",
        "last_content_hash": None,
        "last_finalized_at": None,
        "contract_warnings": [],
    }
    write_json(output_dir / "result.json", result)
    write_json(work_dir / "state.json", state)

    meta_path = work_dir / "meta.json"
    if not meta_path.exists():
        write_json(meta_path, {
            "overall_verdict": None,
            "status": None,
            "sources": [],
            "uncertainty": [],
            "pending_human": [],
            "limitations": [],
            "errors": [],
            "degradations": [],
        })
    return result


def recompute_summary(result: dict, findings: list) -> None:
    counts = {
        "total": len(findings),
        "critical": 0, "major": 0, "minor": 0, "suggestion": 0,
        "by_category": {c: 0 for c in CATEGORY_VALUES},
    }
    for f in findings:
        sev = f.get("severity")
        if sev in counts:
            counts[sev] += 1
        cat = f.get("category")
        if cat in counts["by_category"]:
            counts["by_category"][cat] += 1
    result["summary"]["counts"] = counts


def infer_verdict(findings: list) -> str:
    if not findings:
        return "unchecked"
    sevs = {f.get("severity") for f in findings}
    if "critical" in sevs or "major" in sevs or "minor" in sevs:
        return "issues_found"
    if any(f.get("review_status") == "needs_review" for f in findings):
        return "needs_review"
    return "pass"


def infer_status(meta_status, verdict, findings, input_files) -> str:
    if meta_status in STATUS_VALUES:
        return meta_status
    if not input_files and not findings:
        return "insufficient_evidence"
    if verdict in ("pass", "issues_found", "needs_review"):
        return "ok"
    return "partial"


def finalize(input_dir: Path, output_dir: Path, work_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "result.json"
    result = read_json(result_path)
    if not isinstance(result, dict):
        result = scaffold(input_dir, output_dir, work_dir)

    state = read_json(work_dir / "state.json", {})
    if not isinstance(state, dict):
        state = {}
    meta = read_json(work_dir / "meta.json", {})
    if not isinstance(meta, dict):
        meta = {}

    # Merge agent-written findings (one file per finding, sorted by id).
    findings_dir = work_dir / "findings"
    findings = []
    if findings_dir.exists():
        for p in sorted(findings_dir.glob("*.json"), key=lambda q: q.name):
            f = read_json(p)
            if isinstance(f, dict):
                findings.append(f)
    # de-duplicate by id, keep last file's version for a given id
    by_id = {}
    for f in findings:
        by_id[f.get("id") or ""] = f
    findings = [by_id[k] for k in sorted(by_id) if k]
    result["review_report"]["findings"] = findings

    # Echo output_contract again in case the agent replaced the file.
    meta_case = read_case_meta(input_dir)
    merge_output_contract(result, meta_case["output_contract"])

    # Reflect meta.json fields (agent-authored provenance) into the result.
    for key in ("sources", "uncertainty", "pending_human", "limitations", "errors", "degradations"):
        if isinstance(meta.get(key), list):
            result[key] = meta[key]

    # Rebuild evidence map from findings so every finding has a trace record.
    evidence_map = []
    for f in findings:
        evidence_map.append({
            "finding_id": f.get("id", ""),
            "locator": f.get("locator", ""),
            "quote": f.get("quote", ""),
            "basis": f.get("basis", ""),
            "source_ref": f.get("source_ref", "not_verified"),
        })
    result["evidence"]["evidence_map"] = evidence_map

    recompute_summary(result, findings)
    input_files = sorted([p for p in input_dir.iterdir() if p.is_file()], key=lambda p: p.name) if input_dir.exists() else []
    verdict = meta.get("overall_verdict")
    if verdict not in VERDICT_VALUES:
        if not input_files and not findings:
            verdict = "insufficient"
        else:
            verdict = infer_verdict(findings)
    result["summary"]["verdict"] = verdict
    result["status"] = infer_status(meta.get("status"), verdict, findings, input_files)

    # Contract validation: record hard errors, surface warnings via stdout.
    errors, warnings = validate_result(result)
    for e in errors:
        if e not in result["errors"]:
            result["errors"].append(f"contract: {e}")

    # Auditable change_log: append only when the meaningful content changed.
    h = content_hash(result)
    state.setdefault("last_content_hash", None)
    if h != state.get("last_content_hash"):
        result["change_log"].append({
            "ts": utc_now(),
            "phase": "finalize",
            "action": "merged_findings" if findings else "refreshed_skeleton",
            "finding_count": len(findings),
            "verdict": verdict,
            "content_hash": h,
        })
        state["last_content_hash"] = h

    # Runtime accounting (excluded from the content hash by design).
    started = result["runtime"].get("started_at")
    finished = utc_now()
    result["runtime"]["finished_at"] = finished
    if started:
        try:
            from datetime import datetime as _dt
            s = _dt.fromisoformat(started)
            f = _dt.fromisoformat(finished)
            result["runtime"]["duration_seconds"] = max(0, int((f - s).total_seconds()))
        except ValueError:
            result["runtime"]["duration_seconds"] = None

    state["phase"] = "finalized"
    state["last_finalized_at"] = finished
    state["contract_warnings"] = warnings
    write_json(work_dir / "state.json", state)
    write_json(result_path, result)

    report_path = output_dir / "report.md"
    report_path.write_text(render_report(result), encoding="utf-8")

    print(json.dumps({
        "case_id": result["case_id"],
        "status": result["status"],
        "verdict": result["summary"]["verdict"],
        "findings": len(findings),
        "contract_errors": len(errors),
        "contract_warnings": len(warnings),
        "change_log_entries": len(result["change_log"]),
        "result": str(result_path),
        "report": str(report_path),
    }, ensure_ascii=False))
    if warnings:
        for w in warnings:
            print(f"WARNING: {w}", file=sys.stderr)
    return result


def check(output_dir: Path) -> int:
    result_path = output_dir / "result.json"
    if not result_path.exists():
        print(json.dumps({"ok": False, "errors": ["result.json missing"]}, ensure_ascii=False))
        return 1
    try:
        obj = json.loads(read_text(result_path))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"ok": False, "errors": [f"result.json unreadable: {exc}"]}, ensure_ascii=False))
        return 1
    errors, warnings = validate_result(obj)
    print(json.dumps({"ok": not errors, "errors": errors, "warnings": warnings}, ensure_ascii=False))
    return 0 if not errors else 1


def render_report(result: dict) -> str:
    lines = [
        "# 引用与事实核验审查报告",
        "",
        f"- case_id: `{result.get('case_id', '')}`",
        f"- status: `{result.get('status', '')}`",
        f"- verdict: `{result.get('summary', {}).get('verdict', '')}`",
        "",
    ]
    counts = result.get("summary", {}).get("counts", {})
    lines.append("## 统计")
    lines.append("")
    lines.append(f"- total: {counts.get('total', 0)}；critical: {counts.get('critical', 0)}；"
                 f"major: {counts.get('major', 0)}；minor: {counts.get('minor', 0)}；"
                 f"suggestion: {counts.get('suggestion', 0)}")
    lines.append("")
    findings = result.get("review_report", {}).get("findings", [])
    lines.append("## Findings")
    lines.append("")
    if not findings:
        lines.append("（尚无 findings；本报告为 partial 骨架。）")
    for f in findings:
        lines.append(f"### {f.get('id', '?')} — {f.get('category', '?')} / {f.get('severity', '?')}")
        lines.append("")
        lines.append(f"- 判定：`{f.get('verdict', '')}`（`{f.get('finding_type', '')}`）")
        lines.append(f"- 定位：`{f.get('locator', '')}`")
        lines.append(f"- 摘录：{f.get('quote', '')}")
        if f.get("claim"):
            lines.append(f"- 转述断言：{f.get('claim', '')}")
        if f.get("basis"):
            lines.append(f"- 依据：{f.get('basis', '')}")
        lines.append(f"- 来源：`{f.get('source_ref', 'not_verified')}`")
        lines.append(f"- 建议：{f.get('suggestion', '')}")
        lines.append(f"- 置信度/复核状态：`{f.get('confidence', 'low')}` / `{f.get('review_status', 'needs_review')}`")
        lines.append("")
    for section, title in (
        ("sources", "Sources"),
        ("uncertainty", "Uncertainty"),
        ("pending_human", "Pending human review"),
        ("limitations", "Limitations"),
        ("errors", "Errors"),
        ("degradations", "Degradations"),
    ):
        items = result.get(section, [])
        lines.append(f"## {title}")
        lines.append("")
        if not items:
            lines.append("（无）")
        else:
            for it in items:
                lines.append(f"- {json.dumps(it, ensure_ascii=False)}")
        lines.append("")
    lines.append("## Change log")
    lines.append("")
    log = result.get("change_log", [])
    if not log:
        lines.append("（无）")
    else:
        for it in log:
            lines.append(f"- `{it.get('ts', '')}` {it.get('phase', '')} {it.get('action', '')} "
                         f"hash={it.get('content_hash', '')} findings={it.get('finding_count', 0)}")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    # Shared options use SUPPRESS so they may appear before OR after the
    # subcommand without one default clobbering an explicitly given value.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", dest="input", default=argparse.SUPPRESS,
                        help="input directory (default /input)")
    common.add_argument("--output", dest="output", default=argparse.SUPPRESS,
                        help="output directory (default /app/output)")
    common.add_argument("--work", dest="work", default=argparse.SUPPRESS,
                        help="work directory (default /app/work)")

    p = argparse.ArgumentParser(
        prog="main.py",
        description="Contract-first citation/fact audit runner.",
        parents=[common],
    )
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("discover", help="scan input and extract text", parents=[common])
    sub.add_parser("scaffold", help="land an early valid result.json skeleton", parents=[common])
    sub.add_parser("finalize", help="merge findings, validate and render report", parents=[common])
    sub.add_parser("check", help="validate result.json against the contract (exit 1 on errors)", parents=[common])
    sub.add_parser("run", help="discover + scaffold + finalize in one pass", parents=[common])
    return p


def dispatch(args) -> int:
    input_dir = Path(getattr(args, "input", "/input"))
    output_dir = Path(getattr(args, "output", "/app/output"))
    work_dir = Path(getattr(args, "work", "/app/work"))
    cmd = args.cmd or "run"
    if cmd == "discover":
        manifest = discover(input_dir, work_dir)
        write_json(work_dir / "manifest.json", manifest)
        print(json.dumps(manifest, ensure_ascii=False))
        return 0
    if cmd == "scaffold":
        scaffold(input_dir, output_dir, work_dir)
        print(f"scaffolded -> {output_dir / 'result.json'}")
        return 0
    if cmd == "finalize":
        finalize(input_dir, output_dir, work_dir)
        return 0
    if cmd == "check":
        return check(output_dir)
    # default: run
    manifest = discover(input_dir, work_dir)
    write_json(work_dir / "manifest.json", manifest)
    scaffold(input_dir, output_dir, work_dir)
    finalize(input_dir, output_dir, work_dir)
    return 0
