#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 对 Skill 包做一次端到端自检（纯标准库）。

检查内容：
  1. main.py --help 退出码 0；
  2. 正常样例：discover → scaffold → 写一条 finding → finalize，result.json 可解析、
     case_id 存在、output_contract 字段被原样回填、finding 六必填齐全；
  3. 空输入目录：状态为 insufficient_evidence 或 input_error，且不抛异常；
  4. 坏 JSON 输入：记录错误并继续，退出码 0。

全部在系统临时目录构造样例，不读写真实案例，不预写正式测试产物。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

MAIN = Path(__file__).resolve().parent / "main.py"
FACETS = ["research_design", "variable_definition", "statistical_methods",
          "sample_size_power", "results_reporting"]
REQUIRED = ["id", "facet", "locator", "quote", "severity", "suggestion"]


def run(args, cwd=None):
    proc = subprocess.run([sys.executable, "-B", str(MAIN), *args],
                          capture_output=True, text=True, cwd=cwd)
    return proc.returncode, proc.stdout, proc.stderr


def ok(label, cond, detail=""):
    print(("PASS" if cond else "FAIL") + f"  {label}" + (f"  [{detail}]" if detail else ""))
    return cond


def main():
    failures = []
    tmp = Path(tempfile.mkdtemp(prefix="skill-selftest-"))

    # 1. help 退出码 0
    rc, out, err = run(["--help"])
    failures.append(ok("main.py --help exit 0", rc == 0, f"rc={rc}"))

    # 2. 正常样例
    demo_in = tmp / "demo" / "input"
    demo_out = tmp / "demo" / "output"
    demo_work = tmp / "demo" / "work"
    (demo_in / "materials").mkdir(parents=True)
    (demo_in / "case.json").write_text(json.dumps({
        "case_id": "demo-001",
        "output_contract": {
            "expected_fields": ["review_report", "evidence"],
            "language": "zh",
            "required_format": None,
        },
    }, ensure_ascii=False), encoding="utf-8")
    (demo_in / "task.md").write_text(
        "对材料做研究设计、变量定义、统计方法、样本量、结果报告审查。\n", encoding="utf-8")
    (demo_in / "materials" / "paper.md").write_text(
        "研究设计：回顾性队列。\n\n样本量：共纳入 N=120 例患者。\n\n"
        "结果：两组比较 p=0.03，差异无统计学意义。\n\n"
        "样本量：实际入组 N=118 例。\n", encoding="utf-8")

    rc, out, err = run(["discover", "--input", str(demo_in), "--output", str(demo_out),
                        "--work", str(demo_work)])
    failures.append(ok("discover normal", rc == 0, f"rc={rc}"))

    rc, out, err = run(["scaffold", "--input", str(demo_in), "--output", str(demo_out),
                        "--work", str(demo_work)])
    failures.append(ok("scaffold normal", rc == 0, f"rc={rc}"))

    finding = {
        "id": "F001",
        "facet": "results_reporting",
        "finding_type": "factual_error",
        "check_class": "statistical_reporting",
        "locator": "p.1 ¶3",
        "quote": "结果：两组比较 p=0.03，差异无统计学意义。",
        "severity": "critical",
        "basis": "p=0.03 与“无统计学意义”措辞矛盾",
        "suggestion": "核对 p 值与结论表述，二者只能保留一个",
        "confidence": "high",
        "review_status": "confirmed",
    }
    (demo_work / "findings").mkdir(parents=True, exist_ok=True)
    (demo_work / "findings" / "F001.json").write_text(
        json.dumps(finding, ensure_ascii=False), encoding="utf-8")
    (demo_work / "meta.json").write_text(json.dumps({
        "status": "ok",
        "overall_verdict": "problem",
        "result_status": "problem",
        "checked_facets": FACETS,
        "sources": [{"kind": "material", "path": "materials/paper.md"}],
        "uncertainty": [],
        "pending_human": [],
        "limitations": ["仅演示样例"],
    }, ensure_ascii=False), encoding="utf-8")

    rc, out, err = run(["finalize", "--input", str(demo_in), "--output", str(demo_out),
                        "--work", str(demo_work)])
    failures.append(ok("finalize normal", rc == 0, f"rc={rc}"))

    result = json.loads((demo_out / "result.json").read_text(encoding="utf-8"))
    failures.append(ok("result.json loads", isinstance(result, dict)))
    failures.append(ok("case_id present", result.get("case_id") == "demo-001"))
    failures.append(ok("output_contract echoed",
                       result.get("expected_fields") == ["review_report", "evidence"]
                       and result.get("language") == "zh"
                       and "required_format" in result))
    findings = result.get("review_report", {}).get("findings", [])
    failures.append(ok("finding merged", len(findings) == 1))
    if findings:
        f = findings[0]
        failures.append(ok("finding six keys", all(k in f and f.get(k) for k in REQUIRED)))
        failures.append(ok("quote verbatim",
                           f.get("quote") == "结果：两组比较 p=0.03，差异无统计学意义。"))
    failures.append(ok("numeric candidate emitted", len(result.get("numeric_checks", [])) >= 1))
    failures.append(ok("change_log present", len(result.get("change_log", [])) >= 2))
    failures.append(ok("status honest", result.get("status") in
                       ("ok", "partial", "insufficient_evidence", "input_error")))
    failures.append(ok("report.md generated", (demo_out / "report.md").exists()))

    # 3. 空输入目录
    empty_in = tmp / "empty" / "input"
    empty_in.mkdir(parents=True)
    empty_out = tmp / "empty" / "output"
    empty_work = tmp / "empty" / "work"
    rc, out, err = run(["discover", "--input", str(empty_in), "--output", str(empty_out),
                        "--work", str(empty_work)])
    rc2, out2, err2 = run(["scaffold", "--input", str(empty_in), "--output", str(empty_out),
                           "--work", str(empty_work)])
    rc3, out3, err3 = run(["finalize", "--input", str(empty_in), "--output", str(empty_out),
                           "--work", str(empty_work)])
    empty_result = json.loads((empty_out / "result.json").read_text(encoding="utf-8"))
    failures.append(ok("empty input exit 0", rc == 0 and rc2 == 0 and rc3 == 0,
                       f"rc={rc},{rc2},{rc3}"))
    failures.append(ok("empty input honest status",
                       empty_result.get("status") in ("insufficient_evidence", "input_error"),
                       empty_result.get("status")))

    # 4. 坏 JSON 输入
    bad_in = tmp / "bad" / "input"
    (bad_in / "materials").mkdir(parents=True)
    (bad_in / "case.json").write_text("{ not valid json", encoding="utf-8")
    (bad_in / "materials" / "paper.md").write_text("材料正文。\n", encoding="utf-8")
    bad_out = tmp / "bad" / "output"
    bad_work = tmp / "bad" / "work"
    rc, out, err = run(["discover", "--input", str(bad_in), "--output", str(bad_out),
                        "--work", str(bad_work)])
    rc2, out2, err2 = run(["scaffold", "--input", str(bad_in), "--output", str(bad_out),
                           "--work", str(bad_work)])
    bad_result = json.loads((bad_out / "result.json").read_text(encoding="utf-8"))
    failures.append(ok("bad json exit 0", rc == 0 and rc2 == 0, f"rc={rc},{rc2}"))
    failures.append(ok("bad json produces legal result", isinstance(bad_result, dict)
                       and "case_id" in bad_result))

    n_fail = sum(1 for f in failures if not f)
    print(f"\n{n_fail} failure(s), {len(failures)} checks.")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
