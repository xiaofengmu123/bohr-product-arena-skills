#!/usr/bin/env python3
"""scix-graphics 出图模块：从 figure spec JSON 生成分层 SVG + PNG/PDF 导出。

纯标准库实现（PNG/PDF 优先尝试可选第三方库，缺失时用内嵌最小矢量 PDF 回退，
并如实标注，不伪造位图）。SVG 元素携带 data-field/data-row 溯源属性。
"""
import argparse
import json
import math
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

MM = 3.779527559  # mm -> px @96dpi

CMAP_STOPS = {
    "viridis": [(68, 1, 84), (59, 82, 139), (33, 145, 140), (94, 201, 98), (253, 231, 37)],
    "coolwarm": [(59, 76, 192), (180, 200, 240), (221, 221, 221), (240, 160, 160), (180, 4, 38)],
    "grayscale": [(0, 0, 0), (255, 255, 255)],
}


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def q(s):
    return str(s).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def nice_step(span, target=5):
    if span <= 0:
        return 1.0
    raw = span / max(target, 1)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1.0, 2.0, 5.0, 10.0):
        if raw <= m * mag + 1e-12:
            return m * mag
    return 10.0 * mag


def ticks(lo, hi, target=5):
    step = nice_step(hi - lo, target)
    start = math.ceil(lo / step - 1e-9) * step
    out = []
    v = start
    while v <= hi + 1e-9:
        out.append(v)
        v += step
    return out, step


def fmt(v):
    a = abs(v)
    if a != 0 and (a >= 1e4 or a < 1e-3):
        return "%.3g" % v
    if abs(v - round(v)) < 1e-9:
        return "%d" % round(v)
    return "%.4g" % v


def colormap(name):
    stops = CMAP_STOPS.get(name, CMAP_STOPS["viridis"])
    n = len(stops) - 1

    def f(t):
        t = max(0.0, min(1.0, t))
        x = t * n
        i = min(int(x), n - 1)
        u = x - i
        a, b = stops[i], stops[i + 1]
        return tuple(int(round(a[k] + (b[k] - a[k]) * u)) for k in range(3))
    return f


def rgb_hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, c)) for c in rgb)


# ---------------------------------------------------------------- elements

def el_rect(x, y, w, h, fill, stroke="none", sw=1, extra=""):
    return ('<rect x="%.3f" y="%.3f" width="%.3f" height="%.3f" fill="%s" stroke="%s" '
            'stroke-width="%.3f" %s/>' % (x, y, w, h, q(fill), q(stroke), sw, extra))


def el_line(x1, y1, x2, y2, stroke, sw=1, dash=None, extra=""):
    d = ' stroke-dasharray="%s"' % dash if dash else ""
    return ('<line x1="%.3f" y1="%.3f" x2="%.3f" y2="%.3f" stroke="%s" stroke-width="%.3f"%s %s/>'
            % (x1, y1, x2, y2, q(stroke), sw, d, extra))


def el_text(x, y, s, size, fill="#111111", anchor="start", baseline=None, weight="normal",
            italic=False, extra=""):
    bs = ' dominant-baseline="%s"' % baseline if baseline else ""
    st = ' font-style="italic"' if italic else ""
    return ('<text x="%.3f" y="%.3f" font-size="%.3f" fill="%s" text-anchor="%s" '
            'font-family="sans-serif" font-weight="%s"%s%s %s>%s</text>'
            % (x, y, size, q(fill), anchor, weight, st, bs, extra, esc(s)))


def el_path(d, stroke, sw=1, fill="none", extra=""):
    return '<path d="%s" fill="%s" stroke="%s" stroke-width="%.3f" %s/>' % (
        q(d), q(fill), q(stroke), sw, extra)


def marker_defs():
    return ("<defs>"
            "<marker id='arr' viewBox='0 0 10 10' refX='9' refY='5' markerWidth='7' "
            "markerHeight='7' orient='auto-start-reverse'>"
            "<path d='M 0 0 L 10 5 L 0 10 z' fill='#333333'/></marker>"
            "</defs>")


# ---------------------------------------------------------------- chart layout

