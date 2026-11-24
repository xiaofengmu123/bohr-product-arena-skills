#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic, layered SVG builder for biological mechanism/pathway figures.

Standard library only. Reads a mechanism specification (spec.json) and writes a
single-file SVG editable in Inkscape/draw.io: named layers, a distinct arrowhead
per relation type, compartment bands, a legend generated from the styles actually
used (so the legend cannot disagree with the figure), and data-source traceability
attributes (locator + verbatim quote) on every entity and relation.

Usage:
    python scripts/build_svg.py --spec /app/work/spec.json --out /app/output/mechanism.svg
    python scripts/build_svg.py --spec spec.json --out figure.svg --size 1200x800
    python scripts/build_svg.py --spec spec.json --out figure.svg --report report.json
Exit codes: 0 = ok, 1 = blocking spec error, 2 = warnings only.
"""
import argparse
import html
import json
import math
import os
import sys

SCHEMA_VERSION = "1.0.0"

ENTITY_TYPES = (
    "gene", "protein", "complex", "family", "receptor",
    "metabolite", "cell_type", "compartment",
)
RELATION_TYPES = (
    "activation", "inhibition", "binding", "modification", "transport", "conversion",
)

# shape/color per entity type. "italic" encodes gene-vs-protein writing convention.
ENTITY_STYLE = {
    "gene":        {"shape": "ellipse",    "fill": "#BFD7EA", "stroke": "#1B4F72", "italic": True},
    "protein":     {"shape": "rect",       "fill": "#D5E8D4", "stroke": "#1E8449", "italic": False},
    "complex":     {"shape": "hexagon",    "fill": "#FAD7A0", "stroke": "#B9770E", "italic": False},
    "family":      {"shape": "roundrect",  "fill": "#E8DAEF", "stroke": "#6C3483", "italic": False, "dash": "6,4"},
    "receptor":    {"shape": "roundrect",  "fill": "#D4E6F1", "stroke": "#2874A6", "italic": False},
    "metabolite":  {"shape": "circle",     "fill": "#FCF3CF", "stroke": "#B7950B", "italic": False},
    "cell_type":   {"shape": "roundrect",  "fill": "#FADBD8", "stroke": "#943126", "italic": False},
    "compartment": {"shape": "band",       "fill": "#F4F6F7", "stroke": "#839192", "italic": False},
    "default":     {"shape": "rect",       "fill": "#ECF0F1", "stroke": "#7F8C8D", "italic": False},
}

# marker kind + color + dash per relation type. marker "none" means no arrowhead.
RELATION_STYLE = {
    "activation":   {"marker": "arrow",   "color": "#1E8449", "dash": None,  "width": 2.2},
    "inhibition":   {"marker": "bar",     "color": "#943126", "dash": None,  "width": 2.2},
    "binding":      {"marker": "none",    "color": "#6C3483", "dash": "5,3", "width": 2.0},
    "modification": {"marker": "open",    "color": "#B9770E", "dash": None,  "width": 2.2},
    "transport":    {"marker": "chevron", "color": "#2874A6", "dash": "2,3", "width": 2.2},
    "conversion":   {"marker": "arrow",   "color": "#7F8C8D", "dash": None,  "width": 2.2, "double": True},
    "default":      {"marker": "arrow",   "color": "#5D6D7E", "dash": None,  "width": 2.0},
}

CANONICAL_COMPARTMENTS = {
    "extracellular": 0, "membrane": 1, "cytosol": 2, "nucleus": 3,
    "mitochondrion": 4, "endosome": 4, "lysosome": 4,
}
DEFAULT_COMPARTMENT_LABELS = {
    "extracellular": "Extracellular", "membrane": "Membrane",
    "cytosol": "Cytosol", "nucleus": "Nucleus",
}

FONT_STACK = "Arial, 'Helvetica Neue', Helvetica, sans-serif"


# --------------------------------------------------------------------------- #
# spec validation
# --------------------------------------------------------------------------- #
def validate_spec(spec):
    """Return (errors, warnings). Blocking errors forbid rendering; warnings are
    recorded but the figure still builds (used for early incremental skeletons)."""
    errors, warnings = [], []
    if not isinstance(spec, dict):
        return ["spec must be a JSON object"], []
    if spec.get("schema_version") not in (None, SCHEMA_VERSION):
        warnings.append(
            "schema_version %r differs from supported %s"
            % (spec.get("schema_version"), SCHEMA_VERSION)
        )
    entities = spec.get("entities")
    if not isinstance(entities, list):
        errors.append("entities must be a list")
        entities = []
    ids = set()
    for i, e in enumerate(entities):
        if not isinstance(e, dict):
            errors.append("entities[%d] must be an object" % i)
            continue
        eid = e.get("id")
        if not eid:
            errors.append("entities[%d] missing id" % i)
            continue
        if eid in ids:
            errors.append("duplicate entity id %r" % eid)
        ids.add(eid)
        if not e.get("label"):
            warnings.append("entity %r has no label" % eid)
        etype = e.get("type") or "default"
        if etype not in ENTITY_TYPES and etype != "default":
            errors.append("entity %r has unknown type %r" % (eid, etype))
        src = e.get("source")
        if not (isinstance(src, dict) and src.get("locator") and src.get("quote")):
            warnings.append(
                "entity %r missing source.locator/quote (traceability required)" % eid
            )
    relations = spec.get("relations")
    if not isinstance(relations, list):
        errors.append("relations must be a list")
        relations = []
    seen_edges = set()
    for i, r in enumerate(relations):
        if not isinstance(r, dict):
            errors.append("relations[%d] must be an object" % i)
            continue
        rid = r.get("id") or ("r%d" % i)
        for endpoint in ("from", "to"):
            v = r.get(endpoint)
            if v not in ids:
                errors.append("relation %r references unknown entity %s=%r" % (rid, endpoint, v))
        rtype = r.get("type") or "default"
        if rtype not in RELATION_TYPES and rtype != "default":
            errors.append("relation %r has unknown type %r" % (rid, rtype))
        edge_key = (r.get("from"), r.get("to"), rtype)
        if edge_key in seen_edges:
            warnings.append("relation %r duplicates edge %s" % (rid, edge_key))
        seen_edges.add(edge_key)
        src = r.get("source")
        if not (isinstance(src, dict) and src.get("locator") and src.get("quote")):
            warnings.append(
                "relation %r missing source.locator/quote (traceability required)" % rid
            )
    return errors, warnings


# --------------------------------------------------------------------------- #
# geometry / layout
# --------------------------------------------------------------------------- #
def _attr(s):
    if s is None:
        return ""
    return html.escape(str(s), quote=True).replace("\n", " ").replace("\r", " ")


def _text(s):
    if s is None:
        return ""
    return html.escape(str(s))


def _node_size(entity):
    label = str(entity.get("label") or entity.get("id") or "?")
    w = min(230, max(74, 9 * len(label) + 28))
    h = 40
    if entity.get("type") in ("gene", "metabolite"):
        h = 36
    return float(w), float(h)


def _compartment_order(cid, decl_map):
    if cid in CANONICAL_COMPARTMENTS:
        return CANONICAL_COMPARTMENTS[cid]
    c = decl_map.get(cid)
    if isinstance(c, dict):
        o = c.get("order")
        if isinstance(o, (int, float)):
            return float(o)
    return 1000.0


def compute_layout(spec, width, height):
    """Deterministic layout: compartments stack top-to-bottom, entities flow
    left-to-right inside each compartment band. Explicit x/y in an entity wins."""
    entities = spec.get("entities") or []
    layout_cfg = spec.get("layout") or {}
    legend_panel = 200
    margin = 36
    title_h = 40
    main_x0 = margin
    main_width = width - 2 * margin - legend_panel - 20

    decl = layout_cfg.get("compartments") or []
    decl_map = {}
    for c in decl:
        if isinstance(c, dict):
            key = c.get("id") or c.get("label")
            if key:
                decl_map[key] = c

    comp_entities = {}
    for e in entities:
        comp = e.get("compartment") or "cytosol"
        comp_entities.setdefault(comp, []).append(e)

    all_comps = set(decl_map.keys()) | set(comp_entities.keys())
    ordered = sorted(all_comps, key=lambda c: (_compartment_order(c, decl_map), str(c)))

    sizes = {}
    for e in entities:
        sizes[e.get("id")] = _node_size(e)
    max_w = max([w for w, _h in sizes.values()] + [74.0])
    node_h_max = max([h for _w, h in sizes.values()] + [40.0])
    step_x = max_w + 26.0

    y = margin + title_h
    bands = []
    positions = {}
    for cid in ordered:
        ents = comp_entities.get(cid, [])
        capacity = max(1, int(main_width // step_x))
        rows = max(1, math.ceil(len(ents) / capacity)) if ents else 1
        label_h = 22.0
        band_h = label_h + rows * (node_h_max + 22.0) + 12.0
        band = {
            "id": cid,
            "label": (decl_map.get(cid) or {}).get("label") or DEFAULT_COMPARTMENT_LABELS.get(cid, cid),
            "y0": y,
            "y1": y + band_h,
        }
        bands.append(band)
        for i, e in enumerate(ents):
            eid = e.get("id")
            if isinstance(e.get("x"), (int, float)) and isinstance(e.get("y"), (int, float)):
                positions[eid] = (float(e["x"]), float(e["y"]))
                continue
            w, h = sizes.get(eid, (74.0, 40.0))
            col = i % capacity
            row = i // capacity
            ex = main_x0 + col * step_x + w / 2.0
            ey = y + label_h + row * (node_h_max + 22.0) + h / 2.0
            positions[eid] = (ex, ey)
        y += band_h

    required_h = y + margin
    height = max(height, int(required_h))
    return {
        "bands": bands, "positions": positions, "sizes": sizes,
        "main_x0": main_x0, "main_width": main_width,
        "width": width, "height": height, "legend_panel": legend_panel,
        "margin": margin, "title_h": title_h,
    }


def _exit_point(cx, cy, w, h, dx, dy):
    if dx == 0 and dy == 0:
        return cx, cy
    hw = w / 2.0
    hh = h / 2.0
    tx = hw / abs(dx) if dx != 0 else float("inf")
    ty = hh / abs(dy) if dy != 0 else float("inf")
    t = min(tx, ty)
    return cx + dx * t, cy + dy * t


# --------------------------------------------------------------------------- #
# rendering helpers
# --------------------------------------------------------------------------- #
def _marker_svg(mid, kind, color):
    if kind == "arrow":
        return ('<marker id="%s" viewBox="0 0 10 10" refX="8.6" refY="5" '
                'markerWidth="7.5" markerHeight="7.5" orient="auto-start-reverse">'
                '<path d="M0,0 L10,5 L0,10 Z" fill="%s"/></marker>' % (mid, color))
    if kind == "bar":
        return ('<marker id="%s" viewBox="0 0 10 10" refX="8" refY="5" '
                'markerWidth="9" markerHeight="9" orient="auto">'
                '<path d="M8,1 L8,9" stroke="%s" stroke-width="2.6" '
                'stroke-linecap="round"/></marker>' % (mid, color))
    if kind == "open":
        return ('<marker id="%s" viewBox="0 0 10 10" refX="8.6" refY="5" '
                'markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
                '<path d="M0,0 L10,5 L0,10" fill="none" stroke="%s" '
                'stroke-width="1.6"/></marker>' % (mid, color))
    if kind == "chevron":
        return ('<marker id="%s" viewBox="0 0 10 10" refX="8.6" refY="5" '
                'markerWidth="8.5" markerHeight="8.5" orient="auto-start-reverse">'
                '<path d="M1,1 L10,5 L1,9" fill="none" stroke="%s" '
                'stroke-width="2.2"/></marker>' % (mid, color))
    return ""


def _render_entity(e, positions, sizes):
    eid = e.get("id")
    x, y = positions[eid]
    w, h = sizes.get(eid, (74.0, 40.0))
    label = e.get("label") or e.get("id") or "?"
    etype = e.get("type") or "default"
    st = ENTITY_STYLE.get(etype, ENTITY_STYLE["default"])
    fill = e.get("fill") or st["fill"]
    stroke = e.get("stroke") or st["stroke"]
    shape = e.get("shape") or st["shape"]
    italic = bool(e.get("italic", st.get("italic", False)))
    dash = e.get("dash") or st.get("dash")

    if shape == "ellipse":
        body = ('<ellipse cx="%s" cy="%s" rx="%s" ry="%s" fill="%s" stroke="%s" '
                'stroke-width="1.6"/>' % (x, y, w / 2.0, h / 2.0, fill, stroke))
    elif shape == "circle":
        r = max(w, h) / 2.0
        body = ('<circle cx="%s" cy="%s" r="%s" fill="%s" stroke="%s" '
                'stroke-width="1.6"/>' % (x, y, r, fill, stroke))
    elif shape == "hexagon":
        pts = []
        for k in range(6):
            a = math.radians(60 * k)
            px = x + (w / 2.0) * math.cos(a)
            py = y + (h / 2.0) * math.sin(a)
            pts.append("%.2f,%.2f" % (px, py))
        body = ('<polygon points="%s" fill="%s" stroke="%s" stroke-width="1.6"/>'
                % (" ".join(pts), fill, stroke))
    else:
        rr = ' rx="8" ry="8"' if shape in ("rect", "roundrect", "roundrect_dash") else ""
        dash_attr = ' stroke-dasharray="%s"' % dash if dash else ""
        body = ('<rect x="%s" y="%s" width="%s" height="%s"%s fill="%s" stroke="%s" '
                'stroke-width="1.6"%s/>'
                % (x - w / 2.0, y - h / 2.0, w, h, rr, fill, stroke, dash_attr))

    tm = ""
    if e.get("transmembrane") and etype == "receptor":
        tm = ('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="1.4"/>'
              % (x, y - h / 2.0 + 4.0, x, y + h / 2.0 - 4.0, stroke))

    fs = 12 if len(str(label)) <= 14 else (11 if len(str(label)) <= 22 else 10)
    fstyle = "italic" if italic else "normal"
    text = ('<text x="%s" y="%s" text-anchor="middle" font-family="%s" font-size="%s" '
            'font-style="%s" fill="#1B2631">%s</text>'
            % (x, y + 4.0, FONT_STACK, fs, fstyle, _text(label)))

    src = e.get("source") or {}
    loc = _attr(src.get("locator"))
    quote = _attr(src.get("quote"))
    title = _text("%s | %s | %s | %s"
                  % (eid, label, src.get("locator", ""), src.get("quote", "")))
    return ('<g data-entity-id="%s" data-entity-type="%s" data-source-locator="%s" '
            'data-source-quote="%s"><title>%s</title>%s%s%s</g>'
            % (_attr(eid), _attr(etype), loc, quote, title, body, tm, text))


def _render_relation(r, positions, sizes, used_markers, mid_labels):
    src_id = r.get("from")
    dst_id = r.get("to")
    if src_id not in positions or dst_id not in positions:
        return ""
    x1, y1 = positions[src_id]
    x2, y2 = positions[dst_id]
    w1, h1 = sizes.get(src_id, (74.0, 40.0))
    w2, h2 = sizes.get(dst_id, (74.0, 40.0))
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        dy = -1.0
    ex1 = _exit_point(x1, y1, w1, h1, dx, dy)
    ex2 = _exit_point(x2, y2, w2, h2, -dx, -dy)

    rtype = r.get("type") or "default"
    st = RELATION_STYLE.get(rtype, RELATION_STYLE["default"])
    color = r.get("color") or r.get("stroke") or st["color"]
    width = float(r.get("width") or st["width"])
    dash = r.get("dash") if r.get("dash") is not None else st["dash"]
    marker_kind = st["marker"]
    double = bool(st.get("double"))

    mid = None
    if marker_kind != "none":
        key = (marker_kind, color)
        mid = used_markers.setdefault(key, "m-%d" % len(used_markers))

    dash_attr = ' stroke-dasharray="%s"' % dash if dash else ""
    marker_attr = ' marker-end="url(#%s)"' % mid if mid else ""

    parts = []
    if double:
        ddx = ex2[0] - ex1[0]
        ddy = ex2[1] - ex1[1]
        length = math.hypot(ddx, ddy) or 1.0
        px = -ddy / length * 3.4
        py = ddx / length * 3.4
        for sign in (1.0, -1.0):
            xa, ya = ex1[0] + px * sign, ex1[1] + py * sign
            xb, yb = ex2[0] + px * sign, ex2[1] + py * sign
            parts.append(
                '<path d="M%s,%s L%s,%s" fill="none" stroke="%s" stroke-width="%s"%s%s/>'
                % (xa, ya, xb, yb, color, width, dash_attr, marker_attr)
            )
    else:
        parts.append(
            '<path d="M%s,%s L%s,%s" fill="none" stroke="%s" stroke-width="%s"%s%s/>'
            % (ex1[0], ex1[1], ex2[0], ex2[1], color, width, dash_attr, marker_attr)
        )
    if marker_kind == "none":  # binding: contact dots at both ends
        for px, py in (ex1, ex2):
            parts.append('<circle cx="%s" cy="%s" r="2.8" fill="%s"/>' % (px, py, color))

    # edge label (modifier such as P / Ub / "recruit")
    lbl = r.get("label") or r.get("modifier")
    if lbl:
        mx = (ex1[0] + ex2[0]) / 2.0
        my = (ex1[1] + ex2[1]) / 2.0
        ddx = ex2[0] - ex1[0]
        ddy = ex2[1] - ex1[1]
        length = math.hypot(ddx, ddy) or 1.0
        ox = -ddy / length * 9.0
        oy = ddx / length * 9.0
        mid_labels.append(
            '<text x="%s" y="%s" text-anchor="middle" font-family="%s" font-size="10" '
            'fill="%s">%s</text>' % (mx + ox, my + oy + 3.0, FONT_STACK, color, _text(lbl))
        )

    src = r.get("source") or {}
    rid = _attr(r.get("id") or "")
    loc = _attr(src.get("locator"))
    quote = _attr(src.get("quote"))
    return ('<g data-relation-id="%s" data-relation-type="%s" data-source-locator="%s" '
            'data-source-quote="%s">%s</g>'
            % (rid, _attr(rtype), loc, quote, "".join(parts)))


def _legend_entity_sample(etype, cx, cy):
    st = ENTITY_STYLE.get(etype, ENTITY_STYLE["default"])
    fill, stroke = st["fill"], st["stroke"]
    w, h = 16.0, 12.0
    if st["shape"] == "ellipse":
        return ('<ellipse cx="%s" cy="%s" rx="%s" ry="%s" fill="%s" stroke="%s" '
                'stroke-width="1.2"/>' % (cx, cy, w / 2.0, h / 2.0, fill, stroke))
    if st["shape"] == "circle":
        return ('<circle cx="%s" cy="%s" r="%s" fill="%s" stroke="%s" stroke-width="1.2"/>'
                % (cx, cy, max(w, h) / 2.0, fill, stroke))
    if st["shape"] == "hexagon":
        pts = []
        for k in range(6):
            a = math.radians(60 * k)
            pts.append("%.1f,%.1f" % (cx + (w / 2.0) * math.cos(a),
                                      cy + (h / 2.0) * math.sin(a)))
        return ('<polygon points="%s" fill="%s" stroke="%s" stroke-width="1.2"/>'
                % (" ".join(pts), fill, stroke))
    return ('<rect x="%s" y="%s" width="%s" height="%s" rx="3" fill="%s" stroke="%s" '
            'stroke-width="1.2"/>' % (cx - w / 2.0, cy - h / 2.0, w, h, fill, stroke))


def _render_legend(spec, layout, used_entity_types, used_relation_types, used_markers):
    x0 = layout["width"] - layout["legend_panel"] + 12
    y = layout["margin"] + layout["title_h"] + 4
    line_h = 22
    parts = ['<rect x="%s" y="%s" width="%s" height="10000" fill="white" '
             'fill-opacity="0.55"/>' % (x0 - 12, y - 10, layout["legend_panel"] - 8)]
    parts.append('<text x="%s" y="%s" font-family="%s" font-size="14" '
                 'font-weight="bold" fill="#1B2631">Legend</text>' % (x0, y, FONT_STACK))
    y += line_h + 6
    parts.append('<text x="%s" y="%s" font-family="%s" font-size="11" '
                 'font-weight="bold" fill="#5D6D7E">Entities</text>' % (x0, y, FONT_STACK))
    y += line_h
    for etype in used_entity_types:
        parts.append(_legend_entity_sample(etype, x0 + 10, y - 4))
        italic = ENTITY_STYLE.get(etype, ENTITY_STYLE["default"]).get("italic", False)
        note = " (gene, italic)" if italic else ""
        parts.append('<text x="%s" y="%s" font-family="%s" font-size="11" fill="#1B2631">'
                     '%s%s</text>' % (x0 + 24, y, FONT_STACK, _text(etype), note))
        y += line_h
    y += 4
    parts.append('<text x="%s" y="%s" font-family="%s" font-size="11" '
                 'font-weight="bold" fill="#5D6D7E">Relations</text>' % (x0, y, FONT_STACK))
    y += line_h
    for rtype in used_relation_types:
        st = RELATION_STYLE.get(rtype, RELATION_STYLE["default"])
        color = st["color"]
        key = (st["marker"], color)
        mid = used_markers.setdefault(key, "m-%d" % len(used_markers))
        dash_attr = ' stroke-dasharray="%s"' % st["dash"] if st["dash"] else ""
        marker_attr = ' marker-end="url(#%s)"' % mid if st["marker"] != "none" else ""
        parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="2"%s%s/>'
                     % (x0 + 2, y - 4, x0 + 26, y - 4, color, dash_attr, marker_attr))
        parts.append('<text x="%s" y="%s" font-family="%s" font-size="11" fill="#1B2631">'
                     '%s</text>' % (x0 + 34, y, FONT_STACK, _text(rtype)))
        y += line_h
    maps = spec.get("data_mappings") or []
    if maps:
        y += 4
        parts.append('<text x="%s" y="%s" font-family="%s" font-size="11" '
                     'font-weight="bold" fill="#5D6D7E">Data</text>' % (x0, y, FONT_STACK))
        y += line_h
        for m in maps:
            if not isinstance(m, dict):
                continue
            chan = m.get("channel") or "style"
            field = m.get("field") or ""
            parts.append('<rect x="%s" y="%s" width="14" height="10" rx="2" fill="%s" '
                         'stroke="#5D6D7E"/>'
                         % (x0 + 2, y - 9, m.get("sample") or "#95A5A6"))
            parts.append('<text x="%s" y="%s" font-family="%s" font-size="10.5" '
                         'fill="#1B2631">%s: %s</text>'
                         % (x0 + 24, y, FONT_STACK, _text(chan), _text(field)))
            y += line_h
    notes = (spec.get("legend") or {}).get("notes")
    if notes:
        y += 4
        parts.append('<text x="%s" y="%s" font-family="%s" font-size="10" '
                     'fill="#5D6D7E">%s</text>' % (x0, y, FONT_STACK, _text(notes)))
    return "".join(parts)


# --------------------------------------------------------------------------- #
# build entry
# --------------------------------------------------------------------------- #
def build_svg(spec, out_path, size=None, report_path=None):
    errors, warnings = validate_spec(spec)
    if report_path:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump({"errors": errors, "warnings": warnings}, f, ensure_ascii=False, indent=2)
    if errors:
        raise ValueError("; ".join(errors[:5]) + (" ..." if len(errors) > 5 else ""))

    width, height = 1120, 760
    if size:
        try:
            w, h = size.lower().replace(",", "x").split("x")
            width, height = int(w), int(h)
        except ValueError:
            warnings.append("bad --size %r; using default" % size)

    layout = compute_layout(spec, width, height)
    width, height = layout["width"], layout["height"]
    entities = spec.get("entities") or []
    relations = spec.get("relations") or []

    positions = layout["positions"]
    sizes = layout["sizes"]

    used_entity_types = []
    for e in entities:
        etype = e.get("type") or "default"
        if etype not in used_entity_types:
            used_entity_types.append(etype)
    used_relation_types = []
    for r in relations:
        rtype = r.get("type") or "default"
        if rtype not in used_relation_types:
            used_relation_types.append(rtype)

    # compartments
    comp_parts = []
    for band in layout["bands"]:
        comp_parts.append(
            '<rect x="%s" y="%s" width="%s" height="%s" fill="%s" fill-opacity="0.5" '
            'stroke="%s" stroke-dasharray="4,3" stroke-width="1"/>'
            % (layout["margin"], band["y0"], layout["main_width"], band["y1"] - band["y0"],
               ENTITY_STYLE["compartment"]["fill"], ENTITY_STYLE["compartment"]["stroke"])
        )
        comp_parts.append(
            '<text x="%s" y="%s" font-family="%s" font-size="11" font-weight="bold" '
            'fill="#5D6D7E">%s</text>'
            % (layout["margin"] + 8, band["y0"] + 14, FONT_STACK, _text(band["label"]))
        )

    title = (spec.get("layout") or {}).get("title") or spec.get("title") or spec.get("case_id") or ""
    comp_parts.append(
        '<text x="%s" y="%s" font-family="%s" font-size="18" font-weight="bold" '
        'fill="#1B2631">%s</text>'
        % (layout["margin"], layout["margin"] + 8, FONT_STACK, _text(title))
    )

    used_markers = {}
    mid_labels = []
    entity_svg = "".join(_render_entity(e, positions, sizes) for e in entities)
    relation_svg = "".join(
        _render_relation(r, positions, sizes, used_markers, mid_labels) for r in relations
    )
    labels_svg = "".join(mid_labels)
    legend_svg = _render_legend(spec, layout, used_entity_types, used_relation_types, used_markers)
    defs_svg = "".join(
        _marker_svg(mid, kind, color) for (kind, color), mid in sorted(
            used_markers.items(), key=lambda kv: kv[1]
        )
    )

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
        'width="%s" height="%s" viewBox="0 0 %s %s" font-family="%s">'
        % (width, height, width, height, FONT_STACK)
        + '<metadata><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
          '<rdf:Description rdf:about="">'
          '<dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">%s</dc:title>'
          '<dc:source xmlns:dc="http://purl.org/dc/elements/1.1/">%s</dc:source>'
          '</rdf:Description></rdf:RDF></metadata>'
        % (_text(title), _attr(spec.get("case_id") or ""))
        + '<defs>%s</defs>' % defs_svg
        + '<g id="layer-compartments" inkscape:groupmode="layer" inkscape:label="compartments">%s</g>' % "".join(comp_parts)
        + '<g id="layer-entities" inkscape:groupmode="layer" inkscape:label="entities">%s</g>' % entity_svg
        + '<g id="layer-relations" inkscape:groupmode="layer" inkscape:label="relations">%s</g>' % relation_svg
        + '<g id="layer-labels" inkscape:groupmode="layer" inkscape:label="labels">%s</g>' % labels_svg
        + '<g id="layer-legend" inkscape:groupmode="layer" inkscape:label="legend">%s</g>' % legend_svg
        + '</svg>'
    )

    out_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(svg)
    return errors, warnings


def main(argv=None):
    ap = argparse.ArgumentParser(description="Deterministic layered SVG builder")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", default=None, help="WxH, e.g. 1200x800")
    ap.add_argument("--report", default=None, help="write validation report JSON here")
    a = ap.parse_args(argv)
    try:
        with open(a.spec, "r", encoding="utf-8-sig", errors="replace") as f:
            spec = json.load(f)
    except (OSError, ValueError) as e:
        print("cannot read spec: %s" % e, file=sys.stderr)
        return 1
    try:
        errors, warnings = build_svg(spec, a.out, size=a.size, report_path=a.report)
    except ValueError as e:
        print("blocking spec error: %s" % e, file=sys.stderr)
        return 1
    for w in warnings:
        print("warning: %s" % w, file=sys.stderr)
    print("wrote %s (%d entities, %d relations, %d warnings)"
          % (a.out, len(spec.get("entities") or []), len(spec.get("relations") or []),
             len(warnings)))
    return 2 if warnings else 0


if __name__ == "__main__":
    sys.exit(main())
