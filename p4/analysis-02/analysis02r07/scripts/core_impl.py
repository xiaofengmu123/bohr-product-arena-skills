"""core_impl.py - robustness-first analysis pipeline (standard library only).

Responsibilities
  * Discover and safely read input material (size caps, encoding fallback).
  * Echo the authoritative output_contract from /input/case.json.
  * Write a legal partial result.json early, then improve incrementally.
  * Run deterministic cleaning + descriptive + inference + effect-size + CI.
  * Degrade gracefully on empty input, bad JSON, missing params, oversized
    files, unreadable data, or write failures -- never crash, never fabricate.

CLI commands are dispatched by main.py:
  init     scan input + write partial skeleton result.json (first 3 minutes)
  survey   material/tool inventory
  inspect  schema + column types + missing/duplicate/outlier census
  analyze  deterministic cleaning + statistics
  build    assemble result.json + cleaned CSV + report + figures + quality log
  selfcheck  validate outputs
  selftest   numeric self-test (statlib)
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from collections import Counter

import statlib

# ---------------------------------------------------------------------------
# Robustness limits (documented, tunable)
# ---------------------------------------------------------------------------

SCAN_SIZE_CAP = 64 * 1024 * 1024          # skip any single file larger than this
TEXT_READ_CAP = 10 * 1024 * 1024          # cap for text/csv/json reads
MAX_TABLE_ROWS = 200_000                  # cap rows loaded from any table
MAX_TABLE_COLS = 500                      # cap columns kept per table
KNOWN_TEXT_EXTS = {".csv", ".tsv", ".txt", ".json", ".md", ".yaml", ".yml", ".log"}
KNOWN_TABLE_EXTS = {".csv", ".tsv"}
KNOWN_DB_EXTS = {".sqlite", ".sqlite3", ".db"}
MISSING_TOKENS = {"", "na", "n/a", "nan", "null", "none", "nil", "-", "--",
                  "missing", "not reported", "not_reported", "unknown", "nd", "?"}

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"

RESULT_ENVELOPE_KEYS = [
    "case_id", "status", "result", "quality", "descriptive", "inferential",
    "effects", "interpretation", "sources", "uncertainty", "pending_human",
    "errors", "degradations", "change_log", "runtime",
]

# ---------------------------------------------------------------------------
# Safe I/O helpers
# ---------------------------------------------------------------------------

def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def _log_change(change_log, action, before, after, reason):
    change_log.append({
        "id": "C%03d" % (len(change_log) + 1),
        "time": _now(),
        "action": action,
        "before": before,
        "after": after,
        "reason": reason,
    })


def read_bytes_safe(path, cap=None):
    """Read raw bytes; return None and register a degradation on oversize/IO error."""
    cap = cap or TEXT_READ_CAP
    try:
        size = os.path.getsize(path)
        if size > cap:
            return None, "oversize: %d bytes > cap %d" % (size, cap)
        with open(path, "rb") as f:
            return f.read(), None
    except OSError as e:
        return None, "io_error: %s" % e


def read_text_safe(path, cap=None):
    """Read text with encoding fallback; returns (text, encoding, error)."""
    raw, err = read_bytes_safe(path, cap)
    if raw is None:
        return None, None, err
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            return raw.decode(enc), enc, None
        except (UnicodeDecodeError, ValueError):
            continue
    return None, None, "undecodable_text"


def read_json_safe(path):
    """Parse JSON with tolerance for BOM/encoding; returns (obj, error)."""
    text, enc, err = read_text_safe(path)
    if text is None:
        return None, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        # Try to salvage a prefix of valid JSON by trimming at the error.
        try:
            partial = text[:e.pos]
            obj = json.loads(partial) if partial.strip() else None
            return obj, "bad_json: %s (salvaged prefix)" % e
        except Exception:
            return None, "bad_json: %s" % e


def _is_missing(v):
    if v is None:
        return True
    s = str(v).strip().lower()
    return s in MISSING_TOKENS


def _try_float(v):
    if _is_missing(v):
        return None, "missing"
    if isinstance(v, (int, float)):
        return float(v), "float"
    s = str(v).strip().replace(",", "")
    try:
        return float(s), "float"
    except ValueError:
        return None, "text"


def _guess_type(values):
    """Infer column type from non-missing values."""
    nonmissing = [v for v in values if not _is_missing(v)]
    if not nonmissing:
        return "empty"
    all_float = True
    all_int = True
    for v in nonmissing:
        f, kind = _try_float(v)
        if kind != "float":
            all_float = False
            all_int = False
            break
        if f is not None and f != int(f):
            all_int = False
    if all_float:
        return "int" if all_int else "float"
    if all(v is True or v is False or str(v).strip().lower() in {"true", "false"} for v in nonmissing):
        return "bool"
    return "text"


# ---------------------------------------------------------------------------
# Tabular data loading
# ---------------------------------------------------------------------------

def _load_delimited(path):
    text, enc, err = read_text_safe(path)
    if text is None:
        return None, err
    sample = text[:4096]
    dialect = None
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        if "\t" in sample.splitlines()[0] if sample.splitlines() else False:
            dialect = csv.excel_tab
        else:
            dialect = csv.excel
    raw_rows = list(csv.reader(io.StringIO(text), dialect))
    # Drop fully blank lines (common trailing newline artifact) so they are not
    # miscounted as missing-value rows.
    raw_rows = [r for r in raw_rows
                if any(cell is not None and str(cell).strip() != "" for cell in r)]
    if not raw_rows:
        return None, "empty_table"
    header = raw_rows[0]
    data_rows = raw_rows[1:]
    return {"header": header, "rows": data_rows}, None


def _load_sqlite(path):
    try:
        con = sqlite3.connect(path)
        cur = con.cursor()
        cur.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = cur.fetchall()
        out = {}
        for name, _sql in tables:
            try:
                cur.execute('SELECT * FROM "%s" LIMIT %d' % (name.replace('"', '""'), MAX_TABLE_ROWS))
                cols = [d[0] for d in cur.description] if cur.description else []
                data = cur.fetchall()
                if cols:
                    out[name] = {"header": cols, "rows": [list(r) for r in data]}
            except sqlite3.Error as e:
                out[name] = {"header": [], "rows": [], "error": str(e)}
        con.close()
        return out, None
    except sqlite3.Error as e:
        return None, "sqlite_error: %s" % e


def load_table(path):
    """Load a table from csv/tsv/sqlite; returns (table_dict, error)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in KNOWN_DB_EXTS:
        return _load_sqlite(path)
    if ext in KNOWN_TABLE_EXTS:
        return _load_delimited(path)
    return None, "unsupported_table_ext: %s" % ext