class Chart:
    def __init__(self, spec):
        self.spec = spec
        self.w = num(spec.get("size_mm", [180, 130])[0], 180) * MM
        self.h = num(spec.get("size_mm", [180, 130])[1], 130) * MM
        self.ml = 46.0
        self.mr = 14.0
        self.mt = 34.0 if spec.get("title") else 16.0
        self.mb = 40.0
        self.x0 = self.ml
        self.x1 = self.w - self.mr
        self.y0 = self.mt
        self.y1 = self.h - self.mb
        self.font = num(spec.get("font_size", 8), 8)
        self.bg = spec.get("background", "#ffffff")

    def x(self, u):
        return self.x0 + u * (self.x1 - self.x0)

    def y(self, v):
        return self.y1 - v * (self.y1 - self.y0)

    def axes(self, yticks, step, ylabel, xlabels=None, xlabel=""):
        out = []
        out.append(el_line(self.x0, self.y1, self.x1, self.y1, "#333333", 1))
        out.append(el_line(self.x0, self.y1, self.x0, self.y0, "#333333", 1))
        for t in yticks:
            yy = self.y(t)
            out.append(el_line(self.x0, yy, self.x1, yy, "#e0e0e0", 0.6))
            out.append(el_line(self.x0, yy, self.x0 - 4, yy, "#333333", 1))
            out.append(el_text(self.x0 - 6, yy, fmt(t), self.font, anchor="end",
                               baseline="middle"))
        if ylabel:
            out.append(el_text(self.ml - 36, (self.y0 + self.y1) / 2, ylabel, self.font + 1,
                               anchor="middle", extra='transform="rotate(-90 %.3f %.3f)"'
                               % (self.ml - 36, (self.y0 + self.y1) / 2)))
        if xlabels is not None:
            nx = len(xlabels)
            for i, lab in enumerate(xlabels):
                xx = self.x((i + 0.5) / nx) if nx else self.x0
                out.append(el_text(xx, self.y1 + 16, lab, self.font, anchor="middle"))
        if xlabel:
            out.append(el_text((self.x0 + self.x1) / 2, self.h - 8, xlabel, self.font,
                               anchor="middle"))
        return "".join(out)

    def wrap(self, body):
        title = self.spec.get("title", "")
        tt = ('<text x="%.3f" y="%.3f" font-size="%.3f" font-weight="bold" '
              'text-anchor="middle" font-family="sans-serif" fill="#111111">%s</text>'
              % ((self.x0 + self.x1) / 2, 16, self.font + 2, esc(title))) if title else ""
        return ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<svg xmlns="http://www.w3.org/2000/svg" width="%.3f" height="%.3f" '
                'viewBox="0 0 %.3f %.3f">\n'
                '<rect x="0" y="0" width="%.3f" height="%.3f" fill="%s"/>\n%s\n%s%s\n</svg>\n'
                % (self.w, self.h, self.w, self.h, self.w, self.h, q(self.bg),
                   marker_defs(), tt, body))


def y_range(spec, series):
    lo = spec.get("y_min")
    hi = spec.get("y_max")
    vals = []
    for s in series:
        for p in s.get("data", []):
            y = num(p.get("y"), 0)
            e = num(p.get("err"), 0)
            vals.append(y - e)
            vals.append(y + e)
            vals.append(y)
    if not vals:
        vals = [0, 1]
    if lo is None:
        lo = min(vals)
    if hi is None:
        hi = max(vals)
    if hi - lo < 1e-12:
        hi = lo + 1
    pad = (hi - lo) * 0.06
    if spec.get("y_log"):
        lo = max(1e-12, lo)
        return lo / 2, hi * 2
    return lo - pad, hi + pad


# ---------------------------------------------------------------- renderers

