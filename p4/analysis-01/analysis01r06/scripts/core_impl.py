#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Core implementation for the sci-data-cleaning skill.

Deterministic, standard-library-only helpers shared by scripts/main.py and
scripts/selftest.py.

Design principle (intermediate representation + auditability): every decision
is first materialised as a *structured plan* (plan.json), then applied to the
data, then re-verified by re-deriving every reported number from the cleaned
table (field_model.json / quality_report.md).  No function here imputes a
missing or invalid value and none infers population statistics.
"""
import csv
import datetime
import hashlib
import io
import json
import os
import re

SCHEMA_VERSION = "1.1"

TABULAR_SUFFIXES = (".csv", ".tsv", ".txt", ".tab", ".dat")
DICT_HINTS = ("dict", "dictionary", "field", "codebook", "schema",
              "数据字典", "字段", "词典", "码表")
RULES_HINTS = ("rule", "qc", "unit", "quality", "control",
               "规则", "质控", "单位", "范围")
WORKORDER_HINTS = ("workorder", "work_order", "order", "task", "step",
                   "工单", "步骤", "instructions", "清洗")

# --------------------------------------------------------------------------
# low-level IO / encoding / JSON
# --------------------------------------------------------------------------
def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def file_sha1(path):
    try:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def read_bytes(path, max_bytes=2 * 1024 * 1024):
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return {"raw": b"", "size": 0, "truncated": False, "error": str(e)}
    truncated = len(raw) > max_bytes
    return {"raw": raw[:max_bytes], "size": len(raw), "truncated": truncated,
            "error": None}


def read_text_robust(path, max_bytes=2 * 1024 * 1024):
    """Read a text file tolerating BOM and common encodings; never raises."""
    got = read_bytes(path, max_bytes)
    if got["error"] is not None:
        return {"text": "", "encoding": None, "degraded": True,
                "truncated": False, "size": 0, "error": got["error"]}
    raw = got["raw"]
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    enc = "utf-8"
    text = None
    for candidate in ("utf-8", "gb18030", "gbk", "cp1252", "latin-1"):
        try:
            text = raw.decode(candidate)
            enc = candidate
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        text = raw.decode("latin-1", errors="replace")
        enc = "latin-1(replace)"
    degraded = got["truncated"] or enc in ("cp1252", "latin-1", "latin-1(replace)")
    return {"text": text, "encoding": enc, "degraded": degraded,
            "truncated": got["truncated"], "size": got["size"], "error": None}


def parse_json_robust(text, default):
    if not text or not text.strip():
        return default, "empty"
    try:
        return json.loads(text), "ok"
    except json.JSONDecodeError as e:
        return default, "bad_json: %s" % e


def rel_locator(path, base):
    """Portable locator relative to a base directory when possible."""
    try:
        rel = os.path.relpath(path, base)
        if not rel.startswith(".."):
            return rel.replace(os.sep, "/")
    except ValueError:
        pass
    return path.replace(os.sep, "/")


# --------------------------------------------------------------------------
# discovery
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


def _hits(name, hints):
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
            if _hits(low, DICT_HINTS):
                manifest["dictionary"].append(f)
            elif _hits(low, RULES_HINTS):
                manifest["rules"].append(f)
            elif _hits(low, WORKORDER_HINTS):
                manifest["workorder"].append(f)
            else:
                manifest["data"].append(f)
        else:
            manifest["other"].append(f)
    if not manifest["data"]:
        seen = set(manifest["dictionary"] + manifest["rules"] + manifest["workorder"])
        for f in files:
            if f.endswith(TABULAR_SUFFIXES) and f not in seen:
                manifest["data"].append(f)
    if manifest["case_json"] is None:
        for f in manifest["other"]:
            if os.path.basename(f).lower() == "case.json":
                manifest["case_json"] = f
                break
    return manifest


def load_case(input_dir):
    manifest = discover_inputs(input_dir)
    case = {"case_id": None, "output_contract": {}, "raw": {}, "manifest": manifest,
            "read_error": None}
    path = manifest["case_json"]
    if path is None:
        case["read_error"] = "no case.json found in input directory"
        case["case_id"] = os.path.basename(os.path.normpath(input_dir)) or "unknown"
        return case
    info = read_text_robust(path)
    parsed, status = parse_json_robust(info["text"], {})
    case["raw"] = parsed if isinstance(parsed, dict) else {}
    case["case_id"] = (case["raw"].get("case_id") or case["raw"].get("id")
                       or case["raw"].get("challenge_id")
                       or os.path.basename(os.path.dirname(path)) or os.path.basename(path))
    if isinstance(case["raw"].get("output_contract"), dict):
        case["output_contract"] = case["raw"]["output_contract"]
    if status != "ok":
        case["read_error"] = "case.json parsed with status=%s" % status
    if info.get("degraded") or info.get("error"):
        case["read_error"] = (case.get("read_error") or "") + " case.json encoding degraded"
    return case


# --------------------------------------------------------------------------
# table parsing
# --------------------------------------------------------------------------
def sniff_delimiter(text):
    lines = [ln for ln in text.splitlines() if ln.strip()][:20]
    if not lines:
        return ","
    counts = {",": 0, "\t": 0, ";": 0, "|": 0}
    for ln in lines:
        for ch in counts:
            counts[ch] += ln.count(ch)
    best = max(counts, key=lambda k: counts[k])
    return best if counts[best] > 0 else ","


def parse_table(text):
    delimiter = sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    raw_rows = [r for r in reader if any(c.strip() for c in r)]
    if not raw_rows:
        return {"headers": [], "rows": [], "delimiter": delimiter, "empty": True,
                "n_rows": 0}
    headers = [h.strip() for h in raw_rows[0]]
    if headers and headers[0].startswith("\ufeff"):
        headers[0] = headers[0].lstrip("\ufeff").strip()
    rows = []
    ragged = 0
    for r in raw_rows[1:]:
        if not any(c.strip() for c in r):
            continue
        if len(r) != len(headers):
            ragged += 1
        padded = (list(r) + [""] * len(headers))[:len(headers)]
        rows.append(dict(zip(headers, padded)))
    return {"headers": headers, "rows": rows, "delimiter": delimiter,
            "empty": False, "n_rows": len(rows), "ragged_rows": ragged}


# --------------------------------------------------------------------------
# numeric coercion / missing classification
# --------------------------------------------------------------------------
def coerce_number(s):
    t = (s or "").strip().replace(" ", "")
    if not t:
        return None
    t = t.replace("\u2212", "-").replace("\u2013", "-").replace("\u2014", "-")
    if re.fullmatch(r"[+-]?\d{1,3}(,\d{3})+(\.\d+)?([eE][+-]?\d+)?", t):
        t = t.replace(",", "")
    try:
        return float(t)
    except ValueError:
        return None


DEFAULT_ENTRY_MISSING = {"", "na", "n/a", "nan", "null", "none", "missing",
                         "no data", "-", "--", "\u2014", "\u2013", ".",
                         "-999", "-9999", "-999.0", "999", "9999", "无", "缺", "缺失"}
DEFAULT_NOT_DETECTED = {"nd", "n.d.", "not detected", "not_detected",
                        "not found", "未检测", "未检出"}
DEFAULT_BELOW_LOD = {"<lod", "<loq", "lod", "loq", "低于检出限", "低于定量限",
                     "低于检测限", "低于定量下限"}


def _norm_tokens(seq):
    return set((t or "").strip().lower() for t in seq if t is not None and str(t).strip() != "")


def classify_cell(raw, sentinels, not_detected, below_lod):
    """Return (class, value_or_None).

    Classes: entry_missing | not_detected | below_lod | true_zero |
    numeric | unrecognized.  Missing/invalid values are *never* imputed.
    """
    s = "" if raw is None else str(raw)
    t = s.strip()
    low = t.lower()
    if t == "" or low in _norm_tokens(sentinels):
        return ("entry_missing", None)
    if low in _norm_tokens(not_detected):
        return ("not_detected", None)
    if low in _norm_tokens(below_lod):
        return ("below_lod", None)
    if t.startswith("<") and re.search(r"[0-9]", t):
        return ("below_lod", t)
    num = coerce_number(t)
    if num is not None:
        if num == 0:
            return ("true_zero", num)
        return ("numeric", num)
    return ("unrecognized", t)


# --------------------------------------------------------------------------
# unit handling (dimensionally-safe; no molar-mass guessing)
# --------------------------------------------------------------------------
_PREFIX = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "\u03bc": 1e-6, "m": 1e-3,
           "c": 1e-2, "d": 1e-1, "": 1.0, "k": 1e3, "M": 1e6}
_MASS_VOL_RE = re.compile(
    r"^(?P<mass>[a-zA-Z\u03bc]*)g\s*/\s*(?P<vol>[a-zA-Z\u03bc]*)l$", re.IGNORECASE)


def normalize_unit(s):
    t = (s or "").strip().lower().replace(" ", "")
    t = t.replace("\u03bc", "u").replace("\u00b5", "u")
    t = t.replace("mcg", "ug").replace("per", "/").replace("l-1", "/l")
    if "/" not in t and t.endswith("l"):
        return t
    return t


def unit_info(unit):
    """Return dict(kind, canonical, factor) or kind='unknown'."""
    if unit is None or not str(unit).strip():
        return {"kind": "unknown", "canonical": None, "factor": None}
    u = normalize_unit(unit)
    if "mol" in u or u.endswith("m"):
        return {"kind": "molar", "canonical": u, "factor": None,
                "needs_molar_mass": True}
    m = _MASS_VOL_RE.match(u)
    if m:
        mp = _PREFIX.get(m.group("mass").lower())
        vp = _PREFIX.get(m.group("vol").lower())
        if mp is not None and vp is not None:
            return {"kind": "mass_per_volume", "canonical": "g/L",
                    "factor": mp / vp, "needs_molar_mass": False}
    if u in ("g/l", "ug/ml", "mg/l", "ng/ml", "ug/dl", "mg/dl", "ng/dl",
             "g/dl", "pg/ml", "ng/l"):
        # explicit fallback through regex above should already cover these
        pass
    return {"kind": "unknown", "canonical": u, "factor": None}


def safe_conversion_factor(src_unit, dst_unit):
    """Return (factor, note).  None factor => cannot convert safely."""
    a, b = unit_info(src_unit), unit_info(dst_unit)
    if src_unit is None or dst_unit is None or not str(src_unit).strip() or not str(dst_unit).strip():
        return 1.0, "no-unit"
    if a["kind"] == "unknown" or b["kind"] == "unknown":
        return None, "unknown-unit"
    if a["kind"] == "molar" or b["kind"] == "molar":
        if normalize_unit(src_unit) == normalize_unit(dst_unit):
            return 1.0, "same-unit"
        return None, "molar-mass-required"
    if a["kind"] == "mass_per_volume" and b["kind"] == "mass_per_volume":
        return a["factor"] / b["factor"], "mass-per-volume-prefix"
    return None, "incompatible"


def find_units_in_line(line):
    pat = re.compile(
        r"([a-zA-Z\u03bc]{0,4}g\s*/\s*[a-zA-Z\u03bc]{0,4}l|"
        r"[a-zA-Z\u03bc]{0,4}mol\s*/\s*[a-zA-Z\u03bc]{0,4}l|"
        r"\u03bcg\s*/\s*m[lL]|ug\s*/\s*m[lL])", re.IGNORECASE)
    return [m.group(0) for m in pat.finditer(line or "")]


def find_numbers_in_line(line):
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", line or "")]


_RANGE_KW = r"(?:range|范围|区间|正常值|参考范围)"
_NUM = r"(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"


def parse_qc_bounds(line):
    """Parse a two-sided QC bound from a rule line, e.g. 'range 0-20'.

    Returns (min, max) or None.  The hyphen in '0-20' is a range separator,
    not a minus sign; negative bounds are handled by an explicit separator
    scan so '0-20' -> (0.0, 20.0) and '-5-20' -> (-5.0, 20.0).
    """
    if not line:
        return None
    # keyword form: "range/范围 ... 0-20" or "0 ~ 20" / "0 to 20"
    m = re.search(
        _RANGE_KW + r"[^\d-]*" + _NUM + r"\s*(?:-|–|—|~|～|to|至|~)\s*" + _NUM,
        line, re.IGNORECASE)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return (min(lo, hi), max(lo, hi))
    # bracket form: "[0, 20]" / "(0, 20]"
    m = re.search(r"[\[\(]\s*" + _NUM + r"\s*,\s*" + _NUM + r"\s*[\]\)]", line)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return (min(lo, hi), max(lo, hi))
    return None


# --------------------------------------------------------------------------
# plan building (the structured intermediate representation)
# --------------------------------------------------------------------------
def _doc_evidence(text, base_path, input_dir, target_substr):
    """Find lines of a document mentioning target_substr; return evidence."""
    out = []
    lines = (text or "").splitlines()
    for i, ln in enumerate(lines, 1):
        if not ln.strip():
            continue
        if target_substr.lower() in ln.lower():
            out.append({"locator": "%s#L%d" % (rel_locator(base_path, input_dir), i),
                        "quote": ln.strip()[:300]})
            if len(out) >= 6:
                break
    return out


def build_plan(case, input_dir):
    """Build a conservative, evidence-linked cleaning plan from discovered docs.

    The plan is the *intermediate representation*: a machine-readable statement
    of every per-field decision plus the verbatim locator it came from.  The
    agent may refine it (it is a plain JSON file) before `full` executes it.
    """
    manifest = case["manifest"]
    data_file = manifest["data"][0] if manifest["data"] else None
    plan = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case["case_id"],
        "data_file": rel_locator(data_file, input_dir) if data_file else None,
        "delimiter": None,
        "n_header_fields": 0,
        "evidence_documents": {},
        "fields": [],
        "global": {
            "primary_key_candidates": [],
            "dedup": {"mode": "normalized", "keep": "first_most_complete",
                      "note": "conservative default; refine after reading workorder",
                      "locator": None, "quote": None},
            "no_imputation": True,
            "no_population_inference": True,
        },
    }
    # Load document texts for evidence.
    docs = {"dictionary": [], "rules": [], "workorder": []}
    for kind in ("dictionary", "rules", "workorder"):
        for f in manifest[kind]:
            info = read_text_robust(f)
            docs[kind].append({"path": f, "text": info["text"],
                               "degraded": info["degraded"],
                               "error": info["error"]})
            plan["evidence_documents"].setdefault(kind, []).append(
                {"path": rel_locator(f, input_dir), "encoding": info["encoding"],
                 "degraded": info["degraded"]})

    headers = []
    data_text = ""
    data_rows = []
    data_info = None
    if data_file:
        info = read_text_robust(data_file)
        data_info = info
        data_text = info["text"]
        parsed = parse_table(data_text)
        headers = parsed["headers"]
        data_rows = parsed["rows"]
        plan["delimiter"] = parsed["delimiter"]
        plan["n_header_fields"] = len(headers)

    # Per-column numeric fraction for light dtype/role inference.
    col_numeric = {}
    col_nonempty = {}
    for h in headers:
        vals = [r.get(h, "") for r in data_rows]
        ne = [v for v in vals if str(v).strip() != ""]
        col_nonempty[h] = len(ne)
        col_numeric[h] = sum(1 for v in ne if coerce_number(v) is not None)

    # candidate key columns: identifiers / sample / time / subject tokens
    key_tokens = ("id", "sample", "subject", "patient", "participant", "no.",
                  "编号", "样本", "样品", "受试", "患者", "时间", "date", "time",
                  "timestamp", "record", "记录")
    for h in headers:
        role = "measure"
        if _hits(str(h).lower(), key_tokens):
            role = "key"
        # A column with no numeric values at all and not a key is text.
        if role != "key" and col_nonempty.get(h, 0) > 0 and col_numeric.get(h, 0) == 0:
            role = "text"
        field = {
            "name": h,
            "role": role,
            "dtype": "numeric" if role == "measure" else "text",
            "unit": None,
            "convert_to": None,
            "conversion_factor": 1.0,
            "conversion_note": None,
            "entry_missing": sorted(DEFAULT_ENTRY_MISSING) if role != "text" else [""],
            "not_detected": sorted(DEFAULT_NOT_DETECTED) if role != "text" else [],
            "below_lod": sorted(DEFAULT_BELOW_LOD) if role != "text" else [],
            "qc_min": None,
            "qc_max": None,
            "dedup_key": role == "key",
            "precision": None,
            "evidence": {"dictionary": [], "rules": [], "workorder": []},
        }
        # attach evidence mentioning this header (exact-ish match)
        for kind in ("dictionary", "rules", "workorder"):
            for doc in docs[kind]:
                field["evidence"][kind].extend(
                    _doc_evidence(doc["text"], doc["path"], input_dir, str(h)))
        # light semantic extraction from rules lines
        for doc in docs["rules"]:
            for line in (doc["text"] or "").splitlines():
                if str(h).lower() not in line.lower():
                    continue
                if field["unit"] is None:
                    units = find_units_in_line(line)
                    if units:
                        field["unit"] = normalize_unit(units[0])
                if field["qc_min"] is None:
                    bounds = parse_qc_bounds(line)
                    if bounds:
                        field["qc_min"], field["qc_max"] = bounds
        if role == "key":
            plan["global"]["primary_key_candidates"].append(h)
        plan["fields"].append(field)

    # unit conversion discovery: look for "convert/换算/统一 to <unit>" tokens
    for doc in docs["workorder"] + docs["rules"]:
        for line in (doc["text"] or "").splitlines():
            units = find_units_in_line(line)
            if len(units) >= 2:
                src, dst = units[0], units[1]
                factor, note = safe_conversion_factor(src, dst)
                for field in plan["fields"]:
                    if field["unit"] and normalize_unit(field["unit"]) == normalize_unit(src):
                        field["convert_to"] = normalize_unit(dst)
                        field["conversion_factor"] = factor
                        field["conversion_note"] = note
    return plan, data_file, data_text, docs, data_info


# --------------------------------------------------------------------------
# execution: apply plan -> cleaned rows + per-field model
# --------------------------------------------------------------------------
def _normalize_key(v):
    t = (v or "").strip().lower()
    return re.sub(r"\s+", "", t)


def execute_plan(plan, data_text):
    """Apply plan to parsed data.  Returns cleaned_rows, stats, change entries.

    Never imputes; missing classes are preserved as markers.  Duplicates are
    removed by normalised real-key comparison and reported separately.
    """
    parsed = parse_table(data_text)
    headers = parsed["headers"]
    rows = parsed["rows"]
    field_by_name = {f["name"]: f for f in plan["fields"]}
    key_cols = [f["name"] for f in plan["fields"] if f.get("dedup_key")]
    if not key_cols:
        key_cols = [plan["global"]["primary_key_candidates"][0]] if plan["global"]["primary_key_candidates"] else []

    stats = {h: {"n": 0, "entry_missing": 0, "not_detected": 0, "below_lod": 0,
                 "true_zero": 0, "numeric": 0, "unrecognized": 0, "anomaly": 0,
                 "converted": 0} for h in headers}
    duplicate_groups = {}
    kept_rows = []
    removed_rows = []
    conversion_log = []
    anomaly_log = []

    seen_keys = {}
    for idx, row in enumerate(rows):
        if key_cols:
            key_tuple = tuple(_normalize_key(row.get(k, "")) for k in key_cols)
        else:
            key_tuple = tuple(str(row.get(h, "")) for h in headers)
        if key_tuple in seen_keys:
            duplicate_groups.setdefault(key_tuple, []).append(idx)
            removed_rows.append({"source_row": idx + 2,
                                 "key": dict(zip(key_cols, key_tuple)),
                                 "reason": "duplicate of row %d" % (seen_keys[key_tuple] + 2)})
            continue
        seen_keys[key_tuple] = idx
        new_row = {}
        for h in headers:
            f = field_by_name.get(h)
            raw = row.get(h, "")
            if f is None:
                new_row[h] = raw
                continue
            klass, val = classify_cell(raw, f.get("entry_missing", []),
                                       f.get("not_detected", []),
                                       f.get("below_lod", []))
            stats[h][klass] = stats[h].get(klass, 0) + 1
            stats[h]["n"] += 1
            if klass == "numeric":
                factor = f.get("conversion_factor")
                if isinstance(factor, (int, float)) and factor not in (None, 1.0):
                    converted = val * factor
                    stats[h]["converted"] += 1
                    conversion_log.append({
                        "field": h, "row": idx + 2, "from": val,
                        "factor": factor, "to": converted,
                        "unit": "%s -> %s" % (f.get("unit"), f.get("convert_to"))})
                    val = converted
                qmin, qmax = f.get("qc_min"), f.get("qc_max")
                if qmin is not None and qmax is not None and not (qmin <= val <= qmax):
                    stats[h]["anomaly"] += 1
                    anomaly_log.append({"field": h, "row": idx + 2, "value": val,
                                        "bounds": [qmin, qmax]})
                new_row[h] = _num_str(val)
            elif klass == "true_zero":
                new_row[h] = "0"
            elif klass == "below_lod":
                new_row[h] = val if val else "<LOD"
            elif klass == "not_detected":
                new_row[h] = "ND"
            elif klass == "entry_missing":
                new_row[h] = ""
            else:  # unrecognized -> preserve verbatim, never overwrite
                new_row[h] = raw
        kept_rows.append(new_row)

    field_model = {
        "schema_version": SCHEMA_VERSION,
        "recomputed_from": "cleaned_data.csv",
        "n_source_rows": len(rows),
        "n_rows_after_clean": len(kept_rows),
        "n_duplicates_removed": len(removed_rows),
        "n_duplicate_groups": len(duplicate_groups),
        "fields": [dict({"name": h, "role": field_by_name.get(h, {}).get("role", "measure"),
                         "unit": field_by_name.get(h, {}).get("unit"),
                         "converted_to": field_by_name.get(h, {}).get("convert_to"),
                         "conversion_factor": field_by_name.get(h, {}).get("conversion_factor")},
                        **stats[h]) for h in headers],
    }
    return {"headers": headers, "kept_rows": kept_rows, "removed_rows": removed_rows,
            "duplicate_groups": duplicate_groups, "field_model": field_model,
            "conversion_log": conversion_log, "anomaly_log": anomaly_log}


def _num_str(v):
    if isinstance(v, float):
        return repr(v)
    return str(v)


# --------------------------------------------------------------------------
# report / evidence / selfcheck
# --------------------------------------------------------------------------
def recompute_stats(headers, rows, plan):
    """Re-derive field stats directly from cleaned rows (for selfcheck)."""
    field_by_name = {f["name"]: f for f in plan["fields"]}
    stats = {h: {"n": 0, "entry_missing": 0, "not_detected": 0, "below_lod": 0,
                 "true_zero": 0, "numeric": 0, "unrecognized": 0, "anomaly": 0}
             for h in headers}
    for row in rows:
        for h in headers:
            f = field_by_name.get(h)
            raw = row.get(h, "")
            if f is None:
                continue
            klass, val = classify_cell(raw, f.get("entry_missing", []),
                                       f.get("not_detected", []),
                                       f.get("below_lod", []))
            stats[h][klass] = stats[h].get(klass, 0) + 1
            stats[h]["n"] += 1
            if klass == "numeric":
                qmin, qmax = f.get("qc_min"), f.get("qc_max")
                if qmin is not None and qmax is not None and not (qmin <= val <= qmax):
                    stats[h]["anomaly"] += 1
    return stats


def render_quality_report(field_model, plan, extra):
    lines = ["# Data Quality Report (auto-generated, numbers recomputed from cleaned_data.csv)",
             "",
             "- case_id: %s" % (plan.get("case_id") or ""),
             "- source rows: %d" % field_model["n_source_rows"],
             "- rows after cleaning: %d" % field_model["n_rows_after_clean"],
             "- duplicate rows removed: %d (groups: %d)" % (
                 field_model["n_duplicates_removed"], field_model["n_duplicate_groups"]),
             "- conversions applied: %d" % len(extra.get("conversion_log", [])),
             "- anomalies flagged (kept, not deleted): %d" % len(extra.get("anomaly_log", [])),
             "",
             "## Per-field problem list",
             "",
             "| field | role | unit | n | entry_missing | not_detected | below_lod | true_zero | numeric | unrecognized | anomaly | converted |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for f in field_model["fields"]:
        lines.append("| %s | %s | %s | %d | %d | %d | %d | %d | %d | %d | %d | %d |" % (
            f["name"], f["role"], f.get("unit") or "-", f["n"], f["entry_missing"],
            f["not_detected"], f["below_lod"], f["true_zero"], f["numeric"],
            f["unrecognized"], f["anomaly"], f.get("converted", 0)))
    lines.append("")
    if extra.get("conversion_log"):
        lines.append("## Unit conversions (reversible factor recorded)")
        for c in extra["conversion_log"]:
            lines.append("- field `%s` row %d: %s x %s = %s (%s)" % (
                c["field"], c["row"], c["from"], c["factor"], c["to"], c["unit"]))
        lines.append("")
    if extra.get("anomaly_log"):
        lines.append("## Anomaly flags (kept verbatim; review against QC bounds)")
        for a in extra["anomaly_log"]:
            lines.append("- field `%s` row %d: value %s outside [%s, %s]" % (
                a["field"], a["row"], a["value"], a["bounds"][0], a["bounds"][1]))
        lines.append("")
    if extra.get("removed_rows"):
        lines.append("## Duplicate rows removed")
        for r in extra["removed_rows"]:
            lines.append("- source row %d (%s): %s" % (
                r["source_row"], r["key"], r["reason"]))
        lines.append("")
    return "\n".join(lines) + "\n"


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def write_csv(path, headers, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def selfcheck(output_dir, input_dir=None):
    """Validate result.json + artifacts; recompute report numbers. Exit 0."""
    result_path = os.path.join(output_dir, "result.json")
    checks = []
    ok = True
    if not os.path.isfile(result_path):
        return {"ok": False, "checks": [{"item": "result.json exists", "pass": False,
                                          "detail": "missing"}]}
    info = read_text_robust(result_path)
    res, status = parse_json_robust(info["text"], {})
    if status != "ok":
        return {"ok": False, "checks": [{"item": "result.json valid JSON",
                                          "pass": False, "detail": status}]}
    checks.append({"item": "result.json valid JSON", "pass": True, "detail": ""})
    checks.append({"item": "case_id present", "pass": bool(res.get("case_id")),
                   "detail": str(res.get("case_id"))})
    checks.append({"item": "status present", "pass": res.get("status") in
                   ("ok", "partial", "insufficient_evidence", "input_error"),
                   "detail": str(res.get("status"))})

    # artifacts existence
    artifacts = res.get("artifacts") or {}
    for label, rel in (("cleaned_data", "cleaned_data.csv"), ("plan", "plan.json"),
                       ("field_model", "field_model.json"),
                       ("quality_report", "quality_report.md"),
                       ("evidence_map", "evidence_map.json")):
        p = os.path.join(output_dir, rel)
        checks.append({"item": "artifact %s exists" % rel,
                       "pass": os.path.isfile(p), "detail": p})

    # recompute: field_model numbers vs cleaned CSV
    rec = {"pass": False, "detail": "not attempted"}
    cleaned_path = os.path.join(output_dir, "cleaned_data.csv")
    plan_path = os.path.join(output_dir, "plan.json")
    model_path = os.path.join(output_dir, "field_model.json")
    if os.path.isfile(cleaned_path) and os.path.isfile(plan_path) and os.path.isfile(model_path):
        ct = read_text_robust(cleaned_path)
        cp = parse_table(ct["text"])
        pt = read_text_robust(plan_path)
        plan, _ = parse_json_robust(pt["text"], {})
        mt = read_text_robust(model_path)
        model, _ = parse_json_robust(mt["text"], {})
        if cp["headers"] and isinstance(plan, dict) and isinstance(model, dict):
            recomputed = recompute_stats(cp["headers"], cp["rows"], plan)
            mismatches = []
            for f in model.get("fields", []):
                h = f.get("name")
                if h not in recomputed:
                    continue
                for k in ("n", "entry_missing", "not_detected", "below_lod",
                          "true_zero", "numeric", "unrecognized", "anomaly"):
                    if recomputed[h].get(k) != f.get(k):
                        mismatches.append("%s.%s: model=%s recomputed=%s" % (
                            h, k, f.get(k), recomputed[h].get(k)))
            rec = {"pass": len(mismatches) == 0,
                   "detail": ("consistent" if not mismatches else "; ".join(mismatches[:5]))}
    checks.append({"item": "report numbers match cleaned table", **rec})

    # evidence map entries
    ev_path = os.path.join(output_dir, "evidence_map.json")
    if os.path.isfile(ev_path):
        et = read_text_robust(ev_path)
        ev, _ = parse_json_robust(et["text"], [])
        good = isinstance(ev, list) and all(
            isinstance(e, dict) and e.get("locator") and e.get("quote") for e in ev)
        checks.append({"item": "evidence_map locator+quote", "pass": good,
                       "detail": "%d entries" % (len(ev) if isinstance(ev, list) else 0)})
    else:
        checks.append({"item": "evidence_map locator+quote", "pass": False,
                       "detail": "missing"})

    # change_log structure
    cl = res.get("change_log") or []
    cl_ok = isinstance(cl, list) and all(
        isinstance(e, dict) and e.get("time") and e.get("step") and e.get("action")
        for e in cl)
    checks.append({"item": "change_log time/step/action", "pass": cl_ok,
                   "detail": "%d entries" % len(cl) if isinstance(cl, list) else "0"})

    all_ok = all(c["pass"] for c in checks)
    return {"ok": all_ok, "checks": checks}


# --------------------------------------------------------------------------
# contract backfill
# --------------------------------------------------------------------------
_ARTIFACT_HINTS = (
    (("clean", "table", "data"), "cleaned_data"),
    (("report", "quality", "qc"), "quality_report"),
    (("model", "profile", "stat", "summar"), "field_model"),
    (("plan", "spec", "config"), "plan"),
    (("evidence", "trace", "proof", "map"), "evidence_map"),
)


def _default_for(value):
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return {}
    return None


def fill_contract_keys(result, contract, artifacts):
    for key, val in (contract or {}).items():
        current = result.get(key)
        empty = current is None or current == [] or current == {}
        if not empty:
            continue
        low = str(key).lower()
        hit = None
        for hints, rel in _ARTIFACT_HINTS:
            if any(h in low for h in hints):
                hit = artifacts.get(rel)
                break
        result[key] = hit if hit is not None else _default_for(val)
    return result
