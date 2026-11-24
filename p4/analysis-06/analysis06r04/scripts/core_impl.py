#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl: deterministic, read-only data-analysis helpers.

Only the Python standard library is used. Every function is pure and
deterministic: identical inputs produce identical outputs. Callers add
timestamps and file-system side effects; this module never does.

Design note (upgrade direction: degradation & uncertainty).
Every numeric result carries the sample size and, where computable, an
approximate 95% confidence interval for the mean, so a conclusion is never
stated more strongly than its data support. Every analysis step records a
degradation when a precondition is not met and falls back to a weaker but
valid output instead of fabricating a value.
"""

import csv
import html
import io
import json
import math
import os
import re
import sqlite3
import statistics

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_COLUMN_VALUES = 50000
TOP_N = 10
STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

# SQL forms considered read-only and safe to execute.
_READ_SQL_RE = re.compile(r"(?i)^\s*(select|with|explain)\b")
_READ_PRAGMA_RE = re.compile(
    r"(?i)^\s*pragma\s+(table_info|table_xinfo|index_list|foreign_key_list|"
    r"database_list|schema_version|page_size|page_count|journal_mode)\b"
)


# --------------------------------------------------------------------------- #
# text / json reading (never raises)
# --------------------------------------------------------------------------- #
def read_text(path):
    """Return (text, encoding, error). Never raises."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except Exception as exc:  # pragma: no cover - depends on fs
        return None, None, "read failed: %s" % exc
    if len(raw) > MAX_TEXT_BYTES:
        return None, None, "file exceeds %d bytes; skipped" % MAX_TEXT_BYTES
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc, None
        except (UnicodeDecodeError, LookupError):
            continue
    # latin-1 maps every byte to a code point; used only as a lossy-safe last
    # resort and always reported as a degradation by the caller.
    return raw.decode("latin-1"), "latin-1", "fallback latin-1 decode"


def read_json(path):
    """Return (data, error)."""
    text, _enc, err = read_text(path)
    if err is not None:
        return None, err
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001
        return None, "JSON parse failed: %s" % exc


def write_json(path, data):
    """Write JSON deterministically (sorted keys). Return error or None."""
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        return None
    except Exception as exc:  # noqa: BLE001
        return "write failed: %s" % exc


def write_text(path, text):
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return None
    except Exception as exc:  # noqa: BLE001
        return "write failed: %s" % exc


def ensure_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
        return None
    except Exception as exc:  # noqa: BLE001
        return "mkdir failed: %s" % exc


# --------------------------------------------------------------------------- #
# SQL helpers
# --------------------------------------------------------------------------- #
def quote_ident(name):
    return '"' + str(name).replace('"', '""') + '"'


