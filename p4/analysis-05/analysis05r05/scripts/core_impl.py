"""core_impl.py - multi-step scientific data analysis pipeline (standard library only).

Design goals
------------
1. Contract-first: every field of /input/case.json's ``output_contract`` is
   echoed into /app/output/result.json at top level, unknown keys preserved,
   missing values filled with null/[] and recorded in change_log.
2. Recomputable: cleaned.csv, clean_meta.json, stats.json and SVG figures are
   the single source of truth; report.md only re-prints numbers from stats.json.
3. Honest degradation: empty input, bad JSON, bad encoding, missing columns,
   type mismatch, division by zero and SQLite failures all exit with code 0 and
   are explained in errors/degradations. No fabricated numbers.
4. Deterministic: stats.json and SVG figures are byte-identical across runs
   (no randomness, no timestamps inside them).
"""
import argparse
import csv
import io
import json
import math
import os
import sqlite3
import sys
import time
from collections import OrderedDict

VERSION = "1.1.0"

DEFAULT_CONTRACT = {
    "analysis_results": "analysis summary",
    "quality_evidence": "reproducibility evidence",
    "anomaly_notes": "anomaly explanations",
}

GROUP_HINTS = (
    "group", "class", "species", "category", "type", "label", "target",
    "variety", "cultivar", "kind", "cluster", "grade", "condition",
)
TARGET_HINTS = (
    "target", "strength", "label", "response", "outcome", "y", "score",
    "quality", "value",
)


# ---------------------------------------------------------------------------
# Floating point / formatting helpers
# ---------------------------------------------------------------------------

def _norm(x):
    """Normalise -0.0 to 0.0 so JSON output is byte-stable."""
    if isinstance(x, float) and x == 0.0:
        return 0.0
    return x


def _fmt(x, nd=6):
    if x is None:
        return None
    if isinstance(x, bool):
        return x
    if isinstance(x, int):
        return x
    return _norm(round(float(x), nd))


def _fmt_list(xs, nd=6):
    if xs is None:
        return None
    return [_fmt(x, nd) for x in xs]


# ---------------------------------------------------------------------------
# Mathematical special functions (Numerical Recipes style, stdlib only)
# ---------------------------------------------------------------------------

def _betacf(a, b, x):
    MAXIT, EPS, FPMIN = 300, 3e-12, 1e-300
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


def _betai(a, b, x):
    """Regularised incomplete beta function I_x(a, b)."""
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


def _gser(a, x):
    gln = math.lgamma(a)
    ap = a
    total = 1.0 / a
    delta = total
    for _ in range(1, 300):
        ap += 1.0
        delta *= x / ap
        total += delta
        if abs(delta) < abs(total) * 1e-12:
            break
    return total * math.exp(-x + a * math.log(x) - gln)


def _gcf(a, x):
    gln = math.lgamma(a)
    FPMIN, EPS = 1e-300, 3e-12
    b = x + 1.0 - a
    c = 1.0 / FPMIN
    d = 1.0 / b
    h = d
    for i in range(1, 301):
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
    return math.exp(-x + a * math.log(x) - gln) * h


def _gammp(a, x):
    """Regularised lower incomplete gamma P(a, x)."""
    if x < 0.0 or a <= 0.0:
        raise ValueError("invalid arguments to gammp")
    if x < a + 1.0:
        return _gser(a, x)
    return 1.0 - _gcf(a, x)


def _norm_cdf(z):
    return 0.5 * math.erfc(-z / math.sqrt(2.0))


def t_two_tail(t, df):
    if df <= 0:
        return None
    t = abs(t)
    x = df / (df + t * t)
    return _betai(df / 2.0, 0.5, x)


def f_pvalue(F, df1, df2):
    if F < 0 or df1 <= 0 or df2 <= 0:
        return None
    x = df1 * F / (df1 * F + df2)
    return 1.0 - _betai(df1 / 2.0, df2 / 2.0, x)


def chi2_pvalue(x2, df):
    if x2 < 0 or df <= 0:
        return None
    return 1.0 - _gammp(df / 2.0, x2 / 2.0)


# ---------------------------------------------------------------------------
# Basic statistics
# ---------------------------------------------------------------------------

def _mean(vals):
    return sum(vals) / len(vals)


def _var(vals):
    m = _mean(vals)
    return sum((x - m) ** 2 for x in vals) / (len(vals) - 1)


def _sd(vals):
    return math.sqrt(_var(vals))


def percentile(vals, q):
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return None
    if n == 1:
        return s[0]
    rank = (q / 100.0) * (n - 1)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return s[lo]
    frac = rank - lo
    return s[lo] * (1.0 - frac) + s[hi] * frac


def _skew(vals):
    n = len(vals)
    if n < 3:
        return None
    m = _mean(vals)
    sd = _sd(vals)
    if sd == 0:
        return None
    return sum((x - m) ** 3 for x in vals) / ((n - 1) * sd ** 3)


def _kurt(vals):
    n = len(vals)
    if n < 4:
        return None
    m = _mean(vals)
    sd = _sd(vals)
    if sd == 0:
        return None
    return sum((x - m) ** 4 for x in vals) / ((n - 1) * sd ** 4) - 3.0


def describe(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0, "missing": 0}
    n = len(vals)
    sd = _sd(vals) if n >= 2 else None
    q1 = percentile(vals, 25)
    q3 = percentile(vals, 75)
    iqr = (q3 - q1) if (q1 is not None and q3 is not None) else None
    return {
        "n": n,
        "mean": _fmt(_mean(vals)),
        "sd": _fmt(sd),
        "min": _fmt(min(vals)),
        "q1": _fmt(q1),
        "median": _fmt(percentile(vals, 50)),
        "q3": _fmt(q3),
        "max": _fmt(max(vals)),
        "iqr": _fmt(iqr),
        "skewness": _fmt(_skew(vals)),
        "kurtosis": _fmt(_kurt(vals)),
    }


# ---------------------------------------------------------------------------
# Statistical tests
# ---------------------------------------------------------------------------

def _tie_correction(vals):
    from collections import Counter
    n = len(vals)
    if n <= 1:
        return 0.0
    c = Counter(vals)
    tie_sum = sum(t ** 3 - t for t in c.values() if t > 1)
    return tie_sum / (n ** 3 - n)


def welch_t(a, b):
    a = [x for x in a if x is not None]
    b = [x for x in b if x is not None]
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return {"error": "sample too small", "n1": n1, "n2": n2}
    m1, m2 = _mean(a), _mean(b)
    v1, v2 = _var(a), _var(b)
    if v1 == 0 and v2 == 0:
        return {"error": "zero variance in both groups", "n1": n1, "n2": n2}
    se = math.sqrt(v1 / n1 + v2 / n2)
    if se == 0:
        return {"error": "standard error is zero", "n1": n1, "n2": n2}
    t = (m1 - m2) / se
    num = (v1 / n1 + v2 / n2) ** 2
    den = (v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1)
    df = num / den if den > 0 else n1 + n2 - 2
    d = (m1 - m2) / math.sqrt((v1 * (n1 - 1) + v2 * (n2 - 1)) / (n1 + n2 - 2)) if (v1 + v2) > 0 else None
    return {
        "method": "Welch t-test (unequal variances)",
        "t": _fmt(t), "df": _fmt(df), "p": _fmt(t_two_tail(t, df)),
        "mean1": _fmt(m1), "mean2": _fmt(m2),
        "sd1": _fmt(math.sqrt(v1)), "sd2": _fmt(math.sqrt(v2)),
        "n1": n1, "n2": n2, "cohens_d": _fmt(d),
    }


