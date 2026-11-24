#!/usr/bin/env python3
"""core_impl.py — deterministic figure pipeline (Python standard library only).

Inputs -> IR (entities/relations/data_encoding/legend_annotations) ->
deterministic layered SVG -> self-check -> honest result.json.

No network, no third-party dependencies. Same inputs produce the same outputs.
"""
from __future__ import annotations

import datetime
import html
import json
import math
import os
import shutil
from pathlib import Path

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

DEFAULT_CONTRACT = [
    "entities",
    "relations",
    "data_encoding",
    "legend_annotations",
    "editable_sources",
    "exports",
]

STATUSES = ("in_progress", "complete", "partial", "failed", "input_error")

FONT_SIZE = 14
NODE_MIN_W, NODE_MAX_W, NODE_H = 96, 320, 54
MARGIN = 60
MAX_CANVAS = 4000

EDGE_STYLES = {
    "flow":     {"stroke": "#0072B2", "dash": None,  "label": "物料/能量流向"},
    "energy":   {"stroke": "#E69F00", "dash": None,  "label": "能量流向"},
    "info":     {"stroke": "#CC79A7", "dash": "4,3", "label": "信息/信号"},
    "sequence": {"stroke": "#333333", "dash": None,  "label": "先后顺序"},
    "branch":   {"stroke": "#D55E00", "dash": None,  "label": "分流/支路"},
    "recycle":  {"stroke": "#009E73", "dash": "6,4", "label": "循环/回流"},
    "waste":    {"stroke": "#999999", "dash": None,  "label": "废料/出口"},
}

KIND_ALIASES = {
    "back": "recycle", "backflow": "recycle", "reflux": "recycle",
    "loop": "recycle", "return": "recycle", "recirculate": "recycle",
    "split": "branch", "divide": "branch", "join": "branch", "merge": "branch",
    "signal": "info", "information": "info", "control": "info",
    "material": "flow", "liquid": "flow", "gas": "flow", "mass": "flow",
    "step": "sequence", "next": "sequence", "then": "sequence",
    "solid": "flow",
}


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def now_ts() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def json_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def write_json(path, obj) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json_dumps(obj) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return str(p)


def read_text(path, max_bytes=2_000_000):
    p = Path(path)
    if not p.exists():
        return None, f"missing: {p}"
    try:
        size = p.stat().st_size
    except OSError as exc:
        return None, f"stat failed: {exc}"
    if size > max_bytes:
        return None, f"oversize ({size}>{max_bytes} bytes)"
    data = p.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc), None
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace"), "fallback decode"


def read_json(path, max_bytes=2_000_000):
    text, err = read_text(path, max_bytes)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"bad JSON: {exc}"


def normalize_contract(contract):
    """output_contract may be dict/list/str; return a stable list of field names."""
    if isinstance(contract, dict):
        return list(contract.keys())
    if isinstance(contract, (list, tuple)):
        return [str(x) for x in contract]
    if isinstance(contract, str) and contract.strip():
        return [contract]
    return None


def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def esc(value) -> str:
    return html.escape(str(value), quote=True)


# --------------------------------------------------------------------------- #
# Result manager
# --------------------------------------------------------------------------- #

class ResultManager:
    """Early partial skeleton -> merge -> finalize, all idempotent."""

    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        if self.path.exists():
            obj, _ = read_json(self.path)
            if isinstance(obj, dict):
                return obj
        return {}

    def init(self, case_id, title="", kind="", contract=None):
        contract = normalize_contract(contract) or DEFAULT_CONTRACT
        r = {
            "case_id": case_id,
            "title": title,
            "kind": kind,
            "status": "in_progress",
            "sources": [],
            "uncertainty": [],
            "pending_human": [],
            "limitations": [],
            "checks": [],
            "artifacts": [],
            "change_log": [{"ts": now_ts(), "action": "init", "msg": "partial skeleton written"}],
        }
        for field in set(DEFAULT_CONTRACT) | set(contract):
            r.setdefault(field, [])
        write_json(self.path, r)
        return r

    def merge(self, partial):
        r = self.load()
        if not r:
            r = {"change_log": []}
        log = r.setdefault("change_log", [])
        for key, value in partial.items():
            if key == "change_log":
                log.extend(value)
            else:
                r[key] = value
        log.append({"ts": now_ts(), "action": "merge", "msg": "merged partial results"})
        write_json(self.path, r)
        return r

    def finalize(self, status="partial", artifacts=None):
        r = self.load()
        if status not in STATUSES:
            status = "partial"
        r["status"] = status
        if artifacts is not None:
            r["artifacts"] = artifacts
        r.setdefault("change_log", []).append(
            {"ts": now_ts(), "action": "finalize", "msg": f"status={status}"})
        write_json(self.path, r)
        return r


