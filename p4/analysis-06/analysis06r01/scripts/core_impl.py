#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Core implementation for the scientific DB query & analysis skill.

Pure Python standard library only. This module is intentionally free of any
prepared answers: it discovers the input snapshot, opens it read-only, profiles
every table/column, detects missing values, duplicates and outliers, computes
descriptive statistics and Pearson correlations, infers candidate join paths,
renders editable SVG charts, and records a granular evidence map in which every
claim carries a structured locator plus a verbatim quote of the underlying
material.

Nothing here writes to the source data. SQLite connections are opened with
``mode=ro`` (read-only URI) and only SELECT/WITH/PRAGMA/EXPLAIN statements are
ever executed.
"""

import csv as _csv
import html as _html
import json
import math
import os
import re
import sqlite3
import time
from pathlib import Path

VERSION = "1.0.0"
MAX_TEXT_BYTES = 2 * 1024 * 1024          # 2 MB per text file
MAX_QUERY_ROWS = 200                       # rows kept per query result
MAX_PROFILE_VALUES = 5                     # verbatim values kept per claim
STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

# --------------------------------------------------------------------------- #
# deterministic helpers
# --------------------------------------------------------------------------- #
def _iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _floatable(value):
    """Return float(value) or None without raising on edge input."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    if text == "":
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _looks_numeric(text):
    if text is None:
        return False
    s = str(text).strip()
    if s == "":
        return False
    return re.fullmatch(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?", s) is not None


def decode_text(raw):
    """Decode bytes tolerantly; returns (text, encoding, error_message)."""
    if raw is None:
        return "", None, "no bytes"
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", "replace"), "utf-8-sig", None
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), enc, None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace"), "utf-8-replace", "undecodable bytes replaced"


def read_text_file(path, max_bytes=MAX_TEXT_BYTES):
    """Read a text file with size guard. Returns dict with text or error."""
    p = Path(path)
    result = {"path": str(p), "name": p.name, "size": 0, "text": "",
              "encoding": None, "error": None, "skipped": False}
    try:
        if not p.exists():
            result["error"] = "file does not exist"
            return result
        size = p.stat().st_size
        result["size"] = size
        if size > max_bytes:
            result["skipped"] = True
            result["error"] = "size %d exceeds %d byte limit" % (size, max_bytes)
            return result
        raw = p.read_bytes()
        text, enc, err = decode_text(raw)
        result["text"] = text
        result["encoding"] = enc
        result["error"] = err
    except OSError as exc:  # pragma: no cover - platform specific
        result["error"] = str(exc)
    return result


def load_json_tolerant(text):
    """Parse JSON text; returns (object, error). Never raises."""
    if text is None:
        return None, "no text"
    try:
        return json.loads(text), None
    except (ValueError, TypeError) as exc:
        # strip BOM / leading junk then retry once
        stripped = text.lstrip("\ufeff \t\r\n")
        if stripped != text:
            try:
                return json.loads(stripped), None
            except (ValueError, TypeError):
                pass
        return None, "bad JSON: %s" % exc


def contract_keys(contract):
    """Return a deterministic list of contract field names to echo back.

    Supports dict (its keys), list of strings, list of dicts (union of keys).
    """
    keys = []
    if isinstance(contract, dict):
        keys = [str(k) for k in contract.keys()]
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                for k in item.keys():
                    keys.append(str(k))
    seen = []
    for k in keys:
        if k not in seen:
            seen.append(k)
    return seen


# --------------------------------------------------------------------------- #
# evidence map builders (variant focus: granular locator + verbatim quote)
# --------------------------------------------------------------------------- #
def make_locator(kind, **fields):
    """Build a structured, machine-checkable locator object."""
    loc = {"kind": kind}
    loc.update({k: v for k, v in fields.items() if v is not None})
    return loc


def make_claim(claim_id, claim, category, locator, quote, method,
               confidence="high", checks=None):
    """A single evidence record. ``quote`` must be a verbatim fragment."""
    rec = {
        "claim_id": claim_id,
        "claim": claim,
        "category": category,
        "locator": locator,
        "quote": quote,
        "method": method,
        "confidence": confidence,
    }
    if checks:
        rec["checks"] = checks
    return rec


class EvidenceMap:
    """Granular, categorised evidence collection with stable claim ids."""

    def __init__(self, case_id):
        self.case_id = case_id
        self._claims = []
        self._counters = {}

    def add(self, claim, category, locator, quote, method, confidence="high",
            checks=None):
        n = self._counters.get(category, 0) + 1
        self._counters[category] = n
        cid = "%s_%03d" % (category, n)
        self._claims.append(make_claim(cid, claim, category, locator, quote,
                                       method, confidence, checks))
        return cid

    def as_dict(self):
        grouped = {}
        for c in self._claims:
            grouped.setdefault(c["category"], []).append(c)
        # deterministic claim order within each category (insertion order is
        # already deterministic because the analysis is deterministic)
        return {
            "meta": {
                "case_id": self.case_id,
                "generated_at": _iso_now(),
                "claim_count": len(self._claims),
                "categories": sorted(grouped.keys()),
            },
            "claims": grouped,
        }


# --------------------------------------------------------------------------- #
# SQLite read-only access
# --------------------------------------------------------------------------- #
def open_readonly_sqlite(path):
    """Open a SQLite file read-only via URI; raises sqlite3.Error on failure."""
    p = str(Path(path).resolve())
    uri = "file:%s?mode=ro" % _uri_quote(p)
    conn = sqlite3.connect(uri, uri=True)
    conn.text_factory = str
    conn.execute("PRAGMA query_only = ON")
    return conn


def _uri_quote(path):
    return path.replace("\\", "/").replace("%", "%25").replace("?", "%3F")


def _ident(name):
    """Quote a SQL identifier defensively."""
    return '"' + str(name).replace('"', '""') + '"'


def _sqlite_path_label(path):
    return str(Path(path).name)


def inspect_schema(conn, db_label):
    """Return list of {table, row_count, columns:[{name,type,notnull,pk}]}."""
    tables = []
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    except sqlite3.Error as exc:
        return {"error": str(exc), "tables": []}
    for (name,) in rows:
        cols = []
        try:
            info = conn.execute("PRAGMA table_info(%s)" % _ident(name)).fetchall()
            for cid, cname, ctype, notnull, default, pk in info:
                cols.append({"name": cname, "type": ctype, "notnull": int(notnull),
                             "pk": int(pk)})
            n = conn.execute("SELECT COUNT(*) FROM %s" % _ident(name)).fetchone()[0]
        except sqlite3.Error as exc:
            tables.append({"table": name, "error": str(exc), "row_count": None,
                           "columns": []})
            continue
        tables.append({"table": name, "row_count": int(n), "columns": cols})
    return {"db_label": db_label, "tables": tables}


