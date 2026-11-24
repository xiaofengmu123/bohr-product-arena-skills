#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dbutil -- read-only SQLite/CSV schema inspection, column profiling,
duplicate detection, correlation and query execution helpers.

Determinism contract: every function returns values in a fixed, documented
order; no randomness is used; floats are computed in double precision and
rounded only at the output layer. All connections are opened read-only.
"""

import csv
import math
import os
import sqlite3
from collections import Counter

MAX_SAMPLE = 10000
MAX_VALUE_COUNTS = 25


# ---------------------------------------------------------------------------
# quoting / safety
# ---------------------------------------------------------------------------
def quote_ident(name):
    """Quote a SQLite identifier safely."""
    return '"' + str(name).replace('"', '""') + '"'


def quote_sql_string(value):
    """Quote a string literal safely."""
    return "'" + str(value).replace("'", "''") + "'"


def sql_literal(value):
    """Render a Python value as a SQL literal."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    return quote_sql_string(value)


def is_read_only_sql(sql):
    """Return True only for statements that cannot modify the database."""
    head = (sql or "").lstrip().upper()
    return head.startswith(("SELECT", "WITH", "PRAGMA", "EXPLAIN", "VALUES"))


# ---------------------------------------------------------------------------
# numeric helpers
# ---------------------------------------------------------------------------
def to_number(value):
    """Convert a value to float when it represents a number, else None.

    Booleans are deliberately excluded so 0/1 flags are not silently treated
    as continuous measurements.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if s == "":
        return None
    try:
        return float(s)
    except (ValueError, OverflowError):
        return None


def _declared_numeric(decl_type):
    t = (decl_type or "").upper()
    return any(k in t for k in ("INT", "REAL", "FLOA", "NUM", "DEC",
                                "DOUB", "BIGINT", "SMALLINT", "TINYINT"))


def _declared_text(decl_type):
    t = (decl_type or "").upper()
    return any(k in t for k in ("TEXT", "CHAR", "CLOB", "VARCHAR", "STRING"))


def _percentile(sorted_vals, p):
    """Linear interpolation percentile (type 7) on an already sorted list."""
    n = len(sorted_vals)
    if n == 0:
        return None
    if n == 1:
        return sorted_vals[0]
    h = (n - 1) * p / 100.0
    lo = int(math.floor(h))
    hi = int(math.ceil(h))
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (h - lo) * (sorted_vals[hi] - sorted_vals[lo])


def pearson(xs, ys):
    """Pearson correlation coefficient; None when undefined."""
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = syy = sxy = 0.0
    for x, y in zip(xs, ys):
        dx = x - mx
        dy = y - my
        sxx += dx * dx
        syy += dy * dy
        sxy += dx * dy
    if sxx == 0.0 or syy == 0.0:
        return None
    return sxy / math.sqrt(sxx * syy)


def _round(v, nd=6):
    """Round a float for output; preserves None."""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    return round(float(v), nd)


# ---------------------------------------------------------------------------
# connection / schema
# ---------------------------------------------------------------------------
def connect_db(path):
    """Open a SQLite database read-only.

    Uses a file: URI with mode=ro so the OS refuses writes, then sets
    query_only=ON as a second SQL-level guard.
    """
    abspath = os.path.abspath(path)
    uri_path = abspath.replace("\\", "/").replace("?", "%3F").replace("#", "%23")
    uri = "file:" + uri_path + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.execute("PRAGMA query_only = ON")
    conn.row_factory = sqlite3.Row
    return conn


def connect_memory():
    """Open a fresh in-memory database for staging CSV snapshots.

    This connection is intentionally read-write so CSV rows can be loaded; the
    on-disk source files are never opened for writing.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def list_tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def table_info(conn, table):
    return [dict(r) for r in
            conn.execute("PRAGMA table_info(%s)" % quote_ident(table)).fetchall()]


def row_count(conn, table):
    row = conn.execute("SELECT COUNT(*) FROM %s" % quote_ident(table)).fetchone()
    return int(row[0])


def inspect_schema(conn):
    """Return a deterministic schema listing for every user table."""
    tables = []
    for t in list_tables(conn):
        cols = table_info(conn, t)
        tables.append({
            "table": t,
            "row_count": row_count(conn, t),
            "columns": [
                {"name": c.get("name"), "type": c.get("type"),
                 "notnull": bool(c.get("notnull")), "pk": bool(c.get("pk"))}
                for c in cols
            ],
        })
    return tables


