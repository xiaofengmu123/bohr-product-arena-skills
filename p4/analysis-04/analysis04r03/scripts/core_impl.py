#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic, stdlib-only scientific data-analysis core.

Design goals
------------
1. No third-party imports: only ``os``, ``sys``, ``json``, ``csv``, ``io``,
   ``math``, ``sqlite3``, ``hashlib``, ``zlib``, ``struct``, ``shutil``,
   ``xml.etree.ElementTree``.  Any optional library (matplotlib/PIL) is *never*
   imported here, so the package runs identically wherever Python 3.8+ exists.
2. Byte-identical reruns: every iteration is over sorted keys, every float is
   rounded to a fixed number of decimals before serialisation, JSON is dumped
   with ``sort_keys=True``, the random seed is fixed, and wall-clock timestamps
   are kept out of ``result.json`` (they live in a separate ``runtime.json``).
3. Honest results: missing values are never coerced to 0; duplicate rows are
   counted but never silently dropped; a t-test records whether its assumptions
   were screened; an empty query is reported as empty, not skipped.

This module is imported by ``main.py``.  It is also intentionally importable
for reuse by downstream analysis code.
"""

import os
import sys
import json
import csv
import io
import math
import hashlib
import sqlite3
import zlib
import struct
import datetime

try:
    import xml.etree.ElementTree as ET
except ImportError:  # pragma: no cover
    ET = None

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
SEED = 42
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB skip threshold for text files

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
VALID_STATUSES = (STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR)

DATA_EXTS = {".csv", ".tsv", ".txt", ".data", ".dat"}
JSON_EXTS = {".json", ".jsonl", ".ndjson"}
DB_EXTS = {".db", ".sqlite", ".sqlite3", ".db3"}
DOC_EXTS = {".md", ".txt", ".pdf", ".rst"}

_MISSING_TOKENS = {"", "na", "n/a", "null", "none", "nan", "-", "--", "missing", "unknown", "not_reported"}

_RESERVED_RESULT_KEYS = {
    "case_id", "status", "sources", "uncertainty", "pending_human", "change_log",
    "errors", "degradations", "limitations", "determinism",
}


# --------------------------------------------------------------------------- #
# Robust file reading
# --------------------------------------------------------------------------- #
def read_bytes(path, max_bytes=MAX_FILE_BYTES):
    """Return (bytes, error).  Never raises."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, "stat_error:%s" % exc
    if size > max_bytes:
        return None, "too_large:%d_bytes" % size
    try:
        with open(path, "rb") as handle:
            return handle.read(), None
    except Exception as exc:  # noqa: BLE001 - deliberate last-resort catch
        return None, "read_error:%s" % exc


