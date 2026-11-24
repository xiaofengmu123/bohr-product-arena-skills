#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dbkit: deterministic, read-only data access and statistics utilities.

Only the Python standard library is used. Responsibilities:
  * text decoding with BOM / encoding fallback (never crashes on bad bytes)
  * input discovery (case.json, task.md, SQLite, CSV/TSV, dictionary files)
  * read-only SQLite connection and schema / FK introspection
  * CSV/TSV -> in-memory SQLite loader with light type inference
  * column profiling, missing/duplicate detection, descriptive statistics
  * Pearson correlation and join-key / foreign-key inference
  * SQL-vs-Python cross-check helpers

Determinism contract: all JSON dumps sort keys, all iterables that affect the
analysis output are sorted, and no random source is used anywhere.
"""

import csv
import io
import json
import math
import os
import re
import sqlite3
import statistics
import time
from pathlib import Path
from urllib.parse import quote

MAX_TEXT_BYTES = 2 * 1024 * 1024          # 2 MB per text file
NUMERIC_MISSING_TOKENS = {
    "", "na", "n/a", "nan", "null", "none", "?", "-", "--", ".", "..",
}
DB_EXTENSIONS = {".db", ".sqlite", ".sqlite3", ".db3"}
TABULAR_EXTENSIONS = {".csv", ".tsv", ".tab"}
DICT_EXTENSIONS = {".md", ".txt", ".yaml", ".yml", ".json"}


# --------------------------------------------------------------------------- #
# text & discovery
# --------------------------------------------------------------------------- #
def read_text(path, max_bytes=MAX_TEXT_BYTES):
    """Read a text file tolerantly. Returns (text, encoding) or (None, None)."""
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError:
        return None, None
    if size > max_bytes:
        return None, "too_large"
    raw = None
    try:
        raw = p.read_bytes()
    except OSError as e:
        return None, "unreadable:%s" % e
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1(replace)"


def _is_db(name):
    return Path(name).suffix.lower() in DB_EXTENSIONS


def _is_tabular(name):
    return Path(name).suffix.lower() in TABULAR_EXTENSIONS


def _is_dict(name):
    return Path(name).suffix.lower() in DICT_EXTENSIONS


def discover_inputs(input_dir):
    """Scan an input directory and classify files without assuming fixed names."""
    root = Path(input_dir)
    result = {
        "case_json": None, "task_md": None, "databases": [], "tabular": [],
        "dictionaries": [], "other": [], "missing": [],
    }
    if not root.exists() or not root.is_dir():
        result["missing"].append(str(root))
        return result
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        name = p.name.lower()
        if name == "case.json":
            result["case_json"] = str(p)
        elif name == "task.md":
            result["task_md"] = str(p)
        elif _is_db(name):
            result["databases"].append(str(p))
        elif _is_tabular(name):
            result["tabular"].append(str(p))
        elif _is_dict(name) and name not in ("case.json", "task.md"):
            result["dictionaries"].append(str(p))
        else:
            result["other"].append(str(p))
    result["databases"].sort()
    result["tabular"].sort()
    result["dictionaries"].sort()
    return result


def load_case_json(path):
    """Load case.json. Returns (case_id, output_contract, raw, error)."""
    if not path:
        return None, None, None, "missing"
    text, enc = read_text(path)
    if text is None:
        return None, None, None, "unreadable_or_too_large"
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        return None, None, None, "bad_json:%s" % e
    if not isinstance(raw, dict):
        return None, None, None, "not_object"
    case_id = raw.get("case_id") or raw.get("caseId") or raw.get("id")
    contract = raw.get("output_contract")
    if contract is None and isinstance(raw.get("contract"), dict):
        contract = raw.get("contract")
    return case_id, contract, raw, None


def find_fallback_json(input_dir):
    """If case.json is absent, return the first *.json in the tree as fallback."""
    root = Path(input_dir)
    if not root.exists():
        return None
    for p in sorted(root.rglob("*.json")):
        if p.name.lower() != "case.json":
            return str(p)
    return None


# --------------------------------------------------------------------------- #
# SQLite / CSV access
# --------------------------------------------------------------------------- #
def open_db_readonly(path):
    """Open a SQLite file in read-only mode. Returns connection or raises."""
    abs_path = str(Path(path).resolve())
    uri = "file:%s?mode=ro" % quote(abs_path, safe="/:")
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn


def inspect_schema(conn):
    """Return schema cards for every user table, sorted by table name."""
    tables = []
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    for (tname,) in rows:
        cols = []
        for c in conn.execute(
                "PRAGMA table_info(%s)" % quote_ident(tname)):
            cols.append({
                "name": c[1],
                "type": c[2] or "",
                "notnull": bool(c[3]),
                "pk": c[5],
            })
        fks = []
        for f in conn.execute("PRAGMA foreign_key_list(%s)" % quote_ident(tname)):
            fks.append({
                "id": f[0], "seq": f[1], "parent_table": f[2],
                "child_col": f[3], "parent_col": f[4],
            })
        try:
            row_count = conn.execute(
                "SELECT COUNT(*) FROM %s" % quote_ident(tname)).fetchone()[0]
        except sqlite3.Error:
            row_count = None
        tables.append({
            "table": tname, "columns": cols, "row_count": row_count,
            "foreign_keys": fks,
        })
    tables.sort(key=lambda t: t["table"])
    return tables


def quote_ident(name):
    """Double-quote an SQLite identifier safely."""
    return '"%s"' % str(name).replace('"', '""')


def infer_csv_types(rows):
    """Infer a declared type per column from sampled non-empty values."""
    if not rows:
        return []
    ncols = max(len(r) for r in rows)
    declared = []
    for ci in range(ncols):
        seen = []
        for r in rows:
            if ci < len(r):
                v = (r[ci] or "").strip()
                if v != "":
                    seen.append(v)
        if not seen:
            declared.append("TEXT")
            continue
        try:
            for v in seen:
                int(v)
            declared.append("INTEGER")
            continue
        except (ValueError, OverflowError):
            pass
        try:
            for v in seen:
                float(v)
            declared.append("REAL")
            continue
        except (ValueError, OverflowError):
            pass
        declared.append("TEXT")
    return declared


def load_tabular(path):
    """Load CSV/TSV into an in-memory SQLite DB. Returns (conn, table, note)."""
    text, enc = read_text(path)
    if text is None:
        raise ValueError("unreadable tabular file")
    # Delimiter sniffing on a small sample.
    sample = text[:4096]
    delim = ","
    if Path(path).suffix.lower() in (".tsv", ".tab"):
        delim = "\t"
    else:
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            delim = dialect.delimiter
        except csv.Error:
            delim = ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    if not rows:
        raise ValueError("empty tabular file")
    header = [h.strip() or ("col_%d" % (i + 1)) for i, h in enumerate(rows[0])]
    # De-duplicate header names deterministically (col, col_2, col_3, ...).
    used = set()
    for i, h in enumerate(header):
        cand, n = h, 2
        while cand in used:
            cand = "%s_%d" % (h, n)
            n += 1
        header[i] = cand
        used.add(cand)
    body = rows[1:]
    # Normalize row lengths.
    ncols = len(header)
    body = [r + [""] * (ncols - len(r)) if len(r) < ncols else r[:ncols]
            for r in body]
    declared = infer_csv_types(body)
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA query_only = ON")
    table = "csv_" + _sanitize_table(Path(path).stem)
    coldefs = ", ".join(
        "%s %s" % (quote_ident(h), declared[i])
        for i, h in enumerate(header))
    conn.execute("CREATE TABLE %s (%s)" % (quote_ident(table), coldefs))
    placeholders = ", ".join(["?"] * ncols)
    conn.executemany(
        "INSERT INTO %s VALUES (%s)" % (quote_ident(table), placeholders), body)
    conn.commit()
    return conn, table, "delimiter=%r; encoding=%s; columns=%d" % (
        delim, enc, ncols)


def _sanitize_table(stem):
    out = []
    for ch in stem:
        out.append(ch if ch.isalnum() or ch == "_" else "_")
    s = "".join(out).strip("_")
    return s or "data"


# --------------------------------------------------------------------------- #
# column profiling
# --------------------------------------------------------------------------- #
def _to_number(v):
    if v is None:
        return None
    s = str(v).strip()
    if s.lower() in NUMERIC_MISSING_TOKENS:
        return None
    try:
        return int(s)
    except (ValueError, OverflowError):
        pass
    try:
        return float(s)
    except (ValueError, OverflowError):
        return None


def _is_inty(v):
    n = _to_number(v)
    return isinstance(n, int)


def _is_numeric(v):
    n = _to_number(v)
    return isinstance(n, (int, float))


def column_profile(conn, table, col, declared_type):
    """Profile one column. Returns a dict of facts used across the pipeline."""
    qtable = quote_ident(table)
    qcol = quote_ident(col)
    try:
        values = [r[0] for r in conn.execute(
            "SELECT %s FROM %s" % (qcol, qtable)).fetchall()]
    except sqlite3.Error as e:
        return {"column": col, "error": str(e), "kind": "error"}
    n_total = len(values)
    missing = [v is None or (isinstance(v, str) and v == "") for v in values]
    n_missing = sum(missing)
    nonnull = [v for v, m in zip(values, missing) if not m]
    n_nonnull = len(nonnull)

    # Kind classification.
    if not nonnull:
        kind = "empty"
    else:
        int_ok = all(_is_inty(v) for v in nonnull)
        float_ok = all(_is_numeric(v) for v in nonnull)
        if int_ok:
            kind = "integer"
        elif float_ok:
            kind = "numeric"
        else:
            kind = "categorical"

    declared_lc = str(declared_type).lower()
    declared_numeric = any(t in declared_lc for t in
                           ("int", "real", "floa", "doub", "num", "dec"))
    coded_as_text = (kind in ("integer", "numeric")) and (not declared_numeric)

    # Top values (sorted by count desc, then by string key asc for stability).
    counts = {}
    for v in nonnull:
        key = str(v)
        counts[key] = counts.get(key, 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:12]
    top_values = [{"value": k, "count": c} for k, c in top]
    n_distinct = len(counts)

    # ID-like detection.
    lname = str(col).lower()
    id_hint = any(t in lname for t in ("id", "key", "sample", "index", "code"))
    unique_ratio = (n_distinct / n_nonnull) if n_nonnull else 0.0
    id_like = id_hint and kind in ("integer", "categorical") and unique_ratio >= 0.9

    profile = {
        "column": col, "declared_type": declared_type or "",
        "kind": kind, "n_total": n_total, "n_nonnull": n_nonnull,
        "n_missing": n_missing, "missing_ratio": round(
            (n_missing / n_total) if n_total else 0.0, 6),
        "n_distinct": n_distinct, "top_values": top_values,
        "coded_as_text": coded_as_text, "id_like": id_like,
    }

    if kind in ("integer", "numeric"):
        nums = [_to_number(v) for v in nonnull]
        nums = [x for x in nums if x is not None]
        if nums:
            nums_sorted = sorted(nums)
            n = len(nums_sorted)
            mean = sum(nums_sorted) / n
            var = (sum((x - mean) ** 2 for x in nums_sorted) / (n - 1)
                   if n > 1 else 0.0)
            stdev = math.sqrt(var)
            q1 = nums_sorted[int(0.25 * (n - 1))]
            q3 = nums_sorted[int(0.75 * (n - 1))]
            median = nums_sorted[n // 2] if n % 2 else (
                nums_sorted[n // 2 - 1] + nums_sorted[n // 2]) / 2.0
            iqr = q3 - q1
            lo = q1 - 1.5 * iqr
            hi = q3 + 1.5 * iqr
            n_outliers = sum(1 for x in nums_sorted if x < lo or x > hi)
            profile["numeric"] = {
                "n": n, "mean": mean, "stdev_sample": stdev,
                "min": nums_sorted[0], "max": nums_sorted[-1],
                "median": median, "q1": q1, "q3": q3, "iqr": iqr,
                "n_outliers_iqr": n_outliers,
            }
    return profile


def detect_duplicates(conn, table):
    """Detect exact full-row duplicates via GROUP BY over all columns."""
    schema = inspect_schema(conn)
    meta = next((t for t in schema if t["table"] == table), None)
    if not meta or not meta["columns"]:
        return {"n_duplicate_groups": 0, "n_rows_in_duplicate_groups": 0,
                "full_row_dup_sql": None}
    cols = [c["name"] for c in meta["columns"]]
    colsql = ", ".join(quote_ident(c) for c in cols)
    sql = ("SELECT COUNT(*) FROM (SELECT %s FROM %s GROUP BY %s "
           "HAVING COUNT(*)>1)" % (colsql, quote_ident(table), colsql))
    try:
        n_groups = conn.execute(sql).fetchone()[0]
    except sqlite3.Error:
        return {"n_duplicate_groups": None, "n_rows_in_duplicate_groups": None,
                "full_row_dup_sql": sql, "error": True}
    sql_rows = ("SELECT SUM(cnt) FROM (SELECT COUNT(*) AS cnt FROM %s "
                "GROUP BY %s HAVING COUNT(*)>1)"
                % (quote_ident(table), colsql))
    n_rows = conn.execute(sql_rows).fetchone()[0] or 0
    return {"n_duplicate_groups": n_groups, "n_rows_in_duplicate_groups": n_rows,
            "full_row_dup_sql": sql}


# --------------------------------------------------------------------------- #
# correlation & cross-check
# --------------------------------------------------------------------------- #
def pearson(xs, ys):
    paired = [(x, y) for x, y in zip(xs, ys)
              if x is not None and y is not None]
    n = len(paired)
    if n < 3:
        return None, n
    mx = sum(x for x, _ in paired) / n
    my = sum(y for _, y in paired) / n
    num = sum((x - mx) * (y - my) for x, y in paired)
    dx = math.sqrt(sum((x - mx) ** 2 for x, _ in paired))
    dy = math.sqrt(sum((y - my) ** 2 for _, y in paired))
    if dx == 0 or dy == 0:
        return None, n
    return num / (dx * dy), n


def correlate_table(conn, table, numeric_cols):
    """Pairwise Pearson correlation over numeric columns."""
    out = []
    for i, a in enumerate(numeric_cols):
        for j, b in enumerate(numeric_cols):
            if j <= i:
                continue
            r, n = pearson(a["values"], b["values"])
            out.append({
                "col_a": a["name"], "col_b": b["name"],
                "pearson_r": None if r is None else round(r, 6),
                "n_paired": n,
            })
    out.sort(key=lambda e: (e["col_a"], e["col_b"]))
    return out


def sql_aggregate(conn, table, col):
    """Run SQL AVG/MIN/MAX/COUNT as an independent cross-check."""
    try:
        row = conn.execute(
            "SELECT AVG(%s), MIN(%s), MAX(%s), COUNT(%s) FROM %s"
            % (quote_ident(col), quote_ident(col), quote_ident(col),
               quote_ident(col), quote_ident(table))).fetchone()
    except sqlite3.Error as e:
        return {"sql": None, "error": str(e)}
    return {"sql": "SELECT AVG(%s), MIN(%s), MAX(%s), COUNT(%s) FROM %s"
                   % (quote_ident(col), quote_ident(col), quote_ident(col),
                      quote_ident(col), quote_ident(table)),
            "avg": row[0], "min": row[1], "max": row[2], "count": row[3]}


def matches(a, b, tol=1e-9):
    if a is None or b is None:
        return False
    try:
        return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(a)), abs(float(b)))
    except (TypeError, ValueError):
        return str(a) == str(b)


# --------------------------------------------------------------------------- #
# join inference
# --------------------------------------------------------------------------- #
def infer_joins(schema):
    """Suggest join paths from declared FKs and shared column name/type.

    Each suggestion carries a cardinality estimate so the agent can avoid
    one-to-many row inflation before writing any SQL.
    """
    joins = []
    by_table = {t["table"]: t for t in schema}
    names = sorted(by_table.keys())
    # 1) Declared foreign keys.
    for t in schema:
        for fk in t.get("foreign_keys", []):
            parent = by_table.get(fk["parent_table"])
            if not parent:
                continue
            joins.append(_join_entry(
                t["table"], fk["child_col"], fk["parent_table"],
                fk["parent_col"], "declared_fk", by_table))
    # 2) Shared column name + compatible-ish type.
    for i, t1 in enumerate(names):
        for t2 in names[i + 1:]:
            c1 = {c["name"]: c for c in by_table[t1]["columns"]}
            c2 = {c["name"]: c for c in by_table[t2]["columns"]}
            for shared in sorted(set(c1) & set(c2)):
                a, b = c1[shared], c2[shared]
                if _compatible(a["type"], b["type"]):
                    joins.append(_join_entry(
                        t1, shared, t2, shared, "shared_column", by_table))
    # De-duplicate by (left, lcol, right, rcol).
    seen = set()
    uniq = []
    for j in joins:
        key = (j["left_table"], j["left_col"], j["right_table"], j["right_col"])
        if key not in seen:
            seen.add(key)
            uniq.append(j)
    uniq.sort(key=lambda j: (j["left_table"], j["left_col"],
                             j["right_table"], j["right_col"]))
    return uniq


def _compatible(t1, t2):
    a = (t1 or "").lower()
    b = (t2 or "").lower()
    num = ("int", "real", "floa", "doub", "num", "dec")
    a_num = any(t in a for t in num) or a == ""
    b_num = any(t in b for t in num) or b == ""
    if a_num and b_num:
        return True
    return a == b


def _join_entry(lt, lc, rt, rc, kind, by_table):
    lt_meta, rt_meta = by_table[lt], by_table[rt]
    lc_meta = next((c for c in lt_meta["columns"] if c["name"] == lc), {})
    rc_meta = next((c for c in rt_meta["columns"] if c["name"] == rc), {})
    left_type = lc_meta.get("type", "")
    right_type = rc_meta.get("type", "")
    # Cardinality: distinct counts, computed lazily by main.
    return {
        "left_table": lt, "left_col": lc, "right_table": rt, "right_col": rc,
        "join_kind": kind, "left_type": left_type, "right_type": right_type,
        "note": "left.%s(%s) -> right.%s(%s)" % (lc, left_type or "?", rc,
                                                  right_type or "?"),
    }


def distinct_count(conn, table, col):
    try:
        return conn.execute(
            "SELECT COUNT(DISTINCT %s) FROM %s"
            % (quote_ident(col), quote_ident(table))).fetchone()[0]
    except sqlite3.Error:
        return None


# --------------------------------------------------------------------------- #
# output helpers
# --------------------------------------------------------------------------- #
def nullify(value):
    """Recursively build a null/[] skeleton mirroring a contract structure."""
    if isinstance(value, dict):
        return {k: nullify(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return []
    return None


def dumps_sorted(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2)


def write_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps_sorted(obj))


# --------------------------------------------------------------------------- #
# output / artifact helpers shared with main
# --------------------------------------------------------------------------- #
def now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def slug(s):
    out = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(s)).strip("_")
    return out or "unnamed"


def load_json_optional(path):
    if not path or not Path(path).exists():
        return None
    text, _enc = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def write_csv(path, header, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def write_chart(figures_dir, fname, content, result, kind):
    path = os.path.join(figures_dir, fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    rel = os.path.join("figures", fname)
    result["artifacts"][rel] = "chart"
    result["analysis"].setdefault("charts", []).append(
        {"file": rel, "kind": kind})
    return rel


def extract_values(conn, table, col):
    try:
        return [r[0] for r in conn.execute(
            "SELECT %s FROM %s" % (quote_ident(col),
                                   quote_ident(table))).fetchall()]
    except sqlite3.Error:
        return []


def corr_matrix(numcols):
    names = [c["name"] for c in numcols]
    n = len(names)
    mat = [[1.0 if i == j else None for j in range(n)] for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            r, _ = pearson(numcols[i]["values"], numcols[j]["values"])
            mat[i][j] = r
            mat[j][i] = r
    return mat


def build_evidence(result):
    evidence = []
    for s in result["sources"]:
        evidence.append({"claim": s.get("role"), "locator": s["locator"],
                         "quote": s["quote"], "role": s["role"]})
    for r in result["analysis"].get("summary_stats", []):
        evidence.append({
            "claim": "numeric summary %s.%s" % (r.get("table"),
                                                r.get("column")),
            "locator": "sqlite://%s#%s.%s" % (r.get("source"), r.get("table"),
                                              r.get("column")),
            "quote": "n=%s; mean=%s; stdev_sample=%s; min=%s; max=%s"
                     % (r.get("n"), r.get("mean"), r.get("stdev_sample"),
                        r.get("min"), r.get("max")),
            "role": "statistic",
        })
    return evidence


def build_report(result, case_id, version):
    lines = ["# 科研数据库查询与分析报告", "",
             "- case_id: `%s`" % (case_id or "unknown"),
             "- pipeline version: `%s`" % version,
             "- status: `%s`" % result["status"],
             "- generated_at: `%s`" % now_ts(), ""]
    lines += ["## 数据源", ""]
    for s in result["analysis"].get("schema", []):
        lines.append("- `%s` → 表 `%s`（%s 行）" % (
            s.get("source"), s.get("table"), s.get("row_count")))
    if not result["analysis"].get("schema"):
        lines.append("- （未发现可读数据源）")
    lines += ["", "## 关键数值（摘要）", ""]
    for r in result["analysis"].get("summary_stats", [])[:30]:
        lines.append("- `%s.%s`: n=%s, mean=%s, stdev=%s, min=%s, max=%s"
                     % (r.get("table"), r.get("column"), r.get("n"),
                        r.get("mean"), r.get("stdev_sample"), r.get("min"),
                        r.get("max")))
    lines += ["", "## 建议连接路径", ""]
    for j in result["analysis"].get("suggested_joins", []):
        lines.append("- `%s.%s` → `%s.%s`（%s；左 distinct=%s，右 distinct=%s）"
                     % (j.get("left_table"), j.get("left_col"),
                        j.get("right_table"), j.get("right_col"),
                        j.get("join_kind"), j.get("left_distinct"),
                        j.get("right_distinct")))
    lines += ["", "## 数据质量", ""]
    for r in result["analysis"].get("data_quality", []):
        flag = ""
        if r.get("coded_as_text"):
            flag = " [coded_as_text]"
        if r.get("id_like"):
            flag += " [id_like]"
        lines.append("- `%s.%s`: %s, missing=%s (%.3f)%s" % (
            r.get("table"), r.get("column"), r.get("kind"),
            r.get("n_missing"), r.get("missing_ratio"), flag))
    lines += ["", "## 限制与待办", ""]
    for lim in result["limitations"]:
        lines.append("- %s" % lim)
    for err in result["errors"]:
        lines.append("- ERROR: %s" % err)
    for pen in result["pending_human"]:
        lines.append("- PENDING: %s" % pen)
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# self-test helpers (used by main --selftest)
# --------------------------------------------------------------------------- #
def frontmatter_ok(skill_path):
    p = Path(skill_path)
    if not p.exists():
        return False, "SKILL.md missing"
    text = p.read_text(encoding="utf-8", errors="replace")
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return False, "no YAML frontmatter"
    fm = m.group(1)
    for key in ("name", "description", "version"):
        if not re.search(r"^%s\s*:" % key, fm, re.M):
            return False, "frontmatter missing %s" % key
    return True, "ok"


def make_synthetic_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE measurements (sample_id INTEGER, group_name TEXT, "
                 "value REAL)")
    conn.executemany("INSERT INTO measurements VALUES (?,?,?)", [
        (1, "A", 1.1), (2, "A", 2.2), (3, "B", 3.3), (4, "B", None),
        (5, "B", 5.5), (6, "C", 6.6),
    ])
    conn.execute("CREATE TABLE groups (group_name TEXT, label TEXT)")
    conn.executemany("INSERT INTO groups VALUES (?,?)",
                     [("A", "alpha"), ("B", "beta"), ("C", "gamma")])
    conn.commit()
    conn.close()
