#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-check for IR + SVG artifacts, plus a --smoke bootstrap test.

Usage:
  python selftest.py --ir /app/work/ir.json --svg /app/work/figure.svg --out /app/work/checks.json
  python selftest.py --smoke --out /app/work/checks.json

Always exits 0; the verdict lives in checks.json (status + pass_all_critical).
"""
import argparse
import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET

EXPECTED_CONTRACT = ["entities", "relations", "data_encoding", "legend_annotations",
                     "editable_sources", "exports"]
REQUIRED_ENTITY_KEYS = ["id", "label", "type", "source_ref", "quote"]
REQUIRED_RELATION_KEYS = ["from", "to", "kind", "direction", "source_ref", "quote"]


def norm_text(s):
    return re.sub(r"\s+", "", (s or ""))


def read_text_tolerant(path):
    for enc in ("utf-8-sig", "utf-8"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def add(checklist, level, cid, passed, message):
    checklist.append({"id": cid, "level": level, "pass": bool(passed), "message": message})


def check_ir(ir, checklist):
    entities = ir.get("entities") or []
    relations = ir.get("relations") or []
    ids = {e.get("id") for e in entities if isinstance(e, dict) and e.get("id")}
    add(checklist, "critical", "ir_has_entities", bool(entities),
        f"entities present ({len(entities)})" if entities else "no entities")
    for i, e in enumerate(entities):
        missing = [k for k in REQUIRED_ENTITY_KEYS if not e.get(k)]
        add(checklist, "critical", f"entity_{i}_fields", not missing,
            f"entity[{i}] {e.get('id')} missing {missing}" if missing else f"entity[{i}] {e.get('id')} fields ok")
    dangling = [r for r in relations if r.get("from") not in ids or r.get("to") not in ids]
    add(checklist, "critical", "no_dangling_relations", not dangling,
        f"dangling relations: {[r.get('from','?') + '->' + r.get('to','?') for r in dangling]}" if dangling else "no dangling relations")
    for i, r in enumerate(relations):
        missing = [k for k in REQUIRED_RELATION_KEYS if not r.get(k)]
        add(checklist, "critical", f"relation_{i}_fields", not missing,
            f"relation[{i}] {r.get('from')}->{r.get('to')} missing {missing}" if missing else f"relation[{i}] fields ok")
        if r.get("direction") not in ("forward", "reverse", "bidirectional"):
            add(checklist, "warning", f"relation_{i}_direction", False,
                f"relation[{i}] direction={r.get('direction')} not in forward/reverse/bidirectional")
    # contract field presence (reported, not fatal: editable_sources/exports merged later)
    for field in EXPECTED_CONTRACT:
        present = field in ir or field in ("entities", "relations")
        if not present:
            add(checklist, "warning", f"missing_contract_{field}", False,
                f"IR lacks contract field {field} (will be merged later)")


def check_svg(svg_path, ir, checklist, viewbox):
    if not os.path.exists(svg_path):
        add(checklist, "critical", "svg_exists", False, f"SVG missing: {svg_path}")
        return
    add(checklist, "critical", "svg_exists", True, f"SVG present: {svg_path}")
    try:
        tree = ET.parse(svg_path)
        root = tree.getroot()
    except ET.ParseError as exc:
        add(checklist, "critical", "svg_parse", False, f"SVG parse error: {exc}")
        return
    add(checklist, "critical", "svg_parse", True, "SVG parses as XML")

    ns = {"s": "http://www.w3.org/2000/svg"}
    layer_ids = {g.get("id") for g in root.findall(".//s:g", ns)}
    for layer in ("layer-relations", "layer-entities", "layer-annotations", "layer-legend"):
        add(checklist, "critical", f"layer_{layer}", layer in layer_ids,
            f"layer {layer} " + ("present" if layer in layer_ids else "missing"))

    entities = ir.get("entities") or []
    relations = ir.get("relations") or []
    # source_ref coverage inside SVG
    svg_text = read_text_tolerant(svg_path) or ""
    uncovered = []
    for e in entities:
        rid = str(e.get("id", ""))
        if rid and f'data-entity-id="{rid}"' not in svg_text:
            uncovered.append(rid)
    add(checklist, "critical", "svg_source_ref_coverage", not uncovered,
        f"entities missing from SVG: {uncovered}" if uncovered else "all entities present in SVG with source_ref")
    # marker-end presence on relation paths
    paths = root.findall(".//s:path", ns)
    has_markers = any("marker-end" in (p.get("marker-end") or "") or p.get("marker-end") for p in paths) or any(
        "marker-end" in (p.attrib or {}) for p in paths)
    add(checklist, "critical", "svg_arrowheads", has_markers or not relations,
        "arrowheads present" if has_markers else "no arrowheads; flow direction may be invisible")

    # canvas overflow
    w, h = (viewbox.get("width"), viewbox.get("height"))
    try:
        w, h = float(w), float(h)
    except (TypeError, ValueError):
        w, h = 1200.0, 900.0
    overflow = []
    for e in entities:
        x = e.get("x"); y = e.get("y")
        if x is None or y is None:
            continue  # auto-layout boxes were computed; check via svg bbox is skipped
        if float(x) < 0 or float(y) < 0 or float(x) + float(e.get("w", 168)) > w or float(y) + float(e.get("h", 64)) > h:
            overflow.append(e.get("id"))
    add(checklist, "warning", "canvas_overflow", not overflow,
        f"overflow entities: {overflow}" if overflow else "no explicit overflow detected")


def check_quotes(ir, source_texts, checklist):
    entities = ir.get("entities") or []
    relations = ir.get("relations") or []
    if not source_texts:
        add(checklist, "info", "quote_source_match", None,
            "no source material text provided; quote matching skipped")
        return
    haystack = norm_text("\n".join(source_texts))
    bad = []
    for e in entities + relations:
        q = e.get("quote") or ""
        if q and norm_text(q) not in haystack:
            bad.append((e.get("id") or e.get("from"), q[:40]))
    add(checklist, "warning", "quote_matches_source", not bad,
        f"quotes not found in material: {bad}" if bad else "all quotes match source material")


def load_source_texts(material_paths):
    texts = []
    for p in material_paths or []:
        if os.path.isdir(p):
            for dp, _, fns in os.walk(p):
                for fn in sorted(fns):
                    if fn.lower().endswith((".txt", ".md", ".json", ".csv", ".yaml", ".yml")):
                        t = read_text_tolerant(os.path.join(dp, fn))
                        if t:
                            texts.append(t)
        else:
            t = read_text_tolerant(p)
            if t:
                texts.append(t)
    return texts


def run(ir_path, svg_path, out_path, material_paths, viewbox):
    checklist = []
    ir = {}
    if ir_path and os.path.exists(ir_path):
        try:
            ir = json.loads(read_text_tolerant(ir_path) or "{}")
        except json.JSONDecodeError as exc:
            add(checklist, "critical", "ir_parse", False, f"IR parse error: {exc}")
    else:
        add(checklist, "critical", "ir_exists", False, "IR missing")
    if ir:
        check_ir(ir, checklist)
    check_svg(svg_path, ir, checklist, viewbox)
    check_quotes(ir, load_source_texts(material_paths), checklist)

    critical_fail = [c for c in checklist if c["level"] == "critical" and c["pass"] is False]
    warnings = [c for c in checklist if c["level"] == "warning" and c["pass"] is False]
    summary = {
        "status": "pass" if not critical_fail else "fail",
        "pass_all_critical": not critical_fail,
        "critical_failures": len(critical_fail),
        "warnings": len(warnings),
        "total_checks": len(checklist),
        "checks": checklist,
    }
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return summary


def smoke(out_path):
    demo_ir = {
        "kind": "flowchart",
        "title": "smoke-demo (示意，非正式案例)",
        "entities": [
            {"id": "e1", "label": "原料A", "type": "input", "source_ref": "demo:1", "quote": "原料A"},
            {"id": "e2", "label": "混合", "type": "step", "source_ref": "demo:2", "quote": "混合"},
            {"id": "e3", "label": "产物B", "type": "output", "source_ref": "demo:3", "quote": "产物B"},
        ],
        "relations": [
            {"id": "r1", "from": "e1", "to": "e2", "kind": "flow", "direction": "forward",
             "source_ref": "demo:r1", "quote": "原料A流向混合"},
            {"id": "r2", "from": "e2", "to": "e3", "kind": "flow", "direction": "forward",
             "source_ref": "demo:r2", "quote": "混合后得产物B"},
        ],
        "data_encoding": [{"key": "流量", "value": "10 mL/min", "visual": "线宽 2.4px",
                           "source_ref": "demo:d1", "quote": "10 mL/min"}],
        "legend": [{"key": "flow", "visual": "蓝色实线箭头", "label": "物料流向",
                    "source_ref": "demo:l1", "quote": "物料流向"}],
    }
    here = os.path.dirname(os.path.abspath(__file__))
    build = os.path.join(here, "build_svg.py")
    with tempfile.TemporaryDirectory() as td:
        ir_file = os.path.join(td, "ir.json")
        svg_file = os.path.join(td, "figure.svg")
        with open(ir_file, "w", encoding="utf-8") as fh:
            json.dump(demo_ir, fh, ensure_ascii=False)
        import subprocess
        proc = subprocess.run([sys.executable, build, "--ir", ir_file, "--out", svg_file],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        built_ok = proc.returncode == 0 and os.path.exists(svg_file)
        if built_ok:
            summary = run(ir_file, svg_file, out_path, [], {"width": 1200, "height": 900})
        else:
            summary = {"status": "fail", "pass_all_critical": False,
                       "critical_failures": 1, "warnings": 0, "total_checks": 1,
                       "checks": [{"id": "smoke_build", "level": "critical", "pass": False,
                                   "message": proc.stderr or "build_svg failed"}]}
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(summary, fh, ensure_ascii=False, indent=2)
    summary["smoke_mode"] = True
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description="self-check IR + SVG")
    ap.add_argument("--ir", default=None)
    ap.add_argument("--svg", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--material", action="append", default=None)
    ap.add_argument("--width", type=float, default=1200)
    ap.add_argument("--height", type=float, default=900)
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args(argv)
    try:
        if args.smoke:
            summary = smoke(args.out)
        else:
            summary = run(args.ir, args.svg, args.out, args.material,
                          {"width": args.width, "height": args.height})
        print(json.dumps(summary, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "fail", "pass_all_critical": False,
                          "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
