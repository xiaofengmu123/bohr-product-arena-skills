#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for the sci-data-cleaning-contract skill.

Run from the skill root (standard library only):

    python -B scripts/selftest.py

It builds synthetic inputs under a temporary directory, runs the three CLI
phases (bootstrap / full / selfcheck), and asserts deterministic,
exit-code-0 behavior on: empty input, a broken case.json, and a minimal valid
case with a tabular source plus dictionary/rules/workorder.  No scores are
invented; this script only verifies that the pipeline degrades honestly.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "scripts", "main.py")


def run(phase, input_dir, output_dir, work_dir):
    return subprocess.run(
        [sys.executable, "-B", MAIN, "--phase", phase,
         "--input", input_dir, "--output", output_dir, "--work", work_dir],
        capture_output=True, text=True, cwd=ROOT)


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(1)


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def make_case(tmp, case_json=None, data=None, extra=None):
    inp = os.path.join(tmp, "input")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    os.makedirs(inp, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    if case_json is not None:
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write(case_json if isinstance(case_json, str)
                     else json.dumps(case_json, ensure_ascii=False))
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# task\n")
    if data:
        with open(os.path.join(inp, data[0]), "w", encoding="utf-8") as fh:
            fh.write(data[1])
    if extra:
        for name, content in extra.items():
            with open(os.path.join(inp, name), "w", encoding="utf-8") as fh:
                fh.write(content)
    return inp, out, work


def main():
    tmp = tempfile.mkdtemp(prefix="scicln-contract-selftest-")
    try:
        # 1) empty input directory -> honest skeleton, exit 0
        inp, out, work = make_case(os.path.join(tmp, "empty"), case_json=None)
        r = run("bootstrap", inp, out, work)
        check("empty-input bootstrap exit 0", r.returncode == 0)
        res = read_json(os.path.join(out, "result.json"))
        check("empty-input honest status", res.get("status") in
              ("input_error", "insufficient_evidence", "partial"))
        check("empty-input case_id present", "case_id" in res)

        # 2) broken case.json -> still exit 0, fallback case_id, skeleton valid
        inp, out, work = make_case(os.path.join(tmp, "badjson"),
                                   case_json="{not valid json")
        r = run("bootstrap", inp, out, work)
        check("broken-json bootstrap exit 0", r.returncode == 0)
        res = read_json(os.path.join(out, "result.json"))
        check("broken-json has envelope fields",
              all(k in res for k in ("case_id", "status", "sources",
                                     "uncertainty", "change_log")))

        # 3) minimal valid case with output_contract + tabular data
        contract = {
            "cleaned_table": None,
            "quality_report": None,
            "evidence_map": None,
            "summary_stats": None,
            "notes": [],
            "unknown_slot": {},
        }
        case = {"case_id": "case-demo-01", "output_contract": contract}
        data = ("measurements.tsv",
                "sample_id\tanalyte\tvalue\tunit\n"
                "S-01\tPb\t0.05\tmg/L\n"
                "S-02\tPb\tnd\tmg/L\n"
                "S-03\tPb\t<0.01\tmg/L\n"
                "S-04\tPb\t-999\tmg/L\n"
                "S-05\tPb\t0\tmg/L\n"
                "S-05\tPb\t0\tmg/L\n")
        extra = {
            "data_dictionary.tsv": "field\tmeaning\tunit\nvalue\tconcentration\tmg/L\n",
            "qc_rules.tsv": "metric\tunit\tlower\tupper\nPb\tmg/L\t0\t10\n",
            "workorder.tsv": "step\taction\n1\tread dictionary\n",
        }
        inp, out, work = make_case(os.path.join(tmp, "minimal"),
                                   case_json=case, data=data, extra=extra)
        r = run("bootstrap", inp, out, work)
        check("minimal bootstrap exit 0", r.returncode == 0)
        res = read_json(os.path.join(out, "result.json"))
        check("minimal bootstrap backfills all contract keys",
              all(k in res for k in contract))
        check("minimal bootstrap defaults notes->[]", res.get("notes") == [])
        check("minimal bootstrap defaults unknown_slot->{}", res.get("unknown_slot") == {})

        r = run("full", inp, out, work)
        check("minimal full exit 0", r.returncode == 0)
        check("minimal full cleaned_data.csv exists",
              os.path.exists(os.path.join(out, "cleaned_data.csv")))
        check("minimal full missing_flags.csv exists",
              os.path.exists(os.path.join(out, "missing_flags.csv")))
        check("minimal full data_profile.json exists",
              os.path.exists(os.path.join(out, "data_profile.json")))
        prof = read_json(os.path.join(out, "data_profile.json"))
        check("minimal profile counts rows", prof.get("n_rows") == 6)
        check("minimal exact duplicate detected",
              prof.get("n_exact_duplicate_rows") == 1)

        res = read_json(os.path.join(out, "result.json"))
        check("minimal full fills contract artifact keys",
              bool(res.get("cleaned_table")) and bool(res.get("quality_report")))
        audit = res.get("contract_audit", {})
        check("minimal contract_audit filled>0", len(audit.get("filled", [])) > 0)

        r = run("selfcheck", inp, out, work)
        check("minimal selfcheck exit 0", r.returncode == 0)
        check("minimal selfcheck reports pass", "SELFCHECK_STATUS=pass" in r.stdout)

        # 4) determinism: re-run full, data_profile must be identical
        run("full", inp, out, work)
        prof2 = read_json(os.path.join(out, "data_profile.json"))
        check("determinism data_profile identical", prof == prof2)

        print("selftest: all checks passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
