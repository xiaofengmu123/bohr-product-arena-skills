#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic layered editable SVG builder for graphical abstracts.

Input : figure_spec.json (produced/edited by the agent, see SKILL.md section 5)
Output: fig_<case_id>.svg + fig_manifest.json + honest export status.

Pure standard library. Layout is deterministic: the same spec always produces
the same SVG structure. Missing geometry is filled with a deterministic grid so
the figure remains reproducible even when the agent only supplied content.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import xml.sax.saxutils as saxutils

try:
    from xml.etree import ElementTree as ET
except ImportError:  # pragma: no cover - Python 3 stdlib always has it
    ET = None

DEFAULT_CANVAS = {"width": 1600, "height": 900, "margin": 40}


def _esc(text):
    if text is None:
        return ""
    return saxutils.escape(str(text), {'"': "&quot;", "'": "&apos;"})


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _grid_slot(index, cols, rows, canvas, margin, gap):
    """Deterministic 2D grid cell (x, y, w, h) for panel auto-layout."""
    avail_w = canvas["width"] - 2 * margin - (cols - 1) * gap
    avail_h = canvas["height"] - 2 * margin - (rows - 1) * gap
    col = index % cols
    row = index // cols
    x = margin + col * (avail_w / cols + gap)
    y = margin + row * (avail_h / rows + gap)
    return round(x, 1), round(y, 1), round(avail_w / cols, 1), round(avail_h / rows, 1)


