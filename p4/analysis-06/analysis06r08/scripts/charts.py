#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""charts: minimal, editable SVG charts built with the standard library only.

SVG is chosen because it is text-based, editable, parseable by XML tooling,
and does not require matplotlib / numpy. Every chart returns valid standalone
SVG (XML 1.0) with a deterministic layout: no random jitter, fixed canvas,
fixed font sizes.
"""

import math

FONT = "DejaVu Sans, sans-serif"
COLOR = "#2b6cb0"
GRID = "#e2e8f0"
AXIS = "#4a5568"


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _header(w, h):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
            'viewBox="0 0 %d %d" font-family="%s" font-size="11">\n'
            % (w, h, w, h, FONT))


def _footer():
    return "</svg>\n"


def _fmt(v, nd=3):
    try:
        return ("%.*f" % (nd, float(v)))
    except (TypeError, ValueError):
        return str(v)


def histogram_svg(values, title, xlabel="value"):
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return None
    lo, hi = min(nums), max(nums)
    if lo == hi:
        lo, hi = lo - 0.5, hi + 0.5
    n_bins = max(5, min(20, int(math.sqrt(len(nums)))))
    width = (hi - lo) / n_bins
    counts = [0] * n_bins
    for v in nums:
        idx = min(n_bins - 1, int((v - lo) / width))
        counts[idx] += 1
    cmax = max(counts)
    W, H = 560, 260
    left, right, top, bottom = 54, 16, 22, 40
    plot_w = W - left - right
    plot_h = H - top - bottom
    parts = [_header(W, H)]
    parts.append('<rect x="0" y="0" width="%d" height="%d" fill="white"/>' % (W, H))
    parts.append('<text x="%d" y="%d" font-size="13" font-weight="bold">%s</text>'
                 % (left, top - 6, _esc(title)))
    # gridlines
    for i in range(5):
        y = top + plot_h - (plot_h * i / 4.0)
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" '
                     'stroke="%s" stroke-width="0.6"/>'
                     % (left, y, left + plot_w, y, GRID))
    bar_w = plot_w / n_bins
    for i, c in enumerate(counts):
        h = (plot_h * c / cmax) if cmax else 0
        x = left + i * bar_w
        y = top + plot_h - h
        parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                     'fill="%s" fill-opacity="0.85"/>'
                     % (x + 1, y, max(1.0, bar_w - 2), h, COLOR))
    # axes
    parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s"/>'
                 % (left, top, left, top + plot_h, AXIS))
    parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s"/>'
                 % (left, top + plot_h, left + plot_w, top + plot_h, AXIS))
    parts.append('<text x="%d" y="%d" text-anchor="middle">%s</text>'
                 % (left + plot_w / 2, H - 8, _esc(xlabel)))
    parts.append('<text x="%d" y="%d" text-anchor="end" transform="rotate(-90 %d %d)">count</text>'
                 % (left - 8, top + plot_h / 2, left - 8, top + plot_h / 2))
    parts.append(_footer())
    return "".join(parts)


def boxplot_svg(series, labels, title):
    """series: list of lists of numbers; labels: matching names."""
    series = [[float(v) for v in s if v is not None] for s in series]
    if not series or not any(series):
        return None
    n = len(series)
    W = 140 + n * 86
    H = 280
    left, right, top, bottom = 54, 16, 26, 40
    plot_w = W - left - right
    plot_h = H - top - bottom
    parts = [_header(W, H)]
    parts.append('<rect x="0" y="0" width="%d" height="%d" fill="white"/>' % (W, H))
    parts.append('<text x="%d" y="%d" font-size="13" font-weight="bold">%s</text>'
                 % (left, top - 8, _esc(title)))
    allv = [v for s in series for v in s]
    lo, hi = min(allv), max(allv)
    if lo == hi:
        lo, hi = lo - 0.5, hi + 0.5
    pad = (hi - lo) * 0.05
    lo, hi = lo - pad, hi + pad
    for i in range(5):
        y = top + plot_h - (plot_h * i / 4.0)
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" '
                     'stroke-width="0.6"/>' % (left, y, left + plot_w, y, GRID))
        val = lo + (hi - lo) * i / 4.0
        parts.append('<text x="%d" y="%.1f" text-anchor="end">%s</text>'
                     % (left - 6, y + 3, _fmt(val, 2)))
    def ypos(v):
        return top + plot_h - (plot_h * (v - lo) / (hi - lo))
    slot = plot_w / max(1, n)
    for i, s in enumerate(series):
        if not s:
            continue
        cx = left + slot * (i + 0.5)
        q1, med, q3 = _quartiles(sorted(s))
        whisk_lo = max(lo, q1 - 1.5 * (q3 - q1))
        whisk_hi = min(hi, q3 + 1.5 * (q3 - q1))
        bw = min(52.0, slot * 0.55)
        y_whisk_lo, y_whisk_hi = ypos(whisk_lo), ypos(whisk_hi)
        # whiskers
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s"/>'
                     % (cx, y_whisk_lo, cx, y_whisk_hi, AXIS))
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s"/>'
                     % (cx - bw / 2, y_whisk_lo, cx + bw / 2, y_whisk_lo, AXIS))
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s"/>'
                     % (cx - bw / 2, y_whisk_hi, cx + bw / 2, y_whisk_hi, AXIS))
        # box
        parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                     'fill="%s" fill-opacity="0.35" stroke="%s"/>'
                     % (cx - bw / 2, ypos(q3), bw, ypos(q1) - ypos(q3),
                        COLOR, AXIS))
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                     'stroke="%s" stroke-width="2"/>'
                     % (cx - bw / 2, ypos(med), cx + bw / 2, ypos(med), AXIS))
        # outliers
        for v in s:
            if v < whisk_lo or v > whisk_hi:
                parts.append('<circle cx="%.1f" cy="%.1f" r="2" fill="%s"/>'
                             % (cx, ypos(v), AXIS))
        lbl = _esc(labels[i]) if i < len(labels) else _esc("series_%d" % i)
        parts.append('<text x="%.1f" y="%d" text-anchor="middle">%s</text>'
                     % (cx, H - 10, lbl))
    parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s"/>'
                 % (left, top, left, top + plot_h, AXIS))
    parts.append(_footer())
    return "".join(parts)


def bar_svg(categories, values, title, ylabel="count"):
    cats = [str(c) for c in categories]
    vals = [float(v) for v in values]
    if not cats or not vals:
        return None
    vmax = max(vals)
    W, H = 560, 260
    left, right, top, bottom = 54, 12, 22, 52
    plot_w = W - left - right
    plot_h = H - top - bottom
    parts = [_header(W, H)]
    parts.append('<rect x="0" y="0" width="%d" height="%d" fill="white"/>' % (W, H))
    parts.append('<text x="%d" y="%d" font-size="13" font-weight="bold">%s</text>'
                 % (left, top - 6, _esc(title)))
    for i in range(5):
        y = top + plot_h - (plot_h * i / 4.0)
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" '
                     'stroke-width="0.6"/>' % (left, y, left + plot_w, y, GRID))
    slot = plot_w / max(1, len(cats))
    bw = slot * 0.7
    for i, (c, v) in enumerate(zip(cats, vals)):
        h = plot_h * v / vmax if vmax else 0
        x = left + slot * i + (slot - bw) / 2
        y = top + plot_h - h
        parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                     'fill="%s" fill-opacity="0.85"/>'
                     % (x, y, bw, h, COLOR))
        parts.append('<text x="%.1f" y="%d" text-anchor="middle" '
                     'transform="rotate(-30 %.1f %d)">%s</text>'
                     % (left + slot * (i + 0.5), H - 12,
                        left + slot * (i + 0.5), H - 12, _esc(c[:14])))
    parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s"/>'
                 % (left, top, left, top + plot_h, AXIS))
    parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s"/>'
                 % (left, top + plot_h, left + plot_w, top + plot_h, AXIS))
    parts.append(_footer())
    return "".join(parts)


def heatmap_svg(matrix, row_labels, col_labels, title):
    """matrix: list of rows; labels align to rows/cols. Square correlation maps."""
    rows = [list(r) for r in matrix]
    if not rows or not rows[0]:
        return None
    n = len(rows)
    labels = list(row_labels) or ["v%d" % i for i in range(n)]
    cell = 40
    left = 120
    top = 40
    W = left + n * cell + 20
    H = top + n * cell + 60
    parts = [_header(W, H)]
    parts.append('<rect x="0" y="0" width="%d" height="%d" fill="white"/>' % (W, H))
    parts.append('<text x="%d" y="%d" font-size="13" font-weight="bold">%s</text>'
                 % (left, top - 14, _esc(title)))
    for i in range(n):
        for j in range(n):
            v = rows[i][j]
            if v is None or (isinstance(v, float) and math.isnan(v)):
                fill = "#edf2f7"
                txt = ""
            else:
                fv = float(v)
                # blue-white-red diverging palette, clamped to [-1, 1].
                t = max(-1.0, min(1.0, fv))
                if t >= 0:
                    r = int(255 - 95 * t)
                    g = int(255 - 90 * t)
                    b = int(255 - 60 * t)
                else:
                    u = -t
                    r = int(255 - 60 * u)
                    g = int(255 - 90 * u)
                    b = int(255 - 95 * u)
                fill = "#%02x%02x%02x" % (r, g, b)
                txt = _fmt(fv, 2)
            x = left + j * cell
            y = top + i * cell
            parts.append('<rect x="%d" y="%d" width="%d" height="%d" '
                         'fill="%s" stroke="#cbd5e0" stroke-width="0.5"/>'
                         % (x, y, cell, cell, fill))
            if txt:
                parts.append('<text x="%d" y="%d" text-anchor="middle" '
                             'font-size="9">%s</text>'
                             % (x + cell / 2, y + cell / 2 + 3, txt))
    for i in range(n):
        parts.append('<text x="%d" y="%d" text-anchor="end">%s</text>'
                     % (left - 6, top + i * cell + cell / 2 + 3,
                        _esc(str(labels[i])[:18])))
        parts.append('<text x="%d" y="%d" text-anchor="start" '
                     'transform="rotate(45 %d %d)">%s</text>'
                     % (left + i * cell + cell / 2, top + n * cell + 14,
                        left + i * cell + cell / 2, top + n * cell + 14,
                        _esc(str(labels[i])[:18])))
    parts.append(_footer())
    return "".join(parts)


def _quartiles(sorted_vals):
    n = len(sorted_vals)
    if n == 0:
        return 0.0, 0.0, 0.0
    q1 = sorted_vals[int(0.25 * (n - 1))]
    q3 = sorted_vals[int(0.75 * (n - 1))]
    med = (sorted_vals[n // 2] if n % 2
           else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0)
    return q1, med, q3
