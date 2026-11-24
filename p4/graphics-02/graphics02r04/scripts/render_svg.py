#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""figtrace 引擎：容错读取、表格解析、列型推断、确定性 SVG 渲染、产物格式校验。

仅依赖 Python 标准库。设计约定：
1. 所有对外函数不抛出未捕获异常；失败返回 (None, errors)，调用方决定降级。
2. 渲染确定性：相同输入 -> 相同 SVG 字节（无随机数、无时间戳）。
3. 溯源：每个图元携带 data-source / data-row / data-col / data-value 属性。
4. 文字一律用 <text> 节点，绝不转曲；颜色用可读的十六进制。
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import sys
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

MAX_FILE_BYTES = 2_000_000

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"
GZIP_MAGIC = b"\x1f\x8b"

CJK_FONT_STACK = (
    "'Noto Sans CJK SC','Noto Sans SC','Source Han Sans SC',"
    "'Microsoft YaHei','PingFang SC','SimSun','Arial Unicode MS',sans-serif"
)


# --------------------------------------------------------------------------- #
# 容错文件读取
# --------------------------------------------------------------------------- #
def read_bytes(path, max_bytes=MAX_FILE_BYTES):
    if not os.path.exists(path):
        return None, ["missing file: %s" % path]
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, ["stat failed: %s" % e]
    if size > max_bytes:
        return None, ["file too large (%dB > %dB): %s" % (size, max_bytes, path)]
    try:
        with open(path, "rb") as f:
            return f.read(), []
    except OSError as e:
        return None, ["read failed: %s" % e]