def read_table_rows(conn, table, columns, limit=200):
    """Read up to ``limit`` rows as dicts for cross-checking and quotes."""
    cols = ", ".join(_ident(c) for c in columns)
    try:
        cur = conn.execute("SELECT %s FROM %s LIMIT %d" % (cols, _ident(table), limit))
        names = [d[0] for d in cur.description]
        rows = [dict(zip(names, r)) for r in cur.fetchall()]
        return rows, None
    except sqlite3.Error as exc:
        return [], str(exc)


def run_select_query(conn, sql, max_rows=MAX_QUERY_ROWS):
    """Execute one read-only query. Returns {columns, rows, row_count, error}."""
    stripped = sql.strip().rstrip(";")
    if not re.match(r"(?is)^\s*(SELECT|WITH|PRAGMA|EXPLAIN)\b", stripped):
        return {"error": "only SELECT/WITH/PRAGMA/EXPLAIN allowed",
                "columns": [], "rows": [], "row_count": None}
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        if truncated:
            rows = rows[:max_rows]
        row_count = len(rows)
        if not truncated:
            # exact row count when within budget
            row_count = len(rows)
        return {"columns": cols, "rows": rows, "row_count": row_count,
                "truncated": truncated, "error": None}
    except sqlite3.Error as exc:
        return {"columns": [], "rows": [], "row_count": None, "truncated": False,
                "error": str(exc)}


# --------------------------------------------------------------------------- #
# column profiling
# --------------------------------------------------------------------------- #
def _raw_value_list(rows, col):
    return [r.get(col) for r in rows]