def render_bar(spec):
    c = Chart(spec)
    series = spec.get("series", [])
    cats = spec.get("categories") or []
    if not cats:
        seen = []
        for s in series:
            for p in s.get("data", []):
                x = p.get("x")
                if x not in seen:
                    seen.append(x)
        cats = seen
    n = len(cats) or 1
    lo, hi = y_range(spec, series)
    yt, step = ticks(lo, hi)
    body = [c.axes(yt, step, spec.get("y_label", ""), cats, spec.get("x_label", ""))]
    ns = len(series) or 1
    group_w = (c.x1 - c.x0) / n
    bw = group_w * 0.7 / ns
    for gi, cat in enumerate(cats):
        cx = c.x0 + (gi + 0.5) * group_w
        for si, s in enumerate(series):
            pts = {p.get("x"): p for p in s.get("data", [])}
            p = pts.get(cat)
            if p is None:
                continue
            yv = num(p.get("y"), 0)
            x = cx + (si - (ns - 1) / 2) * bw
            h = c.y(yv) - c.y(0 if lo <= 0 else lo)
            ytop = min(c.y(yv), c.y(0 if lo <= 0 else lo))
            src = ' data-field="%s" data-row="%s"' % (q(p.get("field", s.get("label", ""))),
                                                       q(p.get("row", "")))
            body.append(el_rect(x - bw / 2, ytop, bw, max(0, abs(h)), s.get("color", "#1f77b4"),
                                extra=src))
            e = num(p.get("err"), 0)
            if e > 0:
                body.append(el_line(x, c.y(yv - e), x, c.y(yv + e), "#222222", 1))
                body.append(el_line(x - bw / 4, c.y(yv - e), x + bw / 4, c.y(yv - e), "#222222", 1))
                body.append(el_line(x - bw / 4, c.y(yv + e), x + bw / 4, c.y(yv + e), "#222222", 1))
    # significance brackets
    cat_x = {cat: c.x0 + (gi + 0.5) * group_w for gi, cat in enumerate(cats)}
    top = min(c.y(num(p.get("y"), 0) + num(p.get("err"), 0))
              for s in series for p in s.get("data", [])) if any(
                  s.get("data") for s in series) else c.y0
    level = top - 14
    for sg in spec.get("significance", []):
        a, b = cat_x.get(sg.get("a")), cat_x.get(sg.get("b"))
        if a is None or b is None:
            continue
        body.append(el_line(a, level, a, level - 4, "#333333", 1))
        body.append(el_line(a, level - 4, b, level - 4, "#333333", 1))
        body.append(el_line(b, level, b, level - 4, "#333333", 1))
        body.append(el_text((a + b) / 2, level - 7, sg.get("mark", "*"), c.font, anchor="middle"))
        level -= 12
    # legend
    lx = c.x1 - 10
    ly = c.y0 + 8
    for s in series:
        body.append(el_rect(lx - 20, ly - 7, 12, 12, s.get("color", "#1f77b4")))
        body.append(el_text(lx - 4, ly + 2, s.get("label", "series"), c.font - 1, anchor="start"))
        lx += 12 + 6 * len(s.get("label", "series")) + 10
    return c.wrap("".join(body))


def render_line(spec):
    c = Chart(spec)
    series = spec.get("series", [])
    lo, hi = y_range(spec, series)
    yt, step = ticks(lo, hi)
    body = [c.axes(yt, step, spec.get("y_label", ""), None, spec.get("x_label", ""))]
    xvals = []
    for s in series:
        for p in s.get("data", []):
            xvals.append(num(p.get("x"), 0))
    if not xvals:
        xvals = [0, 1]
    xlo, xhi = spec.get("x_min", min(xvals)), spec.get("x_max", max(xvals))
    if xhi - xlo < 1e-12:
        xhi = xlo + 1
    xt, xstep = ticks(xlo, xhi)
    for t in xt:
        xx = c.x0 + (t - xlo) / (xhi - xlo) * (c.x1 - c.x0)
        body.append(el_line(xx, c.y1, xx, c.y1 + 4, "#333333", 1))
        body.append(el_text(xx, c.y1 + 16, fmt(t), c.font, anchor="middle"))
    def sx(u):
        return c.x0 + (u - xlo) / (xhi - xlo) * (c.x1 - c.x0)
    for s in series:
        pts = sorted(s.get("data", []), key=lambda p: num(p.get("x"), 0))
        d = []
        for p in pts:
            d.append("%.3f %.3f" % (sx(num(p.get("x"))), c.y(num(p.get("y")))))
        body.append(el_path("M " + " L ".join(d), s.get("color", "#1f77b4"), 1.5))
        for p in pts:
            src = ' data-field="%s" data-row="%s"' % (q(p.get("field", s.get("label", ""))),
                                                       q(p.get("row", "")))
            body.append('<circle cx="%.3f" cy="%.3f" r="2.4" fill="%s"%s/>'
                        % (sx(num(p.get("x"))), c.y(num(p.get("y"))), q(s.get("color", "#1f77b4")), src))
            e = num(p.get("err"), 0)
            if e > 0:
                body.append(el_line(sx(num(p.get("x"))), c.y(num(p.get("y")) - e),
                                    sx(num(p.get("x"))), c.y(num(p.get("y")) + e), "#222222", 1))
    lx = c.x1 - 10
    ly = c.y0 + 8
    for s in series:
        body.append(el_line(lx - 20, ly, lx - 8, ly, s.get("color", "#1f77b4"), 2))
        body.append(el_text(lx - 4, ly + 3, s.get("label", "series"), c.font - 1))
        lx += 12 + 6 * len(s.get("label", "series")) + 10
    return c.wrap("".join(body))


