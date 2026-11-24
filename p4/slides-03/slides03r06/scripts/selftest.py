#!/usr/bin/env python3
"""Self-test for the academic-talk-organizer Skill package.

Runs the deterministic pipeline end-to-end on synthetic in-memory inputs and
asserts the behaviours required by the checklist:

1. scaffold writes a valid partial result.json + plan.json + figure_model.json;
2. update merges the intermediate representations into a complete result;
3. validate --strict passes a complete result and flags incomplete provenance;
4. empty input dir returns ``insufficient_evidence`` without raising;
5. bad case.json returns ``input_error`` without raising;
6. result.json is deterministic JSON with the required meta fields.

Only the Python standard library is used. Exit code 0 means all checks passed.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402

CHECKS = []


def check(name, fn):
    try:
        fn()
        CHECKS.append((name, True, ""))
    except Exception as exc:  # noqa: BLE001 - report and continue
        CHECKS.append((name, False, "%s: %s" % (type(exc).__name__, exc)))


def make_case(case_id="demo-case-1"):
    return {
        "id": case_id,
        "task": "academic-talk-organizer",
        "output_contract": {
            "report_goal": None,
            "audience": None,
            "narrative": None,
            "pages": None,
            "editable_deck": None,
            "evidence_map": None,
            "qa_prep": None,
            "rendering": None,
        },
    }


def make_plan(case_id):
    return {
        "case_id": case_id,
        "report_goal": {
            "goal": "让评委认可方法的可复现性",
            "audience": "同行评审",
            "duration_min": 15,
            "page_cap": 12,
        },
        "audience": {
            "prior": "熟悉统计方法，不熟悉本领域生物材料",
            "concerns": ["样本量", "对照设计"],
            "term_depth": "术语只解释一次",
        },
        "narrative": {"arc": ["问题", "方法", "结果", "结论"], "tradeoffs": ["略去机制推测页"]},
        "pages": [
            {
                "page_index": 1,
                "title": "研究问题",
                "single_claim": "现有方法无法在低样本量下稳定估计效应量。",
                "evidence_strength": "medium",
                "figures": [],
                "speaker_notes": "先讲缺口，再讲本文方法。",
                "source_refs": [
                    {"kind": "text", "locator": "paper.pdf#p1",
                     "quote": "existing estimators degrade sharply below n=30."}
                ],
            },
        ],
        "qa_prep": [
            {"category": "method", "question": "为何选择该估计量？",
             "answer": "因为其在有限样本下偏差更小。", "page_index": 2},
        ],
    }


def make_figures(case_id):
    return {
        "case_id": case_id,
        "figures": [
            {
                "id": "fig1",
                "kind": "figure",
                "locator": "paper.pdf#p3 Figure 1",
                "quote": "Figure 1 shows mean squared error decreasing with n.",
                "extracted_numbers": [{"label": "n", "value": "30", "unit": None}],
                "caption": "MSE versus sample size.",
                "availability": "ok",
                "pages_used": [1],
                "notes": "",
            },
        ],
        "unresolved": [],
    }


def test_scaffold_and_update():
    with tempfile.TemporaryDirectory() as tmp:
        inp, work, out = (os.path.join(tmp, d) for d in ("input", "work", "output"))
        os.makedirs(inp)
        os.makedirs(work)
        os.makedirs(out)
        case_path = os.path.join(inp, "case.json")
        task_path = os.path.join(inp, "task.md")
        with open(case_path, "w", encoding="utf-8") as f:
            json.dump(make_case(), f)
        with open(task_path, "w", encoding="utf-8") as f:
            f.write("# demo task\n")
        with open(os.path.join(inp, "paper.pdf"), "wb") as f:
            f.write(b"%PDF-1.4 fake")

        result, ok = core_impl.build_scaffold(case_path, task_path, work, out)
        assert ok and result["case_id"] == "demo-case-1"
        assert result["status"] == "partial"
        rpath = os.path.join(out, "result.json")
        assert os.path.exists(rpath)
        parsed, err = core_impl.read_json(rpath)
        assert err is None and parsed["case_id"] == "demo-case-1"
        assert os.path.exists(os.path.join(work, "plan.json"))
        assert os.path.exists(os.path.join(work, "figure_model.json"))

        core_impl.write_json(os.path.join(work, "plan.json"), make_plan("demo-case-1"))
        core_impl.write_json(os.path.join(work, "figure_model.json"), make_figures("demo-case-1"))
        errors, warnings, merged = core_impl.merge(rpath,
                                                   os.path.join(work, "plan.json"),
                                                   os.path.join(work, "figure_model.json"))
        assert not errors, errors
        assert merged["status"] == "complete"
        assert len(merged["evidence_map"]) >= 2

        errors, warnings, ok = core_impl.validate(rpath, work, out, strict=True)
        assert ok, (errors, warnings)


def test_strict_flags_incomplete():
    with tempfile.TemporaryDirectory() as tmp:
        inp, work, out = (os.path.join(tmp, d) for d in ("input", "work", "output"))
        os.makedirs(inp)
        os.makedirs(work)
        os.makedirs(out)
        case_path = os.path.join(inp, "case.json")
        with open(case_path, "w", encoding="utf-8") as f:
            json.dump(make_case(), f)
        result, _ = core_impl.build_scaffold(case_path, os.path.join(inp, "task.md"), work, out)
        rpath = os.path.join(out, "result.json")
        # inject a claim without provenance, then strict validation must fail
        result["pages"] = [{"page_index": 1, "title": "t", "single_claim": "x",
                            "evidence_strength": "high", "source_refs": []}]
        result["status"] = "complete"
        core_impl.write_json(rpath, result)
        errors, warnings, ok = core_impl.validate(rpath, work, out, strict=True)
        assert not ok
        assert any("source_refs" in e for e in errors), errors


def test_empty_input():
    with tempfile.TemporaryDirectory() as tmp:
        inp, work, out = (os.path.join(tmp, d) for d in ("input", "work", "output"))
        os.makedirs(inp)
        os.makedirs(work)
        os.makedirs(out)
        result, ok = core_impl.build_scaffold(os.path.join(inp, "case.json"),
                                              os.path.join(inp, "task.md"), work, out)
        assert ok and result["status"] == "insufficient_evidence"
        parsed, err = core_impl.read_json(os.path.join(out, "result.json"))
        assert err is None and parsed["status"] == "insufficient_evidence"


def test_bad_json():
    with tempfile.TemporaryDirectory() as tmp:
        inp, work, out = (os.path.join(tmp, d) for d in ("input", "work", "output"))
        os.makedirs(inp)
        os.makedirs(work)
        os.makedirs(out)
        case_path = os.path.join(inp, "case.json")
        with open(case_path, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        result, ok = core_impl.build_scaffold(case_path, os.path.join(inp, "task.md"), work, out)
        assert ok and result["status"] == "input_error"
        assert any("case.json" in e for e in result["errors"])


def test_cli_help():
    for cmd in (["--help"], ["scaffold", "--help"], ["validate", "--help"]):
        proc = subprocess.run([sys.executable, os.path.join(HERE, "main.py")] + cmd,
                              capture_output=True, text=True, encoding="utf-8")
        assert proc.returncode == 0, (cmd, proc.stderr)


def main():
    check("scaffold+update+validate", test_scaffold_and_update)
    check("strict flags incomplete", test_strict_flags_incomplete)
    check("empty input -> insufficient_evidence", test_empty_input)
    check("bad json -> input_error", test_bad_json)
    check("main.py --help", test_cli_help)

    failed = [c for c in CHECKS if not c[1]]
    for name, ok, msg in CHECKS:
        print(("PASS" if ok else "FAIL"), name, ("" if ok else ("- " + msg)))
    print("----")
    print("%d/%d checks passed" % (len(CHECKS) - len(failed), len(CHECKS)))
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        prog="selftest.py",
        description="Self-test for academic-talk-organizer (standard library only)")
    ap.parse_args()  # accepts --help; the checks below run otherwise
    sys.exit(main())
