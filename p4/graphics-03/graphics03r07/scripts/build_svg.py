#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic layered-SVG generator for biological mechanism / pathway figures.

Stdlib only. Turns a spec dict (entities / relations / data_mappings) into an
editable, layer-named SVG whose legend, shapes, colours and line styles are all
produced from the *same* lookup tables, so the legend is self-consistent by
construction. Every node and edge carries data-* traceability attributes.

Contract guarantees (robustness-first):
  * never raises on malformed input; always returns a well-formed SVG;
  * empty / missing entity lists produce an explicit "insufficient evidence"
    placeholder instead of a blank or broken file;
  * unknown compartments, duplicate ids and dangling relation endpoints are
    skipped and reported as warnings, never as a crash.
"""

import html
import json
import os
import sys
import xml.etree.ElementTree as ET

CANVAS_W, CANVAS_H = 1000, 700
FONT_STACK = "Arial, 'Helvetica Neue', 'Noto Sans', 'Microsoft YaHei', sans-serif"
MAX_ATTR_LEN = 200  # cap for data-source-quote attributes

# ---- visual vocabulary (single source of truth for both drawing and legend) ----
ENTITY_STYLE = {
    "gene":      {"shape": "ellipse", "fill": "#e8f0fe", "stroke": "#1a56b0",
                  "w": 116, "h": 46, "font_style": "italic"},
    "protein":   {"shape": "rect",    "fill": "#fdecea", "stroke": "#b3261e",
                  "w": 120, "h": 46, "font_style": "normal"},
    "complex":   {"shape": "rect",    "fill": "#e6f4ea", "stroke": "#137333",
                  "w": 140, "h": 52, "font_style": "normal", "double": True},
    "family":    {"shape": "rect",    "fill": "#fff8e1", "stroke": "#f9ab00",
                  "w": 120, "h": 46, "font_style": "normal", "dashed": True},
    "cell_type": {"shape": "rect",    "fill": "#f3e5f5", "stroke": "#7b1fa2",
                  "w": 150, "h": 58, "font_style": "normal", "radius": 16},
    "metabolite": {"shape": "circle", "fill": "#e0f7fa", "stroke": "#00695c",
                   "w": 46, "h": 46, "font_style": "normal"},
    "other":     {"shape": "rect",    "fill": "#eceff1", "stroke": "#455a64",
                  "w": 120, "h": 46, "font_style": "normal"},
}

RELATION_STYLE = {
    "activation":   {"end": "arrow", "dash": None,   "stroke": "#1b5e20"},
    "inhibition":   {"end": "bar",   "dash": None,   "stroke": "#b71c1c"},
    "binding":      {"end": "none",  "dash": None,   "stroke": "#37474f"},
    "modification": {"end": "arrow", "dash": None,   "stroke": "#6a1b9a"},
    "transport":    {"end": "arrow", "dash": "6,4",  "stroke": "#0d47a1"},
    "conversion":   {"end": "tri",   "dash": None,   "stroke": "#e65100"},
}

EVIDENCE_WIDTH = {"high": 2.8, "medium": 2.0, "low": 1.4, None: 2.0}
EVIDENCE_DASH = {"low": "4,3"}

# (key, label, x, y, w, h, fill)
COMPARTMENT_BANDS = [
    ("extracellular", "extracellular", 40, 50, 700, 90, "#f5f9ff"),
    ("membrane",      "membrane",      40, 150, 700, 70, "#fff3e0"),
    ("cytosol",       "cytosol",       40, 230, 700, 250, "#ffffff"),
    ("nucleus",       "nucleus",       40, 490, 700, 170, "#f3eefc"),
    ("unplaced",      "unplaced",      40, 668, 700, 24, "#fafafa"),
]

LEGEND_X, LEGEND_Y, LEGEND_W = 760, 50, 230


def _esc(s):
    if s is None:
        return ""
    return html.escape(str(s), quote=True)


def _short(s, n=MAX_ATTR_LEN):
    if s is None:
        return ""
    s = " ".join(str(s).split())
    return s[:n]


def _join_src(src, key):
    """Join source list items for a data-* attribute."""
    if not isinstance(src, list):
        return ""
    parts = []
    for s in src:
        if isinstance(s, dict) and s.get(key):
            parts.append(str(s[key]))
        elif isinstance(s, str):
            parts.append(s)
    return " | ".join(parts)[:MAX_ATTR_LEN]


def normalize_spec(spec):
    """Coerce a spec to a safe shape; return (spec, warnings). Never raises."""
    warnings = []
    if not isinstance(spec, dict):
        spec = {}
        warnings.append("spec was not a JSON object; treated as empty")
    for key in ("title", "entities", "relations", "data_mappings", "legend_notes"):
        spec.setdefault(key, "" if key == "title" else [])
    for key in ("entities", "relations", "data_mappings", "legend_notes"):
        if not isinstance(spec[key], list):
            warnings.append("spec.%s is not a list; reset to []" % key)
            spec[key] = []

    seen_ids = set()
    out_entities = []
    for i, e in enumerate(spec["entities"]):
        if not isinstance(e, dict):
            warnings.append("entity #%d is not an object; skipped" % i)
            continue
        eid = str(e.get("id", "")).strip() or ("e%d" % (i + 1))
        if eid in seen_ids:
            warnings.append("duplicate entity id '%s'; kept first occurrence" % eid)
            continue
        seen_ids.add(eid)
        e["id"] = eid
        t = e.get("type") or "other"
        if t not in ENTITY_STYLE:
            warnings.append("entity '%s' has unknown type '%s'; -> other" % (eid, t))
            t = "other"
        e["type"] = t
        comp = e.get("compartment") or "unplaced"
        if comp not in {b[0] for b in COMPARTMENT_BANDS}:
            warnings.append("entity '%s' has unknown compartment '%s'; -> unplaced" % (eid, comp))
            comp = "unplaced"
        e["compartment"] = comp
        e.setdefault("transmembrane", False)
        e.setdefault("name", eid)
        e.setdefault("label", None)
        e.setdefault("aliases", [])
        e.setdefault("source", [])
        e.setdefault("data", {})
        out_entities.append(e)
    spec["entities"] = out_entities

    entity_ids = {e["id"] for e in out_entities}
    out_relations = []
    for i, r in enumerate(spec["relations"]):
        if not isinstance(r, dict):
            warnings.append("relation #%d is not an object; skipped" % i)
            continue
        rid = str(r.get("id", "")).strip() or ("r%d" % (i + 1))
        r["id"] = rid
        t = r.get("type")
        if t not in RELATION_STYLE:
            warnings.append("relation '%s' has unknown type '%s'; skipped" % (rid, t))
            continue
        r.setdefault("source", None)
        r.setdefault("target", None)
        r.setdefault("direction", "forward")
        r.setdefault("modifier", None)
        r.setdefault("evidence", None)
        r.setdefault("source_meta", [])
        if r.get("source") not in entity_ids or r.get("target") not in entity_ids:
            warnings.append("relation '%s' references missing entity; skipped" % rid)
            continue
        out_relations.append(r)
    spec["relations"] = out_relations
    return spec, warnings


# ---------------------------------------------------------------------------
# layout
# ---------------------------------------------------------------------------
def _band_of(comp):
    for b in COMPARTMENT_BANDS:
        if b[0] == comp:
            return b
    return COMPARTMENT_BANDS[-1]


def _layout_entities(entities):
    """Place entities in compartment rows. Returns (placed, warnings)."""
    placed = {}
    warnings = []
    by_band = {}
    for e in entities:
        by_band.setdefault(e["compartment"], []).append(e)

    for band in COMPARTMENT_BANDS:
        key, label, bx, by, bw, bh, _fill = band
        items = by_band.get(key, [])
        if not items:
            continue
        # transmembrane entities span the whole membrane band
        tm = [e for e in items if e.get("transmembrane") and key == "membrane"]
        normal = [e for e in items if not (e.get("transmembrane") and key == "membrane")]
        groups = [normal] if not tm else [normal, tm]

        n_normal = len(normal)
        def place_group(grp, is_tm=False):
            n = len(grp)
            if n == 0:
                return
            inner_x = bx + 18
            inner_w = bw - 36
            slot = inner_w / n
            for idx, e in enumerate(grp):
                st = ENTITY_STYLE[e["type"]]
                w = float(e.get("width") or st["w"])
                h = float(e.get("height") or st["h"])
                cx = inner_x + slot * (idx + 0.5)
                cy = by + bh / 2.0
                if is_tm:
                    h = float(bh)
                    cy = by + bh / 2.0
                placed[e["id"]] = {"entity": e, "cx": cx, "cy": cy, "w": w, "h": h}
        place_group(normal)
        if tm:
            place_group(tm, is_tm=True)
    return placed, warnings


def _rect_intersect(ax, ay, bx, by, x0, y0, x1, y1):
    """First point on segment A->B that enters rect [x0,x1]x[y0,y1]."""
    dx, dy = bx - ax, by - ay
    cand = []
    if dx != 0:
        cand += [(x0 - ax) / dx, (x1 - ax) / dx]
    if dy != 0:
        cand += [(y0 - ay) / dy, (y1 - ay) / dy]
    eps = 1e-6
    for t in sorted(cand):
        if -eps <= t <= 1 + eps:
            px, py = ax + t * dx, ay + t * dy
            if x0 - eps <= px <= x1 + eps and y0 - eps <= py <= y1 + eps:
                return (px, py)
    return (bx, by)


def _node_shape(e, cx, cy, w, h, extra=""):
    st = ENTITY_STYLE[e["type"]]
    sw = 1.5
    dash = ""
    if st.get("dashed"):
        dash = ' stroke-dasharray="6,4"'
    if st["shape"] == "ellipse":
        return ('<ellipse cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" fill="%s" '
                'stroke="%s" stroke-width="%.1f"%s%s/>' %
                (cx, cy, w / 2, h / 2, st["fill"], st["stroke"], sw, dash, extra))
    if st["shape"] == "circle":
        return ('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s" stroke="%s" '
                'stroke-width="%.1f"%s%s/>' % (cx, cy, w / 2, st["fill"], st["stroke"], sw, dash, extra))
    radius = st.get("radius", 6)
    if st.get("double"):
        return ('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="%.1f" fill="%s" '
                'stroke="%s" stroke-width="%.1f" stroke-dasharray="3,2"/><rect x="%.1f" y="%.1f" '
                'width="%.1f" height="%.1f" rx="%.1f" fill="none" stroke="%s" stroke-width="%.1f"%s/>'
                % (cx - w / 2, cy - h / 2, w, h, radius, st["fill"], st["stroke"], sw,
                   cx - w / 2 + 3, cy - h / 2 + 3, w - 6, h - 6, radius, st["stroke"], 1.0, dash))
    return ('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="%.1f" fill="%s" '
            'stroke="%s" stroke-width="%.1f"%s%s/>' %
            (cx - w / 2, cy - h / 2, w, h, radius, st["fill"], st["stroke"], sw, dash, extra))


def _font_size(name, w):
    n = len(str(name))
    if n <= 14:
        return 13
    if n <= 22:
        return 11
    return 10


def _node_svg(e, cx, cy, w, h):
    st = ENTITY_STYLE[e["type"]]
    label = e.get("label") or e.get("name") or e["id"]
    fs = _font_size(label, w)
    attrs = (' data-entity-id="%s" data-entity-type="%s" data-compartment="%s" '
             'data-source-locator="%s" data-source-quote="%s"'
             % (_esc(e["id"]), _esc(e["type"]), _esc(e["compartment"]),
                _esc(_join_src(e.get("source"), "locator")), _esc(_join_src(e.get("source"), "quote"))))
    shape = _node_shape(e, cx, cy, w, h)
    text = ('<text x="%.1f" y="%.1f" font-family="%s" font-size="%d" font-style="%s" '
            'text-anchor="middle" fill="#111111">%s</text>'
            % (cx, cy + 4, FONT_STACK, fs, st["font_style"], _esc(label)))
    return '<g class="node"%s>%s%s</g>' % (attrs, shape, text)


def _edge_path(pl, src_id, tgt_id, r):
    sp, tp = pl[src_id], pl[tgt_id]
    sx, sy, sw, sh = sp["cx"], sp["cy"], sp["w"], sp["h"]
    tx, ty, tw, th = tp["cx"], tp["cy"], tp["w"], tp["h"]
    # self-loop
    if src_id == tgt_id:
        return None, ('M%.1f %.1f C %.1f %.1f, %.1f %.1f, %.1f %.1f'
                      % (sx - 10, sy - sh / 2, sx - 46, sy - sh / 2 - 34,
                         sx + 46, sy - sh / 2 - 34, sx + 10, sy - sh / 2)), (sx + 10, sy - sh / 2)
    a = _rect_intersect(sx, sy, tx, ty, sx - sw / 2, sy - sh / 2, sx + sw / 2, sy + sh / 2)
    b = _rect_intersect(tx, ty, sx, sy, tx - tw / 2, ty - th / 2, tx + tw / 2, ty + th / 2)
    return (a, b), 'M%.1f %.1f L%.1f %.1f' % (a[0], a[1], b[0], b[1]), b


def _marker_defs(rel_types):
    defs = []
    for t in sorted(rel_types):
        st = RELATION_STYLE[t]
        color = st["stroke"]
        mid = "mk_" + t
        if st["end"] == "arrow":
            defs.append('<marker id="%s" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6.5" '
                        'markerHeight="6.5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="%s"/></marker>'
                        % (mid, color))
        elif st["end"] == "bar":
            defs.append('<marker id="%s" viewBox="0 0 10 10" refX="0" refY="5" markerWidth="8" '
                        'markerHeight="8" orient="auto"><line x1="0" y1="1.5" x2="0" y2="8.5" '
                        'stroke="%s" stroke-width="2.4"/></marker>' % (mid, color))
        elif st["end"] == "tri":
            defs.append('<marker id="%s" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6.5" '
                        'markerHeight="6.5" orient="auto"><path d="M0,0 L10,5 L0,10" fill="none" '
                        'stroke="%s" stroke-width="1.4"/></marker>' % (mid, color))
    return "\n".join(defs)


def _edge_svg(r, pl, used_markers):
    st = RELATION_STYLE[r["type"]]
    width = EVIDENCE_WIDTH.get(r.get("evidence"), EVIDENCE_WIDTH[None])
    dash = st["dash"]
    if r.get("evidence") == "low":
        dash = EVIDENCE_DASH.get("low")
    dash_attr = (' stroke-dasharray="%s"' % dash) if dash else ""

    direction = r.get("direction") or "forward"
    start_id, end_id = r["source"], r["target"]
    # draw geometry source->target; backward flips which end gets the arrowhead
    geom_start, geom_end = (end_id, start_id) if direction == "backward" else (start_id, end_id)
    pair, dpath, arrow_tip = _edge_path(pl, geom_start, geom_end, r)
    marker_end = (' marker-end="url(#%s)"' % ("mk_" + r["type"])) if used_markers and st["end"] != "none" else ""
    marker_start = ' marker-start="url(#%s)"' % ("mk_" + r["type"]) if (direction == "bidirectional" and used_markers) else ""

    attrs = (' data-relation-id="%s" data-type="%s" data-direction="%s" data-evidence="%s" '
             'data-source-locator="%s" data-source-quote="%s"'
             % (_esc(r["id"]), _esc(r["type"]), _esc(direction), _esc(r.get("evidence") or ""),
                _esc(_join_src(r.get("source_meta"), "locator")), _esc(_join_src(r.get("source_meta"), "quote"))))
    line = ('<path d="%s" fill="none" stroke="%s" stroke-width="%.2f"%s%s%s%s/>'
            % (dpath, st["stroke"], width, dash_attr, marker_start, marker_end, attrs))
    parts = [line]
    if r["type"] == "modification" and r.get("modifier"):
        mid_x = (pl[start_id]["cx"] + pl[end_id]["cx"]) / 2.0
        mid_y = (pl[start_id]["cy"] + pl[end_id]["cy"]) / 2.0 - 6
        parts.append('<text x="%.1f" y="%.1f" font-family="%s" font-size="11" font-style="italic" '
                     'text-anchor="middle" fill="%s" stroke="#ffffff" stroke-width="2" '
                     'paint-order="stroke">%s</text>'
                     % (mid_x, mid_y, FONT_STACK, st["stroke"], _esc(r["modifier"])))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# legend (single source of truth shared with result.json)
# ---------------------------------------------------------------------------
def legend_entries(spec):
    entities = spec.get("entities", [])
    relations = spec.get("relations", [])
    entries = []
    ent_types = sorted({e.get("type", "other") for e in entities})
    rel_types = sorted({r.get("type") for r in relations if r.get("type") in RELATION_STYLE})
    for t in ent_types:
        st = ENTITY_STYLE.get(t, ENTITY_STYLE["other"])
        entries.append({"key": "entity_type:%s" % t, "meaning": t,
                        "visual_encoding": "shape=%s fill=%s stroke=%s" % (st["shape"], st["fill"], st["stroke"])})
    for t in rel_types:
        st = RELATION_STYLE[t]
        entries.append({"key": "relation_type:%s" % t, "meaning": t,
                        "visual_encoding": "end=%s stroke=%s dash=%s" % (st["end"], st["stroke"], st["dash"])})
    for dm in spec.get("data_mappings", []):
        if isinstance(dm, dict):
            entries.append({"key": "data:%s" % dm.get("channel", ""),
                            "meaning": str(dm.get("field", "")),
                            "visual_encoding": str(dm.get("scale", ""))})
    comps = {e.get("compartment") for e in entities}
    for b in COMPARTMENT_BANDS:
        if b[0] in comps:
            entries.append({"key": "compartment:%s" % b[0], "meaning": b[1],
                            "visual_encoding": "fill=%s" % b[5]})
    return entries


def _legend_svg(entries):
    out = ['<g id="layer-legend" inkscape:groupmode="layer" inkscape:label="legend">',
           '<text x="%d" y="%d" font-family="%s" font-size="14" font-weight="bold" fill="#111111">Legend</text>'
           % (LEGEND_X, LEGEND_Y, FONT_STACK)]
    y = LEGEND_Y + 20
    x = LEGEND_X
    for en in entries:
        key, meaning, enc = en["key"], en["meaning"], en["visual_encoding"]
        if key.startswith("entity_type:"):
            t = key.split(":", 1)[1]
            st = ENTITY_STYLE.get(t, ENTITY_STYLE["other"])
            sw = ('<ellipse cx="%d" cy="%d" rx="9" ry="6" fill="%s" stroke="%s"/>'
                  % (x + 10, y + 6, st["fill"], st["stroke"]) if st["shape"] == "ellipse" else
                  '<rect x="%d" y="%d" width="18" height="12" rx="3" fill="%s" stroke="%s"/>'
                  % (x + 1, y, st["fill"], st["stroke"]))
            out.append(sw)
            note = enc.split(" stroke=")[0].replace("shape=", "").replace("fill=", "")
            out.append('<text x="%d" y="%d" font-family="%s" font-size="11" fill="#111111">%s (%s)</text>'
                       % (x + 28, y + 10, FONT_STACK, _esc(meaning), _esc(note)))
        elif key.startswith("relation_type:"):
            t = key.split(":", 1)[1]
            st = RELATION_STYLE[t]
            dash = (' stroke-dasharray="6,4"' if st["dash"] else "")
            m = (' marker-end="url(#%s)"' % ("mk_" + t)) if st["end"] != "none" else ""
            out.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-width="2"%s%s/>'
                       % (x + 1, y + 6, x + 19, y + 6, st["stroke"], dash, m))
            out.append('<text x="%d" y="%d" font-family="%s" font-size="11" fill="#111111">%s</text>'
                       % (x + 28, y + 10, FONT_STACK, _esc("%s — %s" % (meaning, enc))))
        elif key.startswith("compartment:"):
            fill = enc.split("=")[-1]
            out.append('<rect x="%d" y="%d" width="18" height="10" fill="%s" stroke="#9e9e9e"/>'
                       % (x + 1, y, fill))
            out.append('<text x="%d" y="%d" font-family="%s" font-size="11" fill="#111111">%s</text>'
                       % (x + 28, y + 10, FONT_STACK, _esc(meaning)))
        else:
            out.append('<text x="%d" y="%d" font-family="%s" font-size="11" fill="#111111">%s: %s = %s</text>'
                       % (x, y + 10, FONT_STACK, _esc(meaning), _esc(enc.split("=")[-1] if "=" in enc else ""),
                          _esc(enc)))
        y += 20
        if y > CANVAS_H - 16:
            break
    out.append('</g>')
    return "\n".join(out)


# ---------------------------------------------------------------------------
# main SVG builder
# ---------------------------------------------------------------------------
def build_svg(spec):
    spec, warnings = normalize_spec(spec)
    entities = spec["entities"]
    relations = spec["relations"]

    placed, _lay_warn = _layout_entities(entities)
    rel_types = {r["type"] for r in relations}
    markers = _marker_defs(rel_types)
    entries = legend_entries(spec)

    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append('<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
                 'width="%d" height="%d" viewBox="0 0 %d %d">' % (CANVAS_W, CANVAS_H, CANVAS_W, CANVAS_H))
    if markers:
        lines.append('<defs>%s</defs>' % markers)
    title = _short(spec.get("title") or "biological_mechanism_pathway", 120)
    lines.append('<title>%s</title>' % _esc(title))

    # compartments
    lines.append('<g id="layer-compartments" inkscape:groupmode="layer" inkscape:label="compartments">')
    for key, label, bx, by, bw, bh, fill in COMPARTMENT_BANDS:
        lines.append('<rect x="%d" y="%d" width="%d" height="%d" fill="%s" stroke="#b0bec5" '
                     'stroke-width="1"/>' % (bx, by, bw, bh, fill))
        lines.append('<text x="%d" y="%d" font-family="%s" font-size="10" fill="#546e7a">%s</text>'
                     % (bx + 6, by + 12, FONT_STACK, _esc(label)))
    # membrane bilayer lines
    lines.append('<line x1="40" y1="175" x2="740" y2="175" stroke="#b0bec5" stroke-width="1"/>')
    lines.append('<line x1="40" y1="205" x2="740" y2="205" stroke="#b0bec5" stroke-width="1"/>')
    lines.append('</g>')

    # edges (behind nodes)
    lines.append('<g id="layer-edges" inkscape:groupmode="layer" inkscape:label="edges">')
    for r in relations:
        lines.append(_edge_svg(r, placed, bool(markers)))
    lines.append('</g>')

    # nodes
    lines.append('<g id="layer-nodes" inkscape:groupmode="layer" inkscape:label="nodes">')
    for e in entities:
        p = placed[e["id"]]
        lines.append(_node_svg(e, p["cx"], p["cy"], p["w"], p["h"]))
    lines.append('</g>')

    # labels layer for modifiers / data annotations
    lines.append('<g id="layer-labels" inkscape:groupmode="layer" inkscape:label="labels">')
    lines.append('</g>')

    if not entities:
        lines.append('<g id="layer-message" inkscape:groupmode="layer" inkscape:label="message">')
        lines.append('<rect x="140" y="280" width="500" height="60" rx="8" fill="#fff8e1" stroke="#f9ab00"/>')
        lines.append('<text x="390" y="316" font-family="%s" font-size="14" text-anchor="middle" fill="#8d6e00">'
                     'insufficient evidence: no entities extracted yet</text>' % FONT_STACK)
        lines.append('</g>')

    lines.append(_legend_svg(entries))
    lines.append('</svg>')
    svg = "\n".join(lines) + "\n"
    return svg, warnings


def _write_svg(svg, path):
    # atomic write: tmp then replace
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(svg)
    os.replace(tmp, path)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    spec_path = argv[0] if len(argv) > 0 else os.environ.get("SPEC_PATH", "/app/work/spec.json")
    out_path = argv[1] if len(argv) > 1 else os.environ.get("OUT_PATH", "/app/output/mechanism.svg")

    spec = {}
    read_err = None
    if os.path.isfile(spec_path):
        try:
            with open(spec_path, "r", encoding="utf-8-sig") as f:
                spec = json.load(f)
        except Exception as e:
            read_err = "spec.json unreadable: %s" % e
            spec = {}
    else:
        read_err = "spec.json missing (%s); generated placeholder" % spec_path

    svg, warnings = build_svg(spec)
    try:
        ET.fromstring(svg)  # well-formedness guard
        valid = True
    except Exception as e:
        valid = False
        warnings.append("generated SVG failed XML check: %s" % e)

    try:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        _write_svg(svg, out_path)
        wrote = True
    except Exception as e:
        warnings.append("could not write SVG: %s" % e)
        wrote = False

    summary = {
        "ok": wrote,
        "svg": out_path if wrote else None,
        "bytes": len(svg.encode("utf-8")) if wrote else 0,
        "xml_valid": valid,
        "entity_count": len(spec.get("entities", [])),
        "relation_count": len(spec.get("relations", [])),
        "warnings": warnings + ([read_err] if read_err else []),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if wrote else 1


if __name__ == "__main__":
    sys.exit(main())