def render_scatter(spec):
    c = Chart(spec)
    series = spec.get("series", [])
    xs, ys = [], []
    for s in series:
        for p in s.get("data", []):
            xs.append(num(p.get("x"), 0))
            ys.append(num(p.get("y"), 0))
    if not xs:
        xs, ys = [0, 1], [0, 1]
    xlo, xhi = spec.get("x_min", min(xs)), spec.get("x_max", max(xs))
    if xhi - xlo < 1e-12:
        xhi = xlo + 1
    lo, hi = y_range(spec, series)
    yt, step = ticks(lo, hi)
    xt, xstep = ticks(xlo, xhi)
    body = [c.axes(yt, step, spec.get("y_label", ""), None, spec.get("x_label", ""))]
    def sx(u):
        return c.x0 + (u - xlo) / (xhi - xlo) * (c.x1 - c.x0)
    for t in xt:
        body.append(el_line(sx(t), c.y1, sx(t), c.y1 + 4, "#333333", 1))
        body.append(el_text(sx(t), c.y1 + 16, fmt(t), c.font, anchor="middle"))
    for s in series:
        for p in s.get("data", []):
            src = ' data-field="%s" data-row="%s"' % (q(p.get("field", s.get("label", ""))),
                                                       q(p.get("row", "")))
            body.append('<circle cx="%.3f" cy="%.3f" r="3.2" fill="%s" fill-opacity="0.8"%s/>'
                        % (sx(num(p.get("x"))), c.y(num(p.get("y"))), q(s.get("color", "#d62728")), src))
    lx = c.x1 - 10
    ly = c.y0 + 8
    for s in series:
        body.append('<circle cx="%d" cy="%d" r="3.2" fill="%s"/>' % (lx - 14, ly - 3, q(s.get("color", "#d62728"))))
        body.append(el_text(lx - 6, ly + 1, s.get("label", "series"), c.font - 1))
        lx += 12 + 6 * len(s.get("label", "series")) + 10
    return c.wrap("".join(body))


def quartiles(vals):
    v = sorted(vals)
    n = len(v)
    if n == 0:
        return 0, 0, 0, 0, 0

    def pct(q):
        pos = q * (n - 1)
        i = int(math.floor(pos))
        j = int(math.ceil(pos))
        return v[i] + (v[j] - v[i]) * (pos - i)
    return min(v), pct(0.25), pct(0.5), pct(0.75), max(v)


def render_box(spec):
    c = Chart(spec)
    boxes = spec.get("boxes", [])
    all_vals = [num(v) for b in boxes for v in b.get("values", [])]
    lo = spec.get("y_min", min(all_vals) if all_vals else 0)
    hi = spec.get("y_max", max(all_vals) if all_vals else 1)
    if hi - lo < 1e-12:
        hi = lo + 1
    pad = (hi - lo) * 0.06
    lo, hi = lo - pad, hi + pad
    yt, step = ticks(lo, hi)
    labels = [b.get("label", "g%d" % i) for i, b in enumerate(boxes)]
    body = [c.axes(yt, step, spec.get("y_label", ""), labels, spec.get("x_label", ""))]
    n = len(boxes) or 1
    bw = (c.x1 - c.x0) / n * 0.5
    for i, b in enumerate(boxes):
        cx = c.x0 + (i + 0.5) * (c.x1 - c.x0) / n
        vals = [num(v) for v in b.get("values", [])]
        if vals:
            wlo, q1, med, q3, whi = quartiles(vals)
        else:
            wlo, q1, med, q3, whi = num(b.get("whislo")), num(b.get("q1")), num(b.get("med")), \
                num(b.get("q3")), num(b.get("whishi"))
        body.append(el_rect(cx - bw / 2, c.y(q3), bw, max(0.1, c.y(q1) - c.y(q3)),
                            b.get("color", "#9ecae1"), stroke="#333333", sw=1))
        body.append(el_line(cx - bw / 4, c.y(med), cx + bw / 4, c.y(med), "#222222", 1.6))
        body.append(el_line(cx, c.y(wlo), cx, c.y(q1), "#333333", 1))
        body.append(el_line(cx, c.y(q3), cx, c.y(whi), "#333333", 1))
        body.append(el_line(cx - bw / 4, c.y(wlo), cx + bw / 4, c.y(wlo), "#333333", 1))
        body.append(el_line(cx - bw / 4, c.y(whi), cx + bw / 4, c.y(whi), "#333333", 1))
    return c.wrap("".join(body))


