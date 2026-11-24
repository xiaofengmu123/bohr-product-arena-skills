#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalize, validate and render a source-grounded mind map (stdlib only).

Input : a draft JSON describing the tree plus cross-relations, see
        references/rules.md for the exact schema. The draft is produced by the
        calling agent after reading the actual source materials.
Output: mindmap.md (editable outline), mindmap.json (normalized structure),
        mindmap.mm (FreeMind XML, importable by FreeMind/XMind), mindmap.svg
        (vector preview), and an updated result.json that fills in the
        original output_contract fields plus honest status/evidence records.

Deterministic: same draft -> same artifacts. No network, no third-party
packages. It never invents facts; every claim it renders must already carry a
locator+quote in the draft, otherwise it is flagged as unverified.
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

ALLOWED_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")
NODE_KINDS = {
    "concept", "question", "method", "evidence", "conclusion", "task",
    "risk", "gap", "limitation", "decision", "milestone", "fact", "unknown",
}
RELATIONS = {
    "causal", "dependency", "contrast", "temporal", "citation", "support",
    "conflict", "unknown",
}
PROVENANCE = {"fact", "summary", "inference", "unknown"}
# Fields always computed by the Skill; the draft's contract echo must not clobber them.
INTERNAL_KEYS = {
    "case_id", "status", "title", "artifacts", "evidence_map", "sources",
    "uncertainty", "change_log", "errors", "degradations", "pending_human",
    "limitations", "runtime", "output_contract",
}
REL_LABEL = {
    "causal": "导致", "dependency": "依赖", "contrast": "对比",
    "temporal": "先后", "citation": "引用", "support": "支持",
    "conflict": "冲突", "unknown": "关系",
}
KIND_LABEL = {
    "concept": "概念", "question": "问题", "method": "方法",
    "evidence": "证据", "conclusion": "结论", "task": "任务",
    "risk": "风险", "gap": "空白", "limitation": "局限",
    "decision": "决策", "milestone": "里程碑", "fact": "事实",
    "unknown": "未分类",
}
KIND_COLOR = {
    "concept": "#2c6fbb", "question": "#7b3fa0", "method": "#0e8a6d",
    "evidence": "#3a7d44", "conclusion": "#b26a00", "task": "#4a6fa5",
    "risk": "#c0392b", "gap": "#8a8a8a", "limitation": "#a05c2c",
    "decision": "#1f6f8b", "milestone": "#5b6b1a", "fact": "#3a7d44",
    "unknown": "#555555",
}
REL_COLOR = {
    "causal": "#c0392b", "dependency": "#2c6fbb", "contrast": "#7b3fa0",
    "temporal": "#0e8a6d", "citation": "#1f6f8b", "support": "#3a7d44",
    "conflict": "#c0392b", "unknown": "#888888",
}


def utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _stdout(text):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text)


def norm(s):
    """Normalize a label for matching relations to nodes."""
    if s is None:
        return ""
    return "".join(str(s).split()).casefold()


def _clean_evidence(ev):
    """Return a normalized evidence dict or None."""
    if not isinstance(ev, dict):
        return None
    loc = (ev.get("locator") or "").strip()
    quote = (ev.get("quote") or "").strip()
    prov = (ev.get("provenance") or "unknown").strip().lower()
    if prov not in PROVENANCE:
        prov = "unknown"
    if not loc and not quote:
        return None
    return {"locator": loc, "quote": quote, "provenance": prov}


def _clean_kind(kind):
    k = (kind or "unknown").strip().lower()
    return k if k in NODE_KINDS else "unknown"


def _clean_relation(rel):
    r = (rel or "unknown").strip().lower()
    return r if r in RELATIONS else "unknown"


def load_draft(path):
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        return None, [f"cannot read draft {path}: {e}"]
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            obj = json.loads(data.decode(enc))
            return obj, []
        except UnicodeDecodeError:
            continue
        except json.JSONDecodeError as e:
            return None, [f"draft is not valid JSON: {e}"]
    return None, ["draft has undecodable encoding"]


