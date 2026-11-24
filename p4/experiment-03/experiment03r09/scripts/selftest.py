#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill 自检脚本（仅标准库）。

覆盖三个场景：空输入、坏 JSON、正常输入。每个场景通过 subprocess 调用
scripts/main.py 走完整链路（init -> validate -> doe -> finalize -> check），
验证骨架落盘、错误记录、候选点复现与产物生成。退出码 0=全部通过，1=存在失败。
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN_PY = str(ROOT / "scripts" / "main.py")
STATUS_ENUM = ("ok", "partial", "insufficient_evidence", "input_error")


def _run(argv):
    return subprocess.run([sys.executable, MAIN_PY] + argv,
                          capture_output=True, text=True, cwd=str(ROOT))


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def scenario_empty():
    with tempfile.TemporaryDirectory() as tmp:
        inp, out, work = (os.path.join(tmp, d)
                          for d in ("input", "output", "work"))
        os.makedirs(inp)
        r = _run(["init", "--input", inp, "--output", out, "--work", work])
        assert r.returncode == 0, r.stderr
        obj = _load(os.path.join(out, "result.json"))
        assert obj["status"] in STATUS_ENUM, obj["status"]


def scenario_bad_json():
    with tempfile.TemporaryDirectory() as tmp:
        inp, out, work = (os.path.join(tmp, d)
                          for d in ("input", "output", "work"))
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{bad json")
        r = _run(["init", "--input", inp, "--output", out, "--work", work])
        assert r.returncode == 0, r.stderr
        digest = _load(os.path.join(work, "input_digest.json"))
        assert digest.get("case_json_error"), "应记录坏 JSON 错误"
        obj = _load(os.path.join(out, "result.json"))
        assert obj["errors"], "result.json 应记录 case.json 错误"


def _demo_plan():
    return {
        "case_id": "case-demo-001",
        "status": "partial",
        "title": "演示方案",
        "research_question": "反应温度如何影响转化率？",
        "objective": "在设备量程内优化反应温度",
        "assumptions": ["假设历史数据可用"],
        "variables": [{"name": "温度", "unit": "°C", "type": "independent"}],
        "parameters": [{
            "name": "反应温度", "unit": "°C", "suggested_value": 80,
            "feasible_range": {"min": 60, "max": 90}, "step": 5,
            "hard_bounds": {"min": 0, "max": 120, "label": "设备量程"},
            "constraint_source": {"locator": "task.md#device",
                                  "quote": "设备量程 0–120°C"}}],
        "controls": ["对照：未升温基线"],
        "procedures": ["升温→保温→采样"],
        "resources": [{"name": "反应釜", "range": "0–120°C"}],
        "response_metrics": [{
            "name": "转化率", "unit": "%", "measurement_method": "GC",
            "direction": "maximize", "target": "≥85",
            "acceptable_lower": 80, "prediction": "经验规则",
            "uncertainty_note": "±5 个百分点"}],
        "interpretation_criteria": ["转化率≥85 判为可行"],
        "search_strategy": {"method": "网格+序贯", "initial_points": [80],
                            "per_round": "±5°C 补点",
                            "stop_criteria": ["相邻两轮转化率差<2"],
                            "candidate_count": 6, "seed": 0},
        "cost_budget": {"items": [{"name": "机时", "cost": "6h"}],
                        "total": "6h", "time_hours": 6,
                        "tradeoff_note": "温度每降 10°C 接受转化率降 5 个百分点"},
        "basis": ["历史数据拟合"],
        "risks": ["越界风险"],
        "exception_handling": ["越界则降目标温度"],
        "confirmations": ["安全阈值需专家确认"],
        "sources": [{"locator": "task.md#device", "quote": "设备量程 0–120°C"}],
        "uncertainty": {"unverified_values": ["转化率预测"], "missing_data": [],
                        "conflicts": [], "notes": []},
        "limitations": ["演示数据"],
        "pending_human": ["安全阈值确认"],
        "change_log": [{"time": "2026-01-01T00:00:00Z", "step": "init",
                        "note": "demo"}],
        "errors": [],
        "degradations": [],
    }


def scenario_normal():
    with tempfile.TemporaryDirectory() as tmp:
        inp, out, work = (os.path.join(tmp, d)
                          for d in ("input", "output", "work"))
        os.makedirs(inp)
        os.makedirs(work)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "case-demo-001", "task": "demo"}, fh)
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# demo task\n")
        with open(os.path.join(work, "plan.json"), "w", encoding="utf-8") as fh:
            json.dump(_demo_plan(), fh, ensure_ascii=False)

        r = _run(["validate", "--work", work, "--report",
                  os.path.join(work, "validation_report.json")])
        assert r.returncode in (0, 2), r.stdout + r.stderr

        r = _run(["doe", "--work", work, "--seed", "0"])
        assert r.returncode == 0, r.stderr
        cand = _load(os.path.join(work, "candidates.json"))
        assert cand["candidate_count"] > 0

        r = _run(["finalize", "--input", inp, "--output", out, "--work", work])
        assert r.returncode == 0, r.stderr
        assert os.path.isfile(os.path.join(out, "plan.md"))
        assert os.path.isfile(os.path.join(out, "plan.json"))
        result = _load(os.path.join(out, "result.json"))
        assert result["case_id"] == "case-demo-001"

        r = _run(["check", os.path.join(out, "result.json"), "--output-dir", out])
        assert r.returncode in (0, 2), r.stdout + r.stderr


def run_selftest():
    results = []
    for name, fn in (("empty", scenario_empty), ("bad_json", scenario_bad_json),
                     ("normal", scenario_normal)):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as exc:  # noqa: BLE001
            results.append((name, "FAIL", str(exc)))
    for name, verdict, detail in results:
        print("selftest %-9s %s %s" % (name, verdict, detail))
    return 0 if all(v == "PASS" for _, v, _ in results) else 1


if __name__ == "__main__":
    sys.exit(run_selftest())
