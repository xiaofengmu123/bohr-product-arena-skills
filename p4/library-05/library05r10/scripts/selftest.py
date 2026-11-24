#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 本地冒烟自检：五命令主路径、确定性、四类降级场景。

用法：
    python -B scripts/selftest.py

只依赖标准库。为规避 Windows 下子进程 stdio 管道限制，本脚本直接 import
pipeline/main 以进程内方式运行；评测运行时五个主命令仍由 Agent 以
`python scripts/main.py <cmd>` 方式独立执行。全部通过退出码 0，任一失败退出码 1。
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import pipeline  # noqa: E402

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("[%s] %s %s" % ("PASS" if ok else "FAIL", name, detail))


def make_fixture(base: Path) -> Path:
    """构造一个含重复/版本/文本/契约的最小文件库。"""
    inp = base / "input"
    (inp / "materials").mkdir(parents=True, exist_ok=True)
    pdf = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n%%EOF\n"
    (inp / "materials" / "paper.pdf").write_bytes(pdf)
    (inp / "materials" / "paper_copy.pdf").write_bytes(pdf)  # 重复
    (inp / "materials" / "notes_v1.txt").write_text(
        "alpha beta gamma\nversion one here\n", encoding="utf-8")
    (inp / "materials" / "notes_v2.txt").write_text(
        "alpha beta gamma\nversion two revised\n", encoding="utf-8")
    case = {
        "case_id": "smoke-case-1",
        "output_contract": ["file_index", "duplicates", "version_map", "search_answers"],
        "relations": [{"field": "alias", "value": {"F001": "F002"}}],
        "metadata": {"owner": "smoke"},
        "queries": ["Where is version one?"],
    }
    (inp / "case.json").write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
    return inp


def run_seal(inp: Path, work: Path, out: Path, judgments=None):
    facts = pipeline.scan(inp, work)
    pipeline.dump_json(facts, work / "facts.json")
    inferences = pipeline.infer(facts, work)
    pipeline.dump_json(inferences, work / "inferences.json")
    if judgments is None:
        judgments = {"case_id": facts.get("case_id"), "notes": [], "queries": []}
    report = pipeline.adjudicate(facts, inferences, judgments, work)
    runtime = {"command": "selftest", "duration_seconds": 0.0}
    result = pipeline.seal(facts, inferences, judgments, report, out, runtime)
    return result, facts, inferences, report


def test_main_path():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        inp = make_fixture(base)
        work, out = base / "work", base / "output"
        judgments = {
            "case_id": "smoke-case-1",
            "notes": [{"target": "F003", "kind": "version", "text": "notes 的 v1 版本",
                       "basis": "文件名含 v1", "confidence": "explicit",
                       "evidence": [{"source_id": "F003", "locator": "materials/notes_v1.txt",
                                     "quote": "version one here"}]}],
            "queries": [{"query_id": "Q1", "answer": "version one here",
                         "status": "answered",
                         "evidence": [{"source_id": "F003", "locator": "materials/notes_v1.txt",
                                       "quote": "version one here"}]}],
        }
        result, facts, inferences, report = run_seal(inp, work, out, judgments)
        ok = (result["status"] in ("ok", "partial")
              and result["case_id"] == "smoke-case-1"
              and any(c["claim_id"] for c in result["claims"])
              and (out / "result.json").exists()
              and (out / "library_index.json").exists()
              and (out / "evidence_map.json").exists()
              and (out / "inventory.md").exists())
        # 契约字段回填
        for k in ("file_index", "duplicates", "version_map", "search_answers"):
            ok = ok and k in result
        record("main_path_full", ok,
               "status=%s files=%d claims=%d" % (result["status"], len(facts["files"]), len(result["claims"])))


def test_determinism():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        inp = make_fixture(base)
        outs = []
        for i in (1, 2):
            work, out = base / ("work%d" % i), base / ("out%d" % i)
            run_seal(inp, work, out)
            outs.append(out)
        ok = True
        for name in ("library_index.json", "evidence_map.json", "inventory.md"):
            a = (outs[0] / name).read_bytes()
            b = (outs[1] / name).read_bytes()
            if a != b:
                ok = False
                record("determinism_" + name, False, "两次运行字节不一致")
        # result.json 除 runtime 外一致
        ra = json.loads((outs[0] / "result.json").read_text(encoding="utf-8"))
        rb = json.loads((outs[1] / "result.json").read_text(encoding="utf-8"))
        ra.pop("runtime", None)
        rb.pop("runtime", None)
        if ra != rb:
            ok = False
        record("determinism_result", ok, "除 runtime 外逐字节一致" if ok else "result.json 不一致")


def _degrade_case(name, case_bytes, expect_status, with_file=False):
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        inp = base / "input"
        inp.mkdir(parents=True)
        if case_bytes is not None:
            (inp / "case.json").write_bytes(case_bytes)
        (inp / "materials").mkdir(exist_ok=True)
        if with_file:
            (inp / "materials" / "a.txt").write_text("hello\n", encoding="utf-8")
        work, out = base / "work", base / "output"
        facts = pipeline.scan(inp, work)
        inferences = pipeline.infer(facts, work)
        report = {"notes": [], "queries": [], "summary": {}}
        result = pipeline.seal(facts, inferences, {"notes": [], "queries": []},
                               report, out, {"command": "selftest"})
        ok = result["status"] == expect_status and (out / "result.json").exists()
        record("degrade_" + name, ok, "status=%s" % result["status"])


def test_degradations():
    _degrade_case("empty", b'{"case_id":"x"}', "insufficient_evidence")
    _degrade_case("bad_json", b'{"case_id":', "input_error")
    # 缺 case.json：scan 会报 missing case.json，仍盘点材料，status=input_error
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        inp = base / "input"
        inp.mkdir()
        (inp / "materials").mkdir()
        (inp / "materials" / "a.txt").write_text("hello\n", encoding="utf-8")
        work, out = base / "work", base / "output"
        facts = pipeline.scan(inp, work)
        inferences = pipeline.infer(facts, work)
        result = pipeline.seal(facts, inferences, {"notes": [], "queries": []},
                               {"notes": [], "queries": [], "summary": {}},
                               out, {"command": "selftest"})
        record("degrade_missing_case", result["status"] == "input_error",
               "status=%s files=%d" % (result["status"], len(facts["files"])))
    _degrade_case("no_judgments", b'{"case_id":"x","output_contract":["files"]}', "partial", with_file=True)


def test_quote_grounding():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        inp = make_fixture(base)
        work = base / "work"
        facts = pipeline.scan(inp, work)
        corpus = pipeline.load_corpus(facts, work)
        by_id = {f["id"]: f for f in facts["files"]}
        # 找一个文本文件
        txt = next(f["id"] for f in facts["files"] if f.get("has_text_layer"))
        grounded = pipeline._quote_hit(txt, "version one here", by_id, corpus)[0]
        miss = pipeline._quote_hit(txt, "not present anywhere", by_id, corpus)[0]
        ok = grounded == "grounded" and miss == "ungrounded"
        record("quote_grounding", ok, "grounded=%s miss=%s" % (grounded, miss))


def main():
    print("== library-doc-triage selftest ==")
    tests = [test_main_path, test_determinism, test_quote_grounding, test_degradations]
    for t in tests:
        try:
            t()
        except Exception as exc:
            record(t.__name__, False, "异常: %s" % exc)
    failed = [r for r in RESULTS if not r[1]]
    print("== 结果：%d 通过 / %d 失败 ==" % (len(RESULTS) - len(failed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
