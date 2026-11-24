#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""statlib.py — pure-standard-library statistics for analysis-02.

Every public function returns a plain dict and every numeric result is a JSON-safe
float or ``None`` when undefined.  There are no third-party dependencies.

Conventions recorded in each result (the ``method`` field) so the report can state
the exact convention:

* special functions (regularised beta / gamma) are implemented from Numerical
  Recipes, which makes Student-t, F and chi-square p-values accurate to ~1e-12;
* normality uses the D'Agostino-Pearson K^2 omnibus test (skewness + kurtosis,
  chi-square with 2 df) — a recognised, deterministic normality test;
* Mann-Whitney U uses the normal approximation with continuity and tie correction;
* Kruskal-Wallis uses the chi-square approximation with tie correction;
* Levene / Brown-Forsythe use an ANOVA F test on absolute deviations from the
  mean / median respectively;
* quantiles use the type-7 (linear interpolation) convention, matching numpy/scipy.
"""

from __future__ import annotations

import math

EPS = 3.0e-14
FPMIN = 1.0e-300
MAXIT = 300
SQRT2 = math.sqrt(2.0)


# --------------------------------------------------------------------------
# Special functions
# --------------------------------------------------------------------------

def normal_cdf(x: float) -> float:
    """Standard normal cumulative distribution function."""
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def normal_ppf(p: float) -> float:
    """Inverse normal CDF (Acklam's rational approximation)."""
    if p <= 0.0 or p >= 1.0:
        raise ValueError("p must be in (0,1)")
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    plow = 0.02425
    phigh = 1.0 - plow
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    elif p <= phigh:
        q = p - 0.5
        r = q * q
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    # one Newton refinement for extra safety
    e = normal_cdf(x) - p
    x -= e / (math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi))
    return x


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (NR 6.4)."""
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
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < EPS:
            break
    return h


def betai(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
          + a * math.log(x) + b * math.log(1.0 - x))
    bt = math.exp(ln)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _gser(a: float, x: float) -> float:
    ap = a
    sm = 1.0 / a
    d = sm
    for _ in range(1, MAXIT + 1):
        ap += 1.0
        d *= x / ap
        sm += d
        if abs(d) < abs(sm) * EPS:
            break
    return sm * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gcf(a: float, x: float) -> float:
    b = x + 1.0 - a
    c = 1.0 / FPMIN
    d = 1.0 / b
    h = d
    for i in range(1, MAXIT + 1):
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
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def gammp(a: float, x: float) -> float:
    """Regularised lower incomplete gamma P(a, x)."""
    if x < 0.0 or a <= 0.0:
        raise ValueError("invalid arguments")
    if x == 0.0:
        return 0.0
    if x < a + 1.0:
        return _gser(a, x)
    return 1.0 - _gcf(a, x)


def gammq(a: float, x: float) -> float:
    """Regularised upper incomplete gamma Q(a, x)."""
    return 1.0 - gammp(a, x)


def t_cdf(t: float, df: float) -> float:
    """Student-t CDF P(T <= t)."""
    if df <= 0:
        raise ValueError("df must be positive")
    x = df / (df + t * t)
    ib = betai(df / 2.0, 0.5, x)
    if t >= 0.0:
        return 1.0 - 0.5 * ib
    return 0.5 * ib


def t_pvalue_two_sided(t: float, df: float) -> float:
    """Two-sided Student-t p-value for observed statistic t."""
    t = abs(t)
    return betai(df / 2.0, 0.5, df / (df + t * t))


def t_ppf(p: float, df: float) -> float:
    """Inverse Student-t CDF (bisection); p in (0,1)."""
    if p <= 0.0 or p >= 1.0:
        raise ValueError("p must be in (0,1)")
    if p == 0.5:
        return 0.0
    target = p if p > 0.5 else 1.0 - p
    lo, hi = 0.0, 1.0
    while t_cdf(hi, df) < target:
        hi *= 2.0
        if hi > 1e6:
            return math.inf
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_cdf(mid, df) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0 if p > 0.5 else -(lo + hi) / 2.0


def f_pvalue(F: float, df1: float, df2: float) -> float:
    """Upper-tail F p-value."""
    if F <= 0.0:
        return 1.0
    x = df2 / (df2 + df1 * F)
    return betai(df2 / 2.0, df1 / 2.0, x)


def chi2_pvalue(x: float, df: float) -> float:
    """Upper-tail chi-square p-value."""
    if x <= 0.0:
        return 1.0
    return gammq(df / 2.0, x / 2.0)


# --------------------------------------------------------------------------
# Descriptive helpers
# --------------------------------------------------------------------------

def _finite(x) -> bool:
    return x is not None and isinstance(x, (int, float)) and math.isfinite(float(x))


def _clean_sample(values):
    return [float(v) for v in values if _finite(v)]


def quantile_sorted(x, q: float):
    """Type-7 quantile from an ascending-sorted list."""
    n = len(x)
    if n == 0:
        return None
    if n == 1:
        return float(x[0])
    pos = (n - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(x[lo])
    frac = pos - lo
    return float(x[lo]) + frac * (float(x[hi]) - float(x[lo]))


def _moments(x):
    n = len(x)
    mean = sum(x) / n
    m2 = m3 = m4 = 0.0
    for v in x:
        d = v - mean
        m2 += d * d
        m3 += d * d * d
        m4 += d * d * d * d
    m2 /= n
    m3 /= n
    m4 /= n
    return n, mean, m2, m3, m4


def _ranks(x):
    """Average ranks with tie handling."""
    n = len(x)
    order = sorted(range(n), key=lambda i: x[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and x[order[j + 1]] == x[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def describe(values, unit: str = "unit_unknown"):
    """Descriptive statistics for one numeric sample.

    Returns n, missing, mean, median, sd (sample), min, max, q1, q3, iqr,
    skewness, kurtosis (excess), ci95_mean (t-based), unit, method.
    """
    raw = list(values)
    missing = sum(0 if _finite(v) else 1 for v in raw)
    x = _clean_sample(raw)
    n = len(x)
    if n == 0:
        return {"n": 0, "missing": missing, "mean": None, "median": None, "sd": None,
                "min": None, "max": None, "q1": None, "q3": None, "iqr": None,
                "skewness": None, "kurtosis": None, "ci95_mean": None,
                "unit": unit, "method": "sample moments, type-7 quantiles"}

    sx = sorted(x)
    mean = sum(x) / n
    variance = sum((v - mean) ** 2 for v in x) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(variance)
    q1 = quantile_sorted(sx, 0.25)
    q3 = quantile_sorted(sx, 0.75)
    iqr = q3 - q1

    _, mu, m2, m3, m4 = _moments(x)
    if n >= 3 and m2 > 0:
        g1 = m3 / (m2 ** 1.5)
        skew = math.sqrt(n * (n - 1)) / (n - 2) * g1
    else:
        skew = None
    if n >= 4 and m2 > 0:
        g2 = m4 / (m2 * m2) - 3.0
        kurt = ((n + 1) * g2 + 6.0) * (n - 1) / ((n - 2) * (n - 3))
    else:
        kurt = None

    if n >= 2 and sd > 0:
        tcrit = t_ppf(0.975, n - 1)
        half = tcrit * sd / math.sqrt(n)
        ci95_mean = [mean - half, mean + half]
    else:
        ci95_mean = None

    return {"n": n, "missing": missing, "mean": mean, "median": quantile_sorted(sx, 0.5),
            "sd": sd, "min": sx[0], "max": sx[-1], "q1": q1, "q3": q3, "iqr": iqr,
            "skewness": skew, "kurtosis": kurt, "ci95_mean": ci95_mean,
            "unit": unit, "method": "sample moments, type-7 quantiles, t 95% mean CI"}


def iqr_outliers(values):
    """Outlier flags by Tukey fences [Q1-1.5IQR, Q3+1.5IQR]."""
    x = _clean_sample(values)
    sx = sorted(x)
    q1 = quantile_sorted(sx, 0.25)
    q3 = quantile_sorted(sx, 0.75)
    iqr = q3 - q1
    lo = q1 - 1.5 * iqr
    hi = q3 + 1.5 * iqr
    flags = [v < lo or v > hi for v in x]
    return {"n": len(x), "n_outlier": sum(flags), "lower_fence": lo,
            "upper_fence": hi, "rule": "Tukey IQR 1.5", "indexes": [i for i, f in enumerate(flags) if f]}


# --------------------------------------------------------------------------
# Hypothesis tests
# --------------------------------------------------------------------------

def _t_two(x, y, welch: bool):
    nx, ny = len(x), len(y)
    if nx < 2 or ny < 2:
        return {"applicable": False, "reason": "n<2 in at least one group"}
    mx, my = sum(x) / nx, sum(y) / ny
    vx = sum((v - mx) ** 2 for v in x) / (nx - 1)
    vy = sum((v - my) ** 2 for v in y) / (ny - 1)
    diff = mx - my
    if welch:
        se = math.sqrt(vx / nx + vy / ny)
        df = (vx / nx + vy / ny) ** 2 / ((vx / nx) ** 2 / (nx - 1) + (vy / ny) ** 2 / (ny - 1))
        method = "Welch's t-test (unequal variances)"
    else:
        sp = math.sqrt(((nx - 1) * vx + (ny - 1) * vy) / (nx + ny - 2))
        se = sp * math.sqrt(1.0 / nx + 1.0 / ny)
        df = nx + ny - 2
        method = "Student's t-test (pooled variance)"
    if se == 0.0:
        return {"applicable": False, "reason": "zero standard error", "method": method}
    t = diff / se
    p = t_pvalue_two_sided(t, df)
    return {"applicable": True, "statistic": t, "df": df, "p": p, "method": method,
            "mean_diff": diff, "se": se, "n1": nx, "n2": ny,
            "mean1": mx, "mean2": my, "var1": vx, "var2": vy}


def student_t_test(x, y):
    return _t_two(x, y, welch=False)


def welch_t_test(x, y):
    return _t_two(x, y, welch=True)


def mann_whitney_u(x, y):
    """Mann-Whitney U via normal approximation with continuity + tie correction."""
    nx, ny = len(x), len(y)
    n = nx + ny
    if nx == 0 or ny == 0:
        return {"applicable": False, "reason": "empty group"}
    allv = list(x) + list(y)
    ranks = _ranks(allv)
    rx = sum(ranks[:nx])
    u1 = rx - nx * (nx + 1) / 2.0
    u2 = nx * ny - u1
    U = min(u1, u2)
    # tie correction
    seen = {}
    for v in allv:
        seen[v] = seen.get(v, 0) + 1
    tie_sum = sum(t ** 3 - t for t in seen.values())
    sigma2 = nx * ny / 12.0 * ((n + 1) - tie_sum / (n * (n - 1))) if n > 1 else 0.0
    if sigma2 <= 0:
        z = 0.0
    else:
        z = (abs(U - nx * ny / 2.0) - 0.5) / math.sqrt(sigma2)
    p = 2.0 * (1.0 - normal_cdf(z))
    return {"applicable": True, "statistic": U, "p": p, "z": z,
            "n1": nx, "n2": ny, "method": "Mann-Whitney U, normal approximation (continuity + ties)",
            "tie_groups": len(seen)}


def _anova_components(groups):
    k = len(groups)
    ns = [len(g) for g in groups]
    N = sum(ns)
    if k < 2 or any(n == 0 for n in ns):
        return None
    means = [sum(g) / len(g) for g in groups]
    grand = sum(sum(g) for g in groups) / N
    ss_between = sum(ns[i] * (means[i] - grand) ** 2 for i in range(k))
    ss_within = 0.0
    for i, g in enumerate(groups):
        ss_within += sum((v - means[i]) ** 2 for v in g)
    ss_total = ss_between + ss_within
    return k, ns, N, means, grand, ss_between, ss_within, ss_total


def anova_f(groups):
    comp = _anova_components(groups)
    if comp is None:
        return {"applicable": False, "reason": "need >=2 non-empty groups"}
    k, ns, N, means, grand, ss_b, ss_w, ss_t = comp
    df_b = k - 1
    df_w = N - k
    if df_w <= 0 or ss_w == 0.0:
        return {"applicable": False, "reason": "zero within-group variance / df",
                "k": k, "n": N}
    ms_b = ss_b / df_b
    ms_w = ss_w / df_w
    F = ms_b / ms_w
    p = f_pvalue(F, df_b, df_w)
    eta2 = ss_b / ss_t if ss_t > 0 else None
    omega2 = (ss_b - df_b * ms_w) / (ss_t + ms_w) if (ss_t + ms_w) > 0 else None
    return {"applicable": True, "statistic": F, "df_between": df_b, "df_within": df_w,
            "p": p, "method": "one-way ANOVA F", "k": k, "n": N,
            "ss_between": ss_b, "ss_within": ss_w, "ss_total": ss_t,
            "eta_squared": eta2, "omega_squared": omega2}


def kruskal_wallis(groups):
    comp = _anova_components(groups)
    if comp is None:
        return {"applicable": False, "reason": "need >=2 non-empty groups"}
    k, ns, N, means, grand, ss_b, ss_w, ss_t = comp
    allv = [v for g in groups for v in g]
    ranks = _ranks(allv)
    idx = 0
    rank_sums = []
    for g in groups:
        rank_sums.append(sum(ranks[idx:idx + len(g)]))
        idx += len(g)
    H = 12.0 / (N * (N + 1)) * sum(rs ** 2 / ns[i] for i, rs in enumerate(rank_sums)) - 3.0 * (N + 1)
    seen = {}
    for v in allv:
        seen[v] = seen.get(v, 0) + 1
    tie_sum = sum(t ** 3 - t for t in seen.values())
    correction = 1.0 - tie_sum / (N ** 3 - N) if N > 1 else 1.0
    if correction > 0:
        H /= correction
    p = chi2_pvalue(H, k - 1)
    return {"applicable": True, "statistic": H, "df": k - 1, "p": p,
            "method": "Kruskal-Wallis H, chi-square approximation (tie corrected)",
            "k": k, "n": N, "rank_sums": rank_sums}


def levene_test(groups, center="median"):
    """Levene (mean) / Brown-Forsythe (median) via ANOVA F on absolute deviations."""
    centers = []
    for g in groups:
        if not g:
            return {"applicable": False, "reason": "empty group"}
        if center == "median":
            s = sorted(g)
            centers.append(quantile_sorted(s, 0.5))
        else:
            centers.append(sum(g) / len(g))
    devs = [[abs(v - centers[i]) for v in g] for i, g in enumerate(groups)]
    res = anova_f(devs)
    res["method"] = ("Brown-Forsythe (median-based Levene)" if center == "median"
                     else "Levene (mean-based)")
    return res


def pearson_r(x, y):
    nx, ny = len(x), len(y)
    if nx != ny or nx < 3:
        return {"applicable": False, "reason": "need equal n>=3"}
    mx, my = sum(x) / nx, sum(y) / ny
    sxx = sum((v - mx) ** 2 for v in x)
    syy = sum((v - my) ** 2 for v in y)
    sxy = sum((x[i] - mx) * (y[i] - my) for i in range(nx))
    if sxx <= 0 or syy <= 0:
        return {"applicable": False, "reason": "zero variance"}
    r = sxy / math.sqrt(sxx * syy)
    r = max(-1.0, min(1.0, r))
    if abs(r) >= 1.0:
        p = 0.0
    else:
        t = r * math.sqrt((nx - 2) / (1.0 - r * r))
        p = t_pvalue_two_sided(t, nx - 2)
    z = math.atanh(r) if abs(r) < 1.0 else (math.inf if r > 0 else -math.inf)
    se = 1.0 / math.sqrt(nx - 3)
    zlo, zhi = z - 1.96 * se, z + 1.96 * se
    ci = [math.tanh(zlo), math.tanh(zhi)]
    return {"applicable": True, "statistic": r, "n": nx, "p": p,
            "method": "Pearson r, t test, Fisher z 95% CI", "ci95": ci, "r2": r * r}


def spearman_rho(x, y):
    nx, ny = len(x), len(y)
    if nx != ny or nx < 3:
        return {"applicable": False, "reason": "need equal n>=3"}
    rx = _ranks(x)
    ry = _ranks(y)
    res = pearson_r(rx, ry)
    res["method"] = "Spearman rho (Pearson on ranks)"
    return res


def simple_linear_regression(x, y):
    n = len(x)
    if n < 3:
        return {"applicable": False, "reason": "need n>=3"}
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((v - mx) ** 2 for v in x)
    sxy = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    syy = sum((v - my) ** 2 for v in y)
    if sxx <= 0:
        return {"applicable": False, "reason": "zero variance in predictor"}
    slope = sxy / sxx
    intercept = my - slope * mx
    resid = [y[i] - (intercept + slope * x[i]) for i in range(n)]
    sse = sum(r * r for r in resid)
    mse = sse / (n - 2)
    se_slope = math.sqrt(mse / sxx)
    t = slope / se_slope if se_slope > 0 else math.inf
    p = t_pvalue_two_sided(t, n - 2)
    r2 = 1.0 - sse / syy if syy > 0 else None
    r = slope * math.sqrt(sxx / syy) if syy > 0 else None
    tcrit = t_ppf(0.975, n - 2)
    ci_slope = [slope - tcrit * se_slope, slope + tcrit * se_slope]
    return {"applicable": True, "slope": slope, "intercept": intercept, "r": r,
            "r_squared": r2, "se_slope": se_slope, "t": t, "df": n - 2, "p": p,
            "ci95_slope": ci_slope, "n": n,
            "method": "OLS simple linear regression, t-based slope CI"}


def chi2_independence(contingency):
    """Pearson chi-square for a 2D contingency table of counts."""
    rows = len(contingency)
    cols = len(contingency[0]) if rows else 0
    if rows < 2 or cols < 2:
        return {"applicable": False, "reason": "need 2x2 or larger table"}
    row_tot = [sum(r) for r in contingency]
    col_tot = [sum(contingency[i][j] for i in range(rows)) for j in range(cols)]
    n = sum(row_tot)
    if n == 0:
        return {"applicable": False, "reason": "empty table"}
    stat = 0.0
    for i in range(rows):
        for j in range(cols):
            exp = row_tot[i] * col_tot[j] / n
            if exp > 0:
                stat += (contingency[i][j] - exp) ** 2 / exp
    df = (rows - 1) * (cols - 1)
    p = chi2_pvalue(stat, df)
    cramer = math.sqrt(stat / (n * min(rows - 1, cols - 1))) if min(rows - 1, cols - 1) > 0 else None
    return {"applicable": True, "statistic": stat, "df": df, "p": p, "n": n,
            "method": "Pearson chi-square independence", "cramers_v": cramer}


# --------------------------------------------------------------------------
# Effect sizes
# --------------------------------------------------------------------------

def cohens_d(x, y):
    nx, ny = len(x), len(y)
    if nx < 2 or ny < 2:
        return {"applicable": False, "reason": "n<2"}
    mx, my = sum(x) / nx, sum(y) / ny
    vx = sum((v - mx) ** 2 for v in x) / (nx - 1)
    vy = sum((v - my) ** 2 for v in y) / (ny - 1)
    sp = math.sqrt(((nx - 1) * vx + (ny - 1) * vy) / (nx + ny - 2))
    if sp == 0.0:
        return {"applicable": False, "reason": "zero pooled sd"}
    d = (mx - my) / sp
    se = math.sqrt((nx + ny) / (nx * ny) + d * d / (2 * (nx + ny)))
    ci = [d - 1.96 * se, d + 1.96 * se]
    return {"applicable": True, "statistic": d, "ci95": ci,
            "method": "Cohen's d (pooled sd), normal-approx 95% CI",
            "direction": "group1 - group2", "mean1": mx, "mean2": my}


def hedges_g(x, y):
    res = cohens_d(x, y)
    if not res["applicable"]:
        return res
    n = res.get("n1", 0) + res.get("n2", 0)
    d = res["statistic"]
    corr = 1.0 - 3.0 / (4 * n - 9) if n > 2 else 1.0
    g = d * corr
    ci = [v * corr for v in res["ci95"]]
    res.update({"statistic": g, "ci95": ci, "d_uncorrected": d,
                "method": "Hedges' g (bias-corrected Cohen's d)"})
    return res


def glass_delta(x, y, control="first"):
    """Glass' Delta using the control group's sd."""
    nx, ny = len(x), len(y)
    if nx < 2 or ny < 2:
        return {"applicable": False, "reason": "n<2"}
    mx, my = sum(x) / nx, sum(y) / ny
    if control == "first":
        sd = math.sqrt(sum((v - mx) ** 2 for v in x) / (nx - 1))
    else:
        sd = math.sqrt(sum((v - my) ** 2 for v in y) / (ny - 1))
    if sd == 0.0:
        return {"applicable": False, "reason": "zero control sd"}
    return {"applicable": True, "statistic": (mx - my) / sd,
            "method": "Glass' Delta (control sd)", "control": control,
            "mean1": mx, "mean2": my}


# --------------------------------------------------------------------------
# Confidence intervals
# --------------------------------------------------------------------------

def mean_ci(x, alpha=0.05):
    x = _clean_sample(x)
    n = len(x)
    if n < 2:
        return None
    m = sum(x) / n
    sd = math.sqrt(sum((v - m) ** 2 for v in x) / (n - 1))
    if sd == 0.0:
        return [m, m]
    tcrit = t_ppf(1.0 - alpha / 2.0, n - 1)
    half = tcrit * sd / math.sqrt(n)
    return [m - half, m + half]


def welch_mean_diff_ci(x, y, alpha=0.05):
    nx, ny = len(x), len(y)
    if nx < 2 or ny < 2:
        return None
    mx, my = sum(x) / nx, sum(y) / ny
    vx = sum((v - mx) ** 2 for v in x) / (nx - 1)
    vy = sum((v - my) ** 2 for v in y) / (ny - 1)
    se = math.sqrt(vx / nx + vy / ny)
    df = (vx / nx + vy / ny) ** 2 / ((vx / nx) ** 2 / (nx - 1) + (vy / ny) ** 2 / (ny - 1))
    tcrit = t_ppf(1.0 - alpha / 2.0, df)
    d = mx - my
    return [d - tcrit * se, d + tcrit * se]


def wilson_proportion_ci(k, n, alpha=0.05):
    if n <= 0:
        return None
    z = normal_ppf(1.0 - alpha / 2.0)
    phat = k / n
    denom = 1.0 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return [max(0.0, center - half), min(1.0, center + half)]


def fisher_z_ci(r, n, alpha=0.05):
    if n < 4 or abs(r) >= 1.0:
        return None
    z = math.atanh(r)
    zc = normal_ppf(1.0 - alpha / 2.0)
    se = 1.0 / math.sqrt(n - 3)
    return [math.tanh(z - zc * se), math.tanh(z + zc * se)]


# --------------------------------------------------------------------------
# Normality (D'Agostino-Pearson K^2 omnibus)
# --------------------------------------------------------------------------

def _d_agostino_skew(x):
    n, mean, m2, m3, _ = _moments(x)
    if m2 <= 0:
        return None
    g1 = m3 / (m2 ** 1.5)
    if n < 8:
        return None
    y = g1 * math.sqrt((n + 1) * (n + 3) / (6.0 * (n - 2)))
    beta2 = (3.0 * (n * n + 27 * n - 70) * (n + 1) * (n + 3)) / \
            ((n - 2.0) * (n + 5) * (n + 7) * (n + 9))
    w2 = -1.0 + math.sqrt(2.0 * (beta2 - 1.0))
    delta = 1.0 / math.sqrt(math.log(math.sqrt(w2)))
    alpha = math.sqrt(2.0 / (w2 - 1.0))
    if y == 0:
        y = 1.0
    return delta * math.log(y / alpha + math.sqrt((y / alpha) ** 2 + 1.0))


def _anscombe_glynn_kurt(x):
    n, mean, m2, m3, m4 = _moments(x)
    if m2 <= 0:
        return None
    b2 = m4 / (m2 * m2)
    if n < 20:
        return None
    E = 3.0 * (n - 1) / (n + 1)
    varb2 = 24.0 * n * (n - 2) * (n - 3) / ((n + 1) ** 2 * (n + 3) * (n + 5))
    if varb2 <= 0:
        return None
    xstat = (b2 - E) / math.sqrt(varb2)
    sqrtbeta1 = (6.0 * (n * n - 5 * n + 2) / ((n + 7) * (n + 9))) * \
        math.sqrt(6.0 * (n + 3) * (n + 5) / (n * (n - 2) * (n - 3)))
    A = 6.0 + 8.0 / sqrtbeta1 * (2.0 / sqrtbeta1 + math.sqrt(1.0 + 4.0 / (sqrtbeta1 * sqrtbeta1)))
    term1 = 1.0 - 2.0 / (9.0 * A)
    term2 = 1.0 - 2.0 / A
    denom = 1.0 + xstat * math.sqrt(2.0 / (A - 4.0))
    term3 = math.copysign(abs(term2 / denom) ** (1.0 / 3.0), term2 / denom)
    return (term1 - term3) / math.sqrt(2.0 / (9.0 * A))


def normality_test(values):
    """D'Agostino-Pearson K^2 omnibus normality test."""
    x = _clean_sample(values)
    n = len(x)
    if n < 20:
        return {"applicable": False, "reason": "n<20: K^2 needs >=20; use Shapiro-Wilk via scipy for small n",
                "n": n}
    z1 = _d_agostino_skew(x)
    z2 = _anscombe_glynn_kurt(x)
    if z1 is None or z2 is None:
        return {"applicable": False, "reason": "undefined moment transform", "n": n}
    k2 = z1 * z1 + z2 * z2
    p = chi2_pvalue(k2, 2.0)
    return {"applicable": True, "statistic": k2, "df": 2, "p": p,
            "z_skew": z1, "z_kurt": z2,
            "method": "D'Agostino-Pearson K^2 omnibus (chi-square, df=2)",
            "normal_at_0.05": p >= 0.05, "n": n}


# --------------------------------------------------------------------------
# Multiple-comparison correction
# --------------------------------------------------------------------------

def bonferroni(pvalues):
    """Return adjusted p-values (cap at 1.0) plus the multiplier."""
    m = len(pvalues)
    return [min(1.0, p * m) for p in pvalues], m


def benjamini_hochberg(pvalues):
    """BH step-up FDR adjustment."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    adj = [1.0] * m
    prev = 1.0
    for rank in range(m - 1, -1, -1):
        i = order[rank]
        val = min(prev, pvalues[i] * m / (rank + 1))
        adj[i] = min(1.0, val)
        prev = adj[i]
    return adj, m


# --------------------------------------------------------------------------
# Small utilities
# --------------------------------------------------------------------------

def jsonable(obj):
    """Convert non-finite floats to None recursively (JSON-safe)."""
    if isinstance(obj, dict):
        return {k: jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [jsonable(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def round_float(x, ndigits=6):
    if x is None or not _finite(x):
        return x
    return round(float(x), ndigits)
