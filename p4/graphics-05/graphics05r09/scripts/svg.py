"""Deterministic layered-SVG builder, renderer fallback and layout validator.

Stdlib only. Everything is a pure function of ``spec`` so the same input produces
the same SVG bytes (fixed palette, fixed order, fixed geometry).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

CANVAS_DEFAULT = {"width": 1400, "height": 900}
MARGIN = 46
PANEL_GAP = 28
PANEL_PAD = 18
PANEL_W = 300
TITLE_SIZE = 22

# Okabe-Ito inspired colourblind-safe, grayscale-distinct palette.
PALETTE = [
    "#0072B2", "#E69F00", "#009E73", "#D55E00",
    "#CC79A7", "#56B4E9", "#F0E442", "#000000",
]


def esc(s):
    return (
        str(s).replace("&", "&amp;").replace("<", "&lt;")
        .replace(">", "&gt;").replace('"', "&quot;")
    )


def _wrap(text, max_chars=26):
    text = str(text or "").strip()
    if not text:
        return [""]
    lines, cur = [], ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > max_chars:
            lines.append(cur)
            cur = word
        elif cur:
            cur += " " + word
        else:
            cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def _luma(hex_color):
    c = (hex_color or "#333333").lstrip("#")
    if len(c) != 6:
        return 0.0
    try:
        r, g, b = (int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return 0.0
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(fg, bg):
    a, b = _luma(fg), _luma(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def _sort_key(e):
    return (e.get("panel") or "zzz", e.get("type") or "zzz", e.get("id") or "")


def resolve_layout(spec):
    """Deep-copy spec and fill every missing geometry deterministically."""
    spec = json.loads(json.dumps(spec or {}))
    canvas = dict(CANVAS_DEFAULT)
    canvas.update(spec.get("canvas") or {})
    spec["canvas"] = canvas
    W, H = canvas["width"], canvas["height"]

    els = [dict(e) for e in spec.get("elements", [])]
    panels = [e for e in els if e.get("type") == "panel"]
    if not panels:
        main = {"id": "panel-main", "type": "panel", "label": "图"}
        panels = [main]
        els = [main] + els
        for e in els:
            e.setdefault("panel", "panel-main")
    spec["elements"] = els
    default_panel = panels[0]["id"]

    panels.sort(key=_sort_key)
    per_row = max(1, (W - 2 * MARGIN + PANEL_GAP) // (PANEL_W + PANEL_GAP))
    rows = (len(panels) + per_row - 1) // per_row
    row_h = (H - 2 * MARGIN - (rows - 1) * PANEL_GAP) // max(1, rows)
    for i, p in enumerate(panels):
        r, c = divmod(i, per_row)
        p.setdefault("x", MARGIN + c * (PANEL_W + PANEL_GAP))
        p.setdefault("y", MARGIN + r * (row_h + PANEL_GAP))
        p.setdefault("w", PANEL_W)
        p.setdefault("h", row_h)

    for p in panels:
        pid = p["id"]
        members = [
            e for e in els
            if e.get("id") != pid and e.get("type") != "arrow"
            and (e.get("panel") or default_panel) == pid
        ]
        members.sort(key=_sort_key)
        cx = p["x"] + PANEL_PAD
        cy = p["y"] + PANEL_PAD + 30
        gap = 12
        inner_w = p["w"] - 2 * PANEL_PAD
        for e in members:
            t = e.get("type")
            if t == "box":
                w, h = inner_w, 54
            elif t == "bar":
                w, h = 46, 150
            elif t == "swatch":
                w, h = 18, 18
            elif t == "text":
                w, h = inner_w, 40
            else:
                w, h = inner_w, 54
            e.setdefault("x", cx)
            e.setdefault("y", cy)
            e.setdefault("w", w)
            e.setdefault("h", h)
            cy += h + gap

    centers = {}
    for e in els:
        if e.get("type") in ("panel", "arrow") or "x" not in e:
            continue
        x, y, w, h = e["x"], e.get("y", 0), e.get("w", 0), e.get("h", 0)
        centers[e["id"]] = (x + w / 2.0, y + h / 2.0)

    for e in els:
        if e.get("type") != "arrow":
            continue
        f, t = centers.get(e.get("from")), centers.get(e.get("to"))
        if f and ("x1" not in e or "y1" not in e):
            e["x1"], e["y1"] = f[0], f[1]
        if t and ("x2" not in e or "y2" not in e):
            e["x2"], e["y2"] = t[0], t[1]
        e.setdefault("x1", 0.0)
        e.setdefault("y1", 0.0)
        e.setdefault("x2", 0.0)
        e.setdefault("y2", 0.0)
    return spec


def _assign_colors(els):
    idx = 0
    for e in sorted(els, key=_sort_key):
        if e.get("type") not in ("box", "bar", "swatch"):
            continue
        if not e.get("fill"):
            e["fill"] = PALETTE[idx % len(PALETTE)]
            idx += 1
    return els


def _source_attr(e):
    s = e.get("source") or {}
    loc = s.get("locator") if isinstance(s, dict) else str(s)
    return f' data-source="{esc(loc or "")}"'


def _clip_end(ax, ay, bx, by, box):
    """Return point where segment from B toward A exits the rectangle ``box``."""
    bx0, by0 = box.get("x", 0), box.get("y", 0)
    bw, bh = box.get("w", 0), box.get("h", 0)
    dx, dy = ax - bx, ay - by
    ts = []
    if abs(dx) > 1e-9:
        ts.append((bx0 - bx) / dx)
        ts.append((bx0 + bw - bx) / dx)
    if abs(dy) > 1e-9:
        ts.append((by0 - by) / dy)
        ts.append((by0 + bh - by) / dy)
    t = min((t for t in ts if 1e-9 <= t <= 1.0), default=0.0)
    return (bx + t * dx, by + t * dy)


def _render_text(e):
    size = e.get("size", 14)
    fill = e.get("text_fill") or "#111111"
    lines = _wrap(e.get("text", "") or e.get("label", ""), max_chars=e.get("wrap", 26))
    x, y = e.get("x", 0), e.get("y", 0)
    tspans = ""
    for i, line in enumerate(lines):
        dy = 0.0 if i == 0 else size * 1.25
        tspans += f'<tspan x="{x}" dy="{dy:.1f}">{esc(line)}</tspan>'
    return (
        f'<text x="{x}" y="{y + size * 0.8:.1f}" font-size="{size}" '
        f'font-family="sans-serif" fill="{fill}">{tspans}</text>'
    )


def _render_box(e):
    fill = e.get("fill", "#0072B2")
    stroke = e.get("stroke", "#333333")
    x, y, w, h = e["x"], e["y"], e["w"], e["h"]
    rect = (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>'
    )
    tcolor = "#111111" if _luma(fill) > 0.55 else "#ffffff"
    text = e.get("text") or e.get("label") or ""
    size = e.get("size", 13)
    lines = _wrap(text, max_chars=max(8, int(w // 7.2)))
    lh = size * 1.25
    ty = y + (h - len(lines) * lh) / 2.0 + size * 0.85
    tspans = ""
    for i, line in enumerate(lines):
        dy = 0.0 if i == 0 else lh
        tspans += (
            f'<tspan x="{x + w / 2.0:.1f}" dy="{dy:.1f}" '
            f'text-anchor="middle">{esc(line)}</tspan>'
        )
    text_el = (
        f'<text x="{x + w / 2.0:.1f}" y="{ty:.1f}" font-size="{size}" '
        f'font-family="sans-serif" fill="{tcolor}" text-anchor="middle">{tspans}</text>'
    )
    return rect + text_el


def _render_bar(e):
    x, y, w, h = e["x"], e["y"], e["w"], e["h"]
    val = float(e.get("value", 0) or 0)
    mx = float(e.get("max", 1) or 1) or 1.0
    ratio = max(0.0, min(1.0, val / mx))
    top, lab_pad = 12, 20
    body = max(20.0, h - top - lab_pad)
    base_y = y + top + body
    bh = body * ratio
    by = base_y - bh
    fill = e.get("fill", "#0072B2")
    unit = e.get("unit", "")
    base = (
        f'<line x1="{x - 6}" y1="{base_y:.1f}" x2="{x + w + 6}" y2="{base_y:.1f}" '
        f'stroke="#999999" stroke-width="1"/>'
    )
    rect = (
        f'<rect x="{x}" y="{by:.1f}" width="{w}" height="{bh:.1f}" '
        f'fill="{fill}" stroke="#333333" stroke-width="1"/>'
    )
    vtext = (
        f'<text x="{x + w / 2.0:.1f}" y="{by - 4:.1f}" font-size="13" '
        f'text-anchor="middle" fill="#111111">{esc(f"{val}{unit}")}</text>'
    )
    lab = (
        f'<text x="{x + w / 2.0:.1f}" y="{y + h - 4:.1f}" font-size="12" '
        f'text-anchor="middle" fill="#333333">{esc(e.get("label", ""))}</text>'
    )
    return base + rect + vtext + lab


def _render_swatch(e):
    fill = e.get("fill", "#0072B2")
    x, y, w, h = e["x"], e["y"], e["w"], e["h"]
    rect = (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
        f'fill="{fill}" stroke="#333333" stroke-width="1"/>'
    )
    lab = (
        f'<text x="{x + w + 8}" y="{y + h * 0.75:.1f}" font-size="12" '
        f'font-family="sans-serif" fill="#222222">{esc(e.get("label") or e.get("text") or "")}</text>'
    )
    return rect + lab


def _render_panel(p):
    rect = (
        f'<rect x="{p["x"]}" y="{p["y"]}" width="{p["w"]}" height="{p["h"]}" '
        f'rx="8" fill="#f7f7f7" stroke="#c9c9c9" stroke-width="1.2"/>'
    )
    lab = (
        f'<text x="{p["x"] + PANEL_PAD}" y="{p["y"] + PANEL_PAD + 4}" font-size="16" '
        f'font-family="sans-serif" font-weight="700" fill="#111111">'
        f'{esc(p.get("label") or p.get("text") or "")}</text>'
    )
    return rect + lab


def _render_arrow(e, by_id):
    x1, y1, x2, y2 = e["x1"], e["y1"], e["x2"], e["y2"]
    f, t = by_id.get(e.get("from")), by_id.get(e.get("to"))
    if f and f.get("type") != "panel":
        x1, y1 = _clip_end(x2, y2, x1, y1, f)
    if t and t.get("type") != "panel":
        x2, y2 = _clip_end(x1, y1, x2, y2, t)
    directed = e.get("directed", True)
    mk = ' marker-end="url(#arrowhead)"' if directed else ""
    line = (
        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
        f'stroke="#333333" stroke-width="2"{mk}/>'
    )
    lab = ""
    if e.get("label"):
        mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0 - 6
        lab = (
            f'<text x="{mx:.1f}" y="{my:.1f}" font-size="12" text-anchor="middle" '
            f'font-family="sans-serif" fill="#222222">{esc(e["label"])}</text>'
        )
    return line + lab


def _render_typed(e):
    t = e.get("type")
    if t == "text":
        return _render_text(e)
    if t == "bar":
        return _render_bar(e)
    if t == "swatch":
        return _render_swatch(e)
    return _render_box(e)


def build_svg(spec):
    spec = resolve_layout(spec)
    _assign_colors(spec["elements"])
    W, H = spec["canvas"]["width"], spec["canvas"]["height"]
    els = spec["elements"]
    by_id = {e["id"]: e for e in els if e.get("id")}
    panels = [e for e in els if e.get("type") == "panel"]
    default_panel = panels[0]["id"] if panels else None

    def panel_of(e):
        if e.get("type") == "arrow":
            src = by_id.get(e.get("from"))
            return e.get("panel") or (src or {}).get("panel") or default_panel
        return e.get("panel") or default_panel

    defs = (
        '<defs><marker id="arrowhead" markerWidth="10" markerHeight="8" '
        'refX="9" refY="4" orient="auto" markerUnits="strokeWidth">'
        '<path d="M0,0 L10,4 L0,8 Z" fill="#333333"/></marker></defs>'
    )

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}">',
        defs,
        '<g id="layer-frame" data-role="frame">',
        f'<rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff"/>',
        f'<text x="{MARGIN}" y="32" font-size="{TITLE_SIZE}" font-family="sans-serif" '
        f'font-weight="700" fill="#111111">{esc(spec.get("title") or "Graphical Abstract")}</text>',
        f'<text x="{W - MARGIN}" y="32" font-size="12" text-anchor="end" '
        f'font-family="sans-serif" fill="#666666">{esc(spec.get("case_id") or "")}</text>',
        "</g>",
    ]

    for p in panels:
        parts.append(
            f'<g id="layer-{esc(p["id"])}" data-role="panel" '
            f'data-label="{esc(p.get("label") or "")}">'
        )
        parts.append(_render_panel(p))
        members = [
            e for e in els
            if e.get("id") != p["id"] and e.get("type") != "arrow" and panel_of(e) == p["id"]
        ]
        members.sort(key=_sort_key)
        for e in members:
            parts.append(
                f'<g id="{esc(e.get("id", ""))}" data-id="{esc(e.get("id", ""))}" '
                f'data-type="{esc(e.get("type", ""))}"{_source_attr(e)}>{_render_typed(e)}</g>'
            )
        arrows = [e for e in els if e.get("type") == "arrow" and panel_of(e) == p["id"]]
        for e in arrows:
            parts.append(
                f'<g id="{esc(e.get("id", ""))}" data-id="{esc(e.get("id", ""))}" '
                f'data-type="arrow"{_source_attr(e)}>{_render_arrow(e, by_id)}</g>'
            )
        parts.append("</g>")

    parts.append("</svg>")
    return "\n".join(parts)


def _run(cmd, timeout=60):
    try:
        subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=timeout, check=False,
        )
        return True
    except Exception:
        return False


def _which_first(names):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def render_exports(svg_path, out_dir, case_id):
    """Attempt real PNG/PDF previews with whatever renderers exist; never fake."""
    exports = []
    width = 1400
    png_path = os.path.join(out_dir, f"fig_{case_id}.png")
    pdf_path = os.path.join(out_dir, f"fig_{case_id}.pdf")

    chromium = _which_first(["chromium", "chromium-browser", "google-chrome", "chrome"])
    attempts = {
        "png": [
            ("cairosvg", ["python3", "-m", "cairosvg", svg_path, "-o", png_path,
                          "--output-width", str(width)]),
            ("rsvg-convert", ["rsvg-convert", "-w", str(width), svg_path, "-o", png_path]),
            ("inkscape", ["inkscape", svg_path, "--export-type=png",
                          "--export-filename=" + png_path, "--export-width=" + str(width)]),
        ],
        "pdf": [
            ("cairosvg", ["python3", "-m", "cairosvg", svg_path, "-o", pdf_path]),
            ("rsvg-convert", ["rsvg-convert", "-f", "pdf", svg_path, "-o", pdf_path]),
            ("inkscape", ["inkscape", svg_path, "--export-type=pdf",
                          "--export-filename=" + pdf_path]),
        ],
    }
    if chromium:
        attempts["png"].append(
            ("chromium", [chromium, "--headless", "--disable-gpu",
                          "--screenshot=" + png_path, "--window-size=1400,900", svg_path])
        )
        attempts["pdf"].append(
            ("chromium", [chromium, "--headless", "--disable-gpu",
                          "--print-to-pdf=" + pdf_path, svg_path])
        )

    for fmt, path in (("png", png_path), ("pdf", pdf_path)):
        record = {
            "format": fmt, "path": None, "status": "preview_not_rendered",
            "renderer": None, "width_px": width if fmt == "png" else None,
            "dpi": None, "attempted": [],
        }
        for name, cmd in attempts[fmt]:
            record["attempted"].append(name)
            if not _which_first([cmd[0]]):
                continue
            if _run(cmd) and os.path.isfile(path) and os.path.getsize(path) > 0:
                record.update({"path": path, "status": "rendered", "renderer": name})
                break
        exports.append(record)
    return exports


def validate(spec, svg_text=None):
    spec = resolve_layout(spec)
    W, H = spec["canvas"]["width"], spec["canvas"]["height"]
    cmin = (spec.get("constraints") or {}).get("font_min_px", 8)
    findings = []
    els = spec["elements"]

    if not spec.get("case_id"):
        findings.append({"level": "error", "code": "missing_case_id",
                         "message": "figure_spec 缺少 case_id"})

    if svg_text:
        try:
            root = ET.fromstring(svg_text)
            tag = lambda t: t.split("}")[-1]
            texts = [x for x in root.iter() if tag(x.tag) == "text"]
            text_content = "".join(x.text or "" for x in texts)
            groups = [x for x in root.iter()
                      if tag(x.tag) == "g" and (x.get("id") or "").startswith("layer-")]
            if not texts or not text_content.strip():
                findings.append({"level": "error", "code": "no_text",
                                 "message": "SVG 无可编辑文字"})
            if not groups:
                findings.append({"level": "error", "code": "no_layers",
                                 "message": "SVG 缺少 layer-* 命名分组"})
            for x in texts:
                fs = x.get("font-size")
                m = re.match(r"([0-9.]+)", str(fs)) if fs else None
                if m and float(m.group(1)) < cmin:
                    findings.append({"level": "warn", "code": "font_too_small",
                                     "element": x.get("id"), "message": f"字号 {fs} < {cmin}px"})
            for x in root.iter():
                if tag(x.tag) != "g" or not x.get("id"):
                    continue
                if x.get("id").startswith("layer-"):
                    continue
                if x.get("data-source") is None:
                    findings.append({"level": "warn", "code": "missing_source",
                                     "element": x.get("id"),
                                     "message": "元素缺少 data-source 溯源属性"})
        except ET.ParseError as ex:
            findings.append({"level": "error", "code": "svg_parse",
                             "message": f"SVG 解析失败: {ex}"})

    by_id = {e.get("id") for e in els if e.get("id")}
    for e in els:
        t = e.get("type")
        if t in ("panel", "arrow") or "x" not in e:
            continue
        x, y, w, h = e["x"], e.get("y", 0), e.get("w", 0), e.get("h", 0)
        if x < 0 or y < 0 or x + w > W + 2 or y + h > H + 2:
            findings.append({"level": "warn", "code": "overflow",
                             "element": e.get("id"),
                             "message": f"元素越界 ({x},{y},{w},{h}) 画布 {W}x{H}"})
        if t in ("box", "bar", "swatch") and e.get("fill"):
            if _contrast(e["fill"], "#ffffff") < 1.8:
                findings.append({"level": "warn", "code": "low_contrast",
                                 "element": e.get("id"),
                                 "message": f"填充 {e['fill']} 与白底对比不足"})
        if t == "arrow":
            for end in ("from", "to"):
                if e.get(end) and e.get(end) not in by_id:
                    findings.append({"level": "warn", "code": "dangling_arrow",
                                     "element": e.get("id"),
                                     "message": f"箭头 {end}={e.get(end)} 不存在"})

    errors = [f for f in findings if f["level"] == "error"]
    warnings = [f for f in findings if f["level"] == "warn"]
    return {"ok": not errors, "findings": findings, "errors": errors, "warnings": warnings}
