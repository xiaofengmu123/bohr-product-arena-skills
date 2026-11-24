#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core: deterministic read-only scientific DB query & analysis engine.

Design (verification-first, IR-decoupled):
  * discovery -> build a normalized input manifest;
  * catalog  -> open every source read-only, expose tables as one namespace;
  * profile  -> per-column typing, missing/duplicate detection, numeric stats,
                value counts, and an SQL aggregate cross-check against Python;
  * semantic -> ground columns against a data dictionary, mark coded columns;
  * correlate-> Pearson matrix on numeric columns (pairwise complete cases);
  * charts   -> editable, deterministic SVG (histogram / bar / heatmap);
  * contract -> transparently suggest values for output_contract keys, never
                inventing numbers that were not computed from the data.

Only the Python standard library is used. No prepared answers are produced.
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
from datetime import datetime, timezone
from pathlib import Path

VERSION = "2.0.0"
MAX_TEXT_BYTES = 2_000_000
MAX_QUERY_ROWS = 200
STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")


# --------------------------------------------------------------------------- #
# small deterministic helpers
# --------------------------------------------------------------------------- #
def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize(s):
    """Lowercase and collapse non-alphanumerics to single spaces."""
    return re.sub(r"[^0-9a-z]+", " ", str(s).lower()).strip()


def quote_ident(name):
    return '"' + str(name).replace('"', '""') + '"'


def _xml_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;")
            .replace("'", "&apos;"))


def _f(v, nd=6):
    """Format a float deterministically; None stays None."""
    if v is None:
        return None
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return round(v, nd)
    return v


def read_text(path, max_bytes=MAX_TEXT_BYTES):
    if not os.path.exists(path):
        return None, "missing"
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, "stat failed: %s" % e
    if size > max_bytes:
        return None, "oversize: %d bytes > %d" % (size, max_bytes)
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        return None, "read failed: %s" % e
    last = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), None
        except (UnicodeDecodeError, LookupError) as e:  # noqa: PERF203
            last = e
    return None, "decode failed: %s" % last


def read_json(path):
    text, err = read_text(path)
    if err:
        return None, err
    try:
        obj = json.loads(text)
    except Exception as e:  # noqa: BLE001
        return None, "bad json: %s" % e
    return obj, None


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, default=_json_default)
    return path


def _json_default(o):
    if isinstance(o, (datetime,)):
        return o.isoformat()
    if isinstance(o, (Path,)):
        return str(o)
    raise TypeError("not serializable: %r" % type(o))


# --------------------------------------------------------------------------- #
# input discovery
# --------------------------------------------------------------------------- #
_DB_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".db3")
_DICT_SUFFIXES = (".md", ".yaml", ".yml", ".txt", ".json")
_DICT_HEADER_HINTS = ("field", "column", "name", "variable", "description",
                      "desc", "说明", "含义", "字段", "列名", "变量",
                      "definition", "meaning", "unit", "单位", "type", "类型",
                      "range", "范围", "dtype", "semantics")
# Avoid re-ingesting our own deliverables if output/work are nested under input.
_ARTIFACT_NAMES = {"result.json", "schema.json", "summary_stats.csv",
                   "data_quality.csv", "correlation_matrix.csv",
                   "query_log.csv", "value_counts.json", "evidence_map.json",
                   "analysis_ir.json", "report.md", "queries.json"}


def discover_input(input_dir):
    d = {"case_json": None, "task_md": None, "dbs": [], "csvs": [],
         "dicts": [], "jsons": [], "other": [],
         "errors": [], "degradations": []}
    root = Path(input_dir)
    if not root.exists():
        d["errors"].append("input directory missing: %s" % input_dir)
        return d
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        name = p.name.lower()
        if name in _ARTIFACT_NAMES:
            continue
        try:
            if name == "case.json":
                d["case_json"] = str(p)
            elif name == "task.md":
                d["task_md"] = str(p)
            elif name.endswith(_DB_SUFFIXES):
                d["dbs"].append(str(p))
            elif name.endswith((".csv", ".tsv")):
                if _looks_like_dictionary(p):
                    d["dicts"].append(str(p))
                else:
                    d["csvs"].append(str(p))
            elif name.endswith(".json"):
                d["jsons"].append(str(p))
            elif name.endswith(_DICT_SUFFIXES):
                d["dicts"].append(str(p))
            else:
                d["other"].append(str(p))
        except OSError as e:
            d["degradations"].append("skip %s: %s" % (p, e))
    if d["case_json"] is None and d["jsons"]:
        d["case_json"] = d["jsons"].pop(0)
    if d["task_md"] is None:
        for p in d["dicts"]:
            text, err = read_text(p)
            if err is None and ("任务" in text or "task" in text.lower()
                                or "output_contract" in text):
                d["task_md"] = p
                d["dicts"].remove(p)
                break
    return d


def _looks_like_dictionary(path):
    text, err = read_text(path, 200_000)
    if err:
        return False
    try:
        header = text.splitlines()[0].lower()
    except IndexError:
        return False
    hits = sum(1 for h in _DICT_HEADER_HINTS if h in header)
    return hits >= 2


