#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest：用合成最小输入验证 init / check / render 的健壮性与确定性。

注意：本脚本只用合成用例自检脚本本身，不产出、也不冒充正式案例结果。
进程内调用（不 spawn 子进程），避免命名管道限制；重复运行结果一致。
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import core_impl as core
import proposal


def call_main(argv):
    old = sys.argv
    sys.argv = argv
    try:
        return proposal.main(argv[1:])
    finally:
        sys.argv = old


def main():
    ok = True
    tmp = tempfile.mkdtemp(prefix="proposal_selftest_")
    try:
        in_dir = os.path.join(tmp, "input")
        out_dir = os.path.join(tmp, "output")
        os.makedirs(in_dir)
        os.makedirs(out_dir)
        case_path = os.path.join(in_dir, "case.json")
        result_path = os.path.join(out_dir, "result.json")

        # 1. 缺 case.json：init 仍落盘合法 partial 骨架，不崩溃
        rc = call_main(["proposal.py", "init", "--case", case_path, "--result", result_path])
        ok &= (rc == 0)
        sk = json.load(open(result_path, encoding="utf-8"))
        ok &= (sk.get("status") == "partial" and isinstance(sk.get("change_log"), list) and bool(sk["change_log"]))

        # 2. 坏 JSON：init 不崩溃，报 WARN（stderr）
        with open(case_path, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        rc = call_main(["proposal.py", "init", "--case", case_path, "--result", result_path])
        ok &= (rc == 0)

        # 3. 契约字段原样回填 + check 检测缺口
        with open(case_path, "w", encoding="utf-8") as f:
            json.dump({"case_id": "selftest-1",
                       "output_contract": {"title": {"required": True}, "custom_field": {"required": True}}},
                      f, ensure_ascii=False)
        rc = call_main(["proposal.py", "init", "--case", case_path, "--result", result_path])
        ok &= (rc == 0)
        sk = json.load(open(result_path, encoding="utf-8"))
        ok &= ("custom_field" in sk and "title" in sk and sk["case_id"] == "selftest-1")
        rc = call_main(["proposal.py", "check", "--case", case_path, "--result", result_path])
        ok &= (rc == 1)  # 契约字段未回填，应有 FAIL

        # 4. 回填后 check 通过 + render 产出真实文件 + 确定性
        sk["title"] = "自检示例"
        sk["custom_field"] = "filled"
        sk["summary"] = "合成自检用例，非正式案例结果"
        sk["seven_elements"]["research_questions"].append(
            {"id": "RQ1", "text": "示例问题", "falsifiable": "可证伪表述", "metrics": ["指标"],
             "sample": None, "control": None, "timepoint": None, "evidence": ["ev1"]})
        sk["seven_elements"]["objectives"].append(
            {"id": "O1", "question_ref": "RQ1", "text": "示例目标", "criteria": ["标准"], "evidence": []})
        sk["seven_elements"]["technical_route"].append(
            {"id": "TR1", "step": "示例步骤", "input": "i", "output": "o", "depends_on": [], "objective_ref": "O1", "evidence": []})
        sk["seven_elements"]["schedule"].append(
            {"id": "M1", "phase": "示例阶段", "month_start": 1, "month_end": 3, "milestone": "m", "depends_on": [], "evidence": []})
        sk["seven_elements"]["risks"].append(
            {"id": "R1", "category": "数据获取", "description": "d", "trigger": "t", "impact": "i",
             "mitigation": "m", "decision_point": "p", "evidence": []})
        sk["seven_elements"]["expected_outcomes"].append(
            {"id": "E1", "objective_ref": "O1", "outcome": "o", "assessment": "a", "evidence": []})
        sk["evidence_map"].append(
            {"id": "ev1", "locator": "materials/note.md#p1", "quote": "逐字引用示例", "claim_refs": ["RQ1"]})
        sk["sources"].append({"id": "src1", "path": "materials/note.md", "kind": "markdown"})
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(sk, f, ensure_ascii=False, indent=2)
        rc = call_main(["proposal.py", "render", "--result", result_path, "--out", out_dir])
        ok &= (rc == 0)
        rc = call_main(["proposal.py", "check", "--case", case_path, "--result", result_path])
        ok &= (rc == 0)
        for fn in ("proposal.md", "evidence_map.md", "schedule.mmd", "preview.svg"):
            ok &= os.path.exists(os.path.join(out_dir, fn))
        import xml.etree.ElementTree as ET
        try:
            ET.parse(os.path.join(out_dir, "preview.svg"))
        except Exception:
            ok = False
        with open(os.path.join(out_dir, "proposal.md"), encoding="utf-8") as f:
            first = f.read()
        call_main(["proposal.py", "render", "--result", result_path, "--out", out_dir])
        with open(os.path.join(out_dir, "proposal.md"), encoding="utf-8") as f:
            second = f.read()
        ok &= (first == second)  # 确定性

        # 5. 要素对齐错误能被 check 捕获
        sk["seven_elements"]["objectives"][0]["question_ref"] = "RQ_MISSING"
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(sk, f, ensure_ascii=False, indent=2)
        rc = call_main(["proposal.py", "check", "--case", case_path, "--result", result_path])
        ok &= (rc == 1)  # 对齐错误应为 FAIL
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("SELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
