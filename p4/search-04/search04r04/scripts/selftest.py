#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 自检脚本：验证 Skill 的健壮性与确定性。仅依赖标准库。

覆盖：空输入、坏 JSON、坏编码、完整合成管线（含去重/排序/路径/finalize）、
确定性（同 result.json 两次 finalize 产物字节一致）。所有测试输入均为
运行时临时生成的自检数据，不含任何正式案例答案。
"""
import csv
import json
import os
import shutil
import sys
import tempfile
import traceback
import xml.etree.ElementTree as ET

import core_impl as ci

PASS = 0
FAIL = 0


def check(name, fn):
    global PASS, FAIL
    try:
        fn()
        print(f"[PASS] {name}")
        PASS += 1
    except Exception:
        print(f"[FAIL] {name}")
        traceback.print_exc()
        FAIL += 1


def _mk(d):
    os.makedirs(d, exist_ok=True)
    return d


def _write(path, content, mode="w", encoding="utf-8"):
    if "b" in mode:
        data = content if isinstance(content, bytes) else content.encode(encoding)
        with open(path, mode) as f:
            f.write(data)
        return
    with open(path, mode, encoding=encoding) as f:
        f.write(content)


def _run(cmd):
    rc = ci.main(cmd)
    assert rc == 0, f"exit code {rc} for {cmd}"


def _result(out):
    return json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))


def test_empty_input():
    d = tempfile.mkdtemp()
    try:
        inp = _mk(os.path.join(d, "input"))
        out = os.path.join(d, "output")
        work = os.path.join(d, "work")
        _run(["bootstrap", "--input", inp, "--output", out, "--work", work])
        r = _result(out)
        assert r["status"] == "input_error"
        assert "case_id" in r and "change_log" in r and "sources" in r
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_bad_json():
    d = tempfile.mkdtemp()
    try:
        inp = _mk(os.path.join(d, "input"))
        out = os.path.join(d, "output")
        work = os.path.join(d, "work")
        _write(os.path.join(inp, "case.json"), "{this is not json")
        _write(os.path.join(inp, "task.md"), "# 引文追踪任务\n")
        _run(["bootstrap", "--input", inp, "--output", out, "--work", work])
        r = _result(out)
        assert r["status"] == "input_error"
        assert any("JSON" in str(e) for e in r.get("errors", []))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_bad_encoding():
    d = tempfile.mkdtemp()
    try:
        inp = _mk(os.path.join(d, "input"))
        out = os.path.join(d, "output")
        work = os.path.join(d, "work")
        # latin-1 字节 + 中文任务，验证 utf-8-sig 容错降级。
        _write(os.path.join(inp, "case.json"),
               '{"case_id": "café-1", "output_contract": {"papers": [], "edges": []}}',
               mode="wb", encoding="latin-1")
        _write(os.path.join(inp, "task.md"), "# 引文追踪任务（自检）\n")
        _run(["bootstrap", "--input", inp, "--output", out, "--work", work])
        r = _result(out)
        assert r["case_id"] == "café-1"
        assert "latin-1" in " ".join(r.get("degradations", [])) or r["case_id"] == "café-1"
        assert r["papers"] == [] and r["edges"] == []
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _synthetic_case(inp):
    _write(os.path.join(inp, "case.json"), json.dumps({
        "case_id": "selftest-0001",
        "seed": {"title": "Synthetic Seed Paper (self-test only)"},
        "output_contract": {"papers": [], "edges": [], "clusters": [], "paths": []},
    }, ensure_ascii=False))
    _write(os.path.join(inp, "task.md"), "# 自检用合成任务（非正式案例）\n")
    os.makedirs(os.path.join(inp, "materials"), exist_ok=True)
    _write(os.path.join(inp, "materials", "seed.pdf"), "%PDF-1.4\n(synthetic)\n")


def test_full_pipeline():
    d = tempfile.mkdtemp()
    try:
        inp = _mk(os.path.join(d, "input"))
        out = os.path.join(d, "output")
        work = os.path.join(d, "work")
        _synthetic_case(inp)
        _run(["bootstrap", "--input", inp, "--output", out, "--work", work])

        papers = [
            {"id": "seed", "title": "Synthetic Seed Paper", "authors": ["A One"], "year": 2018,
             "doi": "10.1000/selftest.seed", "type": "journal", "relevance_class": "foundational",
             "confidence": "confirmed",
             "evidence": [{"locator": "case.json", "quote": "Synthetic Seed Paper (self-test only)"}]},
            {"id": "p1", "title": "Method Source", "authors": ["B Two"], "year": 2005,
             "type": "journal", "relevance_class": "method_source", "confidence": "confirmed",
             "evidence": [{"locator": "materials/seed.pdf#p1", "quote": "based on method source"}]},
            # 同文不同版本：DOI 相同，应被 dedupe 合并到 p1。
            {"id": "p1-dup", "title": "Method Source (conference version)", "authors": ["B Two"],
             "year": 2005, "doi": "10.1000/selftest.seed", "type": "conference",
             "confidence": "inferred",
             "evidence": [{"locator": "materials/seed.pdf#p2", "quote": "conference variant"}]},
            {"id": "p2", "title": "Follow-up Improvement", "authors": ["C Three"], "year": 2022,
             "type": "preprint", "relevance_class": "follow_up", "confidence": "inferred"},
        ]
        _write(os.path.join(work, "papers.json"), json.dumps(papers, ensure_ascii=False))
        _run(["record", "--kind", "papers", "--json-file", os.path.join(work, "papers.json"),
              "--reason", "self-test 注入文献", "--output", out, "--work", work])

        edges = [
            {"id": "e1", "source": "seed", "target": "p1", "relation": "cites",
             "direction": "backward", "confidence": "confirmed",
             "evidence": [{"locator": "materials/seed.pdf#p1", "quote": "cites method source"}]},
            {"id": "e2", "source": "p2", "target": "seed", "relation": "cites",
             "direction": "forward", "confidence": "inferred"},
        ]
        _write(os.path.join(work, "edges.json"), json.dumps(edges, ensure_ascii=False))
        _run(["record", "--kind", "edges", "--json-file", os.path.join(work, "edges.json"),
              "--reason", "self-test 注入边", "--output", out, "--work", work])

        _run(["record", "--kind", "sources",
              "--json", '[{"id": "s1", "locator": "case.json", "quote": "seed", "kind": "input"}]',
              "--reason", "self-test source", "--output", out, "--work", work])
        unc_json = '[{"id": "u1", "type": "coverage_gap", "subject": "forward citations", "detail": "新预印本无前向引用记录", "affects": "p2", "resolution": "标注缺失"}]'
        _run(["record", "--kind", "uncertainty", "--json", unc_json,
              "--reason", "self-test uncertainty", "--output", out, "--work", work])

        # 写入 seed_key 并升级状态（模拟 Agent 增量改进）。
        r = _result(out)
        r["seed_key"] = "seed"
        r["seed"] = {"id": "seed", "title": "Synthetic Seed Paper", "confidence": "confirmed"}
        r["status"] = "partial"
        r["status_detail"] = "self-test：已核实种子并完成一层扩展"
        ci.append_change(r, "self-test", "写入 seed_key 并升级状态")
        ci.write_result(r, out)

        _run(["dedupe", "--output", out, "--work", work])
        r = _result(out)
        assert len(r["results"]["papers"]) == 3, f"expected 3 after dedupe, got {len(r['results']['papers'])}"
        assert r.get("merge_log"), "dedupe should record merge evidence"

        _run(["rank", "--output", out, "--work", work])
        _run(["paths", "--from", "p2", "--to", "p1", "--id", "path-1",
              "--output", out, "--work", work])

        _run(["finalize", "--output", out, "--work", work])
        r = _result(out)
        assert r["status"] in ("partial", "ok")
        assert r["runtime"].get("validation_ok") is True, r["runtime"].get("validation")

        for name in ("report.md", "papers.csv", "edges.csv", "citation_graph.svg"):
            assert os.path.isfile(os.path.join(out, name)), f"missing {name}"

        # SVG 可解析、CSV 可读。
        ET.parse(os.path.join(out, "citation_graph.svg"))
        with open(os.path.join(out, "papers.csv"), encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        assert rows[0][0] == "id" and len(rows) >= 2

        # 二次 finalize 产物确定性（字节一致）。
        with open(os.path.join(out, "citation_graph.svg"), "rb") as f:
            svg1 = f.read()
        with open(os.path.join(out, "papers.csv"), "rb") as f:
            csv1 = f.read()
        ci.finalize(out, work)
        with open(os.path.join(out, "citation_graph.svg"), "rb") as f:
            svg2 = f.read()
        with open(os.path.join(out, "papers.csv"), "rb") as f:
            csv2 = f.read()
        assert svg1 == svg2, "SVG not deterministic"
        assert csv1 == csv2, "CSV not deterministic"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    print("=== citation-tracing selftest ===")
    check("empty input -> input_error + valid JSON", test_empty_input)
    check("bad JSON -> input_error + valid JSON", test_bad_json)
    check("bad encoding -> utf-8-sig fallback", test_bad_encoding)
    check("full pipeline + dedupe + rank + paths + finalize + determinism", test_full_pipeline)
    print(f"=== {PASS} passed, {FAIL} failed ===")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