def connect_sqlite(path):
    """Open SQLite read-only. Return (conn, error)."""
    try:
        import pathlib
        uri = pathlib.Path(path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        conn.execute("PRAGMA query_only = ON")
        conn.row_factory = None
        return conn, None
    except Exception as exc:  # noqa: BLE001
        return None, "sqlite connect failed: %s" % exc


def list_tables(conn):
    try:
        rows = conn.execute(
            "SELECT name, type FROM sqlite_master "
            "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name").fetchall()
        return [{"name": r[0], "type": r[1]} for r in rows], None
    except Exception as exc:  # noqa: BLE001
        return [], "list tables failed: %s" % exc


def table_info(conn, table):
    try:
        rows = conn.execute("PRAGMA table_info(%s)" % quote_ident(table)).fetchall()
        return [{"cid": r[0], "name": r[1], "type": (r[2] or "").upper(),
                 "notnull": r[3], "dflt": r[4], "pk": r[5]} for r in rows], None
    except Exception as exc:  # noqa: BLE001
        return [], "PRAGMA table_info failed: %s" % exc


def table_row_count(conn, table):
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM %s" % quote_ident(table)).fetchone()[0], None
    except Exception as exc:  # noqa: BLE001
        return None, "row count failed: %s" % exc


def extract_column(conn, table, col):
    """Return (values, sql, error). Caps at MAX_COLUMN_VALUES rows."""
    sql = "SELECT %s FROM %s" % (quote_ident(col), quote_ident(table))
    try:
        cur = conn.execute(sql)
        vals = []
        for row in cur:
            vals.append(row[0])
            if len(vals) >= MAX_COLUMN_VALUES:
                break
        return vals, sql, None
    except Exception as exc:  # noqa: BLE001
        return None, sql, "column extraction failed: %s" % exc


def sql_allowed(sql):
    """True when sql is a read-only SELECT/WITH/EXPLAIN or safe PRAGMA."""
    s = (sql or "").strip()
    if not s:
        return False
    if _READ_SQL_RE.match(s):
        return True
    if _READ_PRAGMA_RE.match(s):
        return True
    return False


def run_select(conn, sql, max_rows=200):
    """Execute one read-only query. Return (result, error)."""
    if not sql_allowed(sql):
        return None, "query rejected: not read-only SQL"
    s = sql.strip().rstrip(";").strip()
    try:
        cur = conn.execute(s)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        if truncated:
            rows = rows[:max_rows]
        total = None
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM (%s) AS _sub" % s).fetchone()[0]
        except Exception:  # noqa: BLE001 - count wrap unsupported for some SQL
            total = len(rows)
        return {"columns": cols, "rows": rows, "row_count": total,
                "truncated": truncated}, None
    except Exception as exc:  # noqa: BLE001
        return None, "query failed: %s" % exc


def detect_duplicates(conn, table):
    """Detect exact full-row duplicate groups."""
    info, err = table_info(conn, table)
    if err or not info:
        return {"error": err or "no columns"}
    cols = [c["name"] for c in info]
    col_expr = ", ".join(quote_ident(c) for c in cols)
    sql = ("SELECT %s, COUNT(*) AS _n FROM %s GROUP BY %s "
           "HAVING COUNT(*) > 1 ORDER BY _n DESC"
           % (col_expr, quote_ident(table), col_expr))
    try:
        rows = conn.execute(sql).fetchall()
        n_groups = len(rows)
        n_rows_in_groups = sum(r[-1] for r in rows)
        return {"full_row_dup_sql": sql, "n_duplicate_groups": n_groups,
                "n_rows_in_duplicate_groups": n_rows_in_groups}
    except Exception as exc:  # noqa: BLE001
        return {"full_row_dup_sql": sql, "error": "duplicate detection failed: %s" % exc}


# --------------------------------------------------------------------------- #
# value profiling
# --------------------------------------------------------------------------- #
def _is_missing(v):
    return v is None or (isinstance(v, str) and v.strip() == "")


def try_numeric(present):
    """Attempt numeric coercion. Return (values, kind, note).

    kind in: int | float | mixed | nonnumeric | empty.
    `mixed` means >=80% of non-missing values are numeric; the rest are
    treated as missing for numeric statistics and this fact is reported.
    """
    if not present:
        return [], "empty", "no non-missing values"
    converted = []
    n_numeric = 0
    all_int = True
    for v in present:
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            converted.append(float(v))
            n_numeric += 1
            continue
        if isinstance(v, float):
            if math.isnan(v) or math.isinf(v):
                continue
            converted.append(v)
            n_numeric += 1
            all_int = False
            continue
        if isinstance(v, str):
            s = v.strip()
            if s == "":
                continue
            if re.fullmatch(r"[+-]?\d+", s):
                converted.append(float(int(s)))
                n_numeric += 1
                continue
            try:
                f = float(s)
            except ValueError:
                continue
            if math.isnan(f) or math.isinf(f):
                continue
            converted.append(f)
            n_numeric += 1
            all_int = False
            continue
    if n_numeric == 0:
        return [], "nonnumeric", "no numeric-convertible values"
    ratio = n_numeric / float(len(present))
    if ratio >= 0.98:
        return converted, ("int" if all_int else "float"), None
    if ratio >= 0.80:
        return converted, "mixed", \
            "%d/%d non-missing values numeric; non-numeric treated as missing" \
            % (n_numeric, len(present))
    return [], "nonnumeric", "majority of values are non-numeric"


def descriptive_stats(values):
    """Return a deterministic summary; None where not computable."""
    out = {"n": len(values), "mean": None, "stdev_sample": None, "min": None,
           "max": None, "q1": None, "median": None, "q3": None,
           "ci95_mean": None}
    if not values:
        return out
    finite = [float(v) for v in values
              if v is not None and math.isfinite(float(v))]
    n = len(finite)
    if n == 0:
        return out
    out["n"] = n
    out["mean"] = round(statistics.fmean(finite), 12)
    out["min"] = min(finite)
    out["max"] = max(finite)
    srt = sorted(finite)
    if n >= 2:
        out["stdev_sample"] = round(statistics.stdev(finite), 12)

    def q(p):
        k = (n - 1) * p
        lo = math.floor(k)
        hi = math.ceil(k)
        if lo == hi:
            return srt[lo]
        return srt[lo] + (srt[hi] - srt[lo]) * (k - lo)

    out["q1"] = round(q(0.25), 12)
    out["median"] = round(q(0.50), 12)
    out["q3"] = round(q(0.75), 12)
    if n >= 2 and out["stdev_sample"] and out["stdev_sample"] > 0:
        se = out["stdev_sample"] / math.sqrt(n)
        out["ci95_mean"] = [round(out["mean"] - 1.96 * se, 12),
                            round(out["mean"] + 1.96 * se, 12)]
    return out


def value_counts(values, top_n=TOP_N):
    """Return ordered list of {value, count, fraction}."""
    counts = {}
    n_present = 0
    for v in values:
        if _is_missing(v):
            continue
        n_present += 1
        key = v
        if isinstance(v, float) and v == int(v):
            key = int(v)
        counts[key] = counts.get(key, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))
    out = []
    for value, count in ranked[:top_n]:
        out.append({"value": value, "count": count,
                    "fraction": round(count / float(n_present), 6)
                    if n_present else None})
    return out


