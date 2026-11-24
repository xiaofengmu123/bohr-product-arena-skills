# -*- coding: utf-8 -*-
"""selftest.py — 空目录/坏 JSON/缺输入/坏编码/契约回填/工具确定性/重复运行自检。

全部场景退出码 0（失败会断言抛错并以非零退出）。交付前必须运行本脚本。
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402

NOW = "2026-01-01T00:00:00Z"


def make_tmp():
    return tempfile.mkdtemp(prefix="citation_selftest_")


def run_case(name, setup):
    base = make_tmp()
    input_dir = os.path.join(base, "input")
    output_dir = os.path.join(base, "output")
    work_dir = os.path.join(base, "work")
    os.makedirs(input_dir)
    setup(input_dir)
    core_impl.build_skeleton(input_dir, output_dir, work_dir, now=NOW)
    rp = os.path.join(output_dir, "result.json")
    assert os.path.isfile(rp), name + ": 未写 result.json"
    data = json.load(open(rp, encoding="utf-8"))
    assert data.get("case_id") and data.get("status"), name + ": 缺 case_id/status"
    core_impl.finalize(output_dir)
    for f in ("report.md", "papers.csv", "edges.csv", "citation_graph.svg"):
        assert os.path.isfile(os.path.join(output_dir, f)), name + ": 缺 " + f
    return data


def main():
    passed = 0

    # 1. 空目录
    d1 = run_case("空目录", lambda inp: None)
    assert d1["status"] == "insufficient_evidence"
    passed += 1

    # 2. 坏 JSON
    def bad_json(inp):
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            f.write("{ not valid json ")
    d2 = run_case("坏JSON", bad_json)
    assert d2["status"] == "input_error"
    passed += 1

    # 3. 缺输入目录
    base = make_tmp()
    core_impl.build_skeleton(os.path.join(base, "nope"), os.path.join(base, "out"),
                             os.path.join(base, "work"), now=NOW)
    assert os.path.isfile(os.path.join(base, "out", "result.json"))
    passed += 1

    # 4. 坏编码（UTF-16，应容错不崩溃）
    def bad_encoding(inp):
        with open(os.path.join(inp, "case.json"), "wb") as f:
            f.write('{"case_id":"c1","output_contract":{"x":1}}'.encode("utf-16"))
    d4 = run_case("坏编码", bad_encoding)
    assert d4["status"] in ("input_error", "partial")
    passed += 1

    # 5. output_contract 回填（缺值填 null/[]，不改键）
    def with_contract(inp):
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            json.dump({"case_id": "c9",
                       "output_contract": {"papers": [], "graph": {}, "notes": None}},
                      f, ensure_ascii=False)
    d5 = run_case("契约回填", with_contract)
    assert set(d5["output_contract"].keys()) == {"papers", "graph", "notes"}
    assert d5["output_contract"]["papers"] == []
    passed += 1

    # 6. 工具确定性（去重/排序/路径）
    papers = [
        {"id": "A", "title": "Deep Learning", "authors": ["LeCun Y"], "year": 2015,
         "doi": "10.1/x", "type": "article"},
        {"id": "B", "title": "Deep Learning", "authors": ["Yann LeCun"], "year": 2015,
         "doi": "10.1/x", "type": "preprint"},
        {"id": "C", "title": "Attention Is All", "authors": ["Vaswani A"], "year": 2017,
         "type": "article"},
    ]
    d6 = core_impl.dedupe(papers)
    assert any(g["members"] == ["A", "B"] for g in d6["merge_groups"]), \
        "同一 DOI 应合并为一组"
    edges = [{"source": "A", "target": "C", "relation": "cites"}]
    rk = core_impl.rank_papers(papers, edges, "A")
    by_id = {r["id"]: r for r in rk}
    assert by_id["A"]["distance"] == 0
    assert by_id["B"]["distance"] == 999
    assert by_id["C"]["cited_by"] == 1
    assert rk[-1]["id"] == "B", "孤立节点应排最后"
    sp = core_impl.shortest_path(edges, "A", "C")
    assert sp.get("reachable") and sp["length"] == 1
    passed += 1

    # 7. 重复运行确定性
    base = make_tmp()
    inp = os.path.join(base, "input")
    out = os.path.join(base, "out1")
    work = os.path.join(base, "work1")
    os.makedirs(inp)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        json.dump({"case_id": "det", "output_contract": {"papers": []}}, f)
    core_impl.build_skeleton(inp, out, work, now=NOW)
    core_impl.finalize(out)
    s1 = open(os.path.join(out, "papers.csv"), encoding="utf-8").read()
    core_impl.build_skeleton(inp, out, work, now=NOW)
    core_impl.finalize(out)
    s2 = open(os.path.join(out, "papers.csv"), encoding="utf-8").read()
    assert s1 == s2, "同输入两次运行产物应一致"
    passed += 1

    print("selftest ok: %d/7 通过" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
