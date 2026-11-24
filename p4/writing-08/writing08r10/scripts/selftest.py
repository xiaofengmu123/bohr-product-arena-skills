# -*- coding: utf-8 -*-
"""selftest.py -- 自检脚本（纯标准库）。

在临时目录构建最小夹具，验证三条路径与幂等性：
  1. 正常输入：output_contract 全字段原样回填、case_id/status 齐、产物存在、validate 通过。
  2. 空输入目录：status=insufficient_evidence，不抛异常，result.json 仍合法。
  3. 坏 JSON（case.json 损坏）：记录 errors，不崩溃，仍落盘 result.json。
  4. 幂等性：同一输入运行两次，除 runtime 外 result.json 一致。

退出码 0 表示全部通过，非 0 表示失败并打印差异。
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl  # noqa: E402

CASES = []


def make_case_json(case_id, contract):
    return {
        "case_id": case_id,
        "id": case_id,
        "task": {
            "genre": "methods",
            "output_contract": contract,
        },
    }


def run_pipeline(input_dir, output_dir, work_dir):
    class Args:
        pass
    a = Args()
    a.input = input_dir
    a.output = output_dir
    a.work = work_dir
    a.step = "all"
    core_impl.run(a)
    # validate
    report = core_impl.validate(input_dir, output_dir, work_dir)
    return report


def check(name, cond, detail=""):
    CASES.append((name, bool(cond), detail))


def test_normal():
    base = tempfile.mkdtemp(prefix="selftest_normal_")
    try:
        inp, out, work = (os.path.join(base, d) for d in ("input", "output", "work"))
        os.makedirs(inp)
        contract = {
            "manuscript": {"path": "manuscript.md", "format": "markdown"},
            "parameters_table": ["name", "value", "unit", "source"],
            "notes": "any",
        }
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(make_case_json("case-01", contract), fh)
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# 任务\n写成 Methods 章节\n")
        with open(os.path.join(inp, "train_config.yaml"), "w", encoding="utf-8") as fh:
            fh.write("lr: 0.001\nseed: 42\nepochs: 100\nbatch_size: 32\n")

        report = run_pipeline(inp, out, work)
        result = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))

        check("正常: validate ok", report["ok"] is True, str(report)[:200])
        check("正常: case_id", result.get("case_id") == "case-01", str(result.get("case_id")))
        check("正常: 契约字段原样回填",
              all(k in result for k in contract),
              "missing=%s" % [k for k in contract if k not in result])
        check("正常: status", result.get("status") in
              ("ok", "partial", "insufficient_evidence", "input_error"), str(result.get("status")))
        for art in ("manuscript.md", "evidence_map.json", "preserved_facts.json"):
            check("正常: 产物 %s" % art, os.path.isfile(os.path.join(out, art)))
        check("正常: 抽取到 seed=42 候选",
              _candidate_contains(os.path.join(work, "fact_candidates.json"), "42"))
        check("正常: change_log 非空", isinstance(result.get("change_log"), list) and len(result["change_log"]) > 0)
        # 幂等性
        base2 = tempfile.mkdtemp(prefix="selftest_idem_")
        try:
            inp2, out2, work2 = (os.path.join(base2, d) for d in ("input", "output", "work"))
            shutil.copytree(inp, inp2)
            os.makedirs(out2); os.makedirs(work2)
            run_pipeline(inp2, out2, work2)
            r1 = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
            r2 = json.load(open(os.path.join(out2, "result.json"), encoding="utf-8"))
            r1.pop("runtime", None); r2.pop("runtime", None)
            check("幂等: 两次结果一致(除 runtime)", r1 == r2)
        finally:
            shutil.rmtree(base2, ignore_errors=True)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_empty():
    base = tempfile.mkdtemp(prefix="selftest_empty_")
    try:
        inp, out, work = (os.path.join(base, d) for d in ("input", "output", "work"))
        os.makedirs(inp)
        report = run_pipeline(inp, out, work)
        result = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        check("空输入: 不崩溃且落盘", os.path.isfile(os.path.join(out, "result.json")))
        check("空输入: status=insufficient_evidence",
              result.get("status") == "insufficient_evidence", str(result.get("status")))
        check("空输入: case_id=unknown", result.get("case_id") == "unknown")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_bad_json():
    base = tempfile.mkdtemp(prefix="selftest_bad_")
    try:
        inp, out, work = (os.path.join(base, d) for d in ("input", "output", "work"))
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ this is not valid json \x00\x01")
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# 任务\n")
        report = run_pipeline(inp, out, work)
        result = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        check("坏 JSON: 不崩溃且落盘", os.path.isfile(os.path.join(out, "result.json")))
        check("坏 JSON: errors 记录", isinstance(result.get("errors"), list) and len(result["errors"]) > 0,
              str(result.get("errors")))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _candidate_contains(path, needle):
    obj, err = core_impl.read_json_safe(path)
    if err or not isinstance(obj, dict):
        return False
    for c in obj.get("candidates", []):
        v = c.get("value")
        if isinstance(v, dict):
            if needle in str(v):
                return True
        elif needle in str(v):
            return True
    return False


def main():
    test_normal()
    test_empty()
    test_bad_json()

    failed = [c for c in CASES if not c[1]]
    for name, ok, detail in CASES:
        print("[%s] %s %s" % ("PASS" if ok else "FAIL", name, ("-- " + detail) if detail else ""))
    print("\n%d/%d passed" % (len(CASES) - len(failed), len(CASES)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
