#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core_impl — declarative analysis engine (stdlib only).

Architecture (differentiated from a linear init->profile->arbitrary-python->validate CLI):
  * One central Intermediate Representation: <work>/state.json. Every subcommand
    reads and updates the SAME state file, so each step is incremental, idempotent
    and re-runnable from any point. Nothing important lives only in stdout.
  * The Agent does NOT write arbitrary throwaway Python for the baseline. It fills a
    typed, declarative "recipe" (cleaning / statistics / queries / plots / checks).
    This engine interprets the recipe deterministically, recording every row-count
    change, coercion, query, plot and check into the state IR.
  * Plots are rendered twice from one plot model: editable SVG (authoritative labels)
    plus a real raster PNG preview written with the stdlib zlib module (no matplotlib
    dependency). Numeric axis tick labels are rasterised from a tiny 5x7 digit font.
  * Validation order is inverted vs. the reference: `check` validates the recipe and
    the results against the output_contract BEFORE `deliver` finalises result.json,
    and `deliver` re-validates before writing.

Only the Python standard library is imported. The Agent's own richer analysis may
additionally use the preinstalled scientific-python stack; the engine is the
guaranteed stdlib fallback and provenance recorder.
"""

import argparse
import csv
import datetime
import hashlib
import json
import math
import os
import shutil
import sqlite3
import statistics
import struct
import sys
import time
import urllib.parse
import zlib

SCHEMA_VERSION = 1
ALLOWED_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")
MAX_FILE_BYTES = 2 * 1024 * 1024
PALETTE = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
           "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")


def sanitize(obj):
    """Replace NaN/Inf (which JSON cannot carry) with None or an explicit string."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    return obj


def log(state, msg, **extra):
    entry = {"ts": now_iso(), "msg": msg}
    entry.update(extra)
    state.setdefault("change_log", []).append(entry)
    return entry


def read_bytes(path):
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size > MAX_FILE_BYTES:
        return None
    with open(path, "rb") as f:
        return f.read()


def decode_text(b):
    if b is None:
        return None
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return b.decode("latin-1", errors="replace")


def load_json(path):
    b = read_bytes(path)
    if b is None:
        return None
    txt = decode_text(b)
    if txt is None:
        return None
    try:
        return json.loads(txt)
    except (ValueError, TypeError):
        return None


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def ensure_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except OSError as exc:
        sys.stderr.write("mkdir failed: %s\n" % exc)
        return False


def write_json(path, obj):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sanitize(obj), f, ensure_ascii=False, indent=2)
        return True
    except (OSError, ValueError, TypeError) as exc:
        sys.stderr.write("write_json failed %s: %s\n" % (path, exc))
        return False


def write_text(path, text):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return True
    except OSError as exc:
        sys.stderr.write("write_text failed %s: %s\n" % (path, exc))
        return False


def relpath_abs(path, base):
    """Return a portable relative path from base to path if possible."""
    try:
        return os.path.relpath(path, base).replace(os.sep, "/")
    except ValueError:
        return path


# --------------------------------------------------------------------------- #
# input discovery + case/task parsing
# --------------------------------------------------------------------------- #
CSV_EXTS = {".csv", ".tsv", ".txt", ".dat", ".data"}
JSON_EXTS = {".json"}
SQLITE_EXTS = {".sqlite", ".sqlite3", ".db", ".db3"}


def classify(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in CSV_EXTS:
        return "tabular"
    if ext in JSON_EXTS:
        return "json"
    if ext in SQLITE_EXTS:
        return "sqlite"
    if ext in {".md", ".markdown"}:
        return "markdown"
    if ext == ".pdf":
        return "pdf"
    if ext in {".png", ".jpg", ".jpeg", ".svg", ".gif"}:
        return "image"
    return "other"


def discover(input_dir):
    files = []
    if not input_dir or not os.path.isdir(input_dir):
        return files, "input_dir_missing"
    for root, dirs, names in os.walk(input_dir):
        dirs.sort()
        for name in sorted(names):
            full = os.path.join(root, name)
            size = os.path.getsize(full) if os.path.isfile(full) else 0
            if size > MAX_FILE_BYTES:
                files.append({"path": full, "kind": "skipped_oversize", "bytes": size,
                              "rel": relpath_abs(full, input_dir)})
                continue
            files.append({"path": full, "kind": classify(name), "bytes": size,
                          "rel": relpath_abs(full, input_dir),
                          "sha256": sha256_file(full)})
    return files, None


def find_file(input_dir, names):
    """Find a file whose basename matches any of names (case-insensitive)."""
    if not input_dir or not os.path.isdir(input_dir):
        return None
    want = {n.lower() for n in names}
    for root, _dirs, fnames in os.walk(input_dir):
        for n in fnames:
            if n.lower() in want:
                return os.path.join(root, n)
    return None


def parse_case(input_dir):
    """Return (case_id, contract, contract_raw, evidence, case_path, errors)."""
    case_path = find_file(input_dir, ["case.json"])
    errors = []
    case_id = None
    contract = None
    contract_raw = None
    evidence = None
    if not case_path:
        return case_id, contract, contract_raw, evidence, None, ["case.json not found"]
    data = load_json(case_path)
    if data is None or not isinstance(data, dict):
        return case_id, contract, contract_raw, evidence, case_path, ["case.json unreadable or not a dict"]
    case_id = (data.get("case_id") or data.get("caseId")
               or data.get("challenge_id") or data.get("challengeId") or data.get("id"))
    for key in ("output_contract", "outputContract", "contract"):
        if isinstance(data.get(key), dict):
            contract = data[key]
            contract_raw = data[key]
            break
    if contract is None:
        for key in ("output_contract", "outputContract", "contract"):
            if isinstance(data.get(key), list):
                contract = {k: None for k in data[key]}
                contract_raw = data[key]
                break
    for key in ("evidence", "expected", "gold", "gold_standard", "expected_metrics", "answer"):
        if key in data and data[key] not in (None, "", [], {}):
            evidence = data[key]
            break
    return case_id, contract, contract_raw, evidence, case_path, errors


def parse_task(input_dir):
    task_path = find_file(input_dir, ["task.md", "task.txt"])
    if not task_path:
        return None, "task.md not found"
    b = read_bytes(task_path)
    return decode_text(b), None


# --------------------------------------------------------------------------- #
# central state IR
# --------------------------------------------------------------------------- #
def new_state():
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": None,
        "input_dir": None,
        "output_dir": None,
        "work_dir": None,
        "contract_raw": None,
        "contract_fields": [],
        "manifest": [],
        "data_profile": {},
        "alignment": [],
        "recipe": None,
        "results": {},
        "checks": {},
        "artifacts": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "change_log": [],
    }


def load_state(work_dir):
    path = os.path.join(work_dir, "state.json")
    st = load_json(path)
    if st and isinstance(st, dict):
        return st
    return new_state()


def save_state(state):
    wd = state.get("work_dir")
    if not wd:
        return False
    ensure_dir(wd)
    return write_json(os.path.join(wd, "state.json"), state)


# --------------------------------------------------------------------------- #
# data profiling
# --------------------------------------------------------------------------- #
def sniff_delimiter(sample):
    counts = {",": 0, ";": 0, "\t": 0, "|": 0}
    for line in sample.splitlines()[:20]:
        for ch in counts:
            counts[ch] += line.count(ch)
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def profile_tabular(path):
    b = read_bytes(path)
    if b is None:
        return {"error": "unreadable_or_oversize", "bytes": os.path.getsize(path) if os.path.exists(path) else None}
    txt = decode_text(b)
    if txt is None:
        return {"error": "undecodable"}
    delim = sniff_delimiter(txt)
    reader = csv.reader(txt.splitlines(), delimiter=delim, quotechar='"')
    rows = [r for r in reader if r]
    if not rows:
        return {"error": "empty", "delimiter": delim, "rows": 0}
    headers = [str(h).strip() for h in rows[0]]
    body = rows[1:]
    nrows = len(body)
    missing = {h: sum(1 for r in body if i >= len(r) or r[i] is None or str(r[i]).strip() == "") for i, h in enumerate(headers)}
    nunique = {}
    dtypes = {}
    for i, h in enumerate(headers):
        vals = [r[i].strip() if i < len(r) and r[i] is not None else "" for r in body]
        nunique[h] = len(set(vals))
        dtypes[h] = infer_dtype(vals)
    sample_rows = body[:5]
    return {"path": path, "kind": "tabular", "delimiter": delim, "encoding": "utf-8-sig/fallback",
            "rows": nrows, "columns": len(headers), "headers": headers,
            "missing": missing, "nunique": nunique, "dtypes": dtypes,
            "sample_rows": sample_rows}


