#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — deterministic layered-SVG builder, exporter, validator and finalizer.

Standard library only. Invoke with cwd = /skills/weekly-skill so relative paths
`scripts/*.py` resolve, or run `python -B scripts/main.py <subcommand>` directly
(the script inserts its own directory into sys.path).

Subcommands (all accept --input/--output/--work, defaulting to /input,/app/output,/app/work):
  skeleton   normalize input + persist contract-complete partial result.json (delegates
             to extract_case.py)
  build      render fig_<case_id>.svg + fig_manifest.json from <work>/figure_spec.json
  export     attempt real PNG/PDF renders from the SVG; failures recorded honestly
  validate   check result.json contract alignment + SVG structure + exports
  finalize   reconcile result.json with the case contract, append change_log, set status
  all        skeleton -> build -> export -> validate -> finalize in one pass
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract_case  # noqa: E402

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
STATUSES = ("completed", "partial", "failed")
MIN_FONT_PX = 8

# Okabe-Ito colorblind-safe palette (grayscale-distinguishable luminance order).
PALETTE = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9",
           "#F0E442", "#000000"]


def utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def esc(value):
    if value is None:
        return ""
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;"))


def wrap_text(text, max_chars=30):
    if text is None:
        return []
    text = str(text)
    if not text.strip():
        return [""]
    lines, cur = [], ""
    for word in text.split():
        candidate = word if not cur else cur + " " + word
        if len(candidate) <= max_chars:
            cur = candidate
        else:
            if cur:
                lines.append(cur)
            while len(word) > max_chars:
                lines.append(word[:max_chars])
                word = word[max_chars:]
            cur = word
    if cur:
        lines.append(cur)
    return lines or [text]


def _attr(dct, key, default):
    return dct.get(key) if isinstance(dct, dict) else default