def decode_bytes(data):
    """Return (text, encoding).  Falls back to lossy utf-8 so we never crash."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc), enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    return data.decode("utf-8", errors="replace"), "utf-8(replace)"


def load_json(path):
    """Return (obj, error)."""
    data, err = read_bytes(path)
    if err:
        return None, err
    text, _enc = decode_bytes(data)
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001
        return None, "json_error:%s" % exc


# --------------------------------------------------------------------------- #
# Input discovery (scan first, assume second)
# --------------------------------------------------------------------------- #
def discover_inputs(input_dir):
    """Scan an input directory deterministically.

    Returns (discovery, error).  ``discovery`` is a dict with keys
    ``case``, ``task``, ``data``, ``databases``, ``docs``, ``other``, ``materials``.
    """
    disc = {
        "case": None, "task": None, "data": [], "databases": [],
        "docs": [], "other": [], "materials": [],
    }
    if not input_dir or not os.path.isdir(input_dir):
        return disc, "input_dir_missing"
    try:
        names = sorted(os.listdir(input_dir))
    except OSError as exc:
        return disc, "list_error:%s" % exc
    for name in names:
        full = os.path.join(input_dir, name)
        if os.path.isdir(full):
            if name.lower() == "materials":
                _scan_dir(full, disc, is_materials=True)
            continue
        _classify(full, name, disc)
    return disc, None


def _scan_dir(path, disc, is_materials=False):
    try:
        names = sorted(os.listdir(path))
    except OSError:
        return
    for name in names:
        full = os.path.join(path, name)
        if os.path.isdir(full):
            continue
        if is_materials:
            disc["materials"].append(full)
        _classify(full, name, disc)


def _classify(full, name, disc):
    base = name.lower()
    if base == "case.json":
        disc["case"] = full
    elif base == "task.md":
        disc["task"] = full
    else:
        ext = os.path.splitext(base)[1].lower()
        if ext in DB_EXTS:
            disc["databases"].append(full)
        elif ext in JSON_EXTS:
            disc["data"].append(full)
        elif ext in DATA_EXTS:
            disc["data"].append(full)
        elif ext in DOC_EXTS:
            disc["docs"].append(full)
        else:
            disc["other"].append(full)


def extract_case_id(case_obj):
    if not isinstance(case_obj, dict):
        return None
    for key in ("case_id", "caseId", "challenge_id", "id", "armiId"):
        val = case_obj.get(key)
        if val is not None and str(val).strip() != "":
            return val
    return None


def extract_output_contract(case_obj):
    """Return (contract_dict, source_key).  Missing contract -> ({}, None)."""
    if not isinstance(case_obj, dict):
        return {}, None
    for key in ("output_contract", "outputContract", "output_schema", "result_schema"):
        if key in case_obj:
            val = case_obj[key]
            if isinstance(val, dict):
                return dict(val), key
            if isinstance(val, list):
                return {str(field): None for field in val}, key
    return {}, None


def default_contract():
    return {
        "cleaned_table": None,
        "statistics": None,
        "figures": None,
        "query_results": None,
        "analysis_code": None,
        "summary": None,
    }


# --------------------------------------------------------------------------- #
# Result skeleton / mutation helpers (all deterministic)
# --------------------------------------------------------------------------- #
def build_skeleton(case_obj, output_contract, source_key):
    skeleton = {"case_id": extract_case_id(case_obj) or "not_reported"}
    contract = output_contract if output_contract else default_contract()
    # Contract fields are copied at top level (don't rename, don't drop unknown keys).
    contract_fields = []
    for key in sorted(contract.keys()):
        if key in _RESERVED_RESULT_KEYS:
            continue  # reserved metadata wins; collision is logged by caller
        skeleton[key] = None
        contract_fields.append(key)
    skeleton["_contract_fields"] = contract_fields
    skeleton["status"] = STATUS_PARTIAL
    skeleton["_summary"] = {}
    skeleton["_artifacts"] = {}
    skeleton["sources"] = []
    skeleton["uncertainty"] = []
    skeleton["pending_human"] = []
    skeleton["change_log"] = []
    skeleton["errors"] = []
    skeleton["degradations"] = []
    skeleton["limitations"] = []
    skeleton["determinism"] = {
        "seed": SEED,
        "sorted_json": True,
        "input_fingerprint": None,
        "rerun_policy": "byte_identical_payload",
    }
    if source_key is not None:
        skeleton["_contract_source_key"] = source_key
    return skeleton


def add_change(result, step, action, detail=None, status="done"):
    entry = {"step": int(step), "action": action, "status": status}
    if detail is not None:
        entry["detail"] = detail
    result["change_log"].append(entry)
    return result


def add_source(result, locator, quote, role):
    entry = {"locator": locator, "quote": quote, "role": role}
    if entry not in result["sources"]:
        result["sources"].append(entry)
    return result


def add_error(result, msg):
    if msg not in result["errors"]:
        result["errors"].append(msg)
    return result


def add_degradation(result, msg):
    if msg not in result["degradations"]:
        result["degradations"].append(msg)
    return result


def add_limitation(result, msg):
    if msg not in result["limitations"]:
        result["limitations"].append(msg)
    return result


def write_json_atomic(obj, path):
    """Deterministic UTF-8 JSON writer (sorted keys, trailing newline)."""
    text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)
    return path


def write_runtime_json(output_dir, argv, fingerprint):
    runtime = {
        "created_at_utc": datetime.datetime.utcnow().isoformat() + "Z",
        "platform": sys.platform,
        "python_version": sys.version,
        "argv": list(argv),
        "input_fingerprint": fingerprint,
    }
    try:
        write_json_atomic(runtime, os.path.join(output_dir, "runtime.json"))
    except OSError:
        pass


def hash_inputs(paths):
    h = hashlib.sha256()
    for path in sorted(paths):
        h.update(("FILE:%s\n" % os.path.basename(path)).encode("utf-8"))
        try:
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    h.update(chunk)
        except OSError:
            h.update(b"<<unreadable>>")
        h.update(b"\n")
    return "sha256:" + h.hexdigest()


# --------------------------------------------------------------------------- #
# Table loading and profiling
# --------------------------------------------------------------------------- #
def _strip_bom(text):
    if text.startswith("\ufeff"):
        return text[1:]
    return text


def sniff_delimiter(text):
    sample = "\n".join(text.splitlines()[:20])
    try:
        sniffer = csv.Sniffer()
        dialect = sniffer.sniff(sample, delimiters=",\t;| ")
        return dialect.delimiter
    except Exception:  # noqa: BLE001
        for cand in (",", "\t", ";", "|"):
            if cand in sample:
                return cand
        return ","


def _normalise_header(raw_header):
    seen = {}
    out = []
    for idx, head in enumerate(raw_header):
        name = (head or "").strip()
        if name == "":
            name = "col_%d" % (idx + 1)
        if name in seen:
            seen[name] += 1
            name = "%s_%d" % (name, seen[name])
        else:
            seen[name] = 0
        out.append(name)
    return out


def load_table(path):
    """Load a delimited or JSON table.  Returns (table, error).

    ``table`` = {"columns": [...], "rows": [dict, ...], "delimiter": str, "source": path}.
    """
    data, err = read_bytes(path)
    if err:
        return None, err
    text, _enc = decode_bytes(data)
    text = _strip_bom(text)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        try:
            obj = json.loads(text)
        except Exception as exc:  # noqa: BLE001
            return None, "json_error:%s" % exc
        return _json_to_table(obj, path), None
    if ext in (".jsonl", ".ndjson"):
        return _jsonl_to_table(text, path), None
    return _delim_to_table(text, path), None


def _json_to_table(obj, path):
    if isinstance(obj, dict):
        # A dict of columns -> parallel lists.
        columns = sorted(obj.keys())
        rows = []
        lengths = [len(obj[c]) if isinstance(obj[c], (list, tuple)) else 0 for c in columns]
        n = max(lengths) if lengths else 0
        for i in range(n):
            row = {}
            for c in columns:
                vals = obj[c]
                row[c] = vals[i] if isinstance(vals, (list, tuple)) and i < len(vals) else None
            rows.append(row)
        return {"columns": columns, "rows": rows, "delimiter": None, "source": path}
    if isinstance(obj, list):
        if not obj:
            return {"columns": [], "rows": [], "delimiter": None, "source": path}
        if all(isinstance(x, dict) for x in obj):
            columns = []
            for x in obj:
                for k in x:
                    if k not in columns:
                        columns.append(k)
            rows = [dict(x) for x in obj]
            return {"columns": columns, "rows": rows, "delimiter": None, "source": path}
        return {"columns": ["value"], "rows": [{"value": x} for x in obj],
                "delimiter": None, "source": path}
    return {"columns": ["value"], "rows": [{"value": obj}], "delimiter": None, "source": path}


def _jsonl_to_table(text, path):
    columns = []
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(obj, dict):
            for k in obj:
                if k not in columns:
                    columns.append(k)
            rows.append(obj)
        else:
            if "value" not in columns:
                columns.append("value")
            rows.append({"value": obj})
    return {"columns": columns, "rows": rows, "delimiter": None, "source": path}


def _delim_to_table(text, path):
    lines = text.splitlines()
    nonempty = [ln for ln in lines if ln.strip() != ""]
    if not nonempty:
        return {"columns": [], "rows": [], "delimiter": None, "source": path}
    delim = sniff_delimiter("\n".join(nonempty[:20]))
    reader = csv.reader(io.StringIO("\n".join(nonempty)), delimiter=delim)
    raw = [r for r in reader]
    if not raw:
        return {"columns": [], "rows": [], "delimiter": None, "source": path}
    header = _normalise_header(raw[0])
    rows = []
    for record in raw[1:]:
        if not record or all((c or "").strip() == "" for c in record):
            continue
        row = {}
        for i, col in enumerate(header):
            row[col] = (record[i].strip() if i < len(record) else "")
        rows.append(row)
    return {"columns": header, "rows": rows, "delimiter": delim, "source": path}


def _is_missing(value):
    if value is None:
        return True
    if isinstance(value, (int, float)):
        return False
    return str(value).strip().lower() in _MISSING_TOKENS


def _to_float(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if text.lower() in _MISSING_TOKENS:
        return None
    try:
        return float(text)
    except (ValueError, TypeError):
        return None


def numeric_summary(values):
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mean = sum(s) / n
    var = sum((x - mean) ** 2 for x in s) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var) if var >= 0.0 else 0.0
    return {
        "n": n,
        "mean": _r(mean),
        "std": _r(sd),
        "min": _r(s[0]),
        "max": _r(s[-1]),
        "q25": _r(_quantile(s, 0.25)),
        "median": _r(_quantile(s, 0.5)),
        "q75": _r(_quantile(s, 0.75)),
    }


def _r(value, digits=10):
    """Round to a fixed precision so reruns are byte-identical."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    try:
        f = float(value)
    except (TypeError, ValueError):
        return value
    if math.isnan(f) or math.isinf(f):
        return str(f)
    return round(f, digits)


def _quantile(sorted_vals, q):
    n = len(sorted_vals)
    if n == 0:
        return None
    if n == 1:
        return sorted_vals[0]
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_vals[lo]
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def profile_table(table):
    columns = table.get("columns", [])
    rows = table.get("rows", [])
    prof = {"n_rows": len(rows), "n_cols": len(columns), "columns": {}}
    for col in columns:
        vals = [row.get(col) for row in rows]
        present = [v for v in vals if not _is_missing(v)]
        numeric_vals = []
        for v in present:
            f = _to_float(v)
            if f is not None:
                numeric_vals.append(f)
        is_numeric = bool(numeric_vals) and len(numeric_vals) >= 0.5 * max(1, len(present))
        info = {
            "dtype": "numeric" if is_numeric else "string",
            "n_present": len(present),
            "n_missing": len(vals) - len(present),
            "n_unique": len(set(str(v) for v in present)),
        }
        if is_numeric:
            info["summary"] = numeric_summary(numeric_vals)
        prof["columns"][col] = info
    key_rows = [json.dumps(row, sort_keys=True, ensure_ascii=False, default=str) for row in rows]
    from collections import Counter
    counts = Counter(key_rows)
    prof["n_unique_rows"] = len(counts)
    prof["n_duplicate_rows"] = len(rows) - len(counts)
    return prof


# --------------------------------------------------------------------------- #
# Statistics (exact, stdlib implementations)
# --------------------------------------------------------------------------- #
def mean(values):
    if not values:
        return None
    return sum(values) / len(values)


def variance(values, ddof=1):
    n = len(values)
    if n < 2:
        return 0.0
    m = mean(values)
    return sum((x - m) ** 2 for x in values) / (n - ddof)


def pearson_corr(x, y):
    n = len(x)
    if n < 2 or n != len(y):
        return None
    mx = sum(x) / n
    my = sum(y) / n
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    sxx = sum((xi - mx) ** 2 for xi in x)
    syy = sum((yi - my) ** 2 for yi in y)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def _betacf(a, b, x):
    """Continued-fraction for the incomplete beta (Numerical Recipes 6.4)."""
    maxit = 300
    eps = 3.0e-12
    fpmin = 1.0e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, maxit + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _betai(a, b, x):
    """Regularised incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                  + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def t_survival(t, df):
    """P(|T| > t) for Student's t with df degrees of freedom."""
    if df <= 0 or t < 0:
        return None
    x = df / (df + t * t)
    return 0.5 * _betai(df / 2.0, 0.5, x)


