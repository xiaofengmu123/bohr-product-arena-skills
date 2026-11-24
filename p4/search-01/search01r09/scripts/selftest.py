#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检脚本：在隔离的临时目录中验证 Skill 的健壮性、契约回填与可复现性。

纯标准库。运行时以进程内导入 main.py 的方式执行，不依赖网络、不依赖外部数据。
退出码 0 表示全部通过；1 表示至少一项失败。
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main  # noqa: E402

CHECKS = []


def check(name, cond, detail=""):
    CHECKS.append((name, bool(cond), detail))
    mark = "PASS" if cond else "FAIL"
    print("%s: %s%s" % (mark, name, (" — " + detail) if detail else ""))
    return cond


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def write_json(path, obj):
    write(path, json.dumps(obj, ensure_ascii=False))


def test_help():
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = main.main(["--help"])
    except SystemExit as e:
        code = e.code
    check("help_exit_0", code == 0, "got exit %r" % code)


def test_empty_input(tmp):
    inp = os.path.join(tmp, "empty_in")
    out = os.path.join(tmp, "empty_out")
    wrk = os.path.join(tmp, "empty_work")
    os.makedirs(inp, exist_ok=True)
    main.run_pipeline(inp, out, wrk)
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    check("empty_has_result_json", os.path.isfile(os.path.join(out, "result.json")))
    check("empty_status_partial", res.get("status") == "partial",
          res.get("status"))
    check("empty_valid_json", isinstance(res, dict))


def test_bad_json(tmp):
    inp = os.path.join(tmp, "bad_in")
    out = os.path.join(tmp, "bad_out")
    wrk = os.path.join(tmp, "bad_work")
    os.makedirs(inp, exist_ok=True)
    write(os.path.join(inp, "case.json"), "{{{ 这不是JSON")
    write(os.path.join(inp, "task.md"), "# 任务\n研究问题：测试坏 JSON 容错\n")
    main.run_pipeline(inp, out, wrk)
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    deg = res.get("degradations") or []
    check("bad_json_not_crash", res.get("status") in
          ("partial", "insufficient_evidence", "input_error"),
          res.get("status"))
    check("bad_json_recorded", any("bad_json" in d for d in deg),
          ";".join(deg))


def test_happy_path(tmp):
    inp = os.path.join(tmp, "case_in")
    out = os.path.join(tmp, "case_out")
    wrk = os.path.join(tmp, "case_work")
    os.makedirs(inp, exist_ok=True)

    contract = {
        "summary": None,
        "top_hits": [],
    }
    write_json(os.path.join(inp, "case.json"), {
        "case_id": "self-test-case-001",
        "research_question": "基于图神经网络的蛋白质相互作用预测",
        "output_contract": contract,
    })
    write(os.path.join(inp, "task.md"),
          "# 学术文献检索\n\n研究问题：基于图神经网络的蛋白质相互作用预测\n")

    # 骨架
    main.run_pipeline(inp, out, wrk)
    check("happy_skeleton", json.load(open(os.path.join(out, "result.json"),
                                           encoding="utf-8")).get("status") == "partial")

    # 导入两条候选：一条 DOI 重复，应被合并
    cand = os.path.join(wrk, "candidates_in.json")
    write_json(cand, [
        {
            "title": "Graph neural networks for protein interaction prediction",
            "authors": ["A. Smith", "B. Jones"],
            "year": 2021, "journal": "Bioinformatics", "doi": "10.1000/xyz123",
            "url": "https://doi.org/10.1000/xyz123",
            "database": "crossref", "relevance": 0.9,
            "relevance_reason": "直接讨论图神经网络用于蛋白质相互作用预测",
            "evidence": [{"locator": "paper_search#1", "quote": "GNN-based PPI prediction"}],
        },
        {
            "title": "Graph Neural Networks for Protein Interaction Prediction",
            "authors": ["Alice Smith", "Bob Jones"],
            "year": 2021, "journal": "Bioinformatics", "doi": "10.1000/xyz123",
            "database": "europepmc", "relevance": 0.9,
            "relevance_reason": "同一文献的跨库重复记录",
            "evidence": [{"locator": "web_search#1", "quote": "same article"}],
        },
        {
            "title": "Deep learning for molecular property prediction",
            "authors": ["C. Lee"], "year": 2020, "journal": "J. Chem. Inf.",
            "doi": None, "url": "https://arxiv.org/abs/2001.00001",
            "database": "arxiv", "relevance": 0.6,
            "relevance_reason": "方法相关但主题偏分子性质",
            "evidence": [{"locator": "paper_search#2", "quote": "molecular property"}],
        },
    ])
    n, _info = main.import_candidates(wrk, cand)
    check("happy_import_count", n == 3, "imported %d" % n)

    main.add_evidence(wrk, {
        "query": "graph neural network protein interaction",
        "source": "paper_search", "database": "crossref",
        "rationale": "概念种子查询", "kind": "query",
    })

    res = main.assemble(inp, out, wrk)
    check("happy_dedup", len(res["results"]) == 2,
          "expected 2 after dedup, got %d" % len(res["results"]))
    check("happy_rank_order", res["results"][0]["title"].startswith("Graph"),
          res["results"][0]["title"][:40])
    check("happy_contract_backfill",
          "summary" in res and "top_hits" in res,
          str(sorted(k for k in ("summary", "top_hits") if k in res)))

    f, ok = main.selfcheck(out, inp, wrk)
    check("happy_selfcheck_pass", not f, ";".join(f))

    # 可复现性：再次 assemble，忽略时间戳后内容一致
    res2 = main.assemble(inp, out, wrk)
    strip = lambda r: {k: v for k, v in r.items()  # noqa: E731
                       if k not in ("runtime", "change_log")}
    check("happy_reproducible", strip(res) == strip(res2),
          "两次 assemble 的确定性结果不一致")


def test_status_insufficient(tmp):
    inp = os.path.join(tmp, "si_in")
    out = os.path.join(tmp, "si_out")
    wrk = os.path.join(tmp, "si_work")
    os.makedirs(inp, exist_ok=True)
    write_json(os.path.join(inp, "case.json"),
               {"case_id": "si-1", "research_question": "空结果案例"})
    main.run_pipeline(inp, out, wrk)
    res = main.assemble(inp, out, wrk)
    check("si_status", res["status"] == "insufficient_evidence", res["status"])


def main_selftest():
    tmp = tempfile.mkdtemp(prefix="skill_selftest_")
    try:
        test_help()
        test_empty_input(tmp)
        test_bad_json(tmp)
        test_happy_path(tmp)
        test_status_insufficient(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    fails = [n for n, c, _d in CHECKS if not c]
    print("\nselftest: %d pass, %d fail" % (len(CHECKS) - len(fails), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main_selftest())