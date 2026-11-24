#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic layered-SVG builder + honest PNG/PDF rendering. Stdlib only.

Reads /app/work/figure_spec.json (curated by the agent), writes
/app/output/fig_<case_id>.svg and attempts real previews. Never fabricates an
image: a missing renderer yields `preview_not_rendered` instead of a fake file.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess

DEFAULT_WORK = "/app/work"
DEFAULT_OUTPUT = "/app/output"

# Colorblind-safe (Okabe-Ito), ordered for determinism.
PALETTE = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7",
           "#56B4E9", "#8c564b", "#444444"]
INK = "#1a1a1a"
PANEL_FILL = "#f7f7f5"
PANEL_STROKE = "#8c8c8c"
TEXT_FONT = "DejaVu Sans, Arial, sans-serif"
MIN_FONT = 12
ARROW_MARKER = "arrowhead"


def escape_xml(text):
    if text is None:
        return ""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;"))


def char_width(ch):
    o = ord(ch)
    if o > 0x2E7F or (0x3000 <= o <= 0x303F) or (0xFF00 <= o <= 0xFFEF):
        return 1.0
    return 0.6


def wrap_label(label, max_width, font_size):
    label = str(label or "")
    lines, current, w = [], "", 0.0
    for ch in label:
        cw = char_width(ch) * font_size
        if current and w + cw > max_width:
            lines.append(current)
            current, w = ch, cw
        else:
            current += ch
            w += cw
    if current:
        lines.append(current)
    return lines or [""]


def _svg_header(w, h):
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" font-family="{TEXT_FONT}">\n'
        f'<defs>\n<marker id="{ARROW_MARKER}" markerWidth="10" markerHeight="7" '
        f'refX="9" refY="3.5" orient="auto" markerUnits="strokeWidth">'
        f'<path d="M0,0 L10,3.5 L0,7 z" fill="{INK}"/></marker>\n</defs>\n'
    )


def _rect(g, x, y, w, h, fill, stroke=None, rx=8, extra=""):
    s = (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
         f'rx="{rx}" fill="{fill}"')
    if stroke:
        s += f' stroke="{stroke}" stroke-width="1.5"'
    if extra:
        s += " " + extra
    return g + s + "/>"


def _text(g, x, y, text, size, fill=INK, weight="normal", anchor="start", extra=""):
    s = (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
         f'font-weight="{weight}" text-anchor="{anchor}"')
    if extra:
        s += " " + extra
    return g + s + f">{escape_xml(text)}</text>"


def _line(g, x1, y1, x2, y2, stroke=INK, width=2, marker=None, extra=""):
    s = f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke}" stroke-width="{width}"'
    if marker:
        s += f' marker-end="url(#{marker})"'
    if extra:
        s += " " + extra
    return g + s + "/>"


def _panel(g, name, x, y, w, h, title):
    out = [_rect("", x, y, w, h, PANEL_FILL, PANEL_STROKE, 10)]
    out.append(_text("", x + 16, y + 28, title, 20, INK, "bold"))
    return "".join(out)


