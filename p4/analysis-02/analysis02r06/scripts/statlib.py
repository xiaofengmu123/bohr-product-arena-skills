# -*- coding: utf-8 -*-
"""statlib.py - deterministic, standard-library-only statistical engine.

Everything here uses only the Python standard library (``math`` only). No
NumPy/SciPy dependency at runtime, so the skill runs on a bare interpreter.
Numerical routines follow published algorithms:

  * normal CDF/inverse CDF  -> Abramowitz & Stegun 26.2.17 / Acklam (2003)
  * Student-t / F / chi2    -> regularised incomplete beta/gamma (NR 6.2, 6.4)
  * Shapiro-Wilk           -> Royston (1995), AS R94 (same as R's shapiro.test)
  * Brown-Forsythe          -> Levene test centered on the median (BF 1974)

All functions are pure and deterministic: same input -> same output. Run the
built-in self test with ``python statlib.py`` (exit code 0 on success).
"""

from __future__ import annotations

import math

EPS = 3.0e-12
FPMIN = 1.0e-300


# ---------------------------------------------------------------------------
# Basic continuous distributions
# ---------------------------------------------------------------------------

def norm_cdf(x: float) -> float:
    """Standard normal CDF Phi(x)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_sf(x: float) -> float:
    """Standard normal survival function 1 - Phi(x)."""
    return 0.5 * math.erfc(x / math.sqrt(2.0))


def ndtri(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation).

    Relative precision ~1.15e-9, sufficient for two-sided p-values down to
    1e-9. Raises ValueError outside (0, 1).
    """
    if p <= 0.0 or p >= 1.0:
        raise ValueError("p must be in (0, 1)")

    # Acklam coefficients
    a = (-3.969683028665376e+01, 2.209460984245205e+02,
         -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02,
         -1.556989798598866e+02, 6.680131188771972e+01,
         -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01,
         2.445134137142996e+00, 3.754408661907416e+00)

    plow, phigh = 0.02425, 0.97575
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        num = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
        den = ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
        return num / den
    if p <= phigh:
        q = p - 0.5
        r = q * q
        num = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
        den = (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
        return num / den
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    num = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
    den = ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    return -num / den


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for incomplete beta (Numerical Recipes 6.4)."""
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, 300):
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
    if x < 0.0 or x > 1.0:
        raise ValueError("x must be in [0, 1]")
    if x == 0.0 or x == 1.0:
        bt = 0.0
    else:
        bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                      + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: float) -> float:
    """CDF of Student's t with df degrees of freedom."""
    if df <= 0:
        raise ValueError("df must be > 0")
    x = df / (df + t * t)
    ib = betai(df / 2.0, 0.5, x)
    if t >= 0:
        return 1.0 - 0.5 * ib
    return 0.5 * ib


def t_inv(p: float, df: float) -> float:
    """Inverse Student-t CDF: returns t with P(T <= t) = p."""
    if p <= 0.0 or p >= 1.0:
        raise ValueError("p must be in (0, 1)")
    lo, hi = -1.0e3, 1.0e3
    for _ in range(300):
        mid = 0.5 * (lo + hi)
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def f_cdf(f: float, d1: float, d2: float) -> float:
    """CDF of the F distribution with d1, d2 degrees of freedom."""
    if f < 0:
        return 0.0
    if f == 0:
        return 0.0
    x = d1 * f / (d1 * f + d2)
    return betai(d1 / 2.0, d2 / 2.0, x)


def _gser(a: float, x: float) -> float:
    """Series for lower regularised gamma P(a, x)."""
    if x <= 0.0:
        return 0.0
    gln = math.lgamma(a)
    ap = a
    s = 1.0 / a
    delta = s
    for _ in range(300):
        ap += 1.0
        delta *= x / ap
        s += delta
        if abs(delta) < abs(s) * EPS:
            break
    return s * math.exp(-x + a * math.log(x) - gln)


def _gcf(a: float, x: float) -> float:
    """Continued fraction for upper regularised gamma Q(a, x)."""
    b = x + 1.0 - a
    c = 1.0 / FPMIN
    d = 1.0 / b
    h = d
    for i in range(1, 300):
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
    """Lower regularised gamma P(a, x)."""
    if x < 0.0 or a <= 0.0:
        raise ValueError("require x >= 0 and a > 0")
    if x == 0.0:
        return 0.0
    if x < a + 1.0:
        return _gser(a, x)
    return 1.0 - _gcf(a, x)


def chi2_cdf(x: float, df: float) -> float:
    """CDF of chi-square with df degrees of freedom."""
    if x < 0:
        return 0.0
    return gammp(df / 2.0, x / 2.0)


# ---------------------------------------------------------------------------
# Descriptive statistics
# ---------------------------------------------------------------------------

def _quantile_sorted(xs: list, q: float) -> float:
    """Linear-interpolation quantile of a sorted list (type 7, as R/numpy)."""
    n = len(xs)
    if n == 0:
        return float("nan")
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def descriptive(xs) -> dict:
    """Descriptive statistics for a numeric sequence (ignoring non-finite).

    Keys: n, mean, median, sd (sample), min, max, q1, q3, iqr, skew, kurtosis.
    """
    vals = [v for v in xs if isinstance(v, (int, float)) and math.isfinite(v)]
    n = len(vals)
    if n == 0:
        return {"n": 0}
    s = sorted(vals)
    mean = sum(s) / n
    median = _quantile_sorted(s, 0.5)
    var = sum((v - mean) ** 2 for v in s) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var)
    q1 = _quantile_sorted(s, 0.25)
    q3 = _quantile_sorted(s, 0.75)
    iqr = q3 - q1
    # sample skewness (bias-corrected) and excess kurtosis (Fisher)
    if n >= 3 and sd > 0:
        z = [(v - mean) / sd for v in s]
        m3 = sum(t ** 3 for t in z)
        skew = (n / ((n - 1) * (n - 2))) * m3
    else:
        skew = float("nan")
    if n >= 4 and sd > 0:
        z = [(v - mean) / sd for v in s]
        m4 = sum(t ** 4 for t in z)
        kurt = ((n * (n + 1)) / ((n - 1) * (n - 2) * (n - 3))) * m4 \
            - 3.0 * (n - 1) ** 2 / ((n - 2) * (n - 3))
    else:
        kurt = float("nan")
    return {
        "n": n, "mean": mean, "median": median, "sd": sd,
        "min": s[0], "max": s[-1], "q1": q1, "q3": q3,
        "iqr": iqr, "skew": skew, "kurtosis": kurt,
    }


