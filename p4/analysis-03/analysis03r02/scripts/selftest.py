#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — sci-viz-checkpoint 自检脚本（仅标准库）。

在临时目录构造 4 个场景并验证 init/commit/check 闭环：
  1) 空输入目录         -> result.json 合法、status=insufficient_evidence、退出码 0
  2) 坏 JSON            -> 记录 degradations，仍产出合法 result.json
  3) 缺 output_contract -> 最小字段集兜底，uncertainty 记录 pending_human
  4) 完整契约           -> 契约字段全部原样回填，commit 后 status=ok

用法：
  python3 scripts/selftest.py            # 全部场景，逐项打印
  python3 scripts/selftest.py --quiet    # 只打印最终结论
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
PYTHON = sys.executable
VALID_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")


def run(*argv):
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run([PYTHON, "-B", MAIN] + list(argv), cwd=HERE,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env)
    return proc.returncode, proc.stdout, proc.stderr


def load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def scenario(quiet, name, setup, expect_status=None, expect_keys=(), expect_degraded=False):
    tmp = tempfile.mkdtemp(prefix="scv_selftest_")
    input_dir = os.path.join(tmp, "input")
    output_dir = os.path.join(tmp, "output")
    work_dir = os.path.join(tmp, "work")
    os.makedirs(input_dir)
    setup(input_dir)
    checks = []
    try:
        rc_init, _, _ = run("init", "--input", input_dir, "--output", output_dir, "--work", work_dir)
        result_path = os.path.join(output_dir, "result.json")
        obj = load(result_path)
        checks.append(("init 退出码 0", rc_init == 0))
        checks.append(("result.json 存在", os.path.exists(result_path)))
        checks.append(("case_id 非空", bool(obj.get("case_id"))))
        checks.append(("status 合法", obj.get("status") in VALID_STATUS))
        if expect_status:
            checks.append(("status=%s" % expect_status, obj.get("status") == expect_status))
        checks.append(("change_log 非空", len(obj.get("change_log", [])) >= 1))
        checks.append(("budget.phases 存在", isinstance(obj.get("budget", {}).get("phases"), list)))
        checks.append(("work/plan.md 存在", os.path.exists(os.path.join(work_dir, "plan.md"))))
        for key in expect_keys:
            checks.append(("契约字段 %s 存在" % key, key in obj))
        if expect_degraded:
            checks.append(("degradations 非空", len(obj.get("degradations", [])) >= 1))
        if not expect_keys:
            has_pending = any(isinstance(u, dict) and u.get("pending_human")
                              for u in obj.get("uncertainty", []))
            checks.append(("uncertainty 记录 pending_human", has_pending))

        rc_commit, _, _ = run("commit", "--input", input_dir, "--output", output_dir,
                              "--work", work_dir, "--status", "ok", "--phase", "A",
                              "--log", "自检提交")
        obj = load(result_path)
        checks.append(("commit 退出码 0", rc_commit == 0))
        checks.append(("commit 后 status=ok", obj.get("status") == "ok"))
        checks.append(("budget 阶段 A 标记完成",
                       any(p.get("id") == "A" and p.get("done")
                           for p in obj.get("budget", {}).get("phases", []))))

        rc_check, out_check, _ = run("check", "--input", input_dir, "--output", output_dir,
                                     "--work", work_dir, "--require-contract")
        checks.append(("check 退出码 0", rc_check == 0))
        checks.append(("check 输出 summary", "summary:" in out_check))
    except Exception as exc:  # noqa: BLE001
        checks.append(("场景不抛异常", False))
        if not quiet:
            print("  [%s] 异常: %s" % (name, exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for _, ok in checks if ok)
    total = len(checks)
    if not quiet:
        for label, ok in checks:
            print("    %s %s" % ("PASS" if ok else "FAIL", label))
    print("%s 场景 %s: %d/%d" % ("PASS" if passed == total else "FAIL", name, passed, total))
    return passed == total


def setup_empty(_input_dir):
    pass


def setup_bad_json(input_dir):
    with open(os.path.join(input_dir, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{ this is not json")
    with open(os.path.join(input_dir, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 任务\n")


def setup_no_contract(input_dir):
    with open(os.path.join(input_dir, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "demo-no-contract"}, fh, ensure_ascii=False)
    with open(os.path.join(input_dir, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 任务\n")


def setup_full_contract(input_dir):
    with open(os.path.join(input_dir, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "demo-full", "output_contract": {
            "figure_type": "box", "n_samples": 0,
            "units": {"x": "g/L", "y": "count"}}}, fh, ensure_ascii=False)
    with open(os.path.join(input_dir, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 任务\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description="sci-viz-checkpoint 自检")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    scenarios = [
        ("空输入", setup_empty, "insufficient_evidence", (), False),
        ("坏 JSON", setup_bad_json, None, (), True),
        ("缺契约", setup_no_contract, None, (), False),
        ("完整契约", setup_full_contract, None, ("figure_type", "n_samples", "units"), False),
    ]
    results = []
    for name, setup, expect_status, expect_keys, expect_degraded in scenarios:
        results.append(scenario(args.quiet, name, setup,
                                expect_status=expect_status,
                                expect_keys=expect_keys,
                                expect_degraded=expect_degraded))
    ok = all(results)
    print("%s 自检合计: %d/%d 场景通过" % ("PASS" if ok else "FAIL", sum(results), len(results)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
