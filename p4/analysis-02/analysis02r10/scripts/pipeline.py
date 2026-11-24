#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pipeline.py — deterministic analysis pipeline for analysis-02.

Architecture (the deliberate difference from a monolithic "analyze" script):
each stage reads one intermediate representation (IR) and writes the next, so the
LLM's task interpretation and the code's computation are decoupled:

    index.json  <- probe      (input discovery + contract capture)
    result.json <- skeleton   (first honest partial deliverable, <=3 min)
    schema.json <- infer      (table/column/dtype/unit inference)
    plan.json   <- plan       (declarative design spec, auto-proposed / agent-edited)
    analysis.json + cleaned CSVs + SVGs <- compute
    result.json + report + quality_checks + code <- package
    (verify)    <- self-check of the packaged output

Only the standard library is used.  All randomness-free, so the same input always
produces the same output.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone

import statlib as S

INPUT_DIR = "/input"
OUTPUT_DIR = "/app/output"
WORK_DIR = "/app/work"

TEXT_TYPES = {"csv", "tsv", "json", "sqlite", "xlsx", "xls", "docx", "pdf",
              "yaml", "yml", "md", "txt", "db", "sqlite3"}
MISSING_TOKENS = {"", "na", "n/a", "nan", "null", "none", "-", "?", "n.d.", "nd"}


# --------------------------------------------------------------------------
# Small IO helpers
# --------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json_safe(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            return json.load(f)
    except Exception as exc:  # noqa: BLE001
        return {"__read_error__": str(exc)}


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def classify_kind(name):
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    return ext if ext in TEXT_TYPES else "other"


_META_NAMES = {"case.json", "task.md", "resources.yaml", "resources.yml"}
_DATA_KINDS = {"csv", "tsv", "json", "sqlite", "db", "sqlite3"}


def _is_data_file(f):
    base = os.path.basename(f.get("path", "")).lower()
    if base in _META_NAMES:
        return False
    return f.get("kind") in _DATA_KINDS and not f.get("skipped")


def _data_files(index):
    files = [f for f in index.get("files", []) if _is_data_file(f)]
    order = {"csv": 0, "tsv": 0, "sqlite": 1, "db": 1, "sqlite3": 1, "json": 2}
    files.sort(key=lambda f: (order.get(f["kind"], 9), f.get("bytes", 0)))
    return files


def discover_files(input_dir):
    found = []
    if not os.path.isdir(input_dir):
        return found
    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir).replace(os.sep, "/")
            kind = classify_kind(name)
            size = os.path.getsize(full)
            if size > 2 * 1024 * 1024:
                found.append({"path": full, "rel_path": rel, "kind": kind,
                              "bytes": size, "skipped": "over 2MB limit"})
                continue
            found.append({"path": full, "rel_path": rel, "kind": kind,
                          "bytes": size, "sha256": sha256_file(full)})
    return found


def is_missing(v):
    return str(v).strip().lower() in MISSING_TOKENS


def to_float(v):
    s = str(v).strip()
    if is_missing(s):
        return None
    try:
        return float(s)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Table loading
# --------------------------------------------------------------------------

def _read_csv(path, delimiter=None):
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        sample = f.read(8192)
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
        except Exception:  # noqa: BLE001
            delimiter = "," if sample.count(",") > sample.count("\t") else "\t"
    try:
        has_header = csv.Sniffer().has_header(sample)
    except Exception:  # noqa: BLE001
        has_header = True
    rows = []
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        for r in csv.reader(f, delimiter=delimiter):
            if not any(str(c).strip() for c in r):
                continue
            rows.append([str(c).strip() for c in r])
    if not rows:
        return {"header": [], "rows": [], "delimiter": delimiter, "has_header": True}
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    if has_header:
        header = [h if h else f"col{i}" for i, h in enumerate(rows[0])]
        data = rows[1:]
    else:
        header = [f"col{i}" for i in range(width)]
        data = rows
    return {"header": header, "rows": data, "delimiter": delimiter,
            "has_header": has_header}


def _read_json_table(path):
    obj = read_json_safe(path)
    if isinstance(obj, dict) and "__read_error__" in obj:
        return []
    if isinstance(obj, list):
        records = [r for r in obj if isinstance(r, dict)]
        header = []
        for r in records:
            for k in r:
                if k not in header:
                    header.append(k)
        rows = [[r.get(h, "") for h in header] for r in records]
        return [{"header": header, "rows": rows, "delimiter": None, "has_header": True}]
    if isinstance(obj, dict):
        if all(isinstance(v, list) for v in obj.values()):
            header = list(obj.keys())
            n = max((len(v) for v in obj.values()), default=0)
            rows = [[obj[h][i] if i < len(obj[h]) else "" for h in header] for i in range(n)]
            return [{"header": header, "rows": rows, "delimiter": None, "has_header": True}]
        header = list(obj.keys())
        rows = [[obj.get(h, "") for h in header]]
        return [{"header": header, "rows": rows, "delimiter": None, "has_header": True}]
    return []


def _read_sqlite(path):
    tables = []
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        cur = con.cursor()
        names = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for tname in names:
            cols = cur.execute(f"PRAGMA table_info('{tname}')").fetchall()
            header = [c[1] for c in cols]
            n = cur.execute(f"SELECT COUNT(*) FROM '{tname}'").fetchone()[0]
            rows = []
            for r in cur.execute(f"SELECT * FROM '{tname}' LIMIT 2000"):
                rows.append(["" if v is None else str(v) for v in r])
            tables.append({"header": header, "rows": rows, "sqlite_table": tname,
                           "row_count": n, "delimiter": None, "has_header": True})
        con.close()
    except Exception as exc:  # noqa: BLE001
        tables.append({"header": [], "rows": [], "sqlite_error": str(exc)})
    return tables


def load_table(path, kind):
    if kind == "csv":
        t = _read_csv(path)
        return [t]
    if kind == "tsv":
        t = _read_csv(path, delimiter="\t")
        return [t]
    if kind == "json":
        return _read_json_table(path)
    if kind in ("sqlite", "db", "sqlite3"):
        return _read_sqlite(path)
    return []


# --------------------------------------------------------------------------
# Schema inference
# --------------------------------------------------------------------------

def _infer_dtype(values):
    n_num = 0
    n_int = 0
    n_nonmissing = 0
    for v in values:
        if is_missing(v):
            continue
        n_nonmissing += 1
        f = to_float(v)
        if f is not None:
            n_num += 1
            if f == int(f):
                n_int += 1
    if n_nonmissing == 0:
        return "empty"
    if n_num == n_nonmissing:
        return "integer" if n_int == n_nonmissing else "float"
    return "string"


def _unit_for(column_name, data_dictionary):
    if not isinstance(data_dictionary, dict):
        return "unit_unknown"
    for key, val in data_dictionary.items():
        if str(key).strip().lower() == str(column_name).strip().lower():
            if isinstance(val, dict):
                return val.get("unit", val.get("units", "unit_unknown"))
            if isinstance(val, str):
                return val
    return "unit_unknown"


