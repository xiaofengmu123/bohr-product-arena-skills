# -*- coding: utf-8 -*-
"""Deterministic mind-map renderer: spec JSON -> OPML + Markdown + SVG.

Standard library only. The spec is written by the harness agent after
semantic extraction; this module only does deterministic formatting and
layout. It never adds, removes, or invents node content.

Spec schema (JSON):
{
  "meta": {"title": str, "purpose": str, "audience": str},
  "nodes": [
    {"id": "n0", "label": "根节点", "parent": null,
     "kind": "topic|concept|question|method|evidence|conclusion|task|risk|reference",
     "evidence": {"locator": "...", "quote": "...", "source": "...", "kind": "fact|summary|inference"}}
  ],
  "relations": [
    {"from": "n1", "to": "n2",
     "type": "causal|dependency|contrast|temporal|support|conflict|citation",
     "evidence": {"locator": "...", "quote": "..."}}
  ]
}
"""

import json
import os
import sys
import xml.etree.ElementTree as ET

KINDS = {
    "topic", "concept", "question", "method", "evidence",
    "conclusion", "task", "risk", "reference",
}
RELATION_TYPES = {
    "causal", "dependency", "contrast", "temporal", "support", "conflict", "citation",
}

KIND_COLORS = {
    "topic": ("#e8f0fe", "#1a73e8"),
    "concept": ("#e6f4ea", "#188038"),
    "question": ("#fef7e0", "#b06000"),
    "method": ("#e0f2f1", "#00796b"),
    "evidence": ("#fce8e6", "#c5221f"),
    "conclusion": ("#f3e8fd", "#7627bb"),
    "task": ("#fff3e0", "#e37400"),
    "risk": ("#fde7e9", "#b3261e"),
    "reference": ("#f1f3f4", "#5f6368"),
}


def _esc(s):
    """Escape XML/HTML special characters."""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;"))


def load_spec(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    last_err = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, ValueError) as e:
            last_err = e
    raise ValueError("cannot decode spec json: %s" % last_err)


def validate_spec(spec):
    """Return a list of problem dicts (empty list means valid)."""
    problems = []
    if not isinstance(spec, dict):
        return [{"severity": "error", "check": "spec is object", "detail": "not a dict"}]
    nodes = spec.get("nodes")
    relations = spec.get("relations", [])
    if not isinstance(nodes, list) or not nodes:
        problems.append({"severity": "error", "check": "nodes non-empty list", "detail": "missing"})
        return problems
    ids = []
    seen = set()
    for n in nodes:
        if not isinstance(n, dict):
            problems.append({"severity": "error", "check": "node is object", "detail": str(n)[:60]})
            continue
        nid = n.get("id")
        label = n.get("label")
        if not nid or not str(nid).strip():
            problems.append({"severity": "error", "check": "node.id", "detail": "missing/empty"})
        if label is None or not str(label).strip():
            problems.append({"severity": "error", "check": "node.label", "detail": "missing/empty id=%s" % nid})
        if nid in seen:
            problems.append({"severity": "error", "check": "node.id unique", "detail": "duplicate %s" % nid})
        seen.add(nid)
        ids.append(nid)
        k = n.get("kind")
        if k and k not in KINDS:
            problems.append({"severity": "warn", "check": "node.kind", "detail": "unknown %r" % k})
    id_set = set(ids)
    for n in nodes:
        p = n.get("parent")
        if p not in (None, "") and p not in id_set:
            problems.append({"severity": "error", "check": "node.parent exists", "detail": "%s -> %s" % (n.get("id"), p)})
    # Cycle check on parent links.
    parent = {n["id"]: n.get("parent") for n in nodes if isinstance(n, dict)}
    def has_cycle():
        for start in id_set:
            seen_chain = set()
            cur = start
            while cur in parent and parent.get(cur):
                if cur in seen_chain:
                    return True
                seen_chain.add(cur)
                cur = parent[cur]
        return False
    if has_cycle():
        problems.append({"severity": "error", "check": "parent tree acyclic", "detail": "cycle detected"})
    for r in relations:
        if not isinstance(r, dict):
            problems.append({"severity": "error", "check": "relation object", "detail": str(r)[:60]})
            continue
        f = r.get("from")
        t = r.get("to")
        if f not in id_set or t not in id_set:
            problems.append({"severity": "error", "check": "relation endpoints", "detail": "%s->%s" % (f, t)})
        if r.get("type") and r.get("type") not in RELATION_TYPES:
            problems.append({"severity": "warn", "check": "relation.type", "detail": "unknown %r" % r.get("type")})
    return problems


def _children_map(nodes, id_set):
    children = {}
    roots = []
    for n in nodes:
        p = n.get("parent")
        nid = n["id"]
        if p in id_set:
            children.setdefault(p, []).append(nid)
        else:
            roots.append(nid)
    return children, roots


