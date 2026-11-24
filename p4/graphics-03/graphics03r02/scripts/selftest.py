#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for the mechanism-figure Skill scripts.

Runs the real CLI end-to-end on synthetic, clearly-labelled example data and
asserts the deterministic invariants the Skill promises:
  * bootstrap always writes a valid partial result.json (even on empty/broken input);
  * build produces a parseable, layered SVG with semantic markers and traceability;
  * check accepts a well-formed result (exit 0 or 2 = warnings only);
  * merge/finalize are append-only on change_log.

Standard library only. Usage: python scripts/selftest.py  (exit 0 = pass).
"""
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
PY = sys.executable


def run(*args, cwd=None):
    p = subprocess.run([PY, MAIN] + list(args), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=cwd)
    return p.returncode, p.stdout, p.stderr


def fail(msg):
    print("FAIL: %s" % msg)
    return False


def main():
    ok = True
    tmp = tempfile.mkdtemp(prefix="mech_selftest_")
    input_dir = os.path.join(tmp, "input")
    output_dir = os.path.join(tmp, "output")
    work_dir = os.path.join(tmp, "work")
    os.makedirs(input_dir)

    # 1. empty input: bootstrap must still write a valid partial skeleton
    rc, out, err = run("bootstrap", "--input", input_dir, "--output", output_dir, "--work", work_dir)
    if rc != 0:
        ok = fail("bootstrap on empty input returned %d (%s)" % (rc, err.strip()))
    res_path = os.path.join(output_dir, "result.json")
    if not os.path.exists(res_path):
        ok = fail("bootstrap did not write result.json")
    else:
        res = json.load(open(res_path, encoding="utf-8"))
        if res.get("status") != "partial":
            ok = fail("bootstrap status != partial")
        if not res.get("case_id"):
            ok = fail("bootstrap missing case_id")
        if not (isinstance(res.get("change_log"), list) and res["change_log"]):
            ok = fail("bootstrap change_log empty")
    print("[1/7] bootstrap-empty-input: %s" % ("PASS" if ok else "see above"))

    # 2. broken case.json: still tolerant
    with open(os.path.join(input_dir, "case.json"), "w", encoding="utf-8") as f:
        f.write("{not valid json")
    rc, out, err = run("bootstrap", "--input", input_dir, "--output", output_dir, "--work", work_dir)
    if rc != 0:
        ok = fail("bootstrap on broken case.json returned %d" % rc)
    res = json.load(open(res_path, encoding="utf-8"))
    if not (res.get("degradations") and res.get("case_id")):
        ok = fail("broken input not degraded gracefully")
    print("[2/7] bootstrap-broken-json: %s" % ("PASS" if ok else "see above"))

    # 3. valid case + minimal spec
    case = {"case_id": "case-selftest-001", "title": "Synthetic EGFR-RAS example (self-test only)"}
    with open(os.path.join(input_dir, "case.json"), "w", encoding="utf-8") as f:
        json.dump(case, f)
    spec = {
        "schema_version": "1.0.0",
        "case_id": "case-selftest-001",
        "title": "Synthetic EGFR-RAS example (self-test only)",
        "entities": [
            {"id": "egfr", "label": "EGFR", "type": "receptor", "compartment": "membrane",
             "transmembrane": True, "source": {"locator": "example.md#p1",
             "quote": "EGFR is a transmembrane receptor"}},
            {"id": "ras", "label": "RAS", "type": "protein", "compartment": "cytosol",
             "source": {"locator": "example.md#p2", "quote": "RAS is activated"}},
            {"id": "erk", "label": "ERK", "type": "protein", "compartment": "cytosol",
             "source": {"locator": "example.md#p3", "quote": "ERK translocates to the nucleus"}},
        ],
        "relations": [
            {"id": "r1", "from": "egfr", "to": "ras", "type": "activation",
             "source": {"locator": "example.md#p4", "quote": "EGFR activates RAS"}},
            {"id": "r2", "from": "ras", "to": "erk", "type": "modification", "modifier": "P",
             "source": {"locator": "example.md#p5", "quote": "RAS phosphorylates ERK"}},
        ],
        "data_mappings": [
            {"channel": "fill", "field": "expression", "sample": "#D5E8D4",
             "source": {"locator": "example.md#p6", "quote": "colour encodes expression"}},
        ],
    }
    with open(os.path.join(work_dir, "spec.json"), "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False)
    print("[3/7] fixture-written")

    # 4. validate
    rc, out, err = run("validate", "--work", work_dir)
    if rc not in (0, 2):
        ok = fail("validate returned %d (%s)" % (rc, err.strip()))
    print("[4/7] validate-spec: %s" % ("PASS" if rc in (0, 2) else "see above"))

    # 5. build
    rc, out, err = run("build", "--work", work_dir, "--output", output_dir)
    if rc not in (0, 2):
        ok = fail("build returned %d (%s)" % (rc, err.strip()))
    svg_path = os.path.join(output_dir, "mechanism.svg")
    if not os.path.exists(svg_path):
        ok = fail("build did not write mechanism.svg")
    else:
        try:
            tree = ET.parse(svg_path)
            root = tree.getroot()
            layers = [g.get("{%s}label" % "http://www.inkscape.org/namespaces/inkscape")
                      for g in root.iter() if g.tag.endswith("g")]
            svg_text = open(svg_path, encoding="utf-8").read()
            if "layer-entities" not in svg_text or "layer-relations" not in svg_text:
                ok = fail("SVG missing named layers")
            if "data-source-quote" not in svg_text:
                ok = fail("SVG missing data-source traceability attributes")
            if "marker" not in svg_text:
                ok = fail("SVG missing semantic markers")
        except Exception as e:  # noqa: BLE001
            ok = fail("SVG parse failed: %s" % e)
    print("[5/7] build-svg: %s" % ("PASS" if os.path.exists(svg_path) else "see above"))

    # 6. merge + finalize + check
    rc, out, err = run("merge", "--work", work_dir, "--output", output_dir)
    if rc != 0:
        ok = fail("merge returned %d" % rc)
    rc, out, err = run("finalize", "--output", output_dir, "--status", "partial",
                       "--limitation", "self-test fixture; no real export artifacts")
    if rc != 0:
        ok = fail("finalize returned %d" % rc)
    res = json.load(open(res_path, encoding="utf-8"))
    if len(res.get("change_log", [])) < 3:
        ok = fail("change_log should be append-only (>=3 entries)")
    if not res.get("entities"):
        ok = fail("merge did not populate entities")
    rc, out, err = run("check", "--output", output_dir)
    if rc not in (0, 2):
        ok = fail("check returned %d (%s)" % (rc, err.strip()))
    print("[6/7] merge+finalize+check: %s (rc=%d)" % ("PASS" if rc in (0, 2) else "see above", rc))

    # 7. invalid spec (dangling relation) must be blocking
    bad = json.loads(json.dumps(spec))
    bad["relations"].append({"id": "r9", "from": "nope", "to": "ras", "type": "activation"})
    with open(os.path.join(work_dir, "spec.json"), "w", encoding="utf-8") as f:
        json.dump(bad, f)
    rc, out, err = run("build", "--work", work_dir, "--output", output_dir)
    if rc != 1:
        ok = fail("dangling relation should be blocking (rc=%d)" % rc)
    print("[7/7] blocking-error-detection: %s" % ("PASS" if rc == 1 else "see above"))

    print("selftest: %s (artifacts in %s)" % ("ALL PASS" if ok else "FAILURES PRESENT", tmp))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
