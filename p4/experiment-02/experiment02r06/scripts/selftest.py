#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 在不属于作者的临时目录里生成合成案例，端到端验证 Skill。

覆盖：骨架先落盘、full 全流程、七类维度齐备、证据结构、输出契约回填、
确定性复跑、坏 JSON 降级、空输入降级、Agent 复核合并。

合成案例只用于自检，不是正式答案，也不写入 outputs/。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
VERDICTS = ("ok", "conditional", "blocked", "unconfirmed", "missing", "conflict")
DIMENSIONS = ("assumptions_variables", "controls", "equipment_materials",
              "time_cost", "parameter_ranges", "risks_exceptions", "ethics_safety")
EVIDENCE_VERDICTS = ("ok", "conditional", "blocked", "conflict")

SYN_CASE = {
    "case_id": "SYN-001",
    "challenge_id": "challenge-self-test",
    "title": "合成自检案例：血清标志物检测（非真实实验）",
    "planned_start": "2026-04-01",
    "decision_date": "2026-03-01",
    "budget_cny": 60000,
    "output_contract": {
        "case_id": "string",
        "status": "string",
        "experiment_plan": "object",
        "review": "object",
        "plan_comparison": "object",
        "sources": "array",
        "uncertainty": "array",
        "pending_human": "array",
        "change_log": "array",
    },
    "parameter_domains": {
        "孵育温度": {"min": 35, "max": 38, "unit": "°C"},
        "离心转速": {"min": 3000, "max": 12000, "unit": "rpm"},
    },
    "resources": {
        "equipment": [
            {"id": "centrifuge-1", "name": "台式离心机", "status": "已具备",
             "capacity_hours_per_week": 20, "booked_hours_per_week": 16,
             "unit_price": 120, "price_unit": "CNY/h"},
            {"id": "plate-reader", "name": "酶标仪", "status": "需采购",
             "lead_time_days": 45, "unit_price": 300, "price_unit": "CNY/h"},
            {"id": "incubator", "name": "恒温培养箱", "status": "已具备"},
        ],
        "materials": [
            {"id": "DMEM", "stock": 200, "unit": "mL", "pack_size": 500,
             "price": 180, "lead_time_days": 10},
            {"id": "FBS", "stock": 0, "unit": "mL", "pack_size": 100,
             "price": 900, "lead_time_days": 30},
        ],
    },
    "contracts": {
        "plate-reader": {"status": "需采购", "lead_time_days": 45,
                         "unit_price": 300, "price_unit": "CNY/h"},
    },
    "samples": {"type": "human_serum", "available": 40, "consent": True},
    "ethics": [
        {"scope": "human", "status": "已批准", "valid_until": "2027-01-01",
         "approval_id": "IRB-2026-01"},
    ],
    "plans": [
        {
            "id": "v1",
            "research_question": "验证 ELISA 平台能否稳定检出 1 ng/mL 目标蛋白",
            "hypothesis": "在 37°C、5000 rpm 条件下，检测限可达 1 ng/mL",
            "assumptions": ["血清样本 -80°C 保存稳定", "试剂批次间 CV<15%"],
            "variables": [
                {"name": "样本稀释比", "role": "independent", "levels": ["1:1", "1:10"]},
                {"name": "OD450", "role": "dependent", "unit": "AU"},
            ],
            "controls": ["空白对照", "阳性对照"],
            "criteria": ["标准曲线 R²≥0.99", "阳性对照回收率 90–110%"],
            "samples": {"n": 12, "per_arm": True, "unit": "samples"},
            "parameters": {"孵育温度": {"value": 37, "unit": "°C"},
                           "离心转速": {"value": 5000, "unit": "rpm"}},
            "steps": [
                {"name": "样本稀释", "duration": "60 min", "operator": "30 min"},
                {"name": "加样孵育", "duration": "120 min", "equipment": ["incubator"]},
                {"name": "离心", "duration": "30 min", "equipment": ["centrifuge-1"],
                 "parameters": {"离心转速": {"value": 5000, "unit": "rpm"}}},
                {"name": "读板", "duration": "30 min", "equipment": ["plate-reader"],
                 "materials": [{"id": "FBS", "qty": 50, "unit": "mL"}]},
            ],
            "risks": [
                {"risk": "酶标仪到货延迟", "mitigation": ["改外协检测"]},
                {"risk": "血清样本不足", "mitigation": []},
            ],
            "personnel": [{"role": "技师", "n": 1, "hours_per_week": 40}],
        },
        {
            "id": "v2",
            "research_question": "验证 ELISA 平台能否稳定检出 1 ng/mL 目标蛋白",
            "hypothesis": "提高转速到 15000 rpm 可降低背景",
            "assumptions": ["血清样本 -80°C 保存稳定"],
            "variables": [
                {"name": "样本稀释比", "role": "independent", "levels": ["1:1", "1:10"]},
                {"name": "OD450", "role": "dependent", "unit": "AU"},
            ],
            "controls": ["空白对照"],
            "samples": {"n": 12, "per_arm": True, "unit": "samples"},
            "parameters": {"孵育温度": {"value": 37, "unit": "°C"},
                           "离心转速": {"value": 15000, "unit": "rpm"}},
            "steps": [
                {"name": "样本稀释", "duration": "60 min"},
                {"name": "加样孵育", "duration": "120 min", "equipment": ["incubator"]},
                {"name": "离心", "duration": "30 min", "equipment": ["centrifuge-1"],
                 "parameters": {"离心转速": {"value": 15000, "unit": "rpm"}}},
                {"name": "读板", "duration": "30 min", "equipment": ["plate-reader"],
                 "materials": [{"id": "FBS", "qty": 50, "unit": "mL"}]},
            ],
            "risks": [],
            "personnel": [{"role": "技师", "n": 1, "hours_per_week": 40}],
        },
        {
            "id": "v3",
            "research_question": "验证 ELISA 平台能否稳定检出 1 ng/mL 目标蛋白",
            "hypothesis": "减少样本量仍能保证统计效力",
            "assumptions": ["试剂批次间 CV<15%"],
            "variables": [{"name": "OD450", "role": "dependent", "unit": "AU"}],
            "controls": [],
            "samples": {"n": 30, "per_arm": True, "unit": "samples"},
            "parameters": {"孵育温度": {"value": 40, "unit": "°C"}},
            "steps": [
                {"name": "加样孵育", "duration": "120 min", "equipment": ["incubator"]},
                {"name": "读板", "duration": "30 min", "equipment": ["plate-reader"],
                 "materials": [{"id": "FBS", "qty": 80, "unit": "mL"}]},
            ],
            "risks": [{"risk": "样本不足", "mitigation": []}],
            "personnel": [],
        },
    ],
}


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def run_main(stage, input_dir, output_dir, work_dir, adjudication=None):
    cmd = [sys.executable, "-B", MAIN, "--stage", stage,
           "--input", input_dir, "--output", output_dir, "--work", work_dir,
           "--quiet"]
    if adjudication:
        cmd += ["--adjudication", adjudication]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return proc


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)