def load_case(discovery):
    case_id = None
    contract = {}
    raw = {}
    errors = list(discovery.get("errors", []))
    path = discovery.get("case_json")
    if path:
        obj, err = read_json(path)
        if err:
            errors.append("case json %s: %s" % (path, err))
        elif isinstance(obj, dict):
            raw = obj
            case_id = obj.get("case_id") or obj.get("id")
            if case_id is None and isinstance(obj.get("case"), dict):
                case_id = obj["case"].get("id")
            if case_id is None:
                case_id = Path(path).stem
            contract = obj.get("output_contract")
            if not isinstance(contract, dict):
                contract = {}
    return case_id, contract, raw, errors


def build_skeleton(case_id, contract, discovery):
    result = {
        "case_id": case_id,
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "change_log": [{
            "ts": now_iso(), "step": "skeleton",
            "change": "initial legal skeleton written before any analysis",
            "status": "partial"}],
        "analysis": {},
        "query_log": [],
        "answers": {},
        "artifacts": {},
        "errors": list(discovery.get("errors", [])),
        "degradations": list(discovery.get("degradations", [])),
        "limitations": [],
        "pending_human": [],
        "runtime": {"started_at": now_iso(), "version": VERSION},
    }
    for k in contract:
        result[k] = None
    return result


# --------------------------------------------------------------------------- #
# catalog: unified read-only table namespace
# --------------------------------------------------------------------------- #
def open_sqlite_ro(path):
    apath = os.path.abspath(path).replace("\\", "/")
    uri = "file:%s?mode=ro" % apath
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _is_int(v):
    if isinstance(v, bool):
        return False
    try:
        int(str(v).strip())
        return True
    except (ValueError, TypeError):
        return False


def _is_float(v):
    if isinstance(v, bool):
        return False
    try:
        float(str(v).strip())
        return True
    except (ValueError, TypeError):
        return False


def _infer_csv_types(header, rows):
    types = []
    for i, _ in enumerate(header):
        vals = [r[i] for r in rows if i < len(r) and str(r[i]).strip() != ""]
        if not vals:
            types.append("TEXT")
        elif all(_is_int(v) for v in vals):
            types.append("INTEGER")
        elif all(_is_float(v) for v in vals):
            types.append("REAL")
        else:
            types.append("TEXT")
    return types


def _sanitize_ident(name, seen):
    base = re.sub(r"[^0-9A-Za-z_]", "_", str(name).strip()) or "col"
    if base[0].isdigit():
        base = "c_" + base
    out = base
    i = 2
    while out.lower() in seen:
        out = "%s_%d" % (base, i)
        i += 1
    seen.add(out.lower())
    return out


def load_csv_to_sqlite(path, label):
    text, err = read_text(path)
    if err:
        raise RuntimeError("csv %s: %s" % (path, err))
    first = text.splitlines()[0] if text.splitlines() else ""
    delim = "\t" if first.count("\t") > first.count(",") else ","
    if first.count(";") > first.count(delim):
        delim = ";"
    conn = sqlite3.connect(":memory:")
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    try:
        rows = list(reader)
    except csv.Error as e:
        raise RuntimeError("csv parse %s: %s" % (path, e))
    if not rows:
        return conn, label, [], [], 0
    seen = set()
    header = [_sanitize_ident(h, seen) for h in rows[0]]
    data = rows[1:]
    types = _infer_csv_types(header, data)
    cols = [{"name": h, "type": t, "notnull": False, "pk": 0}
            for h, t in zip(header, types)]
    conn.execute("CREATE TABLE %s (%s)" % (
        quote_ident(label),
        ", ".join("%s %s" % (quote_ident(h), t)
                  for h, t in zip(header, types))))
    converted = []
    for r in data:
        row = []
        for i, t in enumerate(types):
            v = r[i] if i < len(r) else ""
            s = str(v).strip()
            if s == "":
                row.append(None)
            elif t == "INTEGER":
                row.append(int(float(s)) if "." in s else int(s))
            elif t == "REAL":
                row.append(float(s))
            else:
                row.append(s)
        converted.append(row)
    if converted:
        conn.executemany("INSERT INTO %s VALUES (%s)" % (
            quote_ident(label), ",".join("?" * len(header))), converted)
    n = conn.execute("SELECT COUNT(*) FROM %s" % quote_ident(label)).fetchone()[0]
    return conn, label, cols, header, n


def build_catalog(discovery, work_dir):
    catalog = []
    errors = []
    degradations = []
    for path in discovery.get("dbs", []):
        label = Path(path).stem or "db"
        label = _sanitize_ident(label, set())
        try:
            conn = open_sqlite_ro(path)
            schema = inspect_sqlite_schema(conn)
            catalog.append({"label": label, "kind": "sqlite", "path": path,
                            "conn": conn, "schema": schema})
        except Exception as e:  # noqa: BLE001
            errors.append("db open failed %s: %s" % (path, e))
    for path in discovery.get("csvs", []):
        label = Path(path).stem or "csv"
        label = _sanitize_ident(label, set())
        try:
            conn, lbl, cols, _header, n = load_csv_to_sqlite(path, label)
            catalog.append({"label": lbl, "kind": "csv", "path": path,
                            "conn": conn,
                            "schema": [{"table": lbl, "columns": cols,
                                        "row_count": n}]})
        except Exception as e:  # noqa: BLE001
            errors.append("csv load failed %s: %s" % (path, e))
    return catalog, errors, degradations


