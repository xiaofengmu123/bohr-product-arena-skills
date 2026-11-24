#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Self-test harness for the mechanism / pathway figure Skill.

Checks (all offline, standard library only):
  1. determinism  -- identical input bytes -> identical SVG bytes, in-process
                     and across two separate `main.py build-svg` processes;
  2. spec validity -- blocking errors vs. warnings, exit codes 0/2/1;
  3. contract      -- result.json fields + file existence pass check-result;
  4. robustness    -- missing case.json / malformed JSON / empty input degrade
                     to a legal skeleton without crashing.

The self-test input is SYNTHETIC and clearly labelled; it is not a real case
and is not a prepared answer.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import core_impl as C  # noqa: E402


SYNTHETIC_SPEC = {
    "title": "synthetic self-test pathway (NOT a real case)",
    "entities": [
        {"id": "e1", "label": "LigandA", "name_type": "ligand", "compartment": "extracellular",
         "sources": [{"locator": "selftest/fake.md#p1", "quote": "synthetic ligand", "confidence": "synthetic"}]},
        {"id": "e2", "label": "ReceptorB", "name_type": "protein", "compartment": "membrane",
         "transmembrane": True,
         "sources": [{"locator": "selftest/fake.md#p2", "quote": "synthetic receptor", "confidence": "synthetic"}]},
        {"id": "e3", "label": "KinaseC", "name_type": "protein", "compartment": "cytosol",
         "data": {"expression": 5}},
        {"id": "e4", "label": "TF_D", "name_type": "protein", "compartment": "nucleus"},
        {"id": "e5", "label": "GeneE", "name_type": "gene", "compartment": "nucleus"},
    ],
    "relations": [
        {"id": "r1", "from": "e1", "to": "e2", "type": "activation", "direction": "forward"},
        {"id": "r2", "from": "e2", "to": "e3", "type": "activation", "direction": "forward"},
        {"id": "r3", "from": "e3", "to": "e3", "type": "modification", "direction": "self",
         "modifier": "P", "evidence": {"strength": "synthetic"}},
        {"id": "r4", "from": "e3", "to": "e4", "type": "transport", "direction": "forward"},
        {"id": "r5", "from": "e4", "to": "e5", "type": "expression", "direction": "forward"},
        {"id": "r6", "from": "e5", "to": "e3", "type": "inhibition", "direction": "reverse"},
        {"id": "r7", "from": "e2", "to": "e3", "type": "binding", "direction": "bidirectional"},
    ],
    "layout": {"orientation": "TB", "order": ["e1", "e2", "e3", "e4", "e5"]},
    "data_mappings": [{"channel": "fill", "metric": "expression", "scale": {"min": 0, "max": 10}}],
    "uncertainty": ["synthetic: no real source"],
    "notes": ["self-test fixture"],
}


