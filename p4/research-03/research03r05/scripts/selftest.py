#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline self-test for the systematic-review skill scripts.

Runs the pipeline against synthetic fixtures (empty input, bad JSON, a minimal
review case) and asserts robust, deterministic behaviour. This is a self-check,
not a benchmark: it fabricates no formal case results and no scores.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as R
import main as M


def ns(**kwargs):
    args = argparse.Namespace()
    for key, value in kwargs.items():
        setattr(args, key, value)
    return args


def run_pipeline(input_dir, output_dir, work_dir):
    args = ns(input=input_dir, output=output_dir, work=work_dir)
    M.cmd_skeleton(args)
    M.cmd_finalize(args)
    report = M.cmd_selfcheck(args)
    with open(os.path.join(output_dir, "result.json"), "r", encoding="utf-8") as fh:
        result = json.load(fh)
    return result, report


def write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def case_empty(tmp):
    d = os.path.join(tmp, "empty")
    os.makedirs(os.path.join(d, "input"))
    return d, d + "/input", d + "/output", d + "/work"


def case_bad_json(tmp):
    d = os.path.join(tmp, "badjson")
    os.makedirs(os.path.join(d, "input"))
    write(os.path.join(d, "input", "case.json"), "{ not json !!")
    write(os.path.join(d, "input", "task.md"), "# task\n")
    return d, d + "/input", d + "/output", d + "/work"


def case_mini(tmp):
    d = os.path.join(tmp, "mini")
    os.makedirs(os.path.join(d, "input"))
    case = {
        "case_id": "selftest-001",
        "output_contract": {
            "review_question": "string",
            "search_strategy": {"type": "object", "fields": {
                "query_blocks": "array", "databases": "array"}},
            "screening_records": "array",
            "synthesis": {"type": "object", "fields": {
                "conclusions": "array", "gaps": "array"}},
            "evidence_map": "array",
            "custom_extra": "string",
        },
    }
    write(os.path.join(d, "input", "case.json"), json.dumps(case, ensure_ascii=False))
    write(os.path.join(d, "input", "task.md"), "# mini case\n")
    write(os.path.join(d, "input", "paper.md"),
          "Treatment X reduced Y in 40 patients (p=0.03).")
    return d, d + "/input", d + "/output", d + "/work"


def seed_work(work_dir):
    os.makedirs(work_dir, exist_ok=True)
    protocol = {
        "review_question": "Does X reduce Y?",
        "subquestions": ["SQ1", "SQ2"],
        "search_strategy": {"databases": ["PubMed"], "query_blocks": [
            {"database": "PubMed", "query": "X AND Y", "intent": "core hit",
             "time_range": "2015-2026", "language_limits": ["en"]}]},
        "inclusion_criteria": ["I1"],
        "exclusion_criteria": ["E1"],
    }
    write(os.path.join(work_dir, "protocol.json"), json.dumps(protocol, ensure_ascii=False))
    screening = [
        {"id": "r1", "stage": "title_abstract", "decision": "include",
         "judge": "agent", "reason": "", "criterion_id": ""},
        {"id": "r2", "stage": "title_abstract", "decision": "exclude",
         "judge": "agent", "reason": "wrong outcome", "criterion_id": "E1"},
        {"id": "r1", "stage": "full_text", "decision": "include",
         "judge": "agent", "reason": "", "criterion_id": ""},
    ]
    write(os.path.join(work_dir, "screening.jsonl"),
          "\n".join(json.dumps(x, ensure_ascii=False) for x in screening) + "\n")
    quality = [
        {"id": "r1", "dimension": "randomisation", "rating": "low risk",
         "basis_locator": "paper.md#L1", "quote": "40 patients", "impact": "supports"},
    ]
    write(os.path.join(work_dir, "quality_appraisal.jsonl"),
          "\n".join(json.dumps(x, ensure_ascii=False) for x in quality) + "\n")
    evidence = [
        {"evidence_id": "e1", "locator": "paper.md#L1",
         "quote": "Treatment X reduced Y in 40 patients (p=0.03)."},
    ]
    write(os.path.join(work_dir, "evidence_cards.jsonl"),
          "\n".join(json.dumps(x, ensure_ascii=False) for x in evidence) + "\n")
    synthesis = {
        "conclusions": [{"claim": "X reduces Y (weak evidence)",
                         "evidence_ids": ["e1"]}],
        "conflicts": [],
        "gaps": [{"gap": "no long-term follow-up", "evidence_ids": []}],
    }
    write(os.path.join(work_dir, "synthesis.json"), json.dumps(synthesis, ensure_ascii=False))


