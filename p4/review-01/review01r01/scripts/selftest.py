#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""paper-quality-review Skill 自检脚本（仅标准库）。

在临时目录构造三个合成场景（正常样例 / 空目录 / 坏 JSON），跑通
init -> show -> check -> build -> selfcheck 全链路，断言：
  1. 每个场景退出码均为 0，且都产出合法 result.json；
  2. 正常样例的 quote 逐字命中、六类 facet 全部覆盖、evidence_map 一致；
  3. 空目录返回 insufficient_evidence，坏 JSON 返回 input_error/partial；
  4. --help 退出码 0。

说明：合成样例文本在脚本内生成，仅用于机械自检，不含任何真实/预制答案，
也不会写入 outputs/ 目录（全部落在系统临时目录并在结束后删除）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "scripts" / "main.py"

FACETS = ["fact_check", "citation_support", "method_design",
          "statistics_report", "argument_chain", "academic_norms"]

SAMPLE_CASE = {
    "case_id": "selftest-case-001",
    "task_instruction": "对给定论文材料完成质量、引用、事实、方法、统计、逻辑与规范的逐条审查。",
    "output_contract": {
        "finding_list": [],
        "report_path": None,
        "audit_notes": None,
    },
}

SAMPLE_TASK = "# 论文质量审查（自检用合成样例，非正式案例）\n对材料逐条审查并给出定位与逐字证据。\n"

# 六类 facet 各留一段可命中文本，让 check 能验证逐字命中与覆盖
SAMPLE_PAPER = """\
# 样例论文（合成，仅自检）

## 摘要

本文声称方法在三个数据集上取得了 95% 的准确率，且显著优于基线方法。

## 引言

已有研究表明该问题长期存在挑战（文献 [1]）。

## 方法

我们随机把样本分为训练集和测试集，但没有设置对照实验，也没有报告样本量。

## 结果

方法在测试集上达到 95% 准确率，但正文没有报告置信区间、误差棒或显著性检验。

## 讨论

因为结果优于基线，所以本方法一定适用于所有真实场景，无需更多验证。

## 规范

本文未声明数据可用性、伦理批准与利益冲突。图 1 未标注来源。
"""

# 一条候选 finding 用于自检 check 的逐字命中（quote 与 SAMPLE_PAPER 严格一致）
SAMPLE_DRAFT = {
    "status": "partial",
    "findings": [
        {
            "finding_id": "F01",
            "facet": "statistics_report",
            "severity": "major",
            "issue_type": "insufficient_evidence",
            "verdict": "to_review",
            "claim": "结果段落报告了准确率但缺少置信区间/误差/显著性报告。",
            "location": "para9",
            "quote": "方法在测试集上达到 95% 准确率，但正文没有报告置信区间、误差棒或显著性检验。",
            "rationale": "SR-1：显著性/误差报告缺失；措辞为报告不全而非方法缺陷。",
            "revision_suggestion": "补充效应量、置信区间与显著性检验结果，并说明重复次数。",
            "confidence": "high",
            "evidence": [
                {
                    "evid_id": "E01", "role": "data_point", "source_id": "D1",
                    "locator": "para9",
                    "quote": "方法在测试集上达到 95% 准确率，但正文没有报告置信区间、误差棒或显著性检验。",
                    "relation": "supports",
                }
            ],
        }
    ],
    "unchecked_items": [
        {"facet": "fact_check", "reason": "合成样例未提供外部事实来源，无法核验，标记未检查。"},
        {"facet": "citation_support", "reason": "参考文献列表缺失，引用 [1] 无法核验。"},
        {"facet": "method_design", "reason": "合成样例已由 F 之外条目覆盖；此处示例保留未检查。"},
        {"facet": "argument_chain", "reason": "示例保留未检查，说明 coverage 允许 finding+unchecked 合并覆盖。"},
        {"facet": "academic_norms", "reason": "示例保留未检查。"},
    ],
}


