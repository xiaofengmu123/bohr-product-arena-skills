#!/usr/bin/env python3
"""Self-test for the systematic-review Skill package.

Runs ``main.py`` end-to-end on synthetic fixtures (empty input, a minimal case
with output_contract, bad JSON, bad encoding) and verifies that every path
exits 0 and produces a valid, honestly-stated result. Uses temp directories
only; it never touches /input or /app/output.

Run:  python3 -B scripts/selftest.py
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
PY = sys.executable

PASS, FAIL = [], []


def run(args, cwd=None):
    return subprocess.run(
        [PY, "-B", MAIN] + args, capture_output=True, text=True, cwd=cwd,
        encoding="utf-8", errors="replace",
    )


def check(label, condition, detail=""):
    (PASS if condition else FAIL).append(label)
    print(("[PASS] " if condition else "[FAIL] ") + label + (("  " + detail) if detail else ""))


def read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def make_case(tmp, case_body, task_text="TASK placeholder"):
    inp = os.path.join(tmp, "input")
    os.makedirs(inp, exist_ok=True)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as handle:
        json.dump(case_body, handle, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as handle:
        handle.write(task_text)
    return inp


def write_work(tmp, protocol, screening=None, quality=None, evidence=None, synthesis=None):
    work = os.path.join(tmp, "work")
    os.makedirs(work, exist_ok=True)
    if protocol is not None:
        with open(os.path.join(work, "protocol.json"), "w", encoding="utf-8") as handle:
            json.dump(protocol, handle, ensure_ascii=False)
    for name, rows in (("screening.jsonl", screening), ("quality_appraisal.jsonl", quality),
                       ("evidence_cards.jsonl", evidence)):
        if rows is not None:
            with open(os.path.join(work, name), "w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    if synthesis is not None:
        with open(os.path.join(work, "synthesis.json"), "w", encoding="utf-8") as handle:
            json.dump(synthesis, handle, ensure_ascii=False)
    return work


def test_empty():
    with tempfile.TemporaryDirectory() as tmp:
        inp = os.path.join(tmp, "input")
        os.makedirs(inp, exist_ok=True)
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        proc = run(["skeleton", "--input", inp, "--output", out, "--work", work])
        check("empty input: exit 0", proc.returncode == 0, "rc=%s" % proc.returncode)
        result = read_json(os.path.join(out, "result.json"))
        check("empty input: status insufficient_evidence",
              result.get("status") == "insufficient_evidence", result.get("status"))


def test_contract():
    with tempfile.TemporaryDirectory() as tmp:
        case = {
            "case_id": "case-demo-1",
            "output_contract": {
                "review_question": "string",
                "screening_records": "array",
                "synthesis": {"type": "object"},
                "custom_field": "string",
            },
        }
        inp = make_case(tmp, case)
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        proc = run(["skeleton", "--input", inp, "--output", out, "--work", work])
        check("contract case: skeleton exit 0", proc.returncode == 0)
        result = read_json(os.path.join(out, "result.json"))
        check("contract case: case_id echoed", result.get("case_id") == "case-demo-1")
        for key in ("review_question", "screening_records", "synthesis", "custom_field"):
            check("contract case: field %s present" % key, key in result)
        check("contract case: array field typed []",
              result.get("screening_records") == [], str(result.get("screening_records")))
        check("contract case: string field typed ''",
              result.get("review_question") == "")

        proto = {
            "review_question": "Demo question?",
            "subquestions": ["a", "b"],
            "search_strategy": {"query_blocks": [
                {"database": "PubMed", "query": "x AND y", "intent": "core terms"}]},
            "inclusion_criteria": ["peer-reviewed"],
            "exclusion_criteria": ["case reports"],
        }
        screening = [
            {"record_id": "r1", "source": "Paper A", "stage": "title_abstract",
             "decision": "exclude", "reason": "wrong population",
             "criterion_id": "E1", "judge": "agent"},
            {"record_id": "r2", "source": "Paper B", "stage": "full_text",
             "decision": "include", "judge": "agent"},
        ]
        quality = [{"record_id": "r2", "overall_grade": "moderate",
                    "dimensions": [{"name": "internal_validity", "rating": "some",
                                    "basis_locator": "B#p3", "quote": "randomized"}]}]
        evidence = [{"evidence_id": "ev1", "source": "Paper B", "locator": "B#p3",
                     "quote": "randomized controlled trial", "claim": "RCT"}]
        synthesis = {"conclusions": [
            {"id": "c1", "text": "Limited evidence", "evidence_ids": ["ev1"]}],
            "conflicts": [{"text": "A vs B", "source_a": "Paper A", "source_b": "Paper B"}],
            "gaps": [{"text": "no long-term data", "scope": "follow-up > 1y"}],
            "uncertainty": {"conflicts": ["A vs B"]}}
        work = write_work(tmp, proto, screening, quality, evidence, synthesis)
        proc = run(["finalize", "--input", inp, "--output", out, "--work", work])
        check("contract case: finalize exit 0", proc.returncode == 0)
        result = read_json(os.path.join(out, "result.json"))
        check("contract case: final status ok", result.get("status") == "ok",
              result.get("status"))
        check("contract case: screening kept", len(result.get("screening_records") or []) == 2)
        check("contract case: prisma included", result.get("prisma_flow", {}).get("included") == 1)
        check("contract case: review.md written",
              os.path.exists(os.path.join(out, "review.md")))
        proc = run(["selfcheck", "--input", inp, "--output", out, "--work", work])
        report = json.loads(proc.stdout)
        check("contract case: selfcheck all pass", report["failed"] == [],
              "failed=%s" % report["failed"])


def test_bad_json():
    with tempfile.TemporaryDirectory() as tmp:
        inp = os.path.join(tmp, "input")
        os.makedirs(inp, exist_ok=True)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        proc = run(["skeleton", "--input", inp, "--output", out, "--work", work])
        check("bad JSON: exit 0", proc.returncode == 0, "rc=%s" % proc.returncode)
        result = read_json(os.path.join(out, "result.json"))
        check("bad JSON: status input_error", result.get("status") == "input_error",
              result.get("status"))


def test_bad_encoding():
    with tempfile.TemporaryDirectory() as tmp:
        inp = os.path.join(tmp, "input")
        os.makedirs(inp, exist_ok=True)
        case = {"case_id": "gbk-case-1", "output_contract": {"review_question": "string"},
                "note": "中文说明：围绕固定问题做综述。"}
        raw = json.dumps(case, ensure_ascii=False).encode("gb18030")
        with open(os.path.join(inp, "case.json"), "wb") as handle:
            handle.write(raw)
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        proc = run(["skeleton", "--input", inp, "--output", out, "--work", work])
        check("bad encoding: skeleton exit 0", proc.returncode == 0,
              "rc=%s" % proc.returncode)
        result = read_json(os.path.join(out, "result.json"))
        check("bad encoding: gb18030 JSON decoded", result.get("case_id") == "gbk-case-1",
              str(result.get("case_id")))
        check("bad encoding: not marked input_error",
              result.get("status") != "input_error", result.get("status"))


def main():
    test_empty()
    test_contract()
    test_bad_json()
    test_bad_encoding()
    print("")
    print("passed=%d failed=%d" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