def one_way_anova(groups):
    groups = [[x for x in g if x is not None] for g in groups]
    ks = [g for g in groups if len(g) >= 1]
    if len(ks) < 2:
        return {"error": "fewer than two groups"}
    k = len(ks)
    n = sum(len(g) for g in ks)
    grand_mean = sum(sum(g) for g in ks) / n
    ss_between = sum(len(g) * (_mean(g) - grand_mean) ** 2 for g in ks)
    ss_within = sum(sum((x - _mean(g)) ** 2 for x in g) for g in ks)
    df_between = k - 1
    df_within = n - k
    if df_within <= 0:
        return {"error": "no within-group degrees of freedom"}
    if ss_within <= 0:
        return {"error": "zero within-group variance"}
    ms_between = ss_between / df_between
    ms_within = ss_within / df_within
    F = ms_between / ms_within
    eta2 = ss_between / (ss_between + ss_within)
    omega2 = (ss_between - df_between * ms_within) / (ss_between + ss_within + ms_within) if (ss_between + ss_within + ms_within) > 0 else None
    return {
        "method": "one-way ANOVA",
        "F": _fmt(F), "df1": df_between, "df2": df_within,
        "p": _fmt(f_pvalue(F, df_between, df_within)),
        "ss_between": _fmt(ss_between), "ss_within": _fmt(ss_within),
        "eta_squared": _fmt(eta2), "omega_squared": _fmt(omega2),
        "n": n, "k": k,
    }


