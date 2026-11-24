#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_map.py — 由 map_spec 确定性渲染可编辑 + 可预览的思维导图产物。

仅使用 Python 标准库。输入为语义规格 JSON（Agent 按 SKILL.md/references 生成），
输出：
    /app/output/mindmap.mm     FreeMind 1.0.1 XML（XMind/FreeMind 可继续编辑）
    /app/output/mindmap.svg    分层树形 SVG 预览（浏览器可看、矢量可改、文字不转曲）
    /app/output/outline.md     带证据标记的大纲（人类可读可改）
    /app/output/evidence.json  节点/关系 -> locator + quote + evidence_class 溯源映射
    /app/output/result.json    含 case_id + output_contract 全部字段 + 状态/来源/留痕
同时写 /app/work/artifacts.json 清单；若环境有栅格化工具，另产 mindmap.png。

用法:
    python3 -B scripts/build_map.py --spec /app/work/map_spec.json \
        --output /app/output --work /app/work
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
# 语义常量
# ---------------------------------------------------------------------------
NODE_KINDS = ["root", "concept", "question", "method", "evidence", "conclusion",
              "task", "risk", "gap", "decision", "goal", "milestone",
              "deliverable", "owner", "deadline", "other"]

NODE_STYLE = {
    "root":        {"fill": "#1f6fb2", "stroke": "#154a78", "text": "#ffffff"},
    "concept":     {"fill": "#e8f1fb", "stroke": "#1f6fb2", "text": "#1a1a1a"},
    "question":    {"fill": "#fdecea", "stroke": "#c0392b", "text": "#1a1a1a"},
    "method":      {"fill": "#e6f4ea", "stroke": "#2e7d32", "text": "#1a1a1a"},
    "evidence":    {"fill": "#fff7e0", "stroke": "#b78a00", "text": "#1a1a1a"},
    "conclusion":  {"fill": "#efe8f7", "stroke": "#5e35b1", "text": "#1a1a1a"},
    "task":        {"fill": "#e0f6f8", "stroke": "#007a86", "text": "#1a1a1a"},
    "risk":        {"fill": "#fbe9e7", "stroke": "#d84315", "text": "#1a1a1a"},
    "gap":         {"fill": "#f3e5f5", "stroke": "#8e24aa", "text": "#1a1a1a"},
    "decision":    {"fill": "#eceff1", "stroke": "#37474f", "text": "#1a1a1a"},
    "goal":        {"fill": "#e0f2f1", "stroke": "#00695c", "text": "#1a1a1a"},
    "milestone":   {"fill": "#fce4ec", "stroke": "#c2185b", "text": "#1a1a1a"},
    "deliverable": {"fill": "#fff3e0", "stroke": "#e65100", "text": "#1a1a1a"},
    "owner":       {"fill": "#e0f2f1", "stroke": "#00695c", "text": "#1a1a1a"},
    "deadline":    {"fill": "#fbe9e7", "stroke": "#bf360c", "text": "#1a1a1a"},
    "other":       {"fill": "#f5f5f5", "stroke": "#616161", "text": "#1a1a1a"},
}

REL_TYPES = ["causes", "depends_on", "contrasts", "precedes", "cites",
             "supports", "conflicts", "part_of", "example_of",
             "defined_by", "measures", "limits"]

REL_COLOR = {
    "causes": "#d84315", "depends_on": "#1f6fb2", "contrasts": "#8e24aa",
    "precedes": "#546e7a", "cites": "#007a86", "supports": "#2e7d32",
    "conflicts": "#c0392b", "part_of": "#5e35b1", "example_of": "#6d4c41",
    "defined_by": "#00695c", "measures": "#b78a00", "limits": "#bf360c",
}

EVIDENCE_CLASSES = ("fact", "summary", "inference")

KIND_CN = {
    "root": "主题", "concept": "概念", "question": "问题", "method": "方法",
    "evidence": "证据", "conclusion": "结论", "task": "任务", "risk": "风险",
    "gap": "空白", "decision": "决策", "goal": "目标", "milestone": "里程碑",
    "deliverable": "交付物", "owner": "负责人", "deadline": "截止", "other": "其他",
}

FONT_SIZE = 13
LINE_HEIGHT = 17
MAX_LINE_UNITS = 18
MAX_LINES = 5
H_GAP = 288
V_SLOT = 84
MARGIN = 44
MAX_NODES = 400
MAX_NODE_TEXT = 200

