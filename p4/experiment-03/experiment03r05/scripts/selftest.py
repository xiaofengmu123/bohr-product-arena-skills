#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合成输入自检（不含任何正式答案，只验证脚本契约与降级路径）。

覆盖 4 个场景：
  happy         正常流程：init -> plan 校验 -> finalize -> result 校验 -> doe
  empty         空输入目录：init 不崩溃且落盘合法 result.json
  bad_json      坏 case.json：init 捕获错误并继续
  conflict      建议值越界：validate.py 返回阻断退出码 1

退出码 0 = 全部通过；1 = 存在失败项。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(SKILL_ROOT, "scripts", "main.py")
VALIDATE = os.path.join(SKILL_ROOT, "scripts", "validate.py")

RESULTS = []


def run(args):
    return subprocess.run([sys.executable, "-B"] + args, cwd=SKILL_ROOT,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("[PASS] " if ok else "[FAIL] ") + name + ((" :: " + str(detail)[:160]) if detail else ""))


def minimal_valid_plan(case_id):
    return {
        "case_id": case_id,
        "title": "Synthetic self-test plan",
        "research_question": "Does temperature affect conversion within the feasible range?",
        "objective": "Find temperature maximizing conversion subject to furnace limit.",
        "assumptions": [{"id": "a1", "text": "synthetic assumption", "evidence": "self-test"}],
        "variables": [{"id": "v1", "name": "temperature", "type": "continuous",
                       "unit": "degC", "role": "independent"}],
        "parameters": [{
            "id": "p1", "name": "temperature", "unit": "degC", "kind": "continuous",
            "suggested_value": 350.0,
            "feasible_range": {"min": 300.0, "max": 400.0},
            "step": 10.0,
            "constraint_source": "synthetic furnace manual#max",
            "constraint_quote": "synthetic quote: max 400 degC",
            "bounds_origin": "device_range",
            "interactions": [],
        }],
        "controls": [{"id": "ct1", "name": "ambient", "value": "25 degC"}],
        "procedures": [{"id": "pr1", "step": "heat to setpoint", "note": "synthetic"}],
        "resources": [{"id": "rs1", "name": "furnace", "spec": "synthetic"}],
        "response_metrics": [{
            "id": "m1", "name": "conversion", "definition": "fraction reacted", "unit": "%",
            "measurement_method": "synthetic GC", "direction": "maximize",
            "target": 95.0, "acceptable_lower_bound": 80.0, "tolerance": "+-2",
            "prediction_method": "empirical_rule",
            "prediction_uncertainty": "synthetic +-5",
        }],
        "interpretation_criteria": [{"id": "ic1", "text": "accept if conversion >= lower bound"}],
        "search_strategy": {
            "method": "grid", "initial_points": [], "per_round": "add 4 grid points",
            "convergence_criteria": ["response change < 1%"],
            "stop_criteria": ["budget exhausted"], "candidate_count": 4,
            "seed": 0, "rounds_planned": 1,
        },
        "cost_budget": {
            "items": [{"item": "machine_hours", "amount": 4, "unit": "h", "cost": "synthetic"}],
            "total": "synthetic total", "time_hours": 8, "sample_count": 4,
            "throughput_note": "synthetic", "tradeoff_note": "synthetic",
        },
        "basis": [{"id": "b1", "claim": "synthetic basis", "source": "self-test"}],
        "risks": [{"id": "r1", "risk": "synthetic risk", "mitigation": "synthetic"}],
        "exception_handling": [{"id": "e1", "condition": "no_feasible_solution",
                                "action": "degrade and report"}],
        "confirmations": [{"id": "cf1", "item": "synthetic safety check",
                           "kind": "safety", "status": "pending"}],
    }


