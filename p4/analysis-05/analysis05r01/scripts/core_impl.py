#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py - implementation for the evidence-reinforced data-analysis Skill.

Design contract
---------------
* Standard library only (csv / json / sqlite3 / math / xml / hashlib / etc.).
* Deterministic: fixed iteration order, sorted JSON keys, no randomness.
* Idempotent: every stage overwrites its own artifacts and can be rerun.
* Never raises to the caller: run() catches per-stage exceptions, records them,
  and always writes at least a minimal valid result.json.
* Evidence provenance is first-class: every key conclusion/number/rule/figure
  datum gets a locator + verbatim quote entry in evidence_map.json.

Commands
--------
scaffold  -> result.json skeleton (status=partial, contract keys null/[]).
profile   -> work/sources.json + work/profile.json (field dictionary).
clean     -> work/cleaned.csv + work/clean_meta.json.
analyze   -> work/stats.json (descriptive + tests + correlations + evidence seeds).
render    -> /app/output/report.md + figure_*.svg + evidence_map.json.
finalize  -> final result.json (status, contract backfill, sources, uncertainty).
full      -> run all six stages in order.
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
from collections import Counter, defaultdict

VERSION = "1.1.0"
ENCODINGS = ("utf-8-sig", "utf-8", "latin-1", "gbk")
MAX_TEXT_BYTES = 50 * 1024 * 1024  # 50 MB guard for text inputs
DEFAULT_CONTRACT_KEYS = ("analysis_results", "quality_evidence", "anomaly_notes")


# --------------------------------------------------------------------------- #
# Numeric kernels (standard library only)
# --------------------------------------------------------------------------- #
def _log_gamma(x: float) -> float:
    if x <= 0:
        return float("nan")
    g = 7
    p = (0.99999999999980993, 676.5203681218851, -1259.1392167224028,
         771.32342877765313, -176.61502916214059, 12.507343278686905,
         -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7)
    if x < 0.5:
        return math.log(math.pi) - math.log(math.sin(math.pi * x)) - _log_gamma(1.0 - x)
    x -= 1.0
    a = p[0]
    t = x + g + 0.5
    for i in range(1, len(p)):
        a += p[i] / (x + i)
    return 0.5 * math.log(2.0 * math.pi) + (x + 0.5) * math.log(t) - t + math.log(a)


def _gser(a: float, x: float) -> float:
    ap = a
    total = 1.0 / a
    delta = total
    for _ in range(500):
        ap += 1.0
        delta *= x / ap
        total += delta
        if abs(delta) < abs(total) * 1e-15:
            break
    return total * math.exp(-x + a * math.log(x) - _log_gamma(a))


def _gcf(a: float, x: float) -> float:
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-12:
            break
    return math.exp(-x + a * math.log(x) - _log_gamma(a)) * h


def _gamma_p(a: float, x: float) -> float:
    """Regularized lower incomplete gamma P(a, x)."""
    if x < 0.0 or a <= 0.0:
        return float("nan")
    if x == 0.0:
        return 0.0
    if x < a + 1.0:
        return _gser(a, x)
    return 1.0 - _gcf(a, x)


def _chi2_cdf(x: float, k: float) -> float:
    if x <= 0.0:
        return 0.0
    return _gamma_p(k / 2.0, x / 2.0)


def _betacf(a: float, b: float, x: float) -> float:
    tiny = 1e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 500):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-12:
            break
    return h


def _regularized_beta(a: float, b: float, x: float) -> float:
    if x < 0.0 or x > 1.0 or a <= 0 or b <= 0:
        return float("nan")
    if x == 0.0:
        return 0.0
    if x == 1.0:
        return 1.0
    bt = math.exp(_log_gamma(a + b) - _log_gamma(a) - _log_gamma(b)
                  + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _student_t_cdf(t: float, df: float) -> float:
    if df <= 0:
        return float("nan")
    x = df / (df + t * t)
    ib = _regularized_beta(df / 2.0, 0.5, x)
    if t >= 0:
        return 1.0 - 0.5 * ib
    return 0.5 * ib


def _f_cdf(f: float, d1: float, d2: float) -> float:
    if f <= 0 or d1 <= 0 or d2 <= 0:
        return float("nan")
    x = d1 * f / (d1 * f + d2)
    return _regularized_beta(d1 / 2.0, d2 / 2.0, x)


def _erf(x: float) -> float:
    sign = 1.0 if x >= 0 else -1.0
    x = abs(x)
    t = 1.0 / (1.0 + 0.3275911 * x)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
                - 0.284496736) * t + 0.254829592) * t * math.exp(-x * x)
    return sign * y


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + _erf(x / math.sqrt(2.0)))


def _mean(xs):
    return sum(xs) / len(xs)


def _variance(xs, mean=None):
    if mean is None:
        mean = _mean(xs)
    n = len(xs)
    if n < 2:
        return 0.0
    return sum((x - mean) ** 2 for x in xs) / (n - 1)


def _quantile(sorted_xs, q):
    n = len(sorted_xs)
    if n == 0:
        return float("nan")
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_xs[lo]
    frac = pos - lo
    return sorted_xs[lo] * (1.0 - frac) + sorted_xs[hi] * frac


def _rankdata(xs):
    """Average ranks with ties; deterministic via stable sort on (value, index)."""
    order = sorted(range(len(xs)), key=lambda i: (xs[i], i))
    ranks = [0.0] * len(xs)
    i = 0
    n = len(order)
    while i < n:
        j = i
        while j + 1 < n and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _mannwhitney_exact_p(u1: float, u2: float, n1: int, n2: int) -> float:
    """Exact two-sided p via dynamic programming over rank sums (small samples)."""
    n = n1 + n2
    if n > 40:
        return float("nan")
    dp = [defaultdict(int) for _ in range(n1 + 1)]
    dp[0][0] = 1
    for rank in range(1, n + 1):
        for k in range(min(rank, n1), 0, -1):
            for s, c in list(dp[k - 1].items()):
                dp[k][s + rank] += c
    total = math.comb(n, n1)
    if total == 0:
        return float("nan")
    u = min(u1, u2)
    lo = n1 * (n1 + 1) // 2
    p_lo = sum(c for s, c in dp[n1].items() if (s - lo) <= u) / total
    p_hi = sum(c for s, c in dp[n1].items() if (s - lo) >= u) / total
    return min(1.0, 2.0 * min(p_lo, p_hi))


def _mannwhitney_normal_p(u: float, n1: int, n2: int, ranks) -> float:
    n = n1 + n2
    m_u = n1 * n2 / 2.0
    counts = Counter(round(float(r), 12) for r in ranks)
    tie = sum(v ** 3 - v for v in counts.values() if v > 1)
    denom = n * (n - 1)
    sigma = math.sqrt(n1 * n2 / 12.0 * ((n + 1) - (tie / denom if denom else 0.0)))
    if sigma <= 0:
        return 1.0
    z = (u - m_u - (0.5 if u > m_u else -0.5)) / sigma
    return 2.0 * _normal_cdf(-abs(z))


