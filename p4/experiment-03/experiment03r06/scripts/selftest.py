#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end self-test for the Skill scripts (standard library only).

Runs in a fresh temporary directory so it never touches /input or /app/output.
The synthetic inputs below are clearly labeled SELF-TEST data, not formal answers.

Checks
  1. happy path: init -> fill intermediates -> doe -> finalize -> validate (exit 0)
  2. empty input directory: init + finalize still succeed (partial skeleton)
  3. blocking validation: out-of-domain recommended value is rejected (exit 1)

Exit code 0 = all checks passed; non-zero = failure.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join("scripts", "main.py")
VALIDATE = os.path.join("scripts", "validate_plan.py")


def run(args, cwd=SKILL_ROOT):
    proc = subprocess.run([sys.executable] + args, cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    return proc


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def fill_work(work):
    """Fill intermediates and plan with minimal, complete SELF-TEST content."""
    case_id = "selftest-case-000"
    constraint = {
        "schema_version": "1.0", "case_id": case_id,
        "feasibility": {"status": "feasible", "violations": []},
        "parameters": [{
            "id": "T", "name": "温度", "unit": "°C",
            "recommended_value": 80, "domain": {"min": 60, "max": 120, "step": 5},
            "hard_bounds": [{"kind": "equipment", "min": 20, "max": 150,
                             "locator": "case.json#equipment.max_temp",
                             "quote": "设备量程上限 150 °C"}],
            "constraints": [{"type": "leq", "rhs": 150,
                             "locator": "case.json#equipment.max_temp",
                             "quote": "设备量程上限 150 °C"}],
            "status": "feasible", "conflict_note": "",
        }],
        "constraints": [],
    }
    response = {
        "schema_version": "1.0", "case_id": case_id,
        "metrics": [{
            "id": "yield", "name": "转化率", "unit": "%", "direction": "maximize",
            "measurement_method": "HPLC 定量", "target": 85, "acceptable_min": 70,
            "uncertainty": "±5 %",
            "prediction": {"method": "经验规则", "confidence": "low",
                           "basis": [{"locator": "materials/history.csv#r1",
                                      "quote": "80 °C 时转化率约 78 %"}]},
        }],
        "relations": [{"from": "T", "to": "yield", "direction": "positive",
                       "note": "温度升高转化率上升", "basis": []}],
        "model_note": "",
    }
    search = {
        "schema_version": "1.0", "case_id": case_id, "method": "grid",
        "initial_points": [{"T": 80}],
        "per_round": "每轮补中心点与边界中点", "stop_criteria": ["预算耗尽"],
        "candidate_count": 4, "seed": 0, "reproducible": True, "candidates": [],
    }
    evidence = {
        "schema_version": "1.0", "case_id": case_id,
        "sources": [{"id": "s1", "kind": "constraint",
                     "locator": "case.json#equipment.max_temp",
                     "quote": "设备量程上限 150 °C", "claims": ["温度上限"],
                     "verified": True, "source_conflict": False}],
    }
    tradeoff = {
        "schema_version": "1.0", "case_id": case_id,
        "budget": {"currency": "CNY", "total": 400, "time_hours": 4},
        "cost_items": [{"name": "机时", "unit": "h", "amount": 4, "unit_cost": 100,
                        "total": 400, "locator": "case.json#budget.machine_rate",
                        "quote": "机时单价 100 CNY/h"}],
        "total": "400 CNY", "time_hours": 4,
        "tradeoff_note": "预算 400 内优先保证 4 个候选点",
        "acceptance_criteria": "转化率 ≥70 %",
    }
    plan = {
        "schema_version": "1.0", "case_id": case_id, "status": "ok",
        "title": "SELF-TEST 合成方案", "research_question": "温度如何影响转化率？",
        "objective": "在可行域内找到高转化率的可执行温度点",
        "assumptions": ["设备量程与材料耐温以 case.json 为准"],
        "variables": [{"name": "温度", "type": "independent", "unit": "°C"}],
        "parameters": [], "controls": [{"name": "反应时间", "value": "2 h"}],
        "procedures": [{"step": 1, "action": "按候选点升温并采样"}],
        "resources": [{"name": "反应釜", "spec": "量程 20–150 °C"}],
        "response_metrics": [], "interpretation_criteria": ["转化率 ≥70 % 可接受"],
        "search_strategy": {"method": "", "initial_points": [], "per_round": "",
                            "stop_criteria": [], "candidate_count": 4, "seed": 0,
                            "reproducible": True, "candidates": []},
        "cost_budget": {"items": [], "total": "", "time_hours": None,
                        "tradeoff_note": "", "acceptance_criteria": ""},
        "basis": ["依据 case.json 的设备量程"],
        "risks": [{"name": "无可行解", "mitigation": "转人工"}],
        "exception_handling": [{"case": "设备报错", "action": "重试一次后转人工"}],
        "confirmations": ["安全、伦理、设备可用性、专家确认"],
        "sources": [],
        "uncertainty": {"unverified_values": [], "missing_data": [],
                        "assumptions": [], "prediction_uncertainty": ["±5 %"]},
        "limitations": ["SELF-TEST 合成数据，非正式案例"],
        "pending_human": ["专家确认温度上限"],
        "change_log": [],
    }
    write_json(os.path.join(work, "constraint_model.json"), constraint)
    write_json(os.path.join(work, "response_model.json"), response)
    write_json(os.path.join(work, "search_model.json"), search)
    write_json(os.path.join(work, "evidence_map.json"), evidence)
    write_json(os.path.join(work, "tradeoff_model.json"), tradeoff)
    write_json(os.path.join(work, "plan.json"), plan)
    return case_id


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (("  -> " + detail) if detail else ""))
    return ok


