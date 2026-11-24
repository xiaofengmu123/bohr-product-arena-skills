#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 作者侧自检（不触碰 /input，不预写任何正式测试答案）。

用临时目录跑端到端场景，断言：空输入、缺输入、坏 JSON、output_contract 回填、
机械预审数值冲突、逐字 quote 命中/未命中、--help 退出码 0。
退出码恒为 0，结论以 PASS/FAIL 打印（作者本地核验用，运行时无需调用）。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import main as M  # noqa: E402

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def tmpdir():
    return Path(tempfile.mkdtemp(prefix="pqr-selftest-"))


@case("空输入目录 -> insufficient_evidence 且退出码 0")
def t_empty():
    root = tmpdir()
    inp, work, out = root / "input", root / "work", root / "output"
    inp.mkdir()
    rc = M.do_init(inp, work, out, False)
    assert rc == 0
    r = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert r["status"] == "insufficient_evidence", r["status"]
    assert r["result"]["findings"] == []


@case("缺输入目录 -> insufficient_evidence")
def t_missing():
    root = tmpdir()
    rc = M.do_init(root / "nonexistent-input", root / "work", root / "output", False)
    assert rc == 0
    r = json.loads((root / "output" / "result.json").read_text(encoding="utf-8"))
    assert r["status"] == "insufficient_evidence"


@case("坏 JSON case.json -> input_error 且记录 errors")
def t_badjson():
    root = tmpdir()
    inp, work, out = root / "input", root / "work", root / "output"
    inp.mkdir()
    (inp / "case.json").write_text("{ not json", encoding="utf-8")
    rc = M.do_init(inp, work, out, False)
    assert rc == 0
    r = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert r["status"] == "input_error", r["status"]
    assert any("bad JSON" in str(e.get("error", "")) for e in r["errors"])


@case("output_contract 全部键原样回填")
def t_contract():
    root = tmpdir()
    inp, work, out = root / "input", root / "work", root / "output"
    inp.mkdir()
    (inp / "case.json").write_text(json.dumps({
        "case_id": "c-42", "task_instruction": "审核",
        "output_contract": {"metric": "findings", "unknown_key": None, "list_key": []},
    }), encoding="utf-8")
    (inp / "paper.txt").write_text(
        "This is a sample abstract.\n\nWe tested N = 32 patients.\n\n"
        "Methods N = 35 subjects were enrolled.\n\nConclusion works.\n\n"
        "References [1] Smith 2020.", encoding="utf-8")
    M.do_init(inp, work, out, False)
    r = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert r["case_id"] == "c-42"
    assert r["metric"] == "findings"
    assert "unknown_key" in r and r["unknown_key"] is None
    assert "list_key" in r and r["list_key"] == []


@case("机械预审发现数值冲突（fact_check）")
def t_numeric():
    root = tmpdir()
    inp, work, out = root / "input", root / "work", root / "output"
    inp.mkdir()
    (inp / "case.json").write_text(json.dumps({"case_id": "c-1"}), encoding="utf-8")
    (inp / "paper.txt").write_text(
        "We enrolled N = 32 patients in cohort A.\n\n"
        "We enrolled N = 35 patients in cohort B.\n", encoding="utf-8")
    M.do_init(inp, work, out, False)
    r = json.loads((out / "result.json").read_text(encoding="utf-8"))
    facets = {f["facet"] for f in r["result"]["findings"]}
    assert "fact_check" in facets, facets
    assert any(f["verdict"] == "to_review" and "32" in f["claim"] for f in r["result"]["findings"])


@case("逐字 quote 校验：命中通过 / 未命中报 ERROR")
def t_quote():
    root = tmpdir()
    inp, work, out = root / "input", root / "work", root / "output"
    inp.mkdir()
    (inp / "case.json").write_text(json.dumps({"case_id": "c-1"}), encoding="utf-8")
    (inp / "paper.txt").write_text(
        "The p-value was less than 0.05 in the primary analysis.\n", encoding="utf-8")
    M.do_init(inp, work, out, False)
    draft = M._load_json(work / "draft.json")
    draft["findings"].append({
        "finding_id": "F01", "facet": "statistics_report", "severity": "minor",
        "issue_type": "insufficient_evidence", "verdict": "to_review",
        "claim": "未报告效应量与置信区间", "location": "D1#para1",
        "quote": "The p-value was less than 0.05", "rationale": "test",
        "revision_suggestion": "补充效应量", "confidence": "high", "evidence": [],
    })
    M._save_json(work / "draft.json", draft)
    M.do_check(inp, work, out)
    rep = M._load_json(work / "check_report.json")
    assert not any("quote not found" in e for e in rep["errors"]), rep["errors"]
    draft = M._load_json(work / "draft.json")
    draft["findings"][0]["quote"] = "THIS SENTENCE DOES NOT EXIST"
    M._save_json(work / "draft.json", draft)
    M.do_check(inp, work, out)
    rep = M._load_json(work / "check_report.json")
    assert any("quote not found" in e for e in rep["errors"]), rep["errors"]


@case("build 后 review_report.md 存在且非空")
def t_build():
    root = tmpdir()
    inp, work, out = root / "input", root / "work", root / "output"
    inp.mkdir()
    (inp / "case.json").write_text(json.dumps({"case_id": "c-1"}), encoding="utf-8")
    (inp / "paper.txt").write_text("Some claims without evidence here.\n\n"
                                   "Another paragraph about results N = 10 and N = 20.\n",
                                   encoding="utf-8")
    M.do_init(inp, work, out, False)
    M.do_build(inp, work, out)
    assert (out / "review_report.md").exists()
    assert (out / "review_report.md").stat().st_size > 0
    r = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert "change_log" in r and len(r["change_log"]) >= 1


@case("--help 退出码 0")
def t_help():
    raised = False
    try:
        M.main(["--help"])
    except SystemExit as exc:
        raised = True
        assert exc.code == 0
    assert raised


def run_all():
    failures = []
    for name, fn in CASES:
        try:
            fn()
            print(f"[PASS] {name}")
        except AssertionError as exc:
            failures.append(name)
            print(f"[FAIL] {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures.append(name)
            print(f"[FAIL] {name}: unexpected {type(exc).__name__}: {exc}")
    print(f"selftest: {len(CASES) - len(failures)}/{len(CASES)} 通过")
    return 0


if __name__ == "__main__":
    sys.exit(run_all())
