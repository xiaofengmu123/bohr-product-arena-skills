#!/usr/bin/env python3
"""Deterministic layered-SVG renderer for figure_spec.json.

Pure standard library. No randomness, no wall-clock, no environment-dependent
font resolution: the same spec bytes render to byte-identical SVG. This is the
determinism guarantee checked by selftest.py.

Grammar: references/figure-grammar.md
"""
import argparse
import json
import sys
import xml.sax.saxutils as saxutils

# Fixed palette: category colors are assigned in first-appearance order when a
# legend item carries no explicit color. Deterministic and reusable.
PALETTE = [
    "#1f77b4", "#d62728", "#2ca02c", "#ff7f0e",
    "#9467bd", "#8c564b", "#e377c2", "#17becf",
    "#bcbd22", "#7f7f7f", "#393b79", "#637939",
]

KIND_GLYPH = {
    "activation": "arrow",
    "inhibition": "bar",
    "upstream": "arrow",
    "downstream": "arrow",
    "causal": "arrow",
    "temporal": "arrow",
    "maps_to": "dashed",
    "parallel": "plain",
}

ENTITY_KIND_ORDER = {"group": 0, "node": 1, "process": 2, "annotation": 3}


def esc(s):
    return saxutils.escape(str(s), {'"': "&quot;", "'": "&apos;"})


