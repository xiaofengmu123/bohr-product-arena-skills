"""Self-test for the research selection & novelty review skill.

Run from the package root:

    python3 -B scripts/selftest.py

The test builds a synthetic input (with an output_contract, a readable
material, a bad-JSON material and a minimal PDF), drives the full
init -> discover -> record -> finalize pipeline, then verifies the result
contract and the degradation paths.  It never touches /input or /app/output.
Exit code 0 = all checks pass, 2 = failure.
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl  # noqa: E402
import main as entry  # noqa: E402

MINIMAL_PDF = b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj
4 0 obj << /Length 60 >> stream
BT /F1 12 Tf 72 720 Td (Hello selection review) Tj ET
endstream endobj
5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
xref
0 6
0000000000 65535 f 
trailer << /Size 6 /Root 1 0 R >>
startxref
0
%%EOF
"""


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def make_input(root):
    write(os.path.join(root, "case.json"), json.dumps({
        "case_id": "case-selftest-001",
        "output_contract": {
            "research_question": None,
            "search_protocol": None,
            "prior_work": None,
            "novelty": None,
            "feasibility": None,
            "evidence_matrix": [],
            "conflicts": [],
            "synthesis": None,
        },
    }, ensure_ascii=False))
    write(os.path.join(root, "task.md"),
          "# 选题评估\n\n请对材料做选题评估。\n")
    write(os.path.join(root, "materials", "paper.md"),
          "# Paper A\n\nMethod X is described here. Baseline B is used.\n")
    write(os.path.join(root, "materials", "broken.json"), "{not valid json")
    with open(os.path.join(root, "materials", "paper.pdf"), "wb") as fh:
        fh.write(MINIMAL_PDF)


def make_fragments(work):
    plan = {
        "research_question": {
            "original": "Is X better than B?",
            "object": "method X",
            "variables": ["outcome"], "baseline": "baseline B",
            "expected_outcome": "X >= B",
            "falsification_condition": "X < B on held-out set",
            "testable_statement": "X improves outcome over B",
            "status": "complete", "gaps": [],
        },
        "search_protocol": {
            "queries": ["X baseline B"], "inclusion_criteria": ["peer-reviewed"],
            "exclusion_criteria": ["non-English"], "scope": "materials",
            "screening_steps": ["dedupe"], "limitations": ["no external DB"],
        },
    }
    evidence = {"records": [
        {"evidence_id": "e1", "source_id": "src01", "locator":
         "materials/paper.md#L1", "quote": "Method X is described here.",
         "claim": "paper describes method X", "field": "prior_work",
         "status": "reported"},
    ]}
    figure = {
        "comparison_matrix": [
            {"row_id": "w1", "work": "Paper A", "source_id": "src01",
             "task": "eval X", "method": "X", "data": "not reported",
             "evaluation": "baseline B", "conclusion": "X works",
             "comparable": True, "non_comparable_reason": None,
             "evidence_ids": ["e1"]},
        ],
        "novelty_claims": [
            {"claim_id": "n1", "statement": "X is new", "type": "method",
             "verdict": "partially_covered", "covered_by": ["w1"],
             "reason": "same method appears", "evidence_ids": ["e1"]},
        ],
        "feasibility_premises": [
            {"premise_id": "f1", "aspect": "data", "statement": "data exists",
             "status": "reported", "risk": "low", "evidence_ids": [],
             "conditional_conclusion": "feasible if data available"},
        ],
        "conditional_conclusion": "feasible with stated data",
    }
    synthesis = {
        "conclusions": ["X is partially covered by Paper A"],
        "research_gaps": ["held-out evaluation missing"],
        "boundary_statement": "only materials in scope",
        "conflicts": [],
        "uncertainty": {"biases": [], "gaps": ["no external DB"],
                        "undetermined_items": []},
        "pending_human": [], "limitations": ["single source"],
    }
    write(os.path.join(work, "fragments", "01_plan.json"), json.dumps(plan))
    write(os.path.join(work, "fragments", "02_evidence.json"),
          json.dumps(evidence))
    write(os.path.join(work, "fragments", "03_figure.json"),
          json.dumps(figure))
    write(os.path.join(work, "fragments", "04_synthesis.json"),
          json.dumps(synthesis))


def run_pipeline(input_dir, output_dir, work_dir):
    entry.main(["init", "--input", input_dir, "--output", output_dir,
                "--work", work_dir])
    entry.main(["discover", "--input", input_dir, "--output", output_dir,
                "--work", work_dir])
    for kind, name in (("plan", "01_plan.json"), ("evidence", "02_evidence.json"),
                       ("figure", "03_figure.json"),
                       ("synthesis", "04_synthesis.json")):
        rc = entry.main(["record", "--kind", kind, "--file",
                         os.path.join(work_dir, "fragments", name),
                         "--work", work_dir])
        if rc != 0:
            return rc
    return entry.main(["finalize", "--input", input_dir, "--output", output_dir,
                       "--work", work_dir])


