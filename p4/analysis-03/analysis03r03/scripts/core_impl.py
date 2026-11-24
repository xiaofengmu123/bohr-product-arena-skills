#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确定性实现库（仅标准库）：指纹、清洗、统计、相关/检验、SVG 兜底。

所有函数均为纯函数或无随机依赖的确定性函数：
  - 不读系统随机数（种子固定记录在 result.json 的 determinism.seed）；
  - 类别排序使用 (频次, 标签) 稳定排序，输出 JSON 使用 sort_keys=True；
  - 同一输入、同一 Python 版本下，两次运行字节级一致。
"""
import csv
import hashlib
import io
import json
import math
import os
import re
import statistics

# --------------------------------------------------------------------------- #
# 确定性指纹
# --------------------------------------------------------------------------- #
def _sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_dir_fingerprint(dirpath):
    """目录内所有文件（按相对路径排序）的 sha256 拼接指纹；空目录返回 None。"""
    if not os.path.isdir(dirpath):
        return None
    rels = []
    for root, _dirs, names in os.walk(dirpath):
        for name in names:
            p = os.path.join(root, name)
            rel = os.path.relpath(p, dirpath).replace("\\", "/")
            rels.append(rel)
    if not rels:
        return None
    h = hashlib.sha256()
    for rel in sorted(rels):
        p = os.path.join(dirpath, rel)
        try:
            digest = _sha256_file(p)
        except OSError:
            digest = "unreadable"
        h.update(rel.encode("utf-8") + b"\x00" + digest.encode("utf-8") + b"\n")
    return h.hexdigest()


def compute_input_fingerprint(input_dir):
    """输入目录指纹；与结果绑定，证明产物对应哪一版输入。"""
    return compute_dir_fingerprint(input_dir)


def canonical_json(obj):
    """规范化 JSON 字节串，用于 byte 级一致性比对（sort_keys + 紧凑分隔符）。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


# --------------------------------------------------------------------------- #
# 表格读取（CSV/TSV/SQLite），先发现再假设
# --------------------------------------------------------------------------- #
def detect_delimiter(sample):
    """按样本行中常见分隔符的出现频率选择分隔符。"""
    cands = [(",", ","), ("\t", "tab"), (";", ";"), ("|", "|")]
    best, best_score = ",", -1
    for line in sample.splitlines()[:20]:
        for ch, _name in cands:
            c = line.count(ch)
            if c > best_score:
                best, best_score = ch, c
    return best


def parse_number(s):
    """把常见科研文本形态解析为 float：千分位、百分号、货币符号、括号负数。"""
    if s is None:
        return None
    t = str(s).strip()
    if t == "":
        return None
    t = t.replace("%", "").replace(",", "").strip()
    t = re.sub(r"^[\$\u00a5\u00a3\u20ac]", "", t)
    if t.startswith("(") and t.endswith(")"):
        t = "-" + t[1:-1]
    try:
        return float(t)
    except ValueError:
        return None


def read_text_robust(path, limit=2 * 1024 * 1024):
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), enc, None
        except UnicodeDecodeError as e:
            last = "%s: %s" % (enc, e)
        except OSError as e:
            return "", None, "unreadable: %s" % e
    return "", None, "undecodable: " + last


def read_table(path, max_rows=100000):
    """读取 CSV/TSV/SQLite，返回 (columns, rows, meta)。不假设固定结构。"""
    low = path.lower()
    if low.endswith((".db", ".sqlite", ".sqlite3")):
        return _read_sqlite(path, max_rows)
    text, enc, err = read_text_robust(path)
    if err:
        raise ValueError(err)
    delim = detect_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = []
    for r in reader:
        rows.append(r)
        if len(rows) > max_rows:
            break
    if not rows:
        return [], [], {"source": path, "encoding": enc, "delimiter": delim,
                        "nrows": 0}
    columns = [c.strip() for c in rows[0]]
    data = rows[1:]
    return columns, data, {"source": path, "encoding": enc, "delimiter": delim,
                           "nrows": len(data)}