def iqr_outlier_bounds(xs, k: float = 1.5):
    """Tukey fences [Q1 - k*IQR, Q3 + k*IQR]."""
    vals = [v for v in xs if isinstance(v, (int, float)) and math.isfinite(v)]
    if len(vals) < 4:
        return None
    s = sorted(vals)
    q1 = _quantile_sorted(s, 0.25)
    q3 = _quantile_sorted(s, 0.75)
    iqr = q3 - q1
    return (q1 - k * iqr, q3 + k * iqr)


# ---------------------------------------------------------------------------
# Normality (Shapiro-Wilk, Royston 1995 / AS R94)
# ---------------------------------------------------------------------------

def _poly(cc, x: float) -> float:
    """Polynomial with cc[0] as the zero-order coefficient."""
    ret = cc[0]
    nord = len(cc)
    if nord > 1:
        p = x * cc[nord - 1]
        for j in range(nord - 2, 0, -1):
            p = (p + cc[j]) * x
        ret += p
    return ret


def _sign(z) -> float:
    return -1.0 if z < 0 else 1.0


def shapiro_wilk(x):
    """Shapiro-Wilk W statistic and p-value (Royston 1995, AS R94).

    Port of the R swilk.c implementation; matches scipy.stats.shapiro. Valid
    for 3 <= n <= 5000. Returns (nan, nan) when undefined (n < 3, zero range).
    """
    x = [v for v in x if isinstance(v, (int, float)) and math.isfinite(v)]
    n = len(x)
    if n < 3:
        return (float("nan"), float("nan"))
    x = sorted(x)

    small = 1e-19
    g = [-2.273, 0.459]
    c1 = [0.0, 0.221157, -0.147981, -2.07119, 4.434685, -2.706056]
    c2 = [0.0, 0.042981, -0.293762, -1.752461, 5.682633, -3.582633]
    c3 = [0.544, -0.39978, 0.025054, -6.714e-4]
    c4 = [1.3822, -0.77857, 0.062767, -0.0020322]
    c5 = [-1.5861, -0.31082, -0.083751, 0.0038915]
    c6 = [-0.4803, -0.082676, 0.0030302]

    nn2 = n // 2
    a = [0.0] * (nn2 + 1)  # 1-based, index 0 unused
    an = float(n)

    if n == 3:
        a[1] = 0.70710678
    else:
        an25 = an + 0.25
        summ2 = 0.0
        for i in range(1, nn2 + 1):
            a[i] = ndtri((i - 0.375) / an25)
            summ2 += a[i] * a[i]
        summ2 *= 2.0
        ssumm2 = math.sqrt(summ2)
        rsn = 1.0 / math.sqrt(an)
        a1 = _poly(c1, rsn) - a[1] / ssumm2
        if n > 5:
            i1 = 3
            a2 = -a[2] / ssumm2 + _poly(c2, rsn)
            fac = math.sqrt((summ2 - 2.0 * a[1] * a[1] - 2.0 * a[2] * a[2])
                            / (1.0 - 2.0 * a1 * a1 - 2.0 * a2 * a2))
            a[2] = a2
        else:
            i1 = 2
            fac = math.sqrt((summ2 - 2.0 * a[1] * a[1])
                            / (1.0 - 2.0 * a1 * a1))
        a[1] = a1
        for i in range(i1, nn2 + 1):
            a[i] /= -fac

    rng = x[n - 1] - x[0]
    if rng < small:
        return (float("nan"), float("nan"))

    xx = x[0] / rng
    sx = xx
    sa = -a[1]
    i, j = 1, n - 1
    while i < n:
        xi = x[i] / rng
        sx += xi
        i += 1
        if i != j:
            sa += _sign(i - j) * a[min(i, j)]
        xx = xi
        j -= 1

    sa /= n
    sx /= n
    ssa = ssx = sax = 0.0
    i, j = 0, n - 1
    while i < n:
        if i != j:
            asa = _sign(i - j) * a[1 + min(i, j)] - sa
        else:
            asa = -sa
        xsx = x[i] / rng - sx
        ssa += asa * asa
        ssx += xsx * xsx
        sax += asa * xsx
        i += 1
        j -= 1

    ssassx = math.sqrt(ssa * ssx)
    w1 = (ssassx - sax) * (ssassx + sax) / (ssa * ssx)
    W = 1.0 - w1

    if n == 3:
        pi6 = 1.90985931710274
        stqr = 1.04719755119660
        pw = pi6 * (math.asin(math.sqrt(W)) - stqr)
        return (W, max(pw, 0.0))

    y = math.log(w1)
    xx = math.log(an)
    if n <= 11:
        gamma = _poly(g, an)
        if y >= gamma:
            return (W, 1e-99)
        y = -math.log(gamma - y)
        m = _poly(c3, an)
        s = math.exp(_poly(c4, an))
    else:
        m = _poly(c5, xx)
        s = math.exp(_poly(c6, xx))

    pw = 1.0 - norm_cdf((y - m) / s)
    return (W, max(pw, 0.0))