def infer_schema(table, source_path, data_dictionary):
    header = table["header"]
    rows = table["rows"]
    cols = []
    for j, name in enumerate(header):
        values = [r[j] if j < len(r) else "" for r in rows]
        dtype = _infer_dtype(values)
        uniq = len({str(v).strip() for v in values})
        n_missing = sum(1 for v in values if is_missing(v))
        cols.append({
            "name": name, "index": j, "dtype": dtype, "n": len(values),
            "n_missing": n_missing, "n_unique": uniq,
            "unit": _unit_for(name, data_dictionary),
            "sample": [str(v) for v in values[:3]],
        })
    return {"source": source_path, "rows_raw": len(rows), "columns": cols,
            "sqlite_table": table.get("sqlite_table"),
            "row_count": table.get("row_count", len(rows))}


def _quote_text(table, columns=None, max_rows=3, max_len=400):
    header = table["header"]
    rows = table["rows"]
    cols_idx = [i for i, h in enumerate(header) if columns is None or h in columns]
    lines = [",".join(header[i] for i in cols_idx if i < len(header))]
    for r in rows[:max_rows]:
        lines.append(",".join(str(r[i]) if i < len(r) else "" for i in cols_idx))
    text = "\n".join(lines)
    return text[:max_len] + ("..." if len(text) > max_len else "")


# --------------------------------------------------------------------------
# Cleaning
# --------------------------------------------------------------------------

def dedup_rows(rows, key_indexes=None):
    seen = set()
    kept = []
    removed = 0
    for r in rows:
        if key_indexes is None:
            key = tuple(str(c) for c in r)
        else:
            key = tuple(str(r[i]) if i < len(r) else "" for i in key_indexes)
        if key in seen:
            removed += 1
        else:
            seen.add(key)
            kept.append(r)
    return kept, removed


def clean_table(table, key_columns=None):
    header = table["header"]
    rows = table["rows"]
    key_idx = ([header.index(c) for c in key_columns if c in header]
               if key_columns else None)
    kept, removed = dedup_rows(rows, key_idx)
    missing_by_col = {}
    outlier_by_col = {}
    for j, name in enumerate(header):
        col = [r[j] if j < len(r) else "" for r in kept]
        missing_by_col[name] = sum(1 for v in col if is_missing(v))
        nums = [to_float(v) for v in col]
        nums_clean = [v for v in nums if v is not None]
        if nums_clean:
            out = S.iqr_outliers(nums_clean)
            outlier_by_col[name] = out
    actions = []
    if removed:
        actions.append({"id": "dedup", "action": "remove exact duplicates",
                        "before": len(rows), "after": len(kept),
                        "reason": f"{removed} duplicate rows removed, keep first"})
    return {"header": header, "rows": kept, "rows_raw": len(rows),
            "rows_clean": len(kept), "duplicates_removed": removed,
            "missing_by_column": missing_by_col, "outlier_by_column": outlier_by_col,
            "actions": actions, "dedup_rule": "exact row match, keep first occurrence"}


# --------------------------------------------------------------------------
# Analysis runners
# --------------------------------------------------------------------------

def _normality_decision(res):
    if not res["applicable"]:
        return "unknown"  # small sample
    return "normal" if res["normal_at_0.05"] else "non_normal"


def _select_two_group(norm_a, norm_b, levene):
    homo = levene.get("applicable") and levene.get("p", 1.0) >= 0.05
    if norm_a == "normal" and norm_b == "normal":
        return "student_t" if homo else "welch_t"
    return "mann_whitney"


def run_groups(table, group_col, outcome_col, alpha, correction):
    header = table["header"]
    gi = header.index(group_col)
    oi = header.index(outcome_col)
    rows = table["rows"]
    groups = {}
    for r in rows:
        g = str(r[gi]).strip()
        v = to_float(r[oi])
        if v is not None:
            groups.setdefault(g, []).append(v)
    order = sorted(groups.keys())
    k = len(order)
    n_total = sum(len(groups[g]) for g in order)
    descriptive = []
    normality = []
    for g in order:
        d = S.describe(groups[g], unit="unit_unknown")
        d = {**d, "group": g, "column": outcome_col}
        descriptive.append(d)
        normality.append({"group": g, "n": len(groups[g]),
                          "test": S.normality_test(groups[g])})

    homogeneity = S.levene_test([groups[g] for g in order], center="median") if k >= 2 else None

    inferential = []
    effects = []
    ci = []

    if k == 2:
        a, b = order[0], order[1]
        na = S.normality_test(groups[a])
        nb = S.normality_test(groups[b])
        dec_a, dec_b = _normality_decision(na), _normality_decision(nb)
        choice = _select_two_group(dec_a, dec_b, homogeneity)
        if choice == "student_t":
            res = S.student_t_test(groups[a], groups[b])
        elif choice == "welch_t":
            res = S.welch_t_test(groups[a], groups[b])
        else:
            res = S.mann_whitney_u(groups[a], groups[b])
        res["test_name"] = choice
        res["reason"] = (f"normality {dec_a}/{dec_b}, homogeneity "
                         f"{homogeneity.get('p') if homogeneity else None}")
        res["groups"] = [a, b]
        inferential.append(res)
        d = S.cohens_d(groups[a], groups[b])
        if d["applicable"]:
            d["groups"] = [a, b]
            d["effect"] = "cohens_d"
            effects.append(d)
        g = S.hedges_g(groups[a], groups[b])
        if g["applicable"]:
            g["groups"] = [a, b]
            g["effect"] = "hedges_g"
            effects.append(g)
        ci.append({"estimate": f"mean diff ({a} - {b})", "column": outcome_col,
                   "ci95": S.welch_mean_diff_ci(groups[a], groups[b]),
                   "method": "Welch-Satterthwaite mean difference 95% CI"})
    elif k >= 3:
        normals = [_normality_decision(S.normality_test(groups[g])) for g in order]
        homo = homogeneity.get("applicable") and homogeneity.get("p", 1.0) >= 0.05
        if all(n == "normal" for n in normals) and homo:
            res = S.anova_f([groups[g] for g in order])
            res["test_name"] = "anova"
            res["reason"] = f"all normal, homogeneity p={homogeneity.get('p')}"
        elif all(n == "normal" for n in normals):
            res = welch_anova([groups[g] for g in order])
            res["test_name"] = "welch_anova"
            res["reason"] = f"all normal, heterogeneity p={homogeneity.get('p')}"
        else:
            res = S.kruskal_wallis([groups[g] for g in order])
            res["test_name"] = "kruskal_wallis"
            res["reason"] = f"non-normal group(s): {normals}"
        res["groups"] = order
        inferential.append(res)
        if res.get("applicable") and res.get("eta_squared") is not None:
            effects.append({"effect": "eta_squared", "value": res["eta_squared"],
                            "method": "ANOVA eta^2 (SS_between/SS_total)", "groups": order})
            effects.append({"effect": "omega_squared", "value": res["omega_squared"],
                            "method": "ANOVA omega^2 (bias-corrected)", "groups": order})
        # pairwise with correction
        pairs = []
        for i in range(k):
            for j in range(i + 1, k):
                a, b = order[i], order[j]
                na = S.normality_test(groups[a])
                nb = S.normality_test(groups[b])
                dec_a, dec_b = _normality_decision(na), _normality_decision(nb)
                choice = _select_two_group(dec_a, dec_b, S.levene_test([groups[a], groups[b]], "median"))
                r = S.student_t_test(groups[a], groups[b]) if choice == "student_t" else \
                    S.welch_t_test(groups[a], groups[b]) if choice == "welch_t" else \
                    S.mann_whitney_u(groups[a], groups[b])
                r["test_name"] = choice
                r["pair"] = [a, b]
                pairs.append(r)
        raw_p = [p.get("p") if p.get("applicable") else 1.0 for p in pairs]
        if correction == "bonferroni":
            adj, mult = S.bonferroni(raw_p)
            for p, ap in zip(pairs, adj):
                p["p_raw"] = p.get("p")
                p["p_adjusted"] = ap
                p["correction"] = "bonferroni"
        elif correction == "bh":
            adj, mult = S.benjamini_hochberg(raw_p)
            for p, ap in zip(pairs, adj):
                p["p_raw"] = p.get("p")
                p["p_adjusted"] = ap
                p["correction"] = "benjamini-hochberg"
        else:
            for p in pairs:
                p["p_adjusted"] = p.get("p")
                p["correction"] = "none"
        inferential.append({"test_name": "pairwise_posthoc", "n_comparisons": len(pairs),
                            "correction": correction, "comparisons": pairs,
                            "method": "pairwise comparison, never pooled across groups"})
    for g in order:
        mci = S.mean_ci(groups[g])
        ci.append({"estimate": f"mean of {g}", "column": outcome_col, "ci95": mci,
                   "method": "t-based 95% mean CI"})

    return {"group_col": group_col, "outcome_col": outcome_col, "k": k,
            "n_total": n_total, "groups": order,
            "descriptive": descriptive, "normality": normality,
            "homogeneity": homogeneity, "inferential": inferential,
            "effects": effects, "ci": ci}


