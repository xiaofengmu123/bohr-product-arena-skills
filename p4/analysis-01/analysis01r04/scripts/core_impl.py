#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Core helpers for the sci-data-cleaning skill.

Design contract (deterministic, standard library only):
  * Never fabricate data. Every computed value comes from an input file or a
    recorded, explicit rule; anything the script cannot verify is marked as
    pending_human / uncertainty instead of guessed.
  * Non-destructive: cleaning is annotation-first. Original values are kept in
    a verbatim snapshot; every change is written to a per-cell change map so it
    can be audited and reversed.
  * Idempotent: two runs over the same input produce byte-identical artifacts
    except for timestamps, which are metadata only.

This module contains no entry point; scripts/main.py and scripts/selftest.py
import it.
"""

import csv
import datetime
import hashlib
import io
import json
import math
import os
import re
import statistics
import unicodedata

# ---------------------------------------------------------------------------
# Basic time / text I/O
# ---------------------------------------------------------------------------

def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def file_sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def read_text_robust(path, max_bytes=2 * 1024 * 1024):
    """Read a text file tolerating BOM/bad encodings.

    Returns a dict with ``text``, ``encoding``, ``degraded`` and ``error``.
    Never raises; a failure is reported in ``error`` and the caller decides
    how to degrade.
    """
    out = {"text": "", "encoding": None, "degraded": False, "error": None,
           "truncated": False}
    if not os.path.exists(path):
        out["error"] = "file not found"
        return out
    if os.path.isdir(path):
        out["error"] = "path is a directory"
        return out
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        out["error"] = "stat failed: %s" % exc
        return out
    if size > max_bytes:
        out["truncated"] = True
    try:
        with open(path, "rb") as f:
            raw = f.read(max_bytes)
    except OSError as exc:
        out["error"] = "read failed: %s" % exc
        return out

    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        out["text"] = text
        out["encoding"] = enc
        if "\ufffd" in text:
            out["degraded"] = True
        return out
    # latin-1 decodes every byte, so the loop always returns.
    out["text"] = raw.decode("latin-1", errors="replace")
    out["encoding"] = "latin-1"
    out["degraded"] = True
    return out


def parse_json_robust(text, default):
    """Parse JSON tolerating a UTF-8 BOM and trailing whitespace.

    Returns ``(parsed, status)`` where status is ``ok`` or ``error``.
    """
    if text is None:
        return default, "error"
    cleaned = text.lstrip("\ufeff").strip()
    if not cleaned:
        return default, "error"
    try:
        return json.loads(cleaned), "ok"
    except ValueError:
        return default, "error"


def normalize_key(s):
    """Normalize a header for fuzzy matching: lowercase, NFKD, alnum only."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


# ---------------------------------------------------------------------------
# Delimited-file discovery and parsing
# ---------------------------------------------------------------------------

TABULAR_SUFFIXES = (".csv", ".tsv", ".txt", ".tab", ".dat")
DICT_HINTS = ("dict", "dictionary", "field", "codebook", "schema", "数据字典", "字段", "变量")
RULES_HINTS = ("rule", "qc", "unit", "quality", "质控", "规则", "单位", "范围")
WORKORDER_HINTS = ("workorder", "work_order", "order", "instruction", "工单", "步骤", "流程")


def list_files(root):
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
        for fn in filenames:
            out.append(os.path.join(dirpath, fn))
    return sorted(out)


def _has_any(name, hints):
    low = name.lower()
    return any(h in low for h in hints)


