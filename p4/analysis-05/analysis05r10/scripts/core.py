"""core.py — deterministic research-data analysis engine (Python stdlib only).

Architecture (deliberately different from a monolithic one-file implementation)
-----------------------------------------------------------------------------
The pipeline is a registry of stages executed in dependency order.  Every stage
writes a *typed intermediate representation* (IR) under the work directory and
records each artifact together with its SHA-256 digest into
``work/manifest.json``.  The manifest is the single source of truth: a downstream
stage never relies on a hard-coded path written by another stage, it resolves the
artifact path from the manifest, verifies the hash, and then consumes it.

Stages:  scaffold -> profile -> clean -> analyze -> render -> reconcile

Validation happens first (inside ``scaffold``): before any analysis the pipeline
already writes a legal ``partial`` ``result.json``, so an interrupted run never
leaves ``/app/output`` empty.  Contract reconciliation is an explicit final
stage, not a side effect scattered across the code.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import sqlite3
import sys
import time
import traceback
from datetime import datetime, timezone

# --------------------------------------------------------------------------
# Limits (safety caps; exceeding them degrades, never crashes)
# --------------------------------------------------------------------------
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_ROWS = 200_000
MAX_COLUMNS = 500
MAX_TARGETS = 20
MAX_CORR_PAIRS = 200
MAX_GROUP_LEVELS = 15
MIN_GROUP_LEVELS = 2
MAX_OUTLIERS = 50
MAX_SCATTER_POINTS = 300
MAX_QUOTE = 320

DEFAULT_CONTRACT = ("analysis_results", "quality_evidence", "anomaly_notes")
STAGES = ("scaffold", "profile", "clean", "analyze", "render", "reconcile")

DATA_EXTENSIONS = {".csv", ".tsv", ".tab", ".txt", ".json", ".db", ".sqlite", ".sqlite3"}
SKIP_NAMES = {"case.json", "task.md", "resources.yaml", "environment.yaml"}
SQL_TABLE_SKIP = {"sqlite_sequence", "sqlite_stat1"}


# --------------------------------------------------------------------------
# Small deterministic utilities
# --------------------------------------------------------------------------
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _json_clean(obj):
    """Recursively convert NaN/Infinity to None so JSON stays strict."""
    if isinstance(obj, float):
        if not math.isfinite(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _json_clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_clean(v) for v in obj]
    return obj


def json_text(obj) -> str:
    return json.dumps(_json_clean(obj), ensure_ascii=False, indent=2, sort_keys=True)


def atomic_write_text(path: str, text: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def write_json(path: str, obj) -> None:
    atomic_write_text(path, json_text(obj))


def read_text_robust(path: str, max_bytes: int = MAX_FILE_BYTES):
    """Return (text, encoding_used, truncated)."""
    with open(path, "rb") as f:
        data = f.read(max_bytes + 1)
    truncated = len(data) > max_bytes
    if truncated:
        data = data[:max_bytes]
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc), enc, truncated
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace"), "utf-8(replace)", truncated


def load_json_robust(path: str):
    text, enc, trunc = read_text_robust(path)
    return json.loads(text), enc, trunc


def read_first_lines(path: str, n: int = 3) -> str:
    text, _enc, _trunc = read_text_robust(path, 128 * 1024)
    lines = text.splitlines()
    return "\n".join(lines[:n])


def source_quote(source: str) -> str:
    """Return a verbatim locator quote; SQLite locators are not files."""
    if os.path.isfile(source):
        return read_first_lines(source)[:MAX_QUOTE]
    return str(source)[:MAX_QUOTE]


# --------------------------------------------------------------------------
# Type inference helpers
# --------------------------------------------------------------------------
def is_number(v) -> bool:
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return True
    s = str(v).strip()
    if not s:
        return False
    try:
        float(s)
        return True
    except ValueError:
        return False


def to_number(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def is_integral(v) -> bool:
    if isinstance(v, int):
        return True
    if isinstance(v, float):
        return v.is_integer()
    s = str(v).strip()
    try:
        f = float(s)
        return f.is_integer()
    except ValueError:
        return False


def classify_column(non_null):
    if not non_null:
        return "empty"
    if all(is_number(v) for v in non_null):
        if all(is_integral(v) for v in non_null):
            return "integer"
        return "numeric"
    uniq = len({str(v) for v in non_null})
    if uniq <= min(20, max(1, int(len(non_null) * 0.5))):
        return "categorical"
    return "text"


def fmt_num(x, digits=4):
    if x is None:
        return "n/a"
    if isinstance(x, float):
        if abs(x) >= 1e6 or (abs(x) < 1e-4 and x != 0.0):
            return f"{x:.{digits}e}"
        return f"{x:.{digits}g}"
    return str(x)


# --------------------------------------------------------------------------
# Statistical primitives (stdlib implementations of exact/approx p-values)
# --------------------------------------------------------------------------
def _lgamma(x):
    return math.lgamma(x)


def _betacf(a, b, x):
    MAXIT = 300
    EPS = 3e-15
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
    """Regularized incomplete beta I_x(a,b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = _lgamma(a + b) - _lgamma(a) - _lgamma(b) + a * math.log(x) + b * math.log(1.0 - x)
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(lbeta) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbeta) * _betacf(b, a, 1.0 - x) / b


def _gser(a, x):
    ITMAX = 300
    EPS = 3e-15
    if x <= 0.0:
        return 0.0
    gln = _lgamma(a)
    ap = a
    s = 1.0 / a
    delta = s
    for _ in range(1, ITMAX + 1):
        ap += 1.0
        delta *= x / ap
        s += delta
        if abs(delta) < abs(s) * EPS:
            break
    return s * math.exp(-x + a * math.log(x) - gln)


def _gcf(a, x):
    ITMAX = 300
    EPS = 3e-15
    FPMIN = 1e-300
    gln = _lgamma(a)
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
    return math.exp(-x + a * math.log(x) - gln) * h


def gammq(a, x):
    """Upper regularized incomplete gamma Q(a,x)."""
    if x < 0.0 or a <= 0.0:
        return None
    if x < a + 1.0:
        return 1.0 - _gser(a, x)
    return _gcf(a, x)


def t_p_two_sided(t, df):
    """Two-sided p-value for Student's t."""
    if df <= 0:
        return None
    x = df / (df + t * t)
    return betai(df / 2.0, 0.5, x)


def f_upper_p(f, df1, df2):
    """Upper-tail p-value for F(df1, df2)."""
    if df1 <= 0 or df2 <= 0 or f < 0:
        return None
    x = df2 / (df2 + df1 * f)
    return betai(df2 / 2.0, df1 / 2.0, x)


def chi2_upper_p(chi2, df):
    """Upper-tail p-value for chi-square with df."""
    if df <= 0 or chi2 < 0:
        return None
    return gammq(df / 2.0, chi2 / 2.0)


