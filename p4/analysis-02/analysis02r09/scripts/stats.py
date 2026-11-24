#!/usr/bin/env python3
"""stats.py — deterministic statistical functions, standard library only.

All numerical routines are implemented from documented definitions so that the
same input always yields the same output.  Reference implementations:
  * normal quantile: Acklam's algorithm
  * log-gamma: Lanczos (g=7, n=9)
  * regularized incomplete beta / gamma: Numerical-Recipes continued fractions
  * Shapiro-Wilk: Royston (1982/1992) approximation, AS R94, valid 3 <= n <= 5000
    (Statistics and Computing 2:117-119, doi:10.1007/BF01891203)

Run `python3 stats.py` to execute the inline self-tests (exit code 0 on pass).
"""

from __future__ import annotations

import math
from math import exp, log, pi, sqrt, asin, floor, ceil, erf

FPMIN = 1e-300
EPS = 1e-14
SQRT2 = sqrt(2.0)


# --------------------------------------------------------------------------
# Special functions
# --------------------------------------------------------------------------
def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / SQRT2))


def norm_sf(x: float) -> float:
    return 1.0 - norm_cdf(x)


def norm_ppf(p: float) -> float:
    """Inverse standard-normal CDF (Acklam)."""
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
        q = sqrt(-2.0 * log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if p > phigh:
        q = sqrt(-2.0 * log(1.0 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    q = p - 0.5
    r = q * q
    num = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
    den = (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    return num / den


def log_gamma(x: float) -> float:
    """Log-gamma via Lanczos approximation."""
    if x < 0.5:
        return log(pi) - log(math.sin(pi * x)) - log_gamma(1.0 - x)
    cof = (0.99999999999980993, 676.5203681218851, -1259.1392167224028,
           771.32342877765313, -176.61502916214059, 12.507343278686905,
           -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7)
    z = x - 1.0
    ser = cof[0]
    for i in range(1, 9):
        ser += cof[i] / (z + i)
    t = z + 7.5
    return 0.5 * log(2.0 * pi) + (z + 0.5) * log(t) - t + log(ser)


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for incomplete beta (Numerical Recipes)."""
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, 301):
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
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < EPS:
            break
    return h


def reg_beta_i(x: float, a: float, b: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = exp(log_gamma(a + b) - log_gamma(a) - log_gamma(b)
             + a * log(x) + b * log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def gammp(a: float, x: float) -> float:
    """Regularized lower incomplete gamma P(a, x)."""
    if x < 0.0 or a <= 0.0:
        raise ValueError("gammp requires a>0, x>=0")
    if x < a + 1.0:
        ap = a
        total = 1.0 / a
        term = total
        for _ in range(1, 400):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) < abs(total) * EPS:
                break
        return total * exp(-x + a * log(x) - log_gamma(a))
    # continued fraction for Q(a, x)
    b = x + 1.0 - a
    c = 1.0 / FPMIN
    d = 1.0 / b
    h = d
    for i in range(1, 400):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < FPMIN:
            d = FPMIN
        c = b + an / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < EPS:
            break
    return 1.0 - exp(-x + a * log(x) - log_gamma(a)) * h


def t_cdf(t: float, df: float) -> float:
    if df <= 0:
        return float("nan")
    x = df / (df + t * t)
    ib = reg_beta_i(x, df / 2.0, 0.5)
    return 1.0 - 0.5 * ib if t > 0 else 0.5 * ib


def t_two_sided_p(t: float, df: float) -> float:
    return reg_beta_i(df / (df + t * t), df / 2.0, 0.5)


def t_ppf(p: float, df: float) -> float:
    """Two-sided-safe t quantile via bisection (for CIs)."""
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


def f_cdf(x: float, d1: float, d2: float) -> float:
    if x <= 0.0:
        return 0.0
    return reg_beta_i(d1 * x / (d1 * x + d2), d1 / 2.0, d2 / 2.0)


def f_sf(x: float, d1: float, d2: float) -> float:
    return 1.0 - f_cdf(x, d1, d2)


def chi2_cdf(x: float, df: float) -> float:
    if x <= 0.0:
        return 0.0
    return gammp(df / 2.0, x / 2.0)


def chi2_sf(x: float, df: float) -> float:
    return 1.0 - chi2_cdf(x, df)


# --------------------------------------------------------------------------
# Descriptive statistics
# --------------------------------------------------------------------------
def mean(x) -> float:
    if not x:
        return float("nan")
    return sum(x) / len(x)


def median(x) -> float:
    x = sorted(x)
    n = len(x)
    if n == 0:
        return float("nan")
    mid = n // 2
    if n % 2:
        return x[mid]
    return 0.5 * (x[mid - 1] + x[mid])


def variance(x, ddof: int = 1) -> float:
    n = len(x)
    if n <= ddof:
        return float("nan")
    m = mean(x)
    return sum((v - m) ** 2 for v in x) / (n - ddof)


def stdev(x, ddof: int = 1) -> float:
    return sqrt(variance(x, ddof)) if variance(x, ddof) >= 0 else float("nan")


def quantile(x, p: float) -> float:
    """Linear-interpolation quantile (matches numpy default 'linear')."""
    x = sorted(x)
    n = len(x)
    if n == 0:
        return float("nan")
    if n == 1:
        return x[0]
    idx = (n - 1) * p
    lo = int(floor(idx))
    hi = int(ceil(idx))
    if lo == hi:
        return x[lo]
    return x[lo] * (1.0 - (idx - lo)) + x[hi] * (idx - lo)


def iqr(x) -> float:
    return quantile(x, 0.75) - quantile(x, 0.25)


def skewness(x) -> float:
    """Adjusted Fisher-Pearson skewness (G1)."""
    n = len(x)
    if n < 3:
        return float("nan")
    m = mean(x)
    m2 = sum((v - m) ** 2 for v in x) / n
    m3 = sum((v - m) ** 3 for v in x) / n
    if m2 <= 0:
        return float("nan")
    g1 = m3 / (m2 ** 1.5)
    return sqrt(n * (n - 1)) / (n - 2) * g1


def kurtosis(x) -> float:
    """Adjusted excess kurtosis (G2)."""
    n = len(x)
    if n < 4:
        return float("nan")
    m = mean(x)
    m2 = sum((v - m) ** 2 for v in x) / n
    m4 = sum((v - m) ** 4 for v in x) / n
    if m2 <= 0:
        return float("nan")
    g2 = m4 / (m2 ** 2) - 3.0
    return (n - 1) / ((n - 2) * (n - 3)) * ((n + 1) * g2 + 6.0)


def describe(x) -> dict:
    x = [float(v) for v in x]
    n = len(x)
    if n == 0:
        return {"n": 0}
    return {
        "n": n,
        "mean": mean(x),
        "median": median(x),
        "sd": stdev(x),
        "min": min(x),
        "max": max(x),
        "q1": quantile(x, 0.25),
        "q3": quantile(x, 0.75),
        "iqr": iqr(x),
        "skewness": skewness(x),
        "kurtosis": kurtosis(x),
    }


def _ranks(values):
    """Average ranks (1-based) for a sequence, ties averaged."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    n = len(order)
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


# --------------------------------------------------------------------------
# Normality and homoscedasticity
# --------------------------------------------------------------------------
def shapiro_wilk(x):
    """Shapiro-Wilk W and approximate p (Royston AS R94). Returns (W, p)."""
    x = sorted(float(v) for v in x)
    n = len(x)
    if n < 3:
        return (float("nan"), float("nan"))
    if n > 5000:
        return (float("nan"), float("nan"))  # approximation out of range

    m = [norm_ppf((i + 1 - 0.375) / (n + 0.25)) for i in range(n)]
    mm = sum(v * v for v in m)
    u = 1.0 / sqrt(n)

    if n == 3:
        a = [-sqrt(0.5), 0.0, sqrt(0.5)]
    else:
        c1 = (0.0, 0.221157, -0.147981, -2.071190, 4.434685, -2.706056)
        c2 = (0.0, 0.042981, -0.293762, -1.752461, 5.682633, -3.582633)
        p1 = p2 = 0.0
        uk = 1.0
        for k in range(6):
            p1 += c1[k] * uk
            p2 += c2[k] * uk
            uk *= u

        a = m[:]
        an = m[n - 1] / sqrt(mm) + p1
        if n > 5:
            anm1 = m[n - 2] / sqrt(mm) + p2
            phi = (mm - 2.0 * m[n - 1] ** 2 - 2.0 * m[n - 2] ** 2) / \
                  (1.0 - 2.0 * an ** 2 - 2.0 * anm1 ** 2)
            for i in range(2, n - 2):
                a[i] = m[i] / sqrt(phi)
            a[n - 1] = an
            a[n - 2] = anm1
            a[0] = -an
            a[1] = -anm1
        else:
            phi = (mm - 2.0 * m[n - 1] ** 2) / (1.0 - 2.0 * an ** 2)
            for i in range(1, n - 1):
                a[i] = m[i] / sqrt(phi)
            a[n - 1] = an
            a[0] = -an

    xbar = mean(x)
    ssd = sum((v - xbar) ** 2 for v in x)
    if ssd <= 0:
        return (float("nan"), float("nan"))
    ax = sum(ai * xi for ai, xi in zip(a, x))
    W = min(ax * ax / ssd, 1.0)

    nd = float(n)
    if n == 3:
        p = 6.0 / pi * (asin(sqrt(W)) - asin(sqrt(0.75)))
        p = min(max(p, 0.0), 1.0)
        return (W, p)
    if n <= 11:
        g = -2.273 + 0.459 * nd
        z_in = -log(g - log(1.0 - W))
        mu = 0.5440 - 0.39978 * nd + 0.025054 * nd ** 2 - 0.0006714 * nd ** 3
        sigma = exp(1.3822 - 0.77857 * nd + 0.062767 * nd ** 2 - 0.0020322 * nd ** 3)
    else:
        ln = log(nd)
        z_in = log(1.0 - W)
        mu = -1.5861 - 0.31082 * ln - 0.083751 * ln ** 2 + 0.0038915 * ln ** 3
        sigma = exp(-0.4803 - 0.082676 * ln + 0.0030302 * ln ** 2)
    z = (z_in - mu) / sigma
    return (W, norm_sf(z))


def brown_forsythe(groups) -> dict:
    """Brown-Forsythe equal-variance test (ANOVA on |x - group median|)."""
    k = len(groups)
    if k < 2:
        return {"statistic": None, "p": None, "df1": None, "df2": None,
                "note": "need >= 2 groups"}
    meds = [median(g) for g in groups]
    zs = [[abs(v - md) for v in g] for g, md in zip(groups, meds)]
    N = sum(len(g) for g in groups)
    grand = sum(sum(g) for g in zs) / N if N else float("nan")
    ssb = sum(len(g) * (mean(g) - grand) ** 2 for g in zs)
    ssw = sum(sum((v - mean(g)) ** 2 for v in g) for g in zs)
    df1, df2 = k - 1, N - k
    if ssw <= 0 or df2 <= 0:
        return {"statistic": None, "p": None, "df1": df1, "df2": df2,
                "note": "within-group variance is zero"}
    msb, msw = ssb / df1, ssw / df2
    if msw <= 0:
        return {"statistic": None, "p": None, "df1": df1, "df2": df2,
                "note": "zero within-group variance"}
    F = msb / msw
    return {"statistic": F, "p": f_sf(F, df1, df2), "df1": df1, "df2": df2,
            "note": None}


# --------------------------------------------------------------------------
# Hypothesis tests
# --------------------------------------------------------------------------
def student_t_test(x, y) -> dict:
    n1, n2 = len(x), len(y)
    if n1 < 2 or n2 < 2:
        return {"test": "student_t", "t": None, "df": None, "p": None,
                "note": "need n>=2 per group"}
    m1, m2 = mean(x), mean(y)
    v1, v2 = variance(x), variance(y)
    sp2 = ((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2)
    if sp2 <= 0:
        return {"test": "student_t", "t": None, "df": n1 + n2 - 2, "p": None,
                "note": "pooled variance is zero"}
    se = sqrt(sp2 * (1.0 / n1 + 1.0 / n2))
    t = (m1 - m2) / se
    df = n1 + n2 - 2
    return {"test": "student_t", "t": t, "df": df, "p": t_two_sided_p(abs(t), df),
            "note": None}


def welch_t_test(x, y) -> dict:
    n1, n2 = len(x), len(y)
    if n1 < 2 or n2 < 2:
        return {"test": "welch_t", "t": None, "df": None, "p": None,
                "note": "need n>=2 per group"}
    m1, m2 = mean(x), mean(y)
    v1, v2 = variance(x), variance(y)
    se = sqrt(v1 / n1 + v2 / n2)
    if se <= 0:
        return {"test": "welch_t", "t": None, "df": None, "p": None,
                "note": "zero standard error"}
    t = (m1 - m2) / se
    num = (v1 / n1 + v2 / n2) ** 2
    den = (v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1)
    df = num / den if den > 0 else float("nan")
    return {"test": "welch_t", "t": t, "df": df, "p": t_two_sided_p(abs(t), df),
            "note": None}


def mann_whitney_u(x, y) -> dict:
    """Mann-Whitney U with tie-corrected normal approximation."""
    n1, n2 = len(x), len(y)
    if n1 < 1 or n2 < 1:
        return {"test": "mann_whitney_u", "U": None, "z": None, "p": None,
                "note": "empty group"}
    values = list(x) + list(y)
    r = _ranks(values)
    R1 = sum(r[:n1])
    U1 = R1 - n1 * (n1 + 1) / 2.0
    U2 = n1 * n2 - U1
    U = min(U1, U2)
    n = n1 + n2
    mu = n1 * n2 / 2.0
    # tie correction
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    tie_adj = sum(c ** 3 - c for c in counts.values())
    var_u = n1 * n2 / 12.0 * ((n + 1) - tie_adj / (n * (n - 1))) if n > 1 else 0.0
    if var_u <= 0:
        return {"test": "mann_whitney_u", "U": U, "z": None, "p": None,
                "note": "zero variance (all ties)"}
    z = (U - mu) / sqrt(var_u)
    p = 2.0 * norm_cdf(-abs(z))
    return {"test": "mann_whitney_u", "U": U, "z": z, "p": p,
            "note": "normal approximation with tie correction"}


def kruskal_wallis(groups) -> dict:
    k = len(groups)
    N = sum(len(g) for g in groups)
    if k < 2 or N < 3:
        return {"test": "kruskal_wallis", "H": None, "df": None, "p": None,
                "note": "need >= 2 groups, >= 3 obs"}
    values = [v for g in groups for v in g]
    r = _ranks(values)
    idx = 0
    Ri = []
    for g in groups:
        Ri.append(sum(r[idx:idx + len(g)]))
        idx += len(g)
    H = 12.0 / (N * (N + 1)) * sum(ri ** 2 / ni for ri, ni in zip(Ri, (len(g) for g in groups))) \
        - 3.0 * (N + 1)
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    tie_adj = sum(c ** 3 - c for c in counts.values())
    if tie_adj > 0:
        H /= 1.0 - tie_adj / (N ** 3 - N)
    df = k - 1
    return {"test": "kruskal_wallis", "H": H, "df": df, "p": chi2_sf(H, df),
            "note": "tie-corrected"}


def one_way_anova(groups) -> dict:
    k = len(groups)
    N = sum(len(g) for g in groups)
    if k < 2 or N < 3:
        return {"test": "anova", "F": None, "df1": None, "df2": None, "p": None,
                "note": "need >= 2 groups"}
    grand = sum(sum(g) for g in groups) / N
    ssb = sum(len(g) * (mean(g) - grand) ** 2 for g in groups)
    ssw = sum(sum((v - mean(g)) ** 2 for v in g) for g in groups)
    df1, df2 = k - 1, N - k
    if ssw <= 0 or df2 <= 0:
        return {"test": "anova", "F": None, "df1": df1, "df2": df2, "p": None,
                "note": "zero within-group variance"}
    msb, msw = ssb / df1, ssw / df2
    if msw <= 0:
        return {"test": "anova", "F": None, "df1": df1, "df2": df2, "p": None,
                "note": "zero within-group variance"}
    F = msb / msw
    return {"test": "anova", "F": F, "df1": df1, "df2": df2, "p": f_sf(F, df1, df2),
            "ss_between": ssb, "ss_within": ssw, "ms_within": msw, "note": None}


def pearson_r(x, y) -> dict:
    n = len(x)
    if n < 3 or len(y) != n:
        return {"r": None, "p": None, "df": None, "note": "need n>=3 equal length"}
    mx, my = mean(x), mean(y)
    sxx = sum((v - mx) ** 2 for v in x)
    syy = sum((v - my) ** 2 for v in y)
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    den = sqrt(sxx * syy)
    if den <= 0:
        return {"r": None, "p": None, "df": None, "note": "zero variance"}
    r = sxy / den
    r = max(-1.0, min(1.0, r))
    t = r * sqrt((n - 2) / (1 - r * r)) if abs(r) < 1.0 else math.inf
    p = t_two_sided_p(abs(t), n - 2) if math.isfinite(t) else 0.0
    return {"r": r, "t": t, "p": p, "df": n - 2, "note": None}


def spearman_rho(x, y) -> dict:
    n = len(x)
    if n < 3 or len(y) != n:
        return {"rho": None, "p": None, "note": "need n>=3 equal length"}
    rx = _ranks(list(x))
    ry = _ranks(list(y))
    mr1, mr2 = mean(rx), mean(ry)
    num = sum((a - mr1) * (b - mr2) for a, b in zip(rx, ry))
    den = sqrt(sum((a - mr1) ** 2 for a in rx) * sum((b - mr2) ** 2 for b in ry))
    if den <= 0:
        return {"rho": None, "p": None, "note": "zero variance"}
    rho = num / den
    t = rho * sqrt((n - 2) / (1 - rho * rho)) if abs(rho) < 1.0 else math.inf
    p = t_two_sided_p(abs(t), n - 2) if math.isfinite(t) else 0.0
    return {"rho": rho, "t": t, "p": p, "note": None}


def paired_t_test(x, y) -> dict:
    """Paired two-sided t-test on differences (x_i - y_i)."""
    n = len(x)
    if n < 2 or len(y) != n:
        return {"test": "paired_t", "t": None, "df": None, "p": None,
                "note": "need n>=2 equal length pairs"}
    d = [a - b for a, b in zip(x, y)]
    md = mean(d)
    sd = stdev(d)
    if sd <= 0:
        return {"test": "paired_t", "t": None, "df": n - 1, "p": None,
                "note": "zero variance of differences"}
    t = md / (sd / sqrt(n))
    return {"test": "paired_t", "t": t, "df": n - 1, "p": t_two_sided_p(abs(t), n - 1),
            "note": None}


def chi2_contingency(table) -> dict:
    """Chi-square test of independence on a 2-D contingency table."""
    rows = len(table)
    if rows < 1:
        return {"chi2": None, "df": None, "p": None, "note": "empty table"}
    cols = len(table[0])
    if rows < 2 or cols < 2:
        return {"chi2": None, "df": None, "p": None, "note": "need 2x2 or larger"}
    row_sum = [sum(r) for r in table]
    col_sum = [sum(table[i][j] for i in range(rows)) for j in range(cols)]
    n = sum(row_sum)
    chi2 = 0.0
    for i in range(rows):
        for j in range(cols):
            expct = row_sum[i] * col_sum[j] / n if n else 0.0
            if expct > 0:
                chi2 += (table[i][j] - expct) ** 2 / expct
    df = (rows - 1) * (cols - 1)
    return {"chi2": chi2, "df": df, "p": chi2_sf(chi2, df), "note": None}


# --------------------------------------------------------------------------
# Effect sizes
# --------------------------------------------------------------------------
def cohen_d(x, y) -> float:
    n1, n2 = len(x), len(y)
    sp = sqrt(((n1 - 1) * variance(x) + (n2 - 1) * variance(y)) / (n1 + n2 - 2))
    return (mean(x) - mean(y)) / sp if sp > 0 else float("nan")


def hedges_g(x, y) -> float:
    d = cohen_d(x, y)
    n = len(x) + len(y)
    if math.isnan(d):
        return d
    return d * (1.0 - 3.0 / (4.0 * n - 9.0))


def glass_delta(x, y) -> float:
    """(mean_x - mean_y) / sd_y ; y treated as reference/control."""
    sd = stdev(y)
    return (mean(x) - mean(y)) / sd if sd > 0 else float("nan")


def eta_squared(groups) -> float:
    N = sum(len(g) for g in groups)
    grand = sum(sum(g) for g in groups) / N if N else float("nan")
    ssb = sum(len(g) * (mean(g) - grand) ** 2 for g in groups)
    sst = sum(sum((v - grand) ** 2 for v in g) for g in groups)
    return ssb / sst if sst > 0 else float("nan")


def omega_squared(groups) -> float:
    aov = one_way_anova(groups)
    if aov.get("F") is None:
        return float("nan")
    N = sum(len(g) for g in groups)
    k = len(groups)
    ssb = aov["ss_between"]
    ssw = aov["ss_within"]
    msw = aov["ms_within"]
    sst = ssb + ssw
    den = sst + msw
    return (ssb - (k - 1) * msw) / den if den > 0 else float("nan")


def cramers_v(table) -> float:
    res = chi2_contingency(table)
    if res.get("chi2") is None:
        return float("nan")
    n = sum(sum(r) for r in table)
    r = len(table)
    c = len(table[0])
    dmin = min(r - 1, c - 1)
    if n <= 0 or dmin <= 0:
        return float("nan")
    return sqrt(res["chi2"] / (n * dmin))


# --------------------------------------------------------------------------
# Confidence intervals (two-sided 95% default)
# --------------------------------------------------------------------------
def mean_ci(x, conf: float = 0.95) -> tuple:
    n = len(x)
    if n < 2:
        return (float("nan"), float("nan"))
    tc = t_ppf(0.5 + conf / 2.0, n - 1)
    se = stdev(x) / sqrt(n)
    m = mean(x)
    return (m - tc * se, m + tc * se)


def mean_diff_ci_welch(x, y, conf: float = 0.95) -> tuple:
    n1, n2 = len(x), len(y)
    if n1 < 2 or n2 < 2:
        return (float("nan"), float("nan"))
    m1, m2 = mean(x), mean(y)
    v1, v2 = variance(x), variance(y)
    se = sqrt(v1 / n1 + v2 / n2)
    num = (v1 / n1 + v2 / n2) ** 2
    den = (v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1)
    df = num / den if den > 0 else n1 + n2 - 2
    tc = t_ppf(0.5 + conf / 2.0, df)
    diff = m1 - m2
    return (diff - tc * se, diff + tc * se)


def proportion_wilson(k: int, n: int, conf: float = 0.95) -> tuple:
    if n <= 0:
        return (float("nan"), float("nan"))
    z = norm_ppf(0.5 + conf / 2.0)
    phat = k / n
    den = 1.0 + z * z / n
    center = (phat + z * z / (2.0 * n)) / den
    half = z * sqrt(phat * (1 - phat) / n + z * z / (4.0 * n * n)) / den
    return (max(0.0, center - half), min(1.0, center + half))


def corr_fisher_z_ci(r: float, n: int, conf: float = 0.95) -> tuple:
    if n <= 3 or abs(r) >= 1.0:
        return (float("nan"), float("nan"))
    z = math.atanh(r)
    zc = norm_ppf(0.5 + conf / 2.0) / sqrt(n - 3)
    return (math.tanh(z - zc), math.tanh(z + zc))


# --------------------------------------------------------------------------
# Inline self-tests
# --------------------------------------------------------------------------
def _close(a, b, tol=1e-6):
    return abs(a - b) <= tol


def _selftest() -> int:
    ok = True

    def check(name, cond):
        nonlocal ok
        if not cond:
            ok = False
            print("FAIL", name)

    check("norm_cdf(0)", _close(norm_cdf(0.0), 0.5))
    check("norm_ppf(0.975)", _close(norm_ppf(0.975), 1.959963984540054, 1e-6))
    check("norm_ppf(0.5)", _close(norm_ppf(0.5), 0.0, 1e-9))
    # t two-sided p for t=2.0, df=10 ~ 0.073388
    check("t_two_sided_p(2,10)", _close(t_two_sided_p(2.0, 10), 0.073388, 1e-4))
    check("t_cdf(0,10)", _close(t_cdf(0.0, 10), 0.5, 1e-9))
    # chi2 sf(3.841459, 1) ~ 0.05
    check("chi2_sf(3.841459,1)", _close(chi2_sf(3.841459, 1), 0.05, 1e-3))
    # f sf(4.0, 1, 10) known ~ 0.0730
    check("f_sf(4.0,1,10)", _close(f_sf(4.0, 1, 10), 0.07302, 1e-3))

    d = [1.0, 2.0, 3.0, 4.0, 5.0]
    check("mean", _close(mean(d), 3.0))
    check("median", _close(median(d), 3.0))
    check("variance(ddof=1)", _close(variance(d), 2.5))
    check("quantile p=.5", _close(quantile(d, 0.5), 3.0))
    check("quantile p=.25", _close(quantile(d, 0.25), 2.0))
    check("iqr", _close(iqr(d), 2.0))

    # Shapiro-Wilk: near-normal sample should not reject strongly.
    import random
    rnd = random.Random(7)
    samp = [rnd.gauss(0, 1) for _ in range(200)]
    W, p = shapiro_wilk(samp)
    check("shapiro W in (0.9,1]", W is not None and 0.9 < W <= 1.0)
    check("shapiro normal p > 0.001", p is not None and p > 0.001)
    # Skewed sample should reject.
    skew = [rnd.expovariate(1) for _ in range(200)]
    _, p2 = shapiro_wilk(skew)
    check("shapiro skewed p < 0.05", p2 is not None and p2 < 0.05)

    # Mann-Whitney: clearly separated groups.
    a = [rnd.gauss(0, 1) for _ in range(60)]
    b = [rnd.gauss(2, 1) for _ in range(60)]
    mw = mann_whitney_u(a, b)
    check("MW separated p<0.001", mw["p"] is not None and mw["p"] < 0.001)

    w = welch_t_test(a, b)
    check("welch separated p<0.001", w["p"] is not None and w["p"] < 0.001)

    aov = one_way_anova([a, b])
    check("anova separated p<0.001", aov["p"] is not None and aov["p"] < 0.001)

    print("selftest", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
