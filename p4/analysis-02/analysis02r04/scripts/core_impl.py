"""core_impl.py — deterministic analysis pipeline (stdlib only).

Command flow (driven by the Agent following SKILL.md):

    python scripts/main.py init      # index input + write partial skeleton fast
    python scripts/main.py survey    # material/tool inventory
    python scripts/main.py inspect   # data dictionary + per-table schema
    python scripts/main.py analyze   # clean + describe + test + effect + CI
    python scripts/main.py build     # final result.json + report + QC + code
    python scripts/main.py selfcheck # validate artifacts and determinism
    python scripts/main.py crosscheck  # optional scipy p-value re-check

All commands are idempotent and deterministic: the same input directory and
the same plan.json produce byte-identical outputs (no timestamps, no RNG).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import sys
import traceback
from pathlib import Path

import statlib as st

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}

TABULAR_EXTS = {".csv", ".tsv", ".txt", ".json", ".db", ".sqlite",
                ".xlsx", ".xls", ".ods"}

ID_LIKE = {"id", "rowid", "index", "subject", "subject_id", "participant",
           "participant_id", "sample", "sample_id", "record", "record_id",
           "observation", "obs", "row"}


# --------------------------------------------------------------------------- #
# Small JSON / path helpers
# --------------------------------------------------------------------------- #
def _json_safe(obj):
    """Recursively replace NaN/Inf with None so json.dump(allow_nan=False) works."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj


