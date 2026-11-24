#!/usr/bin/env python3
"""Self-test for the systematic-review-ledger Skill (standard library only).

Builds throwaway fixture directories (explicitly synthetic, not formal case
answers), runs scripts/main.py against them via subprocess, and asserts:

  * empty input  -> exit 0 + valid result.json with status insufficient_evidence
  * bad JSON     -> exit 0 + valid result.json with errors recorded
  * full review  -> status ok, selfcheck exit 0, deterministic re-run

Exit code is 0 when every check passes, 1 otherwise. No network, no third-party
packages, no writes outside the temporary directories.
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("PASS  %s%s" % (name, ("  " + detail) if detail else ""))
    else:
        FAIL.append(name)
        print("FAIL  %s%s" % (name, ("  " + detail) if detail else ""))


def run(args):
    return subprocess.run([sys.executable, "-B", MAIN] + args,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True)


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def make_full_input(base):
    inp = os.path.join(base, "input")
    out = os.path.join(base, "output")
    work = os.path.join(base, "work")
    os.makedirs(inp, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    write_json(os.path.join(inp, "case.json"), {
        "case_id": "selftest-case-1",
        "output_contract": {
            "review_question": None,
            "subquestions": [],
            "search_strategy": None,
            "inclusion_criteria": [],
            "exclusion_criteria": [],
            "screening_records": [],
            "prisma_flow": None,
            "quality_appraisal": [],
            "synthesis": None,
            "evidence_map": [],
            "uncertainty": None,
        },
    })
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# selftest task\n\nSynthetic fixture; not a formal case.\n")
    with open(os.path.join(inp, "paper-a.md"), "w", encoding="utf-8") as fh:
        fh.write("Paper A. The intervention reduced risk (RR 0.60).\n")
    return inp, out, work


def make_full_work(work):
    plan = {
        "protocol_version": "1.0",
        "frozen": True,
        "frozen_at": "2026-01-01T00:00:00Z",
        "review_question": "What is the effect of intervention X on outcome Y?",
        "subquestions": [
            {"id": "sq1", "question": "Effect size?", "depends_on": [], "rationale": "core"},
            {"id": "sq2", "question": "Heterogeneity?", "depends_on": ["sq1"], "rationale": "scope"},
        ],
        "search_strategy": {"query_blocks": [{
            "id": "q1", "database": "PubMed", "fields": "ti,ab",
            "query": '(intervention X) AND (outcome Y) AND ("2020"[dp] : "2026"[dp])',
            "synonyms": ["treatment X", "therapy X"], "controlled_terms": ["MeSH X"],
            "time_range": "2020-2026", "language_limits": "English",
            "intent": "core effect estimate", "status": "run", "hits": 1, "run_date": "2026-01-01",
        }]},
        "inclusion_criteria": [
            {"id": "in1", "statement": "human RCT", "priority": 1,
             "applies_to": "title_abstract", "version": 1},
        ],
        "exclusion_criteria": [
            {"id": "ex1", "statement": "no control group", "priority": 1,
             "applies_to": "fulltext", "version": 1},
        ],
        "quality_rubric": {"dimensions": [
            {"id": "q_design", "name": "design", "definition": "risk of bias",
             "scoring": "low/some/high"},
        ], "grades": ["high", "moderate", "low", "critical"]},
    }
    write_json(os.path.join(work, "plan.json"), plan)

    screening = [
        {"record_id": "scr-1", "round": "title_abstract", "source_id": "s1",
         "source_label": "paper-a.md", "decision": "include", "reason": "",
         "criterion_id": [], "judge": "agent", "duplicate_of": None},
        {"record_id": "scr-2", "round": "fulltext", "source_id": "s1",
         "source_label": "paper-a.md", "decision": "include", "reason": "",
         "criterion_id": [], "judge": "agent", "duplicate_of": None},
    ]
    with open(os.path.join(work, "screening.jsonl"), "w", encoding="utf-8") as fh:
        for r in screening:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    quality = [
        {"study_id": "s1", "dimensions": [
            {"dimension_id": "q_design", "rating": "low_risk",
             "basis_locator": "paper-a.md#L1", "quote": "randomized controlled trial"},
        ], "overall_grade": "moderate", "impact_on_conclusion": "downgrade to suggestive"},
    ]
    with open(os.path.join(work, "quality.jsonl"), "w", encoding="utf-8") as fh:
        for r in quality:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    evidence = [
        {"evidence_id": "ev-1", "source_id": "s1", "locator": "paper-a.md#L1",
         "quote": "The intervention reduced risk (RR 0.60).", "claim_type": "measured",
         "confidence": "high", "notes": ""},
    ]
    with open(os.path.join(work, "evidence.jsonl"), "w", encoding="utf-8") as fh:
        for r in evidence:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    synthesis = {
        "conclusions": [
            {"id": "c1", "statement": "Intervention X likely reduces outcome Y (suggestive).",
             "evidence_ids": ["ev-1"], "claim_type": "inference", "strength": "suggestive"},
        ],
        "conflicts": [],
        "gaps": [
            {"id": "g1", "description": "no long-term follow-up",
             "location": "paper-a.md reports a single time point", "evidence_basis": "ev-1"},
        ],
        "uncertainty": {"notes": "single small study", "bias_risks": ["single study"],
                        "unresolved": []},
    }
    write_json(os.path.join(work, "synthesis.json"), synthesis)

    ledger = [
        {"id": "led-1", "ts": "2026-01-01T00:00:00Z", "action": "criteria_freeze",
         "before": None, "after": "in1,ex1", "reason": "criteria fixed before screening",
         "actor": "agent"},
    ]
    with open(os.path.join(work, "ledger.jsonl"), "w", encoding="utf-8") as fh:
        for r in ledger:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def main():
    with tempfile.TemporaryDirectory(prefix="srledger-selftest-") as tmp:
        # 1. empty input
        e_inp = os.path.join(tmp, "empty", "input")
        e_out = os.path.join(tmp, "empty", "output")
        e_work = os.path.join(tmp, "empty", "work")
        os.makedirs(e_inp, exist_ok=True)
        os.makedirs(e_out, exist_ok=True)
        os.makedirs(e_work, exist_ok=True)
        r = run(["skeleton", "--input", e_inp, "--output", e_out, "--work", e_work])
        check("empty input skeleton exit 0", r.returncode == 0, str(r.returncode))
        res = read_json(os.path.join(e_out, "result.json"))
        check("empty input status insufficient_evidence",
              res.get("status") == "insufficient_evidence")
        check("empty input case_id present", bool(res.get("case_id")))

        # 2. bad JSON
        b_inp = os.path.join(tmp, "bad", "input")
        b_out = os.path.join(tmp, "bad", "output")
        b_work = os.path.join(tmp, "bad", "work")
        os.makedirs(b_inp, exist_ok=True)
        os.makedirs(b_out, exist_ok=True)
        os.makedirs(b_work, exist_ok=True)
        with open(os.path.join(b_inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        with open(os.path.join(b_inp, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# bad json fixture\n")
        r = run(["skeleton", "--input", b_inp, "--output", b_out, "--work", b_work])
        check("bad JSON skeleton exit 0", r.returncode == 0, str(r.returncode))
        res = read_json(os.path.join(b_out, "result.json"))
        check("bad JSON status input_error", res.get("status") == "input_error")
        check("bad JSON errors recorded", bool(res.get("errors")))

        # 3. full review
        inp, out, work = make_full_input(tmp)
        make_full_work(work)
        r = run(["skeleton", "--input", inp, "--output", out, "--work", work])
        check("full skeleton exit 0", r.returncode == 0, str(r.returncode))
        r = run(["plan", "--input", inp, "--output", out, "--work", work])
        check("plan exit 0", r.returncode == 0, str(r.returncode))
        r = run(["screening", "--input", inp, "--output", out, "--work", work])
        check("screening exit 0", r.returncode == 0, str(r.returncode))
        r = run(["evidence", "--input", inp, "--output", out, "--work", work])
        check("evidence exit 0", r.returncode == 0, str(r.returncode))
        r = run(["finalize", "--input", inp, "--output", out, "--work", work])
        check("finalize exit 0", r.returncode == 0, str(r.returncode))
        res = read_json(os.path.join(out, "result.json"))
        check("full status ok", res.get("status") == "ok", res.get("status"))
        check("prisma recounted included=1", res.get("prisma_flow", {}).get("included") == 1)
        check("evidence linkage ok", res.get("synthesis", {}).get("conclusions", [{}])[0]
              .get("evidence_ids") == ["ev-1"])
        check("deliverables present", bool(res.get("deliverables")))
        for artifact in ("review.md", "screening_log.json", "evidence_map.json"):
            check("artifact %s exists" % artifact,
                  os.path.exists(os.path.join(out, artifact)))

        r = run(["selfcheck", "--input", inp, "--output", out, "--work", work])
        check("selfcheck exit 0", r.returncode == 0, str(r.returncode))

        # 4. determinism: finalize twice, compare stable subset
        stable_keys = ("review_question", "subquestions", "screening_records",
                       "prisma_flow", "quality_appraisal", "evidence_map", "synthesis")
        r = run(["finalize", "--input", inp, "--output", out, "--work", work])
        first = {k: read_json(os.path.join(out, "result.json")).get(k) for k in stable_keys}
        r = run(["finalize", "--input", inp, "--output", out, "--work", work])
        second = {k: read_json(os.path.join(out, "result.json")).get(k) for k in stable_keys}
        check("deterministic assembly", first == second)

    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
