#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FigureIR 中间表示、确定性布局、SVG 构建、校验与渲染探测（纯标准库）。

本模块只做机械工作：归一化输入、生成/校验中间表示、按确定性网格布局绘制分层 SVG、
探测可用的 SVG→PNG/PDF 渲染器。科学抽取（实体/关系方向/数据编码/evidence 的
locator+quote）由上层 LLM 在 extract 阶段填写 fig_ir.json；脚本不根据领域标签补造数据。
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

VERSION = "1.1.0"
CANVAS_W = 1000
CANVAS_H = 700
MIN_FONT = 8
MAX_FILE_BYTES = 2 * 1024 * 1024

# 色觉友好 + 灰度可分离的面板主色（4 要素各一色）
PANEL_COLORS = {
    "question": "#4477AA",
    "method": "#CC6677",
    "results": "#117733",
    "conclusion": "#AA4499",
}
RELATION_COLOR = "#444444"
PANEL_KINDS = ("question", "method", "results", "conclusion")
DEFAULT_SCHEMA = {
    "case_id": None,
    "status": "partial",
    "graphical_abstract": {
        "question": None,
        "method": None,
        "results": None,
        "conclusion": None,
    },
    "editable_source": {
        "path": None,
        "layers": ["layer-background", "layer-panels", "layer-labels"],
        "text_editable": True,
        "renderer": "native-svg",
    },
    "exports": [],
    "entities": [],
    "relations": [],
    "data_mapping": [],
    "legend": [],
    "sources": [],
    "uncertainty": [],
    "pending_human": [],
    "change_log": [],
    "limitations": [],
    "runtime": {"seconds": 0.0, "script_version": VERSION},
    "artifacts": [],
}


def _fmt(s: str) -> str:
    """基础 XML/属性转义。"""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def read_text(path: Path, max_bytes: int = MAX_FILE_BYTES) -> Tuple[str, List[str]]:
    """按 utf-8-sig 容错读取文本；超限或失败返回 ('', degradations)。"""
    deg: List[str] = []
    try:
        if not path.exists():
            return "", [f"missing:{path}"]
        size = path.stat().st_size
        if size > max_bytes:
            return "", [f"oversize_skipped:{path}({size}B>{max_bytes}B)"]
        raw = path.read_bytes()
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                return raw.decode(enc), deg
            except UnicodeDecodeError:
                continue
        deg.append(f"bad_encoding:{path}")
        return raw.decode("utf-8", errors="replace"), deg
    except OSError as exc:
        return "", [f"io_error:{path}:{exc}"]


def load_json(path: Path) -> Tuple[Any, List[str]]:
    text, deg = read_text(path)
    if not text:
        return None, deg
    try:
        return json.loads(text), deg
    except json.JSONDecodeError as exc:
        deg.append(f"bad_json:{path}:{exc}")
        return None, deg


