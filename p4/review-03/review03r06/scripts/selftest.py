"""selftest.py -- deterministic end-to-end self-test in a temporary directory.

Scenarios (all use synthetic inputs created at runtime, never shipped):
  1. valid input -> discover + scaffold + one sample finding + meta + finalize;
     result.json must load, carry case_id + findings + numeric candidates.
  2. empty input -> status insufficient_evidence / input_error, exit 0.
  3. bad case.json -> error recorded, no crash.
  4. --help on every script -> exit code 0.

Stdlib only. Exit 0 iff all checks pass.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = ["discover.py", "scaffold.py", "finalize.py"]


def run_py(script, args, cwd=None):
    cmd = [sys.executable, "-B", os.path.join(HERE, script)] + args
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)


def write(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def check(cond, msg, results):
    results.append((bool(cond), msg))
    if not cond:
        print("  FAIL: " + msg, flush=True)


def scenario_valid(root, results):
    print("[selftest] scenario 1: valid synthetic input", flush=True)
    inp = os.path.join(root, "s1", "input")
    out = os.path.join(root, "s1", "output")
    work = os.path.join(root, "s1", "work")
    mat = os.path.join(inp, "materials")
    os.makedirs(mat)
    case = {"case_id": "selftest-001", "output_contract": {
        "expected_fields": [], "review_scope": "", "required_evidence": None}}
    write(os.path.join(inp, "case.json"), json.dumps(case, ensure_ascii=False))
    write(os.path.join(inp, "task.md"), "# 自检任务\n请审查材料。\n")
    material = ("A single-centre randomised trial enrolled N=100 patients and N=120 controls. "
                "The primary outcome improved after treatment (p = 0.06, 95% CI 0.95 to 2.10). "
                "The difference was statistically significant, so the authors concluded the "
                "treatment works.\n")
    write(os.path.join(mat, "paper.txt"), material)

    r = run_py("discover.py", ["--input", inp, "--output", out, "--work", work])
    check(r.returncode == 0, "discover exit 0 (valid)", results)
    r = run_py("scaffold.py", ["--input", inp, "--output", out, "--work", work])
    check(r.returncode == 0, "scaffold exit 0 (valid)", results)

    os.makedirs(os.path.join(work, "findings"))
    finding = {
        "id": "f001", "facet": "results_reporting", "check_class": "statistical_reporting",
        "finding_type": "factual_error", "severity": "major", "locator": "¶1",
        "quote": "The difference was statistically significant",
        "basis": "primary-outcome p=0.06 contradicts the wording 'statistically significant'.",
        "suggestion": "Report the exact p value and revise the significance claim accordingly.",
        "confidence": "high", "review_status": "confirmed", "segment_id": "s00002",
    }
    write(os.path.join(work, "findings", "f001.json"),
          json.dumps(finding, ensure_ascii=False))
    meta = {
        "status": "partial", "overall_verdict": "需要修改",
        "checked_facets": ["results_reporting"],
        "sources": [{"file": "materials/paper.txt", "locator": "¶1"}],
        "uncertainty": [], "pending_human": [], "limitations": ["自检：其余检查面未走查"],
        "numeric_decisions": [],
    }
    write(os.path.join(work, "meta.json"), json.dumps(meta, ensure_ascii=False))
    r = run_py("finalize.py", ["--input", inp, "--output", out, "--work", work])
    check(r.returncode == 0, "finalize exit 0 (valid)", results)

    res_path = os.path.join(out, "result.json")
    res = None
    try:
        res = json.load(open(res_path, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        check(False, "result.json loads (%s)" % e, results)
    if res:
        check(res.get("case_id") == "selftest-001", "case_id preserved", results)
        check(res.get("status") in ("ok", "partial"), "status valid", results)
        findings = res.get("review_report", {}).get("findings", [])
        check(bool(findings) and findings[0].get("id") == "f001", "finding merged", results)
        check(bool(findings) and findings[0].get("source_sha"), "source_sha auto-filled", results)
        check(len(res.get("numeric_checks", [])) >= 1, "numeric candidate produced", results)
        check(res.get("expected_fields") == [], "output_contract list backfilled", results)
        check(res.get("review_scope") == "", "output_contract empty-string backfilled", results)
    rep = os.path.join(out, "report.md")
    check(os.path.isfile(rep) and os.path.getsize(rep) > 0, "report.md rendered", results)


def scenario_empty(root, results):
    print("[selftest] scenario 2: empty input", flush=True)
    inp = os.path.join(root, "s2", "input")
    out = os.path.join(root, "s2", "output")
    work = os.path.join(root, "s2", "work")
    os.makedirs(inp)
    r = run_py("discover.py", ["--input", inp, "--output", out, "--work", work])
    check(r.returncode == 0, "discover exit 0 (empty)", results)
    r = run_py("scaffold.py", ["--input", inp, "--output", out, "--work", work])
    check(r.returncode == 0, "scaffold exit 0 (empty)", results)
    r = run_py("finalize.py", ["--input", inp, "--output", out, "--work", work])
    check(r.returncode == 0, "finalize exit 0 (empty)", results)
    res = None
    try:
        res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        res = None
    check(res is not None, "empty result.json loads", results)
    if res:
        check(res.get("status") in ("insufficient_evidence", "input_error"),
              "empty status honest (%s)" % res.get("status"), results)


def scenario_bad_json(root, results):
    print("[selftest] scenario 3: bad case.json", flush=True)
    inp = os.path.join(root, "s3", "input")
    out = os.path.join(root, "s3", "output")
    work = os.path.join(root, "s3", "work")
    os.makedirs(inp)
    write(os.path.join(inp, "case.json"), "{not valid json")
    write(os.path.join(inp, "task.md"), "# task\n")
    write(os.path.join(inp, "paper.txt"), "N=50 patients were analysed.\n")
    r = run_py("discover.py", ["--input", inp, "--output", out, "--work", work])
    check(r.returncode == 0, "discover exit 0 (bad json)", results)
    r = run_py("scaffold.py", ["--input", inp, "--output", out, "--work", work])
    check(r.returncode == 0, "scaffold exit 0 (bad json)", results)
    r = run_py("finalize.py", ["--input", inp, "--output", out, "--work", work])
    check(r.returncode == 0, "finalize exit 0 (bad json)", results)
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    check(any(e.get("kind") == "bad_json" for e in res.get("errors", [])),
          "bad_json recorded in errors", results)


def scenario_help(results):
    print("[selftest] scenario 4: --help", flush=True)
    for s in SCRIPTS + ["selftest.py"]:
        r = run_py(s, ["--help"])
        check(r.returncode == 0, "%s --help exit 0" % s, results)


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print("usage: selftest.py [--help]\nruns the deterministic self-test suite.")
        return 0
    results = []
    root = tempfile.mkdtemp(prefix="selftest_stats_audit_")
    try:
        scenario_valid(root, results)
        scenario_empty(root, results)
        scenario_bad_json(root, results)
        scenario_help(results)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    fails = [m for ok, m in results if not ok]
    print("[selftest] %d checks, %d failed" % (len(results), len(fails)), flush=True)
    for m in fails:
        print("  FAIL: " + m, flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
