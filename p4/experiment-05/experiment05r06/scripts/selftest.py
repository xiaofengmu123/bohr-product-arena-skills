#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验解释 Skill 自检脚本：只依赖标准库。

在 Skill 根目录执行：python scripts/selftest.py
覆盖：完整管线（skeleton→profile→model→render→validate→finalize）、
空输入降级、坏 JSON 降级、证据完整性校验。
不生成正式案例答案，全部为合成示例。
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
import core_impl  # noqa: E402


def _run(argv):
    """调用 main.main 并返回退出码。"""
    try:
        return main.main(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        return code
    return 0


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def test_full_pipeline(root):
    in_dir = os.path.join(root, "input")
    out_dir = os.path.join(root, "output")
    work_dir = os.path.join(root, "work")
    os.makedirs(in_dir, exist_ok=True)

    _write(os.path.join(in_dir, "case.json"), json.dumps({
        "case_id": "selftest-case",
        "output_contract": {"fields": ["custom_required"], "custom_required": None},
    }))
    _write(os.path.join(in_dir, "task.md"),
           "# 自检任务\n- challenge_id: `challenge-selftest`\n")
    _write(os.path.join(in_dir, "materials.csv"),
           "sample,time_s,force_N\n"
           "a,0,10.0\na,1,9.8\na,2,9.6\na,3,9.4\na,4,9.2\na,5,9.0\n"
           "b,0,10.1\nb,1,9.9\nb,2,99.0\nb,3,9.7\nb,4,9.5\nb,5,9.3\n")

    steps = [
        ["skeleton", "--input", in_dir, "--output", out_dir, "--work", work_dir],
        ["profile", "--input", in_dir, "--output", out_dir, "--work", work_dir],
        ["model", "--input", in_dir, "--output", out_dir, "--work", work_dir],
    ]
    for argv in steps:
        code = _run(argv)
        assert code == 0, "step failed: %s (code=%d)" % (argv, code)

    # 由自检脚本填入 model 解释槽（合成示例，非正式案例）
    model = core_impl.load_json_safe(os.path.join(work_dir, "model.json"))
    assert isinstance(model, dict), "model.json 未生成"
    model["series_models"] = model.get("series_models", [])[:1]
    if model["series_models"]:
        s = model["series_models"][0]
        s["trend"] = {
            "direction": "decreasing", "interval": "0-5 s", "control": None,
            "fit_method": "linear regression", "amplitude": "~1.0 N",
            "units": "N", "claim": "力随采样序单调下降",
            "evidence": [{"locator": "materials.csv#col:force_N", "quote": "force_N"}],
        }
    model["anomaly_model"] = [{
        "id": "A1", "subject": {"dimension": "sample", "locator": "materials.csv#row:9"},
        "observed": 99.0, "expected": 9.9, "deviation": 89.1,
        "method": "z_score", "threshold": 3.0,
        "relation_to_controls": "相对同批其他样本偏离约 10 倍",
        "claim": "样本 b 第 3 个时间点为离群点",
        "evidence": [{"locator": "materials.csv#row:9", "quote": "b,2,99.0"}],
    }]
    model["cause_model"] = [{
        "id": "C1", "hypothesis": "该点可能为记录/传感瞬时错误", "rank": 1,
        "supporting_evidence": [{"locator": "materials.csv#row:9", "quote": "99.0"}],
        "excluding_evidence": [],
        "confound_risk": "单点异常不能证明因果",
        "testability": "回查原始设备日志可判定",
    }]
    model["verification_model"] = [{
        "id": "V1", "action": "回查原始记录行", "target": "materials.csv#row:9",
        "expected_if_supported": "原始记录同为 99.0", "decision_rule": "一致则保留，不一致则纠正",
        "evidence": [{"locator": "materials.csv#row:9", "quote": "b,2,99.0"}],
    }]
    model["followup_model"] = [{
        "id": "F1", "change": "补平行重复", "reason": "单点异常需排除记录错误",
        "expected_outcome": "确认异常是否复现",
        "evidence": [{"locator": "materials.csv#row:9", "quote": "99.0"}],
    }]
    model["experiment_plan_model"] = {
        "experiment_plan": {
            "hypothesis": "样本受力随时间递减", "testable_question": "斜率是否显著为负",
            "variables": ["time_s", "force_N"], "controls": [], "steps": [],
            "parameters": [], "resources": [], "interpretation_criteria": [],
        },
        "evidence_and_risk": {
            "evidence_basis": [{"locator": "materials.csv", "quote": "sample,time_s,force_N"}],
            "risk_and_anomaly_handling": [], "assumptions": [],
        },
        "confirmation_items": {
            "safety": [], "ethics": [], "equipment_availability": [], "expert_confirmation": [],
        },
    }
    core_impl.write_json_atomic(os.path.join(work_dir, "model.json"), model)

    code = _run(["render", "--input", in_dir, "--output", out_dir, "--work", work_dir])
    assert code == 0, "render failed"
    code = _run(["validate", "--input", os.path.join(out_dir, "result.json")])
    assert code == 0, "validate failed"
    code = _run(["finalize", "--input", os.path.join(out_dir, "result.json")])
    assert code == 0, "finalize failed"

    result = core_impl.load_json_safe(os.path.join(out_dir, "result.json"))
    assert result.get("case_id") == "selftest-case", "case_id 不正确"
    assert result.get("status") in core_impl.STATUSES
    assert "custom_required" in result, "output_contract 未回填"
    assert result["interpretation"]["anomalies"], "异常未渲染"
    assert os.path.exists(os.path.join(out_dir, "evidence_map.json"))
    return "full pipeline ok"


def test_empty_input(root):
    in_dir = os.path.join(root, "empty_input")
    os.makedirs(in_dir, exist_ok=True)
    code = _run(["skeleton", "--input", in_dir, "--output", os.path.join(root, "eo"),
                 "--work", os.path.join(root, "ew")])
    assert code == 0, "空输入 skeleton 应退出码 0"
    result = core_impl.load_json_safe(os.path.join(root, "eo", "result.json"))
    assert isinstance(result, dict) and result.get("case_id"), "空输入应写合法骨架"
    return "empty input ok"


def test_bad_json(root):
    in_dir = os.path.join(root, "bad_json")
    os.makedirs(in_dir, exist_ok=True)
    _write(os.path.join(in_dir, "case.json"), "{ not valid json")
    _write(os.path.join(in_dir, "task.md"), "# 任务\n- challenge_id: `challenge-bad`\n")
    code = _run(["skeleton", "--input", in_dir, "--output", os.path.join(root, "bo"),
                 "--work", os.path.join(root, "bw")])
    assert code == 0, "坏 JSON 不应崩溃"
    result = core_impl.load_json_safe(os.path.join(root, "bo", "result.json"))
    assert result.get("case_id") == "challenge-bad", "坏 JSON 应从 task.md 回退取 case_id"
    return "bad json ok"


def main_selftest():
    root = tempfile.mkdtemp(prefix="skill_selftest_")
    try:
        results = [
            test_full_pipeline(root),
            test_empty_input(root),
            test_bad_json(root),
        ]
        for r in results:
            print("PASS: %s" % r)
        print("selftest all ok")
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main_selftest())
