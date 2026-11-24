#!/usr/bin/env python3
"""Deterministic core for the sci-figure Skill.

Design (differentiator vs. a direct "extract -> plot" package):
  * a declarative, JSON-based intermediate representation (IR, schema figurespec/1)
    is the single source of truth: it snapshots the data, the glyph list, each
    glyph's source locator + verbatim quote, style, and export parameters;
  * rendering (SVG/PNG/PDF) is a pure function of the IR, so re-running the
    pipeline reproduces the same figure;
  * validation is layered and ordered *before* rendering where it is cheap to
    fix: schema -> source/provenance -> glyph-vs-value mapping -> rendered
    artifacts.  A reverse index (data_ref -> glyph id) enables bi-directional
    tracing.

Only the Python standard library is required.  matplotlib is imported lazily
and guarded: when absent, editable SVG is still produced and PNG/PDF are
reported as degraded instead of being fabricated.
"""
import csv
import datetime
import hashlib
import html
import io
import json
import math
import os
import re
import sys
import xml.etree.ElementTree as ET

VERSION = "1.0.0"
IR_SCHEMA = "figurespec/1"

PALETTE = [
    "#4C72B0", "#DD8452", "#55A868", "#C44E52", "#8172B3",
    "#937860", "#DA8BC3", "#8C8C8C", "#CCB974", "#64B5CD",
]

# ---- generic io -----------------------------------------------------------

def now_utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path, cap=2 * 1024 * 1024):
    with open(path, "rb") as f:
        data = f.read()
    if len(data) > cap:
        return data[:cap].decode("utf-8", errors="replace") + "\n[truncated]"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def read_json(path):
    """Tolerant JSON read: BOM/bad encoding/parse errors never raise."""
    try:
        raw = open(path, "rb").read()
    except OSError as exc:
        return None, str(exc)
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return json.loads(raw.decode(enc)), None
        except (UnicodeDecodeError, LookupError):
            continue
        except ValueError as exc:
            return None, "bad JSON: %s" % exc
    return None, "undecodable file"


def write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
    return path


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def esc(txt):
    return html.escape("" if txt is None else str(txt), quote=False)