def _g(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return default


def normalize_case(input_dir: Path) -> Tuple[Dict[str, Any], List[str]]:
    """把 /input 下的 case.json + task.md 归一化成统一结构。缺 case.json 也返回骨架。"""
    deg: List[str] = []
    case: Dict[str, Any] = {"case_id": None, "output_contract": {}, "facts": {},
                            "materials": [], "task_text": "", "raw": {}}
    cj = input_dir / "case.json"
    raw, d = load_json(cj)
    deg.extend(d)
    if isinstance(raw, dict):
        case["raw"] = raw
        case["case_id"] = _g(raw, "case_id") or _g(raw, "id") or _g(raw, "challenge_id")
        contract = _g(raw, "output_contract")
        if isinstance(contract, dict):
            case["output_contract"] = contract
        # 兼容多种材料键名
        for key in ("materials", "material", "sources", "documents"):
            v = _g(raw, key)
            if isinstance(v, list):
                case["materials"].extend(x if isinstance(x, str) else json.dumps(x, ensure_ascii=False) for x in v)
        # raw.facts 是主事实来源，先整体并入；顶层同名键再覆盖
        raw_facts = _g(raw, "facts")
        if isinstance(raw_facts, dict):
            case["facts"].update(raw_facts)
        for key in ("entities", "relations", "data", "figure", "abstract"):
            v = _g(raw, key)
            if v is not None:
                case["facts"][key] = v
    tm = input_dir / "task.md"
    ttext, d = read_text(tm)
    deg.extend(d)
    case["task_text"] = ttext
    return case, deg


def _first_entities(case: Dict[str, Any]) -> List[Dict[str, Any]]:
    facts = case.get("facts") or {}
    for key in ("entities", "entity", "objects"):
        v = _g(facts, key)
        if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            return v
    # 极简回退：从 materials 的列表项生成占位实体，标记待人工确认
    mats = case.get("materials") or []
    if mats:
        return [{"id": f"e{i}", "label": f"material_{i}",
                 "kind": "unknown", "source": None,
                 "pending": "label_from_material"} for i in range(min(len(mats), 8))]
    return []


def extract_figure_ir(case: Dict[str, Any], work_dir: Path) -> Tuple[Dict[str, Any], List[str]]:
    """从归一化案例提取/初始化 FigureIR。已存在的 work/fig_ir.json 优先复用。"""
    deg: List[str] = []
    ir_path = work_dir / "fig_ir.json"
    if ir_path.exists():
        existing, d = load_json(ir_path)
        deg.extend(d)
        if isinstance(existing, dict) and _g(existing, "schema") == "figure-ir":
            existing.setdefault("case_id", case.get("case_id"))
            return existing, deg

    facts = case.get("facts") or {}
    entities = _first_entities(case)
    rels = _g(facts, "relations") if isinstance(_g(facts, "relations"), list) else []
    data = _g(facts, "data_mapping") if isinstance(_g(facts, "data_mapping"), list) else \
        _g(facts, "data") if isinstance(_g(facts, "data"), list) else []
    legend = _g(facts, "legend") if isinstance(_g(facts, "legend"), list) else []
    evidence = _g(facts, "evidence") if isinstance(_g(facts, "evidence"), list) else []

    panels = []
    for kind in PANEL_KINDS:
        blk = _g(facts, kind)
        blocks: List[Dict[str, Any]] = []
        if isinstance(blk, dict):
            blocks = [{"id": f"{kind}_b{i}", "text": t, "kind": "text", "source": None}
                      for i, t in enumerate([_g(blk, "text"), _g(blk, "summary")]) if t]
        elif isinstance(blk, str) and blk.strip():
            blocks = [{"id": f"{kind}_b0", "text": blk, "kind": "text", "source": None}]
        panels.append({"id": kind, "title": kind.capitalize(), "kind": "text",
                       "x": 0, "y": 0, "w": 0, "h": 0, "blocks": blocks})

    ir: Dict[str, Any] = {
        "schema": "figure-ir",
        "version": 1,
        "case_id": case.get("case_id"),
        "title": _g(facts, "title") or case.get("case_id"),
        "canvas": {"width": CANVAS_W, "height": CANVAS_H, "unit": "px"},
        "reading_order": list(PANEL_KINDS),
        "panels": panels,
        "entities": entities,
        "relations": rels,
        "data_mapping": data,
        "legend": legend,
        "evidence": evidence,
    }
    return ir, deg


def layout_panels(ir: Dict[str, Any]) -> Dict[str, Any]:
    """确定性网格布局：四面板横向等宽，坐标写回 panels。"""
    canvas = ir.get("canvas") or {"width": CANVAS_W, "height": CANVAS_H}
    w = int(canvas.get("width", CANVAS_W))
    h = int(canvas.get("height", CANVAS_H))
    n = max(1, len(ir.get("panels", [])))
    panel_w = w // n
    for i, p in enumerate(ir.get("panels", [])):
        p["x"] = i * panel_w
        p["y"] = 0
        p["w"] = panel_w
        p["h"] = h
    ir["canvas"] = canvas
    return ir


def _entity_rows(entities: List[Dict[str, Any]], panel_id: str) -> List[Dict[str, Any]]:
    return [e for e in entities if _g(e, "panel") == panel_id or
            (_g(e, "panel") is None and panel_id == "results")]


def build_svg(ir: Dict[str, Any], out_path: Path) -> Tuple[Path, List[str]]:
    """按布局绘制分层可编辑 SVG。"""
    notes: List[str] = []
    layout_panels(ir)
    canvas = ir.get("canvas") or {}
    W = int(canvas.get("width", CANVAS_W))
    H = int(canvas.get("height", CANVAS_H))
    entities = ir.get("entities") or []
    relations = ir.get("relations") or []
    data_map = ir.get("data_mapping") or []
    legend = ir.get("legend") or []

    body: List[str] = []
    body.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
                f'viewBox="0 0 {W} {H}" font-family="Arial, DejaVu Sans, sans-serif">')
    body.append(f'<rect class="layer-background" x="0" y="0" width="{W}" height="{H}" '
                f'fill="#FFFFFF"/>')
    body.append('<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" '
                'refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#444444"/>'
                '</marker></defs>')
    body.append('<title>%s</title>' % _fmt(ir.get("title") or "Graphical Abstract"))

    # 图例（若提供）放在顶部条带
    if legend:
        body.append(f'<g class="layer-labels" id="legend" transform="translate(10 10)">')
        lx = 0
        for i, lg in enumerate(legend):
            color = _g(lg, "color") or PANEL_COLORS.get(str(_g(lg, "channel", "")), "#666666")
            meaning = _fmt(_g(lg, "meaning") or f"channel_{i}")
            body.append(f'<rect x="{lx}" y="0" width="14" height="14" fill="{color}" '
                        f'stroke="#222222" data-source="{_fmt(_g(lg, "source") or "")}"/>')
            body.append(f'<text x="{lx + 18}" y="11" font-size="11" fill="#222222">{meaning}</text>')
            lx += 18 + 8 * max(4, len(meaning)) + 14
        body.append('</g>')

    # 面板背景与标题
    for p in ir.get("panels", []):
        x, y, pw, ph = (int(_g(p, "x", 0)), int(_g(p, "y", 0)),
                        int(_g(p, "w", 0)), int(_g(p, "h", 0)))
        if pw <= 0 or ph <= 0:
            continue
        color = PANEL_COLORS.get(str(p.get("id")), "#888888")
        body.append(f'<g class="layer-panels" id="{_fmt(p.get("id"))}">')
        body.append(f'<rect x="{x}" y="{y}" width="{pw}" height="{ph}" fill="none" '
                    f'stroke="{color}" stroke-width="2"/>')
        body.append(f'<text x="{x + 8}" y="{y + 22}" font-size="14" font-weight="bold" '
                    f'fill="{color}">{_fmt(p.get("title") or p.get("id"))}</text>')

        # 文本要点块
        ty = y + 42
        for b in _g(p, "blocks") or []:
            text = _fmt(_g(b, "text") or "")
            if text:
                body.append(f'<text x="{x + 8}" y="{ty}" font-size="12" fill="#111111" '
                            f'data-source="{_fmt(_g(b, "source") or "")}">{text}</text>')
                ty += 18
        # 实体框：面板内纵向堆叠
        rows = _entity_rows(entities, str(p.get("id")))
        ey = ty + 8
        for e in rows:
            if ey + 30 > y + ph - 6:
                notes.append(f"overflow_risk:panel={p.get('id')}:entity={_g(e, 'id')}")
                break
            eid = _fmt(_g(e, "id") or "")
            label = _fmt(_g(e, "label") or eid)
            shape = _g(e, "shape") or "rect"
            body.append(f'<g class="layer-panels entity" id="entity-{eid}">')
            if shape == "ellipse":
                body.append(f'<ellipse cx="{x + pw // 2}" cy="{ey + 15}" rx="{pw // 2 - 16}" '
                            f'ry="15" fill="#EEF3FA" stroke="{color}" stroke-width="1.5" '
                            f'data-source="{_fmt(_g(e, "source") or "")}" '
                            f'data-entity="{eid}"/>')
            else:
                body.append(f'<rect x="{x + 8}" y="{ey}" width="{pw - 16}" height="30" '
                            f'fill="#EEF3FA" stroke="{color}" stroke-width="1.5" '
                            f'data-source="{_fmt(_g(e, "source") or "")}" '
                            f'data-entity="{eid}"/>')
            body.append(f'<text x="{x + 12}" y="{ey + 19}" font-size="11" fill="#111111" '
                        f'data-source="{_fmt(_g(e, "source") or "")}" '
                        f'data-entity="{eid}">{label}</text>')
            body.append('</g>')
            ey += 38

        body.append('</g>')

    # 关系箭头：用连线连接实体框中心（无向则不加箭头）
    if relations:
        body.append('<g class="layer-panels relations" id="relations">')
        for r in relations:
            f, t = _g(r, "from"), _g(r, "to")
            pf = next((e for e in entities if _g(e, "id") == f), None)
            pt = next((e for e in entities if _g(e, "id") == t), None)
            if pf is None or pt is None:
                notes.append(f"unresolved_relation:{f}->{t}")
                continue
            direction = _g(r, "direction") or "forward"
            x1, y1 = _center(pf, ir)
            x2, y2 = _center(pt, ir)
            marker = ' marker-end="url(#arrow)"' if direction in ("forward", "backward") else ""
            if direction == "backward":
                x1, y1, x2, y2 = x2, y2, x1, y1
            body.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                        f'stroke="{RELATION_COLOR}" stroke-width="1.5"{marker} '
                        f'data-source="{_fmt(_g(r, "source") or "")}" '
                        f'data-relation="{_fmt(_g(r, "id") or "")}"/>')
            lbl = _fmt(_g(r, "label") or "")
            if lbl:
                body.append(f'<text x="{(x1 + x2) // 2 + 4}" y="{(y1 + y2) // 2 - 4}" '
                            f'font-size="10" fill="#333333">{lbl}</text>')
        body.append('</g>')

    # 数据编码标签：值 + 单位 + 比例
    if data_map:
        body.append('<g class="layer-labels data" id="data-labels">')
        for i, dm in enumerate(data_map):
            ent = next((e for e in entities if _g(e, "id") == _g(dm, "entity_id")), None)
            if ent is None:
                continue
            cx, cy = _center(ent, ir)
            val = _g(dm, "value")
            unit = _fmt(_g(dm, "unit") or "")
            scale = _fmt(_g(dm, "scale") or "")
            txt = f"{val}{unit}" + (f" ({scale})" if scale else "")
            body.append(f'<text x="{cx}" y="{cy - 24 - 6 * (i % 3)}" font-size="10" '
                        f'fill="#000000" data-source="{_fmt(_g(dm, "source") or "")}" '
                        f'data-dataid="{_fmt(_g(dm, "id") or "")}" '
                        f'data-mapping-id="{_fmt(_g(dm, "id") or "")}">{_fmt(txt)}</text>')
        body.append('</g>')

    body.append('</svg>')
    svg = "\n".join(body)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(svg, encoding="utf-8")
    return out_path, notes


