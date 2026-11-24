#!/usr/bin/env python3
"""Core implementation for scix-figure-contract-skill.

Responsibilities:
  * discover case.json / task.md / materials under an input directory
  * read the declared output_contract and derive field names
  * manage result.json (init / merge / finalize) with an append-only change_log
  * build a layered, deterministic, editable SVG from a structured IR
  * validate the SVG against the IR (dangling arrows, overflow, evidence coverage)
  * record honest render status without fabricating tool output

Standard library only. All paths are resolved relative to this file's directory,
so the scripts keep working when the Skill is installed at /skills/weekly-skill.
"""

import datetime
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

DEFAULT_CONTRACT_FIELDS = [
    "entities",
    "relations",
    "data_encoding",
    "legend_annotations",
    "editable_sources",
    "exports",
]

STANDARD_FIELDS = [
    "status",
    "title",
    "figure_kind",
    "sources",
    "uncertainty",
    "pending_human",
    "limitations",
    "checks",
    "artifacts",
    "change_log",
    "runtime",
]

STATUSES = {"in_progress", "complete", "partial", "failed"}

# --------------------------------------------------------------------------- #
# Small IO helpers (tolerant of BOM / bad encodings / oversized files)
# --------------------------------------------------------------------------- #

def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_bytes(path, max_bytes=2 * 1024 * 1024):
    with open(path, "rb") as fh:
        data = fh.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("file too large: %s" % path)
    return data


def read_text(path, max_bytes=2 * 1024 * 1024):
    raw = _read_bytes(path, max_bytes)
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def read_json(path, default=None):
    if not os.path.isfile(path):
        return default
    try:
        text = read_text(path)
    except (OSError, ValueError) as exc:
        return {"__read_error__": str(exc)}
    try:
        return json.loads(text)
    except (ValueError, TypeError) as exc:
        return {"__parse_error__": str(exc)}


def write_json(path, obj):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #

def discover(input_dir):
    """Return {case, task, materials} without assuming fixed file names."""
    result = {"case": None, "task": None, "materials": [], "input_dir": input_dir}
    if not os.path.isdir(input_dir):
        return result
    entries = sorted(os.listdir(input_dir))
    for name in entries:
        path = os.path.join(input_dir, name)
        if not os.path.isfile(path):
            continue
        low = name.lower()
        if low in ("case.json", "input.json") and result["case"] is None:
            result["case"] = read_json(path)
        elif low in ("task.md", "task.txt", "README.md") and result["task"] is None:
            result["task"] = read_text(path)
        elif low in ("case.json", "task.md", "task.txt"):
            continue
        else:
            result["materials"].append(path)
    return result


def contract_field_names(case):
    """Return the output_contract field names declared by a case, else defaults."""
    names = None
    if isinstance(case, dict):
        oc = case.get("output_contract")
        if isinstance(oc, dict):
            names = [str(k) for k in oc.keys()]
        elif isinstance(oc, list):
            names = [str(x) for x in oc if isinstance(x, str)]
        elif isinstance(oc, str) and oc.strip():
            names = [oc.strip()]
    if not names:
        names = list(DEFAULT_CONTRACT_FIELDS)
    # de-dup preserving order
    seen, out = set(), []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def case_id_of(case):
    if not isinstance(case, dict):
        return None
    for key in ("case_id", "id", "challenge_id"):
        v = case.get(key)
        if isinstance(v, (str, int)) and str(v).strip():
            return str(v).strip()
    return None


# --------------------------------------------------------------------------- #
# result.json management
# --------------------------------------------------------------------------- #

def _empty_value(field):
    if field in DEFAULT_CONTRACT_FIELDS:
        return []
    return None


