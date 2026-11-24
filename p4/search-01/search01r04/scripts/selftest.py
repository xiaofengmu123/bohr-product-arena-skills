#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py：在临时目录用合成输入跑完整流程并做不变量断言（纯标准库）。

覆盖：
  1. 空输入目录 -> input_error/insufficient_evidence，退出码 0，result.json 合法；
  2. 坏 JSON -> 记录 degradation，不崩溃；
  3. 正常 case + 带证据候选 -> results 排序、证据闸门、不编造不变量；
  4. 无证据候选 -> 隔离到 pending_human，不进入 results；
  5. 重复运行一致性。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
PY = sys.executable


def run(cmd, **kw):
    return subprocess.run([PY, "-B", MAIN] + cmd,
                          capture_output=True, text=True, **kw)


def sh(*args):
    return subprocess.run(list(args), capture_output=True, text=True)


CASE_OK = {
    "case_id": "case-demo-1",
    "armiId": "ARMI:demo",
    "question": "How do deep learning methods detect diabetic retinopathy?",
    "constraints": {"max_results": 10},
    "output_contract": {
        "required_fields": ["case_id", "results"],
        "metric": "precision_at_10",
    },
}

TASK_OK = """# 案例说明

研究问题：How do deep learning methods detect diabetic retinopathy?

约束：关注 CNN 与眼底图像；结果须可复核。
"""

REC_WITH_EV = {
    "title": "Development and Validation of a Deep Learning Algorithm for Detection of Diabetic Retinopathy in Retinal Fundus Photographs",
    "authors": ["Gulshan V", "Peng L", "Coram M"],
    "year": 2016,
    "journal": "JAMA",
    "doi": "10.1001/jama.2016.17216",
    "url": "https://doi.org/10.1001/jama.2016.17216",
    "database": "crossref",
    "relevance": 0.95,
    "relevance_reason": "核心方法 CNN + 眼底照片 + 糖尿病视网膜病变，主题高度相关",
    "meta_verified": True,
    "meta_confidence": 0.9,
    "evidence": {
        "locator": "crossref:10.1001/jama.2016.17216",
        "quote": "Development and Validation of a Deep Learning Algorithm for Detection of Diabetic Retinopathy in Retinal Fundus Photographs",
        "source": "crossref",
    },
}

REC_NO_EV = {
    "title": "A study with no verifiable source",
    "authors": ["Anonymous"],
    "year": 2020,
    "journal": "Unknown Journal",
    "database": "web",
    "relevance": 0.5,
    "relevance_reason": "unverified",
    "meta_verified": False,
}


def make_input(root, case=None, task=None):
    inp = os.path.join(root, "input")
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    os.makedirs(inp, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    if case is not None:
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            json.dump(case, f, ensure_ascii=False)
    if task is not None:
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
            f.write(task)
    return inp, out, work


def check(cond, msg, results):
    results.append((cond, msg))
    status = "PASS" if cond else "FAIL"
    print("[%s] %s" % (status, msg))
    return cond


def main():
    results = []
    tmp = tempfile.mkdtemp(prefix="selftest-search-")

    # ---- 1. 空输入 ----
    inp, out, work = make_input(os.path.join(tmp, "t1"))
    r = run(["run", "--input", inp, "--output", out, "--work", work])
    check(r.returncode == 0, "空输入 run 退出码 0", results)
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    check(res.get("case_id") is not None, "空输入仍有 case_id（占位）", results)
    check(res.get("status") in ("insufficient_evidence", "input_error", "partial"),
          "空输入 status 诚实（%s）" % res.get("status"), results)
    check(isinstance(res.get("results"), list) and res.get("results") == [],
          "空输入 results 为空列表（不编造）", results)

    # ---- 2. 坏 JSON ----
    inp, out, work = make_input(os.path.join(tmp, "t2"), task=TASK_OK)
    with open(os.path.join(inp, "case.json"), "wb") as f:
        f.write(b"\x00\xff{not valid json")
    r = run(["run", "--input", inp, "--output", out, "--work", work])
    check(r.returncode == 0, "坏 JSON 不崩溃（退出码 0）", results)
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    check("bad_json" in (res.get("errors") or []) or "bad_json" in " ".join(str(x) for x in res.get("errors") or []),
          "坏 JSON 记录到 errors", results)

    # ---- 3. 正常案例 + 带证据候选 ----
    inp, out, work = make_input(os.path.join(tmp, "t3"), case=CASE_OK, task=TASK_OK)
    r = run(["run", "--input", inp, "--output", out, "--work", work])
    check(r.returncode == 0, "正常案例 run 退出码 0", results)
    # import 带证据候选
    cand = os.path.join(work, "cand.json")
    with open(cand, "w", encoding="utf-8") as f:
        json.dump([REC_WITH_EV, REC_NO_EV], f, ensure_ascii=False)
    r = run(["import", "--file", cand, "--work", work])
    check(r.returncode == 0, "import 退出码 0", results)
    # 追加查询证据
    r = run(["evidence", "--inline", json.dumps({
        "kind": "query", "query": "deep learning diabetic retinopathy",
        "source": "paper_search", "database": "crossref",
        "rationale": "单概念召回", "results_count": 10}),
        "--work", work])
    # 追加不确定性
    r = run(["uncertainty", "--inline", json.dumps({
        "coverage_gaps": [{"aspect": "引用追踪", "gap": "未追踪种子文献引文", "next_query": "citation chain"}]}),
        "--work", work])
    r = run(["assemble", "--input", inp, "--output", out, "--work", work])
    check(r.returncode == 0, "assemble 退出码 0", results)
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    check(res.get("case_id") == "case-demo-1", "case_id 取自输入", results)
    check(res.get("status") in ("ok", "partial"), "status 为 ok/partial", results)
    check(len(res.get("results")) == 1, "带证据候选 1 条进入 results（实际 %d）" % len(res.get("results")), results)
    check(len(res.get("pending_human")) == 1, "无证据候选被隔离到 pending_human", results)
    rr = res["results"][0]
    check(rr.get("verification_status") in ("verified", "partial"), "results 条目有核验状态", results)
    check("required_fields" in res and "metric" in res, "output_contract 字段原样回填", results)
    check(res["required_fields"] == ["case_id", "results"], "output_contract 原值保留", results)
    ev = rr.get("evidence") or {}
    check(bool(ev.get("locator")) and bool(ev.get("quote")), "results 条目有 locator+quote", results)
    check(any(e.get("kind") == "relevance" for e in res.get("evidence", [])), "evidence 含 relevance 溯源", results)
    check(isinstance(res.get("uncertainty", {}).get("coverage_gaps"), list) and res["uncertainty"]["coverage_gaps"],
          "coverage_gaps 显式化", results)

    # ---- 4. 重复运行一致性 ----
    r2 = run(["assemble", "--input", inp, "--output", out, "--work", work])
    res2 = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    check(res2["results"][0]["score"] == rr["score"], "重复 assemble 排序一致", results)
    check(sorted(r["record_id"] for r in res2["results"]) == sorted(r["record_id"] for r in res["results"]),
          "重复运行结果集合一致", results)

    # ---- 5. selfcheck ----
    r = run(["selfcheck", "--output", out, "--work", work])
    check(r.returncode == 0, "selfcheck 退出码 0", results)
    check("SELFCHECK PASS" in r.stdout, "selfcheck 通过自检", results)

    shutil.rmtree(tmp, ignore_errors=True)

    n_fail = sum(1 for ok, _ in results if not ok)
    print("\nselftest: %d checks, %d failed" % (len(results), n_fail))
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())