# ---------------------------------------------------------------------------
# Location / two-sample tests
# ---------------------------------------------------------------------------

def _mean_var(xs):
    vals = [v for v in xs if isinstance(v, (int, float)) and math.isfinite(v)]
    n = len(vals)
    if n == 0:
        return (0, 0.0, 0.0)
    m = sum(vals) / n
    var = sum((v - m) ** 2 for v in vals) / (n - 1) if n > 1 else 0.0
    return (n, m, var)


def ttest_equal_var(a, b):
    """Student's two-sample t test (equal variance). Returns (t, df, p)."""
    n1, m1, v1 = _mean_var(a)
    n2, m2, v2 = _mean_var(b)
    if n1 < 2 or n2 < 2:
        return (float("nan"), None, float("nan"))
    df = n1 + n2 - 2
    sp2 = ((n1 - 1) * v1 + (n2 - 1) * v2) / df
    if sp2 <= 0:
        return (float("nan"), df, float("nan"))
    se = math.sqrt(sp2 * (1.0 / n1 + 1.0 / n2))
    t = (m1 - m2) / se
    p = 2.0 * (1.0 - t_cdf(abs(t), df))
    return (t, df, p)


def welch_ttest(a, b):
    """Welch's two-sample t test (unequal variance). Returns (t, df, p)."""
    n1, m1, v1 = _mean_var(a)
    n2, m2, v2 = _mean_var(b)
    if n1 < 2 or n2 < 2 or v1 <= 0 or v2 <= 0:
        return (float("nan"), None, float("nan"))
    se = math.sqrt(v1 / n1 + v2 / n2)
    t = (m1 - m2) / se
    num = (v1 / n1 + v2 / n2) ** 2
    den = (v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1)
    df = num / den
    p = 2.0 * (1.0 - t_cdf(abs(t), df))
    return (t, df, p)