class ResultManager:
    """Manage result.json so that it always carries every contract field."""

    def __init__(self, path, case_id="", contract_fields=None, title=""):
        self.path = path
        self.case_id = case_id or ""
        self.contract_fields = contract_fields or list(DEFAULT_CONTRACT_FIELDS)
        self.title = title

    def load(self):
        if os.path.isfile(self.path):
            obj = read_json(self.path)
            if isinstance(obj, dict):
                return obj
        return {}

    def _append_change(self, obj, action, detail):
        log = obj.setdefault("change_log", [])
        if not isinstance(log, list):
            log = obj["change_log"] = []
        log.append({"time": utcnow(), "action": action, "detail": detail})

    def init_skeleton(self):
        obj = {
            "case_id": self.case_id,
            "status": "in_progress",
            "title": self.title,
        }
        for field in self.contract_fields:
            obj[field] = _empty_value(field)
        for field in STANDARD_FIELDS:
            if field == "status":
                continue
            if field == "change_log":
                obj[field] = []
            elif field == "runtime":
                obj[field] = {"started_at": utcnow()}
            elif field in ("sources", "uncertainty", "pending_human",
                           "limitations", "checks", "artifacts"):
                obj[field] = []
            else:
                obj[field] = None
        self._append_change(obj, "init", "落盘 result.json 骨架，契约字段=" + ",".join(self.contract_fields))
        write_json(self.path, obj)
        return obj

    def add_change(self, action, detail=""):
        obj = self.load()
        self._append_change(obj, action, detail)
        write_json(self.path, obj)
        return obj

    def merge(self, partial, keys=None):
        """Merge key-value pairs without ever dropping contract fields."""
        obj = self.load()
        if not obj:
            obj = self.init_skeleton()
        if not isinstance(partial, dict):
            partial = {}
        allowed = None if keys is None else set(keys)
        for k, v in partial.items():
            if allowed is not None and k not in allowed:
                continue
            if k in ("case_id", "status"):
                continue  # managed by init / finalize
            obj[k] = v
        # guarantee every contract field still exists after merge
        for field in self.contract_fields:
            obj.setdefault(field, _empty_value(field))
        self._append_change(obj, "merge", "合并部分结果 keys=" + ",".join(sorted(partial.keys())))
        write_json(self.path, obj)
        return obj

    def finalize(self, status):
        if status not in STATUSES:
            status = "partial"
        obj = self.load()
        if not obj:
            obj = self.init_skeleton()
        obj["status"] = status
        runtime = obj.get("runtime") if isinstance(obj.get("runtime"), dict) else {}
        runtime["finished_at"] = utcnow()
        obj["runtime"] = runtime
        self._append_change(obj, "finalize", "status=" + status)
        write_json(self.path, obj)
        return obj


def validate_contract(result, contract_fields):
    """Return {ok, missing, extra} comparing result.json against contract fields."""
    if not isinstance(result, dict):
        return {"ok": False, "missing": contract_fields, "extra": []}
    missing = [f for f in contract_fields if f not in result]
    required = ["case_id", "status", "sources", "uncertainty", "change_log"]
    missing += [f for f in required if f not in result]
    extra = [k for k in result.keys() if k not in set(contract_fields) | set(required) | set(STANDARD_FIELDS)]
    return {"ok": not missing, "missing": sorted(set(missing)), "extra": extra}


# --------------------------------------------------------------------------- #
# Deterministic layout
# --------------------------------------------------------------------------- #

