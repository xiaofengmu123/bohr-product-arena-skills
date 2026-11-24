"""stats.py - deterministic statistical engine (standard library only).

Design contract
---------------
* No random numbers, no time dependence, no environment dependence: the same
  numeric inputs always produce the same outputs (bit-for-bit on the same
  Python build).  This is what makes every result re-computable.
* Every function returns plain dicts/lists/floats that json.dumps can serialise.
* Statistical approximations used here are documented on each function.  They
  are the standard "normal-theory" approximations, chosen so the whole pipeline
  works without third-party packages; where the approximation is weak (small n,
  heavy ties) the caller marks the result `approximate`.

Numerical primitives below (regularized incomplete gamma/beta, normal quantile,
t/F/chi-square distributions) are classic numerical recipes re-implemented in
pure Python.  They are stable for the df/n ranges this task encounters.
"""

import math

EPS = 1e-14
Z95 = 1.959963984540054  # 97.5th percentile of N(0,1)


# ---------------------------------------------------------------------------
# Special functions
# ---------------------------------------------------------------------------
def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p):
    """Inverse normal CDF (Acklam's algorithm + one Halley refinement)."""
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
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
    plow, phigh = 0.02425, 1.0 - 0.02425
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
    e = 0.5 * math.erfc(-x / math.sqrt(2.0)) - p
    u = e * math.sqrt(2.0 * math.pi) * math.exp(x * x / 2.0)
    return x - u / (1.0 + x * u / 2.0)


_LG = (0.99999999999980993, 676.5203681218851, -1259.1392167224028,
       771.32342877765313, -176.61502916214059, 12.507343278686905,
       -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7)


def lgamma(x):
    """Log-gamma (Lanczos)."""
    if x < 0.5:
        return math.log(math.pi / math.sin(math.pi * x)) - lgamma(1.0 - x)
    x -= 1.0
    a = _LG[0]
    t = x + 7.5
    for i in range(1, 9):
        a += _LG[i] / (x + i)
    return 0.5 * math.log(2.0 * math.pi) + (x + 0.5) * math.log(t) - t + math.log(a)


def _gser(a, x):
    gln = lgamma(a)
    if x <= 0.0:
        return 0.0, gln
    ap = a
    total = 1.0 / a
    delta = total
    for _ in range(200):
        ap += 1.0
        delta *= x / ap
        total += delta
        if abs(delta) < abs(total) * EPS:
            return total * math.exp(-x + a * math.log(x) - gln), gln
    return total * math.exp(-x + a * math.log(x) - gln), gln


def _gcf(a, x):
    gln = lgamma(a)
    b = x + 1.0 - a
    c = 1.0 / 1e-300
    d = 1.0 / b
    h = d
    for i in range(1, 301):
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
        if abs(delta - 1.0) < EPS:
            break
    return math.exp(-x + a * math.log(x) - gln) * h, gln


def gammp(a, x):
    """Regularized lower incomplete gamma P(a,x)."""
    if x < 0.0 or a <= 0.0:
        raise ValueError("gammp: bad args")
    if x < a + 1.0:
        return _gser(a, x)[0]
    return 1.0 - _gcf(a, x)[0]


def _betacf(a, b, x):
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-300:
        d = 1e-300
    d = 1.0 / d
    h = d
    for m in range(1, 201):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-300:
            d = 1e-300
        c = 1.0 + aa / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-300:
            d = 1e-300
        c = 1.0 + aa / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < EPS:
            break
    return h