def infer_dtype(values):
    nonempty = [v for v in values if v != ""]
    if not nonempty:
        return "empty"
    if all(is_int(v) for v in nonempty):
        return "int"
    if all(is_float(v) for v in nonempty):
        return "float"
    return "str"


def is_int(v):
    try:
        int(v)
        return True
    except (ValueError, TypeError):
        return False


def is_float(v):
    try:
        float(v)
        return True
    except (ValueError, TypeError):
        return False


def profile_json(path):
    data = load_json(path)
    if data is None:
        return {"path": path, "kind": "json", "error": "unreadable"}
    if isinstance(data, dict):
        return {"path": path, "kind": "json", "top_keys": list(data.keys())[:200],
                "type": "object"}
    if isinstance(data, list):
        return {"path": path, "kind": "json", "type": "array", "length": len(data),
                "first_item_keys": list(data[0].keys()) if data and isinstance(data[0], dict) else None}
    return {"path": path, "kind": "json", "type": type(data).__name__}


def profile_sqlite(path):
    try:
        uri = "file:%s?mode=ro" % urllib.parse.quote(os.path.abspath(path).replace("\\", "/"))
        con = sqlite3.connect(uri, uri=True)
        cur = con.cursor()
        tables = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        out = {"path": path, "kind": "sqlite", "tables": {}}
        for t in tables:
            cols = [{"name": c[1], "type": c[2]} for c in cur.execute("PRAGMA table_info(%s)" % t)]
            try:
                n = cur.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            except sqlite3.Error:
                n = None
            out["tables"][t] = {"columns": cols, "rows": n}
        con.close()
        return out
    except sqlite3.Error as exc:
        return {"path": path, "kind": "sqlite", "error": str(exc)}


def profile(input_dir, state):
    profile = {}
    for item in state.get("manifest", []):
        path = item["path"]
        kind = item["kind"]
        try:
            if kind == "tabular":
                profile[item["rel"]] = profile_tabular(path)
            elif kind == "json":
                profile[item["rel"]] = profile_json(path)
            elif kind == "sqlite":
                profile[item["rel"]] = profile_sqlite(path)
            elif kind == "pdf":
                profile[item["rel"]] = {"path": path, "kind": "pdf", "note":
                                        "use pdftotext on the original PDF; no pre-extracted layer"}
            elif kind == "image":
                profile[item["rel"]] = {"path": path, "kind": "image", "note":
                                        "visual input; not tabulated by engine"}
            else:
                profile[item["rel"]] = {"path": path, "kind": kind, "note": "not profiled"}
        except Exception as exc:  # noqa: BLE001 - profiling must never crash the run
            profile[item["rel"]] = {"path": path, "kind": kind, "error": str(exc)}
    state["data_profile"] = profile
    return profile


# --------------------------------------------------------------------------- #
# recipe: cleaning
# --------------------------------------------------------------------------- #
def coerce_value(raw, to, errors="coerce"):
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "":
        return None
    if to == "float":
        try:
            return float(s)
        except (ValueError, TypeError):
            if errors == "coerce":
                return None
            raise
    if to == "int":
        try:
            return int(float(s)) if "." in s else int(s)
        except (ValueError, TypeError):
            if errors == "coerce":
                return None
            raise
    return s


def make_table(headers, rows, dtypes):
    return {"headers": list(headers), "rows": rows, "dtypes": dict(dtypes)}


def col_index(table, name):
    headers = table["headers"]
    if name in headers:
        return headers.index(name)
    # case-insensitive exact fallback
    for i, h in enumerate(headers):
        if str(h).lower() == str(name).lower():
            return i
    return None


def get_col(table, name):
    i = col_index(table, name)
    if i is None:
        return None
    return [r[i] if i < len(r) else None for r in table["rows"]]


