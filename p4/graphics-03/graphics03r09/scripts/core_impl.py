#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — 生物机制图/通路图 Skill 核心实现（仅 Python 标准库）。

子命令逻辑（由 main.py 分发，也可直接 import）：
  validate(spec_path, report_path, norm_path=None) -> (errors, warnings)
  build(spec_path, svg_path)                       -> svg 路径或抛 ValueError
  check(result_path, output_dir)                   -> (errors, warnings)
  write_skeleton(out_path, case_id, title)         -> 写入 partial 骨架

约定：
  * 全流程确定性（无随机、无时间依赖的布局抖动），重复运行输出一致。
  * 文本按 utf-8-sig 读取并容错 BOM / 坏 JSON；坏输入降级不崩溃。
  * SVG 为分层、可编辑源文件：命名图层 + 语义箭头 + 区室布局 + 自洽图例
    + data-* 溯源属性。
"""
from __future__ import annotations

import json
import os
import time
from xml.sax.saxutils import escape

ALLOWED_KINDS = {
    "gene", "protein", "complex", "family", "cell_type",
    "small_molecule", "process", "compartment",
}
ALLOWED_REL_TYPES = {
    "activation", "inhibition", "binding", "modification", "transport", "conversion",
}
ALLOWED_STATUS = {"success", "partial", "failed", "insufficient_evidence", "input_error"}

DEFAULT_COMPARTMENTS = ["extracellular", "membrane", "cytosol", "nucleus"]

# 实体种类 -> (形状, 填充色, 描边色, 名称样式)
KIND_STYLE = {
    "gene":           ("rect",      "#E2EFDA", "#548235", "italic"),
    "protein":        ("roundrect", "#DEEBF7", "#2E75B6", "normal"),
    "complex":        ("roundrect", "#FBE5D6", "#C55A11", "normal"),
    "family":         ("roundrect", "#E4DFEC", "#7030A0", "normal"),
    "cell_type":      ("ellipse",   "#FCE4D6", "#ED7D31", "normal"),
    "small_molecule": ("circle",    "#FFF2CC", "#BF9000", "normal"),
    "process":        ("diamond",   "#EDEDED", "#7F7F7F", "italic"),
    "compartment":    ("band",      "#F2F2F2", "#A6A6A6", "normal"),
}

# 关系类型 -> (端点样式, 线色, 线型)
REL_STYLE = {
    "activation":   ("arrow",       "#1A1A1A", ""),
    "inhibition":   ("tbar",        "#1A1A1A", ""),
    "binding":      ("none",        "#7F7F7F", "6,3"),
    "modification": ("circle",      "#2E75B6", ""),
    "transport":    ("open_arrow",  "#548235", ""),
    "conversion":   ("diamond",     "#C00000", ""),
}

REL_DEFAULT_LABEL = {
    "activation": "激活", "inhibition": "抑制", "binding": "结合",
    "modification": "修饰", "transport": "转运", "conversion": "转化",
}


def _read_text(path):
    """utf-8-sig 优先，失败后尝试常见编码；不可读时返回空串。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError:
            return ""
    return ""


def load_json(path):
    """读取 JSON，返回 (obj, error)。obj 在失败时为 None。"""
    text = _read_text(path)
    if text == "" and not os.path.exists(path):
        return None, "missing file: %s" % path
    try:
        return json.loads(text), None
    except ValueError as exc:  # 坏 JSON
        # 尝试去掉 BOM 后再解析一次
        try:
            return json.loads(text.lstrip("\ufeff")), None
        except ValueError:
            return None, "invalid JSON in %s: %s" % (path, exc)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _uid(prefix, seen):
    i = 1
    while "%s%d" % (prefix, i) in seen:
        i += 1
    seen.add("%s%d" % (prefix, i))
    return "%s%d" % (prefix, i)


