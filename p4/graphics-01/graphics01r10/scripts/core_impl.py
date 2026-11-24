#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-figure-authoring core library (standard library only).

Single source of truth: ``figure_ir.json`` (FigureIR). This module provides:

  * input discovery (UTF-8-sig / UTF-8 / GB18030 tolerant, 2MB size cap)
  * IR schema validation + evidence-completeness + legend-data consistency
  * deterministic layered-SVG rendering derived from the IR
  * best-effort PNG/PDF export via detected external renderers
  * result.json derivation (provenance / sources derive from IR evidence)

Design intent (differentiation): rendering is gated behind validation, and
legend items are cross-checked against the distinct values they claim to
map, so a wrong "抑制/激活" marker or a legend that drifts from the data is
caught before export rather than after. Layout is deterministic: no RNG,
no wall-clock dependence, stable output across repeated runs.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from collections import deque
from datetime import datetime, timezone
from xml.sax.saxutils import escape, quoteattr

VERSION = "2.0.0"
MAX_MATERIAL_BYTES = 2 * 1024 * 1024
DEFAULT_CANVAS = {
    "width": 1600,
    "height": 1100,
    "font_family": "DejaVu Sans, Arial, sans-serif",
    "dpi": 300,
}
RELATION_TYPES = {
    "activates", "inhibits", "contains", "part_of", "flows_to",
    "causes", "leads_to", "temporal", "parallel", "maps_to",
}
CONFIDENCE_LEVELS = {"high", "medium", "low", "agent_inferred"}

# --------------------------------------------------------------------------
# time / io helpers
# --------------------------------------------------------------------------

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _decode(data):
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def read_text(path, max_bytes=MAX_MATERIAL_BYTES):
    """Read a text file tolerantly; return '' on any failure."""
    try:
        with open(path, "rb") as f:
            data = f.read(max_bytes)
        truncated = os.path.getsize(path) > max_bytes
        text = _decode(data)
        if truncated:
            text += "\n[truncated: file exceeds %d bytes]" % max_bytes
        return text
    except OSError:
        return ""


def load_json(path, default=None):
    try:
        with open(path, "rb") as f:
            data = f.read()
        return json.loads(_decode(data))
    except Exception:
        return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return path


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


# --------------------------------------------------------------------------
# input discovery
# --------------------------------------------------------------------------

def discover_input(input_dir):
    """Scan input dir without assuming fixed names. Returns
    (case_path, task_path, material_paths)."""
    case_path = None
    task_path = None
    materials = []
    if not os.path.isdir(input_dir):
        return None, None, []
    for root, _dirs, files in os.walk(input_dir):
        for fn in sorted(files):
            p = os.path.join(root, fn)
            low = fn.lower()
            if low == "case.json":
                case_path = p
            elif low in ("task.md", "task.txt"):
                task_path = p
            else:
                try:
                    if os.path.getsize(p) <= MAX_MATERIAL_BYTES:
                        materials.append(p)
                except OSError:
                    pass
    return case_path, task_path, materials


def extract_case_id(case):
    if not isinstance(case, dict):
        return "unknown"
    for key in ("case_id", "caseId", "id", "challenge_id"):
        v = case.get(key)
        if isinstance(v, (str, int)) and str(v).strip():
            return str(v).strip()
    return "unknown"


# --------------------------------------------------------------------------
# skeleton + IR template
# --------------------------------------------------------------------------

def skeleton_result(case_id, case_found, output_dir):
    reason = "输入解析完成，等待实体关系抽取" if case_found else "未发现 /input/case.json"
    return {
        "case_id": case_id,
        "status": "partial" if case_found else "failed",
        "editable_source_files": [],
        "export_files": [],
        "provenance": [],
        "sources": [],
        "uncertainty": [
            {"item": "骨架", "reason": reason, "impact": "尚无图稿", "mitigation": "按 SKILL.md 执行阶段 1-3"}
        ],
        "pending_human": [],
        "change_log": [
            {"time": now_iso(), "action": "skeleton", "detail": reason}
        ],
        "runtime": {"tool_version": VERSION, "started": now_iso()},
    }


