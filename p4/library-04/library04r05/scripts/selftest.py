#!/usr/bin/env python3
"""Library-QA selftest: validates a finished result.json against the output_contract and
verifies evidence completeness, then drills the degradation paths (empty input, bad JSON,
missing case.json) end-to-end through main.py. Standard library only.

Usage:
  python3 scripts/selftest.py --input /input --output /app/output --work /app/work
Exit code 0 = all checks pass; non-zero lists the failures.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN_PY = os.path.join(HERE, "main.py")

FRAMEWORK_KEYS = ["case_id", "status", "sources", "uncertainty", "change_log",
                  "pending_human", "errors", "degradations", "contract_fidelity", "runtime"]


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f), None
    except Exception as exc:
        return None, "parse_error:%s" % exc


def validate_result(result_path, case_summary_path):
    issues = []
    result, err = read_json(result_path)
    if result is None:
        return ["result.json unreadable: %s" % err]
    for k in FRAMEWORK_KEYS:
        if k not in result:
            issues.append("missing framework key: %s" % k)
    summary, _ = read_json(case_summary_path)
    contract = {}
    if isinstance(summary, dict) and isinstance(summary.get("contract"), dict):
        contract = summary["contract"]
    for k in contract:
        if k not in result:
            issues.append("missing output_contract key in result: %s" % k)
    if not isinstance(result.get("change_log"), list) or len(result.get("change_log", [])) == 0:
        issues.append("change_log missing or empty")
    for i, a in enumerate(result.get("answers", []) if isinstance(result.get("answers"), list) else []):
        if not isinstance(a, dict):
            issues.append("answers[%d] not object" % i)
            continue
        for j, ev in enumerate(a.get("evidence", []) if isinstance(a.get("evidence"), list) else []):
            for field in ("file", "sha256", "locator", "quote"):
                if field not in ev:
                    issues.append("answers[%d].evidence[%d] missing %s" % (i, j, field))
            if "verified" not in ev:
                issues.append("answers[%d].evidence[%d] missing verification field" % (i, j))
    if result.get("contract_fidelity", {}).get("preserved_keys") != len(contract):
        issues.append("contract_fidelity.preserved_keys mismatch")
    return issues


def drill(name, input_dir, output_dir, work_dir):
    """Run the full pipeline on a synthetic input; returns (status, issues)."""
    proc = subprocess.run(
        [sys.executable, MAIN_PY, "--action", "run",
         "--input", input_dir, "--output", output_dir, "--work", work_dir],
        capture_output=True, text=True, timeout=300,
    )
    issues = []
    if proc.returncode != 0:
        issues.append("%s: non-zero exit %d" % (name, proc.returncode))
    result, err = read_json(os.path.join(output_dir, "result.json"))
    if result is None:
        issues.append("%s: result.json unreadable: %s" % (name, err))
        return "failed", issues
    return result.get("status"), issues


def run_selftest(args):
    reports = []

    issues = validate_result(os.path.join(args.output, "result.json"),
                             os.path.join(args.work, "case_summary.json"))
    reports.append(("validate_result", "ok" if not issues else "FAIL", issues))

    for name, expected in [
        ("empty_input", "insufficient_evidence"),
        ("bad_json", "input_error"),
        ("missing_case", "input_error"),
    ]:
        with tempfile.TemporaryDirectory(prefix="selftest_%s_" % name) as base:
            inp = os.path.join(base, "input")
            out = os.path.join(base, "output")
            work = os.path.join(base, "work")
            os.makedirs(inp)
            os.makedirs(out)
            os.makedirs(work)
            if name == "bad_json":
                with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
                    f.write("{not-valid-json")
            elif name == "missing_case":
                with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
                    f.write("# selftest\n")
            # empty_input: leave the directory completely empty
            status, di = drill(name, inp, out, work)
            ok = status == expected and not di
            reports.append((name, "ok" if ok else "FAIL",
                            ["status=%s (expected %s)" % (status, expected)] + di))

    # determinism drill: two identical runs must yield identical non-volatile output
    with tempfile.TemporaryDirectory(prefix="selftest_determinism_") as base:
        inp = os.path.join(base, "input")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            json.dump({"case_id": "det-1", "output_contract": {"result": None}}, f)
        os.makedirs(os.path.join(inp, "materials"))
        with open(os.path.join(inp, "materials", "a.txt"), "w", encoding="utf-8") as f:
            f.write("alpha line one\nbeta line two\n")
        with open(os.path.join(inp, "materials", "b.txt"), "w", encoding="utf-8") as f:
            f.write("gamma line three\n")
        snapshots = []
        for i in (1, 2):
            out = os.path.join(base, "out%d" % i)
            work = os.path.join(base, "work%d" % i)
            os.makedirs(out)
            os.makedirs(work)
            drill("determinism", inp, out, work)
            r, _ = read_json(os.path.join(out, "result.json"))
            snapshots.append({k: r.get(k) for k in ("case_id", "sources", "library", "contract_fidelity")})
        ok = snapshots[0] == snapshots[1]
        reports.append(("determinism", "ok" if ok else "FAIL",
                        [] if ok else ["non-volatile output differs between two runs"]))

    all_fail = [r for r in reports if r[1] == "FAIL"]
    for name, verdict, detail in reports:
        print("[%s] %s" % (verdict, name))
        for d in detail:
            print("   -", d)
    print("selftest:", "PASS" if not all_fail else "FAIL")
    return 1 if all_fail else 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="selftest.py")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(run_selftest(parse_args()))