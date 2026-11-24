"""core_impl.py - budgeted multi-step tabular analysis engine (pure stdlib).

Deterministic, idempotent, exit-code-0-always. Every command either succeeds or
records an honest error/degradation; no step ever leaves an empty /app/output
after `scaffold` has run.
"""
import csv
import io
import json
import math
import os
import re
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET

SCHEMA_VERSION = "2.0.0"
DEFAULT_CONTRACT = ["analysis_results", "quality_evidence", "anomaly_notes"]
NA_TOKENS = {"", "na", "n/a", "nan", "null", "none", "-", "--", "?", "#n/a", "n.d.", "nd"}
STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")


# --------------------------------------------------------------------------
# small utilities
# --------------------------------------------------------------------------
def now_epoch():
    return time.time()


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text_bytes(path):
    """Read bytes and decode with fallback encodings. Returns (text, encoding)."""
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in ("utf-8-sig", "utf-8", "latin-1", "gbk"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1"


def read_text(path):
    return read_text_bytes(path)[0]


def read_json(path):
    try:
        return json.loads(read_text(path))
    except Exception:
        return None


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, path)


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


def fmt_num(x, digits=4):
    """Deterministic numeric formatting."""
    if x is None:
        return "null"
    if isinstance(x, bool):
        return "true" if x else "false"
    try:
        f = float(x)
    except (TypeError, ValueError):
        return str(x)
    if math.isnan(f):
        return "nan"
    if math.isinf(f):
        return "inf" if f > 0 else "-inf"
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    return ("%%.%dg" % digits) % f


def safe_float(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return None if math.isnan(f) else f
    s = str(v).strip()
    if s.lower() in NA_TOKENS:
        return None
    try:
        f = float(s)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def parse_args(argv, defaults):
    """Parse `--key value` and `--key` boolean args into a dict."""
    out = dict(defaults)
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            key = a[2:]
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                out[key] = argv[i + 1]
                i += 2
            else:
                out[key] = True
                i += 1
        else:
            i += 1
    return out


# --------------------------------------------------------------------------
# statistical math (pure stdlib: lgamma + incomplete beta/gamma)
# --------------------------------------------------------------------------
def _mean(xs):
    n = len(xs)
    return math.fsum(xs) / n if n else None


def _sd(xs, ddof=1):
    n = len(xs)
    if n < 2:
        return None
    m = _mean(xs)
    ss = math.fsum((x - m) ** 2 for x in xs)
    var = ss / (n - ddof)
    return math.sqrt(var) if var >= 0 else None


def _median(xs):
    n = len(xs)
    if not n:
        return None
    s = sorted(xs)
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def _quantile(xs, p):
    """Linear-interpolation quantile (type 7, matches numpy default)."""
    n = len(xs)
    if not n:
        return None
    s = sorted(xs)
    if n == 1:
        return s[0]
    pos = p * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return s[lo]
    frac = pos - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def _betacf(a, b, x):
    EPS = 3e-14
    FPMIN = 1e-300
    MAXIT = 200
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
        ddel = d * c
        h *= ddel
        if abs(ddel - 1.0) < EPS:
            break
    return h


def _incbeta(a, b, x):
    """Regularized incomplete beta I_x(a,b)."""
    if a <= 0 or b <= 0:
        return None
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1.0 - x)
    bt = math.exp(lbeta)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _gser(a, x):
    EPS = 3e-14
    ITMAX = 200
    if x <= 0:
        return 0.0
    ap = a
    summ = 1.0 / a
    d = summ
    for _ in range(1, ITMAX + 1):
        ap += 1.0
        d *= x / ap
        summ += d
        if abs(d) < abs(summ) * EPS:
            break
    return summ * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gcf(a, x):
    EPS = 3e-14
    ITMAX = 200
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
        ddel = d * c
        h *= ddel
        if abs(ddel - 1.0) < EPS:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def gammainc_p(a, x):
    """Lower regularized incomplete gamma P(a,x)."""
    if a <= 0 or x < 0:
        return None
    if x == 0:
        return 0.0
    if x < a + 1.0:
        return _gser(a, x)
    return 1.0 - _gcf(a, x)


def norm_twotail_p(z):
    return math.erfc(abs(z) / math.sqrt(2.0))


def t_twotail_p(t, df):
    if df <= 0:
        return None
    x = df / (df + t * t)
    return _incbeta(df / 2.0, 0.5, x)


def f_survival_p(F, d1, d2):
    if F <= 0:
        return 1.0
    if d1 <= 0 or d2 <= 0:
        return None
    x = d1 * F / (d1 * F + d2)
    return 1.0 - _incbeta(d1 / 2.0, d2 / 2.0, x)


def chisq_survival_p(x, k):
    if x < 0 or k <= 0:
        return None
    return 1.0 - gammainc_p(k / 2.0, x / 2.0)


