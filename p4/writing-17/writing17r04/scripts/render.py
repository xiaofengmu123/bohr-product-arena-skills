#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render.py — validate a mind-map spec and render editable, renderable outputs.

Input: a JSON spec file describing a tree of nodes plus optional cross-relations.
Output (written next to --output dir, or alongside the spec when --output omitted):
  mindmap.md    Markdown nested outline (human editable, readable everywhere)
  mindmap.mmd   Mermaid "mindmap" source (text editable, widely renderable)
  mindmap.svg   deterministic SVG tree layout (editable vector + renderable)
  mindmap.json  canonical, re-importable structure with computed metadata

The layout is deterministic: the same spec always produces the same SVG.

Spec schema:
{
  "title": "root label",
  "root": "n0",
  "nodes": [
    {"id": "n0", "label": "...", "type": "root", "parent": null,
     "evidence": {"locator": "materials/paper.md#p3", "quote": "verbatim text"}},
    {"id": "n1", "label": "...", "type": "question", "parent": "n0"}
  ],
  "edges": [
    {"from": "n1", "to": "n2", "rel": "supports",
     "evidence": {"locator": "...", "quote": "..."}}
  ]
}

Standard library only.
"""
import argparse
import json
import os
import sys
from xml.sax.saxutils import escape

NODE_TYPES = {
    "root", "concept", "question", "hypothesis", "method", "material",
    "evidence", "result", "conclusion", "task", "risk", "gap",
    "limitation", "decision", "other",
}
REL_TYPES = {
    "part_of", "causal", "depends_on", "supports", "contradicts",
    "contrasts", "cites", "temporal", "compares", "example_of", "other",
}

# deterministic palette per node type
COLORS = {
    "root": "#1f3864",
    "concept": "#2e75b6",
    "question": "#7030a0",
    "hypothesis": "#bf9000",
    "method": "#2f8f6b",
    "material": "#7f7f7f",
    "evidence": "#548235",
    "result": "#0b6e99",
    "conclusion": "#c00000",
    "task": "#e36c09",
    "risk": "#c55a11",
    "gap": "#8c8c8c",
    "limitation": "#9c4a2a",
    "decision": "#5b2c86",
    "other": "#555555",
}

WRAP = 26          # characters per SVG text line
VGAP = 96          # vertical pixels per depth
LEAF_GAP = 150     # horizontal pixels between adjacent leaves
MARGIN = 60        # canvas margin
FONT = 13


def read_text(path):
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, ValueError, OSError):
            continue
    return None


def load_spec(path):
    text = read_text(path)
    if text is None:
        raise ValueError("cannot read spec: %s" % path)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ValueError("invalid JSON in spec: %s" % exc)


def validate_spec(spec):
    """Return (nodes_by_id, children, warnings). Raise ValueError on fatal errors."""
    if not isinstance(spec, dict):
        raise ValueError("spec root must be an object")
    if not isinstance(spec.get("title"), str) or not spec["title"].strip():
        raise ValueError("spec.title is required and must be a non-empty string")
    nodes = spec.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("spec.nodes must be a non-empty list")
    root = spec.get("root")
    warnings = []

    by_id = {}
    for n in nodes:
        if not isinstance(n, dict) or not isinstance(n.get("id"), str) or not n["id"]:
            raise ValueError("every node needs a non-empty string id")
        if n["id"] in by_id:
            raise ValueError("duplicate node id: %s" % n["id"])
        if not isinstance(n.get("label"), str) or not n["label"].strip():
            raise ValueError("node %s needs a non-empty label" % n["id"])
        t = n.get("type", "concept")
        if t not in NODE_TYPES:
            warnings.append("node %s has unknown type %r (using 'concept')" % (n["id"], t))
            n["type"] = "concept"
        by_id[n["id"]] = n

    if root not in by_id:
        raise ValueError("spec.root %r not found in nodes" % (root,))

    children = {nid: [] for nid in by_id}
    for n in nodes:
        p = n.get("parent")
        if n["id"] == root:
            if p not in (None, ""):
                raise ValueError("root node %s must have null parent" % root)
            continue
        if not p or p not in by_id:
            raise ValueError("node %s has unknown parent %r" % (n["id"], p))
        children[p].append(n["id"])

    # cycle / reachability check
    depths = {root: 0}
    queue = [root]
    order = [root]
    while queue:
        cur = queue.pop(0)
        for c in children.get(cur, []):
            if c in depths:
                raise ValueError("node %s has multiple parents or a cycle" % c)
            depths[c] = depths[cur] + 1
            queue.append(c)
            order.append(c)
    for nid in by_id:
        if nid not in depths:
            raise ValueError("node %s is not reachable from root %s" % (nid, root))

    edges = spec.get("edges", [])
    if edges is None:
        edges = []
    if not isinstance(edges, list):
        raise ValueError("spec.edges must be a list")
    for e in edges:
        if not isinstance(e, dict):
            raise ValueError("each edge must be an object")
        frm, to = e.get("from"), e.get("to")
        if frm not in by_id or to not in by_id:
            raise ValueError("edge references unknown node: %r -> %r" % (frm, to))
        if frm == to:
            raise ValueError("self-loop edge on %s" % frm)
        r = e.get("rel", "other")
        if r not in REL_TYPES:
            warnings.append("edge %s->%s has unknown rel %r (using 'other')" % (frm, to, r))
            e["rel"] = "other"

    return by_id, children, order, depths, edges, warnings


def wrap_text(text, width=WRAP):
    """Wrap text into lines of at most `width` characters (no word breaking unless
    a single token exceeds the width)."""
    words = text.split()
    lines = []
    cur = ""
    for w in words:
        if len(w) > width:
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(w)
            continue
        cand = (cur + " " + w).strip()
        if len(cand) <= width:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def compute_positions(root, children):
    """Deterministic tidy-tree layout: leaves get sequential x, parents get the
    mean of their children's x, y = depth * VGAP. Returns {id: (x, y)}."""
    leaf_counter = [0]
    leaf_x = {}

    def assign_leaves(nid):
        ch = children.get(nid, [])
        if not ch:
            leaf_x[nid] = leaf_counter[0]
            leaf_counter[0] += 1
            return
        for c in ch:
            assign_leaves(c)

    assign_leaves(root)
    if leaf_counter[0] == 0:  # single root with no children
        leaf_x[root] = 0
        leaf_counter[0] = 1

    depths = {root: 0}
    queue = [root]
    order = [root]
    while queue:
        cur = queue.pop(0)
        for c in children.get(cur, []):
            depths[c] = depths[cur] + 1
            queue.append(c)
            order.append(c)

    x = {}

    def compute_x(nid):
        ch = children.get(nid, [])
        if not ch:
            x[nid] = float(leaf_x[nid])
            return x[nid]
        xs = [compute_x(c) for c in ch]
        x[nid] = sum(xs) / len(xs)
        return x[nid]

    compute_x(root)
    pos = {}
    max_depth = max(depths.values()) if depths else 0
    for nid in order:
        pos[nid] = (x[nid], depths[nid])
    return pos, leaf_counter[0], max_depth


