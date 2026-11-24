#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render.py — 声明式图模型 → 可编辑 SVG + 真实 PNG/PDF（确定性渲染器）。

LLM 填 figure_model.json（每图元带 value/几何/样式/source），本脚本确定性渲染，
渲染阶段无 LLM 判断故可复现；无 matplotlib 时只出 SVG 并如实记录（不伪造导出件）。
"""
import argparse
import json
import math
import os
import sys
import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
DEFAULT_PALETTE = ["#4C72B0", "#DD8452", "#55A868", "#C44E52", "#8172B2", "#937860"]

# 几何图元种类（SVG pt，原点左上，y 向下）
GEOM_KINDS = {"rect", "line", "arrow", "circle", "ellipse", "text", "polyline",
              "path", "hline", "vline"}


def load_json(path):
    if not path or not os.path.exists(path):
        return None, ["missing:%s" % path]
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f), []
    except Exception as e:
        return None, ["json_parse_failed:%s:%s" % (path, e)]


def num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _scale_pos(v, lo, hi, lo_px, hi_px, scale):
    """数据值 v → 像素坐标（linear 或 log10）。"""
    if scale == "log":
        if v <= 0 or lo <= 0:
            return lo_px
        v, lo, hi = math.log10(v), math.log10(lo), math.log10(hi)
    if hi == lo:
        return lo_px
    return lo_px + (v - lo) / (hi - lo) * (hi_px - lo_px)


def _group_spacing(xvals):
    xs = sorted(set(xvals))
    if len(xs) < 2:
        return 1.0
    diffs = [xs[i + 1] - xs[i] for i in range(len(xs) - 1) if xs[i + 1] > xs[i]]
    diffs = sorted(diffs)
    return diffs[len(diffs) // 2] if diffs else 1.0


def _source_of(mark):
    s = mark.get("source") if isinstance(mark, dict) else None
    return s if isinstance(s, dict) else None


def _rec_marks(obj, out):
    """递归收集 panels→series→marks / panels→marks 中所有带 source 的图元。"""
    if isinstance(obj, dict):
        if "source" in obj and isinstance(obj.get("source"), dict):
            out.append(obj)
        for v in obj.values():
            _rec_marks(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _rec_marks(v, out)


def build_scene(model):
    """figure_model.json → 规范化场景 {canvas, font, shapes[], meta[]}。

    shapes 中每个元素含 id/kind/geom/style/text/source；meta 为 id→source 映射。
    """
    issues = []
    canvas = model.get("canvas", {}) or {}
    w = num(canvas.get("width_pt"), 360)
    h = num(canvas.get("height_pt"), 300)
    bg = canvas.get("background", "#FFFFFF") or "#FFFFFF"
    font = model.get("font", {}) or {}
    palette = model.get("palette") or DEFAULT_PALETTE

    shapes = []
    meta = {}

    def add(shape):
        if not isinstance(shape, dict) or not shape.get("kind"):
            return
        shapes.append(shape)
        if shape.get("source"):
            meta[shape["id"]] = shape["source"]
        elif shape.get("id"):
            meta.setdefault(shape["id"], None)

    # ---- 面板 ----
    for pi, panel in enumerate(model.get("panels", []) or []):
        bbox = panel.get("bbox") or [40, 20, w - 20, h - 30]
        try:
            x0, y0, x1, y1 = [num(v) for v in bbox[:4]]
        except Exception:
            x0, y0, x1, y1 = 40, 20, w - 20, h - 30
            issues.append("panel_%s_bad_bbox" % panel.get("id", pi))
        W, H = x1 - x0, y1 - y0
        axes = panel.get("axes") or {}
        xax = axes.get("x") or {}
        yax = axes.get("y") or {}
        x_min = num(xax.get("min"), 0); x_max = num(xax.get("max"), 1)
        y_min = num(yax.get("min"), 0); y_max = num(yax.get("max"), 1)
        x_scale = xax.get("scale", "linear") or "linear"
        y_scale = yax.get("scale", "linear") or "linear"

        def xpx(v):
            return _scale_pos(v, x_min, x_max, x0, x1, x_scale)

        def ypx(v):
            return _scale_pos(v, y_min, y_max, y1, y0, y_scale)

        # 轴线与刻度
        add({"id": "axis_%s_x" % panel.get("id", pi), "kind": "line",
             "geom": {"x1": x0, "y1": y1, "x2": x1, "y2": y1},
             "style": {"stroke": "#222222", "stroke_width": 0.6}})
        add({"id": "axis_%s_y" % panel.get("id", pi), "kind": "line",
             "geom": {"x1": x0, "y1": y0, "x2": x0, "y2": y1},
             "style": {"stroke": "#222222", "stroke_width": 0.6}})
        for t in yax.get("ticks", []) or []:
            tv = num(t.get("value") if isinstance(t, dict) else t)
            if y_scale == "log" and tv <= 0:
                continue
            yy = ypx(tv)
            label = t.get("label") if isinstance(t, dict) else str(tv)
            add({"id": "ytick_%s_%g" % (panel.get("id", pi), tv), "kind": "line",
                 "geom": {"x1": x0 - 3, "y1": yy, "x2": x0, "y2": yy},
                 "style": {"stroke": "#222222", "stroke_width": 0.5}})
            add({"id": "ytickl_%s_%g" % (panel.get("id", pi), tv), "kind": "text",
                 "geom": {"x": x0 - 5, "y": yy, "anchor": "end", "size": num(font.get("size_pt"), 7)},
                 "text": label, "style": {"fill": "#222222"}})
        for t in xax.get("ticks", []) or []:
            tv = num(t.get("pos") if isinstance(t, dict) else t)
            xx = xpx(tv)
            label = (t.get("label") if isinstance(t, dict) else str(tv)) or str(tv)
            add({"id": "xtickl_%s_%s" % (panel.get("id", pi), str(label)), "kind": "text",
                 "geom": {"x": xx, "y": y1 + 10, "anchor": "middle",
                          "size": num(font.get("size_pt"), 7)},
                 "text": label, "style": {"fill": "#222222"}})
        x_title = xax.get("title")
        if x_title:
            add({"id": "xtitle_%s" % panel.get("id", pi), "kind": "text",
                 "geom": {"x": (x0 + x1) / 2, "y": y1 + 22, "anchor": "middle",
                          "size": num(font.get("size_pt"), 7), "bold": True},
                 "text": x_title, "style": {"fill": "#222222"}})
        y_title = yax.get("title")
        if y_title:
            add({"id": "ytitle_%s" % panel.get("id", pi), "kind": "text",
                 "geom": {"x": x0 - 28, "y": (y0 + y1) / 2, "anchor": "middle",
                          "size": num(font.get("size_pt"), 7), "bold": True, "rotate": -90},
                 "text": y_title, "style": {"fill": "#222222"}})

        # 数据系列
        series = panel.get("series") or []
        n_series = max(1, len(series))
        all_x = []
        for s in series:
            for m in (s.get("marks") or []):
                if "x_data" in m:
                    all_x.append(num(m["x_data"]))
        spacing = _group_spacing(all_x) if all_x else 1.0
        slot_w = spacing * 0.7 / n_series
        bar_w = panel.get("bar_width") or (slot_w * 0.8)
        # 数据单位宽度 → 像素宽度
        if x_scale == "log" or x_max == x_min:
            bar_w_px = num(panel.get("bar_width_pt"), 8)
            slot_w_px = num(panel.get("bar_width_pt"), 8) / 0.8
        else:
            bar_w_px = bar_w / (x_max - x_min) * W
            slot_w_px = slot_w / (x_max - x_min) * W
        y_base = ypx(0) if y_min <= 0 <= y_max else (y1 if 0 < y_min else y0)

        for si, s in enumerate(series):
            color = s.get("color") or palette[si % len(palette)]
            sname = s.get("name") or "series_%d" % si
            offset_px = (si - (n_series - 1) / 2) * slot_w_px
            pts = []
            for m in (s.get("marks") or []):
                mid = m.get("id") or "%s_%d" % (sname, len(pts))
                src = _source_of(m)
                x_data = num(m.get("x_data"))
                xx = xpx(x_data) + offset_px
                kind = s.get("kind") or panel.get("kind") or model.get("figure_type", "bar")
                if kind in ("bar", "box"):
                    value = num(m.get("value"))
                    if "stats" in m and isinstance(m["stats"], dict):
                        st = m["stats"]
                        q1, med, q3 = num(st.get("q1")), num(st.get("median")), num(st.get("q3"))
                        lo, hi = num(st.get("lower")), num(st.get("upper"))
                        b_w = bar_w_px
                        add({"id": mid + "_whisker_lo", "kind": "line",
                             "geom": {"x1": xx, "y1": ypx(lo), "x2": xx, "y2": ypx(q1)},
                             "style": {"stroke": "#222222", "stroke_width": 0.6}, "source": src})
                        add({"id": mid + "_whisker_hi", "kind": "line",
                             "geom": {"x1": xx, "y1": ypx(q3), "x2": xx, "y2": ypx(hi)},
                             "style": {"stroke": "#222222", "stroke_width": 0.6}, "source": src})
                        add({"id": mid, "kind": "rect",
                             "geom": {"x": xx - b_w / 2, "y": min(ypx(q1), ypx(q3)),
                                      "w": b_w, "h": abs(ypx(q3) - ypx(q1))},
                             "style": {"fill": color, "stroke": "#222222", "stroke_width": 0.5},
                             "source": src})
                        add({"id": mid + "_med", "kind": "line",
                             "geom": {"x1": xx - b_w / 2, "y1": ypx(med), "x2": xx + b_w / 2, "y2": ypx(med)},
                             "style": {"stroke": "#111111", "stroke_width": 0.9}, "source": src})
                        for oi, ov in enumerate(m.get("outliers") or []):
                            add({"id": mid + "_out_%d" % oi, "kind": "circle",
                                 "geom": {"cx": xx, "cy": ypx(num(ov)), "r": 1.4},
                                 "style": {"fill": "none", "stroke": "#222222", "stroke_width": 0.5},
                                 "source": src})
                        continue
                    top = ypx(value)
                    yy = min(top, y_base)
                    hh = max(abs(y_base - top), 0.01)
                    add({"id": mid, "kind": "rect",
                         "geom": {"x": xx - bar_w_px / 2, "y": yy, "w": bar_w_px, "h": hh},
                         "style": {"fill": color, "stroke": "#333333", "stroke_width": 0.4},
                         "source": src})
                    if m.get("error") is not None:
                        err = num(m["error"])
                        e_top, e_bot = ypx(value + err), ypx(value - err)
                        add({"id": mid + "_err", "kind": "line",
                             "geom": {"x1": xx, "y1": e_bot, "x2": xx, "y2": e_top},
                             "style": {"stroke": "#111111", "stroke_width": 0.6}, "source": src})
                        add({"id": mid + "_errcap1", "kind": "line",
                             "geom": {"x1": xx - 2, "y1": e_top, "x2": xx + 2, "y2": e_top},
                             "style": {"stroke": "#111111", "stroke_width": 0.6}, "source": src})
                        add({"id": mid + "_errcap2", "kind": "line",
                             "geom": {"x1": xx - 2, "y1": e_bot, "x2": xx + 2, "y2": e_bot},
                             "style": {"stroke": "#111111", "stroke_width": 0.6}, "source": src})
                    if m.get("show_value"):
                        add({"id": mid + "_val", "kind": "text",
                             "geom": {"x": xx, "y": top - 3, "anchor": "middle",
                                      "size": num(font.get("size_pt"), 7) - 1},
                             "text": str(value), "style": {"fill": "#222222"}, "source": src})
                elif kind == "scatter":
                    yy = ypx(num(m.get("value")))
                    pts.append((xx, yy))
                    add({"id": mid, "kind": "circle", "geom": {"cx": xx, "cy": yy, "r": 2.2},
                         "style": {"fill": color, "stroke": "#333333", "stroke_width": 0.4},
                         "source": src})
                    if m.get("error") is not None:
                        err = num(m["error"])
                        add({"id": mid + "_err", "kind": "line",
                             "geom": {"x1": xx, "y1": ypx(num(m["value"]) + err),
                                      "x2": xx, "y2": ypx(num(m["value"]) - err)},
                             "style": {"stroke": "#111111", "stroke_width": 0.6}, "source": src})
                elif kind == "line":
                    yy = ypx(num(m.get("value")))
                    pts.append((xx, yy))
                    add({"id": mid, "kind": "circle", "geom": {"cx": xx, "cy": yy, "r": 2.2},
                         "style": {"fill": color, "stroke": "#333333", "stroke_width": 0.4},
                         "source": src})
            if kind == "line" and len(pts) >= 2:
                add({"id": "%s_line" % sname, "kind": "polyline",
                     "geom": {"points": [list(p) for p in pts]},
                     "style": {"stroke": color, "stroke_width": 1.1, "fill": "none"},
                     "source": _source_of(s.get("marks")[0]) if s.get("marks") else None})

        # 面板级几何图元
        for m in panel.get("marks") or []:
            mid = m.get("id") or "geom_%d" % len(shapes)
            kind = m.get("kind")
            src = _source_of(m)
            geom = dict(m.get("geom") or {})
            style = dict(m.get("style") or {})
            if kind in GEOM_KINDS:
                add({"id": mid, "kind": kind, "geom": geom, "style": style,
                     "text": m.get("text"), "source": src})
            else:
                issues.append("unknown_mark_kind:%s" % kind)

    # 图例
    for li, leg in enumerate(model.get("legends") or []):
        bb = leg.get("bbox") or [x1 + 10, y0, w - 10, y0 + 60] if 'x1' in dir() else [w - 90, 20, w - 10, 90]
        try:
            lx, ly, lx2, ly2 = [num(v) for v in bb[:4]]
        except Exception:
            lx, ly, lx2, ly2 = w - 90, 20, w - 10, 90
        cur_y = ly + 6
        for ei, e in enumerate(leg.get("entries") or []):
            color = e.get("color") or palette[ei % len(palette)]
            label = e.get("label") or ""
            add({"id": "legend_%d_%d_sw" % (li, ei), "kind": "rect",
                 "geom": {"x": lx, "y": cur_y - 4, "w": 10, "h": 8},
                 "style": {"fill": color, "stroke": "#333333", "stroke_width": 0.4}})
            add({"id": "legend_%d_%d_lb" % (li, ei), "kind": "text",
                 "geom": {"x": lx + 13, "y": cur_y, "anchor": "start",
                          "size": num(font.get("size_pt"), 7)},
                 "text": label, "style": {"fill": "#222222"}})
            cur_y += 12

    # 标注
    for a in model.get("annotations") or []:
        aid = a.get("id") or "ann_%d" % len(shapes)
        at = a.get("at") or [num(canvas.get("width_pt", 360)) / 2, num(canvas.get("height_pt", 300)) - 10]
        try:
            ax, ay = num(at[0]), num(at[1])
        except Exception:
            ax, ay = num(canvas.get("width_pt", 360)) / 2, num(canvas.get("height_pt", 300)) - 10
        txt = a.get("text", "")
        style = dict(a.get("style") or {})
        add({"id": aid, "kind": "text",
             "geom": {"x": ax, "y": ay, "anchor": a.get("anchor", "middle"),
                      "size": num(font.get("size_pt"), 7)},
             "text": txt, "style": style, "source": _source_of(a)})

    # 图注
    cap = model.get("caption")
    if cap:
        add({"id": "caption", "kind": "text",
             "geom": {"x": num(canvas.get("width_pt", 360)) / 2,
                      "y": num(canvas.get("height_pt", 300)) - 6, "anchor": "middle",
                      "size": num(font.get("size_pt"), 7)},
             "text": cap, "style": {"fill": "#222222"}})

    return {"canvas": {"w": w, "h": h, "bg": bg}, "font": font,
            "shapes": shapes, "meta": meta, "issues": issues}


# SVG 渲染

def _svg_style(style):
    m = {
        "fill": style.get("fill", "none"),
        "stroke": style.get("stroke", "none"),
        "stroke-width": str(style.get("stroke_width", 1.0)),
    }
    if style.get("dash"):
        m["stroke-dasharray"] = style["dash"]
    return m


def _et_el(tag, attrib, text=None):
    el = ET.Element("{%s}%s" % (SVG_NS, tag), attrib)
    if text is not None:
        el.text = str(text)
    return el


def write_svg(scene, path, font_default):
    w, h, bg = scene["canvas"]["w"], scene["canvas"]["h"], scene["canvas"]["bg"]
    root = ET.Element("{%s}svg" % SVG_NS, {
        "width": "%gpt" % w, "height": "%gpt" % h,
        "viewBox": "0 0 %g %g" % (w, h), "version": "1.1",
    })
    root.append(_et_el("rect", {"x": "0", "y": "0", "width": "%g" % w, "height": "%g" % h,
                                "fill": bg}))
    font_family = scene["font"].get("family") or font_default or "sans-serif"

    g = _et_el("g", {"id": "scene"})
    for s in scene["shapes"]:
        kind, geom, style, text, sid = s["kind"], s.get("geom") or {}, s.get("style") or {}, s.get("text"), s["id"]
        attrs = {"id": sid}
        if s.get("source"):
            attrs["data-src"] = json.dumps(s["source"], ensure_ascii=False)
        st = _svg_style(style)
        if kind == "rect":
            attrs.update({"x": "%g" % num(geom.get("x")), "y": "%g" % num(geom.get("y")),
                          "width": "%g" % num(geom.get("w")), "height": "%g" % num(geom.get("h"))})
            attrs.update(st)
            g.append(_et_el("rect", attrs))
        elif kind == "line":
            attrs.update({"x1": "%g" % num(geom.get("x1")), "y1": "%g" % num(geom.get("y1")),
                          "x2": "%g" % num(geom.get("x2")), "y2": "%g" % num(geom.get("y2"))})
            attrs.update(st)
            g.append(_et_el("line", attrs))
        elif kind == "hline":
            attrs.update({"x1": "%g" % num(geom.get("x1", 0)), "y1": "%g" % num(geom.get("y")),
                          "x2": "%g" % num(geom.get("x2", w)), "y2": "%g" % num(geom.get("y"))})
            attrs.update(st)
            g.append(_et_el("line", attrs))
        elif kind == "vline":
            attrs.update({"x1": "%g" % num(geom.get("x")), "y1": "%g" % num(geom.get("y1", 0)),
                          "x2": "%g" % num(geom.get("x")), "y2": "%g" % num(geom.get("y2", h))})
            attrs.update(st)
            g.append(_et_el("line", attrs))
        elif kind == "arrow":
            x1, y1, x2, y2 = [num(geom.get(k)) for k in ("x1", "y1", "x2", "y2")]
            attrs.update({"x1": "%g" % x1, "y1": "%g" % y1, "x2": "%g" % x2, "y2": "%g" % y2})
            attrs.update(st)
            attrs["marker-end"] = "url(#arrowhead)"
            g.append(_et_el("line", attrs))
        elif kind == "circle":
            attrs.update({"cx": "%g" % num(geom.get("cx")), "cy": "%g" % num(geom.get("cy")),
                          "r": "%g" % num(geom.get("r", 2))})
            attrs.update(st)
            g.append(_et_el("circle", attrs))
        elif kind == "ellipse":
            attrs.update({"cx": "%g" % num(geom.get("cx")), "cy": "%g" % num(geom.get("cy")),
                          "rx": "%g" % num(geom.get("rx")), "ry": "%g" % num(geom.get("ry"))})
            attrs.update(st)
            g.append(_et_el("ellipse", attrs))
        elif kind == "polyline":
            pts = geom.get("points") or []
            pstr = " ".join("%g,%g" % (num(p[0]), num(p[1])) for p in pts if len(p) >= 2)
            attrs.update({"points": pstr})
            attrs.update(st)
            g.append(_et_el("polyline", attrs))
        elif kind == "path":
            attrs.update({"d": geom.get("d", "")})
            attrs.update(st)
            g.append(_et_el("path", attrs))
        elif kind == "text":
            txt = text if text is not None else geom.get("text", "")
            size = num(geom.get("size"), num(scene["font"].get("size_pt"), 8))
            tattrs = {"id": sid, "x": "%g" % num(geom.get("x")), "y": "%g" % num(geom.get("y")),
                      "font-family": style.get("family") or font_family,
                      "font-size": "%g" % size,
                      "text-anchor": geom.get("anchor", "start"),
                      "fill": style.get("fill", "#222222")}
            if geom.get("bold"):
                tattrs["font-weight"] = "bold"
            if geom.get("rotate"):
                tattrs["transform"] = "rotate(%g %g %g)" % (num(geom["rotate"]),
                                                             num(geom.get("x")), num(geom.get("y")))
            if s.get("source"):
                tattrs["data-src"] = json.dumps(s["source"], ensure_ascii=False)
            g.append(_et_el("text", tattrs, txt))
    root.append(g)

    # 箭头 defs
    defs = _et_el("defs", {})
    marker = ET.Element("{%s}marker" % SVG_NS, {"id": "arrowhead", "markerWidth": "6",
                                                "markerHeight": "6", "refX": "5", "refY": "3",
                                                "orient": "auto"})
    marker.append(_et_el("path", {"d": "M0,0 L6,3 L0,6 Z", "fill": "#222222"}))
    defs.append(marker)
    root.insert(1, defs)

    # 溯源 metadata（id→source）
    meta = _et_el("metadata", {})
    meta.text = json.dumps({"provenance": scene["meta"]}, ensure_ascii=False)
    root.append(meta)

    ET.register_namespace("", SVG_NS)
    tree = ET.ElementTree(root)
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return path


# matplotlib 渲染（PNG/PDF）

def _try_import_mpl():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle, Circle, Ellipse, FancyArrowPatch
        return plt, Rectangle, Circle, Ellipse, FancyArrowPatch
    except Exception:
        return None


def _has_cjk(s):
    return any('\u4e00' <= c <= '\u9fff' for c in str(s))


def _pick_cjk_font():
    try:
        from matplotlib import font_manager as fm
        for name in ("Noto Sans CJK SC", "Noto Sans CJK JP", "WenQuanYi Zen Hei",
                     "SimHei", "Microsoft YaHei", "PingFang SC", "Arial Unicode MS"):
            try:
                fm.findfont(name, fallback_to_default=False)
                return name
            except Exception:
                continue
    except Exception:
        pass
    return None


def render_mpl(scene, out_dir, dpi, basename):
    """用 matplotlib 把场景重绘为 PNG + PDF；返回 (png, pdf, issues)。"""
    mpl = _try_import_mpl()
    if mpl is None:
        return None, None, ["matplotlib_unavailable"]
    plt, Rectangle, Circle, Ellipse, FancyArrowPatch = mpl
    w, h = scene["canvas"]["w"], scene["canvas"]["h"]
    fig, ax = plt.subplots(figsize=(w / 72.0, h / 72.0))
    ax.set_xlim(0, w)
    ax.set_ylim(0, h)
    ax.invert_yaxis()
    ax.set_aspect("equal")
    ax.axis("off")
    fig.patch.set_facecolor(scene["canvas"]["bg"])

    need_cjk = any(_has_cjk(s.get("text")) for s in scene["shapes"])
    cjk_font = _pick_cjk_font() if need_cjk else None
    import matplotlib as mplroot
    mplroot.rcParams["font.family"] = "sans-serif"

    issues = []
    for s in scene["shapes"]:
        kind, geom, style, text = s["kind"], s.get("geom") or {}, s.get("style") or {}, s.get("text")
        try:
            if kind == "rect":
                ax.add_patch(Rectangle((num(geom.get("x")), num(geom.get("y"))),
                                       num(geom.get("w")), num(geom.get("h")),
                                       facecolor=style.get("fill", "none"),
                                       edgecolor=style.get("stroke", "none"),
                                       linewidth=num(style.get("stroke_width"), 1)))
            elif kind in ("line", "hline", "vline", "arrow"):
                if kind == "hline":
                    x1, y1, x2, y2 = num(geom.get("x1", 0)), num(geom.get("y")), num(geom.get("x2", w)), num(geom.get("y"))
                elif kind == "vline":
                    x1, y1, x2, y2 = num(geom.get("x")), num(geom.get("y1", 0)), num(geom.get("x")), num(geom.get("y2", h))
                else:
                    x1, y1, x2, y2 = [num(geom.get(k)) for k in ("x1", "y1", "x2", "y2")]
                if kind == "arrow":
                    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2),
                                                 arrowstyle="-|>", mutation_scale=8,
                                                 color=style.get("stroke", "#222222"),
                                                 linewidth=num(style.get("stroke_width"), 1)))
                else:
                    ax.plot([x1, x2], [y1, y2], color=style.get("stroke", "#222222"),
                            linewidth=num(style.get("stroke_width"), 1), solid_capstyle="round")
            elif kind == "circle":
                ax.add_patch(Circle((num(geom.get("cx")), num(geom.get("cy"))), num(geom.get("r", 2)),
                                    facecolor=style.get("fill", "none"),
                                    edgecolor=style.get("stroke", "none"),
                                    linewidth=num(style.get("stroke_width"), 1)))
            elif kind == "ellipse":
                ax.add_patch(Ellipse((num(geom.get("cx")), num(geom.get("cy"))),
                                     num(geom.get("rx")), num(geom.get("ry")),
                                     facecolor=style.get("fill", "none"),
                                     edgecolor=style.get("stroke", "none"),
                                     linewidth=num(style.get("stroke_width"), 1)))
            elif kind == "polyline":
                pts = [p for p in (geom.get("points") or []) if len(p) >= 2]
                if pts:
                    xs = [num(p[0]) for p in pts]; ys = [num(p[1]) for p in pts]
                    ax.plot(xs, ys, color=style.get("stroke", "#222222"),
                            linewidth=num(style.get("stroke_width"), 1))
            elif kind == "text":
                txt = text if text is not None else geom.get("text", "")
                ha = {"start": "left", "middle": "center", "end": "right"}.get(geom.get("anchor", "start"), "left")
                va = "baseline"
                ax.text(num(geom.get("x")), num(geom.get("y")), str(txt),
                        ha=ha, va=va, color=style.get("fill", "#222222"),
                        fontsize=num(geom.get("size"), num(scene["font"].get("size_pt"), 8)),
                        family=cjk_font if (cjk_font and _has_cjk(txt)) else None,
                        fontweight="bold" if geom.get("bold") else "normal",
                        rotation=num(geom.get("rotate"), 0))
            # path 跳过
            elif kind == "path":
                issues.append("path_skipped_in_mpl:%s" % s.get("id"))
        except Exception as e:
            issues.append("mpl_draw_fail:%s:%s" % (s.get("id"), e))

    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
    png = os.path.join(out_dir, "%s.png" % basename)
    pdf = os.path.join(out_dir, "%s.pdf" % basename)
    try:
        fig.savefig(png, dpi=dpi, facecolor=fig.get_facecolor())
    except Exception as e:
        issues.append("png_fail:%s" % e); png = None
    try:
        fig.savefig(pdf, facecolor=fig.get_facecolor())
    except Exception as e:
        issues.append("pdf_fail:%s" % e); pdf = None
    plt.close(fig)
    return png, pdf, issues


# 主流程

def main(argv=None):
    ap = argparse.ArgumentParser(description="figure_model.json → SVG + PNG/PDF")
    ap.add_argument("--model", required=True)
    ap.add_argument("--data", default=None)
    ap.add_argument("--out", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    ap.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--font", default=None, help="SVG 兜底字族")
    a = ap.parse_args(argv)

    os.makedirs(a.out, exist_ok=True)
    os.makedirs(a.work, exist_ok=True)

    model, errs = load_json(a.model)
    if model is None:
        print(json.dumps({"ok": False, "errors": errs}, ensure_ascii=False))
        return 1

    scene = build_scene(model)
    scene["issues"].extend(errs)

    # 溯源校验
    marks = []
    _rec_marks(model, marks)
    missing = [m.get("id") for m in marks if not (m.get("source", {}).get("locator") and
                                                  m.get("source", {}).get("quote"))]
    if missing:
        scene["issues"].append("marks_without_source:%d" % len(missing))

    fig_id = model.get("figure_id") or "figure"
    svg_path = os.path.join(a.out, "%s.svg" % fig_id)
    write_svg(scene, svg_path, a.font)

    # 中间表示落盘
    with open(os.path.join(a.work, "scene.json"), "w", encoding="utf-8") as f:
        json.dump(scene, f, ensure_ascii=False, indent=2)

    png, pdf, mpl_issues = render_mpl(scene, a.out, a.dpi, fig_id)
    scene["issues"].extend(mpl_issues)

    manifest = {
        "ok": True,
        "figure_id": fig_id,
        "svg": os.path.relpath(svg_path, a.out).replace("\\", "/"),
        "png": os.path.relpath(png, a.out).replace("\\", "/") if png else None,
        "pdf": os.path.relpath(pdf, a.out).replace("\\", "/") if pdf else None,
        "dpi": a.dpi,
        "color_space": "RGB",
        "background": scene["canvas"]["bg"],
        "font": scene["font"].get("family"),
        "marks_total": len(scene["shapes"]),
        "marks_with_source": len([s for s in scene["shapes"] if s.get("source")]),
        "missing_source_ids": missing,
        "issues": scene["issues"],
    }
    with open(os.path.join(a.work, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