def decode_text(raw):
    if raw is None:
        return None
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    for enc in ("utf-8", "gb18030", "big5", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1", errors="replace")


def read_text(path, max_bytes=MAX_FILE_BYTES):
    raw, errs = read_bytes(path, max_bytes)
    if errs:
        return None, errs
    return decode_text(raw), []


def load_json_tolerant(path, max_bytes=MAX_FILE_BYTES):
    raw, errs = read_bytes(path, max_bytes)
    if errs:
        return None, errs
    if raw is None or not raw.strip():
        return None, ["empty file: %s" % path]
    text = decode_text(raw)
    try:
        return json.loads(text), []
    except Exception as e:  # noqa: BLE001
        return None, ["invalid json: %s" % e]


def write_json(path, obj):
    """写 UTF-8(无 BOM) JSON，缩进 2，保留中文。失败返回错误列表。"""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        return []
    except OSError as e:
        return ["write failed %s: %s" % (path, e)]


# --------------------------------------------------------------------------- #
# 输入发现
# --------------------------------------------------------------------------- #
def discover_inputs(input_dir):
    """扫描输入目录，识别材料与可解析数据文件。返回 (materials, issues)。"""
    materials = []
    issues = []
    if not os.path.isdir(input_dir):
        return materials, ["input dir missing: %s" % input_dir]
    try:
        names = sorted(os.listdir(input_dir))
    except OSError as e:
        return materials, ["listdir failed: %s" % e]
    for name in names:
        p = os.path.join(input_dir, name)
        if os.path.isdir(p):
            continue
        ext = os.path.splitext(name)[1].lower()
        kind = {
            ".json": "json", ".md": "markdown", ".txt": "text",
            ".csv": "csv", ".tsv": "tsv", ".tab": "tsv",
            ".pdf": "pdf", ".png": "png", ".jpg": "jpg", ".jpeg": "jpg",
            ".svg": "svg", ".pptx": "pptx", ".xlsx": "xlsx", ".xls": "xls",
            ".docx": "docx", ".eps": "eps", ".tif": "tif", ".tiff": "tif",
        }.get(ext, "other")
        try:
            size = os.path.getsize(p)
        except OSError:
            size = 0
        materials.append({
            "path": p,
            "basename": name,
            "ext": ext or "",
            "kind": kind,
            "size": size,
            "parseable_here": kind in ("json", "csv", "tsv", "markdown", "text"),
        })
    return materials, issues


# --------------------------------------------------------------------------- #
# 表格解析
# --------------------------------------------------------------------------- #
def _guess_delimiter(text):
    first_line = ""
    for line in text.splitlines():
        if line.strip():
            first_line = line
            break
    if "|" in first_line and first_line.strip().lstrip("|").count("|") >= 1:
        return "markdown"
    counts = {d: first_line.count(d) for d in (",", "\t", ";")}
    best = max(counts, key=counts.get)
    if counts[best] >= 1:
        return "csv" if best == "," else ("tsv" if best == "\t" else "semicolon")
    return "whitespace"


def _split_markdown_row(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def parse_table(text, name="table"):
    """解析 CSV/TSV/Markdown/空白分隔表格。返回 (table, issues)。"""
    issues = []
    lines = [ln for ln in text.splitlines() if ln.strip() != ""]
    if not lines:
        return None, ["no non-empty lines in %s" % name]
    dialect = _guess_delimiter(text)
    rows = []
    if dialect == "markdown":
        for ln in lines:
            if re.match(r"^\s*\|?\s*:?-{2,}", ln):
                continue  # 分隔行
            rows.append(_split_markdown_row(ln))
    elif dialect == "csv":
        try:
            rows = list(csv.reader(io.StringIO(text)))
        except csv.Error as e:
            return None, ["csv parse failed: %s" % e]
    elif dialect == "tsv":
        try:
            rows = list(csv.reader(io.StringIO(text), delimiter="\t"))
        except csv.Error as e:
            return None, ["tsv parse failed: %s" % e]
    elif dialect == "semicolon":
        try:
            rows = list(csv.reader(io.StringIO(text), delimiter=";"))
        except csv.Error as e:
            return None, ["csv parse failed: %s" % e]
    else:
        rows = [ln.split() for ln in lines]

    rows = [r for r in rows if any(c.strip() != "" for c in r)]
    if not rows:
        return None, ["no data rows in %s" % name]
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]

    header, data = None, rows
    if _looks_like_header(rows[0]):
        header = rows[0]
        data = rows[1:]
        if len(header) != len(set(header)):
            issues.append("duplicate header names in %s" % name)
    return {
        "name": name,
        "dialect": dialect,
        "header": header,
        "rows": data,
        "n_rows": len(data),
        "n_cols": width,
    }, issues


def _looks_like_header(row):
    if not row:
        return False
    numeric = 0
    for c in row:
        if _try_float(c) is not None:
            numeric += 1
    return numeric == 0  # 全非数值 -> 视为表头


def _try_float(s):
    s = s.strip()
    if s == "":
        return None
    s2 = s.replace(",", "").replace("±", "").strip()
    try:
        return float(s2)
    except ValueError:
        return None


def infer_columns(table):
    """推断列类型并检测缺失/量级异常。返回 (columns, issues)。"""
    if not table:
        return [], ["no table to infer"]
    header = table["header"] or ["col%d" % (i + 1) for i in range(table["n_cols"])]
    rows = table["rows"]
    cols = []
    issues = []
    for j in range(table["n_cols"]):
        vals = [r[j] if j < len(r) else "" for r in rows]
        numeric_vals = []
        missing = 0
        non_numeric = 0
        for v in vals:
            if v.strip() == "":
                missing += 1
                continue
            f = _try_float(v)
            if f is None:
                non_numeric += 1
            else:
                numeric_vals.append(f)
        total = len(vals)
        kind = "numeric" if (non_numeric == 0 and numeric_vals) else (
            "categorical" if non_numeric > 0 else "empty")
        col = {
            "name": header[j] if j < len(header) else "col%d" % (j + 1),
            "index": j,
            "kind": kind,
            "missing": missing,
            "non_numeric": non_numeric,
            "n": total - missing,
        }
        if numeric_vals:
            col["min"] = min(numeric_vals)
            col["max"] = max(numeric_vals)
            col["mean"] = sum(numeric_vals) / len(numeric_vals)
            col["has_negative"] = any(x < 0 for x in numeric_vals)
            if col["min"] > 0 and col["max"] > 0:
                span = math.log10(col["max"] / col["min"])
                col["orders_of_magnitude"] = round(span, 2)
                if span > 3:
                    issues.append(
                        "col %r spans %.1f orders of magnitude; unit/magnitude suspect"
                        % (col["name"], span))
        cols.append(col)
    if table["header"] is None:
        issues.append("no header row detected in %s; using col1..colN" % table["name"])
    return cols, issues


def auto_figure_spec(table, cols, chart_type="bar"):
    """从解析表生成 figure spec（尽力而为，问题记入 issues）。返回 (spec, issues)。"""
    if not table or not cols:
        return None, ["cannot build spec: no table"]
    issues = []
    cats = [c for c in cols if c["kind"] == "categorical"]
    nums = [c for c in cols if c["kind"] == "numeric"]
    if not nums:
        return None, ["no numeric column found; cannot encode values"]
    x_col = cats[0] if cats else None
    categories = []
    if x_col is not None:
        j = x_col["index"]
        categories = [r[j] if j < len(r) else "" for r in table["rows"]]
        categories = [c if c.strip() else ("row%d" % (i + 1)) for i, c in enumerate(categories)]
    else:
        categories = ["row%d" % (i + 1) for i in range(table["n_rows"])]

    err_cols = {c["index"]: c for c in nums
                if re.search(r"(?i)(err|sd|sem|se|ci|±|error|_se$|_ci$)", c["name"])}
    value_cols = [c for c in nums if c["index"] not in err_cols]
    if not value_cols:
        value_cols = nums

    series = []
    for c in value_cols:
        j = c["index"]
        vals = []
        errs = []
        for i, r in enumerate(table["rows"]):
            v = r[j] if j < len(r) else ""
            fv = _try_float(v)
            vals.append(fv if fv is not None else None)
            errs.append(None)
        s = {
            "name": c["name"],
            "col": j,
            "values": vals,
            "errors": errs,
            "error_type": "SD",
            "n": None,
            "source_map": [{
                "series": c["name"], "category": categories[i],
                "source": table["name"], "row": i + 1, "col": j, "value": vals[i],
            } for i in range(len(vals))],
        }
        # 找同前缀误差列
        base = c["name"].split(".")[0].split("_")[0].lower()
        for ei, ec in err_cols.items():
            if base and base in ec["name"].lower():
                s["errors"] = [_try_float(r[ei] if ei < len(r) else "") for r in table["rows"]]
                s["error_type"] = "SD" if "sd" in ec["name"].lower() else (
                    "SEM" if "sem" in ec["name"].lower() else "CI")
                break
        if s["errors"] and all(e is None for e in s["errors"]):
            s["errors"] = [None] * len(vals)
        series.append(s)
        if any(v is None for v in vals):
            issues.append("series %r has missing values" % c["name"])

    spec = {
        "chart_type": chart_type,
        "title": table["name"],
        "x_label": x_col["name"] if x_col else "Row",
        "y_label": "Value",
        "categories": categories,
        "series": series,
        "significance": [],
        "width": 840,
        "height": 560,
        "dpi_note": "SVG is vector; PNG/PDF must be rendered separately",
    }
    return spec, issues


# --------------------------------------------------------------------------- #
# SVG 渲染（确定性、可溯源、<text> 文本）
# --------------------------------------------------------------------------- #
_FMT_COLORS = [
    "#1f77b4", "#d62728", "#2ca02c", "#9467bd",
    "#ff7f0e", "#8c564b", "#e377c2", "#7f7f7f",
]


def _nice_ticks(vmin, vmax, n=5):
    if vmax <= vmin:
        vmax = vmin + 1.0
    span = vmax - vmin
    step0 = span / max(1, n)
    mag = 10 ** math.floor(math.log10(step0 + 1e-300))
    step = mag
    for m in (1.0, 2.0, 5.0, 10.0):
        if step0 / mag <= m + 1e-12:
            step = m * mag
            break
    start = math.ceil(vmin / step - 1e-9) * step
    end = math.floor(vmax / step + 1e-9) * step
    ticks = []
    v = start
    while v <= end + step * 1e-6:
        ticks.append(round(v, 10))
        v += step
    return ticks, step


def _fmt_num(v):
    if v is None:
        return ""
    if abs(v) < 1e-12:
        return "0"
    if abs(v) >= 1e6 or (abs(v) < 1e-3):
        return "%.3g" % v
    s = ("%.6f" % v).rstrip("0").rstrip(".")
    return s if s else "0"


def _esc(s):
    return escape(str(s) if s is not None else "")


def _qattr(s):
    return escape(str(s) if s is not None else "", {'"': "&quot;"})


def build_svg(spec):
    """根据 spec 渲染可编辑 SVG。返回 (svg_string, issues)。"""
    if not spec:
        return None, ["empty spec"]
    issues = []
    chart = spec.get("chart_type", "bar")
    W = float(spec.get("width", 840))
    H = float(spec.get("height", 560))
    ml, mr, mt, mb = 84, 30, 48, 66
    if spec.get("legend", True):
        mr = 150
    px, py = ml, mt
    pw = W - ml - mr
    ph = H - mt - mb

    cats = spec.get("categories") or ["c%d" % (i + 1) for i in range(1)]
    series = spec.get("series") or []
    if not series:
        return None, ["spec has no series"]
    n = len(cats)
    m = len(series)

    all_vals = [v for s in series for v in (s.get("values") or []) if v is not None]
    all_err = [e for s in series for e in (s.get("errors") or []) if e is not None]
    if not all_vals:
        return None, ["no numeric values to render"]
    ymax = max(all_vals) or 0
    if all_err:
        ymax = max(ymax, max(v + (e or 0) for s in series
                             for v, e in zip(s.get("values") or [], s.get("errors") or [])
                             if v is not None))
    ymin = min(0.0, min(all_vals) if all_vals else 0.0)
    if ymax <= ymin:
        ymax = ymin + 1.0
    ymax = ymax * 1.12 + (ymax - ymin) * 0.02

    def sx(x):
        return px + (float(x) / (n + 1)) * pw if n > 0 else px

    def sy(y):
        return py + ph - (float(y) - ymin) / (ymax - ymin) * ph

    ticks, step = _nice_ticks(ymin, ymax)
    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append(
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" font-family="%s">'
        % (int(W), int(H), int(W), int(H), CJK_FONT_STACK))
    parts.append('<rect x="0" y="0" width="%d" height="%d" fill="#ffffff"/>' % (int(W), int(H)))
    # 标题
    if spec.get("title"):
        parts.append('<text x="%d" y="30" text-anchor="middle" font-size="16" '
                     'font-weight="bold">%s</text>' % (int(W / 2), _esc(spec["title"])))
    # 绘图区边框
    parts.append('<g id="plot-area" data-xmin="%s" data-xmax="%s" data-ymin="%s" data-ymax="%s">'
                 % (_fmt_num(0), _fmt_num(n + 1), _fmt_num(ymin), _fmt_num(ymax)))
    parts.append('<rect x="%s" y="%s" width="%s" height="%s" fill="none" stroke="#333333" '
                 'stroke-width="1"/>' % (px, py, pw, ph))
    # y 刻度与网格
    for t in ticks:
        yy = sy(t)
        parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#cccccc" stroke-width="1"/>'
                     % (px, yy, px + pw, yy))
        parts.append('<text x="%s" y="%s" text-anchor="end" font-size="12" fill="#000000">%s</text>'
                     % (px - 8, yy + 4, _esc(_fmt_num(t))))
    # x 轴标签
    for i, c in enumerate(cats):
        cx = sx(i + 1)
        label = c if len(str(c)) <= 14 else str(c)[:13] + "…"
        parts.append('<text x="%s" y="%s" text-anchor="middle" font-size="12" fill="#000000" '
                     'transform="rotate(-30 %s %s)">%s</text>'
                     % (cx, py + ph + 18, cx, py + ph + 18, _esc(label)))
    # 轴标签
    if spec.get("y_label"):
        parts.append('<text x="18" y="%s" text-anchor="middle" font-size="13" '
                     'transform="rotate(-90 18 %s)">%s</text>'
                     % ((py + ph / 2), (py + ph / 2), _esc(spec["y_label"])))
    if spec.get("x_label"):
        parts.append('<text x="%s" y="%s" text-anchor="middle" font-size="13">%s</text>'
                     % (px + pw / 2, py + ph + 44, _esc(spec["x_label"])))

    bar_w = pw / (n * (m + 1)) if chart == "bar" else 0
    for si, s in enumerate(series):
        color = _FMT_COLORS[si % len(_FMT_COLORS)]
        sname = s.get("name", "series%d" % (si + 1))
        vals = s.get("values") or []
        errs = s.get("errors") or [None] * len(vals)
        if chart == "bar":
            for i, v in enumerate(vals):
                if v is None:
                    continue
                x0 = px + (i + 1) * pw / (n + 1) - bar_w * (m / 2.0) + si * bar_w
                y0 = sy(v)
                ybase = sy(0) if ymin <= 0 <= ymax else sy(ymin)
                sm = (s.get("source_map") or [{}])
                si_map = sm[i] if i < len(sm) else {}
                parts.append(
                    '<rect class="bar" data-series="%s" data-category="%s" data-source="%s" '
                    'data-row="%s" data-col="%s" data-value="%s" x="%s" y="%s" width="%s" '
                    'height="%s" fill="%s" stroke="#000000" stroke-width="0.5"/>'
                    % (_qattr(sname), _qattr(cats[i] if i < len(cats) else i),
                       _qattr(si_map.get("source", "")),
                       _qattr(si_map.get("row", "")), _qattr(si_map.get("col", "")),
                       _qattr(_fmt_num(v)), _qattr(round(x0, 2)), _qattr(round(y0, 2)),
                       _qattr(round(bar_w * 0.85, 2)), _qattr(round(abs(ybase - y0), 2)),
                       color))
            # 误差线
            for i, (v, e) in enumerate(zip(vals, errs)):
                if v is None or e is None:
                    continue
                cx = px + (i + 1) * pw / (n + 1) - bar_w * (m / 2.0) + (si + 0.5) * bar_w
                ytop = sy(v + e)
                ybot = sy(v - e)
                parts.append('<line class="errorbar" data-series="%s" data-error-type="%s" '
                             'x1="%s" y1="%s" x2="%s" y2="%s" stroke="#000000" stroke-width="1"/>'
                             % (_qattr(sname), _qattr(s.get("error_type", "")),
                                _qattr(round(cx, 2)), _qattr(round(ytop, 2)),
                                _qattr(round(cx, 2)), _qattr(round(ybot, 2))))
                parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#000000" stroke-width="1"/>'
                             % (cx - 3, ytop, cx + 3, ytop))
                parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#000000" stroke-width="1"/>'
                             % (cx - 3, ybot, cx + 3, ybot))
        elif chart in ("line", "scatter"):
            pts = []
            for i, v in enumerate(vals):
                if v is None:
                    continue
                cx = px + (i + 1) * pw / (n + 1)
                cy = sy(v)
                pts.append((cx, cy))
                sm = (s.get("source_map") or [{}])
                si_map = sm[i] if i < len(sm) else {}
                if chart == "scatter":
                    parts.append(
                        '<circle class="point" data-series="%s" data-category="%s" data-source="%s" '
                        'data-row="%s" data-col="%s" data-value="%s" cx="%s" cy="%s" r="4" '
                        'fill="%s" stroke="#000000" stroke-width="0.5"/>'
                        % (_qattr(sname), _qattr(cats[i] if i < len(cats) else i),
                           _qattr(si_map.get("source", "")), _qattr(si_map.get("row", "")),
                           _qattr(si_map.get("col", "")), _qattr(_fmt_num(v)),
                           _qattr(round(cx, 2)), _qattr(round(cy, 2)), color))
                if e is not None:
                    parts.append('<line class="errorbar" data-series="%s" data-error-type="%s" '
                                 'x1="%s" y1="%s" x2="%s" y2="%s" stroke="#000000" stroke-width="1"/>'
                                 % (_qattr(sname), _qattr(s.get("error_type", "")),
                                    _qattr(round(cx, 2)), _qattr(round(sy(v + e), 2)),
                                    _qattr(round(cx, 2)), _qattr(round(sy(v - e), 2))))
            if chart == "line" and pts:
                d = "M" + " L".join("%s %s" % (round(x, 2), round(y, 2)) for x, y in pts)
                parts.append('<path class="line" data-series="%s" d="%s" fill="none" '
                             'stroke="%s" stroke-width="2"/>' % (_qattr(sname), d, color))
                for cx, cy in pts:
                    parts.append('<circle cx="%s" cy="%s" r="3.5" fill="%s" stroke="#ffffff" '
                                 'stroke-width="1"/>' % (round(cx, 2), round(cy, 2), color))
        elif chart == "heatmap":
            values = s.get("values") or []
            vmax = max((v for v in values if v is not None), default=1.0)
            vmin = min((v for v in values if v is not None), default=0.0)
            for i, v in enumerate(values):
                if v is None:
                    continue
                t = 0.0 if vmax == vmin else (v - vmin) / (vmax - vmin)
                r = int(255 * (1 - t))
                g = int(255 * (1 - t * 0.6))
                b = 255
                parts.append(
                    '<rect class="cell" data-series="%s" data-category="%s" data-value="%s" '
                    'x="%s" y="%s" width="%s" height="%s" fill="#%02x%02x%02x" stroke="#ffffff"/>'
                    % (_qattr(sname), _qattr(cats[i] if i < len(cats) else i),
                       _qattr(_fmt_num(v)), _qattr(round(px + (i + 1) * pw / (n + 1) - 12, 2)),
                       _qattr(round(py, 2)), _qattr(round(24, 2)), _qattr(round(ph, 2)), r, g, b))
        # 图例
        if spec.get("legend", True):
            lx = px + pw + 20
            parts.append('<rect x="%s" y="%s" width="%s" height="%s" fill="#ffffff" '
                         'stroke="#999999" stroke-width="0.5"/>'
                         % (lx - 10, py, mr - 10, max(24, 20 * m + 8)))
            for si, s in enumerate(series):
                color = _FMT_COLORS[si % len(_FMT_COLORS)]
                parts.append('<rect x="%s" y="%s" width="12" height="12" fill="%s"/>'
                             % (lx, py + 8 + si * 20, color))
                parts.append('<text x="%s" y="%s" font-size="12">%s</text>'
                             % (lx + 18, py + 19 + si * 20, _esc(s.get("name", "series%d" % (si + 1)))))

    # 显著性标注
    for sig in spec.get("significance") or []:
        i, j = sig.get("i"), sig.get("j")
        if i is None or j is None:
            continue
        yb = sig.get("y", ymax * 0.95)
        x1 = sx(i + 1)
        x2 = sx(j + 1)
        yy = sy(yb)
        parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#000000" stroke-width="1"/>'
                     % (x1, yy, x2, yy))
        parts.append('<text x="%s" y="%s" text-anchor="middle" font-size="13" data-p="%s">%s</text>'
                     % ((x1 + x2) / 2, yy - 4, _qattr(sig.get("p", "")), _esc(sig.get("mark", "*"))))
    parts.append('</g>')
    parts.append('</svg>')
    svg = "\n".join(parts)
    return svg, issues


