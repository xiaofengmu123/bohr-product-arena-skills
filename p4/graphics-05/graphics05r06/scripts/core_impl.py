# -*- coding: utf-8 -*-
"""graphical-abstract core (Python 标准库 only).

确定性管线：
  plan.json（语义意图层，人工可审）→ figure_model.json（几何/视觉通道/源指针层）
  → 分层可编辑 SVG → 真实 PNG/PDF 预览（有渲染器时）。

所有布局决策由 plan["seed"] 决定；发射前对 id 排序，因此相同输入 + 相同 seed
产生字节一致的 SVG。本模块不做语义抽取：语义抽取由调用 Agent 写入 plan.json，
本模块只负责确定性解析、布局、渲染、校验与溯源映射。
"""

import hashlib
import html
import json
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET

CANONICAL_VERSION = "1.1.0"
DEFAULT_CANVAS = {"width": 1600, "height": 1000, "unit": "px"}
DEFAULT_SEED = 20261123
MATERIAL_EXTS = {".pdf", ".csv", ".tsv", ".txt", ".md", ".png", ".jpg", ".jpeg",
                 ".tif", ".tiff", ".svg", ".json", ".docx", ".pptx", ".xlsx"}

PALETTE = {
    "question": "#4C78A8", "method": "#F58518", "results": "#54A24B",
    "conclusion": "#B279A2", "default": "#76B7B2", "cell": "#4C78A8",
    "molecule": "#E45756", "material": "#F58518", "device": "#54A24B",
    "sample": "#B279A2", "process": "#72B7B2", "group": "#9D755D",
}
BAR_COLORS = ["#4C78A8", "#F58518", "#54A24B", "#E45756", "#B279A2",
              "#76B7B2", "#9D755D", "#FF9DA6"]

SVG_LAYER_ORDER = ["layer-background", "layer-panels", "layer-entities",
                   "layer-relations", "layer-data", "layer-legend",
                   "layer-footer", "layer-title"]


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path, max_bytes=2_000_000):
    if not os.path.isfile(path):
        return None, "missing file: %s" % path
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, "unreadable: %s" % e
    if size > max_bytes:
        return None, "skipped: >%d bytes" % max_bytes
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc, errors="strict") as f:
                return f.read(), None
        except (UnicodeDecodeError, UnicodeError):
            continue
    try:
        with open(path, "rb") as f:
            return f.read().decode("latin-1"), None
    except OSError as e:
        return None, "unreadable: %s" % e


def load_json(path, default=None):
    if not os.path.isfile(path):
        return default, "missing file: %s" % path
    text, err = read_text(path)
    if err:
        return default, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return default, "bad json %s: %s" % (path, e)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return path


def discover_inputs(input_dir):
    case_path = None
    task_path = None
    materials = []
    if not os.path.isdir(input_dir):
        return None, None, [], "input dir missing: %s" % input_dir
    for root, dirs, files in os.walk(input_dir):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for fn in sorted(files):
            p = os.path.join(root, fn)
            low = fn.lower()
            if low == "case.json" and case_path is None:
                case_path = p
            elif low == "task.md" and task_path is None:
                task_path = p
            elif os.path.splitext(low)[1] in MATERIAL_EXTS:
                materials.append(p)
    return case_path, task_path, sorted(set(materials)), None


def default_plan(case_id, figure_type="graphical_abstract"):
    return {
        "case_id": case_id,
        "figure_type": figure_type,
        "title": "",
        "seed": DEFAULT_SEED,
        "canvas": dict(DEFAULT_CANVAS),
        "reading_order": ["question", "method", "results", "conclusion"],
        "graphical_abstract": {"question": "", "method": "", "results": "", "conclusion": ""},
        "panels": [
            {"id": "p_question", "role": "question", "label": "研究问题", "order": 1},
            {"id": "p_method", "role": "method", "label": "方法", "order": 2},
            {"id": "p_results", "role": "results", "label": "核心结果", "order": 3},
            {"id": "p_conclusion", "role": "conclusion", "label": "结论", "order": 4},
        ],
        "entities": [],
        "relations": [],
        "data": [],
        "legend": [],
        "annotations": [],
        "constraints": {"margin": 24, "pad": 16, "font_min_px": 10, "font_max_px": 22,
                        "title_font_px": 26, "entity_h": 46, "panel_header_h": 34,
                        "bar_max_w": 420, "data_row_h": 30},
        "sources": [],
        "uncertainty": [],
        "limitations": [],
        "pending_human": [],
        "change_log": [],
    }


