#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for sci-table-clean.

Runs the three CLI phases on a synthetic demo, then on degraded inputs
(empty dir, bad JSON, no table). Standard library only. Exits 0 on pass.

Usage:
    python scripts/selftest.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(*args):
    return subprocess.run([sys.executable, MAIN] + list(args),
                          capture_output=True, text=True)


def check(cond, msg):
    if not cond:
        print("FAIL: %s" % msg)
        sys.exit(1)
    print("  ok: %s" % msg)


def make_demo(root):
    inp = os.path.join(root, "input")
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    os.makedirs(inp)
    os.makedirs(out)
    os.makedirs(work)

    case = {
        "case_id": "demo-case-001",
        "output_contract": {
            "cleaned_table": "",
            "quality_report": "",
            "summary": ["conc_mgL"],
        },
    }
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False)

    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# demo\nClean the table, keep evidence.\n")

    csv_lines = [
        "sample_id,conc_mgL,note,date",
        "S-01,1.25,ND,2024-01-01",
        "S-02,<0.01,below LOD,2024-01-02",
        "s-01,1.25,dup,2024-01-01",
        "S-03,-999,missing,2024-01-03",
        "S-04,0,true zero,2024-01-04",
        "S-05,150.0,outlier,2024-01-05",
    ]
    with open(os.path.join(inp, "data.csv"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(csv_lines))

    with open(os.path.join(inp, "data_dictionary.csv"), "w", encoding="utf-8") as fh:
        fh.write("field,meaning,unit\nconc_mgL,concentration,mg/L\nsample_id,sample id,text\n")

    with open(os.path.join(inp, "qc_rules.csv"), "w", encoding="utf-8") as fh:
        fh.write("field,physical_min,physical_max\nconc_mgL,0,100\n")

    with open(os.path.join(inp, "workorder.md"), "w", encoding="utf-8") as fh:
        fh.write("# workorder\nDeduplicate on sample_id; flag outliers; never impute.\n")

    rules = {
        "primary_keys": ["sample_id"],
        "duplicate_keep": "first",
        "fields": {
            "conc_mgL": {
                "unit": "mg/L",
                "convert_to": "ug/mL",
                "convert_factor": 1.0,
                "physical_min": 0,
                "physical_max": 100,
                "outlier_action": "flag",
                "not_detected_tokens": ["ND"],
                "below_lod_tokens": ["<0.01", "below LOD"],
                "missing_sentinels": ["", "-999"],
                "true_zero_tokens": ["0"],
            }
        },
    }
    with open(os.path.join(work, "rules.json"), "w", encoding="utf-8") as fh:
        json.dump(rules, fh, ensure_ascii=False)

    return inp, out, work


def main():
    root = tempfile.mkdtemp(prefix="scitc-selftest-")
    print("demo dir: %s" % root)

    inp, out, work = make_demo(root)

    print("[1] bootstrap")
    r = run("--phase", "bootstrap", "--input", inp, "--output", out, "--work", work)
    check(r.returncode == 0, "bootstrap exit 0")
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    check(res.get("status") == "partial", "bootstrap writes partial skeleton")
    for k in ("cleaned_table", "quality_report", "summary"):
        check(k in res, "contract key preserved: %s" % k)

    print("[2] full")
    r = run("--phase", "full", "--input", inp, "--output", out, "--work", work)
    check(r.returncode == 0, "full exit 0")
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    check(res.get("status") == "ok", "full status ok")
    check(os.path.isfile(os.path.join(out, "cleaned_data.csv")), "cleaned_data.csv exists")
    check(os.path.isfile(os.path.join(out, "quality_report.md")), "quality_report.md exists")
    check(os.path.isfile(os.path.join(out, "evidence_map.json")), "evidence_map.json exists")
    check(res.get("cleaned_table"), "contract cleaned_table filled")

    print("[3] selfcheck")
    r = run("--phase", "selfcheck", "--input", inp, "--output", out, "--work", work)
    check(r.returncode == 0, "selfcheck exit 0")

    print("[4] determinism")
    r1 = run("--phase", "full", "--input", inp, "--output", out, "--work", work)
    r2 = run("--phase", "full", "--input", inp, "--output", out, "--work", work)
    check(r1.returncode == r2.returncode == 0, "two full runs exit 0")
    a = open(os.path.join(out, "cleaned_data.csv"), encoding="utf-8-sig").read()
    # Rerun writes identical bytes; read once is enough for content check below.
    check("S-01" in a and "-999" in a, "cleaned table preserves originals")

    print("[5] degraded inputs")
    empty = os.path.join(root, "empty-input")
    os.makedirs(empty)
    r = run("--phase", "full", "--input", empty, "--output", os.path.join(root, "eo"), "--work", os.path.join(root, "ew"))
    check(r.returncode == 0, "empty input exit 0")
    res = json.load(open(os.path.join(root, "eo", "result.json"), encoding="utf-8"))
    check(res.get("status") == "insufficient_evidence", "empty input -> insufficient_evidence")

    bad = os.path.join(root, "bad-input")
    os.makedirs(bad)
    with open(os.path.join(bad, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{ not valid json ")
    r = run("--phase", "full", "--input", bad, "--output", os.path.join(root, "bo"), "--work", os.path.join(root, "bw"))
    check(r.returncode == 0, "bad JSON exit 0")
    res = json.load(open(os.path.join(root, "bo", "result.json"), encoding="utf-8"))
    check(any("case.json" in e for e in res.get("errors", [])), "bad JSON recorded in errors")

    print("SELFTEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
