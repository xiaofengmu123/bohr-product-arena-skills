#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""渲染核心：布局、分层 SVG、stdlib 矢量 PDF、图例（纯标准库、确定性）。"""
from __future__ import annotations

import html
import math

ENGINE_VERSION = "1.1.0"
DEFAULT_COMPARTMENTS = ["extracellular", "membrane", "cytosol", "nucleus"]

NODE_STYLE = {
    "gene":        {"fill": "#E8F0FE", "stroke": "#1A56DB", "shape": "rounded"},
    "protein":     {"fill": "#E6F4EA", "stroke": "#1E8E3E", "shape": "rounded"},
    "complex":     {"fill": "#FEF7E0", "stroke": "#F9AB00", "shape": "hexagon"},
    "family":      {"fill": "#F3E8FD", "stroke": "#9334E6", "shape": "rounded"},
    "cell_type":   {"fill": "#FCE8E6", "stroke": "#D93025", "shape": "ellipse"},
    "compartment": {"fill": "#FFFFFF", "stroke": "#5F6368", "shape": "rectangle"},
    "other":       {"fill": "#F1F3F4", "stroke": "#5F6368", "shape": "rounded"},
}
RELATION_STYLE = {
    "activation":   {"color": "#1E8E3E", "marker": "arrow", "dash": None},
    "inhibition":   {"color": "#D93025", "marker": "tbar",  "dash": None},
    "binding":      {"color": "#5F6368", "marker": "none",  "dash": "8,4"},
    "modification": {"color": "#9334E6", "marker": "arrow", "dash": None},
    "transport":    {"color": "#1565C0", "marker": "arrow", "dash": None},
    "conversion":   {"color": "#EF6C00", "marker": "double","dash": None},
}
RELATION_LABEL = {
    "activation": "activation", "inhibition": "inhibition", "binding": "binding",
    "modification": "modification", "transport": "transport", "conversion": "conversion",
}
KIND_LABEL = {
    "gene": "gene", "protein": "protein", "complex": "complex", "family": "family",
    "cell_type": "cell type", "compartment": "compartment", "other": "other",
}


def rect_exit(cx, cy, tx, ty, rect):
    """从矩形内部点 (cx,cy) 沿 (tx,ty) 方向到矩形边界的交点。"""
    xmin, ymin, xmax, ymax = rect
    dx, dy = tx - cx, ty - cy
    t = 1.0
    if dx > 0:
        t = min(t, (xmax - cx) / dx)
    elif dx < 0:
        t = min(t, (xmin - cx) / dx)
    if dy > 0:
        t = min(t, (ymax - cy) / dy)
    elif dy < 0:
        t = min(t, (ymin - cy) / dy)
    return cx + dx * t, cy + dy * t