def _walk(node, parent, depth, nodes, order):
    """Assign deterministic ids and flatten the nested tree."""
    if not isinstance(node, dict) or "label" not in node:
        return None
    idx = len(nodes)
    nid = "n%d" % (idx + 1)
    label = str(node.get("label") or "").strip()
    if not label:
        label = "(未命名)"
    children_raw = node.get("children") or []
    evidence = [_clean_evidence(e) for e in (node.get("evidence") or [])]
    evidence = [e for e in evidence if e]
    rec = {
        "id": nid,
        "label": label,
        "kind": _clean_kind(node.get("kind")),
        "evidence": evidence,
        "confidence": node.get("confidence"),
        "note": (node.get("note") or "").strip(),
        "parent": parent,
        "depth": depth,
        "children": [],
    }
    nodes.append(rec)
    order.append(nid)
    for c in children_raw:
        child = _walk(c, nid, depth + 1, nodes, order)
        if child is not None:
            rec["children"].append(child["id"])
    return rec


def build_tree(nodes):
    """Return the root record (first node) with child lists resolved."""
    by_id = {n["id"]: n for n in nodes}
    for n in nodes:
        n["_children"] = [by_id[c] for c in n["children"] if c in by_id]
    return nodes[0] if nodes else None


def resolve_relations(rel_raw, nodes):
    """Match relations (by label or id) to node ids; return resolved + warnings."""
    resolved, warnings = [], []
    by_norm, by_id = {}, {n["id"]: n for n in nodes}
    for n in nodes:
        by_norm.setdefault(norm(n["label"]), []).append(n)
    for r in (rel_raw or []):
        if not isinstance(r, dict):
            continue
        frm, to = r.get("from"), r.get("to")
        src = _resolve_one(frm, by_norm, by_id, warnings)
        dst = _resolve_one(to, by_norm, by_id, warnings)
        if src is None or dst is None or src["id"] == dst["id"]:
            continue
        resolved.append({
            "from": src["id"], "to": dst["id"],
            "from_label": src["label"], "to_label": dst["label"],
            "relation": _clean_relation(r.get("relation")),
            "evidence": [_clean_evidence(e) for e in (r.get("evidence") or [])],
            "confidence": r.get("confidence"),
            "note": (r.get("note") or "").strip(),
        })
    return resolved, warnings


def _resolve_one(ref, by_norm, by_id, warnings):
    if ref is None:
        return None
    if isinstance(ref, dict):
        ref = ref.get("id") or ref.get("label")
    s = str(ref).strip()
    if not s:
        return None
    if s in by_id:
        return by_id[s]
    matches = by_norm.get(norm(s), [])
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        warnings.append("关系端点标签重复，无法唯一匹配：%s" % s)
        return None
    warnings.append("关系端点未在节点中找到：%s" % s)
    return None


def wrap_label(text, max_chars=22):
    text = str(text or "").strip() or "(未命名)"
    lines, cur = [], ""
    for ch in text:
        cur += ch
        if len(cur) >= max_chars:
            lines.append(cur)
            cur = ""
    if cur:
        lines.append(cur)
    return lines or [""]


def _char_w(ch):
    return 13.0 if ord(ch) > 0x2E80 else 7.4


def text_width(text):
    return sum(_char_w(c) for c in text)


def box_h(text):
    lines = wrap_label(text)
    return 22 + 17 * len(lines)


def box_w(text):
    lines = wrap_label(text)
    return max(18, int(max(text_width(l) for l in lines)) + 16)


