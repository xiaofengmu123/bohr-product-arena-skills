#!/usr/bin/env python3
"""本地自检：在临时目录合成最小输入，验证 init/check/render 可运行、幂等、异常不崩溃。

仅用于开发自检，不生成正式案例结果或隐藏测试。退出码 0 表示全部通过。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(args, cwd):
    return subprocess.run(
        [sys.executable, "-B", MAIN] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd,
    )


def main():
    tmp = tempfile.mkdtemp(prefix="weekly-skill-selftest-")
    inp = os.path.join(tmp, "input")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    for d in (inp, out, work):
        os.makedirs(d, exist_ok=True)
    ok = True

    def step(name, cond, detail=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + ((" | " + detail) if detail else ""))
        if not cond:
            ok = False

    case_p = os.path.join(inp, "case.json")
    task_p = os.path.join(inp, "task.md")
    result_p = os.path.join(out, "result.json")

    # 1. 空输入：init 不崩溃且落盘合法 partial 骨架
    r = run(["init", "--input", case_p, "--task", task_p, "--out", out, "--work", work], tmp)
    step("init 空输入退出码 0", r.returncode == 0, f"rc={r.returncode}")
    step("init 空输入落盘 result.json", os.path.exists(result_p))
    if os.path.exists(result_p):
        with open(result_p, encoding="utf-8") as f:
            data = json.load(f)
        step("骨架含 case_id 与 status=partial", data.get("case_id") and data.get("status") == "partial")

    # 2. 正常输入：output_contract 字段原样回填
    case = {
        "case_id": "selftest-001",
        "output_contract": {
            "title": {"required": True},
            "summary": {"required": True},
            "proposal": {"required": True},
        },
    }
    with open(case_p, "w", encoding="utf-8") as f:
        json.dump(case, f, ensure_ascii=False)
    with open(task_p, "w", encoding="utf-8") as f:
        f.write("# 自检示例任务\n周期 6 个月，经费 20 万元。")
    r = run(["init", "--input", case_p, "--task", task_p, "--out", out, "--work", work], tmp)
    step("init 正常输入退出码 0", r.returncode == 0)
    with open(result_p, encoding="utf-8") as f:
        data = json.load(f)
    step("case_id 回填", data.get("case_id") == "selftest-001")
    for k in ("title", "summary", "proposal"):
        step(f"contract 字段 {k} 存在", k in data)

    # 填一版最小完整内容，验证 check 全绿与 render 产物
    data["status"] = "complete"
    data["title"] = "自检示例：材料力学性能预测"
    data["summary"] = "示意摘要，非正式案例结果。"
    data["proposal"] = "# 自检示例\n## 1 研究背景与缺口\n示意内容。"
    data["elements"]["questions"] = [
        {"id": "Q1", "question": "示例研究问题", "falsifiable_test": "可据实验结果证伪", "source_ids": ["S1"]}
    ]
    data["elements"]["objectives"] = [
        {"id": "O1", "question_id": "Q1", "objective": "示例目标", "metric": "R^2", "threshold": ">=0.9", "threshold_basis": "S1"}
    ]
    data["elements"]["plan"]["constraints"] = {"period": "6 个月", "budget": "20 万元", "personnel": "2 人"}
    data["elements"]["risks"] = [
        {"id": "R1", "category": "data", "trigger_signal": "样本不足", "impact_scope": "无法训练", "fallback": "公开数据", "decision_point": "第 2 月末"}
    ]
    data["sources"] = [{"id": "S1", "locator": "task.md#L1", "quote": "周期 6 个月", "claims": ["Q1"]}]
    with open(result_p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    r = run(["check", "--result", result_p, "--input", case_p], tmp)
    step("check 退出码 0", r.returncode == 0, f"rc={r.returncode}")
    step("check FAIL=0", "FAIL=0" in r.stdout, (r.stdout.strip().splitlines() or [""])[-1])
    r = run(["render", "--result", result_p, "--out", out], tmp)
    step("render 生成 proposal.md", r.returncode == 0 and os.path.exists(os.path.join(out, "proposal.md")))
    step("render 生成 evidence_map.md", os.path.exists(os.path.join(out, "evidence_map.md")))

    # 3. 坏 JSON：init 不崩溃，仍落盘可解析骨架
    with open(case_p, "w", encoding="utf-8") as f:
        f.write("{ 不是合法 json")
    r = run(["init", "--input", case_p, "--task", task_p, "--out", out, "--work", work], tmp)
    step("init 坏 JSON 退出码 0", r.returncode == 0, f"rc={r.returncode}")
    step("init 坏 JSON 仍可解析 result.json", os.path.exists(result_p))

    # 4. 幂等：两次 init 的 case_id/status/contract_fields 一致
    with open(case_p, "w", encoding="utf-8") as f:
        json.dump(case, f, ensure_ascii=False)
    run(["init", "--input", case_p, "--task", task_p, "--out", out, "--work", work], tmp)
    with open(result_p, encoding="utf-8") as f:
        d1 = json.load(f)
    run(["init", "--input", case_p, "--task", task_p, "--out", out, "--work", work], tmp)
    with open(result_p, encoding="utf-8") as f:
        d2 = json.load(f)
    step("重复运行确定性", d1.get("case_id") == d2.get("case_id")
         and d1.get("status") == d2.get("status")
         and d1.get("runtime", {}).get("contract_fields") == d2.get("runtime", {}).get("contract_fields"))

    shutil.rmtree(tmp, ignore_errors=True)
    print("selftest:", "ALL PASS" if ok else "HAS FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
