#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — evidence-first figure pipeline (standard library only).

Design principle: every scientific element (entity / relation / step / stage /
data encoding / legend entry / annotation) carries a locator plus a verbatim
quote, and the whole set is aggregated into a granular evidence_map with a
forward index (element -> source) and a reverse index (source -> elements).

This module deliberately contains no network calls, no third-party imports and
no randomness, so the same input produces the same figure every run.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# constants & small helpers
# --------------------------------------------------------------------------- #

MAX_FILE_BYTES = 2 * 1024 * 1024
QUOTE_MAX = 240
XML_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;"}

# Default output-contract fields for this drawing topic (six categories).
DEFAULT_CONTRACT_FIELDS = [
    "entities",
    "relations",
    "data_encoding",
    "legend_annotations",
    "editable_sources",
    "exports",
]

# Generic fields every result.json must contain regardless of case contract.
GENERIC_RESULT_FIELDS = [
    "case_id",
    "status",
    "title",
    "primary_artifact",
    "sources",
    "evidence_map",
    "uncertainty",
    "pending_human",
    "change_log",
    "checks",
    "limitations",
    "artifacts",
    "runtime",
]

_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
           "八": 8, "九": 9, "十": 10}
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"

# --------------------------------------------------------------------------- #
# time & hashing
# --------------------------------------------------------------------------- #


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def xml_escape(text: Any) -> str:
    if text is None:
        return ""
    return "".join(XML_ESCAPES.get(ch, ch) for ch in str(text))


def truncate_quote(text: str, limit: int = QUOTE_MAX) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    if len(t) <= limit:
        return t
    return t[:limit] + "…"


# --------------------------------------------------------------------------- #
# tolerant file reading & input discovery
# --------------------------------------------------------------------------- #


def read_text_tolerant(path: str) -> Tuple[Optional[str], Dict[str, Any]]:
    """Read a UTF-8 (or fallback) text file; never raises on bad content."""
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError as exc:
        return None, {"error": str(exc), "path": str(p)}

    meta: Dict[str, Any] = {"path": str(p), "bytes": len(data),
                            "sha256": hashlib.sha256(data).hexdigest()}
    if len(data) > MAX_FILE_BYTES:
        meta["error"] = "file exceeds %d bytes" % MAX_FILE_BYTES
        return None, meta

    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            text = data.decode(enc)
            meta["encoding"] = enc
            meta["chars"] = len(text)
            return text, meta
        except (UnicodeDecodeError, LookupError):
            continue
    text = data.decode("utf-8", errors="replace")
    meta["encoding"] = "utf-8(replace)"
    meta["chars"] = len(text)
    meta["decoding_errors"] = True
    return text, meta


def _is_text_filename(name: str) -> bool:
    low = name.lower()
    if low in ("case.json", "task.md"):
        return True
    if low.endswith((".txt", ".md", ".json", ".csv", ".tsv", ".yaml", ".yml", ".rst")):
        return True
    return False


def discover_input_files(input_dir: str) -> List[Dict[str, Any]]:
    """Walk an input directory; return discoverable files with metadata."""
    base = Path(input_dir)
    found: List[Dict[str, Any]] = []
    if not base.exists():
        return found
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        if not _is_text_filename(path.name):
            found.append({"path": str(path), "kind": _guess_kind(path),
                          "parseable": False,
                          "note": "binary/other; parse with pdf-text-tool or ocr-tool"})
            continue
        text, meta = read_text_tolerant(str(path))
        item = {"path": str(path), "kind": _guess_kind(path),
                "parseable": text is not None, "meta": meta}
        if text is not None:
            item["text"] = text
        found.append(item)
    return found


def _guess_kind(path: Path) -> str:
    name = path.name.lower()
    if name == "case.json":
        return "case"
    if name == "task.md":
        return "task"
    if path.name.lower().endswith(".pdf"):
        return "pdf"
    if path.name.lower().endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp")):
        return "image"
    if any(seg.lower() in ("materials", "source", "data", "refs", "references")
           for seg in path.parts[:-1]):
        return "material"
    return "material"


def load_case_json(input_dir: str) -> Tuple[Dict[str, Any], Optional[str]]:
    """Return (case_dict, locator). Missing / bad JSON never raises."""
    case_path = Path(input_dir) / "case.json"
    if not case_path.exists():
        return {}, None
    text, meta = read_text_tolerant(str(case_path))
    if text is None:
        return {}, None
    obj = parse_json_tolerant(text)
    if obj is None:
        return {"_parse_error": "case.json is not valid JSON"}, str(case_path)
    if isinstance(obj, list):
        obj = {"_parse_note": "case.json is a JSON array", "items": obj}
    if not isinstance(obj, dict):
        obj = {"_parse_note": "case.json is not a JSON object", "value": obj}
    return obj, str(case_path)


def parse_json_tolerant(text: str) -> Optional[Any]:
    t = text.lstrip("\ufeff").strip()
    if not t:
        return None
    try:
        return json.loads(t)
    except Exception:
        pass
    # try to locate the first balanced top-level object
    try:
        start = t.find("{")
        if start >= 0:
            dec = json.JSONDecoder()
            obj, _ = dec.raw_decode(t[start:])
            return obj
    except Exception:
        return None
    return None


