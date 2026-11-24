#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py - end-to-end self test for the sci-plan-comparison skill.

Runs the demo pipeline (example data, explicitly labelled 示例), validates the
result, and additionally exercises empty-input and bad-JSON degradation paths.
Exits 0 when all checks pass; prints a plain PASS/FAIL summary.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl  # noqa: E402


def run_case(input_dir, output_dir, work_dir, expect_status):
    core_impl.cmd_inspect(__import__("argparse").Namespace(
        input=input_dir, output=output_dir, work=work_dir))
    if os.path.isfile(os.path.join(work_dir, "analysis.json")):
        core_impl.cmd_assemble(__import__("argparse").Namespace(
            input=input_dir, output=output_dir, work=work_dir))
    result_path = os.path.join(output_dir, "result.json")
    r = json.load(open(result_path, encoding="utf-8"))
    issues, warnings = [], []
    core_impl._validate_result(r, result_path, issues, warnings)
    return r, issues, warnings


def _strip_volatile(obj):
    """Remove timestamps so determinism is checked on computed content only."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ("change_log", "runtime", "created_at", "assembled_at", "time"):
                continue
            out[k] = _strip_volatile(v)
        return out
    if isinstance(obj, list):
        return [_strip_volatile(x) for x in obj]
    return obj


def main():
    checks = []
    tmp = tempfile.mkdtemp(prefix="sci-plan-selftest-")

    # 1. demo end-to-end
    inp = os.path.join(tmp, "demo_in")
    out = os.path.join(tmp, "demo_out")
    wrk = os.path.join(tmp, "demo_work")
    os.makedirs(inp); os.makedirs(out); os.makedirs(wrk)
    core_impl._write_demo_input(inp, wrk)
    demo_r, issues, warnings = run_case(inp, out, wrk, "ok")
    ok = (demo_r.get("status") == "ok" and len(issues) == 0)
    checks.append(("demo_end_to_end", ok,
                   "status=%s issues=%s" % (demo_r.get("status"), issues)))

    # 2. empty input directory -> input_error / insufficient_evidence, no crash
    einp = os.path.join(tmp, "empty_in")
    eout = os.path.join(tmp, "empty_out")
    ewrk = os.path.join(tmp, "empty_work")
    os.makedirs(einp); os.makedirs(eout); os.makedirs(ewrk)
    r, issues, warnings = run_case(einp, eout, ewrk, "input_error")
    ok = (r.get("status") in ("input_error", "insufficient_evidence"))
    checks.append(("empty_input_degradation", ok, "status=%s" % r.get("status")))

    # 3. bad JSON -> input_error, no crash
    binp = os.path.join(tmp, "bad_in")
    bout = os.path.join(tmp, "bad_out")
    bwrk = os.path.join(tmp, "bad_work")
    os.makedirs(binp); os.makedirs(bout); os.makedirs(bwrk)
    with open(os.path.join(binp, "case.json"), "w", encoding="utf-8") as f:
        f.write("{ not valid json ")
    r, issues, warnings = run_case(binp, bout, bwrk, "input_error")
    ok = (r.get("status") == "input_error")
    checks.append(("bad_json_degradation", ok, "status=%s" % r.get("status")))

    # 4. determinism: demo twice, identical result.json
    out2 = os.path.join(tmp, "demo_out2")
    wrk2 = os.path.join(tmp, "demo_work2")
    os.makedirs(out2); os.makedirs(wrk2)
    core_impl._write_demo_input(inp, wrk2)
    r2, _, _ = run_case(inp, out2, wrk2, "ok")
    s1 = json.dumps(_strip_volatile(demo_r), ensure_ascii=False, sort_keys=True)
    s2 = json.dumps(_strip_volatile(r2), ensure_ascii=False, sort_keys=True)
    checks.append(("deterministic_rerun", s1 == s2, "identical=%s" % (s1 == s2)))

    # 5. artifacts present and valid
    art_ok = True
    for fn in ("comparison_matrix.csv", "comparison_matrix.svg",
               "comparison_matrix.pdf", "plan_comparison.md"):
        if not os.path.isfile(os.path.join(out, fn)):
            art_ok = False
    checks.append(("artifacts_present", art_ok, ""))

    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print("[%s] %s %s" % ("PASS" if ok else "FAIL", name, detail))
    print("selftest %s (%d/%d passed)" % ("PASS" if not failed else "FAIL",
                                          len(checks) - len(failed), len(checks)))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
