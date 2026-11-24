#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""figure.py — tracefigure-skill 核心实现。

子命令：
  build     IR(JSON) -> 分层可编辑 SVG（确定性布图，纯标准库）
  validate  IR + SVG 自检，输出 checks.json

确定性保证：无随机数、固定调色板、按出现顺序稳定遍历；同一 IR 生成逐字节一致 SVG。
可被 main.py / selftest.py 以模块方式复用，也可作为脚本独立调用。
"""
import argparse
import html
import json
import os
import sys
import xml.etree.ElementTree as ET

FONT = "Arial, 'Noto Sans', 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif"

PALETTE = {
    "entity": "#dbe7f5",
    "entity_stroke": "#2b5f8a",
    "step": "#e2efda",
    "step_stroke": "#4d7c3f",
    "stage": "#f4e8cf",
    "stage_stroke": "#8a6d2b",
    "flow": "#333333",
    "backflow": "#a33a3a",
    "waste": "#7a7a7a",
    "data": "#5f6fd0",
    "annotation": "#666666",
    "legend_bg": "#ffffff",
}

BACK_KINDS = {"return", "reflux", "loop", "backflow", "recycle", "waste", "bypass"}


# --------------------------------------------------------------------------- helpers
def esc(s):
    return html.escape(str(s), quote=True)


def _num(v, default):
    try:
        f = float(v)
        return f if f == f else default
    except (TypeError, ValueError):
        return default


def _a(**kw):
    """把 kwargs 序列化为 XML 属性串（下划线转连字符，值做转义）。"""
    parts = []
    for k, v in kw.items():
        if v is None:
            continue
        parts.append('%s="%s"' % (k.replace("_", "-"), esc(v)))
    return " ".join(parts)


def _trunc(s, n):
    s = str(s)
    return s if len(s) <= n else s[:n] + "…"


def load_ir(path):
    if not path or not os.path.exists(path):
        return None, "missing: %s" % path
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f), None
    except Exception as e:  # noqa: BLE001
        return None, "bad json %s: %s" % (path, e)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return path


# --------------------------------------------------------------------------- nodes
def _collect(ir):
    """归一化 entity/step/stage 为统一节点列表。"""
    nodes = []
    for e in ir.get("entities") or []:
        if isinstance(e, dict):
            nodes.append(dict(e, kind="entity", id=(e.get("id") or ("n%d" % (len(nodes) + 1)))))
    for s in ir.get("steps") or []:
        if isinstance(s, dict):
            nodes.append(dict(s, kind="step", id=(s.get("id") or ("s%d" % (len(nodes) + 1)))))
    for s in ir.get("stages") or []:
        if isinstance(s, dict):
            nodes.append(dict(s, kind="stage", id=(s.get("id") or ("g%d" % (len(nodes) + 1)))))
    return nodes


# --------------------------------------------------------------------------- layout
def _layout(ir, nodes):
    W = _num((ir.get("canvas") or {}).get("width"), 960)
    H = _num((ir.get("canvas") or {}).get("height"), 640)
    kind = (ir.get("figure_kind") or "apparatus").lower()
    M = 44
    all_coords = bool(nodes) and all(
        all(isinstance(n.get(k), (int, float)) for k in ("x", "y", "w", "h")) for n in nodes
    )
    pos = {}
    if all_coords:
        for n in nodes:
            pos[n["id"]] = (float(n["x"]), float(n["y"]), float(n["w"]), float(n["h"]))
        return W, H, pos, False

    entities = [n for n in nodes if n["kind"] == "entity"]
    steps = [n for n in nodes if n["kind"] == "step"]
    stages = [n for n in nodes if n["kind"] == "stage"]

    if kind in ("flow", "stream"):
        ns = max(len(steps), 1)
        rh = max((H - 2 * M) / ns, 44)
        for i, n in enumerate(steps):
            pos[n["id"]] = (M, M + i * rh + 6, 280, rh - 12)
        for i, n in enumerate(entities):
            pos[n["id"]] = (M + 330, M + i * 60 + 6, 250, 46)
        for i, n in enumerate(stages):
            pos[n["id"]] = (M + 620, M + i * 60 + 6, 250, 46)
    elif kind == "route":
        ns = max(len(stages), 1)
        cw = (W - 2 * M) / ns
        for i, n in enumerate(stages):
            pos[n["id"]] = (M + i * cw + 6, M + 6, cw - 12, H - 2 * M - 96)
        for i, n in enumerate(entities):
            pos[n["id"]] = (M + (i % 6) * 150 + 6, H - 84, 140, 42)
        for i, n in enumerate(steps):
            pos[n["id"]] = (M + (i % 6) * 150 + 6, H - 84, 140, 42)
    else:
        n = max(len(entities), 1)
        cols = 3
        cw = (W - 2 * M) / cols
        rows = max((n + cols - 1) // cols, 1)
        ch = min(96, (H - 2 * M - 150) / rows)
        for i, e in enumerate(entities):
            r, c = divmod(i, cols)
            pos[e["id"]] = (M + c * cw + 6, M + r * ch + 6, cw - 12, ch - 12)
        for i, s in enumerate(steps):
            pos[s["id"]] = (M + (i % 6) * 150 + 6, H - 120, 140, 46)
        for i, s in enumerate(stages):
            pos[s["id"]] = (M + (i % 6) * 150 + 6, H - 120, 140, 46)
    return W, H, pos, True


def _rel_style(rel):
    kind = (rel.get("kind") or "").lower()
    direction = (rel.get("direction") or "forward").lower()
    if kind in BACK_KINDS:
        return "dashed", PALETTE["backflow"], "arrBack"
    if direction == "backward":
        return "dashed", PALETTE["backflow"], "arrBack"
    if kind == "data":
        return "dashed", PALETTE["data"], "arrData"
    if kind == "waste":
        return "dashed", PALETTE["waste"], "arrWaste"
    return "solid", PALETTE["flow"], "arr"


def _anchor(pf, pt):
    fx, fy, fw, fh = pf
    tx, ty, tw, th = pt
    fcx, fcy = fx + fw / 2, fy + fh / 2
    tcx, tcy = tx + tw / 2, ty + th / 2
    dx, dy = tcx - fcx, tcy - fcy
    if abs(dx) >= abs(dy):
        sx = fx + fw if dx >= 0 else fx
        sy = fcy
        ex = tx if dx >= 0 else tx + tw
        ey = tcy
    else:
        sx = fcx
        sy = fy + fh if dy >= 0 else fy
        ex = tcx
        ey = ty if dy >= 0 else ty + th
    return (sx, sy), (ex, ey)


def _label_text(x, y, label, size, color, anchor="middle", weight=None):
    extra = ' font-weight="%s"' % weight if weight else ""
    return ('<text x="%s" y="%s" font-size="%s" fill="%s" text-anchor="%s" '
            'dominant-baseline="middle" font-family="%s"%s>%s</text>'
            % (x, y, size, color, anchor, FONT, extra, esc(label)))


def _node_svg(n, posn, idx):
    x, y, w, h = posn
    sid = esc(n["id"])
    sr = esc(str(n.get("source_ref") or ""))
    quote = str(n.get("quote") or "")
    kind = n["kind"]
    label = str(n.get("label") or n["id"])
    out = []
    if kind == "entity":
        out.append('<g id="entity-%s" %s>' % (sid, _a(data_source_ref=sr, data_kind="entity", data_quote=quote)))
        out.append('<rect x="%s" y="%s" width="%s" height="%s" rx="6" fill="%s" stroke="%s" stroke-width="1.5"/>'
                   % (x, y, w, h, PALETTE["entity"], PALETTE["entity_stroke"]))
        out.append(_label_text(x + w / 2, y + h / 2, label, 13, "#111111"))
        typ = str(n.get("type") or "")
        if typ and typ.lower() != label.lower():
            out.append('<text x="%s" y="%s" font-size="9" fill="#555555" font-family="%s">%s</text>'
                       % (x + 6, y + 13, FONT, esc(typ)))
        cnt = n.get("count")
        if cnt not in (None, "", 1, "1"):
            out.append('<text x="%s" y="%s" font-size="11" font-weight="bold" fill="%s" text-anchor="end" font-family="%s">×%s</text>'
                       % (x + w - 6, y + 15, PALETTE["entity_stroke"], FONT, esc(cnt)))
        out.append('</g>')
    elif kind == "step":
        out.append('<g id="step-%s" %s>' % (sid, _a(data_source_ref=sr, data_kind="step", data_quote=quote)))
        cy = y + h / 2
        out.append('<circle cx="%s" cy="%s" r="13" fill="%s" stroke="%s" stroke-width="1.5"/>'
                   % (x + 20, cy, PALETTE["step"], PALETTE["step_stroke"]))
        order = n.get("order") if n.get("order") is not None else (n.get("index") if n.get("index") is not None else idx)
        out.append('<text x="%s" y="%s" font-size="11" font-weight="bold" fill="#111111" text-anchor="middle" dominant-baseline="middle" font-family="%s">%s</text>'
                   % (x + 20, cy, FONT, esc(order)))
        out.append(_label_text(x + 42, cy, label, 12, "#111111", anchor="start"))
        out.append('</g>')
    else:
        out.append('<g id="stage-%s" %s>' % (sid, _a(data_source_ref=sr, data_kind="stage", data_quote=quote)))
        out.append('<rect x="%s" y="%s" width="%s" height="%s" rx="8" fill="%s" stroke="%s" stroke-width="1.5"/>'
                   % (x, y, w, h, PALETTE["stage"], PALETTE["stage_stroke"]))
        out.append(_label_text(x + w / 2, y + 22, label, 13, "#111111", weight="bold"))
        yy = y + 42
        for k, pre in (("goal", "目标"), ("method", "方法"), ("output", "产出"), ("input", "输入")):
            v = n.get(k)
            if v:
                out.append('<text x="%s" y="%s" font-size="10" fill="#444444" font-family="%s">%s</text>'
                           % (x + 8, yy, FONT, esc("%s：%s" % (pre, _trunc(v, 24)))))
                yy += 15
        out.append('</g>')
    return out


def _legend_svg(ir, W, H):
    entries = ir.get("legend") or ir.get("legend_annotations") or []
    if not entries:
        entries = [
            {"key": "entity", "label": "实体/部件", "color": PALETTE["entity"], "line_style": "box"},
            {"key": "step", "label": "步骤", "color": PALETTE["step"], "line_style": "box"},
            {"key": "stage", "label": "阶段", "color": PALETTE["stage"], "line_style": "box"},
            {"key": "flow", "label": "流向", "color": PALETTE["flow"], "line_style": "solid"},
            {"key": "backflow", "label": "回流/旁路", "color": PALETTE["backflow"], "line_style": "dashed"},
        ]
    out = ['<g id="layer-legend">']
    x, y = 44.0, H - 34.0
    for e in entries:
        if not isinstance(e, dict):
            continue
        color = e.get("color") or PALETTE.get(e.get("key")) or PALETTE["flow"]
        style = (e.get("line_style") or e.get("lineStyle") or "solid").lower()
        label = str(e.get("label") or e.get("key") or "")
        sr = esc(str(e.get("source_ref") or ""))
        if x > W - 170:
            x = 44.0
            y -= 26.0
        if style == "box":
            out.append('<rect x="%s" y="%s" width="14" height="10" fill="%s" stroke="#888888" stroke-width="1" %s/>'
                       % (x, y - 5, color, _a(data_source_ref=sr)))
        else:
            dash = ' stroke-dasharray="4,3"' if style == "dashed" else ""
            out.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="2"%s %s/>'
                       % (x, y, x + 16, y, color, dash, _a(data_source_ref=sr)))
        out.append('<text x="%s" y="%s" font-size="10" fill="#333333" dominant-baseline="middle" font-family="%s">%s</text>'
                   % (x + 20, y, FONT, esc(label)))
        x += 24 + 8 * len(label) + 26
    out.append('</g>')
    return out


def _data_svg(ir, W, H):
    items = ir.get("data_encoding") or []
    if not items:
        return []
    out = ['<g id="layer-data">']
    y = 54.0
    for d in items:
        if not isinstance(d, dict):
            continue
        txt = "%s = %s → %s" % (d.get("key") or "?", d.get("value") or "?", d.get("target") or "?")
        out.append('<text x="%s" y="%s" font-size="10" fill="%s" font-family="%s" %s>%s</text>'
                   % (W - 320, y, PALETTE["data"], FONT, _a(data_source_ref=str(d.get("source_ref") or "")), esc(txt)))
        y += 15
    out.append('</g>')
    return out


def _anno_svg(ir, W, H):
    items = ir.get("annotations") or []
    if not items:
        return []
    out = ['<g id="layer-annotations">']
    y = 54.0
    for a in items:
        if not isinstance(a, dict):
            continue
        out.append('<text x="44" y="%s" font-size="11" fill="%s" font-family="%s" %s>%s</text>'
                   % (y, PALETTE["annotation"], FONT, _a(data_source_ref=str(a.get("source_ref") or "")), esc(str(a.get("text") or ""))))
        y += 16
    out.append('</g>')
    return out


# --------------------------------------------------------------------------- build
def build_svg(ir, out_path=None):
    ir = ir if isinstance(ir, dict) else {}
    nodes = _collect(ir)
    W, H, pos, auto = _layout(ir, nodes)
    kind = str(ir.get("figure_kind") or "apparatus")
    title = str(ir.get("title") or "")
    L = []
    A = L.append
    A('<?xml version="1.0" encoding="UTF-8"?>')
    A('<!-- tracefigure-skill editable SVG; layout=%s; generated deterministically -->' % ("auto" if auto else "explicit"))
    A('<svg xmlns="http://www.w3.org/2000/svg" width="%s" height="%s" viewBox="0 0 %s %s" data-figure-kind="%s" %s>'
      % (W, H, W, H, esc(kind), _a(data_source_ref=str(ir.get("source_ref") or ""))))
    A('<metadata>kind=%s; auto-layout=%s; title=%s</metadata>' % (esc(kind), "true" if auto else "false", esc(title)))
    if title:
        A('<text x="%s" y="30" font-size="20" font-weight="bold" fill="#111111" text-anchor="middle" font-family="%s">%s</text>'
          % (W / 2, FONT, esc(title)))
    A('<defs>')
    A('  <marker id="arr" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L8,3 L0,6 z" fill="%s"/></marker>' % PALETTE["flow"])
    A('  <marker id="arrBack" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L8,3 L0,6 z" fill="%s"/></marker>' % PALETTE["backflow"])
    A('  <marker id="arrData" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L8,3 L0,6 z" fill="%s"/></marker>' % PALETTE["data"])
    A('  <marker id="arrWaste" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L8,3 L0,6 z" fill="%s"/></marker>' % PALETTE["waste"])
    A('</defs>')
    A('<g id="layer-relations">')
    for i, r in enumerate(ir.get("relations") or []):
        if not isinstance(r, dict):
            continue
        frm = str(r.get("from"))
        to = str(r.get("to"))
        if frm not in pos or to not in pos:
            continue
        style, color, marker = _rel_style(r)
        direction = (r.get("direction") or "forward").lower()
        (x1, y1), (x2, y2) = _anchor(pos[frm], pos[to])
        if direction == "backward":
            x1, y1, x2, y2 = x2, y2, x1, y1
        dash = ' stroke-dasharray="5,4"' if style == "dashed" else ""
        me = ' marker-end="url(#%s)"' % marker
        ms = ' marker-start="url(#%s)"' % marker if direction == "bidirectional" else ""
        rid = r.get("id") or ("rel%d" % (i + 1))
        A('<line id="%s" x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="2"%s%s%s %s/>'
          % (esc(rid), x1, y1, x2, y2, color, dash, me, ms,
             _a(data_source_ref=str(r.get("source_ref") or ""), data_from=frm, data_to=to, data_direction=direction)))
    A('</g>')
    for gid, kindname in (("layer-entities", "entity"), ("layer-steps", "step"), ("layer-stages", "stage")):
        group = [n for n in nodes if n["kind"] == kindname]
        if not group:
            continue
        A('<g id="%s">' % gid)
        for idx, n in enumerate(group, 1):
            for line in _node_svg(n, pos[n["id"]], idx):
                A(line)
        A('</g>')
    for line in _data_svg(ir, W, H):
        A(line)
    for line in _anno_svg(ir, W, H):
        A(line)
    for line in _legend_svg(ir, W, H):
        A(line)
    A('</svg>')
    text = "\n".join(L) + "\n"
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
    return text


# --------------------------------------------------------------------------- validate
def validate_ir(ir):
    issues = []

    def add(level, code, msg, refs=None):
        issues.append({"level": level, "code": code, "message": msg, "refs": refs or []})

    if not isinstance(ir, dict):
        add("critical", "IR_NOT_DICT", "ir 不是 JSON 对象")
        return issues
    if not ir.get("case_id"):
        add("warning", "CASE_ID_MISSING", "IR 缺 case_id")
    ids = set()
    for n in _collect(ir):
        i = n["id"]
        if i in ids:
            add("critical", "DUP_ID", "重复节点 id: %s" % i, [i])
        ids.add(i)
        if not str(n.get("label") or "").strip():
            add("warning", "NODE_NO_LABEL", "节点缺 label: %s" % i, [i])
    for r in ir.get("relations") or []:
        if not isinstance(r, dict):
            continue
        frm = str(r.get("from"))
        to = str(r.get("to"))
        if frm and frm not in ids:
            add("critical", "DANGLING_FROM", "relation from 不存在: %s" % frm, [frm])
        if to and to not in ids:
            add("critical", "DANGLING_TO", "relation to 不存在: %s" % to, [to])
        d = (r.get("direction") or "forward").lower()
        if d not in ("forward", "backward", "bidirectional"):
            add("warning", "BAD_DIRECTION", "未知方向 %s" % d, [r.get("id") or (frm + "->" + to)])
    return issues


def validate_svg(ir, svg_path):
    issues = validate_ir(ir if isinstance(ir, dict) else {})
    if not svg_path or not os.path.exists(svg_path):
        issues.append({"level": "critical", "code": "SVG_MISSING", "message": "SVG 文件不存在", "refs": []})
        return issues
    try:
        tree = ET.parse(svg_path)
        root = tree.getroot()
    except Exception as e:  # noqa: BLE001
        issues.append({"level": "critical", "code": "SVG_PARSE", "message": "SVG 解析失败: %s" % e, "refs": []})
        return issues

    def is_tag(el, name):
        return el.tag == name or el.tag.endswith("}" + name)

    layer_ids = set()
    for g in root.iter():
        if is_tag(g, "g") and g.get("id"):
            layer_ids.add(g.get("id"))
    for want in ("layer-relations", "layer-entities", "layer-legend"):
        if want not in layer_ids:
            issues.append({"level": "critical", "code": "LAYER_MISSING", "message": "缺图层 %s" % want, "refs": []})

    marker_ids = set()
    for m in root.iter():
        if is_tag(m, "marker") and m.get("id"):
            marker_ids.add(m.get("id"))
    for ln in root.iter():
        if not (is_tag(ln, "line") or is_tag(ln, "path")):
            continue
        for attr in ("marker-end", "marker-start"):
            ref = ln.get(attr) or ""
            if ref.startswith("url(#") and ref.endswith(")"):
                mid = ref[5:-1]
                if mid not in marker_ids:
                    issues.append({"level": "critical", "code": "MARKER_UNRESOLVED", "message": "未定义箭头 %s" % mid, "refs": []})

    vb = (root.get("viewBox") or "").split()
    if len(vb) == 4:
        try:
            w = float(vb[2])
            h = float(vb[3])
            for r in root.iter():
                if not is_tag(r, "rect"):
                    continue
                x = _num(r.get("x"), None)
                y = _num(r.get("y"), None)
                rw = _num(r.get("width"), None)
                rh = _num(r.get("height"), None)
                if None in (x, y, rw, rh):
                    continue
                if x < -1 or y < -1 or x + rw > w + 1 or y + rh > h + 1:
                    issues.append({"level": "warning", "code": "OVERFLOW", "message": "rect 越界 (%.0f,%.0f)" % (x, y), "refs": []})
        except ValueError:
            pass

    missing = [n["id"] for n in _collect(ir if isinstance(ir, dict) else {}) if not str(n.get("source_ref") or "").strip()]
    if missing:
        issues.append({"level": "warning", "code": "SOURCE_REF_MISSING", "message": "缺 source_ref 的节点: %s" % ", ".join(missing[:8]), "refs": missing})
    return issues


# --------------------------------------------------------------------------- cli
def main(argv=None):
    ap = argparse.ArgumentParser(prog="figure.py", description="IR -> SVG 与自检")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="IR -> 分层 SVG")
    b.add_argument("--ir", required=True)
    b.add_argument("--out", required=True)
    v = sub.add_parser("validate", help="IR + SVG 自检 -> checks.json")
    v.add_argument("--ir", required=True)
    v.add_argument("--svg", required=True)
    v.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "build":
        ir, err = load_ir(a.ir)
        if ir is None:
            print(json.dumps({"ok": False, "error": err}, ensure_ascii=False))
            return 1
        build_svg(ir, a.out)
        print(json.dumps({"ok": True, "out": a.out}, ensure_ascii=False))
        return 0
    if a.cmd == "validate":
        ir, _ = load_ir(a.ir)
        issues = validate_svg(ir if ir is not None else {}, a.svg)
        crit = sum(1 for i in issues if i["level"] == "critical")
        warn = sum(1 for i in issues if i["level"] == "warning")
        write_json(a.out, {"svg": a.svg, "critical": crit, "warning": warn, "issues": issues})
        print(json.dumps({"ok": True, "critical": crit, "warning": warn, "issues": len(issues)}, ensure_ascii=False))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
