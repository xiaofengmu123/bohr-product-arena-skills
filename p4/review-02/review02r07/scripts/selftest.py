"""selftest.py -- self-test for the source-bound citation audit skill.

Runs the robustness scenarios this variant is designed for, then validates the
result.json structure. Standard library only, deterministic.

Usage:
    python -B scripts/selftest.py            # self-test + current-artifact check
    python -B scripts/selftest.py --input DIR --output DIR --work DIR

Exit code 0 means all checks passed; 1 means at least one check failed.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402

MAIN_PY = os.path.join(HERE, "main.py")


def _run_main(args, cwd=None):
    return subprocess.run(
        [sys.executable, "-B", MAIN_PY] + args,
        capture_output=True, text=True, cwd=cwd,
    )


def check(ok, msg, failures):
    if not ok:
        failures.append(msg)
        print("  FAIL: %s" % msg)
    else:
        print("  ok:   %s" % msg)


def validate_result(result_path, contract):
    problems = []
    obj = core_impl.load_json(result_path)
    if not isinstance(obj, dict):
        return ["result.json is not a JSON object"]
    if not obj.get("case_id"):
        problems.append("result.json missing case_id")
    if obj.get("status") not in core_impl.STATUSES:
        problems.append("result.json has invalid status: %r" % obj.get("status"))
    if obj.get("summary", {}).get("verdict") not in core_impl.VERDICTS:
        problems.append("invalid summary.verdict: %r"
                        % obj.get("summary", {}).get("verdict"))
    for key in ("sources", "uncertainty", "pending_human", "change_log",
                "limitations", "errors", "degradations"):
        if key not in obj:
            problems.append("result.json missing top-level key: %s" % key)
    if isinstance(contract, dict):
        for key in contract:
            if key not in obj:
                problems.append("result.json missing output_contract field: %s" % key)
    for f in obj.get("review_report", {}).get("findings", []):
        missing = [k for k in core_impl.REQUIRED_FINDING_KEYS
                   if f.get(k) in (None, "")]
        if missing:
            problems.append("finding %s missing keys: %s"
                            % (f.get("id") or "?", ",".join(missing)))
        if f.get("severity") not in core_impl.SEVERITIES:
            problems.append("finding %s invalid severity %r"
                            % (f.get("id"), f.get("severity")))
        if f.get("category") == "citation_support" and \
                f.get("verdict") not in core_impl.CITATION_VERDICTS:
            problems.append("finding %s citation_support verdict not in four-tier set: %r"
                            % (f.get("id"), f.get("verdict")))
        if not f.get("quote") or not f.get("locator"):
            problems.append("finding %s has empty quote/locator" % f.get("id"))
    return problems


def scenario_empty(input_dir, output_dir, work_dir):
    print("[scenario] empty input directory")
    proc = _run_main(["pipeline", "--input", input_dir,
                      "--output", output_dir, "--work", work_dir])
    check(proc.returncode == 0, "exit code 0", [])
    obj = core_impl.load_json(os.path.join(output_dir, "result.json"))
    check(isinstance(obj, dict) and obj.get("status") == "insufficient_evidence",
          "status=insufficient_evidence", [])
    check(isinstance(obj, dict) and obj.get("summary", {}).get("verdict") == "insufficient",
          "verdict=insufficient", [])


def scenario_bad_json(input_dir, output_dir, work_dir):
    print("[scenario] malformed case.json")
    os.makedirs(input_dir, exist_ok=True)
    with open(os.path.join(input_dir, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{ this is not json ")
    with open(os.path.join(input_dir, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# task\n")
    proc = _run_main(["pipeline", "--input", input_dir,
                      "--output", output_dir, "--work", work_dir])
    check(proc.returncode == 0, "exit code 0", [])
    obj = core_impl.load_json(os.path.join(output_dir, "result.json"))
    check(isinstance(obj, dict), "result.json parseable", [])
    check(isinstance(obj, dict) and obj.get("errors"), "malformed JSON recorded in errors", [])


def scenario_oversized(input_dir, output_dir, work_dir):
    print("[scenario] oversized material file")
    os.makedirs(input_dir, exist_ok=True)
    with open(os.path.join(input_dir, "case.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"case_id": "c-oversized",
                             "output_contract": {"required": "field"}}))
    with open(os.path.join(input_dir, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# task\n")
    big = os.path.join(input_dir, "big.txt")
    with open(big, "wb") as fh:
        fh.write(b"x" * (core_impl.MAX_FILE_BYTES + 1))
    proc = _run_main(["pipeline", "--input", input_dir,
                      "--output", output_dir, "--work", work_dir])
    check(proc.returncode == 0, "exit code 0", [])
    manifest = core_impl.load_json(os.path.join(work_dir, "manifest.json"))
    mats = (manifest or {}).get("materials", [])
    check(any(m.get("status") == "skipped" for m in mats),
          "oversized file skipped and recorded", [])
    obj = core_impl.load_json(os.path.join(output_dir, "result.json"))
    check(isinstance(obj, dict) and obj.get("required") == "field",
          "output_contract field echoed", [])


def scenario_missing_param():
    print("[scenario] missing/bad --input directory")
    proc = _run_main(["pipeline", "--input",
                      os.path.join(tempfile.gettempdir(), "no-such-dir-xyz-42")])
    check(proc.returncode == 0, "exit code 0 with missing input dir", [])


def scenario_normal(input_dir, output_dir, work_dir):
    print("[scenario] normal tiny case with one text material")
    os.makedirs(input_dir, exist_ok=True)
    contract = {"target": "citation-fact", "require_evidence": True}
    with open(os.path.join(input_dir, "case.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"case_id": "c-normal", "output_contract": contract}))
    with open(os.path.join(input_dir, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 引用与事实核验\n\n审查材料。\n")
    with open(os.path.join(input_dir, "paper.md"), "w", encoding="utf-8") as fh:
        fh.write("# Methods\n\nN=100 subjects were enrolled.\n")
    proc = _run_main(["pipeline", "--input", input_dir,
                      "--output", output_dir, "--work", work_dir])
    check(proc.returncode == 0, "exit code 0", [])
    manifest = core_impl.load_json(os.path.join(work_dir, "manifest.json"))
    mats = (manifest or {}).get("materials", [])
    check(any(m.get("status") == "ok" and m.get("extracted_text") for m in mats),
          "text material extracted", [])
    obj = core_impl.load_json(os.path.join(output_dir, "result.json"))
    check(isinstance(obj, dict) and obj.get("case_id") == "c-normal",
          "case_id echoed", [])
    check(isinstance(obj, dict) and obj.get("status") == "partial",
          "status=partial before review completes", [])
    check(os.path.exists(os.path.join(output_dir, "report.md")),
          "report.md rendered", [])
    return obj, contract


def check_artifacts(input_dir, output_dir):
    print("[scenario] validate current --input/--output artifacts")
    result_path = os.path.join(output_dir, "result.json")
    if not os.path.exists(result_path):
        print("  skip: no result.json at %s (run pipeline first)" % result_path)
        return
    manifest = core_impl.load_json(os.path.join(
        os.path.dirname(output_dir), "work", "manifest.json"))
    contract = {}
    if isinstance(manifest, dict):
        contract = (manifest.get("case") or {}).get("output_contract") or {}
    problems = validate_result(result_path, contract)
    if problems:
        for p in problems:
            print("  FAIL: %s" % p)
        raise SystemExit(1)
    print("  ok:   current result.json passes structural validation")


def main(argv=None):
    parser = argparse.ArgumentParser(description="self-test for the skill")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--work", default="/app/work")
    args = parser.parse_args(argv)

    failures = []
    print("== help exits 0 ==")
    proc = _run_main(["--help"])
    check(proc.returncode == 0, "main.py --help exit code 0", failures)

    with tempfile.TemporaryDirectory() as td:
        root = os.path.join(td, "s")
        scenario_empty(os.path.join(root, "e_in"), os.path.join(root, "e_out"),
                       os.path.join(root, "e_work"))
        scenario_bad_json(os.path.join(root, "b_in"), os.path.join(root, "b_out"),
                          os.path.join(root, "b_work"))
        scenario_oversized(os.path.join(root, "o_in"), os.path.join(root, "o_out"),
                           os.path.join(root, "o_work"))
        scenario_missing_param()
        obj, contract = scenario_normal(os.path.join(root, "n_in"),
                                        os.path.join(root, "n_out"),
                                        os.path.join(root, "n_work"))

        print("== structure validation on normal scenario ==")
        problems = validate_result(os.path.join(root, "n_out", "result.json"), contract)
        for p in problems:
            failures.append(p)
            print("  FAIL: %s" % p)
        if not problems:
            print("  ok:   normal result.json passes validation")

    print("== current artifacts (if present) ==")
    check_artifacts(args.input, args.output)

    if failures:
        print("\nSELFTEST FAILED: %d problem(s)" % len(failures))
        return 1
    print("\nSELFTEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
