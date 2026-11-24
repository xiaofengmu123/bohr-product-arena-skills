#!/usr/bin/env python3
"""build_svg.py — 从 spec.json 生成分层、可编辑、带语义箭头与证据属性的 SVG。

只依赖 Python 标准库。布局与配色确定性：同一 spec.json 两次运行产出字节一致的 SVG。
设计原则：
  * 图例与图面同源生成（图例项直接从本脚本的关系/实体样式表派生），保证自洽；
  * 每个实体/关系都内嵌 data-* 证据属性（locator 列表），可独立回溯；
  * 命名图层（compartments / relations / entities / labels / legend），可用 Inkscape 打开编辑。
"""
import argparse
import html
import json
import math
import os
import sys

# ---------------------------------------------------------------------------
# 关系类型 → 视觉样式（颜色 / 线型 / 端点 / 图例）。图例直接从本表派生。
# ---------------------------------------------------------------------------
REL_STYLES = {
    "activation":   {"stroke": "#1a7f37", "dash": None,  "width": 2.4, "end": "arrow", "legend": "激活"},
    "inhibition":   {"stroke": "#c62828", "dash": None,  "width": 2.4, "end": "bar",   "legend": "抑制"},
    "binding":      {"stroke": "#5d5d5d", "dash": None,  "width": 2.0, "end": "none",  "legend": "结合"},
    "modification": {"stroke": "#9c6500", "dash": "6,4", "width": 2.0, "end": "dot",   "legend": "修饰"},
    "transport":    {"stroke": "#2166ac", "dash": "7,5", "width": 2.2, "end": "arrow", "legend": "转运"},
    "conversion":   {"stroke": "#7b3f9d", "dash": None,  "width": 2.2, "end": "both",  "legend": "转化"},
    "recruitment":  {"stroke": "#a15c00", "dash": "2,3", "width": 2.0, "end": "arrow", "legend": "募集"},
}
FALLBACK_REL = {"stroke": "#555555", "dash": "3,3", "width": 1.5, "end": "arrow", "legend": "未知关系"}

# 证据强度 → 线宽系数（视觉编码，图例同步说明）
STRENGTH_FACTOR = {"stated": 1.0, "inferred": 0.75, "uncertain": 0.5, "conflict": 1.0}

# ---------------------------------------------------------------------------
# 实体类型 → 形状/配色。图例直接从本表派生。
# ---------------------------------------------------------------------------
ENTITY_STYLES = {
    "gene":           {"shape": "ellipse",    "fill": "#f7f7f7", "stroke": "#333333", "italic": True,  "legend": "基因"},
    "protein":        {"shape": "roundrect",  "fill": "#e3ecf7", "stroke": "#2b6cb0", "italic": False, "legend": "蛋白"},
    "complex":        {"shape": "hexagon",    "fill": "#efe6f7", "stroke": "#6b3fa0", "italic": False, "legend": "复合物"},
    "family":         {"shape": "dashedrect", "fill": "#fbf3e4", "stroke": "#9c6500", "italic": False, "legend": "家族"},
    "cell_type":      {"shape": "diamond",    "fill": "#e8f5e9", "stroke": "#1a7f37", "italic": False, "legend": "细胞类型"},
    "small_molecule": {"shape": "circle",     "fill": "#fff3e0", "stroke": "#c65d00", "italic": False, "legend": "小分子"},
    "process":        {"shape": "rect",       "fill": "#f5f5f5", "stroke": "#555555", "italic": False, "legend": "过程/其他"},
}
FALLBACK_ENT = {"shape": "rect", "fill": "#ffffff", "stroke": "#555555", "italic": False, "legend": "未分类实体"}

DEFAULT_COMPARTMENTS = [
    {"id": "extracellular", "name": "细胞外", "color": "#eef4fb"},
    {"id": "membrane",      "name": "细胞膜", "color": "#fdeee9"},
    {"id": "cytosol",       "name": "细胞质", "color": "#f5f7f0"},
    {"id": "nucleus",       "name": "细胞核", "color": "#f3eef9"},
]
FONT_STACK = "Arial, 'DejaVu Sans', 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif"


def esc(s):
    return html.escape("" if s is None else str(s), quote=True)


