#!/usr/bin/env python3
"""analyze.py — deterministic statistical analysis engine (standard library only).

Subcommands
  init       discover /input, write /app/work/index.json and a partial result.json
  inspect    survey materials and infer per-column schemas  -> /app/work/schema.json
  analyze    clean + describe + test + effect size + CI      -> /app/work/analysis.json
  build      assemble /app/output/result.json + report + artifacts
  selfcheck  validate result.json, artifacts, and reproducibility

Defaults: --input /input --output /app/output --work /app/work.
All commands use relative script paths internally and never depend on cwd.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import shutil
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import stats as S  # noqa: E402

DEFAULTS = {"input": "/input", "output": "/app/output", "work": "/app/work"}
MISSING_TOKENS = {"", "na", "n/a", "nan", "null", "none", "missing", ".", "-"}


# --------------------------------------------------------------------------
# small utilities
# --------------------------------------------------------------------------
def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def round_sig(x, sig: int = 6):
    if x is None or isinstance(x, bool):
        return x
    if isinstance(x, (int, str)):
        return x
    try:
        f = float(x)
    except (TypeError, ValueError):
        return x
    if math.isnan(f) or math.isinf(f):
        return None
    if f == 0.0:
        return 0.0
    return float(f"{f:.{sig}g}")


def sanitize(obj):
    if obj is None or isinstance(obj, (bool, str, int)):
        return obj
    if isinstance(obj, float):
        return round_sig(obj)
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    return obj


def json_read(path, default=None):
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def json_write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(sanitize(obj), fh, ensure_ascii=False, allow_nan=False,
                  sort_keys=True, indent=2)
    os.replace(tmp, path)


def fmt_p(p) -> str:
    if p is None:
        return "n/a"
    if math.isnan(p):
        return "n/a"
    if p < 0.001:
        return "p<0.001"
    if p < 0.01:
        return f"p={p:.3f}"
    return f"p={p:.3f}"


def change_entry(cid, action, before, after, reason):
    return {"id": cid, "time": now_iso(), "action": action,
            "before": before, "after": after, "reason": reason}


# --------------------------------------------------------------------------
# file discovery and table reading
# --------------------------------------------------------------------------
def discover(input_dir: str) -> list:
    out = []
    if not os.path.isdir(input_dir):
        return out
    for root, _dirs, files in os.walk(input_dir):
        for fn in sorted(files):
            if fn.startswith("."):
                continue
            p = os.path.join(root, fn)
            try:
                size = os.path.getsize(p)
            except OSError:
                size = -1
            rel = os.path.relpath(p, input_dir)
            base = os.path.basename(fn).lower()
            ext = os.path.splitext(fn)[1].lower()
            if base in ("case.json", "task.md"):
                kind = "contract"
            elif ext in (".csv", ".tsv", ".txt"):
                kind = "text-table"
            elif ext == ".json":
                kind = "json"
            elif ext in (".db", ".sqlite", ".sqlite3"):
                kind = "sqlite"
            elif ext in (".xlsx", ".xls"):
                kind = "excel-unsupported"
            elif ext in (".docx", ".pdf"):
                kind = "document"
            else:
                kind = "other"
            out.append({"path": p, "rel_path": rel, "kind": kind, "size": size,
                        "name": fn})
    return out


def parse_cell(v):
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    s = str(v).strip()
    if s.lower() in MISSING_TOKENS:
        return None
    try:
        if s.isdigit() or (s[0] in "+-" and s[1:].isdigit()):
            return int(s)
    except IndexError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _dedup_header(header):
    seen = {}
    out = []
    for h in header:
        h = (h or "").strip() or "col"
        if h in seen:
            seen[h] += 1
            h = f"{h}_{seen[h]}"
        else:
            seen[h] = 0
        out.append(h)
    return out


def read_csv(path):
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        sample = fh.read(8192)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(fh, dialect)
        raw = list(reader)
    if not raw:
        return [], []
    header = _dedup_header(raw[0])
    rows = []
    for r in raw[1:]:
        if not r or all((c or "").strip() == "" for c in r):
            continue
        rows.append({h: parse_cell(r[i] if i < len(r) else None)
                     for i, h in enumerate(header)})
    return header, rows


def read_json_table(path):
    obj = json_read(path)
    if obj is None:
        return [], []
    if isinstance(obj, list):
        if all(isinstance(x, dict) for x in obj):
            cols = []
            for x in obj:
                for k in x:
                    if k not in cols:
                        cols.append(k)
            return cols, obj
        return [], []
    if isinstance(obj, dict):
        if all(isinstance(v, list) for v in obj.values()):
            cols = list(obj.keys())
            n = max((len(v) for v in obj.values()), default=0)
            rows = [{c: obj[c][i] if i < len(obj[c]) else None for c in cols}
                    for i in range(n)]
            return cols, rows
        cols = list(obj.keys())
        return cols, [obj]
    return [], []


def read_sqlite(path):
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    if not tables:
        conn.close()
        return [], [], {}, None
    sizes = []
    for t in tables:
        try:
            cnt = cur.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        except sqlite3.Error:
            cnt = 0
        sizes.append((cnt, t))
    sizes.sort(reverse=True)
    table = sizes[0][1]
    cur.execute(f'SELECT * FROM "{table}"')
    cols = [d[0] for d in cur.description]
    db_types = {d[0]: d[1] for d in cur.description}
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return cols, rows, db_types, table


def load_primary_table(files, plan, input_dir):
    """Return (columns, rows, source_dict, db_types, table_name)."""
    candidates = [f for f in files if f["kind"] in
                  ("text-table", "json", "sqlite")]
    if not candidates:
        return [], [], {}, {}, None
    chosen = None
    if plan.get("table"):
        for f in candidates:
            if f["name"] == plan["table"] or f["rel_path"] == plan["table"]:
                chosen = f
                break
    if chosen is None:
        chosen = max(candidates, key=lambda f: f.get("size", 0))
    src = {"source_id": "S1", "path": chosen["path"], "kind": chosen["kind"],
           "rel_path": chosen["rel_path"], "name": chosen["name"]}
    db_types = {}
    table = None
    if chosen["kind"] == "sqlite":
        cols, rows, db_types, table = read_sqlite(chosen["path"])
    elif chosen["kind"] == "json":
        cols, rows = read_json_table(chosen["path"])
    else:
        cols, rows = read_csv(chosen["path"])
    return cols, rows, src, db_types, table


def classify_column(rows, col, db_types):
    vals = [r.get(col) for r in rows]
    n = len(vals)
    nonnull = [v for v in vals if v is not None and v != ""]
    nn = len(nonnull)
    numeric = 0
    for v in nonnull:
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            numeric += 1
        elif isinstance(v, str):
            try:
                float(v)
                numeric += 1
            except ValueError:
                pass
    ratio = (numeric / nn) if nn else 0.0
    distinct = len({str(v) for v in nonnull})
    if distinct >= max(2, 0.9 * nn) and nn > 0 and ratio < 0.5:
        kind = "id"
    elif ratio >= 0.9:
        kind = "numeric"
    elif distinct <= max(10, int(0.2 * nn)) or ratio >= 0.5:
        kind = "categorical"
    else:
        kind = "text"
    return {"column": col, "kind": kind, "missing": n - nn,
            "missing_ratio": round_sig((n - nn) / n if n else 0.0, 4),
            "distinct": distinct, "numeric_ratio": round_sig(ratio, 4),
            "db_type": db_types.get(col)}


def to_float(v):
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# cleaning
# --------------------------------------------------------------------------
def clean_rows(rows, columns, group_col, value_col):
    n_raw = len(rows)
    changes = []
    # 1) drop rows missing group/value
    keep = []
    for i, r in enumerate(rows):
        g = r.get(group_col) if group_col else True
        v = r.get(value_col) if value_col else True
        if group_col and (g is None or g == ""):
            continue
        if value_col and (v is None or v == ""):
            continue
        keep.append((i, r))
    n_missing_removed = n_raw - len(keep)
    changes.append(change_entry("C1", "drop_missing",
                                {"n": n_raw},
                                {"n": len(keep), "removed": n_missing_removed},
                                f"rows missing {group_col} or {value_col}"))
    # 2) exact duplicates (all columns)
    seen = set()
    dedup = []
    dup_removed = 0
    for i, r in keep:
        key = tuple(sorted((str(k), str(v)) for k, v in r.items()))
        if key in seen:
            dup_removed += 1
            continue
        seen.add(key)
        dedup.append({"row": r, "_orig_index": i})
    changes.append(change_entry("C2", "drop_duplicates",
                                {"n": len(keep)},
                                {"n": len(dedup), "removed": dup_removed},
                                "keep first occurrence of exact duplicate rows"))
    # 3) outlier flags (IQR 1.5 within each group; not silently deleted)
    groups = {}
    for rec in dedup:
        g = str(rec["row"].get(group_col)) if group_col else "_all"
        groups.setdefault(g, []).append(rec)
    flagged = 0
    for g, recs in groups.items():
        vals = [to_float(r["row"].get(value_col)) for r in recs]
        vals = [v for v in vals if v is not None]
        if len(vals) < 4:
            continue
        q1, q3 = S.quantile(vals, 0.25), S.quantile(vals, 0.75)
        iqr = q3 - q1
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        for rec in recs:
            v = to_float(rec["row"].get(value_col))
            rec["_outlier"] = bool(v is not None and (v < lo or v > hi))
            flagged += 1 if rec["_outlier"] else 0
    changes.append(change_entry("C3", "flag_outliers",
                                {"n": len(dedup)},
                                {"n": len(dedup), "flagged": flagged},
                                "IQR*1.5 flags, outliers retained not deleted"))
    return dedup, changes


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------
def group_values(cleaned, group_col, value_col):
    groups = {}
    order = []
    for rec in cleaned:
        g = str(rec["row"].get(group_col)) if group_col else "_all"
        v = to_float(rec["row"].get(value_col))
        if v is None:
            continue
        if g not in groups:
            groups[g] = []
            order.append(g)
        groups[g].append(v)
    return groups, order


def run_analysis(cleaned, group_col, value_col, design, plan, columns, rows_meta):
    design = design or "two_groups"
    value_unit = plan.get("value_unit")
    analysis = {"design": design, "group_col": group_col, "value_col": value_col,
                "value_unit": value_unit if value_unit else "unit_unknown"}

    if design in ("two_groups", "multi_group"):
        groups, order = group_values(cleaned, group_col, value_col)
        analysis["groups"] = order
        analysis["descriptive"] = {g: {"locator": f"cleaned#{group_col}={g}",
                                       "stats": S.describe(groups[g])}
                                   for g in order}
        # normality + homoscedasticity
        analysis["normality"] = {g: {"W": round_sig(S.shapiro_wilk(groups[g])[0]),
                                     "p": round_sig(S.shapiro_wilk(groups[g])[1]),
                                     "note": "Shapiro-Wilk (Royston AS R94 approximation)"}
                                 for g in order}
        analysis["homoscedasticity"] = S.brown_forsythe([groups[g] for g in order])

        if design == "two_groups":
            g0, g1 = order[0], order[1]
            x, y = groups[g0], groups[g1]
            norm_ok = all(analysis["normality"][g]["p"] is None or
                          analysis["normality"][g]["p"] >= 0.05 for g in order)
            equal_ok = (analysis["homoscedasticity"]["p"] is None or
                        analysis["homoscedasticity"]["p"] >= 0.05)
            if norm_ok and equal_ok:
                test = S.student_t_test(x, y)
            elif norm_ok:
                test = S.welch_t_test(x, y)
            else:
                test = S.mann_whitney_u(x, y)
            analysis["test"] = test
            analysis["effect"] = {
                "cohen_d": round_sig(S.cohen_d(x, y)),
                "hedges_g": round_sig(S.hedges_g(x, y)),
                "glass_delta": round_sig(S.glass_delta(x, y)),
                "direction": f"mean({g0}) - mean({g1})",
                "note": "glass_delta uses second group as reference"}
            analysis["ci"] = {
                "mean_diff_welch_95": [round_sig(v) for v in
                                       S.mean_diff_ci_welch(x, y)],
                "mean_95": {g: [round_sig(v) for v in S.mean_ci(groups[g])]
                            for g in order}}
        else:  # multi_group
            norm_ok = all(analysis["normality"][g]["p"] is None or
                          analysis["normality"][g]["p"] >= 0.05 for g in order)
            equal_ok = (analysis["homoscedasticity"]["p"] is None or
                        analysis["homoscedasticity"]["p"] >= 0.05)
            if norm_ok and equal_ok:
                test = S.one_way_anova([groups[g] for g in order])
            elif norm_ok:
                test = S.one_way_anova([groups[g] for g in order])
                test["note"] = "unequal variances: report Welch ANOVA or Kruskal-Wallis"
            else:
                test = S.kruskal_wallis([groups[g] for g in order])
            analysis["test"] = test
            analysis["effect"] = {
                "eta_squared": round_sig(S.eta_squared([groups[g] for g in order])),
                "omega_squared": round_sig(S.omega_squared([groups[g] for g in order])),
                "note": "eta^2 / omega^2 = proportion of variance explained"}
            # pairwise post-hoc with Bonferroni correction (never raw pairwise t)
            pairs = []
            m = len(order)
            for i in range(m):
                for j in range(i + 1, m):
                    gi, gj = order[i], order[j]
                    pw = S.mann_whitney_u(groups[gi], groups[gj]) \
                        if not norm_ok else S.welch_t_test(groups[gi], groups[gj])
                    p_raw = pw["p"]
                    p_corr = min(1.0, p_raw * (m * (m - 1) / 2)) if p_raw is not None else None
                    pairs.append({"pair": [gi, gj], "test": pw.get("test") or pw.get("test"),
                                  "statistic": pw.get("t", pw.get("U")),
                                  "p_raw": round_sig(p_raw),
                                  "p_bonferroni": round_sig(p_corr),
                                  "cohen_d": round_sig(S.cohen_d(groups[gi], groups[gj]))})
            analysis["posthoc"] = {"method": "Bonferroni correction",
                                   "pairs": pairs}
            analysis["ci"] = {"mean_95": {g: [round_sig(v) for v in S.mean_ci(groups[g])]
                                          for g in order}}

    elif design == "correlation":
        xcol = plan.get("value_col")
        ycol = plan.get("value_col2")
        xs = [to_float(r["row"].get(xcol)) for r in cleaned]
        ys = [to_float(r["row"].get(ycol)) for r in cleaned]
        pr = S.pearson_r(xs, ys)
        sr = S.spearman_rho(xs, ys)
        analysis["test"] = pr
        analysis["spearman"] = sr
        analysis["effect"] = {"r_squared": round_sig(pr["r"] ** 2)}
        analysis["ci"] = {"pearson_r_95": [round_sig(v) for v in
                                           S.corr_fisher_z_ci(pr["r"], len(xs))]}
        analysis["descriptive"] = {xcol: {"stats": S.describe(xs)},
                                   ycol: {"stats": S.describe(ys)}}

    elif design == "paired":
        xcol, ycol = plan.get("value_col"), plan.get("value_col2")
        xs = [to_float(r["row"].get(xcol)) for r in cleaned]
        ys = [to_float(r["row"].get(ycol)) for r in cleaned]
        test = S.paired_t_test(xs, ys)
        analysis["test"] = test
        analysis["effect"] = {"cohen_d_paired": round_sig(
            (S.mean(xs) - S.mean(ys)) / S.stdev([a - b for a, b in zip(xs, ys)])
            if S.stdev([a - b for a, b in zip(xs, ys)]) > 0 else None)}
        analysis["ci"] = {"mean_diff_95": [round_sig(v) for v in
                                           S.mean_ci([a - b for a, b in zip(xs, ys)])]}

    elif design == "one_sample":
        xcol = plan.get("value_col")
        xs = [to_float(r["row"].get(xcol)) for r in cleaned]
        xs = [v for v in xs if v is not None]
        analysis["descriptive"] = {xcol: {"stats": S.describe(xs)}}
        analysis["ci"] = {"mean_95": [round_sig(v) for v in S.mean_ci(xs)]}
        analysis["test"] = {"test": "one_sample_t", "note": "hypothesized mean required"}

    return analysis


# --------------------------------------------------------------------------
# figures (editable SVG)
# --------------------------------------------------------------------------
def _svg_esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def boxplot_svg(groups, labels, title, filepath):
    stats = {g: (S.quantile(v, 0.25), S.median(v), S.quantile(v, 0.75),
                 min(v), max(v), v) for g, v in groups.items()}
    k = len(labels)
    w, h = 80 + 90 * k, 360
    pad_l, pad_b = 60, 50
    ymax = max(hi for _, _, _, _, hi, _ in stats.values()) if stats else 1
    ymin = min(lo for _, _, _, lo, _, _ in stats.values()) if stats else 0
    ymin = min(0.0, ymin)
    rng = (ymax - ymin) or 1.0
    def Y(v):
        return h - pad_b - (v - ymin) / rng * (h - pad_b - 40)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
             f'viewBox="0 0 {w} {h}">']
    parts.append(f'<text x="{w/2}" y="24" text-anchor="middle" font-size="15">'
                 f'{_svg_esc(title)}</text>')
    for idx, g in enumerate(labels):
        cx = pad_l + idx * 90 + 40
        q1, med, q3, lo, hi, vals = stats[g]
        parts.append(f'<line x1="{cx}" y1="{Y(lo)}" x2="{cx}" y2="{Y(hi)}" '
                     f'stroke="black" stroke-width="1.5"/>')
        parts.append(f'<rect x="{cx-28}" y="{Y(q3)}" width="56" height="{Y(q1)-Y(q3)}" '
                     f'fill="#dce6f1" stroke="black"/>')
        parts.append(f'<line x1="{cx-28}" y1="{Y(med)}" x2="{cx+28}" y2="{Y(med)}" '
                     f'stroke="black" stroke-width="2"/>')
        for v in vals:
            if v < lo or v > hi:
                parts.append(f'<circle cx="{cx}" cy="{Y(v)}" r="2.5" fill="red"/>')
        parts.append(f'<text x="{cx}" y="{h-14}" text-anchor="middle" font-size="12">'
                     f'{_svg_esc(g)}</text>')
    parts.append(f'<line x1="{pad_l-14}" y1="{h-pad_b}" x2="{w-20}" y2="{h-pad_b}" '
                 f'stroke="black"/>')
    parts.append(f'<text x="{pad_l-20}" y="{h-pad_b}" text-anchor="end" font-size="11">'
                 f'{round_sig(ymin,4)}</text>')
    parts.append(f'<text x="{pad_l-20}" y="{Y(ymax)}" text-anchor="end" font-size="11">'
                 f'{round_sig(ymax,4)}</text>')
    parts.append("</svg>")
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))


def scatter_svg(xs, ys, xlabel, ylabel, title, filepath):
    w, h, pad = 420, 360, 50
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    xr = (xmax - xmin) or 1.0
    yr = (ymax - ymin) or 1.0
    def X(v): return pad + (v - xmin) / xr * (w - 2 * pad)
    def Y(v): return h - pad - (v - ymin) / yr * (h - 2 * pad)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
             f'viewBox="0 0 {w} {h}">']
    parts.append(f'<text x="{w/2}" y="22" text-anchor="middle" font-size="14">'
                 f'{_svg_esc(title)}</text>')
    for x, y in zip(xs, ys):
        parts.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="2.2" fill="#2b6cb0" '
                     f'fill-opacity="0.7"/>')
    parts.append(f'<line x1="{pad}" y1="{h-pad}" x2="{w-pad}" y2="{h-pad}" stroke="black"/>')
    parts.append(f'<line x1="{pad}" y1="{h-pad}" x2="{pad}" y2="{pad}" stroke="black"/>')
    parts.append(f'<text x="{w/2}" y="{h-16}" text-anchor="middle" font-size="12">'
                 f'{_svg_esc(xlabel)}</text>')
    parts.append(f'<text x="16" y="{h/2}" text-anchor="middle" font-size="12" '
                 f'transform="rotate(-90 16 {h/2})">{_svg_esc(ylabel)}</text>')
    parts.append("</svg>")
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def cmd_init(args):
    inp, work, out = args.input, args.work, args.output
    os.makedirs(work, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    case_path = os.path.join(inp, "case.json")
    case = json_read(case_path, {})
    case_id = case.get("case_id") or "unknown-case"
    contract = case.get("output_contract") if isinstance(case.get("output_contract"), dict) else {}
    files = discover(inp)
    index = {"case_id": case_id, "contract_keys": list(contract.keys()),
             "files": files, "case_path": case_path,
             "time": now_iso(), "input_dir": inp}
    json_write(os.path.join(work, "index.json"), index)

    result = {"case_id": case_id}
    listish = ("descriptive", "inferential", "effects", "quality", "sources",
               "uncertainty", "errors", "degradations", "pending")
    for k in contract:
        result[k] = [] if k.lower().split(".")[-1] in listish or "list" in k.lower() else None
    result.update({
        "status": "partial",
        "result": {},
        "sources": [{"source_id": "S1", "path": case_path, "kind": "text",
                     "rel_path": "case.json"}] if os.path.exists(case_path) else [],
        "uncertainty": ["initial skeleton: analysis not yet run"],
        "change_log": [change_entry("I1", "init", {}, {"status": "partial"},
                                    "skeleton written within 3-minute budget")],
        "errors": [], "degradations": [], "pending_human": [],
        "runtime": {"python": sys.version.split()[0], "time": now_iso(),
                    "input_dir": inp, "output_dir": out}})
    json_write(os.path.join(out, "result.json"), result)
    print("init ok:", case_id, "files:", len(files))


def cmd_inspect(args):
    work = args.work
    index = json_read(os.path.join(work, "index.json"), {})
    plan = json_read(os.path.join(work, "plan.json"), {})
    cols, rows, src, db_types, table = load_primary_table(index.get("files", []), plan, args.input)
    schema = {"source": src, "table": table, "n_rows": len(rows),
              "columns": [classify_column(rows, c, db_types) for c in cols]}
    json_write(os.path.join(work, "schema.json"), schema)
    print("inspect ok: rows", len(rows), "cols", len(cols))


def cmd_analyze(args):
    work, out = args.work, args.output
    index = json_read(os.path.join(work, "index.json"), {})
    plan = json_read(os.path.join(work, "plan.json"), {})
    schema = json_read(os.path.join(work, "schema.json"), {})
    cols, rows, src, db_types, table = load_primary_table(index.get("files", []), plan, args.input)
    if not rows:
        json_write(os.path.join(work, "analysis.json"),
                   {"error": "no_rows", "note": "insufficient data"})
        print("analyze: no rows")
        return
    col_meta = schema.get("columns") or [classify_column(rows, c, db_types) for c in cols]
    cats = [c for c in col_meta if c["kind"] == "categorical" and 2 <= c["distinct"] <= 12]
    nums = [c for c in col_meta if c["kind"] == "numeric"]

    group_col = plan.get("group_col") or (cats[0]["column"] if cats else None)
    value_col = plan.get("value_col") or (nums[0]["column"] if nums else None)
    value_col2 = plan.get("value_col2") or (nums[1]["column"] if len(nums) >= 2 else None)
    design = plan.get("design") or ("correlation" if not group_col and len(nums) >= 2
                                    else ("two_groups" if group_col and
                                          (next((c for c in col_meta if c["column"] == group_col), {}).get("distinct", 0) == 2)
                                          else "multi_group" if group_col else "one_sample"))
    if design in ("correlation", "paired") and not plan.get("value_col2"):
        plan["value_col2"] = value_col2
    # write auto-detected plan back so run_analysis sees the same fields
    if not plan.get("group_col"):
        plan["group_col"] = group_col
    if not plan.get("value_col"):
        plan["value_col"] = value_col
    if not plan.get("design"):
        plan["design"] = design

    cleaned, changes = clean_rows(rows, cols, group_col, value_col)
    analysis = run_analysis(cleaned, group_col, value_col, design, plan, cols, col_meta)

    # cleaned CSV
    os.makedirs(os.path.join(out, "cleaned"), exist_ok=True)
    clean_name = (table or os.path.splitext(src["name"])[0]) + "_cleaned.csv"
    clean_path = os.path.join(out, "cleaned", clean_name)
    if cleaned:
        fieldnames = [c for c in cols] + ["_orig_index", "_outlier"]
        with open(clean_path, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            for rec in cleaned:
                row = dict(rec["row"])
                row["_orig_index"] = rec["_orig_index"]
                row["_outlier"] = rec.get("_outlier", False)
                w.writerow(row)

    # figures
    os.makedirs(os.path.join(out, "figures"), exist_ok=True)
    if design in ("two_groups", "multi_group") and group_col and value_col:
        groups, order = group_values(cleaned, group_col, value_col)
        if groups:
            boxplot_svg(groups, order, f"{value_col} by {group_col}",
                        os.path.join(out, "figures", "boxplot.svg"))
    elif design == "correlation" and plan.get("value_col2"):
        xs = [to_float(r["row"].get(value_col)) for r in cleaned]
        ys = [to_float(r["row"].get(plan["value_col2"])) for r in cleaned]
        xs = [v for v in xs if v is not None]
        ys = [v for v in ys if v is not None]
        n = min(len(xs), len(ys))
        if n > 1:
            scatter_svg(xs[:n], ys[:n], value_col, plan["value_col2"],
                        f"{value_col} vs {plan['value_col2']}",
                        os.path.join(out, "figures", "scatter.svg"))

    # quality checks (seven cases)
    qc = {
        "missing": {"count": sum(1 for r in rows if (group_col and r.get(group_col) in (None, "")) or
                                 (value_col and r.get(value_col) in (None, ""))),
                    "rule": "rows missing group/value dropped and counted"},
        "outliers": {"method": "IQR*1.5 within group", "rule": "flagged, not silently deleted",
                     "flagged": sum(1 for r in cleaned if r.get("_outlier"))},
        "duplicates": {"rule": "exact-duplicate rows removed, keep first",
                       "removed": changes[1]["before"]["n"] - changes[1]["after"]["n"]
                       if len(changes) > 1 else 0},
        "assumptions": {"normality": "Shapiro-Wilk per group",
                        "homoscedasticity": "Brown-Forsythe"},
        "chart_choice": {"boxplot" if design in ("two_groups", "multi_group") else
                         ("scatter" if design == "correlation" else "none"): "editable SVG"},
        "code_run": {"script": "scripts/analyze.py", "stdlib_only": True,
                     "deterministic": True, "seed": None},
        "database_field": {"db_types": db_types, "unit": plan.get("value_unit") or "unit_unknown"},
    }
    json_write(os.path.join(out, "quality_checks.json"), qc)
    json_write(os.path.join(work, "analysis.json"),
               {"analysis": analysis, "changes": changes,
                "n_raw": len(rows), "n_cleaned": len(cleaned),
                "group_col": group_col, "value_col": value_col, "design": design,
                "source": src, "cleaned_path": clean_path})
    print("analyze ok:", design, "n_cleaned", len(cleaned))


def cmd_build(args):
    work, out, inp = args.work, args.output, args.input
    index = json_read(os.path.join(work, "index.json"), {})
    case = json_read(index.get("case_path", os.path.join(inp, "case.json")), {})
    contract = case.get("output_contract") if isinstance(case.get("output_contract"), dict) else {}
    analysis_doc = json_read(os.path.join(work, "analysis.json"), {})
    analysis = analysis_doc.get("analysis", {})
    changes = analysis_doc.get("changes", [])
    qc = json_read(os.path.join(out, "quality_checks.json"), {})

    result = {"case_id": index.get("case_id", "unknown-case")}
    cleaned_path = analysis_doc.get("cleaned_path", "")

    # backfill every output_contract key by keyword matching; never drop unknown keys
    for k in contract:
        result[k] = contract_value(k, analysis, qc, cleaned_path)

    # canonical result payload (independent of contract naming)
    payload = {
        "design": analysis.get("design"),
        "group_col": analysis.get("group_col"),
        "value_col": analysis.get("value_col"),
        "value_unit": analysis.get("value_unit", "unit_unknown"),
        "descriptive": analysis.get("descriptive", []),
        "normality": analysis.get("normality", {}),
        "homoscedasticity": analysis.get("homoscedasticity", {}),
        "test": analysis.get("test", {}),
        "posthoc": analysis.get("posthoc", []),
        "effect": analysis.get("effect", {}),
        "ci": analysis.get("ci", {}),
        "spearman": analysis.get("spearman", {}),
    }
    sources = build_sources(index, analysis_doc)
    result["result"] = payload
    result["status"] = "ok" if analysis else "partial"
    result["sources"] = sources
    result["uncertainty"] = build_uncertainty(analysis, qc)
    result["change_log"] = changes
    result["pending_human"] = build_pending(analysis, qc)
    result["errors"] = []
    result["degradations"] = build_degradations(analysis_doc)
    result["runtime"] = {"python": sys.version.split()[0], "time": now_iso(),
                         "script": "scripts/analyze.py", "deterministic": True}
    result["artifacts"] = {
        "cleaned": [analysis_doc.get("cleaned_path", "")],
        "figures": [os.path.join(out, "figures", f) for f in
                    os.listdir(os.path.join(out, "figures"))]
        if os.path.isdir(os.path.join(out, "figures")) else [],
        "report": os.path.join(out, "analysis_report.md"),
        "quality_checks": os.path.join(out, "quality_checks.json"),
        "code": os.path.join(out, "analysis_code.py"),
    }
    json_write(os.path.join(out, "result.json"), result)

    write_report(out, index, analysis_doc, qc)
    copy_code(out)
    print("build ok:", result["status"])


def build_uncertainty(analysis, qc):
    u = []
    if analysis.get("value_unit") in (None, "unit_unknown"):
        u.append("value unit not stated in data dictionary: marked unit_unknown")
    norm = analysis.get("normality", {})
    for g, d in norm.items():
        if d.get("p") is not None and d["p"] < 0.05:
            u.append(f"group '{g}' deviates from normality (Shapiro-Wilk p<0.05)")
    hs = analysis.get("homoscedasticity", {})
    if hs.get("p") is not None and hs["p"] < 0.05:
        u.append("variances unequal (Brown-Forsythe p<0.05)")
    if analysis.get("test", {}).get("note"):
        u.append("test note: " + str(analysis["test"]["note"]))
    if not u:
        u.append("no blocking uncertainties identified")
    return u


def build_pending(analysis, qc):
    p = []
    if analysis.get("value_unit") in (None, "unit_unknown"):
        p.append("confirm unit of value column from data dictionary")
    if qc.get("outliers", {}).get("flagged"):
        p.append("review flagged outliers before final interpretation")
    if analysis.get("test", {}).get("p") is None:
        p.append("test not applicable; inspect note for cause")
    return p


def build_degradations(analysis_doc):
    d = []
    if analysis_doc.get("error"):
        d.append(str(analysis_doc["error"]))
    if not analysis_doc.get("analysis"):
        d.append("analysis incomplete")
    return d


def contract_value(key, analysis, qc, cleaned_path):
    """Best-effort mapping from an output_contract key to computed results."""
    k = key.lower()
    if "descript" in k or k in ("stats", "summary"):
        return analysis.get("descriptive", {})
    if "infer" in k or "hypoth" in k or k in ("test", "tests"):
        return {"test": analysis.get("test", {}),
                "posthoc": analysis.get("posthoc", [])}
    if "effect" in k or "magnitude" in k:
        return analysis.get("effect", {})
    if "confid" in k or "ci" in k or "interval" in k:
        return analysis.get("ci", {})
    if "interpret" in k or "conclusion" in k or "finding" in k:
        return build_interpretation(analysis, qc)
    if "quality" in k or "check" in k:
        return qc
    if "clean" in k or "data" in k:
        return cleaned_path
    if "assumpt" in k or "normal" in k:
        return {"normality": analysis.get("normality", {}),
                "homoscedasticity": analysis.get("homoscedasticity", {})}
    # fallback: leave the key present, value null
    return None


def build_interpretation(analysis, qc):
    t = analysis.get("test", {}) or {}
    p = t.get("p")
    sig = None
    if p is not None:
        sig = p < 0.05
    eff = analysis.get("effect", {}) or {}
    lines = []
    if sig is True:
        lines.append(f"reject null at alpha=0.05 ({fmt_p(p)}); "
                     f"statistical significance does not imply practical importance")
    elif sig is False:
        lines.append(f"insufficient evidence to reject null at alpha=0.05 ({fmt_p(p)})")
    else:
        lines.append("test not applicable; see note")
    if eff.get("cohen_d") is not None:
        d = abs(eff["cohen_d"])
        label = "large" if d >= 0.8 else ("medium" if d >= 0.5 else ("small" if d >= 0.2 else "negligible"))
        lines.append(f"standardized mean difference |d|={d:.3f} ({label})")
    if eff.get("eta_squared") is not None:
        lines.append(f"eta^2={eff['eta_squared']:.3f}: proportion of variance explained")
    lines.append("95% CI is a parameter coverage interval, not an individual-value range")
    return {"conclusion": " ".join(lines),
            "significant": sig, "p": p,
            "caveats": build_uncertainty(analysis, qc)}


def build_sources(index, analysis_doc):
    out = []
    i = 0
    for f in index.get("files", []):
        if f.get("kind") in ("contract", "text-table", "sqlite", "json"):
            i += 1
            out.append({"source_id": f"S{i}", "path": f.get("path"),
                        "kind": "text" if f.get("kind") == "contract" else f.get("kind"),
                        "rel_path": f.get("rel_path"), "name": f.get("name")})
    if not out and analysis_doc.get("source"):
        out.append(analysis_doc["source"])
    return out


def write_report(out, index, analysis_doc, qc):
    analysis = analysis_doc.get("analysis", {})
    lines = ["# Statistical Analysis Report", "",
             f"- case_id: `{index.get('case_id', 'unknown-case')}`",
             f"- design: {analysis.get('design', 'n/a')}",
             f"- group column: `{analysis.get('group_col')}`",
             f"- value column: `{analysis.get('value_col')}`",
             f"- value unit: `{analysis.get('value_unit', 'unit_unknown')}`",
             f"- n_raw: {analysis_doc.get('n_raw')}; n_cleaned: {analysis_doc.get('n_cleaned')}",
             "", "## Cleaning", ""]
    for c in analysis_doc.get("changes", []):
        lines.append(f"- {c['action']}: before={c['before']} after={c['after']} ({c['reason']})")
    lines += ["", "## Descriptive statistics", ""]
    for g, d in (analysis.get("descriptive", {}) or {}).items():
        s = d.get("stats", {})
        lines.append(f"### {g}  (locator: {d.get('locator')})")
        lines.append(f"- n={s.get('n')}, mean={s.get('mean')}, median={s.get('median')}, "
                     f"sd={s.get('sd')}")
        lines.append(f"- min={s.get('min')}, max={s.get('max')}, Q1={s.get('q1')}, "
                     f"Q3={s.get('q3')}, IQR={s.get('iqr')}")
        lines.append(f"- skewness={s.get('skewness')}, kurtosis={s.get('kurtosis')}")
    if analysis.get("normality"):
        lines += ["", "## Assumption checks", ""]
        for g, d in analysis["normality"].items():
            lines.append(f"- Shapiro-Wilk {g}: W={d.get('W')}, {fmt_p(d.get('p'))}")
        hs = analysis.get("homoscedasticity", {})
        lines.append(f"- Brown-Forsythe: F={hs.get('statistic')}, {fmt_p(hs.get('p'))}")
    lines += ["", "## Hypothesis test", ""]
    t = analysis.get("test", {}) or {}
    lines.append(f"- method: {t.get('test')}")
    for k in ("t", "U", "H", "F", "z"):
        if t.get(k) is not None:
            lines.append(f"- statistic ({k}): {t[k]}")
    if t.get("df") is not None:
        lines.append(f"- df: {t['df']}")
    lines.append(f"- p: {fmt_p(t.get('p'))}")
    if analysis.get("posthoc"):
        lines += ["", "## Post-hoc (Bonferroni corrected)", ""]
        for pr in analysis["posthoc"]["pairs"]:
            lines.append(f"- {pr['pair'][0]} vs {pr['pair'][1]}: "
                         f"p_raw={pr['p_raw']}, p_bonferroni={pr['p_bonferroni']}, "
                         f"Cohen d={pr['cohen_d']}")
    if analysis.get("effect"):
        lines += ["", "## Effect size", ""]
        for k, v in analysis["effect"].items():
            if k != "note":
                lines.append(f"- {k}: {v}")
    if analysis.get("ci"):
        lines += ["", "## 95% confidence intervals", ""]
        for k, v in analysis["ci"].items():
            lines.append(f"- {k}: {v}")
    lines += ["", "## Interpretation", "",
              "CI is a parameter coverage interval, not the range of individual values.",
              "Statistical significance is reported separately from practical significance.",
              "", "## Reproducibility", "",
              f"- code: scripts/analyze.py (standard library only; deterministic, no random seed used)",
              f"- environment: Python {sys.version.split()[0]}", "",
              "## Limitations", ""]
    for u in build_uncertainty(analysis, qc):
        lines.append(f"- {u}")
    lines.append("")
    path = os.path.join(out, "analysis_report.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def copy_code(out):
    dst_dir = os.path.join(out)
    os.makedirs(dst_dir, exist_ok=True)
    shutil.copy(os.path.join(HERE, "analyze.py"), os.path.join(dst_dir, "analysis_code.py"))
    shutil.copy(os.path.join(HERE, "stats.py"), os.path.join(dst_dir, "analysis_stats.py"))


def cmd_selfcheck(args):
    out = args.output
    work = args.work
    ok = True
    result = json_read(os.path.join(out, "result.json"), None)
    index = json_read(os.path.join(work, "index.json"), {})
    problems = []
    if result is None:
        problems.append("result.json not parseable")
        ok = False
    else:
        if not result.get("case_id"):
            problems.append("missing case_id"); ok = False
        if "status" not in result:
            problems.append("missing status"); ok = False
        for k in index.get("contract_keys", []):
            if k not in result:
                problems.append(f"output_contract key missing: {k}"); ok = False
        for k in ("sources", "uncertainty", "change_log"):
            if k not in result:
                problems.append(f"missing envelope key: {k}"); ok = False
    for f in result.get("artifacts", {}).get("figures", []) if result else []:
        if f.endswith(".svg") and os.path.exists(f):
            try:
                ET.parse(f)
            except ET.ParseError:
                problems.append(f"invalid SVG: {f}"); ok = False
    if result and result.get("artifacts", {}).get("cleaned"):
        for c in result["artifacts"]["cleaned"]:
            if c and not os.path.exists(c):
                problems.append(f"missing cleaned CSV: {c}"); ok = False
    print("selfcheck", "PASS" if ok else "FAIL")
    for p in problems:
        print(" -", p)
    return 0 if ok else 1


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="deterministic statistical analysis engine")
    ap.add_argument("cmd", choices=["init", "inspect", "analyze", "build", "selfcheck"])
    ap.add_argument("--input", default=DEFAULTS["input"])
    ap.add_argument("--output", default=DEFAULTS["output"])
    ap.add_argument("--work", default=DEFAULTS["work"])
    args = ap.parse_args(argv)
    return {"init": cmd_init, "inspect": cmd_inspect, "analyze": cmd_analyze,
            "build": cmd_build, "selfcheck": cmd_selfcheck}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main() or 0)