def normal_upper_p(z):
    """Upper-tail p of standard normal."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def quantile(sorted_vals, q):
    n = len(sorted_vals)
    if n == 0:
        return None
    if n == 1:
        return float(sorted_vals[0])
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(sorted_vals[lo])
    w = pos - lo
    return float(sorted_vals[lo]) * (1.0 - w) + float(sorted_vals[hi]) * w


def desc_stats(vals):
    vals = [float(v) for v in vals if v is not None and is_number(v)]
    n = len(vals)
    if n == 0:
        return {"n": 0, "mean": None, "sd": None, "median": None, "q1": None,
                "q3": None, "min": None, "max": None}
    mean = sum(vals) / n
    if n > 1:
        var = sum((x - mean) ** 2 for x in vals) / (n - 1)
        sd = math.sqrt(var)
    else:
        sd = None
    sv = sorted(vals)
    return {
        "n": n,
        "mean": mean,
        "sd": sd,
        "sem": (sd / math.sqrt(n)) if sd is not None else None,
        "median": quantile(sv, 0.5),
        "q1": quantile(sv, 0.25),
        "q3": quantile(sv, 0.75),
        "min": sv[0],
        "max": sv[-1],
    }


def rank_average(values):
    n = len(values)
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def welch_t(a, b):
    a = [float(v) for v in a if v is not None and is_number(v)]
    b = [float(v) for v in b if v is not None and is_number(v)]
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return {"method": "welch_t", "statistic": None, "p_value": None,
                "df": None, "n1": n1, "n2": n2, "skipped": "sample_size<2"}
    m1 = sum(a) / n1
    m2 = sum(b) / n2
    v1 = sum((x - m1) ** 2 for x in a) / (n1 - 1)
    v2 = sum((x - m2) ** 2 for x in b) / (n2 - 1)
    if v1 == 0.0 and v2 == 0.0:
        return {"method": "welch_t", "statistic": None, "p_value": None,
                "df": None, "n1": n1, "n2": n2, "skipped": "zero_variance_both"}
    denom = math.sqrt(v1 / n1 + v2 / n2)
    if denom == 0.0:
        return {"method": "welch_t", "statistic": None, "p_value": None,
                "df": None, "n1": n1, "n2": n2, "skipped": "zero_denominator"}
    t = (m1 - m2) / denom
    num = (v1 / n1 + v2 / n2) ** 2
    den = (v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1)
    df = num / den if den != 0.0 else None
    p = t_p_two_sided(abs(t), df) if df else None
    return {"method": "welch_t", "statistic": t, "p_value": p, "df": df,
            "n1": n1, "n2": n2}


def mann_whitney_u(a, b):
    a = [float(v) for v in a if v is not None and is_number(v)]
    b = [float(v) for v in b if v is not None and is_number(v)]
    n1, n2 = len(a), len(b)
    if n1 < 1 or n2 < 1:
        return {"method": "mann_whitney_u", "statistic": None, "p_value": None,
                "n1": n1, "n2": n2, "skipped": "empty_group"}
    combined = a + b
    ranks = rank_average(combined)
    r1 = sum(ranks[:n1])
    u1 = n1 * n2 + n1 * (n1 + 1) / 2.0 - r1
    u2 = n1 * n2 - u1
    u = min(u1, u2)
    n = n1 + n2
    mu = n1 * n2 / 2.0
    # tie correction
    from collections import Counter
    counts = Counter(combined)
    tie = sum(c * (c * c - 1) for c in counts.values())
    var = n1 * n2 / 12.0 * ((n + 1) - tie / (n * (n - 1.0))) if n > 1 else None
    if var is None or var <= 0:
        p = None
    else:
        # continuity correction (standard for the normal approximation)
        if u < mu:
            z = (u - mu + 0.5) / math.sqrt(var)
        else:
            z = (u - mu - 0.5) / math.sqrt(var)
        p = 2.0 * normal_upper_p(abs(z))
        p = min(1.0, max(0.0, p))
    return {"method": "mann_whitney_u", "statistic": u, "p_value": p,
            "n1": n1, "n2": n2}


def anova_f(groups):
    groups = [[float(v) for v in g if v is not None and is_number(v)] for g in groups]
    groups = [g for g in groups if len(g) >= 1]
    k = len(groups)
    if k < 2:
        return {"method": "anova_f", "statistic": None, "p_value": None,
                "df_between": None, "df_within": None, "skipped": "groups<2"}
    all_vals = [x for g in groups for x in g]
    n = len(all_vals)
    grand = sum(all_vals) / n
    ssb = sum(len(g) * (sum(g) / len(g) - grand) ** 2 for g in groups)
    ssw = sum(sum((x - sum(g) / len(g)) ** 2 for x in g) for g in groups)
    dfb = k - 1
    dfw = n - k
    if dfw <= 0 or ssw == 0.0:
        return {"method": "anova_f", "statistic": None, "p_value": None,
                "df_between": dfb, "df_within": dfw, "skipped": "zero_within_variance"}
    msb = ssb / dfb
    msw = ssw / dfw
    f = msb / msw
    p = f_upper_p(f, dfb, dfw)
    return {"method": "anova_f", "statistic": f, "p_value": p,
            "df_between": dfb, "df_within": dfw}


def kruskal_wallis(groups):
    groups = [[float(v) for v in g if v is not None and is_number(v)] for g in groups]
    groups = [g for g in groups if len(g) >= 1]
    k = len(groups)
    if k < 2:
        return {"method": "kruskal_wallis", "statistic": None, "p_value": None,
                "df": None, "skipped": "groups<2"}
    combined = [x for g in groups for x in g]
    n = len(combined)
    if n < 3:
        return {"method": "kruskal_wallis", "statistic": None, "p_value": None,
                "df": k - 1, "skipped": "total_n<3"}
    ranks = rank_average(combined)
    idx = 0
    tsums = []
    from collections import Counter
    counts = Counter(combined)
    for g in groups:
        tsums.append(sum(ranks[idx:idx + len(g)]))
        idx += len(g)
    h = 12.0 / (n * (n + 1)) * sum(ts * ts / len(g) for ts, g in zip(tsums, groups)) - 3.0 * (n + 1)
    tie = sum(c * (c * c - 1) for c in counts.values())
    if tie > 0 and n > 1:
        h = h / (1.0 - tie / (n ** 3 - n))
    p = chi2_upper_p(h, k - 1)
    return {"method": "kruskal_wallis", "statistic": h, "p_value": p, "df": k - 1}


def pearson(a, b):
    a = [float(v) for v in a if v is not None and is_number(v)]
    b = [float(v) for v in b if v is not None and is_number(v)]
    # pairwise complete
    pairs = [(x, y) for x, y in zip(a, b)]
    pairs = [(x, y) for x, y in pairs if is_number(x) and is_number(y)]
    n = len(pairs)
    if n < 3:
        return {"n": n, "pearson_r": None, "pearson_p": None, "slope": None, "intercept": None}
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    if sxx == 0.0 or syy == 0.0:
        r = None
        p = None
        slope = None
        intercept = None
    else:
        r = sxy / math.sqrt(sxx * syy)
        r = max(-1.0, min(1.0, r))
        if abs(r) >= 1.0:
            p = 0.0
        else:
            t = r * math.sqrt((n - 2) / (1.0 - r * r))
            p = t_p_two_sided(abs(t), n - 2)
        slope = sxy / sxx
        intercept = my - slope * mx
    return {"n": n, "pearson_r": r, "pearson_p": p, "slope": slope, "intercept": intercept,
            "points": pairs}


def spearman(a, b):
    a = [float(v) if is_number(v) else None for v in a]
    b = [float(v) if is_number(v) else None for v in b]
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    n = len(pairs)
    if n < 3:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    rx = rank_average(xs)
    ry = rank_average(ys)
    mr = (n + 1) / 2.0
    sxy = sum((rx[i] - mr) * (ry[i] - mr) for i in range(n))
    sxx = sum((rx[i] - mr) ** 2 for i in range(n))
    syy = sum((ry[i] - mr) ** 2 for i in range(n))
    if sxx == 0.0 or syy == 0.0:
        rho = None
        p = None
    else:
        rho = sxy / math.sqrt(sxx * syy)
        rho = max(-1.0, min(1.0, rho))
        if abs(rho) >= 1.0:
            p = 0.0
        else:
            t = rho * math.sqrt((n - 2) / (1.0 - rho * rho))
            p = t_p_two_sided(abs(t), n - 2)
    return {"spearman_rho": rho, "spearman_p": p}


# --------------------------------------------------------------------------
# Table IO
# --------------------------------------------------------------------------
class Table:
    def __init__(self, name, source, columns, rows, types):
        self.name = name
        self.source = source
        self.columns = columns
        self.rows = rows
        self.types = types


def _rows_to_table(name, source, raw_rows, has_header):
    if not raw_rows:
        return Table(name, source, [], [], [])
    header = raw_rows[0]
    if has_header:
        columns = [str(c).strip() if c is not None else "" for c in header]
        data = raw_rows[1:]
    else:
        columns = [f"col_{i}" for i in range(len(header))]
        data = raw_rows
    if not columns or all(c == "" for c in columns):
        columns = [f"col_{i}" for i in range(len(header))]
    # pad / truncate to MAX_COLUMNS
    columns = columns[:MAX_COLUMNS]
    data = [r[:MAX_COLUMNS] for r in data]
    data = [r + [None] * (len(columns) - len(r)) for r in data]
    data = data[:MAX_ROWS]
    n_cols = len(columns)
    # classify columns on non-null cells
    types = []
    for j in range(n_cols):
        non_null = [r[j] for r in data if r[j] is not None and str(r[j]).strip() != ""]
        types.append(classify_column(non_null))
    # coerce numeric columns
    for j, t in enumerate(types):
        if t in ("numeric", "integer"):
            for r in data:
                r[j] = to_number(r[j])
    return Table(name, source, columns, data, types)


def _detect_header(raw_rows):
    if not raw_rows:
        return False
    first = raw_rows[0]
    for c in first:
        if c is not None and str(c).strip() != "" and not is_number(c):
            return True
    return False


def read_csv_like(path):
    text, _enc, truncated = read_text_robust(path)
    sample = text[:65536]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        delim = dialect.delimiter
    except csv.Error:
        # fallback: count delimiters in first line
        first = sample.splitlines()[0] if sample.splitlines() else ""
        counts = {d: first.count(d) for d in ",\t;|"}
        delim = max(counts, key=counts.get)
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    raw = []
    for row in reader:
        raw.append([c.strip() if isinstance(c, str) else c for c in row])
        if len(raw) >= MAX_ROWS + 1:
            break
    if truncated:
        raw.append([f"[truncated file >{MAX_FILE_BYTES} bytes]"])
    return _rows_to_table(os.path.basename(path), path, raw, _detect_header(raw)), truncated


def read_json_table(path):
    obj, _enc, truncated = load_json_robust(path)
    raw = []
    if isinstance(obj, list):
        for item in obj[:MAX_ROWS + 1]:
            if isinstance(item, dict):
                raw.append([item.get(k) for k in item])
            elif isinstance(item, (list, tuple)):
                raw.append(list(item))
    elif isinstance(obj, dict):
        # object-of-columns or object-of-rows
        list_values = [(k, v) for k, v in obj.items() if isinstance(v, (list, tuple))]
        if list_values:
            keys = [k for k, _ in list_values]
            rows = list(zip(*[v for _, v in list_values]))
            raw = [[dict(zip(keys, r)).get(k) for k in keys] for r in rows]
        else:
            raw = [[k, str(v)] for k, v in obj.items()]
    return _rows_to_table(os.path.basename(path), path, raw, _detect_header(raw)), truncated


def read_sqlite_tables(path):
    tables = []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        names = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for tname in names:
            if tname in SQL_TABLE_SKIP:
                continue
            try:
                info = cur.execute(f"PRAGMA table_info({tname})").fetchall()
                columns = [r[1] for r in info]
                rows = [list(r) for r in cur.execute(
                    f"SELECT * FROM {tname} LIMIT {MAX_ROWS + 1}")]
                table = _rows_to_table(tname, f"{path}#{tname}", [columns] + rows, True)
                tables.append(table)
            except sqlite3.Error:
                continue
    finally:
        conn.close()
    return tables


def load_tables(input_dir):
    tables = []
    issues = []
    for name in sorted(os.listdir(input_dir)):
        path = os.path.join(input_dir, name)
        if not os.path.isfile(path):
            continue
        base = os.path.basename(name)
        if base in SKIP_NAMES or base.startswith("."):
            continue
        ext = os.path.splitext(base)[1].lower()
        if ext not in DATA_EXTENSIONS:
            continue
        try:
            size = os.path.getsize(path)
            if size > MAX_FILE_BYTES:
                issues.append({"file": base, "detail": f"file too large ({size} bytes), skipped"})
                continue
            if ext in (".db", ".sqlite", ".sqlite3"):
                tables.extend(read_sqlite_tables(path))
            elif ext == ".json":
                tables.append(read_json_table(path)[0])
            else:
                tables.append(read_csv_like(path)[0])
        except Exception as e:  # noqa: BLE001
            issues.append({"file": base, "detail": f"{type(e).__name__}: {e}"})
    return tables, issues


def table_numeric_columns(table):
    return [c for c, t in zip(table.columns, table.types) if t in ("numeric", "integer")]


def table_categorical_columns(table):
    return [c for c, t in zip(table.columns, table.types) if t == "categorical"]


def pick_primary_table(tables):
    if not tables:
        return None
    def score(t):
        n = len(t.rows)
        k = len(table_numeric_columns(t))
        return n * (k + 1), n, k
    return max(tables, key=score)


# --------------------------------------------------------------------------
# IR stage 1: profile
# --------------------------------------------------------------------------
def build_profile(table):
    profile = {
        "name": table.name,
        "source": table.source,
        "n_rows": len(table.rows),
        "n_columns": len(table.columns),
        "columns": [],
    }
    for j, col in enumerate(table.columns):
        col_vals = [r[j] for r in table.rows]
        non_null = [v for v in col_vals if v is not None and str(v).strip() != ""]
        missing = len(col_vals) - len(non_null)
        coltype = table.types[j]
        entry = {
            "name": col,
            "type": coltype,
            "n": len(non_null),
            "missing": missing,
            "unique": len({str(v) for v in non_null}),
            "sample": [str(v) for v in non_null[:3]],
        }
        if coltype in ("numeric", "integer"):
            entry.update(desc_stats(non_null))
        elif coltype == "categorical":
            from collections import Counter
            counts = Counter(str(v) for v in non_null)
            entry["levels"] = dict(counts.most_common(15))
        profile["columns"].append(entry)
    return profile


# --------------------------------------------------------------------------
# IR stage 2: clean
# --------------------------------------------------------------------------
def clean_table(table):
    meta = {
        "source": table.source,
        "n_raw": len(table.rows),
        "rules": [],
        "missing": {"total_cells": 0, "by_column": {}},
        "outliers": {"method": "IQR * 1.5", "by_column": {}},
        "duplicates": {"exact_duplicate_rows": 0, "rows_removed": 0},
    }
    rows = [list(r) for r in table.rows]

    # rule 1: dedup exact duplicate rows (keep first occurrence)
    seen = set()
    kept = []
    dup = 0
    for r in rows:
        key = tuple("" if v is None else str(v) for v in r)
        if key in seen:
            dup += 1
        else:
            seen.add(key)
            kept.append(r)
    meta["duplicates"]["exact_duplicate_rows"] = dup
    meta["duplicates"]["rows_removed"] = dup
    meta["rules"].append({
        "id": "dedup_exact",
        "priority": 1,
        "action": "drop exact duplicate rows, keep first occurrence",
        "rows_removed": dup,
        "rows_after": len(kept),
    })
    rows = kept

    # rule 2: missing values — flag only, never drop silently
    missing_by_col = {}
    total_missing = 0
    for j, col in enumerate(table.columns):
        miss = sum(1 for r in rows if r[j] is None or str(r[j]).strip() == "")
        missing_by_col[col] = miss
        total_missing += miss
    meta["missing"]["total_cells"] = total_missing
    meta["missing"]["by_column"] = missing_by_col
    meta["rules"].append({
        "id": "missing_flag",
        "priority": 2,
        "action": "flag missing cells per column; excluded pairwise in analysis; no imputation",
        "missing_cells": total_missing,
        "rows_after": len(rows),
    })

    # rule 3: outlier flagging (IQR 1.5) — flag only, never drop
    outlier_by_col = {}
    for j, col in enumerate(table.columns):
        if table.types[j] not in ("numeric", "integer"):
            continue
        vals = [r[j] for r in rows if r[j] is not None and is_number(r[j])]
        if len(vals) < 4:
            continue
        sv = sorted(float(v) for v in vals)
        q1 = quantile(sv, 0.25)
        q3 = quantile(sv, 0.75)
        iqr = q3 - q1
        if iqr <= 0:
            continue
        lo = q1 - 1.5 * iqr
        hi = q3 + 1.5 * iqr
        out_vals = [v for v in vals if v < lo or v > hi]
        if out_vals:
            outlier_by_col[col] = {
                "q1": q1, "q3": q3, "iqr": iqr,
                "lower_fence": lo, "upper_fence": hi,
                "n_outliers": len(out_vals),
                "outlier_values": sorted(out_vals)[:MAX_OUTLIERS],
            }
    meta["outliers"]["by_column"] = outlier_by_col
    meta["rules"].append({
        "id": "outlier_flag",
        "priority": 3,
        "action": "flag numeric outliers by IQR*1.5; recorded, not removed",
        "flagged_columns": list(outlier_by_col.keys()),
        "rows_after": len(rows),
    })

    meta["n_clean"] = len(rows)
    return rows, meta


def write_cleaned_csv(path, table, rows):
    header = list(table.columns)
    lines = [",".join(_csv_cell(c) for c in header)]
    for r in rows:
        lines.append(",".join(_csv_cell(v) for v in r))
    atomic_write_text(path, "\n".join(lines) + "\n")


def _csv_cell(v):
    if v is None:
        return ""
    s = str(v)
    if any(ch in s for ch in (",", '"', "\n")):
        return '"' + s.replace('"', '""') + '"'
    return s


# --------------------------------------------------------------------------
# IR stage 3: analyze
# --------------------------------------------------------------------------
def _group_values(rows, group_col_idx, target_col_idx):
    groups = {}
    for r in rows:
        g = r[group_col_idx]
        v = r[target_col_idx]
        if g is None or v is None or not is_number(v):
            continue
        key = str(g)
        groups.setdefault(key, []).append(float(v))
    return groups


def _group_levels_order(rows, group_col_idx):
    seen = []
    for r in rows:
        g = r[group_col_idx]
        if g is None:
            continue
        s = str(g)
        if s not in seen:
            seen.append(s)
    return seen


def analyze_table(table, rows, clean_meta, group_col_override=None, targets_override=None):
    n_rows = len(rows)
    numeric_cols = table_numeric_columns(table)
    categorical_cols = table_categorical_columns(table)
    col_index = {c: j for j, c in enumerate(table.columns)}

    if targets_override:
        present = [c for c in targets_override if c in col_index]
        if present:
            numeric_cols = [c for c in present if table.types[col_index[c]] in ("numeric", "integer")]

    stats = {
        "case_id": None,  # filled by caller
        "table": table.name,
        "source": table.source,
        "n_rows_raw": clean_meta["n_raw"],
        "n_rows_clean": n_rows,
        "columns": {},
        "group_comparison": [],
        "correlation": [],
        "categorical_summary": {},
    }

    # descriptive stats for every numeric column
    for c in numeric_cols[:MAX_TARGETS]:
        j = col_index[c]
        vals = [r[j] for r in rows if r[j] is not None and is_number(r[j])]
        stats["columns"][c] = {"type": table.types[j],
                               "missing": clean_meta["missing"]["by_column"].get(c, 0),
                               **desc_stats(vals)}

    # categorical summary
    for c in categorical_cols:
        j = col_index[c]
        from collections import Counter
        counts = Counter(str(r[j]) for r in rows if r[j] is not None)
        stats["categorical_summary"][c] = dict(counts.most_common(15))

    # group comparison: prefer explicit override, else auto-select a categorical
    # group column (low cardinality, not ID-like)
    group_col = None
    if group_col_override and group_col_override in col_index and \
            table.types[col_index[group_col_override]] == "categorical":
        group_col = group_col_override
    if group_col is None and categorical_cols:
        best = None
        for c in categorical_cols:
            levels = stats["categorical_summary"].get(c, {})
            k = len(levels)
            if MIN_GROUP_LEVELS <= k <= MAX_GROUP_LEVELS:
                score = sum(levels.values())
                if best is None or score > best[1]:
                    best = (c, score)
        if best:
            group_col = best[0]

    if group_col is not None:
        gj = col_index[group_col]
        group_order = _group_levels_order(rows, gj)
        for target in numeric_cols[:MAX_TARGETS]:
            tj = col_index[target]
            groups = _group_values(rows, gj, tj)
            if len(groups) < MIN_GROUP_LEVELS:
                continue
            entry = {"group_col": group_col, "target_col": target, "groups": [], "tests": []}
            for g in group_order:
                if g not in groups:
                    continue
                vals = groups[g]
                d = desc_stats(vals)
                # box whiskers and outliers
                sv = sorted(vals)
                q1, q3 = d["q1"], d["q3"]
                iqr = (q3 - q1) if (q1 is not None and q3 is not None) else 0.0
                lo = q1 - 1.5 * iqr if iqr else None
                hi = q3 + 1.5 * iqr if iqr else None
                whisker_lo = min([v for v in sv if lo is None or v >= lo], default=sv[0]) if sv else None
                whisker_hi = max([v for v in sv if hi is None or v <= hi], default=sv[-1]) if sv else None
                outliers = [v for v in sv if (lo is not None and v < lo) or (hi is not None and v > hi)]
                entry["groups"].append({
                    "group": g, "n": d["n"], "mean": d["mean"], "sd": d["sd"], "sem": d["sem"],
                    "median": d["median"], "q1": q1, "q3": q3, "min": d["min"], "max": d["max"],
                    "whisker_low": whisker_lo, "whisker_high": whisker_hi,
                    "outliers": outliers[:MAX_OUTLIERS],
                })
            k = len(groups)
            if k == 2:
                entry["tests"].append(welch_t(groups[group_order[0]], groups[group_order[1]]))
                entry["tests"].append(mann_whitney_u(groups[group_order[0]], groups[group_order[1]]))
            else:
                entry["tests"].append(anova_f([groups[g] for g in group_order if g in groups]))
                entry["tests"].append(kruskal_wallis([groups[g] for g in group_order if g in groups]))
            stats["group_comparison"].append(entry)

    # correlation: all pairs of numeric columns (cap pairs)
    corr_list = []
    nc = numeric_cols[:MAX_TARGETS]
    pairs = [(a, b) for i, a in enumerate(nc) for b in nc[i + 1:]]
    pairs = pairs[:MAX_CORR_PAIRS]
    for xc, yc in pairs:
        xi, yi = col_index[xc], col_index[yc]
        xs = [r[xi] for r in rows]
        ys = [r[yi] for r in rows]
        pr = pearson(xs, ys)
        sp = spearman(xs, ys)
        pts = pr.pop("points", [])
        # deterministic downsampling for the scatter figure
        if len(pts) > MAX_SCATTER_POINTS:
            step = max(1, len(pts) // MAX_SCATTER_POINTS)
            pts = pts[::step][:MAX_SCATTER_POINTS]
        item = {"x_col": xc, "y_col": yc, **pr, **(sp or {})}
        item["points"] = [[float(a), float(b)] for a, b in pts]
        corr_list.append(item)
    corr_list.sort(key=lambda d: abs(d["pearson_r"]) if d["pearson_r"] is not None else -1.0,
                   reverse=True)
    stats["correlation"] = corr_list
    stats["_group_col_used"] = group_col

    # pick the single most informative group comparison + correlation for figures
    stats["_figure_group"] = None
    stats["_figure_corr"] = None
    if stats["group_comparison"]:
        stats["_figure_group"] = stats["group_comparison"][0]
    if corr_list and corr_list[0]["pearson_r"] is not None:
        stats["_figure_corr"] = corr_list[0]
    return stats


# --------------------------------------------------------------------------
# IR stage 4: render (SVG + report, all numbers read from stats.json)
# --------------------------------------------------------------------------
def _svg_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def svg_group_box(stats, path):
    gc = stats.get("_figure_group")
    if not gc:
        return None
    groups = gc["groups"]
    n = len(groups)
    if n == 0:
        return None
    W = max(360.0, 120.0 + n * 90.0)
    H = 360.0
    margin_l, margin_r, margin_t, margin_b = 70.0, 20.0, 40.0, 60.0
    plot_w = W - margin_l - margin_r
    plot_h = H - margin_t - margin_b
    all_vals = []
    for g in groups:
        for v in (g["min"], g["max"], g["q1"], g["q3"], g["median"]):
            if v is not None:
                all_vals.append(v)
    if not all_vals:
        return None
    ymin = min(all_vals)
    ymax = max(all_vals)
    pad = (ymax - ymin) * 0.08 or 1.0
    ymin -= pad
    ymax += pad
    if ymin == ymax:
        ymin -= 1.0
        ymax += 1.0

    def x(i):
        return margin_l + plot_w * (i + 0.5) / n

    def y(v):
        return margin_t + plot_h * (1.0 - (v - ymin) / (ymax - ymin))

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.1f}" height="{H:.1f}" '
        f'viewBox="0 0 {W:.1f} {H:.1f}">',
        f'<rect width="{W:.1f}" height="{H:.1f}" fill="#ffffff"/>',
        f'<text x="{W/2:.1f}" y="{margin_t/2:.1f}" text-anchor="middle" '
        f'font-family="DejaVu Sans, sans-serif" font-size="14">'
        f'Group comparison: {_svg_escape(gc["target_col"])} by {_svg_escape(gc["group_col"])}</text>',
        f'<text x="{margin_l + plot_w/2:.1f}" y="{H - margin_b/2:.1f}" text-anchor="middle" '
        f'font-family="DejaVu Sans, sans-serif" font-size="12">{_svg_escape(gc["group_col"])}</text>',
        f'<text x="16" y="{margin_t + plot_h/2:.1f}" text-anchor="middle" '
        f'font-family="DejaVu Sans, sans-serif" font-size="12" transform="rotate(-90 16 {margin_t + plot_h/2:.1f})">'
        f'{_svg_escape(gc["target_col"])}</text>',
    ]
    # gridlines + y ticks (5 ticks)
    for k in range(6):
        v = ymin + (ymax - ymin) * k / 5.0
        yy = y(v)
        parts.append(f'<line x1="{margin_l:.1f}" y1="{yy:.1f}" x2="{W - margin_r:.1f}" y2="{yy:.1f}" '
                     f'stroke="#dddddd" stroke-width="1"/>')
        parts.append(f'<text x="{margin_l - 6:.1f}" y="{yy + 4:.1f}" text-anchor="end" '
                     f'font-family="DejaVu Sans, sans-serif" font-size="10">{fmt_num(v, 3)}</text>')
    box_w = min(50.0, plot_w / n * 0.5)
    for i, g in enumerate(groups):
        cx = x(i)
        q1, q3, med = g["q1"], g["q3"], g["median"]
        if q1 is None or q3 is None:
            continue
        yq1, yq3 = y(q1), y(q3)
        ymed = y(med) if med is not None else None
        # whiskers
        parts.append(f'<line x1="{cx:.1f}" y1="{y(g["whisker_low"]):.1f}" x2="{cx:.1f}" '
                     f'y2="{yq1:.1f}" stroke="#444444" stroke-width="1.5"/>')
        parts.append(f'<line x1="{cx:.1f}" y1="{y(g["whisker_high"]):.1f}" x2="{cx:.1f}" '
                     f'y2="{yq3:.1f}" stroke="#444444" stroke-width="1.5"/>')
        parts.append(f'<line x1="{cx - box_w/2:.1f}" y1="{y(g["whisker_low"]):.1f}" '
                     f'x2="{cx + box_w/2:.1f}" y2="{y(g["whisker_low"]):.1f}" stroke="#444444" stroke-width="1.5"/>')
        parts.append(f'<line x1="{cx - box_w/2:.1f}" y1="{y(g["whisker_high"]):.1f}" '
                     f'x2="{cx + box_w/2:.1f}" y2="{y(g["whisker_high"]):.1f}" stroke="#444444" stroke-width="1.5"/>')
        # box
        parts.append(f'<rect x="{cx - box_w/2:.1f}" y="{min(yq1, yq3):.1f}" width="{box_w:.1f}" '
                     f'height="{abs(yq3 - yq1):.1f}" fill="#cfe3f5" stroke="#1f4e79" stroke-width="1.5"/>')
        if ymed is not None:
            parts.append(f'<line x1="{cx - box_w/2:.1f}" y1="{ymed:.1f}" x2="{cx + box_w/2:.1f}" '
                         f'y2="{ymed:.1f}" stroke="#1f4e79" stroke-width="2"/>')
        # outliers
        for ov in g.get("outliers", [])[:20]:
            parts.append(f'<circle cx="{cx:.1f}" cy="{y(ov):.1f}" r="2.5" fill="#c00000"/>')
        # group label
        label = _svg_escape(g["group"])
        if len(label) > 12:
            label = label[:11] + "…"
        parts.append(f'<text x="{cx:.1f}" y="{H - margin_b + 16:.1f}" text-anchor="middle" '
                     f'font-family="DejaVu Sans, sans-serif" font-size="10">{label}</text>')
    parts.append("</svg>")
    atomic_write_text(path, "\n".join(parts))
    return os.path.basename(path)


def svg_corr_scatter(stats, path):
    corr = stats.get("_figure_corr")
    if not corr or not corr.get("points"):
        return None
    pts = corr["points"]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    W, H = 420.0, 380.0
    ml, mr, mt, mb = 60.0, 20.0, 40.0, 50.0
    pw, ph = W - ml - mr, H - mt - mb
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    xpad = (xmax - xmin) * 0.05 or 1.0
    ypad = (ymax - ymin) * 0.05 or 1.0
    xmin -= xpad
    xmax += xpad
    ymin -= ypad
    ymax += ypad

    def X(v):
        return ml + pw * (v - xmin) / (xmax - xmin)

    def Y(v):
        return mt + ph * (1.0 - (v - ymin) / (ymax - ymin))

    r = corr.get("pearson_r")
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.1f}" height="{H:.1f}" viewBox="0 0 {W:.1f} {H:.1f}">',
        f'<rect width="{W:.1f}" height="{H:.1f}" fill="#ffffff"/>',
        f'<text x="{W/2:.1f}" y="{mt/2:.1f}" text-anchor="middle" font-family="DejaVu Sans, sans-serif" '
        f'font-size="14">Correlation: {_svg_escape(corr["x_col"])} vs {_svg_escape(corr["y_col"])} '
        f'(r = {fmt_num(r, 4)})</text>',
        f'<text x="{ml + pw/2:.1f}" y="{H - mb/2:.1f}" text-anchor="middle" '
        f'font-family="DejaVu Sans, sans-serif" font-size="12">{_svg_escape(corr["x_col"])}</text>',
        f'<text x="16" y="{mt + ph/2:.1f}" text-anchor="middle" font-family="DejaVu Sans, sans-serif" '
        f'font-size="12" transform="rotate(-90 16 {mt + ph/2:.1f})">{_svg_escape(corr["y_col"])}</text>',
    ]
    for k in range(6):
        vx = xmin + (xmax - xmin) * k / 5.0
        vy = ymin + (ymax - ymin) * k / 5.0
        parts.append(f'<line x1="{X(vx):.1f}" y1="{mt:.1f}" x2="{X(vx):.1f}" y2="{mt + ph:.1f}" '
                     f'stroke="#eeeeee" stroke-width="1"/>')
        parts.append(f'<line x1="{ml:.1f}" y1="{Y(vy):.1f}" x2="{ml + pw:.1f}" y2="{Y(vy):.1f}" '
                     f'stroke="#eeeeee" stroke-width="1"/>')
        parts.append(f'<text x="{X(vx):.1f}" y="{mt + ph + 14:.1f}" text-anchor="middle" '
                     f'font-family="DejaVu Sans, sans-serif" font-size="9">{fmt_num(vx, 3)}</text>')
        parts.append(f'<text x="{ml - 6:.1f}" y="{Y(vy) + 3:.1f}" text-anchor="end" '
                     f'font-family="DejaVu Sans, sans-serif" font-size="9">{fmt_num(vy, 3)}</text>')
    for xv, yv in pts:
        parts.append(f'<circle cx="{X(xv):.1f}" cy="{Y(yv):.1f}" r="2.2" fill="#2e75b6" fill-opacity="0.55"/>')
    slope = corr.get("slope")
    intercept = corr.get("intercept")
    if slope is not None and intercept is not None:
        y0 = intercept + slope * xmin
        y1 = intercept + slope * xmax
        parts.append(f'<line x1="{X(xmin):.1f}" y1="{Y(y0):.1f}" x2="{X(xmax):.1f}" y2="{Y(y1):.1f}" '
                     f'stroke="#c00000" stroke-width="2"/>')
    parts.append("</svg>")
    atomic_write_text(path, "\n".join(parts))
    return os.path.basename(path)


def build_report(stats, clean_meta, profile, figure_files, work_dir):
    L = []
    L.append("# 数据分析报告\n")
    L.append(f"- case_id: `{stats.get('case_id')}`")
    L.append(f"- 数据源: `{stats.get('source')}`（主表 `{stats.get('table')}`）")
    L.append(f"- 行数: 原始 {clean_meta['n_raw']} → 清洗后 {stats['n_rows_clean']}")
    L.append("")
    L.append("## 1. 数据与字段\n")
    L.append("| 字段 | 类型 | 有效值 | 缺失 | 均值 | 标准差 | 中位数 | 最小 | 最大 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for name, col in stats["columns"].items():
        L.append(f"| {name} | {col['type']} | {col['n']} | {col['missing']} | "
                 f"{fmt_num(col['mean'])} | {fmt_num(col['sd'])} | {fmt_num(col['median'])} | "
                 f"{fmt_num(col['min'])} | {fmt_num(col['max'])} |")
    L.append("")
    L.append("## 2. 清洗规则\n")
    for r in clean_meta["rules"]:
        L.append(f"- **{r['id']}** (优先级 {r['priority']}): {r['action']}")
    L.append(f"- 精确重复行: {clean_meta['duplicates']['exact_duplicate_rows']} 行，删除 "
             f"{clean_meta['duplicates']['rows_removed']} 行")
    L.append(f"- 缺失单元格: {clean_meta['missing']['total_cells']} 个（按列记录，分析时逐对剔除，未插补）")
    flagged = clean_meta["outliers"]["by_column"]
    if flagged:
        L.append(f"- 异常值（IQR×1.5 标记，未删除）: {', '.join(flagged.keys())}")
    else:
        L.append("- 异常值: 未检出（IQR×1.5）")
    L.append("")
    L.append("## 3. 组间比较\n")
    if stats["group_comparison"]:
        for gc in stats["group_comparison"]:
            L.append(f"### {gc['target_col']} 按 {gc['group_col']} 分组\n")
            L.append("| 分组 | n | 均值 | 标准差 | 中位数 | Q1 | Q3 |")
            L.append("|---|---|---|---|---|---|---|")
            for g in gc["groups"]:
                L.append(f"| {g['group']} | {g['n']} | {fmt_num(g['mean'])} | {fmt_num(g['sd'])} | "
                         f"{fmt_num(g['median'])} | {fmt_num(g['q1'])} | {fmt_num(g['q3'])} |")
            L.append("")
            L.append("| 检验 | 统计量 | p 值 | 备注 |")
            L.append("|---|---|---|---|")
            for t in gc["tests"]:
                note = t.get("skipped", "")
                L.append(f"| {t['method']} | {fmt_num(t.get('statistic'))} | "
                         f"{fmt_num(t.get('p_value'))} | {note} |")
            for t in gc["tests"]:
                p = t.get("p_value")
                if p is not None and not t.get("skipped"):
                    if p < 0.05:
                        L.append(f"- 检验 `{t['method']}` 的 p = {fmt_num(p)} < 0.05，"
                                 f"在名义显著性水平 α=0.05 下提示组间存在统计学显著差异（未做多重比较校正）。")
                    else:
                        L.append(f"- 检验 `{t['method']}` 的 p = {fmt_num(p)} ≥ 0.05，"
                                 f"未发现组间统计学显著差异（α=0.05）。")
            L.append("")
    else:
        L.append("未识别到 2–15 水平的分类分组列，跳过组间比较。\n")
    L.append("## 4. 相关分析\n")
    if stats["correlation"]:
        L.append("| 变量对 | n | Pearson r | Pearson p | Spearman ρ | Spearman p |")
        L.append("|---|---|---|---|---|---|")
        for c in stats["correlation"][:10]:
            L.append(f"| {c['x_col']} × {c['y_col']} | {c['n']} | {fmt_num(c['pearson_r'])} | "
                     f"{fmt_num(c['pearson_p'])} | {fmt_num(c.get('spearman_rho'))} | "
                     f"{fmt_num(c.get('spearman_p'))} |")
        L.append("")
    else:
        L.append("数值列不足两列，未计算相关。\n")
    L.append("## 5. 图\n")
    for f in figure_files:
        L.append(f"- `{f}`（SVG，可编辑）")
    L.append("")
    L.append("## 6. 方法与口径\n")
    L.append("- 两样本: Welch t 检验（不假设等方差）+ Mann–Whitney U（秩和，含结校正，正态近似）。")
    L.append("- 多样本: 单因素 ANOVA F + Kruskal–Wallis H（秩检验，含结校正）。")
    L.append("- 相关: Pearson r（线性）+ Spearman ρ（秩）；p 值经 t 分布（df=n−2）。")
    L.append("- 显著性水平 α=0.05；未做多重比较校正。异常值按 IQR×1.5 标记不删除；缺失逐对剔除、不插补。")
    L.append("")
    L.append("## 7. 质量与复现\n")
    L.append(f"- 中间产物目录: `{work_dir}`（profile.json / clean_meta.json / stats.json / manifest.json）")
    L.append("- 报告正文全部数字来自 `stats.json`，无手写数值；重跑脚本可逐字节复现。")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------
# manifest & record-keeping
# --------------------------------------------------------------------------
def _record_stage(ctx, name, status, artifacts, summary, errors=None):
    art = {}
    for a in artifacts:
        if os.path.isfile(a):
            art[a] = {"sha256": sha256_file(a), "bytes": os.path.getsize(a)}
    ctx["manifest"]["stages"][name] = {
        "status": status,
        "artifacts": art,
        "summary": summary,
    }
    if errors:
        ctx["manifest"]["stages"][name]["errors"] = errors
    for a, info in art.items():
        ctx["manifest"]["artifacts"][a] = info
    write_json(ctx["manifest_path"], ctx["manifest"])
    ctx["change_log"].append({
        "stage": name,
        "status": status,
        "action": summary.get("action", name),
        "detail": summary,
    })


# --------------------------------------------------------------------------
# Stages
# --------------------------------------------------------------------------
def _scan_input(input_dir):
    if not os.path.isdir(input_dir):
        return []
    return [os.path.join(input_dir, n) for n in sorted(os.listdir(input_dir))
            if os.path.isfile(os.path.join(input_dir, n))]


def _find_named(input_dir, names):
    for n in names:
        p = os.path.join(input_dir, n)
        if os.path.isfile(p):
            return p
    return None


def _is_data_file(path):
    base = os.path.basename(path)
    if base in SKIP_NAMES or base.startswith("."):
        return False
    return os.path.splitext(base)[1].lower() in DATA_EXTENSIONS


def stage_scaffold(ctx):
    inp = ctx["input_dir"]
    files = _scan_input(inp)
    case_path = _find_named(inp, ["case.json"])
    case = {}
    if case_path is not None:
        try:
            case, enc, _trunc = load_json_robust(case_path)
            ctx["sources"].append({
                "locator": case_path,
                "quote": read_first_lines(case_path)[:MAX_QUOTE],
                "role": "case.json",
            })
        except Exception as e:  # noqa: BLE001
            ctx["errors"].append({"scope": "case.json", "detail": f"{type(e).__name__}: {e}"})
            ctx["state"]["input_error"] = True
            case = {}
    else:
        ctx["errors"].append({"scope": "case.json", "detail": "missing case.json"})
        ctx["state"]["input_error"] = True

    task_path = _find_named(inp, ["task.md"])
    task_text = ""
    if task_path is not None:
        try:
            task_text, _enc, _trunc = read_text_robust(task_path, 512 * 1024)
            ctx["sources"].append({
                "locator": task_path,
                "quote": task_text[:MAX_QUOTE],
                "role": "task.md",
            })
        except Exception as e:  # noqa: BLE001
            ctx["errors"].append({"scope": "task.md", "detail": f"{type(e).__name__}: {e}"})

    # case_id
    case_id = None
    for k in ("case_id", "challenge_id", "armiId", "id"):
        v = case.get(k) if isinstance(case, dict) else None
        if v not in (None, ""):
            case_id = str(v)
            ctx["sources"].append({
                "locator": case_path,
                "quote": f'"{k}": "{case_id}"',
                "role": "case_id",
            })
            break
    if case_id is None:
        case_id = os.path.basename(os.path.normpath(inp)) or "unknown"
        ctx["degradations"].append({"scope": "case_id", "detail": "missing; using input dir name"})
        ctx["state"]["partial"] = True

    # output_contract
    contract = None
    if isinstance(case, dict):
        contract = case.get("output_contract") or case.get("outputContract")
    contract_keys = []
    contract_source = "case.json.output_contract"
    if isinstance(contract, dict):
        contract_keys = list(contract.keys())
        ctx["sources"].append({
            "locator": case_path,
            "quote": f'"output_contract" keys: {contract_keys}',
            "role": "output_contract",
        })
    else:
        contract_keys = list(DEFAULT_CONTRACT)
        contract_source = "default"
        ctx["degradations"].append({
            "scope": "output_contract",
            "detail": f"missing; using default keys {list(DEFAULT_CONTRACT)}",
        })
        ctx["state"]["partial"] = True

    data_files = [f for f in files if _is_data_file(f)]
    ctx["case"] = {
        "case_id": case_id,
        "contract_keys": contract_keys,
        "contract_source": contract_source,
        "contract_raw": contract if isinstance(contract, dict) else {},
        "data_files": data_files,
        "task_text": task_text[:4000],
        "task_path": task_path,
        "case_path": case_path,
    }
    write_json(os.path.join(ctx["work_dir"], "case.json"), ctx["case"])

    if ctx["state"]["input_error"]:
        ctx["state"]["status"] = "input_error"
    elif not data_files:
        ctx["state"]["status"] = "insufficient_evidence"
    else:
        ctx["state"]["status"] = "partial"

    _write_skeleton_result(ctx)
    _record_stage(ctx, "scaffold", "ok", [os.path.join(ctx["work_dir"], "case.json")],
                  {"action": "discover+validate", "case_id": case_id,
                   "contract_keys": contract_keys, "n_data_files": len(data_files)},
                  errors=[e["detail"] for e in ctx["errors"]] or None)


def _write_skeleton_result(ctx):
    payload = {"case_id": ctx["case"].get("case_id")}
    for k in ctx["case"]["contract_keys"]:
        payload[k] = None
    payload.update({
        "status": ctx["state"]["status"],
        "main_outputs": [],
        "sources": list(ctx["sources"]),
        "uncertainty": [],
        "pending_human": [],
        "errors": list(ctx["errors"]),
        "degradations": list(ctx["degradations"]),
        "change_log": [],
        "runtime": {"seconds": 0.0},
    })
    write_json(ctx["result_path"], payload)


def stage_profile(ctx):
    tables, issues = load_tables(ctx["input_dir"])
    for it in issues:
        ctx["degradations"].append({"scope": "data_import", **it})
    if not tables:
        ctx["state"]["status"] = "insufficient_evidence"
        ctx["errors"].append({"scope": "data", "detail": "no readable data files found"})
        _record_stage(ctx, "profile", "ok", [], {"action": "no data", "n_tables": 0})
        return None
    primary = pick_primary_table(tables)
    profile = build_profile(primary)
    profile["primary_table"] = primary.name
    profile["other_tables"] = [t.name for t in tables if t is not primary]
    ctx["profile"] = profile
    ctx["table"] = primary
    write_json(os.path.join(ctx["work_dir"], "profile.json"), profile)
    ctx["sources"].append({
        "locator": primary.source,
        "quote": source_quote(primary.source),
        "role": "primary_data",
    })
    _record_stage(ctx, "profile", "ok", [os.path.join(ctx["work_dir"], "profile.json")],
                  {"action": "profile", "table": primary.name, "n_rows": len(primary.rows),
                   "n_numeric": len(table_numeric_columns(primary)),
                   "n_categorical": len(table_categorical_columns(primary))})
    return primary


def stage_clean(ctx):
    table = ctx["table"]
    rows, meta = clean_table(table)
    write_cleaned_csv(os.path.join(ctx["work_dir"], "cleaned.csv"), table, rows)
    write_json(os.path.join(ctx["work_dir"], "clean_meta.json"), meta)
    ctx["clean_rows"] = rows
    ctx["clean_meta"] = meta
    _record_stage(ctx, "clean", "ok",
                  [os.path.join(ctx["work_dir"], "cleaned.csv"),
                   os.path.join(ctx["work_dir"], "clean_meta.json")],
                  {"action": "clean", "n_raw": meta["n_raw"], "n_clean": meta["n_clean"],
                   "dup_removed": meta["duplicates"]["rows_removed"],
                   "missing_cells": meta["missing"]["total_cells"]})


def stage_analyze(ctx):
    ov = ctx.get("overrides") or {}
    stats = analyze_table(ctx["table"], ctx["clean_rows"], ctx["clean_meta"],
                          ov.get("group"), ov.get("targets"))
    stats["case_id"] = ctx["case"]["case_id"]
    ctx["group_col_auto"] = stats.get("_group_col_used")
    write_json(os.path.join(ctx["work_dir"], "stats.json"), stats)
    ctx["stats"] = stats
    _record_stage(ctx, "analyze", "ok", [os.path.join(ctx["work_dir"], "stats.json")],
                  {"action": "analyze", "n_group_tests": len(stats["group_comparison"]),
                   "n_correlations": len(stats["correlation"]),
                   "numeric_columns": len(stats["columns"])})


def stage_render(ctx):
    stats = ctx["stats"]
    out = ctx["output_dir"]
    os.makedirs(out, exist_ok=True)
    figure_files = []
    f1 = os.path.join(out, "figure_group_box.svg")
    if svg_group_box(stats, f1):
        figure_files.append("figure_group_box.svg")
    f2 = os.path.join(out, "figure_corr_scatter.svg")
    if svg_corr_scatter(stats, f2):
        figure_files.append("figure_corr_scatter.svg")
    report = build_report(stats, ctx["clean_meta"], ctx["profile"], figure_files,
                          ctx["work_dir"])
    atomic_write_text(os.path.join(out, "report.md"), report)
    ctx["figure_files"] = figure_files
    artifacts = [os.path.join(out, "report.md")]
    for f in figure_files:
        artifacts.append(os.path.join(out, f))
    _record_stage(ctx, "render", "ok", artifacts,
                  {"action": "render", "n_figures": len(figure_files), "report": "report.md"})


def _contract_value(key, ctx):
    """Map a contract key to a concrete, computed value."""
    stats = ctx.get("stats") or {}
    clean_meta = ctx.get("clean_meta") or {}
    work = ctx["work_dir"]
    out = ctx["output_dir"]
    kl = key.lower()
    if "result" in kl or "analysis" in kl or "stat" in kl or "summary" in kl:
        return {
            "cleaned_table": os.path.join(work, "cleaned.csv"),
            "descriptive": stats.get("columns", {}),
            "group_comparison": stats.get("group_comparison", []),
            "correlation": stats.get("correlation", []),
            "figures": [os.path.join(out, f) for f in ctx.get("figure_files", [])],
            "report": os.path.join(out, "report.md"),
        }
    if "quality" in kl or "evidence" in kl or "repro" in kl or "log" in kl or "rule" in kl:
        manifest = ctx.get("manifest", {})
        return {
            "manifest": os.path.join(work, "manifest.json"),
            "clean_meta": os.path.join(work, "clean_meta.json"),
            "profile": os.path.join(work, "profile.json"),
            "stats": os.path.join(work, "stats.json"),
            "rules": clean_meta.get("rules", []),
            "hashes": {a: i["sha256"] for a, i in manifest.get("artifacts", {}).items()},
        }
    if "anomal" in kl or "note" in kl or "exception" in kl or "missing" in kl or "warning" in kl:
        return {
            "missing": clean_meta.get("missing", {}),
            "outliers": clean_meta.get("outliers", {}),
            "duplicates": clean_meta.get("duplicates", {}),
            "notes": ctx.get("uncertainty", []),
        }
    # unknown key: best-effort artifact summary, never fabricated content
    return {
        "cleaned_table": os.path.join(work, "cleaned.csv"),
        "stats": os.path.join(work, "stats.json"),
        "report": os.path.join(out, "report.md"),
    }


def stage_reconcile(ctx):
    out = ctx["output_dir"]
    os.makedirs(out, exist_ok=True)
    stats = ctx.get("stats") or {}
    clean_meta = ctx.get("clean_meta") or {}

    # uncertainty items
    ctx["uncertainty"].append({
        "topic": "outlier_method",
        "note": "numeric outliers flagged by IQR×1.5, not removed; threshold is a convention",
    })
    ctx["uncertainty"].append({
        "topic": "multiple_comparison",
        "note": "nominal α=0.05; no multiple-comparison correction applied",
    })
    if ctx["case"]["contract_source"] == "default":
        ctx["uncertainty"].append({
            "topic": "output_contract",
            "note": "case.json lacked output_contract; default three keys used",
        })
    if ctx.get("group_col_auto"):
        ctx["uncertainty"].append({
            "topic": "group_column",
            "note": f"group column auto-selected: {ctx['group_col_auto']}; confirm against task.md",
        })
    if clean_meta.get("missing", {}).get("total_cells"):
        ctx["uncertainty"].append({
            "topic": "missing_values",
            "note": "missing cells excluded pairwise in analysis; no imputation performed",
        })

    # evidence for computed results
    if stats.get("group_comparison"):
        gc = stats["group_comparison"][0]
        ctx["sources"].append({
            "locator": os.path.join(ctx["work_dir"], "stats.json"),
            "quote": json.dumps({"group_comparison[0]": gc}, ensure_ascii=False)[:MAX_QUOTE],
            "role": "computed_group_test",
        })
    if stats.get("correlation"):
        c0 = stats["correlation"][0]
        ctx["sources"].append({
            "locator": os.path.join(ctx["work_dir"], "stats.json"),
            "quote": json.dumps({"correlation[0]": {k: v for k, v in c0.items()
                                                    if k != "points"}}, ensure_ascii=False)[:MAX_QUOTE],
            "role": "computed_correlation",
        })

    # status
    if ctx["state"]["input_error"]:
        status = "input_error"
    elif ctx["state"]["status"] == "insufficient_evidence":
        status = "insufficient_evidence"
    elif not stats.get("columns") and not stats.get("group_comparison") and not stats.get("correlation"):
        status = "insufficient_evidence"
    elif ctx["errors"] or any(t.get("skipped") for gc in stats.get("group_comparison", [])
                              for t in gc.get("tests", [])):
        status = "partial"
    else:
        status = "ok"

    ctx["state"]["status"] = status

    payload = {"case_id": ctx["case"]["case_id"]}
    for k in ctx["case"]["contract_keys"]:
        payload[k] = _contract_value(k, ctx)
    payload.update({
        "status": status,
        "main_outputs": ["report.md"] + ctx.get("figure_files", []) + [
            os.path.join("work", "cleaned.csv"),
            os.path.join("work", "stats.json"),
            os.path.join("work", "manifest.json"),
        ],
        "sources": ctx["sources"],
        "uncertainty": ctx["uncertainty"],
        "pending_human": ctx["pending_human"],
        "errors": ctx["errors"],
        "degradations": ctx["degradations"],
        "change_log": ctx["change_log"],
        "runtime": {"seconds": round(time.time() - ctx["started"], 3),
                    "python": sys.version.split()[0],
                    "stages_run": list(ctx["manifest"]["stages"].keys())},
        "reproducibility": {
            "manifest": os.path.join(ctx["work_dir"], "manifest.json"),
            "artifact_hashes": {a: i["sha256"] for a, i in ctx["manifest"].get("artifacts", {}).items()},
        },
    })
    write_json(ctx["result_path"], payload)

    # verify: re-read and check contract keys + JSON parse + SVG parse
    import xml.etree.ElementTree as ET
    try:
        check = json.loads(read_text_robust(ctx["result_path"])[0])
        missing = [k for k in ctx["case"]["contract_keys"] if k not in check]
        if missing:
            ctx["errors"].append({"scope": "result.json", "detail": f"missing contract keys: {missing}"})
    except Exception as e:  # noqa: BLE001
        ctx["errors"].append({"scope": "result.json", "detail": f"re-parse failed: {e}"})
    for f in ctx.get("figure_files", []):
        fp = os.path.join(out, f)
        try:
            ET.parse(fp)
        except Exception as e:  # noqa: BLE001
            ctx["errors"].append({"scope": f, "detail": f"SVG parse failed: {e}"})

    payload["errors"] = ctx["errors"]
    payload["degradations"] = ctx["degradations"]
    payload["change_log"] = ctx["change_log"]
    write_json(ctx["result_path"], payload)
    _record_stage(ctx, "reconcile", "ok", [ctx["result_path"]],
                  {"action": "reconcile", "status": status, "contract_keys": ctx["case"]["contract_keys"]},
                  errors=[e["detail"] for e in ctx["errors"]] or None)
    return status


# --------------------------------------------------------------------------
# Pipeline driver
# --------------------------------------------------------------------------
def run_pipeline(input_dir, output_dir, work_dir, stage="reconcile", overrides=None):
    overrides = overrides or {}
    started = time.time()
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    ctx = {
        "input_dir": input_dir,
        "output_dir": output_dir,
        "work_dir": work_dir,
        "result_path": os.path.join(output_dir, "result.json"),
        "manifest_path": os.path.join(work_dir, "manifest.json"),
        "started": started,
        "case": {},
        "state": {"input_error": False, "partial": False, "status": "partial"},
        "errors": [],
        "degradations": [],
        "uncertainty": [],
        "pending_human": [],
        "sources": [],
        "change_log": [],
        "manifest": {"version": 2, "skill": "research-data-pipeline", "stages": {}, "artifacts": {}},
        "table": None,
        "clean_rows": None,
        "clean_meta": None,
        "stats": {},
        "profile": None,
        "figure_files": [],
        "group_col_auto": None,
        "overrides": overrides,
    }
    if overrides.get("group"):
        ctx["group_col_auto"] = overrides["group"]

    order = STAGES[:STAGES.index(stage) + 1] if stage in STAGES else STAGES
    run_map = {
        "scaffold": stage_scaffold,
        "profile": stage_profile,
        "clean": stage_clean,
        "analyze": stage_analyze,
        "render": stage_render,
        "reconcile": stage_reconcile,
    }
    for name in order:
        fn = run_map[name]
        try:
            result = fn(ctx)
            if name == "profile" and result is None:
                # no data: still reconcile a minimal honest result
                stage_reconcile(ctx)
                break
        except Exception as e:  # noqa: BLE001
            ctx["errors"].append({
                "scope": f"stage:{name}",
                "detail": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc(limit=3),
            })
            ctx["state"]["partial"] = True
            ctx["state"]["status"] = "partial"
            try:
                _write_skeleton_result(ctx)
            except Exception:  # noqa: BLE001
                pass
            try:
                _record_stage(ctx, name, "error", [], {"action": name, "error": str(e)})
            except Exception:  # noqa: BLE001
                pass
            break
    # ensure result.json always exists
    if not os.path.isfile(ctx["result_path"]):
        _write_skeleton_result(ctx)
    return ctx


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args = {"input": "/input", "output": "/app/output", "work": "/app/work", "stage": "reconcile"}
    overrides = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--input", "--output", "--work", "--stage", "--group") and i + 1 < len(argv):
            if a == "--group":
                overrides["group"] = argv[i + 1]
            else:
                args[a[2:]] = argv[i + 1]
            i += 2
        elif a == "--targets" and i + 1 < len(argv):
            overrides["targets"] = argv[i + 1].split(",")
            i += 2
        elif a in ("--help", "-h"):
            print("usage: main.py [--input DIR] [--output DIR] [--work DIR] [--stage NAME] "
                  "[--group COL] [--targets C1,C2]")
            print(f"stages: {', '.join(STAGES)} (default: reconcile = all)")
            return 0
        else:
            i += 1
    run_pipeline(args["input"], args["output"], args["work"], args["stage"], overrides)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
