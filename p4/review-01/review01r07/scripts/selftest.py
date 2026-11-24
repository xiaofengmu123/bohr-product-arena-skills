#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — deterministic self-test for the paper-quality-review skill.

Runs the CLI (by importing `main`) against throwaway temp directories and checks
that every robustness scenario leaves a legal result.json and exit code 0.

Scenarios:
  A. empty input directory
  B. malformed case.json
  C. oversized material file (skipped, not truncated)
  D. full happy path: init -> check -> build with a verbatim quote
  E. no command given (default skeleton)
  F. unknown command and unknown flag (graceful degradation)

Usage:
  python3 -B scripts/selftest.py
Exit code: 0 if all scenarios pass, 1 otherwise.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import main as skill_main
from core_impl import FACETS, load_json

PASS = []
FAIL = []


def record(name, ok, detail=""):
    if ok:
        PASS.append(name)
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}  {detail}")


def call(argv):
    """Run main.main(argv), swallowing SystemExit(0/help) as success."""
    try:
        skill_main.main(argv)
        return 0
    except SystemExit as exc:
        return exc.code if exc.code is not None else 0
    except Exception as exc:
        return f"exception: {exc}"


def new_dirs(base):
    d = Path(base)
    (d / "input").mkdir(parents=True, exist_ok=True)
    (d / "work").mkdir(parents=True, exist_ok=True)
    (d / "output").mkdir(parents=True, exist_ok=True)
    return d / "input", d / "work", d / "output"


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load_result(out_dir):
    return load_json(Path(out_dir) / "result.json")


def check_legal_result(out_dir):
    r = load_result(out_dir)
    if not isinstance(r, dict):
        return False, "result.json not a dict"
    for key in ["case_id", "status", "sources", "uncertainty", "pending_human",
                "errors", "degradations", "changes", "change_log", "runtime"]:
        if key not in r:
            return False, f"missing key '{key}'"
    rr = r.get("result")
    if not isinstance(rr, dict):
        return False, "result is not a dict"
    if not isinstance(rr.get("findings"), list):
        return False, "result.findings not a list"
    if not isinstance(rr.get("unchecked_items"), list):
        return False, "result.unchecked_items not a list"
    return True, ""


def scenario_empty(base):
    inp, work, out = new_dirs(base / "empty")
    code = call(["init", "--input", str(inp), "--work", str(work),
                 "--output", str(out)])
    ok = code == 0
    r = load_result(out)
    if ok and isinstance(r, dict):
        ok = r.get("status") == "insufficient_evidence"
        ok = ok and all(
            any(u.get("facet") == f for u in (r.get("result") or {}).get("unchecked_items", []))
            for f in FACETS)
    record("A empty input", ok, f"code={code} status={r and r.get('status')}")


def scenario_badjson(base):
    inp, work, out = new_dirs(base / "badjson")
    write_text(inp / "case.json", '{"case_id": 42, "output_contract": [')
    code = call(["init", "--input", str(inp), "--work", str(work),
                 "--output", str(out)])
    r = load_result(out)
    ok = code == 0 and isinstance(r, dict) and r.get("status") == "input_error"
    record("B bad JSON case.json", ok, f"code={code} status={r and r.get('status')}")


def scenario_oversize(base):
    inp, work, out = new_dirs(base / "oversize")
    write_text(inp / "case.json",
               json.dumps({"case_id": "case-oversize",
                           "output_contract": {"notes": "string"}}))
    write_text(inp / "task.md", "# task\n")
    write_text(inp / "small.md", "This is a small readable paragraph.\n\nSecond one.")
    big = inp / "big.txt"
    big.write_bytes(b"x" * (2 * 1024 * 1024 + 1024))
    code = call(["init", "--input", str(inp), "--work", str(work),
                 "--output", str(out)])
    r = load_result(out)
    ok = code == 0 and isinstance(r, dict)
    if ok:
        ok = r.get("status") == "partial"
        ok = ok and any("too large" in d for d in r.get("degradations", []))
        ok = ok and r.get("case_id") == "case-oversize"
        # contract field must be present
        ok = ok and "notes" in r
    record("C oversized file skipped", ok, f"code={code} status={r and r.get('status')}")