def column_profile(conn, table, col_name, col_type):
    """Profile one column. Returns a rich dict with verbatim quote fragments."""
    table_q = _ident(table)
    col_q = _ident(col_name)
    extraction_sql = "SELECT %s FROM %s" % (col_q, table_q)
    missing_sql = ("SELECT COUNT(*) FROM %s WHERE %s IS NULL OR "
                   "TRIM(CAST(%s AS TEXT)) = ''" % (table_q, col_q, col_q))
    value_counts_sql = ("SELECT CAST(%s AS TEXT) AS v, COUNT(*) AS n FROM %s "
                        "GROUP BY CAST(%s AS TEXT) ORDER BY n DESC, v ASC LIMIT 10"
                        % (col_q, table_q, col_q))
    prof = {"table": table, "column": col_name, "declared_type": col_type,
            "extraction_sql": extraction_sql, "missing_sql": missing_sql,
            "value_counts_sql": value_counts_sql, "error": None}
    try:
        rows = conn.execute("SELECT %s FROM %s" % (col_q, table_q)).fetchall()
        values = [r[0] for r in rows]
    except sqlite3.Error as exc:
        prof["error"] = str(exc)
        return prof
    n_total = len(values)
    non_null = [v for v in values if v is not None and str(v).strip() != ""]
    n_nonnull = len(non_null)
    n_missing = n_total - n_nonnull
    prof["n_total"] = n_total
    prof["n_nonnull"] = n_nonnull
    prof["n_missing"] = n_missing

    # value counts (top 10) - deterministic order by count desc, value asc
    counts = {}
    for v in non_null:
        key = str(v)
        counts[key] = counts.get(key, 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    prof["n_distinct"] = len(counts)
    prof["top_values"] = [{"value": v, "count": c} for v, c in top]
    prof["top_quote"] = "top_values=%s" % json.dumps(
        prof["top_values"], ensure_ascii=False)

    # numeric determination
    declared_num = re.search(r"(INT|REAL|FLOAT|DOUBLE|DECIMAL|NUM)", col_type or "",
                             re.I) is not None
    all_num = bool(non_null) and all(_looks_numeric(v) for v in non_null)
    coded = bool(non_null) and all_num and not declared_num
    prof["coded_as_text"] = coded
    prof["kind"] = "numeric" if (declared_num or all_num) and non_null else "other"

    if prof["kind"] == "numeric" and non_null:
        floats = []
        for v in non_null:
            f = _floatable(v)
            if f is not None:
                floats.append(f)
        floats.sort()
        n = len(floats)
        mean = sum(floats) / n
        variance = sum((x - mean) ** 2 for x in floats) / (n - 1) if n > 1 else 0.0
        stdev = math.sqrt(variance) if n > 1 else 0.0
        median = floats[n // 2] if n % 2 else (floats[n // 2 - 1] + floats[n // 2]) / 2.0
        q1 = _quantile(floats, 0.25)
        q3 = _quantile(floats, 0.75)
        iqr = q3 - q1
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        outliers = [x for x in floats if x < lo or x > hi]
        stats = {"n": n, "mean": _round6(mean), "stdev_sample": _round6(stdev),
                 "min": _round6(floats[0]), "max": _round6(floats[-1]),
                 "median": _round6(median), "q1": _round6(q1), "q3": _round6(q3),
                 "iqr": _round6(iqr), "n_outliers_iqr": len(outliers)}
        prof["numeric"] = {"stats": stats,
                           "first_values": floats[:MAX_PROFILE_VALUES]}
        prof["values_quote"] = "first_values=%s" % json.dumps(
            floats[:MAX_PROFILE_VALUES])
        prof["stats_quote"] = ("n=%d; mean=%s; stdev_sample=%s; min=%s; max=%s"
                               % (n, stats["mean"], stats["stdev_sample"],
                                  stats["min"], stats["max"]))
    else:
        prof["numeric"] = None
        prof["values_quote"] = "first_values=%s" % json.dumps(
            [str(v) for v in non_null[:MAX_PROFILE_VALUES]], ensure_ascii=False)
    return prof


def _round6(x):
    if x is None:
        return None
    r = round(float(x), 6)
    return int(r) if r == int(r) else r


def _quantile(sorted_values, q):
    if not sorted_values:
        return None
    pos = (len(sorted_values) - 1) * q
    base = int(math.floor(pos))
    rest = pos - base
    if base + 1 < len(sorted_values):
        return sorted_values[base] + rest * (sorted_values[base + 1] - sorted_values[base])
    return sorted_values[base]


def detect_duplicates(conn, table, columns):
    """Detect exact full-row duplicate groups via GROUP BY all columns."""
    if not columns:
        return {"n_duplicate_groups": 0, "n_rows_in_duplicate_groups": 0,
                "full_row_dup_sql": None, "groups_quote": "[]"}
    all_cols = ", ".join(_ident(c) for c in columns)
    sql = ("SELECT %s, COUNT(*) AS n FROM %s GROUP BY %s HAVING COUNT(*) > 1 "
           "ORDER BY n DESC LIMIT 10" % (all_cols, _ident(table), all_cols))
    try:
        rows = conn.execute(sql).fetchall()
    except sqlite3.Error as exc:
        return {"n_duplicate_groups": None, "n_rows_in_duplicate_groups": None,
                "full_row_dup_sql": sql, "groups_quote": "[]", "error": str(exc)}
    groups = [{"key": dict(zip(columns, r[:-1])), "count": r[-1]} for r in rows]
    n_groups = len(groups)
    n_rows = sum(g["count"] for g in groups)
    return {"n_duplicate_groups": n_groups, "n_rows_in_duplicate_groups": n_rows,
            "full_row_dup_sql": sql,
            "groups_quote": "duplicate_groups(top10)=%s" % json.dumps(
                groups, ensure_ascii=False)}


def correlate_columns(columns_numeric):
    """Pearson correlation for a list of {name, values}; pairwise complete."""
    names = [c["name"] for c in columns_numeric]
    vals = [c["values"] for c in columns_numeric]
    n = len(names)
    matrix = [[None] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                matrix[i][j] = 1.0
            elif j > i:
                pairs = [(a, b) for a, b in zip(vals[i], vals[j])
                         if a is not None and b is not None]
                matrix[i][j] = _pearson(pairs)
                matrix[j][i] = matrix[i][j]
    return {"columns": names, "matrix": matrix}


def _pearson(pairs):
    if len(pairs) < 2:
        return None
    xs = [a for a, _ in pairs]
    ys = [b for _, b in pairs]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    return _round6(sxy / math.sqrt(sxx * syy))


def infer_join_paths(conn, tables):
    """Find same-named columns across tables and infer cardinality."""
    paths = []
    for a in range(len(tables)):
        for b in range(a + 1, len(tables)):
            ta, tb = tables[a], tables[b]
            cols_a = {c["name"] for c in ta["columns"]}
            cols_b = {c["name"] for c in tb["columns"]}
            common = sorted(cols_a & cols_b)
            for cname in common:
                try:
                    da = conn.execute(
                        "SELECT COUNT(DISTINCT %s) FROM %s" % (_ident(cname), _ident(ta["table"]))).fetchone()[0]
                    db = conn.execute(
                        "SELECT COUNT(DISTINCT %s) FROM %s" % (_ident(cname), _ident(tb["table"]))).fetchone()[0]
                    na = ta["row_count"] or 0
                    nb = tb["row_count"] or 0
                except sqlite3.Error:
                    continue
                if da == na and db == nb:
                    cardinality = "one-to-one"
                elif da == na:
                    cardinality = "one-to-many"
                elif db == nb:
                    cardinality = "many-to-one"
                else:
                    cardinality = "many-to-many"
                paths.append({
                    "left_table": ta["table"], "right_table": tb["table"],
                    "join_column": cname,
                    "left_distinct": da, "left_rows": na,
                    "right_distinct": db, "right_rows": nb,
                    "cardinality": cardinality,
                    "join_sql": ("SELECT ... FROM %s LEFT JOIN %s USING(%s)"
                                 % (_ident(ta["table"]), _ident(tb["table"]),
                                    _ident(cname))),
                })
    return paths


# --------------------------------------------------------------------------- #
# SVG chart generation (pure standard library, editable & parseable)
# --------------------------------------------------------------------------- #
def _esc(text):
    return _html.escape(str(text), quote=True)


def _svg_doc(width, height, body):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
            'viewBox="0 0 %d %d" font-family="sans-serif">\n%s</svg>\n'
            % (width, height, width, height, body))


def svg_histogram(values, title):
    if not values:
        return None
    lo, hi = min(values), max(values)
    if hi == lo:
        hi = lo + 1.0
    n_bins = 10
    width = 1.0 * (hi - lo) / n_bins
    counts = [0] * n_bins
    for v in values:
        idx = int((v - lo) / width)
        if idx >= n_bins:
            idx = n_bins - 1
        counts[idx] += 1
    W, H = 640, 360
    margin = {"l": 56, "r": 20, "t": 46, "b": 48}
    plot_w = W - margin["l"] - margin["r"]
    plot_h = H - margin["t"] - margin["b"]
    maxc = max(counts) or 1
    bar_w = plot_w / n_bins
    parts = ['<rect width="%d" height="%d" fill="white"/>' % (W, H),
             '<text x="%d" y="28" font-size="16" text-anchor="middle" '
             'font-weight="bold">%s</text>' % (W / 2, _esc(title))]
    for i, c in enumerate(counts):
        h = plot_h * c / maxc
        x = margin["l"] + i * bar_w
        y = margin["t"] + plot_h - h
        parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                     'fill="#4C78A8" stroke="#2F4B6E"/>'
                     % (x, y, max(bar_w - 1, 1), max(h, 0)))
    parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#333"/>'
                 % (margin["l"], margin["t"] + plot_h, margin["l"] + plot_w,
                    margin["t"] + plot_h))
    parts.append('<text x="%d" y="%d" font-size="12" text-anchor="middle">%s</text>'
                 % (margin["l"] + plot_w / 2, H - 10, _esc("%.4g..%.4g" % (lo, hi))))
    return _svg_doc(W, H, "\n".join(parts))


def svg_value_counts(top_values, title):
    if not top_values:
        return None
    items = top_values[:10]
    W, H = 640, 60 + 26 * len(items)
    margin = {"l": 240, "r": 60, "t": 46, "b": 30}
    plot_w = W - margin["l"] - margin["r"]
    maxc = max(v["count"] for v in items) or 1
    parts = ['<rect width="%d" height="%d" fill="white"/>' % (W, H),
             '<text x="%d" y="28" font-size="16" text-anchor="middle" '
             'font-weight="bold">%s</text>' % (W / 2, _esc(title))]
    for i, v in enumerate(items):
        y = margin["t"] + i * 26
        bw = plot_w * v["count"] / maxc
        label = str(v["value"])
        if len(label) > 32:
            label = label[:32] + "..."
        parts.append('<text x="%d" y="%d" font-size="11" text-anchor="end">%s</text>'
                     % (margin["l"] - 8, y + 14, _esc(label)))
        parts.append('<rect x="%d" y="%d" width="%.1f" height="18" fill="#4C78A8"/>'
                     % (margin["l"], y, bw))
        parts.append('<text x="%.1f" y="%d" font-size="11">%d</text>'
                     % (margin["l"] + bw + 4, y + 14, v["count"]))
    return _svg_doc(W, H, "\n".join(parts))