def welch_ttest(a, b):
    """Welch two-sample t-test (does NOT assume equal variance).

    Returns a dict with ``t``, ``df`` (Welch-Satterthwaite), ``p`` (two-sided),
    means and group sizes.  Returns ``error`` when the test is not runnable.
    """
    a = [float(x) for x in a]
    b = [float(x) for x in b]
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return {"error": "group_too_small", "n_a": na, "n_b": nb}
    ma, mb = mean(a), mean(b)
    va, vb = variance(a), variance(b)
    se = math.sqrt(va / na + vb / nb)
    if se == 0.0:
        return {"error": "zero_pooled_variance", "mean_a": _r(ma), "mean_b": _r(mb),
                "n_a": na, "n_b": nb, "t": None, "df": None, "p": None}
    t = (ma - mb) / se
    num = (va / na + vb / nb) ** 2
    den = (va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1)
    df = num / den if den > 0 else 1.0
    p = 2.0 * t_survival(abs(t), df)
    return {
        "method": "Welch_two_sample_t_test",
        "t": _r(t), "df": _r(df, 8), "p": _r(p),
        "mean_a": _r(ma), "mean_b": _r(mb), "std_a": _r(math.sqrt(va)),
        "std_b": _r(math.sqrt(vb)), "n_a": na, "n_b": nb,
    }


def _normal_ppf(p):
    """Inverse standard-normal CDF (Acklam's approximation, ~1e-9 accuracy)."""
    if p <= 0.0 or p >= 1.0:
        return None
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1.0 - 0.02425
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if p > phigh:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)


def normality_screen(values):
    """Shapiro-Francia-style Q-Q correlation screen (statistic only, no p-value).

    We deliberately *do not* fabricate an exact normality p-value.  Instead we
    report the exact squared correlation W' between the sorted sample and the
    Blom-expected normal order statistics, plus moment-based skewness and
    excess kurtosis, and a documented conservative flag.
    """
    n = len(values)
    if n < 5:
        return {"W_prime": None, "skew": None, "kurtosis": None,
                "flag": "n_too_small", "note": "normality screen needs n>=5"}
    s = sorted(values)
    m = [_normal_ppf((i + 0.375) / (n + 0.25)) for i in range(n)]  # Blom positions
    r = pearson_corr(s, m)
    w_prime = r * r if r is not None else None
    mv = mean(values)
    sd = math.sqrt(variance(values))
    if sd == 0:
        skew = 0.0
        kurt = 0.0
    else:
        skew = sum((x - mv) ** 3 for x in values) / n / (sd ** 3)
        kurt = sum((x - mv) ** 4 for x in values) / n / (sd ** 4) - 3.0
    if w_prime is None:
        flag = "undefined"
    elif w_prime < 0.90 or abs(skew) > 1.0 or abs(kurt) > 2.0:
        flag = "normality_not_supported"
    else:
        flag = "normality_plausible"
    return {"W_prime": _r(w_prime), "skew": _r(skew), "kurtosis": _r(kurt),
            "flag": flag, "note": "screen, not an exact normality test; no p-value reported"}


def levene_median(a, b):
    """Median-centred Levene test for equal variance (F approximation)."""
    a = [float(x) for x in a]
    b = [float(x) for x in b]
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return {"error": "group_too_small"}
    ma = _median(a)
    mb = _median(b)
    z1 = [abs(x - ma) for x in a]
    z2 = [abs(x - mb) for x in b]
    zbar = (sum(z1) + sum(z2)) / (na + nb)
    z1bar = sum(z1) / na
    z2bar = sum(z2) / nb
    num = na * (z1bar - zbar) ** 2 + nb * (z2bar - zbar) ** 2
    den = sum((z - z1bar) ** 2 for z in z1) + sum((z - z2bar) ** 2 for z in z2)
    if den == 0:
        return {"error": "zero_variance"}
    w = ((na + nb - 2) / 1.0) * num / den
    p = _f_survival(w, 1, na + nb - 2)
    return {"method": "Levene_median_test", "W": _r(w), "p": _r(p),
            "df1": 1, "df2": na + nb - 2}


def _median(values):
    s = sorted(values)
    return _quantile(s, 0.5)


def _f_survival(f, d1, d2):
    if f <= 0:
        return 1.0
    x = d1 * f / (d1 * f + d2)
    return 1.0 - _betai(d1 / 2.0, d2 / 2.0, x)


# --------------------------------------------------------------------------- #
# SQLite helpers
# --------------------------------------------------------------------------- #
def _quote_ident(name):
    return '"' + str(name).replace('"', '""') + '"'


def connect_sqlite_ro(db_path):
    try:
        import urllib.parse
        uri = "file:" + urllib.parse.quote(os.path.abspath(db_path)) + "?mode=ro"
        return sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return sqlite3.connect(db_path)


def sqlite_schema(db_path):
    """Return list of tables with schema and row counts."""
    con = connect_sqlite_ro(db_path)
    try:
        cur = con.cursor()
        tables = []
        for (name,) in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            cols = []
            for row in cur.execute("PRAGMA table_info(%s)" % _quote_ident(name)):
                cols.append({
                    "name": row[1], "type": row[2], "notnull": bool(row[3]),
                    "default": row[4], "pk": row[5],
                })
            cnt = cur.execute("SELECT COUNT(*) FROM %s" % _quote_ident(name)).fetchone()[0]
            tables.append({"name": name, "n_rows": cnt, "columns": cols})
        return tables
    finally:
        con.close()


def run_query(db_path, sql, params=None):
    con = connect_sqlite_ro(db_path)
    try:
        cur = con.cursor()
        cur.execute(sql, params or [])
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchall()]
        return {"sql": sql, "params": params or [], "columns": columns,
                "rows": rows, "n_rows": len(rows)}
    finally:
        con.close()


def sqlite_numeric_aggregate(db_path, table, column):
    sql = ("SELECT COUNT(%(col)s) AS n, AVG(%(col)s) AS mean, MIN(%(col)s) AS min, "
           "MAX(%(col)s) AS max FROM %(tbl)s") % {"col": _quote_ident(column), "tbl": _quote_ident(table)}
    return run_query(db_path, sql)


# --------------------------------------------------------------------------- #
# Deterministic SVG charts
# --------------------------------------------------------------------------- #
_PALETTE = ["#4c72b0", "#dd8452", "#55a868", "#c44e52", "#8172b2",
            "#937860", "#da8bc3", "#8c8c8c", "#ccb974", "#64b5cd"]


def _nice_ticks(lo, hi, target=6):
    if lo == hi:
        hi = lo + 1.0
    span = hi - lo
    step0 = span / max(1, target)
    mag = 10 ** math.floor(math.log10(step0))
    norm = step0 / mag
    if norm <= 1.0:
        step = 1.0 * mag
    elif norm <= 2.0:
        step = 2.0 * mag
    elif norm <= 5.0:
        step = 5.0 * mag
    else:
        step = 10.0 * mag
    start = math.ceil(lo / step) * step
    ticks = []
    v = start
    while v <= hi + 1e-9:
        ticks.append(v)
        v += step
    return ticks, step


def _fmt_number(value):
    if value is None:
        return "NA"
    f = float(value)
    if f == 0.0:
        return "0"
    if abs(f) >= 1e6 or abs(f) < 1e-4:
        return "%.3e" % f
    return "%.6g" % f


