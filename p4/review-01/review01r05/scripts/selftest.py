#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""论文质量审查 Skill 自检脚本（仅标准库，不随包携带正式测试产物）。

运行时在系统临时目录创建最小合成输入，验证：
  1. --help 退出码 0
  2. init 扫描合成材料并落盘合法 partial result.json
  3. check 对逐字 quote 与契约对齐给出正确 PASS/FAIL
  4. build 生成 result.json + review_report.md，契约字段一一回填
  5. selfcheck 复检通过
  6. 空目录 → insufficient_evidence，坏 JSON → input_error，均退出码 0

合成材料为通用示意文本，与任何正式案例无关，仅用于验证接口契约。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAIN = HERE / "main.py"


def run(args, cwd=None):
    return subprocess.run(
        [sys.executable, "-B", str(MAIN)] + args,
        cwd=str(cwd or HERE), capture_output=True, text=True,
        encoding="utf-8", errors="replace")


def write(path, text):
    path.write_text(text, encoding="utf-8", newline="\n")


def make_case(tmp: Path):
    case = {
        "case_id": "selftest-case-001",
        "task_instruction": "审查示例材料中的事实与论证问题",
        "output_contract": {
            "review_report": None,
            "verdict": None,
            "meta": {"reviewer": None, "round": None},
        },
    }
    write(tmp / "case.json", json.dumps(case, ensure_ascii=False))
    write(tmp / "task.md", "# 自检任务\n请逐条审查并给出证据。\n")
    paper = (
        "标题：示例研究\n\n"
        "摘要：本研究表明方法 A 显著优于方法 B，准确率提升 12%。\n\n"
        "方法：共纳入 100 例样本，未说明是否设对照与随机化。\n\n"
        "结果：两组差异具有统计学意义（p < 0.05）。\n\n"
        "结论：方法 A 可广泛应用于所有临床场景。\n"
    )
    write(tmp / "materials" / "paper.md", paper)


def check(case_dir, expect_status, label):
    work = case_dir / "work"
    out = case_dir / "out"
    r = run(["init", "--input", str(case_dir), "--work", str(work),
             "--output", str(out)])
    if r.returncode != 0:
        return False, f"{label}: init exit {r.returncode}"
    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    if result.get("status") != expect_status:
        return False, (f"{label}: status={result.get('status')} "
                       f"want {expect_status}")
    return True, f"{label}: ok (status={result.get('status')})"


def main():
    results = []
    # 1) --help
    r = run(["--help"])
    results.append(("help exit 0", r.returncode == 0,
                    f"exit={r.returncode}"))

    tmp = Path(tempfile.mkdtemp(prefix="paper_audit_selftest_"))
    case_dir = tmp / "case1"
    (case_dir / "materials").mkdir(parents=True)
    make_case(case_dir)
    work = case_dir / "work"
    out = case_dir / "out"

    # 2) init
    r = run(["init", "--input", str(case_dir), "--work", str(work),
             "--output", str(out)])
    results.append(("init exit 0", r.returncode == 0, f"exit={r.returncode}"))
    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    results.append(("init partial skeleton",
                    result.get("status") == "partial",
                    f"status={result.get('status')}"))
    results.append(("case_id preserved",
                    result.get("case_id") == "selftest-case-001",
                    f"case_id={result.get('case_id')}"))

    # 3) draft 填写 + check
    draft = json.loads((work / "draft.json").read_text(encoding="utf-8"))
    draft["contract_values"] = {
        "review_report": "审查报告占位",
        "verdict": "issues_found",
        "meta": {"reviewer": "agent", "round": 1},
    }
    draft["findings"] = [{
        "finding_id": "F01",
        "facet": "argument_chain",
        "severity": "major",
        "issue_type": "insufficient_evidence",
        "verdict": "confirmed",
        "claim": "结论外推超出数据范围",
        "location": "D1::para5",
        "quote": "方法 A 可广泛应用于所有临床场景",
        "rationale": "样本仅 100 例且未说明外部效度，结论使用全称量化。",
        "revision_suggestion": "改为限定范围或补充泛化证据。",
        "confidence": "high",
        "contract_ref": "verdict",
        "evidence": [{"source_id": "D1", "locator": "para5",
                      "quote": "方法 A 可广泛应用于所有临床场景"}],
    }]
    draft["status"] = "partial"
    draft["uncertainty"] = [{"item": "样本量", "note": "原文未说明抽样方式"}]
    draft["pending_human"] = [{"item": "引用文献", "note": "参考文献缺失"}]
    write(work / "draft.json", json.dumps(draft, ensure_ascii=False))
    r = run(["check", "--work", str(work)])
    report = json.loads((work / "check_report.json").read_text(encoding="utf-8"))
    results.append(("check PASS on valid draft", report.get("overall") == "PASS",
                    f"overall={report.get('overall')}"))

    # 4) 引入坏 quote，check 应 FAIL
    draft2 = json.loads((work / "draft.json").read_text(encoding="utf-8"))
    draft2["findings"][0]["quote"] = "这段文字不在原文中"
    write(work / "draft.json", json.dumps(draft2, ensure_ascii=False))
    r = run(["check", "--work", str(work)])
    report2 = json.loads((work / "check_report.json").read_text(encoding="utf-8"))
    results.append(("check FAIL on bad quote",
                    report2.get("overall") == "FAIL",
                    f"overall={report2.get('overall')}"))
    # 恢复
    write(work / "draft.json", json.dumps(draft, ensure_ascii=False))

    # 5) build + selfcheck
    r = run(["build", "--work", str(work), "--output", str(out)])
    final = json.loads((out / "result.json").read_text(encoding="utf-8"))
    results.append(("build keeps contract fields",
                    all(k in final for k in
                        ("review_report", "verdict", "meta")),
                    "contract fields present"))
    results.append(("build change_log present",
                    isinstance(final.get("change_log"), list)
                    and len(final.get("change_log", [])) >= 2,
                    f"log len={len(final.get('change_log', []))}"))
    results.append(("review_report.md exists",
                    (out / "review_report.md").exists(), "exists"))
    r = run(["selfcheck", "--work", str(work), "--output", str(out)])
    sc = json.loads((out / "selfcheck.json").read_text(encoding="utf-8"))
    results.append(("selfcheck PASS", sc.get("overall") == "PASS",
                    f"overall={sc.get('overall')}"))

    # 6) 空目录
    empty = tmp / "empty"
    empty.mkdir()
    ok, msg = check(empty, "insufficient_evidence", "empty dir")
    results.append(("empty dir insufficient_evidence", ok, msg))

    # 7) 坏 JSON
    bad = tmp / "badjson"
    bad.mkdir()
    write(bad / "case.json", "{not valid json")
    ok, msg = check(bad, "input_error", "bad json")
    results.append(("bad json input_error", ok, msg))

    # 汇总（恒退出 0）
    print("=== selftest summary ===")
    all_ok = True
    for name, ok, detail in results:
        all_ok = all_ok and ok
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    print(f"overall: {'PASS' if all_ok else 'FAIL'} "
          f"({sum(1 for _, o, _ in results if o)}/{len(results)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
