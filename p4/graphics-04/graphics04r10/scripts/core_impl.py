#!/usr/bin/env python3
"""Pure helpers for figure-model-skill: contract, IR, layout, SVG, validation, result.

Standard library only. Deterministic by construction: no randomness, no wall-clock
dependence in rendering, so re-running the same IR yields byte-identical SVG.
"""
import html as _html
import json
import math
import os
import re
import xml.etree.ElementTree as _ET
from datetime import datetime, timezone

DEFAULT_CONTRACT = [
    "entities",
    "relations",
    "data_encoding",
    "legend_annotations",
    "editable_sources",
    "exports",
]

# Fields that are always represented as JSON lists ([] when empty).
COLLECTION_FIELDS = set(DEFAULT_CONTRACT) | {
    "sources",
    "uncertainty",
    "pending_human",
    "change_log",
    "limitations",
    "checks",
    "artifacts",
}

SVG_NS = "{http://www.w3.org/2000/svg}"
MAX_TEXT_BYTES = 2 * 1024 * 1024

# Allowed direction vocabulary; anything else is flagged as a warning, not rewritten.
DIRECTIONS = {
    "", None, "forward", "backward", "bidirectional", "none",
    "up", "down", "left", "right", "recycle",
}

# Backward / recycle flows are rendered dashed in a distinct color, never silently reversed.
RECYCLE_DIRECTIONS = {"recycle", "backward"}


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def esc(value):
    """Escape a value for safe use in XML attribute/text content."""
    return _html.escape("" if value is None else str(value), quote=True)


def read_text(path, max_bytes=MAX_TEXT_BYTES):
    """Read text with BOM/encoding tolerance. Returns (text, truncated)."""
    with open(path, "rb") as fh:
        data = fh.read(max_bytes + 1)
    truncated = len(data) > max_bytes
    data = data[:max_bytes]
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1"):
        try:
            return data.decode(enc), truncated
        except (UnicodeDecodeError, ValueError):
            continue
    return data.decode("utf-8", errors="replace"), truncated


def load_json(path):
    """Load JSON with encoding tolerance. Raises on unreadable/oversized/malformed file."""
    text, truncated = read_text(path)
    if truncated:
        raise ValueError("file exceeds %d bytes: %s" % (MAX_TEXT_BYTES, path))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(text.lstrip())


def normalize_contract(case_obj):
    """Return declared output_contract keys, or the six-element default if absent/empty."""
    oc = case_obj.get("output_contract") if isinstance(case_obj, dict) else None
    keys = []
    if isinstance(oc, dict):
        keys = [str(k) for k in oc.keys()]
    elif isinstance(oc, list):
        keys = [str(k) for k in oc if k]
    return keys if keys else list(DEFAULT_CONTRACT)


def init_result(case_id, contract_keys, title=""):
    """Build a valid in-progress result skeleton with all contract keys present."""
    result = {}
    for key in contract_keys:
        result[key] = [] if key in COLLECTION_FIELDS else None
    result.update({
        "case_id": case_id,
        "status": "in_progress",
        "title": title,
        "change_log": [{
            "time": utc_now(),
            "action": "init",
            "detail": "result skeleton written before extraction; incremental refinement follows",
        }],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "checks": [],
        "artifacts": [],
        "started_at": utc_now(),
        "_contract_keys": list(contract_keys),
    })
    return result


def merge_result(result, updates):
    """Merge incremental updates into result.

    Contract collections are replaced wholesale; evidence/degradation lists are
    appended with de-duplication; change_log is managed by the caller.
    """
    for key, value in (updates or {}).items():
        if key == "case_id":
            if not result.get("case_id") or result.get("case_id") == "unknown":
                result["case_id"] = value
            continue
        if key in DEFAULT_CONTRACT and isinstance(value, list):
            result[key] = list(value)
        elif key in ("sources", "uncertainty", "pending_human", "limitations") and isinstance(value, list):
            existing = result.get(key)
            base = list(existing) if isinstance(existing, list) else []
            result[key] = base + [v for v in value if v not in base]
        elif key == "checks" and isinstance(value, list):
            result["checks"] = list(value)
        elif key == "change_log":
            continue
        else:
            result[key] = value
    return result


def finalize_result(result, status, artifacts=None):
    result["status"] = status
    result["change_log"] = list(result.get("change_log") or []) + [
        {"time": utc_now(), "action": "finalize", "status": status}
    ]
    if artifacts is not None:
        result["artifacts"] = list(artifacts)
    result["finished_at"] = utc_now()
    return result


# --------------------------------------------------------------------------- #
# Three-gate validation (contract -> IR -> SVG). Order is deliberate: the
# declared contract is checked before any extraction or rendering work.
# --------------------------------------------------------------------------- #

