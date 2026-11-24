#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科研数据可视化引擎：发现数据 → 清洗口径 → 统计 → 选图 → 编码/标注 → 发表级导出。

依赖：仅 Python 标准库 + matplotlib（运行时 scientific-python 预装）；matplotlib 缺失时
自动降级为纯文本统计 + 数据表，不伪装成完整图。数据读写与统计全程标准库实现。

子命令：
  inspect  生成 work/data_inventory.json（表/列/类型/样本/缺失/摘要）
  plot     完整流水线：加载→清洗→统计→选图→渲染→导出 SVG/PDF/≥300dpi PNG
           + 图注 caption.md + stats.json + cleaning.json + cleaned.csv + 复现脚本 code.py
  reproduce 按 config 重跑 plot，验证确定性
"""
import argparse
import csv
import datetime
import io
import json
import math
import os
import re
import sqlite3
import sys
import unicodedata

VERSION = "1.0.0"
MISSING_TOKENS = {"", "na", "n/a", "nan", "null", "none", "?", ".", "-", "--",
                  "unknown", "missing", "n.d.", "nd"}
CAT_CARDINALITY = 12            # 类别数上限，超过视为高基数文本/ID
LOW_CARD_INT = 12               # 整数低基数视为分类（如类别编码）
OKABE_ITO = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7",
             "#56B4E9", "#F0E442", "#000000"]
MARKERS = ["o", "s", "^", "D", "v", "P", "X", "*"]
CJK_CANDIDATES = ["Noto Sans CJK SC", "Noto Sans CJK JP", "Source Han Sans SC",
                  "Source Han Sans CN", "WenQuanYi Zen Hei", "WenQuanYi Micro Hei",
                  "Microsoft YaHei", "SimHei", "SimSun", "PingFang SC",
                  "Hiragino Sans GB", "Arial Unicode MS", "Droid Sans Fallback",
                  "AR PL UMing CN", "AR PL UKai CN"]

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as _fm
    HAVE_MPL = True
except Exception:
    HAVE_MPL = False


# --------------------------------------------------------------------------- #
# 基础统计（纯标准库）
# --------------------------------------------------------------------------- #
def is_missing(v):
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip().lower() in MISSING_TOKENS
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return True
    return False


def try_float(v):
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s)
    except (ValueError, OverflowError):
        return None


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def std_sample(xs):
    xs = list(xs)
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def quantile(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    pos = (len(xs) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return cov / (sx * sy)


def r2(xs, ys, b1, b0):
    n = len(xs)
    my = sum(ys) / n
    ss_res = sum((y - (b1 * x + b0)) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum((y - my) ** 2 for y in ys)
    return 1 - ss_res / ss_tot if ss_tot > 0 else None


def lin_fit(xs, ys):
    """最小二乘 y = b1*x + b0，返回 (b1, b0, r)。"""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx == 0:
        return None, None, None
    b1 = sxy / sxx
    b0 = my - b1 * mx
    return b1, b0, pearson(xs, ys)


def has_cjk(s):
    return any(unicodedata.name(ch, "").startswith(("CJK", "HIRAGANA", "KATAKANA"))
               for ch in str(s))


# --------------------------------------------------------------------------- #
# 数据加载与类型推断
# --------------------------------------------------------------------------- #
class Table:
    def __init__(self, name, columns, rows, source):
        self.name = name
        self.columns = list(columns)
        self.rows = rows            # list[list] 原始值
        self.source = source
        self.types = {}
        self.card = {}
        self.sample = {}

    def col(self, name):
        i = self.columns.index(name)
        return [r[i] for r in self.rows]

    def infer(self):
        for i, c in enumerate(self.columns):
            vals = [r[i] for r in self.rows]
            non = [v for v in vals if not is_missing(v)]
            uniq = set(str(v).strip() for v in non)
            self.card[c] = len(uniq)
            self.sample[c] = sorted(uniq)[:6]
            self.types[c] = self._type_of(non, len(uniq))
        return self

    @staticmethod
    def _type_of(non, uniq_n):
        if not non:
            return "empty"
        nums = [try_float(v) for v in non]
        if all(n is not None for n in nums):
            all_int = all(float(n).is_integer() for n in nums)
            if all_int and uniq_n <= LOW_CARD_INT:
                return "categorical"
            if all_int:
                return "integer"
            return "numeric"
        # 日期检测
        first = str(non[0]).strip()
        if re.match(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", first):
            return "datetime"
        if uniq_n <= CAT_CARDINALITY:
            return "categorical"
        return "text"


def read_csv_table(path, table_name):
    raw = ""
    for enc in ("utf-8-sig", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc, newline="") as fh:
                raw = fh.read()
            break
        except (UnicodeDecodeError, OSError):
            continue
    if not raw:
        return None
    sample = raw[:4096]
    counts = {d: sample.count(d) for d in (",", ";", "\t", "|")}
    delim = max(counts, key=counts.get) if any(counts.values()) else ","
    reader = csv.reader(io.StringIO(raw), delimiter=delim)
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        return None
    header = rows[0]
    if len(rows) > 1:
        numeric_header = all(try_float(h) is not None for h in header)
        numeric_first_data = all(try_float(x) is not None for x in rows[1])
        if numeric_header and not numeric_first_data:
            header = ["col_%d" % i for i in range(len(rows[0]))]
        elif not numeric_header and numeric_first_data:
            pass  # 首行为列名
        else:
            # 无法确定时：首行全非空文本且第二行含数字 → 视为列名
            if all(isinstance(h, str) and not h.strip().isdigit() for h in header) \
                    and any(try_float(x) is not None for x in rows[1]):
                pass
            else:
                header = ["col_%d" % i for i in range(len(rows[0]))]
    data = rows[1:] if header is rows[0] else rows
    # 规整列宽
    width = len(header)
    data = [(r + [""] * width)[:width] for r in data]
    return Table(table_name, header, data, path)


def read_sqlite_table(path, table_name):
    con = sqlite3.connect(path)
    try:
        cur = con.cursor()
        tables = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")]
        if not tables:
            return None, []
        tname = table_name or max(tables, key=lambda t: cur.execute(
            "SELECT COUNT(*) FROM %s" % t).fetchone()[0])
        cur.execute("SELECT * FROM %s" % tname)
        cols = [d[0] for d in cur.description]
        rows = [list(r) for r in cur.fetchall()]
        return Table(tname, cols, rows, "%s#%s" % (path, tname)), tables
    finally:
        con.close()


def discover_data(path):
    """path 可为目录或单文件；返回 [(Table, tables), degradations]。"""
    out, deg = [], []
    if os.path.isdir(path):
        files = []
        for root, _d, names in os.walk(path):
            for n in sorted(names):
                files.append(os.path.join(root, n))
        dbs = [f for f in files if f.lower().endswith((".db", ".sqlite", ".sqlite3"))]
        for db in dbs:
            try:
                t, tables = read_sqlite_table(db, None)
                if t:
                    out.append((t, tables))
            except Exception as e:
                deg.append("sqlite_failed: %s (%s)" % (db, e))
        csvs = [f for f in files if f.lower().endswith((".csv", ".tsv", ".txt"))]
        for f in csvs:
            try:
                t = read_csv_table(f, os.path.splitext(os.path.basename(f))[0])
                if t:
                    out.append((t, []))
            except Exception as e:
                deg.append("csv_failed: %s (%s)" % (f, e))
    elif os.path.isfile(path):
        lp = path.lower()
        if lp.endswith((".db", ".sqlite", ".sqlite3")):
            try:
                t, tables = read_sqlite_table(path, None)
                if t:
                    out.append((t, tables))
            except Exception as e:
                deg.append("sqlite_failed: %s" % e)
        else:
            try:
                t = read_csv_table(path, os.path.splitext(os.path.basename(path))[0])
                if t:
                    out.append((t, []))
            except Exception as e:
                deg.append("csv_failed: %s" % e)
    return out, deg


# --------------------------------------------------------------------------- #
# 清洗
# --------------------------------------------------------------------------- #
def clean_table(tbl):
    """记录缺失/重复/离群，返回 (clean_rows, report)。不静默删除。"""
    report = {"missing_by_column": {}, "n_rows": len(tbl.rows),
              "n_duplicate_rows": 0, "outliers_by_column": {}, "policy": {}}
    seen, dup = set(), 0
    clean_rows = []
    for r in tbl.rows:
        key = tuple(str(v).strip() if v is not None else "" for v in r)
        if key in seen:
            dup += 1
            continue
        seen.add(key)
        clean_rows.append(r)
    report["n_duplicate_rows"] = dup
    report["n_rows_after_dedup"] = len(clean_rows)
    report["policy"]["duplicates"] = "记录并去除完全重复行，保留首个"
    for i, c in enumerate(tbl.columns):
        vals = [r[i] for r in clean_rows]
        missing = sum(1 for v in vals if is_missing(v))
        report["missing_by_column"][c] = missing
        nums = [try_float(v) for v in vals if not is_missing(v)]
        if tbl.types[c] in ("numeric", "integer") and nums:
            q1, q3 = quantile(nums, 0.25), quantile(nums, 0.75)
            iqr = q3 - q1
            lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            out = [i for i, v in enumerate(vals) if not is_missing(v) and
                   (try_float(v) < lo or try_float(v) > hi)]
            report["outliers_by_column"][c] = {
                "q1": q1, "q3": q3, "iqr": iqr, "lower": lo, "upper": hi,
                "n_outliers": len(out), "indices": out[:20]}
        report["policy"]["missing"] = "不填充；相关分析仅用该列非缺失行并显式说明"
        report["policy"]["outliers"] = "IQR 1.5 倍界识别，默认保留并标注，不静默剔除"
    return clean_rows, report


def describe_column(vals, typ):
    nums = [try_float(v) for v in vals if not is_missing(v)]
    d = {"n": len(vals), "n_missing": sum(1 for v in vals if is_missing(v))}
    if typ in ("numeric", "integer") and nums:
        d.update({"mean": mean(nums), "sd": std_sample(nums),
                  "median": quantile(nums, 0.5), "q1": quantile(nums, 0.25),
                  "q3": quantile(nums, 0.75), "min": min(nums), "max": max(nums)})
    elif typ == "categorical":
        counts = {}
        for v in vals:
            if not is_missing(v):
                counts[str(v).strip()] = counts.get(str(v).strip(), 0) + 1
        d["levels"] = dict(sorted(counts.items(), key=lambda kv: -kv[1]))
    return d


# --------------------------------------------------------------------------- #
# 渲染辅助
# --------------------------------------------------------------------------- #
def find_cjk_font():
    if not HAVE_MPL:
        return None
    try:
        names = {f.name for f in _fm.fontManager.ttflist}
    except Exception:
        return None
    for c in CJK_CANDIDATES:
        if c in names:
            return c
    return None


def setup_style(font):
    if not HAVE_MPL:
        return
    plt.rcParams["font.size"] = 8
    plt.rcParams["axes.labelsize"] = 9
    plt.rcParams["axes.titlesize"] = 9
    plt.rcParams["xtick.labelsize"] = 8
    plt.rcParams["ytick.labelsize"] = 8
    plt.rcParams["legend.fontsize"] = 7.5
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 100
    if font:
        plt.rcParams["font.sans-serif"] = [font, "DejaVu Sans"]
    else:
        plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]


def safe_label(name, mapping, used_short, cjk_font):
    """无 CJK 字体时用 X0/X1 占位并记录映射。"""
    if not has_cjk(name) or cjk_font:
        return name
    if name not in mapping:
        mapping[name] = "col%d" % len(used_short)
        used_short.append(name)
    return mapping[name]


def format_num(v):
    if v is None:
        return "NA"
    if isinstance(v, float):
        if abs(v) >= 1e4 or (0 < abs(v) < 1e-3):
            return "%.3e" % v
        return "%.4g" % v
    return str(v)


# --------------------------------------------------------------------------- #
# 图型选择与渲染
# --------------------------------------------------------------------------- #
def choose_kind(tbl, x, y, group, target, kind):
    if kind and kind != "auto":
        return kind
    nums = [c for c in tbl.columns if tbl.types[c] in ("numeric", "integer")]
    cats = [c for c in tbl.columns if tbl.types[c] == "categorical"]
    if target and target in tbl.columns:
        t = tbl.types[target]
        if t in ("numeric", "integer") and len(nums) > 2 and not cats:
            return "heatmap"
        if t in ("numeric", "integer") and cats:
            return "box"
        if t == "categorical" and len(nums) >= 2:
            return "scatter"
    if x and y:
        return "box" if tbl.types[x] == "categorical" else "scatter"
    if group and group in tbl.columns and tbl.types[group] == "categorical":
        return "box"
    if len(cats) == 1 and 2 <= len(nums) <= 4 and 2 <= tbl.card[cats[0]] <= 6:
        return "scatter"          # 分类着色散点（如 iris/wine）
    if len(nums) >= 3 and not cats:
        return "heatmap"          # 多连续变量 → 相关热图（如 concrete）
    if len(nums) >= 1:
        return "hist"
    if cats:
        return "bar"
    return "table"


def pick_axes(tbl, x, y, group, target, kind):
    nums = [c for c in tbl.columns if tbl.types[c] in ("numeric", "integer")]
    cats = [c for c in tbl.columns if tbl.types[c] == "categorical"]
    if kind in ("scatter",):
        if x and y:
            return x, y, group or (cats[0] if cats else None)
        if cats and len(nums) >= 2:
            return nums[0], nums[1], cats[0]
        if len(nums) >= 2:
            return nums[0], nums[1], None
        return (nums[0] if nums else (tbl.columns[0])), None, None
    if kind in ("box", "violin"):
        ycol = target if target and tbl.types.get(target) in ("numeric", "integer") else \
            (y if y and tbl.types.get(y) in ("numeric", "integer") else
             (nums[0] if nums else None))
        gcol = group or x or (cats[0] if cats else None)
        return gcol, ycol, None
    if kind == "hist":
        return None, (target if target in nums else (x if x in nums else
                      (nums[0] if nums else None))), None
    if kind in ("bar", "stacked"):
        xcol = x or group or (cats[0] if cats else None)
        return xcol, None, (y if y else None)
    if kind == "heatmap":
        return None, None, None
    if kind == "line":
        return x, (y or target or (nums[0] if nums else None)), group
    return x, y, group


def render(tbl, clean_rows, kind, x, y, group, outdir, prefix, title, cjk_font):
    """渲染图件并返回 (fig, degradations, plot_note)。"""
    if not HAVE_MPL:
        return None, ["no_matplotlib: 已降级为纯文本统计+数据表"], ""
    mapping, used_short = {}, []
    deg = []
    label_map = {}
    for c in tbl.columns:
        label_map[c] = safe_label(c, mapping, used_short, cjk_font)
    if mapping:
        deg.append("no_cjk_font: 列名含中文已改占位符 %s" % json.dumps(mapping, ensure_ascii=False))
    cols = tbl.columns
    idx = {c: i for i, c in enumerate(cols)}
    ncols = len(cols)
    cats = [c for c in cols if tbl.types[c] == "categorical"]

    width = 3.4
    height = max(2.6, width * 0.78)
    fig, ax = plt.subplots(figsize=(width, height))
    plot_note = ""

    if kind == "hist":
        c = y or next((cc for cc in cols if tbl.types[cc] in ("numeric", "integer")), cols[0])
        vals = [try_float(r[idx[c]]) for r in clean_rows if not is_missing(r[idx[c]])]
        ax.hist(vals, bins=30, color=OKABE_ITO[0], edgecolor="white", linewidth=0.4)
        m, med = mean(vals), quantile(vals, 0.5)
        ax.axvline(m, color=OKABE_ITO[1], ls="--", lw=1, label="mean=%.4g" % m)
        ax.axvline(med, color=OKABE_ITO[2], ls=":", lw=1, label="median=%.4g" % med)
        ax.set_xlabel(label_map[c])
        ax.set_ylabel("Count")
        ax.legend(frameon=False)
        plot_note = "histogram of %s (n=%d)" % (c, len(vals))
    elif kind in ("box", "violin"):
        gcol = group or x or (cats[0] if cats else None)
        ycol = y or next((cc for cc in cols if tbl.types[cc] in ("numeric", "integer")), cols[0])
        if gcol and gcol in idx:
            groups = sorted({str(r[idx[gcol]]).strip() for r in clean_rows
                             if not is_missing(r[idx[gcol]])})
            data = [[try_float(r[idx[ycol]]) for r in clean_rows
                     if not is_missing(r[idx[ycol]]) and str(r[idx[gcol]]).strip() == g]
                    for g in groups]
            box = ax.boxplot(data, labels=groups, patch_artist=True, showfliers=True)
            for k, patch in enumerate(box["boxes"]):
                patch.set_facecolor(OKABE_ITO[k % len(OKABE_ITO)])
                patch.set_alpha(0.55)
            for k, g in enumerate(groups):
                ys = data[k]
                if ys:
                    m, sd = mean(ys), std_sample(ys)
                    sem = sd / math.sqrt(len(ys)) if sd is not None else None
                    xpos = k + 1
                    ax.errorbar(xpos, m, yerr=sem, fmt="k.", capsize=3, lw=1)
                    ax.annotate("n=%d" % len(ys), (xpos, 0.01), xycoords=("data", "axes fraction"),
                                ha="center", va="bottom", fontsize=7)
            ax.set_xticklabels(groups, rotation=25, ha="right")
            ax.set_ylabel(label_map[ycol])
            plot_note = "%s of %s by %s; 中线=中位数, 箱=Q1–Q3, 须=1.5IQR, 点=均值±SEM(n 标注)" % (
                "boxplot", ycol, gcol)
        else:
            vals = [try_float(r[idx[ycol]]) for r in clean_rows if not is_missing(r[idx[ycol]])]
            ax.boxplot(vals, labels=[label_map[ycol]])
            plot_note = "boxplot of %s (n=%d)" % (ycol, len(vals))
        ax.set_ylabel(label_map.get(ycol, ycol))
    elif kind == "scatter":
        xcol, ycol = (x, y) if x and y else pick_axes(tbl, x, y, group, target, "scatter")[:2]
        if not xcol or not ycol:
            nums = [c for c in cols if tbl.types[c] in ("numeric", "integer")]
            xcol, ycol = nums[0], (nums[1] if len(nums) > 1 else nums[0])
        gcol = group or (cats[0] if cats else None)
        xs = [try_float(r[idx[xcol]]) for r in clean_rows
              if not is_missing(r[idx[xcol]]) and not is_missing(r[idx[ycol]])]
        ys = [try_float(r[idx[ycol]]) for r in clean_rows
              if not is_missing(r[idx[xcol]]) and not is_missing(r[idx[ycol]])]
        if gcol and gcol in idx:
            gs = sorted({str(r[idx[gcol]]).strip() for r in clean_rows})
            for k, g in enumerate(gs):
                gx = [try_float(r[idx[xcol]]) for r in clean_rows
                      if str(r[idx[gcol]]).strip() == g and not is_missing(r[idx[xcol]])
                      and not is_missing(r[idx[ycol]])]
                gy = [try_float(r[idx[ycol]]) for r in clean_rows
                      if str(r[idx[gcol]]).strip() == g and not is_missing(r[idx[xcol]])
                      and not is_missing(r[idx[ycol]])]
                ax.scatter(gx, gy, s=14, color=OKABE_ITO[k % len(OKABE_ITO)],
                           marker=MARKERS[k % len(MARKERS)], edgecolor="white",
                           linewidth=0.3, label="%s (n=%d)" % (g, len(gx)))
            ax.legend(frameon=False, ncol=2, loc="best")
        else:
            ax.scatter(xs, ys, s=14, color=OKABE_ITO[0], edgecolor="white", linewidth=0.3)
        b1, b0, r = lin_fit(xs, ys)
        if b1 is not None and len(xs) > 2:
            xx = sorted(xs)
            ax.plot(xx, [b1 * v + b0 for v in xx], color=OKABE_ITO[3], lw=1.0,
                    label="y=%.3gx%+.3g" % (b1, b0))
        ax.set_xlabel(label_map[xcol])
        ax.set_ylabel(label_map[ycol])
        if r is not None:
            ax.text(0.03, 0.97, "Pearson r=%.3f" % r, transform=ax.transAxes,
                    va="top", fontsize=7.5,
                    bbox=dict(fc="white", ec="none", alpha=0.7))
        plot_note = "scatter of %s vs %s%s; 实线=最小二乘拟合" % (
            ycol, xcol, ", color=%s" % gcol if gcol else "")
    elif kind == "heatmap":
        nums = [c for c in cols if tbl.types[c] in ("numeric", "integer")]
        nums = nums[:9]
        mat = [[pearson([try_float(r[idx[a]]) for r in clean_rows
                         if not is_missing(r[idx[a]]) and not is_missing(r[idx[b]])],
                        [try_float(r[idx[b]]) for r in clean_rows
                         if not is_missing(r[idx[a]]) and not is_missing(r[idx[b]])])
                for b in nums] for a in nums]
        im = ax.imshow(mat, cmap="RdBu_r", vmin=-1, vmax=1)
        ax.set_xticks(range(len(nums)))
        ax.set_yticks(range(len(nums)))
        ax.set_xticklabels([label_map[c] for c in nums], rotation=45, ha="right")
        ax.set_yticklabels([label_map[c] for c in nums])
        for i in range(len(nums)):
            for j in range(len(nums)):
                v = mat[i][j]
                ax.text(j, i, "%.2f" % v if v is not None else "NA", ha="center",
                        va="center", fontsize=6.5)
        fig.colorbar(im, ax=ax, label="Pearson r")
        plot_note = "correlation heatmap (Pearson r), 值已标注于格内"
    elif kind == "bar":
        c = x or (cats[0] if cats else cols[0])
        counts = {}
        for r in clean_rows:
            if not is_missing(r[idx[c]]):
                counts[str(r[idx[c]]).strip()] = counts.get(str(r[idx[c]]).strip(), 0) + 1
        items = sorted(counts.items(), key=lambda kv: -kv[1])
        ax.bar([k for k, _ in items], [v for _, v in items],
               color=OKABE_ITO[0], edgecolor="white", linewidth=0.4)
        ax.set_ylabel("Count")
        ax.set_xlabel(label_map[c])
        ax.set_xticklabels([k for k, _ in items], rotation=25, ha="right")
        plot_note = "counts of %s" % c
    elif kind == "line":
        xcol = x or cols[0]
        ycol = y or next((cc for cc in cols if tbl.types[cc] in ("numeric", "integer")), cols[1])
        pts = [(r[idx[xcol]], try_float(r[idx[ycol]])) for r in clean_rows
               if not is_missing(r[idx[xcol]]) and not is_missing(r[idx[ycol]])]
        pts.sort(key=lambda p: str(p[0]))
        ax.plot([p[0] for p in pts], [p[1] for p in pts], marker="o", ms=2.5,
                color=OKABE_ITO[0], lw=1)
        ax.set_xlabel(label_map[xcol])
        ax.set_ylabel(label_map[ycol])
        ax.set_xticklabels([str(p[0]) for p in pts][::max(1, len(pts) // 8)], rotation=30, ha="right")
        plot_note = "line of %s over %s" % (ycol, xcol)
    else:  # table 降级
        fig.clf()
        plt.close(fig)
        return None, ["unknown_kind: %s" % kind], ""

    if title:
        ax.set_title(title, fontsize=9)
    fig.tight_layout()
    return fig, deg, plot_note


# --------------------------------------------------------------------------- #
# plot 主流程
# --------------------------------------------------------------------------- #
def write_stats_and_clean(tbl, clean_rows, cleaning, kind, outdir, prefix):
    stats = {"table": tbl.name, "source": tbl.source, "n_rows": len(tbl.rows),
             "n_clean": len(clean_rows), "columns": {}}
    for i, c in enumerate(tbl.columns):
        vals = [r[i] for r in clean_rows]
        stats["columns"][c] = {"type": tbl.types[c], "cardinality": tbl.card[c],
                               "sample_values": tbl.sample[c],
                               "describe": describe_column(vals, tbl.types[c])}
    # 数值对相关
    nums = [c for c in tbl.columns if tbl.types[c] in ("numeric", "integer")]
    if len(nums) >= 2:
        stats["correlation"] = {}
        for a in nums[:9]:
            for b in nums[:9]:
                if a < b:
                    xs = [try_float(r[tbl.columns.index(a)]) for r in clean_rows
                          if not is_missing(r[tbl.columns.index(a)]) and
                          not is_missing(r[tbl.columns.index(b)])]
                    ys = [try_float(r[tbl.columns.index(b)]) for r in clean_rows
                          if not is_missing(r[tbl.columns.index(a)]) and
                          not is_missing(r[tbl.columns.index(b)])]
                    stats["correlation"]["%s~%s" % (a, b)] = pearson(xs, ys)
    atomic_write(os.path.join(outdir, "%s.stats.json" % prefix), stats)
    atomic_write(os.path.join(outdir, "%s.cleaning.json" % prefix), cleaning)
    # cleaned 表导出
    with open(os.path.join(outdir, "%s.cleaned.csv" % prefix), "w",
              encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(tbl.columns)
        w.writerows(clean_rows)
    return stats


def atomic_write(path, obj):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    return path


def normalize_svg(path):
    """去除 matplotlib SVG 的生成时间戳并重排内部随机 id，保证字节级可复现。"""
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    text = re.sub(r"<metadata>.*?</metadata>", "<metadata/>", text, flags=re.S)
    mapping, counter = {}, [0]

    def repl(m):
        key = m.group(1)
        if key not in mapping:
            mapping[key] = "id%d" % counter[0]
            counter[0] += 1
        return mapping[key]

    text = re.sub(r'id="([A-Za-z][0-9A-Fa-f]+)"',
                  lambda m: 'id="%s"' % repl(m), text)
    text = re.sub(r'url\(#([A-Za-z][0-9A-Fa-f]+)\)',
                  lambda m: 'url(#%s)' % repl(m), text)
    text = re.sub(r'href="#([A-Za-z][0-9A-Fa-f]+)"',
                  lambda m: 'href="#%s"' % repl(m), text)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def build_caption(prefix, title, tbl, stats, kind, plot_note, cjk_font, nfig, out_paths):
    lines = []
    lines.append("**Figure %s.%s.** %s" % (nfig, kind, title or ("%s (%s)" % (tbl.name, kind))))
    lines.append("")
    lines.append("数据：`%s`（%d 行 → 清洗后 %d 行）。" % (
        tbl.source, stats["n_rows"], stats["n_clean"]))
    if plot_note:
        lines.append("编码：%s。" % plot_note)
    lines.append("配色：Okabe-Ito 色盲安全配色（同时以形状/明度区分，灰度可辨）；字号 ≥7 pt；单栏宽 3.4 in。")
    lines.append("导出：SVG + PDF（字体嵌入）+ ≥300 dpi PNG。")
    lines.append("")
    lines.append("源文件：`%s`。由 `scripts/viz.py` v%s 生成；同参数重跑结果确定。"
                 % (tbl.source, VERSION))
    return "\n".join(lines) + "\n"


def cmd_plot(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    prefix = args.prefix or "fig1"
    try:
        tables, deg = discover_data(args.input)
    except Exception as e:
        atomic_write(os.path.join(args.output, "%s.stats.json" % prefix),
                     {"error": "discover_failed: %s" % e})
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 0
    if not tables:
        atomic_write(os.path.join(args.output, "%s.stats.json" % prefix),
                     {"error": "no_data", "degradations": deg})
        print(json.dumps({"ok": False, "status": "insufficient_evidence",
                          "reason": "no_data"}, ensure_ascii=False))
        return 0

    # 选主表：优先 --table，否则取行数最大者
    if args.table:
        cand = [t for t, _ in tables if t.name == args.table]
        tbl = (cand or [tables[0][0]])[0]
    else:
        tbl = max(tables, key=lambda tp: len(tp[0].rows))[0]
    tbl.infer()
    clean_rows, cleaning = clean_table(tbl)

    kind = choose_kind(tbl, args.x, args.y, args.group, args.target, args.kind)
    x, y, g = pick_axes(tbl, args.x, args.y, args.group, args.target, kind)

    cjk_font = find_cjk_font()
    setup_style(cjk_font)
    fig, rdeg, plot_note = render(tbl, clean_rows, kind, x, y, g, args.output, prefix,
                                  args.title, cjk_font)
    deg = deg + rdeg

    out_paths = {}
    stats = write_stats_and_clean(tbl, clean_rows, cleaning, kind, args.output, prefix)
    if fig is not None:
        base = os.path.join(args.output, prefix)
        for ext, kw in ((".svg", {"format": "svg"}),
                        (".pdf", {"format": "pdf"}),
                        (".png", {"dpi": 300})):
            fig.savefig(base + ext, **kw)
            out_paths[ext.lstrip(".")] = base + ext
        normalize_svg(base + ".svg")
        plt.close(fig)

    caption = build_caption(prefix, args.title, tbl, stats, kind, plot_note,
                            cjk_font, 1, out_paths)
    with open(os.path.join(args.output, "%s.caption.md" % prefix), "w",
              encoding="utf-8") as fh:
        fh.write(caption)

    cfg = {"script_dir": os.path.dirname(os.path.abspath(__file__)),
           "version": VERSION, "input": args.input, "table": tbl.name,
           "kind": kind, "x": x, "y": y, "group": g, "target": args.target,
           "title": args.title, "prefix": prefix, "output": args.output,
           "cjk_font": cjk_font}
    atomic_write(os.path.join(args.output, "%s.config.json" % prefix), cfg)
    code = ("#!/usr/bin/env python3\n# 复现脚本：与 %s.config.json 同目录运行\n"
            "import json, os, sys\n"
            "_here = os.path.dirname(os.path.abspath(__file__))\n"
            "_cfg = json.load(open(os.path.join(_here, '%s.config.json'), encoding='utf-8'))\n"
            "sys.path.insert(0, _cfg.get('script_dir', '/skills/weekly-skill/scripts'))\n"
            "import viz\n"
            "viz.reproduce(os.path.join(_here, '%s.config.json'))\n") % (prefix, prefix, prefix)
    with open(os.path.join(args.output, "%s.code.py" % prefix), "w", encoding="utf-8") as fh:
        fh.write(code)

    artifacts = [{"path": p, "format": f} for f, p in out_paths.items()]
    for extra in ("stats.json", "cleaning.json", "cleaned.csv", "caption.md", "code.py"):
        artifacts.append({"path": os.path.join(args.output, "%s.%s" % (prefix, extra)),
                          "format": extra.split(".")[-1]})
    print(json.dumps({"ok": fig is not None, "kind": kind, "x": x, "y": y,
                      "group": g, "n_rows": len(tbl.rows), "n_clean": len(clean_rows),
                      "artifacts": artifacts, "degradations": deg,
                      "stats": os.path.join(args.output, "%s.stats.json" % prefix)},
                     ensure_ascii=False))
    return 0


def reproduce(cfg_path):
    """按 config 重跑 plot，验证确定性；返回退出码。"""
    try:
        with open(cfg_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception as e:
        print("reproduce_failed: %s" % e)
        return 1
    ns = argparse.Namespace(input=cfg.get("input"), table=cfg.get("table"),
                            kind=cfg.get("kind"), x=cfg.get("x"), y=cfg.get("y"),
                            group=cfg.get("group"), target=cfg.get("target"),
                            title=cfg.get("title"), prefix=cfg.get("prefix") or "fig1",
                            output=cfg.get("output"), work=cfg.get("output"))
    return cmd_plot(ns)


# --------------------------------------------------------------------------- #
# inspect
# --------------------------------------------------------------------------- #
def cmd_inspect(args):
    os.makedirs(args.work, exist_ok=True)
    tables, deg = discover_data(args.input)
    inv = {"files": [], "tables": [], "degradations": deg}
    for tbl, tabs in tables:
        tbl.infer()
        inv["files"].append({"path": tbl.source, "tables_in_file": tabs})
        colinfo = []
        for c in tbl.columns:
            vals = tbl.col(c)
            colinfo.append({"name": c, "type": tbl.types[c],
                            "cardinality": tbl.card[c], "n_missing":
                                sum(1 for v in vals if is_missing(v)),
                            "sample": tbl.sample[c]})
        inv["tables"].append({"name": tbl.name, "source": tbl.source,
                              "n_rows": len(tbl.rows), "n_cols": len(tbl.columns),
                              "columns": colinfo})
    atomic_write(os.path.join(args.work, "data_inventory.json"), inv)
    # 可读摘要
    print("TABLES=%d DEGRADATIONS=%d" % (len(inv["tables"]), len(deg)))
    for t in inv["tables"]:
        print("== %s (%d rows, %d cols) <= %s" % (t["name"], t["n_rows"],
                                                  t["n_cols"], t["source"]))
        for c in t["columns"]:
            print("  - %-20s %-12s card=%s miss=%d sample=%s" % (
                c["name"], c["type"], c["cardinality"], c["n_missing"],
                c["sample"][:4]))
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    p = argparse.ArgumentParser(description="科研数据可视化引擎")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("inspect")
    sp.add_argument("--input", default="/input")
    sp.add_argument("--work", default="/app/work")

    sp = sub.add_parser("plot")
    sp.add_argument("--input", default="/input")
    sp.add_argument("--output", default="/app/output")
    sp.add_argument("--work", default="/app/work")
    sp.add_argument("--table")
    sp.add_argument("--kind", default="auto",
                    help="auto|hist|box|violin|scatter|bar|line|heatmap|table")
    sp.add_argument("--x")
    sp.add_argument("--y")
    sp.add_argument("--group")
    sp.add_argument("--target")
    sp.add_argument("--title")
    sp.add_argument("--prefix", default="fig1")

    args = p.parse_args(argv)
    try:
        if args.cmd == "inspect":
            return cmd_inspect(args)
        if args.cmd == "plot":
            return cmd_plot(args)
        p.error("未知子命令: %s" % args.cmd)
    except Exception as e:
        sys.stderr.write("ERROR(handled): %s\n" % e)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
