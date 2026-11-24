# -*- coding: utf-8 -*-
"""selftest.py -- end-to-end self-check + determinism guarantee.

Runs the real pipeline (main.py) on a synthetic demo input in a temporary
directory. Verifies:

1. full pipeline produces valid result.json + well-formed SVG;
2. two runs of identical input produce byte-identical result.json and SVG
   (determinism requirement);
3. evidence traceability (locator + verbatim quote) is backfilled;
4. degradation paths (empty input dir, bad JSON) still write a valid result
   and do not crash.

Stdlib only. Exit code 0 = all checks pass.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main as pipeline  # noqa: E402

DEMO_CASE_ID = "demo-001"
DEMO_CASE = {
    "case_id": DEMO_CASE_ID,
    "title": "Demo: deterministic graphical abstract",
    "output_contract": {
        "graphical_abstract": {"question": None, "method": None, "results": None, "conclusion": None},
        "editable_source": None,
        "exports": None,
        "sources": None,
        "custom_field": "keep-me",
    },
}
DEMO_TASK = "# Task\nProduce a one-page graphical abstract (Question -> Method -> Results -> Conclusion).\n"
DEMO_MATERIAL = (
    "# materials/demo.md\n"
    "## Results\n"
    "Treatment B increased signal by 42% relative to control (n=6). "
    "The kinase K activates factor F, which then inhibits target T.\n"
)

DEMO_SPEC = {
    "schema": "figure-spec-v1",
    "case_id": DEMO_CASE_ID,
    "title": "Demo: deterministic graphical abstract",
    "canvas": {"width": 1200, "height": 900, "unit": "px"},
    "panels": [
        {"id": "question", "label": "Question", "order": 1},
        {"id": "method", "label": "Method", "order": 2},
        {"id": "results", "label": "Results", "order": 3},
        {"id": "conclusion", "label": "Conclusion", "order": 4},
    ],
    "entities": [
        {"id": "e1", "label": "Kinase K", "kind": "molecule", "panel": "method",
         "source": "materials/demo.md#p1", "quote": "kinase K activates factor F"},
        {"id": "e2", "label": "Factor F", "kind": "molecule", "panel": "method",
         "source": "materials/demo.md#p1", "quote": "factor F"},
        {"id": "e3", "label": "Target T", "kind": "molecule", "panel": "method",
         "source": "materials/demo.md#p1", "quote": "inhibits target T"},
    ],
    "relations": [
        {"id": "r1", "from": "e1", "to": "e2", "direction": "->", "label": "activates",
         "source": "materials/demo.md#p1", "quote": "activates factor F"},
        {"id": "r2", "from": "e2", "to": "e3", "direction": "->", "label": "inhibits",
         "source": "materials/demo.md#p1", "quote": "inhibits target T"},
    ],
    "data": [
        {"id": "d1", "label": "Signal B", "value": 42.0, "unit": "%", "encoding": "bar-length",
         "panel": "results", "source": "materials/demo.md#p1",
         "quote": "increased signal by 42% relative to control"},
    ],
    "constraints": [
        {"type": "condition", "label": "n", "value": "6", "source": "materials/demo.md#p1",
         "quote": "n=6"},
    ],
    "legend": [
        {"id": "l1", "label": "Treatment B", "color": "#4C72B0", "target": "d1",
         "source": "materials/demo.md#p1", "quote": "Treatment B increased signal"},
    ],
    "evidence": [
        {"id": "s1", "locator": "materials/demo.md#p1",
         "quote": "Treatment B increased signal by 42% relative to control (n=6)."},
    ],
}

REQUIRED_RESULT_KEYS = [
    "case_id", "status", "graphical_abstract", "editable_source", "exports",
    "entities", "relations", "data_mapping", "legend", "sources", "uncertainty",
    "pending_human", "change_log", "limitations", "errors", "degradations", "determinism",
]


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def make_demo_input(root):
    inp = os.path.join(root, "input")
    _write(os.path.join(inp, "case.json"), json.dumps(DEMO_CASE, ensure_ascii=False))
    _write(os.path.join(inp, "task.md"), DEMO_TASK)
    _write(os.path.join(inp, "materials", "demo.md"), DEMO_MATERIAL)
    return inp


def run_full(root, populate_spec=True):
    inp = make_demo_input(root)
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    os.makedirs(work, exist_ok=True)
    if populate_spec:
        _write(os.path.join(work, "figure_spec.json"),
               json.dumps(DEMO_SPEC, ensure_ascii=False, sort_keys=True))
    code = pipeline.main(["run", "--input", inp, "--output", out, "--work", work])
    return code, out, work


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def check_determinism():
    root1 = tempfile.mkdtemp(prefix="selftest-a-")
    root2 = tempfile.mkdtemp(prefix="selftest-b-")
    try:
        code1, out1, _w1 = run_full(root1)
        code2, out2, _w2 = run_full(root2)
        assert code1 == code2 == 0, "pipeline exited non-zero (%s, %s)" % (code1, code2)
        r1 = read(os.path.join(out1, "result.json"))
        r2 = read(os.path.join(out2, "result.json"))
        assert r1 == r2, "result.json differs between two identical runs"
        svg1 = read(os.path.join(out1, "fig_%s.svg" % DEMO_CASE_ID))
        svg2 = read(os.path.join(out2, "fig_%s.svg" % DEMO_CASE_ID))
        assert svg1 == svg2, "SVG differs between two identical runs"
        result = json.loads(r1)
        assert result["status"] == "completed", "expected completed, got %s" % result["status"]
        assert result["determinism"]["fingerprint"], "fingerprint missing"
        assert result["sources"], "sources not backfilled"
        assert result["sources"][0]["quote"], "verbatim quote missing in sources"
        ET.fromstring(svg1)
        return True, "determinism: identical result.json + SVG, fingerprint present, status=completed"
    finally:
        shutil.rmtree(root1, ignore_errors=True)
        shutil.rmtree(root2, ignore_errors=True)


def check_contract():
    root = tempfile.mkdtemp(prefix="selftest-c-")
    try:
        code, out, _w = run_full(root)
        result = json.loads(read(os.path.join(out, "result.json")))
        for key in REQUIRED_RESULT_KEYS:
            assert key in result, "missing required key %s" % key
        assert result["custom_field"] == "keep-me", "output_contract field not preserved"
        assert result["case_id"] == DEMO_CASE_ID, "case_id not preserved"
        return True, "contract: all required keys + output_contract field preserved"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_empty_dir():
    root = tempfile.mkdtemp(prefix="selftest-e-")
    try:
        inp = os.path.join(root, "empty")
        os.makedirs(inp)
        out = os.path.join(root, "output")
        work = os.path.join(root, "work")
        code = pipeline.main(["extract", "--input", inp, "--output", out, "--work", work])
        result = json.loads(read(os.path.join(out, "result.json")))
        assert result["status"] == "failed", "empty dir should be failed, got %s" % result["status"]
        assert result["case_id"], "case_id placeholder missing"
        assert result["limitations"], "limitation missing for empty dir"
        return True, "empty input: valid result.json (status=failed) written, exit=%s" % code
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_bad_json():
    root = tempfile.mkdtemp(prefix="selftest-j-")
    try:
        inp = os.path.join(root, "input")
        os.makedirs(inp)
        _write(os.path.join(inp, "case.json"), "{ not valid json ")
        out = os.path.join(root, "output")
        work = os.path.join(root, "work")
        code = pipeline.main(["extract", "--input", inp, "--output", out, "--work", work])
        result = json.loads(read(os.path.join(out, "result.json")))
        assert any("not valid JSON" in e for e in result.get("errors", [])), "bad JSON not recorded"
        return True, "bad JSON: error recorded, valid result.json still written, exit=%s" % code
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    checks = [
        ("determinism", check_determinism),
        ("contract", check_contract),
        ("empty_dir", check_empty_dir),
        ("bad_json", check_bad_json),
    ]
    failed = 0
    for name, fn in checks:
        try:
            ok, msg = fn()
            print("PASS  %-12s %s" % (name, msg))
            if not ok:
                failed += 1
        except AssertionError as exc:
            failed += 1
            print("FAIL  %-12s %s" % (name, exc))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("ERROR %-12s %s" % (name, exc))
    print("selftest: %d/%d passed" % (len(checks) - failed, len(checks)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
