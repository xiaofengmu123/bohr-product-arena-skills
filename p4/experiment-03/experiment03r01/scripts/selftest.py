#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检脚本：在临时目录构造一个合成有界参数优化案例，完整跑一遍
init -> finalize -> validate_plan -> doe -> evidence -> check，验证退出码、
产物存在性与 evidence_map 逐字溯源。仅依赖标准库；退出码 0=PASS，非 0=FAIL。

运行：python3 -B scripts/selftest.py
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
VALIDATE = os.path.join(HERE, "validate_plan.py")


def write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def make_demo(root):
    inp = os.path.join(root, "input")
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    for d in (inp, os.path.join(inp, "materials"), out, work):
        os.makedirs(d, exist_ok=True)

    case = {"case_id": "demo-case-001", "topic": "bounded_parameter_optimization"}
    write(os.path.join(inp, "case.json"), json.dumps(case, ensure_ascii=False))
    write(os.path.join(inp, "task.md"),
          "# 合成案例\n给定反应器量程 20–200 °C、材料耐温 ≤120 °C、安全阈值 ≤130 °C，"
          "优化反应温度与时间。\n")
    write(os.path.join(inp, "materials", "manual.md"),
          "## 设备与材料\n反应器量程 20–200 °C，催化剂耐温 120 °C。\n"
          "建议初温 80 °C，步进 10 °C。响应为转化率（%），方向越大越好。\n")
    return inp, out, work


def write_plan(work):
    plan = {
        "case_id": "demo-case-001",
        "status": "partial",
        "title": "合成案例方案",
        "research_question": "在量程与耐温约束下最大化转化率",
        "objective": "给出可行温度/时间初值",
        "assumptions": ["材料耐温为硬约束"],
        "variables": [{"name": "温度", "role": "independent", "unit": "°C"}],
        "parameters": [
            {"name": "温度", "unit": "°C", "suggested_value": 80,
             "feasible_range": {"min": 20, "max": 120}, "step": 10,
             "equipment_limit": {"min": 20, "max": 200},
             "material_limit": {"max": 120},
             "safety_threshold": {"max": 130},
             "constraint_source": "s1",
             "locator": "materials/manual.md#L2",
             "quote": "反应器量程 20–200 °C，催化剂耐温 120 °C",
             "rationale": "取量程上限与耐温的交集，初值 80°C"},
            {"name": "时间", "unit": "min", "suggested_value": 30,
             "feasible_range": {"min": 5, "max": 60}, "step": 5,
             "constraint_source": "s1",
             "locator": "materials/manual.md#L2",
             "quote": "建议初温 80 °C，步进 10 °C",
             "rationale": "经验初值"},
        ],
        "controls": [{"name": "空白对照", "purpose": "无催化剂基线"}],
        "procedures": [{"step": 1, "action": "设定温度", "params": "80°C"}],
        "resources": [{"item": "催化剂", "qty": 1, "unit": "批次"}],
        "response_metrics": [
            {"name": "转化率", "unit": "%", "measurement_method": "GC",
             "direction": "maximize", "target": 95, "acceptable_lower": 80,
             "uncertainty": "±2%", "prediction_method": "经验规则",
             "prediction_note": "未用历史数据拟合，不确定性高"},
        ],
        "interpretation_criteria": ["转化率 ≥ 80% 为可接受"],
        "search_strategy": {"method": "分阶段筛选", "initial_points": [{"温度": 80}],
                            "per_round": "单因子扰动", "stop_criteria": ["候选点≥8"],
                            "candidate_count": 8, "seed": 0},
        "cost_budget": {"items": [{"item": "机时", "unit": "h", "qty": 2,
                                   "unit_price": 100, "amount": 200}],
                        "total": "200", "time_hours": 2,
                        "tradeoff_note": "成本受限时接受转化率下限 80%"},
        "basis": [{"claim": "量程 20–200°C", "source_id": "s1",
                   "locator": "materials/manual.md#L2",
                   "quote": "反应器量程 20–200 °C，催化剂耐温 120 °C"}],
        "risks": [{"risk": "温度越耐温", "trigger": "目标>120°C", "impact": "催化剂失活",
                   "mitigation": "上限 120°C"}],
        "exception_handling": [{"scenario": "无可行解", "detection": "可行域为空",
                                "action": "降级/转人工", "decision": "终止"}],
        "confirmations": [{"item": "安全审批", "status": "pending", "owner": "专家"}],
        "sources": [{"source_id": "s1", "path": "materials/manual.md",
                     "locator": "materials/manual.md#L2",
                     "quote": "反应器量程 20–200 °C，催化剂耐温 120 °C",
                     "kind": "material", "page": 1}],
        "claims": [
            {"claim_id": "P1", "claim_type": "parameter_value",
             "statement": "建议温度 80 °C",
             "status": "supported", "confidence": "medium",
             "provenance": [{"source_id": "s1",
                             "locator": "materials/manual.md#L2",
                             "quote": "建议初温 80 °C，步进 10 °C",
                             "quote_type": "verbatim", "role": "value_support"}],
             "derivation": "直接取材料建议初值",
             "conflicts": [], "verified": True, "verification": "script_check"},
            {"claim_id": "C1", "claim_type": "constraint",
             "statement": "温度上限 120 °C（材料耐温）",
             "status": "supported", "confidence": "high",
             "provenance": [{"source_id": "s1",
                             "locator": "materials/manual.md#L2",
                             "quote": "催化剂耐温 120 °C",
                             "quote_type": "verbatim", "role": "domain_bound"}],
             "derivation": "材料耐温作为硬约束",
             "conflicts": [], "verified": True, "verification": "script_check"},
        ],
        "uncertainty": {"unverified_values": ["时间初值 30min"],
                        "missing_data": ["历史转化率数据"], "assumption_risk": []},
        "limitations": ["合成案例，非真实实验室"],
        "pending_human": ["时间初值需专家确认"],
        "change_log": [{"time": "2026-11-23T00:00:00Z", "entry": "自检构建"}],
    }
    write(os.path.join(work, "plan.json"), json.dumps(plan, ensure_ascii=False))