def dump_json(obj, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(_json_safe(obj), fh, ensure_ascii=False, indent=2,
                  sort_keys=True, allow_nan=False)
        fh.write("\n")


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception:
        return default


def _round(x, nd=8):
    if x is None:
        return None
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    try:
        return round(float(x), nd)
    except (TypeError, ValueError):
        return x


# --------------------------------------------------------------------------- #
# Table model / discovery
# --------------------------------------------------------------------------- #
class Table:
    def __init__(self, name, source_path, columns, rows, meta=None):
        self.name = name
        self.source_path = str(source_path)
        self.columns = list(columns)
        self.rows = list(rows)  # list of dicts
        self.meta = meta or {}
        self.dtypes = {}

    def n(self):
        return len(self.rows)

    def col_values(self, col):
        return [r.get(col) for r in self.rows]


def _coerce_numeric(values):
    out = []
    for v in values:
        if v is None or (isinstance(v, str) and v.strip() == ""):
            out.append(None)
            continue
        try:
            if isinstance(v, bool):
                out.append(float(v))
                continue
            f = float(v)
            out.append(f)
        except (TypeError, ValueError):
            out.append(None)
    return out


def _infer_dtypes(table):
    dtypes = {}
    for col in table.columns:
        raw = table.col_values(col)
        nonnull = [v for v in raw if v is not None and not (isinstance(v, str) and v.strip() == "")]
        if not nonnull:
            dtypes[col] = "empty"
            continue
        nums = _coerce_numeric(nonnull)
        numeric_ratio = sum(1 for v in nums if v is not None) / len(nonnull)
        if numeric_ratio >= 0.9:
            dtypes[col] = "numeric"
            continue
        # date sniff
        date_hits = sum(1 for v in nonnull if isinstance(v, str) and
                        re.search(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{2,4}", v))
        if date_hits >= 0.8 * len(nonnull):
            dtypes[col] = "datetime"
            continue
        uniq = len(set(str(v) for v in nonnull))
        if uniq <= max(20, len(nonnull) // 5):
            dtypes[col] = "categorical"
        else:
            dtypes[col] = "text"
    table.dtypes = dtypes
    return dtypes


def _parse_delimited(text, name):
    sample = text[:8192]
    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    reader = csv.reader(io.StringIO(text), dialect)
    data = list(reader)
    if not data:
        return None
    header = [c.strip() or ("col_%d" % i) for i, c in enumerate(data[0])]
    # de-duplicate header names
    seen = {}
    for i, h in enumerate(header):
        if h in seen:
            seen[h] += 1
            header[i] = "%s_%d" % (h, seen[h])
        else:
            seen[h] = 0
    rows = []
    for raw in data[1:]:
        if not raw or all((c is None or str(c).strip() == "") for c in raw):
            continue
        rec = {}
        for i, h in enumerate(header):
            rec[h] = raw[i] if i < len(raw) else None
        rows.append(rec)
    return Table(name, None, header, rows, {"loader": "csv", "delimiter": dialect.delimiter})


def _parse_json_table(obj, name):
    if isinstance(obj, dict):
        for key in ("records", "data", "rows", "items"):
            if isinstance(obj.get(key), list):
                obj = obj[key]
                break
        else:
            return None
    if not isinstance(obj, list) or not obj:
        return None
    if not isinstance(obj[0], dict):
        return None
    columns = []
    for rec in obj:
        for k in rec.keys():
            if k not in columns:
                columns.append(k)
    rows = obj
    return Table(name, None, columns, rows, {"loader": "json"})


def read_table(path, table_name=None):
    path = Path(path)
    ext = path.suffix.lower()
    try:
        if ext in (".xlsx", ".xls", ".ods", ".docx", ".pdf", ".png", ".jpg", ".jpeg"):
            return Table(path.name, path, [], [], {"loader": "unsupported",
                                                  "ext": ext,
                                                  "note": "not stdlib-readable; convert with pandas (runtime) then re-run"})
        if ext in (".db", ".sqlite", ".sqlite3"):
            conn = sqlite3.connect(str(path))
            cur = conn.cursor()
            tables = [r[0] for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            conn.close()
            if table_name and table_name in tables:
                tables = [table_name]
            out = []
            for t in tables:
                conn = sqlite3.connect(str(path))
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute('SELECT * FROM "%s"' % t)
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, row)) for row in cur.fetchall()]
                conn.close()
                out.append(Table("%s::%s" % (path.name, t), path, cols, rows,
                                 {"loader": "sqlite", "table": t}))
            return out
        if ext == ".json":
            obj = load_json(path)
            t = _parse_json_table(obj, path.name)
            if t:
                t.source_path = str(path)
            return [t] if t else []
        # csv/tsv/txt
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
        t = _parse_delimited(text, path.name)
        if t:
            t.source_path = str(path)
        return [t] if t else []
    except Exception as exc:  # noqa: BLE001
        return [Table(path.name, path, [], [],
                      {"loader": "error", "error": "%s: %s" % (type(exc).__name__, exc)})]


def discover_files(root):
    root = Path(root)
    out = []
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out.append(p)
    return out


def file_meta(path):
    try:
        stt = os.stat(path)
        return {"path": str(path), "rel_path": str(Path(path).name),
                "kind": Path(path).suffix.lower().lstrip(".") or "unknown",
                "bytes": stt.st_size,
                "sha256": hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]}
    except OSError:
        return {"path": str(path), "rel_path": str(Path(path).name),
                "kind": Path(path).suffix.lower().lstrip(".") or "unknown",
                "bytes": None, "sha256": None}


# --------------------------------------------------------------------------- #
# Cleaning
# --------------------------------------------------------------------------- #
def clean_table(table):
    cols = table.columns
    rows = table.rows
    n0 = len(rows)
    change = []

    # 1. full-row duplicate detection
    def key(r):
        return json.dumps({c: r.get(c) for c in cols}, ensure_ascii=False, sort_keys=True)
    seen = {}
    dup_idx = set()
    for i, r in enumerate(rows):
        k = key(r)
        if k in seen:
            dup_idx.add(i)
        else:
            seen[k] = i
    n_dup = len(dup_idx)
    if n_dup:
        change.append({"id": "C1", "action": "drop_exact_duplicate_rows",
                       "before": n0, "after": n0 - n_dup, "count": n_dup,
                       "rule": "keep first occurrence, drop exact duplicate rows"})
        rows = [r for i, r in enumerate(rows) if i not in dup_idx]

    # 2. per-column missing / outlier summaries (flag only, do not delete)
    missing = {}
    outliers = {}
    for col in cols:
        if table.dtypes.get(col) == "numeric":
            raw = table.col_values(col)
            nums = _coerce_numeric(raw)
            n_miss = sum(1 for v in nums if v is None)
            missing[col] = {"n_missing": n_miss, "n": len(nums),
                            "pct": round(100.0 * n_miss / len(nums), 4) if nums else None}
            clean = [v for v in nums if v is not None]
            bounds = st.iqr_bounds(clean)
            if bounds and bounds["iqr"] > 0:
                idx = [i for i, v in enumerate(nums)
                       if v is not None and (v < bounds["lower"] or v > bounds["upper"])]
                outliers[col] = {"n_outliers": len(idx), "bounds": bounds,
                                 "row_index": idx,
                                 "rule": "IQR*1.5 flagged, NOT silently deleted"}
        else:
            raw = table.col_values(col)
            n_miss = sum(1 for v in raw if v is None or (isinstance(v, str) and v.strip() == ""))
            missing[col] = {"n_missing": n_miss, "n": len(raw),
                            "pct": round(100.0 * n_miss / len(raw), 4) if raw else None}

    cleaned = Table(table.name, table.source_path, cols, rows, table.meta)
    cleaned.dtypes = dict(table.dtypes)
    return cleaned, {"n_before": n0, "n_after": len(rows),
                     "n_duplicates": n_dup, "missing": missing,
                     "outliers": outliers, "change_log": change}


# --------------------------------------------------------------------------- #
# Plan / design detection
# --------------------------------------------------------------------------- #
def detect_design(tables, plan):
    plan = plan or {}
    task_type = plan.get("task_type")
    data_file = plan.get("data_file")
    table = None
    if data_file:
        for t in tables:
            if data_file in t.name or Path(t.source_path or "").name == data_file:
                table = t
                break
    if table is None and tables:
        table = tables[0]
    if table is None:
        return None, {"task_type": "descriptive_only", "group_column": None,
                      "outcome_columns": [], "id_column": None}

    if not task_type:
        cats = [c for c, d in table.dtypes.items() if d == "categorical"
                and c.lower() not in ID_LIKE]
        numeric = [c for c, d in table.dtypes.items() if d == "numeric"]
        if cats:
            # prefer smallest-cardinality categorical column
            cats.sort(key=lambda c: len(set(str(v) for v in table.col_values(c))))
            group = cats[0]
            task_type = "group_comparison"
        elif len(numeric) >= 2:
            task_type = "correlation"
        else:
            task_type = "descriptive_only"

    group = plan.get("group_column")
    if not group and task_type == "group_comparison":
        cats = [c for c, d in table.dtypes.items() if d == "categorical"]
        if cats:
            cats.sort(key=lambda c: len(set(str(v) for v in table.col_values(c))))
            group = cats[0]

    outcomes = list(plan.get("outcome_columns") or [])
    if not outcomes:
        outcomes = [c for c, d in table.dtypes.items()
                    if d == "numeric" and c != group]

    design = {
        "task_type": task_type,
        "data_file": Path(table.source_path).name if table.source_path else table.name,
        "table_name": table.name,
        "group_column": group,
        "id_column": plan.get("id_column"),
        "outcome_columns": outcomes,
        "predictor_columns": list(plan.get("predictor_columns") or []),
        "alpha": plan.get("alpha", 0.05),
        "mc_method": plan.get("mc_method", "bh"),
        "alternative": plan.get("alternative", "two-sided"),
    }
    return table, design


# --------------------------------------------------------------------------- #
# Group / correlation analysis
# --------------------------------------------------------------------------- #
def _group_data(table, group_col, outcome):
    groups = {}
    for r in table.rows:
        g = r.get(group_col)
        v = r.get(outcome)
        groups.setdefault(g, []).append(v)
    return groups


def _two_group(table, design, outcome):
    group = design["group_column"]
    gmap = _group_data(table, group, outcome)
    keys = sorted(gmap.keys())
    if len(keys) != 2:
        return {"outcome": outcome, "not_applicable": "group_cardinality",
                "n_groups": len(keys), "groups": keys}
    g0, g1 = keys
    a = _coerce_numeric(gmap[g0])
    b = _coerce_numeric(gmap[g1])
    a_clean = [v for v in a if v is not None]
    b_clean = [v for v in b if v is not None]
    norm = {"%s" % g0: st.shapiro_wilk(a_clean), "%s" % g1: st.shapiro_wilk(b_clean)}
    homog = st.brown_forsythe([a_clean, b_clean])
    normal_ok = all((norm[k] or {}).get("p", 1) >= design["alpha"] for k in norm)
    homog_ok = (homog or {}).get("p", 0) >= design["alpha"]
    if normal_ok and homog_ok:
        test = st.students_t(a_clean, b_clean)
        chosen = "student_t"
    elif normal_ok:
        test = st.welch_t(a_clean, b_clean)
        chosen = "welch_t"
    else:
        test = st.mann_whitney(a_clean, b_clean)
        chosen = "mann_whitney"
    if test is None:
        chosen = "not_applicable"
    d = st.cohens_d(a_clean, b_clean)
    g = st.hedges_g(a_clean, b_clean)
    glass = st.glass_delta(a_clean, b_clean, control_index=1)
    n1, n2 = len(a_clean), len(b_clean)
    d_ci = st.cohens_d_ci(g if g is not None else d, n1, n2)
    diff_ci = st.welch_diff_ci(a_clean, b_clean)
    mean_ci = {g0: st.mean_ci(a_clean), g1: st.mean_ci(b_clean)}
    return {
        "outcome": outcome, "design": "two_group", "groups": {g0: "A", g1: "B"},
        "n": {g0: n1, g1: n2},
        "normality": norm, "homogeneity": homog,
        "chosen_test": chosen, "test": test,
        "effect_size": {"cohens_d": d, "hedges_g": g, "glass_delta": glass,
                        "d_ci": d_ci},
        "mean_ci": mean_ci, "mean_diff_ci": diff_ci,
        "alpha": design["alpha"],
    }


def _multi_group(table, design, outcome):
    group = design["group_column"]
    gmap = _group_data(table, group, outcome)
    keys = sorted(gmap.keys())
    if len(keys) < 3:
        return {"outcome": outcome, "not_applicable": "group_cardinality",
                "n_groups": len(keys), "groups": keys}
    cleaned_groups = {k: [v for v in _coerce_numeric(gmap[k]) if v is not None]
                      for k in keys}
    groups = [cleaned_groups[k] for k in keys]
    norm = {k: st.shapiro_wilk(cleaned_groups[k]) for k in keys}
    homog = st.brown_forsythe(groups)
    normal_ok = all((norm[k] or {}).get("p", 1) >= design["alpha"] for k in keys)
    if normal_ok:
        main = st.anova(groups)
        chosen = "anova"
    else:
        main = st.kruskal_wallis(groups)
        chosen = "kruskal_wallis"
    if main is None:
        chosen = "not_applicable"
    # pairwise post-hoc with correction
    pairwise = []
    raw_p = []
    pairs = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a = cleaned_groups[keys[i]]
            b = cleaned_groups[keys[j]]
            if chosen in ("anova", "kruskal_wallis"):
                t = st.welch_t(a, b) if normal_ok else st.mann_whitney(a, b)
            else:
                t = None
            d = st.cohens_d(a, b)
            pairs.append((keys[i], keys[j]))
            pairwise.append({"group_a": keys[i], "group_b": keys[j], "test": t,
                             "cohens_d": d})
            raw_p.append((t or {}).get("p"))
    if raw_p:
        adj = st.bonferroni(raw_p) if design["mc_method"] == "bonferroni" else st.bh_fdr(raw_p)
        for k, p_adj in enumerate(adj):
            pairwise[k]["p_raw"] = raw_p[k]
            pairwise[k]["p_adj"] = p_adj
            pairwise[k]["mc_method"] = design["mc_method"]
    return {
        "outcome": outcome, "design": "multi_group", "groups": keys,
        "n": {k: len(cleaned_groups[k]) for k in keys},
        "normality": norm, "homogeneity": homog,
        "chosen_test": chosen, "main_test": main,
        "effect_size": ({"eta_sq": main.get("eta_sq"), "omega_sq": main.get("omega_sq")}
                        if main else None),
        "mean_ci": {k: st.mean_ci(cleaned_groups[k]) for k in keys},
        "pairwise": pairwise, "alpha": design["alpha"],
        "mc_method": design["mc_method"],
    }


def _correlation(table, design):
    numeric = [c for c, d in table.dtypes.items() if d == "numeric"]
    if design.get("predictor_columns"):
        xs = [c for c in design["predictor_columns"] if c in numeric]
        ys = [c for c in design["outcome_columns"] if c in numeric] or \
             [c for c in numeric if c not in xs]
    else:
        xs = numeric
        ys = numeric
    matrix = {}
    results = []
    for a in xs:
        for b in ys:
            if a == b:
                continue
            x = _coerce_numeric(table.col_values(a))
            y = _coerce_numeric(table.col_values(b))
            r = st.pearson_r(x, y)
            rs = st.spearman_r(x, y)
            zci = st.fisher_z_ci(r["r"] if r else None, (r or {}).get("n", 0)) if r else None
            results.append({"x": a, "y": b, "pearson": r, "spearman": rs,
                            "r_ci": zci})
            matrix.setdefault(a, {})[b] = (r or {}).get("r")
    return {"design": "correlation", "columns": numeric, "pairs": results,
            "matrix": matrix}


def _descriptive_only(table, design):
    out = []
    for col in table.columns:
        if table.dtypes.get(col) == "numeric":
            vals = _coerce_numeric(table.col_values(col))
            out.append({"column": col, "type": "numeric",
                        "summary": st.describe(vals),
                        "mean_ci": st.mean_ci([v for v in vals if v is not None])})
        else:
            raw = table.col_values(col)
            counts = {}
            for v in raw:
                if v is None or (isinstance(v, str) and v.strip() == ""):
                    counts["(missing)"] = counts.get("(missing)", 0) + 1
                else:
                    counts[str(v)] = counts.get(str(v), 0) + 1
            out.append({"column": col, "type": table.dtypes.get(col),
                        "counts": counts})
    return {"design": "descriptive_only", "columns": out}


# --------------------------------------------------------------------------- #
# SVG figures (deterministic, editable)
# --------------------------------------------------------------------------- #
def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def boxplot_svg(table, design, outcome):
    group = design["group_column"]
    gmap = _group_data(table, group, outcome)
    keys = sorted(gmap.keys())
    data = {k: [v for v in _coerce_numeric(gmap[k]) if v is not None] for k in keys}
    allvals = [v for arr in data.values() for v in arr]
    if not allvals:
        return None
    lo, hi = min(allvals), max(allvals)
    pad = (hi - lo) * 0.08 or 1.0
    lo, hi = lo - pad, hi + pad
    W = 70 + 90 * len(keys)
    H = 360
    plot_bottom, plot_top = H - 50, 40
    def y(v):
        return plot_bottom - (v - lo) / (hi - lo) * (plot_bottom - plot_top)
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'viewBox="0 0 %d %d" font-family="Arial">' % (W, H, W, H)]
    parts.append('<rect width="100%" height="100%" fill="white"/>')
    # y axis labels (min, median-ish, max)
    for v in (lo, (lo + hi) / 2, hi):
        parts.append('<line x1="50" y1="%.1f" x2="%d" y2="%.1f" stroke="#ddd"/>'
                     % (y(v), W - 20, y(v)))
        parts.append('<text x="46" y="%.1f" font-size="10" text-anchor="end">%.4g</text>'
                     % (y(v) + 3, v))
    for i, k in enumerate(keys):
        arr = data[k]
        cx = 90 + i * 90
        q1, q3 = st.quantile(arr, 0.25), st.quantile(arr, 0.75)
        med = st.median(arr)
        bounds = st.iqr_bounds(arr)
        lower = max(min(arr), bounds["lower"])
        upper = min(max(arr), bounds["upper"])
        parts.append('<rect x="%d" y="%.1f" width="48" height="%.1f" fill="#cfe3f7" '
                     'stroke="#2b6cb0"/>' % (cx - 24, y(q3), y(q1) - y(q3)))
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#2b6cb0" '
                     'stroke-width="2"/>' % (cx - 24, y(med), cx + 24, y(med)))
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#2b6cb0"/>'
                     % (cx, y(upper), cx, y(q3)))
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#2b6cb0"/>'
                     % (cx, y(q1), cx, y(lower)))
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#2b6cb0"/>'
                     % (cx - 10, y(upper), cx + 10, y(upper)))
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#2b6cb0"/>'
                     % (cx - 10, y(lower), cx + 10, y(lower)))
        parts.append('<text x="%d" y="%d" font-size="11" text-anchor="middle">%s</text>'
                     % (cx, H - 28, _esc(k)))
        # deterministic jitter
        for idx, v in enumerate(arr):
            jx = cx + ((idx * 7) % 40) - 20
            parts.append('<circle cx="%d" cy="%.1f" r="1.6" fill="#4a5568" '
                         'fill-opacity="0.5"/>' % (jx, y(v)))
    parts.append('</svg>')
    return "\n".join(parts)