def inspect_sqlite_schema(conn):
    tables = []
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
    except Exception:
        names = []
    for name in names:
        cols = []
        try:
            for _cid, cname, ctype, notnull, _dflt, pk in conn.execute(
                    "PRAGMA table_info(%s)" % quote_ident(name)):
                cols.append({"name": cname, "type": ctype or "",
                             "notnull": bool(notnull), "pk": pk})
        except Exception:
            cols = []
        rc = None
        try:
            rc = conn.execute("SELECT COUNT(*) FROM %s"
                              % quote_ident(name)).fetchone()[0]
        except Exception:
            pass
        tables.append({"table": name, "columns": cols, "row_count": rc})
    return tables


# --------------------------------------------------------------------------- #
# profiling
# --------------------------------------------------------------------------- #
def _norm_value(v):
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8")
        except Exception:  # noqa: BLE001
            return str(v)
    return v


def _is_numeric(v):
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    try:
        float(str(v).strip())
        return True
    except (ValueError, TypeError):
        return False


def _to_float(v):
    if isinstance(v, (int, float)):
        return float(v)
    return float(str(v).strip())


def _classify_kind(declared, values):
    if not values:
        return "empty", False
    nonnull = [_norm_value(v) for v in values
               if v is not None and str(v).strip() != ""]
    if not nonnull:
        return "empty", False
    all_text = all(isinstance(v, str) for v in nonnull)
    all_num = all(_is_numeric(v) for v in nonnull)
    if all_text and all_num:
        return "numeric", True  # coded_as_text
    if all_num:
        if all(_is_int(v) for v in nonnull):
            return "numeric", False
        return "numeric", False
    # boolean-like
    bools = {"0", "1", "true", "false", "yes", "no", "t", "f"}
    if all(str(_norm_value(v)).strip().lower() in bools for v in nonnull):
        return "boolean", False
    return "text", False


def _numeric_stats(values):
    floats = [_to_float(_norm_value(v)) for v in values
              if v is not None and str(v).strip() != ""
              and _is_numeric(v)]
    n = len(floats)
    if n == 0:
        return {"n": 0, "mean": None, "stdev_sample": None, "min": None,
                "max": None, "median": None, "q1": None, "q3": None,
                "sum": None, "values": []}
    floats.sort()
    mean = statistics.fmean(floats)
    stdev = statistics.stdev(floats) if n >= 2 else None
    median = statistics.median(floats)
    if n >= 4:
        q = statistics.quantiles(floats, n=4)
        q1, q3 = q[0], q[2]
    else:
        q1 = q3 = None
    return {"n": n, "mean": _f(mean), "stdev_sample": _f(stdev),
            "min": _f(floats[0]), "max": _f(floats[-1]),
            "median": _f(median), "q1": _f(q1), "q3": _f(q3),
            "sum": _f(sum(floats)), "values": floats}


def column_profile(conn, table, col):
    name = col["name"]
    ctype = col["type"]
    ident = quote_ident(name)
    tbl = quote_ident(table)
    extraction_sql = "SELECT %s FROM %s" % (ident, tbl)
    try:
        vals = [r[0] for r in conn.execute(extraction_sql)]
    except Exception as e:  # noqa: BLE001
        return {"name": name, "type": ctype, "kind": "error",
                "error": str(e), "extraction_sql": extraction_sql,
                "n_total": None, "n_nonnull": None, "n_missing": None,
                "n_distinct": None, "top_values": []}
    n_total = len(vals)
    nonnull = [v for v in vals if v is not None and str(v).strip() != ""]
    n_nonnull = len(nonnull)
    n_missing = n_total - n_nonnull
    kind, coded = _classify_kind(ctype, nonnull)
    # distinct (values normalized; NaN folded to a string bucket)
    distinct = {}
    for v in nonnull:
        vv = _norm_value(v)
        if isinstance(vv, float) and not math.isfinite(vv):
            vv = repr(vv)
        distinct[vv] = distinct.get(vv, 0) + 1
    n_distinct = len(distinct)
    top = sorted(distinct.items(), key=lambda kv: (-kv[1], str(kv[0])))[:10]
    top_values = [{"value": k, "count": c} for k, c in top]
    # value counts sql (deterministic, grouped only)
    value_counts_sql = ("SELECT %s, COUNT(*) AS n FROM %s GROUP BY %s"
                        % (ident, tbl, ident))
    missing_sql = ("SELECT COUNT(*) FROM %s WHERE %s IS NULL OR %s = ''"
                   % (tbl, ident, ident))
    prof = {
        "name": name, "type": ctype, "kind": kind,
        "coded_as_text": coded,
        "low_cardinality": (kind == "numeric" and n_distinct is not None
                           and n_total and 1 <= n_distinct <= 10
                           and n_distinct / float(n_total) <= 0.5),
        "extraction_sql": extraction_sql,
        "missing_sql": missing_sql,
        "value_counts_sql": value_counts_sql,
        "n_total": n_total, "n_nonnull": n_nonnull,
        "n_missing": n_missing, "n_distinct": n_distinct,
        "top_values": top_values,
    }
    if kind == "numeric":
        st = _numeric_stats(nonnull)
        prof["numeric"] = {k: v for k, v in st.items() if k != "values"}
        prof["numeric_values"] = st["values"]
        # SQL aggregate cross-check (Python recompute is the independent side)
        agg_expr = ("CAST(%s AS REAL)" % ident if coded else ident)
        agg_sql = ("SELECT COUNT(*), AVG(%s), MIN(%s), MAX(%s) FROM %s"
                   % (agg_expr, agg_expr, agg_expr, tbl))
        try:
            row = conn.execute(agg_sql).fetchone()
            avg, mn, mx = row[1], row[2], row[3]
            py_avg = _f(st["mean"])
            match = (py_avg is None or avg is None
                     or abs(float(avg) - float(py_avg)) < 1e-6 * max(
                         1.0, abs(float(py_avg))))
            prof["aggregate"] = {
                "sql": agg_sql, "count": row[0],
                "avg": _f(avg), "min": _f(mn), "max": _f(mx),
                "matches_python": bool(match),
                "conversion": ("CAST(col AS REAL)" if coded
                               else "direct numeric")}
        except Exception as e:  # noqa: BLE001
            prof["aggregate"] = {"sql": agg_sql, "error": str(e)}
    return prof