# 渲染/回填结果的状态枚举
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def xml_escape(value) -> str:
    if value is None:
        return ""
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;"))


def char_units(ch: str) -> float:
    return 1.0 if ord(ch) > 0x2E80 else 0.56


def text_units(text) -> float:
    return sum(char_units(c) for c in str(text or ""))


def wrap_text(text, max_units=MAX_LINE_UNITS, max_lines=MAX_LINES):
    text = str(text or "").strip()[:MAX_NODE_TEXT]
    if not text:
        return [""]
    lines, current, width = [], "", 0.0
    for ch in text:
        if ch == "\n":
            lines.append(current)
            current, width = "", 0.0
            if len(lines) >= max_lines:
                break
            continue
        w = char_units(ch)
        if current and width + w > max_units:
            lines.append(current)
            current, width = ch, w
        else:
            current += ch
            width += w
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    lines = lines[:max_lines]
    if lines and text_units("".join(lines)) < text_units(text):
        lines[-1] = lines[-1].rstrip("…") + "…"
    return lines or [""]


# ---------------------------------------------------------------------------
# 谱规约解析
# ---------------------------------------------------------------------------
def collect_nodes(root, errors):
    nodes, order = {}, []
    used = set()

    def walk(node, parent, depth):
        if not isinstance(node, dict):
            errors.append("node not object under parent=%s" % parent)
            return None
        nid = str(node.get("id", "") or "").strip()
        if not nid:
            nid = "n%d" % (len(order) + 1)
        if nid in used:
            errors.append("duplicate node id: %s" % nid)
            nid = "%s_%d" % (nid, len(order))
        used.add(nid)
        node["id"] = nid
        node["parent"] = parent
        node["depth"] = depth
        node["_children_ids"] = []
        nodes[nid] = node
        order.append(node)
        for child in node.get("children", []) or []:
            cid = walk(child, nid, depth + 1)
            if cid:
                node["_children_ids"].append(cid)
        return nid

    walk(root, None, 0)
    return nodes, order


def normalize_kind(node) -> str:
    kind = str(node.get("kind", "other") or "other").strip().lower()
    return kind if kind in NODE_KINDS else "other"


def evidence_of(item) -> tuple:
    """返回 (locator, quote, evidence_class)。"""
    if not isinstance(item, dict):
        return "", "", "unattributed"
    ev = item.get("evidence")
    if not isinstance(ev, dict):
        return "", "", "unattributed"
    loc = str(ev.get("locator", "") or "").strip()
    quote = str(ev.get("quote", "") or "").strip()
    cls = str(ev.get("class", "") or ev.get("evidence_class", "") or "").strip().lower()
    if cls not in EVIDENCE_CLASSES:
        cls = "unattributed"
    return loc, quote, cls


# ---------------------------------------------------------------------------
# 布局：叶子按谱中顺序占槽，内部节点取子节点垂直中心，确定性且无重叠
# ---------------------------------------------------------------------------
def layout(nodes, order):
    leaf_slot = [0]

    def assign(node):
        children = [nodes[c] for c in node.get("_children_ids", [])]
        if not children:
            node["_y"] = leaf_slot[0]
            leaf_slot[0] += 1
            return node["_y"], node["_y"]
        first = last = None
        for child in children:
            f, l = assign(child)
            first = f if first is None else first
            last = l
        node["_y"] = (first + last) / 2.0
        return first, last

    if order:
        assign(order[0])
    return leaf_slot[0]


# ---------------------------------------------------------------------------
# FreeMind 渲染
# ---------------------------------------------------------------------------
def _note_html(loc, quote, cls, node) -> ET.Element:
    rc = ET.Element("richcontent", {"TYPE": "NOTE"})
    html = ET.SubElement(rc, "html")
    ET.SubElement(html, "head")
    body = ET.SubElement(html, "body")
    p = ET.SubElement(body, "p")
    parts = ["[%s] %s" % (normalize_kind(node), str(node.get("text", "") or ""))]
    if loc:
        parts.append("source: %s" % loc)
    if quote:
        parts.append("quote: %s" % quote)
    if cls and cls != "unattributed":
        parts.append("evidence_class: %s" % cls)
    p.text = "\n".join(parts)
    return rc


