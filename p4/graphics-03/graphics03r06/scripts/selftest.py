#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py -- offline self-test for the Skill package.

Runs the entry point end-to-end on synthetic inputs (happy path, empty input,
bad JSON) and verifies the output contract and artifact validity. Uses only the
Python standard library; does not require the platform runtime or external
tools. Exit code 0 means all checks passed; 1 means at least one failed.

Usage::

    python scripts/selftest.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAIN = HERE / "main.py"
ROOT = HERE.parent

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

PLAN = {
    "schema_version": "1.0.0",
    "case_id": "selftest-case-001",
    "task_notes": "synthetic self-test mechanism",
    "material_files": [{"path": "materials/paper.md", "kind": "text", "source_id": "M1"}],
    "entity_mentions": [
        {"mention": "EGF", "resolved_id": "EGF", "type_hint": "small_molecule",
         "label": "EGF", "confidence": "high",
         "sources": [{"locator": "materials/paper.md#p1",
                      "quote": "epidermal growth factor (EGF) binds the receptor"}]},
        {"mention": "EGFR", "resolved_id": "EGFR", "type_hint": "protein",
         "label": "EGFR", "confidence": "high",
         "sources": [{"locator": "materials/paper.md#p1",
                      "quote": "EGF binds the epidermal growth factor receptor (EGFR)"}]},
        {"mention": "AKT1", "resolved_id": "AKT1", "type_hint": "gene",
         "label": "AKT1", "confidence": "high",
         "sources": [{"locator": "materials/paper.md#p2",
                      "quote": "AKT1 is transcriptionally induced"}]},
        {"mention": "AKT protein", "resolved_id": "AKT", "type_hint": "protein",
         "label": "AKT", "confidence": "high",
         "sources": [{"locator": "materials/paper.md#p3",
                      "quote": "AKT phosphorylates mTOR"}]},
        {"mention": "mTOR", "resolved_id": "mTOR", "type_hint": "protein",
         "label": "mTOR", "confidence": "high",
         "sources": [{"locator": "materials/paper.md#p3",
                      "quote": "AKT phosphorylates mTOR"}]},
    ],
    "relation_mentions": [
        {"subject": "EGF", "object": "EGFR", "predicate_text": "binds",
         "semantic_hint": "binding", "confidence": "high",
         "sources": [{"locator": "materials/paper.md#p1",
                      "quote": "EGF binds EGFR"}]},
        {"subject": "EGFR", "object": "AKT", "predicate_text": "activates",
         "semantic_hint": "activation", "confidence": "high",
         "sources": [{"locator": "materials/paper.md#p3",
                      "quote": "EGFR activates AKT"}]},
        {"subject": "AKT", "object": "mTOR", "predicate_text": "phosphorylates",
         "semantic_hint": "modification", "confidence": "high",
         "sources": [{"locator": "materials/paper.md#p3",
                      "quote": "AKT phosphorylates mTOR"}]},
        {"subject": "AKT1", "object": "AKT", "predicate_text": "encodes",
         "semantic_hint": "expression", "confidence": "medium",
         "sources": [{"locator": "materials/paper.md#p2",
                      "quote": "AKT1 encodes AKT"}]},
    ],
    "layout_hints": [
        {"entity": "EGF", "compartment": "extracellular"},
        {"entity": "EGFR", "compartment": "membrane", "transmembrane": True},
        {"entity": "AKT1", "compartment": "nucleus"},
        {"entity": "AKT", "compartment": "cytosol"},
        {"entity": "mTOR", "compartment": "cytosol"},
    ],
    "data_items": [
        {"subject": "AKT", "metric": "expression", "value": 2.3, "unit": "fold",
         "encoding": "node badge", "sources": [
             {"locator": "materials/paper.md#p4", "quote": "AKT increased 2.3-fold"}]},
    ],
    "ambiguities": [{"text": "AKT gene vs protein disambiguated by context",
                     "alternatives": ["AKT gene", "AKT protein"],
                     "locator": "materials/paper.md#p2"}],
    "pending_human": [],
}

CASE_JSON = {"case_id": "selftest-case-001", "title": "synthetic test case"}

TASK_MD = "# synthetic mechanism task\n\nDraw the EGF/EGFR/AKT/mTOR pathway.\n"

MATERIAL_MD = """# synthetic paper

p1: epidermal growth factor (EGF) binds the epidermal growth factor receptor (EGFR).
p2: AKT1 is transcriptionally induced and encodes AKT.
p3: EGFR activates AKT; AKT phosphorylates mTOR.
p4: AKT increased 2.3-fold.
"""


def run_main(args, cwd=None):
    return subprocess.run(
        [sys.executable, str(MAIN)] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=cwd)


def make_case(base: Path, with_plan: bool = True, corrupt_case: bool = False) -> None:
    (base / "input").mkdir(parents=True, exist_ok=True)
    (base / "input" / "materials").mkdir(parents=True, exist_ok=True)
    (base / "work").mkdir(parents=True, exist_ok=True)
    (base / "output").mkdir(parents=True, exist_ok=True)
    (base / "input" / "case.json").write_text(
        "{broken" if corrupt_case else json.dumps(CASE_JSON), encoding="utf-8")
    (base / "input" / "task.md").write_text(TASK_MD, encoding="utf-8")
    (base / "input" / "materials" / "paper.md").write_text(MATERIAL_MD, encoding="utf-8")
    if with_plan:
        (base / "work" / "plan.json").write_text(
            json.dumps(PLAN, ensure_ascii=False), encoding="utf-8")


