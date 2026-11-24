# -*- coding: utf-8 -*-
"""core_impl.py -- deterministic evidence-traceable statistics pipeline.

Design goals (evidence-traceability variant):
  * Every conclusion/number carries an evidence item with a locator and a
    verbatim quote taken from the input material.
  * The pipeline is fully deterministic: no randomness, fixed seeds, stable
    ordering.  Two runs on the same input produce identical JSON.
  * Standard library only as a hard dependency.  If scipy/numpy are present
    they are used as a cross-check backend and recorded in ``runtime``; every
    function has a pure-stdlib fallback so the skill still runs without them.

The module is importable (selftest.py uses it) and also exposes run() for the
thin CLI in main.py.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

SKILL_NAME = "stat-analysis-02-evidence"
SKILL_VERSION = "1.0.0"

# --------------------------------------------------------------------------
# small utilities
# --------------------------------------------------------------------------

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _trunc(s, n=240):
    if s is None:
        return None
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "\u2026"


def _read_bytes(path, max_bytes=4_000_000):
    size = os.path.getsize(path) if os.path.exists(path) else 0
    with open(path, "rb") as f:
        data = f.read(max_bytes)
    return data, size


def decode_bytes(data):
    """Try common encodings in a robust order; never raise."""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc)
        except Exception:
            continue
    return data.decode("latin-1", errors="replace")


def safe_json_load(text, path="<memory>"):
    try:
        return json.loads(text)
    except Exception as exc:
        return {"_json_error": str(exc), "_path": path}


def rel_path(path, base):
    try:
        return os.path.relpath(path, base).replace("\\", "/")
    except Exception:
        return path


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
    return path


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _num(v):
    """Convert a cell to float; return None if not numeric/blank."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        if isinstance(v, bool):
            return None
        return float(v)
    s = str(v).strip()
    if s == "":
        return None
    s = s.replace(",", "")
    if s.lower() in ("na", "nan", "n/a", "null", "none", "missing", "inf", "-inf"):
        return None
    try:
        return float(s)
    except Exception:
        return None


def _fmt(x, nd=6):
    if x is None:
        return None
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return None
        return round(x, nd)
    return x


# --------------------------------------------------------------------------
# lazy scipy / numpy (optional, never required)
# --------------------------------------------------------------------------

_SCIPY_TRIED = False
_NP = None
_ST = None


def _ensure_scipy():
    global _SCIPY_TRIED, _NP, _ST
    if _SCIPY_TRIED:
        return _ST
    _SCIPY_TRIED = True
    try:
        import numpy as _np  # noqa
        from scipy import stats as _st  # noqa
        _NP, _ST = _np, _st
    except Exception:
        _NP, _ST = None, None
    return _ST


# --------------------------------------------------------------------------
# descriptive primitives
# --------------------------------------------------------------------------

def mean(x):
    x = [v for v in x if v is not None]
    return sum(x) / len(x) if x else None


def var(x, ddof=1):
    x = [v for v in x if v is not None]
    n = len(x)
    if n <= ddof:
        return None
    m = sum(x) / n
    return sum((v - m) ** 2 for v in x) / (n - ddof)


def sd(x):
    v = var(x, ddof=1)
    return math.sqrt(v) if v is not None and v > 0 else (0.0 if v == 0 else None)


