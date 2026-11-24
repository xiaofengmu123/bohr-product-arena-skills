"""core_impl.py - deterministic, standard-library-only research data analysis.

Pipeline: scaffold -> profile -> clean+analyze -> render -> finalize.
Each stage reads/writes work/state.json so it can be re-run incrementally and
is idempotent. No numpy/scipy/pandas/matplotlib are required; statistics are
implemented from first principles (incomplete beta/gamma), and figures are
hand-written SVG. The pipeline never fabricates results: missing data,
unparseable inputs and failed tests are recorded in errors/degradations and
reflected in the final status.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import sqlite3
import sys
import time
import traceback
from xml.sax.saxutils import escape as _xml_escape

# --------------------------------------------------------------------------
# Small numeric / statistics helpers (standard library only)
# --------------------------------------------------------------------------

_G = (76.18009172947146, -86.50532032941677, 24.01409824083091,
      -1.231739572450155, 0.1208650973866179e-2, -0.5395239384953e-5)


def ln_gamma(x: float) -> float:
    y = x
    tmp = x + 5.5
    tmp -= (x + 0.5) * math.log(tmp)
    ser = 1.000000000190015
    for c in _G:
        y += 1.0
        ser += c / y
    return -tmp + math.log(2.5066282746310005 * ser / x)


def _gser(a: float, x: float) -> float:
    gln = ln_gamma(a)
    ap, s, d = a, 1.0 / a, 1.0 / a
    for _ in range(300):
        ap += 1.0
        d *= x / ap
        s += d
        if abs(d) < abs(s) * 1e-14:
            break
    return s * math.exp(-x + a * math.log(x) - gln)


def _gcf(a: float, x: float) -> float:
    gln = ln_gamma(a)
    b = x + 1.0 - a
    c = 1.0 / 1e-30
    d = 1.0 / b
    h = d
    for i in range(1, 300):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < 1e-30:
            d = 1e-30
        c = b + an / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return math.exp(-x + a * math.log(x) - gln) * h


def gammp(a: float, x: float) -> float:
    if x < 0.0 or a <= 0.0:
        return float("nan")
    if x < a + 1.0:
        return _gser(a, x)
    return 1.0 - _gcf(a, x)


def gammq(a: float, x: float) -> float:
    if x < 0.0 or a <= 0.0:
        return float("nan")
    if x < a + 1.0:
        return 1.0 - _gser(a, x)
    return _gcf(a, x)


def betacf(a: float, b: float, x: float) -> float:
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return h


def betai(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(ln_gamma(a + b) - ln_gamma(a) - ln_gamma(b)
                  + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * betacf(a, b, x) / a
    return 1.0 - bt * betacf(b, a, 1.0 - x) / b


def normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def t_two_tailed(t: float, df: float) -> float:
    if df <= 0.0 or t < 0.0:
        return float("nan")
    x = df / (df + t * t)
    return betai(df / 2.0, 0.5, x)


def f_cdf(f: float, d1: float, d2: float) -> float:
    if f <= 0.0:
        return 0.0
    x = d1 * f / (d1 * f + d2)
    return betai(d1 / 2.0, d2 / 2.0, x)


def chi2_cdf(x: float, k: float) -> float:
    if x < 0.0:
        return 0.0
    return gammp(k / 2.0, x / 2.0)


def _quantile(s: list, p: float) -> float:
    n = len(s)
    if n == 0:
        return float("nan")
    idx = (n - 1) * p
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return s[lo]
    w = idx - lo
    return s[lo] * (1.0 - w) + s[hi] * w


def desc(vals) -> dict:
    """Descriptive statistics for a non-empty list of numbers."""
    n = len(vals)
    if n == 0:
        return None
    m = sum(vals) / n
    if n > 1:
        var = sum((x - m) ** 2 for x in vals) / (n - 1)
        sd = math.sqrt(var)
    else:
        var = sd = 0.0
    s = sorted(vals)
    return {
        "n": n, "mean": m, "sd": sd, "var": var,
        "median": _quantile(s, 0.5), "q1": _quantile(s, 0.25), "q3": _quantile(s, 0.75),
        "min": s[0], "max": s[-1], "se": sd / math.sqrt(n), "sum": sum(vals),
    }


def welch_t(a, b):
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return None
    ma, mb = sum(a) / na, sum(b) / nb
    va = sum((x - ma) ** 2 for x in a) / (na - 1)
    vb = sum((x - mb) ** 2 for x in b) / (nb - 1)
    se = math.sqrt(va / na + vb / nb)
    if se == 0.0:
        if ma == mb:
            t, df, p = 0.0, float(na + nb - 2), 1.0
        else:
            t, df, p = float("inf"), float(na + nb - 2), 0.0
    else:
        t = (ma - mb) / se
        df = (va / na + vb / nb) ** 2 / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
        p = t_two_tailed(abs(t), df)
    sp = math.sqrt(((na - 1) * va + (nb - 1) * vb) / (na + nb - 2)) if na + nb > 2 else 0.0
    d = (ma - mb) / sp if sp > 0 else 0.0
    return {"test": "welch_t", "t": t, "df": df, "p": p, "mean1": ma, "mean2": mb,
            "n1": na, "n2": nb, "effect": "cohens_d", "effect_size": d}


def _ranks(pairs):
    n = len(pairs)
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    return ranks


def _tie_sum(pairs):
    n = len(pairs)
    s = 0.0
    i = 0
    while i < n:
        j = i
        while j + 1 < n and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        t = j - i + 1
        s += t ** 3 - t
        i = j + 1
    return s


def mann_whitney(a, b):
    na, nb = len(a), len(b)
    if na == 0 or nb == 0:
        return None
    pairs = sorted([(x, 0) for x in a] + [(x, 1) for x in b], key=lambda p: p[0])
    n = na + nb
    ranks = _ranks(pairs)
    r1 = sum(ranks[k] for k in range(n) if pairs[k][1] == 0)
    u1 = r1 - na * (na + 1) / 2.0
    u2 = na * nb - u1
    U = min(u1, u2)
    mu = na * nb / 2.0
    var = na * nb / 12.0 * ((n + 1) - _tie_sum(pairs) / (n * (n - 1)))
    z = (U - mu) / math.sqrt(var) if var > 0 else 0.0
    p = 2.0 * (1.0 - normal_cdf(abs(z)))
    rbc = 1.0 - 2.0 * U / (na * nb)
    return {"test": "mann_whitney_u", "U": U, "z": z, "p": p,
            "n1": na, "n2": nb, "effect": "rank_biserial", "effect_size": rbc}


def anova(groups):
    ns = [len(g) for g in groups]
    k = len(groups)
    N = sum(ns)
    if k < 2 or any(n < 1 for n in ns):
        return None
    grand = sum(sum(g) for g in groups) / N
    ssb = 0.0
    for g, n in zip(groups, ns):
        ssb += n * (sum(g) / n - grand) ** 2
    ssw = sum(sum((x - sum(g) / len(g)) ** 2 for x in g) for g in groups)
    dfb, dfw = k - 1, N - k
    if dfw <= 0 or ssw <= 0.0:
        return None
    msb, msw = ssb / dfb, ssw / dfw
    F = msb / msw
    p = 1.0 - f_cdf(F, dfb, dfw)
    eta2 = ssb / (ssb + ssw) if (ssb + ssw) > 0 else 0.0
    return {"test": "one_way_anova", "F": F, "df_between": dfb, "df_within": dfw,
            "p": p, "n": N, "k": k, "effect": "eta_squared", "effect_size": eta2,
            "group_means": [sum(g) / len(g) for g in groups]}


def kruskal_wallis(groups):
    flat = []
    for gi, g in enumerate(groups):
        for x in g:
            flat.append((x, gi))
    if not flat:
        return None
    flat.sort(key=lambda p: p[0])
    n = len(flat)
    ranks = _ranks(flat)
    R = [0.0] * len(groups)
    ns = [0] * len(groups)
    for idx, (_, gi) in enumerate(flat):
        R[gi] += ranks[idx]
        ns[gi] += 1
    H = 12.0 / (n * (n + 1)) * sum(R[i] ** 2 / ns[i] for i in range(len(groups)) if ns[i] > 0) - 3 * (n + 1)
    ties = _tie_sum(flat)
    if n ** 3 - n > 0 and ties > 0:
        H /= 1.0 - ties / (n ** 3 - n)
    return {"test": "kruskal_wallis", "H": H, "df": len(groups) - 1,
            "p": 1.0 - chi2_cdf(H, len(groups) - 1), "n": n, "k": len(groups)}


def pearson(x, y):
    n = len(x)
    if n < 3:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    sxx = sum((xi - mx) ** 2 for xi in x)
    syy = sum((yi - my) ** 2 for yi in y)
    if sxx <= 0.0 or syy <= 0.0:
        r, t, p = 0.0, None, 1.0
    else:
        r = max(-1.0, min(1.0, sxy / math.sqrt(sxx * syy)))
        if abs(r) >= 1.0:
            t, p = None, 0.0
        else:
            t = r * math.sqrt((n - 2) / (1 - r * r))
            p = t_two_tailed(abs(t), n - 2)
    return {"test": "pearson", "r": r, "r2": r * r, "t": t, "df": n - 2, "p": p, "n": n}


def iqr_outliers(vals):
    s = sorted(vals)
    q1, q3 = _quantile(s, 0.25), _quantile(s, 0.75)
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    flags = [x < lo or x > hi for x in vals]
    return flags, lo, hi


# --------------------------------------------------------------------------
# Input discovery and parsing (tolerant: encoding / format / sqlite)
# --------------------------------------------------------------------------

DATA_EXTS = (".csv", ".tsv", ".json", ".db", ".sqlite", ".sqlite3", ".txt")
META_NAMES = {"case.json", "task.md", "notes.md", "readme.md", "resources.yaml"}


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path: str):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "latin-1", "gbk"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1(replace)"


def is_number(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return True
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return False
        try:
            float(s)
            return True
        except ValueError:
            return False
    return False


def to_float(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def missing(v):
    return v is None or (isinstance(v, str) and v.strip() == "")


def discover_inputs(input_dir: str):
    """Return dict of data source paths, excluding case/task metadata."""
    found = []
    for root, _dirs, files in os.walk(input_dir):
        for fn in files:
            low = fn.lower()
            if low in META_NAMES:
                continue
            if low.endswith(DATA_EXTS):
                found.append(os.path.join(root, fn))
    return sorted(found)


def read_case(input_dir: str):
    """Read case.json (if present) and task.md. Never raises."""
    case_path = os.path.join(input_dir, "case.json")
    task_path = os.path.join(input_dir, "task.md")
    case_id, contract_keys, contract_note = None, None, None
    errors, degradations = [], []
    case_raw = None
    if os.path.exists(case_path):
        text, enc = read_text(case_path)
        try:
            case_raw = json.loads(text)
        except Exception as e:  # noqa: BLE001
            errors.append({"stage": "scaffold", "message": "case.json unparseable", "detail": str(e)})
            case_raw = None
        if enc not in ("utf-8", "utf-8-sig"):
            degradations.append({"stage": "scaffold", "message": f"case.json decoded as {enc}"})
    else:
        errors.append({"stage": "scaffold", "message": "case.json missing"})

    if isinstance(case_raw, dict):
        for k in ("case_id", "challenge_id", "armiId", "id"):
            if case_raw.get(k):
                case_id = str(case_raw[k])
                break
        oc = case_raw.get("output_contract") or case_raw.get("outputContract")
        if isinstance(oc, dict) and oc:
            contract_keys = list(oc.keys())
        elif isinstance(oc, list) and oc:
            contract_keys = [str(x) for x in oc]
    if not case_id:
        case_id = os.path.basename(os.path.abspath(input_dir)) or "unknown"
        degradations.append({"stage": "scaffold", "message": "case_id not found; used input directory name"})
    if not contract_keys:
        contract_keys = ["analysis_results", "quality_evidence", "anomaly_notes"]
        degradations.append({"stage": "scaffold",
                             "message": "no output_contract in case.json; used default contract keys"})

    task_text = ""
    if os.path.exists(task_path):
        task_text, _ = read_text(task_path)

    return {"case_id": case_id, "contract_keys": contract_keys,
            "task_text": task_text, "errors": errors, "degradations": degradations,
            "case_raw": case_raw if isinstance(case_raw, dict) else {}}


def _guess_delim(name, sample):
    tabs = sample.count("\t")
    commas = sample.count(",")
    if name.lower().endswith(".tsv") or (tabs > 0 and tabs >= commas):
        return "\t"
    return ","


def load_table(path: str):
    """Load one tabular source into {name, path, kind, columns, rows, note}."""
    base = os.path.basename(path)
    low = base.lower()
    if low.endswith((".db", ".sqlite", ".sqlite3")):
        return _load_sqlite(path, base)
    if low.endswith(".json"):
        return _load_json(path, base)
    return _load_delimited(path, base)


def _load_delimited(path, base):
    text, enc = read_text(path)
    delim = _guess_delim(base, text[:4096])
    rows_raw = list(csv.reader(io.StringIO(text), delimiter=delim))
    rows_raw = [r for r in rows_raw if any(cell.strip() for cell in r)]
    if not rows_raw:
        return {"name": base, "path": path, "kind": "csv", "columns": [], "rows": [],
                "note": "no non-empty rows", "encoding": enc}
    header = [c.strip() for c in rows_raw[0]]
    # dedupe empty header names
    seen = {}
    for i, h in enumerate(header):
        if h == "":
            h = f"col_{i}"
        if h in seen:
            seen[h] += 1
            h = f"{h}_{seen[h]}"
        else:
            seen[h] = 0
        header[i] = h
    rows = []
    for raw in rows_raw[1:]:
        row = {}
        for i, h in enumerate(header):
            row[h] = raw[i].strip() if i < len(raw) else ""
        rows.append(row)
    return {"name": base, "path": path, "kind": "csv", "columns": header,
            "rows": rows, "note": f"delimiter={delim!r}", "encoding": enc}


def _load_json(path, base):
    text, enc = read_text(path)
    try:
        data = json.loads(text)
    except Exception as e:  # noqa: BLE001
        return {"name": base, "path": path, "kind": "json", "columns": [], "rows": [],
                "note": f"unparseable: {e}", "encoding": enc}
    if isinstance(data, dict):
        for k in ("data", "rows", "records", "items"):
            if isinstance(data.get(k), list):
                data = data[k]
                break
        else:
            data = [data]
    if not isinstance(data, list) or not data:
        return {"name": base, "path": path, "kind": "json", "columns": [], "rows": [],
                "note": "no list of records found", "encoding": enc}
    cols = []
    for item in data:
        if isinstance(item, dict):
            for k in item.keys():
                if k not in cols:
                    cols.append(k)
    rows = []
    for item in data:
        if isinstance(item, dict):
            rows.append({c: item.get(c) for c in cols})
    return {"name": base, "path": path, "kind": "json", "columns": cols,
            "rows": rows, "note": "", "encoding": enc}


def _load_sqlite(path, base):
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        cur = con.cursor()
        tables = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
        table = tables[0] if tables else None
        if table:
            cols = [r[1] for r in cur.execute(f"PRAGMA table_info('{table}')").fetchall()]
            rows = [dict(zip(cols, r)) for r in cur.execute(f"SELECT * FROM '{table}'").fetchall()]
        else:
            cols, rows = [], []
        con.close()
    except Exception as e:  # noqa: BLE001
        return {"name": base, "path": path, "kind": "sqlite", "columns": [], "rows": [],
                "note": f"sqlite error: {e}"}
    return {"name": base, "path": path, "kind": "sqlite", "columns": cols, "rows": rows,
            "note": f"table={table}" if table else "no tables"}


# --------------------------------------------------------------------------
# Profiling: field dictionary, types, missing/duplicate/outlier first pass
# --------------------------------------------------------------------------

def profile_table(table):
    cols = table["columns"]
    rows = table["rows"]
    prof = {"table": table["name"], "path": table["path"], "kind": table["kind"],
            "n_rows": len(rows), "columns": {}}
    for c in cols:
        vals = [r.get(c) for r in rows]
        nonmiss = [v for v in vals if not missing(v)]
        numeric = all(is_number(v) for v in nonmiss) and len(nonmiss) > 0
        uniq = len({str(v) for v in vals})
        info = {"name": c, "type": "numeric" if numeric else "categorical",
                "n_missing": len(vals) - len(nonmiss), "n_unique": uniq}
        if numeric:
            floats = [to_float(v) for v in nonmiss]
            floats = [x for x in floats if x is not None]
            d = desc(floats)
            if d:
                info["stats"] = d
        prof["columns"][c] = info
    return prof


def is_id_like(info, n_rows):
    """A column whose every value is unique and either named as an identifier or
    has many rows is treated as an ID/key, not a numeric target. Small-n columns
    with all-distinct values (e.g. a 7-row measurement) are kept as targets."""
    if n_rows <= 1 or info.get("n_unique") != n_rows:
        return False
    low = str(info.get("name", "")).lower()
    if any(p in low for p in ("id", "key", "index", "idx", "uuid", "guid", "row")):
        return True
    return n_rows > 30


def keyword_match(name, words):
    low = str(name).lower()
    return any(w in low for w in words)


def infer_group_targets(prof, task_text, group_hint=None, targets_hint=None):
    cols = prof["columns"]
    n_rows = prof["n_rows"]
    group = group_hint if group_hint and group_hint in cols else None
    targets = [t for t in (targets_hint or []) if t in cols]

    if not group:
        group_names = ("group", "grp", "class", "species", "label", "target", "type",
                       "category", "treatment", "arm", "condition", "diagnosis", "outcome")
        # categorical candidates first, then low-cardinality numeric
        cand = [c for c, i in cols.items()
                if i["type"] == "categorical" and 2 <= i["n_unique"] <= 30 and not is_id_like(i, n_rows)]
        if not cand:
            cand = [c for c, i in cols.items()
                    if i["type"] == "numeric" and 2 <= i["n_unique"] <= 10]
        if cand:
            # prefer keyword-named columns; else prefer lower cardinality
            named = [c for c in cand if keyword_match(c, group_names)]
            group = (named or cand)[0]
    if not targets:
        for c, i in cols.items():
            if c == group:
                continue
            if i["type"] != "numeric":
                continue
            if is_id_like(i, n_rows):
                continue
            targets.append(c)
    # fall back: every numeric column except group (even if not auto-selected above)
    if not targets:
        targets = [c for c, i in cols.items() if c != group and i["type"] == "numeric"]
    return group, targets


# --------------------------------------------------------------------------
# Cleaning: dedup + missing accounting + outlier marking (no silent drops)
# --------------------------------------------------------------------------

def clean_table(table, group_col, target_cols):
    cols, rows = table["columns"], table["rows"]
    n0 = len(rows)
    seen, dedup, dup_rows = set(), [], 0
    for r in rows:
        key = tuple(str(r.get(c)) for c in cols)
        if key in seen:
            dup_rows += 1
        else:
            seen.add(key)
            dedup.append(r)
    rules = []
    if dup_rows:
        rules.append({"rule": "drop_exact_duplicates", "rows_removed": dup_rows,
                      "note": "按全部列精确去重；原始数据保留于源文件"})
    outliers = {}
    for t in target_cols:
        idx_vals = [(i, to_float(r.get(t))) for i, r in enumerate(dedup) if is_number(r.get(t))]
        vals = [x for _, x in idx_vals]
        if len(vals) >= 5:
            flags, lo, hi = iqr_outliers(vals)
            idx = [idx_vals[j][0] for j in range(len(vals)) if flags[j]]
            if idx:
                outliers[t] = {"method": "iqr_1.5_tukey", "low_fence": lo, "high_fence": hi,
                               "n_outliers": len(idx), "row_indices": idx,
                               "action": "仅标记，未删除"}
    complete_note = ("分组列/目标列缺失的行按测试做完全个案剔除（complete cases），"
                     "各测试逐项记录 n；不在清洗阶段擅自删行。")
    meta = {"original_rows": n0, "rows_after_dedup": len(dedup), "duplicates_removed": dup_rows,
            "rules": rules, "outliers": outliers, "group_col": group_col,
            "target_cols": target_cols, "complete_case_note": complete_note}
    return dedup, meta


# --------------------------------------------------------------------------
# Analysis: descriptive + hypothesis tests + correlations
# --------------------------------------------------------------------------

def analyze(rows, group_col, target_cols):
    stats = {"n_rows_cleaned": len(rows), "group_col": group_col,
             "target_cols": target_cols, "descriptive": {}, "tests": [], "correlations": []}
    groups = {}
    for r in rows:
        g = r.get(group_col)
        if missing(g):
            continue
        groups.setdefault(str(g), []).append(r)
    group_labels = sorted(groups.keys())
    stats["groups"] = group_labels
    for t in target_cols:
        stats["descriptive"][t] = {}
        allvals = [to_float(r.get(t)) for r in rows if is_number(r.get(t))]
        allvals = [x for x in allvals if x is not None]
        stats["descriptive"][t]["__overall__"] = desc(allvals)
        arrs = []
        for g in group_labels:
            vals = [to_float(r.get(t)) for r in groups[g] if is_number(r.get(t))]
            vals = [x for x in vals if x is not None]
            d = desc(vals)
            if d:
                stats["descriptive"][t][g] = d
            if vals:
                arrs.append((g, vals))
        k = len(arrs)
        if k == 2:
            a, b = arrs[0][1], arrs[1][1]
            tt = welch_t(a, b)
            mw = mann_whitney(a, b)
            if tt:
                tt["groups"] = f"{arrs[0][0]} vs {arrs[1][0]}"
            if mw:
                mw["groups"] = f"{arrs[0][0]} vs {arrs[1][0]}"
            stats["tests"].append({"target": t, "design": "two_group",
                                   "welch_t": tt, "mann_whitney_u": mw})
        elif k > 2:
            av = anova([gv for _, gv in arrs])
            kw = kruskal_wallis([gv for _, gv in arrs])
            if av:
                av["groups"] = [g for g, _ in arrs]
            if kw:
                kw["groups"] = [g for g, _ in arrs]
            stats["tests"].append({"target": t, "design": "multi_group",
                                   "one_way_anova": av, "kruskal_wallis": kw})
        elif k == 1:
            stats["tests"].append({"target": t, "design": "single_group",
                                   "note": "only one group present; no between-group test run"})
        else:
            stats["tests"].append({"target": t, "design": "none",
                                   "note": "no analyzable groups"})
    # pairwise Pearson among numeric targets
    for i in range(len(target_cols)):
        for j in range(i + 1, len(target_cols)):
            xc, yc = target_cols[i], target_cols[j]
            pairs = [(to_float(r.get(xc)), to_float(r.get(yc))) for r in rows
                     if is_number(r.get(xc)) and is_number(r.get(yc))]
            pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
            if len(pairs) >= 3:
                pr = pearson([p[0] for p in pairs], [p[1] for p in pairs])
                if pr:
                    pr["columns"] = [xc, yc]
                    stats["correlations"].append(pr)
    return stats


# --------------------------------------------------------------------------
# SVG rendering (editable, deterministic)
# --------------------------------------------------------------------------

def _ticks(vmin, vmax, n=5):
    if vmin == vmax:
        vmax = vmin + 1.0
    span = vmax - vmin
    step = 10 ** math.floor(math.log10(span / n))
    for mult in (1, 2, 2.5, 5, 10):
        if span / (step * mult) <= n:
            step *= mult
            break
    start = math.ceil(vmin / step) * step
    out = []
    v = start
    while v <= vmax + step * 1e-9:
        out.append(v)
        v += step
    return out, step


def svg_bar_chart(target, group_labels, desc_by_group, overall=None, width=640, height=420):
    means = {g: desc_by_group[g]["mean"] for g in group_labels if g in desc_by_group}
    sems = {g: desc_by_group[g]["se"] for g in group_labels if g in desc_by_group}
    if not means:
        return None
    ymin = min(m - 1.5 * sems.get(g, 0) for g, m in means.items())
    ymax = max(m + 1.5 * sems.get(g, 0) for g, m in means.items())
    if ymin == ymax:
        ymin, ymax = ymin - 1, ymax + 1
    pad = (ymax - ymin) * 0.08
    ymin -= pad
    ymax += pad
    if ymin > 0:
        baseline = ymin
        note = f"y-axis starts at {ymin:.4g} (not 0)"
    else:
        baseline = 0.0
        note = "y-axis starts at 0"
    left, right, top, bottom = 70, 20, 46, 78
    plot_w = width - left - right
    plot_h = height - top - bottom
    yscale = plot_h / (ymax - baseline) if ymax > baseline else plot_h
    n = len(group_labels)
    slot = plot_w / n
    bar_w = min(0.6 * slot, 80)
    ticks, step = _ticks(baseline, ymax)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
             f'viewBox="0 0 {width} {height}" font-family="sans-serif" font-size="12">']
    parts.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="white"/>')
    parts.append(f'<text x="{left}" y="{top - 14}" font-size="15" font-weight="bold">'
                 f'{_xml_escape(target)}: group means &amp; SEM</text>')
    # gridlines + y labels
    for tv in ticks:
        y = top + plot_h - (tv - baseline) * yscale
        parts.append(f'<line x1="{left}" y1="{y:.2f}" x2="{width - right}" y2="{y:.2f}" '
                     f'stroke="#e0e0e0"/>')
        parts.append(f'<text x="{left - 8}" y="{y + 4:.2f}" text-anchor="end" '
                     f'font-size="11">{tv:.4g}</text>')
    # bars + error bars
    for i, g in enumerate(group_labels):
        m = means.get(g)
        if m is None:
            continue
        x = left + slot * i + slot / 2
        y = top + plot_h - (m - baseline) * yscale
        ybase = top + plot_h
        h = abs(y - ybase)
        ytop = min(y, ybase)
        color = f"hsl({(i * 360 // max(n, 1))}, 60%, 55%)"
        parts.append(f'<rect x="{x - bar_w / 2:.2f}" y="{ytop:.2f}" width="{bar_w:.2f}" '
                     f'height="{h:.2f}" fill="{color}" stroke="#333"/>')
        se = sems.get(g, 0)
        e_top = top + plot_h - (m + se - baseline) * yscale
        e_bot = top + plot_h - (m - se - baseline) * yscale
        parts.append(f'<line x1="{x:.2f}" y1="{e_top:.2f}" x2="{x:.2f}" y2="{e_bot:.2f}" '
                     f'stroke="#111" stroke-width="1.5"/>')
        parts.append(f'<line x1="{x - 5:.2f}" y1="{e_top:.2f}" x2="{x + 5:.2f}" y2="{e_top:.2f}" '
                     f'stroke="#111"/>')
        parts.append(f'<line x1="{x - 5:.2f}" y1="{e_bot:.2f}" x2="{x + 5:.2f}" y2="{e_bot:.2f}" '
                     f'stroke="#111"/>')
        parts.append(f'<text x="{x:.2f}" y="{height - bottom + 18:.2f}" text-anchor="middle" '
                     f'font-size="11">{_xml_escape(str(g))}</text>')
    # axes
    parts.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="#000"/>')
    parts.append(f'<line x1="{left}" y1="{top + plot_h}" x2="{width - right}" y2="{top + plot_h}" '
                 f'stroke="#000"/>')
    parts.append(f'<text x="{left}" y="{height - 8}" font-size="11">group ({_xml_escape(target)} '
                 f'by group)</text>')
    parts.append(f'<text x="14" y="{top + plot_h / 2}" text-anchor="middle" font-size="11" '
                 f'transform="rotate(-90 14 {top + plot_h / 2})">value</text>')
    parts.append(f'<text x="{left}" y="{height - 46}" font-size="10" fill="#555">'
                 f'error bars = SEM; {note}</text>')
    parts.append("</svg>")
    return "".join(parts)


def svg_heatmap(corrs, cols, width=520):
    n = len(cols)
    if n < 2:
        return None
    cell = 60
    left, top = 130, 40
    size = left + n * cell + 20
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size + 60}" '
             f'viewBox="0 0 {size} {size + 60}" font-family="sans-serif" font-size="11">']
    parts.append(f'<rect x="0" y="0" width="{size}" height="{size + 60}" fill="white"/>')
    parts.append(f'<text x="{left}" y="{top - 12}" font-size="14" font-weight="bold">'
                 f'Pearson correlation (r)</text>')
    grid = {(a, b): r for a, b, r in corrs}
    for i in range(n):
        for j in range(n):
            x = left + j * cell
            y = top + i * cell
            r = 1.0 if i == j else grid.get((cols[i], cols[j]), grid.get((cols[j], cols[i]), None))
            if r is None:
                fill = "#eeeeee"
                label = ""
            else:
                v = (r + 1) / 2
                if v >= 0.5:
                    fill = f"hsl(210, 90%, {100 - (v - 0.5) * 60:.0f}%)"
                else:
                    fill = f"hsl(0, 90%, {100 - (0.5 - v) * 60:.0f}%)"
                label = f"{r:.2f}"
            parts.append(f'<rect x="{x}" y="{y}" width="{cell - 2}" height="{cell - 2}" '
                         f'fill="{fill}" stroke="#999"/>')
            if label:
                parts.append(f'<text x="{x + cell / 2}" y="{y + cell / 2 + 4}" '
                             f'text-anchor="middle">{label}</text>')
    for i, c in enumerate(cols):
        parts.append(f'<text x="{left + i * cell + cell / 2}" y="{top - 6}" text-anchor="middle" '
                     f'transform="rotate(-40 {left + i * cell + cell / 2} {top - 6})">'
                     f'{_xml_escape(c)}</text>')
        parts.append(f'<text x="{left - 6}" y="{top + i * cell + cell / 2 + 4}" text-anchor="end">'
                     f'{_xml_escape(c)}</text>')
    parts.append("</svg>")
    return "".join(parts)


# --------------------------------------------------------------------------
# Report generation (numbers all drawn from stats / meta / profile)
# --------------------------------------------------------------------------

def fmt(x, sig=4):
    if x is None:
        return "—"
    if isinstance(x, str):
        return x
    if isinstance(x, int):
        return str(x)
    f = float(x)
    if f != f:
        return "NaN"
    if f == 0:
        return "0"
    return f"{f:.{sig}g}"


def build_report(st):
    L = []
    L.append(f"# 数据分析报告 — {st['case_id']}\n")
    L.append(f"生成时间：{st.get('finalized_at', now_iso())}\n")
    L.append("> 本报告所有数字均来自 `work/stats.json`，可回算；图形与统计数据同源。\n")
    prof = st.get("profile", {})
    meta = st.get("clean_meta", {})
    stats = st.get("stats", {})
    table = st.get("table", {})
    L.append("## 1. 数据来源与字段\n")
    L.append(f"- 主表：`{table.get('name', '—')}`（{table.get('kind', '—')}），原始行数 {meta.get('original_rows', '—')}")
    L.append(f"- 字段数：{len(table.get('columns', []))}")
    for c, info in prof.get("columns", {}).items():
        L.append(f"  - `{c}`：{info.get('type')}，缺失 {info.get('n_missing')}，唯一值 {info.get('n_unique')}")
    L.append("\n## 2. 清洗规则与行数影响\n")
    L.append(f"- 去重后行数：{meta.get('rows_after_dedup', '—')}（移除精确重复 {meta.get('duplicates_removed', 0)} 行）")
    for rule in meta.get("rules", []):
        L.append(f"  - {rule.get('rule')}：移除 {rule.get('rows_removed')} 行 — {rule.get('note', '')}")
    for t, o in (meta.get("outliers") or {}).items():
        L.append(f"  - `{t}` 异常值：{o.get('method')}，阈值 [{fmt(o.get('low_fence'))}, {fmt(o.get('high_fence'))}]，"
                 f"标记 {o.get('n_outliers')} 个（{o.get('action')}）")
    if not meta.get("rules") and not meta.get("outliers"):
        L.append("  - 未检测到精确重复或 IQR 异常值。")
    L.append("\n## 3. 分组与目标\n")
    L.append(f"- 分组列：`{stats.get('group_col')}`，分组：{', '.join(stats.get('groups', [])) or '—'}")
    L.append(f"- 目标列：{', '.join(stats.get('target_cols', [])) or '—'}\n")
    L.append("## 4. 描述统计（按组）\n")
    for t in stats.get("target_cols", []):
        L.append(f"\n### `{t}`\n")
        L.append("| 组 | n | 均值 | SD | 中位数 | Q1 | Q3 | 最小 | 最大 |")
        L.append("|---|---|---|---|---|---|---|---|")
        for g, d in stats.get("descriptive", {}).get(t, {}).items():
            if g == "__overall__":
                continue
            L.append(f"| {g} | {d.get('n')} | {fmt(d.get('mean'))} | {fmt(d.get('sd'))} | "
                     f"{fmt(d.get('median'))} | {fmt(d.get('q1'))} | {fmt(d.get('q3'))} | "
                     f"{fmt(d.get('min'))} | {fmt(d.get('max'))} |")
        ov = stats.get("descriptive", {}).get(t, {}).get("__overall__")
        if ov:
            L.append(f"| 总体 | {ov.get('n')} | {fmt(ov.get('mean'))} | {fmt(ov.get('sd'))} | "
                     f"{fmt(ov.get('median'))} | {fmt(ov.get('q1'))} | {fmt(ov.get('q3'))} | "
                     f"{fmt(ov.get('min'))} | {fmt(ov.get('max'))} |")
    L.append("\n## 5. 推断统计（p 值与效应量）\n")
    for t in stats.get("tests", []):
        L.append(f"\n### `{t.get('target')}`（{t.get('design')}）")
        if t.get("welch_t"):
            w = t["welch_t"]
            L.append(f"- Welch t 检验：t = {fmt(w.get('t'))}，df = {fmt(w.get('df'))}，"
                     f"p = {fmt(w.get('p'))}，Cohen's d = {fmt(w.get('effect_size'))}")
        if t.get("mann_whitney_u"):
            m = t["mann_whitney_u"]
            L.append(f"- Mann-Whitney U：U = {fmt(m.get('U'))}，z = {fmt(m.get('z'))}，"
                     f"p = {fmt(m.get('p'))}，rank-biserial r = {fmt(m.get('effect_size'))}")
        if t.get("one_way_anova"):
            a = t["one_way_anova"]
            L.append(f"- 单因素 ANOVA：F({a.get('df_between')},{a.get('df_within')}) = {fmt(a.get('F'))}，"
                     f"p = {fmt(a.get('p'))}，η² = {fmt(a.get('effect_size'))}")
        if t.get("kruskal_wallis"):
            k = t["kruskal_wallis"]
            L.append(f"- Kruskal-Wallis：H = {fmt(k.get('H'))}，df = {k.get('df')}，"
                     f"p = {fmt(k.get('p'))}")
        if t.get("note"):
            L.append(f"- {t.get('note')}")
    L.append("\n### 相关分析（Pearson）\n")
    for c in stats.get("correlations", []):
        L.append(f"- `{c.get('columns', ['—', '—'])[0]}` × `{c.get('columns', ['—', '—'])[1]}`："
                 f"r = {fmt(c.get('r'))}，r² = {fmt(c.get('r2'))}，p = {fmt(c.get('p'))}（n = {c.get('n')}）")
    if not stats.get("correlations"):
        L.append("- 数值目标列不足两个，未做相关分析。")
    L.append("\n## 6. 结论口径\n")
    L.append("p < 0.05 视为具有统计显著性；效应量用于判断实际意义。上述 p 值未经多重比较校正，"
             "多个目标列同时检验时应谨慎解读。")
    L.append("\n## 7. 不确定性与异常\n")
    for u in st.get("uncertainty", []):
        L.append(f"- {u}")
    for a in st.get("anomaly_notes", []):
        L.append(f"- 异常：{a}")
    if not st.get("uncertainty") and not st.get("anomaly_notes"):
        L.append("- 无特别标注。")
    L.append("\n## 8. 复现\n")
    L.append("```bash\npython3 -B scripts/main.py run --input /input --output /app/output --work /app/work\n```\n")
    L.append("中间产物：`work/profile.json`、`work/clean_meta.json`、`work/cleaned.csv`、`work/stats.json`。\n")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------
# Pipeline stages
# --------------------------------------------------------------------------

def _load_state(work_dir):
    p = os.path.join(work_dir, "state.json")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _sanitize(obj):
    """Convert non-finite floats to strings so JSON output stays strict/parseable."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj):
            return "nan"
        if math.isinf(obj):
            return "inf" if obj > 0 else "-inf"
    return obj