def render_mm(root_node, nodes, relations) -> ET.Element:
    mm = ET.Element("map", {"version": "1.0.1"})

    def add(node, parent_el):
        loc, quote, cls = evidence_of(node)
        style = NODE_STYLE.get(normalize_kind(node), NODE_STYLE["other"])
        el = ET.SubElement(parent_el, "node", {
            "ID": node["id"],
            "TEXT": str(node.get("text", "") or "")[:MAX_NODE_TEXT],
            "POSITION": "right",
            "FOLDED": "false",
            "COLOR": style["stroke"],
        })
        el.append(_note_html(loc, quote, cls, node))
        for rel in relations:
            if rel.get("from") == node["id"]:
                ET.SubElement(el, "arrowlink", {
                    "DESTINATION": str(rel.get("to", "")),
                    "COLOR": REL_COLOR.get(rel.get("type", ""), "#616161"),
                    "ENDARROW": "Default",
                })
        for cid in node.get("_children_ids", []):
            add(nodes[cid], el)

    add(root_node, mm)
    return mm


# ---------------------------------------------------------------------------
# SVG 渲染
# ---------------------------------------------------------------------------
def render_svg(nodes, order, relations, title):
    if not order:
        return ""
    root_node = order[0]
    leaf_count = layout(nodes, order)
    max_depth = max(n["depth"] for n in order)

    line_cache, widths = {}, {}
    for n in order:
        lines = wrap_text(n.get("text", ""))
        line_cache[n["id"]] = lines
        widths[n["id"]] = max((text_units(ln) for ln in lines), default=0) * FONT_SIZE + 18

    col_w = max(widths.values()) if widths else 200
    h_gap = max(H_GAP, col_w + 70)
    width = MARGIN * 2 + max_depth * h_gap + col_w + 20
    height = MARGIN * 2 + max(leaf_count - 1, 0) * V_SLOT + 90

    pos = {}
    for n in order:
        pos[n["id"]] = (MARGIN + n["depth"] * h_gap,
                        MARGIN + n["_y"] * V_SLOT, widths[n["id"]])

    out = ['<?xml version="1.0" encoding="UTF-8"?>']
    out.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
               'viewBox="0 0 %d %d" font-family="PingFang SC,Microsoft YaHei,'
               'Noto Sans CJK SC,sans-serif">' % (width, height, width, height))
    out.append('<rect width="100%%" height="100%%" fill="#ffffff"/>')
    out.append('<text x="%d" y="26" font-size="16" font-weight="bold" '
               'fill="#1a1a1a">%s</text>' % (MARGIN, xml_escape(title or "思维导图")))
    out.append('<text x="%d" y="46" font-size="10" fill="#666666">图例(kind)</text>'
               % MARGIN)
    lx = MARGIN + 70
    for kind in NODE_KINDS:
        style = NODE_STYLE[kind]
        if lx > width - 90:
            break
        label = KIND_CN.get(kind, kind)
        out.append('<rect x="%d" y="37" width="10" height="10" rx="2" fill="%s" '
                   'stroke="%s"/>' % (lx, style["fill"], style["stroke"]))
        out.append('<text x="%d" y="46" font-size="10" fill="#444444">%s</text>'
                   % (lx + 13, label))
        lx += 13 + 10 * len(label) + 18

    # 层级连线（先画，保证在节点下方）
    for n in order:
        if n.get("parent") is None or n is root_node:
            continue
        px, py, pw = pos[nodes[n["parent"]]["id"]]
        cx, cy, _ = pos[n["id"]]
        x1, y1, x2, y2 = px + pw, py, cx, cy
        midx = (x1 + x2) / 2.0
        out.append('<path d="M %.1f %.1f C %.1f %.1f, %.1f %.1f, %.1f %.1f" '
                   'fill="none" stroke="#b0bec5" stroke-width="1.4"/>'
                   % (x1, y1, midx, y1, midx, y2, x2, y2))

    # 关系连线（cross-link，虚线带箭头与标签）
    for rel in relations:
        f, t = rel.get("from"), rel.get("to")
        if f not in pos or t not in pos:
            continue
        x1, y1, _ = pos[f]
        x2, y2, _ = pos[t]
        color = REL_COLOR.get(rel.get("type", ""), "#616161")
        bow = 24 if y2 >= y1 else -24
        mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0 + bow
        out.append('<path d="M %.1f %.1f Q %.1f %.1f %.1f %.1f" fill="none" '
                   'stroke="%s" stroke-width="1.6" stroke-dasharray="5,3"/>'
                   % (x1, y1, mx, my, x2, y2, color))
        out.append(_svg_arrow(x1, y1, mx, my, x2, y2, color))
        label = rel.get("label") or rel.get("type") or ""
        if label:
            out.append('<text x="%.1f" y="%.1f" font-size="10" fill="%s" '
                       'text-anchor="middle">%s</text>'
                       % (mx, my - 4, color, xml_escape(label)))

    # 节点盒
    for n in order:
        x, y, w = pos[n["id"]]
        lines = line_cache[n["id"]]
        h = len(lines) * LINE_HEIGHT + 12
        style = NODE_STYLE.get(normalize_kind(n), NODE_STYLE["other"])
        top = y - h / 2.0
        rx = 8 if n is root_node else 6
        out.append('<g id="node-%s"><rect x="%.1f" y="%.1f" width="%.1f" '
                   'height="%.1f" rx="%d" fill="%s" stroke="%s" stroke-width="1.6"/>'
                   % (xml_escape(n["id"]), x, top, w, h, rx,
                      style["fill"], style["stroke"]))
        ty = top + LINE_HEIGHT
        for line in lines:
            out.append('<text x="%.1f" y="%.1f" font-size="%d" fill="%s">%s</text>'
                       % (x + 9, ty, FONT_SIZE, style["text"], xml_escape(line)))
            ty += LINE_HEIGHT
        out.append('<text x="%.1f" y="%.1f" font-size="9" fill="%s">%s</text></g>'
                   % (x + 9, top + h - 4, style["text"],
                      KIND_CN.get(normalize_kind(n), "other")))
    out.append('</svg>')
    return "\n".join(out) + "\n"


