#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Core implementation for the biological mechanism / pathway figure Skill.

Design contract: every artifact is a pure function of ONE canonical, normalized
spec (spec.json). No wall-clock timestamps, no random numbers, no reliance on
dict iteration order. Re-running the pipeline on identical input bytes yields
identical output bytes; this determinism is checked by scripts/selftest.py.

Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys

SCHEMA_VERSION = "1.0"
FONT_STACK = "Arial, 'Helvetica Neue', Helvetica, 'Noto Sans', sans-serif"

# ---------------------------------------------------------------------------
# Visual encoding tables. The figure and the legend are BOTH derived from
# these tables, so the two cannot disagree about color / shape / line style.
# ---------------------------------------------------------------------------
RELATION_STYLE = {
    "activation":   {"stroke": "#2e7d32", "dash": None,  "end": "arrow", "label": "activation"},
    "inhibition":   {"stroke": "#c62828", "dash": None,  "end": "bar",   "label": "inhibition"},
    "binding":      {"stroke": "#455a64", "dash": None,  "end": "none",  "label": "binding"},
    "modification": {"stroke": "#6a1b9a", "dash": None,  "end": "arrow", "label": "modification"},
    "transport":    {"stroke": "#1565c0", "dash": "6 4", "end": "arrow", "label": "transport"},
    "conversion":   {"stroke": "#e65100", "dash": "2 2", "end": "arrow", "label": "conversion"},
    "expression":   {"stroke": "#8e24aa", "dash": "4 3", "end": "arrow", "label": "expression"},
    "recruitment":  {"stroke": "#00838f", "dash": "3 3", "end": "arrow", "label": "recruitment"},
}

NAME_TYPE_STYLE = {
    "gene":        {"shape": "rect",    "font_style": "italic", "fill": "#f5f5f5", "stroke": "#616161", "stroke_width": 1.6, "label": "gene"},
    "protein":     {"shape": "ellipse", "font_style": "normal", "fill": "#e3f2fd", "stroke": "#1565c0", "stroke_width": 1.6, "label": "protein"},
    "complex":     {"shape": "rect",    "font_style": "normal", "fill": "#ede7f6", "stroke": "#4527a0", "stroke_width": 2.6, "label": "complex"},
    "family":      {"shape": "rect",    "font_style": "normal", "fill": "#fff3e0", "stroke": "#e65100", "stroke_width": 1.8, "label": "family"},
    "cell_type":   {"shape": "round",   "font_style": "normal", "fill": "#e0f7fa", "stroke": "#00695c", "stroke_width": 1.8, "label": "cell type"},
    "ligand":      {"shape": "circle",  "font_style": "normal", "fill": "#fce4ec", "stroke": "#880e4f", "stroke_width": 1.6, "label": "ligand"},
    "compartment": {"shape": "round",   "font_style": "normal", "fill": "#fafafa", "stroke": "#9e9e9e", "stroke_width": 1.4, "label": "compartment"},
    "other":       {"shape": "rect",    "font_style": "normal", "fill": "#eceff1", "stroke": "#607d8b", "stroke_width": 1.4, "label": "entity"},
}

COMPARTMENT_ORDER = ["extracellular", "membrane", "cytosol", "nucleus", "organelle", "unspecified"]

COMPARTMENT_LABEL = {
    "extracellular": "extracellular",
    "membrane": "membrane",
    "cytosol": "cytosol",
    "nucleus": "nucleus",
    "organelle": "organelle",
    "unspecified": "unspecified compartment",
}

BAND_HEIGHT = {
    "extracellular": 110,
    "membrane": 90,
    "cytosol": 160,
    "nucleus": 160,
    "organelle": 130,
    "unspecified": 130,
}

VALID_NAME_TYPES = set(NAME_TYPE_STYLE)
VALID_REL_TYPES = set(RELATION_STYLE)
VALID_COMPARTMENTS = set(COMPARTMENT_ORDER)
VALID_DIRECTIONS = {"forward", "reverse", "bidirectional", "self"}
STATUSES = {"success", "partial", "failed", "insufficient_evidence", "input_error"}

COLOR_PALETTE = ["#f7fbff", "#deebf7", "#c6dbef", "#9ecae1", "#6baed6", "#4292c6", "#2171b5"]


