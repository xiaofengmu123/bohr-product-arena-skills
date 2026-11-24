"""Graphical-abstract core implementation (standard library only).

This module holds the deterministic pipeline used by scripts/main.py. It is
intentionally free of third-party dependencies so the Skill can run on any
Python 3.8+ interpreter available to the platform.

Design contract
---------------
1. Every function is deterministic for the same input (fixed palette, fixed
   layout grid, stable JSON key order via ``sort_keys=False`` + explicit order).
2. Nothing is fabricated: missing values stay ``null``/``[]`` and are recorded
   in ``uncertainty``/``limitations``. PNG/PDF previews are only claimed when a
   real renderer produced a real file with the correct magic bytes.
3. The first disk artifact is always a valid ``result.json`` skeleton with
   ``status="partial"``, written before any fact extraction.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from xml.dom import minidom
from xml.etree import ElementTree as ET

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATUS_VALUES = ("completed", "partial", "failed")

CANONICAL_SCHEMA = {
    "graphical_abstract": {"question": None, "method": None,
                           "results": None, "conclusion": None},
    "editable_source": {
        "path": None, "format": "svg", "layers": [],
        "text_editable": True, "renderer": None,
    },
    "exports": [],
    "entities": [],
    "relations": [],
    "data_mapping": [],
    "legend": [],
    "sources": [],
    "uncertainty": [],
    "limitations": [],
    "pending_human": [],
    "errors": [],
    "degradations": [],
    "change_log": [],
    "runtime": {},
}

# Phase budget: 900 s wall clock / 64 model requests, with an early-skeleton
# hard deadline. The ledger persists this so each phase can be re-checked.
BUDGET_PLAN = [
    {"stage": "skeleton",  "clock_s": 60,  "requests": 4,
     "deadline_s": 180,
     "checkpoint": "result.json (status=partial) + stage_ledger.json written"},
    {"stage": "facts",     "clock_s": 90,  "requests": 12,
     "checkpoint": "figure_spec.json populated with entities/relations/data"},
    {"stage": "build",     "clock_s": 60,  "requests": 6,
     "checkpoint": "layered SVG + fig_manifest.json + attempted previews"},
    {"stage": "validate",  "clock_s": 60,  "requests": 8,
     "checkpoint": "validation report written; defects listed"},
    {"stage": "improve",   "clock_s": 360, "requests": 20,
     "checkpoint": "each rewrite bumps change_log; re-run build+validate"},
    {"stage": "finalize",  "clock_s": 60,  "requests": 6,
     "checkpoint": "status set; limitations/uncertainty filled"},
    {"stage": "reserve",   "clock_s": 120, "requests": 6,
     "checkpoint": "renderer fallback, error handling, re-checks"},
]

# Okabe-Ito colorblind-safe palette (also distinguishable in grayscale).
PALETTE = {
    "question": "#4477AA", "method": "#117733", "results": "#CC6677",
    "conclusion": "#882255", "neutral": "#666666", "arrow": "#333333",
    "ink": "#222222", "panel_fill": "#F7F7F7", "canvas": "#FFFFFF",
    "good": "#228833", "warn": "#CCBB44", "bad": "#BB5566",
}

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PDF_MAGIC = b"%PDF-"

DEFAULT_CANVAS = {
    "width": 1600, "height": 1000, "unit": "px",
    "font_family": "Arial, Helvetica, sans-serif", "min_font_size": 10,
    "margin": 40,
}

# Renderers probed with shutil.which, in preference order. Each entry is
# (name, args_builder). ``args_builder(svg, out, width, height)`` returns argv.
PNG_RENDERERS = [
    ("rsvg-convert", lambda s, o, w, h: ["rsvg-convert", "-w", str(w),
                                         "-h", str(h), "-o", o, s]),
    ("inkscape", lambda s, o, w, h: ["inkscape", "--export-type=png",
                                     f"--export-filename={o}", "-w", str(w), s]),
    ("chromium", lambda s, o, w, h: ["chromium", "--headless", "--disable-gpu",
                                     f"--screenshot={o}",
                                     f"--window-size={w},{h}",
                                     "file://" + os.path.abspath(s)]),
    ("google-chrome", lambda s, o, w, h: ["google-chrome", "--headless",
                                          "--disable-gpu", f"--screenshot={o}",
                                          f"--window-size={w},{h}",
                                          "file://" + os.path.abspath(s)]),
    ("wkhtmltoimage", lambda s, o, w, h: ["wkhtmltoimage", "--width", str(w),
                                          s, o]),
    ("magick", lambda s, o, w, h: ["magick", "-density", "150", s, "-resize",
                                   f"{w}x{h}", o]),
    ("convert", lambda s, o, w, h: ["convert", "-density", "150", s,
                                    "-resize", f"{w}x{h}", o]),
]

PDF_RENDERERS = [
    ("rsvg-convert", lambda s, o, w, h: ["rsvg-convert", "-f", "pdf",
                                         "-o", o, s]),
    ("inkscape", lambda s, o, w, h: ["inkscape", "--export-type=pdf",
                                     f"--export-filename={o}", s]),
    ("chromium", lambda s, o, w, h: ["chromium", "--headless", "--disable-gpu",
                                     f"--print-to-pdf={o}",
                                     "file://" + os.path.abspath(s)]),
    ("google-chrome", lambda s, o, w, h: ["google-chrome", "--headless",
                                          "--disable-gpu", f"--print-to-pdf={o}",
                                          "file://" + os.path.abspath(s)]),
    ("wkhtmltopdf", lambda s, o, w, h: ["wkhtmltopdf", s, o]),
]


# ---------------------------------------------------------------------------
# Small I/O helpers (BOM / bad-encoding tolerant, never crash)
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path: str, size_limit: int = 2_000_000) -> str:
    """Read UTF-8 text tolerantly (BOM, size cap, common fallbacks)."""
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    size = os.path.getsize(path)
    if size > size_limit:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return fh.read(size_limit) + "\n[TRUNCATED]"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, ValueError):
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def load_json_robust(path: str):
    """Load JSON tolerating BOM and bad encoding; returns (obj, error)."""
    try:
        raw = read_text(path)
    except FileNotFoundError:
        return None, f"file_not_found:{path}"
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as exc:
        return None, f"bad_json:{exc}"


def write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _hash8(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


# ---------------------------------------------------------------------------
# Input discovery & normalization
# ---------------------------------------------------------------------------

def find_case_file(input_dir: str):
    """Locate the case JSON without trusting a fixed name."""
    cand = os.path.join(input_dir, "case.json")
    if os.path.isfile(cand):
        return cand
    if os.path.isdir(input_dir):
        for name in sorted(os.listdir(input_dir)):
            if not name.lower().endswith(".json"):
                continue
            p = os.path.join(input_dir, name)
            obj, err = load_json_robust(p)
            if err is None and isinstance(obj, dict):
                if any(k in obj for k in ("case_id", "id", "output_contract",
                                          "outputContract", "task")):
                    return p
    return None


def extract_case_id(case_obj, task_text: str, case_path: str):
    if isinstance(case_obj, dict):
        for key in ("case_id", "caseId", "challenge_id", "id"):
            if isinstance(case_obj.get(key), str) and case_obj[key].strip():
                return case_obj[key].strip()
    m = re.search(r"challenge[_ -]?id\s*[`:\"']\s*([A-Za-z0-9._-]+)", task_text,
                  flags=re.I)
    if m:
        return m.group(1)
    if case_path:
        return "case-" + _hash8(os.path.basename(case_path))
    return "case-unknown"


def _echo_contract_fields(contract, sink: dict) -> None:
    """Echo every output_contract key into sink without renaming/dropping."""
    if isinstance(contract, dict):
        for key in contract:
            if key in sink:
                continue
            val = contract[key]
            if isinstance(val, dict) and "type" in val:
                val = val.get("type")
            if isinstance(val, str):
                default = [] if val.lower() in ("list", "array", "[]") else None
                if val.lower() in ("object", "dict", "{}"):
                    default = {}
                sink[key] = default
            elif isinstance(val, list):
                sink[key] = []
            else:
                sink[key] = None
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, dict):
                name = item.get("name") or item.get("field") or item.get("key")
                if name:
                    if name not in sink:
                        sink[name] = None
            elif isinstance(item, str):
                if item not in sink:
                    sink[item] = None


def extract_contract(case_obj):
    if isinstance(case_obj, dict):
        for key in ("output_contract", "outputContract", "output_schema"):
            if key in case_obj and case_obj[key] not in (None, {}, []):
                return case_obj[key]
        for key in ("output", "result_schema"):
            if isinstance(case_obj.get(key), dict):
                return case_obj[key]
    return None


def build_result_skeleton(case_id: str, contract) -> dict:
    result = {"case_id": case_id, "status": "partial"}
    _echo_contract_fields(contract, result)
    # canonical base fields (never overwrite contract fields)
    base = {
        "graphical_abstract": dict(CANONICAL_SCHEMA["graphical_abstract"]),
        "editable_source": dict(CANONICAL_SCHEMA["editable_source"]),
        "exports": [],
        "entities": [],
        "relations": [],
        "data_mapping": [],
        "legend": [],
        "sources": [],
        "uncertainty": [],
        "limitations": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "runtime": {"started_at": _now_iso()},
    }
    for key, val in base.items():
        result.setdefault(key, val)
    return result


def normalize_case(input_dir: str, work_dir: str, output_dir: str):
    """Write case_normalized.json, figure_spec.json, result.json skeleton and
    the stage ledger. This is the early-checkpoint step."""
    errors = []
    degradations = []
    task_path = os.path.join(input_dir, "task.md")
    task_text = ""
    if os.path.isfile(task_path):
        task_text = read_text(task_path)
    else:
        degradations.append("task.md not found; using case.json only")

    case_path = find_case_file(input_dir)
    case_obj = None
    if case_path is None:
        errors.append("no case.json found under /input")
    else:
        case_obj, err = load_json_robust(case_path)
        if err:
            errors.append(f"case.json unreadable: {err}")

    contract = extract_contract(case_obj) if isinstance(case_obj, dict) else None
    case_id = extract_case_id(case_obj, task_text, case_path)

    # Title for the figure.
    title = "Graphical abstract"
    if isinstance(case_obj, dict):
        for key in ("title", "name", "task_title"):
            if isinstance(case_obj.get(key), str) and case_obj[key].strip():
                title = case_obj[key].strip()
                break
    else:
        m = re.search(r"^#\s+(.+)$", task_text, flags=re.M)
        if m:
            title = m.group(1).strip()

    result = build_result_skeleton(case_id, contract)
    result["errors"] = list(errors)
    result["degradations"] = list(degradations)
    if errors:
        result["status"] = "failed"
        result["limitations"].append(
            "input unavailable; skeleton written with status=failed")
    else:
        result["change_log"].append({
            "version": 1, "time": _now_iso(),
            "changes": "skeleton persisted (status=partial)",
            "reason": "early checkpoint before fact extraction"})

    normalized = {
        "case_id": case_id,
        "title": title,
        "case_path": case_path,
        "task_path": task_path if os.path.isfile(task_path) else None,
        "case_obj": case_obj if isinstance(case_obj, dict) else None,
        "task_text": task_text,
        "output_contract": contract,
    }

    spec = build_figure_spec_template(case_id, title)

    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    write_json(os.path.join(work_dir, "case_normalized.json"), normalized)
    write_json(os.path.join(work_dir, "figure_spec.json"), spec)
    write_json(os.path.join(output_dir, "result.json"), result)

    ledger = init_ledger(case_id)
    write_ledger(work_dir, ledger, "skeleton",
                 ok=not errors, detail="; ".join(errors + degradations) or "ok")

    return {
        "case_id": case_id, "title": title,
        "case_path": case_path, "contract_found": contract is not None,
        "errors": errors, "degradations": degradations,
        "result": result, "spec": spec,
    }


def build_figure_spec_template(case_id: str, title: str) -> dict:
    return {
        "case_id": case_id,
        "title": title,
        "version": 1,
        "canvas": dict(DEFAULT_CANVAS),
        "layout": {
            "mode": "q_m_r_c",
            "reading_order": ["question", "method", "results", "conclusion"],
            "panel_gap": 20, "panel_padding": 12,
        },
        "panels": {
            "question": {"title": "Question", "summary": "", "source": None},
            "method": {"title": "Method", "summary": "", "source": None},
            "results": {"title": "Results", "summary": "", "source": None},
            "conclusion": {"title": "Conclusion", "summary": "", "source": None},
        },
        "entities": [],
        "relations": [],
        "charts": [],
        "legend": [],
        "notes": {
            "units": "", "scale": "", "conditions": "",
            "uncertainty": "",
        },
    }


# ---------------------------------------------------------------------------
# Stage ledger (the time-budget / early-skeleton bookkeeping)
# ---------------------------------------------------------------------------

def init_ledger(case_id: str) -> dict:
    return {
        "case_id": case_id,
        "started_at": _now_iso(),
        "wall_clock_s": 900,
        "model_requests": 64,
        "plan": [dict(p) for p in BUDGET_PLAN],
        "phases": [],
        "hard_deadline_skeleton_s": 180,
        "deadline_met": None,
    }


def write_ledger(work_dir: str, ledger: dict, phase: str, ok: bool,
                 detail: str = "") -> dict:
    started = ledger.get("started_at")
    elapsed = None
    try:
        dt = datetime.strptime(started, "%Y-%m-%dT%H:%M:%SZ")
        elapsed = int(time.time()) - int(dt.replace(tzinfo=timezone.utc).timestamp())
    except (ValueError, TypeError, AttributeError):
        elapsed = None
    ledger["phases"].append({
        "phase": phase, "at": _now_iso(), "elapsed_s": elapsed, "ok": ok,
        "detail": detail[:400],
    })
    if phase == "skeleton" and elapsed is not None:
        ledger["deadline_met"] = elapsed <= ledger["hard_deadline_skeleton_s"]
    write_json(os.path.join(work_dir, "stage_ledger.json"), ledger)
    return ledger


def read_ledger(work_dir: str):
    path = os.path.join(work_dir, "stage_ledger.json")
    obj, err = load_json_robust(path)
    return obj if err is None and isinstance(obj, dict) else None


# ---------------------------------------------------------------------------
# SVG builder (deterministic layered output)
# ---------------------------------------------------------------------------

SVG_NS = "http://www.w3.org/2000/svg"


def _wrap(text: str, max_chars: int) -> list:
    if not text:
        return []
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 <= max_chars or not cur:
            cur = (cur + " " + w).strip()
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _sourced(el, source) -> None:
    """Attach data-source only when a real locator/quote exists."""
    if isinstance(source, dict) and (source.get("locator") or source.get("quote")):
        el.set("data-source", _esc(source.get("id") or source.get("locator")))


def _text_el(x, y, lines, size, fill, anchor="start", weight="normal",
             source=None, extra=None) -> str:
    parts = [f'<text x="{x:.1f}" y="{y:.1f}" font-family="{DEFAULT_CANVAS["font_family"]}" '
             f'font-size="{size}" fill="{fill}" text-anchor="{anchor}" '
             f'font-weight="{weight}"{extra or ""}>']
    for i, line in enumerate(lines):
        parts.append(f'<tspan x="{x:.1f}" dy="{(0 if i == 0 else size * 1.25):.1f}">{_esc(line)}</tspan>')
    parts.append("</text>")
    el = ET.fromstring("".join(parts))
    _sourced(el, source)
    return ET.tostring(el, encoding="unicode")


def _node_rect(cx, cy, w, h, fill, stroke, radius=8) -> str:
    return (f'<rect x="{(cx - w / 2):.1f}" y="{(cy - h / 2):.1f}" width="{w}" '
            f'height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="1.5"/>')


def _arrow(from_xy, to_xy, color, stroke_width=2.0, label="", dash=None,
           source=None) -> str:
    x1, y1 = from_xy
    x2, y2 = to_xy
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    mid = ((x1 + x2) / 2, (y1 + y2) / 2)
    parts = [f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
             f'stroke="{color}" stroke-width="{stroke_width}" marker-end="url(#arrowhead)"{dash_attr}/>']
    if label:
        parts.append(_text_el(mid[0], mid[1] - 4, [label], 11, color,
                              anchor="middle", source=source))
    return "".join(parts)


def _panel_rect(px, py, pw, ph, color) -> str:
    return (f'<rect x="{px:.1f}" y="{py:.1f}" width="{pw}" height="{ph}" '
            f'rx="10" fill="{PALETTE["panel_fill"]}" stroke="{color}" '
            f'stroke-width="2"/>')


def build_svg(spec: dict) -> str:
    canvas = spec.get("canvas") or DEFAULT_CANVAS
    W = int(canvas.get("width", 1600))
    H = int(canvas.get("height", 1000))
    margin = int(canvas.get("margin", 40))
    min_font = int(canvas.get("min_font_size", 10))
    layout = spec.get("layout") or {}
    order = layout.get("reading_order",
                       ["question", "method", "results", "conclusion"])
    gap = int(layout.get("panel_gap", 20))
    pad = int(layout.get("panel_padding", 12))

    title_y = 46
    legend_y = H - margin - 10
    panel_top = 110
    panel_bottom = legend_y - 40
    panel_height = panel_bottom - panel_top
    n = max(len(order), 1)
    panel_width = (W - 2 * margin - (n - 1) * gap) // n

    panel_x = {}
    for i, key in enumerate(order):
        panel_x[key] = margin + i * (panel_width + gap)

    svg = [f'<svg xmlns="{SVG_NS}" width="{W}" height="{H}" '
           f'viewBox="0 0 {W} {H}" font-family="{_esc(DEFAULT_CANVAS["font_family"])}">']
    svg.append('<defs>'
               '<marker id="arrowhead" markerWidth="10" markerHeight="7" '
               'refX="9" refY="3.5" orient="auto">'
               '<polygon points="0 0, 10 3.5, 0 7" fill="#333333"/>'
               '</marker></defs>')

    # layer-background
    svg.append(f'<g id="layer-background">'
               f'<rect x="0" y="0" width="{W}" height="{H}" '
               f'fill="{PALETTE["canvas"]}"/>'
               f'<text x="{W / 2:.1f}" y="{title_y:.1f}" '
               f'font-size="30" fill="{PALETTE["ink"]}" text-anchor="middle" '
               f'font-weight="bold">{_esc(spec.get("title", ""))}</text></g>')

    panels = spec.get("panels") or {}
    entities = spec.get("entities") or []
    relations = spec.get("relations") or []
    charts = spec.get("charts") or []
    legend = spec.get("legend") or []

    # Entity geometry (deterministic stack inside panels).
    node_w = panel_width - 2 * pad
    node_h = 52
    node_gap = 12
    by_panel = {}
    for ent in entities:
        by_panel.setdefault(ent.get("panel", "method"), []).append(ent)

    node_pos = {}  # id -> (cx, cy)
    panel_content_top = panel_top + 46
    svg.append('<g id="layer-panels">')
    for key in order:
        px = panel_x[key]
        color = PALETTE.get(key, PALETTE["neutral"])
        svg.append(_panel_rect(px, panel_top, panel_width, panel_height, color))
        ptxt = (panels.get(key) or {}).get("title") or key.capitalize()
        svg.append(_text_el(px + pad, panel_top + 26, [ptxt], 20, color,
                            weight="bold"))
    svg.append('</g>')

    svg.append('<g id="layer-entities">')
    for key in order:
        px = panel_x[key]
        ents = by_panel.get(key, [])
        for j, ent in enumerate(ents):
            cx = px + panel_width / 2
            cy = panel_content_top + j * (node_h + node_gap) + node_h / 2
            node_pos[ent.get("id")] = (cx, cy)
            color = PALETTE.get(ent.get("kind"), PALETTE["neutral"])
            el = ET.fromstring(_node_rect(cx, cy, node_w, node_h,
                                          PALETTE["canvas"], color))
            _sourced(el, ent.get("source"))
            svg.append(ET.tostring(el, encoding="unicode"))
            svg.append(_text_el(cx, cy + 4,
                                _wrap(ent.get("label", ""), 34), 13,
                                PALETTE["ink"], anchor="middle",
                                source=ent.get("source")))
    svg.append('</g>')

    svg.append('<g id="layer-relations">')
    for rel in relations:
        a, b = node_pos.get(rel.get("from")), node_pos.get(rel.get("to"))
        if a is None or b is None:
            continue
        direction = rel.get("direction", "forward")
        dash = "6,4" if rel.get("type") == "inhibits" else None
        color = PALETTE.get(rel.get("type"), PALETTE["arrow"])
        svg.append(_arrow(a, b, color, 2.0, rel.get("label", ""),
                          dash=dash, source=rel.get("source")))
    svg.append('</g>')

    svg.append('<g id="layer-data">')
    for ch in charts:
        px = panel_x.get(ch.get("panel", "results"), margin)
        values = ch.get("values") or []
        if not values:
            continue
        bar_w = (panel_width - 2 * pad - 20) / max(len(values), 1)
        max_v = max([abs(v.get("value", 0)) for v in values] or [1]) or 1
        base_y = panel_top + panel_height - 50
        scale_h = 120
        for i, v in enumerate(values):
            val = float(v.get("value", 0))
            bh = max(scale_h * abs(val) / max_v, 4)
            bx = px + pad + 10 + i * bar_w
            by = base_y - bh
            el = ET.fromstring(
                f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w - 8:.1f}" '
                f'height="{bh:.1f}" fill="{PALETTE["results"]}"/>')
            _sourced(el, ch.get("source"))
            svg.append(ET.tostring(el, encoding="unicode"))
            label = v.get("label", "")
            unit = v.get("unit", "")
            svg.append(_text_el(bx + (bar_w - 8) / 2, by - 6,
                                [str(val), unit] if unit else [str(val)],
                                11, PALETTE["ink"], anchor="middle",
                                source=ch.get("source")))
            if label:
                svg.append(_text_el(bx + (bar_w - 8) / 2, base_y + 16,
                                    _wrap(label, 12), min(max(min_font, 10), 12),
                                    PALETTE["ink"], anchor="middle"))
    svg.append('</g>')

    svg.append('<g id="layer-labels">')
    for key in order:
        px = panel_x[key]
        summary = (panels.get(key) or {}).get("summary", "")
        lines = _wrap(summary, int(panel_width / 8.5))
        svg.append(_text_el(px + pad, panel_top + 52, lines,
                            max(min_font, 13), PALETTE["ink"],
                            source=(panels.get(key) or {}).get("source")))
    svg.append('</g>')

    svg.append('<g id="layer-legend">')
    lx = margin
    ly = legend_y + 22
    for i, lg in enumerate(legend or []):
        cx = lx + i * 240
        el = ET.fromstring(
            f'<rect x="{cx:.1f}" y="{ly - 11:.1f}" width="14" height="14" '
            f'fill="{_esc(lg.get("color", PALETTE["neutral"]))}" '
            f'stroke="{PALETTE["ink"]}" stroke-width="0.5"/>')
        _sourced(el, lg.get("source"))
        svg.append(ET.tostring(el, encoding="unicode"))
        svg.append(_text_el(cx + 20, ly, [lg.get("label", "")], 12,
                            PALETTE["ink"], source=lg.get("source")))
    notes = spec.get("notes") or {}
    note_txt = " | ".join(f"{k}: {v}" for k, v in notes.items() if v)
    if note_txt:
        svg.append(_text_el(W - margin, legend_y - 6, _wrap(note_txt, 80), 11,
                            PALETTE["neutral"], anchor="end"))
    svg.append('</g>')

    svg.append('</svg>')
    raw = "\n".join(svg)
    try:
        return minidom.parseString(raw).toprettyxml(indent="  ")
    except Exception:
        return raw


def save_svg(svg_text: str, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(svg_text)


# ---------------------------------------------------------------------------
# Renderer probing (honest previews, never fabricated)
# ---------------------------------------------------------------------------

def _has_magic(path: str, magic: bytes) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(len(magic)) == magic
    except OSError:
        return False


def attempt_render(svg_path: str, output_dir: str, case_id: str):
    canvas = DEFAULT_CANVAS
    w, h = canvas["width"], canvas["height"]
    exports = []
    found = []

    def _try(candidates, out_path, magic):
        for name, builder in candidates:
            exe = shutil.which(name)
            if not exe:
                continue
            found.append(name)
            argv = builder(svg_path, out_path, w, h)
            try:
                proc = subprocess.run(argv, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, timeout=45)
                if proc.returncode == 0 and _has_magic(out_path, magic):
                    return name
            except (OSError, subprocess.SubprocessError):
                continue
        return None

    png_path = os.path.join(output_dir, f"fig_{case_id}.png")
    pdf_path = os.path.join(output_dir, f"fig_{case_id}.pdf")
    png_renderer = _try(PNG_RENDERERS, png_path, PNG_MAGIC)
    pdf_renderer = _try(PDF_RENDERERS, pdf_path, PDF_MAGIC)

    if png_renderer:
        exports.append({"format": "png", "path": os.path.basename(png_path),
                        "renderer": png_renderer, "status": "rendered",
                        "width": w, "height": h, "dpi": 150})
    else:
        exports.append({"format": "png", "path": os.path.basename(png_path),
                        "renderer": None, "status": "preview_not_rendered",
                        "width": w, "height": h, "dpi": None,
                        "note": "no stdlib-visible renderer produced a real PNG"})
    if pdf_renderer:
        exports.append({"format": "pdf", "path": os.path.basename(pdf_path),
                        "renderer": pdf_renderer, "status": "rendered",
                        "width": w, "height": h, "dpi": None})
    else:
        exports.append({"format": "pdf", "path": os.path.basename(pdf_path),
                        "renderer": None, "status": "preview_not_rendered",
                        "width": w, "height": h, "dpi": None,
                        "note": "no stdlib-visible renderer produced a real PDF"})
    return exports, sorted(set(found))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _check(rep: dict, name: str, ok: bool, level: str, msg: str) -> None:
    rep["checks"].append({"name": name, "ok": ok, "level": level, "msg": msg})
    if not ok:
        rep[level + "s"].append(msg)


def validate_output(result_path: str, svg_path: str, work_dir: str) -> dict:
    rep = {"checks": [], "errors": [], "warnings": []}
    result, err = load_json_robust(result_path)
    if err is not None or not isinstance(result, dict):
        _check(rep, "result_json_parse", False, "error",
               f"result.json invalid: {err}")
        return rep
    _check(rep, "result_json_parse", True, "ok", "result.json parses")

    _check(rep, "case_id", bool(result.get("case_id")), "error",
           "case_id present")
    _check(rep, "status", result.get("status") in STATUS_VALUES, "error",
           f"status in {STATUS_VALUES}")

    for key in ("sources", "uncertainty", "change_log"):
        _check(rep, f"field_{key}", isinstance(result.get(key), list), "warning",
               f"{key} must be a list")

    if not svg_path or not os.path.isfile(svg_path):
        _check(rep, "svg_exists", False, "error", f"SVG missing: {svg_path}")
        return rep
    _check(rep, "svg_exists", True, "ok", "SVG exists")

    try:
        tree = ET.parse(svg_path)
        root = tree.getroot()
    except ET.ParseError as exc:
        _check(rep, "svg_parse", False, "error", f"SVG unparseable: {exc}")
        return rep
    _check(rep, "svg_parse", True, "ok", "SVG well-formed XML")

    ns = {"s": SVG_NS}
    layers = root.findall(".//s:g", ns)
    layer_ids = [g.get("id", "") for g in layers]
    has_layer = any(i.startswith("layer-") for i in layer_ids)
    _check(rep, "svg_layers", has_layer, "warning",
           "SVG has named layer-* groups")

    texts = root.findall(".//s:text", ns)
    _check(rep, "svg_editable_text", len(texts) > 0, "error",
           "SVG contains editable <text> elements")
    paths_as_text = [t for t in texts if t.find(".//s:path", ns) is not None]
    _check(rep, "svg_no_text_as_path", len(paths_as_text) == 0, "warning",
           "no text rendered as path")

    # Every entity/relation/data entry should have a source pointer.
    for key in ("entities", "relations", "data_mapping"):
        items = result.get(key) or []
        missing = [i.get("id", f"#{n}") for n, i in enumerate(items)
                   if not (isinstance(i.get("source"), dict) and
                           (i["source"].get("locator") or i["source"].get("quote")))]
        if missing:
            _check(rep, f"provenance_{key}", False, "warning",
                   f"{key} missing source: {missing[:5]}")
        else:
            _check(rep, f"provenance_{key}", True, "ok",
                   f"{key} all sourced" if items else f"{key} empty")

    # Exports honesty: rendered => file with magic bytes; else preview_not_rendered.
    exports = result.get("exports") or []
    for exp in exports:
        p = exp.get("path")
        full = os.path.join(os.path.dirname(result_path), p) if p else None
        if exp.get("status") == "rendered":
            if not full or not os.path.isfile(full):
                _check(rep, f"export_{p}", False, "error",
                       f"rendered export missing on disk: {p}")
            elif exp.get("format") == "png" and not _has_magic(full, PNG_MAGIC):
                _check(rep, f"export_{p}", False, "error", f"PNG magic invalid: {p}")
            elif exp.get("format") == "pdf" and not _has_magic(full, PDF_MAGIC):
                _check(rep, f"export_{p}", False, "error", f"PDF magic invalid: {p}")
            else:
                _check(rep, f"export_{p}", True, "ok", f"export verified: {p}")
        elif exp.get("status") == "preview_not_rendered":
            _check(rep, f"export_{p}", False, "warning",
                   f"honest preview_not_rendered for {p}: editable SVG retained")
        else:
            _check(rep, f"export_{p}", False, "warning",
                   f"unknown export status: {exp.get('status')}")

    rep["ok"] = not rep["errors"]
    return rep


# ---------------------------------------------------------------------------
# Finalize
# ---------------------------------------------------------------------------

def sync_spec_to_result(spec, result):
    """Non-destructively backfill result.json from figure_spec.json only when
    the result field is still empty. The agent may already have hand-mapped
    sources; those are never overwritten."""
    if not isinstance(spec, dict) or not isinstance(result, dict):
        return result
    for key in ("entities", "relations"):
        if not result.get(key):
            result[key] = list(spec.get(key) or [])
    if not result.get("legend"):
        result["legend"] = list(spec.get("legend") or [])
    if not result.get("data_mapping"):
        dm = []
        for ch in spec.get("charts") or []:
            channel = ch.get("channel") or (
                "length" if ch.get("kind") == "bar" else "value")
            for v in ch.get("values") or []:
                dm.append({
                    "visual_channel": channel,
                    "entity_or_relation": ch.get("id"),
                    "data_value": v.get("value"),
                    "unit": v.get("unit"),
                    "source": ch.get("source"),
                })
        result["data_mapping"] = dm
    ga = result.get("graphical_abstract")
    panels = spec.get("panels") or {}
    if isinstance(ga, dict):
        for key in ("question", "method", "results", "conclusion"):
            if not ga.get(key):
                ga[key] = (panels.get(key) or {}).get("summary")
    return result


def finalize(result_path: str, svg_path: str, work_dir: str,
             case_id: str, exports, renderers, extra_limitations=None):
    result, err = load_json_robust(result_path)
    if err is not None or not isinstance(result, dict):
        return False, f"cannot read result.json: {err}"
    spec, _ = load_json_robust(os.path.join(work_dir, "figure_spec.json"))
    result = sync_spec_to_result(spec, result)
    write_json(result_path, result)  # persist sync before validation
    rep = validate_output(result_path, svg_path, work_dir)

    result["case_id"] = result.get("case_id") or case_id
    result["exports"] = exports
    if result.get("editable_source") is None:
        result["editable_source"] = dict(CANONICAL_SCHEMA["editable_source"])
    es = result["editable_source"]
    es.setdefault("path", os.path.basename(svg_path) if svg_path else None)
    es.setdefault("format", "svg")
    es.setdefault("text_editable", True)
    es.setdefault("renderer", renderers[0] if renderers else None)
    try:
        if svg_path and os.path.isfile(svg_path):
            tree = ET.parse(svg_path)
            es["layers"] = [g.get("id") for g in tree.getroot()
                            if g.get("id", "").startswith("layer-")]
    except ET.ParseError:
        es["layers"] = []

    result["errors"] = rep["errors"]
    result["degradations"] = list(result.get("degradations") or [])
    if not renderers:
        result["degradations"].append(
            "no renderer found; SVG source kept, preview_not_rendered recorded")

    lims = list(result.get("limitations") or [])
    for x in (extra_limitations or []):
        if x not in lims:
            lims.append(x)
    for w in rep["warnings"]:
        if w not in lims:
            lims.append(w)
    result["limitations"] = lims

    if result.get("status") != "failed":
        # A real preview is required only when exports were attempted; if none
        # rendered, the export requirement is unmet -> honest partial.
        has_rendered = any(isinstance(e, dict) and e.get("status") == "rendered"
                           for e in (exports or []))
        if exports and not has_rendered:
            lims.append("no real PNG/PDF preview rendered (no renderer "
                        "available); editable SVG is the primary deliverable")
            result["status"] = "partial"
        else:
            result["status"] = "completed" if rep["ok"] else "partial"

    result["change_log"] = list(result.get("change_log") or [])
    result["change_log"].append({
        "version": 1 + len(result["change_log"]), "time": _now_iso(),
        "changes": "finalized: validation applied, status set",
        "reason": "final checkpoint"})
    result["runtime"] = result.get("runtime") or {}
    result["runtime"]["finished_at"] = _now_iso()

    write_json(result_path, result)
    write_json(os.path.join(work_dir, "validation_report.json"), rep)
    return True, rep


# ---------------------------------------------------------------------------
# Full pipeline (used by selftest and CLI convenience)
# ---------------------------------------------------------------------------

def run_pipeline(input_dir, output_dir, work_dir):
    step1 = normalize_case(input_dir, work_dir, output_dir)
    if step1["errors"]:
        write_ledger(work_dir, read_ledger(work_dir) or init_ledger(step1["case_id"]),
                     "build", ok=False, detail="blocked: no input")
        return step1

    spec_path = os.path.join(work_dir, "figure_spec.json")
    spec, err = load_json_robust(spec_path)
    if err is not None:
        spec = step1["spec"]
    svg_path = os.path.join(output_dir, f"fig_{step1['case_id']}.svg")
    save_svg(build_svg(spec), svg_path)
    exports, renderers = attempt_render(svg_path, output_dir, step1["case_id"])

    ledger = read_ledger(work_dir) or init_ledger(step1["case_id"])
    write_ledger(work_dir, ledger, "build", ok=True,
                 detail=f"renderers={renderers or 'none'}")

    manifest = {"case_id": step1["case_id"], "svg": os.path.basename(svg_path),
                "exports": exports, "renderers": renderers,
                "generated_at": _now_iso()}
    write_json(os.path.join(work_dir, "fig_manifest.json"), manifest)

    ok, rep = finalize(os.path.join(output_dir, "result.json"), svg_path,
                       work_dir, step1["case_id"], exports, renderers,
                       extra_limitations=[
                           "fact extraction and panel summaries must be filled "
                           "by the agent between skeleton and build; the CLI "
                           "run only renders the template"])
    write_ledger(work_dir, read_ledger(work_dir), "finalize", ok=ok,
                 detail=f"ok={ok}")
    return {"case_id": step1["case_id"], "validation": rep}