def scenario_happy(base):
    inp, work, out = new_dirs(base / "happy")
    material = ("The intervention reduced systolic blood pressure by 12 mmHg.\n\n"
                "However, no control group was reported for this comparison.")
    write_text(inp / "case.json",
               json.dumps({"case_id": "case-happy",
                           "task_instruction": "review this paper",
                           "output_contract": {"findings": "list"}}))
    write_text(inp / "task.md", "# 论文质量审查\n")
    write_text(inp / "paper.md", material)
    code = call(["init", "--input", str(inp), "--work", str(work),
                 "--output", str(out)])
    if code != 0:
        record("D happy path", False, f"init code={code}")
        return
    index = load_json(work / "index.json")
    locator = None
    if isinstance(index, dict):
        for s in index.get("sources") or []:
            if s.get("paragraphs"):
                locator = s["paragraphs"][0]["locator"]
                break
    if locator is None:
        record("D happy path", False, "no locator found in index")
        return
    draft = {
        "findings": [{
            "finding_id": "F01",
            "facet": "method_design",
            "severity": "major",
            "issue_type": "insufficient_evidence",
            "verdict": "to_review",
            "claim": "The comparison lacks a control group.",
            "location": locator,
            "quote": "However, no control group was reported for this comparison.",
            "rationale": "A causal claim about reduction needs a comparator.",
            "revision_suggestion": "Report the control/comparator group explicitly.",
            "confidence": "high",
            "evidence": [{"source_id": "D1", "locator": locator,
                          "quote": "However, no control group was reported for this comparison."}],
        }],
        "unchecked_items": [
            {"facet": "fact_check", "reason": "no external data to verify", "detail": ""},
            {"facet": "citation_support", "reason": "no reference list present", "detail": ""},
            {"facet": "statistics_report", "reason": "not applicable", "detail": ""},
            {"facet": "argument_chain", "reason": "not reviewed in this pass", "detail": ""},
            {"facet": "academic_norms", "reason": "not reviewed in this pass", "detail": ""},
        ],
    }
    write_text(work / "draft.json", json.dumps(draft, ensure_ascii=False))
    code1 = call(["check", "--input", str(inp), "--work", str(work),
                  "--output", str(out)])
    crep = load_json(work / "check_report.json")
    code2 = call(["build", "--input", str(inp), "--work", str(work),
                  "--output", str(out)])
    r = load_result(out)
    ok = (code1 == 0 and code2 == 0 and isinstance(crep, dict)
          and crep.get("ok") is True and isinstance(r, dict)
          and r.get("status") == "ok"
          and len(r.get("result", {}).get("findings", [])) == 1
          and r.get("case_id") == "case-happy"
          and (out / "review_report.md").exists()
          and "findings" in r)  # contract field present
    record("D happy path", ok,
           f"check={code1} build={code2} check_ok={crep and crep.get('ok')}")


def scenario_no_command(base):
    inp, work, out = new_dirs(base / "nocommand")
    write_text(inp / "case.json", json.dumps({"case_id": "case-default"}))
    code = call(["--input", str(inp), "--work", str(work), "--output", str(out)])
    r = load_result(out)
    ok = code == 0 and isinstance(r, dict) and r.get("case_id") == "case-default"
    record("E no command default", ok, f"code={code}")


def scenario_unknown(base):
    inp, work, out = new_dirs(base / "unknown")
    code = call(["bogus-command", "--unknown-flag", "1",
                 "--input", str(inp), "--work", str(work), "--output", str(out)])
    r = load_result(out)
    ok = code == 0 and isinstance(r, dict)
    record("F unknown command/flag", ok, f"code={code}")


def main():
    base = Path(tempfile.mkdtemp(prefix="review-selftest-"))
    print(f"selftest temp dir: {base}")
    try:
        scenario_empty(base)
        scenario_badjson(base)
        scenario_oversize(base)
        scenario_happy(base)
        scenario_no_command(base)
        scenario_unknown(base)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        FAIL.append("unhandled selftest exception")
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for name in FAIL:
        print("  FAILED: " + name)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
