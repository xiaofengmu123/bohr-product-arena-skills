"""statlib.py — deterministic, standard-library-only statistical library.

Design goals
------------
* No third-party imports: everything runs on a bare CPython 3.8+ install.
* Deterministic: no randomness anywhere (the caller never needs a seed here).
* Numerics are standard references (AS R94 Shapiro-Wilk, Numerical-Recipes
  incomplete beta/gamma, Acklam inverse normal).  They are approximations of
  the reference distributions, so every p-value carries an explicit
  ``method_note`` and can be cross-checked with scipy when it is available at
  run time (see core_impl.py / SKILL.md step "crosscheck").
* Honesty: functions return ``None`` / mark ``not_applicable`` instead of
  inventing a number when inputs violate preconditions (n < 2, zero variance,
  constant group, etc.).
"""

from __future__ import annotations

import math
from math import sqrt, log, exp, pi, erf, lgamma, atanh

INF = float("inf")
NAN = float("nan")


# --------------------------------------------------------------------------- #
# Normal distribution
# --------------------------------------------------------------------------- #
def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def normal_sf(x: float) -> float:
    return 1.0 - normal_cdf(x)


def normal_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's algorithm + one Halley step)."""
    if p <= 0.0:
        return -INF
    if p >= 1.0:
        return INF
    a = (-3.969683028665376e1, 2.209460984245205e2, -2.759285104469687e2,
         1.383577518672690e2, -3.066479806614716e1, 2.506628277459239e0)
    b = (-5.447609879822406e1, 1.615858368580409e2, -1.556989798598866e2,
         6.680131188771972e1, -1.328068155288572e1)
    c = (-7.784894002430293e-3, -3.223964580411365e-1, -2.400758277161838e0,
         -2.549732539343734e0, 4.374664141464968e0, 2.938163982698783e0)
    d = (7.784695709041462e-3, 3.224671290700398e-1, 2.445134137142996e0,
         3.754408661907416e0)
    plow, phigh = 0.02425, 0.97575
    if p < plow:
        q = sqrt(-2.0 * log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    elif p <= phigh:
        q = p - 0.5
        r = q * q
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    else:
        q = sqrt(-2.0 * log(1.0 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    e = normal_cdf(x) - p
    u = e * sqrt(2.0 * pi) * exp(x * x / 2.0)
    return x - u / (1.0 + x * u / 2.0)


# --------------------------------------------------------------------------- #
# Incomplete beta (continued fraction, Numerical Recipes 6.4)
# --------------------------------------------------------------------------- #
def _betacf(a: float, b: float, x: float) -> float:
    maxit, eps, fpmin = 200, 3.0e-12, 1.0e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, maxit + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def incomplete_beta(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if a <= 0.0 or b <= 0.0:
        raise ValueError("incomplete_beta requires a>0, b>0")
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = exp(lgamma(a + b) - lgamma(a) - lgamma(b)
             + a * log(x) + b * log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


# --------------------------------------------------------------------------- #
# Regularized gamma (series + continued fraction, Numerical Recipes 6.2)
# --------------------------------------------------------------------------- #
def _gser(a: float, x: float) -> float:
    ap = a
    total = 1.0 / a
    delta = total
    for _ in range(1, 400):
        ap += 1.0
        delta *= x / ap
        total += delta
        if abs(delta) < abs(total) * 1.0e-15:
            break
    return total


def _gcf(a: float, x: float) -> float:
    fpmin, eps = 1.0e-300, 3.0e-12
    b = x + 1.0 - a
    c = 1.0 / fpmin
    d = 1.0 / b
    h = d
    for i in range(1, 401):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < fpmin:
            d = fpmin
        c = b + an / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return exp(-x + a * log(x) - lgamma(a)) * h


def gamma_p(a: float, x: float) -> float:
    """Lower regularized gamma P(a, x)."""
    if x < 0.0 or a <= 0.0:
        raise ValueError("gamma_p requires a>0, x>=0")
    if x == 0.0:
        return 0.0
    if x < a + 1.0:
        return exp(a * log(x) - x - lgamma(a)) * _gser(a, x)
    # _gcf already carries the exp(-x + a*ln x - lngamma(a)) factor.
    return 1.0 - _gcf(a, x)


def gamma_q(a: float, x: float) -> float:
    return 1.0 - gamma_p(a, x)


# --------------------------------------------------------------------------- #
# Student t / F / chi-square
# --------------------------------------------------------------------------- #
def t_cdf(t: float, df: float) -> float:
    if df <= 0.0:
        raise ValueError("df must be positive")
    x = df / (df + t * t)
    ib = incomplete_beta(df / 2.0, 0.5, x)
    if t > 0.0:
        return 1.0 - 0.5 * ib
    return 0.5 * ib


def t_sf(t: float, df: float) -> float:
    return 1.0 - t_cdf(t, df)


def t_two_sided(t: float, df: float) -> float:
    return 2.0 * t_sf(abs(t), df)


def t_ppf(p: float, df: float) -> float:
    """Inverse t CDF for 0 < p < 1, via bisection on the CDF."""
    if p <= 0.0 or p >= 1.0 or df <= 0.0:
        raise ValueError("t_ppf requires 0<p<1 and df>0")
    if p == 0.5:
        return 0.0
    upper = p > 0.5
    target = p if upper else 1.0 - p
    lo, hi = 0.0, 200.0
    while t_cdf(hi, df) < target:
        hi *= 2.0
        if hi > 1.0e7:
            break
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_cdf(mid, df) < target:
            lo = mid
        else:
            hi = mid
    val = (lo + hi) / 2.0
    return val if upper else -val


def f_cdf(f: float, df1: float, df2: float) -> float:
    if f <= 0.0:
        return 0.0
    x = df1 * f / (df1 * f + df2)
    return incomplete_beta(df1 / 2.0, df2 / 2.0, x)


def f_sf(f: float, df1: float, df2: float) -> float:
    return 1.0 - f_cdf(f, df1, df2)


def chi2_cdf(x: float, df: float) -> float:
    if x <= 0.0:
        return 0.0
    return gamma_p(df / 2.0, x / 2.0)


def chi2_sf(x: float, df: float) -> float:
    return 1.0 - chi2_cdf(x, df)


# --------------------------------------------------------------------------- #
# Descriptive statistics
# --------------------------------------------------------------------------- #
def mean(x) -> float:
    x = [v for v in x if v is not None]
    if not x:
        return NAN
    return sum(x) / len(x)


def median(x) -> float:
    x = sorted(v for v in x if v is not None)
    if not x:
        return NAN
    n = len(x)
    if n % 2:
        return x[n // 2]
    return (x[n // 2 - 1] + x[n // 2]) / 2.0


def variance(x, ddof: int = 1) -> float:
    x = [v for v in x if v is not None]
    n = len(x)
    if n <= ddof:
        return NAN
    m = sum(x) / n
    return sum((v - m) ** 2 for v in x) / (n - ddof)


def stdev(x, ddof: int = 1) -> float:
    v = variance(x, ddof)
    return sqrt(v) if v >= 0 else NAN


def quantile(x, p: float) -> float:
    """Linear-interpolation quantile (type 7, matches numpy default)."""
    x = sorted(v for v in x if v is not None)
    if not x:
        return NAN
    if p < 0.0 or p > 1.0:
        raise ValueError("p must be in [0, 1]")
    if p == 0.0:
        return x[0]
    if p == 1.0:
        return x[-1]
    pos = (len(x) - 1) * p
    lo = int(pos)
    hi = lo + 1
    if hi >= len(x):
        return x[-1]
    frac = pos - lo
    return x[lo] * (1.0 - frac) + x[hi] * frac


def skewness(x) -> float:
    x = [v for v in x if v is not None]
    n = len(x)
    if n < 3:
        return NAN
    m = sum(x) / n
    m2 = sum((v - m) ** 2 for v in x) / n
    m3 = sum((v - m) ** 3 for v in x) / n
    if m2 <= 0:
        return NAN
    g1 = m3 / m2 ** 1.5
    return g1 * sqrt(n * (n - 1)) / (n - 2)


def kurtosis(x) -> float:
    x = [v for v in x if v is not None]
    n = len(x)
    if n < 4:
        return NAN
    m = sum(x) / n
    m2 = sum((v - m) ** 2 for v in x) / n
    m4 = sum((v - m) ** 4 for v in x) / n
    if m2 <= 0:
        return NAN
    g2 = m4 / m2 ** 2 - 3.0
    return (n - 1) / ((n - 2) * (n - 3)) * ((n + 1) * g2 + 6.0)


def describe(x):
    """Univariate summary.  Returns None values where not computable."""
    clean = [v for v in x if v is not None]
    n_all = len(x)
    n = len(clean)
    missing = n_all - n
    if n == 0:
        return {"n": 0, "n_missing": missing, "missing_pct": None,
                "mean": None, "median": None, "sd": None, "min": None,
                "max": None, "q1": None, "q3": None, "iqr": None,
                "skewness": None, "kurtosis": None}
    m = mean(clean)
    sd = stdev(clean)
    q1 = quantile(clean, 0.25)
    q3 = quantile(clean, 0.75)
    return {
        "n": n, "n_missing": missing,
        "missing_pct": round(100.0 * missing / n_all, 4) if n_all else None,
        "mean": m, "median": median(clean), "sd": sd,
        "min": min(clean), "max": max(clean),
        "q1": q1, "q3": q3,
        "iqr": (q3 - q1) if (q1 is not None and q3 is not None) else None,
        "skewness": skewness(clean), "kurtosis": kurtosis(clean),
    }


def zscore_modified(x):
    """Robust outlier detector: 0.6745*(v - median)/MAD."""
    clean = [v for v in x if v is not None]
    if not clean:
        return [None] * len(x)
    med = median(clean)
    mad = median([abs(v - med) for v in clean])
    if mad == 0 or mad is None:
        return [None] * len(x)
    return [0.6745 * (v - med) / mad if v is not None else None for v in x]


def iqr_bounds(x):
    clean = [v for v in x if v is not None]
    if not clean:
        return None
    q1 = quantile(clean, 0.25)
    q3 = quantile(clean, 0.75)
    iqr = q3 - q1
    return {"lower": q1 - 1.5 * iqr, "upper": q3 + 1.5 * iqr,
            "q1": q1, "q3": q3, "iqr": iqr}


# --------------------------------------------------------------------------- #
# Normality: Shapiro-Wilk (AS R94, Royston 1995)
# --------------------------------------------------------------------------- #
def _poly(cc, x: float) -> float:
    nord = len(cc)
    ret = cc[0]
    if nord > 1:
        p = x * cc[nord - 1]
        for j in range(nord - 2, 0, -1):
            p = (p + cc[j]) * x
        ret += p
    return ret


def shapiro_wilk(x):
    """Return {'W':.., 'p':.., 'n':.., 'ifault':..} or None if n<3."""
    x = sorted(v for v in x if v is not None)
    n = len(x)
    if n < 3:
        return None
    nn2 = n // 2
    a = [0.0] * (nn2 + 1)  # 1-based: indices 1..nn2
    small = 1e-19
    g = [-2.273, 0.459]
    c1 = [0.0, 0.221157, -0.147981, -2.07119, 4.434685, -2.706056]
    c2 = [0.0, 0.042981, -0.293762, -1.752461, 5.682633, -3.582633]
    c3 = [0.544, -0.39978, 0.025054, -6.714e-4]
    c4 = [1.3822, -0.77857, 0.062767, -0.0020322]
    c5 = [-1.5861, -0.31082, -0.083751, 0.0038915]
    c6 = [-0.4803, -0.082676, 0.0030302]

    an = float(n)
    if n == 3:
        a[1] = 0.70710678
    else:
        an25 = an + 0.25
        summ2 = 0.0
        for i in range(1, nn2 + 1):
            a[i] = normal_ppf((i - 0.375) / an25)
            summ2 += a[i] * a[i]
        summ2 *= 2.0
        ssumm2 = sqrt(summ2)
        rsn = 1.0 / sqrt(an)
        a1 = _poly(c1, rsn) - a[1] / ssumm2
        if n > 5:
            i1 = 3
            a2 = -a[2] / ssumm2 + _poly(c2, rsn)
            fac = sqrt((summ2 - 2.0 * a[1] * a[1] - 2.0 * a[2] * a[2]) /
                       (1.0 - 2.0 * a1 * a1 - 2.0 * a2 * a2))
            a[2] = a2
        else:
            i1 = 2
            fac = sqrt((summ2 - 2.0 * a[1] * a[1]) /
                       (1.0 - 2.0 * a1 * a1))
        a[1] = a1
        for i in range(i1, nn2 + 1):
            a[i] /= -fac

    range_ = x[-1] - x[0]
    if range_ < small:
        return {"W": None, "p": None, "n": n, "ifault": 6,
                "method_note": "constant sample (zero range)"}

    xx = x[0] / range_
    sx = xx
    sa = -a[1]
    j = n - 1
    for i in range(1, n):
        xi = x[i] / range_
        sx += xi
        i += 1
        if i != j:
            sa += (1 if i > j else -1) * a[min(i, j)]
        xx = xi
        j -= 1

    sa /= n
    sx /= n
    ssa = ssx = sax = 0.0
    j = n - 1
    for i in range(n):
        if i != j:
            asa = (1 if i > j else -1) * a[1 + min(i, j)] - sa
        else:
            asa = -sa
        xsx = x[i] / range_ - sx
        ssa += asa * asa
        ssx += xsx * xsx
        sax += asa * xsx
        j -= 1

    ssassx = sqrt(ssa * ssx)
    w1 = (ssassx - sax) * (ssassx + sax) / (ssa * ssx)
    w = 1.0 - w1

    if n == 3:
        pi6 = 1.90985931710274
        stqr = 1.04719755119660
        pw = pi6 * (math.asin(sqrt(w)) - stqr)
        pw = pw if pw >= 0.0 else 0.0
        return {"W": w, "p": pw, "n": n, "ifault": 0,
                "method_note": "Shapiro-Wilk (AS R94), exact for n=3"}

    y = log(w1)
    xx_l = log(an)
    if n <= 11:
        gamma_v = _poly(g, an)
        if y >= gamma_v:
            return {"W": w, "p": 1e-99, "n": n, "ifault": 0,
                    "method_note": "Shapiro-Wilk (AS R94), p floored"}
        y = -log(gamma_v - y)
        m = _poly(c3, an)
        s = exp(_poly(c4, an))
    else:
        m = _poly(c5, xx_l)
        s = exp(_poly(c6, xx_l))
    pw = normal_sf((y - m) / s)
    return {"W": w, "p": pw, "n": n, "ifault": 0,
            "method_note": "Shapiro-Wilk (AS R94), Royston p approximation"}


def shapiro_francia_w(x):
    """Shapiro-Francia W' statistic (correlation with Blom scores)."""
    x = sorted(v for v in x if v is not None)
    n = len(x)
    if n < 5:
        return None
    m = [normal_ppf((i + 1 - 0.375) / (n + 0.25)) for i in range(n)]
    xbar = sum(x) / n
    mbar = sum(m) / n
    sxy = sum((a - xbar) * (b - mbar) for a, b in zip(x, m))
    sxx = sum((a - xbar) ** 2 for a in x)
    smm = sum((b - mbar) ** 2 for b in m)
    if sxx <= 0 or smm <= 0:
        return None
    return sxy / sqrt(sxx * smm)


# --------------------------------------------------------------------------- #
# Rank helpers (average ties)
# --------------------------------------------------------------------------- #
def _average_ranks(values):
    """Return parallel list of average ranks for a numeric sequence."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    n = len(order)
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


# --------------------------------------------------------------------------- #
# Two-sample and multi-sample tests
# --------------------------------------------------------------------------- #
def students_t(a, b):
    a = [v for v in a if v is not None]
    b = [v for v in b if v is not None]
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return None
    m1, m2 = mean(a), mean(b)
    v1, v2 = variance(a), variance(b)
    df = n1 + n2 - 2
    sp2 = ((n1 - 1) * v1 + (n2 - 1) * v2) / df
    if sp2 <= 0:
        return None
    se = sqrt(sp2 * (1.0 / n1 + 1.0 / n2))
    t = (m1 - m2) / se
    return {"t": t, "df": df, "p": t_two_sided(t, df),
            "mean_diff": m1 - m2, "se": se,
            "method": "Student's t (equal variance)"}


def welch_t(a, b):
    a = [v for v in a if v is not None]
    b = [v for v in b if v is not None]
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return None
    m1, m2 = mean(a), mean(b)
    v1, v2 = variance(a), variance(b)
    if v1 <= 0 or v2 <= 0:
        return None
    se = sqrt(v1 / n1 + v2 / n2)
    t = (m1 - m2) / se
    num = (v1 / n1 + v2 / n2) ** 2
    den = (v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1)
    df = num / den if den > 0 else float("inf")
    p = t_two_sided(t, df)
    return {"t": t, "df": df, "p": p,
            "mean_diff": m1 - m2, "se": se,
            "method": "Welch's t (unequal variance)"}


def paired_t(a, b):
    a = list(a)
    b = list(b)
    if len(a) != len(b):
        return None
    d = [x - y for x, y in zip(a, b) if x is not None and y is not None]
    n = len(d)
    if n < 2:
        return None
    md = mean(d)
    sd = stdev(d)
    if sd is None or sd <= 0:
        return None
    t = md / (sd / sqrt(n))
    p = t_two_sided(t, n - 1)
    return {"t": t, "df": n - 1, "p": p, "mean_diff": md,
            "sd_diff": sd, "n_pairs": n,
            "method": "paired t-test"}


def mann_whitney(a, b, continuity: bool = True):
    a = [v for v in a if v is not None]
    b = [v for v in b if v is not None]
    n1, n2 = len(a), len(b)
    if n1 < 1 or n2 < 1:
        return None
    vals = a + b
    ranks = _average_ranks(vals)
    r1 = sum(ranks[:n1])
    u1 = r1 - n1 * (n1 + 1) / 2.0
    u2 = n1 * n2 - u1
    n = n1 + n2
    freq = {}
    for v in vals:
        freq[v] = freq.get(v, 0) + 1
    tsum = sum(t ** 3 - t for t in freq.values())
    var_u = (n1 * n2 / 12.0) * ((n + 1) - tsum / (n * (n - 1)))
    if var_u <= 0:
        return None
    u = min(u1, u2)
    znum = abs(u - (n1 * n2) / 2.0)
    if continuity:
        znum = max(0.0, znum - 0.5)
    z = znum / sqrt(var_u)
    p = 2.0 * normal_sf(z)
    return {"U": u, "U1": u1, "U2": u2, "z": z, "p": p,
            "n1": n1, "n2": n2,
            "method": "Mann-Whitney U (normal approximation, tie-corrected)"}


def anova(groups):
    """One-way ANOVA.  groups is a list of lists."""
    groups = [[v for v in g if v is not None] for g in groups]
    groups = [g for g in groups if g]
    k = len(groups)
    if k < 2:
        return None
    ns = [len(g) for g in groups]
    n = sum(ns)
    if any(x < 2 for x in ns):
        return None
    means = [mean(g) for g in groups]
    grand = sum(sum(g) for g in groups) / n
    ssb = sum(ns[i] * (means[i] - grand) ** 2 for i in range(k))
    ssw = sum((v - means[i]) ** 2 for i, g in enumerate(groups) for v in g)
    df1, df2 = k - 1, n - k
    if ssw <= 0:
        return None
    msb = ssb / df1
    msw = ssw / df2
    f = msb / msw
    p = f_sf(f, df1, df2)
    sst = ssb + ssw
    eta2 = ssb / sst if sst > 0 else None
    omega2 = (ssb - df1 * msw) / (sst + msw) if (sst + msw) > 0 else None
    return {"F": f, "df1": df1, "df2": df2, "p": p,
            "ss_between": ssb, "ss_within": ssw, "ss_total": sst,
            "eta_sq": eta2, "omega_sq": omega2,
            "method": "one-way ANOVA"}


def kruskal_wallis(groups):
    groups = [[v for v in g if v is not None] for g in groups]
    groups = [g for g in groups if g]
    k = len(groups)
    if k < 2:
        return None
    ns = [len(g) for g in groups]
    n = sum(ns)
    if n < 3:
        return None
    flat = [v for g in groups for v in g]
    ranks = _average_ranks(flat)
    r_sums = []
    idx = 0
    for g in groups:
        r_sums.append(sum(ranks[idx:idx + len(g)]))
        idx += len(g)
    h = 12.0 / (n * (n + 1)) * sum(r ** 2 / ns[i] for i, r in enumerate(r_sums)) - 3.0 * (n + 1)
    freq = {}
    for v in flat:
        freq[v] = freq.get(v, 0) + 1
    tsum = sum(t ** 3 - t for t in freq.values())
    denom = n ** 3 - n
    correction = 1.0 - tsum / denom if denom > 0 else 1.0
    h_corr = h / correction if correction > 0 else h
    p = chi2_sf(h_corr, k - 1)
    return {"H": h, "H_tie_corrected": h_corr, "df": k - 1, "p": p,
            "n": n, "k": k,
            "method": "Kruskal-Wallis H (chi-square approx, tie-corrected)"}


def brown_forsythe(groups):
    """Brown-Forsythe (Levene on medians) homogeneity-of-variance test."""
    groups = [[v for v in g if v is not None] for g in groups]
    groups = [g for g in groups if g]
    k = len(groups)
    if k < 2:
        return None
    medians = [median(g) for g in groups]
    zs = [[abs(v - medians[i]) for v in g] for i, g in enumerate(groups)]
    ns = [len(g) for g in groups]
    n = sum(ns)
    if n <= k or any(x < 2 for x in ns):
        return None
    zm = [mean(z) for z in zs]
    zgrand = sum(sum(z) for z in zs) / n
    ssb = sum(ns[i] * (zm[i] - zgrand) ** 2 for i in range(k))
    ssw = sum((v - zm[i]) ** 2 for i, z in enumerate(zs) for v in z)
    if ssw <= 0:
        return None
    df1, df2 = k - 1, n - k
    f = (ssb / df1) / (ssw / df2)
    p = f_sf(f, df1, df2)
    return {"F": f, "df1": df1, "df2": df2, "p": p,
            "method": "Brown-Forsythe (Levene on medians)"}


# --------------------------------------------------------------------------- #
# Effect sizes
# --------------------------------------------------------------------------- #
def pooled_sd(a, b):
    a = [v for v in a if v is not None]
    b = [v for v in b if v is not None]
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return None
    v1, v2 = variance(a), variance(b)
    df = n1 + n2 - 2
    sp2 = ((n1 - 1) * v1 + (n2 - 1) * v2) / df
    return sqrt(sp2) if sp2 > 0 else None


def cohens_d(a, b):
    a = [v for v in a if v is not None]
    b = [v for v in b if v is not None]
    sp = pooled_sd(a, b)
    if sp is None or sp <= 0:
        return None
    return (mean(a) - mean(b)) / sp


def hedges_g(a, b):
    a = [v for v in a if v is not None]
    b = [v for v in b if v is not None]
    d = cohens_d(a, b)
    if d is None:
        return None
    n1, n2 = len(a), len(b)
    df = n1 + n2 - 2
    if df < 1:
        return None
    return d * (1.0 - 3.0 / (4.0 * df - 1.0))


def glass_delta(a, b, control_index: int = 1):
    """Standardized mean difference using control group SD (control_index 0=a,1=b)."""
    a = [v for v in a if v is not None]
    b = [v for v in b if v is not None]
    sd_ref = stdev(b) if control_index == 1 else stdev(a)
    if sd_ref is None or sd_ref <= 0:
        return None
    return (mean(a) - mean(b)) / sd_ref


def cohens_d_ci(d, n1, n2, conf: float = 0.95):
    if d is None:
        return None
    z = normal_ppf(1.0 - (1.0 - conf) / 2.0)
    se = sqrt((n1 + n2) / (n1 * n2) + d * d / (2.0 * (n1 + n2)))
    return {"lower": d - z * se, "upper": d + z * se, "conf": conf}


def cramers_v(chi2, n, r, c):
    if n <= 0 or r < 2 or c < 2:
        return None
    denom = n * min(r - 1, c - 1)
    return sqrt(chi2 / denom) if denom > 0 else None


# --------------------------------------------------------------------------- #
# Confidence intervals
# --------------------------------------------------------------------------- #
def mean_ci(x, conf: float = 0.95):
    x = [v for v in x if v is not None]
    n = len(x)
    if n < 2:
        return None
    m = mean(x)
    sd = stdev(x)
    if sd is None:
        return None
    tc = t_ppf(1.0 - (1.0 - conf) / 2.0, n - 1)
    half = tc * sd / sqrt(n)
    return {"estimate": m, "lower": m - half, "upper": m + half,
            "conf": conf, "method": "t-interval for mean"}


def welch_diff_ci(a, b, conf: float = 0.95):
    a = [v for v in a if v is not None]
    b = [v for v in b if v is not None]
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return None
    m1, m2 = mean(a), mean(b)
    v1, v2 = variance(a), variance(b)
    if v1 <= 0 or v2 <= 0:
        return None
    se = sqrt(v1 / n1 + v2 / n2)
    num = (v1 / n1 + v2 / n2) ** 2
    den = (v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1)
    df = num / den if den > 0 else float("inf")
    tc = t_ppf(1.0 - (1.0 - conf) / 2.0, df)
    half = tc * se
    diff = m1 - m2
    return {"estimate": diff, "lower": diff - half, "upper": diff + half,
            "df": df, "conf": conf, "method": "Welch interval for mean difference"}


def wilson_ci(k, n, conf: float = 0.95):
    if n <= 0:
        return None
    z = normal_ppf(1.0 - (1.0 - conf) / 2.0)
    phat = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (phat + z2 / (2.0 * n)) / denom
    half = z * sqrt((phat * (1.0 - phat) / n) + z2 / (4.0 * n * n)) / denom
    return {"estimate": phat, "lower": center - half, "upper": center + half,
            "conf": conf, "method": "Wilson score interval"}


def pearson_r(x, y):
    pairs = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]
    n = len(pairs)
    if n < 3:
        return None
    mx = sum(a for a, _ in pairs) / n
    my = sum(b for _, b in pairs) / n
    sxy = sum((a - mx) * (b - my) for a, b in pairs)
    sxx = sum((a - mx) ** 2 for a, _ in pairs)
    syy = sum((b - my) ** 2 for _, b in pairs)
    if sxx <= 0 or syy <= 0:
        return None
    r = sxy / sqrt(sxx * syy)
    r = max(-1.0, min(1.0, r))
    t = r * sqrt((n - 2) / (1.0 - r * r)) if abs(r) < 1.0 else (INF if r > 0 else -INF)
    p = t_two_sided(t, n - 2) if abs(r) < 1.0 else 0.0
    return {"r": r, "n": n, "t": t, "df": n - 2, "p": p,
            "method": "Pearson product-moment correlation"}


def spearman_r(x, y):
    pairs = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]
    n = len(pairs)
    if n < 3:
        return None
    xs = _average_ranks([a for a, _ in pairs])
    ys = _average_ranks([b for _, b in pairs])
    r = pearson_r(xs, ys)
    if r is None:
        return None
    r["method"] = "Spearman rank correlation"
    return r


def fisher_z_ci(r, n, conf: float = 0.95):
    if r is None or n < 4 or abs(r) >= 1.0:
        return None
    z = normal_ppf(1.0 - (1.0 - conf) / 2.0)
    zr = atanh(r)
    se = 1.0 / sqrt(n - 3)
    return {"estimate": r, "lower": math.tanh(zr - z * se),
            "upper": math.tanh(zr + z * se), "conf": conf,
            "method": "Fisher z interval for correlation"}


# --------------------------------------------------------------------------- #
# Multiple-comparison correction
# --------------------------------------------------------------------------- #
def bonferroni(pvalues):
    """Bonferroni-adjusted p-values (cap at 1.0)."""
    m = len(pvalues)
    if m == 0:
        return []
    return [min(1.0, p * m) for p in pvalues]


def bh_fdr(pvalues):
    """Benjamini-Hochberg FDR-adjusted p-values."""
    m = len(pvalues)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvalues[i])
    adj = [1.0] * m
    prev = 1.0
    for rank in range(m - 1, -1, -1):
        idx = order[rank]
        q = pvalues[idx] * m / (rank + 1)
        q = min(prev, q)
        q = min(1.0, q)
        adj[idx] = q
        prev = q
    return adj


def fmt_p(p) -> str:
    if p is None:
        return "NA"
    if p < 1e-6:
        return "p<0.000001"
    return "p=%.6g" % p


# --------------------------------------------------------------------------- #
# Self-test (run:  python scripts/statlib.py)
# --------------------------------------------------------------------------- #
def _selftest():
    import json
    checks = []
    # normal symmetry
    checks.append(("normal_cdf(0)", abs(normal_cdf(0.0) - 0.5) < 1e-12))
    checks.append(("normal_ppf(0.975)", abs(normal_ppf(0.975) - 1.959963984540054) < 1e-6))
    # t / F / chi2 spot values (validated against standard tables)
    checks.append(("t_sf(2, 10)", abs(t_sf(2.0, 10) - 0.03669402) < 1e-6))
    checks.append(("f_sf(3.354131, 2, 27)", abs(f_sf(3.354131, 2, 27) - 0.05) < 1e-6))
    checks.append(("chi2_sf(6, 3)", abs(chi2_sf(6.0, 3) - 0.1116102) < 1e-5))
    # descriptive
    d = describe([1, 2, 3, 4, 5])
    checks.append(("mean", abs(d["mean"] - 3.0) < 1e-12))
    checks.append(("median", abs(d["median"] - 3.0) < 1e-12))
    checks.append(("q1/q3", abs(d["q1"] - 2.0) < 1e-12 and abs(d["q3"] - 4.0) < 1e-12))
    # shapiro on symmetric data is defined
    sw = shapiro_wilk([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    checks.append(("shapiro returns", sw is not None and 0.0 <= sw["W"] <= 1.0))
    # ANOVA on clearly separated groups -> small p
    av = anova([[1, 2, 3, 4], [10, 11, 12, 13], [20, 21, 22, 23]])
    checks.append(("anova p small", av is not None and av["p"] < 0.001))
    # identical groups -> p ~ 1
    av2 = anova([[1, 2, 3], [1, 2, 3], [1, 2, 3]])
    checks.append(("anova identical", av2 is not None and av2["p"] == 1.0))
    # welch CI contains mean diff
    ci = welch_diff_ci([1, 2, 3, 4], [2, 3, 4, 5])
    checks.append(("welch ci", ci is not None and ci["lower"] <= ci["estimate"] <= ci["upper"]))
    # bh monotone
    adj = bh_fdr([0.001, 0.01, 0.5])
    checks.append(("bh", adj[0] <= adj[1] <= adj[2]))
    # determinism: run twice
    sw_a = json.dumps(shapiro_wilk([3.1, 4.2, 5.0, 5.1, 6.0, 7.2]))
    sw_b = json.dumps(shapiro_wilk([3.1, 4.2, 5.0, 5.1, 6.0, 7.2]))
    checks.append(("determinism", sw_a == sw_b))

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(("PASS" if ok else "FAIL"), name)
    if failed:
        print("statlib selftest FAILED:", failed)
        return 1
    print("statlib selftest OK (%d checks)" % len(checks))
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
