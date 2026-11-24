# -*- coding: utf-8 -*-
"""core_impl.py -- shared implementation for citation-fact-audit-v2.

Stdlib-only. Deterministic (no randomness). All file paths are resolved with
os.path and every write goes through ensure_dir. Functions here are thin and
testable; main.py and selftest.py are entry points.

Subcommands:
  discover  -- scan /input, extract PDF text (page markers), write manifest.json
  scaffold  -- write a legal partial result.json + state.json + meta.json skeleton
  finalize  -- merge findings + meta, render report.md, refresh result.json
  selftest  -- validate a result.json against the contract (also usable standalone)
"""

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

STATUS_OK = ("ok", "partial", "insufficient_evidence", "input_error")
VERDICT_OK = ("pass", "issues_found", "needs_review", "unchecked", "insufficient")
SEVERITY_OK = ("critical", "major", "minor", "suggestion")
CATEGORY_OK = (
    "citation_support",
    "fact_source",
    "evidence_location",
    "numerical_statistical",
    "argumentation",
    "academic_norm",
)
FINDING_REQUIRED = (
    "id", "category", "locator", "quote", "verdict", "severity", "suggestion",
)
MAX_FILE_BYTES = 2 * 1024 * 1024


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def read_text(path, limit=None):
    """Read a text file tolerating BOM and common encodings."""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(limit) if limit else fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read(limit) if limit else fh.read()


def read_json(path, default=None):
    """Parse JSON tolerantly; return default on any failure."""
    try:
        raw = read_text(path)
        return json.loads(raw)
    except (OSError, ValueError, TypeError):
        return default


def write_json(path, obj):
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    return path


def parse_common_args(argv):
    """Return (input_dir, output_dir, work_dir, help_requested)."""
    input_dir, output_dir, work_dir = DEFAULT_INPUT, DEFAULT_OUTPUT, DEFAULT_WORK
    want_help = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--help", "-h"):
            want_help = True
        elif a == "--input" and i + 1 < len(argv):
            input_dir = argv[i + 1]; i += 1
        elif a == "--output" and i + 1 < len(argv):
            output_dir = argv[i + 1]; i += 1
        elif a == "--work" and i + 1 < len(argv):
            work_dir = argv[i + 1]; i += 1
        i += 1
    return input_dir, output_dir, work_dir, want_help


# --------------------------------------------------------------------------- #
# discover
# --------------------------------------------------------------------------- #

def _pdf_text_path(work_dir, stem):
    return os.path.join(work_dir, "texts", stem + ".txt")


def _extract_pdf(pdf_path, work_dir):
    """Extract PDF text with page markers; return (rel_path, pages, degradation)."""
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    out_path = _pdf_text_path(work_dir, stem)
    ensure_dir(os.path.dirname(out_path))
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        return None, 0, "missing_pdftotext:needs_ocr"

    raw_path = out_path + ".raw.tmp"
    try:
        proc = subprocess.run(
            [pdftotext, "-layout", "-enc", "UTF-8", pdf_path, raw_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None, 0, "pdftotext_error:needs_ocr"

    if proc.returncode != 0 or not os.path.exists(raw_path):
        return None, 0, "pdftotext_failed:needs_ocr"

    try:
        raw = read_text(raw_path)
    except OSError:
        return None, 0, "pdftotext_read_failed:needs_ocr"
    finally:
        try:
            os.remove(raw_path)
        except OSError:
            pass

    pages = raw.split("\f") if raw else []
    if not raw.strip():
        return None, 0, "empty_text:needs_ocr"
    if len(pages) == 1 and len(raw.strip()) < 40:
        # Possibly a title-only or scanned single page; keep but flag low-confidence.
        flagged = "low_text:possible_scan"
    else:
        flagged = None

    lines = []
    for idx, page in enumerate(pages, 1):
        lines.append("----- PAGE %d / %d -----" % (idx, len(pages)))
        lines.append(page.strip("\n"))
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines).rstrip() + "\n")

    rel = os.path.relpath(out_path, work_dir)
    return rel, len(pages), flagged


def _classify(name):
    lower = name.lower()
    if lower == "case.json":
        return "case"
    if lower == "task.md":
        return "task"
    if lower.endswith(".pdf"):
        return "pdf"
    if lower.endswith((".txt", ".md", ".csv", ".json")):
        return "text"
    return "other"


