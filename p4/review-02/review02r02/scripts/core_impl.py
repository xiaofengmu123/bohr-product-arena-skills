# -*- coding: utf-8 -*-
"""core_impl.py — deterministic toolchain for the citation/fact audit Skill.

Everything here is Python standard library only. The toolchain does the
mechanical, reproducible part of the audit: discover inputs, extract PDF text
with page markers, commit an early partial result.json skeleton, merge
per-finding JSON files, render a human-readable report.md, and self-check the
output contract. The scientific judgment itself is performed by the Agent
following SKILL.md; these scripts never invent findings, numbers, sources or
verdicts.

Default paths target the official harness: /input, /app/output, /app/work.
All functions are deterministic for identical inputs (no randomness, no
network, no wall-clock dependence in the content that matters).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

# Canonical six review categories required by the task (事实核验/引用支持/
# 方法设计/统计报告/论证链/学术规范).
CANONICAL_CATEGORIES = (
    "citation_support",
    "fact_source",
    "method_design",
    "numerical_statistical",
    "argumentation",
    "academic_norm",
)

SEVERITIES = ("critical", "major", "minor", "suggestion")
CITATION_VERDICTS = (
    "fully_supported",
    "partially_supported",
    "unsupported",
    "undeterminable",
)
STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
SUMMARY_VERDICTS = ("pass", "issues_found", "needs_review", "unchecked", "insufficient")

# Seven keys that every finding must carry (mirrors references/result_schema.md).
FINDING_REQUIRED = ("id", "category", "locator", "quote", "verdict", "severity", "suggestion")

TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl",
    ".tex", ".bib", ".xml", ".yaml", ".yml", ".html", ".htm", ".rst",
}
PDF_EXTS = {".pdf"}
MAX_TEXT_BYTES = 2_000_000  # skip oversized files, never read them whole

# Phase budgets. These numbers encode the "time budget + early commit" design:
# phase 0 must commit a legal partial skeleton within the first 3 minutes.
PHASE_BUDGETS = (
    {
        "phase": 0,
        "label": "discover + scaffold",
        "seconds": 180,
        "requests": 8,
        "commit": "legal result.json skeleton + state.json",
        "hard_rule": "commit partial result before this phase ends",
    },
    {
        "phase": 1,
        "label": "read + record findings",
        "seconds": 300,
        "requests": 20,
        "commit": "one finding file per issue under /app/work/findings/",
    },
    {
        "phase": 2,
        "label": "review + cross-check",
        "seconds": 300,
        "requests": 25,
        "commit": "verified quotes/locators + meta.json",
    },
    {
        "phase": 3,
        "label": "finalize + selftest",
        "seconds": 120,
        "requests": 11,
        "commit": "final result.json + report.md + passing selftest",
    },
)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def now_iso():
    """UTC timestamp in ISO-8601. Used only for bookkeeping metadata."""
    try:
        return _dt.datetime.now(_dt.timezone.utc).isoformat()
    except Exception:  # pragma: no cover - very old Python fallback
        return _dt.datetime.utcnow().isoformat() + "Z"


def package_root():
    """Absolute path of the Skill package (parent of scripts/)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def write_json(path, obj):
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def read_json(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def read_json_or_none(path):
    try:
        return read_json(path), None
    except FileNotFoundError:
        return None, "not found"
    except Exception as exc:  # bad JSON / bad encoding
        return None, "unreadable: %s" % exc


def read_text(path, max_bytes=MAX_TEXT_BYTES):
    """Read UTF-8 text with BOM tolerance; returns (text, error)."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(max_bytes + 1)
    except OSError as exc:
        return None, "unreadable: %s" % exc
    if len(raw) > max_bytes:
        return None, "skipped: file larger than %d bytes" % max_bytes
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(encoding), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "decoded with replacement characters"


def norm_rel(path, base):
    try:
        return os.path.relpath(path, base).replace(os.sep, "/")
    except ValueError:
        return os.path.abspath(path)


def find_matching_case_id(case_data, task_text):
    """Extract a stable case identifier without guessing the file schema."""
    if isinstance(case_data, dict):
        for key in ("case_id", "id", "challenge_id", "caseId", "caseID"):
            value = case_data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if task_text:
        m = re.search(r"challenge_id[:：]\s*[`'\"]?([A-Za-z0-9._-]+)", task_text)
        if m:
            return m.group(1)
        m = re.search(r"case_id[:：]\s*[`'\"]?([A-Za-z0-9._-]+)", task_text)
        if m:
            return m.group(1)
    return "unknown"


def build_contract_echo(case_data):
    """Echo every output_contract / input_contract field verbatim.

    Returns (echo_dict, raw_contract) where echo_dict is flattened to top level
    and raw_contract keeps the untouched original for traceability. Missing
    values are preserved as null/[]/"" rather than invented; unknown keys are
    never renamed or dropped.
    """
    echo = {}
    raw = None
    if not isinstance(case_data, dict):
        return echo, raw
    for key in ("output_contract", "input_contract"):
        value = case_data.get(key)
        if isinstance(value, dict):
            raw = raw if raw is not None else {}
            raw[key] = value
            for k, v in value.items():
                echo[k] = v
        elif isinstance(value, list):
            # list-of-field-names form: reserve the keys with null values.
            raw = raw if raw is not None else {}
            raw[key] = value
            for k in value:
                if isinstance(k, str) and k not in echo:
                    echo[k] = None
    return echo, raw


# --------------------------------------------------------------------------- #
# discover
# --------------------------------------------------------------------------- #
def discover(input_dir, work_dir):
    """Scan input, extract PDF text with page markers, write manifest.json."""
    ensure_dir(work_dir)
    errors = []
    degradations = []
    files = []
    materials = []
    case_file = None
    task_file = None
    case_data = None
    task_text = None

    if not os.path.isdir(input_dir):
        errors.append("input directory not found: %s" % input_dir)
    else:
        for root, _dirs, names in os.walk(input_dir):
            for name in sorted(names):
                full = os.path.join(root, name)
                rel = norm_rel(full, input_dir)
                ext = os.path.splitext(name)[1].lower()
                kind = None
                if name == "case.json":
                    kind = "case"
                elif name == "task.md":
                    kind = "task"
                elif ext in PDF_EXTS:
                    kind = "pdf"
                elif ext in TEXT_EXTS:
                    kind = "text"
                else:
                    kind = "other"
                entry = {"path": rel, "abs": full, "kind": kind, "ext": ext,
                         "size": os.path.getsize(full)}
                files.append(entry)
                if kind == "case":
                    case_file = rel
                elif kind == "task":
                    task_file = rel
                elif kind in ("pdf", "text"):
                    materials.append(entry)

    # Read case.json and task.md for identifiers and degradation recording.
    if case_file:
        text, err = read_text(os.path.join(input_dir, case_file))
        if err:
            degradations.append({"file": case_file, "issue": err})
        else:
            try:
                case_data = json.loads(text)
            except Exception as exc:
                degradations.append({"file": case_file, "issue": "bad JSON: %s" % exc})
    if task_file:
        text, err = read_text(os.path.join(input_dir, task_file))
        if err:
            degradations.append({"file": task_file, "issue": err})
        else:
            task_text = text

    # Extract PDF text with page markers.
    texts_dir = ensure_dir(os.path.join(work_dir, "texts"))
    for mat in materials:
        if mat["kind"] != "pdf":
            continue
        extracted, note = extract_pdf(mat["abs"], texts_dir)
        mat["extracted"] = norm_rel(extracted, work_dir) if extracted else None
        mat["pages"] = note.get("pages") if note else None
        if note is None:
            continue
        if note.get("needs_ocr"):
            mat["needs_ocr"] = True
            degradations.append({"file": mat["path"], "issue": note.get("error", "OCR required")})
        elif note.get("error"):
            degradations.append({"file": mat["path"], "issue": note["error"]})
        elif note.get("empty"):
            mat["needs_ocr"] = True
            degradations.append({"file": mat["path"], "issue": "extracted empty text, likely scanned"})

    manifest = {
        "case_id": find_matching_case_id(case_data, task_text),
        "input_dir": input_dir,
        "case_file": case_file,
        "task_file": task_file,
        "files": files,
        "materials": materials,
        "errors": errors,
        "degradations": degradations,
        "generated_at": now_iso(),
    }
    write_json(os.path.join(work_dir, "manifest.json"), manifest)
    print(json.dumps({
        "case_id": manifest["case_id"],
        "files": len(files),
        "materials": len(materials),
        "degradations": len(degradations),
        "errors": len(errors),
    }, ensure_ascii=False))
    return 0


def extract_pdf(pdf_path, texts_dir):
    """Extract PDF to marked UTF-8 text; returns (path, note)."""
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    out_path = os.path.join(texts_dir, base + ".txt")
    cmd = ["pdftotext", "-layout", pdf_path, "-"]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
    except FileNotFoundError:
        return None, {"needs_ocr": True, "error": "pdftotext not found; use an OCR path"}
    except subprocess.TimeoutExpired:
        return None, {"needs_ocr": False, "error": "pdftotext timed out after 180s"}
    except OSError as exc:
        return None, {"needs_ocr": False, "error": "pdftotext failed: %s" % exc}
    if proc.returncode != 0:
        return None, {"needs_ocr": False,
                      "error": "pdftotext rc=%d: %s" % (
                          proc.returncode, proc.stderr.decode("utf-8", "replace")[:500])}
    text = proc.stdout.decode("utf-8", errors="replace")
    pages = text.split("\f")
    while pages and pages[-1].strip() == "":
        pages.pop()
    if not pages or all(not p.strip() for p in pages):
        return None, {"empty": True, "pages": len(pages)}
    marked = []
    for i, page in enumerate(pages, 1):
        marked.append("----- PAGE %d / %d -----" % (i, len(pages)))
        marked.append(page.rstrip("\n"))
    final = "\n".join(marked)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(final)
    return out_path, {"pages": len(pages), "chars": len(final)}


# --------------------------------------------------------------------------- #
# scaffold
# --------------------------------------------------------------------------- #
def scaffold(input_dir, output_dir, work_dir):
    """Commit a legal partial result.json skeleton + state.json immediately."""
    ensure_dir(output_dir)
    ensure_dir(work_dir)
    ensure_dir(os.path.join(work_dir, "findings"))

    manifest, _ = read_json_or_none(os.path.join(work_dir, "manifest.json"))
    manifest = manifest or {}

    case_data = None
    case_err = None
    case_path = os.path.join(input_dir, "case.json")
    if os.path.isfile(case_path):
        text, err = read_text(case_path)
        if err:
            case_err = err
        else:
            try:
                case_data = json.loads(text)
            except Exception as exc:
                case_err = "bad JSON: %s" % exc

    task_text = None
    task_path = os.path.join(input_dir, "task.md")
    if os.path.isfile(task_path):
        text, _ = read_text(task_path)
        task_text = text

    case_id = find_matching_case_id(case_data, task_text)
    if manifest.get("case_id") and manifest["case_id"] != "unknown":
        case_id = manifest["case_id"]

    echo, raw_contract = build_contract_echo(case_data)

    status = "input_error" if case_err else "partial"
    result = {}
    result.update(echo)  # contract fields first; standard fields override below
    result["case_id"] = case_id
    result["status"] = status
    result["summary"] = {
        "verdict": "unchecked",
        "counts": {
            "total": 0,
            "critical": 0,
            "major": 0,
            "minor": 0,
            "suggestion": 0,
            "by_category": {c: 0 for c in CANONICAL_CATEGORIES},
        },
    }
    result["review_report"] = {
        "findings": [],
        "coverage": {c: "unchecked" for c in CANONICAL_CATEGORIES},
        "sections_covered": [],
    }
    result["evidence"] = {"evidence_map": {}}
    result["sources"] = []
    result["uncertainty"] = []
    result["pending_human"] = []
    result["limitations"] = []
    result["errors"] = []
    result["degradations"] = list((manifest.get("degradations") or [])[:])
    result["change_log"] = []
    result["runtime"] = {
        "started_at": now_iso(),
        "requests_used": 0,
        "budget": {
            "total_seconds": 900,
            "total_requests": 64,
            "phase_budgets": [dict(p) for p in PHASE_BUDGETS],
        },
    }
    if raw_contract is not None:
        result["output_contract"] = raw_contract  # untouched copy for audit
    if case_err:
        result["errors"].append({"file": "case.json", "issue": case_err})

    state = {
        "phase": 0,
        "requests_used": 0,
        "finding_count": 0,
        "coverage": {c: "unchecked" for c in CANONICAL_CATEGORIES},
        "checkpoints": [
            {"phase": 0, "at": now_iso(), "requests_used": 0,
             "note": "skeleton committed by scaffold"}
        ],
        "last_action": "scaffold",
        "updated_at": now_iso(),
    }

    write_json(os.path.join(output_dir, "result.json"), result)
    write_json(os.path.join(work_dir, "state.json"), state)
    print(json.dumps({
        "case_id": case_id,
        "status": status,
        "contract_fields": sorted(echo.keys()),
        "skeleton_written": os.path.join(output_dir, "result.json"),
    }, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- #
# checkpoint — the budget ledger
# --------------------------------------------------------------------------- #
def count_findings(work_dir):
    find_dir = os.path.join(work_dir, "findings")
    if not os.path.isdir(find_dir):
        return 0
    count = 0
    for name in os.listdir(find_dir):
        if name.endswith(".json"):
            count += 1
    return count


def checkpoint(input_dir, output_dir, work_dir, phase, requests_used, note):
    """Record a phase transition and request budget in state.json.

    This is the concrete 'budget ledger' that keeps the 900s/64-request plan
    auditable instead of a prose promise.
    """
    state_path = os.path.join(work_dir, "state.json")
    state, err = read_json_or_none(state_path)
    if state is None:
        state = {"phase": 0, "requests_used": 0, "checkpoints": [],
                 "finding_count": 0, "coverage": {}}
    if not isinstance(state, dict):
        state = {"phase": 0, "requests_used": 0, "checkpoints": []}
    state["phase"] = phase
    if requests_used is not None:
        state["requests_used"] = requests_used
    state.setdefault("checkpoints", []).append({
        "phase": phase,
        "at": now_iso(),
        "requests_used": state.get("requests_used", 0),
        "note": note or "",
    })
    state["finding_count"] = count_findings(work_dir)
    state["last_action"] = "checkpoint"
    state["updated_at"] = now_iso()
    write_json(state_path, state)

    # Keep the runtime ledger in result.json in sync when it already exists.
    result_path = os.path.join(output_dir, "result.json")
    result, _err = read_json_or_none(result_path)
    if isinstance(result, dict):
        runtime = result.setdefault("runtime", {})
        runtime["requests_used"] = state["requests_used"]
        runtime["phase"] = phase
        write_json(result_path, result)

    print(json.dumps({
        "phase": phase,
        "requests_used": state["requests_used"],
        "finding_count": state["finding_count"],
    }, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- #
# finalize
# --------------------------------------------------------------------------- #
def load_meta(work_dir):
    meta, _ = read_json_or_none(os.path.join(work_dir, "meta.json"))
    return meta if isinstance(meta, dict) else {}


def load_findings(work_dir):
    """Load and validate per-finding JSON files, deterministic order."""
    find_dir = os.path.join(work_dir, "findings")
    findings = []
    errors = []
    if not os.path.isdir(find_dir):
        return findings, errors
    for name in sorted(os.listdir(find_dir)):
        if not name.endswith(".json"):
            continue
        obj, err = read_json_or_none(os.path.join(find_dir, name))
        if obj is None:
            errors.append({"finding_file": name, "issue": err or "unreadable"})
            continue
        if not isinstance(obj, dict):
            errors.append({"finding_file": name, "issue": "not a JSON object"})
            continue
        missing = [k for k in FINDING_REQUIRED
                   if not isinstance(obj.get(k), str) or not obj[k].strip()]
        if missing:
            errors.append({"finding_file": name, "issue": "missing required keys: %s" % missing})
            continue
        obj.setdefault("finding_type", None)
        obj.setdefault("claim", "")
        obj.setdefault("basis", "")
        obj.setdefault("source_ref", "not_verified")
        obj.setdefault("confidence", None)
        obj.setdefault("review_status", "recorded")
        findings.append(obj)
    # stable ordering: by finding id (fall back to filename).
    findings.sort(key=lambda f: (str(f.get("id", "")), ))
    return findings, errors


def compute_summary(findings, coverage):
    counts = {
        "total": len(findings),
        "critical": 0,
        "major": 0,
        "minor": 0,
        "suggestion": 0,
        "by_category": {c: 0 for c in CANONICAL_CATEGORIES},
    }
    for f in findings:
        sev = f.get("severity")
        if sev in counts:
            counts[sev] += 1
        cat = f.get("category")
        if cat in counts["by_category"]:
            counts["by_category"][cat] += 1

    has_material_issue = any(
        f.get("severity") in ("critical", "major", "minor") for f in findings)
    has_any_finding = bool(findings)
    any_unchecked = any(v == "unchecked" for v in coverage.values()) if coverage else True

    if has_material_issue:
        verdict = "issues_found"
    elif has_any_finding:
        verdict = "needs_review"  # only suggestions present
    elif any_unchecked:
        verdict = "needs_review"
    else:
        verdict = "pass"
    return counts, verdict


def finalize(input_dir, output_dir, work_dir):
    """Rebuild result.json and report.md from the deterministic source of truth.

    The result is reconstructed from case.json + findings/*.json + meta.json +
    state.json, so running finalize repeatedly yields identical core content.
    """
    ensure_dir(output_dir)
    ensure_dir(work_dir)

    manifest, _ = read_json_or_none(os.path.join(work_dir, "manifest.json"))
    manifest = manifest or {}
    state, _ = read_json_or_none(os.path.join(work_dir, "state.json"))
    state = state if isinstance(state, dict) else {}
    meta = load_meta(work_dir)

    case_data = None
    case_err = None
    case_path = os.path.join(input_dir, "case.json")
    if os.path.isfile(case_path):
        text, err = read_text(case_path)
        if err:
            case_err = err
        else:
            try:
                case_data = json.loads(text)
            except Exception as exc:
                case_err = "bad JSON: %s" % exc
    task_text = None
    if os.path.isfile(os.path.join(input_dir, "task.md")):
        task_text, _ = read_text(os.path.join(input_dir, "task.md"))

    case_id = find_matching_case_id(case_data, task_text)
    if manifest.get("case_id") and manifest["case_id"] != "unknown":
        case_id = manifest["case_id"]

    echo, raw_contract = build_contract_echo(case_data)
    findings, finding_errors = load_findings(work_dir)

    coverage = {c: "unchecked" for c in CANONICAL_CATEGORIES}
    for c in CANONICAL_CATEGORIES:
        if any(f.get("category") == c for f in findings):
            coverage[c] = "checked"

    counts, auto_verdict = compute_summary(findings, coverage)
    verdict = meta.get("overall_verdict", auto_verdict)

    # Status: honest about completion and material availability.
    materials = manifest.get("materials") or []
    if not materials and not case_err:
        status = "insufficient_evidence"
        if "overall_verdict" not in meta:
            verdict = "insufficient"
    elif case_err:
        status = "input_error"
    elif any(v == "unchecked" for v in coverage.values()):
        status = "partial"
    else:
        status = "ok"
    if meta.get("status") in STATUSES:
        status = meta["status"]

    result = {}
    result.update(echo)
    result["case_id"] = case_id
    result["status"] = status
    result["summary"] = {"verdict": verdict, "counts": counts}
    sections_covered = meta.get("sections_covered")
    if not isinstance(sections_covered, list):
        sections_covered = []
    result["review_report"] = {
        "findings": findings,
        "coverage": coverage,
        "sections_covered": sections_covered,
    }
    result["evidence"] = {
        "evidence_map": {
            f["id"]: {
                "locator": f.get("locator"),
                "quote": f.get("quote"),
                "basis": f.get("basis", ""),
                "source_ref": f.get("source_ref", "not_verified"),
            }
            for f in findings
        }
    }
    result["sources"] = meta.get("sources", [])
    result["uncertainty"] = meta.get("uncertainty", [])
    result["pending_human"] = meta.get("pending_human", [])
    result["limitations"] = meta.get("limitations", [])
    result["errors"] = list(meta.get("errors", [])) + finding_errors
    if case_err:
        result["errors"].append({"file": "case.json", "issue": case_err})
    result["degradations"] = list(manifest.get("degradations") or []) + list(meta.get("degradations", []))
    result["change_log"] = list(state.get("checkpoints", []))[:]
    result["runtime"] = {
        "started_at": state.get("checkpoints", [{}])[0].get("at", now_iso()),
        "finished_at": now_iso(),
        "requests_used": state.get("requests_used", 0),
        "phase": state.get("phase", 0),
        "budget": {
            "total_seconds": 900,
            "total_requests": 64,
            "phase_budgets": [dict(p) for p in PHASE_BUDGETS],
        },
    }
    if raw_contract is not None:
        result["output_contract"] = raw_contract

    write_json(os.path.join(output_dir, "result.json"), result)
    write_report(os.path.join(output_dir, "report.md"), result)
    print(json.dumps({
        "case_id": case_id,
        "status": status,
        "verdict": verdict,
        "total_findings": counts["total"],
        "finding_errors": len(finding_errors),
    }, ensure_ascii=False))
    return 0


def write_report(path, result):
    lines = []
    lines.append("# 引用与事实核验报告")
    lines.append("")
    lines.append("- case_id: `%s`" % result.get("case_id", "unknown"))
    lines.append("- status: `%s`" % result.get("status"))
    summary = result.get("summary", {})
    lines.append("- verdict: `%s`" % summary.get("verdict"))
    counts = summary.get("counts", {})
    lines.append("- 结论条数: %d（critical %d / major %d / minor %d / suggestion %d）" % (
        counts.get("total", 0), counts.get("critical", 0), counts.get("major", 0),
        counts.get("minor", 0), counts.get("suggestion", 0)))
    lines.append("")
    lines.append("## 覆盖矩阵")
    lines.append("")
    lines.append("| 类别 | 状态 |")
    lines.append("|---|---|")
    coverage = result.get("review_report", {}).get("coverage", {})
    for cat in CANONICAL_CATEGORIES:
        lines.append("| %s | %s |" % (cat, coverage.get(cat, "unchecked")))
    lines.append("")
    lines.append("## 问题清单")
    lines.append("")
    findings = result.get("review_report", {}).get("findings", [])
    if not findings:
        lines.append("（暂无已落盘结论。）")
        lines.append("")
    current_cat = None
    for f in findings:
        cat = f.get("category", "other")
        if cat != current_cat:
            current_cat = cat
            lines.append("### %s" % cat)
            lines.append("")
        lines.append("#### %s `[%s]`" % (f.get("id", "?"), f.get("severity", "?")))
        lines.append("")
        lines.append("- **判定**: %s（%s）" % (f.get("verdict", "?"), f.get("finding_type", "")))
        lines.append("- **定位**: %s" % f.get("locator", ""))
        lines.append("- **逐字摘录**: %s" % f.get("quote", ""))
        if f.get("claim"):
            lines.append("- **转述断言**: %s" % f["claim"])
        if f.get("basis"):
            lines.append("- **判定依据**: %s" % f["basis"])
        lines.append("- **来源**: %s" % f.get("source_ref", "not_verified"))
        lines.append("- **建议**: %s" % f.get("suggestion", ""))
        lines.append("")
    if result.get("uncertainty"):
        lines.append("## 不确定性")
        lines.append("")
        for item in result["uncertainty"]:
            lines.append("- %s" % (item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)))
        lines.append("")
    if result.get("limitations"):
        lines.append("## 局限")
        lines.append("")
        for item in result["limitations"]:
            lines.append("- %s" % (item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)))
        lines.append("")
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


# --------------------------------------------------------------------------- #
# selftest
# --------------------------------------------------------------------------- #
def selftest(input_dir, output_dir, work_dir):
    """Run the self-check battery; returns True when every check passes."""
    pkg = package_root()
    checks = []

    def add(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    # 1. Package-level checks (no output needed).
    skill_md = os.path.join(pkg, "SKILL.md")
    if os.path.isfile(skill_md):
        text, _ = read_text(skill_md)
        front = re.match(r"^---\s*\n(.*?)\n---", text or "", re.S)
        has_name = bool(front and re.search(r"^name:\s*\S+", front.group(1), re.M))
        has_desc = bool(front and re.search(r"^description:\s*\S+", front.group(1), re.M))
        has_ver = bool(front and re.search(r"^version:\s*\S+", front.group(1), re.M))
        add("SKILL.md frontmatter (name/description/version)",
            has_name and has_desc and has_ver,
            "name=%s desc=%s version=%s" % (has_name, has_desc, has_ver))
    else:
        add("SKILL.md frontmatter (name/description/version)", False, "SKILL.md not found")

    main_py = os.path.join(pkg, "scripts", "main.py")
    if os.path.isfile(main_py):
        rc = subprocess.run(
            [sys.executable, "-B", main_py, "--help"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        add("scripts/main.py --help exits 0", rc == 0, "rc=%d" % rc)
    else:
        add("scripts/main.py --help exits 0", False, "main.py not found")

    # 2. Ensure a result exists, then check it.
    finalize(input_dir, output_dir, work_dir)
    result_path = os.path.join(output_dir, "result.json")
    result, err = read_json_or_none(result_path)
    if result is None:
        add("result.json parses", False, err or "missing")
        _print_checks(checks)
        return all(c["ok"] for c in checks)
    add("result.json parses", True)

    add("result.json has case_id", bool(isinstance(result.get("case_id"), str) and result["case_id"]))
    add("result.json has valid status", result.get("status") in STATUSES,
        "status=%s" % result.get("status"))

    # 3. Contract echo completeness.
    case_path = os.path.join(input_dir, "case.json")
    if os.path.isfile(case_path):
        case_data, _ = read_json_or_none(case_path)
        echo, _raw = build_contract_echo(case_data)
        missing_contract = [k for k in echo if k not in result]
        add("output_contract fields echoed", not missing_contract,
            "missing=%s" % (missing_contract or "none"))
    else:
        add("output_contract fields echoed", True, "no case.json to compare")

    # 4. Coverage + findings.
    coverage = result.get("review_report", {}).get("coverage", {})
    add("coverage covers six categories",
        all(c in coverage for c in CANONICAL_CATEGORIES),
        "present=%s" % sorted(coverage.keys()))

    findings = result.get("review_report", {}).get("findings", [])
    bad = []
    for f in findings:
        missing = [k for k in FINDING_REQUIRED
                   if not isinstance(f.get(k), str) or not f[k].strip()]
        if missing:
            bad.append((f.get("id", "?"), missing))
    add("every finding has seven required keys", not bad, "bad=%s" % (bad or "none"))

    empty_loc = [f.get("id") for f in findings if not str(f.get("locator", "")).strip()]
    add("every finding has non-empty locator", not empty_loc, "bad=%s" % (empty_loc or "none"))

    # 5. Determinism: finalize twice, compare core content.
    snapshot_a = _core_snapshot(result)
    finalize(input_dir, output_dir, work_dir)
    result2, err2 = read_json_or_none(result_path)
    snapshot_b = _core_snapshot(result2) if result2 else None
    add("finalize is deterministic (summary+findings)", snapshot_a == snapshot_b)

    _print_checks(checks)
    return all(c["ok"] for c in checks)


def _core_snapshot(result):
    if not isinstance(result, dict):
        return None
    return {
        "summary": result.get("summary"),
        "review_report": result.get("review_report"),
        "case_id": result.get("case_id"),
    }


def _print_checks(checks):
    print("selftest results:")
    ok_count = 0
    for c in checks:
        mark = "PASS" if c["ok"] else "FAIL"
        ok_count += 1 if c["ok"] else 0
        detail = (" — " + c["detail"]) if c["detail"] else ""
        print("  [%s] %s%s" % (mark, c["name"], detail))
    print("summary: %d/%d passed" % (ok_count, len(checks)))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Citation/fact audit toolchain (discover, scaffold, "
                    "checkpoint, finalize, selftest).",
    )
    p.add_argument("--input", default=DEFAULT_INPUT, help="input dir (default /input)")
    p.add_argument("--output", default=DEFAULT_OUTPUT, help="output dir (default /app/output)")
    p.add_argument("--work", default=DEFAULT_WORK, help="work dir (default /app/work)")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("discover", help="scan input, extract PDF text, write manifest.json")
    sub.add_parser("scaffold", help="commit legal partial result.json + state.json")
    cp = sub.add_parser("checkpoint", help="record phase/request budget in the ledger")
    cp.add_argument("--phase", type=int, required=True, choices=[0, 1, 2, 3])
    cp.add_argument("--requests-used", type=int, default=None)
    cp.add_argument("--note", default="")
    sub.add_parser("finalize", help="merge findings and render result.json + report.md")
    sub.add_parser("selftest", help="run the self-check battery")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    cmd = args.command
    if cmd == "discover":
        return discover(args.input, args.work)
    if cmd == "scaffold":
        return scaffold(args.input, args.output, args.work)
    if cmd == "checkpoint":
        return checkpoint(args.input, args.output, args.work,
                          args.phase, args.requests_used, args.note)
    if cmd == "finalize":
        return finalize(args.input, args.output, args.work)
    if cmd == "selftest":
        ok = selftest(args.input, args.output, args.work)
        return 0 if ok else 1
    print("unknown command: %s" % cmd, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