# --------------------------------------------------------------------------- #
# 产物校验
# --------------------------------------------------------------------------- #
def svg_has_text(svg_text):
    return "<text" in (svg_text or "")


def svg_parseable(svg_text):
    try:
        ET.fromstring(svg_text)
        return True
    except ET.ParseError:
        return False


def check_magic(path):
    """返回 (kind, ok, note)。kind ∈ {png,pdf,svg,office,unknown}。"""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
    except OSError as e:
        return "unknown", False, "read failed: %s" % e
    if not head:
        return "unknown", False, "empty file"
    if head.startswith(PNG_MAGIC):
        return "png", True, "PNG magic ok"
    if head.startswith(PDF_MAGIC):
        return "pdf", True, "PDF magic ok"
    if head.startswith(ZIP_MAGIC):
        return "office", True, "ZIP/Office magic ok"
    text = head.decode("latin-1", errors="replace").lstrip()
    if text.lower().startswith("<?xml") or text.lower().startswith("<svg"):
        try:
            with open(path, "rb") as f:
                body = f.read(10_000_000)
        except OSError as e:
            return "svg", False, "read failed: %s" % e
        try:
            ET.fromstring(body.decode("utf-8", errors="replace"))
            ok = b"<text" in body
            return "svg", ok, "SVG parseable; text editable" if ok else "SVG parseable but no <text>"
        except ET.ParseError as e:
            return "svg", False, "SVG parse failed: %s" % e
    return "unknown", False, "unrecognized magic"


