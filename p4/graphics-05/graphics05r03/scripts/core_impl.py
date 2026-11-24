# -*- coding: utf-8 -*-
"""core_impl.py -- deterministic extraction, layout, SVG build, validation and rendering.

Pure Python standard library. Imported by main.py and selftest.py.

Design contract
---------------
1. ``result.json`` is a *pure function* of the input files: it contains no wall-clock
   timestamps, no process id, no random values. Runtime telemetry goes to
   ``/app/work/run_meta.json`` and the console log, never into ``result.json``.
2. All JSON is serialized with ``sort_keys=True`` so two runs of the same input produce
   byte-identical structured artefacts (``result.json`` and ``fig_*.svg``).
3. Entity/relation/data identifiers and their layout positions are derived by stable
   sorting only; no PRNG is imported at build time.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

SCHEMA_VERSION = "figure-spec-v1"
CANONICAL_RESULT_VERSION = "result-v1"
SVG_NS = "http://www.w3.org/2000/svg"
FONT_MIN_PX = 8.0
MAX_SOURCE_FILE_BYTES = 2 * 1024 * 1024  # 2 MB per text source

# Colorblind-safe, grayscale-separable palette (fixed order = deterministic encoding).
PALETTE = [
    "#4C72B0",  # blue
    "#DD8452",  # orange
    "#55A868",  # green
    "#C44E52",  # red
    "#8172B3",  # purple
    "#937860",  # brown
    "#DA8BC3",  # pink
    "#8C8C8C",  # gray
    "#CCB974",  # olive
    "#64B5CD",  # cyan
]

ET.register_namespace("", SVG_NS)


# --------------------------------------------------------------------------- #
# Robust file / input handling
# --------------------------------------------------------------------------- #
def read_text_any(path, max_bytes=MAX_SOURCE_FILE_BYTES):
    """Read a text file tolerating BOM and common bad encodings.

    Returns (text, degradation) where degradation is None or a string.
    """
    size = os.path.getsize(path)
    if size > max_bytes:
        return "", "skipped: file exceeds %d bytes (%s)" % (max_bytes, path)
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "decoded with replacement chars"


def discover_inputs(input_dir):
    """Scan the input directory without assuming fixed filenames."""
    found = {"case": [], "task": [], "materials": [], "other": []}
    if not os.path.isdir(input_dir):
        return found
    for root, _dirs, files in os.walk(input_dir):
        for name in files:
            full = os.path.join(root, name)
            low = name.lower()
            if name == "case.json" or (low.endswith(".json") and "case" in low):
                found["case"].append(full)
            elif name == "task.md" or (low.endswith(".md") and "task" in low):
                found["task"].append(full)
            elif low.endswith((".pdf", ".csv", ".tsv", ".txt", ".md", ".xml", ".json")):
                found["materials"].append(full)
            else:
                found["other"].append(full)
    return found


def _first_contract_candidate(case):
    for key in ("output_contract", "outputContract", "output", "contract"):
        if isinstance(case.get(key), (dict, list)):
            return case[key]
    return {}


def _flatten_contract_keys(contract):
    """Return contract field names, preserving order for lists and dicts."""
    if isinstance(contract, dict):
        return list(contract.keys())
    if isinstance(contract, list):
        keys = []
        for item in contract:
            if isinstance(item, dict):
                keys.extend(item.keys())
            elif isinstance(item, str):
                keys.append(item)
        return keys
    return []


def load_case(input_dir):
    """Load case.json + task.md robustly. Never raises on bad input.

    Returns (case, degradations, errors).
    """
    found = discover_inputs(input_dir)
    case = {}
    degradations = []
    errors = []
    case_path = found["case"][0] if found["case"] else None
    if case_path:
        text, deg = read_text_any(case_path)
        if deg:
            degradations.append(deg)
        try:
            case = json.loads(text)
        except json.JSONDecodeError as exc:
            errors.append("case.json not valid JSON: %s" % exc)
            case = {}
        if not isinstance(case, dict):
            errors.append("case.json top level is not an object")
            case = {}
    else:
        errors.append("no case.json found under %s" % input_dir)

    task_text = ""
    if found["task"]:
        task_text, deg = read_text_any(found["task"][0])
        if deg:
            degradations.append(deg)
    case.setdefault("task_text", task_text)

    case.setdefault("case_id", _derive_case_id(case, case_path))
    return case, degradations, errors


def _derive_case_id(case, case_path):
    for key in ("case_id", "caseId", "id", "challenge_id", "challengeId"):
        if case.get(key):
            return str(case[key])
    if case_path:
        return "case-" + _stable_slug(os.path.basename(os.path.dirname(case_path)))
    return "case-unknown"


# --------------------------------------------------------------------------- #
# Determinism helpers
# --------------------------------------------------------------------------- #
def canonical_json(obj):
    """Deterministic JSON serialization used for fingerprints and file writes."""
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)


def _stable_slug(text):
    text = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "-", str(text)).strip("-")
    return text[:40] or "item"


def stable_id(prefix, text, seen):
    base = prefix + "-" + _stable_slug(text)
    if base not in seen:
        seen.add(base)
        return base
    i = 2
    while ("%s-%d" % (base, i)) in seen:
        i += 1
    sid = "%s-%d" % (base, i)
    seen.add(sid)
    return sid


def fingerprint(obj):
    """SHA-256 over canonical JSON; stable across runs for identical inputs."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()[:16]


