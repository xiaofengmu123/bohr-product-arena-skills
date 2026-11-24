#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone self-test for the sci-data-cleaning Skill package.

Run from the package root (or anywhere) with:

    python scripts/selftest.py

It builds a temporary synthetic demo under the system temp directory, exercises
the bootstrap / full / selfcheck phases, verifies determinism (two runs give
the same data output), and exercises the degradation paths (empty input, bad
JSON). It never invents scores; it only reports whether the package behaves as
documented.

Standard library only. Exit code 0 means "test completed"; individual check
results are printed and summarized (failures are also reported in the printed
summary, not only via exit code, so CI logs stay readable).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run_main(args, cwd):
    return subprocess.run(
        [sys.executable, MAIN] + args, cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)


def make_case(input_dir, contract=None):
    os.makedirs(input_dir, exist_ok=True)
    contract = contract or {
        "cleaned_table": None,
        "quality_report": None,
        "statistics_summary": None,
        "evidence_map": None,
    }
    case = {"case_id": "demo-case-001", "output_contract": contract}
    with open(os.path.join(input_dir, "case.json"), "w", encoding="utf-8") as f:
        json.dump(case, f, ensure_ascii=False)
    with open(os.path.join(input_dir, "task.md"), "w", encoding="utf-8") as f:
        f.write("# demo task\nClean the table and keep evidence.\n")
    # A dirty table: -999 sentinel, "未检出", "低于检出限", true zero, units.
    with open(os.path.join(input_dir, "samples.csv"), "w", encoding="utf-8") as f:
        f.write("sample_id,conc_mg_l,temp_c,note\n")
        f.write("A1,12.5,25.0,ok\n")
        f.write("A2,-999,24.8,entry missing\n")
        f.write("A3,未检出,24.9,not detected\n")
        f.write("A4,低于检出限,,\n")
        f.write("A5,0,26.1,true zero\n")
        f.write("A6,1.2e2,26.5,scientific\n")


def load_result(output_dir):
    with open(os.path.join(output_dir, "result.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL") + "  " + name + (("  -> " + detail) if detail else ""))
    return bool(cond)


def main():
    root = tempfile.mkdtemp(prefix="sci-clean-selftest-")
    results = []
    try:
        demo_in = os.path.join(root, "demo", "input")
        demo_out = os.path.join(root, "demo", "output")
        demo_work = os.path.join(root, "demo", "work")
        make_case(demo_in)

        # 1. --help works.
        r = run_main(["--help"], root)
        results.append(check("main.py --help exits 0", r.returncode == 0))

        # 2. bootstrap -> partial skeleton with contract keys echoed.
        r = run_main(["--phase", "bootstrap", "--input", demo_in,
                      "--output", demo_out, "--work", demo_work], root)
        results.append(check("bootstrap exits 0", r.returncode == 0,
                             r.stderr.strip()[:80]))
        res = load_result(demo_out)
        results.append(check("bootstrap writes partial skeleton",
                             res.get("status") == "partial"))
        results.append(check("contract keys echoed",
                             all(k in res for k in
                                 ("cleaned_table", "quality_report",
                                  "statistics_summary", "evidence_map"))))
        results.append(check("bookkeeping keys present",
                             all(k in res for k in
                                 ("case_id", "sources", "uncertainty",
                                  "change_log", "limitations"))))

        # 3. full -> artifacts + profile.
        r = run_main(["--phase", "full", "--input", demo_in,
                      "--output", demo_out, "--work", demo_work], root)
        results.append(check("full exits 0", r.returncode == 0,
                             r.stderr.strip()[:80]))
        res = load_result(demo_out)
        arts = res.get("artifacts") or {}
        results.append(check("cleaned_data artifact written",
                             bool(arts.get("cleaned_data")) and
                             os.path.exists(arts["cleaned_data"])))
        results.append(check("quality_report artifact written",
                             bool(arts.get("quality_report")) and
                             os.path.exists(arts["quality_report"])))

        # 4. determinism: run full twice, data artifacts byte-identical.
        with open(arts["cleaned_data"], "rb") as f:
            first = f.read()
        run_main(["--phase", "full", "--input", demo_in,
                  "--output", demo_out, "--work", demo_work], root)
        with open(arts["cleaned_data"], "rb") as f:
            second = f.read()
        results.append(check("deterministic cleaned_data", first == second))

        # 5. selfcheck.
        r = run_main(["--phase", "selfcheck", "--input", demo_in,
                      "--output", demo_out, "--work", demo_work], root)
        results.append(check("selfcheck exits 0", r.returncode == 0,
                             r.stderr.strip()[:80]))
        sc = os.path.join(demo_out, "selfcheck_report.json")
        results.append(check("selfcheck report written", os.path.exists(sc)))
        if os.path.exists(sc):
            with open(sc, "r", encoding="utf-8") as f:
                sr = json.load(f)
            results.append(check("selfcheck report has checks",
                                 isinstance(sr.get("checks"), list)))

        # 6. degradation: empty input dir -> insufficient_evidence, no crash.
        empty_in = os.path.join(root, "empty", "input")
        empty_out = os.path.join(root, "empty", "output")
        empty_work = os.path.join(root, "empty", "work")
        os.makedirs(empty_in, exist_ok=True)
        r = run_main(["--phase", "full", "--input", empty_in,
                      "--output", empty_out, "--work", empty_work], root)
        results.append(check("empty input exits 0", r.returncode == 0))
        res = load_result(empty_out)
        results.append(check("empty input -> insufficient_evidence",
                             res.get("status") in
                             ("insufficient_evidence", "input_error")))

        # 7. degradation: bad JSON case.json still yields a loadable result.
        bad_in = os.path.join(root, "badjson", "input")
        bad_out = os.path.join(root, "badjson", "output")
        bad_work = os.path.join(root, "badjson", "work")
        os.makedirs(bad_in, exist_ok=True)
        with open(os.path.join(bad_in, "case.json"), "w", encoding="utf-8") as f:
            f.write("{ this is not json")
        r = run_main(["--phase", "bootstrap", "--input", bad_in,
                      "--output", bad_out, "--work", bad_work], root)
        results.append(check("bad JSON exits 0", r.returncode == 0))
        res = load_result(bad_out)
        results.append(check("bad JSON still yields result.json",
                             isinstance(res, dict) and res.get("case_id")))

    finally:
        shutil.rmtree(root, ignore_errors=True)

    passed = sum(results)
    total = len(results)
    print("\nselftest summary: %d/%d checks passed" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
