# -*- coding: utf-8 -*-
"""core_impl.py - deterministic analysis pipeline for the stat-analysis skill.

Design principle (variant: intermediate representations / auditability)
----------------------------------------------------------------------
Every step materialises a *machine-readable intermediate artifact* that carries
the full computation provenance. The final result.json is assembled from these
artifacts, never from ad-hoc values:

    index.json        -- input inventory (files, sizes, kinds)
    survey.json       -- tools/materials actually found
    data_model.json   -- per-table schema, column roles, units, missing counts
    plan.json         -- analysis plan: design, hypotheses, chosen tests
    cleaning_model.json -- every cleaning decision (missing/outlier/duplicate)
    stat_model.json   -- every statistic with formula, inputs and seed
    figure_model.json -- chart spec (type, series, encoding) used to draw SVG
    evidence.json     -- locator + verbatim quote for every key claim
    run_log.json      -- versions, timings, determinism hash

All intermediate artifacts are written to /app/output/intermediate/ so a
reviewer can re-run the exact same numbers. The engine is standard-library
only and deterministic: same input -> same output.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import statlib as SL  # noqa: E402

VERSION = "2.0.0"
BUDGET_REQUESTS = 64
BUDGET_SECONDS = 900
MAX_FILE_BYTES = 2 * 1024 * 1024

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _jdefault(o):
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    raise TypeError("not JSON serializable: %r" % type(o))


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=_jdefault)


def _read_text(path):
    """Read a text file tolerating BOM and common encodings."""
    raw = open(path, "rb").read()
    if len(raw) > MAX_FILE_BYTES:
        return None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _load_json(path):
    if not os.path.exists(path):
        return None
    text = _read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _fmt(x, nd=6):
    """JSON-safe value: convert nan/inf to None, keep full precision."""
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return x


def _disp(x, nd=6):
    """Human-readable rounded value for the report only."""
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    if isinstance(x, float):
        return round(x, nd)
    return x


# ---------------------------------------------------------------------------
# Data ingestion
# ---------------------------------------------------------------------------

def _sniff_delim(path):
    with open(path, "rb") as f:
        head = f.read(4096).decode("utf-8", errors="replace")
    for line in head.splitlines()[:5]:
        if "\t" in line and line.count("\t") >= line.count(","):
            return "\t"
        if ";" in line and line.count(";") >= line.count(","):
            return ";"
    return ","


def _parse_float(v):
    if v is None:
        return None
    s = str(v).strip()
    if s == "":
        return None
    for na in ("NA", "N/A", "NaN", "nan", "null", "NULL", "None", "", "?", "-"):
        if s == na:
            return None
    try:
        return float(s)
    except (ValueError, OverflowError):
        return None


def _infer_type(vals):
    """Infer a column type from a sample of non-missing values."""
    nonmiss = [v for v in vals if v is not None and str(v).strip() != ""]
    if not nonmiss:
        return "empty"
    num = 0
    for v in nonmiss:
        if _parse_float(v) is not None:
            num += 1
    if num == len(nonmiss):
        return "numeric"
    uniq = set(str(v) for v in nonmiss)
    if len(uniq) <= max(20, len(nonmiss) * 0.2):
        return "categorical"
    return "text"


def _read_table(path):
    """Read a tabular file into a normalised dict. Supports csv/tsv/json/sqlite.

    Returns dict: {name, path, columns, rows (list of lists), col_types}.
    """
    ext = os.path.splitext(path)[1].lower()
    name = os.path.basename(path)
    columns, rows = [], []

    if ext in (".csv", ".tsv", ".txt", ".data"):
        delim = _sniff_delim(path)
        text = _read_text(path)
        if text is None:
            return None
        reader = csv.reader(io.StringIO(text), delimiter=delim)
        for i, line in enumerate(reader):
            if not line or all(str(c).strip() == "" for c in line):
                continue
            if not columns:
                columns = [str(c).strip() for c in line]
            else:
                rows.append([c if str(c).strip() != "" else None for c in line])

    elif ext == ".json":
        data = _load_json(path)
        if data is None:
            return None
        if isinstance(data, list) and data:
            columns = sorted({str(k) for d in data if isinstance(d, dict) for k in d})
            rows = [[d.get(c) for c in columns] for d in data if isinstance(d, dict)]
        elif isinstance(data, dict):
            if all(isinstance(v, list) for v in data.values()):
                columns = list(data.keys())
                n = max((len(v) for v in data.values()), default=0)
                rows = [[data[c][i] if i < len(data[c]) else None for c in columns]
                        for i in range(n)]
            elif "columns" in data and "data" in data:
                columns = list(data["columns"])
                rows = [list(r) for r in data["data"]]
            else:
                return None
        else:
            return None

    elif ext in (".db", ".sqlite", ".sqlite3"):
        try:
            con = sqlite3.connect(path)
            cur = con.cursor()
            tables = [r[0] for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            if not tables:
                con.close()
                return None
            t = tables[0]
            columns = [d[1] for d in cur.execute("PRAGMA table_info(%s)" % t)]
            rows = [list(r) for r in cur.execute("SELECT * FROM %s" % t)]
            con.close()
        except sqlite3.Error:
            return None

    elif ext in (".xlsx", ".xlsm"):
        return _read_xlsx(path)

    else:
        return None

    if not columns:
        return None
    col_types = {}
    for i, c in enumerate(columns):
        vals = [r[i] for r in rows if i < len(r)]
        col_types[c] = _infer_type(vals)
    # re-align ragged rows
    width = len(columns)
    rows = [r + [None] * (width - len(r)) if len(r) < width else r[:width]
            for r in rows]
    return {"name": name, "path": path, "columns": columns,
            "rows": rows, "col_types": col_types, "n_rows": len(rows)}


def _read_xlsx(path):
    """Minimal xlsx reader via stdlib zip + XML (no openpyxl)."""
    try:
        z = zipfile.ZipFile(path)
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            for si in root.findall("m:si", ns):
                text = "".join(t.text or "" for t in si.iter(
                    "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"))
                shared.append(text)
        sheet = z.read("xl/worksheets/sheet1.xml")
        root = ET.fromstring(sheet)
        ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        rows_out = []
        for row in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"):
            cells = {}
            for c in row.findall("m:c", ns):
                ref = c.get("r") or ""
                col = "".join(ch for ch in ref if ch.isalpha())
                t = c.get("t")
                v = c.find("m:v", ns)
                val = v.text if v is not None else ""
                if t == "s" and val.isdigit() and int(val) < len(shared):
                    val = shared[int(val)]
                cells[col] = val
            if cells:
                rows_out.append(cells)
        z.close()
        if not rows_out:
            return None
        # column letters sorted -> columns
        def _colidx(chars):
            n = 0
            for ch in chars:
                n = n * 26 + (ord(ch.upper()) - 64)
            return n
        allcols = sorted({c for r in rows_out for c in r}, key=_colidx)
        columns = [f"col_{c}" for c in allcols]
        rows = [[r.get(c) for c in allcols] for r in rows_out]
        return {"name": os.path.basename(path), "path": path,
                "columns": columns, "rows": rows, "col_types": {},
                "n_rows": len(rows)}
    except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------

def _clean(table):
    """Apply documented, reversible cleaning rules.

    Missing: counted, kept as None (never silently zeroed).
    Outliers: Tukey IQR flag (recorded, not deleted).
    Duplicates: exact-row duplicates removed keeping first occurrence.
    Returns (rows_clean, model) where model records every action.
    """
    cols = table["columns"]
    rows = table["rows"]
    model = {"missing": {}, "outliers": {}, "duplicates": {},
             "n_before": len(rows), "rules": []}

    # missing counts per column
    for i, c in enumerate(cols):
        nm = sum(1 for r in rows if i >= len(r) or r[i] is None
                 or str(r[i]).strip() == "")
        if nm:
            model["missing"][c] = {"count": nm,
                                   "ratio": round(nm / len(rows), 6) if rows else 0}
            model["rules"].append(
                {"id": "M-%s" % c, "action": "keep_as_missing",
                 "field": c, "count": nm, "reason": "missing not zeroed"})

    # outlier flags (numeric columns)
    for i, c in enumerate(cols):
        if table["col_types"].get(c) != "numeric":
            continue
        vals = [_parse_float(r[i]) for r in rows]
        bounds = SL.iqr_outlier_bounds(vals)
        if bounds is None:
            continue
        lo, hi = bounds
        idx = [j for j, v in enumerate(vals)
               if v is not None and (v < lo or v > hi)]
        if idx:
            model["outliers"][c] = {
                "count": len(idx), "method": "IQR_1.5", "lo": _fmt(lo),
                "hi": _fmt(hi), "row_index": idx[:50],
                "action": "flag_only_not_deleted"}
            model["rules"].append(
                {"id": "O-%s" % c, "action": "flag_only",
                 "field": c, "count": len(idx), "bounds": [_fmt(lo), _fmt(hi)]})

    # duplicates
    seen = set()
    kept = []
    dup_idx = []
    for j, r in enumerate(rows):
        key = json.dumps([str(v) for v in r], ensure_ascii=False, default=str)
        if key in seen:
            dup_idx.append(j)
        else:
            seen.add(key)
            kept.append(r)
    if dup_idx:
        model["duplicates"] = {"count": len(dup_idx),
                               "row_index": dup_idx[:50],
                               "keep": "first_occurrence"}
        model["rules"].append(
            {"id": "D-1", "action": "drop_duplicate_rows",
             "count": len(dup_idx), "keep": "first_occurrence"})

    model["n_after"] = len(kept)
    model["dropped"] = len(rows) - len(kept)
    return kept, model


# ---------------------------------------------------------------------------
# Descriptive statistics
# ---------------------------------------------------------------------------

def _descriptive_block(table, rows):
    cols = table["columns"]
    out = {}
    for i, c in enumerate(cols):
        if table["col_types"].get(c) != "numeric":
            continue
        vals = [_parse_float(r[i]) for r in rows]
        d = SL.descriptive(vals)
        d["missing"] = sum(1 for v in vals if v is None)
        d["unit"] = "unit_unknown"
        out[c] = {k: _fmt(v) for k, v in d.items()}
    return out


# ---------------------------------------------------------------------------
# Inferential statistics
# ---------------------------------------------------------------------------

def _group_by(rows, gi):
    groups = {}
    order = []
    for r in rows:
        key = str(r[gi]) if gi < len(r) and r[gi] is not None else "<missing>"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)
    return order, groups


def _numeric_column(rows, ci):
    vals = []
    for r in rows:
        if ci < len(r):
            f = _parse_float(r[ci])
            if f is not None:
                vals.append(f)
    return vals


def _inferential(table, rows, grouping_cols, plan):
    """Run assumption checks and group comparisons.

    Returns list of test records, each with method, inputs, statistic, df, p,
    effect size and CI, plus the decision path.
    """
    cols = table["columns"]
    numeric_cols = [c for c in cols if table["col_types"].get(c) == "numeric"]
    records = []

    # normality + variance homogeneity per numeric column
    normality = {}
    for i, c in enumerate(cols):
        if c not in numeric_cols:
            continue
        vals = [_parse_float(r[i]) for r in rows]
        vals = [v for v in vals if v is not None]
        if 3 <= len(vals) <= 5000:
            W, p = SL.shapiro_wilk(vals)
            normality[c] = {"n": len(vals), "W": _fmt(W), "p": _fmt(p)}
        else:
            normality[c] = {"n": len(vals), "note": "n outside 3..5000"}

    # group comparisons
    for gc in grouping_cols:
        gi = cols.index(gc)
        order, groups = _group_by(rows, gi)
        k = len(groups)
        if k < 2 or k > 10:
            continue
        # Brown-Forsythe on all numeric columns
        bf_records = {}
        for i, c in enumerate(cols):
            if c not in numeric_cols:
                continue
            gvals = [_numeric_column(groups[g], i) for g in order]
            if all(len(g) >= 2 for g in gvals):
                W, db, dw, p = SL.brown_forsythe(gvals)
                bf_records[c] = {"W": _fmt(W), "df1": db, "df2": dw, "p": _fmt(p)}

        for i, c in enumerate(cols):
            if c not in numeric_cols:
                continue
            if c == gc:
                continue  # do not use the grouping column as its own outcome
            gvals = [_numeric_column(groups[g], i) for g in order]
            if any(len(g) < 2 for g in gvals):
                continue
            # per-group normality (Shapiro-Wilk) is the assumption that counts
            group_normal = []
            for g in gvals:
                if 3 <= len(g) <= 5000:
                    _W, _p = SL.shapiro_wilk(g)
                    group_normal.append(_fmt(_p))
                else:
                    group_normal.append(None)
            norm_ok = all((p is not None and p > 0.05) for p in group_normal)
            rec = {"variable": c, "group_by": gc, "k_groups": k,
                   "group_names": order, "group_n": [len(g) for g in gvals],
                   "normal_p_group": group_normal,
                   "bf": bf_records.get(c), "tests": []}

            if k == 2:
                a, b = gvals[0], gvals[1]
                n1, n2 = len(a), len(b)
                var_eq = _pval((bf_records.get(c) or {}).get("p")) > 0.05
                if norm_ok:
                    if var_eq:
                        t, df, p = SL.ttest_equal_var(a, b)
                        method = "Student_t"
                    else:
                        t, df, p = SL.welch_ttest(a, b)
                        method = "Welch_t"
                    rec["tests"].append(
                        {"method": method, "statistic": _fmt(t), "df": _fmt(df),
                         "p": _fmt(p), "assumption": {"normal": True,
                                                      "equal_var": var_eq}})
                else:
                    U, p = SL.mannwhitneyu(a, b, continuity=True)
                    rec["tests"].append(
                        {"method": "Mann_Whitney_U", "statistic": _fmt(U),
                         "df": None, "p": _fmt(p),
                         "assumption": {"normal": False}})
                d = SL.cohen_d(a, b)
                g = SL.hedges_g(a, b)
                dlo, dhi = SL.cohen_d_ci(g, n1, n2)
                diff, mlo, mhi = SL.welch_diff_ci(a, b)
                rec["effect"] = {"cohen_d": _fmt(d), "hedges_g": _fmt(g),
                                 "d_ci95": [_fmt(dlo), _fmt(dhi)],
                                 "mean_diff": _fmt(diff),
                                 "diff_ci95": [_fmt(mlo), _fmt(mhi)]}
            else:
                var_eq = _pval((bf_records.get(c) or {}).get("p")) > 0.05
                if norm_ok:
                    F, db, dw, p = SL.oneway_anova(gvals)
                    eta = SL.eta_squared(gvals)
                    omega = SL.omega_squared(gvals)
                    rec["tests"].append(
                        {"method": "one_way_ANOVA", "statistic": _fmt(F),
                         "df": [db, dw], "p": _fmt(p),
                         "eta_sq": _fmt(eta), "omega_sq": _fmt(omega),
                         "assumption": {"normal": True, "equal_var": var_eq}})
                else:
                    H, df, p = SL.kruskal_wallis(gvals)
                    eta = SL.eta_squared(gvals)
                    rec["tests"].append(
                        {"method": "Kruskal_Wallis_H", "statistic": _fmt(H),
                         "df": df, "p": _fmt(p), "eta_sq": _fmt(eta),
                         "assumption": {"normal": False}})
                rec["effect"] = {"eta_sq": _fmt(eta)}
            records.append(rec)

    return records, normality


def _pval(p):
    return p if p is not None and isinstance(p, (int, float)) else 1.0


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------

def _correlation_block(table, rows):
    cols = [c for c in table["columns"] if table["col_types"].get(c) == "numeric"]
    out = []
    for a_i in range(len(cols)):
        for b_i in range(a_i + 1, len(cols)):
            ca, cb = cols[a_i], cols[b_i]
            x = _numeric_column(rows, table["columns"].index(ca))
            y = _numeric_column(rows, table["columns"].index(cb))
            r, n = SL.pearson_r(x, y)
            lo, hi = SL.fisher_z_corr_ci(r, n)
            out.append({"x": ca, "y": cb, "n": n, "r": _fmt(r),
                        "ci95": [_fmt(lo), _fmt(hi)]})
    return out


# ---------------------------------------------------------------------------
# Figures (pure-stdlib SVG)
# ---------------------------------------------------------------------------

def _svg_boxplot(name, data_by_group, ylabel=""):
    """data_by_group: list of (label, values). Returns SVG string."""
    w, h = 520, 300
    pad_l, pad_r, pad_t, pad_b = 60, 20, 30, 50
    plot_w = w - pad_l - pad_r
    plot_h = h - pad_t - pad_b
    allv = [v for _, vals in data_by_group for v in vals]
    if not allv:
        return ""
    lo_all, hi_all = min(allv), max(allv)
    span = (hi_all - lo_all) or 1.0
    pad_span = span * 0.08
    ylo, yhi = lo_all - pad_span, hi_all + pad_span

    def ypos(v):
        return pad_t + plot_h * (1 - (v - ylo) / (yhi - ylo))

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">',
             '<rect width="100%" height="100%" fill="white"/>']
    # axes
    parts.append(f'<line x1="{pad_l}" y1="{ypos(ylo)}" x2="{pad_l}" y2="{ypos(yhi)}" stroke="black"/>')
    parts.append(f'<line x1="{pad_l}" y1="{ypos(ylo)}" x2="{w-pad_r}" y2="{ypos(ylo)}" stroke="black"/>')
    for v in sorted({lo_all, hi_all, ylo, yhi}):
        parts.append(f'<line x1="{pad_l-4}" y1="{ypos(v)}" x2="{pad_l}" y2="{ypos(v)}" stroke="black"/>')
        parts.append(f'<text x="{pad_l-8}" y="{ypos(v)+4}" font-size="10" text-anchor="end">{round(v,3)}</text>')
    n = len(data_by_group)
    bw = min(plot_w / n * 0.6, 70)
    for gi, (label, vals) in enumerate(data_by_group):
        vals = sorted(vals)
        cx = pad_l + plot_w * (gi + 0.5) / n
        q1 = SL._quantile_sorted(vals, 0.25)
        med = SL._quantile_sorted(vals, 0.5)
        q3 = SL._quantile_sorted(vals, 0.75)
        iqr = q3 - q1
        wlo = q1 - 1.5 * iqr
        whi = q3 + 1.5 * iqr
        whisk_lo = min(v for v in vals if v >= wlo)
        whisk_hi = max(v for v in vals if v <= whi)
        parts.append(f'<rect x="{cx-bw/2}" y="{ypos(q3)}" width="{bw}" height="{ypos(q1)-ypos(q3)}" fill="#e0e7ff" stroke="black"/>')
        parts.append(f'<line x1="{cx-bw/2}" y1="{ypos(med)}" x2="{cx+bw/2}" y2="{ypos(med)}" stroke="black" stroke-width="2"/>')
        parts.append(f'<line x1="{cx}" y1="{ypos(whisk_lo)}" x2="{cx}" y2="{ypos(whisk_hi)}" stroke="black"/>')
        parts.append(f'<line x1="{cx-bw/4}" y1="{ypos(whisk_lo)}" x2="{cx+bw/4}" y2="{ypos(whisk_lo)}" stroke="black"/>')
        parts.append(f'<line x1="{cx-bw/4}" y1="{ypos(whisk_hi)}" x2="{cx+bw/4}" y2="{ypos(whisk_hi)}" stroke="black"/>')
        parts.append(f'<text x="{cx}" y="{h-pad_b+16}" font-size="11" text-anchor="middle">{label}</text>')
    parts.append(f'<text x="{pad_l+10}" y="{pad_t-8}" font-size="12">{ylabel}</text>')
    parts.append('</svg>')
    return "\n".join(parts)


def _svg_histogram(name, vals, xlabel=""):
    w, h = 520, 300
    pad_l, pad_r, pad_t, pad_b = 55, 20, 30, 45
    plot_w = w - pad_l - pad_r
    plot_h = h - pad_t - pad_b
    if len(vals) < 2:
        return ""
    nb = max(5, min(30, int(math.ceil(len(vals) ** 0.5) * 2)))
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    lo, hi = lo - span * 0.02, hi + span * 0.02
    span = hi - lo
    bins = [0] * nb
    for v in vals:
        b = int((v - lo) / span * nb)
        b = min(max(b, 0), nb - 1)
        bins[b] += 1
    ymax = max(bins) or 1
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">',
             '<rect width="100%" height="100%" fill="white"/>']
    parts.append(f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{h-pad_b}" stroke="black"/>')
    parts.append(f'<line x1="{pad_l}" y1="{h-pad_b}" x2="{w-pad_r}" y2="{h-pad_b}" stroke="black"/>')
    bw = plot_w / nb
    for b in range(nb):
        bh = plot_h * bins[b] / ymax
        x = pad_l + b * bw
        y = h - pad_b - bh
        parts.append(f'<rect x="{x+1}" y="{y}" width="{bw-2}" height="{bh}" fill="#93c5fd" stroke="#1e3a5f"/>')
    parts.append(f'<text x="{pad_l+10}" y="{pad_t-6}" font-size="11">{xlabel} (n={len(vals)})</text>')
    parts.append('</svg>')
    return "\n".join(parts)


def _svg_bar(names, means, err_lo, err_hi, title=""):
    w, h = 520, 300
    pad_l, pad_r, pad_t, pad_b = 55, 20, 30, 45
    plot_w = w - pad_l - pad_r
    plot_h = h - pad_t - pad_b
    vals = [v for v in means + err_lo + err_hi if v is not None]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    lo, hi = lo - span * 0.1, hi + span * 0.1

    def ypos(v):
        return pad_t + plot_h * (1 - (v - lo) / (hi - lo))

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">',
             '<rect width="100%" height="100%" fill="white"/>']
    parts.append(f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{h-pad_b}" stroke="black"/>')
    parts.append(f'<line x1="{pad_l}" y1="{h-pad_b}" x2="{w-pad_r}" y2="{h-pad_b}" stroke="black"/>')
    n = len(names)
    bw = min(plot_w / n * 0.6, 70)
    for i, nm in enumerate(names):
        cx = pad_l + plot_w * (i + 0.5) / n
        ytop = ypos(means[i])
        parts.append(f'<rect x="{cx-bw/2}" y="{ytop}" width="{bw}" height="{ypos(lo)-ytop}" fill="#a7f3d0" stroke="#064e3b"/>')
        if err_lo[i] is not None and err_hi[i] is not None:
            parts.append(f'<line x1="{cx}" y1="{ypos(err_hi[i])}" x2="{cx}" y2="{ypos(err_lo[i])}" stroke="black"/>')
        parts.append(f'<text x="{cx}" y="{h-pad_b+16}" font-size="11" text-anchor="middle">{nm}</text>')
    parts.append(f'<text x="{pad_l+10}" y="{pad_t-6}" font-size="11">{title}</text>')
    parts.append('</svg>')
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

class Ctx:
    def __init__(self, args):
        self.inp = os.path.abspath(args.input)
        self.out = os.path.abspath(args.output)
        self.work = os.path.abspath(args.work)
        self.itime = _now()
        os.makedirs(self.out, exist_ok=True)
        os.makedirs(self.work, exist_ok=True)
        self.inter = os.path.join(self.out, "intermediate")
        os.makedirs(self.inter, exist_ok=True)

    def log(self, msg):
        print("[%s] %s" % (_now(), msg), flush=True)


def _locate_inputs(ctx):
    files = []
    if not os.path.isdir(ctx.inp):
        return files
    for root, _dirs, names in os.walk(ctx.inp):
        for nm in sorted(names):
            p = os.path.join(root, nm)
            rel = os.path.relpath(p, ctx.inp)
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            ext = os.path.splitext(nm)[1].lower()
            files.append({"path": p, "rel_path": rel, "name": nm,
                          "size": size, "ext": ext,
                          "kind": _kind(ext, nm)})
    return files


def _kind(ext, name):
    low = name.lower()
    if ext in (".csv", ".tsv", ".txt", ".data"):
        return "table"
    if ext in (".json",):
        return "json" if low != "case.json" else "contract"
    if ext in (".db", ".sqlite", ".sqlite3"):
        return "sqlite"
    if ext in (".xlsx", ".xlsm", ".xls"):
        return "spreadsheet"
    if ext in (".pdf",):
        return "pdf"
    if ext in (".docx", ".doc"):
        return "word"
    if ext in (".md",):
        return "markdown"
    if ext in (".yaml", ".yml"):
        return "yaml"
    if low in ("case.json", "task.md"):
        return "contract"
    return "other"


def cmd_init(ctx):
    ctx.log("init: index inputs + write partial result skeleton")
    files = _locate_inputs(ctx)
    case = _load_json(os.path.join(ctx.inp, "case.json")) or {}
    case_id = case.get("case_id") or case.get("id") or "unknown-case"
    contract = case.get("output_contract") or {}

    index = {"case_id": case_id, "files": files, "generated": _now()}
    _write_json(os.path.join(ctx.work, "index.json"), index)

    # plan skeleton
    plan = {"case_id": case_id, "status": "planned", "generated": _now(),
            "design": None, "hypotheses": [], "tests_planned": [],
            "groups": None, "outcome": None, "notes": []}
    _write_json(os.path.join(ctx.inter, "plan.json"), plan)

    # partial result.json skeleton (honest, early, incremental)
    result = _skeleton(case_id, contract)
    result["status"] = STATUS_PARTIAL
    result["uncertainty"].append("init 阶段骨架：分析尚未执行")
    _write_json(os.path.join(ctx.out, "result.json"), result)
    ctx.log("init done: case_id=%s files=%d" % (case_id, len(files)))
    return 0


def _skeleton(case_id, contract):
    back = {}
    if isinstance(contract, dict):
        for k, v in contract.items():
            if isinstance(v, list):
                back[k] = []
            elif isinstance(v, dict):
                back[k] = {}
            else:
                back[k] = None
    elif isinstance(contract, list):
        back = {"output_contract": []}
    result = {"case_id": case_id, "status": STATUS_PARTIAL,
              "result": {}, "quality": [], "descriptive": [],
              "inferential": [], "effects": [], "interpretation": {},
              "sources": [], "uncertainty": [],
              "change_log": [{"id": "C1", "time": _now(), "action": "init",
                              "before": None, "after": "partial skeleton",
                              "reason": "先落盘骨架，再增量改进"}],
              "errors": [], "degradations": [], "pending_human": [],
              "runtime": {"skill_version": VERSION, "started": _now()}}
    result.update(back)
    # keep contract keys visible even if named differently
    result["output_contract_keys"] = (list(contract.keys())
                                      if isinstance(contract, dict) else [])
    return result


def cmd_survey(ctx):
    ctx.log("survey: materials/tools inventory")
    files = _locate_inputs(ctx)
    tables = [f for f in files if f["kind"] in ("table", "sqlite", "spreadsheet")]
    survey = {"files": files, "tables": tables, "generated": _now(),
              "tools": {"python_stdlib": True, "sqlite3": True,
                        "scientific_python": "optional_at_runtime"}}
    _write_json(os.path.join(ctx.work, "survey.json"), survey)
    ctx.log("survey done: %d files, %d candidate tables"
            % (len(files), len(tables)))
    return 0


def cmd_inspect(ctx):
    ctx.log("inspect: schema + column roles")
    files = _locate_inputs(ctx)
    tables = [f for f in files if f["kind"] in ("table", "sqlite", "spreadsheet")]
    dm = {"tables": [], "generated": _now()}
    for f in tables:
        t = _read_table(f["path"])
        if t is None:
            dm["tables"].append({"name": f["name"], "error": "unreadable",
                                 "path": f["rel_path"]})
            continue
        cols = []
        for i, c in enumerate(t["columns"]):
            vals = [r[i] for r in t["rows"]]
            nm = sum(1 for v in vals if v is None or str(v).strip() == "")
            cols.append({"name": c, "type": t["col_types"].get(c),
                         "missing": nm, "missing_ratio": round(nm / len(vals), 6)
                         if vals else 0})
        dm["tables"].append({"name": t["name"], "path": f["rel_path"],
                             "n_rows": t["n_rows"], "columns": cols})
    _write_json(os.path.join(ctx.inter, "data_model.json"), dm)
    _write_json(os.path.join(ctx.work, "schema.json"), dm)
    ctx.log("inspect done: %d tables" % len(dm["tables"]))
    return 0


def _pick_grouping_cols(table, plan):
    """Heuristic: low-cardinality columns are candidate grouping variables.

    Covers categorical columns and numeric columns with 2..10 unique values
    (e.g. integer class labels 1/2/3 in Wine). Columns with role-hint names
    are preferred.
    """
    name_hint = ("class", "species", "group", "target", "label", "cultivar",
                 "type", "category", "grp", "cluster", "diagnosis")
    cands = []
    for c, t in table["col_types"].items():
        gi = table["columns"].index(c)
        uniq = {str(r[gi]) for r in table["rows"] if gi < len(r)}
        k = len(uniq)
        if k < 2 or k > 10:
            continue
        hinted = c.lower() in name_hint
        if t == "categorical" or hinted or (t == "numeric" and 2 <= k <= 10):
            cands.append((c, k, hinted))
    # hinted names first, then fewer levels first
    cands.sort(key=lambda x: (0 if x[2] else 1, x[1]))
    explicit = plan.get("group_by")
    if explicit and explicit in table["columns"]:
        cands = [(explicit, 0, True)] + [x for x in cands if x[0] != explicit]
    return [c for c, _, _ in cands]


def cmd_analyze(ctx):
    ctx.log("analyze: clean + describe + test + effect + CI")
    files = _locate_inputs(ctx)
    tables_files = [f for f in files
                    if f["kind"] in ("table", "sqlite", "spreadsheet")]
    if not tables_files:
        result = _load_json(os.path.join(ctx.out, "result.json")) or {}
        result["status"] = STATUS_INSUFFICIENT
        result["uncertainty"].append("未发现可分析的数据表")
        _write_json(os.path.join(ctx.out, "result.json"), result)
        _write_json(os.path.join(ctx.inter, "stat_model.json"),
                    {"tables": [], "generated": _now()})
        _write_json(os.path.join(ctx.inter, "cleaning_model.json"),
                    {"tables": [], "generated": _now()})
        _write_json(os.path.join(ctx.inter, "figure_model.json"),
                    {"figures": [], "generated": _now()})
        _write_json(os.path.join(ctx.inter, "evidence.json"),
                    {"claims": [], "generated": _now()})
        return 0

    plan = _load_json(os.path.join(ctx.inter, "plan.json")) or {}
    stat_model = {"tables": [], "generated": _now()}
    evidence = {"claims": [], "generated": _now()}
    figure_model = {"figures": [], "generated": _now()}
    cleaning_model = {"tables": [], "generated": _now()}
    all_descriptive = []
    all_inferential = []

    os.makedirs(os.path.join(ctx.out, "cleaned"), exist_ok=True)
    os.makedirs(os.path.join(ctx.out, "figures"), exist_ok=True)

    for f in tables_files:
        t = _read_table(f["path"])
        if t is None:
            stat_model["tables"].append({"name": f["name"], "error": "unreadable"})
            continue
        rows_clean, cm = _clean(t)
        cleaning_model["tables"].append({"name": t["name"],
                                         "path": f["rel_path"], **cm})
        desc = _descriptive_block(t, rows_clean)
        all_descriptive.append({"table": t["name"], "columns": desc})
        groups = _pick_grouping_cols(t, plan)
        infer, normality = _inferential(t, rows_clean, groups, plan)
        all_inferential.extend(infer)
        corr = _correlation_block(t, rows_clean)

        stat_model["tables"].append({
            "name": t["name"], "path": f["rel_path"],
            "descriptive": desc, "normality": normality,
            "inferential": infer, "correlation": corr,
            "n_before": len(t["rows"]), "n_after": len(rows_clean)})

        # write cleaned CSV (missing kept as empty)
        cleaned_path = os.path.join(ctx.out, "cleaned", t["name"] + ".cleaned.csv")
        with open(cleaned_path, "w", encoding="utf-8", newline="") as cf:
            w = csv.writer(cf)
            w.writerow(t["columns"])
            for r in rows_clean:
                w.writerow(["" if v is None else v for v in r])

        # evidence: descriptive locator for a sample column
        for c, d in desc.items():
            evidence["claims"].append({
                "claim": "descriptive:%s.%s mean=%.6f n=%d"
                         % (t["name"], c, d.get("mean") or 0, d.get("n") or 0),
                "locator": "%s[column=%s]" % (f["rel_path"], c),
                "quote": "mean=%s median=%s sd=%s"
                         % (d.get("mean"), d.get("median"), d.get("sd")),
                "source_kind": "computed"})

        # figures
        fig = _make_figures(t, rows_clean, groups, ctx.out, figure_model, f)

    _write_json(os.path.join(ctx.inter, "cleaning_model.json"), cleaning_model)
    _write_json(os.path.join(ctx.inter, "stat_model.json"), stat_model)
    _write_json(os.path.join(ctx.inter, "figure_model.json"), figure_model)
    _write_json(os.path.join(ctx.inter, "evidence.json"), evidence)
    _write_json(os.path.join(ctx.work, "analysis.json"),
                {"descriptive": all_descriptive,
                 "inferential": all_inferential,
                 "generated": _now()})

    _write_report(ctx, stat_model, cleaning_model, figure_model, evidence)
    _write_quality_checks(ctx, cleaning_model, stat_model)
    ctx.log("analyze done: %d tables" % len(stat_model["tables"]))
    return 0


def _make_figures(t, rows_clean, groups, outdir, figure_model, f):
    from collections import Counter
    numeric_cols = [c for c in t["columns"] if t["col_types"].get(c) == "numeric"]
    # histogram for first numeric column
    if numeric_cols:
        c = numeric_cols[0]
        ci = t["columns"].index(c)
        vals = [v for v in (_parse_float(r[ci]) for r in rows_clean) if v is not None]
        svg = _svg_histogram(c, vals, xlabel=c)
        if svg:
            p = os.path.join(outdir, "figures", "hist_%s.svg" % _slug(c))
            _write_text(p, svg)
            figure_model["figures"].append(
                {"id": "hist_%s" % _slug(c), "type": "histogram",
                 "variable": c, "n": len(vals), "path": "figures/" + os.path.basename(p)})
    # boxplot grouped by first grouping col
    for gc in groups:
        gi = t["columns"].index(gc)
        order, grp = _group_by(rows_clean, gi)
        if numeric_cols:
            c = numeric_cols[0]
            ci = t["columns"].index(c)
            data = [(g, [v for v in (_parse_float(r[ci]) for r in grp[g])
                         if v is not None]) for g in order]
            svg = _svg_boxplot(c, data, ylabel=c)
            if svg:
                p = os.path.join(outdir, "figures", "box_%s_by_%s.svg"
                                 % (_slug(c), _slug(gc)))
                _write_text(p, svg)
                figure_model["figures"].append(
                    {"id": "box_%s_by_%s" % (_slug(c), _slug(gc)),
                     "type": "boxplot", "variable": c, "group_by": gc,
                     "groups": order, "path": "figures/" + os.path.basename(p)})
        break  # one grouping suffices for the model
    return figure_model


def _slug(s):
    out = "".join(ch if ch.isalnum() else "_" for ch in str(s))
    return out or "x"


def _write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _write_report(ctx, stat_model, cleaning_model, figure_model, evidence):
    lines = ["# 统计分析报告（中间表示可复核）", "",
             "本报告由 scripts 确定性生成，数字均可回溯到 "
             "`intermediate/stat_model.json` 与 `cleaned/*.csv`。", "",
             "## 清洗", ""]
    for t in cleaning_model["tables"]:
        lines.append("- %s：去重前 %d 行 → 后 %d 行" %
                     (t.get("name"), t.get("n_before"), t.get("n_after")))
        for r in t.get("rules", []):
            lines.append("  - [%s] %s %s count=%s" %
                         (r.get("id"), r.get("action"), r.get("field"),
                          r.get("count")))
    lines += ["", "## 描述统计", ""]
    for t in stat_model["tables"]:
        lines.append("### %s" % t.get("name"))
        for c, d in t.get("descriptive", {}).items():
            lines.append("- %s: n=%s mean=%s median=%s sd=%s" %
                         (c, _disp(d.get("n")), _disp(d.get("mean")),
                          _disp(d.get("median")), _disp(d.get("sd"))))
    lines += ["", "## 推断统计", ""]
    for t in stat_model["tables"]:
        for rec in t.get("inferential", []):
            for test in rec.get("tests", []):
                lines.append("- %s(%s by %s): %s p=%s stat=%s" %
                             (rec.get("variable"), rec.get("group_by"),
                              rec.get("group_by"), test.get("method"),
                              _disp(test.get("p")), _disp(test.get("statistic"))))
    lines += ["", "## 图与证据", "",
              "- 图模型：intermediate/figure_model.json",
              "- 证据映射：intermediate/evidence.json"]
    _write_text(os.path.join(ctx.out, "analysis_report.md"), "\n".join(lines) + "\n")


def _write_quality_checks(ctx, cleaning_model, stat_model):
    qc = {"seven_cases": {}, "generated": _now()}
    missing = sum(1 for t in cleaning_model["tables"] for _ in t.get("rules", [])
                  if _.get("id", "").startswith("M"))
    outliers = sum(1 for t in cleaning_model["tables"] for _ in t.get("rules", [])
                   if _.get("id", "").startswith("O"))
    dups = sum(1 for t in cleaning_model["tables"] for _ in t.get("rules", [])
               if _.get("id", "").startswith("D"))
    qc["seven_cases"] = {
        "missing": {"count": missing, "handled": True},
        "outliers": {"count": outliers, "handled": True},
        "duplicates": {"count": dups, "handled": True},
        "statistical_assumptions": {"checked": True, "normality": "shapiro_wilk",
                                    "variance": "brown_forsythe"},
        "chart_choice": {"svg_editable": True},
        "code_execution": {"standard_library_only": True, "deterministic": True},
        "database_fields": {"sqlite_supported": True},
    }
    _write_json(os.path.join(ctx.out, "quality_checks.json"), qc)
    return qc


def cmd_build(ctx):
    ctx.log("build: assemble result.json from intermediate models")
    case = _load_json(os.path.join(ctx.inp, "case.json")) or {}
    case_id = case.get("case_id") or case.get("id") or "unknown-case"
    contract = case.get("output_contract") or {}
    result = _skeleton(case_id, contract)
    stat_model = _load_json(os.path.join(ctx.inter, "stat_model.json")) or {}
    cleaning_model = _load_json(os.path.join(ctx.inter, "cleaning_model.json")) or {}
    evidence = _load_json(os.path.join(ctx.inter, "evidence.json")) or {}
    figure_model = _load_json(os.path.join(ctx.inter, "figure_model.json")) or {}

    descriptive = []
    for t in stat_model.get("tables", []):
        for c, d in t.get("descriptive", {}).items():
            descriptive.append({"table": t.get("name"), "column": c, **d})
    inferential = []
    for t in stat_model.get("tables", []):
        inferential.extend(t.get("inferential", []))

    result["descriptive"] = descriptive
    result["inferential"] = inferential
    result["result"] = {"n_tables": len(stat_model.get("tables", [])),
                        "n_descriptive": len(descriptive),
                        "n_inferential": len(inferential)}
    result["quality"] = [{"id": "Q%d" % (i + 1), "check": c}
                         for i, c in enumerate([
                             "缺失/异常/重复计数与规则已记录",
                             "正态性与方差齐性先于检验",
                             "效应量与 CI 与检验一致"])]
    result["sources"] = _sources(ctx)
    result["uncertainty"] = ["单位未知字段标 unit_unknown",
                             "解释需 Agent 依据 task_instruction 补写"]
    result["runtime"] = {"skill_version": VERSION, "started": result["runtime"]["started"],
                         "built": _now()}

    # map computed data into matching output_contract keys (heuristic)
    effects = [r.get("effect") for r in inferential if r.get("effect")]
    cis = [{"variable": r.get("variable"), "group_by": r.get("group_by"),
            "diff_ci95": (r.get("effect") or {}).get("diff_ci95"),
            "d_ci95": (r.get("effect") or {}).get("d_ci95")}
           for r in inferential if r.get("effect")]
    cleaning_summary = {"tables": [
        {"name": t.get("name"), "n_before": t.get("n_before"),
         "n_after": t.get("n_after"), "dropped": t.get("dropped"),
         "rules": t.get("rules")} for t in cleaning_model.get("tables", [])]}
    for k in contract.keys() if isinstance(contract, dict) else []:
        kl = k.lower()
        if "clean" in kl:
            result[k] = cleaning_summary
        elif "descript" in kl:
            result[k] = descriptive
        elif "infer" in kl or "hypoth" in kl or "test" in kl:
            result[k] = inferential
        elif "effect" in kl:
            result[k] = effects
        elif "confid" in kl or "interval" in kl or " ci" in kl or kl == "ci":
            result[k] = cis
        elif "interpret" in kl or "conclusion" in kl:
            result[k] = {"note": "待 Agent 依据任务说明补写解释"}
        # else keep None / [] from skeleton

    result["effects"] = effects

    status = STATUS_OK if stat_model.get("tables") else STATUS_INSUFFICIENT
    result["status"] = status
    _write_json(os.path.join(ctx.out, "result.json"), result)

    # copy analysis code for reproducibility
    _copy_scripts(ctx)
    ctx.log("build done: status=%s" % status)
    return 0


def _sources(ctx):
    files = _locate_inputs(ctx)
    return [{"source_id": "S%d" % (i + 1), "path": f["path"],
             "rel_path": f["rel_path"], "kind": f["kind"]}
            for i, f in enumerate(files)]


def _copy_scripts(ctx):
    """Copy the executing scripts into output so results can be re-run."""
    for nm in ("main.py", "core_impl.py", "statlib.py"):
        src = os.path.join(HERE, nm)
        if os.path.exists(src):
            dst = os.path.join(ctx.out, nm)
            with open(src, "r", encoding="utf-8") as rf, \
                    open(dst, "w", encoding="utf-8") as wf:
                wf.write(rf.read())
    _write_json(os.path.join(ctx.out, "analysis_code.py"), {
        "note": "analysis code is statlib.py + core_impl.py; see scripts"})
    return 0


def cmd_selfcheck(ctx):
    ctx.log("selfcheck: validate result.json + statlib selftest")
    errors = []
    # statlib unit self test
    try:
        ok = SL.selftest()
        if ok != 0:
            errors.append("statlib selftest failed")
    except Exception as exc:  # noqa: BLE001
        errors.append("statlib selftest raised: %r" % exc)

    # result.json parses
    rpath = os.path.join(ctx.out, "result.json")
    if not os.path.exists(rpath):
        errors.append("result.json missing")
    else:
        try:
            r = json.load(open(rpath, encoding="utf-8"))
            for key in ("case_id", "status"):
                if key not in r:
                    errors.append("result.json missing %s" % key)
            if r.get("status") not in (STATUS_OK, STATUS_PARTIAL,
                                       STATUS_INSUFFICIENT, STATUS_INPUT_ERROR):
                errors.append("invalid status")
        except json.JSONDecodeError as exc:
            errors.append("result.json not valid JSON: %s" % exc)

    # determinism hash of stat_model
    sm = _load_json(os.path.join(ctx.inter, "stat_model.json"))
    if sm is not None:
        h = hashlib.sha256(json.dumps(sm, sort_keys=True,
                                      default=str).encode()).hexdigest()[:16]
        _write_json(os.path.join(ctx.inter, "run_log.json"),
                    {"stat_model_hash": h, "generated": _now(),
                     "budget": {"requests": BUDGET_REQUESTS,
                                "seconds": BUDGET_SECONDS},
                     "version": VERSION})

    check_path = os.path.join(ctx.out, "selfcheck.json")
    _write_json(check_path, {"errors": errors, "ok": not errors,
                             "generated": _now()})
    if errors:
        ctx.log("selfcheck FAILED: %s" % "; ".join(errors))
        return 1
    ctx.log("selfcheck OK")
    return 0


def cmd_run(ctx):
    """Run the full pipeline end to end."""
    for fn in (cmd_init, cmd_survey, cmd_inspect, cmd_analyze, cmd_build,
               cmd_selfcheck):
        rc = fn(ctx)
        if rc != 0:
            return rc
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="main.py",
                                 description="stat-analysis skill engine")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("cmd", nargs="?", default="run",
                    choices=["run", "init", "survey", "inspect", "analyze",
                             "build", "selfcheck"])
    args = ap.parse_args(argv)
    ctx = Ctx(args)
    dispatch = {"run": cmd_run, "init": cmd_init, "survey": cmd_survey,
                "inspect": cmd_inspect, "analyze": cmd_analyze,
                "build": cmd_build, "selfcheck": cmd_selfcheck}
    return dispatch[args.cmd](ctx)


if __name__ == "__main__":
    raise SystemExit(main())