def stable_sorted(items, key_fn):
    return sorted(items, key=key_fn)


# --------------------------------------------------------------------------- #
# Result skeleton and contract backfill
# --------------------------------------------------------------------------- #
def empty_abstract():
    return {"question": None, "method": None, "results": None, "conclusion": None}


def canonical_result(case, contract):
    """Build the result skeleton with every output_contract field preserved."""
    result = {
        "case_id": case.get("case_id", "case-unknown"),
        "status": "partial",
        "graphical_abstract": empty_abstract(),
        "editable_source": {
            "format": "svg",
            "path": None,
            "layers": [],
            "text_editable": True,
        },
        "exports": [],
        "entities": [],
        "relations": [],
        "data_mapping": [],
        "legend": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "determinism": {
            "schema_version": SCHEMA_VERSION,
            "result_version": CANONICAL_RESULT_VERSION,
            "seed": "none",
            "layout": "grid-v1",
            "fingerprint": None,
        },
    }
    # Preserve original contract keys verbatim; unknown keys default to null.
    for key in _flatten_contract_keys(contract):
        result.setdefault(key, None)
    if isinstance(contract, dict):
        for key, val in contract.items():
            result.setdefault(key, None if not isinstance(val, (dict, list)) else ({} if isinstance(val, dict) else []))
    return result


def apply_contract_values(result, contract):
    """Overlay concrete contract defaults where the case provided literal values."""
    if not isinstance(contract, dict):
        return result
    for key, val in contract.items():
        if key not in result:
            result[key] = val
        elif isinstance(val, (str, int, float, bool)) and (result.get(key) in (None, "")):
            result[key] = val
    return result


def new_change_log(result, seq, step, action, reason=""):
    result["change_log"].append({
        "seq": seq,
        "step": step,
        "action": action,
        "reason": reason,
    })
    return result


# --------------------------------------------------------------------------- #
# Figure spec template and layout
# --------------------------------------------------------------------------- #
def template_figure_spec(case):
    return {
        "schema": SCHEMA_VERSION,
        "case_id": case.get("case_id", "case-unknown"),
        "title": case.get("title") or "Graphical Abstract",
        "canvas": {"width": 1200, "height": 900, "unit": "px"},
        "panels": [
            {"id": "question", "label": "Question", "order": 1},
            {"id": "method", "label": "Method", "order": 2},
            {"id": "results", "label": "Results", "order": 3},
            {"id": "conclusion", "label": "Conclusion", "order": 4},
        ],
        "entities": [],
        "relations": [],
        "data": [],
        "constraints": [],
        "legend": [],
        "evidence": [],
    }