def num(v):
    """Best-effort float; empty/None -> None."""
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if s == "" or s.lower() in ("na", "nan", "n/a", "-", "null", "none"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ---- material discovery ---------------------------------------------------

MATERIAL_EXTS = {
    ".csv", ".tsv", ".txt", ".md", ".json", ".xml", ".pdf", ".png", ".jpg",
    ".jpeg", ".svg", ".tif", ".tiff", ".docx", ".xlsx", ".pptx", ".gif",
}
TEXT_EXTS = {".csv", ".tsv", ".txt", ".md", ".json", ".xml"}


def discover_materials(input_dir):
    found = []
    if not os.path.isdir(input_dir):
        return found
    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            ext = os.path.splitext(name)[1].lower()
            if ext not in MATERIAL_EXTS:
                continue
            full = os.path.join(root, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            rel = os.path.relpath(full, input_dir)
            found.append({
                "name": name, "path": full, "rel": rel, "ext": ext,
                "size": size, "text_readable": ext in TEXT_EXTS,
            })
    return found


# ---- contract / bootstrap -------------------------------------------------

def get_case_id(case):
    for key in ("case_id", "id", "challenge_id", "caseId"):
        if case.get(key) not in (None, "", {}):
            return str(case[key])
    return None


def extract_output_contract(case):
    """Find the output_contract wherever it may live (per-case, not fixed)."""
    for key in ("output_contract", "outputContract", "contract", "outputs"):
        if isinstance(case.get(key), dict):
            return case[key]
    # nested: case.task.output_contract
    task = case.get("task")
    if isinstance(task, dict):
        for key in ("output_contract", "outputContract", "contract"):
            if isinstance(task.get(key), dict):
                return task[key]
    return None


def _artifact_keys():
    return ["editable_svg", "preview_png", "preview_pdf", "figure_spec",
            "evidence_map", "check_report", "render_report", "reverse_index"]


def bootstrap(case_path, input_dir, work_dir, output_dir):
    """S0: write a valid partial result.json immediately (never leave a broken dir)."""
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    case, err = read_json(case_path) if os.path.exists(case_path) else (None, "case.json missing")
    case_id = get_case_id(case) if case else None
    contract = extract_output_contract(case) if case else None
    materials = discover_materials(input_dir)

    payload = {
        "case_id": case_id,
        "status": "partial",
        "output_contract": {k: None for k in (contract or {}).keys()},
        "primary_artifacts": {k: None for k in _artifact_keys()},
        "sources": [
            {"kind": "case", "path": case_path},
            {"kind": "materials", "items": [
                {"rel": m["rel"], "size": m["size"], "sha256": sha256_file(m["path"])}
                for m in materials if m["size"] <= 64 * 1024 * 1024
            ]},
        ],
        "uncertainty": [],
        "pending_human": [],
        "errors": [] if not err else [err],
        "degradations": [],
        "evidence_map": [],
        "annotation_checks": {},
        "change_log": [{
            "step": "S0_bootstrap", "time_utc": now_utc(), "status": "ok",
            "note": "partial skeleton written; contract fields discovered=%d" % len((contract or {}).keys()),
        }],
        "runtime": {
            "version": VERSION, "ir_schema": IR_SCHEMA,
            "started_utc": now_utc(), "finished_utc": None,
            "renderer": None, "fonts": None,
        },
    }
    write_json(os.path.join(work_dir, "contract.json"),
               {"case_id": case_id, "contract": contract, "materials": materials})
    write_json(os.path.join(output_dir, "result.json"), payload)
    return {"case_id": case_id, "n_contract": len((contract or {}).keys()),
            "n_materials": len(materials)}


# ---- table parsing --------------------------------------------------------

def _sniff_delimiter(text):
    lines = [l for l in text.splitlines() if l.strip()][:10]
    counts = {",": 0, "\t": 0, ";": 0}
    for l in lines:
        for d in counts:
            counts[d] += l.count(d)
    best, bestn = ",", counts[","]
    for d, n in counts.items():
        if n > bestn:
            best, bestn = d, n
    if bestn == 0 and any("|" in l for l in lines):
        return "|"
    return best if bestn > 0 else None


def _split_row(line, delim):
    if delim == "|":
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
    else:
        parts = next(csv.reader([line], delimiter=delim))
    return parts


def parse_table(text_or_path, name="table", source=None):
    """Parse CSV/TSV/markdown/whitespace into {columns, rows, column_meta, issues}."""
    if os.path.exists(text_or_path):
        text = read_text(text_or_path)
        source = source or {"file": os.path.basename(text_or_path)}
    else:
        text = text_or_path
    lines = text.splitlines()
    issues = []
    body = []
    delim = _sniff_delimiter(text)
    header = None
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        if _looks_markdown_sep(s):
            continue
        if delim and delim in s and not (delim == "|" and s.startswith("|") is False and "|" in s and _looks_markdown_sep(s)):
            if header is None:
                header = _split_row(s, delim)
            else:
                body.append(_split_row(s, delim))
            continue
        # markdown separator row
        if re.fullmatch(r"\|?[\s:|-]+\|?", s) and "-" in s:
            continue
        # whitespace fallback
        parts = re.split(r"\s{2,}|\t", s)
        if len(parts) < 2:
            continue
        if header is None:
            header = parts
        else:
            body.append(parts)
    if header is None and body:
        header = ["c%d" % (j + 1) for j in range(len(body[0]))]
    if header is None:
        header = []
    ncol = max([len(header)] + [len(r) for r in body]) if (header or body) else 0
    header = (header + ["c%d" % (j + 1) for j in range(ncol)])[:ncol]
    rows = [(r + [""] * ncol)[:ncol] for r in body]
    column_meta = []
    for j, colname in enumerate(header):
        vals = [r[j] for r in rows]
        floats = [num(v) for v in vals if num(v) is not None]
        if floats and all(isinstance(v, float) or isinstance(v, int) for v in floats):
            dtype = "float" if any(isinstance(f, float) and not float(f).is_integer() for f in floats) else "int"
        elif floats and len(floats) >= max(1, len(vals) * 0.5):
            dtype = "float"
        else:
            dtype = "string"
        column_meta.append({"name": colname, "dtype": dtype, "unit": None, "role": None})
        if floats:
            lo, hi = min(floats), max(floats)
            if hi != 0 and (abs(hi - lo) / max(abs(hi), 1e-9)) > 1e6:
                issues.append({"column": colname, "kind": "magnitude", "note":
                               "value span %.3g..%.3g suggests a unit/magnitude check" % (lo, hi)})
    if not rows:
        issues.append({"column": None, "kind": "empty", "note": "no data rows parsed"})
    return {"name": name, "source": source or {}, "header": header, "rows": rows,
            "column_meta": column_meta, "issues": issues}


def _looks_markdown_sep(s):
    return bool(re.fullmatch(r"\|?[\s:|-]+\|?", s)) and "-" in s


def extract_all(work_dir, input_dir):
    """Parse all readable CSVs/TSVs/txt tables into /app/work/t_<name>.json."""
    os.makedirs(work_dir, exist_ok=True)
    out = []
    for m in discover_materials(input_dir):
        if m["ext"] not in (".csv", ".tsv", ".txt"):
            continue
        try:
            name = "t_" + re.sub(r"[^0-9A-Za-z_]+", "_", os.path.splitext(m["name"])[0]).strip("_") or "t"
            parsed = parse_table(m["path"], name=name,
                                 source={"file": m["rel"], "locator": "file:" + m["rel"],
                                         "sha256": sha256_file(m["path"])})
            write_json(os.path.join(work_dir, name + ".json"), parsed)
            out.append({"name": name, "file": m["rel"], "issues": parsed["issues"]})
        except Exception as exc:  # keep going, never crash on one bad file
            out.append({"name": m["name"], "file": m["rel"], "issues": [{"kind": "error", "note": str(exc)}]})
    return out


# ---- IR --------------------------------------------------------------------

def new_ir(kind, name):
    return {
        "schema": IR_SCHEMA, "name": name,
        "figure": {"id": name, "kind": kind, "title": "", "panel": {"rows": 1, "cols": 1}},
        "data": {"tables": [], "mapping": {}},
        "glyphs": [], "nodes": [], "edges": [], "matrix": None, "boxes": [],
        "axis": {}, "style": {"font_family": "sans-serif", "font_size": 11,
                              "palette": PALETTE, "background": "#ffffff"},
        "export": {"size": {"w": 800, "h": 600}, "png": {"dpi": 300, "colorspace": "RGB"},
                   "pdf": {"embed_fonts": True}},
        "evidence": [],
    }


def load_ir(work_dir):
    path = os.path.join(work_dir, "figure_spec.json")
    if os.path.exists(path):
        ir, err = read_json(path)
        if ir is None:
            return None, err
        return ir, None
    return None, "figure_spec.json missing"


def save_ir(work_dir, ir):
    return write_json(os.path.join(work_dir, "figure_spec.json"), ir)


def _guess_roles(table):
    """Heuristic column roles for a source_value_chart workorder."""
    roles = {}
    metas = table["column_meta"]
    for j, m in enumerate(metas):
        n = m["name"].lower()
        if any(k in n for k in ("group", "sample", "treatment", "condition", "label", "category", "x")):
            roles.setdefault("x", j)
        elif any(k in n for k in ("sem", "se", "sd", "error", "err", "ci")):
            roles.setdefault("error", j)
        elif any(k in n for k in ("series", "color", "legend", "factor", "fill")):
            roles.setdefault("series", j)
        elif m["dtype"] in ("float", "int") and "value" not in roles:
            roles["value"] = j
    if "value" not in roles:
        for j, m in enumerate(metas):
            if m["dtype"] in ("float", "int"):
                roles["value"] = j
                break
    return roles


def scaffold_ir(work_dir, name="figure", kind="bar", table_name=None):
    """Scaffold an IR from a parsed table, embedding rows and per-glyph refs."""
    ir = new_ir(kind, name)
    ir["figure"]["kind"] = kind
    tables = []
    if table_name:
        tpath = os.path.join(work_dir, table_name + ".json")
        if os.path.exists(tpath):
            t, _ = read_json(tpath)
            if t:
                tables.append(t)
    if not tables:
        # discover the first parsed table
        for f in sorted(os.listdir(work_dir)):
            if f.startswith("t_") and f.endswith(".json") and f != "contract.json":
                t, _ = read_json(os.path.join(work_dir, f))
                if t and t.get("rows"):
                    tables.append(t)
                    break
    ir["data"]["tables"] = tables
    for t in tables:
        roles = _guess_roles(t)
        ir["data"]["mapping"][t["name"]] = {
            "x_column": roles.get("x"), "value_column": roles.get("value"),
            "error_column": roles.get("error"), "series_column": roles.get("series"),
        }
        for i, row in enumerate(t["rows"], start=1):
            vj = roles.get("value")
            xj = roles.get("x")
            if vj is None:
                continue
            value = num(row[vj])
            xlab = row[xj] if xj is not None else ("r%d" % i)
            ej = roles.get("error")
            error = num(row[ej]) if ej is not None else None
            ir["glyphs"].append({
                "id": "%s-%d" % (kind, len(ir["glyphs"]) + 1),
                "kind": kind,
                "data_ref": "%s#r%dc%d" % (t["name"], i, vj + 1),
                "value": value, "quote": row[vj].strip(), "unit": None,
                "x_label": str(xlab).strip(), "series": None, "error": error,
                "error_kind": None, "color": PALETTE[(len(ir["glyphs"])) % len(PALETTE)],
            })
            if ej is not None:
                ir["glyphs"][-1]["data_ref_error"] = "%s#r%dc%d" % (t["name"], i, ej + 1)
    save_ir(work_dir, ir)
    return ir


# ---- pre-render validation -------------------------------------------------

def validate_ir(ir):
    """Layered checks 1-3 (schema / provenance / glyph-vs-value). Returns issues."""
    issues = []
    if not isinstance(ir, dict):
        return [{"severity": "fatal", "kind": "schema", "note": "IR is not an object"}]
    if ir.get("schema") != IR_SCHEMA:
        issues.append({"severity": "fatal", "kind": "schema", "note": "unknown IR schema"})
    kind = (ir.get("figure") or {}).get("kind")
    if kind not in ("bar", "line", "scatter", "box", "heatmap", "graph"):
        issues.append({"severity": "fatal", "kind": "schema", "note": "unsupported kind: %r" % kind})

    # build table cell lookup: name -> (row-1, col-1) -> string
    cells = {}
    for t in ir.get("data", {}).get("tables", []):
        table_cells = {}
        for i, row in enumerate(t.get("rows", [])):
            for j, cell in enumerate(row):
                table_cells[(i, j)] = cell
        cells[t.get("name")] = {"cells": table_cells, "nrows": len(t.get("rows", [])),
                                "ncols": len(t.get("header", []))}

    seen_refs = set()
    for g in ir.get("glyphs", []):
        ref = g.get("data_ref", "")
        if not ref:
            issues.append({"severity": "fatal", "kind": "provenance",
                           "note": "glyph %r has no data_ref" % g.get("id")})
            continue
        seen_refs.add(ref)
        m = re.match(r"^([^#]+)#r(\d+)c(\d+)$", ref)
        if not m:
            issues.append({"severity": "error", "kind": "provenance",
                           "note": "glyph %r bad data_ref %r" % (g.get("id"), ref)})
            continue
        tname, ri, ci = m.group(1), int(m.group(2)), int(m.group(3))
        info = cells.get(tname)
        if not info:
            issues.append({"severity": "error", "kind": "provenance",
                           "note": "glyph %r refs unknown table %r" % (g.get("id"), tname)})
            continue
        if ri < 1 or ri > info["nrows"] or ci < 1 or ci > info["ncols"]:
            issues.append({"severity": "error", "kind": "provenance",
                           "note": "glyph %r ref out of range" % g.get("id")})
            continue
        cell = info["cells"].get((ri - 1, ci - 1), "")
        if g.get("quote") not in (None, "") and str(g.get("quote")).strip() != str(cell).strip():
            issues.append({"severity": "error", "kind": "mapping",
                           "note": "glyph %r quote %r != cell %r" % (g.get("id"), g.get("quote"), cell)})
        cv = num(cell)
        gv = num(g.get("value"))
        if cv is not None and gv is not None and abs(cv - gv) > 1e-6:
            issues.append({"severity": "error", "kind": "mapping",
                           "note": "glyph %r value %r != cell value %r" % (g.get("id"), gv, cv)})
    return issues


def reverse_index(ir):
    idx = {}
    for g in ir.get("glyphs", []):
        idx.setdefault(g.get("data_ref"), []).append(g.get("id"))
    return idx


def build(work_dir, name="figure", kind=None, table_name=None):
    """Scaffold or load IR, then run pre-render validation."""
    ir, err = load_ir(work_dir)
    if ir is None:
        ir = scaffold_ir(work_dir, name=name, kind=kind or "bar", table_name=table_name)
        ir, _ = load_ir(work_dir)
    issues = validate_ir(ir)
    rindex = reverse_index(ir)
    report = {"ir": os.path.join("figure_spec.json"), "issues": issues,
              "reverse_index": rindex, "glyph_count": len(ir.get("glyphs", [])),
              "n_fatal": sum(1 for i in issues if i["severity"] == "fatal"),
              "n_error": sum(1 for i in issues if i["severity"] == "error")}
    write_json(os.path.join(work_dir, "ir_check.json"), report)
    return report


# ---- font discovery --------------------------------------------------------

def discover_cjk_fonts():
    """Best-effort CJK-capable font list; matplotlib is optional/guarded."""
    fonts = []
    try:
        import matplotlib.font_manager as fm
        for f in fm.fontManager.ttflist:
            low = f.name.lower()
            if any(k in low for k in ("cjk", "noto sans", "simhei", "simsun", "microsoft yahei",
                                      "wenquanyi", "source han", "pingfang", "hiragino")):
                fonts.append(f.name)
    except Exception:
        pass
    # deterministic fixed fallback candidates (names only, no file assumptions)
    fonts += ["Noto Sans CJK SC", "Microsoft YaHei", "SimHei", "SimSun",
              "WenQuanYi Micro Hei", "Source Han Sans SC", "sans-serif"]
    seen = []
    for f in fonts:
        if f not in seen:
            seen.append(f)
    return seen


# ---- SVG rendering (pure stdlib, always works) -----------------------------

def _svg_scale(ir, values, is_bar):
    ax = ir.get("axis") or {}
    ymin = num(ax.get("ymin"))
    ymax = num(ax.get("ymax"))
    if ymin is None or ymax is None:
        vals = [v for v in values if v is not None]
        if not vals:
            ymin, ymax = 0, 1
        else:
            lo, hi = min(vals), max(vals)
            if is_bar and lo > 0:
                lo = 0
            pad = (hi - lo) * 0.08 if hi != lo else max(abs(hi) * 0.1, 0.5)
            ymin, ymax = lo - pad, hi + pad
    return ymin, ymax


def _viridis(t):
    # coarse 16-stop viridis approximation for heatmaps
    stops = [(0.0, "#440154"), (0.13, "#482878"), (0.25, "#3e4989"),
             (0.37, "#31688e"), (0.5, "#26828e"), (0.62, "#1f9e89"),
             (0.75, "#35b779"), (0.87, "#6ece58"), (1.0, "#fde725")]
    t = max(0.0, min(1.0, t))
    for (t1, c1), (t2, c2) in zip(stops, stops[1:]):
        if t1 <= t <= t2:
            f = (t - t1) / (t2 - t1)
            r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
            r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
            r, g, b = r1 + (r2 - r1) * f, g1 + (g2 - g1) * f, b1 + (b2 - b1) * f
            return "#%02x%02x%02x" % (int(r), int(g), int(b))
    return stops[-1][1]


def _text(x, y, s, fs, anchor="middle", color="#111111", weight=None, font="sans-serif", rotate=None):
    extra = ""
    if rotate:
        extra += ' transform="rotate(%s %s %s)"' % (rotate, x, y)
    if weight:
        extra += ' font-weight="%s"' % weight
    return ('<text x="%s" y="%s" font-family="%s" font-size="%s" fill="%s" text-anchor="%s"%s>%s</text>'
            % (x, y, esc(font), fs, color, anchor, extra, esc(s)))


def _arrowhead(marker_id):
    if marker_id == "bar":
        # T-bar head for inhibition/repression edges
        return ('<marker id="bar" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto" '
                'markerUnits="strokeWidth"><path d="M8,0 L8,9" stroke="#333333" stroke-width="1.4"/></marker>')
    return ('<marker id="%s" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" '
            'markerUnits="strokeWidth"><path d="M0,0 L0,6 L9,3 z" fill="#333333"/></marker>' % marker_id)


def render_svg(ir, svg_path):
    kind = (ir.get("figure") or {}).get("kind", "bar")
    w = int(ir["export"]["size"].get("w", 800))
    h = int(ir["export"]["size"].get("h", 600))
    font = ir.get("style", {}).get("font_family", "sans-serif")
    fs = int(ir.get("style", {}).get("font_size", 11))
    bg = ir.get("style", {}).get("background", "#ffffff")
    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
                 'viewBox="0 0 %d %d" font-family="%s">' % (w, h, w, h, esc(font)))
    parts.append('<rect x="0" y="0" width="%d" height="%d" fill="%s"/>' % (w, h, bg))
    parts.append('<defs>%s%s</defs>' % (_arrowhead("arrow"), _arrowhead("bar")))
    if kind == "bar":
        parts.extend(_bar_svg(ir, w, h, font, fs))
    elif kind == "line":
        parts.extend(_line_scatter_svg(ir, w, h, font, fs, "line"))
    elif kind == "scatter":
        parts.extend(_line_scatter_svg(ir, w, h, font, fs, "scatter"))
    elif kind == "box":
        parts.extend(_box_svg(ir, w, h, font, fs))
    elif kind == "heatmap":
        parts.extend(_heatmap_svg(ir, w, h, font, fs))
    elif kind == "graph":
        parts.extend(_graph_svg(ir, w, h, font, fs))
    parts.append('</svg>')
    svg = "\n".join(parts)
    os.makedirs(os.path.dirname(os.path.abspath(svg_path)), exist_ok=True)
    with open(svg_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(svg)
    return {"ok": True, "path": svg_path, "renderer": "stdlib-svg", "bytes": len(svg.encode("utf-8"))}


def _plot_region(w, h, ml=76, mr=24, mt=60, mb=72):
    return ml, mt, w - mr, h - mb


def _y_ticks(ymin, ymax, target=5):
    span = ymax - ymin
    if span <= 0:
        return [ymin]
    raw = span / max(1, target)
    mag = 10 ** math.floor(math.log10(raw))
    for mult in (1, 2, 2.5, 5, 10):
        step = mult * mag
        if raw / step <= target:
            break
    ticks = []
    v = math.ceil(ymin / step) * step
    while v <= ymax + 1e-9:
        ticks.append(v)
        v += step
    return ticks


def _fmt_tick(v):
    if abs(v) >= 1000 or (0 < abs(v) < 0.001):
        return "%.1e" % v
    return ("%g" % v)


def _bar_svg(ir, w, h, font, fs):
    x0, y0, x1, y1 = _plot_region(w, h)
    groups = []
    for g in ir.get("glyphs", []):
        if g.get("x_label") not in groups:
            groups.append(g.get("x_label"))
    series = []
    for g in ir.get("glyphs", []):
        if g.get("series") not in series:
            series.append(g.get("series"))
    values = [g.get("value") for g in ir.get("glyphs", [])]
    ymin, ymax = _svg_scale(ir, values, is_bar=True)
    yticks = _y_ticks(ymin, ymax)
    parts = ['<g id="axes" data-role="axes">']
    # axes
    parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#111111" stroke-width="1"/>' % (x0, y1, x0, y0))
    parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#111111" stroke-width="1"/>' % (x0, y1, x1, y1))
    for t in yticks:
        ty = y1 - (t - ymin) / (ymax - ymin) * (y1 - y0)
        parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#dddddd" stroke-width="1"/>' % (x0, ty, x1, ty))
        parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#111111" stroke-width="1"/>' % (x0 - 4, ty, x0, ty))
        parts.append(_text(x0 - 8, ty + fs * 0.33, _fmt_tick(t), fs, "end"))
    ylabel = ir.get("axis", {}).get("ylabel")
    if ylabel:
        parts.append('<text x="%s" y="%s" font-size="%s" text-anchor="middle" transform="rotate(-90 %s %s)">%s</text>'
                     % (16, (y0 + y1) / 2, fs, 16, (y0 + y1) / 2, esc(ylabel)))
    parts.append('</g>')
    # bars grouped
    nser = max(1, len([s for s in series if s is not None]) or 1)
    ng = max(1, len(groups))
    slot = (x1 - x0) / ng
    bw = slot * 0.6 / nser
    parts.append('<g id="glyphs" data-role="glyphs">')
    ser_index = {s: i for i, s in enumerate(series)}
    for g in ir.get("glyphs", []):
        gi = groups.index(g.get("x_label"))
        si = ser_index.get(g.get("series"), 0)
        val = g.get("value")
        if val is None:
            continue
        cx = x0 + gi * slot + slot / 2 + (si - (nser - 1) / 2) * bw
        yv = y1 - (val - ymin) / (ymax - ymin) * (y1 - y0)
        color = g.get("color") or PALETTE[si % len(PALETTE)]
        parts.append('<rect class="glyph" data-id="%s" data-ref="%s" data-value="%s" x="%s" y="%s" '
                     'width="%s" height="%s" fill="%s" stroke="#333333" stroke-width="0.8"/>'
                     % (esc(g.get("id")), esc(g.get("data_ref", "")), esc(str(val)), round(cx - bw / 2, 2),
                        round(min(yv, y1), 2), round(bw, 2), round(abs(yv - y1), 2), color))
        if g.get("error") is not None:
            ey = (g.get("error")) / (ymax - ymin) * (y1 - y0)
            parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#111111" stroke-width="1.2"/>'
                         % (cx, yv - ey, cx, yv + ey))
            parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#111111" stroke-width="1.2"/>'
                         % (cx - bw * 0.25, yv - ey, cx + bw * 0.25, yv - ey))
            parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#111111" stroke-width="1.2"/>'
                         % (cx - bw * 0.25, yv + ey, cx + bw * 0.25, yv + ey))
    parts.append('</g>')
    # x labels
    parts.append('<g id="xlabels" data-role="labels">')
    for gi, lab in enumerate(groups):
        cx = x0 + gi * slot + slot / 2
        parts.append(_text(cx, y1 + fs + 8, lab, fs, "middle", rotate=(90 if len(str(lab)) > 8 else None)))
    parts.append('</g>')
    xlabel = ir.get("axis", {}).get("xlabel")
    if xlabel:
        parts.append(_text((x0 + x1) / 2, h - 10, xlabel, fs, "middle"))
    title = (ir.get("figure") or {}).get("title")
    if title:
        parts.append(_text(w / 2, 28, title, fs + 2, "middle", weight="bold"))
    return parts


