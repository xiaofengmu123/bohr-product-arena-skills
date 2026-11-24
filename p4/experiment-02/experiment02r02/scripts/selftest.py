#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 在临时目录生成合成小案例，验证 Skill 的关键不变量。

重要：本脚本的案例是运行期在 tempfile 里生成的合成输入，仅用于接口自检，
不是正式案例、不是白板、不是预写答案；提交物不包含这些案例。

覆盖：契约回填、七类审查、算术台账可复算、引文逐字校验、版本排序、
确定性（两次运行一致）、坏 JSON、空输入。仅标准库。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import main  # noqa: E402
import review_core as core  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    if ok:
        print("PASS %-40s %s" % (name, detail))
    else:
        print("FAIL %-40s %s" % (name, detail))
        FAILS.append(name)


def write_case(root, data):
    os.makedirs(os.path.join(root, "input"), exist_ok=True)
    with open(os.path.join(root, "input", "case.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(root, "input", "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 合成自检任务\n\n只用于接口自检，不冒充正式案例。\n")


def full_case():
    return {
        "case_id": "synth-001",
        "challenge_id": "challenge-2e2c5820",
        "title": "合成小案例（接口自检用）",
        "research_goal": "验证 X 处理相对对照能否提升 Y 至少 20%",
        "budget": 50000,
        "output_contract": {
            "verdict_text": "string",
            "recommended_plan": "string",
            "evidence_map": {"type": "array"},
        },
        "parameter_domains": [
            {"name": "temperature_C", "min": 20, "max": 60, "step": 5, "unit": "C"},
            {"name": "ph", "allowed": [6.5, 7.0, 7.4], "unit": "pH"},
        ],
        "contracts": {
            "equipment": [
                {"id": "EQ-1", "name": "实时荧光定量PCR仪 AB7500", "status": "需采购",
                 "unit_price": 450000, "currency": "CNY", "lead_time_days": 45},
                {"id": "EQ-2", "name": "恒温摇床", "status": "已具备",
                 "rate_per_hour": 120, "booking_fee": 500, "currency": "CNY"},
            ],
            "consumables": [
                {"id": "KIT-1", "name": "RNA提取试剂盒", "status": "已具备",
                 "unit_price": 4200, "currency": "CNY", "unit": "盒", "stock": 3},
            ],
        },
        "plans": [
            {
                "version": "v1",
                "goal": "X 处理使 Y 提高 20%",
                "budget": 50000,
                "timeline_days": 30,
                "hypothesis": "X 处理使 Y 相对对照显著升高",
                "independent_variables": ["X浓度"],
                "dependent_variables": ["Y"],
                "success_criteria": "Y 组间差异 p<0.05 且效应≥20%",
                "controls": ["空白对照", "溶剂对照"],
                "replicates": 3,
                "parameters": [
                    {"name": "temperature_C", "value": 37, "unit": "C"},
                    {"name": "ph", "value": 7.0},
                ],
                "steps": [
                    {"name": "样品制备", "duration_h": 2,
                     "resources": [{"name": "RNA提取试剂盒", "qty": 2}]},
                    {"name": "qPCR上机", "duration_h": 4,
                     "resources": [{"name": "实时荧光定量PCR仪 AB7500"}]},
                    {"name": "摇床培养", "duration_h": 6,
                     "resources": [{"name": "恒温摇床", "hours": 6}]},
                ],
                "samples": {"n": 36, "n_per_group": 12, "groups": 3,
                            "effect_size": 0.2, "cv": 0.15, "alpha": 0.05, "power": 0.8,
                            "attrition": 0.1},
                "risks": [
                    {"risk": "设备故障", "trigger": "qPCR异常升温", "response": "改用备用机", "owner": "平台"},
                    {"risk": "材料批次差异", "trigger": "试剂批号变更", "response": "加做质控", "owner": "操作员"},
                    {"risk": "样本不足", "trigger": "RNA得率低", "response": "补采样", "owner": "PI"},
                    {"risk": "参数越界", "trigger": "温度漂移", "response": "停机校准", "owner": "操作员"},
                ],
                "ethics": "不涉及动物与人体样本",
            },
            {
                "version": "v2",
                "goal": "X 处理使 Y 提高 20%",
                "budget": 80000,
                "timeline_days": 60,
                "hypothesis": "X 处理使 Y 显著升高",
                "independent_variables": ["X浓度"],
                "dependent_variables": ["Y"],
                "parameters": [
                    {"name": "temperature_C", "value": 68, "unit": "C"},
                    {"name": "ph", "value": 7.4},
                ],
                "steps": [
                    {"name": "样品制备", "duration_h": 2,
                     "resources": [{"name": "RNA提取试剂盒", "qty": 4}]},
                    {"name": "qPCR上机", "duration_h": 4,
                     "resources": [{"name": "实时荧光定量PCR仪 AB7500"}]},
                ],
                "samples": {"n": 60, "n_per_group": 20, "groups": 3,
                            "effect_size": 0.2, "cv": 0.15, "alpha": 0.05, "power": 0.8},
                "risks": [],
            },
            {
                "version": "v3",
                "goal": "X 处理使 Y 提高 20%",
                "budget": 60000,
                "timeline_days": 45,
                "hypothesis": "X 处理使 Y 显著升高",
                "independent_variables": ["X浓度"],
                "dependent_variables": ["Y"],
                "controls": ["空白对照"],
                "parameters": [
                    {"name": "temperature_C", "value": 40, "unit": "C"},
                    {"name": "ph", "value": 6.5},
                ],
                "steps": [
                    {"name": "样品制备", "duration_h": 1,
                     "resources": [{"name": "RNA提取试剂盒", "qty": 3}]},
                    {"name": "qPCR上机", "duration_h": 3,
                     "resources": [{"name": "实时荧光定量PCR仪 AB7500"}]},
                    {"name": "摇床培养", "duration_h": 5,
                     "resources": [{"name": "恒温摇床", "hours": 5}]},
                ],
                "samples": {"n": 48, "n_per_group": 16, "groups": 3,
                            "effect_size": 0.2, "cv": 0.15, "alpha": 0.05, "power": 0.8},
                "risks": [
                    {"risk": "参数越界", "trigger": "温度漂移", "response": "停机校准", "owner": "操作员"},
                ],
                "ethics": "涉及小鼠动物实验",
            },
        ],
    }


def run_main_stage(stage, root, out, work):
    return getattr(main, stage)(os.path.join(root, "input"), out, work)


def test_full_case():
    root = tempfile.mkdtemp(prefix="skill-selftest-full-")
    write_case(root, full_case())
    out1 = os.path.join(root, "out1")
    out2 = os.path.join(root, "out2")
    work = os.path.join(root, "work")
    res = main.finalize_stage(os.path.join(root, "input"), out1, work)

    check("full_status_ok_or_partial", res["status"] in ("ok", "partial"), res["status"])
    check("all_dimensions_nonempty", all(res["review"].get(d) for d in core.DIMENSIONS),
          str({d: len(res["review"].get(d, [])) for d in core.DIMENSIONS}))
    c = res.get("counts", {})
    check("ledger_computed_ge3", c.get("computed_ledger", 0) >= 3, f"computed={c.get('computed_ledger')}")
    check("contract_echo_complete", all(k in res for k in ("verdict_text", "recommended_plan", "evidence_map")),
          ",".join(sorted(res.get("contract_fields", {}))))
    check("ranking_3_plans", len(res["plan_comparison"]["ranking"]) == 3,
          str(res["plan_comparison"]["ranking"]))
    check("recommended_present", res["plan_comparison"]["recommended"] is not None,
          str(res["plan_comparison"]["recommended"]))
    # 台账复算
    bad = [e["id"] for e in res["arithmetic_ledger"] if e.get("status") == "computed" and not e.get("recompute_ok")]
    check("ledger_recompute_ok", not bad, ",".join(bad))
    # 引文逐字校验
    badq = [e["locator"] for e in res["evidence_index"] if e.get("quote") and not e.get("quote_verified")]
    check("quotes_verbatim", not badq, ",".join(str(x) for x in badq[:4]))
    # 关键发现：v2 参数 68 越界、v1 有 control 而 v2 无（回归）
    items = [it for lst in res["review"].values() for it in lst]
    blocked_t = [it for it in items if it.get("dimension") == "parameter_ranges" and it.get("verdict") == "blocked"]
    check("domain_violation_detected", len(blocked_t) >= 1, f"n={len(blocked_t)}")
    regress = [it for it in items if it.get("id", "").startswith("CTRL-regress")]
    check("control_regression_detected", len(regress) >= 1, f"n={len(regress)}")
    eth = [it for it in items if it.get("dimension") == "ethics_safety"]
    check("ethics_items_present", len(eth) >= 1, f"n={len(eth)}")
    # 确定性
    main.finalize_stage(os.path.join(root, "input"), out2, work)
    r1 = json.load(open(os.path.join(out1, "result.json"), encoding="utf-8"))
    r2 = json.load(open(os.path.join(out2, "result.json"), encoding="utf-8"))
    for r in (r1, r2):
        r.pop("artifacts", None)
        r.pop("runtime", None)
    check("deterministic", r1 == r2, "stripped runtime/artifacts")
    shutil.rmtree(root, ignore_errors=True)


def test_bad_json():
    root = tempfile.mkdtemp(prefix="skill-selftest-badjson-")
    os.makedirs(os.path.join(root, "input"))
    with open(os.path.join(root, "input", "case.json"), "w", encoding="utf-8") as fh:
        fh.write('{"plans": [{"version": "v1",')
    res = main.skeleton(os.path.join(root, "input"), os.path.join(root, "out"), os.path.join(root, "work"))
    check("badjson_status", res["status"] in ("input_error", "partial"), res["status"])
    check("badjson_errors_recorded", any("bad_json" in str(e) for e in res.get("errors", [])),
          str(res.get("errors")))
    check("badjson_valid_result", isinstance(res.get("review"), dict), "review present")
    shutil.rmtree(root, ignore_errors=True)


def test_empty_dir():
    root = tempfile.mkdtemp(prefix="skill-selftest-empty-")
    res = main.skeleton(os.path.join(root, "input"), os.path.join(root, "out"), os.path.join(root, "work"))
    check("empty_status_insufficient", res["status"] == "insufficient_evidence", res["status"])
    check("empty_result_on_disk", os.path.exists(os.path.join(root, "out", "result.json")), "result.json")
    shutil.rmtree(root, ignore_errors=True)


def test_verify_pass():
    root = tempfile.mkdtemp(prefix="skill-selftest-verify-")
    write_case(root, full_case())
    res = main.finalize_stage(os.path.join(root, "input"), os.path.join(root, "out"), os.path.join(root, "work"))
    checks = main.verify(res)
    hard = [c for c in checks if c.get("hard")]
    check("verify_hard_pass", all(c["pass"] for c in hard), ",".join(c["name"] for c in hard if not c["pass"]))
    shutil.rmtree(root, ignore_errors=True)


def main_selftest():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    test_full_case()
    test_bad_json()
    test_empty_dir()
    test_verify_pass()
    print("\n%d failed" % len(FAILS))
    if "--strict" in sys.argv and FAILS:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main_selftest())