# ---------------------------------------------------------------------------
# Robust text / JSON IO
# ---------------------------------------------------------------------------
def _read_text(path: str) -> str:
    """Read a UTF-8/UTF-8-sig text file, degrading to gb18030 then latin-1.

    Never crashes on BOM or bad encoding: bad bytes are replaced so a malformed
    input is reported as a content problem, not as a process crash.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def load_json(path: str):
    """Load JSON with BOM tolerance and a descriptive error on failure."""
    if not os.path.exists(path):
        raise FileNotFoundError("no such file: %s" % path)
    text = _read_text(path).lstrip("\ufeff \t\r\n")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ValueError("bad JSON in %s: %s" % (path, exc))


def canonical_dumps(obj) -> str:
    """Deterministic JSON serialization: sorted keys, stable separators."""
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, separators=(",", ": "))


def sha256_hex(obj) -> str:
    if isinstance(obj, bytes):
        data = obj
    elif isinstance(obj, str):
        data = obj.encode("utf-8")
    else:
        data = canonical_dumps(obj).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _fmt(x) -> str:
    """Fixed-point formatting with trailing zeros removed (deterministic)."""
    s = "%.2f" % float(x)
    s = s.rstrip("0").rstrip(".")
    return s or "0"


def _xml_escape(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;"))


# ---------------------------------------------------------------------------
# Spec normalization / validation
# ---------------------------------------------------------------------------
def _norm_sources(raw):
    out = []
    for s in (raw or []):
        if not isinstance(s, dict):
            continue
        locator = s.get("locator")
        quote = s.get("quote")
        if locator is None and quote is None:
            continue
        out.append({
            "locator": str(locator) if locator is not None else None,
            "quote": str(quote) if quote is not None else None,
            "confidence": str(s.get("confidence")) if s.get("confidence") is not None else None,
        })
    return out


def _norm_mappings(raw):
    out = []
    for m in (raw or []):
        if not isinstance(m, dict):
            continue
        channel = str(m.get("channel") or "")
        metric = str(m.get("metric") or "")
        if not channel or not metric:
            continue
        scale = m.get("scale")
        if not isinstance(scale, dict):
            scale = None
        out.append({"channel": channel, "metric": metric, "scale": scale, "note": m.get("note")})
    return out


def _spec_problems(spec):
    """Return (errors, warnings) for a raw spec, before normalization."""
    errors, warnings = [], []
    if not isinstance(spec, dict):
        return ["spec must be a JSON object"], []
    ents = spec.get("entities")
    if not isinstance(ents, list) or not ents:
        errors.append("spec.entities must be a non-empty list")
        ents = []
    ids = set()
    for i, e in enumerate(ents):
        if not isinstance(e, dict):
            errors.append("entities[%d] is not an object" % i)
            continue
        eid = e.get("id")
        if not isinstance(eid, str) or not eid:
            errors.append("entities[%d].id missing or not a string" % i)
            continue
        if eid in ids:
            errors.append("duplicate entity id %r" % eid)
        ids.add(eid)
        if not e.get("label"):
            warnings.append("entity %s has empty label" % eid)
        nt = e.get("name_type")
        if nt not in VALID_NAME_TYPES:
            warnings.append("entity %s: unknown name_type %r -> 'other'" % (eid, nt))
        comp = e.get("compartment")
        if comp not in VALID_COMPARTMENTS:
            warnings.append("entity %s: unknown compartment %r -> 'unspecified'" % (eid, comp))
        if e.get("transmembrane") and comp not in ("membrane", "unspecified"):
            warnings.append("entity %s: transmembrane=true but compartment=%r" % (eid, comp))
    rels = spec.get("relations", [])
    if not isinstance(rels, list):
        rels = []
    for i, r in enumerate(rels):
        if not isinstance(r, dict):
            errors.append("relations[%d] is not an object" % i)
            continue
        rid = r.get("id")
        if not isinstance(rid, str) or not rid:
            warnings.append("relations[%d].id missing; auto-assigned" % i)
        for key in ("from", "to"):
            if r.get(key) not in ids:
                errors.append("relation %r references unknown %s=%r" % (rid or i, key, r.get(key)))
        t = r.get("type")
        if t not in VALID_REL_TYPES:
            errors.append("relation %r has unknown type %r" % (rid or i, t))
        d = r.get("direction")
        if d is not None and d not in VALID_DIRECTIONS:
            warnings.append("relation %r: unknown direction %r -> 'forward'" % (rid or i, d))
    return errors, warnings


def normalize_spec(spec):
    """Return (normalized_spec, errors, warnings). Never raises on bad content."""
    if not isinstance(spec, dict):
        spec = {}
    errors, warnings = _spec_problems(spec)
    entities = []
    for i, e in enumerate(spec.get("entities") or []):
        if not isinstance(e, dict):
            e = {}
        eid = e.get("id") if isinstance(e.get("id"), str) and e.get("id") else ("e%d" % (i + 1))
        nt = e.get("name_type") if e.get("name_type") in VALID_NAME_TYPES else "other"
        comp = e.get("compartment") if e.get("compartment") in VALID_COMPARTMENTS else "unspecified"
        entities.append({
            "id": eid,
            "label": str(e.get("label") or eid),
            "name_type": nt,
            "species": e.get("species") if isinstance(e.get("species"), str) else None,
            "aliases": sorted({str(a) for a in (e.get("aliases") or []) if isinstance(a, str)}),
            "compartment": comp,
            "transmembrane": bool(e.get("transmembrane")),
            "data": dict(e.get("data") or {}),
            "sources": _norm_sources(e.get("sources")),
        })
    relations = []
    for i, r in enumerate(spec.get("relations") or []):
        if not isinstance(r, dict):
            r = {}
        rid = r.get("id") if isinstance(r.get("id"), str) and r.get("id") else ("r%d" % (i + 1))
        t = r.get("type") if r.get("type") in VALID_REL_TYPES else "binding"
        d = r.get("direction") if r.get("direction") in VALID_DIRECTIONS else "forward"
        relations.append({
            "id": rid,
            "from": r.get("from"),
            "to": r.get("to"),
            "type": t,
            "direction": d,
            "modifier": r.get("modifier") if isinstance(r.get("modifier"), str) else None,
            "evidence": dict(r.get("evidence") or {}),
            "sources": _norm_sources(r.get("sources")),
        })
    layout = spec.get("layout") if isinstance(spec.get("layout"), dict) else {}
    order = layout.get("order") if isinstance(layout.get("order"), list) else []
    normalized = {
        "schema_version": SCHEMA_VERSION,
        "title": str(spec.get("title") or "mechanism"),
        "entities": entities,
        "relations": relations,
        "layout": {
            "orientation": "TB",
            "order": [str(x) for x in order if isinstance(x, str)],
        },
        "data_mappings": _norm_mappings(spec.get("data_mappings")),
        "uncertainty": [str(u) for u in (spec.get("uncertainty") or []) if str(u)],
        "notes": [str(n) for n in (spec.get("notes") or []) if str(n)],
    }
    return normalized, errors, warnings


# ---------------------------------------------------------------------------
# Deterministic SVG rendering
# ---------------------------------------------------------------------------
def _wrap_label(label, max_chars=20):
    words = str(label).split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if not cur or len(trial) <= max_chars:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if not lines:
        lines = [str(label)]
    if len(lines) == 1 and len(lines[0]) > max_chars:
        s = lines[0]
        lines = [s[i:i + max_chars] for i in range(0, len(s), max_chars)]
    return lines[:3]


def _on_boundary(ncx, ncy, toward_x, toward_y, hw, hh):
    """Point on the node's bounding box, on the side facing (toward_x, toward_y)."""
    vx = toward_x - ncx
    vy = toward_y - ncy
    if vx == 0 and vy == 0:
        return (ncx, ncy - hh)
    scales = []
    if vx:
        scales.append(hw / abs(vx))
    if vy:
        scales.append(hh / abs(vy))
    t = min(scales) if scales else 0.0
    return (ncx + vx * t, ncy + vy * t)


