#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — 科研示意图 Skill 的纯标准库实现。

被 scripts/main.py 与 scripts/selftest.py 复用。职责：

1. 输入发现与 PDF 文本抽取（生成带 locator 的逐页文本索引）。
2. figure_spec.json -> 分层可编辑 SVG（`<g>` 图层、可编辑 `<text>`、可替换字体，
   且每个图元带 data-evidence-id / data-source-ref 溯源属性）。
3. SVG -> PNG / PDF 导出（按可用性依次尝试预装外部渲染器）。
4. result.json 契约与产物自检（SVG 可解析、PNG/PDF 头与尺寸、字段完整性）。
5. evidence_map（结论级 locator + 逐字 quote）与 provenance 的合并与回填。

约定：本文件只用 Python 标准库；外部工具通过 subprocess 调用并在失败时降级，
不伪造缺失的软件状态或科学数据。
"""
import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

UTF8_SIG = "utf-8-sig"
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB，超限跳过并记录


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_json(path, default=None):
    """读取 JSON，容错 BOM / 坏编码 / 坏 JSON；失败返回 default 不抛异常。"""
    try:
        with open(path, "r", encoding=UTF8_SIG, errors="replace") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def read_text(path):
    try:
        with open(path, "r", encoding=UTF8_SIG, errors="replace") as f:
            return f.read()
    except OSError:
        return None


def which_tool(*names):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def run_cmd(cmd, timeout=120):
    """运行外部命令；异常/非零返回都作为失败记录，不崩溃。"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return r.returncode == 0, (r.stdout or "")[:2000], (r.stderr or "")[:2000]
    except (OSError, subprocess.SubprocessError):
        return False, "", "exec failed"


# ---------------------------------------------------------------------------
# 输入发现与 PDF 文本抽取
# ---------------------------------------------------------------------------

MATERIAL_EXTS = (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".svg",
                 ".csv", ".tsv", ".xlsx", ".xls", ".docx", ".pptx", ".md", ".txt")

SKIP_NAMES = ("case.json", "task.md", "result.json")


def discover_inputs(input_dir):
    """扫描输入目录，返回 (case_path, task_path, materials, warnings)。"""
    case_path = os.path.join(input_dir, "case.json")
    task_path = os.path.join(input_dir, "task.md")
    materials, warnings = [], []
    for root, _dirs, files in os.walk(input_dir):
        for fn in sorted(files):
            if fn in SKIP_NAMES:
                continue
            full = os.path.join(root, fn)
            low = fn.lower()
            if low.endswith(MATERIAL_EXTS):
                try:
                    if os.path.getsize(full) > MAX_FILE_BYTES:
                        warnings.append({"file": full, "reason": "size > 2MB, skipped"})
                        continue
                    materials.append(full)
                except OSError:
                    continue
    return case_path, task_path, materials, warnings


def extract_pdf_text(pdf_path, work_dir, pdftotext="pdftotext"):
    """pdftotext 抽取；输出逐页文本并返回 locator 索引。

    返回 dict：{page: {"text": str, "chars": int, "path": 相对路径}}
    无文本层（所有页 chars==0）时返回空 dict，调用方应改用 OCR 并标注。
    """
    tool = shutil.which(pdftotext)
    if not tool:
        return None, {"reason": "pdftotext not found"}
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    out_prefix = os.path.join(work_dir, base)
    ok, so, se = run_cmd([tool, "-layout", "-enc", "UTF-8",
                          pdf_path, out_prefix + ".txt"])
    txt_path = out_prefix + ".txt"
    index = {}
    if ok and os.path.exists(txt_path):
        raw = read_text(txt_path) or ""
        pages = re.split(r"\f", raw)
        for i, chunk in enumerate(pages, start=1):
            text = chunk.strip()
            index[i] = {"text": text, "chars": len(text),
                        "path": os.path.relpath(txt_path, work_dir)}
        has_text = any(v["chars"] > 0 for v in index.values())
        if not has_text:
            index = {}
    return index, {"stderr": se[:500]} if not ok else {"ok": True}