# --------------------------------------------------------------------------- #
# CLI（被 main.py 调用，也可独立调试）
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="figtrace engine")
    ap.add_argument("--spec", help="figure spec JSON to render as SVG")
    ap.add_argument("--out", help="output SVG path")
    ap.add_argument("--table", help="parse a CSV/TSV/MD file")
    args = ap.parse_args()
    if args.table:
        text, errs = read_text(args.table)
        if errs:
            print(json.dumps({"ok": False, "errors": errs}, ensure_ascii=False))
            sys.exit(1)
        table, issues = parse_table(text, os.path.basename(args.table))
        if table is None:
            print(json.dumps({"ok": False, "errors": issues}, ensure_ascii=False))
            sys.exit(1)
        cols, cissues = infer_columns(table)
        print(json.dumps({"ok": True, "table": table, "columns": cols,
                          "issues": issues + cissues}, ensure_ascii=False, indent=2))
    elif args.spec and args.out:
        spec, errs = load_json_tolerant(args.spec)
        if errs:
            print(json.dumps({"ok": False, "errors": errs}, ensure_ascii=False))
            sys.exit(1)
        svg, issues = build_svg(spec)
        if svg is None:
            print(json.dumps({"ok": False, "errors": issues}, ensure_ascii=False))
            sys.exit(1)
        werrs = write_json(args.out, svg) if args.out.endswith(".json") else None
        if args.out.endswith(".svg"):
            try:
                with open(args.out, "w", encoding="utf-8", newline="\n") as f:
                    f.write(svg)
                werrs = []
            except OSError as e:
                werrs = ["write failed: %s" % e]
        print(json.dumps({"ok": werrs == [], "svg_bytes": len(svg), "issues": issues,
                          "errors": werrs}, ensure_ascii=False))
    else:
        ap.print_help()
