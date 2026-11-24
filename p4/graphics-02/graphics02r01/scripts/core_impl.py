#!/usr/bin/env python3
"""标准库实现：输入扫描、表格解析、证据链、SVG 手写、渲染器探测。

本模块只依赖 Python 标准库。渲染 PNG/PDF 时按需探测外部工具
（cairosvg / rsvg-convert / inkscape / chromium），找不到则如实返回 None，
调用方在 result.json 中标注 uncertainty，不得伪造导出件。

图元↔源数据的双向证据链：SVG 元素带 data-element / data-source /
data-locator 属性；evidence_map 条目携带 element_id / locator / 逐字 quote。
"""
from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

DEFAULT_FONT = (
    "DejaVu Sans, Arial, 'Noto Sans CJK SC', 'Source Han Sans SC', "
    "'WenQuanYi Zen Hei', 'Microsoft YaHei', sans-serif"
)
MAX_INPUT_FILE = 2 * 1024 * 1024  # 2MB，超限跳过并记录
CATEGORY_COLORS = [
    "#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]

# viridis 近似色标（用于 heatmap），t ∈ [0,1]
VIRIDIS = [
    (0.00, (68, 1, 84)),
    (0.25, (59, 82, 139)),
    (0.50, (33, 145, 140)),
    (0.75, (94, 201, 98)),
    (1.00, (253, 231, 37)),
]


def now_utc() -> str:
    """UTC 时间戳，change_log 使用。"""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_sha256(path: str, limit: int = MAX_INPUT_FILE) -> str | None:
    """材料版本指纹；超限返回 None。"""
    try:
        size = os.path.getsize(path)
        if size > limit:
            return None
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def load_json_safe(path: str):
    """读取 JSON，容忍 BOM 与常见坏编码；失败返回 (None, err)。"""
    if not os.path.exists(path):
        return None, "missing: %s" % path
    raw = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                raw = f.read()
            break
        except (UnicodeDecodeError, OSError):
            continue
    if raw is None:
        return None, "unreadable: %s" % path
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as e:
        return None, "bad json %s: %s" % (path, e)


def read_text_safe(path: str, limit: int = MAX_INPUT_FILE) -> tuple[str | None, str | None]:
    """读取文本；超限/编码失败返回 (None, err)。"""
    if not os.path.exists(path):
        return None, "missing: %s" % path
    try:
        if os.path.getsize(path) > limit:
            return None, "oversize(>%d bytes): %s" % (limit, path)
    except OSError as e:
        return None, "stat failed %s: %s" % (path, e)
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read(), None
        except UnicodeDecodeError:
            continue
        except OSError as e:
            return None, "read failed %s: %s" % (path, e)
    return None, "undecodable: %s" % path


def scan_inputs(input_dir: str) -> list[dict]:
    """扫描输入目录；单文件 ≤2MB，记录类型与版本指纹。"""
    out = []
    if not os.path.isdir(input_dir):
        return out
    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            p = os.path.join(root, name)
            rel = os.path.relpath(p, input_dir).replace(os.sep, "/")
            size = os.path.getsize(p) if os.path.exists(p) else 0
            ext = os.path.splitext(name)[1].lower()
            kind = {
                ".pdf": "pdf", ".csv": "table", ".tsv": "table", ".txt": "text",
                ".md": "text", ".json": "json", ".png": "image", ".jpg": "image",
                ".jpeg": "image", ".tif": "image", ".tiff": "image", ".svg": "svg",
                ".pptx": "office", ".ppt": "office", ".xlsx": "office", ".docx": "office",
            }.get(ext, "other")
            out.append({
                "path": rel,
                "abs": p,
                "kind": kind,
                "size": size,
                "sha256": file_sha256(p),
                "skipped": size > MAX_INPUT_FILE,
            })
    return out


def extract_pdf_text(path: str) -> tuple[str | None, str | None]:
    """调用 pdftotext 提取文本；不可用则返回错误，不臆造。"""
    exe = shutil.which("pdftotext")
    if not exe:
        return None, "pdftotext not found"
    try:
        r = subprocess.run([exe, "-layout", path, "-"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, "pdftotext failed: %s" % e
    if r.returncode != 0:
        return None, "pdftotext rc=%d: %s" % (r.returncode, r.stderr.strip()[:200])
    if not r.stdout.strip():
        return None, "pdftotext empty (可能是扫描件，需 ocr-tool)"
    return r.stdout, None


# ---------------------------------------------------------------------------
# 表格解析
# ---------------------------------------------------------------------------

def _to_float(s: str):
    t = s.strip().replace(",", "").replace(" ", "")
    if not t:
        return None
    # 百分数
    if t.endswith("%"):
        try:
            return float(t[:-1]) / 100.0, "percent"
        except ValueError:
            return None
    # 科学计数或普通数字（含正负、单位后缀不解析）
    m = re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", t)
    if m:
        try:
            return float(t), "number"
        except ValueError:
            return None
    return None


def infer_type(values: list[str]) -> str:
    """推断列类型：number / int / percent / date / string / mixed / empty。"""
    kinds = set()
    nonempty = 0
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if not s:
            continue
        nonempty += 1
        r = _to_float(s)
        if r is None:
            kinds.add("string")
        else:
            num, tag = r
            if tag == "percent":
                kinds.add("percent")
            elif float(num).is_integer():
                kinds.add("int")
            else:
                kinds.add("number")
    if nonempty == 0:
        return "empty"
    if "string" in kinds:
        return "mixed" if len(kinds) > 1 else "string"
    if len(kinds) > 1:
        if kinds == {"int", "number"}:
            return "number"
        if kinds == {"percent"}:
            return "percent"
        return "mixed"
    return next(iter(kinds))


def _split_table_rows(text: str) -> tuple[list[list[str]], str]:
    """识别 CSV/TSV/Markdown/空白分隔，返回 (rows, fmt)。rows 全部为字符串。"""
    text = text.strip("\ufeff").strip()
    if not text:
        return [], "empty"
    lines = [ln.rstrip("\n") for ln in text.splitlines() if ln.strip()]
    if not lines:
        return [], "empty"

    # Markdown 表格
    if any(re.match(r"^\s*\|.*\|\s*$", ln) for ln in lines):
        rows = []
        for ln in lines:
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(cells) == 1 and re.fullmatch(r"[-:\s]+", cells[0]):
                continue  # 分隔行
            rows.append(cells)
        return rows, "markdown"

    # CSV/TSV 嗅探
    sample = "\n".join(lines[:20])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
        rows = list(csv.reader(io.StringIO(text), dialect))
        return rows, "csv(%r)" % dialect.delimiter
    except csv.Error:
        pass

    # 空白分隔兜底
    rows = [re.split(r"\s{2,}|\t+", ln.strip()) for ln in lines]
    return rows, "whitespace"


def parse_table_text(text: str, name: str = "table") -> dict:
    """解析表格文本，返回列类型、缺失/量级 issue 与映射提示。"""
    rows, fmt = _split_table_rows(text)
    if not rows:
        return {"name": name, "format": fmt, "rows": [], "columns": [], "issues": [],
                "mapping_hints": [], "header": [], "n_data_rows": 0}
    header = rows[0]
    data = rows[1:]
    ncol = max(len(r) for r in rows) if rows else 0
    columns = []
    for c in range(ncol):
        vals = [(r[c] if c < len(r) else None) for r in data]
        coltype = infer_type(vals)
        missing = sum(1 for v in vals if v is None or str(v).strip() == "")
        columns.append({
            "name": header[c] if c < len(header) else "col_%d" % (c + 1),
            "type": coltype,
            "missing": missing,
            "n": len(vals),
        })
    issues = []
    mapping_hints = []
    for col in columns:
        if col["type"] == "empty":
            issues.append("列 %r 全空" % col["name"])
        if col["missing"]:
            issues.append("列 %r 缺 %d/%d 个值" % (col["name"], col["missing"], col["n"]))
        if col["name"] and len(str(col["name"])) > 24:
            issues.append("列名过长 %r" % col["name"])
    # 数值列量级异常检查：|值| > 1000 * 中位数
    for c in range(ncol):
        vals = []
        for r in data:
            if c < len(r):
                rr = _to_float(str(r[c]))
                if rr:
                    vals.append(abs(rr[0]))
        if len(vals) >= 3:
            med = sorted(vals)[len(vals) // 2]
            if med and any(v > 1000 * med for v in vals):
                issues.append("列 %r 存在量级异常（>1000×中位数），请核对单位" % (header[c] if c < len(header) else c))
    # 映射提示：找可能的 x 列（字符串/类别）与 y 列（数值）
    cat_cols = [c for c, col in enumerate(columns) if col["type"] in ("string", "mixed")]
    num_cols = [c for c, col in enumerate(columns) if col["type"] in ("number", "int", "percent")]
    if cat_cols and num_cols:
        mapping_hints.append("x 候选列：%s；数值系列列：%s" % (
            [columns[c]["name"] for c in cat_cols],
            [columns[c]["name"] for c in num_cols]))
    return {"name": name, "format": fmt, "rows": rows, "columns": columns,
            "issues": issues, "mapping_hints": mapping_hints, "header": header,
            "n_data_rows": len(data)}


def parse_table_file(path: str, name: str = "table") -> dict:
    text, err = read_text_safe(path)
    if text is None:
        return {"name": name, "error": err, "rows": [], "columns": [],
                "issues": [err], "mapping_hints": []}
    out = parse_table_text(text, name)
    out["source_file"] = path
    return out


# ---------------------------------------------------------------------------
# 证据链
# ---------------------------------------------------------------------------

def evidence_record(element_id, element_type, source_file, locator, quote,
                    value=None, unit=None, mapping="", confidence="exact",
                    conflicts=None, note=None):
    rec = {
        "element_id": element_id,
        "element_type": element_type,
        "source_file": source_file,
        "locator": locator,
        "quote": quote,
        "value": value,
        "unit": unit,
        "mapping": mapping,
        "confidence": confidence,
        "conflicts": conflicts or [],
    }
    if note:
        rec["note"] = note
    return rec


# ---------------------------------------------------------------------------
# SVG 工具
# ---------------------------------------------------------------------------

def svg_escape(s) -> str:
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def attr_escape(s) -> str:
    return svg_escape(s).replace('"', "&quot;")


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _colormap(t: float) -> str:
    t = max(0.0, min(1.0, t))
    anchors = VIRIDIS
    if t <= anchors[0][0]:
        r, g, b = anchors[0][1]
        return "#%02x%02x%02x" % (r, g, b)
    for i in range(len(anchors) - 1):
        t0, c0 = anchors[i]
        t1, c1 = anchors[i + 1]
        if t0 <= t <= t1:
            f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            r = int(round(c0[0] + (c1[0] - c0[0]) * f))
            g = int(round(c0[1] + (c1[1] - c0[1]) * f))
            b = int(round(c0[2] + (c1[2] - c0[2]) * f))
            return "#%02x%02x%02x" % (r, g, b)
    r, g, b = anchors[-1][1]
    return "#%02x%02x%02x" % (r, g, b)


def nice_ticks(vmin: float, vmax: float, n: int = 5) -> list[float]:
    """产生“整”刻度值。"""
    if vmin == vmax:
        return [vmin]
    span = vmax - vmin
    step = span / max(1, n - 1)
    mag = 10 ** math.floor(math.log10(abs(step)))
    norm = step / mag
    for s in (1.0, 2.0, 2.5, 5.0, 10.0):
        if norm <= s:
            step = s * mag
            break
    start = math.ceil(vmin / step) * step
    ticks = []
    v = start
    while v <= vmax + 1e-9:
        ticks.append(round(v, 10))
        v += step
    return ticks


def _fmt_tick(v: float) -> str:
    if v == 0:
        return "0"
    a = abs(v)
    if a >= 1e5 or a < 1e-3:
        return "%.1e" % v
    if float(v).is_integer():
        return str(int(v))
    return ("%.4g" % v)


# ---------------------------------------------------------------------------
# 图渲染（spec -> SVG）
# ---------------------------------------------------------------------------

def _series_evidence(series: dict, i: int, panel_id: str, kind: str):
    ev = series.get("evidence") or []
    if i < len(ev) and isinstance(ev[i], dict):
        return ev[i]
    default = series.get("evidence_default") or {}
    return dict(default)


def _ev_attr(ev: dict) -> str:
    src = attr_escape(ev.get("source_file", ""))
    loc = attr_escape(ev.get("locator", ""))
    return ' data-source="%s" data-locator="%s"' % (src, loc)


def _panel_plot_area(panel, x0, y0, w, h):
    ml = float(panel.get("margin_left", 84))
    mr = float(panel.get("margin_right", 26))
    mt = float(panel.get("margin_top", 42))
    mb = float(panel.get("margin_bottom", 58))
    return (x0 + ml, y0 + mt, max(10.0, w - ml - mr), max(10.0, h - mt - mb))


def _num_range(panels):
    """跨面板数值范围（用于统一 y 轴），忽略 log 面板。"""
    lo, hi = None, None
    for p in panels:
        for s in p.get("series", []):
            for v in s.get("values", []) or []:
                if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
                    lo = v if lo is None else min(lo, v)
                    hi = v if hi is None else max(hi, v)
                elif isinstance(v, (list, tuple)):
                    for w in v:
                        if isinstance(w, (int, float)):
                            lo = w if lo is None else min(lo, w)
                            hi = w if hi is None else max(hi, w)
            for e in s.get("errors", []) or []:
                if isinstance(e, (int, float)):
                    pass  # 误差只参与 ylim 扩展，不做全局
    return lo, hi


def render_spec(spec: dict) -> str:
    """把图 spec 渲染为 SVG 字符串。spec 由 Agent 依据解析结果编写。"""
    W = float(spec.get("width", 1000))
    H = float(spec.get("height", 700))
    bg = spec.get("background", "#ffffff")
    font = spec.get("font", DEFAULT_FONT)
    version = spec.get("version", "2.0.0")
    sources = spec.get("sources", [])
    title = spec.get("title", "")
    panels = spec.get("panels", [])

    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append(
        '<svg xmlns="http://www.w3.org/2000/svg" width="%g" height="%g" '
        'viewBox="0 0 %g %g" data-version="%s" data-sources="%s">'
        % (W, H, W, H, attr_escape(version), attr_escape(";".join(sources))))
    parts.append('<rect x="0" y="0" width="%g" height="%g" fill="%s"/>' % (W, H, bg))
    parts.append('<style>text{font-family:%s;}</style>' % attr_escape(font))
    if title:
        parts.append('<text x="%g" y="34" text-anchor="middle" font-size="18" '
                     'font-weight="bold">%s</text>' % (W / 2, svg_escape(title)))

    n = len(panels)
    if n == 0:
        parts.append('</svg>')
        return "\n".join(parts)

    # 面板布局：≤3 单行；否则 2 列网格
    if n <= 3:
        cols, rows = n, 1
    else:
        cols, rows = 2, math.ceil(n / 2)
    top_margin = 56.0 if title else 30.0
    bottom_margin = 24.0
    avail_w = W - 40.0
    avail_h = H - top_margin - bottom_margin
    cell_w = avail_w / cols
    cell_h = avail_h / rows
    for i, panel in enumerate(panels):
        r, c = divmod(i, cols)
        x0 = 20.0 + c * cell_w
        y0 = top_margin + r * cell_h
        _render_panel(panel, i, x0, y0, cell_w, cell_h, parts)
    parts.append('</svg>')
    return "\n".join(parts)


def _render_panel(panel, idx, x0, y0, w, h, parts):
    kind = panel.get("kind", "bar")
    pid = panel.get("id", chr(97 + idx) if idx < 26 else "p%d" % idx)
    title = panel.get("title", "")
    xlabel = panel.get("xlabel", "")
    ylabel = panel.get("ylabel", "")
    px, py, pw, ph = _panel_plot_area(panel, x0, y0, w, h)
    if title:
        parts.append('<text x="%g" y="%g" text-anchor="middle" font-size="13" '
                     'font-weight="bold" data-element="%s.title">%s</text>'
                     % (x0 + w / 2, y0 + 14, pid, svg_escape(title)))
    fn = {"bar": _render_bar, "line": _render_line, "scatter": _render_scatter,
          "box": _render_box, "heatmap": _render_heatmap}.get(kind)
    if fn:
        fn(panel, pid, px, py, pw, ph, parts)
    else:
        parts.append('<text x="%g" y="%g" font-size="12" fill="#c00">'
                     'unsupported kind: %s</text>' % (px, py + ph / 2, svg_escape(kind)))
    if xlabel:
        parts.append('<text x="%g" y="%g" text-anchor="middle" font-size="12" '
                     'data-element="%s.xlabel">%s</text>'
                     % (px + pw / 2, py + ph + 40, pid, svg_escape(xlabel)))
    if ylabel:
        parts.append('<text x="%g" y="%g" text-anchor="middle" font-size="12" '
                     'transform="rotate(-90 %g %g)" data-element="%s.ylabel">%s</text>'
                     % (x0 + 16, py + ph / 2, x0 + 16, py + ph / 2, pid, svg_escape(ylabel)))


def _y_map(v, vmin, vmax, py, ph, logy):
    if logy:
        if v <= 0:
            return py + ph  # 非正数压到底部
        v = math.log10(v)
        vmin = math.log10(vmin)
        vmax = math.log10(vmax)
    if vmax == vmin:
        return py + ph / 2
    return py + ph - (v - vmin) / (vmax - vmin) * ph


def _draw_axes(parts, pid, px, py, pw, ph, vmin, vmax, logy, xcenters, xlabels):
    parts.append('<g data-element="%s.axis" data-source="task.md" data-locator="spec">'
                 % pid)
    parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#333" stroke-width="1"/>'
                 % (px, py, px + pw, py))
    parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#333" stroke-width="1"/>'
                 % (px, py, px, py + ph))
    if logy and vmin > 0 and vmax > 0:
        ticks = [10 ** k for k in range(int(math.floor(math.log10(vmin))),
                                        int(math.ceil(math.log10(vmax))) + 1)]
    else:
        ticks = nice_ticks(vmin, vmax)
    for t in ticks:
        if (not logy and vmin <= t <= vmax) or (logy and vmin <= t <= vmax):
            y = _y_map(t, vmin, vmax, py, ph, logy)
            parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#bbb" '
                         'stroke-width="1"/>' % (px, y, px + pw, y))
            parts.append('<text x="%g" y="%g" text-anchor="end" font-size="11" '
                         'fill="#333">%s</text>' % (px - 6, y + 4, _fmt_tick(t)))
    for cx, lab in zip(xcenters, xlabels):
        parts.append('<text x="%g" y="%g" text-anchor="middle" font-size="11" '
                     'fill="#333">%s</text>' % (cx, py + ph + 18, svg_escape(lab)))
    parts.append('</g>')


def _x_centers(xlabels, px, pw, n=None):
    n = len(xlabels) if n is None else n
    step = pw / max(1, n)
    return [px + step * (i + 0.5) for i in range(n)]


def _vminmax(series_list, spec_ylim=None, logy=False):
    lo, hi = None, None
    for s in series_list:
        for v in s.get("values", []) or []:
            if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
                if not logy or v > 0:
                    lo = v if lo is None else min(lo, v)
                    hi = v if hi is None else max(hi, v)
            if isinstance(v, (list, tuple)):
                for w in v:
                    if isinstance(w, (int, float)) and (not logy or w > 0):
                        lo = w if lo is None else min(lo, w)
                        hi = w if hi is None else max(hi, w)
        for e in s.get("errors", []) or []:
            if isinstance(e, (int, float)):
                pass
    if lo is None:
        lo, hi = 0.0, 1.0
    if spec_ylim and spec_ylim[0] is not None:
        lo = float(spec_ylim[0])
    if spec_ylim and len(spec_ylim) > 1 and spec_ylim[1] is not None:
        hi = float(spec_ylim[1])
    if hi == lo:
        hi = lo + 1.0
    return lo, hi


def _series_color(s, i):
    return s.get("color") or CATEGORY_COLORS[i % len(CATEGORY_COLORS)]


def _render_bar(panel, pid, px, py, pw, ph, parts):
    series_list = panel.get("series", [])
    xlabels = panel.get("xlabels", [])
    logy = bool(panel.get("logy", False))
    vmin, vmax = _vminmax(series_list, panel.get("ylim"), logy)
    n = len(xlabels) or 1
    xcenters = _x_centers(xlabels, px, pw, n)
    _draw_axes(parts, pid, px, py, pw, ph, vmin, vmax, logy, xcenters, xlabels)
    ngroups = len(series_list)
    group_w = pw / max(1, n)
    bar_w = group_w / max(1, ngroups) * 0.7
    for si, s in enumerate(series_list):
        color = _series_color(s, si)
        vals = s.get("values", [])
        errors = s.get("errors", []) or []
        name = s.get("name", "series%d" % si)
        for i, v in enumerate(vals):
            if not isinstance(v, (int, float)):
                continue
            cx = xcenters[i] if i < len(xcenters) else px
            off = (si - (ngroups - 1) / 2.0) * bar_w
            x = cx + off - bar_w / 2
            y = _y_map(v, vmin, vmax, py, ph, logy)
            base_y = _y_map(0 if not logy else (vmin if vmin > 0 else 1e-9), vmin, vmax, py, ph, logy)
            if logy:
                base_y = py + ph
            ev = _series_evidence(s, i, pid, "bar")
            eid = "%s.bar.%d.%d" % (pid, si, i)
            parts.append('<rect id="%s" data-element="%s" x="%g" y="%g" width="%g" '
                         'height="%g" fill="%s"%s/>'
                         % (eid, eid, x, min(y, base_y), bar_w, abs(base_y - y), color,
                            _ev_attr(ev)))
            if i < len(errors) and isinstance(errors[i], (int, float)):
                e = errors[i]
                yhi = _y_map(v + e, vmin, vmax, py, ph, logy)
                ylo = _y_map(v - e, vmin, vmax, py, ph, logy)
                mx = cx + off
                parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#333" '
                             'stroke-width="1" data-element="%s.errorbar.%d.%d" '
                             'data-source="%s" data-locator="%s"/>'
                             % (mx, yhi, mx, ylo, pid, si, i,
                                attr_escape(ev.get("source_file", "")),
                                attr_escape(ev.get("locator", ""))))
        # 图例
        lx = px + pw - 8
        ly = py + 12 + si * 18
        parts.append('<rect x="%g" y="%g" width="12" height="12" fill="%s"/>'
                     % (lx - 16, ly - 10, color))
        parts.append('<text x="%g" y="%g" font-size="11" fill="#333">%s</text>'
                     % (lx, ly, svg_escape(name)))


