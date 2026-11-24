#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检脚本：在临时目录内验证 Skill 工具链的健壮性与确定性。

覆盖：空输入目录、坏 JSON、正常 init->validate->render->merge、产物存在性、
重复运行一致性。全部用临时目录，不污染正式产物；退出码 0 表示全部通过。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
sys.path.insert(0, HERE)
import core_impl as core  # noqa: E402


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, MAIN] + args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def make_case(input_dir: str, case_id: str = "demo", contract: dict | None = None):
    case = {
        "case_id": case_id,
        "output_contract": contract or {
            "title": {"required": True},
            "background": {"required": True},
            "research_questions": {"required": True},
            "objectives": {"required": True},
            "technical_route": {"required": True},
            "schedule": {"required": True},
            "risks": {"required": True},
            "expected_outcomes": {"required": True},
            "citations": {"required": True},
            "artifacts": {"required": True},
        },
    }
    with open(os.path.join(input_dir, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False)


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" :: {detail}" if detail else ""))
    if not cond:
        raise SystemExit(1)


def main() -> int:
    root = tempfile.mkdtemp(prefix="proposal_selftest_")
    passed = []
    try:
        # 1. 空输入目录：init 应落盘 insufficient_evidence 骨架
        empty_in = os.path.join(root, "empty_in")
        empty_out = os.path.join(root, "empty_out")
        os.makedirs(empty_in)
        r = run(["init", "--input", empty_in, "--out", empty_out, "--work", os.path.join(root, "w1")])
        check("空输入 init 退出码 0", r.returncode == 0, f"rc={r.returncode}")
        skel = json.load(open(os.path.join(empty_out, "result.json"), encoding="utf-8"))
        check("空输入骨架 status=insufficient_evidence", skel.get("status") == "insufficient_evidence")
        check("空输入骨架含 case_id", bool(skel.get("case_id")))

        # 2. 坏 JSON：init 应降级不崩溃
        bad_in = os.path.join(root, "bad_in")
        bad_out = os.path.join(root, "bad_out")
        os.makedirs(bad_in)
        with open(os.path.join(bad_in, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ not valid json ")
        r = run(["init", "--input", bad_in, "--out", bad_out, "--work", os.path.join(root, "w2")])
        check("坏 JSON init 退出码 0", r.returncode == 0, f"rc={r.returncode}")
        bad = json.load(open(os.path.join(bad_out, "result.json"), encoding="utf-8"))
        check("坏 JSON 骨架可解析", isinstance(bad, dict))
        check("坏 JSON 记录 degradations", bool(bad.get("degradations")))

        # 3. 正常流程 init -> validate -> render -> merge
        in_dir = os.path.join(root, "in")
        out_dir = os.path.join(root, "out")
        os.makedirs(in_dir)
        make_case(in_dir)
        r = run(["init", "--input", in_dir, "--out", out_dir, "--work", os.path.join(root, "w3")])
        check("正常 init 退出码 0", r.returncode == 0, f"rc={r.returncode}")
        skel = json.load(open(os.path.join(out_dir, "result.json"), encoding="utf-8"))
        check("骨架含七要素占位", all(k in skel for k in core.SEVEN_ELEMENTS))

        # 回填一份最小但完整的 demo（仅自检用，非正式案例）
        skel["title"] = "自检用最小示例（非正式案例）"
        skel["background"] = {"known": ["已知结论 A"], "unknown": ["未知缺口 B"], "to_verify": ["待验证 C"], "sources": ["S1"]}
        skel["research_questions"] = [{"id": "RQ1", "question": "Q", "falsifiable": "判据", "sources": ["S1"]}]
        skel["objectives"] = [{"id": "O1", "question_ref": "RQ1", "goal": "G", "metric": "M", "target": "T", "judgment": "J", "sources": ["S1"]}]
        skel["technical_route"] = [{"id": "S1", "step": "步骤一", "depends_on": [], "sources": ["S1"]}]
        skel["schedule"] = [{"id": "M1", "milestone": "里程碑一", "month": 3, "sources": ["S1"]}]
        skel["risks"] = [{"id": "R1", "category": "method_feasibility", "risk": "风险", "trigger": "信号", "impact": "影响", "mitigation": "备选", "decision_point": "时点", "sources": ["S1"]}]
        skel["expected_outcomes"] = [{"id": "E1", "objective_ref": "O1", "outcome": "成果", "assessment": "考核", "sources": ["S1"]}]
        skel["citations"] = [{"claim": "论断", "citation": "引用", "sources": ["S1"]}]
        skel["sources"] = [{"id": "S1", "locator": "materials/demo.pdf#p1", "quote": "逐字引用片段", "kind": "fact"}]
        skel["alignment"] = {"matrix": [{"question": "RQ1", "objective": "O1", "route_step": "S1", "outcome": "E1"}]}
        skel["contract_fields"] = core.extract_contract({"output_contract": {}})["fields"]
        core.dump_json(skel, os.path.join(out_dir, "result.json"))

        r = run(["validate", "--input", in_dir, "--out", out_dir, "--work", os.path.join(root, "w4")])
        check("validate 退出码 0（无 FAIL）", r.returncode == 0, r.stdout[:200])

        r = run(["merge", "--input", in_dir, "--out", out_dir, "--work", os.path.join(root, "w5")])
        check("merge 退出码 0", r.returncode == 0, f"rc={r.returncode}")
        final = json.load(open(os.path.join(out_dir, "result.json"), encoding="utf-8"))
        for f in ("proposal.md", "evidence_map.md", "alignment.md"):
            check(f"产物存在 {f}", os.path.isfile(os.path.join(out_dir, f)))
        check("信封字段齐全", all(k in final for k in ("case_id", "status", "sources", "uncertainty", "pending_human", "change_log")))

        # 4. 重复运行确定性：render 两次产物一致
        run(["render", "--input", in_dir, "--out", out_dir, "--work", os.path.join(root, "w6")])
        md1 = open(os.path.join(out_dir, "proposal.md"), encoding="utf-8").read()
        run(["render", "--input", in_dir, "--out", out_dir, "--work", os.path.join(root, "w7")])
        md2 = open(os.path.join(out_dir, "proposal.md"), encoding="utf-8").read()
        check("重复 render 结果一致", md1 == md2)

        print(f"\n自检通过：{root}")
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