def welch_t(a, b):
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return None
    m1, m2 = _mean(a), _mean(b)
    v1, v2 = _variance(a), _variance(b)
    se = math.sqrt(v1 / n1 + v2 / n2)
    if se <= 0:
        return None
    t = (m1 - m2) / se
    num = (v1 / n1 + v2 / n2) ** 2
    den = (v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1)
    df = num / den if den > 0 else n1 + n2 - 2
    p = 2.0 * (1.0 - _student_t_cdf(abs(t), df))
    sp2 = ((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2)
    sp = math.sqrt(sp2) if sp2 > 0 else 0.0
    d = (m1 - m2) / sp if sp > 0 else 0.0
    return {"t": t, "df": df, "p": p, "cohens_d": d, "n1": n1, "n2": n2}


def oneway_anova(groups):
    k = len(groups)
    n = sum(len(g) for g in groups)
    if k < 2 or n == 0:
        return None
    grand = sum(sum(g) for g in groups) / n
    ss_between = sum(len(g) * (_mean(g) - grand) ** 2 for g in groups)
    ss_within = sum(sum((x - _mean(g)) ** 2 for x in g) for g in groups)
    df_b = k - 1
    df_w = n - k
    if df_w <= 0 or ss_within <= 0:
        return None
    ms_b = ss_between / df_b
    ms_w = ss_within / df_w
    if ms_w <= 0:
        return None
    f = ms_b / ms_w
    p = 1.0 - _f_cdf(f, df_b, df_w)
    eta2 = ss_between / (ss_between + ss_within) if (ss_between + ss_within) > 0 else 0.0
    return {"F": f, "df_between": df_b, "df_within": df_w, "p": p, "eta_squared": eta2}


def mann_whitney(a, b):
    n1, n2 = len(a), len(b)
    if n1 == 0 or n2 == 0:
        return None
    combined = list(a) + list(b)
    ranks = _rankdata(combined)
    r1 = sum(ranks[:n1])
    u1 = r1 - n1 * (n1 + 1) / 2.0
    u2 = n1 * n2 - u1
    u = min(u1, u2)
    if n1 + n2 <= 40:
        p = _mannwhitney_exact_p(u1, u2, n1, n2)
        method = "exact"
    else:
        p = _mannwhitney_normal_p(u, n1, n2, ranks)
        method = "normal_approx"
    if p != p:  # NaN fallback
        p = _mannwhitney_normal_p(u, n1, n2, ranks)
        method = "normal_approx"
    return {"U": u, "p": p, "method": method, "n1": n1, "n2": n2}


def kruskal_wallis(groups):
    k = len(groups)
    all_vals = []
    for g in groups:
        all_vals.extend(g)
    n = len(all_vals)
    if k < 2 or n == 0:
        return None
    ranks = _rankdata(all_vals)
    idx = 0
    rj = []
    for g in groups:
        rj.append(sum(ranks[idx:idx + len(g)]))
        idx += len(g)
    h = 12.0 / (n * (n + 1)) * sum(r * r / len(g) for r, g in zip(rj, groups)) - 3.0 * (n + 1)
    counts = Counter(round(float(v), 12) for v in all_vals)
    tie = sum(v ** 3 - v for v in counts.values() if v > 1)
    denom = n ** 3 - n
    if tie > 0 and denom > 0:
        h = h / (1.0 - tie / denom)
    p = 1.0 - _chi2_cdf(h, k - 1)
    return {"H": h, "df": k - 1, "p": p, "n": n}


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = _mean(xs), _mean(ys)
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    sxx = sum(v * v for v in dx)
    syy = sum(v * v for v in dy)
    sxy = sum(a * b for a, b in zip(dx, dy))
    if sxx <= 0 or syy <= 0:
        return None
    r = sxy / math.sqrt(sxx * syy)
    r = max(-1.0, min(1.0, r))
    if abs(r) >= 1.0:
        p = 0.0
    else:
        t = r * math.sqrt((n - 2) / (1.0 - r * r))
        p = 2.0 * (1.0 - _student_t_cdf(abs(t), n - 2))
    return {"r": r, "p": p, "n": n}


def describe(values):
    if not values:
        return None
    sorted_v = sorted(values)
    n = len(values)
    q1 = _quantile(sorted_v, 0.25)
    q3 = _quantile(sorted_v, 0.75)
    iqr = q3 - q1
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    outliers = [v for v in values if v < lower or v > upper]
    return {
        "n": n,
        "mean": _mean(values),
        "sd": math.sqrt(_variance(values)),
        "min": sorted_v[0],
        "max": sorted_v[-1],
        "median": _quantile(sorted_v, 0.5),
        "q1": q1,
        "q3": q3,
        "iqr": iqr,
        "lower_whisker": next((v for v in sorted_v if v >= lower), sorted_v[0]),
        "upper_whisker": next((v for v in reversed(sorted_v) if v <= upper), sorted_v[-1]),
        "outlier_count": len(outliers),
    }


# --------------------------------------------------------------------------- #
# Small utilities
# --------------------------------------------------------------------------- #
def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def _read_text(path):
    """Return (text, encoding) with BOM-safe fallback chain."""
    with open(path, "rb") as fh:
        raw = fh.read()
    if len(raw) > MAX_TEXT_BYTES:
        raise ValueError("file too large (>50MB): %s" % path)
    last = None
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError as exc:
            last = exc
    raise last if last else ValueError("undecodable file: %s" % path)


def _read_json(path):
    text, enc = _read_text(path)
    if text.lstrip().startswith("\ufeff"):
        text = text.lstrip()[1:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise


def _write_json(path, obj):
    _ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _fmt(v, nd=4):
    if v is None:
        return "null"
    if isinstance(v, float):
        if v != v:
            return "NaN"
        return ("%%.%df" % nd) % v
    return str(v)


# --------------------------------------------------------------------------- #
# Evidence map
# --------------------------------------------------------------------------- #
class EvidenceMap:
    def __init__(self):
        self.entries = []
        self._seen = set()

    def add(self, conclusion, claim_type, locator, quote, quote_source,
            method, confidence="medium", value=None):
        eid = "E-%03d" % (len(self.entries) + 1)
        self.entries.append({
            "id": eid,
            "conclusion": conclusion,
            "claim_type": claim_type,
            "locator": locator,
            "quote": quote,
            "quote_source": quote_source,
            "method": method,
            "confidence": confidence,
            "value": value,
        })
        return eid

    def dump(self, path):
        _write_json(path, {"schema_version": VERSION, "entries": self.entries})
        return [e["id"] for e in self.entries]


def _quote_cells(values, n=3):
    if not values:
        return ""
    head = [str(v) for v in values[:n]]
    return " | ".join(head)


def _find_sentence(text, keywords):
    """Return the first sentence containing any keyword, else empty string."""
    if not text or not keywords:
        return ""
    # Split on sentence boundaries while keeping CJK punctuation.
    import re
    parts = re.split(r"(?<=[。！？.!?\n])", text)
    for part in parts:
        for kw in keywords:
            if kw in part:
                return part.strip()
    # fallback: first line containing any keyword
    for line in text.splitlines():
        for kw in keywords:
            if kw in line:
                return line.strip()
    return ""


# --------------------------------------------------------------------------- #
# Input discovery
# --------------------------------------------------------------------------- #
def _parse_flags(args):
    flags = {"input": "/input", "output": "/app/output", "work": "/app/work",
             "primary": None, "group": None, "targets": []}
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--input" and i + 1 < len(args):
            flags["input"] = args[i + 1]; i += 2
        elif a == "--output" and i + 1 < len(args):
            flags["output"] = args[i + 1]; i += 2
        elif a == "--work" and i + 1 < len(args):
            flags["work"] = args[i + 1]; i += 2
        elif a == "--primary" and i + 1 < len(args):
            flags["primary"] = args[i + 1]; i += 2
        elif a == "--group" and i + 1 < len(args):
            flags["group"] = args[i + 1]; i += 2
        elif a == "--targets" and i + 1 < len(args):
            flags["targets"] = [t.strip() for t in args[i + 1].split(",") if t.strip()]; i += 2
        else:
            i += 1
    return flags


def _iter_files(root):
    for dirpath, _dirs, files in os.walk(root):
        for fn in sorted(files):
            full = os.path.join(dirpath, fn)
            yield full, fn.lower()


def _load_case(input_dir):
    case_path = os.path.join(input_dir, "case.json")
    task_path = os.path.join(input_dir, "task.md")
    case = None
    errors = []
    if os.path.isfile(case_path):
        try:
            case = _read_json(case_path)
        except Exception as exc:
            errors.append("case.json unreadable: %s" % exc)
    task_text = ""
    if os.path.isfile(task_path):
        try:
            task_text, _ = _read_text(task_path)
        except Exception as exc:
            errors.append("task.md unreadable: %s" % exc)

    case_id = None
    if isinstance(case, dict):
        for key in ("case_id", "challenge_id", "armiId", "id"):
            if case.get(key):
                case_id = str(case[key])
                break
    if not case_id:
        case_id = os.path.basename(os.path.abspath(input_dir)) or "input"
    contract = None
    if isinstance(case, dict) and isinstance(case.get("output_contract"), dict):
        contract = dict(case["output_contract"])
    elif isinstance(case, dict) and isinstance(case.get("output_contract"), list):
        contract = {k: None for k in case["output_contract"]}
    if not contract:
        contract = {k: None for k in DEFAULT_CONTRACT_KEYS}
    return case, case_id, contract, task_text, errors


def _read_csv(path):
    text, enc = _read_text(path)
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        delim = dialect.delimiter
    except csv.Error:
        delim = "," if "\t" not in sample else "\t"
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    return _table_from_rows(rows, enc)


def _read_json_table(path):
    data = _read_json(path)
    if isinstance(data, list):
        return _table_from_rows(_list_of_dicts_to_rows(data))
    if isinstance(data, dict):
        for key in ("data", "records", "rows", "values"):
            if isinstance(data.get(key), list):
                return _table_from_rows(_list_of_dicts_to_rows(data[key]))
        # not tabular (e.g. metadata) -> do not manufacture a fake table
        return None
    return None


def _list_of_dicts_to_rows(records):
    keys = []
    for rec in records:
        if isinstance(rec, dict):
            for k in rec:
                if k not in keys:
                    keys.append(k)
    rows = [list(keys)]
    for rec in records:
        if isinstance(rec, dict):
            rows.append([rec.get(k) for k in keys])
    return rows


def _table_from_rows(rows, encoding=None):
    if not rows:
        return None
    rows = [r for r in rows if r]
    if not rows:
        return None
    header = rows[0]
    body = rows[1:]
    # Header detection: if every first-row cell is non-numeric text while the
    # second row has some numeric cells, treat the first row as a header.
    def _looks_numeric(s):
        if s is None or (isinstance(s, str) and not s.strip()):
            return False
        try:
            float(str(s).strip().replace(",", ""))
            return True
        except (ValueError, TypeError):
            return False

    first_all_text = all(not _looks_numeric(c) for c in header)
    if not body:
        columns = ["col_%d" % i for i in range(len(header))]
        return {"columns": columns, "rows": [], "header": True, "encoding": encoding}
    second_has_num = any(_looks_numeric(c) for c in body[0])
    if first_all_text and second_has_num:
        columns = [str(c).strip() if c is not None else "col_%d" % i
                   for i, c in enumerate(header)]
        data = body
    else:
        columns = ["col_%d" % i for i in range(len(header))]
        data = rows  # no header, keep all rows
    return {"columns": columns, "rows": data, "header": first_all_text and second_has_num,
            "encoding": encoding}


def _read_sqlite(path):
    con = sqlite3.connect(path)
    try:
        cur = con.cursor()
        tables = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        out = []
        for t in tables:
            cols = [d[1] for d in cur.execute("PRAGMA table_info(%s)" % t)]
            data = [list(r) for r in cur.execute("SELECT * FROM %s" % t)]
            out.append({"table": t, "columns": cols, "rows": data})
        return out, tables
    finally:
        con.close()


def discover_data(input_dir):
    sources = []
    skip_basenames = {"case.json", "challenge.json", "task.json", "metadata.json"}
    for full, lower in _iter_files(input_dir):
        base = os.path.relpath(full, input_dir)
        if os.path.basename(lower) in skip_basenames:
            continue
        if lower.endswith((".csv", ".tsv", ".txt")):
            kind = "csv"
        elif lower.endswith(".json"):
            kind = "json"
        elif lower.endswith((".db", ".sqlite", ".sqlite3")):
            kind = "sqlite"
        else:
            continue
        sources.append({"path": full, "rel": base, "kind": kind})
    return sources


# --------------------------------------------------------------------------- #
# Stage implementations
# --------------------------------------------------------------------------- #
def stage_scaffold(flags, state):
    _ensure_dir(flags["output"])
    _ensure_dir(flags["work"])
    case, case_id, contract, task_text, errors = _load_case(flags["input"])
    change_log = [{
        "stage": "scaffold",
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "action": "wrote partial skeleton; contract keys set to null/[]",
    }]
    skeleton = {
        "case_id": case_id,
        "status": "partial",
        "output_contract": {k: ([] if v == [] else None) for k, v in contract.items()},
        "sources": [],
        "uncertainty": [],
        "change_log": change_log,
        "pending_human": [],
        "errors": list(errors),
        "degradations": [],
        "runtime": 0.0,
        "evidence": [],
        "artifacts": {"report": "/app/output/report.md", "figures": []},
    }
    # merge contract keys directly at top level for downstream checkers
    for k in contract:
        skeleton.setdefault(k, contract[k])
    _write_json(os.path.join(flags["output"], "result.json"), skeleton)
    state.update({"case": case, "case_id": case_id, "contract": contract,
                  "task_text": task_text, "result": skeleton})
    return skeleton


def _infer_types(rows, columns):
    coltypes = []
    for ci in range(len(columns)):
        numeric = 0
        nonempty = 0
        uniq = set()
        for row in rows:
            if ci >= len(row):
                continue
            cell = row[ci]
            if cell is None or (isinstance(cell, str) and not cell.strip()):
                continue
            nonempty += 1
            uniq.add(str(cell))
            s = str(cell).strip().replace(",", "")
            try:
                float(s)
                numeric += 1
            except ValueError:
                pass
        if nonempty == 0:
            coltypes.append("empty")
        elif numeric >= max(1, int(0.8 * nonempty)):
            # numeric-dominant, but very low cardinality (e.g. integer class labels)
            if len(uniq) <= 20 and len(uniq) < max(2, int(0.2 * nonempty)):
                coltypes.append("categorical")
            else:
                coltypes.append("numeric")
        else:
            coltypes.append("categorical")
    return coltypes


def _numeric_values(rows, ci):
    out = []
    for row in rows:
        if ci >= len(row):
            continue
        cell = row[ci]
        if cell is None or (isinstance(cell, str) and not cell.strip()):
            continue
        try:
            out.append(float(str(cell).strip().replace(",", "")))
        except ValueError:
            continue
    return out


def stage_profile(flags, state):
    sources = discover_data(flags["input"])
    if not sources:
        profile = {"input_dir": flags["input"], "case_id": state.get("case_id"),
                   "sources": [], "datasets": [], "note": "no data files found"}
        _write_json(os.path.join(flags["work"], "sources.json"), [])
        _write_json(os.path.join(flags["work"], "profile.json"), profile)
        state["profile"] = profile
        return profile

    inventory = []
    datasets = []
    for src in sources:
        item = {"path": src["rel"], "kind": src["kind"], "sha256": _sha256(src["path"])}
        try:
            if src["kind"] == "sqlite":
                tables, names = _read_sqlite(src["path"])
                item["tables"] = names
                for tb in tables:
                    cols = tb["columns"]
                    rows = tb["rows"]
                    coltypes = _infer_types(rows, cols)
                    colinfo = []
                    for ci, name in enumerate(cols):
                        vals = _numeric_values(rows, ci)
                        missing = sum(1 for r in rows if ci >= len(r) or r[ci] is None
                                      or (isinstance(r[ci], str) and not r[ci].strip()))
                        d = describe(vals) if coltypes[ci] == "numeric" else None
                        colinfo.append({
                            "name": name, "type": coltypes[ci], "missing": missing,
                            "unique": len(set(str(r[ci]) for r in rows if ci < len(r))),
                            "outlier_count": d["outlier_count"] if d else 0,
                            "sample": _quote_cells(vals[:3] if coltypes[ci] == "numeric"
                                                   else [r[ci] for r in rows[:3] if ci < len(r)]),
                        })
                    datasets.append({
                        "id": "D%d" % (len(datasets) + 1),
                        "path": "%s#table=%s" % (src["rel"], tb["table"]),
                        "kind": "sqlite", "table": tb["table"],
                        "rows": len(rows), "columns": colinfo,
                        "numeric_columns": [c["name"] for c in colinfo if c["type"] == "numeric"],
                        "categorical_columns": [c["name"] for c in colinfo if c["type"] == "categorical"],
                        "duplicate_rows": _count_duplicate_rows(rows),
                    })
            else:
                table = _read_csv(src["path"]) if src["kind"] == "csv" else _read_json_table(src["path"])
                if not table:
                    item["error"] = "unable to parse"
                    inventory.append(item)
                    continue
                cols = table["columns"]
                rows = table["rows"]
                coltypes = _infer_types(rows, cols)
                colinfo = []
                for ci, name in enumerate(cols):
                    vals = _numeric_values(rows, ci)
                    missing = sum(1 for r in rows if ci >= len(r) or r[ci] is None
                                  or (isinstance(r[ci], str) and not r[ci].strip()))
                    d = describe(vals) if coltypes[ci] == "numeric" else None
                    sample_vals = (vals[:3] if coltypes[ci] == "numeric"
                                   else [r[ci] for r in rows[:3] if ci < len(r)])
                    colinfo.append({
                        "name": name, "type": coltypes[ci], "missing": missing,
                        "unique": len(set(str(r[ci]) for r in rows if ci < len(r))),
                        "outlier_count": d["outlier_count"] if d else 0,
                        "sample": _quote_cells(sample_vals),
                    })
                datasets.append({
                    "id": "D%d" % (len(datasets) + 1),
                    "path": src["rel"], "kind": src["kind"],
                    "rows": len(rows), "columns": colinfo,
                    "header": table.get("header", True),
                    "encoding": table.get("encoding"),
                    "numeric_columns": [c["name"] for c in colinfo if c["type"] == "numeric"],
                    "categorical_columns": [c["name"] for c in colinfo if c["type"] == "categorical"],
                    "duplicate_rows": _count_duplicate_rows(rows),
                })
        except Exception as exc:
            item["error"] = "%s: %s" % (type(exc).__name__, exc)
        inventory.append(item)

    profile = {
        "input_dir": flags["input"],
        "case_id": state.get("case_id"),
        "sources": inventory,
        "datasets": datasets,
    }
    _write_json(os.path.join(flags["work"], "sources.json"), inventory)
    _write_json(os.path.join(flags["work"], "profile.json"), profile)
    state["profile"] = profile
    return profile


def _count_duplicate_rows(rows):
    seen = set()
    dup = 0
    for row in rows:
        key = tuple("" if c is None else str(c) for c in row)
        if key in seen:
            dup += 1
        else:
            seen.add(key)
    return dup


def _choose_dataset(profile, flags):
    ds = profile["datasets"]
    if not ds:
        return None
    if flags["primary"]:
        for d in ds:
            if d["path"] == flags["primary"] or d["path"].endswith(flags["primary"]):
                return d
    # prefer a non-SQLite tabular file, then the largest by rows
    best = min(ds, key=lambda d: (0 if d["kind"] != "sqlite" else 1, -d["rows"]))
    return best


def _choose_group(dataset, flags, task_text):
    cats = dataset["categorical_columns"]
    if flags["group"] and flags["group"] in [c["name"] for c in dataset["columns"]]:
        return flags["group"]
    if cats:
        # prefer names hinting at a class/label/group
        for pref in ("class", "species", "group", "label", "type", "category", "target"):
            for c in cats:
                if pref in c.lower():
                    return c
        # else lowest-cardinality categorical
        best = None
        for c in dataset["columns"]:
            if c["type"] == "categorical":
                if best is None or c["unique"] < best["unique"]:
                    best = c
        if best:
            return best["name"]
    return None


def _choose_targets(dataset, flags):
    nums = [c["name"] for c in dataset["columns"] if c["type"] == "numeric"]
    if not nums:
        return []
    if flags["targets"]:
        return [t for t in flags["targets"] if t in nums]
    # prefer a target-like name; else all numeric columns
    for pref in ("strength", "target", "label", "outcome", "result", "y", "value"):
        for c in nums:
            if pref in c.lower():
                return [c]
    return nums


def _read_rows(dataset, flags):
    if dataset["kind"] == "sqlite":
        path = os.path.join(flags["input"], dataset["path"].split("#")[0])
        tables, _ = _read_sqlite(path)
        for tb in tables:
            if tb["table"] == dataset["table"]:
                return tb["columns"], tb["rows"]
        return [], []
    path = os.path.join(flags["input"], dataset["path"])
    if dataset["kind"] == "json":
        table = _read_json_table(path)
    else:
        table = _read_csv(path)
    if not table:
        return [], []
    return table["columns"], table["rows"]


def stage_clean(flags, state):
    profile = state.get("profile") or stage_profile(flags, state)
    dataset = _choose_dataset(profile, flags)
    evidence = EvidenceMap()
    clean_meta = {"primary": None, "before_rows": 0, "after_rows": 0, "rules": [],
                  "columns_skipped": [], "outliers_flagged_not_removed": []}
    if not dataset:
        clean_meta["note"] = "no dataset to clean"
        _write_json(os.path.join(flags["work"], "clean_meta.json"), clean_meta)
        state["clean_meta"] = clean_meta
        return clean_meta

    clean_meta["primary"] = dataset["id"]
    columns, rows = _read_rows(dataset, flags)
    clean_meta["before_rows"] = len(rows)

    # R1: drop exact duplicate rows (keep first occurrence)
    seen = set()
    deduped = []
    dup_count = 0
    for row in rows:
        key = tuple("" if c is None else str(c) for c in row)
        if key in seen:
            dup_count += 1
            continue
        seen.add(key)
        deduped.append(row)

    locator = "data:%s#rows=all" % dataset["path"]
    quote = _find_sentence(state.get("task_text", ""),
                           ["重复", "去重", "duplicate", "重复记录"])
    eid = evidence.add(
        conclusion="drop exact duplicate rows: removed %d of %d rows" % (dup_count, len(rows)),
        claim_type="rule", locator=locator,
        quote=quote or _quote_cells([c for c in columns]),
        quote_source="task.md" if quote else dataset["path"],
        method="exact whole-row duplicate key, keep first occurrence",
        confidence="high", value={"removed": dup_count, "before": len(rows)})
    clean_meta["rules"].append({
        "id": "R1", "name": "drop exact duplicates", "applied": True,
        "rows_removed": dup_count, "evidence_id": eid,
        "quote": quote or "(no task text quote; rule derived from data)",
    })

    after = deduped
    clean_meta["after_rows"] = len(after)

    # Write cleaned table (with a generated header if none existed)
    header = list(columns)
    with open(os.path.join(flags["work"], "cleaned.csv"), "w", encoding="utf-8", newline="\n") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        for row in after:
            w.writerow(["" if c is None else c for c in row])

    # Flag outliers (do NOT remove)
    for ci, colmeta in enumerate(dataset["columns"]):
        if colmeta["type"] == "numeric" and colmeta["outlier_count"] > 0:
            vals = _numeric_values(after, ci)
            d = describe(vals)
            if d and d["outlier_count"] > 0:
                eid = evidence.add(
                    conclusion="flag IQR outliers on column %s: %d values outside 1.5*IQR (not removed)"
                               % (colmeta["name"], d["outlier_count"]),
                    claim_type="rule",
                    locator="data:%s#col=%s" % (dataset["path"], colmeta["name"]),
                    quote=_quote_cells(vals[:3]),
                    quote_source=dataset["path"],
                    method="Tukey fences: [Q1-1.5*IQR, Q3+1.5*IQR]; flag only",
                    confidence="high", value={"outlier_count": d["outlier_count"]})
                clean_meta["outliers_flagged_not_removed"].append(
                    {"column": colmeta["name"], "count": d["outlier_count"],
                     "evidence_id": eid})

    clean_meta["evidence_ids"] = evidence.dump(os.path.join(flags["work"], "evidence_clean.json"))
    _write_json(os.path.join(flags["work"], "clean_meta.json"), clean_meta)
    state["clean_meta"] = clean_meta
    state["evidence_clean"] = evidence
    return clean_meta


def stage_analyze(flags, state):
    profile = state.get("profile") or stage_profile(flags, state)
    clean_meta = state.get("clean_meta")
    dataset = _choose_dataset(profile, flags)
    evidence = EvidenceMap()
    stats = {"case_id": state.get("case_id"), "dataset": None,
             "descriptive": {}, "group_column": None, "group_tests": {},
             "correlations": [], "evidence_ids": []}
    if not dataset:
        stats["note"] = "no dataset"
        _write_json(os.path.join(flags["work"], "stats.json"), stats)
        state["stats"] = stats
        return stats

    stats["dataset"] = dataset["id"]
    columns, rows = _read_rows(dataset, flags)
    # use cleaned rows when available
    if clean_meta and clean_meta.get("after_rows") is not None:
        cleaned_path = os.path.join(flags["work"], "cleaned.csv")
        if os.path.isfile(cleaned_path):
            tbl = _read_csv(cleaned_path)
            columns, rows = tbl["columns"], tbl["rows"]

    num_idx = [i for i, c in enumerate(dataset["columns"]) if c["type"] == "numeric"]
    col_name = {i: dataset["columns"][i]["name"] for i in num_idx}

    for ci in num_idx:
        name = col_name[ci]
        vals = _numeric_values(rows, ci)
        d = describe(vals)
        if not d:
            stats["descriptive"][name] = {"n": 0, "note": "no numeric values"}
            continue
        stats["descriptive"][name] = d
        missing = sum(1 for r in rows if ci >= len(r) or r[ci] is None
                      or (isinstance(r[ci], str) and not r[ci].strip()))
        stats["descriptive"][name]["missing"] = missing
        evidence.add(
            conclusion="descriptive statistics for column %s (n=%d, mean=%s, sd=%s)"
                       % (name, d["n"], _fmt(d["mean"]), _fmt(d["sd"])),
            claim_type="observation",
            locator="data:%s#col=%s#rows=1..%d" % (dataset["path"], name, d["n"]),
            quote=_quote_cells(vals[:3]),
            quote_source=dataset["path"],
            method="mean/sd computed over all non-missing numeric values (sd denominator n-1)",
            confidence="high", value={"n": d["n"], "mean": d["mean"], "sd": d["sd"]})

    group_col = _choose_group(dataset, flags, state.get("task_text", ""))
    stats["group_column"] = group_col

    if group_col:
        gidx = None
        for i, c in enumerate(dataset["columns"]):
            if c["name"] == group_col:
                gidx = i
                break
        if gidx is not None:
            gnames = sorted({str(r[gidx]) for r in rows if gidx < len(r)
                             and r[gidx] is not None
                             and not (isinstance(r[gidx], str) and not r[gidx].strip())})
            stats["groups"] = gnames
            for ci in num_idx:
                name = col_name[ci]
                # per-group values for THIS numeric column only
                gv = {g: [] for g in gnames}
                for row in rows:
                    if gidx >= len(row) or ci >= len(row):
                        continue
                    g = row[gidx]
                    if g is None or (isinstance(g, str) and not g.strip()):
                        continue
                    try:
                        gv[str(g)].append(float(str(row[ci]).strip().replace(",", "")))
                    except ValueError:
                        pass
                gv = {g: vals for g, vals in gv.items() if vals}
                ordered = sorted(gv.keys())
                if not ordered:
                    continue
                per_group = {g: describe(gv[g]) for g in ordered}
                gvals = [gv[g] for g in ordered]
                if len(ordered) == 2:
                    wt = welch_t(gvals[0], gvals[1])
                    mw = mann_whitney(gvals[0], gvals[1])
                    res = {"test": "welch_t", "groups": per_group}
                    if wt:
                        res["welch"] = wt
                        evidence.add(
                            conclusion="Welch t-test %s vs %s on %s: t=%s, df=%s, p=%s, Cohen's d=%s"
                                       % (ordered[0], ordered[1], name, _fmt(wt["t"]),
                                          _fmt(wt["df"], 3), _fmt(wt["p"], 6), _fmt(wt["cohens_d"])),
                            claim_type="inference",
                            locator="data:%s#col=%s#group=%s" % (dataset["path"], name, group_col),
                            quote=_quote_cells(gvals[0][:2]) + " / " + _quote_cells(gvals[1][:2]),
                            quote_source=dataset["path"],
                            method="Welch unequal-variance two-sample t, two-tailed p via incomplete beta",
                            confidence="high", value=wt)
                    if mw:
                        res["mann_whitney"] = mw
                        evidence.add(
                            conclusion="Mann-Whitney U %s vs %s on %s: U=%s, p=%s (%s)"
                                       % (ordered[0], ordered[1], name, _fmt(mw["U"]),
                                          _fmt(mw["p"], 6), mw["method"]),
                            claim_type="inference",
                            locator="data:%s#col=%s#group=%s" % (dataset["path"], name, group_col),
                            quote=_quote_cells(gvals[0][:2]) + " / " + _quote_cells(gvals[1][:2]),
                            quote_source=dataset["path"],
                            method="Mann-Whitney U with average ranks%s" %
                                  (" exact DP" if mw["method"] == "exact" else " + normal approx"),
                            confidence="high" if mw["method"] == "exact" else "medium", value=mw)
                    stats["group_tests"][name] = res
                elif len(ordered) > 2:
                    av = oneway_anova(gvals)
                    kw = kruskal_wallis(gvals)
                    res = {"test": "anova", "groups": per_group}
                    if av:
                        res["anova"] = av
                        evidence.add(
                            conclusion="one-way ANOVA across %d groups on %s: F(%d,%d)=%s, p=%s, eta^2=%s"
                                       % (len(ordered), name, av["df_between"], av["df_within"],
                                          _fmt(av["F"]), _fmt(av["p"], 6), _fmt(av["eta_squared"])),
                            claim_type="inference",
                            locator="data:%s#col=%s#group=%s" % (dataset["path"], name, group_col),
                            quote=" | ".join(_quote_cells(gvals[i][:2]) for i in range(min(len(gvals), 3))),
                            quote_source=dataset["path"],
                            method="one-way ANOVA (between/within sum of squares), p via incomplete beta",
                            confidence="high", value=av)
                    if kw:
                        res["kruskal_wallis"] = kw
                        evidence.add(
                            conclusion="Kruskal-Wallis H across %d groups on %s: H=%s, df=%s, p=%s"
                                       % (len(ordered), name, _fmt(kw["H"]), kw["df"], _fmt(kw["p"], 6)),
                            claim_type="inference",
                            locator="data:%s#col=%s#group=%s" % (dataset["path"], name, group_col),
                            quote=" | ".join(_quote_cells(gvals[i][:2]) for i in range(min(len(gvals), 3))),
                            quote_source=dataset["path"],
                            method="Kruskal-Wallis rank test with tie correction, p via chi-square",
                            confidence="high", value=kw)
                    stats["group_tests"][name] = res

    # Correlations (Pearson) between numeric columns
    corr = []
    for i in range(len(num_idx)):
        for j in range(i + 1, len(num_idx)):
            xs = _numeric_values(rows, num_idx[i])
            ys = _numeric_values(rows, num_idx[j])
            # align pairwise on row position
            xv, yv = [], []
            for r in rows:
                if num_idx[i] < len(r) and num_idx[j] < len(r):
                    try:
                        a = float(str(r[num_idx[i]]).strip().replace(",", ""))
                        b = float(str(r[num_idx[j]]).strip().replace(",", ""))
                        xv.append(a); yv.append(b)
                    except ValueError:
                        pass
            pr = pearson(xv, yv)
            if not pr:
                continue
            pair = {"x": col_name[num_idx[i]], "y": col_name[num_idx[j]], **pr}
            corr.append(pair)
            evidence.add(
                conclusion="Pearson r(%s, %s)=%s, p=%s, n=%d"
                           % (pair["x"], pair["y"], _fmt(pr["r"]), _fmt(pr["p"], 6), pr["n"]),
                claim_type="inference",
                locator="data:%s#col=%s#col=%s" % (dataset["path"], pair["x"], pair["y"]),
                quote=_quote_cells(xv[:2]) + " / " + _quote_cells(yv[:2]),
                quote_source=dataset["path"],
                method="Pearson product-moment r, two-tailed p via t(n-2)",
                confidence="high", value=pr)
    corr.sort(key=lambda c: -abs(c["r"]))
    stats["correlations"] = corr

    stats["evidence_ids"] = evidence.dump(os.path.join(flags["work"], "evidence_analyze.json"))
    _write_json(os.path.join(flags["work"], "stats.json"), stats)
    state["stats"] = stats
    state["evidence_analyze"] = evidence
    return stats


def stage_render(flags, state):
    stats = state.get("stats") or stage_analyze(flags, state)
    evidence = state.get("evidence_analyze") or EvidenceMap()
    _ensure_dir(flags["output"])
    figures = []

    # Figure 1: group box plot (first group test result)
    if stats.get("group_tests"):
        target = next(iter(stats["group_tests"]))
        res = stats["group_tests"][target]
        svg_path = os.path.join(flags["output"], "figure_group.svg")
        _svg_boxplot(res, target, stats.get("group_column", "group"), svg_path)
        figures.append(os.path.basename(svg_path))
        evidence.add(
            conclusion="figure_group.svg boxplot for %s grouped by %s (median/Q1/Q3/whiskers from stats.json)"
                       % (target, stats.get("group_column")),
            claim_type="artifact",
            locator="work:stats.json#group_tests.%s.groups" % target,
            quote=_quote_cells([g["median"] for g in res["groups"].values()][:3]),
            quote_source="work/stats.json",
            method="SVG drawn from stats.json quartiles; no hardcoded numbers",
            confidence="high")

    # Figure 2: correlation heat map
    if stats.get("correlations"):
        svg_path = os.path.join(flags["output"], "figure_corr.svg")
        _svg_corr_heatmap(stats["correlations"], svg_path)
        figures.append(os.path.basename(svg_path))
        evidence.add(
            conclusion="figure_corr.svg heatmap of %d Pearson correlations from stats.json"
                       % len(stats["correlations"]),
            claim_type="artifact", locator="work:stats.json#correlations",
            quote=_quote_cells([c["r"] for c in stats["correlations"][:3]]),
            quote_source="work/stats.json",
            method="SVG heatmap colored by r; values from stats.json",
            confidence="high")

    report_path = os.path.join(flags["output"], "report.md")
    _render_report(stats, clean_meta=state.get("clean_meta"), path=report_path)

    # Merge clean-stage + analyze-stage evidence (figure entries were added to
    # the analyze map above) into one evidence_map, renumbering deterministically.
    merged = EvidenceMap()
    for ev in (state.get("evidence_clean"), evidence):
        if ev:
            for e in ev.entries:
                merged.entries.append(e)
    for i, e in enumerate(merged.entries):
        e["id"] = "E-%03d" % (i + 1)
    ev_ids = merged.dump(os.path.join(flags["output"], "evidence_map.json"))
    state["evidence_map"] = merged
    state["artifacts"] = {"report": report_path, "figures": figures,
                          "evidence_map": os.path.join(flags["output"], "evidence_map.json")}
    return {"report": report_path, "figures": figures, "evidence_ids": ev_ids}


def _svg_boxplot(res, target, group_col, path):
    groups = res.get("groups", {})
    gnames = sorted(groups.keys())
    if not gnames:
        return
    w = 120 + 90 * len(gnames)
    h = 360
    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
                 'viewBox="0 0 %d %d">' % (w, h, w, h))
    lines.append('<rect width="100%%" height="100%%" fill="white"/>')
    # gather numeric bounds
    all_q = []
    for g in gnames:
        d = groups[g]
        for k in ("lower_whisker", "q1", "median", "q3", "upper_whisker"):
            v = d.get(k)
            if isinstance(v, (int, float)) and v == v:
                all_q.append(v)
    if not all_q:
        lines.append('<text x="20" y="30" font-size="14">no numeric data</text>')
        lines.append('</svg>')
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        return
    lo = min(all_q)
    hi = max(all_q)
    span = (hi - lo) or 1.0
    lo -= 0.1 * span
    hi += 0.1 * span

    def _y(v):
        return 40 + (1.0 - (v - lo) / (hi - lo)) * 280

    lines.append('<text x="20" y="20" font-size="14" font-family="sans-serif">%s by %s</text>'
                 % (_xml_escape(target), _xml_escape(group_col)))
    # y axis
    lines.append('<line x1="60" y1="40" x2="60" y2="320" stroke="black"/>')
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        v = lo + frac * (hi - lo)
        yy = _y(v)
        lines.append('<line x1="55" y1="%d" x2="60" y2="%d" stroke="black"/>' % (round(yy), round(yy)))
        lines.append('<text x="40" y="%d" font-size="10" text-anchor="end">%s</text>'
                     % (round(yy + 3), _fmt(v, 3)))
    for gi, g in enumerate(gnames):
        d = groups[g]
        cx = 100 + 90 * gi
        q1 = _y(d.get("q1", lo))
        q3 = _y(d.get("q3", lo))
        med = _y(d.get("median", lo))
        lw = _y(d.get("lower_whisker", lo))
        uw = _y(d.get("upper_whisker", lo))
        lines.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="black"/>'
                     % (cx, round(lw), cx, round(uw)))
        lines.append('<rect x="%d" y="%d" width="40" height="%d" fill="#dbeafe" stroke="black"/>'
                     % (cx - 20, round(min(q1, q3)), max(1, round(abs(q3 - q1)))))
        lines.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="black" stroke-width="2"/>'
                     % (cx - 20, round(med), cx + 20, round(med)))
        lines.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="black"/>'
                     % (cx - 10, round(lw), cx + 10, round(lw)))
        lines.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="black"/>'
                     % (cx - 10, round(uw), cx + 10, round(uw)))
        lines.append('<text x="%d" y="340" font-size="11" text-anchor="middle">%s (n=%s)</text>'
                     % (cx, _xml_escape(g), d.get("n", 0)))
    lines.append('</svg>')
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _svg_corr_heatmap(corrs, path):
    if not corrs:
        return
    names = sorted(set([c["x"] for c in corrs] + [c["y"] for c in corrs]))
    cell = 28
    label_w = 140
    w = label_w + cell * len(names)
    h = 40 + cell * len(names)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">'
             % (w, h, w, h),
             '<rect width="100%%" height="100%%" fill="white"/>']
    idx = {n: i for i, n in enumerate(names)}
    mat = {}
    for c in corrs:
        mat[(idx[c["x"]], idx[c["y"]])] = c["r"]
        mat[(idx[c["y"]], idx[c["x"]])] = c["r"]
    for i, n in enumerate(names):
        lines.append('<text x="%d" y="%d" font-size="9">%s</text>'
                     % (label_w + i * cell + 2, 12, _xml_escape(n[:18])))
        lines.append('<text x="%d" y="%d" font-size="9" text-anchor="end">%s</text>'
                     % (label_w - 4, 40 + i * cell + cell - 8, _xml_escape(n[:18])))
    for i in range(len(names)):
        for j in range(len(names)):
            if i == j:
                color = "#666666"
            else:
                r = mat.get((i, j), 0.0)
                color = _heat_color(r)
            lines.append('<rect x="%d" y="%d" width="%d" height="%d" fill="%s" stroke="#dddddd"/>'
                         % (label_w + j * cell, 40 + i * cell, cell, cell, color))
            if i != j:
                r = mat.get((i, j), 0.0)
                lines.append('<text x="%d" y="%d" font-size="7" text-anchor="middle">%s</text>'
                             % (label_w + j * cell + cell // 2, 40 + i * cell + cell // 2 + 3,
                                _fmt(r, 2)))
    lines.append('</svg>')
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _heat_color(r):
    if r >= 0:
        return "#%02x%02x%02x" % (255, int(255 * (1 - r)), int(255 * (1 - r)))
    return "#%02x%02x%02x" % (int(255 * (1 + r)), int(255 * (1 + r)), 255)