# ---------------- Markdown ----------------
def render_markdown(title, root, nodes, relations, uncertainty):
    out = []
    out.append("# %s" % (title or "思维导图"))
    out.append("")
    out.append("> 来源与证据见文末「证据映射」；[事实]/[概括]/[推断] 表示出处类型。")
    out.append("")

    def rec(node, depth):
        ev = node["evidence"]
        tag = {"fact": "事实", "summary": "概括", "inference": "推断",
               "unknown": "未注明"}.get(ev[0]["provenance"] if ev else "unknown", "未注明")
        kind = KIND_LABEL.get(node["kind"], node["kind"])
        line = "%s- **%s**（%s）[%s]" % ("  " * depth, node["label"], kind, tag)
        if ev:
            line += "  ← %s" % ev[0]["locator"]
        out.append(line)
        for c in node["_children"]:
            rec(c, depth + 1)

    rec(root, 0)
    if relations:
        out.append("")
        out.append("## 关系（跨层级）")
        for r in relations:
            label = REL_LABEL.get(r["relation"], r["relation"])
            ev = "；".join(e["locator"] for e in r["evidence"]) or "无证据"
            out.append("- %s --[%s]--> %s（证据：%s）"
                       % (r["from_label"], label, r["to_label"], ev))
    out.append("")
    out.append("## 证据映射")
    seen = set()
    for n in nodes:
        for e in n["evidence"]:
            key = (n["id"], e["locator"], e["quote"])
            if key in seen:
                continue
            seen.add(key)
            out.append("- [%s] %s「%s」" % (n["id"], e["locator"], e["quote"]))
    for r in relations:
        for e in r["evidence"]:
            key = (r["from"] + "->" + r["to"], e["locator"], e["quote"])
            if key in seen:
                continue
            seen.add(key)
            out.append("- [%s->%s %s] %s「%s」" % (r["from"], r["to"],
                                                   REL_LABEL.get(r["relation"]), e["locator"], e["quote"]))
    out.append("")
    out.append("## 不确定性与缺口")
    if uncertainty:
        for u in uncertainty:
            if isinstance(u, dict):
                out.append("- %s" % (u.get("detail") or u.get("item") or str(u)))
            else:
                out.append("- %s" % str(u))
    else:
        out.append("- （未声明）")
    out.append("")
    return "\n".join(out)


# ---------------- FreeMind ----------------
def render_freemind(title, root, relations):
    lines = ['<map version="1.0.1">']
    lines.append('  <node ID="root" TEXT=%s>' % quoteattr(title or "思维导图"))

    def rec(node, depth):
        pad = "    " * depth
        lines.append('%s<node ID=%s TEXT=%s>' % (pad, quoteattr(node["id"]),
                                                 quoteattr(node["label"])))
        note_bits = []
        if node["kind"] != "unknown":
            note_bits.append("类型:%s" % KIND_LABEL.get(node["kind"], node["kind"]))
        for e in node["evidence"]:
            note_bits.append("证据[%s] %s: %s" % (e["provenance"], e["locator"], e["quote"]))
        if note_bits:
            lines.append('%s  <richcontent TYPE="NOTE"><html><body><p>%s</p></body></html></richcontent>'
                         % (pad, escape("<br/>".join(note_bits))))
        for c in node["_children"]:
            rec(c, depth + 1)
        lines.append('%s</node>' % pad)

    # FreeMind cross-links go inside their source node.
    by_id = {n["id"]: n for n in _flatten(root)}
    rel_lines = {}
    for r in relations:
        src = r["from"]
        rel_lines.setdefault(src, []).append(r)

    def rec2(node, depth):
        pad = "    " * depth
        lines.append('%s<node ID=%s TEXT=%s>' % (pad, quoteattr(node["id"]),
                                                 quoteattr(node["label"])))
        note_bits = []
        if node["kind"] != "unknown":
            note_bits.append("类型:%s" % KIND_LABEL.get(node["kind"], node["kind"]))
        for e in node["evidence"]:
            note_bits.append("证据[%s] %s: %s" % (e["provenance"], e["locator"], e["quote"]))
        if note_bits:
            lines.append('%s  <richcontent TYPE="NOTE"><html><body><p>%s</p></body></html></richcontent>'
                         % (pad, escape("<br/>".join(note_bits))))
        for r in rel_lines.get(node["id"], []):
            label = REL_LABEL.get(r["relation"], r["relation"])
            lines.append('%s  <arrowlink DESTINATION=%s COLOR=%s ENDARROW="Default" '
                         'TYPE="Linear">%s</arrowlink>'
                         % (pad, quoteattr(r["to"]), quoteattr(REL_COLOR[r["relation"]]),
                            escape(label)))
        for c in node["_children"]:
            rec2(c, depth + 1)
        lines.append('%s</node>' % pad)

    rec2(root, 2)
    # Relation legend as root note.
    if relations:
        legend = ["关系图例"] + ["%s -> %s [%s] %s" % (
            r["from_label"], r["to_label"], REL_LABEL.get(r["relation"]), r["relation"])
            for r in relations]
        lines.insert(2, '  <richcontent TYPE="NOTE"><html><body><p>%s</p></body></html></richcontent>'
                     % escape("<br/>".join(legend)))
    lines.append('  </node>')
    lines.append('</map>')
    return "\n".join(lines)


