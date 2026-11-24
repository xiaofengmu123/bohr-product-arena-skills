#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Core implementation for Results & Discussion section writing.

Design goals (standard library only, deterministic, degradation-first):

1. Discover input without assuming fixed file names or keys.
2. Read text/JSON/CSV tolerantly (BOM, bad encoding, bad JSON, oversized files).
3. Produce three structured *intermediates* before any prose is written:
   - plan.json        : writing plan (audience/style/format, section split,
                        figure/table inventory, claim slots, constraints)
   - figure_model.json: figure/table/numeric model (locator + verbatim quote +
                        unit + statistics + reading status per item)
   - evidence_map.json: claim -> evidence mapping (type, strength, status)
   These make the final manuscript independently re-checkable ("可复核").
4. Drop a legal result.json skeleton early (honest partial), then merge
   incremental patches atomically.
5. Validate the final result.json against the case's output_contract and
   cross-check manuscript numbers against the numeric inventory.

Nothing here fabricates scores, DOIs, URLs or scientific data.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from datetime import datetime, timezone

MAX_FILE_BYTES = 2 * 1024 * 1024  # skip files larger than this

TEXT_EXT = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl",
    ".xml", ".html", ".rst", ".tex", ".yaml", ".yml", ".log", ".bib",
}
BINARY_EXT = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".docx",
    ".xlsx", ".pptx", ".tif", ".tiff", ".odt", ".ods", ".odp",
}
TABLE_EXT = {".csv", ".tsv"}

# Fields the Skill always appends beyond the case's own output_contract.
RESERVED_FIELDS = (
    "case_id", "status", "sources", "uncertainty", "pending_human",
    "change_log", "errors", "degradations", "runtime", "intermediates",
)

# Inflated claims that must never appear without a cited source.
FORBIDDEN_PATTERNS = [
    re.compile(r"首次"),
    re.compile(r"国际领先|国际先进|世界领先|国内领先"),
    re.compile(r"填补空白|填补了.{0,6}空白"),
    re.compile(r"颠覆性|革命性"),
    re.compile(r"开创性|奠基性|里程碑"),
    re.compile(r"史无前例|前所未有|绝无仅有"),
]

# Numbers with optional units (single-pass, no catastrophic backtracking).
NUMBER_RE = re.compile(
    r"(?<![\w.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?"
    r"(?:\s*(?:%|‰|mg|kg|g|μg|ug|mL|ml|μL|ul|L|mol|mM|μM|uM|nM|pM|M|"
    r"s|min|h|hr|d|day|wk|week|mo|month|yr|year|cm|mm|μm|um|nm|"
    r"°C|°F|IU|U|×|x|fold|times))?"
)