def _svg_arrow(x1, y1, mx, my, x2, y2, color) -> str:
    dx, dy = 2.0 * (x2 - mx), 2.0 * (y2 - my)
    norm = math.hypot(dx, dy) or 1.0
    ux, uy = dx / norm, dy / norm
    px, py = -uy, ux
    ah = 7.0
    bx = x2 - ux * ah
    return ('<polygon points="%.1f,%.1f %.1f,%.1f %.1f,%.1f" fill="%s"/>'
            % (x2, y2, bx + px * 3.5, y2 + py * 3.5, bx - px * 3.5, y2 - py * 3.5, color))


# ---------------------------------------------------------------------------
# 大纲与证据映射
# ---------------------------------------------------------------------------
def render_outline(nodes, order, relations, title) -> str:
    lines = ["# %s" % (title or "思维导图"), ""]
    rel_by_from = {}
    for rel in relations:
        rel_by_from.setdefault(rel.get("from"), []).append(rel)

    def emit(node, depth):
        indent = "  " * depth
        loc, quote, cls = evidence_of(node)
        kind = KIND_CN.get(normalize_kind(node), "other")
        seg = "%s- [%s] %s" % (indent, kind, str(node.get("text", "") or ""))
        marks = []
        if loc:
            marks.append("⟦%s⟧" % loc)
        if quote:
            marks.append('"%s"' % (quote[:80] + ("…" if len(quote) > 80 else "")))
        if cls and cls != "unattributed":
            marks.append("(%s)" % cls)
        if marks:
            seg += "  " + " ".join(marks)
        lines.append(seg)
        for rel in rel_by_from.get(node["id"], []):
            target = nodes.get(rel.get("to"), {})
            target_text = str(target.get("text", "") or rel.get("to"))
            lines.append("%s  ↳ [%s] → %s%s" % (
                indent, rel.get("type", ""), target_text,
                (" · %s" % rel["label"]) if rel.get("label") else ""))
        for cid in node.get("_children_ids", []):
            emit(nodes[cid], depth + 1)

    emit(order[0], 0)
    return "\n".join(lines) + "\n"