def _flatten(node):
    out = [node]
    for c in node["_children"]:
        out.extend(_flatten(c))
    return out


# ---------------- SVG ----------------
def _measure(node):
    G = 14
    h = box_h(node["label"])
    ch = node["_children"]
    if not ch:
        node["_h"] = h
        return h
    total = sum(_measure(c) for c in ch) + G * (len(ch) - 1)
    node["_h"] = max(h, total)
    return node["_h"]


def _place(node, y_top, dx):
    G = 14
    h = box_h(node["label"])
    ch = node["_children"]
    if not ch:
        node["_cy"] = y_top + h / 2.0
        return y_top + h
    span = sum(c["_h"] for c in ch) + G * (len(ch) - 1)
    y = y_top + (node["_h"] - span) / 2.0
    for c in ch:
        _place(c, y, dx)
        y += c["_h"] + G
    node["_cy"] = (ch[0]["_cy"] + ch[-1]["_cy"]) / 2.0
    return y_top + node["_h"]


def render_svg(title, root, relations):
    DX = 190
    margin = 30
    nodes = _flatten(root)
    for i, n in enumerate(nodes):
        n["_x"] = margin + n["depth"] * DX
    total_h = _measure(root)
    _place(root, margin, DX)
    max_x = max(n["_x"] + box_w(n["label"]) for n in nodes)
    width = int(max_x + margin + 120)
    height = int(total_h + 2 * margin)
    svg = ['<?xml version="1.0" encoding="UTF-8"?>']
    svg.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
               'viewBox="0 0 %d %d" font-family="sans-serif">' % (width, height, width, height))
    svg.append('<rect width="100%" height="100%" fill="#ffffff"/>')
    svg.append('<text x="%d" y="20" font-size="18" font-weight="bold" fill="#222">%s</text>'
               % (margin, escape(title or "思维导图")))

    def draw_edge(a, b, color="#9aa0a6", dash=None, arrow=False, width_=1.5):
        x0, y0 = a["_x"] + box_w(a["label"]), a["_cy"]
        x1, y1 = b["_x"], b["_cy"]
        mx = (x0 + x1) / 2.0
        d = 'M %s %s C %s %s, %s %s, %s %s' % (x0, y0, mx, y0, mx, y1, x1, y1)
        dash_attr = ' stroke-dasharray="5,4"' if dash else ''
        svg.append('<path d="%s" fill="none" stroke="%s" stroke-width="%s"%s/>'
                   % (d, color, width_, dash_attr))
        if arrow:
            svg.append('<polygon points="%s,%s %s,%s %s,%s" fill="%s"/>'
                       % (x1 - 8, y1 - 4, x1, y1, x1 - 8, y1 + 4, color))

    for n in nodes:
        for c in n["_children"]:
            draw_edge(n, c)

    for n in nodes:
        lines = wrap_label(n["label"])
        w, h = box_w(n["label"]), box_h(n["label"])
        x, y = n["_x"], n["_cy"] - h / 2.0
        color = KIND_COLOR.get(n["kind"], KIND_COLOR["unknown"])
        svg.append('<rect x="%s" y="%s" width="%s" height="%s" rx="6" fill="%s" '
                   'fill-opacity="0.12" stroke="%s" stroke-width="1.3"/>'
                   % (x, y, w, h, color, color))
        ty = y + 15
        for ln in lines:
            svg.append('<text x="%s" y="%s" font-size="13" fill="#1a1a1a">%s</text>'
                       % (x + 8, ty, escape(ln)))
            ty += 17
        if n["evidence"]:
            svg.append('<circle cx="%s" cy="%s" r="3" fill="%s" title="有证据"/>'
                       % (x + w - 9, y + 9, color))

    by_id = {n["id"]: n for n in nodes}
    for r in relations:
        a, b = by_id.get(r["from"]), by_id.get(r["to"])
        if not a or not b:
            continue
        color = REL_COLOR.get(r["relation"], REL_COLOR["unknown"])
        draw_edge(a, b, color=color, dash=True, arrow=True, width_=1.4)
        mx = (a["_x"] + box_w(a["label"]) + b["_x"]) / 2.0
        my = (a["_cy"] + b["_cy"]) / 2.0 - 4
        svg.append('<text x="%s" y="%s" font-size="11" fill="%s">%s</text>'
                   % (mx, my, color, escape(REL_LABEL.get(r["relation"], r["relation"]))))

    # legend
    ly = height - 10
    lx = margin
    for k, label in sorted(KIND_LABEL.items(), key=lambda kv: kv[1]):
        if k == "unknown":
            continue
        color = KIND_COLOR[k]
        svg.append('<rect x="%s" y="%s" width="9" height="9" rx="2" fill="%s" fill-opacity="0.25" stroke="%s"/>'
                   % (lx, ly - 9, color, color))
        svg.append('<text x="%s" y="%s" font-size="10" fill="#444">%s</text>'
                   % (lx + 12, ly, escape(label)))
        lx += 12 + text_width(label) * 0.72 + 22
    svg.append('</svg>')
    return "\n".join(svg)