def build(spec, output_dir, work_dir):
    """Build the SVG and attempt PNG/PDF. Returns {svg, exports, errors}."""
    errors = []
    case_id = str((spec or {}).get("case_id", "case"))
    w = int(spec.get("width", 1400) or 1400)
    h = int(spec.get("height", 1000) or 1000)
    title = spec.get("title") or "Graphical Abstract"

    # Panel bands (deterministic, top-to-bottom reading order).
    bands = {
        "question": (90, 220),
        "method": (220, 560),
        "results": (560, 820),
        "conclusion": (820, 920),
        "legend": (920, 1000),
    }
    parts = []
    parts.append(_svg_header(w, h))
    parts.append('<g id="layer-background">')
    parts.append(_rect("", 0, 0, w, h, "#ffffff"))
    parts.append("</g>")

    # Panels (fixed reading order: question -> method -> results -> conclusion).
    parts.append('<g id="layer-panels">')
    titles = {"question": "研究问题 Question", "method": "方法 Method",
              "results": "核心结果 Results", "conclusion": "结论 Conclusion",
              "legend": "图例 Legend"}
    for name, (y0, y1) in bands.items():
        parts.append(_panel("", name, 40, y0, w - 80, y1 - y0, titles[name]))
    parts.append("</g>")

    # All text in a single layer-labels group: title + four-element narrative.
    ga = spec.get("graphical_abstract") or {}
    ga_pos = {"question": 130, "method": 260, "results": 600, "conclusion": 860}
    parts.append('<g id="layer-labels">')
    parts.append(_text("", w / 2, 48, title, 30, INK, "bold", "middle"))
    for key in ("question", "method", "results", "conclusion"):
        text = ga.get(key)
        if text:
            lines = wrap_label(text, w - 160, 18)
            for i, ln in enumerate(lines[:4]):
                parts.append(_text("", 60, ga_pos[key] + i * 26, ln, 18))
    parts.append("</g>")

    # Entities grid inside method band.
    entities = [e for e in (spec.get("entities") or []) if isinstance(e, dict)
                and e.get("evidence") and e["evidence"].get("locator")
                and e["evidence"].get("quote")]
    parts.append('<g id="layer-entities">')
    n = len(entities)
    centers = {}
    if n:
        cols = min(4, n)
        bw, bh, gap = 300, 58, 26
        x0 = 60
        y0 = 300
        for i, ent in enumerate(entities):
            c = i % cols
            r = i // cols
            x = x0 + c * (bw + gap)
            y = y0 + r * (bh + gap)
            color = PALETTE[i % len(PALETTE)]
            ev = ent["evidence"]
            extra = (f'data-source="{escape_xml(ent.get("source") or ev.get("locator"))}" '
                     f'data-evidence="{escape_xml(ent.get("id") or "")}"')
            parts.append(_rect("", x, y, bw, bh, "#ffffff", color, 8, extra))
            label = ent.get("label") or ent.get("id") or "?"
            lines = wrap_label(label, bw - 20, 14)
            for j, ln in enumerate(lines[:3]):
                parts.append(_text("", x + bw / 2, y + 22 + j * 18, ln, 14,
                                   color, "bold", "middle"))
            centers[ent.get("id")] = (x + bw / 2, y + bh / 2)
    parts.append("</g>")

    # Relations between resolved entity ids (unresolved are flagged, not invented).
    parts.append('<g id="layer-relations">')
    relations = [r for r in (spec.get("relations") or []) if isinstance(r, dict)
                 and r.get("from") in centers and r.get("to") in centers]
    for rel in relations:
        x1, y1 = centers[rel["from"]]
        x2, y2 = centers[rel["to"]]
        marker = ARROW_MARKER if rel.get("directed", True) else None
        ev = rel.get("evidence") or {}
        extra = (f'data-source="{escape_xml(rel.get("source") or ev.get("locator"))}" '
                 f'data-evidence="{escape_xml(rel.get("id") or "")}"')
        parts.append(_line("", x1 + 20, y1, x2 - 20, y2, INK, 2.5, marker, extra))
        mid = ((x1 + x2) / 2, (y1 + y2) / 2)
        if rel.get("label"):
            parts.append(_text("", mid[0], mid[1] - 6, rel["label"], 13, INK,
                               "bold", "middle"))
    parts.append("</g>")

    for rel in (spec.get("relations") or []):
        if isinstance(rel, dict) and (rel.get("from") not in centers
                                      or rel.get("to") not in centers):
            errors.append(
                f"relation {rel.get('id', '?')} unresolved from/to; "
                "set from/to to entity ids or mark pending_human")
    if spec.get("entities") and not entities:
        errors.append("entities exist but lack evidence; nothing drawn")

    # Data marks inside results band (bars sized by numeric value only).
    data = [d for d in (spec.get("data") or []) if isinstance(d, dict)
            and d.get("evidence") and d["evidence"].get("locator")
            and d["evidence"].get("quote")]
    parts.append('<g id="layer-data">')
    if data:
        x0, y0 = 60, 640
        max_val = 1.0
        vals = []
        for d in data:
            m = re.search(r"-?\d+(?:\.\d+)?", str(d.get("value", "")))
            vals.append(float(m.group(0)) if m else None)
        numeric = [v for v in vals if v is not None]
        if numeric:
            max_val = max(abs(v) for v in numeric) or 1.0
        for i, d in enumerate(data):
            y = y0 + i * 52
            color = PALETTE[(i + 2) % len(PALETTE)]
            v = vals[i]
            if v is not None:
                bar_w = max(10.0, 420.0 * abs(v) / max_val)
            else:
                bar_w = 12.0  # placeholder swatch, value stays null (not fabricated)
            ev = d["evidence"]
            extra = (f'data-source="{escape_xml(d.get("source") or ev.get("locator"))}" '
                     f'data-evidence="{escape_xml(d.get("id") or "")}"')
            parts.append(_rect("", x0, y, bar_w, 26, color, None, 4, extra))
            label = d.get("label") or d.get("value") or "?"
            value = d.get("value")
            if v is not None and value and str(value) != str(label):
                label = f"{label}  {value}"
            parts.append(_text("", x0 + bar_w + 12, y + 19, label, 14, INK))
        parts.append("</g>")
    else:
        parts.append("</g>")

    # Legend.
    legend = [lg for lg in (spec.get("legend") or []) if isinstance(lg, dict)]
    parts.append('<g id="layer-legend">')
    lx = 60
    for i, lg in enumerate(legend):
        color = PALETTE[i % len(PALETTE)]
        ev = lg.get("evidence") or {}
        extra = (f'data-source="{escape_xml(lg.get("source") or ev.get("locator"))}" '
                 f'data-evidence="{escape_xml(lg.get("id") or "")}"')
        parts.append(_rect("", lx, 940, 18, 18, color, None, 3, extra))
        parts.append(_text("", lx + 26, 954, lg.get("label") or lg.get("id") or "?",
                           13, INK))
        lx += 26 + max(120, len(str(lg.get("label") or "")) * 8 + 60)
        if lx > w - 200:
            break
    parts.append("</g>")

    parts.append("</svg>\n")
    svg_text = "".join(parts)
    svg_path = os.path.join(output_dir, f"fig_{case_id}.svg")
    os.makedirs(output_dir, exist_ok=True)
    with open(svg_path, "w", encoding="utf-8") as fh:
        fh.write(svg_text)

    exports = _render(svg_path, w, h, output_dir)
    return {"svg": svg_path, "exports": exports, "errors": errors}