def _line_scatter_svg(ir, w, h, font, fs, mode):
    x0, y0, x1, y1 = _plot_region(w, h)
    glyphs = [g for g in ir.get("glyphs", []) if num(g.get("x")) is not None and num(g.get("y")) is not None]
    ys = [num(g.get("y")) for g in glyphs]
    xs = [num(g.get("x")) for g in glyphs]
    ymin, ymax = _svg_scale(ir, ys, is_bar=False)
    xmin, xmax = (ir.get("axis") or {}).get("xmin"), (ir.get("axis") or {}).get("xmax")
    if xmin is None:
        xmin = min(xs) if xs else 0
    if xmax is None:
        xmax = max(xs) if xs else 1
    parts = ['<g id="axes" data-role="axes">']
    parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#111111"/>' % (x0, y1, x0, y0))
    parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#111111"/>' % (x0, y1, x1, y1))
    for t in _y_ticks(ymin, ymax):
        ty = y1 - (t - ymin) / (ymax - ymin) * (y1 - y0)
        parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#dddddd"/>' % (x0, ty, x1, ty))
        parts.append(_text(x0 - 8, ty + fs * 0.33, _fmt_tick(t), fs, "end"))
    for t in _y_ticks(xmin, xmax):
        tx = x0 + (t - xmin) / (xmax - xmin) * (x1 - x0)
        parts.append(_text(tx, y1 + fs + 6, _fmt_tick(t), fs, "middle"))
    parts.append('</g>')
    by_series = {}
    for g in glyphs:
        by_series.setdefault(g.get("series") or "series0", []).append(g)
    parts.append('<g id="glyphs" data-role="glyphs">')
    for si, (sname, gs) in enumerate(sorted(by_series.items())):
        color = (gs[0].get("color") if gs else None) or PALETTE[si % len(PALETTE)]
        pts = []
        for g in sorted(gs, key=lambda z: num(z.get("x"))):
            tx = x0 + (num(g.get("x")) - xmin) / (xmax - xmin) * (x1 - x0)
            ty = y1 - (num(g.get("y")) - ymin) / (ymax - ymin) * (y1 - y0)
            pts.append((tx, ty))
            if mode == "scatter":
                parts.append('<circle class="glyph" data-id="%s" data-ref="%s" cx="%s" cy="%s" r="3.5" fill="%s" stroke="#333333"/>'
                             % (esc(g.get("id")), esc(g.get("data_ref", "")), tx, ty, color))
        if mode == "line" and len(pts) > 1:
            d = "M " + " L ".join("%s %s" % (px, py) for px, py in pts)
            parts.append('<path d="%s" fill="none" stroke="%s" stroke-width="2"/>' % (d, color))
            for (tx, ty), g in zip(pts, sorted(gs, key=lambda z: num(z.get("x")))):
                parts.append('<circle class="glyph" data-id="%s" data-ref="%s" cx="%s" cy="%s" r="3" fill="%s"/>'
                             % (esc(g.get("id")), esc(g.get("data_ref", "")), tx, ty, color))
    parts.append('</g>')
    if ir.get("axis", {}).get("xlabel"):
        parts.append(_text((x0 + x1) / 2, h - 10, ir["axis"]["xlabel"], fs, "middle"))
    if ir.get("axis", {}).get("ylabel"):
        parts.append('<text x="16" y="%s" font-size="%s" text-anchor="middle" transform="rotate(-90 16 %s)">%s</text>'
                     % ((y0 + y1) / 2, fs, (y0 + y1) / 2, esc(ir["axis"]["ylabel"])))
    return parts