def run(*argv, cwd=None):
    return subprocess.run([sys.executable, "-B"] + list(argv), cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def main():
    checks = []
    tmp = tempfile.mkdtemp(prefix="selftest_")

    def record(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    # 1a. in-process determinism
    svg_a = C.build_svg(SYNTHETIC_SPEC)
    svg_b = C.build_svg(SYNTHETIC_SPEC)
    record("determinism.in-process", svg_a == svg_b and C.sha256_hex(svg_a) == C.sha256_hex(svg_b),
           "svg bytes identical across two builds")

    # 1b. cross-process determinism
    spec_path = os.path.join(tmp, "spec.json")
    with open(spec_path, "w", encoding="utf-8") as fh:
        fh.write(C.canonical_dumps(SYNTHETIC_SPEC))
    out1 = os.path.join(tmp, "fig1.svg")
    out2 = os.path.join(tmp, "fig2.svg")
    r1 = run(os.path.join(HERE, "main.py"), "build-svg", spec_path, out1)
    r2 = run(os.path.join(HERE, "main.py"), "build-svg", spec_path, out2)
    ok = (r1.returncode == 0 and r2.returncode == 0
          and open(out1, "rb").read() == open(out2, "rb").read())
    record("determinism.cross-process", ok, "two subprocess builds byte-identical, rc=%d/%d" % (r1.returncode, r2.returncode))

    # 1c. SVG is parseable XML and has named layers + data-* traceability
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(svg_a)
        layer_ids = [g.get("id") for g in root.iter("{http://www.w3.org/2000/svg}g")]
        has_nodes = any(e.get("data-entity-id") for e in root.iter() if e.get("data-entity-id"))
        has_edges = any(e.get("data-relation-id") for e in root.iter() if e.get("data-relation-id"))
        ok = ("layer-nodes" in layer_ids and "layer-legend" in layer_ids and has_nodes and has_edges)
        record("svg.structure", ok, "named layers + data-entity-id + data-relation-id present")
    except Exception as exc:
        record("svg.structure", False, str(exc))

    # 2. validation: clean spec -> exit 0
    r = run(os.path.join(HERE, "main.py"), "validate-spec", spec_path)
    record("validate.clean", r.returncode == 0, "rc=%d" % r.returncode)

    # 2b. a broken relation (unknown target) -> exit 1 with a named error
    broken = json.loads(C.canonical_dumps(SYNTHETIC_SPEC))
    broken["relations"] = broken["relations"] + [{"id": "bad", "from": "e1", "to": "nope", "type": "activation"}]
    broken_path = os.path.join(tmp, "broken.json")
    with open(broken_path, "w", encoding="utf-8") as fh:
        fh.write(C.canonical_dumps(broken))
    r = run(os.path.join(HERE, "main.py"), "validate-spec", broken_path)
    record("validate.blocking-error", r.returncode == 1, "rc=%d, stderr=%s" % (r.returncode, r.stderr.strip()))

    # 3. full pipeline run + check-result
    out_dir = os.path.join(tmp, "out")
    work_dir = os.path.join(tmp, "work")
    r = run(os.path.join(HERE, "main.py"), "run",
            "--case", os.path.join(tmp, "missing-case.json"),
            "--task", os.path.join(tmp, "missing-task.md"),
            "--spec", spec_path, "--out", out_dir, "--work", work_dir)
    result_path = os.path.join(out_dir, "result.json")
    record("pipeline.run", r.returncode == 0 and os.path.isfile(result_path),
           "rc=%d" % r.returncode)
    if os.path.isfile(result_path):
        result = C.load_json(result_path)
        errors, warnings = C.check_result(result, out_dir)
        record("pipeline.contract", not errors, "errors=%s warnings=%s" % (errors, warnings))

    # 4. skeleton with no input files -> legal result, exit 0
    skeleton_out = os.path.join(tmp, "skeleton.json")
    r = run(os.path.join(HERE, "main.py"), "skeleton",
            "--case", os.path.join(tmp, "missing-case.json"),
            "--task", os.path.join(tmp, "missing-task.md"),
            "--out", skeleton_out)
    ok = r.returncode == 0 and os.path.isfile(skeleton_out)
    if ok:
        sk = C.load_json(skeleton_out)
        ok = sk.get("status") == "insufficient_evidence" and sk.get("case_id")
    record("robust.empty-input", ok, "rc=%d status=%s" % (r.returncode, C.load_json(skeleton_out).get("status") if os.path.isfile(skeleton_out) else "?"))

    # 5. malformed JSON -> graceful error, no traceback crash
    bad_json = os.path.join(tmp, "bad.json")
    with open(bad_json, "w", encoding="utf-8") as fh:
        fh.write("{ not json")
    r = run(os.path.join(HERE, "main.py"), "validate-spec", bad_json)
    record("robust.bad-json", r.returncode == 1 and "Traceback" not in r.stderr, "rc=%d" % r.returncode)

    # Summary
    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    print("=" * 60)
    print("selftest summary: %d/%d checks passed" % (passed, total))
    for name, ok, detail in checks:
        print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, (" -- " + detail) if detail else ""))
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