def render_heatmap(spec):
    c = Chart(spec)
    rows = spec.get("row_labels", [])
    cols = spec.get("col_labels", [])
    vals = spec.get("values", [])
    cmap = colormap(spec.get("cmap", "viridis"))
    flat = [num(v) for row in vals for v in row]
    vmin = spec.get("vmin", min(flat) if flat else 0)
    vmax = spec.get("vmax", max(flat) if flat else 1)
    if vmax - vmin < 1e-12:
        vmax = vmin + 1
    cellw = (c.x1 - c.x0) / max(1, len(cols))
    cellh = (c.y1 - c.y0) / max(1, len(rows))
    body = []
    for i, row in enumerate(vals):
        for j, v in enumerate(row):
            t = (num(v) - vmin) / (vmax - vmin)
            fill = rgb_hex(cmap(t))
            body.append(el_rect(c.x0 + j * cellw, c.y0 + i * cellh, cellw, cellh, fill))
            body.append(el_text(c.x0 + (j + 0.5) * cellw, c.y0 + (i + 0.5) * cellh, fmt(v),
                                c.font - 1, anchor="middle", baseline="middle",
                                fill="#000000" if 0.25 < t < 0.75 else "#ffffff"))
    for j, lab in enumerate(cols):
        body.append(el_text(c.x0 + (j + 0.5) * cellw, c.y0 - 4, lab, c.font, anchor="middle"))
    for i, lab in enumerate(rows):
        body.append(el_text(c.x0 - 4, c.y0 + (i + 0.5) * cellh, lab, c.font, anchor="end",
                            baseline="middle"))
    # colorbar
    bx0 = c.x1 + 8
    bh = c.y1 - c.y0
    for k in range(64):
        t = k / 63.0
        body.append(el_rect(bx0, c.y0 + (1 - t) * bh, 6, bh / 64, rgb_hex(cmap(t))))
    body.append(el_text(bx0 + 10, c.y0, fmt(vmin), c.font - 1))
    body.append(el_text(bx0 + 10, c.y1, fmt(vmax), c.font - 1))
    return c.wrap("".join(body))


def render_mechanism(spec):
    c = Chart(spec)
    body = []
    for n in spec.get("nodes", []):
        x, y = num(n.get("x")), num(n.get("y"))
        w, h = num(n.get("w"), 40), num(n.get("h"), 16)
        shape = n.get("shape", "rect")
        fill = n.get("fill", "#e8f0fe")
        src = ' data-field="%s"' % q(n.get("source_field", ""))
        if shape == "round":
            body.append('<rect x="%.3f" y="%.3f" width="%.3f" height="%.3f" rx="8" ry="8" '
                        'fill="%s" stroke="#333333" stroke-width="1"%s/>' % (x, y, w, h, q(fill), src))
        elif shape == "ellipse":
            body.append('<ellipse cx="%.3f" cy="%.3f" rx="%.3f" ry="%.3f" fill="%s" '
                        'stroke="#333333" stroke-width="1"%s/>' % (x + w / 2, y + h / 2, w / 2, h / 2, q(fill), src))
        else:
            body.append(el_rect(x, y, w, h, fill, stroke="#333333", sw=1, extra=src))
        body.append(el_text(x + w / 2, y + h / 2, n.get("label", ""), c.font, anchor="middle",
                            baseline="middle", weight="bold" if n.get("bold") else "normal"))
    for e in spec.get("edges", []):
        a = spec.get("nodes", [])[e.get("from")] if isinstance(e.get("from"), int) else None
        b = spec.get("nodes", [])[e.get("to")] if isinstance(e.get("to"), int) else None
        # 也支持按 id 查找
        nodes = {str(n.get("id")): n for n in spec.get("nodes", [])}
        if a is None:
            a = nodes.get(str(e.get("from")))
        if b is None:
            b = nodes.get(str(e.get("to")))
        if a is None or b is None:
            continue
        x1 = num(a.get("x")) + num(a.get("w"), 40) / 2
        y1 = num(a.get("y")) + num(a.get("h"), 16) / 2
        x2 = num(b.get("x")) + num(b.get("w"), 40) / 2
        y2 = num(b.get("y")) + num(b.get("h"), 16) / 2
        kind = e.get("kind", "plain")
        color = "#d62728" if kind == "inhibition" else "#2ca02c" if kind == "activation" else "#333333"
        dash = "4,3" if kind in ("inhibition", "dashed") else None
        arrow = "" if kind in ("plain", "inhibition", "dashed") else ' marker-end="url(#arr)"'
        body.append(el_line(x1, y1, x2, y2, color, 1.4, dash=dash, extra=arrow))
        if e.get("label"):
            body.append(el_text((x1 + x2) / 2, (y1 + y2) / 2 - 4, e.get("label", ""), c.font - 1,
                                anchor="middle", fill=color))
    return c.wrap("".join(body))