def _box_svg(ir, w, h, font, fs):
    x0, y0, x1, y1 = _plot_region(w, h)
    boxes = ir.get("boxes", [])
    vals = []
    for b in boxes:
        vals += [num(b[k]) for k in ("min", "q1", "median", "q3", "max")]
    ymin, ymax = _svg_scale(ir, [v for v in vals if v is not None], is_bar=False)
    parts = ['<g id="axes" data-role="axes">']
    parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#111111"/>' % (x0, y1, x0, y0))
    parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#111111"/>' % (x0, y1, x1, y1))
    for t in _y_ticks(ymin, ymax):
        ty = y1 - (t - ymin) / (ymax - ymin) * (y1 - y0)
        parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#dddddd"/>' % (x0, ty, x1, ty))
        parts.append(_text(x0 - 8, ty + fs * 0.33, _fmt_tick(t), fs, "end"))
    parts.append('</g>')
    parts.append('<g id="glyphs" data-role="glyphs">')
    n = max(1, len(boxes))
    slot = (x1 - x0) / n
    for i, b in enumerate(boxes):
        cx = x0 + i * slot + slot / 2
        bw = slot * 0.4
        color = b.get("color") or PALETTE[i % len(PALETTE)]
        y_of = lambda v: y1 - (num(v) - ymin) / (ymax - ymin) * (y1 - y0)
        yq1, ymed, yq3, ylo, yhi = (y_of(b["q1"]), y_of(b["median"]), y_of(b["q3"]),
                                    y_of(b["min"]), y_of(b["max"]))
        parts.append('<rect class="glyph" data-id="%s" data-ref="%s" x="%s" y="%s" width="%s" height="%s" '
                     'fill="%s" fill-opacity="0.5" stroke="#111111"/>'
                     % (esc(b["id"]), esc(b.get("data_ref", "")), cx - bw / 2, yq3, bw, yq1 - yq3, color))
        parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#111111"/>' % (cx - bw / 2, ymed, cx + bw / 2, ymed))
        parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#111111"/>' % (cx, ylo, cx, yq1))
        parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#111111"/>' % (cx, yq3, cx, yhi))
        parts.append(_text(cx, y1 + fs + 8, b.get("label", b["id"]), fs, "middle", rotate=(90 if len(str(b.get("label", ""))) > 8 else None)))
    parts.append('</g>')
    return parts