def discover(input_dir, output_dir, work_dir):
    """Scan input_dir; extract PDFs; write work_dir/manifest.json and meta skeleton."""
    ensure_dir(work_dir)
    ensure_dir(output_dir)
    errors, degradations = [], []
    files, materials = [], []

    if not os.path.isdir(input_dir):
        errors.append("input_dir_missing: %s" % input_dir)
        manifest = {
            "case_id": None, "input_dir": input_dir, "files": [], "materials": [],
            "errors": errors, "degradations": degradations, "created_at": now_iso(),
        }
        write_json(os.path.join(work_dir, "manifest.json"), manifest)
        _write_meta_skeleton(work_dir, errors, degradations)
        return manifest

    entries = sorted(os.listdir(input_dir))
    for name in entries:
        path = os.path.join(input_dir, name)
        if not os.path.isfile(path):
            continue
        size = os.path.getsize(path)
        if size > MAX_FILE_BYTES:
            degradations.append("skipped_oversize: %s (%d bytes)" % (name, size))
            continue
        kind = _classify(name)
        rec = {"name": name, "path": path, "kind": kind, "size": size}
        if kind == "case":
            files.append(rec)
        elif kind == "task":
            files.append(rec)
        elif kind == "pdf":
            rel, pages, deg = _extract_pdf(path, work_dir)
            mat = {"name": name, "path": path, "kind": "pdf",
                   "extracted": rel, "pages": pages}
            if deg:
                degradations.append("%s: %s" % (name, deg))
                mat["degradation"] = deg
            materials.append(mat)
        elif kind == "text":
            materials.append({"name": name, "path": path, "kind": "text",
                              "extracted": None, "pages": None})
        else:
            materials.append({"name": name, "path": path, "kind": "other",
                              "extracted": None, "pages": None})

    case_id = None
    for rec in files:
        if rec["kind"] == "case":
            obj = read_json(rec["path"])
            if isinstance(obj, dict) and obj.get("case_id"):
                case_id = obj["case_id"]
            break

    manifest = {
        "case_id": case_id,
        "input_dir": input_dir,
        "files": files,
        "materials": materials,
        "errors": errors,
        "degradations": degradations,
        "created_at": now_iso(),
    }
    write_json(os.path.join(work_dir, "manifest.json"), manifest)
    _write_meta_skeleton(work_dir, errors, degradations)
    return manifest


def _write_meta_skeleton(work_dir, errors, degradations):
    path = os.path.join(work_dir, "meta.json")
    if not os.path.exists(path):
        write_json(path, {
            "overall_verdict": None, "status": None,
            "sources": [], "uncertainty": [], "pending_human": [],
            "limitations": [], "errors": list(errors),
            "degradations": list(degradations), "model_requests": 0,
        })


# --------------------------------------------------------------------------- #
# scaffold
# --------------------------------------------------------------------------- #

def _empty_counts():
    return {
        "total": 0, "critical": 0, "major": 0, "minor": 0, "suggestion": 0,
        "by_category": {c: 0 for c in CATEGORY_OK},
    }


def _base_result(case_id, status, verdict, started_at):
    return {
        "case_id": case_id,
        "status": status,
        "summary": {"verdict": verdict, "counts": _empty_counts()},
        "review_report": {"findings": []},
        "evidence": {"evidence_map": []},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "change_log": [
            {"event": "scaffold", "status": status, "verdict": verdict,
             "ts": started_at}
        ],
        "runtime": {
            "started_at": started_at, "finished_at": None, "seconds": None,
            "model_requests": 0, "scaffold_landed": True,
        },
    }


def scaffold(input_dir, output_dir, work_dir):
    """Write a legal partial result.json skeleton, honoring output_contract."""
    ensure_dir(work_dir)
    ensure_dir(output_dir)
    started_at = now_iso()

    case_obj = read_json(os.path.join(input_dir, "case.json"), default={})
    case_id = case_obj.get("case_id") if isinstance(case_obj, dict) else None
    if not case_id:
        case_id = "unknown"
    contract = case_obj.get("output_contract") if isinstance(case_obj, dict) else None
    if not isinstance(contract, dict):
        contract = {}

    status, verdict = "partial", "unchecked"
    manifest = read_json(os.path.join(work_dir, "manifest.json"))
    if isinstance(manifest, dict):
        if not manifest.get("materials") and manifest.get("case_id") is None:
            status, verdict = "insufficient_evidence", "insufficient"
        for d in manifest.get("degradations", []):
            if isinstance(d, str) and "missing_pdftotext" in d:
                pass  # keep partial; the agent must still try OCR or skip

    result = _base_result(case_id, status, verdict, started_at)
    # Echo every output_contract field at top level without renaming.
    for key, value in contract.items():
        if key not in result:
            result[key] = value
    result["errors"] = []
    result["degradations"] = []

    write_json(os.path.join(output_dir, "result.json"), result)
    write_json(os.path.join(work_dir, "state.json"), {
        "case_id": case_id, "created_at": started_at,
        "output_contract": contract, "scaffold_landed": True,
        "scaffold_status": status, "scaffold_verdict": verdict,
    })
    return result


