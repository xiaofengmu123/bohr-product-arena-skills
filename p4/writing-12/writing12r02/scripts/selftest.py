#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py -- deterministic self-test for the Skill package.

Runs without any LaTeX binary: it exercises tolerant input handling, the
output contract, evidence quoting, the log classifier, and deterministic
re-runs. Exit code 0 means every test passed; 1 means at least one failed.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl as C  # noqa: E402


RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    print("[%s] %s%s" % (mark, name, (" -- " + detail) if detail else ""))


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _struct(result):
    """Deterministic structural view: drop timestamps/change_log/runtime."""
    return {k: v for k, v in result.items()
            if k not in ("change_log", "runtime")}


def test_empty_input():
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, "input"))
        skeleton, keys, case_id, notes = C.build_skeleton(os.path.join(root, "input"))
        ok = (skeleton.get("status") in C.ALLOWED_STATUS
              and case_id == "unknown"
              and isinstance(skeleton.get("sources"), list))
        check("empty input -> valid honest skeleton", ok,
              "status=%s" % skeleton.get("status"))


def test_bad_json():
    with tempfile.TemporaryDirectory() as root:
        inp = os.path.join(root, "input")
        os.makedirs(inp)
        _write(os.path.join(inp, "case.json"), '{"case_id": "c1", "output_contract": [')
        skeleton, keys, case_id, notes = C.build_skeleton(inp)
        ok = skeleton.get("status") in ("partial", "input_error") and bool(skeleton.get("errors"))
        check("truncated/bad case.json tolerated", ok, "errors=%s" % skeleton.get("errors"))


def test_contract_echo():
    with tempfile.TemporaryDirectory() as root:
        inp = os.path.join(root, "input")
        os.makedirs(inp)
        _write(os.path.join(inp, "case.json"),
               json.dumps({"case_id": "c9", "output_contract": {
                   "title": "T", "abstract": "", "body": "", "bibliography": ""}}))
        _write(os.path.join(inp, "task.md"), "# task\n")
        _write(os.path.join(inp, "materials", "paper.md"), "alpha = 1.23 eV\n")
        skeleton, keys, case_id, notes = C.build_skeleton(inp)
        ok = (case_id == "c9" and keys == ["title", "abstract", "body", "bibliography"]
              and all(k in skeleton for k in keys))
        check("output_contract keys echoed verbatim", ok, "keys=%s" % keys)


def test_reserved_key_protection():
    with tempfile.TemporaryDirectory() as root:
        inp = os.path.join(root, "input")
        os.makedirs(inp)
        _write(os.path.join(inp, "case.json"),
               json.dumps({"case_id": "c9", "output_contract": {"status": "whatever"}}))
        skeleton, keys, case_id, notes = C.build_skeleton(inp)
        ok = skeleton.get("status") in C.ALLOWED_STATUS
        check("reserved keys not overwritten by contract", ok,
              "status=%s" % skeleton.get("status"))


def test_evidence_quote():
    with tempfile.TemporaryDirectory() as root:
        f = os.path.join(root, "m.md")
        _write(f, "line1\nline2\nline3\n")
        q = C.quote(f, 2, 3)
        ok = q["text"] == "line2\nline3"
        check("evidence quote returns verbatim lines", ok, repr(q.get("text")))


def test_log_classifier():
    log = (
        "! LaTeX Error: File `siunitx.sty' not found.\n"
        "l.12 \\usepackage{siunitx}\n"
        "! Undefined control sequence.\n"
        "l.30 \\badcmd\n"
        "LaTeX Warning: Reference `eq:1' on page 3 undefined.\n"
        "LaTeX Warning: Citation `Smith2020' on page 3 undefined.\n"
        "Rerun to get cross-references right.\n"
    )
    res = C.classify_log(log, "main.tex")
    cats = {f["category"] for f in res["findings"]}
    ok = (res["has_error"]
          and "missing_package" in cats
          and "undefined_control_sequence" in cats
          and res["root_cause"]["category"] == "missing_package"
          and res["refcheck"]["undefined_refs"] == ["eq:1"]
          and res["refcheck"]["undefined_cites"] == ["Smith2020"]
          and res["rerun_needed"])
    check("log classifier finds root cause + categories", ok,
          "root=%s cats=%s" % (res["root_cause"]["category"] if res["root_cause"] else None, sorted(cats)))


def test_log_classifier_fileline():
    log = (
        "./main.tex:12: LaTeX Error: File `siunitx.sty' not found.\n"
        "./main.tex:30: Undefined control sequence.\n"
        "l.30 \\badcmd\n"
    )
    res = C.classify_log(log, "main.tex")
    ok = (res["has_error"]
          and res["root_cause"]["category"] == "missing_package"
          and res["root_cause"]["line"] == "12"
          and any(f["category"] == "undefined_control_sequence" for f in res["findings"]))
    check("file-line-error format classified", ok,
          "root=%s@%s" % (res["root_cause"]["category"], res["root_cause"]["line"]))


def test_budget_downgrade():
    state = {
        "started_at": "2000-01-01T00:00:00Z",  # far past -> elapsed huge
        "budget": {"phase_plan": C.PHASE_PLAN},
        "phases": [{"phase": "bootstrap", "requests": 60}],
    }
    gov = C.BudgetGovernor(state)
    down, reasons = gov.must_downgrade()
    ok = down and "low_time" in reasons and "low_requests" in reasons
    check("budget governor forces finalize on low resources", ok, "reasons=%s" % reasons)


def test_determinism():
    with tempfile.TemporaryDirectory() as root:
        inp = os.path.join(root, "input")
        os.makedirs(inp)
        _write(os.path.join(inp, "case.json"),
               json.dumps({"case_id": "d1", "output_contract": {"a": "", "b": ""}}))
        _write(os.path.join(inp, "task.md"), "# t\n")
        s1, _, _, _ = C.build_skeleton(inp)
        s2, _, _, _ = C.build_skeleton(inp)
        ok = _struct(s1) == _struct(s2)
        check("repeat run yields identical structural result", ok)


def run_all():
    tests = [
        test_empty_input, test_bad_json, test_contract_echo,
        test_reserved_key_protection, test_evidence_quote,
        test_log_classifier, test_log_classifier_fileline,
        test_budget_downgrade, test_determinism,
    ]
    for t in tests:
        try:
            t()
        except Exception as exc:  # noqa: BLE001 -- selftest must not crash
            check(t.__name__, False, "raised %r" % exc)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("----")
    print("passed %d/%d" % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    C.reconfigure_stdio()
    sys.exit(run_all())
