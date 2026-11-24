#!/usr/bin/env python3
"""core_impl: standard-library implementation for the apparatus/technical-route
diagram skill.

Design focus (robustness variant): every entry point degrades gracefully.
Empty input, malformed JSON, missing parameters, path traversal, oversized
files, missing render tools and font absence must never crash the process;
they produce an honest, valid result.json with status/errors/degradations.

Determinism: layout, sorting and id generation are stable, so the same input
produces the same figure on re-run. No randomness, no network, no third-party
packages.
"""
from __future__ import annotations

import datetime as _dt
import html as _html
import json as _json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import xml.etree.ElementTree as _ET
from collections import OrderedDict

VERSION = "1.0.0"
# Files larger than this are skipped and recorded, not blindly loaded.
MAX_TEXT_BYTES = 2 * 1024 * 1024
# Canvas geometry defaults; explicit geometry in the IR wins.
CANVAS = {"width": 1200, "height": 900, "margin": 60, "grid_x": 240, "grid_y": 130}

DEFAULT_CONTRACT = OrderedDict(
    [
        ("entities", []),
        ("relations", []),
        ("data_encoding", []),
        ("legend_annotations", []),
        ("editable_sources", []),
        ("exports", []),
    ]
)

# Status vocabulary. complete/partial describe a usable diagram;
# insufficient_evidence / input_error / failed describe honest failure modes.
STATUSES = ("in_progress", "complete", "partial", "insufficient_evidence", "input_error", "failed")

_LINE_STYLE = {
    "flow": ("solid", "black", 2.0, "triangle"),
    "material": ("solid", "#1f77b4", 2.4, "triangle"),
    "energy": ("solid", "#d62728", 2.0, "triangle"),
    "information": ("solid", "#2ca02c", 2.0, "triangle"),
    "sequence": ("solid", "#7f7f7f", 2.0, "triangle"),
    "back": ("dashed", "#9467bd", 2.0, "triangle"),
    "recycle": ("dashed", "#9467bd", 2.0, "triangle"),
    "waste": ("solid", "#8c564b", 2.0, "triangle"),
    "split": ("solid", "#17becf", 2.0, "open"),
    "merge": ("solid", "#17becf", 2.0, "open"),
}


def utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text_safe(path: str, max_bytes: int = MAX_TEXT_BYTES):
    """Read a UTF-8 text file tolerantly. Returns (text, note) where note is
    None on success or a degradation string. Never raises for IO/encoding
    problems; it returns empty text plus a note instead."""
    if not path or not os.path.exists(path):
        return "", "missing_file"
    try:
        size = os.path.getsize(path)
    except OSError:
        return "", "stat_failed"
    if size > max_bytes:
        return "", "oversized(%dB>%dB)" % (size, max_bytes)
    raw = b""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return "", "read_failed(%s)" % exc.__class__.__name__
    if not raw:
        return "", "empty_file"
    # BOM handling: utf-8-sig then latin-1 last resort (never raises).
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), None
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), "replaced_encoding"


def json_load_safe(path: str, fallback=None, max_bytes: int = MAX_TEXT_BYTES):
    """Load JSON without raising. Returns (obj, error) where error is None or a
    short code. Tolerates a leading BOM. A JSON array is wrapped so callers
    always receive a mapping when possible."""
    text, note = read_text_safe(path, max_bytes=max_bytes)
    if note:
        return fallback, note
    try:
        obj = _json.loads(text)
    except (ValueError, TypeError) as exc:
        return fallback, "bad_json(%s)" % exc.__class__.__name__
    if isinstance(obj, list):
        return {"items": obj}, "wrapped_array"
    if not isinstance(obj, dict):
        return fallback, "non_object"
    return obj, None


def sanitize_id(value: str, fallback: str = "e") -> str:
    """Stable, path-safe id. Deterministic for repeated runs."""
    if not value:
        return fallback
    s = unicodedata.normalize("NFKC", str(value))
    s = re.sub(r"[^0-9A-Za-z_.-]+", "_", s).strip("_")
    if not s:
        s = "n_%s" % _stable_digest(str(value))[:8]
    if not re.match(r"^[A-Za-z_]", s):
        s = "n_" + s
    return s[:48]


def _stable_digest(text: str) -> str:
    """Small deterministic digest for tie-breaking without random seeds."""
    import hashlib

    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