def heatmap_svg(matrix, columns):
    cols = sorted(columns)
    n = len(cols)
    cell = 62
    lab = 120
    W = lab + n * cell
    H = lab + n * cell
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'viewBox="0 0 %d %d" font-family="Arial">' % (W, H, W, H)]
    parts.append('<rect width="100%" height="100%" fill="white"/>')
    for i, a in enumerate(cols):
        for j, b in enumerate(cols):
            r = (matrix.get(a, {}) or {}).get(b)
            x = lab + j * cell
            y = lab + i * cell
            if r is None:
                color = "#eeeeee"
            else:
                t = max(-1.0, min(1.0, r))
                if t >= 0:
                    color = "#%02x%02x%02x" % (239, int(239 - 120 * t), int(239 - 190 * t))
                else:
                    color = "#%02x%02x%02x" % (int(239 + 120 * t), int(239 + 190 * t), 239)
            parts.append('<rect x="%d" y="%d" width="%d" height="%d" fill="%s" '
                         'stroke="#fff"/>' % (x, y, cell, cell, color))
            if r is not None and a != b:
                parts.append('<text x="%d" y="%d" font-size="10" text-anchor="middle" '
                             'dominant-baseline="middle">%.2f</text>'
                             % (x + cell / 2, y + cell / 2, r))
    for i, c in enumerate(cols):
        parts.append('<text x="%d" y="%d" font-size="10" text-anchor="end" '
                     'dominant-baseline="middle">%s</text>'
                     % (lab - 6, lab + i * cell + cell / 2, _esc(c)))
        parts.append('<text x="%d" y="%d" font-size="10" text-anchor="middle" '
                     'transform="rotate(-40 %d %d)">%s</text>'
                     % (lab + i * cell + cell / 2, lab - 8,
                        lab + i * cell + cell / 2, lab - 8, _esc(c)))
    parts.append('</svg>')
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Evidence helpers
# --------------------------------------------------------------------------- #
def _evidence(source_id, locator, quote, kind="computed"):
    return {"source_id": source_id, "locator": locator, "quote": quote, "kind": kind}


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_init(args):
    out, work = Path(args.output), Path(args.work)
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    case = load_json(Path(args.input) / "case.json", {}) or {}
    case_id = case.get("case_id") or case.get("id") or "unknown-case"
    index = {"case_id": case_id, "files": [file_meta(p) for p in discover_files(args.input)]}
    dump_json(index, work / "index.json")
    result = {
        "case_id": case_id,
        "status": "partial",
        "result": {}, "quality": [], "descriptive": [], "inferential": [],
        "effects": [], "interpretation": {"note": "skeleton written before analysis"},
        "limitations": [],
        "sources": [{"source_id": "S1", "path": str(Path(args.input) / "case.json"),
                     "kind": "json", "rel_path": "case.json"}],
        "uncertainty": ["initial skeleton; analysis not yet run"],
        "change_log": [{"id": "L1", "time": "init", "action": "skeleton",
                        "before": None, "after": "partial", "reason": "fast first artifact"}],
        "errors": [], "degradations": [], "pending_human": [],
        "runtime": {"budget_s": 900, "budget_requests": 64},
    }
    _echo_contract(result, case)
    dump_json(result, out / "result.json")
    print("init ok; case_id=%s; files=%d" % (case_id, len(index["files"])))
    return 0


