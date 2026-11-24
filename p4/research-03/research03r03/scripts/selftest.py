#!/usr/bin/env python3
"""Self-test harness for the verifiable systematic-review skill.

Runs the real scripts (scripts/main.py) against synthetic fixtures and asserts
the invariants that matter for grading: valid contract, honest status, verbatim
evidence, deterministic byte-identical output, and graceful handling of empty /
bad-JSON / bad-encoding inputs. Standard library only; no network.

Usage:  python -B scripts/selftest.py      (from the skill root)
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)
import core_impl as R

MAIN = os.path.join("scripts", "main.py")


def run_main(cwd, *args, timeout=120):
    cmd = [sys.executable, "-B", MAIN] + list(args)
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def write_file(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


QUOTE_A = "Patients were randomized 1:1 to intervention or control."
QUOTE_A_EFFECT = "Twelve-week follow-up showed a mean reduction of 4.2 mmHg."
QUOTE_B = "The trial reported no significant difference between groups."


def build_positive_fixture(root):
    inp = os.path.join(root, "input")
    os.makedirs(inp, exist_ok=True)
    case = {
        "case_id": "selftest-001",
        "research_question": "In adults with hypertension, does dietary sodium reduction lower blood pressure?",
        "output_contract": {
            "review_question": "",
            "subquestions": [],
            "search_strategy": {"query_blocks": []},
            "inclusion_criteria": [],
            "exclusion_criteria": [],
            "screening_records": [],
            "prisma_flow": {},
            "quality_appraisal": [],
            "synthesis": {"conclusions": [], "conflicts": [], "gaps": []},
            "evidence_map": [],
            "uncertainty": {},
            "extra_field": None,
        },
    }
    write_file(os.path.join(inp, "case.json"), json.dumps(case, ensure_ascii=False))
    write_file(os.path.join(inp, "task.md"), "# systematic review task\n")
    write_file(os.path.join(inp, "materials", "a.md"),
               "# Study A\n\n## Selection\n\n%s\n\n## Findings\n\n%s\n" % (QUOTE_A, QUOTE_A_EFFECT))
    write_file(os.path.join(inp, "materials", "b.md"),
               "# Study B\n\n## Findings\n\n%s\n" % QUOTE_B)
    return inp


def build_positive_work(work):
    evidence_id = "ev-" + R.stable_id("ev", "S01", "materials/a.md", QUOTE_A_EFFECT)
    protocol = {
        "review_question": "In adults with hypertension, does dietary sodium reduction lower blood pressure?",
        "subquestions": [
            {"id": "SQ1", "question": "What is the effect size on systolic blood pressure?", "depends_on": []},
            {"id": "SQ2", "question": "What is the risk of bias across trials?", "depends_on": ["SQ1"]},
        ],
        "search_strategy": {
            "databases": ["PubMed"],
            "run_date": None,
            "query_blocks": [{
                "id": "QB1", "database": "PubMed", "fields": ["title", "abstract"],
                "query": "(\"sodium reduction\" OR \"salt restriction\") AND \"blood pressure\" AND randomized controlled trial[pt]",
                "intent": "core intervention and outcome terms",
                "date_limit": "2015-01-01 to 2025-12-31", "language_limit": "English",
            }],
        },
        "inclusion_criteria": [
            {"id": "I1", "criterion": "randomized controlled trial", "kind": "inclusion", "priority": 1, "note": ""},
            {"id": "I2", "criterion": "reports systolic blood pressure outcome", "kind": "inclusion", "priority": 2, "note": ""},
        ],
        "exclusion_criteria": [
            {"id": "E1", "criterion": "non-human study", "kind": "exclusion", "priority": 1, "note": ""},
            {"id": "E2", "criterion": "does not report systolic blood pressure outcome", "kind": "exclusion", "priority": 2, "note": ""},
        ],
        "sources": [
            {"source_id": "S01", "file": "materials/a.md", "type": "fulltext", "title": "Study A"},
            {"source_id": "S02", "file": "materials/b.md", "type": "fulltext", "title": "Study B"},
        ],
        "pending_human": [], "limitations": ["only two synthetic studies"],
    }
    write_file(os.path.join(work, "protocol.json"), json.dumps(protocol, ensure_ascii=False))
    write_jsonl(os.path.join(work, "screening.jsonl"), [
        {"record_id": "SC01", "source_id": "S01", "stage": "title", "decision": "include",
         "reason": "", "criterion_id": "I1", "judge": "agent", "duplicate_of": None},
        {"record_id": "SC02", "source_id": "S02", "stage": "title", "decision": "include",
         "reason": "", "criterion_id": "I1", "judge": "agent", "duplicate_of": None},
        {"record_id": "SC03", "source_id": "S01", "stage": "fulltext", "decision": "include",
         "reason": "", "criterion_id": "I1", "judge": "agent", "duplicate_of": None},
        {"record_id": "SC04", "source_id": "S02", "stage": "fulltext", "decision": "exclude",
         "reason": "study reports no systolic blood pressure outcome", "criterion_id": "E2",
         "judge": "agent", "duplicate_of": None},
    ])
    write_jsonl(os.path.join(work, "quality.jsonl"), [
        {"record_id": "QA01", "source_id": "S01", "overall": "moderate",
         "dimensions": [{"dimension": "randomization", "rating": "low risk",
                         "basis_locator": "materials/a.md", "quote": QUOTE_A}],
         "impact_on_conclusions": "downgrade confidence to low due to single trial"},
    ])
    write_jsonl(os.path.join(work, "evidence.jsonl"), [
        {"source_id": "S01", "locator": "materials/a.md", "quote": QUOTE_A_EFFECT,
         "claim": "sodium reduction reduced systolic blood pressure by 4.2 mmHg", "kind": "fact"},
    ])
    write_file(os.path.join(work, "synthesis.json"), json.dumps({
        "conclusions": [{
            "claim": "Dietary sodium reduction lowers systolic blood pressure by about 4.2 mmHg in one included RCT.",
            "evidence_ids": [evidence_id], "confidence": "low",
            "scope": "single included trial; not generalizable",
        }],
        "conflicts": [{
            "claim": "Whether the effect is significant", "source_a": "S01", "source_b": "S02",
            "quote_a": "mean reduction of 4.2 mmHg", "quote_b": "no significant difference between groups",
            "file_a": "materials/a.md", "file_b": "materials/b.md", "resolution": "undetermined",
        }],
        "gaps": [{"gap": "no long-term evidence", "scope": "follow-up beyond 12 weeks", "evidence_status": "no_studies"}],
        "uncertainty": {"bias": ["single included trial"], "not_reported": ["standard errors"]},
    }, ensure_ascii=False))
    return evidence_id


def test_positive(root):
    inp = build_positive_fixture(root)
    work = os.path.join(root, "work")
    evidence_id = build_positive_work(work)

    code, out, err = run_main(SKILL_ROOT, "discover", "--input", inp, "--output", os.path.join(root, "out"), "--work", work)
    if code != 0:
        return False, "discover exit %d: %s" % (code, err)
    code, out, err = run_main(SKILL_ROOT, "skeleton", "--input", inp, "--output", os.path.join(root, "out"), "--work", work)
    if code != 0:
        return False, "skeleton exit %d: %s" % (code, err)

    out1 = os.path.join(root, "out1")
    out2 = os.path.join(root, "out2")
    code1, _, err1 = run_main(SKILL_ROOT, "finalize", "--input", inp, "--output", out1, "--work", work)
    code2, _, err2 = run_main(SKILL_ROOT, "finalize", "--input", inp, "--output", out2, "--work", work)
    if code1 != 0 or code2 != 0:
        return False, "finalize exits %d/%d: %s %s" % (code1, code2, err1, err2)

    result = read_json(os.path.join(out1, "result.json"))
    if result.get("case_id") != "selftest-001":
        return False, "wrong case_id"
    if result.get("status") != R.STATUS_OK:
        return False, "expected ok status, got %r" % result.get("status")
    for key in ("review_question", "subquestions", "search_strategy", "inclusion_criteria",
                "exclusion_criteria", "screening_records", "prisma_flow", "quality_appraisal",
                "synthesis", "evidence_map", "uncertainty", "extra_field"):
        if key not in result:
            return False, "missing contract field %s" % key
    for key in ("case_id", "status", "sources", "uncertainty", "change_log", "pending_human"):
        if key not in result:
            return False, "missing honest field %s" % key
    if result.get("prisma_flow", {}).get("included") != 1:
        return False, "prisma included != 1"
    concl = result["synthesis"]["conclusions"][0]
    if evidence_id not in concl["evidence_ids"]:
        return False, "conclusion evidence backlink lost"

    # Determinism: same input + work -> byte-identical deliverables.
    if read_bytes(os.path.join(out1, "result.json")) != read_bytes(os.path.join(out2, "result.json")):
        return False, "result.json differs across two runs"
    if read_bytes(os.path.join(out1, "review.md")) != read_bytes(os.path.join(out2, "review.md")):
        return False, "review.md differs across two runs"

    code, out, err = run_main(SKILL_ROOT, "selfcheck", "--json", "--input", inp, "--output", out1, "--work", work)
    if code != 0:
        return False, "selfcheck exit %d: %s" % (code, err)
    rep = json.loads(out)
    if not rep.get("ok"):
        return False, "selfcheck not ok: %s" % rep.get("fatal")
    quote_checks = [c for c in rep["checks"] if c["check"] == "quote_verbatim"]
    if not quote_checks or not all(c["ok"] for c in quote_checks):
        return False, "verbatim quote check failed: %s" % quote_checks
    return True, "positive flow ok, deterministic, selfcheck passed"


def test_empty_input(root):
    inp = os.path.join(root, "empty")
    os.makedirs(inp, exist_ok=True)
    out = os.path.join(root, "empty_out")
    work = os.path.join(root, "empty_work")
    code, _, err = run_main(SKILL_ROOT, "skeleton", "--input", inp, "--output", out, "--work", work)
    if code != 0:
        return False, "empty skeleton exit %d: %s" % (code, err)
    result = read_json(os.path.join(out, "result.json"))
    if result.get("status") != R.STATUS_INSUFFICIENT:
        return False, "empty input should be insufficient_evidence, got %r" % result.get("status")
    return True, "empty input handled"


def test_bad_json(root):
    inp = os.path.join(root, "badjson")
    os.makedirs(inp, exist_ok=True)
    write_file(os.path.join(inp, "case.json"), "{ not valid json")
    write_file(os.path.join(inp, "task.md"), "# task\n")
    out = os.path.join(root, "badjson_out")
    work = os.path.join(root, "badjson_work")
    code, _, err = run_main(SKILL_ROOT, "skeleton", "--input", inp, "--output", out, "--work", work)
    if code != 0:
        return False, "bad-json skeleton exit %d: %s" % (code, err)
    result = read_json(os.path.join(out, "result.json"))
    if result.get("status") != R.STATUS_INPUT_ERROR:
        return False, "bad JSON should be input_error, got %r" % result.get("status")
    return True, "bad JSON handled"


def test_bad_encoding(root):
    inp = os.path.join(root, "badenc")
    os.makedirs(os.path.join(inp, "materials"), exist_ok=True)
    write_file(os.path.join(inp, "case.json"), json.dumps({"case_id": "enc-1", "output_contract": {}}))
    write_file(os.path.join(inp, "task.md"), "# task\n")
    write_bytes(os.path.join(inp, "materials", "legacy.txt"), b"\x80\x81legacy bytes\xff")
    out = os.path.join(root, "badenc_out")
    work = os.path.join(root, "badenc_work")
    code, _, err = run_main(SKILL_ROOT, "skeleton", "--input", inp, "--output", out, "--work", work)
    if code != 0:
        return False, "bad-encoding skeleton exit %d: %s" % (code, err)
    code, _, err = run_main(SKILL_ROOT, "discover", "--input", inp, "--output", out, "--work", work)
    if code != 0:
        return False, "bad-encoding discover exit %d: %s" % (code, err)
    return True, "bad encoding handled without crash"


def main():
    root = tempfile.mkdtemp(prefix="sysrev-selftest-")
    tests = [
        ("positive flow + determinism", lambda: test_positive(root)),
        ("empty input", lambda: test_empty_input(root)),
        ("bad JSON input", lambda: test_bad_json(root)),
        ("bad encoding input", lambda: test_bad_encoding(root)),
    ]
    failed = 0
    try:
        for name, fn in tests:
            try:
                ok, detail = fn()
            except Exception as exc:  # noqa: BLE001
                ok, detail = False, "exception: %r" % exc
            print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, (" — " + detail) if detail else ""))
            if not ok:
                failed += 1
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("selftest: %d/%d passed" % (len(tests) - failed, len(tests)))
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
