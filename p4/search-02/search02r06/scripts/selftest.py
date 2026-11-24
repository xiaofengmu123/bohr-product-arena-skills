#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地自检脚本（纯标准库，可与评测环境等价运行）。

目的：在交付前用最小演示案例把主路径、兜底场景、幂等性各跑一遍，
把“本地能跑、评测必挂”的问题挡在提交前。脚本会自行创建临时目录并在
结束后清理，不污染包目录；退出码 0 表示全部通过，1 表示存在失败。

运行：python3 -B scripts/selftest.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl as C  # noqa: E402


def _run(argv):
    return C.main(argv)


def _demo_case(contract=None):
    if contract is None:
        contract = {
            "research_question": "string",
            "sub_questions": "list",
            "query_plan": "list",
            "term_expansion": "list",
            "stop_decision": "object",
            "results": "list",
            "evidence": "list",
            "status_notes": "list",
        }
    return {
        "case_id": "demo-search-001",
        "task_instruction": "围绕固定科研问题检索并筛选文献，保留筛选依据。",
        "research_question": "CRISPR/Cas9 对镰状细胞病的基因治疗机制与临床试验证据",
        "output_contract": contract,
        "constraints": {"max_results": 5},
        "papers": [
            {
                "record_id": "P1",
                "title": "CRISPR-Cas9 Gene Editing for Sickle Cell Disease and beta-Thalassemia",
                "authors": ["Frangoul H", "Altshuler D"],
                "year": 2021,
                "journal": "New England Journal of Medicine",
                "doi": "10.1056/NEJMoa2031054",
                "database": "PubMed",
                "relevance": 0.98,
                "relevance_reason": "直接报告 BCL11A 增强子编辑临床试验终点",
                "screening": "include",
                "screening_reason": "直接回答机制与临床证据两个子问题",
                "sub_question_ids": ["SQ1", "SQ2"],
                "evidence": [{"locator": "abstract#para1", "quote": "sickle cell disease"}],
            },
            {
                "record_id": "P2",
                "title": "Gene therapy for beta-thalassemia",
                "authors": ["Thompson AA"],
                "year": 2018,
                "journal": "New England Journal of Medicine",
                "doi": "10.1056/NEJMra1705342",
                "database": "PubMed",
                "relevance": 0.5,
                "relevance_reason": "相关但未聚焦 Cas9 编辑机制",
                "screening": "exclude",
                "screening_reason": "对象为 β 地中海贫血且非编辑机制研究",
                "sub_question_ids": [],
                "evidence": [{"locator": "abstract#para1", "quote": "beta-thalassemia"}],
            },
        ],
    }


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_help():
    code = _run(["--help"])
    _assert(code == 0, "--help 应返回 0")
    return "help"

def test_empty_input():
    root = tempfile.mkdtemp(prefix="selftest-empty-")
    try:
        inp = os.path.join(root, "input")
        work = os.path.join(root, "work")
        out = os.path.join(root, "output")
        os.makedirs(inp)
        code = _run(["run", "--input", inp, "--work", work, "--output", out])
        _assert(code == 0, "空输入 run 应返回 0")
        res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        _assert(res.get("case_id") == "unknown", "空输入 case_id 应为 unknown")
        _assert(res.get("status") == "insufficient_evidence", "空输入应 insufficient_evidence")
        _assert("sources" in res and "uncertainty" in res and "change_log" in res,
                "空输入 result.json 应含信封字段")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return "empty-input"

def test_bad_json():
    root = tempfile.mkdtemp(prefix="selftest-badjson-")
    try:
        inp = os.path.join(root, "input")
        work = os.path.join(root, "work")
        out = os.path.join(root, "output")
        os.makedirs(inp)
        _write(os.path.join(inp, "case.json"), "{ broken json")
        _write(os.path.join(inp, "task.md"), "# 演示任务\n")
        code = _run(["run", "--input", inp, "--work", work, "--output", out])
        _assert(code == 0, "坏 JSON run 应返回 0")
        res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        _assert(res.get("status") in ("input_error", "partial", "insufficient_evidence"),
                "坏 JSON 应有合法 status")
        _assert(any("bad_json" in e for e in (res.get("errors") or []) + (res.get("degradations") or [])),
                "坏 JSON 应记录到 errors/degradations")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return "bad-json"

