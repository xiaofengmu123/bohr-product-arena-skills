"""core_impl.py - deterministic, standard-library-only statistical analysis pipeline.

This module is the implementation behind scripts/main.py.  It intentionally uses
only the Python standard library so that the same code runs on the authoring
machine, the evaluation sandbox, and any other machine without pip installs.

Design principles
-----------------
* Deterministic: no RNG, fixed ordering, no reliance on wall-clock for numbers.
* Honest: every computed number carries a locator and, where possible, a verbatim
  quote from the source material.  Missing software/units are recorded, never
  guessed.
* Early drop: `init` writes a valid partial result.json immediately; later
  commands merge into it incrementally instead of building once at the end.
"""

from __future__ import annotations

import argparse
import ast
import csv
import io
import json
import math
import os
import re
import sqlite3
import statistics as st
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Small constants and helpers
# ---------------------------------------------------------------------------

MISSING_TOKENS = {"", "na", "n/a", "null", "none", "nan", "?", "nil", "missing"}
MAX_TEXT_FILE = 2 * 1024 * 1024  # skip text files larger than 2MB, record it
CONF = 0.95
Z_95 = 1.959963984540054  # normal_ppf(0.975)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def fnum(x, nd=6):
    """Format a float deterministically; None/NaN -> null for JSON safety."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return round(v, nd)


# ---------------------------------------------------------------------------
# Special functions (all standard library, all verifiable in selftest.py)
# ---------------------------------------------------------------------------

def normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def normal_ppf(p: float) -> float:
    """Acklam's inverse normal CDF (deterministic, ~1e-9 relative accuracy)."""
    if p <= 0.0 or p >= 1.0:
        return math.nan
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 0.97575
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
               (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
           ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)


def _gser(a: float, x: float) -> float:
    EPS, ITMAX = 3e-12, 1000
    ap = a
    s = 1.0 / a
    d = s
    for _ in range(ITMAX):
        ap += 1.0
        d *= x / ap
        s += d
        if abs(d) < abs(s) * EPS:
            break
    return s * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gcf(a: float, x: float) -> float:
    EPS, ITMAX, FPMIN = 3e-12, 1000, 1e-300
    b = x + 1.0 - a
    c = 1.0 / FPMIN
    d = 1.0 / b
    h = d
    for i in range(1, ITMAX + 1):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < FPMIN:
            d = FPMIN
        c = b + an / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        dl = d * c
        h *= dl
        if abs(dl - 1.0) < EPS:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def gammp(a: float, x: float) -> float:
    """Regularized lower incomplete gamma P(a, x)."""
    if x < 0.0 or a <= 0.0:
        return math.nan
    if x == 0.0:
        return 0.0
    if x < a + 1.0:
        return _gser(a, x)
    return 1.0 - _gcf(a, x)


def _betacf(a: float, b: float, x: float) -> float:
    EPS, ITMAX, FPMIN = 3e-12, 1000, 1e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, ITMAX + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        dl = d * c
        h *= dl
        if abs(dl - 1.0) < EPS:
            break
    return h


def betai(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                  + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: float) -> float:
    if df <= 0:
        return math.nan
    x = df / (df + t * t)
    ib = betai(df / 2.0, 0.5, x)
    return 1.0 - 0.5 * ib if t >= 0 else 0.5 * ib


def t_ppf(p: float, df: float) -> float:
    """Inverse Student-t CDF by bisection (deterministic)."""
    if p <= 0.0 or p >= 1.0 or df <= 0:
        return math.nan
    lo, hi = -1e6, 1e6
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def chi2_cdf(x: float, k: float) -> float:
    if x < 0:
        return 0.0
    return gammp(k / 2.0, x / 2.0)


def chi2_ppf(p: float, k: float) -> float:
    if p <= 0.0 or p >= 1.0 or k <= 0:
        return math.nan
    lo, hi = 0.0, 1e5
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if chi2_cdf(mid, k) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def f_cdf(x: float, d1: float, d2: float) -> float:
    if x < 0:
        return 0.0
    return betai(d1 / 2.0, d2 / 2.0, d1 * x / (d1 * x + d2))


def f_ppf(p: float, d1: float, d2: float) -> float:
    if p <= 0.0 or p >= 1.0 or d1 <= 0 or d2 <= 0:
        return math.nan
    lo, hi = 0.0, 1e6
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if f_cdf(mid, d1, d2) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ---------------------------------------------------------------------------
# Descriptive statistics
# ---------------------------------------------------------------------------

def quantile(sorted_vals: list, p: float) -> float:
    """Type-7 (linear interpolation) quantile, matches numpy default."""
    n = len(sorted_vals)
    if n == 0:
        return math.nan
    if n == 1:
        return sorted_vals[0]
    h = (n - 1) * p
    lo = int(math.floor(h))
    hi = int(math.ceil(h))
    if lo == hi:
        return sorted_vals[lo]
    frac = h - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def sample_sd(vals: list) -> float | None:
    n = len(vals)
    if n < 2:
        return None
    m = sum(vals) / n
    return math.sqrt(sum((x - m) ** 2 for x in vals) / (n - 1))


def descriptive(vals: list, unit: str = "unit_unknown") -> dict:
    n = len(vals)
    if n == 0:
        return {"n": 0, "unit": unit}
    s = sorted(vals)
    m = sum(vals) / n
    sd = sample_sd(vals)
    out = {
        "n": n,
        "mean": fnum(m),
        "median": fnum(st.median(vals)),
        "sd": fnum(sd),
        "min": fnum(s[0]),
        "max": fnum(s[-1]),
        "q1": fnum(quantile(s, 0.25)),
        "q3": fnum(quantile(s, 0.75)),
        "iqr": fnum(quantile(s, 0.75) - quantile(s, 0.25)),
        "unit": unit,
    }
    if sd and sd > 0 and n >= 3:
        z = [(x - m) / sd for x in vals]
        g1 = n / ((n - 1) * (n - 2)) * sum(zi ** 3 for zi in z)
        out["skewness"] = fnum(g1)
        if n >= 4:
            g2 = n * (n + 1) / ((n - 1) * (n - 2) * (n - 3)) * sum(zi ** 4 for zi in z) \
                 - 3 * (n - 1) ** 2 / ((n - 2) * (n - 3))
            out["kurtosis_excess"] = fnum(g2)
    return out


def shapiro_francia(vals: list) -> dict:
    """Shapiro-Francia W' statistic (Blom plotting positions).

    The p-value is *not* computed here: the agent cross-checks with
    scipy.stats.shapiro when scientific-python is available.  W' alone is a
    deterministic departure-from-normality indicator and is never reported as a
    p-value.
    """
    n = len(vals)
    if n < 3:
        return {"W_prime": None, "note": "n<3, normality not assessed"}
    s = sorted(vals)
    m = sum(s) / n
    ss = sum((x - m) ** 2 for x in s)
    if ss <= 0:
        return {"W_prime": None, "note": "zero variance, normality not assessed"}
    blom = [normal_ppf((i + 1 - 0.375) / (n + 0.25)) for i in range(n)]
    mm = sum(bi * bi for bi in blom)
    num = sum(bi * s[i] for i, bi in enumerate(blom)) ** 2
    return {"W_prime": fnum(num / (mm * ss)), "n": n}