# ---------------------------------------------------------------------------
# Column-level census (missing / duplicate / outlier)
# ---------------------------------------------------------------------------

def column_census(header, rows):
    n = len(rows)
    cols = len(header) if header else 0
    info = []
    for c in range(cols):
        vals = [row[c] if c < len(row) else None for row in rows]
        n_missing = sum(1 for v in vals if _is_missing(v))
        n_numeric = 0
        numeric_vals = []
        for v in vals:
            f, kind = _try_float(v)
            if kind == "float":
                n_numeric += 1
                numeric_vals.append(f)
        typ = _guess_type(vals)
        entry = {
            "column": header[c] if c < len(header) else "col_%d" % c,
            "type": typ,
            "n": n,
            "n_missing": n_missing,
            "missing_ratio": (n_missing / n) if n else None,
            "n_numeric": n_numeric,
            "unit": None,
        }
        if numeric_vals and len(numeric_vals) >= 4:
            q1 = statlib.quantile(numeric_vals, 0.25)
            q3 = statlib.quantile(numeric_vals, 0.75)
            iqr = (q3 - q1) if (q1 is not None and q3 is not None) else None
            if iqr is not None and iqr > 0:
                lo = q1 - 1.5 * iqr
                hi = q3 + 1.5 * iqr
                n_out = sum(1 for v in numeric_vals if v < lo or v > hi)
                entry["outlier_low"] = lo
                entry["outlier_high"] = hi
                entry["n_outliers_iqr"] = n_out
                entry["outlier_ratio"] = n_out / len(numeric_vals)
        info.append(entry)
    return info


def duplicate_census(rows):
    """Count exact duplicate rows and return (before, after, kept_index)."""
    seen = {}
    keep = []
    dup_count = 0
    for i, row in enumerate(rows):
        key = tuple(row)
        if key in seen:
            dup_count += 1
        else:
            seen[key] = i
            keep.append(i)
    return len(rows), len(keep), dup_count, keep


# ---------------------------------------------------------------------------
# Design inference
# ---------------------------------------------------------------------------

def infer_design(header, rows, census):
    """Heuristically classify columns as grouping vs numeric vs paired.

    Returns a dict describing the plausible design. These are heuristics to be
    confirmed by the agent against the data dictionary; they never override
    the task text.
    """
    numeric_cols = []
    cat_cols = []
    for e in census:
        if e["type"] in ("int", "float"):
            numeric_cols.append(e["column"])
        elif e["type"] == "text":
            cat_cols.append(e["column"])
    design = {"numeric_columns": numeric_cols, "categorical_columns": cat_cols,
              "mode": None, "grouping": None}
    # Grouping: low-cardinality categorical column.
    if cat_cols:
        best = None
        best_card = 10 ** 9
        for col in cat_cols:
            idx = header.index(col) if col in header else -1
            if idx < 0:
                continue
            distinct = set()
            for row in rows:
                v = row[idx] if idx < len(row) else None
                if not _is_missing(v):
                    distinct.add(v)
                if len(distinct) > 60:
                    break
            card = len(distinct)
            if 2 <= card <= 50 and card < best_card:
                best = col
                best_card = card
        if best is not None:
            design["grouping"] = best
            design["group_cardinality"] = best_card
            design["mode"] = "grouped" if len(numeric_cols) >= 1 else "categorical_association"
    # Paired: hint from column names (pre/post, before/after, baseline/followup).
    if design["mode"] is None and len(numeric_cols) >= 2:
        joined = " ".join(numeric_cols).lower()
        if any(k in joined for k in ("pre", "post", "before", "after", "baseline", "follow", "paired")):
            design["mode"] = "paired"
    if design["mode"] is None:
        if len(numeric_cols) >= 2:
            design["mode"] = "correlation"
        elif len(numeric_cols) == 1:
            design["mode"] = "descriptive_only"
    return design


# ---------------------------------------------------------------------------
# Cleaned CSV generation
# ---------------------------------------------------------------------------

def build_cleaned_rows(header, rows, keep_idx, census, outlier_cols):
    """Return header + deduplicated rows with an added __outlier_flag column."""
    out_header = list(header) + ["__outlier_flag"]
    out_rows = []
    for i in keep_idx:
        row = list(rows[i])
        flags = []
        for c, e in enumerate(census):
            if e["column"] in outlier_cols and c < len(row):
                f, kind = _try_float(row[c])
                if kind == "float" and f is not None:
                    lo = e.get("outlier_low")
                    hi = e.get("outlier_high")
                    if lo is not None and hi is not None and (f < lo or f > hi):
                        flags.append(e["column"])
        row.append(";".join(flags) if flags else "")
        out_rows.append(row)
    return out_header, out_rows


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