def svg_scatter(x_values, y_values, x_name, y_name, title):
    if not x_values or len(x_values) != len(y_values):
        return None
    pairs = [(x, y) for x, y in zip(x_values, y_values)
             if x is not None and y is not None]
    if len(pairs) < 2:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    if xmax == xmin:
        xmax = xmin + 1.0
    if ymax == ymin:
        ymax = ymin + 1.0
    W, H = 640, 420
    margin = {"l": 70, "r": 24, "t": 46, "b": 54}
    pw = W - margin["l"] - margin["r"]
    ph = H - margin["t"] - margin["b"]
    parts = ['<rect width="%d" height="%d" fill="white"/>' % (W, H),
             '<text x="%d" y="28" font-size="16" text-anchor="middle" '
             'font-weight="bold">%s</text>' % (W / 2, _esc(title)),
             '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#333"/>'
             % (margin["l"], margin["t"], margin["l"], margin["t"] + ph),
             '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#333"/>'
             % (margin["l"], margin["t"] + ph, margin["l"] + pw, margin["t"] + ph)]
    for x, y in pairs:
        px = margin["l"] + (x - xmin) / (xmax - xmin) * pw
        py = margin["t"] + ph - (y - ymin) / (ymax - ymin) * ph
        parts.append('<circle cx="%.1f" cy="%.1f" r="2.6" fill="#E15759" '
                     'opacity="0.7"/>' % (px, py))
    parts.append('<text x="%d" y="%d" font-size="12" text-anchor="middle">%s</text>'
                 % (margin["l"] + pw / 2, H - 8, _esc(x_name)))
    parts.append('<text x="%d" y="%d" font-size="12" text-anchor="middle" '
                 'transform="rotate(-90 %d %d)">%s</text>'
                 % (18, margin["t"] + ph / 2, 18, margin["t"] + ph / 2, _esc(y_name)))
    return _svg_doc(W, H, "\n".join(parts))


def svg_correlation_heatmap(matrix, labels, title):
    n = len(labels)
    if n == 0:
        return None
    cell = 46
    label_w = 170
    W = label_w + n * cell + 20
    H = 120 + n * cell
    parts = ['<rect width="%d" height="%d" fill="white"/>' % (W, H),
             '<text x="%d" y="30" font-size="16" text-anchor="middle" '
             'font-weight="bold">%s</text>' % (W / 2, _esc(title))]
    for i in range(n):
        y = 70 + i * cell
        lbl = labels[i] if len(str(labels[i])) <= 26 else str(labels[i])[:26] + "..."
        parts.append('<text x="%d" y="%d" font-size="10" text-anchor="end">%s</text>'
                     % (label_w - 8, y + cell / 2 + 3, _esc(lbl)))
        for j in range(n):
            x = label_w + j * cell
            val = matrix[i][j]
            if val is None:
                fill = "#DDDDDD"
                txt = "NA"
            else:
                fill = _corr_color(val)
                txt = "%.2f" % val
            parts.append('<rect x="%d" y="%d" width="%d" height="%d" fill="%s" '
                         'stroke="#999"/>' % (x, y, cell - 1, cell - 1, fill))
            parts.append('<text x="%d" y="%d" font-size="9" text-anchor="middle" '
                         'fill="#111">%s</text>' % (x + cell / 2, y + cell / 2 + 3,
                                                    txt))
    return _svg_doc(W, H, "\n".join(parts))


def _corr_color(v):
    # blue for negative, white near zero, red for positive
    v = max(-1.0, min(1.0, float(v)))
    if v >= 0:
        r = 255
        g = int(255 - 255 * v)
        b = int(255 - 255 * v)
    else:
        r = int(255 + 255 * v)
        g = int(255 + 255 * v)
        b = 255
    return "#%02X%02X%02X" % (r, g, b)


# --------------------------------------------------------------------------- #
# CSV/TSV loading into a temporary read-only sqlite table
# --------------------------------------------------------------------------- #
def load_csv_to_sqlite(path):
    """Import a CSV/TSV into an in-memory SQLite table (read-only after import).

    Returns (conn, db_label, table_name, columns, row_count, error).
    """
    import io
    try:
        text = read_text_file(path)
        if text["error"]:
            return None, None, None, [], 0, text["error"]
        if not text["text"].strip():
            return None, None, None, [], 0, "empty CSV/TSV"
        delim = "\t" if path.lower().endswith((".tsv", ".tab")) else None
        sample = text["text"][:4096]
        if delim is None:
            try:
                delim = _csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
            except _csv.Error:
                delim = ","
        reader = _csv.reader(io.StringIO(text["text"]), delimiter=delim)
        rows = list(reader)
        if not rows:
            return None, None, None, [], 0, "no rows parsed"
        header = rows[0]
        table_name = "csv_" + re.sub(r"[^A-Za-z0-9_]", "_", Path(path).stem)[:40]
        cols = [_sanitize_col(h, i) for i, h in enumerate(header)]
        conn = sqlite3.connect(":memory:")
        conn.text_factory = str
        create = ("CREATE TABLE %s (%s)" %
                  (_ident(table_name), ", ".join("%s TEXT" % _ident(c) for c in cols)))
        conn.execute(create)
        n = 0
        for r in rows[1:]:
            if not r:
                continue
            vals = list(r) + [None] * (len(cols) - len(r))
            placeholders = ", ".join("?" * len(cols))
            conn.execute("INSERT INTO %s VALUES (%s)" % (_ident(table_name),
                                                         placeholders),
                         [v if str(v).strip() != "" else None for v in vals[:len(cols)]])
            n += 1
        conn.commit()
        conn.execute("PRAGMA query_only = ON")
        return conn, _sqlite_path_label(path), table_name, cols, n, None
    except Exception as exc:  # noqa: BLE001 - deliver safe degradation
        return None, None, None, [], 0, str(exc)


def _sanitize_col(name, idx):
    if name is None or str(name).strip() == "":
        return "column_%d" % idx
    s = re.sub(r"\s+", "_", str(name).strip())
    return s if s else "column_%d" % idx


# --------------------------------------------------------------------------- #
# main analysis pipeline (called by main.py)
# --------------------------------------------------------------------------- #
def discover_input(input_dir):
    """Scan the input directory. Returns a dict of discovered material."""
    base = Path(input_dir)
    found = {"case_json": None, "task_files": [], "dbs": [], "csvs": [],
             "dicts": [], "other_json": [], "all_files": []}
    if not base.exists():
        return found
    for p in sorted(base.rglob("*")):
        if p.is_dir():
            continue
        found["all_files"].append(str(p))
        low = p.name.lower()
        if p.name == "case.json":
            found["case_json"] = str(p)
        elif low.endswith((".db", ".sqlite", ".sqlite3", ".db3")):
            found["dbs"].append(str(p))
        elif low.endswith((".csv", ".tsv")):
            found["csvs"].append(str(p))
        elif low.endswith((".md", ".yaml", ".yml", ".txt")):
            if p.name.lower() == "task.md":
                found["task_files"].insert(0, str(p))
            else:
                found["dicts"].append(str(p))
        elif low.endswith(".json"):
            if p.name == "case.json":
                found["case_json"] = str(p)
            else:
                found["other_json"].append(str(p))
    return found