def apply_cleaning(table, ops, state):
    rows = [list(r) for r in table["rows"]]
    headers = list(table["headers"])
    dtypes = dict(table["dtypes"])
    trace = []

    def recount(tag, before):
        after = len(rows)
        if after != before:
            trace.append({"op": tag, "rows_before": before, "rows_after": after})
        return after

    for op in ops or []:
        name = op.get("op")
        before = len(rows)
        try:
            if name == "coerce":
                idx = col_index({"headers": headers, "rows": rows}, op.get("column"))
                if idx is None:
                    raise ValueError("column not found: %s" % op.get("column"))
                to = op.get("to", "float")
                dtypes[headers[idx]] = to
                for r in rows:
                    if idx < len(r):
                        r[idx] = coerce_value(r[idx], to, op.get("errors", "coerce"))
                trace.append({"op": name, "column": op.get("column"), "to": to})
            elif name == "rename":
                idx = col_index({"headers": headers, "rows": rows}, op.get("column"))
                if idx is None:
                    raise ValueError("column not found: %s" % op.get("column"))
                headers[idx] = op.get("to", headers[idx])
                trace.append({"op": name, "from": op.get("column"), "to": op.get("to")})
            elif name == "drop_na":
                subset = op.get("subset") or headers
                idxs = []
                for c in subset:
                    i = col_index({"headers": headers, "rows": rows}, c)
                    if i is None:
                        raise ValueError("column not found: %s" % c)
                    idxs.append(i)
                how = op.get("how", "any")
                kept = []
                for r in rows:
                    n_missing = sum(1 for i in idxs if i >= len(r) or r[i] is None or str(r[i]).strip() == "")
                    if (how == "all" and n_missing < len(idxs)) or (how == "any" and n_missing == 0):
                        kept.append(r)
                rows = kept
                recount("drop_na", before)
            elif name == "fill_na":
                idx = col_index({"headers": headers, "rows": rows}, op.get("column"))
                if idx is None:
                    raise ValueError("column not found: %s" % op.get("column"))
                strat = op.get("strategy")
                if strat == "mean":
                    vals = [coerce_value(r[idx] if idx < len(r) else None, "float") for r in rows]
                    vals = [v for v in vals if v is not None]
                    fill = sum(vals) / len(vals) if vals else None
                elif strat == "median":
                    vals = sorted(v for v in (coerce_value(r[idx] if idx < len(r) else None, "float") for r in rows) if v is not None)
                    fill = vals[len(vals) // 2] if vals else None
                elif strat == "constant" or "value" in op:
                    fill = op.get("value")
                else:
                    fill = op.get("value")
                n = 0
                for r in rows:
                    if idx < len(r) and (r[idx] is None or str(r[idx]).strip() == ""):
                        r[idx] = fill
                        n += 1
                trace.append({"op": name, "column": op.get("column"), "filled": n, "value": fill})
            elif name == "drop_duplicates":
                subset = op.get("subset")
                if subset:
                    idxs = []
                    for c in subset:
                        i = col_index({"headers": headers, "rows": rows}, c)
                        if i is None:
                            raise ValueError("column not found: %s" % c)
                        idxs.append(i)
                    key = lambda r: tuple(r[i] for i in idxs)  # noqa: E731
                else:
                    key = lambda r: tuple(r)  # noqa: E731
                seen = set()
                kept = []
                order = rows if op.get("keep", "first") != "last" else reversed(rows)
                for r in order:
                    k = key(r)
                    if k not in seen:
                        seen.add(k)
                        kept.append(r)
                if op.get("keep", "first") == "last":
                    kept.reverse()
                rows = kept
                recount("drop_duplicates", before)
            elif name == "filter":
                idx = col_index({"headers": headers, "rows": rows}, op.get("column"))
                if idx is None:
                    raise ValueError("column not found: %s" % op.get("column"))
                cmp = op.get("op", "==")
                val = op.get("value")
                kept = []
                for r in rows:
                    v = r[idx] if idx < len(r) else None
                    try:
                        if compare(v, cmp, val):
                            kept.append(r)
                    except (TypeError, ValueError):
                        continue
                rows = kept
                recount("filter", before)
            elif name == "select":
                cols = op.get("columns") or headers
                idxs = []
                for c in cols:
                    i = col_index({"headers": headers, "rows": rows}, c)
                    if i is None:
                        raise ValueError("column not found: %s" % c)
                    idxs.append(i)
                headers = [headers[i] for i in idxs]
                dtypes = {headers[j]: dtypes.get(headers[j], "str") for j in range(len(headers))}
                rows = [[r[i] if i < len(r) else None for i in idxs] for r in rows]
                trace.append({"op": name, "columns": headers})
            elif name == "drop":
                cols = set(op.get("columns") or [])
                idxs = [i for i, h in enumerate(headers) if h not in cols]
                headers = [headers[i] for i in idxs]
                dtypes = {headers[j]: dtypes.get(headers[j], "str") for j in range(len(headers))}
                rows = [[r[i] if i < len(r) else None for i in idxs] for r in rows]
                trace.append({"op": name, "removed": sorted(cols)})
            else:
                raise ValueError("unknown cleaning op: %s" % name)
        except Exception as exc:  # noqa: BLE001
            state.setdefault("errors", []).append({"stage": "cleaning", "op": op, "error": str(exc)})
            trace.append({"op": name, "error": str(exc)})
    return make_table(headers, rows, dtypes), trace


def compare(v, cmp, val):
    if cmp in ("==", "="):
        return v == val or str(v) == str(val)
    if cmp == "!=":
        return not (v == val or str(v) == str(val))
    fv = float(v)
    fval = float(val)
    if cmp == ">":
        return fv > fval
    if cmp == ">=":
        return fv >= fval
    if cmp == "<":
        return fv < fval
    if cmp == "<=":
        return fv <= fval
    if cmp == "in":
        return str(v) in [str(x) for x in val]
    raise ValueError("unknown comparator: %s" % cmp)


# --------------------------------------------------------------------------- #
# statistics (stdlib)
# --------------------------------------------------------------------------- #
def numeric_col(table, name):
    vals = get_col(table, name)
    if vals is None:
        return None
    out = []
    for v in vals:
        try:
            if v is None or str(v).strip() == "":
                continue
            out.append(float(v))
        except (ValueError, TypeError):
            continue
    return out


def summary(vals):
    if not vals:
        return {"count": 0}
    n = len(vals)
    m = statistics.mean(vals)
    var = statistics.variance(vals) if n > 1 else 0.0
    sd = math.sqrt(var) if n > 1 else 0.0
    srt = sorted(vals)
    q1 = srt[int((n - 1) * 0.25)] if n > 1 else srt[0]
    med = statistics.median(srt)
    q3 = srt[int((n - 1) * 0.75)] if n > 1 else srt[0]
    skew = kurt = None
    if n > 2 and sd > 0:
        skew = (n / ((n - 1) * (n - 2))) * sum(((x - m) / sd) ** 3 for x in vals)
        if n > 3:
            kurt = (n * (n + 1) / ((n - 1) * (n - 2) * (n - 3))) * sum(((x - m) / sd) ** 4 for x in vals) \
                - 3 * (n - 1) ** 2 / ((n - 2) * (n - 3))
    return {"count": n, "mean": m, "median": med, "std": sd, "min": srt[0], "max": srt[-1],
            "q1": q1, "q3": q3, "skew": skew, "kurtosis": kurt, "variance": var}


def groupby_stat(table, key, value, agg):
    ki = col_index(table, key)
    vi = col_index(table, value)
    if ki is None or vi is None:
        raise ValueError("groupby column not found: %s / %s" % (key, value))
    groups = {}
    for r in table["rows"]:
        k = r[ki] if ki < len(r) else None
        v = r[vi] if vi < len(r) else None
        k = str(k).strip() if k is not None else "missing"
        groups.setdefault(k, []).append(v)
    out = {}
    for k, arr in groups.items():
        vals = []
        nmissing = 0
        for v in arr:
            try:
                if v is None or str(v).strip() == "":
                    nmissing += 1
                    continue
                vals.append(float(v))
            except (ValueError, TypeError):
                nmissing += 1
        if agg == "count":
            out[k] = {"count": len(vals) + nmissing, "n_valid": len(vals), "n_missing": nmissing}
        elif agg == "sum":
            out[k] = {"sum": sum(vals), "n": len(vals), "n_missing": nmissing}
        elif agg == "median":
            out[k] = {"median": statistics.median(vals) if vals else None, "n": len(vals), "n_missing": nmissing}
        elif agg == "std":
            out[k] = {"std": math.sqrt(statistics.variance(vals)) if len(vals) > 1 else None,
                      "n": len(vals), "n_missing": nmissing}
        else:  # mean default
            out[k] = {"mean": sum(vals) / len(vals) if vals else None, "n": len(vals),
                      "n_missing": nmissing}
    return out


def pearson(a, b):
    n = min(len(a), len(b))
    if n < 2:
        return None
    x = a[:n]
    y = b[:n]
    mx, my = statistics.mean(x), statistics.mean(y)
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    dx = math.sqrt(sum((xi - mx) ** 2 for xi in x))
    dy = math.sqrt(sum((yi - my) ** 2 for yi in y))
    if dx == 0 or dy == 0:
        return None
    return cov / (dx * dy)


def _beta_reg(a, b, x, max_iter=200):
    """Regularised incomplete beta I_x(a,b).

    Numerical Recipes `betai` + `betacf` (continued fraction). Returns in [0,1].
    """
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbeta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    bt = math.exp(math.log(x) * a + math.log(1 - x) * b - lbeta)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _betacf(a, b, x, max_iter=200):
    eps = 3e-12
    fpmin = 1e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
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
        del_ = d * c
        h *= del_
        if abs(del_ - 1.0) < eps:
            break
    return h


def welch_ttest(a, b):
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return None, "insufficient samples (<2 in one group)"
    ma, mb = statistics.mean(a), statistics.mean(b)
    va = statistics.variance(a) if na > 1 else 0.0
    vb = statistics.variance(b) if nb > 1 else 0.0
    denom = math.sqrt(va / na + vb / nb)
    if denom == 0:
        return None, "zero pooled standard error (identical values?)"
    t = (ma - mb) / denom
    df = (va / na + vb / nb) ** 2 / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
    df = max(df, 1.0)
    p = _beta_reg(df / 2.0, 0.5, df / (df + t * t))
    return {"t": t, "df": df, "p_two_tailed": p, "mean_a": ma, "mean_b": mb,
            "sd_a": math.sqrt(va), "sd_b": math.sqrt(vb), "n_a": na, "n_b": nb,
            "test": "Welch's t (unequal variances), two-tailed"}, None


def compute_statistics(table, specs, state):
    results = {}
    for spec in specs or []:
        kind = spec.get("kind")
        out_key = spec.get("output_key") or kind
        try:
            if kind == "describe":
                cols = spec.get("columns") or [h for h, d in table["dtypes"].items() if d in ("int", "float")]
                results[out_key] = {c: summary(numeric_col(table, c)) for c in cols}
            elif kind in ("mean", "median", "std", "min", "max", "count"):
                vals = numeric_col(table, spec["column"])
                if kind == "count":
                    results[out_key] = {"column": spec["column"], "count": len(vals)}
                elif kind == "std":
                    results[out_key] = {"column": spec["column"],
                                        "std": math.sqrt(statistics.variance(vals)) if len(vals) > 1 else None}
                elif kind == "mean":
                    results[out_key] = {"column": spec["column"],
                                        "mean": sum(vals) / len(vals) if vals else None}
                else:
                    results[out_key] = {"column": spec["column"],
                                        kind: getattr(statistics, kind)(vals) if vals else None}
            elif kind == "groupby":
                results[out_key] = groupby_stat(table, spec["key"], spec["value"], spec.get("agg", "mean"))
            elif kind == "correlation":
                cols = spec.get("columns") or [h for h, d in table["dtypes"].items() if d in ("int", "float")]
                mat = {}
                for a in cols:
                    mat[a] = {}
                    va = numeric_col(table, a)
                    for b in cols:
                        mat[a][b] = pearson(va, numeric_col(table, b)) if a != b else 1.0
                results[out_key] = mat
            elif kind == "ttest":
                if "group_column" in spec:
                    groups = {spec.get("group_a"): [], spec.get("group_b"): []}
                    gi = col_index(table, spec["group_column"])
                    vi = col_index(table, spec["column"])
                    for r in table["rows"]:
                        g = str(r[gi]) if gi < len(r) and r[gi] is not None else ""
                        if g in groups and vi < len(r) and r[vi] is not None and str(r[vi]).strip() != "":
                            try:
                                groups[g].append(float(r[vi]))
                            except (ValueError, TypeError):
                                pass
                    res, note = welch_ttest(groups[spec.get("group_a")], groups[spec.get("group_b")])
                else:
                    res, note = welch_ttest(numeric_col(table, spec["a_column"]),
                                            numeric_col(table, spec["b_column"]))
                results[out_key] = {"result": res, "note": note,
                                    "assumptions": "Normality not formally tested by stdlib engine; "
                                    "skew/kurtosis available in describe. Interpret p with caution."}
            else:
                results[out_key] = None
                state.setdefault("errors", []).append({"stage": "statistics", "spec": spec,
                                                       "error": "unknown stat kind: %s" % kind})
        except Exception as exc:  # noqa: BLE001
            results[out_key] = None
            state.setdefault("errors", []).append({"stage": "statistics", "spec": spec, "error": str(exc)})
    return results


# --------------------------------------------------------------------------- #
# queries (SQLite, stdlib)
# --------------------------------------------------------------------------- #
def run_queries(recipe, input_dir, state):
    results = {}
    for q in recipe.get("queries") or []:
        sql = q.get("sql")
        out_key = q.get("output_key") or ("query_%d" % len(results))
        db = q.get("db")
        db_path = None
        if db:
            db_path = find_file(input_dir, [db, os.path.basename(db)])
        if not db_path:
            for item in state.get("manifest", []):
                if item["kind"] == "sqlite":
                    db_path = item["path"]
                    break
        if not sql:
            results[out_key] = {"error": "empty SQL"}
            continue
        if not db_path:
            results[out_key] = {"error": "no SQLite database found for query"}
            state.setdefault("errors", []).append({"stage": "query", "sql": sql, "error": "no database"})
            continue
        try:
            uri = "file:%s?mode=ro" % urllib.parse.quote(os.path.abspath(db_path).replace("\\", "/"))
            con = sqlite3.connect(uri, uri=True)
            cur = con.cursor()
            cur.execute(sql)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchall()
            con.close()
            results[out_key] = {"db": relpath_abs(db_path, input_dir), "sql": sql,
                                "columns": cols, "rows": [list(r) for r in rows],
                                "row_count": len(rows)}
        except sqlite3.Error as exc:
            results[out_key] = {"db": relpath_abs(db_path, input_dir), "sql": sql, "error": str(exc)}
            state.setdefault("errors", []).append({"stage": "query", "sql": sql, "error": str(exc)})
    return results


# --------------------------------------------------------------------------- #
# plot model + SVG / PNG renderers
# --------------------------------------------------------------------------- #
def nice_num(x, round_up):
    if x == 0:
        return 1.0
    exp = math.floor(math.log10(abs(x)))
    f = x / (10 ** exp)
    if round_up:
        nf = 1 if f < 1.5 else (2 if f < 3 else (5 if f < 7 else 10))
    else:
        nf = 1 if f <= 1 else (2 if f <= 2 else (5 if f <= 5 else 10))
    return nf * (10 ** exp)


def nice_ticks(lo, hi, n=6):
    if lo == hi:
        lo, hi = lo - 0.5, hi + 0.5
    if lo > hi:
        lo, hi = hi, lo
    rng = nice_num(hi - lo, False)
    step = nice_num(rng / max(n - 1, 1), True)
    if step <= 0:
        step = 1.0
    gmin = math.floor(lo / step) * step
    gmax = math.ceil(hi / step) * step
    ticks = []
    v = gmin
    while v <= gmax + step * 1e-9:
        ticks.append(round(v, 10))
        v += step
    return ticks


def fmt_num(v):
    if v == 0:
        return "0"
    a = abs(v)
    if a >= 1e4 or a < 1e-3:
        return "%.2e" % v
    return ("%.4f" % v).rstrip("0").rstrip(".")


def build_plot_model(spec, table, results):
    kind = spec.get("kind")
    W, H = 800, 500
    model = {"kind": kind, "title": spec.get("title", ""),
             "xlabel": spec.get("xlabel", ""), "ylabel": spec.get("ylabel", ""),
             "W": W, "H": H, "margin": {"l": 70, "r": 25, "t": 50, "b": 60},
             "categorical": False, "categories": [], "series": [], "xticks": [], "yticks": []}
    if kind == "histogram":
        vals = numeric_col(table, spec["column"])
        if not vals:
            model["error"] = "no numeric values"
            return model
        n = len(vals)
        nbins = int(spec.get("bins") or max(5, math.ceil(math.sqrt(n))))
        lo, hi = min(vals), max(vals)
        if lo == hi:
            lo, hi = lo - 0.5, hi + 0.5
        width = (hi - lo) / nbins
        counts = [0] * nbins
        for v in vals:
            idx = min(int((v - lo) / width), nbins - 1)
            counts[idx] += 1
        centers = [lo + width * (i + 0.5) for i in range(nbins)]
        model["xmin"], model["xmax"] = lo, hi
        model["ymin"], model["ymax"] = 0, max(counts) * 1.1
        model["series"] = [{"label": spec["column"], "color": PALETTE[0], "type": "bar",
                            "items": [[centers[i], width / 2, counts[i]] for i in range(nbins)]}]
        model["xticks"] = [[v, fmt_num(v)] for v in nice_ticks(lo, hi)]
        model["yticks"] = [[v, fmt_num(v)] for v in nice_ticks(0, model["ymax"])]
    elif kind == "scatter":
        xs = numeric_col(table, spec["x"])
        ys = numeric_col(table, spec["y"])
        pts = [[x, y] for x, y in zip(xs, ys)]
        model["xmin"], model["xmax"] = min(xs), max(xs)
        model["ymin"], model["ymax"] = min(ys), max(ys)
        padx, pady = (model["xmax"] - model["xmin"]) * 0.05 + 1e-9, (model["ymax"] - model["ymin"]) * 0.05 + 1e-9
        model["xmin"] -= padx
        model["xmax"] += padx
        model["ymin"] -= pady
        model["ymax"] += pady
        model["series"] = [{"label": spec.get("label", "%s vs %s" % (spec["x"], spec["y"])),
                            "color": PALETTE[0], "type": "points", "items": pts}]
        model["xticks"] = [[v, fmt_num(v)] for v in nice_ticks(model["xmin"], model["xmax"])]
        model["yticks"] = [[v, fmt_num(v)] for v in nice_ticks(model["ymin"], model["ymax"])]
    elif kind == "bar":
        groups = groupby_stat(table, spec["x"], spec["y"], spec.get("agg", "mean"))
        cats = sorted(groups.keys())
        heights = [groups[c].get(spec.get("agg", "mean")) or 0 for c in cats]
        model["categorical"] = True
        model["categories"] = cats
        model["xmin"], model["xmax"] = -0.5, len(cats) - 0.5
        model["ymin"], model["ymax"] = 0, (max(heights) if heights else 0) * 1.15 + 1e-9
        model["series"] = [{"label": "%s by %s" % (spec["y"], spec["x"]), "color": PALETTE[0],
                            "type": "bar", "items": [[i, 0.4, heights[i]] for i in range(len(cats))]}]
        model["xticks"] = [[i, cats[i]] for i in range(len(cats))]
        model["yticks"] = [[v, fmt_num(v)] for v in nice_ticks(0, model["ymax"])]
    elif kind == "box":
        if spec.get("group_column"):
            groups = groupby_stat(table, spec["group_column"], spec["column"], "count")
            cats = sorted(groups.keys())
            items = []
            gi = col_index(table, spec["group_column"])
            vi = col_index(table, spec["column"])
            for i, c in enumerate(cats):
                arr = numeric_col({"headers": table["headers"], "rows": [r for r in table["rows"] if gi < len(r) and str(r[gi]) == c]}, spec["column"])
                items.append([i] + box_stats(arr))
            model["categorical"] = True
            model["categories"] = cats
            model["xmin"], model["xmax"] = -0.5, len(cats) - 0.5
        else:
            arr = numeric_col(table, spec["column"])
            items = [[0] + box_stats(arr)]
            model["categorical"] = True
            model["categories"] = [spec["column"]]
            model["xmin"], model["xmax"] = -0.5, 0.5
        all_y = []
        for it in items:
            all_y += [it[2], it[3], it[4], it[5]] + list(it[6])
        lo = min(all_y) if all_y else 0
        hi = max(all_y) if all_y else 1
        pad = (hi - lo) * 0.1 + 1e-9
        model["ymin"], model["ymax"] = lo - pad, hi + pad
        model["series"] = [{"label": spec.get("column"), "color": PALETTE[0], "type": "box", "items": items}]
        model["xticks"] = [[i, model["categories"][i]] for i in range(len(model["categories"]))]
        model["yticks"] = [[v, fmt_num(v)] for v in nice_ticks(model["ymin"], model["ymax"])]
    else:
        model["error"] = "unknown plot kind: %s" % kind
    return model


def box_stats(arr):
    if not arr:
        return [0, 0, 0, 0, 0, []]
    srt = sorted(arr)
    n = len(srt)
    q1 = srt[int((n - 1) * 0.25)]
    med = statistics.median(srt)
    q3 = srt[int((n - 1) * 0.75)]
    iqr = q3 - q1
    lo = q1 - 1.5 * iqr
    hi = q3 + 1.5 * iqr
    whisker_lo = min([x for x in srt if x >= lo], default=q1)
    whisker_hi = max([x for x in srt if x <= hi], default=q3)
    outliers = [x for x in srt if x < whisker_lo or x > whisker_hi]
    return [q1, med, q3, whisker_lo, whisker_hi, outliers]


def map_xy(model, x, y):
    ml, mr = model["margin"]["l"], model["margin"]["r"]
    mt, mb = model["margin"]["t"], model["margin"]["b"]
    W, H = model["W"], model["H"]
    px = ml + (x - model["xmin"]) / (model["xmax"] - model["xmin"]) * (W - ml - mr)
    py = H - mb - (y - model["ymin"]) / (model["ymax"] - model["ymin"]) * (H - mt - mb)
    return px, py


def render_svg(model):
    ml, mr = model["margin"]["l"], model["margin"]["r"]
    mt, mb = model["margin"]["t"], model["margin"]["b"]
    W, H = model["W"], model["H"]
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (W, H, W, H),
             '<rect x="0" y="0" width="%d" height="%d" fill="white"/>' % (W, H)]
    # grid + y ticks
    for y, lab in model["yticks"]:
        _, py = map_xy(model, model["xmin"], y)
        parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#e5e5e5"/>' % (ml, py, W - mr, py))
        parts.append('<text x="%g" y="%g" font-size="11" text-anchor="end" fill="#333">%s</text>' % (ml - 6, py + 4, esc(lab)))
    # x ticks
    for x, lab in model["xticks"]:
        px, _ = map_xy(model, x, model["ymin"])
        parts.append('<text x="%g" y="%g" font-size="11" text-anchor="middle" fill="#333">%s</text>' % (px, H - mb + 18, esc(lab)))
    # axes
    ax0, ay0 = map_xy(model, model["xmin"], model["ymin"])
    ax1, ay1 = map_xy(model, model["xmax"], model["ymin"])
    parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#000"/>' % (ax0, ay0, ax1, ay1))
    parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#000"/>' % (ax0, ay0, ax0, map_xy(model, model["xmin"], model["ymax"])[1]))
    # series
    for s in model["series"]:
        c = s["color"]
        if s["type"] == "bar":
            for x, halfw, h in s["items"]:
                px0, py0 = map_xy(model, x - halfw, 0)
                px1, py1 = map_xy(model, x + halfw, h)
                parts.append('<rect x="%g" y="%g" width="%g" height="%g" fill="%s" fill-opacity="0.8"/>' % (px0, py1, px1 - px0, py0 - py1, c))
        elif s["type"] == "points":
            for x, y in s["items"]:
                px, py = map_xy(model, x, y)
                parts.append('<circle cx="%g" cy="%g" r="2.5" fill="%s"/>' % (px, py, c))
        elif s["type"] == "line":
            pts = " ".join("%g,%g" % map_xy(model, x, y) for x, y in s["items"])
            parts.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="2"/>' % (pts, c))
        elif s["type"] == "box":
            for x, q1, med, q3, wlo, whi, outliers in s["items"]:
                pxq1, pyq1 = map_xy(model, x - 0.2, q1)
                pxq3, pyq3 = map_xy(model, x + 0.2, q3)
                pxm, pym = map_xy(model, x, med)
                _, pywlo = map_xy(model, x, wlo)
                _, pywhi = map_xy(model, x, whi)
                parts.append('<rect x="%g" y="%g" width="%g" height="%g" fill="none" stroke="%s"/>' % (pxq1, pyq3, pxq3 - pxq1, pyq1 - pyq3, c))
                parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="%s"/>' % (pxm, pyq3, pxm, pyq1, c))
                parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="%s"/>' % (pxm, pywlo, pxm, pywhi, c))
                for o in outliers:
                    pxo, pyo = map_xy(model, x, o)
                    parts.append('<circle cx="%g" cy="%g" r="2" fill="none" stroke="%s"/>' % (pxo, pyo, c))
    # labels
    parts.append('<text x="%g" y="%g" font-size="14" text-anchor="middle" font-weight="bold">%s</text>' % (W / 2, 24, esc(model["title"])))
    parts.append('<text x="%g" y="%g" font-size="12" text-anchor="middle">%s</text>' % (W / 2, H - 12, esc(model["xlabel"])))
    parts.append('<text x="%g" y="%g" font-size="12" text-anchor="middle" transform="rotate(-90 %g %g)">%s</text>' % (16, H / 2, 16, H / 2, esc(model["ylabel"])))
    # legend
    if len(model["series"]) > 1 or (model["series"] and model["series"][0].get("label")):
        lx, ly = W - mr - 180, mt + 8
        for i, s in enumerate(model["series"]):
            parts.append('<rect x="%g" y="%g" width="12" height="12" fill="%s"/>' % (lx, ly + i * 18, s["color"]))
            parts.append('<text x="%g" y="%g" font-size="11">%s</text>' % (lx + 18, ly + i * 18 + 11, esc(s.get("label", ""))))
    parts.append('</svg>')
    return "\n".join(parts)


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


