#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py: 自检脚本。只依赖标准库，在临时目录构造合成案例，验证
init / update / check / render 四步链路，以及空输入、坏 JSON、重复运行等
异常路径。任何断言失败打印 FAIL 并以非零退出码返回。合成案例仅用于自检，
不是正式案例答案。
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl  # noqa: E402


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def make_case(tmp):
    case = {
        "case_id": "selftest-demo",
        "task": "开题报告",
        "output_contract": {
            "title": {"required": True},
            "summary": {"required": True},
            "proposal": {"required": True, "type": "object"},
            "evidence_map": {"required": True, "type": "array"},
            "sources": {"required": True, "type": "array"},
            "artifacts": {"required": True, "type": "array"},
            "custom_flag": {"required": True},
        },
    }
    p = os.path.join(tmp, "case.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(case, f, ensure_ascii=False)
    return p


def make_fragment(tmp):
    frag = {
        "status": "partial",
        "title": "示例：某课题开题方案",
        "summary": "示意内容，仅用于自检，非正式案例。",
        "audience": "导师与评审",
        "genre": "开题报告",
        "format_constraints": ["markdown", "docx"],
        "constraints": {"period": "24 个月", "budget": "50 万元", "personnel": "2 人", "data": "公开数据集"},
        "proposal": {
            "background_gap": {
                "known": [{"id": "BG1", "text": "已知结论 A", "kind": "fact", "sources": ["S1"]}],
                "unknown": [{"id": "BG2", "text": "未知缺口 B", "kind": "fact", "sources": ["S1"]}],
                "to_verify": [{"id": "BG3", "text": "待验证 C", "kind": "inference", "sources": []}],
                "gap_statement": {"text": "缺口陈述 D", "kind": "summary", "sources": ["S1"]},
            },
            "research_questions": [
                {"id": "RQ1", "question": "可证伪问题一", "falsifiable_criterion": "判定标准", "kind": "fact", "sources": ["S1"]}
            ],
            "objectives": [
                {"id": "OBJ1", "objective": "目标一", "metric": "指标", "threshold": "阈值",
                 "sample": "样本", "control": "对照", "timepoint": "时间点",
                 "verification": "考核方式", "linked_question": "RQ1", "kind": "summary", "sources": ["S1"]}
            ],
            "technical_route": [
                {"id": "ST1", "name": "步骤一", "input": "输入", "output": "输出",
                 "depends_on": [], "parallel_with": ["ST2"], "kind": "fact", "sources": ["S1"]},
                {"id": "ST2", "name": "步骤二", "input": "输入", "output": "输出",
                 "depends_on": [], "kind": "fact", "sources": ["S1"]},
            ],
            "phase_plan": [
                {"id": "P1", "phase": "Q1", "duration": "3 个月", "milestones": ["M1"],
                 "key_path": True, "resources": "1 人", "linked_steps": ["ST1"]}
            ],
            "risks": [
                {"id": "R1", "category": "data", "risk": "数据缺失", "trigger_signal": "信号",
                 "impact": "影响", "fallback": "备选", "decision_point": "时点", "kind": "inference", "sources": []}
            ],
            "expected_outcomes": [
                {"id": "EO1", "outcome": "成果一", "verification_method": "考核方式", "linked_objective": "OBJ1"}
            ],
        },
        "sources": [
            {"id": "S1", "locator": "materials/paper.md#p3", "quote": "原文片段", "kind": "fact", "title": "示例文献"}
        ],
        "uncertainty": [
            {"item": "BG3", "issue": "由材料推断，未经原文直接支持", "kind": "inference", "action": "人工确认"},
            {"item": "R1", "issue": "风险为推断项", "kind": "inference", "action": "人工确认"},
        ],
        "pending_human": [{"item": "custom_flag", "issue": "契约未知字段待人工定义"}],
        "limitations": ["示例：未生成 docx 预览（运行时 office-renderer 未调用）"],
    }
    p = os.path.join(tmp, "fragment.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(frag, f, ensure_ascii=False)
    return p


def main():
    tmp = tempfile.mkdtemp(prefix="weekly_selftest_")
    failures = []
    try:
        case = make_case(tmp)
        out = os.path.join(tmp, "out", "result.json")
        frag = make_fragment(tmp)

        # 1. init
        rc = core_impl.cmd_init(case, out)
        if rc != 0 or not os.path.exists(out):
            failures.append("init 失败")
        first_hash = sha(out) if os.path.exists(out) else None

        # 2. 重复 init 幂等（内容一致）
        core_impl.cmd_init(case, out)
        if sha(out) != first_hash:
            failures.append("init 幂等性破坏")

        # 3. update 增量合并
        rc = core_impl.cmd_update(out, frag, "自检：回填七要素")
        if rc != 0:
            failures.append("update 失败")
        r = json.load(open(out, encoding="utf-8"))
        if r.get("status") != "partial":
            failures.append("update 未回填 status")
        if r.get("case_id") != "selftest-demo":
            failures.append("case_id 被误改")
        if "custom_flag" not in r:
            failures.append("output_contract 未知字段未保留")

        # 4. check 应 FAIL=0
        core_impl.cmd_check(case, out)
        # 再补 render，产物齐全后 check 不应因 artifacts 报错
        rc = core_impl.cmd_render(out, os.path.dirname(out))
        if rc != 0:
            failures.append("render 失败")
        for art in ("proposal.md", "evidence_map.json", "proposal_preview.html"):
            if not os.path.exists(os.path.join(os.path.dirname(out), art)):
                failures.append("产物缺失: {}".format(art))
        if core_impl.cmd_check(case, out) != 0:
            failures.append("完整结果 check 仍 FAIL")

        # 5. 空输入目录：无 case.json → input_error 骨架，退出码 0，不抛异常
        empty_dir = os.path.join(tmp, "empty")
        os.makedirs(empty_dir, exist_ok=True)
        out2 = os.path.join(empty_dir, "result.json")
        rc = core_impl.cmd_init(os.path.join(empty_dir, "nope.json"), out2)
        if rc != 0 or not os.path.exists(out2):
            failures.append("空输入未落盘骨架")
        r2 = json.load(open(out2, encoding="utf-8"))
        if r2.get("status") != "input_error":
            failures.append("空输入 status 非 input_error")

        # 6. 坏 JSON：不崩溃，记录 error
        bad = os.path.join(tmp, "bad.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("{ not json ")
        out3 = os.path.join(tmp, "out3.json")
        rc = core_impl.cmd_init(bad, out3)
        if rc != 0 or not os.path.exists(out3):
            failures.append("坏 JSON 未降级")

        # 7. 重复 render 幂等（markdown 内容一致）
        md1 = os.path.join(os.path.dirname(out), "proposal.md")
        h1 = sha(md1)
        core_impl.cmd_render(out, os.path.dirname(out))
        if sha(md1) != h1:
            failures.append("render 幂等性破坏")

    except Exception as e:  # noqa: BLE001
        failures.append("异常: {!r}".format(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        for f in failures:
            print("FAIL", f)
        print("selftest: FAIL ({} issues)".format(len(failures)))
        return 1
    print("selftest: PASS (init/update/check/render + 空输入/坏JSON/幂等)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