def normalize_spec(spec):
    """把宽松的规格归一到内部结构；绝不静默丢弃未知字段，仅补默认值。"""
    if not isinstance(spec, dict):
        spec = {}
    out = dict(spec)
    out.setdefault("case_id", spec.get("case_id") or "")
    out.setdefault("title", spec.get("title") or "")
    out.setdefault("entities", [])
    out.setdefault("relations", [])
    out.setdefault("legend", {})
    out.setdefault("data_mappings", [])
    out.setdefault("uncertainty", [])
    out.setdefault("change_log", [])
    layout = dict(spec.get("layout") or {})
    layout.setdefault("orientation", "top_to_bottom")
    layout.setdefault("compartments", DEFAULT_COMPARTMENTS)
    out["layout"] = layout

    ents = out["entities"]
    if not isinstance(ents, list):
        ents = []
        out["entities"] = ents
    seen_ids = set()
    for i, e in enumerate(ents):
        if not isinstance(e, dict):
            continue
        eid = e.get("id") or _uid("e", seen_ids)
        e["id"] = eid
        e.setdefault("label", eid)
        e.setdefault("kind", "protein")
        e.setdefault("name_style", KIND_STYLE.get(e.get("kind"), ("", "", "normal"))[2])
        e.setdefault("compartment", "")
        e.setdefault("transmembrane", False)
        e.setdefault("sources", [])
        e.setdefault("uncertain", False)

    rels = out["relations"]
    if not isinstance(rels, list):
        rels = []
        out["relations"] = rels
    for i, r in enumerate(rels):
        if not isinstance(r, dict):
            continue
        rid = r.get("id") or _uid("r", seen_ids)
        r["id"] = rid
        r.setdefault("type", "binding")
        r.setdefault("direction", "forward")
        r.setdefault("evidence", "inferred")
        r.setdefault("sources", [])
        r.setdefault("uncertain", False)
    return out


def _norm_sources(items):
    """归一 sources 为 [{locator, quote}]。"""
    if not isinstance(items, list):
        return []
    out = []
    for s in items:
        if isinstance(s, str):
            out.append({"locator": s, "quote": ""})
        elif isinstance(s, dict):
            out.append({
                "locator": s.get("locator") or s.get("path") or "",
                "quote": s.get("quote") or "",
            })
    return out


def validate_spec(spec):
    """校验/归一化机制规格。

    返回 (errors, warnings, normalized)。
    errors 阻断构建；warnings 可继续但需记录。"""
    errors, warnings = [], []
    norm = normalize_spec(spec)
    ents = norm["entities"]
    rels = norm["relations"]

    if not norm.get("case_id"):
        errors.append("case_id 缺失或为空")

    ids = [e.get("id") for e in ents if isinstance(e, dict)]
    if len(ids) != len(set(ids)):
        errors.append("实体 id 重复")
    seen_labels = {}
    for e in ents:
        kind = e.get("kind")
        if kind not in ALLOWED_KINDS:
            warnings.append("实体 %s 种类未知: %r（暂按 protein 渲染）" % (e.get("id"), kind))
            e["kind"] = "protein"
        if not e.get("label"):
            warnings.append("实体 %s 缺少 label，使用 id" % e.get("id"))
            e["label"] = e.get("id")
        e["sources"] = _norm_sources(e.get("sources"))
        if not e["sources"]:
            warnings.append("实体 %s 无来源溯源" % e.get("id"))
        lab = e.get("label")
        if lab in seen_labels and seen_labels[lab] != e.get("id"):
            warnings.append("同名标签 %r 同时用于 %s 与 %s，请核对是否为同名异义"
                            % (lab, seen_labels[lab], e.get("id")))
        seen_labels.setdefault(lab, e.get("id"))

    for r in rels:
        rtype = r.get("type")
        if rtype not in ALLOWED_REL_TYPES:
            errors.append("关系 %s 类型非法: %r" % (r.get("id"), rtype))
        if r.get("from") not in ids:
            errors.append("关系 %s 的 from=%r 未在实体中" % (r.get("id"), r.get("from")))
        if r.get("to") not in ids:
            errors.append("关系 %s 的 to=%r 未在实体中" % (r.get("id"), r.get("to")))
        r["sources"] = _norm_sources(r.get("sources"))
        if not r["sources"]:
            warnings.append("关系 %s 无来源溯源" % r.get("id"))
        if r.get("uncertain"):
            warnings.append("关系 %s 标记为不确定，请同步写入 uncertainty" % r.get("id"))

    if not ents:
        warnings.append("尚未抽取任何实体（骨架阶段可接受）")
    return errors, warnings, norm