def ranks_all(values):
    """Average ranks for tied values (ascending)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    n = len(order)
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        r = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = r
        i = j + 1
    return ranks


def _tie_correction(values):
    from collections import Counter
    n = len(values)
    if n < 2:
        return 0.0
    counts = Counter(values)
    return math.fsum(c ** 3 - c for c in counts.values() if c > 1)


def welch_t(a, b):
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return {"name": "welch_t", "status": "skipped", "reason": "sample too small"}
    ma, mb = _mean(a), _mean(b)
    va, vb = _sd(a) ** 2, _sd(b) ** 2
    if va is None or vb is None or (va == 0 and vb == 0):
        return {"name": "welch_t", "status": "skipped", "reason": "zero variance"}
    se = math.sqrt(va / na + vb / nb)
    if se == 0:
        return {"name": "welch_t", "status": "skipped", "reason": "zero standard error"}
    t = (ma - mb) / se
    df_num = (va / na + vb / nb) ** 2
    df_den = (va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1)
    df = df_num / df_den if df_den > 0 else na + nb - 2
    p = t_twotail_p(t, df)
    return {"name": "welch_t", "status": "ok", "statistic": t, "df": df, "p": p, "n": [na, nb],
            "mean_diff": ma - mb}


def mann_whitney_u(a, b):
    na, nb = len(a), len(b)
    n = na + nb
    if na == 0 or nb == 0:
        return {"name": "mann_whitney_u", "status": "skipped", "reason": "empty group"}
    combined = list(a) + list(b)
    ranks = ranks_all(combined)
    r1 = math.fsum(ranks[:na])
    u1 = r1 - na * (na + 1) / 2.0
    u2 = na * nb - u1
    U = min(u1, u2)
    tie = _tie_correction(combined)
    var = na * nb / 12.0 * ((n + 1) - tie / (n * (n - 1))) if n > 1 else 0.0
    if var <= 0:
        return {"name": "mann_whitney_u", "status": "skipped", "reason": "zero variance (all tied)"}
    z = (U - na * nb / 2.0) / math.sqrt(var)
    p = norm_twotail_p(z)
    return {"name": "mann_whitney_u", "status": "ok", "statistic": U, "z": z, "p": p,
            "n": [na, nb], "ties": int(tie)}


def anova_f(groups):
    k = len(groups)
    all_vals = [v for g in groups for v in g]
    N = len(all_vals)
    if k < 2 or N <= k:
        return {"name": "anova_f", "status": "skipped", "reason": "not enough groups/data"}
    grand = _mean(all_vals)
    ss_between = math.fsum(len(g) * (_mean(g) - grand) ** 2 for g in groups if len(g))
    ss_within = math.fsum(math.fsum((v - _mean(g)) ** 2 for v in g) for g in groups if len(g) >= 2)
    df_b = k - 1
    df_w = N - k
    if df_w <= 0 or ss_within == 0:
        return {"name": "anova_f", "status": "skipped", "reason": "zero within-group variance"}
    ms_b = ss_between / df_b
    ms_w = ss_within / df_w
    F = ms_b / ms_w if ms_w > 0 else None
    p = f_survival_p(F, df_b, df_w) if F is not None else None
    return {"name": "anova_f", "status": "ok", "statistic": F, "df1": df_b, "df2": df_w, "p": p,
            "n_groups": k, "n": N}


def kruskal_wallis(groups):
    k = len(groups)
    all_vals = [v for g in groups for v in g]
    N = len(all_vals)
    if k < 2 or N <= k:
        return {"name": "kruskal_wallis_h", "status": "skipped", "reason": "not enough groups/data"}
    ranks = ranks_all(all_vals)
    idx = 0
    rsums = []
    for g in groups:
        rsums.append(math.fsum(ranks[idx:idx + len(g)]))
        idx += len(g)
    H = 12.0 / (N * (N + 1)) * math.fsum(rs ** 2 / len(g) for g, rs in zip(groups, rsums) if len(g)) - 3.0 * (N + 1)
    tie = _tie_correction(all_vals)
    if N ** 3 - N > 0:
        H = H / (1.0 - tie / (N ** 3 - N))
    p = chisq_survival_p(H, k - 1)
    return {"name": "kruskal_wallis_h", "status": "ok", "statistic": H, "df": k - 1, "p": p,
            "n_groups": k, "n": N, "ties": int(tie)}


def pearson(a, b):
    n = len(a)
    if n < 3 or len(b) != n:
        return {"name": "pearson", "status": "skipped", "reason": "sample too small or length mismatch"}
    ma, mb = _mean(a), _mean(b)
    sa, sb = _sd(a), _sd(b)
    if sa is None or sb is None or sa == 0 or sb == 0:
        return {"name": "pearson", "status": "skipped", "reason": "zero variance"}
    cov = math.fsum((x - ma) * (y - mb) for x, y in zip(a, b)) / (n - 1)
    r = cov / (sa * sb)
    r = max(-1.0, min(1.0, r))
    slope = cov / (sa ** 2)
    intercept = mb - slope * ma
    if 1 - r * r <= 0:
        return {"name": "pearson", "status": "ok", "statistic": r, "t": None, "df": n - 2, "p": None,
                "n": n, "slope": slope, "intercept": intercept, "note": "perfect linear relation"}
    t = r * math.sqrt((n - 2) / (1 - r * r))
    p = t_twotail_p(t, n - 2)
    return {"name": "pearson", "status": "ok", "statistic": r, "t": t, "df": n - 2, "p": p,
            "n": n, "slope": slope, "intercept": intercept}


def cohens_d(a, b):
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return None
    ma, mb = _mean(a), _mean(b)
    va, vb = _sd(a) ** 2, _sd(b) ** 2
    sp = math.sqrt(((na - 1) * va + (nb - 1) * vb) / (na + nb - 2))
    if sp == 0:
        return None
    return (ma - mb) / sp


def eta_squared(groups):
    all_vals = [v for g in groups for v in g]
    N = len(all_vals)
    grand = _mean(all_vals)
    ss_b = math.fsum(len(g) * (_mean(g) - grand) ** 2 for g in groups if len(g))
    ss_t = math.fsum((v - grand) ** 2 for v in all_vals)
    if ss_t == 0:
        return None
    return ss_b / ss_t


# --------------------------------------------------------------------------
# data discovery and table loading
# --------------------------------------------------------------------------
DATA_EXTS = (".csv", ".tsv", ".tab", ".txt", ".json", ".jsonl", ".ndjson", ".sqlite", ".sqlite3", ".db")
RESERVED_BASENAMES = {"case.json", "task.md", "result.json", "challenge.json", "output.json"}


def sniff_delimiter(sample):
    counts = {",": sample.count(","), "\t": sample.count("\t"), ";": sample.count(";")}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def iter_data_files(input_dir):
    out = []
    if not os.path.isdir(input_dir):
        return out
    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            if name.lower() in RESERVED_BASENAMES or name.startswith("."):
                continue
            if name.lower().endswith(DATA_EXTS):
                out.append(os.path.join(root, name))
    return out


def _unify_records(records):
    """list of dicts -> (headers, rows)."""
    headers = []
    for rec in records:
        if isinstance(rec, dict):
            for k in rec.keys():
                if k not in headers:
                    headers.append(k)
    rows = []
    for rec in records:
        if isinstance(rec, dict):
            rows.append([rec.get(h) for h in headers])
    return headers, rows


def load_delimited(path):
    text, enc = read_text_bytes(path)
    sample = text[:4096]
    delim = sniff_delimiter(sample)
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = []
    for row in reader:
        if not row or all(not str(c).strip() for c in row):
            continue
        rows.append([str(c).strip() for c in row])
    if not rows:
        return [], [], {"delimiter": delim, "encoding": enc, "kind": "delimited"}
    headers = rows[0]
    if headers and headers[0].startswith("\ufeff"):
        headers[0] = headers[0].lstrip("\ufeff")
    body = rows[1:]
    return headers, body, {"delimiter": delim, "encoding": enc, "kind": "delimited"}


def load_json_table(path):
    data = read_json(path)
    if data is None:
        return [], [], {"kind": "json", "error": "invalid json"}
    if isinstance(data, list):
        headers, rows = _unify_records(data)
        return headers, rows, {"kind": "json"}
    if isinstance(data, dict):
        for key in ("data", "rows", "records", "items"):
            if isinstance(data.get(key), list):
                headers, rows = _unify_records(data[key])
                return headers, rows, {"kind": "json", "list_key": key}
        headers, rows = _unify_records([data])
        return headers, rows, {"kind": "json", "single_record": True}
    return [], [], {"kind": "json", "error": "unsupported structure"}


def load_jsonl_table(path):
    rows_records = []
    text = read_text(path)
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows_records.append(json.loads(line))
        except Exception:
            continue
    headers, rows = _unify_records(rows_records)
    return headers, rows, {"kind": "jsonl"}


def sqlite_tables(path):
    try:
        con = sqlite3.connect(path)
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        tables = [r[0] for r in cur.fetchall()]
        con.close()
        return tables
    except Exception as exc:
        return []


def load_sqlite_table(path, table=None):
    try:
        con = sqlite3.connect(path)
        cur = con.cursor()
        tables = sqlite_tables(path)
        if not tables:
            con.close()
            return [], [], {"kind": "sqlite", "error": "no tables"}
        target = table if table in tables else tables[0]
        cur.execute("PRAGMA table_info(%s)" % target)
        cols = [r[1] for r in cur.fetchall()]
        cur.execute("SELECT * FROM %s" % target)
        rows = [[str(c) for c in row] for row in cur.fetchall()]
        con.close()
        return cols, rows, {"kind": "sqlite", "table": target, "tables": tables}
    except Exception as exc:
        return [], [], {"kind": "sqlite", "error": str(exc)}


def load_table(path, table=None):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".sqlite", ".sqlite3", ".db"):
        return load_sqlite_table(path, table)
    if ext == ".json":
        return load_json_table(path)
    if ext in (".jsonl", ".ndjson"):
        return load_jsonl_table(path)
    return load_delimited(path)


# --------------------------------------------------------------------------
# column typing and profiling
# --------------------------------------------------------------------------
def is_int_token(v):
    if not isinstance(v, str) or not v.strip():
        return False
    s = v.strip()
    if s.lower() in NA_TOKENS:
        return False
    try:
        int(s)
        return True
    except ValueError:
        return False


def is_float_token(v):
    return safe_float(v) is not None


def classify_column(values):
    """Classify a column's dtype from string values (excluding NA)."""
    non_na = [v for v in values if not (isinstance(v, str) and v.strip().lower() in NA_TOKENS)]
    n = len(non_na)
    if n == 0:
        return "empty"
    n_num = sum(1 for v in non_na if is_float_token(v))
    n_int = sum(1 for v in non_na if is_int_token(v))
    if n_num >= 0.9 * n:
        return "int" if n_int == n_num else "numeric"
    # boolean-like
    bools = {v.strip().lower() for v in non_na if isinstance(v, str)}
    if bools <= {"true", "false", "0", "1", "yes", "no"} and len(bools) <= 2:
        return "boolean"
    return "text"


