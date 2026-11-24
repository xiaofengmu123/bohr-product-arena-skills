"""statlib.py - deterministic, standard-library-only statistical computing.

Design constraints (robustness-first):
  * No third-party imports: works where numpy/scipy are absent.
  * Deterministic: no randomness, no global mutable state, stable output order.
  * Every numeric routine returns None (never raises) when its assumptions
    cannot be satisfied (n<2, zero variance, undefined denominator), so callers
    can degrade gracefully instead of crashing.
  * All test statistics and p-values are computed from first principles
    (incomplete beta / incomplete gamma / normal CDF), so the same numbers can
    be re-derived by hand or re-checked with scipy when available.

Numerical conventions (documented so results are auditable):
  * standard deviation uses sample (Bessel-corrected, ddof=1) formula.
  * quantiles use the linear-interpolation method (type 7, matching numpy's
    default percentile).
  * skewness is Fisher-Pearson g1; kurtosis is excess kurtosis g2.
  * p-values are two-sided unless stated otherwise.
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# Basic helpers
# ---------------------------------------------------------------------------

EPS = 1e-15
FPMIN = 1e-300


def mean(x):
    if not x:
        return None
    return sum(x) / len(x)


def variance(x, ddof: int = 1):
    n = len(x)
    if n < 2:
        return None
    m = mean(x)
    if m is None:
        return None
    denom = n - ddof
    if denom <= 0:
        return None
    return sum((xi - m) ** 2 for xi in x) / denom


def std(x, ddof: int = 1):
    v = variance(x, ddof)
    if v is None:
        return None
    return math.sqrt(v)


def median(x):
    if not x:
        return None
    a = sorted(x)
    n = len(a)
    if n % 2:
        return a[n // 2]
    return (a[n // 2 - 1] + a[n // 2]) / 2.0


def quantile(x, p: float):
    """Linear-interpolation quantile (numpy 'linear'/type 7)."""
    if not x:
        return None
    a = sorted(x)
    n = len(a)
    if n == 1:
        return a[0]
    pos = (n - 1) * p
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo < 0:
        lo = 0
    if hi > n - 1:
        hi = n - 1
    if lo == hi:
        return a[lo]
    frac = pos - lo
    return a[lo] * (1.0 - frac) + a[hi] * frac


def skewness(x):
    n = len(x)
    if n < 3:
        return None
    m = mean(x)
    s = std(x, 1)
    if m is None or s is None or s == 0:
        return None
    z3 = sum(((xi - m) / s) ** 3 for xi in x)
    return (n / ((n - 1) * (n - 2))) * z3


def kurtosis(x):
    """Excess kurtosis (g2)."""
    n = len(x)
    if n < 4:
        return None
    m = mean(x)
    s = std(x, 1)
    if m is None or s is None or s == 0:
        return None
    z4 = sum(((xi - m) / s) ** 4 for xi in x)
    return (n * (n + 1) / ((n - 1) * (n - 2) * (n - 3))) * z4 \
        - 3 * (n - 1) ** 2 / ((n - 2) * (n - 3))


def descriptive(x):
    """Return a dict of descriptive statistics (all None-safe)."""
    n = len(x)
    out = {
        "n": n,
        "mean": mean(x),
        "median": median(x),
        "sd": std(x, 1),
        "min": min(x) if x else None,
        "max": max(x) if x else None,
        "q1": quantile(x, 0.25),
        "q3": quantile(x, 0.75),
    }
    out["iqr"] = (out["q3"] - out["q1"]) if (out["q1"] is not None and out["q3"] is not None) else None
    out["skewness"] = skewness(x)
    out["kurtosis"] = kurtosis(x)
    return out


# ---------------------------------------------------------------------------
# Special functions
# ---------------------------------------------------------------------------

def _lgamma(z):
    return math.lgamma(z)


def _betacf(a, b, x):
    """Continued fraction for incomplete beta (Numerical Recipes betacf)."""
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, 500):
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


def betainc(a, b, x):
    """Regularized incomplete beta I_x(a,b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(_lgamma(a + b) - _lgamma(a) - _lgamma(b)
                  + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _gammainc_series(a, x):
    ap = a
    total = 1.0 / a
    term = 1.0 / a
    for n in range(1, 20000):
        ap += 1.0
        term *= x / ap
        total += term
        if abs(term) < abs(total) * 1e-15:
            break
    return total * math.exp(-x + a * math.log(x) - _lgamma(a))


def _gammainc_cf(a, x):
    b = x + 1.0 - a
    c = 1e300
    d = 1.0 / b
    h = d
    for i in range(1, 20000):
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
        if abs(delta - 1.0) < 1e-15:
            break
    return math.exp(-x + a * math.log(x) - _lgamma(a)) * h


def gammainc_p(a, x):
    """Regularized lower incomplete gamma P(a,x)."""
    if x < 0 or a <= 0:
        return None
    if x == 0.0:
        return 0.0
    if x < a + 1.0:
        return _gammainc_series(a, x)
    return 1.0 - _gammainc_cf(a, x)


def normal_cdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def normal_quantile(p):
    """Inverse normal CDF (Acklam's algorithm + one Halley refinement)."""
    if p <= 0.0:
        return float("-inf")
    if p >= 1.0:
        return float("inf")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow = 0.02425
    phigh = 1.0 - plow
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
    e = 0.5 * math.erfc(-x / math.sqrt(2.0)) - p
    u = e * math.sqrt(2.0 * math.pi) * math.exp(x * x / 2.0)
    x = x - u / (1.0 + x * u / 2.0)
    return x


# ---------------------------------------------------------------------------
# Distribution CDFs / p-values
# ---------------------------------------------------------------------------

def t_cdf(t, df):
    if df <= 0:
        return None
    df = min(df, 1e6)
    x = df / (df + t * t)
    ib = betainc(df / 2.0, 0.5, x)
    if t > 0:
        return 1.0 - 0.5 * ib
    return 0.5 * ib


def t_pvalue(t, df):
    """Two-sided p-value for Student t."""
    if df <= 0:
        return None
    df = min(df, 1e6)
    return betainc(df / 2.0, 0.5, df / (df + t * t))


def t_quantile(p, df):
    """Inverse Student t CDF via bisection."""
    if p <= 0.0:
        return float("-inf")
    if p >= 1.0:
        return float("inf")
    lo, hi = -1e4, 1e4
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def f_cdf(x, df1, df2):
    if x <= 0:
        return 0.0
    y = df1 * x / (df1 * x + df2)
    return betainc(df1 / 2.0, df2 / 2.0, y)


def f_pvalue(x, df1, df2):
    if x < 0:
        return None
    if x == 0:
        return 1.0
    y = df2 / (df2 + df1 * x)
    return betainc(df2 / 2.0, df1 / 2.0, y)


def chi2_cdf(x, df):
    if x <= 0:
        return 0.0
    return gammainc_p(df / 2.0, x / 2.0)


def chi2_pvalue(x, df):
    if x < 0:
        return None
    if x == 0:
        return 1.0
    p = gammainc_p(df / 2.0, x / 2.0)
    if p is None:
        return None
    return 1.0 - p


# ---------------------------------------------------------------------------
# Assumption tests
# ---------------------------------------------------------------------------

def _rank_avg(values):
    """Average ranks with tie handling. Returns list aligned with values."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    n = len(order)
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        r = (i + 1 + j + 1) / 2.0  # average rank of tie group
        for k in range(i, j + 1):
            ranks[order[k]] = r
        i = j + 1
    return ranks


def shapiro_francia(x):
    """Shapiro-Francia W' via Blom scores. Screening statistic only.

    Returns W' in (0,1]; a low value flags non-normality. No p-value is
    attached here (exact p requires extra tables); use jarque_bera for an
    inferential p-value or cross-check scipy.stats.shapiro when available.
    """
    n = len(x)
    if n < 3:
        return None
    a = sorted(x)
    scores = [normal_quantile((i + 1 - 0.375) / (n + 0.25)) for i in range(n)]
    s2 = sum((xi - mean(a)) ** 2 for xi in a)
    sm2 = sum(mi * mi for mi in scores)
    if s2 == 0 or sm2 == 0:
        return None
    num = sum(mi * xi for mi, xi in zip(scores, a))
    return (num * num) / (sm2 * s2)


def jarque_bera(x):
    """Jarque-Bera normality test; returns dict or None if n<4."""
    n = len(x)
    if n < 4:
        return None
    s = skewness(x)
    k = kurtosis(x)
    if s is None or k is None:
        return None
    jb = n / 6.0 * (s * s + k * k / 4.0)
    p = chi2_pvalue(jb, 2)
    return {"statistic": jb, "p": p, "df": 2, "skewness": s, "kurtosis": k}


def levene_brown_forsythe(groups):
    """Brown-Forsythe (median-centered) homogeneity-of-variance test."""
    groups = [g for g in groups if g]
    k = len(groups)
    if k < 2:
        return None
    z = []
    group_idx = []
    for gi, g in enumerate(groups):
        med = median(g)
        if med is None:
            return None
        for v in g:
            z.append(abs(v - med))
            group_idx.append(gi)
    return _anova_on_indexed(z, group_idx, k)


def _anova_on_indexed(values, group_idx, k):
    """One-way ANOVA on already-indexed values."""
    n_total = len(values)
    if n_total <= k:
        return None
    group_vals = [[] for _ in range(k)]
    for v, gi in zip(values, group_idx):
        group_vals[gi].append(v)
    grand = mean(values)
    ss_b = 0.0
    ss_w = 0.0
    for g in group_vals:
        if not g:
            return None
        m = mean(g)
        ss_b += len(g) * (m - grand) ** 2
        ss_w += sum((v - m) ** 2 for v in g)
    df_b = k - 1
    df_w = n_total - k
    if df_w <= 0 or ss_w == 0:
        # Zero within-group variance: test not applicable.
        return {"statistic": None, "p": None, "df1": df_b, "df2": df_w,
                "not_applicable": "zero_within_group_variance"}
    f = (ss_b / df_b) / (ss_w / df_w)
    p = f_pvalue(f, df_b, df_w)
    return {"statistic": f, "p": p, "df1": df_b, "df2": df_w,
            "ss_between": ss_b, "ss_within": ss_w}


def anova(groups):
    """One-way ANOVA on groups of numeric values."""
    groups = [g for g in groups if g]
    k = len(groups)
    if k < 2:
        return None
    values = []
    idx = []
    for gi, g in enumerate(groups):
        for v in g:
            values.append(v)
            idx.append(gi)
    if len(values) <= k:
        return None
    res = _anova_on_indexed(values, idx, k)
    if res:
        res["k"] = k
        res["n_total"] = len(values)
    return res


# ---------------------------------------------------------------------------
# Inference: two-sample tests
# ---------------------------------------------------------------------------

def student_t_test(x, y):
    n1, n2 = len(x), len(y)
    if n1 < 2 or n2 < 2:
        return None
    m1, m2 = mean(x), mean(y)
    v1, v2 = variance(x), variance(y)
    if m1 is None or m2 is None or v1 is None or v2 is None:
        return None
    sp2 = ((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2)
    if sp2 <= 0:
        return {"statistic": None, "p": None, "df": n1 + n2 - 2,
                "not_applicable": "zero_pooled_variance"}
    se = math.sqrt(sp2 * (1.0 / n1 + 1.0 / n2))
    t = (m1 - m2) / se
    df = n1 + n2 - 2
    p = t_pvalue(t, df)
    return {"statistic": t, "p": p, "df": df, "mean_diff": m1 - m2,
            "pooled_sd": math.sqrt(sp2), "method": "student_t_pooled"}


def welch_t_test(x, y):
    n1, n2 = len(x), len(y)
    if n1 < 2 or n2 < 2:
        return None
    m1, m2 = mean(x), mean(y)
    v1, v2 = variance(x), variance(y)
    if m1 is None or m2 is None or v1 is None or v2 is None or v1 == 0 or v2 == 0:
        return None
    se = math.sqrt(v1 / n1 + v2 / n2)
    if se == 0:
        return None
    t = (m1 - m2) / se
    df = (v1 / n1 + v2 / n2) ** 2 / (
        (v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1))
    p = t_pvalue(t, df)
    return {"statistic": t, "p": p, "df": df, "mean_diff": m1 - m2,
            "method": "welch_t"}


def mann_whitney(x, y):
    n1, n2 = len(x), len(y)
    if n1 < 1 or n2 < 1:
        return None
    combined = list(x) + list(y)
    ranks = _rank_avg(combined)
    r1 = sum(ranks[:n1])
    r2 = sum(ranks[n1:])
    u1 = r1 - n1 * (n1 + 1) / 2.0
    u2 = r2 - n2 * (n2 + 1) / 2.0
    u = min(u1, u2)
    n = n1 + n2
    from collections import Counter
    counts = Counter(combined)
    tie_adj = sum(c ** 3 - c for c in counts.values())
    var_u = n1 * n2 / 12.0 * ((n + 1) - tie_adj / (n * (n - 1)))
    if var_u <= 0:
        return None
    mean_u = n1 * n2 / 2.0
    # Continuity correction for a slightly better normal approximation.
    z = (abs(u - mean_u) - 0.5) / math.sqrt(var_u)
    p = 2.0 * (1.0 - normal_cdf(abs(z)))
    return {"statistic": u, "p": p, "u1": u1, "u2": u2,
            "n1": n1, "n2": n2, "method": "mann_whitney_normal_approx",
            "note": "normal approximation with tie and continuity correction"}


def kruskal_wallis(groups):
    groups = [g for g in groups if g]
    k = len(groups)
    if k < 2:
        return None
    ns = [len(g) for g in groups]
    if any(n < 1 for n in ns):
        return None
    all_vals = []
    for g in groups:
        all_vals.extend(g)
    n = len(all_vals)
    if n <= k:
        return None
    ranks = _rank_avg(all_vals)
    rj = []
    pos = 0
    for nj in ns:
        rj.append(sum(ranks[pos:pos + nj]))
        pos += nj
    h = 12.0 / (n * (n + 1)) * sum(r ** 2 / nj for r, nj in zip(rj, ns)) - 3 * (n + 1)
    from collections import Counter
    counts = Counter(all_vals)
    tie_adj = sum(c ** 3 - c for c in counts.values())
    correction = 1.0 - tie_adj / (n ** 3 - n)
    if correction <= 0:
        return None
    h_c = h / correction
    p = chi2_pvalue(h_c, k - 1)
    return {"statistic": h_c, "p": p, "df": k - 1, "k": k, "n_total": n,
            "method": "kruskal_wallis"}


def paired_t_test(x, y):
    if len(x) != len(y) or len(x) < 2:
        return None
    d = [a - b for a, b in zip(x, y)]
    md = mean(d)
    sd_ = std(d, 1)
    n = len(d)
    if md is None or sd_ is None or sd_ == 0:
        return None
    t = md / (sd_ / math.sqrt(n))
    p = t_pvalue(t, n - 1)
    return {"statistic": t, "p": p, "df": n - 1, "mean_diff": md,
            "sd_diff": sd_, "method": "paired_t"}


def wilcoxon_signed_rank(x, y):
    if len(x) != len(y) or len(x) < 1:
        return None
    d = [a - b for a, b in zip(x, y)]
    nz = [v for v in d if v != 0]
    n = len(nz)
    if n == 0:
        return None
    abs_vals = [abs(v) for v in nz]
    ranks = _rank_avg(abs_vals)
    w_pos = sum(r for v, r in zip(nz, ranks) if v > 0)
    w_neg = sum(r for v, r in zip(nz, ranks) if v < 0)
    w = min(w_pos, w_neg)
    mean_w = n * (n + 1) / 4.0
    from collections import Counter
    counts = Counter(abs_vals)
    tie_adj = sum(c ** 3 - c for c in counts.values())
    var_w = n * (n + 1) * (2 * n + 1) / 24.0 - tie_adj / 48.0
    if var_w <= 0:
        return None
    z = (abs(w - mean_w) - 0.5) / math.sqrt(var_w)
    p = 2.0 * (1.0 - normal_cdf(abs(z)))
    return {"statistic": w, "p": p, "n_nonzero": n, "w_pos": w_pos, "w_neg": w_neg,
            "method": "wilcoxon_signed_rank_normal_approx"}


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------

def pearson(x, y):
    n = len(x)
    if n != len(y) or n < 3:
        return None
    mx, my = mean(x), mean(y)
    if mx is None or my is None:
        return None
    dx = [xi - mx for xi in x]
    dy = [yi - my for yi in y]
    sxx = sum(a * a for a in dx)
    syy = sum(b * b for b in dy)
    sxy = sum(a * b for a, b in zip(dx, dy))
    if sxx == 0 or syy == 0:
        return None
    r = sxy / math.sqrt(sxx * syy)
    r = max(-1.0, min(1.0, r))
    t = r * math.sqrt((n - 2) / (1 - r * r)) if abs(r) < 1.0 else float("inf")
    p = t_pvalue(t, n - 2) if abs(r) < 1.0 else 0.0
    return {"r": r, "p": p, "n": n, "method": "pearson"}


def fisher_z_ci(r, n, level=0.95):
    """Confidence interval for Pearson r via Fisher z-transformation."""
    if n <= 3 or abs(r) >= 1.0:
        return None
    z = 0.5 * math.log((1 + r) / (1 - r))
    zcrit = normal_quantile(1 - (1 - level) / 2)
    se = 1.0 / math.sqrt(n - 3)
    lo = math.tanh(z - zcrit * se)
    hi = math.tanh(z + zcrit * se)
    return {"lower": lo, "upper": hi, "level": level, "method": "fisher_z"}


def spearman(x, y):
    n = len(x)
    if n != len(y) or n < 3:
        return None
    rx = _rank_avg(list(x))
    ry = _rank_avg(list(y))
    res = pearson(rx, ry)
    if res is None:
        return None
    res["method"] = "spearman"
    return res


# ---------------------------------------------------------------------------
# Effect sizes
# ---------------------------------------------------------------------------

def cohens_d(x, y):
    n1, n2 = len(x), len(y)
    if n1 < 2 or n2 < 2:
        return None
    m1, m2 = mean(x), mean(y)
    v1, v2 = variance(x), variance(y)
    if m1 is None or m2 is None or v1 is None or v2 is None:
        return None
    sp2 = ((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2)
    if sp2 <= 0:
        return None
    return (m1 - m2) / math.sqrt(sp2)


def hedges_g(x, y):
    d = cohens_d(x, y)
    if d is None:
        return None
    n = len(x) + len(y)
    j = 1.0 - 3.0 / (4 * n - 9)
    return d * j


def glass_delta(x, control):
    n1, nc = len(x), len(control)
    if n1 < 2 or nc < 2:
        return None
    m1, mc = mean(x), mean(control)
    sc = std(control, 1)
    if m1 is None or mc is None or sc is None or sc == 0:
        return None
    return (m1 - mc) / sc


def cohens_d_ci(d, n1, n2, level=0.95):
    """Large-sample CI for Cohen's d (approximate)."""
    if d is None or n1 < 1 or n2 < 1:
        return None
    zcrit = normal_quantile(1 - (1 - level) / 2)
    se = math.sqrt((n1 + n2) / (n1 * n2) + d * d / (2 * (n1 + n2)))
    return {"lower": d - zcrit * se, "upper": d + zcrit * se, "level": level,
            "method": "asymptotic_approximation"}


def eta_squared_omega(anova_res):
    """Compute eta^2 and omega^2 from an anova() result dict."""
    if not anova_res or anova_res.get("statistic") is None:
        return None
    ss_b = anova_res.get("ss_between")
    ss_w = anova_res.get("ss_within")
    if ss_b is None or ss_w is None:
        return None
    ss_t = ss_b + ss_w
    df_b = anova_res.get("df1")
    df_w = anova_res.get("df2")
    n_total = anova_res.get("n_total")
    if not ss_t or df_b is None or df_w is None:
        return None
    eta2 = ss_b / ss_t
    ms_w = ss_w / df_w if df_w else None
    if ms_w is None or n_total is None:
        return {"eta_sq": eta2, "omega_sq": None}
    omega2 = (ss_b - df_b * ms_w) / (ss_t + ms_w) if (ss_t + ms_w) else None
    if omega2 is not None and omega2 < 0:
        omega2 = 0.0
    return {"eta_sq": eta2, "omega_sq": omega2}


def cramers_v(contingency):
    """Cramer's V from a 2D contingency table of counts."""
    n_rows = len(contingency)
    if n_rows == 0:
        return None
    n_cols = len(contingency[0])
    if n_cols == 0:
        return None
    row_sums = [sum(r) for r in contingency]
    col_sums = [sum(contingency[i][j] for i in range(n_rows)) for j in range(n_cols)]
    n = sum(row_sums)
    if n == 0:
        return None
    chi2 = 0.0
    for i in range(n_rows):
        for j in range(n_cols):
            expected = row_sums[i] * col_sums[j] / n
            if expected > 0:
                chi2 += (contingency[i][j] - expected) ** 2 / expected
    df = (n_rows - 1) * (n_cols - 1)
    if df <= 0:
        return None
    p = chi2_pvalue(chi2, df)
    denom = n * min(n_rows - 1, n_cols - 1)
    v = math.sqrt(chi2 / denom) if denom > 0 else None
    return {"chi2": chi2, "p": p, "df": df, "cramers_v": v, "n": n}


# ---------------------------------------------------------------------------
# Confidence intervals
# ---------------------------------------------------------------------------

def mean_ci(x, level=0.95):
    n = len(x)
    if n < 2:
        return None
    m = mean(x)
    s = std(x, 1)
    if m is None or s is None:
        return None
    tcrit = t_quantile(1 - (1 - level) / 2, n - 1)
    half = tcrit * s / math.sqrt(n)
    return {"estimate": m, "lower": m - half, "upper": m + half, "level": level,
            "method": "student_t"}


def mean_diff_ci(x, y, level=0.95):
    """Welch (unequal-variance) CI for the difference of two means."""
    n1, n2 = len(x), len(y)
    if n1 < 2 or n2 < 2:
        return None
    m1, m2 = mean(x), mean(y)
    v1, v2 = variance(x), variance(y)
    if m1 is None or m2 is None or v1 is None or v2 is None:
        return None
    se = math.sqrt(v1 / n1 + v2 / n2)
    df = (v1 / n1 + v2 / n2) ** 2 / (
        (v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1))
    tcrit = t_quantile(1 - (1 - level) / 2, df)
    diff = m1 - m2
    return {"estimate": diff, "lower": diff - tcrit * se, "upper": diff + tcrit * se,
            "level": level, "method": "welch"}


def wilson_ci(successes, n, level=0.95):
    if n <= 0:
        return None
    z = normal_quantile(1 - (1 - level) / 2)
    phat = successes / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    margin = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return {"estimate": phat, "lower": center - margin, "upper": center + margin,
            "level": level, "method": "wilson_score"}


# ---------------------------------------------------------------------------
# Self-test (run: python scripts/statlib.py)
# ---------------------------------------------------------------------------

def _close(a, b, tol=1e-3):
    if a is None or b is None:
        return False
    return abs(a - b) <= tol * max(1.0, abs(b))


def selftest():
    checks = []
    # Descriptive
    d = descriptive([1, 2, 3, 4, 5])
    checks.append(("descriptive mean", _close(d["mean"], 3.0)))
    checks.append(("descriptive median", _close(d["median"], 3.0)))
    checks.append(("descriptive sd", _close(d["sd"], math.sqrt(2.5))))
    checks.append(("descriptive q1", _close(d["q1"], 2.0)))
    checks.append(("descriptive q3", _close(d["q3"], 4.0)))
    # t p-value: t=2, df=10 -> ~0.0734
    checks.append(("t_pvalue", _close(t_pvalue(2.0, 10), 0.0734, tol=5e-3)))
    # chi2 p-value: chi2=3.841459, df=1 -> 0.05
    checks.append(("chi2_pvalue", _close(chi2_pvalue(3.841458820694124, 1), 0.05, tol=1e-3)))
    # normal quantile
    checks.append(("normal_quantile 0.975", _close(normal_quantile(0.975), 1.959964, tol=1e-4)))
    # Wilson CI
    w = wilson_ci(5, 10)
    checks.append(("wilson lower", _close(w["lower"], 0.2366, tol=1e-3)))
    checks.append(("wilson upper", _close(w["upper"], 0.7634, tol=1e-3)))
    # Cohen's d on equal-separation data (d should be large and defined)
    dval = cohens_d([1, 2, 3], [4, 5, 6])
    checks.append(("cohens_d defined", dval is not None))
    # ANOVA on clearly separated groups -> small p
    a = anova([[1, 2, 3, 4], [10, 11, 12, 13], [20, 21, 22, 23]])
    checks.append(("anova small p", a is not None and a["p"] is not None and a["p"] < 0.001))
    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(("PASS" if ok else "FAIL"), name)
    if failed:
        print("FAILED:", failed)
        raise SystemExit(1)
    print("statlib selftest OK")


if __name__ == "__main__":
    selftest()