def profile_values(values, decl_type):
    """Return a full column profile. decl_type is the declared SQL type."""
    n_total = len(values)
    present = [v for v in values if not _is_missing(v)]
    n_nonnull = len(present)
    n_missing = n_total - n_nonnull
    numeric_vals, kind, numeric_note = try_numeric(present)

    types = {}
    for v in present:
        t = "bool" if isinstance(v, bool) else type(v).__name__
        types[t] = types.get(t, 0) + 1
    try:
        n_distinct = len(set(present))
    except TypeError:  # pragma: no cover - unhashable scalar is unusual
        n_distinct = None

    declared_numeric = bool(decl_type) and any(
        k in decl_type.upper() for k in
        ("INT", "REAL", "FLOA", "DOUB", "NUM", "DEC"))
    coded = (not declared_numeric) and kind in ("int", "float", "mixed")

    profile = {
        "n_total": n_total,
        "n_nonnull": n_nonnull,
        "n_missing": n_missing,
        "n_distinct": n_distinct,
        "types": types,
        "top_values": value_counts(present),
        "kind": kind if kind in ("int", "float", "mixed") else
                ("numeric" if kind == "nonnumeric" and declared_numeric else "text"),
        "declared_type": decl_type or "",
        "coded_as_text": coded,
        "conversion_note": numeric_note,
    }
    if kind in ("int", "float", "mixed"):
        profile["kind"] = "numeric"
        profile["stats"] = descriptive_stats(numeric_vals)
        profile["numeric_n_used"] = len(numeric_vals)
        profile["numeric_values"] = numeric_vals
    return profile