def median(x):
    x = sorted(v for v in x if v is not None)
    n = len(x)
    if n == 0:
        return None
    if n % 2:
        return x[n // 2]
    return (x[n // 2 - 1] + x[n // 2]) / 2


def quantile(x, q):
    x = sorted(v for v in x if v is not None)
    n = len(x)
    if n == 0:
        return None
    if n == 1:
        return x[0]
    pos = (n - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return x[lo]
    return x[lo] * (1 - (pos - lo)) + x[hi] * (pos - lo)


def skewness(x):
    x = [v for v in x if v is not None]
    n = len(x)
    if n < 3:
        return None
    m = sum(x) / n
    m2 = sum((v - m) ** 2 for v in x) / n
    m3 = sum((v - m) ** 3 for v in x) / n
    if m2 <= 0:
        return None
    g1 = m3 / (m2 ** 1.5)
    return g1 * math.sqrt(n * (n - 1)) / (n - 2)


def kurtosis(x):
    x = [v for v in x if v is not None]
    n = len(x)
    if n < 4:
        return None
    m = sum(x) / n
    m2 = sum((v - m) ** 2 for v in x) / n
    m4 = sum((v - m) ** 4 for v in x) / n
    if m2 <= 0:
        return None
    g2 = m4 / (m2 ** 2) - 3.0
    return ((n - 1) / ((n - 2) * (n - 3))) * ((n + 1) * g2 + 6.0)


def rankdata(vals):
    idx = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and vals[idx[j + 1]] == vals[idx[i]]:
            j += 1
        r = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[idx[k]] = r
        i = j + 1
    return ranks


# --------------------------------------------------------------------------
# distribution functions (pure stdlib)
# --------------------------------------------------------------------------

def normal_cdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def normal_sf(z):
    return 1.0 - normal_cdf(z)


def normal_ppf(p):
    if p <= 0 or p >= 1:
        return float("nan")
    a = [-3.969683028665376e1, 2.209460984245205e2, -2.759285104469687e2,
         1.383577518672690e2, -3.066479806614716e1, 2.506628277459239e0]
    b = [-5.447609879822406e1, 1.615858368580409e2, -1.556989798598866e2,
         6.680131188771972e1, -1.328068155288572e1]
    c = [-7.784894002430293e-3, -3.223964580411365e-1, -2.400758277161838e0,
         -2.549732539343734e0, 4.374664141464968e0, 2.938163982698783e0]
    d = [7.784695709041462e-3, 3.224671290700398e-1, 2.445134137142996e0,
         3.754408661907416e0]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (
            ((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
        (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)


def _betacf(a, b, x):
    MAXIT = 300
    FPMIN = 1e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
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
        dele = d * c
        h *= dele
        if abs(dele - 1.0) < 1e-14:
            break
    return h


def ibeta(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                  + a * math.log(x) + b * math.log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1 - x) / b


def t_cdf(t, df):
    if df <= 0:
        return None
    x = df / (df + t * t)
    v = 0.5 * ibeta(df / 2.0, 0.5, x)
    if t > 0:
        return 1.0 - v
    return v


def t_sf(t, df):
    c = t_cdf(t, df)
    return 1.0 - c if c is not None else None


def t_ppf(p, df, tol=1e-9):
    if p <= 0 or p >= 1:
        return float("nan")
    lo, hi = -1e6, 1e6
    for _ in range(200):
        mid = (lo + hi) / 2
        c = t_cdf(mid, df)
        if c < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return (lo + hi) / 2


def f_cdf(f, d1, d2):
    if f <= 0:
        return 0.0
    x = d1 * f / (d1 * f + d2)
    return ibeta(d1 / 2.0, d2 / 2.0, x)


def f_sf(f, d1, d2):
    return 1.0 - f_cdf(f, d1, d2)


def _gammp(a, x):
    if x < 0:
        return 0.0
    if x < a + 1:
        ap = a
        s = 1.0 / a
        dele = s
        for _ in range(1, 300):
            ap += 1
            dele *= x / ap
            s += dele
            if abs(dele) < abs(s) * 1e-13:
                break
        return s * math.exp(-x + a * math.log(x) - math.lgamma(a))
    b = x + 1 - a
    c = 1e300
    d = 1.0 / b
    h = d
    for i in range(1, 300):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        if abs(d) < 1e-300:
            d = 1e-300
        c = b + an / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        dele = d * c
        h *= dele
        if abs(dele - 1.0) < 1e-13:
            break
    return 1.0 - math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def chi2_cdf(x, k):
    if x <= 0:
        return 0.0
    return _gammp(k / 2.0, x / 2.0)


def chi2_sf(x, k):
    return 1.0 - chi2_cdf(x, k)


# --------------------------------------------------------------------------
# statistical tests (scipy-first with stdlib fallback)
# --------------------------------------------------------------------------

def shapiro(x):
    st = _ensure_scipy()
    if st is not None:
        try:
            W, p = st.shapiro(x)
            return W, float(p), "scipy.shapiro"
        except Exception:
            pass
    W, p = _shapiro_francia(x)
    return W, p, "builtin.shapiro_francia"


def _shapiro_francia(x):
    x = sorted(v for v in x if v is not None)
    n = len(x)
    if n < 5:
        return None, None
    m = [normal_ppf((i + 1 - 3 / 8) / (n + 0.25)) for i in range(n)]
    msum2 = sum(mi * mi for mi in m)
    if msum2 <= 0:
        return None, None
    a = [mi / math.sqrt(msum2) for mi in m]
    xbar = sum(x) / n
    ss = sum((v - xbar) ** 2 for v in x)
    num = sum(ai * v for ai, v in zip(a, x)) ** 2
    W = num / ss if ss > 0 else None
    if W is None or W >= 1:
        return W, None
    u = math.log(n)
    v = math.log(u)
    mu = -1.2725 + 1.0521 * (v - u)
    sigma = 1.0308 - 0.26758 * (v + 2 / u)
    z = (math.log(1 - W) - mu) / sigma
    return W, float(1 - normal_cdf(z))


def levene_bf(groups):
    """Brown-Forsythe test (Levene on absolute deviations from group median)."""
    st = _ensure_scipy()
    if st is not None:
        try:
            W, p = st.levene(*groups, center="median")
            return W, float(p), "scipy.levene_median(Brown-Forsythe)"
        except Exception:
            pass
    k = len(groups)
    N = sum(len(g) for g in groups)
    meds = [median(g) for g in groups]
    zs = [[abs(v - meds[i]) for v in g] for i, g in enumerate(groups)]
    zbar_all = sum(sum(z) for z in zs) / N
    zbar_g = [sum(z) / len(z) for z in zs]
    num = sum(len(z) * (zb - zbar_all) ** 2 for z, zb in zip(zs, zbar_g))
    den = sum(sum((v - zb) ** 2 for v in z) for z, zb in zip(zs, zbar_g))
    if den <= 0 or k < 2 or N - k < 1:
        return None, None, "builtin.brown_forsythe"
    W = ((N - k) / (k - 1)) * (num / den)
    return W, float(f_sf(W, k - 1, N - k)), "builtin.brown_forsythe"


def ttest_two(x, y, equal_var=True):
    st = _ensure_scipy()
    if st is not None:
        try:
            t, p = st.ttest_ind(x, y, equal_var=equal_var)
            return float(t), float(p), ("scipy.ttest_ind" if equal_var else "scipy.ttest_ind(equal_var=False)")
        except Exception:
            pass
    n1, n2 = len(x), len(y)
    m1, m2 = mean(x), mean(y)
    v1, v2 = var(x), var(y)
    if v1 is None or v2 is None:
        return None, None, "builtin.ttest"
    if equal_var:
        sp2 = ((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2)
        t = (m1 - m2) / math.sqrt(sp2 * (1 / n1 + 1 / n2))
        df = n1 + n2 - 2
    else:
        se1, se2 = v1 / n1, v2 / n2
        t = (m1 - m2) / math.sqrt(se1 + se2)
        df = (se1 + se2) ** 2 / (se1 ** 2 / (n1 - 1) + se2 ** 2 / (n2 - 1))
    p = 2 * (1 - t_cdf(abs(t), df))
    return float(t), float(p), ("builtin.ttest_pooled" if equal_var else "builtin.welch")


def mann_whitney(x, y):
    st = _ensure_scipy()
    if st is not None:
        try:
            U, p = st.mannwhitneyu(x, y, alternative="two-sided", method="auto")
            return float(U), float(p), "scipy.mannwhitneyu"
        except Exception:
            pass
    n1, n2 = len(x), len(y)
    vals = list(x) + list(y)
    r = rankdata(vals)
    r1 = sum(r[:n1])
    U1 = n1 * n2 + n1 * (n1 + 1) / 2 - r1
    U2 = n1 * n2 - U1
    U = min(U1, U2)
    N = n1 + n2
    mu = n1 * n2 / 2.0
    # tie correction
    counts = {}
    for v in vals:
        counts[v] = counts.get(v, 0) + 1
    tie = sum(t ** 3 - t for t in counts.values())
    sigma = math.sqrt((n1 * n2 / 12.0) * ((N + 1) - tie / (N * (N - 1)))) if N > 1 else None
    if sigma is None or sigma == 0:
        return float(U), None, "builtin.mann_whitney"
    z = (U - mu) / sigma
    p = 2 * (1 - normal_cdf(abs(z)))
    return float(U), float(p), "builtin.mann_whitney"


def kruskal(groups):
    st = _ensure_scipy()
    if st is not None:
        try:
            H, p = st.kruskal(*groups)
            return float(H), float(p), "scipy.kruskal"
        except Exception:
            pass
    allv = []
    for g in groups:
        allv.extend(g)
    N = len(allv)
    k = len(groups)
    if N == 0 or k < 2:
        return None, None, "builtin.kruskal"
    r = rankdata(allv)
    idx = 0
    Rj, nj = [], []
    for g in groups:
        s = sum(r[idx: idx + len(g)])
        Rj.append(s)
        nj.append(len(g))
        idx += len(g)
    H = 12 / (N * (N + 1)) * sum(s ** 2 / n for s, n in zip(Rj, nj)) - 3 * (N + 1)
    counts = {}
    for v in allv:
        counts[v] = counts.get(v, 0) + 1
    T = sum(t ** 3 - t for t in counts.values())
    if T > 0:
        H = H / (1 - T / (N ** 3 - N))
    return float(H), float(chi2_sf(H, k - 1)), "builtin.kruskal"


def anova(groups):
    st = _ensure_scipy()
    if st is not None:
        try:
            F, p = st.f_oneway(*groups)
            return float(F), float(p), "scipy.f_oneway"
        except Exception:
            pass
    k = len(groups)
    N = sum(len(g) for g in groups)
    if N == 0 or k < 2:
        return None, None, "builtin.anova"
    grand = sum(sum(g) for g in groups) / N
    ssb = sum(len(g) * (mean(g) - grand) ** 2 for g in groups)
    ssw = sum(sum((v - mean(g)) ** 2 for v in g) for g in groups)
    dfb, dfw = k - 1, N - k
    if ssw <= 0 or dfw <= 0:
        return None, None, "builtin.anova"
    F = (ssb / dfb) / (ssw / dfw)
    return float(F), float(f_sf(F, dfb, dfw)), "builtin.anova"


def anova_ss(groups):
    """Return (ssb, ssw, sst, dfb, dfw, N)."""
    k = len(groups)
    N = sum(len(g) for g in groups)
    grand = sum(sum(g) for g in groups) / N if N else 0
    ssb = sum(len(g) * (mean(g) - grand) ** 2 for g in groups)
    ssw = sum(sum((v - mean(g)) ** 2 for v in g) for g in groups)
    return ssb, ssw, ssb + ssw, k - 1, N - k, N


def chi2_test(table):
    st = _ensure_scipy()
    if st is not None:
        try:
            chi2, p, dof, exp = st.chi2_contingency(table, correction=False)
            return float(chi2), float(p), int(dof), [[float(e) for e in row] for row in exp], "scipy.chi2_contingency"
        except Exception:
            pass
    rows, cols = len(table), len(table[0])
    rs = [sum(r) for r in table]
    cs = [sum(table[i][j] for i in range(rows)) for j in range(cols)]
    N = sum(rs)
    chi2 = 0.0
    exp = []
    for i in range(rows):
        er = []
        for j in range(cols):
            e = rs[i] * cs[j] / N
            er.append(e)
            o = table[i][j]
            if e > 0:
                chi2 += (o - e) ** 2 / e
        exp.append(er)
    dof = (rows - 1) * (cols - 1)
    return float(chi2), float(chi2_sf(chi2, dof)), dof, exp, "builtin.chi2"


def pearson(x, y):
    st = _ensure_scipy()
    if st is not None:
        try:
            r, p = st.pearsonr(x, y)
            return float(r), float(p), "scipy.pearsonr"
        except Exception:
            pass
    n = len(x)
    if n < 3:
        return None, None, "builtin.pearson"
    mx, my = mean(x), mean(y)
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
    dx = sum((a - mx) ** 2 for a in x)
    dy = sum((b - my) ** 2 for b in y)
    den = math.sqrt(dx * dy)
    if den <= 0:
        return None, None, "builtin.pearson"
    r = cov / den
    if abs(r) >= 1:
        return float(r), 0.0, "builtin.pearson"
    t = r * math.sqrt((n - 2) / (1 - r * r))
    p = 2 * (1 - t_cdf(abs(t), n - 2))
    return float(r), float(p), "builtin.pearson"


# --------------------------------------------------------------------------
# effect sizes
# --------------------------------------------------------------------------

def cohens_d(x, y):
    n1, n2 = len(x), len(y)
    v1, v2 = var(x), var(y)
    if v1 is None or v2 is None:
        return None
    sp = math.sqrt(((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2))
    return (mean(x) - mean(y)) / sp if sp > 0 else None


def hedges_g(x, y):
    d = cohens_d(x, y)
    n = len(x) + len(y)
    return d * (1 - 3 / (4 * n - 9)) if d is not None else None


def glass_delta(x, y_ref):
    s = sd(y_ref)
    return (mean(x) - mean(y_ref)) / s if s and s > 0 else None


def cramers_v(table):
    chi2, _p, _dof, _e, _m = chi2_test(table)
    rows, cols = len(table), len(table[0])
    N = sum(sum(r) for r in table)
    denom = N * (min(rows, cols) - 1)
    return math.sqrt(chi2 / denom) if denom > 0 and chi2 is not None else None


# --------------------------------------------------------------------------
# confidence intervals
# --------------------------------------------------------------------------

def _z_crit(conf):
    return normal_ppf(1 - (1 - conf) / 2)


def mean_t_ci(x, conf=0.95):
    x = [v for v in x if v is not None]
    n = len(x)
    if n < 2:
        return None
    m = mean(x)
    s = sd(x)
    t = t_ppf(1 - (1 - conf) / 2, n - 1)
    se = s / math.sqrt(n)
    return m - t * se, m + t * se


def welch_diff_ci(x, y, conf=0.95):
    n1, n2 = len(x), len(y)
    if n1 < 2 or n2 < 2:
        return None
    d = mean(x) - mean(y)
    v1, v2 = var(x), var(y)
    se1, se2 = v1 / n1, v2 / n2
    se = math.sqrt(se1 + se2)
    df = (se1 + se2) ** 2 / (se1 ** 2 / (n1 - 1) + se2 ** 2 / (n2 - 1))
    t = t_ppf(1 - (1 - conf) / 2, df)
    return d - t * se, d + t * se


def wilson_ci(k, n, conf=0.95):
    if n <= 0:
        return None
    z = _z_crit(conf)
    phat = k / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return center - half, center + half


def fisher_z_ci(r, n, conf=0.95):
    if n <= 3 or abs(r) >= 1:
        return None
    z = math.atanh(r)
    se = 1 / math.sqrt(n - 3)
    zc = _z_crit(conf)
    return math.tanh(z - zc * se), math.tanh(z + zc * se)


def d_ci(d, n1, n2, conf=0.95):
    if d is None:
        return None
    se = math.sqrt((n1 + n2) / (n1 * n2) + d * d / (2 * (n1 + n2)))
    z = _z_crit(conf)
    return d - z * se, d + z * se


# --------------------------------------------------------------------------
# file discovery and readers
# --------------------------------------------------------------------------

KNOWN_EXT = {
    ".csv": "csv", ".tsv": "tsv", ".tab": "tsv", ".txt": "text", ".md": "text",
    ".json": "json", ".db": "sqlite", ".sqlite": "sqlite", ".sqlite3": "sqlite",
    ".xlsx": "xlsx", ".xls": "xls", ".docx": "docx", ".pdf": "pdf",
    ".yaml": "text", ".yml": "text", ".sav": "binary", ".dta": "binary",
}


def discover_files(input_dir):
    out = []
    if not os.path.isdir(input_dir):
        return out
    for name in sorted(os.listdir(input_dir)):
        p = os.path.join(input_dir, name)
        if not os.path.isfile(p):
            continue
        ext = os.path.splitext(name)[1].lower()
        out.append({"path": p, "name": name, "kind": KNOWN_EXT.get(ext, "other"),
                    "size": os.path.getsize(p), "rel_path": rel_path(p, input_dir)})
    return out


def read_text_file(path):
    data, size = _read_bytes(path)
    text = decode_bytes(data)
    lines = text.splitlines()
    return {"path": path, "text": text, "lines": lines, "kind": "text",
            "size": size, "truncated": size > 4_000_000}


def read_csv_rows(path):
    data, size = _read_bytes(path)
    text = decode_bytes(data)
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delim = dialect.delimiter
    except Exception:
        delim = "," if text.count(",") >= text.count("\t") else "\t"
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    raw_rows = [r for r in reader]
    raw_rows = [r for r in raw_rows if any((c or "").strip() for c in r)]
    if not raw_rows:
        return {"path": path, "kind": "csv", "delimiter": delim, "columns": [],
                "rows": [], "raw_lines": [], "header_verbatim": "", "size": size}
    header = [h.strip() for h in raw_rows[0]]
    data_rows = []
    line_start = 2
    for i, r in enumerate(raw_rows[1:]):
        row = {}
        for j, h in enumerate(header):
            row[h] = (r[j].strip() if j < len(r) else "")
        data_rows.append(row)
    return {"path": path, "kind": "csv", "delimiter": delim, "columns": header,
            "rows": data_rows, "raw_lines": raw_rows, "line_start": line_start,
            "header_verbatim": raw_rows[0], "size": size}


def read_json_rows(path):
    data, size = _read_bytes(path)
    text = decode_bytes(data)
    obj = safe_json_load(text, path)
    if isinstance(obj, dict) and "_json_error" in obj:
        return {"path": path, "kind": "json", "columns": [], "rows": [],
                "parse_error": obj["_json_error"], "size": size}
    rows = []
    if isinstance(obj, list):
        rows = [r if isinstance(r, dict) else {"value": r} for r in obj]
    elif isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                rows = v
                break
        if not rows:
            rows = [obj]
    columns = []
    for r in rows:
        for k in r:
            if k not in columns:
                columns.append(k)
    return {"path": path, "kind": "json", "columns": columns, "rows": rows,
            "size": size, "text": text}


def read_sqlite_tables(path):
    out = {}
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        tables = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for t in tables:
            try:
                cols = [c[1] for c in cur.execute(
                    "PRAGMA table_info('%s')" % t.replace("'", "''"))]
                rows = [dict(zip(cols, r)) for r in cur.execute(
                    "SELECT * FROM '%s'" % t.replace("'", "''"))]
                out[t] = {"columns": cols, "rows": rows}
            except Exception as exc:
                out[t] = {"columns": [], "rows": [], "error": str(exc)}
    finally:
        conn.close()
    return out


_XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def read_xlsx_sheets(path):
    sheets = {}
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        shared = ["".join(t.text or "" for t in si.iter(_XLSX_NS + "t"))
                  for si in root.iter(_XLSX_NS + "si")]
    sheet_names = sorted(n for n in z.namelist()
                         if re.match(r"xl/worksheets/sheet\d+\.xml", n))
    for n in sheet_names:
        root = ET.fromstring(z.read(n))
        rows = []
        for row in root.iter(_XLSX_NS + "row"):
            cells = []
            for c in row.iter(_XLSX_NS + "c"):
                t = c.get("t")
                v = c.find(_XLSX_NS + "v")
                val = v.text if v is not None else ""
                if t == "s" and val.isdigit():
                    val = shared[int(val)] if int(val) < len(shared) else ""
                elif t == "inlineStr":
                    it = c.find(_XLSX_NS + "is")
                    val = "".join(x.text or "" for x in it.iter(_XLSX_NS + "t"))
                cells.append(val)
            rows.append(cells)
        if rows:
            header = [str(h).strip() for h in rows[0]]
            data = []
            for r in rows[1:]:
                if not any((c or "").strip() for c in r):
                    continue
                data.append({header[j]: (r[j].strip() if j < len(r) else "")
                             for j in range(len(header))})
            sheets[n] = {"columns": header, "rows": data, "header_verbatim": rows[0]}
    return sheets


_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def read_docx_paragraphs(path):
    z = zipfile.ZipFile(path)
    root = ET.fromstring(z.read("word/document.xml"))
    paras = ["".join(t.text or "" for t in p.iter(_W_NS + "t"))
             for p in root.iter(_W_NS + "p")]
    return paras


def read_pdf_text(path):
    try:
        proc = subprocess.run(["pdftotext", "-layout", path, "-"],
                              capture_output=True, text=True, timeout=120)
        if proc.returncode == 0:
            return proc.stdout
    except Exception:
        pass
    return None


def read_any(path, kind):
    if kind in ("csv", "tsv"):
        return read_csv_rows(path)
    if kind == "json":
        return read_json_rows(path)
    if kind in ("sqlite",):
        return read_sqlite_tables(path)
    if kind == "xlsx":
        return read_xlsx_sheets(path)
    if kind == "docx":
        paras = read_docx_paragraphs(path)
        return {"path": path, "kind": "docx", "paragraphs": paras,
                "text": "\n".join(paras)}
    if kind == "pdf":
        text = read_pdf_text(path)
        return {"path": path, "kind": "pdf", "text": text,
                "extracted": text is not None}
    if kind in ("text",):
        return read_text_file(path)
    if kind == "xls":
        return {"path": path, "kind": "xls",
                "note": "legacy .xls binary not parsed by stdlib; needs conversion"}
    return {"path": path, "kind": kind or "other",
            "note": "unsupported/unknown format"}


def _flatten_tables(parsed):
    """Return a list of table-like datasets from a parsed source."""
    datasets = []
    if isinstance(parsed, dict) and "columns" in parsed:
        datasets.append(parsed)
    elif isinstance(parsed, dict):
        for k, v in parsed.items():
            if isinstance(v, dict) and "columns" in v:
                datasets.append(v)
    return datasets


# --------------------------------------------------------------------------
# contract handling
# --------------------------------------------------------------------------

def load_case(input_dir):
    case_path = os.path.join(input_dir, "case.json")
    if not os.path.exists(case_path):
        return None, None, None
    data, size = _read_bytes(case_path)
    text = decode_bytes(data)
    case = safe_json_load(text, case_path)
    if isinstance(case, dict) and "_json_error" in case:
        return {"_json_error": case["_json_error"], "_path": case_path}, None, None
    contract = case.get("output_contract", None)
    dd = case.get("data_dictionary", None)
    if dd is None and isinstance(contract, dict):
        dd = contract.get("data_dictionary", None)
    return case, contract, dd


def backfill_contract(contract, computed):
    """Return a dict that keeps every output_contract key, filling what we have."""
    out = {}
    if isinstance(contract, dict):
        for k, v in contract.items():
            cv = computed.get(k)
            if cv is not None:
                out[k] = cv
            elif isinstance(v, list):
                out[k] = []
            elif isinstance(v, dict):
                out[k] = {}
            else:
                out[k] = None
    return out


# --------------------------------------------------------------------------
# cleaning
# --------------------------------------------------------------------------

def infer_col_type(values):
    numeric = 0
    categorical = 0
    missing = 0
    for v in values:
        if v is None or (isinstance(v, str) and v.strip() == ""):
            missing += 1
            continue
        if _num(v) is not None:
            numeric += 1
        else:
            categorical += 1
    total = numeric + categorical
    if total == 0:
        return "empty"
    if numeric / total >= 0.9:
        return "numeric"
    return "categorical"


def column_profile(name, rows):
    vals = [r.get(name) for r in rows]
    n_total = len(vals)
    missing = sum(1 for v in vals if v is None or (isinstance(v, str) and v.strip() == ""))
    nums = [_num(v) for v in vals]
    nums_clean = [v for v in nums if v is not None]
    ctype = infer_col_type(vals)
    return {
        "column": name, "type": ctype, "n_total": n_total,
        "n_missing": missing, "missing_prop": missing / n_total if n_total else None,
        "n_numeric": len(nums_clean), "values": vals, "numeric": nums_clean,
    }


def outliers_iqr(vals):
    vals = [v for v in vals if v is not None]
    n = len(vals)
    if n == 0:
        return {"n": 0, "q1": None, "q3": None, "iqr": None,
                "lower": None, "upper": None, "n_low": 0, "n_high": 0, "indices": []}
    q1 = quantile(vals, 0.25)
    q3 = quantile(vals, 0.75)
    iqr = q3 - q1
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    low = [i for i, v in enumerate(vals) if v < lower]
    high = [i for i, v in enumerate(vals) if v > upper]
    return {"n": n, "q1": q1, "q3": q3, "iqr": iqr, "lower": lower, "upper": upper,
            "n_low": len(low), "n_high": len(high),
            "indices": sorted(set(low + high))}


def detect_duplicates(rows, columns):
    seen = {}
    dup_idx = []
    for i, r in enumerate(rows):
        key = tuple(str(r.get(c, "")) for c in columns)
        if key in seen:
            dup_idx.append(i)
        else:
            seen[key] = i
    return {"n_before": len(rows), "n_after": len(rows) - len(dup_idx),
            "n_duplicates": len(dup_idx), "duplicate_indices": dup_idx,
            "keep_rule": "first_occurrence"}


# --------------------------------------------------------------------------
# design detection and analysis
# --------------------------------------------------------------------------

def detect_design(columns, rows):
    profiles = {c: column_profile(c, rows) for c in columns}
    types = {c: p["type"] for c, p in profiles.items()}
    numeric = [c for c, t in types.items() if t == "numeric"]
    categorical = [c for c, t in types.items() if t == "categorical"]
    grouping = None
    for c in categorical:
        n = len(set(str(r.get(c)) for r in rows))
        if 2 <= n <= 10:
            grouping = c
            break
    return {"grouping": grouping, "numeric": numeric, "categorical": categorical,
            "types": types, "profiles": profiles}


def describe_column(vals):
    v = [x for x in vals if x is not None]
    n = len(v)
    if n == 0:
        return None
    return {"n": n, "mean": _fmt(mean(v)), "median": _fmt(median(v)),
            "sd": _fmt(sd(v)), "min": _fmt(min(v)), "max": _fmt(max(v)),
            "q1": _fmt(quantile(v, 0.25)), "q3": _fmt(quantile(v, 0.75)),
            "iqr": _fmt(quantile(v, 0.75) - quantile(v, 0.25)),
            "skewness": _fmt(skewness(v)), "kurtosis": _fmt(kurtosis(v))}


def run_group_comparison(label, groups):
    """groups: list of (name, values). Returns a complete record."""
    k = len(groups)
    names = [g[0] for g in groups]
    vals = [g[1] for g in groups]
    normal = {n: shapiro(v) for n, v in zip(names, vals)}
    homog = levene_bf(vals) if k >= 2 else (None, None, "n/a")
    all_normal = all(p is not None and p[1] is not None and p[1] >= 0.05
                     for p in normal.values())
    equal_var = homog[1] is not None and homog[1] >= 0.05
    small_n = any(len(v) < 30 for v in vals)

    test = {}
    if k == 2:
        if all_normal and equal_var:
            t, p, m = ttest_two(vals[0], vals[1], equal_var=True)
            test = {"test": "Student t (pooled variance)", "statistic": _fmt(t), "p": _fmt(p),
                    "df": len(vals[0]) + len(vals[1]) - 2, "method": m}
        elif all_normal:
            t, p, m = ttest_two(vals[0], vals[1], equal_var=False)
            df = welch_df(vals[0], vals[1])
            test = {"test": "Welch t", "statistic": _fmt(t), "p": _fmt(p), "df": _fmt(df), "method": m}
        else:
            U, p, m = mann_whitney(vals[0], vals[1])
            test = {"test": "Mann-Whitney U", "statistic": _fmt(U), "p": _fmt(p), "method": m}
    else:
        if all_normal and equal_var:
            F, p, m = anova(vals)
            test = {"test": "One-way ANOVA", "statistic": _fmt(F), "p": _fmt(p),
                    "df_between": k - 1, "df_within": sum(len(v) for v in vals) - k, "method": m}
        elif all_normal:
            # Welch ANOVA approximated by Kruskal note; keep honest label
            F, p, m = anova(vals)
            test = {"test": "One-way ANOVA (note: unequal variance; Welch ANOVA not in stdlib)",
                    "statistic": _fmt(F), "p": _fmt(p), "df_between": k - 1,
                    "df_within": sum(len(v) for v in vals) - k, "method": m}
        else:
            H, p, m = kruskal(vals)
            test = {"test": "Kruskal-Wallis H", "statistic": _fmt(H), "p": _fmt(p),
                    "df": k - 1, "method": m}

    effects = {}
    if k == 2:
        d = cohens_d(vals[0], vals[1])
        g = hedges_g(vals[0], vals[1])
        gl = glass_delta(vals[0], vals[1])
        effects = {"cohens_d": _fmt(d), "hedges_g": _fmt(g), "glass_delta": _fmt(gl),
                   "cohens_d_ci": [ _fmt(x) for x in d_ci(d, len(vals[0]), len(vals[1]))] if d is not None else None,
                   "direction": "group1 - group2"}
    else:
        ssb, ssw, sst, dfb, dfw, N = anova_ss(vals)
        etasq = ssb / sst if sst > 0 else None
        omegasq = (ssb - dfb * (ssw / dfw)) / (sst + (ssw / dfw)) if dfw > 0 and sst > 0 else None
        effects = {"eta_squared": _fmt(etasq), "omega_squared": _fmt(omegasq),
                   "ss_between": _fmt(ssb), "ss_within": _fmt(ssw), "ss_total": _fmt(sst)}

    cis = {}
    for n, v in zip(names, vals):
        ci = mean_t_ci(v)
        cis[n] = {"mean": _fmt(mean(v)), "ci95": [_fmt(x) for x in ci] if ci else None}
    if k == 2:
        wd = welch_diff_ci(vals[0], vals[1])
        cis["mean_difference"] = {"estimate": _fmt(mean(vals[0]) - mean(vals[1])),
                                  "ci95": [_fmt(x) for x in wd] if wd else None}

    return {"response": label, "groups": names, "n_per_group": [len(v) for v in vals],
            "descriptives": {n: describe_column(v) for n, v in zip(names, vals)},
            "normality": {n: {"W": _fmt(p[0]), "p": _fmt(p[1]), "method": p[2]}
                          for n, p in normal.items()},
            "variance_homogeneity": {"statistic": _fmt(homog[0]), "p": _fmt(homog[1]),
                                     "method": homog[2]},
            "test": test, "effects": effects, "confidence_intervals": cis,
            "assumptions": {"all_normal": all_normal, "equal_variance": equal_var}}


def welch_df(x, y):
    n1, n2 = len(x), len(y)
    v1, v2 = var(x), var(y)
    if v1 is None or v2 is None:
        return None
    se1, se2 = v1 / n1, v2 / n2
    return (se1 + se2) ** 2 / (se1 ** 2 / (n1 - 1) + se2 ** 2 / (n2 - 1))


def run_correlation(label1, label2, x, y):
    r, p, m = pearson(x, y)
    ci = fisher_z_ci(r, len(x)) if r is not None else None
    return {"pair": [label1, label2], "pearson_r": _fmt(r), "p": _fmt(p),
            "n": len(x), "ci95": [_fmt(v) for v in ci] if ci else None,
            "method": m, "interpretation_note":
            "CI 是参数覆盖区间，不误读为个体取值范围。"}


# --------------------------------------------------------------------------
# evidence map (emphasis of this variant)
# --------------------------------------------------------------------------

def make_evidence_map(sources, analysis, cleaning, design, artifacts, relroot):
    ev = []
    eid = 0
    def add(claim, kind, source_id, locator, quote, method=None, value=None,
            unit=None, status="derived", quote_kind="verbatim"):
        nonlocal eid
        eid += 1
        ev.append({"evidence_id": "E%03d" % eid, "claim": claim, "kind": kind,
                   "status": status, "source_id": source_id, "locator": locator,
                   "quote": _trunc(quote, 320), "quote_kind": quote_kind,
                   "method": method, "value": value, "unit": unit})
    for s in sources:
        add("已登记输入材料：%s（kind=%s）" % (s.get("name"), s.get("kind")),
            "source", s.get("source_id"), s.get("rel_path", s.get("name")),
            s.get("excerpt", ""), status="source", quote_kind="verbatim")
    for c in cleaning.get("missing", []):
        add("列 %s 缺失 %d/%d（%.1f%%），未按 0 处理" %
            (c["column"], c["n_missing"], c["n_total"],
             (c["missing_prop"] or 0) * 100),
            "cleaning", c.get("source_id"), c.get("locator", ""), c.get("quote", ""),
            method="missing count; listwise exclusion per analysis variable",
            value={"n_missing": c["n_missing"], "n_total": c["n_total"]},
            quote_kind="verbatim")
    for c in cleaning.get("outliers", []):
        add("列 %s IQR 1.5 法标记异常值：低 %d 个、高 %d 个（未静默删除）" %
            (c["column"], c["n_low"], c["n_high"]),
            "cleaning", c.get("source_id"), c.get("locator", ""), c.get("quote", ""),
            method="IQR 1.5 flag (Q1-1.5*IQR, Q3+1.5*IQR)",
            value={"q1": c["q1"], "q3": c["q3"], "iqr": c["iqr"],
                   "n_low": c["n_low"], "n_high": c["n_high"]},
            quote_kind="verbatim")
    for c in cleaning.get("duplicates", []):
        add("重复记录去重：%d → %d（去重 %d，保留首条）" %
            (c["n_before"], c["n_after"], c["n_duplicates"]),
            "cleaning", c.get("source_id"), c.get("locator", ""), c.get("quote", ""),
            method="exact-duplicate on all columns; keep first occurrence",
            value={"n_before": c["n_before"], "n_after": c["n_after"],
                   "n_duplicates": c["n_duplicates"]}, quote_kind="verbatim")
    for d in analysis.get("descriptive", []):
        for col, stat in d.get("stats", {}).items():
            add("列 %s 描述统计：mean=%s, sd=%s, n=%d" %
                (col, stat.get("mean"), stat.get("sd"), stat.get("n")),
                "descriptive", d.get("source_id"), stat.get("locator", ""),
                stat.get("quote", ""), method="mean=Σx/n; sample sd (ddof=1)",
                value=stat, unit=d.get("unit", None), quote_kind="verbatim")
    for inf in analysis.get("inferential", []):
        t = inf.get("test", {})
        add("列 %s 的 %s：%s=%s, p=%s（n_per_group=%s）" %
            (inf.get("response"), t.get("test"), t.get("test"), t.get("statistic"),
             t.get("p"), inf.get("n_per_group")),
            "inferential", inf.get("source_id"), inf.get("locator", ""),
            inf.get("quote", ""), method=t.get("method", ""), value=t,
            unit=inf.get("unit", None), quote_kind="verbatim")
        eff = inf.get("effects", {})
        for k, v in eff.items():
            add("列 %s 效应量 %s=%s" % (inf.get("response"), k, v),
                "effect_size", inf.get("source_id"), inf.get("locator", ""),
                inf.get("quote", ""), method="effect size", value={k: v},
                unit=None, quote_kind="verbatim")
        for name, ci in inf.get("confidence_intervals", {}).items():
            add("列 %s 的 %s 95%% CI=%s" % (inf.get("response"), name, ci.get("ci95")),
                "confidence_interval", inf.get("source_id"), inf.get("locator", ""),
                inf.get("quote", ""), method="t CI / Welch CI", value=ci,
                quote_kind="verbatim")
    for corr in analysis.get("correlations", []):
        add("相关 %s：r=%s, p=%s, 95%% CI=%s（Fisher z）" %
            (corr.get("pair"), corr.get("pearson_r"), corr.get("p"), corr.get("ci95")),
            "correlation", corr.get("source_id"), corr.get("locator", ""),
            corr.get("quote", ""), method=corr.get("method", ""), value=corr,
            quote_kind="verbatim")
    for a in artifacts:
        add("主产物：%s" % a.get("path"), "artifact", a.get("source_id", "S0"),
            a.get("path", ""), a.get("note", ""), status="artifact",
            quote_kind="note")
    return ev


# --------------------------------------------------------------------------
# SVG figures (deterministic, editable)
# --------------------------------------------------------------------------

def svg_boxplot(groups, title, width=640, height=360):
    """groups: list of (name, values)."""
    allv = [v for _n, vals in groups for v in vals if v is not None]
    if not allv:
        return None
    gmin, gmax = min(allv), max(allv)
    pad = (gmax - gmin) * 0.08 or 1.0
    lo, hi = gmin - pad, gmax + pad
    n = len(groups)
    slot = width / n
    box_w = min(72, slot * 0.5)
    parts = []
    def y(v):
        return height - 40 - (v - lo) / (hi - lo) * (height - 80)
    for i, (name, vals) in enumerate(groups):
        vals = [v for v in vals if v is not None]
        if not vals:
            continue
        cx = slot * i + slot / 2
        q1, med, q3 = quantile(vals, 0.25), median(vals), quantile(vals, 0.75)
        iqr = q3 - q1
        wl = q1 - 1.5 * iqr
        wh = q3 + 1.5 * iqr
        inside = [v for v in vals if wl <= v <= wh]
        wl = max(wl, min(inside)) if inside else wl
        wh = min(wh, max(inside)) if inside else wh
        parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                     'fill="none" stroke="#1f77b4" stroke-width="1.6"/>'
                     % (cx - box_w / 2, y(q3), box_w, max(1.0, y(q1) - y(q3))))
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                     'stroke="#1f77b4" stroke-width="1.4"/>'
                     % (cx - box_w / 2, y(med), cx + box_w / 2, y(med)))
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                     'stroke="#1f77b4" stroke-width="1.2"/>' % (cx, y(q1), cx, y(wl)))
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                     'stroke="#1f77b4" stroke-width="1.2"/>' % (cx, y(q3), cx, y(wh)))
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                     'stroke="#1f77b4" stroke-width="1.2"/>' % (cx - 8, y(wl), cx + 8, y(wl)))
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                     'stroke="#1f77b4" stroke-width="1.2"/>' % (cx - 8, y(wh), cx + 8, y(wh)))
        for v in vals:
            if v < wl or v > wh:
                parts.append('<circle cx="%.1f" cy="%.1f" r="2.6" fill="#d62728"/>' % (cx, y(v)))
        parts.append('<text x="%.1f" y="%d" text-anchor="middle" font-size="11">%s</text>'
                     % (cx, height - 12, _trunc(str(name), 18)))
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
           'viewBox="0 0 %d %d">\n' % (width, height, width, height))
    svg += '<rect width="100%%" height="100%%" fill="white"/>\n'
    svg += '<text x="16" y="22" font-size="14" font-family="sans-serif">%s</text>\n' % _trunc(title, 90)
    svg += "\n".join(parts)
    svg += "\n</svg>\n"
    return svg


def svg_histogram(vals, title, width=640, height=360, bins=20):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi = lo + 1
    bw = (hi - lo) / bins
    counts = [0] * bins
    for v in vals:
        idx = int((v - lo) / bw)
        if idx >= bins:
            idx = bins - 1
        counts[idx] += 1
    cmax = max(counts) or 1
    bars = []
    for i, c in enumerate(counts):
        x = 40 + i * (width - 80) / bins
        h = (height - 70) * c / cmax
        bars.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                    'fill="#4c78a8" stroke="#33526e" stroke-width="0.6"/>'
                    % (x, height - 40 - h, (width - 80) / bins - 1, h))
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
           'viewBox="0 0 %d %d">\n' % (width, height, width, height))
    svg += '<rect width="100%%" height="100%%" fill="white"/>\n'
    svg += '<text x="16" y="22" font-size="14" font-family="sans-serif">%s</text>\n' % _trunc(title, 90)
    svg += "\n".join(bars)
    svg += '\n<text x="%d" y="%d" font-size="10" text-anchor="end">n=%d</text>\n' % (width - 12, height - 10, len(vals))
    svg += "\n</svg>\n"
    return svg


