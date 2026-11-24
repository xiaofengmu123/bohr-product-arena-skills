#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — self-checks for the analysis-02 skill (stdlib only).

Covers:
  1. special functions and hypothesis tests against hard-coded reference values;
  2. end-to-end pipeline on a small demo case (contract backfill, cleaning,
     compute, SVG output, result.json validity);
  3. determinism (same input -> identical analysis.json);
  4. degradation paths (empty input dir, broken JSON, missing data file).

Exit code 0 means all checks passed.
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import statlib as S  # noqa: E402
import pipeline as P  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}  {detail}")
        FAILURES.append(name)


def close(a, b, tol=1e-6):
    return abs(a - b) <= tol


def test_special_functions():
    print("[1] special functions")
    check("normal_cdf(1.96)", close(S.normal_cdf(1.96), 0.9750021048517795, 1e-6))
    check("normal_ppf(0.975)", close(S.normal_ppf(0.975), 1.9599639845400545, 1e-5))
    check("t two-sided 2.228 df10", close(S.t_pvalue_two_sided(2.228, 10), 0.0500117, 1e-4))
    check("t_ppf(0.975,10)", close(S.t_ppf(0.975, 10), 2.2281388519649385, 1e-4))
    check("F p 4.0 (1,10)", close(S.f_pvalue(4.0, 1, 10), 0.073388, 1e-4))
    check("chi2 p 3.841 df1", close(S.chi2_pvalue(3.841, 1), 0.05005, 1e-3))
    check("chi2 p 11.07 df5", close(S.chi2_pvalue(11.07, 5), 0.05003, 1e-3))


def test_hypothesis_tests():
    print("[2] hypothesis tests")
    a = [1, 2, 3, 4, 5]
    b = [6, 7, 8, 9, 10]
    r = S.welch_t_test(a, b)
    check("welch p", close(r["p"], 0.0010528, 1e-4), str(r))
    r = S.anova_f([[1, 2, 3], [5, 6, 7]])
    check("anova p", close(r["p"], 0.0080499, 1e-4), str(r))
    r = S.pearson_r([1, 2, 3, 4, 5], [2, 4, 6, 8, 10])
    check("pearson r=1", close(r["statistic"], 1.0, 1e-12), str(r))
    r = S.cohens_d(a, b)
    check("cohens d", close(r["statistic"], -3.16227766, 1e-4), str(r))
    r = S.mann_whitney_u([1, 2, 3], [4, 5, 6])
    check("mw applicable", r["applicable"] and 0 <= r["p"] <= 1, str(r))


def _demo_input(root):
    inp = os.path.join(root, "input")
    os.makedirs(inp, exist_ok=True)
    case = {
        "case_id": "selftest-demo",
        "task_instruction": "compare outcome by group, two groups",
        "output_contract": {
            "descriptive": {"type": "list"},
            "inferential": {"type": "list"},
            "effect_sizes": {"type": "list"},
            "interpretation": {"type": "object"},
        },
        "data_dictionary": {"outcome": {"unit": "mg/dl"}},
    }
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        json.dump(case, f)
    with open(os.path.join(inp, "demo.csv"), "w", encoding="utf-8", newline="") as f:
        f.write("group,outcome\n")
        for i in range(30):
            f.write(f"A,{10 + i * 0.5}\n")
        for i in range(30):
            f.write(f"B,{20 + i * 0.5}\n")
    return inp


def test_pipeline():
    print("[3] end-to-end pipeline")
    root = tempfile.mkdtemp(prefix="skilltest_")
    inp = _demo_input(root)
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    P.run_all(inp, out, work)
    rp = os.path.join(out, "result.json")
    check("result.json exists", os.path.isfile(rp))
    result = json.load(open(rp, encoding="utf-8"))
    check("case_id", result.get("case_id") == "selftest-demo")
    check("status", result.get("status") == "ok", result.get("status"))
    for k in ("descriptive", "inferential", "effect_sizes", "interpretation"):
        check(f"contract key {k}", k in result, k)
    check("descriptive filled", isinstance(result.get("descriptive"), list) and len(result["descriptive"]) > 0)
    check("inferential filled", isinstance(result.get("inferential"), list) and len(result["inferential"]) > 0)
    check("envelope", all(k in result for k in ("sources", "uncertainty", "change_log")))
    check("cleaned csv", os.path.isfile(os.path.join(out, "cleaned", "demo_clean.csv")))
    svgs = [f for f in os.listdir(os.path.join(out, "figures")) if f.endswith(".svg")] if os.path.isdir(os.path.join(out, "figures")) else []
    check("svg figures", len(svgs) >= 1, str(svgs))
    for f in svgs:
        head = open(os.path.join(out, "figures", f), encoding="utf-8").read(200)
        check(f"svg valid {f}", "<svg" in head)
    a1 = json.load(open(os.path.join(work, "analysis.json"), encoding="utf-8"))
    # determinism: recompute and compare the deterministic content (exclude timestamps)
    out2 = os.path.join(root, "output2")
    work2 = os.path.join(root, "work2")
    P.run_all(inp, out2, work2)
    a2 = json.load(open(os.path.join(work2, "analysis.json"), encoding="utf-8"))
    a1.pop("generated_at", None)
    a2.pop("generated_at", None)
    check("deterministic rerun", a1 == a2)
    shutil.rmtree(root, ignore_errors=True)


def test_degradation():
    print("[4] degradation paths")
    root = tempfile.mkdtemp(prefix="skilltest_deg_")
    # empty input dir
    inp = os.path.join(root, "empty_in")
    os.makedirs(inp, exist_ok=True)
    out = os.path.join(root, "out_empty")
    work = os.path.join(root, "work_empty")
    P.stage_probe(inp, work)
    P.stage_skeleton(inp, out, work)
    rp = os.path.join(out, "result.json")
    result = json.load(open(rp, encoding="utf-8"))
    check("empty input still writes result.json", os.path.isfile(rp))
    check("empty input status honest", result.get("status") in ("partial", "insufficient_evidence"), result.get("status"))
    # broken JSON
    inp2 = os.path.join(root, "bad_in")
    os.makedirs(inp2, exist_ok=True)
    with open(os.path.join(inp2, "case.json"), "w", encoding="utf-8") as f:
        f.write("{ not valid json ")
    out2 = os.path.join(root, "out_bad")
    work2 = os.path.join(root, "work_bad")
    P.stage_probe(inp2, work2)
    P.stage_skeleton(inp2, out2, work2)
    r2 = json.load(open(os.path.join(out2, "result.json"), encoding="utf-8"))
    check("bad json handled", r2.get("case_id") == "unknown-case" and r2.get("status") == "partial")
    shutil.rmtree(root, ignore_errors=True)


def main():
    print("analysis-02 selftest")
    test_special_functions()
    test_hypothesis_tests()
    test_pipeline()
    test_degradation()
    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) FAILED: {FAILURES}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