def _render_line(panel, pid, px, py, pw, ph, parts):
    series_list = panel.get("series", [])
    xlabels = panel.get("xlabels", [])
    logy = bool(panel.get("logy", False))
    vmin, vmax = _vminmax(series_list, panel.get("ylim"), logy)
    n = len(xlabels) or 1
    xcenters = _x_centers(xlabels, px, pw, n)
    _draw_axes(parts, pid, px, py, pw, ph, vmin, vmax, logy, xcenters, xlabels)
    for si, s in enumerate(series_list):
        color = _series_color(s, si)
        vals = s.get("values", [])
        pts = []
        for i, v in enumerate(vals):
            if isinstance(v, (int, float)) and i < len(xcenters):
                pts.append((xcenters[i], _y_map(v, vmin, vmax, py, ph, logy)))
        if len(pts) >= 2:
            d = "M" + " L".join("%g,%g" % p for p in pts)
            parts.append('<path d="%s" fill="none" stroke="%s" stroke-width="2" '
                         'data-element="%s.line.%d"/>' % (d, color, pid, si))
        for i, (cx, cy) in enumerate(pts):
            ev = _series_evidence(s, i, pid, "point")
            eid = "%s.point.%d.%d" % (pid, si, i)
            parts.append('<circle id="%s" data-element="%s" cx="%g" cy="%g" r="3.5" '
                         'fill="%s"%s/>' % (eid, eid, cx, cy, color, _ev_attr(ev)))
        name = s.get("name", "series%d" % si)
        lx = px + pw - 8
        ly = py + 12 + si * 18
        parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="%s" '
                     'stroke-width="2"/>' % (lx - 22, ly - 8, lx - 10, ly - 8, color))
        parts.append('<text x="%g" y="%g" font-size="11" fill="#333">%s</text>'
                     % (lx, ly, svg_escape(name)))


