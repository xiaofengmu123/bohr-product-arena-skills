"""core_impl.py — pure-standard-library implementation of the data analysis skill.

Design goals (robustness variant):
  * never crash: empty input, broken JSON, missing columns, wrong encodings,
    oversized files and division-by-zero all degrade to a valid result.json;
  * deterministic: same input, byte-identical work/stats.json and SVG output;
  * auditable: every key number carries a locator + verbatim quote in sources.

Only the Python standard library is required.  Optional scientific packages
(numpy/pandas/scipy/matplotlib) are never imported, so the core deliverables
cannot change behaviour between environments.
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
import traceback

VERSION = "1.1.0"

# ---------------------------------------------------------------------------
# Limits / constants
# ---------------------------------------------------------------------------
TEXT_SIZE_CAP = 2 * 1024 * 1024          # per CSV/TSV/JSON text file (bytes)
SQLITE_SIZE_CAP = 200 * 1024 * 1024      # per SQLite database (bytes)
MAX_ROWS = 100000                        # rows fully analysed; larger -> deterministic sample
MAX_GROUP_LEVELS = 20                    # more unique values -> not a grouping column
MAX_TARGET_COLS = 20                     # cap for numeric target columns
MAX_FIGURES = 8                          # cap on rendered SVG figures
ENCODINGS = ("utf-8-sig", "utf-8", "latin-1", "cp1252", "gbk", "utf-16")
DEFAULT_CONTRACT_KEYS = ("analysis_results", "quality_evidence", "anomaly_notes")
DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

DATA_EXT = {".csv", ".tsv", ".tab", ".txt", ".json", ".jsonl"}
SQL_EXT = {".sqlite", ".sqlite3", ".db", ".db3"}
META_NAMES = {"case.json", "task.md", "challenge.json"}


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------
def _clean_float(x):
    """Collapse floating-point noise without destroying small p-values."""
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return None
        return float(format(x, ".10g"))
    return x


def _sanitize(obj):
    """Recursively make a value JSON-safe and deterministic-looking."""
    if isinstance(obj, dict):
        return {str(k): _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return _clean_float(obj)


def _read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def _read_text_any(path, max_size=TEXT_SIZE_CAP):
    """Read a text file trying several encodings; returns (text, encoding) or (None, reason)."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, "stat failed: %s" % exc
    if size > max_size:
        return None, "oversized (%d bytes > cap %d)" % (size, max_size)
    raw = _read_bytes(path)
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    return None, "undecodable bytes (tried %s)" % ", ".join(ENCODINGS)


def _json_loads(text):
    """Try to parse JSON from text with several common cleanup passes."""
    text = text.lstrip("\ufeff").strip()
    if not text:
        raise ValueError("empty document")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # tolerate a trailing comma / trailing content after the top-level value
    for pat in (r",\s*([}\]])",):
        try:
            return json.loads(re.sub(pat, r"\1", text))
        except json.JSONDecodeError:
            continue
    raise ValueError("invalid JSON")


def _round(x, nd=6):
    try:
        return round(float(x), nd)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Mathematical special functions (stdlib only)
# ---------------------------------------------------------------------------
_G = 7
_LANCZOS = (
    0.99999999999980993,
    676.5203681218851,
    -1259.1392167224028,
    771.32342877765313,
    -176.61502916214059,
    12.507343278686905,
    -0.13857109526572012,
    9.9843695780195716e-6,
    1.5056327351493116e-7,
)


def _lgamma(x):
    """Natural log of Gamma(x), Lanczos approximation."""
    if x < 0.5:
        return math.log(math.pi) - math.log(math.sin(math.pi * x)) - _lgamma(1.0 - x)
    x -= 1.0
    a = _LANCZOS[0]
    for i in range(1, _G + 2):
        a += _LANCZOS[i] / (x + i)
    t = x + _G + 0.5
    return 0.5 * math.log(2.0 * math.pi) + (x + 0.5) * math.log(t) - t + math.log(a)


def _betacf(a, b, x):
    EPS = 3e-12
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
    for m in range(1, 200):
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
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = _lgamma(a + b) - _lgamma(a) - _lgamma(b) + a * math.log(x) + b * math.log(1.0 - x)
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(lbeta) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbeta) * _betacf(b, a, 1.0 - x) / b


def _gammainc_p(a, x):
    """Regularized lower incomplete gamma P(a, x), used for chi-square CDF."""
    if x < 0:
        return 0.0
    if x == 0.0:
        return 0.0
    if a <= 0:
        return 1.0
    if x < a + 1.0:
        ap = a
        s = 1.0 / a
        d = s
        for _ in range(300):
            ap += 1.0
            d *= x / ap
            s += d
            if abs(d) < abs(s) * 1e-15:
                break
        return s * math.exp(-x + a * math.log(x) - _lgamma(a))
    FPMIN = 1e-300
    b = x + 1.0 - a
    c = 1.0 / FPMIN
    d = 1.0 / b if b != 0 else FPMIN
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
        if abs(delta - 1.0) < 1e-15:
            break
    q = math.exp(-x + a * math.log(x) - _lgamma(a)) * h
    return 1.0 - q


def _normal_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _normal_ppf(p):
    """Inverse normal CDF (Acklam's rational approximation + one Halley step)."""
    if p <= 0.0:
        return -float("inf")
    if p >= 1.0:
        return float("inf")
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


def _t_cdf(t, df):
    if df <= 0:
        return float("nan")
    x = df / (df + t * t)
    if t > 0:
        return 1.0 - 0.5 * _betai(df / 2.0, 0.5, x)
    return 0.5 * _betai(df / 2.0, 0.5, x)


def _t_ppf(p, df):
    if p <= 0.0 or p >= 1.0:
        return float("nan")
    lo, hi = -1e6, 1e6
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if _t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _f_cdf(f, d1, d2):
    if f <= 0:
        return 0.0
    x = d1 * f / (d1 * f + d2)
    return _betai(d1 / 2.0, d2 / 2.0, x)


def _chi2_cdf(x, df):
    if x <= 0:
        return 0.0
    return _gammainc_p(df / 2.0, x / 2.0)


# ---------------------------------------------------------------------------
# Basic statistics
# ---------------------------------------------------------------------------
def _mean(v):
    return sum(v) / len(v) if v else float("nan")


def _variance(v):
    n = len(v)
    if n < 2:
        return float("nan")
    m = _mean(v)
    return sum((x - m) ** 2 for x in v) / (n - 1)


def _std(v):
    var = _variance(v)
    return math.sqrt(var) if not math.isnan(var) and var >= 0 else float("nan")