def now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def append_change(record: dict, action: str, detail: str = "") -> None:
    """Append one change-log entry; deduplicate identical adjacent entries."""
    entry = {
        "time": utcnow(),
        "action": action,
        "detail": detail,
    }
    log = record.setdefault("change_log", [])
    if log and log[-1].get("action") == action and log[-1].get("detail") == detail:
        log[-1]["time"] = entry["time"]
        return
    log.append(entry)


def ensure_list(value):
    return value if isinstance(value, list) else []


def ensure_dict(value):
    return value if isinstance(value, dict) else {}


# --------------------------------------------------------------------------
# Input discovery and case reading
# --------------------------------------------------------------------------
def discover_case(input_dir: str) -> dict:
    """Find case.json/task.md without assuming fixed names. Returns a dict with
    the found paths and any errors; never raises."""
    out = {"case_path": None, "task_path": None, "notes": []}
    if not input_dir:
        out["notes"].append("no_input_dir")
        return out
    try:
        names = sorted(os.listdir(input_dir))
    except OSError as exc:
        out["notes"].append("listdir_failed(%s)" % exc.__class__.__name__)
        return out
    for name in names:
        low = name.lower()
        if out["case_path"] is None and low.endswith(".json") and "case" in low:
            out["case_path"] = os.path.join(input_dir, name)
        if out["task_path"] is None and low == "task.md":
            out["task_path"] = os.path.join(input_dir, name)
        if out["case_path"] is None and low.endswith(".json"):
            out["case_path"] = os.path.join(input_dir, name)
    if out["case_path"] is None:
        out["notes"].append("no_case_json")
    if out["task_path"] is None:
        out["notes"].append("no_task_md")
    return out


def load_case(case_path) -> dict:
    """Load the case mapping and copy its output_contract verbatim."""
    obj, err = json_load_safe(case_path, fallback={})
    meta = {"error": err}
    if not isinstance(obj, dict):
        return meta
    meta.update({"raw": obj})
    meta["case_id"] = obj.get("case_id") or obj.get("challenge_id") or obj.get("id") or "unknown_case"
    contract = obj.get("output_contract")
    if isinstance(contract, dict):
        # Preserve key order and all unknown keys; values default to null/[].
        meta["contract"] = OrderedDict((k, contract[k] if contract[k] is not None else None) for k in contract)
        # Default typed defaults for the six standard fields if present.
        for key, default in DEFAULT_CONTRACT.items():
            if key in meta["contract"] and meta["contract"][key] is None:
                meta["contract"][key] = default
    else:
        meta["contract"] = OrderedDict(DEFAULT_CONTRACT)
    meta["title"] = obj.get("title") or obj.get("task_title") or "diagram"
    return meta


# --------------------------------------------------------------------------
# IR schema helpers (full schema in references/ir_schema.md)
# --------------------------------------------------------------------------
def empty_ir(title: str = "diagram", case_id: str = "unknown_case") -> dict:
    return {
        "meta": {
            "title": title,
            "case_id": case_id,
            "kind": "apparatus_technical_route",
            "layout_mode": "auto",
            "generated_at": utcnow(),
            "generator": "scix-figure-skill/core_impl:%s" % VERSION,
        },
        "entities": [],
        "relations": [],
        "data_encoding": [],
        "legend_annotations": [],
        "sources": [],
        "uncertainty": [],
        "notes": [],
    }


def normalize_ir(ir, title="diagram", case_id="unknown_case") -> dict:
    """Coerce arbitrary IR input into a usable, validated structure without
    raising. Unknown fields are preserved but the six contract sections are
    guaranteed to exist."""
    if not isinstance(ir, dict):
        ir = {}
    base = empty_ir(title=title, case_id=case_id)
    meta = ensure_dict(ir.get("meta"))
    for k, v in meta.items():
        base["meta"][k] = v
    for key in ("entities", "relations", "data_encoding", "legend_annotations", "sources", "uncertainty", "notes"):
        base[key] = ensure_list(ir.get(key))
    return base