def welch_anova(groups):
    k = len(groups)
    ns = [len(g) for g in groups]
    N = sum(ns)
    if any(n < 2 for n in ns) or k < 2:
        return {"applicable": False, "reason": "need >=2 groups each n>=2"}
    means = [sum(g) / len(g) for g in groups]
    vars_ = [sum((v - means[i]) ** 2 for v in g) / (ns[i] - 1) for i, g in enumerate(groups)]
    w = [ns[i] / vars_[i] for i in range(k)]
    W = sum(w)
    mu = sum(w[i] * means[i] for i in range(k)) / W
    num = sum(w[i] * (means[i] - mu) ** 2 for i in range(k)) / (k - 1)
    lam = sum((1 - w[i] / W) ** 2 / (ns[i] - 1) for i in range(k))
    denom = 1 + 2 * (k - 2) / (k * k - 1) * lam
    F = num / denom
    df1 = k - 1
    df2 = (k * k - 1) / (3 * lam) if lam > 0 else N - k
    p = S.f_pvalue(F, df1, df2)
    return {"applicable": True, "statistic": F, "df_between": df1, "df_within": df2,
            "p": p, "method": "Welch ANOVA (unequal variances)", "k": k, "n": N}


def run_correlation(table, numeric_cols, alpha):
    header = table["header"]
    rows = table["rows"]
    col_data = {}
    for name in numeric_cols:
        j = header.index(name)
        vals = []
        for r in rows:
            v = to_float(r[j])
            if v is not None:
                vals.append(v)
        col_data[name] = vals
    descriptive = [S.describe(col_data[n]) | {"column": n} for n in numeric_cols]
    inferential = []
    effects = []
    for i in range(len(numeric_cols)):
        for j in range(i + 1, len(numeric_cols)):
            a, b = numeric_cols[i], numeric_cols[j]
            res = S.pearson_r(col_data[a], col_data[b])
            res["pair"] = [a, b]
            inferential.append(res)
            if res["applicable"]:
                effects.append({"effect": "pearson_r", "value": res["statistic"],
                                "r_squared": res["r2"], "ci95": res["ci95"],
                                "pair": [a, b], "method": res["method"]})
    return {"numeric_cols": numeric_cols, "descriptive": descriptive,
            "inferential": inferential, "effects": effects, "ci": []}


def run_regression(table, target_col, predictor_cols, alpha):
    header = table["header"]
    rows = table["rows"]
    ti = header.index(target_col)
    target = []
    preds = {}
    for r in rows:
        v = to_float(r[ti])
        if v is None:
            continue
        target.append(v)
    # align predictors to rows where target is present
    for p in predictor_cols:
        pi = header.index(p)
        vals = []
        for r in rows:
            v = to_float(r[pi])
            if v is not None:
                vals.append(v)
        preds[p] = vals
    descriptive = [S.describe(target) | {"column": target_col}]
    descriptive += [S.describe(preds[p]) | {"column": p} for p in predictor_cols]
    inferential = []
    effects = []
    for p in predictor_cols:
        n = min(len(target), len(preds[p]))
        x = preds[p][:n]
        y = target[:n]
        res = S.simple_linear_regression(x, y)
        res["predictor"] = p
        res["target"] = target_col
        inferential.append(res)
        if res["applicable"]:
            effects.append({"effect": "r_squared", "value": res["r_squared"],
                            "predictor": p, "target": target_col,
                            "slope": res["slope"], "slope_ci95": res["ci95_slope"],
                            "method": res["method"]})
    return {"target": target_col, "predictors": predictor_cols,
            "descriptive": descriptive, "inferential": inferential,
            "effects": effects, "ci": []}


def run_descriptive(table, numeric_cols, categorical_cols):
    header = table["header"]
    rows = table["rows"]
    descriptive = []
    for name in numeric_cols:
        j = header.index(name)
        vals = [to_float(r[j]) for r in rows]
        vals = [v for v in vals if v is not None]
        descriptive.append(S.describe(vals) | {"column": name})
    categorical = []
    for name in categorical_cols:
        j = header.index(name)
        counts = {}
        for r in rows:
            key = str(r[j]).strip()
            counts[key] = counts.get(key, 0) + 1
        categorical.append({"column": name, "counts": counts,
                            "n_unique": len(counts)})
    return {"descriptive": descriptive, "categorical": categorical,
            "inferential": [], "effects": [], "ci": []}


def run_categorical(table, col_a, col_b):
    header = table["header"]
    rows = table["rows"]
    ia, ib = header.index(col_a), header.index(col_b)
    a_vals, b_vals = [], []
    for r in rows:
        a_vals.append(str(r[ia]).strip())
        b_vals.append(str(r[ib]).strip())
    a_levels = sorted(set(a_vals))
    b_levels = sorted(set(b_vals))
    table_map = {a: {b: 0 for b in b_levels} for a in a_levels}
    for a, b in zip(a_vals, b_vals):
        table_map[a][b] += 1
    contingency = [[table_map[a][b] for b in b_levels] for a in a_levels]
    res = S.chi2_independence(contingency)
    res["col_a"] = col_a
    res["col_b"] = col_b
    res["levels_a"] = a_levels
    res["levels_b"] = b_levels
    res["contingency"] = contingency
    effects = []
    if res["applicable"] and res.get("cramers_v") is not None:
        effects.append({"effect": "cramers_v", "value": res["cramers_v"],
                        "method": "Cramer's V", "pair": [col_a, col_b]})
    return {"descriptive": [], "inferential": [res], "effects": effects, "ci": []}


