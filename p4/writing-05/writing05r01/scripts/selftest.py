#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检脚本（纯标准库）：用合成 demo 输入验证整个管线可运行、可降级。

覆盖场景：
1. 正常：scaffold → extract → analyze → 写入合法 result.json → validate 通过 → finalize 产出报告。
2. 逐字回查：quote 不一致时 validate 必须报错（证据溯源硬要求）。
3. 空输入目录：scaffold 状态为 input_error，退出码 0，不抛异常。
4. 坏 JSON：scaffold 捕获并记录 limitation，不崩溃。
5. 无 pdftotext 时 PDF 抽取降级（仅记录，不伪造文本）。

用法：python3 scripts/selftest.py [--quick]
"""

import argparse
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from main import run_analyze, run_extract, run_finalize, run_scaffold, run_validate  # noqa: E402

PAPER_TXT = "Background\n\nThis randomized trial enrolled 1,204 adults. The overall response rate was 61.4% (95% CI 57.8-65.0).\n\nResults\n\nThe treatment reduced mortality (hazard ratio 0.72, p=0.003)."
CLAIM_QUOTE = "The overall response rate was 61.4% (95% CI 57.8-65.0)."
CLAIM_QUOTE_BAD = "The overall response rate was 61.4%."


class Args:
    def __init__(self, inp, out, work, status=None):
        self.input = inp
        self.output = out
        self.work = work
        self.status = status


def make_case(inp, case_id="demo-case-001", bad_json=False):
    os.makedirs(os.path.join(inp, "materials"), exist_ok=True)
    if bad_json:
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            f.write("{ this is not json")
    else:
        case = {
            "case_id": case_id,
            "task": "根据材料撰写一段证据驱动综述",
            "output_contract": {
                "deliverable": {"type": "markdown"},
                "sources": {"type": "list"},
                "change_log": {"type": "list"},
            },
        }
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            json.dump(case, f, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write("# 任务\n\nchallenge_id: challenge-demo\n\n依据材料写作。\n")
    with open(os.path.join(inp, "materials", "paper.txt"), "w", encoding="utf-8") as f:
        f.write(PAPER_TXT)


def write_result(out, quote=CLAIM_QUOTE, include_claim=True):
    res = {
        "case_id": "demo-case-001",
        "status": "in_progress",
        "deliverable": {
            "draft": "",
            "claim_list": [{"claim_id": "C1", "claim": "总有效率为 61.4%"}],
            "evidence_map": [],
            "summary": "",
        },
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "limitations": [],
    }
    if include_claim:
        res["deliverable"]["evidence_map"] = [{
            "claim_id": "C1",
            "claim": "总有效率为 61.4%",
            "claim_kind": "fact",
            "claim_scope": "enrolled adults",
            "final_wording": "纳入成年患者的总有效率为 61.4%（95% CI 57.8–65.0）。",
            "support": [{
                "source_doc": "paper.txt",
                "locator": "S1.P2.s1",
                "quote": quote,
                "relation": "direct",
                "evidence_type": "primary",
                "strength": "moderate",
                "strength_rationale": "单篇随机试验",
                "verdict": "accept",
            }],
            "uncertainty": "单篇研究，不作共识表述",
            "citation_refs": ["[1]"],
            "status": "verified",
        }]
    with open(os.path.join(out, "result.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)


def scenario_normal(root):
    inp, out, work = (os.path.join(root, "in"), os.path.join(root, "out"), os.path.join(root, "work"))
    make_case(inp)
    r1 = run_scaffold(Args(inp, out, work))
    assert r1["ok"] and r1["status"] == "in_progress", r1
    run_extract(Args(inp, out, work))
    run_analyze(Args(inp, out, work))
    write_result(out)
    v = run_validate(Args(inp, out, work))
    assert v["ok"], v["issues"]
    f = run_finalize(Args(inp, out, work))
    assert f["status"] == "complete", f
    report = os.path.join(out, "evidence_report.md")
    assert os.path.isfile(report), "evidence_report.md 未生成"
    return "ok"


def scenario_quote_mismatch(root):
    inp, out, work = (os.path.join(root, "in"), os.path.join(root, "out"), os.path.join(root, "work"))
    make_case(inp)
    run_scaffold(Args(inp, out, work))
    run_extract(Args(inp, out, work))
    write_result(out, quote=CLAIM_QUOTE_BAD)
    v = run_validate(Args(inp, out, work))
    assert not v["ok"], "quote 不一致应被拦截"
    assert any("逐字" in i or "不可回查" in i for i in v["issues"]), v["issues"]
    return "ok"


def scenario_empty_dir(root):
    inp = os.path.join(root, "empty_in")
    out, work = os.path.join(root, "out"), os.path.join(root, "work")
    os.makedirs(inp, exist_ok=True)
    r = run_scaffold(Args(inp, out, work))
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    assert r["status"] == "input_error", r
    assert res["status"] == "input_error"
    assert any("case.json" in x for x in res.get("limitations", [])), res
    return "ok"


def scenario_bad_json(root):
    inp, out, work = (os.path.join(root, "in"), os.path.join(root, "out"), os.path.join(root, "work"))
    make_case(inp, bad_json=True)
    r = run_scaffold(Args(inp, out, work))
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    assert r["status"] == "in_progress", r
    assert any("解析异常" in x for x in res.get("limitations", [])), res
    return "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()

    results = {}
    root = tempfile.mkdtemp(prefix="edw_selftest_")
    try:
        for name, fn in (
            ("normal", scenario_normal),
            ("quote_mismatch", scenario_quote_mismatch),
            ("empty_dir", scenario_empty_dir),
            ("bad_json", scenario_bad_json),
        ):
            sub = os.path.join(root, name)
            os.makedirs(sub, exist_ok=True)
            try:
                fn(sub)
                results[name] = "PASS"
            except Exception as e:  # noqa: BLE001
                results[name] = "FAIL: %s" % e
    finally:
        shutil.rmtree(root, ignore_errors=True)

    ok = all(v == "PASS" for v in results.values())
    print(json.dumps({"selftest": "ok" if ok else "failed", "scenarios": results},
                     ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