def column_profile(name, values):
    floats = [safe_float(v) for v in values]
    floats = [f for f in floats if f is not None]
    n = len(values)
    missing = sum(1 for v in values if isinstance(v, str) and v.strip().lower() in NA_TOKENS)
    unique = len(set(str(v) for v in values))
    prof = {"name": name, "n": n, "missing": missing, "unique": unique,
            "dtype": classify_column(values)}
    if floats:
        prof.update({"mean": _mean(floats), "median": _median(floats), "sd": _sd(floats),
                     "q1": _quantile(floats, 0.25), "q3": _quantile(floats, 0.75),
                     "min": min(floats), "max": max(floats)})
        q1, q3 = prof["q1"], prof["q3"]
        iqr = q3 - q1
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        prof["outlier_iqr"] = sum(1 for f in floats if f < lo or f > hi)
    return prof


# --------------------------------------------------------------------------
# budget clock
# --------------------------------------------------------------------------
PHASE_DEADLINES = {"scaffold": 180, "profile": 360, "clean": 600, "analyze": 600,
                   "render": 780, "finalize": 900}


def load_clock(work):
    p = os.path.join(work, "clock.json")
    if os.path.isfile(p):
        c = read_json(p)
        if isinstance(c, dict):
            return c
    return {"t0": now_epoch(), "deadline_s": 900, "phases": dict(PHASE_DEADLINES), "history": []}


def save_clock(work, clock):
    write_json(os.path.join(work, "clock.json"), clock)


def touch_clock(work, phase, deadline_s=900):
    clock = load_clock(work)
    clock["deadline_s"] = deadline_s
    now = now_epoch()
    elapsed = now - clock["t0"]
    remaining = deadline_s - elapsed
    rec = {"phase": phase, "t": now, "elapsed_s": round(elapsed, 1), "remaining_s": round(remaining, 1)}
    if remaining <= 30:
        rec["recommendation"] = "STOP: finalize now; do not start new work"
    elif remaining <= 120:
        rec["recommendation"] = "low budget: skip optional figures, run finalize"
    elif remaining <= 240:
        rec["recommendation"] = "tight: prioritize finalize over extra charts"
    else:
        rec["recommendation"] = "on schedule"
    clock["history"].append(rec)
    save_clock(work, clock)
    return clock


# --------------------------------------------------------------------------
# contract extraction
# --------------------------------------------------------------------------
def extract_contract(input_dir, case):
    """Return (field_names, raw_contract)."""
    raw = case.get("output_contract", case.get("outputContract"))
    if isinstance(raw, str):
        p = os.path.join(input_dir, raw)
        if os.path.isfile(p):
            loaded = read_json(p)
            if loaded is not None:
                raw = loaded
    if isinstance(raw, dict):
        for key in ("fields", "result_fields", "required_fields", "required"):
            if isinstance(raw.get(key), list):
                return [str(f) for f in raw[key]], raw
        keys = [str(k) for k in raw.keys() if not str(k).startswith("_")]
        if keys:
            return keys, raw
    if isinstance(raw, list):
        return [str(f) for f in raw], {"fields": raw}
    return list(DEFAULT_CONTRACT), None


def extract_case_id(input_dir, case):
    for key in ("case_id", "challenge_id", "armiId", "id"):
        v = case.get(key)
        if v not in (None, ""):
            return str(v)
    return os.path.basename(os.path.abspath(input_dir))


# --------------------------------------------------------------------------
# command implementations
# --------------------------------------------------------------------------
def cmd_scaffold(args):
    input_dir = args.get("input", "/input")
    output_dir = args.get("output", "/app/output")
    work = args.get("work", "/app/work")
    try:
        deadline = int(float(args.get("deadline", "900")))
    except (TypeError, ValueError):
        deadline = 900
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work, exist_ok=True)

    case = read_json(os.path.join(input_dir, "case.json"))
    case_id = extract_case_id(input_dir, case) if isinstance(case, dict) else os.path.basename(os.path.abspath(input_dir))
    contract_fields, raw_contract = extract_contract(input_dir, case) if isinstance(case, dict) else (list(DEFAULT_CONTRACT), None)

    errors = []
    degradations = []
    if not isinstance(case, dict):
        errors.append("case.json missing or invalid; input_error candidate")
    if raw_contract is None:
        degradations.append("no output_contract in case.json; used default fields " + ",".join(DEFAULT_CONTRACT))

    skeleton = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "status": "partial",
        "output_contract_echo": raw_contract,
        "artifacts": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": errors,
        "degradations": degradations,
        "change_log": [{"t": now_iso(), "phase": "scaffold",
                        "action": "wrote partial skeleton; contract fields initialized to null"}],
        "runtime": {"deadline_s": deadline, "requests_estimate": 1},
    }
    for f in contract_fields:
        skeleton[f] = None
    write_json(os.path.join(output_dir, "result.json"), skeleton)
    touch_clock(work, "scaffold", deadline)
    print(json.dumps({"command": "scaffold", "case_id": case_id,
                      "contract_fields": contract_fields, "status": "partial"}, ensure_ascii=False))
    return 0


