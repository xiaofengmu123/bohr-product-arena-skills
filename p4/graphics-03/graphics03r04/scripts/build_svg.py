#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Convert a mechanism spec (spec.json) into a layered, editable SVG.

Standard library only. Deterministic: identical input yields byte-identical SVG.
Layers (SVG groups): compartments, relations, entities, labels, legend, data.

Evidence grading (evidence_status) drives rendering:
  direct   -> solid stroke
  inferred -> relations dashed (non-transport), entities solid with data tag
  missing  -> gray, dashed/dotted, trailing "?" (no positive assertion)

Usage:
  python3 scripts/build_svg.py --spec /app/work/spec.json --out /app/output
  python3 scripts/build_svg.py --spec spec.json --out out.svg   # file target also ok
"""
import argparse
import html
import json
import math
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

COMPARTMENT_ORDER = ["extracellular", "membrane", "cytosol", "nucleus", "organelle", "unknown"]
COMPARTMENT_LABELS = {
    "extracellular": "Extracellular",
    "membrane": "Membrane",
    "cytosol": "Cytosol",
    "nucleus": "Nucleus",
    "organelle": "Organelle / other",
    "unknown": "Unknown compartment",
}
COMPARTMENT_ALIASES = {
    "ec": "extracellular", "extracellular_space": "extracellular", "outside": "extracellular",
    "exterior": "extracellular", "extracellular space": "extracellular",
    "pm": "membrane", "plasma_membrane": "membrane", "cell_membrane": "membrane",
    "plasma membrane": "membrane", "cell membrane": "membrane",
    "cytoplasm": "cytosol", "cytosolic": "cytosol", "cyto": "cytosol", "cytoplasmic": "cytosol",
    "nuclear": "nucleus", "nucleoplasm": "nucleus", "nuc": "nucleus",
    "mitochondria": "organelle", "er": "organelle", "endosome": "organelle",
    "lysosome": "organelle", "endoplasmic reticulum": "organelle",
    "golgi": "organelle", "vesicle": "organelle",
}

RELATION_STYLES = {
    "activation":   {"marker": "arrow",  "color": "#202020", "dash": None,       "label": "Activation / promotion"},
    "inhibition":   {"marker": "bar",    "color": "#c0392b", "dash": None,       "label": "Inhibition / suppression"},
    "binding":      {"marker": "dot",    "color": "#1f4e79", "dash": None,       "label": "Binding / interaction"},
    "modification": {"marker": "circle", "color": "#6a329f", "dash": None,       "label": "Modification (P, Ub, ...)"},
    "transport":    {"marker": "open",   "color": "#0f7b5f", "dash": "6,4",      "label": "Transport / translocation"},
    "conversion":   {"marker": "arrow",  "color": "#b45f06", "dash": None,       "label": "Conversion / catalysis"},
    "unknown":      {"marker": "arrow",  "color": "#808080", "dash": "4,4",      "label": "Unknown / unspecified"},
}

ENTITY_STYLES = {
    "gene":          {"shape": "rect", "rx": 6,  "fill": "#ffffff", "stroke": "#404040", "font_style": "italic",  "font_weight": "normal"},
    "protein":       {"shape": "rect", "rx": 20, "fill": "#ffffff", "stroke": "#1f4e79", "font_style": "normal",  "font_weight": "normal"},
    "complex":       {"shape": "rect", "rx": 8,  "fill": "#f2f6fa", "stroke": "#1f4e79", "font_style": "normal",  "font_weight": "bold"},
    "family":        {"shape": "rect", "rx": 6,  "fill": "#ffffff", "stroke": "#6a329f", "font_style": "italic",  "font_weight": "normal"},
    "cell_type":     {"shape": "ellipse", "rx": 46, "fill": "#fdf3e7", "stroke": "#b45f06", "font_style": "normal", "font_weight": "normal"},
    "compartment":   {"shape": "rect", "rx": 4,  "fill": "#f4f4f4", "stroke": "#808080", "font_style": "normal",  "font_weight": "normal"},
    "small_molecule": {"shape": "rect", "rx": 18, "fill": "#fffdf5", "stroke": "#b45f06", "font_style": "normal", "font_weight": "normal"},
    "other":         {"shape": "rect", "rx": 6,  "fill": "#ffffff", "stroke": "#404040", "font_style": "normal",  "font_weight": "normal"},
}

FONT_STACK = "Arial, 'Helvetica Neue', 'Noto Sans', 'Noto Sans CJK SC', 'DejaVu Sans', 'Microsoft YaHei', sans-serif"

# layout constants
MARGIN_LEFT = 150
MARGIN_RIGHT = 36
MARGIN_TOP = 48
NODE_W = 150
NODE_H = 42
H_GAP = 34
V_GAP = 58
BAND_PAD = 24
LEGEND_ROW_H = 26
LEGEND_TOP_PAD = 22


def normalize_compartment(raw):
    if raw is None:
        return "unknown"
    key = str(raw).strip().lower().replace("_", " ")
    key2 = key.replace(" ", "_")
    if key2 in COMPARTMENT_ALIASES:
        return COMPARTMENT_ALIASES[key2]
    if key in COMPARTMENT_ALIASES:
        return COMPARTMENT_ALIASES[key]
    for c in COMPARTMENT_ORDER:
        if key == c or key2 == c or key.replace(" ", "_") == c:
            return c
    return "unknown"


def _clean_text(s):
    return html.escape(str(s), quote=True)


def _fmt(v, nd=1):
    return ("%%.%df" % nd) % float(v)


def _ensure_id(entity, idx):
    eid = entity.get("id")
    if not eid:
        eid = "e%d" % (idx + 1)
        entity["id"] = eid
    return str(eid)


def _first_locator(evidence):
    ev = evidence or []
    for item in ev:
        if isinstance(item, dict) and item.get("locator"):
            return str(item["locator"])
    return ""


def _style_for(entity):
    t = str(entity.get("type", "other")).lower()
    st = ENTITY_STYLES.get(t, ENTITY_STYLES["other"])
    # explicit overrides
    ss = entity.get("symbol_style")
    if ss in ("italic", "normal"):
        st = dict(st)
        st["font_style"] = ss
    if entity.get("font_weight") in ("normal", "bold"):
        st = dict(st)
        st["font_weight"] = entity["font_weight"]
    return st


def _relation_visual(rtype, evidence_status):
    st = RELATION_STYLES.get(str(rtype).lower(), RELATION_STYLES["unknown"])
    st = dict(st)
    if evidence_status == "inferred" and st["dash"] is None:
        st["dash"] = "5,4"
    if evidence_status == "missing":
        st["color"] = "#9a9a9a"
        st["dash"] = st["dash"] or "2,3"
    return st


def _entity_visual(entity):
    st = _style_for(entity)
    st = dict(st)
    if entity.get("evidence_status") == "missing":
        st["stroke"] = "#9a9a9a"
        st["dash"] = "4,4"
    else:
        st["dash"] = None
    return st


def layout(entities):
    """Group entities into compartment bands and compute node centers.

    Returns (positions dict id->(cx,cy), bands list of (comp, top, bottom),
             total_height).
    """
    comps = {}
    for e in entities:
        c = normalize_compartment(e.get("compartment"))
        comps.setdefault(c, []).append(e)
    order = [c for c in COMPARTMENT_ORDER if c in comps]
    order += [c for c in comps if c not in order]

    canvas_w = 1000.0
    usable_w = canvas_w - MARGIN_LEFT - MARGIN_RIGHT
    per_row = max(1, int((usable_w + H_GAP) // (NODE_W + H_GAP)))

    positions = {}
    bands = []
    y = MARGIN_TOP
    for c in order:
        items = sorted(comps[c], key=lambda e: (str(e.get("id", "")),))
        n = len(items)
        n_rows = max(1, math.ceil(n / per_row))
        band_h = 2 * BAND_PAD + n_rows * NODE_H + (n_rows - 1) * V_GAP
        top = y
        bottom = top + band_h
        for i, e in enumerate(items):
            row = i // per_row
            col = i % per_row
            row_n = min(per_row, n - row * per_row)
            row_w = row_n * NODE_W + (row_n - 1) * H_GAP
            x0 = MARGIN_LEFT + (usable_w - row_w) / 2.0
            cx = x0 + col * (NODE_W + H_GAP) + NODE_W / 2.0
            cy = top + BAND_PAD + row * (NODE_H + V_GAP) + NODE_H / 2.0
            positions[_ensure_id(e, i)] = (cx, cy)
        bands.append((c, top, bottom))
        y = bottom
    return positions, bands, y + MARGIN_TOP


def _marker_id(name, status):
    # markers are shared per (marker, gray?) combination
    return "m-%s" % name


def _edge_geometry(x1, y1, x2, y2):
    """Return (exit point, enter point, control points) as strings."""
    dx = x2 - x1
    dy = y2 - y1
    if abs(dy) <= 14:  # same-row: route horizontally
        ex = (x1 + NODE_W / 2.0, y1)
        en = (x2 - NODE_W / 2.0, y2)
        mx = (ex[0] + en[0]) / 2.0
        path = "M %s %s C %s %s, %s %s, %s %s" % (
            _fmt(ex[0]), _fmt(ex[1]), _fmt(mx), _fmt(ex[1]),
            _fmt(mx), _fmt(en[1]), _fmt(en[0]), _fmt(en[1]))
        return ex, en, path
    if dy > 0:  # downward: exit bottom, enter top
        ex = (x1, y1 + NODE_H / 2.0)
        en = (x2, y2 - NODE_H / 2.0)
    else:  # upward (feedback): exit top, enter bottom
        ex = (x1, y1 - NODE_H / 2.0)
        en = (x2, y2 + NODE_H / 2.0)
    bulge = max(28.0, min(130.0, abs(dx) * 0.4 + 22.0))
    if dy < 0:
        bulge = -bulge
    c1 = (ex[0] + bulge, ex[1] + dy * 0.4)
    c2 = (en[0] + bulge, en[1] - dy * 0.4)
    path = "M %s %s C %s %s, %s %s, %s %s" % (
        _fmt(ex[0]), _fmt(ex[1]), _fmt(c1[0]), _fmt(c1[1]),
        _fmt(c2[0]), _fmt(c2[1]), _fmt(en[0]), _fmt(en[1]))
    return ex, en, path


def _build_markers():
    """SVG <defs> marker definitions for semantic endpoints."""
    out = []
    arrow = ('<marker id="m-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
             'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
             '<path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke"/></marker>')
    bar = ('<marker id="m-bar" viewBox="0 0 10 10" refX="8.5" refY="5" '
           'markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
           '<line x1="1" y1="1" x2="1" y2="9" stroke="context-stroke" stroke-width="2.4"/></marker>')
    circle = ('<marker id="m-circle" viewBox="0 0 10 10" refX="6" refY="5" '
              'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
              '<circle cx="5" cy="5" r="3.4" fill="context-stroke"/></marker>')
    dot = ('<marker id="m-dot" viewBox="0 0 10 10" refX="6" refY="5" '
           'markerWidth="5" markerHeight="5" orient="auto-start-reverse">'
           '<circle cx="5" cy="5" r="3.4" fill="context-stroke"/></marker>')
    open = ('<marker id="m-open" viewBox="0 0 10 10" refX="8" refY="5" '
            'markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
            '<path d="M 1 1 L 9 5 L 1 9" fill="none" stroke="context-stroke" stroke-width="1.6"/></marker>')
    out.extend([arrow, bar, circle, dot, open])
    return "\n    ".join(out)


def render(spec):
    """Render the spec into a complete SVG document string."""
    entities = spec.get("entities") or []
    relations = spec.get("relations") or []
    data_mappings = spec.get("data_mappings") or []
    title = _clean_text(spec.get("title") or spec.get("case_id") or "mechanism figure")

    # assign ids / normalize
    for i, e in enumerate(entities):
        _ensure_id(e, i)
    positions, bands, content_h = layout(entities)

    # legend items
    rel_types_used = []
    for r in relations:
        t = str(r.get("type", "unknown")).lower()
        if t not in rel_types_used:
            rel_types_used.append(t)
    ent_types_used = []
    for e in entities:
        t = str(e.get("type", "other")).lower()
        if t not in ent_types_used:
            ent_types_used.append(t)
    custom_legend = spec.get("legend") or []

    legend_rows = []
    for t in rel_types_used:
        st = RELATION_STYLES.get(t, RELATION_STYLES["unknown"])
        legend_rows.append(("rel", t, st["color"], st["dash"], st["marker"], st["label"]))
    for t in ent_types_used:
        est = ENTITY_STYLES.get(t, ENTITY_STYLES["other"])
        legend_rows.append(("ent", t, est["stroke"], None, None, "Entity: " + t))
    for item in custom_legend:
        if isinstance(item, dict) and item.get("item"):
            legend_rows.append(("custom", str(item.get("item")), "#404040", None, None,
                                str(item.get("encoding", "")) + " " + str(item.get("applies_to", ""))))

    legend_h = LEGEND_TOP_PAD + len(legend_rows) * LEGEND_ROW_H + 16

    data_h = 0
    data_rows = []
    if data_mappings:
        data_rows.append(("Element", "Metric", "Value", "Evidence"))
        for dm in data_mappings:
            if not isinstance(dm, dict):
                continue
            data_rows.append((
                str(dm.get("element", "")),
                str(dm.get("metric", "")),
                ("%s %s" % (dm.get("value", ""), dm.get("unit", ""))).strip(),
                str(dm.get("evidence_status", "")),
            ))
        data_h = 24 + len(data_rows) * 24 + 16

    total_h = content_h + legend_h + data_h

    svg_parts = []
    svg_parts.append(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="%d" '
        'viewBox="0 0 1000 %d" font-family="%s">' % (int(math.ceil(total_h)), int(math.ceil(total_h)), FONT_STACK)
    )
    svg_parts.append('<title>%s</title>' % title)
    svg_parts.append('<desc>Layered editable mechanism/pathway figure generated by scripts/build_svg.py. '
                     'Evidence statuses: direct=solid, inferred=dashed relation, missing=gray dashed.</desc>')
    svg_parts.append('<defs>\n    %s\n  </defs>' % _build_markers())
    svg_parts.append('<rect width="1000" height="%d" fill="#ffffff"/>' % int(math.ceil(total_h)))

    # ---- compartments layer ----
    comp_el = ['<g id="layer-compartments" fill="none">']
    for c, top, bottom in bands:
        label = COMPARTMENT_LABELS.get(c, c)
        membrane = (c == "membrane")
        fill = "#fbfbfb" if not membrane else "#eef4f7"
        stroke = "#c0c0c0" if not membrane else "#7fb3d5"
        comp_el.append(
            '<rect x="%d" y="%d" width="%d" height="%d" rx="6" fill="%s" stroke="%s" stroke-dasharray="%s"/>'
            % (MARGIN_LEFT - 46, int(top), int(1000 - MARGIN_RIGHT - (MARGIN_LEFT - 46)),
               int(bottom - top), fill, stroke, ("4,3" if membrane else "none"))
        )
        comp_el.append(
            '<text x="%d" y="%d" font-size="11" fill="#555" text-anchor="end" dominant-baseline="middle">%s</text>'
            % (MARGIN_LEFT - 56, int((top + bottom) / 2), _clean_text(label))
        )
        if membrane:
            my = (top + bottom) / 2.0
            comp_el.append(
                '<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#7fb3d5" stroke-width="2"/>'
                % (MARGIN_LEFT - 46, my, int(1000 - MARGIN_RIGHT), my)
            )
    comp_el.append('</g>')

    # ---- relations layer ----
    rel_el = ['<g id="layer-relations" fill="none">']
    rel_el.append('<g id="layer-relations-edges" stroke-linecap="round">')
    entity_by_id = {str(e.get("id")): e for e in entities}
    for r in relations:
        src = str(r.get("source", ""))
        tgt = str(r.get("target", ""))
        if src not in positions or tgt not in positions:
            continue
        rtype = str(r.get("type", "unknown")).lower()
        es = str(r.get("evidence_status", "direct")).lower()
        vis = _relation_visual(rtype, es)
        x1, y1 = positions[src]
        x2, y2 = positions[tgt]
        _, _, path = _edge_geometry(x1, y1, x2, y2)
        dash = ' stroke-dasharray="%s"' % vis["dash"] if vis["dash"] else ""
        marker_end = ' marker-end="url(#%s)"' % _marker_id(vis["marker"], es)
        marker_start = ""
        if str(r.get("direction", "")).lower() == "bidirectional":
            marker_start = ' marker-start="url(#%s)"' % _marker_id(vis["marker"], es)
        loc = _first_locator(r.get("evidence"))
        rid = _clean_text(r.get("id", ""))
        rel_el.append(
            '<path%s d="%s" stroke="%s" stroke-width="2"%s%s data-relation-id="%s" '
            'data-type="%s" data-evidence-status="%s" data-source-locator="%s"/>'
            % (marker_start, path, vis["color"], dash, marker_end, rid,
               _clean_text(rtype), _clean_text(es), _clean_text(loc))
        )
    rel_el.append('</g></g>')

    # ---- entities layer ----
    ent_el = ['<g id="layer-entities">']
    for i, e in enumerate(entities):
        eid = str(e.get("id", ""))
        if eid not in positions:
            continue
        cx, cy = positions[eid]
        vis = _entity_visual(e)
        label = str(e.get("label", eid))
        if e.get("evidence_status") == "missing":
            label = label + " ?"
        label = _clean_text(label)
        es = str(e.get("evidence_status", "direct")).lower()
        loc = _first_locator(e.get("evidence"))
        dash = ' stroke-dasharray="%s"' % vis["dash"] if vis.get("dash") else ""
        if vis["shape"] == "ellipse":
            shape_el = '<ellipse cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" fill="%s" stroke="%s" stroke-width="1.6"%s/>' % (
                cx, cy, vis["rx"], NODE_H / 2.0 + 2, vis["fill"], vis["stroke"], dash)
        else:
            shape_el = '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="%s" fill="%s" stroke="%s" stroke-width="1.6"%s/>' % (
                cx - NODE_W / 2.0, cy - NODE_H / 2.0, float(NODE_W), float(NODE_H),
                vis["rx"], vis["fill"], vis["stroke"], dash)
        ent_el.append(
            '<g data-entity-id="%s" data-type="%s" data-compartment="%s" '
            'data-evidence-status="%s" data-source-locator="%s">%s</g>'
            % (_clean_text(eid), _clean_text(str(e.get("type", "other")).lower()),
               _clean_text(normalize_compartment(e.get("compartment"))),
               _clean_text(es), _clean_text(loc), shape_el)
        )
    ent_el.append('</g>')

    # ---- labels layer ----
    lab_el = ['<g id="layer-labels" font-size="12" text-anchor="middle" dominant-baseline="central">']
    for e in entities:
        eid = str(e.get("id", ""))
        if eid not in positions:
            continue
        cx, cy = positions[eid]
        vis = _style_for(e)
        label = str(e.get("label", eid))
        if e.get("evidence_status") == "missing":
            label = label + " ?"
        lab_el.append(
            '<text x="%.1f" y="%.1f" font-style="%s" font-weight="%s" fill="#111">%s</text>'
            % (cx, cy, vis["font_style"], vis["font_weight"], _clean_text(label))
        )
    lab_el.append('</g>')

    # ---- legend layer ----
    leg_el = ['<g id="layer-legend" font-size="12">']
    ly = content_h + LEGEND_TOP_PAD
    leg_el.append('<text x="%d" y="%d" font-size="13" font-weight="bold" fill="#111">Legend</text>'
                  % (MARGIN_LEFT, int(ly)))
    ly += 8
    for kind, key, color, dash, marker, label in legend_rows:
        ly += LEGEND_ROW_H
        if kind == "rel":
            dash_attr = ' stroke-dasharray="%s"' % dash if dash else ""
            mk = ' marker-end="url(#%s)"' % _marker_id(marker, "direct")
            leg_el.append(
                '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-width="2"%s%s/>'
                % (MARGIN_LEFT, int(ly), MARGIN_LEFT + 30, int(ly), color, dash_attr, mk)
            )
        elif kind == "ent":
            leg_el.append(
                '<rect x="%d" y="%d" width="30" height="14" rx="4" fill="#ffffff" stroke="%s" stroke-width="1.4"/>'
                % (MARGIN_LEFT, int(ly - 7), color)
            )
        else:
            leg_el.append('<text x="%d" y="%d" fill="#404040">\u2014</text>' % (MARGIN_LEFT, int(ly)))
        leg_el.append('<text x="%d" y="%d" fill="#111">%s</text>'
                      % (MARGIN_LEFT + 42, int(ly), _clean_text(label)))
    leg_el.append('</g>')

    # ---- data layer ----
    dat_el = ['<g id="layer-data" font-size="11">']
    if data_rows:
        dy = content_h + legend_h + 22
        dat_el.append('<text x="%d" y="%d" font-size="13" font-weight="bold" fill="#111">Data mappings</text>'
                      % (MARGIN_LEFT, int(dy)))
        dy += 6
        col_x = [MARGIN_LEFT, MARGIN_LEFT + 160, MARGIN_LEFT + 320, MARGIN_LEFT + 480]
        for ri, row in enumerate(data_rows):
            dy += 24
            is_head = (ri == 0)
            for ci, cell in enumerate(row):
                dat_el.append(
                    '<text x="%d" y="%d" fill="%s" font-weight="%s">%s</text>'
                    % (col_x[ci], int(dy), "#555" if is_head else "#111",
                       "bold" if is_head else "normal", _clean_text(cell))
                )
    dat_el.append('</g>')

    svg_parts.extend(["\n  ".join(comp_el), "\n  ".join(rel_el), "\n  ".join(ent_el),
                      "\n  ".join(lab_el), "\n  ".join(leg_el), "\n  ".join(dat_el)])
    svg_parts.append('</svg>')
    return "\n".join(svg_parts) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="spec.json -> layered editable SVG")
    ap.add_argument("--spec", default="/app/work/spec.json")
    ap.add_argument("--out", default="/app/output")
    args = ap.parse_args(argv)

    if not os.path.exists(args.spec):
        print(json.dumps({"ok": False, "error": "spec not found: %s" % args.spec},
                         ensure_ascii=False))
        return 1
    with open(args.spec, "r", encoding="utf-8-sig") as f:
        try:
            spec = json.load(f)
        except json.JSONDecodeError as e:
            print(json.dumps({"ok": False, "error": "bad spec json: %s" % e},
                             ensure_ascii=False))
            return 1

    svg_text = render(spec)

    if args.out.lower().endswith(".svg"):
        out_path = args.out
    else:
        os.makedirs(args.out, exist_ok=True)
        out_path = os.path.join(args.out, "mechanism.svg")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(svg_text)

    print(json.dumps({
        "ok": True,
        "path": out_path,
        "bytes": os.path.getsize(out_path),
        "layers": ["compartments", "relations", "entities", "labels", "legend", "data"],
        "entities": len(spec.get("entities") or []),
        "relations": len(spec.get("relations") or []),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