def validate_contract(result, contract_keys):
    critical, warnings = [], []
    missing = [k for k in contract_keys if k not in result]
    if missing:
        critical.append("missing contract keys: " + ", ".join(missing))
    for key in contract_keys:
        if key in result and result[key] is None:
            warnings.append("contract field '%s' is null (empty)" % key)
    return {"gate": "contract", "critical": critical, "warnings": warnings, "pass": not critical}


def validate_ir(model):
    critical, warnings = [], []
    model = model if isinstance(model, dict) else {}
    entities = model.get("entities") if isinstance(model.get("entities"), list) else []
    relations = model.get("relations") if isinstance(model.get("relations"), list) else []
    if not entities:
        critical.append("entities missing or empty")
    ids = set()
    for e in entities:
        if not isinstance(e, dict):
            critical.append("entity entry is not an object")
            continue
        eid = e.get("id")
        if not eid:
            critical.append("entity missing id")
        elif eid in ids:
            critical.append("duplicate entity id: %s" % eid)
        else:
            ids.add(eid)
        if not e.get("label"):
            warnings.append("entity '%s' missing label" % eid)
        if not e.get("source_ref"):
            warnings.append("entity '%s' missing source_ref" % eid)
        if not e.get("quote"):
            warnings.append("entity '%s' missing verbatim quote" % eid)
    for r in relations:
        if not isinstance(r, dict):
            critical.append("relation entry is not an object")
            continue
        f, t = r.get("from"), r.get("to")
        if f not in ids:
            critical.append("relation.from not an entity: %s" % f)
        if t not in ids:
            critical.append("relation.to not an entity: %s" % t)
        if not r.get("source_ref"):
            warnings.append("relation %s->%s missing source_ref" % (f, t))
        if r.get("direction") not in DIRECTIONS:
            warnings.append("relation %s->%s unusual direction: %r" % (f, t, r.get("direction")))
    return {"gate": "ir", "critical": critical, "warnings": warnings, "pass": not critical}


# --------------------------------------------------------------------------- #
# Deterministic layout + layered SVG builder
# --------------------------------------------------------------------------- #

def auto_layout(entities, kind="flowchart", width=1600, height=1000):
    """Deterministic grid layout. No randomness, so output is reproducible."""
    entities = entities or []
    positions = {}
    n = len(entities)
    if n == 0:
        return positions, width, height
    # Route/flow/process diagrams favor a stage-oriented 3-column reading order;
    # apparatus/mechanism diagrams use a balanced near-square grid.
    cols = 3 if str(kind).lower() in ("route", "flowchart", "process") else int(math.ceil(math.sqrt(n)))
    rows = int(math.ceil(n / max(cols, 1)))
    mx, my = 130.0, 130.0
    gw = (width - 2 * mx) / max(cols, 1)
    gh = (height - 2 * my) / max(rows, 1)
    for i, e in enumerate(entities):
        if not isinstance(e, dict):
            continue
        eid = e.get("id") or "e%d" % i
        col = i % cols
        row = i // cols
        cx = mx + col * gw + gw / 2.0
        cy = my + row * gh + gh / 2.0
        positions[eid] = {
            "x": round(cx, 1),
            "y": round(cy, 1),
            "w": round(min(190.0, gw - 50.0), 1),
            "h": 64.0,
        }
    return positions, width, height