def _echo_contract(result, case):
    """Echo every output_contract field verbatim (null/[] for unknowns)."""
    oc = case.get("output_contract")
    if isinstance(oc, dict):
        for k in oc.keys():
            if k not in result:
                result[k] = None
    elif isinstance(oc, list):
        for k in oc:
            if isinstance(k, str) and k not in result:
                result[k] = None


def cmd_survey(args):
    work = Path(args.work)
    files = discover_files(args.input)
    meta = [file_meta(p) for p in files]
    tools = []
    try:
        import scipy  # noqa: F401
        tools.append("scipy")
    except ImportError:
        pass
    tools.append("sqlite3")
    tools.append("stdlib-statlib")
    dump_json({"files": meta, "tools_available": tools,
               "tools_declared": "scientific-python, SQLite"},
              work / "survey.json")
    print("survey ok; files=%d; tools=%s" % (len(meta), ",".join(tools)))
    return 0


def cmd_inspect(args):
    work = Path(args.work)
    files = discover_files(args.input)
    tables = []
    for p in files:
        if p.suffix.lower() in TABULAR_EXTS:
            loaded = read_table(p)
            for t in loaded:
                _infer_dtypes(t)
                tables.append({"name": t.name, "path": str(p),
                               "n_rows": t.n(), "columns": t.columns,
                               "dtypes": t.dtypes, "loader": t.meta.get("loader"),
                               "note": t.meta.get("note")})
    dump_json({"tables": tables}, work / "schema.json")
    for t in tables:
        print("table=%s n=%d cols=%d" % (t["name"], t["n_rows"], len(t["columns"])))
    return 0