def load_spec(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def validate_spec(spec):
    errors = []
    if not isinstance(spec, dict):
        raise ValueError("spec 顶层必须是 JSON object")
    for key in ("figure_id", "entities", "relations"):
        if key not in spec:
            errors.append("缺少必填字段: %s" % key)
    entities = spec.get("entities", [])
    ids = {}
    for e in entities:
        eid = e.get("id")
        if not eid:
            errors.append("实体缺少 id")
            continue
        if eid in ids:
            errors.append("实体 id 重复: %s" % eid)
        ids[eid] = e
        kind = e.get("kind")
        if kind not in ENTITY_KIND_ORDER:
            errors.append("实体 %s 的 kind 非法: %r" % (eid, kind))
    for r in spec.get("relations", []):
        for end in ("from", "to"):
            if r.get(end) not in ids:
                errors.append("关系 %s 的 %s 引用不存在实体: %r"
                              % (r.get("id"), end, r.get(end)))
        if r.get("kind") not in KIND_GLYPH:
            errors.append("关系 %s 的 kind 非法: %r"
                          % (r.get("id"), r.get("kind")))
    if errors:
        raise ValueError("spec 校验失败:\n- " + "\n- ".join(errors))
    return spec


def assign_colors(spec):
    """category -> color, deterministic: explicit color wins, else palette."""
    colors = {}
    used = 0
    legend = spec.get("legend") or {}
    for item in legend.get("items", []):
        cat = item.get("category") or item.get("id")
        if cat in colors:
            continue
        color = item.get("color")
        if not color:
            color = PALETTE[used % len(PALETTE)]
            used += 1
        colors[cat] = color
    # entities with a category that has no legend item still get a color
    for e in spec.get("entities", []):
        cat = e.get("category")
        if cat and cat not in colors:
            colors[cat] = PALETTE[used % len(PALETTE)]
            used += 1
    return colors


def layout(spec):
    """Return {id: (x, y, w, h)}. Explicit x/y win; the rest is a pure function."""
    canvas = spec.get("canvas") or {}
    W = int(canvas.get("width", 2400))
    H = int(canvas.get("height", 1400))
    M = int(canvas.get("margin", 80))
    CELL_H = 88
    PAD = 16
    GROUP_GAP = 64
    GROUP_HEADER = 64

    entities = spec.get("entities", [])
    pos = {}
    for e in entities:
        if e.get("x") is not None and e.get("y") is not None:
            pos[e["id"]] = (
                float(e["x"]), float(e["y"]),
                float(e.get("w") or 0), float(e.get("h") or 0),
            )

    def needs(e):
        return e["id"] not in pos

    groups = sorted(
        [e for e in entities if e.get("kind") == "group" and needs(e)],
        key=lambda e: e["id"],
    )
    ng = len(groups)
    group_bottom = M
    if ng:
        total_gap = (ng - 1) * GROUP_GAP
        gw = max(200.0, (W - 2 * M - total_gap) / ng)
        for i, g in enumerate(groups):
            gx = M + i * (gw + GROUP_GAP)
            members = [
                e for e in entities
                if e.get("parent") == g["id"] and needs(e)
            ]
            gy = M + GROUP_HEADER
            for j, m in enumerate(members):
                pos[m["id"]] = (gx + PAD, gy + j * CELL_H, gw - 2 * PAD,
                                CELL_H - PAD)
            gh = GROUP_HEADER + max(1, len(members)) * CELL_H + PAD
            pos[g["id"]] = (gx, M, gw, gh)
            group_bottom = max(group_bottom, M + gh)

    # Ungrouped non-group entities in a deterministic grid below the group row.
    leaves = sorted(
        [e for e in entities
         if e.get("kind") != "group" and not e.get("parent") and needs(e)],
        key=lambda e: e["id"],
    )
    if leaves:
        row = 0
        col = 0
        per_row = max(1, int((W - 2 * M) / 320))
        top = group_bottom + M if ng else M
        for m in leaves:
            lw = 300.0
            lh = float(CELL_H - PAD)
            lx = M + col * (lw + 24)
            ly = top + row * (CELL_H + 24)
            pos[m["id"]] = (lx, ly, lw, lh)
            col += 1
            if col >= per_row:
                col = 0
                row += 1

    # Fill remaining sizes with sensible defaults so edges can anchor.
    out = {}
    for e in entities:
        x, y, w, h = pos.get(
            e["id"],
            (float(M), float(M), 0.0, 0.0),
        )
        if w <= 0:
            w = 300.0 if e.get("kind") == "group" else 220.0
        if h <= 0:
            h = CELL_H if e.get("kind") == "group" else 52.0
        out[e["id"]] = (x, y, w, h)
    return out


def center(pos, eid):
    x, y, w, h = pos[eid]
    return x + w / 2.0, y + h / 2.0


def render(spec, colors, pos):
    parts = []
    canvas = spec.get("canvas") or {}
    W = int(canvas.get("width", 2400))
    H = int(canvas.get("height", 1400))
    title = spec.get("title") or ""
    fid = spec.get("figure_id") or "figure"
    parts.append(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" font-family="Arial, Helvetica, sans-serif">'
        % (W, H, W, H)
    )
    parts.append(
        "<defs>\n"
        '<marker id="arrow-%s" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" fill="#222"/></marker>'
        "</defs>" % fid
    )
    parts.append('<g id="layer-background">'
                 '<rect x="0" y="0" width="%d" height="%d" fill="#ffffff"/>'
                 "</g>" % (W, H))

    if title:
        parts.append(
            '<g id="layer-title"><text x="%d" y="%d" font-size="40" '
            'font-weight="bold" fill="#111">%s</text></g>'
            % (W // 2, 52, esc(title))
        )

    # Subfigure panels
    parts.append('<g id="layer-panels">')
    for sf in spec.get("subfigures", []):
        ids = sf.get("entity_ids", [])
        if not ids:
            continue
        xs = [pos[i][0] for i in ids if i in pos]
        ys = [pos[i][1] for i in ids if i in pos]
        x2 = [pos[i][0] + pos[i][2] for i in ids if i in pos]
        y2 = [pos[i][1] + pos[i][3] for i in ids if i in pos]
        if not xs:
            continue
        px, py = min(xs) - 24, min(ys) - 40
        pw, ph = max(x2) - min(xs) + 48, max(y2) - min(ys) + 64
        parts.append(
            '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
            'fill="none" stroke="#bbb" stroke-width="1.5"/>'
            % (px, py, pw, ph)
        )
        parts.append(
            '<text x="%.1f" y="%.1f" font-size="30" font-weight="bold" '
            'fill="#111">%s</text>' % (px + 12, py - 12, esc(sf.get("label", "")))
        )
    parts.append("</g>")

    # Group containers
    parts.append('<g id="layer-groups">')
    for e in spec.get("entities", []):
        if e.get("kind") != "group":
            continue
        x, y, w, h = pos[e["id"]]
        parts.append(
            '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="12" '
            'fill="#f4f6f8" stroke="#555" stroke-width="1.5"/>'
            % (x, y, w, h)
        )
        parts.append(
            '<text x="%.1f" y="%.1f" font-size="28" font-weight="bold" '
            'fill="#222">%s</text>' % (x + 16, y + 40, esc(e.get("label", "")))
        )
    parts.append("</g>")

    # Edges
    parts.append('<g id="layer-edges" fill="none" stroke="#222" '
                 'stroke-width="2.5">')
    for r in spec.get("relations", []):
        a = r.get("from")
        b = r.get("to")
        x1, y1 = center(pos, a)
        x2, y2 = center(pos, b)
        glyph = KIND_GLYPH.get(r.get("kind"), "arrow")
        if glyph == "dashed":
            dash = ' stroke-dasharray="10,7"'
        else:
            dash = ""
        marker = ""
        if glyph in ("arrow", "dashed"):
            marker = ' marker-end="url(#arrow-%s)"' % fid
        parts.append(
            '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f"%s%s/>'
            % (x1, y1, x2, y2, dash, marker)
        )
        if glyph == "bar":
            # inhibition bar: short perpendicular stroke near the target end.
            dx, dy = x2 - x1, y2 - y1
            import math
            ln = math.hypot(dx, dy) or 1.0
            ux, uy = -dy / ln, dx / ln
            bx, by = x2 - 6 * dx / ln, y2 - 6 * dy / ln
            parts.append(
                '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                'stroke="#222" stroke-width="4"/>'
                % (bx - 8 * ux, by - 8 * uy, bx + 8 * ux, by + 8 * uy)
            )
        if r.get("label"):
            parts.append(
                '<text x="%.1f" y="%.1f" font-size="22" fill="#333" '
                'stroke="none">%s</text>'
                % ((x1 + x2) / 2, (y1 + y2) / 2 - 10, esc(r["label"]))
            )
    parts.append("</g>")

    # Nodes
    parts.append('<g id="layer-nodes">')
    for e in spec.get("entities", []):
        if e.get("kind") == "group":
            continue
        x, y, w, h = pos[e["id"]]
        cat = e.get("category")
        color = colors.get(cat, "#ffffff")
        if e.get("kind") == "annotation":
            continue
        if e.get("kind") == "process":
            parts.append(
                '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="8" '
                'fill="%s" stroke="%s" stroke-width="3"/>'
                % (x, y, w, h, _lighten(color), color)
            )
        else:
            parts.append(
                '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="8" '
                'fill="%s" stroke="%s" stroke-width="2"/>'
                % (x, y, w, h, _lighten(color), color)
            )
    parts.append("</g>")

    # Labels (editable text, separate layer)
    parts.append('<g id="layer-labels" font-size="24" fill="#111" '
                 'text-anchor="middle">')
    for e in spec.get("entities", []):
        if e.get("kind") in ("group", "annotation"):
            continue
        x, y, w, h = pos[e["id"]]
        parts.append(
            '<text x="%.1f" y="%.1f">%s</text>'
            % (x + w / 2, y + h / 2 + 8, esc(e.get("label", "")))
        )
    parts.append("</g>")

    # Annotations
    parts.append('<g id="layer-annotations" font-size="22" fill="#333">')
    for a in spec.get("annotations", []):
        if a.get("anchor") and a["anchor"] in pos:
            x, y, w, h = pos[a["anchor"]]
            ax, ay = x + w + 12, y + 12
        else:
            ax, ay = float(a.get("x", 0)), float(a.get("y", 0))
        parts.append('<text x="%.1f" y="%.1f">%s</text>'
                     % (ax, ay, esc(a.get("text", ""))))
    parts.append("</g>")

    # Legend
    legend = spec.get("legend") or {}
    if legend.get("items"):
        parts.append('<g id="layer-legend">')
        lx = W - 360
        ly = H - 40 - len(legend["items"]) * 40
        parts.append(
            '<rect x="%.1f" y="%.1f" width="340" height="%.1f" rx="8" '
            'fill="#fbfbfb" stroke="#ccc"/>'
            % (lx - 12, ly - 40, len(legend["items"]) * 40 + 56)
        )
        if legend.get("title"):
            parts.append(
                '<text x="%.1f" y="%.1f" font-size="26" font-weight="bold" '
                'fill="#111">%s</text>' % (lx, ly - 16, esc(legend["title"]))
            )
        for i, item in enumerate(legend["items"]):
            cat = item.get("category") or item.get("id")
            color = colors.get(cat, "#1f77b4")
            iy = ly + i * 40
            marker = item.get("marker", "rect")
            if marker == "rect":
                parts.append(
                    '<rect x="%.1f" y="%.1f" width="22" height="22" '
                    'fill="%s" stroke="#444"/>' % (lx, iy - 16, color)
                )
            elif marker == "arrow":
                parts.append(
                    '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                    'stroke="%s" stroke-width="3" marker-end="url(#arrow-%s)"/>'
                    % (lx, iy - 5, lx + 22, iy - 5, color, fid)
                )
            elif marker == "bar":
                parts.append(
                    '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                    'stroke="%s" stroke-width="3"/>'
                    % (lx, iy - 5, lx + 22, iy - 5, color)
                )
                parts.append(
                    '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                    'stroke="%s" stroke-width="4"/>'
                    % (lx + 22, iy - 11, lx + 22, iy + 1, color)
                )
            else:
                parts.append(
                    '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                    'stroke="%s" stroke-width="3"/>'
                    % (lx, iy - 5, lx + 22, iy - 5, color)
                )
            parts.append(
                '<text x="%.1f" y="%.1f" font-size="22" fill="#111">%s</text>'
                % (lx + 34, iy, esc(item.get("label", "")))
            )
        parts.append("</g>")

    parts.append("</svg>\n")
    return "\n".join(parts)


def _lighten(hexcolor):
    return _mix(hexcolor, "#ffffff", 0.78)


def _mix(c1, c2, t):
    def _rgb(c):
        c = c.lstrip("#")
        return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
    a, b = _rgb(c1), _rgb(c2)
    return "#%02x%02x%02x" % tuple(
        int(round(a[i] * t + b[i] * (1 - t))) for i in range(3)
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--spec", required=True, help="figure_spec.json 路径")
    ap.add_argument("--out", required=True, help="输出 SVG 路径")
    args = ap.parse_args(argv)
    spec = load_spec(args.spec)
    validate_spec(spec)
    colors = assign_colors(spec)
    pos = layout(spec)
    svg = render(spec, colors, pos)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(svg)
    print(json.dumps({"ok": True, "svg": args.out,
                      "entities": len(spec.get("entities", [])),
                      "relations": len(spec.get("relations", []))},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
