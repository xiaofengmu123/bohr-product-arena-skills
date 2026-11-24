#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — Skill 自检脚本（纯标准库，退出码 0 表示全部通过）。

覆盖：脚本可执行、号码规范化、号码抽取、同族归并、结果校验，
以及空输入 / 坏 JSON / 缺输入三种异常降级场景。
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import patentlib  # noqa: E402

# 使 stdout/stderr 以 UTF-8 输出，避免 Windows GBK 控制台编码崩溃
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PY = sys.executable
MAIN = os.path.join(HERE, "main.py")
PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [ok] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s %s" % (name, detail))


def run_main(args):
    return subprocess.run([PY, "-B", MAIN] + args, cwd=HERE,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def test_normalize():
    print("[normalize]")
    n = patentlib.normalize_patent_number("US 12,345,678 B2")
    check("US grant", n["country"] == "US" and n["kind"] == "B2"
          and n["number_type"] == "grant", repr(n))
    check("US normalized", n["normalized"] == "US12345678B2", repr(n))
    n = patentlib.normalize_patent_number("WO 2020/123456 A1")
    check("WO publication", n["normalized"] == "WO2020/123456A1"
          and n["number_type"] == "publication", repr(n))
    n = patentlib.normalize_patent_number("EP 1 234 567 A1")
    check("EP publication", n["normalized"] == "EP1234567A1", repr(n))
    n = patentlib.normalize_patent_number("US 12/345,678")
    check("US serial as application", n["number_type"] == "application", repr(n))
    n = patentlib.normalize_patent_number("")
    check("empty input invalid", n["valid"] is False, repr(n))


def test_extract():
    print("[extract]")
    text = "详见公开号 US 10,123,456 B2 与其同族 WO2019/012345 A1，另见 1234567 号。"
    hits = patentlib.extract_numbers(text)
    norms = [h["normalized"] for h in hits]
    check("finds US + WO", "US10123456B2" in norms and "WO2019/012345A1" in norms, repr(norms))


def test_family():
    print("[family]")
    records = [
        {"record_id": "r1", "publication_number": "US10123456B2",
         "priority_number": "US 62/111,111"},
        {"record_id": "r2", "publication_number": "EP3456789A1",
         "priority_number": "US62111111"},
        {"record_id": "r3", "publication_number": "CN1234567A"},
    ]
    families = patentlib.group_families(records)
    check("two families", len(families) == 2, repr(families))
    shared = [f for f in families if f["family_type"] == "simple"]
    check("simple family by priority", len(shared) == 1
          and set(shared[0]["members"]) == {"r1", "r2"}, repr(shared))
    check("exhaustive false", all(f["exhaustive"] is False for f in families))


def test_validate():
    print("[validate]")
    ok, issues = patentlib.validate_result(
        {"case_id": "x", "status": "ok", "sources": [], "uncertainty": [],
         "change_log": []})
    check("valid skeleton", ok is True, repr(issues))
    ok, issues = patentlib.validate_result({"case_id": "", "status": "ok"})
    check("rejects missing keys", ok is False and any("missing key" in i for i in issues))
    r = patentlib.build_skeleton({"case_id": "c1"},
                                 {"patent_id": None, "answer": []})
    check("contract backfilled verbatim",
          r["patent_id"] is None and r["answer"] == [] and r["case_id"] == "c1",
          repr(r))


def run_scenario(name, setup):
    print("[%s]" % name)
    tmp = tempfile.mkdtemp(prefix="selftest_")
    inp = os.path.join(tmp, "input")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    os.makedirs(inp)
    setup(inp)
    r = run_main(["scaffold", "--input", inp, "--output", out, "--work", work])
    check("scaffold exit 0", r.returncode == 0, r.stderr.strip())
    rj = None
    rp = os.path.join(out, "result.json")
    if os.path.exists(rp):
        with open(rp, "r", encoding="utf-8") as fh:
            rj = json.load(fh)
    check("result.json exists", rj is not None)
    if rj:
        check("case_id present", bool(rj.get("case_id")))
        ok, issues = patentlib.validate_result(rj)
        check("valid result", ok is True, repr(issues))
    r2 = run_main(["finalize", "--input", inp, "--output", out, "--work", work])
    check("finalize exit 0", r2.returncode == 0, r2.stderr.strip())
    r3 = run_main(["validate", "--input", inp, "--output", out, "--work", work])
    check("validate VALID", r3.returncode == 0 and "VALID" in r3.stdout, r3.stdout)


def main():
    print("selftest: main.py = %s" % MAIN)
    r = run_main(["--help"])
    check("--help exit 0", r.returncode == 0, r.stderr.strip())

    test_normalize()
    test_extract()
    test_family()
    test_validate()

    def _empty(_inp):
        pass

    def _bad_json(inp):
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ not valid json")

    def _good_case(inp):
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "case-selftest",
                       "output_contract": {"patent_id": None, "answer": []}}, fh)
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# 任务\n定位 US 10,123,456 B2。\n")

    run_scenario("empty input", _empty)
    run_scenario("bad json", _bad_json)
    run_scenario("good case with contract", _good_case)

    print("\nselftest: %d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