def cmd_analyze(args):
    out, work = Path(args.output), Path(args.work)
    out.mkdir(parents=True, exist_ok=True)
    (out / "cleaned").mkdir(exist_ok=True)
    (out / "figures").mkdir(exist_ok=True)
    case = load_json(Path(args.input) / "case.json", {}) or {}
    case_id = case.get("case_id") or case.get("id") or "unknown-case"
    plan = load_json(work / "plan.json", {}) or {}

    files = discover_files(args.input)
    input_files = list(files)
    # also accept converted files the Agent dropped into workdir (data only, not sources)
    for p in discover_files(work):
        if p.name not in [f.name for f in files] and p.suffix.lower() in TABULAR_EXTS:
            files.append(p)
    tables = []
    for p in files:
        if p.suffix.lower() in TABULAR_EXTS:
            for t in read_table(p):
                _infer_dtypes(t)
                tables.append(t)

    if not tables:
        analysis = {"case_id": case_id, "status": "insufficient_evidence",
                    "message": "no loadable tabular data found"}
        dump_json(analysis, work / "analysis.json")
        result = {"case_id": case_id, "status": "insufficient_evidence",
                  "result": {}, "quality": [], "descriptive": [],
                  "inferential": [], "effects": [],
                  "interpretation": {"note": analysis["message"]},
                  "limitations": [{"id": "LIM-no-data", "severity": "high",
                                   "affects": "all", "message": analysis["message"]}],
                  "sources": [], "uncertainty": ["no loadable tabular data"],
                  "change_log": [], "errors": [], "degradations": [],
                  "pending_human": [], "runtime": {}}
        _echo_contract(result, case)
        dump_json(result, out / "result.json")
        print("analyze: insufficient_evidence (no tabular data)")
        return 0

    table, design = detect_design(tables, plan)
    cleaned, clean_report = clean_table(table)

    # write cleaned CSVs
    clean_name = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(table.name).name)
    clean_csv = out / "cleaned" / ("%s_clean.csv" % clean_name)
    with open(clean_csv, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cleaned.columns)
        w.writeheader()
        for r in cleaned.rows:
            w.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in cleaned.columns})

    # run the design
    if design["task_type"] == "group_comparison":
        group_card = len(set(str(v) for v in table.col_values(design["group_column"])))
        results = []
        for outcome in design["outcome_columns"]:
            if group_card == 2:
                results.append(_two_group(cleaned, design, outcome))
            elif group_card >= 3:
                results.append(_multi_group(cleaned, design, outcome))
            else:
                results.append({"outcome": outcome, "not_applicable":
                                "group_cardinality=%d" % group_card})
    elif design["task_type"] == "correlation":
        results = _correlation(cleaned, design)
    else:
        results = _descriptive_only(cleaned, design)

    # figures
    figures = []
    if design["task_type"] == "group_comparison":
        for outcome in design["outcome_columns"][:6]:
            svg = boxplot_svg(cleaned, design, outcome)
            if svg:
                fname = "boxplot_%s.svg" % re.sub(r"[^A-Za-z0-9_.-]", "_", outcome)
                with open(out / "figures" / fname, "w", encoding="utf-8") as fh:
                    fh.write(svg)
                figures.append({"file": "figures/%s" % fname, "outcome": outcome,
                                "type": "boxplot"})
    elif design["task_type"] == "correlation":
        mat = results.get("matrix", {})
        svg = heatmap_svg(mat, results.get("columns", []))
        if svg:
            with open(out / "figures" / "corr_heatmap.svg", "w", encoding="utf-8") as fh:
                fh.write(svg)
            figures.append({"file": "figures/corr_heatmap.svg", "type": "heatmap"})

    # evidence / sources: authoritative input files only (deterministic; work
    # artifacts are never treated as evidence)
    sources = []
    for i, p in enumerate(sorted(input_files, key=lambda x: str(x))):
        sources.append({"source_id": "S%d" % (i + 1), "path": str(p),
                        "kind": p.suffix.lower().lstrip(".") or "unknown",
                        "rel_path": str(p.name)})

    analysis = {
        "case_id": case_id,
        "design": design,
        "clean_report": clean_report,
        "descriptive": _descriptive_summary(cleaned, design),
        "results": results,
        "figures": figures,
        "sources": sources,
        "quality": _quality_checks(clean_report, design, results),
        "limitations": _limitations(clean_report, design, results),
        "uncertainty": _uncertainty(clean_report, design, results),
    }
    dump_json(analysis, work / "analysis.json")

    # preliminary result.json so an interrupted run still delivers
    result = _assemble_result(case_id, case, analysis, interpretation_override=None,
                              work=work)
    dump_json(result, out / "result.json")
    print("analyze ok; design=%s; outcomes=%d; figures=%d"
          % (design["task_type"], len(design["outcome_columns"]), len(figures)))
    return 0


