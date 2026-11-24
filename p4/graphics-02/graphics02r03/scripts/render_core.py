#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic pure-stdlib chart renderer.

Renders a chart spec (JSON) into:
  - an editable, layered SVG (text kept as <text> nodes, data-* glyph attrs)
  - a real rendered PNG (zlib-only, 300 dpi pHYs chunk)
  - a real vector PDF (base-14 Helvetica, no dates/IDs -> deterministic)
  - a mapping.json (glyph id -> source field/row/col/value)

Supported kinds: bar, line, scatter, heatmap. Schematic/mechanism/flow figures
are authored directly as editable SVG by the agent (see SKILL.md section 7);
this module handles the data-driven charts only.

Determinism: no wall-clock, no absolute paths, no random IDs, fixed palette and
iteration order, fixed number formatting. Same spec + same seed -> byte-identical
outputs within one Python/zlib runtime.
"""

import html
import json
import math
import os
import struct
import zlib

DEFAULT_PALETTE = ["#4C72B0", "#DD8452", "#55A868", "#C44E52",
                   "#8172B3", "#937860", "#DA8BC3", "#8C8C8C"]


# --------------------------------------------------------------------------- #
# text & number formatting
# --------------------------------------------------------------------------- #
def fmt_num(v):
    if v is None:
        return ""
    f = float(v)
    if f != f:  # NaN
        return ""
    return "%.6g" % f


def fmt_coord(v):
    s = "%.3f" % v
    return s.rstrip("0").rstrip(".")


def esc(s):
    return html.escape(str(s), quote=True)


_TRANS = str.maketrans({
    "µ": "u", "μ": "u", "×": "x", "⁻": "^", "³": "3", "²": "2",
    "±": "+/-", "°": "deg", "≥": ">=", "≤": "<=", "Å": "A", "α": "alpha",
    "β": "beta", "γ": "gamma", "–": "-", "—": "-", "′": "'", "″": "\"",
})


def ascii_label(s):
    """Deterministic ASCII fallback for PNG/PDF text (SVG keeps original)."""
    if s is None:
        return ""
    out = []
    for ch in str(s):
        o = ord(ch)
        if 32 <= o < 127:
            out.append(ch)
        else:
            out.append(_TRANS.get(ch, "?"))
    return "".join(out).upper()


# --------------------------------------------------------------------------- #
# 5x7 bitmap font (digits + uppercase + needed punctuation), '#' = on
# --------------------------------------------------------------------------- #
FONT5X7 = {
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11111", "00010", "00100", "00010", "00001", "10001", "01110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    "D": ["11100", "10010", "10001", "10001", "10001", "10010", "11100"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01110", "10001", "10000", "10111", "10001", "10001", "01111"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["01110", "00100", "00100", "00100", "00100", "00100", "01110"],
    "J": ["00111", "00010", "00010", "00010", "00010", "10010", "01100"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "Q": ["01110", "10001", "10001", "10001", "10101", "10010", "01101"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "11011", "10001"],
    "X": ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    "Z": ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
    " ": ["00000"] * 7,
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    ",": ["00000", "00000", "00000", "00000", "00110", "00100", "01000"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    "+": ["00000", "00100", "00100", "11111", "00100", "00100", "00000"],
    "/": ["00001", "00010", "00100", "01000", "10000", "00000", "00000"],
    "(": ["00010", "00100", "01000", "01000", "01000", "00100", "00010"],
    ")": ["01000", "00100", "00010", "00010", "00010", "00100", "01000"],
    "%": ["11001", "11010", "00010", "00100", "01000", "01011", "10011"],
    "=": ["00000", "00000", "11111", "00000", "11111", "00000", "00000"],
    "<": ["00010", "00100", "01000", "10000", "01000", "00100", "00010"],
    ">": ["01000", "00100", "00010", "00001", "00010", "00100", "01000"],
    ":": ["00000", "01100", "01100", "00000", "01100", "01100", "00000"],
    ";": ["00000", "01100", "01100", "00000", "00110", "00100", "01000"],
    "^": ["00100", "01010", "10001", "00000", "00000", "00000", "00000"],
}


# --------------------------------------------------------------------------- #
# model
# --------------------------------------------------------------------------- #
def _hex_rgb(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _colormap(v, lo, hi):
    if hi == lo:
        t = 0.0
    else:
        t = (v - lo) / (hi - lo)
    t = max(0.0, min(1.0, t))
    return _lerp((247, 251, 255), (8, 48, 107), t)  # light -> dark blue


def nice_ticks(lo, hi, n=6):
    if lo > hi:
        lo, hi = hi, lo
    if hi - lo < 1e-12:
        return [lo]
    step0 = (hi - lo) / max(1, n)
    mag = 10 ** math.floor(math.log10(step0))
    norm = step0 / mag
    step = mag
    for m in (1.0, 2.0, 2.5, 5.0, 10.0):
        if norm <= m:
            step = m * mag
            break
    start = math.ceil(lo / step) * step
    ticks = []
    v = start
    guard = 0
    while v <= hi + step * 1e-6 and guard < 60:
        ticks.append(v)
        v += step
        guard += 1
    return ticks


def _validate(spec):
    if not isinstance(spec, dict):
        raise ValueError("spec must be an object")
    kind = spec.get("kind", "bar")
    if kind not in ("bar", "line", "scatter", "heatmap"):
        raise ValueError("unsupported kind: %r" % kind)
    if kind == "heatmap":
        matrix = spec.get("matrix")
        if not isinstance(matrix, list) or not matrix or not isinstance(matrix[0], list):
            raise ValueError("heatmap requires matrix (list of lists)")
        return kind
    series = spec.get("series")
    if not isinstance(series, list) or not series:
        raise ValueError("chart requires non-empty series list")
    return kind


def _model(spec, kind):
    W = int(spec.get("width", 800))
    H = int(spec.get("height", 600))
    title = str(spec.get("title", ""))
    x_label = str(spec.get("x_label", ""))
    y_label = str(spec.get("y_label", ""))
    notes = str(spec.get("figure_notes", ""))
    error_kind = str(spec.get("error_kind", "sd"))
    palette = spec.get("palette") or DEFAULT_PALETTE
    if kind == "heatmap":
        matrix = spec["matrix"]
        row_labels = spec.get("row_labels") or ["R%d" % (i + 1) for i in range(len(matrix))]
        col_labels = spec.get("col_labels") or ["C%d" % (j + 1) for j in range(len(matrix[0]))]
        flat = [v for row in matrix for v in row if isinstance(v, (int, float))]
        lo, hi = (min(flat), max(flat)) if flat else (0.0, 1.0)
        return {"kind": kind, "W": W, "H": H, "title": title, "x_label": x_label,
                "y_label": y_label, "notes": notes, "matrix": matrix,
                "row_labels": row_labels, "col_labels": col_labels,
                "vmin": lo, "vmax": hi, "ymin": lo, "ymax": hi,
                "palette": palette}
    series = spec["series"]
    categories = spec.get("categories")
    if categories is None:
        ncat = max(len(s.get("values", [])) for s in series)
        categories = [str(i + 1) for i in range(ncat)]
    categories = [str(c) for c in categories]
    allvals = []
    allerrs = []
    for s in series:
        vals = s.get("values", [])
        allvals.extend(v for v in vals if isinstance(v, (int, float)))
        errs = s.get("errors") or []
        if errs:
            if all(isinstance(e, (int, float)) for e in errs):
                allerrs.extend(e for e in errs if isinstance(e, (int, float)))
            elif all(isinstance(e, list) for e in errs):
                for pair in errs:
                    allerrs.extend(x for x in pair if isinstance(x, (int, float)))
    ymin = min(allvals) if allvals else 0.0
    ymax = max(allvals) if allvals else 1.0
    if kind == "bar":
        hi = max(0.0, ymax)
        lo = min(0.0, ymin)
        if hi == lo:
            hi = lo + 1.0
        pad = (hi - lo) * 0.12
        lo = (lo - pad) if lo < 0 else 0.0
        hi = hi + pad
    else:
        lo, hi = min(0.0, ymin), max(0.0, ymax)
        pad = (hi - lo) * 0.12 or 1.0
        lo, hi = lo - pad, hi + pad
    if hi <= lo:
        hi = lo + 1.0
    return {"kind": kind, "W": W, "H": H, "title": title, "x_label": x_label,
            "y_label": y_label, "notes": notes, "series": series,
            "categories": categories, "ymin": lo, "ymax": hi,
            "error_kind": error_kind, "palette": palette,
            "row_offset": spec.get("row_offset", 2),
            "col_offset": spec.get("col_offset", 2), "allerr": allerrs}


def _layout(m, kind):
    W, H = m["W"], m["H"]
    left, right, top, bottom = 90, 30, 60, 70
    x0, y0, x1, y1 = left, top, W - right, H - bottom
    if kind == "heatmap":
        ticks = []
    else:
        ticks = nice_ticks(m["ymin"], m["ymax"])
    return {"W": W, "H": H, "left": left, "right": right, "top": top,
            "bottom": bottom, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "pw": x1 - x0, "ph": y1 - y0, "ticks": ticks}


def _y_of(v, m, L):
    return L["y1"] - (v - m["ymin"]) / (m["ymax"] - m["ymin"]) * L["ph"]


def _x_of_cat(i, m, L, ncat):
    return L["x0"] + (i + 0.5) * (L["pw"] / ncat)


def _glyphs(m, L):
    """Compute data glyphs + source locators (deterministic order)."""
    kind = m["kind"]
    out = []
    mapping = []
    if kind == "heatmap":
        nrows, ncols = len(m["matrix"]), len(m["matrix"][0])
        cw, ch = L["pw"] / ncols, L["ph"] / nrows
        for i in range(nrows):
            for j in range(ncols):
                v = m["matrix"][i][j]
                gid = "cell_R%d_C%d" % (i, j)
                x = L["x0"] + j * cw
                y = L["y0"] + i * ch
                out.append({"id": gid, "type": "cell", "x": x, "y": y,
                            "w": cw, "h": ch, "value": v,
                            "color": _colormap(v, m["vmin"], m["vmax"]),
                            "series": m["row_labels"][i],
                            "category": m["col_labels"][j]})
                mapping.append({"id": gid, "series": m["row_labels"][i],
                                "category": m["col_labels"][j], "row": i,
                                "col": j, "value": v})
        return out, mapping
    ncat = len(m["categories"])
    nser = len(m["series"])
    cat_w = L["pw"] / ncat
    row_off = m.get("row_offset", 2)
    col_off = m.get("col_offset", 2)
    for s_idx, s in enumerate(m["series"]):
        name = str(s.get("name", "S%d" % (s_idx + 1)))
        vals = s.get("values", [])
        color = _hex_rgb(m["palette"][s_idx % len(m["palette"])])
        errs = s.get("errors") or []
        err_lo, err_hi = [], []
        for e in errs:
            if isinstance(e, (int, float)):
                err_lo.append(float(e))
                err_hi.append(float(e))
            elif isinstance(e, list) and len(e) == 2:
                err_lo.append(float(e[0]))
                err_hi.append(float(e[1]))
        src_rows = s.get("rows") or None
        src_cols = s.get("cols") or None
        for i in range(len(vals)):
            v = vals[i]
            if not isinstance(v, (int, float)):
                continue
            gid = "g_S%d_C%d" % (s_idx, i)
            if kind == "bar":
                group = cat_w * 0.8
                bw = group / nser
                x = L["x0"] + i * cat_w + cat_w * 0.1 + s_idx * bw
                w = bw * 0.9
                y_base = _y_of(0.0, m, L)
                yv = _y_of(v, m, L)
                top = min(yv, y_base)
                h = abs(y_base - yv)
                out.append({"id": gid, "type": "bar", "x": x, "y": top,
                            "w": w, "h": h, "value": v,
                            "color": color, "series": name,
                            "category": m["categories"][i], "series_i": s_idx,
                            "cat_i": i})
            elif kind in ("line", "scatter"):
                x = _x_of_cat(i, m, L, ncat)
                y = _y_of(v, m, L)
                out.append({"id": gid, "type": kind, "x": x, "y": y,
                            "value": v, "color": color, "series": name,
                            "category": m["categories"][i], "series_i": s_idx,
                            "cat_i": i})
            else:
                continue
            ehi = err_hi[i] if i < len(err_hi) else None
            elo = err_lo[i] if i < len(err_lo) else None
            out[-1]["err_hi"] = ehi
            out[-1]["err_lo"] = elo
            row = src_rows[i] if src_rows and i < len(src_rows) else (i + row_off)
            col = src_cols[i] if src_cols and i < len(src_cols) else (s_idx + col_off)
            mapping.append({"id": gid, "series": name, "category": m["categories"][i],
                            "series_index": s_idx, "category_index": i,
                            "row": row, "col": col, "value": v,
                            "error": ehi, "error_kind": m["error_kind"]})
    return out, mapping


# --------------------------------------------------------------------------- #
# SVG
# --------------------------------------------------------------------------- #
def _svg(m, L, glyphs):
    W, H = L["W"], L["H"]
    x0, y0, x1, y1 = L["x0"], L["y0"], L["x1"], L["y1"]
    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
                 'viewBox="0 0 %d %d" font-family="Arial, Helvetica, sans-serif" '
                 'font-size="12">' % (W, H, W, H))
    lines.append('<rect x="0" y="0" width="%d" height="%d" fill="#ffffff"/>' % (W, H))
    if m["title"]:
        lines.append('<text x="%d" y="%d" text-anchor="middle" font-size="15" '
                     'font-weight="bold" fill="#000000">%s</text>'
                     % (W / 2, 26, esc(m["title"])))
    # grid + ticks
    for t in L["ticks"]:
        y = _y_of(t, m, L)
        lines.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#e0e0e0"/>'
                     % (fmt_coord(x0), fmt_coord(y), fmt_coord(x1), fmt_coord(y)))
        lines.append('<text x="%s" y="%s" text-anchor="end" fill="#333333">%s</text>'
                     % (fmt_coord(x0 - 6), fmt_coord(y + 4), esc(fmt_num(t))))
    # axes
    lines.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#000000"/>'
                 % (fmt_coord(x0), fmt_coord(y0), fmt_coord(x0), fmt_coord(y1)))
    lines.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#000000"/>'
                 % (fmt_coord(x0), fmt_coord(y1), fmt_coord(x1), fmt_coord(y1)))
    # y label
    if m["y_label"]:
        lines.append('<text transform="rotate(-90)" x="%s" y="%s" '
                     'text-anchor="middle" fill="#000000">%s</text>'
                     % (fmt_coord(-(y0 + (y1 - y0) / 2)), fmt_coord(16),
                        esc(m["y_label"])))
    # category labels
    if m["kind"] == "heatmap":
        ncols = len(m["col_labels"])
        cw = L["pw"] / ncols
        for j, lab in enumerate(m["col_labels"]):
            lines.append('<text x="%s" y="%s" text-anchor="middle" fill="#333333">%s</text>'
                         % (fmt_coord(x0 + (j + 0.5) * cw), fmt_coord(y1 + 18),
                            esc(lab)))
        nrows = len(m["row_labels"])
        ch = L["ph"] / nrows
        for i, lab in enumerate(m["row_labels"]):
            lines.append('<text x="%s" y="%s" text-anchor="end" fill="#333333">%s</text>'
                         % (fmt_coord(x0 - 8), fmt_coord(y0 + (i + 0.5) * ch + 4),
                            esc(lab)))
    else:
        ncat = len(m["categories"])
        for i, lab in enumerate(m["categories"]):
            x = _x_of_cat(i, m, L, ncat)
            lines.append('<text x="%s" y="%s" text-anchor="middle" fill="#333333">%s</text>'
                         % (fmt_coord(x), fmt_coord(y1 + 18), esc(lab)))
    # glyphs
    for g in glyphs:
        c = "#%02x%02x%02x" % tuple(g["color"])
        if g["type"] == "bar":
            lines.append('<rect id="%s" data-series="%s" data-category="%s" '
                         'data-value="%s" x="%s" y="%s" width="%s" height="%s" '
                         'fill="%s" stroke="none"/>'
                         % (g["id"], esc(g["series"]), esc(g["category"]),
                            esc(fmt_num(g["value"])), fmt_coord(g["x"]),
                            fmt_coord(g["y"]), fmt_coord(g["w"]),
                            fmt_coord(g["h"]), c))
            if g.get("err_hi") is not None:
                ex = g["x"] + g["w"] / 2
                ey1 = _y_of(g["value"] + g["err_hi"], m, L)
                ey2 = _y_of(g["value"] - g["err_lo"], m, L)
                lines.append('<line id="%s_err" x1="%s" y1="%s" x2="%s" y2="%s" '
                             'stroke="#000000"/>'
                             % (g["id"], fmt_coord(ex), fmt_coord(ey1),
                                fmt_coord(ex), fmt_coord(ey2)))
                lines.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#000000"/>'
                             % (fmt_coord(ex - 4), fmt_coord(ey1),
                                fmt_coord(ex + 4), fmt_coord(ey1)))
                lines.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#000000"/>'
                             % (fmt_coord(ex - 4), fmt_coord(ey2),
                                fmt_coord(ex + 4), fmt_coord(ey2)))
        elif g["type"] == "cell":
            lines.append('<rect id="%s" data-series="%s" data-category="%s" '
                         'data-value="%s" x="%s" y="%s" width="%s" height="%s" '
                         'fill="%s" stroke="#ffffff"/>'
                         % (g["id"], esc(g["series"]), esc(g["category"]),
                            esc(fmt_num(g["value"])), fmt_coord(g["x"]),
                            fmt_coord(g["y"]), fmt_coord(g["w"]),
                            fmt_coord(g["h"]), c))
        elif g["type"] in ("line", "scatter"):
            lines.append('<circle id="%s" data-series="%s" data-category="%s" '
                         'data-value="%s" cx="%s" cy="%s" r="3" fill="%s"/>'
                         % (g["id"], esc(g["series"]), esc(g["category"]),
                            esc(fmt_num(g["value"])), fmt_coord(g["x"]),
                            fmt_coord(g["y"]), c))
    # line connectors
    if m["kind"] == "line":
        for s_idx, s in enumerate(m["series"]):
            c = "#%02x%02x%02x" % tuple(_hex_rgb(m["palette"][s_idx % len(m["palette"])]))
            pts = [g for g in glyphs if g["type"] == "line" and g["series_i"] == s_idx]
            if pts:
                d = "M" + " L".join("%s %s" % (fmt_coord(p["x"]), fmt_coord(p["y"])) for p in pts)
                lines.append('<path fill="none" stroke="%s" stroke-width="1.5" d="%s"/>' % (c, d))
    # legend
    if m["kind"] in ("bar", "line", "scatter"):
        lx = x1 - 8
        ly = y0 + 8
        for s_idx, s in enumerate(m["series"]):
            c = "#%02x%02x%02x" % tuple(_hex_rgb(m["palette"][s_idx % len(m["palette"])]))
            name = str(s.get("name", "S%d" % (s_idx + 1)))
            lines.append('<rect x="%s" y="%s" width="10" height="10" fill="%s"/>'
                         % (fmt_coord(lx - 90), fmt_coord(ly), c))
            lines.append('<text x="%s" y="%s" fill="#000000">%s</text>'
                         % (fmt_coord(lx - 76), fmt_coord(ly + 9), esc(name)))
            ly += 16
    if m["notes"]:
        lines.append('<text x="%s" y="%s" font-size="10" fill="#555555">%s</text>'
                     % (fmt_coord(x0), fmt_coord(H - 8), esc(m["notes"])))
    lines.append('</svg>')
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# PNG (pure zlib rasterizer)
# --------------------------------------------------------------------------- #
class _FB:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.buf = bytearray([255, 255, 255]) * (w * h)

    def set(self, x, y, rgb):
        x, y = int(round(x)), int(round(y))
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.buf[i], self.buf[i + 1], self.buf[i + 2] = rgb

    def fill_rect(self, x0, y0, x1, y1, rgb):
        for y in range(int(round(y0)), int(round(y1)) + 1):
            for x in range(int(round(x0)), int(round(x1)) + 1):
                self.set(x, y, rgb)

    def hline(self, x0, x1, y, rgb):
        for x in range(int(round(x0)), int(round(x1)) + 1):
            self.set(x, y, rgb)

    def vline(self, y0, y1, x, rgb):
        for y in range(int(round(y0)), int(round(y1)) + 1):
            self.set(x, y, rgb)

    def line(self, x0, y0, x1, y1, rgb):
        x0, y0, x1, y1 = int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        cx, cy = x0, y0
        while True:
            self.set(cx, cy, rgb)
            if cx == x1 and cy == y1:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                cx += sx
            if e2 < dx:
                err += dx
                cy += sy

    def text(self, x, y, s, rgb, scale=1):
        cx = int(round(x))
        for ch in str(s):
            glyph = FONT5X7.get(ch.upper() if ch.islower() else ch)
            if glyph is None:
                glyph = FONT5X7.get("?")
            if glyph is not None:
                for row in range(7):
                    for col in range(5):
                        if glyph[row][col] == "1":
                            for sy in range(scale):
                                for sx in range(scale):
                                    self.set(cx + col * scale + sx,
                                             y + row * scale + sy, rgb)
            cx += 6 * scale


def _png_bytes(m, L, glyphs):
    fb = _FB(L["W"], L["H"])
    x0, y0, x1, y1 = L["x0"], L["y0"], L["x1"], L["y1"]
    grid = (230, 230, 230)
    black = (0, 0, 0)
    for t in L["ticks"]:
        y = int(round(_y_of(t, m, L)))
        fb.hline(x0, x1, y, grid)
        lab = ascii_label(fmt_num(t))
        fb.text(x0 - 6 - 6 * len(lab), y - 3, lab, (51, 51, 51))
    fb.vline(y0, y1, x0, black)
    fb.hline(x0, x1, y1, black)
    if m["title"]:
        t = ascii_label(m["title"])
        fb.text(L["W"] / 2 - 3 * len(t), 10, t, black, scale=2)
    if m["x_label"]:
        t = ascii_label(m["x_label"])
        fb.text(x0 + L["pw"] / 2 - 3 * len(t), y1 + 40, t, black)
    if m["y_label"]:
        t = ascii_label(m["y_label"])
        fb.text(6, y0 + L["ph"] / 2 - 3, t, black)
    if m["kind"] == "heatmap":
        for g in glyphs:
            fb.fill_rect(g["x"], g["y"], g["x"] + g["w"] - 1, g["y"] + g["h"] - 1, g["color"])
    else:
        for g in glyphs:
            if g["type"] == "bar":
                fb.fill_rect(g["x"], g["y"], g["x"] + g["w"] - 1,
                             g["y"] + g["h"] - 1, g["color"])
                if g.get("err_hi") is not None:
                    ex = g["x"] + g["w"] / 2
                    ey1 = _y_of(g["value"] + g["err_hi"], m, L)
                    ey2 = _y_of(g["value"] - g["err_lo"], m, L)
                    fb.line(ex, ey1, ex, ey2, black)
                    fb.hline(ex - 4, ex + 4, ey1, black)
                    fb.hline(ex - 4, ex + 4, ey2, black)
            elif g["type"] in ("line", "scatter"):
                fb.fill_rect(g["x"] - 1, g["y"] - 1, g["x"] + 1, g["y"] + 1, g["color"])
        if m["kind"] == "line":
            for s_idx, s in enumerate(m["series"]):
                c = _hex_rgb(m["palette"][s_idx % len(m["palette"])])
                pts = [g for g in glyphs if g["type"] == "line" and g["series_i"] == s_idx]
                for a, b in zip(pts, pts[1:]):
                    fb.line(a["x"], a["y"], b["x"], b["y"], c)
    # category labels (heatmap or categorical)
    if m["kind"] == "heatmap":
        ncols = len(m["col_labels"])
        cw = L["pw"] / ncols
        for j, lab in enumerate(m["col_labels"]):
            t = ascii_label(lab)
            fb.text(x0 + (j + 0.5) * cw - 3 * len(t), y1 + 6, t, (51, 51, 51))
        nrows = len(m["row_labels"])
        ch = L["ph"] / nrows
        for i, lab in enumerate(m["row_labels"]):
            t = ascii_label(lab)
            fb.text(x0 - 8 - 6 * len(t), y0 + (i + 0.5) * ch - 3, t, (51, 51, 51))
    else:
        ncat = len(m["categories"])
        for i, lab in enumerate(m["categories"]):
            t = ascii_label(lab)
            x = _x_of_cat(i, m, L, ncat)
            fb.text(x - 3 * len(t), y1 + 6, t, (51, 51, 51))
    # legend
    if m["kind"] in ("bar", "line", "scatter"):
        lx = x1 - 98
        ly = y0 + 10
        for s_idx, s in enumerate(m["series"]):
            c = _hex_rgb(m["palette"][s_idx % len(m["palette"])])
            fb.fill_rect(lx, ly, lx + 10, ly + 10, c)
            fb.text(lx + 14, ly + 2, ascii_label(str(s.get("name", "S%d" % (s_idx + 1)))), black)
            ly += 16

    raw = b"".join(b"\x00" + bytes(fb.buf[y * L["W"] * 3:(y + 1) * L["W"] * 3])
                   for y in range(L["H"]))
    return _encode_png(L["W"], L["H"], raw)


def _encode_png(w, h, raw_rgb):
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    ppm = 11811  # ~300 dpi
    phys = struct.pack(">IIB", ppm, ppm, 1)
    idat = zlib.compress(raw_rgb, 9)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"pHYs", phys) +
            chunk(b"IDAT", idat) + chunk(b"IEND", b""))


# --------------------------------------------------------------------------- #
# PDF (vector, deterministic)
# --------------------------------------------------------------------------- #
def _pdf_escape(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf(m, L, glyphs):
    W, H = L["W"], L["H"]
    x0, y0, x1, y1 = L["x0"], L["y0"], L["x1"], L["y1"]
    ops = []
    ops.append("1 1 1 rg %d %d %d %d re f" % (0, 0, W, H))  # white bg
    ops.append("0.878 0.878 0.878 RG 1 w")

    def py(v):
        return H - v  # PDF y-up

    for t in L["ticks"]:
        y = _y_of(t, m, L)
        ops.append("%s %s m %s %s l S" % (fmt_coord(x0), fmt_coord(py(y)),
                                          fmt_coord(x1), fmt_coord(py(y))))
    ops.append("0 0 0 RG 1 w")
    ops.append("%s %s m %s %s l S" % (fmt_coord(x0), fmt_coord(py(y0)),
                                      fmt_coord(x0), fmt_coord(py(y1))))
    ops.append("%s %s m %s %s l S" % (fmt_coord(x0), fmt_coord(py(y1)),
                                      fmt_coord(x1), fmt_coord(py(y1))))
    # text helper
    def txt(x, y, s, size=9):
        ops.append("BT /F1 %s Tf %s %s Td (%s) Tj ET"
                   % (fmt_coord(size), fmt_coord(x), fmt_coord(y), _pdf_escape(s)))

    if m["title"]:
        t = ascii_label(m["title"])
        txt(W / 2 - 2.2 * len(t), py(24), t, 12)
    for t in L["ticks"]:
        y = _y_of(t, m, L)
        txt(x0 - 6 - 4 * len(fmt_num(t)), py(y) + 3, ascii_label(fmt_num(t)))
    if m["y_label"]:
        t = ascii_label(m["y_label"])
        txt(6, py(y0 + L["ph"] / 2), t)
    if m["x_label"]:
        t = ascii_label(m["x_label"])
        txt(x0 + L["pw"] / 2 - 2.2 * len(t), py(y1 + 40), t)
    # glyphs
    for g in glyphs:
        r, gg, b = g["color"]
        ops.append("%s %s %s rg" % (fmt_coord(r / 255.0), fmt_coord(gg / 255.0),
                                    fmt_coord(b / 255.0)))
        if g["type"] in ("bar", "cell"):
            ops.append("%s %s %s %s re f" % (fmt_coord(g["x"]), fmt_coord(py(g["y"] + g["h"])),
                                             fmt_coord(g["w"]), fmt_coord(g["h"])))
        elif g["type"] in ("line", "scatter"):
            ops.append("%s %s 1.5 0 360 arc f" % (fmt_coord(g["x"]), fmt_coord(py(g["y"]))))
    if m["kind"] == "line":
        for s_idx, s in enumerate(m["series"]):
            c = _hex_rgb(m["palette"][s_idx % len(m["palette"])])
            pts = [g for g in glyphs if g["type"] == "line" and g["series_i"] == s_idx]
            if pts:
                r, gg, b = c
                ops.append("%s %s %s RG 1.5 w" % (fmt_coord(r / 255.0), fmt_coord(gg / 255.0),
                                                  fmt_coord(b / 255.0)))
                ops.append(" ".join(["%s %s m" % (fmt_coord(pts[0]["x"]), fmt_coord(py(pts[0]["y"])))] +
                                   ["%s %s l" % (fmt_coord(p["x"]), fmt_coord(py(p["y"]))) for p in pts[1:]]) +
                           " S")
    # legend
    if m["kind"] in ("bar", "line", "scatter"):
        lx, ly = x1 - 80, py(y0 + 12)
        for s_idx, s in enumerate(m["series"]):
            c = _hex_rgb(m["palette"][s_idx % len(m["palette"])])
            r, gg, b = c
            ops.append("%s %s %s rg %s %s 6 6 re f"
                       % (fmt_coord(r / 255.0), fmt_coord(gg / 255.0), fmt_coord(b / 255.0),
                          fmt_coord(lx), fmt_coord(ly)))
            txt(lx + 9, ly + 5, ascii_label(str(s.get("name", "S%d" % (s_idx + 1)))))
            ly += 12
    stream = "\n".join(ops)
    return _assemble_pdf(W, H, stream)


def _assemble_pdf(w, h, stream):
    stream_b = stream.encode("latin-1", errors="replace")
    objs = []
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objs.append(("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] "
                 "/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
                 % (w, h)).encode("latin-1"))
    objs.append(b"<< /Length " + str(len(stream_b)).encode("ascii") +
                b" >>\nstream\n" + stream_b + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                b"/Encoding /WinAnsiEncoding >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += (b"%d 0 obj\n" % i) + o + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 %d\n" % (len(objs) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, xref_pos))
    return bytes(out)


# --------------------------------------------------------------------------- #
# orchestrator
# --------------------------------------------------------------------------- #
def render_spec(spec, out_dir, name, seed, work_dir):
    kind = _validate(spec)
    m = _model(spec, kind)
    L = _layout(m, kind)
    glyphs, mapping = _glyphs(m, L)
    svg = _svg(m, L, glyphs)
    png = _png_bytes(m, L, glyphs)
    pdf = _pdf(m, L, glyphs)

    base = safe_name(name)
    svg_path = os.path.join(out_dir, "figures", base + ".svg")
    png_path = os.path.join(out_dir, "figures", base + ".png")
    pdf_path = os.path.join(out_dir, "figures", base + ".pdf")
    map_path = os.path.join(out_dir, "figures", base + ".mapping.json")
    spec_path = os.path.join(out_dir, "figures", base + ".spec.json")
    for p in (svg_path, png_path, pdf_path, map_path, spec_path):
        os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(svg_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(svg)
    with open(png_path, "wb") as f:
        f.write(png)
    with open(pdf_path, "wb") as f:
        f.write(pdf)
    mapping_doc = {"name": base, "kind": kind, "seed": seed, "glyphs": mapping,
                   "source": spec.get("source", None)}
    write_json(map_path, mapping_doc)
    write_json(spec_path, spec)
    fingerprint = hashlib_sha256(svg.encode("utf-8") + png + pdf)
    report = {
        "name": base,
        "kind": kind,
        "artifacts": {
            "svg": rel_to(out_dir, svg_path), "png": rel_to(out_dir, png_path),
            "pdf": rel_to(out_dir, pdf_path),
            "mapping": rel_to(out_dir, map_path),
        },
        "contract_fields": {
            "editable_source": rel_to(out_dir, svg_path),
            "rendered_png": rel_to(out_dir, png_path),
            "rendered_pdf": rel_to(out_dir, pdf_path),
            "data_mapping": rel_to(out_dir, map_path),
        },
        "glyph_count": len(glyphs),
        "fingerprint": fingerprint,
    }
    write_json(os.path.join(work_dir, "render_report.json"), report)
    return report


def hashlib_sha256(data):
    import hashlib
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def safe_name(name, fallback="figure"):
    import re
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", name or "").strip("._")
    return slug or fallback


def rel_to(out_dir, path):
    try:
        return os.path.relpath(path, out_dir).replace(os.sep, "/")
    except ValueError:
        return os.path.basename(path)


def write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(obj, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":")) + "\n")
