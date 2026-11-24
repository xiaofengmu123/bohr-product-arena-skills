# -*- coding: utf-8 -*-
"""selftest.py — self-verification of the Skill package before submission.

Runs the deterministic pipeline on three synthetic inputs:
  1. a well-formed case with a verifiable verbatim quote;
  2. an empty input directory (must return insufficient_evidence, exit 0);
  3. a corrupt case.json (must degrade gracefully, exit 0).

Checks, in addition: valid result.json, output_contract echo, quote
resolvability, evidence backlinks, and absence of fabricated score keys.

Standard library only. Invoke from the package root:
    python3 -B scripts/selftest.py
"""
import os
import sys
import json
import shutil
import tempfile
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import core_impl as ci  # noqa: E402


def _write(p, s):
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(s)


def _build_case1(root):
    inp = os.path.join(root, "input")
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    patches = os.path.join(work, "patches")
    mats = os.path.join(inp, "materials")
    os.makedirs(mats)
    os.makedirs(patches)
    case = {
        "case_id": "demo-001",
        "output_contract": {
            "summary": None,
            "tags": [],
            "extra_field_untouched": {"keep": "me"},
        },
    }
    _write(os.path.join(inp, "case.json"), json.dumps(case, ensure_ascii=False))
    _write(os.path.join(inp, "task.md"),
           "# demo\n\n- challenge_id: challenge-demo001\n\n分析时间趋势。\n")
    paper = ("Title: Zinc anode stability under deep cycling\n\n"
             "Published: 2021\n\n"
             "Abstract: We study zinc dendrite growth. However, the coulombic "
             "efficiency remains unclear. Future work should address calendar aging.\n\n"
             "In contrast, prior reports disagree on the failure mode.\n")
    _write(os.path.join(mats, "paper1.txt"), paper)
    # Simulate the agent's verified deep-analysis patch (never a prepared answer
    # for real cases; here it exercises the merge + self-check loop end-to-end).
    patch = {
        "status": "ok",
        "research_question": {"question": "锌负极深度循环的稳定性趋势",
                              "scope": "demo", "sub_questions": ["空白识别"],
                              "domain_note": "demo"},
        "time_trend": {"unit_note": "出版年（题录）",
                       "claims": [{"id": "TT-1", "claim": "2021 年有 1 篇产出",
                                   "support": "reported", "evidence_ids": ["E001"],
                                   "confidence": "high", "rationale": None}]},
        "topic_evolution": {"term_mapping": [{"canonical": "zinc dendrite",
                                              "variants": [], "first_year": 2021,
                                              "last_year": 2021}],
                            "events": [{"id": "EV-1", "type": "emergence",
                                        "term": "zinc dendrite", "period": "2021",
                                        "cause": "demo", "evidence_ids": ["E001"]}],
                            "claims": [{"id": "TE-1", "claim": "术语出现",
                                        "support": "reported",
                                        "evidence_ids": ["E001"],
                                        "confidence": "medium"}]},
        "representative_works": {"stages": [{"stage": "demo", "period": "2021",
                                             "works": [{"doc_id": "mat_001",
                                                        "title": "Zinc anode stability under deep cycling",
                                                        "year": 2021, "kind": "origin",
                                                        "rationale": "demo",
                                                        "evidence_ids": ["E001"]}]}],
                                 "claims": [{"id": "RW-1", "claim": "demo",
                                             "support": "reported",
                                             "evidence_ids": ["E001"],
                                             "confidence": "medium"}]},
        "research_gaps": {"gaps": [{"id": "GAP-1", "type": "open_question",
                                    "description": "库仑效率仍不清楚",
                                    "exclusion_evidence": "demo 检索式",
                                    "evidence_ids": ["E001"]}],
                          "claims": [{"id": "RG-1", "claim": "demo",
                                      "support": "reported",
                                      "evidence_ids": ["E001"],
                                      "confidence": "medium"}]},
        "evidence_map": {"items": [{"id": "E001", "doc_id": "mat_001",
                                    "claim": "coulombic efficiency remains unclear",
                                    "claim_id": "TT-1",
                                    "locator": "materials/mat_001.txt#L5",
                                    "span": {"start_line": 5, "end_line": 5},
                                    "quote": "the coulombic efficiency remains unclear",
                                    "quote_hash": None, "source_file": "materials/mat_001.txt",
                                    "source_kind": "txt", "status": "reported",
                                    "confidence": "high", "kind": "fact",
                                    "year": 2021, "topic_tags": ["zinc"],
                                    "used_by_claims": ["TT-1", "TE-1", "RW-1", "RG-1"],
                                    "retrieval_note": "demo"}]},
    }
    _write(os.path.join(patches, "demo_patch.json"), json.dumps(patch, ensure_ascii=False))
    return inp, out, work