def scan_cases_and_tasks(case_path, task_path):
    """读取 case.json 与 task.md，返回可安全序列化的摘要。"""
    case = load_json(case_path, {}) or {}
    case_id = case.get("case_id") or case.get("id") or case.get("caseId") or "unknown"
    task_text = read_text(task_path) or ""
    # 材料路径：尊重 case.json 显式声明，否则留给 discover_inputs
    declared = []
    for key in ("materials", "material_files", "files", "sources"):
        v = case.get(key)
        if isinstance(v, list):
            for it in v:
                if isinstance(it, str):
                    declared.append(it)
                elif isinstance(it, dict):
                    declared.append(it.get("path") or it.get("file") or "")
    declared = [d for d in declared if d]
    return {
        "case_id": case_id,
        "case_keys": sorted(case.keys()),
        "task_chars": len(task_text),
        "declared_materials": declared,
        "task_excerpt": task_text[:500],
    }


# ---------------------------------------------------------------------------
# figure_spec.json -> SVG（分层、可编辑、可溯源）
# ---------------------------------------------------------------------------

DEFAULT_FONT = "DejaVu Sans, Arial, sans-serif"

# 关系类型 -> 端点样式
ARROW_TYPES = {"activates", "upstream", "causal", "temporal", "maps_to",
               "leads_to", "produces", "drives"}
BAR_TYPES = {"inhibits", "suppresses", "blocks"}
LINE_TYPES = {"parallel", "co_member", "associates", "binds"}


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _entity_box(e):
    x = float(e.get("x", 0)); y = float(e.get("y", 0))
    w = float(e.get("w", 140)); h = float(e.get("h", 44))
    return x, y, w, h


def _center(e):
    x, y, w, h = _entity_box(e)
    return x + w / 2.0, y + h / 2.0


def _attrs(**kw):
    parts = []
    for k, v in kw.items():
        if v is None or v == "":
            continue
        parts.append(' %s="%s"' % (k, _esc(v)))
    return "".join(parts)


def _svg_id_map(spec):
    """建立 entity id -> box 映射，供关系连线使用。"""
    m = {}
    for e in spec.get("entities", []):
        m[e.get("id")] = _entity_box(e)
    return m


def _defs(spec):
    stroke = spec.get("edge_color", "#333333")
    fill = spec.get("edge_color", "#333333")
    return (
        '<defs>\n'
        '  <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">\n'
        '    <path d="M0,0 L10,5 L0,10 z" fill="%s"/>\n'
        '  </marker>\n'
        '</defs>\n' % (_esc(fill),)
    )


def _render_relation(rel, boxes, entities_by_id, font=DEFAULT_FONT):
    a, b = rel.get("from"), rel.get("to")
    if a not in boxes or b not in boxes:
        return ""
    x1, y1 = _center(entities_by_id[a])
    x2, y2 = _center(entities_by_id[b])
    rtype = rel.get("type", "associates")
    stroke = rel.get("color", "#333333")
    w = float(rel.get("width", 1.4))
    dashed = " stroke-dasharray=\"5,4\"" if rel.get("dashed") else ""
    lbl = rel.get("label") or ""
    attrs = _attrs(**{"data-relation": rel.get("id", ""),
                      "data-evidence-id": rel.get("evidence_id", ""),
                      "data-source-ref": rel.get("source_ref", "")})
    mid = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
    parts = []
    if rtype in LINE_TYPES:
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                     'stroke="%s" stroke-width="%.2f"%s%s/>'
                     % (x1, y1, x2, y2, _esc(stroke), w, dashed, attrs))
    elif rtype in BAR_TYPES:
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                     'stroke="%s" stroke-width="%.2f"%s%s/>'
                     % (x1, y1, x2, y2, _esc(stroke), w, dashed, attrs))
        # 抑制端垂直短杠
        dx, dy = x2 - x1, y2 - y1
        length = (dx * dx + dy * dy) ** 0.5 or 1.0
        ux, uy = dx / length, dy / length
        bar = 8.0
        px, py = -uy * bar, ux * bar
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                     'stroke="%s" stroke-width="%.2f"/>'
                     % (x2 - px, y2 - py, x2 + px, y2 + py, _esc(stroke), w))
    else:  # 箭头类
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                     'stroke="%s" stroke-width="%.2f" marker-end="url(#arrow)"%s%s/>'
                     % (x1, y1, x2, y2, _esc(stroke), w, dashed, attrs))
    if lbl:
        parts.append('<text x="%.1f" y="%.1f" font-family="%s" font-size="11" '
                     'fill="%s" text-anchor="middle">%s</text>'
                     % (mid[0], mid[1] - 6, _esc(font),
                        _esc(stroke), _esc(lbl)))
    return "\n".join(parts)