def _render_scatter(panel, pid, px, py, pw, ph, parts):
    series_list = panel.get("series", [])
    xlabel = panel.get("xlabel", "")
    ylabel = panel.get("ylabel", "")
    logy = bool(panel.get("logy", False))
    # x 数值范围
    xlo, xhi = None, None
    ylo, yhi = None, None
    for s in series_list:
        for p in s.get("points", []) or []:
            x, y = p[0], p[1]
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                xlo = x if xlo is None else min(xlo, x)
                xhi = x if xhi is None else max(xhi, x)
                ylo = y if ylo is None else min(ylo, y)
                yhi = y if yhi is None else max(yhi, y)
    xlo = xlo if xlo is not None else 0.0
    xhi = xhi if xhi is not None else 1.0
    ylo = ylo if ylo is not None else 0.0
    yhi = yhi if yhi is not None else 1.0
    if panel.get("ylim") and panel["ylim"][0] is not None:
        ylo = float(panel["ylim"][0])
    if panel.get("ylim") and len(panel["ylim"]) > 1 and panel["ylim"][1] is not None:
        yhi = float(panel["ylim"][1])
    xspan = xhi - xlo or 1.0
    yspan = yhi - ylo or 1.0
    # 简单坐标轴（数值）
    parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#333" stroke-width="1"/>'
                 % (px, py + ph, px + pw, py + ph))
    parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#333" stroke-width="1"/>'
                 % (px, py, px, py + ph))
    for t in nice_ticks(xlo, xhi):
        cx = px + (t - xlo) / xspan * pw
        parts.append('<text x="%g" y="%g" text-anchor="middle" font-size="11">%s</text>'
                     % (cx, py + ph + 16, _fmt_tick(t)))
    for t in nice_ticks(ylo, yhi):
        cy = py + ph - (t - ylo) / yspan * ph
        parts.append('<text x="%g" y="%g" text-anchor="end" font-size="11">%s</text>'
                     % (px - 6, cy + 4, _fmt_tick(t)))
    for si, s in enumerate(series_list):
        color = _series_color(s, si)
        for i, p in enumerate(s.get("points", []) or []):
            x, y = p[0], p[1]
            if not (isinstance(x, (int, float)) and isinstance(y, (int, float))):
                continue
            cx = px + (x - xlo) / xspan * pw
            cy = py + ph - (y - ylo) / yspan * ph
            ev = _series_evidence(s, i, pid, "point")
            eid = "%s.point.%d.%d" % (pid, si, i)
            parts.append('<circle id="%s" data-element="%s" cx="%g" cy="%g" r="4" '
                         'fill="%s"%s/>' % (eid, eid, cx, cy, color, _ev_attr(ev)))
    parts.append('<text x="%g" y="%g" text-anchor="middle" font-size="12">%s</text>'
                 % (px + pw / 2, py + ph + 38, svg_escape(xlabel)))
    parts.append('<text x="%g" y="%g" text-anchor="middle" font-size="12" '
                 'transform="rotate(-90 %g %g)">%s</text>'
                 % (px - 20, py + ph / 2, px - 20, py + ph / 2, svg_escape(ylabel)))


