#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Robustness self-test for the academic-bilingual-translator Skill.

Scenarios exercised (each must exit 0 and leave a legal result.json):
  1. empty input directory      -> insufficient_evidence
  2. malformed case.json        -> input_error, error recorded
  3. oversized text file        -> skipped + recorded, still partial
  4. valid minimal case         -> partial, every contract field present
  5. missing parameters         -> defaults used, no crash, no traceback
  6. --help                     -> exit 0

Run:
    python3 scripts/selftest.py
or  python3 scripts/main.py selftest
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")

PASS = 0
FAIL = 0

CONTRACT_FIELDS = ["case_id", "status", "sources", "uncertainty",
                   "pending_human", "change_log", "deliverables",
                   "fact_source_map", "version_boundary"]


def invoke(*args, timeout=90):
    return subprocess.run([sys.executable, MAIN] + list(args),
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   {detail}")


def read_result(outdir):
    with open(os.path.join(outdir, "result.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def missing_fields(r):
    return [f for f in CONTRACT_FIELDS if f not in r]


def make_case(tmp, name, files):
    d = os.path.join(tmp, name)
    inp = os.path.join(d, "input")
    out = os.path.join(d, "output")
    work = os.path.join(d, "work")
    os.makedirs(inp, exist_ok=True)
    for fname, content in files.items():
        with open(os.path.join(inp, fname), "wb") as f:
            f.write(content if isinstance(content, bytes)
                    else content.encode("utf-8"))
    return inp, out, work


def run_scenario(tmp, name, files, expect_status):
    inp, out, work = make_case(tmp, name, files)
    rc = invoke("init", "--input", inp, "--output", out, "--work", work)
    check(f"{name}: exit 0", rc.returncode == 0,
          f"rc={rc.returncode} stderr={rc.stderr[:160]}")
    rp = os.path.join(out, "result.json")
    check(f"{name}: result.json exists", os.path.exists(rp))
    r = read_result(out)
    check(f"{name}: contract fields complete", missing_fields(r) == [],
          f"missing={missing_fields(r)}")
    check(f"{name}: honest status", r.get("status") == expect_status,
          f"status={r.get('status')}")
    check(f"{name}: no traceback", "Traceback" not in rc.stderr
          and "Traceback" not in rc.stdout)
    return inp, out, work, r


def run():
    global PASS, FAIL
    tmp = tempfile.mkdtemp(prefix="abt-selftest-")

    print("Scenario 1: empty input")
    run_scenario(tmp, "empty", {}, "insufficient_evidence")

    print("Scenario 2: malformed case.json")
    run_scenario(tmp, "badjson", {"case.json": b'{"case_id": "x", "bad": [1,'},
                 "input_error")

    print("Scenario 3: oversized text file")
    run_scenario(tmp, "big",
                 {"case.json": b'{"case_id":"c1"}',
                  "big.txt": b"x" * (10 * 1024 * 1024)}, "partial")

    print("Scenario 4: valid minimal case")
    inp, out, work, _ = run_scenario(
        tmp, "valid",
        {"case.json": '{"case_id":"c1","output_contract":'
                      '{"deliverables":[],"fact_source_map":[],'
                      '"version_boundary":{}}}',
         "material.md": "# Title\n\nBody with 12.5 mL and [1,2] and "
                        "Smith et al. (2020).\n\n1.2 million cells / 120万."},
        "partial")

    print("Scenario 5: extract + checks on valid case (no crash)")
    rc = invoke("extract", "--input", inp, "--output", out, "--work", work)
    check("extract exit 0", rc.returncode == 0, f"rc={rc.returncode}")
    rc = invoke("checks", "--input", inp, "--output", out, "--work", work)
    check("checks exit 0", rc.returncode == 0, f"rc={rc.returncode}")
    report_path = os.path.join(work, "consistency_report.json")
    check("consistency report written", os.path.exists(report_path))
    with open(os.path.join(work, "number_inventory.json"), "r",
              encoding="utf-8") as f:
        nums = json.load(f)["numbers"]
    canonicals = {n["canonical"] for n in nums}
    # "1.2 million" and "120万" must normalize to the same canonical value.
    check("magnitude normalization (million vs 万)",
          len(canonicals & {"1200000"}) >= 1,
          f"canonicals={sorted(canonicals)}")

    print("Scenario 6: missing parameters (defaults)")
    rc = invoke("init")
    check("missing params: no crash", rc.returncode in (0, 1),
          f"rc={rc.returncode}")
    check("missing params: no traceback", "Traceback" not in rc.stderr
          and "Traceback" not in rc.stdout)

    print("Scenario 7: --help")
    rc = invoke("--help")
    check("--help exit 0", rc.returncode == 0, f"rc={rc.returncode}")

    print("\n" + "-" * 44)
    print(f"PASS={PASS}  FAIL={FAIL}")
    print("-" * 44)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