def _heatmap_svg(ir, w, h, font, fs):
    m = ir.get("matrix") or {}
    row_labels = m.get("row_labels", [])
    col_labels = m.get("col_labels", [])
    values = m.get("values", [])
    if not row_labels and values:
        row_labels = ["r%d" % (i + 1) for i in range(len(values))]
    if not col_labels and values and values[0]:
        col_labels = ["c%d" % (j + 1) for j in range(len(values[0]))]
    nr = len(row_labels)
    nc = len(col_labels)
    ml, mt, mr, mb = 100, 60, 24, 90
    cw = (w - ml - mr) / max(1, nc)
    ch = (h - mt - mb) / max(1, nr)
    flat = [v for row in values for v in row]
    vmin, vmax = (min(flat), max(flat)) if flat else (0, 1)
    span = (vmax - vmin) or 1
    parts = ['<g id="glyphs" data-role="glyphs">']
    for i in range(nr):
        for j in range(nc):
            v = values[i][j] if i < len(values) and j < len(values[i]) else None
            if v is None:
                continue
            t = (v - vmin) / span
            color = _viridis(t)
            x = ml + j * cw
            y = mt + i * ch
            parts.append('<rect class="glyph" data-id="hm-r%dc%d" data-ref="%s" data-value="%s" x="%s" y="%s" '
                         'width="%s" height="%s" fill="%s" stroke="#ffffff" stroke-width="1"/>'
                         % (i + 1, j + 1, esc(m.get("data_ref", "heatmap")), esc(str(v)), round(x, 2), round(y, 2),
                            round(cw, 2), round(ch, 2), color))
            parts.append(_text(x + cw / 2, y + ch / 2 + fs * 0.3, "%g" % v, fs - 2, "middle", color="#000000"))
    parts.append('</g>')
    for j, lab in enumerate(col_labels):
        parts.append(_text(ml + j * cw + cw / 2, mt - 8, lab, fs, "middle", rotate=(90 if len(str(lab)) > 6 else None)))
    for i, lab in enumerate(row_labels):
        parts.append(_text(ml - 8, mt + i * ch + ch / 2 + fs * 0.3, lab, fs, "end"))
    return parts


