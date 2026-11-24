#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py —— 提交前自检：用合成的最小输入跑 init/check/build/selfcheck。

仅用于验证接口与异常处理，不包含任何正式案例或预制答案；合成材料在临时目录
运行时生成，测试结束后不落盘正式产物。所有用例保持退出码 0（与 main.py 约定一致），
结果以 PASS/FAIL 打印，最后一行给出汇总。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import main


def _mkdir(p):
    Path(p).mkdir(parents=True, exist_ok=True)


def _read_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def _case_empty():
    root = Path(tempfile.mkdtemp(prefix="selftest_empty_"))
    inp, work, out = root / "input", root / "work", root / "output"
    _mkdir(inp), _mkdir(work), _mkdir(out)
    main.run_command("init", inp, work, out)
    res = _read_json(out / "result.json")
    ok = bool(res) and res.get("status") in \
        ("insufficient_evidence", "partial", "input_error")
    return "empty_input", ok, res.get("status") if res else None


def _case_bad_json():
    root = Path(tempfile.mkdtemp(prefix="selftest_badjson_"))
    inp, work, out = root / "input", root / "work", root / "output"
    _mkdir(inp), _mkdir(work), _mkdir(out)
    (inp / "case.json").write_text("{ not json", encoding="utf-8")
    (inp / "task.md").write_text("# demo\n", encoding="utf-8")
    main.run_command("init", inp, work, out)
    res = _read_json(out / "result.json")
    ok = bool(res) and res.get("status") == "input_error"
    return "bad_json", ok, res.get("status") if res else None


def _case_good():
    root = Path(tempfile.mkdtemp(prefix="selftest_good_"))
    inp, work, out = root / "input", root / "work", root / "output"
    _mkdir(inp / "materials"), _mkdir(work), _mkdir(out)
    (inp / "case.json").write_text(json.dumps({
        "case_id": "demo-001",
        "task_instruction": "review this paper",
        "output_contract": {"report": "list", "notes": None},
    }, ensure_ascii=False), encoding="utf-8")
    (inp / "task.md").write_text("# demo task\n", encoding="utf-8")
    paper = ("Title: Demo\n\nAbstract: We tested X on N=20 samples.\n\n"
             "Methods: No control group was included.\n")
    (inp / "materials" / "paper.txt").write_text(paper, encoding="utf-8")
    main.run_command("init", inp, work, out)

    draft = {
        "status": "partial",
        "findings": [{
            "finding_id": "F01",
            "facet": "method_design",
            "severity": "major",
            "issue_type": "insufficient_evidence",
            "verdict": "confirmed",
            "claim": "方法缺少对照设置",
            "location": "para3",
            "quote": "No control group was included",
            "rationale": "无对照，无法判断干预效应",
            "revision_suggestion": "补充对照或说明为何不设对照",
            "confidence": "high",
            "check_anchor": "MET-1",
            "uncertainty": {"kind": "none", "note": ""},
            "evidence": [{"source_id": "D1", "locator": "para3",
                          "quote": "No control group was included"}],
        }],
        "unchecked_items": [],
        "coverage": {
            "fact_check": {"status": "not_applicable", "reason": "demo"},
            "citation_support": {"status": "not_applicable", "reason": "demo"},
            "method_design": {"status": "checked", "reason": "demo"},
            "statistics_report": {"status": "not_applicable", "reason": "demo"},
            "argument_chain": {"status": "not_applicable", "reason": "demo"},
            "academic_norms": {"status": "not_applicable", "reason": "demo"},
        },
        "verdict_summary": {},
        "uncertainty": {"source_conflicts": [], "ambiguous": [],
                        "unverifiable": [], "overall_confidence": "medium"},
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "change_log": [],
    }
    (work / "draft.json").write_text(json.dumps(draft, ensure_ascii=False),
                                     encoding="utf-8")
    main.run_command("check", inp, work, out)
    chk = _read_json(work / "check_report.json")
    main.run_command("build", inp, work, out)
    res = _read_json(out / "result.json")
    main.run_command("selfcheck", inp, work, out)
    sc = _read_json(work / "selfcheck.json")
    findings = ((res or {}).get("result") or {}).get("findings") or []
    ok = (bool(res) and res.get("status") == "partial"
          and len(findings) == 1
          and bool(chk) and chk.get("status") == "PASS"
          and bool(sc) and sc.get("status") == "PASS"
          and res.get("report") == "list"  # 契约键平铺回填
          and "output_contract" in res)
    return "good_flow", ok, res.get("status") if res else None


def main_selftest():
    cases = (_case_empty, _case_bad_json, _case_good)
    results = []
    for fn in cases:
        try:
            name, ok, detail = fn()
        except Exception as exc:  # 测试用例自身异常也记为 FAIL
            name, ok, detail = fn.__name__, False, f"exception {exc}"
        results.append((name, ok, detail))
        print(f"[{name}] {'PASS' if ok else 'FAIL'}  status={detail}")
    nfail = sum(1 for _, ok, _ in results if not ok)
    print(f"SELFTEST: {'ok' if nfail == 0 else f'{nfail} failures'}")
    return 0


if __name__ == "__main__":
    sys.exit(main_selftest())
