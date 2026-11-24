#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render a mind-map JSON into editable SVG + FreeMind .mm + Markdown outline.

Standard library only; deterministic for identical input.
Usage:
  python3 -B scripts/mindmap_render.py --input /app/work/mindmap.json \
      --output-dir /app/output
"""
import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

FONT = "Arial, 'Noto Sans CJK SC', 'PingFang SC', 'Microsoft YaHei', 'DejaVu Sans', sans-serif"

KIND_STYLE = {
    "root": ("#1f4e79", "#ffffff"),
    "concept": ("#2e75b6", "#ffffff"),
    "problem": ("#c0392b", "#ffffff"),
    "method": ("#1e8449", "#ffffff"),
    "evidence": ("#6c7a89", "#ffffff"),
    "conclusion": ("#7d3c98", "#ffffff"),
    "task": ("#ca6f1e", "#ffffff"),
    "risk": ("#a93226", "#ffffff"),
    "gap": ("#b7950b", "#1c2833"),
    "limitation": ("#5d6d7e", "#ffffff"),
}
FALLBACK_STYLE = ("#5d6d7e", "#ffffff")

REL_TEXT = {
    "part_of": "属于", "causal": "导致", "dependency": "依赖", "contrast": "对比",
    "temporal": "时序", "citation": "引用", "support": "支持", "conflict": "冲突",
    "comparison": "比较", "unknown": "相关",
}
# relation -> (color, dash pattern, draw arrowhead)
EDGE_DEF = {
    "part_of": ("#5d6d7e", "", False),
    "causal": ("#1f4e79", "", True),
    "dependency": ("#5d6d7e", "", True),
    "contrast": ("#7f8c8d", "6,4", False),
    "temporal": ("#5d6d7e", "3,3", True),
    "citation": ("#6c3483", "", True),
    "support": ("#1e8449", "", True),
    "conflict": ("#c0392b", "6,4", False),
    "comparison": ("#7f8c8d", "6,4", False),
    "unknown": ("#95a5a6", "2,4", False),
}
ARROW_MARKER = {
    "#1f4e79": "arrow-blue",
    "#6c3483": "arrow-purple",
    "#1e8449": "arrow-green",
    "#5d6d7e": "arrow-gray",
    "#c0392b": "arrow-red",
    "#7f8c8d": "arrow-gray",
    "#95a5a6": "arrow-gray",
}

FONT_SIZE = 14
LINE_HEIGHT = 20
PAD_X = 14
PAD_Y = 10
MAX_NODE_WIDTH = 280
MIN_NODE_WIDTH = 64
H_SPACING = 360
V_GAP = 28


def char_units(ch):
    """Return approximate width units for a character."""
    return 1.0 if ord(ch) > 0x2E80 else 0.55


def text_width(s, font_size):
    return sum(char_units(c) for c in s) * font_size * 0.62


def wrap_text(text, font_size, max_width):
    text = str(text)
    max_units = max_width / (font_size * 0.62)
    lines = []
    cur = ""
    cur_units = 0.0
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            cur_units = 0.0
            continue
        u = char_units(ch)
        if cur_units + u > max_units and cur:
            lines.append(cur)
            cur = ch if ch != " " else ""
            cur_units = u if ch != " " else 0.0
        else:
            cur += ch
            cur_units += u
    if cur:
        lines.append(cur)
    lines = [ln.rstrip() for ln in lines]
    return lines or [""]


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def load_mindmap(path):
    try:
        raw = Path(path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        print(f"error: cannot read mindmap: {exc}", file=sys.stderr)
        sys.exit(2)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"error: mindmap is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(2)
    return data


def build_forest(nodes):
    by_id = {}
    for i, n in enumerate(nodes):
        if not isinstance(n, dict):
            continue
        nid = str(n.get("id", f"n{i}"))
        n["_id"] = nid
        by_id[nid] = n
    children = {nid: [] for nid in by_id}
    roots = []
    for n in by_id.values():
        pid = n.get("parent")
        if pid is not None and str(pid) in by_id and str(pid) != n["_id"]:
            children[str(pid)].append(n["_id"])
        else:
            roots.append(n["_id"])
    return by_id, children, roots


def assign_slots(root, children, depth, x_of, y_of, counter, visiting):
    if root in visiting:
        return None
    visiting.add(root)
    kids = [k for k in children.get(root, []) if k not in visiting]
    if not kids:
        slot = counter[0]
        counter[0] += 1
        x_of[root] = depth
        y_of[root] = slot
        visiting.remove(root)
        return (slot, slot, slot)
    first = None
    last = None
    for k in kids:
        r = assign_slots(k, children, depth + 1, x_of, y_of, counter, visiting)
        if r is None:
            continue
        a, b, _c = r
        first = a if first is None or a < first else first
        last = b if last is None or b > last else last
    if first is None:
        slot = counter[0]
        counter[0] += 1
        x_of[root] = depth
        y_of[root] = slot
    else:
        x_of[root] = depth
        y_of[root] = (first + last) / 2.0
    visiting.remove(root)
    return (first, last, (first + last) / 2.0)


def edge_style(rel):
    rel = str(rel or "unknown")
    return EDGE_DEF.get(rel, EDGE_DEF["unknown"])


def build_svg(mindmap):
    nodes = mindmap.get("nodes") or []
    edges = mindmap.get("edges") or []
    title = str(mindmap.get("title") or "Mind Map")

    if not nodes:
        return _empty_svg(title)

    by_id, children, roots = build_forest(nodes)
    if not roots:
        return _empty_svg(title)

    x_of = {}
    y_of = {}
    counter = [0]
    visiting = set()
    for r in roots:
        assign_slots(r, children, 0, x_of, y_of, counter, visiting)

    heights = {}
    labels = {}
    for n in by_id.values():
        kind = n.get("kind", "concept")
        fill, text = KIND_STYLE.get(kind, FALLBACK_STYLE)
        lines = wrap_text(n.get("label", ""), FONT_SIZE, MAX_NODE_WIDTH - 2 * PAD_X)
        labels[n["_id"]] = lines
        w = max([text_width(ln, FONT_SIZE) for ln in lines] + [0])
        w = max(MIN_NODE_WIDTH - 2 * PAD_X, min(MAX_NODE_WIDTH - 2 * PAD_X, w)) + 2 * PAD_X
        h = len(lines) * LINE_HEIGHT + 2 * PAD_Y
        heights[n["_id"]] = (w, h, fill, text)

    max_h = max(v[1] for v in heights.values()) if heights else 60
    pitch = max_h + V_GAP
    top_margin = 60
    left_margin = 60

    pos = {}
    for nid in by_id:
        slot = y_of.get(nid, 0)
        depth = x_of.get(nid, 0)
        w, h, _fill, _text = heights[nid]
        cx = left_margin + depth * H_SPACING + w / 2.0
        cy = top_margin + slot * pitch + pitch / 2.0
        pos[nid] = (cx, cy, w, h)

    max_x = max(p[0] + p[2] / 2.0 for p in pos.values()) + 60
    max_y = max(p[1] + p[3] / 2.0 for p in pos.values()) + 60

    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{max_x:.0f}" height="{max_y:.0f}" '
        f'viewBox="0 0 {max_x:.0f} {max_y:.0f}" font-family="{FONT}">'
    )
    parts.append("<defs>")
    for color, mid in ARROW_MARKER.items():
        parts.append(
            f'<marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" '
            f'markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
            f'<path d="M0,0 L10,5 L0,10 z" fill="{color}"/></marker>'
        )
    parts.append("</defs>")
    parts.append(
        '<rect x="0" y="0" width="100%" height="100%" fill="#ffffff"/>'
    )
    parts.append(
        f'<text x="{left_margin}" y="30" font-size="20" font-weight="bold" fill="#1c2833">'
        f'{esc(title)}</text>'
    )

    # hierarchy (tree) edges, light gray
    parts.append('<g stroke="#b0b7bd" stroke-width="1.2" fill="none">')
    for nid, n in by_id.items():
        pid = n.get("parent")
        if pid is None or str(pid) not in pos or str(pid) == nid:
            continue
        x1, y1, w1, _h1 = pos[str(pid)]
        x2, y2, _w2, _h2 = pos[nid]
        sx = x1 + w1 / 2.0
        tx = x2 - pos[nid][2] / 2.0
        dx = max(40.0, (tx - sx) * 0.4)
        parts.append(
            f'<path d="M{sx:.1f},{y1:.1f} C{sx + dx:.1f},{y1:.1f} '
            f'{tx - dx:.1f},{y2:.1f} {tx:.1f},{y2:.1f}"/>'
        )
    parts.append("</g>")

    # explicit relation edges
    if edges:
        parts.append('<g fill="none" stroke-width="1.6">')
        for e in edges:
            if not isinstance(e, dict):
                continue
            src = str(e.get("from", ""))
            dst = str(e.get("to", ""))
            if src not in pos or dst not in pos or src == dst:
                continue
            rel = e.get("relation", "unknown")
            color, dash, arrow = edge_style(rel)
            dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
            marker = f' marker-end="url(#{ARROW_MARKER.get(color, "arrow-gray")})"' if arrow else ""
            x1, y1, w1, _h1 = pos[src]
            x2, y2, w2, _h2 = pos[dst]
            sx = x1 + w1 / 2.0
            tx = x2 - w2 / 2.0
            dx = max(40.0, abs(tx - sx) * 0.45)
            parts.append(
                f'<path d="M{sx:.1f},{y1:.1f} C{sx + dx:.1f},{y1:.1f} '
                f'{tx - dx:.1f},{y2:.1f} {tx:.1f},{y2:.1f}" '
                f'stroke="{color}"{dash_attr}{marker}/>'
            )
            lbl = e.get("label") or REL_TEXT.get(rel, rel)
            mx = (sx + tx) / 2.0
            my = (y1 + y2) / 2.0 - 8
            lw = text_width(str(lbl), 12) + 8
            parts.append(
                f'<g font-size="12" fill="{color}">'
                f'<rect x="{mx - lw / 2.0:.1f}" y="{my - 9:.1f}" width="{lw:.1f}" '
                f'height="15" fill="#ffffff" fill-opacity="0.9" stroke="none"/>'
                f'<text x="{mx:.1f}" y="{my:.1f}" text-anchor="middle">{esc(str(lbl))}</text></g>'
            )
        parts.append("</g>")

    # nodes
    parts.append('<g font-size="' + str(FONT_SIZE) + '">')
    for nid, n in by_id.items():
        cx, cy, w, h = pos[nid]
        fill, text = heights[nid][2], heights[nid][3]
        lines = labels[nid]
        x = cx - w / 2.0
        y = cy - h / 2.0
        rx = 8
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'rx="{rx}" fill="{fill}" stroke="#34495e" stroke-width="1"/>'
        )
        ty = y + PAD_Y + LINE_HEIGHT * 0.78
        for ln in lines:
            parts.append(
                f'<text x="{cx:.1f}" y="{ty:.1f}" text-anchor="middle" fill="{text}">{esc(ln)}</text>'
            )
            ty += LINE_HEIGHT
    parts.append("</g>")
    parts.append("</svg>")
    return "\n".join(parts)


def _empty_svg(title):
    w, h = 720, 360
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" font-family="{FONT}">\n'
        f'<rect x="0" y="0" width="{w}" height="{h}" fill="#ffffff"/>\n'
        f'<text x="{w/2}" y="40" font-size="20" font-weight="bold" '
        f'text-anchor="middle" fill="#1c2833">{esc(title)}</text>\n'
        f'<text x="{w/2}" y="{h/2}" font-size="16" text-anchor="middle" '
        f'fill="#7f8c8d">无可用节点（insufficient_evidence）</text>\n'
        '</svg>\n'
    )


def build_mm(mindmap):
    nodes = mindmap.get("nodes") or []
    by_id, children, roots = build_forest(nodes)

    def node_el(n):
        nid = n["_id"]
        el = ET.Element("node")
        el.set("TEXT", str(n.get("label", "")))
        el.set("ID", "ID_" + "".join(ch if ch.isalnum() else "_" for ch in nid))
        el.set("POSITION", "right" if n.get("parent") is not None else "center")
        el.set("FOLDED", "false")
        note = str(n.get("note") or "")
        ev = n.get("evidence") or []
        locs = "; ".join(
            f"{str(e.get('locator', ''))}" for e in ev if isinstance(e, dict) and e.get("locator")
        )
        if note or locs:
            note_text = note + (f" [{locs}]" if locs else "")
            el.set("NOTE", note_text[:2000])
        for kid in children.get(nid, []):
            el.append(node_el(by_id[kid]))
        return el

    root = ET.Element("map")
    root.set("version", "1.0.1")
    if roots:
        for r in roots:
            root.append(node_el(by_id[r]))
    else:
        empty = ET.SubElement(root, "node")
        empty.set("TEXT", "insufficient_evidence")
    return ET.tostring(root, encoding="unicode")


def build_outline(mindmap):
    nodes = mindmap.get("nodes") or []
    by_id, children, roots = build_forest(nodes)
    lines = ["# " + str(mindmap.get("title") or "思维导图大纲"), ""]
    if not roots:
        lines.append("- （无可用节点：insufficient_evidence）")
        return "\n".join(lines) + "\n"

    def walk(nid, depth):
        n = by_id[nid]
        kind = n.get("kind", "concept")
        prefix = "  " * depth + "- "
        ev = n.get("evidence") or []
        locs = "; ".join(
            str(e.get("locator", "")) for e in ev if isinstance(e, dict) and e.get("locator")
        )
        suffix = f"  `[{kind}]`" + (f"  @ {locs}" if locs else "")
        inferred = "  （推断）" if n.get("inferred") else ""
        lines.append(prefix + "**" + str(n.get("label", "")) + "**" + suffix + inferred)
        for kid in children.get(nid, []):
            walk(kid, depth + 1)

    for r in roots:
        walk(r, 0)
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output-dir", required=True)
    args = ap.parse_args()
    mindmap = load_mindmap(args.input)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "mindmap.svg").write_text(build_svg(mindmap), encoding="utf-8")
    (out / "mindmap.mm").write_text(build_mm(mindmap), encoding="utf-8")
    (out / "outline.md").write_text(build_outline(mindmap), encoding="utf-8")
    n = len(mindmap.get("nodes") or [])
    e = len(mindmap.get("edges") or [])
    print(json.dumps({"status": "ok", "nodes": n, "edges": e,
                      "outputs": ["mindmap.svg", "mindmap.mm", "outline.md"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