# --------------------------------------------------------------------------- #
# IR normalization and layout
# --------------------------------------------------------------------------- #

def normalize_kind(kind):
    key = (kind or "").strip().lower()
    return KIND_ALIASES.get(key, key)


def est_text_width(label, size=FONT_SIZE):
    if not label:
        return NODE_MIN_W
    cjk = sum(1 for ch in label if ord(ch) > 0x2E80)
    asc = len(label) - cjk
    return cjk * size + asc * size * 0.62 + 28


def assign_ids(entities):
    used = set()
    out = []
    for index, ent in enumerate(entities):
        if isinstance(ent, str):
            ent = {"label": ent}
        ent = dict(ent)
        if not str(ent.get("id", "")).strip():
            base = f"e{index + 1}"
            while base in used:
                index += 1
                base = f"e{index}"
            ent["id"] = base
        used.add(ent["id"])
        out.append(ent)
    return out


def compute_layout(ir):
    """Assign deterministic x/y; honour explicit coordinates where present."""
    entities = assign_ids(ir.get("entities", []))
    kind = (ir.get("kind") or "").lower()
    widths = [clamp(est_text_width(e.get("label", "")), NODE_MIN_W, NODE_MAX_W) for e in entities]
    placed = [e.get("x") is not None and e.get("y") is not None for e in entities]
    auto = not all(placed) or not entities

    if kind in ("flow", "route"):
        x0 = MARGIN + 60
        y = MARGIN + 40
        for i, ent in enumerate(entities):
            if not placed[i]:
                ent["x"] = x0
                ent["y"] = y
            y += NODE_H + 70
    else:
        n = len(entities)
        cols = max(1, math.ceil(math.sqrt(n)))
        max_w = max(widths, default=NODE_MIN_W)
        for i, ent in enumerate(entities):
            if not placed[i]:
                row, col = divmod(i, cols)
                ent["x"] = MARGIN + col * (max_w + 90)
                ent["y"] = MARGIN + 50 + row * (NODE_H + 70)

    width = MARGIN * 2
    height = MARGIN * 2
    for ent, w in zip(entities, widths):
        ent["_w"] = w
        ent["_h"] = NODE_H
        width = max(width, ent["x"] + w + MARGIN)
        height = max(height, ent["y"] + NODE_H + MARGIN)
    height += 90  # room for legend band
    return entities, int(width), int(height), auto


# --------------------------------------------------------------------------- #
# SVG building
# --------------------------------------------------------------------------- #

def rect_exit(cx, cy, w, h, tx, ty, gap=6.0):
    """Point where the line (cx,cy)->(tx,ty) exits the node rectangle."""
    dx = tx - cx
    dy = ty - cy
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return cx, cy
    scale = float("inf")
    if abs(dx) > 1e-9:
        scale = min(scale, (w / 2 + gap) / abs(dx))
    if abs(dy) > 1e-9:
        scale = min(scale, (h / 2 + gap) / abs(dy))
    return cx + dx * scale, cy + dy * scale