def compute_layout(spec):
    ents = spec.get("entities") or []
    comps = [c for c in (spec.get("compartments") or DEFAULT_COMPARTMENTS) if c] or DEFAULT_COMPARTMENTS
    canvas = spec.get("canvas") or {}
    base_w = int(canvas.get("width") or 1200)
    title_h = 56
    band_h = 150
    gap = 16
    margin = 70
    node_w, node_h = 132, 44
    spacing = 62
    groups = []
    for c in comps:
        members = [e for e in ents if (e.get("compartment") or "unknown") == c]
        groups.append((c, members))
    rest = [e for e in ents if (e.get("compartment") or "unknown") not in comps]
    if rest:
        groups.append(("unknown", rest))
    max_nodes = max((len(m) for _, m in groups), default=0)
    needed_w = margin * 2 + max(0, max_nodes - 1) * (node_w + spacing) + node_w
    W = max(base_w, needed_w)
    nodes, node_by_id = [], {}
    y = title_h
    band_rects = []
    for c, members in groups:
        y0, y1 = y, y + band_h
        band_rects.append({"name": c, "x0": 12, "y0": y0, "x1": W - 12, "y1": y1})
        n = len(members)
        total_w = n * (node_w + spacing) - spacing
        x0 = max(margin, (W - total_w) // 2)
        cy = (y0 + y1) // 2
        for i, e in enumerate(members):
            x = x0 + i * (node_w + spacing)
            nd = {"id": e.get("id"), "x": x, "y": cy - node_h // 2, "w": node_w,
                  "h": node_h, "cx": x + node_w // 2, "cy": cy, "entity": e}
            nodes.append(nd)
            node_by_id[e.get("id")] = nd
        y = y1 + gap
    legend_rows = []
    used_rtypes = sorted({r.get("type") for r in (spec.get("relations") or [])
                          if r.get("type") in RELATION_STYLE})
    used_kinds = sorted({e.get("kind") for e in ents if e.get("kind") in NODE_STYLE})
    legend_rows.append(("relation", used_rtypes))
    legend_rows.append(("kind", used_kinds))
    for dm in (spec.get("data_mappings") or []):
        legend_rows.append(("data", [dm]))
    legend_h = 26 * sum(1 for _, items in legend_rows for _ in (items or [""])) + 30
    H = y + legend_h + 30
    edges = []
    for r in (spec.get("relations") or []):
        a, b = node_by_id.get(r.get("from")), node_by_id.get(r.get("to"))
        if not a or not b:
            continue
        ra = (a["x"], a["y"], a["x"] + a["w"], a["y"] + a["h"])
        rb = (b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"])
        x1, y1 = rect_exit(a["cx"], a["cy"], b["cx"], b["cy"], ra)
        x2, y2 = rect_exit(b["cx"], b["cy"], a["cx"], a["cy"], rb)
        edges.append({"rid": r.get("id"), "x1": x1, "y1": y1,
                      "x2": x2, "y2": y2, "rel": r})
    return {"canvas": {"w": W, "h": H}, "bands": band_rects, "nodes": nodes,
            "edges": edges, "legend_rows": legend_rows, "node_by_id": node_by_id,
            "compartments": comps + (["unknown"] if rest else [])}


def svg_escape(s):
    return html.escape("" if s is None else str(s), quote=True)


def build_svg(spec, layout):
    W, H = layout["canvas"]["w"], layout["canvas"]["h"]
    FONT = "Arial, Helvetica, 'Noto Sans', 'Noto Sans CJK SC', sans-serif"
    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
                 'viewBox="0 0 %d %d" font-family="%s">' % (W, H, W, H, FONT))
    used_rtypes = sorted({e["rel"].get("type") for e in layout["edges"]
                          if e["rel"].get("type") in RELATION_STYLE})
    parts.append('<defs>')
    for t in used_rtypes:
        st = RELATION_STYLE[t]
        if st["marker"] == "arrow":
            parts.append('<marker id="mk-arrow-%s" viewBox="0 0 10 10" refX="9" refY="5" '
                         'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                         '<path d="M0,0 L10,5 L0,10 z" fill="%s"/></marker>' % (t, st["color"]))
        elif st["marker"] == "tbar":
            parts.append('<marker id="mk-tbar-%s" viewBox="0 0 10 10" refX="9" refY="5" '
                         'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                         '<path d="M0,0 L0,10" stroke="%s" stroke-width="3"/></marker>' % (t, st["color"]))
    parts.append('</defs>')
    parts.append('<rect x="0" y="0" width="%d" height="%d" fill="#FFFFFF"/>' % (W, H))
    parts.append('<g id="layer-background">')
    title = svg_escape(spec.get("title") or "Biological mechanism / pathway")
    parts.append('<text x="24" y="30" font-size="18" font-weight="bold" fill="#202124">%s</text>' % title)
    for b in layout["bands"]:
        fill = "#FAFBFC" if b["name"] != "unknown" else "#FFFFFF"
        parts.append('<rect x="%d" y="%d" width="%d" height="%d" fill="%s" stroke="#DADCE0" '
                     'stroke-width="1"/>' % (b["x0"], b["y0"], b["x1"] - b["x0"], b["y1"] - b["y0"], fill))
        parts.append('<text x="%d" y="%d" font-size="12" fill="#5F6368" font-style="italic">%s</text>'
                     % (b["x0"] + 8, b["y0"] + 16, svg_escape(b["name"])))
    parts.append('</g>')
    parts.append('<g id="layer-edges">')
    for e in layout["edges"]:
        rtype = e["rel"].get("type") or "binding"
        st = RELATION_STYLE[rtype]
        dash = ' stroke-dasharray="%s"' % st["dash"] if st["dash"] else ""
        mid = ""
        if st["marker"] == "arrow":
            mid = 'marker-end="url(#mk-arrow-%s)"' % rtype
        elif st["marker"] == "tbar":
            mid = 'marker-end="url(#mk-tbar-%s)"' % rtype
        src = "; ".join(s.get("locator", "") for s in (e["rel"].get("sources") or []))
        quote = next((s.get("quote", "") for s in (e["rel"].get("sources") or []) if s.get("quote")), "")
        parts.append('<path d="M%d,%d L%d,%d" fill="none" stroke="%s" stroke-width="2"%s %s '
                     'data-rid="%s" data-src="%s" data-quote="%s"><title>%s: %s</title></path>'
                     % (e["x1"], e["y1"], e["x2"], e["y2"], st["color"], dash, mid,
                        svg_escape(e["rid"]), svg_escape(src), svg_escape(quote),
                        svg_escape(rtype), svg_escape(quote)))
    parts.append('</g>')
    parts.append('<g id="layer-nodes">')
    for nd in layout["nodes"]:
        e = nd["entity"]
        st = NODE_STYLE.get(e.get("kind"), NODE_STYLE["other"])
        italic = ' font-style="italic"' if e.get("kind") == "gene" else ""
        x, y, w, h = nd["x"], nd["y"], nd["w"], nd["h"]
        if st["shape"] == "hexagon":
            pts = "%d,%d %d,%d %d,%d %d,%d %d,%d %d,%d" % (
                x + w * 0.25, y, x + w * 0.75, y, x + w, y + h * 0.5,
                x + w * 0.75, y + h, x + w * 0.25, y + h, x, y + h * 0.5)
            body = '<polygon points="%s" fill="%s" stroke="%s" stroke-width="1.2"/>' % (
                pts, st["fill"], st["stroke"])
        elif st["shape"] == "ellipse":
            body = '<ellipse cx="%d" cy="%d" rx="%d" ry="%d" fill="%s" stroke="%s" stroke-width="1.2"/>' % (
                nd["cx"], nd["cy"], w // 2, h // 2, st["fill"], st["stroke"])
        else:
            rx = 0 if st["shape"] == "rectangle" else 8
            body = '<rect x="%d" y="%d" width="%d" height="%d" rx="%d" fill="%s" stroke="%s" stroke-width="1.2"/>' % (
                x, y, w, h, rx, st["fill"], st["stroke"])
        src = "; ".join(s.get("locator", "") for s in (e.get("sources") or []))
        quote = next((s.get("quote", "") for s in (e.get("sources") or []) if s.get("quote")), "")
        parts.append('<g data-eid="%s" data-kind="%s" data-src="%s" data-quote="%s">%s'
                     '<text x="%d" y="%d" font-size="13" text-anchor="middle"%s fill="#202124">%s</text>'
                     '<title>%s (%s): %s</title></g>'
                     % (svg_escape(e.get("id") or ""), svg_escape(e.get("kind") or ""),
                        svg_escape(src), svg_escape(quote), body, nd["cx"], nd["cy"] + 4,
                        italic, svg_escape(e.get("label") or ""), svg_escape(e.get("label") or ""),
                        svg_escape(e.get("kind") or ""), svg_escape(quote)))
    parts.append('</g>')
    parts.append('<g id="layer-legend">')
    ly = layout["canvas"]["h"] - 26 * sum(len(items or [""]) for _, items in layout["legend_rows"]) - 20
    parts.append('<text x="24" y="%d" font-size="14" font-weight="bold" fill="#202124">Legend</text>' % (ly - 8))
    ly += 14
    for kind, items in layout["legend_rows"]:
        for it in items:
            if kind == "relation":
                st = RELATION_STYLE[it]
                dash = ' stroke-dasharray="%s"' % st["dash"] if st["dash"] else ""
                parts.append('<line x1="34" y1="%d" x2="64" y2="%d" stroke="%s" stroke-width="2"%s/>'
                             % (ly, ly, st["color"], dash))
                parts.append('<text x="72" y="%d" font-size="12" fill="#202124">%s</text>'
                             % (ly + 4, RELATION_LABEL[it]))
            elif kind == "kind":
                st = NODE_STYLE.get(it, NODE_STYLE["other"])
                parts.append('<rect x="34" y="%d" width="30" height="12" fill="%s" stroke="%s"/>'
                             % (ly - 8, st["fill"], st["stroke"]))
                parts.append('<text x="72" y="%d" font-size="12" fill="#202124">%s</text>'
                             % (ly + 4, KIND_LABEL.get(it, it)))
            else:
                dm = it if isinstance(it, dict) else {}
                parts.append('<text x="34" y="%d" font-size="12" fill="#202124">%s: %s -> %s</text>'
                             % (ly + 4, dm.get("channel", "?"), dm.get("data", "?"),
                                dm.get("encoding", "?")))
            ly += 26
    parts.append('</g>')
    parts.append('</svg>')
    return "\n".join(parts)


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def pdf_escape(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def ascii_label(raw, fallback):
    if raw is None:
        raw = ""
    raw = str(raw)
    if all(32 <= ord(ch) <= 126 for ch in raw):
        return raw, True
    out = []
    for ch in raw:
        out.append(ch if 32 <= ord(ch) <= 126 else "?")
    return (out and "".join(out)) or str(fallback), False


def build_pdf(spec, layout):
    W, H = layout["canvas"]["w"], layout["canvas"]["h"]
    cmds = []
    font_issues = False

    def p(s):
        cmds.append(s)

    def Y(y):
        return H - y

    p("1 1 1 rg 0 0 %d %d re f" % (W, H))
    p("0.85 0.86 0.88 RG 1 w")
    for b in layout["bands"]:
        p("0.98 0.98 0.99 rg %d %d %d %d re f" % (b["x0"], Y(b["y1"]), b["x1"] - b["x0"], b["y1"] - b["y0"]))
        p("%d %d %d %d re S" % (b["x0"], Y(b["y1"]), b["x1"] - b["x0"], b["y1"] - b["y0"]))
        lab, ok = ascii_label(b["name"], b["name"])
        font_issues = font_issues or (not ok)
        p("BT /F1 10 Tf %d %d Td (%s) Tj ET" % (b["x0"] + 8, Y(b["y0"]) - 14, pdf_escape(lab)))
    tlab, ok = ascii_label(spec.get("title") or "Biological mechanism / pathway", "figure")
    font_issues = font_issues or (not ok)
    p("0.13 0.13 0.15 rg BT /F1 14 Tf 24 %d Td (%s) Tj ET" % (Y(30) + 4, pdf_escape(tlab)))
    for e in layout["edges"]:
        rtype = e["rel"].get("type") or "binding"
        st = RELATION_STYLE[rtype]
        r, g, b = hex_to_rgb(st["color"])
        p("%.3f %.3f %.3f RG 2 w" % (r, g, b))
        if st["dash"]:
            p("[%s] 0 d" % st["dash"].replace(",", " "))
        x1, y1, x2, y2 = e["x1"], Y(e["y1"]), e["x2"], Y(e["y2"])
        p("%d %d m %d %d l S" % (x1, y1, x2, y2))
        if st["marker"] in ("arrow", "double"):
            dx, dy = x2 - x1, y2 - y1
            L = math.hypot(dx, dy) or 1.0
            ux, uy = dx / L, dy / L
            px, py = -uy, ux
            size = 9
            bx0, by0 = x2 - size * ux, y2 - size * uy
            tri = [(x2, y2), (bx0 + size * 0.5 * px, by0 + size * 0.5 * py),
                   (bx0 - size * 0.5 * px, by0 - size * 0.5 * py)]
            p("%.1f %.1f m %.1f %.1f l %.1f %.1f l h f" % (tri[0][0], tri[0][1], tri[1][0],
                tri[1][1], tri[2][0], tri[2][1]))
        if st["marker"] == "tbar":
            dx, dy = x2 - x1, y2 - y1
            L = math.hypot(dx, dy) or 1.0
            ux, uy = dx / L, dy / L
            px, py = -uy, ux
            size = 6
            bx0, by0 = x2 - size * ux, y2 - size * uy
            p("%.1f %.1f m %.1f %.1f l S" % (bx0 + size * px, by0 + size * py,
                                             bx0 - size * px, by0 - size * py))
        if st["dash"]:
            p("[] 0 d")
    for nd in layout["nodes"]:
        e = nd["entity"]
        st = NODE_STYLE.get(e.get("kind"), NODE_STYLE["other"])
        r, g, b = hex_to_rgb(st["fill"])
        sr, sg, sb = hex_to_rgb(st["stroke"])
        x, y, w, h = nd["x"], Y(nd["y"]) - nd["h"], nd["w"], nd["h"]
        p("%.3f %.3f %.3f rg %d %d %d %d re f" % (r, g, b, x, y, w, h))
        p("%.3f %.3f %.3f RG 1.2 w %d %d %d %d re S" % (sr, sg, sb, x, y, w, h))
        lab, ok = ascii_label(e.get("label"), e.get("id"))
        font_issues = font_issues or (not ok)
        fs = 9
        tw = len(lab) * fs * 0.5
        p("0.13 0.13 0.15 rg BT /F1 %d Tf %d %d Td (%s) Tj ET" % (
            fs, nd["cx"] - tw / 2, y + h * 0.35, pdf_escape(lab)))
    p("0.13 0.13 0.15 rg BT /F1 12 Tf 24 %d Td (Legend) Tj ET" % (Y(layout["canvas"]["h"] - 30),))
    ly = layout["canvas"]["h"] - 56
    for kind, items in layout["legend_rows"]:
        for it in items:
            if kind == "relation":
                lab = RELATION_LABEL[it]
            elif kind == "kind":
                lab = KIND_LABEL.get(it, it)
            else:
                dm = it if isinstance(it, dict) else {}
                lab = "%s: %s -> %s" % (dm.get("channel", "?"), dm.get("data", "?"), dm.get("encoding", "?"))
            lab, ok = ascii_label(lab, "item")
            font_issues = font_issues or (not ok)
            p("BT /F1 9 Tf 34 %d Td (%s) Tj ET" % (Y(ly), pdf_escape(lab)))
            ly -= 20
    content = "\n".join(cmds).encode("latin-1", errors="replace")
    objs = []
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Contents 4 0 R "
                b"/Resources << /Font << /F1 5 0 R >> >> >>" % (W, H))
    objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref_pos = len(out)
    n = len(objs) + 1
    out += b"xref\n0 %d\n" % n
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (n, xref_pos)
    return bytes(out), font_issues


def build_legend(layout):
    legend = []
    for kind, items in layout["legend_rows"]:
        for it in items:
            if kind == "relation":
                st = RELATION_STYLE[it]
                legend.append({"key": it, "meaning": RELATION_LABEL[it],
                               "style": {"color": st["color"], "marker": st["marker"], "dash": st["dash"]}})
            elif kind == "kind":
                st = NODE_STYLE.get(it, NODE_STYLE["other"])
                legend.append({"key": it, "meaning": KIND_LABEL.get(it, it),
                               "style": {"fill": st["fill"], "stroke": st["stroke"], "shape": st["shape"]}})
            else:
                dm = it if isinstance(it, dict) else {}
                legend.append({"key": dm.get("channel", "?"), "meaning": dm.get("encoding", ""),
                               "style": {"data": dm.get("data", "")}})
    return legend