def run_pipeline(input_dir, output_dir, work_dir, queries_path=None):
    """Full deterministic pipeline. Returns the result dict (never raises)."""
    t0 = time.time()
    errors, degradations = [], []
    out = Path(output_dir)
    work = Path(work_dir)
    os.makedirs(out, exist_ok=True)
    os.makedirs(work, exist_ok=True)

    # ---- phase 0: load case contract ----
    found = discover_input(input_dir)
    case = {"case_id": None, "output_contract": {}}
    case_path = found["case_json"]
    if case_path is None:
        # fallback: any JSON
        if found["other_json"]:
            case_path = found["other_json"][0]
            degradations.append("case.json missing; fell back to %s" % case_path)
        else:
            case_path = None
    if case_path is not None:
        raw = read_text_file(case_path)
        if raw["error"]:
            errors.append("case read failed: %s" % raw["error"])
        else:
            obj, err = load_json_tolerant(raw["text"])
            if err:
                errors.append("case.json parse: %s" % err)
            elif isinstance(obj, dict):
                case["case_id"] = obj.get("case_id") or obj.get("id") or obj.get(
                    "caseId") or Path(case_path).stem
                case["output_contract"] = obj.get("output_contract") or {}
                # keep the whole case for reference
                case["_raw"] = obj
    if case["case_id"] is None:
        case["case_id"] = "unknown"

    skeleton = _build_skeleton(case)
    # immediately persist a legal partial skeleton (phase-0 guarantee)
    skeleton_path = out / "result.json"
    _write_json(skeleton_path, skeleton)

    # ---- discover data ----
    dbs = list(found["dbs"])
    csvs = list(found["csvs"])
    if not dbs and not csvs:
        skeleton["status"] = "insufficient_evidence"
        skeleton["pending_human"].append(
            "no SQLite (*.db/*.sqlite) or CSV/TSV snapshot found in /input")
        skeleton["limitations"].append("no queryable data source present")
        _finalize(skeleton, errors, degradations, t0)
        _write_json(skeleton_path, skeleton)
        return skeleton

    evidence = EvidenceMap(case["case_id"])
    analysis = {"schema": [], "profiles": [], "cleaning": [],
                "statistics": [], "correlations": [], "charts": [],
                "cross_table": []}
    query_log = []
    answers = {}
    artifacts = {}
    sources = []
    uncertainty = []

    # record the input files as evidence
    for f in found["all_files"]:
        evidence.add("input file discovered: %s" % Path(f).name, "input",
                     make_locator("file", path=f),
                     "path=%s" % f, "filesystem scan of /input",
                     confidence="high")

    # ---- open each database read-only ----
    conns = []  # (conn, label, kind)
    for db in dbs:
        try:
            conn = open_readonly_sqlite(db)
            label = _sqlite_path_label(db)
            conns.append((conn, label, "sqlite"))
            sources.append({"locator": "sqlite://%s (mode=ro)" % db,
                            "quote": "opened read-only SQLite snapshot",
                            "role": "data-source"})
        except sqlite3.Error as exc:
            errors.append("cannot open %s: %s" % (db, exc))
    for csv_path in csvs:
        conn, label, table, cols, n, err = load_csv_to_sqlite(csv_path)
        if err:
            errors.append("cannot import %s: %s" % (csv_path, err))
            continue
        conns.append((conn, label, "csv"))
        sources.append({"locator": "csv://%s -> temp sqlite table %s"
                        % (csv_path, table),
                        "quote": "imported %d rows" % n, "role": "data-source"})

    if not conns:
        skeleton["status"] = "input_error"
        skeleton["errors"] = errors
        skeleton["limitations"].append("no data source could be opened")
        _finalize(skeleton, errors, degradations, t0)
        _write_json(skeleton_path, skeleton)
        return skeleton

    # ---- schema inspection ----
    schema_json = {"databases": []}
    for conn, label, kind in conns:
        info = inspect_schema(conn, label)
        if isinstance(info, dict) and "error" in info and "tables" not in info:
            errors.append("schema inspection failed for %s: %s" % (label, info["error"]))
            continue
        schema_json["databases"].append(info)
        for t in info["tables"]:
            if t.get("error"):
                errors.append("table %s.%s: %s" % (label, t["table"], t["error"]))
                continue
            analysis["schema"].append({"db": label, "table": t["table"],
                                       "row_count": t["row_count"],
                                       "columns": [c["name"] for c in t["columns"]]})
            cols = [c["name"] for c in t["columns"]]
            evidence.add(
                "table %s.%s has %d columns and %d rows"
                % (label, t["table"], len(t["columns"]), t["row_count"]),
                "schema",
                make_locator("sqlite", path=label, table=t["table"],
                             sql="SELECT COUNT(*) FROM %s" % _ident(t["table"])),
                "columns=%s; row_count=%d" % (json.dumps(cols), t["row_count"]),
                "PRAGMA table_info + COUNT(*) on read-only connection",
                confidence="high",
                checks={"recomputed": True, "row_count": t["row_count"]})

    # ---- column profiling & cleaning ----
    charts_list = []
    figures_dir = out / "figures"
    os.makedirs(figures_dir, exist_ok=True)
    profile_records = []
    numeric_cols_by_table = {}
    for conn, label, kind in conns:
        info = next((d for d in schema_json["databases"] if d["db_label"] == label),
                    None)
        if not info:
            continue
        for t in info["tables"]:
            if t.get("error"):
                continue
            table = t["table"]
            tcols = [c["name"] for c in t["columns"]]
            dup = detect_duplicates(conn, table, tcols)
            if dup.get("full_row_dup_sql"):
                qid = "%s:%s:duplicates" % (label, table)
                query_log.append({"id": qid, "sql": dup["full_row_dup_sql"],
                                  "purpose": "exact full-row duplicate detection",
                                  "row_count": dup["n_duplicate_groups"],
                                  "status": "ok",
                                  "summary": dup["groups_quote"]})
                evidence.add(
                    "table %s.%s has %s exact duplicate groups involving %s rows"
                    % (label, table, dup["n_duplicate_groups"],
                       dup["n_rows_in_duplicate_groups"]),
                    "data_quality",
                    make_locator("sql", sql=dup["full_row_dup_sql"], table=table,
                                 path=label),
                    dup["groups_quote"], "GROUP BY all columns HAVING COUNT(*)>1",
                    confidence="high")
            numeric_cols = []
            for col in t["columns"]:
                prof = column_profile(conn, table, col["name"], col["type"])
                if prof.get("error"):
                    errors.append("profile %s.%s.%s: %s" % (label, table,
                                                            col["name"],
                                                            prof["error"]))
                    continue
                profile_records.append(prof)
                analysis["profiles"].append(_profile_summary(prof))
                # data quality: missing + coded-as-text + outliers
                if prof["n_missing"]:
                    evidence.add(
                        "column %s.%s.%s has %d missing/blank values of %d"
                        % (label, table, col["name"], prof["n_missing"],
                           prof["n_total"]),
                        "data_quality",
                        make_locator("sql", sql=prof["missing_sql"], table=table,
                                     path=label, column=col["name"]),
                        "n_missing=%d; n_total=%d" % (prof["n_missing"],
                                                      prof["n_total"]),
                        "COUNT WHERE NULL OR TRIM(...)=''", confidence="high")
                if prof.get("coded_as_text"):
                    degradations.append(
                        "column %s.%s.%s declared TEXT but all values numeric; "
                        "converted in Python, not SQL AVG"
                        % (label, table, col["name"]))
                    evidence.add(
                        "column %s.%s.%s is coded_as_text (TEXT storage, numeric "
                        "values)" % (label, table, col["name"]),
                        "data_quality",
                        make_locator("sql", sql=prof["value_counts_sql"],
                                     table=table, path=label, column=col["name"]),
                        prof["top_quote"], "type sniff + regex value test",
                        confidence="high")
                if prof.get("kind") == "numeric" and prof.get("numeric"):
                    st = prof["numeric"]["stats"]
                    numeric_cols.append({"name": col["name"],
                                         "values": _full_numeric_values(
                                             conn, table, col["name"])})
                    evidence.add(
                        "column %s.%s.%s numeric summary" % (label, table,
                                                             col["name"]),
                        "statistics",
                        make_locator("sql", sql=prof["extraction_sql"],
                                     table=table, path=label, column=col["name"]),
                        prof["stats_quote"], "Python recomputation from extracted values",
                        confidence="high",
                        checks={"recomputed": True,
                                "n": st["n"], "mean": st["mean"]})
                    if st["n_outliers_iqr"]:
                        evidence.add(
                            "column %s.%s.%s has %d IQR-rule outliers" % (
                                label, table, col["name"], st["n_outliers_iqr"]),
                            "data_quality",
                            make_locator("sql", sql=prof["extraction_sql"],
                                         table=table, path=label,
                                         column=col["name"]),
                            "q1=%s; q3=%s; iqr=%s; n_outliers=%d"
                            % (st["q1"], st["q3"], st["iqr"], st["n_outliers_iqr"]),
                            "Tukey IQR rule [Q1-1.5*IQR, Q3+1.5*IQR]",
                            confidence="medium")
                # value counts evidence (top category fragment is verbatim)
                if prof["n_distinct"] <= 30:
                    evidence.add(
                        "column %s.%s.%s has %d distinct values" % (label, table,
                                                                    col["name"],
                                                                    prof["n_distinct"]),
                        "statistics",
                        make_locator("sql", sql=prof["value_counts_sql"],
                                     table=table, path=label, column=col["name"]),
                        prof["top_quote"], "GROUP BY CAST(col AS TEXT)",
                        confidence="high")
                query_log.append({"id": "%s:%s:%s:extract" % (label, table,
                                                              col["name"]),
                                  "sql": prof["extraction_sql"],
                                  "purpose": "column value extraction",
                                  "row_count": prof["n_total"], "status": "ok"})
            numeric_cols_by_table[(label, table)] = numeric_cols
            # correlation
            if len(numeric_cols) >= 2:
                corr = correlate_columns(numeric_cols)
                analysis["correlations"].append({"db": label, "table": table,
                                                 **corr})
                evidence.add(
                    "Pearson correlation matrix computed for table %s.%s (%d numeric "
                    "columns)" % (label, table, len(numeric_cols)),
                    "statistics",
                    make_locator("sqlite", path=label, table=table,
                                 sql="SELECT numeric columns then Python Pearson"),
                    "numeric_columns=%s" % json.dumps(corr["columns"]),
                    "pairwise complete Pearson, pure Python",
                    confidence="high")
                hm = svg_correlation_heatmap(corr["matrix"], corr["columns"],
                                             "correlation %s.%s" % (label, table))
                _store_chart(figures_dir, "corr_%s_%s.svg" % (label, table), hm,
                             artifacts, charts_list, "correlation_heatmap")
            # per-table histograms and value counts charts (top numeric + categorical)
            _add_table_charts(conn, table, label, numeric_cols, profile_records,
                              figures_dir, artifacts, charts_list)
            # join paths
            pass  # handled below across tables

    # cross-table join path inference per database
    for conn, label, kind in conns:
        info = next((d for d in schema_json["databases"] if d["db_label"] == label),
                    None)
        if not info:
            continue
        tables = [t for t in info["tables"] if not t.get("error")]
        paths = infer_join_paths(conn, tables)
        for p in paths:
            analysis["cross_table"].append(p)
            evidence.add(
                "candidate join %s.%s -[%s]-> %s.%s (%s)"
                % (label, p["left_table"], p["join_column"], label,
                   p["right_table"], p["cardinality"]),
                "cross_table",
                make_locator("sql", sql=p["join_sql"], path=label,
                             table=p["left_table"], column=p["join_column"]),
                "left_distinct=%d/%d; right_distinct=%d/%d"
                % (p["left_distinct"], p["left_rows"], p["right_distinct"],
                   p["right_rows"]),
                "COUNT(DISTINCT col) on both sides to infer cardinality",
                confidence="high" if p["cardinality"] in ("one-to-one",) else "medium")

    # ---- targeted queries from /app/work/queries.json ----
    queries = []
    qpath = Path(queries_path) if queries_path else work / "queries.json"
    if qpath.exists():
        qraw = read_text_file(str(qpath))
        if qraw["error"]:
            degradations.append("queries.json unreadable: %s" % qraw["error"])
        else:
            qobj, qerr = load_json_tolerant(qraw["text"])
            if qerr:
                degradations.append("queries.json: %s" % qerr)
            elif isinstance(qobj, list):
                queries = qobj
    for q in queries:
        if not isinstance(q, dict):
            continue
        qid = q.get("id", "q%d" % len(query_log))
        sql = q.get("sql", "")
        note = q.get("note", "")
        target = q.get("contract_field") or q.get("target")
        # pick a connection: first sqlite is preferred
        conn = conns[0][0] if conns else None
        if conn is None:
            continue
        res = run_select_query(conn, sql)
        if res["error"]:
            query_log.append({"id": qid, "sql": sql, "purpose": note,
                              "row_count": None, "status": "error",
                              "error": res["error"]})
            uncertainty.append({"item": qid, "level": "high",
                                "reason": "query failed: %s" % res["error"]})
            continue
        summary = _summarize_result(res)
        query_log.append({"id": qid, "sql": sql, "purpose": note,
                          "row_count": res["row_count"], "status": "ok",
                          "summary": summary})
        evidence.add("query %s returned %d rows: %s" % (qid, res["row_count"], note),
                     "answers",
                     make_locator("sql", sql=sql, path=conns[0][1]),
                     summary, "read-only SELECT executed by main.py",
                     confidence="high",
                     checks={"row_count": res["row_count"]})
        if target:
            answers[target] = {"value": _first_answer(res), "query_id": qid,
                               "sql": sql, "note": note,
                               "row_count": res["row_count"]}

    # ---- assemble result.json ----
    result = dict(skeleton)  # carries case_id + contract keys + status
    result["status"] = "partial"  # refined below
    result["sources"] = sources
    result["uncertainty"] = uncertainty
    result["change_log"] = [
        {"ts": _iso_now(), "step": "skeleton", "change": "initial legal skeleton",
         "status": "partial"}]
    result["analysis"] = analysis
    result["query_log"] = query_log
    result["answers"] = answers
    result["artifacts"] = artifacts
    result["errors"] = errors
    result["degradations"] = degradations
    result["limitations"] = []
    result["pending_human"] = skeleton.get("pending_human", [])
    result["runtime"] = {"seconds": _round6(time.time() - t0),
                         "version": VERSION}
    result["evidence_map"] = "evidence_map.json"
    result["evidence_claim_count"] = len(evidence._claims)

    # write auxiliary artifacts
    _write_json(out / "evidence_map.json", evidence.as_dict())
    _write_json(out / "schema.json", schema_json)
    _write_json(out / "profiles.json", profile_records)
    artifacts["evidence_map.json"] = "evidence"
    artifacts["schema.json"] = "schema"
    artifacts["profiles.json"] = "profiles"

    # CSV deliverables
    _write_summary_csv(out / "summary_stats.csv", profile_records)
    artifacts["summary_stats.csv"] = "statistics"
    _write_quality_csv(out / "data_quality.csv", profile_records)
    artifacts["data_quality.csv"] = "quality"
    _write_correlation_csv(out / "correlation_matrix.csv", analysis["correlations"])
    artifacts["correlation_matrix.csv"] = "correlation"
    _write_query_log_csv(out / "query_log.csv", query_log)
    artifacts["query_log.csv"] = "query-log"
    _write_value_counts_json(out / "value_counts.json", profile_records)
    artifacts["value_counts.json"] = "value-counts"

    # report.md
    report = _render_report(case, analysis, errors, degradations, query_log,
                            evidence)
    with open(out / "report.md", "w", encoding="utf-8") as f:
        f.write(report)
    artifacts["report.md"] = "report"
    artifacts["figures"] = "charts"

    result["artifacts"] = artifacts

    # status decision
    if errors:
        result["status"] = "partial"
        result["limitations"].append("%d error(s) recorded; see errors" % len(errors))
    else:
        result["status"] = "ok"
    if skeleton.get("status") == "insufficient_evidence":
        result["status"] = "insufficient_evidence"

    result["change_log"].append({
        "ts": _iso_now(), "step": "analysis",
        "change": "schema, profiling, statistics, charts and evidence written",
        "status": result["status"]})

    _finalize(result, errors, degradations, t0)
    _write_json(skeleton_path, result)
    # write a queries template for the agent to extend
    _write_json(work / "queries.json", queries or [])
    return result