def run(args, cwd=None):
    return subprocess.run([sys.executable, "-B"] + args, cwd=cwd,
                          capture_output=True, encoding="utf-8", errors="replace")


def main():
    fails = []
    with tempfile.TemporaryDirectory(prefix="weekly-skill-selftest-") as tmp:
        inp, out, work = make_demo(tmp)

        def step(name, cmd):
            cp = run(cmd, cwd=HERE)
            if cp.returncode != 0:
                fails.append("%s (exit %d): %s" % (name, cp.returncode,
                                                   cp.stderr.strip()[:400]))
            return cp

        step("init", [MAIN, "init", "--input", inp, "--output", out, "--work", work])
        # 模拟 Agent 在 init 骨架之后填充 plan.json（真实流程：init 只落骨架）
        write_plan(work)
        # validate_plan：期望仅警告或通过（退出码 0 或 2）
        cp = run([VALIDATE, os.path.join(work, "plan.json")], cwd=HERE)
        if cp.returncode not in (0, 2):
            fails.append("validate_plan (exit %d)" % cp.returncode)
        step("finalize", [MAIN, "finalize", "--input", inp, "--output", out,
                          "--work", work])
        step("doe", [MAIN, "doe", "--input", inp, "--output", out, "--work", work])
        step("evidence", [MAIN, "evidence", "--input", inp, "--output", out,
                          "--work", work])
        step("check", [MAIN, "check", "--input", inp, "--output", out,
                       "--work", work])

        # 产物存在性与证据溯源断言
        result_path = os.path.join(out, "result.json")
        if not os.path.exists(result_path):
            fails.append("result.json 未生成")
        else:
            with open(result_path, encoding="utf-8") as fh:
                result = json.load(fh)
            if result.get("case_id") != "demo-case-001":
                fails.append("case_id 不正确")
            if result.get("status") not in ("ok", "partial",
                                            "insufficient_evidence", "input_error"):
                fails.append("status 非法")
            for a in ("plan.md", "plan.json"):
                if not os.path.exists(result["artifacts"][a]):
                    fails.append("artifact 缺失: %s" % a)
            claims = result["evidence_map"]["claims"]
            if not claims:
                fails.append("evidence_map.claims 为空")
            for c in claims:
                for p in c["provenance"]:
                    if not p.get("locator") or not p.get("quote"):
                        fails.append("claim %s 缺 locator/quote" % c.get("claim_id"))
        cand = os.path.join(work, "candidates.json")
        if not os.path.exists(cand):
            fails.append("candidates.json 未生成")
        else:
            with open(cand, encoding="utf-8") as fh:
                cand_obj = json.load(fh)
            if not cand_obj.get("candidates"):
                fails.append("候选点为空")

    if fails:
        print("SELFTEST FAIL (%d 项):" % len(fails))
        for f in fails:
            print("  - %s" % f)
        return 1
    print("SELFTEST PASS: init/finalize/validate_plan/doe/evidence/check 全链路通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
