#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""charts -- deterministic SVG chart generation (standard library only).

Every chart is emitted as editable, XML-parseable SVG. Layout depends only on
the input values, so repeated runs produce byte-identical files (no random
jitter, no timestamps, no external fonts). Values are truncated labels, never
rounded in the underlying data.
"""

import os


def write_chart(output_dir, rel_path, content, artifacts, chart_list, kind):
    """Write an SVG file under output_dir and record it in artifacts."""
    if content is None:
        return None
    path = os.path.join(output_dir, rel_path.replace("/", os.sep))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    chart_list.append({"file": rel_path, "kind": kind})
    artifacts[rel_path] = "chart"
    return rel_path


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))


def _fmt(v, nd=3):
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return ("%%.%df" % nd) % v
    return str(v)


def svg_bar(items, title, xlabel="", width=720, bar_h=26):
    """Horizontal bar chart. items: list of (label, numeric value)."""
    items = [(str(k), v) for k, v in items
             if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not items:
        return None
    items = sorted(items, key=lambda kv: (kv[1], kv[0]))
    vals = [v for _, v in items]
    lo, hi = min(vals), max(vals)
    if lo == hi:
        lo = lo - abs(lo) * 0.5 - 0.5
        hi = hi + abs(hi) * 0.5 + 0.5
    if lo > 0:
        lo = 0.0
    if hi < 0:
        hi = 0.0
    span = hi - lo or 1.0

    left = 210
    right = 70
    top = 56
    bottom = 34
    plot_w = width - left - right
    height = top + bottom + len(items) * bar_h
    zero_x = left + int(round((0.0 - lo) / span * plot_w))

    parts = []
    parts.append(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" font-family="sans-serif" font-size="12">\n'
        % (width, height, width, height))
    parts.append('<rect width="100%%" height="100%%" fill="white"/>\n')
    parts.append('<text x="%d" y="26" font-size="15" font-weight="bold">%s</text>\n'
                 % (left, _esc(title)))
    if xlabel:
        parts.append('<text x="%d" y="%d" font-size="11" fill="#555">%s</text>\n'
                     % (left, height - 8, _esc(xlabel)))

    for i, (label, v) in enumerate(items):
        y = top + i * bar_h
        x = left if v >= 0 else zero_x
        w = int(round((v - lo) / span * plot_w)) if v >= 0 else \
            int(round((0.0 - v) / span * plot_w))
        if w < 1:
            w = 1
        color = "#4c78a8" if v >= 0 else "#d0604c"
        parts.append(
            '<text x="%d" y="%d" font-size="12" text-anchor="end">%s</text>\n'
            % (left - 10, y + bar_h - 8, _esc(label[:28])))
        parts.append(
            '<rect x="%d" y="%d" width="%d" height="%d" fill="%s"/>\n'
            % (x, y + 3, w, bar_h - 6, color))
        parts.append(
            '<text x="%d" y="%d" font-size="11" fill="#222">%s</text>\n'
            % (x + w + 4 if v >= 0 else x - 4, y + bar_h - 8, _fmt(v)))
    parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#999"/>\n'
                 % (zero_x, top, zero_x, height - bottom))
    parts.append('</svg>\n')
    return "".join(parts)


def svg_missing(items, title, width=680, bar_h=24):
    """Horizontal bar chart of missing-value counts by column."""
    items = [(str(k), int(v)) for k, v in items]
    items = sorted(items, key=lambda kv: (-kv[1], kv[0]))
    total = sum(v for _, v in items) or 1
    maxv = max((v for _, v in items), default=0) or 1

    left = 250
    right = 70
    top = 56
    bottom = 30
    plot_w = width - left - right
    height = top + bottom + len(items) * bar_h

    parts = ['<?xml version="1.0" encoding="UTF-8"?>\n',
             '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'viewBox="0 0 %d %d" font-family="sans-serif" font-size="12">\n'
             % (width, height, width, height),
             '<rect width="100%%" height="100%%" fill="white"/>\n',
             '<text x="%d" y="26" font-size="15" font-weight="bold">%s</text>\n'
             % (left, _esc(title))]
    for i, (label, v) in enumerate(items):
        y = top + i * bar_h
        w = int(round(v / maxv * plot_w)) if maxv else 0
        parts.append(
            '<text x="%d" y="%d" text-anchor="end">%s</text>\n'
            % (left - 10, y + bar_h - 7, _esc(label[:32])))
        parts.append('<rect x="%d" y="%d" width="%d" height="%d" fill="#b06a4c"/>\n'
                     % (left, y + 3, max(w, 1), bar_h - 6))
        parts.append('<text x="%d" y="%d" fill="#222">%d</text>\n'
                     % (left + max(w, 1) + 5, y + bar_h - 7, v))
    parts.append('</svg>\n')
    return "".join(parts)


def svg_heatmap(labels, matrix, title, width=600, cell=46, label_w=150):
    """Correlation heatmap. matrix[i][j] is a float in roughly [-1, 1]."""
    n = len(labels)
    if n == 0:
        return None
    top = 60
    height = top + n * cell + 30
    grid_w = n * cell
    total_w = label_w + grid_w + 40

    parts = ['<?xml version="1.0" encoding="UTF-8"?>\n',
             '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'viewBox="0 0 %d %d" font-family="sans-serif" font-size="11">\n'
             % (total_w, height, total_w, height),
             '<rect width="100%%" height="100%%" fill="white"/>\n',
             '<text x="%d" y="30" font-size="15" font-weight="bold">%s</text>\n'
             % (label_w, _esc(title))]

    def cell_fill(v):
        if v is None:
            return "#eeeeee"
        v = max(-1.0, min(1.0, v))
        if v >= 0:
            return "rgb(%d,%d,%d)" % (int(255 - 205 * v), int(255 - 120 * v),
                                      255)
        return "rgb(255,%d,%d)" % (int(255 + 120 * v), int(255 + 205 * v))

    for i, lab in enumerate(labels):
        parts.append('<text x="%d" y="%d" text-anchor="end">%s</text>\n'
                     % (label_w - 8, top + i * cell + cell // 2 + 4,
                        _esc(lab[:20])))
        for j in range(n):
            v = matrix[i][j] if i < len(matrix) and j < len(matrix[i]) else None
            x = label_w + j * cell
            y = top + i * cell
            parts.append(
                '<rect x="%d" y="%d" width="%d" height="%d" fill="%s" '
                'stroke="#ccc"/>\n' % (x, y, cell, cell, cell_fill(v)))
            if v is not None:
                parts.append(
                    '<text x="%d" y="%d" text-anchor="middle" font-size="10">%s'
                    '</text>\n' % (x + cell // 2, y + cell // 2 + 4,
                                   _fmt(v, 2)))
    parts.append('</svg>\n')
    return "".join(parts)