def _full_numeric_values(conn, table, col):
    try:
        rows = conn.execute("SELECT %s FROM %s" % (_ident(col), _ident(table))).fetchall()
    except sqlite3.Error:
        return []
    out = []
    for (v,) in rows:
        f = _floatable(v)
        if f is not None:
            out.append(f)
    return out


def _profile_summary(prof):
    s = {"table": prof["table"], "column": prof["column"],
         "declared_type": prof["declared_type"], "kind": prof["kind"],
         "n_total": prof["n_total"], "n_nonnull": prof["n_nonnull"],
         "n_missing": prof["n_missing"], "n_distinct": prof["n_distinct"],
         "coded_as_text": prof["coded_as_text"],
         "top_values": prof["top_values"]}
    if prof.get("numeric"):
        s["stats"] = prof["numeric"]["stats"]
    return s


def _add_table_charts(conn, table, label, numeric_cols, profiles, figures_dir,
                      artifacts, charts_list):
    # histogram for each numeric column (up to 3)
    n_done = 0
    for col in numeric_cols[:3]:
        vals = _full_numeric_values(conn, table, col["name"])
        if len(vals) >= 3:
            svg = svg_histogram(vals, "histogram %s.%s.%s" % (label, table,
                                                              col["name"]))
            _store_chart(figures_dir, "hist_%s_%s_%s.svg" % (label, table,
                                                             col["name"]),
                         svg, artifacts, charts_list, "histogram")
            n_done += 1
    # value counts bar for lowest-cardinality categorical columns (up to 2)
    cat = [p for p in profiles if p["table"] == table and p["kind"] != "numeric"
           and p["n_distinct"] and p["n_distinct"] <= 20]
    cat_sorted = sorted(cat, key=lambda p: p["n_distinct"])[:2]
    for p in cat_sorted:
        svg = svg_value_counts(p["top_values"], "value_counts %s.%s.%s"
                               % (label, table, p["column"]))
        _store_chart(figures_dir, "bar_%s_%s_%s.svg" % (label, table, p["column"]),
                     svg, artifacts, charts_list, "value_counts")
    # scatter for first two numeric columns
    if len(numeric_cols) >= 2:
        x = _full_numeric_values(conn, table, numeric_cols[0]["name"])
        y = _full_numeric_values(conn, table, numeric_cols[1]["name"])
        svg = svg_scatter(x, y, numeric_cols[0]["name"], numeric_cols[1]["name"],
                          "scatter %s.%s" % (label, table))
        _store_chart(figures_dir, "scatter_%s_%s.svg" % (label, table), svg,
                     artifacts, charts_list, "scatter")