def render_multipanel(spec):
    panels = spec.get("panels", [])
    n = len(panels) or 1
    cols = spec.get("cols", n)
    rows = math.ceil(n / cols)
    w = num(spec.get("size_mm", [180, 130])[0], 180) * MM / cols
    h = num(spec.get("size_mm", [180, 130])[1], 130) * MM / rows
    body = []
    for i, p in enumerate(panels):
        r, cc = divmod(i, cols)
        sub = dict(p)
        sub["size_mm"] = [w / MM, h / MM]
        svg = build_svg(sub)
        # 提取内层 body（去掉外层 svg 包装）
        import re as _re
        inner = _re.search(r"(?s)<defs>.*?</defs>(.*)</svg>", svg)
        core = inner.group(1) if inner else ""
        body.append('<g transform="translate(%.3f %.3f)">%s</g>' % (cc * w, r * h, core))
    title = spec.get("title", "")
    tt = ('<text x="%.3f" y="%.3f" font-size="%.3f" font-weight="bold" text-anchor="middle" '
          'font-family="sans-serif" fill="#111111">%s</text>'
          % ((w * cols) / 2, 16, num(spec.get("font_size", 8)) + 2, esc(title))) if title else ""
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="%.3f" height="%.3f" '
            'viewBox="0 0 %.3f %.3f">\n%s\n%s%s\n</svg>\n'
            % (w * cols, h * rows, w * cols, h * rows, marker_defs(), tt, "".join(body)))


def build_svg(spec):
    ft = spec.get("figure_type", "bar")
    renderers = {"bar": render_bar, "line": render_line, "scatter": render_scatter,
                 "box": render_box, "heatmap": render_heatmap, "mechanism": render_mechanism,
                 "multipanel": render_multipanel}
    fn = renderers.get(ft, render_bar)
    return fn(spec)


# ---------------------------------------------------------------- export

def export_svg(spec, out_dir, name):
    os.makedirs(out_dir, exist_ok=True)
    svg = build_svg(spec)
    path = os.path.join(out_dir, name or ("%s.svg" % spec.get("figure_type", "figure")))
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg)
    return path, svg


