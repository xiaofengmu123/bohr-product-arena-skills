#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only data-access utilities (Python standard library only).

SQLite files are opened with URI ``mode=ro``; CSV/TSV files are imported into a
private in-memory SQLite database so DB and CSV snapshots share one query
engine and one profiling path. All outputs are deterministic: fixed ordering,
no random state, no cached answers.
"""

import csv
import io
import json
import math
import re
import sqlite3
import statistics
from pathlib import Path

TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")
MAX_TEXT_BYTES = 2_000_000
DB_EXTS = {".db", ".sqlite", ".sqlite3", ".db3", ".s3db"}
CSV_EXTS = {".csv", ".tsv"}
NUMERIC_DECL = ("int", "real", "float", "double", "numeric", "decimal", "number")
ALLOWED_FIRST = {"select", "with", "pragma", "explain"}


# --------------------------------------------------------------------------- #
# text / json helpers
# --------------------------------------------------------------------------- #
def read_text(path, max_bytes=MAX_TEXT_BYTES):
    """Read a UTF-8-family text file tolerating common encodings."""
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError:
        return None
    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
    for enc in TEXT_ENCODINGS:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def load_json(path):
    text = read_text(path)
    if text is None:
        return None, "unreadable"
    try:
        return json.loads(text), None
    except ValueError as exc:
        return None, "bad_json:%s" % exc


def jsonable(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
    return value


# --------------------------------------------------------------------------- #
# input discovery (never assume fixed names)
# --------------------------------------------------------------------------- #
def discover(input_dir):
    root = Path(input_dir)
    found = {"case_json": None, "task_md": None, "dbs": [], "csvs": [],
             "dicts": [], "others": []}
    if not root.is_dir():
        return found
    files = [p for p in sorted(root.rglob("*")) if p.is_file()]
    for p in files:
        name = p.name.lower()
        if name == "case.json":
            found["case_json"] = found["case_json"] or p
        elif name == "task.md":
            found["task_md"] = found["task_md"] or p
        elif p.suffix.lower() in DB_EXTS:
            found["dbs"].append(p)
        elif p.suffix.lower() in CSV_EXTS:
            found["csvs"].append(p)
        elif p.suffix.lower() in (".yaml", ".yml") or (
                p.suffix.lower() == ".md" and ("dict" in name or "字段" in name)):
            found["dicts"].append(p)
        else:
            found["others"].append(p)
    if found["case_json"] is None:
        for p in files:
            if p.suffix.lower() == ".json":
                found["case_json"] = p
                break
    if found["task_md"] is None:
        for p in files:
            if p.suffix.lower() == ".md" and p not in found["dicts"]:
                found["task_md"] = p
                break
    return found


# --------------------------------------------------------------------------- #
# source opening (read-only) and schema inspection
# --------------------------------------------------------------------------- #
def _table_names(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    return [r[0] for r in rows]


def _quote(ident):
    return '"' + str(ident).replace('"', '""') + '"'


def _identifier(text):
    text = re.sub(r"[^0-9A-Za-z_]", "_", str(text)).strip("_")
    if not text:
        text = "col"
    if text[0].isdigit():
        text = "c" + text
    return text


def _sniff_delimiter(sample, path):
    if Path(path).suffix.lower() == ".tsv":
        return "\t"
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;").delimiter
    except csv.Error:
        return ","


def _infer_kind(vals):
    saw_int = saw_float = False
    for v in vals:
        if v is None:
            continue
        s = str(v).strip()
        if s == "":
            continue
        if re.fullmatch(r"[+-]?\d+", s):
            saw_int = True
        else:
            try:
                float(s)
                saw_float = True
            except ValueError:
                return "text"
    if saw_float:
        return "float"
    if saw_int:
        return "int"
    return "text"


def _load_csv_table(conn, path):
    text = read_text(path)
    if text is None:
        raise ValueError("unreadable csv")
    delim = _sniff_delimiter(text[:8192], path)
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    if not rows:
        raise ValueError("empty csv")
    header = [str(h).strip() or ("col%d" % (i + 1)) for i, h in enumerate(rows[0])]
    data = rows[1:]
    while data and not any((c or "").strip() for c in data[-1]):
        data.pop()

    seen = {}
    cols = []
    for i, h in enumerate(header):
        base = _identifier(h)
        if base in seen:
            seen[base] += 1
            base = "%s_%d" % (base, seen[base])
        else:
            seen[base] = 1
        cols.append((base, h))
    orig = {base: h for base, h in cols}

    ncols = len(cols)
    kinds = [_infer_kind([row[j] for row in data if j < len(row)])
             for j in range(ncols)]
    decl = {"int": "INTEGER", "float": "REAL"}.get
    table = _identifier(Path(path).stem)
    create = "CREATE TABLE %s (%s)" % (
        _quote(table),
        ", ".join("%s %s" % (_quote(c), decl(k, "TEXT")) for (c, _), k in zip(cols, kinds)))
    conn.execute(create)
    for row in data:
        cells = []
        for j in range(ncols):
            raw = row[j] if j < len(row) else ""
            s = (raw or "").strip()
            if s == "":
                cells.append(None)
            elif kinds[j] == "int":
                try:
                    cells.append(int(float(s)))
                except ValueError:
                    cells.append(s)
            elif kinds[j] == "float":
                try:
                    cells.append(float(s))
                except ValueError:
                    cells.append(s)
            else:
                cells.append(s)
        conn.execute("INSERT INTO %s VALUES (%s)" % (
            _quote(table), ", ".join("?" for _ in range(ncols))), cells)
    conn.commit()
    return table, orig


def open_tables(found, errors, degradations):
    """Return one entry per (connection, table)."""
    tables = []
    for path in found["dbs"]:
        label = "db:" + Path(path).name
        try:
            conn = sqlite3.connect("file:%s?mode=ro" % Path(path).as_posix(),
                                   uri=True)
            conn.execute("PRAGMA query_only=ON")
        except sqlite3.Error as exc:
            errors.append({"item": label, "detail": "open_failed:%s" % exc})
            continue
        names = _table_names(conn)
        if not names:
            degradations.append({"item": label, "detail": "no_tables"})
            conn.close()
            continue
        for t in names:
            tables.append({"label": "%s#%s" % (label, t), "kind": "sqlite",
                           "path": str(path), "conn": conn, "table": t,
                           "orig": {}})
    for path in found["csvs"]:
        label = "csv:" + Path(path).name
        try:
            conn = sqlite3.connect(":memory:")
            table, orig = _load_csv_table(conn, path)
            conn.execute("PRAGMA query_only=ON")
            tables.append({"label": label, "kind": "csv", "path": str(path),
                           "conn": conn, "table": table, "orig": orig})
        except Exception as exc:
            errors.append({"item": label, "detail": "csv_load_failed:%s" % exc})
    return tables


def inspect_schema(tables):
    schema = []
    for t in tables:
        conn = t["conn"]
        try:
            info = conn.execute("PRAGMA table_info(%s)" % _quote(t["table"])).fetchall()
            n = conn.execute("SELECT COUNT(*) FROM %s" % _quote(t["table"])).fetchone()[0]
        except sqlite3.Error:
            continue
        columns = []
        for row in info:
            columns.append({
                "name": row[1], "type": row[2], "notnull": bool(row[3]),
                "pk": row[5], "original": t["orig"].get(row[1], row[1]),
            })
        schema.append({
            "label": t["label"], "kind": t["kind"], "path": t["path"],
            "table": t["table"], "row_count": n, "columns": columns,
        })
    return schema


# --------------------------------------------------------------------------- #
# column profiling / statistics
# --------------------------------------------------------------------------- #
def _numeric_values(vals, decl_type):
    dt = (decl_type or "").lower()
    declared = any(k in dt for k in NUMERIC_DECL)
    nums = []
    all_str = True
    for v in vals:
        if isinstance(v, bool):
            return None, False
        if isinstance(v, (int, float)):
            nums.append(float(v))
            all_str = False
        elif isinstance(v, str):
            s = v.strip()
            try:
                nums.append(float(s))
            except ValueError:
                return None, False
        else:
            return None, False
    if not nums:
        return None, False
    return nums, (all_str and not declared)


def _describe(nums):
    n = len(nums)
    mean = statistics.mean(nums)
    stdev = statistics.stdev(nums) if n >= 2 else None
    median = statistics.median(nums) if n >= 2 else (nums[0] if n == 1 else None)
    q1 = q3 = iqr = lo = hi = None
    outliers = []
    if n >= 4:
        qs = statistics.quantiles(nums, n=4, method="inclusive")
        q1, q3 = qs[0], qs[2]
        iqr = q3 - q1
        if iqr > 0:
            lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            outliers = [x for x in nums if x < lo or x > hi]
    return {
        "n": n, "mean": mean, "stdev_sample": stdev, "min": min(nums),
        "max": max(nums), "median": median, "q1": q1, "q3": q3,
        "iqr": iqr, "lower_fence": lo, "upper_fence": hi,
        "n_outliers_iqr": len(outliers),
    }


def _sql_aggregate(conn, table, col, coded):
    qcol = _quote(col)
    qtab = _quote(table)
    expr = ("CAST(%s AS REAL)" % qcol) if coded else qcol
    where = "WHERE %s IS NOT NULL AND TRIM(%s) <> ''" % (qcol, qcol)
    sql = ("SELECT COUNT(%s), AVG(%s), MIN(%s), MAX(%s) FROM %s %s"
           % (expr, expr, expr, expr, qtab, where))
    try:
        n, avg, mn, mx = conn.execute(sql).fetchone()
    except sqlite3.Error as exc:
        return {"sql": sql, "error": str(exc)}
    return {"sql": sql, "n": n, "avg": avg, "min": mn, "max": mx}


def column_profile(conn, table, col, decl_type):
    qcol = _quote(col)
    sql = "SELECT %s FROM %s" % (qcol, _quote(table))
    try:
        values = [r[0] for r in conn.execute(sql)]
    except sqlite3.Error as exc:
        return {"column": col, "kind": "error", "extraction_sql": sql,
                "error": str(exc)}
    n_total = len(values)
    non_null = [v for v in values if v is not None and str(v).strip() != ""]
    n_nonnull = len(non_null)
    n_missing = n_total - n_nonnull

    counter = {}
    for v in non_null:
        key = str(v)
        counter[key] = counter.get(key, 0) + 1
    top_values = [{"value": k, "count": c}
                  for k, c in sorted(counter.items(),
                                     key=lambda kv: (-kv[1], kv[0]))[:10]]

    prof = {
        "column": col, "declared_type": decl_type, "kind": "text",
        "n_total": n_total, "n_nonnull": n_nonnull, "n_missing": n_missing,
        "missing_rate": (n_missing / n_total) if n_total else None,
        "n_distinct": len(counter),
        "top_values": top_values,
        "value_counts_sql": "SELECT %s, COUNT(*) FROM %s GROUP BY %s ORDER BY 2 DESC LIMIT 10" % (qcol, _quote(table), qcol),
        "missing_sql": "SELECT COUNT(*) FROM %s WHERE %s IS NULL OR TRIM(%s) = ''" % (_quote(table), qcol, qcol),
        "extraction_sql": sql,
    }
    nums, coded = _numeric_values(non_null, decl_type)
    if nums is not None:
        prof["kind"] = "numeric"
        prof["numeric"] = _describe(nums)
        prof["coded_as_text"] = bool(coded)
        prof["numeric"]["values"] = nums
        prof["aggregate"] = _sql_aggregate(conn, table, col, coded)
    return prof


def detect_duplicates(conn, table, columns):
    if not columns:
        return {}
    group = ", ".join(_quote(c["name"]) for c in columns)
    sql = ("SELECT COUNT(*) FROM (SELECT %s FROM %s GROUP BY %s HAVING COUNT(*) > 1)"
           % (group, _quote(table), group))
    try:
        ng = conn.execute(sql).fetchone()[0]
    except sqlite3.Error as exc:
        return {"full_row_dup_sql": sql, "error": str(exc)}
    rows_sql = ("SELECT COALESCE(SUM(c),0) FROM (SELECT COUNT(*) c FROM %s "
                "GROUP BY %s HAVING COUNT(*) > 1)" % (_quote(table), group))
    try:
        nrows = conn.execute(rows_sql).fetchone()[0]
    except sqlite3.Error:
        nrows = 0
    return {"full_row_dup_sql": sql, "n_duplicate_groups": ng,
            "n_rows_in_duplicate_groups": nrows or 0}


# --------------------------------------------------------------------------- #
# Pearson correlation (Python cross-check)
# --------------------------------------------------------------------------- #
def pearson(a, b):
    n = len(a)
    if n < 3:
        return None, n
    ma = statistics.mean(a)
    mb = statistics.mean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((y - mb) ** 2 for y in b))
    if sa == 0 or sb == 0:
        return None, n
    return cov / (sa * sb), n


def correlate(numeric_cols):
    pairs = []
    for i in range(len(numeric_cols)):
        for j in range(i + 1, len(numeric_cols)):
            a, b = numeric_cols[i], numeric_cols[j]
            va, vb = [], []
            for x, y in zip(a["values"], b["values"]):
                if x is not None and y is not None:
                    va.append(x)
                    vb.append(y)
            r, n = pearson(va, vb)
            pairs.append({"a": a["name"], "b": b["name"], "r": r, "n": n})
    return pairs


# --------------------------------------------------------------------------- #
# dictionary annotation (light, evidence-grounded)
# --------------------------------------------------------------------------- #
def annotate_dictionary(dict_files, schema, errors):
    for f in dict_files:
        text = read_text(f)
        if text is None:
            errors.append({"item": str(f), "detail": "dict_unreadable"})
            continue
        for t in schema:
            for c in t["columns"]:
                name = c["name"]
                if not re.search(r"\b%s\b" % re.escape(name), text, re.I):
                    continue
                m = re.search(r".{0,60}\b%s\b.{0,140}" % re.escape(name),
                              text, re.I)
                if m:
                    c.setdefault("dict_notes", []).append(
                        {"source": str(f), "quote": m.group(0).strip()})
    return schema
