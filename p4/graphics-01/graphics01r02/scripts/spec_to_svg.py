#!/usr/bin/env python3
"""Deterministic layered-SVG renderer for editable relationship schematics.

Reads a figure-spec JSON (schema documented in references/figure-grammar.md)
and writes an SVG with:
  * named <g> layers (background, groups, edges, nodes, legend, annotations,
    chart), so layers can be shown/hidden in editors;
  * editable <text> elements (labels split on "\\n");
  * data-source-ref / data-quote attributes on every node/edge/legend item and
    annotation, so every drawn element traces back to the source material.

Standard library only. Rendering is fully deterministic for the same spec.
"""
import argparse
import html
import json
import math
import os
import sys

SCHEMA = "editable_relationship_schematic/v1"

# Reusable color rules by node kind (fill, stroke). Explicit values in the spec
# always win. These are documentation-grade defaults, not scientific claims.
PALETTE = {
    "protein":   ("#dbeafe", "#1e3a8a"),
    "gene":      ("#fce7f3", "#9d174d"),
    "rna":       ("#fef3c7", "#92400e"),
    "process":   ("#d1fae5", "#065f46"),
    "compound":  ("#fef9c3", "#854d0e"),
    "drug":      ("#fee2e2", "#991b1b"),
    "cell":      ("#fee2e2", "#991b1b"),
    "complex":   ("#e9d5ff", "#6b21a8"),
    "condition": ("#e0e7ff", "#3730a3"),
    "structure": ("#e2e8f0", "#334155"),
    "unknown":   ("#f1f5f9", "#475569"),
}

SHAPES = {"rect", "rounded", "ellipse", "pill", "hexagon"}

# edge kind -> arrow head style. "tee" = inhibition bar, "dot" = binding,
# "none" = plain line.
EDGE_HEAD = {
    "activation": "arrow",
    "phosphorylation": "arrow",
    "activation_of": "arrow",
    "inhibition": "tee",
    "repression": "tee",
    "binding": "dot",
    "interaction": "none",
    "association": "dot",
    "dissociation": "none",
    "contains": "none",
    "parallel": "none",
    "upstream": "arrow",
    "downstream": "arrow",
    "causal": "arrow",
    "translocation": "arrow",
    "transport": "arrow",
    "flow": "arrow",
    "temporal": "arrow",
    "phosphorylates": "arrow",
}

# fallback font stack; replaced at export time if journal requires embedding
FONT = "Arial, Helvetica, sans-serif"


def esc(s):
    if s is None:
        return ""
    return html.escape(str(s), quote=True)


def _num(v, default):
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def node_rect(n):
    w = _num(n.get("w"), 132)
    h = _num(n.get("h"), 48)
    x = _num(n.get("x"), None)
    y = _num(n.get("y"), None)
    return x, y, w, h


def _center(x, y, w, h):
    return x + w / 2.0, y + h / 2.0


def layout_nodes(nodes, edges, groups):
    """Deterministic auto-layout via longest-path layer assignment.

    Nodes with explicit x/y keep them. Others get a layer from the DAG of
    edges (cycle edges are ignored for layering only), then are stacked within
    their layer. Groups without explicit bounds are auto-fitted around members.
    """
    explicit = all(_num(n.get("x"), None) is not None and
                   _num(n.get("y"), None) is not None for n in nodes)
    if explicit:
        for n in nodes:
            x, y, w, h = node_rect(n)
            n["_x"], n["_y"], n["_w"], n["_h"] = x, y, w, h
        return

    idx = {n["id"]: i for i, n in enumerate(nodes)}
    layer = {}
    indeg = {n["id"]: 0 for n in nodes}
    adj = {n["id"]: [] for n in nodes}
    for e in edges:
        f, t = e.get("from"), e.get("to")
        if f in idx and t in idx and f != t:
            adj[f].append(t)
            indeg[t] += 1
    # Kahn order (cycle-safe: nodes left over get appended in spec order)
    from collections import deque
    q = deque([n["id"] for n in nodes if indeg[n["id"]] == 0])
    order = []
    while q:
        u = q.popleft()
        order.append(u)
        for v in adj[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v)
    seen = set(order)
    for n in nodes:
        if n["id"] not in seen:
            order.append(n["id"])

    # longest path from zero-indegree sources; nodes not reached get layer 0
    for nid in order:
        layer[nid] = max([layer[e.get("from")] + 1 for e in edges
                          if e.get("to") == nid and e.get("from") in layer] or [0])

    col_x = 60.0
    row_h = 96.0
    col_w = 220.0
    counters = {}
    for nid in order:
        n = nodes[idx[nid]]
        w, h = node_rect(n)[2:]
        L = layer.get(nid, 0)
        r = counters.get(L, 0)
        counters[L] = r + 1
        n["_x"], n["_y"] = col_x + L * col_w, 90.0 + r * row_h
        n["_w"], n["_h"] = w, h

    # auto-fit groups around members (containment is drawn from membership)
    for g in groups:
        members = [nodes[idx[mid]] for mid in g.get("nodes", []) if mid in idx]
        if not members:
            g["_x"], g["_y"], g["_w"], g["_h"] = 60, 60, 200, 90
            continue
        if all(_num(g.get(k), None) is not None for k in ("x", "y", "w", "h")):
            g["_x"], g["_y"], g["_w"], g["_h"] = (
                _num(g["x"], 0), _num(g["y"], 0), _num(g["w"], 0), _num(g["h"], 0))
            continue
        xs = [m["_x"] for m in members]
        ys = [m["_y"] for m in members]
        x1 = min(xs) - 22
        y1 = min(ys) - 44
        x2 = max(m["_x"] + m["_w"] for m in members) + 22
        y2 = max(m["_y"] + m["_h"] for m in members) + 22
        g["_x"], g["_y"], g["_w"], g["_h"] = x1, y1, x2 - x1, y2 - y1