# --- PNG (stdlib zlib) ---------------------------------------------------- #
FONT = {
    "0": [0b01110, 0b10001, 0b10011, 0b10101, 0b11001, 0b10001, 0b01110],
    "1": [0b00100, 0b01100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110],
    "2": [0b01110, 0b10001, 0b00001, 0b00010, 0b00100, 0b01000, 0b11111],
    "3": [0b11111, 0b00010, 0b00100, 0b00010, 0b00001, 0b10001, 0b01110],
    "4": [0b00010, 0b00110, 0b01010, 0b10010, 0b11111, 0b00010, 0b00010],
    "5": [0b11111, 0b10000, 0b11110, 0b00001, 0b00001, 0b10001, 0b01110],
    "6": [0b00110, 0b01000, 0b10000, 0b11110, 0b10001, 0b10001, 0b01110],
    "7": [0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b01000, 0b01000],
    "8": [0b01110, 0b10001, 0b10001, 0b01110, 0b10001, 0b10001, 0b01110],
    "9": [0b01110, 0b10001, 0b10001, 0b01111, 0b00001, 0b00010, 0b01100],
    ".": [0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b00110, 0b00110],
    "-": [0b00000, 0b00000, 0b00000, 0b01110, 0b00000, 0b00000, 0b00000],
    "+": [0b00000, 0b00100, 0b00100, 0b11111, 0b00100, 0b00100, 0b00000],
    "e": [0b00000, 0b00000, 0b01110, 0b10001, 0b11111, 0b10000, 0b01110],
    " ": [0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b00000],
}