def extract_case_id(case: Dict[str, Any], input_dir: str) -> str:
    for key in ("case_id", "id", "challenge_id", "challengeId", "armiId", "armi_id"):
        val = case.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    # fall back to a deterministic hash of the case file content
    case_path = Path(input_dir) / "case.json"
    if case_path.exists():
        text, _ = read_text_tolerant(str(case_path))
        if text:
            return "case-" + sha256_text(text)[:12]
    return "unknown-case"


# --------------------------------------------------------------------------- #
# locator / source normalization
# --------------------------------------------------------------------------- #


def rel_locator(path: str, base_dir: str, line: Optional[int] = None) -> str:
    try:
        rel = os.path.relpath(path, base_dir)
    except ValueError:
        rel = path
    loc = rel.replace(os.sep, "/")
    if line is not None:
        loc = "%s#L%d" % (loc, line)
    return loc


def source_registry_from_files(files: List[Dict[str, Any]], base_dir: str) -> List[Dict[str, Any]]:
    registry: List[Dict[str, Any]] = []
    for f in files:
        loc = rel_locator(f["path"], base_dir)
        entry: Dict[str, Any] = {
            "locator": loc,
            "path": f["path"],
            "kind": f.get("kind", "material"),
            "parseable": f.get("parseable", False),
        }
        meta = f.get("meta") or {}
        for k in ("encoding", "bytes", "chars", "sha256", "error", "decoding_errors"):
            if k in meta:
                entry[k] = meta[k]
        registry.append(entry)
    return registry


# --------------------------------------------------------------------------- #
# deterministic fallback extraction (low-confidence; LLM may overwrite ir.json)
# --------------------------------------------------------------------------- #

_STEP_RE = re.compile(
    r"^\s*(?:(\d{1,3})\s*[.、．)）:：]\s*"
    r"|step\s*(\d{1,3})\b[.:：\-]?\s*"
    r"|([一二三四五六七八九十]+)\s*[、.．:：]\s*"
    r"|([①-⑩])\s*)(.+)$"
)
_ARROW_RE = re.compile(r"(.+?)\s*(→|->|=>|⇒|<-)\s*(.+)$")
_ARROW_TOKENS = ("→", "->", "=>", "⇒", "<-", "←")


def _split_arrow_chain(line: str) -> Tuple[List[str], List[str]]:
    """Split 'A → B -> C ← D' into nodes and the arrow preceding each hop."""
    parts = re.split(r"\s*(→|->|=>|⇒|<-|←)\s*", line)
    nodes = [p for i, p in enumerate(parts) if i % 2 == 0 and p.strip()]
    arrows = [p for i, p in enumerate(parts) if i % 2 == 1]
    return nodes, arrows
_CONTAIN_RE = re.compile(r"(.+?)\s*(?:包括|包含|由|设有|配备)\s*(.+)$")
_SPLIT_RE = re.compile(r"[、，,；;]|\s+与\s+|\s+和\s+|\s+及\s+|\s+以及\s+|\s+或\s+")
_STOP_WORDS = {"包含", "包括", "由", "设有", "配备", "组成", "构成", "搭建", "装配",
               "的", "一个", "一种", "台", "套", "个", "只", "根", "条", "支"}


def _step_order(groups: Tuple) -> int:
    if groups[0]:
        return int(groups[0])
    if groups[1]:
        return int(groups[1])
    if groups[2]:
        return _CN_NUM.get(groups[2], 0)
    if groups[3]:
        return _CIRCLED.index(groups[3]) + 1
    return 0


def _clean_token(tok: str) -> str:
    t = tok.strip().strip("，,。.；;：:、）)（(【】[]")
    t = re.sub(r"^(包含|包括|由|设有|配备)\s*", "", t)
    t = re.sub(r"\s*(组成|构成|搭建|装配)$", "", t)
    t = t.strip(" 　")
    return t[:80]


def _split_items(s: str) -> List[str]:
    parts = [p for p in _SPLIT_RE.split(s) if p and p.strip()]
    out: List[str] = []
    for p in parts:
        tok = _clean_token(p)
        if tok and tok not in _STOP_WORDS and len(tok) >= 1:
            out.append(tok)
    return out