def check_result_json(path: Path):
    result = json.loads(path.read_text(encoding="utf-8"))
    assert result.get("case_id") == "selftest-case-001", result.get("case_id")
    assert result.get("status") in ("success", "partial"), result.get("status")
    for key in ("editable_source_files", "export_artifacts", "entities",
                "relations", "legend", "data_mappings", "figure_model",
                "sources", "uncertainty", "pending_human", "change_log"):
        assert key in result, f"missing {key}"
    assert result.get("change_log"), "change_log empty"


def parse_svg(path: Path):
    ET.parse(str(path))
    svg = path.read_text(encoding="utf-8")
    for token in ("layer-compartments", "layer-relations", "layer-entities",
                  "data-entity-id", "data-relation-id"):
        assert token in svg, f"SVG missing {token}"
    return svg


def test_happy_path() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        make_case(base, with_plan=True)
        r = run_main(["--input", str(base / "input"), "--output", str(base / "output"),
                      "--work", str(base / "work")])
        assert r.returncode == 0, f"build failed: {r.stdout}\n{r.stderr}"
        result_path = base / "output" / "result.json"
        check_result_json(result_path)
        assert (base / "output" / "mechanism.svg").exists()
        assert (base / "output" / "mechanism.pdf").exists()
        assert (base / "output" / "figure_model.json").exists()
        assert (base / "output" / "plan.json").exists()
        parse_svg(base / "output" / "mechanism.svg")
        pdf = (base / "output" / "mechanism.pdf").read_bytes()
        assert pdf.startswith(b"%PDF-"), "PDF header missing"
        assert pdf.rstrip().endswith(b"%%EOF"), "PDF trailer missing"
        model = json.loads((base / "output" / "figure_model.json").read_text(encoding="utf-8"))
        assert len(model["entities"]) == 5
        assert len(model["relations"]) == 4
        # run the contract checker
        r2 = run_main(["--check", "--result", str(result_path)])
        assert r2.returncode == 0, f"check failed: {r2.stdout}\n{r2.stderr}"
        print("  happy path: ok (5 entities, 4 relations, SVG+PDF+model+plan written)")
    return 0


def test_empty_input() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "input").mkdir(parents=True)
        (base / "output").mkdir(parents=True)
        (base / "work").mkdir(parents=True)
        (base / "input" / "case.json").write_text(
            json.dumps({"case_id": "selftest-empty"}), encoding="utf-8")
        r = run_main(["--input", str(base / "input"), "--output", str(base / "output"),
                      "--work", str(base / "work")])
        assert r.returncode == 0, f"empty input should exit 0, got {r.returncode}"
        result = json.loads((base / "output" / "result.json").read_text(encoding="utf-8"))
        assert result.get("status") == "insufficient_evidence", result.get("status")
        print("  empty input: ok (status=insufficient_evidence, exit 0)")
    return 0


def test_bad_json() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        make_case(base, with_plan=False, corrupt_case=True)
        r = run_main(["--input", str(base / "input"), "--output", str(base / "output"),
                      "--work", str(base / "work")])
        result = json.loads((base / "output" / "result.json").read_text(encoding="utf-8"))
        assert result.get("status") in ("insufficient_evidence", "input_error",
                                        "partial", "failed"), result.get("status")
        assert result.get("degradations"), "bad JSON should be recorded"
        print(f"  bad JSON: ok (status={result.get('status')}, degradations recorded)")
    return 0


def test_register_export() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        make_case(base, with_plan=True)
        run_main(["--input", str(base / "input"), "--output", str(base / "output"),
                  "--work", str(base / "work")])
        # Fabricate a tiny valid PNG header + payload just to exercise the path.
        png = base / "output" / "mechanism.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        r = run_main(["--output", str(base / "output"),
                      "--register-export", str(png), "--format", "png",
                      "--width", "1600", "--height", "1200", "--dpi", "200",
                      "--generated-by", "browser-automation"])
        assert r.returncode == 0, f"register-export failed: {r.stdout}\n{r.stderr}"
        result = json.loads((base / "output" / "result.json").read_text(encoding="utf-8"))
        fmts = {a.get("format") for a in result.get("export_artifacts", [])}
        assert "png" in fmts, "PNG artifact not registered"
        print("  register-export: ok (PNG artifact registered)")
    return 0


def main() -> int:
    print("selftest: biological-mechanism/pathway figure skill")
    failed = 0
    for name, fn in [
        ("happy path", test_happy_path),
        ("empty input", test_empty_input),
        ("bad JSON", test_bad_json),
        ("register-export", test_register_export),
    ]:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  {name}: FAILED -- {exc}")
    print(f"selftest: {'PASS' if failed == 0 else 'FAIL'} ({failed} failures)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
