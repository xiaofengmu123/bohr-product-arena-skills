#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — 机制图引擎（仅 Python 标准库）。

架构取向（与“validate_spec + build_svg 分离”的常见拆法不同）：
  单一中间表示（MIR，即 spec.json）驱动整条流水线——
      材料 --(Agent 抽取)--> spec.json(MIR)
            --validate--> --layout+render--> 分层 SVG
            --check--> result.json 契约

差异化的关键取舍：
  1. 单一事实源：归一化、校验、布局、配色、图例共用同一组常量表，
     图例 / 颜色 / 形状永远由同一张表生成，不可能与图面漂移。
  2. 两段式校验：render 之前先做结构性校验（阻断），render 之后再做
     契约校验（check_result），两道校验守护不同层次。
  3. 确定性：稳定排序、无随机数、无 dict 迭代顺序依赖，重复运行产生
     逐字节一致的 SVG（可复核、可 diff）。
  4. MIR 自带证据溯源：每个实体/边/数据映射内嵌 sources[].locator/quote，
     result.json 只是汇总，不另起一套证据结构。
"""
import datetime
import hashlib
import html
import json
import os
import re
from xml.etree import ElementTree as ET

SCHEMA_VERSION = "1.0.0"

# ---- 单一事实源：实体 / 关系 / 区室的常量表 ---------------------------------

ENTITY_KINDS = {
    "gene", "protein", "complex", "family", "cell_type",
    "compartment", "small_molecule", "receptor",
}
RELATION_TYPES = {
    "activation", "inhibition", "binding", "modification",
    "transport", "conversion",
}
RELATION_DIRECTIONS = {"forward", "reverse", "bidirectional", "feedback"}
EVIDENCE_LEVELS = {"direct", "inferred", "uncertain"}
MODIFIERS = {
    "phosphorylation", "dephosphorylation", "ubiquitination",
    "sumoylation", "acetylation", "methylation", "cleavage",
    "glycosylation", "recruitment", "other",
}

# 关系 -> 颜色 / 箭头样式 / 中文语义（图例与图面共用）
RELATION_STYLE = {
    "activation":   {"color": "#2e7d32", "desc": "激活 / 促进",       "marker": "m-act", "dash": None},
    "inhibition":   {"color": "#c62828", "desc": "抑制 / 阻断",       "marker": "m-inh", "dash": None},
    "binding":      {"color": "#546e7a", "desc": "结合 / 相互作用",   "marker": "m-bind", "dash": None},
    "modification": {"color": "#6a1b9a", "desc": "修饰（磷酸化/泛素化等）", "marker": "m-mod", "dash": None},
    "transport":    {"color": "#1565c0", "desc": "转运 / 易位",       "marker": "m-act", "dash": "6,4"},
    "conversion":   {"color": "#ef6c00", "desc": "转化 / 生成",       "marker": "m-conv", "dash": None},
}

# 实体种类 -> 形状 / 颜色 / 是否斜体
ENTITY_STYLE = {
    "gene":           {"shape": "rect",       "fill": "#dbeafe", "stroke": "#1d4ed8", "italic": True,  "desc": "基因（斜体）"},
    "protein":        {"shape": "roundrect",  "fill": "#d1fae5", "stroke": "#047857", "italic": False, "desc": "蛋白（正体）"},
    "receptor":       {"shape": "roundrect",  "fill": "#bfdbfe", "stroke": "#1e40af", "italic": False, "desc": "受体 / 跨膜蛋白"},
    "complex":        {"shape": "roundrect",  "fill": "#fef3c7", "stroke": "#b45309", "italic": False, "desc": "复合物（双边框）"},
    "family":         {"shape": "roundrect",  "fill": "#f1f5f9", "stroke": "#475569", "italic": False, "desc": "蛋白家族 / 同源组"},
    "cell_type":      {"shape": "ellipse",    "fill": "#fce7f3", "stroke": "#be185d", "italic": False, "desc": "细胞类型"},
    "small_molecule": {"shape": "circle",     "fill": "#ccfbf1", "stroke": "#0f766e", "italic": False, "desc": "小分子 / 配体"},
    "compartment":    {"shape": "band",       "fill": "#f8fafc", "stroke": "#94a3b8", "italic": False, "desc": "亚细胞区室 / 细胞器"},
}

# 区室默认自上而下的层级（细胞外 -> 膜 -> 胞质 -> 核），体现“受体—胞内—核内”
COMPARTMENT_ORDER = ["extracellular", "membrane", "cytosol", "nucleus"]


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- 容错读写 ----------------------------------------------------------------

def read_json_tolerant(path):
    """读 JSON，容忍 BOM / 坏编码 / 空文件，失败返回 None 而不抛异常。"""
    if not path or not os.path.exists(path):
        return None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                text = fh.read()
            if not text.strip():
                return None
            return json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return None


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


# ---- MIR 归一化与校验（render 之前，阻断结构错误） ----------------------------

def normalize_entity(e, idx):
    out = dict(e) if isinstance(e, dict) else {}
    out.setdefault("id", e.get("id") if isinstance(e, dict) else f"ent{idx + 1}")
    out.setdefault("label", out.get("name") or out["id"])
    out.setdefault("kind", "protein")
    out.setdefault("compartment", "cytosol")
    out.setdefault("species", None)
    out.setdefault("synonyms", [])
    out.setdefault("transmembrane", False)
    out.setdefault("sources", [])
    for s in out.get("sources", []):
        if isinstance(s, dict):
            s.setdefault("locator", None)
            s.setdefault("quote", None)
            s.setdefault("material", None)
    return out


def normalize_relation(r, idx):
    out = dict(r) if isinstance(r, dict) else {}
    out.setdefault("id", r.get("id") if isinstance(r, dict) else f"rel{idx + 1}")
    out.setdefault("type", "binding")
    out.setdefault("direction", "forward")
    out.setdefault("evidence", "inferred")
    out.setdefault("modifier", None)
    out.setdefault("sources", [])
    for s in out.get("sources", []):
        if isinstance(s, dict):
            s.setdefault("locator", None)
            s.setdefault("quote", None)
            s.setdefault("material", None)
    return out


def validate_mir(mir):
    """返回 (errors, warnings)。errors 非空时禁止渲染；warnings 降级记录。"""
    errors, warnings = [], []
    if not isinstance(mir, dict):
        return ["MIR 顶层必须是 JSON 对象"], []
    for key in ("entities", "relations"):
        if key not in mir:
            errors.append("缺少必需键: %s" % key)
    entities = mir.get("entities", [])
    relations = mir.get("relations", [])
    if not isinstance(entities, list):
        errors.append("entities 必须是数组")
    if not isinstance(relations, list):
        errors.append("relations 必须是数组")

    ids = {}
    for i, e in enumerate(entities or []):
        if not isinstance(e, dict):
            errors.append("entities[%d] 不是对象" % i)
            continue
        eid = e.get("id")
        if not eid:
            errors.append("entities[%d] 缺少 id" % i)
            continue
        if eid in ids:
            errors.append("实体 id 重复: %s" % eid)
        ids[eid] = e
        if e.get("kind") not in ENTITY_KINDS:
            errors.append("实体 %s: 非法 kind %r" % (eid, e.get("kind")))
        if e.get("compartment") is not None and not isinstance(e.get("compartment"), str):
            errors.append("实体 %s: compartment 必须是字符串" % eid)
        if not (e.get("label") or e.get("name")):
            warnings.append("实体 %s: 缺少 label/name" % eid)

    for j, r in enumerate(relations or []):
        if not isinstance(r, dict):
            errors.append("relations[%d] 不是对象" % j)
            continue
        rid = r.get("id") or "rel%d" % (j + 1)
        if r.get("type") not in RELATION_TYPES:
            errors.append("关系 %s: 非法 type %r" % (rid, r.get("type")))
        if r.get("source") not in ids:
            errors.append("关系 %s: source %r 不在实体中" % (rid, r.get("source")))
        if r.get("target") not in ids:
            errors.append("关系 %s: target %r 不在实体中" % (rid, r.get("target")))
        if r.get("source") == r.get("target"):
            warnings.append("关系 %s: 自环（仅反馈/自磷酸化可接受）" % rid)
        if "direction" in r and r.get("direction") not in RELATION_DIRECTIONS:
            warnings.append("关系 %s: direction 非法，将按 forward 处理" % rid)
        if "evidence" in r and r.get("evidence") not in EVIDENCE_LEVELS:
            warnings.append("关系 %s: evidence 非法，将按 inferred 处理" % rid)

    # 溯源完整性（不阻断，但必须写进 uncertainty / warnings）
    for e in (entities or []):
        if isinstance(e, dict) and not e.get("sources"):
            warnings.append("实体 %s: 无来源 locator，需在 uncertainty 说明" % e.get("id"))
    for r in (relations or []):
        if isinstance(r, dict) and not r.get("sources"):
            warnings.append("关系 %s: 无来源 locator，需在 uncertainty 说明" % r.get("id"))

    # 图例自洽预检
    used_types = {r.get("type") for r in (relations or []) if isinstance(r, dict)}
    legend = mir.get("legend") or []
    legend_keys = {lg.get("key") for lg in legend if isinstance(lg, dict)} if isinstance(legend, list) else set()
    missing_legend = used_types - legend_keys
    if missing_legend:
        warnings.append("图例缺少关系类型: %s（渲染时会自动补全）" % ", ".join(sorted(missing_legend)))
    return errors, warnings


# ---- 确定性布局 --------------------------------------------------------------

def _compartment_order(mir):
    order = list(COMPARTMENT_ORDER)
    extra = (mir.get("layout") or {}).get("compartment_order") or []
    for c in extra:
        if c not in order:
            order.append(c)
    return order


def layout(mir):
    """返回 (nodes, bands, legend_items, canvas)。nodes 带 (x,y,w,h) 与原始实体。"""
    entities = [normalize_entity(e, i) for i, e in enumerate(mir.get("entities") or [])]
    relations = [normalize_relation(r, i) for i, r in enumerate(mir.get("relations") or [])]
    order = _compartment_order(mir)

    bands = []
    seen = set()
    for comp in order:
        members = [e for e in entities if e.get("compartment") == comp]
        if members or comp in order[:4]:  # 前四个主区室即使空也画，保证层级骨架
            bands.append(comp)
            seen.add(comp)
    for e in entities:
        if e.get("compartment") not in seen and e.get("compartment") not in (None, ""):
            bands.append(e["compartment"])
            seen.add(e["compartment"])
    for e in entities:
        if e.get("compartment") in (None, "") :
            e["compartment"] = "cytosol"

    # 每种实体排序键：kind 优先级 + id 字典序，保证确定
    kind_rank = {k: i for i, k in enumerate(("receptor", "protein", "gene", "complex",
                                             "family", "cell_type", "small_molecule",
                                             "compartment"))}
    nodes = []
    band_meta = []
    y = 30
    BAND_GAP = 26
    COL = 150
    ROW = 74
    X0 = 70
    for comp in bands:
        members = [e for e in entities if e.get("compartment") == comp]
        members.sort(key=lambda e: (kind_rank.get(e.get("kind"), 99), str(e.get("id"))))
        per_row = max(1, int((900 - 2 * X0) / COL))
        rows = max(1, -(-len(members) // per_row)) if members else 1
        band_h = 52 + rows * ROW + 18
        band_meta.append({"id": comp, "y": y, "h": band_h, "rows": rows})
        for i, e in enumerate(members):
            r = i // per_row
            c = i % per_row
            x = X0 + c * COL
            cy = y + 52 + r * ROW + ROW // 2
            nodes.append({"entity": e, "x": x, "y": cy,
                          "cx": x + 60, "cy": cy})
        y += band_h + BAND_GAP

    legend_height = 46 + 22 * max(1, len(_legend_items(mir, relations, entities)))
    canvas = {"w": 960, "h": y + legend_height + 12}
    return nodes, band_meta, relations, entities, canvas


def _legend_items(mir, relations, entities):
    items = []
    if isinstance(mir.get("legend"), list):
        for lg in mir["legend"]:
            if isinstance(lg, dict) and lg.get("key"):
                items.append(lg)
    # 自动补全实际使用的关系类型与实体种类，保证图例与图面自洽
    used_types = sorted({r.get("type") for r in relations})
    used_kinds = sorted({e.get("kind") for e in entities})
    have = {it.get("key") for it in items}
    for t in used_types:
        if t not in have and t in RELATION_STYLE:
            items.append({"key": t, "shape": RELATION_STYLE[t]["marker"],
                          "description": RELATION_STYLE[t]["desc"], "applies_to": "relation"})
    for k in used_kinds:
        if k not in have and k in ENTITY_STYLE:
            items.append({"key": k, "shape": ENTITY_STYLE[k]["shape"],
                          "description": ENTITY_STYLE[k]["desc"], "applies_to": "entity"})
    return items


# ---- 分层 SVG 渲染 ------------------------------------------------------------

def _esc(txt):
    return html.escape(str(txt if txt is not None else ""), quote=True)


def _wrap(label, width=20):
    label = str(label or "")
    if len(label) <= width:
        return [label]
    words = label.split(" ")
    lines, cur = [], ""
    for w in words:
        cand = (cur + " " + w).strip() if cur else w
        if len(cand) <= width:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines[:2]


def _node_box(kind, label, transmembrane=False):
    style = ENTITY_STYLE.get(kind, ENTITY_STYLE["protein"])
    lines = _wrap(label)
    w = max(70, min(170, max(len(ch) for ch in lines) * 7 + 24))
    h = 34 + 14 * (len(lines) - 1)
    return style, lines, w, h


def _render_marker_defs():
    parts = []
    parts.append('<marker id="m-act" viewBox="0 0 10 10" refX="9" refY="5" '
                 'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                 '<path d="M0,0 L10,5 L0,10 z" fill="#2e7d32"/></marker>')
    parts.append('<marker id="m-inh" viewBox="0 0 10 10" refX="9" refY="5" '
                 'markerWidth="7" markerHeight="7" orient="auto">'
                 '<path d="M2,0 L2,10" stroke="#c62828" stroke-width="3.2"/></marker>')
    parts.append('<marker id="m-mod" viewBox="0 0 10 10" refX="5" refY="5" '
                 'markerWidth="7" markerHeight="7" orient="auto">'
                 '<circle cx="4" cy="5" r="3.6" fill="none" stroke="#6a1b9a" stroke-width="1.6"/></marker>')
    parts.append('<marker id="m-bind" viewBox="0 0 10 10" refX="5" refY="5" '
                 'markerWidth="6" markerHeight="6" orient="auto">'
                 '<circle cx="4" cy="5" r="3" fill="#546e7a"/></marker>')
    parts.append('<marker id="m-conv" viewBox="0 0 10 10" refX="9" refY="5" '
                 'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                 '<path d="M0,0 L10,5 L0,10 z" fill="#ef6c00"/></marker>')
    return "\n".join(parts)


def render_svg(mir):
    nodes, bands, relations, entities, canvas = layout(mir)
    # 预计算每个节点的真实中心（中心 X 取决于节点宽度，避免箭头落在盒子边缘）
    centers = {}
    for n in nodes:
        e = n["entity"]
        _, _, w, _ = _node_box(e.get("kind"), e.get("label"), bool(e.get("transmembrane")))
        centers[e["id"]] = (n["x"] + w / 2.0, n["cy"])

    layers = {
        "background": [],
        "compartments": [],
        "relations": [],
        "entities": [],
        "labels": [],
        "legend": [],
    }

    layers["background"].append(
        '<rect x="0" y="0" width="%d" height="%d" fill="#ffffff"/>' % (canvas["w"], canvas["h"]))

    # 区室带（含膜双线）
    for b in bands:
        lbl = b["id"]
        layers["compartments"].append(
            '<rect x="20" y="%d" width="920" height="%d" rx="10" fill="#f8fafc" '
            'stroke="#cbd5e1" stroke-width="1.2"/>' % (b["y"], b["h"]))
        layers["compartments"].append(
            '<text x="34" y="%d" font-family="sans-serif" font-size="12" '
            'fill="#64748b" font-weight="bold">%s</text>' % (b["y"] + 18, _esc(lbl)))
        if b["id"] == "membrane":
            my = b["y"] + b["h"] / 2
            layers["compartments"].append(
                '<line x1="20" y1="%d" x2="940" y2="%d" stroke="#93c5fd" '
                'stroke-width="3" stroke-dasharray="1,0"/>' % (my, my))

    # 关系（先画，避免压住节点）
    for idx, r in enumerate(relations):
        st = RELATION_STYLE.get(r.get("type"), RELATION_STYLE["binding"])
        x1, y1 = centers.get(r.get("source"), (None, None))
        x2, y2 = centers.get(r.get("target"), (None, None))
        if x1 is None or x2 is None:
            continue
        dy = (idx % 3) * 12 - 12
        path = ('M %d %d C %d %d, %d %d, %d %d'
                % (x1, y1, x1 + 60, y1 + dy, x2 - 60, y2 + dy, x2, y2))
        marker_end = st["marker"]
        marker_start = None
        dash = st.get("dash")
        extra = ""
        if r.get("type") == "binding":
            marker_end = "m-bind"
            marker_start = "m-bind"
        if r.get("type") == "conversion" or r.get("direction") == "bidirectional":
            marker_start = "m-conv"
        if r.get("direction") == "reverse":
            marker_start, marker_end = marker_end, marker_start
        if r.get("direction") == "feedback":
            dash = dash or "4,4"
        mstart = 'marker-start="url(#%s)"' % marker_start if marker_start else ""
        mend = 'marker-end="url(#%s)"' % marker_end
        dattr = 'stroke-dasharray="%s"' % dash if dash else ""
        layers["relations"].append(
            '<path %s %s d="%s" fill="none" stroke="%s" stroke-width="2.2" %s '
            'data-id="%s" data-type="%s" data-source="%s" data-evidence="%s"/>'
            % (mstart, mend, path, st["color"], dattr,
               _esc(r.get("id")), _esc(r.get("type")),
               _esc(_first_locator(r)), _esc(r.get("evidence") or "")))
        if r.get("type") == "modification" and r.get("modifier"):
            mx, my2 = (x1 + x2) / 2, (y1 + y2) / 2 + dy - 8
            layers["labels"].append(
                '<text x="%d" y="%d" font-family="sans-serif" font-size="10" '
                'fill="#6a1b9a" text-anchor="middle">%s</text>'
                % (int(mx), int(my2), _esc(r.get("modifier"))))

    # 实体节点 + 标签
    for n in nodes:
        e = n["entity"]
        style, lines, w, h = _node_box(e.get("kind"), e.get("label"),
                                       bool(e.get("transmembrane")))
        x = n["x"]
        y = n["y"] - h // 2
        cx = n["x"] + w // 2
        kind = e.get("kind")
        eid = _esc(e.get("id"))
        src = _esc(_first_locator(e))
        italic = 'font-style="italic"' if style["italic"] else ""
        double = 'stroke-width="2.6"' if kind == "complex" else 'stroke-width="1.6"'
        dash = 'stroke-dasharray="5,3"' if kind == "family" else ""
        fill = style["fill"]
        stroke = style["stroke"]
        if kind == "ellipse" or kind == "cell_type":
            layers["entities"].append(
                '<ellipse cx="%d" cy="%d" rx="%d" ry="%d" fill="%s" stroke="%s" '
                'stroke-width="1.6" data-id="%s" data-kind="%s" data-source="%s"/>'
                % (cx, n["cy"], w // 2, max(24, h // 2), fill, stroke, eid, _esc(kind), src))
        elif kind == "circle" or kind == "small_molecule":
            layers["entities"].append(
                '<circle cx="%d" cy="%d" r="%d" fill="%s" stroke="%s" stroke-width="1.6" '
                'data-id="%s" data-kind="%s" data-source="%s"/>'
                % (cx, n["cy"], max(22, w // 2), fill, stroke, eid, _esc(kind), src))
        elif kind == "compartment":
            layers["entities"].append(
                '<rect x="%d" y="%d" width="%d" height="%d" rx="8" fill="%s" stroke="%s" '
                '%s %s data-id="%s" data-kind="%s" data-source="%s"/>'
                % (x, y, w, h, fill, stroke, double, dash, eid, _esc(kind), src))
        else:
            layers["entities"].append(
                '<rect x="%d" y="%d" width="%d" height="%d" rx="10" fill="%s" stroke="%s" '
                '%s %s data-id="%s" data-kind="%s" data-source="%s"/>'
                % (x, y, w, h, fill, stroke, double, dash, eid, _esc(kind), src))
        if e.get("transmembrane"):
            layers["entities"].append(
                '<path d="M %d %d C %d %d, %d %d, %d %d" fill="none" stroke="#1e40af" '
                'stroke-width="1.4"/>' % (x + 4, y + h, x + w // 2, y + h // 2,
                                          x + w // 2, y + h // 2, x + w - 4, y))
        ty = y + h // 2 + 4
        for i, ln in enumerate(lines):
            layers["labels"].append(
                '<text x="%d" y="%d" font-family="sans-serif" font-size="11" '
                'text-anchor="middle" %s>%s</text>'
                % (cx, ty + i * 14, italic, _esc(ln)))

    # 图例
    legend_items = _legend_items(mir, relations, entities)
    ly = canvas["h"] - 22 * len(legend_items) - 8
    layers["legend"].append(
        '<text x="30" y="%d" font-family="sans-serif" font-size="13" font-weight="bold" '
        'fill="#0f172a">Legend</text>' % (ly - 8))
    for i, it in enumerate(legend_items):
        row_y = ly + i * 22
        color = RELATION_STYLE.get(it.get("key"), {}).get("color", "#475569")
        shape = it.get("shape") or "line"
        if shape in ("m-act", "m-conv"):
            glyph = ('<line x1="34" y1="%d" x2="56" y2="%d" stroke="%s" stroke-width="2.2" '
                     'marker-end="url(#%s)"/>' % (row_y, row_y, color, shape))
        elif shape == "m-inh":
            glyph = ('<line x1="34" y1="%d" x2="56" y2="%d" stroke="%s" stroke-width="2.2" '
                     'marker-end="url(#m-inh)"/>' % (row_y, row_y, color))
        elif shape == "m-bind":
            glyph = ('<circle cx="45" cy="%d" r="3.4" fill="%s"/>' % (row_y, color))
        elif shape == "m-mod":
            glyph = ('<circle cx="45" cy="%d" r="3.4" fill="none" stroke="%s" stroke-width="1.6"/>'
                     % (row_y, color))
        else:
            glyph = ('<rect x="32" y="%d" width="26" height="14" rx="4" fill="%s" '
                     'stroke="%s" stroke-width="1.4"/>' % (row_y - 7, color, color))
        layers["legend"].append(glyph)
        layers["legend"].append(
            '<text x="66" y="%d" font-family="sans-serif" font-size="11" fill="#334155">%s</text>'
            % (row_y + 4, _esc(it.get("description") or it.get("key"))))

    svg = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
           'viewBox="0 0 %d %d" font-family="sans-serif">' % (canvas["w"], canvas["h"],
                                                              canvas["w"], canvas["h"]),
           "<defs>", _render_marker_defs(), "</defs>"]
    for layer_name in ("background", "compartments", "relations", "entities", "labels", "legend"):
        svg.append('<g id="layer-%s" inkscape:label="%s" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape">'
                   % (layer_name, layer_name))
        svg.extend(layers[layer_name])
        svg.append("</g>")
    svg.append("</svg>")
    return "\n".join(svg)


def _first_locator(obj):
    for s in (obj.get("sources") or []):
        if isinstance(s, dict) and s.get("locator"):
            return s["locator"]
    return ""


def svg_is_valid(svg_text):
    try:
        root = ET.fromstring(svg_text)
        return root.tag.endswith("svg")
    except ET.ParseError:
        return False


def svg_layer_ids(svg_text):
    ids = []
    try:
        root = ET.fromstring(svg_text)
        ns = {"i": "http://www.inkscape.org/namespaces/inkscape"}
        for g in root.iter("{http://www.w3.org/2000/svg}g"):
            ids.append(g.get("id") or g.get("{%s}label" % ns["i"]))
    except ET.ParseError:
        pass
    return [i for i in ids if i]


def svg_sha256(svg_text):
    return hashlib.sha256(svg_text.encode("utf-8")).hexdigest()


# ---- result.json 契约 ----------------------------------------------------------

RESULT_FIELDS = [
    "schema_version", "case_id", "title", "status",
    "editable_source_files", "export_artifacts",
    "entities", "relations", "legend", "data_mappings",
    "sources", "uncertainty", "pending_human",
    "change_log", "limitations", "errors", "degradations", "runtime", "notes",
]

VALID_STATUS = {"success", "partial", "failed"}


def empty_result(case_id=None, title=None):
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id or "unknown",
        "title": title,
        "status": "partial",
        "editable_source_files": [],
        "export_artifacts": [],
        "entities": [],
        "relations": [],
        "legend": [],
        "data_mappings": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "runtime": {"start_iso": now_iso(), "end_iso": None, "seconds": None, "requests_used": None},
        "notes": "",
    }


def append_change(result, action, reason=""):
    result.setdefault("change_log", []).append({
        "step": len(result.get("change_log", [])) + 1,
        "time": now_iso(),
        "action": action,
        "reason": reason,
    })


def mirror_from_mir(mir):
    """把 MIR 直接镜像进 result.json（entities/relations/legend/data_mappings/sources）。"""
    entities = [normalize_entity(e, i) for i, e in enumerate(mir.get("entities") or [])]
    relations = [normalize_relation(r, i) for i, r in enumerate(mir.get("relations") or [])]
    data_mappings = mir.get("data_mappings") or []
    if isinstance(mir.get("legend"), list):
        legend = mir["legend"]
    else:
        legend = []
    sources = []
    for e in entities:
        for s in e.get("sources") or []:
            if isinstance(s, dict):
                sources.append({"element_id": e["id"], "element_type": "entity",
                                "locator": s.get("locator"), "quote": s.get("quote"),
                                "material": s.get("material")})
    for r in relations:
        for s in r.get("sources") or []:
            if isinstance(s, dict):
                sources.append({"element_id": r["id"], "element_type": "relation",
                                "locator": s.get("locator"), "quote": s.get("quote"),
                                "material": s.get("material")})
    return entities, relations, legend, data_mappings, sources


def check_result(result, output_dir):
    """契约自检：返回 (errors, warnings)。exit 0=通过，1=错误，2=仅警告。"""
    errors, warnings = [], []
    if not isinstance(result, dict):
        return ["result.json 必须是 JSON 对象"], []
    for key in ("case_id", "status"):
        if key not in result or result[key] in (None, ""):
            errors.append("缺少必需字段: %s" % key)
    if result.get("status") not in VALID_STATUS:
        errors.append("status 非法: %r（应为 success/partial/failed）" % result.get("status"))
    if result.get("status") in ("partial", "failed") and not result.get("limitations"):
        warnings.append("status=%s 但 limitations 为空，应说明缺口" % result.get("status"))
    if not result.get("change_log"):
        warnings.append("change_log 为空，首条应为骨架落盘记录")
    for key in ("editable_source_files", "export_artifacts", "entities", "relations",
                "legend", "data_mappings", "sources", "uncertainty", "pending_human",
                "change_log", "limitations", "errors", "degradations"):
        if key not in result:
            errors.append("缺少契约字段: %s" % key)
        elif not isinstance(result[key], list):
            errors.append("字段 %s 必须是数组" % key)

    # 主产物文件存在性与合法性
    for i, f in enumerate(result.get("editable_source_files") or []):
        p = f.get("path") if isinstance(f, dict) else None
        if not p:
            errors.append("editable_source_files[%d] 缺少 path" % i)
            continue
        full = p if os.path.isabs(p) else os.path.join(output_dir, os.path.basename(p))
        if not os.path.exists(full):
            errors.append("源文件不存在: %s" % p)
        elif p.lower().endswith(".svg"):
            try:
                txt = open(full, "r", encoding="utf-8").read()
            except UnicodeDecodeError:
                errors.append("SVG 非 UTF-8: %s" % p)
            else:
                if not svg_is_valid(txt):
                    errors.append("SVG 无法解析: %s" % p)
                else:
                    layers = svg_layer_ids(txt)
                    if not any("layer-" in (l or "") for l in layers):
                        warnings.append("SVG 缺少命名图层: %s" % p)
    for i, f in enumerate(result.get("export_artifacts") or []):
        p = f.get("path") if isinstance(f, dict) else None
        if not p:
            errors.append("export_artifacts[%d] 缺少 path" % i)
            continue
        full = p if os.path.isabs(p) else os.path.join(output_dir, os.path.basename(p))
        if not os.path.exists(full):
            errors.append("导出件不存在: %s" % p)
        if p.lower().endswith(".pdf"):
            try:
                head = open(full, "rb").read(5)
            except OSError:
                errors.append("PDF 无法读取: %s" % p)
            else:
                if not head.startswith(b"%PDF-"):
                    errors.append("PDF 魔数错误: %s" % p)
    return errors, warnings