def auto_extract_ir(text: str, title: str, case_id: str) -> dict:
    """Deterministic heuristic fallback for a skeleton IR when the agent has not
    authored /app/work/ir.json. It only marks candidate items as low-confidence
    so downstream SVG still renders; the LLM is expected to refine the IR."""
    ir = empty_ir(title=title, case_id=case_id)
    if not text:
        return ir
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    ent_id = 0
    seen = set()
    # Numbered lists "1. xxx" and bullet lists are treated as step candidates.
    step_re = re.compile(r"^\s*(?:[-*+]|\d+[\.\)、])\s*(.{2,160})$")
    # Chinese/English numbered stage markers "第一步/阶段一/Step 1/Stage 1".
    stage_re = re.compile(r"(?:第[一二三四五六七八九十0-9]+[步阶段]|Step\s*\d+|Stage\s*\d+)[:：\s]*(.{2,120})", re.I)
    for line in lines[:160]:
        m = step_re.match(line)
        label = m.group(1) if m else None
        if label is None:
            m2 = stage_re.search(line)
            label = m2.group(1) if m2 else None
        if label is None:
            continue
        label = label.strip(" ：:。.")
        if label in seen:
            continue
        seen.add(label)
        ent_id += 1
        ir["entities"].append(
            {
                "id": "s%03d" % ent_id,
                "label": label[:120],
                "type": "step",
                "confidence": "low",
                "source_ref": "auto_heuristic",
            }
        )
    for i in range(max(0, len(ir["entities"]) - 1)):
        ir["relations"].append(
            {
                "from": ir["entities"][i]["id"],
                "to": ir["entities"][i + 1]["id"],
                "kind": "sequence",
                "direction": "forward",
                "source_ref": "auto_heuristic",
            }
        )
    if ent_id:
        ir["notes"].append("auto_extracted skeleton; agent must refine IR before finalizing")
    return ir


# --------------------------------------------------------------------------
# Deterministic layout (pure function of the IR)
# --------------------------------------------------------------------------
def _compute_layers(ir: dict) -> dict:
    """Longest-path layering with cycle back-edges. Deterministic: neighbors
    are visited in stable id order."""
    entities = ensure_list(ir.get("entities"))
    index = {}
    for i, e in enumerate(entities):
        eid = sanitize_id(e.get("id") or e.get("label") or "e%d" % i)
        index[eid] = i
    n = len(entities)
    adj = [[] for _ in range(n)]
    back = []
    for rel in ensure_list(ir.get("relations")):
        f = index.get(rel.get("from"))
        t = index.get(rel.get("to"))
        if f is None or t is None or f == t:
            continue
        if str(rel.get("direction", "")).lower() in ("back", "reverse", "recycle", "feedback", "return"):
            back.append((f, t))
            continue
        adj[f].append(t)
    # Kahn's algorithm on forward edges.
    indeg = [0] * n
    for f in range(n):
        for t in adj[f]:
            indeg[t] += 1
    queue = sorted([i for i in range(n) if indeg[i] == 0])
    layer = [-1] * n
    for i in queue:
        if layer[i] == -1:
            layer[i] = 0
    qi = 0
    while qi < len(queue):
        u = queue[qi]
        qi += 1
        for v in sorted(adj[u]):
            layer[v] = max(layer[v], layer[u] + 1)
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
    # Remaining nodes belong to cycles; assign their reachable layer + 1.
    for u in range(n):
        if layer[u] == -1:
            layer[u] = 0
            for v in adj[u]:
                layer[v] = max(layer[v], 0 + 1)
    # Propagate any remaining -1s to 0.
    layer = [max(0, x) for x in layer]
    # Count per layer, then place each node within its layer.
    counts = {}
    pos_in_layer = {}
    order = sorted(range(n), key=lambda i: (layer[i], entities[i].get("label", "").lower(), i))
    for i in order:
        ly = layer[i]
        pos_in_layer[i] = counts.get(ly, 0)
        counts[ly] = counts.get(ly, 0) + 1
    return {"layer": layer, "counts": counts, "pos": pos_in_layer, "back": back}


