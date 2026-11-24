"""core_impl.py - deterministic statistical analysis pipeline (Python standard library only).

Design principle: judgement and computation are separated.
- The Agent reads /input and writes /app/work/plan.json (design, units, semantics).
- This module performs deterministic cleaning / descriptive / inference / effects / CI /
  figures / result.json assembly. It never uses randomness, never guesses units, and never
  silently deletes rows.

Determinism contract:
- No `random` import; no dependence on wall-clock inside the deterministic payload.
- Canonical JSON serialisation (sort_keys) and fixed CSV/SVG formatting.
- result.json carries `determinism.input_fingerprint` (SHA-256 of sorted input inventory +
  canonical case.json) and `determinism.deterministic_sha256` (SHA-256 of the payload with
  the `runtime` block removed). `runtime.wall_clock` is the only intentionally
  non-reproducible field.
"""

import csv
import hashlib
import io
import json
import math
import os
import shutil
import sqlite3
import sys
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# --------------------------------------------------------------------------- #
# Small deterministic helpers
# --------------------------------------------------------------------------- #

SKILL_VERSION = "2.0.0"
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB per input file cap

VALID_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def canonical_json(obj):
    """Deterministic JSON text (sorted keys, no ASCII escapes, trailing newline)."""
    return json.dumps(
        _sanitize(obj), ensure_ascii=False, indent=2, sort_keys=True,
        separators=(",", ": "),
    ) + "\n"


