"""selftest.py — deterministic self-check for the sci-plan-comparison skill.

Run from anywhere; it locates scripts/ relative to this file, drives main.py
end-to-end on a built-in example, runs every case twice and requires
byte-identical outputs, then exercises empty/bad/missing inputs. Exits 0 only
when every check passes. No third-party dependencies.
"""

import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
SKILL_MD = os.path.abspath(os.path.join(HERE, "..", "SKILL.md"))

CHECKS = []


def check(name, fn):
    try:
        fn()
        CHECKS.append((name, True, ""))
        print("[PASS] %s" % name)
    except Exception as exc:
        CHECKS.append((name, False, str(exc)))
        print("[FAIL] %s -> %s" % (name, exc))


def run_main(*args):
    proc = subprocess.run([sys.executable, "-B", MAIN] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=120)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def write_demo_input(base):
    din = os.path.join(base, "input")
    os.makedirs(din, exist_ok=True)
    case = {
        "case_id": "selftest-case-001",
        "output_contract": {"experimental_plan": None, "evidence_and_risks": None,
                            "confirmation_items": None},
        "candidates": [
            {"plan_id": "P1", "name": "Selftest plan A"},
            {"plan_id": "P2", "name": "Selftest plan B"},
        ],
    }
    with open(os.path.join(din, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False, indent=2, sort_keys=True)
    with open(os.path.join(din, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# Selftest task (example only)\n")
    with open(os.path.join(din, "quote.md"), "w", encoding="utf-8") as fh:
        fh.write("## p1\n\nselftest evidence line\n")
    return din


def write_analysis(work):
    analysis = {
        "case_id": "selftest-case-001",
        "research_question": {"testable_question": "selftest",
                              "variables": {"independent": [], "dependent": [], "controlled": []},
                              "hypothesis": "selftest"},
        "comparison_frame": {"objectives": ["selftest"],
                             "hard_constraints": ["budget <= 1000 CNY", "max_time <= 40 h"],
                             "criteria": ["goal_fit", "risk", "cost", "feasibility", "evidence"]},
        "weights": {"goal_fit": 0.25, "feasibility": 0.25, "evidence": 0.20,
                    "cost": 0.15, "risk": 0.15},
        "candidates": [
            {"plan_id": "P1", "name": "Selftest plan A", "version": "v1",
             "seven_fields": {"assumptions": "x", "variables": "x", "controls": "x",
                              "steps": "x", "parameters": "x", "resources": "x",
                              "interpretation_criteria": "x"},
             "dimension_ratings": {"goal_fit": 4, "risk": 3, "cost": 3,
                                   "feasibility": 4, "evidence": 3},
             "feasibility": {"equipment_available": "yes", "lead_time_ok": "yes"},
             "risks": [{"trigger": "x", "consequence": "x", "magnitude": "low",
                        "mitigation": "x", "fallback": "x"}],
             "evidence": [{"claim": "x", "locator": "quote.md#p1", "quote": "x", "kind": "manual"}],
             "cost_override": {"total": 600, "currency": "CNY", "lines": [], "note": "selftest"},
             "time_override": {"total": 30, "unit": "h", "parts": {}, "computed": True}},
            {"plan_id": "P2", "name": "Selftest plan B", "version": "v1",
             "seven_fields": {"assumptions": "x", "variables": "x", "controls": "x",
                              "steps": "x", "parameters": "x", "resources": "x",
                              "interpretation_criteria": "x"},
             "dimension_ratings": {"goal_fit": 3, "risk": 4, "cost": 4,
                                   "feasibility": 3, "evidence": 2},
             "feasibility": {"equipment_available": "yes", "lead_time_ok": "yes"},
             "risks": [{"trigger": "x", "consequence": "x", "magnitude": "low",
                        "mitigation": "x", "fallback": "x"}],
             "evidence": [{"claim": "x", "locator": "quote.md#p1", "quote": "x", "kind": "manual"}],
             "cost_override": {"total": 800, "currency": "CNY", "lines": [], "note": "selftest"},
             "time_override": {"total": 50, "unit": "h", "parts": {}, "computed": True}},
        ],
        "selection": {"chosen": "P1", "ranking": ["P1", "P2"], "rationale": "selftest",
                      "rejected": [{"plan_id": "P2", "reason": "selftest"}], "sensitivity": {}},
        "confirmations": [{"item": "selftest", "type": "safety", "status": "confirmed", "note": "selftest"}],
        "uncertainty": [], "sources": [], "contract_fills": {}, "change_log": [],
        "errors": [], "degradations": [], "pending_human": [],
    }
    with open(os.path.join(work, "analysis.json"), "w", encoding="utf-8") as fh:
        json.dump(analysis, fh, ensure_ascii=False, indent=2, sort_keys=True)


def read_json(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def main():
    tmp = tempfile.mkdtemp(prefix="sciplan-selftest-")

    def check_stdlib():
        for fn in os.listdir(HERE):
            if not fn.endswith(".py"):
                continue
            with open(os.path.join(HERE, fn), "r", encoding="utf-8") as fh:
                src = fh.read()
            for line in src.splitlines():
                line = line.strip()
                if line.startswith("import ") or line.startswith("from "):
                    mod = line.split()[1].split(".")[0]
                    assert mod in ("os", "sys", "json", "hashlib", "re", "csv",
                                   "xml", "runpy", "subprocess", "tempfile"), \
                        "third-party import %r in %s" % (mod, fn)
    check("scripts use standard library only", check_stdlib)

    def check_help():
        rc, out = run_main("--help")
        assert rc == 0, "main.py --help exit %d" % rc
        for sub in ("inspect", "assemble", "validate", "demo"):
            assert sub in out, "missing subcommand %s in help" % sub
    check("main.py --help lists all four subcommands", check_help)

    def check_frontmatter():
        assert os.path.exists(SKILL_MD), "SKILL.md not found"
        with open(SKILL_MD, "r", encoding="utf-8") as fh:
            text = fh.read()
        assert text.startswith("---"), "SKILL.md missing YAML frontmatter"
        for key in ("name:", "description:", "version:"):
            assert key in text, "frontmatter missing %s" % key
    check("SKILL.md frontmatter has name/description/version", check_frontmatter)

    # End-to-end determinism: inspect+assemble twice -> byte-identical.
    din = write_demo_input(tmp)
    out1 = os.path.join(tmp, "out1")
    out2 = os.path.join(tmp, "out2")
    work1 = os.path.join(tmp, "work1")
    work2 = os.path.join(tmp, "work2")

    def check_run1():
        rc, _ = run_main("inspect", "--input", din, "--output", out1, "--work", work1)
        assert rc == 0, "inspect run1 exit %d" % rc
        write_analysis(work1)
        rc, _ = run_main("assemble", "--input", din, "--output", out1, "--work", work1)
        assert rc == 0, "assemble run1 exit %d" % rc
    check("inspect + assemble run 1 succeeds", check_run1)

    def check_run2():
        rc, _ = run_main("inspect", "--input", din, "--output", out2, "--work", work2)
        assert rc == 0, "inspect run2 exit %d" % rc
        write_analysis(work2)
        rc, _ = run_main("assemble", "--input", din, "--output", out2, "--work", work2)
        assert rc == 0, "assemble run2 exit %d" % rc
    check("inspect + assemble run 2 succeeds", check_run2)

    def check_determinism():
        for name in ("result.json", "comparison_matrix.svg", "comparison_matrix.csv",
                     "plan_comparison.md", "comparison_matrix.pdf"):
            a = os.path.join(out1, name)
            b = os.path.join(out2, name)
            assert os.path.exists(a) and os.path.exists(b), "missing %s" % name
            with open(a, "rb") as fh:
                ba = fh.read()
            with open(b, "rb") as fh:
                bb = fh.read()
            assert ba == bb, "%s differs between identical runs" % name
    check("byte-identical outputs across two runs", check_determinism)

    def check_result_shape():
        r = read_json(os.path.join(out1, "result.json"))
        assert r["case_id"] == "selftest-case-001"
        assert r["status"] == "ok", "status %r" % r["status"]
        for key in ("sources", "uncertainty", "change_log", "pending_human",
                    "errors", "degradations", "runtime", "artifacts"):
            assert key in r, "result.json missing %s" % key
        for c in r["candidates"]:
            for f in ("assumptions", "variables", "controls", "steps", "parameters",
                      "resources", "interpretation_criteria"):
                assert f in c["seven_fields"], "missing seven_fields.%s" % f
        assert r["selection"]["chosen"] == "P1"
        assert "experimental_plan" in r and "evidence_and_risks" in r \
            and "confirmation_items" in r
    check("result.json contract shape", check_result_shape)

    def check_artifacts():
        svg = os.path.join(out1, "comparison_matrix.svg")
        ET.parse(svg)
        with open(os.path.join(out1, "comparison_matrix.csv"), "rb") as fh:
            assert fh.read(3) == b"\xef\xbb\xbf", "CSV missing BOM"
        with open(os.path.join(out1, "comparison_matrix.pdf"), "rb") as fh:
            raw = fh.read()
        assert raw.startswith(b"%PDF-") and raw.rstrip().endswith(b"%%EOF"), "PDF bad"
        assert os.path.getsize(os.path.join(out1, "plan_comparison.md")) > 0
    check("SVG parses, CSV has BOM, PDF well-formed, MD non-empty", check_artifacts)

    def check_validate_ok():
        rc, out = run_main("validate", os.path.join(out1, "result.json"))
        assert rc == 0, "validate exit %d: %s" % (rc, out)
        assert json.loads(out).get("valid") is True
    check("validate returns valid=true", check_validate_ok)

    # Edge cases.
    def check_empty():
        empty = os.path.join(tmp, "empty")
        os.makedirs(empty, exist_ok=True)
        eout = os.path.join(tmp, "empty_out")
        ework = os.path.join(tmp, "empty_work")
        rc, _ = run_main("inspect", "--input", empty, "--output", eout, "--work", ework)
        assert rc == 0, "empty input exit %d" % rc
        r = read_json(os.path.join(eout, "result.json"))
        assert r["status"] == "input_error"
    check("empty input -> exit 0, status=input_error", check_empty)

    def check_bad_json():
        bad = os.path.join(tmp, "bad")
        os.makedirs(bad, exist_ok=True)
        with open(os.path.join(bad, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ this is not json")
        with open(os.path.join(bad, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# task\n")
        bout = os.path.join(tmp, "bad_out")
        bwork = os.path.join(tmp, "bad_work")
        rc, _ = run_main("inspect", "--input", bad, "--output", bout, "--work", bwork)
        assert rc == 0
        r = read_json(os.path.join(bout, "result.json"))
        assert r["status"] == "input_error"
        assert r["errors"], "bad JSON should record errors"
    check("bad JSON -> exit 0, errors recorded", check_bad_json)

    def check_demo():
        dout = os.path.join(tmp, "demo_out")
        dwork = os.path.join(tmp, "demo_work")
        rc, out = run_main("demo", "--output", dout, "--work", dwork)
        assert rc == 0, "demo exit %d: %s" % (rc, out)
        r = read_json(os.path.join(dout, "demo", "result.json"))
        assert r["status"] in ("ok", "partial")
    check("demo command produces example outputs", check_demo)

    print("\n%d/%d checks passed" % (sum(1 for _, ok, _ in CHECKS if ok), len(CHECKS)))
    return 0 if all(ok for _, ok, _ in CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