# ---------------------------------------------------------------------------
# column profiling
# ---------------------------------------------------------------------------
def column_profile(conn, table, column, decl_type):
    tq = quote_ident(table)
    cq = quote_ident(column)
    total = row_count(conn, table)

    missing_sql = ("SELECT COUNT(*) AS missing FROM %s "
                   "WHERE %s IS NULL OR TRIM(CAST(%s AS TEXT)) = ''"
                   % (tq, cq, cq))
    missing = int(conn.execute(missing_sql).fetchone()[0])
    nonnull = total - missing

    # Deterministic sample in rowid order (no ORDER BY => stable per file).
    extraction_sql = ("SELECT %s AS value FROM %s WHERE %s IS NOT NULL LIMIT %d"
                      % (cq, tq, cq, MAX_SAMPLE))
    raw_values = [r[0] for r in conn.execute(extraction_sql).fetchall()]

    value_counts_sql = (
        "SELECT COALESCE(CAST(%s AS TEXT), '<NULL>') AS value, COUNT(*) AS n "
        "FROM %s GROUP BY 1 ORDER BY n DESC, value ASC LIMIT %d"
        % (cq, tq, MAX_VALUE_COUNTS))
    top_values = [{"value": r[0], "count": int(r[1])}
                  for r in conn.execute(value_counts_sql).fetchall()]

    n_distinct = int(conn.execute(
        "SELECT COUNT(DISTINCT %s) FROM %s WHERE %s IS NOT NULL"
        % (cq, tq, cq)).fetchone()[0])

    nums = []
    for v in raw_values:
        n = to_number(v)
        if n is not None:
            nums.append(n)
    parseable_ratio = (len(nums) / len(raw_values)) if raw_values else 0.0

    is_decl_numeric = _declared_numeric(decl_type)
    is_decl_text = _declared_text(decl_type)
    numeric = bool(nums) and (is_decl_numeric or parseable_ratio >= 0.8)
    coded_as_text = bool(is_decl_text and numeric)

    prof = {
        "name": column,
        "declared_type": decl_type or None,
        "kind": "numeric" if numeric else ("text" if raw_values else "empty"),
        "coded_as_text": coded_as_text,
        "n_total": int(total),
        "n_nonnull": int(nonnull),
        "n_missing": int(missing),
        "n_distinct": int(n_distinct),
        "extraction_sql": extraction_sql,
        "missing_sql": missing_sql,
        "value_counts_sql": value_counts_sql,
        "top_values": top_values,
    }

    if numeric:
        n = len(nums)
        mean = sum(nums) / n if n else None
        variance = 0.0
        if n > 1:
            m = sum(nums) / n
            variance = sum((x - m) ** 2 for x in nums) / (n - 1)
        stdev = math.sqrt(variance) if n > 1 else None
        ordered = sorted(nums)
        prof["numeric"] = {
            "values": nums,
            "stats": {
                "n": n,
                "mean": _round(mean),
                "stdev_sample": _round(stdev),
                "min": _round(min(nums)) if nums else None,
                "max": _round(max(nums)) if nums else None,
                "q1": _round(_percentile(ordered, 25)),
                "median": _round(_percentile(ordered, 50)),
                "q3": _round(_percentile(ordered, 75)),
            },
        }
    return prof


def aggregate_numeric(conn, table, column):
    """SQL aggregate used to cross-check Python descriptive statistics."""
    tq = quote_ident(table)
    cq = quote_ident(column)
    sql = ("SELECT COUNT(%s) AS n, AVG(%s) AS avg, MIN(%s) AS mn, MAX(%s) AS mx "
           "FROM %s" % (cq, cq, cq, cq, tq))
    try:
        row = conn.execute(sql).fetchone()
        return {
            "sql": sql,
            "n": int(row[0]) if row[0] is not None else 0,
            "avg": _round(row[1]),
            "min": _round(row[2]),
            "max": _round(row[3]),
            "error": None,
        }
    except Exception as e:  # noqa: BLE001 - record and continue
        return {"sql": sql, "n": None, "avg": None, "min": None,
                "max": None, "error": str(e)}


# ---------------------------------------------------------------------------
# duplicates
# ---------------------------------------------------------------------------
def detect_duplicates(conn, table):
    cols = [c.get("name") for c in table_info(conn, table)]
    if not cols:
        return {"n_duplicate_groups": 0, "n_rows_in_duplicate_groups": 0,
                "full_row_dup_sql": None}
    col_exprs = ", ".join(
        "COALESCE(CAST(%s AS TEXT), '<NULL>')" % quote_ident(c) for c in cols)
    sql = ("SELECT COUNT(*) AS _n, %s FROM %s "
           "GROUP BY %s HAVING COUNT(*) > 1 ORDER BY _n DESC"
           % (col_exprs, quote_ident(table), col_exprs))
    groups = conn.execute(sql).fetchall()
    n_groups = len(groups)
    n_rows_in_groups = sum(int(r[0]) for r in groups)
    return {"n_duplicate_groups": n_groups,
            "n_rows_in_duplicate_groups": n_rows_in_groups,
            "full_row_dup_sql": sql}


