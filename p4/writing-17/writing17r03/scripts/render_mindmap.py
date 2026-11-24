#!/usr/bin/env python3
"""render_mindmap.py — 把思维导图 spec（JSON）渲染成可编辑、可预览的多格式产物。

输入
----
/app/work/mindmap_spec.json（由 Agent 按 SKILL.md 的 spec 规范生成）：
{
  "title": "根主题",
  "root": {"id": "root", "text": "...", "kind": "topic", "note": "", "evidence": [{"locator": "...", "quote": "..."}]},
  "nodes": [{"id": "n1", "text": "...", "kind": "concept|question|method|evidence|conclusion|task|risk|gap|milestone|decision|todo",
             "parent": "root", "note": "", "evidence": [...]}],
  "relations": [{"from": "n1", "to": "n2", "kind": "causal|dependency|contrast|temporal|citation|support|conflict|part-of",
                 "label": "", "evidence": [...]}]
}

输出（全部写到 --output，确定性布局）
------------------------------------
mindmap.md        Markdown 大纲（可编辑）
mindmap.mmd       Mermaid mindmap（可编辑文本）
mindmap.graphml   GraphML（可导入 draw.io / yEd）
mindmap.svg       SVG 树形布局预览（含关系虚线箭头）
evidence_map.json 证据映射（节点/关系 -> locator + 逐字 quote）
render_summary.json 渲染统计
并同步把 primary_artifacts 与 change_log 增量写回 result.json。
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

DEFAULTS = {"work": "/app/work", "output": "/app/output"}

# 节点类别 -> SVG 填充色（浅色配深字；root 用深色配白字）
KIND_FILL = {
    "topic": ("#1f4e79", "#ffffff"),
    "concept": ("#dbeafe", "#1e293b"),
    "question": ("#fee2e2", "#7f1d1d"),
    "method": ("#dcfce7", "#14532d"),
    "evidence": ("#fef9c3", "#713f12"),
    "conclusion": ("#fde68a", "#78350f"),
    "task": ("#e0e7ff", "#312e81"),
    "risk": ("#fecaca", "#7f1d1d"),
    "gap": ("#f3e8ff", "#581c87"),
    "milestone": ("#ffe4e6", "#881337"),
    "decision": ("#e2e8f0", "#0f172a"),
    "todo": ("#e0f2fe", "#0c4a6e"),
}
DEFAULT_FILL = ("#f1f5f9", "#0f172a")

REL_KINDS = {"causal", "dependency", "contrast", "temporal", "citation",
             "support", "conflict", "part-of", "is-a", "contain"}

BOX_W = 230.0
PAD = 7.0
FONT = 12.0
LINE_H = 16.0
COL_W = 265.0
LEVEL_STEP = 165.0
MARGIN = 40.0
MAX_LINES = 5


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def read_text(path):
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, OSError):
            continue
    return ""


def char_w(ch):
    return 14.0 if ord(ch) > 127 else 7.6


def wrap_text(text, max_px=BOX_W - 2 * PAD, max_lines=MAX_LINES):
    text = (text or "").strip() or "（未命名）"
    text = re.sub(r"\s+", " ", text)
    if len(text) > 120:
        text = text[:119] + "…"
    lines, cur, curw = [], "", 0.0
    for ch in text:
        cw = char_w(ch)
        if cur and curw + cw > max_px:
            lines.append(cur)
            cur, curw = ch, cw
        else:
            cur += ch
            curw += cw
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines - 1]
        last = lines[-1] + "…"
        # 截断到不超宽
        while last and sum(char_w(c) for c in last) > max_px:
            last = last[:-1]
        lines[-1] = last + "…" if last else "…"
    return lines or [""]


def normalize_spec(spec, title_fallback):
    """规范化 spec，返回 (root, nodes, relations, issues)。"""
    issues = []
    if not isinstance(spec, dict):
        return None, [], [], ["mindmap_spec.json 不是对象"]
    root = spec.get("root")
    if not isinstance(root, dict):
        root = {"id": "root", "text": spec.get("title") or title_fallback or "根主题",
                "kind": "topic", "note": "", "evidence": []}
    root.setdefault("id", "root")
    root.setdefault("text", spec.get("title") or title_fallback or "根主题")
    root.setdefault("kind", "topic")
    root.setdefault("note", "")
    root.setdefault("evidence", [])

    raw_nodes = spec.get("nodes", [])
    if not isinstance(raw_nodes, list):
        raw_nodes = []
        issues.append("nodes 不是列表，已按空处理")
    nodes = []
    seen = {"root"}
    for n in raw_nodes:
        if not isinstance(n, dict):
            issues.append("存在非对象节点，已跳过")
            continue
        nid = str(n.get("id", "")).strip()
        if not nid or nid in seen:
            issues.append("节点 id 缺失或重复，已跳过: %r" % n.get("id"))
            continue
        seen.add(nid)
        n.setdefault("text", nid)
        n.setdefault("kind", "concept")
        n.setdefault("note", "")
        n.setdefault("evidence", [])
        parent = str(n.get("parent") or "root")
        if parent not in seen:
            parent = "root"
        n["parent"] = parent
        nodes.append(n)

    raw_rel = spec.get("relations", [])
    if not isinstance(raw_rel, list):
        raw_rel = []
        issues.append("relations 不是列表，已按空处理")
    relations = []
    for r in raw_rel:
        if not isinstance(r, dict):
            continue
        frm, to = str(r.get("from", "")), str(r.get("to", ""))
        if frm not in seen or to not in seen:
            issues.append("关系端点不存在，已跳过: %s -> %s" % (frm, to))
            continue
        kind = str(r.get("kind", "support"))
        if kind not in REL_KINDS:
            kind = "support"
        relations.append({"from": frm, "to": to, "kind": kind,
                          "label": r.get("label") or "", "evidence": r.get("evidence") or []})
    return root, nodes, relations, issues


def build_children(root, nodes):
    children = {}
    for n in nodes:
        children.setdefault(n["parent"], []).append(n["id"])
    return children


def compute_layout(root, nodes, children):
    depth, x, y = {}, {}, {}
    leaf_counter = [0]

    def assign_depth(nid, d):
        depth[nid] = d
        for c in children.get(nid, []):
            assign_depth(c, d + 1)

    assign_depth("root", 0)

    def assign_x(nid):
        ch = children.get(nid, [])
        if not ch:
            x[nid] = float(leaf_counter[0])
            leaf_counter[0] += 1
            return x[nid]
        vals = [assign_x(c) for c in ch]
        x[nid] = sum(vals) / len(vals)
        return x[nid]

    assign_x("root")
    max_depth = max(depth.values()) if depth else 0
    n_leaves = max(leaf_counter[0], 1)
    for nid in depth:
        y[nid] = MARGIN + depth[nid] * LEVEL_STEP
    return depth, x, y, max_depth, n_leaves


def esc_xml(s):
    s = "" if s is None else str(s)
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&apos;"))


def esc_mmd(s):
    s = "" if s is None else str(s)
    for a, b in (("[", "【"), ("]", "】"), ("(", "（"), (")", "）"),
                 ("{{", "｛｛"), ("}}", "｝｝"), ("\n", " "), ("\r", " ")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def svg_escape(s):
    return esc_xml(s)


def text_lines_html(lines, xpos):
    out = []
    for i, ln in enumerate(lines):
        dy = 0.0 if i == 0 else LINE_H
        out.append('<tspan x="%.1f" dy="%.1f">%s</tspan>' % (xpos, dy, svg_escape(ln)))
    return "".join(out)


def render_svg(root, nodes, children, relations, depth, x, y, n_leaves, max_depth, title):
    boxes = {}
    for nid in depth:
        n = root if nid == "root" else next(n for n in nodes if n["id"] == nid)
        lines = wrap_text(n["text"])
        h = len(lines) * LINE_H + 2 * PAD
        boxes[nid] = {"lines": lines, "h": h,
                      "cx": MARGIN + x[nid] * COL_W + COL_W / 2.0,
                      "top": y[nid], "kind": n["kind"], "n": n}

    W = MARGIN * 2 + n_leaves * COL_W
    H = MARGIN * 2 + (max_depth + 1) * LEVEL_STEP + 40
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%.0f" height="%.0f" '
             'viewBox="0 0 %.0f %.0f" font-family="Noto Sans CJK SC, Microsoft YaHei, sans-serif">'
             % (W, H, W, H)]
    parts.append('<rect width="100%" height="100%" fill="#ffffff"/>')
    parts.append('<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" '
                 'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                 '<path d="M0,0 L10,5 L0,10 z" fill="#64748b"/></marker></defs>')
    parts.append('<text x="%.0f" y="28" font-size="15" font-weight="bold" fill="#0f172a">%s</text>'
                 % (MARGIN, svg_escape(title or "思维导图")))

    # 树边（实线）
    for nid in depth:
        p = None
        if nid != "root":
            node = next(n for n in nodes if n["id"] == nid)
            p = node["parent"]
        if p and p in boxes:
            pb, cb = boxes[p], boxes[nid]
            x1, y1 = pb["cx"], pb["top"] + pb["h"]
            x2, y2 = cb["cx"], cb["top"]
            parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                         'stroke="#94a3b8" stroke-width="1.2"/>' % (x1, y1, x2, y2))

    # 关系（虚线箭头）
    for r in relations:
        if r["from"] in boxes and r["to"] in boxes:
            fb, tb = boxes[r["from"]], boxes[r["to"]]
            x1, y1 = fb["cx"], fb["top"] + fb["h"] / 2
            x2, y2 = tb["cx"], tb["top"] + tb["h"] / 2
            parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                         'stroke="#64748b" stroke-width="1" stroke-dasharray="4,3" '
                         'marker-end="url(#arr)"/>' % (x1, y1, x2, y2))
            lab = r["kind"] + ((": " + r["label"]) if r["label"] else "")
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2 - 5
            parts.append('<text x="%.1f" y="%.1f" font-size="9" fill="#475569">%s</text>'
                         % (mx, my, svg_escape(lab)))

    # 节点盒
    for nid in depth:
        b = boxes[nid]
        fill, ink = KIND_FILL.get(b["kind"], DEFAULT_FILL)
        bx = b["cx"] - BOX_W / 2
        by = b["top"]
        parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="8" '
                     'fill="%s" stroke="#334155" stroke-width="1"/>'
                     % (bx, by, BOX_W, b["h"], fill))
        parts.append('<text x="%.1f" y="%.1f" font-size="%.1f" fill="%s">%s</text>'
                     % (bx + PAD, by + PAD + LINE_H * 0.75, FONT, ink,
                        text_lines_html(b["lines"], bx + PAD)))
    parts.append('</svg>')
    return "".join(parts)


def build_md(root, nodes, children, relations):
    lines = ["# %s" % (root["text"] or "思维导图"), ""]
    lines.append("> 生成方式：render_mindmap.py；本文件为可编辑大纲，证据以 ⟦locator⟧ 标注。")

    def emit(nid, indent, node):
        kind = node.get("kind", "concept")
        ev = " ".join("⟦%s⟧" % e.get("locator", "?") for e in node.get("evidence", []) if e.get("locator"))
        suffix = (" " + ev) if ev else ""
        lines.append("%s- [%s] %s%s" % ("  " * indent, kind, node.get("text", ""), suffix))
        for c in children.get(nid, []):
            cn = next(n for n in nodes if n["id"] == c)
            emit(c, indent + 1, cn)

    emit("root", 0, root)
    if relations:
        lines.append("")
        lines.append("## 关系")
        for r in relations:
            ev = " ".join("⟦%s⟧" % e.get("locator", "?") for e in r.get("evidence", []) if e.get("locator"))
            lab = ("（%s）" % r["label"]) if r.get("label") else ""
            lines.append("- %s → %s %s%s %s" % (r["from"], r["to"], r["kind"], lab, ev))
    lines.append("")
    return "\n".join(lines)


def build_mmd(root, nodes, children):
    # Mermaid mindmap 以文本为键，重复文本会导致节点合并，故重复时追加编号。
    counts = {}

    def safe_label(node):
        t = esc_mmd(node.get("text", "")) or "（未命名）"
        counts[t] = counts.get(t, 0) + 1
        if counts[t] > 1:
            t = "%s（%d）" % (t, counts[t])
        return t

    lines = ["mindmap"]
    root_lab = safe_label(root)

    def emit(nid, indent, node):
        lab = safe_label(node)
        lines.append("  " * indent + lab)
        for c in children.get(nid, []):
            cn = next(n for n in nodes if n["id"] == c)
            emit(c, indent + 1, cn)

    # 根节点用圆形（紧跟 mindmap 关键字之后）
    lines.append("  " + "((%s))" % root_lab)
    for c in children.get("root", []):
        cn = next(n for n in nodes if n["id"] == c)
        emit(c, 2, cn)
    return "\n".join(lines)


def build_graphml(root, nodes, children, relations):
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
             '  <key id="label" for="node" attr.name="label" attr.type="string"/>',
             '  <key id="kind" for="node" attr.name="kind" attr.type="string"/>',
             '  <key id="evidence" for="node" attr.name="evidence" attr.type="string"/>',
             '  <key id="ekind" for="edge" attr.name="kind" attr.type="string"/>',
             '  <key id="elabel" for="edge" attr.name="label" attr.type="string"/>',
             '  <key id="eevidence" for="edge" attr.name="evidence" attr.type="string"/>',
             '  <graph id="G" edgedefault="directed">']

    def node_evidence(node):
        evs = [("%s: %s" % (e.get("locator", "?"), (e.get("quote") or "")[:80]))
               for e in node.get("evidence", [])]
        return " | ".join(evs)

    parts.append('    <node id="root"><data key="label">%s</data>'
                 '<data key="kind">%s</data><data key="evidence">%s</data></node>'
                 % (esc_xml(root.get("text", "")), esc_xml(root.get("kind", "topic")),
                    esc_xml(node_evidence(root))))
    for n in nodes:
        parts.append('    <node id="%s"><data key="label">%s</data>'
                     '<data key="kind">%s</data><data key="evidence">%s</data></node>'
                     % (esc_xml(n["id"]), esc_xml(n.get("text", "")),
                        esc_xml(n.get("kind", "concept")), esc_xml(node_evidence(n))))
    for n in nodes:
        parts.append('    <edge source="%s" target="%s"><data key="ekind">part-of</data>'
                     '<data key="elabel"></data><data key="eevidence"></data></edge>'
                     % (esc_xml(n["parent"]), esc_xml(n["id"])))
    for r in relations:
        evs = [("%s: %s" % (e.get("locator", "?"), (e.get("quote") or "")[:80]))
               for e in r.get("evidence", [])]
        parts.append('    <edge source="%s" target="%s"><data key="ekind">%s</data>'
                     '<data key="elabel">%s</data><data key="eevidence">%s</data></edge>'
                     % (esc_xml(r["from"]), esc_xml(r["to"]), esc_xml(r["kind"]),
                        esc_xml(r.get("label", "")), esc_xml(" | ".join(evs))))
    parts.append("  </graph>")
    parts.append("</graphml>")
    return "\n".join(parts)


def build_evidence_map(case_id, root, nodes, relations):
    items = []
    if root.get("evidence"):
        for e in root["evidence"]:
            items.append({"ref": "node:root", "kind": root.get("kind", "topic"),
                          "locator": e.get("locator", ""), "quote": e.get("quote", "")})
    for n in nodes:
        for e in n.get("evidence", []):
            items.append({"ref": "node:%s" % n["id"], "kind": n.get("kind", "concept"),
                          "locator": e.get("locator", ""), "quote": e.get("quote", "")})
    for r in relations:
        for e in r.get("evidence", []):
            items.append({"ref": "relation:%s->%s" % (r["from"], r["to"]), "kind": r["kind"],
                          "locator": e.get("locator", ""), "quote": e.get("quote", "")})
    return {"case_id": case_id, "count": len(items), "items": items}


def update_result(output_dir, work_dir, summary):
    result_path = os.path.join(output_dir, "result.json")
    result = {}
    if os.path.exists(result_path):
        try:
            result = read_json(result_path)
        except Exception:
            result = {}
    if not isinstance(result, dict):
        result = {}
    result["primary_artifacts"] = summary["artifacts"]
    clog = result.get("change_log")
    if not isinstance(clog, list):
        clog = []
    clog.append({"time": now_iso(), "step": "render_mindmap",
                 "note": "生成 mindmap.md/.mmd/.graphml/.svg 与 evidence_map.json"})
    result["change_log"] = clog
    if result.get("status") in (None, "in_progress", "partial"):
        result["status"] = "partial"
    write_json(result_path, result)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--spec", default=None, help="mindmap spec JSON 路径（默认 /app/work/mindmap_spec.json）")
    ap.add_argument("--work", default=DEFAULTS["work"])
    ap.add_argument("--output", default=DEFAULTS["output"])
    args = ap.parse_args(argv)

    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)

    spec_path = args.spec or os.path.join(args.work, "mindmap_spec.json")
    manifest = {}
    manifest_path = os.path.join(args.work, "input_manifest.json")
    if os.path.exists(manifest_path):
        try:
            manifest = read_json(manifest_path)
        except Exception:
            manifest = {}
    case_id = manifest.get("case_id") or "unknown"
    title_fallback = manifest.get("title") or "思维导图"

    spec = {}
    if os.path.exists(spec_path):
        try:
            spec = read_json(spec_path)
        except Exception as exc:
            spec = {"title": title_fallback}
            print("spec 解析失败，降级为最小 spec: %s" % exc)

    root, nodes, relations, issues = normalize_spec(spec, title_fallback)
    title = (spec.get("title") if isinstance(spec, dict) else None) or root["text"]
    children = build_children(root, nodes)
    depth, x, y, max_depth, n_leaves = compute_layout(root, nodes, children)

    artifacts = []
    out = {}

    md = build_md(root, nodes, children, relations)
    p = os.path.join(args.output, "mindmap.md")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(md)
    artifacts.append({"name": "mindmap.md", "path": "mindmap.md", "format": "markdown",
                      "editable": True})
    out["mindmap_md"] = "mindmap.md"

    mmd = build_mmd(root, nodes, children)
    p = os.path.join(args.output, "mindmap.mmd")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(mmd)
    artifacts.append({"name": "mindmap.mmd", "path": "mindmap.mmd", "format": "mermaid",
                      "editable": True})
    out["mindmap_mmd"] = "mindmap.mmd"

    gml = build_graphml(root, nodes, children, relations)
    p = os.path.join(args.output, "mindmap.graphml")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(gml)
    artifacts.append({"name": "mindmap.graphml", "path": "mindmap.graphml", "format": "graphml",
                      "editable": True})
    out["mindmap_graphml"] = "mindmap.graphml"

    svg = render_svg(root, nodes, children, relations, depth, x, y, n_leaves, max_depth, title)
    p = os.path.join(args.output, "mindmap.svg")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(svg)
    artifacts.append({"name": "mindmap.svg", "path": "mindmap.svg", "format": "svg",
                      "editable": True})
    out["mindmap_svg"] = "mindmap.svg"

    em = build_evidence_map(case_id, root, nodes, relations)
    p = os.path.join(args.output, "evidence_map.json")
    write_json(p, em)
    artifacts.append({"name": "evidence_map.json", "path": "evidence_map.json",
                      "format": "json", "editable": True})
    out["evidence_map"] = "evidence_map.json"

    summary = {
        "case_id": case_id,
        "title": title,
        "node_count": 1 + len(nodes),
        "relation_count": len(relations),
        "evidence_count": em["count"],
        "max_depth": max_depth,
        "issues": issues,
        "artifacts": artifacts,
        "rendered_at": now_iso(),
    }
    write_json(os.path.join(args.work, "render_summary.json"), summary)
    update_result(args.output, args.work, summary)

    print(json.dumps({"case_id": case_id, "nodes": summary["node_count"],
                      "relations": summary["relation_count"],
                      "evidence": summary["evidence_count"],
                      "issues": len(issues)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
