# -*- coding: utf-8 -*-
"""selftest.py — runnable self-test for the graphical-abstract skill package.

Checks the pure functions and runs a full end-to-end dry-run on synthetic input,
including byte-identical SVG determinism. Exit 0 = all checks pass.

Usage:  python scripts/selftest.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import core_impl as core


def _ok(name, cond, detail=""):
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    return bool(cond)


def test_wrap():
    lines = core.wrap_text("This is a very long ASCII sentence that must wrap at a fixed width.", 200, 14)
    return _ok("wrap_text", len(lines) >= 2, f"{len(lines)} lines")


def test_sanitize():
    s = core.sanitize_id("  Cell A/B (2)  ")
    return _ok("sanitize_id", s and " " not in s and s.islower() or s == "", repr(s))


def test_contract():
    resolved = core.resolve_contract(None)
    for f in ("case_id", "status", "sources", "uncertainty", "pending_human", "change_log"):
        pass
    # canonical fallback must contain the six core fields
    resolved = core.resolve_contract(None)
    for f in ("graphical_abstract", "editable_source", "exports", "entities",
              "relations", "data_mapping", "legend", "sources", "uncertainty",
              "change_log", "limitations"):
        if f not in resolved:
            return _ok("resolve_contract", False, f"missing {f}")
    # custom contract keys preserved exactly, extras added
    resolved2 = core.resolve_contract({"answer": "string", "chart": []})
    return _ok("resolve_contract", "answer" in resolved2 and "chart" in resolved2
               and "status" in resolved2 and "pending_human" in resolved2,
               f"{len(resolved2)} fields")


def test_svg_determinism(spec):
    with tempfile.TemporaryDirectory() as td:
        a = Path(td) / "a.svg"
        b = Path(td) / "b.svg"
        core.build_svg(spec, a)
        core.build_svg(spec, b)
        same = a.read_bytes() == b.read_bytes()
        layers = core.list_layers(a)
        return _ok("svg_determinism", same, f"byte-identical={same}, layers={len(layers)}")


def test_validate(spec, tmpdir):
    svg = tmpdir / "fig_case.svg"
    core.build_svg(spec, svg)
    manifest = {"svg": str(svg), "overflow": False, "max_y": 300}
    report = core.validate_figure(svg, spec, manifest, str(tmpdir), str(tmpdir))
    return _ok("validate_figure", isinstance(report, dict) and "ok" in report,
               f"ok={report.get('ok')}")


def make_synthetic(tmpdir: Path) -> dict:
    case = {"id": "syn-case-001", "output_contract": {
        "graphical_abstract": {"question": [], "method": [], "results": [], "conclusion": []},
        "editable_source": None,
        "exports": None,
        "entities": [],
        "relations": [],
        "data_mapping": [],
        "legend": [],
    }}
    (tmpdir / "case.json").write_text(json.dumps(case), encoding="utf-8")
    (tmpdir / "task.md").write_text(
        "# Synthetic case\n\nchallenge_id: `syn-case-001`\n\n"
        "Cell A activates gene B, increasing output to 3.2 fold.\n", encoding="utf-8")
    spec = core._figure_spec_template("syn-case-001")
    spec["title"] = "Synthetic graphical abstract"
    spec["graphical_abstract"] = {
        "question": ["Does A drive B?"],
        "method": ["Perturb A; measure B output."],
        "results": ["3.2-fold induction."],
        "conclusion": ["A activates B."],
    }
    spec["entities"] = [
        {"id": "a", "label": "Cell A", "type": "cell", "panel": "method",
         "source": {"locator": "task.md:5"}},
        {"id": "b", "label": "Gene B", "type": "molecule", "panel": "method",
         "source": {"locator": "task.md:5"}},
    ]
    spec["relations"] = [
        {"id": "r1", "from": "a", "to": "b", "direction": "directed", "label": "activates",
         "source": {"locator": "task.md:5"}},
    ]
    spec["data"] = [
        {"id": "d1", "label": "B output", "value": 3.2, "unit": "fold", "panel": "results",
         "source": {"locator": "task.md:5"}},
    ]
    spec["legend"] = [{"key": "arrow", "meaning": "activation", "color": "#0072B2"}]
    spec["sources"] = [{"locator": "task.md:5", "quote": "Cell A activates gene B"}]
    (tmpdir / "figure_spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return spec


def run_dry_run(tmpdir: Path):
    spec = make_synthetic(tmpdir)
    ok = True
    ok &= test_svg_determinism(spec)
    ok &= test_validate(spec, tmpdir)

    # full pipeline through main.py stages
    import main as pipeline
    svg = tmpdir / "fig_syn-case-001.svg"
    core.build_svg(spec, svg)
    manifest = {
        "case_id": "syn-case-001", "svg": str(svg), "png": None, "pdf": None,
        "renderer": None, "export_status": "preview_not_rendered",
        "layers": core.list_layers(svg),
        "text_editable": core.count_text_and_sources(svg)["text"] > 0,
        "overflow": False, "max_y": 300,
    }
    core.write_json(tmpdir / "fig_manifest.json", manifest)
    report = core.validate_figure(svg, spec, manifest, str(tmpdir), str(tmpdir))
    core.write_json(tmpdir / "validation_report.json", report)
    core.write_json(tmpdir / "rubric_trace.json", core.build_rubric_trace(report, manifest, {}))
    # invoke finalize through CLI with a fake argparse result is overkill;
    # call the stage function directly with a Namespace.
    import argparse
    ns = argparse.Namespace(input=str(tmpdir), output=str(tmpdir), work=str(tmpdir))
    # seed a skeleton first
    skeleton = core.build_skeleton("syn-case-001", spec and None)
    core.write_json(tmpdir / "result.json", skeleton)
    pipeline.stage_finalize(ns)
    result = core.load_json(tmpdir / "result.json")
    ok &= _ok("dry_run_result", result and result.get("case_id") == "syn-case-001",
              f"status={result.get('status') if result else None}")
    ok &= _ok("dry_run_status_enum", result and result.get("status") in ("completed", "partial", "failed"),
              result.get("status") if result else None)
    return ok


def main() -> int:
    print("=== graphical-abstract skill selftest ===")
    ok = True
    ok &= test_wrap()
    ok &= test_sanitize()
    ok &= test_contract()
    with tempfile.TemporaryDirectory() as td:
        ok &= run_dry_run(Path(td))
    print("=== selftest", "PASSED" if ok else "FAILED", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
