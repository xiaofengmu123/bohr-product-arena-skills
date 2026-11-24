#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest: verify the skill package itself before submission.

Runs on synthetic fixtures created in a temporary directory at runtime; no
prepared answers or formal test outputs are shipped. Checks:

1. CLI --help exits 0;
2. a normal synthetic SQLite + case.json + task.md run produces a legal
   result.json, granular evidence_map (every claim has locator + quote), and
   parseable SVG figures;
3. determinism: two runs agree on all non-timestamp fields;
4. empty input -> insufficient_evidence with exit code 0;
5. bad JSON -> errors recorded, process does not crash.

Usage:
  python3 -B scripts/selftest.py
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAIN = HERE / "main.py"


def run_cli(args, cwd=None):
    return subprocess.run(
        [sys.executable, "-B", str(MAIN)] + args,
        cwd=str(cwd or HERE), capture_output=True, text=True)


def make_synthetic_case(root):
    """Create a tiny, clearly-synthetic self-test fixture in a temp dir."""
    inp = Path(root) / "input"
    inp.mkdir(parents=True)
    db = inp / "specimens.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        "CREATE TABLE specimens (sample_id TEXT PRIMARY KEY, species TEXT, "
        "length_mm REAL, status INTEGER);\n"
        "CREATE TABLE experiments (exp_id TEXT PRIMARY KEY, sample_id TEXT, "
        "dose REAL);\n"
        "INSERT INTO specimens VALUES "
        "('S1','A',5.1,1),('S2','A',4.9,0),('S3','B',6.0,1),"
        "('S4','B',5.5,1),('S5','A',5.0,0),('S6','C',7.0,1);\n"
        "INSERT INTO experiments VALUES "
        "('E1','S1',1.0),('E2','S1',2.0),('E3','S2',1.0),('E4','S3',3.0);\n"
    )
    conn.commit()
    conn.close()
    case = {
        "case_id": "selftest-case",
        "output_contract": {
            "primary_table": "specimens",
            "n_samples": None,
            "mean_length_mm": None,
            "cross_table_n": None,
        },
    }
    (inp / "case.json").write_text(json.dumps(case, ensure_ascii=False),
                                   encoding="utf-8")
    (inp / "task.md").write_text(
        "# selftest fixture\n这是自检用的合成数据，不是正式案例。\n",
        encoding="utf-8")
    return inp


def check_result(out, expect_status=None, expect_case_id="selftest-case"):
    rp = Path(out) / "result.json"
    assert rp.exists(), "result.json missing"
    r = json.loads(rp.read_text(encoding="utf-8"))
    if expect_case_id is not None:
        assert r.get("case_id") == expect_case_id, r.get("case_id")
    assert r.get("status") in ("ok", "partial", "insufficient_evidence",
                               "input_error")
    if expect_status:
        assert r["status"] == expect_status, r["status"]
    for key in ("sources", "uncertainty", "change_log", "query_log",
                "answers", "artifacts", "errors", "degradations",
                "limitations", "pending_human"):
        assert key in r, "missing key %s" % key
    return r


def check_evidence(out):
    ep = Path(out) / "evidence_map.json"
    assert ep.exists(), "evidence_map.json missing"
    ev = json.loads(ep.read_text(encoding="utf-8"))
    claims = ev.get("claims", {})
    total = 0
    for cat, items in claims.items():
        for c in items:
            total += 1
            assert c.get("locator"), "claim %s missing locator" % c.get("claim_id")
            assert isinstance(c["locator"], dict), "locator must be structured dict"
            assert c.get("quote"), "claim %s missing verbatim quote" % c.get("claim_id")
            assert c.get("claim"), "claim %s missing claim text" % c.get("claim_id")
            assert c.get("method"), "claim %s missing method" % c.get("claim_id")
    assert total > 0, "no evidence claims"
    return total


def check_svg(out):
    figdir = Path(out) / "figures"
    if not figdir.exists():
        return 0
    n = 0
    for svg in figdir.glob("*.svg"):
        ET.parse(str(svg))
        n += 1
    return n


