#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — end-to-end self test (standard library only).

Exercises the full chain on a synthetic bounded-parameter case:
    init -> fill plan -> doe -> finalize -> validate (expect exit 0)
plus degradation paths: empty input dir, broken case.json, out-of-domain value.

Exit 0 = PASS. No network, no third-party packages.
"""

import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
VALIDATE = os.path.join(HERE, "validate_plan.py")


def run(cmd, cwd=HERE):
    proc = subprocess.run([sys.executable, "-B"] + cmd, cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    return proc.returncode, proc.stdout, proc.stderr


def make_case(root, broken_json=False):
    os.makedirs(os.path.join(root, "input"), exist_ok=True)
    os.makedirs(os.path.join(root, "output"), exist_ok=True)
    os.makedirs(os.path.join(root, "work"), exist_ok=True)
    if broken_json:
        with open(os.path.join(root, "input", "case.json"), "w",
                  encoding="utf-8") as fh:
            fh.write("{not valid json")
    else:
        case = {"case_id": "synth-01",
                "topic": "catalytic conversion",
                "constraints_note": "synthetic, not a real lab quote"}
        with open(os.path.join(root, "input", "case.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(case, fh, ensure_ascii=False)
    with open(os.path.join(root, "input", "task.md"), "w",
              encoding="utf-8") as fh:
        fh.write("# 合成案例任务\n给定材料优化转化率，注意设备与安全边界。\n")
    with open(os.path.join(root, "input", "manual.md"), "w",
              encoding="utf-8") as fh:
        fh.write("反应器量程 20–200 °C；催化剂耐温上限 180 °C；"
                 "安全阈值 175 °C。\n历史：80 °C 时转化率约 62%。\n")


def fill_plan(work, case_id="synth-01"):
    plan_path = os.path.join(work, "plan.json")
    with open(plan_path, "r", encoding="utf-8") as fh:
        plan = json.load(fh)
    plan["case_id"] = case_id
    plan["status"] = "ok"
    plan["title"] = "合成案例：转化率优化"
    plan["research_question"] = "在设备量程与材料耐温内最大化转化率"
    plan["objective"] = "maximize conversion"
    plan["assumptions"] = ["历史数据可迁移", "无交互副作用"]
    plan["variables"] = [{"name": "temperature", "role": "independent",
                          "unit": "°C", "levels_or_range": [60, 120],
                          "measured_how": "热电偶"}]
    plan["parameters"] = [{
        "name": "temperature", "unit": "°C", "suggested_value": 80,
        "feasible_range": {"min": 60, "max": 120}, "step": 10,
        "equipment_limit": {"min": 20, "max": 200},
        "material_limit": {"min": None, "max": 180},
        "safety_threshold": {"min": None, "max": 175},
        "constraint_source": "s1", "locator": "input/manual.md#L1",
        "quote": "反应器量程 20–200 °C；催化剂耐温上限 180 °C",
        "rationale": "取历史中心点并留安全裕度",
    }]
    plan["controls"] = [{"name": "blank", "purpose": "基线", "type": "negative"}]
    plan["procedures"] = [{"step": 1, "action": "预热", "params": "80°C",
                           "expected": "稳定", "measure": "热电偶"}]
    plan["resources"] = [{"item": "催化剂", "qty": 1, "unit": "g",
                          "source": "synthetic"}]
    plan["response_metrics"] = [{
        "name": "conversion", "unit": "%", "measurement_method": "GC",
        "direction": "maximize", "target": 80, "acceptable_lower": 60,
        "uncertainty": "±2%", "prediction_method": "历史拟合",
        "prediction_note": "80°C 时约 62%（合成）",
    }]
    plan["interpretation_criteria"] = ["转化率≥60% 可接受"]
    plan["search_strategy"] = {
        "method": "sequential", "initial_points": [80],
        "per_round": "每次补 2 点，向响应上升方向 ±10°C",
        "stop_criteria": ["连续两轮改善 <2%", "越界即停"],
        "candidate_count": 8, "seed": 0}
    plan["cost_budget"] = {
        "items": [{"item": "机时", "qty": 8, "unit": "h", "unit_price": 10,
                   "subtotal": 80}],
        "total": "80", "time_hours": 8,
        "tradeoff_note": "成本受限时接受转化率下降 ≤5%",
        "acceptance_criteria": ["转化率≥60%"]}
    plan["basis"] = [{"claim": "量程", "source_id": "s1",
                      "locator": "input/manual.md#L1",
                      "quote": "反应器量程 20–200 °C"}]
    plan["risks"] = [{"risk": "超温", "trigger": "控温失效",
                      "impact": "材料失活", "mitigation": "安全阈值报警"}]
    plan["exception_handling"] = [
        {"scenario": "无可行解", "detection": "可行域为空",
         "action": "降级目标", "decision": "转人工"},
        {"scenario": "约束冲突", "detection": "目标温度>材料耐温",
         "action": "降低目标", "decision": "转人工"},
        {"scenario": "数据缺失", "detection": "无历史值",
         "action": "补做预实验", "decision": "转人工"},
        {"scenario": "设备报错", "detection": "报警", "action": "重试",
         "decision": "终止"},
        {"scenario": "指标矛盾", "detection": "两来源不一致",
         "action": "双记录", "decision": "转人工"},
    ]
    plan["confirmations"] = [
        {"item": "安全阈值", "status": "pending", "owner": "安全员"},
        {"item": "伦理审查", "status": "na", "owner": "IRB"},
        {"item": "设备可用性", "status": "pending", "owner": "实验室"},
        {"item": "专家确认", "status": "pending", "owner": "PI"},
    ]
    plan["sources"] = [{"source_id": "s1", "path": "input/manual.md",
                        "locator": "input/manual.md#L1",
                        "quote": "反应器量程 20–200 °C", "kind": "manual",
                        "page": None}]
    plan["evidence_map"] = {
        "schema_version": "2.0.0",
        "claims": [{
            "claim_id": "P1", "claim_type": "parameter_domain",
            "statement": "建议温度 80°C",
            "status": "supported", "confidence": "high",
            "provenance": [{"source_id": "s1", "locator": "input/manual.md#L1",
                            "quote": "反应器量程 20–200 °C",
                            "quote_type": "verbatim", "role": "domain_bound"}],
            "derivation": "量程交集取中心", "conflicts": [], "verified": True,
            "verification": "script_check"}],
        "conflict_count": 0, "unverified_count": 0}
    plan["uncertainty"] = {"unverified_values": [], "missing_data": [],
                           "assumption_risk": []}
    plan["limitations"] = ["合成案例，非真实实验"]
    plan["pending_human"] = ["安全阈值确认", "专家确认"]
    plan["errors"] = []
    plan["degradations"] = []
    plan["runtime"] = {"seconds": 1.0, "started_at": "2026-01-01T00:00:00Z",
                       "finished_at": "2026-01-01T00:00:01Z",
                       "model_requests": 3}
    plan["rubric_trace"] = [
        {"rubric_id": "R01", "dimension": "实际效果",
         "target_fields": "parameters,response_metrics,search_strategy,cost_budget",
         "gate": "四项非空", "evidence": "plan.json",
         "notes": "合成示例"},
    ]
    with open(plan_path, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, ensure_ascii=False, indent=2)
    return plan


def main():
    failures = []
    tmp = tempfile.mkdtemp(prefix="weekly-skill-selftest-")
    root = tmp
    try:
        # 1) full happy path
        make_case(root)
        rc, out, err = run([MAIN, "init", "--input",
                            os.path.join(root, "input"),
                            "--output", os.path.join(root, "output"),
                            "--work", os.path.join(root, "work")])
        if rc != 0:
            failures.append("init rc=%d: %s" % (rc, err))
        fill_plan(os.path.join(root, "work"))
        rc, out, err = run([MAIN, "doe", "--work",
                            os.path.join(root, "work"), "--seed", "0"])
        if rc != 0:
            failures.append("doe rc=%d: %s" % (rc, err))
        rc, out, err = run([MAIN, "finalize", "--input",
                            os.path.join(root, "input"),
                            "--output", os.path.join(root, "output"),
                            "--work", os.path.join(root, "work")])
        if rc != 0:
            failures.append("finalize rc=%d: %s" % (rc, err))
        rc, out, err = run([VALIDATE, os.path.join(root, "output",
                                                   "result.json")])
        if rc != 0:
            failures.append("validate happy rc=%d: %s\n%s" % (rc, out, err))
        res_path = os.path.join(root, "output", "result.json")
        with open(res_path, "r", encoding="utf-8") as fh:
            res = json.load(fh)
        for art in ("plan.md", "plan.json", "audit_report.json"):
            p = res.get("artifacts", {}).get(art)
            if not p or not os.path.exists(p):
                failures.append("artifact missing: %s" % art)
        if res.get("rubric_audit", {}).get("fail", 1) != 0:
            failures.append("happy path has failed hard gates")
        # deterministic doe reproducibility
        run([MAIN, "doe", "--work", os.path.join(root, "work"), "--seed", "0"])
        with open(os.path.join(root, "work", "candidates.json"),
                  encoding="utf-8") as fh:
            c1 = json.load(fh)
        run([MAIN, "doe", "--work", os.path.join(root, "work"), "--seed", "0"])
        with open(os.path.join(root, "work", "candidates.json"),
                  encoding="utf-8") as fh:
            c2 = json.load(fh)
        if json.dumps(c1["candidates"], sort_keys=True) != \
           json.dumps(c2["candidates"], sort_keys=True):
            failures.append("doe not deterministic")

        # 2) empty input dir -> init must still write partial skeleton
        root2 = tempfile.mkdtemp(prefix="weekly-skill-empty-")
        os.makedirs(os.path.join(root2, "input"), exist_ok=True)
        rc, out, err = run([MAIN, "init", "--input", os.path.join(root2,
                            "input"), "--output", os.path.join(root2,
                            "output"), "--work", os.path.join(root2, "work")])
        if rc != 0 or not os.path.exists(os.path.join(root2, "output",
                                                      "result.json")):
            failures.append("empty-input init failed: rc=%d %s" % (rc, err))

        # 3) broken case.json -> init must not crash
        root3 = tempfile.mkdtemp(prefix="weekly-skill-badjson-")
        make_case(root3, broken_json=True)
        rc, out, err = run([MAIN, "init", "--input", os.path.join(root3,
                            "input"), "--output", os.path.join(root3,
                            "output"), "--work", os.path.join(root3, "work")])
        if rc != 0 or not os.path.exists(os.path.join(root3, "output",
                                                      "result.json")):
            failures.append("broken-json init failed: rc=%d %s" % (rc, err))

        # 4) out-of-domain value -> validate must block (exit 1) when ok
        root4 = tempfile.mkdtemp(prefix="weekly-skill-oob-")
        make_case(root4)
        run([MAIN, "init", "--input", os.path.join(root4, "input"),
             "--output", os.path.join(root4, "output"),
             "--work", os.path.join(root4, "work")])
        fill_plan(os.path.join(root4, "work"))
        with open(os.path.join(root4, "work", "plan.json"),
                  encoding="utf-8") as fh:
            plan4 = json.load(fh)
        plan4["parameters"][0]["suggested_value"] = 500  # beyond 200/180/175
        with open(os.path.join(root4, "work", "plan.json"),
                  "w", encoding="utf-8") as fh:
            json.dump(plan4, fh, ensure_ascii=False)
        run([MAIN, "finalize", "--input", os.path.join(root4, "input"),
             "--output", os.path.join(root4, "output"),
             "--work", os.path.join(root4, "work")])
        rc, out, err = run([VALIDATE, os.path.join(root4, "output",
                                                   "result.json")])
        if rc != 1:
            failures.append("out-of-domain not blocked: rc=%d\n%s" % (rc, out))
    finally:
        pass  # keep temp dirs for debugging; OS cleans them up
    if failures:
        print("SELFTEST FAILED:")
        for f in failures:
            print(" - " + f)
        return 1
    print("SELFTEST PASS (init/doe/finalize/validate + empty/broken/oob)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