def _center(e: Dict[str, Any], ir: Dict[str, Any]) -> Tuple[int, int]:
    """从实体所在面板推断其框中心坐标（构建 SVG 时与布局逻辑保持一致）。"""
    p = next((pp for pp in ir.get("panels", []) if _g(pp, "id") == _g(e, "panel")), None)
    if p is None:
        return (0, 0)
    x = int(_g(p, "x", 0))
    y = int(_g(p, "y", 0))
    pw = int(_g(p, "w", 0))
    ty = y + 42
    for b in _g(p, "blocks") or []:
        if _g(b, "text"):
            ty += 18
    rows = _entity_rows(ir.get("entities") or [], str(p.get("id")))
    ey = ty + 8
    for row in rows:
        if _g(row, "id") == _g(e, "id"):
            return (x + pw // 2, ey + 15)
        ey += 38
    return (x + pw // 2, ey + 15)


def validate_figure(svg_path: Path, ir: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """校验 SVG：可解析、分层、可编辑文本、图元溯源、溢出、字号、对比度。"""
    errors: List[str] = []
    warnings: List[str] = []
    if not svg_path.exists():
        return [f"missing_svg:{svg_path}"], []
    try:
        root = ET.parse(svg_path).getroot()
    except ET.ParseError as exc:
        return [f"svg_parse_error:{exc}"], []
    canvas = ir.get("canvas") or {}
    W = int(canvas.get("width", CANVAS_W))
    H = int(canvas.get("height", CANVAS_H))

    texts = root.findall(".//{http://www.w3.org/2000/svg}text")
    if not texts:
        errors.append("no_editable_text")
    path_text = root.findall(".//{http://www.w3.org/2000/svg}path")
    if path_text and not texts:
        warnings.append("text_may_be_path")
    groups = root.findall(".//{http://www.w3.org/2000/svg}g")
    layer_ids = set()
    for g in groups:
        for attr in ("id", "class"):
            v = g.get(attr)
            if v:
                for token in v.split():
                    if token.startswith("layer-"):
                        layer_ids.add(token)
    # 也认可非 g 元素上的 class="layer-*"（如背景矩形）
    for el in root.iter():
        cls = el.get("class")
        if cls:
            for token in cls.split():
                if token.startswith("layer-"):
                    layer_ids.add(token)
    if not layer_ids:
        errors.append("no_named_layers")

    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag not in ("rect", "ellipse", "line", "text", "circle"):
            continue
        src = el.get("data-source")
        if tag in ("rect", "ellipse", "line") and src in (None, ""):
            warnings.append(f"element_without_source:{el.get('id') or tag}")
        try:
            fs = float(el.get("font-size") or "12")
            if tag == "text" and fs < MIN_FONT:
                errors.append(f"font_below_min:{el.get('id') or el.text}")
        except ValueError:
            pass
        for attr, bound in (("x", W), ("y", H)):
            v = el.get(attr)
            if v is not None:
                try:
                    if float(v) < 0 or float(v) > bound:
                        warnings.append(f"coordinate_out_of_canvas:{el.get('id') or tag}:{attr}={v}")
                except ValueError:
                    pass

    # 图元溯源：实体/关系/数据必须都在 IR 中
    eids = {_g(e, "id") for e in ir.get("entities") or []}
    rids = {_g(r, "id") for r in ir.get("relations") or []}
    for el in root.iter():
        rid = el.get("data-relation")
        if rid and rid not in rids:
            errors.append(f"relation_not_in_ir:{rid}")
    if ir.get("relations"):
        for r in ir.get("relations", []):
            if _g(r, "from") not in eids or _g(r, "to") not in eids:
                errors.append(f"relation_endpoint_unknown:{_g(r, 'id')}")

    evidence = ir.get("evidence") or []
    if not evidence and (ir.get("entities") or ir.get("relations")):
        warnings.append("no_evidence_entries")
    for ev in evidence:
        if not _g(ev, "quote") or not _g(ev, "locator"):
            warnings.append(f"evidence_missing_quote_or_locator:{_g(ev, 'id')}")
    return errors, warnings


def detect_renderers() -> Dict[str, Optional[str]]:
    """探测 SVG→PNG/PDF 渲染器路径。跳过 Windows 系统自带 convert.exe。"""
    found: Dict[str, Optional[str]] = {"png": None, "pdf": None}
    for cmd in ("rsvg-convert", "inkscape", "magick", "chromium", "google-chrome"):
        p = shutil.which(cmd)
        if p and Path(p).name.lower() != "convert.exe":
            found["png"] = p
            break
    # cairosvg 常以 python -m cairosvg 形式存在，探测模块可导入性
    for cmd in ("rsvg-convert", "inkscape", "magick"):
        p = shutil.which(cmd)
        if p and Path(p).name.lower() != "convert.exe":
            found["pdf"] = p
            break
    if found["pdf"] is None:
        try:
            import importlib.util  # noqa: F401
            if importlib.util.find_spec("cairosvg"):
                found["pdf"] = "cairosvg:module"
        except (ImportError, ValueError):
            pass
    return found


def build_result(case_id: Any, status: str, ir: Optional[Dict[str, Any]],
                 change_log: List[Dict[str, Any]], extra: Optional[Dict[str, Any]] = None,
                 contract: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """组装规范 result.json（结构 + output_contract 全字段 + 导出件由调用方填充）。"""
    result = dict(DEFAULT_SCHEMA)
    result["case_id"] = case_id
    result["status"] = status
    result["change_log"] = list(change_log)
    if ir:
        result["entities"] = ir.get("entities") or []
        result["relations"] = ir.get("relations") or []
        result["data_mapping"] = ir.get("data_mapping") or []
        result["legend"] = ir.get("legend") or []
        result["sources"] = ir.get("evidence") or []
        result["graphical_abstract"] = {
            "question": _panel_text(ir, "question"),
            "method": _panel_text(ir, "method"),
            "results": _panel_text(ir, "results"),
            "conclusion": _panel_text(ir, "conclusion"),
        }
    if extra:
        result.update(extra)
    if contract:
        for k, v in contract.items():
            result.setdefault(k, None if v is None else v)
    return result


def _panel_text(ir: Dict[str, Any], panel_id: str) -> Optional[str]:
    for p in ir.get("panels", []):
        if _g(p, "id") == panel_id:
            blocks = " ".join(_g(b, "text") or "" for b in _g(p, "blocks") or [])
            return blocks or None
    return None


def main() -> int:
    print("core_impl: library module, no CLI", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
