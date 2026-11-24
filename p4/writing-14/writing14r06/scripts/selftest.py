#!/usr/bin/env python3
"""自检脚本：空输入 / 坏 JSON / 契约回填 / 渲染产物，独立验证 Skill 的健壮性。

运行（在 Skill 根目录）：
  python scripts/selftest.py

仅依赖 Python 标准库；不触碰真实案例，不编造评测结果。
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl as core


def _mkdir(*parts):
    d = os.path.join(*parts)
    os.makedirs(d, exist_ok=True)
    return d


def run():
    base = tempfile.mkdtemp(prefix="proposal_selftest_")
    checks = []
    try:
        # 1) 空输入：case.json 不存在 -> 应写合法 partial 结果，不崩溃
        d1 = base
        i1, o1, w1 = _mkdir(d1, "empty", "input"), _mkdir(d1, "empty", "out"), _mkdir(d1, "empty", "work")
        info1 = core.discover_case(os.path.join(i1, "case.json"))
        assert info1.get("error") and info1.get("case_id") is None, "空输入应报错且 case_id 为空"
        core.init(os.path.join(i1, "case.json"), o1, w1, info1)
        assert os.path.exists(os.path.join(o1, "result.json")), "init 应写 result.json"
        res1 = core.read_json(os.path.join(o1, "result.json"))
        assert res1 and res1.get("status") == "partial", "空输入应为 partial"
        f1, _ = core.check(os.path.join(i1, "case.json"), o1, info1)
        r1 = core.render(os.path.join(i1, "case.json"), o1, info1)
        for a in r1.get("artifacts", []):
            assert os.path.exists(os.path.join(o1, a)), "render 产物缺失: %s" % a
        checks.append(True)

        # 2) 坏 JSON（截断/尾逗号）-> 不崩溃，lenient 降级后仍可 init
        d2 = base
        i2, o2 = _mkdir(d2, "bad", "input"), _mkdir(d2, "bad", "out")
        with open(os.path.join(i2, "case.json"), "w", encoding="utf-8") as f:
            f.write('{"case_id": "bad", "output_contract": {"title": {"required": true},}')
        info2 = core.discover_case(os.path.join(i2, "case.json"))
        core.init(os.path.join(i2, "case.json"), o2, _mkdir(d2, "bad", "work"), info2)
        assert os.path.exists(os.path.join(o2, "result.json")), "坏 JSON 不应崩溃"
        checks.append(True)

        # 3) 契约回填 + 有效七要素/证据 -> check FAIL=0，render 产出 proposal.md 与 overview.svg
        d3 = base
        i3, o3, w3 = _mkdir(d3, "valid", "input"), _mkdir(d3, "valid", "out"), _mkdir(d3, "valid", "work")
        case = {"case_id": "case-1",
                "output_contract": {"title": {"required": True},
                                    "proposal": {"required": True},
                                    "evidence_map": {"required": True}}}
        with open(os.path.join(i3, "case.json"), "w", encoding="utf-8") as f:
            json.dump(case, f, ensure_ascii=False)
        info3 = core.discover_case(os.path.join(i3, "case.json"))
        core.init(os.path.join(i3, "case.json"), o3, w3, info3)
        rp = os.path.join(o3, "result.json")
        res3 = core.read_json(rp)
        res3["status"] = "complete"
        res3["title"] = "样例：某方向开题方案"
        res3["summary"] = "自检合成案例，仅验证流水线，非正式案例结果"
        res3["proposal"]["background"]["gap_statement"] = "已知 A，未知 B，需验证 C"
        res3["proposal"]["background"]["known"].append("A 成立")
        res3["proposal"]["problem_definition"]["questions"].append(
            {"id": "Q1", "question": "B 是否由 C 解释", "falsifiable_test": "若 C 成立则 B 可预测",
             "gap_evidence": ["S1"], "assumptions": []})
        res3["proposal"]["objectives"].append(
            {"id": "O1", "question_ref": "Q1", "objective": "验证 C 与 B 的关系",
             "success_judgement": "阈值有据", "criteria": {"metrics": "指标", "sample": "样本", "control": "对照", "timepoint": "时点"}})
        res3["proposal"]["technical_route"]["steps"].append(
            {"id": "S1", "name": "采集", "input": "数据", "output": "清洗后数据", "method": "标准流程",
             "depends_on": [], "parallelizable": False})
        res3["proposal"]["technical_route"]["critical_path"] = ["S1"]
        res3["proposal"]["schedule"]["cycle"] = "2026-01 至 2026-12"
        res3["proposal"]["schedule"]["milestones"].append(
            {"id": "M1", "month": "1", "deliverable": "数据就绪", "workload": "2人月", "depends_on": []})
        res3["proposal"]["risks"].append(
            {"id": "R1", "category": "数据获取", "description": "数据不可得", "trigger_signal": "缺失率>阈值",
             "impact": "进度受阻", "mitigation": "备选数据", "decision_point": "第2月"})
        res3["proposal"]["expected_outcomes"].append(
            {"id": "E1", "question_ref": "Q1", "outcome": "结论", "assessment_method": "统计检验", "target": "p<0.05"})
        res3["sources"].append({"id": "S1", "locator": "materials/x.md#p1", "quote": "原文片段", "path": "materials/x.md"})
        res3["evidence_map"].append(
            {"claim_id": "C1", "claim": "已知 A", "source_id": "S1", "locator": "materials/x.md#p1", "quote": "原文片段"})
        core.write_json(rp, res3)
        core.write_json(os.path.join(o3, "plan.json"),
                        {"case_id": "case-1", "constraints": {}, "proposal": res3["proposal"]})
        core.write_json(os.path.join(o3, "evidence_map.json"),
                        {"case_id": "case-1", "entries": res3["evidence_map"]})
        f3, w3list = core.check(os.path.join(i3, "case.json"), o3, info3)
        assert not f3, "有效案例应 FAIL=0，实际: %s" % f3
        rr = core.render(os.path.join(i3, "case.json"), o3, info3)
        assert os.path.exists(os.path.join(o3, "proposal.md")), "render 应写 proposal.md"
        assert os.path.exists(os.path.join(o3, "overview.svg")), "render 应写 overview.svg"
        assert os.path.exists(os.path.join(o3, "route.mmd")), "render 应写 route.mmd"
        assert os.path.exists(os.path.join(o3, "schedule.mmd")), "render 应写 schedule.mmd"
        # 幂等：再 render 一次不应重复追加 render 日志
        n_before = len(core.read_json(rp).get("change_log") or [])
        core.render(os.path.join(i3, "case.json"), o3, info3)
        n_after = len(core.read_json(rp).get("change_log") or [])
        assert n_before == n_after, "render 应幂等（不重复追加 change_log）"
        checks.append(True)

        print("selftest PASS %d/3" % len(checks))
        return True
    except AssertionError as e:
        print("selftest FAIL:", e)
        return False
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
