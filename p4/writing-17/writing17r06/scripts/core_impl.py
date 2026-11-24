#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_map.py — 由 map_spec 确定性渲染可编辑 + 可预览的思维导图产物。

仅使用 Python 标准库。输入为语义规格 JSON（由 Agent 按 SKILL.md/references 生成），
输出四类可编辑/可预览产物：
    /app/output/mindmap.mm      FreeMind 1.0.1 XML（XMind/FreeMind 可继续编辑）
    /app/output/mindmap.svg     分层树形 SVG 预览（浏览器可看、矢量可改）
    /app/output/outline.md      带证据标记的大纲（人类可读可改）
    /app/output/evidence.json   节点/关系 -> locator + 逐字 quote 的溯源映射
同时写 /app/work/artifacts.json 清单，供 validate.py 合并。

用法:
    python3 -B scripts/build_map.py --spec /app/work/map_spec.json \
        --output /app/output --work /app/work
"""
import argparse
import hashlib
import json
import os
import sys
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
# 视觉与语义常量
# ---------------------------------------------------------------------------
NODE_KINDS = ["root", "concept", "question", "method", "evidence",
              "conclusion", "task", "risk", "gap", "decision",
              "goal", "milestone", "deliverable", "owner", "deadline", "other"]

NODE_STYLE = {
    "root":       {"fill": "#1f6fb2", "stroke": "#154a78", "text": "#ffffff"},
    "concept":    {"fill": "#e8f1fb", "stroke": "#1f6fb2", "text": "#1a1a1a"},
    "question":   {"fill": "#fdecea", "stroke": "#c0392b", "text": "#1a1a1a"},
    "method":     {"fill": "#e6f4ea", "stroke": "#2e7d32", "text": "#1a1a1a"},
    "evidence":   {"fill": "#fff7e0", "stroke": "#b78a00", "text": "#1a1a1a"},
    "conclusion": {"fill": "#efe8f7", "stroke": "#5e35b1", "text": "#1a1a1a"},
    "task":       {"fill": "#e0f6f8", "stroke": "#007a86", "text": "#1a1a1a"},
    "risk":       {"fill": "#fbe9e7", "stroke": "#d84315", "text": "#1a1a1a"},
    "gap":        {"fill": "#f3e5f5", "stroke": "#8e24aa", "text": "#1a1a1a"},
    "decision":   {"fill": "#eceff1", "stroke": "#37474f", "text": "#1a1a1a"},
    "goal":       {"fill": "#e0f2f1", "stroke": "#00695c", "text": "#1a1a1a"},
    "milestone":  {"fill": "#fce4ec", "stroke": "#c2185b", "text": "#1a1a1a"},
    "deliverable":{"fill": "#fff3e0", "stroke": "#e65100", "text": "#1a1a1a"},
    "owner":      {"fill": "#e0f2f1", "stroke": "#00695c", "text": "#1a1a1a"},
    "deadline":   {"fill": "#fbe9e7", "stroke": "#bf360c", "text": "#1a1a1a"},
    "other":      {"fill": "#f5f5f5", "stroke": "#616161", "text": "#1a1a1a"},
}

REL_TYPES = ["causes", "depends_on", "contrasts", "precedes", "cites",
             "supports", "conflicts", "part_of", "example_of",
             "defined_by", "measures", "limits"]

REL_COLOR = {
    "causes": "#d84315", "depends_on": "#1f6fb2", "contrasts": "#8e24aa",
    "precedes": "#546e7a", "cites": "#007a86", "supports": "#2e7d32",
    "conflicts": "#c0392b", "part_of": "#5e35b1", "example_of": "#6d4c41",
    "defined_by": "#00695c", "measures": "#b78a00", "limits": "#bf360c",
}

FONT_SIZE = 13
LINE_HEIGHT = 17
MAX_LINE_UNITS = 18       # 节点文本单行最大显示宽度（约 18 个 CJK 字）
MAX_LINES = 5
H_GAP = 288               # 列间距
V_SLOT = 80               # 层内节点垂直槽位
MARGIN = 44
MAX_NODES = 400

KIND_CN = {
    "root": "主题", "concept": "概念", "question": "问题", "method": "方法",
    "evidence": "证据", "conclusion": "结论", "task": "任务", "risk": "风险",
    "gap": "空白", "decision": "决策", "goal": "目标", "milestone": "里程碑",
    "deliverable": "交付物", "owner": "负责人", "deadline": "截止", "other": "其他",
}


def xml_escape(s):
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;")
            .replace("'", "&apos;"))


def char_units(ch):
    return 1.0 if ord(ch) > 0x2E80 else 0.56


def text_units(s):
    return sum(char_units(c) for c in str(s or ""))


def wrap_text(text, max_units=MAX_LINE_UNITS, max_lines=MAX_LINES):
    """按显示宽度折行，超长截断。"""
    text = str(text or "").strip()
    if not text:
        return [""]
    lines, cur, cur_w = [], "", 0.0
    for ch in text:
        w = char_units(ch)
        if ch in "\n":
            lines.append(cur); cur, cur_w = "", 0.0
            if len(lines) >= max_lines:
                break
            continue
        if cur and cur_w + w > max_units:
            lines.append(cur); cur, cur_w = ch, w
        else:
            cur += ch; cur_w += w
        if len(lines) >= max_lines and cur:
            # 提前结束
            pass
    if cur and len(lines) < max_lines:
        lines.append(cur)
    lines = lines[:max_lines]
    if len(lines) == max_lines:
        # 标记截断
        last = lines[-1]
        if text_units("".join(lines)) < text_units(text):
            lines[-1] = last.rstrip("…") + "…"
    return [ln for ln in lines if ln != ""] or [""]


# ---------------------------------------------------------------------------
# 谱规约解析
# ---------------------------------------------------------------------------
def collect_nodes(root, errors):
    """前序遍历树，返回有序节点列表 + 每个节点的 parent/depth/children。"""
    nodes, order = {}, []
    ids = set()

    def walk(node, parent, depth):
        if not isinstance(node, dict):
            errors.append("node not object under parent=%s" % parent)
            return
        nid = str(node.get("id", "")).strip()
        if not nid:
            nid = "n%d" % (len(order) + 1)
        if nid in ids:
            errors.append("duplicate node id: %s" % nid)
            nid = "%s_%d" % (nid, len(order))
        ids.add(nid)
        node["id"] = nid
        node["parent"] = parent
        node["depth"] = depth
        node["_children_ids"] = []
        nodes[nid] = node
        order.append(node)
        for ch in node.get("children", []) or []:
            cid = walk(ch, nid, depth + 1)
            node["_children_ids"].append(cid)
        return nid

    walk(root, None, 0)
    return nodes, order


def normalize_kind(node):
    k = str(node.get("kind", "other")).strip().lower()
    return k if k in NODE_KINDS else "other"


def evidence_of(node):
    ev = node.get("evidence") if isinstance(node, dict) else None
    if not isinstance(ev, dict):
        return "", "", "unattributed"
    loc = str(ev.get("locator", "") or "").strip()
    quote = str(ev.get("quote", "") or "").strip()
    cls = str(ev.get("class", "") or ev.get("evidence_class", "")
              or "unattributed").strip().lower()
    if cls not in ("fact", "summary", "inference"):
        cls = "unattributed"
    return loc, quote, cls


# ---------------------------------------------------------------------------
# 布局（确定性：叶子按谱中顺序占槽，内部节点取子叶中心）
# ---------------------------------------------------------------------------
def layout(nodes, order):
    leaf_slot = [0]

    def assign(node):
        children = [nodes[c] for c in node.get("_children_ids", [])]
        if not children:
            node["_y"] = leaf_slot[0]
            leaf_slot[0] += 1
            return node["_y"], node["_y"]
        first = last = None
        for ch in children:
            f, l = assign(ch)
            first = f if first is None else first
            last = l
        node["_y"] = (first + last) / 2.0
        return first, last

    assign(order[0])
    return leaf_slot[0]


# ---------------------------------------------------------------------------
# FreeMind 渲染
# ---------------------------------------------------------------------------
def render_mm(root_node, nodes, relations, title):
    mm = ET.Element("map", {"version": "1.0.1"})

    def add(node, parent_el):
        loc, quote, cls = evidence_of(node)
        attrs = {
            "ID": node["id"],
            "TEXT": str(node.get("text", "") or ""),
            "POSITION": "left" if node is root_node else "right",
            "FOLDED": "false",
        }
        st = NODE_STYLE.get(normalize_kind(node), NODE_STYLE["other"])
        attrs["COLOR"] = st["stroke"]
        el = ET.SubElement(parent_el, "node", attrs)
        el.append(_note_html(loc, quote, cls, node))
        # 关系箭头（cross-link）挂在源节点上
        for rel in relations:
            if rel.get("from") == node["id"]:
                dest = rel.get("to", "")
                color = REL_COLOR.get(rel.get("type", ""), "#616161")
                ET.SubElement(el, "arrowlink", {
                    "DESTINATION": str(dest),
                    "COLOR": color,
                    "ENDARROW": "Default",
                })
        for cid in node.get("_children_ids", []):
            add(nodes[cid], el)

    add(root_node, mm)
    return mm


def _note_html(loc, quote, cls, node):
    """构造 richcontent NOTE 的 html 子树（ET 表达）。"""
    import xml.etree.ElementTree as _ET
    rc = _ET.Element("richcontent", {"TYPE": "NOTE"})
    html = _ET.SubElement(rc, "html")
    head = _ET.SubElement(html, "head")
    body = _ET.SubElement(html, "body")
    p = _ET.SubElement(body, "p")
    parts = ["[%s] %s" % (normalize_kind(node),
                          str(node.get("text", "") or ""))]
    if loc:
        parts.append("source: %s" % loc)
    if quote:
        parts.append("quote: %s" % quote)
    if cls and cls != "unattributed":
        parts.append("evidence_class: %s" % cls)
    p.text = "\n".join(parts)
    return rc


# ---------------------------------------------------------------------------
# SVG 渲染
# ---------------------------------------------------------------------------
def svg_style(node):
    st = NODE_STYLE.get(normalize_kind(node), NODE_STYLE["other"])
    return st


def render_svg(nodes, order, relations, title, meta):
    if not order:
        return "", []
    root_node = order[0]
    leaf_count = layout(nodes, order)
    max_depth = max(n["depth"] for n in order)

    # 计算每个节点的盒宽
    line_cache = {}
    widths = {}
    for n in order:
        lines = wrap_text(n.get("text", ""))
        line_cache[n["id"]] = lines
        max_w = max((text_units(ln) for ln in lines), default=0)
        widths[n["id"]] = max_w * FONT_SIZE + 18

    # 画布
    col_w = max(widths.values()) if widths else 200
    H_GAPx = max(H_GAP, col_w + 70)
    W = MARGIN * 2 + max_depth * H_GAPx + col_w + 20
    H = MARGIN * 2 + max(leaf_count - 1, 0) * V_SLOT + 90

    pos = {}
    for n in order:
        x = MARGIN + n["depth"] * H_GAPx
        y = MARGIN + n["_y"] * V_SLOT
        pos[n["id"]] = (x, y, widths[n["id"]])

    out = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" '
               'height="%d" viewBox="0 0 %d %d" font-family="'
               "'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',"
               'sans-serif">' % (W, H, W, H))
    out.append('<rect width="100%" height="100%" fill="#ffffff"/>')

    # 标题与图例
    title_txt = xml_escape(title or "思维导图")
    out.append('<text x="%d" y="26" font-size="16" font-weight="bold" '
               'fill="#1a1a1a">%s</text>' % (MARGIN, title_txt))
    legend_y = 46
    out.append('<text x="%d" y="%d" font-size="10" fill="#666">图例(kind)</text>'
               % (MARGIN, legend_y))
    lx = MARGIN + 70
    for k in NODE_KINDS:
        st = NODE_STYLE[k]
        if lx > W - 90:
            break
        out.append('<rect x="%d" y="%d" width="10" height="10" rx="2" '
                   'fill="%s" stroke="%s"/>' % (lx, legend_y - 9,
                                                st["fill"], st["stroke"]))
        out.append('<text x="%d" y="%d" font-size="10" fill="#444">%s</text>'
                   % (lx + 13, legend_y, KIND_CN.get(k, k)))
        lx += 13 + 10 * len(KIND_CN.get(k, k)) + 18

    # 层级连线（先画，保证在节点下方）
    for n in order:
        if n is root_node or n.get("parent") is None:
            continue
        p = nodes[n["parent"]]
        px, py, pw = pos[p["id"]]
        cx, cy, cw = pos[n["id"]]
        x1, y1 = px + pw, py
        x2, y2 = cx, cy
        midx = (x1 + x2) / 2.0
        out.append('<path d="M %.1f %.1f C %.1f %.1f, %.1f %.1f, %.1f %.1f" '
                   'fill="none" stroke="#b0bec5" stroke-width="1.4"/>'
                   % (x1, y1, midx, y1, midx, y2, x2, y2))

    # 关系连线（cross-link，虚线带箭头与标签）
    for rel in relations:
        f = rel.get("from"); t = rel.get("to")
        if f not in pos or t not in pos:
            continue
        x1, y1, _ = pos[f]
        x2, y2, _ = pos[t]
        color = REL_COLOR.get(rel.get("type", ""), "#616161")
        # 二次贝塞尔，带垂直拱起避免与普通连线重合
        bow = 24 if y2 >= y1 else -24
        mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0 + bow
        out.append('<path d="M %.1f %.1f Q %.1f %.1f %.1f %.1f" fill="none" '
                   'stroke="%s" stroke-width="1.6" stroke-dasharray="5,3"/>'
                   % (x1, y1, mx, my, x2, y2, color))
        _svg_arrow(out, x1, y1, mx, my, x2, y2, color)
        label = rel.get("label") or rel.get("type") or ""
        if label:
            out.append('<text x="%.1f" y="%.1f" font-size="10" fill="%s" '
                       'text-anchor="middle">%s</text>'
                       % (mx, my - 4, color, xml_escape(label)))

    # 节点盒
    for n in order:
        x, y, w = pos[n["id"]]
        lines = line_cache[n["id"]]
        h = len(lines) * LINE_HEIGHT + 12
        st = svg_style(n)
        top = y - h / 2.0
        rx = 8 if n is root_node else 6
        out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                   'rx="%d" fill="%s" stroke="%s" stroke-width="1.6"/>'
                   % (x, top, w, h, rx, st["fill"], st["stroke"]))
        # 根节点左侧加粗描边暗示中心
        ty = top + LINE_HEIGHT
        for ln in lines:
            out.append('<text x="%.1f" y="%.1f" font-size="%d" fill="%s">%s'
                       '</text>' % (x + 9, ty, FONT_SIZE, st["text"],
                                    xml_escape(ln)))
            ty += LINE_HEIGHT
        # kind 小标签（颜色随节点背景自适应）
        out.append('<text x="%.1f" y="%.1f" font-size="9" fill="%s">%s</text>'
                   % (x + 9, top + h - 4, st["text"],
                      KIND_CN.get(normalize_kind(n), "other")))

    out.append('</svg>')
    return "\n".join(out), []


def _svg_arrow(out, x1, y1, mx, my, x2, y2, color):
    """手动画箭头（二次贝塞尔终点切线方向）。"""
    import math
    # 终点切线方向：Q 曲线在 t=1 处方向 = 2*(P2 - P1)
    dx, dy = 2.0 * (x2 - mx), 2.0 * (y2 - my)
    norm = math.hypot(dx, dy) or 1.0
    ux, uy = dx / norm, dy / norm
    px, py = -uy, ux  # 垂直方向
    ah = 7.0
    bx = x2 - ux * ah
    out.append('<polygon points="%.1f,%.1f %.1f,%.1f %.1f,%.1f" fill="%s"/>'
               % (x2, y2, bx + px * 3.5, y2 + py * 3.5,
                  bx - px * 3.5, y2 - py * 3.5, color))


# ---------------------------------------------------------------------------
# 大纲与证据映射
# ---------------------------------------------------------------------------
def render_outline(nodes, order, relations, title):
    lines = ["# %s" % (title or "思维导图"), ""]
    meta_rels = {}
    for rel in relations:
        meta_rels.setdefault(rel.get("from"), []).append(rel)

    def emit(node, depth):
        ind = "  " * depth
        loc, quote, cls = evidence_of(node)
        kind = KIND_CN.get(normalize_kind(node), "other")
        seg = "%s- [%s] %s" % (ind, kind, str(node.get("text", "") or ""))
        marks = []
        if loc:
            marks.append("⟦%s⟧" % loc)
        if quote:
            marks.append('"%s"' % (quote[:80] + ("…" if len(quote) > 80 else "")))
        if cls and cls != "unattributed":
            marks.append("(%s)" % cls)
        if marks:
            seg += "  " + " ".join(marks)
        lines.append(seg)
        for rid in meta_rels.get(node["id"], []):
            tgt = nodes.get(rid["to"], {})
            tgt_txt = str(tgt.get("text", "") or rid["to"])
            lines.append("%s  ↳ [%s] → %s%s" % (
                ind, rid.get("type", ""), tgt_txt,
                (" · %s" % rid["label"]) if rid.get("label") else ""))
        for cid in node.get("_children_ids", []):
            emit(nodes[cid], depth + 1)

    emit(order[0], 0)
    return "\n".join(lines) + "\n"


def build_evidence(nodes, order, relations, case_id):
    ev_nodes = {}
    for n in order:
        loc, quote, cls = evidence_of(n)
        ev_nodes[n["id"]] = {
            "text": str(n.get("text", "") or ""),
            "kind": normalize_kind(n),
            "locator": loc,
            "quote": quote,
            "evidence_class": cls,
        }
    ev_rels = []
    for rel in relations:
        loc, quote, cls = evidence_of(rel)
        ev_rels.append({
            "from": rel.get("from"), "to": rel.get("to"),
            "type": rel.get("type"), "label": rel.get("label", ""),
            "locator": loc, "quote": quote, "evidence_class": cls,
        })
    return {
        "case_id": case_id or "",
        "counts": {"nodes": len(order), "relations": len(relations)},
        "nodes": ev_nodes,
        "relations": ev_rels,
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def sha256_of(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def main():
    ap = argparse.ArgumentParser(description="Render editable mind-map artifacts.")
    ap.add_argument("--spec", default="/app/work/map_spec.json")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args()

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    errors, degradations = [], []

    spec = None
    try:
        with open(args.spec, "rb") as fh:
            raw = fh.read().decode("utf-8-sig")
        spec = json.loads(raw)
    except (OSError, ValueError, TypeError) as e:
        errors.append("map_spec unreadable: %s" % e)

    if spec is None or not isinstance(spec, dict) or not spec.get("root"):
        # 无规格：降级为单节点占位，仍产出合法产物
        spec = {
            "title": "未提供导图规格",
            "meta": {},
            "root": {"id": "n1", "text": "材料不足或规格缺失",
                     "kind": "other", "evidence": None, "children": []},
            "relations": [],
            "limitations": ["map_spec 缺失或为空；未进行语义抽取"],
            "pending_human": ["确认是否提供了材料与任务说明"],
        }
        degradations.append("map_spec missing/empty; placeholder rendered")

    title = str(spec.get("title", "") or "思维导图")
    meta = spec.get("meta", {}) or {}
    root = spec["root"]
    relations = spec.get("relations", []) or []
    limitations = spec.get("limitations", []) or []
    pending = spec.get("pending_human", []) or []

    nodes, order = collect_nodes(root, errors)
    if len(order) > MAX_NODES:
        degradations.append("node count %d exceeds %d; truncated"
                            % (len(order), MAX_NODES))
        order = order[:MAX_NODES]
        nodes = {n["id"]: n for n in order}
        for n in order:
            n["_children_ids"] = [c for c in n.get("_children_ids", [])
                                  if c in nodes]

    # 校验关系端点
    valid_rels = []
    for rel in relations:
        if not isinstance(rel, dict):
            degradations.append("relation not object: %r" % (rel,))
            continue
        f, t = rel.get("from"), rel.get("to")
        if f not in nodes or t not in nodes:
            degradations.append("relation endpoint missing: %s -> %s"
                                % (f, t))
            continue
        if rel.get("type") not in REL_TYPES:
            rel["type"] = "other"
            degradations.append("relation type normalized: %s -> %s"
                                % (f, t))
        valid_rels.append(rel)
    relations = valid_rels

    case_id = str(spec.get("case_id", "") or "")

    # 渲染各产物
    mm = render_mm(root, nodes, relations, title)
    mm_bytes = ET.tostring(mm, encoding="utf-8").decode("utf-8")
    mm_path = os.path.join(args.output, "mindmap.mm")
    with open(mm_path, "w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n' + mm_bytes + "\n")

    svg_text, _ = render_svg(nodes, order, relations, title, meta)
    svg_path = os.path.join(args.output, "mindmap.svg")
    with open(svg_path, "w", encoding="utf-8") as fh:
        fh.write(svg_text + "\n")

    outline_text = render_outline(nodes, order, relations, title)
    md_path = os.path.join(args.output, "outline.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(outline_text)

    evidence = build_evidence(nodes, order, relations, case_id)
    evidence["errors"] = errors
    evidence["degradations"] = degradations
    ev_path = os.path.join(args.output, "evidence.json")
    with open(ev_path, "w", encoding="utf-8") as fh:
        json.dump(evidence, fh, ensure_ascii=False, indent=2)

    manifest = {
        "title": title,
        "case_id": case_id,
        "limitations": limitations,
        "pending_human": pending,
        "errors": errors,
        "degradations": degradations,
        "artifacts": [
            {"path": "mindmap.mm", "bytes": os.path.getsize(mm_path),
             "sha256": sha256_of(mm_path), "type": "freemind_editable"},
            {"path": "mindmap.svg", "bytes": os.path.getsize(svg_path),
             "sha256": sha256_of(svg_path), "type": "svg_preview"},
            {"path": "outline.md", "bytes": os.path.getsize(md_path),
             "sha256": sha256_of(md_path), "type": "markdown_outline"},
            {"path": "evidence.json", "bytes": os.path.getsize(ev_path),
             "sha256": sha256_of(ev_path), "type": "evidence_map"},
        ],
    }
    with open(os.path.join(args.work, "artifacts.json"), "w",
              encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    print(json.dumps({
        "status": "rendered",
        "case_id": case_id,
        "nodes": len(order),
        "relations": len(relations),
        "errors": errors,
        "degradations": degradations,
        "artifacts": [a["path"] for a in manifest["artifacts"]],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
