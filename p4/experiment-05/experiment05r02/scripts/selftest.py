#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — Skill 包自检脚本（仅标准库）。

在临时目录构造一个最小合成输入（case.json + task.md + 一小段 CSV），
依次跑 skeleton -> inspect -> validate -> finalize，并断言：

  1. result.json 可被 json.load 解析，case_id 非空，status 合法；
  2. 顶层契约键齐全，interpretation/plan/risk/confirm 子键齐全；
  3. report.md 与 data_profile.json 真实存在且非空；
  4. 空输入目录降级为 insufficient_evidence 且退出码 0；
  5. 坏 JSON 不使进程崩溃，只进入 errors/degradations。

退出码 0 表示全部通过；任一断言失败打印 FAIL 并以非 0 退出。
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core_impl import (  # noqa: E402
    TOP_KEYS, INTERPRETATION_KEYS, PLAN_KEYS, RISK_KEYS, CONFIRM_KEYS,
    build_skeleton, inspect_data, validate, finalize,
)

FAILED = []


def check(name, cond):
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        FAILED.append(name)


def make_case(root):
    os.makedirs(os.path.join(root, "input"), exist_ok=True)
    with open(os.path.join(root, "input", "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"id": "selftest-case", "output_contract": {
            "fields": ["extra_field"], "extra_field": {"x": 1}}}, fh, ensure_ascii=False)
    with open(os.path.join(root, "input", "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 合成自检\nchallenge_id: selftest-case\n")
    with open(os.path.join(root, "input", "sample.csv"), "w", encoding="utf-8") as fh:
        fh.write("t,v1,v2\n1,1.0,2.0\n2,1.2,2.1\n3,1.1,99.0\n4,1.3,2.3\n")


def run_full(root):
    inp = os.path.join(root, "input")
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    rp = build_skeleton(inp, out, work)
    inspect_data(inp, out, work)
    # 模拟 Agent 补一条带证据的结论后再定稿
    obj = json.load(open(rp, encoding="utf-8"))
    obj["status"] = "ok"
    obj["summary"] = "合成自检：仅验证流水线，不构成真实实验结论。"
    obj["interpretation"]["trends"].append({
        "claim": "v2 在第 3 行出现离群", "direction": "非单调",
        "evidence": [{"locator": "input/sample.csv#L4", "quote": "3,1.1,99.0"}]})
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    ok, _ = validate(rp)
    check("validate 在补证据后通过", ok)
    finalize(rp, work)
    return out


def main():
    root = tempfile.mkdtemp(prefix="skill_selftest_")
    try:
        make_case(root)
        out = run_full(root)
        rp = os.path.join(out, "result.json")
        obj = json.load(open(rp, encoding="utf-8"))

        check("result.json 可解析", isinstance(obj, dict))
        check("case_id 非空", bool(obj.get("case_id")))
        check("status 合法", obj.get("status") in
              ("ok", "partial", "insufficient_evidence", "input_error"))
        check("顶层键齐全", all(k in obj for k in TOP_KEYS))
        check("interpretation 子键齐全",
              all(k in obj["interpretation"] for k in INTERPRETATION_KEYS))
        check("experiment_plan 子键齐全",
              all(k in obj["experiment_plan"] for k in PLAN_KEYS))
        check("evidence_and_risk 子键齐全",
              all(k in obj["evidence_and_risk"] for k in RISK_KEYS))
        check("confirmation_items 子键齐全",
              all(k in obj["confirmation_items"] for k in CONFIRM_KEYS))
        check("output_contract 额外字段已回填", "extra_field" in obj)
        check("report.md 存在且非空",
              os.path.getsize(os.path.join(out, "report.md")) > 0)
        check("data_profile.json 存在且非空",
              os.path.getsize(os.path.join(out, "data_profile.json")) > 0)
        check("runtime 已闭合", obj.get("runtime", {}).get("end_ts"))

        # 空输入降级
        empty = os.path.join(root, "empty_in")
        os.makedirs(empty, exist_ok=True)
        eout = os.path.join(root, "empty_out")
        erp = build_skeleton(empty, eout, os.path.join(root, "empty_work"))
        eobj = json.load(open(erp, encoding="utf-8"))
        check("空输入 status=insufficient_evidence",
              eobj.get("status") == "insufficient_evidence")

        # 坏 JSON 不崩溃
        bad = os.path.join(root, "bad_in")
        os.makedirs(bad, exist_ok=True)
        with open(os.path.join(bad, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{not-valid-json")
        with open(os.path.join(bad, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# bad\n")
        brp = build_skeleton(bad, os.path.join(root, "bad_out"),
                             os.path.join(root, "bad_work"))
        bobj = json.load(open(brp, encoding="utf-8"))
        check("坏 JSON 仍产出合法骨架", isinstance(bobj, dict) and bobj.get("case_id"))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    if FAILED:
        print("\nFAILED: " + ", ".join(FAILED), file=sys.stderr)
        sys.exit(1)
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    main()
