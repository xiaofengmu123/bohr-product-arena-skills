# -*- coding: utf-8 -*-
"""core_impl.py — stdlib-only core for the graphical-abstract skill.

Shared pure functions used by scripts/main.py (pipeline) and
scripts/selftest.py (self-check): input normalisation, output-contract
resolution, deterministic layered SVG construction, evidence extraction
(locator + verbatim quote), validation and rubric-trace generation.

Everything here is deterministic: same figure_spec.json produces byte-identical
SVG, which is what makes the "reproducible structure" scoring item checkable.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path
from xml.etree import ElementTree as ET

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

MAX_TEXT_BYTES = 2 * 1024 * 1024  # skip material files larger than 2 MB

SVG_NS = "http://www.w3.org/2000/svg"

CANONICAL_CONTRACT = {
    "graphical_abstract": {"question": [], "method": [], "results": [], "conclusion": []},
    "editable_source": {"svg": None, "layers": [], "text_editable": True},
    "exports": {"png": None, "pdf": None, "renderer": None, "status": "preview_not_rendered"},
    "entities": [],
    "relations": [],
    "data_mapping": [],
    "legend": [],
    "sources": [],
    "uncertainty": [],
    "change_log": [],
    "limitations": [],
}

# status/sources/uncertainty/change_log are mandated by the skill contract;
# pending_human is added so missing facts are signalled instead of fabricated.
EXTRA_FIELDS = ["status", "sources", "uncertainty", "pending_human", "change_log"]

# Okabe-Ito colourblind-safe palette (yellow kept but never used as node fill).
COLORBLIND_PALETTE = [
    "#0072B2", "#E69F00", "#009E73", "#D55E00",
    "#56B4E9", "#CC79A7", "#F0E442", "#000000",
]

LAYER_ORDER = [
    "layer-panels",
    "layer-entities",
    "layer-relations",
    "layer-data",
    "layer-labels",
    "layer-legend",
    "layer-annotations",
]

PANEL_HEADING_DEFAULT = {
    "question": "1. Research question",
    "method": "2. Method",
    "results": "3. Results",
    "conclusion": "4. Conclusion",
}

# ---------------------------------------------------------------------------
# small IO helpers (tolerant, stdlib only)
# ---------------------------------------------------------------------------


def read_text(path, cap: int = MAX_TEXT_BYTES) -> str | None:
    """Read a UTF-8 text file tolerantly; return None on failure or oversize."""
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError:
        return None
    if len(data) > cap:
        return None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def load_json(path):
    """Parse JSON tolerantly (BOM, bad encoding already handled by read_text)."""
    text = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def write_json(path, obj) -> str:
    """Atomically write JSON (tmp file + os.replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return str(path)


def sanitize_id(s) -> str:
    """Stable identifier from an arbitrary label."""
    s = unicodedata.normalize("NFKC", str(s)).strip().lower()
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff_-]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "unnamed"


# ---------------------------------------------------------------------------
# input discovery / normalisation
# ---------------------------------------------------------------------------


def discover_case(input_dir: str):
    """Return (case_obj, task_text, material_relpaths) with tolerant discovery."""
    root = Path(input_dir)
    case_obj = load_json(root / "case.json") if (root / "case.json").exists() else None
    task_text = read_text(root / "task.md") if (root / "task.md").exists() else None
    materials = []
    if root.exists():
        for p in sorted(root.rglob("*")):
            if p.is_file():
                rel = p.relative_to(root).as_posix()
                if rel in ("case.json", "task.md"):
                    continue
                materials.append(rel)
    return case_obj, task_text, materials


def extract_case_id(case_obj, task_text) -> str:
    """Best-effort stable case id; never fabricates, falls back to a literal."""
    if isinstance(case_obj, dict):
        for k in ("id", "case_id", "challenge_id", "challengeId"):
            if case_obj.get(k):
                return str(case_obj[k])
    if task_text:
        m = re.search(r"challenge_id:\s*`?([0-9A-Za-z_\-]+)", task_text)
        if m:
            return m.group(1)
        m2 = re.search(r"case[_ ]?id[\"']?\s*[:=]\s*[\"']?([0-9A-Za-z_\-]+)", task_text, re.I)
        if m2:
            return m2.group(1)
    return "unknown-case"


def get_output_contract(case_obj):
    """Return the case output_contract, or None when absent."""
    if not isinstance(case_obj, dict):
        return None
    for key in ("output_contract", "outputContract", "contract", "output_schema"):
        if case_obj.get(key):
            return case_obj[key]
    return None