def _render_box(panel, pid, px, py, pw, ph, parts):
    series_list = panel.get("series", [])
    xlabels = panel.get("xlabels", [])
    logy = bool(panel.get("logy", False))
    vmin, vmax = _vminmax(series_list, panel.get("ylim"), logy)
    n = len(xlabels) or 1
    xcenters = _x_centers(xlabels, px, pw, n)
    _draw_axes(parts, pid, px, py, pw, ph, vmin, vmax, logy, xcenters, xlabels)
    ngroups = len(series_list)
    group_w = pw / max(1, n)
    box_w = group_w / max(1, ngroups) * 0.5
    for si, s in enumerate(series_list):
        color = _series_color(s, si)
        off = (si - (ngroups - 1) / 2.0) * box_w
        for i, v in enumerate(s.get("values", []) or []):
            if not isinstance(v, (list, tuple)) or len(v) < 5:
                continue
            lo, q1, med, q3, hi = [float(x) for x in v[:5]]
            cx = xcenters[i] + off
            ylo = _y_map(lo, vmin, vmax, py, ph, logy)
            yq1 = _y_map(q1, vmin, vmax, py, ph, logy)
            ymed = _y_map(med, vmin, vmax, py, ph, logy)
            yq3 = _y_map(q3, vmin, vmax, py, ph, logy)
            yhi = _y_map(hi, vmin, vmax, py, ph, logy)
            ev = _series_evidence(s, i, pid, "box")
            eid = "%s.box.%d.%d" % (pid, si, i)
            parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#333" '
                         'stroke-width="1"/>' % (cx, ylo, cx, yq1))
            parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#333" '
                         'stroke-width="1"/>' % (cx, yq3, cx, yhi))
            parts.append('<rect x="%g" y="%g" width="%g" height="%g" fill="%s" '
                         'opacity="0.5" stroke="%s"%s/>'
                         % (cx - box_w / 2, yq3, box_w, max(1.0, yq1 - yq3), color, color,
                            _ev_attr(ev)))
            parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="%s" '
                         'stroke-width="2" data-element="%s.med.%d.%d"/>'
                         % (cx - box_w / 2, ymed, cx + box_w / 2, ymed, color, pid, si, i))