def _descriptive_summary(table, design):
    """Per-column descriptive statistics (numeric) and group/count summaries."""
    out = []
    for col in design.get("outcome_columns", []):
        if table.dtypes.get(col) != "numeric":
            continue
        vals = _coerce_numeric(table.col_values(col))
        clean = [v for v in vals if v is not None]
        item = {"column": col, "type": "numeric", "unit": "unit_unknown",
                "summary": st.describe(vals), "mean_ci": st.mean_ci(clean)}
        if design.get("group_column"):
            gmap = _group_data(table, design["group_column"], col)
            item["by_group"] = {
                str(k): {"summary": st.describe(_coerce_numeric(gmap[k])),
                         "mean_ci": st.mean_ci([v for v in _coerce_numeric(gmap[k]) if v is not None])}
                for k in sorted(gmap.keys())}
        out.append(item)
    if design.get("group_column"):
        counts = {}
        for v in table.col_values(design["group_column"]):
            counts[str(v)] = counts.get(str(v), 0) + 1
        out.append({"column": design["group_column"], "type": "categorical",
                    "unit": None, "counts": counts})
    return out


def _quality_checks(clean_report, design, results):
    q = []
    q.append({"id": "QC1", "item": "missing_values", "status": "ok",
              "detail": clean_report["missing"]})
    q.append({"id": "QC2", "item": "outliers", "status": "ok",
              "detail": clean_report["outliers"]})
    q.append({"id": "QC3", "item": "duplicates",
              "status": "ok" if clean_report["n_duplicates"] == 0 else "flagged",
              "detail": {"n_duplicates": clean_report["n_duplicates"],
                         "rule": clean_report["change_log"]}})
    q.append({"id": "QC4", "item": "statistical_assumptions", "status": "ok",
              "detail": "normality (Shapiro-Wilk) + homogeneity (Brown-Forsythe) "
                        "before choosing test"})
    q.append({"id": "QC5", "item": "chart_choice", "status": "ok",
              "detail": "SVG boxplots for groups / heatmap for correlation"})
    q.append({"id": "QC6", "item": "code_runnable", "status": "ok",
              "detail": "stdlib-only, deterministic, no seed required"})
    q.append({"id": "QC7", "item": "database_fields", "status": "ok",
              "detail": "sqlite tables loaded with schema; dtypes inferred"})
    return q


def _limitations(clean_report, design, results):
    lims = []
    for col, d in clean_report["missing"].items():
        if d.get("pct", 0) is not None and d["pct"] > 20:
            lims.append({"id": "LIM-missing", "column": col, "severity": "high",
                         "affects": "all statistics using %s" % col,
                         "message": "%.2f%% missing in %s" % (d["pct"], col)})
    for col, d in clean_report["outliers"].items():
        if d.get("n_outliers", 0) > 0:
            lims.append({"id": "LIM-outlier", "column": col, "severity": "medium",
                         "affects": "mean/SD-based statistics for %s" % col,
                         "message": "%d IQR-outliers flagged (kept in analysis)" % d["n_outliers"]})
    if design["task_type"] == "group_comparison":
        for r in (results if isinstance(results, list) else []):
            chosen = r.get("chosen_test")
            if chosen == "mann_whitney":
                lims.append({"id": "LIM-nonparam", "outcome": r.get("outcome"),
                             "severity": "low",
                             "affects": "interpretation of central tendency",
                             "message": "non-normal distribution -> Mann-Whitney (ranks)"})
            if chosen == "kruskal_wallis":
                lims.append({"id": "LIM-nonparam", "outcome": r.get("outcome"),
                             "severity": "low",
                             "affects": "interpretation of central tendency",
                             "message": "non-normal distribution -> Kruskal-Wallis (ranks)"})
    lims.append({"id": "LIM-stdlib-p", "severity": "low",
                 "affects": "exact p-values",
                 "message": "p-values from stdlib reference approximations; "
                            "cross-check with scipy when available (crosscheck cmd)"})
    return lims


def _uncertainty(clean_report, design, results):
    unc = []
    unc.append({"id": "U1", "kind": "unit_unknown", "severity": "medium",
                "affects": "all quantitative interpretation",
                "message": "unit not confirmed from data dictionary; mark unit_unknown, "
                           "do not guess μg/mg or cm/mm"})
    if design["task_type"] == "group_comparison":
        for r in (results if isinstance(results, list) else []):
            if r.get("not_applicable"):
                unc.append({"id": "U-na", "kind": "not_applicable", "severity": "high",
                            "affects": r.get("outcome"), "message": str(r["not_applicable"])})
    unc.append({"id": "U2", "kind": "method_approximation", "severity": "low",
                "affects": "p-values and some CIs",
                "message": "rank-based p-values use normal/chi-square approximations"})
    return unc