def compute_positions(ir: dict) -> dict:
    """Assign x/y for every entity. Explicit coordinates win; the rest use the
    deterministic layer layout. Returns a mapping id -> {x,y,w,h}."""
    entities = ensure_list(ir.get("entities"))
    meta = ensure_dict(ir.get("meta"))
    W = float(meta.get("width") or CANVAS["width"])
    H = float(meta.get("height") or CANVAS["height"])
    margin = float(meta.get("margin") or CANVAS["margin"])
    gx = float(meta.get("grid_x") or CANVAS["grid_x"])
    gy = float(meta.get("grid_y") or CANVAS["grid_y"])
    lay = _compute_layers(ir)
    positions = {}
    for i, ent in enumerate(entities):
        eid = sanitize_id(ent.get("id") or ent.get("label") or "e%d" % i)
        label = str(ent.get("label") or ent.get("id") or "?")
        w = _node_width(label)
        h = _node_height(label)
        if ent.get("x") is not None and ent.get("y") is not None:
            x, y = float(ent.get("x")), float(ent.get("y"))
        else:
            ly = lay["layer"][i]
            col = lay["pos"][i]
            count = lay["counts"].get(ly, 1)
            # Center the row horizontally.
            row_width = count * gx
            x = margin + (W - 2 * margin - row_width) / 2 + col * gx + (gx - w) / 2
            y = margin + ly * gy + (gy - h) / 2
        x = min(max(x, margin), max(margin, W - w - margin))
        y = min(max(y, margin), max(margin, H - h - margin))
        positions[eid] = {"x": x, "y": y, "w": w, "h": h}
    return positions


def _node_width(label: str) -> float:
    # Heuristic only; CJK glyphs are wider than latin ones.
    latin = sum(1 for c in label if ord(c) < 128)
    cjk = len(label) - latin
    return min(340.0, max(120.0, 26 + latin * 9.0 + cjk * 16.0))


def _node_height(label: str) -> float:
    return 48.0 if "\n" not in label else 48.0 + 16.0 * (label.count("\n"))


# --------------------------------------------------------------------------
# SVG building (layered, editable, source-traceable, deterministic)
# --------------------------------------------------------------------------
_ESCAPE = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&apos;",
}


def xml_escape(text) -> str:
    return "".join(_ESCAPE.get(ch, ch) for ch in str(text))


def _rel_style(kind: str) -> tuple:
    kind = (kind or "flow").lower()
    if kind in _LINE_STYLE:
        return _LINE_STYLE[kind]
    for key, val in _LINE_STYLE.items():
        if key in kind:
            return val
    return _LINE_STYLE["flow"]


