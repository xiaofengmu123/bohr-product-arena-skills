#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill 自检脚本（仅标准库）。

在临时目录合成一套最小案例（case.json + task.md + CSV + SQLite），端到端验证
main.py 的 init → profile → plan → check 链路，并校验三类异常输入也能产出合法
result.json 且退出码 0。任何断言失败退出码 1，全部通过退出码 0。
"""
import csv
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
PY = sys.executable


def run(args, cwd=None):
    return subprocess.run([PY, "-B", MAIN] + args, cwd=cwd or HERE,
                          capture_output=True, text=True)


def make_demo(root):
    inp = os.path.join(root, "input")
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    os.makedirs(inp)
    case = {
        "case_id": "selftest-demo-001",
        "output_contract": {
            "analysis": {"chart_types": None, "summary": None},
            "figures": [],
            "quality_notes": None,
        },
    }
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8-sig") as f:
        json.dump(case, f, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write("# 科研数据可视化示例\n对给定数据完成清洗、统计与可视化。\n")
    rows = [["group", "x", "y"],
            ["A", "1.0", "2.1"], ["A", "2.0", "2.9"], ["B", "3.0", "5.0"],
            ["B", "4.0", "6.1"], ["B", "5.0", "7.0"], ["C", "6.0", "8.2"],
            ["C", "7.0", "9.0"], ["A", "2.5", "3.0"], ["C", "6.5", "8.9"]]
    with open(os.path.join(inp, "data.csv"), "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows)
    con = sqlite3.connect(os.path.join(inp, "data.sqlite"))
    con.execute("CREATE TABLE samples (a REAL, b REAL, class TEXT)")
    con.executemany("INSERT INTO samples VALUES (?,?,?)",
                    [(float(r[1]), float(r[2]), r[0]) for r in rows[1:]])
    con.commit()
    con.close()
    return inp, out, work


def assert_json(path, label):
    assert os.path.exists(path), "%s missing: %s" % (label, path)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check_empty_input():
    root = tempfile.mkdtemp(prefix="selftest_empty_")
    try:
        inp = os.path.join(root, "in")
        os.makedirs(inp)
        out, work = os.path.join(root, "out"), os.path.join(root, "wk")
        r = run(["init", "--input", inp, "--output", out, "--work", work])
        assert r.returncode == 0, r.stderr
        obj = assert_json(os.path.join(out, "result.json"), "empty/result")
        assert obj["status"] == "partial", obj
        assert obj["case_id"], obj
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_bad_json():
    root = tempfile.mkdtemp(prefix="selftest_badjson_")
    try:
        inp = os.path.join(root, "in")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            f.write("{not valid json")
        out, work = os.path.join(root, "out"), os.path.join(root, "wk")
        r = run(["init", "--input", inp, "--output", out, "--work", work])
        assert r.returncode == 0, r.stderr
        obj = assert_json(os.path.join(out, "result.json"), "badjson/result")
        assert obj["status"] in ("partial", "input_error"), obj
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    root = tempfile.mkdtemp(prefix="selftest_")
    try:
        inp, out, work = make_demo(root)
        # init
        r = run(["init", "--input", inp, "--output", out, "--work", work])
        assert r.returncode == 0, r.stderr
        obj = assert_json(os.path.join(out, "result.json"), "init/result")
        assert obj["case_id"] == "selftest-demo-001", obj
        assert obj["status"] == "partial", obj
        assert "analysis" in obj and "figures" in obj and "quality_notes" in obj, obj
        assert obj["change_log"], obj
        # profile
        r = run(["profile", "--input", inp, "--output", out, "--work", work])
        assert r.returncode == 0, r.stderr
        prof = assert_json(os.path.join(work, "data_profile.json"), "profile")
        assert prof["tables"], prof
        # plan
        r = run(["plan", "--input", inp, "--output", out, "--work", work])
        assert r.returncode == 0, r.stderr
        fm = assert_json(os.path.join(work, "figure_model.json"), "figure_model")
        assert fm["figures"], fm
        # update
        r = run(["update", "--output", out, "--work", work,
                 "--set", 'status="ok"', "--log", "完成示例图件"])
        assert r.returncode == 0, r.stderr
        # check（契约回填 + 一致性）
        r = run(["check", "--output", out, "--work", work, "--require-contract"])
        assert r.returncode == 0, r.stderr
        rep = json.loads(r.stdout)
        assert rep["status"] == "PASS", rep
        check_empty_input()
        check_bad_json()
        print(json.dumps({"status": "PASS",
                          "n_tables": len(prof["tables"]),
                          "n_figures": len(fm["figures"]),
                          "check": rep}, ensure_ascii=False))
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