def _edge_endpoints(e, nodes):
    """Return (x1,y1,x2,y2,ux,uy) clipped to node boundaries."""
    f = next((n for n in nodes if n["id"] == e.get("from")), None)
    t = next((n for n in nodes if n["id"] == e.get("to")), None)
    if not f or not t:
        return None
    cx1, cy1 = _center(f["_x"], f["_y"], f["_w"], f["_h"])
    cx2, cy2 = _center(t["_x"], t["_y"], t["_w"], t["_h"])
    dx, dy = cx2 - cx1, cy2 - cy1
    L = math.hypot(dx, dy) or 1.0
    ux, uy = dx / L, dy / L
    # clip along direction against each rect (half-extent projection)
    def clip(cx, cy, w, h):
        hx, hy = w / 2.0, h / 2.0
        tx = hx / abs(ux) if abs(ux) > 1e-9 else float("inf")
        ty = hy / abs(uy) if abs(uy) > 1e-9 else float("inf")
        d = min(tx, ty)
        return cx + ux * d, cy + uy * d
    x1, y1 = clip(cx1, cy1, f["_w"], f["_h"])
    x2, y2 = clip(cx2, cy2, t["_w"], t["_h"])
    # arrow side: clip target from the other direction
    x2i, y2i = cx2 - ux * (t["_w"] / 2.0 + 6.0), cy2 - uy * (t["_h"] / 2.0 + 6.0)
    return x1, y1, x2, y2, x2i, y2i, ux, uy


def _arrow_head(x, y, ux, uy, color, head, sw):
    """Return SVG fragment for the head at tip (x,y) pointing along (ux,uy)."""
    per = (-uy, ux)
    L = 14.0
    W = 7.0
    if head == "tee":
        bx, by = x - ux * 2.0, y - uy * 2.0
        p1 = (bx + per[0] * 7.0, by + per[1] * 7.0)
        p2 = (bx - per[0] * 7.0, by - per[1] * 7.0)
        return (f'<line x1="{p1[0]:.2f}" y1="{p1[1]:.2f}" x2="{p2[0]:.2f}" '
                f'y2="{p2[1]:.2f}" stroke="{esc(color)}" stroke-width="{sw}"/>')
    if head == "dot":
        return (f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{5.0:.2f}" '
                f'fill="{esc(color)}"/>')
    base_x, base_y = x - ux * L, y - uy * L
    c1 = (base_x + per[0] * W, base_y + per[1] * W)
    c2 = (base_x - per[0] * W, base_y - per[1] * W)
    return (f'<polygon points="{x:.2f},{y:.2f} {c1[0]:.2f},{c1[1]:.2f} '
            f'{c2[0]:.2f},{c2[1]:.2f}" fill="{esc(color)}"/>')