def render_opml(spec):
    """Render an OPML 2.0 document (importable by XMind/MindManager/FreeMind)."""
    nodes = [n for n in spec.get("nodes", []) if isinstance(n, dict)]
    id_set = {n["id"] for n in nodes}
    children, roots = _children_map(nodes, id_set)
    opml = ET.Element("opml", {"version": "2.0"})
    head = ET.SubElement(opml, "head")
    title = ET.SubElement(head, "title")
    meta = spec.get("meta") or {}
    title.text = meta.get("title") or "mind-map"
    body = ET.SubElement(opml, "body")

    def add(parent_el, nid):
        n = next(x for x in nodes if x["id"] == nid)
        attrs = {"text": n.get("label", "")}
        notes = []
        k = n.get("kind")
        if k:
            notes.append("kind=%s" % k)
        ev = n.get("evidence") or {}
        if ev.get("locator"):
            notes.append("locator=%s" % ev["locator"])
        if ev.get("quote"):
            notes.append("quote=%s" % ev["quote"])
        if notes:
            attrs["_note"] = " | ".join(notes)
        el = ET.SubElement(parent_el, "outline", attrs)
        for c in children.get(nid, []):
            add(el, c)

    for r in roots:
        add(body, r)
    xml = ET.tostring(opml, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml


def render_markdown(spec):
    """Render a human-readable Markdown outline plus relations and evidence."""
    nodes = [n for n in spec.get("nodes", []) if isinstance(n, dict)]
    id_set = {n["id"] for n in nodes}
    by_id = {n["id"]: n for n in nodes}
    children, roots = _children_map(nodes, id_set)
    meta = spec.get("meta") or {}
    lines = ["# " + (meta.get("title") or "思维导图")]
    if meta.get("purpose"):
        lines.append("")
        lines.append("> 用途：%s" % meta["purpose"])
    if meta.get("audience"):
        lines.append("> 读者：%s" % meta["audience"])
    lines.append("")

    def emit(nid, depth):
        n = by_id[nid]
        prefix = "  " * depth + "- "
        tag = ("[" + n["kind"] + "] ") if n.get("kind") else ""
        ev = n.get("evidence") or {}
        loc = ("（%s）" % ev["locator"]) if ev.get("locator") else ""
        lines.append(prefix + tag + n.get("label", "") + loc)
        for c in children.get(nid, []):
            emit(c, depth + 1)

    for r in roots:
        emit(r, 0)

    relations = spec.get("relations") or []
    if relations:
        lines.append("")
        lines.append("## 关系")
        for r in relations:
            ev = r.get("evidence") or {}
            loc = ("（%s）" % ev["locator"]) if ev.get("locator") else ""
            lines.append("- %s --%s--> %s%s" % (r.get("from"), r.get("type") or "related", r.get("to"), loc))

    evidence_flat = []
    for n in nodes:
        ev = n.get("evidence") or {}
        if ev.get("locator") or ev.get("quote"):
            evidence_flat.append((n.get("id"), n.get("label"), ev))
    for r in relations:
        ev = r.get("evidence") or {}
        if ev.get("locator") or ev.get("quote"):
            evidence_flat.append((r.get("from") + "->" + r.get("to"), r.get("type"), ev))
    if evidence_flat:
        lines.append("")
        lines.append("## 证据")
        for key, label, ev in evidence_flat:
            lines.append("- %s（%s）" % (key, label))
            if ev.get("locator"):
                lines.append("  - 定位：%s" % ev["locator"])
            if ev.get("quote"):
                lines.append("  - 原文：%s" % ev["quote"])
    return "\n".join(lines) + "\n"


def _text_px(s, per_cjk=13, per_ascii=7):
    w = 0
    for ch in s:
        w += per_cjk if ord(ch) > 127 else per_ascii
    return w


def _fit_label(label, max_px=224):
    s = str(label)
    if _text_px(s) <= max_px:
        return _esc(s)
    out = []
    w = 0
    for ch in s:
        cw = 13 if ord(ch) > 127 else 7
        if w + cw > max_px - 16:
            break
        out.append(ch)
        w += cw
    return _esc("".join(out) + "…")


def render_svg(spec):
    """Render a deterministic top-down tree layout as editable SVG."""
    nodes = [n for n in spec.get("nodes", []) if isinstance(n, dict)]
    id_set = {n["id"] for n in nodes}
    by_id = {n["id"]: n for n in nodes}
    children, roots = _children_map(nodes, id_set)
    if not roots and id_set:
        roots = [next(iter(id_set))]

    pos = {}
    counter = [0]

    def assign(nid, depth):
        ch = children.get(nid, [])
        if not ch:
            pos[nid] = (float(counter[0]), depth)
            counter[0] += 1
            return
        for c in ch:
            assign(c, depth + 1)
        xs = [pos[c][0] for c in ch]
        pos[nid] = (sum(xs) / len(xs), depth)

    for r in roots:
        assign(r, 0)

    box_w, box_h = 244.0, 48.0
    col_w = box_w + 64.0
    row_h = 96.0
    margin = 40.0
    max_col = max((c for c, _ in pos.values()), default=0)
    max_depth = max((d for _, d in pos.values()), default=0)
    width = margin * 2 + (max_col + 1) * col_w
    height = margin * 2 + (max_depth + 1) * row_h + 40

    def center_x(col):
        return margin + col * col_w + box_w / 2.0

    def top_y(depth):
        return margin + depth * row_h

    parts = ['<?xml version="1.0" encoding="UTF-8"?>']
    parts.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
                 'viewBox="0 0 %d %d" font-family="sans-serif">' % (width, height, width, height))
    parts.append('<rect width="100%%" height="100%%" fill="#ffffff"/>')
    meta = spec.get("meta") or {}
    parts.append('<text x="%d" y="24" font-size="16" font-weight="bold" fill="#202124">%s</text>'
                 % (int(margin), _esc(meta.get("title") or "思维导图")))

    # Edges from parents to children.
    for nid, (col, depth) in pos.items():
        for cid in children.get(nid, []):
            if cid in pos:
                cc, cd = pos[cid]
                parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                             'stroke="#bdc1c6" stroke-width="1.5"/>'
                             % (center_x(col), top_y(depth) + box_h,
                                center_x(cc), top_y(cd)))

    # Cross-links (relations) as dashed curves.
    for r in spec.get("relations") or []:
        f, t = r.get("from"), r.get("to")
        if f in pos and t in pos:
            fc, fd = pos[f]
            tc, td = pos[t]
            x1, y1 = center_x(fc), top_y(fd) + box_h / 2
            x2, y2 = center_x(tc), top_y(td) + box_h / 2
            mid = ((x1 + x2) / 2, (y1 + y2) / 2 - 24)
            parts.append('<path d="M %.1f %.1f Q %.1f %.1f %.1f %.1f" fill="none" '
                         'stroke="#ea8600" stroke-width="1.2" stroke-dasharray="4 3"/>'
                         % (x1, y1, mid[0], mid[1], x2, y2))
            parts.append('<text x="%.1f" y="%.1f" font-size="10" fill="#ea8600">%s</text>'
                         % (mid[0], mid[1] - 4, _esc(r.get("type") or "related")))

    # Nodes.
    for nid, (col, depth) in pos.items():
        n = by_id[nid]
        kind = n.get("kind") or "topic"
        fill, stroke = KIND_COLORS.get(kind, KIND_COLORS["topic"])
        x = margin + col * col_w
        y = top_y(depth)
        label = _fit_label(n.get("label", ""))
        parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="8" '
                     'fill="%s" stroke="%s" stroke-width="1.4"/>' % (x, y, box_w, box_h, fill, stroke))
        parts.append('<text x="%.1f" y="%.1f" font-size="13" fill="#202124">%s</text>'
                     % (x + 12, y + 30, label))

    # Legend.
    lx = margin
    ly = height - 24
    items = list(KIND_COLORS.items())
    legend = []
    for k, (fill, stroke) in items:
        legend.append('<rect x="%d" y="%d" width="10" height="10" rx="2" fill="%s" stroke="%s"/>'
                      '<text x="%d" y="%d" font-size="10" fill="#5f6368">%s</text>'
                      % (lx, ly - 8, fill, stroke, lx + 14, ly, _esc(k)))
        lx += 14 + 8 * max(len(k), 4) + 26
    if legend:
        parts.append('<g>%s</g>' % "".join(legend))
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def render_all(spec):
    """Return dict {opml, markdown, svg} of rendered strings."""
    return {
        "opml": render_opml(spec),
        "markdown": render_markdown(spec),
        "svg": render_svg(spec),
    }


if __name__ == "__main__":
    # Executable self-test with a tiny inline example (illustrative only).
    demo = {
        "meta": {"title": "示例", "purpose": "self-test", "audience": "self"},
        "nodes": [
            {"id": "n0", "label": "根", "parent": None, "kind": "topic"},
            {"id": "n1", "label": "问题", "parent": "n0", "kind": "question",
             "evidence": {"locator": "demo.md#p1", "quote": "示例引文"}},
            {"id": "n2", "label": "方法", "parent": "n0", "kind": "method"},
        ],
        "relations": [{"from": "n1", "to": "n2", "type": "dependency"}],
    }
    problems = validate_spec(demo)
    if problems:
        print(json.dumps(problems, ensure_ascii=False))
        sys.exit(1)
    out = render_all(demo)
    for k in ("opml", "markdown", "svg"):
        print("== %s ==" % k)
        print(out[k])
    sys.exit(0)