# --------------------------------------------------------------------------- #
#  SVG 构建
# --------------------------------------------------------------------------- #

def _esc(text):
    return escape(str(text))


def _esc_attr(text):
    return escape(str(text)).replace('"', "&quot;").replace("'", "&#39;")


def _markers():
    """返回 <defs> 内各语义端点的 marker 定义。"""
    m = []
    m.append('<marker id="mk-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
             'markerWidth="9" markerHeight="9" orient="auto">'
             '<path d="M0,0 L10,5 L0,10 z" fill="#1A1A1A"/></marker>')
    m.append('<marker id="mk-tbar" viewBox="0 0 10 10" refX="9" refY="5" '
             'markerWidth="10" markerHeight="10" orient="auto">'
             '<line x1="0" y1="0" x2="0" y2="10" stroke="#1A1A1A" stroke-width="2.6"/></marker>')
    m.append('<marker id="mk-circle" viewBox="0 0 10 10" refX="6" refY="5" '
             'markerWidth="8" markerHeight="8" orient="auto">'
             '<circle cx="5" cy="5" r="4.4" fill="#2E75B6" stroke="#1F4E79" stroke-width="1"/></marker>')
    m.append('<marker id="mk-open" viewBox="0 0 10 10" refX="8" refY="5" '
             'markerWidth="9" markerHeight="9" orient="auto">'
             '<path d="M0,0 L10,5 L0,10 z" fill="#FFFFFF" stroke="#548235" stroke-width="1.5"/></marker>')
    m.append('<marker id="mk-diamond" viewBox="0 0 10 10" refX="8" refY="5" '
             'markerWidth="9" markerHeight="9" orient="auto">'
             '<path d="M5,0 L10,5 L5,10 L0,5 z" fill="#C00000"/></marker>')
    return "\n".join(m)


def _node_w_h(label):
    """按标签长度估算节点宽高（确定性）。"""
    w = int(30 + 7.6 * max(1, len(str(label))))
    w = max(92, min(250, w))
    return w, 36


def _band_plan(norm):
    """把实体按区室分组，返回 [(compartment, [entity,...]), ...] 与 unassigned 提示。"""
    entities = norm["entities"]
    layout = norm.get("layout") or {}
    comp_order = layout.get("compartments") or DEFAULT_COMPARTMENTS
    if not isinstance(comp_order, list):
        comp_order = DEFAULT_COMPARTMENTS
    assigned = {e.get("id") for e in entities}
    bands, used = [], set()
    for name in comp_order:
        members = [e for e in entities if e.get("compartment") == name]
        if members:
            bands.append((name, members))
            used.update(e.get("id") for e in members)
    leftover = [e for e in entities if e.get("id") not in used]
    if leftover:
        bands.append(("unassigned", leftover))
    return bands