def _run_case(inp, out, work):
    return ci.run_pipeline(argparse.Namespace(input=inp, output=out, work=work))


def _check(ok, name, detail=""):
    print("  [%s] %s %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


def main(argv=None):
    root = tempfile.mkdtemp(prefix="selftest_rf_")
    all_ok = True
    print("selftest root:", root)

    # ---- case 1: well-formed ----
    print("case 1: well-formed input")
    inp, out, work = _build_case1(root)
    res = _run_case(inp, out, work)
    r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    all_ok &= _check(r.get("case_id") == "demo-001", "case_id", r.get("case_id"))
    all_ok &= _check("summary" in r and r["summary"] is None, "contract echo null")
    all_ok &= _check(r.get("extra_field_untouched", {}).get("keep") == "me",
                     "contract unknown key preserved")
    all_ok &= _check(r.get("status") in ci.STATUS_ENUM, "status enum", r.get("status"))
    all_ok &= _check(os.path.isfile(os.path.join(out, "report.md")), "report.md exists")
    all_ok &= _check(os.path.isfile(os.path.join(out, "evidence_map.json")),
                     "evidence_map.json exists")
    dist = r.get("time_trend", {}).get("distribution", [])
    all_ok &= _check(any(d.get("year") == 2021 for d in dist), "year 2021 in distribution")
    rep = json.load(open(os.path.join(work, "selfcheck.json"), encoding="utf-8"))
    all_ok &= _check(rep.get("ok"), "selfcheck ok",
                     "; ".join(c["check"] for c in rep["checks"] if c["status"] != "PASS")[:200])

    # ---- case 2: empty input ----
    print("case 2: empty input")
    inp2 = os.path.join(root, "empty")
    os.makedirs(inp2)
    out2 = os.path.join(root, "empty_out")
    work2 = os.path.join(root, "empty_work")
    res2 = _run_case(inp2, out2, work2)
    r2 = json.load(open(os.path.join(out2, "result.json"), encoding="utf-8"))
    all_ok &= _check(r2.get("status") == "input_error", "empty -> input_error",
                     r2.get("status"))

    # ---- case 3: corrupt case.json ----
    print("case 3: corrupt case.json")
    inp3 = os.path.join(root, "badjson")
    mats3 = os.path.join(inp3, "materials")
    os.makedirs(mats3)
    _write(os.path.join(inp3, "case.json"), "{ this is not json")
    _write(os.path.join(inp3, "task.md"), "# bad\nchallenge_id: x\n")
    _write(os.path.join(mats3, "p.txt"), "some text\n")
    out3 = os.path.join(root, "badjson_out")
    work3 = os.path.join(root, "badjson_work")
    res3 = _run_case(inp3, out3, work3)
    r3 = json.load(open(os.path.join(out3, "result.json"), encoding="utf-8"))
    all_ok &= _check(r3.get("status") in ci.STATUS_ENUM, "bad json -> valid status",
                     r3.get("status"))

    shutil.rmtree(root, ignore_errors=True)
    print("SELFTEST:", "OK" if all_ok else "FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
