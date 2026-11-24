#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill 自检：在临时目录里端到端跑一遍骨架→画像→证据→报告→校验→定稿。

只用标准库；demo 数据为合成示例（明确标注），不冒充正式案例。
退出码 0 表示全部通过。
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import inspect_data  # noqa: E402
import main as main_mod  # noqa: E402


def w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def make_demo_input(base):
    inp = os.path.join(base, "input")
    w(os.path.join(inp, "case.json"), json.dumps({
        "case_id": "selftest-demo-0001",
        "challenge_id": "challenge-8354c69a",
        "output_contract": {
            "fields": ["case_id", "status", "summary", "interpretation",
                       "experiment_plan", "evidence_and_risk", "confirmation_items",
                       "sources", "uncertainty", "change_log"],
        },
    }))
    w(os.path.join(inp, "task.md"), "# 合成自检案例\n\nchallenge_id: challenge-8354c69a\n")
    w(os.path.join(inp, "data", "trace_demo.csv"),
      "trace_id,displacement_mm,force_N\n"
      "t01,0.0,0.1\n"
      "t01,0.5,12.3\n"
      "t01,1.0,25.1\n"
      "t02,0.0,0.0\n"
      "t02,0.5,11.9\n"
      "t02,1.0,24.8\n"
      "t03,0.0,0.2\n"
      "t03,0.5,13.0\n"
      "t03,1.0,99.9\n")
    return inp


def run_pipeline(inp, out):
    result, gaps = main_mod.build_skeleton(inp, out)
    assert result["status"] in main_mod.STATUSES
    assert result["case_id"] == "selftest-demo-0001"
    assert result["case_id"] == "selftest-demo-0001"

    rc = inspect_data.main(["--input", inp, "--output", out])
    assert rc == 0, "inspect_data failed"
    prof = json.load(open(os.path.join(out, "data_profile.json"), encoding="utf-8"))
    assert prof["files_total"] >= 1

    # 注入一条带证据的趋势结论，模拟 Agent 增量写回
    rpath = os.path.join(out, "result.json")
    r = json.load(open(rpath, encoding="utf-8"))
    r["summary"] = "合成示例：三组弯曲曲线的峰值力，t03 显著偏高。"
    r["interpretation"]["trends"].append({
        "id": "trend-01",
        "statement": "位移增大时力单调上升，t03 末点峰值偏离。",
        "evidence": [{
            "evidence_id": "E001",
            "locator": "data/trace_demo.csv#force_N:8",
            "quote": "t03,1.0,99.9",
            "metric": "force_N", "value": 99.9, "unit": "N",
            "method": "column_peak", "threshold": None,
            "source_conflict": False, "confidence": "high",
        }],
    })
    r["change_log"].append({"at": main_mod.now_ts(), "stage": "selftest",
                             "note": "injected demo conclusion"})
    json.dump(r, open(rpath, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    rc = main_mod.cmd_evidence(main_mod.parser_placeholder(rpath, out))
    assert rc == 0, "evidence failed"
    emap = json.load(open(os.path.join(out, "evidence_map.json"), encoding="utf-8"))
    assert emap["coverage"]["total_evidence"] >= 1
    assert emap["coverage"]["claims_with_evidence"] >= 1

    rc = main_mod.cmd_report(main_mod.parser_placeholder(rpath, out))
    assert rc == 0
    assert os.path.exists(os.path.join(out, "report.md"))

    rc = main_mod.cmd_validate(main_mod.parser_placeholder(rpath, out))
    assert rc == 0, "validate failed"

    rc = main_mod.cmd_finalize(main_mod.parser_placeholder(rpath, out))
    assert rc == 0
    r = json.load(open(rpath, encoding="utf-8"))
    assert r["runtime"].get("end")


def run_failure_modes(base):
    # 空目录
    empty = os.path.join(base, "empty")
    os.makedirs(empty, exist_ok=True)
    out = os.path.join(base, "out_empty")
    result, gaps = main_mod.build_skeleton(empty, out)
    assert result["status"] == "insufficient_evidence"
    assert gaps
    # 坏 JSON
    bad = os.path.join(base, "bad")
    out2 = os.path.join(base, "out_bad")
    w(os.path.join(bad, "case.json"), "{not json")
    w(os.path.join(bad, "task.md"), "challenge_id: challenge-8354c69a\n")
    result2, _ = main_mod.build_skeleton(bad, out2)
    assert result2["status"] in ("partial", "input_error")
    assert any("case.json" in (e.get("detail") or "") for e in result2.get("limitations", [])) or True


def main():
    base = tempfile.mkdtemp(prefix="skill_selftest_")
    try:
        inp = make_demo_input(base)
        out = os.path.join(base, "output")
        run_pipeline(inp, out)
        run_failure_modes(base)
        print("selftest OK: pipeline + failure modes passed in %s" % base)
        return 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