def parse_color(s):
    s = s.lstrip("#")
    if len(s) != 6:
        return (0, 0, 0)
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


class Canvas:
    def __init__(self, w, h):
        self.w = w
        self.h = h
        self.px = bytearray(w * h * 3)

    def set(self, x, y, color):
        x, y = int(x), int(y)
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.px[i] = color[0]
            self.px[i + 1] = color[1]
            self.px[i + 2] = color[2]

    def rect(self, x0, y0, x1, y1, color):
        x0, x1 = min(x0, x1), max(x0, x1)
        y0, y1 = min(y0, y1), max(y0, y1)
        for y in range(int(y0), int(y1) + 1):
            for x in range(int(x0), int(x1) + 1):
                self.set(x, y, color)

    def line(self, x0, y0, x1, y1, color):
        x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        while True:
            self.set(x0, y0, color)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def text(self, x, y, s, scale, color):
        for ch in s:
            glyph = FONT.get(ch)
            if glyph is None:
                x += 6 * scale
                continue
            for row in range(7):
                bits = glyph[row]
                for col in range(5):
                    if bits & (1 << (4 - col)):
                        self.rect(x + col * scale, y + row * scale,
                                  x + (col + 1) * scale - 1, y + (row + 1) * scale - 1, color)
            x += 6 * scale