def _rankdata(xs):
    """Average ranks, ties assigned the mean rank (as scipy)."""
    vals = [v for v in xs if isinstance(v, (int, float)) and math.isfinite(v)]
    order = sorted(range(len(vals)), key=lambda i: (vals[i], i))
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        rnk = (i + j + 2) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = rnk
        i = j + 1
    return vals, ranks


def mannwhitneyu(a, b, continuity: bool = True):
    """Mann-Whitney U (two-sided, asymptotic with tie correction).

    Returns (U, p). U = min(U1, U2), matching scipy's reported statistic.
    When continuity=True a 0.5 continuity correction is applied (as scipy's
    default asymptotic method).
    """
    vals_a = [v for v in a if isinstance(v, (int, float)) and math.isfinite(v)]
    vals_b = [v for v in b if isinstance(v, (int, float)) and math.isfinite(v)]
    n1, n2 = len(vals_a), len(vals_b)
    if n1 == 0 or n2 == 0:
        return (float("nan"), float("nan"))
    allvals = vals_a + vals_b
    _, allranks = _rankdata(allvals)
    # U for first sample
    R1 = sum(allranks[:n1])
    U1 = n1 * n2 + n1 * (n1 + 1) / 2.0 - R1
    U2 = n1 * n2 - U1
    U = min(U1, U2)
    # tie correction factor
    from collections import Counter
    cnt = Counter(allvals)
    n = n1 + n2
    tie_corr = sum((c ** 3 - c) for c in cnt.values()) / (n * (n - 1))
    mu = n1 * n2 / 2.0
    sigma = math.sqrt((n1 * n2 / 12.0) * (n + 1 - tie_corr))
    if sigma == 0:
        return (U, float("nan"))
    cc = 0.5 if continuity else 0.0
    z = (abs(U1 - mu) - cc) / sigma
    p = 2.0 * norm_sf(z)
    return (U, min(max(p, 0.0), 1.0))


def oneway_anova(groups):
    """One-way ANOVA. Returns (F, df_between, df_within, p)."""
    groups = [g for g in groups if len(g) > 0]
    k = len(groups)
    if k < 2:
        return (float("nan"), None, None, float("nan"))
    allvals = [v for g in groups for v in g]
    n = len(allvals)
    if n == 0:
        return (float("nan"), None, None, float("nan"))
    grand = sum(allvals) / n
    ss_between = sum(len(g) * (sum(g) / len(g) - grand) ** 2 for g in groups)
    ss_within = sum(sum((v - sum(g) / len(g)) ** 2 for v in g) for g in groups)
    df_between = k - 1
    df_within = n - k
    if df_within <= 0 or ss_within <= 0:
        return (float("nan"), df_between, df_within, float("nan"))
    ms_between = ss_between / df_between
    ms_within = ss_within / df_within
    F = ms_between / ms_within
    p = 1.0 - f_cdf(F, df_between, df_within)
    return (F, df_between, df_within, p)