def run(cmd, *args, cwd=None, check_exit=True):
    proc = subprocess.run(
        [sys.executable, "-B", str(MAIN), cmd, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd,
    )
    if check_exit and proc.returncode != 0:
        raise AssertionError(f"{cmd} exit={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}")
    return proc


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def scenario_normal(root):
    inp = root / "in"; work = root / "work"; out = root / "out"
    inp.mkdir(); work.mkdir(); out.mkdir()
    (inp / "case.json").write_text(json.dumps(SAMPLE_CASE, ensure_ascii=False), encoding="utf-8")
    (inp / "task.md").write_text(SAMPLE_TASK, encoding="utf-8")
    (inp / "paper.md").write_text(SAMPLE_PAPER, encoding="utf-8")
    run("init", "--input", str(inp), "--work", str(work), "--output", str(out))
    assert (out / "result.json").exists(), "init must write result.json skeleton"
    run("show", "--input", str(inp), "--work", str(work), "--output", str(out), "--max-chars", "5000")
    (work / "draft.json").write_text(json.dumps(SAMPLE_DRAFT, ensure_ascii=False), encoding="utf-8")
    run("check", "--input", str(inp), "--work", str(work), "--output", str(out))
    cr = load(work / "check_report.json")
    assert cr["ok"], f"check should pass on self-contained sample: {cr['errors']}"
    run("build", "--input", str(inp), "--work", str(work), "--output", str(out))
    r = load(out / "result.json")
    assert r["case_id"] == "selftest-case-001", "case_id must be preserved"
    assert r["status"] in ("ok", "partial"), f"unexpected status {r['status']}"
    # output_contract 全部字段回填
    for k in SAMPLE_CASE["output_contract"]:
        assert k in r, f"contract key {k} missing"
    assert r.get("evidence_map"), "evidence_map required"
    em = r["evidence_map"].get("F01")
    assert em and em["evidence_count"] == 1, "evidence_map must mirror finding.evidence"
    vs = r["result"]["verdict_summary"]["coverage"]
    covered = set(vs["checked_facets"]) | set(vs["unchecked_facets"])
    assert covered == set(FACETS), f"six facets must be covered, got {covered}"
    assert (out / "review_report.md").exists(), "review_report.md required"
    run("selfcheck", "--input", str(inp), "--work", str(work), "--output", str(out))
    sc = load(work / "selfcheck.json")
    assert sc["ok"], f"selfcheck should pass: {sc['errors']}"
    return "ok"


def scenario_empty(root):
    inp = root / "in"; work = root / "work"; out = root / "out"
    inp.mkdir(); work.mkdir(); out.mkdir()
    run("init", "--input", str(inp), "--work", str(work), "--output", str(out))
    r = load(out / "result.json")
    assert r["status"] == "insufficient_evidence", f"got {r['status']}"
    assert r["case_id"] is None
    return "ok"


def scenario_bad_json(root):
    inp = root / "in"; work = root / "work"; out = root / "out"
    inp.mkdir(); work.mkdir(); out.mkdir()
    (inp / "case.json").write_text("{not valid json", encoding="utf-8")
    (inp / "task.md").write_text(SAMPLE_TASK, encoding="utf-8")
    (inp / "paper.md").write_text(SAMPLE_PAPER, encoding="utf-8")
    run("init", "--input", str(inp), "--work", str(work), "--output", str(out))
    r = load(out / "result.json")
    assert r["status"] in ("partial", "input_error"), f"got {r['status']}"
    return "ok"


def main():
    if not MAIN.exists():
        print("FAIL: scripts/main.py not found")
        return 1
    help_proc = subprocess.run([sys.executable, "-B", str(MAIN), "--help"],
                               capture_output=True, text=True, encoding="utf-8")
    assert help_proc.returncode == 0, "--help must exit 0"
    tmp = Path(tempfile.mkdtemp(prefix="pqr_selftest_"))
    results = {}
    try:
        for name, fn in (("normal", scenario_normal), ("empty", scenario_empty), ("bad_json", scenario_bad_json)):
            root = tmp / name
            root.mkdir()
            try:
                fn(root)
                results[name] = "PASS"
            except Exception as exc:
                results[name] = f"FAIL: {exc}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for name, status in results.items():
        print(f"[selftest] {name}: {status}")
    return 0 if all(v == "PASS" for v in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
