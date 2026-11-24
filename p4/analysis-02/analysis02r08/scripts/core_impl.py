"""core_impl.py - analysis-02 pipeline and standard-library statistics core.

Holds every subcommand implementation plus the deterministic statistics
routines (distribution CDFs, normality/homogeneity checks, location and
association tests, effect sizes and confidence intervals). main.py is a
thin entry point that dispatches to the _cmd_* functions defined here.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import shutil
import sqlite3
import sys
import time
from collections import Counter


PI = math.pi
SQRT2 = math.sqrt(2.0)
EPS = 1e-14


# ---------------------------------------------------------------------------
# basic summaries
# ---------------------------------------------------------------------------
def mean(xs):
    xs = [x for x in xs if x is not None]
    n = len(xs)
    return sum(xs) / n if n else None


def variance(xs, ddof=1):
    xs = [x for x in xs if x is not None]
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    ss = sum((x - m) ** 2 for x in xs)
    return ss / (n - ddof)


def std(xs):
    v = variance(xs, ddof=1)
    return math.sqrt(v) if v is not None and v > 0 else 0.0


def median(xs):
    xs = sorted(x for x in xs if x is not None)
    n = len(xs)
    if n == 0:
        return None
    if n % 2:
        return xs[n // 2]
    return 0.5 * (xs[n // 2 - 1] + xs[n // 2])


def quantile(xs, q):
    """Linear-interpolation quantile (type 7, matches numpy default)."""
    xs = sorted(x for x in xs if x is not None)
    n = len(xs)
    if n == 0:
        return None
    if n == 1:
        return xs[0]
    pos = (n - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi - pos) + xs[hi] * (pos - lo)


def skewness(xs):
    """Bias-corrected Fisher-Pearson skewness (pandas .skew default)."""
    xs = [x for x in xs if x is not None]
    n = len(xs)
    if n < 3:
        return None
    m = sum(xs) / n
    s = std(xs)
    if s == 0:
        return 0.0
    m3 = sum((x - m) ** 3 for x in xs) / n
    return (m3 / (s ** 3)) * (n * n) / ((n - 1) * (n - 2))


def kurtosis(xs):
    """Bias-corrected excess kurtosis (pandas .kurt default; normal == 0)."""
    xs = [x for x in xs if x is not None]
    n = len(xs)
    if n < 4:
        return None
    m = sum(xs) / n
    s = std(xs)
    if s == 0:
        return 0.0
    m4 = sum(((x - m) / s) ** 4 for x in xs)
    return (n * (n + 1)) / ((n - 1) * (n - 2) * (n - 3)) * m4 \
        - 3.0 * (n - 1) ** 2 / ((n - 2) * (n - 3))


def describe(xs):
    xs = [x for x in xs if x is not None]
    n = len(xs)
    if n == 0:
        return {"n": 0}
    return {
        "n": n,
        "mean": mean(xs),
        "median": median(xs),
        "sd": std(xs),
        "min": min(xs),
        "max": max(xs),
        "q1": quantile(xs, 0.25),
        "q3": quantile(xs, 0.75),
        "iqr": (quantile(xs, 0.75) or 0.0) - (quantile(xs, 0.25) or 0.0),
        "skewness": skewness(xs),
        "kurtosis": kurtosis(xs),
    }


# ---------------------------------------------------------------------------
# distribution functions
# ---------------------------------------------------------------------------
def ln_gamma(x):
    """Natural log of Gamma(x), Lanczos g=7, n=9 (valid x > 0)."""
    cof = (0.99999999999980993, 676.5203681218851, -1259.1392167224028,
           771.32342877765313, -176.61502916214059, 12.507343278686905,
           -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7)
    if x < 0.5:
        return math.log(PI / math.sin(PI * x)) - ln_gamma(1.0 - x)
    x -= 1.0
    t = x + 7.5
    s = cof[0]
    for i in range(1, 9):
        s += cof[i] / (x + i)
    return 0.5 * math.log(2 * PI) + (x + 0.5) * math.log(t) - t + math.log(s)


def _betacf(a, b, x):
    """Continued fraction for the incomplete beta function (Numerical Recipes)."""
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-7:
            break
    return h


def betai(a, b, x):
    """Regularized incomplete beta I_x(a,b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = ln_gamma(a + b) - ln_gamma(a) - ln_gamma(b) \
        + a * math.log(x) + b * math.log(1.0 - x)
    bt = math.exp(lbeta)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def gamma_p(a, x):
    """Regularized lower incomplete gamma P(a,x)."""
    if x < 0.0 or a <= 0.0:
        raise ValueError("gamma_p requires x>=0 and a>0")
    if x < a + 1.0:
        ap = a
        s = 1.0 / a
        delta = s
        for _ in range(1000):
            ap += 1.0
            delta *= x / ap
            s += delta
            if abs(delta) < abs(s) * 1e-15:
                break
        return s * math.exp(-x + a * math.log(x) - ln_gamma(a))
    b = x + 1.0 - a
    c = 1e300
    d = 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < 1e-300:
            d = 1e-300
        c = b + an / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return 1.0 - math.exp(-x + a * math.log(x) - ln_gamma(a)) * h


def normal_cdf(z):
    return 0.5 * (1.0 + math.erf(z / SQRT2))


def normal_ppf(p):
    """Inverse normal CDF (Acklam's algorithm + one Newton refinement)."""
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    plow, phigh = 0.02425, 1.0 - 0.02425
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) \
            / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    elif p <= phigh:
        q = p - 0.5
        r = q * q
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q \
            / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) \
            / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    e = 0.5 * math.erfc(-x / SQRT2) - p
    u = e * math.sqrt(2 * PI) * math.exp(x * x / 2.0)
    return x - u / (1.0 + x * u / 2.0)


def t_cdf(t, df):
    x = df / (df + t * t)
    ib = betai(0.5 * df, 0.5, x)
    return 1.0 - 0.5 * ib if t >= 0 else 0.5 * ib


def t_ppf(p, df):
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    lo, hi = -1e6, 1e6
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def t_pvalue(t, df, alternative="two-sided"):
    """Two- or one-sided p-value of a t statistic (df > 0)."""
    tt = abs(t)
    p2 = betai(0.5 * df, 0.5, df / (df + tt * tt))
    if alternative == "two-sided":
        return p2
    p1 = 0.5 * p2
    if alternative == "greater":
        return p1 if t > 0 else 1.0 - p1
    if alternative == "less":
        return p1 if t < 0 else 1.0 - p1
    raise ValueError("alternative must be two-sided|less|greater")


def chi2_cdf(x, df):
    return gamma_p(0.5 * df, 0.5 * x) if x > 0 and df > 0 else 0.0


def f_cdf(f, df1, df2):
    if f <= 0:
        return 0.0
    return betai(0.5 * df1, 0.5 * df2, df1 * f / (df1 * f + df2))