def _store_chart(figures_dir, fname, content, artifacts, chart_list, kind):
    if content is None:
        return
    path = os.path.join(figures_dir, fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    rel = os.path.relpath(path, os.path.dirname(figures_dir))
    chart_list.append({"file": rel, "kind": kind})
    artifacts[rel] = "chart"


# --------------------------------------------------------------------------- #
# result assembly helpers
# --------------------------------------------------------------------------- #
def _build_skeleton(case):
    skel = {"case_id": case["case_id"], "status": "partial"}
    for k in contract_keys(case["output_contract"]):
        skel[k] = None
    skel["sources"] = []
    skel["uncertainty"] = []
    skel["pending_human"] = []
    skel["change_log"] = [
        {"ts": _iso_now(), "step": "skeleton", "change": "initial legal skeleton",
         "status": "partial"}]
    skel["errors"] = []
    skel["degradations"] = []
    skel["limitations"] = []
    skel["analysis"] = {}
    skel["query_log"] = []
    skel["answers"] = {}
    skel["artifacts"] = {}
    return skel


def _finalize(result, errors, degradations, t0):
    result["runtime"] = {"seconds": _round6(time.time() - t0), "version": VERSION}
    if errors and result.get("status") not in ("input_error", "insufficient_evidence"):
        result["status"] = "partial"
    result["change_log"].append({
        "ts": _iso_now(), "step": "finalize", "change": "finalised result.json",
        "status": result["status"]})
    return result


def _write_json(path, obj):
    """Write JSON atomically-ish with deterministic key order and UTF-8."""
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, str(path))