def template_ir(case_id):
    """Empty FigureIR schema the agent fills in. A template, not an answer."""
    return {
        "meta": {
            "case_id": case_id,
            "figure_type": "mechanism|flow|hierarchy|chart",
            "title": "",
            "canvas": DEFAULT_CANVAS,
            "auto_legend": True,
        },
        "entities": [
            {
                "id": "e1",
                "label": "",
                "kind": "protein|complex|process|condition|group|other",
                "group": "",
                "attributes": {},
                "evidence": {"source_ref": "", "locator": "", "quote": "", "confidence": "high|medium|low|agent_inferred"},
            }
        ],
        "relations": [
            {
                "id": "r1",
                "from": "e1",
                "to": "e2",
                "type": "activates|inhibits|contains|part_of|flows_to|causes|leads_to|temporal|parallel",
                "label": "",
                "color": "#333333",
                "evidence": {"source_ref": "", "locator": "", "quote": "", "confidence": ""},
            }
        ],
        "groups": [
            {"id": "g1", "label": "", "kind": "container|compartment|panel", "members": [], "evidence": {"source_ref": "", "locator": "", "quote": "", "confidence": ""}}
        ],
        "legend": [
            {"item_id": "lg1", "label": "", "color": "#333333", "marker": "arrow|tee|line|shape", "maps_to": "relation.type|entity.kind", "maps_value": "", "evidence": {"source_ref": "", "locator": "", "quote": "", "confidence": ""}}
        ],
        "annotations": [
            {"id": "a1", "text": "", "target": "", "unit": "", "evidence": {"source_ref": "", "locator": "", "quote": "", "confidence": ""}}
        ],
    }


# --------------------------------------------------------------------------
# validation (runs BEFORE render)
# --------------------------------------------------------------------------

def validate_ir(ir):
    """Return (errors, warnings). Hard errors block 'complete' status;
    warnings are recorded in uncertainty but do not block rendering."""
    errors = []
    warnings = []
    if not isinstance(ir, dict):
        return [{"item": "ir", "reason": "figure_ir.json 必须是 JSON 对象"}], []

    entities = ir.get("entities", [])
    relations = ir.get("relations", [])
    legend = ir.get("legend", [])
    groups = ir.get("groups", [])
    annotations = ir.get("annotations", [])

    if not entities:
        errors.append({"item": "entities", "reason": "entities 为空，无法成图"})

    eids = {}
    for e in entities:
        eid = e.get("id")
        if not eid:
            errors.append({"item": "entity", "reason": "存在无 id 的实体"})
            continue
        if eid in eids:
            errors.append({"item": eid, "reason": "实体 id 重复"})
        eids[eid] = e
        if not str(e.get("label", "")).strip():
            warnings.append({"item": eid, "reason": "实体缺 label"})
        ev = e.get("evidence") or {}
        _evidence_check(warnings, eid, ev)

    for r in relations:
        rid = r.get("id", "?")
        if r.get("from") not in eids:
            errors.append({"item": rid, "reason": "relation.from 指向不存在的实体: %s" % r.get("from")})
        if r.get("to") not in eids:
            errors.append({"item": rid, "reason": "relation.to 指向不存在的实体: %s" % r.get("to")})
        rtype = r.get("type", "")
        if rtype not in RELATION_TYPES:
            warnings.append({"item": rid, "reason": "relation.type 非受控词: %r" % rtype})
        ev = r.get("evidence") or {}
        _evidence_check(warnings, rid, ev)

    gids = set()
    for g in groups:
        gid = g.get("id", "?")
        if not gid or gid in gids:
            errors.append({"item": gid or "group", "reason": "group id 缺失或重复"})
        gids.add(gid)

    for a in annotations:
        aid = a.get("id", "?")
        tgt = a.get("target", "")
        if tgt and tgt not in eids:
            warnings.append({"item": aid, "reason": "annotation.target 指向不存在的实体: %s" % tgt})
        ev = a.get("evidence") or {}
        _evidence_check(warnings, aid, ev)

    for l in legend:
        lid = l.get("item_id", "?")
        ev = l.get("evidence") or {}
        _evidence_check(warnings, lid, ev)

    return errors, warnings


def _evidence_check(warnings, item_id, ev):
    quote = str(ev.get("quote", "") or "").strip()
    locator = str(ev.get("locator", "") or "").strip()
    confidence = str(ev.get("confidence", "") or "").strip()
    if not quote and not locator:
        warnings.append({"item": item_id, "reason": "证据缺失：无 locator 且无逐字 quote（推断项请 confidence=agent_inferred）"})
    if confidence and confidence not in CONFIDENCE_LEVELS:
        warnings.append({"item": item_id, "reason": "confidence 非受控值: %r" % confidence})