def _entity_shape(e, font):
    x, y, w, h = _entity_box(e)
    kind = e.get("kind", "node")
    shape = e.get("shape", "rect")
    fill = e.get("fill", "#ffffff")
    stroke = e.get("stroke", "#333333")
    sw = float(e.get("stroke_width", 1.2))
    lw = float(e.get("font_size", 12))
    lbl = e.get("label") or e.get("id") or ""
    attrs = _attrs(**{"data-entity": e.get("id", ""),
                      "data-kind": kind,
                      "data-evidence-id": e.get("evidence_id", ""),
                      "data-source-ref": e.get("source_ref", "")})
    geo = ""
    if shape == "ellipse":
        rx, ry = w / 2.0, h / 2.0
        geo = ('<ellipse cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" '
               'fill="%s" stroke="%s" stroke-width="%.2f"%s/>'
               % (x + rx, y + ry, rx, ry, _esc(fill), _esc(stroke), sw, attrs))
    elif shape == "roundrect":
        r = min(w, h, 40.0) * 0.18
        geo = ('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="%.1f" '
               'fill="%s" stroke="%s" stroke-width="%.2f"%s/>'
               % (x, y, w, h, r, _esc(fill), _esc(stroke), sw, attrs))
    elif shape == "hexagon":
        pts = [(x + w * 0.2, y), (x + w * 0.8, y), (x + w, y + h / 2),
               (x + w * 0.8, y + h), (x + w * 0.2, y + h), (x, y + h / 2)]
        p = " ".join("%.1f,%.1f" % (px, py) for px, py in pts)
        geo = ('<polygon points="%s" fill="%s" stroke="%s" stroke-width="%.2f"%s/>'
               % (p, _esc(fill), _esc(stroke), sw, attrs))
    else:  # rect
        geo = ('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
               'fill="%s" stroke="%s" stroke-width="%.2f"%s/>'
               % (x, y, w, h, _esc(fill), _esc(stroke), sw, attrs))
    txt = ('<text x="%.1f" y="%.1f" font-family="%s" font-size="%.1f" '
           'fill="#111111" text-anchor="middle" dominant-baseline="middle">%s</text>'
           % (x + w / 2.0, y + h / 2.0, _esc(font), lw, _esc(lbl)))
    return geo + "\n" + txt


def _render_legend(legend, x, y, w, font, spec):
    title = legend.get("title", "Legend")
    items = legend.get("items", [])
    box_h = 30 + 22 * max(len(items), 1)
    parts = ['<g id="legend-box" data-evidence-id="%s" data-source-ref="%s">'
             % (_esc(legend.get("evidence_id", "")),
                _esc(legend.get("source_ref", "")))]
    parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                 'fill="#ffffff" stroke="#999999" stroke-width="1"/>'
                 % (x, y, w, box_h))
    parts.append('<text x="%.1f" y="%.1f" font-family="%s" font-size="13" '
                 'font-weight="bold" fill="#111111">%s</text>'
                 % (x + 10, y + 18, _esc(font), _esc(title)))
    for i, it in enumerate(items):
        iy = y + 36 + i * 22
        color = it.get("color", "#888888")
        marker = it.get("marker", "rect")
        label = it.get("label") or ""
        if marker == "circle":
            sym = '<circle cx="%.1f" cy="%.1f" r="6" fill="%s" stroke="#333333"/>' \
                % (x + 16, iy - 4, _esc(color))
        else:
            sym = '<rect x="%.1f" y="%.1f" width="12" height="12" fill="%s" ' \
                  'stroke="#333333"/>' % (x + 10, iy - 10, _esc(color))
        parts.append(sym)
        parts.append('<text x="%.1f" y="%.1f" font-family="%s" font-size="12" '
                     'fill="#111111">%s</text>'
                     % (x + 30, iy, _esc(font), _esc(label)))
    parts.append('</g>')
    return "\n".join(parts)


