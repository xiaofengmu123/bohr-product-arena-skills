# -*- coding: utf-8 -*-
"""sci-figure-editor core implementation. Python standard library only.

Responsibilities: input discovery, honest skeleton, deterministic layered SVG
rendering, pure-stdlib PDF/PNG export fallback, and contract validation.
No third-party packages are imported.
"""
from __future__ import annotations

import json
import math
import os
import re
import struct
import subprocess
import sys
import zlib
from collections import deque
from datetime import datetime, timezone

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S%z"
MAX_FILE_BYTES = 2 * 1024 * 1024
ALLOWED_STATUS = ("complete", "partial", "failed")
ALLOWED_CONFIDENCE = ("confirmed", "inferred", "missing")
ALLOWED_FONT_CHECK = ("embedded", "none", "replaced")

# Relation types mapped to arrow / T-bar / plain line.
ARROW_RELATIONS = {
    "activation", "upstream", "downstream", "causal", "temporal", "mapping",
    "positive", "phosphorylation", "leads_to", "produces", "promotes",
}
TBAR_RELATIONS = {
    "inhibition", "negative", "repression", "suppression", "blocks",
}
PLAIN_RELATIONS = {
    "containment", "parallel", "binding", "association", "interaction",
    "member", "sibling",
}

DEFAULT_COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Safe I/O helpers
# --------------------------------------------------------------------------- #
def read_text_safe(path: str, max_bytes: int = MAX_FILE_BYTES):
    """Read text with BOM / encoding fallback. Returns (text, warnings)."""
    warnings = []
    if not path or not os.path.exists(path):
        return None, ["missing file: %s" % (path or "<none>")]
    size = os.path.getsize(path)
    if size > max_bytes:
        warnings.append("file %s (%d bytes) exceeds %d-byte limit, skipped" % (path, size, max_bytes))
        return None, warnings
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "cp1252"):
        try:
            return raw.decode(enc), warnings
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), warnings + ["decoded with replacement chars"]


def load_json_safe(path: str):
    text, warnings = read_text_safe(path)
    if text is None:
        return None, warnings
    try:
        return json.loads(text), warnings
    except json.JSONDecodeError as exc:
        return None, warnings + ["invalid JSON: %s" % exc]


