#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 用合成夹具自检 Skill 的健壮性与校验器（不预写任何正式案例答案）。

场景：
  A 空输入目录：probe 不崩、scaffold 产出合法骨架、check 报六要素缺失而非崩溃
  B 坏 JSON：probe 记录错误后继续
  C 完整契约：逐键镜像 + 未知根键保留
  D 算术/量程/逐字引用错误：check 全部捕获
  E 渲染确定性：同一 result 渲染两次字节一致
  F 逐字引用可验证：locator+quote 命中本地文本

夹具全部写入 tempfile.mkdtemp，运行后即删。退出码 0 = 全部通过。
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl as C  # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s" % name)
    else:
        FAIL += 1
        print("  FAIL  %s  %s" % (name, detail))


def fixture(root, files):
    inp = os.path.join(root, "input")
    os.makedirs(inp, exist_ok=True)
    for rel, text in files.items():
        p = os.path.join(inp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
    return inp


def case_a(tmp):
    print("[A] 空输入目录")
    inp = fixture(tmp, {})
    work = os.path.join(tmp, "workA")
    out = os.path.join(tmp, "outA")
    rep = C.probe(inp, work)
    ok("probe 不抛异常", isinstance(rep, dict))
    ok("case_id 降级 unknown", rep["case_id"] == "unknown")
    rc = C.cmd_scaffold(inp, out, work)
    res, err = C.load_json_file(os.path.join(out, "result.json"))
    ok("scaffold 产出合法 JSON", res is not None, str(err))
    ok("骨架含信封", isinstance(res, dict) and "status" in res and "change_log" in res)
    report = C.check_result(res, rep, inp, work, out)
    has_empty = any(e["code"] == "six_element_empty" for e in report["errors"])
    ok("check 报六要素缺失（不崩溃）", has_empty)


def case_b(tmp):
    print("[B] 坏 JSON")
    inp = fixture(tmp, {"case.json": "{ not valid json", "task.md": "challenge_id: `challenge-x`"})
    work = os.path.join(tmp, "workB")
    out = os.path.join(tmp, "outB")
    rep = C.probe(inp, work)
    ok("probe 记录坏 JSON 错误", any(e["code"] == "case_json_unreadable" for e in rep["errors"]))
    ok("case_id 来自 task.md", rep["case_id"] == "challenge-x", rep["case_id"])
    C.cmd_scaffold(inp, out, work)
    res, err = C.load_json_file(os.path.join(out, "result.json"))
    ok("坏 JSON 后仍能塑形", res is not None and res["case_id"] == "challenge-x")


def case_c(tmp):
    print("[C] 契约逐键镜像 + 未知键保留")
    contract = {
        "plan": {"variables": [], "parameters": []},
        "evidence_and_risks": {"evidence": [], "risks": []},
        "confirmations": {"safety": [], "unknown_leaf": {"a": None}},
        "extra_root_metric": "keep_me",
    }
    inp = fixture(
        tmp,
        {
            "case.json": json.dumps({"case_id": "c-1", "output_contract": contract}),
            "task.md": "# task\nchallenge_id: `challenge-cf`",
        },
    )
    work = os.path.join(tmp, "workC")
    out = os.path.join(tmp, "outC")
    rep = C.probe(inp, work)
    ok("探测到契约", rep["contract_source"] != "not_reported", rep["contract_source"])
    C.cmd_scaffold(inp, out, work)
    res, err = C.load_json_file(os.path.join(out, "result.json"))
    ok("scaffold 合法", res is not None, str(err))
    missing = [p for p in C.contract_leaf_paths(contract) if p and not C.get_path(res, p)[0]]
    ok("契约所有叶键被镜像", not missing, str(missing))
    ok("未知根键保留", "extra_root_metric" in res, str(list(res)))
    ok("信封叠加", all(k in res for k in ("status", "sources", "uncertainty", "change_log")))


def case_d(tmp):
    print("[D] 算术 / 量程 / 逐字引用错误全部捕获")
    inp = fixture(
        tmp,
        {
            "case.json": json.dumps({"case_id": "d-1"}),
            "materials/paper.md": "## Methods\nThe dose was set to 10 mg/kg. Unrelated sentence here.\n",
        },
    )
    work = os.path.join(tmp, "workD")
    out = os.path.join(tmp, "outD")
    rep = C.probe(inp, work)
    C.cmd_scaffold(inp, out, work)
    path = os.path.join(out, "result.json")
    res, _ = C.load_json_file(path)
    res["status"] = "ok"  # 让“六要素未齐但状态 ok”的诚实性检查可触发
    res["plan"] = {
        "design": {"n_per_group": 5, "n_groups": 4, "n_total": 21},
        "resources": {
            "equipment": [{"id": "lc", "range": [0, 100], "unit": "µM"}],
            "budget": {"currency": "CNY", "cap": 1000, "lines": [{"qty": 3, "unit_price": 100}], "total": 350},
            "bookings": [
                {"resource": "microscope", "sessions": 10, "session_min": 30, "window_min": 100, "unit_price": 50}
            ],
        },
        "parameters": [
            {"name": "dose", "value": 200, "unit": "µM", "basis": "assumption", "domain": [10, 20, 40], "equipment": "lc", "outside_range_action": None}
        ],
        "candidates": [
            {"id": "A", "hard_constraint_check": [{"constraint": "budget", "pass": False}], "rejected_reason": None},
            {"id": "B", "hard_constraint_check": [{"constraint": "budget", "pass": True}], "rejected_reason": None},
            {"id": "C", "hard_constraint_check": [{"constraint": "budget", "pass": True}], "rejected_reason": None},
        ],
        "selected_candidate": "A",
    }
    res["evidence_and_risks"] = {
        "evidence": [{"claim": "剂量为 10 mg/kg", "locator": "materials/paper.md", "quote": "The dose was 99 mg/kg."}]
    }
    C.atomic_write_json(path, res)
    report = C.check_result(res, rep, inp, work, out)
    codes = {e["code"] for e in report["errors"]}
    ok("预算求和错误", "arithmetic_violation" in codes, str(codes))
    ok("样本量不一致", "arithmetic_violation" in codes)
    ok("参数越域", "arithmetic_violation" in codes)
    ok("参数越量程", "arithmetic_violation" in codes)
    ok("预约超窗", "arithmetic_violation" in codes)
    ok("选中方案硬约束失败", "arithmetic_violation" in codes)
    ok("逐字引用不匹配", "evidence_quote_not_found" in codes, str(codes))
    ok("status 不诚实", "status_not_honest" in codes)


def case_e(tmp):
    print("[E] 渲染确定性")
    res = {"case_id": "e-1", "status": "partial", "plan": {"a": 1, "b": [{"x": 1}, {"x": 2}]}}
    ok("两次渲染一致", C.render_md(res) == C.render_md(res))


def case_f(tmp):
    print("[F] 逐字引用可验证通过")
    inp = fixture(tmp, {"materials/note.md": "The dose was set to 10 mg/kg exactly.\n"})
    work = os.path.join(tmp, "workF")
    rep = C.probe(inp, work)
    res = {
        "case_id": "f-1",
        "status": "partial",
        "plan": {},
        "evidence_and_risks": {
            "evidence": [{"claim": "剂量", "locator": "materials/note.md", "quote": "The dose was set to 10 mg/kg exactly."}]
        },
    }
    findings = C.verify_quotes(res, inp, work)
    ok("合法引用无 error", all(f["level"] != "error" for f in findings), str(findings))


def main():
    tmp = tempfile.mkdtemp(prefix="scix_selftest_")
    try:
        for fn in (case_a, case_b, case_c, case_d, case_e, case_f):
            try:
                fn(tmp)
            except Exception as exc:  # noqa: BLE001
                global FAIL
                FAIL += 1
                print("  FAIL  %s 抛出异常 %r" % (fn.__name__, exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("")
    print("selftest: %d passed, %d failed" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