def build_evidence(nodes, order, relations, case_id) -> dict:
    ev_nodes = {}
    for n in order:
        loc, quote, cls = evidence_of(n)
        ev_nodes[n["id"]] = {
            "text": str(n.get("text", "") or ""),
            "kind": normalize_kind(n),
            "locator": loc,
            "quote": quote,
            "evidence_class": cls,
        }
    ev_relations = []
    for rel in relations:
        loc, quote, cls = evidence_of(rel)
        ev_relations.append({
            "from": rel.get("from"), "to": rel.get("to"),
            "type": rel.get("type"), "label": rel.get("label", ""),
            "locator": loc, "quote": quote, "evidence_class": cls,
        })
    return {
        "case_id": case_id or "",
        "counts": {"nodes": len(order), "relations": len(relations)},
        "nodes": ev_nodes,
        "relations": ev_relations,
    }


# ---------------------------------------------------------------------------
# 预览（可选：环境有栅格化工具才产出 PNG）
# ---------------------------------------------------------------------------
def find_rasterizer() -> str:
    for name in ("rsvg-convert", "inkscape", "chromium", "chromium-browser",
                 "google-chrome", "google-chrome-stable", "magick", "convert",
                 "wkhtmltoimage"):
        path = shutil.which(name)
        if path:
            return name
    return ""


