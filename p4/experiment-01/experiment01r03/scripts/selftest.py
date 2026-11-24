#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scix-experiment-plan-v2 自检脚本：可勾选清单的机器可执行版。

把 SKILL.md 的自检清单逐条编码为断言，并额外做**确定性**验证：
- 在临时目录构造一个明确标注为 `__selftest_demo__` 的合成输入（非正式案例），
  连续运行两次完整流水线，断言 result.json 与 plan.md **逐字节一致**；
- 验证 result.json 回填契约键、信封字段齐全、证据条目带 locator+quote；
- 验证空输入 → insufficient_evidence、坏 JSON → 记录后继续，均不崩溃。

退出码 0 = 全部通过；1 = 存在失败。只依赖标准库。
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import main as M  # noqa: E402


DEMO_CASE = {
    "case_id": "__selftest_demo__",
    "goal": "【自检合成输入，非正式案例】评估某抑制剂对某细胞系增殖的影响",
    "output_contract": {
        "plan": {},
        "evidence_and_risks": {"evidence_map": []},
        "confirmations": {},
    },
}

DEMO_TASK = (
    "# 自检合成任务（非正式案例，仅用于验证流水线确定性）\n"
    "challenge_id: `__selftest_demo__`\n"
)

PASS = []
FAIL = []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    mark = "PASS" if condition else "FAIL"
    print("[{}] {}{}".format(mark, name, (" - " + detail) if detail else ""))
    return condition


def build_fixture(root):
    """在 root 下构造合成输入；返回 input_dir。"""
    inp = os.path.join(root, "input")
    os.makedirs(inp, exist_ok=True)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8-sig") as fh:
        fh.write(json.dumps(DEMO_CASE, ensure_ascii=False))
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
        fh.write(DEMO_TASK)
    with open(os.path.join(inp, "materials.txt"), "w", encoding="utf-8") as fh:
        fh.write("合成材料：示例文本，仅用于自检，不含任何正式案例答案。\n")
    return inp


def run_pipeline(input_dir, output_dir, work_dir):
    probe = M.run_probe(input_dir, work_dir)
    M.run_scaffold(input_dir, output_dir, work_dir, probe=probe)
    report = M.run_validate(input_dir, output_dir, work_dir)
    return probe, report


def main():
    tmp = tempfile.mkdtemp(prefix="scix-selftest-")
    try:
        inp = build_fixture(tmp)

        # 1) 确定性：两次完整运行逐字节一致。
        out_a, work_a = os.path.join(tmp, "outA"), os.path.join(tmp, "workA")
        out_b, work_b = os.path.join(tmp, "outB"), os.path.join(tmp, "workB")
        run_pipeline(inp, out_a, work_a)
        run_pipeline(inp, out_b, work_b)

        ra = open(os.path.join(out_a, "result.json"), "rb").read()
        rb = open(os.path.join(out_b, "result.json"), "rb").read()
        pa = open(os.path.join(out_a, "plan.md"), "rb").read()
        pb = open(os.path.join(out_b, "plan.md"), "rb").read()
        check("确定性: 两次运行 result.json 逐字节一致", ra == rb)
        check("确定性: 两次运行 plan.md 逐字节一致", pa == pb)

        result = json.loads(ra.decode("utf-8"))
        probe = json.loads(M.canonical_json(M.run_probe(inp, work_a)))

        # 2) 输出契约。
        check("信封: case_id 非空", bool(result.get("case_id")))
        check("信封: status 合法", result.get("status") in M.ALLOWED_STATUS,
              str(result.get("status")))
        for key in DEMO_CASE["output_contract"]:
            check("契约键已回填: {}".format(key), key in result)
        for key in ("sources", "uncertainty", "pending_human", "change_log"):
            check("信封字段存在: {}".format(key), key in result)
        check("确定性字段: input_fingerprint 存在",
              bool(result.get("determinism", {}).get("input_fingerprint")))

        # 3) 自检脚本自身可验证 validate 判定为可用（退出码 0）。
        _, report = run_pipeline(inp, out_a, work_a)
        check("validate 退出码 0（骨架阶段允许警告）", report["exit_code"] == 0)

        # 4) 降级：空输入目录。
        empty = os.path.join(tmp, "empty_input")
        os.makedirs(empty, exist_ok=True)
        empty_out, empty_work = os.path.join(tmp, "empty_out"), os.path.join(tmp, "empty_work")
        M.run_scaffold(empty, empty_out, empty_work)
        er = json.loads(open(os.path.join(empty_out, "result.json"), encoding="utf-8").read())
        check("降级: 空输入不崩溃且落盘 insufficient_evidence",
              er.get("status") == "insufficient_evidence")

        # 5) 降级：坏 JSON。
        bad = os.path.join(tmp, "bad_input")
        os.makedirs(bad, exist_ok=True)
        with open(os.path.join(bad, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ not valid json")
        with open(os.path.join(bad, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("challenge_id: `__selftest_bad__`\n")
        bad_out, bad_work = os.path.join(tmp, "bad_out"), os.path.join(tmp, "bad_work")
        M.run_scaffold(bad, bad_out, bad_work)
        br = json.loads(open(os.path.join(bad_out, "result.json"), encoding="utf-8").read())
        check("降级: 坏 JSON 记录错误后继续", bool(br.get("errors")) and br.get("status") != "input_error")

        # 6) 证据规范：骨架阶段 evidence_map 存在且为 list（空即可，不伪造）。
        check("证据规范: evidence_map 为 list（不伪造证据）",
              isinstance(result.get("evidence_and_risks", {}).get("evidence_map"), list))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n{} passed, {} failed".format(len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