def extract_ir(files: List[Dict[str, Any]], base_dir: str,
               kind: str = "apparatus") -> Dict[str, Any]:
    """Deterministic, low-confidence IR from plain text. Honest fallback only."""
    ir: Dict[str, Any] = {
        "title": "Auto-extracted figure (low confidence, please review)",
        "kind": kind,
        "confidence": "low",
        "entities": [],
        "relations": [],
        "steps": [],
        "stages": [],
        "data_encoding": [],
        "legend_annotations": [],
        "annotations": [],
        "layout": {"width": 1200, "height": 800, "auto": True, "positions": {}},
    }
    entity_index: Dict[str, str] = {}
    eid = rn = sn = gn = 0

    def get_entity_id(label: str) -> str:
        nonlocal eid
        if label not in entity_index:
            eid += 1
            entity_index[label] = "e%d" % eid
            ir["entities"].append({
                "id": entity_index[label],
                "label": label,
                "type": "device",
                "source_ref": None,
                "quote": None,
                "confidence": "low",
            })
        return entity_index[label]

    for f in files:
        text = f.get("text")
        if not text:
            continue
        loc = rel_locator(f["path"], base_dir)
        for ln, raw in enumerate(text.splitlines(), 1):
            line = raw.rstrip()
            if not line.strip():
                continue
            q = truncate_quote(line)
            ref = "%s#L%d" % (loc, ln)

            m = _STEP_RE.match(line)
            if m and len(m.group(5).strip()) >= 2:
                sn += 1
                order = _step_order(m.groups()) or sn
                ir["steps"].append({
                    "id": "s%d" % sn,
                    "order": order,
                    "label": _clean_token(m.group(5)),
                    "kind": "main",
                    "source_ref": ref,
                    "quote": q,
                    "confidence": "low",
                })
                continue

            am = _ARROW_RE.match(line)
            if am:
                nodes, arrows = _split_arrow_chain(line)
                node_ids = [get_entity_id(_clean_token(n) or ("节点%d" % (i + 1)))
                            for i, n in enumerate(nodes)]
                for i in range(len(node_ids) - 1):
                    seg_arrow = arrows[i] if i < len(arrows) else "→"
                    src_id = node_ids[i]
                    dst_id = node_ids[i + 1]
                    if seg_arrow in ("<-", "←"):
                        src_id, dst_id = dst_id, src_id
                    rn += 1
                    ir["relations"].append({
                        "id": "r%d" % rn,
                        "from": src_id,
                        "to": dst_id,
                        "kind": "material_flow",
                        "direction": "forward",
                        "style": "solid",
                        "label": "",
                        "source_ref": ref,
                        "quote": q,
                        "confidence": "low",
                    })
                continue

            cm = _CONTAIN_RE.match(line)
            if cm and (cm.group(1).strip() or cm.group(2).strip()):
                container = _clean_token(cm.group(1)) or "装置"
                members = _split_items(cm.group(2))
                cid = get_entity_id(container)
                for member in members[:8]:
                    mid = get_entity_id(member)
                    rn += 1
                    ir["relations"].append({
                        "id": "r%d" % rn,
                        "from": cid,
                        "to": mid,
                        "kind": "assembly",
                        "direction": "forward",
                        "style": "solid",
                        "label": "包含",
                        "source_ref": ref,
                        "quote": q,
                        "confidence": "low",
                    })
                continue

    # seed generic legend conventions (derived, not quoted from source)
    ir["legend_annotations"] = [
        {"id": "l1", "key": "solid_arrow", "meaning": "物料/能量/信息流向",
         "visual": "实线箭头", "source_ref": None, "quote": None,
         "confidence": "low", "note": "通用图例约定，非原文引用"},
        {"id": "l2", "key": "dashed_arrow", "meaning": "回流/反馈",
         "visual": "虚线箭头", "source_ref": None, "quote": None,
         "confidence": "low", "note": "通用图例约定，非原文引用"},
    ]
    return ir


# --------------------------------------------------------------------------- #
# evidence map construction (granular)
# --------------------------------------------------------------------------- #


def build_evidence_map(ir: Dict[str, Any]) -> Dict[str, Any]:
    """Aggregate per-element evidence into forward + reverse indexes."""
    elements: Dict[str, Dict[str, Any]] = {}
    reverse: Dict[str, List[str]] = {}
    collections = {
        "entities": ir.get("entities") or [],
        "relations": ir.get("relations") or [],
        "steps": ir.get("steps") or [],
        "stages": ir.get("stages") or [],
        "data_encoding": ir.get("data_encoding") or [],
        "legend_annotations": ir.get("legend_annotations") or [],
        "annotations": ir.get("annotations") or [],
    }
    for coll, items in collections.items():
        for item in items:
            if not isinstance(item, dict):
                continue
            iid = item.get("id") or item.get("key")
            if not iid:
                continue
            loc = item.get("source_ref")
            quote = item.get("quote")
            entry: Dict[str, Any] = {
                "collection": coll,
                "id": iid,
                "locator": loc,
                "quote": quote,
                "confidence": item.get("confidence"),
                "method": item.get("method", "manual_annotation" if quote else "derived"),
                "label": item.get("label") or item.get("meaning") or item.get("key"),
            }
            if item.get("note"):
                entry["note"] = item["note"]
            elements[iid] = entry
            if loc:
                reverse.setdefault(loc, [])
                if iid not in reverse[loc]:
                    reverse[loc].append(iid)

    quoted = [e for e in elements.values() if e.get("locator") and e.get("quote")]
    unquoted = [e for e in elements.values() if not (e.get("locator") and e.get("quote"))]
    return {
        "schema": "evidence_map/v1",
        "granularity": "per-element locator + verbatim quote + confidence + method",
        "elements": elements,
        "reverse_index": reverse,
        "coverage": {
            "total_elements": len(elements),
            "quoted_elements": len(quoted),
            "unquoted_elements": len(unquoted),
            "quote_coverage": round(len(quoted) / max(1, len(elements)), 3),
        },
        "unquoted": unquoted,
    }


