#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 三态自检：最小案例 / 空输入 / 坏 JSON。

在每个临时 fixture 上跑完整管线（input_probe → scaffold_result →
validate_result），断言结果与退出码。全过退出码 0，任一失败退出码 1。
只依赖标准库；不生成示例答案，不预写正式测试产物。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def run_script(name, input_dir, output_dir, work_dir):
    script = os.path.join(HERE, name)
    cmd = [sys.executable, script,
           "--input", input_dir, "--output", output_dir, "--work", work_dir]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def minimal_fixture(root):
    inp = os.path.join(root, "input")
    os.makedirs(inp, exist_ok=True)
    case = {
        "case_id": "self-test-001",
        "goal": "评估抑制剂 X 对细胞系 A 增殖的影响",
        "output_contract": {"plan": {}, "evidence_and_risks": {}, "confirmations": {}},
        "candidate_plans": [
            {"id": "p1", "dose_range": [0, 10], "budget": 5000, "duration_days": 7},
            {"id": "p2", "dose_range": [0, 50], "budget": 8000, "duration_days": 10},
            {"id": "p3", "dose_range": [0, 100], "budget": 12000, "duration_days": 14},
        ],
        "resource_contract": {
            "instrument": "plate_reader",
            "max_range": 3.0,
            "price_per_hour": 120,
            "available": True,
        },
    }
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        json.dump(case, f, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write("# 任务\nchallenge_id: `challenge-self-test`\n生成实验方案。\n")
    return inp


def empty_fixture(root):
    inp = os.path.join(root, "empty_input")
    os.makedirs(inp, exist_ok=True)
    return inp


def bad_json_fixture(root):
    inp = os.path.join(root, "bad_input")
    os.makedirs(inp, exist_ok=True)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        f.write("{ this is not valid json")
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write("# 任务\n生成实验方案。\n")
    return inp


def test_minimal():
    root = tempfile.mkdtemp(prefix="scix_min_")
    try:
        inp = minimal_fixture(root)
        out, work = os.path.join(root, "out"), os.path.join(root, "work")
        p1 = run_script("input_probe.py", inp, out, work)
        p2 = run_script("scaffold_result.py", inp, out, work)
        p3 = run_script("validate_result.py", inp, out, work)
        result = load_json(os.path.join(out, "result.json"))
        ok = (result is not None
              and result.get("case_id") == "self-test-001"
              and result.get("status") == "partial"
              and "plan" in result and "evidence_and_risks" in result
              and "confirmations" in result
              and isinstance(result.get("change_log"), list)
              and p3.returncode == 0
              and os.path.isfile(os.path.join(out, "plan.md")))
        return ok, "minimal", (p1.returncode, p2.returncode, p3.returncode), result
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_empty():
    root = tempfile.mkdtemp(prefix="scix_empty_")
    try:
        inp = empty_fixture(root)
        out, work = os.path.join(root, "out"), os.path.join(root, "work")
        run_script("input_probe.py", inp, out, work)
        run_script("scaffold_result.py", inp, out, work)
        p3 = run_script("validate_result.py", inp, out, work)
        result = load_json(os.path.join(out, "result.json"))
        ok = (result is not None
              and result.get("status") == "insufficient_evidence"
              and result.get("case_id") == "unknown"
              and p3.returncode == 0)
        return ok, "empty", (p3.returncode,), result
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_bad_json():
    root = tempfile.mkdtemp(prefix="scix_bad_")
    try:
        inp = bad_json_fixture(root)
        out, work = os.path.join(root, "out"), os.path.join(root, "work")
        run_script("input_probe.py", inp, out, work)
        run_script("scaffold_result.py", inp, out, work)
        p3 = run_script("validate_result.py", inp, out, work)
        result = load_json(os.path.join(out, "result.json"))
        ok = (result is not None
              and result.get("status") == "input_error"
              and p3.returncode == 0)
        return ok, "bad_json", (p3.returncode,), result
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    failures = 0
    for fn in (test_minimal, test_empty, test_bad_json):
        try:
            ok, name, codes, result = fn()
            status = result.get("status") if isinstance(result, dict) else None
            print("[%s] %s codes=%s status=%s" % ("PASS" if ok else "FAIL", name, codes, status))
            if not ok:
                failures += 1
        except Exception as exc:  # noqa: BLE001
            print("[FAIL] %s exception=%r" % (fn.__name__, exc))
            failures += 1
    print("selftest: %s" % ("ALL_PASS" if failures == 0 else "%d_FAIL" % failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