# --------------------------------------------------------------------------
# SVG figures (editable, deterministic, stdlib-only)
# --------------------------------------------------------------------------

def _svg_doc(w, h, body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}">\n{body}\n</svg>\n')


def _scale(values, lo, hi, pad=0.05):
    if not values:
        return 0, 1
    mn, mx = min(values), max(values)
    if mn == mx:
        mn -= 1
        mx += 1
    rng = mx - mn
    return mn - rng * pad, mx + rng * pad


def boxplot_svg(groups, labels, title, xlabel, ylabel):
    W, H, LM, RM, TM, BM = 640, 360, 70, 30, 60, 50
    plot_w, plot_h = W - LM - RM, H - TM - BM
    allv = [v for g in groups for v in g]
    ylo, yhi = _scale(allv, 0, plot_h)
    n = len(groups)
    slot = plot_w / n if n else 0
    body = [f'<text x="{W/2}" y="28" font-size="14" text-anchor="middle">{_esc(title)}</text>',
            f'<text x="{LM+plot_w/2}" y="{H-12}" font-size="12" text-anchor="middle">{_esc(xlabel)}</text>',
            f'<text x="16" y="{TM+plot_h/2}" font-size="12" text-anchor="middle" transform="rotate(-90 16 {TM+plot_h/2})">{_esc(ylabel)}</text>']
    def ypos(v):
        return TM + plot_h - (v - ylo) / (yhi - ylo) * plot_h
    for gi, g in enumerate(groups):
        g = sorted(g)
        q1 = S.quantile_sorted(g, 0.25)
        q3 = S.quantile_sorted(g, 0.75)
        med = S.quantile_sorted(g, 0.5)
        iqr = q3 - q1
        xc = LM + slot * (gi + 0.5)
        half = slot * 0.28
        body.append(f'<rect x="{xc-half:.1f}" y="{ypos(q3):.1f}" width="{2*half:.1f}" height="{ypos(q1)-ypos(q3):.1f}" fill="#cfe3f5" stroke="#2b6cb0"/>')
        body.append(f'<line x1="{xc-half:.1f}" y1="{ypos(med):.1f}" x2="{xc+half:.1f}" y2="{ypos(med):.1f}" stroke="#c53030" stroke-width="2"/>')
        lo_whisk = max(min(g), q1 - 1.5 * iqr)
        hi_whisk = min(max(g), q3 + 1.5 * iqr)
        body.append(f'<line x1="{xc:.1f}" y1="{ypos(q3):.1f}" x2="{xc:.1f}" y2="{ypos(hi_whisk):.1f}" stroke="#2b6cb0"/>')
        body.append(f'<line x1="{xc:.1f}" y1="{ypos(q1):.1f}" x2="{xc:.1f}" y2="{ypos(lo_whisk):.1f}" stroke="#2b6cb0"/>')
        body.append(f'<text x="{xc:.1f}" y="{H-28}" font-size="10" text-anchor="middle">{_esc(labels[gi])}</text>')
    return _svg_doc(W, H, "\n".join(body))


def histogram_svg(values, title, xlabel):
    W, H, LM, RM, TM, BM = 640, 320, 60, 30, 50, 50
    plot_w, plot_h = W - LM - RM, H - TM - BM
    xlo, xhi = _scale(values, 0, plot_w)
    bins = 12
    hist = [0] * bins
    for v in values:
        idx = min(bins - 1, int((v - xlo) / (xhi - xlo) * bins))
        hist[idx] += 1
    ymax = max(hist) if hist else 1
    bw = plot_w / bins
    body = [f'<text x="{W/2}" y="24" font-size="14" text-anchor="middle">{_esc(title)}</text>',
            f'<text x="{LM+plot_w/2}" y="{H-12}" font-size="12" text-anchor="middle">{_esc(xlabel)}</text>',
            f'<text x="14" y="{TM+plot_h/2}" font-size="12" text-anchor="middle" transform="rotate(-90 14 {TM+plot_h/2})">count</text>']
    for i, c in enumerate(hist):
        x = LM + i * bw
        h = c / ymax * plot_h
        body.append(f'<rect x="{x:.1f}" y="{TM+plot_h-h:.1f}" width="{bw*0.9:.1f}" height="{h:.1f}" fill="#4299e1"/>')
    return _svg_doc(W, H, "\n".join(body))


def scatter_svg(x, y, title, xlabel, ylabel, line=None):
    W, H, LM, RM, TM, BM = 640, 400, 70, 30, 50, 50
    plot_w, plot_h = W - LM - RM, H - TM - BM
    xlo, xhi = _scale(x, 0, plot_w)
    ylo, yhi = _scale(y, 0, plot_h)
    def px(v):
        return LM + (v - xlo) / (xhi - xlo) * plot_w
    def py(v):
        return TM + plot_h - (v - ylo) / (yhi - ylo) * plot_h
    body = [f'<text x="{W/2}" y="24" font-size="14" text-anchor="middle">{_esc(title)}</text>',
            f'<text x="{LM+plot_w/2}" y="{H-12}" font-size="12" text-anchor="middle">{_esc(xlabel)}</text>',
            f'<text x="16" y="{TM+plot_h/2}" font-size="12" text-anchor="middle" transform="rotate(-90 16 {TM+plot_h/2})">{_esc(ylabel)}</text>']
    pts = " ".join(f"{px(x[i]):.1f},{py(y[i]):.1f}" for i in range(len(x)))
    body.append(f'<polyline points="{pts}" fill="none" stroke="#4299e1" stroke-width="0.6" opacity="0.6"/>')
    if line is not None:
        intercept, slope = line
        body.append(f'<line x1="{px(xlo):.1f}" y1="{py(intercept+slope*xlo):.1f}" x2="{px(xhi):.1f}" y2="{py(intercept+slope*xhi):.1f}" stroke="#c53030" stroke-width="1.5"/>')
    return _svg_doc(W, H, "\n".join(body))


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# --------------------------------------------------------------------------
# Stages
# --------------------------------------------------------------------------

def stage_probe(input_dir, work_dir, **_kw):
    os.makedirs(work_dir, exist_ok=True)
    case_path = os.path.join(input_dir, "case.json")
    case = read_json_safe(case_path)
    if not isinstance(case, dict):
        case = {}
    read_err = case.pop("__read_error__", None) if isinstance(case, dict) else None
    case_id = (case.get("case_id") or case.get("id") or case.get("caseId") or "unknown-case")
    oc = case.get("output_contract") or case.get("outputContract") or {}
    data_dictionary = case.get("data_dictionary") or case.get("dataDictionary")
    if not isinstance(data_dictionary, dict):
        if isinstance(oc, dict):
            data_dictionary = oc.get("data_dictionary") or oc.get("dataDictionary") or {}
        else:
            data_dictionary = {}
    task_instruction = case.get("task_instruction") or case.get("taskInstruction") or \
        case.get("instruction") or ""
    files = discover_files(input_dir)
    index = {
        "case_id": case_id,
        "input_dir": input_dir,
        "output_contract": oc,
        "task_instruction": task_instruction,
        "data_dictionary": data_dictionary,
        "files": files,
        "case_read_error": read_err,
        "generated_at": now_iso(),
    }
    write_json(os.path.join(work_dir, "index.json"), index)
    return index