def main():
    base = tempfile.mkdtemp(prefix="skill-selftest-")
    ok_all = True
    try:
        # --- 1. happy path ---
        d = os.path.join(base, "happy")
        inp, out, work = (os.path.join(d, "input"), os.path.join(d, "output"),
                          os.path.join(d, "work"))
        os.makedirs(inp)
        write_json(os.path.join(inp, "case.json"), {
            "case_id": "selftest-case-000",
            "equipment": {"max_temp": 150, "min_temp": 20},
            "budget": {"machine_rate": 100}})
        write_json(os.path.join(inp, "task.md"), "# SELF-TEST task\n合成任务，仅用于自检。\n")
        p = run([MAIN, "init", "--input", inp, "--output", out, "--work", work])
        ok_all &= check("init happy path", p.returncode == 0, p.stdout.strip())
        fill_work(work)
        p = run([MAIN, "doe", "--work", work, "--seed", "0"])
        ok_all &= check("doe happy path", p.returncode == 0, p.stdout.strip())
        p = run([MAIN, "finalize", "--input", inp, "--output", out, "--work", work])
        ok_all &= check("finalize happy path", p.returncode == 0, p.stdout.strip())
        p = run([VALIDATE, os.path.join(work, "plan.json")])
        ok_all &= check("validate plan.json exit 0", p.returncode == 0,
                        p.stdout.strip().splitlines()[-1] if p.stdout.strip() else "")
        p = run([VALIDATE, os.path.join(out, "result.json")])
        ok_all &= check("validate result.json exit 0", p.returncode == 0,
                        p.stdout.strip().splitlines()[-1] if p.stdout.strip() else "")
        result = read_json(os.path.join(out, "result.json"))
        ok_all &= check("result artifacts exist",
                        os.path.exists(out + "/plan.md") and os.path.exists(out + "/plan.json"))
        ok_all &= check("result intermediates recorded",
                        bool(result.get("intermediate_representations")))

        # --- 2. empty input directory ---
        d2 = os.path.join(base, "empty")
        inp2, out2, work2 = (os.path.join(d2, "input"), os.path.join(d2, "output"),
                             os.path.join(d2, "work"))
        os.makedirs(inp2)
        p = run([MAIN, "init", "--input", inp2, "--output", out2, "--work", work2])
        ok_all &= check("init empty input", p.returncode == 0)
        p = run([MAIN, "finalize", "--input", inp2, "--output", out2, "--work", work2])
        ok_all &= check("finalize empty input", p.returncode == 0)
        r2 = read_json(os.path.join(out2, "result.json"))
        ok_all &= check("empty input partial skeleton", r2.get("status") == "partial")

        # --- 3. out-of-domain rejection ---
        d3 = os.path.join(base, "bad")
        work3 = os.path.join(d3, "work")
        os.makedirs(work3)
        bad_plan = {
            "case_id": "bad", "status": "ok", "title": "",
            "research_question": "", "objective": "", "assumptions": [],
            "variables": [], "controls": [], "procedures": [], "resources": [],
            "response_metrics": [], "interpretation_criteria": [],
            "search_strategy": {"method": "grid", "initial_points": [],
                                "per_round": "", "stop_criteria": [],
                                "candidate_count": 8, "seed": 0,
                                "reproducible": True, "candidates": []},
            "cost_budget": {"items": [], "total": "", "time_hours": None,
                            "tradeoff_note": "", "acceptance_criteria": ""},
            "basis": [], "risks": [], "exception_handling": [], "confirmations": [],
            "sources": [],
            "uncertainty": {"unverified_values": [], "missing_data": [],
                            "assumptions": [], "prediction_uncertainty": []},
            "limitations": [], "pending_human": [], "change_log": [],
            "parameters": [{"id": "T", "name": "温度", "unit": "°C",
                            "recommended_value": 200,
                            "domain": {"min": 60, "max": 120, "step": 5},
                            "hard_bounds": [{"kind": "equipment", "min": 20,
                                             "max": 150, "locator": "c", "quote": "q"}],
                            "source": {"locator": "c", "quote": "q"},
                            "status": "feasible", "conflict_note": ""}],
        }
        write_json(os.path.join(work3, "plan.json"), bad_plan)
        p = run([VALIDATE, os.path.join(work3, "plan.json")])
        ok_all &= check("out-of-domain rejected (exit 1)", p.returncode == 1,
                        p.stdout.strip().splitlines()[-1] if p.stdout.strip() else "")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print("selftest %s" % ("ok" if ok_all else "FAILED"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