def _nice_ticks(vmin, vmax, n=5):
    import math
    if vmax <= vmin:
        vmax = vmin + 1.0
    span = vmax - vmin
    raw_step = span / float(n)
    mag = 10 ** int(math.floor(math.log10(abs(raw_step) + 1e-300)))
    norm = raw_step / mag
    if norm <= 1:
        step = 1 * mag
    elif norm <= 2:
        step = 2 * mag
    elif norm <= 2.5:
        step = 2.5 * mag
    elif norm <= 5:
        step = 5 * mag
    else:
        step = 10 * mag
    ticks = []
    v = math.floor(vmin / step) * step
    while v <= vmax + step * 1e-6:
        if vmin - step * 1e-6 <= v <= vmax + step * 1e-6:
            ticks.append(v)
        v += step
    return ticks


def _fmt_tick(v):
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return ("%.2f" % v).rstrip("0").rstrip(".")


def _render_chart(chart, font):
    x, y = float(chart.get("x", 0)), float(chart.get("y", 0))
    w, h = float(chart.get("w", 500)), float(chart.get("h", 320))
    ml, mr, mt, mb = 62.0, 18.0, 28.0, 54.0
    plot_w, plot_h = w - ml - mr, h - mt - mb
    series = chart.get("series", [])
    attrs = _attrs(**{"data-chart": chart.get("id", ""),
                      "data-evidence-id": chart.get("evidence_id", ""),
                      "data-source-ref": chart.get("source_ref", "")})
    parts = ['<g id="chart-%s"%s>' % (_esc(chart.get("id", "c")), attrs)]
    # 标题
    if chart.get("title"):
        parts.append('<text x="%.1f" y="%.1f" font-family="%s" font-size="13" '
                     'font-weight="bold" fill="#111111">%s</text>'
                     % (x + ml, y + mt - 12, _esc(font), _esc(chart["title"])))
    all_y = [v for s in series for _cat, v in s.get("data", [])]
    ymin, ymax = min(all_y + [0.0]), max(all_y + [0.0])
    ticks = _nice_ticks(ymin, ymax)
    # 轴线与网格
    y_scale = plot_h / (ymax - ymin) if ymax != ymin else 1.0
    for t in ticks:
        ty = y + mt + plot_h - (t - ymin) * y_scale
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                     'stroke="#dddddd" stroke-width="1"/>'
                     % (x + ml, ty, x + ml + plot_w, ty))
        parts.append('<text x="%.1f" y="%.1f" font-family="%s" font-size="10" '
                     'fill="#555555" text-anchor="end">%s</text>'
                     % (x + ml - 6, ty + 3, _esc(font), _fmt_tick(t)))
    # 类别轴
    ncat = max((len(s.get("data", [])) for s in series), default=1)
    cat_labels = [str(v[0]) for v in series[0].get("data", [])] if series else []
    nseries = max(len(series), 1)
    slot = plot_w / ncat
    bar_w = slot / (nseries + 1.0)
    for j, s in enumerate(series):
        color = s.get("color", "#4c72b0")
        for i, (cat, val) in enumerate(s.get("data", [])):
            bx = x + ml + i * slot + (j + 1) * bar_w * 0.8
            by = y + mt + plot_h - (val - ymin) * y_scale
            bh = (val - ymin) * y_scale
            parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                         'fill="%s" stroke="#333333" stroke-width="0.6"/>'
                         % (bx, by, bar_w * 0.8, max(bh, 0.5), _esc(color)))
    for i in range(ncat):
        cx = x + ml + (i + 0.5) * slot
        lab = cat_labels[i] if i < len(cat_labels) else str(i + 1)
        parts.append('<text x="%.1f" y="%.1f" font-family="%s" font-size="10" '
                     'fill="#555555" text-anchor="middle">%s</text>'
                     % (cx, y + mt + plot_h + 16, _esc(font), _esc(lab)))
    # 轴标签
    parts.append('<text x="%.1f" y="%.1f" font-family="%s" font-size="11" '
                 'fill="#333333" text-anchor="middle">%s</text>'
                 % (x + ml + plot_w / 2, y + h - 8, _esc(font),
                    _esc(chart.get("x_label", ""))))
    ylab = chart.get("y_label", "")
    if chart.get("unit"):
        ylab = "%s (%s)" % (ylab, chart["unit"])
    parts.append('<text x="%.1f" y="%.1f" font-family="%s" font-size="11" '
                 'fill="#333333" transform="rotate(-90 %d %d)">%s</text>'
                 % (x + 14, y + mt + plot_h / 2, _esc(font),
                    int(x + 14), int(y + mt + plot_h / 2), _esc(ylab)))
    parts.append('</g>')
    return "\n".join(parts)