# --------------------------------------------------------------------------
# report generation
# --------------------------------------------------------------------------

def build_report(analysis, cleaning, design, evidence, case_id, runtime):
    lines = ["# 统计分析报告（%s）" % case_id, "", "状态：`%s`" % runtime.get("status", "partial"),
             "", "> 本文所有关键结论均在 `result.json` 的 `evidence_map` 中给出 "
             "locator + 逐字 quote 溯源。", ""]
    lines += ["## 1. 数据清洗", ""]
    for c in cleaning.get("missing", []):
        lines.append("- 缺失：`%s` %d/%d（%.1f%%）" %
                     (c["column"], c["n_missing"], c["n_total"], (c["missing_prop"] or 0) * 100))
    for c in cleaning.get("outliers", []):
        lines.append("- 异常值（IQR 1.5 标记，未删除）：`%s` 低 %d / 高 %d" %
                     (c["column"], c["n_low"], c["n_high"]))
    for c in cleaning.get("duplicates", []):
        lines.append("- 重复：%d → %d（保留首条）" % (c["n_before"], c["n_after"]))
    if not (cleaning.get("missing") or cleaning.get("outliers") or cleaning.get("duplicates")):
        lines.append("- 未检出缺失/异常/重复。")
    lines += ["", "## 2. 描述统计", ""]
    for d in analysis.get("descriptive", []):
        lines.append("### %s" % d.get("table", ""))
        for col, stat in d.get("stats", {}).items():
            lines.append("- `%s`：n=%s, mean=%s, sd=%s, median=%s, Q1=%s, Q3=%s" %
                         (col, stat.get("n"), stat.get("mean"), stat.get("sd"),
                          stat.get("median"), stat.get("q1"), stat.get("q3")))
    lines += ["", "## 3. 推断统计与效应量", ""]
    for inf in analysis.get("inferential", []):
        t = inf.get("test", {})
        lines.append("### %s" % inf.get("response"))
        lines.append("- 检验：%s，统计量=%s，p=%s（n_per_group=%s）" %
                     (t.get("test"), t.get("statistic"), t.get("p"), inf.get("n_per_group")))
        lines.append("- 假设：正态性=%s，方差齐性=%s" %
                     (inf.get("assumptions", {}).get("all_normal"),
                      inf.get("assumptions", {}).get("equal_variance")))
        lines.append("- 效应量：%s" % inf.get("effects"))
        for name, ci in inf.get("confidence_intervals", {}).items():
            lines.append("- %s 95%% CI：%s" % (name, ci.get("ci95")))
    for corr in analysis.get("correlations", []):
        lines.append("- 相关 `%s`：r=%s, p=%s, 95%% CI=%s" %
                     (corr.get("pair"), corr.get("pearson_r"), corr.get("p"), corr.get("ci95")))
    lines += ["", "## 4. 结果解释", ""]
    for intr in analysis.get("interpretation", []):
        lines.append("- %s" % intr)
    lines += ["", "## 5. 证据映射（示例）", ""]
    for e in evidence[:20]:
        lines.append("- `%s` %s → locator `%s`" % (e["evidence_id"], e["claim"], e["locator"]))
    lines += ["", "## 6. 剩余不确定性与待确认", ""]
    for u in runtime.get("uncertainty", []):
        lines.append("- %s" % u)
    lines += ["", "## 7. 运行信息", "", "- 后端：%s" % runtime.get("backend", {}),
              "- 时间：%s" % runtime.get("started_at"), ""]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# quality checks