class SvgBuilder:
    """Deterministic renderer: same spec -> same SVG bytes (no randomness)."""

    def __init__(self, spec, case_id):
        self.spec = spec if isinstance(spec, dict) else {}
        self.case_id = case_id or "case"
        self.canvas = self.spec.get("canvas", {}) if isinstance(self.spec.get("canvas"), dict) else {}
        self.w = int(self.canvas.get("width", 1200) or 1200)
        self.h = int(self.canvas.get("height", 1600) or 1600)
        self.bg = self.canvas.get("background", "#FFFFFF")
        self.palette = self.spec.get("palette") or PALETTE
        self.errors = []
        self.warnings = []
        self._entity_centers = {}
        self._auto_cursor = {"x": 60, "y": 140, "row_h": 0}

    # ---- layout helpers ----------------------------------------------------
    def _resolve_entity_layout(self, entities):
        """Fill missing coordinates with a deterministic two-column grid."""
        cell_w, cell_h, gap_x, gap_y = 520, 64, 40, 36
        cols = 2
        x0, y0 = 60, 140
        for idx, ent in enumerate(entities):
            if not isinstance(ent, dict):
                continue
            if _attr(ent, "x", None) is None or _attr(ent, "y", None) is None:
                col = idx % cols
                row = idx // cols
                ent["x"] = x0 + col * (cell_w + gap_x)
                ent["y"] = y0 + row * (cell_h + gap_y)
            ent.setdefault("w", cell_w)
            ent.setdefault("h", cell_h)

    def _center(self, ent_id):
        return self._entity_centers.get(ent_id)

    # ---- render ------------------------------------------------------------
    def render(self) -> str:
        parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" '
                 f'height="{self.h}" viewBox="0 0 {self.w} {self.h}" role="img">']
        parts.append("<defs>")
        parts.append('<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
                     'markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
                     '<path d="M0,0 L10,5 L0,10 z" fill="#000000"/></marker>')
        parts.append("</defs>")
        parts.append(f'<g id="layer-background" data-layer="background">'
                     f'<rect x="0" y="0" width="{self.w}" height="{self.h}" '
                     f'fill="{esc(self.bg)}"/></g>')
        self._render_title(parts)
        self._render_panels(parts)
        self._render_entities(parts)
        self._render_relations(parts)
        self._render_data(parts)
        self._render_legend(parts)
        self._render_footnote(parts)
        parts.append("</svg>")
        return "\n".join(parts) + "\n"

    def _text(self, x, y, text, size, anchor="start", weight="normal",
              source=None, fill="#000000"):
        return (f'<text x="{x}" y="{y}" text-anchor="{anchor}" '
                f'font-family="Arial,Helvetica,sans-serif" font-size="{size}" '
                f'font-weight="{weight}" fill="{esc(fill)}"'
                + (f' data-source="{esc(source)}"' if source else "")
                + f'>{esc(text)}</text>')

    def _render_title(self, parts):
        title = self.spec.get("title") or f"Graphical abstract — {self.case_id}"
        parts.append('<g id="layer-title" data-layer="title">')
        parts.append(self._text(self.w / 2, 46, title, 30, anchor="middle",
                                weight="bold", source="title"))
        parts.append("</g>")

    def _render_panels(self, parts):
        panels = [p for p in self.spec.get("panels", []) if isinstance(p, dict)]
        parts.append('<g id="layer-panels" data-layer="panels">')
        for i, panel in enumerate(panels):
            x = int(_attr(panel, "x", 40))
            y = int(_attr(panel, "y", 100))
            w = int(_attr(panel, "w", 540))
            h = int(_attr(panel, "h", 120))
            fill = _attr(panel, "fill", "#F2F2F2")
            stroke = _attr(panel, "stroke", "#B0B0B0")
            label = _attr(panel, "title", "") or ""
            sid = _attr(panel, "id", f"panel_{i}")
            src = _attr(panel, "source", None)
            parts.append(f'<rect id="{esc(sid)}" x="{x}" y="{y}" width="{w}" '
                         f'height="{h}" rx="6" fill="{esc(fill)}" stroke="{esc(stroke)}"'
                         + (f' data-source="{esc(src)}"' if src else "")
                         + "/>")
            parts.append(self._text(x + 12, y + 26, label, 16, weight="bold",
                                    source=src))
            content = _attr(panel, "content", None)
            if content:
                for ln, line in enumerate(wrap_text(content, max(20, w // 24))):
                    parts.append(self._text(x + 12, y + 50 + ln * 20, line, 13,
                                            source=src))
        parts.append("</g>")

    def _render_entities(self, parts):
        entities = [e for e in self.spec.get("entities", []) if isinstance(e, dict)]
        self._resolve_entity_layout(entities)
        parts.append('<g id="layer-entities" data-layer="entities">')
        for ent in entities:
            eid = str(_attr(ent, "id", f"ent_{len(self._entity_centers)}"))
            label = str(_attr(ent, "label", eid))
            kind = _attr(ent, "kind", "entity")
            x = int(_attr(ent, "x", 60))
            y = int(_attr(ent, "y", 140))
            w = int(_attr(ent, "w", 520))
            h = int(_attr(ent, "h", 64))
            src = _attr(ent, "source", None)
            color = self.palette[(len(self._entity_centers)) % len(self.palette)]
            self._entity_centers[eid] = (x + w / 2, y + h / 2)
            parts.append(f'<rect id="{esc(eid)}" x="{x}" y="{y}" width="{w}" '
                         f'height="{h}" rx="8" fill="{esc(color)}" '
                         f'fill-opacity="0.16" stroke="{esc(color)}" stroke-width="2"'
                         + (f' data-source="{esc(src)}"' if src else "")
                         + "/>")
            parts.append(self._text(x + w / 2, y + h / 2 - 4, label, 15,
                                    anchor="middle", weight="bold", source=src,
                                    fill="#111111"))
            parts.append(self._text(x + w / 2, y + h / 2 + 16, kind, 11,
                                    anchor="middle", source=src, fill="#333333"))
        parts.append("</g>")

    def _render_relations(self, parts):
        relations = [r for r in self.spec.get("relations", []) if isinstance(r, dict)]
        parts.append('<g id="layer-relations" data-layer="relations">')
        for rel in relations:
            frm = self._center(str(_attr(rel, "from", "")))
            to = self._center(str(_attr(rel, "to", "")))
            direction = _attr(rel, "direction", "forward")
            kind = str(_attr(rel, "kind", "undefined"))
            label = _attr(rel, "label", None)
            src = _attr(rel, "source", None)
            if frm is None or to is None:
                self.warnings.append(
                    f"relation {_attr(rel, 'id', '?')}: endpoint not found, skipped")
                continue
            x1, y1 = frm
            x2, y2 = to
            marker = ""
            if direction in ("forward", "backward"):
                marker = ' marker-end="url(#arrow)"'
                if direction == "backward":
                    x1, y1, x2, y2 = x2, y2, x1, y1
            parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" '
                         f'y2="{y2:.1f}" stroke="#000000" stroke-width="2"'
                         + marker
                         + (f' data-source="{esc(src)}"' if src else "")
                         + "/>")
            if label:
                parts.append(self._text((x1 + x2) / 2, (y1 + y2) / 2 - 6, label,
                                        12, anchor="middle", source=src,
                                        fill="#000000"))
            if direction not in ("forward", "backward"):
                self.warnings.append(
                    f"relation {_attr(rel, 'id', '?')}: undirected/ambiguous, noted")
        parts.append("</g>")

    def _render_data(self, parts):
        data_items = [d for d in self.spec.get("data", []) if isinstance(d, dict)]
        if not data_items:
            return
        parts.append('<g id="layer-data" data-layer="data">')
        x0, y0 = 60, 320
        row_h = 46
        for i, item in enumerate(data_items):
            y = y0 + i * row_h
            channel = _attr(item, "visual_channel", _attr(item, "channel", "length"))
            label = str(_attr(item, "label", "value"))
            value = _attr(item, "value", None)
            unit = _attr(item, "unit", "")
            src = _attr(item, "source", None)
            parts.append(self._text(x0, y + 16, label, 13, source=src))
            if value is None:
                parts.append(self._text(x0 + 220, y + 16, "not_reported", 12,
                                        source=src, fill="#888888"))
                self.warnings.append(f"data {_attr(item, 'id', i)}: value missing")
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                parts.append(self._text(x0 + 220, y + 16, str(value), 12,
                                        source=src))
                continue
            if channel in ("length", "position", "size"):
                if abs(numeric) <= 1.0:
                    bar_len = max(0.0, min(1.0, numeric))
                else:
                    bar_len = max(0.0, min(1.0, numeric / 100.0))
                parts.append(f'<rect x="{x0 + 220}" y="{y}" width="{bar_len * 500:.0f}" '
                             f'height="18" fill="{self.palette[i % len(self.palette)]}"'
                             + (f' data-source="{esc(src)}"' if src else "") + "/>")
            elif channel in ("color", "luminance", "area"):
                parts.append(f'<rect x="{x0 + 220}" y="{y}" width="24" height="18" '
                             f'fill="{self.palette[i % len(self.palette)]}"'
                             + (f' data-source="{esc(src)}"' if src else "") + "/>")
            parts.append(self._text(x0 + 740, y + 16,
                                    f"{value}{(' ' + str(unit)) if unit else ''}",
                                    13, source=src))
        parts.append("</g>")

    def _render_legend(self, parts):
        items = [g for g in self.spec.get("legend", []) if isinstance(g, dict)]
        if not items:
            return
        parts.append('<g id="layer-legend" data-layer="legend">')
        x0, y0 = 60, 1500
        for i, item in enumerate(items):
            col = i % 3
            row = i // 3
            x = x0 + col * 360
            y = y0 + row * 40
            color = _attr(item, "color", self.palette[i % len(self.palette)])
            label = str(_attr(item, "label", ""))
            src = _attr(item, "source", None)
            parts.append(f'<rect x="{x}" y="{y}" width="22" height="16" rx="3" '
                         f'fill="{esc(color)}" stroke="#666666"'
                         + (f' data-source="{esc(src)}"' if src else "") + "/>")
            parts.append(self._text(x + 32, y + 14, label, 13, source=src))
        parts.append("</g>")

    def _render_footnote(self, parts):
        footnote = self.spec.get("footnote")
        if not footnote:
            return
        parts.append('<g id="layer-footnote" data-layer="footnote">')
        parts.append(self._text(self.w / 2, self.h - 24, footnote, 11,
                                anchor="middle", source="footnote", fill="#555555"))
        parts.append("</g>")


def _read_json(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def build(work_dir=DEFAULT_WORK, output_dir=DEFAULT_OUTPUT):
    spec = _read_json(os.path.join(work_dir, "figure_spec.json")) or {}
    case_id = str(spec.get("case_id") or "case")
    builder = SvgBuilder(spec, case_id)
    svg_text = builder.render()
    svg_path = os.path.join(output_dir, f"fig_{case_id}.svg")
    _write_text(svg_path, svg_text)

    manifest = {
        "case_id": case_id,
        "svg": os.path.basename(svg_path),
        "canvas": {"width": builder.w, "height": builder.h},
        "layers": ["layer-background", "layer-title", "layer-panels", "layer-entities",
                   "layer-relations", "layer-data", "layer-legend", "layer-footnote"],
        "text_editable": True,
        "errors": builder.errors,
        "warnings": builder.warnings,
    }
    manifest_path = os.path.join(output_dir, "fig_manifest.json")
    _write_json(manifest_path, manifest)
    return svg_path, manifest_path, manifest


def _write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


# ---- export ----------------------------------------------------------------
def _which(binary):
    return shutil.which(binary)


def attempt_export(svg_path, output_dir, case_id):
    """Try available SVG renderers for PNG/PDF; record the exact attempt honestly."""
    attempts = []
    png_path = os.path.join(output_dir, f"fig_{case_id}.png")
    pdf_path = os.path.join(output_dir, f"fig_{case_id}.pdf")

    png_candidates = [
        ("rsvg-convert", ["rsvg-convert", "-w", "1200", "-o", png_path, svg_path]),
        ("inkscape", ["inkscape", svg_path, "--export-filename", png_path,
                      "--export-width", "1200"]),
        ("chromium", ["chromium", "--headless", "--disable-gpu",
                      f"--screenshot={png_path}", "--window-size=1200,1600", svg_path]),
        ("google-chrome", ["google-chrome", "--headless", "--disable-gpu",
                           f"--screenshot={png_path}", "--window-size=1200,1600",
                           svg_path]),
    ]
    pdf_candidates = [
        ("rsvg-convert", ["rsvg-convert", "-f", "pdf", "-o", pdf_path, svg_path]),
        ("inkscape", ["inkscape", svg_path, "--export-filename", pdf_path]),
    ]

    def _run(label, candidates, target):
        for name, cmd in candidates:
            if not _which(name):
                attempts.append({"renderer": name, "status": "not_available"})
                continue
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            except (subprocess.TimeoutExpired, OSError) as exc:
                attempts.append({"renderer": name, "status": "failed",
                                 "detail": str(exc)})
                continue
            if proc.returncode == 0 and os.path.isfile(target) and os.path.getsize(target) > 0:
                attempts.append({"renderer": name, "status": "rendered",
                                 "target": os.path.basename(target)})
                return target
            attempts.append({"renderer": name, "status": "failed",
                             "detail": (proc.stderr or proc.stdout or "")[:200]})
        attempts.append({"renderer": label, "status": "preview_not_rendered"})
        return None

    png = _run("png", png_candidates, png_path)
    pdf = _run("pdf", pdf_candidates, pdf_path)

    exports = []
    for target, fmt, label in ((png, "png", "png"), (pdf, "pdf", "pdf")):
        if target and os.path.isfile(target):
            exports.append({"path": os.path.basename(target), "format": fmt,
                            "renderer": _last_renderer(attempts, fmt),
                            "status": "rendered",
                            "width_px": 1200 if fmt == "png" else None,
                            "notes": "real render; verify size/resolution in review"})
        else:
            exports.append({"path": None, "format": fmt, "renderer": None,
                            "status": "preview_not_rendered",
                            "notes": "no available SVG renderer; editable SVG retained"})
    return exports, attempts


def _last_renderer(attempts, fmt):
    for a in reversed(attempts):
        if a.get("status") == "rendered" and a.get("target", "").endswith(fmt):
            return a.get("renderer")
    return None


# ---- validate ----------------------------------------------------------------
def validate(output_dir=DEFAULT_OUTPUT, work_dir=DEFAULT_WORK):
    errors, warnings, checks = [], [], {}
    result = _read_json(os.path.join(output_dir, "result.json"))
    normalized = _read_json(os.path.join(work_dir, "case_normalized.json")) or {}

    if result is None:
        return {"ok": False, "errors": ["result.json missing or unparseable"],
                "warnings": [], "checks": {}}

    # Contract alignment
    case_id = result.get("case_id")
    status = result.get("status")
    checks["has_case_id"] = bool(case_id)
    checks["valid_status"] = status in STATUSES
    if not checks["has_case_id"]:
        errors.append("result.json missing case_id")
    if not checks["valid_status"]:
        errors.append(f"status must be one of {STATUSES}, got {status!r}")

    contract_keys = normalized.get("contract_keys") or \
        [k for k in extract_case.CANONICAL_CONTRACT if k not in ("case_id", "status")]
    missing = [k for k in contract_keys if k not in result]
    checks["contract_aligned"] = not missing
    if missing:
        errors.append(f"result.json missing output_contract fields: {missing}")

    for k in ("sources", "uncertainty", "change_log"):
        if k not in result or not isinstance(result.get(k), list):
            errors.append(f"result.json missing list field {k}")
    if isinstance(result.get("change_log"), list):
        versions = [e.get("version") for e in result["change_log"]
                    if isinstance(e, dict) and isinstance(e.get("version"), int)]
        checks["change_log_monotonic"] = versions == sorted(versions) and len(versions) > 0
        if not checks["change_log_monotonic"]:
            errors.append("change_log empty or version not monotonically increasing")
    else:
        checks["change_log_monotonic"] = False

    # SVG validation
    es = result.get("editable_source") or {}
    svg_rel = es.get("path") if isinstance(es, dict) else None
    svg_path = os.path.join(output_dir, svg_rel) if svg_rel else None
    checks["svg_exists"] = bool(svg_path and os.path.isfile(svg_path))
    if not checks["svg_exists"]:
        warnings.append("editable_source.path missing or SVG not on disk")
    else:
        try:
            tree = ET.parse(svg_path)
            root = tree.getroot()
            layers = [g.get("id") for g in root.iter("{http://www.w3.org/2000/svg}g")
                      if str(g.get("id", "")).startswith("layer-")]
            texts = list(root.iter("{http://www.w3.org/2000/svg}text"))
            src_attrs = [e for e in root.iter() if e.get("data-source")]
            checks["svg_layered"] = len(layers) >= 1
            checks["svg_text_editable"] = len(texts) >= 1
            checks["svg_source_attrs"] = len(src_attrs) >= 1
            if not checks["svg_layered"]:
                errors.append("SVG missing layer-* groups")
            if not checks["svg_text_editable"]:
                errors.append("SVG has no editable <text> elements")
            font_small = [t.get("font-size") for t in texts
                          if t.get("font-size") is not None
                          and float(t.get("font-size")) < MIN_FONT_PX]
            checks["font_min_8px"] = not font_small
            if font_small:
                warnings.append(f"SVG has {len(font_small)} text(s) below {MIN_FONT_PX}px")
        except ET.ParseError as exc:
            checks["svg_parses"] = False
            errors.append(f"SVG unparseable: {exc}")

    # Exports
    exports = result.get("exports") if isinstance(result.get("exports"), list) else []
    checks["exports_recorded"] = len(exports) >= 1
    if not checks["exports_recorded"]:
        warnings.append("exports empty; render step not recorded")

    ok = not errors
    return {"ok": ok, "errors": errors, "warnings": warnings, "checks": checks}


# ---- finalize ----------------------------------------------------------------
def finalize(output_dir=DEFAULT_OUTPUT, work_dir=DEFAULT_WORK, status=None):
    result = _read_json(os.path.join(output_dir, "result.json"))
    normalized = _read_json(os.path.join(work_dir, "case_normalized.json")) or {}
    if result is None:
        result = {"case_id": normalized.get("case_id") or "unknown_case"}

    contract_keys = normalized.get("contract_keys") or \
        [k for k in extract_case.CANONICAL_CONTRACT if k not in ("case_id", "status")]
    for key in contract_keys:
        if key not in result:
            result[key] = extract_case.canonical_default(key)

    report = validate(output_dir, work_dir)
    derived = status
    if derived not in STATUSES:
        derived = "failed" if not report["checks"].get("has_case_id") else \
                  ("completed" if report["ok"] and report["checks"].get("svg_exists")
                   else "partial")
    result["status"] = derived

    change_log = result.get("change_log") if isinstance(result.get("change_log"), list) else []
    next_version = 1
    for entry in change_log:
        if isinstance(entry, dict) and isinstance(entry.get("version"), int):
            next_version = max(next_version, entry["version"] + 1)
    change_log.append({"version": next_version, "time_utc": utcnow(),
                       "what": f"finalize: status={derived}",
                       "why": "contract reconciled and validation report applied"})
    result["change_log"] = change_log

    _write_json(os.path.join(output_dir, "result.json"), result)
    _write_json(os.path.join(output_dir, "validation_report.json"), {
        "case_id": result.get("case_id"), "status": derived, **report,
    })
    return {"case_id": result.get("case_id"), "status": derived,
            "errors": report["errors"], "warnings": report["warnings"]}


def run_all(input_dir, output_dir, work_dir):
    summary = extract_case.normalize(input_dir, output_dir, work_dir)
    svg_path, _, manifest = build(work_dir, output_dir)
    exports, attempts = attempt_export(svg_path, output_dir, summary.get("case_id") or "case")

    result = _read_json(os.path.join(output_dir, "result.json")) or {}
    result["editable_source"] = {
        "path": os.path.basename(svg_path), "format": "svg",
        "layers": manifest.get("layers", []), "text_editable": True,
    }
    result["exports"] = exports
    result.setdefault("change_log", []).append({
        "version": 2, "time_utc": utcnow(),
        "what": "built layered SVG + export attempt",
        "why": "deterministic render from figure_spec.json"})
    _write_json(os.path.join(output_dir, "result.json"), result)
    final = finalize(output_dir, work_dir)
    return final


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("subcommand", nargs="?",
                        choices=["skeleton", "build", "export", "validate",
                                 "finalize", "all"], default="all")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--work", default=DEFAULT_WORK)
    args = parser.parse_args(argv)

    if args.subcommand == "skeleton":
        return extract_case.main(["--input", args.input, "--output", args.output,
                                  "--work", args.work])
    if args.subcommand == "build":
        svg_path, manifest_path, manifest = build(args.work, args.output)
        print(json.dumps({"svg": svg_path, "manifest": manifest_path,
                          "warnings": manifest["warnings"]}, ensure_ascii=False, indent=2))
        return 0
    if args.subcommand == "export":
        result = _read_json(os.path.join(args.output, "result.json")) or {}
        case_id = result.get("case_id") or _read_json(
            os.path.join(args.work, "figure_spec.json")).get("case_id") or "case"
        svg_path = os.path.join(args.output, f"fig_{case_id}.svg")
        exports, attempts = attempt_export(svg_path, args.output, case_id)
        print(json.dumps({"exports": exports, "attempts": attempts},
                         ensure_ascii=False, indent=2))
        return 0
    if args.subcommand == "validate":
        report = validate(args.output, args.work)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    if args.subcommand == "finalize":
        print(json.dumps(finalize(args.output, args.work), ensure_ascii=False, indent=2))
        return 0
    # all
    print(json.dumps(run_all(args.input, args.output, args.work),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
