#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科研数据可视化 Skill 共享实现（仅 Python 标准库）。

提供 main.py 依赖的无状态工具：输入发现、契约提取、数据画像、图表推荐、
产物格式校验。所有函数确定性运行，无随机源、无外部依赖。
"""
import csv
import datetime
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import statistics
import struct
import tempfile
import xml.etree.ElementTree as ET

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TABLE_ROWS = 200_000
TEXT_EXTS = (".csv", ".tsv", ".txt", ".md", ".json", ".yaml", ".yml")
DATA_EXTS = (".db", ".sqlite", ".sqlite3")
DELIMITED_EXTS = (".csv", ".tsv")
MISSING_TOKENS = {"", "na", "n/a", "nan", "null", "none", "missing", "-", "?"}
CHART_TYPES = ("histogram", "scatter", "heatmap", "box", "violin", "line", "bar",
               "strip", "scatter_matrix", "correlation_heatmap", "qq", "ecdf")


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def read_text_robust(path, limit=MAX_FILE_BYTES):
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return "", None, "unreadable: %s" % e
    if size > limit:
        return "", None, "file_too_large(%d > %d)" % (size, limit)
    last = ""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), enc, None
        except UnicodeDecodeError as e:
            last = "%s: %s" % (enc, e)
        except OSError as e:
            return "", None, "unreadable: %s" % e
    return "", None, "undecodable: " + last


def atomic_write_text(path, text):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_", suffix=os.path.splitext(path)[1])
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def atomic_write_json(path, obj):
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    return path


def load_json_robust(path):
    text, enc, err = read_text_robust(path)
    if err:
        return {}, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return {}, "bad_json: %s" % e
    return {}, "unknown"


def utcnow_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def content_hash(text):
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def set_path(obj, dotted, value):
    parts = [p for p in dotted.split(".") if p]
    cur = obj
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value
    return obj


def get_path(obj, dotted):
    cur = obj
    for p in [p for p in dotted.split(".") if p]:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return None
    return cur


def has_path(obj, dotted):
    """判断路径是否存在（只看键是否在场，与值是否为 None/[] 无关）。"""
    cur = obj
    for p in [p for p in dotted.split(".") if p]:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return False
    return True


# --------------------------------------------------------------------------- #
# 输入发现
# --------------------------------------------------------------------------- #
def discover_input(input_dir):
    files = {"case_json": None, "task_md": None, "data": [], "materials": []}
    deg = []
    if not os.path.isdir(input_dir):
        return files, ["input_dir_missing: %s" % input_dir]
    for root, _dirs, names in os.walk(input_dir):
        for name in sorted(names):
            p = os.path.join(root, name)
            rel = os.path.relpath(p, input_dir).replace("\\", "/")
            base = name.lower()
            try:
                size = os.path.getsize(p)
            except OSError as e:
                deg.append("unreadable: %s (%s)" % (rel, e))
                continue
            if base == "case.json":
                files["case_json"] = p
            elif base == "task.md":
                files["task_md"] = p
            elif base.endswith(DATA_EXTS) or base.endswith(DELIMITED_EXTS):
                files["data"].append(p)
            elif base.endswith(TEXT_EXTS):
                files["materials"].append(p)
            if size > MAX_FILE_BYTES and base.endswith(DATA_EXTS):
                deg.append("file_too_large(skipped): %s (%d bytes)" % (rel, size))
    return files, deg


# --------------------------------------------------------------------------- #
# 契约提取
# --------------------------------------------------------------------------- #
CONTRACT_KEYS = ("output_contract", "outputcontract", "contract", "result_contract",
                 "result_schema", "expected_output", "output", "result")


def find_contract(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = re.sub(r"[^a-z0-9]", "", str(k).lower())
            if kl in CONTRACT_KEYS and isinstance(v, dict) and v:
                return v, (path + "/" + str(k)).strip("/")
        for k, v in obj.items():
            found, p = find_contract(v, path + "/" + str(k))
            if found is not None:
                return found, p
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found, p = find_contract(v, path + "[%d]" % i)
            if found is not None:
                return found, p
    return None, None


def leafify(obj):
    if isinstance(obj, dict):
        return {k: leafify(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return []
    return None


def contract_paths(obj, prefix=""):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.extend(contract_paths(v, prefix + "/" + str(k)))
        if not obj:
            out.append(prefix)
    else:
        out.append(prefix)
    return [p for p in out if p]


# --------------------------------------------------------------------------- #
# 数据画像
# --------------------------------------------------------------------------- #
def _try_num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _try_date(v):
    s = (v or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
                "%Y/%m/%d %H:%M:%S", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            datetime.datetime.strptime(s, fmt)
            return fmt
        except ValueError:
            continue
    return None


def percentile(sorted_vals, q):
    if not sorted_vals:
        return None
    n = len(sorted_vals)
    pos = (n - 1) * q
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def numeric_stats(vals):
    s = sorted(vals)
    n = len(s)
    mean = statistics.fmean(s)
    stdev = statistics.stdev(s) if n >= 2 else 0.0
    q1, q3 = percentile(s, 0.25), percentile(s, 0.75)
    iqr = (q3 - q1) if (q1 is not None and q3 is not None) else 0.0
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    n_out = sum(1 for x in s if x < lo or x > hi)
    return {"n": n, "min": s[0], "max": s[-1], "mean": mean, "std": stdev,
            "q1": q1, "median": percentile(s, 0.5), "q3": q3, "iqr": iqr,
            "n_outliers_iqr": n_out, "outlier_bounds": [lo, hi]}


def infer_column(name, raw_values):
    n = len(raw_values)
    non_missing = [v for v in raw_values
                   if v is not None and str(v).strip().lower() not in MISSING_TOKENS]
    n_missing = n - len(non_missing)
    col = {"name": name, "dtype": "text", "n": n, "n_missing": n_missing,
           "n_unique": None, "n_outliers_iqr": None}
    if not non_missing:
        col["dtype"] = "empty"
        return col
    uniq, nums, dates = {}, [], 0
    for v in non_missing:
        s = str(v).strip()
        uniq[s] = uniq.get(s, 0) + 1
        f = _try_num(s)
        if f is not None:
            nums.append(f)
        elif _try_date(s):
            dates += 1
    col["n_unique"] = len(uniq)
    num_ratio = len(nums) / len(non_missing)
    if num_ratio >= 0.95 and len(non_missing) >= 3:
        col["dtype"] = "numeric"
        col.update(numeric_stats(nums))
        col["is_integer"] = all(float(x).is_integer() for x in nums)
    elif dates / len(non_missing) >= 0.8:
        col["dtype"] = "datetime"
    elif len(uniq) <= max(20, n * 0.3):
        col["dtype"] = "categorical"
        col["top_values"] = sorted(uniq.items(), key=lambda kv: -kv[1])[:10]
        col["n_categories"] = len(uniq)
    return col


def sniff_delimiter(text):
    sample = text[:8192]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
    except csv.Error:
        return "," if sample.count(",") >= sample.count("\t") else "\t"


def parse_delimited(text, delimiter):
    rows = []
    for r in csv.reader(io.StringIO(text), delimiter=delimiter):
        if r:
            rows.append(r)
    return rows


def has_header(rows):
    if not rows:
        return False
    try:
        sample = "\n".join(",".join(r) for r in rows[:8])
        return csv.Sniffer().has_header(sample)
    except csv.Error:
        return True


def profile_delimited(path, rel, enc):
    text, _enc, err = read_text_robust(path)
    if err:
        return None, [err + ": " + rel]
    delim = sniff_delimiter(text)
    rows = parse_delimited(text, delim)
    if not rows:
        return None, ["empty_table: %s" % rel]
    if has_header(rows):
        header = [str(c).strip() for c in rows[0]]
        body = rows[1:]
    else:
        header = ["col_%d" % (i + 1) for i in range(len(rows[0]))]
        body = rows
    if len(body) > MAX_TABLE_ROWS:
        body = body[:MAX_TABLE_ROWS]
    table = {"n_rows": len(body), "n_cols": len(header), "encoding": enc,
             "delimiter": delim, "columns": []}
    for j in range(len(header)):
        colvals = [r[j] if j < len(r) else "" for r in body]
        table["columns"].append(infer_column(header[j], colvals))
    table["n_duplicates"] = max(0, len(body) - len({tuple(r) for r in body}))
    return table, []


def profile_sqlite(path, rel):
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
    except sqlite3.Error:
        try:
            con = sqlite3.connect(path)
        except sqlite3.Error as e:
            return {}, ["sqlite_open_failed: %s (%s)" % (rel, e)]
    tables, deg = {}, []
    try:
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name")
        for tname in [r[0] for r in cur.fetchall()]:
            cur.execute('PRAGMA table_info("%s")' % tname.replace('"', '""'))
            header = [r[1] for r in cur.fetchall()]
            cur.execute('SELECT * FROM "%s" LIMIT %d'
                        % (tname.replace('"', '""'), MAX_TABLE_ROWS + 1))
            rows = cur.fetchall()
            trunc = len(rows) > MAX_TABLE_ROWS
            rows = rows[:MAX_TABLE_ROWS]
            table = {"n_rows": len(rows), "n_cols": len(header), "columns": []}
            for j, h in enumerate(header):
                colvals = [r[j] if j < len(r) else None for r in rows]
                table["columns"].append(infer_column(str(h), colvals))
            table["n_duplicates"] = max(0, len(rows) - len({tuple(r) for r in rows}))
            if trunc:
                deg.append("table_truncated: %s#%s > %d rows" % (rel, tname, MAX_TABLE_ROWS))
            tables[tname] = table
    except sqlite3.Error as e:
        deg.append("sqlite_read_error: %s (%s)" % (rel, e))
    finally:
        con.close()
    return tables, deg


def compute_profile(input_dir):
    files, deg = discover_input(input_dir)
    sources, tables, table_degs = [], {}, []
    for p in files["data"]:
        rel = os.path.relpath(p, input_dir).replace("\\", "/")
        low = p.lower()
        if low.endswith(DATA_EXTS):
            tb, d = profile_sqlite(p, rel)
            for tname, t in tb.items():
                tables[rel + "#" + tname] = t
                sources.append({"path": rel, "kind": "sqlite", "table": tname})
            table_degs.extend(d)
        else:
            _, enc, err = read_text_robust(p)
            if err:
                table_degs.append(err + ": " + rel)
                continue
            t, d = profile_delimited(p, rel, enc)
            if t is not None:
                kind = "tsv" if low.endswith(".tsv") else "csv"
                tables[rel] = t
                sources.append({"path": rel, "kind": kind, "table": None})
            table_degs.extend(d)
    for p in files["materials"]:
        if p.lower().endswith((".csv", ".tsv", ".txt")):
            rel = os.path.relpath(p, input_dir).replace("\\", "/")
            _, enc, err = read_text_robust(p)
            if err:
                continue
            t, d = profile_delimited(p, rel, enc)
            if t is not None and t["columns"]:
                tables[rel] = t
                sources.append({"path": rel, "kind": "text", "table": None})
            table_degs.extend(d)
    return {"generated_at": utcnow_iso(), "input_dir": input_dir,
            "sources": sources, "tables": tables, "degradations": table_degs}


# --------------------------------------------------------------------------- #
# 图表推荐（figure_model.json 候选）
# --------------------------------------------------------------------------- #
def _top_numeric(table, k):
    num = [c for c in table["columns"] if c["dtype"] == "numeric"]
    num.sort(key=lambda c: -(c.get("std") or 0.0))
    return num[:k]


def propose_figures(profile):
    figs, n = [], 0
    for src, table in profile["tables"].items():
        cols = table["columns"]
        numeric = [c for c in cols if c["dtype"] == "numeric"]
        categorical = [c for c in cols if c["dtype"] == "categorical"
                       and (c.get("n_categories") or 0) <= 6]
        ordered = [c for c in cols if c["dtype"] == "datetime"]
        if not numeric and not categorical:
            continue
        if len(numeric) >= 2:
            n += 1
            figs.append({"figure_id": "f%02d" % n, "status": "proposed",
                         "source": src, "title": "相关热图（%s）" % src,
                         "purpose": "relationship", "chart_type": "correlation_heatmap",
                         "rationale": "两个及以上连续变量，用相关性热图揭示共线结构；"
                                      "仅显示下三角并显式标注色标范围与相关方法。",
                         "rejected": ["pie", "stacked_bar"],
                         "columns": [{"name": c["name"], "role": "matrix"} for c in numeric],
                         "style": {"palette": "Okabe-Ito", "cbar": True}})
        if len(numeric) >= 2:
            top = _top_numeric(table, min(6, len(numeric)))
            n += 1
            figs.append({"figure_id": "f%02d" % n, "status": "proposed",
                         "source": src,
                         "title": "散点矩阵（%s，方差最大 %d 列）" % (src, len(top)),
                         "purpose": "relationship", "chart_type": "scatter_matrix",
                         "rationale": "多连续变量间两两关系；面板过多时按方差优先截取。",
                         "rejected": ["pie"],
                         "columns": [{"name": c["name"], "role": "matrix"} for c in top],
                         "style": {"palette": "Okabe-Ito"}})
        if numeric:
            top = _top_numeric(table, min(6, len(numeric)))
            n += 1
            figs.append({"figure_id": "f%02d" % n, "status": "proposed",
                         "source": src, "title": "分布分面（%s）" % src,
                         "purpose": "distribution", "chart_type": "histogram",
                         "rationale": "连续变量先看分布与离群，再决定比较方式。",
                         "rejected": ["pie"],
                         "columns": [{"name": c["name"], "role": "x"} for c in top],
                         "style": {"bins": "auto"}})
        if categorical and numeric:
            top = _top_numeric(table, min(5, len(numeric)))
            n += 1
            figs.append({"figure_id": "f%02d" % n, "status": "proposed",
                         "source": src, "title": "分类比较（%s）" % src,
                         "purpose": "comparison", "chart_type": "violin",
                         "rationale": "连续变量按无序分类比较：优先箱线/小提琴/点图。",
                         "rejected": ["pie"],
                         "columns": ([{"name": c["name"], "role": "group"}
                                      for c in categorical] +
                                     [{"name": c["name"], "role": "y"} for c in top]),
                         "style": {"palette": "Okabe-Ito", "show_points": True}})
        if ordered and numeric:
            top = _top_numeric(table, min(3, len(numeric)))
            n += 1
            figs.append({"figure_id": "f%02d" % n, "status": "proposed",
                         "source": src, "title": "时间趋势（%s）" % src,
                         "purpose": "trend", "chart_type": "line",
                         "rationale": "时间序列用折线而非并列柱；对数轴显式标注。",
                         "rejected": ["bar"],
                         "columns": [{"name": ordered[0]["name"], "role": "x"}] +
                                    [{"name": c["name"], "role": "y"} for c in top],
                         "style": {}})
    return {"version": 1, "generated_at": utcnow_iso(), "figures": figs}


# --------------------------------------------------------------------------- #
# 产物格式校验
# --------------------------------------------------------------------------- #
def png_info(path):
    try:
        with open(path, "rb") as fh:
            if fh.read(8) != b"\x89PNG\r\n\x1a\n":
                return {"valid": False, "reason": "bad_signature"}
            w = h = dpi = None
            while True:
                ln = fh.read(4)
                if len(ln) < 4:
                    break
                (length,) = struct.unpack(">I", ln)
                ctype, data = fh.read(4), fh.read(length)
                fh.read(4)
                if ctype == b"IHDR" and length >= 8:
                    w, h = struct.unpack(">II", data[:8])
                elif ctype == b"pHYs" and length >= 9:
                    px, py, unit = struct.unpack(">IIB", data[:9])
                    if unit == 1:
                        dpi = round(px * 0.0254, 1)
                if ctype == b"IEND":
                    break
            return {"valid": True, "width": w, "height": h, "dpi": dpi}
    except OSError as e:
        return {"valid": False, "reason": str(e)}


def pdf_info(path):
    try:
        with open(path, "rb") as fh:
            head = fh.read(5)
        return {"valid": head.startswith(b"%PDF-"),
                "header": head[:5].decode("latin-1", "replace")}
    except OSError as e:
        return {"valid": False, "reason": str(e)}


def svg_info(path):
    try:
        ET.parse(path)
        return {"valid": True}
    except Exception as e:
        return {"valid": False, "reason": str(e)}


def artifact_info(path):
    p = path.lower()
    if p.endswith(".png"):
        return png_info(path)
    if p.endswith(".pdf"):
        return pdf_info(path)
    if p.endswith(".svg"):
        return svg_info(path)
    if os.path.exists(path):
        return {"valid": True, "exists": True}
    return {"valid": False, "reason": "missing"}