def _shape_element(n, x, y, w, h, fill, stroke):
    shape = n.get("shape", "rounded")
    if shape not in SHAPES:
        shape = "rounded"
    if shape == "ellipse":
        return (f'<ellipse cx="{x + w/2:.2f}" cy="{y + h/2:.2f}" rx="{w/2:.2f}" '
                f'ry="{h/2:.2f}" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>')
    if shape == "pill":
        r = min(w, h) / 2.0
        return (f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
                f'rx="{r:.2f}" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>')
    if shape == "hexagon":
        pts = []
        for k in range(6):
            a = math.radians(60 * k - 30)
            pts.append(f"{x + w/2 + w/2*math.cos(a):.2f},{y + h/2 + h/2*math.sin(a):.2f}")
        return (f'<polygon points="{" ".join(pts)}" fill="{fill}" '
                f'stroke="{stroke}" stroke-width="1.5"/>')
    rx = 8.0 if shape == "rounded" else 0.0
    return (f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
            f'rx="{rx:.2f}" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>')


def _text_element(x, y, text, size, color, anchor="middle", bold=False):
    lines = str(text).split("\n")
    dy = float(size) * 1.25
    spans = []
    weight = ' font-weight="bold"' if bold else ""
    for i, ln in enumerate(lines):
        spans.append(f'<tspan x="{x:.2f}" dy="{dy if i else 0:.2f}">{esc(ln)}</tspan>')
    return (f'<text x="{x:.2f}" y="{y:.2f}" text-anchor="{anchor}" '
            f'font-family="{FONT}" font-size="{size}" fill="{color}"{weight}>'
            f'{"".join(spans)}</text>')