def check(label, cond, detail=""):
    global failures
    if cond:
        print("[PASS] %s" % label)
    else:
        failures += 1
        print("[FAIL] %s %s" % (label, detail))


failures = 0
tmp = tempfile.mkdtemp(prefix="selftest-selection-")
try:
    # --- Case 1: full pipeline with an output_contract --------------------
    input_dir = os.path.join(tmp, "case1", "input")
    output_dir = os.path.join(tmp, "case1", "output")
    work_dir = os.path.join(tmp, "case1", "work")
    make_input(input_dir)
    make_fragments(work_dir)

    result_path = os.path.join(output_dir, "result.json")
    entry.main(["init", "--input", input_dir, "--output", output_dir,
                "--work", work_dir])
    skeleton, err = core_impl.read_json_tolerant(result_path)
    check("init writes a result.json skeleton", err is None and isinstance(skeleton, dict))
    check("skeleton status is partial", skeleton.get("status") == "partial")
    check("output_contract echoed", skeleton.get("output_contract") and
          "research_question" in skeleton.get("output_contract", {}))

    rc = run_pipeline(input_dir, output_dir, work_dir)
    result, err = core_impl.read_json_tolerant(result_path)
    check("finalize returns 0", rc == 0)
    check("case_id preserved", result.get("case_id") == "case-selftest-001")
    check("research question merged", (result.get("research_question") or {})
          .get("testable_statement") == "X improves outcome over B")
    check("evidence_matrix has locator+quote", result.get("evidence_matrix") and
          result["evidence_matrix"][0].get("locator") and
          result["evidence_matrix"][0].get("quote"))
    check("novelty verdict + covered_by", result.get("novelty") and
          result["novelty"]["claims"][0].get("verdict") == "partially_covered" and
          result["novelty"]["claims"][0].get("covered_by") == ["w1"])
    check("feasibility premise status", result.get("feasibility") and
          result["feasibility"]["premises"][0].get("status") == "reported")
    check("sources populated", isinstance(result.get("sources"), list) and
          len(result.get("sources", [])) >= 1)
    check("change_log populated", isinstance(result.get("change_log"), list) and
          len(result.get("change_log", [])) >= 3)
    check("report.md written", os.path.exists(os.path.join(output_dir, "report.md")))
    report_md = open(os.path.join(output_dir, "report.md"), encoding="utf-8").read()
    check("report.md is plain markdown", report_md.startswith("# "))
    check("degradation recorded for broken.json", any(
          "broken.json" in d for d in result.get("degradations", [])))

    # --- Case 2: empty input dir -> insufficient_evidence -----------------
    empty_in = os.path.join(tmp, "case2", "input")
    empty_out = os.path.join(tmp, "case2", "output")
    empty_work = os.path.join(tmp, "case2", "work")
    os.makedirs(empty_in, exist_ok=True)
    write(os.path.join(empty_in, "case.json"), "{}")
    entry.main(["init", "--input", empty_in, "--output", empty_out,
                "--work", empty_work])
    entry.main(["discover", "--input", empty_in, "--output", empty_out,
                "--work", empty_work])
    entry.main(["finalize", "--input", empty_in, "--output", empty_out,
                "--work", empty_work])
    empty_result, _ = core_impl.read_json_tolerant(
        os.path.join(empty_out, "result.json"))
    check("empty input -> insufficient_evidence",
          empty_result.get("status") == "insufficient_evidence")

    # --- Case 3: missing input dir -> input_error -------------------------
    missing_out = os.path.join(tmp, "case3", "output")
    missing_work = os.path.join(tmp, "case3", "work")
    entry.main(["init", "--input", os.path.join(tmp, "case3", "nope"),
                "--output", missing_out, "--work", missing_work])
    missing_result, _ = core_impl.read_json_tolerant(
        os.path.join(missing_out, "result.json"))
    check("missing input -> input_error",
          missing_result.get("status") == "input_error")

    # --- Case 4: present dir but missing case.json -> input_error ----------
    no_case_in = os.path.join(tmp, "case4", "input")
    no_case_out = os.path.join(tmp, "case4", "output")
    no_case_work = os.path.join(tmp, "case4", "work")
    os.makedirs(no_case_in, exist_ok=True)
    write(os.path.join(no_case_in, "materials", "paper.md"), "# Paper\n")
    entry.main(["init", "--input", no_case_in, "--output", no_case_out,
                "--work", no_case_work])
    entry.main(["discover", "--input", no_case_in, "--output", no_case_out,
                "--work", no_case_work])
    no_case_result, _ = core_impl.read_json_tolerant(
        os.path.join(no_case_out, "result.json"))
    check("missing case.json -> input_error",
          no_case_result.get("status") == "input_error")

    # --- Help exits 0 -----------------------------------------------------
    check("--help exits 0", entry.main(["--help"]) == 0)

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("-" * 60)
print("selftest: %s" % ("FAILED (%d)" % failures if failures else "PASSED"))
sys.exit(2 if failures else 0)