# ---------------- evidence map ----------------
def build_evidence_map(nodes, relations):
    out = []
    for n in nodes:
        for e in n["evidence"]:
            out.append({"target": "node", "node_id": n["id"],
                        "node_label": n["label"], "locator": e["locator"],
                        "quote": e["quote"], "provenance": e["provenance"]})
    for r in relations:
        for e in r["evidence"]:
            out.append({"target": "relation", "from": r["from"], "to": r["to"],
                        "relation": r["relation"], "locator": e["locator"],
                        "quote": e["quote"], "provenance": e["provenance"]})
    return out


def _png_via_converter(svg_path, png_path):
    for cmd in ("rsvg-convert", "inkscape", "magick", "convert"):
        exe = shutil.which(cmd)
        if not exe:
            continue
        try:
            if cmd == "rsvg-convert":
                subprocess.run([exe, "-o", str(png_path), str(svg_path)],
                               check=True, timeout=120, capture_output=True)
            elif cmd == "inkscape":
                subprocess.run([exe, str(svg_path), "--export-filename", str(png_path)],
                               check=True, timeout=120, capture_output=True)
            else:
                subprocess.run([exe, "-density", "120", str(svg_path), str(png_path)],
                               check=True, timeout=120, capture_output=True)
            if png_path.exists() and png_path.stat().st_size > 0:
                return True
        except Exception:
            continue
    return False


def load_result(outdir):
    rp = Path(outdir) / "result.json"
    if rp.exists():
        try:
            return json.loads(rp.read_text(encoding="utf-8-sig"))
        except Exception:
            pass
    return {"case_id": "unknown", "status": "partial", "sources": [],
            "uncertainty": [], "change_log": [], "errors": [],
            "degradations": [], "pending_human": [], "limitations": [],
            "artifacts": {}, "output_contract": {}, "runtime": {}}


def save_result(outdir, result):
    rp = Path(outdir) / "result.json"
    tmp = rp.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(rp)