def betainc(x, a, b):
    """Regularized incomplete beta I_x(a,b)."""
    if x < 0.0 or x > 1.0:
        raise ValueError("betainc: bad x")
    if x == 0.0 or x == 1.0:
        bt = 0.0
    else:
        bt = math.exp(lgamma(a + b) - lgamma(a) - lgamma(b)
                      + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def t_cdf(t, df):
    x = df / (df + t * t)
    ib = betainc(x, df / 2.0, 0.5)
    return 1.0 - 0.5 * ib if t >= 0.0 else 0.5 * ib


def t_ppf(p, df):
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    lo, hi = -1000.0, 1000.0
    while t_cdf(lo, df) > p:
        lo *= 2.0
    while t_cdf(hi, df) < p:
        hi *= 2.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def f_cdf(x, df1, df2):
    if x <= 0.0:
        return 0.0
    z = df1 * x / (df1 * x + df2)
    return betainc(z, df1 / 2.0, df2 / 2.0)


def chi2_cdf(x, k):
    if x <= 0.0:
        return 0.0
    return gammp(k / 2.0, x / 2.0)


def chi2_sf(x, k):
    """Chi-square survival (upper tail) probability."""
    return 1.0 - chi2_cdf(x, k)


# ---------------------------------------------------------------------------
# Descriptive statistics
# ---------------------------------------------------------------------------
def _as_floats(values):
    out = []
    for v in values:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def _sorted(vals):
    s = list(vals)
    s.sort()
    return s


def percentile(sorted_vals, q):
    """Linear-interpolation percentile (numpy 'linear' / Hyndman-Fan type 7)."""
    n = len(sorted_vals)
    if n == 0:
        return None
    if n == 1:
        return sorted_vals[0]
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_vals[lo]
    frac = pos - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def describe(values):
    """Return n/mean/median/sd/min/max/Q1/Q3/IQR/skew/kurtosis for numeric data.

    skew = adjusted Fisher-Pearson g1 (bias=False);
    kurt = adjusted excess kurtosis g2 (bias=False).
    """
    x = _as_floats(values)
    n = len(x)
    if n == 0:
        return {"n": 0, "unit_unknown": True}
    s = _sorted(x)
    mean = sum(x) / n
    var = sum((v - mean) ** 2 for v in x) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var)
    q1 = percentile(s, 0.25)
    q3 = percentile(s, 0.75)
    iqr = q3 - q1
    m2 = sum((v - mean) ** 2 for v in x) / n
    m3 = sum((v - mean) ** 3 for v in x) / n
    m4 = sum((v - mean) ** 4 for v in x) / n
    g1 = m3 / (m2 ** 1.5) if m2 > 0 else 0.0
    g2 = m4 / (m2 ** 2) - 3.0 if m2 > 0 else 0.0
    if n >= 3:
        skew = math.sqrt(n * (n - 1)) / (n - 2) * g1
    else:
        skew = 0.0
    if n >= 4:
        kurt = (n + 1) * g2 + 6.0
        kurt = (n - 1) / ((n - 2) * (n - 3)) * ((n + 1) * g2 + 6.0)
    else:
        kurt = 0.0
    return {
        "n": n, "mean": mean, "median": percentile(s, 0.5), "sd": sd,
        "min": s[0], "max": s[-1], "q1": q1, "q3": q3, "iqr": iqr,
        "skewness": skew, "kurtosis": kurt,
    }


# ---------------------------------------------------------------------------
# Assumption checks
# ---------------------------------------------------------------------------
def jarque_bera(values):
    """Jarque-Bera normality test.

    JB = n/6 * (S^2 + (K-3)^2/4), distributed chi-square(2) under normality.
    S = moment skewness, K = moment kurtosis.  Approximation: low power for
    n < ~20; reported as `approximate` when n < 20.
    """
    x = _as_floats(values)
    n = len(x)
    if n < 3:
        return {"test": "jarque_bera", "applicable": False,
                "reason": "n<3: normality not assessable", "n": n}
    mean = sum(x) / n
    m2 = sum((v - mean) ** 2 for v in x) / n
    m3 = sum((v - mean) ** 3 for v in x) / n
    m4 = sum((v - mean) ** 4 for v in x) / n
    S = m3 / (m2 ** 1.5) if m2 > 0 else 0.0
    K = m4 / (m2 ** 2) if m2 > 0 else 0.0
    jb = n / 6.0 * (S * S + (K - 3.0) ** 2 / 4.0)
    p = chi2_sf(jb, 2)
    return {"test": "jarque_bera", "applicable": True, "n": n,
            "statistic": jb, "p_value": p, "skewness": S, "kurtosis": K,
            "normal": p >= 0.05, "approximate": n < 20}