def _render_heatmap(panel, pid, px, py, pw, ph, parts):
    matrix = panel.get("matrix", [])
    xlabels = panel.get("xlabels", [])
    ylabels = panel.get("ylabels", [])
    nrows = len(matrix)
    ncols = len(matrix[0]) if nrows else 0
    if not nrows or not ncols:
        parts.append('<text x="%g" y="%g" font-size="12" fill="#c00">empty heatmap</text>'
                     % (px, py + ph / 2))
        return
    vals = [v for row in matrix for v in row if isinstance(v, (int, float))]
    vmin = min(vals) if vals else 0.0
    vmax = max(vals) if vals else 1.0
    cell_w = pw / ncols
    cell_h = ph / nrows
    for r in range(nrows):
        for c in range(ncols):
            v = matrix[r][c]
            if not isinstance(v, (int, float)):
                continue
            t = (v - vmin) / (vmax - vmin) if vmax > vmin else 0.5
            color = _colormap(t)
            ev = panel.get("evidence", {}).get("%d,%d" % (r, c), {})
            eid = "%s.heat.%d.%d" % (pid, r, c)
            parts.append('<rect id="%s" data-element="%s" x="%g" y="%g" width="%g" '
                         'height="%g" fill="%s"%s>'
                         '<title>%s = %s</title></rect>'
                         % (eid, eid, px + c * cell_w, py + r * cell_h, cell_w, cell_h,
                            color, _ev_attr(ev), svg_escape(xlabels[c] if c < len(xlabels) else c),
                            svg_escape(_fmt_tick(v))))
    for c, lab in enumerate(xlabels):
        parts.append('<text x="%g" y="%g" text-anchor="middle" font-size="10">%s</text>'
                     % (px + (c + 0.5) * cell_w, py + ph + 14, svg_escape(lab)))
    for r, lab in enumerate(ylabels):
        parts.append('<text x="%g" y="%g" text-anchor="end" font-size="10">%s</text>'
                     % (px - 6, py + (r + 0.5) * cell_h + 4, svg_escape(lab)))