def export_matplotlib(spec, out_dir, dpi=300):
    """尝试 matplotlib 导出真实 PNG/PDF；失败返回 None + 说明。"""
    report = {"png": None, "pdf": None, "note": []}
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa
        report["note"].append("matplotlib 不可用: %s" % e)
        return report
    try:
        ft = spec.get("figure_type", "bar")
        plt.close("all")
        if ft == "bar":
            fig, ax = plt.subplots(figsize=(num(spec.get("size_mm", [180, 130])[0]) / 25.4,
                                            num(spec.get("size_mm", [180, 130])[1]) / 25.4))
            cats = spec.get("categories", [])
            if not cats:
                cats = sorted({p.get("x") for s in spec.get("series", []) for p in s.get("data", [])},
                              key=lambda v: str(v))
            for s in spec.get("series", []):
                ys = [num(p.get("y")) for p in s.get("data", [])]
                err = [num(p.get("err"), 0) for p in s.get("data", [])]
                if len(ys) < len(cats):
                    ys += [0] * (len(cats) - len(ys))
                    err += [0] * (len(cats) - len(err))
                ax.bar([x for x in range(len(cats))], ys, yerr=err, capsize=3,
                       label=s.get("label", "series"), color=s.get("color"))
            ax.set_xticks(range(len(cats)))
            ax.set_xticklabels([str(x) for x in cats])
            ax.set_ylabel(spec.get("y_label", ""))
            ax.set_xlabel(spec.get("x_label", ""))
            ax.set_title(spec.get("title", ""))
            ax.legend()
        elif ft in ("line", "scatter"):
            fig, ax = plt.subplots(figsize=(num(spec.get("size_mm", [180, 130])[0]) / 25.4,
                                            num(spec.get("size_mm", [180, 130])[1]) / 25.4))
            for s in spec.get("series", []):
                xs = [num(p.get("x")) for p in s.get("data", [])]
                ys = [num(p.get("y")) for p in s.get("data", [])]
                if ft == "line":
                    ax.errorbar(xs, ys, yerr=[num(p.get("err"), 0) for p in s.get("data", [])],
                                label=s.get("label", "series"), color=s.get("color"), marker="o")
                else:
                    ax.scatter(xs, ys, label=s.get("label", "series"), color=s.get("color"), s=20)
            ax.set_ylabel(spec.get("y_label", ""))
            ax.set_xlabel(spec.get("x_label", ""))
            ax.set_title(spec.get("title", ""))
            ax.legend()
        elif ft == "box":
            fig, ax = plt.subplots(figsize=(num(spec.get("size_mm", [180, 130])[0]) / 25.4,
                                            num(spec.get("size_mm", [180, 130])[1]) / 25.4))
            data = [[num(v) for v in b.get("values", [])] for b in spec.get("boxes", [])]
            ax.boxplot(data)
            ax.set_xticklabels([str(b.get("label", "")) for b in spec.get("boxes", [])])
            ax.set_ylabel(spec.get("y_label", ""))
            ax.set_xlabel(spec.get("x_label", ""))
            ax.set_title(spec.get("title", ""))
        elif ft == "heatmap":
            fig, ax = plt.subplots(figsize=(num(spec.get("size_mm", [180, 130])[0]) / 25.4,
                                            num(spec.get("size_mm", [180, 130])[1]) / 25.4))
            im = ax.imshow([[num(v) for v in row] for row in spec.get("values", [])],
                           cmap=spec.get("cmap", "viridis"))
            ax.set_xticks(range(len(spec.get("col_labels", []))))
            ax.set_xticklabels([str(x) for x in spec.get("col_labels", [])])
            ax.set_yticks(range(len(spec.get("row_labels", []))))
            ax.set_yticklabels([str(x) for x in spec.get("row_labels", [])])
            fig.colorbar(im)
            ax.set_title(spec.get("title", ""))
        else:
            report["note"].append("matplotlib 不支持 figure_type=%s，SVG/PDF 由标准库路径输出" % ft)
            plt.close("all")
            return report
        fig.tight_layout()
        png = os.path.join(out_dir, "figure.png")
        pdf = os.path.join(out_dir, "figure.pdf")
        fig.savefig(png, dpi=int(dpi), facecolor=spec.get("background", "white"))
        fig.savefig(pdf, format="pdf", facecolor=spec.get("background", "white"))
        plt.close("all")
        report["png"] = "figure.png"
        report["pdf"] = "figure.pdf"
        report["note"].append("PNG dpi=%s, PDF 由 matplotlib pdf 后端（矢量文本、字体嵌入）" % dpi)
    except Exception as e:  # noqa
        report["note"].append("matplotlib 导出失败: %s" % e)
    return report