def render_markdown(spec, by_id, children, order, edges):
    lines = []
    lines.append("# %s" % spec["title"].strip())
    lines.append("")
    lines.append("> Generated by render.py from `mindmap.json`. Node types shown in")
    lines.append("> brackets; evidence locators follow each fact-bearing node.")
    lines.append("")

    def emit(nid, depth):
        n = by_id[nid]
        indent = "  " * depth
        tag = " [%s]" % n.get("type", "concept")
        lines.append("%s- **%s**%s" % (indent, n["label"].strip(), tag))
        ev = n.get("evidence")
        if isinstance(ev, dict) and ev.get("locator"):
            q = ev.get("quote", "")
            if q:
                lines.append("%s  - source: `%s` — \"%s\"" % (indent, ev["locator"], q))
            else:
                lines.append("%s  - source: `%s`" % (indent, ev["locator"]))
        for c in children.get(nid, []):
            emit(c, depth + 1)

    emit(spec["root"], 0)

    if edges:
        lines.append("")
        lines.append("## Cross-relations")
        lines.append("")
        for e in edges:
            frm = by_id[e["from"]]["label"].strip()
            to = by_id[e["to"]]["label"].strip()
            ev = e.get("evidence", {})
            loc = ev.get("locator", "") if isinstance(ev, dict) else ""
            suffix = (" (source: `%s`)" % loc) if loc else ""
            lines.append("- %s --%s--> %s%s" % (frm, e.get("rel", "other"), to, suffix))

    # evidence appendix
    ev_lines = []
    for nid in order:
        n = by_id[nid]
        ev = n.get("evidence")
        if isinstance(ev, dict) and ev.get("quote"):
            ev_lines.append("- **%s** `[%s]` @ `%s`: %s"
                           % (n["label"].strip(), nid, ev.get("locator", "?"), ev["quote"]))
    for e in edges:
        ev = e.get("evidence", {})
        if isinstance(ev, dict) and ev.get("quote"):
            ev_lines.append("- **edge %s->%s** `[%s]` @ `%s`: %s"
                           % (e["from"], e["to"], e.get("rel", "other"),
                              ev.get("locator", "?"), ev["quote"]))
    if ev_lines:
        lines.append("")
        lines.append("## Evidence appendix (verbatim quotes)")
        lines.append("")
        lines.extend(ev_lines)

    return "\n".join(lines) + "\n"


