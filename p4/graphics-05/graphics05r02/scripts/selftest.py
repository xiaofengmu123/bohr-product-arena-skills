"""Self-test for the graphical-abstract Skill (standard library only).

Runs the pipeline in an isolated temporary directory on synthetic demo input
(clearly labelled as example, never pretending to be a scored case), then
verifies the structural contract: result.json skeleton, case_id, allowed
status, well-formed layered SVG, and the empty-input / bad-JSON degradation
paths. Exit code 0 = all checks pass.
"""

import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import core_impl as ci  # noqa: E402

SVG_NS = "http://www.w3.org/2000/svg"

DEMO_CASE = {
    "case_id": "demo-graphical-abstract-001",
    "title": "Demo: sodium channel gating (example, not a scored case)",
    "output_contract": {
        "graphical_abstract": "object",
        "editable_source": "object",
        "exports": "array",
        "entities": "array",
        "relations": "array",
        "data_mapping": "array",
        "legend": "array",
    },
    "materials": ["demo only"],
}

DEMO_TASK = """# Graphical Abstract

- challenge_id: `demo-graphical-abstract-001`
## 题目说明
Generate a single-page graphical abstract with question, method, results,
conclusion, and keep an editable source. Example input only.
"""

DEMO_SPEC_EXTRA = {
    "panels": {
        "question": {"title": "Question",
                     "summary": "How does voltage gate the channel?",
                     "source": {"locator": "demo.md#p1", "quote": "voltage gates"}},
        "method": {"title": "Method",
                   "summary": "Patch-clamp recording of whole-cell current.",
                   "source": {"locator": "demo.md#p2", "quote": "patch-clamp"}},
        "results": {"title": "Results",
                    "summary": "Peak current increases with depolarization.",
                    "source": {"locator": "demo.md#p3", "quote": "peak current"}},
        "conclusion": {"title": "Conclusion",
                       "summary": "Gating is voltage dependent.",
                       "source": {"locator": "demo.md#p4", "quote": "voltage dependent"}},
    },
    "entities": [
        {"id": "E1", "label": "Na+ channel", "kind": "method",
         "panel": "method", "source": {"locator": "demo.md#p2", "quote": "channel"}},
        {"id": "E2", "label": "Membrane voltage", "kind": "question",
         "panel": "question", "source": {"locator": "demo.md#p1", "quote": "voltage"}},
    ],
    "relations": [
        {"id": "R1", "from": "E2", "to": "E1", "type": "activates",
         "direction": "forward", "label": "depolarizes",
         "source": {"locator": "demo.md#p3", "quote": "depolarization"}},
    ],
    "charts": [
        {"id": "D1", "kind": "bar", "panel": "results",
         "values": [{"label": "I_peak", "value": 1.0, "unit": "nA"}],
         "source": {"locator": "demo.md#p3", "quote": "peak current"}},
    ],
    "legend": [
        {"label": "activation", "color": "#117733",
         "source": {"locator": "demo.md#p3", "quote": "activates"}},
    ],
}


def run(argv, cwd):
    proc = subprocess.run([sys.executable, "-B", "main.py"] + argv, cwd=cwd,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True)
    return proc


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


passed = 0
failed = 0


def test_happy_path(root):
    inp = os.path.join(root, "input")
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    os.makedirs(inp)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(DEMO_CASE, fh, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
        fh.write(DEMO_TASK)

    proc = run(["--phase", "skeleton", "--input", inp, "--output", out,
                "--work", work], SCRIPT_DIR)
    check("skeleton exit 0", proc.returncode == 0, proc.stderr.strip())

    # Agent edits the spec between skeleton and build (as SKILL.md instructs).
    spec_path = os.path.join(work, "figure_spec.json")
    spec, err = ci.load_json_robust(spec_path)
    check("spec template exists", err is None and isinstance(spec, dict))
    for key, val in DEMO_SPEC_EXTRA.items():
        spec[key] = val
    ci.write_json(spec_path, spec)

    proc = run(["--phase", "build", "--input", inp, "--output", out,
                "--work", work], SCRIPT_DIR)
    check("build exit 0", proc.returncode == 0, proc.stderr.strip())

    proc = run(["--phase", "validate", "--input", inp, "--output", out,
                "--work", work], SCRIPT_DIR)
    check("validate exit 0", proc.returncode == 0, proc.stderr.strip())

    proc = run(["--phase", "finalize", "--input", inp, "--output", out,
                "--work", work], SCRIPT_DIR)
    check("finalize exit 0", proc.returncode == 0, proc.stderr.strip())

    result_path = os.path.join(out, "result.json")
    result, err = ci.load_json_robust(result_path)
    check("result.json exists", err is None and isinstance(result, dict), str(err))
    if err is None and isinstance(result, dict):
        check("case_id matches", result.get("case_id") == DEMO_CASE["case_id"])
        check("status allowed", result.get("status") in ci.STATUS_VALUES)
        check("contract fields echoed",
              all(k in result for k in DEMO_CASE["output_contract"]))
        check("sources/uncertainty/change_log present",
              all(k in result for k in ("sources", "uncertainty", "change_log")))

    svg_path = os.path.join(out, f"fig_{DEMO_CASE['case_id']}.svg")
    check("SVG exists", os.path.isfile(svg_path))
    if os.path.isfile(svg_path):
        try:
            tree = ET.parse(svg_path)
            root = tree.getroot()
            check("SVG parses", root.tag == f"{{{SVG_NS}}}svg")
            ids = [g.get("id") for g in root if g.get("id", "").startswith("layer-")]
            check("SVG layered", len(ids) >= 3, str(ids))
            texts = list(root.iter(f"{{{SVG_NS}}}text"))
            check("SVG editable text", len(texts) > 0)
        except ET.ParseError as exc:
            check("SVG parses", False, str(exc))

    ledger_path = os.path.join(work, "stage_ledger.json")
    ledger, _ = ci.load_json_robust(ledger_path)
    check("ledger written", isinstance(ledger, dict) and ledger.get("phases"))
    if isinstance(ledger, dict):
        check("ledger has plan", len(ledger.get("plan", [])) >= 6)


def test_empty_input(root):
    inp = os.path.join(root, "empty_in")
    out = os.path.join(root, "empty_out")
    work = os.path.join(root, "empty_work")
    os.makedirs(inp)
    proc = run(["--phase", "skeleton", "--input", inp, "--output", out,
                "--work", work], SCRIPT_DIR)
    check("empty-input exit 0", proc.returncode == 0, proc.stderr.strip())
    result, err = ci.load_json_robust(os.path.join(out, "result.json"))
    check("empty-input honest status",
          err is None and isinstance(result, dict)
          and result.get("status") == "failed"
          and isinstance(result.get("limitations"), list))


def test_bad_json(root):
    inp = os.path.join(root, "bad_in")
    out = os.path.join(root, "bad_out")
    work = os.path.join(root, "bad_work")
    os.makedirs(inp)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{ this is not json")
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# bad case\n")
    proc = run(["--phase", "skeleton", "--input", inp, "--output", out,
                "--work", work], SCRIPT_DIR)
    check("bad-JSON exit 0", proc.returncode == 0, proc.stderr.strip())
    result, err = ci.load_json_robust(os.path.join(out, "result.json"))
    check("bad-JSON degraded", err is None and isinstance(result, dict)
          and result.get("status") in ("failed", "partial")
          and result.get("errors"))


def main():
    print("graphical-abstract selftest (example inputs only)")
    root = tempfile.mkdtemp(prefix="ga_selftest_")
    try:
        test_happy_path(root)
        test_empty_input(root)
        test_bad_json(root)
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)

    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
