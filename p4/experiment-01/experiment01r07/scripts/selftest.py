#!/usr/bin/env python3
"""selftest.py — 自检：故障注入验证鲁棒性（空输入 / 坏 JSON / 缺参数 / 超大文件 / 越界参数）。

运行：python3 scripts/selftest.py
退出码：0 = 全部通过；1 = 存在失败。
仅依赖 Python 标准库；所有场景写入临时目录，运行后清理。
"""
import json
import os
import shutil
import sys
import tempfile

import core_impl as C

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), name, ("- " + detail if detail else ""))


def build_case(case_id="case-1", contract=None):
    obj = {"case_id": case_id}
    if contract is not None:
        obj["output_contract"] = contract
    return obj


def write_case(obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def run_scaffold(inp, out, work):
    return C.scaffold(inp, out, work)


def main():
    base = tempfile.mkdtemp(prefix="selftest_plan_")
    try:
        # 1) 空输入目录 -> 合法 insufficient_evidence 结果，退出不崩溃
        inp = os.path.join(base, "empty_in")
        out = os.path.join(base, "empty_out")
        work = os.path.join(base, "empty_work")
        os.makedirs(inp)
        r, _rep = run_scaffold(inp, out, work)
        ok_json = os.path.exists(os.path.join(out, "result.json"))
        check("empty_input_legal_result",
              ok_json and r.get("case_id") == "unknown"
              and r.get("status") == "insufficient_evidence")

        # 2) 坏 JSON + 可用 task.md -> 记录错误继续，不崩溃
        inp = os.path.join(base, "bad_in")
        out = os.path.join(base, "bad_out")
        work = os.path.join(base, "bad_work")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            f.write("{not valid json!!")
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
            f.write("# task\nchallenge_id: challenge-bad\n")
        r, _rep = run_scaffold(inp, out, work)
        check("bad_json_tolerated",
              r.get("status") in ("input_error", "partial")
              and any("invalid_json" in e for e in r.get("errors", [])))

        # 3) 缺参数 -> 默认指向 /input、/app/output、/app/work（仅验证声明，不触碰 /input）
        check("defaults_declared",
              C.DEFAULT_INPUT == "/input" and C.DEFAULT_OUTPUT == "/app/output"
              and C.DEFAULT_WORK == "/app/work")

        # 4) 超大文件（>2MB）-> 跳过并记 degradations
        inp = os.path.join(base, "big_in")
        out = os.path.join(base, "big_out")
        work = os.path.join(base, "big_work")
        os.makedirs(inp)
        write_case(build_case("case-big"), os.path.join(inp, "case.json"))
        big = os.path.join(inp, "materials", "big.md")
        os.makedirs(os.path.dirname(big), exist_ok=True)
        with open(big, "wb") as f:
            f.write(b"x" * (C.MAX_TEXT_BYTES + 10))
        r, rep = run_scaffold(inp, out, work)
        check("oversized_skipped",
              any("skipped" in d or "limit" in d for d in rep.get("degradations", [])))

        # 5) 契约回填 + 六要素骨架注入
        contract = {"plan": {}, "evidence_and_risks": {}, "confirmations": []}
        inp = os.path.join(base, "c_in")
        out = os.path.join(base, "c_out")
        work = os.path.join(base, "c_work")
        os.makedirs(inp)
        write_case(build_case("case-c", contract), os.path.join(inp, "case.json"))
        r, _rep = run_scaffold(inp, out, work)
        check("contract_echoed",
              all(k in r for k in contract) and r.get("case_id") == "case-c")
        check("six_skeleton_injected",
              all(k in r.get("plan", {}) for k in C.SIX_ORDER))

        # 6) 参数越界 -> validate 报 ERROR 并退出码 1
        inp = os.path.join(base, "v_in")
        out = os.path.join(base, "v_out")
        work = os.path.join(base, "v_work")
        os.makedirs(inp)
        write_case(build_case("case-v"), os.path.join(inp, "case.json"))
        r, _rep = run_scaffold(inp, out, work)
        r["plan"]["parameter_ranges"] = [
            {"name": "dose", "unit": "mg/kg", "min": 1, "max": 10, "chosen": 50,
             "basis": "literature"}]
        r["plan"]["hypothesis_and_variables"]["hypotheses"] = ["H: dose reduces tumor"]
        C.atomic_write_json(os.path.join(out, "result.json"), r)
        code = C.validate(inp, out, work)
        with open(os.path.join(work, "validate_report.json"), encoding="utf-8") as f:
            vrep = json.load(f)
        check("out_of_range_flagged",
              code == 1 and any("outside allowed range" in e for e in vrep["errors"]))

        # 7) 重复运行确定性（同输入两次 scaffold，顶层键序一致）
        r2, _ = run_scaffold(inp, out, work)
        check("deterministic_keys", list(r2.keys()) == list(r.keys()))

        nfail = sum(1 for _n, ok, _d in RESULTS if not ok)
        print("\nselftest: %d/%d passed" % (len(RESULTS) - nfail, len(RESULTS)))
        return 1 if nfail else 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
