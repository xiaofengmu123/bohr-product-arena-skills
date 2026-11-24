"""selftest.py — author-side self-test for the skill package.

Runs the CLI end-to-end in temporary directories and asserts robustness:
  1. `--help` exits 0
  2. a minimal case with output_contract runs scaffold -> set-plan -> import ->
     evidence -> assemble -> selfcheck, all PASS
  3. empty input -> insufficient_evidence, exit 0
  4. bad JSON -> input_error (or partial), exit 0
  5. bad encoding file -> skipped/degraded, exit 0
  6. repeat scaffold on the same input is byte-identical (deterministic)

Usage:  python3 -B scripts/selftest.py
Exit 0 means all checks passed; any failure exits 1.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAIN = os.path.join(HERE, "main.py")
PY = sys.executable


def run(*args, **kw):
    return subprocess.run([PY, "-B", MAIN, *args], capture_output=True, text=True, **kw)


def make_dirs(base):
    inp = os.path.join(base, "input")
    out = os.path.join(base, "output")
    work = os.path.join(base, "work")
    os.makedirs(inp, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    return inp, out, work


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def test_help():
    r = run("--help")
    assert r.returncode == 0, f"--help exited {r.returncode}"
    print("PASS help")


def test_full_flow():
    with tempfile.TemporaryDirectory() as base:
        inp, out, work = make_dirs(base)
        case = {
            "case_id": "demo-001",
            "research_question": "在何种时间范围与机制下，方法 X 对对象 Y 有效？",
            "output_contract": {"papers": [], "summary": "string"},
            "custom_field": "kept",
        }
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(case, fh, ensure_ascii=False)
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# demo\n")

        r = run("scaffold", "--input", inp, "--output", out, "--work", work)
        assert r.returncode == 0, f"scaffold {r.returncode}: {r.stderr}"
        skeleton = read_json(os.path.join(out, "result.json"))
        assert skeleton["status"] == "partial"
        assert skeleton["case_id"] == "demo-001"
        assert skeleton["output_contract"] == {"papers": [], "summary": "string"}
        assert skeleton["papers"] == [] and skeleton["summary"] is None

        plan = {
            "sub_questions": [{
                "id": "SQ1", "question": "子问题", "object": "Y", "mechanism": "M",
                "method": "方法", "time_range": "T", "comparison": "对照",
                "evidence_requirement": "RCT",
            }],
            "query_plan": [{"step": 1, "query": "X AND Y", "database": "paper_search",
                            "depends_on": [], "parallel_with": [], "rationale": "r"}],
            "term_expansion": [{"concept": "X", "variants": ["X1"], "disambiguation": None,
                                "expansion_limit": 6}],
            "stop_decision": {"status": "converged", "reason": "证据充分",
                              "coverage": {"SQ1": "included"}, "marginal_gain": "low"},
        }
        pfile = os.path.join(work, "plan_in.json")
        with open(pfile, "w", encoding="utf-8") as fh:
            json.dump(plan, fh, ensure_ascii=False)
        assert run("set-plan", "--file", pfile, "--work", work).returncode == 0

        records = [{
            "title": "Real paper title", "authors": ["A", "B"], "year": 2024,
            "journal": "Nature", "doi": "10.0000/example", "url": "https://example.org/1",
            "database": "paper_search", "sub_question_ids": ["SQ1"], "relevance": 0.9,
            "relevance_reason": "direct evidence", "screening": "include",
            "screening_reason": "matches SQ1", "evidence": {"locator": "materials/p.pdf#p3", "quote": "verbatim"},
        }, {
            "title": "Irrelevant paper", "authors": ["C"], "year": 2020, "journal": "J",
            "database": "web_search", "sub_question_ids": [], "relevance": 0.1,
            "screening": "exclude", "screening_reason": "not relevant",
            "evidence": {"locator": "web", "quote": "not relevant"},
        }]
        rfile = os.path.join(work, "records_in.json")
        with open(rfile, "w", encoding="utf-8") as fh:
            json.dump(records, fh, ensure_ascii=False)
        assert run("import", "--file", rfile, "--work", work).returncode == 0

        evidence = [{"query": "X AND Y", "source": "paper_search", "database": "paper_search",
                     "rationale": "plan step 1", "kind": "search"}]
        efile = os.path.join(work, "evidence_in.json")
        with open(efile, "w", encoding="utf-8") as fh:
            json.dump(evidence, fh, ensure_ascii=False)
        assert run("evidence", "--file", efile, "--work", work).returncode == 0

        r = run("assemble", "--input", inp, "--output", out, "--work", work,
                "--runtime-seconds", "120", "--runtime-requests", "7")
        assert r.returncode == 0, f"assemble {r.returncode}: {r.stderr}"
        final = read_json(os.path.join(out, "result.json"))
        assert final["status"] == "ok"
        assert len(final["results"]) == 1 and final["results"][0]["rank"] == 1
        assert final["results"][0]["score"] is not None
        assert final["screening"]["total_records"] == 2

        r = run("selfcheck", "--output", out, "--work", work)
        assert r.returncode == 0, f"selfcheck {r.returncode}: {r.stdout}"
        print("PASS full flow")


def test_empty_input():
    with tempfile.TemporaryDirectory() as base:
        inp, out, work = make_dirs(base)
        r = run("scaffold", "--input", inp, "--output", out, "--work", work)
        assert r.returncode == 0
        obj = read_json(os.path.join(out, "result.json"))
        assert obj["status"] == "insufficient_evidence"
        print("PASS empty input")


def test_bad_json():
    with tempfile.TemporaryDirectory() as base:
        inp, out, work = make_dirs(base)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ not json ")
        r = run("scaffold", "--input", inp, "--output", out, "--work", work)
        assert r.returncode == 0
        obj = read_json(os.path.join(out, "result.json"))
        assert obj["status"] == "input_error"
        print("PASS bad json")


def test_bad_encoding():
    with tempfile.TemporaryDirectory() as base:
        inp, out, work = make_dirs(base)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "enc-1"}, fh)
        with open(os.path.join(inp, "materials"), "w", encoding="utf-8") as fh:
            fh.write("x")
        with open(os.path.join(inp, "bad.bin"), "wb") as fh:
            fh.write(b"\xff\xfe\x00\x80 not utf8 \x9c")
        r = run("scaffold", "--input", inp, "--output", out, "--work", work)
        assert r.returncode == 0
        obj = read_json(os.path.join(out, "result.json"))
        assert obj["status"] in ("partial", "input_error")
        print("PASS bad encoding")


def test_determinism():
    with tempfile.TemporaryDirectory() as base:
        inp, out1, work1 = make_dirs(os.path.join(base, "a"))
        out2 = os.path.join(base, "b", "output")
        work2 = os.path.join(base, "b", "work")
        os.makedirs(out2, exist_ok=True)
        os.makedirs(work2, exist_ok=True)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "det-1", "output_contract": {"x": []}}, fh)
        assert run("scaffold", "--input", inp, "--output", out1, "--work", work1).returncode == 0
        assert run("scaffold", "--input", inp, "--output", out2, "--work", work2).returncode == 0
        with open(os.path.join(out1, "result.json"), "rb") as fh:
            a = fh.read()
        with open(os.path.join(out2, "result.json"), "rb") as fh:
            b = fh.read()
        assert a == b, "scaffold 结果在相同输入下不一致"
        print("PASS determinism")


def main():
    test_help()
    test_full_flow()
    test_empty_input()
    test_bad_json()
    test_bad_encoding()
    test_determinism()
    print("\nselftest: ALL PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"selftest: FAIL - {exc}", file=sys.stderr)
        raise SystemExit(1)
