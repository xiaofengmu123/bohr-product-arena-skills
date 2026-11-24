#!/usr/bin/env python3
"""Deterministic layered-SVG renderer for figure_spec.json (standard library only).

Grammar: see references/figure-spec.md. Editable source: layered <g>, editable
<text>, replaceable font-family. Re-render is deterministic: same spec -> same SVG.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import shutil
import subprocess
import sys

KIND_COLORS = {
    "activation": "#1a73e8",
    "inhibition": "#d93025",
    "contains": "#5f6368",
    "flow": "#188038",
    "causal": "#7b1fa2",
    "sequence": "#5f6368",
    "mapping": "#9334e6",
    "association": "#5f6368",
    "binding": "#1a73e8",
    "phosphorylation": "#e8710a",
}

DEFAULT_FILL = "#eef3fb"
DEFAULT_STROKE = "#3c4043"
DEFAULT_TEXT = "#1a1a1a"


def esc(value):
    return html.escape(str(value), quote=False)


def num(value, default):
    try:
        f = float(value)
        return f if f == f else default  # NaN guard
    except (TypeError, ValueError):
        return default


def _text(x, y, label, fs, color, family, anchor="middle", weight="normal", halo=False):
    lines = str(label).split("\n")
    line_h = fs * 1.2
    y0 = y - (len(lines) - 1) * line_h / 2 + fs * 0.35
    tspans = "".join(
        f'<tspan x="{x:.2f}" y="{(y0 + i * line_h):.2f}">{esc(ln)}</tspan>'
        for i, ln in enumerate(lines)
    )
    halo_attr = f' stroke="#ffffff" stroke-width="{fs * 0.18:.2f}" paint-order="stroke"' if halo else ""
    return (f'<text text-anchor="{anchor}" font-family="{esc(family)}" font-size="{fs:.2f}" '
            f'font-weight="{weight}" fill="{color}"{halo_attr}>{tspans}</text>')


def _arrow_head(x, y, ux, uy, color, size):
    px, py = -uy, ux
    tip = (x, y)
    base = (x - ux * size, y - uy * size)
    p1 = (base[0] + px * size * 0.5, base[1] + py * size * 0.5)
    p2 = (base[0] - px * size * 0.5, base[1] - py * size * 0.5)
    return (f'<polygon points="{tip[0]:.2f},{tip[1]:.2f} {p1[0]:.2f},{p1[1]:.2f} '
            f'{p2[0]:.2f},{p2[1]:.2f}" fill="{color}"/>')


def _tee(x, y, ux, uy, color, size):
    px, py = -uy, ux
    return (f'<line x1="{x - px * size:.2f}" y1="{y - py * size:.2f}" '
            f'x2="{x + px * size:.2f}" y2="{y + py * size:.2f}" '
            f'stroke="{color}" stroke-width="{size * 0.5:.2f}"/>')


def _layout(entities, canvas):
    W = int(num(canvas.get("width_px"), 2400))
    H = int(num(canvas.get("height_px"), 1400))
    pos = {}
    auto = []
    for e in entities:
        eid = str(e.get("id", "e%d" % len(pos)))
        if e.get("x") is not None and e.get("y") is not None:
            pos[eid] = {
                "cx": num(e["x"], 0.5) * W,
                "cy": num(e["y"], 0.5) * H,
                "w": num(e.get("w"), 0.16) * W,
                "h": num(e.get("h"), 0.07) * H,
            }
        else:
            auto.append(eid)
    n = len(auto)
    if n:
        cols = max(1, int(math.ceil(math.sqrt(n))))
        rows = int(math.ceil(n / cols))
        x0, x1 = 0.07 * W, 0.70 * W
        y0, y1 = 0.18 * H, 0.82 * H
        dx = (x1 - x0) / max(1, cols)
        dy = (y1 - y0) / max(1, rows)
        for i, eid in enumerate(auto):
            col, row = i % cols, i // cols
            pos[eid] = {
                "cx": x0 + dx * (col + 0.5),
                "cy": y0 + dy * (row + 0.5),
                "w": 0.16 * W,
                "h": 0.07 * H,
            }
    return pos


def _shape(e, p, base_px, family):
    eid = str(e.get("id"))
    x, y, w, h = p["cx"], p["cy"], p["w"], p["h"]
    shape = e.get("shape", "rounded")
    fill = e.get("fill", DEFAULT_FILL)
    stroke = e.get("stroke", DEFAULT_STROKE)
    sw = max(1.2, num(e.get("stroke_width"), base_px * 0.045))
    fs = num(e.get("font_px"), base_px)
    label = e.get("label", eid)
    half_w, half_h = w / 2, h / 2
    if shape == "ellipse":
        box = (f'<ellipse cx="{x:.2f}" cy="{y:.2f}" rx="{half_w:.2f}" ry="{half_h:.2f}" '
               f'fill="{fill}" stroke="{stroke}" stroke-width="{sw:.2f}"/>')
    elif shape == "circle":
        r = min(w, h) / 2
        box = (f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{r:.2f}" fill="{fill}" '
               f'stroke="{stroke}" stroke-width="{sw:.2f}"/>')
    elif shape == "diamond":
        pts = f"{x:.2f},{y - half_h:.2f} {x + half_w:.2f},{y:.2f} {x:.2f},{y + half_h:.2f} {x - half_w:.2f},{y:.2f}"
        box = f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="{sw:.2f}"/>'
    elif shape == "hexagon":
        pts = " ".join(
            f"{x + half_w * math.cos(math.radians(60 * k + 30)):.2f},{y + half_h * math.sin(math.radians(60 * k + 30)):.2f}"
            for k in range(6)
        )
        box = f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="{sw:.2f}"/>'
    elif shape == "rect":
        box = (f'<rect x="{x - half_w:.2f}" y="{y - half_h:.2f}" width="{w:.2f}" height="{h:.2f}" '
               f'fill="{fill}" stroke="{stroke}" stroke-width="{sw:.2f}"/>')
    else:  # rounded (default)
        r = min(w, h) * 0.25
        box = (f'<rect x="{x - half_w:.2f}" y="{y - half_h:.2f}" width="{w:.2f}" height="{h:.2f}" '
               f'rx="{r:.2f}" ry="{r:.2f}" fill="{fill}" stroke="{stroke}" stroke-width="{sw:.2f}"/>')
    text_color = e.get("text", DEFAULT_TEXT)
    return box + _text(x, y, label, fs, text_color, family)


def _relation_line(r, pos, base_px, family):
    src = pos.get(str(r.get("from")))
    dst = pos.get(str(r.get("to")))
    if not src or not dst:
        return None, f"relation {r.get('id')}: unknown endpoint"
    kind = r.get("kind", "flow")
    color = r.get("color", KIND_COLORS.get(kind, "#5f6368"))
    x1, y1 = src["cx"], src["cy"]
    x2, y2 = dst["cx"], dst["cy"]
    dx, dy = x2 - x1, y2 - y1
    dist = math.hypot(dx, dy) or 1e-6
    ux, uy = dx / dist, dy / dist
    s_short = min(src["w"], src["h"]) / 2 + 8
    t_short = min(dst["w"], dst["h"]) / 2 + 10
    sx, sy = x1 + ux * s_short, y1 + uy * s_short
    tx, ty = x2 - ux * t_short, y2 - uy * t_short
    dash = ' stroke-dasharray="14,10"' if kind in ("association", "mapping") else ""
    sw = max(1.6, num(r.get("stroke_width"), base_px * 0.06))
    parts = [f'<line x1="{sx:.2f}" y1="{sy:.2f}" x2="{tx:.2f}" y2="{ty:.2f}" '
             f'stroke="{color}" stroke-width="{sw:.2f}"{dash}/>']
    style = r.get("style", "")
    if kind == "inhibition" or style == "tee":
        parts.append(_tee(tx, ty, ux, uy, color, base_px * 0.35))
    elif kind == "association" and style != "arrow":
        pass
    else:
        parts.append(_arrow_head(tx, ty, ux, uy, color, base_px * 0.38))
    if r.get("label"):
        mx, my = (sx + tx) / 2, (sy + ty) / 2
        parts.append(_text(mx, my - base_px * 0.5, r["label"], base_px * 0.8, color, family, halo=True))
    return "".join(parts), None


def _groups(spec, pos, base_px, family):
    spec_groups = {str(g.get("id")): g for g in spec.get("groups", [])}
    by_group = {}
    for e in spec.get("entities", []):
        gid = e.get("group")
        if gid is not None:
            by_group.setdefault(str(gid), []).append(str(e.get("id")))
    for gid in spec_groups:
        if gid not in by_group:
            by_group[gid] = [str(m) for m in spec_groups[gid].get("members", [])]
    parts = []
    for gid, members in by_group.items():
        members = [m for m in members if m in pos]
        if not members:
            continue
        label = str((spec_groups.get(gid) or {}).get("label", gid))
        xs = [pos[m]["cx"] - pos[m]["w"] / 2 for m in members]
        ys = [pos[m]["cy"] - pos[m]["h"] / 2 for m in members]
        xe = [pos[m]["cx"] + pos[m]["w"] / 2 for m in members]
        ye = [pos[m]["cy"] + pos[m]["h"] / 2 for m in members]
        pad = base_px * 0.9
        x0, y0 = min(xs) - pad, min(ys) - pad - base_px * 0.8
        x1, y1 = max(xe) + pad, max(ye) + pad
        parts.append(f'<rect x="{x0:.2f}" y="{y0:.2f}" width="{x1 - x0:.2f}" height="{y1 - y0:.2f}" '
                     f'rx="14" fill="none" stroke="#9aa0a6" stroke-width="1.6" stroke-dasharray="8,6"/>')
        parts.append(_text(x0 + 12, y0 + base_px * 0.7, label, base_px * 0.85, "#5f6368", family, anchor="start", weight="bold"))
    return "".join(parts)


def _legend(legend, W, H, base_px, family):
    if not legend:
        return ""
    items = legend.get("items", [])
    if not items:
        return ""
    x = num(legend.get("x"), 0.74) * W
    y = num(legend.get("y"), 0.60) * H
    title = legend.get("title", "Legend")
    w = num(legend.get("w"), 0.22) * W
    item_h = base_px * 1.5
    h = item_h * len(items) + base_px * 1.6
    parts = [f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" rx="12" '
             f'fill="#ffffff" stroke="#9aa0a6" stroke-width="1.4"/>']
    parts.append(_text(x + 12, y + base_px * 0.9, title, base_px * 0.95, "#1a1a1a", family, anchor="start", weight="bold"))
    for i, it in enumerate(items):
        iy = y + base_px * 1.6 + item_h * i + item_h / 2
        color = it.get("color", "#5f6368")
        marker = it.get("marker", "line")
        sx = x + 16
        swatch_w = base_px * 1.3
        if marker in ("arrow", "tee"):
            parts.append(f'<line x1="{sx:.2f}" y1="{iy:.2f}" x2="{(sx + swatch_w):.2f}" y2="{iy:.2f}" '
                         f'stroke="{color}" stroke-width="{base_px * 0.16:.2f}"/>')
            if marker == "arrow":
                parts.append(_arrow_head(sx + swatch_w, iy, 1, 0, color, base_px * 0.28))
            else:
                parts.append(_tee(sx + swatch_w, iy, 1, 0, color, base_px * 0.28))
        elif marker == "circle":
            parts.append(f'<circle cx="{(sx + swatch_w / 2):.2f}" cy="{iy:.2f}" r="{base_px * 0.22:.2f}" fill="{color}"/>')
        else:
            parts.append(f'<rect x="{sx:.2f}" y="{(iy - base_px * 0.2):.2f}" width="{swatch_w:.2f}" '
                         f'height="{base_px * 0.4:.2f}" rx="3" fill="{color}"/>')
        parts.append(_text(sx + swatch_w + 10, iy, it.get("label", ""), base_px * 0.85, "#1a1a1a", family, anchor="start"))
    return "".join(parts)


def render_svg(spec, out_path):
    canvas = spec.get("canvas") or {}
    W = int(num(canvas.get("width_px"), 2400))
    H = int(num(canvas.get("height_px"), 1400))
    family = (spec.get("font") or {}).get("family", "Arial, Helvetica, sans-serif")
    base_px = num((spec.get("font") or {}).get("base_px"), 28)
    entities = spec.get("entities", [])
    relations = spec.get("relations", [])
    annotations = spec.get("annotations", [])
    bg = spec.get("background", "#ffffff")
    pos = _layout(entities, canvas)
    warnings = []
    ent_parts = []
    for e in entities:
        eid = str(e.get("id", "e%d" % len(ent_parts)))
        pos.setdefault(eid, {"cx": 0.5 * W, "cy": 0.5 * H, "w": 0.16 * W, "h": 0.07 * H})
        ent_parts.append(_shape(e, pos[eid], base_px, family))
    rel_parts = []
    for r in relations:
        svg_part, warn = _relation_line(r, pos, base_px, family)
        if warn:
            warnings.append(warn)
        else:
            rel_parts.append(svg_part)
    ann_parts = [
        _text(num(a.get("x"), 0.5) * W, num(a.get("y"), 0.9) * H, a.get("text", ""),
              num(a.get("size_px"), base_px * 0.8), a.get("color", "#555555"), family,
              a.get("anchor", "middle"))
        for a in annotations
    ]
    title_part = ""
    if spec.get("title"):
        title_part = _text(W / 2, base_px * 1.4, spec["title"], base_px * 1.5, "#111111", family, weight="bold")
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!-- Generated from figure_spec.json; edit the spec and re-run scripts/main.py render to redraw. -->\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        f'font-family="{esc(family)}">\n'
        f'  <g id="layer-background"><rect x="0" y="0" width="{W}" height="{H}" fill="{bg}"/></g>\n'
        f'  <g id="layer-groups">{_groups(spec, pos, base_px, family)}</g>\n'
        f'  <g id="layer-relations">{"".join(rel_parts)}</g>\n'
        f'  <g id="layer-entities">{"".join(ent_parts)}</g>\n'
        f'  <g id="layer-legend">{_legend(spec.get("legend"), W, H, base_px, family)}</g>\n'
        f'  <g id="layer-annotations">{"".join(ann_parts)}</g>\n'
        f'  <g id="layer-title">{title_part}</g>\n'
        f'</svg>\n'
    )
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(svg)
    return {"svg_path": out_path, "width_px": W, "height_px": H, "warnings": warnings}


def export_png(svg_path, png_path, width_px=2400, height_px=1400, dpi=300):
    w, h = int(width_px), int(height_px)
    candidates = [
        ("rsvg-convert", ["-w", str(w), "-h", str(h), "-b", "#ffffff", svg_path, "-o", png_path]),
        ("inkscape", [svg_path, "--export-type=png", "--export-filename", png_path,
                      f"--export-width={w}", f"--export-height={h}"]),
        ("magick", ["-density", str(dpi), svg_path, "-resize", f"{w}x{h}!", "-background", "white",
                    "-alpha", "remove", png_path]),
        ("convert", ["-density", str(dpi), svg_path, "-resize", f"{w}x{h}!", "-background", "white",
                     "-alpha", "remove", png_path]),
        ("chrome", ["--headless", "--disable-gpu", f"--screenshot={png_path}",
                    f"--window-size={w},{h}", "--default-background-color=FFFFFFFF", "file://" + os.path.abspath(svg_path)]),
        ("chromium", ["--headless", "--disable-gpu", f"--screenshot={png_path}",
                      f"--window-size={w},{h}", "--default-background-color=FFFFFFFF", "file://" + os.path.abspath(svg_path)]),
        ("google-chrome", ["--headless", "--disable-gpu", f"--screenshot={png_path}",
                           f"--window-size={w},{h}", "--default-background-color=FFFFFFFF", "file://" + os.path.abspath(svg_path)]),
    ]
    tried = []
    for exe, args in candidates:
        path = shutil.which(exe)
        if not path:
            continue
        try:
            r = subprocess.run([path] + args, capture_output=True, text=True, timeout=180)
        except Exception as exc:  # noqa: BLE001
            tried.append({"renderer": exe, "ok": False, "reason": str(exc)})
            continue
        if r.returncode == 0 and os.path.exists(png_path) and os.path.getsize(png_path) > 0:
            return {"ok": True, "renderer": exe, "png_path": png_path, "dpi": int(dpi), "tried": tried}
        tried.append({"renderer": exe, "ok": False, "reason": (r.stderr or r.stdout or "").strip()[:200]})
    return {"ok": False, "renderer": None, "png_path": None, "dpi": int(dpi), "tried": tried,
            "reason": "no SVG rasterizer found (rsvg-convert/inkscape/magick/chrome); "
                      "use browser-automation or cairosvg, or keep SVG only and mark export_files failed"}


def main(argv=None):
    ap = argparse.ArgumentParser(description="render figure_spec.json to a layered SVG (and best-effort PNG)")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--png", default=None)
    ap.add_argument("--dpi", default=None)
    a = ap.parse_args(argv)
    data = {}
    with open(a.spec, "r", encoding="utf-8-sig", errors="replace") as fh:
        data = json.load(fh)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    info = render_svg(data, a.out)
    png_info = {"ok": False, "reason": "not requested"}
    if a.png:
        dpi = int(a.dpi) if a.dpi else int(num((data.get("canvas") or {}).get("dpi"), 300))
        png_info = export_png(a.out, a.png, width_px=info["width_px"], height_px=info["height_px"], dpi=dpi)
    print(json.dumps({"ok": True, "svg": info["svg_path"], "png": png_info, "warnings": info["warnings"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
