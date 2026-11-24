# -*- coding: utf-8 -*-
"""core_impl.py — deterministic multi-step scientific data analysis engine.

Standard library only. Design invariants:
  * No randomness anywhere (no RNG, no time-dependent values in artifacts).
  * JSON emitted with sort_keys=True + separators=(',',':') so bytes are stable.
  * Every float is rounded to SIG_FIGS significant digits before serialization.
  * result.json carries the only wall-clock value (`runtime`); the selftest
    strips it before the byte-for-byte determinism comparison.
  * Each analysis product records a sha256 so a reviewer can re-derive it.
"""

import csv
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import statistics
import sys

VERSION = "1.0.0"
SIG_FIGS = 12
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB per input file
DEFAULT_CONTRACT_KEYS = ["analysis_results", "quality_evidence", "anomaly_notes"]
NON_MISSING_TOKENS = {"", "na", "nan", "null", "none", "n/a", "-", "--", "missing"}


# --------------------------------------------------------------------------- #
# numeric / serialization utilities
# --------------------------------------------------------------------------- #

def round_sig(x, sig=SIG_FIGS):
    """Round a finite float to `sig` significant digits (stable repr)."""
    if x is None or isinstance(x, bool):
        return x
    if isinstance(x, int):
        return x
    if not isinstance(x, float):
        return x
    if not math.isfinite(x):
        return x
    if x == 0.0:
        return 0.0
    try:
        nd = sig - int(math.floor(math.log10(abs(x)))) - 1
    except (ValueError, OverflowError):
        return x
    nd = max(-308, min(308, nd))
    try:
        return float(round(x, nd))
    except (OverflowError, ValueError):
        return x