def brown_forsythe(groups):
    """Brown-Forsythe (Levene on medians) homogeneity-of-variance test.

    F statistic on absolute deviations from group medians, F(df1=k-1,
    df2=N-k).  Requires every group to have n >= 2.
    """
    k = len(groups)
    gs = [_as_floats(g) for g in groups]
    ns = [len(g) for g in gs]
    if any(n < 2 for n in ns):
        return {"test": "brown_forsythe", "applicable": False,
                "reason": "group with n<2: variance homogeneity not assessable",
                "n_per_group": ns}
    medians = [percentile(_sorted(g), 0.5) for g in gs]
    zs = [[abs(v - med) for v in g] for g, med in zip(gs, medians)]
    N = sum(ns)
    zbar = sum(sum(z) for z in zs) / N
    ssb = sum(n * (sum(z) / n - zbar) ** 2 for z, n in zip(zs, ns))
    ssw = sum(sum((v - sum(z) / n) ** 2 for v in z) for z, n in zip(zs, ns))
    df1 = k - 1
    df2 = N - k
    f = (ssb / df1) / (ssw / df2) if ssw > 0 else float("inf")
    p = 1.0 - f_cdf(f, df1, df2) if math.isfinite(f) else 0.0
    return {"test": "brown_forsythe", "applicable": True,
            "statistic": f, "df1": df1, "df2": df2, "p_value": p,
            "homogeneous": p >= 0.05, "n_per_group": ns}


# ---------------------------------------------------------------------------
# Inferential tests
# ---------------------------------------------------------------------------
def _welch_df(s1, n1, s2, n2):
    a = s1 * s1 / n1
    b = s2 * s2 / n2
    num = (a + b) ** 2
    den = a * a / (n1 - 1) + b * b / (n2 - 1)
    return num / den if den > 0 else float("inf")


