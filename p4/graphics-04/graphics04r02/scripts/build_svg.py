#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IR -> layered, editable, traceable SVG. Standard library only.

Usage: python build_svg.py --ir /app/work/ir.json --out /app/work/figure.svg
Deterministic: same IR in -> byte-identical SVG out (no randomness, no timestamps).
"""
import argparse
import json
import os
import sys
from xml.sax.saxutils import escape, quoteattr

PALETTE = {
    "flow": {"stroke": "#1d4ed8", "dash": None, "label": "物料/流体流向", "width": 2.4},
    "material": {"stroke": "#1d4ed8", "dash": None, "label": "物料流向", "width": 2.4},
    "energy": {"stroke": "#dc2626", "dash": None, "label": "能量流向", "width": 2.4},
    "info": {"stroke": "#059669", "dash": "6,4", "label": "信息/信号", "width": 2.0},
    "signal": {"stroke": "#059669", "dash": "6,4", "label": "信息/信号", "width": 2.0},
    "sequence": {"stroke": "#334155", "dash": None, "label": "步骤顺序", "width": 2.2},
    "return": {"stroke": "#9333ea", "dash": "6,4", "label": "回流/反馈", "width": 2.0},
    "feedback": {"stroke": "#9333ea", "dash": "6,4", "label": "回流/反馈", "width": 2.0},
    "cycle": {"stroke": "#9333ea", "dash": "6,4", "label": "循环回流", "width": 2.0},
    "waste": {"stroke": "#b45309", "dash": "2,3", "label": "废料/排放", "width": 1.8},
}

SHAPES = {
    "start": "stadium", "end": "stadium", "step": "rect", "process": "rect",
    "decision": "diamond", "stage": "stadium", "milestone": "stadium",
    "vessel": "rect", "equipment": "rect", "unit": "rect", "reactor": "rect",
    "pump": "circle", "compressor": "circle", "sensor": "circle",
    "instrument": "circle", "valve": "diamond", "branch": "diamond",
    "material": "stadium", "input": "stadium", "output": "stadium",
}

FILLS = {
    "start": "#dcfce7", "end": "#fef2f2", "decision": "#fef9c3", "valve": "#fef9c3",
    "stage": "#e0f2fe", "milestone": "#e0f2fe", "pump": "#fce7f3",
    "sensor": "#f1f5f9", "instrument": "#f1f5f9", "material": "#f5f3ff",
    "input": "#f5f3ff", "output": "#f5f3ff", "waste": "#fff7ed",
}

STROKES = {
    "start": "#166534", "end": "#991b1b", "decision": "#a16207", "valve": "#a16207",
    "stage": "#075985", "milestone": "#075985", "pump": "#9d174d",
    "sensor": "#334155", "instrument": "#334155", "material": "#6d28d9",
    "input": "#6d28d9", "output": "#6d28d9", "waste": "#9a3412",
}

DEFAULT_W, DEFAULT_H = 168.0, 64.0
MARGIN = 40.0
GAP_X, GAP_Y = 72.0, 96.0
FONT = "Noto Sans SC, PingFang SC, Microsoft YaHei, sans-serif"


def norm(s):
    return "" if s is None else str(s)


def shape_for(entity_type):
    return SHAPES.get(norm(entity_type).lower(), "rect")


def fill_for(entity_type, entity):
    return entity.get("fill") or FILLS.get(norm(entity_type).lower(), "#eff6ff")


def stroke_for(entity_type, entity):
    return entity.get("stroke") or STROKES.get(norm(entity_type).lower(), "#1e3a8a")


def marker_color(kind):
    return PALETTE.get(norm(kind).lower(), PALETTE["sequence"])["stroke"]


def wrap_label(text, max_chars):
    text = norm(text)
    if not text:
        return []
    out, line = [], ""
    for ch in text:
        line += ch
        if len(line) >= max_chars:
            out.append(line)
            line = ""
    if line:
        out.append(line)
    return out[:4] or [text]


def load_ir(path):
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        return json.load(fh)


def compute_layout(ir):
    entities = ir.get("entities") or []
    auto = any(e.get("x") is None or e.get("y") is None for e in entities) or not entities
    boxes = []
    if not auto:
        for e in entities:
            boxes.append({
                "id": e.get("id"),
                "x": float(e.get("x", 0)), "y": float(e.get("y", 0)),
                "w": float(e.get("w", DEFAULT_W)), "h": float(e.get("h", DEFAULT_H)),
                "shape": shape_for(e.get("type")),
            })
        return boxes, False

    cols = int(ir.get("layout", {}).get("columns") or (3 if len(entities) > 3 else max(1, len(entities))))
    max_w = max(DEFAULT_W, max((float(e.get("w", DEFAULT_W)) for e in entities), default=DEFAULT_W))
    max_h = max(DEFAULT_H, max((float(e.get("h", DEFAULT_H)) for e in entities), default=DEFAULT_H))
    boxes = []
    for i, e in enumerate(entities):
        c, r = i % cols, i // cols
        w = float(e.get("w", DEFAULT_W))
        h = float(e.get("h", DEFAULT_H))
        x = MARGIN + c * (max_w + GAP_X) + (max_w - w) / 2.0
        y = MARGIN + r * (max_h + GAP_Y) + (max_h - h) / 2.0
        boxes.append({"id": e.get("id"), "x": x, "y": y, "w": w, "h": h, "shape": shape_for(e.get("type"))})
    return boxes, True


def box_center(box):
    return (box["x"] + box["w"] / 2.0, box["y"] + box["h"] / 2.0)


def intersect_rect(cx, cy, tx, ty, box):
    """Point where the line (cx,cy)->(tx,ty) exits the node rectangle."""
    if abs(tx - cx) < 1e-9 and abs(ty - cy) < 1e-9:
        return cx, cy
    dx, dy = tx - cx, ty - cy
    left, right = box["x"], box["x"] + box["w"]
    top, bottom = box["y"], box["y"] + box["h"]
    ts = []
    if dx != 0:
        ts.append(((left - cx) / dx, (left, cy + dy * (left - cx) / dx)))
        ts.append(((right - cx) / dx, (right, cy + dy * (right - cx) / dx)))
    if dy != 0:
        ts.append(((top - cy) / dy, (cx + dx * (top - cy) / dy, top)))
        ts.append(((bottom - cy) / dy, (cx + dx * (bottom - cy) / dy, bottom)))
    candidates = [(t, (px, py)) for t, (px, py) in ts
                  if t > 1e-6 and left - 1e-6 <= px <= right + 1e-6 and top - 1e-6 <= py <= bottom + 1e-6]
    if not candidates:
        return cx, cy
    t, pt = min(candidates, key=lambda item: item[0])
    return pt


def relation_path(fr, to, boxes, kind):
    """Return (d_path, is_return_lane)."""
    fbox = next((b for b in boxes if b["id"] == fr["id"]), None)
    tbox = next((b for b in boxes if b["id"] == to["id"]), None)
    if fbox is None or tbox is None:
        return None, False
    fc = box_center(fbox)
    tc = box_center(tbox)
    k = norm(kind).lower()
    if k in ("return", "feedback", "cycle"):
        y_lane = max(b["y"] + b["h"] for b in boxes) + 30.0
        x1 = fc[0]
        x2 = tc[0]
        d = (f"M {x1:.2f} {fbox['y'] + fbox['h']:.2f} "
             f"L {x1:.2f} {y_lane:.2f} "
             f"L {x2:.2f} {y_lane:.2f} "
             f"L {x2:.2f} {tbox['y'] + tbox['h']:.2f}")
        return d, True
    p1 = intersect_rect(fc[0], fc[1], tc[0], tc[1], fbox)
    p2 = intersect_rect(tc[0], tc[1], fc[0], fc[1], tbox)
    return f"M {p1[0]:.2f} {p1[1]:.2f} L {p2[0]:.2f} {p2[1]:.2f}", False


def render_svg(ir, out_path):
    entities = ir.get("entities") or []
    relations = ir.get("relations") or []
    boxes, auto = compute_layout(ir)
    emap = {b["id"]: b for b in boxes}
    title = norm(ir.get("title") or ir.get("kind") or "figure")
    canvas = ir.get("canvas") or {}
    width = float(canvas.get("width") or 1200)
    height = float(canvas.get("height") or 900)

    used_kinds = sorted({norm(r.get("kind", "flow")).lower() for r in relations})
    defs_markers = []
    for kind in used_kinds:
        color = marker_color(kind)
        defs_markers.append(
            f'<marker id="arrow-{kind}" viewBox="0 0 10 10" refX="8" refY="5" '
            f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{color}"/></marker>'
        )

    parts = []
    parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:g}" height="{height:g}" '
                 f'viewBox="0 0 {width:g} {height:g}" font-family="{FONT}">')
    parts.append("<defs>" + "".join(defs_markers) + "</defs>")

    # Background + title
    parts.append(f'<rect x="0" y="0" width="{width:g}" height="{height:g}" fill="#ffffff"/>')
    parts.append(f'<text x="{MARGIN:g}" y="26" font-size="18" font-weight="700" fill="#0f172a">{escape(title)}</text>')

    # relations layer
    parts.append('<g id="layer-relations">')
    for r in relations:
        fr = {"id": r.get("from")}
        to = {"id": r.get("to")}
        kind = norm(r.get("kind", "flow")).lower()
        style = norm(r.get("style")).lower()
        direction = norm(r.get("direction", "forward")).lower()
        if direction == "reverse":
            fr, to = to, fr
        d, is_lane = relation_path(fr, to, boxes, kind)
        if d is None:
            continue
        pal = PALETTE.get(kind, PALETTE["sequence"])
        dash = pal["dash"] if style != "solid" else None
        if style == "dashed":
            dash = dash or "6,4"
        width_px = float(r.get("width") or pal["width"])
        src_ref = norm(r.get("source_ref"))
        quote = norm(r.get("quote"))
        label = norm(r.get("label"))
        attrs = f'data-source-ref={quoteattr(src_ref)} data-kind={quoteattr(kind)}'
        marker_end = f'marker-end="url(#arrow-{kind})"'
        marker_start = 'marker-start="url(#arrow-{kind})"' if direction == "bidirectional" else ""
        extra = f'stroke-dasharray="{dash}"' if dash else ""
        path = (f'<path d="{d}" fill="none" stroke="{pal["stroke"]}" stroke-width="{width_px:g}" '
                f'{marker_end}{marker_start} {extra} {attrs}>')
        if quote:
            path = path.replace(">", f'><title>{escape(quote)}</title>', 1)
        path += "</path>"
        parts.append(path)
        if label:
            mx = (emap.get(fr["id"], {}).get("x", 0) + emap.get(to["id"], {}).get("x", 0)) / 2.0
            my = (emap.get(fr["id"], {}).get("y", 0) + emap.get(to["id"], {}).get("y", 0)) / 2.0
            parts.append(f'<text x="{mx:g}" y="{my:g}" font-size="12" fill="{pal["stroke"]}" '
                         f'text-anchor="middle" {attrs}>{escape(label)}</text>')
    parts.append("</g>")

    # entities layer
    parts.append('<g id="layer-entities">')
    for e in entities:
        box = emap.get(e.get("id"))
        if box is None:
            continue
        x, y, w, h = box["x"], box["y"], box["w"], box["h"]
        shape = box["shape"]
        fill = fill_for(e.get("type"), e)
        stroke = stroke_for(e.get("type"), e)
        src_ref = norm(e.get("source_ref"))
        quote = norm(e.get("quote"))
        label = norm(e.get("label"))
        attrs = f'data-source-ref={quoteattr(src_ref)} data-entity-id={quoteattr(norm(e.get("id")))}'
        inner = ""
        if shape == "circle":
            r = min(w, h) / 2.0
            inner = f'<ellipse cx="{x + w / 2:g}" cy="{y + h / 2:g}" rx="{r:g}" ry="{r:g}" fill="{fill}" stroke="{stroke}" stroke-width="1.6" {attrs}/>'
        elif shape == "diamond":
            cx, cy = x + w / 2.0, y + h / 2.0
            pts = f"{cx:g},{y:g} {x + w:g},{cy:g} {cx:g},{y + h:g} {x:g},{cy:g}"
            inner = f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="1.6" {attrs}/>'
        elif shape == "stadium":
            rx = min(16.0, h / 2.0)
            inner = f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" rx="{rx:g}" fill="{fill}" stroke="{stroke}" stroke-width="1.6" {attrs}/>'
        else:
            rx = float(e.get("rx", 6))
            inner = f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" rx="{rx:g}" fill="{fill}" stroke="{stroke}" stroke-width="1.6" {attrs}/>'
        if quote:
            tag = inner.split()[0].lstrip("<").rstrip(">")
            inner = inner.replace("/>", f'><title>{escape(quote)}</title></{tag}>', 1)
        parts.append(inner)
        # label centered
        lines = wrap_label(label, max(4, int(w / 14)))
        lh = 15.0
        start_y = y + h / 2.0 - (len(lines) - 1) * lh / 2.0 + 4.0
        for i, line in enumerate(lines):
            parts.append(f'<text x="{x + w / 2:g}" y="{start_y + i * lh:g}" font-size="13" '
                         f'fill="#0f172a" text-anchor="middle" {attrs}>{escape(line)}</text>')
        note = norm(e.get("note"))
        if note:
            parts.append(f'<text x="{x + w / 2:g}" y="{y + h + 14:g}" font-size="11" fill="#475569" '
                         f'text-anchor="middle" {attrs}>{escape(note)}</text>')
    parts.append("</g>")

    # annotations / data encoding layer
    parts.append('<g id="layer-annotations">')
    for item in ir.get("data_encoding") or []:
        key = norm(item.get("key"))
        value = norm(item.get("value"))
        visual = norm(item.get("visual"))
        src_ref = norm(item.get("source_ref"))
        quote = norm(item.get("quote"))
        if not (key or value or visual):
            continue
        text = f"{key} = {value} -> {visual}".strip(" =->")
        parts.append(f'<text x="{MARGIN:g}" y="{height - 40:g}" font-size="12" fill="#334155" '
                     f'data-source-ref={quoteattr(src_ref)}>{escape(text)}</text>')
        if quote:
            parts.append(f'<text x="{MARGIN:g}" y="{height - 24:g}" font-size="10" fill="#94a3b8" '
                         f'data-source-ref={quoteattr(src_ref)}>{escape(quote[:80])}</text>')
        height += 0  # keep single row simple
    for item in ir.get("annotations") or []:
        x = float(item.get("x", MARGIN))
        y = float(item.get("y", height - 60))
        parts.append(f'<text x="{x:g}" y="{y:g}" font-size="12" fill="#334155" '
                     f'data-source-ref={quoteattr(norm(item.get("source_ref")))}>{escape(norm(item.get("text")))}</text>')
    parts.append("</g>")

    # legend layer
    parts.append('<g id="layer-legend">')
    legend = ir.get("legend") or []
    if legend:
        lx = width - 260
        ly = 26
        parts.append(f'<rect x="{lx - 10:g}" y="{ly - 18:g}" width="250" height="{max(24, 20 * len(legend) + 26):g}" '
                     f'fill="#ffffff" stroke="#cbd5e1" stroke-width="1"/>')
        for i, item in enumerate(legend):
            key = norm(item.get("key"))
            visual = norm(item.get("visual"))
            label = norm(item.get("label", key))
            src_ref = norm(item.get("source_ref"))
            quote = norm(item.get("quote"))
            row_y = ly + i * 20
            parts.append(f'<line x1="{lx:g}" y1="{row_y - 4:g}" x2="{lx + 30:g}" y2="{row_y - 4:g}" '
                         f'stroke="#334155" stroke-width="2" data-source-ref={quoteattr(src_ref)}/>')
            parts.append(f'<text x="{lx + 38:g}" y="{row_y:g}" font-size="12" fill="#0f172a" '
                         f'data-source-ref={quoteattr(src_ref)}>{escape(f"{label} ({visual})")}</text>')
            if quote:
                parts.append(f'<title>{escape(quote)}</title>')
    else:
        lx = width - 260
        for i, kind in enumerate(used_kinds):
            pal = PALETTE.get(kind, PALETTE["sequence"])
            dash = f'stroke-dasharray="{pal["dash"]}"' if pal["dash"] else ""
            parts.append(f'<line x1="{lx:g}" y1="{26 + i * 20:g}" x2="{lx + 30:g}" y2="{26 + i * 20:g}" '
                         f'stroke="{pal["stroke"]}" stroke-width="2" {dash}/>')
            parts.append(f'<text x="{lx + 38:g}" y="{30 + i * 20:g}" font-size="12" fill="#0f172a">'
                         f'{escape(pal["label"])}</text>')
    parts.append("</g>")

    # metadata: traceable IR summary + auto-layout flag
    meta = {
        "kind": ir.get("kind"),
        "auto_layout": auto,
        "entity_count": len(entities),
        "relation_count": len(relations),
        "source_ref_required": True,
        "note": "layers: layer-relations, layer-entities, layer-annotations, layer-legend",
    }
    parts.append("<metadata>" + escape(json.dumps(meta, ensure_ascii=False)) + "</metadata>")
    parts.append("</svg>")
    svg_text = "\n".join(parts)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(svg_text)
    return {"out": out_path, "auto_layout": auto, "entities": len(entities), "relations": len(relations)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="IR -> layered editable SVG")
    ap.add_argument("--ir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    try:
        ir = load_ir(args.ir)
        result = render_svg(ir, args.out)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    sys.exit(main())