def atomic_write_json(path: str, obj) -> None:
    """Write JSON atomically via temp file + rename (idempotent, crash-safe)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def log_change(entry: dict, action: str, detail: str) -> None:
    entry.setdefault("change_log", []).append(
        {"time": now_iso(), "action": action, "detail": detail}
    )


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _str(value, default=""):
    if value is None:
        return default
    return str(value)


# --------------------------------------------------------------------------- #
# Input discovery
# --------------------------------------------------------------------------- #
def discover_input(input_dir: str, case_path=None):
    """Find case.json, task.md and material files. Never assumes fixed names."""
    info = {"input_dir": input_dir, "case": None, "task": None, "materials": []}
    if not os.path.isdir(input_dir):
        return info
    entries = sorted(os.listdir(input_dir))
    for name in entries:
        full = os.path.join(input_dir, name)
        if os.path.isdir(full):
            continue
        low = name.lower()
        if low == "case.json" or (case_path and os.path.abspath(full) == os.path.abspath(case_path)):
            info["case"] = full
        elif low == "task.md" or low == "task.txt":
            info["task"] = full
        elif low.endswith((".pdf", ".csv", ".tsv", ".txt", ".md", ".png", ".jpg", ".jpeg", ".svg")):
            info["materials"].append(full)
    if case_path and os.path.exists(case_path) and info["case"] is None:
        info["case"] = case_path
    return info


# --------------------------------------------------------------------------- #
# Skeleton
# --------------------------------------------------------------------------- #
def build_skeleton(case_path, task_path, input_dir):
    case, case_warn = load_json_safe(case_path) if case_path and os.path.exists(case_path) else (None, ["case.json missing"])
    task_text, task_warn = read_text_safe(task_path) if task_path and os.path.exists(task_path) else (None, ["task.md missing"])

    case_id = "unknown"
    output_contract_keys = []
    if isinstance(case, dict):
        case_id = _str(case.get("case_id") or case.get("id") or "unknown", "unknown")
        oc = case.get("output_contract") or case.get("outputContract")
        if isinstance(oc, dict):
            output_contract_keys = sorted(oc.keys())

    status = "failed" if case is None else "partial"
    result = {
        "case_id": case_id,
        "status": status,
        "editable_source_files": [],
        "export_files": [],
        "provenance": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "runtime": {
            "started_at": now_iso(),
            "finished_at": None,
            "budget_s": 900,
            "budget_requests": 64,
            "input_dir": input_dir,
        },
        "change_log": [],
    }
    if output_contract_keys:
        result["output_contract_keys"] = output_contract_keys

    log_change(result, "skeleton", "initial partial skeleton written; extraction pending")

    if case is None:
        result["errors"].extend(case_warn)
        result["uncertainty"].append({
            "item": "input", "reason": "case.json missing or unreadable",
            "impact": "cannot extract entities or determine output contract",
            "mitigation": "provide /input/case.json",
        })
    else:
        result["sources"].append({
            "ref": "case.json", "path": case_path or "/input/case.json",
            "locator": "case.json", "used_for": "case identity and task instructions",
        })
        for w in case_warn:
            result["degradations"].append(w)
    if task_text is None:
        result["degradations"].extend(task_warn)
        result["uncertainty"].append({
            "item": "task", "reason": "task.md missing or unreadable",
            "impact": "scoring dimensions may be incomplete", "mitigation": "provide /input/task.md",
        })
    else:
        result["sources"].append({
            "ref": "task.md", "path": task_path or "/input/task.md",
            "locator": "task.md", "used_for": "task requirements and scoring dimensions",
        })

    # Echo any output_contract keys with null so unknown keys are preserved, not dropped.
    for key in output_contract_keys:
        result.setdefault(key, None)
    return result


# --------------------------------------------------------------------------- #
# Spec loading / normalization
# --------------------------------------------------------------------------- #
def load_spec(path):
    spec, warnings = load_json_safe(path)
    if spec is None:
        return None, warnings
    if not isinstance(spec, dict):
        return None, warnings + ["spec is not a JSON object"]
    spec.setdefault("figure", {})
    spec.setdefault("entities", [])
    spec.setdefault("relations", [])
    spec.setdefault("legends", [])
    spec.setdefault("annotations", [])
    spec.setdefault("sources", [])
    return spec, warnings


def normalize_confidence(value):
    if value in ALLOWED_CONFIDENCE:
        return value
    if value is True or value == "true":
        return "confirmed"
    return "inferred" if value else "missing"


# --------------------------------------------------------------------------- #
# Deterministic auto-layout
# --------------------------------------------------------------------------- #
def _estimate_box(entity):
    label = _str(entity.get("label"), "?")
    lines = _wrap_text(label, 22)
    max_len = max((len(ln) for ln in lines), default=1)
    w = max(120.0, min(360.0, max_len * 8.5 + 36.0))
    h = max(48.0, len(lines) * 18.0 + 20.0)
    return w, h


def _wrap_text(text, width):
    """Naive deterministic wrap; CJK-safe-ish (split per char when needed)."""
    if not text:
        return [""]
    out = []
    cur = ""
    for ch in str(text):
        cur += ch
        if len(cur) >= width:
            out.append(cur)
            cur = ""
    if cur or not out:
        out.append(cur)
    return out


def auto_layout(spec):
    """Assign x/y/w/h deterministically when the spec omits them.

    Strategy: containment tree renders parents around children; remaining
    flow edges (directed) determine level columns; nodes stack within a level.
    Falls back to a single-column grid on any failure.
    """
    ents = spec.get("entities", [])
    rels = spec.get("relations", [])
    if not ents:
        return spec

    for e in ents:
        if not isinstance(e, dict):
            continue
        e.setdefault("id", "e%d" % ents.index(e))
        e.setdefault("label", "")
        e.setdefault("x", None)
        e.setdefault("y", None)
        e.setdefault("w", None)
        e.setdefault("h", None)
        if e.get("w") is None or e.get("h") is None:
            w, h = _estimate_box(e)
            e["w"] = _num(e.get("w")) or w
            e["h"] = _num(e.get("h")) or h

    ids = {e.get("id"): e for e in ents}
    try:
        children = {}
        parent_of = {}
        for r in rels:
            if not isinstance(r, dict):
                continue
            if r.get("type") == "containment":
                p, c = r.get("from"), r.get("to")
                if p in ids and c in ids and p != c:
                    children.setdefault(p, []).append(c)
                    parent_of[c] = p

        # Avoid cycles in containment.
        seen = set()
        def ordered_roots():
            roots = []
            for e in ents:
                nid = e.get("id")
                if nid not in parent_of and nid not in seen:
                    roots.append(nid)
            return roots

        GAP_X, GAP_Y, MARGIN = 60.0, 40.0, 40.0

        def layout_subset(node_ids):
            """Place a list of node ids in columns by flow level."""
            local = {nid: ids[nid] for nid in node_ids if nid in ids}
            if not local:
                return
            idx = {nid: i for i, nid in enumerate(node_ids)}
            adj = [[] for _ in node_ids]
            indeg = [0] * len(node_ids)
            for r in rels:
                if not isinstance(r, dict):
                    continue
                a, b = r.get("from"), r.get("to")
                t = r.get("type")
                if a in idx and b in idx and a != b and t not in PLAIN_RELATIONS:
                    adj[idx[a]].append(idx[b])
                    indeg[idx[b]] += 1
            q = deque([i for i in range(len(node_ids)) if indeg[i] == 0])
            if not q:
                q = deque(range(len(node_ids)))
            levels = [0] * len(node_ids)
            while q:
                i = q.popleft()
                for j in adj[i]:
                    indeg[j] -= 1
                    levels[j] = max(levels[j], levels[i] + 1)
                    if indeg[j] == 0:
                        q.append(j)
            for i in range(len(node_ids)):
                if indeg[i] > 0:
                    levels[i] = 0
            by_level = {}
            for i, nid in enumerate(node_ids):
                by_level.setdefault(levels[i], []).append(nid)
            col_x = MARGIN
            xpos = []
            for lvl in sorted(by_level):
                col_nodes = by_level[lvl]
                col_w = max((_num(ids[n].get("w")) for n in col_nodes), default=120.0)
                y = MARGIN
                for nid in col_nodes:
                    node = ids[nid]
                    node["x"] = col_x
                    node["y"] = y
                    y += _num(node.get("h")) + GAP_Y
                xpos.append((col_x, col_w))
                col_x += col_w + GAP_X
            return max((_num(ids[n].get("x")) + _num(ids[n].get("w")) for n in node_ids), default=MARGIN) + MARGIN

        # Layout each top-level node; containers recurse into children.
        def layout_container(nid, cx, cy):
            node = ids[nid]
            sub = children.get(nid, [])
            if sub:
                # Recurse first so children get sizes.
                for c in sub:
                    layout_container(c, 0.0, 0.0)
                # Place children in a row inside parent.
                px = GAP_X
                py = 30.0  # title band
                maxh = 0.0
                for c in sub:
                    child = ids[c]
                    child["x"] = px
                    child["y"] = py
                    px += _num(child.get("w")) + GAP_X
                    maxh = max(maxh, _num(child.get("h")))
                node["w"] = px
                node["h"] = maxh + 60.0
            node["x"] = cx
            node["y"] = cy

        roots = ordered_roots()
        # Flow among root nodes.
        cx = MARGIN
        cy = MARGIN
        for root in roots:
            layout_container(root, cx, cy)
            node = ids[root]
            cy += _num(node.get("h")) + GAP_Y

        # Simple flow refinement for roots: place in columns by level.
        flow_nodes = [r for r in roots if r not in children]
        layout_subset(flow_nodes)
    except Exception:
        # Deterministic single-column fallback.
        y = 40.0
        for e in ents:
            if e.get("x") is None or e.get("y") is None:
                e["x"] = 40.0
                e["y"] = y
                y += _num(e.get("h")) + 40.0
    return spec


# --------------------------------------------------------------------------- #
# SVG rendering
# --------------------------------------------------------------------------- #
def xml_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def _hex_to_rgb01(color):
    c = _str(color, "#1f77b4").lstrip("#")
    if len(c) != 6:
        return (0.12, 0.47, 0.71)
    try:
        r, g, b = (int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        return (r, g, b)
    except ValueError:
        return (0.12, 0.47, 0.71)


def _entity_fill(entity, default="#eef3f9"):
    style = entity.get("style") or {}
    return _str(style.get("fill"), default)


def _entity_stroke(entity):
    style = entity.get("style") or {}
    return _str(style.get("stroke"), "#1f77b4")


def build_svg_string(spec):
    fig = spec.get("figure", {})
    canvas = fig.get("canvas", {})
    width = _num(canvas.get("width_px"), 2400) or 2400
    height = _num(canvas.get("height_px"), 1400) or 1400
    font_family = _str(canvas.get("font_family"), "Arial, Helvetica, sans-serif")
    base_font = _num(canvas.get("font_size_px"), 16) or 16

    ents = [e for e in spec.get("entities", []) if isinstance(e, dict)]
    rels = [r for r in spec.get("relations", []) if isinstance(r, dict)]
    legends = [l for l in spec.get("legends", []) if isinstance(l, dict)]
    annotations = [a for a in spec.get("annotations", []) if isinstance(a, dict)]

    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append(
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
        'width="%d" height="%d" viewBox="0 0 %d %d">' % (width, height, width, height)
    )
    parts.append(
        '<title>%s</title>' % xml_escape(_str(fig.get("title"), "figure"))
    )
    # defs: arrow and T-bar markers.
    parts.append("<defs>")
    parts.append(
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" fill="%s"/></marker>' % _str(canvas.get("arrow_color"), "#444444")
    )
    parts.append(
        '<marker id="tbar" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto">'
        '<path d="M1,0 L1,10" stroke="%s" stroke-width="2.5"/></marker>' % _str(canvas.get("tbar_color"), "#444444")
    )
    parts.append("</defs>")

    parts.append('<g id="layer-background" inkscape:groupmode="layer" inkscape:label="background">')
    parts.append('<rect x="0" y="0" width="%d" height="%d" fill="white"/>' % (width, height))
    parts.append("</g>")

    parts.append('<g id="layer-relations" inkscape:groupmode="layer" inkscape:label="relations">')
    for r in rels:
        parts.append(_relation_svg(r, ents))
    parts.append("</g>")

    parts.append('<g id="layer-entities" inkscape:groupmode="layer" inkscape:label="entities">')
    for e in ents:
        parts.append(_entity_svg(e))
    parts.append("</g>")

    parts.append('<g id="layer-labels" inkscape:groupmode="layer" inkscape:label="labels">')
    for e in ents:
        parts.append(_entity_text_svg(e, font_family, base_font))
    parts.append("</g>")

    parts.append('<g id="layer-legend" inkscape:groupmode="layer" inkscape:label="legend">')
    legend_x_default = max(60.0, width - 300.0)
    for i, leg in enumerate(legends):
        parts.append(_legend_svg(leg, i, font_family, base_font, legend_x_default))
    parts.append("</g>")

    parts.append('<g id="layer-annotations" inkscape:groupmode="layer" inkscape:label="annotations">')
    for a in annotations:
        parts.append(_annotation_svg(a, font_family, base_font))
    parts.append("</g>")

    parts.append("</svg>")
    return "\n".join(parts)


def _entity_geometry(e):
    return (_num(e.get("x")), _num(e.get("y")), _num(e.get("w"), 140), _num(e.get("h"), 56))


def _entity_svg(e):
    x, y, w, h = _entity_geometry(e)
    shape = _str(e.get("shape"), "rect")
    fill = _entity_fill(e)
    stroke = _entity_stroke(e)
    eid = xml_escape(_str(e.get("id"), "e"))
    is_container = shape in ("group", "container") or e.get("kind") in ("group", "container")
    stroke_style = "stroke-dasharray=\"6,4\"" if is_container else ""
    if shape in ("ellipse", "circle", "oval"):
        cx, cy, rx, ry = x + w / 2.0, y + h / 2.0, w / 2.0, h / 2.0
        return ('<ellipse id="%s" cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" fill="%s" '
                'stroke="%s" stroke-width="2" %s/>' % (eid, cx, cy, rx, ry, fill, stroke, stroke_style))
    if shape in ("text", "annotation", "note", "none"):
        return '<!-- entity %s rendered as text-only -->' % eid
    rx = 12 if shape in ("roundrect", "rounded") else 0
    return ('<rect id="%s" x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="%d" fill="%s" '
            'stroke="%s" stroke-width="2" %s/>' % (eid, x, y, w, h, rx, fill, stroke, stroke_style))


def _entity_text_svg(e, font_family, base_font):
    x, y, w, h = _entity_geometry(e)
    if _str(e.get("shape")) in ("text", "annotation", "note"):
        # text-only: anchor at x,y top-left
        return ('<text id="%s-label" x="%.1f" y="%.1f" font-family="%s" font-size="%d" '
                'fill="#111111" text-anchor="start">%s</text>'
                % (xml_escape(_str(e.get("id"), "e")), x, y + base_font, font_family, base_font,
                   xml_escape(_str(e.get("label")))))
    lines = _wrap_text(_str(e.get("label")), 22)
    line_h = base_font + 4
    total = len(lines) * line_h
    start_y = y + h / 2.0 - total / 2.0 + base_font * 0.6
    out = []
    for i, ln in enumerate(lines):
        out.append(
            '<text id="%s-label-%d" x="%.1f" y="%.1f" font-family="%s" font-size="%d" '
            'fill="#111111" text-anchor="middle">%s</text>'
            % (xml_escape(_str(e.get("id"), "e")), i, x + w / 2.0, start_y + i * line_h,
               font_family, base_font, xml_escape(ln))
        )
    return "\n".join(out)


def _relation_svg(r, ents):
    by_id = {e.get("id"): e for e in ents}
    a, b = r.get("from"), r.get("to")
    if a not in by_id or b not in by_id:
        return '<!-- relation %s skipped: missing endpoint -->' % xml_escape(_str(r.get("id"), "r"))
    ea, eb = by_id[a], by_id[b]
    x1 = _num(ea.get("x")) + _num(ea.get("w"), 140) / 2.0
    y1 = _num(ea.get("y")) + _num(ea.get("h"), 56) / 2.0
    x2 = _num(eb.get("x")) + _num(eb.get("w"), 140) / 2.0
    y2 = _num(eb.get("y")) + _num(eb.get("h"), 56) / 2.0
    t = _str(r.get("type"), "association")
    rid = xml_escape(_str(r.get("id"), "r"))
    color = _str((r.get("style") or {}).get("stroke"), "#444444")
    width = _num((r.get("style") or {}).get("stroke_width"), 2)
    dash = "stroke-dasharray=\"6,4\"" if t in PLAIN_RELATIONS and t != "parallel" else ""
    marker = ""
    if t in ARROW_RELATIONS:
        marker = 'marker-end="url(#arrow)"'
    elif t in TBAR_RELATIONS:
        marker = 'marker-end="url(#tbar)"'
    line = ('<line id="%s" x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
            'stroke-width="%d" %s %s/>' % (rid, x1, y1, x2, y2, color, width, marker, dash))
    label = _str(r.get("label"))
    if label:
        line += ('<text id="%s-label" x="%.1f" y="%.1f" font-size="12" fill="%s" '
                 'text-anchor="middle">%s</text>'
                 % (rid, (x1 + x2) / 2.0, (y1 + y2) / 2.0 - 6, color, xml_escape(label)))
    return line


def _legend_svg(leg, index, font_family, base_font, default_x=60.0):
    color = _str(leg.get("color"), DEFAULT_COLORS[index % len(DEFAULT_COLORS)])
    label = _str(leg.get("label"), "legend-%d" % index)
    x = _num(leg.get("x"), default_x)
    y = _num(leg.get("y"), 60 + index * (base_font + 14))
    swatch = '<rect x="%.1f" y="%.1f" width="16" height="16" fill="%s" stroke="#333333" stroke-width="1"/>' % (x, y, color)
    text = ('<text x="%.1f" y="%.1f" font-family="%s" font-size="%d" fill="#111111">%s</text>'
            % (x + 24, y + base_font * 0.85, font_family, base_font, xml_escape(label)))
    return swatch + "\n" + text


def _annotation_svg(a, font_family, base_font):
    x = _num(a.get("x"), 60)
    y = _num(a.get("y"), 60)
    text = _str(a.get("label") or a.get("text"), "")
    return ('<text id="%s" x="%.1f" y="%.1f" font-family="%s" font-size="%d" fill="#555555">%s</text>'
            % (xml_escape(_str(a.get("id"), "ann")), x, y, font_family, base_font, xml_escape(text)))


def render_spec_to_svg(spec, out_path):
    auto_layout(spec)
    svg = build_svg_string(spec)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(svg)
    return out_path


# --------------------------------------------------------------------------- #
# Pure-stdlib vector PDF fallback (base-14 Helvetica, no embedding)
# --------------------------------------------------------------------------- #
def _pdf_escape_text(s):
    """Encode to WinAnsi where possible; substitute otherwise."""
    out = []
    for ch in str(s):
        cp = ord(ch)
        if cp < 256:
            if ch in "()\\":
                out.append("\\" + ch)
            else:
                out.append(ch)
        else:
            out.append("?")
    return "".join(out)


def _pdf_text_replacements(s):
    return any(ord(ch) >= 256 for ch in str(s))


def _ellipse_ops(cx, cy, rx, ry):
    k = 0.5523
    return (
        "%.2f %.2f m " % (cx - rx, cy)
        + "%.2f %.2f %.2f %.2f %.2f %.2f c " % (cx - rx, cy - k * ry, cx - k * rx, cy - ry, cx, cy - ry)
        + "%.2f %.2f %.2f %.2f %.2f %.2f c " % (cx + k * rx, cy - ry, cx + rx, cy - k * ry, cx + rx, cy)
        + "%.2f %.2f %.2f %.2f %.2f %.2f c " % (cx + rx, cy + k * ry, cx + k * rx, cy + ry, cx, cy + ry)
        + "%.2f %.2f %.2f %.2f %.2f %.2f c " % (cx - k * rx, cy + ry, cx - rx, cy + k * ry, cx - rx, cy)
        + "h\n"
    )


def _arrowhead(cx, cy, angle, size=6.0, color=(0.27, 0.27, 0.27)):
    r, g, b = color
    p1 = (cx - size * math.cos(angle - 0.45), cy - size * math.sin(angle - 0.45))
    p2 = (cx - size * math.cos(angle + 0.45), cy - size * math.sin(angle + 0.45))
    return "%.3f %.3f %.3f rg\n%.2f %.2f m %.2f %.2f l %.2f %.2f l h f\n" % (
        r, g, b, cx, cy, p1[0], p1[1], p2[0], p2[1])


def write_vector_pdf(spec, out_path, dpi=300.0):
    """Write a valid minimal PDF 1.4 with base-14 Helvetica (no embedding)."""
    fig = spec.get("figure", {})
    canvas = fig.get("canvas", {})
    w_px = _num(canvas.get("width_px"), 2400) or 2400
    h_px = _num(canvas.get("height_px"), 1400) or 1400
    dpi = _num(canvas.get("dpi"), dpi) or dpi
    scale = 72.0 / dpi
    W = w_px * scale
    H = h_px * scale

    ents = [e for e in spec.get("entities", []) if isinstance(e, dict)]
    rels = [r for r in spec.get("relations", []) if isinstance(r, dict)]
    legends = [l for l in spec.get("legends", []) if isinstance(l, dict)]

    content = []
    content.append("1 1 1 rg 0 0 %.2f %.2f re f\n" % (W, H))  # white background

    by_id = {e.get("id"): e for e in ents}

    # Relations first (under entities).
    for r in rels:
        a, b = r.get("from"), r.get("to")
        if a not in by_id or b not in by_id:
            continue
        ea, eb = by_id[a], by_id[b]
        x1 = (_num(ea.get("x")) + _num(ea.get("w"), 140) / 2.0) * scale
        y1 = (h_px - (_num(ea.get("y")) + _num(ea.get("h"), 56) / 2.0)) * scale
        x2 = (_num(eb.get("x")) + _num(eb.get("w"), 140) / 2.0) * scale
        y2 = (h_px - (_num(eb.get("y")) + _num(eb.get("h"), 56) / 2.0)) * scale
        t = _str(r.get("type"), "association")
        color = _hex_to_rgb01((r.get("style") or {}).get("stroke", "#444444"))
        r_, g_, b_ = color
        width = _num((r.get("style") or {}).get("stroke_width"), 2) * scale
        dash = " [3 2] 0 d" if t in PLAIN_RELATIONS and t != "parallel" else ""
        content.append("%.3f %.3f %.3f RG %.2f w%s\n%.2f %.2f m %.2f %.2f l S\n" % (
            r_, g_, b_, width, dash, x1, y1, x2, y2))
        if t in ARROW_RELATIONS:
            angle = math.atan2(y2 - y1, x2 - x1)
            content.append(_arrowhead(x2, y2, angle, 7.0, color))
        elif t in TBAR_RELATIONS:
            angle = math.atan2(y2 - y1, x2 - x1)
            # T-bar: short perpendicular stroke near target.
            perp = angle + math.pi / 2.0
            half = 7.0
            tx1 = x2 - half * math.cos(perp)
            ty1 = y2 - half * math.sin(perp)
            tx2 = x2 + half * math.cos(perp)
            ty2 = y2 + half * math.sin(perp)
            content.append("%.3f %.3f %.3f RG %.2f w\n%.2f %.2f m %.2f %.2f l S\n" % (
                r_, g_, b_, width, tx1, ty1, tx2, ty2))

    # Entities.
    for e in ents:
        x = _num(e.get("x")) * scale
        y = (h_px - _num(e.get("y")) - _num(e.get("h"), 56)) * scale
        w = _num(e.get("w"), 140) * scale
        h = _num(e.get("h"), 56) * scale
        fill = _hex_to_rgb01(_entity_fill(e))
        stroke = _hex_to_rgb01(_entity_stroke(e))
        fr, fg, fb = fill
        sr, sg, sb = stroke
        shape = _str(e.get("shape"), "rect")
        content.append("%.3f %.3f %.3f rg %.3f %.3f %.3f RG %.2f w\n" % (fr, fg, fb, sr, sg, sb, 1.0))
        if shape in ("ellipse", "circle", "oval"):
            content.append(_ellipse_ops(x + w / 2.0, y + h / 2.0, w / 2.0, h / 2.0))
            content.append("B\n")
        elif shape not in ("text", "annotation", "note", "none"):
            content.append("%.2f %.2f %.2f %.2f re B\n" % (x, y, w, h))

    # Text (Helvetica, base-14).
    content.append("BT /F1 %.1f Tf\n" % (_num(canvas.get("font_size_px"), 16) * scale))
    content.append("0 0 0 rg\n")
    replaced = False
    for e in ents:
        label = _str(e.get("label"))
        if not label:
            continue
        x = (_num(e.get("x")) + _num(e.get("w"), 140) / 2.0) * scale
        y = (h_px - _num(e.get("y")) - _num(e.get("h"), 56) / 2.0) * scale - 2
        txt = _pdf_escape_text(label)
        if _pdf_text_replacements(label):
            replaced = True
        # crude centering: shift left by half approximate width.
        x -= len(label) * _num(canvas.get("font_size_px"), 16) * scale * 0.28
        content.append("1 0 0 1 %.2f %.2f Tm (%s) Tj\n" % (x, y, txt))
    content.append("ET\n")

    stream = "".join(content).encode("latin-1", errors="replace")
    objects = {
        1: b"<</Type/Catalog/Pages 2 0 R>>",
        2: b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        3: (("<</Type/Page/Parent 2 0 R/MediaBox[0 0 %.2f %.2f]/Resources<</Font<</F1 5 0 R>>>>/Contents 4 0 R>>" % (W, H)).encode("latin-1")),
        4: (("<</Length %d>>\nstream\n" % len(stream)).encode("latin-1") + stream + b"\nendstream"),
        5: b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>",
    }
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += ("%d 0 obj\n" % num).encode("latin-1")
        out += objects[num]
        out += b"\nendobj\n"
    xref_pos = len(out)
    count = len(objects) + 1
    out += ("xref\n0 %d\n" % count).encode("latin-1")
    out += b"0000000000 65535 f \n"
    for num in sorted(objects):
        out += ("%010d 00000 n \n" % offsets[num]).encode("latin-1")
    out += ("trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (count, xref_pos)).encode("latin-1")
    with open(out_path, "wb") as f:
        f.write(bytes(out))
    return {"path": out_path, "replaced_non_ascii": replaced}


# --------------------------------------------------------------------------- #
# Pure-stdlib PNG preview fallback (vector shapes only, no text raster)
# --------------------------------------------------------------------------- #
def _png_chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def write_png_preview(spec, out_path, max_dim=1600):
    """Rasterize shapes only (rects/ellipses/lines) to a white PNG. No text."""
    fig = spec.get("figure", {})
    canvas = fig.get("canvas", {})
    w_px = int(_num(canvas.get("width_px"), 2400) or 2400)
    h_px = int(_num(canvas.get("height_px"), 1400) or 1400)
    s = max_dim / max(w_px, h_px)
    W = max(1, int(w_px * s))
    H = max(1, int(h_px * s))

    rows = []
    for y in range(H):
        rows.append(bytearray(b"\xff" * (W * 3)))  # white

    def set_px(x, y, rgb):
        if 0 <= x < W and 0 <= y < H:
            idx = y * W * 3 + x * 3
            rows[y][idx:idx + 3] = bytes(rgb)

    def fill_rect(x0, y0, x1, y1, rgb):
        x0, x1 = sorted((max(0, x0), min(W - 1, x1)))
        y0, y1 = sorted((max(0, y0), min(H - 1, y1)))
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                set_px(x, y, rgb)

    def stroke_rect(x0, y0, x1, y1, rgb, t=1):
        for i in range(t):
            fill_rect(x0 + i, y0 + i, x1 - i, y0 + i, rgb)
            fill_rect(x0 + i, y1 - i, x1 - i, y1 - i, rgb)
            fill_rect(x0 + i, y0 + i, x0 + i, y1 - i, rgb)
            fill_rect(x1 - i, y0 + i, x1 - i, y1 - i, rgb)

    def fill_ellipse(cx, cy, rx, ry, rgb):
        for y in range(int(cy - ry), int(cy + ry) + 1):
            for x in range(int(cx - rx), int(cx + rx) + 1):
                if rx > 0 and ry > 0 and ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1.0:
                    set_px(x, y, rgb)

    def line(x0, y0, x1, y1, rgb, t=1):
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            for i in range(t):
                set_px(x0 + i, y0, rgb)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    ents = [e for e in spec.get("entities", []) if isinstance(e, dict)]
    by_id = {e.get("id"): e for e in ents}

    for r in spec.get("relations", []):
        a, b = r.get("from"), r.get("to")
        if a in by_id and b in by_id:
            ea, eb = by_id[a], by_id[b]
            x1 = int((_num(ea.get("x")) + _num(ea.get("w"), 140) / 2.0) * s)
            y1 = int((_num(ea.get("y")) + _num(ea.get("h"), 56) / 2.0) * s)
            x2 = int((_num(eb.get("x")) + _num(eb.get("w"), 140) / 2.0) * s)
            y2 = int((_num(eb.get("y")) + _num(eb.get("h"), 56) / 2.0) * s)
            line(x1, y1, x2, y2, (68, 68, 68), t=max(1, int(2 * s)))

    for e in ents:
        x = int(_num(e.get("x")) * s)
        y = int(_num(e.get("y")) * s)
        w = int(_num(e.get("w"), 140) * s)
        h = int(_num(e.get("h"), 56) * s)
        shape = _str(e.get("shape"), "rect")
        fill = tuple(int(c * 255) for c in _hex_to_rgb01(_entity_fill(e)))
        stroke = tuple(int(c * 255) for c in _hex_to_rgb01(_entity_stroke(e)))
        if shape in ("ellipse", "circle", "oval"):
            fill_ellipse(x + w // 2, y + h // 2, w // 2, h // 2, fill)
        elif shape not in ("text", "annotation", "note", "none"):
            fill_rect(x, y, x + w, y + h, fill)
            stroke_rect(x, y, x + w, y + h, stroke, t=max(1, int(2 * s)))

    raw = b""
    for y in range(H):
        raw += b"\x00" + bytes(rows[y])
    ihdr = struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n"
    png += _png_chunk(b"IHDR", ihdr)
    png += _png_chunk(b"IDAT", zlib.compress(raw, 6))
    png += _png_chunk(b"IEND", b"")
    with open(out_path, "wb") as f:
        f.write(png)
    return out_path


# --------------------------------------------------------------------------- #
# Export orchestration (external tools preferred, stdlib fallback guaranteed)
# --------------------------------------------------------------------------- #
def _find_tool(names):
    for name in names:
        path = shutil_which(name)
        if path:
            return path
    return None


def shutil_which(name):
    try:
        import shutil
        return shutil.which(name)
    except Exception:
        return None


def _cairosvg_available():
    try:
        r = subprocess.run([sys.executable, "-c", "import cairosvg"], capture_output=True)
        return r.returncode == 0
    except Exception:
        return False


def _run_cairosvg(svg_path, out_path, fmt):
    try:
        return subprocess.run(
            [sys.executable, "-m", "cairosvg", svg_path, "-o", out_path, "-f", fmt],
            capture_output=True).returncode
    except Exception:
        return 1


def export_figures(spec, svg_path, out_dir, dpi=300.0):
    """Try external converters; always guarantee a stdlib vector PDF fallback."""
    exports = []
    degradations = []
    canvas = spec.get("figure", {}).get("canvas", {})
    w_px = int(_num(canvas.get("width_px"), 2400) or 2400)
    h_px = int(_num(canvas.get("height_px"), 1400) or 1400)
    dpi = _num(canvas.get("dpi"), dpi) or dpi

    # --- PDF ---
    pdf_path = os.path.join(out_dir, "figure01.pdf")
    pdf_note = ""
    pdf_font = "none"
    pdf_written = False
    if _cairosvg_available() and _run_cairosvg(svg_path, pdf_path, "pdf") == 0 and os.path.getsize(pdf_path) > 0:
        pdf_written = True
        pdf_font = "embedded"
        pdf_note = "exported via cairosvg (Python module)"
    converter = _find_tool(["rsvg-convert", "inkscape", "convert", "magick", "chrome", "chromium", "google-chrome"])
    if not pdf_written and converter:
        rc = _run_converter_pdf(converter, svg_path, pdf_path)
        if rc == 0 and os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
            pdf_written = True
            pdf_font = "embedded"
            pdf_note = "exported via %s" % os.path.basename(converter)
        else:
            degradations.append("converter %s failed for PDF; using stdlib fallback" % converter)
    if not pdf_written:
        info = write_vector_pdf(spec, pdf_path, dpi)
        pdf_written = True
        pdf_note = "stdlib vector PDF fallback (Base-14 Helvetica, no embedding)"
        pdf_font = "replaced" if info.get("replaced_non_ascii") else "none"
        if info.get("replaced_non_ascii"):
            degradations.append("non-Latin labels substituted in stdlib PDF fallback")
    exports.append({
        "path": os.path.basename(pdf_path), "format": "pdf",
        "width_px": w_px, "height_px": h_px, "dpi": dpi,
        "color_space": "RGB", "font_check": pdf_font, "note": pdf_note,
    })

    # --- PNG ---
    png_path = os.path.join(out_dir, "figure01.png")
    png_written = False
    if _cairosvg_available() and _run_cairosvg(svg_path, png_path, "png") == 0 and os.path.getsize(png_path) > 0:
        png_written = True
        exports.append({
            "path": os.path.basename(png_path), "format": "png",
            "width_px": w_px, "height_px": h_px, "dpi": dpi,
            "color_space": "RGB", "font_check": "none",
            "note": "exported via cairosvg (Python module)",
        })
    converter2 = _find_tool(["rsvg-convert", "inkscape", "convert", "magick", "chrome", "chromium"])
    if not png_written and converter2:
        rc = _run_converter_png(converter2, svg_path, png_path)
        if rc == 0 and os.path.exists(png_path) and os.path.getsize(png_path) > 0:
            png_written = True
            exports.append({
                "path": os.path.basename(png_path), "format": "png",
                "width_px": w_px, "height_px": h_px, "dpi": dpi,
                "color_space": "RGB", "font_check": "none",
                "note": "exported via %s" % os.path.basename(converter2),
            })
    if not png_written:
        try:
            write_png_preview(spec, png_path)
            exports.append({
                "path": os.path.basename(png_path), "format": "png",
                "width_px": int(_num(canvas.get("width_px"), 2400) * 1600 / max(w_px, h_px) if w_px and h_px else 0),
                "height_px": int(_num(canvas.get("height_px"), 1400) * 1600 / max(w_px, h_px) if w_px and h_px else 0),
                "dpi": dpi, "color_space": "RGB", "font_check": "none",
                "note": "stdlib fallback preview: shapes only, text omitted",
            })
            degradations.append("no PNG converter found; stdlib preview omits text")
        except Exception as exc:
            exports.append({
                "path": "figure01.png", "format": "png", "width_px": w_px, "height_px": h_px,
                "dpi": dpi, "color_space": "RGB", "font_check": "none",
                "note": "failed: %s" % exc,
            })
    return exports, degradations


def _run_converter_pdf(tool, svg_path, pdf_path):
    base = os.path.basename(tool).lower()
    try:
        if "rsvg" in base:
            return subprocess.run([tool, "-f", "pdf", "-o", pdf_path, svg_path], capture_output=True).returncode
        if "inkscape" in base:
            return subprocess.run([tool, "--export-filename=%s" % pdf_path, svg_path], capture_output=True).returncode
        if "cairosvg" in base:
            return subprocess.run([sys.executable, "-m", "cairosvg", svg_path, "-o", pdf_path], capture_output=True).returncode
        if "magick" in base or "convert" in base:
            return subprocess.run([tool, "-density", "300", svg_path, pdf_path], capture_output=True).returncode
        if "chrome" in base or "chromium" in base:
            return subprocess.run([tool, "--headless", "--disable-gpu", "--print-to-pdf=%s" % pdf_path, svg_path], capture_output=True).returncode
    except Exception:
        pass
    return 1


def _run_converter_png(tool, svg_path, png_path):
    base = os.path.basename(tool).lower()
    try:
        if "rsvg" in base:
            return subprocess.run([tool, "-w", "2400", "-o", png_path, svg_path], capture_output=True).returncode
        if "inkscape" in base:
            return subprocess.run([tool, "--export-filename=%s" % png_path, svg_path], capture_output=True).returncode
        if "cairosvg" in base:
            return subprocess.run([sys.executable, "-m", "cairosvg", svg_path, "-o", png_path], capture_output=True).returncode
        if "magick" in base or "convert" in base:
            return subprocess.run([tool, "-density", "300", svg_path, png_path], capture_output=True).returncode
        if "chrome" in base or "chromium" in base:
            return subprocess.run([tool, "--headless", "--disable-gpu", "--screenshot=%s" % png_path, svg_path], capture_output=True).returncode
    except Exception:
        pass
    return 1


# --------------------------------------------------------------------------- #
# Evidence merge
# --------------------------------------------------------------------------- #
def build_provenance(spec):
    prov = []
    ents = [e for e in spec.get("entities", []) if isinstance(e, dict)]
    rels = [r for r in spec.get("relations", []) if isinstance(r, dict)]
    legends = [l for l in spec.get("legends", []) if isinstance(l, dict)]
    for e in ents:
        prov.append({
            "element": _str(e.get("id"), "e"),
            "source_ref": _str(e.get("source_ref"), ""),
            "quote": _str(e.get("quote"), ""),
            "mapping": _str(e.get("mapping"), "entity: %s" % _str(e.get("label"), "")),
            "version": _str(e.get("version"), "1"),
            "confidence": normalize_confidence(e.get("confidence")),
        })
    for r in rels:
        prov.append({
            "element": _str(r.get("id"), "r"),
            "source_ref": _str(r.get("source_ref"), ""),
            "quote": _str(r.get("quote"), ""),
            "mapping": _str(r.get("mapping"), "%s --%s--> %s" % (r.get("from"), r.get("type"), r.get("to"))),
            "version": _str(r.get("version"), "1"),
            "confidence": normalize_confidence(r.get("confidence")),
        })
    for l in legends:
        prov.append({
            "element": _str(l.get("id"), "l"),
            "source_ref": _str(l.get("source_ref"), ""),
            "quote": _str(l.get("quote"), ""),
            "mapping": _str(l.get("mapping"), "legend: %s -> %s" % (l.get("label"), l.get("color"))),
            "version": _str(l.get("version"), "1"),
            "confidence": normalize_confidence(l.get("confidence")),
        })
    return prov


def build_sources(spec, input_dir):
    sources = []
    seen = set()
    for s in spec.get("sources", []):
        if isinstance(s, dict) and s.get("path"):
            key = (s.get("path"), s.get("locator"))
            if key not in seen:
                seen.add(key)
                sources.append({
                    "ref": _str(s.get("ref"), os.path.basename(_str(s.get("path")))),
                    "path": _str(s.get("path")),
                    "locator": _str(s.get("locator"), ""),
                    "used_for": _str(s.get("used_for"), ""),
                })
    return sources


def build_uncertainty_from_spec(spec):
    uncertainty = []
    for e in spec.get("entities", []):
        if isinstance(e, dict) and normalize_confidence(e.get("confidence")) != "confirmed":
            uncertainty.append({
                "item": "entity:%s" % _str(e.get("id"), "e"),
                "reason": "confidence=%s; quote=%s" % (normalize_confidence(e.get("confidence")), "missing" if not e.get("quote") else "present"),
                "impact": "element may not match source",
                "mitigation": "verify against source material or mark as placeholder",
            })
    for r in spec.get("relations", []):
        if isinstance(r, dict) and normalize_confidence(r.get("confidence")) != "confirmed":
            uncertainty.append({
                "item": "relation:%s" % _str(r.get("id"), "r"),
                "reason": "confidence=%s" % normalize_confidence(r.get("confidence")),
                "impact": "relation direction/type may be wrong",
                "mitigation": "confirm direction and type in source",
            })
    return uncertainty


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def validate_result(result, output_dir):
    issues = []
    if not isinstance(result, dict):
        return ["result is not a JSON object"]
    for key in ("case_id", "status", "editable_source_files", "export_files",
                "provenance", "sources", "uncertainty", "change_log"):
        if key not in result:
            issues.append("missing field: %s" % key)
    if result.get("status") not in ALLOWED_STATUS:
        issues.append("invalid status: %r" % result.get("status"))
    for key in ("editable_source_files", "export_files", "provenance", "sources",
                "uncertainty", "change_log"):
        if key in result and not isinstance(result[key], list):
            issues.append("field %s must be a list" % key)

    # Provenance: every entry must have locator + verbatim quote.
    for i, p in enumerate(result.get("provenance", [])):
        if not isinstance(p, dict):
            issues.append("provenance[%d] is not an object" % i)
            continue
        if not _str(p.get("source_ref")):
            issues.append("provenance[%d].source_ref is empty" % i)
        if not _str(p.get("quote")):
            issues.append("provenance[%d].quote is empty (verbatim evidence required)" % i)

    # Editable files must exist and SVG must parse.
    for f in result.get("editable_source_files", []):
        path = os.path.join(output_dir, _str(f.get("path")))
        if not os.path.exists(path):
            issues.append("editable_source_files entry missing on disk: %s" % f.get("path"))
        elif _str(f.get("path")).lower().endswith(".svg"):
            issues.extend(validate_svg(path))

    return issues


def validate_svg(path):
    issues = []
    try:
        import xml.etree.ElementTree as ET
        tree = ET.parse(path)
        root = tree.getroot()
        if root.tag.split("}")[-1] != "svg":
            issues.append("SVG root is not <svg>: %s" % root.tag)
        # count layers and text elements
        namespaces = {"svg": "http://www.w3.org/2000/svg"}
        groups = root.findall(".//svg:g", namespaces)
        texts = root.findall(".//svg:text", namespaces)
        if len(groups) < 3:
            issues.append("SVG has fewer than 3 <g> layers (%d found)" % len(groups))
        if len(texts) < 1:
            issues.append("SVG has no editable <text> elements")
    except Exception as exc:
        issues.append("SVG parse failed: %s" % exc)
    return issues


def validate_pdf(path):
    try:
        with open(path, "rb") as f:
            head = f.read(5)
        if not head.startswith(b"%PDF-"):
            return ["PDF header invalid"]
        return []
    except Exception as exc:
        return ["PDF check failed: %s" % exc]
