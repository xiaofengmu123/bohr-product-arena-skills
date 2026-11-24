#!/usr/bin/env python3
"""Standard-library implementation for sci-figure-lean.

Responsibilities:
  * discover and read /input materials robustly (BOM, bad encoding, size cap)
  * bootstrap an early /app/output/result.json partial skeleton (S0)
  * parse delimited / markdown / whitespace tables with column typing + checks
  * hand-write a minimal editable SVG (<text> nodes, not outlined) as fallback
  * self-check deliverables (contract completeness, SVG editability, PDF/PNG magic)

Only the Python standard library is used, so the committed scripts run in a
bare interpreter. Figure rendering with matplotlib is done by the Agent at
runtime when `scientific-python` is available; these scripts never fake it.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import statistics
import time
import xml.etree.ElementTree as ET

SIZE_CAP = 2 * 1024 * 1024  # 2MB per input file
VALID_STATUS = {"complete", "partial", "failed", "insufficient_evidence", "input_error"}
SVG_NS = "{http://www.w3.org/2000/svg}"


# ------------------------------------------------------------------ io helpers
def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path):
    """Read UTF-8 text tolerating BOM and bad bytes. Returns (text, note)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return "", "unreadable: " + str(path)
    if size > SIZE_CAP:
        return "", "skipped (size > %d bytes): %s" % (SIZE_CAP, path)
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return fh.read(), ""
    except Exception as exc:  # noqa: BLE001
        return "", "read failed (%s): %s" % (exc, path)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    return path


def discover_inputs(input_dir):
    """List material files under input_dir with relative paths and sizes."""
    files = []
    if not os.path.isdir(input_dir):
        return files
    for root, _dirs, names in os.walk(input_dir):
        for name in sorted(names):
            p = os.path.join(root, name)
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            rel = os.path.relpath(p, input_dir).replace("\\", "/")
            files.append({
                "path": p, "rel": rel, "size": size,
                "ext": os.path.splitext(name)[1].lower(),
            })
    return files


# --------------------------------------------------------------- case handling
def default_contract():
    """Fallback contract when case.json carries no explicit output_contract."""
    return {
        "editable_source": None,
        "rendered_exports": None,
        "sources_and_changes": None,
        "data_map": None,
        "annotation_checks": None,
    }


def load_case(input_dir):
    """Find and parse case.json; degrade instead of crashing."""
    degradations = []
    case = {}
    case_path = os.path.join(input_dir, "case.json")
    text, note = read_text(case_path)
    if note:
        degradations.append(note)
    if text.strip():
        try:
            case = json.loads(text)
        except json.JSONDecodeError as exc:
            degradations.append("case.json invalid JSON: %s" % exc)
            m = (re.search(r'"case_id"\s*:\s*"([^"]+)"', text)
                 or re.search(r'"id"\s*:\s*"([^"]+)"', text))
            case = {"case_id": m.group(1)} if m else {}
    else:
        degradations.append("case.json not found or empty")
    case_id = case.get("case_id") or case.get("id") or ""
    contract = (case.get("output_contract") or case.get("outputContract")
                or case.get("contract") or default_contract())
    if not isinstance(contract, dict):
        contract = default_contract()
        degradations.append("output_contract not a dict; used default")
    return case_id, contract, degradations, case_path


def build_skeleton(case_id, contract, degradations):
    """Early partial result.json: every contract field present but null."""
    now = utc_now()
    filled = {k: None for k in contract}
    return {
        "case_id": case_id,
        "status": "partial",
        "output_contract": filled,
        "artifacts": {},
        "sources": [],
        "data_map": [],
        "annotation_checks": {},
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "step": "S0_bootstrap", "time_utc": now, "status": "ok",
            "note": "partial result skeleton written before analysis; incremental updates follow.",
        }],
        "errors": [],
        "degradations": degradations,
        "runtime": {"started_utc": now},
    }


def bootstrap(input_dir, output_dir, work_dir):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    case_id, contract, degradations, case_path = load_case(input_dir)
    skeleton = build_skeleton(case_id, contract, degradations)
    skeleton["sources"] = [
        {
            "file": f["rel"], "size": f["size"], "ext": f["ext"],
            "parse": "pdf-text-tool/ocr-tool" if f["ext"] == ".pdf" else "read_text(utf-8-sig)",
        }
        for f in discover_inputs(input_dir)
    ]
    result_path = os.path.join(output_dir, "result.json")
    write_json(result_path, skeleton)
    contract_path = os.path.join(work_dir, "contract.json")
    write_json(contract_path, {
        "case_id": case_id, "output_contract": contract, "case_file": case_path,
    })
    return {
        "result": result_path, "contract": contract_path, "case_id": case_id,
        "fields": list(contract), "degradations": degradations,
    }


