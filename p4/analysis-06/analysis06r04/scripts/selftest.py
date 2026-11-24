#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest: verify the pipeline on locally built fixtures.

Builds small in-memory fixtures (a SQLite snapshot, a CSV, a broken JSON and
an empty directory), runs the real pipeline on each and checks the deliverable
contract. Only the Python standard library is used.

Run from the skill root:
    python3 -B scripts/selftest.py

Exit code 0 means all checks passed; any failure prints a trace and exits 1.
"""

import json
import os
import sqlite3
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import core_impl as ci
import main as M

FIXED_CLOCK = lambda: "2026-01-01T00:00:00Z"

CHECKS = []


def check(name, cond, detail=""):
    CHECKS.append((name, bool(cond), detail))


def strip_volatile(obj):
    """Remove timestamps so two runs are comparable."""
    if isinstance(obj, dict):
        return {k: strip_volatile(v) for k, v in obj.items()
                if k not in ("ts", "generated_at")}
    if isinstance(obj, list):
        return [strip_volatile(v) for v in obj]
    return obj


def make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE species (
            id INTEGER PRIMARY KEY,
            name TEXT,
            sepal_length REAL,
            label TEXT
        );
        CREATE TABLE samples (
            sample_id INTEGER PRIMARY KEY,
            species_id INTEGER,
            note TEXT
        );
        INSERT INTO species VALUES
            (1,'setosa',5.1,'A'),
            (2,'versicolor',6.2,'B'),
            (3,'virginica',6.9,'A'),
            (4,'setosa',4.9,'A'),
            (5,'versicolor',6.0,'B'),
            (6,'virginica',7.0,'A');
        INSERT INTO samples VALUES
            (1,1,'x'),(2,1,'y'),(3,2,'z'),(4,2,'w'),(5,3,'v');
        """
    )
    conn.commit()
    conn.close()


def make_csv(path):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("x,y,label\n1,2.5,a\n2,3.5,b\n3,4.5,a\n4,5.5,b\n")