# ---------------------------------------------------------------------------
# query execution
# ---------------------------------------------------------------------------
def execute_query(conn, sql, max_rows=200):
    """Execute a read-only query and return rows, columns and metadata."""
    if not is_read_only_sql(sql):
        return {"rows": [], "columns": [], "row_count": None, "truncated": False,
                "error": "refused: statement is not read-only"}
    try:
        cur = conn.execute(sql)
        rows = cur.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        return {
            "rows": [dict(r) for r in rows],
            "columns": [d[0] for d in (cur.description or [])],
            "row_count": len(rows),
            "truncated": truncated,
            "error": None,
        }
    except Exception as e:  # noqa: BLE001 - record and continue
        return {"rows": [], "columns": [], "row_count": None, "truncated": False,
                "error": str(e)}


# ---------------------------------------------------------------------------
# CSV / TSV loading into an in-memory table
# ---------------------------------------------------------------------------
def _is_int(v):
    try:
        int(v)
        return True
    except (ValueError, TypeError):
        return False


def _is_float(v):
    try:
        float(v)
        return True
    except (ValueError, TypeError):
        return False


def sniff_delimiter(sample_text):
    try:
        dialect = csv.Sniffer().sniff(sample_text[:8192], delimiters=",\t;|")
        return dialect.delimiter
    except csv.Error:
        return "," if sample_text.count(",") >= sample_text.count("\t") else "\t"


def _sanitize_table_name(stem):
    out = []
    for ch in str(stem):
        if ch.isalnum() or ch == "_":
            out.append(ch)
        else:
            out.append("_")
    name = "".join(out) or "csv_data"
    if name[0].isdigit():
        name = "t_" + name
    return name


def load_csv_table(path, conn):
    """Load a CSV/TSV into an in-memory table with inferred column types.

    Returns a dict with the table name, source path, delimiter and column
    type list. Uses only the csv module + sqlite3; values are converted to
    int/float/str so schema inspection reports realistic declared types.
    """
    with open(path, "r", encoding="utf-8-sig", errors="replace",
              newline="") as f:
        head = f.read(4096)
    delim = sniff_delimiter(head)
    with open(path, "r", encoding="utf-8-sig", errors="replace",
              newline="") as f:
        reader = csv.reader(f, delimiter=delim)
        rows = list(reader)

    if not rows:
        return {"table": None, "source": path, "delimiter": delim,
                "columns": [], "n_rows": 0, "error": "empty file"}

    header = rows[0]
    body = rows[1:]
    ncols = len(header)
    header = [str(h).strip() or ("col%d" % (i + 1)) for i, h in enumerate(header)]

    types = []
    for c in range(ncols):
        vals = []
        for r in body[:200]:
            if c < len(r):
                s = (r[c] or "").strip()
                if s != "":
                    vals.append(s)
        if not vals:
            types.append("TEXT")
        elif all(_is_int(v) for v in vals):
            types.append("INTEGER")
        elif all(_is_float(v) for v in vals):
            types.append("REAL")
        else:
            types.append("TEXT")

    def convert(v, t):
        s = (v or "").strip()
        if s == "":
            return None
        if t == "INTEGER":
            try:
                return int(s)
            except ValueError:
                return s
        if t == "REAL":
            try:
                return float(s)
            except ValueError:
                return s
        return s

    table = _sanitize_table_name(os.path.splitext(os.path.basename(path))[0])
    col_defs = ", ".join("%s %s" % (quote_ident(h), t)
                         for h, t in zip(header, types))
    conn.execute("DROP TABLE IF EXISTS %s" % quote_ident(table))
    conn.execute("CREATE TABLE %s (%s)" % (quote_ident(table), col_defs))
    placeholders = ", ".join("?" for _ in range(ncols))
    insert_sql = "INSERT INTO %s VALUES (%s)" % (quote_ident(table), placeholders)
    conn.executemany(insert_sql,
                     [tuple(convert(r[c] if c < len(r) else None, types[c])
                            for c in range(ncols)) for r in body])

    return {"table": table, "source": path, "delimiter": delim,
            "columns": list(zip(header, types)), "n_rows": len(body),
            "error": None}