# ------------------------------------------------------------ table extraction
def _num(v):
    s = str(v).strip()
    if not s:
        return None
    s2 = s.replace(",", "")
    try:
        return float(s2)
    except ValueError:
        return None


def _parse_rows(text):
    lines = [ln.rstrip("\r\n") for ln in text.splitlines()]
    lines = [ln for ln in lines if ln.strip() != ""]
    if not lines:
        return [], "empty"
    if "\t" in lines[0]:
        return [ln.split("\t") for ln in lines], "tsv"
    if lines[0].lstrip().startswith("|"):
        rows = []
        for ln in lines:
            if re.fullmatch(r"[\s|:\-]+", ln):
                continue
            body = ln.strip()
            if body.startswith("|"):
                body = body[1:]
            if body.endswith("|"):
                body = body[:-1]
            rows.append([c.strip() for c in body.split("|")])
        return rows, "markdown"
    delim = None
    try:
        delim = csv.Sniffer().sniff("\n".join(lines[:3]), delimiters=",;").delimiter
    except csv.Error:
        pass
    if delim in (",", ";"):
        rows = [r for r in csv.reader(io.StringIO(text))]
        rows = [r for r in rows if any(c.strip() for c in r)]
        return rows, "csv(%s)" % delim
    return [ln.split() for ln in lines], "whitespace"


def infer_column(values):
    nonempty = [v for v in values if v is not None and str(v).strip() != ""]
    if not nonempty:
        return "empty"
    nums = [_num(v) for v in nonempty]
    if all(n is not None for n in nums):
        return "int" if all(n == int(n) for n in nums) else "float"
    return "text"


def _flag_outliers(nums, threshold=5.0):
    clean = [n for n in nums if n is not None and math.isfinite(n)]
    if len(clean) < 4:
        return []
    med = statistics.median(clean)
    mad = statistics.median([abs(n - med) for n in clean])
    if mad == 0:
        return []
    issues = []
    for i, n in enumerate(nums):
        if n is not None and abs(n - med) > threshold * 1.4826 * mad:
            issues.append({"index": i, "value": n, "note": "outlier vs median %.6g" % med})
    return issues


def _hint_for(name, ctype):
    n = str(name).lower()
    if ctype in ("int", "float"):
        if re.search(r"(error|err|sd|sem|ci|se|±)", n):
            return "error_bar"
        if re.search(r"(^x$|group|condition|sample|label|category|type)", n):
            return "x_or_category_numeric"
        return "y_numeric"
    return "x_or_category"


def extract_table_file(path, name, work_dir):
    text, note = read_text(path)
    if note:
        return {"name": name, "error": note}
    rows, dialect = _parse_rows(text)
    if len(rows) < 2:
        return {"name": name, "source": path, "dialect": dialect,
                "error": "insufficient rows (need header + data)"}
    header = [str(c).strip() for c in rows[0]]
    norm = []
    for r in rows[1:]:
        r = list(r) + [""] * (len(header) - len(r))
        norm.append(r[:len(header)])
    columns = []
    for j, h in enumerate(header):
        col_vals = [r[j] for r in norm]
        ctype = infer_column(col_vals)
        nums = [_num(v) for v in col_vals]
        missing = sum(1 for v in col_vals if str(v).strip() == "")
        col = {"name": h, "type": ctype, "missing": missing,
               "issues": _flag_outliers(nums) if ctype in ("int", "float") else [],
               "hint": _hint_for(h, ctype)}
        columns.append(col)
    result = {
        "name": name, "source": path, "dialect": dialect,
        "n_rows": len(norm), "columns": columns,
        "rows": [dict(zip(header, r)) for r in norm],
        "row_index_1based": [i + 2 for i in range(len(norm))],
    }
    if note:
        result["read_note"] = note
    out = os.path.join(work_dir, "t_%s.json" % name)
    write_json(out, result)
    return {
        "name": name, "out": out,
        "columns": [(c["name"], c["type"], c["missing"]) for c in columns],
        "issues": [{"col": c["name"], "issue": i} for c in columns for i in c["issues"]][:50],
    }


