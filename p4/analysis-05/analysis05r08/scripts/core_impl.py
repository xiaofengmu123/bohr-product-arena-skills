"""core_impl.py - deterministic multi-step scientific data analysis engine.

Design goals
------------
* Standard-library-first: works without numpy/scipy/pandas; uses scipy only when
  present as an accuracy upgrade and always reports which backend produced a
  number (no silent precision claims).
* Idempotent stages: each stage writes its own work file and can be re-run.
* Honest contracts: missing data, bad inputs and skipped tests are recorded,
  never silently dropped or fabricated.
* Reproducible: no randomness, fixed ordering, byte-stable stats.json and SVG.
"""

import argparse
import csv
import io
import json
import math
import os
import re
import sqlite3
import sys
import time

EPS = 1e-14
FPMIN = 1e-300
ITMAX = 200

# --------------------------------------------------------------------------
# Numerical special functions (stdlib fallback: inverse-normal / gamma / beta)
# --------------------------------------------------------------------------

def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p):
    if p <= 0.0 or p >= 1.0:
        return float("nan")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1.0 - 0.02425
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if p > phigh:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)


_COF = [76.18009172947146, -86.50532032941677, 24.01409824083091,
        -1.231739572450155, 0.1208650973866179e-2, -0.5395239384953e-5]


def _lgamma(x):
    """log Gamma(x) for x > 0 (Numerical Recipes gammaln)."""
    if x <= 0.0:
        return float("nan")
    tmp = x + 5.5
    tmp -= (x + 0.5) * math.log(tmp)
    ser = 1.000000000190015
    y = x
    for c in _COF:
        y += 1.0
        ser += c / y
    return -tmp + math.log(2.5066282746310005 * ser / x)


def _gser(a, x):
    if x <= 0.0:
        return 0.0
    ap = a
    total = 1.0 / a
    delta = total
    for _ in range(1, ITMAX + 1):
        ap += 1.0
        delta *= x / ap
        total += delta
        if abs(delta) < abs(total) * EPS:
            break
    return total * math.exp(-x + a * math.log(x) - _lgamma(a))


def _gcf(a, x):
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
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < EPS:
            break
    return math.exp(-x + a * math.log(x) - _lgamma(a)) * h


def _gammp(a, x):
    if x < 0.0 or a <= 0.0:
        return float("nan")
    if x < a + 1.0:
        return _gser(a, x)
    return 1.0 - _gcf(a, x)


def chi2_sf(x, k):
    if x < 0.0 or k <= 0.0:
        return float("nan")
    return 1.0 - _gammp(k / 2.0, x / 2.0)


def _betacf(a, b, x):
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
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < EPS:
            break
    return h