def _render_group_container(e, font):
    x, y, w, h = _entity_box(e)
    attrs = _attrs(**{"data-entity": e.get("id", ""), "data-kind": "group",
                      "data-evidence-id": e.get("evidence_id", ""),
                      "data-source-ref": e.get("source_ref", "")})
    lbl = e.get("label") or e.get("id") or ""
    parts = ['<g id="container-%s"%s>' % (_esc(e.get("id", "")), attrs)]
    parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="10" '
                 'fill="%s" fill-opacity="0.15" stroke="%s" stroke-width="1.4" '
                 'stroke-dasharray="6,4"/>'
                 % (x, y, w, h, _esc(e.get("fill", "#cfe2f3")),
                    _esc(e.get("stroke", "#3d6b9b"))))
    parts.append('<text x="%.1f" y="%.1f" font-family="%s" font-size="12" '
                 'font-weight="bold" fill="#333333">%s</text>'
                 % (x + 8, y + 16, _esc(font), _esc(lbl)))
    return "\n".join(parts)


def render_svg(spec, out_path):
    """把 figure_spec.json 渲染为分层可编辑 SVG。"""
    font = spec.get("font_family", DEFAULT_FONT)
    cw = float(spec.get("canvas", {}).get("width", 1600))
    ch = float(spec.get("canvas", {}).get("height", 1000))
    bg = spec.get("background", "#ffffff")
    entities = spec.get("entities", [])
    relations = spec.get("relations", [])
    legend = spec.get("legend", {})
    charts = spec.get("charts", [])
    annotations = spec.get("annotations", [])
    boxes = {e.get("id"): e for e in entities}
    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
                 'viewBox="0 0 %d %d" font-family="%s">'
                 % (int(cw), int(ch), int(cw), int(ch), _esc(font)))
    parts.append('<metadata>generated-by=sci-figure-editor; '
                 'spec=figure_spec.json; editable=yes</metadata>')
    parts.append(_defs(spec))
    parts.append('<g id="layer-canvas"><rect x="0" y="0" width="%d" height="%d" '
                 'fill="%s"/></g>' % (int(cw), int(ch), _esc(bg)))
    # 容器（分组）层
    containers = [e for e in entities if e.get("kind") == "group"]
    if containers:
        parts.append('<g id="layer-containers">')
        for e in containers:
            parts.append(_render_group_container(e, font))
        parts.append('</g>')
    # 关系层
    parts.append('<g id="layer-edges">')
    for r in relations:
        parts.append(_render_relation(r, {k: _entity_box(v) for k, v in boxes.items()},
                                      boxes, font))
    parts.append('</g>')
    # 图表层
    if charts:
        parts.append('<g id="layer-charts">')
        for c in charts:
            parts.append(_render_chart(c, font))
        parts.append('</g>')
    # 节点层
    parts.append('<g id="layer-nodes">')
    for e in entities:
        if e.get("kind") == "group":
            continue
        parts.append(_entity_shape(e, font))
    parts.append('</g>')
    # 标注层
    if annotations:
        parts.append('<g id="layer-annotations">')
        for a in annotations:
            attrs = _attrs(**{"data-evidence-id": a.get("evidence_id", ""),
                              "data-source-ref": a.get("source_ref", "")})
            parts.append('<text x="%.1f" y="%.1f" font-family="%s" font-size="%s" '
                         'fill="%s"%s>%s</text>'
                         % (float(a.get("x", 0)), float(a.get("y", 0)), _esc(font),
                            _esc(a.get("font_size", 11)), _esc(a.get("color", "#333333")),
                            attrs, _esc(a.get("text") or "")))
        parts.append('</g>')
    # 图例层
    if legend:
        lx = float(legend.get("x", cw - 260)); ly = float(legend.get("y", 20))
        lw = float(legend.get("w", 240))
        parts.append('<g id="layer-legend">')
        parts.append(_render_legend(legend, lx, ly, lw, font, spec))
        parts.append('</g>')
    # 标题层
    if spec.get("title"):
        parts.append('<g id="layer-title">')
        parts.append('<text x="%.1f" y="28" font-family="%s" font-size="17" '
                     'font-weight="bold" fill="#111111" text-anchor="middle">%s</text>'
                     % (cw / 2.0, _esc(font), _esc(spec["title"])))
        parts.append('</g>')
    parts.append('</svg>')
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return out_path