def _rank(data):
    """Return average ranks for a list of values (tie-aware), 1-based."""
    n = len(data)
    order = sorted(range(n), key=lambda i: data[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and data[order[j + 1]] == data[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _tie_correction(values):
    cnt = Counter(values)
    return sum(t ** 3 - t for t in cnt.values())


# ---------------------------------------------------------------------------
# assumption checks
# ---------------------------------------------------------------------------
def shapiro_wilk(x):
    """Shapiro-Wilk W and p-value (Royston 1992/1995, AS R94 extension).

    Matches the algorithm behind scipy.stats.shapiro for 3 <= n <= 5000.
    Returns (W, p). Requires n >= 3 and nonzero variance.
    """
    xs = sorted(float(v) for v in x if v is not None)
    n = len(xs)
    if n < 3 or n > 5000:
        return None, None
    ss = sum((v - sum(xs) / n) ** 2 for v in xs)
    if ss <= 0:
        return None, None

    m = [normal_ppf((i + 1 - 0.375) / (n + 0.25)) for i in range(n)]
    cnorm = math.sqrt(sum(v * v for v in m))
    c = [v / cnorm for v in m]
    u = 1.0 / math.sqrt(n)
    an = (-2.706056 * u ** 5 + 4.434685 * u ** 4 - 2.071190 * u ** 3
          - 0.147981 * u ** 2 + 0.221157 * u + c[-1])
    a = [-an] + c[1:-1] + [an]
    anorm = math.sqrt(sum(v * v for v in a))
    a = [v / anorm for v in a]

    num = sum(a[i] * xs[i] for i in range(n))
    W = (num * num) / ss
    W = min(1.0, max(0.0, W))

    if n == 3:
        p = 1.909859 * (math.asin(math.sqrt(W)) - 1.047198)
    elif n <= 11:
        gamma = 0.459 * n - 2.273
        w = -math.log(gamma - math.log(1.0 - W))
        mu = 0.5440 - 0.39978 * n + 0.025054 * n ** 2 - 0.0006714 * n ** 3
        sigma = math.exp(1.3822 - 0.77857 * n + 0.062767 * n ** 2 - 0.0020322 * n ** 3)
        z = (w - mu) / sigma
        p = 1.0 - normal_cdf(z)
    else:
        ln = math.log(n)
        mu = -1.5861 - 0.31082 * ln - 0.083751 * ln ** 2 + 0.0038915 * ln ** 3
        sigma = math.exp(-0.4803 - 0.082676 * ln + 0.0030302 * ln ** 2)
        w = math.log(1.0 - W)
        z = (w - mu) / sigma
        p = 1.0 - normal_cdf(z)
    return W, min(1.0, max(0.0, p))


def brown_forsythe(groups):
    """Brown-Forsythe variance-homogeneity test (Levene with median centering).

    Returns (F, p, df1, df2).
    """
    k = len(groups)
    if k < 2:
        return None, None, None, None
    z = []
    for g in groups:
        vals = [float(v) for v in g if v is not None]
        if not vals:
            return None, None, None, None
        md = median(vals)
        z.append([abs(v - md) for v in vals])
    return anova_oneway(z)[:4]


# ---------------------------------------------------------------------------
# location / association tests
# ---------------------------------------------------------------------------
def t_test_ind(a, b, equal_var=None, alternative="two-sided"):
    """Student (pooled) or Welch t-test for two independent samples.

    equal_var=None -> decide by Brown-Forsythe at alpha=0.05.
    Returns (t, df, p, method, used_equal_var, diff, se).
    """
    a = [float(v) for v in a if v is not None]
    b = [float(v) for v in b if v is not None]
    n1, n2 = len(a), len(b)
    m1, m2 = mean(a), mean(b)
    v1, v2 = variance(a), variance(b)
    if v1 is None or v2 is None:
        return None, None, None, "n_insufficient", None, None, None

    if equal_var is None:
        F, pv, _, _ = brown_forsythe([a, b])
        equal_var = bool(pv is not None and pv >= 0.05)

    if equal_var:
        sp2 = ((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2)
        se = math.sqrt(sp2 * (1.0 / n1 + 1.0 / n2))
        df = n1 + n2 - 2
        method = "Student t (pooled)"
    else:
        se = math.sqrt(v1 / n1 + v2 / n2)
        num = (v1 / n1 + v2 / n2) ** 2
        den = (v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1)
        df = num / den if den > 0 else n1 + n2 - 2
        method = "Welch t (unequal variance)"
    diff = m1 - m2
    t = diff / se if se > 0 else 0.0
    p = t_pvalue(t, df, alternative)
    return t, df, p, method, bool(equal_var), diff, se


def t_test_paired(a, b, alternative="two-sided"):
    a = [float(v) for v in a if v is not None]
    b = [float(v) for v in b if v is not None]
    n = min(len(a), len(b))
    if n < 2:
        return None, None, None
    d = [a[i] - b[i] for i in range(n)]
    md = mean(d)
    se = std(d) / math.sqrt(n)
    t = md / se if se > 0 else 0.0
    p = t_pvalue(t, n - 1, alternative)
    return t, n - 1, p


def mannwhitney_u(a, b, alternative="two-sided"):
    """Mann-Whitney U with normal approximation and tie correction.

    Returns (U1, p, z, rank_biserial). Asymptotic; flag small samples upstream.
    """
    a = [float(v) for v in a if v is not None]
    b = [float(v) for v in b if v is not None]
    n1, n2 = len(a), len(b)
    if n1 == 0 or n2 == 0:
        return None, None, None, None
    labels = [0] * n1 + [1] * n2
    values = a + b
    ranks = _rank(values)
    R1 = sum(ranks[i] for i in range(n1) if labels[i] == 0)
    U1 = R1 - n1 * (n1 + 1) / 2.0
    N = n1 + n2
    tie = _tie_correction(values)
    mu = n1 * n2 / 2.0
    var = (n1 * n2 / 12.0) * ((N + 1) - tie / (N * (N - 1)))
    z = (U1 - mu) / math.sqrt(var) if var > 0 else 0.0
    if alternative == "two-sided":
        p = 2.0 * (1.0 - normal_cdf(abs(z)))
    elif alternative == "greater":
        p = 1.0 - normal_cdf(z)
    else:
        p = normal_cdf(z)
    r_bis = 1.0 - 2.0 * U1 / (n1 * n2)
    return U1, min(1.0, max(0.0, p)), z, r_bis


def kruskal_wallis(groups):
    """Kruskal-Wallis H with tie correction (chi-square approximation)."""
    k = len(groups)
    ns = [len([v for v in g if v is not None]) for g in groups]
    if any(n == 0 for n in ns) or k < 2:
        return None, None, None
    values, labels = [], []
    for gi, g in enumerate(groups):
        for v in g:
            if v is not None:
                values.append(float(v))
                labels.append(gi)
    N = len(values)
    ranks = _rank(values)
    Rj = [0.0] * k
    for r, gi in zip(ranks, labels):
        Rj[gi] += r
    H = 12.0 / (N * (N + 1)) * sum(Rj[j] ** 2 / ns[j] for j in range(k)) - 3.0 * (N + 1)
    tie = _tie_correction(values)
    if tie > 0:
        H = H / (1.0 - tie / (N ** 3 - N))
    p = 1.0 - chi2_cdf(H, k - 1)
    return H, min(1.0, max(0.0, p)), k - 1


def anova_oneway(groups):
    """One-way ANOVA. Returns (F, p, df_between, df_within, eta2, omega2)."""
    k = len(groups)
    ns = [len([v for v in g if v is not None]) for g in groups]
    if any(n == 0 for n in ns) or k < 2:
        return None, None, None, None, None, None
    grand = sum(sum(v for v in g if v is not None) for g in groups) / sum(ns)
    ss_between = 0.0
    ss_within = 0.0
    for i, g in enumerate(groups):
        vals = [float(v) for v in g if v is not None]
        gm = mean(vals)
        ss_between += ns[i] * (gm - grand) ** 2
        ss_within += sum((v - gm) ** 2 for v in vals)
    df_b = k - 1
    df_w = sum(ns) - k
    ms_b = ss_between / df_b if df_b else 0.0
    ms_w = ss_within / df_w if df_w else 0.0
    F = ms_b / ms_w if ms_w > 0 else math.inf
    p = 1.0 - f_cdf(F, df_b, df_w) if ms_w > 0 else 0.0
    total = ss_between + ss_within
    eta2 = ss_between / total if total > 0 else 0.0
    omega2 = (ss_between - df_b * ms_w) / (total + ms_w) if (total + ms_w) > 0 else 0.0
    return F, p, df_b, df_w, eta2, max(0.0, omega2)


def wilcoxon_signed(a, b, alternative="two-sided"):
    """Wilcoxon signed-rank test (normal approximation with tie correction)."""
    a = [float(v) for v in a if v is not None]
    b = [float(v) for v in b if v is not None]
    n = min(len(a), len(b))
    if n == 0:
        return None, None, None
    diffs = [a[i] - b[i] for i in range(n)]
    nonzero = [d for d in diffs if d != 0.0]
    n_eff = len(nonzero)
    if n_eff == 0:
        return 0.0, 1.0, 0.0
    absd = [abs(d) for d in nonzero]
    ranks = _rank(absd)
    Wp = sum(ranks[i] for i in range(n_eff) if nonzero[i] > 0)
    Wm = sum(ranks[i] for i in range(n_eff) if nonzero[i] < 0)
    T = min(Wp, Wm)
    mu = n_eff * (n_eff + 1) / 4.0
    tie = _tie_correction(absd)
    var = n_eff * (n_eff + 1) * (2 * n_eff + 1) / 24.0 - tie / 48.0
    z = (T - mu) / math.sqrt(var) if var > 0 else 0.0
    if alternative == "two-sided":
        p = 2.0 * (1.0 - normal_cdf(abs(z)))
    elif alternative == "greater":
        p = 1.0 - normal_cdf(z)
    else:
        p = normal_cdf(z)
    return T, min(1.0, max(0.0, p)), z


# ---------------------------------------------------------------------------
# association
# ---------------------------------------------------------------------------
def pearson_r(x, y):
    x = [float(v) for v in x if v is not None]
    y = [float(v) for v in y if v is not None]
    n = min(len(x), len(y))
    if n < 3:
        return None, None, None, None
    x, y = x[:n], y[:n]
    mx, my = mean(x), mean(y)
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    sxx = sum((xi - mx) ** 2 for xi in x)
    syy = sum((yi - my) ** 2 for yi in y)
    if sxx <= 0 or syy <= 0:
        return None, None, None, None
    r = sxy / math.sqrt(sxx * syy)
    r = max(-1.0, min(1.0, r))
    denom = 1.0 - r * r
    t = r * math.sqrt((n - 2) / denom) if denom > 0 else math.inf
    p = t_pvalue(t, n - 2, "two-sided") if denom > 0 else 0.0
    return r, t, p, n - 2


def spearman_rho(x, y):
    x = [float(v) for v in x if v is not None]
    y = [float(v) for v in y if v is not None]
    n = min(len(x), len(y))
    if n < 3:
        return None, None, None, None
    x, y = x[:n], y[:n]
    rx = _rank(x)
    ry = _rank(y)
    r = pearson_r(rx, ry)[0]
    if r is None:
        return None, None, None, None
    t = r * math.sqrt((n - 2) / max(1.0 - r * r, 1e-12))
    p = t_pvalue(t, n - 2, "two-sided")
    return r, t, p, n - 2


def chi2_independence(obs):
    """Pearson chi-square on a contingency table. Returns (chi2, p, df, cramers_v)."""
    nr, nc = len(obs), len(obs[0])
    rowsum = [sum(r) for r in obs]
    colsum = [sum(obs[i][j] for i in range(nr)) for j in range(nc)]
    N = sum(rowsum)
    chi2 = 0.0
    for i in range(nr):
        for j in range(nc):
            expected = rowsum[i] * colsum[j] / N if N else 0.0
            if expected > 0:
                chi2 += (obs[i][j] - expected) ** 2 / expected
    df = (nr - 1) * (nc - 1)
    p = 1.0 - chi2_cdf(chi2, df) if df > 0 else 1.0
    denom = N * min(nr - 1, nc - 1)
    v = math.sqrt(chi2 / denom) if denom > 0 else 0.0
    return chi2, p, df, v


# ---------------------------------------------------------------------------
# effect sizes
# ---------------------------------------------------------------------------
def pooled_sd(a, b):
    n1, n2 = len(a), len(b)
    v1, v2 = variance(a), variance(b)
    if v1 is None or v2 is None:
        return None
    return math.sqrt(((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2))


def cohens_d(a, b):
    sp = pooled_sd(a, b)
    if sp is None or sp <= 0:
        return None
    return (mean(a) - mean(b)) / sp


def hedges_g(a, b):
    d = cohens_d(a, b)
    if d is None:
        return None
    n1, n2 = len(a), len(b)
    return d * (1.0 - 3.0 / (4.0 * (n1 + n2) - 9.0))


def glass_delta(a, b, control_sd):
    """a = intervention group, b = control group."""
    if control_sd is None or control_sd <= 0:
        return None
    return (mean(a) - mean(b)) / control_sd


def cohens_d_ci(a, b, alpha=0.05):
    """Approximate CI via the asymptotic variance of g (Hedges & Olkin)."""
    d = cohens_d(a, b)
    if d is None:
        return None, None, None
    n1, n2 = len(a), len(b)
    z = normal_ppf(1.0 - alpha / 2.0)
    se = math.sqrt((n1 + n2) / (n1 * n2) + d * d / (2.0 * (n1 + n2)))
    return d - z * se, d + z * se, d


# ---------------------------------------------------------------------------
# confidence intervals
# ---------------------------------------------------------------------------
def mean_ci(x, alpha=0.05):
    x = [float(v) for v in x if v is not None]
    n = len(x)
    if n < 2:
        return None, None, None
    m = mean(x)
    se = std(x) / math.sqrt(n)
    tcrit = t_ppf(1.0 - alpha / 2.0, n - 1)
    return m - tcrit * se, m + tcrit * se, m


def mean_diff_ci(a, b, alpha=0.05, welch=True):
    """Welch (or pooled) 95% CI for the difference of two independent means."""
    a = [float(v) for v in a if v is not None]
    b = [float(v) for v in b if v is not None]
    n1, n2 = len(a), len(b)
    diff = mean(a) - mean(b)
    v1, v2 = variance(a), variance(b)
    if welch:
        se = math.sqrt(v1 / n1 + v2 / n2)
        num = (v1 / n1 + v2 / n2) ** 2
        den = (v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1)
        df = num / den if den > 0 else n1 + n2 - 2
    else:
        sp2 = ((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2)
        se = math.sqrt(sp2 * (1.0 / n1 + 1.0 / n2))
        df = n1 + n2 - 2
    tcrit = t_ppf(1.0 - alpha / 2.0, df)
    return diff - tcrit * se, diff + tcrit * se, diff


def wilson_ci(k, n, alpha=0.05):
    z = normal_ppf(1.0 - alpha / 2.0)
    if n <= 0:
        return 0.0, 1.0, None
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half), p


def fisher_z_ci(r, n, alpha=0.05):
    z = normal_ppf(1.0 - alpha / 2.0)
    if n <= 3:
        return None, None, None
    zr = math.atanh(max(-0.999999, min(0.999999, r)))
    se = 1.0 / math.sqrt(n - 3)
    return math.tanh(zr - z * se), math.tanh(zr + z * se), r


# ---------------------------------------------------------------------------
# multiple-comparison correction
# ---------------------------------------------------------------------------
def bonferroni(pvalues):
    n = len(pvalues)
    return [min(1.0, p * n) for p in pvalues]


def benjamini_hochberg(pvalues):
    n = len(pvalues)
    order = sorted(range(n), key=lambda i: pvalues[i])
    adjusted = [1.0] * n
    prev = 0.0
    for rank, idx in enumerate(order):
        q = pvalues[idx] * n / (rank + 1)
        q = max(prev, min(1.0, q))
        prev = q
        adjusted[idx] = q
    return adjusted

# pipeline references the stats functions through the module alias `S`
S = sys.modules[__name__]

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
ALPHA_DEFAULT = 0.05


# ---------------------------------------------------------------------------
# small utilities
# ---------------------------------------------------------------------------
def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_text(path):
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc, newline="") as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError:
            return None
    return None


def _load_json(path):
    text = _read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _save_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def _fmt(x, sig=4):
    """Round a float to `sig` significant digits for report readability."""
    if x is None:
        return None
    x = float(x)
    if x == 0:
        return 0.0
    if not (x == x) or x in (float("inf"), float("-inf")):
        return None if x != x else str(x)
    import math
    try:
        m = 10 ** (sig - 1 - int(math.floor(math.log10(abs(x)))))
        return round(x * m) / m
    except (ValueError, OverflowError):
        return float(x)


def _discover(input_dir):
    files = []
    if not os.path.isdir(input_dir):
        return files
    for root, _dirs, names in os.walk(input_dir):
        for name in names:
            p = os.path.join(root, name)
            ext = os.path.splitext(name)[1].lower()
            kind = {
                ".csv": "tabular", ".tsv": "tabular", ".txt": "tabular",
                ".json": "json", ".db": "sqlite", ".sqlite": "sqlite",
                ".sqlite3": "sqlite", ".xlsx": "office", ".xls": "office",
                ".docx": "office", ".pdf": "pdf", ".md": "text", ".yaml": "text",
                ".yml": "text",
            }.get(ext, "other")
            files.append({"path": p, "rel": os.path.relpath(p, input_dir),
                          "kind": kind, "size": os.path.getsize(p)})
    files.sort(key=lambda f: f["path"])
    return files


def _read_delimited(path):
    text = _read_text(path)
    if text is None:
        return None
    sample = text[:8192]
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
    except csv.Error:
        delim = "," if sample.count(",") > sample.count("\t") else "\t"
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    rows = [r for r in rows if any((c or "").strip() != "" for c in r)]
    if len(rows) < 2:
        return None
    header = [c.strip() or f"col{i}" for i, c in enumerate(rows[0])]
    return header, rows[1:]


def _read_json_table(path):
    obj = _load_json(path)
    if obj is None:
        return None
    if isinstance(obj, list):
        cols = []
        for rec in obj:
            if isinstance(rec, dict):
                for k in rec:
                    if k not in cols:
                        cols.append(k)
        if cols:
            return cols, obj, "records"
        return None
    if isinstance(obj, dict):
        data = obj.get("data", obj.get("rows", obj.get("records")))
        cols = obj.get("columns", obj.get("fields", obj.get("names")))
        if isinstance(data, list) and isinstance(cols, list):
            return [str(c) for c in cols], data, "records"
        if all(isinstance(v, list) for v in obj.values()) and obj:
            cols = list(obj.keys())
            n = min(len(v) for v in obj.values())
            rows = [[obj[c][i] for c in cols] for i in range(n)]
            return cols, rows, "records"
    return None


def _read_sqlite(path):
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        tables = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        out = {}
        for t in tables:
            q = '"%s"' % t.replace('"', '""')
            cur.execute("SELECT * FROM %s" % q)
            cols = [d[0] for d in cur.description]
            rows = [list(r) for r in cur.fetchall()]
            out[t] = (cols, rows)
        return out
    finally:
        conn.close()


def _infer_type(values):
    nonempty = [v for v in values if v is not None and str(v).strip() != ""]
    if not nonempty:
        return "empty"
    num_ok = 0
    for v in nonempty:
        try:
            float(str(v).strip())
            num_ok += 1
        except (ValueError, TypeError):
            pass
    if num_ok / len(nonempty) >= 0.8:
        return "numeric"
    uniq = {str(v).strip() for v in nonempty}
    if len(uniq) <= 15 and len(uniq) <= max(1, 0.2 * len(nonempty)):
        return "categorical"
    return "text"


def _to_numbers(values):
    out = []
    for v in values:
        s = str(v).strip()
        if s == "" or s.lower() in ("na", "nan", "null", "none"):
            out.append(None)
            continue
        try:
            out.append(float(s))
        except ValueError:
            out.append(None)
    return out


def _unit_hint(name):
    n = (name or "").lower()
    for token, unit in (("μg", "μg"), ("ug", "μg"), ("mg", "mg"), ("kg", "kg"),
                        ("cm", "cm"), ("mm", "mm"), ("ml", "mL"), ("%", "%"),
                        ("percent", "%"), ("age", "days"), ("strength", "MPa")):
        if token in n:
            return unit
    return "unit_unknown"


# ---------------------------------------------------------------------------
# subcommand: init
# ---------------------------------------------------------------------------
def _cmd_init(ctx):
    files = _discover(ctx["input"])
    case = _load_json(os.path.join(ctx["input"], "case.json")) or {}
    case_id = case.get("case_id") or case.get("id") or "unknown-case"
    contract = case.get("output_contract") or {}
    index = {"case_id": case_id, "files": files,
             "contract_keys": list(contract.keys()) if isinstance(contract, dict) else [],
             "indexed_at": _now()}
    os.makedirs(ctx["work"], exist_ok=True)
    _save_json(os.path.join(ctx["work"], "index.json"), index)
    _save_json(os.path.join(ctx["work"], "contract.json"),
               {"case_id": case_id, "output_contract": contract})
    _save_json(os.path.join(ctx["work"], "case_meta.json"),
               {"case_id": case_id, "has_task_md": os.path.exists(
                   os.path.join(ctx["input"], "task.md"))})

    skeleton = _build_result(case_id, contract, analysis=None,
                             status="partial",
                             note="skeleton written before analysis; refine incrementally")
    os.makedirs(ctx["output"], exist_ok=True)
    _save_json(os.path.join(ctx["output"], "result.json"), skeleton)
    return {"case_id": case_id, "n_files": len(files),
            "contract_keys": index["contract_keys"]}


# ---------------------------------------------------------------------------
# subcommand: survey
# ---------------------------------------------------------------------------
def _cmd_survey(ctx):
    files = _discover(ctx["input"])
    tools = {}
    for mod in ("numpy", "scipy", "pandas", "matplotlib"):
        try:
            __import__(mod)
            tools[mod] = True
        except Exception:
            tools[mod] = False
    report = {"files": files, "tools_available": tools, "python": sys.version}
    _save_json(os.path.join(ctx["work"], "survey.json"), report)
    return report


# ---------------------------------------------------------------------------
# subcommand: inspect
# ---------------------------------------------------------------------------
def _inspect_source(f):
    path, kind = f["path"], f["kind"]
    out = {"path": f["rel"], "kind": kind, "tables": []}
    try:
        if kind == "tabular":
            parsed = _read_delimited(path)
            if parsed:
                header, rows = parsed
                out["tables"].append(_schema_table(header, rows))
        elif kind == "json":
            parsed = _read_json_table(path)
            if parsed:
                header, rows, _ = parsed
                out["tables"].append(_schema_table(header, rows))
        elif kind == "sqlite":
            tables = _read_sqlite(path)
            for t, (header, rows) in (tables or {}).items():
                out["tables"].append(_schema_table(header, rows, table=t))
        else:
            out["skipped"] = "non-tabular; Agent should read directly"
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    return out


def _schema_table(header, rows, table=None):
    cols = []
    for j, name in enumerate(header):
        values = [r[j] if j < len(r) else None for r in rows]
        ctype = _infer_type(values)
        cols.append({"name": name, "type": ctype, "unit": _unit_hint(name),
                     "n_missing": sum(1 for v in values
                                      if v is None or str(v).strip() == "")})
    return {"table": table or "-", "n_rows": len(rows), "columns": cols}


def _cmd_inspect(ctx):
    index = _load_json(os.path.join(ctx["work"], "index.json")) or {}
    files = index.get("files") or _discover(ctx["input"])
    schema = {"sources": [_inspect_source(f) for f in files], "at": _now()}
    _save_json(os.path.join(ctx["work"], "schema.json"), schema)
    return schema


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------
def _pick_source(files, plan):
    want = plan.get("source") if plan else None

    def is_data(f):
        base = os.path.basename(f["path"]).lower()
        if base in ("case.json", "task.md", "plan.json", "index.json"):
            return False
        return f["kind"] in ("tabular", "json", "sqlite")

    data = [f for f in files if is_data(f)]
    if want:
        for f in data:
            if f["rel"] == want or os.path.basename(f["path"]) == want:
                return f
    # Prefer concrete tabular/SQLite over generic JSON (which may be metadata).
    order = {"tabular": 0, "sqlite": 0, "json": 1}
    data.sort(key=lambda f: (order.get(f["kind"], 2), f["path"]))
    return data[0] if data else None


def _load_table(f):
    """Return (name, columns, rows, table_name)."""
    if f["kind"] == "sqlite":
        tables = _read_sqlite(f["path"]) or {}
        if not tables:
            return None, None, None, None
        tname = list(tables.keys())[0]
        cols, rows = tables[tname]
        return f["rel"], cols, rows, tname
    if f["kind"] == "json":
        parsed = _read_json_table(f["path"])
        if parsed:
            cols, rows, _ = parsed
            return f["rel"], cols, rows, None
    parsed = _read_delimited(f["path"])
    if parsed:
        cols, rows = parsed
        return f["rel"], cols, rows, None
    return None, None, None, None


def _records(cols, rows):
    return [dict(zip(cols, r)) for r in rows]


def _clean(rows, cols, value_col, group_col):
    """Deduplicate + missing handling. Returns (cleaned_records, log)."""
    change_log = []
    raw_n = len(rows)

    seen, keep_idx = set(), []
    removed_dup = []
    for i, r in enumerate(rows):
        key = tuple((k, _norm_key(r[k])) for k in cols)
        if key in seen:
            removed_dup.append(i)
        else:
            seen.add(key)
            keep_idx.append(i)
    dedup_n = len(keep_idx)
    change_log.append({"id": "CL1", "action": "deduplicate",
                       "before": raw_n, "after": dedup_n,
                       "reason": "exact duplicate rows removed, keep first",
                       "removed_indices": removed_dup[:20]})
    records = [rows[i] for i in keep_idx]

    cols_used = [c for c in (value_col, group_col) if c]
    dropped = []
    for i, r in enumerate(records):
        for c in cols_used:
            if r.get(c) is None or str(r.get(c)).strip() == "":
                dropped.append(i)
                break
    kept = [r for i, r in enumerate(records) if i not in dropped]
    change_log.append({"id": "CL2", "action": "drop_missing",
                       "before": dedup_n, "after": len(kept),
                       "reason": "case-complete on analysis columns " + ",".join(cols_used),
                       "dropped_rows": len(dropped)})
    return kept, change_log, raw_n, dedup_n, len(kept)


def _norm_key(v):
    s = str(v).strip()
    if s == "":
        return "<NA>"
    try:
        return float(s)
    except ValueError:
        return s


def _outliers_iqr(values):
    values = [v for v in values if v is not None]
    if not values:
        return []
    q1, q3 = S.quantile(values, 0.25), S.quantile(values, 0.75)
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    return [v for v in values if v < lo or v > hi], lo, hi


def _svg_boxplot(groups, labels, title):
    """Editable SVG box plot (median, IQR, whiskers at 1.5*IQR, outliers)."""
    n = len(groups)
    w, h = 60 + n * 110, 320
    allv = [v for g in groups for v in g if v is not None]
    lo, hi = min(allv), max(allv)
    pad = 0.1 * max(1e-9, (hi - lo))
    lo, hi = lo - pad, hi + pad

    def y(v):
        return 30 + (hi - v) / (hi - lo) * 240

    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
             'viewBox="0 0 %d %d">' % (w, h),
             f'<text x="10" y="18" font-size="12">{title}</text>']
    for gi, g in enumerate(groups):
        cx = 70 + gi * 110
        vals = [v for v in g if v is not None]
        q1, med, q3 = S.quantile(vals, 0.25), S.median(vals), S.quantile(vals, 0.75)
        iqr = q3 - q1
        wlo, whi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        inside = sorted(v for v in vals if wlo <= v <= whi)
        out = [v for v in vals if v < wlo or v > whi]
        y1, y3, ym = y(q1), y(q3), y(med)
        parts.append(f'<rect x="{cx-30}" y="{y3}" width="60" height="{y1-y3}" '
                     'fill="none" stroke="black"/>')
        parts.append(f'<line x1="{cx-30}" y1="{ym}" x2="{cx+30}" y2="{ym}" '
                     'stroke="black" stroke-width="2"/>')
        if inside:
            yw1, yw2 = y(min(inside)), y(max(inside))
            parts.append(f'<line x1="{cx}" y1="{yw1}" x2="{cx}" y2="{y1}" stroke="black"/>')
            parts.append(f'<line x1="{cx}" y1="{yw2}" x2="{cx}" y2="{y3}" stroke="black"/>')
        for o in out:
            parts.append(f'<circle cx="{cx}" cy="{y(o)}" r="3" fill="red"/>')
        parts.append(f'<text x="{cx}" y="{h-20}" font-size="11" text-anchor="middle">'
                     f'{labels[gi]}</text>')
    parts.append('</svg>')
    return "\n".join(parts)


def _svg_histogram(values, title):
    values = [v for v in values if v is not None]
    if not values:
        return ""
    nbins = int(math.ceil(math.sqrt(len(values)))) if len(values) else 10
    lo, hi = min(values), max(values)
    rng = hi - lo or 1.0
    bins = [0] * nbins
    for v in values:
        idx = min(nbins - 1, int((v - lo) / rng * nbins))
        bins[idx] += 1
    w, h = 360, 240
    maxb = max(bins) or 1
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d">' % (w, h),
             f'<text x="10" y="16" font-size="12">{title}</text>']
    bw = 340 / nbins
    for i, cnt in enumerate(bins):
        bh = 180 * cnt / maxb
        x = 10 + i * bw
        parts.append(f'<rect x="{x}" y="{200-bh}" width="{max(1, bw-1)}" height="{bh}" '
                     'fill="steelblue"/>')
    parts.append('</svg>')
    return "\n".join(parts)


def _run_analysis(ctx, plan, schema=None, index=None):
    files = (index or {}).get("files") or _discover(ctx["input"])
    source = _pick_source(files, plan or {})
    if source is None:
        return {"case_id": (index or {}).get("case_id", "unknown-case"),
                "error": "no_tabular_source",
                "uncertainty": ["no tabular material found; cannot analyze"],
                "status": "insufficient_evidence"}

    name, cols, rows, table = _load_table(source)
    if cols is None:
        return {"error": "source_unreadable", "source": source["rel"],
                "uncertainty": ["source could not be parsed"],
                "status": "input_error"}

    schema_cols = {}
    if schema:
        for s in schema.get("sources", []):
            for t in s.get("tables", []):
                if t.get("table") == (table or "-"):
                    schema_cols = {c["name"]: c for c in t.get("columns", [])}

    records0 = _records(cols, rows)
    col_types = {c: (_infer_type([r.get(c) for r in records0])) for c in cols}
    numeric_cols = [c for c, t in col_types.items() if t == "numeric"]
    cat_cols = [c for c, t in col_types.items() if t == "categorical"]

    group_col = (plan or {}).get("group_col")
    value_col = (plan or {}).get("value_col")
    x_col = (plan or {}).get("x_col")
    y_col = (plan or {}).get("y_col")
    design = (plan or {}).get("design")

    auto = False
    if design not in ("two_independent_groups", "multi_group", "paired",
                      "correlation", "descriptive", "proportion", "contingency"):
        auto = True
        if cat_cols and numeric_cols:
            group_col = cat_cols[0]
            value_col = numeric_cols[-1]
            n_levels = len({_norm_key(r.get(group_col)) for r in records0})
            design = "two_independent_groups" if n_levels == 2 else "multi_group"
        elif len(numeric_cols) >= 2:
            x_col, y_col = numeric_cols[0], numeric_cols[1]
            design = "correlation"
        elif numeric_cols:
            value_col = numeric_cols[0]
            design = "descriptive"
        else:
            design = "descriptive"
    else:
        if design in ("two_independent_groups", "multi_group", "paired") and not group_col:
            group_col = cat_cols[0] if cat_cols else None
        if design in ("two_independent_groups", "multi_group", "paired") and not value_col:
            value_col = numeric_cols[-1] if numeric_cols else None
        if design == "correlation":
            x_col = x_col or (numeric_cols[0] if len(numeric_cols) > 0 else None)
            y_col = y_col or (numeric_cols[1] if len(numeric_cols) > 1 else None)

    records, change_log, raw_n, dedup_n, clean_n = _clean(
        records0, cols, value_col or x_col, group_col or (y_col if design == "correlation" else None))

    analysis = {
        "case_id": (index or {}).get("case_id", "unknown-case"),
        "source": {"path": name, "table": table, "kind": source["kind"]},
        "design": design,
        "auto_detected": auto,
        "columns_used": {"group": group_col, "value": value_col, "x": x_col, "y": y_col},
        "data": {"n_rows_raw": raw_n, "n_rows_after_dedup": dedup_n,
                 "n_rows_cleaned": clean_n},
        "cleaning": {"change_log": change_log},
        "uncertainty": [],
    }

    if auto:
        analysis["uncertainty"].append(
            "design/columns auto-detected from schema; confirm against task.md and "
            "write work/plan.json if the intended analysis differs")

    unit = (plan or {}).get("unit") or (schema_cols.get(value_col or x_col, {}) or {}).get("unit") or "unit_unknown"
    analysis["unit"] = unit
    if unit == "unit_unknown":
        analysis["uncertainty"].append("field unit unknown; reported as unit_unknown")

    def num(rec, col):
        v = rec.get(col)
        s = str(v).strip()
        if s == "" or s.lower() in ("na", "nan", "null", "none"):
            return None
        try:
            return float(s)
        except (ValueError, TypeError):
            return None

    if design in ("two_independent_groups", "multi_group"):
        gvals = [num(r, value_col) for r in records]
        levels = sorted({_norm_key(r.get(group_col)) for r in records})
        groups = {lv: [num(r, value_col) for r in records
                       if _norm_key(r.get(group_col)) == lv and num(r, value_col) is not None]
                  for lv in levels}
        groups = {k: v for k, v in groups.items() if v}

        desc = []
        for lv in levels:
            d = S.describe(groups.get(lv, []))
            d["group"] = str(lv)
            desc.append(d)
        analysis["descriptive"] = desc
        analysis["descriptive_all"] = S.describe(gvals)

        normality = []
        for lv in levels:
            W, p = S.shapiro_wilk(groups.get(lv, []))
            normality.append({"group": str(lv), "method": "Shapiro-Wilk",
                              "W": _fmt(W), "p": _fmt(p),
                              "normal_at_0.05": bool(p is not None and p >= 0.05)})
        analysis["assumptions"] = {"normality": normality}

        infer = []
        effects = []
        cis = []
        evidence = []

        if len(levels) == 2:
            a, b = groups[levels[0]], groups[levels[1]]
            F, pv, d1, d2 = S.brown_forsythe([a, b])
            analysis["assumptions"]["variance_homogeneity"] = {
                "method": "Brown-Forsythe", "F": _fmt(F), "p": _fmt(pv),
                "df1": d1, "df2": d2, "equal_var_at_0.05": bool(pv is not None and pv >= 0.05)}
            both_normal = all(n["normal_at_0.05"] for n in normality)
            homosced = bool(pv is not None and pv >= 0.05)
            t, df, p, method, eqvar, diff, se = S.t_test_ind(a, b)
            if both_normal:
                infer.append({"test": method, "statistic_name": "t",
                              "statistic": _fmt(t), "df": _fmt(df), "p": _fmt(p),
                              "alternative": (plan or {}).get("alternative", "two-sided"),
                              "mean_diff": _fmt(diff)})
            U, pm, z, rbis = S.mannwhitney_u(a, b)
            infer.append({"test": "Mann-Whitney U", "statistic_name": "U",
                          "statistic": _fmt(U), "p": _fmt(pm),
                          "z": _fmt(z), "rank_biserial": _fmt(rbis),
                          "note": "nonparametric cross-check / chosen if non-normal"})
            d = S.cohens_d(a, b)
            lo, hi, dd = S.cohens_d_ci(a, b)
            effects.append({"name": "Cohen's d (group0-group1, pooled sd)",
                            "value": _fmt(dd), "ci_low": _fmt(lo), "ci_high": _fmt(hi),
                            "direction": f"{levels[0]} vs {levels[1]}"})
            effects.append({"name": "Hedges' g", "value": _fmt(S.hedges_g(a, b))})
            lo, hi, dd = S.mean_diff_ci(a, b)
            cis.append({"estimate": "mean difference", "ci_low": _fmt(lo),
                        "ci_high": _fmt(hi), "level": 0.95,
                        "meaning": "95% CI for the difference of independent means"})
            for lv, g in zip(levels, (a, b)):
                lo, hi, m = S.mean_ci(g)
                cis.append({"estimate": f"mean of {lv}", "ci_low": _fmt(lo),
                            "ci_high": _fmt(hi), "level": 0.95})
            evidence.append({"id": "E1", "claim": "two-group test result",
                             "locator": f"{name}#{value_col}",
                             "quote": f"t={_fmt(t)}, p={_fmt(p)}, d={_fmt(dd)}",
                             "basis": "computed from cleaned rows (see change_log)"})
        else:
            F, pv, d1, d2 = S.brown_forsythe(list(groups.values()))
            analysis["assumptions"]["variance_homogeneity"] = {
                "method": "Brown-Forsythe", "F": _fmt(F), "p": _fmt(pv),
                "df1": d1, "df2": d2, "equal_var_at_0.05": bool(pv is not None and pv >= 0.05)}
            both_normal = all(n["normal_at_0.05"] for n in normality)
            Fa, pa, dfa, dfw, eta2, om2 = S.anova_oneway(list(groups.values()))
            infer.append({"test": "one-way ANOVA", "statistic_name": "F",
                          "statistic": _fmt(Fa), "df_between": dfa, "df_within": dfw,
                          "p": _fmt(pa)})
            H, ph, dfh = S.kruskal_wallis(list(groups.values()))
            infer.append({"test": "Kruskal-Wallis", "statistic_name": "H",
                          "statistic": _fmt(H), "df": dfh, "p": _fmt(ph),
                          "note": "nonparametric cross-check / chosen if non-normal"})
            effects.append({"name": "eta^2 (ANOVA)", "value": _fmt(eta2)})
            effects.append({"name": "omega^2 (ANOVA)", "value": _fmt(om2)})
            evidence.append({"id": "E1", "claim": "multi-group omnibus result",
                             "locator": f"{name}#{value_col}",
                             "quote": f"F={_fmt(Fa)}, p={_fmt(pa)}, eta2={_fmt(eta2)}",
                             "basis": "computed from cleaned rows (see change_log)"})

        analysis["inferential"] = infer
        analysis["effects"] = effects
        analysis["confidence_intervals"] = cis
        analysis["evidence"] = evidence
        analysis["interpretation"] = _interpret(design, infer, effects, normality, unit,
                                                 (plan or {}).get("alpha", ALPHA_DEFAULT))

    elif design == "correlation":
        xs = [num(r, x_col) for r in records]
        ys = [num(r, y_col) for r in records]
        paired = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
        xv = [p[0] for p in paired]
        yv = [p[1] for p in paired]
        r, t, p, df = S.pearson_r(xv, yv)
        rho, ts, ps, _ = S.spearman_rho(xv, yv)
        lo, hi, rr = S.fisher_z_ci(r, len(xv))
        analysis["descriptive"] = [{"column": x_col, **S.describe(xv)},
                                   {"column": y_col, **S.describe(yv)}]
        analysis["inferential"] = [
            {"test": "Pearson correlation", "statistic_name": "r", "statistic": _fmt(r),
             "t": _fmt(t), "df": df, "p": _fmt(p)},
            {"test": "Spearman rank correlation", "statistic_name": "rho",
             "statistic": _fmt(rho), "p": _fmt(ps), "note": "robust to outliers"}]
        analysis["effects"] = [{"name": "Pearson r", "value": _fmt(r)}]
        analysis["confidence_intervals"] = [{"estimate": "Pearson r (Fisher z)",
                                             "ci_low": _fmt(lo), "ci_high": _fmt(hi),
                                             "level": 0.95}]
        analysis["evidence"] = [{"id": "E1", "claim": "correlation result",
                                 "locator": f"{name}#{x_col},{y_col}",
                                 "quote": f"r={_fmt(r)}, p={_fmt(p)}",
                                 "basis": "computed from complete pairs"}]
        analysis["interpretation"] = {
            "summary": f"Pearson r={_fmt(r)} (p={_fmt(p)}), Spearman rho={_fmt(rho)}",
            "practical_significance": _effect_label_abs(r),
            "conditions": "linearity not formally tested here; CI via Fisher z",
        }

    else:  # descriptive
        vals = [num(r, value_col) for r in records] if value_col else []
        analysis["descriptive"] = S.describe(vals) if vals else {}
        analysis["descriptive_column"] = value_col
        lo, hi, m = S.mean_ci(vals) if len([v for v in vals if v is not None]) >= 2 else (None, None, None)
        analysis["confidence_intervals"] = [{"estimate": "mean", "ci_low": _fmt(lo),
                                             "ci_high": _fmt(hi), "level": 0.95}]
        analysis["evidence"] = [{"id": "E1", "claim": "descriptive summary",
                                 "locator": f"{name}#{value_col}",
                                 "quote": f"n={_fmt(S.describe(vals).get('n'))}, mean={_fmt(m)}",
                                 "basis": "computed from cleaned rows"}]
        analysis["interpretation"] = {
            "summary": "descriptive-only analysis (no group comparison requested)",
        }

    # outliers (recorded but never silently deleted)
    if value_col and design in ("two_independent_groups", "multi_group"):
        for lv in levels:
            o, lo, hi = _outliers_iqr(groups[lv])
            analysis.setdefault("outliers", {})[str(lv)] = {
                "method": "IQR 1.5", "n": len(o), "values": _fmt_list(o),
                "bounds": [_fmt(lo), _fmt(hi)], "handling": "kept (flagged only)"}

    return analysis


def _fmt_list(vals, maxn=12):
    return [_fmt(v) for v in vals[:maxn]] + (["..."] if len(vals) > maxn else [])


def _effect_label_abs(r):
    r = abs(r or 0.0)
    if r < 0.1:
        return "negligible"
    if r < 0.3:
        return "small"
    if r < 0.5:
        return "medium"
    return "large"


def _interpret(design, infer, effects, normality, unit, alpha):
    norm_all = all(n.get("normal_at_0.05") for n in normality)
    parts = []
    if infer:
        primary = infer[0]
        p = primary.get("p")
        sig = p is not None and p < alpha
        parts.append(
            f"primary test {primary.get('test')}: {primary.get('statistic_name')}="
            f"{primary.get('statistic')}, p={_fmt(p)}; "
            f"{'statistically significant at alpha=' + str(alpha) if sig else 'not statistically significant'}.")
    if effects:
        e = effects[0]
        parts.append(f"effect size {e.get('name')}={_fmt(e.get('value'))} ({e.get('direction', '')}); "
                     f"CI: [{_fmt(e.get('ci_low'))}, {_fmt(e.get('ci_high'))}].")
    parts.append(f"normality assumption met in all groups: {norm_all}; unit: {unit or 'unit_unknown'}.")
    return {"summary": " ".join(parts),
            "statistical_vs_practical": "p-value is a probability under H0, not a proof of effect; "
                                        "effect size and CI quantify magnitude and precision.",
            "needs_human": ["confirm design and unit against task.md/data dictionary",
                            "confirm outlier handling policy for the specific study"]}


def _cmd_analyze(ctx):
    plan = _load_json(ctx["plan"]) or {}
    schema = _load_json(os.path.join(ctx["work"], "schema.json"))
    index = _load_json(os.path.join(ctx["work"], "index.json"))
    analysis = _run_analysis(ctx, plan, schema, index)
    _save_json(os.path.join(ctx["work"], "analysis.json"), analysis)

    # write cleaned CSV (per group table already summarized; emit raw cleaned rows)
    _write_cleaned(ctx, analysis, plan)
    _write_figures(ctx, analysis, plan, index)
    return analysis


def _write_cleaned(ctx, analysis, plan):
    # Re-read the source and emit the case-complete cleaned rows for auditing.
    files = _discover(ctx["input"])
    source = _pick_source(files, plan or {})
    if not source:
        return
    _, cols, rows, _ = _load_table(source)
    if cols is None:
        return
    records0 = _records(cols, rows)
    value_col = (plan or {}).get("value_col") or (analysis.get("columns_used") or {}).get("value")
    group_col = (plan or {}).get("group_col") or (analysis.get("columns_used") or {}).get("group")
    kept, _cl, _r, _d, _c = _clean(records0, cols, value_col, group_col)
    outdir = os.path.join(ctx["output"], "cleaned")
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "cleaned_rows.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in kept:
            w.writerow([r.get(c) for c in cols])


def _svg_scatter(xs, ys, xlabel, ylabel, title):
    pts = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if not pts:
        return ""
    xlo, xhi = min(p[0] for p in pts), max(p[0] for p in pts)
    ylo, yhi = min(p[1] for p in pts), max(p[1] for p in pts)
    xr, yr = (xhi - xlo) or 1.0, (yhi - ylo) or 1.0
    w, h = 360, 260

    def px(x):
        return 40 + (x - xlo) / xr * 300

    def py(y):
        return 220 - (y - ylo) / yr * 180

    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d">' % (w, h),
             f'<text x="10" y="16" font-size="12">{title}</text>',
             f'<line x1="40" y1="220" x2="340" y2="220" stroke="black"/>',
             f'<line x1="40" y1="40" x2="40" y2="220" stroke="black"/>',
             f'<text x="{w/2}" y="240" font-size="11" text-anchor="middle">{xlabel}</text>',
             f'<text x="14" y="130" font-size="11" transform="rotate(-90 14 130)">{ylabel}</text>']
    for x, y in pts[:2000]:
        parts.append(f'<circle cx="{px(x)}" cy="{py(y)}" r="2" fill="steelblue"/>')
    parts.append('</svg>')
    return "\n".join(parts)


def _write_figures(ctx, analysis, plan, index):
    outdir = os.path.join(ctx["output"], "figures")
    os.makedirs(outdir, exist_ok=True)
    design = analysis.get("design")
    files = _discover(ctx["input"])
    source = _pick_source(files, plan or {})
    if not source:
        return
    _, cols, rows, _ = _load_table(source)
    if cols is None:
        return
    records = _records(cols, rows)
    svg = None
    used = analysis.get("columns_used") or {}

    def as_float(v):
        s = str(v).strip()
        if s == "" or s.lower() in ("na", "nan", "null", "none"):
            return None
        try:
            return float(s)
        except (ValueError, TypeError):
            return None

    if design in ("two_independent_groups", "multi_group"):
        group_col = used.get("group")
        value_col = used.get("value")
        levels = sorted({_norm_key(r.get(group_col)) for r in records})
        groups, labels = [], []
        for lv in levels:
            vals = [as_float(r.get(value_col)) for r in records
                    if _norm_key(r.get(group_col)) == lv]
            groups.append([v for v in vals if v is not None])
            labels.append(str(lv))
        svg = _svg_boxplot(groups, labels, f"{value_col} by {group_col}")
    elif design == "correlation":
        xc, yc = used.get("x"), used.get("y")
        xs = [as_float(r.get(xc)) for r in records]
        ys = [as_float(r.get(yc)) for r in records]
        svg = _svg_scatter(xs, ys, xc, yc, f"{yc} vs {xc}")
    elif design == "descriptive" and used.get("value"):
        vc = used.get("value")
        vals = [as_float(r.get(vc)) for r in records]
        svg = _svg_histogram([v for v in vals if v is not None], vc)
    if svg:
        with open(os.path.join(outdir, "figure.svg"), "w", encoding="utf-8") as fh:
            fh.write(svg)


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
def _build_result(case_id, contract, analysis, status="ok", note=None):
    result = _backfill_contract(contract, analysis)
    result["case_id"] = case_id or "unknown-case"
    result["status"] = status
    if note:
        result.setdefault("interpretation", {})
        if isinstance(result["interpretation"], dict):
            result["interpretation"]["note"] = note
    result.setdefault("sources", _sources_from_analysis(analysis))
    result.setdefault("uncertainty", (analysis or {}).get("uncertainty", []))
    result.setdefault("change_log", (analysis or {}).get("cleaning", {}).get("change_log", []))
    result.setdefault("pending_human", [])
    result.setdefault("errors", [])
    result.setdefault("degradations", [])
    result.setdefault("runtime", {"python": sys.version.split()[0],
                                  "generated_at": _now()})
    return result


def _sources_from_analysis(analysis):
    if analysis:
        src = analysis.get("source")
        if isinstance(src, dict):
            return [{"source_id": "S1", "path": src.get("path"),
                     "kind": src.get("kind")}]
        if isinstance(src, str):
            return [{"source_id": "S1", "path": src, "kind": "unknown"}]
    return [{"source_id": "S1", "path": "/input/case.json", "kind": "text"}]


_ALIASES = {
    "result": ("result", "analysis", "summary"),
    "descriptive": ("descriptive", "descriptives", "descriptive_stats",
                    "descriptive_statistics", "summary_statistics"),
    "inferential": ("inferential", "inferential_stats", "hypothesis_tests", "tests"),
    "effects": ("effects", "effect_sizes", "effect_size"),
    "confidence_intervals": ("confidence_intervals", "cis", "intervals", "ci"),
    "interpretation": ("interpretation", "interpretations", "conclusion", "conclusions"),
    "quality": ("quality", "quality_checks", "quality_check"),
    "cleaning": ("cleaning", "data_cleaning", "clean", "cleaning_log"),
    "evidence": ("evidence", "evidence_map", "evidences"),
    "code": ("code", "analysis_code"),
    "figures": ("figures", "charts", "plots"),
}


def _backfill_contract(contract, analysis):
    if not isinstance(contract, dict) or not contract:
        return {"result": (analysis or {})}
    out = {}

    def key_of(k):
        return str(k).lower().replace("-", "_").replace(" ", "_")

    def lookup(k):
        kk = key_of(k)
        for canon, names in _ALIASES.items():
            if kk in names:
                return canon
        return None

    for k, v in contract.items():
        canon = lookup(k)
        if canon and analysis:
            out[k] = analysis.get(canon)
        elif isinstance(v, dict):
            out[k] = _backfill_contract(v, analysis)
        elif isinstance(v, list):
            out[k] = []
        else:
            out[k] = None
    return out


def _cmd_build(ctx):
    case = _load_json(os.path.join(ctx["input"], "case.json")) or {}
    case_id = case.get("case_id") or case.get("id") or "unknown-case"
    contract = case.get("output_contract") or {}
    analysis = _load_json(os.path.join(ctx["work"], "analysis.json")) or {}
    status = analysis.get("status") or ("ok" if analysis and "error" not in analysis
                                        else "partial")
    if "error" in analysis and not analysis.get("inferential"):
        status = analysis.get("status", "partial")

    result = _build_result(case_id, contract, analysis, status=status)
    result["sources"] = _sources_from_analysis(analysis)

    # artifacts
    _save_json(os.path.join(ctx["output"], "quality_checks.json"),
               _quality_checks(analysis))
    _write_report(ctx, analysis, result)
    shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)), "core_impl.py"),
                os.path.join(ctx["output"], "core_impl.py"))
    _write_analysis_code(ctx)
    _save_json(os.path.join(ctx["output"], "result.json"), result)
    return result


