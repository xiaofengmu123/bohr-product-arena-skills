#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest -- end-to-end verification of the skill pipeline.

Runs the deterministic analysis on synthetic inputs and checks:
  - result.json is always valid (case_id + contract keys + status/sources/
    uncertainty/change_log);
  - empty input returns insufficient_evidence with exit code 0;
  - unparseable case.json is recorded as input_error with exit code 0;
  - two fresh runs over the same input produce identical analysis values,
    CSVs and SVG files (timestamp fields excluded);
  - CSV/TSV snapshots are loaded and analysed like SQLite tables.
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import main as pipeline

FAILED = False


def check(cond, msg):
    global FAILED
    if cond:
        print("PASS  %s" % msg)
    else:
        print("FAIL  %s" % msg)
        FAILED = True


def make_case(root):
    inp = os.path.join(root, "input")
    os.makedirs(inp, exist_ok=True)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write("# Synthetic case\nCompute the mean value and list group counts.\n")
    contract = {
        "mean_value": {"type": "number"},
        "group_counts": {"type": "array"},
        "total_rows": {"type": "number"},
    }
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        json.dump({"case_id": "synthetic-001", "output_contract": contract}, f)
    db = os.path.join(inp, "data.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE samples (id INTEGER, grp TEXT, value REAL, flag TEXT)")
    conn.executemany("INSERT INTO samples VALUES (?,?,?,?)", [
        (1, "a", 1.0, "0"),
        (2, "b", 2.0, "1"),
        (3, "a", 3.0, "1"),
        (4, "b", None, "0"),
        (5, "a", 5.0, "1"),
        (2, "b", 2.0, "1"),  # exact duplicate of row id=2
    ])
    conn.execute("CREATE TABLE labels (id INTEGER, name TEXT)")
    conn.executemany("INSERT INTO labels VALUES (?,?)", [(1, "x"), (2, "y"), (3, "z")])
    conn.commit()
    conn.close()
    return inp


def run(inp, out, work):
    for d in (out, work):
        if os.path.exists(d):
            shutil.rmtree(d)
    os.makedirs(out, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    return pipeline.main(["--input", inp, "--output", out, "--work", work])


def load_result(out):
    with open(os.path.join(out, "result.json"), encoding="utf-8") as f:
        return json.load(f)


def det_snapshot(r):
    return {
        "case_id": r.get("case_id"),
        "status": r.get("status"),
        "analysis": r.get("analysis"),
        "query_log": r.get("query_log"),
        "answers": r.get("answers"),
        "sources": r.get("sources"),
        "uncertainty": r.get("uncertainty"),
        "errors": r.get("errors"),
        "degradations": r.get("degradations"),
        "limitations": r.get("limitations"),
        "pending_human": r.get("pending_human"),
    }


def files_identical(out1, out2, names):
    for n in names:
        p1 = os.path.join(out1, n)
        p2 = os.path.join(out2, n)
        if os.path.exists(p1) != os.path.exists(p2):
            return False, n
        if os.path.exists(p1):
            with open(p1, "rb") as f:
                a = f.read()
            with open(p2, "rb") as f:
                b = f.read()
            if a != b:
                return False, n
    return True, None


def main():
    root = tempfile.mkdtemp(prefix="skill_selftest_")
    try:
        inp = make_case(root)

        out1 = os.path.join(root, "out1")
        work1 = os.path.join(root, "work1")
        rc = run(inp, out1, work1)
        check(rc == 0, "normal case exits 0 (got %r)" % rc)
        r1 = load_result(out1)
        check(r1.get("case_id") == "synthetic-001", "case_id preserved")
        check(r1.get("status") in pipeline.STATUSES, "valid status value")
        for k in ("mean_value", "group_counts", "total_rows"):
            check(k in r1, "contract key %r present" % k)
        for k in ("sources", "uncertainty", "change_log"):
            check(k in r1 and isinstance(r1[k], list), "%s present as list" % k)
        check(os.path.exists(os.path.join(out1, "summary_stats.csv")),
              "summary_stats.csv written")
        check(os.path.exists(os.path.join(out1, "report.md")), "report.md written")

        out2 = os.path.join(root, "out2")
        work2 = os.path.join(root, "work2")
        run(inp, out2, work2)
        r2 = load_result(out2)
        check(det_snapshot(r1) == det_snapshot(r2),
              "two fresh runs: identical analysis snapshot")
        same, name = files_identical(out1, out2, [
            "summary_stats.csv", "data_quality.csv", "correlation_matrix.csv",
            "schema.json", "value_counts.json", "evidence_map.json",
            "figures/summary_means.svg"])
        check(same, "all artifact files byte-identical across runs"
              if same else "artifact %s differs across runs" % name)

        empty_in = os.path.join(root, "empty_in")
        os.makedirs(empty_in, exist_ok=True)
        empty_out = os.path.join(root, "empty_out")
        empty_work = os.path.join(root, "empty_work")
        rc = run(empty_in, empty_out, empty_work)
        re_ = load_result(empty_out)
        check(rc == 0 and re_.get("status") == "insufficient_evidence",
              "empty input -> insufficient_evidence (exit 0), got %r"
              % re_.get("status"))

        bad_in = os.path.join(root, "bad_in")
        os.makedirs(bad_in, exist_ok=True)
        with open(os.path.join(bad_in, "case.json"), "w", encoding="utf-8") as f:
            f.write("{ this is not json")
        bad_out = os.path.join(root, "bad_out")
        bad_work = os.path.join(root, "bad_work")
        rc = run(bad_in, bad_out, bad_work)
        rb = load_result(bad_out)
        check(rc == 0 and rb.get("status") == "input_error",
              "bad case.json -> input_error (exit 0), got %r" % rb.get("status"))
        check(any("case.json" in (e.get("message") or "") for e in rb["errors"]),
              "bad case.json recorded in errors")

        csv_in = os.path.join(root, "csv_in")
        os.makedirs(csv_in, exist_ok=True)
        with open(os.path.join(csv_in, "case.json"), "w", encoding="utf-8") as f:
            json.dump({"case_id": "csv-001",
                       "output_contract": {"answer": {"type": "number"}}}, f)
        with open(os.path.join(csv_in, "task.md"), "w", encoding="utf-8") as f:
            f.write("# CSV case\n")
        with open(os.path.join(csv_in, "data.csv"), "w", encoding="utf-8") as f:
            f.write("x,y\n1,2.5\n2,3.5\n3,4.5\n")
        csv_out = os.path.join(root, "csv_out")
        csv_work = os.path.join(root, "csv_work")
        rc = run(csv_in, csv_out, csv_work)
        rcsv = load_result(csv_out)
        check(rc == 0 and any(s.get("kind") == "csv" for s in
              rcsv.get("analysis", {}).get("data_sources", [])),
              "CSV loaded as data source")

        print("\nselftest result: %s" % ("FAIL" if FAILED else "ALL PASS"))
        return 1 if FAILED else 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
