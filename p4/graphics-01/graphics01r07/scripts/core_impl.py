#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-schematic-editor 共享实现（仅用 Python 标准库）。

提供五组能力：
  1. 健壮 IO：read_text_robust / tolerant_json / write_text / write_json
     容忍 BOM、坏编码（utf-8-sig → utf-8 → latin-1 → replace），单文件 2MB 上限截断，
     坏 JSON 不抛异常并尽力打捞顶层平衡对象。
  2. 输入发现：discover_input 递归扫描、稳定排序、跳过超大/疑似二进制文件并记录。
  3. 结果骨架：build_skeleton 回填 output_contract 全部键，含 case_id/status/sources/
     uncertainty/pending_human/errors/degradations/change_log/runtime。
  4. SVG 生成：render_spec 确定性生成分层可编辑 SVG（mm 坐标系、<g> 图层、
     可编辑 <text>、箭头/抑制符/包含框/图例/标注），相同 spec 相同输出。
  5. 校验：validate_result 检查契约字段、证据溯源、SVG/PNG/PDF 有效性。

设计原则：任何输入都不应让进程崩溃；失败返回可解释的 (值, errors)。
"""

import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

SVG_NS = "http://www.w3.org/2000/svg"

FILE_CAP = 2 * 1024 * 1024            # 单文件 2MB 上限
TOTAL_CAP = 40 * 1024 * 1024          # 输入总量 40MB 提示线
ALLOWED_STATUS = {"complete", "partial", "failed"}
ALLOWED_FONT_CHECK = {"embedded", "none", "replaced", ""}
TEXT_EXT = {
    ".txt", ".md", ".json", ".csv", ".tsv", ".yaml", ".yml", ".xml", ".svg",
    ".html", ".tex", ".bib", ".faa", ".fasta", ".fa", ".bed", ".gff", ".gtf",
    ".vcf", ".nwk", ".obo",
}

# 关系图形语义：类型 -> 端点标记、线型、默认颜色
REL_STYLE = {
    "activation": {"marker": "arrow", "stroke": "#1b1b1b", "dash": None},
    "inhibition": {"marker": "tee",   "stroke": "#1b1b1b", "dash": None},
    "contains":   {"marker": "none",  "stroke": "#4a4a4a", "dash": None},
    "parallel":   {"marker": "none",  "stroke": "#4a4a4a", "dash": "3,2"},
    "upstream":   {"marker": "arrow", "stroke": "#1b1b1b", "dash": None},
    "downstream": {"marker": "arrow", "stroke": "#1b1b1b", "dash": None},
    "causal":     {"marker": "arrow", "stroke": "#1b1b1b", "dash": None},
    "temporal":   {"marker": "arrow", "stroke": "#1b1b1b", "dash": "3,2"},
    "mapping":    {"marker": "arrow", "stroke": "#5a5a5a", "dash": "2,2"},
}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------
# 1. 健壮 IO
# --------------------------------------------------------------------------

def read_text_robust(path, cap=FILE_CAP):
    """读取文本文件，容忍 BOM/坏编码/超限。返回 (text, meta)。绝不抛异常。"""
    meta = {
        "path": path, "size_bytes": 0, "encoding": None, "truncated": False,
        "binary": False, "error": None,
    }
    if not path or not os.path.exists(path):
        meta["error"] = "missing"
        return "", meta
    try:
        size = os.path.getsize(path)
    except OSError as e:
        meta["error"] = "stat: %s" % e
        return "", meta
    meta["size_bytes"] = size
    if size == 0:
        meta["encoding"] = "utf-8"
        return "", meta
    if size > cap:
        meta["truncated"] = True
    try:
        with open(path, "rb") as f:
            raw = f.read(min(size, cap))
    except OSError as e:
        meta["error"] = "read: %s" % e
        return "", meta
    if b"\x00" in raw[:8192]:
        meta["binary"] = True
        return "", meta
    text = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            meta["encoding"] = "utf-8" if enc == "utf-8-sig" else enc
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
        meta["encoding"] = "utf-8"
        meta["error"] = "decode-fallback-replace"
    if meta["truncated"]:
        text = text + "\n[TRUNCATED: file exceeds %d bytes]" % cap
    return text, meta


def _salvage_json(text):
    """从坏 JSON 文本中打捞最后一个顶层平衡对象前缀；失败返回 None。"""
    starts = [i for i, ch in enumerate(text) if ch in "{["]
    if not starts:
        return None
    for s in reversed(starts[-3:]):
        opening = text[s]
        closing = "}" if opening == "{" else "]"
        depth = 0
        in_str = False
        escaped = False
        end = None
        for i in range(s, len(text)):
            ch = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == opening:
                depth += 1
            elif ch == closing:
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end:
            return text[s:end]
    return None


def tolerant_json(path):
    """容错读取 JSON：坏 JSON 返回 (None, errors)，不抛异常。"""
    text, meta = read_text_robust(path)
    if meta.get("error") == "missing":
        return None, ["missing: %s" % path]
    if meta.get("binary"):
        return None, ["binary file treated as JSON: %s" % path]
    if not text.strip():
        return None, ["empty JSON: %s" % path]
    try:
        return json.loads(text), []
    except json.JSONDecodeError as e:
        salvaged = _salvage_json(text)
        if salvaged is not None:
            try:
                obj = json.loads(salvaged)
                return obj, ["JSON partially salvaged: %s (%s)" % (path, e)]
            except json.JSONDecodeError:
                pass
        return None, ["invalid JSON: %s (%s)" % (path, e)]


def write_text(path, text):
    """写 UTF-8 文本；目录不存在则创建。失败抛 OSError 由调用方降级。"""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def write_json(path, obj):
    write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


# --------------------------------------------------------------------------
# 2. 输入发现
# --------------------------------------------------------------------------

def discover_input(input_dir, cap=FILE_CAP, total_cap=TOTAL_CAP):
    """递归发现输入文件，稳定排序，跳过超大/二进制并记录。返回 (files, errors)。"""
    if not input_dir or not os.path.isdir(input_dir):
        return [], ["input dir missing or not a directory: %s" % input_dir]
    files = []
    errors = []
    total = 0
    try:
        walk = list(os.walk(input_dir))
    except OSError as e:
        return [], ["cannot walk %s: %s" % (input_dir, e)]
    for root, dirs, names in walk:
        dirs.sort()
        for n in sorted(names):
            p = os.path.join(root, n)
            try:
                size = os.path.getsize(p)
            except OSError as e:
                errors.append("stat failed %s: %s" % (p, e))
                continue
            rel = os.path.relpath(p, input_dir).replace("\\", "/")
            files.append({
                "path": p, "rel": rel, "size_bytes": size,
                "oversize": size > cap,
                "likely_binary": not _is_text_like(p),
            })
            total += size
    files.sort(key=lambda x: x["rel"])
    if total > total_cap:
        errors.append("total input %d bytes exceeds %d advisory cap; large files read as header text only" % (total, total_cap))
    return files, errors


def _is_text_like(path):
    return os.path.splitext(path)[1].lower() in TEXT_EXT


def find_by_name(files, name):
    """在发现清单里按 basename（忽略大小写）找文件，返回第一个或 None。"""
    target = name.lower()
    for f in files:
        if os.path.basename(f["rel"]).lower() == target:
            return f
    return None


# --------------------------------------------------------------------------
# 3. 结果骨架
# --------------------------------------------------------------------------

def _contract_fields(contract):
    """把 output_contract 解析成 [(字段名, 定义dict或None), ...]，不回填已有键。"""
    out = []
    if isinstance(contract, dict):
        for k, v in contract.items():
            out.append((str(k), v if isinstance(v, dict) else None))
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, dict):
                name = item.get("name") or item.get("field") or item.get("key")
                if name:
                    out.append((str(name), item))
            elif isinstance(item, str):
                out.append((item, None))
    return out


def build_skeleton(case_id=None, contract=None, status="partial", note=""):
    """生成 result.json 骨架：回填 output_contract 全部键 + 标准字段。"""
    result = {}
    for name, spec in _contract_fields(contract):
        if isinstance(spec, dict) and "default" in spec:
            result[name] = spec["default"]
        else:
            result[name] = None
    entry = {
        "time": now_iso(),
        "action": "scaffold",
        "detail": note or "初始 partial 骨架落盘",
    }
    result.update({
        "case_id": case_id or "unknown",
        "status": status,
        "editable_source_files": [],
        "export_files": [],
        "provenance": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "change_log": [entry],
        "runtime": {
            "started_iso": now_iso(),
            "ended_iso": None,
            "elapsed_s": None,
            "model_requests_used": None,
        },
    })
    return result


# --------------------------------------------------------------------------
# 4. SVG 生成（确定性、分层、mm 坐标系）
# --------------------------------------------------------------------------

def _esc(s):
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))


def _char_w(ch, size):
    # 东亚全角字符按 1.0*size，其余按 0.58*size 估算宽度
    return size * 1.0 if ord(ch) > 0x2E80 else size * 0.58


def _text_w(s, size):
    if not s:
        return 0.0
    return sum(_char_w(c, size) for c in str(s))


def _wrap_text(s, max_w, size):
    s = str(s)
    if _text_w(s, size) <= max_w:
        return [s]
    words = s.split()
    if len(words) > 1:
        lines, cur = [], ""
        for wd in words:
            trial = wd if not cur else cur + " " + wd
            if not cur or _text_w(trial, size) <= max_w:
                cur = trial
            else:
                lines.append(cur)
                cur = wd
        if cur:
            lines.append(cur)
        return lines[:3]
    lines, cur = [], ""
    for ch in s:
        if cur and _text_w(cur + ch, size) > max_w:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines[:3]


def _layer_entities(entities, relations):
    """确定性最长路径分层：pred 推进，id 稳定排序，DAG/含环都收敛。"""
    ids = sorted(set(e.get("id") for e in entities if e.get("id")))
    pred = {i: [] for i in ids}
    for r in relations:
        f, t = r.get("from"), r.get("to")
        if f in pred and t in pred and f != t:
            pred[t].append(f)
    layer = {i: 0 for i in ids}
    for _ in range(len(ids) + 1):
        changed = False
        for i in ids:
            if pred[i]:
                lv = max(layer[p] + 1 for p in pred[i])
                if lv > layer[i]:
                    layer[i] = lv
                    changed = True
        if not changed:
            break
    return layer


def _layout_entities(entities, relations, x0, y0, x1, y1):
    layer = _layer_entities(entities, relations)
    by_layer = {}
    for e in entities:
        by_layer.setdefault(layer.get(e.get("id"), 0), []).append(e)
    max_layer = max(by_layer) if by_layer else 0
    n_cols = max_layer + 1
    col_w = (x1 - x0) / max(1, n_cols)
    out = []
    for lv in sorted(by_layer):
        items = sorted(by_layer[lv], key=lambda e: str(e.get("id")))
        row_h = (y1 - y0) / max(1, len(items))
        for k, e in enumerate(items):
            size = float(e.get("font_size") or 3.5)
            label = e.get("label") or e.get("id") or "?"
            pad = float(e.get("pad") or 1.4)
            if e.get("w") is not None:
                ew = float(e["w"])
            else:
                ew = min(col_w * 0.82, max(12.0, _text_w(label, size) + 2 * pad))
            if e.get("h") is not None:
                eh = float(e["h"])
            else:
                eh = size * 1.7 + 2 * pad
            if e.get("x") is not None:
                ex = float(e["x"])
            else:
                ex = x0 + col_w * lv + (col_w - ew) / 2.0
            if e.get("y") is not None:
                ey = float(e["y"])
            else:
                ey = y0 + row_h * k + (row_h - eh) / 2.0
            merged = dict(e)
            merged.update({"_x": ex, "_y": ey, "_w": ew, "_h": eh})
            out.append(merged)
    return out


def _edge_point(cx, cy, w, h, px, py):
    """从矩形中心 (cx,cy) 朝 (px,py) 方向到矩形边界的交点。"""
    dx = px - cx
    dy = py - cy
    if dx == 0 and dy == 0:
        return (cx, cy + h / 2.0)
    hw, hh = w / 2.0, h / 2.0
    sx = hw / abs(dx) if dx else float("inf")
    sy = hh / abs(dy) if dy else float("inf")
    s = min(sx, sy)
    return (cx + dx * s, cy + dy * s)


def _legend_width(items, size=3.2):
    if not items:
        return 0.0
    m = max(_text_w((it.get("label") or ""), size) for it in items)
    return min(60.0, max(18.0, m + 12.0))


def _render_legend(items, x, y, font_family, size=3.2):
    parts = []
    gap = 5.2
    for i, it in enumerate(items):
        yy = y + i * gap
        color = it.get("color") or "#1b1b1b"
        shape = it.get("shape") or "rect"
        sw = 1.3
        if shape == "circle":
            parts.append('<circle cx="%.2f" cy="%.2f" r="1.3" fill="%s"/>' % (x, yy, _esc(color)))
        elif shape == "line":
            parts.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="%s" stroke-width="0.4"/>' % (x - sw, yy, x + sw, yy, _esc(color)))
        elif shape == "arrow":
            parts.append('<path d="M %.2f %.2f L %.2f %.2f" stroke="%s" stroke-width="0.4" marker-end="url(#arrow-head)"/>' % (x - sw, yy, x + sw, yy, _esc(color)))
        else:
            parts.append('<rect x="%.2f" y="%.2f" width="2.6" height="2.6" fill="%s" stroke="#1b1b1b" stroke-width="0.2"/>' % (x - sw, yy - sw, _esc(color)))
        lab = it.get("label") or ""
        val = it.get("value")
        txt = ("%s: %s" % (lab, val)) if (val is not None and str(val) != "") else lab
        parts.append('<text x="%.2f" y="%.2f" font-family="%s" font-size="%.2f" fill="#1b1b1b">%s</text>' % (x + 3.0, yy + size * 0.32, _esc(font_family), size, _esc(txt)))
    return "\n".join(parts)


def render_spec(spec):
    """把 figure_spec 渲染为分层可编辑 SVG 字符串。相同 spec 输出逐字节一致。"""
    spec = spec or {}
    canvas = spec.get("canvas") or {}
    w = float(canvas.get("width_mm") or 180)
    h = float(canvas.get("height_mm") or 120)
    margin = float(canvas.get("margin_mm") or 6)
    bg = canvas.get("background") or "#ffffff"
    font_family = spec.get("font_family") or "DejaVu Sans, Arial, sans-serif"
    title = spec.get("title") or ""
    entities = [e for e in (spec.get("entities") or []) if isinstance(e, dict)]
    relations = [r for r in (spec.get("relations") or []) if isinstance(r, dict)]
    groups = [g for g in (spec.get("groups") or []) if isinstance(g, dict)]
    legend_items = [it for it in ((spec.get("legend") or {}).get("items") or []) if isinstance(it, dict)]
    annotations = [a for a in (spec.get("annotations") or []) if isinstance(a, dict)]

    legend_w = _legend_width(legend_items) if legend_items else 0.0
    legend_gap = 6.0 if legend_w else 0.0
    x0 = margin
    y0 = margin + (8.0 if title else 0.0)
    x1 = w - margin - legend_w - legend_gap
    y1 = h - margin
    ents = _layout_entities(entities, relations, x0, y0, x1, y1)
    pos = {e.get("id"): e for e in ents}

    L = []
    L.append('<svg xmlns="%s" width="%gmm" height="%gmm" viewBox="0 0 %g %g" font-family="%s" role="img">'
             % (SVG_NS, w, h, w, h, _esc(font_family)))
    L.append('<title>%s</title>' % _esc(title))
    L.append('<desc>Generated by sci-schematic-editor; layered editable SVG.</desc>')
    L.append('<defs>')
    L.append('<marker id="arrow-head" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="3" markerHeight="3" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#1b1b1b"/></marker>')
    L.append('<marker id="tee-head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="3" markerHeight="3" markerUnits="userSpaceOnUse" orient="auto"><path d="M9,0 L9,10" stroke="#1b1b1b" stroke-width="1.4"/></marker>')
    L.append('</defs>')

    L.append('<g id="layer-background">')
    L.append('<rect x="0" y="0" width="%g" height="%g" fill="%s"/>' % (w, h, _esc(bg)))
    if title:
        L.append('<text x="%g" y="%g" font-size="4.5" font-weight="bold" fill="#1b1b1b">%s</text>' % (margin, margin, _esc(title)))
    L.append('</g>')

    L.append('<g id="layer-groups">')
    for g in groups:
        members = [e for e in ents if e.get("group") == g.get("id")]
        gx, gy, gw, gh = g.get("x"), g.get("y"), g.get("w"), g.get("h")
        if members:
            pad = 3.0
            xmin = min(e["_x"] for e in members)
            ymin = min(e["_y"] for e in members)
            xmax = max(e["_x"] + e["_w"] for e in members)
            ymax = max(e["_y"] + e["_h"] for e in members)
            gx, gy, gw, gh = xmin - pad, ymin - pad, xmax - xmin + 2 * pad, ymax - ymin + 2 * pad
        if gx is None or gy is None:
            continue
        gw = float(gw) if gw is not None else 20.0
        gh = float(gh) if gh is not None else 12.0
        glabel = g.get("label") or g.get("id") or ""
        L.append('<rect x="%g" y="%g" width="%g" height="%g" fill="none" stroke="#7a7a7a" stroke-width="0.3" stroke-dasharray="3,2" rx="1.5"/>' % (gx, gy, gw, gh))
        if glabel:
            L.append('<text x="%g" y="%g" font-size="3.2" fill="#4a4a4a">%s</text>' % (gx + 1.5, gy + 3.0, _esc(glabel)))
    L.append('</g>')

    L.append('<g id="layer-relations">')
    for r in relations:
        f = pos.get(r.get("from"))
        t = pos.get(r.get("to"))
        if not f or not t:
            continue
        fc = (f["_x"] + f["_w"] / 2.0, f["_y"] + f["_h"] / 2.0)
        tc = (t["_x"] + t["_w"] / 2.0, t["_y"] + t["_h"] / 2.0)
        p1 = _edge_point(fc[0], fc[1], f["_w"], f["_h"], tc[0], tc[1])
        p2 = _edge_point(tc[0], tc[1], t["_w"], t["_h"], fc[0], fc[1])
        rtype = r.get("type") or "activation"
        st = REL_STYLE.get(rtype, REL_STYLE["activation"])
        color = r.get("color") or st["stroke"]
        dash = r.get("dash") if r.get("dash") is not None else st["dash"]
        marker = r.get("marker") or st["marker"]
        width = float(r.get("stroke_width") or 0.25)
        dash_attr = (' stroke-dasharray="%s"' % _esc(str(dash))) if dash else ""
        marker_id = {"arrow": "arrow-head", "tee": "tee-head"}.get(marker)
        end_attr = (' marker-end="url(#%s)"' % marker_id) if marker_id else ""
        L.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="%s" stroke-width="%g"%s%s/>'
                 % (p1[0], p1[1], p2[0], p2[1], _esc(color), width, dash_attr, end_attr))
        label = r.get("label") or ""
        if label:
            mx = (p1[0] + p2[0]) / 2.0
            my = (p1[1] + p2[1]) / 2.0
            L.append('<text x="%g" y="%g" font-size="3.0" fill="#1b1b1b" text-anchor="middle">%s</text>' % (mx, my - 0.8, _esc(label)))
    L.append('</g>')

    L.append('<g id="layer-entities">')
    for e in ents:
        label = e.get("label") or e.get("id") or "?"
        size = float(e.get("font_size") or 3.5)
        fill = e.get("fill") or "#eef3f7"
        stroke = e.get("stroke") or "#1b1b1b"
        sw = float(e.get("stroke_width") or 0.3)
        role = e.get("role") or "node"
        shape = e.get("shape") or ("ellipse" if role == "process" else "rect")
        x, y, ew, eh = e["_x"], e["_y"], e["_w"], e["_h"]
        if shape == "ellipse":
            L.append('<ellipse cx="%g" cy="%g" rx="%g" ry="%g" fill="%s" stroke="%s" stroke-width="%g"/>' % (x + ew / 2, y + eh / 2, ew / 2, eh / 2, _esc(fill), _esc(stroke), sw))
        elif shape == "rounded":
            L.append('<rect x="%g" y="%g" width="%g" height="%g" rx="1.2" fill="%s" stroke="%s" stroke-width="%g"/>' % (x, y, ew, eh, _esc(fill), _esc(stroke), sw))
        else:
            L.append('<rect x="%g" y="%g" width="%g" height="%g" fill="%s" stroke="%s" stroke-width="%g"/>' % (x, y, ew, eh, _esc(fill), _esc(stroke), sw))
        lines = _wrap_text(label, ew - 2.0, size)
        lh = size * 1.25
        total_h = lh * len(lines)
        ty = y + eh / 2.0 - total_h / 2.0 + size * 0.95
        for ln in lines:
            L.append('<text x="%g" y="%g" font-size="%g" fill="#1b1b1b" text-anchor="middle">%s</text>' % (x + ew / 2.0, ty, size, _esc(ln)))
            ty += lh
    L.append('</g>')

    if legend_items:
        L.append('<g id="layer-legend">')
        L.append(_render_legend(legend_items, x1 + legend_gap + 2.0, y0, font_family, 3.2))
        L.append('</g>')

    L.append('<g id="layer-annotations">')
    for a in annotations:
        ax = a.get("x") if a.get("x") is not None else margin
        ay = a.get("y") if a.get("y") is not None else h - 2.0
        size = float(a.get("font_size") or 3.0)
        color = a.get("color") or "#1b1b1b"
        L.append('<text x="%g" y="%g" font-size="%g" fill="%s">%s</text>' % (float(ax), float(ay), size, _esc(color), _esc(a.get("text") or "")))
    L.append('</g>')

    L.append('</svg>')
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------
# 5. 校验
# --------------------------------------------------------------------------

def validate_result(result, output_dir):
    """校验 result.json 契约、证据溯源与产物有效性。返回报告 dict（含 ok/errors/warnings）。"""
    report = {"ok": True, "errors": [], "warnings": [], "checks": 0}

    def add(level, msg):
        report["checks"] += 1
        report[level].append(msg)
        if level == "errors":
            report["ok"] = False

    if not isinstance(result, dict):
        add("errors", "result.json is not a JSON object")
        report["summary"] = "not an object"
        return report

    if not result.get("case_id"):
        add("errors", "case_id missing or empty")
    if result.get("status") not in ALLOWED_STATUS:
        add("errors", "status '%s' not in %s" % (result.get("status"), sorted(ALLOWED_STATUS)))
    for k in ("editable_source_files", "export_files", "provenance", "sources",
              "uncertainty", "pending_human", "errors", "degradations",
              "change_log", "runtime"):
        if k not in result:
            add("errors", "missing contract field '%s'" % k)

    if result.get("status") == "complete":
        if not result.get("editable_source_files"):
            add("errors", "status=complete but editable_source_files empty")
        if not result.get("export_files"):
            add("errors", "status=complete but export_files empty")
        if not result.get("provenance"):
            add("warnings", "status=complete but provenance empty")

    for i, p in enumerate(result.get("provenance") or []):
        if isinstance(p, dict) and p.get("confirmed") is True:
            for f in ("element", "source_ref", "quote"):
                if not p.get(f):
                    add("warnings", "provenance[%d].%s empty for confirmed item" % (i, f))

    out = output_dir or "."
    for f in result.get("editable_source_files") or []:
        if not isinstance(f, dict):
            continue
        path = f.get("path")
        if not path:
            add("warnings", "editable_source_files item missing path")
            continue
        full = path if os.path.isabs(path) else os.path.join(out, path)
        if not os.path.exists(full):
            add("errors", "editable source missing: %s" % path)
            continue
        if (f.get("kind") or "").lower() == "svg":
            _check_svg(full, add, path)

    for f in result.get("export_files") or []:
        if not isinstance(f, dict):
            continue
        path = f.get("path")
        fmt = (f.get("format") or "").lower()
        full = path if os.path.isabs(path) else os.path.join(out, path)
        if not os.path.exists(full):
            add("errors", "export missing: %s" % path)
            continue
        _check_magic(full, fmt, add, path)
        if not (f.get("width_px") and f.get("height_px") and f.get("dpi")):
            add("warnings", "export %s missing width/height/dpi metadata" % path)
        if f.get("font_check") not in ALLOWED_FONT_CHECK:
            add("warnings", "export %s font_check '%s' not in %s" % (path, f.get("font_check"), sorted(ALLOWED_FONT_CHECK)))

    report["summary"] = "%d errors, %d warnings" % (len(report["errors"]), len(report["warnings"]))
    return report


def _check_svg(path, add, rel):
    try:
        tree = ET.parse(path)
        root = tree.getroot()
        ns = root.tag.split("}")[0].strip("{") if "}" in root.tag else ""
        tag = lambda name: "{%s}%s" % (ns, name) if ns else name
        gs = root.findall(".//" + tag("g"))
        ts = root.findall(".//" + tag("text"))
        if not gs:
            add("warnings", "%s: no <g> layers found" % rel)
        if not ts:
            add("warnings", "%s: no editable <text> found" % rel)
    except Exception as e:
        add("errors", "%s: SVG not parseable: %s" % (rel, e))


def _check_magic(path, fmt, add, rel):
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError as e:
        add("errors", "%s: cannot read: %s" % (rel, e))
        return
    if fmt == "png":
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            add("errors", "%s: not a valid PNG (magic mismatch)" % rel)
    elif fmt == "pdf":
        if not head.startswith(b"%PDF-"):
            add("errors", "%s: not a valid PDF (missing %%PDF- header)" % rel)
    elif fmt in ("svg", "eps"):
        pass
    else:
        add("warnings", "%s: unknown format '%s', magic check skipped" % (rel, fmt))


def mm_to_px(mm, dpi=300):
    return int(round(float(mm) / 25.4 * float(dpi)))


if __name__ == "__main__":
    # 直接执行时做最小自检：空 spec 也能渲染出合法 SVG
    svg = render_spec({"title": "smoke", "entities": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
                       "relations": [{"from": "a", "to": "b", "type": "activation"}]})
    ET.fromstring(svg)
    print("smoke ok, svg bytes =", len(svg))