# --------------------------------------------------------------------------- #
# SVG building (layered, editable, deterministic, evidence attributes)
# --------------------------------------------------------------------------- #


def _label_width(label: str, font_size: float = 14.0) -> float:
    w = 0.0
    for ch in label or "":
        w += font_size if ord(ch) > 127 else font_size * 0.62
    return max(72.0, w + 26.0)


def compute_positions(ir: Dict[str, Any], width: int, height: int) -> Dict[str, List[float]]:
    entities = [e for e in (ir.get("entities") or []) if e.get("id")]
    steps = [s for s in (ir.get("steps") or []) if s.get("id")]
    stages = [g for g in (ir.get("stages") or []) if g.get("id")]
    seq = steps or stages
    explicit = ir.get("layout", {}).get("positions") or {}
    pos: Dict[str, List[float]] = {}
    margin = 60.0

    if seq:
        n = max(1, len(seq))
        step_w = min(230.0, (width - 2 * margin) / n)
        row_y = 110.0
        for i, s in enumerate(seq):
            pos[s.get("id")] = [margin + step_w / 2 + i * step_w, row_y]

    ne = len(entities)
    if ne:
        cols = min(4, max(1, ne))
        cell_w = width / cols
        cell_h = 150.0
        start_y = 260.0 if seq else 130.0
        for i, e in enumerate(entities):
            eid = e.get("id")
            if eid in pos:
                continue
            c = i % cols
            r = i // cols
            pos[eid] = [cell_w / 2 + c * cell_w, start_y + r * cell_h]

    for pid, xy in explicit.items():
        try:
            pos[pid] = [float(xy[0]), float(xy[1])]
        except (TypeError, ValueError, IndexError):
            continue
    return pos


def _arrow_markers(style_set: Iterable[str]) -> str:
    out = []
    if "solid" in style_set or not style_set:
        out.append('<marker id="arrow-solid" markerWidth="10" markerHeight="10" '
                   'refX="8" refY="3" orient="auto" markerUnits="strokeWidth">'
                   '<path d="M0,0 L8,3 L0,6 Z" fill="#1f2937"/></marker>')
    if "dashed" in style_set:
        out.append('<marker id="arrow-dashed" markerWidth="10" markerHeight="10" '
                   'refX="8" refY="3" orient="auto" markerUnits="strokeWidth">'
                   '<path d="M0,0 L8,3 L0,6 Z" fill="#b45309"/></marker>')
    if "dotted" in style_set:
        out.append('<marker id="arrow-dotted" markerWidth="10" markerHeight="10" '
                   'refX="8" refY="3" orient="auto" markerUnits="strokeWidth">'
                   '<path d="M0,0 L8,3 L0,6 Z" fill="#6b7280"/></marker>')
    return "\n".join(out)