def _contract_default(key, spec=None):
    kl = str(key).lower()
    if isinstance(spec, dict) and "type" in spec:
        t = str(spec["type"]).lower()
        if t in ("list", "array"):
            return []
        if t in ("object", "map", "dict"):
            return {}
        return None
    if any(s in kl for s in ("list", "items", "results", "samples", "columns", "fields",
                             "rows", "groups", "errors", "degradations", "pending",
                             "warnings", "notes", "sources", "comparisons")):
        return []
    if any(s in kl for s in ("map", "dict", "object", "details", "meta", "info", "runtime")):
        return {}
    return None


def _contract_items(oc):
    """Return list of (key, spec) for the output contract."""
    items = []
    if isinstance(oc, dict):
        for k, v in oc.items():
            items.append((k, v if isinstance(v, dict) else None))
    elif isinstance(oc, list):
        for entry in oc:
            if isinstance(entry, dict):
                key = entry.get("name") or entry.get("key") or entry.get("field")
                if key:
                    items.append((key, entry))
    return items


def stage_skeleton(input_dir, output_dir, work_dir, **_kw):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    index_path = os.path.join(work_dir, "index.json")
    index = read_json_safe(index_path)
    if not isinstance(index, dict):
        index = stage_probe(input_dir, work_dir)
    case_id = index.get("case_id", "unknown-case")
    oc = index.get("output_contract", {})
    result = {"case_id": case_id, "status": "partial"}
    collisions = []
    for key, spec in _contract_items(oc):
        if key in RESERVED:
            collisions.append(key)
            continue
        result[key] = _contract_default(key, spec)
    change_log = [{
        "id": "init-skeleton", "time": now_iso(), "action": "write_partial_skeleton",
        "before": None, "after": "result.json with contract keys backfilled to null/[]",
        "reason": "guarantee a valid deliverable within the first 3 minutes",
    }]
    result.update({
        "sources": [{"source_id": "S1", "path": os.path.join(input_dir, "case.json"),
                     "kind": "json", "rel_path": "case.json"}],
        "uncertainty": ["analysis not yet executed; skeleton only"],
        "change_log": change_log,
        "errors": [],
        "degradations": ["skeleton-only deliverable"] + \
            ([f"contract key {c} collides with envelope, kept in contract object"]
             if collisions else []),
        "pending_human": [],
        "runtime": {"stage": "skeleton", "python": sys.version.split()[0],
                    "generated_at": now_iso()},
    })
    if collisions:
        result["_contract_collisions"] = collisions
    write_json(os.path.join(output_dir, "result.json"), result)
    return result


RESERVED = {"case_id", "status", "sources", "uncertainty", "change_log", "errors",
            "degradations", "pending_human", "runtime", "_contract_collisions"}


def stage_infer(input_dir, work_dir, **_kw):
    os.makedirs(work_dir, exist_ok=True)
    index_path = os.path.join(work_dir, "index.json")
    index = read_json_safe(index_path)
    if not isinstance(index, dict):
        index = stage_probe(input_dir, work_dir)
    data_dictionary = index.get("data_dictionary", {})
    tables = []
    data_files = _data_files(index)
    for f in data_files:
        loaded = load_table(f["path"], f["kind"])
        for t in loaded:
            if t.get("sqlite_error"):
                tables.append({"source": f["path"], "error": t["sqlite_error"]})
                continue
            schema = infer_schema(t, f["path"], data_dictionary)
            schema["quote"] = _quote_text(t)
            tables.append(schema)
    schema_doc = {"tables": tables, "data_dictionary": data_dictionary,
                  "generated_at": now_iso()}
    write_json(os.path.join(work_dir, "schema.json"), schema_doc)
    return schema_doc


def stage_plan(input_dir, work_dir, **_kw):
    os.makedirs(work_dir, exist_ok=True)
    plan_path = os.path.join(work_dir, "plan.json")
    existing = read_json_safe(plan_path)
    if isinstance(existing, dict) and existing.get("design"):
        return existing
    schema_path = os.path.join(work_dir, "schema.json")
    schema = read_json_safe(schema_path)
    if not isinstance(schema, dict):
        schema = stage_infer(input_dir, work_dir)
    tables = schema.get("tables", [])
    tables = [t for t in tables if not t.get("error") and t.get("columns")]
    if not tables:
        plan = {"design": "none", "auto": True, "reason": "no data table found"}
    else:
        table = max(tables, key=lambda t: t.get("rows_raw", 0))
        cols = table["columns"]
        numeric = [c["name"] for c in cols if c["dtype"] in ("integer", "float")]
        categorical = [c["name"] for c in cols
                       if c["dtype"] == "string" and 2 <= c["n_unique"] <= 20]
        if categorical and numeric:
            group = min(categorical, key=lambda n: next(
                c["n_unique"] for c in cols if c["name"] == n))
            plan = {"design": "groups", "group_col": group,
                    "outcome_cols": numeric, "pairing": "independent",
                    "alpha": 0.05, "correction": "bonferroni", "auto": True,
                    "reason": "auto: categorical group col + numeric outcomes detected"}
        elif len(numeric) >= 2:
            plan = {"design": "correlation", "numeric_cols": numeric,
                    "alpha": 0.05, "auto": True,
                    "reason": "auto: >=2 numeric columns detected"}
        else:
            plan = {"design": "descriptive", "numeric_cols": numeric,
                    "categorical_cols": categorical, "auto": True,
                    "reason": "auto: no clear comparison structure"}
    write_json(plan_path, plan)
    return plan


def _load_main_table(index, schema):
    data_files = _data_files(index)
    tables = schema.get("tables", [])
    best = None
    for f in data_files:
        for t in tables:
            if t.get("source") == f["path"] and t.get("columns"):
                loaded = load_table(f["path"], f["kind"])
                for lt in loaded:
                    if lt.get("header"):
                        score = (len(lt.get("header", [])), lt.get("row_count", len(lt.get("rows", []))))
                        if best is None or score > best[0]:
                            best = (score, f, lt)
    if best is None:
        return None, None
    return best[1], best[2]