def _save_state(st, work_dir):
    os.makedirs(work_dir, exist_ok=True)
    with open(os.path.join(work_dir, "state.json"), "w", encoding="utf-8") as f:
        json.dump(_sanitize(st), f, ensure_ascii=False, indent=2, sort_keys=True)


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_sanitize(obj), f, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)


def _log(st, kind, message, **extra):
    entry = {"time": now_iso(), "stage": st.get("stage", "?"), "message": message}
    entry.update(extra)
    st.setdefault(kind, []).append(entry)


def init_state():
    return {"started_at": now_iso(), "started_epoch": time.time(), "stage": "init",
            "case_id": None, "contract_keys": [], "status": "partial", "sources": [],
            "uncertainty": [], "pending_human": [], "change_log": [], "errors": [],
            "degradations": [], "anomaly_notes": [], "artifacts": {"report": None, "figures": []}}


def scaffold(st, input_dir, output_dir, work_dir):
    st["stage"] = "scaffold"
    case = read_case(input_dir)
    st["case_id"] = case["case_id"]
    st["contract_keys"] = case["contract_keys"]
    st["task_text"] = case["task_text"]
    st["errors"].extend(case["errors"])
    st["degradations"].extend(case["degradations"])
    st["case_ok"] = not any("case.json" in e.get("message", "") for e in case["errors"])
    if case.get("case_raw", {}).get("case_id") or case.get("case_raw", {}).get("id") \
            or case.get("case_raw", {}).get("armiId"):
        st["sources"].append({"locator": "case.json",
                              "quote": str(case["case_raw"].get("case_id") or case["case_raw"].get("id")
                                           or case["case_raw"].get("armiId")),
                              "purpose": "case_id"})
    if case["task_text"]:
        first = case["task_text"].strip().splitlines()[0] if case["task_text"].strip() else ""
        st["sources"].append({"locator": "task.md#L1", "quote": first[:200], "purpose": "task requirement"})
    st["change_log"].append({"time": now_iso(), "stage": "scaffold",
                             "action": "skeleton result.json written (status=partial, contract keys null)"})
    # write skeleton result now (first ~seconds)
    result = {"case_id": st["case_id"], "status": "partial"}
    for k in st["contract_keys"]:
        result[k] = None
    result.update({"sources": st["sources"], "uncertainty": [], "pending_human": [],
                   "change_log": st["change_log"], "errors": st["errors"],
                   "degradations": st["degradations"],
                   "runtime": {"seconds": None, "started_at": st["started_at"]},
                   "artifacts": st["artifacts"]})
    os.makedirs(output_dir, exist_ok=True)
    _write_json(os.path.join(output_dir, "result.json"), result)
    _save_state(st, work_dir)