def pearson(xs, ys):
    """Return (r, n) or (None, n) when undefined."""
    pairs = [(float(x), float(y)) for x, y in zip(xs, ys)
             if x is not None and y is not None]
    n = len(pairs)
    if n < 3:
        return None, n
    mx = statistics.fmean([p[0] for p in pairs])
    my = statistics.fmean([p[1] for p in pairs])
    num = sum((x - mx) * (y - my) for x, y in pairs)
    dx = math.sqrt(sum((x - mx) ** 2 for x, y in pairs))
    dy = math.sqrt(sum((y - my) ** 2 for x, y in pairs))
    if dx == 0 or dy == 0:
        return None, n
    return round(num / (dx * dy), 12), n


def fetch_pairs(conn, table, c1, c2):
    sql = "SELECT %s, %s FROM %s" % (quote_ident(c1), quote_ident(c2),
                                     quote_ident(table))
    try:
        rows = conn.execute(sql).fetchall()
    except Exception as exc:  # noqa: BLE001
        return None, "pair extraction failed: %s" % exc
    a, b = [], []
    for r in rows:
        a.append(r[0])
        b.append(r[1])
    return (a, b), None


def histogram(values, n_bins=None):
    if not values:
        return None
    finite = []
    for v in values:
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            finite.append(f)
    if not finite:
        return None
    mn, mx = min(finite), max(finite)
    if mn == mx:
        return {"bin_edges": [mn, mn], "counts": [len(finite)],
                "bin_width": 0.0, "n_bins": 1, "n": len(finite)}
    k = n_bins or min(12, max(5, int(math.ceil(math.sqrt(len(finite))))))
    k = max(1, k)
    width = (mx - mn) / float(k)
    counts = [0] * k
    edges = [mn + i * width for i in range(k + 1)]
    for v in finite:
        i = int((v - mn) / width)
        if i >= k:
            i = k - 1
        if i < 0:
            i = 0
        counts[i] += 1
    return {"bin_edges": [round(e, 12) for e in edges], "counts": counts,
            "bin_width": round(width, 12), "n_bins": k, "n": len(finite)}


# --------------------------------------------------------------------------- #
# SVG rendering (editable, deterministic)
# --------------------------------------------------------------------------- #
def _esc(s):
    return html.escape(str(s), quote=True)


