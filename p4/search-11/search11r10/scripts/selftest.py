#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — Skill 自检脚本（纯标准库，进程内运行，无网络）。

覆盖四类确定性检查：
  1. 三种异常输入（空目录 / 坏 JSON / 缺输入目录）都退出码 0 且产物合法；
  2. 规范化 smoke：公开号、申请号、优先权号、WO 边界合并各一例；
  3. 同族传递闭包：A 与 B 共享申请号、B 与 C 共享优先权号 -> 三者归一族；
  4. finalize 后 validate 打印 VALID。

用法：python3 scripts/selftest.py
全部通过退出码 0；任一失败退出码 1。
"""

import json
import os
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import core_impl as P  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print("PASS  %s" % name)
    else:
        FAILURES.append(name)
        print("FAIL  %s  %s" % (name, detail))


def _fresh(tmp):
    out = os.path.join(tmp, "out")
    work = os.path.join(tmp, "work")
    return out, work


def test_scaffold_contract_echo(tmp):
    case_dir = os.path.join(tmp, "case_contract")
    os.makedirs(case_dir, exist_ok=True)
    contract = {"patent_number": None, "family_summary": None}
    case = {"case_id": "case-demo", "output_contract": contract,
            "task": "locate a patent"}
    with open(os.path.join(case_dir, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False)
    out, work = _fresh(tmp)
    result, path = P.scaffold(case_dir, out, work)
    ok = (os.path.exists(path)
          and result.get("case_id") == "case-demo"
          and "patent_number" in result
          and "family_summary" in result
          and result.get("status") == "partial")
    check("contract echo (flat)", ok)


def test_scaffold_schema_contract(tmp):
    case_dir = os.path.join(tmp, "case_schema")
    os.makedirs(case_dir, exist_ok=True)
    contract = {"type": "object",
                "properties": {"target": {}, "ranking": {}}}
    case = {"case_id": "case-schema", "output_contract": contract}
    with open(os.path.join(case_dir, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False)
    out, work = _fresh(tmp)
    result, path = P.scaffold(case_dir, out, work)
    ok = ("target" in result and "ranking" in result
          and result.get("case_id") == "case-schema")
    check("contract echo (json-schema)", ok)


def test_empty_input(tmp):
    empty = os.path.join(tmp, "empty")
    os.makedirs(empty, exist_ok=True)
    out, work = _fresh(tmp)
    result, path = P.scaffold(empty, out, work)
    ok = (result.get("status") == "insufficient_evidence"
          and os.path.exists(path)
          and any(u.get("item") == "no_input_files"
                  for u in result.get("uncertainty", [])))
    check("empty input -> insufficient_evidence", ok)


def test_bad_json(tmp):
    case_dir = os.path.join(tmp, "badjson")
    os.makedirs(case_dir, exist_ok=True)
    with open(os.path.join(case_dir, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{not valid json")
    out, work = _fresh(tmp)
    result, path = P.scaffold(case_dir, out, work)
    ok = (os.path.exists(path)
          and result.get("case_id") == "unknown"
          and any(e.get("item") == "case_json_unreadable"
                  for e in result.get("errors", [])))
    check("bad JSON -> degraded skeleton", ok)


def test_missing_input_dir(tmp):
    missing = os.path.join(tmp, "does_not_exist")
    out, work = _fresh(tmp)
    result, path = P.scaffold(missing, out, work)
    ok = (os.path.exists(path)
          and result.get("status") == "insufficient_evidence")
    check("missing input dir -> insufficient_evidence", ok)


def test_normalize():
    cases = [
        ("US 12,345,678 B2", "publication", "US", "12345678"),
        ("US 17/123,456", "application", "US", "17123456"),
        ("WO 2023/123456 A1", "publication", "WO", "2023123456"),
        ("PCT/US2020/012345", "application", "WO", "2020012345"),
        ("CN 202010123456.7", "application", "CN", "2020101234567"),
        ("EP 3 456 789 A1", "publication", "EP", "3456789"),
    ]
    all_ok = True
    detail = []
    for raw, ntype, cc, digits in cases:
        n = P.normalize_patent_number(raw)
        if (n["number_type"] != ntype or n["country"] != cc
                or n["number"] != digits or not n["normalized"]):
            all_ok = False
            detail.append("%s -> %s" % (raw, n))
    check("normalize smoke", all_ok, "; ".join(detail))


def test_family_transitive(tmp):
    records = [
        {"record_id": "R1", "application_number": "US 17/123,456"},
        {"record_id": "R2", "application_number": "US 17/123,456",
         "priority_number": "US 63/000,001"},
        {"record_id": "R3", "priority_number": "US 63/000,001"},
        {"record_id": "R4", "publication_number": "US 9,999,999 B2"},
    ]
    res = P.group_families(records)
    fams = res["families"]
    ids = [sorted(m["record_id"] for m in f["members"]) for f in fams]
    ok = (len(fams) == 2
          and sorted(["R1", "R2", "R3"]) in ids
          and ["R4"] in ids
          and res.get("exhaustive") is False)
    check("family transitive closure", ok, "families=%s" % ids)


def test_finalize_and_validate(tmp):
    case_dir = os.path.join(tmp, "case_fin")
    os.makedirs(case_dir, exist_ok=True)
    with open(os.path.join(case_dir, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "case-fin"}, fh, ensure_ascii=False)
    out, work = _fresh(tmp)
    P.scaffold(case_dir, out, work)
    ok, _res, _path, problems = P.finalize(out)
    check("finalize ok", ok, "; ".join(problems))
    check("report.md generated",
          os.path.exists(os.path.join(out, "report.md")))


def main():
    tmp = tempfile.mkdtemp(prefix="sci-patent-selftest-")
    try:
        test_normalize()
        test_scaffold_contract_echo(tmp)
        test_scaffold_schema_contract(tmp)
        test_empty_input(tmp)
        test_bad_json(tmp)
        test_missing_input_dir(tmp)
        test_family_transitive(tmp)
        test_finalize_and_validate(tmp)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    if FAILURES:
        print("\n%d FAILED" % len(FAILURES))
        sys.exit(1)
    print("\nALL SELF-TESTS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