def build_svg(ir: dict) -> str:
    """IR -> layered editable SVG. The IR is embedded (JSON-in-script) so the
    figure can be reproduced from the SVG alone; every entity/relation carries
    source_ref where available."""
    ir = normalize_ir(ir)
    meta = ir["meta"]
    W = float(meta.get("width") or CANVAS["width"])
    H = float(meta.get("height") or CANVAS["height"])
    margin = float(meta.get("margin") or CANVAS["margin"])
    positions = compute_positions(ir)
    entities = ir["entities"]
    rels = ir["relations"]

    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append(
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" width="%g" height="%g" '
        'viewBox="0 0 %g %g" role="img" aria-label="%s">' % (W, H, W, H, xml_escape(meta.get("title", "diagram")))
    )
    parts.append(
        '  <metadata>%s</metadata>' % xml_escape(
            _json.dumps({"generator": "scix-figure-skill", "version": VERSION, "title": meta.get("title", ""), "case_id": meta.get("case_id", ""), "layout_mode": meta.get("layout_mode", "auto")}, ensure_ascii=False)
        )
    )
    parts.append("  <defs>")
    parts.append('    <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="context-stroke"/></marker>')
    parts.append('    <marker id="arrow-open" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10" fill="none" stroke="context-stroke"/></marker>')
    parts.append("  </defs>")
    parts.append('  <g id="layer-canvas" inkscape:label="canvas"><rect x="0" y="0" width="%g" height="%g" fill="#ffffff" stroke="none"/></g>' % (W, H))

    # Relation layer drawn first (under nodes).
    parts.append('  <g id="layer-relations" inkscape:label="relations">')
    for i, rel in enumerate(rels):
        f = rel.get("from")
        t = rel.get("to")
        if f not in positions or t not in positions:
            continue
        kind = str(rel.get("kind") or "flow")
        stroke, color, width, marker = _rel_style(kind)
        dash = ' stroke-dasharray="6,4"' if stroke == "dashed" else ""
        x1 = positions[f]["x"] + positions[f]["w"] / 2
        y1 = positions[f]["y"] + positions[f]["h"] / 2
        x2 = positions[t]["x"] + positions[t]["w"] / 2
        y2 = positions[t]["y"] + positions[t]["h"] / 2
        # Shift the endpoint onto the node edge along the segment.
        dx, dy = x2 - x1, y2 - y1
        length = (dx * dx + dy * dy) ** 0.5 or 1.0
        inset = positions[t]["w"] / 2 + 4
        ex, ey = x2 - dx / length * inset, y2 - dy / length * inset
        src = xml_escape(str(rel.get("source_ref") or ""))
        lab = xml_escape(str(rel.get("label") or ""))
        data = ' data-kind="%s" data-direction="%s" data-source_ref="%s"' % (
            xml_escape(kind), xml_escape(str(rel.get("direction") or "forward")), src)
        marker_name = marker if marker == "triangle" else "arrow-open"
        parts.append(
            '    <line x1="%g" y1="%g" x2="%g" y2="%g" stroke="%s" stroke-width="%g" marker-end="url(#%s)"%s%s/>'
            % (x1, y1, ex, ey, color, width, marker_name, dash, data)
        )
        if lab:
            mx, my = (x1 + ex) / 2, (y1 + ey) / 2 - 8
            parts.append('    <text x="%g" y="%g" font-size="12" fill="%s" text-anchor="middle">%s</text>' % (mx, my, color, lab))
    parts.append("  </g>")

    # Entity layer.
    parts.append('  <g id="layer-entities" inkscape:label="entities">')
    for i, ent in enumerate(entities):
        eid = sanitize_id(ent.get("id") or ent.get("label") or "e%d" % i)
        p = positions.get(eid)
        if p is None:
            continue
        label = str(ent.get("label") or eid)
        etype = str(ent.get("type") or "component")
        fill = "#eef3fb" if etype in ("step", "stage") else "#f6f8f8"
        stroke = "#2b5b84" if etype in ("step", "stage") else "#555555"
        src = xml_escape(str(ent.get("source_ref") or ""))
        conf = xml_escape(str(ent.get("confidence") or ""))
        rx, ry = 10, 10
        parts.append(
            '    <g id="entity-%s" class="entity" data-type="%s" data-source_ref="%s" data-confidence="%s">'
            % (eid, xml_escape(etype), src, conf)
        )
        parts.append(
            '      <rect x="%g" y="%g" width="%g" height="%g" rx="%g" ry="%g" fill="%s" stroke="%s" stroke-width="1.5"/>'
            % (p["x"], p["y"], p["w"], p["h"], rx, ry, fill, stroke)
        )
        parts.append(
            '      <text x="%g" y="%g" font-family="Noto Sans CJK SC, Microsoft YaHei, PingFang SC, Arial, sans-serif" font-size="13" text-anchor="middle">%s</text>'
            % (p["x"] + p["w"] / 2, p["y"] + p["h"] / 2 + 4.5, xml_escape(label))
        )
        if ent.get("note"):
            parts.append('      <text x="%g" y="%g" font-size="10" fill="#777" text-anchor="middle">%s</text>' % (p["x"] + p["w"] / 2, p["y"] + p["h"] - 6, xml_escape(str(ent.get("note"))[:60])))
        parts.append("    </g>")
    parts.append("  </g>")

    # Legend layer.
    parts.append('  <g id="layer-legend" inkscape:label="legend">')
    legends = ir["legend_annotations"]
    lx = margin
    ly = H - margin - 16
    if legends:
        parts.append('    <rect x="%g" y="%g" width="260" height="%g" rx="6" fill="#ffffff" stroke="#cccccc" stroke-width="1"/>' % (lx - 8, ly - 22, 12 + len(legends) * 22))
    for j, lg in enumerate(legends[:40]):
        key = str(lg.get("key") or lg.get("label") or "item")
        label = str(lg.get("label") or key)
        color = str(lg.get("color") or "#555555")
        shape = str(lg.get("shape") or "line")
        yy = ly + j * 22
        if shape in ("swatch", "box"):
            parts.append('    <rect x="%g" y="%g" width="12" height="12" fill="%s" stroke="#333"/>' % (lx, yy - 9, color))
        else:
            parts.append('    <line x1="%g" y1="%g" x2="%g" y2="%g" stroke="%s" stroke-width="2.5"/>' % (lx, yy - 3, lx + 18, yy - 3, color))
        parts.append('    <text x="%g" y="%g" font-size="12" fill="#222">%s</text>' % (lx + 26, yy, xml_escape(label)))
    parts.append("  </g>")

    # Annotations layer (data_encoding + uncertainty text rendered honestly).
    parts.append('  <g id="layer-annotations" inkscape:label="annotations">')
    for j, de in enumerate(ir["data_encoding"][:20]):
        field = str(de.get("field") or de.get("parameter") or "value")
        rule = str(de.get("rule") or de.get("encoding") or "")
        src = xml_escape(str(de.get("source_ref") or ""))
        txt = "%s -> %s" % (field, rule) if rule else field
        parts.append(
            '    <text x="%g" y="%g" font-size="11" fill="#555" data-source_ref="%s">%s</text>'
            % (margin, margin + 14 + j * 15, src, xml_escape(txt))
        )
    for j, u in enumerate(ir["uncertainty"][:10]):
        txt = str(u.get("item") or u.get("detail") or u)
        if not isinstance(u, dict):
            txt = str(u)
        parts.append('    <text x="%g" y="%g" font-size="10" fill="#b06c00" font-style="italic">⚠ %s</text>' % (W - margin - 360, margin + 14 + j * 15, xml_escape(txt)[:80]))
    parts.append("  </g>")

    # Reproducibility: embed the IR (escaped) as script JSON.
    ir_json = _json.dumps(ir, ensure_ascii=False, sort_keys=True)
    ir_json = ir_json.replace("</", "<\\/")
    parts.append('  <script id="ir-json" type="application/json">%s</script>' % ir_json)

    parts.append("</svg>")
    return "\n".join(parts)