def kruskal_wallis(groups):
    """Kruskal-Wallis H (rank ANOVA). Returns (H, df, p) with tie correction."""
    groups = [g for g in groups if len(g) > 0]
    k = len(groups)
    if k < 2:
        return (float("nan"), None, float("nan"))
    sizes = [len(g) for g in groups]
    n = sum(sizes)
    allvals = [v for g in groups for v in g]
    _, ranks = _rankdata(allvals)
    stat = 0.0
    idx = 0
    from collections import Counter
    cnt = Counter(allvals)
    tie_corr = 1.0 - sum((c ** 3 - c) for c in cnt.values()) / (n ** 3 - n)
    for gs in sizes:
        rg = sum(ranks[idx:idx + gs])
        stat += rg * rg / gs
        idx += gs
    H = (12.0 / (n * (n + 1))) * stat - 3.0 * (n + 1)
    H /= tie_corr
    df = k - 1
    p = 1.0 - chi2_cdf(H, df)
    return (H, df, p)


def brown_forsythe(groups):
    """Brown-Forsythe test of equal variance (median-centered Levene).

    Returns (W, df_between, df_within, p); W is the ANOVA F on absolute
    deviations from group medians.
    """
    groups = [g for g in groups if len(g) > 0]
    k = len(groups)
    if k < 2:
        return (float("nan"), None, None, float("nan"))
    medians = [_quantile_sorted(sorted(g), 0.5) for g in groups]
    zs = [[abs(v - medians[i]) for v in g] for i, g in enumerate(groups)]
    allz = [z for g in zs for z in g]
    n = len(allz)
    grand = sum(allz) / n
    ss_between = sum(len(g) * (sum(g) / len(g) - grand) ** 2 for g in zs)
    ss_within = sum(sum((v - sum(g) / len(g)) ** 2 for v in g) for g in zs)
    df_between = k - 1
    df_within = n - k
    if df_within <= 0 or ss_within <= 0:
        return (float("nan"), df_between, df_within, float("nan"))
    W = (ss_between / df_between) / (ss_within / df_within)
    p = 1.0 - f_cdf(W, df_between, df_within)
    return (W, df_between, df_within, p)


# ---------------------------------------------------------------------------
# Effect sizes
# ---------------------------------------------------------------------------

