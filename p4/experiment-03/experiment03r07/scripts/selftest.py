#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 鲁棒性自检（仅标准库）。

覆盖场景：
    1) 空输入目录         -> init 退出码 0，status=insufficient_evidence
    2) 坏 JSON / 坏编码    -> 捕获并记录，不崩溃
    3) 缺参数（默认路径）  -> --help 可用，默认路径为绝对路径
    4) 超大文件            -> 跳过并记录 skipped:too_large
    5) 最小合法流程        -> init -> validate -> finalize -> doe -> check_result 全通过
    6) 不可行方案          -> validate 退出码 1（推荐值越界）
    7) 写盘失败            -> 不崩溃，退出码 0 且记录 errors

退出码: 0=全部通过, 1=存在失败。
"""

import json
import os
import subprocess
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(SCRIPT_DIR, "main.py")
VALIDATE = os.path.join(SCRIPT_DIR, "validate_plan.py")
CHECK = os.path.join(SCRIPT_DIR, "check_result.py")
PY = sys.executable

RESULTS = []


def run(cmd):
    proc = subprocess.run([PY] + cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout, proc.stderr


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", name,
                         (" | " + detail) if detail else ""))


def write(path, content, binary=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if binary else "w"
    kwargs = {} if binary else {"encoding": "utf-8"}
    with open(path, mode, **kwargs) as fh:
        fh.write(content)


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def minimal_plan(case_id="test-001", status="ok"):
    return {
        "case_id": case_id, "status": status,
        "title": "自检最小方案",
        "research_question": "温度与时间对产率的影响",
        "objective": "在安全阈值内最大化产率",
        "assumptions": ["历史数据可用于初始点选择"],
        "variables": [{"name": "产率", "role": "dependent", "unit": "%",
                       "description": "目标响应"}],
        "parameters": [
            {"name": "温度", "unit": "°C", "recommended_value": 80,
             "feasible_range": {"min": 60, "max": 120}, "step": 10,
             "constraint_source": "case.json#constraints",
             "quote": "温度上限 120 °C"},
            {"name": "时间", "unit": "min", "recommended_value": 30,
             "feasible_range": {"min": 10, "max": 60}, "step": 10,
             "constraint_source": "case.json#constraints",
             "quote": "时间 10–60 min"},
        ],
        "controls": [{"name": "空白对照", "purpose": "扣除基线",
                      "spec": "不加热"}],
        "procedures": [{"step": 1, "action": "按候选点设置参数",
                        "expected_output": "记录响应",
                        "acceptance": "响应可测量"}],
        "resources": [{"name": "反应器", "spec": "量程 200 °C",
                       "quantity": 1, "source": "case.json#equipment"}],
        "response_metrics": [
            {"name": "产率", "unit": "%", "measurement_method": "称重",
             "direction": "maximize", "target": ">= 90",
             "acceptable_lower_bound": 80, "prediction_method": "历史拟合",
             "uncertainty": "±5%"}],
        "interpretation_criteria": [{"criterion": "产率 >= 90",
                                     "threshold": 90, "action": "接受"}],
        "search_strategy": {"method": "grid", "initial_points": [[80, 30]],
                            "per_round": "每次 2 点", "stop_criteria": ["预算耗尽"],
                            "candidate_count": 4, "seed": 0},
        "cost_budget": {"items": [{"item": "机时", "quantity": 4,
                                   "unit_cost": 10, "cost": 40,
                                   "basis": "case.json#price"}],
                        "total": "40 元", "time_hours": 2,
                        "tradeoff_note": "成本超 20% 时产率目标降 5%"},
        "basis": [{"claim": "温度上限", "source_id": "s1",
                   "summary": "设备量程"}],
        "risks": [{"risk": "约束冲突", "likelihood": "低",
                   "impact": "高", "mitigation": "转人工"}],
        "exception_handling": [{"scenario": "无可行解",
                                "detection": "validate 阻断",
                                "response": "降级或终止"}],
        "confirmations": [{"item": "安全确认", "owner": "实验室安全员",
                           "status": "pending"}],
        "sources": [{"id": "s1", "locator": "case.json#constraints",
                     "quote": "温度上限 120 °C", "claim": "温度可行域上限"}],
        "uncertainty": {"unverified_values": ["产率预测"],
                        "missing_data": [], "assumptions_flagged": [],
                        "prediction_uncertainty": "±5%"},
        "limitations": ["自检用最小方案"],
        "pending_human": ["安全确认"],
        "change_log": [{"time": "2026-01-01T00:00:00Z",
                        "action": "骨架落盘", "reason": "初始化"}],
        "errors": [], "degradations": [],
    }


def test_empty_input():
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "input"); out = os.path.join(td, "output")
        work = os.path.join(td, "work")
        os.makedirs(inp)
        code, _, _ = run([MAIN, "init", "--input", inp,
                          "--output", out, "--work", work])
        result = read_json(os.path.join(out, "result.json"))
        ok = (code == 0 and result.get("status") == "insufficient_evidence"
              and result.get("case_id") == "")
        record("空输入目录", ok, "code=%d status=%s" % (code, result.get("status")))


def test_bad_json():
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "input"); out = os.path.join(td, "output")
        work = os.path.join(td, "work")
        os.makedirs(inp)
        write(os.path.join(inp, "case.json"), '{"case_id": broken')
        write(os.path.join(inp, "task.md"), "# 任务\n说明")
        code, _, _ = run([MAIN, "init", "--input", inp,
                          "--output", out, "--work", work])
        digest = read_json(os.path.join(work, "input_digest.json"))
        result = read_json(os.path.join(out, "result.json"))
        ok = (code == 0 and digest.get("case_json_error") is not None
              and result.get("status") == "insufficient_evidence")
        record("坏 JSON 容错", ok, "case_json_error=%s" % digest.get("case_json_error"))


def test_bad_encoding():
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "input"); out = os.path.join(td, "output")
        work = os.path.join(td, "work")
        os.makedirs(inp)
        write(os.path.join(inp, "case.json"),
              json.dumps({"case_id": "enc-1"}, ensure_ascii=False),
              binary=False)
        write(os.path.join(inp, "notes.md"), b"\xff\xfe\x00bad", binary=True)
        code, _, _ = run([MAIN, "init", "--input", inp,
                          "--output", out, "--work", work])
        result = read_json(os.path.join(out, "result.json"))
        ok = (code == 0 and result.get("case_id") == "enc-1")
        record("坏编码容错", ok, "case_id=%s" % result.get("case_id"))


def test_defaults_help():
    code, _, _ = run([MAIN, "--help"])
    record("默认路径与 --help", code == 0, "code=%d" % code)


def test_oversized():
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "input"); out = os.path.join(td, "output")
        work = os.path.join(td, "work")
        os.makedirs(inp)
        write(os.path.join(inp, "case.json"), '{"case_id": "big-1"}')
        write(os.path.join(inp, "big.bin"), b"\x00" * (2 * 1024 * 1024 + 1),
              binary=True)
        code, _, _ = run([MAIN, "init", "--input", inp,
                          "--output", out, "--work", work])
        digest = read_json(os.path.join(work, "input_digest.json"))
        skipped = [f for f in digest["files"]
                   if f.get("kind") == "skipped:too_large"]
        result = read_json(os.path.join(out, "result.json"))
        ok = (code == 0 and len(skipped) == 1
              and result.get("case_id") == "big-1")
        record("超大文件跳过", ok, "skipped=%d" % len(skipped))


def test_minimal_flow():
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "input"); out = os.path.join(td, "output")
        work = os.path.join(td, "work")
        os.makedirs(inp)
        write(os.path.join(inp, "case.json"), '{"case_id": "test-001"}')
        write(os.path.join(inp, "task.md"), "# 任务\n最小流程")
        code, _, _ = run([MAIN, "init", "--input", inp,
                          "--output", out, "--work", work])
        if code != 0:
            record("最小合法流程", False, "init code=%d" % code)
            return
        write(os.path.join(work, "plan.json"),
              json.dumps(minimal_plan(), ensure_ascii=False))
        vcode, _, _ = run([VALIDATE, os.path.join(work, "plan.json"),
                           "--report", os.path.join(work, "vr.json")])
        fcode, _, _ = run([MAIN, "finalize", "--input", inp,
                           "--output", out, "--work", work])
        dcode, _, _ = run([MAIN, "doe", "--work", work, "--seed", "0"])
        ccode, _, _ = run([CHECK, os.path.join(out, "result.json"),
                           "--output-dir", out])
        result = read_json(os.path.join(out, "result.json"))
        cand = read_json(os.path.join(work, "candidates.json"))
        plan_md = os.path.isfile(os.path.join(out, "plan.md"))
        plan_json = os.path.isfile(os.path.join(out, "plan.json"))
        ok = (vcode == 0 and fcode == 0 and dcode == 0 and ccode == 0
              and result.get("status") == "ok" and plan_md and plan_json
              and len(cand.get("points", [])) >= 1)
        record("最小合法流程", ok,
               "v=%d f=%d d=%d c=%d points=%d"
               % (vcode, fcode, dcode, ccode, len(cand.get("points", []))))


def test_infeasible():
    with tempfile.TemporaryDirectory() as td:
        plan = minimal_plan()
        plan["parameters"][0]["recommended_value"] = 999
        p = os.path.join(td, "plan.json")
        write(p, json.dumps(plan, ensure_ascii=False))
        code, _, _ = run([VALIDATE, p])
        record("越界检测（exit 1）", code == 1, "code=%d" % code)


def test_write_failure():
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "input"); work = os.path.join(td, "work")
        os.makedirs(inp)
        write(os.path.join(inp, "case.json"), '{"case_id": "wf-1"}')
        # output 指向一个已存在的文件，使目录创建失败
        out_file = os.path.join(td, "output_file")
        write(out_file, "occupied")
        code, _, _ = run([MAIN, "init", "--input", inp,
                          "--output", out_file, "--work", work])
        record("写盘失败不崩溃", code == 0, "code=%d" % code)


def main():
    print("== weekly-skill selftest ==")
    test_empty_input()
    test_bad_json()
    test_bad_encoding()
    test_defaults_help()
    test_oversized()
    test_minimal_flow()
    test_infeasible()
    test_write_failure()
    failed = [r for r in RESULTS if not r[1]]
    print("==" * 20)
    print("通过 %d / %d" % (len(RESULTS) - len(failed), len(RESULTS)))
    if failed:
        for name, _, detail in failed:
            print("失败:", name, detail)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