def profile_stage(st, input_dir, work_dir, primary, group, targets):
    st["stage"] = "profile"
    data_paths = discover_inputs(input_dir)
    if not data_paths:
        st["data_found"] = False
        _log(st, "errors", "no data files found under input")
        _save_state(st, work_dir)
        return
    tables = []
    for p in data_paths:
        try:
            t = load_table(p)
        except Exception as e:  # noqa: BLE001
            _log(st, "errors", f"failed to load {os.path.basename(p)}", detail=str(e))
            continue
        if t.get("columns"):
            tables.append(t)
        else:
            _log(st, "degradations", f"empty/unparseable table skipped: {os.path.basename(p)}",
                 detail=t.get("note", ""))
    if not tables:
        st["data_found"] = False
        _log(st, "errors", "no loadable tables with columns")
        _save_state(st, work_dir)
        return
    st["data_found"] = True
    # pick primary table
    table = None
    if primary:
        for t in tables:
            if primary in (t["name"], os.path.basename(t["path"]), t["path"]):
                table = t
                break
        if not table:
            _log(st, "degradations", f"primary '{primary}' not found; fell back to largest table")
    if not table:
        table = max(tables, key=lambda t: len(t["rows"]))
    st["table"] = {k: table[k] for k in ("name", "path", "kind", "columns", "note")}
    st["table"]["rows"] = table["rows"]
    prof = profile_table(table)
    st["profile"] = prof
    group, targets = infer_group_targets(prof, st.get("task_text", ""), group, targets)
    st["group_col"] = group
    st["target_cols"] = targets
    if not targets:
        _log(st, "errors", "no numeric target columns found")
    if not group:
        _log(st, "degradations", "no group column identified; analysis limited to descriptive + correlation")
    _write_json(os.path.join(work_dir, "profile.json"), prof)
    st["sources"].append({"locator": f"{os.path.basename(table['path'])} (data file)",
                          "quote": f"{len(table['rows'])} rows × {len(table['columns'])} columns",
                          "purpose": "primary table shape"})
    st["change_log"].append({"time": now_iso(), "stage": "profile",
                             "action": f"profiled {len(tables)} table(s); primary={table['name']}; "
                                       f"group={group}; targets={targets}"})
    _save_state(st, work_dir)