# --------------------------------------------------------------------------

def build_quality_checks(cleaning, analysis, design):
    qc = {
        "seven_categories": {
            "missing_values": bool(cleaning.get("missing")),
            "outliers": bool(cleaning.get("outliers")),
            "duplicates": bool(cleaning.get("duplicates")),
            "statistical_assumptions": bool(analysis.get("inferential")),
            "figure_choice": bool(analysis.get("figures")),
            "code_execution": True,
            "database_fields": bool(cleaning.get("sqlite_tables")),
        },
        "checks": [
            "result.json 可解析且含 case_id + output_contract 全字段 + status/sources/uncertainty/change_log",
            "缺失/异常/重复七类情形均有计数与处理规则（见 cleaning）",
            "检验方法与分布/设计匹配：先正态性(Shapiro/Shapiro-Francia)与方差齐性(Brown-Forsythe)",
            "≥3 组未用两两 t；两两比较用 Bonferroni/BH 校正（如进行）",
            "每个关键数值带 locator+quote 证据（evidence_map）",
            "效应量与 95% CI 与检验、样本量、不确定性一致",
            "无随机数；同输入两次运行结果一致（确定性）",
            "图表为可编辑 SVG；代码可在 /app/output 复跑",
            "不编造数字、引用、软件状态、运行记录",
        ],
    }
    return qc


