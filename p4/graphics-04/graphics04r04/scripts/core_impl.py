#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — 证据绑定科研绘图的核心实现（纯标准库，确定性，不编造）。

设计原则：
1. 确定性：同一输入、同一顺序 => 同一输出（固定调色板、固定网格布局、无随机源）。
2. 证据优先：每个图元必须携带 evidence {locator, quote, status} 与 confidence。
3. 不编造：evidence.status in {missing, not_reported} 或 confidence=low 的图元，
   渲染为虚线边框并标记，绝不写成既定事实。
4. 降级诚实：任何失败只降级、只记录，不伪造成功状态。

对外函数（供 main.py / selftest.py 调用）：
  now_iso, read_text, load_json, write_json,
  discover_input, make_skeleton, normalize_ir,
  build_svg, validate, merge_result, finalize_result
"""
from __future__ import annotations

import datetime
import html
import json
import math
import os
import sys
import xml.etree.ElementTree as ET

ENCODING = "utf-8-sig"
MAX_FILE_BYTES = 2 * 1024 * 1024
SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

EVIDENCE_STATUSES = {"verbatim", "paraphrase", "inferred", "missing", "conflicting", "not_reported"}
CONFIDENCES = {"high", "medium", "low"}
STATUSES = {"in_progress", "complete", "partial", "insufficient_evidence", "input_error", "failed"}
FLOW_KINDS = {"process_flow", "technical_route", "mechanism"}

# 确定性调色板（entity type -> fill）。缺省用 default。
TYPE_FILL = {
    "vessel": "#dbeafe", "reactor": "#dbeafe", "tank": "#dbeafe", "column": "#dbeafe",
    "source": "#dcfce7", "reservoir": "#dcfce7", "feed": "#dcfce7",
    "sink": "#fee2e2", "waste": "#fee2e2", "outlet": "#fee2e2", "product": "#fee2e2",
    "instrument": "#fef9c3", "sensor": "#fef9c3", "gauge": "#fef9c3",
    "pump": "#e0e7ff", "valve": "#f3e8ff", "filter": "#fce7f3", "mixer": "#ccfbf1",
    "heater": "#fed7aa", "cooler": "#cffafe",
    "stage": "#dbeafe", "milestone": "#fde68a", "step": "#e2e8f0",
    "decision": "#ffedd5", "input": "#dcfce7", "output": "#fee2e2",
    "default": "#f1f5f9",
}

# 关系 kind -> 线型/颜色；dash 为空表示实线。
KIND_STYLE = {
    "flow": {"stroke": "#334155", "dash": ""},
    "material_flow": {"stroke": "#334155", "dash": ""},
    "connect": {"stroke": "#334155", "dash": ""},
    "sequence": {"stroke": "#1d4ed8", "dash": ""},
    "dependency": {"stroke": "#7c3aed", "dash": "7,4"},
    "return": {"stroke": "#b91c1c", "dash": "5,4"},
    "feedback": {"stroke": "#b91c1c", "dash": "5,4"},
    "energy_flow": {"stroke": "#d97706", "dash": "2,3"},
    "info_flow": {"stroke": "#0e7490", "dash": "1,4"},
    "default": {"stroke": "#334155", "dash": ""},
}

KIND_LABEL = {
    "flow": "物料/流体", "material_flow": "物料/流体", "connect": "连接",
    "sequence": "步骤顺序", "dependency": "依赖", "return": "回流/循环",
    "feedback": "反馈", "energy_flow": "能量流", "info_flow": "信息流",
}


# ---------------------------------------------------------------------------
# 基础 IO
# ---------------------------------------------------------------------------
def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def read_text(path, cap=MAX_FILE_BYTES):
    """UTF-8(-sig) 读取，坏编码容错，超限跳过并记录。返回 (text|None, error|None)。"""
    p = os.path.abspath(path)
    if not os.path.exists(p):
        return None, {"error": "missing_file", "path": p}
    try:
        if os.path.getsize(p) > cap:
            return None, {"error": "file_too_large", "path": p, "cap": cap}
    except OSError as e:
        return None, {"error": "stat_failed", "path": p, "detail": str(e)}
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(p, "r", encoding=enc) as f:
                return f.read(), None
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError as e:
            return None, {"error": "read_failed", "path": p, "detail": str(e)}
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            return f.read(), {"error": "bad_encoding_fallback", "path": p}
    except OSError as e:
        return None, {"error": "read_failed", "path": p, "detail": str(e)}


def load_json(path):
    text, err = read_text(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, {"error": "bad_json", "path": path, "detail": str(e)}


def write_json(path, obj):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return os.path.abspath(path)


def discover_input(input_dir):
    """扫描输入目录，不假设固定文件名/键名。"""
    found = {"case_json": None, "task_md": None, "materials": []}
    if not os.path.isdir(input_dir):
        return found, {"error": "input_dir_missing", "path": input_dir}
    for root, _dirs, files in os.walk(input_dir):
        for fn in sorted(files):
            full = os.path.join(root, fn)
            low = fn.lower()
            if low == "case.json":
                found["case_json"] = found["case_json"] or full
            elif low in ("task.md", "task.txt"):
                found["task_md"] = found["task_md"] or full
            elif low.endswith((".md", ".txt", ".json", ".csv", ".pdf", ".png", ".jpg", ".jpeg")):
                found["materials"].append(full)
    found["materials"].sort()
    return found, None


# ---------------------------------------------------------------------------
# result.json 骨架与增量更新
# ---------------------------------------------------------------------------
def _log(action, detail=""):
    return {"ts": now_iso(), "action": action, "detail": detail}


def make_skeleton(case_id, title=None, kind=None):
    return {
        "case_id": case_id,
        "status": "in_progress",
        "kind": kind or "apparatus",
        "title": title or "",
        "entities": [],
        "relations": [],
        "data_encoding": [],
        "legend_annotations": [],
        "editable_sources": [],
        "exports": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "degradations": [],
        "errors": [],
        "checks": [],
        "change_log": [_log("init", "result.json 骨架落盘（in_progress）")],
        "runtime": {"started_at": now_iso(), "finished_at": None},
        "artifacts": [],
    }


def _merge_obj(dst, src):
    if isinstance(dst, dict) and isinstance(src, dict):
        for k, v in src.items():
            if k in dst:
                dst[k] = _merge_obj(dst[k], v)
            else:
                dst[k] = v
        return dst
    return src


def merge_result(result_path, partial):
    """把 partial（dict）浅合并进既有 result.json；列表字段整体替换。"""
    cur, err = load_json(result_path)
    if err:
        cur = {}
    merged = _merge_obj(cur, partial)
    if "change_log" not in merged or not isinstance(merged["change_log"], list):
        merged["change_log"] = []
    merged["change_log"].append(_log("merge"))
    write_json(result_path, merged)
    return merged


def finalize_result(result_path, status, checks=None, artifacts=None, extra=None):
    cur, err = load_json(result_path)
    if err:
        cur = make_skeleton("unknown")
    if status not in STATUSES:
        status = "partial"
    cur["status"] = status
    if isinstance(cur.get("runtime"), dict):
        cur["runtime"]["finished_at"] = now_iso()
    if isinstance(checks, list):
        cur["checks"] = checks
    if isinstance(artifacts, list):
        cur["artifacts"] = artifacts
    if isinstance(extra, dict):
        cur = _merge_obj(cur, extra)
    cur["change_log"] = cur.get("change_log") or []
    cur["change_log"].append(_log("finalize", status))
    write_json(result_path, cur)
    return cur


# ---------------------------------------------------------------------------
# IR 规范化
# ---------------------------------------------------------------------------
def _norm_evidence(ev):
    if not isinstance(ev, dict):
        ev = {}
    status = ev.get("status")
    if status not in EVIDENCE_STATUSES:
        status = "missing"
    return {
        "locator": ev.get("locator") or "",
        "quote": ev.get("quote") or "",
        "status": status,
    }


def normalize_ir(ir):
    if not isinstance(ir, dict):
        ir = {}
    kind = ir.get("kind") or "apparatus"
    entities = []
    for i, e in enumerate(ir.get("entities", [])):
        if not isinstance(e, dict):
            continue
        eid = e.get("id") or ("e%d" % (i + 1))
        entities.append({
            "id": eid,
            "label": e.get("label") or eid,
            "type": e.get("type") or "default",
            "role": e.get("role") or "",
            "x": e.get("x"),
            "y": e.get("y"),
            "confidence": e.get("confidence") if e.get("confidence") in CONFIDENCES else "high",
            "evidence": _norm_evidence(e.get("evidence")),
            "note": e.get("note") or "",
        })
    relations = []
    for i, r in enumerate(ir.get("relations", [])):
        if not isinstance(r, dict):
            continue
        relations.append({
            "id": r.get("id") or ("r%d" % (i + 1)),
            "from": r.get("from"),
            "to": r.get("to"),
            "kind": r.get("kind") or "connect",
            "direction": r.get("direction") if r.get("direction") in ("forward", "backward", "bidirectional") else "forward",
            "label": r.get("label") or "",
            "confidence": r.get("confidence") if r.get("confidence") in CONFIDENCES else "high",
            "evidence": _norm_evidence(r.get("evidence")),
        })
    annotations = []
    for a in ir.get("annotations", []):
        if isinstance(a, dict):
            annotations.append({
                "id": a.get("id", "a%d" % (len(annotations) + 1)),
                "text": a.get("text") or "",
                "x": a.get("x"), "y": a.get("y"),
                "source_ref": a.get("source_ref") or (a.get("evidence") or {}).get("locator", "") if isinstance(a.get("evidence"), dict) else "",
                "confidence": a.get("confidence") if a.get("confidence") in CONFIDENCES else "high",
            })
    out = {
        "case_id": ir.get("case_id") or "unknown",
        "kind": kind,
        "title": ir.get("title") or "",
        "entities": entities,
        "relations": relations,
        "annotations": annotations,
        "data_encoding": ir.get("data_encoding") if isinstance(ir.get("data_encoding"), list) else [],
        "legend": ir.get("legend") if isinstance(ir.get("legend"), list) else [],
    }
    return out


# ---------------------------------------------------------------------------
# 确定性布图与 SVG 生成
# ---------------------------------------------------------------------------
def _text_w(txt):
    n = sum(2 if ord(c) > 127 else 1 for c in txt)
    return max(110, n * 9 + 28)


def layout_entities(entities, kind):
    n = len(entities)
    pos = {}
    if n == 0:
        return pos
    cols = 1 if kind in FLOW_KINDS else max(1, int(math.ceil(math.sqrt(n))))
    rows = max(1, int(math.ceil(n / float(cols))))
    for i, e in enumerate(entities):
        r, c = divmod(i, cols)
        x = e.get("x") if isinstance(e.get("x"), (int, float)) else 40 + c * 250
        y = e.get("y") if isinstance(e.get("y"), (int, float)) else 40 + r * 96
        pos[e["id"]] = {"x": x, "y": y, "w": _text_w(e["label"]), "h": 52}
    return pos


def _canvas(pos, legend_count):
    if not pos:
        return 640, 240 + 24 * legend_count + 24
    max_x = max(p["x"] + p["w"] for p in pos.values())
    max_y = max(p["y"] + p["h"] for p in pos.values())
    legend_h = (24 * legend_count + 28) if legend_count else 0
    return max(640, int(max_x + 60)), int(max_y + 60 + legend_h)


def _border_point(rect, tx, ty):
    cx = rect["x"] + rect["w"] / 2.0
    cy = rect["y"] + rect["h"] / 2.0
    dx, dy = tx - cx, ty - cy
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return cx, cy
    hw, hh = rect["w"] / 2.0, rect["h"] / 2.0
    sx = hw / abs(dx) if dx != 0 else float("inf")
    sy = hh / abs(dy) if dy != 0 else float("inf")
    s = min(sx, sy)
    return cx + dx * s, cy + dy * s


def _attrs(**kw):
    parts = []
    for k, v in kw.items():
        if v is None or v == "":
            continue
        parts.append('%s="%s"' % (k, html.escape(str(v), quote=True)))
    return " " + " ".join(parts) if parts else ""


def _marker_id(kind, end):
    return "arr-%s-%s" % (kind, end)


def _render_relation(r, pos, by_id):
    f, t = r.get("from"), r.get("to")
    rf, rt = pos.get(f), pos.get(t)
    kind = r.get("kind") or "connect"
    style_key = kind if kind in KIND_STYLE else "default"
    style = KIND_STYLE[style_key]
    dash = style["dash"]
    src = r["evidence"]["locator"]
    conf = r["confidence"]
    stroke = style["stroke"]
    extra_attrs = _attrs(stroke=stroke, **({"stroke-dasharray": dash} if dash else {}))
    if not rf or not rt:
        # 断头关系也要渲染成可见并标记，便于人工发现
        return ('<g id="rel-%s" data-source-ref="%s" data-confidence="%s" data-dangling="true">'
                '<text x="10" y="10" fill="#b91c1c" font-size="11">[断头关系 %s]</text></g>'
                % (html.escape(r["id"]), html.escape(src), conf, html.escape(r["id"])))
    x1, y1 = _border_point(rf, rt["x"] + rt["w"] / 2.0, rt["y"] + rt["h"] / 2.0)
    x2, y2 = _border_point(rt, rf["x"] + rf["w"] / 2.0, rf["y"] + rf["h"] / 2.0)
    d = r.get("direction", "forward")
    if f == t:
        # 自环
        path = ('M %d %d C %d %d %d %d %d %d'
                % (x1, y1, x1 + 40, y1 - 40, x1 + 40, y1 + 40, x1 + 6, y1 + 6))
    else:
        path = 'M %.1f %.1f L %.1f %.1f' % (x1, y1, x2, y2)
    marker = ""
    if d == "forward":
        marker = _attrs(**{"marker-end": "url(#%s)" % _marker_id(style_key, "end")})
    elif d == "backward":
        marker = _attrs(**{"marker-start": "url(#%s)" % _marker_id(style_key, "start")})
    else:
        marker = _attrs(**{"marker-end": "url(#%s)" % _marker_id(style_key, "end"),
                           "marker-start": "url(#%s)" % _marker_id(style_key, "start")})
    label = ""
    if r.get("label"):
        mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0 - 6
        label = ('<text x="%.1f" y="%.1f" font-size="11" fill="#1f2937" text-anchor="middle" '
                 'data-source-ref="%s" data-confidence="%s">%s</text>'
                 % (mx, my, html.escape(src), conf, html.escape(r["label"])))
    return ('<g id="rel-%s" data-source-ref="%s" data-confidence="%s">'
            '<path d="%s" fill="none"%s%s />%s</g>'
            % (html.escape(r["id"]), html.escape(src), conf, path, extra_attrs, marker, label))


def _render_entity(e, pos):
    p = pos.get(e["id"])
    if not p:
        return '<g id="ent-%s"></g>' % html.escape(e["id"])
    fill = TYPE_FILL.get(e.get("type"), TYPE_FILL["default"])
    src = e["evidence"]["locator"]
    conf = e["confidence"]
    low = conf == "low" or e["evidence"]["status"] in ("missing", "not_reported")
    stroke = "#b45309" if low else "#475569"
    dash = _attrs(**{"stroke-dasharray": "6,3"}) if low else ""
    rect = ('<rect x="%d" y="%d" width="%d" height="%d" rx="6" fill="%s" stroke="%s" stroke-width="1.5"%s '
            'data-source-ref="%s" data-confidence="%s" />'
            % (int(p["x"]), int(p["y"]), int(p["w"]), int(p["h"]), fill, stroke, dash,
               html.escape(src), conf))
    # 折行标签
    label = e["label"]
    text = ('<text x="%.1f" y="%.1f" font-size="12" fill="#111827" text-anchor="middle" '
            'dominant-baseline="middle" data-source-ref="%s" data-confidence="%s">%s</text>'
            % (p["x"] + p["w"] / 2.0, p["y"] + p["h"] / 2.0, html.escape(src), conf, html.escape(label)))
    mark = ('<text x="%.1f" y="%.1f" font-size="10" fill="#b45309" text-anchor="start">?</text>'
            % (p["x"] + 4, p["y"] + 12)) if low else ""
    return ('<g id="ent-%s" data-source-ref="%s" data-confidence="%s">%s%s%s</g>'
            % (html.escape(e["id"]), html.escape(src), conf, rect, text, mark))


def _render_annotation(a):
    x = a.get("x") if isinstance(a.get("x"), (int, float)) else 10
    y = a.get("y") if isinstance(a.get("y"), (int, float)) else 20
    return ('<text x="%d" y="%d" font-size="11" fill="#374151" data-source-ref="%s" '
            'data-confidence="%s">%s</text>'
            % (int(x), int(y), html.escape(a.get("source_ref", "")), a.get("confidence", "high"),
               html.escape(a.get("text", ""))))


def _render_legend(ir, relations, entity_types, x, y):
    lines = []
    items = []
    for k in sorted({r.get("kind", "connect") for r in relations}):
        items.append(("kind", k, KIND_LABEL.get(k, k)))
    for t in sorted(entity_types):
        items.append(("type", t, t))
    seen = set()
    for kind, key, label in items:
        if (kind, key) in seen:
            continue
        seen.add((kind, key))
        if kind == "kind":
            st = KIND_STYLE.get(key, KIND_STYLE["default"])
            sw = '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-width="2"%s />' % (
                x, y + 12, x + 28, y + 12, st["stroke"],
                _attrs(**{"stroke-dasharray": st["dash"]} if st["dash"] else {}))
        else:
            sw = '<rect x="%d" y="%d" width="26" height="12" rx="2" fill="%s" stroke="#475569" stroke-width="1" />' % (
                x, y + 5, TYPE_FILL.get(key, TYPE_FILL["default"]))
        txt = '<text x="%d" y="%d" font-size="11" fill="#1f2937">%s</text>' % (x + 36, y + 13, html.escape(label))
        lines.append('<g>%s%s</g>' % (sw, txt))
        y += 24
    return lines


def build_svg(ir, out_path=None):
    """IR -> 分层可编辑 SVG。返回 (svg_text, meta)。"""
    ir = normalize_ir(ir)
    entities = ir["entities"]
    relations = ir["relations"]
    kind = ir["kind"]
    pos = layout_entities(entities, kind)
    by_id = {e["id"]: e for e in entities}
    entity_types = {e["type"] for e in entities}
    legend_items = len({r.get("kind", "connect") for r in relations}) + len(entity_types)
    W, H = _canvas(pos, legend_items)
    markers = []
    used_kinds = {r.get("kind") if r.get("kind") in KIND_STYLE else "default" for r in relations}
    used_kinds.add("default")
    for k in sorted(used_kinds):
        st = KIND_STYLE[k]
        for end in ("start", "end"):
            refx = 10 if end == "end" else 0
            markers.append(
                '<marker id="%s" viewBox="0 0 10 10" refX="%d" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
                '<path d="M 0 0 L 10 5 L 0 10 z" fill="%s"/></marker>'
                % (_marker_id(k, end), refx, st["stroke"]))
    header = (
        '<svg xmlns="%s" width="%d" height="%d" viewBox="0 0 %d %d" font-family="sans-serif">'
        '<metadata id="meta" data-case-id="%s" data-kind="%s" data-layout="auto-layout" data-title="%s"/>'
        '<defs>%s</defs>'
        % (SVG_NS, W, H, W, H, html.escape(ir["case_id"]), html.escape(kind),
           html.escape(ir["title"]), "".join(markers)))
    parts = [header]
    parts.append('<g id="layer-relations">')
    for r in relations:
        parts.append(_render_relation(r, pos, by_id))
    parts.append('</g>')
    parts.append('<g id="layer-entities">')
    for e in entities:
        parts.append(_render_entity(e, pos))
    parts.append('</g>')
    parts.append('<g id="layer-annotations">')
    for a in ir["annotations"]:
        parts.append(_render_annotation(a))
    parts.append('</g>')
    parts.append('<g id="layer-legend">')
    ly = max((p["y"] + p["h"] for p in pos.values()), default=0) + 40
    parts.extend(_render_legend(ir, relations, entity_types, 40, ly))
    parts.append('</g>')
    parts.append('</svg>')
    svg_text = "\n".join(parts)
    meta = {
        "width": W, "height": H, "layers": 4,
        "entity_count": len(entities), "relation_count": len(relations),
        "layout": "auto-layout" if kind in FLOW_KINDS or True else "auto-layout",
        "has_missing_evidence": any(
            (e["evidence"]["status"] in ("missing", "not_reported") or e["confidence"] == "low")
            for e in entities),
    }
    if out_path:
        d = os.path.dirname(os.path.abspath(out_path))
        os.makedirs(d, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(svg_text)
    return svg_text, meta


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
def validate(ir, svg_path=None):
    checks = []
    ir = normalize_ir(ir)
    entities = ir["entities"]
    relations = ir["relations"]
    ids = {}
    for e in entities:
        if not e.get("id"):
            checks.append({"severity": "critical", "code": "entity_missing_id", "msg": "实体缺 id", "target": str(e)[:80]})
        else:
            ids[e["id"]] = e
    for r in relations:
        rid = r.get("id") or "<unnamed>"
        f, t = r.get("from"), r.get("to")
        if f not in ids:
            checks.append({"severity": "critical", "code": "dangling_from", "msg": "关系 from 指向不存在的实体", "target": rid, "detail": f})
        if t not in ids:
            checks.append({"severity": "critical", "code": "dangling_to", "msg": "关系 to 指向不存在的实体", "target": rid, "detail": t})
        if f == t and f in ids:
            checks.append({"severity": "warning", "code": "self_loop", "msg": "自环关系，确认是否为回流/反馈", "target": rid})
        ev = r.get("evidence") or {}
        if not ev.get("locator") and not ev.get("quote"):
            checks.append({"severity": "warning", "code": "relation_missing_evidence", "msg": "关系缺证据定位", "target": rid})
        if ev.get("status") not in EVIDENCE_STATUSES:
            checks.append({"severity": "warning", "code": "bad_evidence_status", "msg": "证据 status 非法", "target": rid, "detail": ev.get("status")})
        if r.get("confidence") == "low" and not (r.get("note") or ev.get("quote")):
            checks.append({"severity": "warning", "code": "low_confidence_relation", "msg": "低置信关系缺说明", "target": rid})
    for e in entities:
        eid = e.get("id")
        conf = e.get("confidence", "")
        ev = e.get("evidence") or {}
        if conf not in CONFIDENCES:
            checks.append({"severity": "warning", "code": "bad_confidence", "msg": "置信度非法", "target": eid, "detail": conf})
        if not ev.get("locator") and not ev.get("quote"):
            checks.append({"severity": "warning", "code": "entity_missing_evidence", "msg": "实体缺证据定位", "target": eid})
        if conf == "low" and not (e.get("note") or ev.get("quote")):
            checks.append({"severity": "warning", "code": "low_confidence_entity", "msg": "低置信实体缺说明", "target": eid})
    if svg_path and os.path.exists(svg_path):
        try:
            tree = ET.parse(svg_path)
            root = tree.getroot()

            def local(tag):
                return tag.rsplit("}", 1)[-1]

            elements = [el for el in root.iter() if local(el.tag) in ("rect", "g", "text", "path", "line")]
            covered = sum(1 for el in elements if "data-source-ref" in el.attrib)
            if covered == 0:
                checks.append({"severity": "critical", "code": "svg_no_source_ref", "msg": "SVG 无 data-source-ref 覆盖"})
            else:
                checks.append({"severity": "info", "code": "svg_source_ref_count", "msg": "带 source_ref 的 SVG 元素数", "detail": str(covered)})
            layers = [local(el.tag) for el in root if local(el.tag) == "g" and "layer" in (el.attrib.get("id") or "")]
            checks.append({"severity": "info", "code": "svg_layers", "msg": "SVG 图层数", "detail": str(len(layers))})
        except Exception as e:
            checks.append({"severity": "critical", "code": "svg_parse_failed", "msg": "SVG 解析失败", "detail": str(e)})
    return checks


def summarize_checks(checks):
    critical = [c for c in checks if c.get("severity") == "critical"]
    warnings = [c for c in checks if c.get("severity") == "warning"]
    return {"critical": len(critical), "warning": len(warnings), "total": len(checks), "pass": len(critical) == 0}