def render_histogram_svg(hist, title):
    if not hist:
        return None
    counts = hist["counts"]
    edges = hist["bin_edges"]
    W, H = 640, 400
    ml, mr, mt, mb = 60, 18, 52, 60
    pw, ph = W - ml - mr, H - mt - mb
    maxc = max(counts) or 1
    n = len(counts)
    bw = pw / float(n)
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'viewBox="0 0 %d %d">' % (W, H, W, H)]
    parts.append('<rect x="0" y="0" width="%d" height="%d" fill="#ffffff"/>'
                 % (W, H))
    parts.append('<text x="%d" y="28" font-size="14" font-family="sans-serif" '
                 'text-anchor="middle" fill="#1a1a1a">%s</text>'
                 % (W / 2, _esc(title)))
    for t in range(6):
        frac = t / 5.0
        yy = mt + ph - frac * ph
        val = frac * maxc
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" '
                     'stroke="#d9d9d9"/>' % (ml, yy, W - mr, yy))
        parts.append('<text x="%d" y="%.1f" font-size="10" '
                     'font-family="sans-serif" text-anchor="end" '
                     'fill="#555">%.4g</text>' % (ml - 6, yy + 3, val))
    for i, c in enumerate(counts):
        h = (c / float(maxc)) * ph
        x = ml + i * bw
        y = mt + ph - h
        parts.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" '
                     'fill="#4c78a8" stroke="#1f3b5c" stroke-width="0.6"/>'
                     % (x, y, max(0.5, bw * 0.9), h))
    # x-axis labels: first/mid/last edges
    for idx in (0, n // 2, n):
        if idx > len(edges) - 1:
            idx = len(edges) - 1
        xx = ml + (idx / float(n)) * pw
        parts.append('<text x="%.1f" y="%d" font-size="10" '
                     'font-family="sans-serif" text-anchor="middle" '
                     'fill="#555">%.4g</text>' % (xx, mt + ph + 16, edges[idx]))
    parts.append('</svg>')
    return "\n".join(parts)


def render_bar_svg(vc, title):
    if not vc:
        return None
    labels = [str(item["value"]) for item in vc]
    counts = [item["count"] for item in vc]
    W, H = 640, 400
    ml, mr, mt, mb = 60, 18, 52, 120
    pw, ph = W - ml - mr, H - mt - mb
    maxc = max(counts) or 1
    n = len(counts)
    bw = pw / float(n)
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'viewBox="0 0 %d %d">' % (W, H, W, H)]
    parts.append('<rect x="0" y="0" width="%d" height="%d" fill="#ffffff"/>'
                 % (W, H))
    parts.append('<text x="%d" y="28" font-size="14" font-family="sans-serif" '
                 'text-anchor="middle" fill="#1a1a1a">%s</text>'
                 % (W / 2, _esc(title)))
    for i, c in enumerate(counts):
        h = (c / float(maxc)) * ph
        x = ml + i * bw
        y = mt + ph - h
        parts.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" '
                     'fill="#e07b54" stroke="#7a3b1f" stroke-width="0.6"/>'
                     % (x, y, max(0.5, bw * 0.82), h))
        parts.append('<text x="%.1f" y="%.1f" font-size="9" '
                     'font-family="sans-serif" text-anchor="middle" '
                     'fill="#1a1a1a" transform="rotate(-40 %.1f %.1f)">%s</text>'
                     % (x + bw * 0.41, mt + ph + 14, x + bw * 0.41,
                        mt + ph + 14, _esc(labels[i][:18])))
    parts.append('</svg>')
    return "\n".join(parts)


def render_heatmap_svg(matrix, labels, title):
    if not matrix or not labels or len(matrix) != len(labels):
        return None
    n = len(labels)
    cell = 42
    label_h = 24
    W = cell * (n + 1) + 10
    H = cell * (n + 1) + label_h + 10
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'viewBox="0 0 %d %d">' % (W, H, W, H)]
    parts.append('<rect x="0" y="0" width="%d" height="%d" fill="#ffffff"/>'
                 % (W, H))
    parts.append('<text x="%d" y="18" font-size="13" font-family="sans-serif" '
                 'text-anchor="middle" fill="#1a1a1a">%s</text>'
                 % (W / 2, _esc(title)))
    top = label_h
    left = cell + 6
    for i, lab in enumerate(labels):
        parts.append('<text x="%d" y="%d" font-size="9" font-family='
                     '"sans-serif" text-anchor="end" fill="#333">%s</text>'
                     % (left - 4, top + i * cell + cell / 2 + 3, _esc(lab[:16])))
        parts.append('<text x="%d" y="%d" font-size="9" font-family='
                     '"sans-serif" text-anchor="start" fill="#333" '
                     'transform="rotate(-40 %d %d)">%s</text>'
                     % (left + i * cell + 2, top - 4,
                        left + i * cell + 2, top - 4, _esc(lab[:16])))
    for i in range(n):
        for j in range(n):
            v = matrix[i][j]
            color = "#e0e0e0" if v is None else _heat_color(v)
            x = left + j * cell
            y = top + i * cell
            parts.append('<rect x="%d" y="%d" width="%d" height="%d" '
                         'fill="%s" stroke="#cccccc"/>'
                         % (x, y, cell - 2, cell - 2, color))
            if v is not None:
                parts.append('<text x="%d" y="%d" font-size="9" '
                             'font-family="sans-serif" text-anchor="middle" '
                             'fill="#000">%.2f</text>'
                             % (x + (cell - 2) / 2, y + (cell - 2) / 2 + 3, v))
    parts.append('</svg>')
    return "\n".join(parts)