# ---------------------------------------------------------------------------
# SVG -> PNG / PDF 导出
# ---------------------------------------------------------------------------

def _svg_px_size(svg_path):
    try:
        tree = ET.parse(svg_path)
        root = tree.getroot()
        w = root.get("width"); h = root.get("height")
        m = re.match(r"^\s*([0-9.]+)", w or "") if w else None
        mh = re.match(r"^\s*([0-9.]+)", h or "") if h else None
        return (int(float(m.group(1))) if m else None,
                int(float(mh.group(1))) if mh else None)
    except (ET.ParseError, OSError):
        return None, None


def _png_size(path):
    try:
        with open(path, "rb") as f:
            if f.read(8) != b"\x89PNG\r\n\x1a\n":
                return None, None
            (length,) = struct.unpack(">I", f.read(4))
            if f.read(4) != b"IHDR":
                return None, None
            w, h = struct.unpack(">II", f.read(8))
            return int(w), int(h)
    except (OSError, struct.error):
        return None, None


def _pdf_font_hint(path):
    try:
        with open(path, "rb") as f:
            data = f.read(2 * 1024 * 1024)
        if not data.startswith(b"%PDF-"):
            return None
        if b"/FontFile" in data or b"/FontFile2" in data or b"/FontFile3" in data:
            return "embedded"
        return "none"
    except OSError:
        return None


def export_svg(svg_path, out_dir, work_dir):
    """导出 PNG 与 PDF。返回 export_files 记录（含真实测量与失败记录）。"""
    records = []
    wpx, hpx = _svg_px_size(svg_path)
    if not wpx or not hpx:
        wpx, hpx = 1600, 1000
    png_path = os.path.join(out_dir, os.path.splitext(os.path.basename(svg_path))[0] + ".png")
    pdf_path = os.path.join(out_dir, os.path.splitext(os.path.basename(svg_path))[0] + ".pdf")

    png_tool, png_ok, png_err = None, False, ""
    for tool, cmd in _png_commands(svg_path, png_path, wpx, hpx):
        if shutil.which(tool):
            ok, _so, se = run_cmd(cmd)
            if ok and os.path.exists(png_path):
                png_tool, png_ok = tool, True
                break
            png_err = se
    if png_ok:
        pw, ph = _png_size(png_path)
        records.append({
            "path": os.path.basename(png_path), "format": "png",
            "width_px": pw or wpx, "height_px": ph or hpx,
            "dpi": 300, "color_space": "RGB",
            "font_check": "rasterized", "tool": png_tool,
            "note": "PNG 无文本层，字体已栅格化；如需可选文本请用 SVG/PDF",
        })
    else:
        records.append({
            "path": os.path.basename(png_path), "format": "png", "failed": True,
            "reason": "no rasterizer available" + (": " + png_err[:200] if png_err else ""),
            "note": "保留 SVG 源文件，PNG 未生成，status 应为 partial",
        })

    pdf_tool, pdf_ok, pdf_err = None, False, ""
    for tool, cmd in _pdf_commands(svg_path, pdf_path):
        if shutil.which(tool):
            ok, _so, se = run_cmd(cmd)
            if ok and os.path.exists(pdf_path):
                pdf_tool, pdf_ok = tool, True
                break
            pdf_err = se
    if pdf_ok:
        fc = _pdf_font_hint(pdf_path)
        records.append({
            "path": os.path.basename(pdf_path), "format": "pdf",
            "width_px": wpx, "height_px": hpx, "dpi": 300, "color_space": "RGB",
            "font_check": fc or "unknown", "tool": pdf_tool,
            "note": "矢量 PDF，文本可选；font_check 由 PDF 内部字体描述推断",
        })
    else:
        records.append({
            "path": os.path.basename(pdf_path), "format": "pdf", "failed": True,
            "reason": "no pdf renderer available" + (": " + pdf_err[:200] if pdf_err else ""),
            "note": "保留 SVG 源文件，PDF 未生成，status 应为 partial",
        })
    return records


