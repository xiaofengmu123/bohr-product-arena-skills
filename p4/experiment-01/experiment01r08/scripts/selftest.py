#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-plan-arbiter 自检脚本（selftest）。

直接调用 scripts/main.py 的函数，不依赖子进程管道，覆盖：
  1) 有 output_contract：契约全键回填 + case_id + 骨架 + validate 退出码 0
  2) 空输入：insufficient_evidence 且不崩溃
  3) 坏 JSON：记录错误后继续，case_id 回退 task.md
  4) 算术检测：预算 subtotal/合计、排期关键路径不一致被捕获
  5) 确定性：两次 scaffold 内容一致（忽略时间量）

全部通过打印 PASS 并退出 0；否则打印 FAIL 并退出 1。
所有临时目录位于系统临时目录，运行后清理。
"""

import argparse
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl as m  # noqa: E402


def ns(input_dir, output_dir, work_dir, force=False):
    return argparse.Namespace(input=input_dir, output=output_dir, work=work_dir, force=force, cmd=None)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def normalize(obj):
    """去掉时间量（ts / runtime / generated_at），便于比较确定性。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ("ts", "runtime", "generated_at", "started_at"):
                continue
            out[k] = normalize(v)
        return out
    if isinstance(obj, list):
        return [normalize(v) for v in obj]
    return obj


def test_contract(base):
    root = os.path.join(base, "contract")
    inp = os.path.join(root, "input")
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    contract = {
        "plan": {"hypotheses": [], "variables": []},
        "evidence_and_risks": {"evidence": [], "risks": []},
        "confirmations": {"safety": []},
        "custom_field": {"x": None},
    }
    write(os.path.join(inp, "case.json"),
          json.dumps({"case_id": "case-selftest-001", "output_contract": contract}, ensure_ascii=False))
    write(os.path.join(inp, "task.md"), "# 自检用示例任务\n\n- challenge_id: ch-selftest\n")
    rc = m.run_scaffold(ns(inp, out, work))
    assert rc == 0, "scaffold 退出码 %s" % rc
    res = load(os.path.join(out, "result.json"))
    assert res.get("case_id") == "case-selftest-001", "case_id 错: %r" % res.get("case_id")
    for key in contract.keys():
        assert key in res, "缺契约字段: %s" % key
    assert res.get("status") == "partial", "status 错: %r" % res.get("status")
    assert isinstance(res.get("change_log"), list) and res["change_log"], "change_log 缺失"
    assert os.path.exists(os.path.join(out, "plan.md")), "plan.md 缺失"
    rc = m.run_validate(ns(inp, out, work))
    assert rc == 0, "validate 退出码 %s（骨架应可含警告但无 error）" % rc
    return "契约回填 + 骨架 + validate OK"


def test_empty(base):
    root = os.path.join(base, "empty")
    inp = os.path.join(root, "input")
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    os.makedirs(inp, exist_ok=True)
    rc = m.run_scaffold(ns(inp, out, work))
    assert rc == 0, "空输入 scaffold 退出码 %s" % rc
    res = load(os.path.join(out, "result.json"))
    assert res.get("status") == "insufficient_evidence", "空输入 status 错: %r" % res.get("status")
    assert res.get("case_id") == "unknown", "空输入 case_id 应 unknown"
    assert res.get("pending_human"), "空输入应写 pending_human"
    return "空输入 -> insufficient_evidence OK"


def test_bad_json(base):
    root = os.path.join(base, "badjson")
    inp = os.path.join(root, "input")
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    write(os.path.join(inp, "case.json"), "{ this is not valid json ")
    write(os.path.join(inp, "task.md"), "- challenge_id: ch-badjson\n")
    rc = m.run_scaffold(ns(inp, out, work))
    assert rc == 0, "坏 JSON scaffold 退出码 %s" % rc
    res = load(os.path.join(out, "result.json"))
    assert res.get("case_id") == "ch-badjson", "坏 JSON 应由 task.md 回退: %r" % res.get("case_id")
    assert res.get("errors"), "坏 JSON 应写入 errors"
    assert res.get("status") == "partial", "坏 JSON 有 task.md 时应为 partial"
    return "坏 JSON -> 记录错误并回退 case_id OK"


def test_arithmetic():
    res = {
        "case_id": "x",
        "status": "partial",
        "plan": {
            "budget": [{"item": "a", "unit_price": 10, "quantity": 3, "subtotal": 99}],
            "steps": [
                {"id": "a", "duration_min": 10},
                {"id": "b", "duration_min": 20, "depends_on": ["a"]},
            ],
            "total_duration_min": 25,
        },
        "budget": [{"item": "a", "unit_price": 10, "quantity": 3, "subtotal": 99}],
        "total": 30,
    }
    budget_errs = m.check_budget(res)
    sched_errs = m.check_schedule(res)
    assert budget_errs, "预算 subtotal/合计不一致未被捕获"
    assert sched_errs, "关键路径不一致未被捕获"
    # 正确值应通过
    good = {
        "plan": {
            "budget": [{"unit_price": 10, "quantity": 3, "subtotal": 30}],
            "steps": [
                {"id": "a", "duration_min": 10},
                {"id": "b", "duration_min": 20, "depends_on": ["a"]},
            ],
            "total_duration_min": 30,
        },
        "budget": [{"unit_price": 10, "quantity": 3, "subtotal": 30}],
        "total": 30,
    }
    assert not m.check_budget(good), "正确预算被误报: %s" % m.check_budget(good)
    assert not m.check_schedule(good), "正确排期被误报: %s" % m.check_schedule(good)
    return "预算/排期算术检测 OK"


def test_determinism(base):
    root = os.path.join(base, "determinism")
    inp = os.path.join(root, "input")
    write(os.path.join(inp, "case.json"),
          json.dumps({"case_id": "case-det", "output_contract": {"plan": {"hypotheses": []}}}, ensure_ascii=False))
    write(os.path.join(inp, "task.md"), "- challenge_id: ch-det\n")
    res_a = os.path.join(root, "out_a")
    res_b = os.path.join(root, "out_b")
    m.run_scaffold(ns(inp, res_a, os.path.join(root, "work_a")))
    m.run_scaffold(ns(inp, res_b, os.path.join(root, "work_b")))
    a = normalize(load(os.path.join(res_a, "result.json")))
    b = normalize(load(os.path.join(res_b, "result.json")))
    assert a == b, "两次 scaffold 内容不一致（忽略时间量后）"
    return "重复运行内容一致 OK"


def main():
    base = tempfile.mkdtemp(prefix="sci-plan-selftest-")
    tests = [
        ("contract", lambda: test_contract(base)),
        ("empty", lambda: test_empty(base)),
        ("bad_json", lambda: test_bad_json(base)),
        ("arithmetic", test_arithmetic),
        ("determinism", lambda: test_determinism(base)),
    ]
    failures = 0
    print("== sci-plan-arbiter selftest ==")
    for name, fn in tests:
        try:
            detail = fn()
            print("  [PASS] %-12s %s" % (name, detail))
        except AssertionError as exc:
            failures += 1
            print("  [FAIL] %-12s %s" % (name, exc))
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print("  [FAIL] %-12s exception: %r" % (name, exc))
    shutil.rmtree(base, ignore_errors=True)
    total = len(tests)
    if failures:
        print("RESULT: FAIL (%d/%d)" % (failures, total))
        return 1
    print("RESULT: PASS (%d/%d)" % (total, total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