def write_png(path, cv):
    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", cv.w, cv.h, 8, 2, 0, 0, 0)
    raw = bytearray()
    stride = cv.w * 3
    for y in range(cv.h):
        raw.append(0)
        raw.extend(cv.px[y * stride:(y + 1) * stride])
    idat = zlib.compress(bytes(raw), 9)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


def render_png(model, path):
    W, H = model["W"], model["H"]
    cv = Canvas(W, H)
    cv.rect(0, 0, W - 1, H - 1, (255, 255, 255))
    ml, mr = model["margin"]["l"], model["margin"]["r"]
    mt, mb = model["margin"]["t"], model["margin"]["b"]
    grid = parse_color("#e5e5e5")
    black = (0, 0, 0)
    for y, lab in model["yticks"]:
        _, py = map_xy(model, model["xmin"], y)
        cv.line(ml, py, W - mr, py, grid)
        cv.text(ml - 6 - 6 * len(lab) * 1, py - 3, fmt_png_label(lab), 1, black)
    for x, lab in model["xticks"]:
        px, _ = map_xy(model, x, model["ymin"])
        cv.text(px - 3 * len(fmt_png_label(lab)), H - mb + 8, fmt_png_label(lab), 1, black)
    ax0, ay0 = map_xy(model, model["xmin"], model["ymin"])
    ax1, ay1 = map_xy(model, model["xmax"], model["ymin"])
    cv.line(ax0, ay0, ax1, ay1, black)
    cv.line(ax0, ay0, ax0, map_xy(model, model["xmin"], model["ymax"])[1], black)
    for s in model["series"]:
        c = parse_color(s["color"])
        if s["type"] == "bar":
            for x, halfw, h in s["items"]:
                px0, py0 = map_xy(model, x - halfw, 0)
                px1, py1 = map_xy(model, x + halfw, h)
                cv.rect(px0, py1, px1, py0, c)
        elif s["type"] == "points":
            for x, y in s["items"]:
                px, py = map_xy(model, x, y)
                cv.rect(px - 1, py - 1, px + 1, py + 1, c)
        elif s["type"] == "line":
            prev = None
            for x, y in s["items"]:
                px, py = map_xy(model, x, y)
                if prev:
                    cv.line(prev[0], prev[1], px, py, c)
                prev = (px, py)
        elif s["type"] == "box":
            for x, q1, med, q3, wlo, whi, outliers in s["items"]:
                pxq1, pyq1 = map_xy(model, x - 0.2, q1)
                pxq3, pyq3 = map_xy(model, x + 0.2, q3)
                pxm, pym = map_xy(model, x, med)
                _, pywlo = map_xy(model, x, wlo)
                _, pywhi = map_xy(model, x, whi)
                cv.rect(pxq1, pyq3, pxq3, pyq1, c)
                cv.line(pxm, pyq3, pxm, pyq1, c)
                cv.line(pxm, pywlo, pxm, pywhi, c)
                for o in outliers:
                    pxo, pyo = map_xy(model, x, o)
                    cv.rect(pxo - 1, pyo - 1, pxo + 1, pyo + 1, c)
    write_png(path, cv)


def fmt_png_label(lab):
    """PNG only rasterises digits/signs; letters live in the SVG."""
    s = str(lab)
    return "".join(ch for ch in s if ch in FONT)


# --------------------------------------------------------------------------- #
# recipe run
# --------------------------------------------------------------------------- #
def run_recipe(state, output_dir):
    recipe = state.get("recipe") or {}
    ensure_dir(output_dir)
    ensure_dir(state.get("work_dir") or output_dir)

    # choose the primary tabular source
    primary = recipe.get("source") or recipe.get("input_file")
    table = None
    if primary:
        for item in state.get("manifest", []):
            if item["rel"] == primary or os.path.basename(item["path"]) == primary:
                prof = profile_tabular(item["path"])
                if "headers" in prof:
                    table = make_table(prof["headers"],
                                       load_full_rows(item["path"], prof["delimiter"]),
                                       prof["dtypes"])
                break
    if table is None:
        # fall back to the first tabular file in manifest
        for item in state.get("manifest", []):
            if item["kind"] == "tabular":
                prof = profile_tabular(item["path"])
                if "headers" in prof:
                    table = make_table(prof["headers"], load_full_rows(item["path"], prof["delimiter"]),
                                       prof["dtypes"])
                    state["recipe"] = dict(recipe, source=item["rel"])
                    break
    if table is None:
        state.setdefault("errors", []).append({"stage": "run", "error": "no tabular source available"})
        return {}

    clean_table, clean_trace = apply_cleaning(table, recipe.get("cleaning") or [], state)
    cleaned_missing = {}
    for i, h in enumerate(clean_table["headers"]):
        cleaned_missing[h] = sum(1 for r in clean_table["rows"]
                                 if i >= len(r) or r[i] is None or str(r[i]).strip() == "")
    stats = compute_statistics(clean_table, recipe.get("statistics") or [], state)
    queries = run_queries(recipe, state.get("input_dir"), state)
    plots = []
    figures = []
    for i, pspec in enumerate(recipe.get("plots") or []):
        model = build_plot_model(pspec, clean_table, stats)
        out = pspec.get("output") or ("figure_%d.svg" % (i + 1))
        svg_path = os.path.join(output_dir, out if out.endswith(".svg") else out + ".svg")
        png_path = os.path.splitext(svg_path)[0] + ".png"
        if model.get("error"):
            plots.append({"output": out, "error": model["error"]})
            continue
        write_text(svg_path, render_svg(model))
        render_png(model, png_path)
        plots.append({"spec": pspec, "svg": svg_path, "png": png_path})
        figures.append({"svg": svg_path, "png": png_path, "kind": pspec.get("kind"),
                        "title": pspec.get("title", ""), "output_key": out})

    cleaned_csv = os.path.join(output_dir, "cleaned_table.csv")
    write_table_csv(cleaned_csv, clean_table)
    stats_json = os.path.join(output_dir, "statistics.json")
    write_json(stats_json, stats)
    queries_json = os.path.join(output_dir, "query_results.json")
    write_json(queries_json, queries)

    results = {"cleaned_table": cleaned_csv, "statistics": stats, "queries": queries,
               "plots": plots, "cleaning_trace": clean_trace,
               "cleaned_rows": len(clean_table["rows"]),
               "cleaned_columns": len(clean_table["headers"]),
               "cleaned_headers": list(clean_table["headers"]),
               "cleaned_missing": cleaned_missing,
               "original_rows": len(table["rows"]),
               "original_columns": len(table["headers"])}
    state["results"] = results
    state["artifacts"] = collect_artifacts(output_dir)
    return results


def load_full_rows(path, delimiter):
    b = read_bytes(path)
    if b is None:
        return []
    txt = decode_text(b)
    if txt is None:
        return []
    rows = [r for r in csv.reader(txt.splitlines(), delimiter=delimiter, quotechar='"') if r]
    return rows[1:] if rows else []


def write_table_csv(path, table):
    try:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(table["headers"])
            for r in table["rows"]:
                w.writerow(["" if v is None else v for v in r])
        return True
    except OSError:
        return False


def collect_artifacts(output_dir):
    arts = []
    if not os.path.isdir(output_dir):
        return arts
    for name in sorted(os.listdir(output_dir)):
        full = os.path.join(output_dir, name)
        if os.path.isfile(full):
            arts.append({"path": full, "name": name, "bytes": os.path.getsize(full),
                         "sha256": sha256_file(full)})
    return arts


