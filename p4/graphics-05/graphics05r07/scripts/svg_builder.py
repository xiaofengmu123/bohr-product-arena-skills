#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic layered SVG builder + validator + export prober (stdlib only).

The builder turns a figure_spec dict (produced by main.py, then refined by the Agent)
into a single-page, layered, editable SVG. Layout is fully deterministic: fixed seed,
sorted element order and a fixed grid, so the same spec reproduces the same structure.
Text stays editable <text>; every visual element carries data-source provenance.

Validation checks the things the scoring dimensions care about (layers, editable text,
font floor, canvas overflow, data-source presence). Export probing never fabricates a
render: if no renderer exists it reports preview_not_rendered.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

LAYER_NAMES = [
    "layer-title", "layer-question", "layer-method",
    "layer-results", "layer-conclusion", "layer-legend",
]

# Okabe-Ito colorblind-safe palette; grayscale-separable by luminance order.
PALETTE = [
    "#0072B2", "#E69F00", "#009E73", "#D55E00",
    "#CC79A7", "#56B4E9", "#F0E442", "#000000",
]
GRAY_PALETTE = ["#1a1a1a", "#3d3d3d", "#666666", "#8c8c8c", "#b3b3b3", "#d9d9d9"]

MIN_FONT_PX = 8


def _esc(s) -> str:
    return "" if s is None else str(s)


