#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 离线冒烟自检（不依赖正式案例，全部用临时合成夹具）。

覆盖：主路径、契约 dict/list 两种形态、确定性复跑、空输入、坏 JSON、
缺 case.json、无 draft 直接 build。退出码 0 表示全部通过，1 表示失败。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MAIN = Path(__file__).resolve().parent / "main.py"
PY = sys.executable


def run(args, cwd=None):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([PY, "-B", str(MAIN)] + args, cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env)


def make_case(root, contract_form="dict"):
    (root / "input").mkdir(parents=True)
    (root / "input" / "materials").mkdir()
    (root / "input" / "materials" / "sub").mkdir()
    (root / "work").mkdir()
    (root / "output").mkdir()

    (root / "input" / "task.md").write_text("# task\n读取文件并整理。\n", encoding="utf-8")

    a = "这是材料 A 的正文。含关键词：量子退火。\n"
    b = a  # 与 a 重复
    v1 = "论文正文版本一。方法：蒙特卡洛。\n"
    v2 = "论文正文版本二。方法：蒙特卡洛与变分推断。\n"
    (root / "input" / "materials" / "a.txt").write_text(a, encoding="utf-8")
    (root / "input" / "materials" / "b.txt").write_text(b, encoding="utf-8")
    (root / "input" / "materials" / "paper_v1.txt").write_text(v1, encoding="utf-8")
    (root / "input" / "materials" / "paper_v2.txt").write_text(v2, encoding="utf-8")
    (root / "input" / "materials" / "sub" / "note.txt").write_text("临时笔记。\n", encoding="utf-8")

    if contract_form == "dict":
        oc = {"inventory": None, "duplicates": None, "alias_groups": None,
              "version_groups": None, "metadata": None, "search_results": None}
    else:
        oc = ["inventory", "duplicates", {"name": "version_groups"},
              {"field": "search_results"}]
    case = {
        "case_id": "smoke-001",
        "output_contract": oc,
        "aliases": {"a.txt": ["b.txt"]},
        "metadata": {"owner": "tester", "project": "smoke"},
        "queries": [{"question": "量子退火在哪个文件？"}],
    }
    (root / "input" / "case.json").write_text(
        json.dumps(case, ensure_ascii=False), encoding="utf-8")


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def check(name, cond, detail=""):
    if cond:
        print("  PASS %s %s" % (name, detail))
        return True
    print("  FAIL %s %s" % (name, detail))
    return False


def test_happy_path():
    print("[selftest] happy_path (dict contract)")
    root = Path(tempfile.mkdtemp(prefix="skilltest-"))
    make_case(root, "dict")
    i, w, o = str(root / "input"), str(root / "work"), str(root / "output")
    run(["init", "--input", i, "--work", w, "--output", o])
    run(["show", "--input", i, "--work", w, "--output", o])
    # 模拟模型写 draft
    draft = load(w + "/draft.json")
    draft["notes"] = [{
        "target": "F003", "kind": "version", "text": "paper_v1 与 paper_v2 为同一文献两个版本",
        "basis": "文件名显式带 v1/v2 后缀", "confidence": "explicit",
        "evidence": [{"source_id": "F003", "locator": "materials/paper_v1.txt",
                      "quote": "论文正文版本一。"}],
    }]
    draft["queries"] = [{
        "query_id": "Q01", "answer": "量子退火出现在 materials/a.txt 与 b.txt",
        "status": "answered",
        "evidence": [{"source_id": "F001", "locator": "materials/a.txt",
                      "quote": "量子退火"}],
    }]
    (Path(w) / "draft.json").write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    run(["check", "--input", i, "--work", w, "--output", o])
    run(["build", "--input", i, "--work", w, "--output", o])
    run(["selfcheck", "--input", i, "--work", w, "--output", o])

    r = load(o + "/result.json")
    ok = True
    ok &= check("case_id", r.get("case_id") == "smoke-001")
    ok &= check("status_ok_or_partial", r.get("status") in ("ok", "partial"), r.get("status"))
    for f in ("inventory", "duplicates", "alias_groups", "version_groups",
              "metadata", "search_results"):
        ok &= check("contract:" + f, f in r, "present" if f in r else "MISSING")
    ok &= check("change_log_nonempty", isinstance(r.get("change_log"), list) and len(r["change_log"]) > 0)
    ok &= check("contract_audit", "contract_audit" in r and r["contract_audit"]["total_fields"] == 6)
    ok &= check("evidence_map", (Path(o) / "evidence_map.json").stat().st_size > 0)
    ok &= check("inventory_md", (Path(o) / "inventory.md").stat().st_size > 0)
    shutil.rmtree(root, ignore_errors=True)
    return ok