def _numeric_scale(value, scale):
    """value -> [0,1] using an explicit {min,max} scale; clamp outside range."""
    if not isinstance(scale, dict):
        return None
    try:
        vmin = float(scale.get("min"))
        vmax = float(scale.get("max"))
        v = float(value)
    except (TypeError, ValueError):
        return None
    if vmax == vmin:
        return 0.0
    return min(1.0, max(0.0, (v - vmin) / (vmax - vmin)))


def _color_for(value, scale):
    t = _numeric_scale(value, scale)
    if t is None:
        return None
    idx = int(round(t * (len(COLOR_PALETTE) - 1)))
    return COLOR_PALETTE[max(0, min(len(COLOR_PALETTE) - 1, idx))]


def _width_for(value, scale, wmin=2.0, wmax=6.0):
    t = _numeric_scale(value, scale)
    if t is None:
        return None
    return wmin + (wmax - wmin) * t


def _entity_display(ent, mappings):
    """Compute fill / stroke-width overrides from data mappings (deterministic)."""
    fill = NAME_TYPE_STYLE[ent["name_type"]]["fill"]
    stroke_width = NAME_TYPE_STYLE[ent["name_type"]]["stroke_width"]
    data = ent.get("data") or {}
    for m in mappings:
        channel = m.get("channel")
        if channel == "fill" and m.get("scale") is not None:
            c = _color_for(data.get(m.get("metric")), m.get("scale"))
            if c:
                fill = c
        elif channel == "stroke_width" and m.get("scale") is not None:
            w = _width_for(data.get(m.get("metric")), m.get("scale"))
            if w:
                stroke_width = w
    return fill, stroke_width