def render(spec):
    canvas = spec.get("canvas", {})
    nodes = list(spec.get("nodes", []))
    edges = list(spec.get("edges", []))
    groups = list(spec.get("groups", []))
    legend = list(spec.get("legend", []))
    annots = list(spec.get("annotations", []))
    chart = spec.get("chart")

    # give nodes a stable copy for geometry
    for n in nodes:
        n = dict(n) if not isinstance(n, dict) else n
    nodes = [dict(n) for n in nodes]
    layout_nodes(nodes, edges, groups)

    W = _num(canvas.get("width"), 1200)
    H = _num(canvas.get("height"), 900)
    bg = canvas.get("background", "#ffffff")
    title = canvas.get("title", "")

    parts = []
    parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
                 f'viewBox="0 0 {W} {H}" data-schema="{SCHEMA}">')
    if canvas.get("description"):
        parts.append(f'<desc>{esc(canvas["description"])}</desc>')

    parts.append(f'<g id="background"><rect width="{W}" height="{H}" fill="{esc(bg)}"/>'
                 f'</g>')

    # groups (behind edges/nodes)
    parts.append('<g id="groups">')
    for g in groups:
        fill = g.get("fill", "#f8fafc")
        stroke = g.get("stroke", "#94a3b8")
        attrs = f'data-source-ref="{esc(g.get("source_ref", ""))}" data-quote="{esc(g.get("quote", ""))}"'
        parts.append(f'<g id="{esc(g.get("id", "group"))}" {attrs}>')
        parts.append(f'<rect x="{g["_x"]:.2f}" y="{g["_y"]:.2f}" width="{g["_w"]:.2f}" '
                     f'height="{g["_h"]:.2f}" rx="12" fill="{fill}" stroke="{stroke}" '
                     f'stroke-width="1.2" stroke-dasharray="6,4"/>')
        lbl = g.get("label", g.get("id", ""))
        if lbl:
            parts.append(_text_element(g["_x"] + 12, g["_y"] + 20, lbl, 13, "#334155",
                                       "start", bold=True))
        parts.append('</g>')
    parts.append('</g>')

    # edges
    parts.append('<g id="edges">')
    for e in edges:
        ep = _edge_endpoints(e, nodes)
        if not ep:
            continue
        x1, y1, x2, y2, x2i, y2i, ux, uy = ep
        color = e.get("color", "#334155")
        style = "stroke-dasharray=\"6,4\" " if e.get("style") == "dashed" else ""
        head = EDGE_HEAD.get(e.get("kind", "interaction"), "arrow")
        sw = 2.0
        attrs = f'data-source-ref="{esc(e.get("source_ref", ""))}" data-quote="{esc(e.get("quote", ""))}"'
        line_end = (x2i, y2i) if head in ("arrow", "tee", "dot") else (x2, y2)
        parts.append(f'<g id="{esc(e.get("id", "edge"))}" {attrs}>')
        parts.append(f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{line_end[0]:.2f}" '
                     f'y2="{line_end[1]:.2f}" stroke="{esc(color)}" stroke-width="{sw}" {style}/>')
        if head in ("arrow", "tee", "dot"):
            parts.append(_arrow_head(x2, y2, ux, uy, color, head, sw))
        lbl = e.get("label", "")
        if lbl:
            mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            parts.append(_text_element(mx, my - 6, lbl, 11, "#111827"))
        parts.append('</g>')
    parts.append('</g>')

    # nodes
    parts.append('<g id="nodes">')
    for n in nodes:
        kind = n.get("kind", "unknown")
        fill = n.get("fill", PALETTE.get(kind, PALETTE["unknown"])[0])
        stroke = n.get("stroke", PALETTE.get(kind, PALETTE["unknown"])[1])
        x, y, w, h = n["_x"], n["_y"], n["_w"], n["_h"]
        attrs = f'data-source-ref="{esc(n.get("source_ref", ""))}" data-quote="{esc(n.get("quote", ""))}"'
        parts.append(f'<g id="{esc(n.get("id", "node"))}" {attrs}>')
        parts.append(_shape_element(n, x, y, w, h, fill, stroke))
        tc = n.get("text_color", "#111827")
        fs = _num(n.get("font_size"), 13)
        parts.append(_text_element(x + w / 2, y + h / 2 - fs * 0.35, n.get("label", ""),
                                   fs, tc))
        sub = n.get("sublabel")
        if sub:
            parts.append(_text_element(x + w / 2, y + h / 2 + fs * 0.75, sub,
                                       fs - 2, "#475569"))
        parts.append('</g>')
    parts.append('</g>')

    # legend (explicit items + auto-derived chart series)
    parts.append('<g id="legend">')
    legend_items = []
    for item in legend:
        legend_items.append(dict(item))
    if chart:
        series = chart.get("series", [])
        for i, s in enumerate(series):
            name = s.get("name", f"series{i}")
            color = s.get("color", chart_series_color(i))
            if not any(li.get("label") == name for li in legend_items):
                legend_items.append({"label": name, "marker": "swatch",
                                     "color": color,
                                     "source_ref": chart.get("source_ref", ""),
                                     "quote": ""})
    lx, ly = W - 260.0, 24.0
    parts.append(f'<rect x="{lx:.2f}" y="{ly:.2f}" width="240" height="{18 + 24*len(legend_items):.2f}" '
                 f'fill="#ffffff" stroke="#cbd5e1" rx="6"/>')
    for i, item in enumerate(legend_items):
        yy = ly + 24.0 + i * 24.0
        color = item.get("color", "#334155")
        attrs = f'data-source-ref="{esc(item.get("source_ref", ""))}" data-quote="{esc(item.get("quote", ""))}"'
        parts.append(f'<g id="{esc(item.get("id", f"legend_{i}"))}" {attrs}>')
        marker = item.get("marker", "swatch")
        if marker in ("arrow", "activation"):
            parts.append(f'<line x1="{lx+10:.2f}" y1="{yy-3:.2f}" x2="{lx+34:.2f}" '
                         f'y2="{yy-3:.2f}" stroke="{esc(color)}" stroke-width="2"/>')
            parts.append(_arrow_head(lx + 34, yy - 3, 1, 0, color, "arrow", 2))
        elif marker in ("tee", "inhibition"):
            parts.append(f'<line x1="{lx+10:.2f}" y1="{yy-3:.2f}" x2="{lx+34:.2f}" '
                         f'y2="{yy-3:.2f}" stroke="{esc(color)}" stroke-width="2"/>')
            parts.append(_arrow_head(lx + 34, yy - 3, 1, 0, color, "tee", 2))
        else:
            parts.append(f'<rect x="{lx+12:.2f}" y="{yy-8:.2f}" width="18" height="12" '
                         f'fill="{esc(color)}" stroke="#475569" stroke-width="0.8"/>')
        parts.append(_text_element(lx + 44, yy, item.get("label", ""), 12, "#111827", "start"))
        parts.append('</g>')
    parts.append('</g>')

    # annotations
    parts.append('<g id="annotations">')
    for a in annots:
        x = _num(a.get("x"), 20)
        y = _num(a.get("y"), 20)
        fs = _num(a.get("font_size"), 12)
        anchor = a.get("anchor", "start")
        color = a.get("color", "#111827")
        attrs = f'data-source-ref="{esc(a.get("source_ref", ""))}" data-quote="{esc(a.get("quote", ""))}"'
        parts.append(f'<g id="{esc(a.get("id", f"annot_{len(parts)}"))}" {attrs}>')
        parts.append(_text_element(x, y, a.get("text", ""), fs, color, anchor))
        parts.append('</g>')
    parts.append('</g>')

    # chart panel
    if chart:
        parts.append(render_chart(chart, W, H))

    if title:
        parts.append(_text_element(W / 2, 24, title, 15, "#0f172a", "middle", bold=True))

    parts.append('</svg>')
    return "\n".join(parts)