def test_determinism():
    print("[selftest] determinism (除 runtime 外逐字节一致)")
    root = Path(tempfile.mkdtemp(prefix="skilltest-"))
    make_case(root, "dict")
    i, w, o = str(root / "input"), str(root / "work"), str(root / "output")
    run(["init", "--input", i, "--work", w, "--output", o])
    run(["build", "--input", i, "--work", w, "--output", o])
    r1 = load(o + "/result.json")
    run(["build", "--input", i, "--work", w, "--output", o])
    r2 = load(o + "/result.json")
    r1.pop("runtime", None)
    r2.pop("runtime", None)
    same = json.dumps(r1, ensure_ascii=False, sort_keys=True) == \
        json.dumps(r2, ensure_ascii=False, sort_keys=True)
    ok = check("byte_identical_except_runtime", same)
    shutil.rmtree(root, ignore_errors=True)
    return ok


def test_list_contract():
    print("[selftest] list-form output_contract")
    root = Path(tempfile.mkdtemp(prefix="skilltest-"))
    make_case(root, "list")
    i, w, o = str(root / "input"), str(root / "work"), str(root / "output")
    run(["init", "--input", i, "--work", w, "--output", o])
    r = load(o + "/result.json")
    ok = True
    for f in ("inventory", "duplicates", "version_groups", "search_results"):
        ok &= check("list_contract:" + f, f in r)
    shutil.rmtree(root, ignore_errors=True)
    return ok


def test_degrades():
    print("[selftest] 降级四场景")
    ok = True
    # 空输入
    root = Path(tempfile.mkdtemp(prefix="skilltest-"))
    (root / "input").mkdir(); (root / "work").mkdir(); (root / "output").mkdir()
    (root / "input" / "case.json").write_text('{"case_id":"empty-1"}', encoding="utf-8")
    i, w, o = str(root / "input"), str(root / "work"), str(root / "output")
    run(["init", "--input", i, "--work", w, "--output", o])
    run(["build", "--input", i, "--work", w, "--output", o])
    r = load(o + "/result.json")
    ok &= check("empty_status", r.get("status") == "insufficient_evidence", r.get("status"))
    shutil.rmtree(root, ignore_errors=True)

    # 坏 JSON
    root = Path(tempfile.mkdtemp(prefix="skilltest-"))
    (root / "input").mkdir(); (root / "work").mkdir(); (root / "output").mkdir()
    (root / "input" / "case.json").write_text("{bad json", encoding="utf-8")
    i, w, o = str(root / "input"), str(root / "work"), str(root / "output")
    run(["init", "--input", i, "--work", w, "--output", o])
    run(["build", "--input", i, "--work", w, "--output", o])
    r = load(o + "/result.json")
    ok &= check("badjson_status", r.get("status") == "input_error", r.get("status"))
    shutil.rmtree(root, ignore_errors=True)

    # 缺 case.json（有材料）
    root = Path(tempfile.mkdtemp(prefix="skilltest-"))
    (root / "input" / "materials").mkdir(parents=True)
    (root / "work").mkdir(); (root / "output").mkdir()
    (root / "input" / "materials" / "x.txt").write_text("x", encoding="utf-8")
    i, w, o = str(root / "input"), str(root / "work"), str(root / "output")
    run(["init", "--input", i, "--work", w, "--output", o])
    run(["build", "--input", i, "--work", w, "--output", o])
    r = load(o + "/result.json")
    ok &= check("missing_case_has_result", "case_id" in r)
    shutil.rmtree(root, ignore_errors=True)

    # 无 draft 直接 build
    root = Path(tempfile.mkdtemp(prefix="skilltest-"))
    make_case(root, "dict")
    i, w, o = str(root / "input"), str(root / "work"), str(root / "output")
    run(["init", "--input", i, "--work", w, "--output", o])
    # 删除 draft
    (Path(w) / "draft.json").unlink()
    run(["build", "--input", i, "--work", w, "--output", o])
    r = load(o + "/result.json")
    ok &= check("nodraft_status", r.get("status") == "partial", r.get("status"))
    deg = [d for d in r.get("degradations", []) if d.get("issue") == "no_draft"]
    ok &= check("nodraft_degradation", len(deg) > 0)
    shutil.rmtree(root, ignore_errors=True)
    return ok


def main():
    results = {}
    for name, fn in (
        ("happy_path", test_happy_path),
        ("determinism", test_determinism),
        ("list_contract", test_list_contract),
        ("degrades", test_degrades),
    ):
        try:
            results[name] = fn()
        except Exception as e:  # noqa: BLE001
            print("  FAIL %s 异常: %s" % (name, e))
            results[name] = False
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    print("[selftest] summary: %d/%d passed" % (passed, total))
    for name, v in results.items():
        print("  %s %s" % ("PASS" if v else "FAIL", name))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
