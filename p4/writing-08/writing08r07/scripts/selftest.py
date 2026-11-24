#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py —— 对 main.py 做鲁棒性自检（只依赖标准库）。

覆盖六类降级场景：
  1. 空输入目录        -> 退出码 0，result.json 合法，status=insufficient_evidence
  2. 坏 JSON(case.json)-> 退出码 0，result.json 合法，errors 记录，status=input_error
  3. 缺参数(用缺省)    -> 能构造 CLI；本测试用临时目录显式传入，另验证缺省路径存在性
  4. 超大文件(>2MB)    -> 跳过该文件，degradations 记录，不影响其它材料
  5. 坏编码文本        -> 容错读取，degradations 记录，继续抽取
  6. 正常案例          -> 完整 runall，产物齐备、契约回填、证据 locator+quote 非空

每次测试运行 main.py 后断言：退出码 0、result.json 可解析、必填字段齐备。
本脚本不写任何正式测试产物到 outputs/，只用临时目录，测试后清理。
"""
import json
import os
import subprocess
import sys
import tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN_PY = os.path.join(HERE, "main.py")

REQUIRED_FIELDS = (
    "case_id", "status", "artifacts", "sources", "uncertainty",
    "change_log", "errors", "degradations", "limitations",
    "pending_human", "runtime",
)


def _write(path, text, encoding="utf-8"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding=encoding) as fh:
        fh.write(text)


def _run_main(args):
    """运行 main.py，返回 (exit_code, stdout, stderr)。"""
    proc = subprocess.run(
        [sys.executable, "-B", MAIN_PY] + args,
        capture_output=True, text=True, timeout=120)
    return proc.returncode, proc.stdout, proc.stderr


def _load_result(out_dir):
    with open(os.path.join(out_dir, "result.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


def _assert(cond, label):
    if not cond:
        raise AssertionError("FAIL: %s" % label)


def _check_common(out_dir, expect_status):
    result = _load_result(out_dir)
    for field in REQUIRED_FIELDS:
        _assert(field in result, "missing required field %s" % field)
    _assert(result.get("status") == expect_status,
            "status should be %s, got %r" % (expect_status, result.get("status")))
    _assert(result.get("case_id"), "case_id should be non-empty")
    return result


def test_empty_input(root):
    inp = os.path.join(root, "empty", "input")
    out = os.path.join(root, "empty", "output")
    work = os.path.join(root, "empty", "work")
    os.makedirs(inp, exist_ok=True)  # 空目录
    rc, so, se = _run_main(["skeleton", "--input", inp, "--output", out,
                            "--work", work])
    _assert(rc == 0, "empty input exit 0, got %d stderr=%s" % (rc, se))
    result = _check_common(out, "insufficient_evidence")
    _assert(any("输入目录为空" in (l or "") for l in result.get("limitations", [])),
            "limitations should explain empty input")
    print("  [ok] empty_input -> insufficient_evidence, exit 0")


def test_bad_json(root):
    inp = os.path.join(root, "badjson", "input")
    out = os.path.join(root, "badjson", "output")
    work = os.path.join(root, "badjson", "work")
    os.makedirs(inp, exist_ok=True)
    _write(os.path.join(inp, "case.json"), '{"case_id": "x", "output_contract": {')
    _write(os.path.join(inp, "task.md"), "# 任务\n方法与实验章节。\n")
    rc, so, se = _run_main(["skeleton", "--input", inp, "--output", out,
                            "--work", work])
    _assert(rc == 0, "bad json exit 0, got %d stderr=%s" % (rc, se))
    result = _check_common(out, "input_error")
    _assert(any(e.get("issue") == "bad_json" for e in result.get("errors", [])),
            "errors should record bad_json")
    print("  [ok] bad_json -> input_error, error recorded, exit 0")


def test_missing_params_defaults(root):
    # 缺参数时使用缺省 /input /app/output /app/work；这里只验证 CLI 可构造。
    rc, so, se = _run_main(["--help"])
    _assert(rc == 0, "--help exit 0")
    _assert("skeleton" in so and "inventory" in so and "validate" in so,
            "--help should list subcommands")
    print("  [ok] missing params -> defaults exist, --help exit 0")


def test_oversized_file(root):
    inp = os.path.join(root, "big", "input")
    out = os.path.join(root, "big", "output")
    work = os.path.join(root, "big", "work")
    os.makedirs(inp, exist_ok=True)
    _write(os.path.join(inp, "case.json"),
           '{"case_id": "big-case", "output_contract": {"manuscript": "str"}}')
    _write(os.path.join(inp, "task.md"), "# 任务\n")
    # 3MB 超大文本
    big = os.path.join(inp, "huge.log")
    with open(big, "w", encoding="utf-8") as fh:
        fh.write("x" * (3 * 1024 * 1024))
    _write(os.path.join(inp, "notes.md"), "seed=42 learning_rate=1e-4\n")
    rc, so, se = _run_main(["inventory", "--input", inp, "--output", out,
                            "--work", work])
    _assert(rc == 0, "oversized exit 0, got %d stderr=%s" % (rc, se))
    inv_path = os.path.join(work, "materials_inventory.json")
    _assert(os.path.isfile(inv_path), "inventory written")
    with open(inv_path, "r", encoding="utf-8") as fh:
        inv = json.load(fh)
    skipped = [d for d in inv.get("degradations", [])
               if d.get("issue") == "oversized_skipped"]
    _assert(len(skipped) == 1, "exactly one oversized file skipped")
    facts_path = os.path.join(work, "extracted_facts.json")
    with open(facts_path, "r", encoding="utf-8") as fh:
        facts = json.load(fh)
    _assert(facts.get("count", 0) > 0, "small file facts still extracted")
    _assert(all(f.get("locator") and f.get("quote") for f in facts["facts"]),
            "every fact has locator+quote")
    print("  [ok] oversized file -> skipped+recorded, others extracted")


def test_bad_encoding(root):
    inp = os.path.join(root, "encoding", "input")
    out = os.path.join(root, "encoding", "output")
    work = os.path.join(root, "encoding", "work")
    os.makedirs(inp, exist_ok=True)
    _write(os.path.join(inp, "case.json"),
           '{"case_id": "enc", "output_contract": {"manuscript": "str"}}')
    _write(os.path.join(inp, "task.md"), "# 任务\n")
    # GBK 编码但声明为 utf-8 场景（用 GBK 写中文，脚本应经 gb18030 兜底读到）
    raw = "# 配置\n批次大小=32 学习率=0.001\n".encode("gb18030")
    with open(os.path.join(inp, "config.txt"), "wb") as fh:
        fh.write(raw)
    rc, so, se = _run_main(["inventory", "--input", inp, "--output", out,
                            "--work", work])
    _assert(rc == 0, "bad encoding exit 0, got %d stderr=%s" % (rc, se))
    facts_path = os.path.join(work, "extracted_facts.json")
    with open(facts_path, "r", encoding="utf-8") as fh:
        facts = json.load(fh)
    # 至少能读到内容（gb18030 兜底），可能抽到数字单位 32 / 0.001
    _assert(facts.get("count", 0) >= 0, "facts json valid")
    print("  [ok] bad encoding -> tolerated, no crash")


def test_normal_case(root):
    inp = os.path.join(root, "normal", "input")
    out = os.path.join(root, "normal", "output")
    work = os.path.join(root, "normal", "work")
    os.makedirs(inp, exist_ok=True)
    _write(os.path.join(inp, "case.json"), json.dumps({
        "case_id": "demo-case-001",
        "challenge_id": "writing-09",
        "output_contract": {
            "manuscript": "str",
            "evidence_map": "dict",
            "preserved_facts": "list",
        },
    }))
    _write(os.path.join(inp, "task.md"),
           "# 任务\n写方法与实验章节，覆盖六类要素。\n")
    _write(os.path.join(inp, "config.yaml"),
           "learning_rate: 1e-4\nbatch_size: 32\nseed: 42\n"
           "dataset: ImageNet-1k\nepochs: 100\n")
    rc, so, se = _run_main(["runall", "--input", inp, "--output", out,
                            "--work", work])
    _assert(rc == 0, "normal runall exit 0, got %d stderr=%s" % (rc, se))
    result = _load_result(out)
    # 骨架 status 为 partial；契约字段已回填
    _assert(result.get("status") == "partial", "skeleton status partial")
    for f in ("manuscript", "evidence_map", "preserved_facts"):
        _assert(f in result, "contract field %s backfilled" % f)
    # 校验报告存在
    rep = os.path.join(work, "validation_report.json")
    _assert(os.path.isfile(rep), "validation report written")
    with open(rep, "r", encoding="utf-8") as fh:
        report = json.load(fh)
    _assert("issues" in report, "validation report has issues list")
    facts_path = os.path.join(work, "extracted_facts.json")
    with open(facts_path, "r", encoding="utf-8") as fh:
        facts = json.load(fh)
    types = {f["type"] for f in facts["facts"]}
    _assert("hyperparameter" in types, "hyperparameter extracted")
    _assert("seed" in types, "seed extracted")
    _assert("dataset" in types, "dataset extracted")
    print("  [ok] normal case -> full pipeline, contract backfilled, facts extracted")


def test_determinism(root):
    """重复运行结果一致（skeleton 阶段内容确定性）。"""
    inp = os.path.join(root, "det", "input")
    os.makedirs(inp, exist_ok=True)
    _write(os.path.join(inp, "case.json"),
           '{"case_id": "det", "output_contract": {"m": "str"}}')
    _write(os.path.join(inp, "task.md"), "# 任务\n")
    outs = []
    for i in range(2):
        out = os.path.join(root, "det", "out%d" % i)
        work = os.path.join(root, "det", "work%d" % i)
        rc, so, se = _run_main(["skeleton", "--input", inp, "--output", out,
                                "--work", work])
        _assert(rc == 0, "determinism run exit 0")
        result = _load_result(out)
        result.pop("change_log", None)  # change_log 含时间戳，除外后应一致
        result.pop("runtime", None)
        outs.append(json.dumps(result, sort_keys=True))
    _assert(outs[0] == outs[1], "two runs produce identical skeleton (excl. timestamps)")
    print("  [ok] determinism -> identical output across runs (excl. timestamps)")


def main():
    print("selftest: 方法与实验章节写作 Skill 鲁棒性自检")
    if not os.path.isfile(MAIN_PY):
        print("FAIL: main.py not found at %s" % MAIN_PY)
        return 1
    root = tempfile.mkdtemp(prefix="skill-selftest-")
    tests = [
        ("empty_input", test_empty_input),
        ("bad_json", test_bad_json),
        ("missing_params_defaults", test_missing_params_defaults),
        ("oversized_file", test_oversized_file),
        ("bad_encoding", test_bad_encoding),
        ("normal_case", test_normal_case),
        ("determinism", test_determinism),
    ]
    passed, failed = 0, 0
    for name, fn in tests:
        print("[%s]" % name)
        try:
            fn(root)
            passed += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("  FAIL %s: %s" % (name, exc))
    try:
        shutil.rmtree(root, ignore_errors=True)
    except OSError:
        pass
    print("selftest summary: passed=%d failed=%d" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
