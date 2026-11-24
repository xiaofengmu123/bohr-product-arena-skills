#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Core implementation for the weekly-skill biological mechanism/pathway figure package.

Standard library only (json, os, re, sys, math, html, xml, tempfile, datetime).
Deterministic: the same spec.json always renders the same SVG.

Design contract (single source of truth):
  spec.json is the scientific model AND the rendering model. `build` renders it,
  `init` writes a valid partial result.json early, `check` audits the result.json
  against the output contract and the files on disk. No result field is invented;
  missing values stay null/[] and are reported as warnings/limitations.
"""

from __future__ import annotations

import html
import json
import math
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timezone

SPEC_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Style tables (single definition reused by renderer AND legend, so figure and
# legend can never diverge).
# ---------------------------------------------------------------------------

ENTITY_TYPES = {
    "gene": "基因 gene",
    "protein": "蛋白 protein",
    "complex": "复合物 complex",
    "family": "家族 family",
    "cell_type": "细胞 cell",
    "small_molecule": "小分子 small molecule",
    "other": "其他 other",
}

# fill, border, border-dash, shape
ENTITY_STYLE = {
    "gene": ("#FFFFFF", "#212121", None, "rect"),
    "protein": ("#E3F2FD", "#1565C0", None, "roundrect"),
    "complex": ("#FFF3E0", "#EF6C00", None, "hexagon"),
    "family": ("#F3E5F5", "#6A1B9A", "5,3", "roundrect"),
    "cell_type": ("#E8F5E9", "#2E7D32", None, "ellipse"),
    "small_molecule": ("#FCE4EC", "#C62828", None, "ellipse"),
    "other": ("#ECEFF1", "#455A64", None, "roundrect"),
}

# type -> (color, end_marker, dash, width, label)
RELATION_TYPES = {
    "activation": ("#C62828", "arrow", None, 2.2, "激活 / activation"),
    "inhibition": ("#1565C0", "tbar", None, 2.2, "抑制 / inhibition"),
    "binding": ("#6A1B9A", "dot", None, 1.6, "结合 / binding"),
    "modification": ("#EF6C00", "arrow", "6,4", 1.8, "修饰 / modification"),
    "transport": ("#2E7D32", "openarrow", None, 2.2, "转运 / transport"),
    "conversion": ("#00695C", "doublearrow", None, 2.2, "转化 / conversion"),
    "recruitment": ("#8D6E63", "arrow", "5,4", 1.6, "募集 / recruitment"),
    "unknown": ("#546E7A", "arrow", "2,3", 1.4, "关系未定 / unspecified"),
}

MODIFIER_GLYPH = {
    "phosphorylation": "P",
    "ubiquitination": "Ub",
    "acetylation": "Ac",
    "methylation": "Me",
    "sumoylation": "SUMO",
    "glycosylation": "G",
    "cleavage": "\u2717",
    "default": "\u2295",
}

# default compartment bands (top -> bottom); overridden by spec.compartments.
DEFAULT_COMPARTMENTS = [
    {"id": "extracellular", "label": "细胞外 Extracellular", "color": "#E8EAF6"},
    {"id": "membrane", "label": "细胞膜 Membrane", "color": "#F3E5F5"},
    {"id": "cytosol", "label": "细胞质 Cytosol", "color": "#E8F5E9"},
    {"id": "nucleus", "label": "细胞核 Nucleus", "color": "#FFF8E1"},
]

FONT_STACK = "Arial, 'Helvetica Neue', 'Noto Sans', 'DejaVu Sans', sans-serif"


# ---------------------------------------------------------------------------
# small I/O helpers (utf-8-sig tolerant, never crash on bad input)
# ---------------------------------------------------------------------------

def load_json(path, default=None):
    """Read a JSON file tolerating BOM and encoding issues. Returns default on failure."""
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return json.load(fh)
    except Exception:
        return default


def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def svg_esc(text):
    if text is None:
        return ""
    return html.escape(str(text), quote=True)


# ---------------------------------------------------------------------------
# spec validation / normalization
# ---------------------------------------------------------------------------

def _norm_sources(sources):
    out = []
    for s in (sources or []):
        if not isinstance(s, dict):
            continue
        out.append({
            "locator": str(s.get("locator") or ""),
            "quote": str(s.get("quote") or ""),
            "version": str(s.get("version") or ""),
        })
    return out


def normalize_spec(spec):
    """Normalize a spec.json and return (spec, errors, warnings).

    Errors are structural (blocking); warnings are scientific/quality gaps that
    the Agent should resolve or record as uncertainty.
    """
    errors, warnings = [], []
    if not isinstance(spec, dict):
        return {"spec_version": SPEC_VERSION}, ["spec is not a JSON object"], []
    spec.setdefault("spec_version", SPEC_VERSION)
    spec.setdefault("case_id", "")
    spec.setdefault("title", "")

    # compartments
    comps = spec.get("compartments")
    if not comps:
        comps = [dict(c) for c in DEFAULT_COMPARTMENTS]
        spec["compartments"] = comps
    comp_ids = []
    for c in comps:
        c.setdefault("id", "")
        c.setdefault("label", c.get("id") or "")
        c.setdefault("color", "#F5F5F5")
        if not c["id"]:
            errors.append("compartment with empty id")
        elif c["id"] in comp_ids:
            errors.append("duplicate compartment id: %s" % c["id"])
        comp_ids.append(c["id"])

    # entities
    entities = spec.get("entities") or []
    seen_eids = set()
    for e in entities:
        eid = e.get("id")
        if not eid:
            errors.append("entity with empty id")
            continue
        if eid in seen_eids:
            errors.append("duplicate entity id: %s" % eid)
            continue
        seen_eids.add(eid)
        e.setdefault("label", eid)
        e.setdefault("type", "other")
        if e["type"] not in ENTITY_TYPES:
            warnings.append("entity %s has unknown type %r -> other" % (eid, e.get("type")))
            e["type"] = "other"
        e.setdefault("compartment", comp_ids[-1] if comp_ids else "")
        if e["compartment"] not in comp_ids:
            warnings.append("entity %s compartment %r not in compartments" % (eid, e["compartment"]))
        e.setdefault("transmembrane", False)
        e.setdefault("display", {})
        disp = e["display"]
        disp.setdefault("italic", e["type"] == "gene")
        disp.setdefault("prefix", "")
        disp.setdefault("suffix", "")
        e.setdefault("aliases", [])
        e["sources"] = _norm_sources(e.get("sources"))
        if not e["sources"]:
            warnings.append("entity %s has no source" % eid)
        elif any(not s["quote"].strip() for s in e["sources"]):
            warnings.append("entity %s source missing verbatim quote" % eid)

    # relations
    relations = spec.get("relations") or []
    seen_rids = set()
    for r in relations:
        rid = r.get("id")
        if not rid:
            errors.append("relation with empty id")
            continue
        if rid in seen_rids:
            errors.append("duplicate relation id: %s" % rid)
            continue
        seen_rids.add(rid)
        r.setdefault("source", "")
        r.setdefault("target", "")
        r.setdefault("type", "unknown")
        if r["type"] not in RELATION_TYPES:
            warnings.append("relation %s has unknown type %r -> unknown" % (rid, r.get("type")))
            r["type"] = "unknown"
        if not r["source"] or not r["target"]:
            errors.append("relation %s missing source/target" % rid)
        else:
            if r["source"] not in seen_eids:
                errors.append("relation %s source %r not an entity id" % (rid, r["source"]))
            if r["target"] not in seen_eids:
                errors.append("relation %s target %r not an entity id" % (rid, r["target"]))
        r.setdefault("direction", "forward")
        if r["direction"] not in ("forward", "reverse", "bidirectional"):
            warnings.append("relation %s direction %r -> forward" % (rid, r.get("direction")))
            r["direction"] = "forward"
        r.setdefault("modifier", "")
        r.setdefault("evidence", "")
        r["sources"] = _norm_sources(r.get("sources"))
        if not r["sources"]:
            warnings.append("relation %s has no source" % rid)
        elif any(not s["quote"].strip() for s in r["sources"]):
            warnings.append("relation %s source missing verbatim quote" % rid)

    # legend / data_mappings / uncertainty
    spec.setdefault("legend", [])
    spec.setdefault("data_mappings", [])
    spec.setdefault("uncertainty", [])
    spec.setdefault("notes", "")
    for dm in spec.get("data_mappings") or []:
        dm.setdefault("encoding", "")
        dm.setdefault("channel", "")
        dm.setdefault("scale", {})
        dm.setdefault("values", {})
        dm.setdefault("sources", [])

    return spec, errors, warnings


# ---------------------------------------------------------------------------
# layout engine (compartment-aware, rank-based, feedback arcs)
# ---------------------------------------------------------------------------

def _entity_index(spec):
    idx = {}
    for i, e in enumerate(spec.get("entities") or []):
        idx[e["id"]] = (i, e)
    return idx


def compute_layout(spec):
    """Assign (x, y, w, h) to every entity. Deterministic and cycle-safe.

    Returns (layout, rank, comp_band, left, right, W, feedback_set). Feedback
    edges (backward arcs / cycle members) are excluded from rank propagation so
    a feedback loop can never inflate ranks unboundedly.
    """
    W = int(spec.get("svg", {}).get("width", 1000))
    left = 64.0
    right = 32.0
    eidx = _entity_index(spec)
    entities = spec.get("entities") or []
    eids = [e["id"] for e in entities]
    id_set = set(eids)

    # ---- cycle-safe ordering: Kahn's algorithm on the full relation graph ----
    indeg = {e: 0 for e in eids}
    adj = {e: [] for e in eids}
    edges = []
    for r in spec.get("relations") or []:
        s, t = r.get("source"), r.get("target")
        if s in id_set and t in id_set:
            edges.append((s, t, r["id"]))
            indeg[t] += 1
            adj[s].append(t)
    queue = deque([e for e in eids if indeg[e] == 0])
    order = []
    while queue:
        n = queue.popleft()
        order.append(n)
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)

    feedback_set = set()
    ordered_set = set(order)
    # nodes/edges left outside the topological order sit in a cycle
    if len(order) < len(eids):
        for s, t, rid in edges:
            if s not in ordered_set or t not in ordered_set:
                feedback_set.add(rid)
    pos = {n: i for i, n in enumerate(order)}
    forward_edges = []
    for s, t, rid in edges:
        if rid in feedback_set:
            continue
        if pos.get(s, 0) < pos.get(t, 0):
            forward_edges.append((s, t))
        else:
            feedback_set.add(rid)

    # longest path over forward (acyclic) edges only
    rank = {e: 0 for e in eids}
    changed = True
    guard = 0
    while changed and guard <= len(eids):
        changed = False
        for s, t in forward_edges:
            if rank[t] < rank[s] + 1:
                rank[t] = rank[s] + 1
                changed = True
        guard += 1

    max_rank = max(rank.values()) if rank else 0
    cols = max_rank + 1
    col_w = (W - left - right) / max(cols, 1)

    # group entities by compartment and rank
    comps = spec.get("compartments") or []
    comp_of = {e["id"]: e.get("compartment", comps[-1]["id"] if comps else "") for e in entities}
    groups = {}  # (comp_id, rank) -> [entity]
    for e in entities:
        key = (comp_of[e["id"]], rank[e["id"]])
        groups.setdefault(key, []).append(e)

    # per-compartment rows = max entities sharing a rank
    comp_rows = {}
    for (cid, r), lst in groups.items():
        comp_rows[cid] = max(comp_rows.get(cid, 1), len(lst))

    y_cursor = 56.0
    header_h = 30.0
    row_h = 74.0
    node_h = 46.0
    comp_band = {}
    for c in comps:
        cid = c["id"]
        nrows = comp_rows.get(cid, 1)
        band_h = header_h + 14.0 + (nrows - 1) * row_h + 46.0
        comp_band[cid] = (y_cursor, y_cursor + band_h)
        y_cursor += band_h + 12.0

    layout = {}
    for cid, band in comp_band.items():
        band_top, band_bot = band
        for r in range(cols):
            lst = groups.get((cid, r), [])
            lst = sorted(lst, key=lambda e: e["id"])
            inner_top = band_top + header_h + 10.0
            inner_h = (band_bot - inner_top - 26.0)
            for k, e in enumerate(lst):
                label = display_label(e)
                w = max(120.0, min(250.0, 20.0 + len(label) * 7.4))
                cx = left + r * col_w + col_w / 2.0
                cy = inner_top + inner_h / 2.0 + (k - (len(lst) - 1) / 2.0) * row_h
                layout[e["id"]] = {"x": cx, "y": cy, "w": w, "h": node_h, "rank": rank[e["id"]],
                                   "compartment": cid, "row": k}
    return layout, rank, comp_band, left, right, W, feedback_set


def display_label(e):
    disp = e.get("display") or {}
    prefix = disp.get("prefix", "")
    suffix = disp.get("suffix", "")
    raw = str(e.get("label") or e.get("id") or "")
    label = (prefix + raw + suffix) if not raw.startswith(prefix) else raw
    # wrap long labels
    if "\n" not in label and len(label) > 24:
        mid = len(label) // 2
        space = label.rfind(" ", 0, mid)
        cut = space if space > 0 else mid
        label = label[:cut] + "\n" + label[cut + 1:]
    return label


def _classify_feedback(r, rank, feedback_set):
    s, t = r["source"], r["target"]
    if s == t:
        return "self"
    if r["id"] in feedback_set:
        return "feedback"
    if rank.get(s, 0) >= rank.get(t, 0):
        return "feedback"
    return "forward"


# ---------------------------------------------------------------------------
# data mapping application
# ---------------------------------------------------------------------------

def _parse_numeric_scale(scale):
    if isinstance(scale, dict):
        try:
            return float(scale.get("min", 0.6)), float(scale.get("max", 1.8))
        except Exception:
            return 0.6, 1.8
    if isinstance(scale, str):
        m = re.match(r"^\s*min\s*=\s*([0-9.+-]+)\s*,\s*max\s*=\s*([0-9.+-]+)\s*$", scale)
        if m:
            return float(m.group(1)), float(m.group(2))
    return 0.6, 1.8


def _parse_cat_scale(scale):
    """Parse 'high=#C62828,low=#B0BEC5' or a dict category->color."""
    out = {}
    if isinstance(scale, dict):
        out = {str(k): str(v) for k, v in scale.items()}
    elif isinstance(scale, str):
        for part in scale.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def apply_data_mappings(spec, layout):
    """Return overrides dict {entity_id: {fill|size}} and {relation_id: {width}}."""
    node_fill, node_size, edge_width = {}, {}, {}
    for dm in spec.get("data_mappings") or []:
        enc = dm.get("encoding")
        values = dm.get("values") or {}
        if enc == "node_fill":
            cmap = _parse_cat_scale(dm.get("scale"))
            for k, v in values.items():
                if k in layout and str(v) in cmap:
                    node_fill[k] = cmap[str(v)]
        elif enc == "node_size":
            vmin, vmax = 1e9, -1e9
            nums = {}
            for k, v in values.items():
                if k in layout:
                    try:
                        fv = float(v)
                    except Exception:
                        continue
                    nums[k] = fv
                    vmin, vmax = min(vmin, fv), max(vmax, fv)
            smin, smax = _parse_numeric_scale(dm.get("scale"))
            for k, v in nums.items():
                if vmax > vmin:
                    node_size[k] = smin + (v - vmin) / (vmax - vmin) * (smax - smin)
                else:
                    node_size[k] = 1.0
        elif enc == "edge_width":
            vmin, vmax = 1e9, -1e9
            nums = {}
            for k, v in values.items():
                try:
                    nums[k] = float(v)
                    vmin, vmax = min(vmin, v), max(vmax, v)
                except Exception:
                    pass
            smin, smax = _parse_numeric_scale(dm.get("scale"))
            for k, v in nums.items():
                if vmax > vmin:
                    edge_width[k] = smin + (v - vmin) / (vmax - vmin) * (smax - smin)
                else:
                    edge_width[k] = 1.0
    return node_fill, node_size, edge_width


# ---------------------------------------------------------------------------
# geometry helpers
# ---------------------------------------------------------------------------

def _rect_intersect_ts(sx, sy, dx, dy, cx, cy, hw, hh):
    """t values in (0,1) where segment src->(src+d) crosses rect boundary."""
    ts = []
    if abs(dx) > 1e-9:
        for x in (cx - hw, cx + hw):
            t = (x - sx) / dx
            if 0 < t < 1:
                y = sy + t * dy
                if cy - hh <= y <= cy + hh:
                    ts.append(t)
    if abs(dy) > 1e-9:
        for y in (cy - hh, cy + hh):
            t = (y - sy) / dy
            if 0 < t < 1:
                x = sx + t * dx
                if cx - hw <= x <= cx + hw:
                    ts.append(t)
    return sorted(ts)


def _edge_clip(a, b, eps=3.0):
    sx, sy = a["x"], a["y"]
    tx, ty = b["x"], b["y"]
    dx, dy = tx - sx, ty - sy
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return (sx, sy), (tx, ty)
    src_ts = _rect_intersect_ts(sx, sy, dx, dy, sx, sy, a["w"] / 2 + 1, a["h"] / 2 + 1)
    tgt_ts = _rect_intersect_ts(sx, sy, dx, dy, tx, ty, b["w"] / 2 + 1, b["h"] / 2 + 1)
    t_start = src_ts[-1] if src_ts else 0.0
    t_end = tgt_ts[0] if tgt_ts else 1.0
    if t_end <= t_start:
        t_start, t_end = 0.0, 1.0
    # shrink slightly so marker tip is just outside the node
    t_start += eps / (math.hypot(dx, dy) or 1)
    t_end -= eps / (math.hypot(dx, dy) or 1)
    x1, y1 = sx + t_start * dx, sy + t_start * dy
    x2, y2 = sx + t_end * dx, sy + t_end * dy
    return (x1, y1), (x2, y2)


def _side_point(node, side):
    x, y, w, h = node["x"], node["y"], node["w"], node["h"]
    if side == "left":
        return (x - w / 2 - 2, y)
    if side == "right":
        return (x + w / 2 + 2, y)
    if side == "top":
        return (x, y - h / 2 - 2)
    return (x, y + h / 2 + 2)


# ---------------------------------------------------------------------------
# SVG generation
# ---------------------------------------------------------------------------

def _marker_defs(used_colors):
    out = []
    for color in used_colors:
        out.append(
            '<marker id="arrow-%s" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            '<path d="M 0 0 L 10 5 L 0 10 z" fill="%s"/></marker>' % (color[1:], color)
        )
        out.append(
            '<marker id="openarrow-%s" viewBox="0 0 10 10" refX="8" refY="5" '
            'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            '<path d="M 0 1 L 9 5 L 0 9" fill="none" stroke="%s" stroke-width="1.4"/></marker>'
            % (color[1:], color)
        )
        out.append(
            '<marker id="tbar-%s" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
            '<path d="M 9 0 L 9 10" stroke="%s" stroke-width="2.4"/></marker>' % (color[1:], color)
        )
        out.append(
            '<marker id="doublearrow-%s" viewBox="0 0 10 10" refX="5" refY="5" '
            'markerWidth="7" markerHeight="7" orient="auto">'
            '<path d="M 0 0 L 5 5 L 0 10 L 1.6 10 L 6.6 5 L 1.6 0 z" fill="%s"/>'
            '<path d="M 10 0 L 5 5 L 10 10 L 8.4 10 L 3.4 5 L 8.4 0 z" fill="%s"/></marker>'
            % (color[1:], color, color)
        )
    return "\n".join(out)


def _edge_marker_id(marker, color):
    if marker in ("arrow", "openarrow", "tbar", "doublearrow"):
        return "url(#%s-%s)" % (marker, color[1:])
    return ""


def _node_shape(e, w, h, fill, border, dash):
    shape = ENTITY_STYLE.get(e.get("type"), ENTITY_STYLE["other"])[3]
    x, y = 0.0, 0.0
    dash_attr = ' stroke-dasharray="%s"' % dash if dash else ""
    if shape == "rect":
        return ('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="0" '
                'fill="%s" stroke="%s" stroke-width="1.6"%s/>'
                % (x - w / 2, y - h / 2, w, h, fill, border, dash_attr))
    if shape == "roundrect":
        return ('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="9" '
                'fill="%s" stroke="%s" stroke-width="1.6"%s/>'
                % (x - w / 2, y - h / 2, w, h, fill, border, dash_attr))
    if shape == "ellipse":
        return ('<ellipse cx="0" cy="0" rx="%.1f" ry="%.1f" fill="%s" '
                'stroke="%s" stroke-width="1.6"%s/>' % (w / 2, h / 2, fill, border, dash_attr))
    if shape == "hexagon":
        pts = []
        rx, ry = w / 2, h / 2
        for i in range(6):
            ang = math.pi / 6 + i * math.pi / 3
            pts.append("%.1f,%.1f" % (rx * math.cos(ang), ry * math.sin(ang)))
        return ('<polygon points="%s" fill="%s" stroke="%s" stroke-width="1.6"%s/>'
                % (" ".join(pts), fill, border, dash_attr))
    return ('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="9" '
            'fill="%s" stroke="%s" stroke-width="1.6"/>' % (x - w / 2, y - h / 2, w, h, fill, border))


def _node_label_svg(e, label, h):
    lines = label.split("\n")
    italic = (e.get("display") or {}).get("italic", e.get("type") == "gene")
    font_style = 'font-style="italic"' if italic else ""
    n = len(lines)
    fs = 12 if n == 1 else 11
    dy_start = -((n - 1) * 7.0)
    parts = ['<text text-anchor="middle" font-family="%s" font-size="%.0f" %s>'
             % (FONT_STACK, fs, font_style)]
    for i, ln in enumerate(lines):
        parts.append('<tspan x="0" dy="%.1f">%s</tspan>' % ((i * 14.0 + dy_start), svg_esc(ln)))
    parts.append('</text>')
    return "".join(parts)


def build_svg(spec):
    """Render spec -> (svg_text, meta). Meta carries derived legend + geometry."""
    spec, errors, warnings = normalize_spec(spec)
    if errors:
        raise ValueError("spec has blocking errors:\n- " + "\n- ".join(errors))

    layout, rank, comp_band, left, right, W, feedback_set = compute_layout(spec)
    node_fill, node_size, edge_width = apply_data_mappings(spec, layout)

    # apply node size scaling
    for eid, k in node_size.items():
        if eid in layout:
            layout[eid]["w"] *= k
            layout[eid]["h"] *= k

    comps = spec.get("compartments") or []
    bottom = max((b for _, b in comp_band.values()), default=400.0)

    # ---- derived legend (never diverges from what is rendered) -----------
    rel_types_used = sorted({r["type"] for r in spec.get("relations") or []})
    ent_types_used = sorted({e["type"] for e in spec.get("entities") or []})
    rel_colors = {RELATION_TYPES[t][0] for t in rel_types_used}
    legend_rows = []  # (kind, key, label, extra)
    for t in rel_types_used:
        legend_rows.append(("relation", t, RELATION_TYPES[t][4], None))
    for t in ent_types_used:
        legend_rows.append(("entity", t, ENTITY_TYPES[t], None))
    for dm in spec.get("data_mappings") or []:
        enc = dm.get("encoding") or "?"
        chan = dm.get("channel") or "?"
        scale = dm.get("scale")
        txt = "%s: %s" % (enc, chan)
        if isinstance(scale, str) and scale:
            txt += "  [%s]" % scale
        elif isinstance(scale, dict) and scale:
            txt += "  [%s]" % ",".join("%s=%s" % (k, v) for k, v in scale.items())
        legend_rows.append(("mapping", enc, txt, None))

    ncols = 2
    legend_h = max(44.0, 22.0 * math.ceil(len(legend_rows) / ncols) + 18.0)
    H = bottom + legend_h + 46.0

    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append('<svg xmlns="http://www.w3.org/2000/svg" width="%.0f" height="%.0f" '
                 'viewBox="0 0 %.0f %.0f" role="img">' % (W, H, W, H))
    parts.append('<title>%s</title>' % svg_esc(spec.get("title") or spec.get("case_id") or "mechanism"))
    parts.append('<desc>case_id=%s spec_version=%s</desc>'
                 % (svg_esc(spec.get("case_id") or ""), SPEC_VERSION))
    parts.append('<defs>%s</defs>' % _marker_defs(rel_colors))

    # layer: background
    parts.append('<g id="layer-background">')
    parts.append('<rect x="0" y="0" width="%.0f" height="%.0f" fill="#FFFFFF"/>' % (W, H))
    parts.append('</g>')

    # layer: compartments
    parts.append('<g id="layer-compartments">')
    for c in comps:
        band_top, band_bot = comp_band.get(c["id"], (0, 0))
        parts.append('<rect x="%.0f" y="%.0f" width="%.0f" height="%.0f" fill="%s" '
                     'stroke="#B0BEC5" stroke-width="1" rx="6" opacity="0.55"/>'
                     % (left - 40, band_top, W - left + 12, band_bot - band_top, c.get("color", "#F5F5F5")))
        parts.append('<text x="%.0f" y="%.0f" font-family="%s" font-size="12" '
                     'font-weight="bold" fill="#37474F">%s</text>'
                     % (left - 36, band_top + 18, FONT_STACK, svg_esc(c.get("label", c["id"]))))
    parts.append('</g>')

    # layer: edges
    parts.append('<g id="layer-edges">')
    eidx = _entity_index(spec)
    for r in spec.get("relations") or []:
        if r["source"] not in layout or r["target"] not in layout:
            continue
        rtype = r["type"]
        color, marker, dash, width, rlabel = RELATION_TYPES[rtype]
        width *= edge_width.get(r["id"], 1.0)
        dash_attr = ' stroke-dasharray="%s"' % dash if dash else ""
        a, b = layout[r["source"]], layout[r["target"]]
        kind = _classify_feedback(r, rank, feedback_set)
        mid_annot = None
        if rtype == "modification":
            glyph = MODIFIER_GLYPH.get((r.get("modifier") or "").lower(), MODIFIER_GLYPH["default"])
            mid_annot = glyph
        srcs = "|".join(s.get("locator", "") for s in r.get("sources") or [])
        if kind == "self":
            x, y = a["x"], a["y"]
            p = ('<path d="M %.1f %.1f C %.1f %.1f %.1f %.1f %.1f %.1f" fill="none" '
                 'stroke="%s" stroke-width="%.1f"%s marker-end="%s"/>'
                 % (x, y - a["h"] / 2, x + 22, y - a["h"] / 2 - 26, x - 22, y - a["h"] / 2 - 26,
                    x, y - a["h"] / 2, color, width, dash_attr, _edge_marker_id(marker, color)))
        elif kind == "feedback":
            sx, sy = _side_point(a, "left")
            tx, ty = _side_point(b, "left")
            corridor = left - 52
            p = ('<path d="M %.1f %.1f C %.1f %.1f %.1f %.1f %.1f %.1f" fill="none" '
                 'stroke="%s" stroke-width="%.1f"%s marker-end="%s"/>'
                 % (sx, sy, corridor, sy, corridor, ty, tx, ty, color, width, dash_attr,
                    _edge_marker_id(marker, color)))
        else:
            (x1, y1), (x2, y2) = _edge_clip(a, b)
            m_end = _edge_marker_id(marker, color)
            m_start = ""
            if marker == "doublearrow":
                m_start = m_end
            p = ('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
                 'stroke-width="%.1f"%s marker-end="%s" marker-start="%s"/>'
                 % (x1, y1, x2, y2, color, width, dash_attr, m_end, m_start))
            if marker == "dot":
                p += ('<circle cx="%.1f" cy="%.1f" r="3" fill="%s"/>'
                      '<circle cx="%.1f" cy="%.1f" r="3" fill="%s"/>'
                      % (x1, y1, color, x2, y2, color))
            if mid_annot:
                mx, my = (x1 + x2) / 2, (y1 + y2) / 2
                p += ('<circle cx="%.1f" cy="%.1f" r="9" fill="#FFF3E0" stroke="%s" stroke-width="1"/>'
                      '<text x="%.1f" y="%.1f" text-anchor="middle" font-family="%s" '
                      'font-size="10" fill="%s">%s</text>'
                      % (mx, my, color, mx, my + 3.4, FONT_STACK, color, svg_esc(mid_annot)))
        parts.append('<g data-relation-id="%s" data-type="%s" data-feedback="%s" data-sources="%s">%s</g>'
                     % (svg_esc(r["id"]), svg_esc(rtype), kind, svg_esc(srcs), p))
    parts.append('</g>')

    # layer: nodes
    parts.append('<g id="layer-nodes">')
    for e in spec.get("entities") or []:
        if e["id"] not in layout:
            continue
        node = layout[e["id"]]
        base_fill, base_border, base_dash, _ = ENTITY_STYLE.get(e["type"], ENTITY_STYLE["other"])
        fill = node_fill.get(e["id"], base_fill)
        border = base_border
        dash = base_dash
        label = display_label(e)
        srcs = "|".join(s.get("locator", "") for s in e.get("sources") or [])
        aliases = ",".join(e.get("aliases") or [])
        shape_svg = _node_shape(e, node["w"], node["h"], fill, border, dash)
        label_svg = _node_label_svg(e, label, node["h"])
        parts.append(
            '<g transform="translate(%.1f %.1f)" data-entity-id="%s" data-type="%s" '
            'data-compartment="%s" data-transmembrane="%s" data-aliases="%s" data-sources="%s">%s%s</g>'
            % (node["x"], node["y"], svg_esc(e["id"]), svg_esc(e.get("type", "other")),
               svg_esc(e.get("compartment", "")), str(bool(e.get("transmembrane"))).lower(),
               svg_esc(aliases), svg_esc(srcs), shape_svg, label_svg))
    parts.append('</g>')

    # layer: data annotations
    parts.append('<g id="layer-data">')
    for dm in spec.get("data_mappings") or []:
        if dm.get("encoding") in ("node_size", "node_fill"):
            for k, v in (dm.get("values") or {}).items():
                if k in layout:
                    node = layout[k]
                    parts.append('<text x="%.1f" y="%.1f" text-anchor="middle" '
                                 'font-family="%s" font-size="9" fill="#546E7A">%s</text>'
                                 % (node["x"], node["y"] + node["h"] / 2 + 10, FONT_STACK, svg_esc(str(v))))
    parts.append('</g>')

    # layer: legend
    parts.append('<g id="layer-legend">')
    lx0, ly0 = left - 40, bottom + 10
    lw = W - (left - 40) - right
    parts.append('<rect x="%.0f" y="%.0f" width="%.0f" height="%.0f" fill="#FAFAFA" '
                 'stroke="#CFD8DC" rx="6"/>' % (lx0, ly0, lw, legend_h))
    parts.append('<text x="%.0f" y="%.0f" font-family="%s" font-size="11" '
                 'font-weight="bold" fill="#37474F">图例 Legend</text>' % (lx0 + 8, ly0 + 15, FONT_STACK))
    col_w = lw / ncols
    for i, (kind, key, rlabel, extra) in enumerate(legend_rows):
        cx = lx0 + 8 + (i % ncols) * col_w
        cy = ly0 + 30 + (i // ncols) * 22
        if kind == "relation":
            color, marker, dash, width, _ = RELATION_TYPES[key]
            parts.append('<line x1="%.0f" y1="%.0f" x2="%.0f" y2="%.0f" stroke="%s" '
                         'stroke-width="%.1f" marker-end="%s"/>'
                         % (cx, cy, cx + 30, cy, color, width, _edge_marker_id(marker, color)))
            parts.append('<text x="%.0f" y="%.0f" font-family="%s" font-size="10" '
                         'fill="#263238">%s</text>' % (cx + 38, cy + 3.4, FONT_STACK, svg_esc(rlabel)))
        elif kind == "entity":
            fill, border, dash, shape = ENTITY_STYLE[key]
            mini = ('<rect x="%.0f" y="%.0f" width="22" height="14" rx="3" fill="%s" stroke="%s"/>'
                    if shape in ("rect", "roundrect") else
                    '<ellipse cx="%.0f" cy="%.0f" rx="11" ry="7" fill="%s" stroke="%s"/>')
            x0, y0 = cx, cy - 7
            if shape in ("rect", "roundrect"):
                parts.append(mini % (x0, y0, fill, border))
            else:
                parts.append(mini % (x0 + 11, y0 + 7, fill, border))
            parts.append('<text x="%.0f" y="%.0f" font-family="%s" font-size="10" '
                         'fill="#263238">%s</text>' % (cx + 30, cy + 3.4, FONT_STACK, svg_esc(rlabel)))
        else:
            parts.append('<rect x="%.0f" y="%.0f" width="18" height="10" fill="#ECEFF1" '
                         'stroke="#90A4AE"/>' % (cx, cy - 5))
            parts.append('<text x="%.0f" y="%.0f" font-family="%s" font-size="9.5" '
                         'fill="#37474F">%s</text>' % (cx + 24, cy + 3.4, FONT_STACK, svg_esc(rlabel)))
    parts.append('</g>')

    # layer: title
    parts.append('<g id="layer-title">')
    parts.append('<text x="%.0f" y="30" text-anchor="middle" font-family="%s" '
                 'font-size="16" font-weight="bold" fill="#212121">%s</text>'
                 % (W / 2, FONT_STACK, svg_esc(spec.get("title") or "Mechanism / Pathway")))
    parts.append('</g>')

    parts.append('</svg>')

    meta = {
        "case_id": spec.get("case_id"),
        "title": spec.get("title"),
        "width": W,
        "height": H,
        "layers": ["layer-background", "layer-compartments", "layer-edges",
                   "layer-nodes", "layer-data", "layer-legend", "layer-title"],
        "entity_ids": [e["id"] for e in spec.get("entities") or []],
        "relation_ids": [r["id"] for r in spec.get("relations") or []],
        "relation_types_used": rel_types_used,
        "entity_types_used": ent_types_used,
        "legend_rows": [{"kind": k, "key": key, "label": lbl} for k, key, lbl, _ in legend_rows],
        "warnings": warnings,
        "feedback_relations": [r["id"] for r in spec.get("relations") or []
                               if _classify_feedback(r, rank, feedback_set) in ("feedback", "self")],
    }
    return "\n".join(parts), meta


def build_svg_file(spec_path, out_path, render_meta_path=None):
    spec = load_json(spec_path)
    if spec is None:
        print("ERROR: cannot read spec %s" % spec_path, file=sys.stderr)
        return 1
    try:
        svg_text, meta = build_svg(spec)
    except ValueError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(svg_text)
    if render_meta_path:
        os.makedirs(os.path.dirname(os.path.abspath(render_meta_path)), exist_ok=True)
        with open(render_meta_path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
    return 0


# ---------------------------------------------------------------------------
# result.json skeleton + contract check
# ---------------------------------------------------------------------------

REQUIRED_TOP_KEYS = [
    "case_id", "status", "title", "task_summary",
    "editable_source_files", "export_artifacts",
    "entities", "relations", "legend", "data_mappings",
    "sources", "uncertainty", "pending_human",
    "change_log", "limitations", "errors", "degradations", "runtime",
]


def _skeleton_result(case_id, title=""):
    return {
        "case_id": case_id,
        "status": "partial",
        "title": title,
        "task_summary": "",
        "editable_source_files": [],
        "export_artifacts": [],
        "entities": [],
        "relations": [],
        "legend": [],
        "data_mappings": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "time": utc_now_iso(),
            "action": "skeleton",
            "detail": "落盘结果骨架（partial），后续仅做增量改进",
        }],
        "limitations": ["skeleton not yet filled with extracted entities/relations/artifacts"],
        "errors": [],
        "degradations": [],
        "runtime": {
            "started_at": utc_now_iso(),
            "finished_at": None,
            "duration_sec": None,
            "model_requests_used": None,
            "steps": ["skeleton"],
        },
    }


def init_outputs(case_path, task_path, out_dir, work_dir):
    """Write a valid partial result.json + empty spec.json early. Never fails hard."""
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    case = load_json(case_path) or {}
    case_id = str(case.get("case_id") or case.get("id") or case.get("caseId") or "")
    title = str(case.get("title") or case.get("topic") or "")
    if not case_id:
        base = os.path.basename(case_path or "")
        case_id = re.sub(r"\.json$", "", base)
    spec = {
        "spec_version": SPEC_VERSION,
        "case_id": case_id,
        "title": title,
        "compartments": [dict(c) for c in DEFAULT_COMPARTMENTS],
        "entities": [],
        "relations": [],
        "legend": [],
        "data_mappings": [],
        "uncertainty": [],
        "notes": "",
    }
    with open(os.path.join(work_dir, "spec.json"), "w", encoding="utf-8") as fh:
        json.dump(spec, fh, ensure_ascii=False, indent=2)
    result = _skeleton_result(case_id, title)
    result["runtime"]["steps"] = ["skeleton"]
    with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    return 0


def _magic_ok(path, fmt):
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except Exception:
        return False
    fmt = (fmt or "").lower()
    if fmt in ("pdf",):
        return head.startswith(b"%PDF-")
    if fmt in ("png",):
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if fmt in ("svg",):
        return head.lstrip().startswith(b"<") or head.lstrip().startswith(b"\xef\xbb\xbf<")
    return True


def _parse_svg_layers(path):
    try:
        tree = ET.parse(path)
        root = tree.getroot()
        layers = [g.get("id") for g in root.iter()
                  if g.tag.endswith("}g") and (g.get("id") or "").startswith("layer-")]
        return True, layers
    except Exception as exc:
        return False, [str(exc)]


def check_result(result_path, out_dir):
    """Audit result.json against the contract + files on disk.

    Returns (errors, warnings). errors -> blocking; warnings -> non-blocking.
    """
    errors, warnings = [], []
    result = load_json(result_path)
    if result is None:
        return ["result.json is missing or not valid JSON"], []
    if not isinstance(result, dict):
        return ["result.json must be a JSON object"], []

    for key in REQUIRED_TOP_KEYS:
        if key not in result:
            errors.append("missing top-level key: %s" % key)

    case_id = result.get("case_id")
    if not case_id or not str(case_id).strip():
        errors.append("case_id is empty")
    status = result.get("status")
    if status not in ("success", "partial", "failed"):
        errors.append("status must be success|partial|failed, got %r" % status)

    # editable source files
    esf = result.get("editable_source_files") or []
    if not isinstance(esf, list) or not esf:
        errors.append("editable_source_files must be a non-empty list")
    else:
        for i, item in enumerate(esf):
            if not isinstance(item, dict) or not item.get("path"):
                errors.append("editable_source_files[%d] missing path" % i)
                continue
            full = item["path"] if os.path.isabs(item["path"]) else os.path.join(out_dir, item["path"])
            if not os.path.exists(full):
                errors.append("editable source file does not exist: %s" % item["path"])
            else:
                ok, layers = _parse_svg_layers(full)
                if not ok:
                    errors.append("editable source SVG invalid: %s (%s)" % (item["path"], layers[0] if layers else "parse error"))
                elif len(layers) < 2:
                    warnings.append("SVG has <2 named layers: %s" % item["path"])
            if not item.get("format"):
                warnings.append("editable_source_files[%d] missing format" % i)
            if not item.get("layers"):
                warnings.append("editable_source_files[%d] missing layers list" % i)

    # export artifacts
    eaf = result.get("export_artifacts") or []
    if not isinstance(eaf, list):
        errors.append("export_artifacts must be a list")
    else:
        for i, item in enumerate(eaf):
            if not isinstance(item, dict) or not item.get("path"):
                errors.append("export_artifacts[%d] missing path" % i)
                continue
            full = item["path"] if os.path.isabs(item["path"]) else os.path.join(out_dir, item["path"])
            if not os.path.exists(full):
                errors.append("export artifact does not exist: %s" % item["path"])
            else:
                fmt = (item.get("format") or "").lower()
                if not _magic_ok(full, fmt):
                    errors.append("export artifact %s fails magic check for format %r" % (item["path"], item.get("format")))
    if not eaf:
        warnings.append("no export_artifacts (PNG/PDF); consider status=partial and add limitations")

    # entities
    entities = result.get("entities") or []
    eids = set()
    for i, e in enumerate(entities):
        if not isinstance(e, dict) or not e.get("id"):
            errors.append("entities[%d] missing id" % i)
            continue
        if e["id"] in eids:
            errors.append("duplicate entity id: %s" % e["id"])
        eids.add(e["id"])
        if not e.get("label"):
            warnings.append("entity %s missing label" % e["id"])
        if not e.get("type"):
            warnings.append("entity %s missing type" % e["id"])
        srcs = e.get("sources") or []
        if not srcs:
            warnings.append("entity %s has no sources" % e["id"])
        elif any(not (s or {}).get("quote", "").strip() if isinstance(s, dict) else True for s in srcs):
            warnings.append("entity %s source missing verbatim quote" % e["id"])

    # relations
    relations = result.get("relations") or []
    for i, r in enumerate(relations):
        if not isinstance(r, dict) or not r.get("id"):
            errors.append("relations[%d] missing id" % i)
            continue
        if r.get("source") not in eids:
            errors.append("relation %s source %r not in entities" % (r["id"], r.get("source")))
        if r.get("target") not in eids:
            errors.append("relation %s target %r not in entities" % (r["id"], r.get("target")))
        if not r.get("type"):
            warnings.append("relation %s missing type" % r["id"])
        srcs = r.get("sources") or []
        if not srcs:
            warnings.append("relation %s has no sources" % r["id"])
        elif any(not (s or {}).get("quote", "").strip() if isinstance(s, dict) else True for s in srcs):
            warnings.append("relation %s source missing verbatim quote" % r["id"])

    # legend coverage (self-consistency: legend vs entities/relations actually used)
    legend = result.get("legend") or []
    if not legend:
        warnings.append("legend is empty; figure/legend self-consistency cannot be verified")
    else:
        rel_used = {r["type"] for r in relations if isinstance(r, dict) and r.get("type")}
        ent_used = {e["type"] for e in entities if isinstance(e, dict) and e.get("type")}
        legend_keys = {(l.get("key") or "").lower() for l in legend if isinstance(l, dict)}
        for t in rel_used:
            if t.lower() not in legend_keys:
                warnings.append("legend missing relation type: %s" % t)
        for t in ent_used:
            if t.lower() not in legend_keys:
                warnings.append("legend missing entity type: %s" % t)

    # change_log
    cl = result.get("change_log") or []
    if not cl:
        errors.append("change_log must be non-empty")
    else:
        first = cl[0] if isinstance(cl[0], dict) else {}
        if "skeleton" not in str(first.get("action", "")).lower():
            warnings.append("change_log first entry should be the 3-minute skeleton record")

    # limitations required when not success
    if status != "success":
        lim = result.get("limitations") or []
        if not lim:
            errors.append("limitations required when status != success")

    return errors, warnings


def run_selftest():
    """End-to-end self-test on a clearly-labelled synthetic example (never a formal case)."""
    tmp = tempfile.mkdtemp(prefix="weekly-skill-selftest-")
    spec = {
        "spec_version": SPEC_VERSION,
        "case_id": "selftest-demo",
        "title": "示例：受体—胞内—核内线性通路（自检样例，非正式案例）",
        "compartments": [dict(c) for c in DEFAULT_COMPARTMENTS],
        "entities": [
            {"id": "e1", "label": "Ligand", "type": "small_molecule", "compartment": "extracellular",
             "display": {"italic": False},
             "sources": [{"locator": "demo.md#L1", "quote": "配体与受体结合"}]},
            {"id": "e2", "label": "RTK", "type": "protein", "compartment": "membrane",
             "transmembrane": True,
             "sources": [{"locator": "demo.md#L2", "quote": "跨膜受体酪氨酸激酶"}]},
            {"id": "e3", "label": "KinaseA", "type": "protein", "compartment": "cytosol",
             "sources": [{"locator": "demo.md#L3", "quote": "胞内激酶被磷酸化激活"}]},
            {"id": "e4", "label": "TF", "type": "protein", "compartment": "nucleus",
             "sources": [{"locator": "demo.md#L4", "quote": "转录因子入核"}]},
        ],
        "relations": [
            {"id": "r1", "source": "e1", "target": "e2", "type": "binding",
             "sources": [{"locator": "demo.md#L5", "quote": "配体结合受体"}]},
            {"id": "r2", "source": "e2", "target": "e3", "type": "modification", "modifier": "phosphorylation",
             "sources": [{"locator": "demo.md#L6", "quote": "受体磷酸化下游激酶"}]},
            {"id": "r3", "source": "e3", "target": "e4", "type": "transport",
             "sources": [{"locator": "demo.md#L7", "quote": "激活的转录因子转运入核"}]},
            {"id": "r4", "source": "e4", "target": "e3", "type": "inhibition",
             "sources": [{"locator": "demo.md#L8", "quote": "负反馈抑制"}]},
        ],
        "data_mappings": [
            {"encoding": "node_fill", "channel": "表达量", "scale": "high=#C62828,low=#B0BEC5",
             "values": {"e3": "high", "e1": "low"}, "sources": [{"locator": "demo.md#L9", "quote": "表达量高低"}]},
        ],
        "uncertainty": [{"item": "r4", "kind": "direction", "detail": "反馈方向按上下文推断", "resolution": "inferred"}],
    }
    out_dir = os.path.join(tmp, "output")
    work_dir = os.path.join(tmp, "work")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    with open(os.path.join(work_dir, "spec.json"), "w", encoding="utf-8") as fh:
        json.dump(spec, fh, ensure_ascii=False, indent=2)

    rc = build_svg_file(os.path.join(work_dir, "spec.json"),
                        os.path.join(out_dir, "mechanism.svg"),
                        os.path.join(work_dir, "render_meta.json"))
    if rc != 0:
        print("SELFTEST FAIL: build_svg returned %d" % rc)
        return 1

    # fabricate a result.json consistent with the spec and run the checker
    result = _skeleton_result("selftest-demo", spec["title"])
    result["status"] = "success"
    result["editable_source_files"] = [{"path": "mechanism.svg", "format": "svg",
                                        "layers": ["layer-background", "layer-compartments", "layer-edges",
                                                   "layer-nodes", "layer-data", "layer-legend", "layer-title"],
                                        "tool": "scripts/main.py build"}]
    result["export_artifacts"] = []
    result["entities"] = spec["entities"]
    result["relations"] = spec["relations"]
    result["legend"] = [{"key": "binding", "visual": "line", "meaning": "结合"},
                        {"key": "modification", "visual": "dashed arrow+P", "meaning": "磷酸化修饰"},
                        {"key": "transport", "visual": "open arrow", "meaning": "转运"},
                        {"key": "inhibition", "visual": "T-bar", "meaning": "抑制"},
                        {"key": "small_molecule", "visual": "ellipse", "meaning": "小分子"},
                        {"key": "protein", "visual": "roundrect", "meaning": "蛋白"}]
    result["data_mappings"] = spec["data_mappings"]
    result["sources"] = []
    result["uncertainty"] = spec["uncertainty"]
    result["limitations"] = []
    result["change_log"].append({"time": utc_now_iso(), "action": "build",
                                 "detail": "selftest build+check"})
    with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    errors, warnings = check_result(os.path.join(out_dir, "result.json"), out_dir)
    if errors:
        print("SELFTEST FAIL:")
        for e in errors:
            print("  ERROR:", e)
        return 1
    print("SELFTEST OK: svg rendered, result.json passes contract check")
    for w in warnings:
        print("  warning:", w)
    print("  artifacts under", tmp)
    return 0