def _geometry(norm):
    """Compute canvas and per-entity geometry deterministically."""
    entities = norm["entities"]
    by_id = {e["id"]: e for e in entities}

    # compartment bands, in fixed order, only those that are used
    used = []
    for c in COMPARTMENT_ORDER:
        if any(e["compartment"] == c for e in entities):
            used.append(c)

    margin = 60.0
    width = 1000.0
    title_h = 56.0
    gap = 8.0
    top = title_h
    band_tops = {}
    for c in used:
        band_tops[c] = top
        top += BAND_HEIGHT[c] + gap

    # deterministic ordering inside each band
    order = norm["layout"]["order"]
    seen_order = {eid: i for i, eid in enumerate(order)}
    rank = {}
    for i, e in enumerate(entities):
        rank[e["id"]] = (seen_order.get(e["id"], 100000 + i), i)
    ordered = sorted(entities, key=lambda e: (COMPARTMENT_ORDER.index(e["compartment"]), rank[e["id"]]))

    centers, boxes = {}, {}
    band_counts = {}
    for e in ordered:
        c = e["compartment"]
        band_counts[c] = band_counts.get(c, 0) + 1
        n = band_counts[c]
        cy = band_tops[c] + BAND_HEIGHT[c] / 2.0
        cx = margin + (width - 2 * margin) * n / (sum(1 for x in entities if x["compartment"] == c) + 1)
        lines = _wrap_label(e["label"])
        label_w = max(len(x) for x in lines) * 7.2
        node_w = max(90.0, min(210.0, 70.0 + label_w))
        node_h = 38.0 + 16.0 * (len(lines) - 1)
        hw, hh = node_w / 2.0, max(22.0, node_h / 2.0)
        centers[e["id"]] = (cx, cy)
        boxes[e["id"]] = (hw, hh)

    return {
        "width": width, "content_bottom": top, "margin": margin,
        "title_h": title_h, "used_bands": used, "band_tops": band_tops,
        "centers": centers, "boxes": boxes, "ordered": ordered,
    }