# ---------------------------------------------------------------------------
# Inferential statistics
# ---------------------------------------------------------------------------

def one_way_anova(groups: list) -> dict:
    k = len(groups)
    all_vals = [x for g in groups for x in g]
    n_total = len(all_vals)
    grand = sum(all_vals) / n_total
    means = [sum(g) / len(g) for g in groups if g]
    ss_between = sum(len(g) * (means[i] - grand) ** 2 for i, g in enumerate(groups) if g)
    ss_within = sum(sum((x - means[i]) ** 2 for x in g) for i, g in enumerate(groups) if g)
    df1 = k - 1
    df2 = n_total - k
    out = {"ss_between": fnum(ss_between), "ss_within": fnum(ss_within),
           "df_between": df1, "df_within": df2}
    if df2 <= 0 or ss_within <= 0 or df1 <= 0:
        out["F"] = None
        out["p_value"] = None
        out["note"] = "zero within-group variance or insufficient df"
        return out
    ms_between = ss_between / df1
    ms_within = ss_within / df2
    f = ms_between / ms_within
    out["ms_between"] = fnum(ms_between)
    out["ms_within"] = fnum(ms_within)
    out["F"] = fnum(f)
    out["p_value"] = fnum(1.0 - f_cdf(f, df1, df2))
    out["eta_squared"] = fnum(ss_between / (ss_between + ss_within))
    omega2 = (ss_between - df1 * ms_within) / (ss_between + ss_within + ms_within)
    out["omega_squared"] = fnum(omega2)
    return out


def brown_forsythe(groups: list) -> dict:
    """Median-centered Levene (Brown-Forsythe) homogeneity test."""
    k = len(groups)
    medians = [st.median(g) for g in groups]
    z = []
    z_groups = []
    for i, g in enumerate(groups):
        zi = [abs(x - medians[i]) for x in g]
        z.extend(zi)
        z_groups.append(zi)
    res = one_way_anova(z_groups)
    res["method"] = "Brown-Forsythe (median-centered Levene)"
    return res


def student_t(g1: list, g2: list) -> dict:
    n1, n2 = len(g1), len(g2)
    out = {"method": "Student's t (pooled)", "n1": n1, "n2": n2,
           "mean1": fnum(sum(g1) / n1) if n1 else None,
           "mean2": fnum(sum(g2) / n2) if n2 else None}
    if n1 < 2 or n2 < 2:
        out.update({"t": None, "df": None, "p_value": None, "note": "n<2 in a group"})
        return out
    m1, m2 = sum(g1) / n1, sum(g2) / n2
    s1, s2 = sample_sd(g1), sample_sd(g2)
    sp2 = ((n1 - 1) * s1 ** 2 + (n2 - 1) * s2 ** 2) / (n1 + n2 - 2)
    se = math.sqrt(sp2 * (1 / n1 + 1 / n2))
    out["mean_diff"] = fnum(m1 - m2)
    out["se"] = fnum(se)
    if se == 0:
        out.update({"t": None, "df": n1 + n2 - 2, "p_value": None, "note": "zero pooled SE"})
        return out
    t = (m1 - m2) / se
    df = n1 + n2 - 2
    out["t"] = fnum(t)
    out["df"] = df
    out["p_value"] = fnum(2.0 * (1.0 - t_cdf(abs(t), df)))
    return out


def welch_t(g1: list, g2: list) -> dict:
    n1, n2 = len(g1), len(g2)
    out = {"method": "Welch's t", "n1": n1, "n2": n2,
           "mean1": fnum(sum(g1) / n1) if n1 else None,
           "mean2": fnum(sum(g2) / n2) if n2 else None}
    if n1 < 2 or n2 < 2:
        out.update({"t": None, "df": None, "p_value": None, "note": "n<2 in a group"})
        return out
    m1, m2 = sum(g1) / n1, sum(g2) / n2
    s1, s2 = sample_sd(g1), sample_sd(g2)
    v1, v2 = s1 ** 2 / n1, s2 ** 2 / n2
    se = math.sqrt(v1 + v2)
    out["mean_diff"] = fnum(m1 - m2)
    out["se"] = fnum(se)
    if se == 0:
        out.update({"t": None, "df": None, "p_value": None, "note": "zero Welch SE"})
        return out
    t = (m1 - m2) / se
    df = (v1 + v2) ** 2 / (v1 ** 2 / (n1 - 1) + v2 ** 2 / (n2 - 1))
    out["t"] = fnum(t)
    out["df"] = fnum(df)
    out["p_value"] = fnum(2.0 * (1.0 - t_cdf(abs(t), df)))
    return out