def cmd_profile(args):
    input_dir = args.get("input", "/input")
    work = args.get("work", "/app/work")
    os.makedirs(work, exist_ok=True)
    primary_override = args.get("primary")
    table_override = args.get("table")

    case = read_json(os.path.join(input_dir, "case.json"))
    case_id = extract_case_id(input_dir, case) if isinstance(case, dict) else os.path.basename(os.path.abspath(input_dir))

    files = []
    primary = None
    errors = []
    degradations = []

    candidates = iter_data_files(input_dir)
    if not candidates:
        prof = {"schema_version": SCHEMA_VERSION, "case_id": case_id, "input_dir": input_dir,
                "files": [], "fields": [], "primary": None, "inferred": {},
                "errors": ["no data files found in input"], "degradations": []}
        write_json(os.path.join(work, "profile.json"), prof)
        touch_clock(work, "profile")
        print(json.dumps({"command": "profile", "sources": 0, "status": "insufficient_evidence"}, ensure_ascii=False))
        return 0

    for path in candidates:
        if primary_override and os.path.basename(path) != primary_override and path != primary_override:
            continue
        headers, rows, meta = load_table(path, table_override)
        if not headers and not rows and "error" in meta:
            errors.append("load failed %s: %s" % (os.path.basename(path), meta["error"]))
            continue
        if not headers:
            degradations.append("empty table %s" % os.path.basename(path))
            continue
        rec = {"path": path, "basename": os.path.basename(path), "headers": headers,
               "n_rows": len(rows), "n_cols": len(headers)}
        rec.update(meta)
        files.append(rec)
        if primary is None:
            primary = rec

    if primary is None:
        prof = {"schema_version": SCHEMA_VERSION, "case_id": case_id, "input_dir": input_dir,
                "files": files, "fields": [], "primary": None, "inferred": {},
                "errors": errors, "degradations": degradations}
        write_json(os.path.join(work, "profile.json"), prof)
        touch_clock(work, "profile")
        return 0

    headers = primary["headers"]
    rows = load_table(primary["path"], primary.get("table"))[1]
    fields = [column_profile(headers[i], [r[i] for r in rows]) for i in range(len(headers))]

    # infer group and targets
    group = None
    targets = []
    # group: categorical-ish with 2..min(10, n/2) unique values, fewest unique first
    n_rows = primary["n_rows"]
    max_cat = min(10, max(2, n_rows // 2))
    cat_fields = [f for f in fields if 2 <= f["unique"] <= max_cat and f["dtype"] in ("text", "boolean", "int")]
    cat_fields.sort(key=lambda f: f["unique"])
    if cat_fields:
        group = cat_fields[0]["name"]
    num_fields = [f for f in fields if f["dtype"] in ("numeric", "int") and f["unique"] > 1]
    # drop id-like numeric columns with all-unique values unless only candidate
    for f in num_fields:
        if f["unique"] == n_rows and len(num_fields) > 1:
            continue
        targets.append(f["name"])
    if not targets and num_fields:
        targets = [f["name"] for f in num_fields]

    if args.get("group"):
        group = args["group"]
    if args.get("targets"):
        targets = [t.strip() for t in str(args["targets"]).split(",") if t.strip()]

    inferred = {"group": group, "targets": targets,
                "group_reason": "fewest unique categorical value" if group else "none found",
                "targets_reason": "numeric non-id columns" if targets else "none found"}

    task_hint = ""
    tpath = os.path.join(input_dir, "task.md")
    if os.path.isfile(tpath):
        task_hint = read_text(tpath)[:500]

    prof = {"schema_version": SCHEMA_VERSION, "case_id": case_id, "input_dir": input_dir,
            "files": files, "fields": fields, "primary": primary, "inferred": inferred,
            "task_hint": task_hint, "errors": errors, "degradations": degradations}
    write_json(os.path.join(work, "profile.json"), prof)
    touch_clock(work, "profile")
    print(json.dumps({"command": "profile", "sources": len(files),
                      "primary": os.path.basename(primary["path"]),
                      "group": group, "targets": targets}, ensure_ascii=False))
    return 0


def _resolve_primary(work, input_dir, primary_override=None):
    prof_path = os.path.join(work, "profile.json")
    prof = read_json(prof_path)
    if isinstance(prof, dict) and isinstance(prof.get("primary"), dict):
        return prof["primary"]
    # fall back: discover on the fly
    candidates = iter_data_files(input_dir)
    for path in candidates:
        headers, rows, meta = load_table(path)
        if headers:
            return {"path": path, "basename": os.path.basename(path), "headers": headers,
                    "n_rows": len(rows), "n_cols": len(headers), **meta}
    return None


def cmd_clean(args):
    input_dir = args.get("input", "/input")
    work = args.get("work", "/app/work")
    os.makedirs(work, exist_ok=True)
    primary = _resolve_primary(work, input_dir, args.get("primary"))
    clean_meta = {"schema_version": SCHEMA_VERSION, "rules": [], "row_impacts": [],
                  "outliers": {}, "degradations": [], "errors": []}
    if primary is None:
        clean_meta["errors"].append("no primary table to clean")
        write_json(os.path.join(work, "clean_meta.json"), clean_meta)
        touch_clock(work, "clean")
        return 0

    headers, rows, _meta = load_table(primary["path"], primary.get("table"))
    orig_n = len(rows)
    clean_meta["source"] = {"path": primary["path"], "basename": primary["basename"],
                            "headers": headers, "original_rows": orig_n}

    # 1) exact-duplicate removal
    seen = set()
    dedup_rows = []
    n_dup = 0
    for r in rows:
        key = tuple(r)
        if key in seen:
            n_dup += 1
            continue
        seen.add(key)
        dedup_rows.append(r)
    clean_meta["rules"].append({"step": 1, "rule": "drop exact duplicate rows",
                                "dropped": n_dup, "note": "recorded, not silent"})
    rows = dedup_rows

    # 2) missing handling: drop rows where group column or all target columns are NA
    prof = read_json(os.path.join(work, "profile.json")) if os.path.isfile(os.path.join(work, "profile.json")) else None
    inferred = (prof or {}).get("inferred", {})
    group = args.get("group") or inferred.get("group")
    targets = args.get("targets")
    if targets:
        targets = [t.strip() for t in str(targets).split(",") if t.strip()]
    else:
        targets = inferred.get("targets", [])
    req_cols = []
    if group and group in headers:
        req_cols.append(headers.index(group))
    target_idx = [headers.index(t) for t in targets if t in headers]

    keep = []
    n_missing_drop = 0
    for r in rows:
        if req_cols and all(isinstance(r[i], str) and r[i].strip().lower() in NA_TOKENS for i in req_cols):
            n_missing_drop += 1
            continue
        if target_idx and all(isinstance(r[i], str) and r[i].strip().lower() in NA_TOKENS for i in target_idx):
            n_missing_drop += 1
            continue
        keep.append(r)
    clean_meta["rules"].append({"step": 2,
                                "rule": "drop rows with missing group key or all target columns missing",
                                "dropped": n_missing_drop, "required_columns": [headers[i] for i in req_cols],
                                "target_columns": targets})
    rows = keep

    # 3) outlier flagging (never dropped)
    method = args.get("outlier_method", "iqr")
    z_thresh = 3.0
    try:
        z_thresh = float(args.get("z", "3.0"))
    except (TypeError, ValueError):
        z_thresh = 3.0
    outlier_flags = []
    for r in rows:
        outlier_flags.append(set())
    for t in targets:
        if t not in headers:
            continue
        idx = headers.index(t)
        vals = [safe_float(r[idx]) for r in rows]
        valid = [v for v in vals if v is not None]
        if not valid:
            continue
        if method == "z":
            m = _mean(valid)
            s = _sd(valid)
            if s is None or s == 0:
                continue
            lo, hi = m - z_thresh * s, m + z_thresh * s
            rule = "z-score beyond +/-%g" % z_thresh
        else:
            q1, q3 = _quantile(valid, 0.25), _quantile(valid, 0.75)
            iqr = q3 - q1
            lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            rule = "1.5*IQR Tukey fences"
        cnt = 0
        for i, v in enumerate(vals):
            if v is not None and (v < lo or v > hi):
                outlier_flags[i].add(t)
                cnt += 1
        clean_meta["outliers"][t] = {"method": method, "rule": rule, "lower": lo, "upper": hi,
                                     "count": cnt, "note": "flagged only, not dropped"}
        clean_meta["rules"].append({"step": 3, "rule": "flag outliers: " + t + " (" + rule + ")",
                                    "dropped": 0, "flagged": cnt})

    # 4) numeric coercion for numeric columns (preserve originals in /input)
    numeric_cols = set(targets)
    coerced = 0
    coerced_rows = []
    for i, r in enumerate(rows):
        nr = list(r)
        for t in numeric_cols:
            if t in headers:
                idx = headers.index(t)
                f = safe_float(r[idx])
                if f is not None:
                    nr[idx] = ("%.15g" % f).rstrip("0").rstrip(".")
                    coerced += 1
        nr.append(",".join(sorted(outlier_flags[i])) if outlier_flags[i] else "")
        coerced_rows.append(nr)
    out_headers = list(headers) + ["__outlier_flag"]
    with open(os.path.join(work, "cleaned.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(out_headers)
        w.writerows(coerced_rows)
    clean_meta["row_impacts"] = [
        {"stage": "original", "rows": orig_n},
        {"stage": "after_dedup", "rows": orig_n - n_dup},
        {"stage": "after_missing_drop", "rows": orig_n - n_dup - n_missing_drop},
        {"stage": "final_cleaned", "rows": len(coerced_rows)},
    ]
    clean_meta["final_rows"] = len(coerced_rows)
    clean_meta["numeric_coercions"] = coerced
    write_json(os.path.join(work, "clean_meta.json"), clean_meta)
    touch_clock(work, "clean")
    print(json.dumps({"command": "clean", "original_rows": orig_n, "dedup_dropped": n_dup,
                      "missing_dropped": n_missing_drop, "final_rows": len(coerced_rows),
                      "outliers_flagged": {t: clean_meta["outliers"].get(t, {}).get("count", 0) for t in targets}},
                     ensure_ascii=False))
    return 0


def _load_cleaned_or_primary(work, input_dir):
    cleaned_path = os.path.join(work, "cleaned.csv")
    if os.path.isfile(cleaned_path):
        with open(cleaned_path, "r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
        if rows:
            headers = rows[0]
            body = rows[1:]
            return headers, body
    primary = _resolve_primary(work, input_dir)
    if primary:
        headers, rows, _ = load_table(primary["path"], primary.get("table"))
        return headers, rows
    return None, None


def cmd_analyze(args):
    input_dir = args.get("input", "/input")
    work = args.get("work", "/app/work")
    os.makedirs(work, exist_ok=True)
    headers, rows = _load_cleaned_or_primary(work, input_dir)
    stats = {"schema_version": SCHEMA_VERSION, "case_id": None,
             "group": None, "targets": [], "n_total": 0, "descriptive": {},
             "group_comparison": {}, "correlation": {}, "assumptions": [],
             "degradations": [], "errors": []}
    prof = read_json(os.path.join(work, "profile.json")) if os.path.isfile(os.path.join(work, "profile.json")) else None
    if isinstance(prof, dict):
        stats["case_id"] = prof.get("case_id")

    if not headers or not rows:
        stats["errors"].append("no data to analyze")
        write_json(os.path.join(work, "stats.json"), stats)
        touch_clock(work, "analyze")
        return 0

    # header cleanup for the added outlier flag column
    def _idx(name):
        try:
            return headers.index(name)
        except ValueError:
            return None

    inferred = (prof or {}).get("inferred", {})
    group = args.get("group") or inferred.get("group")
    if args.get("targets"):
        targets = [t.strip() for t in str(args["targets"]).split(",") if t.strip()]
    else:
        targets = inferred.get("targets", [])
    if not targets:
        targets = [h for h in headers if h != "__outlier_flag" and classify_column([r[headers.index(h)] for r in rows]) in ("numeric", "int")]

    corr_pairs = []
    if args.get("corr"):
        for pair in str(args["corr"]).split(";"):
            parts = [p.strip() for p in pair.split(",") if p.strip()]
            if len(parts) == 2:
                corr_pairs.append(parts)

    stats["group"] = group
    stats["targets"] = targets
    stats["n_total"] = len(rows)

    # descriptive stats for targets
    for t in targets:
        idx = _idx(t)
        if idx is None:
            stats["degradations"].append("target column missing: " + t)
            continue
        vals = [safe_float(r[idx]) for r in rows]
        vals = [v for v in vals if v is not None]
        if not vals:
            stats["degradations"].append("target column all-NA: " + t)
            continue
        stats["descriptive"][t] = {"n": len(vals), "mean": _mean(vals), "sd": _sd(vals),
                                   "median": _median(vals), "q1": _quantile(vals, 0.25),
                                   "q3": _quantile(vals, 0.75), "min": min(vals), "max": max(vals),
                                   "sem": (_sd(vals) / math.sqrt(len(vals))) if len(vals) > 1 else None}

    # group comparison
    if group and _idx(group) is not None:
        gi = _idx(group)
        for t in targets:
            ti = _idx(t)
            if ti is None:
                continue
            buckets = {}
            for r in rows:
                g = str(r[gi]).strip() if r[gi] is not None else ""
                if g.lower() in NA_TOKENS:
                    continue
                v = safe_float(r[ti])
                if v is None:
                    continue
                buckets.setdefault(g, []).append(v)
            buckets = {g: buckets[g] for g in sorted(buckets)}
            if len(buckets) < 2:
                stats["degradations"].append("group comparison skipped for %s: <2 groups" % t)
                continue
            group_summary = {}
            for g, vals in buckets.items():
                group_summary[g] = {"n": len(vals), "mean": _mean(vals), "sd": _sd(vals),
                                    "sem": (_sd(vals) / math.sqrt(len(vals))) if len(vals) > 1 else None,
                                    "median": _median(vals), "q1": _quantile(vals, 0.25),
                                    "q3": _quantile(vals, 0.75), "min": min(vals), "max": max(vals)}
            glist = [buckets[g] for g in sorted(buckets)]
            entry = {"groups": group_summary}
            if len(buckets) == 2:
                entry["test_parametric"] = welch_t(glist[0], glist[1])
                entry["test_nonparametric"] = mann_whitney_u(glist[0], glist[1])
                d = cohens_d(glist[0], glist[1])
                entry["effect_size"] = {"name": "cohens_d", "value": d}
            else:
                entry["test_parametric"] = anova_f(glist)
                entry["test_nonparametric"] = kruskal_wallis(glist)
                es = eta_squared(glist)
                entry["effect_size"] = {"name": "eta_squared", "value": es}
            entry["assumptions"] = ["independence assumed", "normality not formally tested; "
                                    "non-parametric test reported alongside", "2-group primary test: "
                                    "Welch t (unequal-variance); >=3-group: one-way ANOVA F"]
            stats["group_comparison"][t] = entry
    elif group:
        stats["degradations"].append("group column not found or invalid: " + str(group))

    # correlation
    for (x, y) in corr_pairs:
        xi, yi = _idx(x), _idx(y)
        if xi is None or yi is None:
            stats["degradations"].append("correlation pair missing column: %s,%s" % (x, y))
            continue
        pairs = [(safe_float(r[xi]), safe_float(r[yi])) for r in rows]
        pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
        res = pearson([a for a, _ in pairs], [b for _, b in pairs])
        res["x"] = x
        res["y"] = y
        res["points"] = [[a, b] for a, b in pairs][:1000]
        stats["correlation"]["%s~%s" % (x, y)] = res

    write_json(os.path.join(work, "stats.json"), stats)
    touch_clock(work, "analyze")
    print(json.dumps({"command": "analyze", "n_total": stats["n_total"], "group": group,
                      "targets": targets, "group_tests": len(stats["group_comparison"]),
                      "correlations": len(stats["correlation"])}, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------
# SVG rendering (deterministic, editable)
# --------------------------------------------------------------------------
def _svg_doc(width, height, title, subtitle):
    return {"w": width, "h": height, "title": title, "subtitle": subtitle,
            "margin": {"l": 70, "r": 20, "t": 60, "b": 60},
            "els": []}


def _svg_emit(doc, els):
    parts = ['<?xml version="1.0" encoding="UTF-8"?>']
    parts.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" font-family="sans-serif">'
                 % (doc["w"], doc["h"], doc["w"], doc["h"]))
    parts.append('<title>%s</title>' % _xml_escape(doc["title"]))
    parts.append('<rect x="0" y="0" width="%d" height="%d" fill="white"/>' % (doc["w"], doc["h"]))
    m = doc["margin"]
    parts.append('<text x="%d" y="24" font-size="16" font-weight="bold">%s</text>' % (m["l"], _xml_escape(doc["title"])))
    parts.append('<text x="%d" y="42" font-size="11" fill="#444">%s</text>' % (m["l"], _xml_escape(doc["subtitle"])))
    for e in els:
        parts.append(e)
    parts.append("</svg>")
    return "\n".join(parts)


def _xml_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def _plot_area(doc):
    m = doc["margin"]
    return (m["l"], m["t"], doc["w"] - m["r"], doc["h"] - m["b"])


def render_group_bar(stats):
    gc = stats.get("group_comparison") or {}
    targets = list(gc.keys())
    if not targets:
        return None
    # one figure with panels stacked vertically per target
    n_panels = len(targets)
    panel_h = 240
    W = 640
    H = 60 + n_panels * panel_h
    doc = _svg_doc(W, H, "Group means (bar, mean +/- SEM)", "source: stats.json; error bars = SEM")
    els = []
    y0 = doc["margin"]["t"]
    for t in targets:
        entry = gc[t]
        groups = entry["groups"]
        names = list(groups.keys())
        x0, yt, x1, yb = _plot_area(doc)
        pw = (x1 - x0)
        x0 = doc["margin"]["l"]
        pw = W - doc["margin"]["l"] - doc["margin"]["r"]
        yt = y0
        yb = y0 + panel_h - 40
        # y scale
        means = [g["mean"] for g in groups.values()]
        sems = [g["sem"] if g["sem"] is not None else 0 for g in groups.values()]
        hi = max(m + s for m, s in zip(means, sems))
        lo = min(m - s for m, s in zip(means, sems))
        pad = (hi - lo) * 0.15 or 1.0
        ymin, ymax = lo - pad, hi + pad
        def sy(v):
            return yb - (v - ymin) / (ymax - ymin) * (yb - yt)
        n = len(names)
        slot = pw / max(1, n)
        barw = slot * 0.5
        for i, gname in enumerate(names):
            cx = x0 + slot * i + slot / 2
            m = groups[gname]["mean"]
            s = groups[gname]["sem"] if groups[gname]["sem"] is not None else 0
            y_m = sy(m)
            y_top = sy(m + s)
            els.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="#4C78A8" stroke="#333"/>'
                       % (cx - barw / 2, y_m, barw, yb - y_m))
            els.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#333" stroke-width="1.5"/>'
                       % (cx, y_top, cx, sy(m - s)))
            els.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#333" stroke-width="1"/>'
                       % (cx - 4, y_top, cx + 4, y_top))
            els.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#333" stroke-width="1"/>'
                       % (cx - 4, sy(m - s), cx + 4, sy(m - s)))
            els.append('<text x="%.1f" y="%.1f" font-size="9" text-anchor="middle">%s</text>'
                       % (cx, yb + 14, _xml_escape(gname[:14])))
        els.append('<text x="%.1f" y="%.1f" font-size="12" font-weight="bold" text-anchor="middle">%s</text>'
                   % (W / 2, y0 + 14, _xml_escape(t)))
        # y ticks
        for i in range(5):
            v = ymin + (ymax - ymin) * i / 4
            els.append('<text x="%.1f" y="%.1f" font-size="9" text-anchor="end">%s</text>'
                       % (doc["margin"]["l"] - 6, sy(v) + 3, fmt_num(v, 3)))
            els.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#ddd"/>'
                       % (doc["margin"]["l"], sy(v), x1, sy(v)))
        y0 += panel_h
    return _svg_emit(doc, els)


def render_group_box(stats):
    gc = stats.get("group_comparison") or {}
    targets = list(gc.keys())
    if not targets:
        return None
    n_panels = len(targets)
    panel_h = 240
    W = 640
    H = 60 + n_panels * panel_h
    doc = _svg_doc(W, H, "Group box plots", "source: stats.json; box=q1-median-q3, whiskers=min-max")
    els = []
    y0 = doc["margin"]["t"]
    for t in targets:
        entry = gc[t]
        groups = entry["groups"]
        names = list(groups.keys())
        x0 = doc["margin"]["l"]
        pw = W - doc["margin"]["l"] - doc["margin"]["r"]
        yt = y0
        yb = y0 + panel_h - 40
        all_min = min(g["min"] for g in groups.values())
        all_max = max(g["max"] for g in groups.values())
        pad = (all_max - all_min) * 0.1 or 1.0
        ymin, ymax = all_min - pad, all_max + pad
        def sy(v):
            return yb - (v - ymin) / (ymax - ymin) * (yb - yt)
        n = len(names)
        slot = pw / max(1, n)
        bw = slot * 0.45
        for i, gname in enumerate(names):
            g = groups[gname]
            cx = x0 + slot * i + slot / 2
            y_q1, y_med, y_q3 = sy(g["q1"]), sy(g["median"]), sy(g["q3"])
            els.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="#DCE6F1" stroke="#333"/>'
                       % (cx - bw / 2, y_q3, bw, y_q1 - y_q3))
            els.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#333" stroke-width="1.5"/>'
                       % (cx - bw / 2, y_med, cx + bw / 2, y_med))
            els.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#333"/>'
                       % (cx, sy(g["min"]), cx, y_q1))
            els.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#333"/>'
                       % (cx, y_q3, cx, sy(g["max"])))
            els.append('<text x="%.1f" y="%.1f" font-size="9" text-anchor="middle">%s</text>'
                       % (cx, yb + 14, _xml_escape(gname[:14])))
        els.append('<text x="%.1f" y="%.1f" font-size="12" font-weight="bold" text-anchor="middle">%s</text>'
                   % (W / 2, y0 + 14, _xml_escape(t)))
        for i in range(5):
            v = ymin + (ymax - ymin) * i / 4
            els.append('<text x="%.1f" y="%.1f" font-size="9" text-anchor="end">%s</text>'
                       % (doc["margin"]["l"] - 6, sy(v) + 3, fmt_num(v, 3)))
        y0 += panel_h
    return _svg_emit(doc, els)


