#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for the skill package (standard library only).

Runs scripts/main.py against three in-scope input scenarios and verifies the
exit code plus structural validity of /app/output-style artifacts. Produces no
prepared answers and no formal-case outputs; the demo data is synthetic and
clearly labelled.

Usage:  python3 -B scripts/selftest.py
Exit code 0 = all checks pass.
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

MAIN = Path(__file__).resolve().parent / "main.py"


def _run(input_dir, output_dir, work_dir):
    return subprocess.run(
        [sys.executable, "-B", str(MAIN),
         "--input", str(input_dir), "--output", str(output_dir),
         "--work", str(work_dir)],
        capture_output=True, text=True)


def _check_result(output_dir, expect_status, expect_case=None):
    rp = Path(output_dir) / "result.json"
    assert rp.is_file(), "result.json missing"
    r = json.load(open(rp, encoding="utf-8"))
    assert r.get("status") in ("ok", "partial", "insufficient_evidence",
                               "input_error"), "bad status"
    if expect_status is not None:
        assert r["status"] == expect_status, "status %s != %s" % (r["status"], expect_status)
    for key in ("sources", "uncertainty", "change_log"):
        assert isinstance(r.get(key), list), "missing %s" % key
    if expect_case is not None:
        assert r.get("case_id") == expect_case, "case_id mismatch"
    return r


def _make_demo(root):
    os.makedirs(root, exist_ok=True)
    d = Path(root) / "ds.db"
    conn = sqlite3.connect(d)
    cur = conn.cursor()
    cur.execute("CREATE TABLE samples (sample_id TEXT, group_id INTEGER, x REAL, y REAL, status TEXT)")
    cur.executemany(
        "INSERT INTO samples VALUES (?,?,?,?,?)",
        [("s1", 1, 1.0, 2.0, "ok"), ("s2", 1, 2.0, 4.0, "ok"),
         ("s3", 2, 3.0, 6.0, "na"), ("s4", 2, 4.0, 8.0, "ok"),
         ("s5", 2, None, 7.0, "ok"), ("s6", 2, 4.0, 8.0, "ok")])
    cur.execute("CREATE TABLE meta (sample_id TEXT, label TEXT)")
    cur.executemany("INSERT INTO meta VALUES (?,?)",
                    [("s1", "a"), ("s2", "b"), ("s3", "c")])
    conn.commit()
    conn.close()
    (Path(root) / "case.json").write_text(
        json.dumps({"case_id": "demo-case",
                    "output_contract": {"group1_mean_x": None,
                                        "group2_mean_x": None,
                                        "label_lookup": None}}),
        encoding="utf-8")
    (Path(root) / "task.md").write_text(
        "# demo\n计算每个 group_id 的 x 均值；跨表 samples/meta 用 sample_id 连接。\n",
        encoding="utf-8")
    (Path(root) / "data_dictionary.md").write_text(
        "# 字段字典\n- x: 自变量，单位任意\n- y: 因变量\n- status: 样本状态（ok/na）\n",
        encoding="utf-8")


def _make_empty(root):
    os.makedirs(root, exist_ok=True)


def _make_bad(root):
    os.makedirs(root, exist_ok=True)
    (Path(root) / "case.json").write_text("{not valid json", encoding="utf-8")


def main():
    results = []
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)

        demo_in = td / "demo" / "input"
        _make_demo(demo_in)
        rc = _run(demo_in, td / "demo" / "output", td / "demo" / "work")
        assert rc.returncode == 0, "demo main.py exited %d" % rc.returncode
        out = td / "demo" / "output"
        r = _check_result(out, expect_status="partial", expect_case="demo-case")
        assert (out / "summary_stats.csv").is_file(), "summary_stats.csv missing"
        assert (out / "report.md").is_file(), "report.md missing"
        svg = list((out / "figures").glob("*.svg"))
        assert svg, "no SVG charts produced"
        for s in svg:
            ET.parse(str(s))
        # verify contract keys exist (null until agent fills them)
        for k in ("group1_mean_x", "group2_mean_x", "label_lookup"):
            assert k in r, "contract key %s missing" % k
        results.append("demo: ok (%d figures)" % len(svg))

        empty_in = td / "empty" / "input"
        _make_empty(empty_in)
        rc = _run(empty_in, td / "empty" / "output", td / "empty" / "work")
        assert rc.returncode == 0, "empty main.py exited %d" % rc.returncode
        _check_result(td / "empty" / "output",
                      expect_status="insufficient_evidence")
        results.append("empty: ok")

        bad_in = td / "bad" / "input"
        _make_bad(bad_in)
        rc = _run(bad_in, td / "bad" / "output", td / "bad" / "work")
        assert rc.returncode == 0, "bad-json main.py exited %d" % rc.returncode
        _check_result(td / "bad" / "output", expect_status="insufficient_evidence")
        results.append("bad-json: ok")

    print("selftest PASS:")
    for line in results:
        print("  -", line)
    return 0


if __name__ == "__main__":
    sys.exit(main())