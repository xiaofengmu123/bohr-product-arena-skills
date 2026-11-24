#!/usr/bin/env python3
"""Self-test for the systematic-review Skill.

Builds synthetic inputs under a temporary directory and exercises
discover / skeleton / merge / finalize / selfcheck end-to-end, plus the
failure modes the contract requires: empty input, broken JSON, and a badly
encoded material file.

Run from anywhere (imports locate this script's directory):

    python -B scripts/selftest.py

Exit code 0 means every assertion passed. No score or benchmark result is
produced or reported; this only validates the Skill's own machinery.
"""

import contextlib
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main as M  # noqa: E402
import resultlib as R  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append({"name": name, "detail": detail})
    print(("PASS " if cond else "FAIL ") + name + ((" -- " + detail) if detail else ""))


def run(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = M.main(argv)
    return code, buf.getvalue()


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def scenario_empty(root):
    d = os.path.join(root, "empty")
    write_text(os.path.join(d, "input", ".keep"), "")
    code, _ = run(["skeleton", "--input", os.path.join(d, "input"),
                   "--output", os.path.join(d, "output"), "--work", os.path.join(d, "work")])
    r = read_json(os.path.join(d, "output", "result.json"))
    check("empty_input_valid_json", isinstance(r, dict))
    check("empty_input_status", r.get("status") == R.STATUS_INSUFFICIENT,
          "status=%s" % r.get("status"))


def scenario_bad_json(root):
    d = os.path.join(root, "badjson")
    write_text(os.path.join(d, "input", "case.json"), "{ this is not json")
    code, _ = run(["skeleton", "--input", os.path.join(d, "input"),
                   "--output", os.path.join(d, "output"), "--work", os.path.join(d, "work")])
    r = read_json(os.path.join(d, "output", "result.json"))
    check("bad_json_status", r.get("status") == R.STATUS_INPUT_ERROR,
          "status=%s" % r.get("status"))


def scenario_bad_encoding(root):
    d = os.path.join(root, "badenc")
    write_text(os.path.join(d, "input", "case.json"),
               json.dumps({"case_id": "enc-1", "output_contract": {"summary": None}}))
    write_bytes(os.path.join(d, "input", "materials", "notes.txt"),
                "caf\u00e9 \u00e9tude".encode("latin-1"))
    code, _ = run(["discover", "--input", os.path.join(d, "input")])
    check("bad_encoding_discover_exit0", code == 0, "exit=%d" % code)
    text, errs, degs = R.read_text(os.path.join(d, "input", "materials", "notes.txt"))
    check("bad_encoding_fallback", text is not None and ("caf" in text or "caf" in repr(text)),
          "decoded=%r degs=%s" % (text[:20] if text else None, degs))


def scenario_full(root):
    d = os.path.join(root, "full")
    inp = os.path.join(d, "input")
    work = os.path.join(d, "work")
    out = os.path.join(d, "output")

    material = ("Background text about a hypothetical therapy.\n"
                "Treatment X reduced mortality by 20 percent in 500 patients.\n"
                "A second sentence reports no difference in adverse events.\n")
    write_text(os.path.join(inp, "materials", "paper1.txt"), material)
    write_text(os.path.join(inp, "task.md"), "# task\nQuestion: effect of Treatment X on mortality.\n")
    write_text(os.path.join(inp, "case.json"), json.dumps({
        "case_id": "case-full-1",
        "output_contract": {
            "review_question": None,
            "subquestions": None,
            "search_strategy": None,
            "inclusion_criteria": None,
            "exclusion_criteria": None,
            "screening_records": None,
            "prisma_flow": None,
            "quality_appraisal": None,
            "synthesis": None,
            "evidence_map": None,
            "uncertainty": None,
            "custom_field": None,
        },
    }))

    quote = "Treatment X reduced mortality by 20 percent in 500 patients."
    write_text(os.path.join(work, "protocol.json"), json.dumps({
        "review_question": "In adults at risk, does Treatment X reduce all-cause mortality?",
        "subquestions": [
            {"id": "SQ1", "text": "Effect on all-cause mortality", "depends_on": []},
            {"id": "SQ2", "text": "Adverse-event profile", "depends_on": ["SQ1"]},
        ],
        "search_strategy": {
            "search_date": "2026-11-22",
            "databases": ["PubMed"],
            "query_blocks": [{
                "id": "QB1", "database": "PubMed",
                "query": '("Treatment X"[tiab]) AND (mortality[tiab] OR death[tiab]) AND humans[MeSH]',
                "intent": "find primary studies of Treatment X on mortality outcomes",
                "fields": ["tiab"], "date_range": "2015-01-01:2026-11-22", "language": "English",
            }],
        },
        "inclusion_criteria": [{"id": "I1", "text": "reports mortality outcome", "priority": 1}],
        "exclusion_criteria": [{"id": "E1", "text": "not a primary study", "priority": 1}],
    }))
    write_text(os.path.join(work, "screening.jsonl"), "\n".join([
        json.dumps({"record_id": "R1", "round": "title_abstract", "decision": "include",
                    "reason": "title/abstract report mortality outcome", "criterion_id": "I1",
                    "judge": "ai", "confidence": "high"}),
        json.dumps({"record_id": "R1", "round": "fulltext", "decision": "include",
                    "reason": "full text contains the outcome", "criterion_id": "I1",
                    "judge": "ai", "confidence": "high"}),
    ]) + "\n")
    write_text(os.path.join(work, "quality_appraisal.jsonl"),
               json.dumps({"record_id": "R1", "tool": "custom-casph-like", "overall_grade": "moderate",
                           "dimensions": [{"dimension": "internal_validity", "judgement": "low",
                                            "basis_locator": "materials/paper1.txt#L2", "quote": quote}],
                           "effect_on_conclusion": "downgrade to suggestive strength"}) + "\n")
    write_text(os.path.join(work, "evidence_cards.jsonl"),
               json.dumps({"evidence_id": "EV1", "record_id": "R1",
                           "claim": "Treatment X reduced mortality by 20% in 500 patients",
                           "locator": "materials/paper1.txt#L2", "quote": quote,
                           "confidence": "high", "type": "reported"}) + "\n")
    write_text(os.path.join(work, "synthesis.json"), json.dumps({
        "conclusions": [{"id": "C1",
                         "text": "One study reports Treatment X reduced mortality by 20% (suggestive).",
                         "evidence_ids": ["EV1"], "strength": "suggestive",
                         "scope": "single study, adults"}],
        "conflicts": [{"id": "CF1", "description": "mortality benefit vs unchanged adverse events",
                       "source_a": "paper1.txt mortality", "source_b": "paper1.txt adverse events",
                       "evidence_ids": ["EV1"], "resolution": "undetermined"}],
        "gaps": [{"id": "G1", "kind": "evidence", "description": "no long-term follow-up",
                  "impact": "cannot assess durability"}],
        "uncertainty": [{"type": "single_study", "item": "mortality estimate",
                         "effect": "conclusion limited to suggestive strength",
                         "handling": "reported; no extrapolation"}],
        "limitations": ["only one study provided"],
        "pending_human": [],
    }))

    run(["skeleton", "--input", inp, "--output", out, "--work", work])
    code, _ = run(["finalize", "--input", inp, "--output", out, "--work", work])
    r = read_json(os.path.join(out, "result.json"))
    check("full_finalize_status", r.get("status") == R.STATUS_OK, "status=%s" % r.get("status"))
    check("full_contract_fields", "custom_field" in r and "review_question" in r)
    check("full_prisma", r.get("prisma_flow", {}).get("included") == 1,
          "prisma=%s" % r.get("prisma_flow"))
    check("full_evidence_backlink",
          all(e in r.get("evidence_map", {}) for c in r["synthesis"]["conclusions"]
              for e in c.get("evidence_ids", [])))

    code, out_text = run(["selfcheck", "--input", inp, "--output", out, "--work", work])
    rep = json.loads(out_text)
    check("full_selfcheck_pass", rep.get("pass") is True,
          "errors=%d warns=%d" % (rep.get("error_count", -1), rep.get("warn_count", -1)))
    check("full_verbatim_verified",
          all(ch["pass"] for ch in rep["checks"] if ch["id"] == "evidence_verbatim"),
          "evidence_verbatim check")


def main():
    root = tempfile.mkdtemp(prefix="sci-review-selftest-")
    try:
        scenario_empty(root)
        scenario_bad_json(root)
        scenario_bad_encoding(root)
        scenario_full(root)
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)

    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