def _xml_escape(s):
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _render_report(stats, clean_meta, path):
    lines = []
    lines.append("# 科研数据分析报告")
    lines.append("")
    lines.append("> 本报告全部数字来自 `work/stats.json` 与 `work/clean_meta.json`，可回算；"
                 "证据逐条见 `/app/output/evidence_map.json`。")
    lines.append("")
    lines.append("## 1. 数据与清洗")
    lines.append("")
    if clean_meta:
        lines.append("- 主表：`%s`" % clean_meta.get("primary", "N/A"))
        lines.append("- 原始行数：%s；清洗后行数：%s；删除重复行：%s"
                    % (_fmt(clean_meta.get("before_rows")), _fmt(clean_meta.get("after_rows")),
                       _fmt(clean_meta.get("before_rows", 0) - clean_meta.get("after_rows", 0))))
        for r in clean_meta.get("rules", []):
            lines.append("- 规则 %s（%s）：删行 %s" % (r.get("id"), r.get("name"), r.get("rows_removed")))
        for o in clean_meta.get("outliers_flagged_not_removed", []):
            lines.append("- 异常值（仅标记不删除）：列 `%s`，%s 个" % (o.get("column"), o.get("count")))
    lines.append("")
    lines.append("## 2. 描述统计")
    lines.append("")
    lines.append("| 列 | n | 缺失 | 均值 | 标准差 | 中位数 | Q1 | Q3 | 异常数 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for name, d in stats.get("descriptive", {}).items():
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |"
                     % (_xml_escape(name), d.get("n"), d.get("missing", 0),
                        _fmt(d.get("mean")), _fmt(d.get("sd")), _fmt(d.get("median")),
                        _fmt(d.get("q1")), _fmt(d.get("q3")), d.get("outlier_count", 0)))
    lines.append("")
    if stats.get("group_tests"):
        lines.append("## 3. 组间比较（分组列：`%s`）" % stats.get("group_column"))
        lines.append("")
        for name, res in stats["group_tests"].items():
            lines.append("### 目标列 `%s`" % name)
            lines.append("")
            if "welch" in res:
                wt = res["welch"]
                lines.append("- Welch t 检验：t=%s, df=%s, p=%s, Cohen's d=%s"
                             % (_fmt(wt["t"]), _fmt(wt["df"], 3), _fmt(wt["p"], 6),
                                _fmt(wt["cohens_d"])))
            if "mann_whitney" in res:
                mw = res["mann_whitney"]
                lines.append("- Mann-Whitney U 检验：U=%s, p=%s（%s）"
                             % (_fmt(mw["U"]), _fmt(mw["p"], 6), mw.get("method")))
            if "anova" in res:
                av = res["anova"]
                lines.append("- 单因素 ANOVA：F(%d,%d)=%s, p=%s, η²=%s"
                             % (av["df_between"], av["df_within"], _fmt(av["F"]),
                                _fmt(av["p"], 6), _fmt(av["eta_squared"])))
            if "kruskal_wallis" in res:
                kw = res["kruskal_wallis"]
                lines.append("- Kruskal-Wallis 检验：H=%s, df=%s, p=%s"
                             % (_fmt(kw["H"]), kw["df"], _fmt(kw["p"], 6)))
            lines.append("")
            lines.append("| 组 | n | 均值 | 标准差 | 中位数 | Q1 | Q3 |")
            lines.append("|---|---|---|---|---|---|---|")
            for g in sorted(res["groups"].keys()):
                d = res["groups"][g]
                lines.append("| %s | %s | %s | %s | %s | %s | %s |"
                             % (_xml_escape(g), d.get("n"), _fmt(d.get("mean")),
                                _fmt(d.get("sd")), _fmt(d.get("median")),
                                _fmt(d.get("q1")), _fmt(d.get("q3"))))
            lines.append("")
    if stats.get("correlations"):
        lines.append("## 4. 相关分析（Pearson，按 |r| 降序）")
        lines.append("")
        lines.append("| 变量 X | 变量 Y | r | p | n |")
        lines.append("|---|---|---|---|---|")
        for c in stats["correlations"][:20]:
            lines.append("| %s | %s | %s | %s | %s |"
                         % (_xml_escape(c["x"]), _xml_escape(c["y"]),
                            _fmt(c["r"]), _fmt(c["p"], 6), c["n"]))
        lines.append("")
    lines.append("## 5. 口径与前提")
    lines.append("")
    lines.append("- 描述统计：标准差按 n−1；四分位用线性插值；异常值用 Tukey 1.5×IQR 判定且只标记不删除。")
    lines.append("- 组间检验：2 组用 Welch（不假设等方差）+ Mann-Whitney U；≥3 组用 ANOVA + Kruskal-Wallis；"
                 "正态性未单独验证时以非参数检验为主，参数检验作参考。")
    lines.append("- 相关：Pearson 积矩相关，双尾 p 由 t(n−2) 近似。")
    lines.append("- 全部 p 值、效应量与图元数值均可指回 `work/stats.json` 与 `evidence_map.json`。")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))