def chart_series_color(i):
    cols = ["#2563eb", "#dc2626", "#16a34a", "#d97706", "#7c3aed", "#0891b2"]
    return cols[i % len(cols)]


def render_chart(chart, W, H):
    """Minimal deterministic bar/dot chart from provided data.

    This only plots values the caller already provided; it performs no
    statistical computation. Legend items are derived from the same series
    list, so legend and data stay in one source.
    """
    ctype = chart.get("type", "bar")
    categories = chart.get("categories", [])
    series = chart.get("series", [])
    x_label = chart.get("x_label", "")
    y_label = chart.get("y_label", "")
    unit = chart.get("unit", "")
    title = chart.get("title", "")
    # panel geometry: bottom-right area
    pw, ph = 560.0, 260.0
    px, py = W - pw - 20.0, H - ph - 20.0
    parts = []
    parts.append(f'<g id="chart" data-source-ref="{esc(chart.get("source_ref", ""))}" '
                 f'data-quote="{esc(chart.get("quote", ""))}">')
    parts.append(f'<rect x="{px:.2f}" y="{py:.2f}" width="{pw:.2f}" height="{ph:.2f}" '
                 f'fill="#ffffff" stroke="#cbd5e1" rx="8"/>')
    if title:
        parts.append(_text_element(px + pw / 2, py + 18, title, 13, "#0f172a", "middle", True))
    plot_x, plot_y = px + 70.0, py + 40.0
    plot_w, plot_h = pw - 90.0, ph - 90.0
    # axes
    parts.append(f'<line x1="{plot_x:.2f}" y1="{plot_y:.2f}" x2="{plot_x:.2f}" '
                 f'y2="{plot_y + plot_h:.2f}" stroke="#334155" stroke-width="1.2"/>')
    parts.append(f'<line x1="{plot_x:.2f}" y1="{plot_y + plot_h:.2f}" x2="{plot_x + plot_w:.2f}" '
                 f'y2="{plot_y + plot_h:.2f}" stroke="#334155" stroke-width="1.2"/>')
    ncat = max(len(categories), 1)
    nser = max(len(series), 1)
    if ctype == "bar":
        group_w = plot_w / ncat
        bar_w = group_w * 0.8 / nser
        for ci, cat in enumerate(categories):
            cx = plot_x + group_w * ci + group_w * 0.1
            for si, s in enumerate(series):
                vals = s.get("values", [])
                v = _num(vals[ci] if ci < len(vals) else None, 0)
                color = s.get("color", chart_series_color(si))
                bx = cx + bar_w * si
                bh = plot_h * min(max(v / 100.0, 0.0), 1.0) if s.get("percent", True) else 0
                if not s.get("percent", True):
                    # non-percent: scale by max of all values
                    allv = [abs(_num(vv, 0)) for ss in series for vv in ss.get("values", [])]
                    vmax = max(allv) or 1.0
                    bh = plot_h * min(max(v / vmax, 0.0), 1.0)
                parts.append(f'<rect x="{bx:.2f}" y="{plot_y + plot_h - bh:.2f}" '
                             f'width="{bar_w:.2f}" height="{bh:.2f}" fill="{esc(color)}"/>')
            parts.append(_text_element(plot_x + group_w * ci + group_w / 2, plot_y + plot_h + 14,
                                       cat, 10, "#334155"))
    else:  # dot / point
        for si, s in enumerate(series):
            vals = s.get("values", [])
            color = s.get("color", chart_series_color(si))
            for ci in range(min(len(vals), ncat)):
                v = _num(vals[ci], 0)
                x = plot_x + plot_w * (ci + 0.5) / ncat
                y = plot_y + plot_h - plot_h * min(max(v / 100.0, 0.0), 1.0)
                parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3.5" fill="{esc(color)}"/>')
    if x_label:
        parts.append(_text_element(plot_x + plot_w / 2, plot_y + plot_h + 30, x_label, 11, "#334155"))
    if y_label:
        parts.append(_text_element(plot_x - 8, plot_y - 14, f"{y_label} ({unit})" if unit else y_label,
                                   11, "#334155", "start"))
    parts.append('</g>')
    return "\n".join(parts)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Render a figure spec to layered SVG")
    ap.add_argument("--spec", required=True, help="figure_spec.json path")
    ap.add_argument("--out", required=True, help="output .svg path")
    a = ap.parse_args(argv)
    with open(a.spec, "r", encoding="utf-8-sig") as f:
        spec = json.load(f)
    svg = render(spec)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(svg)
    print(json.dumps({"ok": True, "out": a.out, "bytes": len(svg.encode("utf-8"))},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