def detect_duplicates(conn, table, columns):
    exprs = ", ".join(quote_ident(c["name"]) for c in columns)
    if not exprs:
        return {"full_row_dup_sql": None, "n_duplicate_groups": 0,
                "n_rows_in_duplicate_groups": 0}
    sql = ("SELECT %s, COUNT(*) AS n FROM %s GROUP BY %s HAVING COUNT(*) > 1"
           % (exprs, quote_ident(table), exprs))
    try:
        rows = conn.execute(sql).fetchall()
    except Exception as e:  # noqa: BLE001
        return {"full_row_dup_sql": sql, "n_duplicate_groups": None,
                "n_rows_in_duplicate_groups": None, "error": str(e)}
    groups = len(rows)
    involved = sum(r[-1] for r in rows)
    return {"full_row_dup_sql": sql, "n_duplicate_groups": groups,
            "n_rows_in_duplicate_groups": involved}


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None, n
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None, n
    return cov / math.sqrt(vx * vy), n


def correlate_table(conn, table, numeric_cols):
    """Pearson correlations on row-aligned values fetched in one SELECT."""
    names = [c["name"] for c in numeric_cols]
    if len(names) < 2:
        return []
    sel = ", ".join(quote_ident(n) for n in names)
    sql = "SELECT %s FROM %s" % (sel, quote_ident(table))
    try:
        rows = conn.execute(sql).fetchall()
    except Exception:
        return []
    pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            xs, ys = [], []
            for r in rows:
                a, b = r[i], r[j]
                if a is not None and b is not None \
                        and _is_numeric(a) and _is_numeric(b):
                    xs.append(_to_float(a))
                    ys.append(_to_float(b))
            r, n = pearson(xs, ys)
            pairs.append({"x": names[i], "y": names[j], "pearson": _f(r),
                          "n": n,
                          "note": "" if n >= 3 else "n<3, unreliable"})
    return pairs


# --------------------------------------------------------------------------- #
# semantic grounding against a data dictionary
# --------------------------------------------------------------------------- #
def parse_dictionary(path):
    text, err = read_text(path)
    if err:
        return {"path": path, "entries": {}, "notes": [], "error": err}
    entries = {}
    notes = []
    lines = text.splitlines()
    if path.lower().endswith((".csv", ".tsv")):
        _parse_dict_csv(text, entries, notes)
    else:
        _parse_dict_md(lines, entries, notes)
    return {"path": path, "entries": entries, "notes": notes, "error": None}


def _add_entry(entries, key, attrs):
    key = str(key).strip()
    if not key:
        return
    norm = normalize(key)
    target = None
    for k in entries:
        if normalize(k) == norm:
            target = entries[k]
            break
    if target is None:
        entries[key] = attrs
    else:
        target.update(attrs)


def _parse_dict_csv(text, entries, notes):
    try:
        reader = csv.reader(io.StringIO(text))
        rows = [r for r in reader]
    except csv.Error as e:
        notes.append("csv dict parse error: %s" % e)
        return
    if not rows:
        return
    header = [str(h).strip() for h in rows[0]]
    for r in rows[1:]:
        if len(r) < 1:
            continue
        key = str(r[0]).strip()
        attrs = {}
        for h, v in zip(header[1:], r[1:]):
            if h and str(v).strip():
                attrs[str(h).strip()] = str(v).strip()
        _add_entry(entries, key, attrs)


def _parse_dict_md(lines, entries, notes):
    # 1) pipe tables
    table = []
    def flush():
        if table:
            _parse_pipe_table(table, entries, notes)
        table[:] = []
    for ln in lines:
        if "|" in ln:
            table.append(ln)
        else:
            flush()
    flush()
    # 2) key-value lines outside tables: "key : value" or "key - value"
    for ln in lines:
        if "|" in ln or not ln.strip() or ln.lstrip().startswith("#"):
            continue
        for sep in (":", "：", "—", "-"):
            if sep in ln:
                left, right = ln.split(sep, 1)
                if left.strip() and right.strip():
                    _add_entry(entries, left.strip(),
                               {"description": right.strip()})
                break