def build_svg(norm, svg_path):
    """由归一化规格生成分层 SVG，写到 svg_path，返回 svg_path。"""
    errors, warnings, norm2 = validate_spec(norm)
    if errors:
        raise ValueError("规格阻断：\n  " + "\n  ".join(errors))
    norm = norm2
    entities = norm["entities"]
    if not entities:
        raise ValueError("规格无实体，无法构建 SVG（先补齐 entities）")

    orientation = (norm.get("layout") or {}).get("orientation", "top_to_bottom")
    horizontal = orientation == "left_to_right"

    # 区室带几何
    bands = _band_plan(norm)
    label_col = 168          # 区室标签列（cross 方向起点）
    margin_end = 56
    band_h = 96              # 每条区室带在 flow 方向的高度
    header = 26
    legend_h = 34 + 30 * max(1, _legend_row_count(norm))

    # 先算节点宽高
    for e in entities:
        e["_w"], e["_h"] = _node_w_h(e.get("label"))
    # 计算 cross 方向所需宽度
    max_cross_need = 0
    for _name, members in bands:
        total_w = sum(e["_w"] for e in members) + 26 * max(0, len(members) - 1)
        max_cross_need = max(max_cross_need, total_w)
    cross_size = max(960, label_col + margin_end + max_cross_need)
    flow_size = header + band_h * len(bands) + legend_h + 24

    # 为每个实体确定中心坐标（抽象空间：cross / flow）
    def node_center(e, band_idx, slot):
        cross = label_col + slot
        flow = header + band_idx * band_h + band_h / 2.0
        if horizontal:
            return flow, cross  # (x, y)
        return cross, flow      # (x, y)

    centers = {}
    for bi, (name, members) in enumerate(bands):
        n = len(members)
        avail = cross_size - label_col - margin_end
        for i, e in enumerate(members):
            slot = avail * (i + 1) / (n + 1)
            cx, cy = node_center(e, bi, slot)
            centers[e["id"]] = (cx, cy)

    # 关系端点边界缩短
    def boundary(cx, cy, w, h, dx, dy, extra=10.0):
        ax = abs(dx) if abs(dx) > 1e-9 else 1e-9
        ay = abs(dy) if abs(dy) > 1e-9 else 1e-9
        t = min((w / 2.0 + extra) / ax, (h / 2.0 + extra) / ay)
        return cx + dx * t, cy + dy * t

    # 组装 SVG 片段
    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    if horizontal:
        svg_w, svg_h = flow_size, cross_size
    else:
        svg_w, svg_h = cross_size, flow_size
    parts.append(
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" font-family="Arial, Helvetica, sans-serif">'
        % (svg_w, svg_h, svg_w, svg_h))
    parts.append("<defs>")
    parts.append(_markers())
    parts.append("</defs>")

    # 图层 1：背景
    parts.append('<g id="layer-background">')
    parts.append('<rect x="0" y="0" width="%d" height="%d" fill="#FFFFFF"/>'
                 % (svg_w, svg_h))
    parts.append("</g>")

    # 图层 2：区室带
    parts.append('<g id="layer-compartments" fill="none">')
    for bi, (name, _members) in enumerate(bands):
        if horizontal:
            x = header + bi * band_h
            rect = '<rect x="%d" y="%d" width="%d" height="%d" fill="#FBFBFB" ' \
                   'stroke="#BFBFBF" stroke-dasharray="3,3"/>' \
                   % (x, label_col - 30, band_h, cross_size - label_col - margin_end + 30)
            parts.append(rect)
            parts.append('<text x="%d" y="%d" fill="#595959" font-size="13" '
                         'transform="rotate(-90 %d %d)" text-anchor="middle">%s</text>'
                         % (x + band_h / 2, 18, x + band_h / 2, 18, _esc(name)))
        else:
            y = header + bi * band_h
            rect = '<rect x="%d" y="%d" width="%d" height="%d" fill="#FBFBFB" ' \
                   'stroke="#BFBFBF" stroke-dasharray="3,3"/>' \
                   % (label_col - 30, y, cross_size - label_col - margin_end + 30, band_h)
            parts.append(rect)
            parts.append('<text x="%d" y="%d" fill="#595959" font-size="13" '
                         'text-anchor="end">%s</text>'
                         % (label_col - 40, y + band_h / 2 + 4, _esc(name)))
    parts.append("</g>")

    # 图层 3：关系（画在节点之下，节点填充遮住线端）
    parts.append('<g id="layer-relations">')
    eindex = entities_by_id(norm)
    for r in norm["relations"]:
        src, tgt = r.get("from"), r.get("to")
        if src not in centers or tgt not in centers:
            continue
        sx, sy = centers[src]
        tx, ty = centers[tgt]
        dx = tx - sx
        dy = ty - sy
        length = (dx * dx + dy * dy) ** 0.5 or 1.0
        ux, uy = dx / length, dy / length
        w_src, h_src = eindex[src]["_w"], eindex[src]["_h"]
        w_tgt, h_tgt = eindex[tgt]["_w"], eindex[tgt]["_h"]
        ax, ay = boundary(sx, sy, w_src, h_src, ux, uy, extra=4.0)
        bx, by = boundary(tx, ty, w_tgt, h_tgt, -ux, -uy, extra=4.0)
        style = REL_STYLE.get(r.get("type"), REL_STYLE["binding"])
        color, dash = style[1], style[2]
        marker = {"arrow": "url(#mk-arrow)", "tbar": "url(#mk-tbar)",
                  "circle": "url(#mk-circle)", "open_arrow": "url(#mk-open)",
                  "diamond": "url(#mk-diamond)", "none": ""}[style[0]]
        # 反馈/回流（flow 方向反向）走弯曲路径避免与节点重叠
        reverse = (sy > ty + 1e-6) if not horizontal else (sx > tx + 1e-6)
        sources = _norm_sources(r.get("sources"))
        src_attr = "; ".join(s.get("locator") for s in sources if s.get("locator"))
        quote = (sources[0].get("quote") if sources else "") or ""
        common = (' data-id="%s" data-from="%s" data-to="%s" data-type="%s" '
                  'data-sources="%s" data-quote="%s"'
                  % (_esc_attr(r.get("id")), _esc_attr(src), _esc_attr(tgt),
                     _esc_attr(r.get("type")), _esc_attr(src_attr), _esc_attr(quote[:160])))
        if dash:
            dash_attr = ' stroke-dasharray="%s"' % dash
        else:
            dash_attr = ""
        if marker:
            marker_attr = ' marker-end="%s"' % marker
        else:
            marker_attr = ""
        if reverse:
            mx = (ax + bx) / 2.0
            my = (ay + by) / 2.0
            ox = -uy * 46.0
            oy = ux * 46.0
            parts.append('<path d="M %s,%s Q %s,%s %s,%s" fill="none" stroke="%s" '
                         'stroke-width="2"%s%s%s/>'
                         % (ax, ay, mx + ox, my + oy, bx, by, color, dash_attr,
                            marker_attr, common))
        else:
            parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" '
                         'stroke-width="2"%s%s%s/>'
                         % (ax, ay, bx, by, color, dash_attr, marker_attr, common))
        if r.get("label"):
            mx, my = (ax + bx) / 2.0, (ay + by) / 2.0
            parts.append('<text x="%s" y="%s" font-size="11" fill="#404040" '
                         'text-anchor="middle">%s</text>'
                         % (mx, my - 4, _esc(r.get("label"))))
    parts.append("</g>")

    # 图层 4：实体节点
    parts.append('<g id="layer-entities">')
    for e in norm["entities"]:
        if e["id"] not in centers:
            continue
        cx, cy = centers[e["id"]]
        w, h = e["_w"], e["_h"]
        shape, fill, stroke, style = KIND_STYLE.get(e.get("kind"), KIND_STYLE["protein"])
        sources = _norm_sources(e.get("sources"))
        src_attr = "; ".join(s.get("locator") for s in sources if s.get("locator"))
        quote = (sources[0].get("quote") if sources else "") or ""
        common = ('data-id="%s" data-kind="%s" data-compartment="%s" '
                  'data-sources="%s" data-quote="%s"'
                  % (_esc_attr(e.get("id")), _esc_attr(e.get("kind")),
                     _esc_attr(e.get("compartment")), _esc_attr(src_attr),
                     _esc_attr(quote[:160])))
        italic = (e.get("name_style") == "italic") or style == "italic"
        fs = ' font-style="italic"' if italic else ""
        if shape == "rect":
            parts.append('<rect x="%s" y="%s" width="%s" height="%s" rx="0" '
                         'fill="%s" stroke="%s" stroke-width="1.6" %s/>'
                         % (cx - w / 2, cy - h / 2, w, h, fill, stroke, common))
        elif shape == "roundrect":
            extra = ' stroke-width="2.4"' if e.get("kind") == "complex" else \
                    ' stroke-dasharray="7,4" stroke-width="1.6"' if e.get("kind") == "family" else \
                    ' stroke-width="1.6"'
            parts.append('<rect x="%s" y="%s" width="%s" height="%s" rx="9" '
                         'fill="%s" stroke="%s"%s %s/>'
                         % (cx - w / 2, cy - h / 2, w, h, fill, stroke, extra, common))
        elif shape == "ellipse":
            parts.append('<ellipse cx="%s" cy="%s" rx="%s" ry="%s" fill="%s" '
                         'stroke="%s" stroke-width="1.6" %s/>'
                         % (cx, cy, w / 2, h / 2, fill, stroke, common))
        elif shape == "circle":
            r = max(w, h) / 2.0
            parts.append('<circle cx="%s" cy="%s" r="%s" fill="%s" stroke="%s" '
                         'stroke-width="1.6" %s/>'
                         % (cx, cy, r, fill, stroke, common))
        elif shape == "diamond":
            pts = "%s,%s %s,%s %s,%s %s,%s" % (
                cx, cy - h / 2, cx + w / 2, cy, cx, cy + h / 2, cx - w / 2, cy)
            parts.append('<polygon points="%s" fill="%s" stroke="%s" '
                         'stroke-width="1.6" %s/>' % (pts, fill, stroke, common))
        parts.append('<text x="%s" y="%s" font-size="12.5"%s text-anchor="middle" '
                     'dominant-baseline="middle">%s</text>'
                     % (cx, cy, fs, _esc(e.get("label"))))
    parts.append("</g>")

    # 图层 5：图例（与关系/实体种类实际使用项自洽）
    parts.append('<g id="layer-legend">')
    legend_y = header + band_h * len(bands) + 12
    used_types = [t for t in ALLOWED_REL_TYPES
                  if any(r.get("type") == t for r in norm["relations"])]
    used_kinds = [k for k in ALLOWED_KINDS
                  if any(e.get("kind") == k for e in norm["entities"])]
    x = label_col - 30
    for t in used_types:
        style = REL_STYLE[t]
        color, dash = style[1], style[2]
        marker = {"arrow": "url(#mk-arrow)", "tbar": "url(#mk-tbar)",
                  "circle": "url(#mk-circle)", "open_arrow": "url(#mk-open)",
                  "diamond": "url(#mk-diamond)", "none": ""}[style[0]]
        parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" '
                     'stroke-width="2"%s%s/>'
                     % (x, legend_y, x + 34, legend_y, color,
                        (' stroke-dasharray="%s"' % dash) if dash else "",
                        (' marker-end="%s"' % marker) if marker else ""))
        parts.append('<text x="%s" y="%s" font-size="12" fill="#1A1A1A">%s</text>'
                     % (x + 42, legend_y + 4, _esc(REL_DEFAULT_LABEL.get(t, t))))
        legend_y += 22
    for k in used_kinds:
        shape, fill, stroke, style = KIND_STYLE[k]
        if shape == "ellipse":
            parts.append('<ellipse cx="%s" cy="%s" rx="13" ry="9" fill="%s" '
                         'stroke="%s" stroke-width="1.4"/>' % (x + 16, legend_y, fill, stroke))
        elif shape == "circle":
            parts.append('<circle cx="%s" cy="%s" r="9" fill="%s" stroke="%s" '
                         'stroke-width="1.4"/>' % (x + 16, legend_y, fill, stroke))
        else:
            parts.append('<rect x="%s" y="%s" width="26" height="18" rx="5" fill="%s" '
                         'stroke="%s" stroke-width="1.4"/>'
                         % (x + 3, legend_y - 9, fill, stroke))
        parts.append('<text x="%s" y="%s" font-size="12" fill="#1A1A1A">%s</text>'
                     % (x + 42, legend_y + 4, _esc(k)))
        legend_y += 22
    # 数据编码映射
    for dm in norm.get("data_mappings") or []:
        if isinstance(dm, dict) and dm.get("visual") and dm.get("semantic"):
            parts.append('<text x="%s" y="%s" font-size="11" fill="#595959">%s → %s</text>'
                         % (x, legend_y + 3, _esc(dm.get("visual")), _esc(dm.get("semantic"))))
            legend_y += 18
    parts.append("</g>")

    parts.append("</svg>")

    os.makedirs(os.path.dirname(os.path.abspath(svg_path)), exist_ok=True)
    with open(svg_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    return svg_path


def entities_by_id(norm):
    return {e.get("id"): e for e in norm.get("entities", []) if isinstance(e, dict)}


def _legend_row_count(norm):
    n = len({r.get("type") for r in norm.get("relations", [])})
    n += len({e.get("kind") for e in norm.get("entities", [])})
    n += len(norm.get("data_mappings") or [])
    return max(1, n)


# --------------------------------------------------------------------------- #
#  result.json 契约校验
# --------------------------------------------------------------------------- #

REQUIRED_KEYS = [
    "case_id", "status", "editable_source_files", "export_artifacts",
    "entities", "relations", "legend", "data_mappings", "sources",
    "uncertainty", "change_log", "pending_human", "limitations",
]


def check_result(result, output_dir=None):
    """校验 result.json 契约。返回 (errors, warnings)。"""
    errors, warnings = [], []
    if not isinstance(result, dict):
        errors.append("result.json 顶层必须是对象")
        return errors, warnings
    for k in REQUIRED_KEYS:
        if k not in result:
            errors.append("缺少必需字段: %s" % k)
    if result.get("case_id", "") in ("", None):
        errors.append("case_id 缺失或为空")
    status = result.get("status")
    if status not in ALLOWED_STATUS:
        errors.append("status 非法: %r（应为 %s）" % (status, "/".join(sorted(ALLOWED_STATUS))))
    if status in ("partial", "failed", "insufficient_evidence", "input_error") and not result.get("limitations"):
        warnings.append("status=%s 但 limitations 为空，需注明缺口" % status)
    if not result.get("change_log"):
        warnings.append("change_log 为空（首条应记录骨架落盘）")
    if status == "success" and not result.get("entities"):
        warnings.append("status=success 但 entities 为空")

    base = output_dir or ""
    for field in ("editable_source_files", "export_artifacts"):
        for item in result.get(field, []):
            if isinstance(item, dict) and item.get("path"):
                p = item["path"]
                if base and not os.path.isabs(p):
                    p = os.path.join(base, p)
                if not os.path.exists(p):
                    errors.append("%s 引用文件不存在: %s" % (field, item["path"]))
    return errors, warnings


def write_skeleton(out_path, case_id, title=None):
    """落盘一版 partial 结果骨架：字段齐全、case_id 已填、change_log 首条记录。"""
    result = {
        "case_id": case_id,
        "status": "partial",
        "title": title or "",
        "editable_source_files": [],
        "export_artifacts": [],
        "entities": [],
        "relations": [],
        "legend": {},
        "data_mappings": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [
            {"time": _now(), "step": "skeleton", "note": "骨架落盘（partial）",
             "detail": "已读取输入并建立输出契约；实体/关系待抽取"}
        ],
        "limitations": ["骨架阶段：实体、关系与导出件尚未生成"],
        "errors": [],
        "degradations": [],
        "runtime": {},
    }
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    return result