def render_corr_scatter(stats):
    corr = stats.get("correlation") or {}
    pairs = list(corr.keys())
    if not pairs:
        return None
    n_panels = len(pairs)
    panel_h = 240
    W = 640
    H = 60 + n_panels * panel_h
    doc = _svg_doc(W, H, "Correlation (Pearson r)", "source: stats.json; line=least-squares fit")
    els = []
    y0 = doc["margin"]["t"]
    for key in pairs:
        c = corr[key]
        x0 = doc["margin"]["l"]
        pw = W - doc["margin"]["l"] - doc["margin"]["r"]
        yt = y0
        yb = y0 + panel_h - 40
        pts = c.get("points") or []
        if pts:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            xmin, xmax = min(xs), max(xs)
            ymin, ymax = min(ys), max(ys)
            xpad = (xmax - xmin) * 0.05 or 1.0
            ypad = (ymax - ymin) * 0.05 or 1.0
            xmin -= xpad
            xmax += xpad
            ymin -= ypad
            ymax += ypad
            def sx(v):
                return x0 + (v - xmin) / (xmax - xmin) * pw
            def sy(v):
                return yb - (v - ymin) / (ymax - ymin) * (yb - yt)
            els.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="#FBFBFB" stroke="#333"/>'
                       % (x0, yt, pw, yb - yt))
            # axis ticks (5 each)
            for i in range(5):
                xv = xmin + (xmax - xmin) * i / 4
                yv = ymin + (ymax - ymin) * i / 4
                els.append('<text x="%.1f" y="%.1f" font-size="9" text-anchor="middle">%s</text>'
                           % (sx(xv), yb + 12, fmt_num(xv, 3)))
                els.append('<text x="%.1f" y="%.1f" font-size="9" text-anchor="end">%s</text>'
                           % (x0 - 6, sy(yv) + 3, fmt_num(yv, 3)))
            for px, py in pts:
                els.append('<circle cx="%.1f" cy="%.1f" r="2.2" fill="#4C78A8" fill-opacity="0.55"/>'
                           % (sx(px), sy(py)))
            slope, intercept = c.get("slope"), c.get("intercept")
            if slope is not None and intercept is not None:
                y_left = slope * xmin + intercept
                y_right = slope * xmax + intercept
                els.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#B03A2E" stroke-width="1.5"/>'
                           % (x0, sy(y_left), x0 + pw, sy(y_right)))
        else:
            els.append('<text x="%.1f" y="%.1f" font-size="9" fill="#555">no points persisted for this pair</text>'
                       % (x0, (yt + yb) / 2))
        r = c.get("statistic")
        p = c.get("p")
        label = "%s~%s: r=%s" % (_xml_escape(c.get("x", "")), _xml_escape(c.get("y", "")),
                                 fmt_num(r, 4) if r is not None else "na")
        if p is not None:
            label += ", p=" + fmt_num(p, 3)
        els.append('<text x="%.1f" y="%.1f" font-size="12" font-weight="bold" text-anchor="middle">%s</text>'
                   % (W / 2, y0 + 14, label))
        els.append('<text x="%.1f" y="%.1f" font-size="9" text-anchor="middle" fill="#555">n=%s points plotted</text>'
                   % (W / 2, yb + 26, fmt_num(len(pts))))
        y0 += panel_h
    return _svg_emit(doc, els)


