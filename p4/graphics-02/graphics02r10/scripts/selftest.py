#!/usr/bin/env python3
"""Self-test for the sci-figure Skill (no benchmark scores, no fabricated data).

Runs the deterministic core against a small synthetic example in a temporary
directory, plus three degraded paths (empty input, bad JSON, missing data).
Checks only internal invariants; prints pass/fail and exits non-zero on a real
failure so a reviewer or CI can catch regressions.
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl  # noqa: E402

CSV = "group,mean,sd\nA,12.5,2.1\nB,8.2,1.4\nC,5.9,0.8\n"


def _write_case(d, contract):
    case = {"case_id": "selftest-case-0001",
            "output_contract": contract,
            "task": {"output_contract": contract}}
    core_impl.write_json(os.path.join(d, "case.json"), case)
    os.makedirs(os.path.join(d, "input"), exist_ok=True)
    with open(os.path.join(d, "input", "data.csv"), "w", encoding="utf-8") as f:
        f.write(CSV)


def _mkdirs(d):
    for sub in ("input", "work", "output"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)


def run():
    results = []
    base = tempfile.mkdtemp(prefix="scifig-selftest-")

    def check(name, cond, note=""):
        results.append((name, bool(cond), note))

    # 1) full happy path on synthetic bar chart
    d = os.path.join(base, "happy")
    _mkdirs(d)
    _write_case(d, {"figure": "figure.svg", "png": "figure.png", "pdf": "figure.pdf"})
    log = core_impl.pipeline(os.path.join(d, "case.json"), os.path.join(d, "input"),
                             os.path.join(d, "work"), os.path.join(d, "output"),
                             ["bootstrap", "extract", "build", "render", "validate", "finalize"])
    result, _ = core_impl.read_json(os.path.join(d, "output", "result.json"))
    check("happy.result_exists", result is not None)
    check("happy.case_id", result and result.get("case_id") == "selftest-case-0001")
    check("happy.contract_filled", result and result.get("output_contract", {}).get("figure"))
    check("happy.status", result and result.get("status") in ("complete", "partial"))
    check("happy.svg_exists", os.path.exists(os.path.join(d, "output", "figure.svg")))
    svg = os.path.join(d, "output", "figure.svg")
    if os.path.exists(svg):
        info = core_impl.check_svg_text(svg)
        check("happy.svg_text_editable", info.get("editable"), "texts=%s" % info.get("n_text"))
        check("happy.svg_has_glyphs", info.get("n_glyph", 0) >= 3, "glyphs=%s" % info.get("n_glyph"))
    ir_check, _ = core_impl.read_json(os.path.join(d, "work", "ir_check.json"))
    check("happy.ir_no_fatal", ir_check and ir_check.get("n_fatal", 1) == 0)
    check("happy.ir_no_error", ir_check and ir_check.get("n_error", 0) == 0,
          "issues=%s" % (ir_check or {}).get("issues"))
    check("happy.evidence_map", result and len(result.get("evidence_map", [])) >= 3)

    # 2) empty input dir -> valid insufficient/partial result, exit-safe
    d2 = os.path.join(base, "empty")
    _mkdirs(d2)
    core_impl.bootstrap(os.path.join(d2, "case.json"), os.path.join(d2, "input"),
                        os.path.join(d2, "work"), os.path.join(d2, "output"))
    r2, _ = core_impl.read_json(os.path.join(d2, "output", "result.json"))
    check("empty.result_exists", r2 is not None)
    check("empty.status_honest", r2 and r2.get("status") in ("partial", "failed",
                                                             "insufficient_evidence", "input_error"))

    # 3) bad JSON -> error recorded, no crash
    d3 = os.path.join(base, "badjson")
    _mkdirs(d3)
    with open(os.path.join(d3, "case.json"), "w", encoding="utf-8") as f:
        f.write("{not valid json")
    core_impl.bootstrap(os.path.join(d3, "case.json"), os.path.join(d3, "input"),
                        os.path.join(d3, "work"), os.path.join(d3, "output"))
    r3, _ = core_impl.read_json(os.path.join(d3, "output", "result.json"))
    check("badjson.result_exists", r3 is not None)
    check("badjson.error_recorded", r3 and r3.get("errors"))

    # 4) pre-render validation catches a glyph/value mismatch (mapping guard)
    d4 = os.path.join(base, "mismatch")
    _mkdirs(d4)
    _write_case(d4, {"figure": "figure.svg"})
    core_impl.extract_all(os.path.join(d4, "work"), os.path.join(d4, "input"))
    ir = core_impl.scaffold_ir(os.path.join(d4, "work"), name="fig", kind="bar", table_name="t_data")
    ir["glyphs"][0]["value"] = 999.0  # deliberately break the mapping
    core_impl.save_ir(os.path.join(d4, "work"), ir)
    issues = core_impl.validate_ir(ir)
    check("mismatch.detected", any(i.get("kind") == "mapping" for i in issues),
          "issues=%s" % [i.get("kind") for i in issues])

    # summary
    n_pass = sum(1 for _, ok, _ in results if ok)
    for name, ok, note in results:
        print("[%s] %s %s" % ("PASS" if ok else "FAIL", name, ("| " + note) if note else ""))
    print("selftest: %d/%d passed" % (n_pass, len(results)))
    shutil.rmtree(base, ignore_errors=True)
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    sys.exit(run())