def finalize(draft_obj, outdir, draft_path=None):
    errors, warnings = [], []
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    if not isinstance(draft_obj, dict):
        return {"status": "input_error",
                "errors": ["draft is not a JSON object"], "warnings": warnings}

    title = str(draft_obj.get("title") or "").strip() or "思维导图"
    nodes, order = [], []
    root = _walk(draft_obj.get("root") or {"label": title}, None, 0, nodes, order)
    if root is None or not nodes:
        errors.append("draft 缺少 root 节点，无法渲染")
        return {"status": "input_error", "errors": errors, "warnings": warnings}
    build_tree(nodes)
    tree_root = nodes[0]

    relations, rel_warnings = resolve_relations(draft_obj.get("relations"), nodes)
    warnings.extend(rel_warnings)

    sources = draft_obj.get("sources") or []
    uncertainty = draft_obj.get("uncertainty") or []

    md = render_markdown(title, tree_root, nodes, relations, uncertainty)
    mm = render_freemind(title, tree_root, relations)
    svg = render_svg(title, tree_root, relations)
    evidence_map = build_evidence_map(nodes, relations)

    artifacts = {
        "mindmap_md": "mindmap.md",
        "mindmap_json": "mindmap.json",
        "mindmap_mm": "mindmap.mm",
        "mindmap_svg": "mindmap.svg",
        "mindmap_png": None,
    }
    (outdir / "mindmap.md").write_text(md, encoding="utf-8")
    normalized = {
        "title": title,
        "node_count": len(nodes),
        "relation_count": len(relations),
        "nodes": nodes,
        "relations": relations,
        "sources": sources,
        "uncertainty": uncertainty,
        "generated_at": utcnow(),
    }
    (outdir / "mindmap.json").write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "mindmap.mm").write_text(mm, encoding="utf-8")
    (outdir / "mindmap.svg").write_text(svg, encoding="utf-8")

    png = outdir / "mindmap.png"
    if _png_via_converter(outdir / "mindmap.svg", png):
        artifacts["mindmap_png"] = "mindmap.png"
    else:
        warnings.append("未找到可用的 SVG 光栅化工具，未生成 PNG；SVG 为矢量预览，可正常查看")

    # status resolution
    draft_status = (draft_obj.get("status") or "").strip().lower()
    if draft_status in ALLOWED_STATUS:
        status = draft_status
    elif not evidence_map and not nodes:
        status = "insufficient_evidence"
    elif all((n["evidence"] for n in nodes)) and not errors:
        status = "ok"
    else:
        status = "partial"

    result = load_result(outdir)
    result["case_id"] = str(draft_obj.get("case_id") or result.get("case_id") or "unknown")
    result["status"] = status
    result["title"] = title
    result.setdefault("artifacts", {}).update(artifacts)
    result["evidence_map"] = evidence_map
    result["sources"] = sources if sources else result.get("sources", [])
    result["uncertainty"] = uncertainty if uncertainty else result.get("uncertainty", [])
    # echo output_contract fields provided by the draft; never overwrite the
    # fields computed above (evidence_map, sources, uncertainty, ...).
    contract = draft_obj.get("contract") or {}
    if isinstance(contract, dict):
        result.setdefault("output_contract", {}).update(contract)
        for k, v in contract.items():
            if k not in INTERNAL_KEYS:
                result[k] = v
    for e in errors:
        result.setdefault("errors", []).append(e)
    for w in warnings:
        result.setdefault("degradations", []).append(w)
    if draft_obj.get("pending_human"):
        result.setdefault("pending_human", []).extend(
            draft_obj.get("pending_human") if isinstance(draft_obj.get("pending_human"), list)
            else [str(draft_obj.get("pending_human"))])
    if draft_obj.get("limitations"):
        result.setdefault("limitations", []).extend(
            draft_obj.get("limitations") if isinstance(draft_obj.get("limitations"), list)
            else [str(draft_obj.get("limitations"))])
    change = {"at": utcnow(), "action": "mindmap_rendered",
              "detail": "nodes=%d relations=%d" % (len(nodes), len(relations))}
    result.setdefault("change_log", []).append(change)
    runtime = result.setdefault("runtime", {})
    runtime["finished_at"] = utcnow()
    runtime["seconds"] = round(
        time.time() - float(runtime.get("_t0") or time.time()), 1)
    runtime.pop("_t0", None)
    save_result(outdir, result)

    return {"status": status, "errors": errors, "warnings": warnings,
            "artifacts": artifacts, "result": result}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Render a source-grounded mind map.")
    ap.add_argument("--draft", required=True, help="draft JSON produced by the agent")
    ap.add_argument("--output", default="/app/output", help="output directory")
    ap.add_argument("--case-id", default=None, help="optional case id override")
    args = ap.parse_args(argv)
    draft, errs = load_draft(args.draft)
    if draft is None:
        _stdout("FATAL: " + "; ".join(errs))
        return 2
    if args.case_id:
        draft["case_id"] = args.case_id
    report = finalize(draft, args.output, draft_path=args.draft)
    _stdout("status=%s errors=%d warnings=%d artifacts=%s"
            % (report["status"], len(report.get("errors", [])),
               len(report.get("warnings", [])),
               ", ".join(k for k, v in report.get("artifacts", {}).items() if v)))
    for e in report.get("errors", []):
        _stdout("ERROR: " + e)
    for w in report.get("warnings", []):
        _stdout("WARN: " + w)
    return 0 if report["status"] != "input_error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
