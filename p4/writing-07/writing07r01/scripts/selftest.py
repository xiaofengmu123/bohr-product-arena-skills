#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 三种场景自检：空输入目录 / 坏 JSON / 缺输入目录。

对每种场景依次运行 scan.py、emit_skeleton.py、validate.py，断言：
  - 三个脚本退出码均为 0
  - result.json 是合法 JSON，含 case_id 键与合法 status
全部通过打印 PASS，退出码 0；任一失败打印 FAIL 并退出码 1。
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def run(script, *args):
    cmd = [sys.executable, "-B", os.path.join(HERE, script)] + list(args)
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), \
        proc.stderr.decode("utf-8", "replace")


def check_output(output_dir):
    path = os.path.join(output_dir, "result.json")
    if not os.path.exists(path):
        return False, "result.json missing"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return False, "bad result.json: %s" % e
    if "case_id" not in data:
        return False, "case_id missing"
    if data.get("status") not in {"ok", "partial", "insufficient_evidence", "input_error"}:
        return False, "invalid status %r" % data.get("status")
    return True, ""


def scenario(name, setup):
    base = tempfile.mkdtemp(prefix="selftest_")
    input_dir = os.path.join(base, "input")
    work_dir = os.path.join(base, "work")
    output_dir = os.path.join(base, "output")
    setup(input_dir, work_dir, output_dir)
    results = []
    for script, args in [("scan.py", ["--input", input_dir, "--work", work_dir]),
                         ("emit_skeleton.py", ["--work", work_dir, "--output", output_dir]),
                         ("validate.py", ["--work", work_dir, "--output", output_dir])]:
        rc, out, err = run(script, *args)
        ok = (rc == 0)
        results.append((script, ok, rc))
    vok, vmsg = check_output(output_dir)
    all_ok = all(r[1] for r in results) and vok
    detail = " ".join("%s:%s" % (s, "OK" if o else "FAIL(rc=%d)" % c)
                      for s, o, c in results)
    print("%-14s %s  result:%s  %s" % (name, "PASS" if all_ok else "FAIL",
                                        "OK" if vok else vmsg, detail))
    return all_ok


def setup_empty(input_dir, work_dir, output_dir):
    os.makedirs(input_dir, exist_ok=True)


def setup_bad_json(input_dir, work_dir, output_dir):
    os.makedirs(input_dir, exist_ok=True)
    with open(os.path.join(input_dir, "case.json"), "w", encoding="utf-8") as f:
        f.write("{ not valid json !!")


def setup_missing_input(input_dir, work_dir, output_dir):
    pass  # 不创建 input_dir


def main():
    tests = [
        ("empty_dir", setup_empty),
        ("bad_json", setup_bad_json),
        ("missing_input", setup_missing_input),
    ]
    all_ok = True
    for name, fn in tests:
        all_ok = scenario(name, fn) and all_ok
    print("SELFTEST %s" % ("PASS" if all_ok else "FAIL"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