def make_case(tmp, name, case_obj, task_text):
    indir = os.path.join(tmp, name, "input")
    outdir = os.path.join(tmp, name, "output")
    workdir = os.path.join(tmp, name, "work")
    os.makedirs(indir)
    os.makedirs(outdir)
    os.makedirs(workdir)
    with open(os.path.join(indir, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case_obj, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(indir, "task.md"), "w", encoding="utf-8") as fh:
        fh.write(task_text)
    return indir, outdir, workdir


def write_plan(workdir, plan):
    with open(os.path.join(workdir, "plan.json"), "w", encoding="utf-8") as fh:
        json.dump(plan, fh, ensure_ascii=False, indent=2)


def main():
    tmp = tempfile.mkdtemp(prefix="skill_selftest_")
    try:
        # ---- 场景 1：happy path ----
        case = {"case_id": "self-test-synthetic", "topic": "synthetic_only"}
        task = "# synthetic self-test task\nThis is synthetic input for self-test only, not a formal answer.\n"
        indir, outdir, workdir = make_case(tmp, "happy", case, task)
        r = run([MAIN, "init", "--input", indir, "--output", outdir, "--work", workdir])
        check("happy: init exit 0", r.returncode == 0, r.stderr.strip())
        check("happy: result.json skeleton exists",
              os.path.exists(os.path.join(outdir, "result.json")))
        write_plan(workdir, minimal_valid_plan("self-test-synthetic"))
        r = run([VALIDATE, "plan", "--plan", os.path.join(workdir, "plan.json"),
                 "--report", os.path.join(workdir, "plan.vr.json")])
        check("happy: validate plan exit 0", r.returncode == 0, r.stdout.strip()[-200:])
        r = run([MAIN, "finalize", "--input", indir, "--output", outdir, "--work", workdir])
        check("happy: finalize exit 0", r.returncode == 0, r.stderr.strip())
        r = run([VALIDATE, "result", "--result", os.path.join(outdir, "result.json"),
                 "--output-dir", outdir])
        check("happy: validate result exit 0", r.returncode == 0, r.stdout.strip()[-200:])
        with open(os.path.join(outdir, "result.json"), "r", encoding="utf-8") as fh:
            res = json.load(fh)
        clog = res.get("change_log") or []
        check("happy: change_log non-empty and first=skeleton",
              bool(clog) and clog[0].get("step") == "skeleton" and bool(clog[0].get("content_hash")))
        check("happy: artifacts exist",
              os.path.exists(os.path.join(outdir, "plan.md")) and
              os.path.exists(os.path.join(outdir, "plan.json")))
        r = run([MAIN, "doe", "--work", workdir, "--seed", "0"])
        check("happy: doe exit 0", r.returncode == 0, r.stderr.strip())
        with open(os.path.join(workdir, "candidates.json"), "r", encoding="utf-8") as fh:
            cands = json.load(fh)
        check("happy: candidates generated", len(cands.get("candidates") or []) >= 1)

        # ---- 场景 2：空输入 ----
        indir2, outdir2, workdir2 = make_case(tmp, "empty", {"case_id": "empty-synthetic"}, "")
        for fn in ("case.json", "task.md"):
            os.remove(os.path.join(indir2, fn))
        r = run([MAIN, "init", "--input", indir2, "--output", outdir2, "--work", workdir2])
        check("empty: init exit 0", r.returncode == 0, r.stderr.strip())
        check("empty: result.json exists",
              os.path.exists(os.path.join(outdir2, "result.json")))

        # ---- 场景 3：坏 JSON ----
        indir3, outdir3, workdir3 = make_case(tmp, "bad", {"case_id": "bad-synthetic"}, "")
        with open(os.path.join(indir3, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        r = run([MAIN, "init", "--input", indir3, "--output", outdir3, "--work", workdir3])
        check("bad_json: init exit 0", r.returncode == 0, r.stderr.strip())
        with open(os.path.join(workdir3, "input_digest.json"), "r", encoding="utf-8") as fh:
            digest = json.load(fh)
        check("bad_json: error recorded", bool(digest.get("case_json_error")))

        # ---- 场景 4：约束冲突（建议值越界）----
        indir4, outdir4, workdir4 = make_case(tmp, "conflict", {"case_id": "conflict-synthetic"}, "")
        plan = minimal_valid_plan("conflict-synthetic")
        plan["parameters"][0]["suggested_value"] = 500.0  # 越界
        write_plan(workdir4, plan)
        r = run([VALIDATE, "plan", "--plan", os.path.join(workdir4, "plan.json"),
                 "--report", os.path.join(workdir4, "plan.vr.json")])
        check("conflict: validate plan exit 1 (blocking)", r.returncode == 1, r.stdout.strip()[-200:])

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [x for x in RESULTS if not x[1]]
    print("")
    print("SELFTEST %s: %d passed, %d failed" % (
        "PASS" if not failed else "FAIL", len(RESULTS) - len(failed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