def _graph_svg(ir, w, h, font, fs):
    parts = ['<g id="edges" data-role="edges">']
    for e in ir.get("edges", []):
        frm = _find_node(ir, e.get("from"))
        to = _find_node(ir, e.get("to"))
        if not frm or not to:
            continue
        x1, y1 = frm.get("x", 0), frm.get("y", 0)
        x2, y2 = to.get("x", 0), to.get("y", 0)
        color = e.get("color", "#333333")
        kind = e.get("kind", "activation")
        if kind in ("inhibition", "suppress", "repress"):
            parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="1.6" '
                         'marker-end="url(#bar)"/>' % (x1, y1, x2, y2, color))
        else:
            parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="1.6" '
                         'marker-end="url(#arrow)"/>' % (x1, y1, x2, y2, color))
        if e.get("label"):
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2 - 6
            parts.append(_text(mx, my, e["label"], fs - 2, "middle", color="#555555"))
    parts.append('</g>')
    parts.append('<g id="nodes" data-role="nodes">')
    for n in ir.get("nodes", []):
        x, y = n.get("x", 0), n.get("y", 0)
        color = n.get("color", "#E8EEF5")
        shape = n.get("shape", "rect")
        label = n.get("label", n.get("id", ""))
        if shape == "ellipse":
            parts.append('<ellipse class="glyph" data-id="%s" data-ref="%s" cx="%s" cy="%s" rx="%s" ry="%s" '
                         'fill="%s" stroke="#111111"/>' % (esc(n.get("id", "")), esc(n.get("data_ref", "")),
                                                           x, y, n.get("rx", 50), n.get("ry", 26), color))
            parts.append(_text(x, y + fs * 0.33, label, fs, "middle"))
        else:
            tw = max(60, len(str(label)) * fs * 0.62)
            parts.append('<rect class="glyph" data-id="%s" data-ref="%s" x="%s" y="%s" width="%s" height="%s" '
                         'rx="6" fill="%s" stroke="#111111"/>'
                         % (esc(n.get("id", "")), esc(n.get("data_ref", "")), x - tw / 2, y - 18, tw, 36, color))
            parts.append(_text(x, y + fs * 0.33, label, fs, "middle"))
    parts.append('</g>')
    return parts


def _find_node(ir, node_id):
    for n in ir.get("nodes", []):
        if n.get("id") == node_id:
            return n
    return None


# ---- raster/vector rendering via optional matplotlib -----------------------

