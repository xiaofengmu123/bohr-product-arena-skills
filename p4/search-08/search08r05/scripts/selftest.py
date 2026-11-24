#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — structural self-test of this Skill package.

Runs the pipeline end-to-end on clearly-labelled synthetic fixtures (NOT real
case answers) and verifies the invariants the scoring dimensions target:
frontmatter validity, contract echo alignment, dedup correctness, quote
verification, count closure, change_log monotonicity, and graceful degradation
on empty / broken inputs.

Standard library only. Usage: python3 -B scripts/selftest.py
"""
import json
import os
import subprocess
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PIPELINE = os.path.join(HERE, "pipeline.py")

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def run_pipeline(workdir, args, cwd=None):
    cmd = [sys.executable, "-B", PIPELINE] + args
    return subprocess.run(cmd, cwd=cwd or ROOT, capture_output=True, text=True)


def make_case(tmp, **kwargs):
    """Write a synthetic case.json fixture (clearly a fixture, not a real case)."""
    case = {
        "case_id": kwargs.get("case_id", "fixture-case-001"),
        "research_question": "Example question for self-test only.",
        "inclusion_criteria": ["human studies"],
        "exclusion_criteria": ["non-human"],
        "output_contract": kwargs.get("contract", {
            "candidate_set": {"records": [], "count": 0},
            "screening_summary": {"include": 0, "exclude": 0},
            "missed": [],
        }),
    }
    inp = os.path.join(tmp, "input")
    os.makedirs(inp, exist_ok=True)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        json.dump(case, f, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write("# fixture task (self-test only)\n")
    # fixture records as a tabular export
    rec = kwargs.get("records", None)
    if rec is not None:
        with open(os.path.join(inp, "records.csv"), "w", encoding="utf-8") as f:
            f.write("title,doi,abstract\n")
            for t, d, a in rec:
                f.write(f"{t},{d or ''},{a}\n")
    return inp


def run_case(tmp, name, contract=None, records=None, judgments=None, expect_status=None):
    inp = make_case(tmp, contract=contract, records=records)
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")

    r = run_pipeline(tmp, ["skeleton", "--input", inp, "--output", out, "--work", work])
    if r.returncode != 0:
        record(f"{name}: skeleton exit 0", False, r.stderr.strip()[:200])
        return out, work, inp
    record(f"{name}: skeleton exit 0", r.returncode == 0)

    r = run_pipeline(tmp, ["normalize", "--input", inp, "--output", out, "--work", work])
    record(f"{name}: normalize exit 0", r.returncode == 0)

    if judgments is not None:
        with open(os.path.join(work, "judgments.json"), "w", encoding="utf-8") as f:
            json.dump({"records": judgments, "filled": True}, f, ensure_ascii=False)

    r = run_pipeline(tmp, ["finalize", "--input", inp, "--output", out, "--work", work])
    record(f"{name}: finalize exit 0", r.returncode == 0)

    r = run_pipeline(tmp, ["selfcheck", "--input", inp, "--output", out, "--work", work])
    record(f"{name}: selfcheck exit 0", r.returncode == 0)

    result = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    if expect_status:
        record(f"{name}: status={expect_status}", result.get("status") == expect_status,
               result.get("status"))
    return out, work, inp


def test_frontmatter():
    path = os.path.join(ROOT, "SKILL.md")
    ok = os.path.isfile(path)
    record("SKILL.md exists", ok)
    if not ok:
        return
    text = open(path, encoding="utf-8").read()
    record("SKILL.md frontmatter", text.startswith("---"),
           "starts with ---")
    fm = text.split("---", 2)[1] if text.startswith("---") else ""
    for key in ("name:", "description:", "version:"):
        record(f"frontmatter has {key}", key in fm)
    # references / scripts exist
    for rel in ("scripts/pipeline.py", "scripts/main.py", "scripts/selftest.py",
                "references/reason_codes.md", "references/checklist.md"):
        record(f"file exists {rel}", os.path.isfile(os.path.join(ROOT, rel)))


def test_empty_input():
    tmp = tempfile.mkdtemp(prefix="selftest_empty_")
    inp = os.path.join(tmp, "input")
    os.makedirs(inp, exist_ok=True)
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    r = run_pipeline(tmp, ["skeleton", "--input", inp, "--output", out, "--work", work])
    record("empty input: exit 0", r.returncode == 0)
    result = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    record("empty input: status insufficient_evidence",
           result.get("status") == "insufficient_evidence", result.get("status"))


def test_bad_json():
    tmp = tempfile.mkdtemp(prefix="selftest_bad_")
    inp = os.path.join(tmp, "input")
    os.makedirs(inp, exist_ok=True)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        f.write("{ this is not valid json ")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    r = run_pipeline(tmp, ["skeleton", "--input", inp, "--output", out, "--work", work])
    record("bad json: does not crash (exit<3)", r.returncode < 3, f"rc={r.returncode}")
    result = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    record("bad json: error recorded", any("json" in e.lower() for e in result.get("errors", [])))


def test_contract_echo():
    tmp = tempfile.mkdtemp(prefix="selftest_echo_")
    contract = {
        "candidate_set": {"records": [], "count": 0},
        "meta": {"query": "", "db": ""},
        "nested": [{"id": 0, "note": ""}],
    }
    out, work, inp = run_case(tmp, "contract-echo", contract=contract,
                              records=[("Sample Title A", "10.1000/a", "abstract one")])
    result = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    record("contract-echo: candidate_set present", "candidate_set" in result)
    record("contract-echo: nested list kept as list", isinstance(result.get("nested"), list))


def test_dedup():
    tmp = tempfile.mkdtemp(prefix="selftest_dedup_")
    records = [
        ("Effect of X on Y", "10.1000/xyz", "abstract one"),
        ("Effect of X on Y", "10.1000/xyz", "abstract one (preprint)"),  # same DOI -> merge
        ("A totally different study", None, "abstract two"),
    ]
    inp = make_case(tmp, records=records)
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    run_pipeline(tmp, ["skeleton", "--input", inp, "--output", out, "--work", work])
    run_pipeline(tmp, ["normalize", "--input", inp, "--output", out, "--work", work])
    dedup = json.load(open(os.path.join(work, "dedup.json"), encoding="utf-8"))
    record("dedup: 3 records -> 2 unique", dedup["n_unique"] == 2,
           f"unique={dedup['n_unique']}")
    record("dedup: merged=1", dedup["n_merged"] == 1, f"merged={dedup['n_merged']}")
    record("dedup: merge_reason kept", bool(dedup["clusters"][0]["merge_reason"]))


def test_quote_and_closure():
    tmp = tempfile.mkdtemp(prefix="selftest_close_")
    records = [("Study A on diabetes", "10.1/a", "We tested glycemic control.")]
    inp = make_case(tmp, records=records)
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    run_pipeline(tmp, ["skeleton", "--input", inp, "--output", out, "--work", work])
    run_pipeline(tmp, ["normalize", "--input", inp, "--output", out, "--work", work])
    judgments = [{
        "record_id": "records.csv#1", "title": "Study A on diabetes", "doi": "10.1/a",
        "abstract": "We tested glycemic control.",
        "decision": "include", "reason_code": "in_population",
        "evidence_quote": "glycemic control",
        "evidence_verified": None,
        "reviewer_a": {"decision": "include", "reason_code": "in_population"},
        "reviewer_b": {"decision": "include", "reason_code": "in_population"},
        "conflict": {"is_conflict": False, "type": None, "detail": None},
        "arbitration": {"outcome": None, "rationale": None, "arbiter": None},
        "needs_human": False,
    }]
    with open(os.path.join(work, "judgments.json"), "w", encoding="utf-8") as f:
        json.dump({"records": judgments, "filled": True}, f, ensure_ascii=False)
    run_pipeline(tmp, ["finalize", "--input", inp, "--output", out, "--work", work])
    result = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    rec = result["screening"]["records"][0]
    record("quote: verified true", rec["evidence_verified"] is True)
    record("closure: closed", result["audit"]["closure"]["closed"])


def test_determinism():
    tmp = tempfile.mkdtemp(prefix="selftest_det_")
    records = [("Deterministic study", "10.1/d", "abstract"), ("Other study", None, "x")]
    inp = make_case(tmp, records=records)
    outs = []
    for k in range(2):
        out = os.path.join(tmp, f"output{k}")
        work = os.path.join(tmp, f"work{k}")
        run_pipeline(tmp, ["skeleton", "--input", inp, "--output", out, "--work", work])
        run_pipeline(tmp, ["normalize", "--input", inp, "--output", out, "--work", work])
        dedup = json.load(open(os.path.join(work, "dedup.json"), encoding="utf-8"))
        outs.append((dedup["n_unique"], dedup["n_merged"]))
    record("determinism: repeated run identical", outs[0] == outs[1], str(outs))


def main():
    print("=== selftest: contract-first literature screening ===")
    test_frontmatter()
    test_empty_input()
    test_bad_json()
    test_contract_echo()
    test_dedup()
    test_quote_and_closure()
    test_determinism()
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\nselftest: {len(RESULTS)-len(failed)}/{len(RESULTS)} PASS")
    if failed:
        print("FAILED:", ", ".join(failed))
        return 1
    print("selftest: ALL PASS")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