def _png_commands(svg, out, w, h):
    return [
        ("rsvg-convert", ["rsvg-convert", "-w", str(w), "-h", str(h), "-o", out, svg]),
        ("cairosvg", [sys.executable, "-m", "cairosvg", svg, "-o", out,
                      "--output-width", str(w), "--output-height", str(h)]),
        ("inkscape", ["inkscape", svg, "--export-type=png",
                      "--export-filename=" + out, "-w", str(w), "-h", str(h)]),
        ("magick", ["magick", "-density", "300", "-background", "white",
                    svg, "-resize", "%dx%d!" % (w, h), out]),
        ("convert", ["convert", "-density", "300", "-background", "white",
                     svg, "-resize", "%dx%d!" % (w, h), out]),
    ]


def _pdf_commands(svg, out):
    return [
        ("rsvg-convert", ["rsvg-convert", "-f", "pdf", "-o", out, svg]),
        ("cairosvg", [sys.executable, "-m", "cairosvg", svg, "-o", out]),
        ("inkscape", ["inkscape", svg, "--export-type=pdf", "--export-filename=" + out]),
    ]


# ---------------------------------------------------------------------------
# 自检：result.json 契约 + 产物
# ---------------------------------------------------------------------------

REQUIRED_KEYS = ["case_id", "status", "editable_source_files", "export_files",
                 "provenance", "sources", "uncertainty", "change_log"]


def validate_result(result_path, output_dir):
    issues, warnings = [], []
    res = load_json(result_path)
    if res is None:
        return [{"level": "error", "item": "result.json", "detail": "无法解析"}], []
    for k in REQUIRED_KEYS:
        if k not in res:
            issues.append({"level": "error", "item": "result.json",
                           "detail": "缺少字段 %s" % k})
    if res.get("case_id") in (None, ""):
        issues.append({"level": "error", "item": "case_id", "detail": "为空"})
    if res.get("status") not in ("complete", "partial", "failed"):
        issues.append({"level": "error", "item": "status",
                       "detail": "非法状态 %r" % res.get("status")})
    # 各数组必须为 list
    for k in ("editable_source_files", "export_files", "provenance",
              "sources", "uncertainty", "change_log"):
        if k in res and not isinstance(res[k], list):
            issues.append({"level": "error", "item": k, "detail": "应为数组"})
    # 证据溯源：每条结论须有 locator + quote
    em = res.get("evidence_map") or []
    for i, ev in enumerate(em):
        if not isinstance(ev, dict):
            issues.append({"level": "error", "item": "evidence_map[%d]" % i,
                           "detail": "非对象"})
            continue
        if not ev.get("locator"):
            issues.append({"level": "error", "item": "evidence_map[%d].locator" % i,
                           "detail": "缺少 locator"})
        if not ev.get("quote"):
            issues.append({"level": "error", "item": "evidence_map[%d].quote" % i,
                           "detail": "缺少逐字 quote"})
        if ev.get("status") not in ("confirmed", "inferred", "pending_human",
                                    "conflict", "not_reported"):
            warnings.append("evidence_map[%d] 状态未知: %r" % (i, ev.get("status")))
    # 产物存在性与可解析性
    for f in res.get("editable_source_files", []):
        p = os.path.join(output_dir, f.get("path", ""))
        if not os.path.exists(p):
            issues.append({"level": "error", "item": f.get("path"),
                           "detail": "可编辑源文件不存在"})
        elif (f.get("path", "").lower().endswith(".svg")
              and not _svg_ok(p)):
            issues.append({"level": "error", "item": f.get("path"),
                           "detail": "SVG 无法解析"})
    for f in res.get("export_files", []):
        if f.get("failed"):
            warnings.append("%s 导出失败: %s" % (f.get("path"), f.get("reason")))
            continue
        p = os.path.join(output_dir, f.get("path", ""))
        if not os.path.exists(p):
            issues.append({"level": "error", "item": f.get("path"),
                           "detail": "导出件不存在"})
        elif f.get("format") == "png":
            w, h = _png_size(p)
            if (w, h) == (None, None):
                issues.append({"level": "error", "item": f.get("path"),
                               "detail": "PNG 头无效"})
        elif f.get("format") == "pdf":
            try:
                with open(p, "rb") as fh:
                    if not fh.read(5).startswith(b"%PDF-"):
                        issues.append({"level": "error", "item": f.get("path"),
                                       "detail": "PDF 头无效"})
            except OSError:
                issues.append({"level": "error", "item": f.get("path"),
                               "detail": "PDF 不可读"})
    # provenance 每条须可溯源
    for i, p in enumerate(res.get("provenance", [])):
        if isinstance(p, dict) and not (p.get("evidence_id") or p.get("source_ref")
                                        or p.get("locator")):
            issues.append({"level": "error", "item": "provenance[%d]" % i,
                           "detail": "缺少溯源字段"})
    return issues, warnings