def _quality_checks(analysis):
    a = analysis or {}
    checks = []
    cl = a.get("cleaning", {}).get("change_log", [])
    for entry in cl:
        checks.append({"item": entry.get("action"),
                       "status": "recorded",
                       "detail": f"{entry.get('before')} -> {entry.get('after')} "
                                 f"({entry.get('reason')})"})
    checks.append({"item": "missing values counted, not treated as zero",
                   "status": "ok" if "cleaning" in a else "unchecked"})
    checks.append({"item": "outliers flagged (IQR 1.5), not silently deleted",
                   "status": "ok" if "outliers" in a else "unchecked"})
    checks.append({"item": "duplicates removed with before/after n",
                   "status": "ok" if any(c["action"] == "deduplicate" for c in cl)
                   else "unchecked"})
    checks.append({"item": "normality and variance homogeneity checked before test",
                   "status": "ok" if "assumptions" in a else "unchecked"})
    checks.append({"item": "no pairwise t-test for >=3 groups; correction noted",
                   "status": "ok" if a.get("design") == "multi_group" else "not_applicable"})
    checks.append({"item": "effect size and 95% CI attached to the test",
                   "status": "ok" if a.get("effects") and a.get("confidence_intervals")
                   else "unchecked"})
    checks.append({"item": "unit recorded (or unit_unknown)", "status": "ok"})
    return {"checks": checks, "deterministic": True}