def analyze_stage(st, work_dir):
    st["stage"] = "analyze"
    rows = st.get("table", {}).get("rows")
    if not rows:
        _save_state(st, work_dir)
        return
    group_col = st.get("group_col")
    target_cols = st.get("target_cols") or []
    dedup, meta = clean_table({"columns": st["table"]["columns"], "rows": rows}, group_col, target_cols)
    stats = analyze(dedup, group_col, target_cols) if (group_col and target_cols) else {
        "n_rows_cleaned": len(dedup), "group_col": group_col, "target_cols": target_cols,
        "descriptive": {}, "tests": [], "correlations": []}
    st["clean_meta"] = meta
    st["stats"] = stats
    # write cleaned.csv (dedup, original columns)
    cols = st["table"]["columns"]
    with open(os.path.join(work_dir, "cleaned.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in dedup:
            w.writerow([r.get(c, "") for c in cols])
    _write_json(os.path.join(work_dir, "clean_meta.json"), meta)
    _write_json(os.path.join(work_dir, "stats.json"), stats)
    # anomaly notes from outliers + missing
    for t, o in (meta.get("outliers") or {}).items():
        st["anomaly_notes"].append(
            f"列 `{t}` 检出 {o.get('n_outliers')} 个 IQR 异常值（Tukey 1.5 倍，阈值 "
            f"[{fmt(o.get('low_fence'))}, {fmt(o.get('high_fence'))}]），仅标记、未删除。")
    if meta.get("duplicates_removed"):
        st["anomaly_notes"].append(f"移除 {meta['duplicates_removed']} 行精确重复记录。")
    st["uncertainty"].append(
        "Welch t / ANOVA / Mann-Whitney / Kruskal-Wallis / Pearson 均由纯标准库实现；"
        "秩检验的 p 值采用正态/卡方近似并做结值（tie）校正，小样本时应谨慎解读。")
    st["change_log"].append({"time": now_iso(), "stage": "analyze",
                             "action": f"cleaned (dedup {meta['duplicates_removed']} rows) and "
                                       f"computed stats for {len(target_cols)} target(s)"})
    _save_state(st, work_dir)


def render_stage(st, work_dir, output_dir):
    st["stage"] = "render"
    stats = st.get("stats", {})
    target_cols = stats.get("target_cols", []) or []
    group_labels = stats.get("groups", []) or []
    desc = stats.get("descriptive", {})
    figs = []
    for t in target_cols:
        if group_labels and t in desc:
            svg = svg_bar_chart(t, group_labels, desc[t], desc[t].get("__overall__"))
            if svg:
                fname = f"figure_{_safe(t)}.svg"
                with open(os.path.join(output_dir, fname), "w", encoding="utf-8") as f:
                    f.write(svg)
                figs.append(fname)
    corr_map = [(c["columns"][0], c["columns"][1], c["r"]) for c in stats.get("correlations", [])
                if c.get("columns") and len(c["columns"]) == 2]
    if len(target_cols) >= 2 and corr_map:
        hm = svg_heatmap(corr_map, target_cols)
        if hm:
            fname = "figure_corr.svg"
            with open(os.path.join(output_dir, fname), "w", encoding="utf-8") as f:
                f.write(hm)
            figs.append(fname)
    st["artifacts"]["figures"] = figs
    report = build_report(st)
    with open(os.path.join(output_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(report)
    st["artifacts"]["report"] = "report.md"
    st["change_log"].append({"time": now_iso(), "stage": "render",
                             "action": f"wrote report.md and {len(figs)} SVG figure(s)"})
    _save_state(st, work_dir)


def _safe(name):
    out = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(name))
    return out[:60] or "var"


def _contract_values(st):
    stats = st.get("stats", {})
    analysis_results = {
        "n_rows_cleaned": stats.get("n_rows_cleaned"),
        "group_column": stats.get("group_col"),
        "groups": stats.get("groups", []),
        "target_columns": stats.get("target_cols", []),
        "descriptive": stats.get("descriptive", {}),
        "tests": stats.get("tests", []),
        "correlations": stats.get("correlations", []),
        "authoritative_source": "work/stats.json",
    }
    quality_evidence = {
        "cleaned_table": "work/cleaned.csv",
        "clean_rules": "work/clean_meta.json",
        "field_dictionary": "work/profile.json",
        "statistics": "work/stats.json",
        "sources": st.get("sources", []),
        "reproducibility": "python3 -B scripts/main.py run --input /input --output /app/output --work /app/work",
        "determinism_note": "no randomness; re-running yields byte-identical stats.json and SVG",
    }
    anomaly_notes = st.get("anomaly_notes", [])
    values = {"analysis_results": analysis_results, "quality_evidence": quality_evidence,
              "anomaly_notes": anomaly_notes}
    # any extra contract keys are filled from case.json if present, else null
    extra = {k: st.get("case_raw", {}).get(k, None) for k in st["contract_keys"]
             if k not in values}
    values.update(extra)
    return values


def _has_inferential(stats):
    for t in (stats or {}).get("tests", []):
        for k in ("welch_t", "mann_whitney_u", "one_way_anova", "kruskal_wallis"):
            if t.get(k):
                return True
    return bool((stats or {}).get("correlations"))


def _status_for(st):
    """Recompute status from evidence markers, so early stages never lock in a
    stale insufficient_evidence / input_error verdict."""
    if not st.get("case_ok", False):
        return "input_error"
    if not st.get("data_found", False):
        return "insufficient_evidence"
    if _has_inferential(st.get("stats")):
        return "ok" if not st.get("errors") else "partial"
    return "partial" if st.get("errors") else "insufficient_evidence"


def finalize(st, output_dir, work_dir):
    st["stage"] = "finalize"
    end = time.time()
    started_epoch = st.get("started_epoch")
    seconds = round(end - started_epoch, 3) if started_epoch else None
    st["status"] = _status_for(st)
    result = {"case_id": st["case_id"], "status": st["status"]}
    result.update(_contract_values(st))
    result.update({
        "sources": st["sources"],
        "uncertainty": st["uncertainty"],
        "pending_human": st["pending_human"],
        "change_log": st["change_log"],
        "errors": st["errors"],
        "degradations": st["degradations"],
        "runtime": {"seconds": seconds, "started_at": st.get("started_at"), "finished_at": now_iso()},
        "artifacts": st["artifacts"],
    })
    # ensure every contract key present even if value map missed one
    for k in st["contract_keys"]:
        if k not in result:
            result[k] = None
    st["finalized_at"] = now_iso()
    _write_json(os.path.join(output_dir, "result.json"), result)
    _save_state(st, work_dir)


def run_pipeline(input_dir, output_dir, work_dir, primary=None, group=None, targets=None):
    st = init_state()
    try:
        scaffold(st, input_dir, output_dir, work_dir)
        if st["status"] != "insufficient_evidence":
            profile_stage(st, input_dir, work_dir, primary, group, targets)
        analyze_stage(st, work_dir)
        render_stage(st, work_dir, output_dir)
        finalize(st, output_dir, work_dir)
    except Exception as e:  # noqa: BLE001
        st["errors"].append({"time": now_iso(), "stage": st.get("stage", "?"),
                             "message": str(e), "traceback": traceback.format_exc()})
        st["status"] = "partial"
        try:
            finalize(st, output_dir, work_dir)
        except Exception:  # noqa: BLE001
            pass
    return st


def run_stage(stage, input_dir, output_dir, work_dir, primary=None, group=None, targets=None):
    """Run a single named stage, loading prior state so calls compose incrementally."""
    st = _load_state(work_dir) or init_state()
    try:
        # ensure case_id/contract are known before any later stage (idempotent guard)
        if not st.get("contract_keys") or not st.get("case_id"):
            scaffold(st, input_dir, output_dir, work_dir)
        if stage == "scaffold":
            pass  # already scaffolded above
        elif stage == "profile":
            profile_stage(st, input_dir, work_dir, primary, group, targets)
        elif stage == "analyze":
            analyze_stage(st, work_dir)
        elif stage == "render":
            render_stage(st, work_dir, output_dir)
        elif stage == "finalize":
            pass
        else:  # safety net (normally main.py dispatches 'run' to run_pipeline)
            run_pipeline(input_dir, output_dir, work_dir, primary, group, targets)
            return
    except Exception as e:  # noqa: BLE001
        st["errors"].append({"time": now_iso(), "stage": stage,
                             "message": str(e), "traceback": traceback.format_exc()})
        st["status"] = "partial"
        _save_state(st, work_dir)
    finalize(st, output_dir, work_dir)
    return st