def stage_finalize(flags, state):
    result_path = os.path.join(flags["output"], "result.json")
    if os.path.isfile(result_path):
        result = json.load(open(result_path, encoding="utf-8"))
    else:
        result = {"case_id": state.get("case_id"), "status": "partial"}

    contract = state.get("contract") or {k: None for k in DEFAULT_CONTRACT_KEYS}
    # backfill contract fields from artifacts
    analysis = {}
    if state.get("stats"):
        analysis = {"descriptive": state["stats"].get("descriptive"),
                    "group_tests": state["stats"].get("group_tests"),
                    "correlations": state["stats"].get("correlations")}
    quality = {
        "profile": "work/profile.json",
        "clean_meta": "work/clean_meta.json",
        "stats": "work/stats.json",
        "sources": "work/sources.json",
        "evidence_map": "evidence_map.json",
    }
    anomalies = []
    if state.get("clean_meta"):
        anomalies = state["clean_meta"].get("outliers_flagged_not_removed", [])

    for key in contract:
        if key == "analysis_results":
            result[key] = analysis
        elif key == "quality_evidence":
            result[key] = quality
        elif key == "anomaly_notes":
            result[key] = anomalies
        else:
            result.setdefault(key, None)

    # determine status honestly
    has_data = bool(state.get("profile") and state["profile"].get("datasets"))
    has_stats = bool(state.get("stats"))
    has_report = bool(state.get("artifacts") and state["artifacts"].get("report")
                      and os.path.isfile(state["artifacts"]["report"]))
    errors = list(result.get("errors") or [])
    if not os.path.isfile(os.path.join(flags["input"], "case.json")):
        status = "input_error"
        errors.append("missing /input/case.json")
    elif not has_data:
        status = "insufficient_evidence"
    elif has_stats and has_report:
        status = "ok"
    else:
        status = "partial"

    # sources: evidence-map based locator + quote pairs
    sources = []
    for ev in (state.get("evidence_map") or EvidenceMap()).entries:
        sources.append({"locator": ev["locator"], "quote": ev["quote"],
                        "claim_type": ev["claim_type"], "id": ev["id"]})

    uncertainty = list(result.get("uncertainty") or [])
    if state.get("stats") and state["stats"].get("group_tests"):
        uncertainty.append("parametric tests assume approximate normality/independence; "
                           "non-parametric tests are reported alongside as robustness checks.")
    if state.get("profile"):
        deg = []
        for src in state["profile"].get("sources", []):
            if src.get("error"):
                deg.append("source %s: %s" % (src.get("path"), src["error"]))
        if deg:
            result["degradations"] = deg

    change_log = list(result.get("change_log") or [])
    change_log.append({
        "stage": "finalize",
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "action": "finalized status=%s; backfilled %d contract fields; %d evidence entries"
                  % (status, len(contract),
                     len((state.get("evidence_map") or EvidenceMap()).entries)),
    })

    pending = list(result.get("pending_human") or [])
    if status in ("input_error", "insufficient_evidence"):
        pending.append("manual check: missing or unreadable input materials")
    if state.get("profile") and not state["profile"].get("datasets"):
        pending.append("no tabular/SQLite data file was discovered under /input")

    runtime = result.get("runtime", 0.0)
    result.update({
        "case_id": state.get("case_id") or result.get("case_id"),
        "status": status,
        "sources": sources,
        "uncertainty": uncertainty,
        "change_log": change_log,
        "pending_human": pending,
        "errors": errors,
        "runtime": runtime,
        "evidence": (state.get("evidence_map") or EvidenceMap()).dump(
            os.path.join(flags["output"], "evidence_map.json")) if state.get("evidence_map") else [],
        "artifacts": state.get("artifacts") or result.get("artifacts", {}),
    })
    _write_json(result_path, result)
    state["result"] = result
    return result