def build_svg(model, width=1600, height=1000):
    """Build a layered, editable SVG from a FigureModel IR.

    Layers: grid / relations / entities / annotations / legend.
    Every entity, relation and legend entry carries data-source-ref; entities and
    relations also carry data-id / data-from / data-to for independent re-checking.
    """
    model = model or {}
    kind = str(model.get("kind") or "flowchart")
    entities = model.get("entities") or []
    relations = model.get("relations") or []
    legend = model.get("legend_annotations") or []
    title = model.get("title") or "figure"
    case_id = model.get("case_id") or ""
    positions, width, height = auto_layout(entities, kind, width, height)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (width, height, width, height),
        '  <metadata data-case-id="%s" data-kind="%s" data-layout="auto-layout" data-generator="figure-model-skill"/>' % (esc(case_id), esc(kind)),
        '  <defs>',
        '    <marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L9,3 z" fill="#2c3e50"/></marker>',
        '    <marker id="arrow-recycle" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L9,3 z" fill="#8e44ad"/></marker>',
        '  </defs>',
        '  <text x="30" y="40" font-family="DejaVu Sans, Noto Sans CJK SC, Arial, sans-serif" font-size="22" font-weight="bold">%s</text>' % esc(title),
        '  <g id="layer-grid" data-layer="grid"/>',
        '  <g id="layer-relations" data-layer="relations">',
    ]

    for r in relations:
        if not isinstance(r, dict):
            continue
        f, t = r.get("from"), r.get("to")
        if f not in positions or t not in positions:
            continue
        x1, y1 = positions[f]["x"], positions[f]["y"]
        x2, y2 = positions[t]["x"], positions[t]["y"]
        direction = str(r.get("direction") or "forward")
        is_recycle = direction in RECYCLE_DIRECTIONS
        marker = "url(#arrow-recycle)" if is_recycle else "url(#arrow)"
        stroke = "#8e44ad" if is_recycle else "#2c3e50"
        dash = ' stroke-dasharray="6,4"' if is_recycle else ""
        lines.append(
            '    <line data-relation="true" data-from="%s" data-to="%s" data-direction="%s" '
            'data-source-ref="%s" x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="2"%s marker-end="%s"/>'
            % (esc(f), esc(t), esc(direction), esc(r.get("source_ref", "")),
               x1, y1, x2, y2, stroke, dash, marker)
        )
    lines.append('  </g>')

    lines.append('  <g id="layer-entities" data-layer="entities">')
    for e in entities:
        if not isinstance(e, dict):
            continue
        eid = e.get("id") or ""
        p = positions.get(eid)
        if not p:
            continue
        x = round(p["x"] - p["w"] / 2.0, 1)
        y = round(p["y"] - p["h"] / 2.0, 1)
        lines.append('    <g data-id="%s" data-entity="true" data-source-ref="%s">' % (esc(eid), esc(e.get("source_ref", ""))))
        lines.append('      <rect x="%s" y="%s" width="%s" height="%s" rx="8" fill="#eef4fb" stroke="#2c6fbb" stroke-width="1.5"/>'
                     % (x, y, p["w"], p["h"]))
        lines.append('      <text x="%s" y="%s" text-anchor="middle" font-family="DejaVu Sans, Noto Sans CJK SC, Arial, sans-serif" font-size="15">%s</text>'
                     % (p["x"], p["y"] + 5, esc(e.get("label") or eid)))
        lines.append('    </g>')
    lines.append('  </g>')

    lines.append('  <g id="layer-annotations" data-layer="annotations"/>')

    lines.append('  <g id="layer-legend" data-layer="legend">')
    for i, lg in enumerate(legend):
        if not isinstance(lg, dict):
            continue
        key = lg.get("key") or lg.get("text") or "legend-%d" % i
        text = lg.get("text") or key
        lx = 30 + (i % 4) * 360
        ly = height - 60 + (i // 4) * 28
        color = lg.get("color") or "#2c6fbb"
        lines.append('    <g data-legend="true" data-source-ref="%s">' % esc(lg.get("source_ref", "")))
        lines.append('      <rect x="%s" y="%s" width="16" height="16" fill="%s" stroke="#333333" stroke-width="1"/>' % (lx, ly, esc(color)))
        lines.append('      <text x="%s" y="%s" font-family="DejaVu Sans, Noto Sans CJK SC, Arial, sans-serif" font-size="13">%s</text>' % (lx + 22, ly + 13, esc(text)))
        lines.append('    </g>')
    lines.append('  </g>')

    lines.append('</svg>')
    return "\n".join(lines)


def validate_svg(svg_text, model):
    critical, warnings = [], []
    text = svg_text or ""
    if text.lstrip().startswith("<?xml"):
        text = re.sub(r"^\s*<\?xml[^?]*\?>", "", text, count=1)
    try:
        root = _ET.fromstring(text)
    except Exception as ex:  # noqa: BLE001
        return {"gate": "svg", "critical": ["svg parse error: %s" % ex], "warnings": [], "pass": False}

    if root.tag.split("}")[-1] != "svg":
        critical.append("svg root element is not <svg>")
    viewbox = root.get("viewBox") or ""
    if not re.match(r"^\s*[\d.\-]+\s+[\d.\-]+\s+[\d.]+\s+[\d.]+\s*$", viewbox):
        warnings.append("svg missing or malformed viewBox")

    layers = {g.get("id") for g in root.iter() if g.tag == SVG_NS + "g" and g.get("data-layer")}
    for want in ("layer-relations", "layer-entities", "layer-annotations", "layer-legend"):
        if want not in layers:
            warnings.append("missing layer '%s'" % want)

    entity_ids = set()
    for e in (model or {}).get("entities") or []:
        if isinstance(e, dict) and e.get("id"):
            entity_ids.add(e.get("id"))
    svg_entities = set()
    for g in root.iter(SVG_NS + "g"):
        if g.get("data-entity") == "true":
            svg_entities.add(g.get("data-id"))
    for eid in entity_ids - svg_entities:
        critical.append("IR entity not rendered in SVG: %s" % eid)
    for eid in svg_entities - entity_ids:
        warnings.append("SVG entity not present in IR: %s" % eid)

    for line in root.iter(SVG_NS + "line"):
        if line.get("data-relation") == "true":
            f, t = line.get("data-from"), line.get("data-to")
            if f not in entity_ids or t not in entity_ids:
                critical.append("SVG relation references missing entity: %s->%s" % (f, t))
            if not line.get("marker-end"):
                warnings.append("SVG relation %s->%s has no arrow marker" % (f, t))
    return {"gate": "svg", "critical": critical, "warnings": warnings, "pass": not critical}
