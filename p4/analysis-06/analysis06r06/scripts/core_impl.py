#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl: 纯标准库只读数据发现、画像、中间表示与可编辑 SVG 图表。

本模块不写任何最终结果文件，只返回结构化对象，供 main.py 落盘；
selftest.py 也会复用其中的读取与校验函数。
"""

import csv
import io
import json
import math
import os
import re
import sqlite3
import statistics
import urllib.parse
from pathlib import Path

TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")
MAX_TEXT_BYTES = 2_000_000
NUMERIC_DECL_RE = re.compile(r"int|real|float|double|num|decimal|money", re.I)
ALLOWED_SQL_HEAD = ("select", "with", "pragma", "explain")
MAX_QUERY_ROWS = 200
MAX_VALUE_COUNTS = 12
MAX_BAR_CATS = 20
MAX_SCATTER_POINTS = 1500


# --------------------------------------------------------------------------- #
# 文本 / JSON 读取
# --------------------------------------------------------------------------- #
def read_text(path, max_bytes=MAX_TEXT_BYTES):
    """返回 (text, encoding, error)。坏编码按序回退；超限或不可读返回错误。"""
    p = Path(path)
    try:
        data = p.read_bytes()
    except Exception as exc:  # noqa: BLE001
        return "", None, "read_failed: %s" % exc
    if len(data) > max_bytes:
        return "", None, "too_large: %d bytes > %d" % (len(data), max_bytes)
    for enc in TEXT_ENCODINGS:
        try:
            return data.decode(enc), enc, None
        except UnicodeDecodeError:
            continue
    # 兜底：丢弃非法字节
    return data.decode("latin-1", errors="replace"), "latin-1", None


def load_json_loose(path):
    """返回 (obj, error)。obj 在失败时为 None。"""
    text, enc, err = read_text(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001
        return None, "bad_json: %s (encoding=%s)" % (exc, enc)


# --------------------------------------------------------------------------- #
# 输入发现
# --------------------------------------------------------------------------- #
def discover_inputs(input_dir):
    """扫描输入目录，返回分类清单（路径字符串）。"""
    base = Path(input_dir)
    result = {
        "case_file": None,
        "task_file": None,
        "db_files": [],
        "csv_files": [],
        "dict_files": [],
        "other_files": [],
    }
    if not base.exists():
        return result
    files = sorted(str(p) for p in base.rglob("*") if p.is_file())
    db_exts = (".db", ".sqlite", ".sqlite3", ".db3")
    csv_exts = (".csv", ".tsv", ".txt", ".data")
    for f in files:
        name = os.path.basename(f).lower()
        ext = os.path.splitext(name)[1]
        if name == "case.json":
            result["case_file"] = f
        elif name == "task.md":
            result["task_file"] = f
        elif ext in db_exts:
            result["db_files"].append(f)
        elif ext in csv_exts:
            result["csv_files"].append(f)
        elif ext in (".md", ".yaml", ".yml", ".json", ".txt"):
            result["dict_files"].append(f)
        else:
            result["other_files"].append(f)
    return result


def load_case(discovery):
    """读取 case.json（缺失则回退任意 json），返回 (case, file, error)。"""
    cand = discovery.get("case_file")
    if cand is None:
        jsons = [f for f in discovery.get("other_files", []) +
                 discovery.get("dict_files", [])
                 if f.lower().endswith(".json")]
        cand = jsons[0] if jsons else None
    if cand is None:
        return None, None, "no_case_json"
    obj, err = load_json_loose(cand)
    if err:
        return None, cand, err
    if not isinstance(obj, dict):
        return None, cand, "case_json_not_object"
    return obj, cand, None


def extract_contract(case):
    """从 case.json 提取 output_contract；缺失或非 dict 时返回 {}。"""
    if not isinstance(case, dict):
        return {}
    c = case.get("output_contract")
    return c if isinstance(c, dict) else {}


# --------------------------------------------------------------------------- #
# 只读 SQLite
# --------------------------------------------------------------------------- #
def sqlite_uri(path):
    p = str(Path(path).resolve())
    return "file:" + urllib.parse.quote(p, safe="/\\:") + "?mode=ro"


def connect_ro(path):
    """只读连接；优先 URI mode=ro，失败则普通连接 + query_only。"""
    last_err = None
    try:
        conn = sqlite3.connect(sqlite_uri(path), uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn
    except Exception as exc:  # noqa: BLE001
        last_err = exc
    try:
        conn = sqlite3.connect(str(path), timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn
    except Exception as exc:  # noqa: BLE001
        last_err = exc
    raise last_err


def quote_ident(name):
    return '"%s"' % str(name).replace('"', '""')


def list_tables(conn):
    rows = conn.execute(
        "SELECT name, type FROM sqlite_master "
        "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
        "ORDER BY type, name").fetchall()
    return [{"name": r["name"], "type": r["type"]} for r in rows]


def table_row_count(conn, table):
    q = "SELECT COUNT(*) AS n FROM %s" % quote_ident(table)
    return int(conn.execute(q).fetchone()["n"])


def inspect_schema(conn):
    """返回每表 {table,type,row_count,columns,fks,indexes}。"""
    tables = []
    for t in list_tables(conn):
        name = t["name"]
        cols = []
        for r in conn.execute("PRAGMA table_info(%s)" % quote_ident(name)):
            cols.append({
                "cid": r["cid"], "name": r["name"], "type": r["type"],
                "notnull": int(r["notnull"] or 0), "pk": int(r["pk"] or 0),
                "dflt_value": r["dflt_value"],
            })
        fks = []
        try:
            for r in conn.execute("PRAGMA foreign_key_list(%s)" % quote_ident(name)):
                fks.append({"id": r["id"], "seq": r["seq"], "table": r["table"],
                            "from": r["from"], "to": r["to"]})
        except Exception:  # noqa: BLE001
            pass
        indexes = []
        try:
            for r in conn.execute("PRAGMA index_list(%s)" % quote_ident(name)):
                indexes.append({"name": r["name"], "unique": int(r["unique"] or 0)})
        except Exception:  # noqa: BLE001
            pass
        tables.append({
            "table": name, "type": t["type"], "row_count": table_row_count(conn, name),
            "columns": cols, "fks": fks, "indexes": indexes,
        })
    return tables


def _is_nullish(v):
    return v is None or (isinstance(v, str) and v.strip() == "")


def parse_number(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        try:
            return float(v)
        except Exception:  # noqa: BLE001
            return None
    s = str(v).strip()
    if s == "":
        return None
    try:
        return float(s)
    except Exception:  # noqa: BLE001
        return None


def classify_column(decl_type, values):
    """返回 kind ∈ numeric|coded_as_text|categorical|empty。"""
    nonnull = [v for v in values if not _is_nullish(v)]
    if not nonnull:
        return "empty"
    nums = [parse_number(v) for v in nonnull]
    n_num = sum(1 for x in nums if x is not None)
    ratio = n_num / len(nonnull)
    decl = (decl_type or "").lower()
    if NUMERIC_DECL_RE.search(decl):
        return "numeric" if ratio >= 0.9 else "categorical"
    if ratio >= 0.9:
        return "coded_as_text"
    return "categorical"


def column_profile(conn, table, column, decl_type):
    """对单列做只读画像，返回可序列化 dict（不含原始值列表）。"""
    qname = "%s.%s" % (quote_ident(table), quote_ident(column))
    sql_extract = "SELECT %s AS v FROM %s" % (quote_ident(column), quote_ident(table))
    rows = conn.execute(sql_extract).fetchall()
    values = [r["v"] for r in rows]
    n_total = len(values)
    nonnull = [v for v in values if not _is_nullish(v)]
    n_missing = n_total - len(nonnull)
    kind = classify_column(decl_type, values)
    distinct = len(set(str(v) for v in nonnull))
    profile = {
        "column": column, "decl_type": decl_type, "kind": kind,
        "n_total": n_total, "n_nonnull": len(nonnull), "n_missing": n_missing,
        "n_distinct": distinct,
        "extraction_sql": sql_extract,
        "missing_sql": "SELECT COUNT(*) AS n FROM %s WHERE %s IS NULL OR CAST(%s AS TEXT)=''"
                       % (quote_ident(table), quote_ident(column), quote_ident(column)),
    }
    # top 值（按频次降序、值升序，保证确定性）
    counter = {}
    for v in nonnull:
        key = str(v)
        counter[key] = counter.get(key, 0) + 1
    top = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_VALUE_COUNTS]
    profile["top_values"] = [{"value": k, "count": c} for k, c in top]
    profile["value_counts_sql"] = (
        "SELECT CAST(%s AS TEXT) AS value, COUNT(*) AS n FROM %s "
        "WHERE %s IS NOT NULL AND CAST(%s AS TEXT)<>'' "
        "GROUP BY 1 ORDER BY n DESC, value ASC LIMIT %d"
        % (quote_ident(column), quote_ident(table), quote_ident(column),
           quote_ident(column), MAX_VALUE_COUNTS))
    if kind in ("numeric", "coded_as_text"):
        nums = [x for x in (parse_number(v) for v in nonnull) if x is not None]
        st = numeric_stats(nums)
        profile["numeric"] = {
            "values": nums,
            "stats": st,
            "conversion_rule": "text values parsed as float; non-numeric/blank treated as missing"
                              if kind == "coded_as_text" else "declared numeric column",
        }
        agg = None
        if kind == "numeric":
            try:
                r = conn.execute(
                    "SELECT COUNT(*) AS n, AVG(%s) AS avg, MIN(%s) AS mn, MAX(%s) AS mx "
                    "FROM %s WHERE %s IS NOT NULL"
                    % (quote_ident(column), quote_ident(column),
                       quote_ident(column), quote_ident(table), quote_ident(column))
                ).fetchone()
                n_sql, avg_sql = r["n"], r["avg"]
                mn_sql, mx_sql = r["mn"], r["mx"]
                py_mean = st["mean"] if st else None
                matches = None
                if avg_sql is not None and py_mean is not None:
                    matches = abs(float(avg_sql) - float(py_mean)) < 1e-9
                agg = {"sql": "SELECT COUNT(*),AVG(%s),MIN(%s),MAX(%s) FROM %s WHERE %s IS NOT NULL"
                             % (quote_ident(column), quote_ident(column),
                                quote_ident(column), quote_ident(table), quote_ident(column)),
                       "n": n_sql, "avg": avg_sql, "min": mn_sql, "max": mx_sql,
                       "matches_python": matches}
            except Exception as exc:  # noqa: BLE001
                agg = {"error": str(exc)}
        profile["aggregate"] = agg
    return profile


def numeric_stats(values):
    n = len(values)
    if n == 0:
        return None
    mean = float(statistics.fmean(values))
    stdev = float(statistics.stdev(values)) if n >= 2 else None
    s = sorted(values)
    def q(q_):
        if n == 1:
            return float(s[0])
        idx = (n - 1) * q_
        lo = int(math.floor(idx)); hi = int(math.ceil(idx))
        if lo == hi:
            return float(s[lo])
        return float(s[lo] * (hi - idx) + s[hi] * (idx - lo))
    q1, q3 = q(0.25), q(0.75)
    iqr = q3 - q1
    lo_f, hi_f = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    outliers = sorted(v for v in values if v < lo_f or v > hi_f)
    return {
        "n": n, "mean": mean, "stdev_sample": stdev,
        "min": float(s[0]), "max": float(s[-1]),
        "median": q(0.5), "q1": q1, "q3": q3, "iqr": iqr,
        "outlier_lower_fence": lo_f, "outlier_upper_fence": hi_f,
        "n_outliers_iqr15": len(outliers),
        "outlier_values": outliers[:20],
    }


def detect_duplicates(conn, table):
    cols = [r["name"] for r in conn.execute(
        "PRAGMA table_info(%s)" % quote_ident(table))]
    if not cols:
        return {"n_duplicate_groups": 0, "n_rows_in_duplicate_groups": 0,
                "full_row_dup_sql": None}
    col_list = ", ".join(quote_ident(c) for c in cols)
    sql = ("SELECT %s, COUNT(*) AS n FROM %s GROUP BY %s HAVING COUNT(*)>1 "
           "ORDER BY n DESC, %s ASC"
           % (col_list, quote_ident(table), col_list,
              quote_ident(cols[0])))
    groups = conn.execute(sql).fetchall()
    n_rows = sum(int(g["n"]) for g in groups)
    return {"n_duplicate_groups": len(groups), "n_rows_in_duplicate_groups": n_rows,
            "full_row_dup_sql": sql, "max_group_size": (int(groups[0]["n"]) if groups else 0)}


def correlate(numeric_series):
    """numeric_series: list of (name, [float])，返回 Pearson 矩阵与配对列表。"""
    names = [n for n, _ in numeric_series]
    n = len(names)
    matrix = [[None] * n for _ in range(n)]
    pairs = []
    for i in range(n):
        for j in range(n):
            if i > j:
                continue
            xs = numeric_series[i][1]; ys = numeric_series[j][1]
            r = pearson(xs, ys)
            matrix[i][j] = r
            if j != i:
                matrix[j][i] = r
            if i != j:
                pairs.append({"var1": names[i], "var2": names[j], "pearson_r": r,
                              "n_pairs": min(len(xs), len(ys))})
    return matrix, pairs


def pearson(xs, ys):
    n = min(len(xs), len(ys))
    if n < 2:
        return None
    xs = xs[:n]; ys = ys[:n]
    mx = statistics.fmean(xs); my = statistics.fmean(ys)
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    dx = math.sqrt(sum((a - mx) ** 2 for a in xs))
    dy = math.sqrt(sum((b - my) ** 2 for b in ys))
    if dx == 0 or dy == 0:
        return None
    return float(num / (dx * dy))


# --------------------------------------------------------------------------- #
# CSV 快照读取
# --------------------------------------------------------------------------- #
def read_csv_as_table(path):
    """嗅探分隔符/表头并读取 CSV，返回 (columns, rows, error)。"""
    text, _enc, err = read_text(path)
    if err:
        return None, None, err
    sample = text[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except Exception:  # noqa: BLE001
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    try:
        header = next(reader)
    except StopIteration:
        return None, None, "empty_csv"
    header = [str(h).strip() for h in header]
    rows = []
    for row in reader:
        if not row or all(str(c).strip() == "" for c in row):
            continue
        rows.append([c for c in row])
    return header, rows, None


def profile_csv_table(name, header, rows):
    """把 CSV 视为表做列画像（数值列同样做 Python 统计）。"""
    n = len(rows)
    cols = []
    for idx, h in enumerate(header):
        values = [r[idx] if idx < len(r) else None for r in rows]
        nonnull = [v for v in values if not _is_nullish(v)]
        kind = classify_column("text", values)
        prof = {"column": h, "decl_type": "text", "kind": kind,
                "n_total": n, "n_nonnull": len(nonnull),
                "n_missing": n - len(nonnull),
                "n_distinct": len(set(str(v) for v in nonnull)),
                "extraction_sql": None,
                "missing_sql": None, "value_counts_sql": None}
        counter = {}
        for v in nonnull:
            counter[str(v)] = counter.get(str(v), 0) + 1
        top = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_VALUE_COUNTS]
        prof["top_values"] = [{"value": k, "count": c} for k, c in top]
        if kind in ("numeric", "coded_as_text"):
            nums = [x for x in (parse_number(v) for v in nonnull) if x is not None]
            prof["numeric"] = {"stats": numeric_stats(nums),
                               "conversion_rule": "text values parsed as float; non-numeric/blank treated as missing"}
            prof["aggregate"] = None
        cols.append(prof)
    return {"table": name, "type": "csv", "row_count": n,
            "columns": [{"cid": i, "name": h, "type": "text", "notnull": 0,
                         "pk": 0, "dflt_value": None}
                        for i, h in enumerate(header)],
            "fks": [], "indexes": [], "profiles": cols}


# --------------------------------------------------------------------------- #
# 只读 SQL 安全执行
# --------------------------------------------------------------------------- #
def safe_sql(sql):
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        return False, "empty_sql"
    head = re.split(r"[\s(]", text, maxsplit=1)[0].lower()
    if head not in ALLOWED_SQL_HEAD:
        return False, "disallowed_statement: %s" % head
    return True, None


def run_query(conn, sql, max_rows=MAX_QUERY_ROWS):
    ok, err = safe_sql(sql)
    if not ok:
        return None, err
    try:
        cur = conn.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in (cur.description or [])]
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        return {"columns": cols,
                "rows": [tuple(r) for r in rows],
                "row_count": len(rows),
                "truncated": truncated}, None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


# --------------------------------------------------------------------------- #
# 可编辑 SVG 图表（确定性布局，图与数分离）
# --------------------------------------------------------------------------- #
def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _shorten(s, max_len=18):
    s = str(s)
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def bar_chart_svg(title, labels, counts, xlabel="category", ylabel="count"):
    if not labels:
        return None
    labels = labels[:MAX_BAR_CATS]
    counts = counts[:MAX_BAR_CATS]
    n = len(labels)
    W, H = 640, 320
    left, right, top, bottom = 60, 20, 30, 70
    plot_w = W - left - right
    plot_h = H - top - bottom
    mx = max(counts) or 1
    bar_w = plot_w / n * 0.7
    step = plot_w / n
    parts = []
    parts.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
                 'viewBox="0 0 %d %d">' % (W, H, W, H))
    parts.append('<style>text{font-family:sans-serif;font-size:11px}'
                 '.axis{stroke:#888;stroke-width:1}.bar{fill:#4c72b0}</style>')
    parts.append('<text x="%d" y="20" text-anchor="middle" font-size="13" '
                 'font-weight="bold">%s</text>' % (W // 2, _esc(_shorten(title, 80))))
    # y 轴与刻度
    parts.append('<line class="axis" x1="%d" y1="%d" x2="%d" y2="%d"/>'
                 % (left, top, left, top + plot_h))
    parts.append('<line class="axis" x1="%d" y1="%d" x2="%d" y2="%d"/>'
                 % (left, top + plot_h, left + plot_w, top + plot_h))
    for i in range(5):
        yv = mx * i / 4
        y = top + plot_h - (plot_h * i / 4)
        parts.append('<line class="axis" x1="%d" y1="%d" x2="%d" y2="%d" '
                     'stroke-dasharray="2,2"/>' % (left, y, left + plot_w, y))
        parts.append('<text x="%d" y="%d" text-anchor="end" font-size="10">%.4g</text>'
                     % (left - 4, y + 3, yv))
    for i, (lab, cnt) in enumerate(zip(labels, counts)):
        x = left + step * i + step * 0.15
        h = plot_h * (cnt / mx)
        y = top + plot_h - h
        parts.append('<rect class="bar" x="%.2f" y="%.2f" width="%.2f" height="%.2f">'
                     '<title>%s: %s</title></rect>'
                     % (x, y, bar_w, h, _esc(lab), cnt))
        parts.append('<text x="%.2f" y="%d" text-anchor="middle" font-size="9" '
                     'transform="rotate(-30 %.2f %d)">%s</text>'
                     % (x + bar_w / 2, top + plot_h + 14, x + bar_w / 2,
                        top + plot_h + 14, _esc(_shorten(lab, 14))))
    parts.append('<text x="%d" y="%d" text-anchor="middle">%s</text>'
                 % (left + plot_w / 2, H - 6, _esc(xlabel)))
    parts.append('<text x="14" y="%d" text-anchor="middle" '
                 'transform="rotate(-90 14 %d)">%s</text>'
                 % (top + plot_h / 2, top + plot_h / 2, _esc(ylabel)))
    parts.append('</svg>')
    return "".join(parts)


def histogram_svg(title, values, nbins=10):
    if not values:
        return None
    lo, hi = min(values), max(values)
    if lo == hi:
        lo, hi = lo - 0.5, hi + 0.5
    width = (hi - lo) / nbins
    bins = [0] * nbins
    for v in values:
        idx = int((v - lo) / width)
        if idx == nbins:
            idx -= 1
        if 0 <= idx < nbins:
            bins[idx] += 1
    labels = ["%.3g" % (lo + width * (i + 0.5)) for i in range(nbins)]
    return bar_chart_svg(title, labels, bins, xlabel="value (bin center)", ylabel="frequency")


def corr_heatmap_svg(title, names, matrix):
    n = len(names)
    if n < 2:
        return None
    cell = 34
    lab_w = 130
    W = lab_w + n * cell + 20
    H = 60 + n * cell + 20
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'viewBox="0 0 %d %d">' % (W, H, W, H)]
    parts.append('<style>text{font-family:sans-serif;font-size:10px}</style>')
    parts.append('<text x="%d" y="18" text-anchor="middle" font-size="13" '
                 'font-weight="bold">%s</text>' % (W // 2, _esc(_shorten(title, 90))))
    top0 = 40
    for i in range(n):
        for j in range(n):
            r = matrix[i][j]
            x = lab_w + j * cell
            y = top0 + i * cell
            if r is None:
                fill = "#eeeeee"
                txt = ""
            else:
                v = max(-1.0, min(1.0, r))
                if v >= 0:
                    fill = "#%02x%02x%02x" % (255, int(255 - 175 * v), int(255 - 175 * v))
                else:
                    fill = "#%02x%02x%02x" % (int(255 - 175 * abs(v)), int(255 - 175 * abs(v)), 255)
                txt = "%.2f" % r
            parts.append('<rect x="%d" y="%d" width="%d" height="%d" fill="%s" '
                         'stroke="#ccc"><title>%s vs %s: %s</title></rect>'
                         % (x, y, cell - 1, cell - 1, fill, _esc(names[i]),
                            _esc(names[j]), txt))
            if i == j:
                parts.append('<text x="%d" y="%d" text-anchor="middle">%s</text>'
                             % (x + cell / 2 - 0.5, y + cell / 2 + 3,
                                _esc(_shorten(names[i], 4))))
    # 行/列标签
    for i in range(n):
        parts.append('<text x="%d" y="%d" text-anchor="end" font-size="9">%s</text>'
                     % (lab_w - 4, top0 + i * cell + cell / 2 + 3,
                        _esc(_shorten(names[i], 16))))
        parts.append('<text x="%d" y="%d" text-anchor="middle" font-size="9" '
                     'transform="rotate(-45 %d %d)">%s</text>'
                     % (lab_w + i * cell + cell / 2, top0 - 8,
                        lab_w + i * cell + cell / 2, top0 - 8,
                        _esc(_shorten(names[i], 12))))
    parts.append('</svg>')
    return "".join(parts)


def scatter_svg(title, xs, ys, xname, yname):
    if not xs or len(xs) != len(ys):
        return None
    n = min(len(xs), MAX_SCATTER_POINTS)
    xs = xs[:n]; ys = ys[:n]
    W, H = 560, 360
    left, right, top, bottom = 60, 20, 30, 60
    plot_w = W - left - right
    plot_h = H - top - bottom
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    if x0 == x1:
        x0, x1 = x0 - 0.5, x1 + 0.5
    if y0 == y1:
        y0, y1 = y0 - 0.5, y1 + 0.5
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'viewBox="0 0 %d %d">' % (W, H, W, H)]
    parts.append('<style>text{font-family:sans-serif;font-size:11px}'
                 '.axis{stroke:#888;stroke-width:1}.pt{fill:#c44e52;opacity:0.6}</style>')
    parts.append('<text x="%d" y="20" text-anchor="middle" font-size="13" '
                 'font-weight="bold">%s</text>' % (W // 2, _esc(_shorten(title, 80))))
    parts.append('<rect x="%d" y="%d" width="%d" height="%d" fill="none" '
                 'stroke="#888"/>' % (left, top, plot_w, plot_h))
    for x, y in zip(xs, ys):
        px = left + plot_w * (x - x0) / (x1 - x0)
        py = top + plot_h - plot_h * (y - y0) / (y1 - y0)
        parts.append('<circle class="pt" cx="%.2f" cy="%.2f" r="2.2"/>' % (px, py))
    parts.append('<text x="%d" y="%d" text-anchor="middle">%s</text>'
                 % (left + plot_w / 2, H - 8, _esc(xname)))
    parts.append('<text x="14" y="%d" text-anchor="middle" '
                 'transform="rotate(-90 14 %d)">%s</text>'
                 % (top + plot_h / 2, top + plot_h / 2, _esc(yname)))
    parts.append('</svg>')
    return "".join(parts)


# --------------------------------------------------------------------------- #
# 中间表示构建
# --------------------------------------------------------------------------- #
def build_plan(case_id, discovery, contract, data_summary, field_semantics,
               join_candidates, task_notes, unresolved, checks):
    """data_summary: [{source, kind, table, row_count}]。"""
    return {
        "schema_version": "1.0.0",
        "case_id": case_id,
        "inputs": {
            "case_file": discovery.get("case_file"),
            "task_file": discovery.get("task_file"),
            "db_files": discovery.get("db_files"),
            "csv_files": discovery.get("csv_files"),
            "dict_files": discovery.get("dict_files"),
        },
        "contract_keys": sorted(contract.keys()) if contract else [],
        "task_requirements": task_notes,
        "data_sources": data_summary,
        "field_semantics": field_semantics,
        "join_candidates": join_candidates,
        "steps": [
            {"id": "p0", "phase": "discover", "goal": "定位输入与数据快照",
             "method": "扫描 /input 全部文件", "outputs": ["result.json skeleton"]},
            {"id": "p1", "phase": "schema", "goal": "只读连接并检索 schema",
             "method": "PRAGMA table_info / foreign_key_list / index_list + COUNT(*)",
             "outputs": ["schema.json"]},
            {"id": "p2", "phase": "profile", "goal": "列画像与数据质量",
             "method": "逐列缺失/重复/类型/分布统计；编码型字段显式转数值",
             "outputs": ["data_quality.csv", "summary_stats.csv", "value_counts.json"]},
            {"id": "p3", "phase": "analyze", "goal": "描述统计、相关与可视化",
             "method": "Python 统计 + SQL 聚合交叉核对；Pearson 相关；SVG 图",
             "outputs": ["correlation_matrix.csv", "figure_model.json", "figures/*.svg"]},
            {"id": "p4", "phase": "cross_table", "goal": "跨表连接键与膨胀控制",
             "method": "FK 优先、名称匹配与取值重叠推断候选；记录膨胀比",
             "outputs": ["analysis.cross_table"]},
            {"id": "p5", "phase": "finalize", "goal": "自检与收口",
             "method": "scripts/selftest.py 校验；补齐 contract 字段",
             "outputs": ["result.json final", "report.md", "evidence_map.json"]},
        ],
        "checks": checks,
        "unresolved": unresolved,
    }


def build_figure_model(figures):
    return {"schema_version": "1.0.0", "figures": figures}


# --------------------------------------------------------------------------- #
# 确定性 JSON 写出
# --------------------------------------------------------------------------- #
def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=_json_default)
    return path


def _json_default(o):
    if isinstance(o, (Path,)):
        return str(o)
    raise TypeError("not serializable: %r" % o)


def now_ts():
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def quote_task_lines(task_text, max_chars=800):
    lines = [ln.strip() for ln in (task_text or "").splitlines() if ln.strip()]
    picked, budget = [], 0
    for ln in lines:
        if budget + len(ln) > max_chars:
            break
        picked.append(ln)
        budget += len(ln)
    return picked


def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def field_semantics_from_profiles(table, profiles):
    out = []
    for prof in profiles:
        entry = {"table": table, "column": prof["column"], "kind": prof["kind"],
                 "unit": None, "meaning": None, "basis": "type_inference",
                 "confidence": "low"}
        if prof["kind"] == "coded_as_text":
            entry["conversion_rule"] = "text values parsed as float; non-numeric/blank treated as missing"
            entry["basis"] = "type_inference: declared text but all non-null values numeric"
            entry["confidence"] = "high"
        if prof["kind"] == "categorical":
            entry["distinct_values"] = prof["n_distinct"]
            entry["top_values"] = prof.get("top_values", [])[:5]
        if prof["kind"] in ("numeric", "coded_as_text"):
            st = (prof.get("numeric") or {}).get("stats") or {}
            entry["observed_range"] = [st.get("min"), st.get("max")]
        out.append(entry)
    return out
