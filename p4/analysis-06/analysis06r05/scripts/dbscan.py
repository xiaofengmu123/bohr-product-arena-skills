#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dbscan: read-only scientific data discovery, profiling and SVG rendering.

Design constraints
------------------
- Standard library only (csv, json, math, os, re, sqlite3, statistics).
- Deterministic: iteration is always over sorted names, no randomness.
- Read-only: every SQLite connection is opened with ``mode=ro`` and
  ``PRAGMA query_only=ON``; only SELECT / WITH / PRAGMA / EXPLAIN run.
- Defensive: a bad file, bad encoding or an unsupported column never aborts
  the whole pipeline; the problem is returned so the caller can record it.

The module treats SQLite databases and delimited text snapshots uniformly:
CSV/TSV files are ingested into an in-memory SQLite table so that schema
inspection, profiling, duplicate detection and correlation all go through the
same, re-runnable SQL + Python cross-check code path.
"""

import csv
import json
import math
import os
import re
import sqlite3
import statistics

VERSION = "1.0.0"

TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")
DB_EXTENSIONS = (".db", ".sqlite", ".sqlite3", ".db3")
CSV_EXTENSIONS = (".csv", ".tsv")
MAX_TEXT_FILE = 2 * 1024 * 1024
MAX_VALUES = 8000
MAX_TOP_VALUES = 20

NUMERIC_TYPE_RE = re.compile(r"int|real|float|double|numeric|decimal|number",
                             re.IGNORECASE)
READONLY_STMT_RE = re.compile(r"^\s*(select|with|pragma|explain)\b", re.IGNORECASE)
_IDENT_RE = re.compile(r"[A-Za-z0-9_]+$")


# --------------------------------------------------------------------------- #
# encoding / discovery
# --------------------------------------------------------------------------- #
def read_text_file(path, errors=None):
    """Decode a text file with a tolerant fallback chain. latin-1 never fails."""
    try:
        raw = open(path, "rb").read()
    except OSError as exc:
        if errors is not None:
            errors.append("%s: unreadable (%s)" % (path, exc))
        return None
    if len(raw) > MAX_TEXT_FILE:
        if errors is not None:
            errors.append("%s: exceeds %dB limit, skipped"
                          % (path, MAX_TEXT_FILE))
        return None
    for enc in TEXT_ENCODINGS:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError) as exc:
            last = "%s: decode %s failed (%s)" % (path, enc, exc)
    if errors is not None:
        errors.append(last)
    return None


def discover_files(input_dir, extensions):
    """Sorted list of files under *input_dir* matching *extensions*."""
    out = []
    if not os.path.isdir(input_dir):
        return out
    for name in sorted(os.listdir(input_dir)):
        path = os.path.join(input_dir, name)
        if not os.path.isfile(path):
            continue
        if name.lower().endswith(extensions):
            out.append(path)
    return out


# --------------------------------------------------------------------------- #
# SQLite helpers
# --------------------------------------------------------------------------- #
def quote_ident(name):
    return '"' + name.replace('"', '""') + '"'


def open_sqlite_ro(path):
    """Open a SQLite file read-only (fails if the file cannot be opened)."""
    posix = path.replace("\\", "/")
    conn = sqlite3.connect("file:%s?mode=ro" % posix, uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def is_readonly_stmt(sql):
    return bool(READONLY_STMT_RE.match(sql or ""))


def list_tables(conn):
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        return [r[0] for r in cur.fetchall()]
    except sqlite3.Error as exc:
        return ["__sqlite_error__%s" % str(exc)[:80]]


def table_info(conn, table):
    """Return column metadata from pragma_table_info."""
    q = ("SELECT name, type, \"notnull\", pk FROM pragma_table_info(%s)"
         % quote_ident(table))
    try:
        cur = conn.execute(q)
    except sqlite3.Error as exc:
        return [{"name": "__error__", "type": str(exc)[:120],
                 "notnull": False, "pk": 0, "sql": q}]
    return [{"name": r[0], "type": r[1] or "", "notnull": bool(r[2]),
             "pk": int(r[3] or 0)} for r in cur.fetchall()]


def row_count(conn, table):
    try:
        return conn.execute("SELECT COUNT(*) FROM %s" % quote_ident(table)) \
                   .fetchone()[0]
    except sqlite3.Error:
        return None


def inspect_schema(conn):
    schema = []
    for table in list_tables(conn):
        cols = table_info(conn, table)
        schema.append({"table": table,
                       "columns": cols,
                       "row_count": row_count(conn, table)})
    return schema


# --------------------------------------------------------------------------- #
# CSV ingestion (into an in-memory SQLite table)
# --------------------------------------------------------------------------- #
def _sniff_csv(text):
    """Return (dialect, has_header) or (None, False) on failure."""
    sample = text[:8192]
    if not sample.strip():
        return None, False
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        # fall back to comma
        class _D:  # minimal dialect stand-in
            delimiter = ","
            quotechar = '"'
            doublequote = True
            skipinitialspace = False
            lineterminator = "\r\n"
            escapechar = None
            strict = False
        dialect = _D
    try:
        has_header = csv.Sniffer().has_header(sample)
    except csv.Error:
        has_header = False
    return dialect, has_header


def _infer_col_type(values):
    """Map a list of raw strings to an approximate SQLite type."""
    nonempty = [v for v in values if v not in (None, "")]
    if not nonempty:
        return "TEXT"
    all_int = True
    all_num = True
    for v in nonempty:
        s = v.strip()
        if re.fullmatch(r"[+-]?\d+", s):
            continue
        all_int = False
        try:
            float(s)
        except ValueError:
            all_num = False
            break
    if all_int:
        return "INTEGER"
    if all_num:
        return "REAL"
    return "TEXT"


def ingest_csv(path, errors=None, degradations=None):
    """Read a CSV/TSV into an in-memory SQLite table; return table name or None."""
    text = read_text_file(path, errors)
    if text is None:
        return None
    dialect, has_header = _sniff_csv(text)
    reader = csv.reader(text.splitlines(), dialect)
    rows = [row for row in reader if row and any(c.strip() for c in row)]
    if not rows:
        if degradations is not None:
            degradations.append("%s: no non-empty rows" % path)
        return None
    header = [("col%d" % (i + 1)) for i in range(len(rows[0]))]
    if has_header:
        header = [h.strip() or ("col%d" % (i + 1))
                  for i, h in enumerate(rows[0])]
        # dedupe names
        seen = {}
        for i, h in enumerate(header):
            base = h
            k = 0
            while h in seen:
                k += 1
                h = "%s_%d" % (base, k)
            seen[h] = True
            header[i] = h
        body = rows[1:]
    else:
        body = rows

    table = re.sub(r"[^A-Za-z0-9_]", "_", os.path.splitext(os.path.basename(path))[0])
    if not table or table[0].isdigit():
        table = "csv_" + table
    table = table[:40]

    # type inference on the columns of the first MAX_VALUES rows
    cols = len(header)
    values_by_col = [[] for _ in range(cols)]
    for r in body[:MAX_VALUES]:
        for i in range(cols):
            values_by_col[i].append(r[i] if i < len(r) else "")
    col_types = [_infer_col_type(values_by_col[i]) for i in range(cols)]

    conn = sqlite3.connect(":memory:")
    ddl_cols = ", ".join("%s %s" % (quote_ident(header[i]), col_types[i])
                         for i in range(cols))
    conn.execute("CREATE TABLE %s (%s)" % (quote_ident(table), ddl_cols))
    ph = ",".join("?" * cols)
    ins = "INSERT INTO %s VALUES (%s)" % (quote_ident(table), ph)
    conn.executemany(ins, body[:MAX_VALUES])
    conn.commit()
    return table, conn, path


# --------------------------------------------------------------------------- #
# column profiling / duplicate detection / correlation
# --------------------------------------------------------------------------- #
def _numeric_stats(values):
    """Deterministic descriptive statistics for a list of floats."""
    n = len(values)
    if n == 0:
        return None
    mean = sum(values) / n
    if n >= 2:
        stdev_sample = statistics.stdev(values)
        stdev_pop = statistics.pstdev(values)
    else:
        stdev_sample = None
        stdev_pop = None
    srt = sorted(values)
    def _q(p):
        if n == 0:
            return None
        idx = (n - 1) * p
        lo = int(math.floor(idx))
        hi = int(math.ceil(idx))
        if lo == hi:
            return srt[lo]
        return srt[lo] + (srt[hi] - srt[lo]) * (idx - lo)
    q1 = _q(0.25)
    median = _q(0.5)
    q3 = _q(0.75)
    iqr = (q3 - q1) if (q1 is not None and q3 is not None) else None
    lo_fence = (q1 - 1.5 * iqr) if iqr is not None else None
    hi_fence = (q3 + 1.5 * iqr) if iqr is not None else None
    n_outliers = 0
    if lo_fence is not None and hi_fence is not None:
        n_outliers = sum(1 for v in values if v < lo_fence or v > hi_fence)
    return {"n": n, "mean": mean, "stdev_sample": stdev_sample,
            "stdev_pop": stdev_pop, "min": min(values), "max": max(values),
            "q1": q1, "median": median, "q3": q3, "iqr": iqr,
            "sum": sum(values), "n_outliers": n_outliers,
            "lo_fence": lo_fence, "hi_fence": hi_fence}


def _round(v, nd=6):
    if v is None:
        return None
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return round(v, nd)
    return v


def column_profile(conn, table, col, decl_type):
    qcol = quote_ident(col)
    qtab = quote_ident(table)
    extract_sql = "SELECT %s FROM %s" % (qcol, qtab)
    missing_sql = ("SELECT COUNT(*) FROM %s WHERE %s IS NULL "
                   "OR TRIM(CAST(%s AS TEXT)) = ''" % (qtab, qcol, qcol))
    vc_sql = ("SELECT CAST(%s AS TEXT) AS v, COUNT(*) AS c FROM %s "
              "GROUP BY 1 ORDER BY c DESC, v ASC LIMIT %d" % (qcol, qtab,
                                                               MAX_TOP_VALUES))
    try:
        values = [r[0] for r in conn.execute(extract_sql).fetchall()]
    except sqlite3.Error as exc:
        return {"column": col, "declared_type": decl_type, "error": str(exc),
                "extraction_sql": extract_sql, "n_total": 0, "n_nonnull": 0,
                "n_missing": 0, "n_distinct": 0, "top_values": []}
    nonnull = [v for v in values
               if v is not None and not (isinstance(v, str) and v.strip() == "")]
    n_total = len(values)
    n_nonnull = len(nonnull)
    n_missing = n_total - n_nonnull

    # value counts
    counts = {}
    for v in nonnull:
        key = v if not isinstance(v, float) else _round(v)
        counts[key] = counts.get(key, 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))[:MAX_TOP_VALUES]
    top_values = [{"value": k, "count": c} for k, c in top]
    n_distinct = len(counts)

    prof = {"column": col, "declared_type": decl_type,
            "extraction_sql": extract_sql, "missing_sql": missing_sql,
            "value_counts_sql": vc_sql,
            "n_total": n_total, "n_nonnull": n_nonnull, "n_missing": n_missing,
            "n_distinct": n_distinct, "top_values": top_values}

    # numeric inference
    nums = []
    all_numeric = bool(nonnull)
    for v in nonnull:
        if isinstance(v, bool):
            all_numeric = False
            break
        if isinstance(v, (int, float)):
            nums.append(float(v))
        elif isinstance(v, str):
            s = v.strip()
            try:
                nums.append(float(s))
            except ValueError:
                all_numeric = False
                break
        else:
            all_numeric = False
            break

    if all_numeric and nums:
        decl_numeric = bool(NUMERIC_TYPE_RE.search(decl_type or ""))
        coded_as_text = (not decl_numeric)
        stats = _numeric_stats(nums)
        # SQL aggregate cross-check only for genuinely numeric declared columns;
        # text-encoded numeric columns are converted in Python and flagged.
        agg = None
        if decl_numeric:
            agg_sql = ("SELECT COUNT(%s), AVG(%s), MIN(%s), MAX(%s), SUM(%s) "
                       "FROM %s" % (qcol, qcol, qcol, qcol, qcol, qtab))
            try:
                row = conn.execute(agg_sql).fetchone()
                agg = {"sql": agg_sql, "n": row[0], "avg": _round(row[1]),
                       "min": _round(row[2]), "max": _round(row[3]),
                       "sum": _round(row[4]),
                       "matches_python": (
                           row[0] == stats["n"]
                           and _round(row[1]) == _round(stats["mean"])
                           and _round(row[2]) == _round(stats["min"])
                           and _round(row[3]) == _round(stats["max"]))}
            except sqlite3.Error as exc:
                agg = {"sql": agg_sql, "error": str(exc)}
        prof["inferred_kind"] = "numeric"
        prof["coded_as_text"] = coded_as_text
        prof["numeric"] = {"values_note": "python-converted floats",
                           "raw_values": nums,
                           "stats": {k: _round(v) for k, v in stats.items()},
                           "raw_min": min(nums), "raw_max": max(nums)}
        prof["aggregate"] = agg
    else:
        prof["inferred_kind"] = "empty" if not nonnull else "text"
        prof["coded_as_text"] = False
    return prof


def detect_duplicates(conn, table):
    cols = table_info(conn, table)
    if not cols or (len(cols) == 1 and cols[0]["name"].startswith("__")):
        return {"full_row_dup_sql": None, "n_duplicate_groups": None,
                "n_rows_in_duplicate_groups": None, "error": "no columns"}
    col_expr = ", ".join(quote_ident(c["name"]) for c in cols)
    sql = ("SELECT %s, COUNT(*) AS c FROM %s GROUP BY %s HAVING c > 1"
           % (col_expr, quote_ident(table), col_expr))
    try:
        rows = conn.execute(sql).fetchall()
    except sqlite3.Error as exc:
        return {"full_row_dup_sql": sql, "n_duplicate_groups": None,
                "n_rows_in_duplicate_groups": None, "error": str(exc)}
    return {"full_row_dup_sql": sql, "n_duplicate_groups": len(rows),
            "n_rows_in_duplicate_groups": sum(r[-1] for r in rows)}


def _pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return None
    return cov / math.sqrt(vx * vy)


def correlate_table(conn, table, numeric_cols):
    """Pearson correlation for every numeric column pair (deterministic)."""
    pairs = []
    n = len(numeric_cols)
    for i in range(n):
        for j in range(i + 1, n):
            a = numeric_cols[i]
            b = numeric_cols[j]
            xs = [x for x in a["values"] if x is not None]
            ys = [y for y in b["values"] if y is not None]
            if len(xs) != len(ys) or len(xs) < 2:
                r = None
            else:
                r = _pearson(xs, ys)
            pairs.append({"a": a["name"], "b": b["name"],
                          "pearson_r": _round(r), "n": len(xs)})
    return pairs


def run_query(conn, sql, max_rows=200):
    """Execute one read-only query and return rows / row_count / error."""
    if not is_readonly_stmt(sql):
        return {"rows": [], "row_count": None,
                "error": "non read-only statement blocked"}
    try:
        cur = conn.execute(sql)
        rows = cur.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        row_count = None
        try:
            row_count = cur.rowcount if cur.rowcount >= 0 else None
        except Exception:
            row_count = None
        if row_count is None:
            row_count = len(rows)
        return {"rows": rows, "row_count": row_count, "truncated": truncated}
    except sqlite3.Error as exc:
        return {"rows": [], "row_count": None, "error": str(exc)}


# --------------------------------------------------------------------------- #
# editable SVG charts (every chart embeds its source data as a comment)
# --------------------------------------------------------------------------- #
def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def svg_histogram(values, title, xlabel):
    """Histogram SVG; returns None when there are fewer than two values."""
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return None
    lo, hi = min(vals), max(vals)
    if lo == hi:
        return None
    nbins = min(20, max(5, int(math.sqrt(len(vals)))))
    width = (hi - lo) / nbins
    bins = [0] * nbins
    for v in vals:
        idx = min(nbins - 1, int((v - lo) / width))
        bins[idx] += 1
    max_b = max(bins) or 1
    W, H = 640, 360
    plot_l, plot_r, plot_t, plot_b = 70, 620, 30, 310
    body = []
    for i, c in enumerate(bins):
        x = plot_l + (plot_r - plot_l) * i / nbins
        bw = (plot_r - plot_l) / nbins
        bh = (plot_b - plot_t) * c / max_b
        y = plot_b - bh
        body.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                    'fill="#4c72b0" stroke="#ffffff" stroke-width="1"/>'
                    % (x, y, bw - 1, bh))
    data_json = json.dumps({"title": title, "bins": bins, "min": lo,
                            "max": hi, "n": len(vals)}, ensure_ascii=False)
    svg = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<!-- data: %s -->\n'
           '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
           'viewBox="0 0 %d %d">\n'
           '<rect width="100%%" height="100%%" fill="white"/>\n'
           '<text x="%d" y="20" font-size="14" font-family="sans-serif">%s</text>\n'
           '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#333"/>'
           '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#333"/>\n'
           '%s\n'
           '<text x="%d" y="%d" font-size="11" font-family="sans-serif" '
           'text-anchor="middle">%s</text>\n'
           '</svg>\n'
           % (data_json, W, H, W, H, plot_l, _esc(title), plot_l, plot_b,
              plot_r, plot_b, plot_l, plot_t, plot_l, plot_b,
              "\n".join(body), (plot_l + plot_r) / 2, H - 6, _esc(xlabel)))
    return svg


def svg_corr_heatmap(pairs):
    """Correlation matrix heatmap SVG; None when there are no pairs."""
    if not pairs:
        return None
    names = []
    for p in pairs:
        if p["a"] not in names:
            names.append(p["a"])
        if p["b"] not in names:
            names.append(p["b"])
    names = sorted(names)
    n = len(names)
    idx = {name: i for i, name in enumerate(names)}
    cell = 56
    pad = 110
    W = pad + n * cell
    H = pad + n * cell
    body = []
    for p in pairs:
        r = p["pearson_r"]
        if r is None:
            color = "#eeeeee"
            txt = "NA"
        else:
            # blue-white-red diverging scale
            t = max(-1.0, min(1.0, r))
            if t >= 0:
                color = "#%02x%02x%02x" % (255, int(255 - 255 * t),
                                           int(255 - 255 * t))
            else:
                color = "#%02x%02x%02x" % (int(255 + 255 * t),
                                           int(255 + 255 * t), 255)
            txt = "%.2f" % r
        i, j = idx[p["a"]], idx[p["b"]]
        x = pad + i * cell
        y = pad + j * cell
        body.append('<rect x="%d" y="%d" width="%d" height="%d" fill="%s"/>'
                    % (x, y, cell - 2, cell - 2, color))
        body.append('<text x="%d" y="%d" font-size="10" text-anchor="middle" '
                    'dominant-baseline="middle" font-family="sans-serif">%s</text>'
                    % (x + (cell - 2) / 2, y + (cell - 2) / 2, txt))
    labels = []
    for i, name in enumerate(names):
        labels.append('<text x="%d" y="%d" font-size="10" font-family="sans-serif" '
                      'transform="rotate(-40 %d %d)" text-anchor="end">%s</text>'
                      % (pad + i * cell, pad - 8, pad + i * cell, pad - 8,
                         _esc(name)))
    data_json = json.dumps({"columns": names, "pairs": pairs},
                           ensure_ascii=False)
    svg = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<!-- data: %s -->\n'
           '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d">\n'
           '<rect width="100%%" height="100%%" fill="white"/>\n%s\n%s\n'
           '</svg>\n' % (data_json, W, H, "\n".join(body), "\n".join(labels)))
    return svg


def svg_missing_bar(columns):
    """Bar chart of missing counts per column; None when there are no columns."""
    if not columns:
        return None
    cols = [c for c in columns if c.get("n_missing", 0) is not None]
    if not cols:
        return None
    max_m = max(c["n_missing"] for c in cols) or 1
    W, H = 640, 360
    plot_l, plot_r, plot_t, plot_b = 150, 620, 30, 320
    n = len(cols)
    body = []
    for i, c in enumerate(cols):
        x = plot_l + (plot_r - plot_l) * i / n
        bw = (plot_r - plot_l) / n
        bh = (plot_b - plot_t) * c["n_missing"] / max_m
        body.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                    'fill="#c0392b"/>' % (x, plot_b - bh, bw - 2, bh))
        body.append('<text x="%.1f" y="%d" font-size="9" '
                    'transform="rotate(-35 %.1f %d)" text-anchor="end" '
                    'font-family="sans-serif">%s</text>'
                    % (x + bw / 2, plot_b + 12, x + bw / 2, plot_b + 12,
                       _esc(c["column"][:16])))
    data_json = json.dumps(
        [{"column": c["column"], "n_missing": c["n_missing"]} for c in cols],
        ensure_ascii=False)
    svg = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<!-- data: %s -->\n'
           '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d">\n'
           '<rect width="100%%" height="100%%" fill="white"/>\n'
           '<text x="20" y="20" font-size="14" font-family="sans-serif">'
           'missing values per column</text>\n%s\n</svg>\n'
           % (data_json, W, H, "\n".join(body)))
    return svg


# --------------------------------------------------------------------------- #
# generic artifact writers (kept here so main.py stays small)
# --------------------------------------------------------------------------- #
def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=False,
                  default=str)


def build_report(result, tables, degradations, errors, pending_human,
                 contract_fidelity):
    lines = ["# 科研数据库查询与分析报告", "",
             "- case_id: `%s`" % str(result.get("case_id")), "",
             "## 1. 输入与数据快照", ""]
    lines.append("- 表数量：%d" % len(tables))
    for t in tables:
        lines.append("- `%s`：%s 行，%d 列"
                     % (t["table"], str(t["row_count"]), len(t["columns"])))
    lines += ["", "## 2. 数据质量", "",
              "缺失、重复与类型推断见 `data_quality.csv`；每列 top 取值见 "
              "`value_counts.json`。"]
    if degradations:
        lines += ["", "### 降级与处理说明", ""]
        lines += ["- " + d for d in degradations]
    if errors:
        lines += ["", "### 错误", ""]
        lines += ["- " + e for e in errors]
    lines += ["", "## 3. 统计与相关", "",
              "数值列描述统计见 `summary_stats.csv`，Pearson 相关见 "
              "`correlation_matrix.csv`，图表见 `figures/`。",
              "", "## 4. 契约对齐", "",
              "- contract 字段总数：%d" % contract_fidelity["total"],
              "- 已填充：%d" % contract_fidelity["filled"],
              "- 未填充：%d" % contract_fidelity["unfilled"],
              "- status：`%s`" % result.get("status"), "",
              "## 5. 未决与限制", ""]
    lines += ["- " + p for p in pending_human] or ["- 无"]
    return "\n".join(lines) + "\n"

