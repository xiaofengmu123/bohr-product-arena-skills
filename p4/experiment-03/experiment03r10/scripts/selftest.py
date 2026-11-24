#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for the Skill package (standard library only).

Runs the orchestrator against synthetic inputs in a temporary directory and
asserts the contract invariants. It never writes into /input, /app/work or
/app/output; everything happens under a temporary directory.

Exit codes:
    0  all checks passed
    1  at least one check failed
    2  usage error
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(cmd, cwd):
    if cmd and cmd[0] == "main.py":
        cmd = cmd[1:]
    proc = subprocess.run([sys.executable, "-B", MAIN] + cmd, cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    return proc


def load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def make_case(tmp, case_id="demo-case", bad_json=False, with_task=True):
    inp = os.path.join(tmp, "input")
    os.makedirs(inp, exist_ok=True)
    if bad_json:
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{not json")
    else:
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": case_id, "topic": "catalysis"}, fh,
                      ensure_ascii=False)
    if with_task:
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# task\n研究目标：温度与转化率。\n")
    if not bad_json:
        mat = os.path.join(inp, "materials")
        os.makedirs(mat, exist_ok=True)
        with open(os.path.join(mat, "oven.md"), "w", encoding="utf-8") as fh:
            fh.write("# oven\n量程 20-120 °C；材料耐温 ≤ 100 °C。\n")
    return inp


def check_empty(tmp):
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    inp = os.path.join(tmp, "input-empty")
    os.makedirs(inp, exist_ok=True)
    p = run(["main.py", "init", "--input", inp, "--output", out, "--work", work], tmp)
    assert p.returncode == 0, "empty init failed: %s" % p.stderr
    r = load(os.path.join(out, "result.json"))
    assert r["status"] == "partial"
    assert "plan" in r and "sources" in r
    return "empty input ok"


def check_bad_json(tmp):
    out = os.path.join(tmp, "output-bad")
    work = os.path.join(tmp, "work-bad")
    inp = make_case(tmp, bad_json=True)
    p = run(["main.py", "init", "--input", inp, "--output", out, "--work", work], tmp)
    assert p.returncode == 0, "bad-json init crashed: %s" % p.stderr
    digest = load(os.path.join(work, "input_digest.json"))
    assert digest.get("case_json_error"), "bad JSON should be recorded"
    return "bad json ok"


def check_happy(tmp):
    inp = make_case(tmp)
    out = os.path.join(tmp, "output-happy")
    work = os.path.join(tmp, "work-happy")
    p = run(["main.py", "init", "--input", inp, "--output", out, "--work", work], tmp)
    assert p.returncode == 0, p.stderr
    plan = load(os.path.join(work, "plan.json"))
    assert plan["case_id"] == "demo-case"

    plan["research_question"] = "在给定温度区间内最大化转化率并控制副产物"
    plan["objective"] = "确定温度建议值、可行区间与停止判据"
    plan["parameters"] = [{
        "name": "reaction_temperature", "unit": "°C", "role": "key",
        "suggested_value": 90, "feasible_range": {"min": 40, "max": 100},
        "step": 10,
        "limits": [
            {"kind": "device", "min": 20, "max": 120, "source": "materials/oven.md",
             "quote": "量程 20-120 °C"},
            {"kind": "material", "min": None, "max": 100,
             "source": "materials/oven.md", "quote": "材料耐温 ≤ 100 °C"},
        ],
        "basis": {"locator": "materials/oven.md", "quote": "材料耐温 ≤ 100 °C"},
    }]
    plan["response_metrics"] = [{
        "name": "conversion", "unit": "%", "direction": "maximize", "target": 90,
        "measurement_method": "GC", "prediction_method": "history fit",
        "uncertainty": "±5 %",
        "basis": {"locator": "task.md", "quote": "研究目标：温度与转化率"},
    }]
    plan["search_strategy"] = {"method": "grid", "initial_points": [40, 90, 100],
                               "per_round": "每轮 3 点", "stop_criteria": ["收敛"],
                               "candidate_count": 8, "seed": 0}
    plan["cost_budget"] = {"items": [{"name": "机时", "amount": "2 h"}],
                           "total": "约 2 机时", "time_hours": 2,
                           "tradeoff_note": "转化率 < 85% 即停止加温"}
    with open(os.path.join(work, "plan.json"), "w", encoding="utf-8") as fh:
        json.dump(plan, fh, ensure_ascii=False)

    p = run(["main.py", "validate", "--work", work, "--strict-sources"], tmp)
    assert p.returncode in (0, 2), "validate failed: %s" % p.stderr
    report = load(os.path.join(work, "plan.validation_report.json"))
    assert not report["errors"], "expected no blocking errors, got %r" % report["errors"]

    p = run(["main.py", "doe", "--work", work], tmp)
    assert p.returncode == 0, p.stderr
    cands = load(os.path.join(work, "candidates.json"))
    assert cands["count"] == 8 and cands["candidates"]

    p = run(["main.py", "finalize", "--output", out, "--work", work], tmp)
    assert p.returncode == 0, p.stderr
    r = load(os.path.join(out, "result.json"))
    assert r["case_id"] == "demo-case"
    assert r["sources"], "sources should be populated"
    assert os.path.isfile(os.path.join(out, "plan.md"))
    assert os.path.isfile(os.path.join(out, "plan.json"))

    p = run(["main.py", "check", "--output", out], tmp)
    assert p.returncode in (0, 2), "check failed: %s" % p.stderr

    # reproducibility: run doe twice, same seed -> same output
    p2 = run(["main.py", "doe", "--work", work], tmp)
    assert p2.returncode == 0
    cands2 = load(os.path.join(work, "candidates.json"))
    assert cands == cands2, "doe is not deterministic"
    return "happy path ok"


def main():
    tmp = tempfile.mkdtemp(prefix="skill-selftest-")
    try:
        results = []
        results.append(check_empty(tmp))
        results.append(check_bad_json(tmp))
        results.append(check_happy(tmp))
        print("SELFTEST PASS")
        for r in results:
            print("  - " + r)
        return 0
    except AssertionError as exc:
        print("SELFTEST FAIL: %s" % exc, file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - defensive
        print("SELFTEST ERROR: %s" % exc, file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