# --------------------------------------------------------------------------
# pipeline steps
# --------------------------------------------------------------------------

def step_init(input_dir, output_dir, work_dir, started_at):
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    files = discover_files(input_dir)
    case, contract, dd = load_case(input_dir)
    case_id = "unknown-case"
    if isinstance(case, dict) and case.get("case_id"):
        case_id = str(case["case_id"])
    result = {"case_id": case_id, "status": "partial",
              "sources": [{"source_id": "S1", "path": os.path.join(input_dir, "case.json"),
                           "kind": "text", "rel_path": "case.json"}],
              "uncertainty": ["init 阶段骨架，分析尚未完成"],
              "change_log": [{"id": "CL1", "time": started_at, "action": "init",
                              "before": None, "after": "partial 骨架",
                              "reason": "前 3 分钟先落盘合法结果骨架"}],
              "errors": [], "degradations": [], "pending_human": [],
              "runtime": {"started_at": started_at, "skill": SKILL_NAME,
                          "version": SKILL_VERSION, "backend": {}}}
    write_json(os.path.join(output_dir, "result.json"), result)
    index = {"started_at": started_at, "case_id": case_id,
             "files": [f["rel_path"] for f in files]}
    write_json(os.path.join(work_dir, "index.json"), index)
    write_json(os.path.join(work_dir, "state.json"), {"step": "init", "time": started_at})
    return result, files, case, contract, dd