def discover_inputs(input_dir):
    files = list_files(input_dir)
    manifest = {"case_json": None, "task_md": None, "data": [], "dictionary": [],
                "rules": [], "workorder": [], "other": []}
    for f in files:
        base = os.path.basename(f)
        low = base.lower()
        if low == "case.json":
            manifest["case_json"] = f
        elif low == "task.md":
            manifest["task_md"] = f
        elif low.endswith(TABULAR_SUFFIXES):
            if _has_any(low, DICT_HINTS):
                manifest["dictionary"].append(f)
            elif _has_any(low, RULES_HINTS):
                manifest["rules"].append(f)
            elif _has_any(low, WORKORDER_HINTS):
                manifest["workorder"].append(f)
            else:
                manifest["data"].append(f)
        else:
            manifest["other"].append(f)
    # Never leave the main table unclassified: any remaining tabular file that
    # was not explicitly a dictionary/rules/workorder is treated as data.
    if not manifest["data"]:
        classified = set(manifest["dictionary"] + manifest["rules"] + manifest["workorder"])
        for f in files:
            if f.endswith(TABULAR_SUFFIXES) and f not in classified:
                manifest["data"].append(f)
    return manifest


def load_case(input_dir):
    manifest = discover_inputs(input_dir)
    case = {"case_id": None, "output_contract": {}, "raw": {},
            "manifest": manifest, "read_error": None}
    path = manifest["case_json"]
    if path is None:
        for f in manifest["other"]:
            if os.path.basename(f).lower() == "case.json":
                path = f
                break
    if path is None:
        case["read_error"] = "no case.json found under input directory"
        return case
    info = read_text_robust(path)
    parsed, status = parse_json_robust(info["text"], {})
    if not isinstance(parsed, dict):
        parsed = {}
        status = "error"
    case["raw"] = parsed
    case["case_id"] = (parsed.get("case_id") or parsed.get("id")
                       or parsed.get("challenge_id")
                       or os.path.basename(os.path.dirname(path))
                       or os.path.basename(path))
    contract = parsed.get("output_contract")
    if isinstance(contract, dict):
        case["output_contract"] = contract
    elif isinstance(contract, list):
        case["output_contract"] = {k: None for k in contract}
    if status != "ok":
        case["read_error"] = "case.json parsed with status=%s" % status
    if info.get("degraded"):
        case["read_error"] = (case["read_error"] or "") + " case.json encoding degraded"
    if info.get("error"):
        case["read_error"] = (case["read_error"] or "") + " case.json read: %s" % info["error"]
    return case


def contract_default(value):
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return {}
    return None


# ---------------------------------------------------------------------------
# Delimited parsing
# ---------------------------------------------------------------------------

def sniff_delimiter(text):
    sample_lines = [ln for ln in text.splitlines()[:10] if ln.strip()]
    if not sample_lines:
        return ","
    scores = {d: 0 for d in (",", "\t", ";", "|")}
    for ln in sample_lines:
        for d in scores:
            scores[d] += ln.count(d)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else ","


def read_delimited(path):
    """Parse a delimited file; return headers/rows with encoding metadata."""
    out = {"headers": [], "rows": [], "encoding": None, "delimiter": None,
           "degraded": False, "error": None, "row_count": 0}
    info = read_text_robust(path)
    out["encoding"] = info["encoding"]
    out["degraded"] = info["degraded"]
    if info["error"]:
        out["error"] = info["error"]
        return out
    delim = sniff_delimiter(info["text"])
    out["delimiter"] = delim
    try:
        reader = csv.reader(io.StringIO(info["text"]), delimiter=delim)
        rows = [r for r in reader if any(c.strip() for c in r)]
    except Exception as exc:  # csv rarely raises, but never crash the caller
        out["error"] = "csv parse failed: %s" % exc
        return out
    if not rows:
        out["error"] = "empty table"
        return out
    out["headers"] = rows[0]
    out["rows"] = rows[1:]
    out["row_count"] = len(out["rows"])
    return out


# ---------------------------------------------------------------------------
# Missing-value classification (no interpolation, four categories kept apart)
# ---------------------------------------------------------------------------

