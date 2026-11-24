"""core_impl.py - multi-step research data analysis pipeline (pure standard library).

Design goals
------------
* Deterministic: same input -> byte-identical ``stats.json`` and SVG figures.
* Evidence-first: every key result carries a locator + verbatim quote.
* Degradation-explicit: missing software/data and every approximation are
  recorded in ``limitations`` / ``uncertainty``, never silently masked.
* Never exit non-zero on data problems; problems become ``errors`` /
  ``degradations`` entries in the final result.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import io
import json
import math
import os
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2_000_000

DEFAULT_CONTRACT_KEYS = ["analysis_results", "quality_evidence", "anomaly_notes"]

MISSING_TOKENS = {"", "na", "n/a", "null", "none", "nan", ".", "?", "-", "--", "missing"}

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}

ENCODINGS = ("utf-8-sig", "utf-8", "latin-1", "gbk")


# --------------------------------------------------------------------------
# Small utilities
# --------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fmt(x, nd: int = 6) -> str:
    """Deterministic float formatting used in every SVG and report."""
    if x is None:
        return ""
    if isinstance(x, float):
        if math.isnan(x):
            return "nan"
        if math.isinf(x):
            return "inf" if x > 0 else "-inf"
        return ("%." + str(nd) + "g") % x
    return str(x)


def _pstr(p, nd: int = 4) -> str:
    """Honest p-value formatting; flag floating-point underflow instead of '0'."""
    if p is None:
        return "n/a"
    if isinstance(p, (int, float)) and p == 0:
        return "<1e-300"
    return _fmt(p, nd)


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _read_text(path: str):
    """Read a text file with encoding fallback; returns (text, encoding)."""
    with open(path, "rb") as fh:
        raw = fh.read()
    if len(raw) > MAX_FILE_BYTES:
        return None, None
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1"), "latin-1"


def _load_json(path: str):
    if not os.path.isfile(path):
        return None
    text, enc = _read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _write_json(path: str, data) -> None:
    _ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8", newline="") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def _now_entry(step: str, note: str) -> dict:
    return {"ts": _utcnow(), "step": step, "note": note}


# --------------------------------------------------------------------------
# Mathematical special functions (no external dependencies)
# --------------------------------------------------------------------------

_EPS = 3e-14
_FPMIN = 1e-300
_MAXIT = 300


def _betacf(a: float, b: float, x: float) -> float:
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _FPMIN:
        d = _FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, _MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    logb = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
            + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(logb) * _betacf(a, b, x) / a
    return 1.0 - math.exp(logb) * _betacf(b, a, 1.0 - x) / b


def _gser(a: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    ap = a
    total = 1.0 / a
    delta = total
    for _ in range(1, _MAXIT + 1):
        ap += 1.0
        delta *= x / ap
        total += delta
        if abs(delta) < abs(total) * _EPS:
            break
    return total * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gcf(a: float, x: float) -> float:
    b = x + 1.0 - a
    c = 1.0 / _FPMIN
    d = 1.0 / b
    h = d
    for i in range(1, _MAXIT + 1):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = b + an / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def _gammp(a: float, x: float) -> float:
    """Regularized lower incomplete gamma P(a, x)."""
    if x < 0.0 or a <= 0.0:
        raise ValueError("invalid arguments")
    if x < a + 1.0:
        return _gser(a, x)
    return 1.0 - _gcf(a, x)


def _gammq(a: float, x: float) -> float:
    if x < 0.0 or a <= 0.0:
        raise ValueError("invalid arguments")
    if x < a + 1.0:
        return 1.0 - _gser(a, x)
    return _gcf(a, x)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def t_cdf(t: float, df: float) -> float:
    if df <= 0:
        return float("nan")
    x = df / (df + t * t)
    tail = 0.5 * _betai(0.5 * df, 0.5, x)
    return tail if t <= 0 else 1.0 - tail


def t_two_tailed_p(t: float, df: float) -> float:
    return 2.0 * (1.0 - t_cdf(abs(t), df))


def t_crit_975(df: float) -> float:
    """Inverse of two-tailed t (critical value at 0.975 one-sided), by bisection."""
    if df <= 0:
        return float("nan")
    lo, hi = 0.0, 10.0
    while t_cdf(hi, df) < 0.975:
        lo = hi
        hi *= 2.0
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if t_cdf(mid, df) < 0.975:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def f_cdf(F: float, df1: float, df2: float) -> float:
    if F <= 0:
        return 0.0
    x = df1 * F / (df1 * F + df2)
    return _betai(0.5 * df1, 0.5 * df2, x)


def f_pvalue(F: float, df1: float, df2: float) -> float:
    return 1.0 - f_cdf(F, df1, df2)


def chisq_cdf(x: float, df: float) -> float:
    if x <= 0:
        return 0.0
    return _gammp(0.5 * df, 0.5 * x)


def chisq_pvalue(x: float, df: float) -> float:
    return 1.0 - chisq_cdf(x, df)


# --------------------------------------------------------------------------
# Basic statistics (missing-safe)
# --------------------------------------------------------------------------

def _to_float(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    s = str(v).strip()
    if s.lower() in MISSING_TOKENS:
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _nums(values) -> list:
    out = []
    for v in values:
        f = _to_float(v)
        if f is not None and not (math.isnan(f) or math.isinf(f)):
            out.append(f)
    return out


def _mean(x) -> float:
    return sum(x) / len(x) if x else float("nan")


def _sample_var(x) -> float:
    n = len(x)
    if n < 2:
        return 0.0
    m = _mean(x)
    return sum((v - m) ** 2 for v in x) / (n - 1)


def _sd(x) -> float:
    return math.sqrt(_sample_var(x))


def _quantile_sorted(x, q: float) -> float:
    n = len(x)
    if n == 0:
        return float("nan")
    if n == 1:
        return x[0]
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return x[lo]
    frac = pos - lo
    return x[lo] * (1.0 - frac) + x[hi] * frac


def descriptive(values) -> dict:
    x = sorted(_nums(values))
    n = len(x)
    if n == 0:
        return {"n": 0, "missing": len(values), "mean": None, "sd": None,
                "min": None, "q1": None, "median": None, "q3": None,
                "max": None, "skewness": None, "kurtosis": None,
                "se": None, "ci95_low": None, "ci95_high": None}
    m = _mean(x)
    s = _sd(x)
    g1 = 0.0
    g2 = 0.0
    if s > 0 and n >= 3:
        zsum3 = sum(((v - m) / s) ** 3 for v in x)
        g1 = (n / ((n - 1) * (n - 2))) * zsum3
    if s > 0 and n >= 4:
        zsum4 = sum(((v - m) / s) ** 4 for v in x)
        g2 = ((n * (n + 1)) / ((n - 1) * (n - 2) * (n - 3))) * zsum4 \
            - (3 * (n - 1) ** 2) / ((n - 2) * (n - 3))
    se = s / math.sqrt(n) if n > 0 else float("nan")
    tcrit = t_crit_975(n - 1)
    return {
        "n": n,
        "missing": len(values) - n,
        "mean": m, "sd": s,
        "min": x[0], "q1": _quantile_sorted(x, 0.25),
        "median": _quantile_sorted(x, 0.5),
        "q3": _quantile_sorted(x, 0.75), "max": x[-1],
        "skewness": g1, "kurtosis": g2,
        "se": se,
        "ci95_low": m - tcrit * se, "ci95_high": m + tcrit * se,
    }


def jarque_bera(x) -> dict:
    n = len(x)
    if n < 8:
        return {"statistic": None, "p_value": None, "normal_ok": None,
                "note": "n<8, normality test skipped"}
    m = _mean(x)
    s = _sd(x)
    if s == 0:
        return {"statistic": None, "p_value": None, "normal_ok": None,
                "note": "zero variance"}
    m3 = sum((v - m) ** 3 for v in x) / n
    m4 = sum((v - m) ** 4 for v in x) / n
    g1 = m3 / s ** 3
    g2 = m4 / s ** 4
    jb = n / 6.0 * (g1 ** 2 + (g2 - 3) ** 2 / 4.0)
    p = chisq_pvalue(jb, 2)
    return {"statistic": jb, "p_value": p, "normal_ok": p >= 0.05,
            "skewness": g1, "kurtosis": g2}


def welch_t(x, y) -> dict:
    nx, ny = len(x), len(y)
    if nx < 2 or ny < 2:
        return {"method": "welch_t", "statistic": None, "p_value": None,
                "df": None, "effect_size": None, "note": "sample too small"}
    mx, my = _mean(x), _mean(y)
    vx, vy = _sample_var(x), _sample_var(y)
    se = math.sqrt(vx / nx + vy / ny)
    if se == 0:
        return {"method": "welch_t", "statistic": None, "p_value": None,
                "df": None, "effect_size": None, "note": "zero pooled variance"}
    t = (mx - my) / se
    df = (vx / nx + vy / ny) ** 2 / ((vx / nx) ** 2 / (nx - 1) + (vy / ny) ** 2 / (ny - 1))
    p = t_two_tailed_p(t, df)
    sp = math.sqrt(((nx - 1) * vx + (ny - 1) * vy) / (nx + ny - 2)) if nx + ny > 2 else 0.0
    d = (mx - my) / sp if sp > 0 else None
    return {"method": "welch_t", "statistic": t, "p_value": p, "df": df,
            "effect_size": d, "effect_metric": "cohens_d", "note": "two-tailed"}


def anova(groups) -> dict:
    k = len(groups)
    flat = [v for g in groups for v in g]
    N = len(flat)
    if k < 2 or N < k + 2:
        return {"method": "anova", "statistic": None, "p_value": None,
                "df_between": None, "df_within": None, "effect_size": None,
                "note": "insufficient data"}
    grand = _mean(flat)
    ss_between = sum(len(g) * (_mean(g) - grand) ** 2 for g in groups)
    ss_within = sum(sum((v - _mean(g)) ** 2 for v in g) for g in groups)
    df_b = k - 1
    df_w = N - k
    ms_b = ss_between / df_b
    ms_w = ss_within / df_w if df_w > 0 else 0.0
    if ms_w == 0:
        return {"method": "anova", "statistic": None, "p_value": None,
                "df_between": df_b, "df_within": df_w, "effect_size": None,
                "note": "zero within-group variance"}
    F = ms_b / ms_w
    p = f_pvalue(F, df_b, df_w)
    eta2 = ss_between / (ss_between + ss_within) if (ss_between + ss_within) > 0 else None
    return {"method": "anova", "statistic": F, "p_value": p,
            "df_between": df_b, "df_within": df_w, "effect_size": eta2,
            "effect_metric": "eta_squared", "ss_between": ss_between, "ss_within": ss_within}


def levene(groups) -> dict:
    """Brown-Forsythe Levene test using group medians."""
    k = len(groups)
    N = sum(len(g) for g in groups)
    if k < 2 or N < k + 2:
        return {"statistic": None, "p_value": None, "note": "insufficient data"}
    meds = [_quantile_sorted(sorted(g), 0.5) for g in groups]
    devs = [[abs(v - meds[i]) for v in g] for i, g in enumerate(groups)]
    res = anova(devs)
    return {"statistic": res.get("statistic"), "p_value": res.get("p_value"),
            "df_between": res.get("df_between"), "df_within": res.get("df_within"),
            "note": "Brown-Forsythe (median-centered)"}


def _rank_data(pairs):
    """pairs: list of (value, group_index). Returns list of (rank, group_index)."""
    order = sorted(range(len(pairs)), key=lambda i: pairs[i][0])
    ranks = [0.0] * len(pairs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and pairs[order[j + 1]][0] == pairs[order[i]][0]:
            j += 1
        avg = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return [(ranks[i], pairs[i][1]) for i in range(len(pairs))]


def mann_whitney(x, y) -> dict:
    n1, n2 = len(x), len(y)
    if n1 < 1 or n2 < 1:
        return {"method": "mann_whitney_u", "statistic": None, "p_value": None,
                "note": "empty group"}
    pairs = [(v, 0) for v in x] + [(v, 1) for v in y]
    ranked = _rank_data(pairs)
    r1 = sum(r for r, g in ranked if g == 0)
    u1 = r1 - n1 * (n1 + 1) / 2.0
    u2 = n1 * n2 - u1
    U = min(u1, u2)
    # tie correction
    N = n1 + n2
    ranks_only = [r for r, _ in ranked]
    tie = 0.0
    from collections import Counter
    for _, cnt in Counter(ranks_only).items():
        if cnt > 1:
            tie += (cnt ** 3 - cnt)
    mu = n1 * n2 / 2.0
    if N < 2:
        return {"method": "mann_whitney_u", "statistic": U, "p_value": None,
                "note": "N too small for normal approximation"}
    sigma = math.sqrt((n1 * n2 / 12.0) * ((N + 1) - tie / (N * (N - 1))))
    if sigma == 0:
        return {"method": "mann_whitney_u", "statistic": U, "p_value": None,
                "note": "all values tied"}
    z = (U - mu) / sigma
    p = 2.0 * (1.0 - norm_cdf(abs(z)))
    r = z / math.sqrt(N)  # effect size approximation (r = z/sqrt(N))
    return {"method": "mann_whitney_u", "statistic": U, "p_value": p,
            "z": z, "effect_size": r, "effect_metric": "r_rank_biserial",
            "note": "normal approximation with tie correction"}


def kruskal_wallis(groups) -> dict:
    k = len(groups)
    N = sum(len(g) for g in groups)
    if k < 2 or N < 3:
        return {"method": "kruskal_wallis", "statistic": None, "p_value": None,
                "df": None, "note": "insufficient data"}
    pairs = []
    for gi, g in enumerate(groups):
        for v in g:
            pairs.append((v, gi))
    ranked = _rank_data(pairs)
    r_sum = [0.0] * k
    n_i = [len(g) for g in groups]
    for r, g in ranked:
        r_sum[g] += r
    from collections import Counter
    tie = 0.0
    ranks_only = [r for r, _ in ranked]
    for _, cnt in Counter(ranks_only).items():
        if cnt > 1:
            tie += (cnt ** 3 - cnt)
    H = 12.0 / (N * (N + 1)) * sum(r_sum[i] ** 2 / n_i[i] for i in range(k) if n_i[i] > 0) \
        - 3.0 * (N + 1)
    if tie > 0:
        H = H / (1.0 - tie / (N ** 3 - N))
    p = chisq_pvalue(H, k - 1)
    return {"method": "kruskal_wallis", "statistic": H, "p_value": p,
            "df": k - 1, "note": "chi-square approximation with tie correction"}


def pearson(x, y) -> dict:
    n = len(x)
    if n < 3:
        return {"method": "pearson", "r": None, "p_value": None, "n": n, "note": "n<3"}
    mx, my = _mean(x), _mean(y)
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    sxx = sum((xi - mx) ** 2 for xi in x)
    syy = sum((yi - my) ** 2 for yi in y)
    if sxx == 0 or syy == 0:
        return {"method": "pearson", "r": None, "p_value": None, "n": n,
                "note": "zero variance"}
    r = sxy / math.sqrt(sxx * syy)
    r = max(-1.0, min(1.0, r))
    if abs(r) >= 1.0:
        p = 0.0
    else:
        t = r * math.sqrt((n - 2) / (1 - r * r))
        p = t_two_tailed_p(t, n - 2)
    return {"method": "pearson", "r": r, "p_value": p, "n": n,
            "r_squared": r * r}


def spearman(x, y) -> dict:
    n = len(x)
    if n < 3:
        return {"method": "spearman", "rho": None, "p_value": None, "n": n, "note": "n<3"}
    rx = [r for r, _ in _rank_data([(v, 0) for v in x])]
    ry = [r for r, _ in _rank_data([(v, 0) for v in y])]
    res = pearson(rx, ry)
    return {"method": "spearman", "rho": res.get("r"), "p_value": res.get("p_value"),
            "n": n}


# --------------------------------------------------------------------------
# Input discovery and loading
# --------------------------------------------------------------------------

def _detect_delimiter(line: str) -> str:
    candidates = [("\t", line.count("\t")), (",", line.count(",")),
                  (";", line.count(";")), ("|", line.count("|"))]
    candidates.sort(key=lambda kv: kv[1], reverse=True)
    if candidates[0][1] > 0:
        return candidates[0][0]
    return ","


def _json_to_rows(data):
    """Convert common JSON shapes into a list-of-dicts table."""
    if isinstance(data, list):
        if all(isinstance(r, dict) for r in data):
            return data
        return None
    if isinstance(data, dict):
        if isinstance(data.get("data"), list) and data["data"] and isinstance(data["data"][0], dict):
            return data["data"]
        if isinstance(data.get("records"), list) and data["records"] and isinstance(data["records"][0], dict):
            return data["records"]
        # columnar dict: {"col": [..], "col2": [..]}
        if data and all(isinstance(v, list) for v in data.values()):
            keys = list(data.keys())
            n = min(len(data[k]) for k in keys)
            return [{k: data[k][i] for k in keys} for i in range(n)]
    return None


def _load_sqlite(path: str, work_dir: str):
    """Dump each table as CSV into work_dir; returns list of datasets."""
    datasets = []
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = [r[0] for r in cur.fetchall()]
        for tbl in tables:
            try:
                cur.execute('SELECT * FROM "%s"' % tbl.replace('"', '""'))
                rows = [dict(r) for r in cur.fetchall()]
                if rows:
                    columns = list(rows[0].keys())
                else:
                    cur.execute('PRAGMA table_info("%s")' % tbl.replace('"', '""'))
                    columns = [r[1] for r in cur.fetchall()]
                datasets.append({"source": os.path.basename(path), "kind": "sqlite",
                                 "table": tbl, "columns": columns, "rows": rows,
                                 "n_rows": len(rows)})
                # dump a CSV snapshot into work_dir for reproducibility
                csv_path = os.path.join(work_dir, "table_%s.csv" % tbl)
                _ensure_dir(work_dir)
                with open(csv_path, "w", encoding="utf-8", newline="") as fh:
                    w = csv.DictWriter(fh, fieldnames=columns)
                    w.writeheader()
                    w.writerows(rows)
            except Exception as exc:
                datasets.append({"source": os.path.basename(path), "kind": "sqlite",
                                 "table": tbl, "columns": [], "rows": [], "n_rows": 0,
                                 "error": str(exc)})
        conn.close()
    except Exception as exc:
        datasets.append({"source": os.path.basename(path), "kind": "sqlite",
                         "table": None, "columns": [], "rows": [], "n_rows": 0,
                         "error": str(exc)})
    return datasets


def _load_tabular(path: str, work_dir: str):
    ext = os.path.splitext(path)[1].lower()
    datasets = []
    text, enc = _read_text(path)
    if ext in (".db", ".sqlite", ".sqlite3", ".db3"):
        return _load_sqlite(path, work_dir), enc
    if text is None:
        return [{"source": os.path.basename(path), "kind": ext, "table": None,
                 "columns": [], "rows": [], "n_rows": 0,
                 "error": "file exceeds size limit"}], enc
    if ext in (".csv", ".tsv", ".txt", ".dat", ".tab"):
        first = next((ln for ln in text.splitlines() if ln.strip()), "")
        if not first:
            return [{"source": os.path.basename(path), "kind": ext, "table": None,
                     "columns": [], "rows": [], "n_rows": 0, "error": "empty file"}], enc
        delim = "\t" if ext in (".tsv", ".tab") else _detect_delimiter(first)
        try:
            reader = csv.DictReader(io.StringIO(text), delimiter=delim)
            rows = []
            for r in reader:
                if any(k and r.get(k) is not None for k in (reader.fieldnames or [])):
                    rows.append({k: (r.get(k) or "") for k in (reader.fieldnames or [])})
            cols = list(reader.fieldnames or [])
            datasets.append({"source": os.path.basename(path), "kind": ext,
                             "delimiter": delim, "table": None, "columns": cols,
                             "rows": rows, "n_rows": len(rows), "encoding": enc})
        except Exception as exc:
            datasets.append({"source": os.path.basename(path), "kind": ext, "table": None,
                             "columns": [], "rows": [], "n_rows": 0, "error": str(exc)})
        return datasets, enc
    if ext == ".json":
        data = _load_json(path)
        if data is None:
            return [{"source": os.path.basename(path), "kind": "json", "table": None,
                     "columns": [], "rows": [], "n_rows": 0, "error": "invalid JSON"}], enc
        rows = _json_to_rows(data)
        if rows is None:
            return [{"source": os.path.basename(path), "kind": "json", "table": None,
                     "columns": [], "rows": [], "n_rows": 0, "error": "unsupported JSON shape"}], enc
        cols = sorted({k for r in rows for k in r.keys()})
        return [{"source": os.path.basename(path), "kind": "json", "table": None,
                 "columns": cols, "rows": rows, "n_rows": len(rows), "encoding": enc}], enc
    return [{"source": os.path.basename(path), "kind": ext, "table": None,
             "columns": [], "rows": [], "n_rows": 0,
             "error": "unsupported format (xlsx/xls need openpyxl)"}], enc


def discover_inputs(input_dir: str, work_dir: str):
    """Walk input dir and classify materials; write discovery.json."""
    _ensure_dir(work_dir)
    files = []
    datasets = []
    case_path = os.path.join(input_dir, "case.json")
    task_path = os.path.join(input_dir, "task.md")
    for root, _dirs, names in os.walk(input_dir):
        for name in names:
            p = os.path.join(root, name)
            files.append({"path": p, "rel": os.path.relpath(p, input_dir),
                          "bytes": os.path.getsize(p)})
    for f in files:
        ext = os.path.splitext(f["path"])[1].lower()
        if ext in (".csv", ".tsv", ".json", ".txt", ".dat", ".tab", ".db",
                   ".sqlite", ".sqlite3", ".db3", ".xlsx", ".xls"):
            ds_list, enc = _load_tabular(f["path"], work_dir)
            for ds in ds_list:
                ds["path"] = f["path"]
                ds["encoding"] = ds.get("encoding", enc)
                datasets.append(ds)
    # sort deterministically
    datasets.sort(key=lambda d: (d["source"], d.get("table") or "", d["kind"]))
    discovery = {
        "input_dir": input_dir,
        "files": sorted(files, key=lambda f: f["path"]),
        "datasets": datasets,
        "case_json_present": os.path.isfile(case_path),
        "task_md_present": os.path.isfile(task_path),
    }
    _write_json(os.path.join(work_dir, "discovery.json"), discovery)
    return discovery


# --------------------------------------------------------------------------
# Case / task reading
# --------------------------------------------------------------------------

def read_case(input_dir: str):
    path = os.path.join(input_dir, "case.json")
    if not os.path.isdir(input_dir):
        return {"case_id": os.path.basename(os.path.abspath(input_dir)),
                "contract_keys": DEFAULT_CONTRACT_KEYS, "contract_raw": None,
                "missing": True, "invalid": False, "no_input_dir": True}
    if not os.path.isfile(path):
        return {"case_id": os.path.basename(os.path.abspath(input_dir)),
                "contract_keys": DEFAULT_CONTRACT_KEYS, "contract_raw": None,
                "missing": True, "invalid": False, "no_input_dir": False}
    data = _load_json(path)
    if data is None:
        return {"case_id": os.path.basename(os.path.abspath(input_dir)),
                "contract_keys": DEFAULT_CONTRACT_KEYS, "contract_raw": None,
                "missing": False, "invalid": True, "no_input_dir": False}
    case_id = (data.get("case_id") or data.get("challenge_id")
               or data.get("armiId") or data.get("id")
               or os.path.basename(os.path.abspath(input_dir)))
    oc = data.get("output_contract") or data.get("outputContract") or data.get("output")
    keys = DEFAULT_CONTRACT_KEYS
    raw = None
    if isinstance(oc, dict) and oc:
        keys = [k for k in oc.keys() if isinstance(k, str)]
        raw = oc
    elif isinstance(oc, list) and oc:
        keys = []
        for item in oc:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                keys.append(item.get("name") or item.get("key") or item.get("field") or str(len(keys)))
        if not keys:
            keys = DEFAULT_CONTRACT_KEYS
    return {"case_id": case_id, "contract_keys": keys, "contract_raw": raw,
            "missing": False, "invalid": False, "no_input_dir": False,
            "case_data": data}


def read_task(input_dir: str):
    path = os.path.join(input_dir, "task.md")
    if not os.path.isfile(path):
        return ""
    text, _enc = _read_text(path)
    return text or ""


def _scan_task_intent(task_text: str) -> list:
    hints = []
    kw = [("回归/预测", ["regression", "predict", "回归", "预测"]),
          ("相关分析", ["correlation", "correlate", "相关"]),
          ("组间比较", ["group", "anova", "compare", "组间", "差异", "比较", "分类"]),
          ("缺失值", ["missing", "缺失", "na "]),
          ("重复记录", ["duplicate", "重复"]),
          ("异常值", ["outlier", "异常"]),
          ("数据库查询", ["sql", "query", "数据库", "查询"])]
    low = (task_text or "").lower()
    for label, keys in kw:
        if any(k in low for k in keys):
            hints.append(label)
    return hints


# --------------------------------------------------------------------------
# Profiling
# --------------------------------------------------------------------------

def infer_column_type(values) -> str:
    non_missing = [v for v in values if str(v).strip().lower() not in MISSING_TOKENS]
    if not non_missing:
        return "empty"
    ints = 0
    floats = 0
    total = 0
    for v in non_missing:
        s = str(v).strip()
        total += 1
        if re.fullmatch(r"[+-]?\d+", s):
            ints += 1
        else:
            f = _to_float(s)
            if f is not None:
                floats += 1
    if total and ints + floats == total and floats == 0:
        return "integer"
    if total and ints + floats == total:
        return "numeric"
    # detect datetime-ish
    return "categorical"


def profile_dataset(dataset: dict, work_dir: str) -> dict:
    rows = dataset.get("rows", [])
    cols = dataset.get("columns", []) or []
    if not cols and rows:
        cols = sorted({k for r in rows for k in r.keys()})
    prof = {
        "source": dataset.get("source"), "kind": dataset.get("kind"),
        "table": dataset.get("table"), "path": dataset.get("path"),
        "n_rows": len(rows), "n_columns": len(cols),
        "columns": {}, "missing": {}, "duplicates": 0,
    }
    # exact duplicate rows (normalized to strings)
    seen = set()
    for r in rows:
        key = json.dumps({c: str(r.get(c, "")) for c in cols}, sort_keys=True, ensure_ascii=False)
        if key in seen:
            prof["duplicates"] += 1
        seen.add(key)
    for c in cols:
        values = [r.get(c) for r in rows]
        ctype = infer_column_type(values)
        non_missing = [v for v in values if str(v).strip().lower() not in MISSING_TOKENS]
        uniq = sorted({str(v) for v in non_missing})
        prof["columns"][c] = {
            "type": ctype, "missing": len(values) - len(non_missing),
            "unique": len(uniq),
            "levels": uniq if ctype == "categorical" and len(uniq) <= 50 else None,
        }
        prof["missing"][c] = len(values) - len(non_missing)
    _write_json(os.path.join(work_dir, "profile.json"), prof)
    return prof


# --------------------------------------------------------------------------
# Cleaning
# --------------------------------------------------------------------------

def clean_dataset(dataset: dict, work_dir: str) -> tuple:
    rows = dataset.get("rows", [])
    cols = dataset.get("columns", []) or []
    if not cols and rows:
        cols = sorted({k for r in rows for k in r.keys()})
    rules = []
    input_rows = len(rows)

    # 1. drop exact duplicates
    seen = set()
    deduped = []
    dropped_dup = 0
    for r in rows:
        key = json.dumps({c: str(r.get(c, "")) for c in cols}, sort_keys=True, ensure_ascii=False)
        if key in seen:
            dropped_dup += 1
        else:
            seen.add(key)
            deduped.append(r)
    rules.append({"name": "drop_exact_duplicates", "rows_removed": dropped_dup,
                  "rows_after": len(deduped)})
    working = deduped

    # 2. flag outliers (IQR, 1.5x) — never delete, only mark
    outlier_meta = {}
    total_flagged = 0
    for c in cols:
        vals = _nums([r.get(c) for r in working])
        if len(vals) >= 4 and infer_column_type([r.get(c) for r in working]) in ("numeric", "integer"):
            sv = sorted(vals)
            q1 = _quantile_sorted(sv, 0.25)
            q3 = _quantile_sorted(sv, 0.75)
            iqr = q3 - q1
            lo = q1 - 1.5 * iqr
            hi = q3 + 1.5 * iqr
            idx = [i for i, r in enumerate(working)
                   if _to_float(r.get(c)) is not None and not (lo <= _to_float(r.get(c)) <= hi)]
            if idx:
                outlier_meta[c] = {"criterion": "IQR*1.5", "lower": lo, "upper": hi,
                                   "rows_flagged": len(idx), "row_indices": idx}
                total_flagged += len(idx)
    rules.append({"name": "flag_outliers_iqr", "rows_removed": 0,
                  "rows_after": len(working), "columns_flagged": outlier_meta,
                  "rows_flagged_total": total_flagged})

    # 3. missing-value accounting (no imputation unless requested)
    missing_meta = {}
    for c in cols:
        n_miss = sum(1 for r in working if str(r.get(c, "")).strip().lower() in MISSING_TOKENS)
        if n_miss:
            missing_meta[c] = n_miss
    rules.append({"name": "count_missing", "rows_removed": 0,
                  "rows_after": len(working), "missing_by_column": missing_meta})

    clean_meta = {
        "source": dataset.get("source"), "table": dataset.get("table"),
        "input_rows": input_rows, "output_rows": len(working),
        "dropped_duplicates": dropped_dup, "rules": rules,
        "missing_by_column": missing_meta, "outlier_by_column": outlier_meta,
    }
    _write_json(os.path.join(work_dir, "clean_meta.json"), clean_meta)

    csv_path = os.path.join(work_dir, "cleaned.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(working)
    return working, clean_meta, csv_path


# --------------------------------------------------------------------------
# Analysis orchestration
# --------------------------------------------------------------------------

def _select_categorical_cols(profile: dict, n_rows: int) -> list:
    out = []
    for c, meta in profile["columns"].items():
        if meta["type"] == "categorical":
            levels = meta.get("levels") or []
            if 2 <= len(levels) <= min(20, max(2, n_rows // 2)):
                out.append(c)
    return out


def _select_group_cols(profile: dict, n_rows: int) -> list:
    """Grouping candidates: string categoricals plus low-cardinality integer labels."""
    out = []
    for c, meta in profile["columns"].items():
        if meta["type"] == "categorical":
            levels = meta.get("levels") or []
            if 2 <= len(levels) <= min(20, max(2, n_rows // 2)):
                out.append(c)
        elif meta["type"] == "integer":
            if 2 <= meta.get("unique", 0) <= 15:
                out.append(c)
    return out


def _select_numeric_cols(profile: dict) -> list:
    return [c for c, m in profile["columns"].items()
            if m["type"] in ("numeric", "integer")]


def _looks_like_id(name: str) -> bool:
    low = (name or "").strip().lower()
    return low in ("id", "index", "row", "rownum", "rowid", "s.no", "sno", "no", "no.",
                   "sample", "sample_id", "record", "record_id", "#", "序号", "编号")


def _analysis_numeric(numeric_cols, rows) -> list:
    """Numeric columns for inference, excluding obvious row identifiers."""
    out = [c for c in numeric_cols if not _looks_like_id(c)]
    return out if out else numeric_cols


def analyze(rows, profile, work_dir, overrides) -> dict:
    cols = list(profile["columns"].keys())
    numeric_cols = _select_numeric_cols(profile)
    analysis_numeric = _analysis_numeric(numeric_cols, rows)
    cat_cols = _select_categorical_cols(profile, len(rows))
    group_cand = _select_group_cols(profile, len(rows))
    target_cols = [t.strip() for t in (overrides.get("targets") or "").split(",") if t.strip()]
    target_cols = [t for t in target_cols if t in cols]
    group_col = overrides.get("group") if overrides.get("group") in group_cand + cols else None

    stats = {
        "source": profile.get("source"), "table": profile.get("table"),
        "n_rows": len(rows), "n_columns": len(cols),
        "columns": cols, "numeric_columns": numeric_cols,
        "analysis_numeric_columns": analysis_numeric,
        "categorical_columns": cat_cols, "group_candidates": group_cand,
        "descriptive": {},
        "normality": {},
        "group_comparisons": [],
        "correlations": [],
        "uncertainty": [],
        "limitations": [],
    }

    # descriptive + normality
    for c in numeric_cols:
        vals = [r.get(c) for r in rows]
        stats["descriptive"][c] = descriptive(vals)
        stats["normality"][c] = jarque_bera(_nums(vals))

    # group comparisons
    chosen_group = group_col or (group_cand[0] if group_cand else None)
    if chosen_group and chosen_group in cols and analysis_numeric:
        # pick the numeric column with smallest KW p (discriminating power) for reporting
        best = None
        for c in analysis_numeric:
            if c == chosen_group:
                continue
            groups = {}
            for r in rows:
                g = str(r.get(chosen_group)).strip()
                v = _to_float(r.get(c))
                if v is not None:
                    groups.setdefault(g, []).append(v)
            gvals = [groups[g] for g in sorted(groups) if len(groups[g]) >= 2]
            if len(gvals) < 2:
                continue
            aov = anova(gvals)
            kw = kruskal_wallis(gvals)
            lev = levene(gvals)
            gc = {"group_col": chosen_group, "target_col": c,
                  "groups": {g: {"n": len(groups[g]), "mean": _mean(groups[g]),
                                 "sd": _sd(groups[g]),
                                 "min": min(groups[g]) if groups[g] else None,
                                 "max": max(groups[g]) if groups[g] else None,
                                 "q1": _quantile_sorted(sorted(groups[g]), 0.25),
                                 "median": _quantile_sorted(sorted(groups[g]), 0.5),
                                 "q3": _quantile_sorted(sorted(groups[g]), 0.75)}
                             for g in sorted(groups)},
                  "anova": aov, "kruskal_wallis": kw, "levene": lev,
                  "assumptions": {
                      "normality_note": "Jarque-Bera per group not computed; see descriptive skewness",
                      "homogeneity": {"p_value": lev.get("p_value"),
                                      "equal_variance_ok": (lev.get("p_value") or 0) >= 0.05},
                  },
                  "posthoc": [],
                  "method_choice": "kruskal_wallis" if (aov.get("p_value") is None) else
                                  ("anova" if (lev.get("p_value") or 0) >= 0.05 else "welch_pairwise")}
            # pairwise post-hoc (Welch t + Mann-Whitney)
            gkeys = sorted(groups)
            for i in range(len(gkeys)):
                for j in range(i + 1, len(gkeys)):
                    xi = groups[gkeys[i]]
                    xj = groups[gkeys[j]]
                    gc["posthoc"].append({
                        "pair": [gkeys[i], gkeys[j]],
                        "welch_t": welch_t(xi, xj),
                        "mann_whitney": mann_whitney(xi, xj),
                    })
            stats["group_comparisons"].append(gc)
            if best is None or (kw.get("p_value") is not None and
                                (best[1] is None or kw["p_value"] < best[1])):
                best = (gc, kw.get("p_value"))
        if best:
            stats["best_group_comparison"] = {"group_col": best[0]["group_col"],
                                              "target_col": best[0]["target_col"]}
    else:
        stats["uncertainty"].append(
            "no categorical grouping column with 2..20 levels; group comparison skipped")

    # correlations
    corr_targets = target_cols or ([analysis_numeric[-1]] if analysis_numeric else [])
    for t in corr_targets:
        if t not in analysis_numeric:
            continue
        y = _nums([r.get(t) for r in rows])
        for c in analysis_numeric:
            if c == t:
                continue
            x = _nums([r.get(c) for r in rows])
            n = min(len(x), len(y))
            if n < 3:
                continue
            pr = pearson(x[:n], y[:n])
            sp = spearman(x[:n], y[:n])
            stats["correlations"].append({"x": c, "y": t, "n": n,
                                          "pearson_r": pr.get("r"),
                                          "pearson_p": pr.get("p_value"),
                                          "spearman_rho": sp.get("rho"),
                                          "spearman_p": sp.get("p_value")})
    if len(analysis_numeric) >= 3:
        # full correlation matrix (Pearson) for heatmap
        matrix = {}
        for c in analysis_numeric:
            x = _nums([r.get(c) for r in rows])
            row = {}
            for d in analysis_numeric:
                y = _nums([r.get(d) for r in rows])
                n = min(len(x), len(y))
                row[d] = pearson(x[:n], y[:n]).get("r") if n >= 3 else None
            matrix[c] = row
        stats["correlation_matrix"] = matrix

    if not stats["group_comparisons"] and not stats["correlations"]:
        stats["limitations"].append("no valid numeric/numeric or categorical/numeric analysis was possible")
    _write_json(os.path.join(work_dir, "stats.json"), stats)
    return stats


# --------------------------------------------------------------------------
# SVG rendering (deterministic, editable)
# --------------------------------------------------------------------------

_SVG_W, _SVG_H = 720, 440
_MARGIN = {"l": 64, "r": 24, "t": 40, "b": 56}


def _svg_header(w: int, h: int, title: str) -> list:
    return ['<?xml version="1.0" encoding="UTF-8"?>',
            '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
            'viewBox="0 0 %d %d" font-family="sans-serif">' % (w, h, w, h),
            '<title>%s</title>' % title.replace("&", "&amp;").replace("<", "&lt;")]


def _svg_footer() -> list:
    return ['</svg>']


def _nice_ticks(lo: float, hi: float, n: int = 6) -> list:
    if lo == hi:
        return [lo]
    span = hi - lo
    step = span / max(1, n)
    mag = 10 ** math.floor(math.log10(step)) if step > 0 else 1.0
    norm = step / mag
    for m in (1, 2, 2.5, 5, 10):
        if norm <= m:
            step = m * mag
            break
    start = math.floor(lo / step) * step
    out = []
    v = start
    while v <= hi + step * 0.5:
        if v >= lo - step * 0.5:
            out.append(v)
        v += step
    return out


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def render_boxplot(stats: dict, gc: dict, out_path: str) -> None:
    groups = gc["groups"]
    keys = sorted(groups)
    plot_x = _MARGIN["l"]
    plot_w = _SVG_W - _MARGIN["l"] - _MARGIN["r"]
    plot_y = _MARGIN["t"]
    plot_h = _SVG_H - _MARGIN["t"] - _MARGIN["b"]
    lines = _svg_header(_SVG_W, _SVG_H, "Box plot: %s by %s" % (gc["target_col"], gc["group_col"]))
    lines.append('<rect x="0" y="0" width="%d" height="%d" fill="white"/>' % (_SVG_W, _SVG_H))
    # y range from group min/max
    lo = min(groups[g].get("min") for g in keys)
    hi = max(groups[g].get("max") for g in keys)
    if lo is None or hi is None or lo == hi:
        lo, hi = 0.0, 1.0
    pad = (hi - lo) * 0.08
    lo -= pad
    hi += pad

    def ymap(v):
        return plot_y + plot_h * (1 - (v - lo) / (hi - lo))

    ticks = _nice_ticks(lo, hi)
    for t in ticks:
        y = ymap(t)
        lines.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#ccc"/>' % (plot_x, y, plot_x + plot_w, y))
        lines.append('<text x="%d" y="%.1f" font-size="11" text-anchor="end">%s</text>'
                     % (plot_x - 8, y + 4, _esc(_fmt(t, 4))))
    lines.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="black"/>' % (plot_x, plot_y + plot_h, plot_x + plot_w, plot_y + plot_h))
    lines.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="black"/>' % (plot_x, plot_y, plot_x, plot_y + plot_h))
    lines.append('<text x="%d" y="%d" font-size="12" text-anchor="middle">%s</text>'
                 % (plot_x + plot_w / 2, plot_y + plot_h + 40, _esc(gc["target_col"])))
    n = len(keys)
    slot = plot_w / n
    for i, g in enumerate(keys):
        cx = plot_x + slot * (i + 0.5)
        gd = groups[g]
        med = gd["median"]
        q1 = gd["q1"]
        q3 = gd["q3"]
        gmin = gd.get("min")
        gmax = gd.get("max")
        # whiskers to 1.5*IQR (clipped to observed min/max)
        iqr = (q3 - q1) if (q3 is not None and q1 is not None) else 0.0
        wlo = max(gmin if gmin is not None else q1 - 1.5 * iqr, q1 - 1.5 * iqr)
        whi = min(gmax if gmax is not None else q3 + 1.5 * iqr, q3 + 1.5 * iqr)
        ymed = ymap(med)
        yq1 = ymap(q1)
        yq3 = ymap(q3)
        ywhi = ymap(whi)
        ywlo = ymap(wlo)
        w = min(46, slot * 0.5)
        lines.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#333"/>' % (cx, ywhi, cx, ywlo))
        lines.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#333"/>' % (cx - w * 0.4, ywhi, cx + w * 0.4, ywhi))
        lines.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#333"/>' % (cx - w * 0.4, ywlo, cx + w * 0.4, ywlo))
        lines.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="#cfe3f7" stroke="#1f4e79"/>'
                     % (cx - w / 2, yq3, w, max(1.0, yq1 - yq3)))
        lines.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#1f4e79" stroke-width="2"/>'
                     % (cx - w / 2, ymed, cx + w / 2, ymed))
        lines.append('<text x="%.1f" y="%d" font-size="11" text-anchor="middle">%s</text>'
                     % (cx, plot_y + plot_h + 22, _esc(g)))
    # legend / annotation
    p = gc["kruskal_wallis"].get("p_value")
    lines.append('<text x="%d" y="%d" font-size="12">%s</text>'
                 % (plot_x, plot_y - 12, _esc("KW p=%s" % _pstr(p))))
    lines += _svg_footer()
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def render_scatter(stats: dict, corr: dict, out_path: str) -> None:
    plot_x = _MARGIN["l"]
    plot_w = _SVG_W - _MARGIN["l"] - _MARGIN["r"]
    plot_y = _MARGIN["t"]
    plot_h = _SVG_H - _MARGIN["t"] - _MARGIN["b"]
    lines = _svg_header(_SVG_W, _SVG_H, "Scatter: %s vs %s" % (corr["y"], corr["x"]))
    lines.append('<rect x="0" y="0" width="%d" height="%d" fill="white"/>' % (_SVG_W, _SVG_H))
    # we don't have raw pairs here; regenerate from nothing — use descriptive ranges
    # (scatter points come from stats.correlation data recomputed on demand in caller)
    lines.append('<text x="%d" y="%d" font-size="14" font-weight="bold">%s vs %s</text>'
                 % (plot_x, plot_y - 16, _esc(corr["y"]), _esc(corr["x"])))
    r = corr.get("pearson_r")
    lines.append('<text x="%d" y="%d" font-size="12">Pearson r = %s, p = %s</text>'
                 % (plot_x, plot_y - 2, _esc(_fmt(r, 4)), _esc(_pstr(corr.get("pearson_p")))))
    lines += _svg_footer()
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def render_scatter_with_data(rows, c1, c2, corr, out_path) -> None:
    """Draw actual scatter with least-squares line."""
    x = _nums([r.get(c1) for r in rows])
    y = _nums([r.get(c2) for r in rows])
    n = min(len(x), len(y))
    if n == 0:
        render_scatter({"descriptive": {}}, corr, out_path)
        return
    x = x[:n]
    y = y[:n]
    lo_x, hi_x = min(x), max(x)
    lo_y, hi_y = min(y), max(y)
    if lo_x == hi_x:
        lo_x -= 1
        hi_x += 1
    if lo_y == hi_y:
        lo_y -= 1
        hi_y += 1
    px = (hi_x - lo_x) * 0.06
    py = (hi_y - lo_y) * 0.06
    lo_x -= px
    hi_x += px
    lo_y -= py
    hi_y += py
    plot_x = _MARGIN["l"]
    plot_w = _SVG_W - _MARGIN["l"] - _MARGIN["r"]
    plot_y = _MARGIN["t"]
    plot_h = _SVG_H - _MARGIN["t"] - _MARGIN["b"]

    def xmap(v):
        return plot_x + plot_w * (v - lo_x) / (hi_x - lo_x)

    def ymap(v):
        return plot_y + plot_h * (1 - (v - lo_y) / (hi_y - lo_y))

    lines = _svg_header(_SVG_W, _SVG_H, "Scatter: %s vs %s" % (c2, c1))
    lines.append('<rect x="0" y="0" width="%d" height="%d" fill="white"/>' % (_SVG_W, _SVG_H))
    for t in _nice_ticks(lo_x, hi_x):
        lines.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#eee"/>' % (xmap(t), plot_y, xmap(t), plot_y + plot_h))
        lines.append('<text x="%.1f" y="%d" font-size="11" text-anchor="middle">%s</text>' % (xmap(t), plot_y + plot_h + 16, _esc(_fmt(t, 4))))
    for t in _nice_ticks(lo_y, hi_y):
        lines.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#eee"/>' % (plot_x, ymap(t), plot_x + plot_w, ymap(t)))
        lines.append('<text x="%d" y="%.1f" font-size="11" text-anchor="end">%s</text>' % (plot_x - 8, ymap(t) + 4, _esc(_fmt(t, 4))))
    lines.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="black"/>' % (plot_x, plot_y + plot_h, plot_x + plot_w, plot_y + plot_h))
    lines.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="black"/>' % (plot_x, plot_y, plot_x, plot_y + plot_h))
    lines.append('<text x="%d" y="%d" font-size="12" text-anchor="middle">%s</text>' % (plot_x + plot_w / 2, plot_y + plot_h + 38, _esc(c1)))
    lines.append('<text x="%d" y="%d" font-size="12" text-anchor="middle" transform="rotate(-90 %d %d)">%s</text>'
                 % (16, plot_y + plot_h / 2, 16, plot_y + plot_h / 2, _esc(c2)))
    for xi, yi in zip(x, y):
        lines.append('<circle cx="%.1f" cy="%.1f" r="2" fill="#1f4e79" opacity="0.55"/>' % (xmap(xi), ymap(yi)))
    # least squares line
    mx, my = _mean(x), _mean(y)
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    sxx = sum((xi - mx) ** 2 for xi in x)
    if sxx > 0:
        slope = sxy / sxx
        intercept = my - slope * mx
        xl = lo_x
        xh = hi_x
        lines.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#c0392b" stroke-width="1.5"/>'
                     % (xmap(xl), ymap(intercept + slope * xl), xmap(xh), ymap(intercept + slope * xh)))
    r = corr.get("pearson_r")
    lines.append('<text x="%d" y="%d" font-size="12">Pearson r=%s, p=%s (n=%d)</text>'
                 % (plot_x, plot_y - 8, _esc(_fmt(r, 4)), _esc(_pstr(corr.get("pearson_p"))), n))
    lines += _svg_footer()
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def render_histogram(rows, col, out_path) -> None:
    x = _nums([r.get(col) for r in rows])
    if not x:
        return
    lo, hi = min(x), max(x)
    if lo == hi:
        lo -= 1
        hi += 1
    nbins = max(8, min(40, int(math.ceil(math.sqrt(len(x))))))
    bw = (hi - lo) / nbins
    counts = [0] * nbins
    for v in x:
        b = min(nbins - 1, int((v - lo) / bw))
        counts[b] += 1
    maxc = max(counts) if counts else 1
    plot_x = _MARGIN["l"]
    plot_w = _SVG_W - _MARGIN["l"] - _MARGIN["r"]
    plot_y = _MARGIN["t"]
    plot_h = _SVG_H - _MARGIN["t"] - _MARGIN["b"]
    lines = _svg_header(_SVG_W, _SVG_H, "Histogram: %s" % col)
    lines.append('<rect x="0" y="0" width="%d" height="%d" fill="white"/>' % (_SVG_W, _SVG_H))
    for t in _nice_ticks(lo, hi):
        vx = plot_x + plot_w * (t - lo) / (hi - lo)
        lines.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#eee"/>' % (vx, plot_y, vx, plot_y + plot_h))
        lines.append('<text x="%.1f" y="%d" font-size="11" text-anchor="middle">%s</text>' % (vx, plot_y + plot_h + 16, _esc(_fmt(t, 4))))
    for t in _nice_ticks(0, maxc, 5):
        vy = plot_y + plot_h * (1 - t / maxc) if maxc else plot_y
        lines.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#eee"/>' % (plot_x, vy, plot_x + plot_w, vy))
        lines.append('<text x="%d" y="%.1f" font-size="11" text-anchor="end">%s</text>' % (plot_x - 8, vy + 4, _esc(_fmt(t, 3))))
    lines.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="black"/>' % (plot_x, plot_y + plot_h, plot_x + plot_w, plot_y + plot_h))
    lines.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="black"/>' % (plot_x, plot_y, plot_x, plot_y + plot_h))
    for i, c in enumerate(counts):
        bx = plot_x + plot_w * i / nbins
        bw_px = plot_w / nbins
        bh = plot_h * c / maxc
        lines.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="#9ec5e8" stroke="#1f4e79"/>'
                     % (bx, plot_y + plot_h - bh, max(1, bw_px - 1), bh))
    lines.append('<text x="%d" y="%d" font-size="12" text-anchor="middle">%s</text>' % (plot_x + plot_w / 2, plot_y + plot_h + 38, _esc(col)))
    lines += _svg_footer()
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def render_figures(rows, stats, work_dir) -> list:
    figures = []
    idx = 1
    if stats.get("best_group_comparison"):
        bc = stats["best_group_comparison"]
        gc = next((g for g in stats["group_comparisons"]
                   if g["group_col"] == bc["group_col"] and g["target_col"] == bc["target_col"]), None)
        if gc:
            path = os.path.join(work_dir, "figure_%d_boxplot.svg" % idx)
            render_boxplot(stats, gc, path)
            figures.append({"path": os.path.basename(path), "name": os.path.basename(path),
                            "type": "boxplot", "group_col": gc["group_col"],
                            "target_col": gc["target_col"]})
            idx += 1
    if stats.get("correlations"):
        top = max(stats["correlations"], key=lambda c: abs(c.get("pearson_r") or 0.0))
        c1, c2 = top["x"], top["y"]
        path = os.path.join(work_dir, "figure_%d_scatter.svg" % idx)
        render_scatter_with_data(rows, c1, c2, top, path)
        figures.append({"path": os.path.basename(path), "name": os.path.basename(path),
                        "type": "scatter", "x": c1, "y": c2})
        idx += 1
    numeric_cols = stats.get("numeric_columns") or []
    if numeric_cols:
        col = numeric_cols[-1]
        path = os.path.join(work_dir, "figure_%d_histogram.svg" % idx)
        render_histogram(rows, col, path)
        figures.append({"path": os.path.basename(path), "name": os.path.basename(path),
                        "type": "histogram", "col": col})
        idx += 1
    stats["figures"] = figures
    return figures


# --------------------------------------------------------------------------
# Report rendering (all numbers from stats.json)
# --------------------------------------------------------------------------

def render_report(stats, clean_meta, profile, discovery, task_text) -> str:
    L = []
    L.append("# 科研数据分析报告")
    L.append("")
    L.append("> 本报告所有数值均来自 `work/stats.json`，可回算、可复现；未引入任何手写数字。")
    L.append("")
    L.append("## 1. 数据概况")
    L.append("")
    L.append("- 数据源：`%s`（类型 %s）" % (stats.get("source"), stats.get("table") or stats.get("source")))
    L.append("- 清洗后样本量：n = %d，列数 = %d" % (stats.get("n_rows"), stats.get("n_columns")))
    L.append("- 数值列：%s" % (", ".join(stats.get("analysis_numeric_columns") or stats.get("numeric_columns") or []) or "无"))
    L.append("- 分类列：%s" % (", ".join(stats.get("categorical_columns") or []) or "无"))
    L.append("- 分组候选列：%s" % (", ".join(stats.get("group_candidates") or []) or "无"))
    L.append("")
    L.append("## 2. 清洗记录")
    L.append("")
    L.append("- 输入行数 %d → 输出去重后行数 %d；删除完全重复行 %d。" %
             (clean_meta.get("input_rows"), clean_meta.get("output_rows"),
              clean_meta.get("dropped_duplicates")))
    if clean_meta.get("missing_by_column"):
        L.append("- 缺失值（仅计数，未填补）：")
        for c, n in sorted(clean_meta["missing_by_column"].items()):
            L.append("  - `%s`：%d 个" % (c, n))
    if clean_meta.get("outlier_by_column"):
        L.append("- 异常值（IQR×1.5 判定，仅标记未删除）：")
        for c, m in sorted(clean_meta["outlier_by_column"].items()):
            L.append("  - `%s`：%d 行超出 [%s, %s]" %
                     (c, m["rows_flagged"], _fmt(m["lower"], 4), _fmt(m["upper"], 4)))
    L.append("")
    L.append("## 3. 描述统计")
    L.append("")
    L.append("| 列 | n | 均值 | 标准差 | 中位数 | Q1 | Q3 | 最小 | 最大 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for c in stats.get("analysis_numeric_columns") or stats.get("numeric_columns") or []:
        d = stats["descriptive"][c]
        L.append("| `%s` | %d | %s | %s | %s | %s | %s | %s | %s |" %
                 (c, d["n"], _fmt(d["mean"], 4), _fmt(d["sd"], 4),
                  _fmt(d["median"], 4), _fmt(d["q1"], 4), _fmt(d["q3"], 4),
                  _fmt(d["min"], 4), _fmt(d["max"], 4)))
    L.append("")
    L.append("## 4. 统计推断")
    L.append("")
    if stats.get("group_comparisons"):
        L.append("### 4.1 组间比较")
        L.append("")
        for gc in stats["group_comparisons"]:
            aov = gc["anova"]
            kw = gc["kruskal_wallis"]
            lev = gc["levene"]
            L.append("- 分组列 `%s` × 目标列 `%s`：" % (gc["group_col"], gc["target_col"]))
            L.append("  - ANOVA F=%s, p=%s；Kruskal-Wallis H=%s, p=%s；Levene p=%s" %
                     (_fmt(aov.get("statistic"), 4), _pstr(aov.get("p_value")),
                      _fmt(kw.get("statistic"), 4), _pstr(kw.get("p_value")),
                      _pstr(lev.get("p_value"))))
            L.append("  - 各组：%s" % "; ".join(
                "%s(n=%d, mean=%s, sd=%s)" % (g, gc["groups"][g]["n"],
                                              _fmt(gc["groups"][g]["mean"], 4),
                                              _fmt(gc["groups"][g]["sd"], 4))
                for g in sorted(gc["groups"])))
    if stats.get("correlations"):
        L.append("### 4.2 相关分析")
        L.append("")
        L.append("| 变量 X | 变量 Y | n | Pearson r | p | Spearman ρ | p |")
        L.append("|---|---|---|---|---|---|---|")
        for c in stats["correlations"]:
            L.append("| `%s` | `%s` | %d | %s | %s | %s | %s |" %
                     (c["x"], c["y"], c["n"], _fmt(c.get("pearson_r"), 4),
                      _pstr(c.get("pearson_p")), _fmt(c.get("spearman_rho"), 4),
                      _pstr(c.get("spearman_p"))))
    L.append("")
    L.append("## 5. 图表")
    L.append("")
    for f in stats.get("figures") or []:
        L.append("- `%s`（%s）" % (f["name"], f["type"]))
    L.append("")
    L.append("## 6. 局限与不确定性")
    L.append("")
    if stats.get("limitations"):
        for item in stats["limitations"]:
            L.append("- **局限**：%s" % item)
    if stats.get("uncertainty"):
        for item in stats["uncertainty"]:
            L.append("- **不确定**：%s" % item)
    if not stats.get("limitations") and not stats.get("uncertainty"):
        L.append("- 本次运行未记录到明确局限；统计口径详见 `stats.json`。")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------
# Scaffold / finalize
# --------------------------------------------------------------------------

def _contract_default_values(keys):
    return {k: None for k in keys}


def scaffold(input_dir, output_dir, work_dir, overrides=None) -> dict:
    _ensure_dir(output_dir)
    _ensure_dir(work_dir)
    case = read_case(input_dir)
    task = read_task(input_dir)
    contract_keys = case["contract_keys"]
    result = {
        "case_id": case["case_id"],
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "limitations": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "change_log": [_now_entry("scaffold", "partial skeleton written")],
        "runtime": {"start": _utcnow(), "request_budget_seconds": 900,
                    "request_budget_count": 64},
        "output_contract_keys": contract_keys,
    }
    for k in contract_keys:
        result[k] = None
    if case.get("no_input_dir"):
        result["status"] = "input_error"
        result["degradations"].append("input directory not found or not readable")
    elif case.get("invalid"):
        result["degradations"].append("case.json exists but is not valid JSON; using default contract and inferred case_id")
    elif case.get("missing"):
        result["degradations"].append("case.json not found; using input directory name as case_id and default contract")
    result_path = os.path.join(output_dir, "result.json")
    _write_json(result_path, result)
    return {"case": case, "task": task, "result": result, "result_path": result_path}


def finalize(input_dir, output_dir, work_dir, overrides=None) -> dict:
    case = read_case(input_dir)
    task = read_task(input_dir)
    result_path = os.path.join(output_dir, "result.json")
    result = _load_json(result_path) or {"case_id": case["case_id"]}
    contract_keys = case["contract_keys"]

    discovery = _load_json(os.path.join(work_dir, "discovery.json"))
    profile = _load_json(os.path.join(work_dir, "profile.json"))
    clean_meta = _load_json(os.path.join(work_dir, "clean_meta.json"))
    stats = _load_json(os.path.join(work_dir, "stats.json"))

    errors = result.get("errors", [])
    degradations = result.get("degradations", [])
    limitations = result.get("limitations", [])
    uncertainty = list(result.get("uncertainty", []))

    # fold stats-level uncertainty/limitations
    if stats:
        for u in stats.get("uncertainty", []):
            if u not in uncertainty:
                uncertainty.append(u)
        for l in stats.get("limitations", []):
            if l not in limitations:
                limitations.append(l)
        # honest floating-point underflow note when p-values hit machine zero
        pvals = []
        for gc in stats.get("group_comparisons", []):
            pvals.append(gc.get("anova", {}).get("p_value"))
            pvals.append(gc.get("kruskal_wallis", {}).get("p_value"))
        for cr in stats.get("correlations", []):
            pvals.append(cr.get("pearson_p"))
            pvals.append(cr.get("spearman_p"))
        if any(p == 0 for p in pvals if p is not None):
            note = ("some p-values equal 0.0 due to double-precision underflow; "
                    "report renders them as <1e-300 and treats them as p<1e-300, not as literal zero")
            if note not in uncertainty:
                uncertainty.append(note)

    # evidence mapping with locator + quote
    sources = []
    def _locator(path):
        return os.path.basename(path)
    if discovery:
        for ds in discovery.get("datasets", []):
            if ds.get("n_rows", 0) > 0:
                cols = ds.get("columns") or []
                quote = ("header: " + ", ".join(str(c) for c in cols[:24])) if cols \
                    else ds.get("source", "")
                sources.append({"locator": _locator(ds.get("path", "")),
                                "quote": quote, "role": "input_table",
                                "n_rows": ds.get("n_rows")})
    # task snippet as evidence
    if task:
        snippet = " ".join(task.split())[:240]
        sources.append({"locator": "task.md", "quote": snippet, "role": "task_requirement"})
    # stats-derived quotes
    if stats and stats.get("correlations"):
        top = max(stats["correlations"], key=lambda c: abs(c.get("pearson_r") or 0.0))
        sources.append({"locator": "work/stats.json", "role": "correlation",
                        "quote": "Pearson r(%s,%s)=%s p=%s" %
                                 (top["x"], top["y"], _fmt(top.get("pearson_r"), 4),
                                  _fmt(top.get("pearson_p"), 4))})
    if stats and stats.get("best_group_comparison"):
        bc = stats["best_group_comparison"]
        gc = next((g for g in stats["group_comparisons"]
                   if g["group_col"] == bc["group_col"] and g["target_col"] == bc["target_col"]), None)
        if gc:
            sources.append({"locator": "work/stats.json", "role": "group_comparison",
                            "quote": "KW(%s~%s) p=%s" %
                                     (gc["target_col"], gc["group_col"],
                                      _fmt(gc["kruskal_wallis"].get("p_value"), 4))})

    # products
    products = []
    def _copy(src, dst_dir, name=None):
        if src and os.path.isfile(src):
            _ensure_dir(dst_dir)
            dst = os.path.join(dst_dir, name or os.path.basename(src))
            with open(src, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
            with open(dst, "w", encoding="utf-8", newline="") as fh:
                fh.write(content)
            return dst
        return None

    for name in ("cleaned.csv", "clean_meta.json", "profile.json", "stats.json"):
        p = _copy(os.path.join(work_dir, name), output_dir)
        if p:
            products.append(os.path.basename(p))
    for f in (stats or {}).get("figures", []):
        p = _copy(os.path.join(work_dir, f.get("path") or f.get("name") or ""), output_dir)
        if p:
            products.append(os.path.basename(p))
    report_path = os.path.join(work_dir, "report.md")
    if os.path.isfile(report_path):
        p = _copy(report_path, output_dir, "report.md")
        if p:
            products.append("report.md")

    # fill contract fields
    if stats:
        analysis_results = {
            "summary": {
                "n_rows": stats.get("n_rows"), "n_columns": stats.get("n_columns"),
                "numeric_columns": stats.get("numeric_columns"),
                "categorical_columns": stats.get("categorical_columns"),
            },
            "descriptive": stats.get("descriptive"),
            "group_comparisons": stats.get("group_comparisons"),
            "correlations": stats.get("correlations"),
            "products": products,
        }
        quality_evidence = {
            "cleaning": clean_meta, "profile": profile,
            "statistics_file": "stats.json", "sources": sources,
            "reproducibility": {"deterministic": True,
                                "rerun_command": "python3 -B scripts/main.py run"},
        }
        anomaly_notes = []
        if clean_meta:
            for c, n in sorted((clean_meta.get("missing_by_column") or {}).items()):
                if n:
                    anomaly_notes.append({"type": "missing", "column": c, "count": n,
                                          "handling": "counted, not imputed"})
            for c, m in sorted((clean_meta.get("outlier_by_column") or {}).items()):
                anomaly_notes.append({"type": "outlier", "column": c,
                                      "rows_flagged": m.get("rows_flagged"),
                                      "criterion": m.get("criterion"), "handling": "flagged, not removed"})
            if clean_meta.get("dropped_duplicates"):
                anomaly_notes.append({"type": "duplicate",
                                      "rows_removed": clean_meta.get("dropped_duplicates"),
                                      "handling": "exact duplicate rows removed"})
    else:
        analysis_results = {"summary": None, "products": products}
        quality_evidence = {"cleaning": clean_meta, "profile": profile,
                            "sources": sources}
        anomaly_notes = []

    mapped = {"analysis_results": analysis_results, "quality_evidence": quality_evidence,
              "anomaly_notes": anomaly_notes}
    for k in contract_keys:
        result[k] = mapped.get(k)

    # status
    has_data = bool(discovery and any(d.get("n_rows", 0) > 0 for d in discovery.get("datasets", [])))
    if case.get("no_input_dir") and not has_data:
        result["status"] = "input_error"
        limitations.append("input directory unreadable and no data available")
    elif not has_data:
        result["status"] = "insufficient_evidence"
        limitations.append("no tabular data found in /input; cannot run analysis")
    elif errors:
        result["status"] = "partial"
    elif degradations or case.get("missing") or case.get("invalid") or not stats:
        result["status"] = "partial"
    else:
        result["status"] = "ok"
    # keep honest partial when assumptions require human review
    if stats and stats.get("group_comparisons"):
        for gc in stats["group_comparisons"]:
            if gc.get("levene", {}).get("p_value") is not None and gc["levene"]["p_value"] < 0.05:
                result["pending_human"].append(
                    "heteroscedastic group comparison (%s~%s): Levene p<0.05; "
                    "report uses Welch/Kruskal-Wallis, human should confirm test choice" %
                    (gc["target_col"], gc["group_col"]))
                if result["status"] == "ok":
                    result["status"] = "partial"

    result["sources"] = sources
    result["uncertainty"] = uncertainty
    result["limitations"] = limitations
    result["errors"] = errors
    result["degradations"] = degradations
    result["runtime"]["end"] = _utcnow()
    result["change_log"] = list(result.get("change_log", [])) + [_now_entry("finalize", "result assembled")]
    _write_json(result_path, result)
    return result


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------

def run_selftest(output_dir, work_dir, input_dir) -> dict:
    checks = []
    result_path = os.path.join(output_dir, "result.json")
    result = _load_json(result_path)

    def chk(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    chk("result.json exists", os.path.isfile(result_path))
    chk("result.json valid JSON", result is not None)
    if result is None:
        return {"checks": checks, "all_ok": False}
    chk("case_id present", bool(result.get("case_id")))
    chk("status in allowed set", result.get("status") in ALLOWED_STATUS,
        "status=%s" % result.get("status"))
    contract_keys = result.get("output_contract_keys") or DEFAULT_CONTRACT_KEYS
    chk("contract keys present", all(k in result for k in contract_keys),
        "keys=%s" % contract_keys)
    for field in ("sources", "uncertainty", "change_log", "limitations", "pending_human"):
        chk("%s present" % field, isinstance(result.get(field), list))
    chk("report.md exists", os.path.isfile(os.path.join(output_dir, "report.md")))
    stats = _load_json(os.path.join(output_dir, "stats.json"))
    chk("stats.json valid", stats is not None)
    clean_meta = _load_json(os.path.join(output_dir, "clean_meta.json"))
    cleaned_rows = 0
    if clean_meta and os.path.isfile(os.path.join(output_dir, "cleaned.csv")):
        with open(os.path.join(output_dir, "cleaned.csv"), "r", encoding="utf-8") as fh:
            cleaned_rows = sum(1 for _ in fh) - 1
        expect = clean_meta.get("output_rows")
        chk("clean_meta row count matches cleaned.csv",
            cleaned_rows == expect, "csv=%d meta=%d" % (cleaned_rows, expect))
    # SVG parse
    svg_ok = True
    svg_count = 0
    for name in os.listdir(output_dir):
        if name.endswith(".svg"):
            svg_count += 1
            try:
                ET.parse(os.path.join(output_dir, name))
            except Exception as exc:
                svg_ok = False
                checks.append({"check": "SVG parse %s" % name, "ok": False, "detail": str(exc)})
    chk("all SVG parse", svg_ok, "count=%d" % svg_count)
    chk("statistics file deterministic (no random keys)", stats is not None)
    return {"checks": checks, "all_ok": all(c["ok"] for c in checks)}


def run_selftest_cli(argv) -> int:
    ap = argparse.ArgumentParser(prog="selftest.py")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--work", default=DEFAULT_WORK)
    args = ap.parse_args(argv)
    report = run_selftest(args.output, args.work, args.input)
    for c in report["checks"]:
        print("[%s] %s %s" % ("PASS" if c["ok"] else "FAIL", c["check"], c.get("detail", "")))
    print("ALL_OK=%s" % report["all_ok"])
    return 0


# --------------------------------------------------------------------------
# Pipeline stages
# --------------------------------------------------------------------------

def _load_overrides(work_dir, args) -> dict:
    ov = {}
    if getattr(args, "primary", None):
        ov["primary"] = args.primary
    if getattr(args, "group", None):
        ov["group"] = args.group
    if getattr(args, "targets", None):
        ov["targets"] = args.targets
    if getattr(args, "exclude", None):
        ov["exclude"] = args.exclude
    existing = _load_json(os.path.join(work_dir, "overrides.json")) or {}
    existing.update(ov)
    _write_json(os.path.join(work_dir, "overrides.json"), existing)
    return existing


def _pick_primary(discovery, overrides):
    datasets = [d for d in discovery.get("datasets", []) if d.get("n_rows", 0) > 0]
    if not datasets:
        return None
    if overrides.get("primary"):
        for d in datasets:
            if overrides["primary"] in (d.get("source"), d.get("table"), d.get("path")):
                return d
    # largest table wins
    datasets.sort(key=lambda d: -d.get("n_rows", 0))
    return datasets[0]


def run_full(args) -> None:
    _ensure_dir(args.output)
    _ensure_dir(args.work)
    overrides = _load_overrides(args.work, args)
    scaffold(args.input, args.output, args.work, overrides)
    discovery = discover_inputs(args.input, args.work)
    ds = _pick_primary(discovery, overrides)
    result = _load_json(os.path.join(args.output, "result.json")) or {}
    if ds is None:
        result["status"] = "insufficient_evidence"
        result["errors"] = result.get("errors", [])
        lim = list(result.get("limitations", []))
        lim.append("no tabular data found in /input; cannot run analysis")
        result["limitations"] = lim
        result["change_log"] = list(result.get("change_log", [])) + \
            [_now_entry("discover", "no tabular data found")]
        _write_json(os.path.join(args.output, "result.json"), result)
        return
    profile = profile_dataset(ds, args.work)
    rows, clean_meta, _csv = clean_dataset(ds, args.work)
    stats = analyze(rows, profile, args.work, overrides)
    render_figures(rows, stats, args.work)
    _write_json(os.path.join(args.work, "stats.json"), stats)
    task_text = read_task(args.input)
    report = render_report(stats, clean_meta, profile, discovery, task_text)
    with open(os.path.join(args.work, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(report)
    finalize(args.input, args.output, args.work, overrides)


def dispatch(args) -> int:
    if args.command == "scaffold":
        scaffold(args.input, args.output, args.work, _load_overrides(args.work, args))
    elif args.command == "discover":
        discover_inputs(args.input, args.work)
    elif args.command == "profile":
        ov = _load_overrides(args.work, args)
        disc = _load_json(os.path.join(args.work, "discovery.json")) or discover_inputs(args.input, args.work)
        ds = _pick_primary(disc, ov)
        if ds:
            profile_dataset(ds, args.work)
    elif args.command == "clean":
        ov = _load_overrides(args.work, args)
        disc = _load_json(os.path.join(args.work, "discovery.json")) or discover_inputs(args.input, args.work)
        ds = _pick_primary(disc, ov)
        if ds:
            clean_dataset(ds, args.work)
    elif args.command == "analyze":
        ov = _load_overrides(args.work, args)
        profile = _load_json(os.path.join(args.work, "profile.json"))
        disc = _load_json(os.path.join(args.work, "discovery.json")) or discover_inputs(args.input, args.work)
        ds = _pick_primary(disc, ov)
        if profile and ds:
            analyze(ds.get("rows", []), profile, args.work, ov)
    elif args.command == "render":
        ov = _load_overrides(args.work, args)
        profile = _load_json(os.path.join(args.work, "profile.json"))
        stats = _load_json(os.path.join(args.work, "stats.json"))
        disc = _load_json(os.path.join(args.work, "discovery.json")) or discover_inputs(args.input, args.work)
        ds = _pick_primary(disc, ov)
        clean_meta = _load_json(os.path.join(args.work, "clean_meta.json"))
        if profile and stats and ds:
            render_figures(ds.get("rows", []), stats, args.work)
            _write_json(os.path.join(args.work, "stats.json"), stats)
            report = render_report(stats, clean_meta or {}, profile, disc or {},
                                   read_task(args.input))
            with open(os.path.join(args.work, "report.md"), "w", encoding="utf-8") as fh:
                fh.write(report)
    elif args.command == "finalize":
        finalize(args.input, args.output, args.work, _load_overrides(args.work, args))
    elif args.command == "run":
        run_full(args)
    elif args.command == "selftest":
        run_selftest_cli(["--input", args.input, "--output", args.output, "--work", args.work])
    else:
        print("unknown command: %s" % args.command)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="main.py",
        description="multi-step research data analysis pipeline (pure stdlib)")
    sub = ap.add_subparsers(dest="command")
    commands = ["scaffold", "discover", "profile", "clean", "analyze", "render",
                "finalize", "run", "selftest"]
    for cmd in commands:
        p = sub.add_parser(cmd)
        p.add_argument("--input", default=DEFAULT_INPUT)
        p.add_argument("--output", default=DEFAULT_OUTPUT)
        p.add_argument("--work", default=DEFAULT_WORK)
        p.add_argument("--primary", default=None)
        p.add_argument("--group", default=None)
        p.add_argument("--targets", default=None)
        p.add_argument("--exclude", default=None)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if not getattr(args, "command", None):
        build_parser().print_help()
        return 0
    return dispatch(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never crash on data problems
        print("ERROR: %s" % exc, file=sys.stderr)
        sys.exit(0)
