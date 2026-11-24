#!/usr/bin/env python3
"""本地自检：覆盖缺输入 / 坏 JSON / 无材料 / 正常 demo 与契约校验。

退出码 0 = 通过；1 = 失败。所有场景在临时目录内构造，不依赖正式案例，不产生预写答案。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

MAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")

PASS = 0
FAIL = 0


def run(args):
    return subprocess.run([sys.executable, "-B", MAIN] + args,
                          capture_output=True, text=True)


def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS  " + msg)
    else:
        FAIL += 1
        print("FAIL  " + msg)


def write(path, content, binary=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if binary:
        with open(path, "wb") as f:
            f.write(content)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def contract_keys_ok(result, contract):
    for k in contract:
        if k not in result:
            return False
    return result.get("output_contract") == contract


def scenario_missing_input(tmp):
    inp = os.path.join(tmp, "nope")
    out = os.path.join(tmp, "out1")
    work = os.path.join(tmp, "work1")
    r = run(["skeleton", "--input", inp, "--output", out, "--work", work])
    ok = os.path.isfile(os.path.join(out, "result.json"))
    res = load(os.path.join(out, "result.json")) if ok else {}
    check(r.returncode == 0 and ok and res.get("status") == "input_error",
          "缺 /input 目录 → 退出码 0 且 status=input_error")
    return res


def scenario_no_case(tmp):
    inp = os.path.join(tmp, "in_empty")
    os.makedirs(inp, exist_ok=True)
    out = os.path.join(tmp, "out2")
    work = os.path.join(tmp, "work2")
    r = run(["skeleton", "--input", inp, "--output", out, "--work", work])
    res = load(os.path.join(out, "result.json"))
    check(r.returncode == 0 and res.get("status") == "input_error",
          "空 /input（无 case.json）→ status=input_error")


def scenario_bad_json(tmp):
    inp = os.path.join(tmp, "in_bad")
    os.makedirs(inp, exist_ok=True)
    write(os.path.join(inp, "case.json"), "{ not valid json")
    out = os.path.join(tmp, "out3")
    work = os.path.join(tmp, "work3")
    r = run(["skeleton", "--input", inp, "--output", out, "--work", work])
    res = load(os.path.join(out, "result.json"))
    check(r.returncode == 0 and res.get("status") == "input_error" and res.get("errors"),
          "坏 JSON case.json → input_error 且记录 error")


def scenario_no_material(tmp):
    inp = os.path.join(tmp, "in_nomat")
    os.makedirs(inp, exist_ok=True)
    write(os.path.join(inp, "case.json"), json.dumps({"case_id": "c-nomat",
                                                       "output_contract": {"summary": "str"}}))
    write(os.path.join(inp, "task.md"), "# 任务说明\n仅元信息，无研究材料。\n")
    out = os.path.join(tmp, "out4")
    work = os.path.join(tmp, "work4")
    run(["skeleton", "--input", inp, "--output", out, "--work", work])
    r = run(["evidence", "--input", inp, "--work", work, "--output", out])
    res = load(os.path.join(out, "result.json"))
    check(r.returncode == 0 and res.get("status") == "insufficient_evidence",
          "有 case.json/task.md 但无研究材料 → insufficient_evidence")
    check(res.get("case_id") == "c-nomat" and "summary" in res,
          "case_id 保留且 output_contract 键提升到顶层")


def scenario_normal(tmp):
    inp = os.path.join(tmp, "in_ok")
    os.makedirs(os.path.join(inp, "materials"), exist_ok=True)
    write(os.path.join(inp, "case.json"), json.dumps({
        "case_id": "c-demo",
        "output_contract": {"summary": "str", "key_findings": "list"},
    }))
    write(os.path.join(inp, "task.md"), "# 任务\n评估选题。\n")
    write(os.path.join(inp, "materials", "paper.md"),
          "研究问题：方法 X 相对基线 Y 是否更优。\n方法 X 报告了 A=0.8 与 B=0.7。\n")
    write(os.path.join(inp, "materials", "notes.txt"), "已有工作 W1 使用旧方法。\n")
    out = os.path.join(tmp, "out5")
    work = os.path.join(tmp, "work5")

    run(["skeleton", "--input", inp, "--output", out, "--work", work])
    r = run(["evidence", "--input", inp, "--work", work, "--output", out])
    res = load(os.path.join(out, "result.json"))
    check(r.returncode == 0 and res.get("status") == "partial",
          "正常输入 evidence 后保持 partial（骨架已落盘）")

    # 找到材料源 id，构造 fragment
    mat = [s for s in res.get("sources", []) if not s.get("is_meta")]
    check(bool(mat), "材料被发现并登记到 sources")
    if not mat:
        return
    sid = mat[0]["source_id"]
    frag = {
        "research_question": {
            "testable_statement": "在给定材料内，方法 X 相对基线 Y 的 A 指标差异可测量且可被否定",
            "object": "方法 X", "variables": ["A", "B"], "baseline": "基线 Y",
            "expected_conclusion": "X 的 A 高于 Y", "falsification": "若 A 不高于 Y 则否定",
            "status": "complete", "gaps": []},
        "search_protocol": {
            "query_terms": ["X", "Y"], "inclusion_criteria": ["相关"], "exclusion_criteria": ["无关"],
            "databases": ["本材料集"], "screening_notes": "仅核对 /input 内材料"},
        "prior_work": {"comparison_matrix": [{
            "work": "W1", "task": "同类", "method": "旧方法", "data": "未知",
            "evaluation": "未知", "conclusion": "未与 Y 比较"}],
            "closest_work": "W1", "non_comparable_reasons": ["数据口径未报告"]},
        "novelty": {"claims": [{"claim": "方法 X 与 Y 的直接比较", "type": "结论修正",
                                "verdict": "novel", "covered_by": "", "reason": "W1 未做该比较"}]},
        "feasibility": {"preconditions": [{"dimension": "数据", "statement": "A/B 已报告",
                                            "status": "reported"}],
                        "conditional_conclusion": "前提成立时可行", "failure_risks": ["若数据口径不可比"]},
        "evidence_matrix": [{"source_id": sid, "locator": "materials/paper.md#L2",
                             "quote": "方法 X 报告了 A=0.8 与 B=0.7。", "field": "数据",
                             "claim": "X 报告了 A/B", "claim_type": "fact", "status": "ok"}],
        "conflicts": [], "synthesis": {"conclusions": ["X 相对 Y 的直接比较未见报告"],
                                        "research_gaps": ["缺 W1 数据口径"], "boundary_statement": "仅限本材料集"},
        "uncertainty": {"biases": ["仅两篇材料"], "gaps": ["无更多文献"], "unresolved": []},
    }
    write(os.path.join(work, "fragments", "01_demo.json"), json.dumps(frag, ensure_ascii=False))
    r = run(["finalize", "--input", inp, "--output", out, "--work", work])
    res2 = load(os.path.join(out, "result.json"))
    check(r.returncode == 0 and res2.get("status") == "ok",
          "四项交付+证据+综合齐全 → status=ok")
    check(contract_keys_ok(res2, {"summary": "str", "key_findings": "list"}),
          "output_contract 原样回显且键提升到顶层")
    check(res2.get("case_id") == "c-demo", "case_id 保留")
    ev = res2.get("evidence_matrix") or []
    check(ev and ev[0].get("locator") and ev[0].get("quote"),
          "evidence_matrix 含 locator 与逐字 quote")
    check(os.path.isfile(os.path.join(out, "report.md")), "report.md 已生成")

    # 可复跑：重跑 finalize 结论一致
    r2 = run(["finalize", "--input", inp, "--output", out, "--work", work])
    res3 = load(os.path.join(out, "result.json"))
    same = (res3.get("research_question") == res2.get("research_question")
            and res3.get("novelty") == res2.get("novelty"))
    check(r2.returncode == 0 and same, "重复运行结论一致（可复跑）")


def main():
    print("== selftest: research-selection-assessment ==")
    check(run(["--help"]).returncode == 0, "main.py --help 退出码 0")

    tmp = tempfile.mkdtemp(prefix="selftest-")
    try:
        scenario_missing_input(tmp)
        scenario_no_case(tmp)
        scenario_bad_json(tmp)
        scenario_no_material(tmp)
        scenario_normal(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("== 结果: %d PASS, %d FAIL ==" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
