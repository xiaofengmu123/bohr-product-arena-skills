#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — self-check for the literature-screening Skill (stdlib only).

Runs three scenarios in temporary directories (never touches /app/output):
  1. empty input dir      -> exit 0, result.json with status=insufficient_evidence
  2. bad case.json        -> graceful degradation, status=input_error, errors recorded
  3. minimal synthetic    -> run pipeline twice, assert identical result.json
                             (determinism), and assert per-record contract fields.

The synthetic case is a *fixture for testing only*, clearly labelled, not a
prepared answer and not a formal test output.
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl  # noqa: E402


def _stable_view(result):
    """Deterministic view: drop wall-clock timestamps (runtime/change_log.ts),
    which legitimately differ between runs; keep every judgment field."""
    if not isinstance(result, dict):
        return result
    out = {}
    for key, val in result.items():
        if key == "runtime":
            continue
        if key == "change_log" and isinstance(val, list):
            out[key] = [{k: v for k, v in e.items() if k != "ts"} for e in val
                        if isinstance(e, dict)]
        else:
            out[key] = _stable_view(val)
    return out


def _run(input_dir):
    work = tempfile.mkdtemp(prefix="selftest_work_")
    out = tempfile.mkdtemp(prefix="selftest_out_")
    core_impl.run_discover(input_dir, work, out)
    core_impl.run_screen(work, out)
    errors, warnings = core_impl.run_finalize(work, out)
    result = core_impl._read_json(os.path.join(out, "result.json"))
    selfcheck = core_impl._read_json(os.path.join(out, "selfcheck.json"))
    shutil.rmtree(work, ignore_errors=True)
    shutil.rmtree(out, ignore_errors=True)
    return result, selfcheck, errors, warnings


def test_empty():
    d = tempfile.mkdtemp(prefix="selftest_empty_")
    result, selfcheck, errors, warnings = _run(d)
    shutil.rmtree(d, ignore_errors=True)
    assert result is not None, "empty input must still write result.json"
    assert result["status"] == "insufficient_evidence", result["status"]
    assert result["results"] == [], "empty input must not fabricate candidates"
    print("PASS empty input -> status=%s" % result["status"])


def test_bad_json():
    d = tempfile.mkdtemp(prefix="selftest_badjson_")
    with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{ this is not valid json ")
    with open(os.path.join(d, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# Test task\n")
    result, selfcheck, errors, warnings = _run(d)
    shutil.rmtree(d, ignore_errors=True)
    assert result is not None
    assert result["status"] in ("input_error", "insufficient_evidence", "partial"), \
        result["status"]
    assert any(e.get("scope") == "case.json" for e in result.get("errors", [])), \
        "bad JSON must be recorded in errors/degradations"
    print("PASS bad json -> status=%s, errors=%d" %
          (result["status"], len(result.get("errors", []))))


def test_deterministic():
    d = tempfile.mkdtemp(prefix="selftest_synth_")
    fixture = {
        "_fixture_note": "synthetic self-test fixture, not a prepared answer",
        "case_id": "selftest_case",
        "research_question": "Effect of early mobilisation on ICU delirium",
        "inclusion_criteria": "adult ICU RCT on early mobilisation",
        "exclusion_criteria": "pediatric; non-RCT",
        "output_contract": {"summary": "string", "ranked_papers": "array"},
        "papers": [
            {"id": "p1", "title": "Early mobilisation in the intensive care unit "
                                  "reduces delirium: a randomised controlled trial",
             "authors": "A. Doe; B. Roe", "year": 2024,
             "venue": "Crit Care", "doi": "10.1000/example.1",
             "abstract": "Randomised trial of early mobilisation versus usual care."},
            {"id": "p2", "title": "Sedation depth and delirium",
             "authors": "C. Moe", "year": 2020,
             "venue": None, "doi": None,
             "abstract": "Observational study of sedation depth."},
        ],
    }
    with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(fixture, fh, ensure_ascii=False)
    with open(os.path.join(d, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# Synthetic self-test task\n")

    r1, s1, e1, w1 = _run(d)
    r2, s2, e2, w2 = _run(d)
    shutil.rmtree(d, ignore_errors=True)

    assert _stable_view(r1) == _stable_view(r2), \
        "same input must yield identical judgments (determinism, timestamps aside)"
    assert r1["case_id"] == "selftest_case"
    assert "summary" in r1 and "ranked_papers" in r1, "output_contract fields echoed"
    assert r1["ranked_papers"] == [], "list-typed contract field defaulted to []"
    assert len(r1["results"]) == 2, "coverage of both candidates"
    for rec in r1["results"]:
        for field in ("record_id", "title", "relevance", "tier", "priority_score",
                      "rank", "reasons", "evidence", "confidence",
                      "criteria_matched", "conflict", "dedupe"):
            assert field in rec, "missing per-record field %s" % field
    print("PASS deterministic -> identical result.json across two runs; "
          "contract echoed; coverage=2")


def main():
    test_empty()
    test_bad_json()
    test_deterministic()
    print("selftest OK: 3/3 scenarios passed")


if __name__ == "__main__":
    main()