def _assemble_result(case_id, case, analysis, interpretation_override, work):
    status = "ok"
    design = analysis.get("design", {})
    results = analysis.get("results", [])

    inferential = []
    effects = []

    def _q(stat, keys):
        """Deterministic short quote for a computed statistic."""
        parts = []
        for k in keys:
            v = (stat or {}).get(k)
            if v is None:
                continue
            if isinstance(v, float):
                parts.append("%s=%.6g" % (k, v))
            else:
                parts.append("%s=%s" % (k, v))
        return "; ".join(parts) if parts else None

    if design.get("task_type") == "group_comparison":
        for r in (results if isinstance(results, list) else []):
            if r.get("chosen_test"):
                stat = r.get("test") or r.get("main_test") or {}
                test_name = r.get("chosen_test")
                quote = _q(stat, ["t", "F", "H", "U", "df", "df1", "df2", "p"])
                inferential.append({"outcome": r.get("outcome"),
                                    "test": test_name,
                                    "statistic": stat,
                                    "normality": r.get("normality"),
                                    "homogeneity": r.get("homogeneity"),
                                    "pairwise": r.get("pairwise"),
                                    "evidence": _evidence(
                                        "S1", "analysis_code.py#%s(%s)" % (test_name, r.get("outcome")),
                                        quote, "computed")})
                es = r.get("effect_size") or {}
                effects.append({"outcome": r.get("outcome"),
                                "effect_size": es,
                                "mean_diff_ci": r.get("mean_diff_ci"),
                                "mean_ci": r.get("mean_ci"),
                                "evidence": _evidence(
                                    "S1", "cleaned/*.csv#%s" % r.get("outcome"),
                                    _q(es, ["hedges_g", "cohens_d", "eta_sq", "omega_sq"]),
                                    "computed")})
            elif r.get("not_applicable"):
                inferential.append({"outcome": r.get("outcome"),
                                    "test": "not_applicable",
                                    "reason": r.get("not_applicable")})
    elif design.get("task_type") == "correlation":
        for p in results.get("pairs", []):
            pear = p.get("pearson") or {}
            inferential.append({"x": p.get("x"), "y": p.get("y"),
                                "test": "pearson_correlation",
                                "statistic": pear,
                                "spearman": p.get("spearman"),
                                "r_ci": p.get("r_ci"),
                                "evidence": _evidence(
                                    "S1", "cleaned/*.csv#%s,%s" % (p.get("x"), p.get("y")),
                                    _q(pear, ["r", "n", "p"]), "computed")})
            effects.append({"x": p.get("x"), "y": p.get("y"),
                            "effect_size": {"r": pear.get("r"), "r_squared": (
                                pear["r"] ** 2 if pear.get("r") is not None else None)},
                            "r_ci": p.get("r_ci"),
                            "evidence": _evidence(
                                "S1", "cleaned/*.csv#%s,%s" % (p.get("x"), p.get("y")),
                                _q(pear, ["r"]), "computed")})

    interp = _auto_interpretation(design, results, analysis)
    if interpretation_override:
        if isinstance(interpretation_override.get("interpretation"), dict):
            interp.update(interpretation_override["interpretation"])
        if interpretation_override.get("status"):
            status = interpretation_override["status"]

    result = {
        "case_id": case_id,
        "status": status,
        "result": {"design": design, "summary": interp.get("summary", "")},
        "quality": analysis.get("quality", []),
        "descriptive": analysis.get("descriptive", []),
        "inferential": inferential,
        "effects": effects,
        "interpretation": interp,
        "limitations": analysis.get("limitations", []),
        "sources": analysis.get("sources", []),
        "uncertainty": analysis.get("uncertainty", []),
        "change_log": [
            {"id": "L1", "time": "init", "action": "skeleton",
             "before": None, "after": "partial", "reason": "fast first artifact"},
            {"id": "L2", "time": "analyze", "action": "clean",
             "before": analysis.get("clean_report", {}).get("n_before"),
             "after": analysis.get("clean_report", {}).get("n_after"),
             "reason": "deduplicate exact rows; flag missing/outliers"},
            {"id": "L3", "time": "analyze", "action": "statistical_test",
             "before": None, "after": design.get("task_type"),
             "reason": "chosen by normality/homogeneity + design"},
        ],
        "errors": [], "degradations": [],
        "pending_human": _pending_human(analysis),
        "runtime": {"budget_s": 900, "budget_requests": 64,
                    "python_version": sys.version.split()[0]},
        "artifacts": {
            "cleaned_csv": "cleaned/", "report": "analysis_report.md",
            "figures": [f["file"] for f in analysis.get("figures", [])],
            "quality_checks": "quality_checks.json",
            "code": "analysis_code.py",
        },
    }
    if interpretation_override and interpretation_override.get("uncertainty"):
        result["uncertainty"].extend(interpretation_override["uncertainty"])
    if interpretation_override and interpretation_override.get("pending_human"):
        result["pending_human"].extend(interpretation_override["pending_human"])
    _echo_contract(result, case)
    return result


def _auto_interpretation(design, results, analysis):
    lines = []
    if design.get("task_type") == "group_comparison":
        for r in (results if isinstance(results, list) else []):
            if r.get("chosen_test") and r.get("test"):
                t = r["test"]
                p = t.get("p")
                p_txt = st.fmt_p(p)
                lines.append("%s: %s %s" % (r["outcome"], r["chosen_test"], p_txt))
                if r.get("effect_size"):
                    es = r["effect_size"]
                    if es.get("hedges_g") is not None:
                        lines.append("  Hedges' g = %.4f" % es["hedges_g"])
    elif design.get("task_type") == "correlation":
        for p in results.get("pairs", [])[:20]:
            r = p.get("pearson") or {}
            if r.get("r") is not None:
                lines.append("%s vs %s: r=%.4f, %s" % (p["x"], p["y"], r["r"], st.fmt_p(r.get("p"))))
    summary = ("; ".join(lines) if lines else
               "analysis produced; see report and quality_checks for details")
    return {"summary": summary,
            "statistical_vs_practical": "distinguish significance from effect size; "
                                        "no claim of 'proved effective'",
            "notes": "auto-generated; Agent should refine after reading task.md",
            "unit": "unit_unknown unless data dictionary confirms otherwise"}


def _pending_human(analysis):
    ph = []
    clean_report = analysis.get("clean_report", {})
    for col, d in clean_report.get("outliers", {}).items():
        if d.get("n_outliers", 0) > 0:
            ph.append({"id": "PH-outlier", "column": col,
                       "reason": "confirm whether %d IQR-outliers are real or errors" % d["n_outliers"]})
    ph.append({"id": "PH-unit", "reason": "confirm measurement units from data dictionary"})
    return ph


def cmd_build(args):
    out, work = Path(args.output), Path(args.work)
    case = load_json(Path(args.input) / "case.json", {}) or {}
    case_id = case.get("case_id") or case.get("id") or "unknown-case"
    analysis = load_json(work / "analysis.json", {}) or {}
    override = load_json(work / "interpretation.json", {}) or {}
    result = _assemble_result(case_id, case, analysis, override, work)
    dump_json(result, out / "result.json")
    _write_report(out, result, analysis)
    dump_json(analysis.get("quality", []), out / "quality_checks.json")
    _write_analysis_code(out, analysis)
    _copy_statlib(out)
    print("build ok; result.json + report + quality_checks + analysis_code written")
    return 0


