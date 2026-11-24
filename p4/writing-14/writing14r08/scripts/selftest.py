#!/usr/bin/env python3
"""自检脚本：在临时目录跑 init -> check -> 回填 -> check -> render，验证契约与产物。

仅用于开发自检；示例输入输出均标注为"示例/自测"，不冒充正式案例结果。
退出码 0 表示全部通过。
"""
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(args):
    return subprocess.run(
        [sys.executable, MAIN] + args,
        cwd=HERE,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _fill(out):
    res = json.load(open(out, encoding="utf-8"))
    res["status"] = "complete"
    res["title"] = "自测：某机制研究开题方案（示例，非正式案例）"
    res["summary"] = "示意内容，仅用于脚本自检；不冒充正式案例结果。"
    p = res["proposal"]
    p["background"] = {
        "known": [{"claim": "已知现象 A", "sources": [{"id": "s1", "locator": "demo.md#p1", "quote": "示例证据片段"}]}],
        "unknown": ["机制 B 未明"],
        "gap": {"statement": "缺口 C", "sources": [{"locator": "demo.md#p2", "quote": "示例证据片段2"}]},
    }
    p["research_questions"] = [{"question": "问题 Q1", "falsifiable_criterion": "可证伪判据"}]
    p["objectives"] = [{"objective": "目标 O1", "metrics": ["指标 m1"], "answer_to": "Q1"}]
    p["technical_route"] = [
        {"step_id": "R1", "step": "步骤一", "input": "x", "output": "y", "depends_on": []},
        {"step_id": "R2", "step": "步骤二", "input": "y", "output": "z", "depends_on": ["R1"]},
    ]
    p["phase_plan"] = [
        {"phase": "阶段一", "milestone": "M1-M6 完成 R1", "period": "M1-M6"},
        {"phase": "阶段二", "milestone": "M7-M12 完成 R2", "period": "M7-M12"},
    ]
    p["risks"] = [{
        "category": "数据获取", "risk": "样本不足", "trigger": "样本 < 50",
        "impact": "无法做统计推断", "mitigation": "扩大样本", "decision_point": "M3",
    }]
    p["expected_outcomes"] = [{"outcome": "成果 E", "assessment": "考核方式 A"}]
    res["sources"] = [{"id": "s1", "path": "demo.md", "locator": "#p1", "quote": "示例证据片段"}]
    res["evidence_map"] = [{"claim_id": "c1", "claim": "已知现象 A", "locator": "demo.md#p1", "quote": "示例证据片段", "kind": "fact"}]
    res["pending_human"] = [{"item": "示例：待确认口径", "reason": "自测填充"}]
    res["change_log"].append({"at": "2026-01-01T00:00:00Z", "action": "fill demo", "reason": "示例"})
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def main():
    tmp = tempfile.mkdtemp(prefix="proposal_selftest_")
    case = os.path.join(tmp, "case.json")
    outdir = os.path.join(tmp, "output")
    os.makedirs(outdir, exist_ok=True)
    # result.json 与产物同目录，匹配 Harness 的 /app/output/result.json 布局
    out = os.path.join(outdir, "result.json")
    json.dump(
        {
            "case_id": "selftest-demo",
            "output_contract": {
                "title": {"required": True},
                "summary": {"required": True},
                "proposal": {"required": True},
                "evidence_map": {"required": True},
                "artifacts": {"required": True},
            },
            "constraints": {"period_months": 12},
        },
        open(case, "w", encoding="utf-8"),
        ensure_ascii=False,
    )

    r = run(["init", "--case", case, "--out", out])
    if r.returncode != 0:
        print("FAIL init rc=%s\n%s" % (r.returncode, r.stderr))
        return 1
    res = json.load(open(out, encoding="utf-8"))
    if res.get("case_id") != "selftest-demo" or res.get("status") != "partial":
        print("FAIL init 骨架字段不符")
        return 1

    r = run(["check", "--case", case, "--out", out])
    if r.returncode != 0:
        print("FAIL check-skeleton rc=%s\n%s" % (r.returncode, r.stdout))
        return 1

    _fill(out)

    r = run(["check", "--case", case, "--out", out])
    if r.returncode != 0:
        print("FAIL check-filled rc=%s\n%s" % (r.returncode, r.stdout))
        return 1

    r = run(["render", "--out", out, "--outdir", outdir])
    if r.returncode != 0:
        print("FAIL render rc=%s\n%s" % (r.returncode, r.stderr))
        return 1
    for f in ("proposal.md", "proposal_plan.mmd", "proposal_plan.svg"):
        if not os.path.exists(os.path.join(outdir, f)):
            print("FAIL missing %s" % f)
            return 1
    try:
        ET.parse(os.path.join(outdir, "proposal_plan.svg"))
    except Exception as e:  # noqa: BLE001
        print("FAIL svg 解析失败: %s" % e)
        return 1

    print("PASS selftest: init/check/render 全通过（示例数据，目录 %s）" % tmp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