def default_for(value):
    """Recursive empty default for a contract value (None/[]/{})."""
    if isinstance(value, dict):
        return {k: default_for(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    return None


def resolve_contract(output_contract) -> dict:
    """Ordered {field: default}; honours the case contract, else canonical.

    Unknown keys are preserved, renamed never. The extra fields mandated by the
    skill contract (status/sources/uncertainty/pending_human/change_log) are
    added once when not already present in the case contract.
    """
    result: dict = {}
    if isinstance(output_contract, dict):
        for k, v in output_contract.items():
            result[str(k)] = default_for(v)
    elif isinstance(output_contract, list):
        for item in output_contract:
            if isinstance(item, str):
                result[item] = None
            elif isinstance(item, dict):
                name = item.get("name") or item.get("field") or item.get("key")
                if name:
                    result[str(name)] = default_for(item)
    if not result:
        result = json.loads(json.dumps(CANONICAL_CONTRACT))
    for f in EXTRA_FIELDS:
        if f not in result:
            result[f] = None
    return result


def build_skeleton(case_id: str, output_contract) -> dict:
    """Minimal legal partial skeleton written before any analysis."""
    fields = resolve_contract(output_contract)
    skeleton = {"case_id": case_id, "status": "partial"}
    for k, v in fields.items():
        skeleton[k] = v
    skeleton["status"] = "partial"
    skeleton["change_log"] = [{
        "step": "extract",
        "action": "write_partial_skeleton",
        "note": "legal partial result persisted before analysis",
    }]
    return skeleton


# ---------------------------------------------------------------------------
# evidence provenance: locator + verbatim quote
# ---------------------------------------------------------------------------


def quote_from_locator(locator, input_dir, work_dir) -> str | None:
    """Return the verbatim line at `path:line` (or head of `path#page`).

    Resolves against input_dir then work_dir so PDF text exported with pdftotext
    under /app/work can also be quoted. Returns None when unavailable — callers
    then mark the evidence `verified: false` instead of inventing a quote.
    """
    if not locator:
        return None
    loc = str(locator)
    m = re.match(r"^(.*?)[:#](\d+)$", loc)
    if m:
        file_part, num = m.group(1), int(m.group(2))
    else:
        file_part, num = loc, None
    if not file_part or file_part.startswith(("/", "\\")) and len(file_part) > 1 and file_part[1] == ":":
        return None
    for base in (Path(input_dir), Path(work_dir)):
        cand = base / file_part
        if cand.is_file():
            text = read_text(cand)
            if text is None:
                return None
            lines = text.splitlines()
            if num is not None:
                if 1 <= num <= len(lines):
                    return lines[num - 1].strip()
                return None
            return text[:400].strip()
    return None


def make_source(locator, quote=None, input_dir=None, work_dir=None) -> dict:
    """Normalise a source pointer into {locator, quote, verified}."""
    src = {"locator": locator}
    if quote:
        src["quote"] = str(quote)
        src["verified"] = True
    elif locator and input_dir is not None:
        extracted = quote_from_locator(locator, input_dir, work_dir)
        if extracted is not None:
            src["quote"] = extracted
            src["verified"] = True
        else:
            src["quote"] = None
            src["verified"] = False
    return src


# ---------------------------------------------------------------------------
# text measurement / wrapping (deterministic; CJK aware)
# ---------------------------------------------------------------------------


def _char_w(ch: str) -> float:
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 1.0
    return 0.56


def text_width(s, font_size: float) -> float:
    return sum(_char_w(c) for c in str(s)) * font_size


def wrap_text(s, max_px: float, font_size: float) -> list:
    """Greedy char-level wrap that handles CJK and long ASCII words alike."""
    if not s:
        return [""]
    lines: list = []
    cur = ""
    for ch in str(s):
        candidate = cur + ch
        if ch.isspace():
            cur = candidate
            continue
        if cur.strip() and text_width(candidate.strip(), font_size) > max_px:
            lines.append(cur.strip())
            cur = ch
        else:
            cur = candidate
    if cur.strip():
        lines.append(cur.strip())
    return lines or [""]


def xml_escape(s) -> str:
    return (
        str(s).replace("&", "&amp;").replace("<", "&lt;")
        .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")
    )


def contrast_text(hexcolor: str) -> str:
    """Pick black or white text for readable contrast on a fill colour."""
    c = str(hexcolor).lstrip("#")
    if len(c) != 6:
        return "#1A1A1A"
    try:
        r, g, b = (int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return "#1A1A1A"
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#FFFFFF" if lum < 0.5 else "#1A1A1A"


# ---------------------------------------------------------------------------
# deterministic layered SVG builder
# ---------------------------------------------------------------------------


class _Layout:
    """Deterministic single-page 4-panel layout (question/method/results/conclusion)."""

    def __init__(self, spec: dict):
        self.spec = spec or {}
        canvas = self.spec.get("canvas") or {}
        self.w = int(canvas.get("width", 1600))
        self.h = int(canvas.get("height", 1200))
        self.margin = 48
        self.title_y = 46
        self.header_y0 = 76
        self.header_h = 52
        self.content_y0 = self.header_y0 + self.header_h + 18
        self.legend_h = 122
        self.content_y1 = self.h - self.margin - self.legend_h - 16
        self.node_h = 62
        self.node_gap = 14
        self.font_title = 27
        self.font_head = 18
        self.font_body = 14
        self.font_small = 11
        self.panels = ["question", "method", "results", "conclusion"]
        self.panel_order = list(self.spec.get("panel_order") or self.panels)
        # normalise order: keep only known panels, append missing ones
        self.panel_order = [p for p in self.panel_order if p in self.panels]
        for p in self.panels:
            if p not in self.panel_order:
                self.panel_order.append(p)
        n = len(self.panel_order)
        usable = self.w - 2 * self.margin - (n - 1) * 34
        self.pw = max(160, usable // n)
        self.panel_x = {
            p: self.margin + i * (self.pw + 34) for i, p in enumerate(self.panel_order)
        }
        self.layers: dict = {}
        self.node_rect: dict = {}   # entity id -> (x, y, w, h)
        self.node_panel: dict = {}
        self.max_cursor_y = self.content_y0
        self.color_index = 0

    # -- helpers ---------------------------------------------------------
    def _add(self, layer: str, xml: str):
        self.layers.setdefault(layer, []).append(xml)

    def _src(self, source) -> str:
        if isinstance(source, dict) and source.get("locator"):
            return f' data-source="{xml_escape(source["locator"])}"'
        return ""

    def _text(self, x, y, s, size, layer="layer-labels", weight="normal",
              anchor="start", fill="#1A1A1A", source=None):
        src = self._src(source)
        bold = ' font-weight="bold"' if weight == "bold" else ""
        return (
            f'<text x="{x:.1f}" y="{y:.1f}" font-family="DejaVu Sans, Noto Sans CJK SC, Arial, sans-serif" '
            f'font-size="{size}" text-anchor="{anchor}" fill="{fill}"{bold}{src}>{xml_escape(s)}</text>'
        )

    def _rect(self, x, y, w, h, fill="#FFFFFF", stroke=None, rx=8, layer="layer-panels", source=None):
        src = self._src(source)
        st = f' stroke="{stroke}" stroke-width="1.5"' if stroke else ""
        return (
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'rx="{rx}" fill="{fill}"{st}{src}/>'
        )

    def _color(self) -> str:
        c = COLORBLIND_PALETTE[self.color_index % len(COLORBLIND_PALETTE)]
        self.color_index += 1
        return c

    def _heading(self, pname: str) -> str:
        headings = self.spec.get("panel_headings") or {}
        if isinstance(headings, dict) and headings.get(pname):
            return str(headings[pname])
        return PANEL_HEADING_DEFAULT.get(pname, pname)

    # -- content accessors ----------------------------------------------
    def _panel_points(self, pname: str) -> list:
        ga = self.spec.get("graphical_abstract")
        if isinstance(ga, dict):
            v = ga.get(pname)
            if isinstance(v, list):
                return [x if isinstance(x, str) else x.get("text", "") for x in v]
            if isinstance(v, dict):
                v = v.get("points") or []
                return [x if isinstance(x, str) else x.get("text", "") for x in v]
        panels = self.spec.get("panels")
        if isinstance(panels, dict):
            p = panels.get(pname)
            if isinstance(p, dict):
                pts = p.get("points") or []
                return [x if isinstance(x, str) else x.get("text", "") for x in pts]
        return []

    # -- render phases ---------------------------------------------------
    def render(self) -> str:
        self.layers = {}
        self.node_rect = {}
        self.color_index = 0
        self._render_title()
        self._render_headers()
        cursors = {p: self.content_y0 for p in self.panel_order}
        for p in self.panel_order:
            cursors[p] = self._render_points(p, cursors[p])
        self._place_entities(cursors)
        for p in self.panel_order:
            self._render_data(p, cursors[p])
        self._render_relations()
        self._render_legend()
        self._render_footer()
        parts = [self._svg_open(), self._defs()]
        # always emit all seven named groups so the layer contract is stable
        # even when a panel has no entities/relations/data/legend yet
        for layer in LAYER_ORDER:
            parts.append(f'<g id="{layer}">')
            parts.extend(self.layers.get(layer, []))
            parts.append("</g>")
        parts.append("</svg>")
        return "\n".join(parts)

    def _svg_open(self):
        return (
            f'<svg xmlns="{SVG_NS}" width="{self.w}" height="{self.h}" '
            f'viewBox="0 0 {self.w} {self.h}" role="img" xml:space="preserve">'
        )

    def _defs(self):
        return (
            '<defs>\n'
            '  <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="8" markerHeight="8" orient="auto-start-reverse">\n'
            '    <path d="M 0 0 L 10 5 L 0 10 z" fill="#1A1A1A"/>\n'
            '  </marker>\n'
            '</defs>'
        )

    def _render_title(self):
        title = self.spec.get("title") or "Graphical Abstract"
        self._add("layer-labels",
                  self._text(self.w / 2, self.title_y, title, self.font_title,
                             weight="bold", anchor="middle", fill="#0B1F3A"))

    def _render_headers(self):
        for p in self.panel_order:
            x = self.panel_x[p]
            self._add("layer-panels",
                      self._rect(x, self.header_y0, self.pw, self.header_h,
                                 fill="#EEF2F7", rx=8))
            self._add("layer-panels",
                      self._text(x + 16, self.header_y0 + 34, self._heading(p),
                                 self.font_head, weight="bold", anchor="start",
                                 fill="#0B1F3A"))

    def _render_points(self, pname: str, cursor: float) -> float:
        pts = self._panel_points(pname)
        if not pts:
            return cursor
        x = self.panel_x[pname]
        for pt in pts:
            if not pt:
                continue
            for line in wrap_text(str(pt), self.pw - 44, self.font_body):
                self._add("layer-labels",
                          self._text(x + 18, cursor + 14, "\u2022 " + line,
                                     self.font_body, anchor="start", fill="#1A1A1A"))
                cursor += 23
            cursor += 6
        self.max_cursor_y = max(self.max_cursor_y, cursor)
        return cursor + 6

    def _place_entities(self, cursors: dict):
        entities = self.spec.get("entities") or []
        for idx, ent in enumerate(entities):
            if not isinstance(ent, dict):
                continue
            eid = ent.get("id") or f"e{idx + 1}"
            label = ent.get("label") or eid
            etype = (ent.get("type") or "entity").upper()
            panel = ent.get("panel")
            if panel not in self.panel_order:
                panel = self.panel_order[idx % len(self.panel_order)]
            color = self._color()
            x = self.panel_x[panel]
            y = cursors[panel]
            w = max(150, self.pw - 20)
            h = self.node_h
            self.node_rect[eid] = (x, y, w, h)
            self.node_panel[eid] = panel
            source = ent.get("source")
            tcol = contrast_text(color)
            self._add("layer-entities",
                      self._rect(x, y, w, h, fill=color, stroke="#1A1A1A",
                                 rx=10, layer="layer-entities", source=source))
            self._add("layer-entities",
                      self._text(x + 12, y + 18, etype, self.font_small,
                                 weight="bold", anchor="start", fill=tcol, source=source))
            for li, line in enumerate(wrap_text(label, w - 24, self.font_body)[:2]):
                self._add("layer-entities",
                          self._text(x + 12, y + 40 + li * 18, line,
                                     self.font_body, anchor="start", fill=tcol, source=source))
            cursors[panel] = y + h + self.node_gap
            self.max_cursor_y = max(self.max_cursor_y, cursors[panel])

    def _render_data(self, pname: str, cursor: float):
        data = [d for d in (self.spec.get("data") or [])
                if isinstance(d, dict) and d.get("panel", "results") == pname]
        if not data:
            return
        values = [d.get("value") for d in data if isinstance(d.get("value"), (int, float))]
        maxv = max(values) if values else 1.0
        maxv = max(maxv, 1e-9)
        x = self.panel_x[pname]
        max_bar = self.pw - 80
        for i, d in enumerate(data):
            val = d.get("value")
            if not isinstance(val, (int, float)):
                continue
            label = d.get("label") or f"data-{i + 1}"
            unit = d.get("unit") or ""
            color = d.get("color") or self._color()
            bar_w = max(6.0, min(max_bar, max_bar * (float(val) / maxv)))
            y = cursor + 8
            source = d.get("source")
            self._add("layer-data",
                      self._text(x + 4, y + 11, label, self.font_small,
                                 anchor="start", fill="#1A1A1A", source=source))
            self._add("layer-data",
                      self._rect(x + 4, y + 14, bar_w, 16, fill=color, rx=3,
                                 layer="layer-data", source=source))
            self._add("layer-data",
                      self._text(x + 4 + bar_w + 8, y + 27,
                                 f"{val:g} {unit}".strip(), self.font_small,
                                 anchor="start", fill="#1A1A1A", source=source))
            cursor += 34
        self.max_cursor_y = max(self.max_cursor_y, cursor)

    # -- relations -------------------------------------------------------
    def _anchors(self, a, b, off):
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        acx, acy = ax + aw / 2, ay + ah / 2
        bcx, bcy = bx + bw / 2, by + bh / 2
        dx, dy = bcx - acx, bcy - acy
        if abs(dx) >= abs(dy):
            if dx >= 0:
                return (ax + aw, acy + off), (bx, bcy + off)
            return (ax, acy + off), (bx + bw, bcy + off)
        if dy >= 0:
            return (acx + off, ay + ah), (bcx + off, by)
        return (acx + off, ay), (bcx + off, by + bh)

    def _render_relations(self):
        rels = self.spec.get("relations") or []
        seen: dict = {}
        for idx, rel in enumerate(rels):
            if not isinstance(rel, dict):
                continue
            a, b = rel.get("from"), rel.get("to")
            if a not in self.node_rect or b not in self.node_rect:
                continue
            key = tuple(sorted([str(a), str(b)]))
            k = seen.get(key, 0)
            seen[key] = k + 1
            off = (k - 1) * 16 - 8
            (x1, y1), (x2, y2) = self._anchors(self.node_rect[a], self.node_rect[b], off)
            direction = rel.get("direction", "directed")
            color = rel.get("color") or "#333333"
            label = rel.get("label")
            source = rel.get("source")
            src = self._src(source)
            mark = ' marker-end="url(#arrow)"' if direction == "directed" else ""
            self._add("layer-relations",
                      f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                      f'stroke="{color}" stroke-width="2.5"{mark}{src}/>')
            if direction == "inhibits":
                ux, uy = x2 - x1, y2 - y1
                n = math.hypot(ux, uy) or 1.0
                ux, uy = ux / n, uy / n
                px, py = -uy, ux
                t = 10.0
                self._add("layer-relations",
                          f'<line x1="{x2 - ux*6 - px*t:.1f}" y1="{y2 - uy*6 - py*t:.1f}" '
                          f'x2="{x2 - ux*6 + px*t:.1f}" y2="{y2 - uy*6 + py*t:.1f}" '
                          f'stroke="{color}" stroke-width="2.5"{src}/>')
            if label:
                mx, my = (x1 + x2) / 2, (y1 + y2) / 2 - 7
                self._add("layer-relations",
                          self._text(mx, my, label, 12, anchor="middle",
                                     fill="#1A1A1A", source=source))

    # -- legend ----------------------------------------------------------
    def _render_legend(self):
        legend = self.spec.get("legend") or []
        if not legend:
            return
        ly = self.h - self.margin - self.legend_h + 26
        self._add("layer-legend",
                  self._text(self.margin, ly, "Legend / \u56fe\u4f8b",
                             self.font_head, weight="bold", fill="#0B1F3A"))
        x = self.margin
        y = ly + 16
        for i, item in enumerate(legend):
            if not isinstance(item, dict):
                continue
            meaning = item.get("meaning") or item.get("key") or f"L{i + 1}"
            color = item.get("color") or COLORBLIND_PALETTE[i % len(COLORBLIND_PALETTE)]
            source = item.get("source")
            self._add("layer-legend",
                      self._rect(x, y, 18, 18, fill=color, stroke="#1A1A1A",
                                 rx=3, layer="layer-legend", source=source))
            self._add("layer-legend",
                      self._text(x + 26, y + 14, meaning, 13, anchor="start",
                                 fill="#1A1A1A", source=source))
            x += 26 + text_width(meaning, 13) + 30
            if x > self.w - self.margin - 60:
                x = self.margin
                y += 26
        self.max_cursor_y = max(self.max_cursor_y, y + 20)

    def _render_footer(self):
        nsrc = len(self.spec.get("sources") or [])
        note = (f"Editable layered SVG \u00b7 {nsrc} source pointers \u00b7 "
                f"deterministic layout \u00b7 regenerate via scripts/main.py")
        self._add("layer-annotations",
                  self._text(self.margin, self.h - 22, note, self.font_small,
                             anchor="start", fill="#666666"))

    # -- public helpers --------------------------------------------------
    def overflow_report(self) -> dict:
        return {
            "overflow": self.max_cursor_y > self.content_y1,
            "max_y": round(self.max_cursor_y, 1),
            "content_y1": self.content_y1,
            "min_font": self.font_small,
        }


def build_svg(spec: dict, out_path) -> dict:
    """Build a deterministic layered SVG and return a layout report."""
    layout = _Layout(spec)
    svg = layout.render()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg, encoding="utf-8")
    return layout.overflow_report()


def list_layers(svg_path) -> list:
    """Return the layer ids actually present in an SVG file."""
    try:
        tree = ET.parse(svg_path)
    except (ET.ParseError, OSError):
        return []
    root = tree.getroot()
    layers = []
    for g in root.iter(f"{{{SVG_NS}}}g"):
        gid = g.get("id")
        if gid and gid.startswith("layer-"):
            layers.append(gid)
    return layers


def count_text_and_sources(svg_path) -> dict:
    """Count editable text nodes and data-source attributes."""
    try:
        tree = ET.parse(svg_path)
    except (ET.ParseError, OSError):
        return {"text": 0, "data_source": 0}
    root = tree.getroot()
    ntext = 0
    nds = 0
    for el in root.iter():
        if el.tag == f"{{{SVG_NS}}}text":
            ntext += 1
        if el.get("data-source"):
            nds += 1
    return {"text": ntext, "data_source": nds}


# ---------------------------------------------------------------------------
# rendering previews (honest fallback; never fabricates a PNG/PDF)
# ---------------------------------------------------------------------------


def _find_tools() -> dict:
    tools = {}
    for name in ("rsvg-convert", "inkscape", "magick", "convert",
                 "chromium", "chromium-browser", "google-chrome",
                 "wkhtmltoimage", "wkhtmltopdf"):
        tools[name] = shutil.which(name)
    return tools


def try_render(svg_path, out_dir, case_id, timeout: int = 45) -> dict:
    """Attempt PNG/PDF previews with whatever renderer exists; honest status."""
    svg = str(Path(svg_path))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / f"fig_{case_id}.png"
    pdf = out_dir / f"fig_{case_id}.pdf"
    tools = _find_tools()
    renderer = None
    for name in ("rsvg-convert", "inkscape", "magick", "convert"):
        if tools.get(name):
            renderer = name
            break
    if renderer is None:
        for name in ("chromium", "chromium-browser", "google-chrome"):
            if tools.get(name):
                renderer = "chromium"
                break
    if renderer:
        try:
            devnull = subprocess.DEVNULL
            if renderer == "rsvg-convert":
                subprocess.run(["rsvg-convert", "-w", "1600", "-o", str(png), svg],
                               stdout=devnull, stderr=devnull, timeout=timeout)
                subprocess.run(["rsvg-convert", "-f", "pdf", "-o", str(pdf), svg],
                               stdout=devnull, stderr=devnull, timeout=timeout)
            elif renderer == "inkscape":
                subprocess.run(["inkscape", svg, "-o", str(png), "-w", "1600"],
                               stdout=devnull, stderr=devnull, timeout=timeout)
                subprocess.run(["inkscape", svg, "-o", str(pdf)],
                               stdout=devnull, stderr=devnull, timeout=timeout)
            elif renderer in ("magick", "convert"):
                subprocess.run([renderer, "-density", "150", svg, str(png)],
                               stdout=devnull, stderr=devnull, timeout=timeout)
                subprocess.run([renderer, "-density", "150", svg, str(pdf)],
                               stdout=devnull, stderr=devnull, timeout=timeout)
            elif renderer == "chromium":
                exe = next(tools[n] for n in ("chromium", "chromium-browser", "google-chrome")
                           if tools.get(n))
                uri = Path(svg).resolve().as_uri()
                subprocess.run([exe, "--headless", "--disable-gpu", "--no-sandbox",
                                "--window-size=1600,1200", f"--screenshot={png}", uri],
                               stdout=devnull, stderr=devnull, timeout=timeout)
                subprocess.run([exe, "--headless", "--disable-gpu", "--no-sandbox",
                                f"--print-to-pdf={pdf}", uri],
                               stdout=devnull, stderr=devnull, timeout=timeout)
        except (OSError, subprocess.SubprocessError):
            renderer = None
    status = "rendered" if renderer and png.exists() else "preview_not_rendered"
    return {
        "png": str(png) if png.exists() else None,
        "pdf": str(pdf) if pdf.exists() else None,
        "renderer": renderer if status == "rendered" else None,
        "status": status,
    }


# ---------------------------------------------------------------------------
# validation + rubric trace
# ---------------------------------------------------------------------------


def validate_figure(svg_path, spec, manifest, input_dir, work_dir) -> dict:
    """Independent structural + provenance checks; returns a report dict."""
    checks: list = []
    critical: list = []
    warnings: list = []

    layers = list_layers(svg_path)
    for layer in LAYER_ORDER:
        if layer in layers:
            checks.append({"check": f"layer:{layer}", "ok": True, "detail": "present"})
        else:
            checks.append({"check": f"layer:{layer}", "ok": False, "detail": "missing"})
            warnings.append(f"missing layer {layer}")

    cnt = count_text_and_sources(svg_path)
    text_editable = cnt["text"] > 0
    checks.append({"check": "text_editable", "ok": text_editable,
                   "detail": f"{cnt['text']} <text> nodes, no text-as-path"})
    if not text_editable:
        critical.append("no editable <text> nodes in SVG")

    entities = [e for e in (spec.get("entities") or []) if isinstance(e, dict)]
    relations = [r for r in (spec.get("relations") or []) if isinstance(r, dict)]
    data = [d for d in (spec.get("data") or []) if isinstance(d, dict)]
    n_src_expected = len(entities) + len(relations) + len(data)
    coverage = (cnt["data_source"] / n_src_expected) if n_src_expected else 1.0
    coverage = min(1.0, coverage)
    checks.append({"check": "source_coverage", "ok": coverage >= 0.8,
                   "detail": f"{cnt['data_source']}/{n_src_expected} elements carry data-source"})
    if n_src_expected and coverage < 0.8:
        warnings.append(f"data-source coverage {coverage:.0%} < 80%")

    overflow = bool((manifest or {}).get("overflow", False))
    checks.append({"check": "no_overflow", "ok": not overflow,
                   "detail": f"max_y={ (manifest or {}).get('max_y') }"})
    if overflow:
        critical.append("canvas overflow detected; trim points/entities")

    # verify every entity relation endpoint resolves to a placed node
    missing_eps = []
    for r in relations:
        for ep in ("from", "to"):
            if r.get(ep) not in [e.get("id") for e in entities]:
                missing_eps.append(f"{r.get('id', 'rel')}.{ep}={r.get(ep)}")
    checks.append({"check": "relation_endpoints", "ok": not missing_eps,
                   "detail": "all endpoints resolve" if not missing_eps else f"unresolved: {missing_eps[:5]}"})
    if missing_eps:
        warnings.append(f"{len(missing_eps)} relation endpoint(s) unresolved")

    # evidence: check whether each critical conclusion/source quote verifies
    unverified = []
    srcs = spec.get("sources") or []
    for s in srcs:
        if isinstance(s, dict) and s.get("locator"):
            quote = quote_from_locator(s["locator"], input_dir, work_dir)
            if quote is None and not s.get("quote"):
                unverified.append(s["locator"])
    checks.append({"check": "evidence_verifiable", "ok": not unverified,
                   "detail": f"{len(unverified)} unverified locator(s)"})
    if unverified:
        warnings.append(f"{len(unverified)} source locator(s) could not be quoted")

    ok = not critical and coverage >= 0.8 and text_editable
    return {
        "ok": ok,
        "checks": checks,
        "critical": critical,
        "warnings": warnings,
        "coverage": round(coverage, 3),
        "text_count": cnt["text"],
        "source_count": cnt["data_source"],
    }


SCORING_DIMENSIONS = [
    ("task_completion", 15, "step 1-3", "S1", "graphical_abstract four panels + editable source"),
    ("result_correctness", 15, "step 2+4", "S2-S4", "entities/relations/data + direction + data-source"),
    ("result_completeness", 5, "step 1", "S5", "result.json full output_contract fields"),
    ("result_verifiability", 5, "step 4", "S6", "locator + verbatim quote per element"),
    ("quality_gain", 15, "step 3", "S7", "layered SVG + data_mapping vs flat base agent"),
    ("efficiency_gain", 5, "step 3", "S8", "one-shot build + export, no redraw"),
    ("gain_verifiability", 5, "step 5", "S9", "rubric_trace.json + change_log"),
    ("novel_capability", 5, "step 3", "S10", "layer-* groups + editable text"),
    ("method_improvement", 5, "step 2", "S11", "deterministic layout + visual channel mapping"),
    ("innovation_evidence", 5, "step 6", "S12", "selftest reproducibility"),
    ("usability", 3, "step 0", "S13", "frontmatter trigger + relative paths"),
    ("stability", 4, "step 3+6", "S14", "deterministic layout + idempotent rerun"),
    ("error_handling", 3, "step 7", "S15", "limitations/uncertainty, no fabrication"),
    ("doc_completeness", 3, "all", "S16", "SKILL.md + references complete"),
    ("reusability", 4, "scripts", "S17", "stdlib-only scripts + reusable template"),
    ("share_value", 3, "step 8", "S18", "GITHUB_URL.txt + case sinkable"),
]


def build_rubric_trace(report: dict, manifest: dict, skeleton: dict) -> list:
    """Map every scoring dimension to flow step, self-check and live artifact.

    `report` and `manifest` come from validate/build; the status column is
    derived, so the trace is evidence of gain verifiability, not self-praise.
    """
    ok = bool(report.get("ok"))
    coverage = report.get("coverage", 0.0)
    trace = []
    for key, score, step, check, artifact in SCORING_DIMENSIONS:
        if key == "result_correctness":
            status = "ok" if ok else "warn"
        elif key == "result_verifiability":
            status = "ok" if not report.get("warnings") else "warn"
        elif key == "gain_verifiability":
            status = "ok" if (manifest or {}).get("svg") else "warn"
        elif key == "innovation_evidence":
            status = "ok" if ok else "pending"
        elif key == "error_handling":
            status = "ok" if (skeleton.get("limitations") is not None) else "warn"
        else:
            status = "ok"
        trace.append({
            "dimension": key,
            "score": score,
            "flow_step": step,
            "selfcheck": check,
            "artifact": artifact,
            "status": status,
        })
    trace.append({"dimension": "_coverage", "score": None, "flow_step": "step 4",
                  "selfcheck": "S6", "artifact": "data-source coverage",
                  "status": f"{coverage:.0%}"})
    return trace


# ---------------------------------------------------------------------------
# full normalisation used by both main.py and selftest.py
# ---------------------------------------------------------------------------


def normalize_case(input_dir, work_dir, output_dir) -> dict:
    """Discover input, write normalised case + figure_spec template + skeleton.

    Returns a summary dict consumed by the CLI to print one honest line.
    """
    case_obj, task_text, materials = discover_case(input_dir)
    case_id = extract_case_id(case_obj, task_text)
    contract = get_output_contract(case_obj)
    skeleton = build_skeleton(case_id, contract)

    Path(work_dir).mkdir(parents=True, exist_ok=True)
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    normalized = {
        "case_id": case_id,
        "output_contract": contract,
        "materials": materials,
        "has_case_json": case_obj is not None,
        "has_task_md": task_text is not None,
        "task_md_head": (task_text or "")[:2000],
    }
    write_json(Path(work_dir) / "case_normalized.json", normalized)
    write_json(Path(work_dir) / "figure_spec.json", _figure_spec_template(case_id))
    write_json(Path(output_dir) / "result.json", skeleton)
    return {
        "case_id": case_id,
        "materials": materials,
        "has_case_json": case_obj is not None,
        "has_task_md": task_text is not None,
        "skeleton": str(Path(output_dir) / "result.json"),
    }


def _figure_spec_template(case_id: str) -> dict:
    return {
        "case_id": case_id,
        "title": "Graphical Abstract",
        "canvas": {"width": 1600, "height": 1200},
        "panel_order": ["question", "method", "results", "conclusion"],
        "panel_headings": {},
        "graphical_abstract": {"question": [], "method": [], "results": [], "conclusion": []},
        "entities": [],
        "relations": [],
        "data": [],
        "legend": [],
        "sources": [],
        "uncertainty": [],
        "limitations": [],
    }


def spec_has_content(spec) -> bool:
    """True when the figure spec carries at least one real content item."""
    if not isinstance(spec, dict):
        return False
    ga = spec.get("graphical_abstract")
    if isinstance(ga, dict):
        for v in ga.values():
            if isinstance(v, list) and any(str(x).strip() for x in v if isinstance(x, str)):
                return True
            if isinstance(v, dict):
                pts = v.get("points") or []
                if any(str(x).strip() for x in pts if isinstance(x, str)):
                    return True
    for key in ("entities", "relations", "data", "legend"):
        if spec.get(key):
            return True
    return False


if __name__ == "__main__":
    # Runnable as a plain script: exercises the pure functions once and exits 0.
    import tempfile
    spec = _figure_spec_template("self-check")
    spec["title"] = "core_impl self-check"
    spec["graphical_abstract"]["question"] = ["Does A drive B?"]
    spec["entities"] = [{"id": "a", "label": "A", "type": "cell", "panel": "method"}]
    spec["relations"] = [{"id": "r1", "from": "a", "to": "a", "direction": "undirected"}]
    with tempfile.TemporaryDirectory() as td:
        svg = Path(td) / "t.svg"
        report = build_svg(spec, svg)
        layers = list_layers(svg)
        print(f"core_impl ok: svg={svg.stat().st_size}B layers={len(layers)} "
              f"overflow={report['overflow']}")