# --------------------------------------------------------------------------
# legend-data consistency (validation order step 2)
# --------------------------------------------------------------------------

def _distinct_field_values(ir, field_path):
    parts = str(field_path).split(".")
    if len(parts) != 2:
        return None
    coll, attr = parts
    if coll == "relation":
        coll = "relations"
    if coll == "entity":
        coll = "entities"
    items = ir.get(coll, [])
    vals = set()
    for it in items:
        v = it.get(attr)
        if isinstance(v, (str, int, float)):
            vals.add(str(v))
    return vals


def legend_consistency(ir):
    """Cross-check legend against the data it claims to map."""
    errors = []
    warnings = []
    legend = ir.get("legend", [])
    if not legend:
        warnings.append({"item": "legend", "reason": "无图例项；若图使用关系类型或实体分类，应提供与数据同源的图例"})
        return errors, warnings

    seen_colors = {}
    mapped = {}
    for it in legend:
        lid = it.get("item_id", "?")
        maps_to = it.get("maps_to", "")
        maps_value = it.get("maps_value", "")
        color = str(it.get("color", "") or "")

        if not maps_to:
            warnings.append({"item": lid, "reason": "图例缺 maps_to（未声明映射目标）"})
        else:
            distinct = _distinct_field_values(ir, maps_to)
            if distinct is None:
                errors.append({"item": lid, "reason": "maps_to 字段路径无效: %r" % maps_to})
            else:
                mapped.setdefault(maps_to, set()).add(str(maps_value))
                if maps_value and maps_value not in distinct:
                    warnings.append({"item": lid, "reason": "图例值 %r 不在数据字段 %s 的实际取值 %s 中" % (maps_value, maps_to, sorted(distinct))})

        if color:
            if color in seen_colors and seen_colors[color] != str(it.get("label", "")):
                warnings.append({"item": lid, "reason": "颜色 %s 被多个图例项复用且含义不同" % color})
            seen_colors[color] = str(it.get("label", ""))

    # every distinct value should have a legend entry (when maps_value declared)
    for field_path, vals in mapped.items():
        distinct = _distinct_field_values(ir, field_path)
        if distinct is not None:
            missing = distinct - vals - {""}
            if missing:
                warnings.append({"item": "legend", "reason": "字段 %s 的取值 %s 缺少对应图例项" % (field_path, sorted(missing))})

    return errors, warnings


# --------------------------------------------------------------------------
# deterministic layout
# --------------------------------------------------------------------------

def est_text_width(text, font_size=15.0):
    w = 0.0
    for ch in str(text):
        o = ord(ch)
        if o > 0x2E7F:  # CJK / wide
            w += font_size * 1.0
        elif ch in "MW@":
            w += font_size * 0.8
        else:
            w += font_size * 0.55
    return w


def _topo_levels(entities, relations):
    ids = [e.get("id") for e in entities if e.get("id")]
    idx = {i: n for n, i in enumerate(ids)}
    indeg = {i: 0 for i in ids}
    adj = {i: [] for i in ids}
    for r in relations:
        f, t = r.get("from"), r.get("to")
        if f in idx and t in idx and f != t:
            adj[f].append(t)
            indeg[t] += 1
    level = {i: 0 for i in ids}
    q = deque([i for i in ids if indeg[i] == 0])
    seen = 0
    while q:
        i = q.popleft()
        seen += 1
        for j in adj[i]:
            level[j] = max(level[j], level[i] + 1)
            indeg[j] -= 1
            if indeg[j] == 0:
                q.append(j)
    if seen < len(ids):  # cycle: fall back to insertion order columns
        for n, i in enumerate(ids):
            level[i] = n % 4
    return level, ids