def render_mermaid(spec, by_id, children):
    lines = ["mindmap", "  root((%s))" % _mermaid_text(spec["title"].strip())]

    def emit(nid, depth):
        n = by_id[nid]
        indent = "  " * (depth + 1)
        label = _mermaid_text(n["label"].strip())
        lines.append("%s%s" % (indent, label))
        for c in children.get(nid, []):
            emit(c, depth + 1)

    for c in children.get(spec["root"], []):
        emit(c, 1)
    return "\n".join(lines) + "\n"


def _mermaid_text(s):
    # strip characters that break Mermaid node text
    for ch in "()[]{}<>:\"#":
        s = s.replace(ch, " ")
    return " ".join(s.split())


def render_svg(spec, by_id, children, order, edges, pos, n_leaves, max_depth, warnings):
    width = max(1, n_leaves) * LEAF_GAP + 2 * MARGIN
    height = max_depth * VGAP + 2 * MARGIN + 40
    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append(
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" font-family="sans-serif">' % (width, height, width, height)
    )
    parts.append(
        '<rect x="0" y="0" width="%d" height="%d" fill="#ffffff"/>' % (width, height)
    )
    parts.append('<title>%s</title>' % escape(spec["title"].strip()))

    # tree edges first (under nodes)
    for nid in order:
        p = by_id[nid].get("parent")
        if not p:
            continue
        x1, y1 = pos[p]
        x2, y2 = pos[nid]
        px = MARGIN + x1 * LEAF_GAP
        py = MARGIN + y1 * VGAP
        cx = MARGIN + x2 * LEAF_GAP
        cy = MARGIN + y2 * VGAP
        parts.append(
            '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
            'stroke="#9aa5b1" stroke-width="1.5"/>' % (px, py + 20, cx, cy - 20)
        )

    # cross-relations as dashed curves with labels
    for e in edges:
        x1, y1 = pos[e["from"]]
        x2, y2 = pos[e["to"]]
        px1 = MARGIN + x1 * LEAF_GAP
        py1 = MARGIN + y1 * VGAP
        px2 = MARGIN + x2 * LEAF_GAP
        py2 = MARGIN + y2 * VGAP
        midx = (px1 + px2) / 2.0
        midy = (py1 + py2) / 2.0
        parts.append(
            '<path d="M %.1f %.1f C %.1f %.1f, %.1f %.1f, %.1f %.1f" '
            'fill="none" stroke="#c00000" stroke-width="1.2" stroke-dasharray="4,3"/>'
            % (px1, py1, midx, py1, midx, py2, px2, py2)
        )
        parts.append(
            '<text x="%.1f" y="%.1f" font-size="10" fill="#c00000" '
            'text-anchor="middle">%s</text>'
            % (midx, midy, escape(e.get("rel", "other")))
        )

    # nodes
    for nid in order:
        n = by_id[nid]
        x, y = pos[nid]
        cx = MARGIN + x * LEAF_GAP
        cy = MARGIN + y * VGAP
        fill = COLORS.get(n.get("type", "concept"), COLORS["other"])
        lines = wrap_text(n["label"].strip())
        box_h = max(30, 20 * len(lines) + 12)
        box_w = 2 * WRAP * 6 + 10  # approx width per char
        bx = cx - box_w / 2.0
        by = cy - box_h / 2.0
        is_root = (nid == spec["root"])
        rx = 12 if is_root else 6
        parts.append(
            '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="%.1f" '
            'fill="%s" stroke="#333333" stroke-width="%s"/>'
            % (bx, by, box_w, box_h, rx, fill, "2" if is_root else "1")
        )
        ty = by + (box_h - 16 * len(lines)) / 2.0 + 12
        for i, ln in enumerate(lines):
            parts.append(
                '<text x="%.1f" y="%.1f" font-size="%d" fill="#ffffff" '
                'text-anchor="middle" font-weight="%s">%s</text>'
                % (cx, ty + i * 16, FONT, "bold" if is_root else "normal", escape(ln))
            )

    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main(argv):
    ap = argparse.ArgumentParser(description="Render a mind-map spec to editable outputs.")
    ap.add_argument("--spec", required=True, help="path to mindmap JSON spec")
    ap.add_argument("--output", default=None, help="output directory (default: spec dir)")
    args = ap.parse_args(argv)

    spec_path = os.path.abspath(args.spec)
    if not os.path.isfile(spec_path):
        print("error: spec not found: %s" % args.spec, file=sys.stderr)
        return 1

    out_dir = os.path.abspath(args.output) if args.output else os.path.dirname(spec_path)
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        print("error: cannot create output dir: %s" % exc, file=sys.stderr)
        return 1

    try:
        spec = load_spec(spec_path)
        by_id, children, order, depths, edges, warnings = validate_spec(spec)
    except ValueError as exc:
        print("error: invalid spec: %s" % exc, file=sys.stderr)
        return 1

    pos, n_leaves, max_depth = compute_positions(spec["root"], children)

    md = render_markdown(spec, by_id, children, order, edges)
    mmd = render_mermaid(spec, by_id, children)
    svg = render_svg(spec, by_id, children, order, edges, pos, n_leaves,
                     max_depth, warnings)

    files = {
        "mindmap.md": md,
        "mindmap.mmd": mmd,
        "mindmap.svg": svg,
    }
    for name, content in files.items():
        p = os.path.join(out_dir, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(content)

    canonical = {
        "title": spec["title"],
        "root": spec["root"],
        "nodes": spec["nodes"],
        "edges": edges,
        "metadata": {
            "node_count": len(by_id),
            "edge_count": len(edges),
            "max_depth": max_depth,
            "breadth": n_leaves,
            "warnings": warnings,
        },
    }
    with open(os.path.join(out_dir, "mindmap.json"), "w", encoding="utf-8") as fh:
        json.dump(canonical, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(json.dumps({
        "status": "ok" if not warnings else "partial",
        "node_count": len(by_id),
        "edge_count": len(edges),
        "max_depth": max_depth,
        "warnings": warnings,
        "files": sorted(files.keys()) + ["mindmap.json"],
        "output_dir": out_dir,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
