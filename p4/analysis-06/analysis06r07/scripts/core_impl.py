#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl: shared pure-stdlib helpers for the read-only DB/CSV analysis skill.

Robustness-first design: every reader has a per-file failure path, text is
decoded with a multi-encoding fallback, JSON is salvaged when truncated, and
SQL is guarded so only SELECT/WITH/PRAGMA/EXPLAIN may run. No function here
raises on malformed input; callers receive (result, error) pairs instead.
"""

import csv
import json
import math
import os
import re
import sqlite3
import statistics
from pathlib import Path

VERSION = "1.0.0"
MAX_TEXT_BYTES = 2 * 1024 * 1024          # 2 MiB per text file (safety cap)
MAX_CSV_ROWS = 50000                      # per-file CSV row cap
MAX_QUERY_ROWS = 200                      # rows fetched per query
TOP_VALUES = 20                           # top-k value counts kept per column
DB_EXTS = (".db", ".sqlite", ".sqlite3", ".db3")
TAB_EXTS = (".csv", ".tsv", ".tab")
DICT_EXTS = (".md", ".yaml", ".yml", ".json", ".txt", ".csv", ".tsv")

_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030")
_MISSING_TOKENS = {"", "na", "n/a", "nan", "null", "none", "-", "?", "missing"}
_ALLOWED_SQL = re.compile(r"^\s*(SELECT|WITH|PRAGMA|EXPLAIN)\b", re.IGNORECASE)
_DANGEROUS_SQL = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|VACUUM|"
    r"REINDEX|REPLACE|COMMIT|ROLLBACK|BEGIN|SAVEPOINT|RELEASE)\b",
    re.IGNORECASE)


# --------------------------------------------------------------------------- #
# deterministic number helpers
# --------------------------------------------------------------------------- #
def round6(x):
    """Round a float to 6 decimals for reproducible JSON/CSV output."""
    if x is None:
        return None
    if isinstance(x, bool):
        return int(x)
    if isinstance(x, int):
        return x
    try:
        return round(float(x), 6)
    except (TypeError, ValueError, OverflowError):
        return None


def is_missing(v):
    """Treat None, NaN, blank and common NA tokens as missing."""
    if v is None:
        return True
    if isinstance(v, float):
        return math.isnan(v)
    if isinstance(v, str):
        s = v.strip()
        return s == "" or s.lower() in _MISSING_TOKENS
    return False


def is_num(v):
    """True for int, non-NaN float, or numeric-looking text (coded-as-text)."""
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    if isinstance(v, float):
        return not math.isnan(v)
    if isinstance(v, str):
        s = v.strip()
        if s == "" or s.lower() in _MISSING_TOKENS:
            return False
        try:
            float(s)
            return True
        except (ValueError, OverflowError):
            return False
    return False


def to_float(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return float(v)
    if isinstance(v, float):
        return None if math.isnan(v) else v
    if isinstance(v, str):
        try:
            return float(v.strip())
        except (ValueError, OverflowError):
            return None
    return None


def numeric_stats(nums):
    """Compute reproducible univariate stats for a list of numbers."""
    n = len(nums)
    if n == 0:
        return {"n": 0, "mean": None, "stdev_sample": None, "min": None,
                "max": None, "median": None, "q1": None, "q3": None, "sum": None}
    sn = sorted(nums)
    mean = statistics.fmean(nums)
    stdev = statistics.stdev(nums) if n >= 2 else None

    def pct(q):
        k = (n - 1) * q
        lo = int(math.floor(k))
        hi = int(math.ceil(k))
        if lo == hi:
            return sn[lo]
        return sn[lo] + (sn[hi] - sn[lo]) * (k - lo)

    total = sum(nums)
    if all(isinstance(x, int) and not isinstance(x, bool) for x in nums):
        total_out = int(total)
        mn, mx = min(nums), max(nums)
    else:
        total_out = round6(total)
        mn, mx = min(nums), max(nums)
    return {
        "n": n,
        "mean": round6(mean),
        "stdev_sample": round6(stdev),
        "min": mn,
        "max": mx,
        "median": round6(statistics.median(sn)),
        "q1": round6(pct(0.25)),
        "q3": round6(pct(0.75)),
        "sum": total_out,
    }


def pearson(xs, ys):
    """Pairwise-complete Pearson correlation; None when undefined."""
    pairs = [(x, y) for x, y in zip(xs, ys)
             if x is not None and y is not None
             and not is_missing(x) and not is_missing(y)]
    n = len(pairs)
    if n < 2:
        return None
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    cov = sum((p[0] - mx) * (p[1] - my) for p in pairs)
    vx = sum((p[0] - mx) ** 2 for p in pairs)
    vy = sum((p[1] - my) ** 2 for p in pairs)
    if vx <= 0 or vy <= 0:
        return None
    return round6(cov / math.sqrt(vx * vy))


def correlate_table(numeric_cols):
    """numeric_cols: [{'name', 'values'}]. Returns {columns, matrix}."""
    names = [c["name"] for c in numeric_cols]
    n = len(names)
    matrix = [[None] * n for _ in range(n)]
    for i in range(n):
        matrix[i][i] = 1.0
        for j in range(i + 1, n):
            r = pearson(numeric_cols[i]["values"], numeric_cols[j]["values"])
            matrix[i][j] = r
            matrix[j][i] = r
    return {"columns": names, "matrix": matrix}


# --------------------------------------------------------------------------- #
# robust readers
# --------------------------------------------------------------------------- #
def read_text_robust(path, max_bytes=MAX_TEXT_BYTES):
    """Read text with multi-encoding fallback.

    Returns (text, encoding, truncated, error, degraded). Never raises.
    """
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, None, False, "stat failed: %s" % e, False
    truncated = size > max_bytes
    try:
        with open(path, "rb") as f:
            data = f.read(max_bytes)
    except OSError as e:
        return None, None, truncated, "read failed: %s" % e, False
    for enc in _ENCODINGS:
        try:
            return data.decode(enc), enc, truncated, None, False
        except (UnicodeDecodeError, LookupError):
            continue
    # latin-1 never fails; mark as degraded so it is surfaced honestly.
    return data.decode("latin-1", errors="replace"), "latin-1", truncated, None, True


def load_json_robust(path, max_bytes=MAX_TEXT_BYTES):
    """Parse JSON, salvaging a truncated prefix with raw_decode when possible.

    Returns (obj_or_None, error_or_None, degraded). Never raises.
    """
    text, enc, truncated, err, degraded = read_text_robust(path, max_bytes)
    if err is not None and text is None:
        return None, err, False
    if text is None:
        return None, "no content", False
    s = text.lstrip("\ufeff").strip()
    if not s:
        return None, "empty file", False
    try:
        return json.loads(s), None, degraded or truncated
    except (json.JSONDecodeError, ValueError) as e:
        try:
            obj, _ = json.JSONDecoder().raw_decode(s)
            return obj, "JSON truncated or malformed; salvaged prefix: %s" % e, True
        except Exception:
            return None, "JSON parse error: %s" % e, True


def discover_inputs(input_dir):
    """Classify files under input_dir. Returns (inputs_dict, error_or_None)."""
    root = Path(input_dir)
    result = {
        "case_json": None, "task_md": None, "dbs": [], "tabs": [],
        "dictionaries": [], "other": [], "resource_yaml": None,
    }
    if not root.is_dir():
        return result, "input dir not found: %s" % input_dir
    try:
        entries = sorted(root.rglob("*"), key=lambda p: str(p).lower())
    except OSError as e:
        return result, "cannot list input dir: %s" % e
    for p in entries:
        if not p.is_file():
            continue
        name = p.name.lower()
        ext = p.suffix.lower()
        if name == "case.json":
            result["case_json"] = result["case_json"] or str(p)
        elif name == "task.md":
            result["task_md"] = result["task_md"] or str(p)
        elif ext in DB_EXTS:
            result["dbs"].append(str(p))
        elif ext in TAB_EXTS and name != "case.json":
            result["tabs"].append(str(p))
        elif ext in DICT_EXTS and name not in ("case.json", "task.md"):
            result["dictionaries"].append(str(p))
        elif name in ("resources.yaml", "resources.yml"):
            result["resource_yaml"] = result["resource_yaml"] or str(p)
        else:
            result["other"].append(str(p))
    return result, None


# --------------------------------------------------------------------------- #
# SQLite helpers
# --------------------------------------------------------------------------- #
def safe_open_sqlite(path):
    """Open a SQLite file strictly read-only. Returns (conn, error_or_None)."""
    try:
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=3000")
        # Probe: a non-DB file raises DatabaseError on first real query.
        conn.execute("SELECT count(*) FROM sqlite_master")
        return conn, None
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        return None, "%s: %s" % (type(e).__name__, e)


def quote_ident(name):
    return '"' + str(name).replace('"', '""') + '"'


def inspect_schema(conn):
    """Return ([{table, kind, row_count, columns:[...]}], error_or_None)."""
    tables = []
    try:
        rows = conn.execute(
            "SELECT name, type FROM sqlite_master "
            "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name").fetchall()
    except Exception as e:
        return [], "schema read failed: %s: %s" % (type(e).__name__, e)
    for name, typ in rows:
        cols = []
        try:
            info = conn.execute("PRAGMA table_info(%s)" % quote_ident(name)).fetchall()
            for row in info:
                cid, cname, ctype, notnull, dflt, pk = row
                cols.append({"cid": cid, "name": cname, "type": ctype,
                             "notnull": bool(notnull), "pk": bool(pk)})
            cnt = conn.execute("SELECT COUNT(*) FROM %s" % quote_ident(name)).fetchone()[0]
        except Exception as e:
            cnt = None
            cols = [{"error": "%s: %s" % (type(e).__name__, e)}]
        tables.append({"table": name, "kind": typ, "row_count": cnt, "columns": cols})
    return tables, None


def detect_duplicates(conn, table, columns):
    """Count exact full-row duplicate groups (NULLs treated as equal)."""
    names = [c["name"] for c in columns if c.get("name")]
    if not names:
        return {"n_duplicate_groups": 0, "n_rows_in_duplicate_groups": 0,
                "full_row_dup_sql": None}
    cols = ", ".join(quote_ident(n) for n in names)
    t = quote_ident(table)
    sql = ("SELECT %s, COUNT(*) AS dup_cnt FROM %s "
           "GROUP BY %s HAVING COUNT(*) > 1" % (cols, t, cols))
    try:
        rows = conn.execute(sql).fetchall()
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, e), "full_row_dup_sql": sql}
    n_groups = len(rows)
    n_rows_in_groups = sum(r[-1] for r in rows)
    return {"n_duplicate_groups": n_groups,
            "n_rows_in_duplicate_groups": n_rows_in_groups,
            "full_row_dup_sql": sql}


def column_profile(conn, table, col_name, col_type, top_values=TOP_VALUES):
    """Profile one column; includes SQL/Python cross-check for numeric columns."""
    t = quote_ident(table)
    c = quote_ident(col_name)
    prof = {"column": col_name, "declared_type": col_type, "kind": "unreadable"}
    extraction_sql = "SELECT %s FROM %s" % (c, t)
    prof["extraction_sql"] = extraction_sql
    try:
        vals = [r[0] for r in conn.execute(extraction_sql).fetchall()]
    except Exception as e:
        prof["error"] = "%s: %s" % (type(e).__name__, e)
        return prof
    n = len(vals)
    nonnull = [v for v in vals if not is_missing(v)]
    n_missing = n - len(nonnull)
    prof["n"] = n
    prof["n_nonnull"] = len(nonnull)
    prof["n_missing"] = n_missing
    prof["missing_sql"] = (
        "SELECT COUNT(*) FROM %s WHERE %s IS NULL "
        "OR (%s IS NOT NULL AND (typeof(%s)='text' AND trim(%s)=''))"
        % (t, c, c, c, c))
    counts = {}
    for v in nonnull:
        key = v if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)
        counts[key] = counts.get(key, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))
    prof["distinct_count"] = len(counts)
    prof["top_values"] = [{"value": k, "count": v} for k, v in ordered[:top_values]]
    prof["value_counts_sql"] = (
        "SELECT %s, COUNT(*) AS n FROM %s GROUP BY %s "
        "ORDER BY COUNT(*) DESC, %s ASC LIMIT %d" % (c, t, c, c, top_values))

    if nonnull and all(is_num(v) for v in nonnull):
        nums = [to_float(v) for v in nonnull]
        nums = [x for x in nums if x is not None]
        prof["kind"] = "numeric"
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                   for v in nonnull):
            prof["coded_as_text"] = True
        prof["numeric"] = numeric_stats(nums)
        prof["numeric"]["values"] = nums
        agg_sql = ("SELECT COUNT(*), AVG(%s), MIN(%s), MAX(%s), SUM(%s) FROM %s"
                   % (c, c, c, c, t))
        try:
            row = conn.execute(agg_sql).fetchone()
            py_mean = prof["numeric"]["mean"]
            matches = (row[1] is None and py_mean is None) or (
                row[1] is not None and py_mean is not None
                and abs(float(row[1]) - float(py_mean)) < 1e-9)
            prof["aggregate"] = {"sql": agg_sql, "n": row[0], "avg": round6(row[1]),
                                 "min": row[2], "max": row[3], "sum": round6(row[4]),
                                 "matches_python": bool(matches)}
        except Exception as e:
            prof["aggregate"] = {"sql": agg_sql,
                                 "error": "%s: %s" % (type(e).__name__, e)}
    else:
        prof["kind"] = "text"
    return prof


def guard_sql(sql):
    """Validate a read-only SQL string. Returns (ok, error_or_None)."""
    if not isinstance(sql, str):
        return False, "SQL must be a string"
    s = sql.strip()
    if not s:
        return False, "empty SQL"
    if not _ALLOWED_SQL.match(s):
        return False, "only SELECT/WITH/PRAGMA/EXPLAIN statements are allowed"
    body = s[:-1].strip() if s.endswith(";") else s
    if ";" in body:
        return False, "multiple statements are not allowed"
    if _DANGEROUS_SQL.match(body):
        return False, "write/destructive statement blocked"
    return True, None


def run_safe_query(conn, sql, max_rows=MAX_QUERY_ROWS):
    """Run a guarded read-only query. Returns (cols, rows, n, err, status)."""
    ok, err = guard_sql(sql)
    if not ok:
        return None, None, 0, err, "blocked"
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        status = "truncated" if truncated else "ok"
        return cols, [list(r) for r in rows], len(rows), None, status
    except Exception as e:
        return None, None, 0, "%s: %s" % (type(e).__name__, e), "error"


# --------------------------------------------------------------------------- #
# CSV/TSV analysis (no external deps)
# --------------------------------------------------------------------------- #
def analyze_tabular(path):
    """Read CSV/TSV and return a schema-like profile dict. Never raises."""
    result = {"file": path, "kind": "csv", "truncated_rows": False, "error": None}
    try:
        with open(path, "rb") as f:
            head = f.read(8192)
    except OSError as e:
        result["error"] = "read failed: %s" % e
        return result
    try:
        sample = head.decode("utf-8-sig", errors="replace")
    except Exception:
        sample = head.decode("latin-1", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delim = dialect.delimiter
    except Exception:
        delim = "\t" if Path(path).suffix.lower() in (".tsv", ".tab") else ","
    rows = []
    header = None
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
            reader = csv.reader(f, delimiter=delim)
            header = next(reader)
            for i, r in enumerate(reader):
                if i >= MAX_CSV_ROWS:
                    result["truncated_rows"] = True
                    break
                rows.append(r)
    except Exception as e:
        result["error"] = "parse failed: %s" % e
        return result
    if header is None:
        result["error"] = "empty file"
        return result
    header = [h.strip() if isinstance(h, str) else str(h) for h in header]
    result["header"] = header
    result["row_count"] = len(rows)
    result["delimiter"] = repr(delim)
    columns = []
    for idx, name in enumerate(header):
        col_vals = []
        for r in rows:
            if idx < len(r):
                col_vals.append(r[idx])
            else:
                col_vals.append(None)
        nonnull = [v for v in col_vals if not is_missing(v)]
        if nonnull and all(is_num(v) for v in nonnull):
            nums = [x for x in (to_float(v) for v in nonnull) if x is not None]
            columns.append({"name": name, "kind": "numeric", "n_nonnull": len(nonnull),
                            "n_missing": len(col_vals) - len(nonnull),
                            "numeric": numeric_stats(nums),
                            "coded_as_text": not all(
                                isinstance(v, (int, float)) and not isinstance(v, bool)
                                for v in nonnull)})
        else:
            counts = {}
            for v in nonnull:
                key = v if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)
                counts[key] = counts.get(key, 0) + 1
            ordered = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))
            columns.append({"name": name, "kind": "text", "n_nonnull": len(nonnull),
                            "n_missing": len(col_vals) - len(nonnull),
                            "distinct_count": len(counts),
                            "top_values": [{"value": k, "count": v}
                                           for k, v in ordered[:TOP_VALUES]]})
    result["columns"] = columns
    return result


# --------------------------------------------------------------------------- #
# SVG charts (editable, parseable)
# --------------------------------------------------------------------------- #
def xml_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;"))


def _fmt(v):
    if v is None:
        return "NA"
    if isinstance(v, float):
        return ("%.6g" % v)
    return str(v)


def render_bar_svg(title, labels, values):
    n = len(labels)
    if n == 0:
        return None
    w, h = 640, 320
    mx = max(values) if values else 0
    if mx <= 0:
        mx = 1
    bw = (w - 90) / max(n, 1)
    parts = ['<text x="20" y="24" font-size="14" font-weight="bold">%s</text>'
             % xml_escape(title)]
    for i, (lab, v) in enumerate(zip(labels, values)):
        bh = (v / mx) * (h - 110)
        x = 70 + i * bw
        y = h - 50 - bh
        parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                     'fill="#3b82f6"/>' % (x, y, bw - 2, bh))
        parts.append('<text x="%.1f" y="%.1f" font-size="9" text-anchor="middle">%s</text>'
                     % (x + (bw - 2) / 2, y - 4, xml_escape(_fmt(v))))
        parts.append('<text x="%.1f" y="%d" font-size="9" text-anchor="end" '
                     'transform="rotate(-40 %.1f %d)">%s</text>'
                     % (x + (bw - 2) / 2, h - 30, x + (bw - 2) / 2, h - 30,
                        xml_escape(str(lab)[:24])))
    svg = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
           'viewBox="0 0 %d %d">\n%s\n</svg>\n') % (w, h, w, h, "\n".join(parts))
    return svg


def render_hist_svg(title, values, bins=10):
    vals = [v for v in values if v is not None]
    n = len(vals)
    if n == 0:
        return None
    lo, hi = min(vals), max(vals)
    if lo == hi:
        return render_bar_svg(title + " (all values = %s)" % _fmt(lo),
                              [_fmt(lo)], [n])
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in vals:
        idx = int((v - lo) / width)
        if idx >= bins:
            idx = bins - 1
        counts[idx] += 1
    labels = ["%.6g" % (lo + (i + 0.5) * width) for i in range(bins)]
    return render_bar_svg(title, labels, counts)


def render_heatmap_svg(title, labels, matrix):
    n = len(labels)
    if n < 2:
        return None
    cell = 48
    pad = 150
    size = pad + n * cell
    parts = ['<text x="20" y="24" font-size="14" font-weight="bold">%s</text>'
             % xml_escape(title)]
    for i in range(n):
        for j in range(n):
            r = matrix[i][j]
            if r is None:
                color = "#e5e7eb"
                txt = "NA"
            else:
                v = max(-1.0, min(1.0, float(r)))
                color = "#2563eb" if v >= 0 else "#dc2626"
                opacity = abs(v)
                color = _rgba(color, opacity)
                txt = "%.3f" % r
            x = pad + j * cell
            y = 60 + i * cell
            parts.append('<rect x="%d" y="%d" width="%d" height="%d" fill="%s" '
                         'stroke="#ffffff"/>' % (x, y, cell, cell, color))
            parts.append('<text x="%d" y="%d" font-size="9" text-anchor="middle" '
                         'dominant-baseline="middle">%s</text>'
                         % (x + cell / 2, y + cell / 2, xml_escape(txt)))
    for i in range(n):
        parts.append('<text x="%d" y="%d" font-size="9" text-anchor="end" '
                     'dominant-baseline="middle">%s</text>'
                     % (pad - 6, 60 + i * cell + cell / 2,
                        xml_escape(str(labels[i])[:20])))
    svg = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
           'viewBox="0 0 %d %d">\n%s\n</svg>\n') % (size, 60 + n * cell + 40,
                                                     size, 60 + n * cell + 40,
                                                     "\n".join(parts))
    return svg


def _rgba(hexcolor, opacity):
    opacity = max(0.0, min(1.0, opacity))
    r = int(hexcolor[1:3], 16)
    g = int(hexcolor[3:5], 16)
    b = int(hexcolor[5:7], 16)
    return "rgba(%d,%d,%d,%.2f)" % (r, g, b, opacity)


# --------------------------------------------------------------------------- #
# deterministic file writers
# --------------------------------------------------------------------------- #
def atomic_write(path, text):
    """Write text via temp file + os.replace so a crash never leaves a torn file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, p)