def _parse_pipe_table(lines, entries, notes):
    cells = []
    for ln in lines:
        parts = [c.strip() for c in ln.strip().strip("|").split("|")]
        cells.append(parts)
    if not cells:
        return
    # find header row (skip separator rows of dashes)
    header_idx = None
    for i, row in enumerate(cells):
        if row and not all(re.fullmatch(r"[-: ]*", c or "") for c in row):
            header_idx = i
            break
    if header_idx is None:
        return
    header = [str(h).strip() for h in cells[header_idx]]
    hl = [h.lower() for h in header]
    field_idx = None
    for want in ("field", "column", "name", "variable", "字段", "列名",
                 "变量", "变量名", "属性"):
        for i, h in enumerate(hl):
            if h == want or h.startswith(want):
                field_idx = i
                break
        if field_idx is not None:
            break
    if field_idx is None:
        field_idx = 0
    for row in cells[header_idx + 1:]:
        if not row or all(not c for c in row):
            continue
        if all(re.fullmatch(r"[-: ]*", c or "") for c in row):
            continue
        key = row[field_idx] if field_idx < len(row) else (row[0] if row else "")
        attrs = {}
        for i in range(len(header)):
            if i == field_idx or i >= len(row):
                continue
            h = header[i]
            v = row[i]
            if h and str(v).strip():
                attrs[h] = str(v).strip()
        _add_entry(entries, key, attrs)


def apply_semantics(tables, dictionaries):
    entries = {}
    dict_notes = []
    dict_errors = []
    for dpath in dictionaries:
        parsed = parse_dictionary(dpath)
        entries.update(parsed["entries"])
        dict_notes.extend(parsed["notes"])
        if parsed["error"]:
            dict_errors.append("%s: %s" % (dpath, parsed["error"]))
    semantics = []
    for t in tables:
        for c in t["columns"]:
            norm = normalize(c["name"])
            match = None
            for k, v in entries.items():
                if normalize(k) == norm:
                    match = v
                    break
            if match is None and entries:
                for k, v in entries.items():
                    if normalize(k) and normalize(k) in norm:
                        match = v
                        break
            semantics.append({
                "table": t["table"], "column": c["name"],
                "dictionary": match or {},
                "grounded": match is not None})
    return semantics, entries, dict_notes, dict_errors


# --------------------------------------------------------------------------- #
# SVG charts
# --------------------------------------------------------------------------- #
def render_histogram_svg(col, stats, table):
    vals = stats.get("numeric_values") or []
    if not vals:
        return None
    lo, hi = stats["min"], stats["max"]
    if hi <= lo:
        hi = lo + 1.0
    nb = 10
    width = (hi - lo) / nb
    counts = [0] * nb
    for v in vals:
        idx = int((v - lo) / width)
        if idx >= nb:
            idx = nb - 1
        if idx < 0:
            idx = 0
        counts[idx] += 1
    mx = max(counts) if counts else 1
    W, H = 640, 380
    ml, mr, mt, mb = 60, 20, 46, 56
    pw = W - ml - mr
    ph = H - mt - mb
    bw = pw / nb
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'viewBox="0 0 %d %d">' % (W, H, W, H)]
    parts.append('<text x="%d" y="22" font-family="monospace" font-size="14">'
                 '%s.%s</text>' % (ml, _xml_escape(table), _xml_escape(col)))
    for i, c in enumerate(counts):
        bh = c / mx * ph
        x = ml + i * bw
        y = mt + ph - bh
        parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                     'fill="steelblue" stroke="#333"/>' % (x, y, bw - 1, bh))
    parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#333"/>'
                 % (ml, mt + ph, W - mr, mt + ph))
    mean = stats.get("mean")
    if mean is not None:
        mxpos = ml + (mean - lo) / (hi - lo) * pw
        parts.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="red" '
                     'stroke-dasharray="4 3"/>' % (mxpos, mt, mxpos, mt + ph))
        parts.append('<text x="%.1f" y="%d" font-family="monospace" '
                     'font-size="12" fill="red">mean=%.4f</text>'
                     % (mxpos + 3, mt + 12, mean))
    parts.append('<text x="%d" y="%d" font-family="monospace" font-size="12">'
                 'n=%d  min=%.4f  max=%.4f  sd=%.4f</text>'
                 % (ml, H - 12, stats.get("n", 0), stats.get("min", 0),
                    stats.get("max", 0),
                    stats.get("stdev_sample") or 0))
    parts.append('</svg>')
    return "".join(parts)


def render_bar_svg(col, top_values, table):
    if not top_values:
        return None
    items = top_values[:12]
    labels = [str(t["value"])[:24] for t in items]
    counts = [t["count"] for t in items]
    mx = max(counts) if counts else 1
    n = len(items)
    W, H = 640, max(200, 60 + n * 26)
    ml, mr, mt, mb = 220, 40, 34, 26
    pw = W - ml - mr
    ph = H - mt - mb
    bh = ph / n * 0.7
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'viewBox="0 0 %d %d">' % (W, H, W, H)]
    parts.append('<text x="%d" y="22" font-family="monospace" font-size="14">'
                 '%s.%s (top categories)</text>' % (ml,
                                                    _xml_escape(table),
                                                    _xml_escape(col)))
    for i, (lab, c) in enumerate(zip(labels, counts)):
        y = mt + i * (ph / n)
        bw = c / mx * pw
        parts.append('<text x="%d" y="%.1f" font-family="monospace" '
                     'font-size="12" text-anchor="end">%s</text>'
                     % (ml - 6, y + bh / 2 + 4, _xml_escape(lab)))
        parts.append('<rect x="%d" y="%.1f" width="%.1f" height="%.1f" '
                     'fill="tomato" stroke="#333"/>' % (ml, y, bw, bh))
        parts.append('<text x="%.1f" y="%.1f" font-family="monospace" '
                     'font-size="12">%d</text>' % (ml + bw + 4,
                                                   y + bh / 2 + 4, c))
    parts.append('</svg>')
    return "".join(parts)