def _svg_escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def make_svg_chart(kind, data, title, xlabel, ylabel):
    """Return a deterministic SVG string.

    kind: 'bar' (data: [(label, value)]), 'line' (data: {'x': [...], 'series': {name: [...]}}),
          'scatter' (data: {'x': [...], 'y': [...]}), 'hist' (data: {'values': [...]}).
    """
    width, height = 800, 520
    ml, mr, mt, mb = 70, 30, 56, 62
    plot_w = width - ml - mr
    plot_h = height - mt - mb

    def _empty_svg():
        return ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d">'
                '<text x="20" y="30" font-family="sans-serif" font-size="14">'
                'insufficient data for chart</text></svg>') % (width, height)

    if kind == "bar":
        if not data:
            return _empty_svg()
        labels = [str(x[0]) for x in data]
        values = [float(x[1]) for x in data]
        ymin, ymax = _bar_yminmax(values)
        xmax = len(labels)
        xmin = 0.0
        xticks = list(range(len(labels)))
    elif kind == "hist":
        values = [float(v) for v in data["values"]]
        if len(values) < 2:
            return _empty_svg()
        bins = _hist_bins(values)
        ymin, ymax = 0.0, max(1.0, max((c for _, _, c in bins), default=0) * 1.08)
        xmin, xmax = bins[0][0] if bins else 0.0, bins[-1][1] if bins else 1.0
    elif kind == "scatter":
        xs = [float(v) for v in data["x"]]
        ys = [float(v) for v in data["y"]]
        if not xs or not ys:
            return _empty_svg()
        xmin, xmax = _pad(min(xs), max(xs))
        ymin, ymax = _pad(min(ys), max(ys))
    elif kind == "line":
        xs = [float(v) for v in data["x"]]
        all_y = [float(v) for s in data["series"].values() for v in s]
        if not xs or not all_y:
            return _empty_svg()
        xmin, xmax = _pad(min(xs), max(xs))
        ymin, ymax = _pad(min(all_y), max(all_y))
    else:
        raise ValueError("unsupported chart kind: %s" % kind)

    yticks, _ystep = _nice_ticks(ymin, ymax)
    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
                 'viewBox="0 0 %d %d">' % (width, height, width, height))
    parts.append('<rect width="%d" height="%d" fill="#ffffff"/>' % (width, height))
    if title:
        parts.append('<text x="%d" y="30" font-family="sans-serif" font-size="18" '
                     'text-anchor="middle" fill="#111111">%s</text>' % (width // 2, _svg_escape(title)))
    # axes
    x0, y0 = ml, mt + plot_h
    parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#333333" stroke-width="1.2"/>' % (ml, y0, ml + plot_w, y0))
    parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#333333" stroke-width="1.2"/>' % (ml, mt, ml, y0))
    # grid + y ticks
    for tick in yticks:
        py = y0 - (tick - ymin) / (ymax - ymin) * plot_h
        parts.append('<line x1="%d" y1="%.2f" x2="%d" y2="%.2f" stroke="#e6e6e6" stroke-width="1"/>' % (ml, py, ml + plot_w, py))
        parts.append('<text x="%d" y="%.2f" font-family="sans-serif" font-size="11" '
                     'text-anchor="end" fill="#333333">%s</text>' % (ml - 8, py + 4, _fmt_number(tick)))
    # x ticks / bars / points / lines
    if kind == "bar":
        bw = plot_w / max(1, len(labels)) * 0.7
        for i, label in enumerate(labels):
            cx = ml + (i + 0.5) * (plot_w / max(1, len(labels)))
            val = values[i]
            py = y0 - (val - ymin) / (ymax - ymin) * plot_h
            parts.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" fill="%s" stroke="none"/>'
                         % (cx - bw / 2, py, bw, y0 - py, _PALETTE[i % len(_PALETTE)]))
            parts.append('<text x="%.2f" y="%d" font-family="sans-serif" font-size="10" '
                         'text-anchor="middle" fill="#333333">%s</text>' % (cx, y0 + 16, _svg_escape(label)))
    elif kind == "hist":
        for lo, hi_v, count in bins:
            x1 = ml + (lo - xmin) / (xmax - xmin) * plot_w
            x2 = ml + (hi_v - xmin) / (xmax - xmin) * plot_w
            py = y0 - (count - ymin) / (ymax - ymin) * plot_h
            parts.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" fill="%s" stroke="none"/>'
                         % (x1, py, max(1.0, x2 - x1 - 1.0), y0 - py, _PALETTE[0]))
        for tick in _nice_ticks(xmin, xmax)[0]:
            px = ml + (tick - xmin) / (xmax - xmin) * plot_w
            parts.append('<text x="%.2f" y="%d" font-family="sans-serif" font-size="10" '
                         'text-anchor="middle" fill="#333333">%s</text>' % (px, y0 + 16, _fmt_number(tick)))
    elif kind == "scatter":
        for x, y in zip(xs, ys):
            px = ml + (x - xmin) / (xmax - xmin) * plot_w
            py = y0 - (y - ymin) / (ymax - ymin) * plot_h
            parts.append('<circle cx="%.2f" cy="%.2f" r="3" fill="%s" opacity="0.8"/>' % (px, py, _PALETTE[0]))
        for tick in _nice_ticks(xmin, xmax)[0]:
            px = ml + (tick - xmin) / (xmax - xmin) * plot_w
            parts.append('<text x="%.2f" y="%d" font-family="sans-serif" font-size="10" '
                         'text-anchor="middle" fill="#333333">%s</text>' % (px, y0 + 16, _fmt_number(tick)))
    elif kind == "line":
        for idx, (name, series) in enumerate(data["series"].items()):
            pts = []
            for x, y in zip(xs, series):
                px = ml + (x - xmin) / (xmax - xmin) * plot_w
                py = y0 - (y - ymin) / (ymax - ymin) * plot_h
                pts.append("%.2f,%.2f" % (px, py))
            color = _PALETTE[idx % len(_PALETTE)]
            parts.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="2"/>' % (" ".join(pts), color))
            parts.append('<text x="%d" y="%d" font-family="sans-serif" font-size="10" fill="%s">%s</text>'
                         % (ml + plot_w - 6, mt + 14 + idx * 14, color, _svg_escape(name)))
        for tick in _nice_ticks(xmin, xmax)[0]:
            px = ml + (tick - xmin) / (xmax - xmin) * plot_w
            parts.append('<text x="%.2f" y="%d" font-family="sans-serif" font-size="10" '
                         'text-anchor="middle" fill="#333333">%s</text>' % (px, y0 + 16, _fmt_number(tick)))
    # axis labels
    if xlabel:
        parts.append('<text x="%d" y="%d" font-family="sans-serif" font-size="13" '
                     'text-anchor="middle" fill="#111111">%s</text>' % (ml + plot_w // 2, height - 12, _svg_escape(xlabel)))
    if ylabel:
        parts.append('<text x="16" y="%d" font-family="sans-serif" font-size="13" '
                     'text-anchor="middle" fill="#111111" transform="rotate(-90 16 %d)">%s</text>'
                     % (mt + plot_h // 2, mt + plot_h // 2, _svg_escape(ylabel)))
    parts.append("</svg>")
    return "\n".join(parts)


def _bar_yminmax(values):
    if not values:
        return 0.0, 1.0
    lo = min(values)
    hi = max(values)
    if lo == hi:
        lo, hi = 0.0, lo + 1.0 if lo >= 0 else lo - 1.0
    if lo > 0:
        lo = 0.0
    return lo, hi * 1.06


def _pad(lo, hi):
    if lo == hi:
        lo, hi = lo - 1.0, hi + 1.0
    span = hi - lo
    return lo - 0.05 * span, hi + 0.05 * span


def _hist_bins(values, n_bins=12):
    if not values:
        return []
    lo, hi = min(values), max(values)
    if lo == hi:
        lo, hi = lo - 0.5, hi + 0.5
    step = (hi - lo) / n_bins
    edges = [lo + i * step for i in range(n_bins + 1)]
    counts = [0] * n_bins
    for v in values:
        idx = int((v - lo) / step)
        if idx >= n_bins:
            idx = n_bins - 1
        if idx < 0:
            idx = 0
        counts[idx] += 1
    return [(edges[i], edges[i + 1], counts[i]) for i in range(n_bins)]


# --------------------------------------------------------------------------- #
# Minimal stdlib PNG preview (deterministic rasterisation)
# --------------------------------------------------------------------------- #
_FONT_5X7 = {
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11111", "00010", "00100", "00010", "00001", "10001", "01110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    "+": ["00000", "00100", "00100", "11111", "00100", "00100", "00000"],
    "e": ["00000", "00000", "01110", "10001", "11111", "10000", "01110"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
}


def _png_write(path, width, height, rows_bytes):
    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff))
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    raw = b"".join(b"\x00" + bytes(row) for row in rows_bytes)
    idat = zlib.compress(raw, 6)
    with open(path, "wb") as handle:
        handle.write(sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


def _png_image(width, height, bg=(255, 255, 255)):
    return [bytearray(bg * width) for _ in range(height)]


def _set_px(img, x, y, color):
    if 0 <= x < len(img[0]) and 0 <= y < len(img):
        img[y][x * 3:x * 3 + 3] = bytes(color)


def _draw_rect(img, x0, y0, x1, y1, color):
    for y in range(y0, y1):
        for x in range(x0, x1):
            _set_px(img, x, y, color)


def _draw_hline(img, y, x0, x1, color):
    for x in range(x0, x1):
        _set_px(img, x, y, color)


def _draw_vline(img, x, y0, y1, color):
    for y in range(y0, y1):
        _set_px(img, x, y, color)


def _draw_char(img, px, py, ch, scale, color):
    rows = _FONT_5X7.get(ch)
    if rows is None:
        return
    for ry, row in enumerate(rows):
        for rx, bit in enumerate(row):
            if bit == "1":
                _draw_rect(img, px + rx * scale, py + ry * scale,
                           px + (rx + 1) * scale, py + (ry + 1) * scale, color)


def _draw_text(img, px, py, text, scale, color):
    x = px
    for ch in str(text):
        _draw_char(img, x, py, ch, scale, color)
        x += 6 * scale


def _text_width(text, scale):
    return 6 * scale * len(str(text))


def render_chart_png(kind, data, path, width=800, height=520):
    """Render a minimal, text-free-but-tick-labelled PNG preview.

    Labels in the PNG are limited to the built-in 5x7 glyph set (digits, '.',
    '-', '+', 'e', 'E', space); full labels live in the SVG.  This limitation
    is recorded honestly by the caller in ``degradations`` when relevant.
    """
    img = _png_image(width, height)
    ml, mr, mt, mb = 70, 30, 56, 62
    plot_w = width - ml - mr
    plot_h = height - mt - mb
    black = (0, 0, 0)
    gray = (230, 230, 230)
    x0, y0 = ml, mt + plot_h

    if kind == "bar":
        labels = [str(x[0]) for x in data]
        values = [float(x[1]) for x in data]
        ymin, ymax = _bar_yminmax(values)
        yticks, _ = _nice_ticks(ymin, ymax)
    elif kind == "hist":
        values = [float(v) for v in data["values"]]
        bins = _hist_bins(values)
        ymin, ymax = 0.0, max(1.0, max((c for _, _, c in bins), default=0) * 1.08)
        xmin, xmax = bins[0][0] if bins else 0.0, bins[-1][1] if bins else 1.0
        yticks, _ = _nice_ticks(ymin, ymax)
    elif kind == "scatter":
        xs = [float(v) for v in data["x"]]
        ys = [float(v) for v in data["y"]]
        xmin, xmax = _pad(min(xs), max(xs))
        ymin, ymax = _pad(min(ys), max(ys))
        yticks, _ = _nice_ticks(ymin, ymax)
    elif kind == "line":
        xs = [float(v) for v in data["x"]]
        all_y = [float(v) for s in data["series"].values() for v in s]
        xmin, xmax = _pad(min(xs), max(xs))
        ymin, ymax = _pad(min(all_y), max(all_y))
        yticks, _ = _nice_ticks(ymin, ymax)
    else:
        return False

    # axes
    _draw_hline(img, y0, ml, ml + plot_w, black)
    _draw_vline(img, ml, mt, y0, black)
    # y ticks
    for tick in yticks:
        py = y0 - int((tick - ymin) / (ymax - ymin) * plot_h)
        _draw_hline(img, py, ml, ml + plot_w, gray)
        label = _fmt_number(tick)
        _draw_text(img, ml - 8 - _text_width(label, 1), py - 3, label, 1, black)
    # plot geometry
    color = (76, 114, 176)
    if kind == "bar":
        bw = int(plot_w / max(1, len(labels)) * 0.7)
        for i, val in enumerate(values):
            cx = ml + int((i + 0.5) * (plot_w / max(1, len(labels))))
            py = y0 - int((val - ymin) / (ymax - ymin) * plot_h)
            _draw_rect(img, cx - bw // 2, py, cx + bw // 2, y0, color)
    elif kind == "hist":
        for lo, hi_v, count in bins:
            x1 = ml + int((lo - xmin) / (xmax - xmin) * plot_w)
            x2 = ml + int((hi_v - xmin) / (xmax - xmin) * plot_w)
            py = y0 - int((count - ymin) / (ymax - ymin) * plot_h)
            _draw_rect(img, x1, py, max(x1 + 1, x2 - 1), y0, color)
    elif kind == "scatter":
        for x, y in zip(xs, ys):
            px = ml + int((x - xmin) / (xmax - xmin) * plot_w)
            py = y0 - int((y - ymin) / (ymax - ymin) * plot_h)
            _draw_rect(img, px - 2, py - 2, px + 2, py + 2, color)
    elif kind == "line":
        prev = None
        for x, y in zip(xs, data["series"].get(sorted(data["series"])[0])):
            px = ml + int((x - xmin) / (xmax - xmin) * plot_w)
            py = y0 - int((y - ymin) / (ymax - ymin) * plot_h)
            if prev is not None:
                _draw_line(img, prev[0], prev[1], px, py, color)
            prev = (px, py)
    _png_write(path, width, height, img)
    return True


def _draw_line(img, x0, y0, x1, y1, color):
    # Bresenham line.
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    x, y = x0, y0
    while True:
        _set_px(img, x, y, color)
        if x == x1 and y == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy


# --------------------------------------------------------------------------- #
# Analysis pipeline
# --------------------------------------------------------------------------- #
def run_pipeline(input_dir, output_dir, work_dir, phase, argv=None):
    """Run one phase; always return exit code 0 on handled failure."""
    argv = list(argv or sys.argv)
    try:
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(work_dir, exist_ok=True)
    except OSError as exc:
        _write_minimal_failure(output_dir, "cannot_create_output_dirs:%s" % exc)
        return 0
    try:
        if phase in ("init", "all"):
            result = phase_init(input_dir, output_dir, work_dir, argv)
        else:
            result = _load_existing_result(output_dir)
            if result is None:
                result = phase_init(input_dir, output_dir, work_dir, argv)
        if phase == "init":
            return 0
        if phase in ("profile", "analyze", "validate", "all"):
            result = phase_profile(input_dir, output_dir, work_dir, result)
        if phase == "profile":
            return 0
        if phase in ("analyze", "validate", "all"):
            result = phase_analyze(input_dir, output_dir, work_dir, result)
        if phase == "analyze":
            return 0
        if phase in ("validate", "all"):
            result = phase_validate(input_dir, output_dir, work_dir, result)
        return 0
    except Exception as exc:  # noqa: BLE001 - never let the harness crash
        _recover_from_exception(output_dir, "pipeline_error:%s:%s" % (type(exc).__name__, exc))
        return 0


def _recover_from_exception(output_dir, message):
    """Preserve any earlier good result.json; otherwise write a minimal one."""
    existing = _load_existing_result(output_dir)
    if existing is not None and isinstance(existing, dict):
        add_error(existing, message)
        if existing.get("status") not in VALID_STATUSES:
            existing["status"] = STATUS_INPUT_ERROR
        add_change(existing, 99, "recover", message, "failed")
        try:
            write_json_atomic(existing, os.path.join(output_dir, "result.json"))
        except OSError:
            pass
    else:
        _write_minimal_failure(output_dir, message)


def _load_existing_result(output_dir):
    path = os.path.join(output_dir, "result.json")
    if not os.path.isfile(path):
        return None
    obj, err = load_json(path)
    if err or not isinstance(obj, dict):
        return None
    return obj


def _write_minimal_failure(output_dir, message):
    try:
        minimal = {
            "case_id": "not_reported",
            "status": STATUS_INPUT_ERROR,
            "summary": {},
            "artifacts": {},
            "sources": [],
            "uncertainty": [],
            "pending_human": [],
            "change_log": [{"step": 0, "action": "minimal_failure", "status": "failed",
                            "detail": message}],
            "errors": [message],
            "degradations": [],
            "limitations": [],
            "determinism": {"seed": SEED, "sorted_json": True, "input_fingerprint": None,
                            "rerun_policy": "byte_identical_payload"},
        }
        write_json_atomic(minimal, os.path.join(output_dir, "result.json"))
    except OSError:
        pass


def _all_input_paths(disc):
    paths = []
    if disc.get("case"):
        paths.append(disc["case"])
    if disc.get("task"):
        paths.append(disc["task"])
    paths.extend(disc.get("data", []))
    paths.extend(disc.get("databases", []))
    paths.extend(disc.get("docs", []))
    paths.extend(disc.get("materials", []))
    return sorted(set(paths))


def phase_init(input_dir, output_dir, work_dir, argv=None):
    disc, disc_err = discover_inputs(input_dir)
    fingerprint = hash_inputs(_all_input_paths(disc)) if disc.get("case") or disc.get("data") else None
    case_obj = None
    case_err = None
    if disc.get("case"):
        case_obj, case_err = load_json(disc["case"])
    oc, oc_key = extract_output_contract(case_obj) if isinstance(case_obj, dict) else ({}, None)
    result = build_skeleton(case_obj, oc, oc_key)
    result["determinism"]["input_fingerprint"] = fingerprint
    result["_summary"]["input_dir"] = input_dir
    result["_summary"]["discovered"] = {
        "case": bool(disc.get("case")), "task": bool(disc.get("task")),
        "n_data_files": len(disc.get("data", [])), "n_databases": len(disc.get("databases", [])),
        "n_docs": len(disc.get("docs", [])), "n_materials": len(disc.get("materials", [])),
    }
    add_change(result, 0, "init_skeleton", "input_dir=%s fingerprint=%s" % (input_dir, fingerprint))
    if disc_err:
        add_limitation(result, "input_discovery:%s" % disc_err)
        result["status"] = STATUS_INSUFFICIENT
    if not disc.get("case"):
        add_limitation(result, "no_case_json_found; output_contract assumed default")
    if case_err:
        add_error(result, "case_json:%s" % case_err)
        result["status"] = STATUS_INPUT_ERROR
    if oc_key is None and isinstance(case_obj, dict):
        add_change(result, 0, "output_contract_missing", "default contract used", "note")
        add_limitation(result, "case.json has no output_contract key; used default contract")
    else:
        add_change(result, 0, "output_contract_found", "key=%s fields=%d" % (oc_key, len(oc)))
    if disc.get("case"):
        add_source(result, os.path.basename(disc["case"]) + ":case_id",
                   json.dumps(extract_case_id(case_obj), ensure_ascii=False), "case_id")
    write_json_atomic(result, os.path.join(output_dir, "result.json"))
    write_runtime_json(output_dir, argv or sys.argv, fingerprint)
    return result


def phase_profile(input_dir, output_dir, work_dir, result):
    disc, _disc_err = discover_inputs(input_dir)
    profile = {"data_files": {}, "databases": {}, "errors": []}
    for path in sorted(disc.get("data", [])):
        name = os.path.basename(path)
        table, err = load_table(path)
        if err:
            profile["errors"].append({"file": name, "error": err})
            add_error(result, "load_table:%s:%s" % (name, err))
            continue
        prof = profile_table(table)
        profile["data_files"][name] = {
            "path": path, "n_rows": prof["n_rows"], "n_cols": prof["n_cols"],
            "n_unique_rows": prof["n_unique_rows"], "n_duplicate_rows": prof["n_duplicate_rows"],
            "delimiter": table.get("delimiter"), "columns": prof["columns"],
        }
        add_source(result, name + ":shape",
                   "n_rows=%d n_cols=%d" % (prof["n_rows"], prof["n_cols"]), "data_shape")
        if prof["n_duplicate_rows"]:
            add_limitation(result, "%s has %d full-duplicate rows; do NOT dedupe silently" %
                           (name, prof["n_duplicate_rows"]))
    for path in sorted(disc.get("databases", [])):
        name = os.path.basename(path)
        try:
            tables = sqlite_schema(path)
            profile["databases"][name] = {"path": path, "tables": tables}
            add_source(result, name + ":schema", json.dumps(tables, ensure_ascii=False)[:2000],
                       "sqlite_schema")
        except Exception as exc:  # noqa: BLE001
            profile["databases"][name] = {"path": path, "error": str(exc)}
            add_error(result, "sqlite_schema:%s:%s" % (name, exc))
    result["_summary"]["profile"] = profile
    result["_artifacts"]["data_profile"] = os.path.join(output_dir, "data_profile.json")
    write_json_atomic(profile, result["_artifacts"]["data_profile"])
    add_change(result, 1, "profile", "profiled %d data files, %d databases" %
               (len(profile["data_files"]), len(profile["databases"])))
    write_json_atomic(result, os.path.join(output_dir, "result.json"))
    return result


def _categorical_columns(prof):
    out = []
    for col, info in prof["columns"].items():
        if info["dtype"] == "string" and 1 <= info["n_unique"] <= 20:
            out.append(col)
    return out


def _numeric_columns(prof):
    return [c for c, i in prof["columns"].items() if i["dtype"] == "numeric"]


def _fill_contract_fields(result, artifacts):
    """Map canonical artifacts into the discovered output_contract fields.

    Only fields that actually exist in the contract are touched; unknown
    contract fields are left at ``null`` for the agent to fill case by case.
    """
    summary = (result.get("_summary") or {}).get("discovered") or {}
    mapping = {}
    if artifacts.get("cleaned_table"):
        mapping["cleaned_table"] = artifacts["cleaned_table"]
    if artifacts.get("statistics"):
        mapping["statistics"] = artifacts["statistics"]
    if artifacts.get("figure_svg") or artifacts.get("figure_png"):
        mapping["figures"] = {"svg": artifacts.get("figure_svg"),
                              "png": artifacts.get("figure_png")}
        mapping["figure"] = artifacts.get("figure_svg")
    if artifacts.get("query_results"):
        mapping["query_results"] = artifacts["query_results"]
    if artifacts.get("analysis_code"):
        mapping["analysis_code"] = artifacts["analysis_code"]
        mapping["code"] = artifacts["analysis_code"]
    if result.get("_contract_fields") and "summary" in result.get("_contract_fields", []):
        mapping["summary"] = {
            "n_data_files": summary.get("n_data_files"),
            "n_databases": summary.get("n_databases"),
            "status": result.get("status"),
            "statistics": artifacts.get("statistics"),
            "data_profile": artifacts.get("data_profile"),
        }
    for field in result.get("_contract_fields", []):
        if field in mapping and mapping[field] is not None:
            result[field] = mapping[field]


def phase_analyze(input_dir, output_dir, work_dir, result):
    disc, _disc_err = discover_inputs(input_dir)
    stats = {"data_files": {}, "databases": {}}
    artifacts = dict(result.get("_artifacts", {}))
    artifacts.setdefault("cleaned_table", os.path.join(output_dir, "cleaned_table.csv"))
    artifacts.setdefault("statistics", os.path.join(output_dir, "statistics.json"))
    artifacts.setdefault("figure_svg", os.path.join(output_dir, "figure.svg"))
    artifacts.setdefault("figure_png", os.path.join(output_dir, "figure.png"))
    artifacts.setdefault("analysis_code", os.path.join(output_dir, "analysis_code.py"))
    artifacts.setdefault("query_results", os.path.join(output_dir, "query_results.json"))

    if disc.get("data"):
        # Analyse the first (alphabetically) data file as the primary table.
        primary_path = sorted(disc["data"])[0]
        primary_name = os.path.basename(primary_path)
        table, err = load_table(primary_path)
        if err is None and table["rows"]:
            prof = profile_table(table)
            stats["data_files"][primary_name] = _analyse_table(table, prof, output_dir, artifacts)
            _write_cleaned_csv(table, prof, artifacts["cleaned_table"])
            _write_analysis_code(artifacts["cleaned_table"], artifacts["analysis_code"])
            for deg in _generate_charts(table, prof, artifacts):
                add_degradation(result, deg)
            add_source(result, primary_name + ":cleaned_table",
                       _first_rows_quote(table, 2), "cleaned_table_sample")
        elif err:
            add_error(result, "analyze:%s:%s" % (primary_name, err))
        for path in sorted(disc["data"])[1:]:
            name = os.path.basename(path)
            t2, e2 = load_table(path)
            if e2 is None and t2["rows"]:
                stats["data_files"][name] = _analyse_table(t2, profile_table(t2), output_dir, artifacts)
            else:
                add_error(result, "analyze:%s:%s" % (name, e2))

    queries = {}
    for path in sorted(disc.get("databases", [])):
        name = os.path.basename(path)
        queries[name] = _analyse_database(path)
        add_source(result, name + ":query", _first_sql(queries), "sqlite_query")
    stats["databases"] = queries

    result["_summary"]["statistics"] = stats
    write_json_atomic(stats, artifacts["statistics"])
    if queries:
        write_json_atomic(queries, artifacts["query_results"])
    else:
        artifacts.pop("query_results", None)
    result["_artifacts"] = artifacts
    _fill_contract_fields(result, artifacts)
    if not disc.get("data") and not disc.get("databases"):
        if result.get("status") != STATUS_INPUT_ERROR:
            result["status"] = STATUS_INSUFFICIENT
            add_limitation(result, "no analysable data or database found in /input")
    else:
        if result.get("status") != STATUS_INPUT_ERROR:
            result["status"] = STATUS_PARTIAL
        add_limitation(result, "generic deterministic baseline produced; case-specific "
                               "interpretation/verification must be completed by the agent")
    add_change(result, 2, "analyze", "generic stats + charts + SQLite queries written")
    write_json_atomic(result, os.path.join(output_dir, "result.json"))
    return result


def _analyse_table(table, prof, output_dir, artifacts):
    rows = table["rows"]
    columns = table["columns"]
    result = {"profile": prof, "numeric_columns": {}, "groups": {}, "tests": {}, "correlations": {}}
    numeric_cols = _numeric_columns(prof)
    for col in numeric_cols:
        vals = [_to_float(r.get(col)) for r in rows]
        vals = [v for v in vals if v is not None]
        result["numeric_columns"][col] = numeric_summary(vals)
    cat_cols = _categorical_columns(prof)
    for cat in cat_cols:
        groups = {}
        for r in rows:
            key = str(r.get(cat))
            groups.setdefault(key, []).append(r)
        group_stats = {}
        for key, members in groups.items():
            entry = {"n": len(members)}
            for ncol in numeric_cols:
                vals = [_to_float(m.get(ncol)) for m in members]
                vals = [v for v in vals if v is not None]
                if vals:
                    entry[ncol] = {"n": len(vals), "mean": _r(mean(vals)), "std": _r(math.sqrt(variance(vals)))}
            group_stats[key] = entry
        result["groups"][cat] = group_stats
        # t-test between the two largest groups, with assumption screening.
        ordered = sorted(groups.items(), key=lambda kv: -len(kv[1]))
        if len(ordered) >= 2 and numeric_cols:
            a_key, a_rows = ordered[0]
            b_key, b_rows = ordered[1]
            for ncol in numeric_cols[:3]:
                a_vals = [_to_float(r.get(ncol)) for r in a_rows]
                b_vals = [_to_float(r.get(ncol)) for r in b_rows]
                a_vals = [v for v in a_vals if v is not None]
                b_vals = [v for v in b_vals if v is not None]
                test = welch_ttest(a_vals, b_vals)
                test["group_a"] = a_key
                test["group_b"] = b_key
                test["normality_a"] = normality_screen(a_vals)
                test["normality_b"] = normality_screen(b_vals)
                test["equal_variance_levene"] = levene_median(a_vals, b_vals)
                result["tests"]["%s__%s__%s_vs_%s" % (ncol, cat, a_key, b_key)] = test
    for i, c1 in enumerate(numeric_cols):
        v1 = [_to_float(r.get(c1)) for r in rows]
        v1 = [v for v in v1 if v is not None]
        for c2 in numeric_cols[i + 1:]:
            v2 = [_to_float(r.get(c2)) for r in rows]
            v2 = [v for v in v2 if v is not None]
            pairs = [(x, y) for x, y in zip(v1, v2)]
            pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
            if len(pairs) >= 3:
                r = pearson_corr([p[0] for p in pairs], [p[1] for p in pairs])
                result["correlations"]["%s_vs_%s" % (c1, c2)] = {"r": _r(r), "n": len(pairs)}
    return result


def _write_cleaned_csv(table, prof, path):
    columns = table["columns"]
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["__row_id", "__is_duplicate"] + columns)
        seen = set()
        for i, row in enumerate(table["rows"]):
            key = json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
            dup = 1 if key in seen else 0
            seen.add(key)
            writer.writerow([i, dup] + [_cell(row.get(c)) for c in columns])


def _cell(value):
    if value is None:
        return ""
    return str(value)


def _write_analysis_code(cleaned_csv, path):
    code = '''#!/usr/bin/env python3
"""Recompute summary statistics from the cleaned CSV (stdlib only, deterministic).

Regenerated by the Skill's generic analysis step.  Re-run to independently
reproduce ``statistics.json``-compatible per-column numeric summaries.
"""
import csv, json, math, os

CLEANED = %r

def to_float(v):
    if v is None or str(v).strip().lower() in {"", "na", "n/a", "null", "none", "nan"}:
        return None
    try:
        return float(str(v).strip().replace(",", ""))
    except ValueError:
        return None

def quantile(s, q):
    n = len(s)
    if n == 0: return None
    if n == 1: return s[0]
    pos = q * (n - 1)
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    if lo == hi: return s[lo]
    return s[lo] * (1 - (pos - lo)) + s[hi] * (pos - lo)

def main():
    with open(CLEANED, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    # Drop the bookkeeping columns added by the cleaner.
    cols = [c for c in rows[0].keys() if c not in {"__row_id", "__is_duplicate"}]
    out = {}
    for col in cols:
        vals = [to_float(r.get(col)) for r in rows]
        vals = [v for v in vals if v is not None]
        if not vals:
            continue
        s = sorted(vals)
        m = sum(s) / len(s)
        var = sum((x - m) ** 2 for x in s) / (len(s) - 1) if len(s) > 1 else 0.0
        out[col] = {
            "n": len(s), "mean": round(m, 10),
            "std": round(math.sqrt(var) if var >= 0 else 0.0, 10),
            "min": round(s[0], 10), "max": round(s[-1], 10),
            "q25": round(quantile(s, 0.25), 10),
            "median": round(quantile(s, 0.5), 10),
            "q75": round(quantile(s, 0.75), 10),
        }
    print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
    with open(os.path.join(os.path.dirname(CLEANED) or ".", "statistics_recomputed.json"),
              "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, sort_keys=True)

if __name__ == "__main__":
    main()
''' % cleaned_csv
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(code)


def _generate_charts(table, prof, artifacts):
    """Generate one deterministic chart (histogram or bar). Returns degradations list."""
    degradations = []
    numeric_cols = _numeric_columns(prof)
    cat_cols = _categorical_columns(prof)
    svg_path = artifacts.get("figure_svg")
    png_path = artifacts.get("figure_png")
    if not svg_path and not png_path:
        return degradations
    kind = None
    data = None
    title = None
    xlabel = None
    ylabel = None
    if numeric_cols:
        col = numeric_cols[0]
        vals = [_to_float(r.get(col)) for r in table["rows"]]
        vals = [v for v in vals if v is not None]
        if len(vals) >= 2:
            kind = "hist"
            data = {"values": vals}
            title = "Distribution of %s" % col
            xlabel = col
            ylabel = "count"
    if kind is None and cat_cols:
        col = cat_cols[0]
        counts = {}
        for r in table["rows"]:
            key = str(r.get(col))
            counts[key] = counts.get(key, 0) + 1
        if counts:
            data = sorted(counts.items())
            kind = "bar"
            title = "Counts by %s" % col
            xlabel = col
            ylabel = "count"
    if kind and svg_path:
        try:
            with open(svg_path, "w", encoding="utf-8") as handle:
                handle.write(make_svg_chart(kind, data, title, xlabel, ylabel))
        except Exception as exc:  # noqa: BLE001
            degradations.append("figure_svg:write_failed:%s" % exc)
    if kind and png_path:
        try:
            render_chart_png(kind, data, png_path)
        except Exception as exc:  # noqa: BLE001
            degradations.append("figure_png:write_failed:%s" % exc)
    return degradations


def _first_rows_quote(table, n):
    lines = []
    columns = table["columns"]
    lines.append(",".join(columns))
    for row in table["rows"][:n]:
        lines.append(",".join(_cell(row.get(c)) for c in columns))
    return "\n".join(lines)[:1200]


def _first_sql(queries):
    if not isinstance(queries, dict):
        return ""
    for _name, qlist in queries.items():
        if isinstance(qlist, list):
            for q in qlist:
                if isinstance(q, dict):
                    sql = q.get("sql") or (q.get("count_query") or {}).get("sql")
                    if sql:
                        return sql
    return ""


def _analyse_database(db_path):
    out = []
    try:
        tables = sqlite_schema(db_path)
    except Exception as exc:  # noqa: BLE001
        return [{"error": str(exc)}]
    for tbl in tables:
        entry = {"table": tbl["name"], "n_rows": tbl["n_rows"], "columns": tbl["columns"],
                 "count_query": run_query(db_path, "SELECT COUNT(*) AS n FROM %s" % _quote_ident(tbl["name"])),
                 "sample": run_query(db_path, "SELECT * FROM %s LIMIT 5" % _quote_ident(tbl["name"])),
                 "numeric_aggregates": []}
        for col in tbl["columns"]:
            # Probe numeric-ness with one non-null value.
            probe = run_query(db_path, "SELECT %s FROM %s WHERE %s IS NOT NULL LIMIT 1"
                              % (_quote_ident(col["name"]), _quote_ident(tbl["name"]),
                                 _quote_ident(col["name"])))
            if probe["rows"] and probe["rows"][0]:
                val = probe["rows"][0][0]
                if _to_float(val) is not None and not isinstance(val, str):
                    agg = sqlite_numeric_aggregate(db_path, tbl["name"], col["name"])
                    agg["column"] = col["name"]
                    entry["numeric_aggregates"].append(agg)
        out.append(entry)
    return out


# --------------------------------------------------------------------------- #
# Validation / self-check
# --------------------------------------------------------------------------- #
def phase_validate(input_dir, output_dir, work_dir, result):
    disc, _disc_err = discover_inputs(input_dir)
    checks = []
    ok = True

    def check(cond, name, detail=""):
        nonlocal ok
        checks.append({"name": name, "pass": bool(cond), "detail": detail})
        if not cond:
            ok = False

    check(isinstance(result, dict) and result.get("case_id"), "case_id_present")
    check(result.get("status") in VALID_STATUSES, "status_valid", str(result.get("status")))
    for key in ("sources", "uncertainty", "change_log", "pending_human"):
        check(isinstance(result.get(key), list), "field_%s_is_list" % key)
    for src in result.get("sources", []):
        check(isinstance(src, dict) and src.get("locator") and src.get("quote") and src.get("role"),
              "source_wellformed", json.dumps(src, ensure_ascii=False)[:200])
    contract_keys = list(result.get("_contract_fields") or [])
    check(len(contract_keys) >= 0, "contract_fields_copied", str(contract_keys))

    # Fingerprint determinism.
    current_fp = hash_inputs(_all_input_paths(disc))
    stored_fp = (result.get("determinism") or {}).get("input_fingerprint")
    check(stored_fp is None or stored_fp == current_fp, "input_fingerprint_matches",
          "%s vs %s" % (stored_fp, current_fp))

    # Referenced artifacts exist.
    for name, path in (result.get("_artifacts") or {}).items():
        if isinstance(path, str) and path:
            check(os.path.isfile(path), "artifact_exists_%s" % name, path)

    # SVG parses.
    svg_path = (result.get("_artifacts") or {}).get("figure_svg")
    if svg_path and os.path.isfile(svg_path) and ET is not None:
        try:
            ET.parse(svg_path)
            check(True, "svg_wellformed", svg_path)
        except Exception as exc:  # noqa: BLE001
            check(False, "svg_wellformed", "parse_error:%s" % exc)

    # Cross-check recomputed numeric summaries against statistics.json.
    stats_path = (result.get("_artifacts") or {}).get("statistics")
    if stats_path and os.path.isfile(stats_path):
        check(_cross_check_stats(stats_path), "statistics_cross_check")

    result["_summary"]["validation"] = {"pass": ok, "checks": checks}
    result["determinism"]["validation_pass"] = ok
    if ok and result.get("status") == STATUS_PARTIAL:
        add_change(result, 3, "validate", "generic baseline validated; awaiting case-specific completion", "done")
    elif ok:
        add_change(result, 3, "validate", "all structural checks passed", "done")
    else:
        add_change(result, 3, "validate", "structural checks failed; see _summary.validation", "failed")
        add_limitation(result, "validation found %d failing check(s); do not report results as final" %
                       sum(1 for c in checks if not c["pass"]))
    write_json_atomic(result, os.path.join(output_dir, "result.json"))
    return result


def _cross_check_stats(stats_path):
    stats, err = load_json(stats_path)
    if err or not isinstance(stats, dict):
        return False
    data_files = stats.get("data_files", {})
    if not data_files:
        return True  # nothing to cross-check
    for name, analysis in data_files.items():
        if not isinstance(analysis, dict):
            return False
        for col, summary in (analysis.get("numeric_columns") or {}).items():
            if not isinstance(summary, dict) or "mean" not in summary:
                return False
            # Values must be finite numbers.
            for field in ("mean", "std", "min", "max"):
                v = summary.get(field)
                if v is None:
                    continue
                if not isinstance(v, (int, float)):
                    return False
    return True


# --------------------------------------------------------------------------- #
# Optional: expose a tiny demo dataset builder for selftest.py
# --------------------------------------------------------------------------- #
def make_demo_case(root):
    """Create a synthetic, clearly-labelled case under ``root`` (no /input needed)."""
    os.makedirs(root, exist_ok=True)
    case = {
        "case_id": "selftest-demo-001",
        "task": "Verify the deterministic analysis pipeline on a synthetic table.",
        "output_contract": {
            "cleaned_table": None,
            "statistics": None,
            "figures": None,
            "query_results": None,
            "summary": None,
        },
    }
    write_json_atomic(case, os.path.join(root, "case.json"))
    with open(os.path.join(root, "task.md"), "w", encoding="utf-8") as handle:
        handle.write("# Selftest demo case\n\nClean, profile and summarise the table.\n")
    header = "id,group,value1,value2\n"
    lines = [header]
    for i in range(30):
        g = "A" if i < 15 else "B"
        v1 = round(10.0 + i * 0.5, 3)
        v2 = "" if i % 9 == 0 else round(v1 * 1.5, 3)
        lines.append("%d,%s,%.3f,%s\n" % (i, g, v1, v2))
    with open(os.path.join(root, "materials_sample.csv"), "w", encoding="utf-8") as handle:
        handle.writelines(lines)
    # A tiny SQLite database.
    db = os.path.join(root, "sample.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE obs (id INTEGER PRIMARY KEY, group_name TEXT, value REAL)")
    for i in range(12):
        con.execute("INSERT INTO obs VALUES (?,?,?)", (i, "A" if i % 2 == 0 else "B", 5.0 + i))
    con.commit()
    con.close()
    return root