def render_preview(svg_path: str, output_dir: str) -> str:
    png_path = os.path.join(output_dir, "mindmap.png")
    tool = find_rasterizer()
    if not tool:
        return ""
    try:
        kwargs = {"check": True, "timeout": 60,
                  "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if tool == "rsvg-convert":
            subprocess.run([tool, "-w", "1600", svg_path, "-o", png_path], **kwargs)
        elif tool == "inkscape":
            subprocess.run([tool, svg_path, "--export-png", png_path,
                            "--export-width", "1600"], **kwargs)
        elif tool in ("chromium", "chromium-browser", "google-chrome",
                      "google-chrome-stable"):
            subprocess.run([tool, "--headless", "--disable-gpu",
                            "--screenshot=%s" % png_path,
                            "--window-size=1600,1200", "file://" + svg_path], **kwargs)
        elif tool in ("magick", "convert"):
            subprocess.run([tool, "-density", "150", svg_path, png_path], **kwargs)
        elif tool == "wkhtmltoimage":
            subprocess.run([tool, "--width", "1600", svg_path, png_path], **kwargs)
        else:
            return ""
        return png_path if os.path.isfile(png_path) else ""
    except (subprocess.SubprocessError, OSError):
        return ""


# ---------------------------------------------------------------------------
# result.json 契约回填
# ---------------------------------------------------------------------------
def contract_keys(contract) -> list:
    keys = []
    if isinstance(contract, dict):
        keys = list(contract.keys())
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                for cand in ("name", "key", "field", "field_id"):
                    value = item.get(cand)
                    if isinstance(value, str) and value.strip():
                        keys.append(value.strip())
                        break
    ordered, seen = [], set()
    for key in keys:
        if isinstance(key, str) and key and key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


def build_payload(spec, nodes, order, relations, evidence, case_id, status,
                  sources, uncertainties, change_log, limitations, pending,
                  errors, degradations, artifacts, runtime) -> dict:
    flat_nodes = [{
        "id": n["id"], "label": n.get("text", ""), "kind": normalize_kind(n),
        "level": n.get("depth", 0), "parent": n.get("parent"),
        "locator": evidence_of(n)[0], "evidence_class": evidence_of(n)[2],
    } for n in order]
    mindmap_obj = {
        "title": spec.get("title", "") or case_id,
        "file": "mindmap.mm",
        "preview": "mindmap.svg",
        "root_id": order[0]["id"] if order else None,
        "node_count": len(order),
        "relation_count": len(relations),
        "nodes": flat_nodes,
        "relations": [{
            "from": r.get("from"), "to": r.get("to"), "type": r.get("type"),
            "label": r.get("label", ""), "locator": evidence_of(r)[0],
            "evidence_class": evidence_of(r)[2],
        } for r in relations],
    }
    evidence_list = [{
        "node_id": node_id, "text": item.get("text", ""),
        "kind": item.get("kind"), "locator": item.get("locator"),
        "quote": item.get("quote"), "evidence_class": item.get("evidence_class"),
    } for node_id, item in evidence.get("nodes", {}).items()]
    note = (spec.get("meta", {}) or {}).get("purpose", "") or ""
    if not note:
        note = "围绕“%s”的可编辑思维导图：%d 个节点、%d 条关系，证据见 evidence.json。" % (
            mindmap_obj["title"], len(order), len(relations))

    return {
        "case_id": case_id,
        "status": status,
        "mindmap": mindmap_obj,
        "map": mindmap_obj,
        "nodes": flat_nodes,
        "edges": mindmap_obj["relations"],
        "relations": mindmap_obj["relations"],
        "evidence_map": evidence_list,
        "evidence": evidence_list,
        "note": note,
        "summary": note,
        "explanation": note,
        "deliverable": "mindmap.mm",
        "deliverable_path": "mindmap.mm",
        "outline": "outline.md",
        "sources": sources,
        "uncertainty": uncertainties,
        "change_log": change_log,
        "limitations": limitations,
        "pending_human": pending,
        "errors": errors,
        "degradations": degradations,
        "artifacts": artifacts,
        "runtime": runtime,
    }


def fill_contract(keys, payload) -> tuple:
    filled, missing = {}, []
    for key in keys:
        if key in payload:
            filled[key] = payload[key]
        else:
            filled[key] = None
            missing.append(key)
    return filled, missing


def sha256_of(path) -> str:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


def read_json(path):
    try:
        with open(path, "rb") as fh:
            raw = fh.read().decode("utf-8-sig")
        return json.loads(raw), None
    except (OSError, ValueError, TypeError) as exc:
        return None, str(exc)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Render editable mind-map artifacts.")
    parser.add_argument("--spec", default="/app/work/map_spec.json")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--work", default="/app/work")
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    errors, degradations = [], []

    spec = None
    spec, spec_err = read_json(args.spec)
    if spec_err:
        errors.append("map_spec unreadable: %s" % spec_err)

    if not isinstance(spec, dict) or not spec.get("root"):
        spec = {
            "title": "未提供导图规格",
            "case_id": "",
            "meta": {},
            "root": {"id": "n1", "text": "材料不足或规格缺失", "kind": "other",
                     "evidence": None, "children": []},
            "relations": [],
            "limitations": ["map_spec 缺失或为空；未进行语义抽取"],
            "pending_human": ["确认是否提供了材料与任务说明"],
        }
        degradations.append("map_spec missing/empty; placeholder rendered")

    title = str(spec.get("title", "") or "思维导图")
    meta = spec.get("meta", {}) or {}
    root = spec["root"]
    relations = spec.get("relations", []) or []
    limitations = spec.get("limitations", []) or []
    pending = spec.get("pending_human", []) or []
    case_id = str(spec.get("case_id", "") or "")

    # case_id 缺失时回退到 case.json / discovery.json
    if not case_id:
        case, _ = read_json(os.path.join(args.input, "case.json"))
        if isinstance(case, dict):
            for key in ("case_id", "caseId", "id"):
                if isinstance(case.get(key), (str, int, float)) and str(case[key]).strip():
                    case_id = str(case[key])
                    break
    if not case_id:
        discovery, _ = read_json(os.path.join(args.work, "discovery.json"))
        if isinstance(discovery, dict):
            case_id = str(discovery.get("case_id", "") or "")

    nodes, order = collect_nodes(root, errors)
    if len(order) > MAX_NODES:
        degradations.append("node count %d exceeds %d; truncated" % (len(order), MAX_NODES))
        order = order[:MAX_NODES]
        nodes = {n["id"]: n for n in order}
        for n in order:
            n["_children_ids"] = [c for c in n.get("_children_ids", []) if c in nodes]

    valid_relations = []
    for rel in relations:
        if not isinstance(rel, dict):
            degradations.append("relation not object: %r" % (rel,))
            continue
        f, t = rel.get("from"), rel.get("to")
        if f not in nodes or t not in nodes:
            degradations.append("relation endpoint missing: %s -> %s" % (f, t))
            continue
        if rel.get("type") not in REL_TYPES:
            rel["type"] = "other"
            degradations.append("relation type normalized: %s -> %s" % (f, t))
        valid_relations.append(rel)
    relations = valid_relations

    # 渲染各产物
    mm_path = os.path.join(args.output, "mindmap.mm")
    mm_tree = render_mm(root, nodes, relations)
    mm_xml = ET.tostring(mm_tree, encoding="utf-8").decode("utf-8")
    with open(mm_path, "w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n' + mm_xml + "\n")

    svg_text = render_svg(nodes, order, relations, title)
    svg_path = os.path.join(args.output, "mindmap.svg")
    with open(svg_path, "w", encoding="utf-8") as fh:
        fh.write(svg_text)

    outline_text = render_outline(nodes, order, relations, title)
    outline_path = os.path.join(args.output, "outline.md")
    with open(outline_path, "w", encoding="utf-8") as fh:
        fh.write(outline_text)

    evidence = build_evidence(nodes, order, relations, case_id)
    evidence_path = os.path.join(args.output, "evidence.json")
    with open(evidence_path, "w", encoding="utf-8") as fh:
        json.dump(evidence, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    # 可选 PNG 预览
    preview_path = render_preview(svg_path, args.output)
    if not preview_path:
        degradations.append("no usable SVG rasterizer found (rsvg-convert/inkscape/"
                            "chromium/ImageMagick); mindmap.png preview not generated")

    artifacts = {
        "mindmap.mm": {"bytes": os.path.getsize(mm_path),
                        "sha256": sha256_of(mm_path), "type": "freemind_editable"},
        "mindmap.svg": {"bytes": os.path.getsize(svg_path),
                         "sha256": sha256_of(svg_path), "type": "svg_preview"},
        "outline.md": {"bytes": os.path.getsize(outline_path),
                        "sha256": sha256_of(outline_path), "type": "markdown_outline"},
        "evidence.json": {"bytes": os.path.getsize(evidence_path),
                           "sha256": sha256_of(evidence_path), "type": "evidence_map"},
    }
    if preview_path and os.path.isfile(preview_path):
        artifacts["mindmap.png"] = {"bytes": os.path.getsize(preview_path),
                                     "sha256": sha256_of(preview_path), "type": "png_preview"}

    # 状态判定
    key_kinds = {"concept", "question", "method", "evidence", "conclusion",
                 "task", "risk", "gap"}
    missing_evidence = [n for n in order
                        if normalize_kind(n) in key_kinds and not evidence_of(n)[0]]
    status = "ok"
    if errors or missing_evidence or not case_id:
        status = "partial"
    if not order or order[0].get("text", "").startswith("材料不足"):
        status = "insufficient_evidence"

    uncertainties = list(limitations)
    if missing_evidence:
        uncertainties.append("关键节点缺证据定位: %d 个" % len(missing_evidence))

    change_log = [{
        "t": now_iso(), "action": "build_map",
        "detail": "rendered mindmap.mm/svg/outline/evidence and filled result.json",
    }]

    runtime = {
        "started": now_iso(), "skill_version": "1.0.0",
        "nodes": len(order), "relations": len(relations),
        "preview_rendered": bool(preview_path),
    }

    sources = []
    discovery, _ = read_json(os.path.join(args.work, "discovery.json"))
    if isinstance(discovery, dict):
        sources = discovery.get("inputs", [])
        runtime["started"] = discovery.get("scanned_at", runtime["started"])

    payload = build_payload(spec, nodes, order, relations, evidence, case_id, status,
                            sources, uncertainties, change_log, limitations, pending,
                            errors, degradations, artifacts, runtime)

    contract = None
    if isinstance(discovery, dict):
        contract = discovery.get("contract")
    if contract is None:
        case, _ = read_json(os.path.join(args.input, "case.json"))
        if isinstance(case, dict):
            contract = case.get("output_contract")
    result, missing = fill_contract(contract_keys(contract), payload)
    # 保证标准字段总在顶层
    for key, value in payload.items():
        if key not in result:
            result[key] = value
    result["status"] = status if status in ALLOWED_STATUS else "partial"
    if missing:
        result.setdefault("degradations", []).append(
            "contract keys unfilled (no matching payload): %s" % ", ".join(missing))

    result_path = os.path.join(args.output, "result.json")
    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    manifest = {
        "title": title, "case_id": case_id, "status": status,
        "limitations": limitations, "pending_human": pending,
        "errors": errors, "degradations": degradations,
        "artifacts": [{"path": k, "bytes": v["bytes"], "sha256": v["sha256"],
                       "type": v["type"]} for k, v in artifacts.items()],
    }
    with open(os.path.join(args.work, "artifacts.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    print(json.dumps({
        "status": status, "case_id": case_id, "nodes": len(order),
        "relations": len(relations), "errors": errors, "degradations": degradations,
        "contract_missing": missing,
        "artifacts": sorted(artifacts.keys()),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