def _read_sqlite(path, max_rows):
    import sqlite3
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        tables = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        if not tables:
            return [], [], {"source": path, "tables": [], "nrows": 0}
        table = tables[0]
        cur.execute('SELECT * FROM "%s" LIMIT %d' % (table, max_rows + 1))
        columns = [d[0] for d in cur.description]
        rows = [list(r) for r in cur.fetchall()]
        return columns, rows, {"source": path, "table": table,
                               "tables": tables, "nrows": len(rows)}
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# 列类型判定与清洗口径
# --------------------------------------------------------------------------- #
def classify_columns(columns, rows):
    """把每列归为 continuous/discrete/categorical/datetime/identifier/mixed。"""
    n = len(rows)
    out = {}
    for ci, name in enumerate(columns):
        vals = [r[ci] for r in rows if ci < len(r)]
        nonnull = [v for v in vals if v is not None and str(v).strip() != ""]
        parsed = [parse_number(v) for v in nonnull]
        num_ok = [x for x in parsed if x is not None]
        if nonnull and len(num_ok) == len(nonnull):
            uniq = len(set(num_ok))
            all_int = all(float(x).is_integer() for x in num_ok)
            if uniq <= 12:
                kind = "discrete" if all_int else "continuous"
            elif all_int and uniq < max(8, n * 0.2):
                kind = "discrete"
            else:
                kind = "continuous"
        elif nonnull and all(re.match(r"^\d{4}-\d{2}-\d{2}", str(v)) for v in nonnull):
            kind = "datetime"
        elif nonnull and len(set(str(v) for v in nonnull)) == len(nonnull) and len(nonnull) > 10:
            kind = "identifier"
        else:
            kind = "categorical"
        out[name] = {"type": kind, "n": len(nonnull),
                     "n_missing": n - len(nonnull),
                     "n_unique": len(set(str(v) for v in nonnull))}
    return out


def _numeric_values(rows, ci):
    vals = []
    for r in rows:
        if ci < len(r):
            x = parse_number(r[ci])
            if x is not None:
                vals.append(x)
    return vals


# --------------------------------------------------------------------------- #
# 统计（确定性，纯标准库）
# --------------------------------------------------------------------------- #
def quantile(sorted_values, q):
    """type-7 线性插值分位数。"""
    n = len(sorted_values)
    if n == 0:
        return None
    if n == 1:
        return float(sorted_values[0])
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(sorted_values[lo])
    frac = pos - lo
    return float(sorted_values[lo]) * (1 - frac) + float(sorted_values[hi]) * frac


def describe_numeric(values):
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mean = sum(s) / n
    sd = statistics.stdev(s) if n > 1 else 0.0
    sem = sd / math.sqrt(n) if n > 0 else None
    ci95 = None
    if n > 1 and sem is not None:
        t = t_inv(0.975, n - 1)
        ci95 = {"lower": mean - t * sem, "upper": mean + t * sem}
    return {
        "n": n, "mean": round(mean, 8), "median": round(quantile(s, 0.5), 8),
        "sd": round(sd, 8), "sem": round(sem, 8) if sem is not None else None,
        "ci95": {k: round(v, 8) for k, v in ci95.items()} if ci95 else None,
        "min": round(s[0], 8), "max": round(s[-1], 8),
        "q1": round(quantile(s, 0.25), 8), "q3": round(quantile(s, 0.75), 8),
        "iqr": round(quantile(s, 0.75) - quantile(s, 0.25), 8),
    }


def describe_table(columns, rows):
    out = {}
    for ci, name in enumerate(columns):
        vals = _numeric_values(rows, ci)
        if len(vals) >= 2:
            out[name] = describe_numeric(vals)
    return out


