#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill 自检脚本（仅标准库，确定性）。

在系统临时目录构造合成夹具并跑通 init -> validate -> doe -> finalize -> check，
另覆盖空输入、坏 JSON、建议值越界三种降级场景。合成夹具仅为自检，非正式案例答案。
用法: python scripts/selftest.py
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import (cmd_init, cmd_finalize, cmd_doe, cmd_check,  # noqa: E402
                  validate_plan_obj, new_skeleton_plan, write_json)

FAIL = []


def expect(name, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + name + ((" — " + detail) if detail else ""))
    if not cond:
        FAIL.append(name)


def make_input(root):
    input_dir = os.path.join(root, "input")
    materials = os.path.join(input_dir, "materials")
    os.makedirs(materials, exist_ok=True)
    case = {
        "case_id": "selftest-case-001",
        "task": "合成自检案例：在设备量程内为反应温度与压力给出参数方案",
        "constraints": {"device": "量程 0–300 °C, 0–10 MPa",
                        "material_tolerance": "耐温 ≤ 260 °C"},
    }
    write_json(os.path.join(input_dir, "case.json"), case)
    with open(os.path.join(input_dir, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 合成自检任务\n\n目标：提高转化率，控制副产物。\n")
    with open(os.path.join(materials, "device_spec.md"), "w", encoding="utf-8") as fh:
        fh.write("## 设备规格\n\n反应釜量程：温度 0–300 °C，压力 0–10 MPa。\n")
    with open(os.path.join(materials, "literature.md"), "w", encoding="utf-8") as fh:
        fh.write("## 材料\n\n催化剂耐温 ≤ 260 °C。历史记录：180 °C 转化率约 82%。\n")
    return input_dir


def build_plan(case_id, out_of_bound=False):
    plan = new_skeleton_plan(case_id)
    plan["status"] = "partial"
    plan["title"] = "合成自检方案"
    plan["research_question"] = "在设备量程与材料耐温内，温度与压力如何取值以最大化转化率"
    plan["objective"] = "给出可执行参数并说明依据、风险与待确认项"
    plan["assumptions"] = [{"name": "历史转化率可迁移", "evidence_level": "assumed"}]
    plan["variables"] = ["反应温度", "反应压力"]
    plan["controls"] = ["催化剂批次", "进料组成"]
    plan["procedures"] = ["1) 升温至设定温度", "2) 恒压反应并采样"]
    plan["resources"] = ["高压反应釜", "气相色谱"]
    temp = {
        "name": "反应温度", "unit": "°C", "suggested_value": 260 if out_of_bound else 180,
        "min": 120, "max": 260, "step": 20,
        "device_limit": {"min": 0, "max": 300},
        "material_limit": {"min": None, "max": 260},
        "constraint_source": "materials/literature.md#催化剂耐温",
        "quote": "催化剂耐温 ≤ 260 °C",
        "evidence_level": "quoted",
    }
    if out_of_bound:
        temp["suggested_value"] = 280
    press = {
        "name": "反应压力", "unit": "MPa", "suggested_value": 3.0,
        "min": 1.0, "max": 8.0, "step": 1.0,
        "device_limit": {"min": 0, "max": 10},
        "constraint_source": "materials/device_spec.md#反应釜量程",
        "quote": "反应釜量程：压力 0–10 MPa",
        "evidence_level": "quoted",
    }
    plan["parameters"] = [temp, press]
    plan["response_metrics"] = [{
        "name": "转化率", "unit": "%", "method": "气相色谱外标法",
        "direction": "maximize", "target": 85, "acceptable_lower": 70,
        "constraint_source": "materials/literature.md#历史记录",
        "quote": "180 °C 转化率约 82%",
        "evidence_level": "quoted",
    }]
    plan["interpretation_criteria"] = ["转化率与选择性同步记录，单指标不结论"]
    plan["search_strategy"] = {"method": "分阶段网格+序贯",
                               "initial_points": ["180 °C, 3.0 MPa"],
                               "per_round": "每轮 2 点",
                               "stop_criteria": ["可行域内无法再提升 1% 即停"],
                               "candidate_count": 8, "seed": 0}
    plan["cost_budget"] = {"items": [{"name": "机时", "value": "4 h"}],
                           "total": "机时 4 h + 试剂 1 批次",
                           "time_hours": 4, "tradeoff_note": "最多牺牲 2% 转化率换 1 h 机时"}
    plan["basis"] = [{"locator": "materials/literature.md#历史记录",
                      "quote": "180 °C 转化率约 82%"}]
    plan["risks"] = ["目标温度与材料耐温冲突风险"]
    plan["exception_handling"] = ["若候选点越界，降级至可行域内最近点并记录"]
    plan["confirmations"] = ["安全审批", "设备可用性确认"]
    plan["sources"] = [{"locator": "materials/literature.md#催化剂耐温",
                        "quote": "催化剂耐温 ≤ 260 °C"},
                       {"locator": "materials/device_spec.md#反应釜量程",
                        "quote": "反应釜量程：温度 0–300 °C，压力 0–10 MPa"}]
    plan["uncertainty"] = {"unverified_values": ["历史转化率未实测"],
                           "missing_data": ["副产物选择性数据"],
                           "assumptions": ["历史记录可迁移"],
                           "model_notes": "经验规则外推，未做代理模型拟合"}
    plan["limitations"] = ["合成自检夹具，非正式案例数据"]
    plan["pending_human"] = ["专家确认设备量程与材料耐温的最新口径"]
    plan["change_log"] = [{"at": "2026-01-01T00:00:00Z", "entry": "自检初始化"}]
    return plan


def run_pipeline(root, plan, label):
    input_dir = make_input(root)
    output_dir = os.path.join(root, "output")
    work_dir = os.path.join(root, "work")
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    r = cmd_init(input_dir, output_dir, work_dir)
    expect(label + ": init 产出 case_id", r.get("case_id") == "selftest-case-001")
    write_json(os.path.join(work_dir, "plan.json"), plan)
    errs, warns = validate_plan_obj(plan)
    expect(label + ": validate 无阻断错误", not errs, "; ".join(errs))
    doe = cmd_doe(work_dir, seed=0)
    pts = (doe.get("candidates") or {}).get("points") or []
    expect(label + ": doe 生成候选点", len(pts) > 0 and len(pts) <= 8,
           "n=%d" % len(pts))
    fin = cmd_finalize(input_dir, output_dir, work_dir)
    expect(label + ": finalize 成功", fin.get("ok"))
    chk = cmd_check(output_dir)
    expect(label + ": check 无阻断错误", chk.get("exit_code", 1) in (0, 2),
           "exit=%d" % chk.get("exit_code", 1))
    return output_dir


def main():
    root = tempfile.mkdtemp(prefix="skill-selftest-")
    try:
        # 1) 正常流程
        plan = build_plan("selftest-case-001")
        out = run_pipeline(os.path.join(root, "ok"), plan, "normal")
        result = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        expect("normal: result.json 字段齐全",
               all(k in result for k in ("case_id", "status", "plan", "sources",
                                         "uncertainty", "pending_human", "change_log")))
        # 2) 建议值越界（材料耐温）应被阻断
        plan2 = build_plan("selftest-case-001", out_of_bound=True)
        errs, _ = validate_plan_obj(plan2)
        expect("out-of-bound: 越界被识别为阻断错误",
               any("超出材料限" in e for e in errs))
        # 3) 空输入目录
        empty = os.path.join(root, "empty")
        os.makedirs(os.path.join(empty, "input"))
        cmd_init(os.path.join(empty, "input"), os.path.join(empty, "output"),
                 os.path.join(empty, "work"))
        res2 = json.load(open(os.path.join(empty, "output", "result.json"),
                              encoding="utf-8"))
        expect("empty-input: 落盘 insufficient_evidence",
               res2.get("status") == "insufficient_evidence")
        # 4) 坏 JSON
        bad = os.path.join(root, "bad")
        os.makedirs(os.path.join(bad, "input"))
        with open(os.path.join(bad, "input", "case.json"), "w",
                  encoding="utf-8") as fh:
            fh.write("{ not valid json ")
        cmd_init(os.path.join(bad, "input"), os.path.join(bad, "output"),
                 os.path.join(bad, "work"))
        res3 = json.load(open(os.path.join(bad, "output", "result.json"),
                              encoding="utf-8"))
        expect("bad-json: 落盘 input_error", res3.get("status") == "input_error")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("")
    if FAIL:
        print("SELFTEST FAILED: %d 项未通过 -> %s" % (len(FAIL), "; ".join(FAIL)))
        return 1
    print("SELFTEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
