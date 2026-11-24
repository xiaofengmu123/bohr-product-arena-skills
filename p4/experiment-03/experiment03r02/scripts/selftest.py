#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验参数优化建议 Skill — 脚本自检（仅标准库）。

在临时目录中合成最小输入（明确标注为自检合成 fixture，非正式案例答案），
验证 main.py / validate_plan.py 的契约行为：
  init 早落盘、evaluate 成本算术、doe 确定性、finalize 产物、check 契约、
  空输入降级、坏 JSON 降级。

用法：python scripts/selftest.py [--scripts DIR]
退出码：0=全部通过；1=存在失败项。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

PY = sys.executable

CHECKS = []


def record(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    print("%s  %s%s" % (mark, name, ("  -- " + detail) if detail else ""))


def run(cmd, cwd):
    """运行子命令并返回 (returncode, stdout)。stdout 解码容错。"""
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)
        return proc.returncode, proc.stdout
    except Exception as exc:
        return -999, "EXCEPTION: %s" % exc


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def make_minimal_plan(case_id="selftest-case"):
    return {
        "case_id": case_id,
        "title": "自检合成方案（非正式案例答案）",
        "research_question": "验证脚本契约：参数、指标、搜索、成本字段可被解析。",
        "objective": "验证 feasible 判定与成本算术。",
        "assumptions": ["合成夹具仅用于自检"],
        "variables": [],
        "parameters": [
            {
                "id": "T", "name": "反应温度", "unit": "°C",
                "value": 120, "min": 80, "max": 160, "step": 5,
                "device_limit": {"value": 200, "source": "self-test"},
                "material_limit": {"value": 150, "source": "self-test"},
                "safety_limit": {"value": 180, "source": "self-test"},
                "constraint_source": {"locator": "selftest", "quote": "合成"},
            },
            {
                "id": "ratio", "name": "摩尔比", "unit": "1",
                "value": 1.2, "min": 0.5, "max": 2.0, "step": 0.1,
            },
        ],
        "controls": [],
        "procedures": [],
        "resources": [],
        "response_metrics": [
            {
                "id": "yield", "name": "收率", "unit": "%", "primary": True,
                "measurement_method": "HPLC", "direction": "max",
                "target": 90, "acceptable_lower": 80,
                "prediction_method": "empirical_rule",
            },
        ],
        "interpretation_criteria": [],
        "search_strategy": {
            "method": "grid", "initial_points": ["T=120, ratio=1.2"],
            "per_round": "逐轮网格细化", "stop_criteria": ["两次迭代响应差<1%"],
            "candidate_count": 5, "seed": 0,
        },
        "cost_budget": {
            "currency": "CNY", "items": [
                {"name": "机时", "quantity": 4, "unit": "h", "unit_price": 80, "subtotal": 320},
            ],
            "total": 320, "budget_cap": 500, "time_hours": 4,
            "time_budget_hours": 8, "tradeoff_note": "自检",
        },
        "basis": [], "risks": [], "exception_handling": [], "confirmations": [],
        "candidates": [
            {
                "version": "v1", "name": "方案一",
                "source": {"locator": "selftest", "quote": "合成"},
                "parameters": {"T": 120, "ratio": 1.2},
                "cost_items": [{"name": "机时", "quantity": 4, "unit": "h", "unit_price": 80}],
                "predicted_metrics": {"yield": {"value": 88, "uncertainty": "±3"}},
            },
            {
                "version": "v2", "name": "方案二（越界）",
                "source": {"locator": "selftest", "quote": "合成"},
                "parameters": {"T": 170, "ratio": 1.2},
                "cost_items": [{"name": "机时", "quantity": 4, "unit": "h", "unit_price": 80}],
                "predicted_metrics": {"yield": {"value": 92, "uncertainty": "±3"}},
            },
        ],
        "candidate_evaluation": [],
        "sources": [{"id": "s1", "locator": "selftest", "quote": "合成", "claim": "自检"}],
        "uncertainty": {"unverified_values": [], "missing_data": [], "assumptions": [],
                        "prediction_uncertainty": []},
        "limitations": [], "pending_human": [], "change_log": [],
        "errors": [], "degradations": [],
    }