# --------------------------------------------------------------------------- #
# run() dispatcher
# --------------------------------------------------------------------------- #
def run(cmd, args):
    flags = _parse_flags(args)
    state = {"case_id": None}
    start = time.time()

    def _wrap(fn, stage):
        try:
            fn(flags, state)
        except Exception as exc:
            # record and continue; never leave /app/output empty
            _ensure_dir(flags["output"])
            rp = os.path.join(flags["output"], "result.json")
            try:
                if os.path.isfile(rp):
                    res = json.load(open(rp, encoding="utf-8"))
                else:
                    res = {"case_id": state.get("case_id") or "unknown", "status": "partial",
                           "errors": [], "degradations": [], "change_log": [],
                           "uncertainty": [], "pending_human": []}
                res.setdefault("errors", []).append("%s: %s: %s" % (stage, type(exc).__name__, exc))
                res.setdefault("change_log", []).append({
                    "stage": stage,
                    "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "action": "error recorded: %s" % exc})
                _write_json(rp, res)
            except Exception:
                pass

    stages = {
        "scaffold": stage_scaffold,
        "profile": stage_profile,
        "clean": stage_clean,
        "analyze": stage_analyze,
        "render": stage_render,
        "finalize": stage_finalize,
    }
    if cmd == "full":
        for name in ("scaffold", "profile", "clean", "analyze", "render", "finalize"):
            _wrap(stages[name], name)
    else:
        _wrap(stages[cmd], cmd)

    # always stamp runtime into result.json
    rp = os.path.join(flags["output"], "result.json")
    try:
        if os.path.isfile(rp):
            res = json.load(open(rp, encoding="utf-8"))
            res["runtime"] = round(time.time() - start, 3)
            _write_json(rp, res)
    except Exception:
        pass
    return 0