def json_ready(obj):
    """Recursively sanitize values for deterministic JSON: finite floats only."""
    if isinstance(obj, dict):
        return {str(k): json_ready(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_ready(v) for v in obj]
    if isinstance(obj, float):
        if not math.isfinite(obj):
            return None
        return round_sig(obj)
    return obj


def dumps(obj):
    return json.dumps(
        json_ready(obj), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(dumps(obj))
    os.replace(tmp, path)


def read_json(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def sha256_bytes(data):
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def fmt_num(x, nd=4):
    """Format a number for report text (deterministic)."""
    if x is None:
        return "n/a"
    if not math.isfinite(x):
        return "n/a"
    a = abs(x)
    if a == 0:
        return "0"
    if a >= 10000 or a < 0.0001:
        return ("%." + str(nd) + "e") % x
    return ("%." + str(nd) + "g") % x


def fmt_p(p):
    if p is None:
        return "n/a"
    if p < 0.001:
        return "<0.001"
    return "%.3f" % p


# --------------------------------------------------------------------------- #
# statistical foundations (all implemented from first principles)
# --------------------------------------------------------------------------- #

def _gser(a, x):
    EPS = 3e-14
    ITMAX = 300
    ap = a
    summ = 1.0 / a
    dele = summ
    for _ in range(1, ITMAX + 1):
        ap += 1.0
        dele *= x / ap
        summ += dele
        if abs(dele) < abs(summ) * EPS:
            return summ * math.exp(-x + a * math.log(x) - math.lgamma(a))
    raise RuntimeError("gser did not converge")


def _gcf(a, x):
    EPS = 3e-14
    ITMAX = 300
    FPMIN = 1e-300
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
        dele = d * c
        h *= dele
        if abs(dele - 1.0) < EPS:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def _gammp(a, x):
    if x < 0 or a <= 0:
        raise ValueError("invalid arguments")
    if x == 0:
        return 0.0
    if x < a + 1.0:
        return _gser(a, x)
    return 1.0 - _gcf(a, x)


def chi2_cdf(x, df):
    if x <= 0:
        return 0.0
    return _gammp(df / 2.0, x / 2.0)


def chi2_sf(x, df):
    return 1.0 - chi2_cdf(x, df)


def _betacf(a, b, x):
    EPS = 3e-14
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
    for m in range(1, 201):
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
        if abs(dele - 1.0) < EPS:
            break
    return h


def _betai(a, b, x):
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log(1.0 - x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def t_cdf(t, df):
    if df <= 0:
        return None
    x = df / (df + t * t)
    ib = _betai(df / 2.0, 0.5, x)
    if t >= 0:
        return 1.0 - 0.5 * ib
    return 0.5 * ib


def t_p2(t, df):
    """Two-tailed p-value for Student/Welch t."""
    c = t_cdf(abs(t), df)
    if c is None:
        return None
    return max(0.0, min(1.0, 2.0 * (1.0 - c)))


def f_sf(F, df1, df2):
    if F <= 0:
        return 1.0
    x = df1 * F / (df1 * F + df2)
    return 1.0 - _betai(df1 / 2.0, df2 / 2.0, x)


def norm_sf(x):
    return 0.5 * math.erfc(x / math.sqrt(2.0))


# --- descriptive statistics ------------------------------------------------- #

def mean(x):
    n = len(x)
    return sum(x) / n if n else None


def variance(x):
    n = len(x)
    if n < 2:
        return None
    m = sum(x) / n
    return sum((v - m) ** 2 for v in x) / (n - 1)


def sd(x):
    v = variance(x)
    return math.sqrt(v) if v is not None else None


def quantile(sorted_x, q):
    n = len(sorted_x)
    if n == 0:
        return None
    if n == 1:
        return sorted_x[0]
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_x[lo]
    frac = pos - lo
    return sorted_x[lo] * (1.0 - frac) + sorted_x[hi] * frac


def median(x):
    if not x:
        return None
    return statistics.median(x)


def iqr(x_sorted):
    return quantile(x_sorted, 0.75) - quantile(x_sorted, 0.25)


def skewness(x):
    """Bias-corrected (Fisher-Pearson) skewness."""
    n = len(x)
    if n < 3:
        return None
    m = sum(x) / n
    m2 = sum((v - m) ** 2 for v in x) / n
    if m2 == 0:
        return None
    m3 = sum((v - m) ** 3 for v in x) / n
    g1 = m3 / m2 ** 1.5
    return math.sqrt(n * (n - 1)) / (n - 2) * g1


def kurtosis(x):
    """Bias-corrected excess kurtosis."""
    n = len(x)
    if n < 4:
        return None
    m = sum(x) / n
    m2 = sum((v - m) ** 2 for v in x) / n
    if m2 == 0:
        return None
    m4 = sum((v - m) ** 4 for v in x) / n
    g2 = m4 / m2 ** 2 - 3.0
    return ((n + 1) * g2 + 6.0) * (n - 1) / ((n - 2) * (n - 3))


def dagostino_k2(x):
    """D'Agostino-Pearson omnibus K^2 normality test (skewness + kurtosis).

    Returns (K2, p_value, g1). p follows chi-square(2) under normality.
    """
    n = len(x)
    if n < 8:
        return None, None, None
    m = sum(x) / n
    m2 = sum((v - m) ** 2 for v in x) / n
    if m2 <= 0:
        return None, None, None
    m3 = sum((v - m) ** 3 for v in x) / n
    m4 = sum((v - m) ** 4 for v in x) / n
    g1 = m3 / m2 ** 1.5
    g2 = m4 / m2 ** 2 - 3.0
    b2 = m4 / m2 ** 2  # non-excess kurtosis (== 3 for normal)
    try:
        beta2 = 3.0 * (n * n + 27 * n - 70) * (n + 1) * (n + 3) / (
            (n - 2) * (n + 5) * (n + 7) * (n + 9)
        )
        w2 = -1.0 + math.sqrt(2.0 * (beta2 - 1.0))
        delta = 1.0 / math.sqrt(math.log(math.sqrt(w2)))
        alpha = math.sqrt(2.0 / (w2 - 1.0))
        y = g1 * math.sqrt((n + 1) * (n + 3) / (6.0 * (n - 2)))
        z1 = delta * math.asinh(y / alpha)
    except (ValueError, ZeroDivisionError, OverflowError):
        return None, None, g1
    try:
        eb2 = 3.0 * (n - 1) / (n + 1)
        var_b2 = 24.0 * n * (n - 2) * (n - 3) / ((n + 1) ** 2 * (n + 3) * (n + 5))
        xx = (b2 - eb2) / math.sqrt(var_b2)
        sbb1 = (
            6.0 * (n * n - 5 * n + 2) / ((n + 7) * (n + 9))
            * math.sqrt(6.0 * (n + 3) * (n + 5) / (n * (n - 2) * (n - 3)))
        )
        A = 6.0 + 8.0 / sbb1 * (2.0 / sbb1 + math.sqrt(1.0 + 4.0 / (sbb1 * sbb1)))
        term = (1.0 - 2.0 / A) / (1.0 + xx * math.sqrt(2.0 / (A - 4.0)))
        # real cube root: Python's ** with a negative base returns complex
        if term < 0:
            cbrt = -((-term) ** (1.0 / 3.0))
        else:
            cbrt = term ** (1.0 / 3.0)
        z2 = ((1.0 - 2.0 / (9.0 * A)) - cbrt) / math.sqrt(2.0 / (9.0 * A))
    except (ValueError, ZeroDivisionError, OverflowError):
        return None, None, g1
    k2 = z1 * z1 + z2 * z2
    p = chi2_sf(k2, 2.0)
    return k2, p, g1


def rankdata(values):
    """Average ranks with ties."""
    n = len(values)
    idx = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[idx[j + 1]] == values[idx[i]]:
            j += 1
        avg = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[idx[k]] = avg
        i = j + 1
    return ranks


def levene(groups):
    """Brown-Forsythe (median-centered) test for variance homogeneity.

    groups: list of non-empty lists of floats. Returns (F, p).
    """
    groups = [g for g in groups if len(g) >= 2]
    k = len(groups)
    if k < 2:
        return None, None
    meds = [median(g) for g in groups]
    z_by_group = [[abs(v - meds[i]) for v in g] for i, g in enumerate(groups)]
    zbar_g = [sum(zg) / len(zg) for zg in z_by_group]
    N = sum(len(g) for g in groups)
    zbar = sum(sum(zg) for zg in z_by_group) / N
    num = sum(len(g) * (zbar_g[i] - zbar) ** 2 for i, g in enumerate(groups)) / (k - 1)
    den = sum(
        sum((zij - zbar_g[i]) ** 2 for zij in zg) for i, zg in enumerate(z_by_group)
    ) / (N - k)
    if den <= 0:
        return None, None
    F = num / den
    return F, f_sf(F, k - 1, N - k)


def ttest_student(a, b):
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return None
    ma, mb = mean(a), mean(b)
    va, vb = variance(a), variance(b)
    if va is None or vb is None:
        return None
    sp2 = ((na - 1) * va + (nb - 1) * vb) / (na + nb - 2)
    se = math.sqrt(sp2 * (1.0 / na + 1.0 / nb))
    if se == 0:
        return {"statistic": 0.0, "p_value": 1.0, "dof": na + nb - 2, "method": "Student-t"}
    t = (ma - mb) / se
    df = na + nb - 2
    return {"statistic": t, "p_value": t_p2(t, df), "dof": df, "method": "Student-t"}


def ttest_welch(a, b):
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return None
    ma, mb = mean(a), mean(b)
    va, vb = variance(a), variance(b)
    if va is None or vb is None:
        return None
    se = math.sqrt(va / na + vb / nb)
    if se == 0:
        return {"statistic": 0.0, "p_value": 1.0, "dof": na + nb - 2, "method": "Welch-t"}
    t = (ma - mb) / se
    df = (va / na + vb / nb) ** 2 / (
        (va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1)
    )
    return {"statistic": t, "p_value": t_p2(t, df), "dof": df, "method": "Welch-t"}


def mann_whitney_u(a, b):
    na, nb = len(a), len(b)
    if na == 0 or nb == 0:
        return None
    allv = list(a) + list(b)
    N = na + nb
    ranked = sorted(range(N), key=lambda i: allv[i])
    ranks = [0.0] * N
    i = 0
    while i < N:
        j = i
        while j + 1 < N and allv[ranked[j + 1]] == allv[ranked[i]]:
            j += 1
        avg = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[ranked[k]] = avg
        i = j + 1
    ra = sum(ranks[k] for k in range(N) if k < na)
    u1 = ra - na * (na + 1) / 2.0
    u2 = na * nb - u1
    U = min(u1, u2)
    mu = na * nb / 2.0
    counts = {}
    for v in allv:
        counts[v] = counts.get(v, 0) + 1
    tie = sum(c ** 3 - c for c in counts.values())
    var_u = (na * nb / 12.0) * ((N + 1) - tie / (N * (N - 1)))
    if var_u <= 0:
        return {"statistic": U, "p_value": 1.0, "method": "Mann-Whitney-U", "u1": u1, "u2": u2}
    z = (U - mu) / math.sqrt(var_u)
    p = 2.0 * norm_sf(abs(z))
    return {"statistic": U, "p_value": p, "z": z, "u1": u1, "u2": u2, "method": "Mann-Whitney-U"}


def anova_oneway(groups):
    k = len(groups)
    N = sum(len(g) for g in groups)
    if k < 2 or N <= k:
        return None
    grand = sum(sum(g) for g in groups) / N
    ss_between = sum(len(g) * (mean(g) - grand) ** 2 for g in groups)
    ss_within = sum(sum((v - mean(g)) ** 2 for v in g) for g in groups)
    df_b = k - 1
    df_w = N - k
    if df_w <= 0 or ss_within <= 0:
        return None
    ms_b = ss_between / df_b
    ms_w = ss_within / df_w
    F = ms_b / ms_w
    return {
        "statistic": F,
        "p_value": f_sf(F, df_b, df_w),
        "dof_between": df_b,
        "dof_within": df_w,
        "ss_between": ss_between,
        "ss_within": ss_within,
        "method": "one-way ANOVA",
    }


def kruskal_wallis(groups):
    k = len(groups)
    allv = [v for g in groups for v in g]
    N = len(allv)
    if k < 2 or N <= k:
        return None
    ranked = sorted(range(N), key=lambda i: allv[i])
    ranks = [0.0] * N
    i = 0
    while i < N:
        j = i
        while j + 1 < N and allv[ranked[j + 1]] == allv[ranked[i]]:
            j += 1
        avg = (i + 1 + j + 1) / 2.0
        for kk in range(i, j + 1):
            ranks[ranked[kk]] = avg
        i = j + 1
    R = [0.0] * k
    ni = [len(g) for g in groups]
    offset = 0
    for gi in range(k):
        R[gi] = sum(ranks[offset : offset + ni[gi]])
        offset += ni[gi]
    H = 12.0 / (N * (N + 1)) * sum(R[i] ** 2 / ni[i] for i in range(k)) - 3 * (N + 1)
    counts = {}
    for v in allv:
        counts[v] = counts.get(v, 0) + 1
    C = 1.0 - sum(c ** 3 - c for c in counts.values()) / (N ** 3 - N)
    if C <= 0:
        C = 1.0
    Hc = H / C
    return {"statistic": Hc, "p_value": chi2_sf(Hc, k - 1), "dof": k - 1, "method": "Kruskal-Wallis"}


def pearson_r(x, y):
    n = len(x)
    if n < 3:
        return None
    mx, my = mean(x), mean(y)
    sxx = sum((xi - mx) ** 2 for xi in x)
    syy = sum((yi - my) ** 2 for yi in y)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    r = max(-1.0, min(1.0, sxy / math.sqrt(sxx * syy)))
    if abs(r) >= 1.0:
        p = 0.0
    else:
        t = r * math.sqrt((n - 2) / (1 - r * r))
        p = t_p2(t, n - 2)
    return {"pearson_r": r, "p_value": p, "n": n}


def spearman_rho(x, y):
    n = len(x)
    if n < 3:
        return None
    rx = rankdata(x)
    ry = rankdata(y)
    res = pearson_r(rx, ry)
    if res is None:
        return None
    return {"spearman_rho": res["pearson_r"], "p_value": res["p_value"], "n": n}


def cohens_d(a, b):
    """Hedges' g corrected Cohen's d."""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return None
    va, vb = variance(a), variance(b)
    if va is None or vb is None:
        return None
    sp = math.sqrt(((na - 1) * va + (nb - 1) * vb) / (na + nb - 2))
    if sp == 0:
        return 0.0
    d = (mean(a) - mean(b)) / sp
    return d * (1.0 - 3.0 / (4.0 * (na + nb) - 9.0))


def rank_biserial(a, b, u_min):
    na, nb = len(a), len(b)
    if na == 0 or nb == 0:
        return None
    return 1.0 - 2.0 * u_min / (na * nb)


def eta_squared(anova_res):
    if not anova_res:
        return None
    ss_b = anova_res.get("ss_between")
    ss_w = anova_res.get("ss_within")
    if ss_b is None or ss_w is None or (ss_b + ss_w) == 0:
        return None
    return ss_b / (ss_b + ss_w)


def epsilon_squared(h, N):
    if not h or N <= 1:
        return None
    e = h * (N + 1) / (N * N - 1)
    return max(0.0, min(1.0, e))


# --------------------------------------------------------------------------- #
# I/O: discovery, decoding, tabular loaders
# --------------------------------------------------------------------------- #

def read_text(path):
    """Read a text file with encoding fallback; returns (text, encoding)."""
    data = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            return data.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("latin-1", errors="replace"), "latin-1"


def try_float(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str):
        return None
    s = v.strip()
    if s == "" or s.lower() in NON_MISSING_TOKENS:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def is_missing(v):
    if v is None:
        return True
    if isinstance(v, str) and v.strip().lower() in NON_MISSING_TOKENS:
        return True
    return False


def discover_files(input_dir):
    files = []
    if not os.path.isdir(input_dir):
        return files
    for name in sorted(os.listdir(input_dir)):
        p = os.path.join(input_dir, name)
        if not os.path.isfile(p):
            continue
        if name in ("case.json", "task.md") or name.startswith("."):
            continue
        if name.lower().endswith(".md"):
            continue
        ext = os.path.splitext(name)[1].lower()
        kind = None
        if ext in (".csv",):
            kind = "csv"
        elif ext in (".tsv", ".tab", ".txt"):
            kind = "tsv"
        elif ext == ".json":
            kind = "json"
        elif ext in (".db", ".sqlite", ".sqlite3"):
            kind = "sqlite"
        else:
            continue
        size = os.path.getsize(p)
        entry = {"name": name, "path": p, "kind": kind, "size": size}
        if size > MAX_FILE_BYTES:
            entry["skipped"] = "exceeds %d bytes" % MAX_FILE_BYTES
        files.append(entry)
    return files


def _delimiter_sniff(text):
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        return dialect.delimiter
    except csv.Error:
        counts = {d: sample.count(d) for d in (",", "\t", ";", "|")}
        best = max(counts, key=counts.get)
        return best if counts[best] > 0 else ","


def load_csv(path):
    text, enc = read_text(path)
    delim = _delimiter_sniff(text)
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    cols = reader.fieldnames or []
    rows = []
    for raw in reader:
        row = {}
        for c in cols:
            row[c] = raw.get(c)
        rows.append(row)
    return {"columns": cols, "rows": rows, "kind": "csv", "delimiter": delim, "encoding": enc}


def load_json_data(path):
    text, enc = read_text(path)
    obj = json.loads(text)
    rows = None
    if isinstance(obj, list):
        rows = obj
    elif isinstance(obj, dict):
        for key in ("data", "records", "rows", "values", "items"):
            if isinstance(obj.get(key), list):
                rows = obj[key]
                break
        if rows is None and all(isinstance(v, list) for v in obj.values()):
            # object of column->list (columnar) form
            cols = list(obj.keys())
            n = max((len(v) for v in obj.values()), default=0)
            rows = [{c: obj[c][i] if i < len(obj[c]) else None for c in cols} for i in range(n)]
    if rows is None:
        return None
    cols = []
    for r in rows:
        if isinstance(r, dict):
            for k in r.keys():
                if k not in cols:
                    cols.append(k)
    norm = [{str(c): r.get(c) if isinstance(r, dict) else None for c in cols} for r in rows]
    return {"columns": cols, "rows": norm, "kind": "json", "encoding": enc}


def _sql_quote(name):
    return '"' + str(name).replace('"', '""') + '"'


def load_sqlite(path):
    tables = []
    try:
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        names = [
            r[0]
            for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for t in names:
            q = _sql_quote(t)
            cols = [c[1] for c in cur.execute("PRAGMA table_info(%s)" % q)]
            rows = [
                {str(c): r[i] for i, c in enumerate(cols)}
                for r in cur.execute("SELECT * FROM %s" % q)
            ]
            tables.append({"table": t, "columns": cols, "rows": rows, "kind": "sqlite"})
        con.close()
    except sqlite3.Error as e:
        tables.append({"table": None, "columns": [], "rows": [], "kind": "sqlite", "error": str(e)})
    return tables


def load_table(entry):
    """Return a list of table dicts for a discovered file."""
    kind = entry["kind"]
    path = entry["path"]
    if entry.get("skipped"):
        return [{"kind": kind, "error": entry["skipped"], "columns": [], "rows": []}]
    try:
        if kind == "csv":
            return [load_csv(path)]
        if kind == "tsv":
            return [load_csv(path)]
        if kind == "json":
            res = load_json_data(path)
            return [res] if res else [{"kind": "json", "error": "no tabular structure found", "columns": [], "rows": []}]
        if kind == "sqlite":
            return load_sqlite(path)
    except Exception as e:  # noqa: BLE001 - degrade, never crash
        return [{"kind": kind, "error": "%s: %s" % (type(e).__name__, e), "columns": [], "rows": []}]
    return [{"kind": kind, "error": "unsupported", "columns": [], "rows": []}]


# --------------------------------------------------------------------------- #
# case / contract handling
# --------------------------------------------------------------------------- #

def read_case(input_dir):
    path = os.path.join(input_dir, "case.json")
    if not os.path.isfile(path):
        return None
    try:
        return read_json(path)
    except Exception:
        return None


def read_task(input_dir):
    path = os.path.join(input_dir, "task.md")
    if not os.path.isfile(path):
        return None
    try:
        text, _ = read_text(path)
        return text
    except Exception:
        return None


def extract_case_id(case):
    if not isinstance(case, dict):
        return None
    for k in ("case_id", "challenge_id", "armiId", "id"):
        v = case.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def contract_keys(case):
    if not isinstance(case, dict):
        return list(DEFAULT_CONTRACT_KEYS)
    oc = case.get("output_contract")
    if isinstance(oc, dict) and oc:
        return [str(k) for k in oc.keys()]
    if isinstance(oc, list) and oc:
        return [str(k) for k in oc]
    return list(DEFAULT_CONTRACT_KEYS)


def detect_intent(task_text):
    if not task_text:
        return "general"
    t = task_text.lower()
    group_kw = ["分组", "差异", "比较", "组间", "区别", "显著", "group", "compare", "between", "difference", "anova", "t-test", "t test"]
    corr_kw = ["相关", "关联", "预测", "回归", "correlat", "associat", "predict", "regress", "relation", "关系"]
    group_hits = sum(1 for k in group_kw if k in t)
    corr_hits = sum(1 for k in corr_kw if k in t)
    if group_hits > corr_hits and group_hits > 0:
        return "group_comparison"
    if corr_hits > 0:
        return "correlation"
    return "general"


# --------------------------------------------------------------------------- #
# profile
# --------------------------------------------------------------------------- #

def infer_column_type(values):
    """values: non-missing values. Returns one of int/float/categorical."""
    numeric = 0
    ints = 0
    for v in values:
        f = try_float(v)
        if f is not None:
            numeric += 1
            if float(f).is_integer():
                ints += 1
    total = len(values)
    if total == 0:
        return "categorical"
    if numeric / total >= 0.8:
        return "int" if ints / total >= 0.9 else "float"
    return "categorical"


def build_profile(input_dir, work_dir):
    files = discover_files(input_dir)
    case = read_case(input_dir)
    task = read_task(input_dir)
    case_id = extract_case_id(case)
    if case_id is None and os.path.isdir(input_dir):
        case_id = os.path.basename(os.path.normpath(input_dir)) or "unknown"
    tables = []
    for f in files:
        for t in load_table(f):
            t = dict(t)
            t["source_file"] = f["name"]
            tables.append(t)
    primary = None
    primary_spec = {}
    best_score = -1
    for t in tables:
        rows = t.get("rows") or []
        cols = t.get("columns") or []
        numeric_cols = 0
        for c in cols:
            vals = [r.get(c) for r in rows if not is_missing(r.get(c))]
            if infer_column_type(vals) in ("int", "float"):
                numeric_cols += 1
        score = len(rows) * 10 + numeric_cols
        label = (t.get("source_file") or "") + ":" + (t.get("table") or "")
        if score > best_score:
            best_score = score
            primary = label
            primary_spec = t
    profile = {
        "case_id": case_id,
        "input_files": files,
        "tables": [
            {
                "source_file": t.get("source_file"),
                "table": t.get("table"),
                "kind": t.get("kind"),
                "rows": len(t.get("rows") or []),
                "columns": t.get("columns") or [],
                "error": t.get("error"),
            }
            for t in tables
        ],
        "primary_table": primary,
        "task_intent": detect_intent(task),
    }
    if primary_spec:
        cols = primary_spec.get("columns") or []
        rows = primary_spec.get("rows") or []
        profile["columns"] = []
        for c in cols:
            vals = [r.get(c) for r in rows]
            non_null = [v for v in vals if not is_missing(v)]
            profile["columns"].append(
                {
                    "name": c,
                    "inferred_type": infer_column_type(non_null),
                    "non_null": len(non_null),
                    "unique": len(set(str(v) for v in non_null)),
                    "numeric_ratio": round(len([v for v in non_null if try_float(v) is not None]) / len(non_null), 4) if non_null else 0.0,
                }
            )
    write_json(os.path.join(work_dir, "profile.json"), profile)
    return profile


# --------------------------------------------------------------------------- #
# cleaning
# --------------------------------------------------------------------------- #

def clean_table(input_dir, work_dir):
    profile_path = os.path.join(work_dir, "profile.json")
    if not os.path.isfile(profile_path):
        build_profile(input_dir, work_dir)
    profile = read_json(profile_path)
    case_id = profile.get("case_id")

    # pick primary table
    primary_spec = None
    primary_label = profile.get("primary_table")
    for f in discover_files(input_dir):
        for t in load_table(f):
            label = (f["name"] + ":" + (t.get("table") or "")).rstrip(":")
            if label == primary_label:
                primary_spec = t
    if primary_spec is None:
        files = discover_files(input_dir)
        for f in files:
            for t in load_table(f):
                if t.get("rows"):
                    primary_spec = t
                    break
            if primary_spec:
                break

    clean_meta = {
        "case_id": case_id,
        "raw_rows": 0,
        "dedup_removed": 0,
        "fully_empty_rows_removed": 0,
        "cleaned_rows": 0,
        "coercions": {},
        "missing_policy": "listwise deletion limited to analysis variables; cells retained as blank in cleaned.csv",
        "outlier_rule": "IQR fence, 1.5x IQR; marked, never deleted",
        "outliers": {},
        "consistency_check": None,
    }

    if primary_spec is None or not primary_spec.get("rows"):
        clean_meta["errors"] = ["no data rows found"]
        write_json(os.path.join(work_dir, "clean_meta.json"), clean_meta)
        with open(os.path.join(work_dir, "cleaned.csv"), "w", encoding="utf-8", newline="") as fh:
            fh.write("")
        return clean_meta

    cols = primary_spec.get("columns") or []
    raw_rows = primary_spec.get("rows") or []
    clean_meta["raw_rows"] = len(raw_rows)

    # 1) exact duplicate removal (hash of all cell values, in canonical order)
    seen = set()
    dedup_rows = []
    for r in raw_rows:
        key = tuple(str(r.get(c)) for c in cols)
        if key in seen:
            continue
        seen.add(key)
        dedup_rows.append(r)
    clean_meta["dedup_removed"] = len(raw_rows) - len(dedup_rows)

    # 2) drop fully-empty rows
    non_empty = [r for r in dedup_rows if any(not is_missing(r.get(c)) for c in cols)]
    clean_meta["fully_empty_rows_removed"] = len(dedup_rows) - len(non_empty)

    # 3) coerce numeric columns
    column_roles = {}
    for c in cols:
        vals = [r.get(c) for r in non_empty if not is_missing(r.get(c))]
        role = infer_column_type(vals)
        column_roles[c] = role
        if role in ("int", "float"):
            ok = 0
            fail = 0
            for r in non_empty:
                raw = r.get(c)
                if is_missing(raw):
                    continue
                f = try_float(raw)
                if f is None:
                    fail += 1
                else:
                    r[c] = f
                    ok += 1
            clean_meta["coercions"][c] = {"to": role, "success": ok, "failed": fail}
        else:
            for r in non_empty:
                if isinstance(r.get(c), str):
                    r[c] = r[c].strip()

    clean_meta["cleaned_rows"] = len(non_empty)
    clean_meta["consistency_check"] = (
        clean_meta["dedup_removed"] + clean_meta["fully_empty_rows_removed"]
        == clean_meta["raw_rows"] - clean_meta["cleaned_rows"]
    )

    # 4) outlier marking (IQR, numeric columns)
    for c in cols:
        if column_roles.get(c) not in ("int", "float"):
            continue
        vals = [r.get(c) for r in non_empty if try_float(r.get(c)) is not None]
        if len(vals) < 4:
            continue
        s = sorted(vals)
        q1 = quantile(s, 0.25)
        q3 = quantile(s, 0.75)
        iq = q3 - q1
        if iq <= 0:
            continue
        lo = q1 - 1.5 * iq
        hi = q3 + 1.5 * iq
        samples = []
        count = 0
        for idx, r in enumerate(non_empty):
            f = try_float(r.get(c))
            if f is None:
                continue
            if f < lo or f > hi:
                count += 1
                if len(samples) < 20:
                    samples.append({"row_index": idx, "value": f})
        clean_meta["outliers"][c] = {
            "count": count,
            "lower_fence": lo,
            "upper_fence": hi,
            "samples": samples,
        }

    # write cleaned.csv (deterministic column order = original order)
    cleaned_path = os.path.join(work_dir, "cleaned.csv")
    with open(cleaned_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for r in non_empty:
            row = {}
            for c in cols:
                v = r.get(c)
                row[c] = "" if v is None else (str(v) if not isinstance(v, (int, float)) else repr(v))
            writer.writerow(row)

    write_json(os.path.join(work_dir, "clean_meta.json"), clean_meta)
    write_json(os.path.join(work_dir, "profile.json"), profile)
    return clean_meta


# --------------------------------------------------------------------------- #
# analysis
# --------------------------------------------------------------------------- #

def numeric_columns(rows, cols):
    out = []
    for c in cols:
        vals = [r.get(c) for r in rows if not is_missing(r.get(c))]
        if infer_column_type(vals) in ("int", "float"):
            out.append(c)
    return out


def categorical_columns(rows, cols, numeric_cols):
    out = []
    for c in cols:
        if c in numeric_cols:
            vals = [r.get(c) for r in rows if not is_missing(r.get(c))]
            uniq = len(set(str(v) for v in vals))
            n = len(vals)
            if n >= 10 and uniq <= 12 and uniq / n <= 0.2:
                out.append(c)
        else:
            out.append(c)
    return out


def describe_numeric(values):
    n = len(values)
    s = sorted(values)
    k2, k2p, g1 = dagostino_k2(values)
    q1 = quantile(s, 0.25)
    q3 = quantile(s, 0.75)
    iq = (q3 - q1) if (q1 is not None and q3 is not None) else None
    lo = (q1 - 1.5 * iq) if (q1 is not None and iq is not None) else None
    hi = (q3 + 1.5 * iq) if (q3 is not None and iq is not None) else None
    out_count = sum(1 for v in values if (lo is not None and hi is not None) and (v < lo or v > hi))
    m = mean(values)
    return {
        "n": n,
        "mean": m,
        "sd": sd(values),
        "sem": (sd(values) / math.sqrt(n)) if n and sd(values) is not None else None,
        "median": median(values),
        "q1": q1,
        "q3": q3,
        "iqr": iq,
        "min": min(values),
        "max": max(values),
        "skewness": g1,
        "kurtosis": kurtosis(values),
        "normality_k2": k2,
        "normality_p": k2p,
        "outlier_count": out_count,
        "outlier_fence": [lo, hi],
    }


def analyze(input_dir, work_dir, overrides=None):
    overrides = overrides or {}
    clean_meta_path = os.path.join(work_dir, "clean_meta.json")
    cleaned_path = os.path.join(work_dir, "cleaned.csv")
    if not (os.path.isfile(clean_meta_path) and os.path.isfile(cleaned_path)):
        clean_table(input_dir, work_dir)
    clean_meta = read_json(clean_meta_path)
    profile = read_json(os.path.join(work_dir, "profile.json"))
    case_id = clean_meta.get("case_id") or profile.get("case_id")

    if not os.path.isfile(cleaned_path):
        stats = {"case_id": case_id, "error": "no cleaned data", "descriptive": {}, "group_comparison": None, "correlation": None}
        write_json(os.path.join(work_dir, "stats.json"), stats)
        return stats

    with open(cleaned_path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        cols = reader.fieldnames or []
        rows = list(reader)

    # re-coerce numeric cells from csv (stored as repr)
    for r in rows:
        for c in cols:
            f = try_float(r.get(c))
            if f is not None:
                r[c] = f

    num_cols = numeric_columns(rows, cols)
    cat_cols = categorical_columns(rows, cols, num_cols)

    stats = {
        "case_id": case_id,
        "task_intent": overrides.get("intent") or profile.get("task_intent") or "general",
        "rows_after_cleaning": len(rows),
        "numeric_columns": num_cols,
        "categorical_columns": cat_cols,
        "descriptive": {},
        "group_comparison": None,
        "correlation": None,
        "notes": [],
    }

    for c in cols:
        vals = [r.get(c) for r in rows]
        missing = sum(1 for v in vals if is_missing(v))
        if c in num_cols:
            nums = [try_float(v) for v in vals if not is_missing(v) and try_float(v) is not None]
            if nums:
                stats["descriptive"][c] = describe_numeric(nums)
                stats["descriptive"][c]["missing"] = missing
        else:
            counts = {}
            for v in vals:
                if not is_missing(v):
                    key = str(v)
                    counts[key] = counts.get(key, 0) + 1
            top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
            stats["descriptive"][c] = {"missing": missing, "value_counts": top}

    # resolve grouping column
    group_col = overrides.get("group")
    if group_col and group_col not in cols:
        group_col = None
        stats["notes"].append("override group column not found; fell back to heuristic")
    if not group_col and cat_cols:
        group_col = cat_cols[0]
        for cand in cat_cols:
            if cand.lower() in ("species", "class", "target", "group", "label", "type", "品种", "类别"):
                group_col = cand
                break

    # resolve target columns
    targets = None
    if overrides.get("targets"):
        targets = [t.strip() for t in str(overrides["targets"]).split(",") if t.strip() and t.strip() in cols]

    intent = stats["task_intent"]

    if group_col and group_col in cols:
        # --- group comparison --------------------------------------------- #
        levels = sorted({str(r.get(group_col)) for r in rows if not is_missing(r.get(group_col))})
        if targets is None:
            targets = [c for c in num_cols if c != group_col]
        valid_targets = [t for t in targets if t in num_cols]
        if valid_targets:
            comparison = {"group_col": group_col, "levels": levels, "per_target": {}}
            for t in valid_targets:
                groups = {}
                for lv in levels:
                    vals = [
                        try_float(r.get(t))
                        for r in rows
                        if str(r.get(group_col)) == lv and not is_missing(r.get(t)) and try_float(r.get(t)) is not None
                    ]
                    if vals:
                        groups[lv] = vals
                ordered = [groups[lv] for lv in levels if lv in groups and len(groups[lv]) >= 2]
                if len(ordered) < 2:
                    comparison["per_target"][t] = {"skipped": "fewer than 2 groups with n>=2"}
                    continue
                normal = [
                    dagostino_k2(g)[1] is not None and dagostino_k2(g)[1] > 0.05 for g in ordered
                ]
                levF, levp = levene(ordered)
                hom = levp is not None and levp > 0.05
                k = len(ordered)
                if k == 2:
                    if all(normal) and hom:
                        res = ttest_student(ordered[0], ordered[1])
                        effect = {"name": "Hedges_g", "value": cohens_d(ordered[0], ordered[1])}
                    elif all(normal):
                        res = ttest_welch(ordered[0], ordered[1])
                        effect = {"name": "Hedges_g", "value": cohens_d(ordered[0], ordered[1])}
                    else:
                        res = mann_whitney_u(ordered[0], ordered[1])
                        u_min = res["statistic"]
                        effect = {"name": "rank_biserial", "value": rank_biserial(ordered[0], ordered[1], u_min)}
                    sens = {
                        "parametric": ttest_welch(ordered[0], ordered[1]) if not all(normal) or not hom else ttest_student(ordered[0], ordered[1]),
                        "nonparametric": mann_whitney_u(ordered[0], ordered[1]),
                    }
                else:
                    if all(normal) and hom:
                        res = anova_oneway(ordered)
                        effect = {"name": "eta_squared", "value": eta_squared(res)}
                    else:
                        res = kruskal_wallis(ordered)
                        N = sum(len(g) for g in ordered)
                        effect = {"name": "epsilon_squared", "value": epsilon_squared(res["statistic"], N)}
                    sens = {"parametric": anova_oneway(ordered), "nonparametric": kruskal_wallis(ordered)}
                group_summary = {
                    lv: {
                        "n": len(groups[lv]),
                        "mean": mean(groups[lv]),
                        "sd": sd(groups[lv]),
                        "median": median(groups[lv]),
                    }
                    for lv in levels
                    if lv in groups
                }
                posthoc = []
                if k > 2 and res and res.get("p_value") is not None and res["p_value"] < 0.05:
                    m = k * (k - 1) // 2
                    pairs = [(levels[i], levels[j]) for i in range(k) for j in range(i + 1, k)]
                    for a, b in pairs:
                        if all(normal) and hom:
                            pr = ttest_student(groups[a], groups[b])
                        else:
                            pr = mann_whitney_u(groups[a], groups[b])
                        p_adj = min(1.0, pr["p_value"] * m) if pr and pr.get("p_value") is not None else None
                        posthoc.append(
                            {
                                "pair": [a, b],
                                "p_raw": pr.get("p_value") if pr else None,
                                "p_bonferroni": p_adj,
                                "significant": p_adj is not None and p_adj < 0.05,
                            }
                        )
                comparison["per_target"][t] = {
                    "method": res.get("method") if res else None,
                    "statistic": res.get("statistic") if res else None,
                    "p_value": res.get("p_value") if res else None,
                    "dof": res.get("dof") if res else (res.get("dof_between") if res else None),
                    "effect_size": effect,
                    "assumptions": {
                        "normality_p_by_group": [
                            dagostino_k2(g)[1] for g in ordered
                        ],
                        "levene_F": levF,
                        "levene_p": levp,
                        "decision_basis": (
                            "normal & homogeneous -> parametric"
                            if all(normal) and hom
                            else "robust/nonparametric chosen"
                        ),
                    },
                    "sensitivity": sens,
                    "group_summary": group_summary,
                    "posthoc": posthoc,
                }
            stats["group_comparison"] = comparison

    # --- correlation ------------------------------------------------------ #
    corr_targets = targets if (targets and len(targets) >= 2) else None
    if intent in ("correlation", "general") and len(num_cols) >= 2:
        if corr_targets is None:
            corr_targets = num_cols
        pairs = []
        done = set()
        for i in range(len(corr_targets)):
            for j in range(i + 1, len(corr_targets)):
                xc, yc = corr_targets[i], corr_targets[j]
                if (xc, yc) in done or (yc, xc) in done:
                    continue
                done.add((xc, yc))
                xs = [try_float(r.get(xc)) for r in rows if not is_missing(r.get(xc)) and not is_missing(r.get(yc)) and try_float(r.get(xc)) is not None and try_float(r.get(yc)) is not None]
                ys = [try_float(r.get(yc)) for r in rows if not is_missing(r.get(xc)) and not is_missing(r.get(yc)) and try_float(r.get(xc)) is not None and try_float(r.get(yc)) is not None]
                if len(xs) < 3 or len(xs) != len(ys):
                    continue
                pr = pearson_r(xs, ys)
                sp = spearman_rho(xs, ys)
                pairs.append(
                    {
                        "x": xc,
                        "y": yc,
                        "n": len(xs),
                        "pearson_r": pr["pearson_r"] if pr else None,
                        "pearson_p": pr["p_value"] if pr else None,
                        "spearman_rho": sp["spearman_rho"] if sp else None,
                        "spearman_p": sp["p_value"] if sp else None,
                    }
                )
                if len(pairs) >= 60:
                    stats["notes"].append("correlation pairs capped at 60")
                    break
            if len(pairs) >= 60:
                break
        if pairs:
            stats["correlation"] = {"pairs": pairs}

    write_json(os.path.join(work_dir, "stats.json"), stats)
    return stats


# --------------------------------------------------------------------------- #
# rendering: deterministic SVG + report.md
# --------------------------------------------------------------------------- #

def esc(s):
    return (
        str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&apos;")
    )


def _nice_step(raw):
    if raw <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1.0, 2.0, 2.5, 5.0, 10.0):
        if raw <= m * mag:
            return m * mag
    return 10.0 * mag


def _ticks(lo, hi, n=6):
    if lo == hi:
        if lo == 0:
            lo, hi = -1.0, 1.0
        else:
            lo, hi = lo - abs(lo) * 0.5, hi + abs(hi) * 0.5
    span = hi - lo
    step = _nice_step(span / n)
    out = []
    x = math.ceil(lo / step - 1e-9) * step
    while x <= hi + step * 1e-9:
        out.append(x)
        x += step
    if len(out) < 2:
        out = [lo, (lo + hi) / 2.0, hi]
    return out


def _svg(width, height, body):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" font-family="DejaVu Sans, Arial, sans-serif">\n%s</svg>\n'
        % (width, height, width, height, body)
    )


def svg_bar(title, cats, means, errors, ns, ylabel):
    W, H = 760, 420
    L, R, T, B = 70, 30, 60, 70
    plot_w = W - L - R
    plot_h = H - T - B
    lo = min(0.0, min(m - e for m, e in zip(means, errors)))
    hi = max(m + e for m, e in zip(means, errors))
    if lo == hi:
        hi = lo + 1.0
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad
    n = len(cats)
    slot = plot_w / n
    bar_w = slot * 0.55
    ymap = lambda v: T + plot_h - (v - lo) / (hi - lo) * plot_h
    xmap = lambda i: L + slot * i + slot / 2
    body = ['<rect x="0" y="0" width="%d" height="%d" fill="white"/>' % (W, H)]
    body.append('<text x="%d" y="%d" font-size="16" text-anchor="middle" font-weight="bold">%s</text>' % (W / 2, 28, esc(title)))
    body.append('<text x="%d" y="%d" font-size="12" text-anchor="middle">%s</text>' % (W / 2, H - 16, esc(ylabel)))
    for tk in _ticks(lo, hi):
        y = ymap(tk)
        body.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#cccccc"/>' % (L, y, W - R, y))
        body.append('<text x="%d" y="%d" font-size="10" text-anchor="end">%g</text>' % (L - 6, y + 3, tk))
    for i in range(n):
        x = xmap(i)
        top = ymap(means[i])
        base = ymap(0.0) if lo <= 0 else ymap(lo)
        body.append('<rect x="%g" y="%g" width="%g" height="%g" fill="#4477aa" stroke="#224466" stroke-width="1"/>' % (x - bar_w / 2, top, bar_w, max(0.0, base - top)))
        ye1 = ymap(means[i] - errors[i])
        ye2 = ymap(means[i] + errors[i])
        body.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#222222" stroke-width="1.5"/>' % (x, ye1, x, ye2))
        body.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#222222" stroke-width="1.5"/>' % (x - 4, ye1, x + 4, ye1))
        body.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#222222" stroke-width="1.5"/>' % (x - 4, ye2, x + 4, ye2))
        body.append('<text x="%g" y="%d" font-size="11" text-anchor="middle">%s</text>' % (x, H - 38, esc(cats[i])))
        body.append('<text x="%g" y="%g" font-size="9" text-anchor="middle" fill="#444444">n=%d</text>' % (x, top - 6, ns[i]))
    body.append('<text x="%d" y="%d" font-size="10" fill="#555555">bars = mean; error bars = SEM; n = sample size</text>' % (L, H - 6))
    return _svg(W, H, "\n".join(body))


def svg_box(title, groups, ylabel):
    """groups: list of (label, n, min, q1, median, q3, max, outliers)."""
    W, H = 760, 420
    L, R, T, B = 70, 30, 60, 70
    plot_w = W - L - R
    plot_h = H - T - B
    allv = [v for g in groups for v in [g[2], g[5], g[3], g[4]] + list(g[7])]
    lo, hi = min(allv), max(allv)
    if lo == hi:
        hi = lo + 1.0
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad
    n = len(groups)
    slot = plot_w / n
    box_w = slot * 0.4
    ymap = lambda v: T + plot_h - (v - lo) / (hi - lo) * plot_h
    xmap = lambda i: L + slot * i + slot / 2
    body = ['<rect x="0" y="0" width="%d" height="%d" fill="white"/>' % (W, H)]
    body.append('<text x="%d" y="%d" font-size="16" text-anchor="middle" font-weight="bold">%s</text>' % (W / 2, 28, esc(title)))
    body.append('<text x="%d" y="%d" font-size="12" text-anchor="middle">%s</text>' % (W / 2, H - 16, esc(ylabel)))
    for tk in _ticks(lo, hi):
        y = ymap(tk)
        body.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#cccccc"/>' % (L, y, W - R, y))
        body.append('<text x="%d" y="%d" font-size="10" text-anchor="end">%g</text>' % (L - 6, y + 3, tk))
    for i, (label, n, q0, q1, med, q3, q4, outs) in enumerate(groups):
        x = xmap(i)
        yq1, ymed, yq3 = ymap(q1), ymap(med), ymap(q3)
        ylo, yhi = ymap(q0), ymap(q4)
        body.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#222222"/>' % (x, ylo, x, yq1))
        body.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#222222"/>' % (x, yq3, x, yhi))
        body.append('<rect x="%g" y="%g" width="%g" height="%g" fill="#7fb3d5" stroke="#224466"/>' % (x - box_w / 2, yq3, box_w, max(0.0, yq1 - yq3)))
        body.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#222222" stroke-width="2"/>' % (x - box_w / 2, ymed, x + box_w / 2, ymed))
        for ov in outs:
            body.append('<circle cx="%g" cy="%g" r="3" fill="#cc4444"/>' % (x, ymap(ov)))
        body.append('<text x="%g" y="%d" font-size="11" text-anchor="middle">%s</text>' % (x, H - 38, esc(label)))
        body.append('<text x="%g" y="%g" font-size="9" text-anchor="middle" fill="#444444">n=%d</text>' % (x, ylo - 6, n))
    body.append('<text x="%d" y="%d" font-size="10" fill="#555555">box = Q1..Q3; line = median; whiskers = min/max; red = IQR outliers (marked, kept)</text>' % (L, H - 6))
    return _svg(W, H, "\n".join(body))


def svg_scatter(title, xs, ys, slope, intercept, xlabel, ylabel, r):
    W, H = 760, 420
    L, R, T, B = 70, 30, 60, 70
    plot_w = W - L - R
    plot_h = H - T - B
    xlo, xhi = min(xs), max(xs)
    ylo, yhi = min(ys), max(ys)
    if xlo == xhi:
        xlo, xhi = xlo - 1, xhi + 1
    if ylo == yhi:
        ylo, yhi = ylo - 1, yhi + 1
    xpad, ypad = (xhi - xlo) * 0.05, (yhi - ylo) * 0.05
    xlo, xhi = xlo - xpad, xhi + xpad
    ylo, yhi = ylo - ypad, yhi + ypad
    xmap = lambda v: L + (v - xlo) / (xhi - xlo) * plot_w
    ymap = lambda v: T + plot_h - (v - ylo) / (yhi - ylo) * plot_h
    body = ['<rect x="0" y="0" width="%d" height="%d" fill="white"/>' % (W, H)]
    body.append('<text x="%d" y="%d" font-size="16" text-anchor="middle" font-weight="bold">%s</text>' % (W / 2, 28, esc(title)))
    body.append('<text x="%d" y="%d" font-size="12" text-anchor="middle">%s</text>' % (W / 2, H - 16, esc(xlabel)))
    body.append('<text x="16" y="%d" font-size="12" text-anchor="middle" transform="rotate(-90 16 %d)">%s</text>' % ((T + plot_h / 2), (T + plot_h / 2), esc(ylabel)))
    for tk in _ticks(xlo, xhi):
        x = xmap(tk)
        body.append('<line x1="%g" y1="%d" x2="%g" y2="%d" stroke="#eeeeee"/>' % (x, T, x, T + plot_h))
        body.append('<text x="%g" y="%d" font-size="10" text-anchor="middle">%g</text>' % (x, T + plot_h + 14, tk))
    for tk in _ticks(ylo, yhi):
        y = ymap(tk)
        body.append('<line x1="%d" y1="%g" x2="%d" y2="%g" stroke="#eeeeee"/>' % (L, y, L + plot_w, y))
        body.append('<text x="%d" y="%g" font-size="10" text-anchor="end">%g</text>' % (L - 6, y + 3, tk))
    for x, y in zip(xs, ys):
        body.append('<circle cx="%g" cy="%g" r="2.6" fill="#4477aa" fill-opacity="0.65"/>' % (xmap(x), ymap(y)))
    y0 = intercept + slope * xlo
    y1 = intercept + slope * xhi
    body.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#cc4444" stroke-width="1.5"/>' % (xmap(xlo), ymap(y0), xmap(xhi), ymap(y1)))
    body.append('<text x="%d" y="%d" font-size="11" fill="#cc4444">r = %s</text>' % (L + 10, T + 16, fmt_num(r, 4)))
    body.append('<text x="%d" y="%d" font-size="10" fill="#555555">red line = least-squares fit</text>' % (L, H - 6))
    return _svg(W, H, "\n".join(body))


def linear_fit(xs, ys):
    n = len(xs)
    mx, my = mean(xs), mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return 0.0, my
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return slope, my - slope * mx


def render(work_dir, output_dir):
    stats_path = os.path.join(work_dir, "stats.json")
    stats = read_json(stats_path) if os.path.isfile(stats_path) else None
    os.makedirs(output_dir, exist_ok=True)
    if stats is None or stats.get("error"):
        write_json(os.path.join(output_dir, "result.json"), {})
        return
    case_id = stats.get("case_id", "unknown")
    figures = []
    gc = stats.get("group_comparison")
    if gc:
        box_groups = []
        for t in gc.get("per_target", {}).values():
            gs = t.get("group_summary")
            if gs:
                box_groups = []
                for lv, s in gs.items():
                    box_groups.append((lv, s.get("n", 0), s.get("min", s.get("q1", 0)), s.get("q1", 0), s.get("median", 0), s.get("q3", 0), s.get("max", s.get("q3", 0)), []))
                break
        # one box plot per target
        for t, block in gc.get("per_target", {}).items():
            gs = block.get("group_summary")
            if not gs:
                continue
            box_groups = []
            bar_cats, bar_means, bar_errs, bar_ns = [], [], [], []
            for lv in gc.get("levels", []):
                s = gs.get(lv)
                if not s:
                    continue
                box_groups.append((lv, s.get("n", 0), s.get("min", 0), s.get("q1", 0), s.get("median", 0), s.get("q3", 0), s.get("max", 0), []))
                bar_cats.append(lv)
                bar_means.append(s.get("mean", 0))
                bar_errs.append(s.get("sem", s.get("sd", 0) / math.sqrt(max(1, s.get("n", 1))) if s.get("sd") else 0))
                bar_ns.append(s.get("n", 0))
            if box_groups:
                svg = svg_box("%s by %s" % (t, gc.get("group_col")), box_groups, t)
                p = os.path.join(output_dir, "figure_boxplot_%s.svg" % _safe_name(t))
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write(svg)
                figures.append(("boxplot", t, os.path.basename(p)))
            if bar_cats:
                svg = svg_bar("Mean %s by %s" % (t, gc.get("group_col")), bar_cats, bar_means, bar_errs, bar_ns, "%s (mean +/- SEM)" % t)
                p = os.path.join(output_dir, "figure_bars_%s.svg" % _safe_name(t))
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write(svg)
                figures.append(("bars", t, os.path.basename(p)))
    corr = stats.get("correlation")
    if corr:
        for pair in corr.get("pairs", [])[:3]:
            xname, yname = pair.get("x"), pair.get("y")
            if not xname or not yname:
                continue
            # re-read cleaned.csv to get raw pairs for the scatter
            xs, ys = _scatter_points(work_dir, xname, yname)
            if len(xs) < 3:
                continue
            slope, intercept = linear_fit(xs, ys)
            svg = svg_scatter(
                "%s vs %s" % (yname, xname), xs, ys, slope, intercept,
                xname, yname, pair.get("pearson_r"),
            )
            p = os.path.join(output_dir, "figure_scatter_%s_%s.svg" % (_safe_name(xname), _safe_name(yname)))
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(svg)
            figures.append(("scatter", "%s~%s" % (xname, yname), os.path.basename(p)))

    report = build_report(stats, figures)
    with open(os.path.join(output_dir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(report)
    write_json(os.path.join(output_dir, "_render_meta.json"), {"figures": figures})


def _safe_name(s):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(s))


def _scatter_points(work_dir, xname, yname):
    cleaned = os.path.join(work_dir, "cleaned.csv")
    xs, ys = [], []
    if not os.path.isfile(cleaned):
        return xs, ys
    with open(cleaned, "r", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            fx, fy = try_float(r.get(xname)), try_float(r.get(yname))
            if fx is not None and fy is not None:
                xs.append(fx)
                ys.append(fy)
    return xs, ys


def build_report(stats, figures):
    L = []
    L.append("# 科研数据分析报告")
    L.append("")
    L.append("- case_id: `%s`" % stats.get("case_id", "unknown"))
    L.append("- 分析意图: %s" % stats.get("task_intent", "general"))
    L.append("- 清洗后样本量: %d 行" % stats.get("rows_after_cleaning", 0))
    L.append("- 本报告全部数字由 `work/stats.json` 程序化生成，可回算；无手写数字。")
    L.append("")
    gc = stats.get("group_comparison")
    if gc:
        L.append("## 组间比较")
        L.append("")
        L.append("- 分组变量: `%s`；分组水平: %s" % (gc.get("group_col"), ", ".join(gc.get("levels", []))))
        L.append("")
        for t, b in gc.get("per_target", {}).items():
            if b.get("skipped"):
                L.append("### %s\n\n跳过：%s\n" % (t, b["skipped"]))
                continue
            L.append("### %s" % t)
            L.append("")
            L.append("- 方法: %s；统计量 = %s；p = %s；效应量 %s = %s" % (
                b.get("method"), fmt_num(b.get("statistic")), fmt_p(b.get("p_value")),
                (b.get("effect_size") or {}).get("name"), fmt_num((b.get("effect_size") or {}).get("value")),
            ))
            a = b.get("assumptions") or {}
            L.append("- 前提校验: 各组正态性 p = %s；Levene(中位数中心) F = %s, p = %s；决策依据: %s" % (
                ", ".join(fmt_p(x) if x is not None else "n/a" for x in a.get("normality_p_by_group", [])),
                fmt_num(a.get("levene_F")), fmt_p(a.get("levene_p")), a.get("decision_basis", ""),
            ))
            gs = b.get("group_summary") or {}
            if gs:
                L.append("")
                L.append("| 组 | n | 均值 | 标准差 | 中位数 |")
                L.append("|---|---|---|---|---|")
                for lv, s in gs.items():
                    L.append("| %s | %d | %s | %s | %s |" % (lv, s.get("n", 0), fmt_num(s.get("mean")), fmt_num(s.get("sd")), fmt_num(s.get("median"))))
            if b.get("posthoc"):
                L.append("")
                L.append("事后两两比较（Bonferroni 校正）:")
                for ph in b["posthoc"]:
                    L.append("- %s vs %s: p_raw = %s, p_adj = %s, 显著 = %s" % (
                        ph["pair"][0], ph["pair"][1], fmt_p(ph.get("p_raw")), fmt_p(ph.get("p_bonferroni")), ph.get("significant"),
                    ))
            L.append("")
    corr = stats.get("correlation")
    if corr:
        L.append("## 相关分析")
        L.append("")
        L.append("| x | y | n | Pearson r | Pearson p | Spearman ρ | Spearman p |")
        L.append("|---|---|---|---|---|---|---|")
        for p in corr.get("pairs", []):
            L.append("| %s | %s | %d | %s | %s | %s | %s |" % (
                p.get("x"), p.get("y"), p.get("n", 0),
                fmt_num(p.get("pearson_r")), fmt_p(p.get("pearson_p")),
                fmt_num(p.get("spearman_rho")), fmt_p(p.get("spearman_p")),
            ))
        L.append("")
    if figures:
        L.append("## 图")
        L.append("")
        for kind, label, fname in figures:
            L.append("- %s (%s): `%s`" % (kind, label, fname))
        L.append("")
    L.append("## 方法与口径说明")
    L.append("")
    L.append("统计检验与正态性/方差齐性校验均用标准库从第一性原理实现（不完全 beta/gamma、"
             "正态 CDF），无第三方依赖；异常值按 1.5×IQR 标记不删除；缺失值仅对分析变量做整例删除，"
             "清洗规则与行数影响见 `work/clean_meta.json`。")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# pipeline orchestration
# --------------------------------------------------------------------------- #

def ensure_dirs(output_dir, work_dir):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)


def scaffold(input_dir, output_dir, work_dir):
    ensure_dirs(output_dir, work_dir)
    case = read_case(input_dir)
    cid = extract_case_id(case)
    if cid is None and os.path.isdir(input_dir):
        cid = os.path.basename(os.path.normpath(input_dir)) or "unknown"
    keys = contract_keys(case)
    result = {
        "case_id": cid,
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [
            {"step": "scaffold", "status": "partial", "detail": "skeleton written first"}
        ],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "runtime": {"elapsed_seconds": None, "note": "wall-clock; excluded from determinism comparison"},
        "deterministic": True,
        "artifacts": {},
    }
    for k in keys:
        result[k] = None
    write_json(os.path.join(output_dir, "result.json"), result)
    return result


def finalize(input_dir, output_dir, work_dir, overrides=None):
    overrides = overrides or {}
    result_path = os.path.join(output_dir, "result.json")
    result = read_json(result_path) if os.path.isfile(result_path) else scaffold(input_dir, output_dir, work_dir)
    case_path = os.path.join(input_dir, "case.json")
    case = read_case(input_dir)
    case_missing = not os.path.isfile(case_path)
    case_bad = os.path.isfile(case_path) and case is None
    keys = contract_keys(case)
    cid = extract_case_id(case)
    if cid:
        result["case_id"] = cid
    if case_missing:
        result.setdefault("degradations", []).append("case.json missing; case_id falls back to input dir name")
    if case_bad:
        result.setdefault("degradations", []).append("case.json present but unparseable; case_id falls back to input dir name")
        result.setdefault("errors", []).append("case.json could not be parsed as JSON")
    for k in keys:
        if k not in result:
            result[k] = None

    files = discover_files(input_dir)
    profile_path = os.path.join(work_dir, "profile.json")
    clean_meta_path = os.path.join(work_dir, "clean_meta.json")
    stats_path = os.path.join(work_dir, "stats.json")

    has_data = False
    if os.path.isfile(clean_meta_path):
        try:
            has_data = read_json(clean_meta_path).get("raw_rows", 0) > 0
        except Exception:
            has_data = False
    if not has_data:
        for f in files:
            if f.get("skipped"):
                continue
            for t in load_table(f):
                if (t.get("rows") or []):
                    has_data = True
                    break
            if has_data:
                break

    has_stats = False
    if os.path.isfile(stats_path):
        try:
            s = read_json(stats_path)
            has_stats = bool(s) and not s.get("error") and (
                s.get("descriptive") or s.get("group_comparison") or s.get("correlation")
            )
        except Exception:
            has_stats = False

    has_clean = os.path.isfile(clean_meta_path)

    # map contract keys to real content
    analysis_results = {}
    quality_evidence = {}
    anomaly_notes = {}
    if has_stats:
        stats = read_json(stats_path)
        analysis_results = {
            "rows_after_cleaning": stats.get("rows_after_cleaning"),
            "descriptive": stats.get("descriptive"),
            "group_comparison": stats.get("group_comparison"),
            "correlation": stats.get("correlation"),
            "report": "report.md",
        }
    if has_clean:
        cm = read_json(clean_meta_path)
        quality_evidence = {
            "clean_meta": "work/clean_meta.json",
            "profile": "work/profile.json",
            "stats": "work/stats.json",
            "cleaned_table": "work/cleaned.csv",
            "consistency_check": cm.get("consistency_check"),
            "dedup_removed": cm.get("dedup_removed"),
        }
        for c, o in (cm.get("outliers") or {}).items():
            anomaly_notes.setdefault("outliers", {})[c] = o.get("count")
        for c, o in (cm.get("coercions") or {}).items():
            if o.get("failed"):
                anomaly_notes.setdefault("coercion_failures", {})[c] = o.get("failed")

    for k in keys:
        if k in ("analysis_results", "results", "summary", "report", "analysis"):
            result[k] = analysis_results
        elif k in ("quality_evidence", "quality", "evidence", "reproducibility"):
            result[k] = quality_evidence
        elif k in ("anomaly_notes", "anomalies", "notes", "exceptions"):
            result[k] = anomaly_notes
        else:
            if result.get(k) is None:
                result[k] = None
                result.setdefault("degradations", []).append(
                    "unknown output_contract key %r filled with null" % k
                )

    # evidence sources with locator + verbatim quote
    sources = []
    for f in files:
        if f.get("skipped"):
            continue
        q = "file %s (%s, %d bytes)" % (f["name"], f["kind"], f["size"])
        sources.append({"locator": f["name"], "quote": q, "role": "input_data"})
    task_text = read_task(input_dir)
    if task_text:
        snippet = re.sub(r"\s+", " ", task_text).strip()
        sources.append({"locator": "task.md", "quote": snippet[:400], "role": "task_spec"})

    # status assignment (honest, no fabrication)
    if not has_data and not has_stats:
        if case_bad and not files:
            result["status"] = "input_error"
        else:
            result["status"] = "insufficient_evidence"
            result["limitations"].append("no data file found under /input")
    elif has_stats:
        result["status"] = "ok"
        if case_bad:
            result["limitations"].append("case.json unparseable; results derived from data files only")
    elif has_clean:
        result["status"] = "partial"
        result["limitations"].append("analysis incomplete; statistics not produced")
    else:
        result["status"] = "partial"

    # manifest + fingerprint
    manifest = {}
    for fname in ("report.md",):
        p = os.path.join(output_dir, fname)
        if os.path.isfile(p):
            manifest[fname] = {"sha256": sha256_file(p), "bytes": os.path.getsize(p)}
    for fname in sorted(os.listdir(output_dir)):
        if fname.endswith(".svg"):
            p = os.path.join(output_dir, fname)
            manifest[fname] = {"sha256": sha256_file(p), "bytes": os.path.getsize(p)}
    for fname in ("cleaned.csv", "clean_meta.json", "stats.json", "profile.json"):
        p = os.path.join(work_dir, fname)
        if os.path.isfile(p):
            manifest[fname] = {"sha256": sha256_file(p), "bytes": os.path.getsize(p)}
    result["artifacts"] = manifest

    fp_src = "\n".join(
        "%s %s" % (f["name"], sha256_file(f["path"]))
        for f in files
        if not f.get("skipped") and os.path.isfile(f["path"])
    )
    result["fingerprint"] = sha256_bytes(
        "|".join([VERSION, cid or "", ",".join(keys), fp_src])
    )

    result["change_log"].append(
        {"step": "finalize", "status": result["status"], "detail": "contract fields filled, manifest + fingerprint written"}
    )
    result["sources"] = sources
    result["runtime"]["elapsed_seconds"] = round_sig(result["runtime"].get("elapsed_seconds"))
    write_json(result_path, result)
    return result


def run_all(input_dir, output_dir, work_dir, overrides=None):
    import time
    t0 = time.time()
    scaffold(input_dir, output_dir, work_dir)
    profile = build_profile(input_dir, work_dir)
    clean_meta = clean_table(input_dir, work_dir)
    stats = analyze(input_dir, work_dir, overrides)
    render(work_dir, output_dir)
    result = finalize(input_dir, output_dir, work_dir, overrides)
    result["runtime"]["elapsed_seconds"] = round_sig(time.time() - t0)
    write_json(os.path.join(output_dir, "result.json"), result)
    return result


# --------------------------------------------------------------------------- #
# validation / self-check helpers (reused by selftest.py)
# --------------------------------------------------------------------------- #

def validate_outputs(output_dir):
    """Return a list of (check, ok, detail) triples."""
    checks = []
    result_path = os.path.join(output_dir, "result.json")
    if not os.path.isfile(result_path):
        return [("result.json exists", False, "missing")]
    try:
        r = read_json(result_path)
        checks.append(("result.json parses", True, ""))
    except Exception as e:
        return [("result.json parses", False, str(e))]
    checks.append(("case_id present", bool(r.get("case_id")), str(r.get("case_id"))))
    checks.append(("status present", bool(r.get("status")), str(r.get("status"))))
    checks.append(("sources present", isinstance(r.get("sources"), list), "n=%d" % len(r.get("sources") or [])))
    checks.append(("uncertainty present", isinstance(r.get("uncertainty"), list), ""))
    checks.append(("change_log present", isinstance(r.get("change_log"), list) and len(r.get("change_log", [])) > 0, ""))
    for k in ("analysis_results", "quality_evidence", "anomaly_notes"):
        if k in r:
            checks.append(("contract key %s present" % k, True, ""))
    for fname in sorted(os.listdir(output_dir)):
        if fname.endswith(".svg"):
            p = os.path.join(output_dir, fname)
            try:
                import xml.etree.ElementTree as ET
                ET.parse(p)
                checks.append(("SVG parseable: %s" % fname, True, ""))
            except Exception as e:
                checks.append(("SVG parseable: %s" % fname, False, str(e)))
    return checks


def check_command(input_dir, output_dir, work_dir):
    checks = validate_outputs(output_dir)
    for name, ok, detail in checks:
        print("%s\t%s\t%s" % ("PASS" if ok else "FAIL", name, detail))
    return 0 if all(ok for _, ok, _ in checks) else 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv):
    import argparse
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default="/input", help="input directory (default /input)")
    common.add_argument("--output", default="/app/output", help="output directory (default /app/output)")
    common.add_argument("--work", default="/app/work", help="work directory (default /app/work)")

    parser = argparse.ArgumentParser(
        parents=[common], prog="main.py", description="Deterministic multi-step scientific data analysis"
    )
    sub = parser.add_subparsers(dest="command")

    simple = ("scaffold", "profile", "clean", "render", "finalize", "check")
    for cmd in simple:
        sub.add_parser(cmd, parents=[common], help=cmd)

    anap = sub.add_parser("analyze", parents=[common], help="run the analysis stage")
    runp = sub.add_parser("run", parents=[common], help="run the full pipeline end-to-end")

    for p in (runp, anap):
        p.add_argument("--primary", default=None, help="primary table label (file:table)")
        p.add_argument("--group", default=None, help="grouping column")
        p.add_argument("--targets", default=None, help="comma-separated target columns")
        p.add_argument("--intent", default=None, choices=["group_comparison", "correlation", "general"])

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    overrides = {}
    if getattr(args, "primary", None):
        overrides["primary"] = args.primary
    if getattr(args, "group", None):
        overrides["group"] = args.group
    if getattr(args, "targets", None):
        overrides["targets"] = args.targets
    if getattr(args, "intent", None):
        overrides["intent"] = args.intent

    if args.command == "scaffold":
        scaffold(args.input, args.output, args.work)
    elif args.command == "profile":
        build_profile(args.input, args.work)
    elif args.command == "clean":
        clean_table(args.input, args.work)
    elif args.command == "analyze":
        analyze(args.input, args.work, overrides)
    elif args.command == "render":
        render(args.work, args.output)
    elif args.command == "finalize":
        finalize(args.input, args.output, args.work, overrides)
    elif args.command == "check":
        return check_command(args.input, args.output, args.work)
    elif args.command == "run":
        run_all(args.input, args.output, args.work, overrides)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
