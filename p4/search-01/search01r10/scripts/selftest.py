#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""source-bound literature search：自检脚本（纯标准库）。

在不访问网络、不依赖正式案例的前提下，演练：
  1. --help 退出码 0
  2. 空输入目录        -> result.json 合法且 status != ok（insufficient_evidence/input_error）
  3. 坏 JSON / 坏编码   -> 不崩溃，降级记录进 errors/degradations
  4. 输出契约回填       -> result.json 保留题给 output_contract 的全部字段
  5. 导入+组装+去重+打分 -> 候选可排序、证据保留、sources 非空
  6. 重复运行确定性     -> 同一输入两次 assemble 的 result.json 完全一致
运行：python3 scripts/selftest.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(*argv, cwd=None):
    return subprocess.run([sys.executable, "-B", MAIN] + list(argv),
                          cwd=cwd or HERE, capture_output=True, text=True)


def check(name, ok, detail=""):
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", name,
                         ("  " + detail) if detail else ""))
    return bool(ok)


def main():
    results = []

    # 1. --help
    r = run("--help")
    results.append(check("--help 退出码 0", r.returncode == 0,
                         "rc=%d" % r.returncode))

    tmp = tempfile.mkdtemp(prefix="skill_selftest_")
    inp = os.path.join(tmp, "input")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    os.makedirs(inp, exist_ok=True)

    # 2. 空输入目录
    r = run("run", "--input", inp, "--output", out, "--work", work)
    res = json.load(open(os.path.join(out, "result.json"),
                         encoding="utf-8"))
    ok_empty = r.returncode == 0 and res.get("status") in (
        "insufficient_evidence", "input_error")
    results.append(check("空输入不崩溃且状态诚实", ok_empty,
                         "status=%s rc=%d" % (res.get("status"), r.returncode)))

    # 3. 坏 JSON
    os.makedirs(os.path.join(inp), exist_ok=True)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{not valid json]((")
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# task\n研究问题：高分子材料的自修复行为\n")
    r = run("run", "--input", inp, "--output", out, "--work", work)
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    has_err = bool(res.get("errors") or res.get("degradations"))
    results.append(check("坏 JSON 不崩溃且记录降级", r.returncode == 0 and has_err,
                         "errors=%s" % (res.get("errors"),)))

    # 3b. 坏编码（latin-1 乱码文件）
    with open(os.path.join(inp, "case.json"), "wb") as fh:
        fh.write(b'{"case_id":"enc-case","research_question":"\xff\xfe bad"}')
    r = run("run", "--input", inp, "--output", out, "--work", work)
    results.append(check("坏编码不崩溃", r.returncode == 0, "rc=%d" % r.returncode))

    # 4. 契约回填
    case = {
        "case_id": "case-contract-1",
        "research_question": "CRISPR 基因编辑在作物改良中的应用",
        "output_contract": {
            "results": [], "summary": None, "top_papers": None,
            "extra": {"nested": None},
        },
    }
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# task\n")
    run("run", "--input", inp, "--output", out, "--work", work)
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    keys_ok = all(k in res for k in ("results", "summary", "top_papers", "extra"))
    results.append(check("output_contract 字段原样回填", keys_ok,
                         "keys=%s" % sorted(res.keys())))

    # 4b. plan --plan 接受人工规划（subprocess 列表传参，规避 shell 引号）
    run("plan", "--work", work, "--plan", json.dumps(
        {"keywords": ["CRISPR", "crop"], "search_scope": "2015-2025"}))
    ledger = json.load(open(os.path.join(work, "ledger.json"), encoding="utf-8"))
    results.append(check("plan --plan 写入人工规划",
                         ledger.get("plan", {}).get("search_scope") == "2015-2025"))

    # 5. 导入 -> 去重 -> 打分 -> 证据
    cand = {
        "title": "Genome editing for crop improvement via CRISPR/Cas9",
        "authors": ["A. Author", "B. Author"], "year": 2019,
        "journal": "Nature Plants", "doi": "10.1234/example.2019",
        "url": "https://doi.org/10.1234/example.2019",
        "database": "crossref", "relevance": 0.92,
        "relevance_reason": "直接研究 CRISPR 作物改良",
        "evidence": [{"locator": "abstract", "quote": "CRISPR/Cas9 improves crops"}],
    }
    # 刻意分两条跨两次 import，验证 DOI 去重合并
    run("import", "--work", work, "--inline", json.dumps([cand]))
    cand2 = dict(cand)
    cand2["title"] = "A Different Study"
    cand2["doi"] = "10.1234/example.2019"  # 同一 DOI
    cand2["journal"] = None
    run("import", "--work", work, "--inline", json.dumps([cand2]))
    # 排除记录：不进入 results，且保留筛选依据
    run("import", "--work", work, "--inline", json.dumps([{
        "title": "An off-topic paper", "doi": "10.9999/off.1",
        "database": "web_search", "relevance": 0.1,
        "excluded": True, "exclusion_reason": "研究对象不符",
    }]))
    run("evidence", "--work", work, "--inline",
        json.dumps({"query": "CRISPR crop editing", "source": "crossref",
                    "database": "crossref", "rationale": "single concept"}))
    run("note", "--work", work, "--inline",
        json.dumps({"status_notes": ["已核验 DOI 去重"],
                    "limitations": ["仅覆盖单一数据库"],
                    "coverage_gaps": ["未追引用图"],
                    "pending_human": ["相关性阈值待确认"],
                    "search_scope": "2015-2025 年作物基因编辑"}))
    run("assemble", "--work", work, "--output", out)
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    results.append(check("导入后可组装出结果", len(res.get("results") or []) == 1,
                         "n=%d" % len(res.get("results") or [])))
    results.append(check("结果含 score 且证据保留", (
        res.get("results") and res["results"][0].get("score") is not None and
        res["results"][0].get("evidence"))))
    results.append(check("sources 非空", bool(res.get("sources"))))
    results.append(check("note 说明写入结果", (
        res.get("status_notes") and res.get("limitations") and
        res.get("pending_human") and res.get("search_scope"))))

    # 6. 重复运行确定性
    run("assemble", "--work", work, "--output", out)
    res2 = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    same = json.dumps(res, sort_keys=True) == json.dumps(res2, sort_keys=True)
    results.append(check("重复 assemble 结果一致", same))

    # 7. selfcheck 通过
    r = run("selfcheck", "--output", out)
    results.append(check("selfcheck 通过", r.returncode == 0, "rc=%d" % r.returncode))

    total = len(results)
    passed = sum(results)
    print("\nselftest: %d/%d PASS" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())