def build(spec):
    """返回 (svg_string, warnings_list)。spec 为 dict。"""
    if not isinstance(spec, dict):
        spec = {}
    warnings = []
    entities = [e for e in (spec.get("entities") or []) if isinstance(e, dict)]
    relations = [r for r in (spec.get("relations") or []) if isinstance(r, dict)]
    compartments = _resolve_compartments(spec.get("compartments"), entities, warnings)

    # 布局（区室列占 0..1200，图例放右侧独立栏，避免遮挡最右区室）
    comp_x, comp_w, positions, W, H = _layout(compartments, entities, warnings)
    canvas_w = W + 324

    # 图例（与实际使用同源）
    used_ent_types = [e.get("type") for e in entities if e.get("type") in ENTITY_STYLES]
    used_rel_types = [r.get("type") for r in relations if r.get("type") in REL_STYLES]
    legend_svg, legend_h = _render_legend(used_ent_types, used_rel_types,
                                          spec.get("data_mappings") or [], canvas_w)

    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
        f'xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.0.dtd" '
        f'width="{canvas_w}" height="{H}" viewBox="0 0 {canvas_w} {H}">'
    )
    parts.append(f"  <title>{esc(spec.get('title') or spec.get('case_id') or 'mechanism')}</title>")
    parts.append("  <defs>")
    parts.append(_marker_defs())
    parts.append("  </defs>")
    parts.append('  <sodipodi:namedview id="base" units="px" showgrid="false"/>')

    # ---- 图层：区室背景 ----
    parts.append('  <g id="layer-compartments" inkscape:groupmode="layer" inkscape:label="compartments">')
    for c in compartments:
        x = comp_x.get(c["id"])
        if x is None:
            continue
        parts.append(
            f'    <rect data-compartment="{esc(c["id"])}" x="{x:.1f}" y="20" '
            f'width="{comp_w - 6:.1f}" height="{H - 40:.1f}" '
            f'fill="{esc(c.get("color", "#fafafa"))}" stroke="#c9c9c9" stroke-width="1"/>'
        )
        parts.append(
            f'    <text x="{x + comp_w / 2:.1f}" y="38" text-anchor="middle" '
            f'font-family="{FONT_STACK}" font-size="13" font-weight="bold" fill="#444444">'
            f'{esc(c.get("name", c["id"]))}</text>'
        )
    parts.append("  </g>")

    # ---- 图层：关系（画在实体下层）----
    parts.append('  <g id="layer-relations" inkscape:groupmode="layer" inkscape:label="relations">')
    pair_index = {}
    for r in relations:
        rid = r.get("id") or f"rel-{len(pair_index)}"
        typ = r.get("type", "unknown")
        st = REL_STYLES.get(typ, FALLBACK_REL)
        src, tgt = r.get("source"), r.get("target")
        p1 = positions.get(src)
        p2 = positions.get(tgt)
        if p1 is None or p2 is None:
            warnings.append(f"relation {rid}: 端点 {src!r}/{tgt!r} 无对应实体，已跳过")
            continue
        key = tuple(sorted([str(src), str(tgt)]))
        pair_index[key] = pair_index.get(key, 0) + 1
        bend = (pair_index[key] - 1) * 18
        a, b = _trim(p1, p2, _node_radius(p1), _node_radius(p2))
        d = _path_data(a, b, bend)
        strength = r.get("evidence_strength", "stated")
        width = st["width"] * STRENGTH_FACTOR.get(strength, 1.0)
        dash = f' stroke-dasharray="{st["dash"]}"' if st.get("dash") else ""
        marker = ""
        if st["end"] == "arrow":
            marker = f' marker-end="url(#mk-{esc(typ)}-arr)"'
        elif st["end"] == "bar":
            marker = f' marker-end="url(#mk-{esc(typ)}-bar)"'
        elif st["end"] == "dot":
            marker = f' marker-end="url(#mk-{esc(typ)}-dot)"'
        elif st["end"] == "both":
            marker = (f' marker-start="url(#mk-{esc(typ)}-arr-rev)" '
                      f'marker-end="url(#mk-{esc(typ)}-arr)"')
        locs = _locators(r)
        mod = r.get("modifier")
        mod_label = f' data-modifier="{esc(mod)}"' if mod else ""
        parts.append(
            f'    <path id="{esc(rid)}" data-relation="{esc(rid)}" data-type="{esc(typ)}" '
            f'data-source="{esc(src)}" data-target="{esc(tgt)}" '
            f'data-evidence-strength="{esc(strength)}" data-evidence-locators="{esc(locs)}"{mod_label} '
            f'd="{d}" fill="none" stroke="{st["stroke"]}" stroke-width="{width:.2f}"{dash}{marker}/>'
        )
        if mod:
            mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2 - 8
            parts.append(
                f'    <text x="{mx:.1f}" y="{my:.1f}" text-anchor="middle" font-family="{FONT_STACK}" '
                f'font-size="11" fill="{st["stroke"]}">{esc(mod)}</text>'
            )
    parts.append("  </g>")

    # ---- 图层：实体形状 ----
    parts.append('  <g id="layer-entities" inkscape:groupmode="layer" inkscape:label="entities">')
    for e in entities:
        eid = e.get("id")
        p = positions.get(eid)
        if p is None:
            warnings.append(f"entity {eid!r} 无法布局（缺 compartment），已跳过")
            continue
        x, y, w, h = p
        st = ENTITY_STYLES.get(e.get("type"), FALLBACK_ENT)
        style = e.get("style") or {}
        fill = style.get("fill", st["fill"])
        stroke = style.get("stroke", st["stroke"])
        stroke_w = style.get("stroke_width", 1.6)
        shape = _shape_path(st["shape"], x, y, w, h)
        if st["shape"] == "dashedrect":
            dash = ' stroke-dasharray="5,4"'
        else:
            dash = ""
        tm = e.get("transmembrane")
        tm_attr = f' data-transmembrane="{esc(str(bool(tm)).lower())}"' if tm is not None else ""
        locs = _locators(e)
        parts.append(
            f'    <g id="entity-{esc(eid)}" data-entity="{esc(eid)}" data-type="{esc(e.get("type"))}" '
            f'data-label="{esc(e.get("label"))}" data-compartment="{esc(e.get("compartment"))}" '
            f'data-evidence-locators="{esc(locs)}"{tm_attr}>'
        )
        parts.append(
            f'      <path d="{shape}" fill="{esc(fill)}" stroke="{esc(stroke)}" '
            f'stroke-width="{stroke_w}"{dash}/>'
        )
        parts.append("    </g>")
    parts.append("  </g>")

    # ---- 图层：标签 ----
    parts.append('  <g id="layer-labels" inkscape:groupmode="layer" inkscape:label="labels">')
    for e in entities:
        p = positions.get(e.get("id"))
        if p is None:
            continue
        x, y, w, h = p
        st = ENTITY_STYLES.get(e.get("type"), FALLBACK_ENT)
        style = e.get("style") or {}
        fill = style.get("text_fill", "#222222")
        italic = ' font-style="italic"' if st["italic"] else ""
        label = e.get("label") or e.get("id") or "?"
        fs = style.get("font_size", 13)
        parts.append(
            f'    <text x="{x + w / 2:.1f}" y="{y + h / 2 + 4:.1f}" text-anchor="middle" '
            f'font-family="{FONT_STACK}" font-size="{fs}"{italic} fill="{esc(fill)}">{esc(label)}</text>'
        )
    parts.append("  </g>")

    # ---- 图层：图例 ----
    parts.append('  <g id="layer-legend" inkscape:groupmode="layer" inkscape:label="legend">')
    parts.append(legend_svg)
    parts.append("  </g>")

    parts.append("</svg>")
    return "\n".join(parts) + "\n", warnings


