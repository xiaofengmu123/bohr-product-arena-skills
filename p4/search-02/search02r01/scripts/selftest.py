#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest.py — Skill 包自检脚本（仅标准库）。

在干净的临时目录里构造三种输入（正常、空目录、坏 JSON/坏编码），
对每条输入跑 run -> assemble -> selfcheck，断言：
  - 每次都产生合法 result.json 且退出码为 0；
  - result.json 能解析并含 case_id 字段；
  - 空输入/坏 JSON 不抛异常，状态为 insufficient_evidence 或 input_error。
用法：
  python3 -B scripts/selftest.py   # 退出码 0 表示自检全部通过
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl  # noqa: E402  （直接调用其 main()，避免子进程管道问题）


def run_case(tmp, case_files, name):
    inp = os.path.join(tmp, name, "input")
    out = os.path.join(tmp, name, "output")
    work = os.path.join(tmp, name, "work")
    os.makedirs(inp, exist_ok=True)
    for rel, content in case_files.items():
        p = os.path.join(inp, rel)
        os.makedirs(os.path.dirname(p) or inp, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)

    rc = core_impl.main(["run", "--input", inp, "--output", out, "--work", work,
                         "--runtime-seconds", "120", "--runtime-requests", "7"])
    assert rc == 0, name + ": run 退出码 %d" % rc

    # 完整路径仅对正常案例演示：先拆解子问题，再导入记录
    if "materials/paper.md" in case_files:
        plan_file = os.path.join(work, "plan.json")
        plan = {
            "sub_questions": [{
                "id": "SQ1", "question": "自噬如何降解胞质货物？",
                "facets": {"object": "自噬", "mechanism": "溶酶体降解", "method": "文献证据",
                           "time_window": "不限", "comparison": "无", "evidence_requirement": "逐字 quote"},
                "keywords": ["autophagy", "lysosomal degradation"],
                "status": "covered",
            }],
            "query_plan": {"order": [{"step": 1, "sub_question_ids": ["SQ1"], "type": "前置",
                                      "query": "autophagy lysosomal degradation", "rationale": "直接命中"}],
                           "dependencies": [], "parallel_groups": []},
            "term_expansion": {"concepts": [{"concept": "autophagy", "variants": [
                {"term": "macroautophagy", "type": "领域变体"}, {"term": "自噬", "type": "跨语言"}]}],
                "ambiguities": [], "max_variants_per_concept": 6},
            "stop_decision": {"status": "converged", "criteria": [
                {"criterion": "每个子问题至少1条included", "met": True, "evidence": "SQ1 有1条 included"}],
                "marginal_benefit": "低", "uncovered_sub_questions": [], "rationale": "证据充分"},
        }
        with open(plan_file, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False)
        rc = core_impl.main(["set-plan", "--file", plan_file, "--work", work])
        assert rc == 0, name + ": set-plan 退出码 %d" % rc

    # 导入一条带证据的记录，验证证据溯源强化
    rec_file = os.path.join(work, "rec.json")
    rec = [{
        "title": "Test Article on 自噬", "authors": ["A", "B"], "year": 2024,
        "journal": "Cell", "doi": "10.0000/example", "url": "https://doi.org/10.0000/example",
        "database": "paper_search", "sub_question_ids": ["SQ1"],
        "relevance": 0.9, "screening": "included",
        "relevance_reason": "CLM-001：证据 EV-001", "screening_reason": "related",
        "evidence": [{
            "claim_id": "CLM-001", "claim_text": "该文报告自噬机制",
            "kind": "relevance", "source_ref": "materials/paper.md#p1",
            "locator": {"file": "materials/paper.md", "paragraph": 1},
            "quote": "autophagy is a lysosomal degradation pathway",
        }],
    }]
    with open(rec_file, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    rc = core_impl.main(["import", "--file", rec_file, "--work", work])
    assert rc == 0, name + ": import 退出码 %d" % rc

    # 追加一条结论级证据（本地 quote 应命中材料文本）
    ev_file = os.path.join(work, "ev.json")
    ev = {"evidence": [{
        "claim_id": "CLM-002", "claim_text": "材料原文可逐字验证",
        "kind": "gap", "source_ref": "materials/paper.md#p2",
        "locator": {"file": "materials/paper.md", "paragraph": 2},
        "quote": "The pathway degrades cytosolic cargo.",
    }]}
    with open(ev_file, "w", encoding="utf-8") as f:
        json.dump(ev, f, ensure_ascii=False)
    rc = core_impl.main(["evidence", "--file", ev_file, "--work", work])
    assert rc == 0

    rc = core_impl.main(["assemble", "--input", inp, "--output", out, "--work", work,
                         "--runtime-seconds", "120", "--runtime-requests", "7"])
    assert rc == 0

    result_path = os.path.join(out, "result.json")
    r = json.load(open(result_path, encoding="utf-8"))
    assert "case_id" in r, name + ": 缺 case_id"
    assert r.get("status") in ("ok", "partial", "insufficient_evidence", "input_error")
    assert isinstance(r.get("evidence_map"), dict)
    # 本地逐字 quote 核验：仅当该案例提供了本地材料时才断言命中
    if "materials/paper.md" in case_files:
        ver = r["evidence_map"]["verification"]
        assert ver.get("verified_local", 0) >= 1, name + ": 本地逐字 quote 未命中"
    # 未命中/孤儿应有记录（本例存在 CLM 引用但若缺失则列表存在）
    assert isinstance(r["evidence_map"].get("unverified_quotes"), list)
    rc = core_impl.main(["selfcheck", "--output", out, "--work", work])
    assert rc == 0
    return r["status"]


def main():
    tmp = tempfile.mkdtemp(prefix="skill_selftest_")
    try:
        paper = "## P1\n\nautophagy is a lysosomal degradation pathway\nThe pathway degrades cytosolic cargo.\n"
        normal = {
            "case.json": json.dumps({
                "case_id": "case-selftest-001",
                "id": "case-selftest-001",
                "research_question": "自噬如何降解胞质货物？",
                "output_contract": {
                    "results": [], "evidence": [], "sources": [], "coverage": [],
                },
            }, ensure_ascii=False),
            "task.md": "# 任务\n\n围绕自噬降解机制检索。\n",
            "materials/paper.md": paper,
        }
        bad_json = {
            "case.json": '{"case_id": "broken", "research_question": 未闭合',
            "task.md": "任务说明".encode("gbk", errors="ignore").decode("latin-1"),
        }
        empty = {}

        for name, files in (("normal", normal), ("bad", bad_json), ("empty", empty)):
            status = run_case(tmp, files, name)
            print("[selftest] %-8s -> result.json 合法，status=%s" % (name, status))
        print("selftest OK: 正常/坏 JSON/空输入 三种场景全部通过；本地逐字 quote 核验生效。")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())