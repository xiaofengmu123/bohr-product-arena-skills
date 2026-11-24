#!/usr/bin/env python3
"""Self-test suite for the research-question-driven search Skill.

Covers the reliability checklist end to end with real subprocess calls:
--help, empty input, missing input dir, bad JSON, bad encoding, oversized
file, the full plan->import->assemble->selfcheck flow, and repeat-run
determinism. Exit code 0 means every check passed.

Run from the package root:
    python3 -B scripts/selftest.py
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

MAIN = os.path.join(HERE, "main.py")
PY = sys.executable

PASSED = []
FAILED = []


def run(*argv):
    return subprocess.run([PY, MAIN] + list(argv), capture_output=True, text=True)


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print("PASS  " + name)
    else:
        FAILED.append(name)
        print("FAIL  " + name + ("  " + str(detail) if detail else ""))


def write(path, text, encoding="utf-8"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding=encoding) as fh:
        fh.write(text)


def write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def make_fixture(root, case_extra=None):
    inp = os.path.join(root, "input")
    case = {
        "case_id": "case-demo-01",
        "research_question": "Which interventions reduce ICU delirium, and by what mechanism?",
        "output_contract": {"answer": "object", "topics": "list"},
    }
    if case_extra:
        case.update(case_extra)
    write(os.path.join(inp, "case.json"), json.dumps(case, ensure_ascii=False))
    write(os.path.join(inp, "task.md"), "# 演示任务\n检索并筛选证据，保留筛选依据。\n")
    write(os.path.join(inp, "materials", "paper1.md"), "Paper one: dexmedetomidine reduces delirium.\n")
    return inp


def test_help(tmp):
    r = run("--help")
    check("help exits 0", r.returncode == 0, r.stderr)


def test_no_args(tmp):
    r = run()
    check("no args prints help and exits 0", r.returncode == 0 and "usage" in (r.stdout + r.stderr).lower(), r.returncode)


def test_empty_input(tmp):
    root = os.path.join(tmp, "empty")
    inp = os.path.join(root, "input")
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    os.makedirs(inp)
    r = run("init", "--input", inp, "--output", out, "--work", work)
    check("empty input init exits 0", r.returncode == 0, r.stderr)
    res = read_json(os.path.join(out, "result.json"))
    check("empty input -> insufficient_evidence", res.get("status") == "insufficient_evidence", res.get("status"))
    check("empty input still has case_id", isinstance(res.get("case_id"), str) and bool(res["case_id"]), res.get("case_id"))


def test_missing_input_dir(tmp):
    root = os.path.join(tmp, "missing")
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    r = run("init", "--input", os.path.join(root, "no_such_dir"), "--output", out, "--work", work)
    check("missing input dir init exits 0", r.returncode == 0, r.stderr)
    res = read_json(os.path.join(out, "result.json"))
    check("missing input -> input_error", res.get("status") == "input_error", res.get("status"))
    check("missing input error recorded", any(e.get("error") == "missing_input_dir" for e in res.get("errors", [])), res.get("errors"))


def test_bad_json(tmp):
    root = os.path.join(tmp, "badjson")
    inp = os.path.join(root, "input")
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    os.makedirs(inp)
    write(os.path.join(inp, "case.json"), "{ this is not valid json ")
    r = run("init", "--input", inp, "--output", out, "--work", work)
    check("bad JSON init exits 0", r.returncode == 0, r.stderr)
    res = read_json(os.path.join(out, "result.json"))
    check("bad JSON recorded in errors", any(e.get("error") == "bad_json" for e in res.get("errors", [])), res.get("errors"))


def test_bad_encoding(tmp):
    root = os.path.join(tmp, "badenc")
    inp = os.path.join(root, "input")
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    os.makedirs(inp)
    write(os.path.join(inp, "case.json"), '{"case_id": "enc-01"}')
    write_bytes(os.path.join(inp, "materials", "notes.md"), b"caf\xe9 delirium \xff\xfe notes")
    r = run("init", "--input", inp, "--output", out, "--work", work)
    check("bad encoding init exits 0", r.returncode == 0, r.stderr)
    res = read_json(os.path.join(out, "result.json"))
    check("bad encoding result still valid", "case_id" in res, res.get("case_id"))


def test_oversize(tmp):
    root = os.path.join(tmp, "oversize")
    inp = os.path.join(root, "input")
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    os.makedirs(inp)
    write(os.path.join(inp, "case.json"), '{"case_id": "big-01"}')
    write_bytes(os.path.join(inp, "materials", "big.md"), b"x" * (2 * 1024 * 1024 + 1))
    r = run("init", "--input", inp, "--output", out, "--work", work)
    check("oversize init exits 0", r.returncode == 0, r.stderr)
    res = read_json(os.path.join(out, "result.json"))
    check("oversize recorded in degradations", any(d.get("reason") == "oversize_skipped" for d in res.get("degradations", [])), res.get("degradations"))


def test_full_flow(tmp):
    root = os.path.join(tmp, "full")
    inp = make_fixture(root)
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")

    r = run("init", "--input", inp, "--output", out, "--work", work)
    check("full flow init exits 0", r.returncode == 0, r.stderr)

    subqs = [
        {"id": "SQ1", "object": "intervention", "mechanism": "sedation reduction", "method": "RCT", "time": "2015-2024", "comparison": "usual care", "evidence_requirement": "effect size"},
        {"id": "SQ2", "object": "dexmedetomidine", "mechanism": "neurotransmitter", "method": "mechanistic study", "time": "any", "comparison": "placebo", "evidence_requirement": "mechanism statement"},
    ]
    write(os.path.join(work, "subqs.json"), json.dumps(subqs, ensure_ascii=False))
    r = run("set-plan", "--kind", "sub_questions", "--file", os.path.join(work, "subqs.json"), "--work", work)
    check("set-plan sub_questions exits 0", r.returncode == 0, r.stderr)

    records = [
        {"title": "Dexmedetomidine for ICU delirium", "authors": ["Smith J", "Lee K"], "year": 2020, "journal": "Intensive Care Med", "doi": "10.1000/demo.1", "url": "https://example.org/a", "database": "paper_search", "sub_question_ids": ["SQ1"], "relevance": 0.9, "relevance_reason": "RCT of dexmedetomidine vs usual care", "screening": "included", "screening_reason": "direct evidence for SQ1", "evidence": [{"locator": "materials/paper1.md#L1", "quote": "dexmedetomidine reduces delirium"}], "score_breakdown": {"relevance": 0.9, "evidence": 0.8, "metadata": 0.9, "reliability": 0.9}},
        {"title": "Mechanism of dexmedetomidine action", "authors": ["Wang Y"], "year": 2019, "journal": "Anesthesiology", "doi": "10.1000/demo.2", "url": "https://example.org/b", "database": "paper_search", "sub_question_ids": ["SQ2"], "relevance": 0.8, "relevance_reason": "describes alpha-2 agonism mechanism", "screening": "included", "screening_reason": "covers SQ2 mechanism", "evidence": [{"locator": "materials/paper1.md#L1", "quote": "alpha-2 adrenergic agonism"}], "score_breakdown": {"relevance": 0.8, "evidence": 0.7, "metadata": 0.8, "reliability": 0.8}},
        {"title": "Off-topic review of sepsis", "authors": ["Zhou Q"], "year": 2021, "journal": "Crit Care", "doi": "10.1000/demo.3", "url": "https://example.org/c", "database": "web_search", "sub_question_ids": [], "relevance": 0.1, "relevance_reason": "not about delirium interventions", "screening": "excluded", "screening_reason": "out of scope", "evidence": [], "score_breakdown": {"relevance": 0.1, "evidence": 0.0, "metadata": 0.8, "reliability": 0.6}},
    ]
    write(os.path.join(work, "records.json"), json.dumps(records, ensure_ascii=False))
    r = run("import", "--file", os.path.join(work, "records.json"), "--work", work)
    check("import records exits 0", r.returncode == 0, r.stderr)

    write(os.path.join(work, "stop.json"), json.dumps({"status": "sufficient", "reason": "both sub-questions have direct evidence", "next": None}, ensure_ascii=False))
    r = run("set-plan", "--kind", "stop_decision", "--file", os.path.join(work, "stop.json"), "--work", work)
    check("set-plan stop_decision exits 0", r.returncode == 0, r.stderr)

    write(os.path.join(work, "ocv.json"), json.dumps({"answer": {"summary": "two interventions identified"}, "topics": ["delirium", "dexmedetomidine"]}, ensure_ascii=False))
    r = run("set-plan", "--kind", "output_contract_values", "--file", os.path.join(work, "ocv.json"), "--work", work)
    check("set-plan output_contract_values exits 0", r.returncode == 0, r.stderr)

    r = run("assemble", "--input", inp, "--output", out, "--work", work)
    check("assemble exits 0", r.returncode == 0, r.stderr)

    r = run("selfcheck", "--output", out, "--work", work)
    check("selfcheck exits 0", r.returncode == 0, r.stdout + r.stderr)

    res = read_json(os.path.join(out, "result.json"))
    check("full flow status ok", res.get("status") == "ok", res.get("status"))
    check("full flow 2 included results", len(res.get("results", [])) == 2, len(res.get("results", [])))
    check("no coverage gaps", res.get("uncertainty", {}).get("coverage_gaps") == [], res.get("uncertainty", {}).get("coverage_gaps"))
    check("output_contract fields echoed", res.get("answer") == {"summary": "two interventions identified"} and res.get("topics") == ["delirium", "dexmedetomidine"], (res.get("answer"), res.get("topics")))
    check("excluded record kept in ledger", any(x.get("screening") == "excluded" for x in res.get("screening", {}).get("ledger", [])), "ledger")
    check("screening.md and plan.md exist", os.path.isfile(os.path.join(out, "screening.md")) and os.path.isfile(os.path.join(out, "plan.md")))
    check("manifest.json exists", os.path.isfile(os.path.join(out, "manifest.json")))


def test_determinism(tmp):
    root = os.path.join(tmp, "det")
    inp = make_fixture(root)
    out1 = os.path.join(root, "o1")
    work1 = os.path.join(root, "w1")
    out2 = os.path.join(root, "o2")
    work2 = os.path.join(root, "w2")

    r1 = run("init", "--input", inp, "--output", out1, "--work", work1)
    r2 = run("init", "--input", inp, "--output", out2, "--work", work2)
    check("determinism: both inits exit 0", r1.returncode == 0 and r2.returncode == 0)

    res1 = read_json(os.path.join(out1, "result.json"))
    res2 = read_json(os.path.join(out2, "result.json"))
    body1 = {k: v for k, v in res1.items() if k != "run_meta"}
    body2 = {k: v for k, v in res2.items() if k != "run_meta"}
    check("determinism: analysis body byte-identical", json.dumps(body1, sort_keys=True, ensure_ascii=False) == json.dumps(body2, sort_keys=True, ensure_ascii=False))
    check("determinism: content hash matches", res1["run_meta"]["deterministic_content_sha256"] == res2["run_meta"]["deterministic_content_sha256"])


def main():
    tmp = tempfile.mkdtemp(prefix="skill-selftest-")
    try:
        test_help(tmp)
        test_no_args(tmp)
        test_empty_input(tmp)
        test_missing_input_dir(tmp)
        test_bad_json(tmp)
        test_bad_encoding(tmp)
        test_oversize(tmp)
        test_full_flow(tmp)
        test_determinism(tmp)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("")
    print("selftest summary: %d passed, %d failed" % (len(PASSED), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