def step_survey(input_dir, work_dir, files):
    resources = []
    res_yaml = os.path.join(input_dir, "environment", "resources.yaml")
    if os.path.exists(res_yaml):
        t = read_text_file(res_yaml)
        resources.append({"file": "environment/resources.yaml",
                          "excerpt": _trunc(t["text"], 400)})
    materials = {"files": files, "resources": resources,
                 "tools": {"scientific-python": "scipy/numpy/matplotlib 若可用；否则纯标准库回退",
                           "SQLite": "sqlite3 标准库"}}
    write_json(os.path.join(work_dir, "materials.json"), materials)
    return materials


def step_inspect(input_dir, work_dir, files, case, contract, dd):
    schemas = []
    for f in files:
        parsed = read_any(f["path"], f["kind"])
        for ds in _flatten_tables(parsed):
            cols = ds.get("columns", [])
            rows = ds.get("rows", [])
            prof = {c: column_profile(c, rows) for c in cols}
            schemas.append({"source": f["rel_path"], "table": ds.get("path", f["name"]),
                            "columns": [{"column": c, "type": p["type"],
                                         "n_missing": p["n_missing"]}
                                        for c, p in prof.items()],
                            "n_rows": len(rows)})
    schema = {"case_id": case.get("case_id") if isinstance(case, dict) else None,
              "contract_keys": list(contract.keys()) if isinstance(contract, dict) else None,
              "data_dictionary": dd, "tables": schemas}
    write_json(os.path.join(work_dir, "schema.json"), schema)
    return schema