# ------------------------------------------------------------- SVG fallback
def escape_xml(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def write_minimal_svg(path, title, texts, width=800, height=600):
    """Minimal editable SVG: real <text> nodes, never outlined glyphs."""
    texts = texts or [title or "figure"]
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">'
        % (width, height, width, height),
        '<rect width="100%" height="100%" fill="white"/>',
        '<text x="40" y="40" font-family="sans-serif" font-size="20">%s</text>'
        % escape_xml(title),
    ]
    y = 80
    for t in texts:
        lines.append('<text x="40" y="%d" font-family="sans-serif" font-size="14">%s</text>'
                     % (y, escape_xml(t)))
        y += 24
    lines.append('</svg>')
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


# -------------------------------------------------------------- deliverable check
def _magic(path, n=8):
    try:
        with open(path, "rb") as fh:
            return fh.read(n)
    except OSError:
        return b""


def _file_kind(path):
    head = _magic(path)
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"\x89PNG"):
        return "png"
    if head.startswith(b"<?xml") or head.startswith(b"<svg"):
        return "svg"
    return "unknown"


def _svg_has_text(path):
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return False
    root = tree.getroot()
    return bool(root.findall(".//%stext" % SVG_NS) or root.findall(".//text"))


def check_deliverables(result_path, work_dir, output_dir):
    os.makedirs(work_dir, exist_ok=True)
    checks = []

    def add(sev, code, msg):
        checks.append({"severity": sev, "code": code, "message": msg})

    if not os.path.isfile(result_path):
        add("fail", "result_missing", result_path + " not found")
        report = {"ok": False, "result_path": result_path, "checks": checks}
        write_json(os.path.join(work_dir, "check_report.json"), report)
        return report

    text, _note = read_text(result_path)
    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        add("fail", "result_invalid_json", str(exc))
        report = {"ok": False, "result_path": result_path, "checks": checks}
        write_json(os.path.join(work_dir, "check_report.json"), report)
        return report

    add("pass" if result.get("case_id") else "fail", "case_id",
        "case_id=%r" % result.get("case_id"))
    status = result.get("status")
    add("pass" if status in VALID_STATUS else "fail", "status", "status=%r" % status)

    contract = result.get("output_contract")
    if not isinstance(contract, dict):
        add("fail", "contract_missing", "output_contract is not a dict")
        total, nulls = 0, 0
    else:
        total = len(contract)
        nulls = sum(1 for v in contract.values() if v is None)
        add("pass" if total > 0 else "warn", "contract_present",
            "%d fields present" % total)
        if nulls:
            sev = "warn" if status != "complete" else "fail"
            add(sev, "contract_nulls",
                "%d/%d fields null (must be explained in uncertainty)" % (nulls, total))

    for req, req_sev in (("sources", "warn"), ("uncertainty", "warn"),
                         ("change_log", "fail")):
        v = result.get(req)
        if isinstance(v, list) and len(v) > 0:
            add("pass", req, "%s present (%d)" % (req, len(v)))
        else:
            add(req_sev, req, "%s missing or empty" % req)

    artifacts = result.get("artifacts") or {}
    add("pass" if artifacts else "warn", "artifacts", "%d artifact(s)" % len(artifacts))
    for key, spec in artifacts.items():
        path = spec if isinstance(spec, str) else (spec.get("path") if isinstance(spec, dict) else None)
        if not path:
            continue
        abs_path = path if os.path.isabs(path) else os.path.join(output_dir, path)
        kind = _file_kind(abs_path) if os.path.isfile(abs_path) else "missing"
        if kind == "missing":
            add("fail", "artifact_missing", "%s -> %s" % (key, path))
        elif kind == "svg":
            add("pass" if _svg_has_text(abs_path) else "fail", "svg_editable",
                "%s editable SVG (%s)" % (key, path))
        else:
            add("pass", "artifact_format", "%s %s" % (key, kind))

    fails = [c for c in checks if c["severity"] == "fail"]
    report = {
        "ok": not fails, "result_path": result_path, "checks": checks,
        "summary": {
            "total": len(checks),
            "pass": sum(1 for c in checks if c["severity"] == "pass"),
            "warn": sum(1 for c in checks if c["severity"] == "warn"),
            "fail": len(fails),
        },
    }
    write_json(os.path.join(work_dir, "check_report.json"), report)
    return report