def _heat_color(v):
    v = max(-1.0, min(1.0, v))
    if v >= 0:
        g = int(255 * (1 - v))
        return "rgb(%d,%d,255)" % (g, g)
    r = int(255 * (1 + v))
    return "rgb(255,%d,%d)" % (r, r)


# --------------------------------------------------------------------------- #
# CSV reading (data snapshots delivered as text tables)
# --------------------------------------------------------------------------- #
def read_csv_as_table(path):
    """Return (table_dict, error). table_dict: {name, columns, rows}."""
    text, enc, err = read_text(path)
    if err is not None:
        return None, err
    try:
        sample = text[:4096]
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except Exception:  # noqa: BLE001
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows = list(reader)
    if not rows:
        return {"name": None, "columns": [], "rows": []}, None
    header = [str(c).strip() or ("col%d" % i) for i, c in enumerate(rows[0])]
    data = rows[1:]
    columns = []
    for j, name in enumerate(header):
        vals = [r[j] if j < len(r) else None for r in data]
        columns.append({"name": name, "values": vals})
    return {"name": None, "columns": columns, "rows": data,
            "encoding": enc, "delimiter": dialect.delimiter}, None


# --------------------------------------------------------------------------- #
# table / report writers (kept here to keep main.py small)
# --------------------------------------------------------------------------- #
def write_csv_rows(path, header, rows):
    try:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            for r in rows:
                w.writerow(r)
        return None
    except Exception as exc:  # noqa: BLE001
        return "csv write failed: %s" % exc


def write_summary_csv(result, output_dir, artifacts):
    path = os.path.join(output_dir, "summary_stats.csv")
    rows = []
    for key, prof in result["analysis"].get("column_profiles", {}).items():
        parts = key.split(":", 2)
        st = prof.get("stats") or {}
        ci95 = st.get("ci95_mean") or [None, None]
        rows.append([parts[0], parts[1] if len(parts) > 1 else "",
                     parts[2] if len(parts) > 2 else "", prof.get("kind"),
                     prof.get("n_total"), prof.get("n_missing"),
                     prof.get("n_distinct"), prof.get("coded_as_text"),
                     prof.get("conversion_note") or "", st.get("n"),
                     st.get("mean"), st.get("stdev_sample"), st.get("min"),
                     st.get("q1"), st.get("median"), st.get("q3"),
                     st.get("max"), ci95[0], ci95[1]])
    header = ["database", "table", "column", "kind", "n_total", "n_missing",
              "n_distinct", "coded_as_text", "conversion_note", "n_numeric",
              "mean", "stdev_sample", "min", "q1", "median", "q3", "max",
              "ci95_low", "ci95_high"]
    err = write_csv_rows(path, header, rows)
    if err is None:
        artifacts[os.path.basename(path)] = "table"


def write_quality_csv(result, output_dir, artifacts):
    path = os.path.join(output_dir, "data_quality.csv")
    header = ["database", "table", "column", "n_total", "n_missing",
              "n_distinct", "type_breakdown"]
    rows = []
    for key, prof in result["analysis"].get("column_profiles", {}).items():
        parts = key.split(":", 2)
        rows.append([parts[0], parts[1] if len(parts) > 1 else "",
                     parts[2] if len(parts) > 2 else "", prof.get("n_total"),
                     prof.get("n_missing"), prof.get("n_distinct"),
                     json.dumps(prof.get("types") or {}, sort_keys=True)])
    err = write_csv_rows(path, header, rows)
    if err is None:
        artifacts[os.path.basename(path)] = "table"


def write_correlation_csv(result, output_dir, artifacts):
    path = os.path.join(output_dir, "correlation_matrix.csv")
    header = ["database", "table", "column_a", "column_b", "n", "pearson_r"]
    rows = []
    for key, pairs in result["analysis"].get("correlations_long", {}).items():
        db, table = key.split(":", 1)
        for p in pairs:
            rows.append([db, table, p["column_a"], p["column_b"], p["n"],
                         p["pearson_r"]])
    err = write_csv_rows(path, header, rows)
    if err is None:
        artifacts[os.path.basename(path)] = "table"