def _layout(ir):
    """Assign deterministic positions. Explicit x/y win; otherwise auto grid."""
    nodes = ir.get("entities", []) if isinstance(ir, dict) else []
    if not isinstance(nodes, list):
        nodes = []
    placed = []
    todo = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get("id") is None:
            continue
        if isinstance(node.get("x"), (int, float)) and isinstance(node.get("y"), (int, float)):
            placed.append(node)
        else:
            todo.append(node)
    # deterministic order
    todo.sort(key=lambda n: str(n.get("id", "")))
    n = len(todo)
    if n:
        mode = "grid"
        meta = ir.get("meta", {}) if isinstance(ir.get("meta"), dict) else {}
        if meta.get("layout") in ("vertical", "horizontal"):
            mode = meta["layout"]
        w = 180
        h = 64
        gap_x, gap_y = 60, 80
        if mode == "vertical":
            cols = 1
        elif mode == "horizontal":
            cols = n
        else:
            cols = max(1, int(round(n ** 0.5)))
        rows = max(1, -(-n // cols))
        for i, node in enumerate(todo):
            r, c = divmod(i, cols)
            node["x"] = 60 + c * (w + gap_x)
            node["y"] = 60 + r * (h + gap_y)
            node["w"] = node.get("w", w)
            node["h"] = node.get("h", h)
        ir.setdefault("meta", {})["auto_layout"] = mode
    return ir


# --------------------------------------------------------------------------- #
# SVG building
# --------------------------------------------------------------------------- #

_XML_ESCAPE = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"})


def xml_escape(text):
    return str(text).translate(_XML_ESCAPE)


def _svg_rect(attrs):
    return "<rect " + " ".join('%s="%s"' % (k, xml_escape(str(v))) for k, v in attrs.items()) + "/>"


def build_svg(ir, out_path):
    """IR -> layered, editable SVG. Deterministic; never invents content."""
    ir = _layout(ir)
    width = int(ir.get("canvas", {}).get("width", 1200))
    height = int(ir.get("canvas", {}).get("height", 800))
    entities = ir.get("entities", []) or []
    relations = ir.get("relations", []) or []
    legend = ir.get("legend", []) or []
    annotations = ir.get("annotations", []) or []
    title = ir.get("title", "figure")
    font = "'Noto Sans CJK SC','Microsoft YaHei','SimHei',sans-serif"

    idmap = {}
    for e in entities:
        if isinstance(e, dict) and e.get("id") is not None:
            idmap[str(e["id"])] = e

    def center(node):
        x = float(node.get("x", 0))
        y = float(node.get("y", 0))
        w = float(node.get("w", 160))
        h = float(node.get("h", 56))
        return (x + w / 2.0, y + h / 2.0)

    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
                 'font-family="%s" role="img" aria-label="%s">' % (width, height, width, height, font, xml_escape(title)))
    parts.append("<title>%s</title>" % xml_escape(title))
    parts.append("<defs>")
    parts.append('<marker id="arrow-fwd" markerWidth="10" markerHeight="10" refX="8.5" refY="3" orient="auto" markerUnits="strokeWidth">'
                 '<path d="M0,0 L9,3 L0,6 z" fill="#1f2937"/></marker>')
    parts.append('<marker id="arrow-rev" markerWidth="10" markerHeight="10" refX="0.5" refY="3" orient="auto-start-reverse" markerUnits="strokeWidth">'
                 '<path d="M0,0 L9,3 L0,6 z" fill="#1f2937"/></marker>')
    parts.append("</defs>")

    # metadata: evidence map locator -> quote
    meta = {"title": title, "auto_layout": (ir.get("meta") or {}).get("auto_layout"),
            "sources": {}}
    for e in entities:
        if isinstance(e, dict) and e.get("source_ref"):
            meta["sources"][str(e.get("source_ref"))] = e.get("quote", "")
    for r in relations:
        if isinstance(r, dict) and r.get("source_ref"):
            meta["sources"][str(r.get("source_ref"))] = r.get("quote", "")
    parts.append("<metadata>%s</metadata>" % xml_escape(json.dumps(meta, ensure_ascii=False)))

    # layer: relations (drawn first, below entities)
    parts.append('<g id="layer-relations">')
    for r in relations:
        if not isinstance(r, dict):
            continue
        src = idmap.get(str(r.get("from", "")))
        dst = idmap.get(str(r.get("to", "")))
        if src is None or dst is None:
            parts.append('<!-- dangling relation skipped: %s -> %s -->' % (xml_escape(str(r.get("from"))), xml_escape(str(r.get("to")))))
            continue
        x1, y1 = center(src)
        x2, y2 = center(dst)
        style = str(r.get("style", "solid")).lower()
        dash = ' stroke-dasharray="6 5"' if style in ("dashed", "dash", "recycle", "return") else ""
        direction = str(r.get("direction", "forward")).lower()
        end_marker = ""
        start_marker = ""
        if direction in ("bidirectional", "both"):
            end_marker = ' marker-end="url(#arrow-fwd)"'
            start_marker = ' marker-start="url(#arrow-rev)"'
        elif direction in ("reverse", "backward"):
            start_marker = ' marker-start="url(#arrow-rev)"'
        else:
            end_marker = ' marker-end="url(#arrow-fwd)"'
        color = str(r.get("color", "#1f2937"))
        label = r.get("label", "")
        extra = ""
        if r.get("source_ref"):
            extra += ' data-source-ref="%s"' % xml_escape(str(r["source_ref"]))
        if r.get("kind"):
            extra += ' data-kind="%s"' % xml_escape(str(r["kind"]))
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="2"%s%s%s%s/>'
                     % (x1, y1, x2, y2, xml_escape(color), dash, end_marker, start_marker, extra))
        if label:
            parts.append('<text x="%.1f" y="%.1f" text-anchor="middle" font-size="13" fill="#334155">%s</text>'
                         % ((x1 + x2) / 2.0, (y1 + y2) / 2.0 - 6, xml_escape(str(label))))
    parts.append("</g>")

    # layer: entities
    parts.append('<g id="layer-entities">')
    for e in entities:
        if not isinstance(e, dict) or e.get("id") is None:
            continue
        x = float(e.get("x", 0))
        y = float(e.get("y", 0))
        w = float(e.get("w", 160))
        h = float(e.get("h", 56))
        fill = str(e.get("fill", "#eef2f7"))
        stroke = str(e.get("stroke", "#334155"))
        extra = ""
        if e.get("source_ref"):
            extra += ' data-source-ref="%s"' % xml_escape(str(e["source_ref"]))
        if e.get("type"):
            extra += ' data-type="%s"' % xml_escape(str(e["type"]))
        if e.get("step_index") is not None:
            extra += ' data-step-index="%s"' % xml_escape(str(e["step_index"]))
        parts.append('<g id="%s"%s>' % (xml_escape(str(e["id"])), extra))
        parts.append(_svg_rect({"x": x, "y": y, "width": w, "height": h, "rx": 8,
                                "fill": fill, "stroke": stroke, "stroke-width": 1.5}))
        parts.append('<text x="%.1f" y="%.1f" text-anchor="middle" dominant-baseline="middle" font-size="14" fill="#0f172a">%s</text>'
                     % (x + w / 2.0, y + h / 2.0, xml_escape(str(e.get("label", e.get("id", ""))))))
        parts.append("</g>")
    parts.append("</g>")

    # layer: annotations
    parts.append('<g id="layer-annotations">')
    for a in annotations:
        if not isinstance(a, dict):
            continue
        anchor = idmap.get(str(a.get("ref", "")))
        if anchor is None:
            continue
        x, y = center(anchor)
        y = y + float(a.get("dy", 40))
        extra = ""
        if a.get("source_ref"):
            extra += ' data-source-ref="%s"' % xml_escape(str(a["source_ref"]))
        parts.append('<text x="%.1f" y="%.1f" text-anchor="middle" font-size="12" fill="#475569"%s>%s</text>'
                     % (x, y, extra, xml_escape(str(a.get("text", "")))))
    parts.append("</g>")

    # layer: legend
    parts.append('<g id="layer-legend">')
    if legend:
        lx, ly = width - 260, height - 40 - 28 * len(legend)
        parts.append(_svg_rect({"x": lx - 12, "y": ly - 26, "width": 250, "height": 28 * len(legend) + 16,
                                "rx": 6, "fill": "#ffffff", "stroke": "#cbd5e1", "stroke-width": 1}))
        for i, item in enumerate(legend):
            if not isinstance(item, dict):
                continue
            key = str(item.get("key", "?"))
            visual = str(item.get("visual", ""))
            color = "#334155"
            dash = ""
            m = re.search(r"color:\s*(#[0-9a-fA-F]{3,8}|[a-zA-Z]+)", visual)
            if m:
                color = m.group(1)
            if "dash" in visual or "虚线" in visual:
                dash = ' stroke-dasharray="6 5"'
            yy = ly + i * 28
            extra = ""
            if item.get("source_ref"):
                extra += ' data-source-ref="%s"' % xml_escape(str(item["source_ref"]))
            parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-width="2"%s%s/>'
                         % (lx, yy, lx + 26, yy, xml_escape(color), dash, extra))
            parts.append('<text x="%d" y="%d" font-size="12" fill="#334155">%s</text>'
                         % (lx + 34, yy + 4, xml_escape(str(key) + "  " + str(item.get("text", "")))))
    parts.append("</g>")
    parts.append("</svg>")

    text = "\n".join(parts) + "\n"
    parent = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(parent, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return {"path": out_path, "bytes": len(text.encode("utf-8"))}


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

def validate_svg(ir, svg_path, out_checks_path=None):
    checks = {"critical": [], "warnings": [], "pass": []}
    try:
        ET.parse(svg_path)
        checks["pass"].append("svg_xml_well_formed")
    except (ET.ParseError, OSError) as exc:
        checks["critical"].append("svg_not_parseable: %s" % exc)

    if not isinstance(ir, dict):
        checks["critical"].append("ir_not_object")
    entities = ir.get("entities", []) if isinstance(ir, dict) else []
    relations = ir.get("relations", []) if isinstance(ir, dict) else []
    ids = set()
    for e in entities:
        if isinstance(e, dict) and e.get("id") is not None:
            ids.add(str(e["id"]))
    # dangling arrows
    for r in relations:
        if not isinstance(r, dict):
            continue
        f, t = str(r.get("from", "")), str(r.get("to", ""))
        if f and f not in ids:
            checks["critical"].append("dangling_from: %s" % f)
        if t and t not in ids:
            checks["critical"].append("dangling_to: %s" % t)
    # evidence coverage
    for e in entities:
        if isinstance(e, dict) and not e.get("source_ref"):
            checks["warnings"].append("entity_missing_source_ref: %s" % e.get("id"))
        elif isinstance(e, dict) and not e.get("quote"):
            checks["warnings"].append("entity_missing_quote: %s" % e.get("id"))
    for r in relations:
        if isinstance(r, dict) and not r.get("source_ref"):
            checks["warnings"].append("relation_missing_source_ref: %s->%s" % (r.get("from"), r.get("to")))
    # overflow
    canvas = ir.get("canvas", {}) if isinstance(ir, dict) else {}
    width = int(canvas.get("width", 1200))
    height = int(canvas.get("height", 800))
    for e in entities:
        if not isinstance(e, dict):
            continue
        x = float(e.get("x", 0)); y = float(e.get("y", 0))
        w = float(e.get("w", 160)); h = float(e.get("h", 56))
        if x + w > width or y + h > height:
            checks["critical"].append("overflow: %s" % e.get("id"))
    # duplicate ids
    seen, dups = set(), set()
    for e in entities:
        if isinstance(e, dict) and e.get("id") is not None:
            key = str(e["id"])
            if key in seen:
                dups.add(key)
            seen.add(key)
    for d in sorted(dups):
        checks["critical"].append("duplicate_id: %s" % d)

    checks["ok"] = not checks["critical"]
    checks["pass"].append("validation_complete")
    if out_checks_path:
        write_json(out_checks_path, checks)
    return checks


def render_status_notes(available_tools):
    """Honest render guidance; never claims a successful export that did not run."""
    notes = {"render_status": "pending", "notes": []}
    if not available_tools:
        notes["render_status"] = "skipped"
        notes["notes"].append("无可用渲染工具，仅交付 SVG；PNG/PDF 未生成")
    else:
        notes["notes"].append("可用渲染工具: %s" % ", ".join(available_tools))
    return notes


def build_sources(ir):
    """Deduplicate IR evidence into a locator -> quote table for result.json."""
    if not isinstance(ir, dict):
        return []
    table = {}
    for group in ("entities", "relations", "annotations", "legend", "data_encoding", "steps"):
        items = ir.get(group)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            ref = item.get("source_ref")
            if not ref:
                continue
            key = str(ref)
            entry = table.setdefault(key, {"locator": key, "quote": item.get("quote", ""), "used_for": []})
            if group == "relations":
                used = "%s->%s" % (item.get("from", ""), item.get("to", ""))
            else:
                used = item.get("id") or group
            if used not in entry["used_for"]:
                entry["used_for"].append(str(used))
    return sorted(table.values(), key=lambda x: x["locator"])


def build_exports_list(svg_path, render_result=None):
    """Return a minimal exports list consistent with what actually exists."""
    exports = []
    if svg_path and os.path.isfile(svg_path):
        exports.append({
            "path": os.path.basename(svg_path),
            "format": "svg",
            "render_status": "complete",
            "notes": "分层可编辑源文件",
        })
    if render_result:
        for item in render_result:
            if isinstance(item, dict) and item.get("path") and os.path.isfile(item["path"]):
                exports.append(item)
    return exports
