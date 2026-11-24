# -*- coding: utf-8 -*-
"""selftest.py — 交付前自检：用临时 demo 输入跑通 bootstrap/record/finalize/rank/path，
并覆盖空输入、坏 JSON 两类降级场景。仅依赖标准库，退出码 0 表示全部通过。"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [ok] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def run_script(args, cwd=None):
    return subprocess.run(
        [sys.executable, "-B", MAIN] + args,
        capture_output=True, text=True, cwd=cwd,
    )


def make_case_input(d, case_id="demo-case-1"):
    d = os.path.join(d, "input")
    os.makedirs(d, exist_ok=True)
    contract = {
        "candidate_papers": [],
        "ranking_basis": [],
        "status_note": None,
        "evidence_map": {},
    }
    with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": case_id, "output_contract": contract}, fh, ensure_ascii=False)
    with open(os.path.join(d, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 从种子论文追踪方法与数据出处\n")
    with open(os.path.join(d, "material.txt"), "w", encoding="utf-8") as fh:
        fh.write("seed paper: A cites B and C (dummy evidence)\n")
    return d


def main():
    print("== selftest: 正常流程 ==")
    with tempfile.TemporaryDirectory() as tmp:
        inp = make_case_input(tmp)
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")

        r = run_script(["bootstrap", "--input", inp, "--output", out, "--work", work])
        check("bootstrap 退出码 0", r.returncode == 0, r.stderr)
        rpath = os.path.join(out, "result.json")
        check("result.json 存在", os.path.exists(rpath))
        data = json.load(open(rpath, encoding="utf-8"))
        check("case_id 正确", data.get("case_id") == "demo-case-1")
        check("output_contract 字段回填",
              data.get("candidate_papers") == [] and data.get("ranking_basis") == []
              and data.get("status_note") is None and data.get("evidence_map") == {})
        check("status 合法", data.get("status") in {"ok", "partial", "insufficient_evidence", "input_error"})

        paper = {"id": "p1", "title": "Seed work", "year": 2020, "level": 0, "confidence": 0.9,
                 "doi": "10.1000/xyz", "authors": ["Alice Zhang"], "venue": "J"}
        r = run_script(["record", "--kind", "paper", "--json", json.dumps(paper, ensure_ascii=False),
                        "--reason", "种子核实", "--output", out, "--work", work])
        check("record paper 退出码 0", r.returncode == 0, r.stderr)
        r = run_script(["record", "--kind", "edge", "--json",
                        json.dumps({"source": "p1", "target": "p2", "relation": "cites",
                                    "evidence": [{"locator": "material.txt#L1", "quote": "A cites B"}]}, ensure_ascii=False),
                        "--reason", "后向引用", "--output", out, "--work", work])
        check("record edge 退出码 0", r.returncode == 0, r.stderr)
        r = run_script(["record", "--kind", "source", "--json",
                        json.dumps({"locator": "material.txt#L1", "quote": "A cites B and C"}, ensure_ascii=False),
                        "--reason", "证据留档", "--output", out, "--work", work])
        check("record source 退出码 0", r.returncode == 0, r.stderr)
        r = run_script(["record", "--kind", "status", "--json", '{"status": "partial"}',
                        "--output", out, "--work", work])
        check("record status 退出码 0", r.returncode == 0, r.stderr)

        data = json.load(open(rpath, encoding="utf-8"))
        check("paper 已追加", len(data["results"]["papers"]) == 1)
        check("edge 已追加", len(data["results"]["edges"]) == 1)
        check("source 已追加", len(data["sources"]) == 1)
        check("change_log 递增", len(data["change_log"]) >= 5)

        r = run_script(["rank", "--output", out])
        check("rank 退出码 0 且输出 JSON", r.returncode == 0 and "ranking" in r.stdout, r.stderr)
        r = run_script(["path", "--source", "p1", "--target", "p2", "--output", out])
        check("path 退出码 0", r.returncode == 0, r.stderr)
        check("path 可达", "true" in r.stdout)

        r = run_script(["finalize", "--output", out])
        check("finalize 退出码 0", r.returncode == 0, r.stderr)
        for art in ("report.md", "papers.csv", "edges.csv", "citation_graph.svg"):
            check(f"{art} 已生成", os.path.exists(os.path.join(out, art)))
        data = json.load(open(rpath, encoding="utf-8"))
        check("finalize 后 result.json 合法", data.get("case_id") and data.get("status"))

    print("== selftest: 空输入 ==")
    with tempfile.TemporaryDirectory() as tmp:
        inp = os.path.join(tmp, "input")
        os.makedirs(inp, exist_ok=True)
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        r = run_script(["bootstrap", "--input", inp, "--output", out, "--work", work])
        check("空输入 bootstrap 退出码 0", r.returncode == 0, r.stderr)
        data = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        check("空输入 status=insufficient_evidence", data.get("status") == "insufficient_evidence", data.get("status"))

    print("== selftest: 坏 JSON ==")
    with tempfile.TemporaryDirectory() as tmp:
        inp = os.path.join(tmp, "input")
        os.makedirs(inp, exist_ok=True)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{bad json")
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        r = run_script(["bootstrap", "--input", inp, "--output", out, "--work", work])
        check("坏 JSON bootstrap 退出码 0", r.returncode == 0, r.stderr)
        data = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        check("坏 JSON 记入 degradations", any(d.get("issue") == "bad_json_case" for d in data.get("degradations", [])))

    print("== selftest: 去重合并 ==")
    with tempfile.TemporaryDirectory() as tmp:
        inp = make_case_input(tmp, "demo-dup")
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        run_script(["bootstrap", "--input", inp, "--output", out, "--work", work])
        p1 = {"id": "a", "title": "Same Paper", "year": 2021, "doi": "10.1/x", "authors": ["K Lee"]}
        p2 = {"id": "b", "title": "Same Paper", "year": 2021, "authors": ["K Lee"], "venue": "conf"}
        run_script(["record", "--kind", "paper", "--json", json.dumps(p1), "--output", out, "--work", work])
        run_script(["record", "--kind", "paper", "--json", json.dumps(p2), "--output", out, "--work", work])
        run_script(["finalize", "--output", out])
        data = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        check("去重后剩 1 条", len(data["results"]["papers"]) == 1, str(len(data["results"]["papers"])))
        check("id_remap 写入", "id_remap" in data["results"])

    print(f"\nselftest 完成: {PASS} 通过, {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())