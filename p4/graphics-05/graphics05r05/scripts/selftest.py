#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — run the skill's robustness + contract-alignment checks on synthetic
fixtures (never on real cases). Exits 0 on pass, 1 on any failure.

Scenarios:
  1. missing /input/case.json  -> failed skeleton, exit code 1
  2. unparseable case.json     -> degraded but valid partial skeleton (no crash)
  3. valid case with a custom output_contract -> full run: skeleton -> build ->
     export -> validate -> finalize; contract aligned, SVG layered, change_log monotonic
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract_case  # noqa: E402
import main as main_mod  # noqa: E402


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def test_missing_case_json(root):
    input_dir = os.path.join(root, "in_missing")
    os.makedirs(input_dir, exist_ok=True)
    out = os.path.join(root, "out_missing")
    work = os.path.join(root, "work_missing")
    code = extract_case.main(["--input", input_dir, "--output", out, "--work", work])
    result = _read_json(os.path.join(out, "result.json"))
    assert code == 1, f"expected exit 1, got {code}"
    assert result.get("status") == "failed"
    assert result.get("case_id")
    return True


def test_bad_json(root):
    input_dir = os.path.join(root, "in_bad")
    _write(os.path.join(input_dir, "case.json"), "this is not json at all {{{")
    out = os.path.join(root, "out_bad")
    work = os.path.join(root, "work_bad")
    summary = extract_case.normalize(input_dir, out, work)
    result = _read_json(os.path.join(out, "result.json"))
    assert summary["ok"] is True, "unparseable case.json should still yield a skeleton"
    assert result.get("status") == "partial"
    assert any(d.get("item") == "unparseable" for d in result.get("degradations", []))
    assert result.get("case_id") and result.get("change_log")
    return True


def test_valid_contract_run(root):
    input_dir = os.path.join(root, "in_valid")
    case = {
        "case_id": "c_test",
        "title": "Synthetic self-test case",
        "output_contract": {
            "graphical_abstract": {"type": "object"},
            "editable_source": {"type": "object"},
            "exports": {"type": "list"},
            "entities": {"type": "list"},
            "relations": {"type": "list"},
            "data_mapping": {"type": "list"},
            "legend": {"type": "list"},
            "sources": {"type": "list"},
            "uncertainty": {"type": "list"},
            "pending_human": {"type": "list"},
            "change_log": {"type": "list"},
            "custom_field": {"type": "string"},
        },
    }
    _write(os.path.join(input_dir, "case.json"), json.dumps(case, ensure_ascii=False))
    _write(os.path.join(input_dir, "task.md"),
           "# self-test\nGenerate a graphical abstract.\n")
    out = os.path.join(root, "out_valid")
    work = os.path.join(root, "work_valid")

    summary = extract_case.normalize(input_dir, out, work)
    assert summary["ok"] is True
    assert "custom_field" in summary["contract_keys"]

    # Fill a deterministic figure spec, then build/validate/finalize.
    spec = _read_json(os.path.join(work, "figure_spec.json"))
    spec["title"] = "Self-test graphical abstract"
    spec["panels"] = [
        {"id": "p_question", "title": "Question", "x": 40, "y": 100, "w": 540, "h": 110,
         "content": "Does signal A activate pathway B?", "source": "task.md"},
        {"id": "p_method", "title": "Method", "x": 620, "y": 100, "w": 540, "h": 110,
         "content": "Perturb A, measure B output.", "source": "task.md"},
    ]
    spec["entities"] = [
        {"id": "ent_a", "label": "Signal A", "kind": "protein", "source": "task.md"},
        {"id": "ent_b", "label": "Pathway B", "kind": "pathway", "source": "task.md"},
    ]
    spec["relations"] = [
        {"id": "rel_1", "from": "ent_a", "to": "ent_b", "direction": "forward",
         "kind": "activation", "label": "activates", "source": "task.md"},
    ]
    spec["data"] = [
        {"id": "d_1", "visual_channel": "length", "label": "B output",
         "value": 42, "unit": "%", "source": "task.md"},
    ]
    spec["legend"] = [
        {"id": "l_1", "label": "activation", "color": "#0072B2", "source": "task.md"},
    ]
    _write(os.path.join(work, "figure_spec.json"),
           json.dumps(spec, ensure_ascii=False))

    final = main_mod.run_all(input_dir, out, work)
    result = _read_json(os.path.join(out, "result.json"))

    # Contract alignment: every case-provided key present, incl. custom_field.
    for key in summary["contract_keys"]:
        assert key in result, f"contract key {key} missing from result.json"

    # SVG checks.
    svg_rel = result["editable_source"]["path"]
    svg_path = os.path.join(out, svg_rel)
    assert os.path.isfile(svg_path), "SVG not written"
    root_el = ET.parse(svg_path).getroot()
    layers = [g.get("id") for g in root_el.iter("{http://www.w3.org/2000/svg}g")]
    texts = list(root_el.iter("{http://www.w3.org/2000/svg}text"))
    assert any(l and l.startswith("layer-") for l in layers), "no layer-* groups"
    assert len(texts) >= 1, "no editable <text>"

    # change_log monotonic.
    versions = [e["version"] for e in result["change_log"]]
    assert versions == sorted(versions) and len(versions) >= 2
    assert result["status"] in ("completed", "partial", "failed")
    return True


def main():
    root = tempfile.mkdtemp(prefix="ga_selftest_")
    tests = [
        ("missing_case_json", test_missing_case_json),
        ("bad_json", test_bad_json),
        ("valid_contract_run", test_valid_contract_run),
    ]
    failures = []
    for name, fn in tests:
        try:
            fn(root)
            print(f"PASS  {name}")
        except AssertionError as exc:
            failures.append(name)
            print(f"FAIL  {name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - report any crash as a failure
            failures.append(name)
            print(f"FAIL  {name}: unexpected {type(exc).__name__}: {exc}")

    print(f"\nselftest: {len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
