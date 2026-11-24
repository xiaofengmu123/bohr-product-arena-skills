#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest: deterministic self-check for the read-only DB analysis Skill.

Runs the real pipeline (scripts/main.py) against synthetic inputs and asserts
the output contract that the scoring dimensions depend on. Standard library
only; no network, no prepared answers.

Usage (from the skill root):
  python3 -B scripts/selftest.py
Exit code 0 == all checks pass; 1 == at least one check failed.
"""

import csv
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAIN = HERE / "main.py"
STATUS_SET = ("ok", "partial", "insufficient_evidence", "input_error")


def run_pipeline(input_dir, output_dir, work_dir, extra=None):
    cmd = [sys.executable, "-B", str(MAIN),
           "--input", str(input_dir), "--output", str(output_dir),
           "--work", str(work_dir)]
    if extra:
        cmd += extra
    return subprocess.run(cmd, capture_output=True, text=True)


def load_result(output_dir):
    with open(os.path.join(output_dir, "result.json"), encoding="utf-8") as f:
        return json.load(f)


def assert_valid(result, expect_case_id=True):
    assert result.get("status") in STATUS_SET, "bad status"
    for key in ("sources", "uncertainty", "pending_human", "change_log",
                "errors", "degradations", "limitations", "query_log",
                "answers", "artifacts", "analysis", "runtime"):
        assert key in result, "missing key %s" % key
    assert result.get("case_id") is not None or \
        result["status"] in ("insufficient_evidence", "partial"), \
        "case_id missing with a strong status"
    if expect_case_id:
        assert result.get("case_id"), "case_id must not be empty"


def make_normal_input(root):
    case = {"case_id": "selftest-case-01",
            "output_contract": {"row_count": "number of rows",
                                "group_means": "per-group means",
                                "correlation": "pearson correlation"}}
    with open(os.path.join(root, "case.json"), "w", encoding="utf-8") as f:
        json.dump(case, f, ensure_ascii=False)
    with open(os.path.join(root, "task.md"), "w", encoding="utf-8") as f:
        f.write("# Synthetic check\nCompute row count, group means, correlation.\n")
    with open(os.path.join(root, "data_dictionary.md"), "w", encoding="utf-8") as f:
        f.write("x: predictor in arbitrary units\ny: response in arbitrary units\n"
                "group: categorical label (a/b)\n")
    db = os.path.join(root, "synth.sqlite")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE obs (id INTEGER PRIMARY KEY, x REAL, y REAL, "
                 "group_label TEXT)")
    rows = [(i, i * 1.5, i * 0.5 + 1, "a" if i % 2 == 0 else "b")
            for i in range(1, 41)]
    conn.executemany("INSERT INTO obs VALUES (?,?,?,?)", rows)
    conn.execute("INSERT INTO obs VALUES (41, NULL, 3.0, 'a')")
    conn.commit()
    conn.close()
    with open(os.path.join(root, "groups.csv"), "w", encoding="utf-8",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(["group_label", "label"])
        w.writerow(["a", "Alpha"])
        w.writerow(["b", "Beta"])
    return root


def test_normal():
    base = tempfile.mkdtemp(prefix="sk_selftest_normal_")
    try:
        inp = os.path.join(base, "input")
        out = os.path.join(base, "output")
        work = os.path.join(base, "work")
        os.makedirs(inp)
        make_normal_input(inp)
        cp = run_pipeline(inp, out, work)
        assert cp.returncode == 0, "exit=%d stderr=%s" % (cp.returncode, cp.stderr)
        r = load_result(out)
        assert_valid(r)
        assert r["status"] in ("partial", "ok")
        assert r["analysis"]["schema"], "schema empty"
        assert r["query_log"], "query_log empty"
        assert r["sources"], "sources empty"
        # artifacts must exist
        for name in ("report.md", "evidence_map.json", "schema.json",
                     "summary_stats.csv", "data_quality.csv",
                     "correlation_matrix.csv", "query_log.csv"):
            assert os.path.exists(os.path.join(out, name)), "missing " + name
        # every query_log entry carries sql + row_count
        for q in r["query_log"]:
            assert "sql" in q and "row_count" in q and "status" in q
        # evidence items carry locator + verbatim quote
        for e in r["sources"]:
            assert e.get("locator") and e.get("quote") is not None
        print("PASS normal input")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_empty():
    base = tempfile.mkdtemp(prefix="sk_selftest_empty_")
    try:
        inp = os.path.join(base, "input")
        out = os.path.join(base, "output")
        work = os.path.join(base, "work")
        os.makedirs(inp)
        cp = run_pipeline(inp, out, work)
        assert cp.returncode == 0, "exit=%d" % cp.returncode
        r = load_result(out)
        assert_valid(r, expect_case_id=False)
        assert r["status"] == "insufficient_evidence"
        assert r["pending_human"], "pending_human must list the gap"
        print("PASS empty input")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_bad_json():
    base = tempfile.mkdtemp(prefix="sk_selftest_badjson_")
    try:
        inp = os.path.join(base, "input")
        out = os.path.join(base, "output")
        work = os.path.join(base, "work")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            f.write("{not valid json")
        # still provide data so the pipeline has something to process
        db = os.path.join(inp, "d.sqlite")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE t (a INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        conn.close()
        cp = run_pipeline(inp, out, work)
        assert cp.returncode == 0, "exit=%d" % cp.returncode
        r = load_result(out)
        assert_valid(r, expect_case_id=False)
        assert r["errors"], "bad case.json should be recorded in errors"
        print("PASS bad JSON input")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_idempotent():
    base = tempfile.mkdtemp(prefix="sk_selftest_idem_")
    try:
        inp = os.path.join(base, "input")
        out1 = os.path.join(base, "out1")
        out2 = os.path.join(base, "out2")
        work = os.path.join(base, "work")
        os.makedirs(inp)
        make_normal_input(inp)
        assert run_pipeline(inp, out1, work).returncode == 0
        assert run_pipeline(inp, out2, work).returncode == 0
        r1 = load_result(out1)
        r2 = load_result(out2)
        r1["runtime"] = {}
        r2["runtime"] = {}
        r1["change_log"] = []
        r2["change_log"] = []
        assert r1 == r2, "two runs produced different analysis values"
        print("PASS idempotency")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_help():
    cp = subprocess.run([sys.executable, "-B", str(MAIN), "--help"],
                        capture_output=True, text=True)
    assert cp.returncode == 0, "help exit=%d" % cp.returncode
    print("PASS --help")


def test_svg_parse():
    base = tempfile.mkdtemp(prefix="sk_selftest_svg_")
    try:
        inp = os.path.join(base, "input")
        out = os.path.join(base, "output")
        work = os.path.join(base, "work")
        os.makedirs(inp)
        make_normal_input(inp)
        run_pipeline(inp, out, work)
        import xml.etree.ElementTree as ET
        figs = os.path.join(out, "figures")
        found = False
        for fn in os.listdir(figs) if os.path.isdir(figs) else []:
            if fn.endswith(".svg"):
                ET.parse(os.path.join(figs, fn))
                found = True
        assert found, "no parseable SVG produced"
        print("PASS SVG parse")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main():
    checks = [test_normal, test_empty, test_bad_json, test_idempotent,
              test_help, test_svg_parse]
    failed = 0
    for c in checks:
        try:
            c()
        except AssertionError as e:
            failed += 1
            print("FAIL %s: %s" % (c.__name__, e))
        except Exception as e:
            failed += 1
            print("FAIL %s (unexpected): %r" % (c.__name__, e))
    print("selftest: %d passed, %d failed" % (len(checks) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