def build_svg(ir: Dict[str, Any], out_path: str) -> Dict[str, Any]:
    """Render ir.json into a layered, editable SVG with evidence attributes."""
    entities = ir.get("entities") or []
    relations = ir.get("relations") or []
    steps = ir.get("steps") or []
    stages = ir.get("stages") or []
    annotations = ir.get("annotations") or []
    legend = ir.get("legend_annotations") or []
    layout = ir.get("layout") or {}
    width = int(layout.get("width") or 1200)
    height = int(layout.get("height") or 800)
    title = ir.get("title") or "科研图稿"

    pos = compute_positions(ir, width, height)
    entity_by_id = {e.get("id"): e for e in entities if e.get("id")}
    style_set = {r.get("style", "solid") for r in relations}
    style_set.update(("solid",))

    lines: List[str] = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append('<svg xmlns="http://www.w3.org/2000/svg" '
                 'xmlns:xlink="http://www.w3.org/1999/xlink" '
                 'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
                 'width="%d" height="%d" viewBox="0 0 %d %d">' % (width, height, width, height))
    lines.append("  <metadata>")
    ev = build_evidence_map(ir)
    ev_json = json.dumps(ev, ensure_ascii=False)
    lines.append('    <script type="application/json" id="scix-evidence-map">%s</script>'
                 % xml_escape(ev_json))
    lines.append("  </metadata>")
    lines.append("  <defs>")
    lines.append(_arrow_markers(style_set))
    lines.append("  </defs>")

    # layer: entities
    lines.append('  <g id="layer-entities" inkscape:groupmode="layer" '
                 'inkscape:label="entities">')
    for e in entities:
        eid = e.get("id")
        if not eid:
            continue
        x, y = pos.get(eid, [60.0, 60.0])
        label = e.get("label") or eid
        bw = _label_width(label)
        bh = 46.0
        src = e.get("source_ref") or ""
        q = truncate_quote(e.get("quote") or "", 120)
        fill = "#eff6ff"
        lines.append('    <g id="%s" data-source-ref="%s" data-quote="%s" '
                     'data-evidence-id="%s" data-confidence="%s">'
                     % (xml_escape(eid), xml_escape(src), xml_escape(q),
                        xml_escape(eid), xml_escape(e.get("confidence") or "")))
        lines.append('      <rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                     'rx="6" fill="%s" stroke="#1e3a8a" stroke-width="1.4"/>'
                     % (x - bw / 2, y - bh / 2, bw, bh, fill))
        lines.append('      <text x="%.1f" y="%.1f" font-family="sans-serif" '
                     'font-size="13" text-anchor="middle" dominant-baseline="middle">%s</text>'
                     % (x, y, xml_escape(label)))
        lines.append("    </g>")
    lines.append("  </g>")

    # layer: relations
    lines.append('  <g id="layer-relations" inkscape:groupmode="layer" '
                 'inkscape:label="relations">')
    for r in relations:
        rid = r.get("id")
        src = r.get("source_ref") or ""
        q = truncate_quote(r.get("quote") or "", 120)
        frm = r.get("from")
        to = r.get("to")
        if frm not in pos or to not in pos:
            continue
        x1, y1 = pos[frm]
        x2, y2 = pos[to]
        style = r.get("style", "solid")
        dash = ' stroke-dasharray="6 4"' if style == "dashed" else (
            ' stroke-dasharray="2 3"' if style == "dotted" else "")
        color = "#1f2937" if style == "solid" else ("#b45309" if style == "dashed" else "#6b7280")
        marker = "url(#arrow-%s)" % style if style in ("solid", "dashed", "dotted") else "url(#arrow-solid)"
        label = r.get("label") or ""
        lines.append('    <path id="%s" d="M %.1f %.1f L %.1f %.1f" fill="none" '
                     'stroke="%s" stroke-width="1.8"%s marker-end="%s" '
                     'data-source-ref="%s" data-quote="%s" data-evidence-id="%s" '
                     'data-from="%s" data-to="%s" data-direction="%s"/>'
                     % (xml_escape(rid), x1, y1, x2, y2, color, dash, marker,
                        xml_escape(src), xml_escape(q), xml_escape(rid),
                        xml_escape(frm), xml_escape(to),
                        xml_escape(r.get("direction") or "forward")))
        if label:
            lines.append('    <text x="%.1f" y="%.1f" font-family="sans-serif" '
                         'font-size="11" fill="%s" text-anchor="middle">%s</text>'
                         % ((x1 + x2) / 2, (y1 + y2) / 2 - 6, color, xml_escape(label)))
    lines.append("  </g>")

    # layer: steps / stages
    lines.append('  <g id="layer-steps" inkscape:groupmode="layer" inkscape:label="steps">')
    for s in (steps or stages):
        sid = s.get("id")
        if not sid:
            continue
        x, y = pos.get(sid, [60.0, 110.0])
        label = s.get("label") or sid
        order = s.get("order")
        text = ("%s. %s" % (order, label)) if order is not None else label
        bw = min(300.0, _label_width(text) + 10)
        bh = 44.0
        src = s.get("source_ref") or ""
        q = truncate_quote(s.get("quote") or "", 120)
        lines.append('    <g id="%s" data-source-ref="%s" data-quote="%s" '
                     'data-evidence-id="%s" data-confidence="%s">'
                     % (xml_escape(sid), xml_escape(src), xml_escape(q),
                        xml_escape(sid), xml_escape(s.get("confidence") or "")))
        lines.append('      <rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                     'rx="8" fill="#fefce8" stroke="#a16207" stroke-width="1.3"/>'
                     % (x - bw / 2, y - bh / 2, bw, bh))
        lines.append('      <text x="%.1f" y="%.1f" font-family="sans-serif" '
                     'font-size="12" text-anchor="middle" dominant-baseline="middle">%s</text>'
                     % (x, y, xml_escape(text)))
        lines.append("    </g>")
    lines.append("  </g>")

    # layer: annotations
    lines.append('  <g id="layer-annotations" inkscape:groupmode="layer" '
                 'inkscape:label="annotations">')
    for a in annotations:
        aid = a.get("id") or ("a%d" % (annotations.index(a) + 1))
        x = float(a.get("x", 80))
        y = float(a.get("y", height - 60))
        src = a.get("source_ref") or ""
        q = truncate_quote(a.get("quote") or "", 120)
        text = a.get("text") or ""
        lines.append('    <text id="%s" x="%.1f" y="%.1f" font-family="sans-serif" '
                     'font-size="11" fill="#374151" data-source-ref="%s" data-quote="%s">%s</text>'
                     % (xml_escape(aid), x, y, xml_escape(src), xml_escape(q), xml_escape(text)))
    lines.append("  </g>")

    # layer: legend
    lines.append('  <g id="layer-legend" inkscape:groupmode="layer" inkscape:label="legend">')
    if legend:
        lx = width - 260.0
        ly = 24.0
        lines.append('    <rect x="%.1f" y="%.1f" width="240" height="%d" fill="#ffffff" '
                     'fill-opacity="0.92" stroke="#9ca3af" stroke-width="1"/>'
                     % (lx, ly, 34 + 26 * len(legend)))
        for i, lg in enumerate(legend):
            key = lg.get("key") or "k%d" % i
            visual = lg.get("visual") or ""
            meaning = lg.get("meaning") or ""
            src = lg.get("source_ref") or ""
            q = truncate_quote(lg.get("quote") or "", 120)
            stroke = "#1f2937"
            dash = ""
            if key == "dashed_arrow":
                stroke, dash = "#b45309", ' stroke-dasharray="6 4"'
            elif key == "dotted_arrow":
                stroke, dash = "#6b7280", ' stroke-dasharray="2 3"'
            yy = ly + 22 + 26 * i
            lines.append('      <line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                         'stroke="%s" stroke-width="1.8"%s marker-end="url(#arrow-solid)" '
                         'data-source-ref="%s" data-quote="%s"/>'
                         % (lx + 12, yy, lx + 48, yy, stroke, dash,
                            xml_escape(src), xml_escape(q)))
            lines.append('      <text x="%.1f" y="%.1f" font-family="sans-serif" '
                         'font-size="11" fill="#111827">%s%s</text>'
                         % (lx + 56, yy + 4, xml_escape(meaning),
                            ("  [" + xml_escape(visual) + "]" if visual else "")))
    lines.append("  </g>")

    lines.append("  <g id=\"layer-title\" inkscape:groupmode=\"layer\" inkscape:label=\"title\">")
    lines.append('    <text x="%.1f" y="30" font-family="sans-serif" font-size="16" '
                 'font-weight="bold" text-anchor="middle" fill="#111827">%s</text>'
                 % (width / 2, xml_escape(title)))
    lines.append("  </g>")
    lines.append("</svg>")

    content = "\n".join(lines) + "\n"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(content, encoding="utf-8")
    return {"path": out_path, "bytes": len(content.encode("utf-8")),
            "width": width, "height": height, "layers": [
                "layer-title", "layer-entities", "layer-relations",
                "layer-steps", "layer-annotations", "layer-legend"]}


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #

_NS = {"svg": "http://www.w3.org/2000/svg"}


def _svg_parse(path: str):
    import xml.etree.ElementTree as ET
    try:
        tree = ET.parse(path)
        return tree, None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def validate_svg(ir: Dict[str, Any], svg_path: str) -> List[Dict[str, Any]]:
    """Run structural checks against the generated SVG + IR."""
    checks: List[Dict[str, Any]] = []
    import xml.etree.ElementTree as ET

    tree, err = _svg_parse(svg_path)
    if tree is None:
        checks.append({"id": "svg_parse", "level": "critical", "passed": False,
                       "message": "SVG unparseable: %s" % err})
        return checks
    root = tree.getroot()
    checks.append({"id": "svg_parse", "level": "info", "passed": True,
                   "message": "SVG parsed successfully"})

    svg_ids = set()
    for el in root.iter():
        for attr in ("id",):
            v = el.get(attr)
            if v:
                svg_ids.add(v)

    entities = [e for e in (ir.get("entities") or []) if e.get("id")]
    relations = ir.get("relations") or []
    entity_ids = {e.get("id") for e in entities}
    pos = compute_positions(ir, int(root.get("width", 1200)), int(root.get("height", 800)))

    # dangling relation endpoints
    dangling = []
    for r in relations:
        if r.get("from") and r.get("from") not in entity_ids:
            dangling.append("%s.from=%s" % (r.get("id"), r.get("from")))
        if r.get("to") and r.get("to") not in entity_ids:
            dangling.append("%s.to=%s" % (r.get("id"), r.get("to")))
    if dangling:
        checks.append({"id": "dangling_relations", "level": "critical",
                       "passed": False,
                       "message": "relations reference missing entities: %s" % ", ".join(dangling)})
    else:
        checks.append({"id": "dangling_relations", "level": "info",
                       "passed": True, "message": "all relation endpoints resolve"})

    # source_ref coverage
    total_el = len(entities) + len(relations) + len(ir.get("steps") or []) + len(ir.get("stages") or [])
    traced = sum(1 for e in entities + relations + (ir.get("steps") or []) + (ir.get("stages") or [])
                 if e.get("source_ref"))
    checks.append({"id": "source_ref_coverage", "level": "warning" if traced < total_el else "info",
                   "passed": traced == total_el,
                   "message": "%d/%d elements carry source_ref" % (traced, total_el)})

    # verbatim quote coverage
    quoted = sum(1 for e in entities + relations + (ir.get("steps") or []) + (ir.get("stages") or [])
                 if e.get("quote"))
    checks.append({"id": "quote_coverage", "level": "warning" if quoted < traced else "info",
                   "passed": quoted == traced,
                   "message": "%d/%d traced elements carry a verbatim quote" % (quoted, max(1, traced))})

    # legend consistency
    legend_keys = {lg.get("key") for lg in (ir.get("legend_annotations") or []) if lg.get("key")}
    used_styles = {r.get("style", "solid") for r in relations}
    used_tokens = {"solid" if s == "solid_arrow" else s.replace("_arrow", "")
                   for s in legend_keys}
    orphan_legend = [k for k in legend_keys
                     if (k.replace("_arrow", "") if k.endswith("_arrow") else k) not in used_styles]
    if orphan_legend and relations:
        checks.append({"id": "legend_consistency", "level": "warning", "passed": False,
                       "message": "legend keys without matching usage: %s" % ", ".join(sorted(orphan_legend))})
    else:
        checks.append({"id": "legend_consistency", "level": "info", "passed": True,
                       "message": "legend entries consistent with used styles"})

    # canvas overflow
    width = int(root.get("width", 1200))
    height = int(root.get("height", 800))
    overflow = []
    for e in entities:
        x, y = pos.get(e.get("id"), (0, 0))
        bw = _label_width(e.get("label") or e.get("id"), 14)
        if x - bw / 2 < 0 or x + bw / 2 > width or y - 23 < 0 or y + 23 > height:
            overflow.append(e.get("id"))
    if overflow:
        checks.append({"id": "canvas_overflow", "level": "warning", "passed": False,
                       "message": "entities outside canvas: %s" % ", ".join(overflow)})
    else:
        checks.append({"id": "canvas_overflow", "level": "info", "passed": True,
                       "message": "all entities within canvas"})

    critical_failed = [c for c in checks if c["level"] == "critical" and not c["passed"]]
    checks.append({"id": "summary", "level": "summary", "passed": not critical_failed,
                   "message": "%d check(s), %d failed" % (len(checks), len(critical_failed))})
    return checks