# --------------------------------------------------------------------------- #
# finalize
# --------------------------------------------------------------------------- #

def _iso_seconds(start_iso, end_iso):
    try:
        start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        return max(0, int((end - start).total_seconds()))
    except (ValueError, AttributeError):
        return None


def _load_findings(work_dir):
    fdir = os.path.join(work_dir, "findings")
    items = []
    if not os.path.isdir(fdir):
        return items
    for name in sorted(os.listdir(fdir)):
        if not name.endswith(".json"):
            continue
        obj = read_json(os.path.join(fdir, name))
        if isinstance(obj, dict):
            obj.setdefault("id", os.path.splitext(name)[0])
            items.append(obj)
    items.sort(key=lambda f: f.get("id", ""))
    return items


def _compute_counts(findings):
    counts = _empty_counts()
    counts["total"] = len(findings)
    for f in findings:
        sev = f.get("severity")
        if sev in counts:
            counts[sev] += 1
        cat = f.get("category")
        if cat in counts["by_category"]:
            counts["by_category"][cat] += 1
    return counts


def _decide_status_verdict(meta, findings, manifest):
    """Compute honest status/verdict; agent-provided meta values win."""
    status = meta.get("status") if isinstance(meta, dict) else None
    verdict = meta.get("overall_verdict") if isinstance(meta, dict) else None

    if not status:
        has_material = bool(isinstance(manifest, dict) and manifest.get("materials"))
        if not has_material and not findings:
            status = "insufficient_evidence"
        elif findings:
            status = "ok"
        else:
            status = "partial"
    if not verdict:
        if status == "insufficient_evidence":
            verdict = "insufficient"
        elif findings:
            verdict = "issues_found"
        else:
            verdict = "unchecked"
    if status not in STATUS_OK:
        status = "partial"
    if verdict not in VERDICT_OK:
        verdict = "needs_review"
    return status, verdict


def _render_report(result, out_md):
    lines = []
    lines.append("# 引用与事实核验审查报告")
    lines.append("")
    lines.append("- case_id: %s" % result.get("case_id"))
    lines.append("- status: %s" % result.get("status"))
    lines.append("- verdict: %s" % result.get("summary", {}).get("verdict"))
    counts = result.get("summary", {}).get("counts", {})
    lines.append("- findings: %s (critical=%s major=%s minor=%s suggestion=%s)" % (
        counts.get("total"), counts.get("critical"), counts.get("major"),
        counts.get("minor"), counts.get("suggestion")))
    lines.append("")
    findings = result.get("review_report", {}).get("findings", [])
    if findings:
        lines.append("## 问题清单")
        lines.append("")
        lines.append("| id | category | severity | verdict | locator | quote | suggestion |")
        lines.append("|---|---|---|---|---|---|---|")
        for f in findings:
            quote = (f.get("quote") or "").replace("\n", " ").replace("|", "\\|")
            if len(quote) > 120:
                quote = quote[:120] + "…"
            lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                f.get("id", ""), f.get("category", ""), f.get("severity", ""),
                f.get("verdict", ""), f.get("locator", ""), quote,
                (f.get("suggestion") or "").replace("|", "\\|")))
        lines.append("")
    for section in ("uncertainty", "pending_human", "limitations", "errors",
                    "degradations", "sources"):
        vals = result.get(section) or []
        if not vals:
            continue
        lines.append("## %s" % section)
        lines.append("")
        for v in vals:
            if isinstance(v, dict):
                lines.append("- " + json.dumps(v, ensure_ascii=False))
            else:
                lines.append("- " + str(v))
        lines.append("")
    with open(out_md, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines).rstrip() + "\n")
    return out_md