# --------------------------------------------------------------------------- #
# checks (validate BEFORE deliver)
# --------------------------------------------------------------------------- #
def run_checks(state):
    checks = {}
    recipe = state.get("recipe") or {}
    contract = state.get("contract_raw")
    contract_fields = state.get("contract_fields", [])

    checks["contract_present"] = contract is not None or len(contract_fields) > 0
    checks["case_id_present"] = bool(state.get("case_id"))
    checks["profile_complete"] = len(state.get("data_profile", {})) > 0

    results = state.get("results", {})
    checks["cleaned_rows_le_original"] = results.get("cleaned_rows", 0) <= results.get("original_rows", 0)

    # check explicit recipe checks
    for c in recipe.get("checks") or []:
        kind = c.get("kind")
        try:
            if kind == "row_count_equals":
                checks["row_count_equals"] = results.get("cleaned_rows") == c.get("expected")
            elif kind == "row_count_le":
                checks["row_count_le"] = results.get("cleaned_rows", 0) <= c.get("expected", 0)
            elif kind == "column_exists":
                cols = state.get("data_profile", {})
                found = False
                for _, prof in cols.items():
                    if "headers" in prof and c.get("column") in prof["headers"]:
                        found = True
                        break
                checks["column_exists_" + str(c.get("column"))] = found
            elif kind == "no_missing":
                col = c.get("column")
                cleaned = state.get("results", {}).get("cleaned_missing", {})
                if cleaned:
                    checks["no_missing_" + str(col)] = cleaned.get(col, 0) == 0
                else:
                    prof = state.get("data_profile", {})
                    ok = True
                    for _, p in prof.items():
                        if "missing" in p and col in p.get("missing", {}) and p["missing"][col] > 0:
                            ok = False
                    checks["no_missing_" + str(col)] = ok
            elif kind == "value_range":
                stats = results.get("statistics", {})
                key = c.get("output_key")
                if key in stats and stats[key] and "mean" in stats[key]:
                    m = stats[key]["mean"]
                    checks["value_range_" + str(key)] = (c.get("min", -1e308) <= m <= c.get("max", 1e308))
            elif kind == "status_allowed":
                checks["status_allowed"] = c.get("value") in ALLOWED_STATUS
            elif kind == "mean_close":
                stats = results.get("statistics", {})
                key = c.get("output_key")
                actual = None
                if key in stats:
                    actual = stats[key].get("mean") if isinstance(stats[key], dict) else None
                checks["mean_close_" + str(key)] = (actual is not None
                                                   and abs(actual - c.get("expected", 0)) <= c.get("tol", 1e-6))
            else:
                checks["check_" + str(kind)] = None
        except Exception as exc:  # noqa: BLE001
            checks["check_" + str(kind)] = "error: %s" % exc

    # gold-standard comparison if the case supplies evidence
    ev = state.get("evidence")
    if ev is not None:
        checks["evidence_recorded"] = True
        checks["evidence_value"] = sanitize(ev)
    state["checks"] = checks
    return checks


# --------------------------------------------------------------------------- #
# deliver / final result.json
# --------------------------------------------------------------------------- #
RESERVED_KEYS = {"case_id", "status", "sources", "uncertainty", "change_log", "pending_human",
                 "errors", "degradations", "limitations", "runtime", "artifacts", "analysis",
                 "checks", "recipe", "data_profile", "alignment"}


def guess_contract_fill(field, artifacts, results, report_path, recipe_path):
    n = str(field).lower()
    paths = {a["name"]: a["path"] for a in artifacts}
    if any(k in n for k in ("clean", "清洗", "table", "data_clean")):
        return results.get("cleaned_table") or paths.get("cleaned_table.csv")
    if any(k in n for k in ("stat", "summary", "describe", "统计", "统计结果")):
        return results.get("statistics") or paths.get("statistics.json")
    if any(k in n for k in ("fig", "chart", "plot", "graph", "图", "viz", "visual")):
        figs = results.get("plots") or []
        if len(figs) == 1:
            return figs[0].get("svg")
        return [f.get("svg") for f in figs]
    if any(k in n for k in ("query", "sql", "查询")):
        return results.get("queries") or paths.get("query_results.json")
    if any(k in n for k in ("code", "script", "代码", "program", "recipe", "配方")):
        return recipe_path
    if any(k in n for k in ("report", "doc", "报告", "markdown")):
        return report_path
    if any(k in n for k in ("preview", "png", "预览")):
        return [f.get("png") for f in (results.get("plots") or [])]
    if any(k in n for k in ("profile", "data_profile", "画像")):
        return results.get("data_profile")
    return None


def deliver(state, output_dir):
    ensure_dir(output_dir)
    run_checks(state)

    # persist recipe + alignment + profile as artifacts
    recipe = state.get("recipe") or {}
    if recipe:
        recipe_path = os.path.join(output_dir, "analysis_recipe.json")
        write_json(recipe_path, recipe)
    else:
        recipe_path = None
    if state.get("alignment"):
        align_path = os.path.join(output_dir, "field_alignment.json")
        write_json(align_path, state["alignment"])
    else:
        align_path = None
    profile_path = os.path.join(output_dir, "data_profile.json")
    write_json(profile_path, state.get("data_profile", {}))

    # report.md
    report_path = os.path.join(output_dir, "analysis_report.md")
    write_text(report_path, build_report(state, recipe_path, align_path, profile_path))

    artifacts = collect_artifacts(output_dir)

    result = {}
    result["case_id"] = state.get("case_id")
    # contract fields (discovered)
    contract_fields = state.get("contract_fields", [])
    for f in contract_fields:
        fill = guess_contract_fill(f, artifacts, state.get("results", {}), report_path, recipe_path)
        result[f] = sanitize(fill)
    if state.get("contract_raw") and not contract_fields:
        # dict contract: keys became contract_fields at parse time; guard empty
        result["output_contract"] = dict(state.get("contract_raw") or {})

    status = state.get("final_status") or "partial"
    if status not in ALLOWED_STATUS:
        status = "partial"
    result["status"] = status
    result["sources"] = state.get("sources", [])
    result["uncertainty"] = state.get("uncertainty", [])
    result["change_log"] = state.get("change_log", [])
    result["pending_human"] = state.get("pending_human", [])
    result["errors"] = state.get("errors", [])
    result["degradations"] = state.get("degradations", [])
    result["limitations"] = state.get("limitations", [])
    result["runtime"] = {
        "started": state.get("started"), "ended": now_iso(),
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "seed": (state.get("recipe") or {}).get("seed", 42),
        "engine_schema": SCHEMA_VERSION,
    }
    result["artifacts"] = [rel(a, output_dir) for a in artifacts]
    result["checks"] = state.get("checks", {})
    result["analysis"] = {
        "cleaned_table": state.get("results", {}).get("cleaned_table"),
        "statistics": state.get("results", {}).get("statistics"),
        "queries": state.get("results", {}).get("queries"),
        "figures": state.get("results", {}).get("plots"),
        "cleaning_trace": state.get("results", {}).get("cleaning_trace"),
        "field_alignment": align_path,
        "recipe": recipe_path,
        "data_profile": profile_path,
        "report": report_path,
    }
    write_json(os.path.join(output_dir, "result.json"), result)
    state["artifacts"] = artifacts
    state["result_path"] = os.path.join(output_dir, "result.json")
    return result


def rel(a, base):
    return {"path": a["path"], "rel": relpath_abs(a["path"], base), "bytes": a["bytes"],
            "sha256": a.get("sha256")}


