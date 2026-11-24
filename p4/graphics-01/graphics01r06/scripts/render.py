#!/usr/bin/env python3
"""figure_model.json -> layered editable SVG + vector PDF (+ PNG via pdftoppm).

Pure standard library. Deterministic: same model yields byte-identical SVG/PDF.
This file is the core implementation (core_impl); main.py is a thin CLI wrapper.

Usage:
    python render.py --model /app/work/figure_model.json \
                     --output /app/output [--work /app/work] [--dpi 300]
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import xml.sax.saxutils as sax

SCHEMA_VERSION = "1.0"

# ---------------------------------------------------------------- utilities

def _esc(s):
    return sax.escape("" if s is None else str(s))


def _parse_color(c, default="#333333"):
    if not c:
        return default
    c = str(c).strip()
    if c.startswith("#"):
        h = c[1:]
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        if len(h) == 6:
            try:
                return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
            except ValueError:
                return (0.2, 0.2, 0.2)
    return (0.2, 0.2, 0.2)


def _hex_color(c, default="#333333"):
    if not c:
        return default
    c = str(c).strip()
    if c.startswith("#") and len(c) in (4, 7):
        h = c[1:]
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        return "#" + h.lower()
    return default


def _text_w(s, fs):
    """Estimate rendered text width in px."""
    w = 0.0
    for ch in (s or ""):
        w += fs if ord(ch) > 127 else 0.58 * fs
    return w


def _fmt(v, nd=2):
    return f"{v:.{nd}f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------- layout

def _node_dims(e, fs_default):
    fs = float((e.get("style") or {}).get("font_size") or fs_default)
    label = e.get("label") or ""
    w = max(64.0, _text_w(label, fs) + 30.0)
    h = fs + 26.0
    return w, h, fs


def _auto_layout(model):
    """Deterministic level-based layout. Returns {id: (cx, cy, w, h)}."""
    ents = model.get("entities", [])
    rels = model.get("relations", [])
    by_id = {e["id"]: e for e in ents}
    fs_default = 22
    pos = {}
    if not ents:
        return pos

    # explicit absolute coordinates (all entities)
    if all(isinstance((e.get("layout") or {}).get("x"), (int, float)) and
           isinstance((e.get("layout") or {}).get("y"), (int, float)) for e in ents):
        for e in ents:
            w, h, fs = _node_dims(e, fs_default)
            pos[e["id"]] = (float(e["layout"]["x"]), float(e["layout"]["y"]), w, h)
        return pos

    # explicit grid coordinates (all entities)
    if all(isinstance((e.get("layout") or {}).get("row"), int) and
           isinstance((e.get("layout") or {}).get("col"), int) for e in ents):
        sizes = {e["id"]: _node_dims(e, fs_default) for e in ents}
        col_w = max(v[0] for v in sizes.values()) + 110.0
        row_h = max(v[1] for v in sizes.values()) + 80.0
        for e in ents:
            c = e["layout"]["col"]
            r = e["layout"]["row"]
            w, h, fs = sizes[e["id"]]
            pos[e["id"]] = (120.0 + c * col_w + w / 2, 120.0 + r * row_h + h / 2, w, h)
        return pos

    # level-based auto layout
    indeg = {eid: 0 for eid in by_id}
    adj = {eid: [] for eid in by_id}
    for r in rels:
        a, b = r.get("from"), r.get("to")
        if a in by_id and b in by_id and r.get("kind") != "containment" and r.get("directed", True):
            adj[a].append(b)
            indeg[b] += 1
    from collections import deque
    q = deque(sorted(eid for eid in by_id if indeg[eid] == 0))
    level = {}
    lvl = 0
    while q:
        nxt = deque()
        while q:
            k = q.popleft()
            if k in level:
                continue
            level[k] = lvl
            for nb in sorted(adj[k]):
                indeg[nb] -= 1
                if indeg[nb] <= 0 and nb not in level:
                    nxt.append(nb)
        q = nxt
        lvl += 1
    for eid in sorted(by_id):
        if eid not in level:
            level[eid] = lvl

    buckets = {}
    for e in ents:
        buckets.setdefault(level[e["id"]], []).append(e)
    for lv in buckets:
        buckets[lv].sort(key=lambda e: (e.get("group") or "", e["id"]))

    sizes = {e["id"]: _node_dims(e, fs_default) for e in ents}
    col_w = max(max(v[0] for v in sizes.values()), 64) + 120.0
    row_h = max(max(v[1] for v in sizes.values()), 40) + 84.0
    for lv in sorted(buckets):
        for idx, e in enumerate(buckets[lv]):
            w, h, fs = sizes[e["id"]]
            cx = 120.0 + lv * col_w + w / 2
            cy = 120.0 + idx * row_h + h / 2
            pos[e["id"]] = (cx, cy, w, h)
    return pos


def _group_boxes(model, pos):
    groups = model.get("groups", [])
    by_id = {e["id"]: e for e in model.get("entities", [])}
    out = []
    for g in groups:
        mem = [m for m in g.get("contains", []) if m in pos]
        if not mem:
            continue
        xs = [pos[m][0] - pos[m][2] / 2 for m in mem]
        ys = [pos[m][1] - pos[m][3] / 2 for m in mem]
        xe = [pos[m][0] + pos[m][2] / 2 for m in mem]
        ye = [pos[m][1] + pos[m][3] / 2 for m in mem]
        pad = 40.0
        out.append({"id": g["id"], "label": g.get("label", ""),
                    "x": min(xs) - pad, "y": min(ys) - pad,
                    "w": max(xe) - min(xs) + 2 * pad,
                    "h": max(ye) - min(ys) + 2 * pad})
    return out


# ---------------------------------------------------------------- SVG

def _shape_svg(e, cx, cy, w, h, style):
    shape = style.get("shape", "box")
    fill = _hex_color(style.get("fill"), "#F4F6F8")
    stroke = _hex_color(style.get("stroke"), "#333333")
    lw = float(style.get("stroke_width") or 1.6)
    x = cx - w / 2
    y = cy - h / 2
    parts = []
    if shape == "ellipse" or shape == "circle":
        rx, ry = (w / 2, h / 2) if shape == "circle" else (w / 2, h / 2)
        parts.append(f'<ellipse cx="{_fmt(cx)}" cy="{_fmt(cy)}" rx="{_fmt(rx)}" ry="{_fmt(ry)}" fill="{fill}" stroke="{stroke}" stroke-width="{_fmt(lw)}"/>')
    elif shape == "rounded":
        r = min(14.0, w / 4, h / 4)
        parts.append(f'<rect x="{_fmt(x)}" y="{_fmt(y)}" width="{_fmt(w)}" height="{_fmt(h)}" rx="{_fmt(r)}" fill="{fill}" stroke="{stroke}" stroke-width="{_fmt(lw)}"/>')
    elif shape == "hexagon":
        pts = []
        for i in range(6):
            ang = math.pi / 6 + i * math.pi / 3
            pts.append(f"{_fmt(cx + w/2*math.cos(ang))},{_fmt(cy + h/2*math.sin(ang))}")
        parts.append(f'<polygon points="{" ".join(pts)}" fill="{fill}" stroke="{stroke}" stroke-width="{_fmt(lw)}"/>')
    elif shape == "plain":
        pass
    else:  # box
        parts.append(f'<rect x="{_fmt(x)}" y="{_fmt(y)}" width="{_fmt(w)}" height="{_fmt(h)}" fill="{fill}" stroke="{stroke}" stroke-width="{_fmt(lw)}"/>')
    return "\n".join(parts)


def _relation_svg(r, p_from, p_to, e_from, e_to, idx):
    kind = r.get("kind", "activation")
    color = _hex_color((r.get("style") or {}).get("color"), "#37474F")
    lw = float((r.get("style") or {}).get("width") or 2.0)
    dashed = (r.get("style") or {}).get("line") == "dashed" or kind == "mapping"
    stroke_dash = ' stroke-dasharray="10,8"' if dashed else ""
    # trim to node boundary
    hw = e_from[2] / 2 + 4
    hh = e_from[3] / 2 + 4
    p1 = _trim(p_from, p_to, hw, hh)
    hw2 = e_to[2] / 2 + 4
    hh2 = e_to[3] / 2 + 4
    p2 = _trim(p_to, p_from, hw2, hh2)
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    L = math.hypot(dx, dy) or 1
    ux, uy = dx / L, dy / L
    px, py = -uy, ux
    off = 0.0
    if idx > 0:
        off = (idx * 2 - 1) * 10.0
    p1 = (p1[0] + px * off, p1[1] + py * off)
    p2 = (p2[0] + px * off, p2[1] + py * off)
    head_len = 14.0
    head_w = 7.0
    parts = []
    # main line: back off at head end for arrow types
    if kind in ("activation", "causal", "upstream_downstream", "temporal"):
        bx = p2[0] - ux * head_len
        by = p2[1] - uy * head_len
        parts.append(f'<line x1="{_fmt(p1[0])}" y1="{_fmt(p1[1])}" x2="{_fmt(bx)}" y2="{_fmt(by)}" stroke="{color}" stroke-width="{_fmt(lw)}"{stroke_dash}/>')
        hx1 = bx + px * head_w
        hy1 = by + py * head_w
        hx2 = bx - px * head_w
        hy2 = by - py * head_w
        parts.append(f'<polygon points="{_fmt(p2[0])},{_fmt(p2[1])} {_fmt(hx1)},{_fmt(hy1)} {_fmt(hx2)},{_fmt(hy2)}" fill="{color}"/>')
    elif kind == "inhibition":
        parts.append(f'<line x1="{_fmt(p1[0])}" y1="{_fmt(p1[1])}" x2="{_fmt(p2[0])}" y2="{_fmt(p2[1])}" stroke="{color}" stroke-width="{_fmt(lw)}"{stroke_dash}/>')
        tx1 = p2[0] + px * 9
        ty1 = p2[1] + py * 9
        tx2 = p2[0] - px * 9
        ty2 = p2[1] - py * 9
        parts.append(f'<line x1="{_fmt(tx1)}" y1="{_fmt(ty1)}" x2="{_fmt(tx2)}" y2="{_fmt(ty2)}" stroke="{color}" stroke-width="{_fmt(lw + 1.5)}"/>')
    elif kind == "binding":
        parts.append(f'<line x1="{_fmt(p1[0])}" y1="{_fmt(p1[1])}" x2="{_fmt(p2[0])}" y2="{_fmt(p2[1])}" stroke="{color}" stroke-width="{_fmt(lw)}"{stroke_dash}/>')
        for pt in (p1, p2):
            parts.append(f'<circle cx="{_fmt(pt[0])}" cy="{_fmt(pt[1])}" r="4" fill="{color}"/>')
    else:  # parallel, mapping, default
        parts.append(f'<line x1="{_fmt(p1[0])}" y1="{_fmt(p1[1])}" x2="{_fmt(p2[0])}" y2="{_fmt(p2[1])}" stroke="{color}" stroke-width="{_fmt(lw)}"{stroke_dash}/>')
        if kind == "mapping":
            parts.append(f'<circle cx="{_fmt(p2[0])}" cy="{_fmt(p2[1])}" r="4" fill="{color}"/>')
    return "\n".join(parts), ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)


def _trim(p_from, p_to, hw, hh):
    x1, y1 = p_from
    x2, y2 = p_to
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy) or 1
    ux, uy = dx / L, dy / L
    tx = hw / abs(ux) if abs(ux) > 1e-9 else 1e9
    ty = hh / abs(uy) if abs(uy) > 1e-9 else 1e9
    t = min(tx, ty)
    return (x1 + ux * t, y1 + uy * t)


def _svg_text(label, cx, cy, fs, fill="#1A1A1A", anchor="middle", bold=False):
    weight = ' font-weight="bold"' if bold else ""
    return (f'<text x="{_fmt(cx)}" y="{_fmt(cy + fs * 0.32)}" '
            f'font-family="{_font_family()}" font-size="{_fmt(fs)}"{weight} '
            f'fill="{fill}" text-anchor="{anchor}">{_esc(label)}</text>')


def _font_family():
    return "Arial, 'Helvetica Neue', 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif"


def _build_schematic_layers(model):
    """Build the inner <g> layer content (no outer <svg> wrapper)."""
    fig = model.get("figure", {})
    w = int(fig.get("canvas", {}).get("width_px") or 2400)
    h = int(fig.get("canvas", {}).get("height_px") or 1400)
    ents = model.get("entities", [])
    rels = model.get("relations", [])
    pos = _auto_layout(model)
    boxes = _group_boxes(model, pos)
    by_id = {e["id"]: e for e in ents}

    svg = []
    svg.append(f'<g id="layer-background" data-layer="background">')
    svg.append(f'<rect x="0" y="0" width="{w}" height="{h}" fill="#FFFFFF"/>')
    for b in boxes:
        svg.append(f'<rect x="{_fmt(b["x"])}" y="{_fmt(b["y"])}" width="{_fmt(b["w"])}" height="{_fmt(b["h"])}" rx="18" fill="#FAFAFA" stroke="#B0BEC5" stroke-width="1.4" stroke-dasharray="6,6"/>')
        svg.append(f'<text x="{_fmt(b["x"] + 14)}" y="{_fmt(b["y"] + 24)}" font-family="{_font_family()}" font-size="20" fill="#546E7A" text-anchor="start" font-weight="bold">{_esc(b["label"])}</text>')
    svg.append("</g>")
    svg.append(f'<g id="layer-relations" data-layer="relations">')
    for i, r in enumerate(rels):
        a, b = r.get("from"), r.get("to")
        if a not in by_id or b not in by_id or r.get("kind") == "containment":
            continue
        p_from = (pos[a][0], pos[a][1]) if a in pos else (0, 0)
        p_to = (pos[b][0], pos[b][1]) if b in pos else (0, 0)
        line_svg, mid = _relation_svg(r, p_from, p_to, pos.get(a, (0, 0, 60, 40)), pos.get(b, (0, 0, 60, 40)), i)
        svg.append(line_svg)
        if r.get("label"):
            svg.append(_svg_text(r["label"], mid[0], mid[1], 18, fill="#455A64"))
    svg.append("</g>")
    svg.append(f'<g id="layer-entities" data-layer="entities">')
    for e in ents:
        if e["id"] not in pos:
            continue
        cx, cy, nw, nh = pos[e["id"]]
        fs = float((e.get("style") or {}).get("font_size") or 22)
        fill = _hex_color((e.get("style") or {}).get("fill"), "#F4F6F8")
        svg.append(_shape_svg(e, cx, cy, nw, nh, e.get("style") or {}))
        svg.append(_svg_text(e.get("label", ""), cx, cy, fs, fill="#1A1A1A"))
    svg.append("</g>")
    svg.append(f'<g id="layer-annotations" data-layer="annotations">')
    for a in model.get("annotations", []):
        anchor = a.get("anchor")
        if anchor in pos:
            cx, cy, nw, nh = pos[anchor]
            place = a.get("place", "top")
            dx, dy = {"top": (0, -nh / 2 - 14), "bottom": (0, nh / 2 + 24),
                      "left": (-nw / 2 - 14, 0), "right": (nw / 2 + 14, 0)}.get(place, (0, -nh / 2 - 14))
            svg.append(_svg_text(a.get("text", ""), cx + dx, cy + dy, 16, fill="#6A737D", anchor="middle"))
    svg.append("</g>")
    svg.append(f'<g id="layer-legend" data-layer="legend">')
    svg.append(_legend_svg(model, w, h))
    svg.append("</g>")
    return "\n".join(svg)


def _legend_svg(model, canvas_w, canvas_h):
    legend = model.get("legend", {})
    items = legend.get("items", []) or []
    dm = legend.get("data_mapping")
    if dm and (not items):
        cats = dm.get("categories", [])
        colors = dm.get("colors", {})
        markers = dm.get("markers", {})
        items = []
        for c in cats:
            items.append({"id": f"lg_{c}", "label": c, "color": colors.get(c),
                          "marker": markers.get(c, "box")})
    if not items:
        return ""
    parts = []
    n = len(items)
    cell = min(220.0, (canvas_w - 80) / max(n, 1))
    y = canvas_h - 60.0
    for i, it in enumerate(items):
        x = 40 + i * cell
        color = _hex_color(it.get("color"), "#37474F")
        marker = it.get("marker", "box")
        parts.append(f'<g data-legend-item="{_esc(it.get("id", ""))}">')
        if marker in ("arrow",):
            parts.append(f'<line x1="{_fmt(x)}" y1="{_fmt(y)}" x2="{_fmt(x + 30)}" y2="{_fmt(y)}" stroke="{color}" stroke-width="2"/>')
            parts.append(f'<polygon points="{_fmt(x + 30)},{_fmt(y)} {_fmt(x + 22)},{_fmt(y - 5)} {_fmt(x + 22)},{_fmt(y + 5)}" fill="{color}"/>')
        elif marker in ("tee",):
            parts.append(f'<line x1="{_fmt(x)}" y1="{_fmt(y)}" x2="{_fmt(x + 28)}" y2="{_fmt(y)}" stroke="{color}" stroke-width="2"/>')
            parts.append(f'<line x1="{_fmt(x + 28)}" y1="{_fmt(y - 6)}" x2="{_fmt(x + 28)}" y2="{_fmt(y + 6)}" stroke="{color}" stroke-width="3"/>')
        elif marker in ("circle",):
            parts.append(f'<circle cx="{_fmt(x + 14)}" cy="{_fmt(y)}" r="8" fill="{color}" stroke="#37474F" stroke-width="1"/>')
        elif marker in ("ellipse",):
            parts.append(f'<ellipse cx="{_fmt(x + 14)}" cy="{_fmt(y)}" rx="12" ry="8" fill="{color}" stroke="#37474F" stroke-width="1"/>')
        elif marker in ("hexagon",):
            pts = []
            for k in range(6):
                ang = math.pi / 6 + k * math.pi / 3
                pts.append(f"{_fmt(x + 14 + 9 * math.cos(ang))},{_fmt(y + 9 * math.sin(ang))}")
            parts.append(f'<polygon points="{" ".join(pts)}" fill="{color}" stroke="#37474F" stroke-width="1"/>')
        elif marker == "line":
            parts.append(f'<line x1="{_fmt(x)}" y1="{_fmt(y)}" x2="{_fmt(x + 28)}" y2="{_fmt(y)}" stroke="{color}" stroke-width="2"/>')
        elif marker == "dot":
            parts.append(f'<circle cx="{_fmt(x + 14)}" cy="{_fmt(y)}" r="4" fill="{color}"/>')
        else:  # box
            parts.append(f'<rect x="{_fmt(x)}" y="{_fmt(y - 8)}" width="28" height="16" rx="3" fill="{color}" stroke="#37474F" stroke-width="1"/>')
        parts.append(_svg_text(it.get("label", ""), x + 38, y, 16, fill="#1A1A1A", anchor="start"))
        parts.append("</g>")
    return "\n".join(parts)


def _meta_payload(model):
    return {"schema_version": model.get("schema_version"), "figure_id": model.get("figure", {}).get("id"),
            "entity_ids": [e["id"] for e in model.get("entities", [])],
            "relation_ids": [r["id"] for r in model.get("relations", [])],
            "generated_by": "sci-figure-model/render.py"}


# ---------------------------------------------------------------- chart SVG

def _chart_svg(model, w, h):
    chart = model.get("chart", {})
    xs = chart.get("x", {}).get("values", [])
    series = chart.get("series", [])
    ml, mr, mt, mb = 90, 40, 50, 90
    pw = w - ml - mr
    ph = h - mt - mb
    allvals = []
    for s in series:
        allvals.extend([v for v in s.get("values", []) if isinstance(v, (int, float))])
        for v in s.get("error", []) or []:
            allvals.append(v)
    if not allvals:
        return ""
    vmin = min(allvals)
    vmax = max(allvals)
    if chart.get("type") == "bar":
        vmin = min(0.0, vmin)
    span = (vmax - vmin) or 1.0
    parts = []
    parts.append(f'<g id="layer-axes">')
    parts.append(f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt + ph}" stroke="#333" stroke-width="1.6"/>')
    parts.append(f'<line x1="{ml}" y1="{mt + ph}" x2="{ml + pw}" y2="{mt + ph}" stroke="#333" stroke-width="1.6"/>')
    parts.append(f'<text x="{_fmt(ml - 10)}" y="{_fmt(mt - 14)}" font-family="{_font_family()}" font-size="18" text-anchor="end">{_esc(chart.get("y", {}).get("label", "") + (" (" + chart["y"].get("unit") + ")" if chart.get("y", {}).get("unit") else ""))}</text>')
    parts.append(f'<text x="{_fmt(ml + pw / 2)}" y="{_fmt(h - 24)}" font-family="{_font_family()}" font-size="18" text-anchor="middle">{_esc(chart.get("x", {}).get("label", "") + (" (" + chart["x"].get("unit") + ")" if chart.get("x", {}).get("unit") else ""))}</text>')
    # y ticks
    for i in range(5):
        val = vmin + span * i / 4
        yy = mt + ph - (val - vmin) / span * ph
        parts.append(f'<line x1="{ml}" y1="{_fmt(yy)}" x2="{ml - 5}" y2="{_fmt(yy)}" stroke="#333" stroke-width="1"/>')
        parts.append(f'<text x="{_fmt(ml - 8)}" y="{_fmt(yy + 5)}" font-family="{_font_family()}" font-size="14" text-anchor="end">{_fmt(val)}</text>')
    parts.append("</g>")
    n = len(xs)
    nser = max(len(series), 1)
    parts.append(f'<g id="layer-series">')
    for si, s in enumerate(series):
        color = _hex_color(s.get("color"), "#1565C0")
        vals = s.get("values", [])
        errs = s.get("error", []) or []
        if chart.get("type") == "bar":
            bw = (pw / max(n, 1)) / nser * 0.7
            for i, v in enumerate(vals):
                if not isinstance(v, (int, float)):
                    continue
                xc = ml + (i + 0.5) / max(n, 1) * pw + (si - (nser - 1) / 2) * bw
                yv = mt + ph - (v - vmin) / span * ph
                y0 = mt + ph - (0 - vmin) / span * ph if vmin <= 0 <= vmax else mt + ph
                parts.append(f'<rect x="{_fmt(xc - bw / 2)}" y="{_fmt(min(yv, y0))}" width="{_fmt(bw)}" height="{_fmt(abs(yv - y0))}" fill="{color}"/>')
        else:
            pts = []
            for i, v in enumerate(vals):
                if not isinstance(v, (int, float)):
                    continue
                xc = ml + (i + 0.5) / max(n, 1) * pw
                yv = mt + ph - (v - vmin) / span * ph
                pts.append((xc, yv))
            if chart.get("type") == "line" and len(pts) > 1:
                p = " ".join(f"{_fmt(a)},{_fmt(b)}" for a, b in pts)
                parts.append(f'<polyline points="{p}" fill="none" stroke="{color}" stroke-width="2.4"/>')
            marker = s.get("marker", "circle")
            for xc, yv in pts:
                if marker == "square":
                    parts.append(f'<rect x="{_fmt(xc - 4)}" y="{_fmt(yv - 4)}" width="8" height="8" fill="{color}"/>')
                else:
                    parts.append(f'<circle cx="{_fmt(xc)}" cy="{_fmt(yv)}" r="4.5" fill="{color}"/>')
        for i, e in enumerate(errs):
            if not isinstance(e, (int, float)):
                continue
            xc = ml + (i + 0.5) / max(n, 1) * pw
            yv = mt + ph - (vals[i] - vmin) / span * ph
            ye_hi = mt + ph - (vals[i] + e - vmin) / span * ph
            ye_lo = mt + ph - (vals[i] - e - vmin) / span * ph
            parts.append(f'<line x1="{_fmt(xc)}" y1="{_fmt(ye_hi)}" x2="{_fmt(xc)}" y2="{_fmt(ye_lo)}" stroke="{color}" stroke-width="1.2"/>')
            parts.append(f'<line x1="{_fmt(xc - 4)}" y1="{_fmt(ye_hi)}" x2="{_fmt(xc + 4)}" y2="{_fmt(ye_hi)}" stroke="{color}" stroke-width="1.2"/>')
            parts.append(f'<line x1="{_fmt(xc - 4)}" y1="{_fmt(ye_lo)}" x2="{_fmt(xc + 4)}" y2="{_fmt(ye_lo)}" stroke="{color}" stroke-width="1.2"/>')
    parts.append("</g>")
    # x tick labels
    parts.append(f'<g id="layer-xlabels">')
    for i, xv in enumerate(xs):
        xc = ml + (i + 0.5) / max(n, 1) * pw
        parts.append(f'<text x="{_fmt(xc)}" y="{_fmt(mt + ph + 22)}" font-family="{_font_family()}" font-size="14" text-anchor="middle">{_esc(str(xv))}</text>')
    parts.append("</g>")
    # series legend
    ly = mt + 16
    for si, s in enumerate(series):
        color = _hex_color(s.get("color"), "#1565C0")
        lx = ml + si * 120
        parts.append(f'<rect x="{_fmt(lx)}" y="{_fmt(ly)}" width="14" height="14" fill="{color}"/>')
        parts.append(_svg_text(s.get("label", ""), lx + 20, ly + 7, 14, fill="#1A1A1A", anchor="start"))
    return "\n".join(parts)


def _build_svg_document(model):
    fig = model.get("figure", {})
    w = int(fig.get("canvas", {}).get("width_px") or 2400)
    h = int(fig.get("canvas", {}).get("height_px") or 1400)
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
           f'<metadata>{_esc(json.dumps(_meta_payload(model), ensure_ascii=False))}</metadata>',
           f'<title>{_esc(fig.get("title") or fig.get("id") or "figure")}</title>']
    if fig.get("kind") == "chart" and model.get("chart"):
        svg.append(f'<rect x="0" y="0" width="{w}" height="{h}" fill="#FFFFFF"/>')
        svg.append(f'<g id="layer-chart" data-layer="chart">')
        svg.append(_chart_svg(model, w, h))
        svg.append("</g>")
    else:
        svg.append(_build_schematic_layers(model))
    svg.append("</svg>")
    return "\n".join(svg)


# ---------------------------------------------------------------- PDF

class _Pdf:
    def __init__(self, w_pt, h_pt):
        self.w, self.h = w_pt, h_pt
        self.c = []

    def _p(self, s):
        self.c.append(s)

    def rgb(self, hexc, stroke=False):
        r, g, b = _parse_color(hexc)
        self._p(f"{_fmt(r, 3)} {_fmt(g, 3)} {_fmt(b, 3)} {'RG' if stroke else 'rg'}")

    def lw(self, w):
        self._p(f"{_fmt(w, 2)} w")

    def _pts(self, pts, close=False, fill=False, stroke=True, lw=None):
        if not pts:
            return
        if lw is not None:
            self.lw(lw)
        x0, y0 = pts[0]
        self._p(f"{_fmt(x0)} {_fmt(y0)} m")
        for x, y in pts[1:]:
            self._p(f"{_fmt(x)} {_fmt(y)} l")
        if close:
            self._p("h")
        op = "b" if (fill and stroke) else ("f" if fill else "S")
        self._p(op)

    def line(self, x1, y1, x2, y2, lw=None):
        if lw is not None:
            self.lw(lw)
        self._p(f"{_fmt(x1)} {_fmt(y1)} m {_fmt(x2)} {_fmt(y2)} l S")

    def rect(self, x, y, w, h, fill=None, stroke=None, lw=None, r=None):
        if fill:
            self.rgb(fill)
            if r:
                self._round_rect(x, y, w, h, r, "f")
            else:
                self._p(f"{_fmt(x)} {_fmt(y)} {_fmt(w)} {_fmt(h)} re f")
        if stroke:
            self.rgb(stroke, stroke=True)
            if lw is not None:
                self.lw(lw)
            if r:
                self._round_rect(x, y, w, h, r, "S")
            else:
                self._p(f"{_fmt(x)} {_fmt(y)} {_fmt(w)} {_fmt(h)} re S")

    def _round_rect(self, x, y, w, h, r, op):
        rr = min(r, w / 2, h / 2)
        self._p(f"{_fmt(x + rr)} {_fmt(y)} m")
        self._p(f"{_fmt(x + w - rr)} {_fmt(y)} l")
        self._p(f"{_fmt(x + w - rr)} {_fmt(y + rr)} {_fmt(x + w)} {_fmt(y + rr)} {_fmt(x + w)} {_fmt(y + rr)} c")
        self._p(f"{_fmt(x + w)} {_fmt(y + h - rr)} l")
        self._p(f"{_fmt(x + w - rr)} {_fmt(y + h)} {_fmt(x + w - rr)} {_fmt(y + h)} {_fmt(x + w - rr)} {_fmt(y + h)} c")
        self._p(f"{_fmt(x + rr)} {_fmt(y + h)} l")
        self._p(f"{_fmt(x + rr)} {_fmt(y + h - rr)} {_fmt(x)} {_fmt(y + h - rr)} {_fmt(x)} {_fmt(y + h - rr)} c")
        self._p(f"{_fmt(x)} {_fmt(y + rr)} l")
        self._p(f"{_fmt(x + rr)} {_fmt(y)} {_fmt(x + rr)} {_fmt(y)} {_fmt(x + rr)} {_fmt(y)} c")
        self._p(op)

    def ellipse(self, cx, cy, rx, ry, fill=None, stroke=None, lw=None):
        k = 0.5523
        if fill:
            self.rgb(fill)
            self._ellipse_path(cx, cy, rx, ry, k, "f")
        if stroke:
            self.rgb(stroke, stroke=True)
            if lw is not None:
                self.lw(lw)
            self._ellipse_path(cx, cy, rx, ry, k, "S")

    def _ellipse_path(self, cx, cy, rx, ry, k, op):
        self._p(f"{_fmt(cx + rx)} {_fmt(cy)} m")
        self._p(f"{_fmt(cx + rx)} {_fmt(cy + k * ry)} {_fmt(cx + k * rx)} {_fmt(cy + ry)} {_fmt(cx)} {_fmt(cy + ry)} c")
        self._p(f"{_fmt(cx - k * rx)} {_fmt(cy + ry)} {_fmt(cx - rx)} {_fmt(cy + k * ry)} {_fmt(cx - rx)} {_fmt(cy)} c")
        self._p(f"{_fmt(cx - rx)} {_fmt(cy - k * ry)} {_fmt(cx - k * rx)} {_fmt(cy - ry)} {_fmt(cx)} {_fmt(cy - ry)} c")
        self._p(f"{_fmt(cx + k * rx)} {_fmt(cy - ry)} {_fmt(cx + rx)} {_fmt(cy - k * ry)} {_fmt(cx + rx)} {_fmt(cy)} c")
        self._p(op)

    def polygon(self, pts, fill=None, stroke=None, lw=None):
        if fill:
            self.rgb(fill)
            self._pts(pts, close=True, fill=True, stroke=False)
        if stroke:
            self.rgb(stroke, stroke=True)
            self._pts(pts, close=True, fill=False, stroke=True, lw=lw)

    def text(self, x, y, s, size, anchor="start", bold=False, color="#000000"):
        if not s:
            return
        s = str(s)
        is_ascii = all(ord(ch) < 128 for ch in s)
        self.rgb(color)
        self._p("BT")
        if is_ascii:
            font = "/F2" if bold else "/F1"
            self._p(f"{font} {_fmt(size)} Tf")
            w = _text_w(s, size)
        else:
            self._p(f"/F0 {_fmt(size)} Tf")
            w = sum(size if ord(ch) > 127 else 0.58 * size for ch in s)
        off = {"start": 0.0, "middle": -w / 2, "end": -w}.get(anchor, 0.0)
        self._p(f"1 0 0 1 {_fmt(x + off)} {_fmt(y)} Tm")
        if is_ascii:
            t = s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            self._p(f"({t}) Tj")
        else:
            hx = "FEFF" + s.encode("utf-16-be").hex()
            self._p(f"<{hx}> Tj")
        self._p("ET")

    def save(self, path):
        objs = []
        # font objects
        f_helv = 4
        f_helv_b = 5
        f_cid = 6
        f_desc = 8
        objs.append((f_helv, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"))
        objs.append((f_helv_b, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"))
        objs.append((f_cid, b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light /Encoding /UniGB-UCS2-H /DescendantFonts [8 0 R] >>"))
        objs.append((f_desc, b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light /CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 4 >> >>"))
        content = "\n".join(self.c).encode("utf-8", "replace")
        stream_obj_num = 9
        objs.append((stream_obj_num, b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream"))
        page_obj = 3
        resources = (f"<< /Font << /F1 {f_helv} 0 R /F2 {f_helv_b} 0 R /F0 {f_cid} 0 R >> >>").encode()
        objs.append((page_obj, b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 "
                     + str(int(round(self.w))).encode() + b" " + str(int(round(self.h))).encode()
                     + b"] /Resources " + resources + b" /Contents " + str(stream_obj_num).encode() + b" 0 R >>"))
        objs.append((2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"))
        objs.append((1, b"<< /Type /Catalog /Pages 2 0 R >>"))
        objs.sort(key=lambda o: o[0])
        out = [b"%PDF-1.4", b"%\xe2\xe3\xcf\xd3"]
        offsets = {}
        # account for the 14-byte header when computing absolute offsets
        pos = sum(len(x) for x in out)
        for num, body in objs:
            offsets[num] = pos
            line = f"{num} 0 obj\n".encode() + body + b"\nendobj\n"
            out.append(line)
            pos += len(line)
        xref_pos = sum(len(x) for x in out)
        maxnum = max(offsets)
        out.append(f"xref\n0 {maxnum + 1}\n".encode())
        out.append(b"0000000000 65535 f \n")
        for num in range(1, maxnum + 1):
            if num in offsets:
                out.append(f"{offsets[num]:010d} 00000 n \n".encode())
            else:
                out.append(b"0000000000 65535 f \n")
        out.append(f"trailer\n<< /Size {maxnum + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode())
        with open(path, "wb") as fh:
            for chunk in out:
                fh.write(chunk)


def _build_pdf(model, out_path, dpi):
    fig = model.get("figure", {})
    w_px = int(fig.get("canvas", {}).get("width_px") or 2400)
    h_px = int(fig.get("canvas", {}).get("height_px") or 1400)
    scale = 72.0 / dpi
    w_pt = w_px * scale
    h_pt = h_px * scale
    pdf = _Pdf(w_pt, h_pt)

    def to_pt(x, y):
        return x * scale, h_pt - y * scale

    if fig.get("kind") == "chart" and model.get("chart"):
        _draw_chart_pdf(pdf, model, w_px, h_px, to_pt)
    else:
        _draw_schematic_pdf(pdf, model, to_pt)
    pdf.save(out_path)
    return w_pt, h_pt


def _draw_schematic_pdf(pdf, model, to_pt):
    ents = model.get("entities", [])
    rels = model.get("relations", [])
    pos = _auto_layout(model)
    boxes = _group_boxes(model, pos)
    by_id = {e["id"]: e for e in ents}
    for b in boxes:
        x1, y1 = to_pt(b["x"], b["y"])
        x2, y2 = to_pt(b["x"] + b["w"], b["y"] + b["h"])
        pdf.rect(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1), fill="#FAFAFA", stroke="#B0BEC5", lw=1.0, r=8 * 72.0 / 300.0)
        pdf.text(x1 + 8, y1 - 14, b["label"], 10, color="#546E7A", bold=True)
    for r in rels:
        a, b = r.get("from"), r.get("to")
        if a not in by_id or b not in by_id or r.get("kind") == "containment":
            continue
        if a not in pos or b not in pos:
            continue
        p1 = (pos[a][0], pos[a][1])
        p2 = (pos[b][0], pos[b][1])
        _draw_relation_pdf(pdf, r, p1, p2, pos[a], pos[b], to_pt)
    for e in ents:
        if e["id"] not in pos:
            continue
        cx, cy, nw, nh = pos[e["id"]]
        style = e.get("style") or {}
        fill = style.get("fill", "#F4F6F8")
        stroke = style.get("stroke", "#333333")
        shape = style.get("shape", "box")
        x1, y1 = to_pt(cx - nw / 2, cy - nh / 2)
        x2, y2 = to_pt(cx + nw / 2, cy + nh / 2)
        x = min(x1, x2)
        y = min(y1, y2)
        w = abs(x2 - x1)
        h = abs(y2 - y1)
        fs = float(style.get("font_size") or 22) * 72.0 / 300.0
        if shape in ("ellipse", "circle"):
            pdf.ellipse(cx * 72.0 / 300.0, to_pt(cx, cy)[1], w / 2, h / 2, fill=fill, stroke=stroke, lw=1.0)
        elif shape == "rounded":
            pdf.rect(x, y, w, h, fill=fill, stroke=stroke, lw=1.0, r=6 * 72.0 / 300.0)
        elif shape == "hexagon":
            pts = [(cx + nw / 2 * math.cos(math.pi / 6 + i * math.pi / 3),
                    cy + nh / 2 * math.sin(math.pi / 6 + i * math.pi / 3)) for i in range(6)]
            pdf.polygon([to_pt(px, py) for px, py in pts], fill=fill, stroke=stroke, lw=1.0)
        elif shape == "plain":
            pass
        else:
            pdf.rect(x, y, w, h, fill=fill, stroke=stroke, lw=1.0)
        tx, ty = to_pt(cx, cy + fs * 0.30)
        pdf.text(tx, ty, e.get("label", ""), fs, anchor="middle", color="#1A1A1A")


def _draw_relation_pdf(pdf, r, p1, p2, e1, e2, to_pt):
    kind = r.get("kind", "activation")
    color = (r.get("style") or {}).get("color", "#37474F")
    hw = e1[2] / 2 + 4
    hh = e1[3] / 2 + 4
    a1 = _trim(p1, p2, hw, hh)
    a2 = _trim(p2, p1, e2[2] / 2 + 4, e2[3] / 2 + 4)
    dx, dy = a2[0] - a1[0], a2[1] - a1[1]
    L = math.hypot(dx, dy) or 1
    ux, uy = dx / L, dy / L
    px, py = -uy, ux
    s = 72.0 / 300.0
    head = 10.0 * s
    pdf.rgb(color, stroke=True)
    pdf.lw(1.4)
    if kind in ("activation", "causal", "upstream_downstream", "temporal"):
        bx, by = a2[0] - ux * head, a2[1] - uy * head
        x1, y1 = to_pt(a1[0], a1[1])
        x2, y2 = to_pt(bx, by)
        pdf.line(x1, y1, x2, y2)
        hx1, hy1 = to_pt(bx + px * head * 0.5, by + py * head * 0.5)
        hx2, hy2 = to_pt(bx - px * head * 0.5, by - py * head * 0.5)
        tip = to_pt(a2[0], a2[1])
        pdf.polygon([tip, (hx1, hy1), (hx2, hy2)], fill=color)
    elif kind == "inhibition":
        x1, y1 = to_pt(a1[0], a1[1])
        x2, y2 = to_pt(a2[0], a2[1])
        pdf.line(x1, y1, x2, y2)
        tx1, ty1 = to_pt(a2[0] + px * 7, a2[1] + py * 7)
        tx2, ty2 = to_pt(a2[0] - px * 7, a2[1] - py * 7)
        pdf.lw(2.2)
        pdf.line(tx1, ty1, tx2, ty2)
    else:
        x1, y1 = to_pt(a1[0], a1[1])
        x2, y2 = to_pt(a2[0], a2[1])
        pdf.line(x1, y1, x2, y2)


def _draw_chart_pdf(pdf, model, w_px, h_px, to_pt):
    chart = model.get("chart", {})
    xs = chart.get("x", {}).get("values", [])
    series = chart.get("series", [])
    scale = 72.0 / 300.0
    ml, mr, mt, mb = 90, 40, 50, 90
    pw = w_px - ml - mr
    ph = h_px - mt - mb
    allvals = []
    for s in series:
        allvals.extend([v for v in s.get("values", []) if isinstance(v, (int, float))])
    if not allvals:
        return
    vmin, vmax = min(allvals), max(allvals)
    if chart.get("type") == "bar":
        vmin = min(0.0, vmin)
    span = (vmax - vmin) or 1.0
    pdf.rgb("#333333", stroke=True)
    pdf.lw(1.2)
    x1, y1 = to_pt(ml, mt)
    x2, y2 = to_pt(ml, mt + ph)
    pdf.line(x1, y1, x2, y2)
    x2, y2 = to_pt(ml + pw, mt + ph)
    pdf.line(x1, y1, x2, y2)
    n = len(xs)
    nser = max(len(series), 1)
    for si, s in enumerate(series):
        color = s.get("color", "#1565C0")
        vals = s.get("values", [])
        if chart.get("type") == "bar":
            bw = (pw / max(n, 1)) / nser * 0.7
            for i, v in enumerate(vals):
                if not isinstance(v, (int, float)):
                    continue
                xc = ml + (i + 0.5) / max(n, 1) * pw + (si - (nser - 1) / 2) * bw
                yv = mt + ph - (v - vmin) / span * ph
                y0 = mt + ph - (0 - vmin) / span * ph if vmin <= 0 <= vmax else mt + ph
                xa, ya = to_pt(xc - bw / 2, min(yv, y0))
                xb, yb = to_pt(xc + bw / 2, max(yv, y0))
                pdf.rect(min(xa, xb), min(ya, yb), abs(xb - xa), abs(yb - ya), fill=color)
        else:
            pts = []
            for i, v in enumerate(vals):
                if not isinstance(v, (int, float)):
                    continue
                xc = ml + (i + 0.5) / max(n, 1) * pw
                yv = mt + ph - (v - vmin) / span * ph
                pts.append(to_pt(xc, yv))
            if chart.get("type") == "line" and len(pts) > 1:
                pdf.rgb(color, stroke=True)
                pdf.lw(2.0)
                x0, y0 = pts[0]
                pdf._p(f"{_fmt(x0)} {_fmt(y0)} m")
                for px, py in pts[1:]:
                    pdf._p(f"{_fmt(px)} {_fmt(py)} l")
                pdf._p("S")
            for px, py in pts:
                pdf.rgb(color)
                pdf._p(f"{_fmt(px)} {_fmt(py)} 3 m {_fmt(px)} {_fmt(py)} 3 l S")


# ---------------------------------------------------------------- export

def _read_png_size(path):
    try:
        with open(path, "rb") as fh:
            sig = fh.read(24)
        if sig[:8] == b"\x89PNG\r\n\x1a\n":
            return int.from_bytes(sig[16:20], "big"), int.from_bytes(sig[20:24], "big")
    except Exception:
        pass
    return None, None


def export_png(pdf_path, out_png, dpi):
    """Convert PDF->PNG with pdftoppm (preinstalled). Returns (ok, info)."""
    exe = shutil.which("pdftoppm")
    if exe:
        base = out_png[:-4] if out_png.endswith(".png") else out_png
        r = subprocess.run([exe, "-png", "-r", str(dpi), "-singlefile", pdf_path, base],
                           capture_output=True, text=True)
        if os.path.exists(out_png):
            w, h = _read_png_size(out_png)
            return True, {"converter": "pdftoppm", "width_px": w, "height_px": h, "dpi": dpi}
    for exe_name in ("rsvg-convert", "inkscape", "magick", "convert"):
        exe = shutil.which(exe_name)
        if not exe:
            continue
        r = subprocess.run([exe, pdf_path, "-o", out_png] if exe_name in ("rsvg-convert",) else
                           ([exe, pdf_path, out_png] if exe_name != "inkscape" else
                            [exe, pdf_path, "--export-filename", out_png]),
                           capture_output=True, text=True)
        if os.path.exists(out_png):
            w, h = _read_png_size(out_png)
            return True, {"converter": exe_name, "width_px": w, "height_px": h, "dpi": dpi}
    return False, {"converter": None, "reason": "no PDF/PNG converter available; pdftoppm not found"}


# ---------------------------------------------------------------- main

def render_model(model, output_dir, work_dir=None, dpi=300):
    """Render a figure_model dict. Writes figure.svg, figure.pdf, figure.png.
    Returns a dict of export_file records."""
    fig = model.get("figure", {})
    fid = (fig.get("id") or "figure").replace("/", "_").replace("\\", "_")
    os.makedirs(output_dir, exist_ok=True)
    svg_path = os.path.join(output_dir, f"{fid}.svg")
    pdf_path = os.path.join(output_dir, f"{fid}.pdf")
    png_path = os.path.join(output_dir, f"{fid}.png")
    svg = _build_svg_document(model)
    with open(svg_path, "w", encoding="utf-8") as fh:
        fh.write(svg)
    dpi = int(dpi or fig.get("canvas", {}).get("dpi") or 300)
    w_px = int(fig.get("canvas", {}).get("width_px") or 2400)
    h_px = int(fig.get("canvas", {}).get("height_px") or 1400)
    exports = [{"path": os.path.basename(svg_path), "format": "svg", "kind": "vector_editable",
                "width_px": w_px, "height_px": h_px, "note": "layered, editable text"}]
    pdf_w, pdf_h = _build_pdf(model, pdf_path, dpi)
    exports.append({"path": os.path.basename(pdf_path), "format": "pdf",
                    "width_pt": round(pdf_w, 1), "height_pt": round(pdf_h, 1),
                    "width_px": w_px, "height_px": h_px, "dpi": dpi,
                    "font_check": "base14_or_cid_standard", "note": "vector preview"})
    ok, info = export_png(pdf_path, png_path, dpi)
    if ok:
        exports.append({"path": os.path.basename(png_path), "format": "png",
                        "width_px": info.get("width_px"), "height_px": info.get("height_px"),
                        "dpi": dpi, "color_space": "RGB",
                        "font_check": "n/a", "note": f"raster preview via {info.get('converter')}"})
    else:
        exports.append({"path": os.path.basename(png_path), "format": "png",
                        "status": "failed", "note": info.get("reason"),
                        "dpi": dpi, "color_space": "RGB"})
    return {"source_file": svg_path, "exports": exports}


def _load(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def _cli():
    ap = argparse.ArgumentParser(description="figure_model.json -> SVG/PDF/PNG")
    ap.add_argument("--model", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--work", default=None)
    ap.add_argument("--dpi", type=int, default=300)
    a = ap.parse_args()
    model = _load(a.model)
    res = render_model(model, a.output, a.work, a.dpi)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _cli()