# --------------------------------------------------------------------------- #
# rendering (honest probing, no fabricated success)
# --------------------------------------------------------------------------- #


def render_svg(svg_path: str, out_dir: str) -> Dict[str, Any]:
    """Best-effort SVG -> PNG/PDF using whatever renderer is installed.

    Standard-library-only: probe external commands; if none exists, report
    render_status='skipped' instead of inventing an output.
    """
    result: Dict[str, Any] = {"render_status": "skipped", "exports": [], "note": ""}
    candidates = [
        ("rsvg-convert", ["rsvg-convert", "-o"]),
        ("inkscape", ["inkscape", "--export-filename="]),
        ("magick", ["magick", "-density", "150"]),
        ("convert", ["convert", "-density", "150"]),
        ("cairosvg", ["python", "-c", "import cairosvg"]),
    ]
    png_path = os.path.join(out_dir, "figure.png")
    pdf_path = os.path.join(out_dir, "figure.pdf")
    made = []
    for name, argv in candidates:
        if name == "cairosvg":
            code = subprocess.run(argv, capture_output=True).returncode
            if code == 0:
                try:
                    import cairosvg  # noqa: F401
                    cairosvg.svg2png(url=svg_path, write_to=png_path)
                    cairosvg.svg2pdf(url=svg_path, write_to=pdf_path)
                    made = [png_path, pdf_path]
                except Exception:
                    made = []
            else:
                continue
        else:
            try:
                found = subprocess.run([argv[0], "--version"] if name != "magick"
                                       else [argv[0], "-version"],
                                       capture_output=True)
            except (FileNotFoundError, OSError):
                continue
            if found.returncode != 0:
                continue
            try:
                if name == "rsvg-convert":
                    subprocess.run(["rsvg-convert", "-o", png_path, svg_path], check=True)
                    subprocess.run(["rsvg-convert", "-f", "pdf", "-o", pdf_path, svg_path], check=True)
                elif name == "inkscape":
                    subprocess.run(["inkscape", "--export-filename=" + png_path, svg_path], check=True)
                    subprocess.run(["inkscape", "--export-filename=" + pdf_path, svg_path], check=True)
                elif name in ("magick", "convert"):
                    subprocess.run([argv[0], "-density", "150", svg_path, png_path], check=True)
                made = [p for p in (png_path, pdf_path) if os.path.exists(p)]
            except Exception:
                made = [p for p in (png_path, pdf_path) if os.path.exists(p)]
        if made:
            break

    if made:
        result["render_status"] = "rendered"
        for p in made:
            ext = os.path.splitext(p)[1].lstrip(".").lower()
            result["exports"].append({
                "path": p, "format": ext,
                "bytes": os.path.getsize(p) if os.path.exists(p) else 0,
                "width": None, "height": None, "dpi": 150,
                "fonts": "system fonts", "color_check": "not run",
            })
        result["note"] = "rendered via probe"
    else:
        result["note"] = ("no SVG renderer found (rsvg-convert/inkscape/magick/cairosvg); "
                          "use browser-automation or office-renderer tool, then update exports")
    return result


# --------------------------------------------------------------------------- #
# result.json assembly
# --------------------------------------------------------------------------- #