def cohen_d(a, b):
    """Cohen's d with pooled standard deviation."""
    n1, m1, v1 = _mean_var(a)
    n2, m2, v2 = _mean_var(b)
    if n1 < 1 or n2 < 1:
        return float("nan")
    sp = math.sqrt(((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2)) \
        if n1 + n2 > 2 else math.sqrt((v1 + v2) / 2.0)
    if sp == 0:
        return float("nan")
    return (m1 - m2) / sp


def hedges_g(a, b):
    """Hedges' g (bias-corrected Cohen's d)."""
    d = cohen_d(a, b)
    n1 = sum(1 for v in a if isinstance(v, (int, float)) and math.isfinite(v))
    n2 = sum(1 for v in b if isinstance(v, (int, float)) and math.isfinite(v))
    if n1 + n2 <= 0:
        return float("nan")
    j = 1.0 - 3.0 / (4.0 * (n1 + n2) - 9.0)
    return d * j


def glass_delta(a, control):
    """Glass's Delta = (mean_a - mean_control) / sd_control."""
    nc, mc, vc = _mean_var(control)
    na, ma, _ = _mean_var(a)
    if nc < 2 or vc <= 0:
        return float("nan")
    return (ma - mc) / math.sqrt(vc)


def eta_squared(groups):
    """eta^2 = SS_between / SS_total."""
    groups = [g for g in groups if len(g) > 0]
    if len(groups) < 2:
        return float("nan")
    allvals = [v for g in groups for v in g]
    grand = sum(allvals) / len(allvals)
    ss_between = sum(len(g) * (sum(g) / len(g) - grand) ** 2 for g in groups)
    ss_total = sum((v - grand) ** 2 for v in allvals)
    return ss_between / ss_total if ss_total > 0 else float("nan")


def omega_squared(groups):
    """omega^2 = (SS_b - df_b * MS_w) / (SS_t + MS_w)."""
    groups = [g for g in groups if len(g) > 0]
    k = len(groups)
    if k < 2:
        return float("nan")
    allvals = [v for g in groups for v in g]
    n = len(allvals)
    grand = sum(allvals) / n
    ss_between = sum(len(g) * (sum(g) / len(g) - grand) ** 2 for g in groups)
    ss_within = sum(sum((v - sum(g) / len(g)) ** 2 for v in g) for g in groups)
    ss_total = sum((v - grand) ** 2 for v in allvals)
    df_between = k - 1
    df_within = n - k
    if df_within <= 0:
        return float("nan")
    ms_within = ss_within / df_within
    denom = ss_total + ms_within
    return (ss_between - df_between * ms_within) / denom if denom > 0 else float("nan")


def cramers_v(table):
    """Cramer's V for a contingency table (list of rows of ints/floats).

    Returns (V, chi2, df, p).
    """
    rows = [r for r in table if len(r) > 0]
    if not rows:
        return (float("nan"), float("nan"), None, float("nan"))
    ncols = len(rows[0])
    for r in rows:
        if len(r) != ncols:
            raise ValueError("ragged table")
    n = sum(sum(r) for r in rows)
    if n == 0:
        return (float("nan"), 0.0, None, float("nan"))
    row_sums = [sum(r) for r in rows]
    col_sums = [sum(rows[i][j] for i in range(len(rows))) for j in range(ncols)]
    chi2 = 0.0
    for i in range(len(rows)):
        for j in range(ncols):
            expected = row_sums[i] * col_sums[j] / n
            if expected > 0:
                chi2 += (rows[i][j] - expected) ** 2 / expected
    df = (len(rows) - 1) * (ncols - 1)
    if df < 1:
        return (float("nan"), chi2, df, float("nan"))
    p = 1.0 - chi2_cdf(chi2, df)
    v = math.sqrt(chi2 / (n * min(len(rows) - 1, ncols - 1)))
    return (v, chi2, df, p)


def pearson_r(x, y):
    """Pearson correlation. Returns (r, n)."""
    xs = [v for v in x if isinstance(v, (int, float)) and math.isfinite(v)]
    ys = [v for v in y if isinstance(v, (int, float)) and math.isfinite(v)]
    n = min(len(xs), len(ys))
    xs, ys = xs[:n], ys[:n]
    if n < 2:
        return (float("nan"), n)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((v - mx) ** 2 for v in xs)
    syy = sum((v - my) ** 2 for v in ys)
    sxy = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    if sxx <= 0 or syy <= 0:
        return (float("nan"), n)
    return (sxy / math.sqrt(sxx * syy), n)


# ---------------------------------------------------------------------------
# Confidence intervals
# ---------------------------------------------------------------------------

def mean_ci(x, conf: float = 0.95):
    """t-based CI for the mean. Returns (lo, hi)."""
    n, m, v = _mean_var(x)
    if n < 2:
        return (float("nan"), float("nan"))
    se = math.sqrt(v / n)
    tcrit = t_inv(0.5 + conf / 2.0, n - 1)
    return (m - tcrit * se, m + tcrit * se)


def welch_diff_ci(a, b, conf: float = 0.95):
    """Welch CI for the mean difference. Returns (diff, lo, hi)."""
    n1, m1, v1 = _mean_var(a)
    n2, m2, v2 = _mean_var(b)
    if n1 < 2 or n2 < 2 or v1 <= 0 or v2 <= 0:
        return (float("nan"), float("nan"), float("nan"))
    diff = m1 - m2
    se = math.sqrt(v1 / n1 + v2 / n2)
    num = (v1 / n1 + v2 / n2) ** 2
    den = (v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1)
    df = num / den
    tcrit = t_inv(0.5 + conf / 2.0, df)
    return (diff, diff - tcrit * se, diff + tcrit * se)


def cohen_d_ci(d: float, n1: int, n2: int, conf: float = 0.95):
    """Approximate CI for Cohen's d (Hedges & Olkin large-sample SE)."""
    if n1 < 2 or n2 < 2:
        return (float("nan"), float("nan"))
    se = math.sqrt((n1 + n2) / (n1 * n2) + d * d / (2.0 * (n1 + n2)))
    z = ndtri(0.5 + conf / 2.0)
    return (d - z * se, d + z * se)


def wilson_proportion_ci(k: int, n: int, conf: float = 0.95):
    """Wilson score interval for a proportion. Returns (lo, hi)."""
    if n <= 0:
        return (float("nan"), float("nan"))
    z = ndtri(0.5 + conf / 2.0)
    phat = k / n
    den = 1.0 + z * z / n
    center = (phat + z * z / (2.0 * n)) / den
    half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4.0 * n * n)) / den
    return (center - half, center + half)