def compute_layout(ir):
    canvas = dict(DEFAULT_CANVAS)
    canvas.update((ir.get("meta") or {}).get("canvas", {}) or {})
    W = int(canvas.get("width", 1600))
    H = int(canvas.get("height", 1100))
    entities = ir.get("entities", [])
    relations = ir.get("relations", [])
    levels, _ = _topo_levels(entities, relations)

    bylevel = {}
    for e in entities:
        bylevel.setdefault(levels.get(e.get("id", ""), 0), []).append(e)

    margin = 110
    node_h = 72
    hgap = 150
    vgap = 74
    positions = {}
    for lvl in sorted(bylevel):
        es = bylevel[lvl]
        x = margin + lvl * hgap
        total = len(es) * node_h + (len(es) - 1) * vgap
        y0 = margin + max(0, (H - 2 * margin - total) // 2)
        for k, e in enumerate(es):
            y = y0 + k * (node_h + vgap)
            lab = str(e.get("label", ""))
            w = max(140, min(300, int(est_text_width(lab, 15.0)) + 44))
            positions[e.get("id")] = {"x": x, "y": y, "w": w, "h": node_h}

    max_x = max([p["x"] + p["w"] for p in positions.values()], default=0) + margin
    max_y = max([p["y"] + p["h"] for p in positions.values()], default=0) + margin + 150
    W = max(W, max_x)
    H = max(H, max_y)
    return positions, W, H


def _group_boxes(ir, positions):
    boxes = []
    for g in ir.get("groups", []):
        gid = g.get("id", "")
        member_ids = set(g.get("members", []) or [])
        for e in ir.get("entities", []):
            if e.get("group") == gid:
                member_ids.add(e.get("id"))
        ps = [positions[m] for m in member_ids if m in positions]
        if not ps:
            continue
        x0 = min(p["x"] for p in ps) - 28
        y0 = min(p["y"] for p in ps) - 40
        x1 = max(p["x"] + p["w"] for p in ps) + 28
        y1 = max(p["y"] + p["h"] for p in ps) + 24
        boxes.append({"id": gid, "label": g.get("label", ""), "kind": g.get("kind", "container"),
                      "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0})
    return boxes


# --------------------------------------------------------------------------
# SVG rendering
# --------------------------------------------------------------------------

def _marker_id(color, kind):
    h = re.sub(r"[^0-9a-fA-F]", "", str(color))
    return "%s-%s" % (kind, (h or "333333")[:6])


def _build_defs(relations):
    colors = sorted({r.get("color", "#333333") for r in relations if r.get("color")})
    if not colors:
        colors = ["#333333"]
    parts = []
    for c in colors:
        cid = _marker_id(c, "arrow")
        parts.append('<marker id=%s viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill=%s/></marker>' % (quoteattr(cid), quoteattr(c)))
        tid = _marker_id(c, "tee")
        parts.append('<marker id=%s viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M2,1 L2,9" stroke=%s stroke-width="3"/></marker>' % (quoteattr(tid), quoteattr(c)))
    return parts


def _edge_points(p1, p2):
    x1, y1, w1, h1 = p1["x"], p1["y"], p1["w"], p1["h"]
    x2, y2, w2, h2 = p2["x"], p2["y"], p2["w"], p2["h"]
    cx1, cy1 = x1 + w1 / 2.0, y1 + h1 / 2.0
    cx2, cy2 = x2 + w2 / 2.0, y2 + h2 / 2.0
    if abs(cx2 - cx1) >= abs(cy2 - cy1):
        if cx2 >= cx1:
            return (x1 + w1, cy1), (x2, cy2)
        return (x1, cy1), (x2 + w2, cy2)
    if cy2 >= cy1:
        return (cx1, y1 + h1), (cx2, y2)
    return (cx1, y1), (cx2, y2 + h2)


def _wrap_label(label, max_width, font_size):
    if est_text_width(label, font_size) <= max_width:
        return [label]
    lines = []
    cur = ""
    for ch in label:
        if cur and est_text_width(cur + ch, font_size) > max_width:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines[:4]


def render_svg(ir):
    positions, W, H = compute_layout(ir)
    canvas = dict(DEFAULT_CANVAS)
    canvas.update((ir.get("meta") or {}).get("canvas", {}) or {})
    font = canvas.get("font_family", DEFAULT_CANVAS["font_family"])
    entities = ir.get("entities", [])
    relations = ir.get("relations", [])
    groups = ir.get("groups", [])
    legend = ir.get("legend", [])
    annotations = ir.get("annotations", [])
    title = (ir.get("meta") or {}).get("title", "") or ""

    out = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append('<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.0.dtd" width=%s height=%s viewBox="0 0 %d %d" font-family=%s>' % (quoteattr("%dpx" % W), quoteattr("%dpx" % H), W, H, quoteattr(font)))
    out.append('<defs>')
    out.extend(_build_defs(relations))
    out.append('</defs>')
    out.append('<style>.node{fill:#ffffff;stroke:#444444;stroke-width:1.5;}.node-text{font-size:15px;fill:#111111;text-anchor:middle;dominant-baseline:middle;}.edge{stroke:#333333;stroke-width:1.6;fill:none;}.edge-label{font-size:12px;fill:#444444;text-anchor:middle;}.group-box{fill:#f6f8fb;stroke:#9aa7b5;stroke-width:1.4;stroke-dasharray:6 4;}.legend-text{font-size:13px;fill:#111111;}</style>')

    # layer 1: groups (containers)
    out.append('<g inkscape:groupmode="layer" inkscape:label="groups">')
    for b in _group_boxes(ir, positions):
        out.append('<rect class="group-box" x="%d" y="%d" width="%d" height="%d" rx="10"/>' % (b["x"], b["y"], b["w"], b["h"]))
        if b["label"]:
            out.append('<text class="edge-label" x="%d" y="%d">%s</text>' % (b["x"] + 12, b["y"] + 16, escape(b["label"])))
    out.append('</g>')

    # layer 2: edges (relations)
    out.append('<g inkscape:groupmode="layer" inkscape:label="edges">')
    for r in relations:
        f, t = r.get("from"), r.get("to")
        if f not in positions or t not in positions:
            continue
        p1, p2 = _edge_points(positions[f], positions[t])
        color = str(r.get("color", "#333333"))
        rtype = r.get("type", "")
        marker = "arrow" if rtype in ("activates", "causes", "leads_to", "flows_to") else ("tee" if rtype == "inhibits" else "arrow")
        end = 'marker-end="url(#%s)"' % _marker_id(color, marker)
        start = ""
        if r.get("type") == "parallel" or (r.get("bidirectional") is True):
            start = 'marker-start="url(#%s)"' % _marker_id(color, "arrow")
        out.append('<path class="edge" d="M%d,%d L%d,%d" stroke=%s %s %s/>' % (int(p1[0]), int(p1[1]), int(p2[0]), int(p2[1]), quoteattr(color), end, start))
        label = r.get("label") or rtype
        if label:
            mx, my = (p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0 - 8
            out.append('<text class="edge-label" x="%d" y="%d">%s</text>' % (int(mx), int(my), escape(label)))
    out.append('</g>')

    # layer 3: nodes (entities)
    out.append('<g inkscape:groupmode="layer" inkscape:label="nodes">')
    for e in entities:
        eid = e.get("id")
        if eid not in positions:
            continue
        p = positions[eid]
        kind = e.get("kind", "")
        rx = 8 if kind in ("complex", "group") else 4
        out.append('<rect id="node-%s" class="node" x="%d" y="%d" width="%d" height="%d" rx="%d" data-kind=%s/>' % (escape(eid), p["x"], p["y"], p["w"], p["h"], rx, quoteattr(str(kind))))
    out.append('</g>')

    # layer 4: labels (editable text)
    out.append('<g inkscape:groupmode="layer" inkscape:label="labels">')
    for e in entities:
        eid = e.get("id")
        if eid not in positions:
            continue
        p = positions[eid]
        label = str(e.get("label", ""))
        cx = p["x"] + p["w"] / 2.0
        cy = p["y"] + p["h"] / 2.0
        lines = _wrap_label(label, p["w"] - 16, 15.0)
        n = len(lines)
        y_start = cy - (n - 1) * 9
        for i, ln in enumerate(lines):
            out.append('<text class="node-text" x="%d" y="%d" data-entity=%s>%s</text>' % (int(cx), int(y_start + i * 18), quoteattr(eid), escape(ln)))
    out.append('</g>')

    # layer 5: annotations
    out.append('<g inkscape:groupmode="layer" inkscape:label="annotations">')
    for a in annotations:
        tgt = a.get("target", "")
        text = a.get("text", "")
        unit = a.get("unit", "")
        txt = text + ((" " + unit) if unit else "")
        if not txt:
            continue
        x, y = 30, 30
        if tgt in positions:
            p = positions[tgt]
            x, y = p["x"] + p["w"] + 10, p["y"] - 6
        out.append('<text class="edge-label" x="%d" y="%d" data-annotation=%s>%s</text>' % (int(x), int(y), quoteattr(str(a.get("id", ""))), escape(txt)))
    out.append('</g>')

    # layer 6: legend (derived from IR legend array)
    out.append('<g inkscape:groupmode="layer" inkscape:label="legend">')
    if legend:
        lx = 30
        ly = H - 20 - len(legend) * 22
        out.append('<text class="legend-text" x="%d" y="%d" font-weight="bold">Legend</text>' % (lx, ly - 12))
        for i, it in enumerate(legend):
            yy = ly + i * 22
            color = str(it.get("color", "#333333"))
            marker = it.get("marker", "line")
            if marker == "tee":
                out.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke=%s stroke-width="3"/>' % (lx, yy - 4, lx, yy + 4, quoteattr(color)))
            else:
                out.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke=%s stroke-width="2" marker-end="url(#%s)"/>' % (lx, yy, lx + 26, yy, quoteattr(color), _marker_id(color, "arrow")))
            out.append('<text class="legend-text" x="%d" y="%d">%s</text>' % (lx + 38, yy + 4, escape(str(it.get("label", "")))))
    out.append('</g>')

    # layer 7: title
    out.append('<g inkscape:groupmode="layer" inkscape:label="title">')
    if title:
        out.append('<text x="%d" y="34" font-size="20" font-weight="bold" fill="#111111">%s</text>' % (30, escape(title)))
    out.append('</g>')

    out.append('</svg>')
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# export (best effort via detected external renderers)
# --------------------------------------------------------------------------

def _which(name):
    return shutil.which(name)


def _run_ok(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return r.returncode == 0
    except Exception:
        return False


def _try_png(svg_path, png_path, width, height):
    exe = _which("rsvg-convert")
    if exe and _run_ok([exe, "-w", str(width), "-h", str(height), "-o", png_path, svg_path]):
        return True, "rsvg-convert"
    exe = _which("inkscape")
    if exe and _run_ok([exe, svg_path, "--export-filename=" + png_path, "--export-width=" + str(width), "--export-height=" + str(height)]):
        return True, "inkscape"
    if _run_ok([sys.executable, "-c", "import cairosvg; cairosvg.svg2png(url=%r, write_to=%r, output_width=%d, output_height=%d)" % (svg_path, png_path, width, height)]):
        return True, "cairosvg"
    for c in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome", "msedge"):
        exe = _which(c)
        if exe and _run_ok([exe, "--headless", "--disable-gpu", "--hide-scrollbars",
                            "--screenshot=" + png_path, "--window-size=%d,%d" % (width, height), svg_path]):
            return True, c
    return False, None


def _try_pdf(svg_path, pdf_path):
    exe = _which("rsvg-convert")
    if exe and _run_ok([exe, "-f", "pdf", "-o", pdf_path, svg_path]):
        return True, "rsvg-convert"
    exe = _which("inkscape")
    if exe and _run_ok([exe, svg_path, "--export-filename=" + pdf_path]):
        return True, "inkscape"
    if _run_ok([sys.executable, "-c", "import cairosvg; cairosvg.svg2pdf(url=%r, write_to=%r)" % (svg_path, pdf_path)]):
        return True, "cairosvg"
    return False, None


def verify_export(path, fmt):
    if not os.path.exists(path):
        return False, "missing"
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return False, "unreadable"
    if fmt == "png":
        return head[:8] == b"\x89PNG\r\n\x1a\n", "bad PNG magic"
    if fmt == "pdf":
        return head[:5] == b"%PDF-", "bad PDF magic"
    return True, ""


def _export_entry(path, fmt, width, height, canvas, renderer):
    ok, reason = verify_export(path, fmt)
    entry = {
        "path": os.path.basename(path),
        "format": fmt,
        "width_px": width,
        "height_px": height,
        "dpi": canvas.get("dpi", 300),
        "color_space": "RGB",
        "font_check": "embedded" if fmt == "png" else "unknown",
        "renderer": renderer,
        "ok": ok,
        "note": reason if not ok else ("rasterized by %s" % renderer),
    }
    return entry


def try_export(svg_path, output_dir, name, canvas):
    width = int(canvas.get("width", 1600))
    height = int(canvas.get("height", 1100))
    png_path = os.path.join(output_dir, name + ".png")
    pdf_path = os.path.join(output_dir, name + ".pdf")
    exports = []
    ok, renderer = _try_png(svg_path, png_path, width, height)
    if ok:
        exports.append(_export_entry(png_path, "png", width, height, canvas, renderer))
    else:
        exports.append({
            "path": name + ".png", "format": "png", "width_px": width, "height_px": height,
            "dpi": canvas.get("dpi", 300), "color_space": "RGB", "font_check": "none",
            "renderer": None, "ok": False,
            "note": "导出失败：未发现 rsvg-convert/inkscape/cairosvg/chromium，请用环境 office-renderer/browser-automation 导出后重跑",
        })
    ok, renderer = _try_pdf(svg_path, pdf_path)
    if ok:
        exports.append(_export_entry(pdf_path, "pdf", width, height, canvas, renderer))
    else:
        exports.append({
            "path": name + ".pdf", "format": "pdf", "width_px": None, "height_px": None,
            "dpi": canvas.get("dpi", 300), "color_space": "RGB", "font_check": "unknown",
            "renderer": None, "ok": False,
            "note": "PDF 未自动生成：可用 latex-compiler/office-renderer 生成后重跑自检",
        })
    return exports


# --------------------------------------------------------------------------
# result.json derivation
# --------------------------------------------------------------------------

def build_sources(ir, materials):
    srcs = {}
    for coll_key in ("entities", "relations", "legend", "annotations", "groups"):
        for it in ir.get(coll_key, []):
            ev = it.get("evidence") or {}
            ref = str(ev.get("source_ref", "") or "").strip() or "unknown"
            loc = str(ev.get("locator", "") or "").strip()
            s = srcs.setdefault(ref, {"ref": ref, "path": "", "locator": loc, "used_for": []})
            if loc and loc not in s["locator"].split(";"):
                s["locator"] = loc if not s["locator"] else s["locator"] + "; " + loc
            used = it.get("label") or it.get("type") or it.get("text") or it.get("id", "")
            if used and str(used) not in s["used_for"]:
                s["used_for"].append(str(used))
    for ref, s in srcs.items():
        for m in materials:
            if os.path.basename(m) == ref or ref in m or m.endswith(ref):
                s["path"] = m
                break
    return list(srcs.values())


def build_provenance(ir):
    out = []

    def add(item, kind, mapping):
        ev = item.get("evidence") or {}
        quote = str(ev.get("quote", "") or "").strip()
        out.append({
            "element": str(item.get("id") or item.get("item_id") or kind),
            "source_ref": str(ev.get("source_ref", "") or "").strip(),
            "locator": str(ev.get("locator", "") or "").strip(),
            "quote": quote,
            "mapping": mapping,
            "version": str(ev.get("version", "v1")),
            "confirmed": bool(ev.get("confirmed", bool(quote))),
        })

    for e in ir.get("entities", []):
        add(e, "entity", "kind=%s label=%s" % (e.get("kind", ""), e.get("label", "")))
    for r in ir.get("relations", []):
        add(r, "relation", "type=%s %s->%s" % (r.get("type", ""), r.get("from", ""), r.get("to", "")))
    for l in ir.get("legend", []):
        add(l, "legend", "maps_to=%s maps_value=%s" % (l.get("maps_to", ""), l.get("maps_value", "")))
    for a in ir.get("annotations", []):
        add(a, "annotation", a.get("text", ""))
    return out


def _append_unique(obj, key, entry):
    arr = obj.setdefault(key, [])
    if not any(x.get("item") == entry.get("item") for x in arr):
        arr.append(entry)


def _assemble_result(case_id, prev, ir, svg_path, exports, errors, warnings, human, started, case_path, materials):
    human = human if isinstance(human, dict) else {}
    good_exports = [x for x in exports if x.get("ok")]
    if case_path is None:
        status = "failed"
    elif errors:
        status = "partial"
    elif not good_exports:
        status = "partial"
    else:
        status = "complete"

    editable = [{
        "path": os.path.basename(svg_path),
        "kind": "svg",
        "layers": True,
        "editable_text": True,
        "note": "分层可编辑 SVG（groups/edges/nodes/labels/annotations/legend/title 七层）；由 work/figure_ir.json 确定性渲染",
    }]

    uncertainty = []
    for e in errors:
        uncertainty.append({"item": e["item"], "reason": e["reason"], "impact": "status 降为 partial", "mitigation": "修正 figure_ir.json 后重跑 main.py"})
    for w in warnings:
        uncertainty.append({"item": w["item"], "reason": w["reason"], "impact": "图面元素可核验性下降", "mitigation": "补 evidence.locator/quote 或标 confidence=agent_inferred"})
    for u in (human.get("uncertainty") or []):
        uncertainty.append(u)

    pending = list(human.get("pending_human") or [])
    if not good_exports:
        _append_unique({"pending_human": pending}, "pending_human", {
            "item": "导出 PNG/PDF", "why": "自动探测未找到可用渲染器", "suggested_action": "用环境 office-renderer/browser-automation/latex-compiler 导出后重跑自检"})

    change_log = list(prev.get("change_log") or []) if isinstance(prev, dict) else []
    for c in (human.get("change_log") or []):
        change_log.append(c)
    change_log.append({"time": now_iso(), "action": "render_validate",
                       "detail": "rendered=%s errors=%d warnings=%d exports_ok=%d status=%s" % (os.path.basename(svg_path), len(errors), len(warnings), len(good_exports), status)})

    return {
        "case_id": case_id,
        "status": status,
        "editable_source_files": editable,
        "export_files": exports,
        "provenance": build_provenance(ir),
        "sources": build_sources(ir, materials),
        "uncertainty": uncertainty,
        "pending_human": pending,
        "change_log": change_log,
        "runtime": {
            "tool_version": VERSION,
            "started": started,
            "finished": now_iso(),
            "input_case_path": case_path or None,
            "materials": [os.path.basename(m) for m in materials],
            "notes": "state derived from FigureIR; no benchmark score invented",
        },
    }


# --------------------------------------------------------------------------
# pipeline
# --------------------------------------------------------------------------

def run_pipeline(args):
    started = now_iso()
    input_dir, output_dir, work_dir = args.input, args.output, args.work
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    case_path, _task_path, materials = discover_input(input_dir)
    case = load_json(case_path, None) if case_path else None
    case_id = extract_case_id(case)

    result_path = os.path.join(output_dir, "result.json")
    result = load_json(result_path, None)
    if result is None:
        result = skeleton_result(case_id, case_path is not None, output_dir)
        write_json(result_path, result)

    tpl = os.path.join(work_dir, "figure_ir.template.json")
    if not os.path.exists(tpl):
        write_json(tpl, template_ir(case_id))

    ir_path = os.path.join(work_dir, "figure_ir.json")
    ir = load_json(ir_path, None)

    if not isinstance(ir, dict) or not ir.get("entities"):
        result = load_json(result_path, result)
        result["status"] = "partial" if case_path else "failed"
        _append_unique(result, "pending_human", {
            "item": "build figure_ir.json",
            "why": "材料尚未抽取为 FigureIR（work/figure_ir.json 缺失或 entities 为空）",
            "suggested_action": "读 /input 实际材料，按 references/figure-ir-schema.md 填写 work/figure_ir.json 后重跑 main.py",
        })
        result["change_log"] = list(result.get("change_log") or [])
        result["change_log"].append({"time": now_iso(), "action": "await_ir", "detail": "等待实体关系抽取"})
        write_json(result_path, result)
        return {"ok": True, "stage": "awaiting_ir", "case_id": case_id, "result": result_path}

    errors, warnings = validate_ir(ir)
    le, lw = legend_consistency(ir)
    errors += le
    warnings += lw

    svg_path = os.path.join(output_dir, "figure01.svg")
    write_text(svg_path, render_svg(ir))

    canvas = dict(DEFAULT_CANVAS)
    canvas.update((ir.get("meta") or {}).get("canvas", {}) or {})
    exports = try_export(svg_path, output_dir, "figure01", canvas)

    human = load_json(os.path.join(work_dir, "human_notes.json"), {}) or {}
    new_result = _assemble_result(case_id, result, ir, svg_path, exports, errors, warnings, human, started, case_path, materials)
    write_json(result_path, new_result)

    return {
        "ok": new_result["status"] == "complete",
        "stage": "rendered",
        "case_id": case_id,
        "result": result_path,
        "errors": len(errors),
        "warnings": len(warnings),
    }
