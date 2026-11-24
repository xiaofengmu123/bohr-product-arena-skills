"""Local self-test for the graphical-abstract Skill package.

Runs the actual scripts/main.py pipeline on synthetic, clearly-marked example inputs
under a temp dir, then checks:
  - init -> build -> validate -> finalize exit cleanly,
  - result.json has case_id + status and parses,
  - the SVG parses as XML and is byte-identical across two builds (determinism),
  - empty-input and bad-JSON cases degrade to an honest result.json without crash.

Synthetic inputs are examples only, not formal test cases or answers.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAIN = os.path.join(HERE, "main.py")


def run_py(*args, cwd=ROOT):
    return subprocess.run(
        [sys.executable, "-B", MAIN, *args], cwd=cwd,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def make_case(base):
    os.makedirs(os.path.join(base, "input"), exist_ok=True)
    with open(os.path.join(base, "input", "case.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "case_id": "synthetic-001",
            "title": "Synthetic graphical abstract",
            "output_contract": {"graphical_abstract": {}, "editable_source": {}},
            "note": "synthetic self-test input, not a formal case",
        }, fh, ensure_ascii=False)
    with open(os.path.join(base, "input", "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# Synthetic task\n\nProduce a graphical abstract (self-test example).\n")


def enrich_spec(work):
    path = os.path.join(work, "figure_spec.json")
    with open(path, "r", encoding="utf-8") as fh:
        spec = json.load(fh)
    spec["abstract"] = {
        "question": "Q: synthetic question",
        "method": "M: synthetic method",
        "results": "R: synthetic result",
        "conclusion": "C: synthetic conclusion",
    }
    spec["elements"] = [
        {"id": "panel-q", "type": "panel", "label": "问题"},
        {"id": "e1", "type": "box", "panel": "panel-q", "text": "研究对象",
         "source": {"locator": "input/task.md#L1", "quote": "Produce a graphical abstract"}},
        {"id": "e2", "type": "box", "panel": "panel-q", "text": "核心结果",
         "source": {"locator": "input/task.md#L1", "quote": "Produce a graphical abstract"}},
        {"id": "r1", "type": "arrow", "panel": "panel-q", "from": "e1", "to": "e2",
         "directed": True, "label": "推导", "source": {"locator": "input/task.md#L1"}},
        {"id": "d1", "type": "bar", "panel": "panel-q", "label": "效应量", "value": 42.0,
         "max": 100, "unit": "%", "source": {"locator": "input/task.md#L1"}},
    ]
    spec["sources"] = [{"locator": "input/task.md#L1",
                        "quote": "Produce a graphical abstract (self-test example)."}]
    spec["uncertainty"] = [{"item": "synthetic", "note": "示例输入，非正式案例"}]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, ensure_ascii=False, indent=2)


def check(base):
    out = os.path.join(base, "output")
    result = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    assert result.get("case_id") == "synthetic-001", result
    assert result.get("status") in ("completed", "partial", "failed"), result
    assert "sources" in result and "change_log" in result, result

    svg_path = os.path.join(out, "fig_synthetic-001.svg")
    assert os.path.isfile(svg_path), "SVG missing"
    ET.parse(svg_path)
    with open(svg_path, "r", encoding="utf-8") as fh:
        first = fh.read()
    run_py("build", "--input", os.path.join(base, "input"),
           "--work", os.path.join(base, "work"), "--output", out)
    with open(svg_path, "r", encoding="utf-8") as fh:
        second = fh.read()
    assert first == second, "SVG not deterministic"
    return result["status"]


def main():
    base = tempfile.mkdtemp(prefix="skill-selftest-")
    try:
        make_case(base)
        rc = run_py("init", "--input", os.path.join(base, "input"),
                    "--work", os.path.join(base, "work"), "--output", os.path.join(base, "output"))
        assert rc.returncode == 0, f"init failed rc={rc.returncode}"
        enrich_spec(os.path.join(base, "work"))
        assert run_py("build", "--input", os.path.join(base, "input"),
                      "--work", os.path.join(base, "work"),
                      "--output", os.path.join(base, "output")).returncode == 0
        assert run_py("validate", "--input", os.path.join(base, "input"),
                      "--work", os.path.join(base, "work"),
                      "--output", os.path.join(base, "output")).returncode == 0
        assert run_py("finalize", "--input", os.path.join(base, "input"),
                      "--work", os.path.join(base, "work"),
                      "--output", os.path.join(base, "output")).returncode == 0
        status = check(base)
        print(f"ok  happy-path status={status}")

        # empty input dir -> honest failed/partial result, no crash
        empty = tempfile.mkdtemp(prefix="skill-empty-")
        rc = run_py("run", "--input", empty, "--work", os.path.join(empty, "work"),
                    "--output", os.path.join(empty, "output"))
        r = json.load(open(os.path.join(empty, "output", "result.json"), encoding="utf-8"))
        assert r.get("status") in ("failed", "partial"), r
        print(f"ok  empty-input status={r['status']} rc={rc.returncode}")

        # bad JSON -> degrades without crash
        bad = tempfile.mkdtemp(prefix="skill-bad-")
        os.makedirs(os.path.join(bad, "input"), exist_ok=True)
        with open(os.path.join(bad, "input", "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ this is not valid json")
        run_py("run", "--input", os.path.join(bad, "input"),
               "--work", os.path.join(bad, "work"), "--output", os.path.join(bad, "output"))
        rb = json.load(open(os.path.join(bad, "output", "result.json"), encoding="utf-8"))
        assert rb.get("status") == "failed", rb
        print(f"ok  bad-json status={rb['status']}")
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    main()