def _report_md(stats, clean_meta):
    lines = []
    lines.append("# 科研数据分析报告")
    lines.append("")
    lines.append("> 本报告所有数字均由 `work/stats.json` 与 `work/clean_meta.json` 回填；重跑 `render` 可字节一致复现。")
    lines.append("")
    lines.append("## 1. 数据概览")
    lines.append("")
    lines.append("- case_id: %s" % (stats.get("case_id") or "unknown"))
    lines.append("- 有效行数 n: %s" % fmt_num(stats.get("n_total")))
    lines.append("- 分组变量: %s" % (stats.get("group") or "未指定"))
    lines.append("- 目标变量: %s" % (", ".join(stats.get("targets", [])) or "无"))
    if clean_meta:
        impacts = clean_meta.get("row_impacts", [])
        lines.append("- 清洗轨迹: " + " -> ".join("%s=%s" % (i["stage"], i["rows"]) for i in impacts))
        lines.append("- 异常值处理: 仅标记不删除（规则见 `work/clean_meta.json` 的 `outliers`）")
    lines.append("")
    lines.append("## 2. 描述统计")
    lines.append("")
    lines.append("| 变量 | n | 均值 | 标准差 | 中位数 | Q1 | Q3 |")
    lines.append("|---|---|---|---|---|---|---|")
    for t in stats.get("targets", []):
        d = stats.get("descriptive", {}).get(t)
        if not d:
            continue
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            t, fmt_num(d.get("n")), fmt_num(d.get("mean")), fmt_num(d.get("sd")),
            fmt_num(d.get("median")), fmt_num(d.get("q1")), fmt_num(d.get("q3"))))
    lines.append("")
    lines.append("## 3. 组间比较")
    lines.append("")
    gc = stats.get("group_comparison", {})
    if not gc:
        lines.append("未执行组间比较（无分组变量或样本不足）。")
    for t, entry in gc.items():
        lines.append("### %s" % t)
        lines.append("")
        lines.append("| 组 | n | 均值 | 标准差 | 中位数 |")
        lines.append("|---|---|---|---|---|")
        for g, s in entry["groups"].items():
            lines.append("| %s | %s | %s | %s | %s |" % (
                g, fmt_num(s["n"]), fmt_num(s["mean"]), fmt_num(s["sd"]), fmt_num(s["median"])))
        tp = entry.get("test_parametric", {})
        tn = entry.get("test_nonparametric", {})
        es = entry.get("effect_size", {})
        lines.append("")
        lines.append("- 参数检验 %s: 统计量=%s, p=%s" % (tp.get("name"), fmt_num(tp.get("statistic")), fmt_num(tp.get("p"))))
        lines.append("- 非参数检验 %s: 统计量=%s, p=%s" % (tn.get("name"), fmt_num(tn.get("statistic")), fmt_num(tn.get("p"))))
        lines.append("- 效应量 %s: %s" % (es.get("name"), fmt_num(es.get("value"))))
        lines.append("- 前提说明: " + "; ".join(entry.get("assumptions", [])))
        lines.append("")
    lines.append("## 4. 相关分析")
    lines.append("")
    corr = stats.get("correlation", {})
    if not corr:
        lines.append("未执行相关分析。")
    for key, c in corr.items():
        lines.append("- %s: r=%s (n=%s, p=%s, Pearson)" % (key, fmt_num(c.get("statistic")),
                                                           fmt_num(c.get("n")), fmt_num(c.get("p"))))
    lines.append("")
    lines.append("## 5. 质量与不确定性")
    lines.append("")
    for d in stats.get("degradations", []):
        lines.append("- 降级: %s" % d)
    for e in stats.get("errors", []):
        lines.append("- 错误: %s" % e)
    if not stats.get("degradations") and not stats.get("errors"):
        lines.append("- 无明显降级或错误。")
    lines.append("")
    lines.append("本报告由 Skill `sci-tabular-analysis` v%s 生成，可复核、可重跑。" % SCHEMA_VERSION)
    return "\n".join(lines) + "\n"


