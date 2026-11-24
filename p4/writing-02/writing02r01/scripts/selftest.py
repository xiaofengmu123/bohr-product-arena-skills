# -*- coding: utf-8 -*-
"""Self-test for the academic-polish Skill package.

Runs the full scaffold -> verify -> finalize -> selfcheck pipeline against
synthetic inputs in a temporary directory.  This is the executable proof
that the package works in a clean environment and degrades gracefully on
empty input, malformed JSON and missing material.  Standard library only;
deterministic; no network.

Exit code 0 means all assertions passed.
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import main  # noqa: E402

DRAFT = (
    "Background: We measured serum albumin at 35.2 g/L in 12 patients. "
    "The treatment may reduce mortality [1,2] but did not reach "
    "significance (Fig. 1). These results suggest a possible benefit."
)

CASE = {
    "case_id": "demo-001",
    "output_contract": {
        "deliverable": None,
        "fact_source_map": [],
        "version_boundary": [],
    },
    "text": DRAFT,
}


def run_pipeline(input_dir, work, out):
    for cmd in ("scaffold", "verify", "finalize", "selfcheck"):
        rc = main.main(["--input", input_dir, "--output", out,
                        "--work", work, cmd])
        assert rc == 0, "command %s failed with rc=%d" % (cmd, rc)
    return main.read_json(os.path.join(out, "result.json"))


def test_empty_input():
    root = tempfile.mkdtemp(prefix="polish-selftest-empty-")
    try:
        inp = os.path.join(root, "input")
        os.makedirs(inp)
        work = os.path.join(root, "work")
        out = os.path.join(root, "output")
        result = run_pipeline(inp, work, out)
        assert result["status"] == "insufficient_evidence", result["status"]
        assert result["case_id"] is None
        print("[ok] empty input -> insufficient_evidence")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_bad_json():
    root = tempfile.mkdtemp(prefix="polish-selftest-badjson-")
    try:
        inp = os.path.join(root, "input")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            f.write("{not valid json")
        work = os.path.join(root, "work")
        out = os.path.join(root, "output")
        result = run_pipeline(inp, work, out)
        # must not crash; status honest; result.json exists and parses
        assert result is not None
        assert result.get("status") in {
            "partial", "insufficient_evidence", "input_error"}
        print("[ok] bad JSON -> degrades without crash (%s)" % result["status"])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_minimal_draft():
    root = tempfile.mkdtemp(prefix="polish-selftest-draft-")
    try:
        inp = os.path.join(root, "input")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            json.dump(CASE, f, ensure_ascii=False)
        work = os.path.join(root, "work")
        out = os.path.join(root, "output")

        # scaffold must write the early partial skeleton + evidence index
        assert main.main(["--input", inp, "--output", out, "--work", work,
                          "scaffold"]) == 0
        edits = main.read_json(os.path.join(work, "edits.json"))
        assert len(edits["units"]) >= 3, "expected >=3 sentence units"
        evidence_index = main.read_json(
            os.path.join(work, "evidence_index.json"))
        kinds = set(evidence_index["by_kind"].keys())
        for need in ("number", "citation", "figure", "hedge", "negation"):
            assert need in kinds, "missing evidence kind: %s (have %s)" % (need, kinds)

        # simulate the model filling every unit (identity revision)
        for u in edits["units"]:
            u["revised"] = u["original"]
            u["edit_type"] = "none"
            u["reason"] = "identity revision for self-test"
        main.write_json(os.path.join(work, "edits.json"), edits)

        assert main.main(["--input", inp, "--output", out, "--work", work,
                          "verify"]) == 0
        report = main.read_json(os.path.join(work, "check_report.json"))
        assert len(report["errors"]) == 0, report["errors"]

        assert main.main(["--input", inp, "--output", out, "--work", work,
                          "finalize"]) == 0
        result = main.read_json(os.path.join(out, "result.json"))
        assert result["case_id"] == "demo-001"
        assert result["status"] == "ok", result["status"]
        assert len(result["evidence_map"]) > 0
        assert os.path.isfile(os.path.join(out, "manuscript.md"))
        assert os.path.isfile(os.path.join(out, "result.json"))

        assert main.main(["--input", inp, "--output", out, "--work", work,
                          "selfcheck"]) == 0
        print("[ok] minimal draft -> evidence map + ok result, errors=0")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_conservation_catches_number_loss():
    """A dropped number must be flagged as an error (the core guarantee)."""
    root = tempfile.mkdtemp(prefix="polish-selftest-loss-")
    try:
        inp = os.path.join(root, "input")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            json.dump(CASE, f, ensure_ascii=False)
        work = os.path.join(root, "work")
        out = os.path.join(root, "output")
        main.main(["--input", inp, "--output", out, "--work", work, "scaffold"])
        edits = main.read_json(os.path.join(work, "edits.json"))
        for u in edits["units"]:
            u["revised"] = u["original"].replace("35.2 g/L", "a value")
            u["edit_type"] = "word_choice"
            u["reason"] = "self-test: intentionally drop a number"
        main.write_json(os.path.join(work, "edits.json"), edits)
        main.main(["--input", inp, "--output", out, "--work", work, "verify"])
        report = main.read_json(os.path.join(work, "check_report.json"))
        types = [e["type"] for e in report["errors"]]
        assert "number_lost" in types, types
        print("[ok] dropped number -> flagged as number_lost error")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    test_empty_input()
    test_bad_json()
    test_minimal_draft()
    test_conservation_catches_number_loss()
    print("ALL SELFTESTS PASSED")