def render_heatmap_svg(pairs, names):
    if not pairs:
        return None
    n = len(names)
    cell = 44
    W = cell * (n + 1) + 40
    H = cell * (n + 1) + 40
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'viewBox="0 0 %d %d">' % (W, H, W, H)]
    parts.append('<text x="10" y="20" font-family="monospace" font-size="14">'
                 'Pearson correlation</text>')
    idx = {name: i for i, name in enumerate(names)}
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            if i == j:
                r = 1.0
            else:
                hit = next((p for p in pairs
                            if {p["x"], p["y"]} == {a, b}), None)
                r = hit["pearson"] if hit and hit["pearson"] is not None else None
            x = 40 + j * cell
            y = 40 + i * cell
            if r is None:
                color = "#e0e0e0"
                txt = "NA"
            else:
                v = max(-1.0, min(1.0, float(r)))
                color = "#d9534f" if v < 0 else "#3b82f6"
                alpha = abs(v)
                color = 'rgb(%d,%d,%d)' % _heat_rgb(v)
                txt = "%.2f" % v
            parts.append('<rect x="%d" y="%d" width="%d" height="%d" '
                         'fill="%s" stroke="#fff"/>' % (x, y, cell - 2,
                                                        cell - 2, color))
            parts.append('<text x="%d" y="%d" font-family="monospace" '
                         'font-size="10" text-anchor="middle">%s</text>'
                         % (x + cell / 2, y + cell / 2 + 4, txt))
    for i, name in enumerate(names):
        parts.append('<text x="%d" y="%d" font-family="monospace" font-size="9" '
                     'transform="rotate(-40 %d %d)" text-anchor="end">%s</text>'
                     % (40 + i * cell + cell / 2, 34,
                        40 + i * cell + cell / 2, 34, _xml_escape(name[:18])))
        parts.append('<text x="%d" y="%d" font-family="monospace" font-size="9" '
                     'text-anchor="end">%s</text>'
                     % (34, 40 + i * cell + cell / 2 + 4,
                        _xml_escape(name[:18])))
    parts.append('</svg>')
    return "".join(parts)


def _heat_rgb(v):
    v = max(-1.0, min(1.0, v))
    if v >= 0:
        r = int(255 - 180 * v)
        g = int(255 - 130 * v)
        b = 255
    else:
        r = 255
        g = int(255 + 180 * v)
        b = int(255 + 130 * v)
    return r, g, b


# --------------------------------------------------------------------------- #
# query execution
# --------------------------------------------------------------------------- #
_TABLE_RE = re.compile(r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_]*|\"[^\"]+\")",
                       re.IGNORECASE)


def _referenced_tables(sql):
    out = []
    for m in _TABLE_RE.finditer(sql):
        t = m.group(1).strip('"')
        if t.lower() != "from" and t.lower() != "join":
            out.append(t)
    return out


def execute_query(conn, sql, max_rows=MAX_QUERY_ROWS):
    clean = sql.rstrip(";").strip()
    lower = clean.lower()
    if not (lower.startswith(("select", "with", "pragma", "explain"))):
        return {"rows": [], "columns": [], "row_count": 0,
                "error": "non read-only statement rejected"}
    try:
        cur = conn.execute(clean)
        columns = [d[0] for d in (cur.description or [])]
        rows = cur.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        total = None
        if lower.startswith(("select", "with")):
            try:
                total = conn.execute(
                    "SELECT COUNT(*) FROM (%s)" % clean).fetchone()[0]
            except Exception:
                total = len(rows) if not truncated else None
        else:
            total = len(rows)
        return {"rows": [list(r) for r in rows], "columns": columns,
                "row_count": total, "truncated": truncated, "error": None}
    except Exception as e:  # noqa: BLE001
        return {"rows": [], "columns": [], "row_count": None, "error": str(e)}


def route_and_execute(catalog, sql):
    refs = _referenced_tables(sql)
    candidates = []
    for entry in catalog:
        tables = [t["table"] for t in entry["schema"]]
        if refs and all(any(t.lower() == r.lower() for t in tables)
                        for r in refs):
            candidates.append(entry)
        elif not refs and entry["kind"] == "sqlite":
            candidates.append(entry)
    if len(candidates) == 1:
        res = execute_query(candidates[0]["conn"], sql)
        return res, candidates[0]["label"], None
    if len(candidates) > 1:
        # ambiguous: prefer the single source containing all referenced tables
        res, label, err = None, None, "ambiguous routing: %d sources"
        return res, label, err % len(candidates)
    # try each in catalog order and take first success (record routing note)
    last_err = None
    for entry in catalog:
        res = execute_query(entry["conn"], sql)
        if res["error"] is None:
            return res, entry["label"], ("routed by first-success: %s"
                                         % entry["label"])
        last_err = res["error"]
    return None, None, "no source could execute: %s" % last_err


def load_queries(queries_path):
    if not queries_path or not os.path.exists(queries_path):
        return [], None
    obj, err = read_json(queries_path)
    if err:
        return [], err
    if isinstance(obj, list):
        return obj, None
    if isinstance(obj, dict):
        return [obj], None
    return [], "queries.json must be a list or object"