def build_editable_sources(svg_path: str, work_dir: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    if os.path.exists(svg_path):
        entries.append({
            "path": svg_path,
            "format": "svg",
            "layers": ["layer-title", "layer-entities", "layer-relations",
                       "layer-steps", "layer-annotations", "layer-legend"],
            "editable": True,
            "notes": "分层可编辑 SVG；每个图元带 data-source-ref / data-quote 溯源属性",
        })
    ir_path = os.path.join(work_dir, "ir.json")
    if os.path.exists(ir_path):
        entries.append({
            "path": ir_path, "format": "json",
            "layers": [], "editable": True,
            "notes": "结构化中间表示（IR），改动后重跑 build 即重绘",
        })
    return entries


def build_result(case_id: str, case: Dict[str, Any], ir: Dict[str, Any],
                 evidence_map: Dict[str, Any], sources: List[Dict[str, Any]],
                 editable_sources: List[Dict[str, Any]], exports: List[Dict[str, Any]],
                 checks: List[Dict[str, Any]], status: str, change_log: List[Dict[str, Any]],
                 render: Dict[str, Any], work_dir: str, started_at: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {"case_id": case_id, "status": status}

    # 1) copy every declared output_contract field verbatim (fill later)
    oc = case.get("output_contract")
    if isinstance(oc, dict) and oc:
        for key in oc.keys():
            result[key] = None

    # 2) six default contract categories
    result["entities"] = ir.get("entities") or []
    result["relations"] = ir.get("relations") or []
    result["data_encoding"] = ir.get("data_encoding") or []
    result["legend_annotations"] = ir.get("legend_annotations") or []
    result["editable_sources"] = editable_sources
    result["exports"] = exports

    # 3) generic fields
    result["title"] = ir.get("title") or ""
    result["primary_artifact"] = (editable_sources[0]["path"] if editable_sources else None)
    result["sources"] = sources
    result["evidence_map"] = evidence_map
    result["uncertainty"] = _collect_uncertainty(ir)
    result["pending_human"] = _collect_pending(ir, checks, render)
    result["change_log"] = change_log
    result["checks"] = checks
    result["limitations"] = _collect_limitations(sources, render, checks, ir)
    result["artifacts"] = _collect_artifacts(editable_sources, exports, work_dir)
    result["runtime"] = {
        "started_at": started_at,
        "finished_at": now_iso(),
        "budget_seconds": 900,
        "budget_requests": 64,
    }
    # 4) any contract fields that were declared but not otherwise filled stay null
    #    (kept honest rather than guessed)
    return result


def _collect_uncertainty(ir: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for coll in ("entities", "relations", "steps", "stages", "data_encoding",
                 "legend_annotations", "annotations"):
        for item in ir.get(coll) or []:
            if item.get("confidence") in ("low", "medium", None) or not item.get("source_ref"):
                out.append({
                    "element_id": item.get("id") or item.get("key"),
                    "collection": coll,
                    "reason": "low confidence or missing source_ref",
                    "detail": item.get("label") or item.get("meaning") or item.get("key") or "",
                })
    return out


def _collect_pending(ir: Dict[str, Any], checks: List[Dict[str, Any]],
                     render: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    failed_critical = [c for c in checks if c["level"] == "critical" and not c["passed"]]
    if failed_critical:
        out.append({"issue": "critical checks failed", "detail": [c["message"] for c in failed_critical],
                    "action": "fix IR/SVG then rerun build + validate"})
    if render.get("render_status") == "skipped":
        out.append({"issue": "no PNG/PDF preview", "detail": render.get("note"),
                    "action": "render with browser-automation/office-renderer and update exports"})
    for coll in ("entities", "relations", "steps", "stages"):
        for item in ir.get(coll) or []:
            if not item.get("source_ref") or not item.get("quote"):
                out.append({"issue": "missing evidence",
                            "element_id": item.get("id"),
                            "action": "locate verbatim source quote or mark as derived"})
    return out


def _collect_limitations(sources: List[Dict[str, Any]], render: Dict[str, Any],
                         checks: List[Dict[str, Any]], ir: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for s in sources:
        if not s.get("parseable") and s.get("kind") in ("pdf", "image"):
            out.append("source '%s' requires pdf-text-tool/ocr-tool and was not parsed here"
                       % s.get("locator"))
        if s.get("error"):
            out.append("source '%s': %s" % (s.get("locator"), s.get("error")))
    if render.get("render_status") != "rendered":
        out.append("render_status=%s: %s" % (render.get("render_status"), render.get("note")))
    if ir.get("confidence") == "low":
        out.append("IR was auto-extracted at low confidence; human review required")
    return out


def _collect_artifacts(editable_sources: List[Dict[str, Any]],
                       exports: List[Dict[str, Any]], work_dir: str) -> List[Dict[str, Any]]:
    paths = [e.get("path") for e in editable_sources if e.get("path")]
    for e in exports:
        if isinstance(e, dict) and e.get("path"):
            paths.append(e["path"])
    for extra in ("ir.json", "checks.json", "evidence_map.json"):
        p = os.path.join(work_dir, extra)
        if os.path.exists(p):
            paths.append(p)
    seen = set()
    artifacts = []
    for p in paths:
        if not p or p in seen:
            continue
        seen.add(p)
        artifacts.append({"path": p, "exists": os.path.exists(p),
                          "bytes": os.path.getsize(p) if os.path.exists(p) else 0})
    return artifacts