# ---------------------------------------------------------------------------
def _resolve_compartments(compartments, entities, warnings):
    out = []
    if isinstance(compartments, list) and compartments:
        for c in compartments:
            if isinstance(c, dict) and c.get("id"):
                out.append({"id": c["id"], "name": c.get("name", c["id"]),
                            "color": c.get("color", "#fafafa")})
    used = []
    for e in entities:
        c = e.get("compartment")
        if c and c not in used:
            used.append(c)
    known = {c["id"] for c in out} if out else set()
    if not out:
        for c in DEFAULT_COMPARTMENTS:
            if c["id"] in used:
                out.append(dict(c))
    else:
        for cid in used:
            if cid not in known:
                out.append({"id": cid, "name": cid, "color": "#f5f5f5"})
                warnings.append(f"区室 {cid!r} 未在 compartments 中声明，已按默认样式追加")
    if not out:
        out = [dict(c) for c in DEFAULT_COMPARTMENTS]
    return out


def _layout(compartments, entities, warnings):
    W, margin = 1200, 46
    n = max(len(compartments), 1)
    comp_w = (W - 2 * margin) / n
    comp_x = {}
    for i, c in enumerate(compartments):
        comp_x[c["id"]] = margin + i * comp_w

    default_w, default_h = 150, 56
    groups = {}
    for e in entities:
        cid = e.get("compartment") or "cytosol"
        groups.setdefault(cid, []).append(e)

    positions = {}
    for cid, lst in groups.items():
        if cid not in comp_x:
            comp_x[cid] = margin + (len(compartments) - 1) * comp_w
            warnings.append(f"entity compartment {cid!r} 无布局列，已放入最右列")
        cx = comp_x[cid] + comp_w / 2
        top = 60
        gap = default_h + 42
        for j, e in enumerate(lst):
            if isinstance(e.get("x"), (int, float)) and isinstance(e.get("y"), (int, float)):
                positions[e["id"]] = (float(e["x"]), float(e["y"]),
                                      float(e.get("w", default_w)), float(e.get("h", default_h)))
                continue
            st = e.get("style") or {}
            w = float(st.get("width", default_w))
            h = float(st.get("height", default_h))
            positions[e["id"]] = (cx, top + j * gap, w, h)

    max_stack = max([len(v) for v in groups.values()] or [1])
    H = max(520, 60 + max_stack * (default_h + 42) + 40)
    return comp_x, comp_w, positions, W, H