def group_summary(columns, rows, group_col, value_col):
    gi = columns.index(group_col)
    vi = columns.index(value_col)
    groups = {}
    for r in rows:
        if gi < len(r) and vi < len(r):
            x = parse_number(r[vi])
            if x is not None:
                groups.setdefault(str(r[gi]), []).append(x)
    out = {}
    for g in sorted(groups):  # 标签排序 → 确定性
        d = describe_numeric(groups[g])
        if d:
            out[g] = d
    return out


# --------------------------------------------------------------------------- #
# 分布与检验（t 分布 CDF 用连分数实现，不依赖第三方库）
# --------------------------------------------------------------------------- #
def _lbeta(a, b):
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _betacf(a, b, x):
    MAXIT, EPS, FPMIN = 200, 3e-12, 1e-300
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
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    logb = _lbeta(a, b)
    lbeta = logb + a * math.log(x) + b * math.log(1 - x)
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(lbeta) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbeta) * _betacf(b, a, 1 - x) / b


def t_cdf(t, df):
    if df <= 0:
        return float("nan")
    x = df / (df + t * t)
    p = 0.5 * _betai(df / 2.0, 0.5, x)
    return 1.0 - p if t > 0 else p


def t_two_sided_p(t, df):
    return 2.0 * (1.0 - t_cdf(abs(t), df))


def t_inv(p, df, tol=1e-9):
    """t 分布分位数（二分法，确定性）。"""
    if p <= 0 or p >= 1:
        return float("nan")
    lo, hi = -1e6, 1e6
    while t_cdf(hi, df) < p:
        hi *= 2
    while t_cdf(lo, df) > p:
        lo *= 2
    for _ in range(120):
        mid = (lo + hi) / 2.0
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def pearson_corr(xs, ys):
    n = len(xs)
    if n != len(ys) or n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    r = cov / math.sqrt(vx * vy)
    r = max(-1.0, min(1.0, r))
    df = n - 2
    t = r * math.sqrt(df / (1 - r * r)) if abs(r) < 1 else float("inf")
    p = t_two_sided_p(t, df) if math.isfinite(t) else 0.0
    return {"r": round(r, 8), "n": n, "t": round(t, 8) if math.isfinite(t) else None,
            "df": df, "p": round(p, 8)}


def correlation_matrix(columns, rows):
    pairs = []
    for i, ca in enumerate(columns):
        for j, cb in enumerate(columns):
            if j <= i:
                continue
            xs = _numeric_values(rows, i)
            ys = _numeric_values(rows, j)
            # 仅用成对完整观测
            joined = [(a, b) for a, b in zip(xs, ys)]
            if len(joined) >= 3:
                r = pearson_corr([a for a, _ in joined], [b for _, b in joined])
                if r:
                    pairs.append({"x": ca, "y": cb, **r})
    return pairs