def finalize(input_dir, output_dir, work_dir):
    """Merge findings + meta into result.json; render report.md."""
    ensure_dir(output_dir)
    state = read_json(os.path.join(work_dir, "state.json"), default={})
    if not isinstance(state, dict):
        state = {}
    case_id = state.get("case_id") or "unknown"
    started_at = state.get("created_at") or now_iso()
    contract = state.get("output_contract")
    if not isinstance(contract, dict):
        contract = {}

    manifest = read_json(os.path.join(work_dir, "manifest.json"))
    meta = read_json(os.path.join(work_dir, "meta.json"), default={})
    if not isinstance(meta, dict):
        meta = {}
    findings = _load_findings(work_dir)

    status, verdict = _decide_status_verdict(meta, findings, manifest)
    result = _base_result(case_id, status, verdict, started_at)
    for key, value in contract.items():
        if key not in result:
            result[key] = value

    result["review_report"]["findings"] = findings
    result["summary"]["counts"] = _compute_counts(findings)
    result["summary"]["verdict"] = verdict
    result["status"] = status

    result["sources"] = meta.get("sources") or []
    result["uncertainty"] = meta.get("uncertainty") or []
    result["pending_human"] = meta.get("pending_human") or []
    result["limitations"] = meta.get("limitations") or []
    result["errors"] = meta.get("errors") or []
    result["degradations"] = meta.get("degradations") or []

    evidence_map = []
    for f in findings:
        evidence_map.append({
            "finding_id": f.get("id"),
            "locator": f.get("locator"),
            "quote": f.get("quote"),
            "basis": f.get("basis"),
            "source_ref": f.get("source_ref"),
        })
    result["evidence"]["evidence_map"] = evidence_map

    finished_at = now_iso()
    result["runtime"]["finished_at"] = finished_at
    result["runtime"]["seconds"] = _iso_seconds(started_at, finished_at)
    result["runtime"]["model_requests"] = meta.get("model_requests") or 0
    result["runtime"]["scaffold_landed"] = bool(state.get("scaffold_landed", True))

    result["change_log"] = [
        {"event": "scaffold",
         "status": state.get("scaffold_status", "partial"),
         "verdict": state.get("scaffold_verdict", "unchecked"),
         "ts": started_at},
        {"event": "finalize", "status": status, "verdict": verdict,
         "ts": finished_at, "total": len(findings)},
    ]

    write_json(os.path.join(output_dir, "result.json"), result)
    _render_report(result, os.path.join(output_dir, "report.md"))
    return result


# --------------------------------------------------------------------------- #
# selftest (validation)
# --------------------------------------------------------------------------- #

def _normalize(s):
    return " ".join(s.replace("\r\n", "\n").replace("\n", " ").split())


def _verbatim_hit(quote, source_texts):
    if not quote or not quote.strip():
        return False, "empty quote"
    norm = _normalize(quote)
    if len(norm) < 3:
        return False, "quote too short to verify"
    for src in source_texts:
        if norm in _normalize(src):
            return True, None
    return False, "quote not found verbatim in any extracted source text"


def validate_result(result_path, input_dir, work_dir):
    """Return (ok, list_of_errors). Verifies parse, contract, findings, verbatim quote."""
    errs = []
    if not os.path.exists(result_path):
        return False, ["result.json missing"]
    result = read_json(result_path)
    if not isinstance(result, dict):
        return False, ["result.json not a JSON object"]

    if not result.get("case_id"):
        errs.append("missing case_id")
    if result.get("status") not in STATUS_OK:
        errs.append("invalid status: %r" % result.get("status"))
    verdict = result.get("summary", {}).get("verdict")
    if verdict not in VERDICT_OK:
        errs.append("invalid verdict: %r" % verdict)

    contract = read_json(os.path.join(input_dir, "case.json"), default={})
    if isinstance(contract, dict) and isinstance(contract.get("output_contract"), dict):
        for key in contract["output_contract"]:
            if key not in result:
                errs.append("output_contract key missing: %s" % key)

    findings = result.get("review_report", {}).get("findings", [])
    if not isinstance(findings, list):
        errs.append("findings not a list")
        findings = []
    counts = result.get("summary", {}).get("counts", {})
    if counts.get("total") != len(findings):
        errs.append("counts.total != len(findings)")

    for f in findings:
        fid = f.get("id", "?")
        for key in FINDING_REQUIRED:
            if key not in f or f.get(key) in (None, "", []):
                errs.append("%s missing %s" % (fid, key))
        if f.get("severity") not in SEVERITY_OK:
            errs.append("%s bad severity %r" % (fid, f.get("severity")))
        if f.get("category") not in CATEGORY_OK:
            errs.append("%s bad category %r" % (fid, f.get("category")))

    # Verbatim quote check against extracted text files when available.
    source_texts = []
    if isinstance(result.get("status"), str) and result["status"] not in (
            "insufficient_evidence", "input_error"):
        tdir = os.path.join(work_dir, "texts")
        if os.path.isdir(tdir):
            for name in sorted(os.listdir(tdir)):
                if name.endswith(".txt"):
                    try:
                        source_texts.append(read_text(os.path.join(tdir, name)))
                    except OSError:
                        continue
        else:
            # Fall back to text materials directly under input_dir.
            manifest = read_json(os.path.join(work_dir, "manifest.json"))
            for mat in (manifest or {}).get("materials", []):
                if mat.get("kind") == "text":
                    try:
                        source_texts.append(read_text(mat["path"]))
                    except OSError:
                        continue
        for f in findings:
            hit, why = _verbatim_hit(f.get("quote", ""), source_texts)
            if not hit and source_texts:
                errs.append("%s quote not verbatim (%s)" % (f.get("id", "?"), why))

    return (not errs), errs