def check(cond, msg, failures):
    if cond:
        print("  PASS %s" % msg)
    else:
        print("  FAIL %s" % msg)
        failures.append(msg)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    failures = []
    print("selftest: experiment-feasibility-review")
    # --help 可用
    proc = subprocess.run([sys.executable, "-B", MAIN, "--help"],
                          capture_output=True, text=True, encoding="utf-8")
    check(proc.returncode == 0, "--help 退出码 0", failures)

    tmp = tempfile.mkdtemp(prefix="efr-selftest-")
    case_dir = os.path.join(tmp, "case")
    os.makedirs(case_dir)
    write_json(os.path.join(case_dir, "case.json"), SYN_CASE)

    o1 = os.path.join(tmp, "out-skel")
    w1 = os.path.join(tmp, "work-skel")
    proc = run_main("skeleton", case_dir, o1, w1)
    check(proc.returncode == 0, "skeleton 退出码 0", failures)
    skel = load_json(os.path.join(o1, "result.json"))
    check(skel.get("case_id") == "SYN-001", "skeleton case_id", failures)
    check(skel.get("status") == "partial", "skeleton 状态 partial", failures)
    check(all(k in skel for k in ("experiment_plan", "review", "uncertainty",
                                  "pending_human", "change_log")),
          "skeleton 契约字段齐备", failures)
    check(set(DIMENSIONS) <= set(skel["review"].keys()), "skeleton 七类维度齐备", failures)
    check(set(SYN_CASE["output_contract"].keys()) <= set(skel.keys()),
          "skeleton 回填 output_contract 字段", failures)

    o2 = os.path.join(tmp, "out-full-1")
    o3 = os.path.join(tmp, "out-full-2")
    proc = run_main("full", case_dir, o2, os.path.join(tmp, "work-2"))
    check(proc.returncode == 0, "full 退出码 0", failures)
    result = load_json(os.path.join(o2, "result.json"))
    check(result.get("status") in ("ok", "partial"), "full 状态合法", failures)
    check(set(DIMENSIONS) <= set(result["review"].keys()), "full 七类维度齐备", failures)
    findings = [f for arr in result["review"].values() for f in arr]
    check(len(findings) >= 7, "至少 7 条审查判断", failures)
    check(all(f.get("verdict") in VERDICTS for f in findings), "verdict 枚举合法", failures)
    bad_evidence = [f["id"] for f in findings
                    if f["verdict"] in EVIDENCE_VERDICTS and not f.get("evidence")]
    check(not bad_evidence, "ok/conditional/blocked/conflict 均带证据", failures)
    check(any(f["verdict"] == "blocked" for f in findings),
          "检出合成案例中的越界/阻断项（离心 15000 rpm）", failures)
    plan_model = load_json(os.path.join(o2, "plan_model.json"))
    check(plan_model.get("schema_version") == "plan-model/1.0", "plan_model schema", failures)
    check(len(plan_model.get("plans") or []) == 3, "plan_model 含 3 个版本化方案", failures)
    calc_trace = load_json(os.path.join(o2, "calc_trace.json"))
    check(calc_trace.get("count") and calc_trace["count"] >= 1, "calc_trace 有派生数值", failures)
    check(os.path.exists(os.path.join(o2, "review_report.md")),
          "review_report.md 已生成", failures)

    # 确定性
    proc = run_main("full", case_dir, o3, os.path.join(tmp, "work-3"))
    same_pm = open(os.path.join(o2, "plan_model.json"), "rb").read() == \
        open(os.path.join(o3, "plan_model.json"), "rb").read()
    same_ct = open(os.path.join(o2, "calc_trace.json"), "rb").read() == \
        open(os.path.join(o3, "calc_trace.json"), "rb").read()
    same_res = open(os.path.join(o2, "result.json"), "rb").read() == \
        open(os.path.join(o3, "result.json"), "rb").read()
    check(same_pm and same_ct and same_res, "重复运行字节级一致（确定性）", failures)

    # 坏 JSON
    bad_dir = os.path.join(tmp, "bad")
    os.makedirs(bad_dir)
    with open(os.path.join(bad_dir, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{ not valid json")
    proc = run_main("full", bad_dir, os.path.join(tmp, "out-bad"), os.path.join(tmp, "work-bad"))
    bad_res = load_json(os.path.join(tmp, "out-bad", "result.json"))
    check(proc.returncode == 0 and bad_res.get("status") == "input_error",
          "坏 JSON → input_error 且退出码 0", failures)

    # 空输入
    empty_dir = os.path.join(tmp, "empty")
    os.makedirs(empty_dir)
    proc = run_main("full", empty_dir, os.path.join(tmp, "out-empty"),
                    os.path.join(tmp, "work-empty"))
    empty_res = load_json(os.path.join(tmp, "out-empty", "result.json"))
    check(proc.returncode == 0 and empty_res.get("status") == "insufficient_evidence",
          "空输入 → insufficient_evidence 且退出码 0", failures)

    # 复核合并
    adj_path = os.path.join(tmp, "adjudication.json")
    write_json(adj_path, {
        "overrides": [{"finding_id": "v1.AV-2", "verdict": "conditional",
                       "conditions": ["补充批次一致性与样本稳定性依据"]}],
        "additions": [{"id": "AGT-01", "dimension": "ethics_safety",
                       "subject": "伦理补充", "claim": "复核对样本知情同意进行确认",
                       "verdict": "ok", "evidence": [
                           {"locator": "case.json#samples", "quote": "consent: true",
                            "kind": "plan"}]}],
    })
    proc = run_main("full", case_dir, os.path.join(tmp, "out-adj"),
                    os.path.join(tmp, "work-adj"), adjudication=adj_path)
    adj_res = load_json(os.path.join(tmp, "out-adj", "result.json"))
    adj_findings = [f for arr in adj_res["review"].values() for f in arr]
    check(any(f["id"] == "AGT-01" for f in adj_findings), "复核新增 finding 已合并", failures)
    v1av2 = [f for f in adj_findings if f["id"] == "v1.AV-2"][0]
    check("Agent 复核覆盖" in v1av2.get("notes", []), "复核覆盖已记录", failures)

    print("")
    if failures:
        print("selftest FAILED: %d 项" % len(failures))
        return 1
    print("selftest OK: 全部断言通过（临时目录 %s）" % tmp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