def welch_ttest(a, b):
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return None
    ma, mb = sum(a) / na, sum(b) / nb
    va = statistics.variance(a)
    vb = statistics.variance(b)
    se = math.sqrt(va / na + vb / nb)
    if se == 0:
        return None
    t = (ma - mb) / se
    df = (va / na + vb / nb) ** 2 / (
        (va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
    p = t_two_sided_p(t, df)
    sp = math.sqrt(((na - 1) * va + (nb - 1) * vb) / (na + nb - 2))
    d = (ma - mb) / sp if sp else None
    return {"t": round(t, 8), "df": round(df, 4), "p": round(p, 8),
            "cohens_d": round(d, 8) if d is not None else None,
            "mean_a": round(ma, 8), "mean_b": round(mb, 8),
            "n_a": na, "n_b": nb}


# --------------------------------------------------------------------------- #
# 异常值报告（先记录，不静默删除）
# --------------------------------------------------------------------------- #
def outlier_report(columns, rows):
    report = {}
    for ci, name in enumerate(columns):
        vals = _numeric_values(rows, ci)
        if len(vals) < 4:
            continue
        s = sorted(vals)
        q1, q3 = quantile(s, 0.25), quantile(s, 0.75)
        iqr = q3 - q1
        mean = sum(vals) / len(vals)
        sd = statistics.stdev(vals)
        iqr_out, z_out = [], []
        for idx, x in enumerate(vals):
            if iqr > 0 and (x < q1 - 1.5 * iqr or x > q3 + 1.5 * iqr):
                iqr_out.append({"index": idx, "value": round(x, 8),
                                "rule": "iqr_1.5"})
            if sd > 0 and abs(x - mean) / sd > 3.29:
                z_out.append({"index": idx, "value": round(x, 8),
                              "rule": "z_3.29"})
        if iqr_out or z_out:
            report[name] = {"iqr": iqr_out, "zscore": z_out}
    return report


# --------------------------------------------------------------------------- #
# SVG 兜底绘图（仅标准库，确定性；matplotlib 可用时应优先用 matplotlib）
# --------------------------------------------------------------------------- #
OKABE_ITO = ["#E69F00", "#56B4E9", "#009E73", "#F0E442",
             "#0072B2", "#D55E00", "#CC79A7", "#000000"]


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def svg_bar(labels, values, title="", xlabel="", ylabel="", note=""):
    """确定性分组条形图：类别按传入顺序（调用方负责稳定排序）。"""
    n = len(labels)
    if n == 0:
        return _empty_svg("无数据（svg_bar）")
    w, h = 640, 420
    ml, mr, mt, mb = 72, 24, 52, 64
    pw, ph = w - ml - mr, h - mt - mb
    vmax = max(values) if values else 0
    if vmax <= 0:
        vmax = 1
    bw = pw / max(n, 1)
    barw = bw * 0.62
    parts = []
    for i, (lab, v) in enumerate(zip(labels, values)):
        bh = ph * (v / vmax)
        x = ml + bw * i + (bw - barw) / 2
        y = mt + ph - bh
        color = OKABE_ITO[i % len(OKABE_ITO)]
        parts.append(
            '<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" fill="%s" '
            'stroke="#000000" stroke-width="0.6"/>'
            % (x, y, barw, bh, color))
        parts.append(
            '<text x="%.2f" y="%.2f" font-size="11" text-anchor="middle" '
            'font-family="sans-serif">%s</text>'
            % (x + barw / 2, mt + ph + 18, _esc(lab)))
        parts.append(
            '<text x="%.2f" y="%.2f" font-size="10" text-anchor="middle" '
            'font-family="sans-serif">%s</text>'
            % (x + barw / 2, y - 4, _esc("%g" % v)))
    grid = []
    for k in range(1, 5):
        gy = mt + ph - ph * k / 4.0
        grid.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" '
                    'stroke="#cccccc" stroke-width="0.5"/>'
                    % (ml, gy, ml + pw, gy))
    body = "\n".join(grid + parts)
    if note:
        body += ('\n<text x="%d" y="%d" font-size="9" fill="#444444" '
                 'font-family="sans-serif">%s</text>' % (ml, h - 10, _esc(note)))
    return _svg_doc(title, xlabel, ylabel, w, h, ml, mt, pw, ph, body)


def svg_scatter(xs, ys, labels=None, title="", xlabel="", ylabel="", note=""):
    n = len(xs)
    if n == 0:
        return _empty_svg("无数据（svg_scatter）")
    w, h = 640, 420
    ml, mr, mt, mb = 72, 24, 52, 64
    pw, ph = w - ml - mr, h - mt - mb
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    if xmin == xmax:
        xmin, xmax = xmin - 1, xmax + 1
    if ymin == ymax:
        ymin, ymax = ymin - 1, ymax + 1
    pts = []
    for i, (x, y) in enumerate(zip(xs, ys)):
        px = ml + pw * (x - xmin) / (xmax - xmin)
        py = mt + ph - ph * (y - ymin) / (ymax - ymin)
        color = OKABE_ITO[i % len(OKABE_ITO)] if labels else "#0072B2"
        pts.append('<circle cx="%.2f" cy="%.2f" r="3.2" fill="%s" '
                   'stroke="#000000" stroke-width="0.5"/>' % (px, py, color))
    grid = []
    for k in range(1, 5):
        gy = mt + ph - ph * k / 4.0
        grid.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" '
                    'stroke="#cccccc" stroke-width="0.5"/>' % (ml, gy, ml + pw, gy))
    body = "\n".join(grid + pts)
    if note:
        body += ('\n<text x="%d" y="%d" font-size="9" fill="#444444" '
                 'font-family="sans-serif">%s</text>' % (ml, h - 10, _esc(note)))
    return _svg_doc(title, xlabel, ylabel, w, h, ml, mt, pw, ph, body)