def compute_layout(spec):
    """Deterministic panel grid + entity anchoring. No random values."""
    canvas = spec.get("canvas") or {"width": 1200, "height": 900}
    W = float(canvas.get("width", 1200))
    H = float(canvas.get("height", 900))
    margin = 48.0
    gap = 32.0
    title_h = 72.0
    legend_h = 88.0
    panels = stable_sorted(spec.get("panels", []), lambda p: (p.get("order", 0), p.get("id", "")))
    n = max(len(panels), 1)
    cols = 2 if n >= 2 else 1
    rows = (n + cols - 1) // cols
    content_top = title_h + margin
    content_bottom = H - margin - legend_h
    content_left = margin
    content_right = W - margin
    cell_w = (content_right - content_left - gap * (cols - 1)) / cols
    cell_h = (content_bottom - content_top - gap * (rows - 1)) / rows

    panel_boxes = {}
    for i, panel in enumerate(panels):
        row = i // cols
        col = i % cols
        x = content_left + col * (cell_w + gap)
        y = content_top + row * (cell_h + gap)
        panel_boxes[panel["id"]] = {"x": x, "y": y, "w": cell_w, "h": cell_h}

    # Entity anchoring: stable vertical stack inside its assigned panel.
    anchors = {}
    box_h = 58.0
    pad = 18.0
    stack_cursor = {pid: pad for pid in panel_boxes}
    for ent in stable_sorted(spec.get("entities", []), lambda e: (e.get("panel", ""), e.get("id", ""))):
        pid = ent.get("panel") or "method"
        box = panel_boxes.get(pid) or panel_boxes.get("method")
        y = box["y"] + stack_cursor.get(pid, pad)
        x = box["x"] + pad
        w = max(box["w"] - 2 * pad, 120)
        anchors[ent["id"]] = {"x": x, "y": y, "w": w, "h": box_h, "cx": x + w / 2, "cy": y + box_h / 2, "panel": pid}
        stack_cursor[pid] = stack_cursor.get(pid, pad) + box_h + 14.0
    return {
        "panel_boxes": panel_boxes,
        "anchors": anchors,
        "legend_y": H - legend_h + 28.0,
        "content_bottom": content_bottom,
    }


# --------------------------------------------------------------------------- #
# SVG building
# --------------------------------------------------------------------------- #
def _el(tag, parent, attrs=None, text=None):
    node = ET.SubElement(parent, "{%s}%s" % (SVG_NS, tag))
    for k, v in (attrs or {}).items():
        node.set(k, str(v))
    if text is not None:
        node.text = text
    return node


def _layer(svg, lid):
    return _el("g", svg, {"id": "layer-" + lid})


