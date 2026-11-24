#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — self-test for the search coverage contract audit Skill.

Runs the three mandated states locally without any network or external data:

  1. empty input directory        -> result.json valid, status=insufficient_evidence
  2. bad case.json (invalid JSON) -> result.json valid, error recorded, no crash
  3. normal synthetic case        -> full scaffold -> inspect -> run -> merge loop,
                                     coverage numbers recomputable, contract aligned

Usage:
    python scripts/selftest.py [--tmp DIR] [--keep]

Exit code 0 when all checks pass; non-zero otherwise. Deterministic (no
randomness, no wall-clock-dependent assertions).
"""

import argparse
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import coverage  # noqa: E402  (same directory)


def make_case(input_dir, files):
    os.makedirs(input_dir, exist_ok=True)
    for rel, content in files.items():
        p = os.path.join(input_dir, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(content)


def run_phase(input_dir, output_dir, work_dir, command, **kw):
    argv = [command, "--input", input_dir, "--output", output_dir, "--work", work_dir]
    for k, v in kw.items():
        if v is not None:
            argv += ["--" + k, v]
    rc = coverage.main(argv)
    assert rc == 0, "command %s returned %s" % (command, rc)
    rp = os.path.join(output_dir, "result.json")
    with open(rp, "r", encoding="utf-8") as fh:
        return json.load(fh)


def check_contract(result, label):
    problems = coverage.validate_result(result)
    assert not problems, "%s: contract violations %s" % (label, problems)
    assert result.get("case_id"), "%s: missing case_id" % label
    assert result.get("status") in coverage.STATUS_VALUES, "%s: bad status" % label
    assert isinstance(result.get("change_log"), list) and result["change_log"], \
        "%s: change_log empty" % label
    # change_log revisions must be monotonic
    revs = [c.get("revision") for c in result["change_log"]]
    assert revs == sorted(revs) and revs == list(range(1, len(revs) + 1)), \
        "%s: change_log revisions non-monotonic" % label


def test_empty(tmp):
    base = os.path.join(tmp, "empty")
    inp, out, work = base + "/input", base + "/output", base + "/work"
    os.makedirs(inp, exist_ok=True)
    r = run_phase(inp, out, work, "scaffold")
    assert r["status"] == "insufficient_evidence", "empty status=%s" % r["status"]
    check_contract(r, "empty")
    return "empty-dir"


def test_bad_json(tmp):
    base = os.path.join(tmp, "badjson")
    inp, out, work = base + "/input", base + "/output", base + "/work"
    make_case(inp, {"case.json": "{ this is not valid json !!!", "task.md": "# Topic\n"})
    r = run_phase(inp, out, work, "scaffold")
    assert r["status"] in ("partial", "input_error"), "badjson status=%s" % r["status"]
    assert any(e.get("error") == "case_json_invalid" for e in r.get("errors", [])), \
        "badjson: case_json_invalid not recorded"
    check_contract(r, "bad-json")
    # must also survive a run (no reference set -> insufficient_evidence)
    r2 = run_phase(inp, out, work, "run")
    assert r2["status"] == "insufficient_evidence"
    return "bad-json"


def test_normal(tmp):
    base = os.path.join(tmp, "normal")
    inp, out, work = base + "/input", base + "/output", base + "/work"

    case = {
        "case_id": "case-selftest-001",
        "challenge_id": "challenge-selftest",
        "output_contract": {
            "retrieval_results": "list of candidate records",
            "evidence_records": "queries and filters",
            "status_report": "empty/conflict/permission notes",
        },
        "research_question": "selftest research question",
    }
    # reference set: 3 known papers (one will be missed, one fuzzy-matched)
    refs = [
        {"DOI": "10.1000/ref.0001", "title": "Deep learning for protein folding",
         "author": [{"family": "Jumper", "given": "John"}], "issued": {"date-parts": [[2021]]}},
        {"DOI": "10.1000/ref.0002", "title": "Attention is all you need",
         "author": [{"family": "Vaswani", "given": "Ashish"}], "issued": {"date-parts": [[2017]]}},
        {"DOI": "10.1000/ref.0003", "title": "A missing paper never retrieved",
         "author": [{"family": "Smith", "given": "Alice"}], "issued": {"date-parts": [[2019]]}},
    ]
    # search results: only first two found, second with slight title/author variation
    results = [
        {"DOI": "10.1000/ref.0001", "title": "Deep learning for protein folding",
         "author": [{"family": "Jumper", "given": "John"}], "issued": {"date-parts": [[2021]]}},
        {"DOI": "10.1000/ref.0002", "title": "Attention Is All You Need",
         "author": [{"family": "Vaswani", "given": "Ashish"}], "issued": {"date-parts": [[2017]]}},
    ]
    make_case(inp, {
        "case.json": json.dumps(case),
        "task.md": "# Selftest coverage topic\n\nResearch question placeholder.\n",
        "reference_list.json": json.dumps({"references": refs}),
        "crossref_response.json": json.dumps({"status": "ok", "message": {"items": results}}),
    })

    run_phase(inp, out, work, "scaffold")
    run_phase(inp, out, work, "inspect")
    r = run_phase(inp, out, work, "run")

    # output_contract must be echoed verbatim from case.json
    assert r["output_contract"] == case["output_contract"], \
        "output_contract not echoed verbatim"
    assert r["case_id"] == "case-selftest-001"

    rs = r["reference_set"]
    assert rs["total_records"] == 3, "expected 3 frozen references, got %s" % rs["total_records"]
    cov = r["coverage"]
    assert cov["denominator"] == 3 and cov["numerator"] == 2, cov
    assert abs(cov["coverage_rate"] - round(2 / 3, 4)) < 1e-9
    assert cov["omission_rate"] == round(1 - 2 / 3, 4)
    assert len(r["omissions"]) == 1 and r["omissions"][0]["ref_doi"] == "10.1000/ref.0003"

    # determinism: run again, compare key numbers
    r2 = run_phase(inp, out, work, "run")
    assert r2["coverage"] == cov and [h["status"] for h in r2["hit_matrix"]] == \
        [h["status"] for h in r["hit_matrix"]], "non-deterministic output"

    # merge with attribution + one supplementary search that recovers the miss
    updates = {
        "omission_updates": [{
            "ref_id": r["omissions"][0]["ref_id"],
            "category": "query_synonym_gap",
            "signals": ["title_uses_unsynonymized_phrase"],
            "evidence": [{"locator": "reference_list.json", "quote": "A missing paper never retrieved"}],
            "query_attribution": "query1: 'protein folding' did not cover topic",
            "source_attribution": "crossref_response.json",
            "time_window_attribution": "2019 within window",
        }],
        "supplementary_searches": [{
            "search_id": "s2", "query": "extended synonym query",
            "source": "crossref_response.json", "time_window": None,
            "new_hits": [{"ref_id": r["omissions"][0]["ref_id"],
                          "evidence_locator": "crossref_response.json",
                          "quote": "10.1000/ref.0003"}],
            "added_count": 1,
        }],
        "residual_omissions": [],
        "status": "ok",
    }
    up = os.path.join(work, "updates.json")
    with open(up, "w", encoding="utf-8") as fh:
        json.dump(updates, fh)
    rm = run_phase(inp, out, work, "merge", updates=up)
    mg = rm["marginal_gain"]
    assert mg["before_omissions"] == 1 and mg["after_omissions"] == 0 and mg["reduction"] == 1, mg
    assert rm["coverage"]["numerator"] == 2, "merge must not inflate hits without adjudication"

    check_contract(rm, "normal-final")
    # validate subcommand
    rc = coverage.main(["validate", "--output", out])
    assert rc == 0
    # byproducts exist
    for f in ("hit_matrix.csv", "evidence_map.json", "coverage_report.md"):
        assert os.path.isfile(os.path.join(out, f)), "missing byproduct %s" % f
    return "normal"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tmp", default=None, help="temp root dir")
    ap.add_argument("--keep", action="store_true", help="do not delete temp dir")
    args = ap.parse_args()

    tmp = args.tmp or tempfile.mkdtemp(prefix="coverage-selftest-")
    os.makedirs(tmp, exist_ok=True)
    labels = []
    try:
        labels.append(test_empty(tmp))
        labels.append(test_bad_json(tmp))
        labels.append(test_normal(tmp))
    finally:
        if not args.keep and not args.tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    print("SELFTEST PASS: " + ", ".join(labels))
    return 0


if __name__ == "__main__":
    sys.exit(main())