def cmd_render(args):
    input_dir = args.get("input", "/input")
    output_dir = args.get("output", "/app/output")
    work = args.get("work", "/app/work")
    os.makedirs(output_dir, exist_ok=True)
    stats = read_json(os.path.join(work, "stats.json")) or {}
    clean_meta = read_json(os.path.join(work, "clean_meta.json"))
    figures = []
    chart = args.get("chart", "box,bar,scatter")

    svg_bar = render_group_bar(stats)
    svg_box = render_group_box(stats)
    svg_scatter = render_corr_scatter(stats)
    if svg_box and ("box" in chart):
        write_text(os.path.join(output_dir, "figure_group_box.svg"), svg_box)
        figures.append("figure_group_box.svg")
    if svg_bar and ("bar" in chart):
        write_text(os.path.join(output_dir, "figure_group_bar.svg"), svg_bar)
        figures.append("figure_group_bar.svg")
    if svg_scatter and ("scatter" in chart):
        write_text(os.path.join(output_dir, "figure_corr_scatter.svg"), svg_scatter)
        figures.append("figure_corr_scatter.svg")

    report = _report_md(stats, clean_meta)
    write_text(os.path.join(output_dir, "report.md"), report)
    touch_clock(work, "render")
    print(json.dumps({"command": "render", "figures": figures, "report": "report.md"}, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------
# finalize and evidence
# --------------------------------------------------------------------------
def _quote_line(text, needle, maxlen=200):
    for line in text.splitlines():
        if needle in line:
            return line[:maxlen]
    return text[:maxlen]


def _quote_file(path, maxlen=200):
    if not os.path.isfile(path):
        return ""
    if path.lower().endswith((".csv", ".tsv", ".tab", ".txt", ".jsonl", ".ndjson")):
        t = read_text(path)
        return t.splitlines()[0][:maxlen] if t.splitlines() else ""
    if path.lower().endswith(".json"):
        t = read_text(path)
        return t[:maxlen]
    if path.lower().endswith((".sqlite", ".sqlite3", ".db")):
        try:
            con = sqlite3.connect(path)
            cur = con.cursor()
            cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 1")
            row = cur.fetchone()
            con.close()
            return (row[0] if row else "")[:maxlen]
        except Exception:
            return ""
    return ""


def cmd_finalize(args):
    input_dir = args.get("input", "/input")
    output_dir = args.get("output", "/app/output")
    work = args.get("work", "/app/work")
    os.makedirs(output_dir, exist_ok=True)

    result = read_json(os.path.join(output_dir, "result.json")) or {}
    case = read_json(os.path.join(input_dir, "case.json"))
    profile = read_json(os.path.join(work, "profile.json")) or {}
    stats = read_json(os.path.join(work, "stats.json")) or {}
    clean_meta = read_json(os.path.join(work, "clean_meta.json")) or {}

    contract_fields, raw_contract = extract_contract(input_dir, case) if isinstance(case, dict) else (list(DEFAULT_CONTRACT), None)
    # preserve skeleton keys; reinitialize contract fields only if absent
    if raw_contract is not None:
        result["output_contract_echo"] = raw_contract
    for f in contract_fields:
        result.setdefault(f, None)

    # build analysis_results (default contract field)
    analysis = {"n_total": stats.get("n_total"), "group": stats.get("group"),
                "targets": stats.get("targets", []),
                "descriptive": stats.get("descriptive", {}),
                "group_comparison": stats.get("group_comparison", {}),
                "correlation": stats.get("correlation", {})}
    quality = {"cleaning": clean_meta.get("rules", []), "row_impacts": clean_meta.get("row_impacts", []),
               "outliers_flagged_only": clean_meta.get("outliers", {}),
               "source_paths": [f.get("path") for f in profile.get("files", [])],
               "reproducibility": "scripts are stdlib-only and deterministic; rerun produces byte-identical stats.json/SVG"}
    anomalies = {"errors": stats.get("errors", []) + clean_meta.get("errors", []) + profile.get("errors", []),
                 "degradations": stats.get("degradations", []) + clean_meta.get("degradations", []) + profile.get("degradations", []),
                 "uncertainty": result.get("uncertainty", [])}

    # backfill the default contract keys when present among contract fields
    if "analysis_results" in contract_fields:
        result["analysis_results"] = analysis
    if "quality_evidence" in contract_fields:
        result["quality_evidence"] = quality
    if "anomaly_notes" in contract_fields:
        result["anomaly_notes"] = anomalies

    # evidence sourcing: locator + verbatim quote
    sources = list(result.get("sources", []))
    for f in profile.get("files", []):
        loc = "%s#table=%s" % (f.get("path"), f.get("table", "")) if f.get("kind") == "sqlite" else f.get("path")
        q = _quote_file(f.get("path"))
        if q:
            sources.append({"locator": loc, "quote": q, "provenance": "input file header/schema (verbatim)"})
    tpath = os.path.join(input_dir, "task.md")
    if os.path.isfile(tpath):
        t = read_text(tpath)
        sources.append({"locator": tpath, "quote": t[:200], "provenance": "task requirement (verbatim excerpt)"})
    # de-dup sources by locator
    uniq = {}
    for s in sources:
        uniq.setdefault(s["locator"], s)
    result["sources"] = list(uniq.values())

    # status
    has_data = bool(profile.get("files")) and bool(stats.get("n_total"))
    has_errors = bool(stats.get("errors") or profile.get("errors"))
    if not isinstance(case, dict):
        result["status"] = "input_error"
    elif not profile.get("files"):
        result["status"] = "insufficient_evidence"
    elif has_errors:
        result["status"] = "partial"
    elif has_data:
        result["status"] = args.get("status") if args.get("status") in STATUS_VALUES else "ok"
    else:
        result["status"] = "partial"

    result["case_id"] = extract_case_id(input_dir, case) if isinstance(case, dict) else result.get("case_id", os.path.basename(os.path.abspath(input_dir)))
    artifacts = []
    for f in sorted(os.listdir(output_dir)):
        if f == "report.md" or f.startswith("figure_") or f.endswith(".svg") or f.endswith(".csv") or f.endswith(".png") or f.endswith(".pdf"):
            artifacts.append({"path": os.path.join(output_dir, f), "name": f})
    result["artifacts"] = artifacts
    result["runtime"] = {"deadline_s": result.get("runtime", {}).get("deadline_s", 900),
                         "requests_estimate": (result.get("runtime", {}).get("requests_estimate", 0) or 0) + 1,
                         "finalized_at": now_iso()}
    result.setdefault("uncertainty", [])
    result.setdefault("pending_human", [])
    result.setdefault("errors", [])
    result.setdefault("degradations", [])
    result.setdefault("change_log", []).append({"t": now_iso(), "phase": "finalize",
                                                "action": "backfilled output_contract fields and set status=%s" % result["status"]})
    write_json(os.path.join(output_dir, "result.json"), result)
    touch_clock(work, "finalize")
    print(json.dumps({"command": "finalize", "status": result["status"], "case_id": result["case_id"],
                      "sources": len(result["sources"])}, ensure_ascii=False))
    return 0


def cmd_clock(args):
    work = args.get("work", "/app/work")
    clock = load_clock(work)
    elapsed = now_epoch() - clock["t0"]
    remaining = clock.get("deadline_s", 900) - elapsed
    last = clock["history"][-1] if clock["history"] else {}
    print(json.dumps({"deadline_s": clock.get("deadline_s", 900),
                      "elapsed_s": round(elapsed, 1), "remaining_s": round(remaining, 1),
                      "last_phase": last.get("phase"), "recommendation": last.get("recommendation", "on schedule")},
                     ensure_ascii=False))
    return 0


def cmd_selftest(args):
    import subprocess
    selftest_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selftest.py")
    return subprocess.call([sys.executable, "-B", selftest_path])


COMMANDS = {
    "scaffold": cmd_scaffold,
    "profile": cmd_profile,
    "clean": cmd_clean,
    "analyze": cmd_analyze,
    "render": cmd_render,
    "finalize": cmd_finalize,
    "clock": cmd_clock,
    "selftest": cmd_selftest,
}


def main(argv):
    if not argv:
        print("usage: scripts/main.py <command> [--input DIR] [--output DIR] [--work DIR]")
        return 1
    cmd = argv[0]
    args = parse_args(argv[1:], {})
    if cmd in COMMANDS:
        try:
            return COMMANDS[cmd](args)
        except Exception as exc:  # never leave an empty output: record and exit 0
            output_dir = args.get("output", "/app/output")
            work = args.get("work", "/app/work")
            try:
                os.makedirs(output_dir, exist_ok=True)
                os.makedirs(work, exist_ok=True)
                result = read_json(os.path.join(output_dir, "result.json")) or {}
                result.setdefault("errors", []).append("command %s failed: %s" % (cmd, exc))
                result.setdefault("status", "partial")
                write_json(os.path.join(output_dir, "result.json"), result)
            except Exception:
                pass
            print("command %s failed (exit 0, recorded): %s" % (cmd, exc), file=sys.stderr)
            return 0
    print("unknown command: %s" % cmd, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