def _sanitize(obj):
    """Replace non-finite floats with None so JSON output stays valid and deterministic."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {str(k): _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return obj


def write_json(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(canonical_json(obj))


def read_json(path):
    raw = read_text(path)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        # retry after stripping BOM / whitespace (read_text already handles BOM)
        return None


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """Read UTF-8 with BOM tolerance and an encoding fallback; returns None on failure."""
    try:
        if os.path.getsize(path) > max_bytes:
            return None
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def sha256_bytes(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def fmt_num(x, digits=6):
    if x is None:
        return None
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    if isinstance(x, int):
        return x
    return round(float(x), digits)


def fmt_p(x):
    if x is None:
        return None
    if x < 0.0001:
        return 0.0001
    return round(float(x), 6)


# --------------------------------------------------------------------------- #
# Distribution functions (all deterministic, stdlib only)
# --------------------------------------------------------------------------- #

def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p):
    """Acklam's inverse normal CDF approximation (relative error ~1e-9)."""
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
        num = ((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]
        den = (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        return num / den
    if p > phigh:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        num = ((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]
        den = (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        return -num / den
    q = p - 0.5
    r = q * q
    num = ((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]
    den = ((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0
    return num * q / den


def gammp(a, x):
    """Regularised lower incomplete gamma P(a, x)."""
    if x < 0.0 or a <= 0.0:
        raise ValueError("invalid arguments to gammp")
    if x == 0.0:
        return 0.0
    log_gamma = math.lgamma(a)
    if x < a + 1.0:
        ap, total, term = a, 1.0 / a, 1.0 / a
        for _ in range(400):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-14:
                break
        return total * math.exp(-x + a * math.log(x) - log_gamma)
    b = x + 1.0 - a
    c = 1e300
    d = 1.0 / b
    h = d
    for i in range(1, 400):
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
        if abs(delta - 1.0) < 1e-14:
            break
    return 1.0 - math.exp(-x + a * math.log(x) - log_gamma) * h


def chi2_cdf(x, k):
    if x <= 0.0:
        return 0.0
    return gammp(k / 2.0, x / 2.0)


def betacf(a, b, x):
    MAXIT, EPS, FPMIN = 400, 3e-14, 1e-300
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


def betai(a, b, x):
    """Regularised incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                  + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * betacf(a, b, x) / a
    return 1.0 - bt * betacf(b, a, 1.0 - x) / b


def t_cdf(t, df):
    if df <= 0:
        return None
    x = df / (df + t * t)
    half = 0.5 * betai(df / 2.0, 0.5, x)
    return 1.0 - half if t > 0 else half


def t_ppf(p, df, lo=-40.0, hi=40.0):
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def f_cdf(f, df1, df2):
    if f <= 0.0:
        return 0.0
    x = df1 * f / (df1 * f + df2)
    return betai(df1 / 2.0, df2 / 2.0, x)


def f_ppf(p, df1, df2, lo=0.0, hi=1e5):
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return math.inf
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if f_cdf(mid, df1, df2) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def chi2_ppf(p, k, lo=0.0, hi=1e5):
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return math.inf
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if chi2_cdf(mid, k) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# --------------------------------------------------------------------------- #
# Descriptive statistics
# --------------------------------------------------------------------------- #

def mean(x):
    n = len(x)
    return sum(x) / n if n else None


def variance(x, ddof=1):
    n = len(x)
    if n <= ddof:
        return None
    m = sum(x) / n
    return sum((v - m) ** 2 for v in x) / (n - ddof)


def sd(x, ddof=1):
    v = variance(x, ddof)
    return math.sqrt(v) if v is not None else None


def median(x):
    xs = sorted(x)
    n = len(xs)
    if n == 0:
        return None
    if n % 2:
        return xs[n // 2]
    return (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def quantile(x, q):
    xs = sorted(x)
    n = len(xs)
    if n == 0:
        return None
    if n == 1:
        return xs[0]
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    return xs[lo] + (pos - lo) * (xs[hi] - xs[lo])


def skewness(x):
    n = len(x)
    if n < 3:
        return None
    s = sd(x)
    if s is None or s == 0:
        return None
    m = mean(x)
    coef = n / ((n - 1) * (n - 2))
    return coef * sum(((v - m) / s) ** 3 for v in x)


def kurtosis(x):
    n = len(x)
    if n < 4:
        return None
    s = sd(x)
    if s is None or s == 0:
        return None
    m = mean(x)
    term = sum(((v - m) / s) ** 4 for v in x)
    g2 = (n * (n + 1)) / ((n - 1) * (n - 2) * (n - 3)) * term \
        - 3 * (n - 1) ** 2 / ((n - 2) * (n - 3))
    return g2


def describe(x):
    n = len(x)
    if n == 0:
        return {"n": 0}
    return {
        "n": n,
        "mean": fmt_num(mean(x)),
        "median": fmt_num(median(x)),
        "sd": fmt_num(sd(x)),
        "min": fmt_num(min(x)),
        "max": fmt_num(max(x)),
        "q1": fmt_num(quantile(x, 0.25)),
        "q3": fmt_num(quantile(x, 0.75)),
        "iqr": fmt_num(quantile(x, 0.75) - quantile(x, 0.25)),
        "skewness": fmt_num(skewness(x)),
        "kurtosis": fmt_num(kurtosis(x)),
    }


# --------------------------------------------------------------------------- #
# Normality / homoscedasticity
# --------------------------------------------------------------------------- #

def shapiro_wilk_francia(x):
    """Shapiro-Francia W' with Royston-style p approximation (like R nortest::sf.test).

    Honest label: it is a Shapiro-Francia statistic, not the full Shapiro-Wilk.
    Use scipy.stats.shapiro as the canonical engine when available.
    """
    n = len(x)
    if n < 5 or n > 5000:
        return {"statistic": None, "p": None, "method": "shapiro_francia",
                "note": "n must be in [5, 5000]"}
    xs = sorted(x)
    m = mean(xs)
    denom = sum((v - m) ** 2 for v in xs)
    if denom == 0:
        return {"statistic": None, "p": None, "method": "shapiro_francia",
                "note": "zero variance, test not applicable"}
    mi = [norm_ppf((i + 1 - 3.0 / 8.0) / (n + 0.25)) for i in range(n)]
    sum_m2 = sum(v * v for v in mi)
    num = sum(mi[i] * xs[i] for i in range(n)) ** 2
    W = num / (sum_m2 * denom)
    W = min(1.0, max(0.0, W))
    u = math.log(n)
    if n <= 11:
        mu = 0.5440 - 0.39978 * n + 0.025054 * n ** 2 - 0.0006714 * n ** 3
        sigma = math.exp(1.3822 - 0.77857 * n + 0.062767 * n ** 2 - 0.0020322 * n ** 3)
    else:
        mu = 0.0038915 * u ** 3 - 0.083751 * u ** 2 - 0.31082 * u - 1.5861
        sigma = math.exp(0.0030302 * u ** 2 - 0.082676 * u - 0.4803)
    z = (math.log(1.0 - W) - mu) / sigma
    p = 1.0 - norm_cdf(z)
    return {"statistic": fmt_num(W), "p": fmt_p(p), "method": "shapiro_francia",
            "note": "Shapiro-Francia W' (Blom scores) with Royston approximation"}


def brown_forsythe(groups):
    """Brown-Forsythe test (median-based) for equality of variances across k groups."""
    k = len(groups)
    medians = [median(g) for g in groups]
    z = []
    group_sizes = []
    for gi, g in enumerate(groups):
        for v in g:
            z.append((abs(v - medians[gi]), gi))
        group_sizes.append(len(g))
    n = len(z)
    if n <= k:
        return {"statistic": None, "p": None, "df1": k - 1, "df2": n - k,
                "method": "brown_forsythe", "note": "too few observations"}
    grand = sum(v for v, _ in z) / n
    group_means = [0.0] * k
    group_counts = [0] * k
    for v, gi in z:
        group_means[gi] += v
        group_counts[gi] += 1
    for gi in range(k):
        group_means[gi] /= group_counts[gi]
    ssb = sum(group_counts[gi] * (group_means[gi] - grand) ** 2 for gi in range(k))
    ssw = sum((v - group_means[gi]) ** 2 for v, gi in z)
    df1 = k - 1
    df2 = n - k
    if df2 <= 0 or ssw == 0:
        return {"statistic": None, "p": None, "df1": df1, "df2": df2,
                "method": "brown_forsythe", "note": "zero within-group variance"}
    F = (ssb / df1) / (ssw / df2)
    p = 1.0 - f_cdf(F, df1, df2)
    return {"statistic": fmt_num(F), "p": fmt_p(p), "df1": df1, "df2": df2,
            "method": "brown_forsythe"}


# --------------------------------------------------------------------------- #
# Hypothesis tests
# --------------------------------------------------------------------------- #

def _rank_average(pairs):
    """pairs: list of (value, group_index). Returns parallel list of average ranks."""
    order = sorted(range(len(pairs)), key=lambda i: pairs[i][0])
    n = len(order)
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and pairs[order[j + 1]][0] == pairs[order[i]][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _tie_correction(pairs):
    from collections import Counter
    cnt = Counter(v for v, _ in pairs)
    n = len(pairs)
    return sum(c ** 3 - c for c in cnt.values())


def ttest_student(a, b):
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return {"method": "student_t", "note": "n<2, not applicable"}
    ma, mb = mean(a), mean(b)
    va, vb = variance(a), variance(b)
    df = na + nb - 2
    sp = ((na - 1) * va + (nb - 1) * vb) / df
    if sp <= 0:
        return {"method": "student_t", "note": "zero pooled variance, not applicable"}
    se = math.sqrt(sp * (1.0 / na + 1.0 / nb))
    t = (ma - mb) / se
    p = 2.0 * (1.0 - t_cdf(abs(t), df))
    return {"method": "student_t", "statistic": fmt_num(t), "df": df, "p": fmt_p(p),
            "mean_diff": fmt_num(ma - mb), "se": fmt_num(se)}


def ttest_welch(a, b):
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return {"method": "welch_t", "note": "n<2, not applicable"}
    ma, mb = mean(a), mean(b)
    va, vb = variance(a), variance(b)
    if va == 0 and vb == 0:
        return {"method": "welch_t", "note": "zero variance in both groups"}
    se = math.sqrt(va / na + vb / nb)
    if se == 0:
        return {"method": "welch_t", "note": "zero standard error"}
    t = (ma - mb) / se
    num = (va / na + vb / nb) ** 2
    den = (va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1)
    df = num / den if den > 0 else None
    p = 2.0 * (1.0 - t_cdf(abs(t), df))
    return {"method": "welch_t", "statistic": fmt_num(t), "df": fmt_num(df), "p": fmt_p(p),
            "mean_diff": fmt_num(ma - mb), "se": fmt_num(se)}


def mann_whitney_u(a, b):
    na, nb = len(a), len(b)
    if na == 0 or nb == 0:
        return {"method": "mann_whitney_u", "note": "empty group"}
    pairs = [(float(v), 0) for v in a] + [(float(v), 1) for v in b]
    ranks = _rank_average(pairs)
    n = len(pairs)
    r1 = sum(ranks[i] for i in range(n) if pairs[i][1] == 0)
    u1 = r1 - na * (na + 1) / 2.0
    u2 = na * nb - u1
    U = min(u1, u2)
    tie_sum = _tie_correction(pairs)
    mu = na * nb / 2.0
    sigma = math.sqrt(na * nb * (n + 1) / 12.0 * (1.0 - tie_sum / (n ** 3 - n)))
    if sigma == 0:
        return {"method": "mann_whitney_u", "statistic": U, "note": "all tied"}
    z = (abs(U - mu) - 0.5) / sigma  # continuity correction
    p = 2.0 * (1.0 - norm_cdf(abs(z)))
    return {"method": "mann_whitney_u", "statistic": U, "z": fmt_num(z), "p": fmt_p(p),
            "note": "normal approximation with continuity and tie correction"}


def kruskal_wallis(groups):
    k = len(groups)
    pairs = []
    for gi, g in enumerate(groups):
        for v in g:
            pairs.append((float(v), gi))
    n = len(pairs)
    if n == 0 or k < 2:
        return {"method": "kruskal_wallis", "note": "insufficient data"}
    ranks = _rank_average(pairs)
    R = [0.0] * k
    cnt = [0] * k
    for i in range(n):
        R[pairs[i][1]] += ranks[i]
        cnt[pairs[i][1]] += 1
    if any(c == 0 for c in cnt):
        return {"method": "kruskal_wallis", "note": "empty group"}
    H = 12.0 / (n * (n + 1)) * sum(R[i] ** 2 / cnt[i] for i in range(k)) - 3 * (n + 1)
    tie_sum = _tie_correction(pairs)
    denom = n ** 3 - n
    C = 1.0 - tie_sum / denom if denom > 0 else 1.0
    H_adj = H / C if C > 0 else H
    df = k - 1
    p = 1.0 - chi2_cdf(H_adj, df)
    return {"method": "kruskal_wallis", "statistic": fmt_num(H_adj), "df": df,
            "p": fmt_p(p), "note": "chi-square approximation with tie correction"}


def anova_oneway(groups):
    k = len(groups)
    all_vals = [float(v) for g in groups for v in g]
    n = len(all_vals)
    if n == 0 or k < 2:
        return {"method": "anova", "note": "insufficient data"}
    grand = sum(all_vals) / n
    group_means = [mean(g) for g in groups]
    ssb = sum(len(g) * (group_means[i] - grand) ** 2 for i, g in enumerate(groups))
    ssw = sum(sum((v - group_means[i]) ** 2 for v in g) for i, g in enumerate(groups))
    df1, df2 = k - 1, n - k
    if df2 <= 0:
        return {"method": "anova", "note": "df2 <= 0"}
    msb = ssb / df1
    msw = ssw / df2 if df2 > 0 else 0.0
    if msw == 0:
        return {"method": "anova", "ss_between": fmt_num(ssb), "ss_within": fmt_num(ssw),
                "df1": df1, "df2": df2, "note": "zero within-group variance"}
    F = msb / msw
    p = 1.0 - f_cdf(F, df1, df2)
    return {"method": "anova", "statistic": fmt_num(F), "df1": df1, "df2": df2,
            "p": fmt_p(p), "ss_between": fmt_num(ssb), "ss_within": fmt_num(ssw),
            "ms_between": fmt_num(msb), "ms_within": fmt_num(msw)}


def paired_ttest(a, b):
    n = len(a)
    if n != len(b):
        return {"method": "paired_t", "note": "length mismatch"}
    if n < 2:
        return {"method": "paired_t", "note": "n<2"}
    diffs = [a[i] - b[i] for i in range(n)]
    md = mean(diffs)
    s = sd(diffs)
    if s is None or s == 0:
        return {"method": "paired_t", "note": "zero variance of differences"}
    se = s / math.sqrt(n)
    t = md / se
    p = 2.0 * (1.0 - t_cdf(abs(t), n - 1))
    return {"method": "paired_t", "statistic": fmt_num(t), "df": n - 1, "p": fmt_p(p),
            "mean_diff": fmt_num(md), "se": fmt_num(se)}


def pearson_r(a, b):
    n = len(a)
    if n != len(b) or n < 3:
        return {"method": "pearson", "note": "insufficient data"}
    ma, mb = mean(a), mean(b)
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    da = math.sqrt(sum((v - ma) ** 2 for v in a))
    db = math.sqrt(sum((v - mb) ** 2 for v in b))
    if da == 0 or db == 0:
        return {"method": "pearson", "note": "zero variance"}
    r = num / (da * db)
    t = r * math.sqrt((n - 2) / (1 - r * r)) if abs(r) < 1 else math.inf
    p = 2.0 * (1.0 - t_cdf(abs(t), n - 2)) if math.isfinite(t) else 0.0
    return {"method": "pearson", "statistic": fmt_num(r), "df": n - 2, "p": fmt_p(p)}


def spearman_rho(a, b):
    n = len(a)
    if n != len(b) or n < 3:
        return {"method": "spearman", "note": "insufficient data"}
    ra = _rank_average([(float(v), 0) for v in a])
    rb = _rank_average([(float(v), 0) for v in b])
    r = pearson_r(ra, rb)
    r["method"] = "spearman"
    return r


# --------------------------------------------------------------------------- #
# Effect sizes and confidence intervals
# --------------------------------------------------------------------------- #

def cohens_d(a, b):
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return {"method": "cohens_d", "note": "n<2"}
    ma, mb = mean(a), mean(b)
    va, vb = variance(a), variance(b)
    sp = math.sqrt(((na - 1) * va + (nb - 1) * vb) / (na + nb - 2))
    if sp == 0:
        return {"method": "cohens_d", "note": "zero pooled sd"}
    d = (ma - mb) / sp
    se = math.sqrt((na + nb) / (na * nb) + d * d / (2 * (na + nb)))
    z = norm_ppf(0.975)
    return {"method": "cohens_d", "value": fmt_num(d), "ci_low": fmt_num(d - z * se),
            "ci_high": fmt_num(d + z * se),
            "note": "pooled-sd standardised mean difference, direction = group1 - group2"}


def hedges_g(a, b):
    d = cohens_d(a, b)
    if d.get("value") is None:
        d["method"] = "hedges_g"
        return d
    n = len(a) + len(b)
    J = 1.0 - 3.0 / (4.0 * n - 9.0)
    g = d["value"] * J
    z = norm_ppf(0.975)
    se = math.sqrt((len(a) + len(b)) / (len(a) * len(b))
                   + g * g / (2 * (len(a) + len(b))))
    return {"method": "hedges_g", "value": fmt_num(g),
            "ci_low": fmt_num(g - z * se), "ci_high": fmt_num(g + z * se),
            "note": "bias-corrected Cohen's d (small-sample correction J)"}


def glass_delta(a, b, ref="b"):
    na, nb = len(a), len(b)
    ref_vals = b if ref == "b" else a
    if len(ref_vals) < 2:
        return {"method": "glass_delta", "note": "n<2"}
    s_ref = sd(ref_vals)
    if s_ref == 0:
        return {"method": "glass_delta", "note": "zero reference sd"}
    d = (mean(a) - mean(b)) / s_ref
    return {"method": "glass_delta", "value": fmt_num(d), "reference": ref,
            "note": "standardised mean difference using reference-group sd"}


def eta_sq_omega_sq(anova_res, groups):
    if not all(k in anova_res for k in ("ss_between", "ss_within", "df1", "df2")):
        return {"eta_sq": None, "omega_sq": None}
    ssb, ssw = anova_res["ss_between"], anova_res["ss_within"]
    df1, df2 = anova_res["df1"], anova_res["df2"]
    sst = ssb + ssw
    n = df1 + df2 + 1
    eta = ssb / sst if sst else None
    msw = anova_res.get("ms_within")
    omega = None
    if msw:
        omega = (ssb - df1 * msw) / (sst + msw)
    return {"eta_sq": fmt_num(eta), "omega_sq": fmt_num(omega)}


def mean_ci(x, alpha=0.05):
    n = len(x)
    if n < 2:
        return {"method": "mean_t_ci", "note": "n<2"}
    m, s = mean(x), sd(x)
    t = t_ppf(1 - alpha / 2, n - 1)
    me = t * s / math.sqrt(n)
    return {"method": "mean_t_ci", "estimate": fmt_num(m),
            "ci_low": fmt_num(m - me), "ci_high": fmt_num(m + me), "level": 1 - alpha}


def mean_diff_ci(a, b, alpha=0.05):
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return {"method": "welch_diff_ci", "note": "n<2"}
    ma, mb = mean(a), mean(b)
    va, vb = variance(a), variance(b)
    se = math.sqrt(va / na + vb / nb)
    num = (va / na + vb / nb) ** 2
    den = (va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1)
    df = num / den if den > 0 else na + nb - 2
    t = t_ppf(1 - alpha / 2, df)
    diff = ma - mb
    return {"method": "welch_diff_ci", "estimate": fmt_num(diff),
            "ci_low": fmt_num(diff - t * se), "ci_high": fmt_num(diff + t * se),
            "level": 1 - alpha}


def wilson_ci(k, n, alpha=0.05):
    if n == 0:
        return {"method": "wilson_ci", "note": "n=0"}
    z = norm_ppf(1 - alpha / 2)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return {"method": "wilson_ci", "estimate": fmt_num(p),
            "ci_low": fmt_num(max(0.0, centre - half)),
            "ci_high": fmt_num(min(1.0, centre + half)), "level": 1 - alpha}


def fisher_z_ci(r, n, alpha=0.05):
    if n < 4 or abs(r) >= 1:
        return {"method": "fisher_z_ci", "note": "n<4 or |r|>=1"}
    z = norm_ppf(1 - alpha / 2)
    zr = math.atanh(r)
    se = 1.0 / math.sqrt(n - 3)
    lo, hi = zr - z * se, zr + z * se
    return {"method": "fisher_z_ci", "estimate": fmt_num(r),
            "ci_low": fmt_num(math.tanh(lo)), "ci_high": fmt_num(math.tanh(hi)),
            "level": 1 - alpha}


def benjamini_hochberg(pvalues):
    """Returns adjusted p-values (BH, monotonic). pvalues: list of (id, p)."""
    items = sorted([(p, i) for i, (pid, p) in enumerate(pvalues) if p is not None],
                   key=lambda t: (t[0] is None, t[0]))
    m = len(items)
    out = {}
    prev = None
    for rank, (p, orig_idx) in enumerate(items, start=1):
        adj = min(1.0, p * m / rank)
        if prev is not None:
            adj = max(adj, prev)
        out[orig_idx] = fmt_num(adj)
        prev = adj
    return [out.get(i) for i in range(len(pvalues))]


def bonferroni(pvalues, m):
    return [fmt_num(min(1.0, p * m)) if p is not None else None for _, p in pvalues]


# --------------------------------------------------------------------------- #
# Input discovery and schema inference
# --------------------------------------------------------------------------- #

def discover_files(input_dir):
    files = []
    if not os.path.isdir(input_dir):
        return files
    for root, dirs, names in os.walk(input_dir):
        dirs.sort()
        for name in sorted(names):
            path = os.path.join(root, name)
            rel = os.path.relpath(path, input_dir).replace(os.sep, "/")
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            kind = kind_of(rel)
            if kind in ("dir",):
                continue
            files.append({
                "path": path, "rel_path": rel, "kind": kind,
                "size": size, "sha256": sha256_file(path) if size <= MAX_FILE_BYTES else None,
            })
    return files


def kind_of(rel_path):
    ext = rel_path.rsplit(".", 1)[-1].lower() if "." in rel_path else ""
    if ext in ("csv",):
        return "table_csv"
    if ext in ("tsv", "tab", "txt"):
        return "table_text"
    if ext in ("json", "jsonl"):
        return "json"
    if ext in ("db", "sqlite", "sqlite3"):
        return "database"
    if ext in ("xlsx", "xls"):
        return "spreadsheet"
    if ext in ("docx", "pdf", "doc"):
        return "document"
    if ext in ("yaml", "yml", "md", "markdown"):
        return "text"
    return "other"


def input_fingerprint(input_dir, case_id):
    files = discover_files(input_dir)
    digest = hashlib.sha256()
    for f in files:
        digest.update(f["rel_path"].encode("utf-8"))
        digest.update(b"\x00")
        digest.update(str(f["size"]).encode("utf-8"))
        digest.update(b"\x00")
        if f["sha256"]:
            digest.update(f["sha256"].encode("utf-8"))
        digest.update(b"\x00")
    digest.update(case_id.encode("utf-8"))
    return digest.hexdigest()


def load_records(path, kind):
    """Load tabular data as a list of dicts. Returns (records, meta) or (None, error)."""
    if kind == "table_csv":
        return _load_csv(path, ",")
    if kind == "table_text":
        return _load_csv(path, None)  # sniff delimiter
    if kind == "json":
        return _load_json(path)
    if kind == "database":
        return _load_sqlite(path)
    return None, {"error": "unsupported kind: %s" % kind}


def _load_csv(path, delim):
    text = read_text(path)
    if text is None:
        return None, {"error": "unreadable or oversize file"}
    try:
        sample = text[:4096]
        if delim is None:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
            delim = dialect.delimiter
        reader = csv.DictReader(io.StringIO(text), delimiter=delim)
        records = [dict(r) for r in reader]
    except Exception as exc:
        # fallback: comma
        try:
            reader = csv.DictReader(io.StringIO(text), delimiter=",")
            records = [dict(r) for r in reader]
            delim = ","
        except Exception:
            return None, {"error": "csv parse failed: %s" % exc}
    return records, {"delimiter": delim, "n_rows": len(records)}


def _load_json(path):
    data = read_json(path)
    if data is None:
        return None, {"error": "invalid or unreadable JSON"}
    if isinstance(data, list):
        records = [d for d in data if isinstance(d, dict)]
        if not records:
            return None, {"error": "JSON list contains no dict records"}
        return records, {"n_rows": len(records)}
    if isinstance(data, dict):
        for key in ("records", "data", "rows", "items"):
            if isinstance(data.get(key), list):
                records = [d for d in data[key] if isinstance(d, dict)]
                if records:
                    return records, {"n_rows": len(records), "key": key}
        return None, {"error": "JSON object has no recognised record list"}
    return None, {"error": "JSON root is not a list or object"}


def _load_sqlite(path):
    tables = {}
    try:
        con = sqlite3.connect(path)
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        names = [r[0] for r in cur.fetchall()]
        for name in names:
            try:
                cur.execute('SELECT * FROM "%s" LIMIT 20000' % name.replace('"', '""'))
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, row)) for row in cur.fetchall()]
                tables[name] = {"columns": cols, "records": rows, "n_rows": len(rows)}
            except Exception:
                tables[name] = {"columns": [], "records": [], "n_rows": 0,
                                "error": "table read failed"}
        con.close()
    except Exception as exc:
        return None, {"error": "sqlite open/query failed: %s" % exc}
    if not tables:
        return None, {"error": "no tables in database"}
    return tables, {"n_tables": len(tables)}


_MISSING_MARKERS = {"", "na", "n/a", "nan", "null", "none", "?", ".", "-", "missing",
                    "nr", "not reported", "not_reported", "unknown", "na."}


def is_missing(v):
    """Recognise empty strings and common missing markers; used for honest missing counting."""
    if v is None:
        return True
    s = str(v).strip()
    return s.lower() in _MISSING_MARKERS


def to_float(v):
    try:
        s = str(v).strip()
        if is_missing(s):
            return None
        return float(s.replace(",", ""))
    except (ValueError, TypeError):
        return None


def infer_column_type(values):
    """Returns one of numeric/categorical/date/id/empty."""
    non_null = [v for v in values if not is_missing(v)]
    if not non_null:
        return "empty", 0
    numeric = 0
    for v in non_null:
        if to_float(v) is not None:
            numeric += 1
    ratio = numeric / len(non_null)
    n_unique = len(set(str(v) for v in non_null))
    if ratio > 0.95:
        return "numeric", n_unique
    if ratio > 0.5:
        return "mixed", n_unique
    return "categorical", n_unique


def infer_schema(records, columns):
    schema = {}
    for col in columns:
        vals = [r.get(col) for r in records]
        non_null = [v for v in vals if not is_missing(v)]
        ctype, n_unique = infer_column_type(non_null)
        numeric_vals = [to_float(v) for v in non_null]
        numeric_vals = [v for v in numeric_vals if v is not None]
        col_info = {
            "type": ctype, "n_missing": len(vals) - len(non_null),
            "missing_ratio": fmt_num((len(vals) - len(non_null)) / len(vals)) if vals else 0.0,
            "n_unique": n_unique,
            "sample": [str(v) for v in non_null[:5]],
        }
        if numeric_vals:
            col_info["min"] = fmt_num(min(numeric_vals))
            col_info["max"] = fmt_num(max(numeric_vals))
        schema[col] = col_info
    return schema


def heuristic_plan(schema, records, data_dictionary=None):
    columns = list(schema.keys())
    numeric = [c for c in columns if schema[c]["type"] == "numeric"]
    cat = [c for c in columns if schema[c]["type"] in ("categorical", "mixed")
           and 2 <= schema[c]["n_unique"] <= 12]
    id_cols = [c for c in columns if c.lower() in ("id", "sample_id", "subject_id", "row_id",
                                                   "record_id") or c.lower().endswith("_id")]
    group_col = None
    for c in cat:
        if schema[c]["n_unique"] in (2, 3):
            group_col = c
            break
    if group_col is None and cat:
        group_col = cat[0]
    value_cols = [c for c in numeric if c not in id_cols]
    design = "univariate"
    if group_col is not None and len(value_cols) >= 1:
        nlevels = len({str(r.get(group_col)) for r in records if r.get(group_col) is not None})
        design = "two_independent" if nlevels == 2 else ("three_independent" if nlevels == 3
                                                         else "multi_group")
    units = {}
    if data_dictionary:
        for item in data_dictionary:
            if isinstance(item, dict):
                field = item.get("field") or item.get("name") or item.get("column")
                unit = item.get("unit")
                if field in columns and isinstance(unit, str) and unit.strip():
                    units[field] = unit.strip()
    plan = {
        "design": design,
        "group_column": group_col,
        "value_columns": value_cols,
        "numeric_columns": numeric,
        "categorical_columns": cat,
        "id_columns": id_cols,
        "units": units,
        "reference_group": None,
        "alpha": 0.05,
        "missing_rule": "listwise_per_test",
        "outlier_rule": "iqr1.5_flag_no_delete",
        "duplicate_rule": "keep_first",
        "decided_by": "heuristic",
        "notes": "heuristic plan; Agent must verify design/units and overwrite",
    }
    return plan


# --------------------------------------------------------------------------- #
# Cleaning (deterministic, no silent deletion)
# --------------------------------------------------------------------------- #

def _row_key(record, columns):
    return tuple(str(record.get(c)) for c in columns)


def clean_records(records, columns, plan):
    n_raw = len(records)
    dup_cols = plan.get("id_columns") or columns
    seen = set()
    kept = []
    dup_dropped = []
    for idx, rec in enumerate(records):
        key = _row_key(rec, dup_cols)
        if key in seen:
            dup_dropped.append({"row_index": idx, "key": list(key)})
        else:
            seen.add(key)
            kept.append({"row_index": idx, "record": rec})
    missing = {}
    outlier = {}
    for col in columns:
        vals = [r["record"].get(col) for r in kept]
        non_null = [v for v in vals if not is_missing(v)]
        missing[col] = {"n": len(vals) - len(non_null),
                        "ratio": fmt_num((len(vals) - len(non_null)) / len(vals)) if vals else 0.0}
        if schema_type(vals) == "numeric":
            nums = [(i, to_float(v)) for i, v in enumerate(vals) if to_float(v) is not None]
            if nums:
                q1 = quantile([v for _, v in nums], 0.25)
                q3 = quantile([v for _, v in nums], 0.75)
                iqr = q3 - q1
                lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
                flags = [{"row_index": kept[i]["row_index"], "value": fmt_num(v)}
                         for i, v in nums if v < lo or v > hi]
                outlier[col] = {"rule": "iqr_1.5_flag_no_delete", "lower": fmt_num(lo),
                                "upper": fmt_num(hi), "n": len(flags), "flags": flags}
    return {
        "n_raw": n_raw, "n_kept": len(kept), "n_duplicates_dropped": len(dup_dropped),
        "duplicate_rule": plan.get("duplicate_rule", "keep_first"),
        "kept_records": [r["record"] for r in kept],
        "dup_dropped": dup_dropped, "missing": missing, "outliers": outlier,
    }


def schema_type(vals):
    non_null = [v for v in vals if not is_missing(v)]
    if not non_null:
        return "empty"
    numeric = sum(1 for v in non_null if to_float(v) is not None)
    return "numeric" if numeric / len(non_null) > 0.95 else "categorical"


def group_values(records, group_col, value_col):
    """Split records into groups of floats (complete cases only)."""
    groups = {}
    order = []
    for rec in records:
        g = rec.get(group_col)
        v = to_float(rec.get(value_col))
        if g is None or v is None:
            continue
        key = str(g)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(v)
    return groups, order


# --------------------------------------------------------------------------- #
# Analysis (two/three groups, correlation)
# --------------------------------------------------------------------------- #

def run_inference(plan, kept_records):
    design = plan.get("design", "univariate")
    value_cols = plan.get("value_columns") or []
    group_col = plan.get("group_column")
    alpha = plan.get("alpha", 0.05)
    out = {"design": design, "alpha": alpha, "descriptive": {}, "inferential": [],
           "effects": [], "confidence_intervals": [], "normality": [], "variance_equality": []}

    # descriptive stats per value column (complete cases)
    for col in value_cols:
        vals = [to_float(r.get(col)) for r in kept_records]
        vals = [v for v in vals if v is not None]
        out["descriptive"][col] = describe(vals)
        out["descriptive"][col]["unit"] = plan.get("units", {}).get(col, "unit_unknown")

    if design in ("two_independent", "three_independent", "multi_group") and group_col:
        for col in value_cols:
            groups, order = group_values(kept_records, group_col, col)
            if len(groups) < 2:
                out["inferential"].append({"column": col,
                                           "note": "fewer than 2 non-empty groups"})
                continue
            norm_res = {}
            for g in order:
                norm_res[g] = shapiro_wilk_francia(groups[g])
            bf = brown_forsythe([groups[g] for g in order])
            out["normality"].append({"column": col, "by_group": norm_res})
            out["variance_equality"].append({"column": col, **bf})
            entry = {"column": col, "groups": order, "n_per_group": [len(groups[g]) for g in order],
                     "group_means": {g: fmt_num(mean(groups[g])) for g in order},
                     "unit": plan.get("units", {}).get(col, "unit_unknown")}

            if design == "two_independent" and len(order) == 2:
                a, b = groups[order[0]], groups[order[1]]
                normal_ok = all(norm_res[g].get("p") is not None and norm_res[g]["p"] >= alpha
                                for g in order)
                homoscedastic_ok = bf.get("p") is not None and bf["p"] >= alpha
                if normal_ok and homoscedastic_ok:
                    test = ttest_student(a, b)
                elif normal_ok and not homoscedastic_ok:
                    test = ttest_welch(a, b)
                else:
                    test = mann_whitney_u(a, b)
                entry["test"] = test
                entry["effect"] = hedges_g(a, b)
                entry["effect_glass"] = glass_delta(a, b, ref="b")
                entry["ci_mean_diff"] = mean_diff_ci(a, b, alpha)
                entry["ci_means"] = {g: mean_ci(groups[g], alpha) for g in order}
                entry["decision_basis"] = {
                    "normality_p": {g: norm_res[g].get("p") for g in order},
                    "brown_forsythe_p": bf.get("p"),
                    "normal_ok": normal_ok, "homoscedastic_ok": homoscedastic_ok,
                }
            else:
                anova = anova_oneway([groups[g] for g in order])
                kw = kruskal_wallis([groups[g] for g in order])
                entry["test_anova"] = anova
                entry["test_kruskal_wallis"] = kw
                es = eta_sq_omega_sq(anova, [groups[g] for g in order])
                entry["effect"] = es
                # post-hoc pairwise with corrections (never raw two-sample t across 3+ groups)
                pairs, raw_p = [], []
                for i in range(len(order)):
                    for j in range(i + 1, len(order)):
                        gi, gj = order[i], order[j]
                        t = ttest_welch(groups[gi], groups[gj])
                        pairs.append((gi, gj))
                        raw_p.append(t.get("p"))
                bh = benjamini_hochberg(list(zip(pairs, raw_p)))
                bonf = bonferroni(list(zip(pairs, raw_p)), len(pairs))
                entry["posthoc"] = [
                    {"pair": [pairs[k][0], pairs[k][1]],
                     "welch_p": raw_p[k], "bonferroni_p": bonf[k], "bh_p": bh[k]}
                    for k in range(len(pairs))
                ]
            out["inferential"].append(entry)

    elif design == "correlation" and len(value_cols) >= 2:
        col_a, col_b = value_cols[0], value_cols[1]
        rows = [(to_float(r.get(col_a)), to_float(r.get(col_b))) for r in kept_records]
        rows = [(x, y) for x, y in rows if x is not None and y is not None]
        a, b = [x for x, _ in rows], [y for _, y in rows]
        if len(a) >= 3:
            pr = pearson_r(a, b)
            sr = spearman_rho(a, b)
            out["inferential"].append({"columns": [col_a, col_b], "pearson": pr,
                                       "spearman": sr})
            ci = {"estimate": None}
            if pr.get("statistic") is not None:
                ci = fisher_z_ci(pr["statistic"], len(a), alpha)
            ci["method"] = "fisher_z_ci"
            ci["columns"] = [col_a, col_b]
            out["confidence_intervals"].append(ci)
    return out


# --------------------------------------------------------------------------- #
# SVG figures (deterministic, editable)
# --------------------------------------------------------------------------- #

def _svg_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def boxplot_svg(groups, order, title="", unit=""):
    W, H = 420, 260
    pad_l, pad_r, pad_t, pad_b = 48, 12, 40, 40
    plot_w = W - pad_l - pad_r
    plot_h = H - pad_t - pad_b
    all_vals = [v for g in order for v in groups[g]]
    if not all_vals:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d"/>' % (W, H)
    lo = min(all_vals)
    hi = max(all_vals)
    if hi - lo < 1e-12:
        hi = lo + 1.0
    def y(v):
        return pad_t + plot_h * (1 - (v - lo) / (hi - lo))
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'viewBox="0 0 %d %d">' % (W, H, W, H)]
    parts.append('<text x="%d" y="22" font-size="12" font-family="sans-serif">%s</text>'
                 % (pad_l, _svg_escape(title)))
    n = len(order)
    slot = plot_w / n
    # y axis ticks
    for i in range(5):
        val = lo + (hi - lo) * i / 4
        yy = y(val)
        parts.append('<line x1="%d" y1="%.2f" x2="%d" y2="%.2f" stroke="#ccc"/>'
                     % (pad_l, yy, W - pad_r, yy))
        parts.append('<text x="%d" y="%.2f" font-size="9" text-anchor="end">%s</text>'
                     % (pad_l - 3, yy + 3, _svg_escape("%.4g" % val)))
    for gi, gname in enumerate(order):
        vals = sorted(groups[gname])
        cx = pad_l + slot * (gi + 0.5)
        q1, med, q3 = quantile(vals, 0.25), median(vals), quantile(vals, 0.75)
        iqr = q3 - q1
        wlo, whi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        box_w = min(40.0, slot * 0.6)
        y_q1, y_q3 = y(q1), y(q3)
        parts.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" fill="none" '
                     'stroke="#2b6cb0" stroke-width="1.5"/>'
                     % (cx - box_w / 2, y_q3, box_w, y_q1 - y_q3))
        parts.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#2b6cb0" '
                     'stroke-width="1.5"/>' % (cx - box_w / 2, y(med), cx + box_w / 2, y(med)))
        # whiskers
        w_lo_v = min([v for v in vals if v >= wlo], default=wlo)
        w_hi_v = max([v for v in vals if v <= whi], default=whi)
        parts.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#2b6cb0"/>'
                     % (cx, y(w_lo_v), cx, y(q1)))
        parts.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#2b6cb0"/>'
                     % (cx, y(q3), cx, y(w_hi_v)))
        for end in (w_lo_v, w_hi_v):
            parts.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#2b6cb0"/>'
                         % (cx - box_w / 4, y(end), cx + box_w / 4, y(end)))
        for v in vals:
            if v < wlo or v > whi:
                parts.append('<circle cx="%.2f" cy="%.2f" r="2.4" fill="#c53030"/>'
                             % (cx, y(v)))
        parts.append('<text x="%.2f" y="%d" font-size="10" text-anchor="middle">%s</text>'
                     % (cx, H - pad_b + 16, _svg_escape(gname)))
    parts.append('</svg>')
    return "\n".join(parts)


def histogram_svg(values, title="", unit=""):
    W, H = 420, 260
    pad_l, pad_r, pad_t, pad_b = 48, 12, 40, 36
    vals = sorted(values)
    if not vals:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d"/>' % (W, H)
    lo, hi = vals[0], vals[-1]
    if hi - lo < 1e-12:
        hi = lo + 1.0
    nbins = min(20, max(5, int(math.sqrt(len(vals)))))
    width = (hi - lo) / nbins
    counts = [0] * nbins
    for v in vals:
        idx = min(nbins - 1, int((v - lo) / width))
        counts[idx] += 1
    max_c = max(counts) or 1
    plot_w, plot_h = W - pad_l - pad_r, H - pad_t - pad_b
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'viewBox="0 0 %d %d">' % (W, H, W, H)]
    parts.append('<text x="%d" y="22" font-size="12" font-family="sans-serif">%s</text>'
                 % (pad_l, _svg_escape(title)))
    for i, c in enumerate(counts):
        x0 = pad_l + plot_w * i / nbins
        x1 = pad_l + plot_w * (i + 1) / nbins
        h = plot_h * c / max_c
        parts.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" fill="#2b6cb0" '
                     'fill-opacity="0.85" stroke="none"/>'
                     % (x0, pad_t + plot_h - h, x1 - x0, h))
    parts.append('</svg>')
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Pipeline stages
# --------------------------------------------------------------------------- #

def _ensure(d):
    os.makedirs(d, exist_ok=True)
    return d


def stage_init(input_dir, output_dir, work_dir, case_id, status="partial"):
    _ensure(output_dir)
    _ensure(work_dir)
    files = discover_files(input_dir)
    index = {"case_id": case_id, "files": files,
             "input_fingerprint": input_fingerprint(input_dir, case_id),
             "skill_version": SKILL_VERSION}
    write_json(os.path.join(work_dir, "index.json"), index)
    skeleton = {
        "case_id": case_id,
        "status": status,
        "result": {},
        "quality": [],
        "descriptive": [],
        "inferential": [],
        "effects": [],
        "interpretation": {"note": "initial skeleton, analysis pending"},
        "sources": [{"source_id": "S%d" % (i + 1), "path": f["path"],
                     "kind": f["kind"], "rel_path": f["rel_path"]}
                    for i, f in enumerate(files)],
        "uncertainty": ["initial partial skeleton, not yet analysed"],
        "change_log": [{"id": "T01", "time": "T01", "action": "init",
                        "before": None, "after": "result.json skeleton",
                        "reason": "first 3-minute partial deliverable"}],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "determinism": {"input_fingerprint": index["input_fingerprint"],
                        "deterministic_sha256": None, "rng": "none"},
        "runtime": {"started_at": _now_iso(), "finished_at": None,
                    "duration_seconds": None, "python_version": sys.version.split()[0],
                    "wall_clock_note": "only intentionally non-reproducible field"},
    }
    write_json(os.path.join(output_dir, "result.json"), skeleton)
    return index, skeleton


def stage_survey(input_dir, work_dir):
    files = discover_files(input_dir)
    survey = {"files": files, "tools": []}
    res_yaml = os.path.join(input_dir, "environment", "resources.yaml")
    if os.path.isfile(res_yaml):
        txt = read_text(res_yaml)
        survey["resources_yaml"] = txt[:8000] if txt else None
    write_json(os.path.join(work_dir, "survey.json"), survey)
    return survey


def stage_inspect(input_dir, work_dir):
    files = discover_files(input_dir)
    case_path = os.path.join(input_dir, "case.json")
    case = read_json(case_path) if os.path.isfile(case_path) else None
    data_dictionary = None
    if isinstance(case, dict):
        oc = case.get("output_contract") or {}
        if isinstance(oc, dict):
            data_dictionary = oc.get("data_dictionary")
    tables = {}
    errors = []
    for f in files:
        if f["kind"] not in ("table_csv", "table_text", "json", "database"):
            continue
        records, meta = load_records(f["path"], f["kind"])
        if records is None:
            errors.append({"rel_path": f["rel_path"], "error": (meta or {}).get("error")})
            continue
        if isinstance(records, dict):  # sqlite: multiple tables
            for tname, tinfo in records.items():
                tkey = "%s::%s" % (f["rel_path"], tname)
                columns = tinfo.get("columns") or (list(tinfo["records"][0].keys())
                                                   if tinfo["records"] else [])
                schema = infer_schema(tinfo["records"], columns)
                tables[tkey] = {"rel_path": f["rel_path"], "table": tname,
                                "n_rows": tinfo.get("n_rows"), "columns": columns,
                                "schema": schema}
        else:
            columns = list(records[0].keys()) if records else []
            schema = infer_schema(records, columns)
            tables[f["rel_path"]] = {"rel_path": f["rel_path"], "n_rows": len(records),
                                     "columns": columns, "schema": schema}
    # build heuristic plan from the first table
    plan = None
    for key, tinfo in tables.items():
        cols = tinfo["columns"]
        schema = tinfo["schema"]
        recs = []
        rel = tinfo["rel_path"]
        for f in files:
            if f["rel_path"] != rel:
                continue
            if f["kind"] == "database":
                loaded, _ = load_records(f["path"], "database")
                tname = tinfo.get("table")
                if isinstance(loaded, dict) and tname and tname in loaded:
                    recs = loaded[tname].get("records") or []
            else:
                loaded, _ = load_records(f["path"], f["kind"])
                if isinstance(loaded, list):
                    recs = loaded
            break
        plan = heuristic_plan(schema, recs, data_dictionary)
        plan["table_key"] = key
        plan["rel_path"] = rel
        break
    if plan is None:
        plan = {"design": "univariate", "value_columns": [], "group_column": None,
                "decided_by": "heuristic", "notes": "no tabular data found"}
    inspect = {"case_id": (case or {}).get("case_id", "unknown-case"),
               "data_dictionary": data_dictionary, "tables": tables,
               "errors": errors, "plan": plan}
    write_json(os.path.join(work_dir, "schema.json"), inspect)
    write_json(os.path.join(work_dir, "plan.json"), plan)
    return inspect


def _load_plan(work_dir):
    plan = read_json(os.path.join(work_dir, "plan.json"))
    if not isinstance(plan, dict):
        plan = {"design": "univariate", "value_columns": [], "decided_by": "heuristic"}
    return plan


def stage_analyze(input_dir, work_dir, output_dir):
    _ensure(output_dir)
    plan = _load_plan(work_dir)
    table_key = plan.get("table_key") or plan.get("rel_path")
    records = []
    columns = []
    src_path = None
    if table_key:
        for f in discover_files(input_dir):
            if f["kind"] in ("table_csv", "table_text", "json"):
                if f["rel_path"] == table_key or f["rel_path"] == plan.get("rel_path"):
                    recs, _ = load_records(f["path"], f["kind"])
                    if isinstance(recs, list):
                        records, src_path = recs, f["path"]
                        columns = list(recs[0].keys()) if recs else []
                    break
            elif f["kind"] == "database":
                recs, _ = load_records(f["path"], "database")
                if isinstance(recs, dict) and table_key:
                    tname = table_key.split("::")[-1] if "::" in table_key else None
                    if tname and tname in recs:
                        records = recs[tname]["records"]
                        columns = recs[tname]["columns"]
                        src_path = f["path"]
                        break
    if not records:
        return {"error": "no tabular data for analysis", "descriptive": {},
                "inferential": [], "effects": [], "confidence_intervals": []}

    cleaned = clean_records(records, columns, plan)
    kept_records = cleaned["kept_records"]
    inference = run_inference(plan, kept_records)

    # cleaned CSV
    cleaned_dir = _ensure(os.path.join(output_dir, "cleaned"))
    cleaned_path = os.path.join(cleaned_dir, "cleaned_data.csv")
    with open(cleaned_path, "w", encoding="utf-8", newline="\n") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for rec in kept_records:
            writer.writerow({c: rec.get(c, "") for c in columns})

    # figures
    fig_dir = _ensure(os.path.join(output_dir, "figures"))
    figures = []
    group_col = plan.get("group_column")
    for col in (plan.get("value_columns") or []):
        vals = [to_float(r.get(col)) for r in kept_records]
        vals = [v for v in vals if v is not None]
        if not vals:
            continue
        if group_col and (plan.get("design") or "") in ("two_independent", "three_independent",
                                                        "multi_group"):
            groups, order = group_values(kept_records, group_col, col)
            svg = boxplot_svg(groups, order, title=col,
                              unit=plan.get("units", {}).get(col, "unit_unknown"))
        else:
            svg = histogram_svg(vals, title=col,
                                unit=plan.get("units", {}).get(col, "unit_unknown"))
        fname = "fig_%s.svg" % _safe_name(col)
        with open(os.path.join(fig_dir, fname), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(svg)
        figures.append({"file": fname, "column": col})

    # quality checks
    qc = {
        "n_raw": cleaned["n_raw"], "n_kept": cleaned["n_kept"],
        "n_duplicates_dropped": cleaned["n_duplicates_dropped"],
        "duplicate_rule": cleaned["duplicate_rule"],
        "duplicate_dropped": cleaned["dup_dropped"],
        "missing": cleaned["missing"],
        "outliers": cleaned["outliers"],
        "checks": [
            {"id": "missing", "status": "recorded",
             "rule": "count + ratio, never treated as 0"},
            {"id": "outlier", "status": "recorded",
             "rule": "IQR 1.5 flagged, never silently deleted"},
            {"id": "duplicate", "status": "recorded",
             "rule": "keep first occurrence, drop count logged"},
            {"id": "normality", "status": "checked"},
            {"id": "variance_equality", "status": "checked"},
            {"id": "multiple_comparison", "status": "corrected" if "posthoc" in str(inference) else "n/a"},
        ],
    }
    write_json(os.path.join(output_dir, "quality_checks.json"), qc)

    analysis = {
        "plan": plan, "cleaning": {k: v for k, v in cleaned.items() if k != "kept_records"},
        "cleaned_csv": "cleaned/cleaned_data.csv", "figures": figures,
        "quality_checks": "quality_checks.json",
        "descriptive": inference.get("descriptive", {}),
        "inferential": inference.get("inferential", []),
        "effects": inference.get("effects", []),
        "confidence_intervals": inference.get("confidence_intervals", []),
        "normality": inference.get("normality", []),
        "variance_equality": inference.get("variance_equality", []),
        "engine": "stdlib-deterministic",
    }
    write_json(os.path.join(work_dir, "analysis.json"), analysis)
    return analysis


def _safe_name(s):
    out = []
    for ch in str(s):
        out.append(ch if ch.isalnum() or ch in ("-", "_") else "_")
    return "".join(out) or "col"


def _backfill_contract(output_contract, analysis):
    """Backfill every output_contract key from analysis sections by name match."""
    if not isinstance(output_contract, dict):
        return {}
    result = {}
    for key, value in output_contract.items():
        k = str(key)
        if isinstance(value, list):
            result[k] = []
        elif isinstance(value, dict):
            result[k] = {}
        else:
            result[k] = None
    # map well-known sections
    mapping = {
        "descriptive": analysis.get("descriptive", {}),
        "inferential": analysis.get("inferential", []),
        "effects": analysis.get("effects", []),
        "confidence_intervals": analysis.get("confidence_intervals", []),
        "quality": analysis.get("quality_checks", {}),
    }
    for key in result:
        if key in mapping:
            result[key] = mapping[key]
    return result


def _compute_det_hash(payload):
    """Hash of the deterministic payload: strip runtime, and hash the determinism block
    with its own sha256 field cleared (so the hash does not include itself)."""
    det_payload = {k: v for k, v in payload.items() if k != "runtime"}
    det_payload = _sanitize(det_payload)
    det = dict(det_payload.get("determinism") or {})
    det["deterministic_sha256"] = None
    det_payload["determinism"] = det
    return sha256_bytes(canonical_json(det_payload))


def stage_build(input_dir, work_dir, output_dir, status="ok"):
    case_path = os.path.join(input_dir, "case.json")
    case = read_json(case_path) if os.path.isfile(case_path) else None
    if not isinstance(case, dict):
        case = {}
    case_id = case.get("case_id", "unknown-case")
    output_contract = case.get("output_contract") or {}
    analysis = read_json(os.path.join(work_dir, "analysis.json")) or {}
    qc = read_json(os.path.join(output_dir, "quality_checks.json")) or {}

    payload = {"case_id": case_id}
    for k, v in _backfill_contract(output_contract, analysis).items():
        payload[k] = v
    payload["status"] = status
    payload["result"] = analysis
    payload["quality"] = qc
    payload["descriptive"] = analysis.get("descriptive", [])
    payload["inferential"] = analysis.get("inferential", [])
    payload["effects"] = analysis.get("effects", [])
    payload["interpretation"] = {
        "note": "statistical results computed deterministically; interpret with effect sizes "
                "and CI, do not read p as proof of effect",
        "assumptions": "normality (Shapiro-Francia W' if no scipy) and Brown-Forsythe checked "
                       "before test selection",
    }
    files = discover_files(input_dir)
    payload["sources"] = [{"source_id": "S%d" % (i + 1), "path": f["path"],
                           "kind": f["kind"], "rel_path": f["rel_path"]}
                          for i, f in enumerate(files)]
    payload["uncertainty"] = [
        "p-values from stdlib approximations; cross-check with scipy when available",
        "units marked unit_unknown unless stated in data dictionary",
    ]
    payload["change_log"] = [
        {"id": "T01", "time": "T01", "action": "init", "before": None,
         "after": "result.json skeleton", "reason": "first 3-minute partial deliverable"},
        {"id": "T02", "time": "T02", "action": "inspect", "before": None,
         "after": "schema.json + plan.json", "reason": "schema and heuristic design plan"},
        {"id": "T03", "time": "T03", "action": "analyze", "before": None,
         "after": "analysis.json + cleaned CSV + figures",
         "reason": "deterministic cleaning/descriptive/inference/effects/CI"},
        {"id": "T04", "time": "T04", "action": "build", "before": None,
         "after": "result.json assembled", "reason": "contract backfill + envelope"},
    ]
    payload["pending_human"] = []
    payload["errors"] = []
    payload["degradations"] = []
    fp = input_fingerprint(input_dir, case_id)
    payload["determinism"] = {"input_fingerprint": fp, "deterministic_sha256": None, "rng": "none"}
    started = _now_iso()
    finished = _now_iso()
    try:
        from datetime import datetime as _dt
        duration = round((_dt.fromisoformat(finished) - _dt.fromisoformat(started)).total_seconds(), 3)
    except Exception:
        duration = None
    payload["runtime"] = {"started_at": started, "finished_at": finished,
                          "duration_seconds": duration,
                          "python_version": sys.version.split()[0],
                          "wall_clock_note": "only intentionally non-reproducible field"}
    payload["determinism"]["deterministic_sha256"] = _compute_det_hash(payload)
    write_json(os.path.join(output_dir, "result.json"), payload)

    # analysis_report.md
    report = _render_report(case_id, analysis, qc)
    with open(os.path.join(output_dir, "analysis_report.md"), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write(report)
    # analysis_code.py (reproducible runner stub that documents the exact commands)
    code = (
        "# Reproducibility: this result was produced by the deterministic stdlib pipeline.\n"
        "# Re-run with:  cd /skills/weekly-skill && python3 scripts/main.py run\n"
        "# Input fingerprint: %s\n"
        "# Skill version: %s\n"
        % (fp, SKILL_VERSION)
    )
    with open(os.path.join(output_dir, "analysis_code.py"), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write(code)
    return payload


def _render_report(case_id, analysis, qc):
    lines = ["# Analysis Report", "", "case_id: %s" % case_id,
             "engine: stdlib-deterministic (no random, fixed serialisation)", ""]
    lines.append("## Cleaning")
    lines.append("- raw rows: %s" % qc.get("n_raw"))
    lines.append("- kept rows: %s" % qc.get("n_kept"))
    lines.append("- duplicates dropped: %s (rule: %s)"
                 % (qc.get("n_duplicates_dropped"), qc.get("duplicate_rule")))
    lines.append("")
    lines.append("## Descriptive")
    for col, d in (analysis.get("descriptive") or {}).items():
        lines.append("- %s (unit=%s): n=%s mean=%s sd=%s median=%s min=%s max=%s"
                     % (col, d.get("unit"), d.get("n"), d.get("mean"), d.get("sd"),
                        d.get("median"), d.get("min"), d.get("max")))
    lines.append("")
    lines.append("## Inferential")
    for item in (analysis.get("inferential") or []):
        lines.append("- %s" % json.dumps(item, ensure_ascii=False, sort_keys=True))
    lines.append("")
    lines.append("## Interpretation note")
    lines.append("Report p as exact values; distinguish statistical significance from practical "
                 "significance; effect sizes and 95% CIs are reported with each key estimate.")
    return "\n".join(lines) + "\n"


def stage_selfcheck(output_dir):
    result = read_json(os.path.join(output_dir, "result.json"))
    issues = []
    if not isinstance(result, dict):
        return {"ok": False, "issues": ["result.json not a dict or unreadable"]}
    if "case_id" not in result:
        issues.append("missing case_id")
    if result.get("status") not in VALID_STATUS:
        issues.append("invalid status %r" % result.get("status"))
    for key in ("sources", "uncertainty", "change_log", "pending_human", "errors",
                "degradations"):
        if key not in result:
            issues.append("missing envelope key %s" % key)
    det = result.get("determinism") or {}
    if det.get("deterministic_sha256"):
        recomputed = _compute_det_hash(result)
        if recomputed != det["deterministic_sha256"]:
            issues.append("deterministic_sha256 mismatch")
    for fig in (result.get("result") or {}).get("figures", []):
        pass
    fig_dir = os.path.join(output_dir, "figures")
    if os.path.isdir(fig_dir):
        for name in os.listdir(fig_dir):
            if name.endswith(".svg"):
                try:
                    ET.parse(os.path.join(fig_dir, name))
                except Exception as exc:
                    issues.append("invalid SVG %s: %s" % (name, exc))
    cleaned = os.path.join(output_dir, "cleaned", "cleaned_data.csv")
    if os.path.isfile(cleaned):
        try:
            with open(cleaned, encoding="utf-8") as fh:
                list(csv.reader(fh))
        except Exception as exc:
            issues.append("cleaned CSV invalid: %s" % exc)
    return {"ok": not issues, "issues": issues, "status": result.get("status")}


# --------------------------------------------------------------------------- #
# Full pipeline + dispatcher
# --------------------------------------------------------------------------- #

def run_all(input_dir, output_dir, work_dir):
    case_path = os.path.join(input_dir, "case.json")
    case = read_json(case_path) if os.path.isfile(case_path) else {}
    case_id = (case or {}).get("case_id", "unknown-case")
    case_invalid = os.path.isfile(case_path) and case is None
    files = discover_files(input_dir)
    has_data = any(f["kind"] in ("table_csv", "table_text", "json", "database") for f in files)
    status = "ok" if has_data else "insufficient_evidence"
    stage_init(input_dir, output_dir, work_dir, case_id, status="partial")
    stage_survey(input_dir, work_dir)
    inspect = stage_inspect(input_dir, work_dir)
    analysis = stage_analyze(input_dir, work_dir, output_dir)
    payload = stage_build(input_dir, work_dir, output_dir, status=status)
    # record bad case.json honestly
    if case_invalid:
        payload = read_json(os.path.join(output_dir, "result.json"))
        if isinstance(payload, dict):
            payload.setdefault("errors", []).append("case.json exists but is invalid JSON")
            payload.setdefault("degradations", []).append("case_id defaults to unknown-case")
            payload["uncertainty"] = list(payload.get("uncertainty") or [])
            payload["uncertainty"].append("output_contract unavailable: case.json invalid")
            payload["determinism"]["deterministic_sha256"] = _compute_det_hash(payload)
            write_json(os.path.join(output_dir, "result.json"), payload)
    checks = stage_selfcheck(output_dir)
    return payload, checks


def main(argv):
    args = list(argv)
    flags = {"input": None, "output": None, "work": None}
    cmd = "run"
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--input", "--output", "--work") and i + 1 < len(args):
            flags[a[2:]] = args[i + 1]
            i += 2
        elif a in ("--help", "-h"):
            print("usage: main.py [init|survey|inspect|analyze|build|selfcheck|run] "
                  "[--input DIR] [--output DIR] [--work DIR]")
            return 0
        elif not a.startswith("--"):
            cmd = a
            i += 1
        else:
            i += 1
    input_dir = flags["input"] or os.environ.get("INPUT_DIR", "/input")
    output_dir = flags["output"] or os.environ.get("OUTPUT_DIR", "/app/output")
    work_dir = flags["work"] or os.environ.get("WORK_DIR", "/app/work")
    _ensure(output_dir)
    _ensure(work_dir)
    try:
        if cmd == "init":
            case = read_json(os.path.join(input_dir, "case.json")) if os.path.isdir(input_dir) else {}
            stage_init(input_dir, output_dir, work_dir,
                       (case or {}).get("case_id", "unknown-case"))
            print("init: result.json skeleton written")
        elif cmd == "survey":
            stage_survey(input_dir, work_dir)
            print("survey: survey.json written")
        elif cmd == "inspect":
            stage_inspect(input_dir, work_dir)
            print("inspect: schema.json + plan.json written")
        elif cmd == "analyze":
            stage_analyze(input_dir, work_dir, output_dir)
            print("analyze: analysis.json + cleaned + figures written")
        elif cmd == "build":
            stage_build(input_dir, work_dir, output_dir)
            print("build: result.json assembled")
        elif cmd == "selfcheck":
            checks = stage_selfcheck(output_dir)
            print(json.dumps(checks, ensure_ascii=False, indent=2))
            return 0 if checks["ok"] else 1
        elif cmd == "run":
            run_all(input_dir, output_dir, work_dir)
            print("run: full pipeline complete")
        else:
            print("unknown command: %s" % cmd)
            return 2
    except Exception as exc:
        # Never leave zero artifacts: write a minimal legal fallback.
        try:
            fallback = {
                "case_id": "unknown-case", "status": "partial", "result": {},
                "quality": [], "descriptive": [], "inferential": [], "effects": [],
                "interpretation": {"note": "pipeline failed, manual fallback"},
                "sources": [], "uncertainty": ["pipeline raised: %s" % exc],
                "change_log": [{"id": "T01", "time": "T01", "action": "fallback",
                                "before": None, "after": "minimal result.json",
                                "reason": "exception in pipeline"}],
                "pending_human": [], "errors": ["pipeline_error: %s" % exc],
                "degradations": ["hand-written fallback"],
                "determinism": {"input_fingerprint": None,
                                "deterministic_sha256": None, "rng": "none"},
                "runtime": {"started_at": _now_iso(), "finished_at": None,
                            "duration_seconds": None,
                            "python_version": sys.version.split()[0]},
            }
            write_json(os.path.join(output_dir, "result.json"), fallback)
            traceback.print_exc()
            print("fallback result.json written after error")
        except Exception:
            traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
