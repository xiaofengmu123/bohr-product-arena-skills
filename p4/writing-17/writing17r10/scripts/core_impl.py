#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_mindmap.py —— 把结构化思维导图渲染为多格式产物（纯标准库）。

输入：mindmap JSON（title / root 嵌套树 / relations / metadata）。
输出：mindmap.svg、mindmap.pdf、mindmap.mm、mindmap.md、mindmap.json。

用法：
    python3 -B scripts/render_mindmap.py \
        --mindmap /app/work/mindmap.json --output /app/output

布局确定性：同一输入重复运行结果一致（同级保持输入顺序，坐标由 DFS 槽位计算）。
"""
import argparse
import html
import json
import math
import os
import re
import sys
import unicodedata

DEFAULT_MINDMAP = "/app/work/mindmap.json"
DEFAULT_OUTPUT = "/app/output"

FONT_SIZE = 13
LINE_H = FONT_SIZE + 6
PAD_X = 8
PAD_Y = 5
DX = 46  # 每层水平间距
DY = 26  # 叶节点垂直槽位间距
MARGIN = 30
MAX_LINE_UNITS = 18  # 换行宽度（CJK 字宽 = 1.0，ASCII ≈ 0.55）

VALID_KINDS = {
    "root", "research_question", "concept", "method", "evidence",
    "conclusion", "finding", "task", "risk", "gap", "limitation",
    "decision", "stakeholder", "milestone", "other",
}
VALID_REL_TYPES = {
    "causal", "dependency", "contrast", "temporal", "citation",
    "support", "conflict", "part_of", "example_of",
}
VALID_CLAIM_TYPES = {"fact", "summary", "inference"}

KIND_STYLE = {
    "root":            {"fill": "#1F4E79", "stroke": "#1F4E79", "text": "#FFFFFF"},
    "research_question": {"fill": "#DDEBF7", "stroke": "#2E75B6", "text": "#0B2A4A"},
    "concept":         {"fill": "#E2EFDA", "stroke": "#538135", "text": "#173B00"},
    "method":          {"fill": "#FFF2CC", "stroke": "#BF8F00", "text": "#3F2B00"},
    "evidence":        {"fill": "#FCE4D6", "stroke": "#C55A11", "text": "#3E1B00"},
    "conclusion":      {"fill": "#E4DFEC", "stroke": "#7030A0", "text": "#26003F"},
    "finding":         {"fill": "#E4DFEC", "stroke": "#7030A0", "text": "#26003F"},
    "task":            {"fill": "#DAE3F3", "stroke": "#2E5AA8", "text": "#0B2440"},
    "risk":            {"fill": "#F8CBAD", "stroke": "#C00000", "text": "#3F0000"},
    "gap":             {"fill": "#F2F2F2", "stroke": "#7F7F7F", "text": "#1F1F1F"},
    "limitation":      {"fill": "#F2F2F2", "stroke": "#7F7F7F", "text": "#1F1F1F"},
    "decision":        {"fill": "#FBE5D6", "stroke": "#C55A11", "text": "#3E1B00"},
    "stakeholder":     {"fill": "#D9E1F2", "stroke": "#2E5AA8", "text": "#0B2440"},
    "milestone":       {"fill": "#FFF2CC", "stroke": "#BF8F00", "text": "#3F2B00"},
    "other":           {"fill": "#F2F2F2", "stroke": "#808080", "text": "#1F1F1F"},
}

REL_STYLE = {
    "causal":     {"color": "#C00000", "dash": "",   "label": "因果"},
    "dependency": {"color": "#2E5AA8", "dash": "",   "label": "依赖"},
    "contrast":   {"color": "#7030A0", "dash": "6,3","label": "对比"},
    "temporal":   {"color": "#538135", "dash": "2,3","label": "时间"},
    "citation":   {"color": "#7F7F7F", "dash": "2,3","label": "引用"},
    "support":    {"color": "#538135", "dash": "",   "label": "支持"},
    "conflict":   {"color": "#C00000", "dash": "6,3","label": "冲突"},
    "part_of":    {"color": "#7F7F7F", "dash": "3,3","label": "包含"},
    "example_of": {"color": "#7F7F7F", "dash": "3,3","label": "例证"},
}


def esc_xml(s):
    return html.escape(str(s), quote=False)


def esc_pdf_text(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def char_unit(ch):
    o = ord(ch)
    if 32 <= o < 127:
        return 0.55
    if unicodedata.category(ch) in ("Zs",):
        return 0.55
    return 1.0


def text_units(s):
    return sum(char_unit(c) for c in s)


def wrap_label(label, max_units=MAX_LINE_UNITS):
    lines, cur, cur_w = [], "", 0.0
    for ch in str(label):
        w = char_unit(ch)
        if cur and cur_w + w > max_units:
            lines.append(cur)
            cur, cur_w = ch, w
        else:
            cur += ch
            cur_w += w
    if cur:
        lines.append(cur)
    return lines or [""]


def line_px(line, fs=FONT_SIZE):
    return text_units(line) * fs


def to_ascii(s, fallback=""):
    keep = "".join(c for c in str(s) if 32 <= ord(c) < 127)
    return keep if keep.strip() else (fallback or "")


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


# ----------------------------------------------------------------------
# 载入与校验
# ----------------------------------------------------------------------
def load_mindmap(path):
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    data = json.loads(text)
    return data


def collect_tree(data):
    """返回 (nodes_dict, root_dict, warnings)。nodes_dict[id] = node；warnings 列表。"""
    warnings = []
    nodes = {}
    counter = [0]

    def _assign_id(node, path):
        nid = node.get("id")
        if not nid or nid in nodes:
            nid = "n%d" % counter[0]
            counter[0] += 1
            node["id"] = nid
            warnings.append("auto-assigned id %s at %s" % (nid, path))
        if nid in nodes:
            warnings.append("duplicate id %s at %s" % (nid, path))
        nodes[nid] = node
        kind = node.get("kind", "other")
        if kind not in VALID_KINDS:
            warnings.append("node %s unknown kind '%s' -> other" % (nid, kind))
            node["kind"] = "other"
        if not isinstance(node.get("children"), list):
            node["children"] = []
        for i, ch in enumerate(node["children"]):
            _assign_id(ch, path + "/" + nid + ".children[%d]" % i)

    root = data.get("root")
    if not isinstance(root, dict):
        raise ValueError("mindmap.root 必须是 dict")
    _assign_id(root, "root")
    return nodes, root, warnings


def validate_relations(relations, nodes):
    warnings = []
    out = []
    for r in (relations or []):
        if not isinstance(r, dict):
            continue
        rtype = r.get("type", "other")
        if rtype not in VALID_REL_TYPES:
            warnings.append("relation %s unknown type '%s'" % (r.get("id", "?"), rtype))
            rtype = "other"
        frm = r.get("from")
        to = r.get("to")
        if frm not in nodes or to not in nodes:
            warnings.append("relation %s references missing node (%s -> %s); dropped"
                            % (r.get("id", "?"), frm, to))
            continue
        out.append({"id": r.get("id", "r%d" % len(out)),
                    "from": frm, "to": to, "type": rtype,
                    "label": r.get("label", REL_STYLE.get(rtype, {}).get("label", "")),
                    "evidence": r.get("evidence", []) or []})
    return out, warnings


# ----------------------------------------------------------------------
# 布局
# ----------------------------------------------------------------------
def layout(root, nodes):
    """返回 {nid: (x, y, w, h, lines)} 与画布尺寸。"""
    leaves = []

    def dfs(node):
        ch = node.get("children", [])
        if not ch:
            leaves.append(node)
        for c in ch:
            dfs(c)

    dfs(root)
    slot = {n["id"]: i for i, n in enumerate(leaves)}

    def sub_slots(node):
        ch = node.get("children", [])
        if not ch:
            return [slot[node["id"]]]
        acc = []
        for c in ch:
            acc.extend(sub_slots(c))
        return acc

    def walk(node, depth):
        ch = node.get("children", [])
        ss = sub_slots(node)
        y_slot = sum(ss) / len(ss)
        lines = wrap_label(node.get("label", "(未命名)"))
        w = max(line_px(l) for l in lines) + 2 * PAD_X
        h = len(lines) * LINE_H + 2 * PAD_Y
        pos[node["id"]] = (MARGIN + depth * DX, MARGIN + y_slot * DY, w, h, lines)
        for c in ch:
            walk(c, depth + 1)

    pos = {}
    walk(root, 0)
    x1 = min(p[0] for p in pos.values())
    y1 = min(p[1] for p in pos.values())
    x2 = max(p[0] + p[2] for p in pos.values())
    y2 = max(p[1] + p[3] for p in pos.values())
    return pos, (x1, y1, x2, y2)


# ----------------------------------------------------------------------
# SVG
# ----------------------------------------------------------------------
LEGEND_KINDS = ["root", "research_question", "concept", "method",
                "evidence", "conclusion", "task", "risk", "gap"]


def render_svg(root, nodes, relations, pos, bbox, metadata, warnings, out_path):
    x1, y1, x2, y2 = bbox
    pad = 24
    legend_h = len(LEGEND_KINDS) * 16 + 8
    W = x2 - x1 + 2 * pad
    H = y2 - y1 + 2 * pad + legend_h

    def gx(x):
        return x - x1 + pad

    def gy(y):
        return y - y1 + pad

    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
                 'viewBox="0 0 %d %d" font-family="DejaVu Sans, Noto Sans CJK SC, sans-serif">'
                 % (int(W), int(H), int(W), int(H)))
    parts.append('<defs>')
    for rtype, st in REL_STYLE.items():
        parts.append('<marker id="arr-%s" viewBox="0 0 10 10" refX="9" refY="5" '
                     'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                     '<path d="M0,0 L10,5 L0,10 z" fill="%s"/></marker>' % (rtype, st["color"]))
    parts.append('</defs>')
    parts.append('<rect x="0" y="0" width="%d" height="%d" fill="white"/>' % (int(W), int(H)))

    # 关系连线（先画，置于节点之下）
    for r in relations:
        a = pos[r["from"]]
        b = pos[r["to"]]
        st = REL_STYLE.get(r["type"], REL_STYLE["part_of"])
        xa, ya = gx(a[0] + a[2]), gy(a[1] + a[3] / 2)
        xb, yb = gx(b[0]), gy(b[1] + b[3] / 2)
        mid = (xa + xb) / 2
        dash = (' stroke-dasharray="%s"' % st["dash"]) if st["dash"] else ""
        parts.append('<path d="M %.1f %.1f C %.1f %.1f, %.1f %.1f, %.1f %.1f" '
                     'fill="none" stroke="%s" stroke-width="1.3"%s marker-end="url(#arr-%s)"/>'
                     % (xa, ya, mid, ya, mid, yb, xb, yb, st["color"], dash, r["type"]))

    # 树边（父→子）
    def edges(node):
        ch = node.get("children", [])
        a = pos[node["id"]]
        for c in ch:
            b = pos[c["id"]]
            xa = gx(a[0] + a[2])
            ya = gy(a[1] + a[3] / 2)
            xb = gx(b[0])
            yb = gy(b[1] + b[3] / 2)
            mid = (xa + xb) / 2
            parts.append('<path d="M %.1f %.1f C %.1f %.1f, %.1f %.1f, %.1f %.1f" '
                         'fill="none" stroke="#9AA5B1" stroke-width="1.4"/>'
                         % (xa, ya, mid, ya, mid, yb, xb, yb))
            edges(c)

    edges(root)

    # 节点
    def nodes_svg(node):
        p = pos[node["id"]]
        x, y, w, h, lines = p
        kind = node.get("kind", "other")
        st = KIND_STYLE.get(kind, KIND_STYLE["other"])
        nx, ny = gx(x), gy(y)
        parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="6" ry="6" '
                     'fill="%s" stroke="%s" stroke-width="1.2"/>'
                     % (nx, ny, w, h, st["fill"], st["stroke"]))
        ty = ny + PAD_Y + (LINE_H - FONT_SIZE) / 2 + FONT_SIZE * 0.8
        for ln in lines:
            parts.append('<text x="%.1f" y="%.1f" font-size="%d" fill="%s">%s</text>'
                         % (nx + PAD_X, ty, FONT_SIZE, st["text"], esc_xml(ln)))
            ty += LINE_H
        for c in node.get("children", []):
            nodes_svg(c)

    nodes_svg(root)

    # 图例（置于树下方，避免遮挡）
    lx = pad + 6
    ly = (y2 - y1) + 2 * pad + 6
    for kind in LEGEND_KINDS:
        st = KIND_STYLE[kind]
        parts.append('<rect x="%d" y="%d" width="12" height="12" rx="2" fill="%s" stroke="%s"/>'
                     % (lx, ly, st["fill"], st["stroke"]))
        parts.append('<text x="%d" y="%d" font-size="11" fill="#333">%s</text>'
                     % (lx + 17, ly + 10, esc_xml(kind)))
        ly += 16
    parts.append('</svg>')
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return out_path


# ----------------------------------------------------------------------
# 最小 PDF（纯标准库；拓扑预览）
# ----------------------------------------------------------------------
class Pdf:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.objs = {}
        self.contents = []

    def c(self, s):
        self.contents.append(s)

    def add_obj(self, num, body):
        self.objs[num] = body

    def write(self, path):
        content = "\n".join(self.contents)
        cb = content.encode("latin-1", errors="replace")
        self.add_obj(5, "<< /Length %d >>\nstream\n" % len(cb) + content + "\nendstream")
        self.add_obj(4, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        self.add_obj(3, "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] "
                        "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>" % (self.w, self.h))
        self.add_obj(2, "<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
        self.add_obj(1, "<< /Type /Catalog /Pages 2 0 R >>")
        out = bytearray(b"%PDF-1.4\n")
        offsets = {}
        for num in sorted(self.objs):
            offsets[num] = len(out)
            out += b"%d 0 obj\n" % num
            out += self.objs[num].encode("latin-1", errors="replace")
            out += b"\nendobj\n"
        xref = len(out)
        n = max(self.objs) + 1
        out += b"xref\n0 %d\n" % n
        out += b"0000000000 65535 f \n"
        for num in range(1, n):
            out += b"%010d 00000 n \n" % offsets[num]
        out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (n, xref)
        with open(path, "wb") as f:
            f.write(bytes(out))
        return path


def render_pdf(root, nodes, relations, pos, bbox, metadata, warnings, out_path):
    x1, y1, x2, y2 = bbox
    pad = 24
    W = x2 - x1 + 2 * pad
    H = y2 - y1 + 2 * pad
    pdf = Pdf(int(W) + 2, int(H) + 2)
    # 坐标换算：PDF 原点左下；布局 y 向下。
    def px(x):
        return x - x1 + pad

    def py(y):
        return (H) - (y - y1 + pad)

    title = to_ascii(metadata.get("title", ""), "mind-map")
    pdf.c("BT /F1 13 Tf %d %d Td (%s) Tj ET" % (int(px(x1)), int(py(y1) + 16), esc_pdf_text(title)))

    def edge(a, b, color, dash=None):
        r, g, bl = hex_to_rgb(color)
        pdf.c("%.3f %.3f %.3f RG" % (r, g, bl))
        if dash:
            pdf.c("[%s] 0 d" % dash)
        xa, ya = px(a[0] + a[2]), py(a[1] + a[3] / 2)
        xb, yb = px(b[0]), py(b[1] + b[3] / 2)
        pts = []
        for t in range(0, 13):
            tt = t / 12.0
            mx = xa + (xb - xa) * tt
            my = ya + (yb - ya) * (1 - (1 - tt) ** 2)  # 简单二次缓动近似
            pts.append((mx, my))
        pdf.c("%.1f %.1f m" % (pts[0][0], pts[0][1]))
        for (mx, my) in pts[1:]:
            pdf.c("%.1f %.1f l" % (mx, my))
        pdf.c("S")
        if dash:
            pdf.c("[] 0 d")

    for r in relations:
        st = REL_STYLE.get(r["type"], REL_STYLE["part_of"])
        edge(pos[r["from"]], pos[r["to"]], st["color"], st["dash"] or None)

    def edges(node):
        a = pos[node["id"]]
        for c in node.get("children", []):
            edge(a, pos[c["id"]], "#9AA5B1")
            edges(c)

    edges(root)

    def nodes_pdf(node):
        p = pos[node["id"]]
        x, y, w, h, lines = p
        kind = node.get("kind", "other")
        st = KIND_STYLE.get(kind, KIND_STYLE["other"])
        fx, fy = px(x), py(y)
        fill = hex_to_rgb(st["fill"])
        stroke = hex_to_rgb(st["stroke"])
        pdf.c("%.3f %.3f %.3f rg" % fill)
        pdf.c("%.1f %.1f %.1f %.1f re f" % (fx, fy - h, w, h))
        pdf.c("%.3f %.3f %.3f RG" % stroke)
        pdf.c("%.1f %.1f %.1f %.1f re S" % (fx, fy - h, w, h))
        label = to_ascii(node.get("label", ""), f"[{kind}] {node['id']}")
        label = esc_pdf_text(label[:60])
        tx = fx + PAD_X
        ty = fy - PAD_Y - FONT_SIZE * 0.8
        pdf.c("BT /F1 8 Tf %.1f %.1f Td (%s) Tj ET" % (tx, ty, label))
        for c in node.get("children", []):
            nodes_pdf(c)

    nodes_pdf(root)
    footer = ("Topology preview only (ASCII labels); full readable text in mindmap.svg "
              "because CJK font embedding is unavailable in the pure-stdlib runtime.")
    pdf.c("BT /F1 7 Tf %d %d Td (%s) Tj ET" % (int(px(x1)), int(py(y2)) - 8, esc_pdf_text(footer)))
    pdf.write(out_path)
    return out_path


# ----------------------------------------------------------------------
# FreeMind .mm
# ----------------------------------------------------------------------
def render_mm(root, nodes, relations, out_path):
    def evidence_note(ev):
        loc = ev.get("locator", "?")
        quote = ev.get("quote", "")
        ct = ev.get("claim_type", "summary")
        return "证据[%s] %s :: %s" % (ct, loc, quote)

    def node_xml(node, extra_children=None):
        nid = node["id"]
        kind = node.get("kind", "other")
        st = KIND_STYLE.get(kind, KIND_STYLE["other"])
        txt = esc_xml(node.get("label", "(未命名)"))
        attrs = 'TEXT="%s" ID="%s" POSITION="right" COLOR="%s"' % (txt, nid, st["stroke"])
        inner = []
        evs = (node.get("evidence") or [])
        if evs:
            inner.append('<richcontent TYPE="NOTE"><html><head></head><body>')
            inner.append("<p>%s</p>" % esc_xml(" | ".join(evidence_note(e) for e in evs)))
            inner.append("</body></html></richcontent>")
        for ch in node.get("children", []):
            inner.append(node_xml(ch))
        if extra_children:
            inner.extend(extra_children)
        if not inner:
            return "<node %s/>" % attrs
        return "<node %s>%s</node>" % (attrs, "".join(inner))

    rel_children = []
    for r in relations:
        st = REL_STYLE.get(r["type"], REL_STYLE["part_of"])
        label = "%s -> %s (%s) %s" % (r["from"], r["to"], r["type"], r.get("label", ""))
        inner = []
        evs = (r.get("evidence") or [])
        if evs:
            inner.append('<richcontent TYPE="NOTE"><html><head></head><body>')
            inner.append("<p>%s</p>" % esc_xml(" | ".join(evidence_note(e) for e in evs)))
            inner.append("</body></html></richcontent>")
        rel_children.append('<node TEXT="%s" ID="%s" POSITION="right">%s</node>'
                            % (esc_xml(label), esc_xml(r.get("id", "")), "".join(inner)))

    extra = []
    if rel_children:
        extra.append('<node TEXT="关系" POSITION="right" COLOR="#7F7F7F">%s</node>'
                     % "".join(rel_children))

    parts = ['<map version="1.0.1">']
    parts.append(node_xml(root, extra_children=extra))
    parts.append('</map>')
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return out_path


# ----------------------------------------------------------------------
# Markdown 大纲
# ----------------------------------------------------------------------
def render_md(root, relations, metadata, warnings, out_path):
    L = []
    L.append("# %s" % (metadata.get("title") or "思维导图"))
    if metadata.get("audience"):
        L.append("> 受众：%s" % metadata["audience"])
    if metadata.get("task_summary"):
        L.append("> 任务：%s" % metadata["task_summary"])
    L.append("")

    def walk(node, depth):
        kind = node.get("kind", "other")
        label = node.get("label", "(未命名)")
        L.append("%s- **%s** `[%s]`" % ("  " * depth, label, kind))
        for ev in (node.get("evidence") or []):
            q = ev.get("quote", "")
            loc = ev.get("locator", "?")
            ct = ev.get("claim_type", "summary")
            L.append("%s  - %s · `%s`：%s" % ("  " * depth, ct, loc, q))
        for c in node.get("children", []):
            walk(c, depth + 1)

    walk(root, 0)
    if relations:
        L.append("")
        L.append("## 关系")
        for r in relations:
            st = REL_STYLE.get(r["type"], REL_STYLE["part_of"])
            L.append("- `%s → %s`（%s）%s" % (r["from"], r["to"], r["type"], r.get("label", "")))
            for ev in r.get("evidence", []):
                L.append("  - %s · `%s`：%s" % (ev.get("claim_type", "summary"),
                                                ev.get("locator", "?"), ev.get("quote", "")))
    if warnings:
        L.append("")
        L.append("## 渲染警告")
        for w in warnings:
            L.append("- %s" % w)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return out_path


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mindmap", default=DEFAULT_MINDMAP)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    args = ap.parse_args()
    os.makedirs(args.output, exist_ok=True)

    data = load_mindmap(args.mindmap)
    nodes, root, warnings = collect_tree(data)
    relations, rwarn = validate_relations(data.get("relations", []), nodes)
    warnings.extend(rwarn)

    metadata = data.get("metadata") or {}
    title = metadata.get("title") or data.get("title") or root.get("label", "思维导图")
    metadata.setdefault("title", title)

    pos, bbox = layout(root, nodes)

    out = {}
    out["mindmap_svg"] = render_svg(root, nodes, relations, pos, bbox, metadata, warnings,
                                    os.path.join(args.output, "mindmap.svg"))
    out["mindmap_pdf"] = render_pdf(root, nodes, relations, pos, bbox, metadata, warnings,
                                    os.path.join(args.output, "mindmap.pdf"))
    out["mindmap_mm"] = render_mm(root, nodes, relations, os.path.join(args.output, "mindmap.mm"))
    out["mindmap_md"] = render_md(root, relations, metadata, warnings,
                                  os.path.join(args.output, "mindmap.md"))

    # 规范化 JSON 源
    data["title"] = title
    data["metadata"] = metadata
    data["root"] = root
    data["relations"] = relations
    norm_path = os.path.join(args.output, "mindmap.json")
    with open(norm_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    out["mindmap_json"] = norm_path

    summary = {
        "ok": True,
        "node_count": len(nodes),
        "relation_count": len(relations),
        "canvas": [round(bbox[2] - bbox[0]), round(bbox[3] - bbox[1])],
        "warnings": warnings,
        "artifacts": out,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