DEFAULT_MISSING_SENTINELS = {
    "", "na", "n/a", "nan", "null", "none", "n.a.", "-", "--", ".",
    "-999", "-9999", "-99999", "999", "9999", "#n/a", "#na", "missing",
    "空白", "缺失", "无数据",
}
DEFAULT_NOT_DETECTED_TOKENS = {
    "not detected", "not_detected", "nd", "n.d.", "nd.", "未检出", "未检测",
    "notdetected", "none detected",
}
DEFAULT_BELOW_LOD_TOKENS = {
    "below lod", "< lod", "<lod", "below loq", "< loq", "<loq", "below dl",
    "< dl", "<dl", "低于检出限", "低于检测限", "低于定量限", "检出限以下",
    "below detection", "below detection limit", "<mdl", "<mrl",
}


def classify_missing(raw, extra_missing=None, extra_notdetected=None,
                     extra_belowlod=None):
    """Classify one raw cell into one of the four non-imputed categories.

    ``true_zero`` is distinguished from ``entry_missing`` so the two are never
    merged. Categories: present / not_detected / below_lod / entry_missing /
    true_zero / unparsable.
    """
    s = "" if raw is None else str(raw).strip()
    missing = set(DEFAULT_MISSING_SENTINELS)
    nd = set(DEFAULT_NOT_DETECTED_TOKENS)
    lod = set(DEFAULT_BELOW_LOD_TOKENS)
    if extra_missing:
        missing.update(str(x).strip().lower() for x in extra_missing)
    if extra_notdetected:
        nd.update(str(x).strip().lower() for x in extra_notdetected)
    if extra_belowlod:
        lod.update(str(x).strip().lower() for x in extra_belowlod)

    low = s.lower()
    if low in nd:
        return "not_detected"
    if low in lod:
        return "below_lod"
    # "0" / "0.0" are true zeros, not missing.
    if low in ("0", "0.0", "0.00", "+0", "-0", "0e0", "0.0e0"):
        return "true_zero"
    if low in missing:
        return "entry_missing"
    if low.startswith("<") and re.match(r"^<\s*-?\d", s):
        return "below_lod"
    return "present"


# ---------------------------------------------------------------------------
# Numeric / unit handling (never silently drop units or precision)
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(
    r"^\s*(?P<num>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    r"\s*(?P<unit>.*?)\s*$"
)


def parse_number(raw):
    """Return (value, unit, ok, original). The leading numeric token is parsed
    as a float; any trailing text is returned as a possible unit and is never
    discarded. Thousand separators are not stripped unless the surrounding
    material says they are present.
    """
    original = "" if raw is None else str(raw).strip()
    if not original:
        return None, "", False, original
    m = _NUM_RE.match(original)
    if not m:
        return None, "", False, original
    try:
        value = float(m.group("num"))
    except ValueError:
        return None, m.group("unit"), False, original
    return value, m.group("unit").strip(), True, original


def is_numeric_column(values, threshold=0.6):
    """Conservative column-type guess: at least ``threshold`` of non-missing
    cells parse as numbers."""
    parsed = 0
    total = 0
    for v in values:
        cls = classify_missing(v)
        if cls in ("entry_missing", "not_detected", "below_lod", "unparsable"):
            continue
        total += 1
        val, _unit, ok, _orig = parse_number(v)
        if ok:
            parsed += 1
    if total == 0:
        return False
    return (parsed / total) >= threshold


# ---------------------------------------------------------------------------
# Conservative QC-bound extraction (regex only; always flagged for review)
# ---------------------------------------------------------------------------

_BOUND_PATTERNS = [
    re.compile(r"(?P<field>[A-Za-z_][A-Za-z0-9_.\-]*)\s*"
               r"(?:范围|区间|range|valid\s+range)\s*[::\-]?\s*"
               r"[\[\(]?\s*(?P<lo>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*"
               r"[,，;；]\s*(?P<hi>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"),
    re.compile(r"(?P<field>[A-Za-z_][A-Za-z0-9_.\-]*)\s*"
               r"(?:下限|min(?:imum)?)\s*[::\-]?\s*"
               r"(?P<lo>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"),
    re.compile(r"(?P<field>[A-Za-z_][A-Za-z0-9_.\-]*)\s*"
               r"(?:上限|max(?:imum)?)\s*[::\-]?\s*"
               r"(?P<hi>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"),
]