def _safe_id(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", _esc(s))[:40] or "node"


def _quote(source, field="quote", limit=240) -> str:
    if isinstance(source, dict):
        q = source.get(field) or source.get("quote") or ""
        return _esc(q)[:limit]
    return ""


def _locator(source) -> str:
    if isinstance(source, dict):
        return _esc(source.get("locator") or source.get("file") or "")
    return ""


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #
def build(spec: dict, svg_path: Path):
    spec = spec or {}
    constraints = spec.get("constraints") or {}
    canvas = constraints.get("canvas") or {"width": 1600, "height": 1100}
    W = int(canvas.get("width", 1600))
    H = int(canvas.get("height", 1100))
    grayscale = str(constraints.get("palette", "")).startswith("grayscale")
    palette = GRAY_PALETTE if grayscale else PALETTE

    margin = 60
    title_h = 90
    legend_h = 74
    gap = 30
    panel_w = (W - 2 * margin - 3 * gap) // 4
    panel_y = margin + title_h
    panel_h = H - margin - panel_y - legend_h

    svg = ET.Element("svg", {
        "xmlns": SVG_NS,
        "width": str(W),
        "height": str(H),
        "viewBox": f"0 0 {W} {H}",
        "role": "img",
        "data-case-id": _safe_id(spec.get("case_id", "")),
    })
    ET.SubElement(svg, "title").text = "Graphical abstract"

    defs = ET.SubElement(svg, "defs")
    marker = ET.SubElement(defs, "marker", {
        "id": "arrow", "viewBox": "0 0 10 10", "refX": "9", "refY": "5",
        "markerWidth": "7", "markerHeight": "7", "orient": "auto-start-reverse",
    })
    ET.SubElement(marker, "path", {"d": "M 0 0 L 10 5 L 0 10 z", "fill": "#333333"})

    def layer(name):
        g = ET.SubElement(svg, "g", {"id": name, "data-layer": name})
        return g

    # ---- title ----
    g_title = layer("layer-title")
    t = ET.SubElement(g_title, "text", {
        "x": str(margin), "y": str(margin + 40),
        "font-size": "28", "font-weight": "bold", "fill": "#111111",
        "font-family": "Arial, Helvetica, 'Noto Sans CJK SC', sans-serif",
        "data-source": _locator(spec.get("title_source")),
    })
    t.text = _esc(spec.get("title") or spec.get("case_id") or "Graphical Abstract")

    # ---- four panels ----
    panels = [
        ("layer-question", "研究问题", palette[0], "question"),
        ("layer-method", "方法路线", palette[1], "method"),
        ("layer-results", "核心结果", palette[2], "results"),
        ("layer-conclusion", "结论", palette[3], "conclusion"),
    ]
    ga = spec.get("graphical_abstract") or {}
    entities = spec.get("entities") or []
    relations = spec.get("relations") or []
    data = spec.get("data") or []
    tabular = spec.get("tabular") or []

    for idx, (layer_id, header, color, key) in enumerate(panels):
        g = layer(layer_id)
        x0 = margin + idx * (panel_w + gap)
        ET.SubElement(g, "rect", {
            "x": str(x0), "y": str(panel_y), "width": str(panel_w),
            "height": str(panel_h), "fill": "#fafafa", "stroke": color,
            "stroke-width": "2", "rx": "10", "data-panel": key,
        })
        ht = ET.SubElement(g, "text", {
            "x": str(x0 + 16), "y": str(panel_y + 32),
            "font-size": "18", "font-weight": "bold", "fill": color,
            "font-family": "Arial, Helvetica, 'Noto Sans CJK SC', sans-serif",
        })
        ht.text = header

    # ---- question panel: entity boxes + question text ----
    g = next(g for g in svg if g.get("id") == "layer-question")
    x0 = margin
    question_text = ga.get("question") or "研究问题（待 Agent 从材料确认）"
    qt = ET.SubElement(g, "text", {
        "x": str(x0 + 16), "y": str(panel_y + 60), "font-size": "14", "fill": "#222222",
        "font-family": "Arial, Helvetica, 'Noto Sans CJK SC', sans-serif",
    })
    qt.text = _esc(question_text)[:90]
    q_entities = entities[0:3] if entities else [{"id": "qe", "label": "研究对象", "source": {}}]
    for i, ent in enumerate(q_entities):
        bx = x0 + 16
        by = panel_y + 80 + i * 92
        bw = panel_w - 32
        bh = 74
        ET.SubElement(g, "rect", {
            "x": str(bx), "y": str(by), "width": str(bw), "height": str(bh),
            "rx": "8", "fill": palette[4] if grayscale else palette[(i + 1) % len(palette)],
            "stroke": "#333333", "stroke-width": "1.5",
            "data-source": _locator(ent.get("source")),
            "data-entity": _safe_id(ent.get("id", f"e{i}")),
        })
        et = ET.SubElement(g, "text", {
            "x": str(bx + 12), "y": str(by + 44), "font-size": "13", "fill": "#111111",
            "font-family": "Arial, Helvetica, 'Noto Sans CJK SC', sans-serif",
            "data-source": _locator(ent.get("source")),
        })
        et.text = _esc(ent.get("label"))[:30]

    # ---- method panel: relations as directed arrows ----
    g = next(g for g in svg if g.get("id") == "layer-method")
    x0 = margin + panel_w + gap
    method_text = ga.get("method") or "方法路线（待 Agent 从材料确认）"
    mt = ET.SubElement(g, "text", {
        "x": str(x0 + 16), "y": str(panel_y + 60), "font-size": "14", "fill": "#222222",
        "font-family": "Arial, Helvetica, 'Noto Sans CJK SC', sans-serif",
    })
    mt.text = _esc(method_text)[:90]
    rels = relations[0:4] if relations else [{"id": "mr", "from": "A", "to": "B",
                                              "type": "directed", "source": {}}]
    for i, rel in enumerate(rels):
        y = panel_y + 92 + i * 68
        bx1 = x0 + 16
        bx2 = x0 + panel_w - 16
        bx2_label = bx2 - 90
        line = ET.SubElement(g, "line", {
            "x1": str(bx1), "y1": str(y), "x2": str(bx2_label), "y2": str(y),
            "stroke": "#333333", "stroke-width": "2",
            "marker-end": "url(#arrow)" if rel.get("type") != "undirected" else "",
            "data-source": _locator(rel.get("source")),
            "data-relation": _safe_id(rel.get("id", f"r{i}")),
        })
        if not line.get("marker-end"):
            line.set("marker-end", "none")
        lt = ET.SubElement(g, "text", {
            "x": str(bx1 + 8), "y": str(y - 10), "font-size": "12", "fill": "#111111",
            "font-family": "Arial, Helvetica, 'Noto Sans CJK SC', sans-serif",
            "data-source": _locator(rel.get("source")),
        })
        lt.text = f"{_esc(rel.get('from'))[:16]} → {_esc(rel.get('to'))[:16]}"

    # ---- results panel: length-encoded bars ----
    g = next(g for g in svg if g.get("id") == "layer-results")
    x0 = margin + 2 * (panel_w + gap)
    results_text = ga.get("results") or "核心结果（待 Agent 从材料确认）"
    rt = ET.SubElement(g, "text", {
        "x": str(x0 + 16), "y": str(panel_y + 60), "font-size": "14", "fill": "#222222",
        "font-family": "Arial, Helvetica, 'Noto Sans CJK SC', sans-serif",
    })
    rt.text = _esc(results_text)[:90]
    rows = []
    if tabular:
        rows = tabular[0:6]
    elif data:
        rows = data[0:6]
    if not rows:
        rows = [{"id": "d0", "value": "0", "unit": "", "source": {}}]
    bar_max_w = panel_w - 140
    for i, d in enumerate(rows):
        try:
            val = float(d.get("value"))
        except (TypeError, ValueError):
            val = 0.0
        y = panel_y + 92 + i * 52
        bar_len = int(max(10, min(bar_max_w, abs(val) * bar_max_w / 100.0))) \
            if abs(val) > 0 else 10
        ET.SubElement(g, "rect", {
            "x": str(x0 + 16), "y": str(y), "width": str(bar_len), "height": "30",
            "rx": "4", "fill": palette[(i + 1) % len(palette)],
            "data-source": _locator(d.get("source")),
            "data-value": _esc(d.get("value")),
            "data-unit": _esc(d.get("unit") or ""),
            "data-target": _safe_id(d.get("id", f"d{i}")),
        })
        bt = ET.SubElement(g, "text", {
            "x": str(x0 + 16 + bar_len + 10), "y": str(y + 21),
            "font-size": "12", "fill": "#111111",
            "font-family": "Arial, Helvetica, 'Noto Sans CJK SC', sans-serif",
            "data-source": _locator(d.get("source")),
        })
        bt.text = f"{_esc(d.get('value'))} {_esc(d.get('unit') or '')}"[:24].strip()

    # ---- conclusion panel ----
    g = next(g for g in svg if g.get("id") == "layer-conclusion")
    x0 = margin + 3 * (panel_w + gap)
    conclusion_text = ga.get("conclusion") or "结论（待 Agent 从材料确认）"
    ct = ET.SubElement(g, "text", {
        "x": str(x0 + 16), "y": str(panel_y + 60), "font-size": "14", "fill": "#222222",
        "font-family": "Arial, Helvetica, 'Noto Sans CJK SC', sans-serif",
    })
    ct.text = _esc(conclusion_text)[:90]
    c_box = ET.SubElement(g, "rect", {
        "x": str(x0 + 16), "y": str(panel_y + 88), "width": str(panel_w - 32),
        "height": str(max(60, panel_h - 120)), "rx": "8", "fill": "#fefefe",
        "stroke": palette[3], "stroke-width": "1.5",
        "data-source": _locator(ga.get("conclusion_source")),
    })
    c_box.tail = None

    # ---- legend ----
    g = layer("layer-legend")
    ly = H - margin - legend_h + 24
    legend = spec.get("legend") or []
    if not legend and entities:
        legend = [{"label": f"{e.get('label', '')} "[:24], "color": palette[(i + 1) % len(palette)]}
                  for i, e in enumerate(entities[0:6])]
    if not legend:
        legend = [{"label": "视觉通道：颜色/长度编码", "color": palette[0]}]
    lx = margin
    for i, lg in enumerate(legend[0:8]):
        sw = ET.SubElement(g, "rect", {
            "x": str(lx), "y": str(ly - 14), "width": "18", "height": "18",
            "rx": "3", "fill": _esc(lg.get("color") or palette[0]),
            "data-source": _locator(lg.get("source")),
        })
        sw.tail = None
        lab = ET.SubElement(g, "text", {
            "x": str(lx + 26), "y": str(ly), "font-size": "12", "fill": "#111111",
            "font-family": "Arial, Helvetica, 'Noto Sans CJK SC', sans-serif",
        })
        lab.text = _esc(lg.get("label"))[:26]
        lx += 200

    svg_path.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(svg)
    tree.write(svg_path, encoding="utf-8", xml_declaration=True)
    return svg_path


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def _parse_font_size(value) -> float | None:
    if value is None:
        return None
    m = re.search(r"([\d.]+)", str(value))
    return float(m.group(1)) if m else None


def validate_svg(svg_path: Path) -> dict:
    report = {"valid": False, "errors": [], "warnings": [], "layers": [],
              "text_count": 0, "data_source_count": 0}
    if not svg_path.exists():
        report["errors"].append(f"SVG missing: {svg_path}")
        return report
    try:
        tree = ET.parse(svg_path)
        root = tree.getroot()
    except ET.ParseError as exc:
        report["errors"].append(f"SVG not well-formed XML: {exc}")
        return report
    layers = []
    text_count = 0
    data_source_count = 0
    font_issues = 0
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag == "g" and (el.get("id") or "").startswith("layer-"):
            layers.append(el.get("id"))
        if tag == "text":
            text_count += 1
            fs = _parse_font_size(el.get("font-size"))
            if fs is not None and fs < MIN_FONT_PX:
                font_issues += 1
                if font_issues <= 5:
                    report["warnings"].append(
                        f"font-size {fs}px < {MIN_FONT_PX}px in <text>")
        if el.get("data-source"):
            data_source_count += 1
    report["layers"] = layers
    report["text_count"] = text_count
    report["data_source_count"] = data_source_count
    for name in LAYER_NAMES:
        if name not in layers:
            report["errors"].append(f"missing layer group: {name}")
    if text_count == 0:
        report["errors"].append("no editable <text> elements (possible text-as-path)")
    if data_source_count == 0:
        report["errors"].append("no data-source provenance attributes")
    if font_issues:
        report["errors"].append(f"{font_issues} text elements below {MIN_FONT_PX}px")
    report["valid"] = not report["errors"]
    return report


def validate_existing(output_dir: Path, input_dir: Path) -> dict:
    svg_candidates = sorted(output_dir.glob("fig_*.svg")) if output_dir.exists() else []
    result_path = output_dir / "result.json" if output_dir.exists() else None
    report = {"valid": False, "svg_files": [str(p) for p in svg_candidates],
              "svg_check": None, "result_exists": bool(result_path and result_path.exists())}
    if svg_candidates:
        report["svg_check"] = validate_svg(svg_candidates[0])
    report["valid"] = bool(report["result_exists"] and svg_candidates
                           and (report["svg_check"] or {}).get("valid"))
    return report


# --------------------------------------------------------------------------- #
# Export probing (never fabricates a render)
# --------------------------------------------------------------------------- #
def _which(name: str) -> str | None:
    return shutil.which(name)


def _run_no_capture(cmd) -> bool:
    """Run a renderer with stdio ignored (no pipe capture, sandbox-safe)."""
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, timeout=120,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _cairosvg_available() -> bool:
    return importlib.util.find_spec("cairosvg") is not None


def try_exports(svg_path: Path, output_dir: Path) -> list[dict]:
    exports = []
    png_path = svg_path.with_suffix(".png")
    pdf_path = svg_path.with_suffix(".pdf")

    png_ok = False
    png_renderer = None
    if _cairosvg_available():
        try:
            import cairosvg  # noqa: F401
            cairosvg.svg2png(url=str(svg_path), write_to=str(png_path))
            png_ok, png_renderer = png_path.exists(), "cairosvg"
        except Exception:
            png_ok = False
    if not png_ok and _which("rsvg-convert"):
        if _run_no_capture(["rsvg-convert", "-o", str(png_path), str(svg_path)]):
            png_ok, png_renderer = png_path.exists(), "rsvg-convert"
    if not png_ok and _which("inkscape"):
        if _run_no_capture(["inkscape", "--export-type=png",
                            f"--export-filename={png_path}", str(svg_path)]):
            png_ok, png_renderer = png_path.exists(), "inkscape"
    if not png_ok and _which("magick"):
        if _run_no_capture(["magick", "-density", "150", str(svg_path), str(png_path)]):
            png_ok, png_renderer = png_path.exists(), "imagemagick"
    exports.append({"format": "png", "path": png_path.name,
                    "renderer": png_renderer,
                    "status": "rendered" if png_ok else "preview_not_rendered"})

    pdf_ok = False
    pdf_renderer = None
    if _cairosvg_available():
        try:
            import cairosvg  # noqa: F401
            cairosvg.svg2pdf(url=str(svg_path), write_to=str(pdf_path))
            pdf_ok, pdf_renderer = pdf_path.exists(), "cairosvg"
        except Exception:
            pdf_ok = False
    if not pdf_ok and _which("rsvg-convert"):
        if _run_no_capture(["rsvg-convert", "-f", "pdf", "-o", str(pdf_path), str(svg_path)]):
            pdf_ok, pdf_renderer = pdf_path.exists(), "rsvg-convert"
    if not pdf_ok and _which("inkscape"):
        if _run_no_capture(["inkscape", "--export-type=pdf",
                            f"--export-filename={pdf_path}", str(svg_path)]):
            pdf_ok, pdf_renderer = pdf_path.exists(), "inkscape"
    if not pdf_ok and _which("chromium"):
        if _run_no_capture(["chromium", "--headless", "--disable-gpu",
                            f"--print-to-pdf={pdf_path}", str(svg_path)]):
            pdf_ok, pdf_renderer = pdf_path.exists(), "chromium"
    exports.append({"format": "pdf", "path": pdf_path.name,
                    "renderer": pdf_renderer,
                    "status": "rendered" if pdf_ok else "preview_not_rendered"})
    return exports
