#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验参数优化建议 Skill —— 自检脚本（仅标准库，无 subprocess）。

在同一进程内直接调用 main.py / check.py 的 run() 入口，避免管道捕获的
跨平台问题。覆盖四种降级场景 + 确定性（同输入两次产物逐字节一致）。

用法：python scripts/selftest.py [--keep]
退出码：0=全部通过；1=至少一项失败。
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import main  # noqa: E402
import check  # noqa: E402

FAILS = []


def ok(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print("[%s] %s%s" % (tag, name, (" — " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)
    return cond


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def demo_plan(case_id):
    """合成自检夹具，不是正式案例答案；仅用于验证管线契约与确定性。"""
    return {
        "case_id": case_id,
        "title": "SELFTEST FIXTURE (synthetic, not a real case)",
        "research_question": "在固定催化剂与反应器约束下，温度对转化率的最优可行取值是什么？",
        "objective": "在 20–80 °C 可行域内确定建议温度，并给出可复核的搜索路径。",
        "assumptions": ["假设历史转化率-温度趋势在本批次内单调可外推。"],
        "variables": ["温度 (℃)"],
        "parameters": [
            {"name": "temperature", "unit": "degC", "suggested": 60,
             "min": 20, "max": 80, "step": 10,
             "constraint_source": "materials/spec.md",
             "basis_locator": "materials/spec.md#L1",
             "basis_quote": "catalyst stable up to 80 °C"},
        ],
        "controls": ["对照：20 °C 基线批次。"],
        "procedures": [
            "按候选点顺序设定温度，每个点重复 1 次。",
            "记录转化率与能耗，计算响应指标。"],
        "resources": ["恒温反应器 ×1；在线转化率分析仪 ×1。"],
        "response_metrics": [
            {"name": "conversion", "definition": "主产物摩尔转化率",
             "unit": "%", "method": "在线气相色谱", "direction": "maximize",
             "target": ">= 70", "accept_lower": "55",
             "prediction_method": "历史数据线性插值代理",
             "uncertainty": "±5 个百分点（插值残差）"},
        ],
        "interpretation_criteria": ["若 conversion < accept_lower，则该点不可执行。"],
        "search_strategy": {
            "method": "grid",
            "initial_points": [20, 80],
            "per_round": "补齐网格中未测点",
            "stop_criteria": ["网格遍历完毕"],
            "candidate_count": 8,
            "seed": 0,
        },
        "cost_budget": {
            "items": [{"name": "反应器机时", "unit_cost": "50/h",
                       "quantity": "2h", "total": "100", "basis": "booked rate"}],
            "total": "100",
            "time_hours": 2,
            "tradeoff_note": "允许以 ±5 个百分点转化率换取一半机时成本。",
        },
        "basis": ["历史批次趋势。"],
        "risks": ["目标温度越界风险；指标互相矛盾时转人工。"],
        "exception_handling": ["无可行解 -> 报告并转人工；设备报错 -> 降级采样。"],
        "confirmations": ["安全阈值需实验安全员确认。"],
        "sources": [
            {"locator": "materials/spec.md#L1",
             "quote": "catalyst stable up to 80 °C",
             "claim": "温度上限 80 °C"},
        ],
        "uncertainty": {"unverified_values": [], "missing_data": [],
                        "assumptions": ["插值代理假设"]},
        "limitations": [],
        "pending_human": [],
        "change_log": [
            {"step": "init", "action": "write partial skeleton",
             "reason": "deliver early recoverable result"},
        ],
    }


def make_demo_input(root):
    write(os.path.join(root, "input", "case.json"),
          json.dumps({"case_id": "selftest-demo", "armiId": "ARMI:TEST"}))
    write(os.path.join(root, "input", "task.md"), "# demo task\nbounded optimization demo")
    write(os.path.join(root, "input", "materials", "spec.md"),
          "catalyst stable up to 80 °C\n")


def scenario_demo(keep):
    root = tempfile.mkdtemp(prefix="selftest-demo-")
    inp, out, work = (os.path.join(root, "input"),
                      os.path.join(root, "output"),
                      os.path.join(root, "work"))
    make_demo_input(root)
    ok("demo:init", main.run(["init", "--input", inp, "--output", out,
                              "--work", work]) == 0)

    plan_path = os.path.join(work, "plan.json")
    with open(plan_path, "r", encoding="utf-8") as fh:
        plan = json.load(fh)
    plan.update(demo_plan("selftest-demo"))
    write(plan_path, json.dumps(plan, ensure_ascii=False))

    ok("demo:doe", main.run(["doe", "--work", work, "--seed", "0"]) == 0)
    ok("demo:finalize", main.run(["finalize", "--input", inp, "--output", out,
                                  "--work", work]) == 0)

    r1 = read_bytes(os.path.join(out, "result.json"))
    m1 = read_bytes(os.path.join(out, "plan.md"))
    j1 = read_bytes(os.path.join(out, "plan.json"))
    c1 = read_bytes(os.path.join(out, "candidates.json"))

    # 再次 finalize：result/plan 必须逐字节一致。
    main.run(["finalize", "--input", inp, "--output", out, "--work", work])
    main.run(["doe", "--work", work, "--seed", "0"])
    r2 = read_bytes(os.path.join(out, "result.json"))
    m2 = read_bytes(os.path.join(out, "plan.md"))
    j2 = read_bytes(os.path.join(out, "plan.json"))
    c2 = read_bytes(os.path.join(work, "candidates.json"))

    ok("demo:deterministic-result", r1 == r2)
    ok("demo:deterministic-plan-md", m1 == m2)
    ok("demo:deterministic-plan-json", j1 == j2)
    ok("demo:deterministic-candidates", c1 == c2)

    with open(os.path.join(out, "result.json"), "r", encoding="utf-8") as fh:
        result = json.load(fh)
    ok("demo:status-ok", result.get("status") == "ok")
    ok("demo:fingerprint-present",
       isinstance(result.get("input_fingerprint"), str)
       and len(result["input_fingerprint"]) == 64)

    rc = check.run(["result", os.path.join(out, "result.json"),
                    "--output-dir", out])
    ok("demo:check-result-clean", rc == 0, "exit=%d" % rc)
    rc = check.run(["validate-plan", plan_path])
    ok("demo:validate-plan-no-block", rc in (0, 2), "exit=%d" % rc)

    with open(os.path.join(work, "candidates.json"), "r", encoding="utf-8") as fh:
        cands = json.load(fh)
    ok("demo:candidates-seeded", len(cands.get("points", [])) > 0
       and cands.get("seed") == 0)

    if not keep:
        shutil.rmtree(root, ignore_errors=True)
    else:
        print("demo fixtures kept at %s" % root)


def scenario_empty(keep):
    root = tempfile.mkdtemp(prefix="selftest-empty-")
    inp, out, work = (os.path.join(root, "input"),
                      os.path.join(root, "output"),
                      os.path.join(root, "work"))
    os.makedirs(inp)
    ok("empty:init", main.run(["init", "--input", inp, "--output", out,
                               "--work", work]) == 0)
    ok("empty:finalize", main.run(["finalize", "--input", inp, "--output", out,
                                   "--work", work]) == 0)
    with open(os.path.join(out, "result.json"), "r", encoding="utf-8") as fh:
        result = json.load(fh)
    ok("empty:insufficient-evidence", result.get("status") == "insufficient_evidence")
    if not keep:
        shutil.rmtree(root, ignore_errors=True)
    else:
        print("empty fixtures kept at %s" % root)


def scenario_bad_json(keep):
    root = tempfile.mkdtemp(prefix="selftest-badjson-")
    inp, out, work = (os.path.join(root, "input"),
                      os.path.join(root, "output"),
                      os.path.join(root, "work"))
    os.makedirs(inp)
    write(os.path.join(inp, "case.json"), "{{{ not valid json")
    write(os.path.join(inp, "task.md"), "# still readable task")
    ok("badjson:init", main.run(["init", "--input", inp, "--output", out,
                                 "--work", work]) == 0)
    ok("badjson:finalize", main.run(["finalize", "--input", inp, "--output", out,
                                     "--work", work]) == 0)
    with open(os.path.join(out, "result.json"), "r", encoding="utf-8") as fh:
        result = json.load(fh)
    ok("badjson:errors-recorded", len(result.get("errors", [])) > 0)
    ok("badjson:degraded-status",
       result.get("status") in ("partial", "insufficient_evidence", "input_error"))
    if not keep:
        shutil.rmtree(root, ignore_errors=True)
    else:
        print("badjson fixtures kept at %s" % root)


def scenario_missing(keep):
    root = tempfile.mkdtemp(prefix="selftest-missing-")
    inp, out, work = (os.path.join(root, "no-such-input"),
                      os.path.join(root, "output"),
                      os.path.join(root, "work"))
    ok("missing:init", main.run(["init", "--input", inp, "--output", out,
                                 "--work", work]) == 0)
    ok("missing:finalize", main.run(["finalize", "--input", inp, "--output", out,
                                     "--work", work]) == 0)
    with open(os.path.join(out, "result.json"), "r", encoding="utf-8") as fh:
        result = json.load(fh)
    ok("missing:no-crash-status", result.get("status") in
       ("insufficient_evidence", "input_error", "partial"))
    if not keep:
        shutil.rmtree(root, ignore_errors=True)
    else:
        print("missing fixtures kept at %s" % root)


def main_run():
    if "-h" in sys.argv or "--help" in sys.argv:
        print(__doc__)
        return 0
    keep = "--keep" in sys.argv
    print("== selftest: deterministic core + degraded inputs ==")
    scenario_demo(keep)
    scenario_empty(keep)
    scenario_bad_json(keep)
    scenario_missing(keep)
    print("--")
    if FAILS:
        print("FAILED: %s" % ", ".join(FAILS))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main_run())