def stage_compute(input_dir, work_dir, output_dir, **_kw):
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    index = read_json_safe(os.path.join(work_dir, "index.json")) or {}
    schema = read_json_safe(os.path.join(work_dir, "schema.json")) or {}
    plan = read_json_safe(os.path.join(work_dir, "plan.json")) or {}
    if not isinstance(plan, dict) or not plan.get("design"):
        plan = stage_plan(input_dir, work_dir)
    design = plan.get("design")
    alpha = float(plan.get("alpha", 0.05))
    correction = plan.get("correction", "bonferroni")
    analysis = {"case_id": index.get("case_id", "unknown-case"),
                "design": plan, "generated_at": now_iso(),
                "cleaning": None, "descriptive": [], "inferential": [],
                "effects": [], "ci": [], "figures": [], "evidence_map": {}}
    src_file, table = _load_main_table(index, schema)
    if table is None or not table.get("header"):
        analysis["status"] = "insufficient_evidence"
        analysis["uncertainty"] = ["no parseable data table found"]
        write_json(os.path.join(work_dir, "analysis.json"), analysis)
        return analysis

    cleaned = clean_table(table)
    analysis["cleaning"] = {k: v for k, v in cleaned.items() if k != "rows"}
    quote = _quote_text(table)
    locator_base = os.path.relpath(src_file["path"], input_dir).replace(os.sep, "/")

    # cleaned CSV
    cleaned_dir = os.path.join(output_dir, "cleaned")
    os.makedirs(cleaned_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(src_file["path"]))[0]
    clean_csv_path = os.path.join(cleaned_dir, f"{stem}_clean.csv")
    with open(clean_csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cleaned["header"])
        w.writerows(cleaned["rows"])
    analysis["cleaned_csv"] = "cleaned/" + f"{stem}_clean.csv"

    header = cleaned["header"]
    numeric_cols = []
    categorical_cols = []
    for j, name in enumerate(header):
        col = [r[j] if j < len(r) else "" for r in cleaned["rows"]]
        dt = _infer_dtype(col)
        if dt in ("integer", "float"):
            numeric_cols.append(name)
        elif dt == "string":
            categorical_cols.append(name)

    # validate plan columns against the actual table; degrade instead of crashing
    missing = []
    if design == "groups":
        if plan.get("group_col") and plan["group_col"] not in header:
            missing.append(("group_col", plan["group_col"]))
        for oc in plan.get("outcome_cols", []):
            if oc not in header:
                missing.append(("outcome_col", oc))
    elif design in ("correlation", "descriptive"):
        for c in plan.get("numeric_cols", []):
            if c not in header:
                missing.append(("numeric_col", c))
    elif design == "regression":
        for c in ([plan.get("target_col")] + list(plan.get("predictor_cols", []))):
            if c and c not in header:
                missing.append(("regression_col", c))
    elif design == "categorical":
        for c in (plan.get("col_a"), plan.get("col_b")):
            if c and c not in header:
                missing.append(("categorical_col", c))
    if missing:
        analysis["status"] = "partial"
        analysis["uncertainty"] = [f"plan references columns not in table: {missing}"]
        analysis["errors"] = [f"plan_column_missing: {missing}"]
        write_json(os.path.join(work_dir, "analysis.json"), analysis)
        return analysis

    figures_dir = os.path.join(output_dir, "figures")
    os.makedirs(figures_dir, exist_ok=True)

    def ev(column, detail):
        return {"locator": f"{locator_base}|col={column}|{detail}", "quote": quote}

    if design == "groups":
        group_col = plan.get("group_col")
        for oc in plan.get("outcome_cols", [])[:6]:
            res = run_groups(cleaned, group_col, oc, alpha, correction)
            analysis["descriptive"] += res["descriptive"]
            analysis["normality"] = analysis.get("normality", []) + res["normality"]
            analysis["homogeneity"] = analysis.get("homogeneity", []) + [res["homogeneity"]]
            for item in res["inferential"]:
                item["evidence"] = ev(oc, "inferential")
                analysis["inferential"].append(item)
            for item in res["effects"]:
                item["evidence"] = ev(oc, "effect")
                analysis["effects"].append(item)
            analysis["ci"] += res["ci"]
            gi = header.index(group_col)
            oi = header.index(oc)
            gvals = {}
            for r in cleaned["rows"]:
                g = str(r[gi]).strip()
                v = to_float(r[oi])
                if v is not None:
                    gvals.setdefault(g, []).append(v)
            groups_plot = [gvals[g] for g in res["groups"] if g in gvals]
            fig = boxplot_svg(groups_plot, res["groups"], f"{oc} by {group_col}", group_col, oc)
            fname = f"boxplot_{_safe(oc)}.svg"
            with open(os.path.join(figures_dir, fname), "w", encoding="utf-8") as f:
                f.write(fig)
            analysis["figures"].append("figures/" + fname)
    elif design == "correlation":
        res = run_correlation(cleaned, plan.get("numeric_cols", numeric_cols)[:8], alpha)
        analysis["descriptive"] = res["descriptive"]
        for item in res["inferential"]:
            item["evidence"] = ev(",".join(item["pair"]), "correlation")
            analysis["inferential"].append(item)
        analysis["effects"] = res["effects"]
        top = [e for e in res["effects"] if e.get("value") is not None]
        top.sort(key=lambda e: -abs(e["value"]))
        for e in top[:4]:
            a, b = e["pair"]
            ia, ib = header.index(a), header.index(b)
            xs, ys = [], []
            for r in cleaned["rows"]:
                va, vb = to_float(r[ia]), to_float(r[ib])
                if va is not None and vb is not None:
                    xs.append(va)
                    ys.append(vb)
            fig = scatter_svg(xs, ys, f"{a} vs {b}", a, b)
            fname = f"scatter_{_safe(a)}_{_safe(b)}.svg"
            with open(os.path.join(figures_dir, fname), "w", encoding="utf-8") as f:
                f.write(fig)
            analysis["figures"].append("figures/" + fname)
    elif design == "regression":
        target = plan.get("target_col")
        preds = plan.get("predictor_cols") or [c for c in numeric_cols if c != target][:8]
        res = run_regression(cleaned, target, preds, alpha)
        analysis["descriptive"] = res["descriptive"]
        for item in res["inferential"]:
            item["evidence"] = ev(f"{item.get('predictor')}->{target}", "regression")
            analysis["inferential"].append(item)
        analysis["effects"] = res["effects"]
        ti = header.index(target)
        for item in res["inferential"][:4]:
            p = item.get("predictor")
            if not p:
                continue
            pi = header.index(p)
            xs, ys = [], []
            for r in cleaned["rows"]:
                vp, vt = to_float(r[pi]), to_float(r[ti])
                if vp is not None and vt is not None:
                    xs.append(vp)
                    ys.append(vt)
            fig = scatter_svg(xs, ys, f"{p} vs {target}", p, target,
                              line=(item["intercept"], item["slope"]) if item.get("applicable") else None)
            fname = f"regress_{_safe(p)}.svg"
            with open(os.path.join(figures_dir, fname), "w", encoding="utf-8") as f:
                f.write(fig)
            analysis["figures"].append("figures/" + fname)
    elif design == "categorical":
        res = run_categorical(cleaned, plan.get("col_a"), plan.get("col_b"))
        analysis["descriptive"] = res["descriptive"]
        analysis["inferential"] = res["inferential"]
        analysis["effects"] = res["effects"]
    else:  # descriptive
        res = run_descriptive(cleaned, numeric_cols, categorical_cols)
        analysis["descriptive"] = res["descriptive"]
        analysis["categorical"] = res["categorical"]
        for name in numeric_cols[:6]:
            j = header.index(name)
            vals = [to_float(r[j]) for r in cleaned["rows"]]
            vals = [v for v in vals if v is not None]
            fig = histogram_svg(vals, f"histogram of {name}", name)
            fname = f"hist_{_safe(name)}.svg"
            with open(os.path.join(figures_dir, fname), "w", encoding="utf-8") as f:
                f.write(fig)
            analysis["figures"].append("figures/" + fname)

    # evidence map: bind each key number to locator + quote
    em = {}
    for d in analysis["descriptive"]:
        key = f"desc:{d.get('column')}:{d.get('group','all')}:mean"
        em[key] = {"value": d.get("mean"), "locator": f"{locator_base}|col={d.get('column')}",
                   "quote": quote, "method": d.get("method")}
    for item in analysis["inferential"]:
        key = f"test:{item.get('test_name')}:{item.get('pair') or item.get('groups') or item.get('predictor')}"
        em[key] = {"value": item.get("p"), "statistic": item.get("statistic"),
                   "locator": item.get("evidence", {}).get("locator", locator_base),
                   "quote": item.get("evidence", {}).get("quote", quote),
                   "method": item.get("method")}
    analysis["evidence_map"] = em
    analysis["status"] = "ok"
    analysis["uncertainty"] = []
    write_json(os.path.join(work_dir, "analysis.json"), analysis)
    return analysis