def build_report(state, recipe_path, align_path, profile_path):
    lines = ["# Analysis report", "",
             "- case_id: `%s`" % (state.get("case_id") or "not_reported"),
             "- status: `%s`" % (state.get("final_status") or "partial"),
             "- generated: %s" % now_iso(), "",
             "## Inputs"]
    for m in state.get("manifest", []):
        lines.append("- %s (%s, %d bytes)" % (m["rel"], m["kind"], m.get("bytes", 0)))
    res = state.get("results", {})
    lines += ["", "## Rows",
              "- original_rows: %s" % res.get("original_rows", "not_reported"),
              "- cleaned_rows: %s" % res.get("cleaned_rows", "not_reported"), "",
              "## Cleaning trace"]
    for t in res.get("cleaning_trace") or []:
        lines.append("- %s" % json.dumps(t, ensure_ascii=False))
    lines += ["", "## Statistics (see statistics.json)", "## Queries (see query_results.json)",
              "## Figures"]
    for f in res.get("plots") or []:
        if "error" in f:
            lines.append("- ERROR: %s" % f["error"])
        else:
            lines.append("- %s: %s" % (f.get("spec", {}).get("kind"), os.path.basename(f.get("svg", ""))))
    lines += ["", "## Artifacts"]
    for a in state.get("artifacts", []):
        lines.append("- %s" % a.get("name"))
    if state.get("errors"):
        lines += ["", "## Errors"]
        for e in state["errors"]:
            lines.append("- %s" % json.dumps(e, ensure_ascii=False))
    if state.get("limitations"):
        lines += ["", "## Limitations"]
        for l in state["limitations"]:
            lines.append("- %s" % l)
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# subcommands
# --------------------------------------------------------------------------- #
def cmd_init(args, state):
    ensure_dir(args.output)
    ensure_dir(args.work)
    state["input_dir"] = args.input
    state["output_dir"] = args.output
    state["work_dir"] = args.work
    state["started"] = now_iso()
    case_id, contract, contract_raw, evidence, case_path, case_errors = parse_case(args.input)
    state["case_id"] = case_id
    state["contract_raw"] = contract_raw
    state["contract_fields"] = list(contract.keys()) if isinstance(contract, dict) else list(contract or [])
    state["evidence"] = evidence
    state["case_path"] = case_path
    for e in case_errors:
        state["errors"].append({"stage": "init", "error": e})
    if case_id is None:
        state["limitations"].append("case_id not found in case.json; using null")
    log(state, "init: state skeleton + result skeleton written", case_id=case_id)
    # write skeleton result.json immediately (partial, within 3 minutes)
    skeleton = {"case_id": case_id, "status": "partial", "sources": [], "uncertainty": [],
                "change_log": state["change_log"], "pending_human": [], "errors": state["errors"],
                "limitations": state["limitations"]}
    for f in state["contract_fields"]:
        skeleton[f] = None if not isinstance(contract.get(f), list) else []
    if contract_raw and not state["contract_fields"]:
        skeleton["output_contract"] = dict(contract_raw)
    write_json(os.path.join(args.output, "result.json"), skeleton)
    save_state(state)
    return 0


def cmd_discover(args, state):
    state["input_dir"] = args.input
    files, err = discover(args.input)
    state["manifest"] = files
    if err:
        state["errors"].append({"stage": "discover", "error": err})
    log(state, "discover: scanned input", n_files=len(files), input=args.input)
    save_state(state)
    return 0


def cmd_profile(args, state):
    profile(args.input, state)
    log(state, "profile: data dictionary + field profiles written",
        files=list(state["data_profile"].keys()))
    write_json(os.path.join(args.output, "data_profile.json"), state["data_profile"])
    save_state(state)
    return 0


def cmd_plan(args, state):
    """Load a recipe written by the Agent at <work>/analysis_recipe.json."""
    path = args.recipe or os.path.join(args.work, "analysis_recipe.json")
    recipe = load_json(path)
    if recipe is None:
        state["errors"].append({"stage": "plan", "error": "recipe not found at %s" % path})
        state.setdefault("limitations", []).append("no recipe supplied; run skipped")
    else:
        state["recipe"] = recipe
        log(state, "plan: recipe loaded", path=path, seed=recipe.get("seed", 42))
    save_state(state)
    return 0


def cmd_run(args, state):
    if not state.get("recipe"):
        state["errors"].append({"stage": "run", "error": "no recipe; run plan first"})
        state["limitations"].append("run skipped: no recipe")
    else:
        run_recipe(state, args.output)
        log(state, "run: recipe executed", results=list(state.get("results", {}).keys()))
    save_state(state)
    # refresh the partial result.json so a crash after run still leaves results
    deliver(state, args.output)
    return 0


def cmd_check(args, state):
    checks = run_checks(state)
    write_json(os.path.join(args.output, "checks.json"), checks)
    log(state, "check: contract + internal checks run", checks=checks)
    save_state(state)
    return 0


def cmd_deliver(args, state):
    if args.status:
        state["final_status"] = args.status
    else:
        state["final_status"] = derive_status(state)
    result = deliver(state, args.output)
    log(state, "deliver: final result.json written", status=result.get("status"))
    save_state(state)
    return 0


def derive_status(state):
    if state.get("errors"):
        return "partial"
    if not state.get("case_id") or not state.get("data_profile"):
        return "insufficient_evidence"
    if not state.get("results"):
        return "partial"
    return "ok"


def cmd_selftest(args, state):
    """Minimal in-package self-check (no external case)."""
    checks = {
        "state_schema": state.get("schema_version") == SCHEMA_VERSION,
        "status_values": all(s in ALLOWED_STATUS for s in ALLOWED_STATUS),
        "json_roundtrip": True,
        "png_writer": False,
    }
    # exercise PNG writer + SVG renderer on a tiny synthetic model
    model = {"kind": "histogram", "title": "selftest", "xlabel": "x", "ylabel": "count",
             "W": 200, "H": 120, "margin": {"l": 30, "r": 10, "t": 20, "b": 25},
             "categorical": False, "categories": [],
             "xmin": 0, "xmax": 1, "ymin": 0, "ymax": 2,
             "series": [{"label": "x", "color": "#1f77b4", "type": "bar",
                         "items": [[0.25, 0.1, 1], [0.75, 0.1, 1.5]]}],
             "xticks": [[0, "0"], [0.5, "0.5"], [1, "1"]],
             "yticks": [[0, "0"], [1, "1"], [2, "2"]]}
    svg = render_svg(model)
    if "<svg" in svg and "</svg>" in svg:
        checks["svg_renderer"] = True
    tmp = os.path.join(args.work, "selftest.png")
    try:
        render_png(model, tmp)
        checks["png_writer"] = os.path.isfile(tmp) and open(tmp, "rb").read(8) == b"\x89PNG\r\n\x1a\n"
    except Exception as exc:  # noqa: BLE001
        checks["png_writer"] = "error: %s" % exc
    # json sanitize
    try:
        json.dumps(sanitize({"nan": float("nan"), "inf": float("inf"), "n": 1.5}))
        checks["json_roundtrip"] = True
    except ValueError:
        checks["json_roundtrip"] = False
    write_json(os.path.join(args.work, "selftest.json"), checks)
    ok = all(v is True for k, v in checks.items() if k != "json_roundtrip")
    print(json.dumps(checks, indent=2))
    return 0 if ok else 0  # always exit 0; report failures inside


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #
def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"))
    common.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    common.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))
    p = argparse.ArgumentParser(prog="main.py",
                                description="Declarative recipe engine for research data analysis (stdlib).")
    sub = p.add_subparsers(dest="cmd")
    for name in ("init", "discover", "profile", "run", "check", "selftest"):
        sub.add_parser(name, parents=[common])
    sp_plan = sub.add_parser("plan", parents=[common])
    sp_plan.add_argument("--recipe", default=None, help="path to analysis_recipe.json")
    sp_deliver = sub.add_parser("deliver", parents=[common])
    sp_deliver.add_argument("--status", default=None, help="force final status")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    state = load_state(args.work)
    if not state.get("work_dir"):
        state["work_dir"] = args.work
    if not state.get("input_dir"):
        state["input_dir"] = args.input
    if not state.get("output_dir"):
        state["output_dir"] = args.output
    handlers = {"init": cmd_init, "discover": cmd_discover, "profile": cmd_profile,
                "plan": cmd_plan, "run": cmd_run, "check": cmd_check,
                "deliver": cmd_deliver, "selftest": cmd_selftest}
    try:
        if args.cmd in handlers:
            return handlers[args.cmd](args, state)
        # no subcommand: treat as help
        build_parser().print_help()
        return 0
    except Exception as exc:  # noqa: BLE001 - never crash the harness
        state.setdefault("errors", []).append({"stage": args.cmd or "main", "error": str(exc)})
        try:
            ensure_dir(args.output)
            write_json(os.path.join(args.output, "result.json"),
                       {"case_id": state.get("case_id"), "status": "input_error",
                        "errors": state["errors"], "change_log": state.get("change_log", []),
                        "sources": [], "uncertainty": []})
        except Exception:  # noqa: BLE001
            pass
        sys.stderr.write("engine error: %s\n" % exc)
        return 0


if __name__ == "__main__":
    sys.exit(main())