def atomic_write_json(path, obj):
    atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False))


def write_csv_file(path, headers, rows):
    """Deterministic CSV writer (UTF-8, CRLF-free)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(list(headers))
        for r in rows:
            w.writerow(list(r))


def sanitize(name):
    return re.sub(r"[^0-9A-Za-z_]+", "_", str(name)).strip("_") or "x"


# --------------------------------------------------------------------------- #
# output renderers (operate on a duck-typed `res` object)
# --------------------------------------------------------------------------- #
def make_charts(res, table, numeric_cols, profiles):
    figs = res.output_dir / "figures"
    try:
        figs.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    safe = sanitize(table)
    for nc in numeric_cols[:2]:
        svg = render_hist_svg("%s.%s distribution" % (table, nc["name"]),
                              nc["values"])
        if svg:
            fname = "hist_%s_%s.svg" % (safe, sanitize(nc["name"]))
            try:
                atomic_write(figs / fname, svg)
                res.artifacts["figures/" + fname] = "chart"
                res.analysis["figures"].append(
                    {"file": "figures/" + fname, "kind": "histogram",
                     "table": table, "column": nc["name"]})
            except OSError as e:
                res.degradations.append({"item": "chart:" + fname, "detail": str(e)})
    text_profiles = [p for p in profiles if p.get("kind") == "text"
                     and p.get("top_values")
                     and (p.get("distinct_count") or 0) <= 10]
    for p in text_profiles[:2]:
        labels = [tv["value"] for tv in p["top_values"]]
        vals = [tv["count"] for tv in p["top_values"]]
        svg = render_bar_svg("%s.%s value counts" % (table, p["column"]),
                             labels, vals)
        if svg:
            fname = "bar_%s_%s.svg" % (safe, sanitize(p["column"]))
            try:
                atomic_write(figs / fname, svg)
                res.artifacts["figures/" + fname] = "chart"
                res.analysis["figures"].append(
                    {"file": "figures/" + fname, "kind": "bar",
                     "table": table, "column": p["column"]})
            except OSError as e:
                res.degradations.append({"item": "chart:" + fname, "detail": str(e)})
    if len(numeric_cols) >= 2:
        corr = correlate_table(numeric_cols)
        svg = render_heatmap_svg("%s Pearson correlation" % table,
                                 corr["columns"], corr["matrix"])
        if svg:
            fname = "corr_%s.svg" % safe
            try:
                atomic_write(figs / fname, svg)
                res.artifacts["figures/" + fname] = "chart"
                res.analysis["figures"].append(
                    {"file": "figures/" + fname, "kind": "heatmap",
                     "table": table})
            except OSError as e:
                res.degradations.append({"item": "chart:" + fname, "detail": str(e)})


def write_artifact_csvs(res):
    out = res.output_dir
    try:
        schema_rows = []
        for tset in res.analysis.get("tables", []):
            for t in tset.get("tables", []):
                for c in t.get("columns", []):
                    schema_rows.append([tset.get("database", ""), t.get("table", ""),
                                        c.get("name"), c.get("declared_type"),
                                        c.get("kind"), c.get("n_nonnull"),
                                        c.get("n_missing"), c.get("distinct_count"),
                                        "yes" if c.get("coded_as_text") else "no"])
        write_csv_file(out / "schema.csv",
                       ["database", "table", "column", "declared_type", "kind",
                        "n_nonnull", "n_missing", "distinct_count",
                        "coded_as_text"], schema_rows)
        res.artifacts["schema.csv"] = "table"
        atomic_write_json(out / "schema.json", res.analysis.get("tables", []))
        res.artifacts["schema.json"] = "table"

        stats_rows = []
        for tset in res.analysis.get("tables", []):
            for t in tset.get("tables", []):
                for c in t.get("columns", []):
                    num = c.get("numeric") or {}
                    stats_rows.append([tset.get("database", ""), t.get("table", ""),
                                       c.get("name"), num.get("n"),
                                       num.get("mean"), num.get("stdev_sample"),
                                       num.get("min"), num.get("max"),
                                       num.get("median"), num.get("q1"),
                                       num.get("q3"), num.get("sum")])
        write_csv_file(out / "summary_stats.csv",
                       ["database", "table", "column", "n", "mean",
                        "stdev_sample", "min", "max", "median", "q1", "q3", "sum"],
                       stats_rows)
        res.artifacts["summary_stats.csv"] = "table"

        dq_rows = []
        for d in res.analysis.get("data_quality", []):
            dq_rows.append([d.get("database", ""), d.get("table", ""),
                            d.get("column", ""), d.get("missing"),
                            d.get("nonnull"), d.get("distinct"),
                            d.get("duplicate_groups", ""),
                            d.get("duplicate_rows", "")])
        write_csv_file(out / "data_quality.csv",
                       ["database", "table", "column", "missing", "nonnull",
                        "distinct", "duplicate_groups", "duplicate_rows"], dq_rows)
        res.artifacts["data_quality.csv"] = "table"

        corr_rows = []
        for c in res.analysis.get("correlation", []):
            cols = c.get("columns", [])
            mat = c.get("matrix", [])
            for i, col_i in enumerate(cols):
                for j in range(i + 1, len(cols)):
                    corr_rows.append([c.get("database", ""), c.get("table", ""),
                                      col_i, cols[j],
                                      mat[i][j] if i < len(mat) and j < len(mat[i])
                                      else None])
        write_csv_file(out / "correlation_matrix.csv",
                       ["database", "table", "column_a", "column_b", "pearson_r"],
                       corr_rows)
        res.artifacts["correlation_matrix.csv"] = "table"

        ql_rows = []
        for q in res.query_log:
            summary = q.get("summary")
            summary_s = json.dumps(summary, ensure_ascii=False) if summary else ""
            ql_rows.append([q.get("id"), q.get("sql"), q.get("purpose"),
                            q.get("row_count"), q.get("status"),
                            q.get("error", ""), summary_s])
        write_csv_file(out / "query_log.csv",
                       ["id", "sql", "purpose", "row_count", "status",
                        "error", "summary"], ql_rows)
        res.artifacts["query_log.csv"] = "table"

        vc = {}
        for tset in res.analysis.get("tables", []):
            for t in tset.get("tables", []):
                for c in t.get("columns", []):
                    key = "%s.%s" % (t.get("table", ""), c.get("name"))
                    vc[key] = c.get("top_values", [])
        atomic_write_json(out / "value_counts.json", vc)
        res.artifacts["value_counts.json"] = "table"

        ev = []
        for s in res.sources:
            ev.append({"locator": s.get("locator", ""), "quote": s.get("quote", ""),
                       "role": s.get("role", ""), "claim": s.get("claim", "")})
        atomic_write_json(out / "evidence_map.json", ev)
        res.artifacts["evidence_map.json"] = "evidence"
    except OSError as e:
        res.errors.append({"item": "artifact_csvs", "detail": str(e)})


def write_report(res):
    lines = ["# 科研数据库查询与分析报告", "", "## 概览", "",
             "- case_id: %s" % res.case_id,
             "- status: %s" % res.status,
             "- 数据库: %d 个；表格文件: %d 个"
             % (len(res.analysis.get("databases", [])),
                len(res.analysis.get("tabs", []))), ""]
    for tset in res.analysis.get("tables", []):
        for t in tset.get("tables", []):
            lines += ["## 表 %s" % t.get("table"), "",
                      "- 行数: %s；列数: %d" % (t.get("row_count"),
                                                len(t.get("columns", [])))]
            for c in t.get("columns", []):
                num = c.get("numeric") or {}
                extra = ""
                if num.get("n") is not None:
                    extra = " mean=%s stdev=%s min=%s max=%s" % (
                        num.get("mean"), num.get("stdev_sample"),
                        num.get("min"), num.get("max"))
                lines.append("- %s (%s) kind=%s nonnull=%s missing=%s "
                             "distinct=%s%s%s" % (
                                 c.get("name"), c.get("declared_type"), c.get("kind"),
                                 c.get("n_nonnull"), c.get("n_missing"),
                                 c.get("distinct_count"),
                                 " coded_as_text" if c.get("coded_as_text") else "",
                                 extra))
            lines.append("")
    if res.errors:
        lines += ["## 错误", ""]
        lines += ["- %s: %s" % (e.get("item"), e.get("detail")) for e in res.errors]
        lines.append("")
    if res.degradations:
        lines += ["## 降级", ""]
        lines += ["- %s: %s" % (d.get("item"), d.get("detail"))
                  for d in res.degradations]
        lines.append("")
    if res.limitations:
        lines += ["## 限制", ""]
        lines += ["- %s" % l for l in res.limitations]
        lines.append("")
    if res.pending_human:
        lines += ["## 待人工确认", ""]
        lines += ["- %s" % p for p in res.pending_human]
        lines.append("")
    text = "\n".join(lines)
    try:
        atomic_write(res.output_dir / "report.md", text)
        res.artifacts["report.md"] = "report"
    except OSError as e:
        res.errors.append({"item": "report", "detail": str(e)})