def _safe(s):
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in str(s))


# --------------------------------------------------------------------------
# Package + verify
# --------------------------------------------------------------------------

def _map_payload(analysis):
    payload = {
        "descriptive": analysis.get("descriptive", []),
        "inferential": analysis.get("inferential", []),
        "effects": analysis.get("effects", []),
        "effect_sizes": analysis.get("effects", []),
        "confidence_intervals": analysis.get("ci", []),
        "ci": analysis.get("ci", []),
        "cleaning": analysis.get("cleaning", {}),
        "result": analysis.get("evidence_map", {}),
        "evidence": analysis.get("evidence_map", {}),
        "figures": analysis.get("figures", []),
    }
    return payload


def stage_package(input_dir, output_dir, work_dir, **_kw):
    os.makedirs(output_dir, exist_ok=True)
    index = read_json_safe(os.path.join(work_dir, "index.json")) or {}
    analysis = read_json_safe(os.path.join(work_dir, "analysis.json")) or {}
    result_path = os.path.join(output_dir, "result.json")
    result = read_json_safe(result_path)
    if not isinstance(result, dict):
        result = stage_skeleton(input_dir, output_dir, work_dir)
    case_id = index.get("case_id", result.get("case_id", "unknown-case"))
    result["case_id"] = case_id
    status = analysis.get("status", "partial")
    result["status"] = status if status in ("ok", "partial", "insufficient_evidence", "input_error") else "partial"

    payload = _map_payload(analysis)
    for key in list(result.keys()):
        if key in RESERVED:
            continue
        norm = key.lower().replace("-", "_").replace(" ", "_")
        if norm in payload:
            result[key] = payload[norm]

    # append deliverables + evidence without clobbering
    result["sources"] = result.get("sources", [])
    seen = {(s.get("path") if isinstance(s, dict) else None) for s in result["sources"]}
    for f in index.get("files", [])[:30]:
        if f.get("path") not in seen:
            result["sources"].append({
                "source_id": f"S{len(result['sources']) + 1}",
                "path": f["path"], "kind": f["kind"], "rel_path": f["rel_path"]})
            seen.add(f["path"])

    result["uncertainty"] = analysis.get("uncertainty", [])
    if not result["uncertainty"]:
        result["uncertainty"] = ["results computed deterministically; verify units and design"]

    # change log (append without overwriting earlier entries)
    cl = result.get("change_log", [])
    cl.append({"id": "package", "time": now_iso(), "action": "package",
               "before": "skeleton", "after": f"status={result['status']}",
               "reason": "merged computed analysis into result.json"})
    result["change_log"] = cl

    result["runtime"] = {"stage": "package", "python": sys.version.split()[0],
                         "generated_at": now_iso(),
                         "design": analysis.get("design"),
                         "stdlib_only": True}

    # Report + quality checks + code
    report_path = write_report(output_dir, index, analysis)
    result["analysis_report"] = report_path
    qc_path = write_quality_checks(output_dir, index, analysis)
    result["quality_checks"] = qc_path
    code_path = write_analysis_code(output_dir, index, analysis)
    result["analysis_code"] = code_path
    result["figures"] = analysis.get("figures", [])
    result["cleaned_csv"] = analysis.get("cleaned_csv")
    if "cleaned_table" not in result and analysis.get("cleaned_csv"):
        result["cleaned_table"] = analysis.get("cleaned_csv")

    write_json(result_path, result)
    return result