def test_normal():
    root = tempfile.mkdtemp(prefix="selselftest_")
    inp = make_synthetic_case(root)
    out = Path(root) / "output"
    work = Path(root) / "work"
    r = run_cli(["--input", str(inp), "--output", str(out), "--work", str(work)])
    assert r.returncode == 0, r.stderr
    res = check_result(out)
    assert res["status"] in ("ok", "partial")
    n_ev = check_evidence(out)
    n_svg = check_svg(out)
    # determinism: run again into a fresh output, compare non-timestamp fields
    out2 = Path(root) / "output2"
    r2 = run_cli(["--input", str(inp), "--output", str(out2), "--work", str(work)])
    assert r2.returncode == 0
    res2 = check_result(out2)
    strip = lambda d: {k: v for k, v in d.items()
                       if k not in ("runtime", "change_log", "sources",
                                    "artifacts", "evidence_map",
                                    "evidence_claim_count")}  # noqa: E731
    assert strip(res) == strip(res2), "non-deterministic result"
    shutil.rmtree(root, ignore_errors=True)
    return res["status"], n_ev, n_svg


def test_empty():
    root = tempfile.mkdtemp(prefix="selselftest_empty_")
    inp = Path(root) / "input"
    inp.mkdir()
    out = Path(root) / "output"
    r = run_cli(["--input", str(inp), "--output", str(out),
                 "--work", str(Path(root) / "work")])
    assert r.returncode == 0, "empty input must exit 0"
    res = check_result(out, expect_status="insufficient_evidence",
                       expect_case_id="unknown")
    shutil.rmtree(root, ignore_errors=True)
    return res["status"]


def test_bad_json():
    root = tempfile.mkdtemp(prefix="selselftest_bad_")
    inp = Path(root) / "input"
    inp.mkdir()
    (inp / "case.json").write_text("{not valid json", encoding="utf-8")
    db = inp / "x.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE t(a INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()
    out = Path(root) / "output"
    r = run_cli(["--input", str(inp), "--output", str(out),
                 "--work", str(Path(root) / "work")])
    assert r.returncode == 0
    res = check_result(out, expect_case_id="unknown")
    assert res["errors"], "bad JSON must be recorded"
    shutil.rmtree(root, ignore_errors=True)
    return res["status"]


def test_csv():
    root = tempfile.mkdtemp(prefix="selselftest_csv_")
    inp = Path(root) / "input"
    inp.mkdir()
    (inp / "case.json").write_text(json.dumps({
        "case_id": "selftest-csv",
        "output_contract": {"n_rows": None},
    }, ensure_ascii=False), encoding="utf-8")
    (inp / "task.md").write_text("# csv fixture\n", encoding="utf-8")
    (inp / "data.csv").write_text(
        "id,category,value\n1,A,1.5\n2,B,2.5\n3,A,3.0\n", encoding="utf-8")
    out = Path(root) / "output"
    r = run_cli(["--input", str(inp), "--output", str(out),
                 "--work", str(Path(root) / "work")])
    assert r.returncode == 0, r.stderr
    res = check_result(out, expect_case_id="selftest-csv")
    assert res["status"] in ("ok", "partial")
    schema = json.loads((out / "schema.json").read_text(encoding="utf-8"))
    assert any(t.get("table", "").startswith("csv_") for d in schema["databases"]
               for t in d.get("tables", []))
    shutil.rmtree(root, ignore_errors=True)
    return res["status"]


def main():
    print("== selftest: --help ==")
    r = run_cli(["--help"])
    assert r.returncode == 0, "--help must exit 0"
    print("help ok")
    print("== selftest: normal synthetic case ==")
    status, n_ev, n_svg = test_normal()
    print("normal ok: status=%s evidence_claims=%d svg=%d" % (status, n_ev, n_svg))
    print("== selftest: CSV input ==")
    print("csv ok: status=%s" % test_csv())
    print("== selftest: empty input ==")
    print("empty ok: status=%s" % test_empty())
    print("== selftest: bad JSON ==")
    print("bad-json ok: status=%s" % test_bad_json())
    print("ALL SELF-TESTS PASSED")


if __name__ == "__main__":
    main()
