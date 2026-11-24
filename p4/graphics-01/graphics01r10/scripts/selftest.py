#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for sci-figure-authoring.

Runs four checks without touching the user's materials:
  1. example IR -> validate -> render SVG -> parse XML -> markers/layers present
  2. legend-data mismatch is detected (warning/error)
  3. empty input dir -> honest failed skeleton, exit code 0
  4. bad JSON -> tolerant load, no crash

The example IR below is a synthetic test fixture, NOT a formal case answer.
Standard library only.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl as core


def example_ir():
    """Synthetic self-test fixture (example, not an answer)."""
    return {
        "meta": {"case_id": "selftest-example", "figure_type": "mechanism",
                 "title": "Self-test mechanism (synthetic example)", "canvas": {"width": 1000, "height": 700}, "auto_legend": True},
        "entities": [
            {"id": "A", "label": "Ligand", "kind": "protein",
             "evidence": {"source_ref": "demo.md", "locator": "p1", "quote": "Ligand binds receptor", "confidence": "high"}},
            {"id": "B", "label": "Receptor", "kind": "protein",
             "evidence": {"source_ref": "demo.md", "locator": "p1", "quote": "receptor activation", "confidence": "high"}},
            {"id": "C", "label": "Effector", "kind": "protein",
             "evidence": {"source_ref": "demo.md", "locator": "p2", "quote": "activates effector", "confidence": "high"}},
            {"id": "D", "label": "Repressor", "kind": "protein",
             "evidence": {"source_ref": "demo.md", "locator": "p2", "quote": "repressor inhibits", "confidence": "high"}},
        ],
        "relations": [
            {"id": "r1", "from": "A", "to": "B", "type": "activates", "color": "#333333",
             "evidence": {"source_ref": "demo.md", "locator": "p1", "quote": "Ligand activates receptor", "confidence": "high"}},
            {"id": "r2", "from": "B", "to": "C", "type": "activates", "color": "#333333",
             "evidence": {"source_ref": "demo.md", "locator": "p1", "quote": "receptor activates effector", "confidence": "high"}},
            {"id": "r3", "from": "D", "to": "C", "type": "inhibits", "color": "#cc0000",
             "evidence": {"source_ref": "demo.md", "locator": "p2", "quote": "repressor inhibits effector", "confidence": "high"}},
        ],
        "groups": [],
        "legend": [
            {"item_id": "lg1", "label": "activates", "color": "#333333", "marker": "arrow", "maps_to": "relation.type", "maps_value": "activates",
             "evidence": {"source_ref": "demo.md", "locator": "p1", "quote": "activates", "confidence": "high"}},
            {"item_id": "lg2", "label": "inhibits", "color": "#cc0000", "marker": "tee", "maps_to": "relation.type", "maps_value": "inhibits",
             "evidence": {"source_ref": "demo.md", "locator": "p2", "quote": "inhibits", "confidence": "high"}},
        ],
        "annotations": [],
    }


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print("[%s] %s %s" % (status, name, detail))
    return bool(cond)


def main():
    results = []

    # 1. render + validate
    ir = example_ir()
    errors, warnings = core.validate_ir(ir)
    le, lw = core.legend_consistency(ir)
    errors += le
    warnings += lw
    results.append(check("validate_example_ir", len(errors) == 0, "errors=%d warnings=%d" % (len(errors), len(warnings))))
    svg = core.render_svg(ir)
    results.append(check("render_svg_nonempty", len(svg) > 500, "bytes=%d" % len(svg)))
    root = ET.fromstring(svg)
    ns = {"svg": "http://www.w3.org/2000/svg"}
    markers = root.findall(".//svg:marker", ns)
    layers = root.findall(".//{http://www.w3.org/2000/svg}g", ns)
    texts = root.findall(".//svg:text", ns)
    results.append(check("svg_markers_present", len(markers) >= 2, "markers=%d (arrow+tee)" % len(markers)))
    results.append(check("svg_layers_present", len(layers) >= 4, "layers=%d" % len(layers)))
    results.append(check("svg_editable_text", len(texts) >= 4, "texts=%d" % len(texts)))
    results.append(check("svg_inhibition_is_tee", "tee-" in svg and "arrow-" in svg, "marker ids present"))

    # 2. legend mismatch detection
    bad = example_ir()
    bad["legend"][1]["maps_value"] = "flows_to"  # value not in relation.type distinct set
    _, lw = core.legend_consistency(bad)
    results.append(check("legend_mismatch_detected", any("不在数据字段" in w["reason"] for w in lw), str(lw)))

    # 3 + 4. end-to-end in temp dir
    tmp = tempfile.mkdtemp(prefix="scifig_selftest_")
    try:
        inp = os.path.join(tmp, "input")
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        os.makedirs(inp)
        # empty input
        rc = subprocess.run([sys.executable, os.path.join(HERE, "main.py"),
                             "--input", inp, "--output", out, "--work", work],
                            capture_output=True, text=True)
        res = core.load_json(os.path.join(out, "result.json"), {})
        results.append(check("empty_input_no_crash", rc.returncode == 0, "rc=%d" % rc.returncode))
        results.append(check("empty_input_honest_status", res.get("status") == "failed", "status=%s" % res.get("status")))

        # bad JSON tolerated
        bad_json = os.path.join(inp, "case.json")
        with open(bad_json, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        os.makedirs(work, exist_ok=True)
        write = core.write_json
        write(os.path.join(work, "figure_ir.json"), example_ir())
        rc = subprocess.run([sys.executable, os.path.join(HERE, "main.py"),
                             "--input", inp, "--output", out, "--work", work],
                            capture_output=True, text=True)
        res = core.load_json(os.path.join(out, "result.json"), {})
        results.append(check("bad_json_tolerated", rc.returncode == 0 and res.get("status") in ("complete", "partial"), "status=%s" % res.get("status")))

        # valid synthetic case end-to-end
        with open(bad_json, "w", encoding="utf-8") as f:
            json.dump({"case_id": "selftest-example"}, f, ensure_ascii=False)
        rc = subprocess.run([sys.executable, os.path.join(HERE, "main.py"),
                             "--input", inp, "--output", out, "--work", work],
                            capture_output=True, text=True)
        res = core.load_json(os.path.join(out, "result.json"), {})
        svg_path = os.path.join(out, "figure01.svg")
        svg_ok = os.path.exists(svg_path)
        results.append(check("e2e_pipeline_rc0", rc.returncode == 0, "rc=%d" % rc.returncode))
        # 'complete' requires a verified export; when no renderer is installed the
        # pipeline must honestly degrade to 'partial' instead of faking a PNG/PDF.
        results.append(check("e2e_result_honest", res.get("status") in ("complete", "partial"), "status=%s" % res.get("status")))
        results.append(check("e2e_export_recorded", isinstance(res.get("export_files"), list) and len(res.get("export_files")) >= 1, "exports=%d" % len(res.get("export_files", []))))
        results.append(check("e2e_svg_exists", svg_ok, svg_path))
        results.append(check("e2e_provenance_has_quote", all(p.get("quote") for p in res.get("provenance", [])), "provenance=%d" % len(res.get("provenance", []))))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = sum(1 for r in results if not r)
    print("=" * 40)
    print("selftest: %d/%d passed" % (len(results) - failed, len(results)))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