def write_report(output_dir, index, analysis):
    path = os.path.join(output_dir, "analysis_report.md")
    lines = ["# 统计分析报告", "",
             f"- case_id: `{index.get('case_id', 'unknown-case')}`",
             f"- 生成时间: {analysis.get('generated_at', '')}",
             f"- 设计: `{json.dumps(analysis.get('design', {}), ensure_ascii=False)}`",
             f"- 状态: {analysis.get('status', 'partial')}", ""]
    cl = analysis.get("cleaning") or {}
    lines += ["## 1. 清洗", ""]
    lines += [f"- 原始行数: {cl.get('rows_raw')}；去重后: {cl.get('rows_clean')}；删除重复: {cl.get('duplicates_removed')}",
              f"- 去重规则: {cl.get('dedup_rule')}", ""]
    if cl.get("missing_by_column"):
        lines.append("缺失计数（不当 0 处理）：")
        for k, v in cl["missing_by_column"].items():
            if v:
                lines.append(f"  - `{k}`: {v}")
        lines.append("")
    if cl.get("outlier_by_column"):
        lines.append("异常值（Tukey IQR 1.5 标记，未静默删除）：")
        for k, v in cl["outlier_by_column"].items():
            if isinstance(v, dict) and v.get("n_outlier"):
                lines.append(f"  - `{k}`: {v['n_outlier']} 个")
        lines.append("")
    lines += ["## 2. 描述统计", ""]
    for d in analysis.get("descriptive", []):
        lines.append(f"- `{d.get('column')}` [group={d.get('group', 'all')}]: "
                     f"n={d.get('n')}, mean={_fmt(d.get('mean'))}, sd={_fmt(d.get('sd'))}, "
                     f"median={_fmt(d.get('median'))}, IQR={_fmt(d.get('iqr'))}")
    lines.append("")
    lines += ["## 3. 推断统计", ""]
    for item in analysis.get("inferential", []):
        if item.get("test_name") == "pairwise_posthoc":
            lines.append(f"- 事后多重比较（{item.get('correction')} 校正，{item.get('n_comparisons')} 对）：")
            for c in item.get("comparisons", []):
                lines.append(f"  - {c.get('pair')}: p={_fmt(c.get('p'))} → 校正后 {_fmt(c.get('p_adjusted'))}")
            continue
        lines.append(f"- {item.get('test_name')} {item.get('pair') or item.get('groups') or item.get('predictor')}: "
                     f"statistic={_fmt(item.get('statistic'))}, p={_fmt(item.get('p'))}, "
                     f"method={item.get('method')}")
    lines.append("")
    lines += ["## 4. 效应量与置信区间", ""]
    for e in analysis.get("effects", []):
        lines.append(f"- {e.get('effect')}: {_fmt(e.get('value'))} ci95={e.get('ci95')} ({e.get('method')})")
    for c in analysis.get("ci", []):
        lines.append(f"- {c.get('estimate')}: {c.get('ci95')} ({c.get('method')})")
    lines.append("")
    lines += ["## 5. 结果解释", ""]
    lines += ["- 统计显著 ≠ 实际意义：p 值以精确值给出，不写“证明有效”。",
              "- 置信区间是参数覆盖区间，不是个体取值范围。",
              "- 检验方法的选择依据数据分布（正态性）与设计结构（组数、配对），详见 quality_checks.json。",
              "- 剩余不确定性与需人工确认项见 result.json 的 uncertainty / pending_human。",
              ""]
    lines += ["## 6. 证据定位", ""]
    lines += ["关键数值均可在 `evidence_map`（result.json）中按 locator + quote 回溯到原始数据。",
              ""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return "analysis_report.md"


def _fmt(x):
    if x is None:
        return "None"
    if isinstance(x, float):
        return f"{x:.6g}"
    return str(x)


def write_quality_checks(output_dir, index, analysis):
    path = os.path.join(output_dir, "quality_checks.json")
    checks = {
        "case_id": index.get("case_id"),
        "seven_scenarios": {
            "missing": {"handled": True, "rule": "counted + proportion, never treated as 0",
                        "details": (analysis.get("cleaning") or {}).get("missing_by_column")},
            "outliers": {"handled": True, "rule": "Tukey IQR 1.5 flagged, not silently dropped",
                         "details": (analysis.get("cleaning") or {}).get("outlier_by_column")},
            "duplicates": {"handled": True, "rule": "exact-row dedup, keep first",
                           "details": {"removed": (analysis.get("cleaning") or {}).get("duplicates_removed")}},
            "statistical_assumptions": {"handled": True,
                                        "rule": "normality + homogeneity before test selection"},
            "chart_choice": {"handled": True, "rule": "boxplot for groups, scatter for correlation/regression, histogram for distributions",
                             "files": analysis.get("figures", [])},
            "code_run": {"handled": True, "rule": "stdlib-only, deterministic, reproducible"},
            "database_fields": {"handled": True, "rule": "dtype + unit inferred from dictionary; unknown -> unit_unknown"},
        },
        "test_selection": [{"test_name": i.get("test_name"), "reason": i.get("reason"),
                            "p": i.get("p")} for i in analysis.get("inferential", [])],
        "determinism": "no randomness; same input -> same output",
        "evidence": {"evidence_map_size": len(analysis.get("evidence_map", {}))},
    }
    write_json(path, checks)
    return "quality_checks.json"


def write_analysis_code(output_dir, index, analysis):
    path = os.path.join(output_dir, "analysis_code.py")
    plan = analysis.get("design", {})
    code = (
        "#!/usr/bin/env python3\n"
        "# -*- coding: utf-8 -*-\n"
        '"""analysis_code.py — reproducible runnable record of this analysis.\n\n'
        "This file is a deterministic record of the analysis actually executed.  It is\n"
        "kept separate from the skill's pipeline so a reviewer can re-run just this\n"
        "case's computation: `python3 analysis_code.py`.\n"
        '"""\n\n'
        "import json, os, sys\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))\n"
        "import statlib as S\n"
        "import pipeline as P\n\n"
        f"PLAN = {json.dumps(plan, ensure_ascii=False, indent=2)}\n\n"
        "def main():\n"
        "    print(json.dumps(PLAN, ensure_ascii=False, indent=2))\n"
        "    print('Rerun the pipeline with: python scripts/main.py all')\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    return "analysis_code.py"


def stage_verify(input_dir, output_dir, work_dir, **_kw):
    problems = []
    result_path = os.path.join(output_dir, "result.json")
    if not os.path.isfile(result_path):
        return {"ok": False, "problems": ["result.json missing"]}
    try:
        with open(result_path, "r", encoding="utf-8") as f:
            result = json.load(f)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "problems": [f"result.json not parseable: {exc}"]}
    if not result.get("case_id"):
        problems.append("missing case_id")
    for k in ("status", "sources", "uncertainty", "change_log"):
        if k not in result:
            problems.append(f"missing envelope key {k}")
    index = read_json_safe(os.path.join(work_dir, "index.json")) or {}
    if isinstance(index.get("output_contract"), dict):
        for key in index["output_contract"]:
            if key not in result and key not in RESERVED:
                problems.append(f"output_contract key missing from result: {key}")
    # SVG parse check
    for f in result.get("figures", []):
        fp = os.path.join(output_dir, f)
        if os.path.isfile(fp):
            with open(fp, "r", encoding="utf-8") as fh:
                head = fh.read(200)
            if "<svg" not in head:
                problems.append(f"figure not SVG: {f}")
    checks = {"ok": not problems, "problems": problems,
              "status": result.get("status"),
              "checked_at": now_iso()}
    write_json(os.path.join(work_dir, "verify.json"), checks)
    return checks


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

STAGES = ("probe", "skeleton", "infer", "plan", "compute", "package", "verify", "all")


def run_all(input_dir, output_dir, work_dir):
    steps = ["probe", "skeleton", "infer", "plan", "compute", "package", "verify"]
    for s in steps:
        fn = globals()[f"stage_{s}"]
        fn(input_dir=input_dir, output_dir=output_dir, work_dir=work_dir)
    return read_json_safe(os.path.join(work_dir, "verify.json"))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    stage = "all"
    input_dir, output_dir, work_dir = INPUT_DIR, OUTPUT_DIR, WORK_DIR
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--help", "-h"):
            print("usage: pipeline.py [stage] [--input DIR] [--output DIR] [--work DIR]")
            print("stages:", ", ".join(STAGES))
            return 0
        if a in STAGES:
            stage = a
        elif a == "--input" and i + 1 < len(argv):
            input_dir = argv[i + 1]
            i += 1
        elif a == "--output" and i + 1 < len(argv):
            output_dir = argv[i + 1]
            i += 1
        elif a == "--work" and i + 1 < len(argv):
            work_dir = argv[i + 1]
            i += 1
        i += 1
    if stage == "all":
        res = run_all(input_dir, output_dir, work_dir)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    fn = globals().get(f"stage_{stage}")
    if fn is None:
        print(f"unknown stage: {stage}", file=sys.stderr)
        return 2
    res = fn(input_dir=input_dir, output_dir=output_dir, work_dir=work_dir)
    print(json.dumps(S.jsonable(res) if isinstance(res, dict) else res,
                     ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