def extract_qc_bounds(text):
    """Extract candidate numeric QC bounds from free-text rules.

    This is mechanical evidence collection only: every match is tagged
    ``needs_review: true`` because a regex cannot adjudicate units or whether
    the bound is inclusive. The Agent must confirm each before use.
    """
    found = []
    if not text:
        return found
    for pat in _BOUND_PATTERNS:
        for m in pat.finditer(text):
            lo = m.groupdict().get("lo")
            hi = m.groupdict().get("hi")
            if lo is None and hi is None:
                continue
            found.append({
                "field": m.groupdict().get("field"),
                "lo": float(lo) if lo is not None else None,
                "hi": float(hi) if hi is not None else None,
                "needs_review": True,
            })
    return found


# ---------------------------------------------------------------------------
# Duplicate / near-duplicate detection (report, never auto-drop)
# ---------------------------------------------------------------------------

def group_rows(rows, key_indices, normalize=True):
    """Group rows by the given primary-key column indices.

    Returns a dict key -> list of row positions. ``normalize`` lowercases and
    collapses whitespace for approximate matching but keeps the original in the
    audit trail (no silent merge).
    """
    groups = {}
    for pos, row in enumerate(rows):
        parts = []
        for idx in key_indices:
            if idx < len(row):
                parts.append(str(row[idx]))
        raw = "|".join(parts)
        key = normalize_key(raw) if normalize else raw
        groups.setdefault(key, []).append(pos)
    return groups


# ---------------------------------------------------------------------------
# Field-level profile (numbers computed from data, never hand-written)
# ---------------------------------------------------------------------------

def profile_column(header, values, extra_missing=None, extra_nd=None,
                   extra_lod=None):
    counts = {"present": 0, "not_detected": 0, "below_lod": 0,
              "entry_missing": 0, "true_zero": 0, "unparsable": 0, "total": len(values)}
    numeric = []
    units = set()
    unparsable_samples = []
    for v in values:
        cls = classify_missing(v, extra_missing, extra_nd, extra_lod)
        counts[cls] += 1
        if cls == "present":
            val, unit, ok, orig = parse_number(v)
            if ok and unit == "":
                numeric.append(val)
            elif ok:
                units.add(unit)
            else:
                counts["unparsable"] += 1
                if len(unparsable_samples) < 5:
                    unparsable_samples.append(orig)
    stats = None
    if numeric:
        stats = {
            "n": len(numeric),
            "min": min(numeric),
            "max": max(numeric),
            "mean": statistics.fmean(numeric),
            "median": statistics.median(numeric),
        }
    return {"header": header, "counts": counts, "numeric_stats": stats,
            "units_detected": sorted(units),
            "unparsable_samples": unparsable_samples,
            "missing_rate": round(
                (counts["entry_missing"] + counts["not_detected"] +
                 counts["below_lod"]) / max(1, len(values)), 4)}


def profile_table(headers, rows, extra_missing=None, extra_nd=None, extra_lod=None):
    columns = []
    ncols = len(headers)
    for ci in range(ncols):
        values = [r[ci] if ci < len(r) else "" for r in rows]
        columns.append(profile_column(headers[ci], values, extra_missing,
                                      extra_nd, extra_lod))
    return {"headers": headers, "row_count": len(rows), "columns": columns}


# ---------------------------------------------------------------------------
# Deterministic artifact layout helpers
# ---------------------------------------------------------------------------

def safe_json(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        f.write(safe_json(obj) + "\n")


def read_json(path, default):
    info = read_text_robust(path)
    if info["error"]:
        return default, "error"
    return parse_json_robust(info["text"], default)
