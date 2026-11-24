#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_mindmap.py — 把一个思维导图规格(spec)渲染成可编辑、可预览、可追溯的产物。

输入: 一份 mindmap spec JSON（由 Agent 分析材料后填写，路径默认 /app/work/mindmap_spec.json）。
输出（写入 --output 目录）:
  - mindmap.md           可编辑 Markdown 大纲（含关系与证据标注）
  - mindmap.mm           FreeMind 1.0.1 XML（FreeMind/XMind 可打开、可编辑）
  - mindmap.svg          确定性树形布局的 SVG 预览（可再渲染为 PDF/PNG）
  - evidence_map.json    节点/关系 -> locator + 逐字 quote 的证据映射

规格结构见 references/node-schema.md。布局确定性：不写时间戳、按 spec 中的
节点顺序稳定遍历，因此同一 spec 重复运行结果逐字节一致。

只依赖 Python 标准库。退出码恒为 0；失败写 render_errors.json，不抛异常。
"""

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

KIND_STYLE = {
    "concept":    ("#E3F2FD", "#1565C0"),
    "question":   ("#FFF3E0", "#E65100"),
    "method":     ("#E8F5E9", "#2E7D32"),
    "evidence":   ("#F3E5F5", "#6A1B9A"),
    "conclusion": ("#FCE4EC", "#AD1457"),
    "task":       ("#FFFDE7", "#F9A825"),
    "risk":       ("#FFEBEE", "#C62828"),
    "note":       ("#ECEFF1", "#455A64"),
    "topic":      ("#E1F5FE", "#0277BD"),
}
DEFAULT_STYLE = ("#FFFFFF", "#37474F")


def read_spec(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return data, None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def sanitize_text(text, maxlen=120):
    if not isinstance(text, str):
        text = str(text)
    text = text.replace("\x00", "").strip()
    if len(text) > maxlen:
        text = text[: maxlen - 1] + "…"
    return text


def build_tree(spec):
    """返回 (nodes_by_id, children_map, root_id, order)。"""
    nodes = {}
    root_id = "root"
    nodes[root_id] = {
        "id": root_id, "text": sanitize_text(spec.get("title") or spec.get("root", {}).get("text") or "主题"),
        "kind": spec.get("root", {}).get("kind", "topic"),
        "parent": None, "relation": None, "relation_to": None,
        "evidence": spec.get("root", {}).get("evidence", []),
        "inferred": bool(spec.get("root", {}).get("inferred", False)),
    }
    children = {root_id: []}
    order = [root_id]
    for item in spec.get("nodes", []):
        if not isinstance(item, dict):
            continue
        nid = sanitize_text(item.get("id") or "n%d" % len(nodes), 40)
        # 避免 id 冲突
        base = nid
        i = 2
        while nid in nodes:
            nid = "%s_%d" % (base, i)
            i += 1
        nodes[nid] = {
            "id": nid,
            "text": sanitize_text(item.get("text") or item.get("label") or "(未命名)"),
            "kind": item.get("kind", "note"),
            "parent": item.get("parent", root_id),
            "relation": item.get("relation"),
            "relation_to": item.get("relation_to"),
            "evidence": item.get("evidence", []),
            "inferred": bool(item.get("inferred", False)),
        }
        children[nid] = []
        order.append(nid)
    # 挂接父子关系
    for nid, node in nodes.items():
        if nid == root_id:
            continue
        p = node.get("parent")
        if p not in nodes:
            p = root_id
            node["parent"] = root_id
        children.setdefault(p, []).append(nid)
    # 子节点按出现顺序排序（spec 顺序，确定性）
    for p in children:
        children[p].sort(key=lambda x: order.index(x))
    return nodes, children, root_id, order


def assign_layout(nodes, children, root_id, x_scale=230, y_scale=66):
    pos = {}
    leaf_counter = [0]

    def walk(nid, depth):
        kids = children.get(nid, [])
        x = depth * x_scale
        if not kids:
            y = leaf_counter[0] * y_scale
            leaf_counter[0] += 1
        else:
            ys = []
            for k in kids:
                walk(k, depth + 1)
                ys.append(pos[k][1])
            y = sum(ys) / len(ys)
        pos[nid] = (x, y)
    walk(root_id, 0)
    return pos


def wrap_text(text, max_chars=16):
    """把文本按 CJK 宽度折行，返回行列表。"""
    text = sanitize_text(text)
    if len(text) <= max_chars:
        return [text]
    lines, cur = [], ""
    for ch in text:
        cur += ch
        if len(cur) >= max_chars:
            lines.append(cur)
            cur = ""
    if cur:
        lines.append(cur)
    return lines or [""]


def est_width(lines):
    cjk = re.compile(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]')
    per = 0.0
    for ln in lines:
        per = max(per, sum(15 if cjk.match(c) else 8.5 for c in ln))
    return max(60, per + 24)


def esc_xml(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


def build_svg(nodes, children, root_id, order, pos, spec):
    title = sanitize_text(spec.get("title") or "思维导图")
    max_y = max((p[1] for p in pos.values()), default=0)
    max_x = max((p[0] for p in pos.values()), default=0)
    width = max_x + 320
    height = max_y + 160
    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
                 'viewBox="0 0 %d %d" xml:lang="zh">' % (width, height, width, height))
    lines.append('<rect width="100%%" height="100%%" fill="#FAFAFA"/>')
    lines.append('<text x="20" y="28" font-size="20" font-family="sans-serif" '
                 'fill="#212121">%s</text>' % esc_xml(title))
    # 边（先画，节点覆盖其上）
    for nid, node in nodes.items():
        if nid == root_id:
            continue
        p = node.get("parent")
        if p not in pos:
            continue
        x1, y1 = pos[p]
        x2, y2 = pos[nid]
        midx, midy = (x1 + x2) / 2, (y1 + y2) / 2
        rel = node.get("relation")
        stroke = "#90A4AE"
        if rel:
            stroke = "#546E7A"
        lines.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" '
                     'stroke-width="1.5" marker-end="url(#arrow)"/>' % (x1, y1, x2, y2, stroke))
        if rel:
            label = esc_xml(sanitize_text(str(rel), 12))
            lines.append('<text x="%d" y="%d" font-size="11" font-family="sans-serif" '
                         'fill="#546E7A">%s</text>' % (midx - 12, midy - 4, label))
    lines.append('<defs><marker id="arrow" markerWidth="8" markerHeight="8" '
                 'refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" '
                 'fill="#546E7A"/></marker></defs>')
    # 节点
    for nid in order:
        node = nodes[nid]
        x, y = pos[nid]
        wrap = wrap_text(node["text"], 16)
        fill, border = KIND_STYLE.get(node.get("kind"), DEFAULT_STYLE)
        w = est_width(wrap)
        h = 22 + 20 * len(wrap)
        x0 = x - w / 2
        y0 = y - h / 2
        rx = 8
        lines.append('<g><rect x="%d" y="%d" width="%d" height="%d" rx="%d" '
                     'fill="%s" stroke="%s" stroke-width="1.5"/>'
                     % (int(x0), int(y0), int(w), int(h), rx, fill, border))
        ty = y0 + 18
        for i, ln in enumerate(wrap):
            lines.append('<text x="%d" y="%d" font-size="13" font-family="sans-serif" '
                         'fill="#212121" text-anchor="middle">%s</text>'
                         % (int(x), int(ty + i * 20), esc_xml(ln)))
        tag = node.get("kind", "")
        lines.append('<text x="%d" y="%d" font-size="9" font-family="sans-serif" '
                     'fill="%s" text-anchor="middle">%s%s</text>'
                     % (int(x), int(y0 + h - 6), border,
                        esc_xml(tag), " ＊" if node.get("inferred") else ""))
        if node.get("evidence"):
            lines.append('<text x="%d" y="%d" font-size="9" font-family="sans-serif" '
                         'fill="#1E88E5" text-anchor="middle">[证%d]</text>'
                         % (int(x + w / 2 - 10), int(y0 + 8), len(node["evidence"])))
        lines.append('<title>%s</title></g>' % esc_xml(node["text"]))
    # 底部证据图例
    foot_y = height - 12
    lines.append('<text x="20" y="%d" font-size="11" font-family="sans-serif" '
                 'fill="#607D8B">证据图例见 evidence_map.json；节点右侧[证n]表示挂接证据条数；＊=推断。</text>' % foot_y)
    lines.append('</svg>')
    return "\n".join(lines)


def build_freemind(nodes, children, root_id, order):
    def emit(nid):
        node = nodes[nid]
        txt = node["text"]
        if node.get("relation"):
            txt = "%s [%s]" % (txt, sanitize_text(str(node["relation"]), 16))
        if node.get("evidence"):
            txt += " 证%d" % len(node["evidence"])
        parts = ['<node TEXT="%s"' % esc_xml(txt)]
        if nid == root_id:
            parts.append(' FOLDED="false" POSITION="right"')
        else:
            parts.append(' POSITION="right"')
        parts.append(">")
        inner = "".join(emit(k) for k in children.get(nid, []))
        if inner:
            parts.append(inner)
        parts.append("</node>")
        return "".join(parts)
    return ('<map version="1.0.1">\n'
            + emit(root_id)
            + "\n</map>\n")


def build_markdown(nodes, children, root_id, order, spec):
    title = sanitize_text(spec.get("title") or "思维导图")
    out = ["# %s" % title, ""]
    out.append("## %s" % nodes[root_id]["text"])
    if nodes[root_id].get("evidence"):
        out.append("  - 证据: %s" % _fmt_evidence(nodes[root_id]["evidence"]))
    _md_walk(root_id, 1, nodes, children, out)
    return "\n".join(out) + "\n"


def _fmt_evidence(ev):
    parts = []
    for e in ev:
        if not isinstance(e, dict):
            continue
        loc = e.get("locator", "?")
        q = sanitize_text(e.get("quote", ""), 60)
        t = e.get("type", "fact")
        parts.append("%s(%s) “%s”" % (loc, t, q))
    return "; ".join(parts) if parts else "无"


def _md_walk(nid, level, nodes, children, out):
    prefix = "  " * level
    for kid in children.get(nid, []):
        node = nodes[kid]
        kind = node.get("kind", "")
        rel = ("←%s " % sanitize_text(str(node["relation"]), 12)) if node.get("relation") else ""
        inf = "＊" if node.get("inferred") else ""
        out.append("%s- [%s] %s%s%s" % (prefix, kind, rel, node["text"], inf))
        if node.get("evidence"):
            out.append("%s  - 证据: %s" % (prefix, _fmt_evidence(node["evidence"])))
        _md_walk(kid, level + 1, nodes, children, out)


def build_evidence_map(nodes, order, spec):
    entries = []
    for nid in order:
        node = nodes[nid]
        if not node.get("evidence"):
            continue
        for e in node["evidence"]:
            if not isinstance(e, dict):
                continue
            entries.append({
                "node_id": nid,
                "node_text": node["text"],
                "kind": node.get("kind"),
                "relation": node.get("relation"),
                "inferred": node.get("inferred", False),
                "locator": e.get("locator"),
                "quote": e.get("quote"),
                "type": e.get("type", "fact"),  # fact | summary | inference
            })
    return entries


def main():
    ap = argparse.ArgumentParser(description="渲染思维导图产物")
    ap.add_argument("--spec", default="/app/work/mindmap_spec.json")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args()

    spec, err = read_spec(args.spec)
    errors = []
    if spec is None:
        errors.append("spec_read_failed:%s" % err)
        spec = {"title": "思维导图", "root": {"text": "材料不足", "kind": "topic"}, "nodes": []}

    try:
        os.makedirs(args.output, exist_ok=True)
        os.makedirs(args.work, exist_ok=True)
    except OSError as exc:
        errors.append("mkdir_failed:%s" % exc)

    nodes, children, root_id, order = build_tree(spec)
    pos = assign_layout(nodes, children, root_id)

    artifacts = {}
    # SVG
    svg = build_svg(nodes, children, root_id, order, pos, spec)
    svg_path = os.path.join(args.output, "mindmap.svg")
    # 校验 XML 合法后再落盘
    try:
        ET.fromstring(svg)
        with open(svg_path, "w", encoding="utf-8") as fh:
            fh.write(svg)
        artifacts["svg"] = svg_path
    except Exception as exc:  # noqa: BLE001
        errors.append("svg_write_failed:%s" % exc)

    # FreeMind .mm
    try:
        mm = build_freemind(nodes, children, root_id, order)
        mm_path = os.path.join(args.output, "mindmap.mm")
        with open(mm_path, "w", encoding="utf-8") as fh:
            fh.write(mm)
        artifacts["freemind"] = mm_path
    except Exception as exc:  # noqa: BLE001
        errors.append("freemind_write_failed:%s" % exc)

    # Markdown
    try:
        md = build_markdown(nodes, children, root_id, order, spec)
        md_path = os.path.join(args.output, "mindmap.md")
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write(md)
        artifacts["markdown"] = md_path
    except Exception as exc:  # noqa: BLE001
        errors.append("markdown_write_failed:%s" % exc)

    # 证据映射
    try:
        ev = build_evidence_map(nodes, order, spec)
        ev_path = os.path.join(args.output, "evidence_map.json")
        with open(ev_path, "w", encoding="utf-8") as fh:
            json.dump(ev, fh, ensure_ascii=False, indent=2)
        artifacts["evidence_map"] = ev_path
    except Exception as exc:  # noqa: BLE001
        errors.append("evidence_map_write_failed:%s" % exc)

    # 渲染日志
    report = {
        "n_nodes": len(nodes),
        "n_evidence_entries": sum(1 for n in nodes.values() if n.get("evidence")),
        "artifacts": artifacts,
        "errors": errors,
    }
    try:
        with open(os.path.join(args.work, "render_report.json"), "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        errors.append("render_report_write_failed:%s" % exc)

    print(json.dumps(report, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        import traceback
        print(json.dumps({"fatal": traceback.format_exc()}, ensure_ascii=False))
        sys.exit(0)