def svg_overflow(ir: dict) -> list:
    """Return out-of-canvas entities (id, edge) for validation."""
    meta = ensure_dict(ir.get("meta"))
    W = float(meta.get("width") or CANVAS["width"])
    H = float(meta.get("height") or CANVAS["height"])
    pos = compute_positions(ir)
    out = []
    for eid, p in pos.items():
        if p["x"] < 0 or p["y"] < 0 or p["x"] + p["w"] > W or p["y"] + p["h"] > H:
            out.append({"id": eid, "x": round(p["x"], 1), "y": round(p["y"], 1), "w": round(p["w"], 1), "h": round(p["h"], 1)})
    return out


# --------------------------------------------------------------------------
# Validation (IR + SVG) -> structured checks
# --------------------------------------------------------------------------
def validate_ir(ir: dict) -> dict:
    checks = []
    entities = ensure_list(ir.get("entities"))
    rels = ensure_list(ir.get("relations"))
    ids = set()
    for i, e in enumerate(entities):
        eid = sanitize_id(e.get("id") or e.get("label") or "e%d" % i)
        ids.add(eid)
    def add(level, code, msg):
        checks.append({"level": level, "code": code, "message": msg})

    if not entities:
        add("warning", "no_entities", "IR has no entities; figure will be empty")
    for i, rel in enumerate(rels):
        f = rel.get("from")
        t = rel.get("to")
        if not f or not t:
            add("error", "relation_endpoint_missing", "relation[%d] missing from/to" % i)
            continue
        if f not in ids:
            add("error", "relation_from_missing", "relation[%d] from=%r does not exist" % (i, f))
        if t not in ids:
            add("error", "relation_to_missing", "relation[%d] to=%r does not exist" % (i, t))
        if f == t:
            add("warning", "self_loop", "relation[%d] is a self loop" % i)
        if not rel.get("source_ref"):
            add("warning", "relation_no_source_ref", "relation[%d] missing source_ref" % i)
    for i, e in enumerate(entities):
        if not e.get("source_ref"):
            add("warning", "entity_no_source_ref", "entity[%d] (%s) missing source_ref" % (i, e.get("label", "?")))
    # Legend/data_encoding consistency is advisory, not blocking.
    legend_keys = {lg.get("key") or lg.get("label") for lg in ensure_list(ir.get("legend_annotations")) if lg}
    de_fields = {d.get("field") or d.get("parameter") for d in ensure_list(ir.get("data_encoding")) if d}
    if de_fields and not legend_keys:
        add("warning", "no_legend", "data_encoding present but no legend_annotations")
    overflow = svg_overflow(ir)
    if overflow:
        add("error", "canvas_overflow", "%d entity(s) outside canvas: %s" % (len(overflow), ", ".join(o["id"] for o in overflow[:5])))
    checks.append({"level": "info", "code": "counts", "message": "entities=%d relations=%d legend=%d encoding=%d" % (len(entities), len(rels), len(ensure_list(ir.get("legend_annotations"))), len(ensure_list(ir.get("data_encoding"))))})
    return {"checks": checks, "errors": sum(1 for c in checks if c["level"] == "error"), "warnings": sum(1 for c in checks if c["level"] == "warning"), "overflow": overflow}