def fisher_z_corr_ci(r: float, n: int, conf: float = 0.95):
    """Fisher z-transformed CI for a correlation coefficient."""
    if n < 4 or abs(r) >= 1.0:
        return (float("nan"), float("nan"))
    z = math.atanh(r)
    se = 1.0 / math.sqrt(n - 3)
    zc = ndtri(0.5 + conf / 2.0)
    lo, hi = z - zc * se, z + zc * se
    return (math.tanh(lo), math.tanh(hi))


# ---------------------------------------------------------------------------
# Multiple comparison correction
# ---------------------------------------------------------------------------

def bonferroni(pvals):
    """Bonferroni-adjusted p-values (capped at 1)."""
    m = len(pvals)
    return [min(p * m, 1.0) for p in pvals]


def bh_fdr(pvals):
    """Benjamini-Hochberg FDR-adjusted p-values."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [1.0] * m
    prev = 1.0
    for rank in range(m - 1, -1, -1):
        i = order[rank]
        q = pvals[i] * m / (rank + 1)
        q = min(q, prev)
        prev = q
        adj[i] = q
    return [min(q, 1.0) for q in adj]


# ---------------------------------------------------------------------------
# Self test (compare against hard-coded reference values)
# ---------------------------------------------------------------------------

def _approx(a, b, tol=1e-6):
    return abs(a - b) <= tol * max(1.0, abs(b))


def selftest() -> int:
    """Run internal consistency checks. Exit code 0 on success."""
    checks = []

    # normal quantiles
    checks.append(_approx(ndtri(0.975), 1.959963984540054, 1e-9))
    checks.append(_approx(ndtri(0.5), 0.0, 1e-12))
    checks.append(_approx(norm_cdf(0.0), 0.5, 1e-12))
    checks.append(_approx(norm_cdf(1.96), 0.9750021048517795, 1e-9))

    # t / F / chi2 sanity
    checks.append(_approx(t_cdf(0.0, 10), 0.5, 1e-12))
    checks.append(_approx(t_cdf(2.228, 10), 0.975, 1e-3))
    checks.append(_approx(f_cdf(4.0, 2, 20), 0.966, 1e-2))
    checks.append(_approx(chi2_cdf(3.841, 1), 0.95, 1e-2))

    # descriptive
    d = descriptive([1, 2, 3, 4, 5])
    checks.append(d["mean"] == 3.0 and _approx(d["sd"], 1.5811388300841898))
    checks.append(_approx(d["median"], 3.0))
    checks.append(_approx(d["q1"], 2.0) and _approx(d["q3"], 4.0))

    # Shapiro-Wilk on a known sample (cross-checked vs scipy/R)
    W, p = shapiro_wilk([0.139, 0.157, 0.175, 0.256, 0.344, 0.413, 0.503,
                         0.577, 0.614, 0.655, 0.954, 1.392, 1.557, 1.648,
                         1.690, 1.994, 2.174, 2.206, 3.245, 3.510])
    checks.append(_approx(W, 0.8827054960190692, 1e-6))

    # two-sample tests
    a = [1, 2, 3, 4, 5]
    b = [2, 3, 4, 5, 6]
    t, df, p = welch_ttest(a, b)
    checks.append(_approx(t, -1.0, 1e-9) and df == 8 and _approx(p, 0.3465935, 1e-4))

    # ANOVA
    g1 = [5, 6, 7, 8, 9]
    g2 = [7, 8, 9, 10, 11]
    g3 = [11, 12, 13, 14, 15]
    F, dfb, dfw, p = oneway_anova([g1, g2, g3])
    checks.append(_approx(F, 18.666666666666668, 1e-9) and dfb == 2 and dfw == 12)
    checks.append(_approx(eta_squared([g1, g2, g3]), 0.7567567567567568, 1e-9))

    # CIs
    lo, hi = mean_ci([1, 2, 3, 4, 5])
    checks.append(_approx(lo, 1.036756, 1e-4) and _approx(hi, 4.963244, 1e-4))

    failed = [i + 1 for i, ok in enumerate(checks) if not ok]
    if failed:
        print("statlib selftest FAILED at:", failed)
        return 1
    print("statlib selftest OK (%d checks)" % len(checks))
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())