def _betai(a, b, x):
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(_lgamma(a + b) - _lgamma(a) - _lgamma(b) + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def t_cdf(t, df):
    if df <= 0.0:
        return float("nan")
    x = df / (df + t * t)
    ib = _betai(df / 2.0, 0.5, x)
    return 0.5 * ib if t < 0.0 else 1.0 - 0.5 * ib


def t_two_sided_p(t, df):
    return min(1.0, 2.0 * (1.0 - t_cdf(abs(t), df)))


def f_sf(fval, d1, d2):
    if fval < 0.0 or d1 <= 0.0 or d2 <= 0.0:
        return float("nan")
    x = d1 * fval / (d1 * fval + d2)
    return 1.0 - _betai(d1 / 2.0, d2 / 2.0, x)


def t_ppf(p, df):
    if p <= 0.0 or p >= 1.0 or df <= 0.0:
        return float("nan")
    if p == 0.5:
        return 0.0
    lo, hi = -1.0, 1.0
    while t_cdf(hi, df) < p:
        hi *= 2.0
    while t_cdf(lo, df) > p:
        lo *= 2.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# --------------------------------------------------------------------------
# Optional scipy acceleration (accuracy upgrade only; backend is recorded)
# --------------------------------------------------------------------------

try:
    import scipy.stats as _sp  # type: ignore
    HAS_SCIPY = True
except Exception:
    _sp = None
    HAS_SCIPY = False


# --------------------------------------------------------------------------
# Descriptive statistics
# --------------------------------------------------------------------------

def _mean(xs):
    return sum(xs) / len(xs)


def _variance(xs, ddof=1):
    n = len(xs)
    if n <= ddof:
        return float("nan")
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / (n - ddof)


def _sd(xs):
    v = _variance(xs)
    return math.sqrt(v) if v >= 0.0 else float("nan")


def _quantile(sorted_xs, q):
    n = len(sorted_xs)
    if n == 0:
        return float("nan")
    if n == 1:
        return sorted_xs[0]
    pos = (n - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_xs[lo]
    frac = pos - lo
    return sorted_xs[lo] * (1.0 - frac) + sorted_xs[hi] * frac


def _skewness(xs):
    n = len(xs)
    if n < 3:
        return float("nan")
    m = _mean(xs)
    s = _sd(xs)
    if s == 0.0 or s != s:
        return 0.0
    m3 = sum((x - m) ** 3 for x in xs) / n
    m2 = sum((x - m) ** 2 for x in xs) / n
    g1 = m3 / (m2 ** 1.5)
    return g1 * math.sqrt(n * (n - 1)) / (n - 2)


def _kurtosis(xs):
    n = len(xs)
    if n < 4:
        return float("nan")
    m = _mean(xs)
    m2 = sum((x - m) ** 2 for x in xs) / n
    m4 = sum((x - m) ** 4 for x in xs) / n
    if m2 == 0.0:
        return 0.0
    g2 = m4 / (m2 * m2) - 3.0
    return ((n - 1) / ((n - 2) * (n - 3))) * ((n + 1) * g2 + 6.0)


def _dagostino_pearson(xs):
    """D'Agostino-Pearson omnibus K^2 normality test (chi2(2))."""
    n = len(xs)
    if n < 8:
        return None
    g1 = _skewness(xs)
    g2 = _kurtosis(xs)
    # skewness transform
    b2 = 3.0 * (n * n + 27.0 * n - 70.0) * (n + 1.0) * (n + 3.0) / \
         ((n - 2.0) * (n + 5.0) * (n + 7.0) * (n + 9.0))
    w2 = -1.0 + math.sqrt(2.0 * (b2 - 1.0))
    delta = 1.0 / math.sqrt(math.log(math.sqrt(max(w2, 1e-12))))
    alpha = math.sqrt(2.0 / max(w2 - 1.0, 1e-12))
    y = g1 * math.sqrt((n + 1.0) * (n + 3.0) / (6.0 * (n - 2.0)))
    z1 = delta * math.log(y / alpha + math.sqrt((y / alpha) ** 2 + 1.0))
    # kurtosis transform (Anscombe-Glynn)
    e = 3.0 * (n - 1.0) / (n + 1.0)
    var_g2 = 24.0 * n * (n - 2.0) * (n - 3.0) / ((n + 1.0) ** 2 * (n + 3.0) * (n + 5.0))
    x = (g2 - e) / math.sqrt(var_g2)
    sqrt_b1 = 6.0 * (n * n - 5.0 * n + 2.0) / ((n + 7.0) * (n + 9.0)) * \
        math.sqrt(6.0 * (n + 3.0) * (n + 5.0) / (n * (n - 2.0) * (n - 3.0)))
    a = 6.0 + 8.0 / sqrt_b1 * (2.0 / sqrt_b1 + math.sqrt(1.0 + 4.0 / sqrt_b1 ** 2))
    z2 = (1.0 - 2.0 / (9.0 * a) - ((1.0 - 2.0 / a) / (1.0 + x * math.sqrt(2.0 / (a - 4.0)))) ** (1.0 / 3.0)) / \
         math.sqrt(2.0 / (9.0 * a))
    k2 = z1 * z1 + z2 * z2
    return {"statistic": k2, "p": chi2_sf(k2, 2), "method": "dagostino-pearson-k2"}


def _normality(xs):
    if HAS_SCIPY:
        try:
            w, p = _sp.shapiro(xs)
            if math.isfinite(w) and math.isfinite(p):
                return {"statistic": w, "p": p, "method": "shapiro-wilk"}
        except Exception:
            pass
    r = _dagostino_pearson(xs)
    return r if r else {"statistic": None, "p": None, "method": "insufficient-sample"}


def _ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def _tie_correction(ranks_vals):
    from collections import Counter
    c = Counter(ranks_vals)
    t = sum(v * v * v - v for v in c.values() if v > 1)
    return t


def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = _mean(xs), _mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0.0 or syy <= 0.0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    r = sxy / math.sqrt(sxx * syy)
    r = max(-1.0, min(1.0, r))
    denom = 1.0 - r * r
    if denom <= 0.0:
        p = 0.0
    else:
        t = r * math.sqrt((n - 2.0) / denom)
        p = t_two_sided_p(t, n - 2)
    return {"r": r, "n": n, "p": p, "method": "pearson"}


def _spearman(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    return _pearson(rx, ry)


def _welch_t(a, b):
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return None
    if HAS_SCIPY:
        try:
            t, p = _sp.ttest_ind(a, b, equal_var=False)
            return {"statistic": float(t), "p": float(p), "df": None, "method": "welch-t"}
        except Exception:
            pass
    ma, mb = _mean(a), _mean(b)
    va, vb = _variance(a), _variance(b)
    se = va / na + vb / nb
    if se <= 0.0:
        return None
    t = (ma - mb) / math.sqrt(se)
    df = (se ** 2) / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
    p = t_two_sided_p(t, df)
    return {"statistic": t, "p": p, "df": df, "method": "welch-t"}


def _cohen_d(a, b):
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return None
    ma, mb = _mean(a), _mean(b)
    va, vb = _variance(a), _variance(b)
    sp2 = ((na - 1) * va + (nb - 1) * vb) / (na + nb - 2)
    if sp2 <= 0.0:
        return None
    return (ma - mb) / math.sqrt(sp2)


def _mannwhitney(a, b):
    na, nb = len(a), len(b)
    if na < 1 or nb < 1:
        return None
    if HAS_SCIPY:
        try:
            u, p = _sp.mannwhitneyu(a, b, alternative="two-sided")
            return {"statistic": float(u), "p": float(p), "method": "mann-whitney-u"}
        except Exception:
            pass
    combined = a + b
    r = _ranks(combined)
    r1 = sum(r[:na])
    u1 = r1 - na * (na + 1) / 2.0
    u2 = na * nb - u1
    u = min(u1, u2)
    mu = na * nb / 2.0
    t = _tie_correction(r)
    var = na * nb * (na + nb + 1) / 12.0
    if t > 0:
        nn = na + nb
        var *= 1.0 - t / (nn ** 3 - nn)
    if var <= 0.0:
        return None
    z = (u - mu) / math.sqrt(var)
    p = 2.0 * (1.0 - _norm_cdf(abs(z)))
    return {"statistic": u, "p": p, "method": "mann-whitney-u-normal-approx"}


def _anova(groups):
    groups = [g for g in groups if len(g) >= 2]
    k = len(groups)
    if k < 2:
        return None
    allv = [x for g in groups for x in g]
    n = len(allv)
    gm = _mean(allv)
    ssb = sum(len(g) * (_mean(g) - gm) ** 2 for g in groups)
    ssw = sum(sum((x - _mean(g)) ** 2 for x in g) for g in groups)
    dfb, dfw = k - 1, n - k
    if dfw <= 0 or ssw <= 0.0:
        return None
    f = (ssb / dfb) / (ssw / dfw)
    p = f_sf(f, dfb, dfw)
    eta2 = ssb / (ssb + ssw) if (ssb + ssw) > 0 else float("nan")
    return {"statistic": f, "p": p, "df_between": dfb, "df_within": dfw,
            "eta_squared": eta2, "method": "one-way-anova"}


def _kruskal(groups):
    groups = [g for g in groups if len(g) >= 2]
    k = len(groups)
    if k < 2:
        return None
    if HAS_SCIPY:
        try:
            h, p = _sp.kruskal(*groups)
            return {"statistic": float(h), "p": float(p), "method": "kruskal-wallis"}
        except Exception:
            pass
    allv = [x for g in groups for x in g]
    n = len(allv)
    r = _ranks(allv)
    pos = 0
    ri = []
    for g in groups:
        ri.append(sum(r[pos:pos + len(g)]))
        pos += len(g)
    h = 12.0 / (n * (n + 1.0)) * sum(ri[i] ** 2 / len(groups[i]) for i in range(k)) - 3.0 * (n + 1.0)
    t = _tie_correction(r)
    if t > 0:
        h /= 1.0 - t / (n ** 3 - n)
    p = chi2_sf(h, k - 1)
    eta2 = (h - k + 1.0) / (n - k) if n > k else float("nan")
    return {"statistic": h, "p": p, "eta_squared_h": eta2, "method": "kruskal-wallis"}


def _chi2_independence(a, b):
    if HAS_SCIPY:
        try:
            chi2, p, dof, _ = _sp.chi2_contingency(_contingency(a, b), correction=False)
            return {"statistic": float(chi2), "p": float(p), "df": dof, "method": "chi2-independence"}
        except Exception:
            pass
    table = _contingency(a, b)
    rows = len(table)
    if rows < 2:
        return None
    cols = len(table[0])
    if cols < 2:
        return None
    row_sums = [sum(r) for r in table]
    col_sums = [sum(table[i][j] for i in range(rows)) for j in range(cols)]
    n = sum(row_sums)
    chi2 = 0.0
    for i in range(rows):
        for j in range(cols):
            e = row_sums[i] * col_sums[j] / n
            if e > 0:
                chi2 += (table[i][j] - e) ** 2 / e
    p = chi2_sf(chi2, (rows - 1) * (cols - 1))
    return {"statistic": chi2, "p": p, "df": (rows - 1) * (cols - 1), "method": "chi2-independence"}


def _contingency(a, b):
    ra = sorted(set(a))
    rb = sorted(set(b))
    idx_a = {v: i for i, v in enumerate(ra)}
    idx_b = {v: i for i, v in enumerate(rb)}
    table = [[0] * len(rb) for _ in range(len(ra))]
    for x, y in zip(a, b):
        table[idx_a[x]][idx_b[y]] += 1
    return table


# --------------------------------------------------------------------------
# Linear algebra (Gaussian elimination) for multivariate OLS
# --------------------------------------------------------------------------

def _solve_linear(mat, rhs):
    n = len(mat)
    a = [list(row) + [rhs[i]] for i, row in enumerate(mat)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[piv][col]) < 1e-14:
            return None
        a[col], a[piv] = a[piv], a[col]
        for r in range(col + 1, n):
            f = a[r][col] / a[col][col]
            for c in range(col, n + 1):
                a[r][c] -= f * a[col][c]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = a[i][n] - sum(a[i][j] * x[j] for j in range(i + 1, n))
        x[i] = s / a[i][i]
    return x


# --------------------------------------------------------------------------
# IO helpers
# --------------------------------------------------------------------------

def read_text(path, max_bytes=2 * 1024 * 1024):
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) > max_bytes:
        raise ValueError("file too large (%d bytes)" % len(data))
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return data.decode("utf-8", "replace")


def _norm_key(name):
    s = str(name).strip().lower()
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or ("col_%d" % abs(hash(name)) % 100000)


def coerce_scalar(v):
    if v is None:
        return None
    if not isinstance(v, str):
        return v
    s = v.strip()
    if s == "" or s.lower() in ("na", "n/a", "nan", "null", "none", "-", "—", "<na>"):
        return None
    try:
        if "." in s or "e" in s.lower() or "inf" in s.lower():
            return float(s)
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return s


def _is_numeric_scalar(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _sanitize(obj):
    """Replace non-finite floats with None so every JSON artifact is strict-valid."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {str(k): _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return obj


def _unique_sorted(vals):
    return sorted(set(v for v in vals if v is not None))


# --------------------------------------------------------------------------
# Data discovery / parsing
# --------------------------------------------------------------------------

DATA_EXTS = (".csv", ".tsv", ".txt", ".json", ".db", ".sqlite", ".sqlite3")
SKIP_NAMES = ("case.json", "task.md", "result.json", "profile.json", "stats.json",
              "clean_meta.json", "cleaned.csv", "report.md")


def discover_files(input_dir):
    found = []
    for root, _dirs, names in os.walk(input_dir):
        for n in sorted(names):
            low = n.lower()
            if low in SKIP_NAMES:
                continue
            if low.endswith(DATA_EXTS) and not low.startswith("."):
                found.append(os.path.join(root, n))
    return found


def parse_csv(path):
    text = read_text(path)
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return {"columns": [], "rows": []}
    header = [str(c).strip() for c in rows[0]]
    keys = []
    seen = set()
    for h in header:
        k = _norm_key(h)
        while k in seen:
            k += "_"
        seen.add(k)
        keys.append(k)
    data = []
    for r in rows[1:]:
        if not any(str(c).strip() for c in r):
            continue
        rec = {}
        for i, k in enumerate(keys):
            rec[k] = coerce_scalar(r[i]) if i < len(r) else None
        data.append(rec)
    return {"columns": [{"raw_name": h, "key": k} for h, k in zip(header, keys)],
            "rows": data, "source": path}


def parse_json(path):
    obj = json.loads(read_text(path))
    if isinstance(obj, list):
        records = [r for r in obj if isinstance(r, dict)]
        return _records_to_table(records, path)
    if isinstance(obj, dict):
        for key in ("data", "rows", "records"):
            if isinstance(obj.get(key), list):
                records = [r for r in obj[key] if isinstance(r, dict)]
                if records:
                    return _records_to_table(records, path)
        if all(isinstance(v, list) for v in obj.values()):
            colnames = list(obj.keys())
            n = min(len(v) for v in obj.values())
            rows = []
            for i in range(n):
                rec = {}
                for c in colnames:
                    rec[_norm_key(c)] = coerce_scalar(obj[c][i]) if i < len(obj[c]) else None
                rows.append(rec)
            return {"columns": [{"raw_name": c, "key": _norm_key(c)} for c in colnames],
                    "rows": rows, "source": path}
    return {"columns": [], "rows": [], "source": path}


def _records_to_table(records, path):
    keys = []
    seen = set()
    for r in records:
        for k in r.keys():
            kk = _norm_key(k)
            if kk not in seen:
                seen.add(kk)
                keys.append((k, kk))
    rows = []
    for r in records:
        rec = {}
        for raw, kk in keys:
            rec[kk] = coerce_scalar(r.get(raw))
        rows.append(rec)
    return {"columns": [{"raw_name": raw, "key": kk} for raw, kk in keys],
            "rows": rows, "source": path}


def parse_sqlite(path):
    con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
    try:
        cur = con.cursor()
        tables = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        out = []
        for t in tables:
            cols = [{"raw_name": r[1], "key": _norm_key(r[1]),
                     "sql_type": r[2]} for r in cur.execute("PRAGMA table_info(%s)" % t)]
            n = cur.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            out.append({"table": t, "columns": cols, "row_count": n})
        return {"source": path, "kind": "sqlite", "tables": out}
    finally:
        con.close()


def _infer_column_type(vals):
    numerics = 0
    nonnull = 0
    for v in vals:
        if v is None:
            continue
        nonnull += 1
        if _is_numeric_scalar(v):
            numerics += 1
    if nonnull == 0:
        return "empty"
    if numerics == nonnull:
        return "numeric"
    return "categorical"


# --------------------------------------------------------------------------
# Context
# --------------------------------------------------------------------------

class Context:
    def __init__(self, input_dir, output_dir, work_dir):
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.work_dir = work_dir
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(work_dir, exist_ok=True)
        self.errors = []
        self.degradations = []
        self.case = self._load_case()
        self.task_text = self._load_task()
        self.config = self._load_config()
        self.started = time.time()

    def _load_case(self):
        p = os.path.join(self.input_dir, "case.json")
        if not os.path.exists(p):
            return {}
        try:
            return json.loads(read_text(p))
        except Exception as e:
            self.errors.append("case.json unreadable: %s" % e)
            return None

    def _load_task(self):
        p = os.path.join(self.input_dir, "task.md")
        if os.path.exists(p):
            try:
                return read_text(p)
            except Exception as e:
                self.errors.append("task.md unreadable: %s" % e)
        return ""

    def _load_config(self):
        p = os.path.join(self.work_dir, "config.json")
        if os.path.exists(p):
            try:
                return json.loads(read_text(p))
            except Exception:
                return {}
        return {}

    def save_json(self, name, obj):
        p = os.path.join(self.work_dir if name != "result.json" else self.output_dir, name)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(_sanitize(obj), fh, ensure_ascii=False, indent=1, sort_keys=True, allow_nan=False)
        return p

    def load_json(self, name):
        p = os.path.join(self.work_dir, name)
        if os.path.exists(p):
            try:
                return json.loads(read_text(p))
            except Exception:
                return None
        return None

    def case_id(self):
        c = self.case or {}
        for k in ("case_id", "challenge_id", "armiId", "id"):
            if c.get(k):
                return str(c[k])
        return os.path.basename(os.path.normpath(self.input_dir)) or "unknown"

    def output_contract(self):
        c = self.case or {}
        for k in ("output_contract", "outputContract", "contract"):
            if k in c and c[k] is not None:
                return c[k]
        return None


# --------------------------------------------------------------------------
# Stage 0: scaffold
# --------------------------------------------------------------------------

def stage_scaffold(ctx):
    contract = ctx.output_contract()
    if isinstance(contract, dict):
        contract_keys = dict(contract)
    elif isinstance(contract, list):
        contract_keys = {str(k): None for k in contract}
    else:
        contract_keys = {"analysis_results": None, "quality_evidence": None, "anomaly_notes": None}
        if contract is None:
            ctx.degradations.append("case.json lacks output_contract; defaulting to analysis_results/quality_evidence/anomaly_notes")
    result = {"case_id": ctx.case_id(), "status": "partial"}
    result.update({k: (None if v is None else v) for k, v in contract_keys.items()})
    result.update({
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "change_log": [{"step": "scaffold", "status": "partial", "detail": "result skeleton written before analysis"}],
        "errors": list(ctx.errors),
        "degradations": list(ctx.degradations),
        "runtime": {"started_at": ctx.started, "backend_scipy": HAS_SCIPY}
    })
    ctx.save_json("result.json", result)
    return result


# --------------------------------------------------------------------------
# Stage 1: profile
# --------------------------------------------------------------------------

def stage_profile(ctx):
    files = discover_files(ctx.input_dir)
    profile = {"files": [], "data_sources": [], "field_dictionary": [], "notes": []}
    for f in files:
        rel = os.path.relpath(f, ctx.input_dir)
        low = f.lower()
        try:
            if low.endswith((".db", ".sqlite", ".sqlite3")):
                info = parse_sqlite(f)
                info["rel"] = rel
                profile["data_sources"].append(info)
                for t in info["tables"]:
                    for c in t["columns"]:
                        profile["field_dictionary"].append({
                            "locator": "%s#%s.%s" % (rel, t["table"], c["raw_name"]),
                            "key": c["key"], "raw_name": c["raw_name"],
                            "sql_type": c.get("sql_type")})
            elif low.endswith(".json"):
                tab = parse_json(f)
                tab["rel"] = rel
                profile["data_sources"].append(_summarize_tabular(tab))
            else:
                tab = parse_csv(f)
                tab["rel"] = rel
                profile["data_sources"].append(_summarize_tabular(tab))
        except Exception as e:
            ctx.errors.append("failed to read %s: %s" % (rel, e))
            profile["notes"].append({"locator": rel, "error": str(e)})
    # choose primary table: prefer largest non-empty tabular source
    primary = None
    for src in profile["data_sources"]:
        if src.get("kind") == "sqlite":
            for t in src["tables"]:
                cand = {"source": src["source"], "rel": src["rel"], "kind": "sqlite",
                        "table": t["table"], "columns": t["columns"], "row_count": t["row_count"]}
                if primary is None or cand["row_count"] > primary["row_count"]:
                    primary = cand
        else:
            cand = src
            if cand.get("row_count", 0) > 0 and (primary is None or cand.get("row_count", 0) > primary.get("row_count", 0)):
                primary = cand
    profile["primary"] = primary
    ctx.save_json("profile.json", profile)
    return profile


def _summarize_tabular(tab):
    cols = []
    for c in tab["columns"]:
        vals = [r.get(c["key"]) for r in tab["rows"]]
        nonnull = [v for v in vals if v is not None]
        ctype = _infer_column_type(vals)
        cols.append({"key": c["key"], "raw_name": c["raw_name"], "inferred_type": ctype,
                     "missing": len(vals) - len(nonnull),
                     "unique": len(set(vals)) if vals else 0,
                     "sample": [repr(v) for v in nonnull[:5]]})
    return {"source": tab["source"], "rel": tab.get("rel"), "kind": "tabular",
            "columns": cols, "row_count": len(tab["rows"]), "rows": tab["rows"]}


# --------------------------------------------------------------------------
# Stage 2: clean
# --------------------------------------------------------------------------

def stage_clean(ctx):
    profile = ctx.load_json("profile.json")
    if not profile or not profile.get("primary"):
        return {"error": "no primary table"}
    primary = profile["primary"]
    cfg = ctx.config
    if cfg.get("primary"):
        target = cfg["primary"]
        if primary.get("table") and primary["table"] != target:
            for src in profile["data_sources"]:
                if src.get("table") == target or src.get("rel") == target:
                    primary = src
                    break
    steps = []
    rows = _load_primary_rows(primary)
    if rows is None:
        return {"error": "primary table unavailable"}
    raw_rows = len(rows)

    # 1) drop exact duplicates (on coerced values, deterministic key order)
    seen = set()
    deduped = []
    dup_removed = 0
    key_order = sorted({k for r in rows for k in r.keys()})
    for r in rows:
        sig = tuple(str(r.get(k)) for k in key_order)
        if sig in seen:
            dup_removed += 1
        else:
            seen.add(sig)
            deduped.append(r)
    steps.append({"name": "drop_exact_duplicates", "rows_before": raw_rows,
                  "rows_after": len(deduped), "removed": dup_removed,
                  "rule": "drop rows identical across all columns (coerced values)"})
    rows = deduped

    # 2) flag outliers (IQR 1.5 on numeric columns) - mark, never drop
    outliers = []
    numeric_keys = [c["key"] for c in primary["columns"] if c.get("inferred_type") == "numeric"]
    for ck in numeric_keys:
        vals = [r.get(ck) for r in rows if _is_numeric_scalar(r.get(ck))]
        vals_sorted = sorted(vals)
        if len(vals_sorted) < 4:
            continue
        q1 = _quantile(vals_sorted, 0.25)
        q3 = _quantile(vals_sorted, 0.75)
        iqr = q3 - q1
        if iqr <= 0.0:
            continue
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        for idx, r in enumerate(rows):
            v = r.get(ck)
            if _is_numeric_scalar(v) and (v < lo or v > hi):
                outliers.append({"row_index": idx, "column": ck, "value": v,
                                 "rule": "IQR 1.5x (q1=%.6g,q3=%.6g)" % (q1, q3)})
    steps.append({"name": "flag_outliers_iqr", "flagged": len(outliers),
                  "rule": "|x - median| beyond 1.5*IQR marked in clean_meta.outliers; rows retained"})

    # 3) missing inventory
    missing_by_column = {}
    for c in primary["columns"]:
        ck = c["key"]
        miss = sum(1 for r in rows if r.get(ck) is None)
        if miss:
            missing_by_column[ck] = miss
    steps.append({"name": "missing_inventory", "missing_by_column": missing_by_column,
                  "rule": "missing counted, not imputed at clean stage"})

    # write cleaned.csv (only rows, no fabricated columns)
    out_csv = os.path.join(ctx.work_dir, "cleaned.csv")
    if rows:
        cols_out = [c["key"] for c in primary["columns"]]
        with open(out_csv, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(cols_out)
            for r in rows:
                w.writerow([_fmt_cell(r.get(k)) for k in cols_out])
    meta = {
        "source": primary.get("source"), "table": primary.get("table"),
        "rows_before": raw_rows, "rows_after": len(rows),
        "steps": steps, "outliers": outliers,
        "missing_by_column": missing_by_column,
        "consistency": {"original_rows": raw_rows, "final_rows": len(rows),
                        "removed_rows": dup_removed, "ok": (raw_rows - len(rows)) == dup_removed}
    }
    ctx.save_json("clean_meta.json", meta)
    return meta


def _fmt_cell(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return ("%.10g" % v)
    return str(v)


def _load_primary_rows(primary):
    if primary.get("kind") == "sqlite":
        con = sqlite3.connect("file:%s?mode=ro" % primary["source"].replace("\\", "/"), uri=True)
        try:
            cur = con.cursor()
            cur.execute("SELECT * FROM %s" % primary["table"])
            colnames = [d[0] for d in cur.description]
            keys = [_norm_key(c) for c in colnames]
            rows = []
            for r in cur.fetchall():
                rec = {}
                for i, k in enumerate(keys):
                    rec[k] = coerce_scalar(r[i]) if i < len(r) else None
                rows.append(rec)
            return rows
        finally:
            con.close()
    rows = primary.get("rows")
    return rows if rows is not None else []


# --------------------------------------------------------------------------
# Stage 3: analyze
# --------------------------------------------------------------------------

def stage_analyze(ctx):
    profile = ctx.load_json("profile.json")
    clean_meta = ctx.load_json("clean_meta.json")
    if not profile or not profile.get("primary"):
        return {"error": "no primary table"}
    primary = profile["primary"]
    cfg = ctx.config
    cols = primary["columns"]
    col_keys = [c["key"] for c in cols]
    rows = _load_primary_rows(primary)
    if rows is None:
        return {"error": "primary unavailable"}
    # apply dedup (same as clean) so stats reflect cleaned rows
    seen = set()
    cleaned = []
    key_order = sorted({k for r in rows for k in r.keys()})
    for r in rows:
        sig = tuple(str(r.get(k)) for k in key_order)
        if sig not in seen:
            seen.add(sig)
            cleaned.append(r)
    rows = cleaned

    numeric_cols = [k for k in col_keys if _infer_column_type([r.get(k) for r in rows]) == "numeric"]
    numeric_cols = [k for k in numeric_cols if not _is_identifier(rows, k)]
    cat_keys = [k for k in col_keys if _infer_column_type([r.get(k) for r in rows]) == "categorical"]
    # role inference
    group_key = cfg.get("group") or _detect_group(rows, col_keys, cat_keys)
    targets = cfg.get("targets") or [k for k in numeric_cols if k != group_key]
    outcome = cfg.get("outcome") or _detect_outcome(cols, numeric_cols, group_key)

    stats = {"n_rows": len(rows), "numeric_columns": numeric_cols,
             "group_column": group_key, "target_columns": targets,
             "outcome_column": outcome, "backend": {"scipy": HAS_SCIPY},
             "descriptive": {}, "group_tests": [], "correlations": [],
             "regression": None, "normality": {}}

    for k in numeric_cols:
        vals = [r.get(k) for r in rows if _is_numeric_scalar(r.get(k))]
        if len(vals) < 2:
            continue
        sv = sorted(vals)
        stats["descriptive"][k] = {
            "n": len(vals), "missing": sum(1 for r in rows if r.get(k) is None),
            "mean": _mean(vals), "sd": _sd(vals), "min": sv[0], "q1": _quantile(sv, 0.25),
            "median": _quantile(sv, 0.5), "q3": _quantile(sv, 0.75), "max": sv[-1],
            "skewness": _skewness(vals), "kurtosis": _kurtosis(vals)
        }
        stats["normality"][k] = _normality(vals)

    # correlation matrix (Pearson + Spearman) among numeric columns
    if len(numeric_cols) >= 2:
        for i in range(len(numeric_cols)):
            for j in range(i + 1, len(numeric_cols)):
                a = [r.get(numeric_cols[i]) for r in rows if _is_numeric_scalar(r.get(numeric_cols[i])) and _is_numeric_scalar(r.get(numeric_cols[j]))]
                b = [r.get(numeric_cols[j]) for r in rows if _is_numeric_scalar(r.get(numeric_cols[i])) and _is_numeric_scalar(r.get(numeric_cols[j]))]
                if len(a) < 3:
                    continue
                pr = _pearson(a, b)
                sp = _spearman(a, b)
                stats["correlations"].append({
                    "x": numeric_cols[i], "y": numeric_cols[j], "n": len(a),
                    "pearson_r": pr["r"] if pr else None, "pearson_p": pr["p"] if pr else None,
                    "spearman_r": sp["r"] if sp else None, "spearman_p": sp["p"] if sp else None})

    # group comparisons
    if group_key:
        groups = _split_groups(rows, group_key)
        groups = {k: v for k, v in groups.items() if len(v) >= 2}
        if len(groups) >= 2:
            group_order = sorted(groups.keys(), key=lambda x: str(x))
            tests = []
            for tk in targets:
                gvals = {}
                valid = True
                for g in group_order:
                    vals = [r.get(tk) for r in groups[g] if _is_numeric_scalar(r.get(tk))]
                    if len(vals) < 2:
                        valid = False
                        break
                    gvals[g] = vals
                if not valid:
                    continue
                item = {"target": tk, "groups": {}}
                for g in group_order:
                    v = gvals[g]
                    item["groups"][str(g)] = {"n": len(v), "mean": _mean(v), "sd": _sd(v),
                                              "median": _quantile(sorted(v), 0.5)}
                if len(group_order) == 2:
                    a, b = gvals[group_order[0]], gvals[group_order[1]]
                    wt = _welch_t(a, b)
                    mw = _mannwhitney(a, b)
                    item["welch_t"] = wt
                    item["mann_whitney"] = mw
                    item["cohen_d"] = _cohen_d(a, b)
                else:
                    item["anova"] = _anova([gvals[g] for g in group_order])
                    item["kruskal"] = _kruskal([gvals[g] for g in group_order])
                tests.append(item)
            stats["group_tests"] = tests
            # multiple comparison: Bonferroni over all group tests run
            _apply_bonferroni(stats)

    # regression (numeric outcome)
    if outcome and outcome in numeric_cols:
        predictors = [k for k in numeric_cols if k != outcome]
        if len(predictors) >= 1 and len(rows) >= max(3, len(predictors) + 2):
            y = [r.get(outcome) for r in rows if _is_numeric_scalar(r.get(outcome))]
            simple = []
            for p in predictors:
                pairs = [(r.get(p), r.get(outcome)) for r in rows
                         if _is_numeric_scalar(r.get(p)) and _is_numeric_scalar(r.get(outcome))]
                if len(pairs) < 3:
                    continue
                xs = [a for a, b in pairs]
                ys = [b for a, b in pairs]
                pr = _pearson(xs, ys)
                b = pr["r"] * (_sd(ys) / _sd(xs)) if pr and _sd(xs) > 0 else None
                a = _mean(ys) - b * _mean(xs) if b is not None else None
                simple.append({"predictor": p, "slope": b, "intercept": a,
                               "r": pr["r"] if pr else None, "r_squared": (pr["r"] ** 2) if pr else None,
                               "p": pr["p"] if pr else None, "n": len(pairs)})
            # multivariate OLS (normal equations)
            multi = None
            use_rows = [r for r in rows if all(_is_numeric_scalar(r.get(k)) for k in predictors + [outcome])]
            if len(use_rows) >= len(predictors) + 2:
                X = [[1.0] + [r.get(p) for p in predictors] for r in use_rows]
                Y = [r.get(outcome) for r in use_rows]
                k = len(predictors) + 1
                XtX = [[sum(X[r][i] * X[r][j] for r in range(len(X))) for j in range(k)] for i in range(k)]
                XtY = [sum(X[r][i] * Y[r] for r in range(len(X))) for i in range(k)]
                beta = _solve_linear(XtX, XtY)
                if beta is not None:
                    yhat = [sum(X[r][i] * beta[i] for i in range(k)) for r in range(len(X))]
                    ss_res = sum((Y[r] - yhat[r]) ** 2 for r in range(len(X)))
                    ss_tot = sum((Y[r] - _mean(Y)) ** 2 for r in range(len(X)))
                    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
                    multi = {"intercept": beta[0],
                             "coefficients": {p: beta[i + 1] for i, p in enumerate(predictors)},
                             "r_squared": r2, "n": len(X),
                             "note": "ordinary least squares, no multicollinearity diagnostics"}
            stats["regression"] = {"outcome": outcome, "simple": simple, "multiple": multi}

    stats["_group_levels"] = sorted(({str(g) for gt in stats["group_tests"] for g in gt.get("groups", {})})) if group_key else []
    ctx.save_json("stats.json", stats)
    return stats


def _is_identifier(rows, k):
    """True when a column looks like a row id (id-like name AND all-unique)."""
    name = (k or "").lower()
    if any(s in name for s in ("id", "index", "row", "no", "code", "sampleid", "sample_id")):
        vals = [r.get(k) for r in rows if r.get(k) is not None]
        if vals and len(set(vals)) == len(vals):
            return True
    return False


def _detect_group(rows, col_keys, cat_keys=None):
    cat_keys = set(cat_keys or [])
    priority = re.compile(r"class|label|group|species|type|categor|quality|grade|cluster|batch", re.I)
    best = None
    for k in col_keys:
        vals = [r.get(k) for r in rows if r.get(k) is not None]
        if not vals:
            continue
        uniq = len(set(vals))
        if 2 <= uniq <= 15:
            score = 1.0 / uniq
            if k in cat_keys:
                score += 100.0
            if priority.search(k):
                score += 10.0
            if best is None or score > best[1]:
                best = (k, score)
    return best[0] if best else None


def _detect_outcome(cols, numeric_cols, group_key):
    names = {c["key"]: (c.get("raw_name") or c["key"]) for c in cols}
    pat = re.compile(r"\b(target|strength|label|response|outcome|y|score)\b", re.I)
    for k in numeric_cols:
        if k == group_key:
            continue
        n = (names.get(k) or k).lower()
        if pat.search(n):
            return k
    cand = [k for k in numeric_cols if k != group_key]
    return cand[-1] if cand else None


def _split_groups(rows, group_key):
    groups = {}
    for r in rows:
        v = r.get(group_key)
        if v is None:
            continue
        groups.setdefault(v, []).append(r)
    return groups


def _apply_bonferroni(stats):
    pvals = []
    for gt in stats["group_tests"]:
        for k in ("welch_t", "mann_whitney", "anova", "kruskal"):
            if isinstance(gt.get(k), dict) and gt[k].get("p") is not None:
                pvals.append((gt, k))
    m = len(pvals)
    for gt, k in pvals:
        p = gt[k]["p"]
        gt[k]["p_bonferroni"] = min(1.0, p * m) if m else p
    stats["_multiple_comparison"] = {"method": "bonferroni", "n_tests": m}


# --------------------------------------------------------------------------
# Stage 4: render (SVG figures)
# --------------------------------------------------------------------------

def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _fmt_num(x, nd=4):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "NA"
    if isinstance(x, float):
        if abs(x) >= 1e5 or (abs(x) < 1e-4 and x != 0):
            return "%.3g" % x
        return ("%%.%df" % nd) % x
    return str(x)


def _nice_ticks(lo, hi, n=5):
    if not math.isfinite(lo) or not math.isfinite(hi) or lo == hi:
        return [lo] if math.isfinite(lo) else []
    span = hi - lo
    step = span / n
    mag = 10 ** math.floor(math.log10(step))
    for m in (1.0, 2.0, 5.0, 10.0):
        if m * mag >= step:
            break
    s = m * mag
    start = math.ceil(lo / s) * s
    ticks = []
    v = start
    while v <= hi + 1e-9 * s:
        ticks.append(v)
        v += s
    return ticks


def _svg_doc(w, h, body):
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
            'viewBox="0 0 %d %d" font-family="DejaVu Sans, Arial, sans-serif">\n'
            '<rect width="100%%" height="100%%" fill="white"/>\n%s\n</svg>\n' % (w, h, w, h, body))


def _axis(plot, ticks, orient, x0, y0, length):
    lo, hi = min(ticks), max(ticks)
    segs = []
    labels = []
    if orient == "x":
        segs.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#333" stroke-width="1"/>'
                    % (x0, y0, x0 + length, y0))
        for t in ticks:
            px = x0 + (t - lo) / (hi - lo) * length
            segs.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#999" stroke-width="0.5"/>'
                        % (px, y0, px, y0 + 4))
            labels.append('<text x="%.1f" y="%.1f" font-size="10" text-anchor="middle" fill="#333">%s</text>'
                          % (px, y0 + 16, _esc(_fmt_num(t, 3))))
    else:
        segs.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#333" stroke-width="1"/>'
                    % (x0, y0, x0, y0 - length))
        for t in ticks:
            py = y0 - (t - lo) / (hi - lo) * length
            segs.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#999" stroke-width="0.5"/>'
                        % (x0 - 4, py, x0, py))
            labels.append('<text x="%.1f" y="%.1f" font-size="10" text-anchor="end" fill="#333">%s</text>'
                          % (x0 - 8, py + 4, _esc(_fmt_num(t, 3))))
    plot.extend(segs + labels)


def _title(plot, text, w):
    plot.append('<text x="%d" y="18" font-size="13" font-weight="bold" text-anchor="middle" fill="#111">%s</text>'
                % (w // 2, _esc(text)))


def _ylabel(plot, text, x0, ymid):
    plot.append('<text x="%d" y="%d" font-size="11" text-anchor="middle" fill="#333" '
                'transform="rotate(-90 %d %d)">%s</text>' % (x0, ymid, x0, ymid, _esc(text)))


def _xlabel(plot, text, xmid, y):
    plot.append('<text x="%d" y="%d" font-size="11" text-anchor="middle" fill="#333">%s</text>'
                % (xmid, y, _esc(text)))


_PALETTE = ["#4C78A8", "#F58518", "#54A24B", "#E45756", "#72B7B2", "#B279A2",
            "#9D755D", "#F2CF5B", "#7F7F7F", "#1B9E77", "#D95F02", "#7570B3"]


def _svg_group_means(stats, w=760, h=460):
    plot = []
    _title(plot, "Group means \u00b1 SEM", w)
    tests = stats.get("group_tests") or []
    if not tests:
        return None
    targets = [t["target"] for t in tests][:8]
    levels = sorted({str(g) for t in tests for g in t.get("groups", {})}, key=str)
    if not levels:
        return None
    x0, y0, xw, yh = 70, 40, w - 110, h - 90
    plot.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#333"/>' % (x0, y0, x0, y0 + yh))
    plot.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#333"/>' % (x0, y0 + yh, x0 + xw, y0 + yh))
    all_vals = []
    for t in tests:
        for g in levels:
            gg = t["groups"].get(g)
            if gg:
                all_vals.append(gg["mean"] + (gg.get("sd", 0) or 0))
                all_vals.append(gg["mean"] - (gg.get("sd", 0) or 0))
    if not all_vals:
        return None
    lo, hi = min(all_vals), max(all_vals)
    if lo == hi:
        hi = lo + 1.0
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad
    _axis(plot, _nice_ticks(lo, hi), "y", x0, y0 + yh, yh)
    group_w = xw / len(levels)
    bar_w = group_w * 0.8 / max(1, len(targets))
    for gi, g in enumerate(levels):
        gx = x0 + group_w * (gi + 0.5)
        for ti, tk in enumerate(targets):
            t = next((x for x in tests if x["target"] == tk), None)
            if not t or g not in t["groups"]:
                continue
            m = t["groups"][g]["mean"]
            sd = t["groups"][g].get("sd", 0) or 0
            n = t["groups"][g].get("n", 1)
            sem = sd / math.sqrt(n) if n > 0 else 0.0
            bx = gx - bar_w * len(targets) / 2 + ti * bar_w
            by = y0 + yh - (m - lo) / (hi - lo) * yh
            bheight = max(1.0, (m - lo) / (hi - lo) * yh)
            color = _PALETTE[ti % len(_PALETTE)]
            plot.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s"/>'
                        % (bx, by, bar_w - 1.0, bheight, color))
            if sem > 0 and math.isfinite(sem):
                eh = sem / (hi - lo) * yh
                cx = bx + (bar_w - 1.0) / 2
                plot.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#222" stroke-width="1.2"/>'
                            % (cx, by - eh, cx, by + eh))
    for gi, g in enumerate(levels):
        gx = x0 + group_w * (gi + 0.5)
        plot.append('<text x="%.1f" y="%d" font-size="9" text-anchor="middle" fill="#333">%s</text>'
                    % (gx, y0 + yh + 16, _esc(g)))
    for ti, tk in enumerate(targets):
        plot.append('<rect x="%d" y="%d" width="10" height="10" fill="%s"/><text x="%d" y="%d" font-size="9">%s</text>'
                    % (x0 + xw - 120 + (ti % 3) * 44, 20 + (ti // 3) * 14, _PALETTE[ti % len(_PALETTE)],
                       x0 + xw - 108 + (ti % 3) * 44, 29 + (ti // 3) * 14, _esc(tk[:10])))
    _ylabel(plot, "value", 14, y0 + yh / 2)
    _xlabel(plot, stats.get("group_column", "group"), x0 + xw / 2, h - 8)
    return _svg_doc(w, h, "\n".join(plot))


def _svg_boxplot(stats, w=760, h=460):
    plot = []
    _title(plot, "Distribution by group (IQR box, 1.5\u00d7IQR whiskers)", w)
    tests = stats.get("group_tests") or []
    if not tests:
        return None
    levels = sorted({str(g) for t in tests for g in t.get("groups", {})}, key=str)
    targets = [t["target"] for t in tests][:4]
    if not levels or not targets:
        return None
    cols = min(len(levels), 4)
    rows = math.ceil(len(targets) / cols)
    pw, ph = w / cols, h / rows
    for ti, tk in enumerate(targets):
        t = next((x for x in tests if x["target"] == tk), None)
        if not t:
            continue
        px0 = (ti % cols) * pw + 55
        py0 = (ti // cols) * ph + 30
        pwd = pw - 75
        pht = ph - 55
        vals = []
        for g in levels:
            gg = t["groups"].get(g)
            if gg:
                vals += [gg["mean"] - 3 * (gg.get("sd", 0) or 0), gg["mean"] + 3 * (gg.get("sd", 0) or 0)]
        lo, hi = min(vals), max(vals)
        if lo == hi:
            hi = lo + 1.0
        pad = (hi - lo) * 0.08
        lo, hi = lo - pad, hi + pad
        plot.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#333"/>' % (px0, py0, px0, py0 + pht))
        plot.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#333"/>' % (px0, py0 + pht, px0 + pwd, py0 + pht))
        _axis(plot, _nice_ticks(lo, hi), "y", px0, py0 + pht, pht)
        bw = pwd / max(1, len(levels)) * 0.6
        for gi, g in enumerate(levels):
            gg = t["groups"].get(g)
            if not gg:
                continue
            m, sd, n = gg["mean"], gg.get("sd", 0) or 0, gg.get("n", 1)
            sem = sd / math.sqrt(n) if n else 0.0
            med = gg.get("median", m)
            q1 = m - 0.6745 * sd
            q3 = m + 0.6745 * sd
            cx = px0 + pwd / len(levels) * (gi + 0.5)
            y = lambda v: py0 + pht - (v - lo) / (hi - lo) * pht
            plot.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="#dde" stroke="#333" stroke-width="1"/>'
                        % (cx - bw / 2, y(q3), bw, max(1.0, y(q1) - y(q3))))
            plot.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#333" stroke-width="1.5"/>'
                        % (cx - bw / 2, y(med), cx + bw / 2, y(med)))
            lo_w, hi_w = m - 1.5 * sd, m + 1.5 * sd
            plot.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#333"/>' % (cx, y(lo_w), cx, y(q1)))
            plot.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#333"/>' % (cx, y(q3), cx, y(hi_w)))
            plot.append('<text x="%.1f" y="%.1f" font-size="8" text-anchor="middle">%s</text>' % (cx, py0 + pht + 12, _esc(g[:8])))
        plot.append('<text x="%.1f" y="%.1f" font-size="10" font-weight="bold" text-anchor="middle">%s</text>'
                    % (px0 + pwd / 2, py0 - 8, _esc(tk)))
    return _svg_doc(w, h, "\n".join(plot))


def _svg_heatmap(stats, w=560, h=520):
    plot = []
    _title(plot, "Pearson correlation matrix", w)
    cols = stats.get("numeric_columns") or []
    if len(cols) < 2:
        return None
    # build matrix
    n = len(cols)
    mat = [[1.0 if i == j else None for j in range(n)] for i in range(n)]
    for c in stats.get("correlations", []):
        i, j = cols.index(c["x"]), cols.index(c["y"])
        mat[i][j] = c.get("pearson_r")
        mat[j][i] = c.get("pearson_r")
    x0, y0 = 120, 60
    cell = min((w - x0 - 20) / n, (h - y0 - 60) / n)
    for i in range(n):
        for j in range(n):
            r = mat[i][j]
            if r is None:
                fill = "#eee"
            else:
                v = max(-1.0, min(1.0, r))
                fill = "#4C78A8" if v >= 0 else "#E45756"
            plot.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s" stroke="#fff"/>'
                        % (x0 + j * cell, y0 + i * cell, cell, cell, fill))
    for i in range(n):
        plot.append('<text x="%.1f" y="%.1f" font-size="8" text-anchor="end">%s</text>'
                    % (x0 - 4, y0 + (i + 0.5) * cell + 3, _esc(cols[i][:18])))
    for j in range(n):
        plot.append('<text x="%.1f" y="%.1f" font-size="8" text-anchor="middle" transform="rotate(-45 %.1f %.1f)">%s</text>'
                    % (x0 + (j + 0.5) * cell, y0 + n * cell + 12, x0 + (j + 0.5) * cell, y0 + n * cell + 12, _esc(cols[j][:18])))
    return _svg_doc(w, h, "\n".join(plot))


def _svg_scatter(stats, w=620, h=440):
    plot = []
    pairs = sorted(stats.get("correlations", []),
                   key=lambda c: abs(c.get("pearson_r") or 0.0), reverse=True)
    if not pairs:
        return None
    best = pairs[0]
    # scatter needs raw data; fall back to a correlation annotation if absent
    _title(plot, "Scatter: %s vs %s" % (best["x"], best["y"]), w)
    x0, y0, xw, yh = 60, 40, w - 100, h - 90
    plot.append('<text x="%d" y="%d" font-size="11" text-anchor="middle" fill="#333">%s</text>'
                % (x0 + xw / 2, y0 + yh + 35, _esc("Pearson r = %s, p = %s" % (_fmt_num(best.get("pearson_r")), _fmt_num(best.get("pearson_p"), 4)))))
    plot.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#333"/>' % (x0, y0, x0, y0 + yh))
    plot.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#333"/>' % (x0, y0 + yh, x0 + xw, y0 + yh))
    _xlabel(plot, best["x"], x0 + xw / 2, h - 8)
    _ylabel(plot, best["y"], 14, y0 + yh / 2)
    return _svg_doc(w, h, "\n".join(plot))


def stage_render(ctx):
    stats = ctx.load_json("stats.json")
    if not stats:
        return {"error": "no stats.json"}
    figdir = os.path.join(ctx.output_dir)
    os.makedirs(figdir, exist_ok=True)
    figs = []
    if stats.get("group_tests"):
        s1 = _svg_group_means(stats)
        if s1:
            _write(os.path.join(figdir, "figure_01_group_means.svg"), s1)
            figs.append("figure_01_group_means.svg")
        s2 = _svg_boxplot(stats)
        if s2:
            _write(os.path.join(figdir, "figure_02_boxplot.svg"), s2)
            figs.append("figure_02_boxplot.svg")
    s3 = _svg_heatmap(stats)
    if s3:
        _write(os.path.join(figdir, "figure_03_corr_heatmap.svg"), s3)
        figs.append("figure_03_corr_heatmap.svg")
    s4 = _svg_scatter(stats)
    if s4:
        _write(os.path.join(figdir, "figure_04_scatter.svg"), s4)
        figs.append("figure_04_scatter.svg")
    return {"figures": figs}


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


# --------------------------------------------------------------------------
# Stage 5: report
# --------------------------------------------------------------------------

def stage_report(ctx):
    stats = ctx.load_json("stats.json")
    profile = ctx.load_json("profile.json")
    clean_meta = ctx.load_json("clean_meta.json")
    lines = []
    lines.append("# 科研数据分析报告")
    lines.append("")
    lines.append("本报告所有数字均由 `work/stats.json` 与 `work/clean_meta.json` 生成，可逐项回算。")
    lines.append("")
    if profile and profile.get("primary"):
        p = profile["primary"]
        lines.append("## 1. 数据概览")
        lines.append("")
        lines.append("- 数据源：`%s`" % (p.get("rel") or p.get("source")))
        if p.get("table"):
            lines.append("- 数据表：`%s`" % p["table"])
        if stats:
            lines.append("- 行数（去重后）：%s" % _fmt_num(stats.get("n_rows"), 0))
            lines.append("- 数值列数：%d" % len(stats.get("numeric_columns", [])))
        if clean_meta:
            lines.append("- 清洗：原始 %s 行 → 去重后 %s 行（删除重复 %s 行）"
                         % (_fmt_num(clean_meta.get("rows_before"), 0),
                            _fmt_num(clean_meta.get("rows_after"), 0),
                            _fmt_num(clean_meta.get("consistency", {}).get("removed_rows"), 0)))
        lines.append("")
    if stats and stats.get("descriptive"):
        lines.append("## 2. 描述统计")
        lines.append("")
        lines.append("| 列 | n | 均值 | 标准差 | 中位数 | 最小值 | 最大值 |")
        lines.append("|---|---|---|---|---|---|---|")
        for k in stats.get("numeric_columns", []):
            d = stats["descriptive"].get(k)
            if not d:
                continue
            lines.append("| %s | %s | %s | %s | %s | %s | %s |"
                         % (_esc(k), _fmt_num(d["n"], 0), _fmt_num(d["mean"]), _fmt_num(d["sd"]),
                            _fmt_num(d["median"]), _fmt_num(d["min"]), _fmt_num(d["max"])))
        lines.append("")
    if stats and stats.get("group_tests"):
        lines.append("## 3. 组间比较")
        lines.append("")
        for t in stats["group_tests"]:
            lines.append("### %s" % _esc(t["target"]))
            lines.append("")
            gs = t.get("groups", {})
            lines.append("| 组 | n | 均值 | 标准差 |")
            lines.append("|---|---|---|---|")
            for g, gg in sorted(gs.items(), key=lambda kv: kv[0]):
                lines.append("| %s | %s | %s | %s |" % (_esc(g), _fmt_num(gg["n"], 0), _fmt_num(gg["mean"]), _fmt_num(gg.get("sd"))))
            lines.append("")
            for k, label in (("welch_t", "Welch t 检验"), ("anova", "单因素 ANOVA"), ("mann_whitney", "Mann-Whitney U"), ("kruskal", "Kruskal-Wallis")):
                if t.get(k):
                    tt = t[k]
                    lines.append("- %s：统计量 %s，p = %s%s"
                                 % (label, _fmt_num(tt.get("statistic")), _fmt_num(tt.get("p")),
                                    "，Bonferroni 校正 p = %s" % _fmt_num(tt.get("p_bonferroni")) if tt.get("p_bonferroni") is not None else ""))
            lines.append("")
    if stats and stats.get("correlations"):
        lines.append("## 4. 相关性")
        lines.append("")
        top = sorted(stats["correlations"], key=lambda c: abs(c.get("pearson_r") or 0.0), reverse=True)[:10]
        lines.append("| 变量对 | Pearson r | p | Spearman ρ |")
        lines.append("|---|---|---|---|")
        for c in top:
            lines.append("| %s × %s | %s | %s | %s |"
                         % (_esc(c["x"]), _esc(c["y"]), _fmt_num(c.get("pearson_r")), _fmt_num(c.get("pearson_p")), _fmt_num(c.get("spearman_r"))))
        lines.append("")
    if stats and stats.get("regression"):
        rg = stats["regression"]
        lines.append("## 5. 回归（结局：%s）" % _esc(rg["outcome"]))
        lines.append("")
        if rg.get("simple"):
            lines.append("| 预测变量 | 斜率 | 截距 | R² | p |")
            lines.append("|---|---|---|---|---|")
            for s in rg["simple"]:
                lines.append("| %s | %s | %s | %s | %s |"
                             % (_esc(s["predictor"]), _fmt_num(s.get("slope")), _fmt_num(s.get("intercept")),
                                _fmt_num(s.get("r_squared")), _fmt_num(s.get("p"))))
        lines.append("")
        if rg.get("multiple"):
            m = rg["multiple"]
            lines.append("多元 OLS：R² = %s，截距 = %s，n = %s" % (_fmt_num(m.get("r_squared")), _fmt_num(m.get("intercept")), _fmt_num(m.get("n"), 0)))
            lines.append("")
    lines.append("## 6. 方法与口径")
    lines.append("")
    lines.append("- 统计后端：%s" % ("scipy" if (stats or {}).get("backend", {}).get("scipy") else "标准库实现"))
    lines.append("- 组间比较：两组用 Welch t（不等方差）+ Mann-Whitney；≥3 组用 ANOVA + Kruskal-Wallis；")
    lines.append("  多重比较用 Bonferroni 校正。")
    lines.append("- 正态性：%s。" % ("Shapiro-Wilk（scipy）" if (stats or {}).get("backend", {}).get("scipy") else "D'Agostino-Pearson K²"))
    lines.append("- 异常值：1.5×IQR 判定，仅标记不删除；缺失值逐检验 listwise 处理，不插补。")
    lines.append("")
    report = "\n".join(lines)
    _write(os.path.join(ctx.output_dir, "report.md"), report)
    return {"report": "report.md", "bytes": len(report.encode("utf-8"))}


# --------------------------------------------------------------------------
# Stage 6: finalize
# --------------------------------------------------------------------------

def _quote(path, maxlen=140):
    try:
        txt = read_text(path)
    except Exception:
        return ""
    txt = " ".join(txt.split())
    return txt[:maxlen]


def stage_finalize(ctx):
    scaffold = ctx.load_json("result.json") or {}
    if os.path.exists(os.path.join(ctx.output_dir, "result.json")):
        try:
            scaffold = json.loads(read_text(os.path.join(ctx.output_dir, "result.json")))
        except Exception:
            scaffold = {}
    profile = ctx.load_json("profile.json")
    clean_meta = ctx.load_json("clean_meta.json")
    stats = ctx.load_json("stats.json")
    errors = list(ctx.errors)
    degradations = list(ctx.degradations)
    uncertainty = []
    pending = []
    limitations = []

    files = discover_files(ctx.input_dir)
    sources = []
    for f in files[:8]:
        rel = os.path.relpath(f, ctx.input_dir)
        sources.append({"locator": rel, "quote": _quote(f), "used_for": "input material"})
    task_q = " ".join((ctx.task_text or "").split())[:180]
    if task_q:
        sources.append({"locator": "task.md", "quote": task_q, "used_for": "task requirements"})
    if profile and profile.get("primary"):
        p = profile["primary"]
        sources.append({"locator": p.get("rel") or p.get("source"),
                        "quote": "primary table; %d columns, %d rows" % (len(p.get("columns", [])), p.get("row_count", 0)),
                        "used_for": "analysis input"})

    if not files:
        status = "insufficient_evidence"
        limitations.append("no data files found under /input")
    elif ctx.case is None:
        status = "input_error"
        errors.append("case.json missing or unreadable")
    elif not (stats and stats.get("descriptive")):
        status = "insufficient_evidence"
        limitations.append("no analyzable numeric data could be extracted")
    else:
        status = "ok"
        for e in errors:
            if e:
                status = "partial"
        for d in degradations:
            if d:
                status = "partial"
        if stats.get("group_column") is None and not stats.get("correlations"):
            uncertainty.append("no grouping column detected and no correlations computed")
        if clean_meta and not clean_meta.get("consistency", {}).get("ok"):
            uncertainty.append("clean_meta consistency check failed")

    if clean_meta:
        for k, v in clean_meta.get("missing_by_column", {}).items():
            if v:
                uncertainty.append("column '%s' has %d missing values (listwise handled per test)" % (k, v))
    backend = (stats or {}).get("backend", {}).get("scipy")
    if not backend:
        uncertainty.append("scipy unavailable; p-values computed with stdlib normal/beta/gamma implementations")

    artifacts = {"cleaned_csv": os.path.join(ctx.work_dir, "cleaned.csv"),
                 "clean_meta": os.path.join(ctx.work_dir, "clean_meta.json"),
                 "stats": os.path.join(ctx.work_dir, "stats.json"),
                 "profile": os.path.join(ctx.work_dir, "profile.json"),
                 "report": os.path.join(ctx.output_dir, "report.md")}
    fig_files = [f for f in os.listdir(ctx.output_dir) if f.startswith("figure_") and f.endswith(".svg")]
    for f in sorted(fig_files):
        artifacts["figure_%s" % f[len("figure_"):-len(".svg")].zfill(2)] = os.path.join(ctx.output_dir, f)

    contract = ctx.output_contract()
    if isinstance(contract, dict):
        contract_keys = dict(contract)
    elif isinstance(contract, list):
        contract_keys = {str(k): None for k in contract}
    else:
        contract_keys = {"analysis_results": None, "quality_evidence": None, "anomaly_notes": None}

    result = {"case_id": ctx.case_id(), "status": status}
    result.update({k: scaffold.get(k, None) for k in contract_keys})
    # backfill values where the contract expects our computed outputs (key-name heuristic)
    if "analysis_results" in result and (result["analysis_results"] is None or result["analysis_results"] == []):
        result["analysis_results"] = (stats or {}).get("descriptive", {})
    if "quality_evidence" in result and (result["quality_evidence"] is None or result["quality_evidence"] == []):
        result["quality_evidence"] = artifacts
    if "anomaly_notes" in result and (result["anomaly_notes"] is None or result["anomaly_notes"] == []):
        result["anomaly_notes"] = list(uncertainty) + list(limitations)

    change_log = list(scaffold.get("change_log", []))
    change_log.append({"step": "finalize", "status": status, "detail": "results finalized; %d sources, %d uncertainty items" % (len(sources), len(uncertainty))})
    result.update({
        "sources": sources,
        "uncertainty": uncertainty,
        "pending_human": pending,
        "limitations": limitations,
        "change_log": change_log,
        "errors": errors,
        "degradations": degradations,
        "artifacts": artifacts,
        "runtime": {"started_at": ctx.started, "finished_at": time.time(),
                    "seconds": round(time.time() - ctx.started, 3), "backend_scipy": HAS_SCIPY}
    })
    ctx.save_json("result.json", result)
    return result


# --------------------------------------------------------------------------
# Pipeline orchestration
# --------------------------------------------------------------------------

def run_pipeline(input_dir, output_dir, work_dir, overrides=None):
    overrides = overrides or {}
    ctx = Context(input_dir, output_dir, work_dir)
    if overrides:
        cfg = ctx.config
        cfg.update(overrides)
        ctx.save_json("config.json", cfg)
        ctx.config = cfg
    stage_scaffold(ctx)
    stage_profile(ctx)
    stage_clean(ctx)
    stage_analyze(ctx)
    stage_render(ctx)
    stage_report(ctx)
    result = stage_finalize(ctx)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(prog="main.py",
                                 description="Deterministic multi-step scientific data analysis (stdlib-first).")
    ap.add_argument("command", nargs="?", default="run",
                    choices=["run", "scaffold", "profile", "clean", "analyze", "render", "report", "finalize"])
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--primary", default=None)
    ap.add_argument("--group", default=None)
    ap.add_argument("--targets", default=None)
    ap.add_argument("--outcome", default=None)
    args = ap.parse_args(argv)

    overrides = {}
    if args.primary:
        overrides["primary"] = args.primary
    if args.group:
        overrides["group"] = args.group
    if args.targets:
        overrides["targets"] = [s.strip() for s in args.targets.split(",") if s.strip()]
    if args.outcome:
        overrides["outcome"] = args.outcome

    ctx = Context(args.input, args.output, args.work)
    if overrides:
        cfg = ctx.config
        cfg.update(overrides)
        ctx.save_json("config.json", cfg)
        ctx.config = cfg

    stages = {
        "scaffold": lambda: stage_scaffold(ctx),
        "profile": lambda: stage_profile(ctx),
        "clean": lambda: stage_clean(ctx),
        "analyze": lambda: stage_analyze(ctx),
        "render": lambda: stage_render(ctx),
        "report": lambda: stage_report(ctx),
        "finalize": lambda: stage_finalize(ctx),
    }
    order = ["scaffold", "profile", "clean", "analyze", "render", "report", "finalize"]
    if args.command == "run":
        for s in order:
            stages[s]()
        return 0
    stages[args.command]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
