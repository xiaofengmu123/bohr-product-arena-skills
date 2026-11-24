#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest: verify the pipeline's robustness contract without shipped cases.

Three fixtures are built in a temporary directory and run through
scripts/main.py via subprocess:
  1. empty input dir            -> exit 0, status=insufficient_evidence;
  2. malformed case.json + DB   -> exit 0, legal result, error recorded;
  3. minimal SQLite + contract  -> exit 0, legal result, contract filled.

Only the Python standard library is used. No formal test cases or prepared
answers are shipped. Exit code 0 = all checks passed, 1 = failed.
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
MAIN = SKILL_ROOT / "scripts" / "main.py"

ALLOWED = ("ok", "partial", "insufficient_evidence", "input_error")


def run(input_dir, output_dir, work_dir):
    return subprocess.run(
        [sys.executable, "-B", str(MAIN), "--input", str(input_dir),
         "--output", str(output_dir), "--work", str(work_dir)],
        capture_output=True, text=True, timeout=120)


def load_result(output_dir):
    with open(os.path.join(output_dir, "result.json"), encoding="utf-8") as fh:
        return json.load(fh)


def check_common(result):
    assert result.get("status") in ALLOWED, result.get("status")
    for key in ("sources", "uncertainty", "change_log", "query_log",
                "errors", "degradations", "limitations", "pending_human"):
        assert key in result, key


def scenario_empty(tmp):
    d = tmp / "empty"
    d.mkdir()
    out, work = tmp / "o1", tmp / "w1"
    p = run(d, out, work)
    assert p.returncode == 0, p.stderr
    r = load_result(out)
    check_common(r)
    assert r["status"] == "insufficient_evidence", r["status"]
    return "empty-dir"


def scenario_bad_json(tmp):
    d = tmp / "badjson"
    d.mkdir()
    (d / "case.json").write_text("{not valid json", encoding="utf-8")
    conn = sqlite3.connect(str(d / "data.sqlite"))
    conn.execute("CREATE TABLE t (x INTEGER, y REAL)")
    conn.execute("INSERT INTO t VALUES (1, 2.5), (2, 3.5)")
    conn.commit()
    conn.close()
    out, work = tmp / "o2", tmp / "w2"
    p = run(d, out, work)
    assert p.returncode == 0, p.stderr
    r = load_result(out)
    check_common(r)
    assert any("bad json" in str(e) for e in r.get("errors", [])), r
    return "bad-json"


def scenario_minimal(tmp):
    d = tmp / "minimal"
    d.mkdir()
    (d / "task.md").write_text(
        "# 任务\n分析数据表 t，回答样本量与 x 的均值。", encoding="utf-8")
    conn = sqlite3.connect(str(d / "data.sqlite"))
    conn.execute("CREATE TABLE t (x INTEGER, y REAL, grp TEXT)")
    conn.executemany("INSERT INTO t VALUES (?,?,?)",
                     [(1, 2.5, "a"), (2, 3.5, "b"), (3, 4.5, "a")])
    conn.commit()
    conn.close()
    (d / "case.json").write_text(json.dumps({
        "case_id": "selftest-1",
        "output_contract": {"n_rows": None, "x_mean": None,
                            "grp_mode": None},
    }), encoding="utf-8")
    out, work = tmp / "o3", tmp / "w3"
    p = run(d, out, work)
    assert p.returncode == 0, p.stderr
    r = load_result(out)
    check_common(r)
    assert r["case_id"] == "selftest-1"
    assert r["status"] in ("ok", "partial")
    assert r.get("n_rows") == 3, r.get("n_rows")
    assert abs(r.get("x_mean") - 2.0) < 1e-9, r.get("x_mean")
    for artifact in ("schema.json", "summary_stats.csv", "data_quality.csv",
                     "report.md", "evidence_map.json"):
        assert os.path.exists(os.path.join(out, artifact)), artifact
    return "minimal-sqlite"


def main():
    results = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for name, fn in (("empty", scenario_empty), ("badjson",
                                                     scenario_bad_json),
                         ("minimal", scenario_minimal)):
            try:
                label = fn(tmp)
                results.append((name, True, label))
                print("PASS %s (%s)" % (name, label))
            except Exception as e:  # noqa: BLE001
                results.append((name, False, repr(e)))
                print("FAIL %s: %s" % (name, e))
    failed = [r for r in results if not r[1]]
    print("selftest: %d/%d passed" % (
        len(results) - len(failed), len(results)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