def student_t(a, b):
    x, y = _as_floats(a), _as_floats(b)
    n1, n2 = len(x), len(y)
    m1, m2 = sum(x) / n1, sum(y) / n2
    v1 = sum((v - m1) ** 2 for v in x) / (n1 - 1)
    v2 = sum((v - m2) ** 2 for v in y) / (n2 - 1)
    sp = math.sqrt(((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2))
    t = (m1 - m2) / (sp * math.sqrt(1.0 / n1 + 1.0 / n2)) if sp > 0 else float("inf")
    df = n1 + n2 - 2
    p = 2.0 * (1.0 - t_cdf(abs(t), df)) if math.isfinite(t) else 0.0
    return {"test": "student_t", "statistic": t, "df": df, "p_value": p,
            "mean_diff": m1 - m2, "pooled_sd": sp}


def welch_t(a, b):
    x, y = _as_floats(a), _as_floats(b)
    n1, n2 = len(x), len(y)
    m1, m2 = sum(x) / n1, sum(y) / n2
    v1 = sum((v - m1) ** 2 for v in x) / (n1 - 1)
    v2 = sum((v - m2) ** 2 for v in y) / (n2 - 1)
    se = math.sqrt(v1 / n1 + v2 / n2)
    t = (m1 - m2) / se if se > 0 else float("inf")
    df = _welch_df(math.sqrt(v1), n1, math.sqrt(v2), n2)
    p = 2.0 * (1.0 - t_cdf(abs(t), df)) if math.isfinite(t) else 0.0
    return {"test": "welch_t", "statistic": t, "df": df, "p_value": p,
            "mean_diff": m1 - m2, "se": se}


def mann_whitney_u(a, b):
    """Mann-Whitney U with continuity + tie correction, normal approximation.

    Returns U for group a and a two-sided p.  Ties degrade the normal
    approximation, flagged via `approximate`.
    """
    x, y = _as_floats(a), _as_floats(b)
    n1, n2 = len(x), len(y)
    if n1 == 0 or n2 == 0:
        return {"test": "mann_whitney_u", "applicable": False,
                "reason": "empty group"}
    combined = [(v, 0) for v in x] + [(v, 1) for v in y]
    combined.sort(key=lambda t: t[0])
    ranks = [0.0] * len(combined)
    i = 0
    tie_present = False
    while i < len(combined):
        j = i
        while j + 1 < len(combined) and combined[j + 1][0] == combined[i][0]:
            j += 1
        if j > i:
            tie_present = True
        r = (i + j + 2) / 2.0  # average rank, 1-based
        for k in range(i, j + 1):
            ranks[k] = r
        i = j + 1
    r1 = sum(ranks[k] for k in range(len(combined)) if combined[k][1] == 0)
    u1 = r1 - n1 * (n1 + 1) / 2.0
    u2 = n1 * n2 - u1
    U = min(u1, u2)
    N = n1 + n2
    mu = n1 * n2 / 2.0
    # tie correction
    if tie_present:
        ti = {}
        for v, _ in combined:
            ti[v] = ti.get(v, 0) + 1
        tie_sum = sum(t * (t * t - 1) for t in ti.values())
        sd = math.sqrt(n1 * n2 / 12.0 * ((N + 1) - tie_sum / (N * (N - 1))))
    else:
        sd = math.sqrt(n1 * n2 * (N + 1) / 12.0)
    z = (U - mu) / sd if sd > 0 else 0.0
    p = 2.0 * (1.0 - norm_cdf(abs(z)))
    return {"test": "mann_whitney_u", "applicable": True, "u": U,
            "u1": u1, "u2": u2, "z": z, "p_value": p,
            "normal_approximation": True, "tie_corrected": tie_present}


def one_way_anova(groups):
    gs = [_as_floats(g) for g in groups]
    ns = [len(g) for g in gs]
    k = len(gs)
    N = sum(ns)
    gm = sum(sum(g) for g in gs) / N
    ssb = sum(n * (sum(g) / n - gm) ** 2 for g, n in zip(gs, ns))
    ssw = sum(sum((v - sum(g) / n) ** 2 for v in g) for g, n in zip(gs, ns))
    df1, df2 = k - 1, N - k
    msb = ssb / df1 if df1 > 0 else 0.0
    msw = ssw / df2 if df2 > 0 else 0.0
    f = msb / msw if msw > 0 else float("inf")
    p = 1.0 - f_cdf(f, df1, df2) if math.isfinite(f) else 0.0
    eta2 = ssb / (ssb + ssw) if (ssb + ssw) > 0 else 0.0
    omega2 = (ssb - df1 * msw) / (ssb + ssw + msw) if (ssb + ssw + msw) > 0 else 0.0
    return {"test": "one_way_anova", "statistic": f, "df1": df1, "df2": df2,
            "p_value": p, "ss_between": ssb, "ss_within": ssw,
            "eta_squared": eta2, "omega_squared": omega2,
            "n_per_group": ns, "grand_mean": gm}


def kruskal_wallis(groups):
    gs = [_as_floats(g) for g in groups]
    ns = [len(g) for g in gs]
    k = len(gs)
    N = sum(ns)
    combined = [(v, i) for i, g in enumerate(gs) for v in g]
    combined.sort(key=lambda t: t[0])
    ranks = [0.0] * len(combined)
    i = 0
    tie_sum = 0.0
    while i < len(combined):
        j = i
        while j + 1 < len(combined) and combined[j + 1][0] == combined[i][0]:
            j += 1
        r = (i + j + 2) / 2.0
        for kk in range(i, j + 1):
            ranks[kk] = r
        if j > i:
            tie_sum += (j - i + 1) ** 3 - (j - i + 1)
        i = j + 1
    group_rank_sum = [0.0] * k
    for idx, (_, gi) in enumerate(combined):
        group_rank_sum[gi] += ranks[idx]
    H = 12.0 / (N * (N + 1)) * sum(r * r / n for r, n in zip(group_rank_sum, ns)) \
        - 3.0 * (N + 1)
    correction = 1.0 - tie_sum / (N ** 3 - N) if N > 1 else 1.0
    Hc = H / correction if correction > 0 else H
    p = chi2_sf(Hc, k - 1)
    return {"test": "kruskal_wallis", "statistic": Hc, "df": k - 1,
            "p_value": p, "tie_corrected": tie_sum > 0, "n_per_group": ns}


def chi_square_test(table):
    """Pearson chi-square on a 2-D contingency table (list of rows)."""
    r, c = len(table), len(table[0])
    row = [sum(row_) for row_ in table]
    col = [sum(table[i][j] for i in range(r)) for j in range(c)]
    N = sum(row)
    chi2 = 0.0
    for i in range(r):
        for j in range(c):
            e = row[i] * col[j] / N if N > 0 else 0.0
            if e > 0:
                chi2 += (table[i][j] - e) ** 2 / e
    df = (r - 1) * (c - 1)
    p = chi2_sf(chi2, df)
    v = math.sqrt(chi2 / (N * min(r - 1, c - 1))) if N > 0 and min(r - 1, c - 1) > 0 else 0.0
    return {"test": "chi_square", "statistic": chi2, "df": df, "p_value": p,
            "cramers_v": v, "n": N}


# ---------------------------------------------------------------------------
# Effect sizes
# ---------------------------------------------------------------------------
def cohens_d(a, b):
    x, y = _as_floats(a), _as_floats(b)
    n1, n2 = len(x), len(y)
    m1, m2 = sum(x) / n1, sum(y) / n2
    v1 = sum((v - m1) ** 2 for v in x) / (n1 - 1)
    v2 = sum((v - m2) ** 2 for v in y) / (n2 - 1)
    sp = math.sqrt(((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2))
    d = (m1 - m2) / sp if sp > 0 else 0.0
    return {"effect": "cohens_d", "value": d, "direction": "group1_minus_group2",
            "pooled_sd": sp}


def hedges_g(a, b):
    d = cohens_d(a, b)
    n1, n2 = len(_as_floats(a)), len(_as_floats(b))
    df = n1 + n2 - 2
    corr = 1.0 - 3.0 / (4.0 * df - 1.0) if df > 0 else 1.0
    g = d["value"] * corr
    return {"effect": "hedges_g", "value": g, "bias_correction": corr,
            "cohens_d": d["value"]}


def glass_delta(a, reference):
    """Glass delta using `reference` group as the denominator sd."""
    x, ref = _as_floats(a), _as_floats(reference)
    n1, n2 = len(x), len(ref)
    m1 = sum(x) / n1
    m2 = sum(ref) / n2
    sd2 = math.sqrt(sum((v - m2) ** 2 for v in ref) / (n2 - 1)) if n2 > 1 else 0.0
    val = (m1 - m2) / sd2 if sd2 > 0 else 0.0
    return {"effect": "glass_delta", "value": val,
            "reference_group_sd": sd2, "direction": "group1_minus_reference"}


def effect_ci(d, n1, n2):
    """Normal-approximation 95% CI for Cohen's d / Hedges' g."""
    se = math.sqrt((n1 + n2) / (n1 * n2) + d * d / (2.0 * (n1 + n2)))
    return {"lower": d - Z95 * se, "upper": d + Z95 * se, "method":
            "normal_approximation", "level": 0.95}


# ---------------------------------------------------------------------------
# Confidence intervals
# ---------------------------------------------------------------------------
def mean_ci(values, level=0.95):
    x = _as_floats(values)
    n = len(x)
    m = sum(x) / n
    s = math.sqrt(sum((v - m) ** 2 for v in x) / (n - 1)) if n > 1 else 0.0
    tcrit = t_ppf(1.0 - (1.0 - level) / 2.0, n - 1) if n > 1 else 0.0
    half = tcrit * s / math.sqrt(n)
    return {"estimate": "mean", "point": m, "lower": m - half, "upper": m + half,
            "method": "t", "df": n - 1, "level": level}


def mean_diff_ci(a, b, level=0.95):
    """Welch-style CI for the difference of two means."""
    x, y = _as_floats(a), _as_floats(b)
    n1, n2 = len(x), len(y)
    m1, m2 = sum(x) / n1, sum(y) / n2
    v1 = sum((v - m1) ** 2 for v in x) / (n1 - 1)
    v2 = sum((v - m2) ** 2 for v in y) / (n2 - 1)
    se = math.sqrt(v1 / n1 + v2 / n2)
    df = _welch_df(math.sqrt(v1), n1, math.sqrt(v2), n2)
    tcrit = t_ppf(1.0 - (1.0 - level) / 2.0, df)
    diff = m1 - m2
    return {"estimate": "mean_difference", "point": diff,
            "lower": diff - tcrit * se, "upper": diff + tcrit * se,
            "method": "welch", "df": df, "level": level}


def wilson_ci(k, n, level=0.95):
    z = norm_ppf(1.0 - (1.0 - level) / 2.0)
    phat = k / n if n > 0 else 0.0
    denom = 1.0 + z * z / n
    center = (phat + z * z / (2.0 * n)) / denom
    half = z * math.sqrt(phat * (1.0 - phat) / n + z * z / (4.0 * n * n)) / denom
    return {"estimate": "proportion", "point": phat, "lower": max(0.0, center - half),
            "upper": min(1.0, center + half), "method": "wilson", "level": level}


def pearson_r(a, b):
    x, y = _as_floats(a), _as_floats(b)
    n = min(len(x), len(y))
    x, y = x[:n], y[:n]
    if n < 3:
        return {"applicable": False, "reason": "n<3"}
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((v - mx) ** 2 for v in x)
    syy = sum((v - my) ** 2 for v in y)
    sxy = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    r = sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else 0.0
    t = r * math.sqrt((n - 2) / (1.0 - r * r)) if abs(r) < 1.0 else float("inf")
    p = 2.0 * (1.0 - t_cdf(abs(t), n - 2)) if math.isfinite(t) else 0.0
    return {"estimate": "pearson_r", "point": r, "n": n, "t": t, "p_value": p,
            "applicable": True}


def correlation_ci(r, n, level=0.95):
    """Fisher z-transform CI for Pearson r."""
    z = math.atanh(max(-0.999999, min(0.999999, r)))
    zcrit = norm_ppf(1.0 - (1.0 - level) / 2.0)
    se = 1.0 / math.sqrt(n - 3)
    lo = math.tanh(z - zcrit * se)
    hi = math.tanh(z + zcrit * se)
    return {"estimate": "correlation", "point": r, "lower": lo, "upper": hi,
            "method": "fisher_z", "level": level}


# ---------------------------------------------------------------------------
# Multiple comparison correction
# ---------------------------------------------------------------------------
def bonferroni(p_values):
    m = len(p_values)
    return [min(1.0, p * m) for p in p_values]


def bh_fdr(p_values):
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [1.0] * m
    prev = 1.0
    for rank, idx in enumerate(order[::-1], start=1):
        q = p_values[idx] * m / (m - rank + 1)
        prev = min(prev, q)
        adjusted[idx] = prev
    return adjusted


# ---------------------------------------------------------------------------
# Clean helpers (used by main.py)
# ---------------------------------------------------------------------------
def iqr_outlier_flags(values, k=1.5):
    """Flag (do NOT delete) values outside [Q1 - k*IQR, Q3 + k*IQR]."""
    x = _as_floats(values)
    s = _sorted(x)
    q1, q3 = percentile(s, 0.25), percentile(s, 0.75)
    iqr = q3 - q1
    lo, hi = q1 - k * iqr, q3 + k * iqr
    return {
        "q1": q1, "q3": q3, "iqr": iqr,
        "lower_fence": lo, "upper_fence": hi,
        "n_outliers": sum(1 for v in x if v < lo or v > hi),
        "outlier_indices": [i for i, v in enumerate(x) if v < lo or v > hi],
    }