# ---------------------------------------------------------------------------
# SVG figures (pure stdlib, editable)
# ---------------------------------------------------------------------------

def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render_boxplot_svg(title, values, width=520, height=300):
    n = len(values)
    if n < 5:
        return None
    a = sorted(values)
    q1 = statlib.quantile(a, 0.25)
    med = statlib.quantile(a, 0.50)
    q3 = statlib.quantile(a, 0.75)
    iqr = q3 - q1
    lo_f = q1 - 1.5 * iqr
    hi_f = q3 + 1.5 * iqr
    lo_w = next((v for v in a if v >= lo_f), a[0])
    hi_w = next((v for v in reversed(a) if v <= hi_f), a[-1])
    vmin, vmax = min(a), max(a)
    span = (vmax - vmin) or 1.0
    pad = 60
    plot_x0, plot_x1 = pad, width - 30
    plot_y0, plot_y1 = 30, height - 40

    def px(v):
        return plot_x0 + (v - vmin) / span * (plot_x1 - plot_x0)

    y = plot_y0 + (plot_y1 - plot_y0) / 2
    box_h = 60
    parts = []
    parts.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d">' % (width, height))
    parts.append('<rect width="100%%" height="100%%" fill="white"/>')
    parts.append('<text x="%d" y="20" font-size="13" font-family="sans-serif">%s</text>' % (pad, _esc(title)))
    # whiskers
    parts.append('<line x1="%.2f" y1="%d" x2="%.2f" y2="%d" stroke="black"/>' % (px(lo_w), y, px(hi_w), y))
    parts.append('<line x1="%.2f" y1="%d" x2="%.2f" y2="%d" stroke="black"/>' % (px(lo_w), y - 25, px(lo_w), y + 25))
    parts.append('<line x1="%.2f" y1="%d" x2="%.2f" y2="%d" stroke="black"/>' % (px(hi_w), y - 25, px(hi_w), y + 25))
    # box
    parts.append('<rect x="%.2f" y="%d" width="%.2f" height="%d" fill="none" stroke="black"/>' % (px(q1), y - box_h // 2, px(q3) - px(q1), box_h))
    # median
    parts.append('<line x1="%.2f" y1="%d" x2="%.2f" y2="%d" stroke="black" stroke-width="2"/>' % (px(med), y - box_h // 2, px(med), y + box_h // 2))
    # axis labels
    parts.append('<text x="%.2f" y="%d" font-size="10" font-family="sans-serif">%.4g</text>' % (px(vmin), plot_y1 + 18, vmin))
    parts.append('<text x="%.2f" y="%d" font-size="10" font-family="sans-serif">%.4g</text>' % (px(vmax) - 30, plot_y1 + 18, vmax))
    parts.append('</svg>')
    return "\n".join(parts)


def render_histogram_svg(title, values, bins=20, width=520, height=300):
    n = len(values)
    if n < 2:
        return None
    vmin, vmax = min(values), max(values)
    span = (vmax - vmin) or 1.0
    bin_w = span / bins
    counts = [0] * bins
    for v in values:
        idx = min(bins - 1, int((v - vmin) / bin_w))
        counts[idx] += 1
    cmax = max(counts) or 1
    pad = 60
    plot_x0, plot_x1 = pad, width - 20
    plot_y0, plot_y1 = 20, height - 40
    usable_w = plot_x1 - plot_x0
    usable_h = plot_y1 - plot_y0
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d">' % (width, height)]
    parts.append('<rect width="100%%" height="100%%" fill="white"/>')
    parts.append('<text x="%d" y="14" font-size="13" font-family="sans-serif">%s</text>' % (pad, _esc(title)))
    for b in range(bins):
        bh = counts[b] / cmax * usable_h
        x = plot_x0 + b * usable_w / bins
        w = usable_w / bins
        y = plot_y1 - bh
        parts.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" fill="#4477aa" stroke="none"/>' % (x, y, w, bh))
    parts.append('<text x="%d" y="%d" font-size="10" font-family="sans-serif">min=%.4g max=%.4g n=%d</text>' % (pad, plot_y1 + 16, vmin, vmax, n))
    parts.append('</svg>')
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Analysis driver
# ---------------------------------------------------------------------------

def analyze_table(name, header, rows):
    """Return {cleaning, descriptive, inferential, effects, cis} for a table."""
    n_raw = len(rows)
    before, after, dup_count, keep_idx = duplicate_census(rows)
    census = column_census(header, rows)
    numeric_cols = [e["column"] for e in census if e["type"] in ("int", "float")]
    design = infer_design(header, rows, census)

    cleaning = {
        "table": name, "n_raw": n_raw, "n_after_dedup": after,
        "n_duplicates_removed": dup_count, "keep_first_occurrence": True,
        "missing_policy": "never_imputed_as_zero; reported per column",
        "outlier_policy": "iqr_1.5_flag_not_deleted",
        "columns": census,
    }

    descriptive = []
    for col in numeric_cols:
        idx = header.index(col) if col in header else -1
        if idx < 0:
            continue
        vals = []
        for row in rows:
            f, kind = _try_float(row[idx] if idx < len(row) else None)
            if kind == "float":
                vals.append(f)
        if vals:
            descriptive.append({"table": name, "column": col, "unit": None,
                                "stats": statlib.descriptive(vals)})

    inferential = []
    effects = []
    cis = []

    # Grouped design
    if design["mode"] == "grouped" and design["grouping"]:
        gcol = design["grouping"]
        gidx = header.index(gcol)
        groups = {}
        for row in rows:
            gv = row[gidx] if gidx < len(row) else None
            if _is_missing(gv):
                continue
            groups.setdefault(gv, {})
        for col in numeric_cols:
            cidx = header.index(col)
            per_group = {}
            for gv in groups:
                per_group[gv] = []
            for row in rows:
                gv = row[gidx] if gidx < len(row) else None
                if _is_missing(gv):
                    continue
                f, kind = _try_float(row[cidx] if cidx < len(row) else None)
                if kind == "float":
                    per_group[gv].append(f)
            per_group = {k: v for k, v in per_group.items() if v}
            if len(per_group) < 2:
                continue
            group_names = sorted(per_group.keys())
            group_arrays = [per_group[k] for k in group_names]
            # Assumption checks
            normal = {k: statlib.jarque_bera(v) for k, v in per_group.items()}
            sf = {k: statlib.shapiro_francia(v) for k, v in per_group.items()}
            if len(group_names) == 2:
                a, b = group_arrays[0], group_arrays[1]
                lev = statlib.levene_brown_forsythe(group_arrays)
                normal_ok = all((normal[k] is None) or (normal[k]["p"] is None) or (normal[k]["p"] >= 0.05) for k in group_names)
                equal_var_ok = (lev is None) or (lev["p"] is None) or (lev["p"] >= 0.05)
                chosen = "welch_t" if (normal_ok and not equal_var_ok) else "student_t"
                if not normal_ok:
                    chosen = "mann_whitney"
                tst = None
                if chosen == "student_t":
                    tst = statlib.student_t_test(a, b)
                elif chosen == "welch_t":
                    tst = statlib.welch_t_test(a, b)
                else:
                    tst = statlib.mann_whitney(a, b)
                inferential.append({"table": name, "column": col, "groups": group_names,
                                    "design": "two_group", "normality_jarque_bera": normal,
                                    "shapiro_francia_w": sf, "levene_brown_forsythe": lev,
                                    "test_chosen": chosen, "test": tst})
                d = statlib.cohens_d(a, b)
                g = statlib.hedges_g(a, b)
                gd = statlib.glass_delta(a, b)
                effects.append({"table": name, "column": col, "comparison": group_names,
                                "cohens_d": d, "cohens_d_ci": statlib.cohens_d_ci(d, len(a), len(b)),
                                "hedges_g": g, "glass_delta": gd,
                                "direction": "group0_minus_group1"})
                cis.append({"table": name, "column": col, "mean_diff_ci": statlib.mean_diff_ci(a, b),
                            "mean_ci_group0": statlib.mean_ci(a), "mean_ci_group1": statlib.mean_ci(b)})
            else:
                lev = statlib.levene_brown_forsythe(group_arrays)
                normal_ok = all((normal[k] is None) or (normal[k]["p"] is None) or (normal[k]["p"] >= 0.05) for k in group_names)
                chosen = "anova" if (normal_ok and (lev is None or lev["p"] is None or lev["p"] >= 0.05)) else "kruskal_wallis"
                if chosen == "anova":
                    tst = statlib.anova(group_arrays)
                else:
                    tst = statlib.kruskal_wallis(group_arrays)
                inferential.append({"table": name, "column": col, "groups": group_names,
                                    "design": "multi_group", "normality_jarque_bera": normal,
                                    "shapiro_francia_w": sf, "levene_brown_forsythe": lev,
                                    "test_chosen": chosen, "test": tst,
                                    "pairwise_note": "no_unadjusted_pairwise_t; use corrected post-hoc if needed"})
                if chosen == "anova" and tst:
                    effects.append({"table": name, "column": col,
                                    "eta_omega": statlib.eta_squared_omega(tst)})

    # Paired design
    elif design["mode"] == "paired" and len(numeric_cols) >= 2:
        c1, c2 = numeric_cols[0], numeric_cols[1]
        i1, i2 = header.index(c1), header.index(c2)
        x, y = [], []
        for row in rows:
            a = _try_float(row[i1] if i1 < len(row) else None)
            b = _try_float(row[i2] if i2 < len(row) else None)
            if a[1] == "float" and b[1] == "float":
                x.append(a[0])
                y.append(b[0])
        n_pair = min(len(x), len(y))
        x, y = x[:n_pair], y[:n_pair]
        if n_pair >= 2:
            inferential.append({"table": name, "columns": [c1, c2], "design": "paired",
                                "paired_t": statlib.paired_t_test(x, y),
                                "wilcoxon_signed_rank": statlib.wilcoxon_signed_rank(x, y)})
            d = [a - b for a, b in zip(x, y)]
            effects.append({"table": name, "comparison": [c1, c2],
                            "cohens_dz": statlib.cohens_d(d, [0] * len(d)) if len(d) >= 2 else None})
            cis.append({"table": name, "mean_diff_ci": statlib.mean_diff_ci(x, y)})

    # Correlation design
    elif design["mode"] == "correlation":
        pairs = []
        for i in range(len(numeric_cols)):
            for j in range(i + 1, len(numeric_cols)):
                pairs.append((numeric_cols[i], numeric_cols[j]))
        for c1, c2 in pairs:
            i1, i2 = header.index(c1), header.index(c2)
            x, y = [], []
            for row in rows:
                a = _try_float(row[i1] if i1 < len(row) else None)
                b = _try_float(row[i2] if i2 < len(row) else None)
                if a[1] == "float" and b[1] == "float":
                    x.append(a[0])
                    y.append(b[0])
            if len(x) >= 3:
                pr = statlib.pearson(x, y)
                sr = statlib.spearman(x, y)
                inferential.append({"table": name, "columns": [c1, c2], "design": "correlation",
                                    "pearson": pr, "spearman": sr})
                if pr:
                    cis.append({"table": name, "columns": [c1, c2],
                                "pearson_r_ci": statlib.fisher_z_ci(pr["r"], pr["n"])})

    # Categorical association
    if design["grouping"] and len(design["categorical_columns"]) >= 2:
        c1 = design["grouping"]
        others = [c for c in design["categorical_columns"] if c != c1]
        for c2 in others[:2]:
            i1, i2 = header.index(c1), header.index(c2)
            counts = Counter()
            for row in rows:
                v1 = row[i1] if i1 < len(row) else None
                v2 = row[i2] if i2 < len(row) else None
                if not _is_missing(v1) and not _is_missing(v2):
                    counts[(v1, v2)] += 1
            if counts:
                rvals = sorted({k[0] for k in counts})
                cvals = sorted({k[1] for k in counts})
                table = [[counts.get((rv, cv), 0) for cv in cvals] for rv in rvals]
                cv_res = statlib.cramers_v(table)
                inferential.append({"table": name, "columns": [c1, c2], "design": "categorical",
                                    "cramers_v": cv_res})

    return {"cleaning": cleaning, "descriptive": descriptive,
            "inferential": inferential, "effects": effects, "cis": cis,
            "design": design}


# ---------------------------------------------------------------------------
# Result skeleton + aggregation
# ---------------------------------------------------------------------------

# Canonical result categories the pipeline can produce. Used to fill whatever
# field names the case's output_contract uses, without dropping unknown keys.
CONTRACT_ALIASES = {
    "descriptive": "descriptive",
    "descriptive_statistics": "descriptive",
    "desc": "descriptive",
    "inferential": "inferential",
    "inference": "inferential",
    "hypothesis_tests": "inferential",
    "tests": "inferential",
    "effects": "effects",
    "effect": "effects",
    "effect_size": "effects",
    "effect_sizes": "effects",
    "effect_size_ci": "effects",
    "confidence_interval": "confidence_intervals",
    "confidence_intervals": "confidence_intervals",
    "ci": "confidence_intervals",
    "cis": "confidence_intervals",
    "intervals": "confidence_intervals",
    "interpretation": "interpretation",
    "conclusion": "interpretation",
    "conclusions": "interpretation",
    "quality": "quality",
    "quality_checks": "quality",
    "quality_check": "quality",
    "cleaning": "cleaning",
    "clean": "cleaning",
    "preprocessing": "cleaning",
    "result": "result",
    "results": "result",
    "main_results": "result",
}


def _norm_key(key):
    return str(key).strip().lower().replace("-", "_").replace(" ", "_")


def fill_contract_values(result, contract, computed):
    """Map computed categories into output_contract field names.

    Never renames or drops a contract key; unknown keys keep their echo value
    (null/[]), and known keys receive the actual computed value when available.
    """
    if not isinstance(contract, dict):
        return result
    for key, _val in contract.items():
        norm = _norm_key(key)
        if norm in RESULT_ENVELOPE_KEYS:
            continue  # envelope fields are filled directly by the caller
        target = CONTRACT_ALIASES.get(norm)
        if target is None:
            # Try singular/plural heuristics.
            if norm.endswith("s") and CONTRACT_ALIASES.get(norm[:-1]):
                target = CONTRACT_ALIASES[norm[:-1]]
            elif (norm + "s") in CONTRACT_ALIASES:
                target = CONTRACT_ALIASES[norm + "s"]
        if target and target in computed:
            result[key] = computed[target]
        elif norm.startswith("ci") or "confidence" in norm or "interval" in norm:
            result[key] = computed.get("confidence_intervals", [])
    return result


def build_skeleton(case_id, contract, status, note):
    """A legal-but-partial result.json envelope."""
    result = {
        "case_id": case_id or "unknown-case",
        "status": status,
        "result": {},
        "quality": [],
        "descriptive": [],
        "inferential": [],
        "effects": [],
        "interpretation": {"note": note},
        "sources": [],
        "uncertainty": [note] if note else [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "runtime": {},
    }
    # Echo every output_contract key with null/[] defaults, never rename/drop.
    if isinstance(contract, dict):
        for key, val in contract.items():
            if key not in result:
                result[key] = val if val is not None else None
    return result


def write_json(path, obj):
    # ensure_ascii=True keeps the artifact pure-ASCII (unicode escaped), which
    # any JSON reader parses identically regardless of locale/encoding.
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=True, indent=2, sort_keys=True)
        f.write("\n")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

class Context:
    def __init__(self, input_dir, output_dir, work_dir):
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.work_dir = work_dir
        self.case = None
        self.case_id = None
        self.contract = None
        self.task_text = None
        self.files = []
        self.tables = {}
        self.analysis = {}
        self.errors = []
        self.degradations = []
        self.change_log = []
        self.started = time.time()


def ensure_dirs(ctx):
    for d in (ctx.output_dir, ctx.work_dir):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as e:
            ctx.errors.append("cannot_create_dir %s: %s" % (d, e))


def discover(ctx):
    ctx.files = []
    if not os.path.isdir(ctx.input_dir):
        ctx.errors.append("input_dir_missing: %s" % ctx.input_dir)
        return
    try:
        names = sorted(os.listdir(ctx.input_dir))
    except OSError as e:
        ctx.errors.append("input_dir_unreadable: %s" % e)
        return
    for name in names:
        path = os.path.join(ctx.input_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            size = -1
        kind = "data" if os.path.splitext(name)[1].lower() in (KNOWN_TABLE_EXTS | KNOWN_DB_EXTS) else "document"
        if os.path.splitext(name)[1].lower() in KNOWN_DB_EXTS:
            kind = "database"
        entry = {"source_id": "S%03d" % (len(ctx.files) + 1), "path": path,
                 "name": name, "kind": kind, "size_bytes": size,
                 "rel_path": name, "oversize": size > SCAN_SIZE_CAP}
        if entry["oversize"]:
            ctx.degradations.append("skipped_oversize_file: %s (%d bytes)" % (name, size))
        ctx.files.append(entry)


def load_case(ctx):
    case_path = os.path.join(ctx.input_dir, "case.json")
    if not os.path.isfile(case_path):
        ctx.errors.append("missing_case_json: /input/case.json")
        return
    obj, err = read_json_safe(case_path)
    if obj is None:
        ctx.errors.append("case_json_unreadable: %s" % err)
        return
    ctx.case = obj if isinstance(obj, dict) else {"raw": obj}
    ctx.case_id = obj.get("case_id") if isinstance(obj, dict) else None
    ctx.contract = obj.get("output_contract") if isinstance(obj, dict) else None
    if ctx.case_id is None:
        ctx.case_id = obj.get("id") if isinstance(obj, dict) else None
    task_path = os.path.join(ctx.input_dir, "task.md")
    if os.path.isfile(task_path):
        txt, _enc, err = read_text_safe(task_path)
        if txt is not None:
            ctx.task_text = txt
        else:
            ctx.errors.append("task_md_unreadable: %s" % err)


def cmd_init(ctx):
    ensure_dirs(ctx)
    discover(ctx)
    load_case(ctx)
    note = "initial partial skeleton written before full analysis"
    skeleton = build_skeleton(ctx.case_id, ctx.contract, STATUS_PARTIAL, note)
    skeleton["sources"] = [{"source_id": f["source_id"], "path": f["path"],
                            "kind": f["kind"], "rel_path": f["rel_path"]} for f in ctx.files
                           if not f.get("oversize")]
    for e in ctx.errors:
        skeleton["errors"].append(e)
    for d in ctx.degradations:
        skeleton["degradations"].append(d)
    if not ctx.files:
        skeleton["status"] = STATUS_INSUFFICIENT
        skeleton["uncertainty"].append("no input files found under /input")
    skeleton["runtime"] = {"elapsed_seconds": round(time.time() - ctx.started, 2),
                           "command": "init"}
    write_json(os.path.join(ctx.output_dir, "result.json"), skeleton)
    write_json(os.path.join(ctx.work_dir, "index.json"),
               {"case_id": ctx.case_id, "files": ctx.files,
                "errors": ctx.errors, "degradations": ctx.degradations,
                "written_at": _now()})
    return skeleton


def cmd_survey(ctx):
    discover(ctx)
    load_case(ctx)
    tools = {"python_stdlib": True, "scientific_python": "detect_at_runtime",
             "sqlite": True}
    payload = {"case_id": ctx.case_id, "files": ctx.files,
               "tools": tools, "errors": ctx.errors,
               "degradations": ctx.degradations}
    write_json(os.path.join(ctx.work_dir, "survey.json"), payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_inspect(ctx):
    discover(ctx)
    load_case(ctx)
    schemas = {}
    for f in ctx.files:
        if f.get("oversize"):
            continue
        ext = os.path.splitext(f["name"])[1].lower()
        if ext not in (KNOWN_TABLE_EXTS | KNOWN_DB_EXTS):
            continue
        loaded, err = load_table(f["path"])
        if err:
            ctx.degradations.append("load_failed %s: %s" % (f["name"], err))
            continue
        tables = loaded if isinstance(loaded, dict) and ("header" in loaded or any(isinstance(v, dict) for v in loaded.values())) else {}
        if "header" in tables:
            tables = {f["name"]: tables}
        for tname, t in tables.items():
            if not isinstance(t, dict) or "header" not in t:
                continue
            header = t["header"]
            rows = t["rows"][:MAX_TABLE_ROWS]
            census = column_census(header, rows)
            before, after, dup, keep = duplicate_census(rows)
            schemas["%s::%s" % (f["name"], tname)] = {
                "source": f["path"], "n_rows": len(rows), "n_cols": len(header),
                "columns": census, "duplicates": {"n_before": before, "n_after": after,
                                                  "n_removed": dup}}
            ctx.tables["%s::%s" % (f["name"], tname)] = {"header": header, "rows": rows}
    payload = {"case_id": ctx.case_id, "schemas": schemas,
               "errors": ctx.errors, "degradations": ctx.degradations}
    write_json(os.path.join(ctx.work_dir, "schema.json"), payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_analyze(ctx):
    # Re-discover and load tables if not already loaded.
    discover(ctx)
    load_case(ctx)
    for f in ctx.files:
        if f.get("oversize"):
            continue
        ext = os.path.splitext(f["name"])[1].lower()
        if ext not in (KNOWN_TABLE_EXTS | KNOWN_DB_EXTS):
            continue
        loaded, err = load_table(f["path"])
        if err:
            ctx.degradations.append("load_failed %s: %s" % (f["name"], err))
            continue
        tables = loaded
        if "header" in tables:
            tables = {f["name"]: tables}
        for tname, t in tables.items():
            if not isinstance(t, dict) or "header" not in t:
                continue
            key = "%s::%s" % (f["name"], tname)
            ctx.tables[key] = {"header": t["header"], "rows": t["rows"][:MAX_TABLE_ROWS],
                               "source": f["path"]}
            ctx.analysis[key] = analyze_table(tname, t["header"], t["rows"][:MAX_TABLE_ROWS])
    write_json(os.path.join(ctx.work_dir, "analysis.json"), ctx.analysis)
    print(json.dumps({"tables_analyzed": list(ctx.analysis.keys())}, ensure_ascii=False, indent=2))


def cmd_build(ctx):
    # Load intermediate state if fresh process.
    if not ctx.analysis:
        ap = os.path.join(ctx.work_dir, "analysis.json")
        if os.path.isfile(ap):
            obj, _err = read_json_safe(ap)
            if isinstance(obj, dict):
                ctx.analysis = obj
    discover(ctx)
    load_case(ctx)
    skeleton = build_skeleton(ctx.case_id, ctx.contract, STATUS_OK, "")
    cleaned_dir = os.path.join(ctx.output_dir, "cleaned")
    figures_dir = os.path.join(ctx.output_dir, "figures")
    try:
        os.makedirs(cleaned_dir, exist_ok=True)
        os.makedirs(figures_dir, exist_ok=True)
    except OSError as e:
        ctx.errors.append("cannot_create_output_subdir: %s" % e)

    change_log = []
    _log_change(change_log, "build", "raw input", "analysis artifacts", "deterministic pipeline")

    cleaned_files = []
    figure_files = []
    for key, tab in ctx.tables.items():
        header, rows = tab["header"], tab["rows"]
        analysis = ctx.analysis.get(key, {})
        cleaning = analysis.get("cleaning", {})
        census = cleaning.get("columns", [])
        before, after, dup, keep = duplicate_census(rows)
        outlier_cols = [e["column"] for e in census if e.get("n_outliers_iqr", 0) > 0]
        out_header, out_rows = build_cleaned_rows(header, rows, keep, census, outlier_cols)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", key)
        cpath = os.path.join(cleaned_dir, "cleaned_%s.csv" % safe_name)
        try:
            write_csv(cpath, out_header, out_rows)
            cleaned_files.append(cpath)
        except OSError as e:
            ctx.errors.append("cleaned_csv_write_failed %s: %s" % (safe_name, e))
        # figures for numeric columns
        for e in census:
            if e["type"] not in ("int", "float"):
                continue
            idx = header.index(e["column"]) if e["column"] in header else -1
            if idx < 0:
                continue
            vals = []
            for row in rows:
                f, kind = _try_float(row[idx] if idx < len(row) else None)
                if kind == "float":
                    vals.append(f)
            if len(vals) >= 5:
                svg = render_boxplot_svg("%s :: %s" % (safe_name, e["column"]), vals)
                if svg:
                    fp = os.path.join(figures_dir, "box_%s_%s.svg" % (safe_name, re.sub(r"[^A-Za-z0-9_.-]", "_", e["column"])))
                    try:
                        with open(fp, "w", encoding="utf-8") as fh:
                            fh.write(svg)
                        figure_files.append(fp)
                    except OSError as ex:
                        ctx.errors.append("svg_write_failed %s: %s" % (fp, ex))

    # Report
    report_lines = ["# 统计分析报告（可复核）", "",
                    "case_id: %s" % (ctx.case_id or "unknown-case"),
                    "生成时间: %s" % _now(), ""]
    report_lines.append("## 1. 清洗规则")
    report_lines.append("缺失值不当作 0；重复记录去重（保留首次出现）；异常值按 IQR 1.5 标记、不静默删除；")
    report_lines.append("所有动作见 change_log 与 quality_checks.json。")
    report_lines.append("")
    report_lines.append("## 2. 描述统计 / 推断统计 / 效应量 / 置信区间")
    for key, analysis in ctx.analysis.items():
        report_lines.append("### %s" % key)
        report_lines.append("```json")
        report_lines.append(json.dumps(analysis, ensure_ascii=False, indent=2, sort_keys=True, default=str))
        report_lines.append("```")
        report_lines.append("")
    report_path = os.path.join(ctx.output_dir, "analysis_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))

    # Quality checks
    quality = []
    for key, analysis in ctx.analysis.items():
        cleaning = analysis.get("cleaning", {})
        quality.append({
            "table": key,
            "missing_policy": cleaning.get("missing_policy"),
            "outlier_policy": cleaning.get("outlier_policy"),
            "n_raw": cleaning.get("n_raw"),
            "n_after_dedup": cleaning.get("n_after_dedup"),
            "n_duplicates_removed": cleaning.get("n_duplicates_removed"),
            "columns_checked": len(cleaning.get("columns", [])),
        })
    quality_path = os.path.join(ctx.output_dir, "quality_checks.json")
    write_json(quality_path, {"case_id": ctx.case_id, "checks": quality,
                              "errors": ctx.errors, "degradations": ctx.degradations})

    # Reproducible code snapshot
    code_dir = os.path.join(ctx.output_dir, "code")
    try:
        os.makedirs(code_dir, exist_ok=True)
        here = os.path.dirname(os.path.abspath(__file__))
        for fname in ("statlib.py", "core_impl.py", "main.py"):
            src = os.path.join(here, fname)
            if os.path.isfile(src):
                shutil.copyfile(src, os.path.join(code_dir, fname))
    except OSError as e:
        ctx.errors.append("code_snapshot_failed: %s" % e)

    # Assemble result.json
    result = skeleton
    result["status"] = STATUS_OK if ctx.analysis else STATUS_PARTIAL
    if not ctx.files:
        result["status"] = STATUS_INSUFFICIENT

    descriptive = [d for a in ctx.analysis.values() for d in a.get("descriptive", [])]
    inferential = [d for a in ctx.analysis.values() for d in a.get("inferential", [])]
    effects = [d for a in ctx.analysis.values() for d in a.get("effects", [])]
    cis = [d for a in ctx.analysis.values() for d in a.get("cis", [])]
    cleaning = [a.get("cleaning", {}) for a in ctx.analysis.values()]

    computed = {
        "descriptive": descriptive,
        "inferential": inferential,
        "effects": effects,
        "confidence_intervals": cis,
        "cleaning": cleaning,
        "quality": quality,
        "interpretation": {
            "note": "结果由确定性脚本生成；p 值含义为双侧检验；效应量与 CI 与检验方法一致。",
            "limitations": list(ctx.degradations),
            "pending_human": [],
        },
        "result": {
            "tables_analyzed": list(ctx.analysis.keys()),
            "cleaning": cleaning,
            "descriptive": descriptive,
            "inferential": inferential,
            "effects": effects,
            "confidence_intervals": cis,
        },
    }

    # Envelope fields
    result["result"] = computed["result"]
    result["descriptive"] = descriptive
    result["inferential"] = inferential
    result["effects"] = effects
    result["quality"] = quality
    result["interpretation"] = computed["interpretation"]

    # Fill output_contract fields with actual computed values when the key
    # semantically matches; unknown contract keys keep their echoed default.
    result = fill_contract_values(result, ctx.contract, computed)

    result["sources"] = [{"source_id": f["source_id"], "path": f["path"],
                          "kind": f["kind"], "rel_path": f["rel_path"]} for f in ctx.files
                         if not f.get("oversize")]
    result["uncertainty"] = list(set(result.get("uncertainty", []) + [
        "统计假设仅由脚本内置近似检验，建议在 scipy 可用时交叉核对 shapiro/levene 等。",
    ]))
    result["pending_human"] = [
        "字段单位（μg/mg、cm/mm）需依据数据字典人工确认；单位不明处标 unit_unknown。",
        "配对/分组设计为启发式识别，需与 task.md 任务说明人工核对。",
    ]
    result["errors"] = list(ctx.errors)
    result["degradations"] = list(ctx.degradations)
    result["change_log"] = change_log
    result["runtime"] = {"elapsed_seconds": round(time.time() - ctx.started, 2),
                         "command": "build", "deterministic": True}

    write_json(os.path.join(ctx.output_dir, "result.json"), result)
    return result


def cmd_selfcheck(ctx):
    problems = []
    result_path = os.path.join(ctx.output_dir, "result.json")
    if not os.path.isfile(result_path):
        problems.append("missing result.json")
    else:
        obj, err = read_json_safe(result_path)
        if obj is None:
            problems.append("result.json unparseable: %s" % err)
        else:
            if not obj.get("case_id"):
                problems.append("missing case_id")
            if "status" not in obj:
                problems.append("missing status")
            for key in ("sources", "uncertainty", "change_log"):
                if key not in obj:
                    problems.append("missing envelope key %s" % key)
    for f in ctx.files:
        if not f.get("oversize"):
            continue
    # SVG validity
    figures_dir = os.path.join(ctx.output_dir, "figures")
    if os.path.isdir(figures_dir):
        for name in sorted(os.listdir(figures_dir)):
            if name.endswith(".svg"):
                fp = os.path.join(figures_dir, name)
                try:
                    ET.parse(fp)
                except ET.ParseError as e:
                    problems.append("invalid svg %s: %s" % (name, e))
    quality_path = os.path.join(ctx.output_dir, "quality_checks.json")
    if not os.path.isfile(quality_path):
        problems.append("missing quality_checks.json")
    print(json.dumps({"ok": not problems, "problems": problems}, ensure_ascii=False, indent=2))
    return not problems


def cmd_selftest(_ctx):
    return statlib.selftest()


# ---------------------------------------------------------------------------
# Top-level run (guarded so it never crashes the harness)
# ---------------------------------------------------------------------------

COMMANDS = {
    "init": cmd_init,
    "survey": cmd_survey,
    "inspect": cmd_inspect,
    "analyze": cmd_analyze,
    "build": cmd_build,
    "selfcheck": cmd_selfcheck,
    "selftest": cmd_selftest,
}


def run(input_dir, output_dir, work_dir, command):
    ctx = Context(input_dir, output_dir, work_dir)
    try:
        fn = COMMANDS.get(command, cmd_init)
        fn(ctx)
        return 0
    except BaseException as e:  # noqa: BLE001 - deliberate catch-all for robustness
        # Guarantee a legal minimal result.json even on catastrophic failure.
        ctx.errors.append("unhandled_%s: %s" % (command, e))
        ctx.errors.append(traceback.format_exc(limit=3))
        try:
            ensure_dirs(ctx)
            discover(ctx)
            load_case(ctx)
        except BaseException:
            pass
        skeleton = build_skeleton(ctx.case_id, ctx.contract, STATUS_PARTIAL,
                                  "script degraded to fallback after an error")
        skeleton["errors"] = list(ctx.errors)
        skeleton["degradations"] = list(ctx.degradations)
        skeleton["uncertainty"] = ["pipeline raised an exception; fallback partial result written"]
        skeleton["runtime"] = {"elapsed_seconds": round(time.time() - ctx.started, 2),
                               "command": command, "crashed": True}
        try:
            write_json(os.path.join(output_dir, "result.json"), skeleton)
        except BaseException:
            pass
        return 0  # never propagate: always a legal product and exit code 0