def _marker_defs(used_types):
    parts = []
    for t in used_types:
        c = RELATION_STYLE[t]["stroke"]
        parts.append('<marker id="arrow-%s" viewBox="0 0 10 10" refX="9" refY="5" '
                     'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                     '<path d="M0,0 L10,5 L0,10 z" fill="%s"/></marker>' % (t, c))
        parts.append('<marker id="bar-%s" viewBox="0 0 10 10" refX="9" refY="5" '
                     'markerWidth="8" markerHeight="8" orient="auto">'
                     '<path d="M2,1 L2,9" stroke="%s" stroke-width="2.5" fill="none"/></marker>' % (t, c))
    return "\n    ".join(parts)


def _legend_items(norm):
    items = []
    rel_types = sorted({r["type"] for r in norm["relations"]},
                       key=lambda t: list(RELATION_STYLE).index(t) if t in RELATION_STYLE else 999)
    for t in rel_types:
        items.append({"kind": "relation", "type": t, "label": RELATION_STYLE[t]["label"]})
    nt_types = sorted({e["name_type"] for e in norm["entities"]},
                      key=lambda t: list(NAME_TYPE_STYLE).index(t) if t in NAME_TYPE_STYLE else 999)
    for t in nt_types:
        items.append({"kind": "entity", "type": t, "label": NAME_TYPE_STYLE[t]["label"]})
    for m in norm["data_mappings"]:
        items.append({"kind": "mapping", "channel": m.get("channel"),
                      "metric": m.get("metric"),
                      "label": "%s encodes %s" % (m.get("channel"), m.get("metric"))})
    return items


def _build_svg(norm):
    geo = _geometry(norm)
    legend = _legend_items(norm)
    legend_rows = min(len(legend), 6)
    legend_height = (34.0 if legend else 0.0) + 24.0 * legend_rows + 18.0
    width = geo["width"]
    height = geo["content_bottom"] + legend_height
    margin = geo["margin"]
    mappings = norm["data_mappings"]

    used_types = sorted({r["type"] for r in norm["relations"]},
                        key=lambda t: list(RELATION_STYLE).index(t) if t in RELATION_STYLE else 999)

    svg = []
    svg.append('<?xml version="1.0" encoding="UTF-8"?>')
    svg.append('<svg xmlns="http://www.w3.org/2000/svg" width="%s" height="%s" '
               'viewBox="0 0 %s %s" font-family="%s">' % (_fmt(width), _fmt(height), _fmt(width), _fmt(height), FONT_STACK))
    svg.append('  <title>%s</title>' % _xml_escape(norm["title"]))
    svg.append('  <defs>')
    svg.append('    %s' % _marker_defs(used_types))
    svg.append('  </defs>')

    # layer-background
    svg.append('  <g id="layer-background">')
    svg.append('    <rect x="0" y="0" width="%s" height="%s" fill="#ffffff"/>' % (_fmt(width), _fmt(height)))
    svg.append('  </g>')

    # layer-title
    svg.append('  <g id="layer-title">')
    svg.append('    <text x="%s" y="30" font-size="20" font-weight="bold" fill="#212121">%s</text>'
               % (_fmt(margin), _xml_escape(norm["title"])))
    svg.append('  </g>')

    # layer-compartments
    svg.append('  <g id="layer-compartments">')
    for c in geo["used_bands"]:
        y = geo["band_tops"][c]
        h = BAND_HEIGHT[c]
        if c == "membrane":
            svg.append('    <rect x="%s" y="%s" width="%s" height="%s" fill="#fffde7" '
                       'stroke="#bdbdbd" stroke-width="1"/>' % (_fmt(margin), _fmt(y), _fmt(width - 2 * margin), _fmt(h)))
            mid = y + h / 2.0
            svg.append('    <line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#f9a825" stroke-width="1.4"/>'
                       % (_fmt(margin + 8), _fmt(mid), _fmt(width - margin - 8), _fmt(mid)))
        else:
            svg.append('    <rect x="%s" y="%s" width="%s" height="%s" fill="#fafafa" '
                       'stroke="#e0e0e0" stroke-width="1"/>' % (_fmt(margin), _fmt(y), _fmt(width - 2 * margin), _fmt(h)))
        svg.append('    <text x="%s" y="%s" font-size="12" fill="#757575" font-style="italic">%s</text>'
                   % (_fmt(margin + 8), _fmt(y + 16), _xml_escape(COMPARTMENT_LABEL.get(c, c))))
    svg.append('  </g>')

    # layer-edges
    svg.append('  <g id="layer-edges">')
    for r in norm["relations"]:
        st = RELATION_STYLE[r["type"]]
        src, dst = r["from"], r["to"]
        if src not in geo["centers"] or dst not in geo["centers"]:
            continue
        sx, sy = geo["centers"][src]
        tx, ty = geo["centers"][dst]
        shw, shh = geo["boxes"][src]
        thw, thh = geo["boxes"][dst]
        direction = r["direction"]
        stroke = st["stroke"]
        dash = st["dash"]
        marker = ""
        if st["end"] == "bar":
            marker = ' marker-end="url(#bar-%s)"' % r["type"]
        elif st["end"] == "arrow":
            marker = ' marker-end="url(#arrow-%s)"' % r["type"]
        if direction == "bidirectional" and st["end"] == "arrow":
            marker += ' marker-start="url(#arrow-%s)"' % r["type"]

        dash_attr = ' stroke-dasharray="%s"' % dash if dash else ""
        d_attr = ' data-relation-id="%s" data-type="%s" data-direction="%s"' % (
            _xml_escape(r["id"]), _xml_escape(r["type"]), _xml_escape(direction))

        if direction == "self":
            cx, cy = geo["centers"][src]
            hh = geo["boxes"][src][1]
            x1, y1 = cx - 12.0, cy - hh
            x2, y2 = cx + 12.0, cy - hh
            d = ("M %s %s C %s %s, %s %s, %s %s"
                 % (_fmt(x1), _fmt(y1), _fmt(x1 - 26), _fmt(y1 - 34),
                    _fmt(x2 + 26), _fmt(y2 - 34), _fmt(x2), _fmt(y2)))
            svg.append('    <path d="%s" fill="none" stroke="%s" stroke-width="2"%s%s%s/>'
                       % (d, stroke, dash_attr, marker, d_attr))
            continue

        if direction == "reverse":
            # arrow lands on the 'from' node (signal flows to->from)
            start = _on_boundary(tx, ty, sx, sy, thw, thh)
            end = _on_boundary(sx, sy, tx, ty, shw, shh)
        else:
            start = _on_boundary(sx, sy, tx, ty, shw, shh)
            end = _on_boundary(tx, ty, sx, sy, thw, thh)

        line = '    <line x1="%s" y1="%s" x2="%s" y2="%s" fill="none" stroke="%s" stroke-width="2"%s%s%s/>' % (
            _fmt(start[0]), _fmt(start[1]), _fmt(end[0]), _fmt(end[1]), stroke, dash_attr, marker, d_attr)
        svg.append(line)

        # modification carries a small modifier tag at the midpoint
        if r["type"] == "modification":
            mx, my = (start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0 - 6.0
            tag = r["modifier"] or "mod"
            svg.append('    <text x="%s" y="%s" font-size="11" fill="%s" text-anchor="middle">%s</text>'
                       % (_fmt(mx), _fmt(my), stroke, _xml_escape(tag)))
    svg.append('  </g>')

    # layer-nodes
    svg.append('  <g id="layer-nodes">')
    for e in geo["ordered"]:
        cx, cy = geo["centers"][e["id"]]
        hw, hh = geo["boxes"][e["id"]]
        st = NAME_TYPE_STYLE[e["name_type"]]
        fill, sw = _entity_display(e, mappings)
        lines = _wrap_label(e["label"])
        src_attr = ""
        locators = [s.get("locator") for s in e.get("sources", []) if s.get("locator")]
        if locators:
            src_attr = ' data-source="%s"' % _xml_escape("; ".join(locators))
        node_attr = (' data-entity-id="%s" data-name-type="%s" data-compartment="%s"%s'
                     % (_xml_escape(e["id"]), _xml_escape(e["name_type"]),
                        _xml_escape(e["compartment"]), src_attr))
        x, y = cx - hw, cy - hh
        w, h = 2 * hw, 2 * hh
        stroke = st["stroke"]
        if e["name_type"] == "family":
            dash_attr = ' stroke-dasharray="4 3"'
        else:
            dash_attr = ""
        if st["shape"] == "ellipse":
            svg.append('    <ellipse cx="%s" cy="%s" rx="%s" ry="%s" fill="%s" stroke="%s" stroke-width="%s"%s%s/>'
                       % (_fmt(cx), _fmt(cy), _fmt(hw), _fmt(hh), fill, stroke, _fmt(sw), dash_attr, node_attr))
        elif st["shape"] == "circle":
            r = min(hw, hh)
            svg.append('    <circle cx="%s" cy="%s" r="%s" fill="%s" stroke="%s" stroke-width="%s"%s%s/>'
                       % (_fmt(cx), _fmt(cy), _fmt(r), fill, stroke, _fmt(sw), dash_attr, node_attr))
        else:
            rx = 18 if st["shape"] == "round" else 6
            svg.append('    <rect x="%s" y="%s" width="%s" height="%s" rx="%s" fill="%s" stroke="%s" stroke-width="%s"%s%s/>'
                       % (_fmt(x), _fmt(y), _fmt(w), _fmt(h), rx, fill, stroke, _fmt(sw), dash_attr, node_attr))
        line_h = 15
        total_h = line_h * len(lines)
        ty0 = cy - total_h / 2.0 + line_h * 0.75
        fstyle = "italic" if e["name_type"] == "gene" else "normal"
        for j, ln in enumerate(lines):
            svg.append('    <text x="%s" y="%s" font-size="13" font-style="%s" fill="#212121" text-anchor="middle">%s</text>'
                       % (_fmt(cx), _fmt(ty0 + j * line_h), fstyle, _xml_escape(ln)))
    svg.append('  </g>')

    # layer-legend
    svg.append('  <g id="layer-legend">')
    if legend:
        lx = margin
        ly = geo["content_bottom"] + 20.0
        svg.append('    <text x="%s" y="%s" font-size="13" font-weight="bold" fill="#424242">Legend</text>'
                   % (_fmt(lx), _fmt(ly)))
        row = 0
        col = 0
        for item in legend:
            rx = lx + col * 250.0
            ry = ly + 14.0 + row * 24.0
            if item["kind"] == "relation":
                st = RELATION_STYLE[item["type"]]
                svg.append('    <line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="2"%s%s/>'
                           % (_fmt(rx), _fmt(ry), _fmt(rx + 34), _fmt(ry), st["stroke"],
                              (' stroke-dasharray="%s"' % st["dash"]) if st["dash"] else "",
                              (' marker-end="url(#arrow-%s)"' % item["type"]) if st["end"] == "arrow" else
                              (' marker-end="url(#bar-%s)"' % item["type"]) if st["end"] == "bar" else ""))
                svg.append('    <text x="%s" y="%s" font-size="12" fill="#424242">%s</text>'
                           % (_fmt(rx + 44), _fmt(ry + 4), _xml_escape(item["label"])))
            elif item["kind"] == "entity":
                st = NAME_TYPE_STYLE[item["type"]]
                if st["shape"] == "ellipse":
                    svg.append('    <ellipse cx="%s" cy="%s" rx="14" ry="9" fill="%s" stroke="%s" stroke-width="1.4"/>'
                               % (_fmt(rx + 18), _fmt(ry - 4), st["fill"], st["stroke"]))
                elif st["shape"] == "circle":
                    svg.append('    <circle cx="%s" cy="%s" r="9" fill="%s" stroke="%s" stroke-width="1.4"/>'
                               % (_fmt(rx + 18), _fmt(ry - 4), st["fill"], st["stroke"]))
                else:
                    svg.append('    <rect x="%s" y="%s" width="30" height="16" rx="4" fill="%s" stroke="%s" stroke-width="1.4"/>'
                               % (_fmt(rx + 4), _fmt(ry - 11), st["fill"], st["stroke"]))
                svg.append('    <text x="%s" y="%s" font-size="12" fill="#424242">%s</text>'
                           % (_fmt(rx + 44), _fmt(ry + 4), _xml_escape(item["label"])))
            else:
                svg.append('    <rect x="%s" y="%s" width="30" height="14" fill="%s" stroke="#90a4ae" stroke-width="1"/>'
                           % (_fmt(rx + 4), _fmt(ry - 11), COLOR_PALETTE[3]))
                svg.append('    <text x="%s" y="%s" font-size="12" fill="#424242">%s</text>'
                           % (_fmt(rx + 44), _fmt(ry + 4), _xml_escape(item["label"])))
            row += 1
            if row >= 6:
                row = 0
                col += 1
    svg.append('  </g>')
    svg.append('</svg>')
    return "\n".join(svg) + "\n"


def build_svg(spec) -> str:
    norm, _, _ = normalize_spec(spec)
    return _build_svg(norm)


# ---------------------------------------------------------------------------
# result.json assembly
# ---------------------------------------------------------------------------
def _aggregate_sources(norm):
    out = []
    for e in norm["entities"]:
        for s in e.get("sources", []):
            out.append({"element": e["id"], "kind": "entity", "locator": s.get("locator"),
                        "quote": s.get("quote"), "confidence": s.get("confidence")})
    for r in norm["relations"]:
        for s in r.get("sources", []):
            out.append({"element": r["id"], "kind": "relation", "locator": s.get("locator"),
                        "quote": s.get("quote"), "confidence": s.get("confidence")})
    return out


def _default_limitations(status):
    if status == "partial":
        return ["partial: SVG rendered from spec; export artifacts, final source audit and human confirmation still pending"]
    if status == "failed":
        return ["failed: spec validation produced blocking errors (see errors field)"]
    if status == "insufficient_evidence":
        return ["no materials extracted yet"]
    return []


def build_result(case_id, spec, status="partial"):
    norm, errors, warnings = normalize_spec(spec)
    svg = _build_svg(norm)
    result = {
        "case_id": case_id,
        "status": status,
        "title": norm["title"],
        "editable_source_files": [{
            "path": "mechanism.svg",
            "format": "svg",
            "editable": True,
            "layers": ["layer-background", "layer-title", "layer-compartments",
                       "layer-edges", "layer-nodes", "layer-legend"],
            "sha256": sha256_hex(svg),
        }],
        "export_artifacts": [],
        "entities": norm["entities"],
        "relations": norm["relations"],
        "legend": _legend_items(norm),
        "data_mappings": norm["data_mappings"],
        "sources": _aggregate_sources(norm),
        "uncertainty": norm["uncertainty"],
        "pending_human": [],
        "change_log": [{"sec": 0, "phase": "A", "action": "skeleton written"}],
        "limitations": _default_limitations(status),
        "degradations": [],
        "errors": errors if status == "failed" else [],
        "determinism": {
            "schema_version": SCHEMA_VERSION,
            "spec_sha256": sha256_hex(norm),
            "svg_sha256": sha256_hex(svg),
        },
        "runtime": {"model_requests_used": None, "wall_seconds": None},
    }
    return result, errors, warnings


def skeleton_result(case_id):
    return {
        "case_id": case_id,
        "status": "insufficient_evidence",
        "title": "pending extraction",
        "editable_source_files": [],
        "export_artifacts": [],
        "entities": [],
        "relations": [],
        "legend": [],
        "data_mappings": [],
        "sources": [],
        "uncertainty": ["materials not yet read; skeleton only"],
        "pending_human": [],
        "change_log": [{"sec": 0, "phase": "A", "action": "skeleton written before extraction"}],
        "limitations": ["skeleton written before materials were read; no scientific content yet"],
        "degradations": [],
        "errors": [],
        "determinism": {"schema_version": SCHEMA_VERSION, "spec_sha256": None, "svg_sha256": None},
        "runtime": {"model_requests_used": None, "wall_seconds": None},
    }


def detect_case_id(case_path, task_path):
    case = {}
    try:
        case = load_json(case_path)
    except Exception:
        case = {}
    if isinstance(case, dict):
        for key in ("case_id", "id", "caseId", "challenge_id", "armiId"):
            v = case.get(key)
            if isinstance(v, str) and v:
                return v
    if os.path.exists(task_path):
        try:
            return "case-" + sha256_hex(_read_text(task_path))[:12]
        except Exception:
            pass
    return "case-unknown"


# ---------------------------------------------------------------------------
# result.json contract validation
# ---------------------------------------------------------------------------
REQUIRED_FIELDS = [
    "case_id", "status", "editable_source_files", "export_artifacts",
    "entities", "relations", "legend", "data_mappings", "sources",
    "uncertainty", "change_log",
]


def check_result(result, output_dir):
    errors, warnings = [], []
    if not isinstance(result, dict):
        return ["result must be a JSON object"], []
    for f in REQUIRED_FIELDS:
        if f not in result:
            errors.append("missing required field %r" % f)
    if result.get("case_id") in (None, ""):
        errors.append("case_id is empty")
    status = result.get("status")
    if status not in STATUSES:
        errors.append("invalid status %r" % status)
    if status in ("partial", "failed", "insufficient_evidence"):
        lims = result.get("limitations")
        if not isinstance(lims, list) or not lims:
            warnings.append("status=%r but limitations empty" % status)

    sfiles = result.get("editable_source_files")
    if not isinstance(sfiles, list) or not sfiles:
        errors.append("editable_source_files must be a non-empty list")
    else:
        for i, sf in enumerate(sfiles):
            p = sf.get("path") if isinstance(sf, dict) else None
            if not p:
                errors.append("editable_source_files[%d].path missing" % i)
                continue
            full = os.path.join(output_dir, os.path.basename(str(p)))
            if not os.path.isfile(full):
                errors.append("source file missing on disk: %s" % p)

    arts = result.get("export_artifacts")
    if not isinstance(arts, list) or not arts:
        warnings.append("export_artifacts empty (SVG-only delivery)")
    else:
        for i, a in enumerate(arts):
            p = a.get("path") if isinstance(a, dict) else None
            if not p:
                errors.append("export_artifacts[%d].path missing" % i)
                continue
            full = os.path.join(output_dir, os.path.basename(str(p)))
            if not os.path.isfile(full):
                errors.append("export artifact missing on disk: %s" % p)

    cl = result.get("change_log")
    if not isinstance(cl, list) or not cl:
        warnings.append("change_log empty")
    else:
        first = cl[0] if isinstance(cl[0], dict) else {}
        sec = first.get("sec")
        if not isinstance(sec, (int, float)) or sec > 180:
            warnings.append("change_log first entry should be a skeleton at sec<=180")

    det = result.get("determinism")
    if not isinstance(det, dict) or not det.get("svg_sha256"):
        warnings.append("determinism.svg_sha256 missing")
    return errors, warnings
