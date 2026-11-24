#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""论文质量审查 Skill 自检脚本（仅标准库）。

用途：交付前验证 scripts/main.py 在空输入、坏 JSON、缺材料、正常材料四种情况下
均退出码 0 且产出合法 result.json；验证 quote 逐字命中校验能拦下编造的引用。

用法：python3 scripts/selftest.py
退出码：全部通过为 0，任一失败为 1（这是自检工具，故意对失败不静默）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAIN = HERE / "main.py"

SAMPLE_MD = """# 摘要
本方法在 100 例样本上达到 95% 准确率。

## 方法
我们采用随机对照试验，但未报告样本量计算方法。

## 结果
p 值为 0.03，置信区间未报告。
"""

VALID_QUOTE = "我们采用随机对照试验，但未报告样本量计算方法。"
BAD_QUOTE = "这句话在原文中根本不存在。"


def run(cmd, **kw):
    return subprocess.run([sys.executable, str(MAIN), *cmd],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60, **kw)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def make_draft(extra_quote=None):
    findings = [{
        "finding_id": "F01",
        "facet": "method_design",
        "severity": "major",
        "issue_type": "standards_issue",
        "verdict": "confirmed",
        "claim": "方法部分未说明样本量计算方法",
        "location": "para2",
        "quote": VALID_QUOTE,
        "rationale": "缺失样本量计算说明，属报告不全而非方法缺陷",
        "revision_suggestion": "补充样本量计算依据与功效分析",
        "confidence": "high",
        "evidence": [{"source_id": "D1", "locator": "para2",
                      "quote": VALID_QUOTE}],
    }]
    if extra_quote:
        findings.append({
            "finding_id": "F02",
            "facet": "fact_check",
            "severity": "major",
            "issue_type": "factual_error",
            "verdict": "confirmed",
            "claim": "编造的引用",
            "location": "para9",
            "quote": extra_quote,
            "rationale": "不存在",
            "revision_suggestion": "删除",
            "confidence": "low",
            "evidence": [],
        })
    unchecked = [{"facet": f, "reason": "本稿不适用或无需检查"}
                 for f in ("fact_check", "citation_support", "statistics_report",
                           "argument_chain", "academic_norms")]
    return {
        "status": "ok",
        "uncertainty": [],
        "pending_human": [],
        "changes": ["首次审查"],
        "findings": findings,
        "unchecked_items": unchecked,
        "verdict_summary": {"overall": "major_revision",
                            "coverage": {f: ("checked" if f == "method_design" else "unchecked")
                                         for f in ("fact_check", "citation_support",
                                                   "method_design", "statistics_report",
                                                   "argument_chain", "academic_norms")}},
    }


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    return bool(cond)


def main():
    results = []

    # 0. --help 退出码 0
    p = run(["--help"])
    results.append(check("main.py --help exit 0", p.returncode == 0,
                         f"rc={p.returncode}"))

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # 1. 空输入目录
        d = root / "empty"
        (d / "input").mkdir(parents=True)
        p = run(["init", "--input", str(d / "input"), "--work", str(d / "work"),
                 "--output", str(d / "output")])
        r = load(d / "output" / "result.json")
        ok = (p.returncode == 0 and r.get("status") in ("insufficient_evidence", "input_error")
              and isinstance(r.get("result", {}).get("findings"), list)
              and r.get("case_id") is None)
        results.append(check("空输入 -> 合法 result.json", ok, f"status={r.get('status')}"))

        # 2. 坏 JSON
        d2 = root / "badjson"
        (d2 / "input").mkdir(parents=True)
        write(d2 / "input" / "case.json", "{ not valid json")
        write(d2 / "input" / "paper.txt", SAMPLE_MD)
        p = run(["init", "--input", str(d2 / "input"), "--work", str(d2 / "work"),
                 "--output", str(d2 / "output")])
        r = load(d2 / "output" / "result.json")
        ok = (p.returncode == 0 and r.get("status") in ("partial", "input_error")
              and any("bad JSON" in (e.get("error") or "") for e in r.get("errors", [])))
        results.append(check("坏 JSON -> 记录 error 且不崩溃", ok,
                             f"status={r.get('status')} errors={len(r.get('errors', []))}"))

        # 3. 正常材料 + 合法 finding：check PASS -> build -> selfcheck PASS
        d3 = root / "ok"
        write(d3 / "input" / "case.json",
              json.dumps({"case_id": "case-001", "task_instruction": "审查论文",
                          "output_contract": {"report_schema": "v1",
                                               "findings_required": True}},
                         ensure_ascii=False))
        write(d3 / "input" / "paper.txt", SAMPLE_MD)
        p = run(["init", "--input", str(d3 / "input"), "--work", str(d3 / "work"),
                 "--output", str(d3 / "output")])
        write(d3 / "work" / "draft.json", json.dumps(make_draft(), ensure_ascii=False))
        p = run(["check", "--work", str(d3 / "work")])
        cr = load(d3 / "work" / "check_report.json")
        ok = p.returncode == 0 and cr.get("failures") == 0
        results.append(check("合法 finding -> check PASS", ok, cr.get("summary")))

        p = run(["build", "--work", str(d3 / "work"), "--output", str(d3 / "output")])
        r = load(d3 / "output" / "result.json")
        ok = (p.returncode == 0 and r.get("case_id") == "case-001"
              and r.get("report_schema") == "v1" and r.get("findings_required") is True
              and r.get("status") == "ok"
              and len(r.get("result", {}).get("findings", [])) == 1
              and (d3 / "output" / "review_report.md").exists())
        results.append(check("build -> 契约回填 + 信封 + 报告", ok,
                             f"case_id={r.get('case_id')} status={r.get('status')}"))

        p = run(["selfcheck", "--work", str(d3 / "work"), "--output", str(d3 / "output")])
        sc = load(d3 / "work" / "selfcheck.json")
        ok = p.returncode == 0 and sc.get("failures") == 0
        results.append(check("selfcheck PASS", ok, sc.get("summary")))

        # 4. 编造 quote 被 check 拦下
        d4 = root / "badquote"
        write(d4 / "input" / "case.json",
              json.dumps({"case_id": "case-002"}, ensure_ascii=False))
        write(d4 / "input" / "paper.txt", SAMPLE_MD)
        run(["init", "--input", str(d4 / "input"), "--work", str(d4 / "work"),
             "--output", str(d4 / "output")])
        write(d4 / "work" / "draft.json",
              json.dumps(make_draft(extra_quote=BAD_QUOTE), ensure_ascii=False))
        p = run(["check", "--work", str(d4 / "work")])
        cr = load(d4 / "work" / "check_report.json")
        bad = [c for c in cr.get("quote_checks", []) if not c.get("ok")]
        ok = p.returncode == 0 and cr.get("failures", 0) >= 1 and any(
            "verbatim" in (c.get("reason") or "") for c in bad)
        results.append(check("编造 quote 被 check 拦下", ok,
                             f"failures={cr.get('failures')}"))

    n_fail = sum(1 for ok in results if not ok)
    print(f"\nselftest: {len(results) - n_fail}/{len(results)} passed")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