STATISTICS_TERMS = (
    "p value", "p-value", "p<", "p <", "p=", "p =", "CI", "confidence interval",
    "SEM", "SE", "SD", "standard deviation", "standard error", "IQR",
    "interquartile", "t-test", "t test", "Mann-Whitney", "ANOVA", "Kruskal",
    "Wilcoxon", "χ2", "chi-square", "hazard ratio", "odds ratio", "r =", "r=",
    "n =", "n=", "N =", "N=",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_text_file(name: str) -> bool:
    ext = os.path.splitext(name)[1].lower()
    return ext in TEXT_EXT


def _is_table_file(name: str) -> bool:
    ext = os.path.splitext(name)[1].lower()
    return ext in TABLE_EXT


def _is_binary_file(name: str) -> bool:
    ext = os.path.splitext(name)[1].lower()
    return ext in BINARY_EXT


def read_text(path: str, max_bytes: int = MAX_FILE_BYTES):
    """Return (text, error). Tolerant of BOM, bad encoding, oversized files."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, f"cannot stat {path}: {exc}"
    if size > max_bytes:
        return None, f"file {size} bytes exceeds {max_bytes} limit (skipped)"
    if size == 0:
        return "", None
    raw = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                raw = fh.read()
            break
        except (UnicodeDecodeError, OSError):
            continue
    if raw is None:
        return None, f"cannot decode {path} with tried encodings"
    return raw, None


def read_json(path: str):
    """Return (data, error). Tolerant of BOM/trailing content/bad JSON."""
    text, err = read_text(path)
    if err is not None:
        return None, err
    if text is None:
        return None, "no text"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        # Try to salvage a JSON object prefix (common in concatenated files).
        for end in range(len(text), 0, -1):
            try:
                return json.loads(text[:end]), None
            except json.JSONDecodeError:
                continue
        return None, f"bad JSON in {path}: {exc}"


def discover_files(input_dir: str):
    """Return (files, errors). files is a list of dicts, sorted by name."""
    files, errors = [], []
    if not os.path.isdir(input_dir):
        return files, [f"input dir not found: {input_dir}"]
    try:
        names = sorted(os.listdir(input_dir))
    except OSError as exc:
        return files, [f"cannot list {input_dir}: {exc}"]
    for name in names:
        if name.startswith("."):
            continue
        full = os.path.join(input_dir, name)
        if not os.path.isfile(full):
            continue
        try:
            size = os.path.getsize(full)
        except OSError as exc:
            errors.append(f"cannot stat {full}: {exc}")
            continue
        kind = "binary" if _is_binary_file(name) else (
            "table" if _is_table_file(name) else (
                "text" if _is_text_file(name) else "unknown"))
        files.append({
            "name": name,
            "path": full,
            "size": size,
            "kind": kind,
        })
    return files, errors


def build_input_summary(case_path: str, task_path: str, input_dir: str,
                        work_dir: str):
    """Normalize inputs into a single summary dict; never raises."""
    summary = {
        "case_id": None,
        "case_path": case_path,
        "task_path": task_path,
        "input_dir": input_dir,
        "output_contract": {},
        "materials": [],
        "errors": [],
        "generated_at": now_iso(),
    }
    case, err = read_json(case_path)
    if err is not None:
        summary["errors"].append(f"case.json: {err}")
    elif isinstance(case, dict):
        summary["case_id"] = case.get("case_id") or case.get("id") or case.get("caseId")
        oc = case.get("output_contract")
        if isinstance(oc, dict):
            summary["output_contract"] = {k: None for k in oc}
        elif isinstance(oc, list):
            for item in oc:
                if isinstance(item, str):
                    summary["output_contract"][item] = None
                elif isinstance(item, dict) and item.get("name"):
                    summary["output_contract"][item["name"]] = None
        # Preserve other top-level case facts for the plan step.
        for key in ("task", "instructions", "audience", "style", "format",
                    "constraints", "materials", "requirements"):
            if key in case:
                summary.setdefault("case_" + key, case[key])
    else:
        summary["errors"].append("case.json is not a JSON object")

    task_text, terr = read_text(task_path)
    if terr is not None:
        summary["errors"].append(f"task.md: {terr}")
    else:
        summary["task_text"] = task_text or ""

    files, ferrs = discover_files(input_dir)
    summary["materials"] = files
    summary["errors"].extend(ferrs)

    # Read readable material text (bounded) into a locatable corpus.
    meta_names = {
        os.path.basename(case_path) if case_path else "case.json",
        os.path.basename(task_path) if task_path else "task.md",
    }
    corpus = []
    for f in files:
        if f["kind"] in ("text", "table"):
            text, rerr = read_text(f["path"])
            if rerr is not None:
                summary["errors"].append(f"{f['name']}: {rerr}")
            else:
                corpus.append({
                    "name": f["name"],
                    "kind": f["kind"],
                    "text": text or "",
                    "is_meta": f["name"] in meta_names,
                })
    summary["corpus"] = corpus

    os.makedirs(work_dir, exist_ok=True)
    out = os.path.join(work_dir, "input_summary.json")
    try:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        summary["errors"].append(f"cannot write {out}: {exc}")
    summary["summary_file"] = out
    return summary


def _default_contract():
    return {
        "deliverable": None,
        "facts_sources": None,
        "version_boundaries": None,
    }


def build_skeleton(case_id: str, output_contract, output_dir: str,
                   summary_file: str = None):
    """Write a legal partial result.json; returns (result, path, error).

    All output_contract keys are back-filled verbatim with null placeholders;
    reserved fields carry honest initial values.
    """
    os.makedirs(output_dir, exist_ok=True)
    contract = {}
    if isinstance(output_contract, dict):
        contract = {k: None for k in output_contract}
    if not contract:
        contract = _default_contract()
    result = {}
    result["case_id"] = case_id
    for k in contract:
        result[k] = contract[k]
    result["status"] = "partial" if case_id else "insufficient_evidence"
    result["sources"] = []
    result["uncertainty"] = []
    result["pending_human"] = []
    result["change_log"] = [{
        "timestamp": now_iso(),
        "action": "skeleton_init",
        "detail": "legal partial skeleton written before analysis",
    }]
    result["errors"] = []
    result["degradations"] = []
    result["runtime"] = {"started_at": now_iso(), "summary_file": summary_file}
    result["intermediates"] = {}
    path = os.path.join(output_dir, "result.json")
    try:
        _write_json_atomic(path, result)
    except OSError as exc:
        return result, path, f"cannot write {path}: {exc}"
    return result, path, None


def merge_patch(result_path: str, patch: dict, action: str, detail: str = ""):
    """Merge an incremental patch into result.json; returns (result, error).

    Preserves output_contract keys; reserved fields merge by list-append or
    dict-update semantics. Never raises on bad input.
    """
    result, err = read_json(result_path)
    if err is not None:
        return None, err
    if not isinstance(result, dict):
        return None, "result.json is not an object"
    for key, value in patch.items():
        if key in ("change_log",):
            continue
        if key in ("sources", "uncertainty", "pending_human", "errors",
                   "degradations") and isinstance(value, list):
            existing = result.setdefault(key, [])
            if isinstance(existing, list):
                for item in value:
                    if item not in existing:
                        existing.append(item)
            continue
        if key in ("runtime", "intermediates") and isinstance(value, dict):
            result.setdefault(key, {}).update(value)
            continue
        result[key] = value
    cl = result.setdefault("change_log", [])
    if not isinstance(cl, list):
        cl = []
        result["change_log"] = cl
    cl.append({
        "timestamp": now_iso(),
        "action": action,
        "detail": detail,
    })
    try:
        _write_json_atomic(result_path, result)
    except OSError as exc:
        return result, f"cannot write {result_path}: {exc}"
    return result, None


def _write_json_atomic(path: str, data):
    """Write JSON then rename (atomic on POSIX; best-effort elsewhere)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _context(text: str, start: int, end: int, span: int = 70) -> str:
    lo = max(0, start - span)
    hi = min(len(text), end + span)
    return text[lo:hi].replace("\n", " ").strip()


def build_numeric_inventory(summary: dict):
    """Extract every numeric mention + statistics term from readable corpus.

    This gives the Agent a source-side inventory to cross-check every number
    written into the manuscript (addresses 读得准 / 核得了).
    """
    inventory = {"numbers": [], "statistics": [], "generated_at": now_iso()}
    for doc in summary.get("corpus", []):
        if doc.get("is_meta"):
            continue  # task metadata (case.json/task.md) is not scientific material
        text = doc.get("text") or ""
        for m in NUMBER_RE.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            inventory["numbers"].append({
                "file": doc["name"],
                "line": line,
                "value": m.group(0).strip(),
                "context": _context(text, m.start(), m.end()),
                "locator": f"{doc['name']}#L{line}",
            })
        low = text.lower()
        for term in STATISTICS_TERMS:
            idx = 0
            while True:
                idx = low.find(term.lower(), idx)
                if idx < 0:
                    break
                line = text.count("\n", 0, idx) + 1
                inventory["statistics"].append({
                    "file": doc["name"],
                    "line": line,
                    "term": term,
                    "context": _context(text, idx, idx + len(term)),
                    "locator": f"{doc['name']}#L{line}",
                })
                idx += max(1, len(term))
    return inventory


def _parse_table(path: str):
    """Parse a CSV/TSV into rows + header + numeric-column summary."""
    name = os.path.basename(path)
    delim = "\t" if name.lower().endswith(".tsv") else ","
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            sample = fh.read(4096)
            fh.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
                delim = dialect.delimiter
            except csv.Error:
                pass
            reader = csv.reader(fh, delimiter=delim)
            rows = [r for r in reader]
    except (OSError, UnicodeDecodeError) as exc:
        return {"name": name, "error": f"cannot parse table: {exc}", "rows": []}
    if not rows:
        return {"name": name, "rows": [], "header": None}
    header = rows[0]
    data_rows = rows[1:]
    numeric_cols = []
    for ci, _ in enumerate(header):
        vals = []
        for r in data_rows:
            if ci < len(r):
                v = r[ci].strip().replace(",", "")
                try:
                    float(v)
                    vals.append(v)
                except ValueError:
                    pass
        if vals:
            numeric_cols.append({
                "index": ci,
                "header": header[ci] if ci < len(header) else "",
                "n_numeric": len(vals),
                "sample_values": vals[:5],
            })
    return {
        "name": name,
        "header": header,
        "n_rows": len(data_rows),
        "numeric_columns": numeric_cols,
        "sample_rows": data_rows[:3],
    }


def build_scaffolds(summary: dict, work_dir: str, output_dir: str):
    """Write plan.json / figure_model.json / evidence_map.json scaffolds.

    The scaffolds are *structured* and pre-populated with everything the
    scripts can determine automatically (inventory, table parse, section
    split); the Agent only fills content slots, never invents structure.
    """
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    # figure_model.json: figures (binary -> unread placeholder), tables, numbers.
    figure_model = {
        "case_id": summary.get("case_id"),
        "generated_at": now_iso(),
        "figures": [],
        "tables": [],
        "numeric_inventory": build_numeric_inventory(summary),
        "reading_status": "partial",
    }
    for f in summary.get("materials", []):
        if f["kind"] == "binary":
            figure_model["figures"].append({
                "id": None,
                "file": f["name"],
                "panels": [],
                "x_axis": None,
                "y_axis": None,
                "values": [],
                "statistics": {},
                "verbatim_quotes": [],
                "locator": f["name"],
                "reading_status": "unread_placeholder",
                "note": "PDF/图像需平台 pdf-text-tool / ocr-tool 读取后回填",
            })
        elif f["kind"] == "table":
            figure_model["tables"].append(_parse_table(f["path"]))

    # plan.json: writing plan (section split enforces 分得清).
    plan = {
        "case_id": summary.get("case_id"),
        "generated_at": now_iso(),
        "task_type": "results_discussion",
        "audience": None,
        "style": None,
        "format": None,
        "language": None,
        "sections": [
            {"id": "results", "heading": "Results", "scope": "observation_only",
             "organize_by": None, "content": None, "status": "planned"},
            {"id": "discussion", "heading": "Discussion",
             "scope": "interpretation+literature+limitations",
             "content": None, "status": "planned"},
        ],
        "claim_slots": [],
        "constraints": summary.get("case_constraints"),
        "notes": [],
    }

    # evidence_map.json: claim -> evidence.
    evidence_map = {
        "case_id": summary.get("case_id"),
        "generated_at": now_iso(),
        "claims": [],
    }

    paths = {
        "plan": os.path.join(work_dir, "plan.json"),
        "figure_model": os.path.join(work_dir, "figure_model.json"),
        "evidence_map": os.path.join(work_dir, "evidence_map.json"),
    }
    try:
        _write_json_atomic(paths["plan"], plan)
        _write_json_atomic(paths["figure_model"], figure_model)
        _write_json_atomic(paths["evidence_map"], evidence_map)
    except OSError as exc:
        return paths, f"cannot write scaffold: {exc}"
    return paths, None


def check_forbidden(text: str):
    """Return list of forbidden-phrase hits with 40-char context."""
    hits = []
    if not text:
        return hits
    for pat in FORBIDDEN_PATTERNS:
        for m in pat.finditer(text):
            hits.append({
                "phrase": m.group(0),
                "context": _context(text, m.start(), m.end(), 40),
            })
    return hits


def validate_result(result_path: str, summary_path: str = None):
    """Validate result.json structure and consistency; returns issues list.

    Structural checks always run; consistency checks run when a summary and
    intermediates are available. Never raises.
    """
    issues = []
    result, err = read_json(result_path)
    if err is not None:
        return [f"result.json unreadable: {err}"]
    if not isinstance(result, dict):
        return ["result.json is not a JSON object"]

    if "case_id" not in result:
        issues.append("missing case_id")
    for field in RESERVED_FIELDS:
        if field not in result:
            issues.append(f"missing reserved field: {field}")
    for bad in ("status",):
        if result.get(bad) not in (
                "partial", "complete", "insufficient_evidence", "failed",
                "input_error", "ok"):
            issues.append(f"invalid status: {result.get(bad)!r}")

    # output_contract fields must all be present (no nulls when complete).
    if summary_path and os.path.isfile(summary_path):
        summary, serr = read_json(summary_path)
        if serr is None and isinstance(summary, dict):
            oc = summary.get("output_contract") or {}
            for key in oc:
                if key not in result:
                    issues.append(f"output_contract field missing: {key}")
            if result.get("status") == "complete":
                for key in oc:
                    if result.get(key) is None:
                        issues.append(f"output_contract field null but status=complete: {key}")

    # manuscript forbidden-word check (if deliverable points to a file).
    deliverable = result.get("deliverable")
    if isinstance(deliverable, dict) and deliverable.get("file"):
        fpath = deliverable["file"]
        if not os.path.isabs(fpath):
            fpath = os.path.join(os.path.dirname(result_path), fpath)
        if os.path.isfile(fpath):
            text, _ = read_text(fpath)
            hits = check_forbidden(text or "")
            for h in hits:
                issues.append(f"forbidden phrase in manuscript: {h['phrase']!r} near {h['context']!r}")

    # evidence traceability: facts_sources items need locator + quote.
    fs = result.get("facts_sources")
    if isinstance(fs, list):
        for i, item in enumerate(fs):
            if not isinstance(item, dict):
                issues.append(f"facts_sources[{i}] is not an object")
                continue
            if not item.get("locator") and not item.get("source"):
                issues.append(f"facts_sources[{i}] missing locator/source")
            if not item.get("quote") and not item.get("verbatim_quote"):
                issues.append(f"facts_sources[{i}] missing quote")
    return issues


def run_pipeline(case_path: str, task_path: str, input_dir: str,
                 work_dir: str, output_dir: str):
    """Discover -> skeleton -> scaffold. Returns dict of outcomes."""
    outcome = {"errors": [], "steps": []}
    summary = build_input_summary(case_path, task_path, input_dir, work_dir)
    outcome["summary"] = summary
    outcome["errors"].extend(summary.get("errors", []))

    case_id = summary.get("case_id")
    _, rpath, rerr = build_skeleton(
        case_id, summary.get("output_contract"), output_dir,
        summary.get("summary_file"))
    outcome["result_path"] = rpath
    if rerr:
        outcome["errors"].append(rerr)
    outcome["steps"].append({"phase": "skeleton", "ok": rerr is None,
                             "path": rpath})

    paths, serr = build_scaffolds(summary, work_dir, output_dir)
    outcome["scaffold_paths"] = paths
    if serr:
        outcome["errors"].append(serr)
    outcome["steps"].append({"phase": "scaffold", "ok": serr is None,
                             "paths": paths})

    # Record intermediates into result.json so downstream consumers can find them.
    if rpath and os.path.isfile(rpath):
        merge_patch(rpath, {"intermediates": {
            "plan": paths.get("plan"),
            "figure_model": paths.get("figure_model"),
            "evidence_map": paths.get("evidence_map"),
        }}, "register_intermediates", "scaffolds registered into result.json")
    return outcome