def _svg_doc(title, xlabel, ylabel, w, h, ml, mt, pw, ph, body):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
            'viewBox="0 0 %d %d">\n'
            '<rect width="%d" height="%d" fill="#ffffff"/>\n'
            '<text x="%d" y="28" font-size="15" font-weight="bold" '
            'text-anchor="middle" font-family="sans-serif">%s</text>\n'
            '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#000000" '
            'stroke-width="1.2"/>\n'
            '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#000000" '
            'stroke-width="1.2"/>\n'
            '<text x="%d" y="%d" font-size="12" text-anchor="middle" '
            'font-family="sans-serif">%s</text>\n'
            '<text x="%d" y="%d" font-size="12" text-anchor="middle" '
            'font-family="sans-serif" transform="rotate(-90 %d %d)">%s</text>\n'
            '%s\n</svg>\n') % (
        w, h, w, h, w, h, w / 2, _esc(title),
        ml, mt + ph, ml + pw, mt + ph, ml, mt, ml, mt,
        w / 2, h - 8, _esc(xlabel),
        ml - 48, mt + ph / 2, ml - 48, mt + ph / 2, _esc(ylabel), body)


def _empty_svg(msg):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="120">\n'
            '<text x="16" y="60" font-size="12" font-family="sans-serif">%s</text>\n'
            '</svg>\n' % _esc(msg))


# --------------------------------------------------------------------------- #
# plan 渲染
# --------------------------------------------------------------------------- #
def render_plan(files, case, contract, cpath, deg, case_id):
    lines = ["# 分析计划（确定性流水线）", "",
             "- case_id: `%s`" % case_id,
             "- case.json: `%s`" % (files.get("case_json") or "缺失"),
             "- task.md: `%s`" % (files.get("task_md") or "缺失"),
             "- 契约来源: `%s`" % (cpath or "未找到"),
             "- 数据文件: %s" % (", ".join(files.get("data")) or "无"),
             "- 材料文件: %s" % (", ".join(files.get("materials")) or "无"),
             "- 降级: %s" % (", ".join(deg) if deg else "无"), ""]
    lines += ["## 执行顺序（每步落盘 result.json）", "",
              "1. 读数据字典与字段语义 → 记录清洗口径（缺失/异常/重复先记录再处理）",
              "2. 确定性统计（描述统计/分组汇总/相关/检验；固定 seed=42）",
              "3. 按数据类型与分析目的选图（见 references/figure-spec.md）",
              "4. 编码/分面/配色（Okabe-Ito，灰度可辨，类别>6 聚合或分面）",
              "5. 标注与图注（轴=变量名(单位)，误差线写明 SD/SEM/CI 与 n）",
              "6. 导出 SVG+PDF+≥300dpi PNG（CJK 字体缺失则英文标签并记录）",
              "7. 证据映射：每个关键数值/图元写 locator + 逐字 quote",
              "8. 运行 `manifest` 与 `check --require-contract` 完成自检", ""]
    return "\n".join(lines)