def check(cond, message):
    if not cond:
        raise AssertionError(message)


def main():
    failures = []
    tmp = tempfile.mkdtemp(prefix="sci-skill-selftest-")
    try:
        # Case 1: empty input -> insufficient_evidence, exit-safe, valid JSON.
        _, i1, o1, w1 = case_empty(tmp)
        r1, rep1 = run_pipeline(i1, o1, w1)
        check(r1.get("status") == R.STATUS_INSUFFICIENT,
              "empty input should be insufficient_evidence, got %r" % r1.get("status"))
        check(r1.get("case_id") == "unknown", "empty input case_id should be unknown")
        check(set(R.HONEST_FIELDS).issubset(set(r1.keys())),
              "honest fields missing in empty-input result")

        # Case 2: bad JSON -> input_error, still a valid result.json.
        _, i2, o2, w2 = case_bad_json(tmp)
        r2, rep2 = run_pipeline(i2, o2, w2)
        check(r2.get("status") == R.STATUS_INPUT_ERROR,
              "bad JSON should be input_error, got %r" % r2.get("status"))
        check(isinstance(r2.get("errors"), list) and r2.get("errors"),
              "bad JSON case should record errors")

        # Case 3: minimal review case -> contract aligned, evidence backlinked.
        _, i3, o3, w3 = case_mini(tmp)
        seed_work(w3)
        r3, rep3 = run_pipeline(i3, o3, w3)
        for field in ("review_question", "search_strategy", "screening_records",
                      "synthesis", "evidence_map", "custom_extra"):
            check(field in r3, "contract field missing: %s" % field)
        check(r3.get("custom_extra") is None, "unfilled string field should be null")
        check(r3.get("review_question") == "Does X reduce Y?", "protocol not applied")
        check(len(r3.get("screening_records") or []) == 3, "screening not applied")
        check(r3.get("prisma_flow", {}).get("included") == 1, "PRISMA included should be 1")
        check(r3.get("prisma_flow", {}).get("excluded_title_abstract") == 1,
              "PRISMA title/abstract exclusion should be 1")
        check(rep3.get("passed") is True, "selfcheck should pass: %r" % rep3)
        check(not rep3.get("contract_problems"), "contract problems should be empty")
        check(not rep3.get("synthesis_issues"), "synthesis backlinks should resolve")

        # Determinism of content fields across a second finalize.
        before = {k: r3.get(k) for k in ("review_question", "screening_records",
                                          "evidence_map", "synthesis", "prisma_flow")}
        M.cmd_finalize(ns(input=i3, output=o3, work=w3))
        with open(os.path.join(o3, "result.json"), "r", encoding="utf-8") as fh:
            r3b = json.load(fh)
        after = {k: r3b.get(k) for k in before}
        check(before == after, "content fields should be deterministic across finalize")

        # Sidecars exist and are valid JSON where applicable.
        for name in ("review.md", "screening_log.json", "evidence_map.json",
                     "contract_report.json", "result.json"):
            check(os.path.exists(os.path.join(o3, name)), "missing sidecar: %s" % name)
        for name in ("screening_log.json", "evidence_map.json", "contract_report.json"):
            with open(os.path.join(o3, name), "r", encoding="utf-8") as fh:
                json.load(fh)
    except AssertionError as exc:
        failures.append(str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        failures.append("unexpected %s: %s" % (type(exc).__name__, exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELFTEST FAIL")
        for failure in failures:
            print(" -", failure)
        return 1
    print("SELFTEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