# --------------------------------------------------------------------------- #
# contract resolution (transparent, never invents numbers)
# --------------------------------------------------------------------------- #
_STAT_WORDS = {
    "mean": "mean", "avg": "mean", "average": "mean", "平均值": "mean",
    "min": "min", "minimum": "min", "最小值": "min",
    "max": "max", "maximum": "max", "最大值": "max",
    "std": "stdev_sample", "stdev": "stdev_sample", "sd": "stdev_sample",
    "标准差": "stdev_sample", "deviation": "stdev_sample",
    "median": "median", "中位数": "median",
    "q1": "q1", "q3": "q3", "sum": "sum", "total": "sum",
    "count": "n_nonnull", "missing": "n_missing", "distinct": "n_distinct",
    "unique": "n_distinct", "缺失": "n_missing", "计数": "n_nonnull",
}


def _build_column_index(analysis):
    index = {}
    for t in analysis["schema"]:
        for c in t["columns"]:
            norm = normalize(c["name"])
            if norm:
                index.setdefault(norm, []).append(
                    {"table": t["table"], "column": c["name"]})
    return index


def resolve_contract(contract, analysis):
    col_index = _build_column_index(analysis)
    profile_map = {}
    for t in analysis["profiles"]:
        for p in t["columns"]:
            profile_map.setdefault(normalize(p["name"]), []).append(
                {"table": t["table"], "profile": p})
    corr_map = {}
    for pr in analysis.get("correlation", []):
        corr_map[(normalize(pr["x"]), normalize(pr["y"]))] = pr["pearson"]
    total_rows = sum(t["row_count"] or 0 for t in analysis["schema"])
    resolved = {}
    for key in contract:
        k = normalize(key)
        value = None
        confidence = None
        reason = None
        # correlations between two known columns
        matched_cols = [n for n in col_index if n and n in k]
        if ("corr" in k or "correlation" in k or "相关" in k) \
                and len(matched_cols) >= 2:
            pair = None
            for i in range(len(matched_cols)):
                for j in range(i + 1, len(matched_cols)):
                    hit = corr_map.get((matched_cols[i], matched_cols[j]))
                    if hit is None:
                        hit = corr_map.get((matched_cols[j], matched_cols[i]))
                    if hit is not None:
                        pair = hit
                        break
                if pair is not None:
                    break
            if pair is not None:
                value, confidence, reason = pair, "high", \
                    "pearson of %s" % ", ".join(matched_cols[:2])
        # single-column statistics
        if value is None and matched_cols:
            # "n_<col>" / "num_<col>" / "number of <col>" -> distinct count
            if k.startswith(("n ", "num ", "no ", "number ")) or \
                    any(w in k for w in ("number of", "个数", "数量",
                                         "类别数", "种类数")):
                cands = []
                for n in matched_cols:
                    for pm in profile_map.get(n, []):
                        prof = pm["profile"]
                        if prof.get("n_distinct") is not None:
                            cands.append((prof["n_distinct"], pm["table"], n))
                if len(cands) == 1:
                    value, confidence, reason = cands[0][0], "high", \
                        "n_distinct of %s" % matched_cols[0]
                elif len(cands) > 1:
                    confidence, reason = "medium", \
                        "n_distinct ambiguous across %d columns" % len(cands)
            stat = None
            for w, field in _STAT_WORDS.items():
                if w in k:
                    stat = field
                    break
            if value is None and stat:
                cands = []
                for n in matched_cols:
                    for pm in profile_map.get(n, []):
                        prof = pm["profile"]
                        if prof.get("kind") == "numeric":
                            num = prof.get("numeric", {})
                            if stat in num and num[stat] is not None:
                                cands.append((num[stat], pm["table"], n, stat))
                            elif stat == "n_nonnull":
                                cands.append((prof["n_nonnull"], pm["table"],
                                              n, stat))
                        elif stat == "n_nonnull":
                            # "count of <categorical col>" -> distinct classes
                            cands.append((prof.get("n_distinct"), pm["table"],
                                          n, "n_distinct"))
                if len(cands) == 1:
                    value, confidence, reason = cands[0][0], "high", \
                        "%s of %s" % (cands[0][3], matched_cols[0])
                elif len(cands) > 1:
                    confidence, reason = "medium", \
                        "%s ambiguous across %d columns" % (stat, len(cands))
            elif value is None and any(w in k for w in
                                       ("mode", "most", "众数", "多数",
                                        "majority")):
                cands = []
                for n in matched_cols:
                    for pm in profile_map.get(n, []):
                        prof = pm["profile"]
                        tv = prof.get("top_values") or []
                        if tv:
                            top_count = tv[0]["count"]
                            ties = [t for t in tv if t["count"] == top_count]
                            cands.append((tv[0]["value"], pm["table"], n,
                                          top_count, len(ties)))
                if len(cands) == 1:
                    ties = cands[0][4]
                    if ties == 1:
                        value, confidence, reason = cands[0][0], "high", \
                            "mode of %s (count=%s)" % (matched_cols[0],
                                                       cands[0][3])
                    else:
                        confidence, reason = "medium", \
                            "mode of %s tied across %d values" \
                            % (matched_cols[0], ties)
        # row / sample count
        if value is None and k and any(w in k for w in
                                       ("row", "sample", "observ", "record",
                                        "样本", "记录", "行", "count")):
            if total_rows is not None and total_rows >= 0:
                value, confidence, reason = total_rows, "high", \
                    "sum of table row counts"
        # duplicates
        if value is None and "duplicate" in k:
            dups = [t.get("duplicates", {}) for t in analysis["tables"]]
            grps = [d.get("n_duplicate_groups") for d in dups
                    if d.get("n_duplicate_groups") is not None]
            if grps:
                value, confidence, reason = sum(grps), "high", \
                    "sum of duplicate groups"
        if value is not None:
            resolved[key] = {"value": value, "confidence": confidence,
                             "reason": reason}
    return resolved