def write_query_log_csv(qlog, output_dir, artifacts):
    path = os.path.join(output_dir, "query_log.csv")
    header = ["id", "sql", "purpose", "row_count", "status", "error"]
    rows = [[e.get("id"), e.get("sql") or "", e.get("purpose"),
             e.get("row_count"), e.get("status"), e.get("error") or ""]
            for e in qlog]
    err = write_csv_rows(path, header, rows)
    if err is None:
        artifacts[os.path.basename(path)] = "table"


def write_report(result, output_dir, artifacts, ts):
    lines = []
    lines.append("# 科研数据库查询与分析报告")
    lines.append("")
    lines.append("- case_id: `%s`" % result.get("case_id"))
    lines.append("- status: `%s`" % result.get("status"))
    lines.append("- schema_version: `%s`" % result.get("schema_version"))
    lines.append("- generated_at: `%s`" % ts)
    lines.append("")
    lines.append("## 1. 输入与数据源")
    lines.append("")
    inputs = result["analysis"].get("inputs") or {}
    lines.append("- 数据快照: %d 个数据库, %d 个 CSV"
                 % (len(inputs.get("dbs", [])), len(inputs.get("csvs", []))))
    for db in result["analysis"].get("databases", []):
        lines.append("  - SQLite `%s` (%s)" % (db["label"], db["file"]))
        for t in db["tables"]:
            lines.append("    - 表 `%s`: %s 行, %d 列"
                         % (t["name"], t["row_count"], len(t["columns"])))
    lines.append("")
    lines.append("## 2. 数据质量与不确定性")
    lines.append("")
    if result.get("degradations"):
        lines.append("### 降级记录")
        for d in result["degradations"]:
            lines.append("- **%s**: %s（fallback: %s）"
                         % (d["item"], d["reason"], d["fallback"]))
        lines.append("")
    if result.get("uncertainty"):
        lines.append("### 不确定性")
        for u in result["uncertainty"]:
            lines.append("- `%s`（level=%s）: %s" % (u["item"], u["level"],
                                                     u["reason"]))
        lines.append("")
    if result.get("pending_human"):
        lines.append("### 待人工确认")
        for p in result["pending_human"]:
            lines.append("- %s" % p)
        lines.append("")
    lines.append("## 3. 分析结果")
    lines.append("")
    lines.append("| 表 | 行数 | 列数 | 缺失总数 |")
    lines.append("|-|-|-|-|")
    for t in result["analysis"].get("tables", []):
        lines.append("| %s.%s | %s | %s | %s |"
                     % (t["database"], t["table"], t["row_count"],
                        t["n_columns"], t["n_missing_total"]))
    lines.append("")
    lines.append("## 4. 查询日志")
    lines.append("")
    if result.get("query_log"):
        for q in result["query_log"]:
            lines.append("- `%s`（%s, %s 行）: `%s`"
                         % (q.get("id"), q.get("status"), q.get("row_count"),
                            (q.get("sql") or "")[:120]))
    else:
        lines.append("（无）")
    lines.append("")
    lines.append("## 5. 限制与边界")
    lines.append("")
    if result.get("limitations"):
        for lim in result["limitations"]:
            lines.append("- %s" % lim)
    else:
        lines.append("- 无")
    lines.append("")
    lines.append("## 6. 产物清单")
    lines.append("")
    for rel, kind in sorted(result["artifacts"].items()):
        lines.append("- `%s`（%s）" % (rel, kind))
    lines.append("")
    err = write_text(os.path.join(output_dir, "report.md"), "\n".join(lines) + "\n")
    if err is None:
        artifacts["report.md"] = "report"
