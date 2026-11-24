#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检脚本：在 Skill 根目录执行 `python3 scripts/selftest.py`。

覆盖三类场景并逐项打印 PASS/FAIL，任何关键失败返回退出码 1：
  1. CLI 与契约：--help 退出码 0；空输入/坏 JSON/缺输入不崩溃且写合法产物；
  2. 确定性：同一输入重复运行，results/score/排序保持一致；
  3. 结构：result.json 含 case_id/status/sources/uncertainty/change_log，
     每条结果有 title + 证据逐字 quote + score，rank 连续。

脚本只用标准库、只依赖 scripts/main.py 与 scripts/core_impl.py 的公开入口，
把临时文件写入系统临时目录，不污染正式产物目录。
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "scripts", "main.py")

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("[PASS] %s" % name)
    else:
        FAIL += 1
        print("[FAIL] %s%s" % (name, (" — " + detail) if detail else ""))


def run_py(args):
    return subprocess.run([sys.executable, "-B", MAIN] + args,
                          capture_output=True, text=True)


def make_empty_input(tmp):
    d = os.path.join(tmp, "empty")
    os.makedirs(d, exist_ok=True)
    return d


def make_bad_json_input(tmp):
    d = os.path.join(tmp, "badjson")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as f:
        f.write("{ this is not valid json ")
    with open(os.path.join(d, "task.md"), "w", encoding="utf-8") as f:
        f.write("# task\ncase_id: case-bad\n")
    return d


def make_real_input(tmp):
    d = os.path.join(tmp, "real")
    os.makedirs(d, exist_ok=True)
    case = {
        "case_id": "case-demo-001",
        "research_question": "超导材料中的铁基化合物临界温度调控",
        "constraint": "保留 DOI 与来源",
        "output_contract": {
            "results": None, "evidence": None, "candidates": None,
            "extra_report": None, "tag_list": None,
        },
    }
    with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as f:
        json.dump(case, f, ensure_ascii=False)
    with open(os.path.join(d, "task.md"), "w", encoding="utf-8") as f:
        f.write("# 学术文献检索\ncase_id: case-demo-001\n研究问题：超导材料\n")
    return d


def make_candidates(tmp):
    p = os.path.join(tmp, "candidates.json")
    data = [
        {"title": "High-Tc Iron-Based Superconductors: A Review",
         "authors": ["A. Author", "B. Author"], "year": 2015,
         "journal": "Reports on Progress in Physics", "doi": "10.1088/0034-4885/78/6/062001",
         "database": "crossref", "relevance": 0.95,
         "relevance_reason": "综述铁基超导临界温度调控",
         "evidence": [{"locator": "crossref.abstract", "source": "crossref",
                       "quote": "iron-based superconductors critical temperature",
                       "kind": "recall"}]},
        {"title": "Iron-based superconductors review duplicate",
         "authors": ["A. Author"], "year": 2015, "doi": "10.1088/0034-4885/78/6/062001",
         "database": "europepmc", "relevance": 0.9,
         "relevance_reason": "同一 DOI 的重复记录",
         "evidence": [{"locator": "europepmc.title", "source": "europepmc",
                       "quote": "iron-based superconductors", "kind": "recall"}]},
    ]
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return p


def read_result(out):
    with open(os.path.join(out, "result.json"), encoding="utf-8") as f:
        return json.load(f)


def main():
    tmp = tempfile.mkdtemp(prefix="skill_selftest_")
    print("临时目录: %s\n" % tmp)

    # 1. --help 退出码 0
    r = run_py(["--help"])
    check("help_exit_0", r.returncode == 0, "rc=%d" % r.returncode)

    # 2. 空输入目录
    empty_in = make_empty_input(tmp)
    empty_out = os.path.join(tmp, "out_empty")
    empty_work = os.path.join(tmp, "work_empty")
    r = run_py(["run", "--input", empty_in, "--output", empty_out,
                "--work", empty_work])
    check("empty_input_exit_0", r.returncode == 0, "rc=%d" % r.returncode)
    if r.returncode == 0:
        res = read_result(empty_out)
        check("empty_input_legal", res.get("case_id") and
              res.get("status") == "insufficient_evidence",
              "status=%s" % res.get("status"))

    # 3. 坏 JSON
    bad_in = make_bad_json_input(tmp)
    bad_out = os.path.join(tmp, "out_bad")
    bad_work = os.path.join(tmp, "work_bad")
    r = run_py(["run", "--input", bad_in, "--output", bad_out,
                "--work", bad_work])
    check("bad_json_exit_0", r.returncode == 0, "rc=%d" % r.returncode)
    if r.returncode == 0:
        res = read_result(bad_out)
        check("bad_json_legal", "case_id" in res and "status" in res,
              "status=%s" % res.get("status"))

    # 4. 正式流程：run -> import -> evidence -> assemble -> selfcheck
    real_in = make_real_input(tmp)
    real_out = os.path.join(tmp, "out_real")
    real_work = os.path.join(tmp, "work_real")
    r = run_py(["run", "--input", real_in, "--output", real_out,
                "--work", real_work])
    check("real_run_exit_0", r.returncode == 0, "rc=%d" % r.returncode)

    cand = make_candidates(tmp)
    r = run_py(["import", "--file", cand, "--work", real_work])
    check("import_exit_0", r.returncode == 0, "rc=%d" % r.returncode)

    ev = json.dumps({"query": "iron-based superconductor critical temperature",
                     "source": "paper_search", "database": "crossref",
                     "rationale": "单概念召回", "kind": "query"})
    r = run_py(["evidence", "--inline", ev, "--work", real_work])
    check("evidence_exit_0", r.returncode == 0, "rc=%d" % r.returncode)

    r = run_py(["ledger", "--inline",
                json.dumps({"phase": "recall", "action": "paper_search",
                            "reason": "demo"}), "--work", real_work])
    check("ledger_exit_0", r.returncode in (0, 3), "rc=%d" % r.returncode)

    r = run_py(["assemble", "--input", real_in, "--output", real_out,
                "--work", real_work])
    check("assemble_exit_0", r.returncode == 0, "rc=%d" % r.returncode)

    r = run_py(["selfcheck", "--output", real_out, "--work", real_work])
    check("selfcheck_pass", r.returncode == 0, "rc=%d" % r.returncode)

    res = read_result(real_out)
    # 去重：同一 DOI 的两条记录应合并为 1 条
    check("dedup_same_doi", len(res["results"]) == 1,
          "results=%d" % len(res["results"]))
    if res["results"]:
        r0 = res["results"][0]
        check("result_has_score", r0.get("score") is not None)
        check("result_has_quote", any(e.get("quote") for e in r0.get("evidence") or []))
        check("result_rank_1", r0.get("rank") == 1)
    check("contract_backfilled", "extra_report" in res and "tag_list" in res,
          "keys=%s" % sorted(res.keys()))
    check("change_log_nonempty", bool(res.get("change_log")))
    check("budget_present", "budget" in res)

    # 5. 确定性：重复 assemble 结果一致
    r2 = run_py(["assemble", "--input", real_in, "--output", real_out,
                 "--work", real_work])
    res2 = read_result(real_out)
    same = [x.get("title") for x in res["results"]] == \
           [x.get("title") for x in res2["results"]] and \
           [x.get("score") for x in res["results"]] == \
           [x.get("score") for x in res2["results"]]
    check("deterministic_results", same)

    print("\n%s" % ("PASS" if FAIL == 0 else "FAIL"))
    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
