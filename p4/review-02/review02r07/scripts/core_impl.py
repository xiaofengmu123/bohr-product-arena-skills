"""core_impl.py -- implementation for the source-bound citation audit skill.

Design goals (robustness-first variant)
---------------------------------------
* Standard library only; runs under Python 3.8+.
* Deterministic: no `random`, no sampling, no wall-clock inside the final
  `result.json` payload. Re-running the same pipeline on the same inputs
  produces the same findings, verdicts and counts.
* Never crashes on bad input. Empty directories, missing `case.json`,
  malformed JSON, undecodable text, missing `pdftotext`, scanned PDFs and
  oversized files are all recorded and skipped instead of aborting.
* Writes are atomic (temp file + `os.replace`) so an interruption leaves either
  the previous valid file or the new valid file, never a truncated one.

Sub-commands
------------
    discover   scan /input, extract material text, write manifest.json
    scaffold   write a valid partial result.json skeleton + state.json + meta.json
    finalize   merge findings/*.json + meta.json into result.json and report.md
    pipeline   discover -> scaffold -> finalize (one-call bootstrap)
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

VERSION = "1.0.0"
DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_FILE_BYTES = 2 * 1024 * 1024          # skip individual files above 2MB
MAX_TEXT_CHARS = 5 * 1024 * 1024           # hard cap for extracted text
MIN_PDF_CHARS = 50                         # below this a PDF is treated as scanned
PDF_TOOL_TIMEOUT = 180                     # seconds

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
VERDICTS = ("pass", "issues_found", "needs_review", "unchecked", "insufficient")
SEVERITIES = ("critical", "major", "minor", "suggestion")
CATEGORIES = (
    "citation_support",
    "fact_source",
    "evidence_location",
    "numerical_statistical",
    "argumentation",
    "academic_norm",
    "method_design",
)
CITATION_VERDICTS = (
    "fully_supported",
    "partially_supported",
    "unsupported",
    "undeterminable",
)

# Required fields for every finding object. `claim`/`basis`/`source_ref`/
# `confidence`/`review_status` are optional and receive safe defaults.
REQUIRED_FINDING_KEYS = (
    "id", "category", "finding_type", "severity",
    "verdict", "locator", "quote", "suggestion",
)
FINDING_DEFAULTS = {
    "finding_type": "suggestion",
    "severity": "suggestion",
    "verdict": "undeterminable",
    "claim": "",
    "basis": "",
    "source_ref": "not_verified",
    "confidence": "medium",
    "review_status": "needs_review",
}

TEXT_EXTS = {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".log", ".bib", ".tex"}
PDF_EXTS = {".pdf"}
BINARY_EXTS = {".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls"}


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _abspath(p):
    return os.path.abspath(os.path.expanduser(p))


def read_text(path):
    """Return text content with BOM/encoding tolerance; None on I/O failure."""
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, ValueError):
            continue
        except OSError:
            return None
    return None


def read_json_robust(path):
    """Return (obj, errors). Never raises on malformed JSON/encoding."""
    raw = read_text(path)
    if raw is None:
        return None, ["cannot read file: %s" % path]
    try:
        return json.loads(raw), []
    except (json.JSONDecodeError, ValueError) as exc:
        return None, ["bad JSON in %s: %s" % (path, exc)]


def write_text_atomic(path, text):
    d = os.path.dirname(_abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json_atomic(path, obj):
    write_text_atomic(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def load_json(path):
    obj, _errs = read_json_robust(path)
    return obj


def dedup(items):
    seen, out = set(), []
    for x in items:
        try:
            sig = json.dumps(x, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            sig = str(x)
        if sig not in seen:
            seen.add(sig)
            out.append(x)
    return out


def _signature(obj):
    try:
        return json.dumps(obj, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(obj)


def append_if_new(log, entry):
    if not isinstance(log, list):
        log = []
    if log and _signature(log[-1]) == _signature(entry):
        return log
    return log + [entry]


def _rel_name(base, name, idx):
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", name) or "file"
    return "%s_%02d_%s" % (base, idx, stem)


# --------------------------------------------------------------------------- #
# State files
# --------------------------------------------------------------------------- #
def state_path(work_dir):
    return os.path.join(work_dir, "state.json")


def manifest_path(work_dir):
    return os.path.join(work_dir, "manifest.json")


def meta_path(work_dir):
    return os.path.join(work_dir, "meta.json")


def load_state(work_dir):
    st = load_json(state_path(work_dir)) or {}
    return st


def write_state(work_dir, phase, phases, findings_count):
    st = load_state(work_dir)
    st.update({
        "phase": phase,
        "phases": phases,
        "findings_count": findings_count,
    })
    write_json_atomic(state_path(work_dir), st)


def default_meta():
    return {
        "overall_verdict": None,
        "review_complete": False,
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "coverage": {
            "categories_reviewed": [],
            "sections_reviewed": [],
            "not_covered": [],
        },
        "summary_notes": "",
    }


def load_meta(work_dir):
    meta = load_json(meta_path(work_dir))
    if not isinstance(meta, dict):
        return default_meta()
    base = default_meta()
    for key in base:
        if key not in meta or meta[key] is None:
            meta[key] = base[key]
        elif key == "coverage" and not isinstance(meta.get("coverage"), dict):
            meta["coverage"] = base["coverage"]
    for key in ("sources", "uncertainty", "pending_human", "limitations",
                "errors", "degradations"):
        if not isinstance(meta.get(key), list):
            meta[key] = []
    return meta


# --------------------------------------------------------------------------- #
# discover
# --------------------------------------------------------------------------- #
def _classify_inputs(inp, files):
    case_path, task_path, materials = None, None, []
    for p in files:
        base = os.path.basename(p).lower()
        if base == "case.json":
            case_path = p
        elif base in ("task.md", "task.txt"):
            task_path = p
        else:
            materials.append(p)
    if case_path is None:
        for p in files:
            b = os.path.basename(p).lower()
            if b.endswith(".json") and "case" in b:
                case_path = p
                materials = [x for x in materials if x != p]
                break
    return case_path, task_path, materials


def _extract_pdf(pdf, work_dir, mid):
    texts_dir = os.path.join(work_dir, "texts")
    pages_dir = os.path.join(work_dir, "pages", mid)
    os.makedirs(texts_dir, exist_ok=True)
    os.makedirs(pages_dir, exist_ok=True)
    raw_txt = os.path.join(work_dir, "texts", mid + ".raw.txt")
    full_txt = os.path.join(texts_dir, mid + ".txt")

    cmd = ["pdftotext", "-layout", pdf, raw_txt]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=PDF_TOOL_TIMEOUT)
    except FileNotFoundError:
        return {"status": "needs_pdftotext",
                "degradation": "pdftotext not found; use pdf-render-tool/ocr-tool instead"}
    except subprocess.TimeoutExpired:
        return {"status": "pdf_timeout",
                "degradation": "pdftotext timed out after %ds" % PDF_TOOL_TIMEOUT}
    except OSError as exc:
        return {"status": "pdf_error", "degradation": "pdftotext error: %s" % exc}

    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()[:300]
        return {"status": "pdf_error", "degradation": "pdftotext exit %s: %s" % (proc.returncode, err)}

    text = read_text(raw_txt) or ""
    pages = text.split("\f")
    n_pages = max(1, len(pages))
    marked = []
    for i, page in enumerate(pages, 1):
        body = page.strip("\n")
        if not body.strip():
            continue
        marked.append("----- PAGE %d / %d -----\n%s" % (i, n_pages, body))
        try:
            with open(os.path.join(pages_dir, "p%03d.txt" % i), "w", encoding="utf-8") as fh:
                fh.write(body + "\n")
        except OSError:
            pass
    full = "\n\n".join(marked)
    if len(full) > MAX_TEXT_CHARS:
        full = full[:MAX_TEXT_CHARS] + "\n[... truncated ...]"
    write_text_atomic(full_txt, full or "(no extractable text)")

    non_ws = sum(1 for ch in full if not ch.isspace())
    if non_ws < MIN_PDF_CHARS:
        return {"status": "needs_ocr",
                "extracted_text": full_txt, "pages_dir": pages_dir, "pages": n_pages,
                "degradation": "extracted %d non-space chars; scanned PDF likely, mark needs_ocr" % non_ws}
    return {"status": "ok", "extracted_text": full_txt,
            "pages_dir": pages_dir, "pages": n_pages}


def _material_entry(p, inp, work_dir, idx):
    size = os.path.getsize(p)
    mid = _rel_name("m", os.path.basename(p), idx)
    entry = {
        "id": mid,
        "path": p,
        "rel_path": os.path.relpath(p, inp),
        "kind": "unknown",
        "size_bytes": size,
        "status": "pending",
        "degradations": [],
        "errors": [],
    }
    if size > MAX_FILE_BYTES:
        entry["status"] = "skipped"
        entry["kind"] = "oversized"
        entry["degradations"].append(
            "file size %d bytes exceeds cap %d; skipped" % (size, MAX_FILE_BYTES))
        return entry

    ext = os.path.splitext(p)[1].lower()
    if ext in PDF_EXTS:
        entry["kind"] = "pdf"
        result = _extract_pdf(p, work_dir, mid)
        entry.update(result)
    elif ext in TEXT_EXTS:
        entry["kind"] = "text"
        txt = read_text(p)
        if txt is None:
            entry["status"] = "unreadable"
            entry["errors"].append("cannot decode text file: %s" % p)
            return entry
        if len(txt) > MAX_TEXT_CHARS:
            txt = txt[:MAX_TEXT_CHARS] + "\n[... truncated ...]"
            entry["degradations"].append("text truncated at %d chars" % MAX_TEXT_CHARS)
        dest = os.path.join(work_dir, "texts", mid + ".txt")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        write_text_atomic(dest, txt)
        entry["status"] = "ok"
        entry["extracted_text"] = dest
        entry["text_chars"] = len(txt)
    elif ext in BINARY_EXTS:
        entry["kind"] = "office_binary"
        entry["status"] = "needs_renderer"
        entry["degradations"].append(
            "binary office file (%s); convert with office-renderer before text audit" % ext)
    else:
        entry["kind"] = "unknown_ext"
        entry["status"] = "unsupported"
        entry["degradations"].append("unsupported extension: %s" % (ext or "(none)"))
    return entry


def discover(input_dir, work_dir):
    inp = _abspath(input_dir)
    os.makedirs(work_dir, exist_ok=True)
    manifest = {
        "schema_version": "1.0.0",
        "skill_version": VERSION,
        "input_dir": inp,
        "status": "ok",
        "case": {"path": None, "case_id": None, "output_contract": {}, "read_errors": []},
        "task": {"path": None, "read_errors": []},
        "materials": [],
        "errors": [],
        "degradations": [],
    }

    if not os.path.isdir(inp):
        manifest["status"] = "input_missing"
        manifest["errors"].append("input directory not found: %s" % inp)
        write_json_atomic(manifest_path(work_dir), manifest)
        write_state(work_dir, "discovered", ["discover"], 0)
        print("[discover] input directory missing; manifest written")
        return manifest

    files = []
    for root, dirs, names in os.walk(inp):
        dirs.sort()
        for name in sorted(names):
            p = os.path.join(root, name)
            if os.path.isfile(p):
                files.append(p)

    case_path, task_path, material_paths = _classify_inputs(inp, files)
    manifest["case"]["path"] = case_path
    manifest["task"]["path"] = task_path

    if case_path is None:
        manifest["errors"].append("no case.json found under %s" % inp)
    else:
        case_obj, errs = read_json_robust(case_path)
        manifest["case"]["read_errors"] = errs
        if errs:
            manifest["errors"].extend(errs)
        if isinstance(case_obj, dict):
            manifest["case"]["case_id"] = (
                case_obj.get("case_id") or case_obj.get("caseId")
                or case_obj.get("id") or os.path.splitext(os.path.basename(case_path))[0]
            )
            contract = case_obj.get("output_contract") or case_obj.get("outputContract")
            if contract is None:
                contract = case_obj.get("contract")
            manifest["case"]["output_contract"] = contract if isinstance(contract, dict) else {}

    if task_path is None:
        manifest["degradations"].append("no task.md/task.txt found; review facets taken from SKILL.md")
    else:
        if read_text(task_path) is None:
            manifest["task"]["read_errors"].append("cannot read task file: %s" % task_path)
            manifest["errors"].append("cannot read task file: %s" % task_path)

    for idx, p in enumerate(material_paths, 1):
        manifest["materials"].append(_material_entry(p, inp, work_dir, idx))

    if not material_paths:
        manifest["status"] = "empty"

    for m in manifest["materials"]:
        manifest["degradations"].extend(m.get("degradations", []))
        manifest["errors"].extend(m.get("errors", []))

    write_json_atomic(manifest_path(work_dir), manifest)
    write_state(work_dir, "discovered", ["discover"], 0)
    n = len(manifest["materials"])
    print("[discover] input=%s materials=%d status=%s" % (inp, n, manifest["status"]))
    return manifest


# --------------------------------------------------------------------------- #
# scaffold
# --------------------------------------------------------------------------- #
def build_skeleton(case_id, status, contract):
    std = {
        "case_id": case_id,
        "status": status,
        "summary": {
            "verdict": "unchecked",
            "counts": {
                "total": 0,
                "critical": 0,
                "major": 0,
                "minor": 0,
                "suggestion": 0,
                "by_category": {c: 0 for c in CATEGORIES},
                "by_severity": {s: 0 for s in SEVERITIES},
            },
            "coverage": {
                "categories_reviewed": [],
                "sections_reviewed": [],
                "not_covered": list(CATEGORIES),
            },
        },
        "review_report": {"findings": [], "summary_notes": ""},
        "evidence": {"evidence_map": []},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "change_log": [
            {"step": "scaffold", "event": "partial result skeleton written", "status": status}
        ],
        "runtime": {
            "skill_version": VERSION,
            "phases_completed": ["discover", "scaffold"],
            "model_requests_used": None,
        },
    }
    out = {}
    if isinstance(contract, dict):
        out.update(contract)
    out.update(std)
    out["case_id"] = case_id
    return out


def scaffold(input_dir, output_dir, work_dir):
    manifest = load_json(manifest_path(work_dir)) or {}
    case = manifest.get("case") or {}
    case_id = case.get("case_id") or "unknown_case"
    contract = case.get("output_contract") if isinstance(case.get("output_contract"), dict) else {}

    if manifest.get("status") in ("input_missing", "empty") or not manifest.get("materials"):
        status = "insufficient_evidence"
    else:
        status = "partial"

    result = build_skeleton(case_id, status, contract)
    write_json_atomic(os.path.join(output_dir, "result.json"), result)

    phases = ["discover", "scaffold"]
    write_state(work_dir, "scaffolded", phases, 0)

    if not os.path.exists(meta_path(work_dir)):
        write_json_atomic(meta_path(work_dir), default_meta())

    print("[scaffold] result.json skeleton written status=%s case_id=%s" % (status, case_id))
    return result


# --------------------------------------------------------------------------- #
# finalize
# --------------------------------------------------------------------------- #
def _load_findings(work_dir):
    fdir = os.path.join(work_dir, "findings")
    items, skip_errors = [], []
    if not os.path.isdir(fdir):
        return items, skip_errors
    for name in sorted(os.listdir(fdir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(fdir, name)
        obj, errs = read_json_robust(path)
        if obj is None:
            skip_errors.append("finding file unreadable: %s (%s)" % (name, "; ".join(errs)))
            continue
        if isinstance(obj, list):
            for f in obj:
                items.append(f)
        elif isinstance(obj, dict):
            items.append(obj)
        else:
            skip_errors.append("finding file has wrong type: %s" % name)
    return items, skip_errors


def _coerce_finding(f, degradations):
    if not isinstance(f, dict):
        return None
    missing = [k for k in REQUIRED_FINDING_KEYS if f.get(k) in (None, "")]
    if missing:
        degradations.append(
            "dropped finding missing required keys: %s (missing %s)"
            % (f.get("id") or "?", ", ".join(missing)))
        return None
    out = dict(FINDING_DEFAULTS)
    out.update(f)
    out["id"] = str(out["id"]).strip()
    if not out["id"]:
        return None
    out["quote"] = str(out["quote"])
    out["locator"] = str(out["locator"])
    out["suggestion"] = str(out["suggestion"])
    if out["severity"] not in SEVERITIES:
        degradations.append("finding %s severity '%s' normalized to suggestion"
                            % (out["id"], out["severity"]))
        out["severity"] = "suggestion"
    return out


def _severity_rank(s):
    try:
        return SEVERITIES.index(s)
    except ValueError:
        return len(SEVERITIES)


def _category_rank(c):
    try:
        return CATEGORIES.index(c)
    except ValueError:
        return len(CATEGORIES)


def _compute_counts(findings):
    counts = {
        "total": len(findings),
        "critical": 0, "major": 0, "minor": 0, "suggestion": 0,
        "by_category": {c: 0 for c in CATEGORIES},
        "by_severity": {s: 0 for s in SEVERITIES},
    }
    for f in findings:
        sev = f.get("severity")
        counts["by_severity"][sev] = counts["by_severity"].get(sev, 0) + 1
        if sev in counts:
            counts[sev] += 1
        cat = f.get("category")
        if cat in counts["by_category"]:
            counts["by_category"][cat] += 1
    return counts


def _compute_status_verdict(manifest, meta, findings):
    no_materials = not manifest.get("materials")
    case_bad = bool((manifest.get("case") or {}).get("read_errors"))

    if manifest.get("status") == "input_missing":
        status = "insufficient_evidence"
    elif case_bad:
        status = "input_error"
    elif no_materials and manifest.get("status") != "ok":
        status = "insufficient_evidence"
    elif meta.get("review_complete") is True:
        status = "ok"
    else:
        status = "partial"

    if meta.get("overall_verdict") in VERDICTS:
        verdict = meta["overall_verdict"]
    elif status == "insufficient_evidence":
        verdict = "insufficient"
    elif status == "input_error":
        verdict = "unchecked"
    elif not findings:
        verdict = "pass" if meta.get("review_complete") is True else "unchecked"
    else:
        has_high = any(f.get("severity") in ("critical", "major") for f in findings)
        has_needs = any(f.get("review_status") == "needs_review" for f in findings)
        if has_high:
            verdict = "issues_found"
        elif has_needs:
            verdict = "needs_review"
        else:
            verdict = "issues_found"
    return status, verdict


def _evidence_map(findings):
    return [
        {
            "finding_id": f.get("id"),
            "locator": f.get("locator"),
            "quote": f.get("quote"),
            "basis": f.get("basis", ""),
            "source_ref": f.get("source_ref", "not_verified"),
        }
        for f in findings
    ]


def finalize(input_dir, output_dir, work_dir):
    manifest = load_json(manifest_path(work_dir)) or {}
    meta = load_meta(work_dir)
    raw_findings, skip_errors = _load_findings(work_dir)
    degradations = list(meta.get("degradations", []))
    cleaned = []
    for f in raw_findings:
        c = _coerce_finding(f, degradations)
        if c:
            cleaned.append(c)
    cleaned.sort(key=lambda f: (_category_rank(f.get("category")),
                                _severity_rank(f.get("severity")),
                                f.get("id", "")))

    counts = _compute_counts(cleaned)
    status, verdict = _compute_status_verdict(manifest, meta, cleaned)

    case = manifest.get("case") or {}
    case_id = case.get("case_id") or "unknown_case"
    contract = case.get("output_contract") if isinstance(case.get("output_contract"), dict) else {}

    existing = load_json(os.path.join(output_dir, "result.json"))
    prev_log = existing.get("change_log") if isinstance(existing, dict) else None

    result = build_skeleton(case_id, status, contract)
    result["summary"]["verdict"] = verdict
    result["summary"]["counts"] = counts
    result["summary"]["coverage"] = {
        "categories_reviewed": sorted(set(f.get("category") for f in cleaned)),
        "sections_reviewed": list(meta.get("coverage", {}).get("sections_reviewed", [])),
        "not_covered": [
            c for c in CATEGORIES
            if c not in {f.get("category") for f in cleaned}
        ],
    }
    result["review_report"]["findings"] = cleaned
    result["review_report"]["summary_notes"] = meta.get("summary_notes", "")
    result["evidence"]["evidence_map"] = _evidence_map(cleaned)
    result["sources"] = list(meta.get("sources", []))
    result["uncertainty"] = list(meta.get("uncertainty", []))
    result["pending_human"] = list(meta.get("pending_human", []))
    result["limitations"] = list(meta.get("limitations", []))
    result["errors"] = dedup(list(meta.get("errors", [])) + list(manifest.get("errors", [])) + skip_errors)
    result["degradations"] = dedup(list(manifest.get("degradations", [])) + degradations)

    if status == "partial" and not meta.get("review_complete"):
        result["limitations"] = dedup(result["limitations"] + [
            "review not marked complete (meta.json review_complete=false); partial result"
        ])

    new_entry = {
        "step": "finalize",
        "event": "findings merged and report rendered",
        "findings_count": len(cleaned),
        "status": status,
        "verdict": verdict,
    }
    base_log = prev_log if prev_log is not None else result["change_log"]
    result["change_log"] = append_if_new(list(base_log), new_entry)

    phases = list(load_state(work_dir).get("phases", ["discover", "scaffold"]))
    if "finalize" not in phases:
        phases.append("finalize")
    result["runtime"]["phases_completed"] = phases

    write_json_atomic(os.path.join(output_dir, "result.json"), result)
    _write_report(output_dir, result)
    write_state(work_dir, "finalized", phases, len(cleaned))
    print("[finalize] findings=%d status=%s verdict=%s" % (len(cleaned), status, verdict))
    return result


def _sev_label(s):
    return s


def _write_report(output_dir, result):
    lines = []
    lines.append("# 引用与事实核验审查报告")
    lines.append("")
    lines.append("- case_id: `%s`" % result.get("case_id"))
    lines.append("- status: `%s`" % result.get("status"))
    lines.append("- verdict: `%s`" % (result.get("summary", {}).get("verdict")))
    counts = result.get("summary", {}).get("counts", {})
    lines.append("- findings: total=%s critical=%s major=%s minor=%s suggestion=%s"
                 % (counts.get("total"), counts.get("critical"), counts.get("major"),
                    counts.get("minor"), counts.get("suggestion")))
    lines.append("")
    lines.append("> 本报告由 `scripts/finalize.py` 确定性渲染；复核时以 `result.json` 为准。")
    lines.append("")
    findings = result.get("review_report", {}).get("findings", [])
    lines.append("## 逐条问题清单（按类别）")
    lines.append("")
    if not findings:
        lines.append("_暂无落盘问题。_")
    current = None
    for f in findings:
        cat = f.get("category", "other")
        if cat != current:
            current = cat
            lines.append("")
            lines.append("### %s" % cat)
            lines.append("")
        lines.append("- **%s** [%s/%s] `%s`"
                     % (f.get("id"), f.get("severity"), f.get("verdict"), f.get("locator")))
        quote = (f.get("quote") or "").strip().replace("\n", " ")
        if len(quote) > 240:
            quote = quote[:240] + " …"
        lines.append("  - 原文摘录: \"%s\"" % quote)
        claim = (f.get("claim") or "").strip()
        if claim:
            lines.append("  - 断言转述: %s" % claim)
        sugg = (f.get("suggestion") or "").strip()
        if sugg:
            lines.append("  - 修改建议: %s" % sugg)
        lines.append("")
    lines.append("## 证据映射")
    lines.append("")
    ev = result.get("evidence", {}).get("evidence_map", [])
    lines.append("| finding_id | locator | quote | source_ref |")
    lines.append("|---|---|---|---|")
    for e in ev:
        q = (e.get("quote") or "").strip().replace("\n", " ").replace("|", "\\|")
        if len(q) > 120:
            q = q[:120] + " …"
        lines.append("| %s | %s | %s | %s |"
                     % (e.get("finding_id"), e.get("locator"), q, e.get("source_ref")))
    lines.append("")
    for title, key in (("不确定性", "uncertainty"), ("待人工复核", "pending_human"),
                       ("限制", "limitations"), ("错误", "errors"),
                       ("降级", "degradations")):
        items = result.get(key, [])
        lines.append("## %s" % title)
        lines.append("")
        if not items:
            lines.append("_（无）_")
        for it in items:
            lines.append("- %s" % (it if isinstance(it, str) else json.dumps(it, ensure_ascii=False)))
        lines.append("")
    lines.append("## 变更记录")
    lines.append("")
    for c in result.get("change_log", []):
        lines.append("- %s: %s" % (c.get("step"), c.get("event")))
    lines.append("")
    write_text_atomic(os.path.join(output_dir, "report.md"), "\n".join(lines))


# --------------------------------------------------------------------------- #
# pipeline + CLI
# --------------------------------------------------------------------------- #
def pipeline(input_dir, output_dir, work_dir):
    discover(input_dir, work_dir)
    scaffold(input_dir, output_dir, work_dir)
    result = finalize(input_dir, output_dir, work_dir)
    return result


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="source-bound citation audit: discover / scaffold / finalize / pipeline",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    for name, hlp in (
        ("discover", "scan input, extract material text, write manifest.json"),
        ("scaffold", "write valid partial result.json skeleton"),
        ("finalize", "merge findings + meta into result.json and report.md"),
        ("pipeline", "discover -> scaffold -> finalize in one call"),
    ):
        sp = sub.add_parser(name, help=hlp)
        sp.add_argument("--input", default=DEFAULT_INPUT,
                        help="input directory (default: %(default)s)")
        sp.add_argument("--output", default=DEFAULT_OUTPUT,
                        help="output directory (default: %(default)s)")
        sp.add_argument("--work", default=DEFAULT_WORK,
                        help="work directory (default: %(default)s)")
    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    dispatch = {
        "discover": discover,
        "scaffold": scaffold,
        "finalize": finalize,
        "pipeline": pipeline,
    }
    fn = dispatch[args.command]
    try:
        fn(args.input, args.output, args.work)
    except Exception as exc:  # last-resort guard: report, do not leave a half-written file
        sys.stderr.write("fatal: %s\n" % exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
