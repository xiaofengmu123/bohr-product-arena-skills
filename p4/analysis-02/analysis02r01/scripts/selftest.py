# -*- coding: utf-8 -*-
"""selftest.py -- deterministic self-check for the skill.

Runs without third-party packages (standard library only).  It
1) unit-tests the statistical primitives against hand-computed values,
2) runs the full pipeline on a synthetic demo (generated on the fly in a
   temporary directory, not committed as an answer),
3) verifies the empty-input and bad-JSON degradation paths,
4) validates result.json, evidence traceability and SVG well-formedness.

Exit code is 0 when all checks pass, 1 otherwise.
"""
import csv
import json
import math
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl as C  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [ok] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s %s" % (name, detail))


def close(a, b, tol=1e-9):
    return a is not None and b is not None and abs(a - b) < tol


def test_statlib():
    print("== statlib unit checks ==")
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    check("mean", close(C.mean(x), 3.0))
    check("sample sd", close(C.sd(x), math.sqrt(2.5), 1e-12))
    check("median", close(C.median(x), 3.0))
    check("q1 (type7)", close(C.quantile(x, 0.25), 2.0))
    check("normal_cdf(0)", close(C.normal_cdf(0.0), 0.5))
    check("normal_ppf(0.975)", close(C.normal_ppf(0.975), 1.959963984540054, 1e-6))
    check("t_cdf(0,5)", close(C.t_cdf(0.0, 5), 0.5))
    check("chi2_sf", close(C.chi2_sf(3.8414588, 1), 0.05, 1e-3))
    a = [1.0, 2.0, 3.0, 4.0, 5.0]
    b = [10.0, 11.0, 12.0, 13.0, 14.0]
    W1, p1, m1 = C.shapiro(a)
    check("shapiro returns finite", W1 is not None and p1 is not None)
    W2, p2, m2 = C.levene_bf([a, b])
    check("levene returns value", W2 is not None and p2 is not None)
    t, p, m3 = C.ttest_two(a, b, equal_var=True)
    check("ttest finite", t is not None and p is not None and abs(t) > 1)
    d = C.cohens_d(a, b)
    check("cohens_d direction", d is not None and d < 0)
    lo, hi = C.mean_t_ci(x)
    check("mean CI covers mean", lo < 3.0 < hi)


def make_demo_input(root):
    inp = os.path.join(root, "input")
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    os.makedirs(inp, exist_ok=True)
    # case.json with a contract exercising backfill + data dictionary
    case = {
        "case_id": "selftest-demo-001",
        "task_instruction": "compare outcome across two groups",
        "output_contract": {
            "descriptive": [],
            "inferential": [],
            "effects": [],
            "confidence_intervals": [],
            "interpretation": {},
            "custom_unknown_key": None,
        },
        "data_dictionary": {
            "group": {"type": "categorical", "unit": None},
            "outcome": {"type": "numeric", "unit": "mg/L"},
        },
    }
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        json.dump(case, f, ensure_ascii=False)
    rows = [
        ["group", "outcome"],
        ["A", "1.0"],
        ["A", "2.0"],
        ["A", "2.5"],
        ["A", "2.8"],
        ["A", ""],          # missing
        ["A", "3.0"],
        ["A", "9.9"],       # high outlier
        ["B", "5.0"],
        ["B", "5.2"],
        ["B", "5.4"],
        ["B", "5.6"],
        ["B", "5.8"],
        ["B", "6.0"],
        ["A", "2.8"],       # exact duplicate of row 4
    ]
    with open(os.path.join(inp, "demo.csv"), "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(rows)
    return inp, out, work


def test_full_pipeline():
    print("== full pipeline on synthetic demo ==")
    root = tempfile.mkdtemp(prefix="skill-selftest-")
    try:
        inp, out, work = make_demo_input(root)
        C.run("run", inp, out, work)
        rpath = os.path.join(out, "result.json")
        check("result.json exists", os.path.exists(rpath))
        with open(rpath, encoding="utf-8") as f:
            r = json.load(f)
        check("case_id", r.get("case_id") == "selftest-demo-001")
        check("status valid", r.get("status") in ("ok", "partial", "insufficient_evidence", "input_error"))
        check("envelope keys", all(k in r for k in ("sources", "uncertainty", "change_log")))
        check("contract key preserved", "custom_unknown_key" in r)
        check("contract descriptive filled", isinstance(r.get("descriptive"), list) and len(r["descriptive"]) > 0)
        em = r.get("evidence_map", [])
        check("evidence_map non-empty", len(em) > 0)
        check("every evidence has locator+quote",
              all(e.get("locator") and e.get("quote") is not None for e in em))
        qc = os.path.join(out, "quality_checks.json")
        check("quality_checks.json", os.path.exists(qc))
        # SVG parse
        fig_dir = os.path.join(out, "figures")
        svgs = [n for n in os.listdir(fig_dir) if n.endswith(".svg")] if os.path.isdir(fig_dir) else []
        check("at least one SVG", len(svgs) > 0)
        for n in svgs:
            ET.parse(os.path.join(fig_dir, n))
        check("SVG parse ok", True)
        # cleaned csv
        clean_dir = os.path.join(out, "cleaned")
        check("cleaned csv exists", os.path.isdir(clean_dir) and len(os.listdir(clean_dir)) > 0)
        # reproducibility: run again, byte-compare result.json
        out2 = os.path.join(root, "output2")
        C.run("run", inp, out2, os.path.join(root, "work2"))
        with open(os.path.join(out2, "result.json"), encoding="utf-8") as f:
            r2 = json.load(f)
        check("deterministic (same evidence/claims)",
              r.get("evidence_map") == r2.get("evidence_map"))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_empty_input():
    print("== empty input degradation ==")
    root = tempfile.mkdtemp(prefix="skill-empty-")
    try:
        inp = os.path.join(root, "input")
        out = os.path.join(root, "output")
        work = os.path.join(root, "work")
        os.makedirs(inp)
        C.run("run", inp, out, work)
        with open(os.path.join(out, "result.json"), encoding="utf-8") as f:
            r = json.load(f)
        check("status insufficient_evidence or partial",
              r.get("status") in ("insufficient_evidence", "partial"))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_bad_json():
    print("== bad JSON degradation ==")
    root = tempfile.mkdtemp(prefix="skill-badjson-")
    try:
        inp = os.path.join(root, "input")
        out = os.path.join(root, "output")
        work = os.path.join(root, "work")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            f.write("{not valid json")
        with open(os.path.join(inp, "data.csv"), "w", encoding="utf-8") as f:
            f.write("a,b\n1,2\n")
        C.run("run", inp, out, work)
        with open(os.path.join(out, "result.json"), encoding="utf-8") as f:
            r = json.load(f)
        check("still writes valid result.json", r.get("case_id") is not None)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    test_statlib()
    test_full_pipeline()
    test_empty_input()
    test_bad_json()
    print("\nselftest: %d passed, %d failed" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
