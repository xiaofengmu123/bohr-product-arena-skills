#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检脚本：用临时目录验证脚本的三种失败场景与一次黄金路径。

运行：
  python3 scripts/selftest.py
退出码 0 表示全部通过，非 0 表示存在失败项。
纯标准库，不依赖网络，不产生正式测试产物。
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run_main(args):
    return subprocess.run(
        [sys.executable, "-B", MAIN] + args,
        cwd=os.path.dirname(HERE),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def ok(name, cond, detail=""):
    return {"name": name, "pass": bool(cond), "detail": detail}


def scenario_empty_input():
    with tempfile.TemporaryDirectory() as base:
        inp = os.path.join(base, "empty_input")
        out = os.path.join(base, "out")
        work = os.path.join(base, "work")
        os.makedirs(inp)
        r = run_main(["run", "--input", inp, "--output", out, "--work", work])
        res, _ = _load_result(out)
        checks = [
            ok("空输入不崩溃(退出码0)", r.returncode == 0, f"rc={r.returncode}"),
            ok("空输入写 result.json", res is not None),
            ok("空输入 status 合法",
               res and res.get("status") in ("input_error", "insufficient_evidence", "partial"),
               "status=" + str(res.get("status") if res else None)),
        ]
    return "空输入", checks


def scenario_bad_json():
    with tempfile.TemporaryDirectory() as base:
        inp = os.path.join(base, "badjson")
        out = os.path.join(base, "out")
        work = os.path.join(base, "work")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            f.write("{ this is not valid json ")
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
            f.write("# 任务\n研究问题：测试坏 JSON\n")
        r = run_main(["run", "--input", inp, "--output", out, "--work", work])
        res, deg = _load_result(out)
        checks = [
            ok("坏 JSON 不崩溃", r.returncode == 0, f"rc={r.returncode}"),
            ok("坏 JSON 写 result.json", res is not None),
            ok("坏 JSON 降级记录到 degradations",
               res is not None and any("bad_json" in d for d in res.get("degradations", [])),
               "degradations=" + json.dumps(res.get("degradations", []) if res else [], ensure_ascii=False)),
        ]
    return "坏 JSON", checks


def scenario_missing_input():
    with tempfile.TemporaryDirectory() as base:
        out = os.path.join(base, "out")
        work = os.path.join(base, "work")
        missing = os.path.join(base, "no_such_dir")
        r = run_main(["run", "--input", missing, "--output", out, "--work", work])
        res, _ = _load_result(out)
        checks = [
            ok("缺输入目录不崩溃", r.returncode == 0, f"rc={r.returncode}"),
            ok("缺输入 status=input_error",
               res and res.get("status") == "input_error",
               "status=" + str(res.get("status") if res else None)),
        ]
    return "缺输入", checks


def scenario_golden():
    with tempfile.TemporaryDirectory() as base:
        inp = os.path.join(base, "input")
        out = os.path.join(base, "out")
        work = os.path.join(base, "work")
        os.makedirs(inp)
        contract = {
            "output_contract": {
                "case_id": None,
                "status": None,
                "results": None,
                "sources": None,
                "uncertainty": None,
                "change_log": None,
                "custom_field": None,
            }
        }
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            json.dump({
                "case_id": "case-demo-001",
                "research_question": "图神经网络在图表示学习中的应用",
                "search_scope": {"databases": ["crossref", "europepmc"], "time_range": None},
                **contract,
            }, f, ensure_ascii=False)
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
            f.write("# 任务\n研究问题：图神经网络在图表示学习中的应用\n")

        run_main(["run", "--input", inp, "--output", out, "--work", work])

        records = [
            {
                "title": "Graph Neural Networks: A Review",
                "authors": ["Alice Chen", "Bob Li"],
                "year": 2020,
                "journal": "IEEE TPAMI",
                "doi": "10.1000/demo.001",
                "database": "crossref",
                "relevance": 92,
                "relevance_reason": "综述，直接讨论 GNN 表示学习",
            },
            {
                "title": "Inductive Representation Learning on Large Graphs",
                "authors": ["Will Hamilton", "Rex Ying", "Jure Leskovec"],
                "year": 2017,
                "journal": "NeurIPS",
                "doi": "10.1000/demo.002",
                "database": "europepmc",
                "relevance": 88,
                "relevance_reason": "GraphSAGE，节点嵌入表示学习代表方法",
            },
        ]
        run_main(["import", "--inline", json.dumps(records), "--work", work,
                  "--database", "crossref", "--query-id", "Q1", "--label", "demo"])
        run_main(["evidence", "--inline",
                  json.dumps({"kind": "query", "query": "graph neural network representation learning",
                              "source": "crossref", "database": "crossref",
                              "rationale": "Q1 核心术语宽召回", "query_id": "Q1",
                              "locator": "case.json#research_question",
                              "quote": "图神经网络在图表示学习中的应用"},
                             ensure_ascii=False),
                  "--work", work])
        run_main(["assemble", "--input", inp, "--output", out, "--work", work])
        run_main(["selfcheck", "--output", out, "--work", work])

        res, _ = _load_result(out)
        sc, _ = _load_file(os.path.join(work, "selfcheck.json"))
        checks = [
            ok("黄金路径 result.json 存在", res is not None),
            ok("黄金路径 status=ok", res and res.get("status") == "ok",
               "status=" + str(res.get("status") if res else None)),
            ok("结果条数为 2", res and len(res.get("results", [])) == 2,
               "n=" + str(len(res.get("results", [])) if res else None)),
            ok("每条结果有 score", res and all(isinstance(r.get("score"), float)
               for r in res.get("results", []))),
            ok("每条结果有 rank 且连续",
               res and [r.get("rank") for r in res.get("results", [])] == [1, 2]),
            ok("证据溯源挂到记录", res and any(r.get("evidence") for r in res.get("results", []))),
            ok("output_contract 字段回填", res and "custom_field" in res and "change_log" in res),
            ok("selfcheck PASS", sc and sc.get("conclusion") == "PASS"),
        ]
    return "黄金路径", checks


VOLATILE_KEYS = {
    "runtime", "change_log", "elapsed_s", "finished_at", "started_at",
    "started_ts", "timestamp", "retrieved_at", "accessed_at", "time",
    "created_at", "generated_at", "at", "evidence_id", "manifest_id",
    "plan_id", "sha256", "bytes", "note",
}


def canonical(obj):
    if isinstance(obj, dict):
        return {k: canonical(v) for k, v in obj.items() if k not in VOLATILE_KEYS}
    if isinstance(obj, list):
        return [canonical(x) for x in obj]
    return obj


def scenario_determinism():
    with tempfile.TemporaryDirectory() as base:
        inp = os.path.join(base, "input")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            json.dump({"case_id": "case-demo-002",
                       "research_question": "transformer 注意力机制"}, f, ensure_ascii=False)
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
            f.write("# 任务\n研究问题：transformer 注意力机制\n")
        outs = []
        for i in range(2):
            out = os.path.join(base, f"out{i}")
            work = os.path.join(base, f"work{i}")
            run_main(["run", "--input", inp, "--output", out, "--work", work])
            res, _ = _load_result(out)
            outs.append(json.dumps(canonical(res), sort_keys=True, ensure_ascii=False, default=str))
        checks = [ok("两次运行结果一致（排除时间戳等易变字段）", outs[0] == outs[1])]
    return "重复运行确定性", checks


def _load_result(out_dir):
    return _load_file(os.path.join(out_dir, "result.json"))


def _load_file(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except Exception as e:
        return None, str(e)[:120]


def main():
    scenarios = [
        scenario_empty_input(),
        scenario_bad_json(),
        scenario_missing_input(),
        scenario_golden(),
        scenario_determinism(),
    ]
    all_checks = []
    failed = 0
    for name, checks in scenarios:
        passed = sum(1 for c in checks if c["pass"])
        failed += len(checks) - passed
        print(f"[{name}] {passed}/{len(checks)}")
        for c in checks:
            if not c["pass"]:
                print("  FAIL:", c["name"], "-", c["detail"])
        all_checks.extend(checks)
    total = len(all_checks)
    passed = sum(1 for c in all_checks if c["pass"])
    print(f"\nSELFTEST {passed}/{total}")
    if passed == total:
        print("ALL PASS")
        return 0
    print("存在失败项")
    return 1


if __name__ == "__main__":
    sys.exit(main())