def main():
    ap = argparse.ArgumentParser(description="Skill 脚本自检")
    ap.add_argument("--scripts", default=None, help="scripts 目录（缺省为脚本自身目录）")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    scripts_dir = os.path.abspath(args.scripts or here)
    main_py = os.path.join(scripts_dir, "main.py")
    validate_py = os.path.join(scripts_dir, "validate_plan.py")
    if not (os.path.exists(main_py) and os.path.exists(validate_py)):
        print("FAIL: scripts 目录缺 main.py / validate_plan.py: %s" % scripts_dir)
        return 1

    tmp = tempfile.mkdtemp(prefix="weekly-skill-selftest-")
    in_dir = os.path.join(tmp, "input")
    out_dir = os.path.join(tmp, "output")
    work_dir = os.path.join(tmp, "work")
    os.makedirs(in_dir)
    try:
        # 1. help
        rc, out = run([PY, "-B", main_py, "--help"], scripts_dir)
        record("main.py --help 退出码 0", rc == 0)

        # 2. init 早落盘
        write_json(os.path.join(in_dir, "case.json"), {"case_id": "selftest-case"})
        write_text(os.path.join(in_dir, "task.md"), "# 自检任务\n合成输入。\n")
        rc, out = run([PY, "-B", main_py, "init",
                       "--input", in_dir, "--output", out_dir, "--work", work_dir], scripts_dir)
        skeleton = None
        if os.path.exists(os.path.join(out_dir, "result.json")):
            with open(os.path.join(out_dir, "result.json"), encoding="utf-8") as fh:
                skeleton = json.load(fh)
        ok = (rc == 0 and skeleton is not None
              and skeleton.get("case_id") == "selftest-case"
              and skeleton.get("status") == "partial"
              and isinstance(skeleton.get("change_log"), list)
              and len(skeleton["change_log"]) >= 1)
        record("init 早落盘 partial 骨架", ok, "status=%s" % (skeleton or {}).get("status"))

        # 3. 写入最小 plan 并 validate
        plan = make_minimal_plan()
        write_json(os.path.join(work_dir, "plan.json"), plan)
        rc, out = run([PY, "-B", validate_py, os.path.join(work_dir, "plan.json"),
                       "--report", os.path.join(work_dir, "plan.validation_report.json")],
                      scripts_dir)
        record("validate_plan 通过（退出码 0）", rc == 0, "rc=%d" % rc)

        # 4. evaluate 成本算术与可行性判定
        rc, out = run([PY, "-B", main_py, "evaluate",
                       "--input", in_dir, "--output", out_dir, "--work", work_dir,
                       "--seed", "0"], scripts_dir)
        plan_after = None
        with open(os.path.join(work_dir, "plan.json"), encoding="utf-8") as fh:
            plan_after = json.load(fh)
        ev = plan_after.get("candidate_evaluation") or []
        v1 = next((e for e in ev if e.get("version") == "v1"), None)
        v2 = next((e for e in ev if e.get("version") == "v2"), None)
        ok = (rc == 0 and len(ev) == 2
              and v1 is not None and v1.get("feasible") is True
              and v2 is not None and v2.get("feasible") is False
              and v2.get("violations")
              and abs((v1.get("total_cost") or 0) - 320) < 1e-6)
        record("evaluate 可行性 + 成本算术", ok,
               "v1=%s v2=%s cost=%s" % (v1.get("feasible") if v1 else None,
                                        v2.get("feasible") if v2 else None,
                                        v1.get("total_cost") if v1 else None))

        # 5. rank Pareto 排序
        rc, out = run([PY, "-B", main_py, "rank",
                       "--input", in_dir, "--output", out_dir, "--work", work_dir], scripts_dir)
        with open(os.path.join(work_dir, "plan.json"), encoding="utf-8") as fh:
            plan_after = json.load(fh)
        ev = plan_after.get("candidate_evaluation") or []
        v1 = next((e for e in ev if e.get("version") == "v1"), None)
        ok = (rc == 0 and v1 is not None and v1.get("pareto_rank") == 1
              and v1.get("selected") is True)
        record("rank Pareto 排序选择可行首项", ok)

        # 6. doe 确定性（跑两次结果一致）
        rc1, _ = run([PY, "-B", main_py, "doe",
                      "--input", in_dir, "--output", out_dir, "--work", work_dir,
                      "--seed", "0"], scripts_dir)
        with open(os.path.join(work_dir, "candidates.json"), encoding="utf-8") as fh:
            c1 = json.load(fh)
        rc2, _ = run([PY, "-B", main_py, "doe",
                      "--input", in_dir, "--output", out_dir, "--work", work_dir,
                      "--seed", "0"], scripts_dir)
        with open(os.path.join(work_dir, "candidates.json"), encoding="utf-8") as fh:
            c2 = json.load(fh)
        record("doe 确定性（同种子结果一致）", rc1 == 0 and rc2 == 0 and c1 == c2,
               "count=%d" % len(c1))

        # 7. finalize 产物齐全
        rc, out = run([PY, "-B", main_py, "finalize",
                       "--input", in_dir, "--output", out_dir, "--work", work_dir,
                       "--status", "partial"], scripts_dir)
        plan_md = os.path.join(out_dir, "plan.md")
        plan_json = os.path.join(out_dir, "plan.json")
        result_json = os.path.join(out_dir, "result.json")
        ok = (rc == 0 and os.path.exists(plan_md) and os.path.exists(plan_json)
              and os.path.exists(result_json))
        record("finalize 生成 result.json + plan.md + plan.json", ok)

        # 8. check 契约
        rc, out = run([PY, "-B", main_py, "check",
                       "--input", in_dir, "--output", out_dir, "--work", work_dir], scripts_dir)
        record("main.py check 退出码 0", rc == 0, "rc=%d" % rc)

        # 9. 空输入降级
        empty_in = os.path.join(tmp, "empty")
        os.makedirs(empty_in)
        empty_out = os.path.join(tmp, "empty_out")
        empty_work = os.path.join(tmp, "empty_work")
        rc, out = run([PY, "-B", main_py, "init",
                       "--input", empty_in, "--output", empty_out, "--work", empty_work],
                      scripts_dir)
        skeleton = None
        if os.path.exists(os.path.join(empty_out, "result.json")):
            with open(os.path.join(empty_out, "result.json"), encoding="utf-8") as fh:
                skeleton = json.load(fh)
        record("空输入 init 不崩溃并落骨架", rc == 0 and skeleton is not None
               and "status" in skeleton, "status=%s" % (skeleton or {}).get("status"))

        # 10. 坏 JSON 降级
        bad_in = os.path.join(tmp, "bad")
        os.makedirs(bad_in)
        write_text(os.path.join(bad_in, "case.json"), "{ not valid json")
        bad_out = os.path.join(tmp, "bad_out")
        bad_work = os.path.join(tmp, "bad_work")
        rc, out = run([PY, "-B", main_py, "init",
                       "--input", bad_in, "--output", bad_out, "--work", bad_work], scripts_dir)
        digest = None
        if os.path.exists(os.path.join(bad_work, "input_digest.json")):
            with open(os.path.join(bad_work, "input_digest.json"), encoding="utf-8") as fh:
                digest = json.load(fh)
        ok = (rc == 0 and digest is not None and digest.get("case_json_error") is not None)
        record("坏 JSON 捕获到错误且不崩溃", ok,
               "err=%s" % (digest or {}).get("case_json_error"))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [c for c in CHECKS if not c[1]]
    print("-" * 50)
    print("SELFTEST: %d passed, %d failed" % (len(CHECKS) - len(failed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