def write_svg(path: str, svg_string: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg_string)


# ---------------------------------------------------------------------------
# 渲染器探测与 PNG/PDF 导出
# ---------------------------------------------------------------------------

def _has_py_module(mod: str) -> bool:
    try:
        r = subprocess.run([sys.executable, "-c", "import %s" % mod],
                           capture_output=True, timeout=60)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def detect_renderers() -> dict:
    """按可用性探测 SVG→PNG/PDF 渲染器。"""
    found = []
    if _has_py_module("cairosvg"):
        found.append("cairosvg")
    for exe in ("rsvg-convert", "inkscape", "chromium", "chromium-browser",
                "google-chrome", "chrome", "msedge"):
        if shutil.which(exe):
            found.append(exe)
    return {"renderers": found, "primary": found[0] if found else None}


def render_svg(svg_path: str, out_dir: str, name: str, dpi: int = 300,
               width: float | None = None, height: float | None = None) -> dict:
    """尝试把 SVG 渲染为 PNG/PDF；失败返回 error，不伪造。"""
    os.makedirs(out_dir, exist_ok=True)
    png_path = os.path.join(out_dir, name + ".png")
    pdf_path = os.path.join(out_dir, name + ".pdf")
    det = detect_renderers()
    renderer = det["primary"]
    if not renderer:
        return {"png": None, "pdf": None, "renderer": None,
                "error": "no SVG rasterizer available (cairosvg/rsvg/inkscape/chromium)"}
    png_ok = pdf_ok = False
    errors = []
    try:
        if renderer == "cairosvg":
            code = (
                "import cairosvg,sys\n"
                "cairosvg.svg2png(url=sys.argv[1], write_to=sys.argv[2], dpi=%d)\n"
                "cairosvg.svg2pdf(url=sys.argv[1], write_to=sys.argv[3])\n" % dpi)
            r = subprocess.run([sys.executable, "-c", code, svg_path, png_path, pdf_path],
                               capture_output=True, timeout=180)
            if r.returncode == 0:
                png_ok = os.path.exists(png_path)
                pdf_ok = os.path.exists(pdf_path)
            else:
                errors.append("cairosvg rc=%d %s" % (r.returncode, r.stderr.decode(errors="replace")[:160]))
        elif renderer == "rsvg-convert":
            for args, out in ((["-f", "png", "-d", str(dpi), svg_path, "-o", png_path], png_path),
                              (["-f", "pdf", svg_path, "-o", pdf_path], pdf_path)):
                r = subprocess.run([renderer] + args, capture_output=True, timeout=180)
                if r.returncode == 0 and os.path.exists(out):
                    if out.endswith(".png"):
                        png_ok = True
                    else:
                        pdf_ok = True
                else:
                    errors.append("%s rc=%d" % (renderer, r.returncode))
        elif renderer == "inkscape":
            r = subprocess.run([renderer, svg_path, "--export-type=png", "--export-filename=%s" % png_path,
                                "--export-dpi=%d" % dpi], capture_output=True, timeout=240)
            if r.returncode == 0 and os.path.exists(png_path):
                png_ok = True
            else:
                errors.append("inkscape png rc=%d" % r.returncode)
            r = subprocess.run([renderer, svg_path, "--export-type=pdf", "--export-filename=%s" % pdf_path],
                               capture_output=True, timeout=240)
            if r.returncode == 0 and os.path.exists(pdf_path):
                pdf_ok = True
            else:
                errors.append("inkscape pdf rc=%d" % r.returncode)
        else:  # chromium 系
            import tempfile
            # 截图
            size_arg = "--window-size=%d,%d" % (int(width or 1000), int(height or 700))
            r = subprocess.run([renderer, "--headless=new", "--disable-gpu", "--no-sandbox",
                                size_arg, "--force-device-scale-factor=%g" % (dpi / 96.0),
                                "--screenshot=%s" % png_path, "file://" + os.path.abspath(svg_path)],
                               capture_output=True, timeout=240)
            if r.returncode == 0 and os.path.exists(png_path):
                png_ok = True
            else:
                errors.append("chromium screenshot rc=%d" % r.returncode)
            r = subprocess.run([renderer, "--headless=new", "--disable-gpu", "--no-sandbox",
                                "--print-to-pdf=%s" % pdf_path, "--no-pdf-header-footer",
                                "file://" + os.path.abspath(svg_path)],
                               capture_output=True, timeout=240)
            if r.returncode == 0 and os.path.exists(pdf_path):
                pdf_ok = True
            else:
                errors.append("chromium pdf rc=%d" % r.returncode)
    except (OSError, subprocess.TimeoutExpired) as e:
        errors.append("render exception: %s" % e)
    return {"png": os.path.relpath(png_path, out_dir) if png_ok else None,
            "pdf": os.path.relpath(pdf_path, out_dir) if pdf_ok else None,
            "renderer": renderer if (png_ok or pdf_ok) else None,
            "error": "; ".join(errors) if errors else None}
