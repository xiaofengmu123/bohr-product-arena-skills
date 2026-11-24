#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl: deterministic read-only analysis primitives for scientific data.

Standard library only. Every function is deterministic for the same input;
no prepared answers, no random state, no fabricated numbers. All values are
read from the authorised snapshots at run time.

Design note (variant focus: time budget + early skeleton): this module only
contains the heavy, deterministic computation. The orchestrator in main.py
writes a legal partial result skeleton *before* calling any of this, so an
interrupted run always leaves a parseable result.json on disk.
"""

import csv
import io
import json
import math
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.0.0"

DB_EXTS = {".db", ".sqlite", ".sqlite3", ".db3"}
CSV_EXTS = {".csv", ".tsv"}
TEXT_EXTS = {".md", ".txt", ".yaml", ".yml", ".json", ".csv", ".tsv"}
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_QUERY_ROWS = 200
MAX_VALUE_SET = 20000

_NUM_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_ALLOWED_SQL = re.compile(r"^\s*(SELECT|WITH|PRAGMA|EXPLAIN)\b", re.I)
_ID_LIKE = re.compile(
    r"^(id|rowid|row_id|index|idx|pk|sample_id|subject_id|record_id|patient_id|"
    r"case_id|subject|participant_id)$", re.I)


def is_id_like(name):
    n = str(name).strip().lower()
    return bool(_ID_LIKE.match(n)) or n.endswith("_id") or n.endswith("_pk") \
        or n.endswith("_key")


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def qid(ident):
    """Quote an SQLite identifier safely (table or column name)."""
    return '"' + str(ident).replace('"', '""') + '"'


def read_text(path):
    """Read a text file with encoding fallback; return None when unusable."""
    p = Path(path)
    try:
        if not p.exists() or not p.is_file():
            return None
        if p.stat().st_size > MAX_TEXT_BYTES:
            return None
        data = p.read_bytes()
    except OSError:
        return None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _looks_like_dictionary(name):
    n = name.lower()
    return any(k in n for k in
               ("dict", "dictionary", "schema", "codebook", "variable",
                "字段", "字典", "数据字典"))


def discover_input(input_dir):
    root = Path(input_dir)
    out = {"case_json": None, "task_md": None, "db_files": [],
           "csv_files": [], "dict_files": [], "other_json": []}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        name = p.name.lower()
        ext = p.suffix.lower()
        if name == "case.json":
            out["case_json"] = str(p)
        elif name == "task.md":
            out["task_md"] = str(p)
        elif ext in DB_EXTS:
            out["db_files"].append(str(p))
        elif ext in CSV_EXTS and _looks_like_dictionary(p.name):
            out["dict_files"].append(str(p))
        elif ext in CSV_EXTS:
            out["csv_files"].append(str(p))
        elif ext in (".md", ".yaml", ".yml"):
            out["dict_files"].append(str(p))
        elif ext == ".json":
            out["other_json"].append(str(p))
    return out


def connect_readonly(path):
    """Open a SQLite database read-only (URI mode=ro + PRAGMA query_only)."""
    p = Path(path).resolve()
    uri = p.as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn


# --------------------------------------------------------------------------- #
# CSV ingestion -> in-memory SQLite tables (uniform downstream path)
# --------------------------------------------------------------------------- #
def _sanitize_columns(raw_names):
    seen = {}
    out = []
    for i, n in enumerate(raw_names):
        base = re.sub(r"[^0-9A-Za-z_]+", "_", str(n).strip()) or ("col%d" % i)
        if base[0].isdigit():
            base = "c_" + base
        if base in seen:
            seen[base] += 1
            base = "%s_%d" % (base, seen[base])
        else:
            seen[base] = 0
        out.append(base)
    return out


def _sniff_csv(text, delimiter):
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [r for r in reader if r]
    if not rows:
        return None, None
    first = rows[0]
    has_header = any(re.search(r"[A-Za-z\u4e00-\u9fff]", str(c or ""))
                     for c in first)
    if has_header:
        header = _sanitize_columns(first)
        data = rows[1:]
    else:
        header = _sanitize_columns(["col%d" % i for i in range(len(first))])
        data = rows
    return header, data


def _infer_col_types(header, data):
    types = []
    for ci in range(len(header)):
        col_vals = [r[ci] if ci < len(r) else "" for r in data]
        nonempty = [v for v in col_vals if str(v).strip() != ""]
        all_int = bool(nonempty) and all(
            isinstance(v, int) or (isinstance(v, str) and re.match(r"^[+-]?\d+$", v.strip()))
            for v in nonempty)
        all_float = bool(nonempty) and all(
            isinstance(v, (int, float)) or (isinstance(v, str) and _NUM_RE.match(v.strip()))
            for v in nonempty)
        if all_int:
            types.append("INTEGER")
        elif all_float:
            types.append("REAL")
        else:
            types.append("TEXT")
    return types


def load_csv_table(conn, path, delimiter):
    """Create a table inside an in-memory sqlite connection from one CSV."""
    text = read_text(path)
    if text is None:
        return None
    header, data = _sniff_csv(text, delimiter)
    if header is None:
        return None
    types = _infer_col_types(header, data)
    safe_name = re.sub(r"[^0-9A-Za-z_]+", "_", Path(path).stem) or "csv_table"
    if safe_name[0].isdigit():
        safe_name = "t_" + safe_name
    # ensure uniqueness by caller prefixing; keep stable here
    ddl = "CREATE TABLE %s (%s)" % (
        qid(safe_name),
        ", ".join("%s %s" % (qid(h), t) for h, t in zip(header, types)))
    conn.execute(ddl)
    placeholders = ",".join("?" * len(header))
    ins = "INSERT INTO %s VALUES (%s)" % (qid(safe_name), placeholders)
    def convert(v, t):
        s = "" if v is None else str(v).strip()
        if s == "":
            return None
        if t == "INTEGER":
            try:
                return int(float(s))
            except ValueError:
                return s
        if t == "REAL":
            try:
                return float(s)
            except ValueError:
                return s
        return v
    conn.executemany(ins, [
        tuple(convert(r[i] if i < len(r) else "", types[i])
              for i in range(len(header)))
        for r in data])
    return {"name": safe_name, "path": path, "n_rows": len(data),
            "header": header, "types": types}


# --------------------------------------------------------------------------- #
# schema + profiling
# --------------------------------------------------------------------------- #
def inspect_schema(conn):
    tables = []
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    for (tname,) in rows:
        cols = []
        for _cid, name, decltype, notnull, _dflt, pk in conn.execute(
                "PRAGMA table_info(%s)" % qid(tname)):
            cols.append({"name": name, "type": decltype or "",
                         "notnull": bool(notnull), "pk": pk})
        try:
            cnt = conn.execute("SELECT COUNT(*) FROM %s" % qid(tname)).fetchone()[0]
        except sqlite3.Error:
            cnt = 0
        try:
            fks = [list(r) for r in
                   conn.execute("PRAGMA foreign_key_list(%s)" % qid(tname)).fetchall()]
        except sqlite3.Error:
            fks = []
        tables.append({"table": tname, "row_count": cnt, "columns": cols,
                       "foreign_keys": fks})
    return tables


def describe_numeric(xs):
    xs = sorted(float(x) for x in xs)
    n = len(xs)
    if n == 0:
        return None

    def q(sorted_xs, p):
        if len(sorted_xs) == 1:
            return float(sorted_xs[0])
        pos = p * (len(sorted_xs) - 1)
        lo = math.floor(pos)
        hi = math.ceil(pos)
        if lo == hi:
            return float(sorted_xs[lo])
        return float(sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (pos - lo))

    mean = sum(xs) / n
    stdev = (math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - 1))
             if n >= 2 else None)
    q1, med, q3 = q(xs, 0.25), q(xs, 0.5), q(xs, 0.75)
    iqr = q3 - q1
    lo_f, hi_f = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    outliers = [x for x in xs if x < lo_f or x > hi_f]
    return {"n": n, "mean": _round(mean), "stdev_sample": _round(stdev),
            "min": _round(xs[0]), "q1": _round(q1), "median": _round(med),
            "q3": _round(q3), "max": _round(xs[-1]), "iqr": _round(iqr),
            "outlier_low": _round(lo_f), "outlier_high": _round(hi_f),
            "n_outliers": len(outliers),
            "outlier_values": [_round(x) for x in outliers[:20]]}


def _round(x, nd=8):
    if x is None:
        return None
    if isinstance(x, bool):
        return x
    try:
        v = round(float(x), nd)
        return int(v) if v == int(v) else v
    except (TypeError, ValueError):
        return x


def profile_column(conn, table, col, decltype):
    """Return (serializable_profile, numeric_aligned_list_or_None)."""
    prof = {"name": col, "declared_type": decltype, "kind": "empty",
            "n_rows": 0, "n_nonnull": 0, "n_missing": 0, "n_distinct": 0,
            "top_values": [], "numeric": None,
            "sql": {"extraction": None, "missing": None, "value_counts": None,
                    "aggregate": None}}
    try:
        rows = [r[0] for r in conn.execute(
            "SELECT %s FROM %s" % (qid(col), qid(table)))]
    except sqlite3.Error as e:
        prof["kind"] = "unreadable"
        prof["error"] = str(e)
        return prof, None
    n_rows = len(rows)
    prof["n_rows"] = n_rows
    prof["sql"]["extraction"] = "SELECT %s FROM %s" % (qid(col), qid(table))

    nonnull = []
    missing = 0
    for v in rows:
        if v is None or (isinstance(v, str) and v.strip() == ""):
            missing += 1
        else:
            nonnull.append(v)
    prof["n_nonnull"] = len(nonnull)
    prof["n_missing"] = missing
    prof["sql"]["missing"] = (
        "SELECT COUNT(*) FROM %s WHERE %s IS NULL OR TRIM(CAST(%s AS TEXT))=''"
        % (qid(table), qid(col), qid(col)))
    prof["sql"]["value_counts"] = (
        "SELECT CAST(%s AS TEXT) AS v, COUNT(*) AS n FROM %s "
        "WHERE %s IS NOT NULL AND TRIM(CAST(%s AS TEXT))<>'' "
        "GROUP BY v ORDER BY n DESC LIMIT 5"
        % (qid(col), qid(table), qid(col), qid(col)))
    prof["n_distinct"] = len(set(str(v) for v in nonnull))
    prof["top_values"] = [[str(v), c]
                          for v, c in Counter(str(x) for x in nonnull).most_common(5)]

    numeric_aligned = None
    if nonnull:
        all_native = all(isinstance(v, (int, float)) and not isinstance(v, bool)
                         for v in nonnull)
        all_numstr = all(isinstance(v, str) and _NUM_RE.match(v.strip())
                         for v in nonnull)
        if all_native:
            kind = "numeric"
            numeric_aligned = [None if (v is None or (isinstance(v, str) and v.strip() == ""))
                               else float(v) for v in rows]
        elif all_numstr:
            kind = "coded_as_text"
            numeric_aligned = [None if (v is None or (isinstance(v, str) and v.strip() == ""))
                               else float(v.strip()) for v in rows]
        elif all(isinstance(v, str) for v in nonnull):
            kind = "text"
        else:
            kind = "mixed"
        prof["kind"] = kind

        if kind in ("numeric", "coded_as_text"):
            vals = [x for x in numeric_aligned if x is not None]
            prof["numeric"] = {"n": len(vals), "stats": describe_numeric(vals)}
            if kind == "coded_as_text":
                prof["numeric"]["note"] = (
                    "column is declared as text but every non-blank value parses "
                    "as a number; converted with float(strip()) in Python, SQL AVG "
                    "was intentionally not used on the text column")
            else:
                agg_sql = ("SELECT COUNT(%s), AVG(%s), MIN(%s), MAX(%s) FROM %s "
                           "WHERE %s IS NOT NULL AND TRIM(CAST(%s AS TEXT))<>''"
                           % tuple([qid(col)] * 4 + [qid(table), qid(col), qid(col)]))
                prof["sql"]["aggregate"] = agg_sql
                try:
                    a = conn.execute(agg_sql).fetchone()
                    pmean = prof["numeric"]["stats"]["mean"]
                    pmin = prof["numeric"]["stats"]["min"]
                    pmax = prof["numeric"]["stats"]["max"]
                    matches = (_close(a[1], pmean) and _close(a[2], pmin)
                               and _close(a[3], pmax))
                    prof["numeric"]["aggregate"] = {
                        "sql": agg_sql, "count": a[0],
                        "avg": _round(a[1]), "min": _round(a[2]),
                        "max": _round(a[3]),
                        "matches_python": bool(matches)}
                except sqlite3.Error as e:
                    prof["numeric"]["aggregate"] = {"error": str(e)}
    return prof, numeric_aligned


def _close(a, b, tol=1e-6):
    if a is None or b is None:
        return a is None and b is None
    try:
        return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(b)))
    except (TypeError, ValueError):
        return False


def detect_duplicates(conn, table, columns):
    if not columns:
        return {}
    colsql = ", ".join(qid(c) for c in columns)
    tsql = qid(table)
    sql = ("SELECT %s, COUNT(*) AS n FROM %s GROUP BY %s HAVING COUNT(*) > 1"
           % (colsql, tsql, colsql))
    try:
        rows = conn.execute(sql).fetchall()
    except sqlite3.Error as e:
        return {"error": str(e), "full_row_dup_sql": sql}
    n_groups = len(rows)
    n_in = sum(r[-1] for r in rows)
    return {"full_row_dup_sql": sql, "n_duplicate_groups": n_groups,
            "n_rows_in_duplicate_groups": n_in}


# --------------------------------------------------------------------------- #
# correlation + join inference
# --------------------------------------------------------------------------- #
def pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs, ys):
    if len(xs) < 2:
        return None
    return pearson(_ranks(xs), _ranks(ys))


def correlate_table(conn, table, numeric_columns):
    """numeric_columns: list of (name, aligned_values). Pairwise complete cases."""
    results = []
    ncol = len(numeric_columns)
    for i in range(ncol):
        for j in range(i + 1, ncol):
            n1, a1 = numeric_columns[i]
            n2, a2 = numeric_columns[j]
            xs, ys = [], []
            for x, y in zip(a1, a2):
                if x is not None and y is not None:
                    xs.append(float(x))
                    ys.append(float(y))
            if len(xs) < 2:
                continue
            rp = pearson(xs, ys)
            rs = spearman(xs, ys)
            results.append({"x": n1, "y": n2, "n": len(xs),
                            "pearson": _round(rp), "spearman": _round(rs)})
    return results


def column_value_sets(conn, tables):
    sets = {}
    for t in tables:
        for c in t["columns"]:
            key = (t["table"], c["name"])
            try:
                s = set()
                for (v,) in conn.execute(
                        "SELECT DISTINCT %s FROM %s WHERE %s IS NOT NULL LIMIT %d"
                        % (qid(c["name"]), qid(t["table"]), qid(c["name"]),
                           MAX_VALUE_SET)):
                    s.add(v)
                sets[key] = s
            except sqlite3.Error:
                sets[key] = set()
    return sets


def infer_join_candidates(tables, colsets):
    candidates = []
    for i in range(len(tables)):
        for j in range(i + 1, len(tables)):
            ti, tj = tables[i], tables[j]
            for ci in ti["columns"]:
                for cj in tj["columns"]:
                    si = colsets.get((ti["table"], ci["name"]), set())
                    sj = colsets.get((tj["table"], cj["name"]), set())
                    if not si or not sj:
                        continue
                    inter = len(si & sj)
                    denom = max(1, min(len(si), len(sj)))
                    ratio = inter / denom
                    if ratio >= 0.8:
                        candidates.append({
                            "left_table": ti["table"], "left_key": ci["name"],
                            "right_table": tj["table"], "right_key": cj["name"],
                            "overlap_ratio": _round(ratio),
                            "n_left_distinct": len(si),
                            "n_right_distinct": len(sj),
                            "n_shared": inter,
                            "candidate_join": True})
    candidates.sort(key=lambda d: (-(d["overlap_ratio"] or 0), -d["n_shared"]))
    return candidates


def join_row_check(conn, left, right, lcol, rcol):
    sql = ("SELECT COUNT(*) FROM (SELECT 1 FROM %s AS l LEFT JOIN %s AS r "
           "ON l.%s = r.%s)" % (qid(left), qid(right), qid(lcol), qid(rcol)))
    try:
        joined = conn.execute(sql).fetchone()[0]
        left_rows = conn.execute("SELECT COUNT(*) FROM %s" % qid(left)).fetchone()[0]
        ratio = (joined / left_rows) if left_rows else None
        flag = "row_expansion" if (ratio is not None and ratio > 1.0) else "ok"
        return {"sql": sql, "joined_rows": joined, "left_rows": left_rows,
                "expansion_ratio": _round(ratio), "flag": flag}
    except sqlite3.Error as e:
        return {"sql": sql, "error": str(e), "flag": "error"}


# --------------------------------------------------------------------------- #
# dictionary grounding
# --------------------------------------------------------------------------- #
def ground_dictionary(dict_files, all_columns):
    """Very small, transparent field-annotation extractor."""
    annotations = []
    for f in dict_files:
        txt = read_text(f)
        if txt is None:
            continue
        source = Path(f).name
        # try JSON / YAML dict-of-string first
        parsed = _try_parse_kv(txt)
        if parsed:
            for field, desc in parsed.items():
                if any(field == c for _t, c in all_columns):
                    annotations.append({"field": field, "description": str(desc),
                                        "source": source})
            continue
        for line in txt.splitlines():
            line = line.strip()
            if not line or len(line) > 240:
                continue
            for _t, col in all_columns:
                if col and re.search(r"\b%s\b" % re.escape(col), line):
                    annotations.append({"field": col, "description": line,
                                        "source": source})
                    break
    # dedupe preserving order
    seen = set()
    uniq = []
    for a in annotations:
        k = (a["field"], a["description"], a["source"])
        if k not in seen:
            seen.add(k)
            uniq.append(a)
    return uniq


def _try_parse_kv(text):
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        obj = None
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(v, (str, int, float, bool)):
                out[str(k)] = v
            elif isinstance(v, dict):
                for k2, v2 in v.items():
                    if isinstance(v2, (str, int, float, bool)):
                        out[str(k2)] = v2
        return out or None
    return None


# --------------------------------------------------------------------------- #
# read-only query execution
# --------------------------------------------------------------------------- #
def execute_readonly(conn, sql):
    if not isinstance(sql, str) or not _ALLOWED_SQL.match(sql):
        raise ValueError("only SELECT/WITH/PRAGMA/EXPLAIN statements are allowed")
    cur = conn.execute(sql)
    rows = cur.fetchall()
    cols = [d[0] for d in (cur.description or [])]
    return {"columns": cols, "rows": [list(r) for r in rows],
            "row_count": len(rows)}


# --------------------------------------------------------------------------- #
# minimal editable SVG charts (standard library only)
# --------------------------------------------------------------------------- #
def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _svg_open(w, h):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
            'viewBox="0 0 %d %d" font-family="sans-serif">\n' % (w, h, w, h))


def svg_histogram(values, title, xlabel, n_bins=20, w=640, h=380):
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return None
    lo, hi = min(vals), max(vals)
    if lo == hi:
        lo, hi = lo - 1, hi + 1
    width = (hi - lo) / n_bins
    counts = [0] * n_bins
    for v in vals:
        idx = min(n_bins - 1, int((v - lo) / width))
        counts[idx] += 1
    top = max(counts)
    px, py, pw, ph = 56, 28, w - 84, h - 92
    s = [_svg_open(w, h),
         '<rect x="0" y="0" width="%d" height="%d" fill="white"/>\n' % (w, h),
         '<text x="%d" y="20" font-size="15" font-weight="bold">%s</text>\n'
         % (w // 2, _esc(title)),
         '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="black"/>\n'
         % (px, py + ph, px + pw, py + ph),
         '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="black"/>\n'
         % (px, py, px, py + ph)]
    for i, c in enumerate(counts):
        bh = (c / top) * ph if top else 0
        x = px + i * (pw / n_bins)
        s.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" '
                 'fill="steelblue" stroke="white"/>\n'
                 % (x, py + ph - bh, pw / n_bins, bh))
    s.append('<text x="%d" y="%d" font-size="12" text-anchor="middle">%s</text>\n'
             % (px + pw / 2, py + ph + 28, _esc(xlabel)))
    s.append('<text x="%d" y="%d" font-size="11">n=%d  min=%s  max=%s</text>\n'
             % (px, py + ph + 28, len(vals), _round(lo, 4), _round(hi, 4)))
    s.append('</svg>\n')
    return "".join(s)


def svg_bar(counts, title, w=640, h=380):
    counts = [(str(k), int(v)) for k, v in (counts or [])]
    if not counts:
        return None
    top = max(v for _k, v in counts)
    n = len(counts)
    px, py, pw, ph = 56, 28, w - 84, h - 92
    bw = min(120, pw / max(1, n))
    s = [_svg_open(w, h),
         '<rect x="0" y="0" width="%d" height="%d" fill="white"/>\n' % (w, h),
         '<text x="%d" y="20" font-size="15" font-weight="bold">%s</text>\n'
         % (w // 2, _esc(title)),
         '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="black"/>\n'
         % (px, py + ph, px + pw, py + ph),
         '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="black"/>\n'
         % (px, py, px, py + ph)]
    for i, (k, v) in enumerate(counts):
        bh = (v / top) * ph if top else 0
        x = px + i * (pw / n) + (pw / n - bw) / 2
        s.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" '
                 'fill="darkorange" stroke="white"/>\n' % (x, py + ph - bh, bw, bh))
        s.append('<text x="%.2f" y="%d" font-size="10" text-anchor="middle">%s</text>\n'
                 % (x + bw / 2, py + ph + 16, _esc(k[:14])))
    s.append('</svg>\n')
    return "".join(s)


def svg_heatmap(names, matrix, title, w=640, h=480):
    names = [str(x) for x in names]
    m = [[None if v is None else float(v) for v in row] for row in matrix]
    n = len(names)
    if n == 0:
        return None
    cell = min(46, 420 // max(1, n))
    px, py = 120, 40
    total = px + n * cell
    s = [_svg_open(max(w, total + 30), max(h, total + 60)),
         '<rect x="0" y="0" width="%d" height="%d" fill="white"/>\n'
         % (max(w, total + 30), max(h, total + 60)),
         '<text x="%d" y="20" font-size="15" font-weight="bold">%s</text>\n'
         % ((total + 30) // 2, _esc(title))]
    for i in range(n):
        for j in range(n):
            v = m[i][j]
            color = "white"
            if v is not None:
                if v >= 0:
                    color = "rgb(%d,%d,255)" % (int(255 - 200 * v), int(255 - 200 * v))
                else:
                    color = "rgb(255,%d,%d)" % (int(255 + 200 * v), int(255 + 200 * v))
            s.append('<rect x="%d" y="%d" width="%d" height="%d" fill="%s" '
                     'stroke="gray"/>\n' % (px + j * cell, py + i * cell,
                                            cell, cell, color))
            if v is not None:
                s.append('<text x="%d" y="%d" font-size="9" text-anchor="middle">%.2f</text>\n'
                         % (px + j * cell + cell / 2, py + i * cell + cell / 2 + 3, v))
        s.append('<text x="%d" y="%d" font-size="9" text-anchor="end" '
                 'transform="rotate(-30 %d %d)">%s</text>\n'
                 % (px - 4, py + i * cell + cell / 2, px - 4,
                    py + i * cell + cell / 2, _esc(names[i][:20])))
        s.append('<text x="%d" y="%d" font-size="9" text-anchor="end" '
                 'transform="rotate(-30 %d %d)">%s</text>\n'
                 % (px + i * cell + cell / 2, py - 4,
                    px + i * cell + cell / 2, py - 4, _esc(names[i][:20])))
    s.append('</svg>\n')
    return "".join(s)


def svg_scatter(xs, ys, title, xlabel, ylabel, w=640, h=420):
    pts = [(float(x), float(y)) for x, y in zip(xs, ys)
           if x is not None and y is not None]
    if not pts:
        return None
    xlo, xhi = min(p[0] for p in pts), max(p[0] for p in pts)
    ylo, yhi = min(p[1] for p in pts), max(p[1] for p in pts)
    if xlo == xhi:
        xlo, xhi = xlo - 1, xhi + 1
    if ylo == yhi:
        ylo, yhi = ylo - 1, yhi + 1
    px, py, pw, ph = 60, 30, w - 90, h - 90
    s = [_svg_open(w, h),
         '<rect x="0" y="0" width="%d" height="%d" fill="white"/>\n' % (w, h),
         '<text x="%d" y="20" font-size="15" font-weight="bold">%s</text>\n'
         % (w // 2, _esc(title)),
         '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="black"/>\n'
         % (px, py + ph, px + pw, py + ph),
         '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="black"/>\n'
         % (px, py, px, py + ph)]
    for x, y in pts[:4000]:
        sx = px + (x - xlo) / (xhi - xlo) * pw
        sy = py + ph - (y - ylo) / (yhi - ylo) * ph
        s.append('<circle cx="%.2f" cy="%.2f" r="2.2" fill="steelblue" '
                 'fill-opacity="0.55"/>\n' % (sx, sy))
    s.append('<text x="%d" y="%d" font-size="12" text-anchor="middle">%s</text>\n'
             % (px + pw / 2, py + ph + 26, _esc(xlabel)))
    s.append('<text x="14" y="%d" font-size="12" transform="rotate(-90 14 %d)">%s</text>\n'
             % (py + ph / 2, py + ph / 2, _esc(ylabel)))
    s.append('<text x="%d" y="%d" font-size="11">n=%d</text>\n'
             % (px, py + ph + 26, len(pts)))
    s.append('</svg>\n')
    return "".join(s)


# --------------------------------------------------------------------------- #
# join inference + figure orchestration (kept here to keep main.py small)
# --------------------------------------------------------------------------- #
def infer_joins(sources, tbl_list):
    """Infer candidate join keys across tables, with a row-expansion guard."""
    sets = {}
    conns = {s["label"]: s["conn"] for s in sources}
    for entry in tbl_list:
        lbl = entry["label"]
        try:
            for c in entry["columns"]:
                key = (lbl, entry["table"], c["name"])
                s = set()
                for (v,) in conns[lbl].execute(
                        "SELECT DISTINCT %s FROM %s WHERE %s IS NOT NULL LIMIT %d"
                        % (qid(c["name"]), qid(entry["table"]), qid(c["name"]),
                           MAX_VALUE_SET)):
                    s.add(v)
                sets[key] = s
        except sqlite3.Error:
            continue
    candidates = []
    n = len(tbl_list)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = tbl_list[i], tbl_list[j]
            for ca in a["columns"]:
                for cb in b["columns"]:
                    sa = sets.get((a["label"], a["table"], ca["name"]), set())
                    sb = sets.get((b["label"], b["table"], cb["name"]), set())
                    if not sa or not sb:
                        continue
                    inter = len(sa & sb)
                    ratio = inter / max(1, min(len(sa), len(sb)))
                    if ratio >= 0.8:
                        same_conn = conns[a["label"]] is conns[b["label"]]
                        if same_conn:
                            guard = join_row_check(conns[a["label"]], a["table"],
                                                   b["table"], ca["name"],
                                                   cb["name"])
                        else:
                            guard = {"flag": "cross_source_not_checked",
                                     "note": "join across two different files "
                                             "was not executed"}
                        candidates.append({
                            "left_source": a["label"], "left_table": a["table"],
                            "left_key": ca["name"], "right_source": b["label"],
                            "right_table": b["table"], "right_key": cb["name"],
                            "overlap_ratio": _round(ratio), "n_shared": inter,
                            "row_guard": guard})
    candidates.sort(key=lambda d: -(d.get("overlap_ratio") or 0))
    return candidates


def make_figures(figures_dir, sources):
    """Choose which editable SVG charts to render; return (artifacts, list)."""
    artifacts = {}
    chart_list = []
    numeric_by_table = {}
    cat_by_table = {}
    for src in sources:
        try:
            tables = inspect_schema(src["conn"])
        except sqlite3.Error:
            continue
        for t in tables:
            num = []
            cat = []
            for c in t["columns"]:
                prof, aligned = profile_column(src["conn"], t["table"],
                                               c["name"], c["type"])
                if prof.get("numeric") and aligned:
                    if not is_id_like(c["name"]):
                        num.append((c["name"], aligned))
                elif prof.get("kind") == "text" and prof.get("top_values"):
                    cat.append((c["name"], prof["top_values"]))
            if num:
                numeric_by_table[(src["label"], t["table"])] = (src["conn"], num)
            if cat:
                cat_by_table[(src["label"], t["table"])] = (src["conn"], cat)

    n_figs = 0
    for (label, tname), (conn, num) in numeric_by_table.items():
        for cname, aligned in num[:4]:
            content = svg_histogram(aligned, "%s.%s distribution" % (tname, cname),
                                    cname)
            if content and n_figs < 24:
                n_figs += 1
                _save_fig(figures_dir, "%s_%s_hist.svg" % (tname, cname),
                          content, artifacts, chart_list, "histogram")
        if len(num) >= 2:
            names = [x[0] for x in num]
            mat = []
            for _n1, a1 in num:
                row = []
                for _n2, a2 in num:
                    xs, ys = [], []
                    for x, y in zip(a1, a2):
                        if x is not None and y is not None:
                            xs.append(float(x)); ys.append(float(y))
                    row.append(pearson(xs, ys) if len(xs) >= 2 else None)
                mat.append(row)
            content = svg_heatmap(names, mat, "%s Pearson correlation" % tname)
            if content and n_figs < 24:
                n_figs += 1
                _save_fig(figures_dir, "%s_corr_heatmap.svg" % tname, content,
                          artifacts, chart_list, "heatmap")
            best, best_r = None, -1.0
            for i in range(len(num)):
                for j in range(i + 1, len(num)):
                    n1, a1 = num[i]; n2, a2 = num[j]
                    xs, ys = [], []
                    for x, y in zip(a1, a2):
                        if x is not None and y is not None:
                            xs.append(float(x)); ys.append(float(y))
                    r = pearson(xs, ys) if len(xs) >= 2 else None
                    if r is not None and abs(r) > best_r:
                        best_r = abs(r); best = (n1, n2, xs, ys)
            if best and n_figs < 24:
                n_figs += 1
                _save_fig(figures_dir,
                          "%s_scatter_%s_%s.svg" % (tname, best[0], best[1]),
                          svg_scatter(best[2], best[3],
                                      "%s vs %s" % (best[0], best[1]),
                                      best[0], best[1]),
                          artifacts, chart_list, "scatter")
    for (label, tname), (conn, cat) in cat_by_table.items():
        for cname, topv in cat[:2]:
            content = svg_bar(topv, "%s.%s top values" % (tname, cname))
            if content and n_figs < 24:
                n_figs += 1
                _save_fig(figures_dir, "%s_%s_bar.svg" % (tname, cname),
                          content, artifacts, chart_list, "bar")
    return artifacts, chart_list


def _save_fig(figures_dir, fname, content, artifacts, chart_list, kind):
    path = os.path.join(figures_dir, fname)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        rel = "figures/" + fname
        artifacts[rel] = "chart"
        chart_list.append({"file": rel, "kind": kind})
    except OSError:
        pass