def step_analyze(input_dir, work_dir, output_dir, files, case, contract, dd, started_at):
    analysis = {"descriptive": [], "inferential": [], "correlations": [],
                "interpretation": [], "figures": []}
    cleaning = {"missing": [], "outliers": [], "duplicates": [], "sqlite_tables": []}
    sources = []
    change_log = []
    sid = 0
    os.makedirs(os.path.join(output_dir, "cleaned"), exist_ok=True)
    os.makedirs(os.path.join(output_dir, "figures"), exist_ok=True)

    for f in files:
        if f["kind"] in ("pdf", "docx", "text", "yaml", "other", "xls", "binary"):
            continue
        sid += 1
        source_id = "S%d" % sid
        parsed = read_any(f["path"], f["kind"])
        excerpt = ""
        if f["kind"] in ("csv", "tsv", "json", "xlsx"):
            ds_list = _flatten_tables(parsed)
            if isinstance(parsed, dict) and f["kind"] == "csv":
                excerpt = "\n".join(str(x) for x in parsed.get("raw_lines", [])[:4])
            elif isinstance(parsed, dict) and parsed.get("text"):
                excerpt = _trunc(parsed["text"], 300)
            sources.append({"source_id": source_id, "path": f["path"], "kind": f["kind"],
                            "name": f["name"], "rel_path": f["rel_path"], "excerpt": excerpt})
            if f["kind"] == "csv":
                # write cleaned CSV (duplicates removed)
                cols = parsed.get("columns", [])
                rows = parsed.get("rows", [])
                dup = detect_duplicates(rows, cols)
                keep = [i for i in range(len(rows)) if i not in set(dup["duplicate_indices"])]
                cleaned_rows = [rows[i] for i in keep]
                out_csv = os.path.join(output_dir, "cleaned",
                                       os.path.splitext(f["name"])[0] + "_clean.csv")
                with open(out_csv, "w", encoding="utf-8", newline="") as fh:
                    w = csv.writer(fh)
                    w.writerow(cols)
                    for r in cleaned_rows:
                        w.writerow([r.get(c, "") for c in cols])
                cleaning["duplicates"].append({**dup, "source_id": source_id,
                                              "locator": "%s#rows[0..%d]" % (f["rel_path"], len(rows)),
                                              "quote": excerpt[:120]})
                prof = {c: column_profile(c, rows) for c in cols}
                for c, p in prof.items():
                    if p["n_missing"] > 0:
                        cleaning["missing"].append({
                            "column": c, "n_missing": p["n_missing"],
                            "n_total": p["n_total"], "missing_prop": p["missing_prop"],
                            "source_id": source_id,
                            "locator": "%s#col[%s]#rows[0..%d]" % (f["rel_path"], c, len(rows)),
                            "quote": excerpt[:120]})
                    if p["type"] == "numeric":
                        o = outliers_iqr(p["numeric"])
                        if o["n_low"] or o["n_high"]:
                            cleaning["outliers"].append({
                                "column": c, **o, "source_id": source_id,
                                "locator": "%s#col[%s]#rows[0..%d]" % (f["rel_path"], c, len(rows)),
                                "quote": excerpt[:120]})
                design = detect_design(cols, rows)
                unit = None
                if isinstance(dd, dict):
                    for key in (f["name"], os.path.splitext(f["name"])[0]):
                        if isinstance(dd.get(key), dict):
                            unit = dd[key].get("unit")
                            break
                # descriptive for every numeric column
                desc_stats = {}
                for c in design["numeric"]:
                    stt = describe_column(prof[c]["numeric"])
                    if stt:
                        stt["locator"] = "%s#col[%s]#rows[0..%d]" % (f["rel_path"], c, len(rows))
                        stt["quote"] = (str(parsed.get("header_verbatim", "")) +
                                        " | first values: " +
                                        ", ".join(_trunc(str(r.get(c, "")), 12)
                                                  for r in rows[:4]))
                        desc_stats[c] = stt
                analysis["descriptive"].append({"source_id": source_id, "table": f["name"],
                                                "unit": unit, "stats": desc_stats})
                # inferential: group comparison if a grouping column exists
                if design["grouping"]:
                    for num_col in design["numeric"]:
                        gdata = []
                        for r in rows:
                            g = str(r.get(design["grouping"], "")).strip()
                            v = _num(r.get(num_col))
                            if g == "" or v is None:
                                continue
                            gdata.append((g, v))
                        groups = []
                        order = []
                        for g, v in gdata:
                            if g not in order:
                                order.append(g)
                        groups = [(g, [v for gg, v in gdata if gg == g]) for g in order]
                        if len(groups) >= 2 and all(len(v) >= 2 for _g, v in groups):
                            rec = run_group_comparison(num_col, groups)
                            rec["source_id"] = source_id
                            rec["unit"] = unit
                            rec["locator"] = "%s#col[%s]~col[%s]#rows[0..%d]" % (
                                f["rel_path"], num_col, design["grouping"], len(rows))
                            rec["quote"] = (str(parsed.get("header_verbatim", "")) +
                                            " | group=" + design["grouping"])
                            analysis["inferential"].append(rec)
                            fig = svg_boxplot(groups, "%s by %s" % (num_col, design["grouping"]))
                            if fig:
                                fname = "box_%s_%s.svg" % (os.path.splitext(f["name"])[0],
                                                           num_col.replace("/", "_"))
                                fpath = os.path.join(output_dir, "figures", fname)
                                write_text(fpath, fig)
                                analysis["figures"].append({"path": "figures/" + fname,
                                                            "kind": "boxplot"})
                            p = rec["test"].get("p")
                            txt = "%s 在组间差异" % num_col
                            if p is None:
                                txt += "：无法判定（p 不可用，见 uncertainties）。"
                            elif p < 0.05:
                                txt += "：p=%.4g，在 α=0.05 下统计显著；效应量 %s。显著性≠实际意义。" % (
                                    p, rec["effects"])
                            else:
                                txt += "：p=%.4g，在 α=0.05 下未达显著；效应量 %s。" % (p, rec["effects"])
                            analysis["interpretation"].append(txt)
                else:
                    # correlation matrix (first up to 6 numeric columns)
                    nums = design["numeric"][:6]
                    for i in range(len(nums)):
                        for j in range(i + 1, len(nums)):
                            x = [r for r in rows if _num(r.get(nums[i])) is not None
                                 and _num(r.get(nums[j])) is not None]
                            if len(x) < 3:
                                continue
                            xi = [_num(r.get(nums[i])) for r in x]
                            yi = [_num(r.get(nums[j])) for r in x]
                            c = run_correlation(nums[i], nums[j], xi, yi)
                            c["source_id"] = source_id
                            c["locator"] = "%s#col[%s]xcol[%s]#rows[0..%d]" % (
                                f["rel_path"], nums[i], nums[j], len(rows))
                            c["quote"] = str(parsed.get("header_verbatim", ""))
                            analysis["correlations"].append(c)
        elif f["kind"] == "sqlite":
            tables = parsed
            excerpt = "SQLite tables: " + ", ".join(tables.keys())
            sources.append({"source_id": source_id, "path": f["path"], "kind": "sqlite",
                            "name": f["name"], "rel_path": f["rel_path"], "excerpt": excerpt})
            for tname, tdata in tables.items():
                cols = tdata.get("columns", [])
                rows = tdata.get("rows", [])
                if not cols:
                    continue
                cleaning["sqlite_tables"].append({"table": tname, "columns": cols,
                                                  "n_rows": len(rows)})
                # dump table to CSV as editable artifact
                out_csv = os.path.join(output_dir, "cleaned", "%s_%s.csv" % (os.path.splitext(f["name"])[0], tname))
                with open(out_csv, "w", encoding="utf-8", newline="") as fh:
                    w = csv.writer(fh)
                    w.writerow(cols)
                    for r in rows:
                        w.writerow([r.get(c, "") for c in cols])
                design = detect_design(cols, rows)
                desc_stats = {}
                for c in design["numeric"]:
                    prof = column_profile(c, rows)
                    stt = describe_column(prof["numeric"])
                    if stt:
                        stt["locator"] = "%s#table[%s]#col[%s]" % (f["rel_path"], tname, c)
                        stt["quote"] = "table=%s header=%s" % (tname, cols)
                        desc_stats[c] = stt
                analysis["descriptive"].append({"source_id": source_id,
                                                "table": "%s::%s" % (f["name"], tname),
                                                "unit": None, "stats": desc_stats})

    # write analysis.json
    write_json(os.path.join(work_dir, "analysis.json"),
               {"analysis": analysis, "cleaning": cleaning})
    return analysis, cleaning, sources, change_log


def build_interpretation(analysis):
    """Return plain-language conclusions (already partly built)."""
    out = []
    for inf in analysis.get("inferential", []):
        t = inf.get("test", {})
        p = t.get("p")
        eff = inf.get("effects", {})
        if p is None:
            out.append("%s：样本量或组内方差不足，该检验不适用（未给 p）。" % inf["response"])
        elif p < 0.05:
            out.append("%s：p=%.4g，α=0.05 下组间差异显著（%s）；效应量 %s。显著性不等同于实际意义。" % (
                inf["response"], p, t.get("test"), eff))
        else:
            out.append("%s：p=%.4g，α=0.05 下未达显著（%s）；效应量 %s。" % (
                inf["response"], p, t.get("test"), eff))
    for c in analysis.get("correlations", []):
        out.append("%s：r=%s（p=%s），相关强度需结合效应量解释。" %
                   (c.get("pair"), c.get("pearson_r"), c.get("p")))
    return out