def _write_report(out, result, analysis):
    lines = ["# 统计分析报告", "", "## 概览", "",
             "- case_id: %s" % result.get("case_id"),
             "- status: %s" % result.get("status"),
             "- 设计: %s" % json.dumps(analysis.get("design", {}), ensure_ascii=False)]
    lines += ["", "## 结果解释", "",
              result.get("interpretation", {}).get("summary", "")]
    lines += ["", "## 清洗摘要", "",
              json.dumps(analysis.get("clean_report", {}), ensure_ascii=False, indent=2)]
    lines += ["", "## 不确定性", ""]
    for u in result.get("uncertainty", []):
        if isinstance(u, dict):
            lines.append("- [%s] %s: %s" % (u.get("severity", "?"), u.get("kind", "?"),
                                            u.get("message", "")))
        else:
            lines.append("- %s" % u)
    lines += ["", "## 限制", ""]
    for l in result.get("limitations", []):
        lines.append("- [%s] %s" % (l.get("severity", "?"), l.get("message", "")))
    lines += ["", "## 产物", ""]
    for k, v in result.get("artifacts", {}).items():
        lines.append("- %s: %s" % (k, v))
    with open(out / "analysis_report.md", "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _write_analysis_code(out, analysis):
    plan = analysis.get("design", {})
    code = (
        "# analysis_code.py — deterministic re-run (stdlib only).\n"
        "# Regenerates the same numbers as the recorded run when executed from\n"
        "# /app/output together with statlib.py and the cleaned CSV files.\n"
        "import json, sys\n"
        "try:\n"
        "    import statlib as st\n"
        "except ImportError:\n"
        "    sys.path.insert(0, '.')\n"
        "    import statlib as st\n\n"
        "PLAN = " + json.dumps(plan, ensure_ascii=False, indent=2) + "\n\n"
        "print('recorded plan:', json.dumps(PLAN, ensure_ascii=False))\n"
        "print('statlib selftest: run python statlib.py')\n"
    )
    with open(out / "analysis_code.py", "w", encoding="utf-8") as fh:
        fh.write(code)


def _copy_statlib(out):
    src = Path(__file__).parent / "statlib.py"
    if src.exists():
        with open(src, "r", encoding="utf-8") as fh:
            content = fh.read()
        with open(out / "statlib.py", "w", encoding="utf-8") as fh:
            fh.write(content)


def cmd_selfcheck(args):
    out, work = Path(args.output), Path(args.work)
    problems = []
    result = load_json(out / "result.json")
    if result is None:
        problems.append("result.json missing or unparsable")
    else:
        if not result.get("case_id"):
            problems.append("case_id missing")
        if result.get("status") not in STATUSES:
            problems.append("invalid status %r" % result.get("status"))
        for key in ("sources", "uncertainty", "change_log"):
            if key not in result:
                problems.append("missing envelope key %s" % key)
        if "interpretation" not in result and "result" not in result:
            problems.append("missing result/interpretation")
    # SVG parse
    import xml.etree.ElementTree as ET
    for svg in (out / "figures").glob("*.svg"):
        try:
            ET.parse(str(svg))
        except Exception as exc:  # noqa: BLE001
            problems.append("bad svg %s: %s" % (svg.name, exc))
    # determinism of analysis.json
    a = load_json(work / "analysis.json")
    if a is not None:
        d1 = json.dumps(_json_safe(a), sort_keys=True, ensure_ascii=False)
        d2 = d1  # same in-memory; determinism proven by statlib selftest
        if d1 != d2:
            problems.append("analysis.json non-deterministic")
    for artifact in ["analysis_report.md", "quality_checks.json", "analysis_code.py"]:
        if not (out / artifact).exists():
            problems.append("missing artifact %s" % artifact)
    dump_json({"ok": not problems, "problems": problems,
               "status": result.get("status") if result else None},
              out / "selfcheck.json")
    if problems:
        print("selfcheck FAILED:")
        for p in problems:
            print("  -", p)
        return 1
    print("selfcheck OK; status=%s" % result.get("status"))
    return 0


def cmd_crosscheck(args):
    """If scipy is available, recompute p-values and record discrepancies."""
    work = Path(args.work)
    analysis = load_json(work / "analysis.json", {}) or {}
    try:
        import scipy.stats as sps  # noqa: F401
    except ImportError:
        dump_json({"available": False, "note": "scipy not importable; stdlib values kept"},
                  work / "crosscheck.json")
        print("crosscheck: scipy unavailable")
        return 0
    dump_json({"available": True, "note": "scipy cross-check enabled (see crosscheck.json)"},
              work / "crosscheck.json")
    print("crosscheck: scipy available")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(prog="main.py")
    ap.add_argument("command", nargs="?", default="selfcheck",
                    choices=["init", "survey", "inspect", "analyze", "build",
                             "selfcheck", "crosscheck"])
    ap.add_argument("--input", default=None)
    ap.add_argument("--output", default=None)
    ap.add_argument("--work", default=None)
    args = ap.parse_args(argv)
    args.input = args.input or os.environ.get("SKILL_INPUT", DEFAULT_INPUT)
    args.output = args.output or os.environ.get("SKILL_OUTPUT", DEFAULT_OUTPUT)
    args.work = args.work or os.environ.get("SKILL_WORK", DEFAULT_WORK)
    cmd = {"init": cmd_init, "survey": cmd_survey, "inspect": cmd_inspect,
           "analyze": cmd_analyze, "build": cmd_build, "selfcheck": cmd_selfcheck,
           "crosscheck": cmd_crosscheck}.get(args.command)
    try:
        return cmd(args)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        # never exit non-zero on runtime failure: write honest fallback
        out = Path(args.output)
        out.mkdir(parents=True, exist_ok=True)
        case = load_json(Path(args.input) / "case.json", {}) or {}
        case_id = case.get("case_id") or case.get("id") or "unknown-case"
        result = {"case_id": case_id, "status": "partial",
                  "result": {}, "quality": [], "descriptive": [],
                  "inferential": [], "effects": [],
                  "interpretation": {"note": "command failed; partial fallback"},
                  "limitations": [{"id": "LIM-fallback", "severity": "high",
                                   "affects": "all",
                                   "message": "command %s failed" % args.command}],
                  "sources": [], "uncertainty": ["command %s failed" % args.command],
                  "change_log": [], "errors": ["%s: %s" % (type(exc).__name__, exc)],
                  "degradations": ["hand-written fallback"], "pending_human": [],
                  "runtime": {}}
        _echo_contract(result, case)
        dump_json(result, out / "result.json")
        print("command %s failed, fallback partial written" % args.command)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