def _node_radius(p):
    _, _, w, h = p
    return max(w, h) / 2 + 6


def _trim(p1, p2, r1, r2):
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    L = math.hypot(dx, dy) or 1.0
    ux, uy = dx / L, dy / L
    return ((p1[0] + ux * r1, p1[1] + uy * r1),
            (p2[0] - ux * r2, p2[1] - uy * r2))


def _path_data(a, b, bend=0):
    if abs(a[0] - b[0]) < 1.0:
        # 近垂直：用二次曲线避免重叠
        mid = (a[1] + b[1]) / 2
        cx = a[0] + 60 + bend
        return (f"M {a[0]:.1f} {a[1]:.1f} Q {cx:.1f} {mid:.1f} "
                f"{b[0]:.1f} {b[1]:.1f}")
    if bend:
        midx = (a[0] + b[0]) / 2
        midy = (a[1] + b[1]) / 2 - bend
        return (f"M {a[0]:.1f} {a[1]:.1f} Q {midx:.1f} {midy:.1f} "
                f"{b[0]:.1f} {b[1]:.1f}")
    return f"M {a[0]:.1f} {a[1]:.1f} L {b[0]:.1f} {b[1]:.1f}"


def _shape_path(shape, x, y, w, h):
    cx, cy = x + w / 2, y + h / 2
    r = min(w, h) / 2
    if shape == "ellipse":
        return f"M {cx - w/2:.1f} {cy:.1f} a {w/2:.1f} {h/2:.1f} 0 1 0 {w:.1f} 0 " \
               f"a {w/2:.1f} {h/2:.1f} 0 1 0 {-w:.1f} 0 Z"
    if shape == "circle":
        return f"M {cx - r:.1f} {cy:.1f} a {r:.1f} {r:.1f} 0 1 0 {2*r:.1f} 0 " \
               f"a {r:.1f} {r:.1f} 0 1 0 {-2*r:.1f} 0 Z"
    if shape == "roundrect":
        rr = min(10.0, h / 4)
        return (f"M {x + rr:.1f} {y:.1f} H {x + w - rr:.1f} Q {x + w:.1f} {y:.1f} "
                f"{x + w:.1f} {y + rr:.1f} V {y + h - rr:.1f} Q {x + w:.1f} {y + h:.1f} "
                f"{x + w - rr:.1f} {y + h:.1f} H {x + rr:.1f} Q {x:.1f} {y + h:.1f} "
                f"{x:.1f} {y + h - rr:.1f} V {y + rr:.1f} Q {x:.1f} {y:.1f} {x + rr:.1f} {y:.1f} Z")
    if shape == "hexagon":
        return (f"M {cx - r:.1f} {cy:.1f} L {cx - r/2:.1f} {cy - r:.1f} L {cx + r/2:.1f} {cy - r:.1f} "
                f"L {cx + r:.1f} {cy:.1f} L {cx + r/2:.1f} {cy + r:.1f} L {cx - r/2:.1f} {cy + r:.1f} Z")
    if shape == "diamond":
        return (f"M {cx:.1f} {cy - r:.1f} L {cx + r:.1f} {cy:.1f} L {cx:.1f} {cy + r:.1f} "
                f"L {cx - r:.1f} {cy:.1f} Z")
    # rect / dashedrect
    return f"M {x:.1f} {y:.1f} H {x + w:.1f} V {y + h:.1f} H {x:.1f} Z"


