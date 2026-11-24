# -*- coding: utf-8 -*-
"""selftest.py - 自检与确定性验证（不访问网络、不依赖正式案例）。

验证内容（对应 SKILL.md 自检清单的可执行部分）：
  1) 空输入目录 → 退出码 0，写合法 insufficient_evidence 结果；
  2) 坏 JSON / 坏编码 → 记录 errors/degradations，不崩溃；
  3) 骨架 schema：case_id / status / sources / uncertainty / change_log 齐备；
  4) output_contract 字段原样回填、缺值填 null/[]；
  5) 同一输入两次运行：确定性子集哈希一致 + CSV/SVG/report 逐字节一致；
  6) 去重/身份合并：同一论文的预印本与期刊版合并为一；
  7) 边方向规范化：forward/backward/unknown 正确判定；
  8) 最短路径与排序的确定性（两次调用结果一致）；
  9) citation_graph.svg 可被 XML 解析。
全部使用合成/示例数据，绝不涉及正式案例结果。
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


def _tmp_dir():
    return tempfile.mkdtemp(prefix="cittrace_selftest_")


def _write(path, text, binary=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if binary else "w"
    if binary:
        data = text if isinstance(text, bytes) else text.encode("utf-8")
        with open(path, mode) as fh:
            fh.write(data)
    else:
        with open(path, mode, encoding="utf-8") as fh:
            fh.write(text)


def _synthetic_input(root, case_override=None, bad_case=False):
    inp = os.path.join(root, "input")
    os.makedirs(inp, exist_ok=True)
    case = {
        "case_id": "selftest-case-001",
        "output_contract": {
            "literature_candidates": "list",
            "evidence_records": "list",
            "missing_note": "str",
        },
        "seed": {
            "id": "p-seed",
            "title": "Synthetic Seed Paper",
            "doi": "10.9999/synth.001",
            "authors": [{"family": "Doe", "given": "Jane"}],
            "year": 2020,
        },
        "research_question": "合成问题：沿种子做一层前后向追踪",
    }
    if case_override:
        case.update(case_override)
    if bad_case:
        _write(os.path.join(inp, "case.json"), '{"case_id": "broken", ', binary=True)
    else:
        _write(os.path.join(inp, "case.json"), json.dumps(case, ensure_ascii=False))
    _write(os.path.join(inp, "task.md"), "# 合成任务\n追踪种子论文的引用关系。\n")
    _write(os.path.join(inp, "materials", "refs.json"),
           json.dumps([
               {"id": "p-a-pre", "title": "Method A: a study", "doi": "10.1/a",
                "authors": [{"family": "Smith", "given": "A"}], "year": 2018},
               {"id": "p-a-jour", "title": "Method A: A Study", "doi": "10.1/A",
                "authors": [{"family": "Smith", "given": "A."}], "year": 2019},
               {"id": "p-b", "title": "Unrelated work", "doi": "10.2/b",
                "authors": [{"family": "Jones", "given": "B"}], "year": 2017},
           ], ensure_ascii=False))
    # 坏编码文件（GBK 字节，按 utf-8 会失败，触发编码回退）
    _write(os.path.join(inp, "materials", "legacy.txt"),
           "这是一段 GBK 编码的中文注释。".encode("gb18030"), binary=True)
    return inp


def _run_pipeline(inp):
    out = os.path.join(os.path.dirname(inp), "output")
    work = os.path.join(os.path.dirname(inp), "work")
    rc1 = core_impl.bootstrap(inp, out, work)
    rc2 = core_impl.finalize(out, work)
    rd = core_impl.read_text_tolerant(os.path.join(out, "result.json"))
    return out, work, rc1, rc2, json.loads(rd["text"])


def _read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def _test_empty_input():
    root = _tmp_dir()
    inp = os.path.join(root, "empty_input")
    os.makedirs(inp, exist_ok=True)
    out = os.path.join(root, "out")
    work = os.path.join(root, "work")
    rc = core_impl.bootstrap(inp, out, work)
    rd = core_impl.read_text_tolerant(os.path.join(out, "result.json"))
    result = json.loads(rd["text"])
    assert rc == 0, "空输入应退出码 0"
    assert result.get("status") == "insufficient_evidence", result.get("status")
    assert result.get("case_id"), "应含 case_id"
    assert isinstance(result.get("change_log"), list) and result["change_log"]
    return "空输入 → insufficient_evidence 且退出码 0"


def _test_bad_json():
    root = _tmp_dir()
    inp = _synthetic_input(root, bad_case=True)
    out = os.path.join(root, "out")
    work = os.path.join(root, "work")
    rc = core_impl.bootstrap(inp, out, work)
    result = json.loads(core_impl.read_text_tolerant(
        os.path.join(out, "result.json"))["text"])
    assert rc == 0, "坏 JSON 应退出码 0"
    assert result.get("status") == "input_error", result.get("status")
    assert any("JSON" in e for e in result.get("errors", [])), result.get("errors")
    return "坏 JSON → input_error 且 errors 已记录"


def _test_schema_and_contract():
    root = _tmp_dir()
    inp = _synthetic_input(root)
    out, work, rc1, rc2, result = _run_pipeline(inp)
    assert rc1 == 0 and rc2 == 0
    for key in ("case_id", "status", "sources", "uncertainty", "change_log",
                "pending_human", "limitations", "errors", "degradations",
                "results", "runtime"):
        assert key in result, "缺少字段 %s" % key
    # output_contract 字段原样回填
    assert result.get("literature_candidates") == []
    assert result.get("evidence_records") == []
    assert result.get("missing_note") is None
    assert result.get("case_id") == "selftest-case-001"
    assert result.get("seed") and result["seed"].get("doi")
    assert result.get("research_question")
    return "schema 与 output_contract 回填正确"


def _test_determinism():
    root = _tmp_dir()
    inp1 = _synthetic_input(os.path.join(root, "run1"))
    out1, work1, rc1a, rc1b, r1 = _run_pipeline(inp1)
    inp2 = _synthetic_input(os.path.join(root, "run2"))
    out2, work2, rc2a, rc2b, r2 = _run_pipeline(inp2)
    assert core_impl.content_hash(r1) == core_impl.content_hash(r2), \
        "两次运行确定性子集哈希不一致"
    for fname in ("papers.csv", "edges.csv", "citation_graph.svg", "report.md"):
        b1 = _read_bytes(os.path.join(out1, fname))
        b2 = _read_bytes(os.path.join(out2, fname))
        assert b1 == b2, "产物 %s 两次运行不一致" % fname
    return "两次运行 content_hash 与产物逐字节一致"


def _test_dedupe():
    papers = [
        {"id": "p-a-pre", "title": "Method A: a study", "doi": "10.1/a",
         "authors": [{"family": "Smith", "given": "A"}], "year": 2018},
        {"id": "p-a-jour", "title": "Method A: A Study", "doi": "10.1/A",
         "authors": [{"family": "Smith", "given": "A."}], "year": 2019},
        {"id": "p-b", "title": "Unrelated work", "doi": "10.2/b",
         "authors": [{"family": "Jones", "given": "B"}], "year": 2017},
    ]
    merged, evidence = core_impl.dedupe_papers(papers)
    assert len(merged) == 2, "应合并为 2 篇，实际 %d" % len(merged)
    assert len(evidence) == 1, "应有 1 条合并证据，实际 %d" % len(evidence)
    m = next(p for p in merged if "10.1/a" in str(p.get("doi", "")) or
             any("p-a" in str(v) for v in p.get("versions", [])))
    assert len(m.get("versions", [])) == 2, m.get("versions")
    return "同文不同版本（预印本/期刊版）合并为 1 条并留证据"


def _test_direction_and_path():
    edges = [
        {"source": "p-seed", "target": "p-method", "relation": "cites",
         "direction": "backward"},
        {"source": "p-followup", "target": "p-seed", "relation": "cites"},
        {"source": "p-x", "target": "p-y", "relation": "cites"},
    ]
    assert core_impl.normalize_edge_direction(edges[0], "p-seed") == "backward"
    assert core_impl.normalize_edge_direction(edges[1], "p-seed") == "forward"
    assert core_impl.normalize_edge_direction(edges[2], "p-seed") == "direction_unknown"
    path_edges = [
        {"source": "a", "target": "b", "relation": "cites"},
        {"source": "b", "target": "c", "relation": "cites"},
    ]
    assert core_impl.shortest_path(path_edges, "a", "c") == ["a", "b", "c"]
    assert core_impl.shortest_path(path_edges, "a", "a") == ["a"]
    assert core_impl.shortest_path(path_edges, "c", "a") == []
    return "边方向规范化与最短路径正确"


def _test_rank_determinism():
    papers = [
        {"id": "a", "title": "A", "year": 2020, "sources": [{"locator": "x", "kind": "crossref"}]},
        {"id": "b", "title": "B", "year": 2021},
        {"id": "c", "title": "C", "year": 2019},
    ]
    edges = [{"source": "a", "target": "b", "relation": "cites"}]
    r1 = core_impl.rank_papers(papers, edges, seed_id="a")
    r2 = core_impl.rank_papers(papers, edges, seed_id="a")
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)
    assert r1[0]["id"] == "a", "种子应排第一"
    return "排序两次调用结果一致且种子优先"


def _test_svg_parse():
    import xml.etree.ElementTree as ET
    svg = core_impl.render_svg_deterministic(
        [{"id": "a"}, {"id": "b"}],
        [{"source": "a", "target": "b", "relation": "cites"}],
        seed_id="a")
    ET.fromstring(svg.encode("utf-8"))
    return "citation_graph.svg 可解析"


def run(quiet=False):
    tests = [
        ("空输入", _test_empty_input),
        ("坏 JSON", _test_bad_json),
        ("schema/契约", _test_schema_and_contract),
        ("确定性", _test_determinism),
        ("去重合并", _test_dedupe),
        ("方向/路径", _test_direction_and_path),
        ("排序确定性", _test_rank_determinism),
        ("SVG 解析", _test_svg_parse),
    ]
    passed, failed = 0, 0
    lines = []
    for name, fn in tests:
        try:
            detail = fn()
            lines.append("[PASS] %s — %s" % (name, detail))
            passed += 1
        except Exception as exc:  # noqa: BLE001
            lines.append("[FAIL] %s — %s: %s" % (name, type(exc).__name__, exc))
            failed += 1
    summary = "selftest: %d passed, %d failed" % (passed, failed)
    lines.append(summary)
    if not quiet:
        print("\n".join(lines))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run())