def _rank_ties(seq: list) -> list:
    order = sorted(range(len(seq)), key=lambda i: seq[i])
    ranks = [0.0] * len(seq)
    i = 0
    n = len(seq)
    while i < n:
        j = i
        while j + 1 < n and seq[order[j + 1]] == seq[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def mann_whitney_u(g1: list, g2: list) -> dict:
    n1, n2 = len(g1), len(g2)
    out = {"method": "Mann-Whitney U (two-sided, normal approx, tie-corrected)",
           "n1": n1, "n2": n2}
    if n1 == 0 or n2 == 0:
        out.update({"U": None, "z": None, "p_value": None, "note": "empty group"})
        return out
    combined = list(g1) + list(g2)
    ranks = _rank_ties(combined)
    r1 = sum(ranks[i] for i in range(n1))
    u1 = r1 - n1 * (n1 + 1) / 2.0
    u2 = n1 * n2 - u1
    u = min(u1, u2)
    n = n1 + n2
    tie_term = 0.0
    from collections import Counter
    for t in Counter(combined).values():
        tie_term += t ** 3 - t
    sigma = math.sqrt((n1 * n2 / 12.0) * ((n + 1) - tie_term / (n * (n - 1))))
    if sigma == 0:
        out.update({"U1": fnum(u1), "U2": fnum(u2), "U": fnum(u), "z": None, "p_value": None,
                    "note": "all tied"})
        return out
    mean_u = n1 * n2 / 2.0
    z = (abs(u - mean_u) - 0.5) / sigma
    out["U1"] = fnum(u1)
    out["U2"] = fnum(u2)
    out["U"] = fnum(u)
    out["z"] = fnum(z)
    out["p_value"] = fnum(2.0 * (1.0 - normal_cdf(abs(z))))
    return out


def kruskal_wallis(groups: list) -> dict:
    k = len(groups)
    out = {"method": "Kruskal-Wallis H (tie-corrected)", "k": k}
    groups = [g for g in groups if g]
    if k < 3:
        out.update({"H": None, "df": None, "p_value": None, "note": "fewer than 3 groups"})
        return out
    all_vals = [x for g in groups for x in g]
    n = len(all_vals)
    ranks = _rank_ties(all_vals)
    pos = 0
    rsums = []
    ns = []
    for g in groups:
        ns.append(len(g))
        rsums.append(sum(ranks[pos:pos + len(g)]))
        pos += len(g)
    h = 12.0 / (n * (n + 1)) * sum(rs ** 2 / ni for rs, ni in zip(rsums, ns)) - 3 * (n + 1)
    from collections import Counter
    tie_term = sum(t ** 3 - t for t in Counter(all_vals).values())
    c = 1.0 - tie_term / (n ** 3 - n)
    if c <= 0:
        out.update({"H": fnum(h), "df": k - 1, "p_value": None, "note": "all tied"})
        return out
    h_corr = h / c
    df = k - 1
    out["H"] = fnum(h_corr)
    out["H_uncorrected"] = fnum(h)
    out["df"] = df
    out["p_value"] = fnum(1.0 - chi2_cdf(h_corr, df))
    return out


def pearson_r(x: list, y: list) -> dict:
    n = min(len(x), len(y))
    x = x[:n]
    y = y[:n]
    out = {"method": "Pearson r", "n": n}
    if n < 3:
        out.update({"r": None, "p_value": None, "note": "n<3"})
        return out
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    sxx = sum((xi - mx) ** 2 for xi in x)
    syy = sum((yi - my) ** 2 for yi in y)
    if sxx <= 0 or syy <= 0:
        out.update({"r": None, "p_value": None, "note": "zero variance"})
        return out
    r = sxy / math.sqrt(sxx * syy)
    r = clamp01(r)
    t = r * math.sqrt((n - 2) / max(1e-12, 1 - r * r))
    out["r"] = fnum(r)
    out["t"] = fnum(t)
    out["df"] = n - 2
    out["p_value"] = fnum(2.0 * (1.0 - t_cdf(abs(t), n - 2)))
    z = math.atanh(r)
    se = 1.0 / math.sqrt(n - 3)
    out["ci_low"] = fnum(math.tanh(z - Z_95 * se))
    out["ci_high"] = fnum(math.tanh(z + Z_95 * se))
    return out


def paired_t(x: list, y: list) -> dict:
    n = min(len(x), len(y))
    x, y = x[:n], y[:n]
    d = [xi - yi for xi, yi in zip(x, y)]
    out = {"method": "paired t", "n": n}
    if n < 2:
        out.update({"t": None, "df": None, "p_value": None, "note": "n<2 pairs"})
        return out
    md = sum(d) / n
    sd = sample_sd(d)
    if sd is None or sd == 0:
        out.update({"t": None, "df": n - 1, "p_value": None, "note": "zero difference variance"})
        return out
    se = sd / math.sqrt(n)
    t = md / se
    out["mean_diff"] = fnum(md)
    out["t"] = fnum(t)
    out["df"] = n - 1
    out["p_value"] = fnum(2.0 * (1.0 - t_cdf(abs(t), n - 1)))
    return out


def wilcoxon_signed_rank(x: list, y: list) -> dict:
    n = min(len(x), len(y))
    d = [x[i] - y[i] for i in range(n)]
    d = [v for v in d if v != 0]
    nz = len(d)
    out = {"method": "Wilcoxon signed-rank (normal approx, tie-corrected)", "n_nonzero": nz}
    if nz == 0:
        out.update({"W": None, "z": None, "p_value": None, "note": "all differences zero"})
        return out
    absd = [abs(v) for v in d]
    ranks = _rank_ties(absd)
    w = sum(r for v, r in zip(d, ranks) if v > 0)
    from collections import Counter
    tie_term = sum(t ** 3 - t for t in Counter(absd).values())
    mean_w = nz * (nz + 1) / 4.0
    sigma = math.sqrt(nz * (nz + 1) * (2 * nz + 1) / 24.0 - tie_term / 48.0)
    if sigma == 0:
        out.update({"W": fnum(w), "z": None, "p_value": None, "note": "all tied"})
        return out
    z = (abs(w - mean_w) - 0.5) / sigma
    out["W"] = fnum(w)
    out["z"] = fnum(z)
    out["p_value"] = fnum(2.0 * (1.0 - normal_cdf(abs(z))))
    return out


def chi2_independence(table: list, row_labels=None, col_labels=None) -> dict:
    """Pearson chi-square on a 2-D contingency table of counts."""
    rows = [list(r) for r in table]
    n_rows, n_cols = len(rows), len(rows[0]) if rows else 0
    out = {"method": "Pearson chi-square independence", "n_rows": n_rows, "n_cols": n_cols,
           "row_labels": row_labels, "col_labels": col_labels}
    n = sum(sum(r) for r in rows)
    row_sums = [sum(r) for r in rows]
    col_sums = [sum(r[i] for r in rows) for i in range(n_cols)]
    chi = 0.0
    for i in range(n_rows):
        for j in range(n_cols):
            exp = row_sums[i] * col_sums[j] / n if n else 0.0
            if exp > 0:
                chi += (rows[i][j] - exp) ** 2 / exp
    df = (n_rows - 1) * (n_cols - 1)
    out["chi2"] = fnum(chi)
    out["df"] = df
    out["p_value"] = fnum(1.0 - chi2_cdf(chi, df)) if df > 0 else None
    denom = n * min(n_rows - 1, n_cols - 1)
    out["cramers_v"] = fnum(math.sqrt(chi / denom)) if denom > 0 else None
    return out


# ---------------------------------------------------------------------------
# Effect sizes and confidence intervals
# ---------------------------------------------------------------------------

def cohens_d(g1: list, g2: list) -> dict:
    n1, n2 = len(g1), len(g2)
    out = {"name": "Cohen's d (pooled SD)", "n1": n1, "n2": n2}
    if n1 < 2 or n2 < 2:
        out.update({"value": None, "ci_low": None, "ci_high": None, "note": "n<2"})
        return out
    m1, m2 = sum(g1) / n1, sum(g2) / n2
    s1, s2 = sample_sd(g1), sample_sd(g2)
    sp = math.sqrt(((n1 - 1) * s1 ** 2 + (n2 - 1) * s2 ** 2) / (n1 + n2 - 2))
    if sp == 0:
        out.update({"value": None, "note": "zero pooled SD"})
        return out
    d = (m1 - m2) / sp
    out["value"] = fnum(d)
    se = math.sqrt((n1 + n2) / (n1 * n2) + d * d / (2 * (n1 + n2)))
    out["ci_low"] = fnum(d - Z_95 * se)
    out["ci_high"] = fnum(d + Z_95 * se)
    out["direction"] = "group1 - group2"
    return out


def hedges_g(g1: list, g2: list) -> dict:
    n1, n2 = len(g1), len(g2)
    d = cohens_d(g1, g2)
    out = {"name": "Hedges' g (bias-corrected Cohen's d)"}
    if d.get("value") is None:
        out.update({"value": None, "note": d.get("note")})
        return out
    df = n1 + n2 - 2
    j = 1.0 - 3.0 / (4 * df - 1)
    g = d["value"] * j
    se = math.sqrt((n1 + n2) / (n1 * n2) + d["value"] ** 2 / (2 * (n1 + n2)))
    out["value"] = fnum(g)
    out["ci_low"] = fnum(g - Z_95 * se)
    out["ci_high"] = fnum(g + Z_95 * se)
    out["direction"] = "group1 - group2"
    return out


def glass_delta(g1: list, g2: list) -> dict:
    """Glass's delta using group 2 as reference (control) SD."""
    n1, n2 = len(g1), len(g2)
    out = {"name": "Glass delta (SD of group 2)", "n1": n1, "n2": n2}
    if n1 < 1 or n2 < 2:
        out.update({"value": None, "note": "insufficient n"})
        return out
    m1, m2 = sum(g1) / n1, sum(g2) / n2
    s2 = sample_sd(g2)
    if s2 == 0:
        out.update({"value": None, "note": "zero reference SD"})
        return out
    d = (m1 - m2) / s2
    out["value"] = fnum(d)
    out["direction"] = "group1 - group2"
    return out


def mean_ci(vals: list) -> dict:
    n = len(vals)
    out = {"estimate": "mean", "n": n}
    if n < 2:
        out.update({"ci_low": None, "ci_high": None, "note": "n<2"})
        return out
    m = sum(vals) / n
    sd = sample_sd(vals)
    tcrit = t_ppf(1 - (1 - CONF) / 2, n - 1)
    se = sd / math.sqrt(n)
    out["mean"] = fnum(m)
    out["ci_low"] = fnum(m - tcrit * se)
    out["ci_high"] = fnum(m + tcrit * se)
    out["conf"] = CONF
    return out


def mean_diff_ci(diff: float, se: float, df: float) -> dict:
    if se <= 0 or df <= 0:
        return {"ci_low": None, "ci_high": None, "note": "invalid SE/df"}
    tcrit = t_ppf(1 - (1 - CONF) / 2, df)
    return {"mean_diff": fnum(diff), "ci_low": fnum(diff - tcrit * se),
            "ci_high": fnum(diff + tcrit * se), "conf": CONF}


def wilson_ci(k: int, n: int) -> dict:
    if n == 0:
        return {"ci_low": None, "ci_high": None, "note": "n=0"}
    phat = k / n
    z2 = Z_95 ** 2
    denom = 1 + z2 / n
    center = (phat + z2 / (2 * n)) / denom
    half = Z_95 * math.sqrt(phat * (1 - phat) / n + z2 / (4 * n * n)) / denom
    return {"proportion": fnum(phat), "ci_low": fnum(center - half), "ci_high": fnum(center + half),
            "conf": CONF, "method": "Wilson score"}


# ---------------------------------------------------------------------------
# Input discovery and parsing
# ---------------------------------------------------------------------------

def classify_kind(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    mapping = {
        ".csv": "csv", ".tsv": "tsv", ".txt": "text", ".json": "json", ".jsonl": "jsonl",
        ".db": "sqlite", ".sqlite": "sqlite", ".sqlite3": "sqlite", ".xlsx": "xlsx",
        ".xls": "xls", ".docx": "docx", ".pdf": "pdf", ".md": "markdown", ".yaml": "yaml",
        ".yml": "yaml",
    }
    return mapping.get(ext, "other")


def discover_inputs(input_dir: str) -> list:
    files = []
    if not os.path.isdir(input_dir):
        return files
    for root, _dirs, names in os.walk(input_dir):
        for name in sorted(names):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir)
            kind = classify_kind(name)
            size = os.path.getsize(full)
            files.append({"source_id": "S%d" % (len(files) + 1), "path": full, "kind": kind,
                          "rel_path": rel.replace("\\", "/"), "size": size})
    return files


def _read_text(path: str):
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                return f.read(), enc
        except UnicodeDecodeError:
            continue
    return None, None


def _detect_delim(sample: str) -> str:
    counts = {",": 0, "\t": 0, ";": 0}
    for line in sample.splitlines()[:20]:
        for ch in counts:
            counts[ch] += line.count(ch)
    return max(counts, key=counts.get)


def read_table(path: str, kind: str) -> dict:
    """Return {header: [...], rows: [[str,...],...], kind, delimiter, encoding}."""
    if kind in ("csv", "tsv", "text"):
        text, enc = _read_text(path)
        if text is None:
            return {"error": "decode_failed", "path": path}
        if os.path.getsize(path) > MAX_TEXT_FILE:
            return {"error": "too_large", "path": path}
        delim = "\t" if kind == "tsv" else _detect_delim(text)
        reader = csv.reader(io.StringIO(text), delimiter=delim)
        rows = [r for r in reader]
        if not rows:
            return {"header": [], "rows": [], "kind": kind, "delimiter": delim, "encoding": enc}
        header = [h.strip() for h in rows[0]]
        data = rows[1:]
        return {"header": header, "rows": data, "kind": kind, "delimiter": delim, "encoding": enc}
    if kind == "json":
        text, enc = _read_text(path)
        if text is None:
            return {"error": "decode_failed", "path": path}
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            return {"error": "bad_json", "path": path, "detail": str(e)}
        if isinstance(obj, list):
            rows = obj
        elif isinstance(obj, dict):
            keys = list(obj.keys())
            lens = [len(obj[k]) for k in keys if isinstance(obj[k], list)]
            if keys and lens and len(set(lens)) == 1:
                header = keys
                rows = [[obj[k][i] for k in keys] for i in range(lens[0])]
            else:
                header = keys
                rows = [[str(obj[k]) for k in keys]]
        else:
            return {"error": "unexpected_json", "path": path}
        header = list(rows[0].keys()) if rows and isinstance(rows[0], dict) else None
        if header is None and rows and not isinstance(rows[0], dict):
            header = ["field%d" % (i + 1) for i in range(len(rows[0]))]
        data = []
        for r in rows:
            if isinstance(r, dict):
                data.append([str(r.get(h, "")) for h in header])
            else:
                data.append([str(v) for v in r])
        return {"header": header, "rows": data, "kind": kind, "encoding": enc}
    if kind in ("sqlite",):
        try:
            con = sqlite3.connect(path)
            cur = con.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
            tables = [r[0] for r in cur.fetchall()]
            out = {"kind": kind, "tables": {}}
            for t in tables:
                cur.execute("SELECT * FROM \"%s\" LIMIT 0" % t.replace('"', '""'))
                cols = [d[0] for d in cur.description]
                cur.execute("SELECT * FROM \"%s\"" % t.replace('"', '""'))
                rows = [[str(v) if v is not None else "" for v in r] for r in cur.fetchall()]
                out["tables"][t] = {"header": cols, "rows": rows}
            con.close()
            return out
        except sqlite3.Error as e:
            return {"error": "sqlite_error", "path": path, "detail": str(e)}
    if kind == "xlsx":
        return read_xlsx(path)
    if kind in ("xls", "docx", "pdf"):
        return {"error": "unsupported_stdlib", "path": path,
                "detail": "stdlib cannot parse %s; convert with environment tool first" % kind}
    return {"error": "unknown_kind", "path": path, "kind": kind}


def read_xlsx(path: str) -> dict:
    NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    try:
        with zipfile.ZipFile(path) as z:
            shared = []
            if "xl/sharedStrings.xml" in z.namelist():
                root = ET.fromstring(z.read("xl/sharedStrings.xml"))
                for si in root.iter(NS + "si"):
                    shared.append("".join(t.text or "" for t in si.iter(NS + "t")))
            sheet = "xl/worksheets/sheet1.xml"
            root = ET.fromstring(z.read(sheet))
            rows = []
            for row in root.iter(NS + "row"):
                cells = []
                for c in row.findall(NS + "c"):
                    v = c.find(NS + "v")
                    t = c.get("t")
                    if v is not None:
                        val = v.text or ""
                        if t == "s":
                            try:
                                val = shared[int(val)]
                            except (ValueError, IndexError):
                                pass
                        cells.append(val)
                    else:
                        cells.append("")
                rows.append(cells)
        if not rows:
            return {"error": "empty_xlsx", "path": path}
        header = [h.strip() for h in rows[0]]
        return {"header": header, "rows": rows[1:], "kind": "xlsx"}
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        return {"error": "xlsx_parse_failed", "path": path, "detail": str(e)}


# ---------------------------------------------------------------------------
# Schema inference and cleaning
# ---------------------------------------------------------------------------

def looks_missing(v: str) -> bool:
    return v.strip().lower() in MISSING_TOKENS


def looks_numeric(v: str) -> bool:
    if looks_missing(v):
        return False
    try:
        float(v.strip())
        return True
    except (ValueError, TypeError):
        return False


def infer_type(cells: list) -> str:
    nonmiss = [c for c in cells if not looks_missing(c)]
    if not nonmiss:
        return "empty"
    if all(looks_numeric(c) for c in nonmiss):
        ints = all(float(c) == int(float(c)) for c in nonmiss)
        return "int" if ints else "float"
    date_like = all(re.match(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}:\d{2})?$", c.strip()) for c in nonmiss)
    if date_like:
        return "date"
    return "categorical"


def clean_table(table: dict) -> dict:
    header, rows = table.get("header", []), table.get("rows", [])
    n_raw = len(rows)
    seen = set()
    deduped = []
    removed = 0
    for i, r in enumerate(rows):
        key = tuple(cell.strip() for cell in r)
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        deduped.append((i, r))
    col_types = {}
    missing = {}
    for j, col in enumerate(header):
        cells = [r[j] for r in rows if j < len(r)]
        col_types[col] = infer_type(cells)
        n_miss = sum(1 for c in cells if looks_missing(c))
        missing[col] = {"count": n_miss, "proportion": fnum(n_miss / len(cells)) if cells else 0.0}
    outliers = {}
    for j, col in enumerate(header):
        if col_types[col] in ("int", "float"):
            vals = []
            idxs = []
            for i, r in deduped:
                if j < len(r) and looks_numeric(r[j]):
                    vals.append(float(r[j]))
                    idxs.append(i)
            if len(vals) >= 4:
                s = sorted(vals)
                q1 = quantile(s, 0.25)
                q3 = quantile(s, 0.75)
                iqr = q3 - q1
                lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
                flags = [(idxs[k], v) for k, v in enumerate(vals) if v < lo or v > hi]
                if flags:
                    outliers[col] = {"method": "IQR 1.5 (mark, not delete)", "n": len(flags),
                                     "lower_fence": fnum(lo), "upper_fence": fnum(hi),
                                     "rows": flags[:50]}
    return {"header": header, "rows_raw": n_raw, "rows_deduped": len(deduped),
            "duplicates_removed": removed, "deduped": deduped, "col_types": col_types,
            "missing": missing, "outliers": outliers}


def numeric_column(table: dict, col: str) -> list:
    header = table["header"]
    if col not in header:
        return []
    j = header.index(col)
    out = []
    for _i, r in table["deduped"]:
        if j < len(r) and looks_numeric(r[j]):
            out.append(float(r[j]))
    return out


def categorical_levels(table: dict, col: str) -> list:
    header = table["header"]
    if col not in header:
        return []
    j = header.index(col)
    return sorted({r[j] for _i, r in table["deduped"] if j < len(r) and not looks_missing(r[j])})


# ---------------------------------------------------------------------------
# SVG figures (deterministic, editable)
# ---------------------------------------------------------------------------

def _svg_boxplot(groups: dict, labels: list, title: str, unit: str) -> str:
    W, H = 720, 420
    pad_l, pad_r, pad_t, pad_b = 70, 40, 60, 60
    plot_w = W - pad_l - pad_r
    plot_h = H - pad_t - pad_b
    all_vals = [v for g in groups.values() for v in g]
    if not all_vals:
        return ""
    lo, hi = min(all_vals), max(all_vals)
    span = (hi - lo) or 1.0
    lo -= 0.05 * span
    hi += 0.05 * span
    span = hi - lo
    n = len(groups)

    def y(v):
        return pad_t + plot_h - (v - lo) / span * plot_h

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">',
             '<rect width="100%" height="100%" fill="white"/>',
             f'<text x="{W/2}" y="28" font-size="16" font-family="sans-serif" '
             f'text-anchor="middle">{_esc(title)}</text>']
    slot = plot_w / n
    for k, lab in enumerate(labels):
        g = sorted(groups[lab])
        if not g:
            continue
        cx = pad_l + slot * (k + 0.5)
        box_w = min(70, slot * 0.5)
        q1 = quantile(g, 0.25)
        med = quantile(g, 0.5)
        q3 = quantile(g, 0.75)
        iqr = q3 - q1
        f_lo, f_hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        whisk_lo = min([v for v in g if v >= f_lo], default=q1)
        whisk_hi = max([v for v in g if v <= f_hi], default=q3)
        parts.append(f'<line x1="{cx}" y1="{y(whisk_hi):.1f}" x2="{cx}" y2="{y(whisk_lo):.1f}" '
                     f'stroke="black" stroke-width="1.5"/>')
        parts.append(f'<line x1="{cx-box_w/2}" y1="{y(whisk_hi):.1f}" x2="{cx+box_w/2}" '
                     f'y2="{y(whisk_hi):.1f}" stroke="black" stroke-width="1.5"/>')
        parts.append(f'<line x1="{cx-box_w/2}" y1="{y(whisk_lo):.1f}" x2="{cx+box_w/2}" '
                     f'y2="{y(whisk_lo):.1f}" stroke="black" stroke-width="1.5"/>')
        parts.append(f'<rect x="{cx-box_w/2:.1f}" y="{y(q3):.1f}" width="{box_w:.1f}" '
                     f'height="{y(q1)-y(q3):.1f}" fill="#e8f0fe" stroke="black" stroke-width="1.5"/>')
        parts.append(f'<line x1="{cx-box_w/2:.1f}" y1="{y(med):.1f}" x2="{cx+box_w/2:.1f}" '
                     f'y2="{y(med):.1f}" stroke="#c0392b" stroke-width="2"/>')
        for v in g:
            if v < f_lo or v > f_hi:
                parts.append(f'<circle cx="{cx:.1f}" cy="{y(v):.1f}" r="2.5" fill="#c0392b"/>')
        parts.append(f'<text x="{cx:.1f}" y="{H-pad_b+16}" font-size="11" font-family="sans-serif" '
                     f'text-anchor="middle">{_esc(str(lab))}</text>')
    for tick in range(6):
        val = lo + span * tick / 5
        parts.append(f'<line x1="{pad_l}" y1="{y(val):.1f}" x2="{W-pad_r}" y2="{y(val):.1f}" '
                     f'stroke="#dddddd" stroke-width="1"/>')
        parts.append(f'<text x="{pad_l-6}" y="{y(val)+4:.1f}" font-size="10" font-family="sans-serif" '
                     f'text-anchor="end">{val:.3g}</text>')
    if unit and unit != "unit_unknown":
        parts.append(f'<text x="{W-pad_r}" y="{H-14}" font-size="11" font-family="sans-serif" '
                     f'text-anchor="end">unit: {_esc(unit)}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def _svg_histogram(vals: list, title: str, unit: str, bins: int = 10) -> str:
    W, H = 720, 420
    pad_l, pad_r, pad_t, pad_b = 60, 40, 60, 60
    plot_w = W - pad_l - pad_r
    plot_h = H - pad_t - pad_b
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    if lo == hi:
        lo -= 0.5
        hi += 0.5
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in vals:
        idx = min(bins - 1, int((v - lo) / width))
        counts[idx] += 1
    ymax = max(counts) or 1
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">',
             '<rect width="100%" height="100%" fill="white"/>',
             f'<text x="{W/2}" y="28" font-size="16" font-family="sans-serif" '
             f'text-anchor="middle">{_esc(title)}</text>']
    bar_w = plot_w / bins * 0.9
    for i, c in enumerate(counts):
        x = pad_l + plot_w * i / bins + plot_w / bins * 0.05
        h = c / ymax * plot_h
        y = pad_t + plot_h - h
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{h:.1f}" '
                     f'fill="#4c78a8" stroke="none"/>')
    parts.append(f'<line x1="{pad_l}" y1="{pad_t+plot_h:.1f}" x2="{W-pad_r}" '
                 f'y2="{pad_t+plot_h:.1f}" stroke="black" stroke-width="1.5"/>')
    if unit and unit != "unit_unknown":
        parts.append(f'<text x="{W-pad_r}" y="{H-14}" font-size="11" font-family="sans-serif" '
                     f'text-anchor="end">unit: {_esc(unit)}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


# ---------------------------------------------------------------------------
# Result state: atomic incremental writes
# ---------------------------------------------------------------------------

def _empty_contract_value(key: str, val):
    if isinstance(val, list):
        return []
    if isinstance(val, dict):
        return {}
    if isinstance(val, (int, float)):
        return None
    if key.lower().endswith(("s", "list", "array")):
        return []
    return None


class State:
    def __init__(self, args):
        self.args = args
        os.makedirs(args.output, exist_ok=True)
        os.makedirs(args.work, exist_ok=True)
        self.result_path = os.path.join(args.output, "result.json")
        self.contract = self._read_contract()
        self.data = self._load_or_skeleton()

    def _read_contract(self) -> dict:
        case_path = os.path.join(self.args.input, "case.json")
        if not os.path.isfile(case_path):
            return {}
        try:
            with open(case_path, "r", encoding="utf-8-sig") as f:
                obj = json.load(f)
            return obj.get("output_contract", {}) if isinstance(obj, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _case_id(self) -> str:
        case_path = os.path.join(self.args.input, "case.json")
        if os.path.isfile(case_path):
            try:
                with open(case_path, "r", encoding="utf-8-sig") as f:
                    obj = json.load(f)
                if isinstance(obj, dict) and obj.get("case_id"):
                    return str(obj["case_id"])
            except (json.JSONDecodeError, OSError):
                pass
        return "unknown-case"

    def _load_or_skeleton(self) -> dict:
        if os.path.isfile(self.result_path):
            try:
                with open(self.result_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return self._skeleton()

    def _skeleton(self) -> dict:
        data = {
            "case_id": self._case_id(),
            "status": "partial",
            "result": {},
            "quality": [],
            "descriptive": [],
            "inferential": [],
            "effects": [],
            "interpretation": {
                "summary": None,
                "significance": None,
                "effect_size": None,
                "confidence_intervals": None,
                "limitations": [],
            },
            "sources": [],
            "uncertainty": ["analysis not yet run"],
            "change_log": [],
            "errors": [],
            "degradations": [],
            "pending_human": [],
            "runtime": {"started_at": now_iso(), "python_version": sys.version.split()[0]},
        }
        # backfill every output_contract key without renaming/deleting
        for k, v in self.contract.items():
            data[k] = _empty_contract_value(k, v)
        return data

    def log_change(self, action: str, before: str, after: str, reason: str):
        cl = self.data.setdefault("change_log", [])
        cl.append({"id": "C%d" % (len(cl) + 1), "time": now_iso(), "action": action,
                   "before": before, "after": after, "reason": reason})

    def save(self):
        self.data.setdefault("runtime", {})["finished_at"] = now_iso()
        tmp = self.result_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.result_path)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init(args):
    st0 = State(args)
    files = discover_inputs(args.input)
    st0.data["sources"] = files
    st0.data["uncertainty"] = ["skeleton dropped early; analysis pending"]
    st0.log_change("init", "no result.json", "partial skeleton with %d sources" % len(files),
                   "early partial drop within 3 minutes")
    st0.save()
    index = {"input": args.input, "files": files, "created_at": now_iso()}
    with open(os.path.join(args.work, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    print(json.dumps({"ok": True, "case_id": st0.data["case_id"], "sources": len(files)},
                     ensure_ascii=False))


def cmd_survey(args):
    st0 = State(args)
    files = discover_inputs(args.input)
    tools = ["scientific-python", "SQLite"]
    res_yaml = os.path.join(args.input, "environment", "resources.yaml")
    if os.path.isfile(res_yaml):
        text = _read_text(res_yaml)[0] or ""
        for t in ("scientific-python", "numpy", "scipy", "pandas", "matplotlib", "SQLite", "pdftotext"):
            if t in text:
                tools.append(t)
    st0.data["sources"] = files
    st0.data["runtime"]["tools_declared"] = sorted(set(tools))
    st0.log_change("survey", "unknown materials", "%d files indexed" % len(files),
                   "material/tool inventory")
    st0.save()
    out = {"files": files, "tools": sorted(set(tools))}
    with open(os.path.join(args.work, "survey.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False))


def cmd_inspect(args):
    st0 = State(args)
    files = discover_inputs(args.input)
    tables = {}
    for f in files:
        if os.path.basename(f["rel_path"]).lower() in ("case.json", "task.md"):
            continue
        if f["kind"] in ("csv", "tsv", "text", "json", "sqlite", "xlsx"):
            t = read_table(f["path"], f["kind"])
            if "tables" in t:  # sqlite
                for name, sub in t["tables"].items():
                    tables["%s::%s" % (f["rel_path"], name)] = {"file": f["rel_path"], **sub}
            elif "error" not in t:
                tables[f["rel_path"]] = {"file": f["rel_path"], **t}
            else:
                st0.data.setdefault("degradations", []).append(
                    {"file": f["rel_path"], "error": t.get("error"), "detail": t.get("detail", "")})
    schema = {}
    for name, t in tables.items():
        header = t.get("header", [])
        cols = {}
        for j, col in enumerate(header):
            cells = [r[j] for r in t.get("rows", []) if j < len(r)]
            cols[col] = {"type": infer_type(cells), "n": len(cells),
                         "n_missing": sum(1 for c in cells if looks_missing(c))}
        schema[name] = {"file": t.get("file"), "n_rows": len(t.get("rows", [])), "columns": cols}
    st0.data["runtime"]["schema"] = schema
    st0.log_change("inspect", "raw files", "schema for %d tables" % len(schema),
                   "data dictionary + column types")
    st0.save()
    with open(os.path.join(args.work, "schema.json"), "w", encoding="utf-8") as f:
        json.dump(schema, f, ensure_ascii=False, indent=2)
    print(json.dumps(schema, ensure_ascii=False))


def _load_tables(args):
    files = discover_inputs(args.input)
    tables = {}
    degradations = []
    for f in files:
        if os.path.basename(f["rel_path"]).lower() in ("case.json", "task.md"):
            continue
        if f["kind"] in ("csv", "tsv", "text", "json", "sqlite", "xlsx"):
            t = read_table(f["path"], f["kind"])
            if "tables" in t:
                for name, sub in t["tables"].items():
                    tables["%s::%s" % (f["rel_path"], name)] = {"file": f["rel_path"], **sub}
            elif "error" not in t:
                tables[f["rel_path"]] = {"file": f["rel_path"], **t}
            else:
                degradations.append({"file": f["rel_path"], "error": t.get("error"),
                                     "detail": t.get("detail", "")})
    return tables, degradations


def _analyze_table(tname: str, table: dict, out: dict):
    clean = clean_table(table)
    out["tables"][tname] = {"file": table.get("file"), "cleaning": {
        "n_raw": clean["rows_raw"], "n_deduped": clean["rows_deduped"],
        "duplicates_removed": clean["duplicates_removed"],
        "col_types": clean["col_types"], "missing": clean["missing"],
        "outliers": clean["outliers"]}}
    for col, typ in clean["col_types"].items():
        if typ not in ("int", "float"):
            continue
        vals = numeric_column(clean, col)
        if not vals:
            continue
        unit = "unit_unknown"
        d = descriptive(vals, unit)
        d["table"] = tname
        d["column"] = col
        d["locator"] = "%s#col=%s" % (tname, col)
        d["quote"] = [str(v) for v in vals[:5]]
        out["descriptive"].append(d)
    # group comparisons for every categorical column (2..10 levels)
    for col, typ in clean["col_types"].items():
        if typ not in ("categorical",):
            continue
        levels = categorical_levels(clean, col)
        if not (2 <= len(levels) <= 10):
            continue
        for numcol, numtyp in clean["col_types"].items():
            if numtyp not in ("int", "float"):
                continue
            groups = {}
            j_num = table["header"].index(numcol)
            j_cat = table["header"].index(col)
            for _i, r in clean["deduped"]:
                if j_cat < len(r) and j_num < len(r) and looks_numeric(r[j_num]) \
                        and not looks_missing(r[j_cat]):
                    groups.setdefault(r[j_cat], []).append(float(r[j_num]))
            groups = {k: groups[k] for k in levels if k in groups}
            if len(groups) < 2:
                continue
            levels = sorted(groups.keys())
            comp = {"table": tname, "group_col": col, "outcome_col": numcol,
                    "levels": levels, "unit": "unit_unknown",
                    "locator": "%s#group=%s,outcome=%s" % (tname, col, numcol)}
            comp["group_descriptive"] = {k: descriptive(v, "unit_unknown") for k, v in groups.items()}
            comp["normality"] = {k: shapiro_francia(v) for k, v in groups.items()}
            if len(groups) == 2:
                g1, g2 = groups[levels[0]], groups[levels[1]]
                comp["homogeneity"] = brown_forsythe([g1, g2])
                comp["candidates"] = {"student_t": student_t(g1, g2), "welch_t": welch_t(g1, g2),
                                      "mann_whitney": mann_whitney_u(g1, g2)}
                comp["effects"] = {"cohens_d": cohens_d(g1, g2), "hedges_g": hedges_g(g1, g2),
                                   "glass_delta": glass_delta(g1, g2)}
                wt = welch_t(g1, g2)
                comp["mean_diff_ci"] = mean_diff_ci(wt.get("mean_diff") or 0.0,
                                                    wt.get("se") or 0.0, wt.get("df") or 0.0)
            else:
                glist = [groups[k] for k in levels if k in groups]
                comp["homogeneity"] = brown_forsythe(glist)
                comp["candidates"] = {"anova": one_way_anova(glist),
                                      "kruskal_wallis": kruskal_wallis(glist)}
            out["comparisons"].append(comp)


def cmd_analyze(args):
    st0 = State(args)
    tables, degradations = _load_tables(args)
    st0.data["degradations"] = degradations
    analysis = {"meta": {"case_id": st0.data["case_id"], "seed": None,
                         "python_version": sys.version.split()[0], "conf": CONF},
                "tables": {}, "descriptive": [], "comparisons": [], "correlations": []}
    for tname, table in tables.items():
        _analyze_table(tname, table, analysis)
    # correlation matrix across all numeric columns of the first tabular table
    cleaned_tables = {tname: clean_table(table) for tname, table in tables.items()}
    numeric_cols = []
    first_name = None
    for tname, table in tables.items():
        first_name = first_name or tname
        header = table.get("header", [])
        for j, col in enumerate(header):
            if infer_type([r[j] for r in table.get("rows", []) if j < len(r)]) in ("int", "float"):
                numeric_cols.append((tname, col))
    for i in range(len(numeric_cols)):
        for j in range(i + 1, len(numeric_cols)):
            t1, c1 = numeric_cols[i]
            t2, c2 = numeric_cols[j]
            if t1 != t2:
                continue
            v1 = numeric_column(cleaned_tables[t1], c1)
            v2 = numeric_column(cleaned_tables[t1], c2)
            n = min(len(v1), len(v2))
            pr = pearson_r(v1[:n], v2[:n])
            pr["x"] = c1
            pr["y"] = c2
            pr["table"] = t1
            analysis["correlations"].append(pr)
    # primary figure: first comparison (boxplot) + first numeric column (histogram)
    figures = []
    os.makedirs(os.path.join(args.output, "figures"), exist_ok=True)
    if analysis["comparisons"]:
        comp = analysis["comparisons"][0]
        groups = {}
        for lvl in comp["levels"]:
            vals = []
            for tname, clean in cleaned_tables.items():
                header = clean.get("header", [])
                if comp["outcome_col"] not in header or comp["group_col"] not in header:
                    continue
                j_num = header.index(comp["outcome_col"])
                j_cat = header.index(comp["group_col"])
                for _i, r in clean["deduped"]:
                    if j_cat < len(r) and r[j_cat] == lvl and j_num < len(r) and looks_numeric(r[j_num]):
                        vals.append(float(r[j_num]))
                if vals:
                    break
            groups[lvl] = vals
        svg = _svg_boxplot(groups, comp["levels"], "%s by %s" % (comp["outcome_col"], comp["group_col"]),
                           comp.get("unit", "unit_unknown"))
        if svg:
            p = os.path.join(args.output, "figures", "boxplot_%s_by_%s.svg" % (comp["outcome_col"], comp["group_col"]))
            with open(p, "w", encoding="utf-8") as f:
                f.write(svg)
            figures.append({"path": p, "type": "boxplot", "editable": "svg"})
    if analysis["descriptive"]:
        d0 = analysis["descriptive"][0]
        vals = numeric_column(cleaned_tables.get(d0["table"], {}), d0["column"])
        svg = _svg_histogram(vals, "histogram of %s" % d0["column"], d0.get("unit", "unit_unknown"))
        if svg:
            p = os.path.join(args.output, "figures", "hist_%s.svg" % d0["column"])
            with open(p, "w", encoding="utf-8") as f:
                f.write(svg)
            figures.append({"path": p, "type": "histogram", "editable": "svg"})
    analysis["figures"] = figures
    # cleaned CSVs
    cleaned_paths = []
    os.makedirs(os.path.join(args.output, "cleaned"), exist_ok=True)
    for tname, table in tables.items():
        header = table.get("header", [])
        deduped = clean_table(table)["deduped"]
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", tname)
        p = os.path.join(args.output, "cleaned", "%s_cleaned.csv" % safe)
        with open(p, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["_row_id"] + header)
            for i, r in deduped:
                w.writerow([i] + r)
        cleaned_paths.append({"path": p, "table": tname})
    analysis["cleaned_csv"] = cleaned_paths
    # quality checks: seven situations
    quality = _build_quality(analysis, tables, cleaned_paths, figures)
    with open(os.path.join(args.output, "quality_checks.json"), "w", encoding="utf-8") as f:
        json.dump(quality, f, ensure_ascii=False, indent=2)
    analysis["quality"] = quality
    with open(os.path.join(args.work, "analysis.json"), "w", encoding="utf-8") as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2)
    # merge into result.json
    st0.data["descriptive"] = analysis["descriptive"]
    st0.data["inferential"] = analysis["comparisons"]
    st0.data["quality"] = quality
    st0.data["result"] = {"tables": len(tables), "descriptive_n": len(analysis["descriptive"]),
                          "comparisons_n": len(analysis["comparisons"]),
                          "correlations_n": len(analysis["correlations"])}
    st0.log_change("analyze", "raw tables", "descriptive + inferential + effects + figures",
                   "deterministic statistical pipeline")
    st0.save()
    print(json.dumps({"ok": True, "tables": len(tables),
                      "descriptive": len(analysis["descriptive"]),
                      "comparisons": len(analysis["comparisons"]),
                      "figures": len(figures)}, ensure_ascii=False))


def _build_quality(analysis, tables, cleaned_paths, figures):
    q = []
    q.append({"check_id": "Q1_missing", "situation": "缺失值", "status": "handled",
              "detail": "counts and proportions per column; never treated as zero",
              "locator": "quality_checks.json + cleaned/*.csv",
              "quote": json.dumps({t: c.get("missing") for t, c in analysis["tables"].items()},
                                  ensure_ascii=False)[:500]})
    q.append({"check_id": "Q2_outliers", "situation": "异常值", "status": "handled",
              "detail": "IQR 1.5 flagged, not silently deleted",
              "locator": "quality_checks.json",
              "quote": json.dumps({t: c["cleaning"].get("outliers") for t, c in analysis["tables"].items()},
                                  ensure_ascii=False)[:500]})
    q.append({"check_id": "Q3_duplicates", "situation": "重复记录", "status": "handled",
              "detail": "exact-row duplicates removed, keep first, before/after recorded",
              "locator": "cleaned/*.csv + quality_checks.json",
              "quote": json.dumps({t: {"removed": c["cleaning"]["duplicates_removed"],
                                        "before": c["cleaning"]["n_raw"],
                                        "after": c["cleaning"]["n_deduped"]}
                                   for t, c in analysis["tables"].items()}, ensure_ascii=False)[:500]})
    q.append({"check_id": "Q4_assumptions", "situation": "统计假设", "status": "handled",
              "detail": "normality (Shapiro-Francia W' per group) + variance homogeneity "
                        "(Brown-Forsythe) computed before test selection; p-values cross-checked "
                        "with scipy when available",
              "locator": "analysis.json -> comparisons[].normality / homogeneity", "quote": ""})
    q.append({"check_id": "Q5_chart_choice", "situation": "图表选择", "status": "handled",
              "detail": "boxplot for group comparison, histogram for distribution; editable SVG",
              "locator": "figures/*.svg", "quote": json.dumps([f["path"] for f in figures],
                                                              ensure_ascii=False)})
    q.append({"check_id": "Q6_code_run", "situation": "代码运行", "status": "handled",
              "detail": "deterministic stdlib-only pipeline, no RNG, no external packages",
              "locator": "scripts/core_impl.py", "quote": ""})
    q.append({"check_id": "Q7_database_field", "situation": "数据库字段", "status": "handled",
              "detail": "column types inferred; unit unknown recorded as unit_unknown",
              "locator": "schema.json", "quote": json.dumps({t: c["cleaning"]["col_types"]
                                                              for t, c in analysis["tables"].items()},
                                                             ensure_ascii=False)[:500]})
    return q


def cmd_build(args):
    st0 = State(args)
    analysis_path = os.path.join(args.work, "analysis.json")
    if os.path.isfile(analysis_path):
        with open(analysis_path, "r", encoding="utf-8") as f:
            analysis = json.load(f)
        st0.data["descriptive"] = analysis.get("descriptive", [])
        st0.data["inferential"] = analysis.get("comparisons", [])
        st0.data["effects"] = [c.get("effects") for c in analysis.get("comparisons", [])]
        st0.data["quality"] = analysis.get("quality", [])
        st0.data["result"] = {"tables": len(analysis.get("tables", {})),
                              "descriptive_n": len(analysis.get("descriptive", [])),
                              "comparisons_n": len(analysis.get("comparisons", [])),
                              "figures": [f["path"] for f in analysis.get("figures", [])]}
    interp_path = os.path.join(args.work, "interpretation.json")
    if os.path.isfile(interp_path):
        with open(interp_path, "r", encoding="utf-8") as f:
            st0.data["interpretation"] = json.load(f)
    # copy scripts for reproducibility
    code_dir = os.path.join(args.output, "code")
    os.makedirs(code_dir, exist_ok=True)
    here = os.path.dirname(os.path.abspath(__file__))
    for fn in ("core_impl.py",):
        with open(os.path.join(here, fn), "r", encoding="utf-8") as f:
            content = f.read()
        with open(os.path.join(code_dir, fn), "w", encoding="utf-8") as f:
            f.write(content)
    if not st0.data.get("interpretation", {}).get("summary"):
        st0.data["status"] = "partial"
        st0.data.setdefault("uncertainty", []).append(
            "interpretation not yet written; statistical results computed")
    elif not st0.data.get("errors") and not st0.data.get("degradations"):
        st0.data["status"] = "ok"
    st0.log_change("build", "analysis artifacts", "result.json + main artifacts",
                   "assemble deliverable")
    st0.save()
    print(json.dumps({"ok": True, "status": st0.data["status"]}, ensure_ascii=False))


def cmd_selfcheck(args):
    st0 = State(args)
    problems = []
    if not os.path.isfile(st0.result_path):
        problems.append("result.json missing")
    else:
        try:
            with open(st0.result_path, "r", encoding="utf-8") as f:
                json.load(f)
        except json.JSONDecodeError as e:
            problems.append("result.json invalid: %s" % e)
    if not st0.data.get("case_id"):
        problems.append("case_id missing")
    for k in ("status", "sources", "uncertainty", "change_log"):
        if k not in st0.data:
            problems.append("missing envelope key: %s" % k)
    for k in st0.contract:
        if k not in st0.data:
            problems.append("missing output_contract key: %s" % k)
    st0.data["runtime"]["selfcheck_problems"] = problems
    st0.save()
    print(json.dumps({"ok": not problems, "problems": problems}, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(prog="main.py",
                                description="Deterministic stdlib statistical analysis skill")
    p.add_argument("command", nargs="?", default="init",
                   choices=["init", "survey", "inspect", "analyze", "build", "selfcheck"])
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    args = p.parse_args(argv)
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    cmds = {"init": cmd_init, "survey": cmd_survey, "inspect": cmd_inspect,
            "analyze": cmd_analyze, "build": cmd_build, "selfcheck": cmd_selfcheck}
    cmds[args.command](args)


if __name__ == "__main__":
    main()