def build_svg(spec):
    """Build a layered, text-editable SVG. Returns (svg_text, manifest)."""
    layout = compute_layout(spec)
    canvas = spec.get("canvas") or {"width": 1200, "height": 900}
    W = canvas.get("width", 1200)
    H = canvas.get("height", 900)
    svg = ET.Element("{%s}svg" % SVG_NS, {
        "viewBox": "0 0 %s %s" % (W, H),
        "width": "%s" % W,
        "height": "%s" % H,
        "role": "img",
    })
    svg.set("data-case-id", str(spec.get("case_id", "case-unknown")))
    svg.set("data-schema", SCHEMA_VERSION)
    svg.set("data-layout", "grid-v1")

    layers = {}
    for lid in ("background", "title", "panels", "entities", "relations", "data", "legend", "annotations"):
        layers[lid] = _layer(svg, lid)

    _el("rect", layers["background"], {"x": 0, "y": 0, "width": W, "height": H, "fill": "#FFFFFF"})
    title = _el("text", layers["title"], {
        "x": 48, "y": 40, "font-family": "DejaVu Sans, sans-serif", "font-size": 24,
        "font-weight": "bold", "fill": "#1A1A1A",
    })
    title.text = spec.get("title") or "Graphical Abstract"

    # Panels.
    for pid, box in layout["panel_boxes"].items():
        label = next((p.get("label", pid) for p in spec.get("panels", []) if p.get("id") == pid), pid)
        _el("rect", layers["panels"], {
            "x": box["x"], "y": box["y"], "width": box["w"], "height": box["h"],
            "rx": 10, "fill": "#F7F8FA", "stroke": "#9AA0A6", "stroke-width": 1,
        })
        t = _el("text", layers["panels"], {
            "x": box["x"] + 14, "y": box["y"] + 24, "font-family": "DejaVu Sans, sans-serif",
            "font-size": 15, "font-weight": "bold", "fill": "#333333",
        })
        t.text = label

    # Entities.
    color_by_id = {}
    for i, ent in enumerate(stable_sorted(spec.get("entities", []), lambda e: e.get("id", ""))):
        color_by_id[ent.get("id", "")] = PALETTE[i % len(PALETTE)]
    for ent in stable_sorted(spec.get("entities", []), lambda e: e.get("id", "")):
        eid = ent.get("id", "")
        anchor = layout["anchors"].get(eid)
        if anchor is None:
            continue
        fill = color_by_id.get(eid, "#4C72B0")
        _el("rect", layers["entities"], {
            "x": anchor["x"], "y": anchor["y"], "width": anchor["w"], "height": anchor["h"],
            "rx": 8, "fill": fill, "fill-opacity": 0.18, "stroke": fill, "stroke-width": 1.5,
            "data-id": eid, "data-kind": ent.get("kind", "unknown"),
            "data-source": str(ent.get("source", "")),
        })
        t = _el("text", layers["entities"], {
            "x": anchor["x"] + 10, "y": anchor["y"] + 22, "font-family": "DejaVu Sans, sans-serif",
            "font-size": 13, "fill": "#111111",
        })
        t.text = ent.get("label", eid)
        if ent.get("sub" ):
            sub = _el("text", layers["entities"], {
                "x": anchor["x"] + 10, "y": anchor["y"] + 40, "font-family": "DejaVu Sans, sans-serif",
                "font-size": 11, "fill": "#444444",
            })
            sub.text = ent.get("sub")

    # Relations.
    rel_index = {}
    for rel in stable_sorted(spec.get("relations", []), lambda r: r.get("id", "")):
        rid = rel.get("id", "")
        a = layout["anchors"].get(rel.get("from", ""))
        b = layout["anchors"].get(rel.get("to", ""))
        if a is None or b is None or rel.get("from") == rel.get("to"):
            continue
        direction = rel.get("direction", "->")
        marker = None
        if direction in ("->", "=>"):
            marker = "arrow"
        line_attrs = {
            "x1": a["cx"], "y1": a["cy"], "x2": b["cx"], "y2": b["cy"],
            "stroke": "#555555", "stroke-width": 2,
            "data-id": rid, "data-from": rel.get("from", ""), "data-to": rel.get("to", ""),
            "data-direction": direction, "data-source": str(rel.get("source", "")),
        }
        if marker == "arrow":
            line_attrs["marker-end"] = "url(#arrow)"
        _el("line", layers["relations"], line_attrs)
        rel_index[rid] = {"from": rel.get("from"), "to": rel.get("to"), "direction": direction}
        if rel.get("label"):
            mx = (a["cx"] + b["cx"]) / 2
            my = (a["cy"] + b["cy"]) / 2 - 8
            lab = _el("text", layers["relations"], {
                "x": mx, "y": my, "font-family": "DejaVu Sans, sans-serif", "font-size": 11,
                "fill": "#333333", "text-anchor": "middle",
            })
            lab.text = rel.get("label")

    # Arrow marker defs (single def, deterministic id).
    defs = _el("defs", svg)
    marker = _el("marker", defs, {
        "id": "arrow", "viewBox": "0 0 10 10", "refX": 9, "refY": 5,
        "markerWidth": 7, "markerHeight": 7, "orient": "auto-start-reverse",
    })
    _el("path", marker, {"d": "M 0 0 L 10 5 L 0 10 z", "fill": "#555555"})

    # Data bars.
    data_values = [d for d in spec.get("data", []) if isinstance(d.get("value"), (int, float))]
    if data_values:
        vmax = max(abs(d["value"]) for d in data_values) or 1.0
        box = layout["panel_boxes"].get("results") or list(layout["panel_boxes"].values())[-1]
        base_y = box["y"] + box["h"] - 30
        bar_w = (box["w"] - 60) / max(len(data_values), 1)
        for i, d in enumerate(stable_sorted(data_values, lambda x: x.get("id", ""))):
            did = d.get("id", "")
            fill = color_by_id.get(did, PALETTE[i % len(PALETTE)])
            length = max(12.0, (abs(d["value"]) / vmax) * (box["h"] - 120))
            bx = box["x"] + 30 + i * bar_w
            by = base_y - length
            _el("rect", layers["data"], {
                "x": bx, "y": by, "width": max(bar_w - 20, 24), "height": length,
                "fill": fill, "data-id": did, "data-encoding": d.get("encoding", "bar-length"),
                "data-source": str(d.get("source", "")),
            })
            lab = _el("text", layers["data"], {
                "x": bx, "y": base_y + 16, "font-family": "DejaVu Sans, sans-serif",
                "font-size": 10, "fill": "#222222",
            })
            unit = (" " + d["unit"]) if d.get("unit") else ""
            lab.text = "%s=%s%s" % (d.get("label", did), d.get("value"), unit)

    # Legend.
    legend_items = stable_sorted(spec.get("legend", []), lambda x: x.get("id", ""))
    ly = layout["legend_y"]
    for i, item in enumerate(legend_items):
        lx = 48 + i * 190
        fill = item.get("color") or PALETTE[i % len(PALETTE)]
        _el("rect", layers["legend"], {
            "x": lx, "y": ly - 12, "width": 16, "height": 16, "rx": 3,
            "fill": fill, "data-id": item.get("id", "legend-%d" % i),
            "data-target": str(item.get("target", "")), "data-source": str(item.get("source", "")),
        })
        lab = _el("text", layers["legend"], {
            "x": lx + 22, "y": ly, "font-family": "DejaVu Sans, sans-serif",
            "font-size": 11, "fill": "#222222",
        })
        lab.text = item.get("label", item.get("id", ""))

    # Constraints / annotation strip.
    for i, c in enumerate(spec.get("constraints", [])):
        t = _el("text", layers["annotations"], {
            "x": 48, "y": layout["content_bottom"] + 12 + i * 16,
            "font-family": "DejaVu Sans, sans-serif", "font-size": 10, "fill": "#555555",
        })
        t.text = "%s: %s" % (c.get("type", "note"), c.get("value", c.get("label", "")))

    xml_text = ET.tostring(svg, encoding="unicode")
    xml_text = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_text

    manifest = {
        "case_id": spec.get("case_id"),
        "canvas": canvas,
        "panels": layout["panel_boxes"],
        "anchors": layout["anchors"],
        "relations": rel_index,
        "entity_count": len(spec.get("entities", [])),
        "relation_count": len(spec.get("relations", [])),
        "data_count": len(spec.get("data", [])),
        "legend_count": len(spec.get("legend", [])),
    }
    return xml_text, manifest


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def _color_luminance(hex_color):
    hex_color = (hex_color or "#000000").lstrip("#")
    if len(hex_color) != 6:
        return 0.0
    try:
        r, g, b = (int(hex_color[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return 0.0
    def lin(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast_ratio(a, b):
    la, lb = _color_luminance(a), _color_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def validate(svg_text, spec, result):
    """Validate SVG against the spec and backfill result evidence fields.

    Returns (defects, result). Defects is a list of {level, code, message}.
    """
    defects = []
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        defects.append({"level": "critical", "code": "svg_unparseable", "message": str(exc)})
        result["limitations"].append("SVG failed to parse; editable source unusable")
        return defects, result

    # Source -> verbatim quote map.
    source_map = {}
    for ev in spec.get("evidence", []):
        if ev.get("id") and ev.get("locator"):
            source_map[str(ev.get("locator"))] = {
                "id": ev.get("id"),
                "locator": ev.get("locator"),
                "quote": ev.get("quote", ""),
            }

    entities = {e.get("id"): e for e in spec.get("entities", []) if e.get("id")}
    relations = {r.get("id"): r for r in spec.get("relations", []) if r.get("id")}
    data = {d.get("id"): d for d in spec.get("data", []) if d.get("id")}

    # Backfill entities/relations/data/legend/sources into result.
    result["entities"] = []
    for eid, e in stable_sorted(list(entities.items()), lambda kv: kv[0]):
        result["entities"].append(e)
    result["relations"] = []
    for rid, r in stable_sorted(list(relations.items()), lambda kv: kv[0]):
        result["relations"].append(r)
    result["data_mapping"] = []
    for did, d in stable_sorted(list(data.items()), lambda kv: kv[0]):
        result["data_mapping"].append(d)
    result["legend"] = stable_sorted(spec.get("legend", []), lambda x: x.get("id", ""))
    result["sources"] = stable_sorted(list(source_map.values()), lambda s: s.get("locator", ""))

    # Every referenced entity/relation/data needs a source pointer.
    for label, coll, key in (("entity", entities, "entities"), ("relation", relations, "relations"),
                             ("data", data, "data")):
        for item in coll.values():
            if not item.get("source"):
                defects.append({"level": "warning", "code": "missing_source",
                                "message": "%s '%s' has no source pointer" % (label, item.get("id"))})
                result["uncertainty"].append({
                    "id": item.get("id"),
                    "issue": "missing_source",
                    "note": "%s has no locator; traced only to the spec, not the material" % label,
                })
            elif item.get("source") not in source_map:
                defects.append({"level": "warning", "code": "source_not_in_evidence",
                                "message": "%s '%s' source not in evidence list" % (label, item.get("id"))})

    # Every SVG data-id must map to a known spec id.
    svg_ids = set()
    for node in root.iter():
        if node.get("data-id"):
            svg_ids.add(node.get("data-id"))
    known_ids = set(entities) | set(relations) | set(data) | {l.get("id") for l in spec.get("legend", [])}
    for sid in svg_ids:
        if sid not in known_ids:
            defects.append({"level": "error", "code": "orphan_svg_id",
                            "message": "SVG data-id '%s' has no matching spec entry" % sid})

    # Text legibility checks.
    for node in root.iter():
        if node.tag == "{%s}text" % SVG_NS:
            try:
                fs = float(re.sub(r"px$", "", node.get("font-size", "0")) or 0)
            except ValueError:
                fs = 0.0
            if fs and fs < FONT_MIN_PX:
                defects.append({"level": "warning", "code": "font_too_small",
                                "message": "text '%s' has font-size %s < %s px" % (
                                    (node.text or "").strip()[:20], fs, FONT_MIN_PX)})
            fill = node.get("fill", "#000000")
            if _contrast_ratio(fill, "#FFFFFF") < 2.0:
                defects.append({"level": "warning", "code": "low_contrast",
                                "message": "text '%s' low contrast on white" % (node.text or "").strip()[:20]})

    # Canvas overflow (bounding boxes must stay inside viewBox).
    try:
        vb = [float(v) for v in root.get("viewBox", "0 0 1200 900").split()]
        W, H = vb[2], vb[3]
    except (ValueError, IndexError):
        W, H = 1200, 900
    for node in root.iter():
        for attr in ("x", "y"):
            if node.get(attr) is None:
                continue
            try:
                val = float(node.get(attr))
            except ValueError:
                continue
            if val < -1 or val > (W if attr == "x" else H):
                defects.append({"level": "warning", "code": "canvas_overflow",
                                "message": "element %s %s=%s outside canvas" % (node.tag, attr, val)})

    return defects, result


# --------------------------------------------------------------------------- #
# Rendering (honest fallback)
# --------------------------------------------------------------------------- #
RENDERERS = [
    ("rsvg-convert", ["rsvg-convert", "-w", "1600", "-h", "1200", "-o", "@png", "@svg"]),
    ("inkscape", ["inkscape", "@svg", "--export-type=png", "--export-filename=@png", "--export-width=1600"]),
    ("cairosvg", None),  # python package, handled separately
    ("chromium", ["chromium", "--headless", "--disable-gpu", "--screenshot=@png", "--window-size=1600,1200", "@svg"]),
    ("chrome", ["chrome", "--headless", "--disable-gpu", "--screenshot=@png", "--window-size=1600,1200", "@svg"]),
]


def find_renderer():
    for name, _args in RENDERERS:
        if name == "cairosvg":
            try:
                import cairosvg  # noqa: F401
                return "cairosvg"
            except ImportError:
                continue
        if shutil_which(name):
            return name
    return None


def shutil_which(cmd):
    import shutil
    return shutil.which(cmd)


def render_previews(svg_path, out_dir):
    """Attempt PNG/PDF previews with whatever renderer exists; never fabricate.

    Export ``path`` values are recorded *relative to out_dir* so result.json is
    portable and byte-identical across machines and mount points.
    """
    exports = []
    renderer = find_renderer()
    base = os.path.splitext(os.path.basename(svg_path))[0]
    png_rel = base + ".png"
    pdf_rel = base + ".pdf"
    png_path = os.path.join(out_dir, png_rel)
    pdf_path = os.path.join(out_dir, pdf_rel)
    if renderer is None:
        exports.append({"format": "png", "path": png_rel, "renderer": None, "status": "preview_not_rendered"})
        exports.append({"format": "pdf", "path": pdf_rel, "renderer": None, "status": "preview_not_rendered"})
        return exports

    if renderer == "cairosvg":
        try:
            import cairosvg
            cairosvg.svg2png(url=svg_path, write_to=png_path, output_width=1600, output_height=1200)
            exports.append({"format": "png", "path": png_rel, "renderer": "cairosvg", "status": "rendered"})
            cairosvg.svg2pdf(url=svg_path, write_to=pdf_path)
            exports.append({"format": "pdf", "path": pdf_rel, "renderer": "cairosvg", "status": "rendered"})
        except Exception as exc:  # noqa: BLE001
            exports.append({"format": "png", "path": png_rel, "renderer": "cairosvg",
                            "status": "render_failed", "error": str(exc)})
            exports.append({"format": "pdf", "path": pdf_rel, "renderer": "cairosvg",
                            "status": "render_failed", "error": str(exc)})
        return exports

    template = dict(RENDERERS)[renderer]
    def run(fmt, out, rel):
        args = []
        for part in template:
            if part == "@svg":
                args.append(svg_path)
            elif part == "@png":
                args.append(out)
            else:
                args.append(part)
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=120)
            if proc.returncode == 0 and os.path.exists(out):
                return {"format": fmt, "path": rel, "renderer": renderer, "status": "rendered"}
            return {"format": fmt, "path": rel, "renderer": renderer,
                    "status": "render_failed", "error": (proc.stderr or "").strip()[:200]}
        except Exception as exc:  # noqa: BLE001
            return {"format": fmt, "path": rel, "renderer": renderer,
                    "status": "render_failed", "error": str(exc)}

    exports.append(run("png", png_path, png_rel))
    # PDF export requires a real print-to-pdf renderer; recorded honestly as pending.
    exports.append({"format": "pdf", "path": pdf_rel, "renderer": renderer,
                    "status": "preview_not_rendered",
                    "error": "PDF export requires platform pdf-render-tool/office-renderer"})
    return exports
