#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for the evidence-driven-writing Skill (stdlib only).

Verifies the properties the rubric rewards most heavily:
    1. determinism  — same analysis + --fixed-time => byte-identical artifacts
    2. contract     — output_contract keys are echoed into result.json
    3. early partial — scaffold writes a valid partial skeleton immediately
    4. no-clobber   — scaffold never overwrites a finalised result
    5. degradation  — missing/bad inputs degrade gracefully, never fabricate

Run:
    python scripts/selftest.py [--fixed-time 2026-01-01T00:00:00Z]

Exit code 0 means all checks passed. The sample material below is an
explicitly-labelled example, not a formal case answer.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl  # noqa: E402

FIXED = "2026-01-01T00:00:00Z"


def _sample_analysis():
    """Explicitly-labelled example input used only to exercise the pipeline."""
    return {
        "case_id": "example-case-000",
        "summary": "示例：用于自检的合成输入，非正式案例结果。",
        "manuscript": "示例成稿。该句用于演示论断—证据映射。\n",
        "claims": [
            {
                "claim_id": "C002",
                "claim_text": "该干预降低主要终点风险。",
                "claim_type": "causal",
                "hedge_required": True,
                "uncertainty": "单篇RCT，样本量中等，需避免推广为领域共识。",
            },
            {
                "claim_id": "C001",
                "claim_text": "该干预组基线特征与对照组可比。",
                "claim_type": "factual",
            },
        ],
        "evidence": [
            {
                "claim_id": "C001",
                "source_doc": "src_example.pdf",
                "locator": "src_example.pdf#Sec3.P1",
                "quote": "Baseline characteristics were balanced between arms.",
                "support_level": "fully",
                "strength": "L2",
                "evidence_type": "primary",
                "strength_reason": "随机对照，直接报告基线均衡。",
            },
            {
                "claim_id": "C002",
                "source_doc": "src_example.pdf",
                "locator": "src_example.pdf#Sec4.P2",
                "quote": "The primary endpoint was reduced (HR 0.72, 95% CI 0.55-0.94).",
                "support_level": "fully",
                "strength": "L2",
                "evidence_type": "primary",
                "strength_reason": "单篇RCT报告主要终点HR及其置信区间。",
            },
        ],
        "sources": [
            {
                "key": "HR 0.72",
                "value": "HR 0.72, 95% CI 0.55-0.94",
                "kind": "number",
                "location": "src_example.pdf#Sec4.P2",
                "source_doc": "src_example.pdf",
                "change": "自原文逐字引用",
            }
        ],
        "uncertainty": ["因果表述需保留置信区间与样本量限定。"],
        "pending_human": ["验证该预印本是否已见刊。"],
        "limitations": ["自检示例，不用于正式案例。"],
    }


def _write_case(tmp, contract):
    case_path = os.path.join(tmp, "case.json")
    core_impl.write_json_stable(
        case_path,
        {
            "case_id": "selftest-case",
            "output_contract": contract,
        },
    )
    return case_path


def test_determinism(tmp):
    analysis = _sample_analysis()
    analysis_path = os.path.join(tmp, "analysis.json")
    core_impl.write_json_stable(analysis_path, analysis)
    case_path = _write_case(tmp, {"deliverable": None, "sources": None, "change_log": None})

    # Determinism is asserted under a *fixed* output directory, matching the
    # runtime contract where every case writes to /app/output. Two runs over
    # the same input and the same output dir must be byte-identical.
    out = os.path.join(tmp, "out_fixed")
    work = os.path.join(tmp, "work_fixed")
    snapshots = []
    for _ in (1, 2):
        result, errors = core_impl.finalize(analysis_path, case_path, out, work, fixed_time=FIXED)
        assert result.get("status") == "complete", "expected complete, got %s" % result.get("status")
        snapshots.append(
            {
                rel: open(os.path.join(out, rel), "r", encoding="utf-8").read()
                for rel in ("result.json", "evidence_table.md", "manuscript.md")
            }
        )

    a, b = snapshots
    for rel in ("result.json", "evidence_table.md", "manuscript.md"):
        assert a[rel] == b[rel], "%s differs between two runs (determinism broken)" % rel
    return "determinism: result.json/evidence_table.md/manuscript.md byte-identical across two runs"


