#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minimal editable SVG chart generation (standard library only).

Every figure is a self-contained, XML-parseable SVG string built from the
underlying data; no external font or binary dependency is required.
"""

import math


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return "%g" % v
    return str(v)


def _open(w, h):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
            'viewBox="0 0 %d %d" font-family="DejaVu Sans, sans-serif">\n'
            % (w, h, w, h))


def _clean(vals):
    out = []
    for v in vals:
        if v is None:
            continue
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            continue
        out.append(float(v))
    return out


def histogram(title, values, width=640, height=300, bins=20):
    parts = [_open(width, height)]
    parts.append('<rect x="0" y="0" width="%d" height="%d" fill="white"/>\n'
                 % (width, height))
    parts.append('<text x="16" y="22" font-size="14" fill="#222">%s</text>\n'
                 % _esc(title))
    vals = _clean(values)
    if len(vals) < 2:
        parts.append('<text x="16" y="50" font-size="12" fill="#888">'
                     'insufficient data (n=%d)</text>\n' % len(vals))
        parts.append("</svg>\n")
        return "".join(parts)
    lo, hi = min(vals), max(vals)
    if lo == hi:
        lo -= 0.5
        hi += 0.5
    w = (hi - lo) / bins
    counts = [0] * bins
    for v in vals:
        idx = int((v - lo) / w)
        if idx >= bins:
            idx = bins - 1
        counts[idx] += 1
    ml, mr, mt, mb = 52, 16, 40, 40
    pw = width - ml - mr
    ph = height - mt - mb
    mx = max(counts) or 1
    bw = pw / bins
    for i, c in enumerate(counts):
        bh = (c / mx) * ph
        x = ml + i * bw
        y = mt + ph - bh
        parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                     'fill="#4c72b0" stroke="white" stroke-width="0.5"/>\n'
                     % (x, y, bw, bh))
    parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#333" stroke-width="1"/>\n' % (ml, mt, ml, mt + ph))
    parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#333" stroke-width="1"/>\n' % (ml, mt + ph, ml + pw, mt + ph))
    for i in range(5):
        val = mx * i / 4
        y = mt + ph - (val / mx) * ph
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#ddd" stroke-width="0.5"/>\n' % (ml, y, ml + pw, y))
        parts.append('<text x="%d" y="%.1f" font-size="10" fill="#555" text-anchor="end">%s</text>\n' % (ml - 4, y + 3, _fmt(val)))
    parts.append('<text x="%d" y="%d" font-size="10" fill="#555" text-anchor="middle">%s</text>\n' % (ml, mt + ph + 14, _fmt(lo)))
    parts.append('<text x="%d" y="%d" font-size="10" fill="#555" text-anchor="middle">%s</text>\n' % (ml + pw, mt + ph + 14, _fmt(hi)))
    parts.append("</svg>\n")
    return "".join(parts)


def missingness(items, width=640):
    items = list(items)
    if not items:
        items = [{"label": "no columns", "missing_rate": 0.0}]
    row_h = 26
    ml, mt = 180, 44
    height = mt + len(items) * row_h + 24
    pw = width - ml - 30
    parts = [_open(width, height)]
    parts.append('<rect x="0" y="0" width="%d" height="%d" fill="white"/>\n'
                 % (width, height))
    parts.append('<text x="16" y="22" font-size="14" fill="#222">'
                 'Missing value rate by column</text>\n')
    for i, it in enumerate(items):
        y = mt + i * row_h
        rate = max(0.0, min(1.0, float(it.get("missing_rate", 0.0) or 0.0)))
        parts.append('<text x="%d" y="%d" font-size="10" fill="#333">%s</text>\n'
                     % (ml - 8, y + 12, _esc(it.get("label", ""))[:42]))
        parts.append('<rect x="%d" y="%d" width="%d" height="12" fill="#eee"/>\n'
                     % (ml, y, pw))
        parts.append('<rect x="%d" y="%d" width="%.1f" height="12" fill="#c44e52"/>\n'
                     % (ml, y, pw * rate))
        parts.append('<text x="%d" y="%d" font-size="10" fill="#555">%.1f%%</text>\n'
                     % (ml + pw + 8, y + 11, rate * 100))
    parts.append("</svg>\n")
    return "".join(parts)


def _corr_color(r):
    if r is None:
        return "#eeeeee"
    t = max(-1.0, min(1.0, r))
    if t >= 0:
        return "rgb(%d,%d,%d)" % (255, int(255 * (1 - t)), int(255 * (1 - t)))
    return "rgb(%d,%d,%d)" % (int(255 * (1 + t)), int(255 * (1 + t)), 255)


def corr_heatmap(matrix, labels, cell=44):
    labels = list(labels)
    n = len(labels)
    ml, mt = 10 + max(70, cell), 10
    width = ml + n * cell + 12
    height = mt + n * cell + 26
    parts = [_open(width, height)]
    parts.append('<rect x="0" y="0" width="%d" height="%d" fill="white"/>\n'
                 % (width, height))
    if n == 0:
        parts.append('<text x="16" y="22" font-size="12" fill="#888">'
                     'no numeric columns</text>\n</svg>\n')
        return "".join(parts)
    for i in range(n):
        for j in range(n):
            r = matrix[i][j]
            x = ml + j * cell
            y = mt + i * cell
            parts.append('<rect x="%d" y="%d" width="%d" height="%d" fill="%s" stroke="#fff" stroke-width="1"/>\n' % (x, y, cell, cell, _corr_color(r)))
            if r is not None and n <= 9:
                parts.append('<text x="%d" y="%d" font-size="9" fill="#222" text-anchor="middle">%.2f</text>\n' % (x + cell / 2, y + cell / 2 + 3, r))
    for i, lab in enumerate(labels):
        parts.append('<text x="%d" y="%d" font-size="9" fill="#333" text-anchor="end">%s</text>\n' % (ml - 4, mt + i * cell + cell / 2 + 3, _esc(lab)[:40]))
    parts.append("</svg>\n")
    return "".join(parts)


def build_corr_matrix(pairs, labels):
    n = len(labels)
    idx = {lab: i for i, lab in enumerate(labels)}
    mat = [[None] * n for _ in range(n)]
    for i in range(n):
        mat[i][i] = 1.0
    for p in pairs:
        if p["r"] is None:
            continue
        i, j = idx[p["a"]], idx[p["b"]]
        mat[i][j] = mat[j][i] = float(p["r"])
    return mat