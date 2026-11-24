#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 离线自检：空输入、坏 JSON、正常 CSV、去重确定性。

用法（在 Skill 根目录）：
    python3 -B scripts/selftest.py

不依赖外部数据；全部在临时目录内构造夹具，测试退出后自动清理。
退出码 0 = 全部通过；非 0 = 有失败项。
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pipeline  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print("[%s] %s %s" % (status, name, detail))
    if not cond:
        FAILURES.append(name)


def _fresh(tmp):
    inp = os.path.join(tmp, "input")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    for d in (inp, out, work):
        os.makedirs(d, exist_ok=True)
    return inp, out, work


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_empty_input():
    tmp = tempfile.mkdtemp(prefix="selftest_empty_")
    try:
        inp, out, work = _fresh(tmp)
        rc = pipeline.main(["skeleton", "--input", inp, "--output", out, "--work", work])
        check("空输入退出码 0", rc == 0, "rc=%s" % rc)
        r = _read_json(os.path.join(out, "result.json"))
        check("空输入 status=insufficient_evidence",
              r.get("status") == "insufficient_evidence", str(r.get("status")))
        check("空输入 case_id=unknown", r.get("case_id") == "unknown")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_bad_json():
    tmp = tempfile.mkdtemp(prefix="selftest_badjson_")
    try:
        inp, out, work = _fresh(tmp)
        _write(os.path.join(inp, "case.json"), "{ this is not valid json")
        rc = pipeline.main(["skeleton", "--input", inp, "--output", out, "--work", work])
        check("坏 JSON 不崩溃且退出码 0", rc == 0, "rc=%s" % rc)
        r = _read_json(os.path.join(out, "result.json"))
        deg = r.get("degradations") or []
        check("坏 JSON 记录到 degradations",
              any(d.get("kind") == "bad_case_json" for d in deg), str(deg[:1]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_good_case():
    tmp = tempfile.mkdtemp(prefix="selftest_good_")
    try:
        inp, out, work = _fresh(tmp)
        _write(os.path.join(inp, "case.json"),
               json.dumps({"case_id": "case1",
                           "output_contract": {"candidates": {"type": "array"},
                                               "query_string": {}}}))
        _write(os.path.join(inp, "records.csv"),
               "title,doi,year,abstract\n"
               "Alpha Study,10.1000/a,2020,We tested intervention X in population P\n"
               "Alpha Study journal,10.1000/a,2021,We tested intervention X in population P\n"
               "Beta Study,,2019,Unrelated topic Y\n"
               "Gamma Study,10.1000/g,2018,Study about Z\n")
        rc = pipeline.main(["skeleton", "--input", inp, "--output", out, "--work", work])
        check("正常输入 skeleton 退出码 0", rc == 0)
        rc = pipeline.main(["normalize", "--input", inp, "--output", out, "--work", work])
        check("normalize 退出码 0", rc == 0)
        dedup = _read_json(os.path.join(work, "dedup.json"))
        st = dedup.get("stats", {})
        check("去重计数 retrieved=4 merged=1 unique=3",
              st.get("retrieved") == 4 and st.get("merged") == 1
              and st.get("unique") == 3, str(st))

        # 回填判定（模拟 Agent 双人独立判定）
        _write(os.path.join(work, "judgments.json"), json.dumps({
            "criteria_version": "v1",
            "judgments": [
                {"record_id": "r001", "decision": "include",
                 "reason_code": "in_full", "evidence_quote": "intervention X in population P",
                 "reviewer_a": "include", "reviewer_b": "include",
                 "conflict": None, "arbitration": None, "needs_human": False},
                {"record_id": "r003", "decision": "exclude",
                 "reason_code": "ex_not_relevant", "evidence_quote": "Unrelated topic Y",
                 "reviewer_a": "exclude", "reviewer_b": "exclude",
                 "conflict": None, "arbitration": None, "needs_human": False},
                {"record_id": "r004", "decision": "include",
                 "reason_code": "in_full", "evidence_quote": "Study about Z",
                 "reviewer_a": "include", "reviewer_b": "include",
                 "conflict": None, "arbitration": None, "needs_human": False},
            ]}))
        rc = pipeline.main(["finalize", "--input", inp, "--output", out, "--work", work])
        check("finalize 退出码 0", rc == 0)
        r = _read_json(os.path.join(out, "result.json"))
        check("结果 status=ok", r.get("status") == "ok", str(r.get("status")))
        check("计数闭合", r.get("audit", {}).get("closure", {}).get("closed") is True)
        check("contract 键存在", "candidates" in r and "query_string" in r,
              str([k for k in r if k in ("candidates", "query_string")]))
        check("screening_table.csv 存在",
              os.path.isfile(os.path.join(out, "screening_table.csv")))

        rc = pipeline.main(["selfcheck", "--input", inp, "--output", out, "--work", work])
        check("selfcheck 全部通过（退出码 0）", rc == 0, "rc=%s" % rc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_determinism():
    tmp = tempfile.mkdtemp(prefix="selftest_det_")
    try:
        inp, out, work = _fresh(tmp)
        _write(os.path.join(inp, "case.json"), json.dumps({"case_id": "case1"}))
        _write(os.path.join(inp, "records.csv"),
               "title,doi\nA Study,10.1/a\nA Study journal,10.1/a\nB Study,\n")
        pipeline.main(["skeleton", "--input", inp, "--output", out, "--work", work])
        pipeline.main(["normalize", "--input", inp, "--output", out, "--work", work])
        s1 = _read_json(os.path.join(work, "dedup.json"))["stats"]
        pipeline.main(["normalize", "--input", inp, "--output", out, "--work", work])
        s2 = _read_json(os.path.join(work, "dedup.json"))["stats"]
        check("重复运行去重计数一致（确定性）", s1 == s2, "%s vs %s" % (s1, s2))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("=== selftest: 空输入 ===")
    test_empty_input()
    print("=== selftest: 坏 JSON ===")
    test_bad_json()
    print("=== selftest: 正常案例闭环 ===")
    test_good_case()
    print("=== selftest: 确定性 ===")
    test_determinism()
    print("selftest: %s" % ("ALL PASS" if not FAILURES else
                            "%d FAIL: %s" % (len(FAILURES), ", ".join(FAILURES))))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
