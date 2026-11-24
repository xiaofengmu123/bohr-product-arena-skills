#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""论文质量审查 Skill 自检脚本（仅标准库，进程内调用 main.main）。

覆盖三组场景：空输入目录、坏 case.json、带 planted 问题的小型文本材料。
每组断言：退出码恒 0、result.json 可解析、含 case_id 与信封字段。
本脚本是开发期自检工具，不在正式评测中运行；不包含正式案例答案。

用法：python3 scripts/selftest.py  （退出码 0=PASS，1=FAIL）
"""
from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path

sys.dont_write_bytecode = True  # 运行时不落盘 __pycache__/.pyc
sys.path.insert(0, str(Path(__file__).resolve().parent))

import main  # noqa: E402

ENVELOPE = ["status", "sources", "uncertainty", "pending_human", "errors",
            "degradations", "changes", "change_log", "runtime"]


def _run(args_list):
    try:
        code = main.main(args_list)
    except Exception:
        traceback.print_exc()
        return 1, None, None, None
    return code, None, None, None


def _read_result(outdir):
    p = Path(outdir) / "result.json"
    if not p.exists():
        return None, "result.json missing"
    try:
        return json.loads(p.read_text(encoding="utf-8")), None
    except Exception as exc:
        return None, f"result.json invalid: {exc}"


def _check_result(outdir):
    r, err = _read_result(outdir)
    if err:
        return [err]
    problems = []
    for key in ["case_id"] + ENVELOPE:
        if key not in r:
            problems.append(f"missing key {key}")
    if not isinstance(r.get("result"), dict):
        problems.append("result.result not a dict")
    return problems


def scenario_empty():
    with tempfile.TemporaryDirectory() as root:
        base = Path(root)
        inp, work, out = base / "in", base / "wk", base / "out"
        inp.mkdir()
        code = main.main(["init", "--input", str(inp), "--work", str(work),
                          "--output", str(out)])
        if code != 0:
            return False, f"init exit {code}"
        probs = _check_result(out)
        if probs:
            return False, "; ".join(probs)
        r = json.loads((out / "result.json").read_text(encoding="utf-8"))
        if r.get("status") != "insufficient_evidence":
            return False, f"empty input status={r.get('status')}, "
            f"want insufficient_evidence"
    return True, ""


def scenario_bad_json():
    with tempfile.TemporaryDirectory() as root:
        base = Path(root)
        inp, work, out = base / "in", base / "wk", base / "out"
        inp.mkdir()
        (inp / "case.json").write_text("{ not valid json", encoding="utf-8")
        code = main.main(["init", "--input", str(inp), "--work", str(work),
                          "--output", str(out)])
        if code != 0:
            return False, f"init exit {code}"
        probs = _check_result(out)
        if probs:
            return False, "; ".join(probs)
        r = json.loads((out / "result.json").read_text(encoding="utf-8"))
        if r.get("status") != "input_error":
            return False, f"bad json status={r.get('status')}, want input_error"
    return True, ""


def scenario_full():
    with tempfile.TemporaryDirectory() as root:
        base = Path(root)
        inp, work, out = base / "in", base / "wk", base / "out"
        inp.mkdir()
        case = {
            "case_id": "selftest-case-001",
            "task_instruction": "对该稿做论文质量审查，覆盖六类检查项。",
            "output_contract": {"review_report": "string",
                                "findings": "list",
                                "extra_unknown_key": "keep"},
        }
        (inp / "case.json").write_text(
            json.dumps(case, ensure_ascii=False), encoding="utf-8")
        material = (
            "## 摘要\n\n本研究表明新药 A 可显著降低血压（p < 0.05）。\n\n"
            "## 方法\n\n我们招募了 30 名患者，未设对照组。\n\n"
            "## 结果\n\n干预组血压下降 12 mmHg（p = 0.049）。\n\n"
            "## 结论\n\n新药 A 对所有高血压人群均有效。\n"
        )
        (inp / "paper.md").write_text(material, encoding="utf-8")
        code = main.main(["init", "--input", str(inp), "--work", str(work),
                          "--output", str(out)])
        if code != 0:
            return False, f"init exit {code}"
        # 写一条带逐字 quote 的 draft 并走完 check/build/selfcheck
        index = json.loads((work / "index.json").read_text(encoding="utf-8"))
        paras = index["paras"]
        quote = None
        locator = None
        for p in paras:
            if "30 名患者" in p["text"]:
                quote = p["text"]
                locator = p["locator"]
                break
        if not quote:
            return False, "planted material paragraph not found"
        draft = json.loads((work / "draft.json").read_text(encoding="utf-8"))
        draft["verdict_summary"]["summary"] = "自检示例，非正式案例结果。"
        draft["findings"] = [{
            "finding_id": "F01",
            "facet": "method_design",
            "severity": "major",
            "issue_type": "insufficient_evidence",
            "verdict": "confirmed",
            "claim": "方法部分未说明对照设置",
            "location": locator,
            "quote": quote,
            "rationale": "正文自述未设对照组，且无对照/随机化说明。",
            "revision_suggestion": "补充对照组或说明单臂设计的理由与局限。",
            "confidence": "high",
            "evidence": [{"source_id": paras[0]["source_id"],
                          "locator": locator, "quote": quote}],
        }]
        draft["unchecked_items"] = [
            {"facet": f, "reason": "自检示例：未逐项展开",
             "status": "unchecked"}
            for f in ["fact_check", "citation_support", "statistics_report",
                      "argument_chain", "academic_norms"]
        ]
        (work / "draft.json").write_text(
            json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
        for cmd in (["check"], ["build"], ["selfcheck"]):
            code = main.main(cmd + ["--input", str(inp), "--work", str(work),
                                    "--output", str(out)])
            if code != 0:
                return False, f"{cmd[0]} exit {code}"
        probs = _check_result(out)
        if probs:
            return False, "; ".join(probs)
        r = json.loads((out / "result.json").read_text(encoding="utf-8"))
        # 契约未知键应原样保留
        if r.get("extra_unknown_key") != "keep":
            return False, "unknown contract key not preserved"
        if not (out / "review_report.md").exists():
            return False, "review_report.md missing"
        findings = r["result"]["findings"]
        if len(findings) != 1 or findings[0]["finding_id"] != "F01":
            return False, "findings not built correctly"
    return True, ""


def main_selftest():
    scenarios = [
        ("empty input -> insufficient_evidence", scenario_empty),
        ("bad case.json -> input_error", scenario_bad_json),
        ("full pipeline with planted material", scenario_full),
    ]
    failures = []
    for name, fn in scenarios:
        ok, msg = fn()
        print(f"[{'PASS' if ok else 'FAIL'}] {name}"
              + (f"  ({msg})" if msg else ""))
        if not ok:
            failures.append(name)
    print(f"\n{len(scenarios) - len(failures)}/{len(scenarios)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main_selftest())