def test_full_pipeline():
    root = tempfile.mkdtemp(prefix="selftest-full-")
    try:
        inp = os.path.join(root, "input")
        work = os.path.join(root, "work")
        out = os.path.join(root, "output")
        os.makedirs(inp)
        _write(os.path.join(inp, "case.json"), json.dumps(_demo_case(), ensure_ascii=False))
        _write(os.path.join(inp, "task.md"), "# 演示任务\n研究问题：CRISPR/Cas9 对镰状细胞病的基因治疗机制与临床试验证据\n")

        code = _run(["run", "--input", inp, "--work", work, "--output", out])
        _assert(code == 0, "run 应返回 0")

        plan_in = {
            "sub_questions": [
                {"id": "SQ1", "facet": "mechanism", "text": "Cas9 编辑的分子机制",
                 "key_concepts": ["CRISPR", "Cas9"], "required_evidence": "机制研究"},
                {"id": "SQ2", "facet": "evidence_req", "text": "临床试验终点",
                 "key_concepts": ["clinical trial"], "required_evidence": "RCT/队列"},
            ],
            "term_expansion": [
                {"concept": "CRISPR", "variants": [{"term": "clustered regularly interspaced short palindromic repeats", "type": "full-form"}], "rationale": "缩写展开"},
            ],
            "query_plan": [
                {"step": 1, "purpose": "机制检索", "query_template": "CRISPR Cas9 sickle cell mechanism", "depends_on": [], "parallel_group": "A"},
            ],
            "stop_decision": {"status": "sufficient", "reason": "两个子问题均有一级证据", "subquestion_coverage": {"SQ1": 1, "SQ2": 1}, "idle_rounds": 0},
        }
        plan_path = os.path.join(work, "plan_in.json")
        _write(plan_path, json.dumps(plan_in, ensure_ascii=False))
        code = _run(["set-plan", "--file", plan_path, "--work", work])
        _assert(code == 0, "set-plan 应返回 0")

        ev_path = os.path.join(work, "evidence_in.json")
        _write(ev_path, json.dumps([
            {"kind": "query", "round": 1, "query": "CRISPR Cas9 sickle cell", "source": "paper_search", "database": "PubMed", "rationale": "SQ1/SQ2 前置查询"},
        ], ensure_ascii=False))
        code = _run(["evidence", "--file", ev_path, "--work", work])
        _assert(code == 0, "evidence 应返回 0")

        code = _run(["assemble", "--input", inp, "--work", work, "--output", out,
                     "--runtime-seconds", "12.5", "--runtime-requests", "3"])
        _assert(code == 0, "assemble 应返回 0")

        res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        _assert(res.get("case_id") == "demo-search-001", "case_id 应原样保留")
        _assert(res.get("status") == "ok", "完整流程应 ok，实际 " + str(res.get("status")))
        _assert(len(res.get("results", [])) >= 1, "results 至少一条 include")
        _assert(res.get("screening", {}).get("excluded"), "screening 应记录 excluded")
        _assert(res.get("uncertainty", {}).get("coverage_gaps") == [], "覆盖缺口应为空")
        _assert(os.path.isfile(os.path.join(out, "screening.md")), "screening.md 应存在")
        _assert(os.path.isfile(os.path.join(out, "plan.md")), "plan.md 应存在")
        _assert(os.path.isfile(os.path.join(out, "result.json")), "result.json 应存在")

        code = _run(["selfcheck", "--output", out, "--work", work])
        _assert(code == 0, "selfcheck 应全 PASS 返回 0")

        # 幂等性：重复 assemble 除时间戳外结果一致
        before = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        _run(["assemble", "--input", inp, "--work", work, "--output", out,
              "--runtime-seconds", "12.5", "--runtime-requests", "3"])
        after = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        strip_time = lambda o: {k: v for k, v in o.items() if k != "change_log"}
        _assert(strip_time(before) == strip_time(after), "重复 assemble 结果应幂等")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return "full-pipeline"

def test_contract_list_form():
    root = tempfile.mkdtemp(prefix="selftest-listcontract-")
    try:
        inp = os.path.join(root, "input")
        work = os.path.join(root, "work")
        out = os.path.join(root, "output")
        os.makedirs(inp)
        case = _demo_case(contract=[
            {"name": "case_id", "type": "string", "required": True},
            {"name": "status", "type": "string", "required": True},
            {"name": "results", "type": "array", "required": True},
            {"name": "evidence", "type": "array", "required": True},
        ])
        _write(os.path.join(inp, "case.json"), json.dumps(case, ensure_ascii=False))
        _write(os.path.join(inp, "task.md"), "# 演示任务\n")
        code = _run(["run", "--input", inp, "--work", work, "--output", out])
        _assert(code == 0, "list 型契约 run 应返回 0")
        res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        for key in ("case_id", "status", "results", "evidence"):
            _assert(key in res, "list 型契约缺键 " + key)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return "list-contract"


def main():
    tests = [
        ("help", test_help),
        ("empty-input", test_empty_input),
        ("bad-json", test_bad_json),
        ("list-contract", test_contract_list_form),
        ("full-pipeline", test_full_pipeline),
    ]
    failed = []
    for name, fn in tests:
        try:
            tag = fn()
            print("[PASS]", name, "(" + tag + ")")
        except Exception as exc:  # noqa: BLE001
            failed.append((name, str(exc)))
            print("[FAIL]", name, str(exc))
    print("selftest: %d passed, %d failed" % (len(tests) - len(failed), len(failed)))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