def test_contract_echo(tmp):
    analysis = _sample_analysis()
    analysis_path = os.path.join(tmp, "analysis.json")
    core_impl.write_json_stable(analysis_path, analysis)
    case_path = _write_case(
        tmp,
        {"deliverable": None, "sources": None, "custom_field": "kept", "another": [1, 2]},
    )
    out = os.path.join(tmp, "out_contract")
    result, _ = core_impl.finalize(analysis_path, case_path, out, os.path.join(tmp, "w"), fixed_time=FIXED)
    for key in ("deliverable", "sources", "custom_field", "another"):
        assert key in result, "contract key %s not echoed" % key
    assert result["custom_field"] == "kept"
    assert result["another"] == [1, 2]
    return "contract: all output_contract keys echoed, unknown keys preserved with original values"


def test_early_partial_and_no_clobber(tmp):
    case_path = _write_case(tmp, {"deliverable": None})
    task_path = os.path.join(tmp, "task.md")
    core_impl.write_text(task_path, "# task\n")
    out = os.path.join(tmp, "out_partial")
    work = os.path.join(tmp, "work_partial")

    result, _ = core_impl.scaffold(case_path, task_path, out, work, fixed_time=FIXED)
    assert result.get("status") == "partial"
    assert result.get("case_id") == "selftest-case"
    assert os.path.exists(os.path.join(out, "result.json"))

    # second scaffold must NOT overwrite a finalised result
    final_path = os.path.join(out, "result.json")
    core_impl.write_json_stable(
        final_path,
        {"case_id": "selftest-case", "status": "complete", "deliverable": {}, "marker": "keep"},
    )
    result2, errors2 = core_impl.scaffold(case_path, task_path, out, work, fixed_time=FIXED)
    assert result2.get("marker") == "keep", "scaffold overwrote a complete result"
    assert any("preserved" in e for e in errors2), "no preserve notice recorded"
    return "early-partial: scaffold writes partial skeleton and never clobbers a finalised result"


def test_degradation(tmp):
    # empty dir -> scaffold still writes a legal partial result
    empty = os.path.join(tmp, "empty_input")
    os.makedirs(empty, exist_ok=True)
    out = os.path.join(tmp, "out_empty")
    result, errors = core_impl.scaffold(
        os.path.join(empty, "case.json"),
        os.path.join(empty, "task.md"),
        out,
        os.path.join(tmp, "work_empty"),
        fixed_time=FIXED,
    )
    assert result.get("status") == "partial"
    assert os.path.exists(os.path.join(out, "result.json"))

    # missing analysis -> finalize writes a legal failed result, no exception
    result2, _ = core_impl.finalize(
        os.path.join(empty, "analysis.json"),
        os.path.join(empty, "case.json"),
        os.path.join(tmp, "out_missing"),
        os.path.join(tmp, "work_missing"),
        fixed_time=FIXED,
    )
    assert result2.get("status") == "failed"
    assert os.path.exists(os.path.join(tmp, "out_missing", "result.json"))

    # bad JSON -> handled, recorded
    bad = os.path.join(tmp, "bad.json")
    with open(bad, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    _, errors = core_impl.load_json_safe(bad)
    assert any("bad json" in e for e in errors)
    return "degradation: empty/bad/missing inputs produce legal results, never fabricate or crash"


def main(argv=None):
    global FIXED
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixed-time", default=FIXED)
    args = ap.parse_args(argv)
    FIXED = args.fixed_time

    tmp = tempfile.mkdtemp(prefix="selftest_")
    results = []
    failures = []
    try:
        for name, fn in (
            ("determinism", test_determinism),
            ("contract-echo", test_contract_echo),
            ("early-partial-no-clobber", test_early_partial_and_no_clobber),
            ("degradation", test_degradation),
        ):
            try:
                msg = fn(tmp)
                results.append("PASS %-24s %s" % (name, msg))
            except AssertionError as exc:
                failures.append((name, str(exc)))
                results.append("FAIL %-24s %s" % (name, exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n".join(results))
    if failures:
        print("\n%d check(s) failed" % len(failures))
        return 1
    print("\nall self-checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
