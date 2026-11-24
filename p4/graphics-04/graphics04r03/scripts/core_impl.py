#!/usr/bin/env python3
"""traceable-figure-skill 核心实现：IR 读取、确定性布局、SVG 生成、渲染、自检、收尾。

只依赖 Python 标准库；同输入两次运行，SVG 逐字节一致（科学内容部分确定，
时间戳与运行时计数除外，见 canonicalize()）。CLI 入口见 main.py。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections import OrderedDict
from html import escape as _html_escape

# 默认契约六类字段（当 case.json 未声明 output_contract 时使用）。
DEFAULT_CONTRACT_FIELDS = [
    "entities", "relations", "data_encoding",
    "legend_annotations", "editable_sources", "exports",
]

VOLATILE_PATHS = ("runtime",)           # 规范化对比时剔除的顶层键
VOLATILE_TS_PATHS = ("change_log",)      # 其元素的 ts 字段剔除


# --------------------------------------------------------------------------- #
# 基础 IO：utf-8-sig 容错，坏 JSON 降级为 {} 或 []
# --------------------------------------------------------------------------- #
def read_text(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def read_json(path):
    text = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def read_ir(ir_path):
    ir = read_json(ir_path)
    if not isinstance(ir, dict):
        return None
    for key in ("entities", "relations", "data_encoding",
                "legend_annotations", "sources", "uncertainty", "pending_human"):
        ir.setdefault(key, [])
    ir.setdefault("meta", {})
    return ir


# --------------------------------------------------------------------------- #
# 规范化（确定性对比）：递归排序键；剔除时间戳与运行时计数
# --------------------------------------------------------------------------- #
def canonicalize(obj):
    if isinstance(obj, dict):
        out = OrderedDict()
        for k in sorted(obj.keys()):
            v = obj[k]
            if isinstance(v, dict) and k in VOLATILE_PATHS:
                out[k] = "<volatile>"
                continue
            if isinstance(v, list) and k in VOLATILE_TS_PATHS:
                stripped = []
                for item in v:
                    if isinstance(item, dict):
                        item2 = {kk: vv for kk, vv in item.items() if kk != "ts"}
                        stripped.append(canonicalize(item2))
                    else:
                        stripped.append(canonicalize(item))
                out[k] = stripped
                continue
            out[k] = canonicalize(v)
        return out
    if isinstance(obj, list):
        return [canonicalize(x) for x in obj]
    return obj


def canonical_json(obj):
    return json.dumps(canonicalize(obj), ensure_ascii=False, sort_keys=True)


def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# init：落盘骨架（partial 结果，越早越好）
# --------------------------------------------------------------------------- #
def load_contract(case):
    """从 case.json 读取 output_contract 字段名；缺失则用默认六类。"""
    contract = case.get("output_contract") if isinstance(case, dict) else None
    if isinstance(contract, dict):
        names = []
        for key in ("fields", "required", "schema"):
            val = contract.get(key)
            if isinstance(val, list) and all(isinstance(x, str) for x in val):
                names.extend(val)
            elif isinstance(val, dict):
                names.extend(val.keys())
        names = [n for n in names if n and not n.startswith("_")]
        if names:
            return names
    return list(DEFAULT_CONTRACT_FIELDS)


def init_workspace(case_path, work_dir, out_dir):
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)
    case = read_json(case_path)
    case_id = None
    if isinstance(case, dict):
        case_id = case.get("case_id") or case.get("id") or None
    contract_fields = load_contract(case) if case else list(DEFAULT_CONTRACT_FIELDS)
    result = {
        "case_id": case_id,
        "status": "in_progress",
        "primary_artifact": None,
        "output_contract_fields": contract_fields,
    }
    for f in contract_fields:
        result.setdefault(f, [])
    result.update({
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "ts": now_iso(),
            "action": "init skeleton",
            "detail": "首条落盘；后续只做增量改进",
        }],
        "limitations": [],
        "checks": [],
        "artifacts": [],
        "runtime": {"started_at": now_iso(), "requests_used": 0},
    })
    out_path = os.path.join(out_dir, "result.json")
    write_json(out_path, result)
    # 同时写一份 IR 骨架，便于 Agent 逐条填充（含 source_ref / quote 模板）。
    ir_skeleton = {
        "meta": {"case_id": case_id, "kind": None, "title": None,
                 "canvas": {"width": 1200, "height": 900}},
        "entities": [], "relations": [], "data_encoding": [],
        "legend_annotations": [], "sources": [], "uncertainty": [],
        "pending_human": [], "notes": [],
    }
    write_json(os.path.join(work_dir, "ir.json"), ir_skeleton)
    return out_path


# --------------------------------------------------------------------------- #
# 确定性布局：拓扑分层 + 网格，无随机数
# --------------------------------------------------------------------------- #
def compute_layers(entities, relations):
    ids = [e.get("id") for e in entities if e.get("id")]
    idx = {eid: i for i, eid in enumerate(ids)}
    adj = OrderedDict((eid, []) for eid in ids)
    back_edges = []
    for r in relations:
        a, b = r.get("from"), r.get("to")
        if a in adj and b in adj:
            if r.get("direction") == "back" or r.get("style") in ("dashed", "dotted"):
                back_edges.append(r)
            else:
                adj[a].append(b)
    # Kahn 拓扑序（同层按文件顺序出队，保证确定性）
    indeg = {eid: 0 for eid in ids}
    for u in adj:
        for v in adj[u]:
            indeg[v] += 1
    queue = [eid for eid in ids if indeg[eid] == 0]
    order = []
    while queue:
        u = queue.pop(0)
        order.append(u)
        for v in adj[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
    # 最长路径分层（前向边）
    layer = {eid: 0 for eid in ids}
    for u in order:
        for v in adj[u]:
            if layer[v] < layer[u] + 1:
                layer[v] = layer[u] + 1
    # 环内剩余节点：按前驱最大层 +1 的确定性回退
    leftover = [eid for eid in ids if eid not in order]
    if leftover:
        pred_max = {}
        for r in relations:
            a, b = r.get("from"), r.get("to")
            if a in idx and b in leftover:
                pred_max[b] = max(pred_max.get(b, -1), layer.get(a, 0))
            if b in idx and a in leftover:
                pred_max[a] = max(pred_max.get(a, -1), layer.get(b, 0))
        for eid in leftover:
            layer[eid] = pred_max.get(eid, -1) + 1
    return layer, back_edges, idx


def layout_entities(entities, relations, canvas):
    W = max(0, int(canvas.get("width", 1200)))
    H = max(0, int(canvas.get("height", 900)))
    layer, back_edges, idx = compute_layers(entities, relations)
    # 显式坐标优先；缺省坐标用网格布局
    fixed = [e for e in entities if isinstance(e.get("x"), (int, float))
             and isinstance(e.get("y"), (int, float))]
    auto = [e for e in entities if e not in fixed]
    groups = OrderedDict()
    for e in auto:
        groups.setdefault(layer.get(e.get("id"), 0), []).append(e)
    for k in groups:
        groups[k].sort(key=lambda e: idx.get(e.get("id"), 0))
    NODE_W, NODE_H, DX, DY, MARGIN = 200, 60, 260, 92, 60
    max_col = max((len(g) for g in groups.values()), default=1)
    positions = {}
    for lvl, lst in groups.items():
        for rank, e in enumerate(lst):
            # 该层垂直居中
            offset = max(0, (max_col - len(lst))) * DY // 2
            positions[e["id"]] = (MARGIN + lvl * DX, MARGIN + rank * DY + offset)
    # 已有固定节点直接采用；同时并入画布范围计算
    coords = {}
    for e in entities:
        eid = e.get("id")
        if e in fixed:
            coords[eid] = (float(e["x"]), float(e["y"]))
        elif eid in positions:
            coords[eid] = positions[eid]
        else:
            coords[eid] = (MARGIN, MARGIN + idx.get(eid, 0) * DY)
    max_x = max((x for x, _ in coords.values()), default=MARGIN)
    max_y = max((y for _, y in coords.values()), default=MARGIN)
    need_w = int(max_x + NODE_W + MARGIN)
    need_h = int(max_y + NODE_H + MARGIN)
    out_w = max(W, need_w)
    out_h = max(H, need_h)
    return coords, layer, back_edges, out_w, out_h, NODE_W, NODE_H


# --------------------------------------------------------------------------- #
# SVG 生成：分层、箭头、图例、标注、source_ref 元数据
# --------------------------------------------------------------------------- #
def _fmt(n):
    """固定小数格式，保证逐字节一致。"""
    try:
        f = float(n)
    except (TypeError, ValueError):
        return "0"
    if f == int(f):
        return str(int(f))
    return ("%.2f" % f).rstrip("0").rstrip(".")


def _esc(text):
    return _html_escape(str(text if text is not None else ""), quote=True)


def _wrap_label(text, max_chars=14):
    text = str(text or "")
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


def build_svg(ir, svg_path):
    entities = [e for e in ir.get("entities", []) if isinstance(e, dict)]
    relations = [r for r in ir.get("relations", []) if isinstance(r, dict)]
    legend = [l for l in ir.get("legend_annotations", []) if isinstance(l, dict)]
    data_enc = [d for d in ir.get("data_encoding", []) if isinstance(d, dict)]
    canvas = ir.get("meta", {}).get("canvas", {}) or {}
    coords, layer, back_edges, W, H, NODE_W, NODE_H = \
        layout_entities(entities, relations, canvas)

    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append(
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
    )
    parts.append(
        '<metadata id="traceability">' + _esc(
            canonical_json({
                "entities": [
                    {"id": e.get("id"), "label": e.get("label"),
                     "type": e.get("type"), "source_ref": e.get("source_ref")}
                    for e in entities
                ],
                "relations": [
                    {"id": r.get("id"), "from": r.get("from"),
                     "to": r.get("to"), "kind": r.get("kind"),
                     "source_ref": r.get("source_ref")}
                    for r in relations
                ],
                "sources": ir.get("sources", []),
            })
        ) + '</metadata>'
    )

    def marker(mid, color):
        return (
            f'<marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" '
            f'markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
            f'<path d="M0,0 L10,5 L0,10 z" fill="{_esc(color)}"/></marker>'
        )

    colors = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd",
              "#ff7f0e", "#17becf", "#8c564b", "#e377c2"]
    kind_color = {}
    for i, r in enumerate(relations):
        k = r.get("kind") or "flow"
        kind_color.setdefault(k, colors[i % len(colors)])
    default_color = "#333333"

    # defs：为每种用到的颜色生成箭头 marker（顺序确定）
    used_colors = []
    for r in relations:
        a, b = r.get("from"), r.get("to")
        if a in coords and b in coords:
            color = kind_color.get(r.get("kind"), default_color)
            if color not in used_colors:
                used_colors.append(color)
    parts.append("<defs>")
    for color in used_colors:
        mid = "arrow-" + hashlib.md5(color.encode()).hexdigest()[:8]
        parts.append(marker(mid, color))
    parts.append("</defs>")

    # layer-relations（箭头与连线）
    parts.append('<g id="layer-relations">')
    for r in relations:
        a, b = r.get("from"), r.get("to")
        if a not in coords or b not in coords:
            continue
        color = kind_color.get(r.get("kind"), default_color)
        mid = "arrow-" + hashlib.md5(color.encode()).hexdigest()[:8]
        style = r.get("style") or "solid"
        dash = {"solid": "", "dashed": ' stroke-dasharray="8 6"',
                "dotted": ' stroke-dasharray="2 4"'}.get(style, "")
        x1, y1 = coords[a]
        x2, y2 = coords[b]
        x1r, y1c = x1 + NODE_W, y1 + NODE_H / 2
        x2l, y2c = x2, y2 + NODE_H / 2
        is_back = (r.get("direction") == "back") or style in ("dashed", "dotted")
        label = r.get("label")
        if is_back:
            # 回流走下方弧线
            d = f"M {_fmt(x1r)} {_fmt(y1c)} C {_fmt(x1r + 90)} {_fmt(y1c + 70)}, " \
                f"{_fmt(x2l - 90)} {_fmt(y2c + 70)}, {_fmt(x2l)} {_fmt(y2c)}"
        else:
            d = f"M {_fmt(x1r)} {_fmt(y1c)} L {_fmt(x2l)} {_fmt(y2c)}"
        marker_end = ' marker-end="url(#%s)"' % mid
        src = ' data-source-ref="%s"' % _esc(r.get("source_ref", ""))
        lbl = f'<text x="{_fmt((x1r + x2l) / 2)}" y="{_fmt((y1c + y2c) / 2 - 8)}" ' \
              f'font-size="12" fill="{_esc(color)}" text-anchor="middle">{_esc(label or "")}</text>'
        parts.append(
            f'<path d="{d}" fill="none" stroke="{_esc(color)}" stroke-width="2"{dash}'
            f'{marker_end}{src}/>'
        )
        if label:
            parts.append(lbl)
    parts.append("</g>")

    # layer-entities
    parts.append('<g id="layer-entities">')
    for e in entities:
        eid = e.get("id")
        if eid not in coords:
            continue
        x, y = coords[eid]
        etype = e.get("type") or "node"
        shape = "ellipse" if etype in ("stage", "milestone") else "rect"
        fill = "#eef4fb" if shape == "rect" else "#fdf6e3"
        stroke = "#2b6cb0" if shape == "rect" else "#b7791f"
        src = ' data-source-ref="%s"' % _esc(e.get("source_ref", ""))
        if shape == "rect":
            node = (f'<rect x="{_fmt(x)}" y="{_fmt(y)}" width="{NODE_W}" '
                    f'height="{NODE_H}" rx="8" fill="{fill}" stroke="{stroke}" '
                    f'stroke-width="1.5"{src}/>')
        else:
            node = (f'<ellipse cx="{_fmt(x + NODE_W / 2)}" cy="{_fmt(y + NODE_H / 2)}" '
                    f'rx="{NODE_W / 2}" ry="{NODE_H / 2}" fill="{fill}" '
                    f'stroke="{stroke}" stroke-width="1.5"{src}/>')
        lines = _wrap_label(e.get("label"), 13)
        ty = y + NODE_H / 2 - (len(lines) - 1) * 9
        texts = []
        for ln in lines:
            texts.append(
                f'<text x="{_fmt(x + NODE_W / 2)}" y="{_fmt(ty)}" font-size="14" '
                f'fill="#1a202c" text-anchor="middle" font-family="sans-serif">{_esc(ln)}</text>'
            )
            ty += 18
        parts.append(f'<g id="{_esc("ent-" + str(eid))}">{node}' + "".join(texts) + "</g>")
    parts.append("</g>")

    # layer-annotations（数据编码 + 标注）
    parts.append('<g id="layer-annotations">')
    for i, d in enumerate(data_enc):
        parts.append(
            f'<text x="24" y="{_fmt(24 + i * 20)}" font-size="12" fill="#4a5568" '
            f'font-family="sans-serif" data-source-ref="{_esc(d.get("source_ref", ""))}">'
            f'{_esc((d.get("param") or "") + ": " + (str(d.get("value")) if d.get("value") is not None else ""))}'
            f' → {_esc(str(d.get("maps_to") or ""))}</text>'
        )
    parts.append("</g>")

    # layer-legend
    parts.append('<g id="layer-legend">')
    if legend:
        lx, ly = max(24, W - 320), 24
        parts.append(
            f'<rect x="{_fmt(lx - 12)}" y="{_fmt(ly - 12)}" width="308" '
            f'height="{_fmt(24 + len(legend) * 26)}" rx="6" fill="#ffffff" '
            f'stroke="#cbd5e0" stroke-width="1"/>'
        )
        for i, lg in enumerate(legend):
            yy = ly + i * 26
            swatch_color = str(lg.get("color") or kind_color.get(lg.get("key"), default_color))
            parts.append(
                f'<line x1="{_fmt(lx)}" y1="{_fmt(yy + 5)}" x2="{_fmt(lx + 28)}" '
                f'y2="{_fmt(yy + 5)}" stroke="{_esc(swatch_color)}" stroke-width="3"/>'
            )
            parts.append(
                f'<text x="{_fmt(lx + 36)}" y="{_fmt(yy + 9)}" font-size="12" '
                f'fill="#1a202c" font-family="sans-serif" data-source-ref="{_esc(lg.get("source_ref", ""))}">'
                f'{_esc(str(lg.get("label") or lg.get("key") or ""))}</text>'
            )
    parts.append("</g>")

    parts.append("</svg>")
    svg = "\n".join(parts) + "\n"
    os.makedirs(os.path.dirname(svg_path) or ".", exist_ok=True)
    with open(svg_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(svg)
    return svg_path


# --------------------------------------------------------------------------- #
# 渲染：尽力而为的降级级联，失败如实标注
# --------------------------------------------------------------------------- #
def attempt_render(svg_path, out_dir):
    """返回 {"png": path|None, "pdf": path|None, "render_status": "...", "note": "..."}."""
    base = os.path.splitext(os.path.basename(svg_path))[0]
    png_path = os.path.join(out_dir, base + ".png")
    pdf_path = os.path.join(out_dir, base + ".pdf")
    status = "skipped"
    note = ""
    tried = []
    # 优先 python 内嵌模块
    try:
        import cairosvg  # type: ignore
        with open(svg_path, "rb") as fh:
            svg_bytes = fh.read()
        cairosvg.svg2png(bytestring=svg_bytes, write_to=png_path)
        cairosvg.svg2pdf(bytestring=svg_bytes, write_to=pdf_path)
        status = "ok"
        note = "cairosvg"
        tried.append("cairosvg")
    except Exception:
        pass
    if status != "ok":
        for exe, args_tpl in (
            ("rsvg-convert", ["{svg}", "-o", "{png}"]),
            ("inkscape", ["{svg}", "--export-type=png", "--export-filename={png}"]),
        ):
            if shutil.which(exe):
                tried.append(exe)
                try:
                    subprocess.run(
                        [exe] + [a.replace("{svg}", svg_path).replace("{png}", png_path)
                                 for a in args_tpl],
                        capture_output=True, text=True, timeout=120, check=False,
                    )
                    if os.path.exists(png_path):
                        status = "ok"
                        note = exe
                        break
                except Exception:
                    pass
    if status == "skipped":
        note = "无可用渲染器（cairosvg/rsvg-convert/inkscape 均不可用）；已尝试 " + ",".join(tried or ["none"])
    return {"png": png_path if status == "ok" else None,
            "pdf": pdf_path if status == "ok" else None,
            "render_status": status, "note": note}


# --------------------------------------------------------------------------- #
# 自检：SVG 可解析 / source_ref 覆盖 / 无断头箭头 / 画布溢出 / 图例一致
# --------------------------------------------------------------------------- #
def validate(ir, svg_path):
    checks = []
    def add(cid, severity, passed, detail):
        checks.append({"id": cid, "severity": severity, "passed": bool(passed), "detail": detail})

    # 1. SVG 可解析
    try:
        ET.parse(svg_path)
        add("svg_well_formed", "critical", True, "SVG 可被 XML 解析")
    except Exception as exc:
        add("svg_well_formed", "critical", False, "SVG 解析失败: %s" % exc)
        return checks

    entities = ir.get("entities", [])
    relations = ir.get("relations", [])
    ids = {e.get("id") for e in entities if e.get("id")}

    # 2. source_ref 覆盖
    missing_src = [e.get("id") for e in entities if not e.get("source_ref")]
    add("entity_source_ref", "critical", not missing_src,
        "实体缺 source_ref: %s" % (missing_src or "无"))

    # 3. 无断头箭头
    dangling = [r.get("id") for r in relations
                if r.get("from") not in ids or r.get("to") not in ids]
    add("no_dangling_arrow", "critical", not dangling,
        "断头关系: %s" % (dangling or "无"))

    # 4. 画布溢出
    coords, _, _, W, H, NODE_W, NODE_H = \
        layout_entities(entities, relations, ir.get("meta", {}).get("canvas", {}) or {})
    overflow = [eid for eid, (x, y) in coords.items()
                if x < 0 or y < 0 or x + NODE_W > W or y + NODE_H > H]
    add("canvas_fit", "critical", not overflow, "溢出实体: %s" % (overflow or "无"))

    # 5. 图例与图形一致性（图例声明的 key 在关系中用到，或关系 kind 都有图例）
    legend_keys = {lg.get("key") for lg in ir.get("legend_annotations", [])}
    rel_kinds = {r.get("kind") for r in relations}
    if rel_kinds and legend_keys:
        uncovered = rel_kinds - legend_keys
        add("legend_match", "warning", not uncovered,
            "关系 kind 未在图例声明: %s" % (sorted(uncovered) or "无"))
    else:
        add("legend_match", "info", True, "无关系或图例，跳过一致性检查")

    # 6. 前向流向方向提示（前向关系的 to 应在 from 右/下方；否则疑似反向）
    suspects = []
    for r in relations:
        a, b = r.get("from"), r.get("to")
        if a in coords and b in coords and r.get("direction") != "back":
            xa, ya = coords[a]
            xb, yb = coords[b]
            if xb + 1e-6 < xa and yb + 1e-6 < ya:
                suspects.append(r.get("id"))
    add("flow_direction", "warning", not suspects,
        "疑似反向（to 在 from 左上）: %s" % (suspects or "无"))
    return checks


def write_checks(checks, out_path):
    write_json(out_path, {
        "generated": now_iso(),
        "summary": {
            "critical_failed": sum(1 for c in checks if c["severity"] == "critical" and not c["passed"]),
            "warning_failed": sum(1 for c in checks if c["severity"] == "warning" and not c["passed"]),
            "total": len(checks),
        },
        "checks": checks,
    })
    return out_path


# --------------------------------------------------------------------------- #
# finalize：合并 IR / checks / 渲染结果 → result.json
# --------------------------------------------------------------------------- #
def finalize(work_dir, out_dir, status="partial", render_result=None):
    result_path = os.path.join(out_dir, "result.json")
    result = read_json(result_path) or {"case_id": None, "status": "in_progress"}
    ir = read_ir(os.path.join(work_dir, "ir.json")) or {}
    checks = (read_json(os.path.join(work_dir, "checks.json")) or {}).get("checks", [])

    contract_fields = result.get("output_contract_fields") or list(DEFAULT_CONTRACT_FIELDS)
    for f in contract_fields:
        if f in ("entities", "relations", "data_encoding", "legend_annotations"):
            result[f] = ir.get(f, [])
        elif f == "editable_sources":
            svg_rel = os.path.join("figure.svg")
            result[f] = [{
                "path": svg_rel, "format": "svg", "layers": [
                    "layer-relations", "layer-entities",
                    "layer-annotations", "layer-legend"],
                "notes": "分层可编辑矢量源；每个图元含 data-source-ref",
            }]
        elif f == "exports":
            exports = []
            if render_result and render_result.get("render_status") == "ok":
                for key in ("png", "pdf"):
                    if render_result.get(key):
                        exports.append({
                            "path": os.path.basename(render_result[key]),
                            "format": key, "width": None, "height": None,
                            "dpi": None, "fonts": "未检测",
                            "color_check": "未自动检测", "render_status": "ok",
                        })
            result[f] = exports
        else:
            result.setdefault(f, [])

    result["sources"] = ir.get("sources", [])
    result["uncertainty"] = ir.get("uncertainty", [])
    result["pending_human"] = ir.get("pending_human", [])
    result["limitations"] = result.get("limitations", []) or []
    result["checks"] = checks
    result["status"] = status
    result["primary_artifact"] = result.get("primary_artifact") or "figure.svg"
    result["change_log"] = result.get("change_log", [])
    result["change_log"].append({
        "ts": now_iso(), "action": "finalize", "detail": "status=%s" % status,
    })
    # 把工作目录内的可编辑 SVG 复制到输出目录（若尚未存在），保证主产物在 /app/output。
    src_svg = os.path.join(work_dir, "figure.svg")
    dst_svg = os.path.join(out_dir, "figure.svg")
    if os.path.isfile(src_svg) and not os.path.isfile(dst_svg):
        try:
            shutil.copyfile(src_svg, dst_svg)
        except OSError:
            pass
    # artifacts：列出输出目录内实际存在的文件（相对路径）
    artifacts = []
    if os.path.isdir(out_dir):
        for name in sorted(os.listdir(out_dir)):
            if name.endswith(".tmp"):
                continue
            p = os.path.join(out_dir, name)
            if os.path.isfile(p):
                artifacts.append({
                    "path": name, "bytes": os.path.getsize(p),
                    "sha256": hashlib.sha256(read_text(p).encode("utf-8", "replace")).hexdigest()
                    if name.endswith((".json", ".svg")) else None,
                })
    result["artifacts"] = artifacts
    write_json(result_path, result)
    return result_path