def _quantile_sorted(s, q):
    if not s:
        return float("nan")
    pos = q * (len(s) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return s[lo]
    frac = pos - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def _median(v):
    return _quantile_sorted(sorted(v), 0.5)


def _skewness(v):
    n = len(v)
    if n < 3:
        return float("nan")
    m = _mean(v)
    sd = _std(v)
    if sd == 0 or math.isnan(sd):
        return 0.0
    return sum(((x - m) / sd) ** 3 for x in v) * n / ((n - 1) * (n - 2))


def _rankdata_ties(values):
    """Average ranks with tie handling."""
    idx = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    n = len(idx)
    while i < n:
        j = i
        while j + 1 < n and values[idx[j + 1]] == values[idx[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[idx[k]] = avg
        i = j + 1
    return ranks


def _tie_correction(values):
    """sum(t^3 - t) for tied groups."""
    from collections import Counter
    counts = Counter(values)
    return sum(c ** 3 - c for c in counts.values() if c > 1)


# ---------------------------------------------------------------------------
# Hypothesis tests
# ---------------------------------------------------------------------------
def welch_t(x, y):
    nx, ny = len(x), len(y)
    if nx < 2 or ny < 2:
        return {"status": "skipped", "reason": "group too small"}
    mx, my = _mean(x), _mean(y)
    vx, vy = _variance(x), _variance(y)
    if math.isnan(vx) or math.isnan(vy):
        return {"status": "skipped", "reason": "zero variance / constant group"}
    se = math.sqrt(vx / nx + vy / ny)
    if se == 0:
        return {"status": "no_difference", "t": 0.0, "df": float("nan"), "p": 1.0}
    t = (mx - my) / se
    num = (vx / nx + vy / ny) ** 2
    den = (vx / nx) ** 2 / (nx - 1) + (vy / ny) ** 2 / (ny - 1)
    df = num / den if den > 0 else float("nan")
    p = 2.0 * (1.0 - _t_cdf(abs(t), df)) if not math.isnan(df) else float("nan")
    return {"status": "ok", "t": t, "df": df, "p": p}


def cohens_d(x, y):
    nx, ny = len(x), len(y)
    if nx < 2 or ny < 2:
        return None
    vx, vy = _variance(x), _variance(y)
    sp = math.sqrt(((nx - 1) * vx + (ny - 1) * vy) / (nx + ny - 2))
    if sp == 0 or math.isnan(sp):
        return None
    return (_mean(x) - _mean(y)) / sp


def mann_whitney(x, y):
    nx, ny = len(x), len(y)
    if nx < 1 or ny < 1:
        return {"status": "skipped", "reason": "empty group"}
    n = nx + ny
    ranks = _rankdata_ties(list(x) + list(y))
    r1 = sum(ranks[:nx])
    u1 = r1 - nx * (nx + 1) / 2.0
    u2 = nx * ny - u1
    u = min(u1, u2)
    mu = nx * ny / 2.0
    tie = _tie_correction(list(x) + list(y))
    var_u = nx * ny / 12.0 * ((n + 1) - tie / (n * (n - 1))) if n > 1 else 1.0
    if var_u <= 0:
        return {"status": "no_difference", "U": u, "p": 1.0}
    z = (u - mu + 0.5) / math.sqrt(var_u)
    p = 2.0 * (1.0 - _normal_cdf(abs(z)))
    return {"status": "ok", "U": u, "z": z, "p": p}


def anova_f(groups):
    k = len([g for g in groups if g])
    groups = [g for g in groups if g]
    if k < 2:
        return {"status": "skipped", "reason": "need >= 2 groups"}
    ns = [len(g) for g in groups]
    N = sum(ns)
    means = [_mean(g) for g in groups]
    grand = sum(sum(g) for g in groups) / N if N else float("nan")
    ssb = sum(ns[i] * (means[i] - grand) ** 2 for i in range(k))
    ssw = sum(sum((x - means[i]) ** 2 for x in groups[i]) for i in range(k))
    dfb = k - 1
    dfw = N - k
    if dfw <= 0:
        return {"status": "skipped", "reason": "no within-group degrees of freedom"}
    if ssw == 0 and ssb == 0:
        return {"status": "no_difference", "F": 0.0, "p": 1.0, "df_between": dfb, "df_within": dfw}
    if ssw == 0:
        return {"status": "ok", "F": float("inf"), "df_between": dfb, "df_within": dfw,
                "p": 0.0, "eta_sq": 1.0}
    msb = ssb / dfb
    msw = ssw / dfw
    F = msb / msw
    p = 1.0 - _f_cdf(F, dfb, dfw)
    eta2 = ssb / (ssb + ssw)
    return {"status": "ok", "F": F, "df_between": dfb, "df_within": dfw, "p": p, "eta_sq": eta2}


def kruskal_wallis(groups):
    groups = [g for g in groups if g]
    k = len(groups)
    if k < 2:
        return {"status": "skipped", "reason": "need >= 2 groups"}
    ns = [len(g) for g in groups]
    N = sum(ns)
    flat = [v for g in groups for v in g]
    ranks = _rankdata_ties(flat)
    offset = 0
    rsums = []
    for g in groups:
        rsums.append(sum(ranks[offset:offset + len(g)]))
        offset += len(g)
    H = 12.0 / (N * (N + 1)) * sum(rsums[i] ** 2 / ns[i] for i in range(k)) - 3.0 * (N + 1)
    tie = _tie_correction(flat)
    C = 1.0 - tie / (N ** 3 - N) if N > 1 else 1.0
    Hc = H / C if C > 0 else H
    p = 1.0 - _chi2_cdf(Hc, k - 1)
    return {"status": "ok", "H": Hc, "df": k - 1, "p": p}


def levene(groups, center="median"):
    groups = [g for g in groups if g]
    if len(groups) < 2:
        return {"status": "skipped", "reason": "need >= 2 groups"}
    zs = []
    for g in groups:
        c = _median(g) if center == "median" else _mean(g)
        zs.append([abs(x - c) for x in g])
    return anova_f(zs)


def pearson_r(x, y):
    n = len(x)
    if n < 3:
        return {"status": "skipped", "reason": "too few pairs"}
    mx, my = _mean(x), _mean(y)
    sdx, sdy = _std(x), _std(y)
    if sdx == 0 or sdy == 0 or math.isnan(sdx) or math.isnan(sdy):
        return {"status": "skipped", "reason": "constant column"}
    cov = sum((x[i] - mx) * (y[i] - my) for i in range(n)) / (n - 1)
    r = cov / (sdx * sdy)
    r = max(-1.0, min(1.0, r))
    if abs(r) >= 1.0:
        return {"status": "ok", "r": r, "p": 0.0, "n": n}
    t = r * math.sqrt((n - 2) / (1 - r * r))
    p = 2.0 * (1.0 - _t_cdf(abs(t), n - 2))
    return {"status": "ok", "r": r, "p": p, "n": n}


# ---------------------------------------------------------------------------
# Data ingestion
# ---------------------------------------------------------------------------
def _is_int(s):
    try:
        int(s)
        return True
    except (TypeError, ValueError):
        return False


def _is_float(s):
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def _sniff_delimiter(text):
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        return dialect.delimiter
    except csv.Error:
        pass
    counts = {",": 0, "\t": 0, ";": 0, "|": 0}
    for line in sample.splitlines()[:10]:
        if not line.strip():
            continue
        for ch in counts:
            counts[ch] += line.count(ch)
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def _parse_csv_text(text, delimiter):
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return None, None
    header = [c.strip() for c in rows[0]]
    body = rows[1:]
    return header, body


def _normalize_records(data):
    """Coerce parsed JSON into (columns, rows) list-of-lists form."""
    if isinstance(data, list):
        if not data:
            return [], []
        if all(isinstance(item, dict) for item in data):
            cols = []
            for item in data:
                for k in item:
                    if k not in cols:
                        cols.append(k)
            rows = [[item.get(c, "") for c in cols] for item in data]
            return cols, rows
        if all(isinstance(item, (list, tuple)) for item in data):
            return [str(i) for i in range(len(data[0]))], [[str(v) for v in item] for item in data]
    if isinstance(data, dict):
        if all(isinstance(v, list) for v in data.values()):
            cols = list(data.keys())
            n = min(len(v) for v in data.values())
            rows = [[data[c][i] for c in cols] for i in range(n)]
            return cols, rows
        if all(isinstance(v, dict) for v in data.values()):
            cols = list(data.keys())
            rows = []
            for key in cols:
                inner = data[key]
                for rk, rv in inner.items():
                    rows.append([key, rk, rv])
            return ["key", "sub_key", "value"], rows
    return [], []


def _load_sqlite_tables(path, max_size=SQLITE_SIZE_CAP):
    out = {}
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, "stat failed: %s" % exc
    if size > max_size:
        return None, "oversized (%d bytes > cap %d)" % (size, max_size)
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
    except sqlite3.Error:
        try:
            conn = sqlite3.connect(path)
        except sqlite3.Error as exc:
            return None, "sqlite open failed: %s" % exc
    try:
        cur = conn.cursor()
        try:
            tables = [r[0] for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
        except sqlite3.Error as exc:
            return None, "sqlite_master query failed: %s" % exc
        for t in tables:
            try:
                pragma = cur.execute("PRAGMA table_info(%s)" % t).fetchall()
                cols = [r[1] for r in pragma]
                rows = [list(r) for r in cur.execute("SELECT * FROM %s" % t).fetchall()]
                out[t] = {"columns": cols, "rows": rows}
            except sqlite3.Error as exc:
                out[t] = {"columns": [], "rows": [], "error": str(exc)}
        return out, None
    finally:
        conn.close()


def _cell_to_value(cell):
    s = str(cell).strip()
    if s == "":
        return None
    if _is_int(s):
        try:
            return int(s)
        except ValueError:
            pass
    if _is_float(s):
        try:
            return float(s)
        except ValueError:
            pass
    return s


def _infer_column_type(values):
    """values: list of raw cells; return 'numeric', 'integer', 'categorical', 'empty'."""
    present = [v for v in values if v is not None and str(v).strip() != ""]
    if not present:
        return "empty", 0
    non_null = len(present)
    n_numeric = sum(1 for v in present if _is_float(str(v)))
    if n_numeric == non_null:
        if all(_is_int(str(v)) for v in present):
            return "integer", non_null
        return "numeric", non_null
    return "categorical", non_null


def _discover_files(input_dir):
    """Return (meta_files, data_files) sorted deterministically."""
    meta, data = [], []
    if not os.path.isdir(input_dir):
        return meta, data
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(root, name)
            if name.startswith("."):
                continue
            if name.lower() in META_NAMES:
                meta.append(full)
            else:
                ext = os.path.splitext(name)[1].lower()
                if ext in DATA_EXT or ext in SQL_EXT:
                    data.append(full)
    return meta, data


# ---------------------------------------------------------------------------
# State persistence (work/state.json)
# ---------------------------------------------------------------------------
class State:
    def __init__(self, work_dir):
        self.work_dir = work_dir
        self.path = os.path.join(work_dir, "state.json")
        self.d = {}

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    self.d = json.load(fh)
            except (OSError, ValueError):
                self.d = {}
        return self

    def save(self):
        os.makedirs(self.work_dir, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self.d, fh, ensure_ascii=False, indent=2, sort_keys=True)

    def get(self, key, default=None):
        return self.d.get(key, default)

    def set(self, key, value):
        self.d[key] = value
        return self

    def log(self, step, detail):
        entry = {"step": step, "time": int(time.time()), "detail": detail}
        self.d.setdefault("change_log", []).append(entry)
        return self

    def note_error(self, msg):
        if msg not in self.d.setdefault("errors", []):
            self.d["errors"].append(msg)

    def note_degradation(self, msg):
        if msg not in self.d.setdefault("degradations", []):
            self.d["degradations"].append(msg)

    def note_uncertainty(self, msg):
        if msg not in self.d.setdefault("uncertainty", []):
            self.d["uncertainty"].append(msg)

    def note_pending(self, msg):
        if msg not in self.d.setdefault("pending_human", []):
            self.d["pending_human"].append(msg)


# ---------------------------------------------------------------------------
# Contract handling
# ---------------------------------------------------------------------------
def _read_case(input_dir, state):
    """Extract case_id and output_contract from /input/case.json (or a contract file)."""
    case_path = None
    for cand in ("case.json", "case.json"):
        p = os.path.join(input_dir, cand)
        if os.path.isfile(p):
            case_path = p
            break
    meta_files, _ = _discover_files(input_dir)
    if case_path is None:
        for m in meta_files:
            if os.path.basename(m).lower() == "case.json":
                case_path = m
                break

    case_id = None
    contract = None
    contract_source = "default"

    if case_path is not None:
        text, enc = _read_text_any(case_path)
        if text is None:
            state.note_error("case.json unreadable: %s" % enc)
        else:
            try:
                case = _json_loads(text)
                if isinstance(case, dict):
                    for k in ("case_id", "challenge_id", "armiId", "id"):
                        if case.get(k):
                            case_id = str(case[k])
                            break
                    if isinstance(case.get("output_contract"), dict):
                        contract = case["output_contract"]
                        contract_source = "case.json#output_contract"
            except ValueError as exc:
                state.note_error("case.json is not valid JSON: %s" % exc)

    # fall back to a standalone output-contract document if present
    if contract is None:
        for m in meta_files:
            base = os.path.basename(m).lower()
            if base.startswith("output-contract") or base.startswith("output_contract"):
                text, enc = _read_text_any(m)
                if text is None:
                    continue
                try:
                    parsed = _json_loads(text)
                    if isinstance(parsed, dict):
                        contract = parsed.get("output_contract", parsed)
                        contract_source = os.path.basename(m)
                        break
                except ValueError:
                    state.note_error("output-contract document invalid JSON: %s" % m)

    if contract is not None and not isinstance(contract, dict):
        contract = None

    if case_id is None:
        case_id = os.path.basename(os.path.abspath(input_dir).rstrip("/\\")) or "unknown_case"
        state.note_uncertainty("case_id not found in case.json; using input directory name")

    state.set("case_id", case_id)
    state.set("contract", contract if contract is not None else {})
    state.set("contract_source", contract_source)
    return case_id, contract


# ---------------------------------------------------------------------------
# Profiling
# ---------------------------------------------------------------------------
def _profile(input_dir, work_dir, state, override_primary=None):
    meta, data = _discover_files(input_dir)
    sources = []
    for path in data:
        ext = os.path.splitext(path)[1].lower()
        rel = os.path.relpath(path, input_dir).replace("\\", "/")
        if ext in SQL_EXT:
            tables, err = _load_sqlite_tables(path)
            if err:
                state.note_degradation("skip sqlite %s: %s" % (rel, err))
                continue
            for tname, tinfo in (tables or {}).items():
                cols, rows = tinfo.get("columns", []), tinfo.get("rows", [])
                sources.append({
                    "locator": "%s::table:%s" % (rel, tname),
                    "kind": "sqlite",
                    "file": rel,
                    "table": tname,
                    "columns": cols,
                    "n_rows": len(rows),
                    "n_cols": len(cols),
                })
        elif ext in DATA_EXT:
            text, enc = _read_text_any(path)
            if text is None:
                state.note_degradation("skip text file %s: %s" % (rel, enc))
                continue
            if ext == ".json" or ext == ".jsonl":
                try:
                    cols, rows = _normalize_records(_json_loads(text))
                except ValueError as exc:
                    state.note_error("skip invalid JSON %s: %s" % (rel, exc))
                    continue
            else:
                delim = "\t" if ext in (".tsv", ".tab") else _sniff_delimiter(text)
                cols, rows = _parse_csv_text(text, delim)
                if cols is None:
                    state.note_degradation("empty tabular file %s" % rel)
                    continue
            sources.append({
                "locator": rel,
                "kind": "table",
                "file": rel,
                "table": None,
                "columns": cols,
                "n_rows": len(rows),
                "n_cols": len(cols),
            })

    if not sources:
        state.set("has_data", False)
        state.set("sources", [])
        profile = {"sources": [], "primary": None, "has_data": False}
        _write_json(os.path.join(work_dir, "profile.json"), profile)
        return profile

    def key(s):
        return (s["n_rows"], s["n_cols"])
    sources.sort(key=lambda s: (-s["n_rows"], -s["n_cols"], s["locator"]))
    primary = None
    if override_primary:
        for s in sources:
            if s["file"] == override_primary or s["table"] == override_primary or s["locator"] == override_primary:
                primary = s
                break
    if primary is None:
        primary = sources[0]

    state.set("has_data", True)
    state.set("sources", [s["locator"] for s in sources])
    state.set("primary", primary["locator"])
    profile = {
        "has_data": True,
        "sources": sources,
        "primary": primary["locator"],
        "primary_kind": primary["kind"],
        "join_candidates": _join_candidates(sources),
    }
    _write_json(os.path.join(work_dir, "profile.json"), profile)
    return profile


def _join_candidates(sources):
    common = {}
    for s in sources:
        for c in s["columns"]:
            common.setdefault(c, []).append(s["locator"])
    return {c: locs for c, locs in common.items() if len(locs) > 1}


def _load_primary(input_dir, profile, state, max_rows=MAX_ROWS):
    """Load the primary source into (columns, rows, locator, kind)."""
    primary = profile.get("primary")
    src = None
    for s in profile.get("sources", []):
        if s["locator"] == primary:
            src = s
            break
    if src is None:
        return None, None, None, None

    locator = src["locator"]
    if src["kind"] == "sqlite":
        path = os.path.join(input_dir, src["file"])
        tables, err = _load_sqlite_tables(path)
        if err:
            state.note_error("reload sqlite failed: %s" % err)
            return None, None, None, None
        tinfo = tables.get(src["table"], {})
        cols, rows = tinfo.get("columns", []), tinfo.get("rows", [])
    else:
        path = os.path.join(input_dir, src["file"])
        text, enc = _read_text_any(path)
        if text is None:
            state.note_error("reload %s failed: %s" % (locator, enc))
            return None, None, None, None
        if src["file"].lower().endswith((".json", ".jsonl")):
            try:
                cols, rows = _normalize_records(_json_loads(text))
            except ValueError as exc:
                state.note_error("reload JSON failed: %s" % exc)
                return None, None, None, None
        else:
            delim = "\t" if src["file"].lower().endswith((".tsv", ".tab")) else _sniff_delimiter(text)
            cols, rows = _parse_csv_text(text, delim)

    if rows is None:
        return None, None, None, None
    n = len(rows)
    if n > max_rows:
        step = int(math.ceil(n / max_rows))
        sampled = rows[::step]
        state.note_degradation("source %s has %d rows > cap %d; deterministic systematic sample of %d rows (step=%d)"
                               % (locator, n, max_rows, len(sampled), step))
        rows = sampled
    return cols, rows, locator, src["kind"]


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------
def _clean(input_dir, work_dir, state, override_group=None, override_targets=None):
    profile = _read_json(os.path.join(work_dir, "profile.json"), {})
    if not profile.get("has_data"):
        return {"status": "no_data"}
    cols, rows, locator, kind = _load_primary(input_dir, profile, state)
    if cols is None:
        return {"status": "no_data"}

    n_orig = len(rows)
    # deduplicate exact rows (keep first occurrence)
    seen = set()
    deduped = []
    dup_count = 0
    for r in rows:
        key = tuple(str(c) for c in r)
        if key in seen:
            dup_count += 1
            continue
        seen.add(key)
        deduped.append(r)

    col_types = {}
    col_nonnull = {}
    col_missing = {}
    for j, c in enumerate(cols):
        vals = [r[j] if j < len(r) else None for r in deduped]
        t, nonnull = _infer_column_type(vals)
        col_types[c] = t
        col_nonnull[c] = nonnull
        col_missing[c] = len(deduped) - nonnull

    # outliers: numeric columns only, IQR 1.5x — flagged, never removed
    outliers = {}
    for j, c in enumerate(cols):
        if col_types[c] not in ("numeric", "integer"):
            continue
        vals = []
        for r in deduped:
            cell = r[j] if j < len(r) else ""
            v = _cell_to_value(cell)
            if isinstance(v, (int, float)):
                vals.append((len(vals), v))
        if not vals:
            continue
        xs = sorted(v[1] for v in vals)
        q1 = _quantile_sorted(xs, 0.25)
        q3 = _quantile_sorted(xs, 0.75)
        iqr = q3 - q1
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        flagged = [v for v in vals if v[1] < lo or v[1] > hi]
        if flagged:
            outliers[c] = {"rule": "IQR 1.5x", "q1": q1, "q3": q3, "n_flagged": len(flagged),
                           "row_indices": [v[0] for v in flagged][:200]}

    clean_meta = {
        "source": locator,
        "rows_original": n_orig,
        "rows_after_dedup": len(deduped),
        "rows_dropped_duplicates": dup_count,
        "dedup_rule": "exact duplicate rows removed, first occurrence kept",
        "column_types": col_types,
        "column_missing": col_missing,
        "outliers": outliers,
        "dropped_empty_columns": [c for c, t in col_types.items() if t == "empty"],
    }

    # write cleaned.csv (deduped, missing kept as empty cells)
    cleaned_path = os.path.join(work_dir, "cleaned.csv")
    with open(cleaned_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in deduped:
            w.writerow(["" if c is None else c for c in r])
    _write_json(os.path.join(work_dir, "clean_meta.json"), clean_meta)

    state.set("clean_meta", clean_meta)
    state.log("clean", "dedup %d/%d rows; missing per column recorded; %d columns with outliers flagged"
              % (dup_count, n_orig, len(outliers)))
    return clean_meta


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
def _numeric_values(deduped, j):
    out = []
    for r in deduped:
        if j >= len(r):
            continue
        v = _cell_to_value(r[j])
        if isinstance(v, (int, float)):
            out.append(v)
    return out


def _describe_column(vals):
    n = len(vals)
    if n == 0:
        return {"n": 0}
    s = sorted(vals)
    mean = _mean(vals)
    sd = _std(vals)
    sem = sd / math.sqrt(n) if sd == sd and sd > 0 and n else float("nan")
    tcrit = _t_ppf(0.975, n - 1) if n >= 2 else float("nan")
    return {
        "n": n, "mean": mean, "std": sd, "sem": sem,
        "min": s[0], "q1": _quantile_sorted(s, 0.25), "median": _median(s),
        "q3": _quantile_sorted(s, 0.75), "max": s[-1],
        "ci95_low": (mean - tcrit * sem) if not math.isnan(tcrit) and not math.isnan(sem) else None,
        "ci95_high": (mean + tcrit * sem) if not math.isnan(tcrit) and not math.isnan(sem) else None,
        "skewness": _skewness(vals),
    }


def _group_column_candidates(cols, deduped, col_types):
    cands = []
    for j, c in enumerate(cols):
        t = col_types.get(c, "categorical")
        vals = [r[j] if j < len(r) else None for r in deduped]
        uniq = set(str(v) for v in vals if v is not None and str(v).strip() != "")
        k = len(uniq)
        n = len(vals)
        is_id = k >= max(2, n - 1)
        if is_id:
            continue
        if t == "categorical":
            if 2 <= k <= MAX_GROUP_LEVELS:
                cands.append((j, c, k, "categorical"))
        elif t == "integer":
            if 2 <= k <= MAX_GROUP_LEVELS:
                cands.append((j, c, k, "integer"))
    # prefer the LAST such column (classic UCI layouts put class last)
    return cands


def _analyze(input_dir, work_dir, state, override_group=None, override_targets=None):
    profile = _read_json(os.path.join(work_dir, "profile.json"), {})
    clean_meta = _read_json(os.path.join(work_dir, "clean_meta.json"), {})
    if not profile.get("has_data") or not clean_meta:
        return {"status": "no_data"}

    cols, rows, locator, kind = _load_primary(input_dir, profile, state)
    if cols is None:
        return {"status": "no_data"}

    # rebuild deduped rows (same rule as cleaning)
    seen = set()
    deduped = []
    for r in rows:
        key = tuple(str(c) for c in r)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)

    col_types = clean_meta.get("column_types", {})
    # re-infer if missing
    if not col_types:
        col_types = {}
        for j, c in enumerate(cols):
            t, _ = _infer_column_type([r[j] if j < len(r) else None for r in deduped])
            col_types[c] = t

    n_rows = len(deduped)
    group_col = override_group
    if group_col is None:
        cands = _group_column_candidates(cols, deduped, col_types)
        if cands:
            # prefer explicit categorical, then last position
            categorical = [c for c in cands if c[3] == "categorical"]
            pool = categorical or cands
            j, c, k, _ = pool[-1]
            group_col = c
            state.note_uncertainty("group column auto-detected as '%s' (%d levels); override with --group if wrong" % (c, k))

    numeric_cols = [c for c, t in col_types.items() if t in ("numeric", "integer") and c != group_col]
    target_cols = [c.strip() for c in override_targets.split(",")] if override_targets else numeric_cols
    target_cols = [c for c in target_cols if c in cols and col_types.get(c) in ("numeric", "integer")]
    target_cols = list(dict.fromkeys(target_cols))[:MAX_TARGET_COLS]

    stats = {
        "schema_version": VERSION,
        "case_id": state.get("case_id"),
        "source": locator,
        "n_rows": n_rows,
        "n_columns": len(cols),
        "column_types": col_types,
        "group_column": group_col,
        "target_columns": target_cols,
        "descriptive": {},
        "group_tests": {},
        "pairwise": {},
        "correlations": {},
        "skipped": [],
    }
    state.set("group_column", group_col)
    state.set("target_columns", target_cols)

    for c in target_cols:
        j = cols.index(c)
        vals = _numeric_values(deduped, j)
        stats["descriptive"][c] = _describe_column(vals)
        if not vals:
            stats["skipped"].append({"column": c, "reason": "no numeric values"})

    # group comparison
    if group_col and group_col in cols and len(target_cols) >= 1:
        gj = cols.index(group_col)
        groups = {}
        for r in deduped:
            key = str(r[gj]) if gj < len(r) else ""
            groups.setdefault(key, []).append(r)
        levels = sorted(groups.keys())
        # keep only levels with enough rows
        level_rows = {lv: len(groups[lv]) for lv in levels}
        usable = [lv for lv in levels if len(groups[lv]) >= 2]
        stats["group_levels"] = {lv: level_rows[lv] for lv in levels}
        if len(usable) < 2:
            stats["skipped"].append({"column": group_col, "reason": "too few usable groups"})
        else:
            for c in target_cols:
                j = cols.index(c)
                gvals = {lv: _numeric_values(groups[lv], j) for lv in usable}
                gvals = {lv: v for lv, v in gvals.items() if len(v) >= 2}
                if len(gvals) < 2:
                    stats["skipped"].append({"column": c, "reason": "groups too small for test"})
                    continue
                gs = [gvals[lv] for lv in gvals]
                anova = anova_f(gs)
                kw = kruskal_wallis(gs) if len(gs) >= 3 else None
                lev = levene(gs)
                entry = {
                    "groups": list(gvals.keys()),
                    "n_per_group": {lv: len(v) for lv, v in gvals.items()},
                    "group_means": {lv: _mean(v) for lv, v in gvals.items()},
                    "group_sd": {lv: _std(v) for lv, v in gvals.items()},
                    "levene": lev,
                }
                if len(gs) == 2:
                    x, y = gs[list(gvals.keys())[0]], gs[list(gvals.keys())[1]]
                    entry["welch"] = welch_t(x, y)
                    entry["mann_whitney"] = mann_whitney(x, y)
                    entry["cohens_d"] = cohens_d(x, y)
                else:
                    entry["anova"] = anova
                    entry["kruskal"] = kw
                    # pairwise Welch + Mann-Whitney + Cohen's d, with Bonferroni
                    keys = list(gvals.keys())
                    pairs = []
                    for a in range(len(keys)):
                        for b in range(a + 1, len(keys)):
                            pairs.append({
                                "g1": keys[a], "g2": keys[b],
                                "welch": welch_t(gvals[keys[a]], gvals[keys[b]]),
                                "mann_whitney": mann_whitney(gvals[keys[a]], gvals[keys[b]]),
                                "cohens_d": cohens_d(gvals[keys[a]], gvals[keys[b]]),
                            })
                    entry["pairwise"] = pairs
                    m = len(pairs)
                    entry["bonferroni_alpha"] = 0.05 / m if m else None
                stats["group_tests"][c] = entry

    # correlations among numeric target columns
    if len(target_cols) >= 2:
        corr = {}
        for a in range(len(target_cols)):
            for b in range(a + 1, len(target_cols)):
                ca, cb = target_cols[a], target_cols[b]
                va = _numeric_values(deduped, cols.index(ca))
                vb = _numeric_values(deduped, cols.index(cb))
                # complete pairs
                pairs = [(va[i], vb[i]) for i in range(min(len(va), len(vb)))]
                x = [p[0] for p in pairs if p[0] is not None and p[1] is not None]
                y = [p[1] for p in pairs if p[0] is not None and p[1] is not None]
                res = pearson_r(x, y)
                corr["%s <-> %s" % (ca, cb)] = res
        stats["correlations"] = corr

    _write_json(os.path.join(work_dir, "stats.json"), stats)
    state.log("analyze", "descriptive %d cols; group tests %d cols (%s); correlations %d pairs"
              % (len(stats["descriptive"]), len(stats["group_tests"]), group_col or "none", len(stats["correlations"])))
    return stats


# ---------------------------------------------------------------------------
# SVG rendering
# ---------------------------------------------------------------------------
def _svg_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _nice_ticks(vmin, vmax, n=6):
    if vmin == vmax:
        vmax = vmin + 1.0 if vmin == 0 else vmin * 1.1
    span = vmax - vmin
    step = span / max(1, n)
    mag = 10 ** math.floor(math.log10(step)) if step > 0 else 1.0
    norm = step / mag
    if norm < 1.5:
        step = mag
    elif norm < 3:
        step = 2 * mag
    elif norm < 7:
        step = 5 * mag
    else:
        step = 10 * mag
    start = math.ceil(vmin / step) * step
    ticks = []
    t = start
    while t <= vmax + step * 1e-9:
        if vmin - step * 0.1 <= t <= vmax + step * 0.1:
            ticks.append(t)
        t += step
    return ticks


def _svg_open(w, h):
    return ['<?xml version="1.0" encoding="UTF-8"?>',
            '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" font-family="DejaVu Sans, Arial, sans-serif">' % (w, h, w, h)]


def _svg_axis(svg, x0, y0, x1, y1, ticks, tick_fmt, orient, title):
    if not ticks:
        return
    svg.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#333" stroke-width="1"/>' % (x0, y0, x1, y1))
    for t in ticks:
        if orient == "x":
            px = x0 + (t - ticks[0]) / (ticks[-1] - ticks[0]) * (x1 - x0) if ticks[-1] != ticks[0] else x0
            svg.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#ccc" stroke-width="1"/>' % (px, y0, px, y0 + 4))
            svg.append('<text x="%.2f" y="%.2f" font-size="10" text-anchor="middle" fill="#333">%s</text>' % (px, y0 + 16, _svg_escape(tick_fmt(t))))
        else:
            py = y0 + (ticks[0] - t) / (ticks[0] - ticks[-1]) * (y1 - y0) if ticks[0] != ticks[-1] else y0
            svg.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#ccc" stroke-width="1"/>' % (x0, py, x0 - 4, py))
            svg.append('<text x="%.2f" y="%.2f" font-size="10" text-anchor="end" fill="#333">%s</text>' % (x0 - 6, py + 3, _svg_escape(tick_fmt(t))))
    if title:
        svg.append('<text x="%.2f" y="14" font-size="12" text-anchor="middle" fill="#111">%s</text>' % ((x0 + x1) / 2, _svg_escape(title)))


def _fmt_num(x):
    return ("%g" % x) if isinstance(x, float) else str(x)


def svg_boxplot(gvals, ylabel="value", unit="", title=""):
    W, H = 640, 420
    top, bottom = 50, H - 60
    left, right = 60, W - 30
    svg = _svg_open(W, H)
    svg.append('<rect x="0" y="0" width="%d" height="%d" fill="#fff"/>' % (W, H))
    labels = list(gvals.keys())
    n = len(labels)
    all_vals = [v for g in gvals.values() for v in g]
    if not all_vals:
        svg.append('<text x="%d" y="%d" font-size="14" fill="#a00">no numeric data</text>' % (W // 2, H // 2))
        svg.append('</svg>')
        return "\n".join(svg)
    vmin, vmax = min(all_vals), max(all_vals)
    pad = (vmax - vmin) * 0.05 or 1.0
    vmin -= pad
    vmax += pad
    ticks = _nice_ticks(vmin, vmax, 6)

    def ypos(v):
        return bottom - (v - vmin) / (vmax - vmin) * (bottom - top)

    _svg_axis(svg, left, bottom, right, bottom, ticks, _fmt_num, "x", None)
    _svg_axis(svg, left, top, left, bottom, ticks, _fmt_num, "y", None)
    ylabel_full = ylabel + ((" (%s)" % unit) if unit else "")
    svg.append('<text x="14" y="%.2f" font-size="11" text-anchor="middle" transform="rotate(-90 14 %.2f)" fill="#333">%s</text>'
               % ((top + bottom) / 2, (top + bottom) / 2, _svg_escape(ylabel_full)))
    if title:
        svg.append('<text x="%d" y="22" font-size="13" text-anchor="middle" fill="#111">%s</text>' % (W // 2, _svg_escape(title)))

    slot = (right - left) / n
    bw = min(56, slot * 0.6)
    for i, label in enumerate(labels):
        vals = sorted(gvals[label])
        cx = left + slot * (i + 0.5)
        q1 = _quantile_sorted(vals, 0.25)
        med = _median(vals)
        q3 = _quantile_sorted(vals, 0.75)
        iqr = q3 - q1
        lo = max(min(vals), q1 - 1.5 * iqr)
        hi = min(max(vals), q3 + 1.5 * iqr)
        y1, y2, y3, y4, y5 = ypos(hi), ypos(q3), ypos(med), ypos(q1), ypos(lo)
        svg.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#333" stroke-width="1.2"/>' % (cx, y1, cx, y5))
        svg.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#333" stroke-width="1.2"/>' % (cx - bw * 0.3, y1, cx + bw * 0.3, y1))
        svg.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#333" stroke-width="1.2"/>' % (cx - bw * 0.3, y5, cx + bw * 0.3, y5))
        svg.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" fill="#7fb3d5" stroke="#2c5d8a" stroke-width="1"/>'
                   % (cx - bw / 2, y2, bw, max(0.5, y4 - y2)))
        svg.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#1b3a57" stroke-width="1.8"/>' % (cx - bw / 2, y3, cx + bw / 2, y3))
        mean = _mean(vals)
        svg.append('<circle cx="%.2f" cy="%.2f" r="2.6" fill="#d95f02"/>' % (cx, ypos(mean)))
        # outliers beyond whiskers
        for v in vals:
            if v < lo or v > hi:
                svg.append('<circle cx="%.2f" cy="%.2f" r="2" fill="none" stroke="#d95f02" stroke-width="1"/>' % (cx, ypos(v)))
        svg.append('<text x="%.2f" y="%d" font-size="10" text-anchor="middle" fill="#111">%s</text>' % (cx, bottom + 30, _svg_escape(label)))
    svg.append('</svg>')
    return "\n".join(svg)


def svg_heatmap(matrix, labels, title=""):
    """matrix: dict key -> float (e.g. correlation r)."""
    n = len(labels)
    if n < 2:
        return None
    W, H = 620, 620
    cell = min(46, (H - 120) // n)
    left = 130
    top = 30
    svg = _svg_open(W, H)
    svg.append('<rect x="0" y="0" width="%d" height="%d" fill="#fff"/>' % (W, H))
    if title:
        svg.append('<text x="%d" y="18" font-size="13" text-anchor="middle" fill="#111">%s</text>' % (W // 2, _svg_escape(title)))
    for i in range(n):
        for j in range(n):
            x = left + j * cell
            y = top + i * cell
            if i == j:
                color = "#c0392b"
                r = 1.0
            else:
                key = "%s <-> %s" % (labels[i], labels[j])
                rev = "%s <-> %s" % (labels[j], labels[i])
                raw = matrix.get(key, matrix.get(rev))
                if isinstance(raw, dict):
                    r = raw.get("r")
                else:
                    r = raw
                if r is None:
                    color = "#eeeeee"
                else:
                    r = max(-1.0, min(1.0, r))
                    # blue for negative, red for positive, white for zero
                    if r >= 0:
                        color = _heat_color(1.0, 0.98 - 0.5 * r, 0.94 - 0.6 * r)
                    else:
                        color = _heat_color(0.94 - 0.6 * abs(r), 0.98 - 0.5 * abs(r), 1.0)
            svg.append('<rect x="%d" y="%d" width="%d" height="%d" fill="%s" stroke="#bbb" stroke-width="0.5"/>' % (x, y, cell, cell, color))
            if i == j:
                svg.append('<text x="%d" y="%d" font-size="8" text-anchor="middle" fill="#fff">1</text>' % (x + cell / 2, y + cell / 2 + 3))
            elif r is not None:
                svg.append('<text x="%d" y="%d" font-size="7" text-anchor="middle" fill="#111">%.2f</text>' % (x + cell / 2, y + cell / 2 + 3, r))
    for i, lab in enumerate(labels):
        svg.append('<text x="%d" y="%d" font-size="9" text-anchor="end" fill="#111">%s</text>' % (left - 4, top + i * cell + cell / 2 + 3, _svg_escape(lab[:16])))
        svg.append('<text x="%d" y="%d" font-size="9" text-anchor="middle" fill="#111" transform="rotate(-45 %d %d)">%s</text>'
                   % (left + i * cell + cell / 2, top - 4, left + i * cell + cell / 2, top - 4, _svg_escape(lab[:16])))
    svg.append('</svg>')
    return "\n".join(svg)


def _heat_color(r, g, b):
    ri = max(0, min(255, int(round(r * 255))))
    gi = max(0, min(255, int(round(g * 255))))
    bi = max(0, min(255, int(round(b * 255))))
    return "#%02x%02x%02x" % (ri, gi, bi)


def svg_scatter(x, y, xlabel="x", ylabel="y", title=""):
    if len(x) < 2 or len(x) != len(y):
        return None
    W, H = 640, 420
    left, right, top, bottom = 60, W - 30, 40, H - 60
    svg = _svg_open(W, H)
    svg.append('<rect x="0" y="0" width="%d" height="%d" fill="#fff"/>' % (W, H))
    xmin, xmax = min(x), max(x)
    ymin, ymax = min(y), max(y)
    xpad = (xmax - xmin) * 0.05 or 1.0
    ypad = (ymax - ymin) * 0.05 or 1.0
    xmin -= xpad
    xmax += xpad
    ymin -= ypad
    ymax += ypad
    xticks = _nice_ticks(xmin, xmax, 6)
    yticks = _nice_ticks(ymin, ymax, 6)
    _svg_axis(svg, left, bottom, right, bottom, xticks, _fmt_num, "x", None)
    _svg_axis(svg, left, top, left, bottom, yticks, _fmt_num, "y", None)
    svg.append('<text x="%d" y="%d" font-size="11" text-anchor="middle" fill="#333">%s</text>' % (W // 2, H - 12, _svg_escape(xlabel)))
    svg.append('<text x="14" y="%d" font-size="11" text-anchor="middle" transform="rotate(-90 14 %d)" fill="#333">%s</text>' % ((top + bottom) // 2, (top + bottom) // 2, _svg_escape(ylabel)))
    if title:
        svg.append('<text x="%d" y="20" font-size="13" text-anchor="middle" fill="#111">%s</text>' % (W // 2, _svg_escape(title)))
    for xi, yi in zip(x, y):
        px = left + (xi - xmin) / (xmax - xmin) * (right - left)
        py = bottom - (yi - ymin) / (ymax - ymin) * (bottom - top)
        svg.append('<circle cx="%.2f" cy="%.2f" r="2.4" fill="#2c5d8a" fill-opacity="0.55"/>' % (px, py))
    svg.append('</svg>')
    return "\n".join(svg)


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------
def _fmt_p(p):
    """Format a p-value without lying about tiny probabilities."""
    if p is None:
        return "—"
    if isinstance(p, float) and (math.isnan(p) or math.isinf(p)):
        return "—"
    if p < 0.0001:
        return "<0.0001"
    return "%.4g" % p


def _fmt_eff(e):
    if e is None:
        return "—"
    if isinstance(e, float) and (math.isnan(e) or math.isinf(e)):
        return "—"
    return "%.4g" % e


def _render_report(stats, clean_meta, report_path, figures):
    lines = []
    lines.append("# 科研数据分析报告")
    lines.append("")
    lines.append("> 本报告全部数字均由 `work/stats.json` 与 `work/clean_meta.json` 生成，可回算；")
    lines.append("> 生成脚本：`scripts/core_impl.py`（纯标准库，确定性输出）。")
    lines.append("")
    lines.append("## 1. 数据概览")
    lines.append("")
    lines.append("- 数据源：`%s`" % stats.get("source"))
    lines.append("- 清洗后行数：%d（原始 %d，去重 %d）" % (
        stats.get("n_rows", 0),
        clean_meta.get("rows_original", 0),
        clean_meta.get("rows_dropped_duplicates", 0)))
    lines.append("- 数值分析列：%s" % (", ".join(stats.get("target_columns", [])) or "无"))
    g = stats.get("group_column")
    if g:
        lv = stats.get("group_levels", {})
        lines.append("- 分组列：`%s`（%s）" % (g, ", ".join("%s(n=%d)" % (k, v) for k, v in sorted(lv.items()))))
    lines.append("")

    lines.append("## 2. 描述统计")
    lines.append("")
    desc = stats.get("descriptive", {})
    if desc:
        lines.append("| 列 | n | 均值 | 标准差 | 中位数 | Q1–Q3 | min–max |")
        lines.append("|---|--:|--:|--:|--:|--:|--:|")
        for c, d in desc.items():
            lines.append("| %s | %d | %.4g | %.4g | %.4g | %.4g–%.4g | %.4g–%.4g |" % (
                c, d.get("n", 0), d.get("mean", float("nan")), d.get("std", float("nan")),
                d.get("median", float("nan")), d.get("q1", float("nan")), d.get("q3", float("nan")),
                d.get("min", float("nan")), d.get("max", float("nan"))))
        lines.append("")
    else:
        lines.append("无可分析的数值列。")
        lines.append("")

    gt = stats.get("group_tests", {})
    if gt:
        lines.append("## 3. 组间比较")
        lines.append("")
        lines.append("对每个数值列做 Welch t（2 组）或单因素 ANOVA（≥3 组），并以 Levene 检验方差齐性、")
        lines.append("以 Mann-Whitney U / Kruskal-Wallis 提供秩检验对照，多重比较采用 Bonferroni 校正。")
        lines.append("")
        lines.append("| 列 | 检验 | 统计量 | p 值 | 效应量 | Levene p |")
        lines.append("|---|--|--:|--:|--:|--:|")
        for c, e in gt.items():
            if "anova" in e:
                a = e["anova"]
                kw = e.get("kruskal", {})
                lines.append("| %s | ANOVA | F=%.4g | %s | η²=%.4g | %s |" % (
                    c, a.get("F", float("nan")), _fmt_p(a.get("p")),
                    a.get("eta_sq", float("nan")), _fmt_p(e.get("levene", {}).get("p"))))
                if kw:
                    lines.append("| %s | Kruskal-Wallis | H=%.4g | %s | — | — |" % (
                        c, kw.get("H", float("nan")), _fmt_p(kw.get("p"))))
            else:
                w = e.get("welch", {})
                mw = e.get("mann_whitney", {})
                lines.append("| %s | Welch t | t=%.4g | %s | d=%.4g | %s |" % (
                    c, w.get("t", float("nan")), _fmt_p(w.get("p")),
                    e.get("cohens_d") or float("nan"), _fmt_p(e.get("levene", {}).get("p"))))
                if mw:
                    lines.append("| %s | Mann-Whitney U | U=%.4g | %s | — | — |" % (
                        c, mw.get("U", float("nan")), _fmt_p(mw.get("p"))))
        lines.append("")
        # pairwise summary for >=3 groups
        for c, e in gt.items():
            pairs = e.get("pairwise")
            if not pairs:
                continue
            lines.append("### 3.%d `%s` 两两比较（Welch t，Bonferroni α=%.4g）" % (list(gt).index(c) + 1, c, e.get("bonferroni_alpha") or 0.05))
            lines.append("")
            lines.append("| 组对 | Welch t | p | 显著 |")
            lines.append("|---|--:|--:|--|")
            for p in pairs:
                pw = p.get("welch", {})
                pv = pw.get("p", float("nan"))
                sig = "是" if (isinstance(pv, float) and pv < (e.get("bonferroni_alpha") or 0.05)) else "否"
                lines.append("| %s vs %s | %.4g | %s | %s |" % (p["g1"], p["g2"], pw.get("t", float("nan")), _fmt_p(pv), sig))
            lines.append("")

    corr = stats.get("correlations", {})
    if corr:
        lines.append("## 4. 相关分析（Pearson）")
        lines.append("")
        ranked = sorted(corr.items(), key=lambda kv: abs(kv[1].get("r", 0) or 0), reverse=True)
        lines.append("| 列对 | r | p 值 | n |")
        lines.append("|---|--:|--:|--:|")
        for name, res in ranked[:12]:
            lines.append("| %s | %.4g | %s | %d |" % (name, res.get("r", float("nan")), _fmt_p(res.get("p")), res.get("n", 0)))
        lines.append("")

    if figures:
        lines.append("## 5. 图表")
        lines.append("")
        for f in figures:
            lines.append("- `%s`" % os.path.basename(f))
        lines.append("")

    lines.append("## 6. 质量与异常说明")
    lines.append("")
    missing = clean_meta.get("column_missing", {})
    miss_cols = [(c, n) for c, n in missing.items() if n]
    lines.append("- 缺失值：%s" % (", ".join("%s(n=%d)" % (c, n) for c, n in sorted(miss_cols, key=lambda kv: -kv[1])) if miss_cols else "无"))
    outliers = clean_meta.get("outliers", {})
    lines.append("- 异常值（IQR 1.5× 标记，未删除）：%s" % (", ".join("%s(%d)" % (c, o["n_flagged"]) for c, o in sorted(outliers.items())) if outliers else "无"))
    skipped = stats.get("skipped", [])
    lines.append("- 跳过项：%s" % (", ".join("`%s`（%s）" % (s.get("column"), s.get("reason")) for s in skipped) if skipped else "无"))
    lines.append("")
    lines.append("## 7. 口径与不确定性")
    lines.append("")
    lines.append("- 推断检验在 Welch/秩检验双轨下给出；方差齐性以 Levene 检验评估。")
    lines.append("- 缺失值按列记录，未静默删除；异常值按 IQR 1.5× 标记，不删除。")
    lines.append("- 详细不确定性见 `result.json` 的 `uncertainty` 字段。")
    lines.append("")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


# ---------------------------------------------------------------------------
# Render command
# ---------------------------------------------------------------------------
def _render(input_dir, work_dir, output_dir, state):
    stats = _read_json(os.path.join(work_dir, "stats.json"), {})
    clean_meta = _read_json(os.path.join(work_dir, "clean_meta.json"), {})
    if not stats:
        state.note_error("no stats.json to render")
        return []
    os.makedirs(output_dir, exist_ok=True)
    figures = []

    # box plots for group-tested columns (top MAX_FIGURES by significance)
    gt = stats.get("group_tests", {})
    if gt:
        ordered = []
        for c, e in gt.items():
            pval = e.get("anova", {}).get("p") if "anova" in e else e.get("welch", {}).get("p")
            if pval is None or (isinstance(pval, float) and (math.isnan(pval) or math.isinf(pval))):
                pval = 1.0
            ordered.append((pval if isinstance(pval, float) else 1.0, c, e))
        ordered.sort(key=lambda t: t[0])
        for _pval, c, e in ordered[:MAX_FIGURES]:
            svg = svg_boxplot(_group_raw_values(input_dir, work_dir, state, c, e["groups"]),
                              ylabel=c, unit="", title="%s 分组分布" % c)
            if svg:
                fname = "figure_box_%s.svg" % _slug(c)
                _write_text(os.path.join(output_dir, fname), svg)
                figures.append(os.path.join(output_dir, fname))

    # correlation heatmap
    corr = stats.get("correlations", {})
    labels = stats.get("target_columns", [])
    if len(labels) >= 2:
        svg = svg_heatmap(corr, labels, title="Pearson 相关矩阵")
        if svg:
            fname = "figure_corr_heatmap.svg"
            _write_text(os.path.join(output_dir, fname), svg)
            figures.append(os.path.join(output_dir, fname))

    # one scatter for the strongest correlation
    if corr:
        ranked = sorted(corr.items(), key=lambda kv: abs(kv[1].get("r", 0) or 0), reverse=True)
        top = ranked[0][0]
        ca, cb = top.split(" <-> ")
        if ca in labels and cb in labels:
            xs, ys = _paired_values(input_dir, work_dir, state, ca, cb)
            if xs and ys:
                svg = svg_scatter(xs, ys, xlabel=ca, ylabel=cb, title="%s（r=%.3f）" % (top, ranked[0][1].get("r", 0)))
                if svg:
                    fname = "figure_scatter_%s_%s.svg" % (_slug(ca), _slug(cb))
                    _write_text(os.path.join(output_dir, fname), svg)
                    figures.append(os.path.join(output_dir, fname))

    report_path = os.path.join(output_dir, "report.md")
    _render_report(stats, clean_meta, report_path, figures)
    state.log("render", "%d figures + report.md" % len(figures))
    return figures


def _slug(s):
    return re.sub(r"[^0-9A-Za-z_\-]+", "_", str(s)).strip("_") or "col"


def _group_raw_values(input_dir, work_dir, state, col, levels):
    profile = _read_json(os.path.join(work_dir, "profile.json"), {})
    cols, rows, locator, kind = _load_primary(input_dir, profile, state)
    if cols is None or col not in cols:
        return {}
    group_col = state.get("group_column")
    if group_col is None:
        group_col = _read_json(os.path.join(work_dir, "stats.json"), {}).get("group_column")
    j = cols.index(col)
    gj = cols.index(group_col) if group_col and group_col in cols else -1
    out = {lv: [] for lv in levels}
    seen = set()
    for r in rows:
        key = tuple(str(c) for c in r)
        if key in seen:
            continue
        seen.add(key)
        if gj >= 0:
            gk = str(r[gj]) if gj < len(r) else ""
        else:
            gk = ""
        if gk not in out:
            continue
        v = _cell_to_value(r[j]) if j < len(r) else None
        if isinstance(v, (int, float)):
            out[gk].append(v)
    return out


def _paired_values(input_dir, work_dir, state, ca, cb):
    profile = _read_json(os.path.join(work_dir, "profile.json"), {})
    cols, rows, locator, kind = _load_primary(input_dir, profile, state)
    if cols is None or ca not in cols or cb not in cols:
        return [], []
    ja, jb = cols.index(ca), cols.index(cb)
    seen = set()
    xs, ys = [], []
    for r in rows:
        key = tuple(str(c) for c in r)
        if key in seen:
            continue
        seen.add(key)
        va = _cell_to_value(r[ja]) if ja < len(r) else None
        vb = _cell_to_value(r[jb]) if jb < len(r) else None
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            xs.append(va)
            ys.append(vb)
    return xs, ys


# ---------------------------------------------------------------------------
# Result assembly
# ---------------------------------------------------------------------------
def _fill_contract_value(key, stats, clean_meta, state, sources, artifacts):
    if key == "analysis_results":
        return {
            "primary_table": stats.get("source"),
            "rows_original": clean_meta.get("rows_original"),
            "rows_after_cleaning": stats.get("n_rows"),
            "columns_analyzed": stats.get("target_columns"),
            "group_column": stats.get("group_column"),
            "descriptive": stats.get("descriptive", {}),
            "group_tests": stats.get("group_tests", {}),
            "correlations": stats.get("correlations", {}),
            "report": "report.md",
            "figures": [os.path.basename(a) for a in artifacts],
        }
    if key == "quality_evidence":
        return {
            "reproducible": True,
            "deterministic": True,
            "scripts": ["scripts/main.py", "scripts/core_impl.py"],
            "stats_source": "work/stats.json",
            "cleaning_rules": "work/clean_meta.json",
            "field_dictionary": "work/profile.json",
            "cleaned_table": "work/cleaned.csv",
            "sources": sources,
        }
    if key == "anomaly_notes":
        return {
            "missing": clean_meta.get("column_missing", {}),
            "outliers": clean_meta.get("outliers", {}),
            "errors": state.d.get("errors", []),
            "degradations": state.d.get("degradations", []),
            "uncertainty": state.d.get("uncertainty", []),
            "pending_human": state.d.get("pending_human", []),
        }
    if key in stats:
        return stats[key]
    return None


def _finalize(input_dir, work_dir, output_dir, state):
    stats = _read_json(os.path.join(work_dir, "stats.json"), {})
    clean_meta = _read_json(os.path.join(work_dir, "clean_meta.json"), {})
    profile = _read_json(os.path.join(work_dir, "profile.json"), {})

    # assemble sources: locator + verbatim quote for key conclusions
    sources = []
    primary = profile.get("primary")
    if primary:
        quote = _source_quote(input_dir, profile, state, primary)
        sources.append({"locator": primary, "quote": quote})
    for c in stats.get("target_columns", [])[:8]:
        d = stats.get("descriptive", {}).get(c)
        if d:
            sources.append({
                "locator": "%s#col:%s" % (primary, c),
                "quote": "n=%d mean=%.4g sd=%.4g median=%.4g" % (
                    d.get("n", 0), d.get("mean", float("nan")), d.get("std", float("nan")), d.get("median", float("nan"))),
            })

    # status determination
    has_case = os.path.isfile(os.path.join(input_dir, "case.json")) or state.get("contract_source") != "default"
    if not profile.get("has_data") and not stats:
        status = "insufficient_evidence" if has_case else "input_error"
    elif state.d.get("errors") and not stats:
        status = "input_error"
    elif stats:
        if state.d.get("errors") or state.d.get("degradations"):
            status = "partial"
        else:
            status = "ok"
    else:
        status = "partial"

    contract = state.get("contract") or {}
    contract_keys = list(contract.keys()) if contract else list(DEFAULT_CONTRACT_KEYS)
    state.log("finalize", "status=%s; contract keys filled: %s" % (status, ", ".join(contract_keys)))

    result = {
        "case_id": state.get("case_id"),
        "status": status,
        "runtime": {
            "seconds_elapsed": round(time.time() - state.get("started_at", time.time()), 2),
            "schema_version": VERSION,
        },
        "artifacts": [f for f in _list_artifacts(output_dir)],
        "sources": sources,
        "uncertainty": state.d.get("uncertainty", []),
        "change_log": state.d.get("change_log", []),
        "errors": state.d.get("errors", []),
        "degradations": state.d.get("degradations", []),
        "pending_human": state.d.get("pending_human", []),
    }

    # fill output_contract fields at top level (original keys preserved)
    for key in contract_keys:
        result[key] = _fill_contract_value(key, stats, clean_meta, state, sources, result["artifacts"])
    result["contract_keys"] = list(contract_keys)

    _write_json(os.path.join(output_dir, "result.json"), result)
    return result


def _source_quote(input_dir, profile, state, locator):
    for s in profile.get("sources", []):
        if s["locator"] == locator:
            if s["kind"] == "sqlite":
                return "table %s: %d rows x %d columns" % (s["table"], s["n_rows"], s["n_cols"])
            path = os.path.join(input_dir, s["file"])
            text, _ = _read_text_any(path)
            if text:
                first = text.strip().splitlines()[0][:200] if text.strip() else "(empty)"
                return "header: %s" % first
    return "(no verbatim source)"


def _list_artifacts(output_dir):
    out = []
    if os.path.isdir(output_dir):
        for name in sorted(os.listdir(output_dir)):
            full = os.path.join(output_dir, name)
            if os.path.isfile(full) and not name.startswith("."):
                out.append(name)
    return out


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(_sanitize(obj), fh, ensure_ascii=False, indent=2, sort_keys=True)


def _write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _read_json(path, default):
    if not os.path.isfile(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def cmd_scaffold(input_dir, work_dir, output_dir, state):
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    state.set("started_at", time.time())
    state.set("input_dir", input_dir)
    state.set("output_dir", output_dir)
    state.set("work_dir", work_dir)
    case_id, contract = _read_case(input_dir, state)
    state.log("scaffold", "case_id=%s; contract_source=%s" % (case_id, state.get("contract_source")))
    # write partial skeleton immediately
    contract_keys = list(contract.keys()) if contract else list(DEFAULT_CONTRACT_KEYS)
    skeleton = {
        "case_id": case_id,
        "status": "partial",
        "runtime": {"seconds_elapsed": 0.0, "schema_version": VERSION},
        "artifacts": [],
        "sources": [],
        "uncertainty": state.d.get("uncertainty", []),
        "change_log": state.d.get("change_log", []),
        "errors": state.d.get("errors", []),
        "degradations": state.d.get("degradations", []),
        "pending_human": state.d.get("pending_human", []),
        "contract_keys": contract_keys,
    }
    for k in contract_keys:
        skeleton[k] = None
    _write_json(os.path.join(output_dir, "result.json"), skeleton)
    state.save()
    return skeleton


def cmd_query(input_dir, work_dir, output_dir, state, sql, db=None):
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    meta, data = _discover_files(input_dir)
    dbs = [d for d in data if os.path.splitext(d)[1].lower() in SQL_EXT]
    if db:
        dbs = [d for d in dbs if os.path.basename(d) == db or os.path.relpath(d, input_dir).replace("\\", "/") == db]
    if not dbs:
        result = {"status": "error", "error": "no SQLite database found", "rows": []}
        state.note_error("query: no SQLite database found")
    else:
        path = dbs[0]
        try:
            conn = sqlite3.connect(path)
            cur = conn.cursor()
            cur.execute(sql)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = [list(r) for r in cur.fetchall()]
            conn.close()
            result = {"status": "ok", "db": os.path.relpath(path, input_dir).replace("\\", "/"),
                      "sql": sql, "columns": cols, "rows": rows, "n_rows": len(rows)}
        except sqlite3.Error as exc:
            result = {"status": "error", "error": str(exc), "rows": []}
            state.note_error("query failed: %s" % exc)
    _write_json(os.path.join(work_dir, "query.json"), result)
    state.log("query", "status=%s" % result["status"])
    state.save()
    return result


def _parse_args(argv):
    sub = "run"
    opts = {"input": DEFAULT_INPUT, "output": DEFAULT_OUTPUT, "work": DEFAULT_WORK,
            "primary": None, "group": None, "targets": None, "sql": None, "db": None}
    rest = list(argv)
    if rest and not rest[0].startswith("-"):
        sub = rest.pop(0)
    i = 0
    flags = {"--input": "input", "--output": "output", "--work": "work", "--primary": "primary",
             "--group": "group", "--targets": "targets", "--sql": "sql", "--db": "db"}
    while i < len(rest):
        a = rest[i]
        if a in flags:
            if i + 1 < len(rest):
                opts[flags[a]] = rest[i + 1]
                i += 2
            else:
                i += 1
        elif a.startswith("--") and "=" in a:
            k, v = a.split("=", 1)
            if k in flags:
                opts[flags[k]] = v
            i += 1
        else:
            i += 1
    return sub, opts


def cli_main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    started = time.time()
    sub, opts = _parse_args(argv)
    input_dir = opts["input"]
    output_dir = opts["output"]
    work_dir = opts["work"]

    state = State(work_dir).load()
    try:
        if sub == "scaffold":
            cmd_scaffold(input_dir, work_dir, output_dir, state)
        elif sub == "profile":
            if not state.d.get("started_at"):
                cmd_scaffold(input_dir, work_dir, output_dir, state)
            _profile(input_dir, work_dir, state, opts["primary"])
        elif sub == "clean":
            if not state.d.get("started_at"):
                cmd_scaffold(input_dir, work_dir, output_dir, state)
            _clean(input_dir, work_dir, state, opts["group"], opts["targets"])
        elif sub == "analyze":
            if not state.d.get("started_at"):
                cmd_scaffold(input_dir, work_dir, output_dir, state)
            _analyze(input_dir, work_dir, state, opts["group"], opts["targets"])
        elif sub == "render":
            if not state.d.get("started_at"):
                cmd_scaffold(input_dir, work_dir, output_dir, state)
            _render(input_dir, work_dir, output_dir, state)
        elif sub == "finalize":
            if not state.d.get("started_at"):
                cmd_scaffold(input_dir, work_dir, output_dir, state)
            _finalize(input_dir, work_dir, output_dir, state)
        elif sub == "query":
            cmd_query(input_dir, work_dir, output_dir, state, opts["sql"] or "SELECT name FROM sqlite_master WHERE type='table'", opts["db"])
        else:  # "run" or unknown -> full pipeline
            cmd_scaffold(input_dir, work_dir, output_dir, state)
            _profile(input_dir, work_dir, state, opts["primary"])
            _clean(input_dir, work_dir, state, opts["group"], opts["targets"])
            _analyze(input_dir, work_dir, state, opts["group"], opts["targets"])
            _render(input_dir, work_dir, output_dir, state)
            _finalize(input_dir, work_dir, output_dir, state)
        state.set("elapsed", round(time.time() - started, 2))
        state.save()
        return 0
    except Exception as exc:  # noqa: BLE001 — never leave an empty /app/output
        state.note_error("uncaught exception: %s" % exc)
        state.note_error(traceback.format_exc(limit=3))
        try:
            os.makedirs(output_dir, exist_ok=True)
            if not os.path.isfile(os.path.join(output_dir, "result.json")):
                _write_json(os.path.join(output_dir, "result.json"), {
                    "case_id": state.get("case_id", "unknown_case"),
                    "status": "input_error",
                    "runtime": {"seconds_elapsed": round(time.time() - started, 2), "schema_version": VERSION},
                    "errors": state.d.get("errors", []),
                    "degradations": state.d.get("degradations", []),
                    "uncertainty": state.d.get("uncertainty", []),
                    "pending_human": state.d.get("pending_human", []),
                    "change_log": state.d.get("change_log", []),
                    "sources": [],
                    "artifacts": [],
                    "contract_keys": list(DEFAULT_CONTRACT_KEYS),
                    "analysis_results": None,
                    "quality_evidence": None,
                    "anomaly_notes": {"errors": state.d.get("errors", [])},
                })
        except OSError:
            pass
        try:
            state.save()
        except OSError:
            pass
        return 0


if __name__ == "__main__":
    raise SystemExit(cli_main())