def mann_whitney(a, b):
    a = [x for x in a if x is not None]
    b = [x for x in b if x is not None]
    n1, n2 = len(a), len(b)
    if n1 == 0 or n2 == 0:
        return {"error": "empty group"}
    combined = [(v, 0) for v in a] + [(v, 1) for v in b]
    combined.sort(key=lambda x: x[0])
    n = len(combined)
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and combined[j + 1][0] == combined[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    r1 = sum(r for r, (_, g) in zip(ranks, combined) if g == 0)
    u1 = r1 - n1 * (n1 + 1) / 2.0
    u2 = n1 * n2 - u1
    U = min(u1, u2)
    tie = _tie_correction([v for v, _ in combined])
    var_u = (n1 * n2 / 12.0) * ((n + 1) - tie)
    if var_u <= 0:
        return {"error": "zero variance for U statistic"}
    z = (U - n1 * n2 / 2.0) / math.sqrt(var_u)
    return {
        "method": "Mann-Whitney U (normal approximation)",
        "u": _fmt(U), "u1": _fmt(u1), "u2": _fmt(u2),
        "z": _fmt(z), "p": _fmt(2.0 * (1.0 - _norm_cdf(abs(z)))),
        "n1": n1, "n2": n2,
    }


def kruskal_wallis(groups):
    groups = [[x for x in g if x is not None] for g in groups]
    ks = [g for g in groups if len(g) >= 1]
    if len(ks) < 2:
        return {"error": "fewer than two groups"}
    n = sum(len(g) for g in ks)
    combined = []
    for gi, g in enumerate(ks):
        for v in g:
            combined.append((v, gi))
    combined.sort(key=lambda x: x[0])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and combined[j + 1][0] == combined[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    rank_sums = [0.0] * len(ks)
    counts = [len(g) for g in ks]
    for k, (_, gi) in enumerate(combined):
        rank_sums[gi] += ranks[k]
    H = 12.0 / (n * (n + 1)) * sum(rs * rs / c for rs, c in zip(rank_sums, counts)) - 3.0 * (n + 1)
    tie = _tie_correction([v for v, _ in combined])
    if tie > 0:
        H = H / (1.0 - tie)
    df = len(ks) - 1
    return {
        "method": "Kruskal-Wallis H",
        "H": _fmt(H), "df": df, "p": _fmt(chi2_pvalue(H, df)),
        "n": n, "groups": counts,
    }


def pearson(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 3:
        return {"error": "too few paired observations", "n": n}
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mx, my = _mean(xs), _mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    if sxx == 0 or syy == 0:
        return {"error": "zero variance", "n": n}
    r = sxy / math.sqrt(sxx * syy)
    r = max(-1.0, min(1.0, r))
    if abs(r) >= 1.0:
        t, p = None, 0.0
    else:
        t = r * math.sqrt((n - 2) / (1 - r * r))
        p = t_two_tail(t, n - 2)
    return {
        "method": "Pearson r",
        "r": _fmt(r), "n": n, "t": _fmt(t), "p": _fmt(p),
        "r_squared": _fmt(r * r),
    }


def simple_ols(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 3:
        return {"error": "too few paired observations", "n": n}
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mx, my = _mean(xs), _mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    sst = sum((y - my) ** 2 for y in ys)
    if sxx == 0:
        return {"error": "zero variance in predictor", "n": n}
    slope = sxy / sxx
    intercept = my - slope * mx
    sse = sum((y - (slope * x + intercept)) ** 2 for x, y in pairs)
    r2 = (1.0 - sse / sst) if sst > 0 else None
    pcorr = pearson(xs, ys)
    return {
        "method": "simple OLS regression",
        "slope": _fmt(slope), "intercept": _fmt(intercept),
        "r_squared": _fmt(r2), "n": n,
        "pearson_r": pcorr.get("r"), "pearson_p": pcorr.get("p"),
    }


# ---------------------------------------------------------------------------
# Text and data-file readers
# ---------------------------------------------------------------------------

def _read_text(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1"


def _guess_delim(text):
    lines = [ln for ln in text.splitlines() if ln.strip()][:20]
    if not lines:
        return ","
    best, best_count = ",", -1
    for cand in (",", "\t", ";", "|"):
        counts = [ln.count(cand) for ln in lines]
        if counts and all(c > 0 for c in counts) and all(c == counts[0] for c in counts):
            if counts[0] > best_count:
                best, best_count = cand, counts[0]
    return best


def _is_int(s):
    try:
        int(s)
        return True
    except (ValueError, TypeError):
        return False


def _is_float(s):
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False


def _infer_type(vals):
    if not vals:
        return "empty"
    if all(_is_int(v) for v in vals):
        return "int"
    if all(_is_float(v) for v in vals):
        return "float"
    return "string"


def _to_number(v, typ):
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        if v == "":
            return None
    try:
        if typ == "int":
            return int(float(v))
        return float(v)
    except (ValueError, TypeError):
        return None


def _empty_table(source, kind, note):
    return {
        "source": source, "kind": kind, "header": [], "types": [],
        "records": [], "n_rows": 0, "n_cols": 0, "note": note,
    }


def _records_from_rows(header, body, types):
    recs = []
    for r in body:
        rec = {}
        for j, h in enumerate(header):
            v = r[j] if j < len(r) else ""
            if v is None:
                rec[h] = None
                continue
            s = str(v).strip()
            if s == "":
                rec[h] = None
            elif types[j] in ("int", "float"):
                rec[h] = _to_number(s, types[j])
            else:
                rec[h] = s
        recs.append(rec)
    return recs


def load_csv_table(path):
    try:
        text, enc = _read_text(path)
    except OSError as e:
        return _empty_table(path, "csv", "read error: %s" % e)
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",\t;|")
        delim = dialect.delimiter
    except Exception:
        delim = _guess_delim(text)
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    raw = [r for r in reader if any((c or "").strip() for c in r)]
    if not raw:
        return _empty_table(path, "csv", "no non-empty rows")
    header = [str(c).strip() or ("col%d" % i) for i, c in enumerate(raw[0])]
    body = raw[1:]
    # Heuristic: if every header cell parses as a number, the first row is data.
    if body and all(_is_float(str(c).strip()) for c in raw[0] if str(c).strip()):
        header = ["col%d" % i for i in range(len(raw[0]))]
        body = raw
    types = [_infer_type([str(r[j]).strip() for r in body if j < len(r) and str(r[j]).strip()]) for j in range(len(header))]
    records = _records_from_rows(header, body, types)
    return {
        "source": path, "kind": "csv", "header": header, "types": types,
        "records": records, "n_rows": len(records), "n_cols": len(header),
        "delimiter": delim, "encoding": enc, "note": None,
    }


def load_json_table(path):
    try:
        text, enc = _read_text(path)
        obj = json.loads(text)
    except OSError as e:
        return _empty_table(path, "json", "read error: %s" % e)
    except ValueError as e:
        return _empty_table(path, "json", "bad JSON: %s" % e)

    if isinstance(obj, list):
        records = [r for r in obj if isinstance(r, dict)]
        if not records:
            return _empty_table(path, "json", "array has no object rows")
        header = []
        for r in records:
            for k in r:
                if k not in header:
                    header.append(k)
        types = [_infer_type([str(r.get(h, "")).strip() for r in records if str(r.get(h, "")).strip() != ""]) for h in header]
        return {"source": path, "kind": "json", "header": header, "types": types,
                "records": records, "n_rows": len(records), "n_cols": len(header),
                "encoding": enc, "note": None}

    if isinstance(obj, dict):
        cols = obj.get("columns") or obj.get("fields") or obj.get("names") or obj.get("schema")
        data = obj.get("data") if "data" in obj else (obj.get("rows") or obj.get("records"))
        if cols and data is not None:
            header = [str(c) for c in cols]
            if isinstance(data, list) and data and isinstance(data[0], dict):
                records = [r for r in data if isinstance(r, dict)]
            elif isinstance(data, list):
                records = [dict(zip(header, row)) if isinstance(row, (list, tuple)) else {} for row in data]
            else:
                records = []
            types = [_infer_type([str(r.get(h, "")).strip() for r in records if str(r.get(h, "")).strip() != ""]) for h in header]
            return {"source": path, "kind": "json", "header": header, "types": types,
                    "records": records, "n_rows": len(records), "n_cols": len(header),
                    "encoding": enc, "note": None}
        # orient='split' / orient='records' under some key
        for key in ("data", "rows", "records", "table"):
            if key in obj and isinstance(obj[key], list):
                return _json_sub(path, enc, obj[key])
    return _empty_table(path, "json", "unrecognised JSON shape")


def _json_sub(path, enc, arr):
    if not arr or not isinstance(arr[0], (dict, list)):
        return _empty_table(path, "json", "unsupported nested rows")
    if isinstance(arr[0], dict):
        header = []
        for r in arr:
            for k in r:
                if k not in header:
                    header.append(k)
        types = [_infer_type([str(r.get(h, "")).strip() for r in arr if str(r.get(h, "")).strip() != ""]) for h in header]
        return {"source": path, "kind": "json", "header": header, "types": types,
                "records": [r for r in arr if isinstance(r, dict)],
                "n_rows": len(arr), "n_cols": len(header), "encoding": enc, "note": None}
    return _empty_table(path, "json", "unsupported nested rows")


def load_sqlite_table(path):
    try:
        conn = sqlite3.connect(path)
    except sqlite3.Error as e:
        return _empty_table(path, "sqlite", "connect error: %s" % e)
    try:
        cur = conn.cursor()
        tables = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        if not tables:
            conn.close()
            return _empty_table(path, "sqlite", "no user tables")
        # choose the largest table (most rows)
        best, best_rows = tables[0], -1
        for t in tables:
            try:
                cnt = cur.execute('SELECT COUNT(*) FROM "%s"' % t.replace('"', '""')).fetchone()[0]
            except sqlite3.Error:
                cnt = -1
            if cnt > best_rows:
                best, best_rows = t, cnt
        tname = best
        rows = cur.execute('SELECT * FROM "%s"' % tname.replace('"', '""')).fetchall()
        header = [d[0] for d in cur.description]
        conn.close()
    except sqlite3.Error as e:
        try:
            conn.close()
        except Exception:
            pass
        return _empty_table(path, "sqlite", "query error: %s" % e)
    body = [list(r) for r in rows]
    types = [_infer_type([str(r[j]).strip() for r in body if j < len(r) and str(r[j]).strip()]) for j in range(len(header))]
    records = _records_from_rows(header, body, types)
    return {"source": path, "kind": "sqlite", "header": header, "types": types,
            "records": records, "n_rows": len(records), "n_cols": len(header),
            "table": tname, "note": None}


def load_table(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        return load_json_table(path)
    if ext in (".db", ".sqlite", ".sqlite3"):
        return load_sqlite_table(path)
    return load_csv_table(path)


# ---------------------------------------------------------------------------
# Input discovery / case reading
# ---------------------------------------------------------------------------

def _find_key(obj, key):
    """Recursively find the first occurrence of ``key``."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _find_key(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_key(v, key)
            if found is not None:
                return found
    return None


def read_case(input_dir):
    case_path = os.path.join(input_dir, "case.json")
    if not os.path.exists(case_path):
        return {
            "case_id": os.path.basename(os.path.abspath(input_dir)) or "unknown",
            "output_contract": None, "raw": None,
            "error": "missing case.json",
        }
    try:
        text, enc = _read_text(case_path)
        obj = json.loads(text)
    except Exception as e:
        return {"case_id": "unknown", "output_contract": None, "raw": None,
                "error": "bad case.json: %s" % e}
    cid = (obj.get("case_id") or obj.get("challenge_id")
           or obj.get("armiId") or obj.get("id") or "unknown")
    contract = _find_key(obj, "output_contract")
    if contract is None:
        contract = dict(DEFAULT_CONTRACT)
    return {"case_id": cid, "output_contract": contract, "raw": obj, "error": None}


def read_task(input_dir):
    p = os.path.join(input_dir, "task.md")
    if not os.path.exists(p):
        return None
    try:
        text, _ = _read_text(p)
        return text
    except OSError:
        return None


def discover_data(input_dir):
    files = []
    for name in sorted(os.listdir(input_dir)):
        if name in ("case.json", "task.md"):
            continue
        p = os.path.join(input_dir, name)
        if not os.path.isfile(p):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext in (".csv", ".tsv", ".txt", ".json", ".db", ".sqlite", ".sqlite3"):
            try:
                if os.path.getsize(p) <= 20 * 1024 * 1024:
                    files.append(p)
            except OSError:
                pass
    return files


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------

def n_unique(records, col):
    return len(set(str(r.get(col)) for r in records))


def clean_table(table):
    records = table["records"]
    header = table["header"]
    types = table["types"]
    numeric_cols = [h for h, t in zip(header, types) if t in ("int", "float")]

    def key(r):
        return tuple(str(r.get(h, "")) for h in header)

    seen = {}
    dup_count = 0
    kept = []
    for r in records:
        k = key(r)
        if k in seen:
            dup_count += 1
            continue
        seen[k] = 1
        kept.append(r)

    missing_before = {h: sum(1 for r in records if r.get(h) is None) for h in header}
    n_before_missing = len(kept)
    if numeric_cols:
        kept = [r for r in kept if all(r.get(h) is not None for h in numeric_cols)]
    dropped_missing = n_before_missing - len(kept)

    outliers = {}
    for h in numeric_cols:
        vals = [r[h] for r in kept if r[h] is not None]
        if len(vals) < 4:
            continue
        q1 = percentile(vals, 25)
        q3 = percentile(vals, 75)
        iqr = q3 - q1
        if iqr <= 0:
            continue
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        n_out = sum(1 for r in kept if r[h] is not None and (r[h] < lo or r[h] > hi))
        outliers[h] = {
            "q1": _fmt(q1), "q3": _fmt(q3), "iqr": _fmt(iqr),
            "lower_fence": _fmt(lo), "upper_fence": _fmt(hi),
            "n_outliers": n_out, "rule": "Tukey IQR, marked not removed",
        }

    clean_meta = {
        "source": table["source"],
        "kind": table["kind"],
        "original_rows": len(records),
        "duplicates_dropped": dup_count,
        "rows_after_dedup": n_before_missing,
        "rows_dropped_for_missing": dropped_missing,
        "rows_kept": len(kept),
        "missing_handling_rule": (
            "complete-case on numeric columns" if numeric_cols
            else "no numeric columns, no missing-based dropping"),
        "missing_before": {h: missing_before.get(h, 0) for h in header},
        "outlier_flags": outliers,
        "columns": header,
        "numeric_columns": numeric_cols,
        "consistency_check": {
            "dropped_total": dup_count + dropped_missing,
            "computed": len(records) - len(kept),
            "ok": (dup_count + dropped_missing) == (len(records) - len(kept)),
        },
    }
    return kept, clean_meta


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def pick_group(records, header, types):
    cat_cols = []
    for h, t in zip(header, types):
        if t in ("int", "float", "empty"):
            continue
        u = n_unique(records, h)
        if 2 <= u <= 20:
            cat_cols.append((h, u))
    if not cat_cols:
        return None
    for h, u in cat_cols:
        if any(k in h.lower() for k in GROUP_HINTS):
            return h
    cat_cols.sort(key=lambda x: x[1])
    return cat_cols[0][0]


def pick_target(header, types):
    numeric = [h for h, t in zip(header, types) if t in ("int", "float")]
    for h in numeric:
        if any(k in h.lower() for k in TARGET_HINTS):
            return h
    return numeric[-1] if numeric else None


def analyze(records, header, types):
    numeric_cols = [h for h, t in zip(header, types) if t in ("int", "float")]
    if not numeric_cols:
        return {"error": "no numeric columns"}, None

    descriptive = {}
    for h in numeric_cols:
        descriptive[h] = describe([r.get(h) for r in records])

    group_col = pick_group(records, header, types)
    group_tests = {}
    group_desc = {}
    if group_col:
        order = []
        group_values = OrderedDict()
        group_num = OrderedDict()
        for r in records:
            g = str(r.get(group_col))
            if g not in group_values:
                group_values[g] = []
                order.append(g)
            group_values[g].append(r.get(group_col))
            for h in numeric_cols:
                group_num.setdefault((g, h), []).append(r.get(h))

        for h in numeric_cols:
            gvals = [group_num[(g, h)] for g in order]
            labels = list(order)
            n_groups = len([g for g in gvals if len(g) >= 2])
            desc = OrderedDict()
            for g, vals in zip(labels, gvals):
                desc[g] = describe(vals)
            tests = {}
            if n_groups == 2:
                tests["welch_t"] = welch_t(gvals[0], gvals[1])
                tests["mann_whitney"] = mann_whitney(gvals[0], gvals[1])
            elif n_groups >= 3:
                tests["anova"] = one_way_anova(gvals)
                tests["kruskal_wallis"] = kruskal_wallis(gvals)
            else:
                tests = {"error": "group comparison not possible"}
            group_tests[h] = {"groups": labels, "per_group": desc, "tests": tests}
        group_desc = {"group_column": group_col, "groups": list(order),
                      "group_sizes": {g: len(group_values[g]) for g in order}}

    # correlations among numeric columns
    corr_pairs = []
    for i in range(len(numeric_cols)):
        for j in range(i + 1, len(numeric_cols)):
            a, b = numeric_cols[i], numeric_cols[j]
            r = pearson([r.get(a) for r in records], [r.get(b) for r in records])
            if "error" not in r:
                corr_pairs.append({"x": a, "y": b, **r})
    corr_pairs.sort(key=lambda d: abs(d.get("r") or 0.0), reverse=True)

    # target / regression (single-predictor OLS against a target column)
    regression = {}
    target = pick_target(header, types)
    if target and len(numeric_cols) >= 2:
        for h in numeric_cols:
            if h == target:
                continue
            regression[h] = simple_ols([r.get(h) for r in records], [r.get(target) for r in records])

    stats = {
        "n_rows": len(records),
        "numeric_columns": numeric_cols,
        "descriptive": descriptive,
        "group_column": group_col,
        "group_comparison": group_tests if group_col else {},
        "group_summary": group_desc if group_col else None,
        "correlations": corr_pairs,
        "correlation_note": (
            "Pearson r computed pairwise on complete observations; "
            "p-values are two-tailed t-tests with df=n-2, uncorrected for multiple comparisons."),
        "regression": regression,
        "target_column": target,
        "test_assumptions": {
            "welch_t": "does not assume equal variances; normality only approximately checked via skewness/kurtosis",
            "anova": "assumes independence, approximate normality and equal variances; Levene not available in stdlib, variance homogeneity approximated from per-group SD",
            "mann_whitney": "rank-based, no normality assumption",
            "kruskal_wallis": "rank-based, no normality assumption",
            "pearson": "linear association; p-value assumes bivariate normality",
            "multiple_comparison_note": "no multiplicity correction applied; report p-values as nominal",
        },
    }
    return stats, group_col


# ---------------------------------------------------------------------------
# SVG rendering (deterministic, stdlib only)
# ---------------------------------------------------------------------------

def _svg_esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _five_number(vals):
    s = sorted(vals)
    n = len(s)
    return {
        "min": s[0], "q1": percentile(s, 25), "median": percentile(s, 50),
        "q3": percentile(s, 75), "max": s[-1], "n": n,
    }


def boxplot_svg(group_labels, group_values, title, ylabel, unit=""):
    summaries = [_five_number([v for v in g if v is not None]) for g in group_values]
    summaries = [s for s in summaries if s["n"] > 0]
    labels = [lab for lab, g in zip(group_labels, group_values)
              if len([v for v in g if v is not None]) > 0]
    if not summaries:
        return None
    all_vals = [v for g in group_values for v in g if v is not None]
    ymin, ymax = min(all_vals), max(all_vals)
    pad = (ymax - ymin) * 0.12
    if pad == 0:
        pad = abs(ymax) * 0.1 or 1.0
    ymin -= pad
    ymax += pad

    W = max(340, 90 + 96 * len(summaries))
    H = 400
    ml, mr, mt, mb = 78, 24, 56, 66
    pw, ph = W - ml - mr, H - mt - mb

    def y(v):
        return mt + ph - (v - ymin) / (ymax - ymin) * ph

    parts = []
    parts.append(
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" font-family="sans-serif">' % (W, H, W, H))
    parts.append('<rect x="0" y="0" width="%d" height="%d" fill="white"/>' % (W, H))
    parts.append('<text x="%d" y="32" text-anchor="middle" font-size="16" font-weight="bold">%s</text>'
                 % (W // 2, _svg_esc(title)))
    parts.append('<text x="%d" y="%d" text-anchor="end" font-size="12">%s%s</text>'
                 % (ml - 8, mt - 8, _svg_esc(ylabel), (" (%s)" % unit) if unit else ""))

    # y grid + ticks
    for i in range(6):
        v = ymin + (ymax - ymin) * i / 5.0
        yy = y(v)
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e5e5e5"/>'
                     % (ml, yy, ml + pw, yy))
        parts.append('<text x="%d" y="%.1f" text-anchor="end" font-size="11">%s</text>'
                     % (ml - 6, yy + 4, _fmt(v, 3)))
    parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#333"/>'
                 % (ml, mt + ph, ml + pw, mt + ph))

    box_w = min(64.0, 0.6 * pw / max(1, len(summaries)))
    for idx, (s, lab) in enumerate(zip(summaries, labels)):
        cx = ml + (idx + 0.5) * pw / len(summaries)
        x0, x1 = cx - box_w / 2, cx + box_w / 2
        yq1, ymed, yq3 = y(s["q1"]), y(s["median"]), y(s["q3"])
        ymin_, ymax_ = y(s["min"]), y(s["max"])
        # whiskers
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#555"/>'
                     % (cx, ymin_, cx, yq1))
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#555"/>'
                     % (cx, yq3, cx, ymax_))
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#555"/>'
                     % (cx - box_w / 4, ymin_, cx + box_w / 4, ymin_))
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#555"/>'
                     % (cx - box_w / 4, ymax_, cx + box_w / 4, ymax_))
        # box
        parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                     'fill="#4c8bf5" fill-opacity="0.35" stroke="#2b5cb0"/>'
                     % (x0, yq3, box_w, (yq1 - yq3)))
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#1f3f7a" stroke-width="2"/>'
                     % (x0, ymed, x1, ymed))
        parts.append('<text x="%.1f" y="%d" text-anchor="middle" font-size="11">%s</text>'
                     % (cx, H - mb + 16, _svg_esc(lab)))
        parts.append('<text x="%.1f" y="%d" text-anchor="middle" font-size="10" fill="#555">n=%d</text>'
                     % (cx, H - mb + 32, s["n"]))
    parts.append('<text x="%d" y="%d" text-anchor="end" font-size="11">whiskers=min/max, box=Q1-Q3, line=median</text>'
                 % (ml + pw, H - 6))
    parts.append("</svg>")
    return "\n".join(parts)


def bar_svg(group_labels, means, sds, ns, title, ylabel, unit=""):
    if not group_labels:
        return None
    ymin = 0.0
    top = max((m + (s or 0)) for m, s in zip(means, sds) if m is not None)
    ymax = top * 1.15 if top > 0 else 1.0
    W = max(340, 90 + 96 * len(group_labels))
    H = 400
    ml, mr, mt, mb = 78, 24, 56, 66
    pw, ph = W - ml - mr, H - mt - mb

    def y(v):
        return mt + ph - (v - ymin) / (ymax - ymin) * ph

    parts = []
    parts.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
                 'viewBox="0 0 %d %d" font-family="sans-serif">' % (W, H, W, H))
    parts.append('<rect x="0" y="0" width="%d" height="%d" fill="white"/>' % (W, H))
    parts.append('<text x="%d" y="32" text-anchor="middle" font-size="16" font-weight="bold">%s</text>'
                 % (W // 2, _svg_esc(title)))
    parts.append('<text x="%d" y="%d" text-anchor="end" font-size="12">%s%s</text>'
                 % (ml - 8, mt - 8, _svg_esc(ylabel), (" (%s)" % unit) if unit else ""))
    for i in range(6):
        v = ymin + (ymax - ymin) * i / 5.0
        yy = y(v)
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e5e5e5"/>'
                     % (ml, yy, ml + pw, yy))
        parts.append('<text x="%d" y="%.1f" text-anchor="end" font-size="11">%s</text>'
                     % (ml - 6, yy + 4, _fmt(v, 3)))
    parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#333"/>'
                 % (ml, mt + ph, ml + pw, mt + ph))

    bw = min(56.0, 0.55 * pw / max(1, len(group_labels)))
    for idx, (lab, m, s, n) in enumerate(zip(group_labels, means, sds, ns)):
        cx = ml + (idx + 0.5) * pw / len(group_labels)
        if m is None:
            continue
        x0, x1 = cx - bw / 2, cx + bw / 2
        ybar = y(m)
        parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                     'fill="#4c8bf5" fill-opacity="0.6" stroke="#2b5cb0"/>'
                     % (x0, ybar, bw, (y(0.0) - ybar)))
        if s is not None:
            yhi, ylo = y(m + s), y(m - s)
            parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#1f3f7a" stroke-width="2"/>'
                         % (cx, yhi, cx, ylo))
            parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#1f3f7a"/>'
                         % (cx - bw / 5, yhi, cx + bw / 5, yhi))
            parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#1f3f7a"/>'
                         % (cx - bw / 5, ylo, cx + bw / 5, ylo))
        parts.append('<text x="%.1f" y="%.1f" text-anchor="middle" font-size="10">%.4g</text>'
                     % (cx, ybar - 6, m))
        parts.append('<text x="%.1f" y="%d" text-anchor="middle" font-size="11">%s</text>'
                     % (cx, H - mb + 16, _svg_esc(lab)))
        parts.append('<text x="%.1f" y="%d" text-anchor="middle" font-size="10" fill="#555">n=%d</text>'
                     % (cx, H - mb + 32, n))
    parts.append('<text x="%d" y="%d" text-anchor="end" font-size="11">bars=mean, error bars=SD</text>'
                 % (ml + pw, H - 6))
    parts.append("</svg>")
    return "\n".join(parts)


def corr_bar_svg(pairs, title, top=10):
    pairs = [p for p in pairs if p.get("r") is not None][:top]
    if not pairs:
        return None
    labels = ["%s vs %s" % (p["x"], p["y"]) for p in pairs]
    vals = [abs(p["r"]) for p in pairs]
    vmax = max(vals) * 1.1 if vals else 1.0
    row_h = 30
    H = 80 + row_h * len(pairs)
    W = 720
    ml, mr, mt, mb = 300, 40, 40, 20
    pw = W - ml - mr

    parts = []
    parts.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
                 'viewBox="0 0 %d %d" font-family="sans-serif">' % (W, H, W, H))
    parts.append('<rect x="0" y="0" width="%d" height="%d" fill="white"/>' % (W, H))
    parts.append('<text x="20" y="26" font-size="16" font-weight="bold">%s</text>' % _svg_esc(title))
    for idx, (lab, v, p) in enumerate(zip(labels, vals, pairs)):
        yy = mt + idx * row_h + row_h / 2
        bw = pw * (v / vmax)
        parts.append('<text x="%d" y="%.1f" text-anchor="end" font-size="11">%s</text>'
                     % (ml - 8, yy + 4, _svg_esc(lab)))
        parts.append('<rect x="%d" y="%.1f" width="%.1f" height="14" fill="#4c8bf5" fill-opacity="0.7"/>'
                     % (ml, yy - 7, bw))
        parts.append('<text x="%.1f" y="%.1f" font-size="10" fill="#333">r=%.4g</text>'
                     % (ml + bw + 6, yy + 4, p["r"]))
    parts.append('<text x="%d" y="%d" font-size="10" fill="#555">bars=|Pearson r|</text>'
                 % (ml, H - 4))
    parts.append("</svg>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Report generation (numbers re-printed from stats.json)
# ---------------------------------------------------------------------------

def _pct(x):
    return "%.4g" % x


def render_report(case_id, table, clean_meta, stats, group_col):
    lines = []
    lines.append("# 多步骤数据分析报告")
    lines.append("")
    lines.append("- case_id: `%s`" % case_id)
    lines.append("- 数据来源: `%s`（%s）" % (table.get("source", "?"), table.get("kind", "?")))
    lines.append("- 原始行数: %d；列数: %d" % (table.get("n_rows", 0), table.get("n_cols", 0)))
    lines.append("")
    lines.append("> 本报告所有数字均由 `work/stats.json` 与 `work/clean_meta.json` 回填，可回算。")
    lines.append("")

    lines.append("## 1. 字段字典与清洗")
    lines.append("")
    lines.append("| 列 | 类型 | 缺失(清洗前) |")
    lines.append("|---|---|---|")
    for h, t in zip(table["header"], table["types"]):
        miss = clean_meta["missing_before"].get(h, 0)
        lines.append("| `%s` | %s | %d |" % (h, t, miss))
    lines.append("")
    lines.append("清洗规则（与 `clean_meta.json` 完全一致）：")
    lines.append("- 完全重复行删除: %d 行（删除依据：所有列取值完全相同的记录）" % clean_meta["duplicates_dropped"])
    lines.append("- 缺失处理: %s；因缺失删除 %d 行" % (clean_meta["missing_handling_rule"], clean_meta["rows_dropped_for_missing"]))
    lines.append("- 清洗后保留: %d 行" % clean_meta["rows_kept"])
    lines.append("- 行数守恒校验: %s（删行总数 %d == 原始行数 − 清洗后行数）" % (
        "通过" if clean_meta["consistency_check"]["ok"] else "失败",
        clean_meta["consistency_check"]["dropped_total"]))
    lines.append("")
    outlier_cols = {h: v for h, v in clean_meta["outlier_flags"].items() if v.get("n_outliers", 0) > 0}
    if outlier_cols:
        lines.append("异常值（Tukey IQR 标记，未删除）：")
        for h, v in outlier_cols.items():
            lines.append("- `%s`: %d 个异常值（下界 %.4g，上界 %.4g）" % (h, v["n_outliers"], v["lower_fence"], v["upper_fence"]))
    else:
        lines.append("异常值（Tukey IQR 标记，未删除）：无列检出异常值。")
    lines.append("")

    lines.append("## 2. 描述统计")
    lines.append("")
    lines.append("| 列 | n | mean | SD | min | Q1 | median | Q3 | max |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for h in stats["numeric_columns"]:
        d = stats["descriptive"].get(h, {})
        lines.append("| `%s` | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            h, d.get("n", "-"), d.get("mean", "-"), d.get("sd", "-"),
            d.get("min", "-"), d.get("q1", "-"), d.get("median", "-"),
            d.get("q3", "-"), d.get("max", "-")))
    lines.append("")

    if group_col:
        lines.append("## 3. 分组比较（分组列: `%s`）" % group_col)
        lines.append("")
        lines.append("统计口径: 两组用 Welch t 检验与 Mann-Whitney U；三组及以上用单因素 ANOVA 与 Kruskal-Wallis H。p 值为名义值，未做多重比较校正。")
        lines.append("")
        for h in stats["numeric_columns"]:
            gt = stats["group_comparison"].get(h)
            if not gt:
                continue
            tests = gt["tests"]
            lines.append("### `%s`" % h)
            lines.append("")
            lines.append("| 组 | n | mean | SD | median |")
            lines.append("|---|---|---|---|---|")
            for g in gt["groups"]:
                d = gt["per_group"].get(g, {})
                lines.append("| `%s` | %s | %s | %s | %s |" % (
                    g, d.get("n", "-"), d.get("mean", "-"), d.get("sd", "-"), d.get("median", "-")))
            lines.append("")
            for tname, t in tests.items():
                if isinstance(t, dict) and "error" in t:
                    lines.append("- %s: %s" % (tname, t["error"]))
                elif isinstance(t, dict):
                    lines.append("- %s: %s" % (t.get("method", tname),
                                               ", ".join("%s=%s" % (k, v) for k, v in t.items() if k != "method")))
            lines.append("")
    else:
        lines.append("## 3. 相关分析")
        lines.append("")
        lines.append(stats.get("correlation_note", ""))
        lines.append("")
        lines.append("| 变量对 | r | r² | n | p |")
        lines.append("|---|---|---|---|---|")
        for c in stats["correlations"][:10]:
            lines.append("| `%s` vs `%s` | %s | %s | %s | %s |" % (
                c["x"], c["y"], c.get("r", "-"), c.get("r_squared", "-"),
                c.get("n", "-"), c.get("p", "-")))
        lines.append("")
        if stats.get("target_column"):
            lines.append("## 4. 单变量 OLS 回归（目标: `%s`）" % stats["target_column"])
            lines.append("")
            lines.append("| 预测列 | 斜率 | 截距 | R² | n | r | r 的 p |")
            lines.append("|---|---|---|---|---|---|---|")
            for h, m in stats["regression"].items():
                if "error" in m:
                    lines.append("| `%s` | %s | | | | | |" % (h, m["error"]))
                else:
                    lines.append("| `%s` | %s | %s | %s | %s | %s | %s |" % (
                        h, m.get("slope", "-"), m.get("intercept", "-"),
                        m.get("r_squared", "-"), m.get("n", "-"),
                        m.get("pearson_r", "-"), m.get("pearson_p", "-")))
            lines.append("")

    lines.append("## 5. 不确定性与限制")
    lines.append("")
    lines.append("- 检验前提校验为近似校验（基于偏度/峰度与组内标准差），未做 Shapiro-Wilk 或 Levene 检验（标准库无实现）。")
    lines.append("- 多重比较未校正，p 值为名义值。")
    lines.append("- 缺失值按完整案例分析删除，可能引入选择偏差；异常值仅标记未删除。")
    lines.append("")
    lines.append("## 6. 产物清单")
    lines.append("")
    lines.append("- `cleaned.csv`：清洗后数据表")
    lines.append("- `clean_meta.json`：清洗规则与行数影响")
    lines.append("- `stats.json`：全部统计结果（报告数字的唯一来源）")
    lines.append("- `figure_*.svg`：可编辑矢量图")
    lines.append("- `result.json`：结构化最终结果与证据")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Contract filling
# ---------------------------------------------------------------------------

def _list_hint(spec):
    if isinstance(spec, str):
        return any(k in spec.lower() for k in ("list", "array", "[]", "notes", "figures", "charts"))
    if isinstance(spec, list):
        return True
    return False


def build_computed(output_dir):
    return {
        "analysis_results": _load_json_optional(os.path.join(output_dir, "stats.json")),
        "statistics": _load_json_optional(os.path.join(output_dir, "stats.json")),
        "quality_evidence": _load_json_optional(os.path.join(output_dir, "clean_meta.json")),
        "reproducibility": _load_json_optional(os.path.join(output_dir, "clean_meta.json")),
        "anomaly_notes": _anomaly_notes(output_dir),
        "report": _rel_or_none(output_dir, "report.md"),
        "report_path": _rel_or_none(output_dir, "report.md"),
        "cleaned_table": _rel_or_none(output_dir, "cleaned.csv"),
        "cleaned_csv": _rel_or_none(output_dir, "cleaned.csv"),
        "figures": _figure_list(output_dir),
        "charts": _figure_list(output_dir),
        "visualizations": _figure_list(output_dir),
    }


def _load_json_optional(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def _rel_or_none(output_dir, name):
    p = os.path.join(output_dir, name)
    return name if os.path.exists(p) else None


def _figure_list(output_dir):
    out = []
    if os.path.isdir(output_dir):
        for n in sorted(os.listdir(output_dir)):
            if n.startswith("figure_") and n.endswith(".svg"):
                out.append(n)
    return out


def _anomaly_notes(output_dir):
    meta = _load_json_optional(os.path.join(output_dir, "clean_meta.json"))
    if not meta:
        return []
    notes = []
    for h, v in meta.get("outlier_flags", {}).items():
        if v.get("n_outliers", 0) > 0:
            notes.append("column '%s' has %d IQR outliers (marked, not removed)" % (h, v["n_outliers"]))
    if meta.get("duplicates_dropped", 0) > 0:
        notes.append("%d exact-duplicate rows dropped" % meta["duplicates_dropped"])
    if meta.get("rows_dropped_for_missing", 0) > 0:
        notes.append("%d rows dropped for missing numeric values" % meta["rows_dropped_for_missing"])
    return notes


def fill_contract(contract, computed, change_log):
    """Return (top_level_fields, filled_ok, missing_fields)."""
    fields = {}
    filled_ok = True
    missing = []

    if isinstance(contract, dict):
        items = list(contract.items())
    elif isinstance(contract, list):
        items = [(str(c), None) for c in contract]
    else:
        items = list(DEFAULT_CONTRACT.items())

    for name, spec in items:
        name = str(name)
        low = name.lower()
        matched = False
        value = None
        if "analysis_result" in low or low in ("analysis", "main_result", "result_summary"):
            value = computed["analysis_results"]
            matched = True
        elif "statistic" in low or low == "stats" or "test" in low:
            value = computed["statistics"]
            matched = True
        elif any(k in low for k in ("quality", "reproduc", "evidence", "proof")):
            value = computed["quality_evidence"]
            matched = True
        elif any(k in low for k in ("anomal", "exception", "issue", "error_note")):
            value = computed["anomaly_notes"]
            matched = True
        elif "report" in low:
            value = computed["report"]
            matched = True
        elif any(k in low for k in ("figure", "chart", "plot", "visual", "diagram", "graph")):
            value = computed["figures"]
            matched = True
        elif any(k in low for k in ("clean", "table", "csv", "data_file")):
            value = computed["cleaned_table"]
            matched = True

        if not matched:
            value = [] if _list_hint(spec) else None
            missing.append(name)
        fields[name] = value
        change_log.append({
            "step": "contract_fill",
            "field": name,
            "spec": spec,
            "filled": matched,
            "action": "filled" if matched else "defaulted to null/[]",
        })
    filled_ok = (len(missing) == 0)
    return fields, filled_ok, missing


# ---------------------------------------------------------------------------
# Result assembly
# ---------------------------------------------------------------------------

def assemble_result(case, contract_fields, status, sources, uncertainty,
                    pending_human, errors, degradations, change_log, runtime,
                    output_dir):
    result = OrderedDict()
    result["case_id"] = case["case_id"]
    result["status"] = status
    for k in sorted(contract_fields.keys()):
        result[k] = contract_fields[k]
    result["sources"] = sources
    result["uncertainty"] = uncertainty
    result["pending_human"] = pending_human
    result["change_log"] = change_log
    result["errors"] = errors
    result["degradations"] = degradations
    result["runtime_seconds"] = _fmt(runtime, 3)
    result["skill_version"] = VERSION
    # also record the original contract verbatim for auditability
    result["_output_contract_source"] = case.get("output_contract")
    result["_artifacts"] = {
        "report": _rel_or_none(output_dir, "report.md"),
        "cleaned_csv": _rel_or_none(output_dir, "cleaned.csv"),
        "stats_json": _rel_or_none(output_dir, "stats.json"),
        "clean_meta_json": _rel_or_none(output_dir, "clean_meta.json"),
        "profile_json": _rel_or_none(output_dir, "profile.json"),
        "figures": _figure_list(output_dir),
    }
    return result


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def write_csv(path, records, header):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in records:
            w.writerow([r.get(h) for h in header])


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

def step_scaffold(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    case = read_case(args.input)
    change_log = [{
        "step": "scaffold",
        "action": "partial result skeleton written early",
        "time_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
    }]
    if case["output_contract"] is None:
        contract_fields = {}
    elif isinstance(case["output_contract"], dict):
        contract_fields = {str(k): ([] if _list_hint(v) else None)
                           for k, v in case["output_contract"].items()}
    elif isinstance(case["output_contract"], list):
        contract_fields = {str(c): None for c in case["output_contract"]}
    else:
        contract_fields = {}

    result = OrderedDict()
    result["case_id"] = case["case_id"]
    result["status"] = "partial"
    for k in sorted(contract_fields.keys()):
        result[k] = contract_fields[k]
    result["sources"] = []
    result["uncertainty"] = []
    result["pending_human"] = []
    result["change_log"] = change_log
    result["errors"] = [case["error"]] if case.get("error") else []
    result["degradations"] = []
    result["runtime_seconds"] = 0.0
    result["skill_version"] = VERSION
    if case.get("output_contract") is not None:
        result["_output_contract_source"] = case["output_contract"]
    write_json(os.path.join(args.output, "result.json"), result)
    print("scaffold: wrote partial result.json (case_id=%s, status=partial)"
          % case["case_id"])
    return 0


def step_profile(args):
    os.makedirs(args.work, exist_ok=True)
    case = read_case(args.input)
    files = discover_data(args.input)
    profile = {
        "case_id": case["case_id"],
        "input_dir": args.input,
        "data_files": files,
        "tables": [],
        "errors": [],
    }
    if not files:
        profile["errors"].append("no data files found in input directory")
    for fp in files:
        t = load_table(fp)
        entry = {
            "source": t["source"], "kind": t["kind"],
            "n_rows": t["n_rows"], "n_cols": t["n_cols"],
            "header": t["header"], "types": t["types"],
            "note": t.get("note"),
        }
        profile["tables"].append(entry)
        if t.get("note"):
            profile["errors"].append("%s: %s" % (fp, t["note"]))
    write_json(os.path.join(args.work, "profile.json"), profile)
    print("profile: %d data file(s) discovered -> work/profile.json" % len(files))
    return 0


def _select_primary_table(tables):
    usable = [t for t in tables if t["n_rows"] > 0]
    if not usable:
        return None
    usable.sort(key=lambda t: -t["n_rows"])
    return usable[0]


def step_all(args):
    t0 = time.time()
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    case = read_case(args.input)
    task_text = read_task(args.input)
    files = discover_data(args.input)

    errors = []
    degradations = []
    sources = []
    uncertainty = []
    pending_human = []
    change_log = []

    if case.get("error"):
        errors.append(case["error"])
    if not files:
        errors.append("no data files found in input directory")

    tables = []
    for fp in files:
        t = load_table(fp)
        tables.append(t)
        if t.get("note"):
            degradations.append("%s: %s" % (fp, t["note"]))

    table = _select_primary_table(tables)

    status = "partial"
    contract = case.get("output_contract") or dict(DEFAULT_CONTRACT)

    if table is None:
        status = "insufficient_evidence"
        if case.get("error") and "case.json" in str(case.get("error")):
            status = "input_error"
        change_log.append({
            "step": "discovery",
            "action": "no usable data table found",
            "error": "no data files or empty tables in input directory",
        })
    else:
        sources.append({
            "locator": table["source"],
            "quote": "primary table with %d rows, %d columns" % (table["n_rows"], table["n_cols"]),
            "role": "primary_data",
        })
        if task_text is not None:
            sources.append({
                "locator": os.path.join(args.input, "task.md"),
                "quote": task_text[:500],
                "role": "task_requirement",
            })
        sources.append({
            "locator": os.path.join(args.input, "case.json"),
            "quote": "case_id=%s; output_contract keys=%s" % (
                case["case_id"], ", ".join(str(k) for k in _contract_keys(contract))),
            "role": "case_meta",
        })

        kept, clean_meta = clean_table(table)
        change_log.append({
            "step": "clean",
            "action": "dedupe + complete-case missing handling (numeric columns)",
            "duplicates_dropped": clean_meta["duplicates_dropped"],
            "rows_dropped_for_missing": clean_meta["rows_dropped_for_missing"],
            "rows_kept": clean_meta["rows_kept"],
        })
        if not clean_meta["consistency_check"]["ok"]:
            errors.append("cleaning row-count consistency check failed")

        write_csv(os.path.join(args.output, "cleaned.csv"), kept, table["header"])
        write_csv(os.path.join(args.work, "cleaned.csv"), kept, table["header"])
        write_json(os.path.join(args.output, "clean_meta.json"), clean_meta)
        write_json(os.path.join(args.work, "clean_meta.json"), clean_meta)

        stats, group_col = analyze(kept, table["header"], table["types"])
        write_json(os.path.join(args.output, "stats.json"), stats)
        write_json(os.path.join(args.work, "stats.json"), stats)

        # figures
        figure_names = []
        if group_col and stats.get("group_comparison"):
            # boxplot + bar for the column with the smallest ANOVA/Welch p
            ordered_cols = _order_columns_by_p(stats)
            for h in ordered_cols[:2]:
                gt = stats["group_comparison"][h]
                labels = gt["groups"]
                vals = [[v for v in _group_vals(kept, group_col, g, h)] for g in labels]
                svg1 = boxplot_svg(labels, vals, "Boxplot of %s by %s" % (h, group_col), h)
                svg2 = bar_svg(labels, [gt["per_group"][g].get("mean") for g in labels],
                               [gt["per_group"][g].get("sd") for g in labels],
                               [gt["per_group"][g].get("n") for g in labels],
                               "Mean (SD) of %s by %s" % (h, group_col), h)
                if svg1:
                    fn = "figure_%d_boxplot_%s.svg" % (len(figure_names) + 1, _slug(h))
                    write_text(os.path.join(args.output, fn), svg1)
                    figure_names.append(fn)
                if svg2:
                    fn = "figure_%d_bars_%s.svg" % (len(figure_names) + 1, _slug(h))
                    write_text(os.path.join(args.output, fn), svg2)
                    figure_names.append(fn)
        elif stats.get("correlations"):
            svg = corr_bar_svg(stats["correlations"], "Top |Pearson r| among numeric columns")
            if svg:
                write_text(os.path.join(args.output, "figure_1_correlation.svg"), svg)
                figure_names.append("figure_1_correlation.svg")

        report = render_report(case["case_id"], table, clean_meta, stats, group_col)
        write_text(os.path.join(args.output, "report.md"), report)
        write_text(os.path.join(args.work, "report.md"), report)

        profile = {
            "case_id": case["case_id"], "data_files": files,
            "tables": [{"source": t["source"], "kind": t["kind"],
                        "n_rows": t["n_rows"], "n_cols": t["n_cols"],
                        "header": t["header"], "types": t["types"],
                        "note": t.get("note")} for t in tables],
        }
        write_json(os.path.join(args.output, "profile.json"), profile)
        write_json(os.path.join(args.work, "profile.json"), profile)

        # copy figure files into work dir as well
        for fn in figure_names:
            src = os.path.join(args.output, fn)
            try:
                with open(src, "r", encoding="utf-8") as f:
                    content = f.read()
                with open(os.path.join(args.work, fn), "w", encoding="utf-8") as f:
                    f.write(content)
            except OSError:
                pass

        computed = build_computed(args.output)
        fields, filled_ok, missing = fill_contract(contract, computed, change_log)
        if not filled_ok:
            uncertainty.append({"item": "unfilled output_contract fields",
                                "reason": "fields not computable from this dataset: %s" % ", ".join(missing)})
            pending_human.append("confirm whether fields [%s] require additional analysis" % ", ".join(missing))
        if missing:
            degradations.append("output_contract fields defaulted to null: %s" % ", ".join(missing))

        if stats.get("error"):
            errors.append(stats["error"])

        uncertainty.append({
            "item": "statistical assumptions",
            "reason": "normality/homoscedasticity checked approximately; no Shapiro-Wilk/Levene in stdlib",
        })
        uncertainty.append({
            "item": "multiple comparisons",
            "reason": "p-values are nominal, no multiplicity correction",
        })

        status = "ok" if (filled_ok and not errors) else "partial"

    runtime = time.time() - t0
    result = assemble_result(case, fields if table is not None else _default_fields(contract),
                             status, sources, uncertainty, pending_human,
                             errors, degradations, change_log, runtime, args.output)
    write_json(os.path.join(args.output, "result.json"), result)
    print("all: status=%s runtime=%.1fs" % (status, runtime))
    return 0


def _contract_keys(contract):
    if isinstance(contract, dict):
        return list(contract.keys())
    if isinstance(contract, list):
        return [str(c) for c in contract]
    return list(DEFAULT_CONTRACT.keys())


def _default_fields(contract):
    if isinstance(contract, dict):
        return {str(k): None for k in contract.keys()}
    if isinstance(contract, list):
        return {str(c): None for c in contract}
    return {str(k): None for k in DEFAULT_CONTRACT.keys()}


def _order_columns_by_p(stats):
    cols = list(stats["group_comparison"].keys())
    def pval(h):
        gt = stats["group_comparison"][h]
        for t in gt["tests"].values():
            if isinstance(t, dict) and "p" in t and t["p"] is not None:
                return float(t["p"])
        return 1.0
    cols.sort(key=pval)
    return cols


def _group_vals(records, group_col, g, col):
    return [r.get(col) for r in records if str(r.get(group_col)) == g]


def _slug(s):
    out = []
    for ch in str(s):
        if ch.isalnum() or ch in ("_", "-"):
            out.append(ch)
        else:
            out.append("_")
    return "".join(out).strip("_") or "col"


def step_finalize(args):
    if not os.path.exists(os.path.join(args.output, "result.json")):
        return step_all(args)
    # re-fill contract from existing artifacts without recomputing statistics
    case = read_case(args.input)
    result = _load_json_optional(os.path.join(args.output, "result.json")) or {}
    computed = build_computed(args.output)
    contract = case.get("output_contract") or result.get("_output_contract_source") or dict(DEFAULT_CONTRACT)
    change_log = result.get("change_log") or []
    fields, filled_ok, missing = fill_contract(contract, computed, change_log)
    for k, v in fields.items():
        result[k] = v
    result["case_id"] = case["case_id"] or result.get("case_id")
    result["change_log"] = change_log
    if not filled_ok:
        result["status"] = "partial"
        result.setdefault("uncertainty", []).append({
            "item": "unfilled output_contract fields",
            "reason": ", ".join(missing),
        })
    result["_output_contract_source"] = contract
    write_json(os.path.join(args.output, "result.json"), result)
    print("finalize: re-filled output_contract (%s)" % ("ok" if filled_ok else "partial"))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv):
    p = argparse.ArgumentParser(prog="scripts/main.py",
                                description="Multi-step scientific data analysis Skill (stdlib only)")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.add_argument("command", nargs="?", default="all",
                   choices=["scaffold", "profile", "all", "finalize"])
    args = p.parse_args(argv)
    try:
        if args.command == "scaffold":
            return step_scaffold(args)
        if args.command == "profile":
            return step_profile(args)
        if args.command == "finalize":
            return step_finalize(args)
        return step_all(args)
    except Exception as e:  # noqa: BLE001 - never leave an empty output dir
        os.makedirs(args.output, exist_ok=True)
        try:
            case = read_case(args.input)
        except Exception:
            case = {"case_id": "unknown", "output_contract": None}
        result = OrderedDict()
        result["case_id"] = case.get("case_id", "unknown")
        result["status"] = "input_error"
        result["errors"] = ["unhandled exception: %s" % e]
        result["degradations"] = []
        result["sources"] = []
        result["uncertainty"] = [{"item": "pipeline crash", "reason": str(e)}]
        result["pending_human"] = ["inspect error trace and re-run"]
        result["change_log"] = [{"step": "crash", "action": "fallback minimal result", "error": str(e)}]
        result["runtime_seconds"] = 0.0
        write_json(os.path.join(args.output, "result.json"), result)
        print("FATAL (graceful): %s" % e, file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
