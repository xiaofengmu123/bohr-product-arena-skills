#!/usr/bin/env python3
"""Self-test for grant-writing evidence skill result.json.

Checks, in order of the scoring dimensions:
  - structural completeness (case_id, status, artifact, fact_source_map,
    version_boundary, sources, uncertainty, pending_human, change_log, runtime)
  - evidence traceability (every mapped claim has non-empty locator AND quote)
  - honesty (done must point at an existing artifact; partial must list limits)
  - determinism helpers (empty-input run produces a legal result, no crash)

Usage:
  python scripts/selftest.py --result /app/output/result.json --output /app/output
  python scripts/selftest.py --demo            # run empty-input robustness check
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

REQUIRED_KEYS = [
    "case_id", "status", "artifact", "fact_source_map",
    "version_boundary", "sources", "uncertainty", "pending_human",
    "change_log", "runtime",
]

STATUS_VALUES = {
    "in_progress", "done", "partial", "failed",
    "insufficient_evidence", "input_error",
}


def load(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        return None


def check(result, output_dir):
    issues = []
    warnings = []

    if not isinstance(result, dict):
        issues.append("result_json_not_object")
        return issues, warnings

    for key in REQUIRED_KEYS:
        if key not in result:
            issues.append("missing_key:%s" % key)

    case_id = result.get("case_id")
    if not isinstance(case_id, str) or not case_id.strip():
        issues.append("case_id_empty")

    status = result.get("status")
    if status not in STATUS_VALUES:
        issues.append("status_invalid:%s" % status)

    # evidence traceability: the core of this skill variant
    fs = result.get("fact_source_map")
    if isinstance(fs, dict):
        claims = fs.get("claims", [])
        for c in claims:
            cid = c.get("claim_id", "?")
            if c.get("status") == "mapped":
                if not (c.get("locator") or "").strip():
                    issues.append("claim_missing_locator:%s" % cid)
                if not (c.get("quote") or "").strip():
                    issues.append("claim_missing_quote:%s" % cid)
            elif c.get("status") in ("unmapped", "conflict", "pending_human"):
                if not (c.get("note") or "").strip():
                    warnings.append("claim_undocumented:%s" % cid)
    else:
        warnings.append("fact_source_map_not_object")

    # honesty: done implies an editable artifact that actually exists
    artifact = result.get("artifact")
    if status == "done":
        if isinstance(artifact, dict) and artifact.get("path"):
            p = artifact["path"]
            if not os.path.isabs(p):
                p = os.path.join(output_dir, p)
            if not os.path.isfile(p):
                issues.append("artifact_missing:%s" % artifact.get("path"))
        else:
            issues.append("done_without_artifact_path")
    if status == "partial":
        vb = result.get("version_boundary", {})
        if isinstance(vb, dict) and not (vb.get("material_gaps") or vb.get("limitations")
                                         or vb.get("anomalies")):
            warnings.append("partial_without_limits")
        if not result.get("uncertainty"):
            warnings.append("partial_without_uncertainty")

    # sources carry sha256 for re-location
    for s in result.get("sources", []):
        if isinstance(s, dict) and not s.get("sha256"):
            warnings.append("source_without_sha256:%s" % s.get("path", "?"))

    return issues, warnings


def report(name, issues, warnings):
    ok = len(issues) == 0
    print("== %s: %s ==" % (name, "PASS" if ok else "FAIL"))
    for i in issues:
        print("  ISSUE: %s" % i)
    for w in warnings:
        print("  WARN:  %s" % w)
    return ok


def run_demo():
    """Empty-input robustness: must exit 0 and write a legal result."""
    tmp = tempfile.mkdtemp(prefix="gws-demo-")
    inp = os.path.join(tmp, "input")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    os.makedirs(inp)
    main_py = os.path.join(HERE, "main.py")
    try:
        rc = subprocess.call([sys.executable, main_py, "--input", inp,
                              "--output", out, "--work", work])
        result_path = os.path.join(out, "result.json")
        res = load(result_path)
        issues, warnings = check(res, out) if res else (["no_result_json"], [])
        ok = (rc == 0) and (res is not None) and (res.get("status") in STATUS_VALUES)
        print("== demo empty-input: %s ==" % ("PASS" if ok else "FAIL"))
        print("   rc=%s status=%s" % (rc, res.get("status") if res else None))
        return ok
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Self-test for grant-writing result.json")
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args(argv)

    if a.demo:
        sys.exit(0 if run_demo() else 1)

    result = load(a.result)
    if result is None:
        print("FAIL: cannot load %s" % a.result)
        return 1
    issues, warnings = check(result, a.output)
    ok = report(a.result, issues, warnings)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