def pdf_esc(s):
    return str(s).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_minimal_pdf(spec, out_dir, name):
    """标准库最小矢量 PDF 回退：真实渲染基本图元与文本（Helvetica，无需嵌入）。"""
    ft = spec.get("figure_type", "bar")
    w = num(spec.get("size_mm", [180, 130])[0]) * 2.83465
    h = num(spec.get("size_mm", [180, 130])[1]) * 2.83465
    ops = []

    def rect(x, y, ww, hh, rgb):
        ops.append("%s rg\n" % rgb)
        ops.append("%.3f %.3f %.3f %.3f re f\n" % (x, h - y - hh, ww, hh))

    def line(x1, y1, x2, y2):
        ops.append("0.5 w\n0 0 0 RG\n%.3f %.3f m %.3f %.3f l S\n" % (x1, h - y1, x2, h - y2))

    def text(x, y, s, size=8):
        ops.append("BT /F1 %.3f Tf 0 0 0 rg %.3f %.3f Td (%s) Tj ET\n"
                   % (size, x, h - y, pdf_esc(s)))

    rect(0, 0, w, h, "1 1 1")
    if ft == "mechanism":
        for n in spec.get("nodes", []):
            x, y = num(n.get("x")) * 2.83465, num(n.get("y")) * 2.83465
            ww, hh = num(n.get("w"), 40) * 2.83465, num(n.get("h"), 16) * 2.83465
            rect(x, y, ww, hh, "0.91 0.94 1")
            text(x + ww / 2 - 3 * len(n.get("label", "")), y + hh / 2 + 2, n.get("label", ""), 7)
        nodes_map = {str(n.get("id")): n for n in spec.get("nodes", [])}
        for e in spec.get("edges", []):
            a = nodes_map.get(str(e.get("from")))
            b = nodes_map.get(str(e.get("to")))
            if a and b:
                line(num(a.get("x")) * 2.83465 + num(a.get("w"), 40) * 1.417,
                     num(a.get("y")) * 2.83465 + num(a.get("h"), 16) * 1.417,
                     num(b.get("x")) * 2.83465 + num(b.get("w"), 40) * 1.417,
                     num(b.get("y")) * 2.83465 + num(b.get("h"), 16) * 1.417)
    else:
        c = Chart(spec)
        lo, hi = y_range(spec, spec.get("series", []))
        yt, step = ticks(lo, hi)
        for t in yt:
            line(c.x0 * 2.83465, c.y(t) * 2.83465, c.x1 * 2.83465, c.y(t) * 2.83465)
        text(c.x0 * 2.83465, c.y0 * 2.83465 - 4, spec.get("x_label", ""), 7)
        text(6, (c.y0 + c.y1) * 1.417, spec.get("y_label", ""), 7)
        cats = spec.get("categories", [])
        for s in spec.get("series", []):
            for i, p in enumerate(s.get("data", [])):
                yv = num(p.get("y"))
                x = c.x0 + (i + 0.5) * (c.x1 - c.x0) / max(1, len(cats))
                yy = c.y(yv)
                base = c.y(0 if lo <= 0 else lo)
                hh = abs(base - yy)
                rect(x - 5, min(yy, base), 8, hh, "0.12 0.47 0.71")
    content = "".join(ops)
    header = "%PDF-1.4\n"
    objs = ["1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n",
            "2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n",
            "3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 %.3f %.3f]/Contents 4 0 R"
            "/Resources<</Font<</F1 5 0 R>>>>>>endobj\n" % (w, h),
            "4 0 obj<</Length %d>>stream\n%s\nendstream endobj\n" % (len(content), content),
            "5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"]
    offsets = []
    body = header
    for o in objs:
        offsets.append(len(body))
        body += o
    xref_pos = len(body)
    body += "xref\n0 %d\n" % (len(objs) + 1)
    body += "0000000000 65535 f \n"
    for off in offsets:
        body += "%010d 00000 n \n" % off
    body += "trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1, xref_pos)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name or "figure_stdlib.pdf")
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


def main():
    ap = argparse.ArgumentParser(description="scix-graphics 出图")
    ap.add_argument("--spec", required=True, help="figure spec JSON 路径")
    ap.add_argument("--out", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    ap.add_argument("--svg", default=None, help="SVG 文件名")
    ap.add_argument("--png", action="store_true")
    ap.add_argument("--pdf", action="store_true")
    ap.add_argument("--dpi", default=300, type=int)
    ap.add_argument("--report", default=None, help="导出报告 JSON 输出路径")
    args = ap.parse_args()
    with open(args.spec, "r", encoding="utf-8-sig") as f:
        spec = json.load(f)
    os.makedirs(args.out, exist_ok=True)
    svg_path, _ = export_svg(spec, args.out, args.svg)
    report = {"svg": os.path.basename(svg_path), "png": None, "pdf": None, "note": []}
    if args.png or args.pdf:
        mpl = export_matplotlib(spec, args.out, args.dpi)
        report["note"] += mpl["note"]
        if args.png and mpl["png"]:
            report["png"] = mpl["png"]
        if args.pdf and mpl["pdf"]:
            report["pdf"] = mpl["pdf"]
        # 回退：matplotlib 未产出 PDF 时用标准库最小矢量 PDF
        if args.pdf and not report["pdf"]:
            try:
                report["pdf"] = os.path.basename(write_minimal_pdf(spec, args.out, "figure_stdlib.pdf"))
                report["note"].append("PDF 使用标准库最小矢量回退（Helvetica，无位图伪造）")
            except Exception as e:  # noqa
                report["note"].append("PDF 回退失败: %s" % e)
        if args.png and not report["png"]:
            report["note"].append("无可用光栅化器，PNG 置 null（不伪造位图）；请用 office-renderer/browser-automation 渲染 SVG")
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