def validate_svg(svg_text: str, expected_entities: int = 0) -> dict:
    """Parse the SVG and check the six layers plus reproducible IR embed."""
    checks = []
    if not svg_text or not svg_text.strip():
        return {"checks": [{"level": "error", "code": "empty_svg", "message": "SVG text is empty"}], "errors": 1, "warnings": 0, "parsed": False}
    try:
        root = _ET.fromstring(svg_text)
    except _ET.ParseError as exc:
        return {"checks": [{"level": "error", "code": "svg_parse_error", "message": "SVG parse error: %s" % exc}], "errors": 1, "warnings": 0, "parsed": False}
    if not root.tag.endswith("svg"):
        return {"checks": [{"level": "error", "code": "not_svg", "message": "root tag is %r" % root.tag}], "errors": 1, "warnings": 0, "parsed": False}
    groups = {g.get("id") for g in root.iter() if g.tag.endswith("g") and g.get("id")}
    for lid in ("layer-relations", "layer-entities", "layer-annotations", "layer-legend"):
        if lid not in groups:
            checks.append({"level": "warning", "code": "missing_layer", "message": "layer %s missing" % lid})
    if root.find(".//{*}script[@id='ir-json']") is None:
        checks.append({"level": "warning", "code": "missing_ir_embed", "message": "IR not embedded in SVG"})
    entity_groups = [g for g in root.iter() if g.tag.endswith("g") and g.get("class") == "entity"]
    if expected_entities and len(entity_groups) != expected_entities:
        checks.append({"level": "warning", "code": "entity_count_mismatch", "message": "expected %d entities in SVG, found %d" % (expected_entities, len(entity_groups))})
    return {"checks": checks, "errors": 0, "warnings": len([c for c in checks if c["level"] == "warning"]), "parsed": True}