def load_result(out):
    with open(os.path.join(out, "result.json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_valid():
    tmp = tempfile.mkdtemp(prefix="selftest-valid-")
    try:
        inp = os.path.join(tmp, "input")
        os.makedirs(inp)
        make_db(os.path.join(inp, "data.db"))
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "demo-case",
                       "output_contract": {"answers": {}, "notes": [],
                                           "conclusion": None}}, fh)
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# demo task\n")
        out = os.path.join(tmp, "out")
        work = os.path.join(tmp, "work")
        res = M.run_pipeline(inp, out, work, clock=FIXED_CLOCK)
        r = load_result(out)
        check("valid: result returned", res is not None)
        check("valid: case_id", r.get("case_id") == "demo-case")
        check("valid: status in ok/partial", r.get("status") in ("ok", "partial"))
        check("valid: contract keys echoed",
              all(k in r for k in ("answers", "notes", "conclusion")))
        check("valid: envelope keys present",
              all(k in r for k in ("sources", "uncertainty", "change_log",
                                   "query_log", "evidence_map", "limitations")))
        check("valid: query_log non-empty", len(r.get("query_log", [])) > 0)
        check("valid: evidence has locator+quote",
              all("locator" in e and "quote" in e
                  for e in r.get("evidence_map", [])))
        check("valid: summary_stats.csv exists",
              os.path.exists(os.path.join(out, "summary_stats.csv")))
        check("valid: evidence_map.json exists",
              os.path.exists(os.path.join(out, "evidence_map.json")))
        svgs = [f for f in os.listdir(os.path.join(out, "figures"))
                if f.endswith(".svg")]
        check("valid: at least one SVG figure", len(svgs) > 0)
        if svgs:
            try:
                ET.parse(os.path.join(out, "figures", svgs[0]))
                check("valid: SVG parses", True)
            except Exception as exc:  # noqa: BLE001
                check("valid: SVG parses", False, str(exc))
        # determinism: second run produces identical non-timestamp content
        out2 = os.path.join(tmp, "out2")
        M.run_pipeline(inp, out2, work, clock=FIXED_CLOCK)
        r2 = load_result(out2)
        check("valid: deterministic (ignoring timestamps)",
              strip_volatile(r) == strip_volatile(r2))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_empty():
    tmp = tempfile.mkdtemp(prefix="selftest-empty-")
    try:
        inp = os.path.join(tmp, "input")
        os.makedirs(inp)
        out = os.path.join(tmp, "out")
        res = M.run_pipeline(inp, out, os.path.join(tmp, "work"),
                             clock=FIXED_CLOCK)
        r = load_result(out)
        check("empty: returns", res is not None)
        check("empty: insufficient_evidence",
              r.get("status") == "insufficient_evidence")
        check("empty: valid JSON", isinstance(r, dict))
        check("empty: pending_human lists gap", len(r.get("pending_human", [])) > 0)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_bad_json():
    tmp = tempfile.mkdtemp(prefix="selftest-badjson-")
    try:
        inp = os.path.join(tmp, "input")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        with open(os.path.join(inp, "data.csv"), "w", encoding="utf-8",
                  newline="") as fh:
            fh.write("a,b\n1,2\n")
        out = os.path.join(tmp, "out")
        res = M.run_pipeline(inp, out, os.path.join(tmp, "work"),
                             clock=FIXED_CLOCK)
        r = load_result(out)
        check("badjson: returns", res is not None)
        check("badjson: errors recorded", len(r.get("errors", [])) > 0)
        check("badjson: still valid JSON", isinstance(r, dict))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_csv_only():
    tmp = tempfile.mkdtemp(prefix="selftest-csv-")
    try:
        inp = os.path.join(tmp, "input")
        os.makedirs(inp)
        make_csv(os.path.join(inp, "data.csv"))
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "csv-case", "output_contract": {}}, fh)
        out = os.path.join(tmp, "out")
        res = M.run_pipeline(inp, out, os.path.join(tmp, "work"),
                             clock=FIXED_CLOCK)
        r = load_result(out)
        check("csv: returns", res is not None)
        check("csv: status ok/partial", r.get("status") in ("ok", "partial"))
        check("csv: csv_datasets populated",
              len(r.get("analysis", {}).get("csv_datasets", [])) == 1)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_queries():
    tmp = tempfile.mkdtemp(prefix="selftest-q-")
    try:
        inp = os.path.join(tmp, "input")
        os.makedirs(inp)
        make_db(os.path.join(inp, "data.db"))
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "q-case",
                       "output_contract": {"mean_len": None}}, fh)
        work = os.path.join(tmp, "work")
        os.makedirs(work)
        with open(os.path.join(work, "queries.json"), "w",
                  encoding="utf-8") as fh:
            json.dump([{"id": "q1",
                        "sql": "SELECT AVG(sepal_length) FROM species",
                        "note": "mean sepal length",
                        "answer_key": "mean_len"}], fh)
        out = os.path.join(tmp, "out")
        res = M.run_pipeline(inp, out, work, clock=FIXED_CLOCK)
        r = load_result(out)
        check("queries: answer merged", r.get("mean_len") is not None)
        check("queries: answer in query_log",
              any(q.get("id") == "q1" and q.get("status") == "ok"
                  for q in r.get("query_log", [])))
        check("queries: status ok", r.get("status") == "ok")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_units():
    check("unit: sql_allowed rejects UPDATE",
          ci.sql_allowed("UPDATE t SET x=1") is False)
    check("unit: sql_allowed rejects INSERT",
          ci.sql_allowed("INSERT INTO t VALUES (1)") is False)
    check("unit: sql_allowed accepts SELECT",
          ci.sql_allowed("SELECT * FROM t") is True)
    nums, kind, note = ci.try_numeric(["1", "2", "3", "4"])
    check("unit: coded text coerced", kind in ("int", "float") and len(nums) == 4)
    st = ci.descriptive_stats([1, 2, 3, 4, 5])
    check("unit: descriptive stats mean", st["mean"] == 3.0)
    check("unit: CI present with n>=2", st["ci95_mean"] is not None)
    hist = ci.histogram([1.0, 2.0, "N/A", None, 3.0])
    check("unit: histogram tolerates non-numeric", hist is not None and hist["n"] == 3)
    prof = ci.profile_values(["1", "2", "3", "x", "5"], "TEXT")
    check("unit: mixed coded profile",
          prof["kind"] == "numeric" and prof.get("conversion_note") is not None
          and prof.get("numeric_values") == [1.0, 2.0, 3.0, 5.0])


def main():
    test_units()
    test_valid()
    test_empty()
    test_bad_json()
    test_csv_only()
    test_queries()
    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    for name, ok, detail in CHECKS:
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name,
                             (" - " + detail) if detail else ""))
    print("selftest: %d/%d checks passed" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