def render_png_pdf(ir, work_dir, png_path, pdf_path):
    """Guarded matplotlib render. Returns a status dict; never fabricates files."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:
        return {"ok": False, "renderer": "none", "note": "matplotlib unavailable: %s" % exc}
    try:
        kind = (ir.get("figure") or {}).get("kind", "bar")
        fig = _plot_matplotlib(ir, kind, plt)
        if fig is None:
            plt.close("all")
            return {"ok": False, "renderer": "matplotlib", "note": "kind %r not rasterized" % kind}
        dpi = int(ir.get("export", {}).get("png", {}).get("dpi", 300))
        fig.savefig(pdf_path, format="pdf")
        fig.savefig(png_path, dpi=dpi, format="png")
        plt.close(fig)
        return {"ok": True, "renderer": "matplotlib", "png_dpi": dpi}
    except Exception as exc:
        plt.close("all")
        return {"ok": False, "renderer": "matplotlib-error", "note": str(exc)}


def _plot_matplotlib(ir, kind, plt):
    font = ir.get("style", {}).get("font_family", "sans-serif")
    try:
        import matplotlib as mpl
        mpl.rcParams["font.family"] = "sans-serif"
        if font not in ("sans-serif", "serif"):
            mpl.rcParams["font.sans-serif"] = [font, "DejaVu Sans"]
    except Exception:
        pass
    glyphs = ir.get("glyphs", [])
    fig = None
    if kind == "bar":
        fig, ax = plt.subplots(figsize=(7, 4.5))
        groups = []
        for g in glyphs:
            if g.get("x_label") not in groups:
                groups.append(g.get("x_label"))
        series = [s for s in {g.get("series") for g in glyphs} if s is not None]
        if not series:
            xs = range(len(groups))
            ys = [next((g.get("value") for g in glyphs if g.get("x_label") == lab), 0) for lab in groups]
            errs = [next((g.get("error") for g in glyphs if g.get("x_label") == lab), None) for lab in groups]
            ax.bar(xs, ys, yerr=[e for e in errs if e is not None] or None)
            ax.set_xticks(list(xs))
            ax.set_xticklabels(groups)
        else:
            import numpy as np
            x = np.arange(len(groups))
            wd = 0.8 / len(series)
            for si, s in enumerate(series):
                ys, errs = [], []
                for lab in groups:
                    g = next((z for z in glyphs if z.get("x_label") == lab and z.get("series") == s), None)
                    ys.append(g.get("value") if g else 0)
                    errs.append(g.get("error") if g else None)
                ax.bar(x + (si - (len(series) - 1) / 2) * wd, ys, wd, yerr=errs, label=s)
            ax.set_xticks(x)
            ax.set_xticklabels(groups)
            ax.legend()
        ax.set_xlabel(ir.get("axis", {}).get("xlabel") or "")
        ax.set_ylabel(ir.get("axis", {}).get("ylabel") or "")
        ax.set_title((ir.get("figure") or {}).get("title") or "")
    elif kind in ("line", "scatter"):
        fig, ax = plt.subplots(figsize=(7, 4.5))
        by_series = {}
        for g in glyphs:
            if num(g.get("x")) is None or num(g.get("y")) is None:
                continue
            by_series.setdefault(g.get("series") or "series0", []).append(g)
        for sname, gs in sorted(by_series.items()):
            gs = sorted(gs, key=lambda z: num(z.get("x")))
            xs = [num(g.get("x")) for g in gs]
            ys = [num(g.get("y")) for g in gs]
            if kind == "line":
                ax.plot(xs, ys, marker="o", label=sname)
            else:
                ax.scatter(xs, ys, label=sname)
        ax.legend()
        ax.set_xlabel(ir.get("axis", {}).get("xlabel") or "")
        ax.set_ylabel(ir.get("axis", {}).get("ylabel") or "")
    elif kind == "box":
        fig, ax = plt.subplots(figsize=(7, 4.5))
        data = []
        labels = []
        for b in ir.get("boxes", []):
            data.append([num(b["min"]), num(b["q1"]), num(b["median"]), num(b["q3"]), num(b["max"])])
            labels.append(b.get("label", b.get("id", "")))
        ax.boxplot(data, whis=0, showfliers=False, tick_labels=labels)
        ax.set_ylabel(ir.get("axis", {}).get("ylabel") or "")
    elif kind == "heatmap":
        m = ir.get("matrix") or {}
        values = m.get("values", [])
        fig, ax = plt.subplots(figsize=(7, 5))
        im = ax.imshow(values, aspect="auto", cmap="viridis")
        ax.set_xticks(range(len(m.get("col_labels", []))))
        ax.set_xticklabels(m.get("col_labels", []))
        ax.set_yticks(range(len(m.get("row_labels", []))))
        ax.set_yticklabels(m.get("row_labels", []))
        fig.colorbar(im, ax=ax)
    elif kind == "graph":
        fig, ax = plt.subplots(figsize=(7, 5))
        import math as _math
        for e in ir.get("edges", []):
            frm = _find_node(ir, e.get("from"))
            to = _find_node(ir, e.get("to"))
            if not frm or not to:
                continue
            color = e.get("color", "#333333")
            if e.get("kind") in ("inhibition", "suppress", "repress"):
                x1, y1 = frm["x"], frm["y"]
                x2, y2 = to["x"], to["y"]
                ax.plot([x1, x2], [y1, y2], color=color, lw=1.6)
                ang = _math.atan2(y2 - y1, x2 - x1) + _math.pi / 2
                L = 2.5
                px, py = _math.cos(ang) * L, _math.sin(ang) * L
                ax.plot([x2 - px, x2 + px], [y2 - py, y2 + py], color=color, lw=1.6)
            else:
                ax.annotate("", xy=(to["x"], to["y"]), xytext=(frm["x"], frm["y"]),
                            arrowprops=dict(arrowstyle="->", color=color))
            if e.get("label"):
                ax.text((frm["x"] + to["x"]) / 2, (frm["y"] + to["y"]) / 2 + 4, e["label"],
                        ha="center", fontsize=8, color="#555555")
        for n in ir.get("nodes", []):
            x, y = n.get("x", 0), n.get("y", 0)
            label = n.get("label", n.get("id", ""))
            if n.get("shape") == "ellipse":
                from matplotlib.patches import Ellipse
                ax.add_patch(Ellipse((x, y), n.get("rx", 50) * 2, n.get("ry", 26) * 2,
                                     facecolor=n.get("color", "#E8EEF5"), edgecolor="#111111"))
            else:
                tw = max(2.2, len(str(label)) * 0.16)
                from matplotlib.patches import FancyBboxPatch
                ax.add_patch(FancyBboxPatch((x - tw / 2, y - 0.5), tw, 1.0, boxstyle="round,pad=0.1",
                                            facecolor=n.get("color", "#E8EEF5"), edgecolor="#111111"))
            ax.text(x, y, label, ha="center", va="center", fontsize=9)
        ax.set_xlim(-20, max((n.get("x", 0) for n in ir.get("nodes", [])), default=200) + 40)
        ax.set_ylim(-20, max((n.get("y", 0) for n in ir.get("nodes", [])), default=200) + 40)
        ax.axis("off")
    else:
        return None
    fig.tight_layout()
    return fig


# ---- post-render deliverable validation ------------------------------------

def png_info(path):
    try:
        with open(path, "rb") as f:
            sig = f.read(8)
            if sig != b"\x89PNG\r\n\x1a\n":
                return None
            dpi = None
            w = h = None
            while True:
                lb = f.read(4)
                if len(lb) < 4:
                    break
                length = int.from_bytes(lb, "big")
                ctype = f.read(4)
                data = f.read(length)
                f.read(4)  # crc
                if ctype == b"IHDR" and len(data) >= 8:
                    w = int.from_bytes(data[0:4], "big")
                    h = int.from_bytes(data[4:8], "big")
                elif ctype == b"pHYs" and len(data) >= 9:
                    ppu = int.from_bytes(data[0:4], "big")
                    unit = data[8]
                    if unit == 1 and ppu:
                        dpi = round(ppu * 0.0254)
                if ctype == b"IEND":
                    break
            return {"width": w, "height": h, "dpi": dpi}
    except OSError:
        return None


def file_header(path, n=8):
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except OSError:
        return b""


def check_svg_text(svg_path):
    try:
        tree = ET.parse(svg_path)
        root = tree.getroot()
        texts = root.findall(".//{http://www.w3.org/2000/svg}text")
        glyphs = root.findall(".//*[@data-id]")
        return {"ok": True, "n_text": len(texts), "n_glyph": len(glyphs),
                "editable": len(texts) > 0}
    except Exception as exc:
        return {"ok": False, "note": str(exc)}


def validate_deliverables(result_path, output_dir, work_dir):
    result, err = read_json(result_path) if os.path.exists(result_path) else (None, "result missing")
    checks = []
    def add(name, ok, note=""):
        checks.append({"check": name, "ok": bool(ok), "note": note})
    add("result_json_exists", result is not None, err or "")
    if result:
        add("case_id_present", bool(result.get("case_id")))
        add("status_valid", result.get("status") in ("complete", "partial", "failed",
                                                     "insufficient_evidence", "input_error"))
        contract = result.get("output_contract", {}) or {}
        add("contract_keys_backfilled", all(k in contract for k in contract))

    # resolve artifact paths: prefer result.json, else standard names on disk
    art = (result or {}).get("primary_artifacts", {}) if result else {}
    svg = art.get("editable_svg") or "figure.svg"
    png = art.get("preview_png") or "figure.png"
    pdf = art.get("preview_pdf") or "figure.pdf"
    svg_abs = os.path.join(output_dir, svg)
    png_abs = os.path.join(output_dir, png)
    pdf_abs = os.path.join(output_dir, pdf)

    if os.path.exists(svg_abs):
        info = check_svg_text(svg_abs)
        add("svg_parseable", info.get("ok"), "texts=%s glyphs=%s" % (info.get("n_text"), info.get("n_glyph")))
        add("svg_editable_text", info.get("editable"), "")
    else:
        add("svg_parseable", False, "missing: %s" % svg)
    if os.path.exists(png_abs):
        info = png_info(png_abs)
        add("png_valid", info is not None, "")
        if info:
            add("png_dpi_300", info.get("dpi") is None or info["dpi"] >= 300,
                "dpi=%s" % info.get("dpi"))
    else:
        add("png_valid", False, "missing: %s" % png)
    if os.path.exists(pdf_abs):
        add("pdf_valid", file_header(pdf_abs, 5) == b"%PDF-", "")
    else:
        add("pdf_valid", False, "missing: %s" % pdf)
    report = {"checks": checks, "all_ok": all(c["ok"] for c in checks)}
    write_json(os.path.join(work_dir, "check_report.json"), report)
    return report


# ---- finalize --------------------------------------------------------------

def _contract_fill(work_dir):
    p = os.path.join(work_dir, "contract_fill.json")
    if os.path.exists(p):
        obj, _ = read_json(p)
        return obj if isinstance(obj, dict) else {}
    return {}


def _match_artifact(key):
    """Heuristic contract-field -> primary_artifacts key (unknown per-case keys)."""
    kl = key.lower()
    if "png" in kl or "raster" in kl or "bitmap" in kl or "image" in kl or "thumbnail" in kl:
        return "preview_png"
    if "pdf" in kl:
        return "preview_pdf"
    if "evidence" in kl or "mapping" in kl or "trace" in kl:
        return "evidence_map"
    if "reverse" in kl or "index" in kl:
        return "reverse_index"
    if "spec" in kl or " ir" in kl or kl == "ir" or "json" in kl:
        return "figure_spec"
    if "check" in kl or "report" in kl or "qc" in kl or "validate" in kl:
        return "check_report"
    if ("svg" in kl or "vector" in kl or "editable" in kl or "source" in kl or
            "figure" in kl or "chart" in kl or "plot" in kl or "graph" in kl or
            "output" in kl or "result" in kl or "deliverable" in kl):
        return "editable_svg"
    return None


def finalize(result_path, work_dir, output_dir):
    """Merge IR + reports + artifacts into result.json and set an honest status."""
    result, err = read_json(result_path)
    if result is None:
        result = {"case_id": None, "status": "failed", "output_contract": {},
                  "sources": [], "uncertainty": [err], "pending_human": [],
                  "errors": [err], "degradations": [], "evidence_map": [],
                  "annotation_checks": {}, "change_log": [],
                  "runtime": {"version": VERSION, "started_utc": now_utc(), "finished_utc": None}}
    ir, ir_err = load_ir(work_dir)
    ir_report, _ = read_json(os.path.join(work_dir, "ir_check.json"))
    check_report, _ = read_json(os.path.join(work_dir, "check_report.json"))
    render_report, _ = read_json(os.path.join(work_dir, "render_report.json"))
    art = result.setdefault("primary_artifacts", {})
    # map artifacts (relative to output dir)
    for key, rel in (("editable_svg", "figure.svg"), ("preview_png", "figure.png"),
                     ("preview_pdf", "figure.pdf")):
        p = os.path.join(output_dir, rel)
        if os.path.exists(p) and os.path.getsize(p) > 0:
            art[key] = rel
    art["figure_spec"] = "figure_spec.json"
    if os.path.exists(os.path.join(work_dir, "figure_spec.json")):
        import shutil
        shutil.copyfile(os.path.join(work_dir, "figure_spec.json"),
                        os.path.join(output_dir, "figure_spec.json"))
    # evidence map + reverse index from IR (written before copying artifacts out)
    evidence = []
    for g in (ir or {}).get("glyphs", []):
        evidence.append({"glyph": g.get("id"), "data_ref": g.get("data_ref"),
                         "value": g.get("value"), "quote": g.get("quote"),
                         "locator": g.get("locator"), "unit": g.get("unit")})
    result["evidence_map"] = evidence
    write_json(os.path.join(work_dir, "evidence_map.json"), evidence)
    rindex = reverse_index(ir) if ir else {}
    write_json(os.path.join(work_dir, "reverse_index.json"), rindex)

    for key, rel in (("evidence_map", "evidence_map.json"), ("reverse_index", "reverse_index.json"),
                     ("check_report", "check_report.json"), ("render_report", "render_report.json")):
        src = os.path.join(work_dir, rel)
        if os.path.exists(src):
            import shutil
            shutil.copyfile(src, os.path.join(output_dir, rel))
            art[key] = rel

    # fill output_contract: explicit model mapping first, then heuristic by key name
    fill = _contract_fill(work_dir)
    contract = result.get("output_contract", {}) or {}
    for k in contract:
        if k in fill:
            contract[k] = fill[k]
        else:
            cand = _match_artifact(k)
            if cand:
                contract[k] = art.get(cand)
    result["output_contract"] = contract
    if ir_err:
        result.setdefault("degradations", []).append({"kind": "ir", "note": ir_err})

    issues = (ir_report or {}).get("issues", [])
    for i in issues:
        if i.get("severity") == "fatal":
            result.setdefault("errors", []).append(i)
        elif i.get("severity") == "error":
            result.setdefault("uncertainty", []).append(i)
    result["annotation_checks"] = check_report or {}

    # honest status
    has_artifact = bool(art.get("editable_svg"))
    fatal = any(i.get("severity") == "fatal" for i in issues)
    if fatal or not has_artifact:
        result["status"] = "failed" if fatal else "partial"
        if not has_artifact:
            result.setdefault("degradations", []).append(
                {"kind": "artifact", "note": "no editable SVG produced"})
    else:
        if (ir_report or {}).get("n_error", 0) == 0 and (check_report or {}).get("all_ok", True):
            result["status"] = "complete"
        else:
            result["status"] = "partial"
    result["runtime"]["finished_utc"] = now_utc()
    if render_report:
        result["runtime"]["renderer"] = (render_report.get("png_pdf") or {}).get("renderer") \
            or (render_report.get("svg") or {}).get("renderer")
        result["runtime"]["fonts"] = render_report.get("fonts")
    result["change_log"].append({"step": "S6_finalize", "time_utc": now_utc(),
                                 "status": result["status"], "note": "merged artifacts and reports"})
    write_json(result_path, result)
    return result


# ---- pipeline --------------------------------------------------------------

def pipeline(case_path, input_dir, work_dir, output_dir, phases):
    log = []
    if "bootstrap" in phases:
        s = bootstrap(case_path, input_dir, work_dir, output_dir)
        log.append("bootstrap ok, %d materials" % s["n_materials"])
    if "extract" in phases:
        s = extract_all(work_dir, input_dir)
        log.append("extract %d tables" % len(s))
    if "build" in phases:
        s = build(work_dir)
        log.append("build glyphs=%d issues=%d" % (s["glyph_count"], s["n_fatal"] + s["n_error"]))
    if "render" in phases:
        ir, _ = load_ir(work_dir)
        if ir:
            fonts = discover_cjk_fonts()
            ir.setdefault("style", {})["font_family"] = ir["style"].get("font_family") or fonts[0]
            save_ir(work_dir, ir)
            svg_r = render_svg(ir, os.path.join(output_dir, "figure.svg"))
            png_r = render_png_pdf(ir, work_dir, os.path.join(output_dir, "figure.png"),
                                   os.path.join(output_dir, "figure.pdf"))
            report = {"svg": svg_r, "png_pdf": png_r, "fonts": fonts}
            write_json(os.path.join(work_dir, "render_report.json"), report)
            log.append("render svg=%s png_pdf=%s" % (svg_r["ok"], png_r["ok"]))
        else:
            log.append("render skipped: no IR")
    if "validate" in phases:
        validate_deliverables(os.path.join(output_dir, "result.json"), output_dir, work_dir)
        log.append("validate done")
    if "finalize" in phases:
        finalize(os.path.join(output_dir, "result.json"), work_dir, output_dir)
        log.append("finalize done")
    return log