def build_edges(relations, entities):
    by_id = {e["id"]: e for e in entities}
    edges = []
    for rel in relations:
        src, dst = rel.get("from"), rel.get("to")
        a, b = by_id.get(src), by_id.get(dst)
        if a is None or b is None:
            edges.append({"error": f"dangling edge {src} -> {dst}"})
            continue
        kind = normalize_kind(rel.get("kind"))
        style = EDGE_STYLES.get(kind, EDGE_STYLES["flow"])
        x1, y1 = a["x"] + a["_w"] / 2, a["y"] + a["_h"] / 2
        x2, y2 = b["x"] + b["_w"] / 2, b["y"] + b["_h"] / 2
        sx, sy = rect_exit(x1, y1, a["_w"], a["_h"], x2, y2)
        ex, ey = rect_exit(x2, y2, b["_w"], b["_h"], x1, y1)
        back = (rel.get("direction") or "forward").lower() in ("back", "reverse", "backward")
        dash = f' stroke-dasharray="{style["dash"]}"' if style["dash"] else ""
        marker = ' marker-start="url(#arr)"' if back else ' marker-end="url(#arr)"'
        edges.append({
            "id": f"{src}-{dst}-{kind}",
            "kind": kind, "style": style, "back": back,
            "sx": sx, "sy": sy, "ex": ex, "ey": ey,
            "dash": dash, "marker": marker,
            "ref": rel.get("source_ref", ""),
        })
    return edges


def build_legend(ir, relations):
    ann = ir.get("legend_annotations") or []
    if ann:
        return [dict(a) for a in ann]
    seen = {}
    for rel in relations:
        kind = normalize_kind(rel.get("kind"))
        if kind not in seen and kind in EDGE_STYLES:
            seen[kind] = rel.get("source_ref", "")
    return [
        {
            "key": kind,
            "visual": EDGE_STYLES[kind]["stroke"],
            "line_style": EDGE_STYLES[kind]["dash"],
            "label": EDGE_STYLES[kind]["label"],
            "source_ref": ref,
        }
        for kind, ref in seen.items()
    ]