def _locators(item):
    ev = item.get("evidence") or []
    locs = []
    for it in ev:
        if isinstance(it, dict) and it.get("locator"):
            locs.append(str(it["locator"]))
    return "; ".join(locs)


def _marker_defs():
    out = []
    for typ, st in REL_STYLES.items():
        c = st["stroke"]
        if st["end"] in ("arrow", "both"):
            out.append(
                f'    <marker id="mk-{typ}-arr" markerWidth="10" markerHeight="10" '
                f'refX="8" refY="3" orient="auto" markerUnits="strokeWidth">'
                f'<path d="M0,0 L8,3 L0,6 Z" fill="{c}"/></marker>'
            )
        if st["end"] == "both":
            out.append(
                f'    <marker id="mk-{typ}-arr-rev" markerWidth="10" markerHeight="10" '
                f'refX="2" refY="3" orient="auto" markerUnits="strokeWidth">'
                f'<path d="M8,0 L0,3 L8,6 Z" fill="{c}"/></marker>'
            )
        if st["end"] == "bar":
            out.append(
                f'    <marker id="mk-{typ}-bar" markerWidth="10" markerHeight="10" '
                f'refX="4" refY="3" orient="auto" markerUnits="strokeWidth">'
                f'<line x1="1" y1="0" x2="1" y2="6" stroke="{c}" stroke-width="3"/></marker>'
            )
        if st["end"] == "dot":
            out.append(
                f'    <marker id="mk-{typ}-dot" markerWidth="9" markerHeight="9" '
                f'refX="6" refY="4.5" orient="auto" markerUnits="strokeWidth">'
                f'<circle cx="4.5" cy="4.5" r="4" fill="{c}"/></marker>'
            )
    return "\n".join(out)


def _render_legend(used_ent_types, used_rel_types, data_mappings, W):
    """图例与实际使用同源：实体样式表 + 关系样式表 + 证据强度编码 + 数据映射文本。"""
    y = 24
    x0 = W - 300
    if not used_ent_types:
        used_ent_types = []
    if not used_rel_types:
        used_rel_types = []
    items = []
    for t in used_ent_types:
        st = ENTITY_STYLES[t]
        items.append(("entity", st["legend"], st["fill"], st["stroke"], st["shape"]))
    for t in used_rel_types:
        st = REL_STYLES[t]
        items.append(("rel", st["legend"], None, st["stroke"], t))
    # 证据强度编码（仅当存在关系时展示）
    if used_rel_types:
        items.append(("strength", "证据强度: 线宽 实=stated / 中=inferred / 细=uncertain",
                      None, "#444444", "strength"))
    n = len(items)
    box_h = 34 + n * 22 + (14 if data_mappings else 0) + 20
    parts = []
    parts.append(
        f'    <rect x="{x0:.1f}" y="14" width="290" height="{box_h:.1f}" '
        f'fill="#ffffff" fill-opacity="0.94" stroke="#999999" stroke-width="1"/>'
    )
    parts.append(
        f'    <text x="{x0 + 12:.1f}" y="{y + 18:.1f}" font-family="{FONT_STACK}" '
        f'font-size="13" font-weight="bold" fill="#333333">图例 Legend</text>'
    )
    row_y = y + 40
    for kind, label, fill, stroke, shape in items:
        if kind == "entity":
            sw = _mini_entity(shape, x0 + 22, row_y)
            parts.append(
                f'    <path d="{sw}" fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>'
            )
            parts.append(_legend_label(x0 + 34, row_y, label))
        elif kind == "rel":
            sw = _mini_rel(shape, x0 + 10, row_y, stroke)
            parts.append(sw)
            parts.append(_legend_label(x0 + 34, row_y, label))
        elif kind == "strength":
            parts.append(_legend_label(x0 + 12, row_y, label))
        row_y += 22
    if data_mappings:
        parts.append(
            f'    <text x="{x0 + 12:.1f}" y="{row_y + 4:.1f}" font-family="{FONT_STACK}" '
            f'font-size="11" fill="#333333">数据编码: {esc(_mapping_summary(data_mappings))}</text>'
        )
    return "\n".join(parts), box_h