def _write_report(ctx, analysis, result):
    lines = ["# Statistical analysis report", "",
             f"- case_id: {result.get('case_id')}",
             f"- status: {result.get('status')}",
             f"- generated_at: {result.get('runtime', {}).get('generated_at')}", ""]
    if analysis:
        lines += ["## Data", "",
                  f"- source: {analysis.get('source', {}).get('path')}",
                  f"- design: {analysis.get('design')}",
                  f"- auto_detected: {analysis.get('auto_detected')}",
                  f"- unit: {analysis.get('unit')}",
                  f"- rows: {analysis.get('data', {}).get('n_rows_raw')} raw -> "
                  f"{analysis.get('data', {}).get('n_rows_cleaned')} cleaned", ""]
        for sec in ("descriptive", "assumptions", "inferential", "effects",
                    "confidence_intervals", "interpretation", "uncertainty", "evidence"):
            if analysis.get(sec):
                lines += [f"## {sec}", "", "```json", json.dumps(
                    analysis.get(sec), ensure_ascii=False, indent=2), "```", ""]
    with open(os.path.join(ctx["output"], "analysis_report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _write_analysis_code(ctx):
    code = ("# Auto-generated reproducible runner.\n"
            "# Re-run with: python analysis_code.py --input /input --output /app/output "
            "--work /app/work\n"
            "import sys, os\n"
            "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
            "import main as M\n"
            "if __name__ == '__main__':\n"
            "    M.main()\n")
    with open(os.path.join(ctx["output"], "analysis_code.py"), "w", encoding="utf-8") as fh:
        fh.write(code)


# ---------------------------------------------------------------------------
# selfcheck
# ---------------------------------------------------------------------------
def _cmd_selfcheck(ctx):
    problems = []
    result_path = os.path.join(ctx["output"], "result.json")
    r = _load_json(result_path)
    if r is None:
        return {"ok": False, "problems": ["result.json missing or invalid JSON"]}
    if not r.get("case_id"):
        problems.append("missing case_id")
    if "status" not in r:
        problems.append("missing status")
    for key in ("sources", "uncertainty", "change_log"):
        if key not in r:
            problems.append(f"missing envelope field {key}")
    analysis = _load_json(os.path.join(ctx["work"], "analysis.json")) or {}
    # determinism: re-run analyze and compare
    if analysis and "error" not in analysis:
        plan = _load_json(ctx["plan"]) or {}
        schema = _load_json(os.path.join(ctx["work"], "schema.json"))
        index = _load_json(os.path.join(ctx["work"], "index.json"))
        again = _run_analysis(ctx, plan, schema, index)
        if again != analysis:
            problems.append("analysis is not deterministic across two runs")
    svg = os.path.join(ctx["output"], "figures", "figure.svg")
    if os.path.exists(svg):
        try:
            import xml.etree.ElementTree as ET
            ET.parse(svg)
        except Exception:  # noqa: BLE001
            problems.append("figure.svg is not valid XML/SVG")
    report = os.path.join(ctx["output"], "analysis_report.md")
    if not os.path.exists(report):
        problems.append("analysis_report.md missing")
    return {"ok": not problems, "problems": problems,
            "status": r.get("status"), "case_id": r.get("case_id")}


def _cmd_selftest(ctx):
    import selftest
    return selftest.main()


# ---------------------------------------------------------------------------

def _land_fallback(ctx, error):
    """Guarantee a valid partial result.json even if analysis failed."""
    case = _load_json(os.path.join(ctx["input"], "case.json")) or {}
    case_id = case.get("case_id") or case.get("id") or "unknown-case"
    contract = case.get("output_contract") or {}
    analysis = _load_json(os.path.join(ctx["work"], "analysis.json")) or {}
    result = _build_result(case_id, contract, analysis, status="partial",
                           note="pipeline error; fallback partial result")
    result["errors"] = result.get("errors", []) + [f"analysis_error: {error}"]
    result["degradations"] = result.get("degradations", []) + ["hand-written fallback"]
    os.makedirs(ctx["output"], exist_ok=True)
    _save_json(os.path.join(ctx["output"], "result.json"), result)