def collect_contract_fields(case_obj):
    fields = {}
    if isinstance(case_obj, dict):
        oc = case_obj.get("output_contract")
        if isinstance(oc, dict):
            for k in oc:
                fields[k] = None
        elif isinstance(oc, list):
            for k in oc:
                if isinstance(k, str):
                    fields[k] = None
    return fields


def norm_id(text):
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", str(text))
    return (s or "x").strip("_") or "x"


def plan_checksum(plan):
    p = dict(plan)
    p.pop("change_log", None)
    return hashlib.sha256(
        json.dumps(p, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


def source_refs(obj):
    s = obj.get("sources")
    if s:
        return [str(x) for x in s] if isinstance(s, (list, tuple)) else [str(s)]
    s1 = obj.get("source")
    if s1:
        return [str(s1)]
    return []


def estimate_text_width(text, font_size):
    w = 0.0
    for ch in str(text):
        w += font_size * (1.0 if ord(ch) > 0x2E7F else 0.56)
    return w


def fit_label(text, font_size, max_w, min_font):
    text = str(text)
    fs = float(font_size)
    while fs > min_font and estimate_text_width(text, fs) > max_w:
        fs -= 1.0
    out = text
    if estimate_text_width(out, fs) > max_w:
        while out and estimate_text_width(out + "…", fs) > max_w:
            out = out[:-1]
        out = out + "…"
    return fs, out


def box_center(b):
    return (b[0] + b[2] / 2.0, b[1] + b[3] / 2.0)


def clip_ray_to_box(cx, cy, box):
    x, y, w, h = box
    bx, by = x + w / 2.0, y + h / 2.0
    dx, dy = cx - bx, cy - by
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return (cx, cy)
    t = 1.0
    if abs(dx) > 1e-9:
        t = min(t, (w / 2.0) / abs(dx))
    if abs(dy) > 1e-9:
        t = min(t, (h / 2.0) / abs(dy))
    return (bx + dx * t, by + dy * t)


def connect_boxes(b1, b2):
    c1 = box_center(b1)
    c2 = box_center(b2)
    p1 = clip_ray_to_box(c2[0], c2[1], b1)
    p2 = clip_ray_to_box(c1[0], c1[1], b2)
    return "M %.3f %.3f L %.3f %.3f" % (p1[0], p1[1], p2[0], p2[1])


def _text_fill(hexcolor):
    try:
        c = str(hexcolor).lstrip("#")
        r = int(c[0:2], 16)
        g = int(c[2:4], 16)
        b = int(c[4:6], 16)
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        return "#ffffff" if lum < 140 else "#111111"
    except Exception:
        return "#111111"


def build_figure_model(plan):
    seed = int(plan.get("seed", DEFAULT_SEED))
    canvas = plan.get("canvas") or dict(DEFAULT_CANVAS)
    W = float(canvas.get("width", DEFAULT_CANVAS["width"]))
    H = float(canvas.get("height", DEFAULT_CANVAS["height"]))
    c = plan.get("constraints") or {}
    margin = float(c.get("margin", 24))
    pad = float(c.get("pad", 16))
    font_min = float(c.get("font_min_px", 10))
    title_font = float(c.get("title_font_px", 26))
    header_h = float(c.get("panel_header_h", 34))
    entity_h = float(c.get("entity_h", 46))
    bar_max_w = float(c.get("bar_max_w", 420))
    data_row_h = float(c.get("data_row_h", 30))

    items = []
    layers = list(SVG_LAYER_ORDER)

    items.append({"id": "canvas_bg", "type": "rect", "layer": "layer-background",
                  "x": 0, "y": 0, "w": W, "h": H, "fill": "#ffffff",
                  "stroke": "#ffffff", "source_ref": []})

    title = str(plan.get("title") or "Graphical Abstract")
    items.append({"id": "title", "type": "text", "layer": "layer-title",
                  "x": W / 2.0, "y": margin + title_font * 0.8, "text": title,
                  "anchor": "middle", "font_size": title_font, "fill": "#111111",
                  "source_ref": []})

    panels = sorted(plan.get("panels") or [], key=lambda p: p.get("order", 99))
    if not panels:
        panels = [{"id": "p_main", "role": "content", "label": "", "order": 1}]
    n = len(panels)
    data_items = plan.get("data") or []
    legend_items = plan.get("legend") or []
    data_band = (pad + len(data_items) * data_row_h) if data_items else 0.0
    legend_band = (pad + len(legend_items) * 26) if legend_items else 0.0
    footer_band = 22.0
    top = margin + title_font + 8
    panels_top = top + margin
    gap_bottom = (margin if (data_band or legend_band) else 0.0)
    panels_bottom = H - margin - data_band - legend_band - footer_band - gap_bottom

    col_w = (W - margin * (n + 1)) / n
    panel_geo = {}
    entity_bbox = {}

    for i, p in enumerate(panels):
        px = margin + i * (col_w + margin)
        ptop = panels_top
        pbot = panels_bottom
        pid = str(p.get("id", "p%d" % i))
        panel_geo[pid] = {"x": px, "y": ptop, "w": col_w, "h": pbot - ptop,
                          "role": p.get("role", ""), "label": p.get("label", "")}
        items.append({"id": "panel_%s_bg" % pid, "type": "rect", "layer": "layer-panels",
                      "x": px, "y": ptop, "w": col_w, "h": pbot - ptop,
                      "fill": "#f6f7f9", "stroke": "#c7ccd4", "rx": 6, "source_ref": []})
        items.append({"id": "panel_%s_label" % pid, "type": "text", "layer": "layer-panels",
                      "x": px + pad, "y": ptop + pad + 12,
                      "text": str(p.get("label", "")), "anchor": "start",
                      "font_size": 14, "fill": "#333333", "source_ref": []})

    # 实体按 panel 分组竖向堆叠
    by_panel = {}
    for e in plan.get("entities") or []:
        pid = str(e.get("panel") or panels[0].get("id", "p_main"))
        by_panel.setdefault(pid, []).append(e)
    first_pid = panels[0].get("id", "p_main")
    for pid, ents in by_panel.items():
        g = panel_geo.get(pid) or panel_geo.get(first_pid)
        if g is None:
            continue
        cx = g["x"] + g["w"] / 2.0
        x0 = g["x"] + pad
        x1 = g["x"] + g["w"] - pad
        y0 = g["y"] + header_h + pad
        y1 = g["y"] + g["h"] - pad
        ewidth = min((x1 - x0) * 0.72, 320.0)
        ey = y0
        for e in sorted(ents, key=lambda z: str(z.get("id", ""))):
            eid = str(e.get("id") or norm_id(e.get("label") or "e"))
            ex = cx - ewidth / 2.0
            kind = e.get("kind") or "default"
            fill = e.get("fill") or PALETTE.get(str(kind), PALETTE["default"])
            items.append({"id": "ent_%s" % eid, "type": "rect", "layer": "layer-entities",
                          "x": ex, "y": ey, "w": ewidth, "h": entity_h, "fill": fill,
                          "stroke": "#1a1a1a", "rx": 8, "kind": kind,
                          "label": e.get("label", ""), "source_ref": source_refs(e)})
            label = str(e.get("label") or eid)
            fs, shown = fit_label(label, 13, ewidth - 16, font_min)
            items.append({"id": "ent_%s_text" % eid, "type": "text", "layer": "layer-entities",
                          "x": ex + ewidth / 2.0, "y": ey + entity_h / 2.0 + fs * 0.35,
                          "text": shown, "anchor": "middle", "font_size": fs,
                          "fill": _text_fill(fill), "source_ref": source_refs(e)})
            entity_bbox[eid] = (ex, ey, ewidth, entity_h)
            ey += entity_h + 10

    # 关系箭头
    for r in plan.get("relations") or []:
        rid = str(r.get("id") or norm_id(
            str(r.get("from", "")) + str(r.get("to", "")) or "rel"))
        src = str(r.get("from", ""))
        dst = str(r.get("to", ""))
        if src not in entity_bbox or dst not in entity_bbox:
            items.append({"id": "rel_%s" % rid, "type": "note", "layer": "layer-relations",
                          "x": 0, "y": 0, "w": 0, "h": 0,
                          "text": "unresolved:%s->%s" % (src, dst),
                          "source_ref": source_refs(r), "unresolved": True})
            continue
        b1 = entity_bbox[src]
        b2 = entity_bbox[dst]
        kind = str(r.get("kind") or "flow")
        directed = bool(r.get("directed", True))
        marker = None
        if directed:
            if kind in ("inhibits", "suppresses", "down-regulates", "antagonizes"):
                marker = "url(#tbar)"
            else:
                marker = "url(#arrow)"
        d = connect_boxes(b1, b2)
        items.append({"id": "rel_%s" % rid, "type": "path", "layer": "layer-relations",
                      "d": d, "stroke": "#222222", "stroke_width": 2.0,
                      "marker_end": marker, "fill": "none", "kind": kind,
                      "source_ref": source_refs(r)})
        if r.get("label"):
            mxx = (box_center(b1)[0] + box_center(b2)[0]) / 2.0
            myy = (box_center(b1)[1] + box_center(b2)[1]) / 2.0
            items.append({"id": "rel_%s_label" % rid, "type": "text",
                          "layer": "layer-relations", "x": mxx, "y": myy - 4,
                          "text": str(r["label"]), "anchor": "middle",
                          "font_size": 11, "fill": "#111111", "halo": "#ffffff",
                          "source_ref": source_refs(r)})

    # 数据条带
    data_mapping = {}
    if data_items:
        dsx = margin + pad
        dsy = panels_bottom + margin
        ds_w = W - 2 * (margin + pad)
        bar_rows = [d for d in data_items
                    if str(d.get("encoding", "bar")) in ("bar", "length", "")]
        vals = []
        for d in bar_rows:
            try:
                vals.append(abs(float(d.get("value", 0))))
            except (TypeError, ValueError):
                vals.append(0.0)
        maxv = max(vals) if vals else 1.0
        if maxv <= 0:
            maxv = 1.0
        items.append({"id": "data_title", "type": "text", "layer": "layer-data",
                      "x": dsx, "y": dsy + 12, "text": "核心数据（单位见标签）",
                      "anchor": "start", "font_size": 13, "fill": "#333333",
                      "source_ref": []})
        row_y = dsy + pad + 8
        label_w = 240.0
        val_w = 90.0
        bar_area_w = min(bar_max_w, ds_w - label_w - val_w - 20)
        if bar_area_w < 40:
            bar_area_w = 40.0
        for idx, d in enumerate(data_items):
            did = str(d.get("id", "d%d" % idx))
            enc = str(d.get("encoding", "bar"))
            color = d.get("color") or BAR_COLORS[idx % len(BAR_COLORS)]
            if enc in ("bar", "length", ""):
                try:
                    val = float(d.get("value", 0))
                except (TypeError, ValueError):
                    val = 0.0
                blen = (abs(val) / maxv) * bar_area_w
                items.append({"id": "data_%s_label" % did, "type": "text",
                              "layer": "layer-data", "x": dsx, "y": row_y + 10,
                              "text": str(d.get("label", did)), "anchor": "start",
                              "font_size": 12, "fill": "#111111",
                              "source_ref": source_refs(d)})
                items.append({"id": "data_%s_bar" % did, "type": "rect",
                              "layer": "layer-data", "x": dsx + label_w, "y": row_y,
                              "w": blen, "h": 16, "fill": str(color),
                              "stroke": "#1a1a1a", "source_ref": source_refs(d),
                              "channel": "length", "data_ref": did})
                unit = str(d.get("unit", ""))
                items.append({"id": "data_%s_val" % did, "type": "text",
                              "layer": "layer-data", "x": dsx + label_w + blen + 8,
                              "y": row_y + 12, "text": ("%g %s" % (val, unit)).strip(),
                              "anchor": "start", "font_size": 12, "fill": "#111111",
                              "source_ref": source_refs(d)})
                data_mapping[did] = {"channel": "length",
                                     "visual_item": "data_%s_bar" % did,
                                     "value": val, "unit": unit,
                                     "source": source_refs(d)}
            else:
                items.append({"id": "data_%s_swatch" % did, "type": "rect",
                              "layer": "layer-data", "x": dsx + label_w, "y": row_y + 1,
                              "w": 18, "h": 14, "fill": str(color),
                              "stroke": "#1a1a1a", "source_ref": source_refs(d),
                              "channel": "color", "data_ref": did})
                items.append({"id": "data_%s_label" % did, "type": "text",
                              "layer": "layer-data", "x": dsx + label_w + 26,
                              "y": row_y + 12, "text": str(d.get("label", did)),
                              "anchor": "start", "font_size": 12, "fill": "#111111",
                              "source_ref": source_refs(d)})
                data_mapping[did] = {"channel": "color",
                                     "visual_item": "data_%s_swatch" % did,
                                     "value": d.get("value"), "unit": d.get("unit", ""),
                                     "source": source_refs(d)}
            row_y += data_row_h

    # 图例
    legend_y = H - margin - footer_band
    if legend_items:
        legend_y = H - margin - legend_band - footer_band + pad
        lx = margin + pad
        items.append({"id": "legend_title", "type": "text", "layer": "layer-legend",
                      "x": lx, "y": legend_y - 6, "text": "图例", "anchor": "start",
                      "font_size": 12, "fill": "#333333", "source_ref": []})
        step_x = 170.0
        for idx, lg in enumerate(legend_items):
            swx = lx + idx * step_x
            swy = legend_y + 4
            color = lg.get("color") or lg.get("value") or BAR_COLORS[idx % len(BAR_COLORS)]
            if isinstance(color, (int, float)):
                color = BAR_COLORS[idx % len(BAR_COLORS)]
            items.append({"id": "legend_%s_swatch" % str(lg.get("id", "l%d" % idx)),
                          "type": "rect", "layer": "layer-legend",
                          "x": swx, "y": swy, "w": 16, "h": 14,
                          "fill": str(color), "stroke": "#1a1a1a",
                          "source_ref": source_refs(lg),
                          "channel": str(lg.get("channel", "color"))})
            items.append({"id": "legend_%s_label" % str(lg.get("id", "l%d" % idx)),
                          "type": "text", "layer": "layer-legend",
                          "x": swx + 22, "y": swy + 12,
                          "text": str(lg.get("label", lg.get("id", "l%d" % idx))),
                          "anchor": "start", "font_size": 12, "fill": "#111111",
                          "source_ref": source_refs(lg)})

    # 页脚
    items.append({"id": "footer", "type": "text", "layer": "layer-footer",
                  "x": margin + pad, "y": H - margin - 4,
                  "text": "figure_model checksum %s · seed %d" % (plan_checksum(plan), seed),
                  "anchor": "start", "font_size": 10, "fill": "#777777",
                  "source_ref": []})

    model = {
        "version": CANONICAL_VERSION,
        "case_id": plan.get("case_id", ""),
        "figure_type": plan.get("figure_type", "graphical_abstract"),
        "seed": seed,
        "canvas": {"width": W, "height": H, "unit": canvas.get("unit", "px")},
        "checksum": plan_checksum(plan),
        "layers": layers,
        "items": sorted(items, key=lambda z: z["id"]),
        "entity_count": len(plan.get("entities") or []),
        "relation_count": len(plan.get("relations") or []),
        "data_mapping": data_mapping,
        "sources": plan.get("sources") or [],
    }
    return model


def _attr(name, val):
    return ' %s="%s"' % (name, html.escape(str(val), quote=True))


def render_item(it):
    t = it.get("type")
    data_attrs = []
    if it.get("source_ref"):
        data_attrs.append(_attr("data-source", " ".join(str(x) for x in it["source_ref"])))
    if it.get("data_ref"):
        data_attrs.append(_attr("data-ref", it["data_ref"]))
    if it.get("channel"):
        data_attrs.append(_attr("data-channel", it["channel"]))
    ds = "".join(data_attrs)
    if t == "rect":
        rx = it.get("rx", 0)
        return ('<rect id="%s" x="%g" y="%g" width="%g" height="%g" fill="%s" '
                'stroke="%s" stroke-width="1.5" rx="%g"%s/>\n' % (
                    it["id"], it.get("x", 0), it.get("y", 0), it.get("w", 0),
                    it.get("h", 0), it.get("fill", "#ffffff"),
                    it.get("stroke", "none"), rx, ds))
    if t == "text":
        anchor = it.get("anchor", "start")
        halos = ""
        if it.get("halo"):
            halos = (' stroke="%s" stroke-width="3" paint-order="stroke" '
                     'stroke-linejoin="round"' % it["halo"])
        return ('<text id="%s" x="%g" y="%g" font-size="%g" fill="%s" '
                'text-anchor="%s" font-family="Arial, Noto Sans CJK SC, '
                'sans-serif"%s%s>%s</text>\n' % (
                    it["id"], it.get("x", 0), it.get("y", 0),
                    it.get("font_size", 12), it.get("fill", "#111111"),
                    anchor, halos, ds, html.escape(str(it.get("text", "")))))
    if t == "path":
        marker = (' marker-end="%s"' % it["marker_end"]) if it.get("marker_end") else ""
        return ('<path id="%s" d="%s" fill="%s" stroke="%s" stroke-width="%g"%s%s/>\n' % (
                    it["id"], it.get("d", ""), it.get("fill", "none"),
                    it.get("stroke", "#222222"), it.get("stroke_width", 2.0),
                    marker, ds))
    if t == "line":
        return ('<line id="%s" x1="%g" y1="%g" x2="%g" y2="%g" stroke="%s" '
                'stroke-width="%g"%s/>\n' % (
                    it["id"], it.get("x1", 0), it.get("y1", 0), it.get("x2", 0),
                    it.get("y2", 0), it.get("stroke", "#222222"),
                    it.get("stroke_width", 2.0), ds))
    return '<!-- %s -->\n' % html.escape(str(it.get("id", "")), quote=True)


def render_svg(model, out_path):
    W = model["canvas"]["width"]
    H = model["canvas"]["height"]
    parts = ['<?xml version="1.0" encoding="UTF-8"?>\n']
    parts.append('<svg xmlns="http://www.w3.org/2000/svg" width="%g" height="%g" '
                 'viewBox="0 0 %g %g">\n' % (W, H, W, H))
    parts.append("<defs>\n")
    parts.append('  <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
                 'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                 '<path d="M0,0 L10,5 L0,10 z" fill="#222222"/></marker>\n')
    parts.append('  <marker id="tbar" viewBox="0 0 10 10" refX="8" refY="5" '
                 'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                 '<path d="M7,0 L7,10" stroke="#222222" stroke-width="2.5" '
                 'fill="none"/></marker>\n')
    parts.append("</defs>\n")
    items = model.get("items") or []
    by_layer = {}
    for it in items:
        by_layer.setdefault(it.get("layer", "layer-title"), []).append(it)
    seen = set()
    for layer in SVG_LAYER_ORDER:
        seen.add(layer)
        group = by_layer.get(layer, [])
        parts.append('<g id="%s">\n' % layer)
        for it in group:
            parts.append(render_item(it))
        parts.append("</g>\n")
    for layer in sorted(by_layer):
        if layer in seen:
            continue
        parts.append('<g id="%s">\n' % layer)
        for it in by_layer[layer]:
            parts.append(render_item(it))
        parts.append("</g>\n")
    parts.append("</svg>\n")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("".join(parts))
    return out_path


def _run(cmd, timeout=60):
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=timeout, check=False)
        return True
    except Exception:
        return False


def _is_png(path):
    try:
        with open(path, "rb") as f:
            return f.read(8) == b"\x89PNG\r\n\x1a\n"
    except OSError:
        return False


def _is_pdf(path):
    try:
        with open(path, "rb") as f:
            return f.read(5) == b"%PDF-"
    except OSError:
        return False


def try_render(svg_path, out_base, timeout=60):
    res = {"png": {"path": None, "renderer": None, "status": "preview_not_rendered"},
           "pdf": {"path": None, "renderer": None, "status": "preview_not_rendered"}}
    png_path = out_base + ".png"
    pdf_path = out_base + ".pdf"
    try:
        import cairosvg  # noqa: F401
        cairosvg.svg2png(url=svg_path, write_to=png_path)
        if _is_png(png_path):
            res["png"] = {"path": png_path, "renderer": "cairosvg", "status": "rendered"}
        cairosvg.svg2pdf(url=svg_path, write_to=pdf_path)
        if _is_pdf(pdf_path):
            res["pdf"] = {"path": pdf_path, "renderer": "cairosvg", "status": "rendered"}
        if res["png"]["status"] == "rendered":
            return res
    except Exception:
        pass
    png_exes = ("rsvg-convert", "inkscape", "chromium", "chromium-browser",
                "google-chrome", "chrome")
    for exe in png_exes:
        ok = False
        if exe == "rsvg-convert":
            ok = _run([exe, "-o", png_path, svg_path], timeout)
        elif exe == "inkscape":
            ok = _run([exe, "--export-type=png", "--export-filename",
                       png_path, svg_path], timeout)
        else:
            ok = _run([exe, "--headless", "--disable-gpu", "--no-sandbox",
                       "--screenshot=%s" % png_path, "--window-size=1600,1000",
                       "file://" + os.path.abspath(svg_path)], timeout)
        if ok and _is_png(png_path):
            res["png"] = {"path": png_path, "renderer": exe, "status": "rendered"}
            break
    for exe in png_exes:
        ok = False
        if exe == "rsvg-convert":
            ok = _run([exe, "-f", "pdf", "-o", pdf_path, svg_path], timeout)
        elif exe == "inkscape":
            ok = _run([exe, "--export-type=pdf", "--export-filename",
                       pdf_path, svg_path], timeout)
        else:
            ok = _run([exe, "--headless", "--disable-gpu", "--no-sandbox",
                       "--print-to-pdf=%s" % pdf_path,
                       "file://" + os.path.abspath(svg_path)], timeout)
        if ok and _is_pdf(pdf_path):
            res["pdf"] = {"path": pdf_path, "renderer": exe, "status": "rendered"}
            break
    return res


def validate_model(model, svg_path=None):
    issues = []
    W = float(model.get("canvas", {}).get("width", 0))
    H = float(model.get("canvas", {}).get("height", 0))
    for it in model.get("items") or []:
        if it.get("layer") in ("layer-entities", "layer-relations", "layer-data") and \
           not it.get("source_ref") and it.get("type") not in ("text", "note"):
            issues.append("missing source: %s" % it["id"])
        if it.get("type") == "text" and it.get("layer") == "layer-entities" and \
           float(it.get("font_size", 12)) < 8:
            issues.append("font<8px: %s" % it["id"])
        x = it.get("x", 0)
        y = it.get("y", 0)
        if it.get("type") == "rect":
            if x < -1 or y < -1 or x + it.get("w", 0) > W + 1 or y + it.get("h", 0) > H + 1:
                issues.append("overflow: %s" % it["id"])
        elif it.get("type") in ("text", "path"):
            if x < -1 or y < -1 or x > W + 1 or y > H + 1:
                issues.append("overflow: %s" % it["id"])
    if svg_path and os.path.isfile(svg_path):
        try:
            ET.parse(svg_path)
        except Exception as e:
            issues.append("svg parse failed: %s" % e)
    else:
        issues.append("svg missing")
    return issues