def _first_answer(res):
    if res["rows"] and res["columns"]:
        return res["rows"][0][0]
    return None


def _summarize_result(res):
    cols = res["columns"]
    rows = res["rows"]
    head = [dict(zip(cols, r)) for r in rows[:5]] if cols else rows[:5]
    return "columns=%s; rows=%s; head=%s%s" % (
        json.dumps(cols), res["row_count"], json.dumps(head, ensure_ascii=False),
        " (truncated)" if res.get("truncated") else "")


def _write_summary_csv(path, profiles):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["table", "column", "declared_type", "kind", "n_total",
                    "n_nonnull", "n_missing", "n_distinct", "coded_as_text",
                    "mean", "stdev_sample", "min", "max", "median", "q1", "q3",
                    "n_outliers_iqr"])
        for p in profiles:
            st = (p.get("numeric") or {}).get("stats") or {}
            w.writerow([p["table"], p["column"], p["declared_type"], p["kind"],
                        p["n_total"], p["n_nonnull"], p["n_missing"],
                        p["n_distinct"], p["coded_as_text"],
                        st.get("mean"), st.get("stdev_sample"), st.get("min"),
                        st.get("max"), st.get("median"), st.get("q1"),
                        st.get("q3"), st.get("n_outliers_iqr")])


def _write_quality_csv(path, profiles):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["table", "column", "n_missing", "n_total", "coded_as_text",
                    "n_distinct", "n_outliers_iqr", "top_value", "top_count"])
        for p in profiles:
            st = (p.get("numeric") or {}).get("stats") or {}
            top = p["top_values"][0] if p["top_values"] else {"value": None,
                                                              "count": 0}
            w.writerow([p["table"], p["column"], p["n_missing"], p["n_total"],
                        p["coded_as_text"], p["n_distinct"],
                        st.get("n_outliers_iqr"), top["value"], top["count"]])


def _write_correlation_csv(path, correlations):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["db", "table", "column_a", "column_b", "pearson_r"])
        for corr in correlations:
            cols = corr["columns"]
            for i in range(len(cols)):
                for j in range(i + 1, len(cols)):
                    w.writerow([corr["db"], corr["table"], cols[i], cols[j],
                                corr["matrix"][i][j]])


def _write_query_log_csv(path, query_log):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["id", "purpose", "row_count", "status", "sql", "summary"])
        for q in query_log:
            w.writerow([q.get("id"), q.get("purpose"), q.get("row_count"),
                        q.get("status"), q.get("sql"), q.get("summary") or
                        q.get("error")])


def _write_value_counts_json(path, profiles):
    obj = {}
    for p in profiles:
        key = "%s.%s" % (p["table"], p["column"])
        obj[key] = {"n_distinct": p["n_distinct"], "top_values": p["top_values"]}
    _write_json(path, obj)


def _render_report(case, analysis, errors, degradations, query_log, evidence):
    lines = []
    lines.append("# 科研数据库查询与分析报告")
    lines.append("")
    lines.append("- case_id: `%s`" % case["case_id"])
    lines.append("- 状态: `%s`" % ("partial" if errors else "ok"))
    lines.append("- 证据条目: %d" % len(evidence._claims))
    lines.append("")
    lines.append("## 1. 数据源与 schema")
    for s in analysis["schema"]:
        lines.append("- `%s.%s`: %d 行, %d 列 (%s)"
                    % (s["db"], s["table"], s["row_count"], len(s["columns"]),
                       ", ".join(s["columns"][:12])))
    if not analysis["schema"]:
        lines.append("- （无）")
    lines.append("")
    lines.append("## 2. 数据质量")
    missing = [p for p in analysis["profiles"] if p.get("n_missing")]
    lines.append("- 含缺失值的列: %d" % len(missing))
    for p in missing[:20]:
        lines.append("  - `%s.%s`: %d/%d 缺失"
                     % (p["table"], p["column"], p["n_missing"], p["n_total"]))
    coded = [p for p in analysis["profiles"] if p.get("coded_as_text")]
    lines.append("- 编码为文本的数字列 (coded_as_text): %d" % len(coded))
    for p in coded:
        lines.append("  - `%s.%s`" % (p["table"], p["column"]))
    lines.append("")
    lines.append("## 3. 统计")
    for p in analysis["profiles"]:
        if p.get("stats"):
            st = p["stats"]
            lines.append("- `%s.%s`: n=%d, mean=%s, sd=%s, min=%s, max=%s"
                        % (p["table"], p["column"], st["n"], st["mean"],
                           st["stdev_sample"], st["min"], st["max"]))
    lines.append("")
    lines.append("## 4. 跨表连接候选")
    for p in analysis["cross_table"]:
        lines.append("- `%s.%s.%s` ↔ `%s.%s` (%s)"
                     % (p["left_table"], p["join_column"], p["left_table"],
                        p["right_table"], p["right_table"], p["cardinality"]))
    if not analysis["cross_table"]:
        lines.append("- （无共同列名）")
    lines.append("")
    lines.append("## 5. 查询日志")
    for q in query_log:
        lines.append("- `%s` → %s 行 (%s)" % (q.get("id"), q.get("row_count"),
                                              q.get("status")))
    if errors:
        lines.append("")
        lines.append("## 6. 错误与降级")
        for e in errors:
            lines.append("- 错误: %s" % e)
        for d in degradations:
            lines.append("- 降级: %s" % d)
    lines.append("")
    lines.append("本报告数字与 `evidence_map.json` 中带 locator + 逐字 quote 的条目对应。")
    return "\n".join(lines)