# --------------------------------------------------------------------------
# Rendering (optional, honest about failures)
# --------------------------------------------------------------------------
def render_preview(svg_path: str, out_dir: str) -> dict:
    """Attempt PNG/PDF export via available tools; never fabricates success.
    Returns a list of export descriptors for result.json."""
    exports = []
    if not svg_path or not os.path.exists(svg_path):
        return [{"path": None, "format": "png", "status": "skipped", "reason": "no_svg"}]
    candidates = {
        "png": [["inkscape", "--export-type=png", "--export-filename"], ["rsvg-convert", "-o"], ["convert", "-density", "150"]],
        "pdf": [["inkscape", "--export-type=pdf", "--export-filename"], ["rsvg-convert", "-f", "pdf", "-o"]],
    }
    found_any = False
    for fmt, cmds in candidates.items():
        target = os.path.join(out_dir, "figure." + fmt)
        done = False
        for cmd in cmds:
            exe = shutil.which(cmd[0])
            if not exe:
                continue
            # inkscape syntax: inkscape --export-type=png --export-filename=out.svg
            args = [exe]
            if cmd[0] == "inkscape":
                args += ["--export-type=" + fmt, "--export-filename=" + target, svg_path]
            elif cmd[0] == "rsvg-convert":
                args += (["-f", fmt, "-o", target, svg_path] if fmt == "pdf" else ["-o", target, svg_path])
            elif cmd[0] == "convert":
                args += ["-density", "150", svg_path, target]
            try:
                proc = subprocess.run(args, capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace")
                if proc.returncode == 0 and os.path.exists(target) and os.path.getsize(target) > 0:
                    exports.append({"path": target, "format": fmt, "status": "ok", "renderer": cmd[0]})
                    found_any = True
                    done = True
                    break
                else:
                    # Try next renderer for this format.
                    continue
            except (OSError, subprocess.TimeoutExpired):
                continue
        if not done:
            exports.append({"path": None, "format": fmt, "status": "skipped", "reason": "no_renderer_or_failed"})
    if not found_any:
        exports = [{"path": None, "format": "png", "status": "skipped", "reason": "no_render_tool"}]
    return exports


def discover_fonts() -> list:
    """Return installed CJK-capable font candidates; empty list = unknown."""
    fonts = []
    for path in ("/usr/share/fonts", "/usr/local/share/fonts", "/Library/Fonts", r"C:\Windows\Fonts"):
        if os.path.isdir(path):
            try:
                for name in os.listdir(path):
                    if re.search(r"(Noto|YaHei|PingFang|SimSun|Song|Hei|WenQuanYi|SourceHan|CJK|SimHei)", name, re.I):
                        fonts.append(os.path.join(path, name))
            except OSError:
                pass
    return sorted(fonts)[:20]


# --------------------------------------------------------------------------
# Result assembly
# --------------------------------------------------------------------------
def skeleton(case_id: str, title: str, contract: dict, status: str = "in_progress") -> dict:
    return {
        "case_id": case_id,
        "title": title,
        "status": status,
        "output_contract": OrderedDict(contract),
        # Mirrored top-level default fields for graders that read top level.
        "entities": [],
        "relations": [],
        "data_encoding": [],
        "legend_annotations": [],
        "editable_sources": [],
        "exports": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "checks": [],
        "artifacts": [],
        "change_log": [],
        "runtime": {"started": utcnow(), "finished": None, "seconds": None},
    }


def merge_contract(result: dict, ir: dict, svg_path: str, svg_name: str, svg_bytes: int, exports: list, checks: list) -> dict:
    """Fill the six contract sections from the IR and artifacts."""
    meta = ensure_dict(ir.get("meta"))
    entities = ensure_list(ir.get("entities"))
    rels = ensure_list(ir.get("relations"))
    result["entities"] = entities
    result["relations"] = rels
    result["data_encoding"] = ensure_list(ir.get("data_encoding"))
    result["legend_annotations"] = ensure_list(ir.get("legend_annotations"))
    result["sources"] = ensure_list(ir.get("sources"))
    result["uncertainty"] = ensure_list(ir.get("uncertainty"))
    result["editable_sources"] = [
        {
            "path": svg_path if svg_path else None,
            "name": svg_name,
            "format": "svg",
            "layers": ["canvas", "relations", "entities", "legend", "annotations"],
            "bytes": svg_bytes,
            "reproducible": "ir-json embedded in <script id='ir-json'>",
            "notes": "editable layered SVG; source_ref data attributes on entities/relations",
        }
    ] if svg_path else []
    result["exports"] = exports
    result["checks"] = checks
    # Top-level contract mirror: copy the six standard keys back to the
    # output_contract dict, and leave unknown contract keys untouched.
    contract = result.get("output_contract") or {}
    for key in DEFAULT_CONTRACT:
        if key in contract:
            contract[key] = result[key]
        else:
            contract[key] = result[key]
    result["output_contract"] = contract
    result["artifacts"] = []
    if svg_path:
        result["artifacts"].append({"path": svg_path, "format": "svg", "role": "editable_source"})
    for ex in exports:
        if ex.get("path"):
            result["artifacts"].append({"path": ex["path"], "format": ex["format"], "role": "rendered_preview", "status": ex.get("status")})
    result["meta"] = {"title": meta.get("title"), "kind": meta.get("kind"), "layout_mode": meta.get("layout_mode")}
    return result


def finalize(result: dict, status: str, errors: list = None, degradations: list = None, pending: list = None) -> dict:
    if status in STATUSES:
        result["status"] = status
    else:
        result["status"] = "partial"
    if errors is not None:
        result["errors"] = ensure_list(errors)
    if degradations is not None:
        result["degradations"] = ensure_list(degradations)
    if pending is not None:
        result["pending_human"] = ensure_list(pending)
    started = result.get("runtime", {}).get("started")
    result["runtime"]["finished"] = utcnow()
    if started:
        try:
            t0 = _dt.datetime.fromisoformat(started.replace("Z", "+00:00"))
            result["runtime"]["seconds"] = max(0, int((_dt.datetime.now(_dt.timezone.utc) - t0).total_seconds()))
        except (ValueError, TypeError):
            result["runtime"]["seconds"] = None
    append_change(result, "finalize", "status=%s" % result["status"])
    return result


def write_json(path: str, obj) -> str:
    """Write UTF-8 JSON atomically (tmp+replace). Returns the path on success,
    raises only on genuinely unwritable targets."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        _json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path
