#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py —— 科研图稿管线核心实现（仅 Python 标准库）。

设计主线：**模型与计划分离**，保证"可复核、可复跑"。

  evidence inventory（材料清单，实际扫描 /input，不假设文件名）
      -> figure_model.json（科学真相：实体/关系/步骤/数据编码/图例/证据，
                           每个关键图元带 source_ref + 逐字 quote）
      -> plan.json（确定性布局：节点位置、箭头路由、画布、字体、图例）
      -> figure.svg（分层可编辑 SVG，每个图元带 data-source-ref）
      -> checks.json（自检结果）
      -> result.json（增量合并：先落骨架，再逐步改进）

关键约定：
- 全部函数确定性：无随机数；同一输入重复运行产出逐字节一致。
- 不编造部件、数值或来源；缺失一律写 uncertainty / limitations / pending_human。
- 仅标准库，路径用相对/绝对参数传入，兼容 Linux 与 Windows 运行环境。
"""
from __future__ import annotations

import json
import math
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

MAX_TEXT_BYTES = 2 * 1024 * 1024

KIND_COLORS = {
    "material": "#1f77b4",   # 物料流向：蓝
    "energy": "#d62728",     # 能量流向：红
    "info": "#2ca02c",       # 信息/信号：绿
    "signal": "#2ca02c",
    "sequence": "#7f7f7f",   # 顺序/步骤推进：灰
    "step": "#7f7f7f",
    "relation": "#333333",
}

KIND_LABELS = {
    "material": "物料流向",
    "energy": "能量流向",
    "info": "信息流向",
    "signal": "信息/信号流向",
    "sequence": "顺序关系",
    "step": "步骤推进",
    "relation": "关系",
}

CONTRACT_FIELDS = (
    "entities", "relations", "data_encoding",
    "legend_annotations", "editable_sources", "exports",
)


# --------------------------------------------------------------------------
# 基础 IO（容错：BOM、坏编码、坏 JSON、空文件）
# --------------------------------------------------------------------------
def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_text(path):
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        return f.read()


def read_json(path, default=None):
    raw = read_text(path)
    if raw.strip() == "":
        return {} if default is None else default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise


def write_json(path, obj):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return path


def write_text(path, text):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def rel_path(base, p):
    try:
        return os.path.relpath(p, base)
    except ValueError:
        return p


# --------------------------------------------------------------------------
# 输入发现与材料清单（evidence inventory）
# --------------------------------------------------------------------------
def discover_materials(input_dir):
    """扫描输入目录，产出材料清单。不假设固定文件名/键名。"""
    items = []
    if not os.path.isdir(input_dir):
        return items
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        files.sort()
        for name in files:
            p = os.path.join(root, name)
            try:
                st = os.stat(p)
            except OSError:
                continue
            rel = rel_path(input_dir, p)
            base = {"path": rel, "bytes": st.st_size}
            if st.st_size > MAX_TEXT_BYTES:
                base.update({"kind": "skipped", "reason": "exceeds 2MB"})
                items.append(base)
                continue
            if name in ("case.json", "task.md"):
                base["kind"] = "input_manifest"
                items.append(base)
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff",
                       ".bmp", ".pdf", ".svg", ".xlsx", ".docx", ".pptx", ".zip",
                       ".gz", ".xz"):
                base["kind"] = "binary"
                items.append(base)
                continue
            try:
                txt = read_text(p)
                base["kind"] = "text"
                base["lines"] = txt.count("\n") + (1 if txt else 0)
                base["chars"] = len(txt)
            except Exception as exc:  # 极难触达，兜底
                base["kind"] = "binary"
                base["read_error"] = str(exc)
            items.append(base)
    return items


def load_case(case_path):
    """读取 /input/case.json，容错返回 dict。"""
    if not os.path.isfile(case_path):
        return {"case_id": "unknown-case", "_missing": True}
    try:
        return read_json(case_path)
    except Exception as exc:
        return {"case_id": "unknown-case", "_read_error": str(exc)}


def extract_case_id(case):
    for key in ("case_id", "caseId", "id", "challenge_id", "challengeId", "armiId"):
        v = case.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "unknown-case"


def extract_output_contract(case):
    """在 case.json 中查找 output_contract（支持顶层或嵌套）。"""
    if isinstance(case.get("output_contract"), dict):
        return case["output_contract"]
    for k in ("task", "spec", "contract"):
        v = case.get(k)
        if isinstance(v, dict) and isinstance(v.get("output_contract"), dict):
            return v["output_contract"]
    return None


def _empty_for(v):
    if isinstance(v, list):
        return []
    if isinstance(v, dict):
        return {}
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return None
    return ""


# --------------------------------------------------------------------------
# figure_model.json：科学真相的规范与校验
# --------------------------------------------------------------------------
def make_template(case_id, figure_kind="unspecified"):
    return {
        "case_id": case_id,
        "figure_kind": figure_kind,
        "title": "",
        "canvas": {"width": 1000, "height": 700, "unit": "px"},
        "font_family": "Arial, 'Noto Sans CJK SC', sans-serif",
        "font_embed": False,
        "entities": [],
        "relations": [],
        "steps": [],
        "data_encoding": [],
        "legend": [],
        "evidence": [],
        "uncertainty": [],
        "pending_human": [],
    }


def normalize_model(raw):
    """校验并规范化 figure_model.json；结构非法时抛 ValueError。"""
    if not isinstance(raw, dict):
        raise ValueError("figure_model.json 必须是 JSON 对象")
    model = dict(raw)
    model.setdefault("case_id", raw.get("case_id") or "unknown-case")
    model.setdefault("figure_kind",
                     raw.get("figure_kind") or raw.get("kind") or "unspecified")
    model.setdefault("title", raw.get("title", ""))
    cv = raw.get("canvas") if isinstance(raw.get("canvas"), dict) else {}
    model["canvas"] = {
        "width": int(cv.get("width", 1000)),
        "height": int(cv.get("height", 700)),
        "unit": cv.get("unit", "px"),
    }
    model.setdefault("font_family", "Arial, 'Noto Sans CJK SC', sans-serif")
    model.setdefault("font_embed", bool(raw.get("font_embed", False)))

    # entities
    ents, seen = [], set()
    for i, e in enumerate(raw.get("entities", []) or []):
        if not isinstance(e, dict):
            continue
        eid = str(e.get("id") or f"e{i + 1}")
        if eid in seen:
            raise ValueError("duplicate entity id: %s" % eid)
        seen.add(eid)
        ents.append({
            "id": eid,
            "label": e.get("label", ""),
            "type": e.get("type", "component"),
            "source_ref": e.get("source_ref"),
            "note": e.get("note", ""),
        })
    model["entities"] = ents

    # relations
    rels, rseen = [], set()
    for i, r in enumerate(raw.get("relations", []) or []):
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id") or f"r{i + 1}")
        if rid in rseen:
            raise ValueError("duplicate relation id: %s" % rid)
        rseen.add(rid)
        rels.append({
            "id": rid,
            "from": r.get("from"),
            "to": r.get("to"),
            "kind": r.get("kind", "relation"),
            "direction": r.get("direction", "forward"),
            "line": r.get("line", "dashed" if r.get("direction") in ("backward", "bidirectional") else "solid"),
            "label": r.get("label", ""),
            "source_ref": r.get("source_ref"),
        })
    model["relations"] = rels

    # steps
    steps, sseen, orders = [], set(), set()
    for i, s in enumerate(raw.get("steps", []) or []):
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or f"s{i + 1}")
        if sid in sseen:
            raise ValueError("duplicate step id: %s" % sid)
        sseen.add(sid)
        order = s.get("order", i + 1)
        try:
            order = int(order)
        except (TypeError, ValueError):
            order = i + 1
        if order in orders:
            raise ValueError("duplicate step order: %s" % order)
        orders.add(order)
        steps.append({
            "id": sid,
            "order": order,
            "label": s.get("label", ""),
            "kind": s.get("kind", "main"),
            "source_ref": s.get("source_ref"),
            "note": s.get("note", ""),
        })
    model["steps"] = steps

    # evidence：逐字 quote + locator 是硬要求
    ev, eseen = [], set()
    for i, e in enumerate(raw.get("evidence", []) or []):
        if not isinstance(e, dict):
            continue
        eid = str(e.get("id") or f"q{i + 1}")
        if eid in eseen:
            raise ValueError("duplicate evidence id: %s" % eid)
        eseen.add(eid)
        ev.append({
            "id": eid,
            "locator": e.get("locator", ""),
            "quote": e.get("quote", ""),
        })
    model["evidence"] = ev

    for field in ("data_encoding", "legend"):
        val = raw.get(field) or []
        model[field] = [x for x in val if isinstance(x, dict)]
    for field in ("uncertainty", "pending_human"):
        val = raw.get(field) or []
        model[field] = [x for x in val if isinstance(x, (str, dict))]

    return model


def validate_model(model):
    """模型级自检，返回 checks 列表（severity: critical|warn|info）。"""
    checks = []
    ents = {e["id"]: e for e in model.get("entities", [])}
    steps = {s["id"]: s for s in model.get("steps", [])}
    nodes = dict(ents)
    nodes.update(steps)
    evidence = {e["id"] for e in model.get("evidence", [])}

    for r in model.get("relations", []):
        if not r.get("from") or r.get("from") not in nodes:
            checks.append({"id": "dangling-relation", "severity": "critical", "pass": False,
                           "detail": "关系 %s 的 from=%s 无对应节点" % (r.get("id"), r.get("from"))})
        if not r.get("to") or r.get("to") not in nodes:
            checks.append({"id": "dangling-relation", "severity": "critical", "pass": False,
                           "detail": "关系 %s 的 to=%s 无对应节点" % (r.get("id"), r.get("to"))})

    for e in model.get("entities", []):
        sr = e.get("source_ref")
        if not sr:
            checks.append({"id": "missing-source-ref", "severity": "warn", "pass": True,
                           "detail": "实体 %s 缺 source_ref，需人工补充" % e["id"]})
        elif sr not in evidence:
            checks.append({"id": "broken-source-ref", "severity": "critical", "pass": False,
                           "detail": "实体 %s 的 source_ref=%s 无对应证据" % (e["id"], sr)})
    for s in model.get("steps", []):
        sr = s.get("source_ref")
        if sr and sr not in evidence:
            checks.append({"id": "broken-source-ref", "severity": "critical", "pass": False,
                           "detail": "步骤 %s 的 source_ref=%s 无对应证据" % (s["id"], sr)})

    if not model.get("entities") and not model.get("steps"):
        checks.append({"id": "empty-model", "severity": "warn", "pass": True,
                       "detail": "模型无实体/步骤：交付为诚实部分结果，需在 limitations 说明"})
    return checks


# --------------------------------------------------------------------------
# plan.json：确定性布局（模型 → 位置/路由/图例）
# --------------------------------------------------------------------------
def _node_size(label, kind):
    w = max(120, min(240, 36 + 9 * len(str(label))))
    h = 54 if kind == "step" else 46
    return w, h


def build_legend_entries(model):
    entries, seen = [], set()
    for r in model.get("relations", []):
        kind = r.get("kind", "relation")
        line = r.get("line", "solid")
        direction = r.get("direction", "forward")
        color = KIND_COLORS.get(kind, "#333333")
        key = (kind, line, direction, color)
        if key in seen:
            continue
        seen.add(key)
        suffix = ""
        if line == "dashed" or direction in ("backward", "bidirectional"):
            suffix = "（回流/虚线）" if direction in ("backward", "bidirectional") else "（虚线）"
        entries.append({
            "kind": "relation", "key": kind, "color": color,
            "line": "dashed" if line == "dashed" else "solid",
            "label": KIND_LABELS.get(kind, kind) + suffix,
            "source_ref": r.get("source_ref"),
        })
    for de in model.get("data_encoding", []):
        m = re.search(r"#[0-9a-fA-F]{3,8}", str(de.get("mapping", "")))
        color = m.group(0) if m else "#888888"
        entries.append({
            "kind": "encoding", "key": str(de.get("param", de.get("mapping", ""))),
            "color": color, "line": "swatch",
            "label": "%s → %s" % (de.get("param", ""), de.get("mapping", "")),
            "source_ref": de.get("source_ref"),
        })
    return entries


def _grid_place(items, x0, y0, x1, y1, cols):
    out = {}
    n = len(items)
    if n == 0:
        return out
    rows = math.ceil(n / cols)
    cw = (x1 - x0) / cols
    rh = (y1 - y0) / rows
    for i, (iid, label, kind) in enumerate(items):
        c = i % cols
        r = i // cols
        w, h = _node_size(label, kind)
        w = max(60, min(w, cw - 24))
        h = max(36, min(h, rh - 24))
        cx = x0 + cw * (c + 0.5)
        cy = y0 + rh * (r + 0.5)
        out[iid] = {"id": iid, "x": cx - w / 2, "y": cy - h / 2, "w": w, "h": h, "kind": kind}
    return out


def _box_center(b):
    return (b["x"] + b["w"] / 2, b["y"] + b["h"] / 2)


def _clip_to_box(center, box, toward):
    cx, cy = center
    tx, ty = toward
    dx, dy = tx - cx, ty - cy
    hw, hh = box["w"] / 2, box["h"] / 2
    if dx == 0 and dy == 0:
        return (cx, cy)
    if abs(dx) < 1e-9:
        return (cx, cy + (hh if dy > 0 else -hh))
    if abs(dy) < 1e-9:
        return (cx + (hw if dx > 0 else -hw), cy)
    sx, sy = hw / abs(dx), hh / abs(dy)
    s = min(sx, sy)
    return (cx + dx * s, cy + dy * s)


def _routes_from_rels(rels, placements):
    out = []
    for r in rels:
        a, b = placements.get(r.get("from")), placements.get(r.get("to"))
        if not a or not b:
            out.append({"relation_id": r.get("id"), "error": "missing_node",
                        "from": r.get("from"), "to": r.get("to")})
            continue
        p1 = _clip_to_box(_box_center(a), a, _box_center(b))
        p2 = _clip_to_box(_box_center(b), b, _box_center(a))
        out.append({
            "relation_id": r.get("id"),
            "from_xy": [round(p1[0], 1), round(p1[1], 1)],
            "to_xy": [round(p2[0], 1), round(p2[1], 1)],
            "dashed": r.get("line") == "dashed" or r.get("direction") in ("backward", "bidirectional"),
            "bidirectional": r.get("direction") == "bidirectional",
        })
    return out


def compute_layout(model):
    ents = model.get("entities", [])
    steps_sorted = sorted(
        model.get("steps", []),
        key=lambda s: (int(s.get("order", 10 ** 9)), str(s.get("id", ""))),
    )
    canvas = model.get("canvas", {})
    W = float(canvas.get("width", 1000))
    H = float(canvas.get("height", 700))
    legend_entries = build_legend_entries(model)
    has_legend = bool(legend_entries)
    bottom_margin = 150 if has_legend else 30
    top_margin = 70
    placements = {}

    if steps_sorted:
        # 左列为主序列（步骤），右侧网格放实体
        sx = W * 0.32
        area_top, area_bot = top_margin + 20, H - bottom_margin - 10
        n = len(steps_sorted)
        step_w, step_h, gap = 210, 56, 36
        total_h = n * step_h + (n - 1) * gap
        y = area_top + max(0, (area_bot - area_top - total_h) / 2)
        for s in steps_sorted:
            placements[s["id"]] = {
                "id": s["id"], "x": sx - step_w / 2, "y": y,
                "w": step_w, "h": step_h, "kind": "step", "order": s.get("order"),
            }
            y += step_h + gap
        ents_only = [e for e in ents if e["id"] not in placements]
        if ents_only:
            items = [(e["id"], e.get("label", ""), e.get("type", "component")) for e in ents_only]
            cols = max(1, math.ceil(math.sqrt(len(items))))
            placements.update(_grid_place(items, W * 0.56, top_margin, W - 30, H - bottom_margin - 10, cols))
    else:
        items = [(e["id"], e.get("label", ""), e.get("type", "component")) for e in ents]
        cols = max(1, math.ceil(math.sqrt(len(items)))) if items else 1
        placements.update(_grid_place(items, 30, top_margin, W - 30, H - bottom_margin - 10, cols))

    routes = _routes_from_rels(model.get("relations", []), placements)

    legend_box = None
    if legend_entries:
        lh = 24 + len(legend_entries) * 26
        legend_box = {"x": W * 0.60, "y": H - lh - 20, "w": W * 0.38, "h": lh}

    return {
        "case_id": model.get("case_id"),
        "layout_engine": "grid-deterministic-v1",
        "canvas": {"width": W, "height": H, "unit": canvas.get("unit", "px")},
        "font": {"family": model.get("font_family", "Arial, 'Noto Sans CJK SC', sans-serif"),
                 "embed": bool(model.get("font_embed", False))},
        "placements": list(placements.values()),
        "routes": routes,
        "legend_entries": legend_entries,
        "legend_box": legend_box,
    }


# --------------------------------------------------------------------------
# figure.svg：分层可编辑 SVG
# --------------------------------------------------------------------------
def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _color_id(c):
    return re.sub(r"[^0-9a-fA-F]", "", c).lower() or "333333"


def _fit_label(label, w):
    per = 13
    maxn = max(1, int(w / per))
    s = str(label)
    if len(s) > maxn:
        return s[: maxn - 1] + "…"
    return s


def build_svg(plan, model):
    W = float(plan["canvas"]["width"])
    H = float(plan["canvas"]["height"])
    font = plan.get("font", {}).get("family", "Arial, sans-serif")
    placements = {p["id"]: p for p in plan.get("placements", [])}
    ents_by_id = {e["id"]: e for e in model.get("entities", [])}
    steps_by_id = {s["id"]: s for s in model.get("steps", [])}
    rels_by_id = {r["id"]: r for r in model.get("relations", [])}

    used_colors = set()
    for r in model.get("relations", []):
        used_colors.add(KIND_COLORS.get(r.get("kind", "relation"), "#333333"))
    for de in model.get("data_encoding", []):
        m = re.search(r"#[0-9a-fA-F]{3,8}", str(de.get("mapping", "")))
        if m:
            used_colors.add(m.group(0))

    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append('<svg xmlns="http://www.w3.org/2000/svg" width="%s" height="%s" '
                 'viewBox="0 0 %s %s" role="img">' % (W, H, W, H))
    parts.append('  <metadata id="source-map">')
    parts.append('    <!-- 每图元 data-source-ref 指向 figure_model.json 的 evidence.id；')
    parts.append('         evidence 内含 locator + 逐字 quote，可追溯输入材料原文。 -->')
    parts.append('  </metadata>')
    parts.append('  <defs>')
    for c in sorted(used_colors):
        cid = _color_id(c)
        parts.append('    <marker id="arrow-%s" viewBox="0 0 10 10" refX="9" refY="5" '
                     'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                     '<path d="M0,0 L10,5 L0,10 z" fill="%s"/></marker>' % (cid, c))
        parts.append('    <marker id="arrow-r-%s" viewBox="0 0 10 10" refX="1" refY="5" '
                     'markerWidth="7" markerHeight="7" orient="auto">'
                     '<path d="M10,0 L0,5 L10,10 z" fill="%s"/></marker>' % (cid, c))
    parts.append('  </defs>')
    parts.append('  <g id="layer-background">'
                 '<rect x="0" y="0" width="%s" height="%s" fill="#ffffff"/></g>' % (W, H))

    # 关系层（先画，避免遮挡实体）
    parts.append('  <g id="layer-relations">')
    for rt in plan.get("routes", []):
        if rt.get("error"):
            continue
        rel = rels_by_id.get(rt["relation_id"], {})
        c = KIND_COLORS.get(rel.get("kind", "relation"), "#333333")
        dash = ' stroke-dasharray="6,4"' if rt.get("dashed") else ""
        marker = ' marker-end="url(#arrow-%s)"' % _color_id(c)
        marker_start = ' marker-start="url(#arrow-r-%s)"' % _color_id(c) if rt.get("bidirectional") else ""
        parts.append('    <line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" '
                     'stroke-width="2"%s%s%s data-relation-id="%s" data-source-ref="%s" '
                     'data-direction="%s"/>'
                     % (rt["from_xy"][0], rt["from_xy"][1], rt["to_xy"][0], rt["to_xy"][1],
                        c, dash, marker, marker_start, _esc(rel.get("id", "")),
                        _esc(rel.get("source_ref", "")), _esc(rel.get("direction", "forward"))))
        if rel.get("label"):
            mx = (rt["from_xy"][0] + rt["to_xy"][0]) / 2
            my = (rt["from_xy"][1] + rt["to_xy"][1]) / 2
            parts.append('    <text x="%s" y="%s" text-anchor="middle" font-family="%s" '
                         'font-size="11" fill="#444444" data-relation-id="%s">%s</text>'
                         % (mx, my - 4, _esc(font), _esc(rel.get("id", "")), _esc(rel.get("label"))))
    parts.append('  </g>')

    # 实体层
    parts.append('  <g id="layer-entities">')
    for p in plan.get("placements", []):
        if p.get("kind") == "step":
            continue
        e = ents_by_id.get(p["id"], {})
        fill = "#eef4fb" if e.get("type") in ("vessel", "apparatus", "device") else "#f7f7f7"
        parts.append('    <rect x="%s" y="%s" width="%s" height="%s" rx="4" fill="%s" '
                     'stroke="#444444" stroke-width="1.2" data-entity-id="%s" '
                     'data-type="%s" data-source-ref="%s"/>'
                     % (p["x"], p["y"], p["w"], p["h"], fill, _esc(p["id"]),
                        _esc(e.get("type", "")), _esc(e.get("source_ref", ""))))
        cx, cy = p["x"] + p["w"] / 2, p["y"] + p["h"] / 2
        parts.append('    <text x="%s" y="%s" text-anchor="middle" font-family="%s" '
                     'font-size="14" fill="#111111" data-label-full="%s">%s</text>'
                     % (cx, cy + 5, _esc(font), _esc(e.get("label", "")),
                        _esc(_fit_label(e.get("label", ""), p["w"]))))
    parts.append('  </g>')

    # 步骤层
    parts.append('  <g id="layer-steps">')
    for p in plan.get("placements", []):
        if p.get("kind") != "step":
            continue
        s = steps_by_id.get(p["id"], {})
        parts.append('    <rect x="%s" y="%s" width="%s" height="%s" rx="12" fill="#fef7e0" '
                     'stroke="#b8860b" stroke-width="1.4" data-step-id="%s" data-order="%s" '
                     'data-source-ref="%s"/>'
                     % (p["x"], p["y"], p["w"], p["h"], _esc(p["id"]),
                        _esc(p.get("order", "")), _esc(s.get("source_ref", ""))))
        cx, cy = p["x"] + p["w"] / 2, p["y"] + p["h"] / 2
        parts.append('    <circle cx="%s" cy="%s" r="13" fill="#b8860b"/>'
                     '<text x="%s" y="%s" text-anchor="middle" font-family="%s" font-size="12" '
                     'fill="#ffffff">%s</text>'
                     % (p["x"] + 20, cy, p["x"] + 20, cy + 4, _esc(font), _esc(p.get("order", ""))))
        parts.append('    <text x="%s" y="%s" text-anchor="middle" font-family="%s" '
                     'font-size="13" fill="#111111" data-label-full="%s">%s</text>'
                     % (cx + 12, cy + 5, _esc(font), _esc(s.get("label", "")),
                        _esc(_fit_label(s.get("label", ""), p["w"] - 30))))
    parts.append('  </g>')

    # 标注层（数据编码的数值标注）
    parts.append('  <g id="layer-annotations">')
    for de in model.get("data_encoding", []):
        target = de.get("target_id")
        if target in placements:
            p = placements[target]
            parts.append('    <text x="%s" y="%s" text-anchor="middle" font-family="%s" '
                         'font-size="11" fill="#555555" data-source-ref="%s">%s</text>'
                         % (p["x"] + p["w"] / 2, p["y"] - 6, _esc(font),
                            _esc(de.get("source_ref", "")), _esc(str(de.get("mapping", "")))))
    parts.append('  </g>')

    # 图例层
    parts.append('  <g id="layer-legend">')
    lb = plan.get("legend_box")
    if lb:
        parts.append('    <rect x="%s" y="%s" width="%s" height="%s" rx="6" fill="#ffffff" '
                     'stroke="#999999" stroke-width="1"/>' % (lb["x"], lb["y"], lb["w"], lb["h"]))
        parts.append('    <text x="%s" y="%s" font-family="%s" font-size="13" '
                     'font-weight="bold" fill="#111111">图例</text>'
                     % (lb["x"] + 12, lb["y"] + 18, _esc(font)))
        row_y = lb["y"] + 40
        for le in plan.get("legend_entries", []):
            if le.get("line") == "swatch":
                parts.append('    <rect x="%s" y="%s" width="18" height="12" fill="%s"/>'
                             % (lb["x"] + 12, row_y - 10, le["color"]))
            else:
                dash = ' stroke-dasharray="6,4"' if le.get("line") == "dashed" else ""
                parts.append('    <line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" '
                             'stroke-width="2"%s marker-end="url(#arrow-%s)"/>'
                             % (lb["x"] + 12, row_y - 4, lb["x"] + 30, row_y - 4,
                                le["color"], dash, _color_id(le["color"])))
            parts.append('    <text x="%s" y="%s" font-family="%s" font-size="12" '
                         'fill="#333333" data-source-ref="%s">%s</text>'
                         % (lb["x"] + 38, row_y, _esc(font), _esc(le.get("source_ref", "")),
                            _esc(le.get("label", ""))))
            row_y += 26
    parts.append('  </g>')
    parts.append('</svg>')
    return "\n".join(parts) + "\n"


# --------------------------------------------------------------------------
# 自检
# --------------------------------------------------------------------------
def validate_all(model, plan, svg_text):
    checks = list(validate_model(model))
    W = float(plan["canvas"]["width"])
    H = float(plan["canvas"]["height"])

    for p in plan.get("placements", []):
        if p["x"] < -0.5 or p["y"] < -0.5 or p["x"] + p["w"] > W + 0.5 or p["y"] + p["h"] > H + 0.5:
            checks.append({"id": "overflow", "severity": "critical", "pass": False,
                           "detail": "节点 %s 超出画布" % p["id"]})

    # 图例与关系类型一致
    kinds_used = {r.get("kind", "relation") for r in model.get("relations", [])}
    legend_keys = {le["key"] for le in plan.get("legend_entries", []) if le["kind"] == "relation"}
    for k in kinds_used:
        if k not in legend_keys:
            checks.append({"id": "legend-missing", "severity": "warn", "pass": True,
                           "detail": "图例缺关系类型 %s" % k})

    # SVG 可解析且图层齐全
    try:
        root = ET.fromstring(svg_text)
        ids = {el.get("id") for el in root.iter() if el.get("id")}
        for need in ("layer-relations", "layer-entities", "layer-legend"):
            if need not in ids:
                checks.append({"id": "missing-layer", "severity": "warn", "pass": True,
                               "detail": "缺少图层 %s" % need})
        checks.append({"id": "svg-parse", "severity": "info", "pass": True,
                       "detail": "SVG 可解析"})
    except Exception as exc:
        checks.append({"id": "svg-parse", "severity": "critical", "pass": False,
                       "detail": "SVG 解析失败: %s" % exc})
    return checks


def checks_critical_passed(checks):
    return all(c["pass"] for c in checks if c["severity"] == "critical")


# --------------------------------------------------------------------------
# result.json 管理（先骨架后增量）
# --------------------------------------------------------------------------
def init_result(case, case_id, out_path):
    result = {
        "case_id": case_id,
        "status": "in_progress",
        "title": "",
        "figure_kind": "",
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
        "checks": [],
        "artifacts": [],
        "change_log": [{"ts": now_iso(), "action": "init_skeleton",
                        "detail": "结果骨架落盘（诚实 partial，后续增量改进）"}],
        "runtime": {"started_at": now_iso(), "ended_at": None},
    }
    oc = extract_output_contract(case)
    if isinstance(oc, dict):
        for k in oc.keys():
            if k not in result:
                result[k] = _empty_for(oc[k])
    write_json(out_path, result)
    return result


def merge_result(result_path, *, model=None, plan=None, checks=None, inventory=None,
                 status=None, note=None):
    r = read_json(result_path)
    r["change_log"].append({"ts": now_iso(), "action": "merge",
                            "detail": note or "增量合并"})
    if model is not None:
        r["title"] = model.get("title", "")
        r["figure_kind"] = model.get("figure_kind", "")
        r["entities"] = model.get("entities", [])
        r["relations"] = model.get("relations", [])
        r["data_encoding"] = model.get("data_encoding", [])
        r["legend_annotations"] = model.get("legend", [])
        r["uncertainty"] = model.get("uncertainty", [])
        r["pending_human"] = model.get("pending_human", [])
        r["sources"] = [
            {"id": e.get("id"), "locator": e.get("locator"), "quote": e.get("quote")}
            for e in model.get("evidence", [])
        ]
    if inventory is not None:
        r.setdefault("materials_inventory", inventory)
    if checks is not None:
        r["checks"] = checks
    if status:
        r["status"] = status
    write_json(result_path, r)
    return r


def finalize_result(result_path, work_dir, output_dir, status):
    r = read_json(result_path)
    r["status"] = status
    r["runtime"]["ended_at"] = now_iso()
    r["change_log"].append({"ts": now_iso(), "action": "finalize",
                            "detail": "status=%s" % status})

    artifacts = []
    for d, role in ((output_dir, "output"), (work_dir, "work")):
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                artifacts.append({"path": p, "bytes": os.path.getsize(p), "role": role})
    r["artifacts"] = artifacts

    r["editable_sources"] = []
    r["exports"] = []

    plan = None
    pp = os.path.join(work_dir, "plan.json")
    if os.path.isfile(pp):
        try:
            plan = read_json(pp)
        except Exception:
            plan = None
    canvas = (plan or {}).get("canvas", {})
    font = (plan or {}).get("font", {})
    checks = r.get("checks", [])
    legend_fail = any(c.get("id") == "legend-mismatch" and not c.get("pass") for c in checks)
    color_check = "failed" if legend_fail else ("ok" if checks else "unverified")

    for a in artifacts:
        ext = a["path"].lower().rsplit(".", 1)[-1] if "." in os.path.basename(a["path"]) else ""
        if ext == "svg":
            r["editable_sources"].append({
                "path": a["path"], "format": "svg",
                "layers": ["layer-background", "layer-relations", "layer-entities",
                           "layer-steps", "layer-annotations", "layer-legend"],
                "notes": "分层可编辑，图元带 data-source-ref 可追溯证据",
            })
        elif ext in ("png", "pdf"):
            r["exports"].append({
                "path": a["path"], "format": ext,
                "width": canvas.get("width"), "height": canvas.get("height"),
                "unit": canvas.get("unit", "px"),
                "dpi": None,
                "fonts": font.get("family"), "font_embed": bool(font.get("embed", False)),
                "color_check": color_check,
                "render_status": "rendered",
                "notes": "真实渲染预览，供检查" if ext == "png" else "矢量预览",
            })
    if not r["exports"]:
        r.setdefault("limitations", [])
        if not any(isinstance(x, str) and "渲染" in x for x in r["limitations"]):
            r["limitations"].append("无 PNG/PDF 预览：渲染工具缺失或未执行，render_status=skipped")
    write_json(result_path, r)
    return r