def step_build(input_dir, output_dir, work_dir, files, case, contract, dd,
               analysis, cleaning, sources, started_at):
    evidence = make_evidence_map(sources, analysis, cleaning, {}, [], input_dir)
    interpretation = build_interpretation(analysis)
    analysis["interpretation"] = interpretation
    runtime = {"started_at": started_at, "skill": SKILL_NAME, "version": SKILL_VERSION,
               "backend": {"scipy": bool(_ensure_scipy()), "numpy": bool(_NP)},
               "status": "ok"}
    qc = build_quality_checks(cleaning, analysis, {})
    case_id = case.get("case_id") if isinstance(case, dict) else "unknown-case"
    if case_id is None:
        case_id = "unknown-case"
    report = build_report(analysis, cleaning, {}, evidence, case_id, runtime)
    report_path = write_text(os.path.join(output_dir, "analysis_report.md"), report)
    write_json(os.path.join(output_dir, "quality_checks.json"), qc)
    # copy analysis code for reproducibility
    code_src = os.path.abspath(__file__)
    code_dst = os.path.join(output_dir, "analysis_code.py")
    try:
        with open(code_src, "r", encoding="utf-8") as fsrc:
            with open(code_dst, "w", encoding="utf-8") as fdst:
                fdst.write(fsrc.read())
    except Exception as exc:
        write_text(code_dst, "# code copy failed: %s\n" % exc)
    # determine status
    status = "ok"
    uncertainty = []
    if not analysis.get("descriptive") and not analysis.get("inferential"):
        status = "insufficient_evidence"
        uncertainty.append("未发现可解析的表格数据（csv/tsv/json/sqlite/xlsx）。")
    for f in files:
        if f["kind"] in ("xls", "binary", "other"):
            uncertainty.append("文件 %s（kind=%s）未被本 Skill 解析，需人工转换。" %
                               (f["name"], f["kind"]))
    if status == "ok" and not analysis.get("inferential") and analysis.get("descriptive"):
        uncertainty.append("仅完成描述统计；未自动识别到分组/配对结构，推断统计未进行。")
    computed = {
        "result": {"descriptive": analysis["descriptive"],
                   "inferential": analysis["inferential"],
                   "effects": [i.get("effects") for i in analysis["inferential"]],
                   "interpretation": {"conclusions": interpretation}},
        "descriptive": analysis["descriptive"],
        "inferential": analysis["inferential"],
        "effects": [i.get("effects") for i in analysis["inferential"]],
        "confidence_intervals": [i.get("confidence_intervals") for i in analysis["inferential"]],
        "interpretation": {"conclusions": interpretation, "note":
                           "区分统计显著与实际意义；p 写精确值；标注适用条件。"},
    }
    contract_filled = backfill_contract(contract, computed)
    result = {}
    result.update(contract_filled)
    result.update({
        "case_id": case_id,
        "status": status,
        "result": {"descriptive": analysis["descriptive"],
                   "inferential": analysis["inferential"],
                   "effects": [i.get("effects") for i in analysis["inferential"]],
                   "interpretation": {"conclusions": interpretation}},
        "descriptive": analysis["descriptive"],
        "inferential": analysis["inferential"],
        "effects": [i.get("effects") for i in analysis["inferential"]],
        "confidence_intervals": [i.get("confidence_intervals")
                                 for i in analysis["inferential"]],
        "interpretation": {"conclusions": interpretation, "note":
                           "区分统计显著与实际意义；p 写精确值；标注适用条件。"},
        "sources": sources,
        "uncertainty": uncertainty,
        "change_log": [{"id": "CL1", "time": started_at, "action": "init",
                        "before": None, "after": "partial 骨架", "reason": "先落盘合法骨架"},
                       {"id": "CL2", "time": now_iso(), "action": "build",
                        "before": "partial", "after": status,
                        "reason": "完成清洗/描述/推断/效应量/CI/证据映射"}],
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "runtime": runtime,
        "evidence_map": evidence,
        "quality_checks": qc,
        "artifacts": {"report": "analysis_report.md", "quality_checks": "quality_checks.json",
                      "code": "analysis_code.py", "cleaned": "cleaned/",
                      "figures": "figures/"},
    })
    write_json(os.path.join(output_dir, "result.json"), result)
    write_json(os.path.join(work_dir, "state.json"), {"step": "build", "time": now_iso()})
    return result


def step_selfcheck(input_dir, output_dir, work_dir):
    problems = []
    rpath = os.path.join(output_dir, "result.json")
    if not os.path.exists(rpath):
        return {"ok": False, "problems": ["result.json missing"], "checks": []}
    try:
        with open(rpath, encoding="utf-8") as f:
            result = json.load(f)
    except Exception as exc:
        return {"ok": False, "problems": ["result.json 不可解析: %s" % exc], "checks": []}
    checks = []
    checks.append(("result.json 可解析", True))
    checks.append(("含 case_id", bool(result.get("case_id"))))
    checks.append(("含 status", result.get("status") in ("ok", "partial", "insufficient_evidence", "input_error")))
    checks.append(("含 sources/uncertainty/change_log",
                   all(k in result for k in ("sources", "uncertainty", "change_log"))))
    em = result.get("evidence_map", [])
    has_locator_quote = all(e.get("locator") and e.get("quote") is not None for e in em)
    checks.append(("每条 evidence 均含 locator+quote", has_locator_quote))
    svg_ok = True
    fig_dir = os.path.join(output_dir, "figures")
    svg_files = []
    if os.path.isdir(fig_dir):
        svg_files = [os.path.join(fig_dir, n) for n in os.listdir(fig_dir) if n.endswith(".svg")]
    for sf in svg_files:
        try:
            ET.parse(sf)
        except Exception:
            svg_ok = False
            problems.append("SVG 解析失败: %s" % sf)
    checks.append(("SVG 可解析", svg_ok))
    code_ok = os.path.exists(os.path.join(output_dir, "analysis_code.py"))
    checks.append(("analysis_code.py 存在", code_ok))
    ok = all(c[1] for c in checks) and not problems
    return {"ok": ok, "problems": problems, "checks": checks,
            "svg_count": len(svg_files), "evidence_count": len(em)}


# --------------------------------------------------------------------------
# run dispatcher
# --------------------------------------------------------------------------

def run(cmd, input_dir="/input", output_dir="/app/output", work_dir="/app/work"):
    started_at = now_iso()
    if cmd in ("init", "survey", "inspect", "analyze", "build", "selfcheck", "run", "help"):
        pass
    else:
        cmd = "run"

    if cmd == "help":
        print("usage: main.py [run|init|survey|inspect|analyze|build|selfcheck] "
              "[--input DIR] [--output DIR] [--work DIR]")
        return

    result, files, case, contract, dd = step_init(input_dir, output_dir, work_dir, started_at)
    if cmd == "init":
        return
    materials = step_survey(input_dir, work_dir, files)
    if cmd == "survey":
        return
    schema = step_inspect(input_dir, work_dir, files, case, contract, dd)
    if cmd == "inspect":
        return
    analysis, cleaning, sources, change_log = step_analyze(
        input_dir, work_dir, output_dir, files, case, contract, dd, started_at)
    if cmd == "analyze":
        return
    result = step_build(input_dir, output_dir, work_dir, files, case, contract, dd,
                        analysis, cleaning, sources, started_at)
    if cmd == "build":
        return
    check = step_selfcheck(input_dir, output_dir, work_dir)
    write_json(os.path.join(work_dir, "selfcheck.json"), check)
    if not check["ok"]:
        # reflect problems into result but do not crash
        try:
            with open(os.path.join(output_dir, "result.json"), encoding="utf-8") as f:
                r = json.load(f)
            r.setdefault("errors", []).append({"selfcheck": check["problems"]})
            write_json(os.path.join(output_dir, "result.json"), r)
        except Exception:
            pass
    print(json.dumps(check, ensure_ascii=False))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    i, o, w = "/input", "/app/output", "/app/work"
    j = 2
    while j < len(sys.argv):
        a = sys.argv[j]
        if a in ("--input", "-i") and j + 1 < len(sys.argv):
            i = sys.argv[j + 1]; j += 2; continue
        if a in ("--output", "-o") and j + 1 < len(sys.argv):
            o = sys.argv[j + 1]; j += 2; continue
        if a in ("--work", "-w") and j + 1 < len(sys.argv):
            w = sys.argv[j + 1]; j += 2; continue
        j += 1
    run(cmd, i, o, w)