def build_svg(ir, out_path):
    entities, width, height, auto = compute_layout(ir)
    relations = ir.get("relations", [])
    edges = build_edges(relations, entities)
    legend = build_legend(ir, relations)
    title = esc(ir.get("title") or "Figure")
    parts = []

    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
        f'width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
    )
    parts.append('  <defs>')
    parts.append(
        '    <marker id="arr" viewBox="0 0 10 10" refX="8" refY="5" '
        'markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" fill="#333333"/></marker>'
    )
    parts.append('  </defs>')

    # background + title layer
    parts.append('  <g id="layer-background" data-role="background" '
                 'inkscape:groupmode="layer" inkscape:label="background">')
    parts.append(f'    <rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>')
    parts.append(f'    <text x="{MARGIN}" y="28" font-size="18" font-family="sans-serif" '
                 f'font-weight="bold" fill="#111111">{title}</text>')
    if auto:
        parts.append(f'    <text x="{MARGIN}" y="48" font-size="11" font-family="sans-serif" '
                     f'fill="#888888">auto-layout: deterministic grid/vertical; refine '
                     f'x/y in ir.json when needed</text>')
    parts.append('  </g>')

    # relations layer
    parts.append('  <g id="layer-relations" data-role="relations" '
                 'inkscape:groupmode="layer" inkscape:label="relations">')
    for edge in edges:
        if edge.get("error"):
            continue
        parts.append(
            f'    <path id="rel-{esc(edge["id"])}" data-role="relation" '
            f'data-kind="{esc(edge["kind"])}" data-source-ref="{esc(edge["ref"])}" '
            f'd="M {edge["sx"]:.1f} {edge["sy"]:.1f} L {edge["ex"]:.1f} {edge["ey"]:.1f}" '
            f'fill="none" stroke="{edge["style"]["stroke"]}" stroke-width="2"'
            f'{edge["dash"]}{edge["marker"]}/>'
        )
    parts.append('  </g>')

    # entities layer
    parts.append('  <g id="layer-entities" data-role="entities" '
                 'inkscape:groupmode="layer" inkscape:label="entities">')
    for ent in entities:
        x, y, w, h = ent["x"], ent["y"], ent["_w"], ent["_h"]
        label = esc(ent.get("label", ent.get("id", "")))
        parts.append(
            f'    <g id="entity-{esc(ent["id"])}" data-role="entity" '
            f'data-type="{esc(ent.get("type", ""))}" data-source-ref="{esc(ent.get("source_ref", ""))}">'
        )
        parts.append(f'      <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" '
                     f'fill="#f2f7fb" stroke="#0072B2" stroke-width="1.5"/>')
        parts.append(f'      <text x="{x + w / 2}" y="{y + h / 2 - 2}" font-size="{FONT_SIZE}" '
                     f'font-family="sans-serif" text-anchor="middle" fill="#111111">{label}</text>')
        parts.append(f'      <text x="{x + w / 2}" y="{y + h / 2 + 14}" font-size="10" '
                     f'font-family="sans-serif" text-anchor="middle" fill="#888888">'
                     f'{esc(ent["id"])}</text>')
        parts.append('    </g>')
    parts.append('  </g>')

    # annotations layer: data_encoding on the right column
    parts.append('  <g id="layer-annotations" data-role="annotations" '
                 'inkscape:groupmode="layer" inkscape:label="annotations">')
    data_enc = ir.get("data_encoding") or []
    if data_enc:
        ax = max(MARGIN + 10, width - 380)
        parts.append(f'    <text x="{ax}" y="60" font-size="12" font-family="sans-serif" '
                     f'font-weight="bold" fill="#333333">数据编码 (data_encoding)</text>')
        for i, item in enumerate(data_enc):
            item = item if isinstance(item, dict) else {"key": item}
            key = item.get("key", "")
            mapping = item.get("mapping", "")
            text = f"{key} → {mapping}" if mapping else str(key)
            parts.append(
                f'    <text x="{ax}" y="{76 + i * 16}" font-size="11" font-family="sans-serif" '
                f'fill="#555555" data-source-ref="{esc(item.get("source_ref", ""))}">'
                f'{esc(text)}</text>'
            )
    parts.append('  </g>')

    # legend layer
    parts.append('  <g id="layer-legend" data-role="legend" '
                 'inkscape:groupmode="layer" inkscape:label="legend">')
    if legend:
        lx = MARGIN
        ly = height - MARGIN - 8
        parts.append(f'    <rect x="{lx - 8}" y="{ly - 4 - len(legend) * 18}" width="300" '
                     f'height="{len(legend) * 18 + 10}" fill="#ffffff" stroke="#cccccc" rx="4"/>')
        for i, lg in enumerate(legend):
            y = ly - i * 18
            key = normalize_kind(lg.get("key", ""))
            stroke = esc(lg.get("visual", EDGE_STYLES.get(key, {}).get("stroke", "#0072B2")))
            dash = lg.get("line_style") or ""
            dashattr = f' stroke-dasharray="{esc(dash)}"' if dash else ""
            parts.append(f'    <line x1="{lx}" y1="{y}" x2="{lx + 26}" y2="{y}" '
                         f'stroke="{stroke}" stroke-width="2"{dashattr} marker-end="url(#arr)"/>')
            parts.append(f'    <text x="{lx + 34}" y="{y + 4}" font-size="11" '
                         f'font-family="sans-serif" fill="#333333">{esc(lg.get("label", key))}</text>')
    parts.append('  </g>')
    parts.append('</svg>')

    svg_text = "\n".join(parts) + "\n"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(svg_text, encoding="utf-8")
    return {
        "svg": str(out_path),
        "width": width,
        "height": height,
        "layers": ["background", "relations", "entities", "annotations", "legend"],
        "auto_layout": auto,
        "title": ir.get("title", ""),
        "n_entities": len(entities),
        "n_relations": len(relations),
    }


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