# ------------------------------------------------------------------- rendering
def _probe(candidates):
    for c in candidates:
        if shutil.which(c):
            return c
    return None


def _render(svg_path, w, h, output_dir):
    base = os.path.splitext(svg_path)[0]
    exports = []

    def record(fmt, path, renderer, status):
        exports.append({"path": os.path.relpath(path, output_dir).replace("\\", "/"),
                        "format": fmt, "renderer": renderer, "status": status})

    # PNG
    png = base + ".png"
    rsvg = _probe(["rsvg-convert"])
    inkscape = _probe(["inkscape"])
    chrome = _probe(["chromium", "chromium-browser", "google-chrome",
                     "google-chrome-stable", "chrome"])
    if rsvg:
        if _run(["rsvg-convert", "-w", str(w), "-h", str(h), "-o", png, svg_path]):
            record("png", png, "rsvg-convert", "rendered")
    elif inkscape:
        if _run(["inkscape", svg_path, "--export-type=png",
                 "--export-filename=" + png, "--export-width=" + str(w)]):
            record("png", png, "inkscape", "rendered")
    elif chrome:
        if _run([chrome, "--headless", "--disable-gpu", "--no-sandbox",
                 f"--screenshot={png}", f"--window-size={w},{h}",
                 "--default-background-color=FFFFFFFF",
                 "file://" + os.path.abspath(svg_path)]):
            record("png", png, chrome, "rendered")
    else:
        record("png", png, None, "preview_not_rendered")

    # PDF
    pdf = base + ".pdf"
    if rsvg:
        if _run(["rsvg-convert", "-f", "pdf", "-o", pdf, svg_path]):
            record("pdf", pdf, "rsvg-convert", "rendered")
    elif inkscape:
        if _run(["inkscape", svg_path, "--export-type=pdf", "--export-filename=" + pdf]):
            record("pdf", pdf, "inkscape", "rendered")
    elif chrome:
        if _run([chrome, "--headless", "--disable-gpu", "--no-sandbox",
                 f"--print-to-pdf={pdf}", "file://" + os.path.abspath(svg_path)]):
            record("pdf", pdf, chrome, "rendered")
    else:
        record("pdf", pdf, None, "preview_not_rendered")
    return exports


def _run(cmd):
    try:
        cp = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=180)
        return cp.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _load_spec(work_dir):
    path = os.path.join(work_dir, "figure_spec.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default=DEFAULT_WORK)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    args = ap.parse_args(argv)
    spec = _load_spec(args.work)
    if spec is None:
        print(json.dumps({"error": "figure_spec.json not found or invalid"},
                         ensure_ascii=False))
        return 1
    result = build(spec, args.output, args.work)
    print(json.dumps({"svg": result["svg"], "exports": result["exports"],
                      "errors": result["errors"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