def _mini_entity(shape, cx, cy):
    r = 7
    if shape == "ellipse":
        return f"M {cx - 9:.1f} {cy:.1f} a 9 7 0 1 0 18 0 a 9 7 0 1 0 -18 0 Z"
    if shape == "circle":
        return f"M {cx - r:.1f} {cy:.1f} a {r} {r} 0 1 0 {2*r} 0 a {r} {r} 0 1 0 {-2*r} 0 Z"
    if shape == "hexagon":
        return (f"M {cx - r:.1f} {cy:.1f} L {cx - r/2:.1f} {cy - r:.1f} L {cx + r/2:.1f} {cy - r:.1f} "
                f"L {cx + r:.1f} {cy:.1f} L {cx + r/2:.1f} {cy + r:.1f} L {cx - r/2:.1f} {cy + r:.1f} Z")
    if shape == "diamond":
        return (f"M {cx:.1f} {cy - r:.1f} L {cx + r:.1f} {cy:.1f} L {cx:.1f} {cy + r:.1f} "
                f"L {cx - r:.1f} {cy:.1f} Z")
    return f"M {cx - 9:.1f} {cy - 7:.1f} H {cx + 9:.1f} V {cy + 7:.1f} H {cx - 9:.1f} Z"


def _legend_label(x, y, label):
    return (f'    <text x="{x:.1f}" y="{y + 4:.1f}" font-family="{FONT_STACK}" '
            f'font-size="11" fill="#333333">{esc(label)}</text>')


def _mini_rel(typ, x0, cy, stroke):
    """图例小样按关系类型取同款 marker 与线型，保证与主图一致。"""
    st = REL_STYLES.get(typ, FALLBACK_REL)
    end = st["end"]
    x1, x2 = x0 + 6, x0 + 26
    dash = f' stroke-dasharray="{st["dash"]}"' if st.get("dash") else ""
    marker = ""
    if end == "bar":
        marker = f' marker-end="url(#mk-{typ}-bar)"'
    elif end == "arrow":
        marker = f' marker-end="url(#mk-{typ}-arr)"'
    elif end == "dot":
        marker = f' marker-end="url(#mk-{typ}-dot)"'
    elif end == "both":
        marker = (f' marker-start="url(#mk-{typ}-arr-rev)" '
                  f'marker-end="url(#mk-{typ}-arr)"')
    return (f'    <path d="M {x1:.1f} {cy:.1f} L {x2:.1f} {cy:.1f}" fill="none" '
            f'stroke="{stroke}" stroke-width="2"{dash}{marker}/>')


def _mapping_summary(mappings):
    parts = []
    for m in mappings or []:
        if not isinstance(m, dict):
            continue
        visual = m.get("visual") or "?"
        encodes = m.get("encodes") or "?"
        parts.append(f"{visual}→{encodes}")
    return "; ".join(parts) if parts else "(无)"


# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="spec.json → 分层可编辑 SVG")
    ap.add_argument("spec", help="spec.json 路径")
    ap.add_argument("output", help="输出 SVG 路径")
    ap.add_argument("--report", help="可选：警告写入 JSON 报告")
    a = ap.parse_args(argv)
    with open(a.spec, "r", encoding="utf-8-sig") as f:
        spec = json.load(f)
    svg, warnings = build(spec)
    os.makedirs(os.path.dirname(os.path.abspath(a.output)) or ".", exist_ok=True)
    with open(a.output, "w", encoding="utf-8") as f:
        f.write(svg)
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            json.dump({"svg": os.path.abspath(a.output), "warnings": warnings},
                      f, ensure_ascii=False, indent=2)
    for w in warnings:
        print(f"WARN: {w}", file=sys.stderr)
    print(f"built {a.output} ({len(svg)} bytes, {len(warnings)} warnings)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