def _svg_ok(path):
    try:
        ET.parse(path)
        return True
    except (ET.ParseError, OSError):
        return False


def svg_inventory(svg_path):
    """统计 SVG 的图层数、可编辑文本数、溯源属性数。"""
    try:
        tree = ET.parse(svg_path)
        root = tree.getroot()
        ns = {"s": "http://www.w3.org/2000/svg"}
        groups = root.findall(".//s:g", ns)
        texts = root.findall(".//s:text", ns)
        evid = [e for e in root.iter() if e.get("data-evidence-id")]
        return {"groups": len(groups), "texts": len(texts),
                "traced_elements": len(evid)}
    except (ET.ParseError, OSError):
        return {"groups": 0, "texts": 0, "traced_elements": 0}


# ---------------------------------------------------------------------------
# 骨架 / 合并
# ---------------------------------------------------------------------------

def make_skeleton(case_summary, now, notes=None):
    return {
        "case_id": case_summary.get("case_id", "unknown"),
        "status": "partial",
        "primary_outputs": [],
        "editable_source_files": [],
        "export_files": [],
        "provenance": [],
        "evidence_map": [],
        "sources": [],
        "uncertainty": [{
            "item": "骨架初始化",
            "reason": "在材料解析前先落盘，保证中途异常也留下合法结果",
            "impact": "此时尚无图稿，status=partial",
            "mitigation": "后续阶段增量回填 entities/evidence_map/产物",
        }],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "change_log": [{
            "time": now, "action": "scaffold", "detail": "写入 partial 结果骨架",
        }],
        "runtime": {
            "started_at": now,
            "case_keys": case_summary.get("case_keys", []),
            "declared_materials": case_summary.get("declared_materials", []),
        },
    }


def merge_result(result_path, evidence_path, spec_path, work_dir, extra=None):
    """把 evidence_map / provenance / sources 合并进 result.json，并打时间戳。"""
    res = load_json(result_path, {}) or {}
    evidence = load_json(evidence_path, []) or []
    spec = load_json(spec_path, {}) or {}
    if evidence:
        res["evidence_map"] = evidence
    # 从 spec 生成 provenance（若尚未有）
    prov = list(res.get("provenance", []))
    seen = {p.get("element") for p in prov if isinstance(p, dict)}
    for e in spec.get("entities", []):
        if e.get("id") in seen:
            continue
        prov.append({
            "element": e.get("id"),
            "kind": "entity",
            "evidence_id": e.get("evidence_id", ""),
            "source_ref": e.get("source_ref", ""),
            "mapping": "node label=%r" % (e.get("label")),
            "version": "v1", "confirmed": bool(e.get("evidence_id")),
        })
        seen.add(e.get("id"))
    for r in spec.get("relations", []):
        if r.get("id") in seen:
            continue
        prov.append({
            "element": r.get("id"),
            "kind": "relation",
            "evidence_id": r.get("evidence_id", ""),
            "source_ref": r.get("source_ref", ""),
            "mapping": "%s --%s--> %s" % (r.get("from"), r.get("type"), r.get("to")),
            "version": "v1", "confirmed": bool(r.get("evidence_id")),
        })
        seen.add(r.get("id"))
    res["provenance"] = prov
    res["change_log"] = list(res.get("change_log", [])) + [{
        "time": now_iso(), "action": "merge",
        "detail": "回填 evidence_map=%d 条，provenance=%d 条" % (len(evidence), len(prov)),
    }]
    for e in evidence:
        if e.get("status") == "pending_human" and isinstance(e.get("claim"), str):
            ph = res.setdefault("pending_human", [])
            if not any(p.get("item") == e["claim"] for p in ph):
                ph.append({"item": e["claim"], "reason": e.get("notes", ""),
                           "locator": e.get("locator"), "quote": e.get("quote")})
    if extra:
        for k, v in extra.items():
            res[k] = v
    save_json(result_path, res)
    return res


def set_status(result_path, status):
    res = load_json(result_path, {}) or {}
    res["status"] = status
    res["change_log"] = list(res.get("change_log", [])) + [{
        "time": now_iso(), "action": "set_status", "detail": status,
    }]
    save_json(result_path, res)
    return res