# --------------------------------------------------------------------------- #
# analysis pipeline (IR builder)
# --------------------------------------------------------------------------- #
def compute_analysis(catalog, discovery, dictionaries):
    tables = []
    profiles = []
    correlations = []
    chart_data = []
    evidence = []
    for entry in catalog:
        for t in entry["schema"]:
            table = t["table"]
            cols = t["columns"]
            numeric_cols = []
            profs = []
            dup = detect_duplicates(entry["conn"], table, cols)
            evidence.append({
                "claim": "table %s has %d columns and %d rows"
                         % (table, len(cols), t["row_count"] or 0),
                "locator": "%s#table=%s" % (entry["path"], table),
                "quote": "columns=%s; row_count=%s"
                         % ([c["name"] for c in cols], t["row_count"]),
                "role": "schema"})
            if dup.get("n_duplicate_groups"):
                evidence.append({
                    "claim": "table %s has %d exact duplicate groups"
                             % (table, dup["n_duplicate_groups"]),
                    "locator": dup["full_row_dup_sql"],
                    "quote": "n_duplicate_groups=%d; n_rows_in_groups=%d"
                             % (dup["n_duplicate_groups"],
                                dup["n_rows_in_duplicate_groups"]),
                    "role": "data-quality"})
            for c in cols:
                prof = column_profile(entry["conn"], table, c)
                profs.append(prof)
                if prof.get("kind") == "numeric" and prof.get("numeric") \
                        and not prof.get("low_cardinality"):
                    numeric_cols.append({"name": c["name"]})
                if prof.get("kind") in ("numeric", "text", "boolean"):
                    st = prof.get("numeric") or {}
                    agg = prof.get("aggregate")
                    q = ("n=%s; mean=%s; sd=%s; min=%s; max=%s"
                         % (st.get("n"), st.get("mean"),
                            st.get("stdev_sample"), st.get("min"),
                            st.get("max"))) if prof.get("kind") == "numeric" \
                        else ("n_nonnull=%s; n_distinct=%s"
                              % (prof["n_nonnull"], prof["n_distinct"]))
                    loc = (agg["sql"] if agg and "error" not in agg
                           else prof["extraction_sql"])
                    if prof.get("kind") == "numeric" and agg \
                            and "error" not in agg:
                        evidence.append({
                            "claim": "column %s.%s aggregate cross-check"
                                     % (table, c["name"]),
                            "locator": agg["sql"],
                            "quote": ("avg=%s; matches_python=%s; "
                                      "conversion=%s"
                                      % (agg.get("avg"),
                                         agg.get("matches_python"),
                                         agg.get("conversion"))),
                            "role": "verification"})
                    evidence.append({
                        "claim": "column %s.%s profile" % (table, c["name"]),
                        "locator": loc,
                        "quote": q + ("; coded_as_text=%s"
                                      % prof.get("coded_as_text")),
                        "role": "statistic"})
            pairs = correlate_table(entry["conn"], table, numeric_cols)
            correlations.extend(
                {"table": table, "x": p["x"], "y": p["y"],
                 "pearson": p["pearson"], "n": p["n"], "note": p["note"]}
                for p in pairs)
            tables.append({"table": table, "source": entry["path"],
                           "kind": entry["kind"], "row_count": t["row_count"],
                           "columns": cols, "duplicates": dup})
            profiles.append({"table": table, "columns": profs})
            for prof in profs:
                if prof.get("kind") == "numeric" \
                        and not prof.get("low_cardinality"):
                    chart_profile = dict(prof.get("numeric") or {})
                    chart_profile["numeric_values"] = \
                        prof.get("numeric_values", [])
                    chart_data.append({"kind": "histogram", "table": table,
                                       "column": prof["name"],
                                       "profile": chart_profile})
                elif prof.get("kind") in ("text", "boolean") \
                        or prof.get("low_cardinality"):
                    if prof.get("top_values"):
                        chart_data.append({"kind": "bar", "table": table,
                                           "column": prof["name"],
                                           "profile": prof})
            if numeric_cols:
                chart_data.append({"kind": "heatmap", "table": table,
                                   "names": [c["name"] for c in numeric_cols],
                                   "pairs": pairs})
    return {
        "schema": tables,
        "tables": tables,
        "profiles": profiles,
        "correlation": correlations,
        "chart_data": chart_data,
        "evidence": evidence,
        "numeric_column_count": sum(
            1 for p in profiles for c in p["columns"]
            if c.get("kind") == "numeric"),
        "table_row_counts": [{"table": t["table"], "row_count": t["row_count"]}
                             for t in tables],
    }
