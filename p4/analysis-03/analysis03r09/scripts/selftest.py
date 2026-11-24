#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill 自检：验证 main.py / viz.py 可运行、端到端契约回填、空输入与坏 JSON 健壮性。

仅标准库。退出码：0=全部通过，1=存在 FAIL。供交付前自查，不依赖正式案例。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
VIZ = os.path.join(HERE, "viz.py")

PASS, FAIL = [], []


def run(args, cwd=None, timeout=120):
    return subprocess.run([sys.executable, "-B"] + args, cwd=cwd,
                          capture_output=True, text=True, timeout=timeout)


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append("%s | %s" % (name, detail or ""))


def load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def test_help():
    r1 = run([MAIN, "--help"])
    ok("main --help rc=0", r1.returncode == 0, str(r1.returncode))
    r2 = run([VIZ, "--help"])
    ok("viz --help rc=0", r2.returncode == 0, str(r2.returncode))


def test_end_to_end():
    tmp = tempfile.mkdtemp(prefix="selftest_")
    try:
        inp = os.path.join(tmp, "input")
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        os.makedirs(inp)
        case = {"case_id": "selftest-1",
                "output_contract": {"figure": "string", "mean": "number",
                                    "labels": "array", "nested": {"x": "number"}}}
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(case, fh)
        with open(os.path.join(inp, "data.csv"), "w", encoding="utf-8") as fh:
            fh.write("species,sepal_length,sepal_width\n"
                     "setosa,5.1,3.5\nversicolor,6.2,2.8\nvirginica,6.3,3.3\n"
                     "setosa,4.9,3.0\n")

        r = run([MAIN, "init", "--input", inp, "--output", out, "--work", work])
        ok("init rc=0", r.returncode == 0, str(r.returncode))
        res = load(os.path.join(out, "result.json"))
        ok("case_id 回填", res.get("case_id") == "selftest-1", str(res.get("case_id")))
        ok("status=partial", res.get("status") == "partial", str(res.get("status")))
        ok("契约叶值 figure=null", res.get("figure") is None)
        ok("契约叶值 labels=[]", res.get("labels") == [])
        ok("契约嵌套 nested.x=null", isinstance(res.get("nested"), dict)
           and res["nested"].get("x") is None)
        ok("保留键 change_log 存在", isinstance(res.get("change_log"), list)
           and len(res["change_log"]) >= 1)

        run([VIZ, "inspect", "--input", inp, "--work", work])
        ok("inspect 生成 inventory", os.path.isfile(os.path.join(work, "data_inventory.json")))

        r = run([VIZ, "plot", "--input", inp, "--output", out, "--work", work,
                 "--prefix", "fig1", "--kind", "auto"])
        ok("plot rc=0", r.returncode == 0, str(r.returncode))
        stats_path = os.path.join(out, "fig1.stats.json")
        ok("plot 生成 stats", os.path.isfile(stats_path))

        r = run([MAIN, "update", "--output", out, "--work", work,
                 "--set", 'status="ok"', "--log", "自检完成"])
        ok("update rc=0", r.returncode == 0, str(r.returncode))
        ok("update 后 status=ok", load(os.path.join(out, "result.json"))["status"] == "ok")

        r = run([MAIN, "check", "--output", out, "--work", work, "--require-contract"])
        ok("check 无 FAIL", "fails=0" in r.stdout, r.stdout.strip().splitlines()[-1] if r.stdout else "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_empty_input():
    tmp = tempfile.mkdtemp(prefix="selftest_")
    try:
        inp = os.path.join(tmp, "empty")
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        os.makedirs(inp)
        r = run([MAIN, "init", "--input", inp, "--output", out, "--work", work])
        ok("空输入 init rc=0", r.returncode == 0, str(r.returncode))
        res = load(os.path.join(out, "result.json"))
        ok("空输入 result 合法", bool(res.get("case_id")) and res.get("status") in
           ("partial", "insufficient_evidence", "input_error"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_bad_json():
    tmp = tempfile.mkdtemp(prefix="selftest_")
    try:
        inp = os.path.join(tmp, "input")
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ bad json")
        r = run([MAIN, "init", "--input", inp, "--output", out, "--work", work])
        ok("坏 JSON init rc=0", r.returncode == 0, str(r.returncode))
        res = load(os.path.join(out, "result.json"))
        ok("坏 JSON 记录 errors", isinstance(res.get("errors"), list)
           and any("bad_json" in e for e in res["errors"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_help()
    test_end_to_end()
    test_empty_input()
    test_bad_json()
    print("PASS %d" % len(PASS))
    for p in PASS:
        print("  [PASS] " + p)
    print("FAIL %d" % len(FAIL))
    for f in FAIL:
        print("  [FAIL] " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