def validate(ir, svg_path):
    checks = []
    entities = assign_ids(ir.get("entities", []))
    ids = {e.get("id") for e in entities}
    relations = ir.get("relations", [])
    legend = ir.get("legend_annotations") or []

    try:
        import xml.etree.ElementTree as ET
        ET.parse(svg_path)
        checks.append({"id": "svg_parse", "status": "pass", "detail": "SVG 可解析"})
    except Exception as exc:  # noqa: BLE001
        checks.append({"id": "svg_parse", "status": "fail", "detail": str(exc)})

    dangling = [f"{r.get('from')}->{r.get('to')}" for r in relations
                if r.get("from") not in ids or r.get("to") not in ids]
    checks.append({"id": "dangling_edges", "status": "pass" if not dangling else "fail",
                   "detail": dangling or "无断头箭头"})

    no_ref_e = [e.get("id") for e in entities if not str(e.get("source_ref", "")).strip()]
    checks.append({"id": "entity_source_ref", "status": "pass" if not no_ref_e else "fail",
                   "detail": no_ref_e or "全部实体有 source_ref"})

    no_ref_r = [i for i, r in enumerate(relations) if not str(r.get("source_ref", "")).strip()]
    checks.append({"id": "relation_source_ref", "status": "pass" if not no_ref_r else "fail",
                   "detail": [f"relations[{i}]" for i in no_ref_r] or "全部关系有 source_ref"})

    if not legend:
        checks.append({"id": "legend_coverage", "status": "pass",
                       "detail": "图例由 used relation kinds 自动生成"})
    else:
        used = {normalize_kind(r.get("kind")) for r in relations}
        legend_keys = {normalize_kind(lg.get("key", "")) for lg in legend}
        missing = sorted(used - legend_keys)
        checks.append({"id": "legend_coverage", "status": "pass" if not missing else "fail",
                       "detail": missing or "图例覆盖所有关系类型"})

    _, width, height, _ = compute_layout(ir)
    overflow = [e.get("id") for e in entities
                if e.get("x", 0) < 0 or e.get("y", 0) < 0
                or e.get("x", 0) + e.get("_w", 0) > width
                or e.get("y", 0) + e.get("_h", 0) > height]
    checks.append({"id": "canvas_overflow", "status": "pass" if not overflow else "fail",
                   "detail": overflow or "无画布溢出"})

    big = width > MAX_CANVAS or height > MAX_CANVAS
    checks.append({"id": "canvas_size", "status": "warn" if big else "pass",
                   "detail": f"{width}x{height}" + (" 超过建议上限" if big else "")})
    return checks


# --------------------------------------------------------------------------- #
# Bundle (copy artifacts, merge into result.json)
# --------------------------------------------------------------------------- #

def bundle(ir_path, meta_path, checks_path, result_path, export_files):
    ir, _ = read_json(ir_path) or ({}, "")
    ir = ir if isinstance(ir, dict) else {}
    meta, _ = read_json(meta_path) or ({}, "")
    meta = meta if isinstance(meta, dict) else {}
    checks, _ = read_json(checks_path)
    if checks is None:
        checks = []
    elif isinstance(checks, dict):
        checks = checks.get("checks", checks)

    relations = ir.get("relations", [])
    legend = ir.get("legend_annotations") or []
    if not legend:
        legend = build_legend(ir, relations)

    out_dir = Path(result_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    editable_sources = []
    exports = []
    svg_src = meta.get("svg")
    if svg_src and Path(svg_src).exists():
        dst = out_dir / Path(svg_src).name
        shutil.copy2(svg_src, dst)
        editable_sources.append({
            "path": str(dst),
            "format": "svg",
            "layers": meta.get("layers", []),
            "notes": "分层可编辑矢量源文件；元素带 data-source-ref 溯源属性",
        })
        exports.append({
            "path": str(dst),
            "format": "svg",
            "width": meta.get("width"),
            "height": meta.get("height"),
            "dpi": None,
            "fonts": "系统默认 sans-serif；中文字体缺失见 limitations",
            "color_check": "未做打印校色",
            "render_status": "rendered",
        })

    for f in export_files or []:
        if f and Path(f).exists():
            dst = out_dir / Path(f).name
            shutil.copy2(f, dst)
            ext = Path(f).suffix.lower().lstrip(".") or "bin"
            exports.append({
                "path": str(dst),
                "format": ext,
                "width": meta.get("width"),
                "height": meta.get("height"),
                "dpi": None,
                "fonts": "取决于渲染器",
                "color_check": "未做打印校色",
                "render_status": "rendered",
            })

    partial = {
        "title": ir.get("title") or meta.get("title", ""),
        "kind": ir.get("kind", ""),
        "entities": ir.get("entities", []),
        "relations": relations,
        "data_encoding": ir.get("data_encoding", []),
        "legend_annotations": legend,
        "sources": ir.get("sources", []),
        "uncertainty": ir.get("uncertainty", []),
        "checks": checks,
        "editable_sources": editable_sources,
        "exports": exports,
    }
    limitations = list(ir.get("limitations", []))
    if not editable_sources:
        limitations.append("未生成可编辑源文件/导出件")
    if limitations:
        partial["limitations"] = limitations

    ResultManager(result_path).merge(partial)
    return partial
