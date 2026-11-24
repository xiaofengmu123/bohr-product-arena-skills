#!/usr/bin/env python3
"""Self-test for figure-model-skill.

Runs the full pipeline (init -> build -> validate -> merge -> finalize) on a minimal
synthetic case in a temporary directory, then asserts the result structure and SVG
parseability. Exit code 0 means pass. Does not fabricate benchmark results.
"""
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(*args):
    return subprocess.run(
        [sys.executable, MAIN] + list(args),
        capture_output=True, text=True, encoding="utf-8", errors="replace")


def main():
    tmp = tempfile.mkdtemp(prefix="figure-model-selftest-")
    work = os.path.join(tmp, "work")
    out = os.path.join(tmp, "out")
    os.makedirs(work)
    os.makedirs(out)

    case_path = os.path.join(tmp, "case.json")
    case = {
        "case_id": "selftest-case",
        "title": "selftest figure",
        "output_contract": {
            "entities": [], "relations": [], "data_encoding": [],
            "legend_annotations": [], "editable_sources": [], "exports": [],
        },
    }
    with open(case_path, "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False)

    ir_path = os.path.join(work, "ir.json")
    ir = {
        "case_id": "selftest-case",
        "kind": "flowchart",
        "title": "selftest flow",
        "entities": [
            {"id": "a", "label": "input", "type": "material",
             "source_ref": "case.json#selftest", "quote": "input"},
            {"id": "b", "label": "process", "type": "step",
             "source_ref": "case.json#selftest", "quote": "process"},
            {"id": "c", "label": "output", "type": "material",
             "source_ref": "case.json#selftest", "quote": "output"},
        ],
        "relations": [
            {"from": "a", "to": "b", "kind": "flow", "direction": "forward",
             "source_ref": "case.json#selftest", "quote": "a->b"},
            {"from": "b", "to": "c", "kind": "flow", "direction": "forward",
             "source_ref": "case.json#selftest", "quote": "b->c"},
        ],
        "legend_annotations": [
            {"key": "flow", "text": "material flow", "color": "#2c6fbb",
             "source_ref": "case.json#selftest"},
        ],
        "sources": [{"locator": "case.json", "quote": "selftest"}],
    }
    with open(ir_path, "w", encoding="utf-8") as fh:
        json.dump(ir, fh, ensure_ascii=False)

    svg_path = os.path.join(work, "figure.svg")
    result_path = os.path.join(out, "result.json")
    checks_path = os.path.join(work, "checks.json")

    steps = [
        ("init", ["init", "--case", case_path, "--work", work, "--out", out]),
        ("build", ["build", "--ir", ir_path, "--out", svg_path]),
        ("validate", ["validate", "--ir", ir_path, "--result", result_path,
                      "--svg", svg_path, "--out", checks_path]),
        ("merge", ["merge", "--case", case_path, "--ir", ir_path,
                   "--work", work, "--out", out]),
        ("finalize", ["finalize", "--out", out, "--status", "complete"]),
    ]
    for name, argv in steps:
        proc = run(*argv)
        if proc.returncode != 0:
            print("SELFTEST FAIL at %s: rc=%s\nstdout=%s\nstderr=%s"
                  % (name, proc.returncode, proc.stdout, proc.stderr))
            return 1

    with open(result_path, encoding="utf-8") as fh:
        result = json.load(fh)
    assert result["case_id"] == "selftest-case", result
    assert result["status"] == "complete", result
    for key in ("entities", "relations", "data_encoding",
                "legend_annotations", "editable_sources", "exports"):
        assert key in result, "missing contract key %s" % key
    assert result["entities"], "entities were not merged into result.json"
    assert result["checks"], "checks were not merged into result.json"
    ET.parse(svg_path)

    print("SELFTEST OK: init/build/validate/merge/finalize passed; "
          "SVG parses; contract fields and checks present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
