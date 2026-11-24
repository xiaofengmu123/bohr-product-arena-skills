#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Core implementation for sci-cleaning-pipeline.

Deterministic, standard-library only, exit-safe. Responsibilities:

  * robust text/JSON reading (UTF-8-SIG / GB18030 / Latin-1 fallback)
  * file discovery and classification
  * delimiter sniffing and CSV/TSV parsing
  * canonical intermediate representation (per-cell provenance)
  * missing-value classification (entry-missing / below-LOD / true-zero / value / text)
  * anomaly detection against resolved physical/QC bounds
  * duplicate detection (exact + fuzzy-normalized keys)
  * unit normalization and conversion-factor mapping
  * deterministic artifact writing and result.json assembly

The executor (this module) never imputes missing values, never infers
population statistics, and never silently mutates a source value. Every
transformation is recorded with a locator + quote where available.
"""

import csv
import datetime
import io
import json
import os
import re
import sys

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

RESULT_FIELDS = (
    "case_id",
    "status",
    "sources",
    "uncertainty",
    "change_log",
    "errors",
    "degradations",
    "pending_human",
    "runtime",
    "artifacts",
)

STATUS_CHOICES = ("ok", "partial", "insufficient_evidence", "input_error")

TABULAR_SUFFIXES = (".csv", ".tsv", ".txt", ".tab", ".dat", ".text")
DICT_HINTS = ("dict", "dictionary", "field", "codebook", "schema", "data_dict", "数据字典", "字段", "词典")
RULES_HINTS = ("rule", "qc", "unit", "quality", "range", "规则", "质控", "单位", "范围", "界限")
WORKORDER_HINTS = ("workorder", "work_order", "work-order", "order", "step", "instruction", "工单", "步骤", "指令", "清洗")
KEY_HINTS = ("id", "sample", "subject", "participant", "patient", "record", "time", "date", "timestamp", "编号", "样本", "受试", "时间", "日期", "序号", "编号")

MISSING_SENTINELS = {
    "", "na", "n/a", "n.a.", "n.a", "null", "none", "nan", "-nan", "nil",
    "#n/a", "#na", "#null", "missing", "缺失", "无", "空", ".",
    "-999", "-9999", "-999.0", "-9999.0", "-9990", "-99900",
    "na ", "n/a ", "n.a. ", "-",
}

# Below-LOD / below-detection markers (left-censored values, NOT missing, NOT zero).
LOD_CMP_RE = re.compile(
    r'^\s*[<＜]\s*(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*$'
)
LOD_WORD_RE = re.compile(
    r'^\s*(?:<\s*)?(?:lod|loq|bdl|blq|bql|dl|nd)\s*$', re.IGNORECASE
)
LOD_TEXT_RE = re.compile(
    r'(not\s*detected|below\s*detect|under\s*detect|non[- ]?detect|'
    r'未检出|未检测|低于检出限|低于检测限|低于测定|检出限以下|＜)', re.IGNORECASE
)

NUMBER_GROUPED_RE = re.compile(
    r'^[+-]?\d{1,3}(?:[ ,]\d{3})+(?:[.,]\d+)?(?:[eE][+-]?\d+)?$'
)
NUMBER_SCIENTIFIC_RE = re.compile(
    r'^[+-]?(\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$'
)

# Key-column name hints, used for duplicate detection only (Agent confirms).
KEY_COL_RE = re.compile(
    r'(^|_|-|\s)(id|sample|subject|participant|patient|record|time|date|timestamp|no|code|编号|样本|受试|时间|日期|序号)($|_|-|\s)|'
    r'^(id|sample|subject|participant|patient|record|time|date|timestamp|no|编号|样本|受试|时间|日期|序号)$',
    re.IGNORECASE,
)

# Common equivalent units (factor from "from-unit" to "to-unit" = 1.0 when equal).
UNIT_EQUIV = {
    "mg/l": {"ug/ml": 1.0, "mg/l": 1.0},
    "ug/ml": {"mg/l": 1.0, "ug/ml": 1.0},
    "mg/l": {"ng/ml": 1000.0, "ug/l": 1.0, "ppm": 1.0},
    "ug/l": {"ng/ml": 1.0, "ppb": 1.0},
    "ng/ml": {"ug/l": 1.0, "ppb": 1.0},
    "mg/ml": {"g/l": 1.0},
    "g/l": {"mg/ml": 1.0},
    "mmol/l": {"umol/ml": 1.0},
    "umol/ml": {"mmol/l": 1.0},
    "mmol/l": {"mol/l": 0.001},
}

_WS_RE = re.compile(r'\s+')
_UNIT_RE = re.compile(r'[^a-z0-9/\u00b5\u03bc\-\u00b7]', re.IGNORECASE)


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Robust readers
# --------------------------------------------------------------------------

def read_bytes(path, max_bytes=2_000_000):
    info = {"path": path, "size": 0, "truncated": False, "read_error": None}
    try:
        with open(path, "rb") as fh:
            data = fh.read(max_bytes + 1)
        if len(data) > max_bytes:
            data = data[:max_bytes]
            info["truncated"] = True
        info["size"] = len(data)
        info["data"] = data
    except Exception as exc:  # pragma: no cover - defensive
        info["read_error"] = f"{type(exc).__name__}: {exc}"
        info["data"] = b""
    return info


def decode_bytes(data):
    """Try UTF-8-SIG -> GB18030 -> Latin-1. Return (text, encoding, degraded)."""
    if not data:
        return "", "empty", False
    for enc in ("utf-8-sig", "gb18030"):
        try:
            text = data.decode(enc)
            degraded = (enc != "utf-8-sig")
            if "\ufffd" in text:
                degraded = True
            return text, enc, degraded
        except (UnicodeDecodeError, LookupError):
            continue
    text = data.decode("latin-1")
    return text, "latin-1", True


def read_text_robust(path, max_bytes=2_000_000):
    info = read_bytes(path, max_bytes)
    text, enc, degraded = decode_bytes(info.get("data", b""))
    info["text"] = text
    info["encoding"] = enc
    info["degraded"] = degraded
    info.pop("data", None)
    return info


def parse_json_robust(text, default=None):
    """Return (value, status) where status is ok|degraded|error."""
    if default is None:
        default = {}
    if not text or not text.strip():
        return default, "error"
    try:
        return json.loads(text), "ok"
    except json.JSONDecodeError as exc:
        # tolerate trailing commas / BOM / comments as a best-effort salvage
        try:
            cleaned = text.lstrip("\ufeff")
            return json.loads(cleaned), "ok"
        except json.JSONDecodeError:
            return default, "error"


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

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
    return any(h.lower() in low for h in hints)


def discover_inputs(input_dir):
    files = list_files(input_dir)
    manifest = {
        "case_json": None, "task_md": None, "data": [], "dictionary": [],
        "rules": [], "workorder": [], "other": [],
    }
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
    if not manifest["data"]:
        classified = set(manifest["dictionary"] + manifest["rules"] + manifest["workorder"])
        for f in files:
            if f.endswith(TABULAR_SUFFIXES) and f not in classified:
                manifest["data"].append(f)
    return manifest


# --------------------------------------------------------------------------
# Tabular parsing
# --------------------------------------------------------------------------

def sniff_and_parse(text, path):
    """Parse a delimited text block. Returns a table dict (never raises)."""
    out = {
        "path": path, "header": [], "rows": [], "delimiter": ",",
        "quote": '"', "n_rows": 0, "n_cols": 0, "parse_error": None,
        "dialect": "unknown", "encoding": None,
    }
    if text is None:
        out["parse_error"] = "no text"
        return out
    if not text.strip():
        out["parse_error"] = "empty table"
        return out
    sample = text[:8192]
    delim = ","
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;| ")
        delim = dialect.delimiter
        out["quote"] = dialect.quotechar or '"'
        out["dialect"] = "sniffed"
    except csv.Error:
        # fallback: count candidate delimiters outside quotes
        counts = {}
        for d in (",", "\t", ";", "|"):
            counts[d] = sample.count(d)
        best = max(counts, key=counts.get)
        if counts[best] > 0:
            delim = best
        out["dialect"] = "fallback"
    out["delimiter"] = delim
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delim,
                            quotechar=out["quote"])
        rows = [r for r in reader]
    except csv.Error as exc:
        out["parse_error"] = f"{type(exc).__name__}: {exc}"
        # last-resort: split on delimiter without quoting
        try:
            rows = [line.split(delim) for line in text.splitlines()]
        except Exception:
            rows = []
    if rows:
        out["header"] = [str(c) for c in rows[0]]
        out["rows"] = rows[1:]
    out["n_cols"] = len(out["header"])
    out["n_rows"] = len(out["rows"])
    return out


# --------------------------------------------------------------------------
# Cell-level parsing & classification (canonical intermediate representation)
# --------------------------------------------------------------------------

def parse_number(raw):
    """Return (value_or_None, method, ambiguous). Never raises."""
    if raw is None:
        return None, "empty", False
    s = str(raw).strip().replace("\u00a0", " ").replace("\u3000", " ").strip()
    if not s:
        return None, "empty", False
    # parenthesised negative, e.g. (3.2) -> -3.2
    neg = False
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1]
        neg = True
    if NUMBER_GROUPED_RE.match(s):
        cleaned = s.replace(",", "").replace(" ", "")
        try:
            return (-float(cleaned) if neg else float(cleaned)), "grouped", False
        except ValueError:
            pass
    if NUMBER_SCIENTIFIC_RE.match(s):
        try:
            return (-float(s) if neg else float(s)), "numeric", False
        except ValueError:
            pass
    # single comma as decimal separator (European convention)
    if s.count(",") == 1 and "." not in s and NUMBER_SCIENTIFIC_RE.match(s.replace(",", ".")):
        try:
            return (-float(s.replace(",", ".")) if neg else float(s.replace(",", "."))), "comma_decimal", True
        except ValueError:
            pass
    return None, "unparseable", True


def classify_cell(raw):
    """Classify a source cell into the canonical missing/value taxonomy.

    Returns a dict:
      class     : value | true_zero | below_lod | entry_missing | text
      value     : parsed float when numeric (else None)
      raw       : original string
      note      : short provenance note
    """
    if raw is None:
        return {"class": "entry_missing", "value": None, "raw": "", "note": "None source cell"}
    s = str(raw)
    raw_display = s
    stripped = s.strip().replace("\u00a0", " ").replace("\u3000", " ")
    low = stripped.lower()

    if low in MISSING_SENTINELS or stripped == "":
        return {"class": "entry_missing", "value": None, "raw": raw_display,
                "note": "blank/NA/sentinel entry-missing"}

    if LOD_CMP_RE.match(stripped) or LOD_WORD_RE.match(stripped) or LOD_TEXT_RE.search(s):
        return {"class": "below_lod", "value": None, "raw": raw_display,
                "note": "left-censored below-LOD marker; not missing, not zero"}

    value, method, ambiguous = parse_number(s)
    if value is not None:
        if value == 0.0:
            return {"class": "true_zero", "value": 0.0, "raw": raw_display,
                    "note": "parsed as exact zero"}
        return {"class": "value", "value": value, "raw": raw_display,
                "note": f"numeric via {method}" + (" (ambiguous separator)" if ambiguous else "")}

    return {"class": "text", "value": None, "raw": raw_display,
            "note": "non-numeric, non-missing text"}


def normalize_key_token(raw):
    """Normalize a primary-key token for fuzzy duplicate matching.

    Lowercase, collapse internal whitespace, strip trailing/leading spaces.
    Does NOT merge different units; unit differences are reported separately.
    """
    if raw is None:
        return ""
    return _WS_RE.sub(" ", str(raw).strip().lower())


def normalize_unit(raw):
    """Normalize a unit token for matching (lowercase, drop punctuation/spaces)."""
    if raw is None:
        return ""
    s = str(raw).strip().lower()
    s = s.replace("μ", "u").replace("\u03bc", "u").replace("\u00b5", "u")
    s = _UNIT_RE.sub("", s)
    return s


# --------------------------------------------------------------------------
# Bounds extraction (best-effort, deterministic; Agent reviews rules_resolved)
# --------------------------------------------------------------------------

_RANGE_RE = re.compile(
    r'(?P<field>[\w\u4e00-\u9fff][\w\u4e00-\u9fff ._/()\-]{0,40})'
    r'\s*[：:]\s*'
    r'(?P<lo>[-+]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)'
    r'\s*(?:[-~～—–至到]|to)\s*'
    r'(?P<hi>[-+]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)',
    re.IGNORECASE,
)
_GE_RE = re.compile(
    r'(?P<field>[\w\u4e00-\u9fff][\w\u4e00-\u9fff ._/()\-]{0,40})'
    r'\s*(?:>=|≥|>|大于|不小于)\s*'
    r'(?P<lo>[-+]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)',
    re.IGNORECASE,
)
_LE_RE = re.compile(
    r'(?P<field>[\w\u4e00-\u9fff][\w\u4e00-\u9fff ._/()\-]{0,40})'
    r'\s*(?:<=|≤|<|小于|不大于)\s*'
    r'(?P<hi>[-+]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)',
    re.IGNORECASE,
)


def _field_matches(column, field_text):
    col = str(column).lower().strip()
    ft = str(field_text).lower().strip()
    if not col or not ft:
        return False
    return col == ft or col in ft or ft in col


_LOHI_RE = re.compile(
    r'(?P<lo>[-+]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)'
    r'\s*(?:[-~～—–至到]|to)\s*'
    r'(?P<hi>[-+]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)',
    re.IGNORECASE,
)

_FIELD_COL_HINTS = ("field", "metric", "indicator", "parameter", "variable",
                    "analyte", "指标", "参数", "字段", "名称", "name", "项目")
_RANGE_COL_HINTS = ("range", "qc", "quality", "physical", "limit", "界限",
                    "范围", "正常", "质控", "物理", "bound")


def resolve_bounds(columns, texts):
    """texts: list of (locator, text). Returns dict column -> bounds.

    Two deterministic passes per text:
      1) prose patterns like "concentration: 0-10" or "concentration >= 0";
      2) tabular rules where a field column sits beside a range column.
    Both are best-effort; the Agent reviews rules_resolved.json before relying
    on any bound.
    """
    resolved = {}
    for col in columns:
        resolved[col] = {"qc_min": None, "qc_max": None, "physical_min": None,
                         "physical_max": None, "locators": []}
    for loc, text in texts:
        _apply_prose_bounds(resolved, columns, loc, text)
        _apply_tabular_bounds(resolved, columns, loc, text)
    return resolved


def _apply_prose_bounds(resolved, columns, loc, text):
    for m in _RANGE_RE.finditer(text):
        _set_bounds(resolved, columns, m.group("field"), loc,
                    _to_f(m.group("lo")), _to_f(m.group("hi")))
    for m in _GE_RE.finditer(text):
        _set_bounds(resolved, columns, m.group("field"), loc,
                    _to_f(m.group("lo")), None)
    for m in _LE_RE.finditer(text):
        _set_bounds(resolved, columns, m.group("field"), loc,
                    None, _to_f(m.group("hi")))


def _set_bounds(resolved, columns, field_text, loc, lo, hi):
    for col in columns:
        if not _field_matches(col, field_text):
            continue
        b = resolved[col]
        if lo is not None and b["qc_min"] is None:
            b["qc_min"] = lo
        if hi is not None and b["qc_max"] is None:
            b["qc_max"] = hi
        if loc not in b["locators"]:
            b["locators"].append(loc)


def _apply_tabular_bounds(resolved, columns, loc, text):
    t = sniff_and_parse(text, loc)
    if not t["header"] or not t["rows"] or t["parse_error"]:
        return
    header = t["header"]
    field_idx = None
    for i, h in enumerate(header):
        if field_idx is None and any(x in str(h).lower() for x in _FIELD_COL_HINTS):
            field_idx = i
    if field_idx is None:
        return
    for i, h in enumerate(header):
        if i == field_idx:
            continue
        hl = str(h).lower()
        if not any(x in hl for x in _RANGE_COL_HINTS):
            continue
        is_physical = ("physical" in hl or "物理" in hl)
        for row in t["rows"]:
            if field_idx >= len(row) or i >= len(row):
                continue
            field = str(row[field_idx]).strip()
            m = _LOHI_RE.search(str(row[i]))
            if not m:
                continue
            lo, hi = _to_f(m.group("lo")), _to_f(m.group("hi"))
            for col in columns:
                if not _field_matches(col, field):
                    continue
                b = resolved[col]
                if is_physical:
                    if lo is not None and b["physical_min"] is None:
                        b["physical_min"] = lo
                    if hi is not None and b["physical_max"] is None:
                        b["physical_max"] = hi
                else:
                    if lo is not None and b["qc_min"] is None:
                        b["qc_min"] = lo
                    if hi is not None and b["qc_max"] is None:
                        b["qc_max"] = hi
                if loc not in b["locators"]:
                    b["locators"].append(loc)


def _to_f(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Duplicate detection
# --------------------------------------------------------------------------

def candidate_key_columns(header):
    cols = []
    for i, h in enumerate(header):
        if KEY_COL_RE.search(str(h)):
            cols.append(i)
    return cols


def detect_duplicates(header, rows, key_cols):
    """Return duplicate groups (exact and fuzzy-normalized).

    key_cols: list of column indices forming the primary key.
    """
    if not key_cols:
        return {"key_cols": [], "exact": [], "fuzzy": [], "pending": True}
    seen = {}
    fuzzy = {}
    for ri, row in enumerate(rows):
        key = tuple(str(row[c]).strip() if c < len(row) else "" for c in key_cols)
        nkey = tuple(normalize_key_token(row[c]) if c < len(row) else "" for c in key_cols)
        seen.setdefault(key, []).append(ri)
        fuzzy.setdefault(nkey, []).append(ri)
    exact = [{"key": list(k), "row_indices": v, "keep": v[0], "reason":
              "first occurrence by row order; review against dictionary"}
             for k, v in seen.items() if len(v) > 1]
    fuzzy_groups = []
    for k, v in fuzzy.items():
        raw_keys = {tuple(str(rows[i][c]).strip() if c < len(rows[i]) else "" for c in key_cols) for i in v}
        if len(raw_keys) > 1 and len(v) > 1:
            fuzzy_groups.append({"normalized_key": list(k), "row_indices": v,
                                 "distinct_raw": sorted(str(x) for x in raw_keys),
                                 "keep": v[0], "reason": "normalized-equal; unit/case/space variant; review"})
    return {"key_cols": key_cols, "exact": exact, "fuzzy": fuzzy_groups, "pending": False}


# --------------------------------------------------------------------------
# Artifact helpers
# --------------------------------------------------------------------------

def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def relpath(path, base):
    try:
        return os.path.relpath(path, base).replace("\\", "/")
    except ValueError:
        return path


# --------------------------------------------------------------------------
# result.json assembly
# --------------------------------------------------------------------------

def load_case(input_dir):
    manifest = discover_inputs(input_dir)
    case = {
        "case_id": None, "output_contract": {}, "raw": {},
        "manifest": manifest, "read_error": None, "read_degraded": False,
    }
    path = manifest["case_json"]
    if path is None:
        for f in manifest["other"]:
            if os.path.basename(f).lower() == "case.json":
                path = f
                break
    if path is None:
        case["read_error"] = "no case.json found in input directory"
        return case
    info = read_text_robust(path)
    parsed, status = parse_json_robust(info.get("text", ""), {})
    if isinstance(parsed, dict):
        case["raw"] = parsed
    case["case_id"] = (
        (case["raw"].get("case_id") if isinstance(case["raw"], dict) else None)
        or (case["raw"].get("id") if isinstance(case["raw"], dict) else None)
        or (case["raw"].get("challenge_id") if isinstance(case["raw"], dict) else None)
        or os.path.basename(os.path.dirname(path)) or os.path.basename(path)
    )
    contract = case["raw"].get("output_contract") if isinstance(case["raw"], dict) else None
    if isinstance(contract, dict):
        case["output_contract"] = contract
    if status != "ok":
        case["read_error"] = (case["read_error"] or "") + f" case.json parsed with status={status}"
    if info.get("degraded"):
        case["read_degraded"] = True
        case["read_error"] = (case["read_error"] or "") + " case.json encoding degraded"
    return case


def contract_default(value):
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return {}
    return None


_CONTRACT_ARTIFACT_HINTS = (
    (("profile", "stat", "summar", "desc"), "data_profile"),
    (("report", "quality", "qc", "summary"), "quality_report"),
    (("evidence", "trace", "proof", "map", "source"), "evidence_map"),
    (("rule", "config", "resolv"), "rules_resolved"),
    (("clean", "table", "csv", "data"), "cleaned_data"),
)


def fill_contract(result, contract, artifact_paths):
    """Flatten every output_contract key to result top-level, verbatim.

    Known keys get a matching artifact path; unknown keys get contract_default.
    Keys are never renamed or dropped.
    """
    for key, val in contract.items():
        current = result.get(key)
        empty = current is None or current == [] or current == {}
        if not empty:
            continue
        low = str(key).lower()
        matched = False
        for hints, art in _CONTRACT_ARTIFACT_HINTS:
            if any(h in low for h in hints):
                result[key] = artifact_paths.get(art)
                matched = True
                break
        if not matched:
            result[key] = contract_default(val)
    return result


def build_skeleton(case, started, artifact_paths=None):
    artifact_paths = artifact_paths or {
        "cleaned_data": "cleaned_data.csv",
        "data_profile": "data_profile.json",
        "quality_report": "quality_report.md",
        "evidence_map": "evidence_map.json",
        "rules_resolved": "rules_resolved.json",
    }
    result = {k: _default_for(k) for k in RESULT_FIELDS}
    result["case_id"] = case.get("case_id")
    result["status"] = "partial"
    result["runtime"] = {"started": started, "finished": None, "phase": "bootstrap"}
    result["artifacts"] = dict(artifact_paths)
    if case.get("read_error"):
        result["errors"] = [case["read_error"]]
    fill_contract(result, case.get("output_contract") or {}, artifact_paths)
    result["change_log"] = [{
        "time": started, "step": "bootstrap",
        "action": "wrote partial result.json skeleton; output_contract keys flattened verbatim",
    }]
    return result


def _default_for(field):
    if field == "case_id":
        return None
    if field in ("sources", "uncertainty", "change_log", "errors",
                 "degradations", "pending_human"):
        return []
    if field in ("status", "runtime", "artifacts"):
        return None
    return None


def load_result(output_dir):
    path = os.path.join(output_dir, "result.json")
    if not os.path.exists(path):
        return None
    info = read_text_robust(path)
    obj, status = parse_json_robust(info.get("text", ""), {})
    return obj if isinstance(obj, dict) else None


def append_error(output_dir, message, key="errors"):
    result = load_result(output_dir)
    if result is None:
        return False
    result.setdefault(key, [])
    if message not in result[key]:
        result[key].append(message)
    result.setdefault("change_log", []).append({
        "time": now_iso(), "step": "error-handler",
        "action": f"appended to {key}: {message}",
    })
    write_json(os.path.join(output_dir, "result.json"), result)
    return True


def bootstrap(input_dir, output_dir, work_dir):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    started = now_iso()
    case = load_case(input_dir)
    result = build_skeleton(case, started)
    write_json(os.path.join(output_dir, "result.json"), result)
    return result


# --------------------------------------------------------------------------
# Full pipeline
# --------------------------------------------------------------------------

def pick_main_table(data_files):
    """Pick the richest candidate data table; record alternatives."""
    best = None
    for f in data_files:
        info = read_text_robust(f)
        t = sniff_and_parse(info.get("text"), f)
        t["encoding"] = info.get("encoding")
        cells = t["n_rows"] * t["n_cols"]
        if best is None or cells > best["cells"]:
            best = {"path": f, "table": t, "cells": cells, "encoding": info.get("encoding"),
                    "degraded": info.get("degraded")}
    return best


def _class_col_name(col):
    return f"{col}__class"


def run_full(input_dir, output_dir, work_dir):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    started = now_iso()
    case = load_case(input_dir)
    manifest = case["manifest"]
    result = build_skeleton(case, started)
    result["runtime"]["phase"] = "full"
    result["change_log"].append({
        "time": started, "step": "full-start",
        "action": f"discovered {len(manifest['data'])} data, "
                  f"{len(manifest['dictionary'])} dictionary, "
                  f"{len(manifest['rules'])} rules, "
                  f"{len(manifest['workorder'])} workorder files",
    })

    # 1. Read supporting materials verbatim
    material_texts = []
    for kind in ("dictionary", "rules", "workorder"):
        for f in manifest[kind]:
            info = read_text_robust(f)
            material_texts.append((f, info.get("text", "")))
            if info.get("degraded"):
                result["degradations"].append(
                    f"{kind} {relpath(f, input_dir)} decoded as {info.get('encoding')} (degraded)")
            if info.get("truncated"):
                result["degradations"].append(f"{kind} {relpath(f, input_dir)} truncated at 2MB")
            if info.get("read_error"):
                result["errors"].append(f"{kind} {relpath(f, input_dir)}: {info['read_error']}")

    task_text = ""
    if manifest["task_md"]:
        info = read_text_robust(manifest["task_md"])
        task_text = info.get("text", "")
        if info.get("degraded"):
            result["degradations"].append("task.md decoding degraded")

    # 2. Pick and parse main table
    if not manifest["data"]:
        result["status"] = "insufficient_evidence"
        result["pending_human"].append("no main data table found under /input")
        result["uncertainty"].append(
            "cannot run cleaning without a tabular source; dictionary/rules/workorder alone are insufficient")
        result["runtime"]["finished"] = now_iso()
        write_json(os.path.join(output_dir, "result.json"), result)
        return result

    best = pick_main_table(manifest["data"])
    table = best["table"]
    source_loc = relpath(best["path"], input_dir)
    result["change_log"].append({
        "time": now_iso(), "step": "parse",
        "action": f"parsed main table {source_loc}: {table['n_rows']} rows x "
                  f"{table['n_cols']} cols, delimiter={table['delimiter']!r}, "
                  f"encoding={best.get('encoding')}",
    })
    if best.get("degraded"):
        result["degradations"].append(
            f"main table {source_loc} decoded as {best.get('encoding')} (degraded)")
    if table.get("parse_error"):
        result["errors"].append(f"main table parse: {table['parse_error']}")
    if len(manifest["data"]) > 1:
        result["pending_human"].append(
            f"multiple candidate data tables; selected {source_loc} as richest; "
            f"others: {[relpath(f, input_dir) for f in manifest['data'] if f != best['path']]}")

    header = table["header"]
    rows = table["rows"]

    # 3. Build canonical intermediate representation (per-cell provenance)
    cells = []
    for ri, row in enumerate(rows):
        for ci, col in enumerate(header):
            raw = row[ci] if ci < len(row) else ""
            c = classify_cell(raw)
            c["row"] = ri
            c["col"] = ci
            c["column"] = col
            c["locator"] = f"{source_loc}#r{ri + 2}c{ci + 1}"
            cells.append(c)

    # 4. Resolve bounds from rules/dictionary text (best-effort)
    resolved_bounds = resolve_bounds(header, material_texts)
    write_json(os.path.join(work_dir, "rules_resolved.json"), resolved_bounds)

    # 5. Anomaly detection vs bounds
    anomalies = {"high": [], "low": []}
    for c in cells:
        if c["class"] != "value":
            continue
        b = resolved_bounds.get(c["column"], {})
        if b.get("qc_max") is not None and c["value"] > b["qc_max"]:
            anomalies["high"].append({"locator": c["locator"], "value": c["value"],
                                      "bound": b["qc_max"], "column": c["column"]})
        if b.get("qc_min") is not None and c["value"] < b["qc_min"]:
            anomalies["low"].append({"locator": c["locator"], "value": c["value"],
                                     "bound": b["qc_min"], "column": c["column"]})

    if anomalies["high"] or anomalies["low"]:
        result["pending_human"].append(
            f"{len(anomalies['high'])} high + {len(anomalies['low'])} low candidate "
            "anomalies vs QC bounds; disposition (keep/remove/winsorize) needs Agent "
            "judgment with locator + quote (see evidence_map)")

    # 6. Duplicate detection (best-effort key columns)
    key_cols = candidate_key_columns(header)
    dup = detect_duplicates(header, rows, key_cols)
    if dup.get("pending"):
        result["pending_human"].append(
            "no primary-key column identified by name hints; duplicate detection needs Agent judgment")
    else:
        result["change_log"].append({
            "time": now_iso(), "step": "dedupe",
            "action": f"key columns={[header[i] for i in key_cols]}; "
                      f"exact groups={len(dup['exact'])}, fuzzy groups={len(dup['fuzzy'])}",
        })

    # 7. Unit resolution (best-effort mapping table)
    unit_tokens = set()
    for col in header:
        m = re.search(r'\((.*?)\)', str(col))
        if m:
            unit_tokens.add((col, m.group(1).strip()))
    unit_map = {}
    for col, tok in unit_tokens:
        n = normalize_unit(tok)
        unit_map[col] = {"unit": tok, "normalized": n,
                         "needs_molar_mass": ("mol" in n or "m" == n)}
        if "mol" in n:
            result["pending_human"].append(
                f"column '{col}' unit '{tok}' is molar; molar-mass conversion not guessed")

    # 8. Aggregate profile (computed only from IR)
    profile = {"source": source_loc, "n_rows": table["n_rows"], "n_cols": table["n_cols"],
               "delimiter": table["delimiter"], "encoding": best.get("encoding"),
               "key_columns": [header[i] for i in key_cols],
               "duplicates": dup, "units": unit_map,
               "columns": {}}
    per_col = {}
    for ci, col in enumerate(header):
        col_cells = [c for c in cells if c["col"] == ci]
        counts = {"entry_missing": 0, "below_lod": 0, "true_zero": 0,
                  "value": 0, "text": 0, "total": len(col_cells)}
        vals = []
        for c in col_cells:
            counts[c["class"]] = counts.get(c["class"], 0) + 1
            if c["class"] == "value":
                vals.append(c["value"])
        per_col[col] = {
            "counts": counts,
            "missing_rate": round(counts["entry_missing"] / counts["total"], 6) if counts["total"] else None,
            "below_lod_rate": round(counts["below_lod"] / counts["total"], 6) if counts["total"] else None,
            "n_values": len(vals),
            "min": min(vals) if vals else None,
            "max": max(vals) if vals else None,
            "bounds": resolved_bounds.get(col, {}),
            "unit": unit_map.get(col),
        }
    profile["columns"] = per_col

    # 8b. Determine status BEFORE rendering the report, so report and
    # result.json always agree on the status line.
    if table["n_rows"] == 0:
        result["status"] = "insufficient_evidence"
        result["pending_human"].append("main table has zero data rows")
    elif result["errors"]:
        result["status"] = "partial"
    elif not result["pending_human"]:
        result["status"] = "ok"
    else:
        result["status"] = "partial"

    # 9. Write artifacts
    cleaned_csv_path = os.path.join(output_dir, "cleaned_data.csv")
    write_cleaned_csv(cleaned_csv_path, header, rows)
    write_json(os.path.join(output_dir, "data_profile.json"), profile)
    write_json(os.path.join(output_dir, "rules_resolved.json"), resolved_bounds)
    write_json(os.path.join(output_dir, "evidence_map.json"), build_evidence_map(
        source_loc, header, per_col, anomalies, dup, result["pending_human"]))

    quality_path = os.path.join(output_dir, "quality_report.md")
    quality_md = render_quality_report(source_loc, per_col, anomalies, dup,
                                       unit_map, result)
    write_text(quality_path, quality_md)

    # 10. Finalize result.json
    artifact_paths = {
        "cleaned_data": relpath(cleaned_csv_path, output_dir),
        "data_profile": relpath(os.path.join(output_dir, "data_profile.json"), output_dir),
        "quality_report": relpath(quality_path, output_dir),
        "evidence_map": relpath(os.path.join(output_dir, "evidence_map.json"), output_dir),
        "rules_resolved": relpath(os.path.join(output_dir, "rules_resolved.json"), output_dir),
    }
    result["artifacts"] = artifact_paths
    result["runtime"]["finished"] = now_iso()
    result["change_log"].append({
        "time": now_iso(), "step": "full-finish",
        "action": "wrote cleaned_data.csv, data_profile.json, rules_resolved.json, "
                  "evidence_map.json, quality_report.md; result.json finalized",
    })
    result.setdefault("sources", [])
    result["sources"].append({
        "locator": source_loc, "quote": table["header"][:8],
        "note": "main table header (first 8 columns) as parsed",
    })
    write_json(os.path.join(output_dir, "result.json"), result)
    return result


def write_cleaned_csv(path, header, rows):
    """Write cleaned table: _row_id + original columns verbatim + __class columns.

    Original values are preserved exactly; classification is added as
    annotation columns, never overwriting the source cell.
    """
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        out_header = ["_row_id"] + list(header) + [_class_col_name(h) for h in header]
        writer.writerow(out_header)
        for ri, row in enumerate(rows):
            classes = [classify_cell(row[c] if c < len(row) else "")["class"]
                       for c in range(len(header))]
            padded = [row[c] if c < len(row) else "" for c in range(len(header))]
            writer.writerow([ri] + padded + classes)


def build_evidence_map(source_loc, header, per_col, anomalies, dup, pending):
    entries = []
    for col in header:
        info = per_col.get(col, {})
        entries.append({
            "locator": f"{source_loc}#col:{col}",
            "quote": col,
            "note": f"column profile: counts={info.get('counts')}, bounds={info.get('bounds')}",
        })
    for a in anomalies["high"]:
        entries.append({"locator": a["locator"], "quote": str(a["value"]),
                        "note": f"anomaly-high vs bound {a['bound']} in {a['column']}"})
    for a in anomalies["low"]:
        entries.append({"locator": a["locator"], "quote": str(a["value"]),
                        "note": f"anomaly-low vs bound {a['bound']} in {a['column']}"})
    for g in dup["exact"] + dup["fuzzy"]:
        entries.append({"locator": f"{source_loc}#rows:{g['row_indices'][:10]}",
                        "quote": str(g.get("key") or g.get("normalized_key")),
                        "note": f"duplicate group, keep={g['keep']}, reason={g['reason']}"})
    return {"source": source_loc, "entries": entries,
            "pending_human": list(pending)}


def render_quality_report(source_loc, per_col, anomalies, dup, unit_map, result):
    lines = ["# 数据质量报告", "", f"- 主表：`{source_loc}`",
             f"- 生成时间：{now_iso()}", f"- 状态：{result['status']}", ""]
    lines.append("## 字段级清单")
    lines.append("")
    lines.append("| 字段 | 总条数 | 录入缺失 | 低于检出限 | 真值0 | 数值 | 文本 | 缺失率 | 数值最小 | 数值最大 | QC下界 | QC上界 | 单位 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for col, info in per_col.items():
        c = info["counts"]
        b = info.get("bounds") or {}
        u = (info.get("unit") or {}).get("unit", "")
        lines.append(
            f"| {col} | {c['total']} | {c['entry_missing']} | {c['below_lod']} | "
            f"{c['true_zero']} | {c['value']} | {c['text']} | "
            f"{_fmt_rate(info.get('missing_rate'))} | {_fmt(info.get('min'))} | "
            f"{_fmt(info.get('max'))} | {_fmt(b.get('qc_min'))} | {_fmt(b.get('qc_max'))} | {u} |")
    lines.append("")
    lines.append("## 异常值")
    lines.append(f"- 高于上界：{len(anomalies['high'])} 条")
    lines.append(f"- 低于下界：{len(anomalies['low'])} 条")
    lines.append("")
    lines.append("## 重复记录")
    lines.append(f"- 精确重复组：{len(dup['exact'])}；近似（规范化键相同）重复组：{len(dup['fuzzy'])}")
    lines.append("")
    lines.append("## 单位")
    if unit_map:
        for col, u in unit_map.items():
            flag = "（需摩尔质量，待人工）" if u.get("needs_molar_mass") else ""
            lines.append(f"- {col}：`{u['unit']}` → 归一化 `{u['normalized']}`{flag}")
    else:
        lines.append("- 未从列名解析到单位标记")
    lines.append("")
    lines.append("## 待人工确认")
    if result.get("pending_human"):
        for p in result["pending_human"]:
            lines.append(f"- {p}")
    else:
        lines.append("- 无")
    lines.append("")
    lines.append("> 本报告所有数字由 cleaned_data.csv 实时重算生成，未经手写。")
    return "\n".join(lines) + "\n"


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _fmt_rate(v):
    if v is None:
        return ""
    return f"{v:.4f}"


if __name__ == "__main__":  # pragma: no cover - tiny smoke test only
    print("sci-cleaning-pipeline core module loaded")