def _normalize_spec(spec):
    """Fill defaults and compute deterministic geometry for panels/entities."""
    spec = dict(spec or {})
    canvas = dict(DEFAULT_CANVAS)
    canvas.update(spec.get("canvas") or {})
    spec["canvas"] = canvas

    reading_order = spec.get("reading_order") or ["problem", "method", "results", "conclusion"]
    panels = spec.get("panels") or []
    panel_map = {p.get("id"): p for p in panels}

    # Ensure the four canonical panels exist in reading order.
    ordered = []
    seen = set()
    for pid in reading_order:
        if pid not in seen:
            seen.add(pid)
            ordered.append(panel_map.get(pid) or {"id": pid, "title": pid})
    for p in panels:
        if p.get("id") not in seen:
            seen.add(p.get("id"))
            ordered.append(p)
    spec["panels"] = ordered

    cols = 2 if len(ordered) >= 3 else 1
    rows = (len(ordered) + cols - 1) // cols
    gap = 24
    m = canvas["margin"]
    for i, p in enumerate(ordered):
        x, y, w, h = _grid_slot(i, cols, rows, canvas, m, gap)
        p.setdefault("x", x)
        p.setdefault("y", y)
        p.setdefault("w", w)
        p.setdefault("h", h)
        p.setdefault("kind", "panel")
        p.setdefault("title", p.get("id", ""))

    entities = spec.get("entities") or []
    by_panel = {}
    for e in entities:
        by_panel.setdefault(e.get("panel", "problem"), []).append(e)
    for i, e in enumerate(entities):
        e.setdefault("id", "e%d" % (i + 1))
        e.setdefault("label", e["id"])
        e.setdefault("type", "entity")
        e.setdefault("fill", "#ffffff")
        e.setdefault("stroke", "#1f2937")
        e.setdefault("w", 150)
        e.setdefault("h", 54)
        panel = e.get("panel", "problem")
        p = next((q for q in ordered if q.get("id") == panel), None)
        if p is None:
            p = ordered[0]
        if "x" not in e or "y" not in e:
            n = len(by_panel.get(panel, []))
            idx = next((j for j, q in enumerate(by_panel.get(panel, [])) if q is e), 0)
            col = idx % max(1, int(p.get("w", 300) // (e.get("w", 150) + 20)))
            row = idx // max(1, int(p.get("w", 300) // (e.get("w", 150) + 20)))
            e["x"] = round(p.get("x", m) + 24 + col * (e.get("w", 150) + 20), 1)
            e["y"] = round(p.get("y", m) + 44 + row * (e.get("h", 54) + 24), 1)
            _ = n  # retained for readability
        e.setdefault("source_ref", None)
        e.setdefault("locator", None)
        e.setdefault("quote", None)

    for i, r in enumerate(spec.get("relations") or []):
        r.setdefault("id", "r%d" % (i + 1))
        r.setdefault("kind", "arrow")
        r.setdefault("direction", "forward")
        r.setdefault("label", "")
        r.setdefault("source_ref", None)
        r.setdefault("locator", None)
        r.setdefault("quote", None)

    for i, d in enumerate(spec.get("data") or []):
        d.setdefault("id", "d%d" % (i + 1))
        d.setdefault("channel", "bar")
        d.setdefault("value", None)
        d.setdefault("unit", "")
        d.setdefault("label", d["id"])
        d.setdefault("source_ref", None)
        d.setdefault("locator", None)
        d.setdefault("quote", None)

    for i, lg in enumerate(spec.get("legend") or []):
        lg.setdefault("swatch_id", "lg%d" % (i + 1))
        lg.setdefault("label", lg["swatch_id"])
        lg.setdefault("channel", "color")
        lg.setdefault("source_ref", None)
        lg.setdefault("locator", None)
        lg.setdefault("quote", None)

    spec.setdefault("annotations", [])
    return spec


def _panel_block(p):
    x, y, w, h = p.get("x"), p.get("y"), p.get("w"), p.get("h")
    title = _esc(p.get("title", ""))
    lines = [
        '    <g class="panel" data-id="%s" data-kind="%s">' % (_esc(p.get("id")), _esc(p.get("kind", "panel"))),
        '      <rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="10" fill="#f8fafc" stroke="#94a3b8" stroke-width="1.5"/>' % (x, y, w, h),
        '      <text x="%.1f" y="%.1f" font-family="DejaVu Sans, Arial, sans-serif" font-size="20" font-weight="bold" fill="#0f172a">%s</text>' % (x + 14, y + 28, title),
    ]
    return "\n".join(lines) + "\n    </g>"


def _entity_block(e):
    x, y, w, h = e.get("x"), e.get("y"), e.get("w"), e.get("h")
    label = _esc(e.get("label", ""))
    fill = _esc(e.get("fill", "#ffffff"))
    stroke = _esc(e.get("stroke", "#1f2937"))
    attrs = ' data-source="%s" data-locator="%s"' % (_esc(e.get("source_ref")), _esc(e.get("locator")))
    lines = [
        '    <g class="entity" data-id="%s" data-type="%s"%s>' % (_esc(e.get("id")), _esc(e.get("type", "entity")), attrs),
        '      <rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="6" fill="%s" stroke="%s" stroke-width="2"/>' % (x, y, w, h, fill, stroke),
        '      <text x="%.1f" y="%.1f" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="15" fill="#0f172a">%s</text>' % (x + w / 2, y + h / 2 + 5, label),
    ]
    return "\n".join(lines) + "\n    </g>"


def _arrow_line(e_from, e_to, label, rel_id, attrs, kind):
    if not e_from or not e_to:
        return ""
    x1 = e_from.get("x", 0) + e_from.get("w", 0) / 2
    y1 = e_from.get("y", 0) + e_from.get("h", 0) / 2
    x2 = e_to.get("x", 0) + e_to.get("w", 0) / 2
    y2 = e_to.get("y", 0) + e_to.get("h", 0) / 2
    marker = "" if kind == "undirected" else ' marker-end="url(#arrow)"'
    lines = [
        '    <g class="relation" data-id="%s" data-kind="%s"%s>' % (_esc(rel_id), _esc(kind), attrs),
        '      <line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#2563eb" stroke-width="2.5"%s/>' % (x1, y1, x2, y2, marker),
    ]
    if label:
        lines.append(
            '      <text x="%.1f" y="%.1f" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="13" fill="#1e3a8a">%s</text>'
            % ((x1 + x2) / 2, (y1 + y2) / 2 - 6, _esc(label))
        )
    return "\n".join(lines) + "\n    </g>"


def _data_blocks(data, entities, canvas):
    lines = []
    used = set()
    for i, d in enumerate(data):
        anchor = next((e for e in entities if e.get("id") == d.get("entity_id")), None)
        if anchor:
            bx = anchor.get("x", 0)
            by = anchor.get("y", 0) + anchor.get("h", 54) + 10
        else:
            bx = canvas["width"] - 320 + (i % 3) * 90
            by = canvas["height"] - 160 + (i // 3) * 60
        value = d.get("value")
        numeric = isinstance(value, (int, float))
        bar_w = 30
        bar_h = 0
        if numeric:
            try:
                mx = max(abs(float(x.get("value", 0))) for x in data if isinstance(x.get("value"), (int, float)))
            except ValueError:
                mx = abs(float(value)) or 1.0
            bar_h = max(8.0, 120.0 * abs(float(value)) / (mx or 1.0))
        key = "%s|%s" % (bx, by)
        n = sum(1 for k in used if k.startswith("%s|" % bx))
        used.add(key)
        x = bx + n * (bar_w + 16)
        attrs = ' data-source="%s" data-locator="%s"' % (_esc(d.get("source_ref")), _esc(d.get("locator")))
        lines.append('    <g class="data" data-id="%s" data-channel="%s"%s>' % (_esc(d.get("id")), _esc(d.get("channel", "bar")), attrs))
        if numeric:
            lines.append('      <rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="#3b82f6" stroke="#1e40af" stroke-width="1"/>' % (x, by - bar_h, bar_w, bar_h))
            lines.append('      <text x="%.1f" y="%.1f" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="12" fill="#0f172a">%s%s</text>' % (x + bar_w / 2, by - bar_h - 6, _esc(value), _esc(d.get("unit", ""))))
        else:
            lines.append('      <text x="%.1f" y="%.1f" font-family="DejaVu Sans, Arial, sans-serif" font-size="13" fill="#0f172a">%s</text>' % (x, by, _esc(d.get("label", d.get("id", "")))))
        lines.append("    </g>")
    return "\n".join(lines)


def _legend_blocks(legend, canvas):
    lines = []
    y = 40.0
    x = canvas["width"] - 260.0
    lines.append('    <g class="legend" data-id="legend-frame">')
    lines.append('      <rect x="%.1f" y="%.1f" width="240" height="%d" rx="6" fill="#ffffff" stroke="#cbd5e1" stroke-width="1"/>' % (x, y - 8, 28 + 26 * len(legend)))
    for i, lg in enumerate(legend):
        swatch = lg.get("swatch", "#3b82f6")
        attrs = ' data-source="%s" data-locator="%s"' % (_esc(lg.get("source_ref")), _esc(lg.get("locator")))
        yy = y + i * 26
        lines.append('      <g class="legend-item" data-id="%s"%s>' % (_esc(lg.get("swatch_id")), attrs))
        lines.append('        <rect x="%.1f" y="%.1f" width="16" height="16" fill="%s" stroke="#334155" stroke-width="1"/>' % (x + 12, yy, _esc(swatch)))
        lines.append('        <text x="%.1f" y="%.1f" font-family="DejaVu Sans, Arial, sans-serif" font-size="13" fill="#0f172a">%s</text>' % (x + 36, yy + 13, _esc(lg.get("label", ""))))
        lines.append("      </g>")
    lines.append("    </g>")
    return "\n".join(lines)


def build_svg_text(spec):
    spec = _normalize_spec(spec)
    canvas = spec["canvas"]
    panels = spec["panels"]
    entities = spec["entities"]
    relations = spec["relations"]
    data = spec.get("data") or []
    legend = spec.get("legend") or []

    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (canvas["width"], canvas["height"], canvas["width"], canvas["height"]))
    parts.append('  <defs>')
    parts.append('    <marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto" markerUnits="strokeWidth">')
    parts.append('      <path d="M0,0 L9,3 L0,6 z" fill="#2563eb"/>')
    parts.append('    </marker>')
    parts.append('  </defs>')
    parts.append('  <g id="layer-canvas">')
    parts.append('    <rect width="100%" height="100%" fill="#ffffff"/>')
    parts.append('  </g>')
    parts.append('  <g id="layer-panels">')
    parts.extend(_panel_block(p) for p in panels)
    parts.append('  </g>')
    parts.append('  <g id="layer-entities">')
    parts.extend(_entity_block(e) for e in entities)
    parts.append('  </g>')
    parts.append('  <g id="layer-relations">')
    emap = {e.get("id"): e for e in entities}
    for r in relations:
        attrs = ' data-source="%s" data-locator="%s"' % (_esc(r.get("source_ref")), _esc(r.get("locator")))
        parts.append(_arrow_line(emap.get(r.get("from")), emap.get(r.get("to")), r.get("label", ""), r.get("id"), attrs, r.get("kind", "arrow")))
    parts.append('  </g>')
    parts.append('  <g id="layer-data">')
    parts.append(_data_blocks(data, entities, canvas))
    parts.append('  </g>')
    parts.append('  <g id="layer-legend">')
    parts.append(_legend_blocks(legend, canvas))
    parts.append('  </g>')
    parts.append('  <g id="layer-annotations">')
    for i, a in enumerate(spec.get("annotations") or []):
        parts.append('    <text x="%s" y="%s" font-family="DejaVu Sans, Arial, sans-serif" font-size="11" fill="#64748b">%s</text>' % (_esc(a.get("x", 40)), _esc(a.get("y", canvas["height"] - 16)), _esc(a.get("text", ""))))
    parts.append('  </g>')
    parts.append('</svg>')
    return "\n".join(parts)


def build_svg(spec, out_path):
    svg_text = build_svg_text(spec)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(svg_text)
    return svg_text


def write_manifest(spec, svg_path, manifest_path):
    manifest = {
        "svg": os.path.basename(svg_path),
        "canvas": spec.get("canvas", {}),
        "layers": ["layer-canvas", "layer-panels", "layer-entities", "layer-relations",
                   "layer-data", "layer-legend", "layer-annotations"],
        "text_editable": True,
        "counts": {
            "panels": len(spec.get("panels", [])),
            "entities": len(spec.get("entities", [])),
            "relations": len(spec.get("relations", [])),
            "data": len(spec.get("data", [])),
            "legend": len(spec.get("legend", [])),
        },
        "deterministic": True,
    }
    save_json(manifest_path, manifest)
    return manifest


def attempt_render(svg_path, out_dir, case_id):
    """Honest export-status probe. stdlib cannot rasterize SVG, so we record
    `preview_not_rendered` unless a real renderer is later attached by the agent.
    """
    exports = {}
    for fmt in ("png", "pdf"):
        exports[fmt] = {"path": None, "status": "preview_not_rendered", "renderer": None,
                        "note": "stdlib cannot rasterize SVG; use platform pdf-render-tool/browser-automation, then update result.json exports."}
    # A renderer may have been invoked separately by the agent; keep any file it produced.
    for fmt in ("png", "pdf"):
        cand = os.path.join(out_dir, "fig_%s.%s" % (case_id, fmt))
        if os.path.isfile(cand) and os.path.getsize(cand) > 0:
            exports[fmt]["path"] = os.path.basename(cand)
            exports[fmt]["status"] = "rendered"
            exports[fmt]["renderer"] = "external"
    return exports


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build layered editable SVG from figure_spec.json")
    ap.add_argument("--spec", default="/app/work/figure_spec.json")
    ap.add_argument("--out", default="/app/output")
    ap.add_argument("--case-id", default=None)
    args = ap.parse_args(argv)

    spec = load_json(args.spec) if os.path.isfile(args.spec) else {}
    case_id = args.case_id or spec.get("case_id") or "case"
    os.makedirs(args.out, exist_ok=True)
    svg_path = os.path.join(args.out, "fig_%s.svg" % case_id)
    svg_text = build_svg(spec, svg_path)
    manifest = write_manifest(spec, svg_path, os.path.join(args.out, "fig_manifest.json"))
    exports = attempt_render(svg_path, args.out, case_id)
    report = {"svg": svg_path, "manifest": manifest, "exports": exports, "svg_bytes": len(svg_text.encode("utf-8"))}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
