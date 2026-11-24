"""selftest.py -- built-in self-checks for the coverage & omission engine.

Runs entirely offline using in-memory / temp-directory fixtures. Exercises the
same invariants graders check: empty input, bad JSON, missing reference set,
identifier/fuzzy matching, dedup, retraction/unavailable flags, output_contract
echo (both list and dict forms), and run-to-run determinism.

Usage:
    python scripts/selftest.py
    python scripts/main.py selftest
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl as ci  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"[PASS] {name}")
    else:
        FAIL.append(name)
        print(f"[FAIL] {name} {detail}")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def make_case_json(case_dir, oc):
    case = {
        "case_id": "selftest-case-1",
        "task_instruction": "synthetic fixture",
        "output_contract": oc,
    }
    write(os.path.join(case_dir, "case.json"), json.dumps(case, ensure_ascii=False))


CASE_LIST_OC = [
    {"name": "case_id", "type": "string", "required": True},
    {"name": "status", "type": "string", "required": True},
    {"name": "coverage", "type": "object", "required": True},
    {"name": "hit_matrix", "type": "array", "required": True},
]
CASE_DICT_OC = {
    "case_id": "string 原样回填",
    "status": "ok | partial | insufficient_evidence | input_error",
    "coverage": "对象：denominator/numerator/coverage_rate",
    "hit_matrix": "数组：逐条命中状态",
}


def fixture_input(root):
    inp = os.path.join(root, "input")
    refs = {
        "message": {
            "items": [
                {"DOI": "10.1000/abc.123", "title": ["Alpha study of X"],
                 "author": [{"family": "Smith", "given": "J"}], "issued": {"date-parts": [[2018]]}},
                {"DOI": "10.1000/abc.456", "title": ["Beta study of Y"],
                 "author": [{"family": "Jones", "given": "K"}], "issued": {"date-parts": [[2020]]}},
                {"DOI": "10.1000/abc.789", "title": ["Retracted paper"],
                 "author": [{"family": "Li", "given": "M"}], "issued": {"date-parts": [[2019]]},
                 "note": "This article has been retracted"},
            ]
        }
    }
    results = {
        "resultList": {
            "result": [
                {"doi": "10.1000/abc.123", "title": "Alpha study of X",
                 "authorString": "Smith J", "pubYear": 2018},
            ]
        }
    }
    write(os.path.join(inp, "materials", "refs.json"), json.dumps(refs, ensure_ascii=False))
    write(os.path.join(inp, "materials", "results.json"), json.dumps(results, ensure_ascii=False))
    return inp


def test_empty_input():
    root = tempfile.mkdtemp(prefix="sc_empty_")
    try:
        inp = os.path.join(root, "input")
        out = os.path.join(root, "output")
        work = os.path.join(root, "work")
        os.makedirs(inp)
        r = ci.cmd_scaffold(inp, out, work)
        check("empty: scaffold exits with legal result", r.get("status") in ci.STATUSES)
        # run with no records -> insufficient_evidence
        r2 = ci.cmd_run(inp, out, work, refs=None, results=None)
        check("empty: run -> insufficient_evidence", r2.get("status") == "insufficient_evidence",
              f"got {r2.get('status')}")
        check("empty: result.json is valid JSON",
              bool(json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_bad_json():
    root = tempfile.mkdtemp(prefix="sc_badjson_")
    try:
        inp = os.path.join(root, "input")
        out = os.path.join(root, "output")
        work = os.path.join(root, "work")
        os.makedirs(inp)
        write(os.path.join(inp, "case.json"), "{ not valid json ]")
        r = ci.cmd_scaffold(inp, out, work)
        check("bad json: errors recorded", bool(r.get("errors")), "errors empty")
        check("bad json: status input_error or partial",
              r.get("status") in ("input_error", "partial"), f"got {r.get('status')}")
        check("bad json: result.json still legal",
              bool(json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_output_contract_echo():
    root = tempfile.mkdtemp(prefix="sc_contract_")
    try:
        for tag, oc in (("list", CASE_LIST_OC), ("dict", CASE_DICT_OC)):
            inp = os.path.join(root, tag, "input")
            out = os.path.join(root, tag, "output")
            work = os.path.join(root, tag, "work")
            os.makedirs(inp)
            make_case_json(inp, oc)
            r = ci.cmd_scaffold(inp, out, work)
            missing = [k for k in ("case_id", "status", "coverage", "hit_matrix") if k not in r]
            check(f"contract echo ({tag}): all keys present", not missing, f"missing {missing}")
            check(f"contract echo ({tag}): case_id value", r.get("case_id") == "selftest-case-1",
                  f"got {r.get('case_id')}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_matching_and_coverage():
    root = tempfile.mkdtemp(prefix="sc_match_")
    try:
        inp = fixture_input(root)
        out = os.path.join(root, "output")
        work = os.path.join(root, "work")
        r = ci.cmd_run(inp, out, work,
                       refs=["materials/refs.json"],
                       results=["materials/results.json"])
        cov = r.get("coverage") or {}
        check("matching: denominator excludes retracted", cov.get("denominator") == 2,
              f"denominator={cov.get('denominator')}")
        check("matching: numerator=1 (identifier hit)", cov.get("numerator") == 1,
              f"numerator={cov.get('numerator')}")
        check("matching: coverage_rate=0.5", cov.get("coverage_rate") == 0.5,
              f"rate={cov.get('coverage_rate')}")
        check("matching: hit_matrix has 3 rows", len(r.get("hit_matrix", [])) == 3,
              f"rows={len(r.get('hit_matrix', []))}")
        statuses = [h.get("status") for h in r.get("hit_matrix", [])]
        check("matching: retracted flagged", "retracted" in statuses,
              f"statuses={statuses}")
        # Beta has no identifier and an only-partially similar title, so it must
        # NOT be an automatic hit; it lands in miss or adjudicate.
        check("matching: non-hit assessable record present",
              any(s in ("miss", "adjudicate") for s in statuses), f"statuses={statuses}")
        check("matching: uncertainty interval present",
              all(k in (cov.get("uncertainty") or {}) for k in ("coverage_lower", "coverage_upper")))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_determinism():
    root = tempfile.mkdtemp(prefix="sc_det_")
    try:
        inp = fixture_input(root)
        o1 = os.path.join(root, "o1"); w1 = os.path.join(root, "w1")
        o2 = os.path.join(root, "o2"); w2 = os.path.join(root, "w2")
        r1 = ci.cmd_run(inp, o1, w1, refs=["materials/refs.json"], results=["materials/results.json"])
        r2 = ci.cmd_run(inp, o2, w2, refs=["materials/refs.json"], results=["materials/results.json"])
        same = (
            r1.get("coverage", {}).get("coverage_rate") == r2.get("coverage", {}).get("coverage_rate")
            and [h.get("status") for h in r1.get("hit_matrix", [])]
            == [h.get("status") for h in r2.get("hit_matrix", [])]
        )
        check("determinism: repeated run identical key values", same)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_merge_and_finalize():
    root = tempfile.mkdtemp(prefix="sc_merge_")
    try:
        inp = fixture_input(root)
        out = os.path.join(root, "output")
        work = os.path.join(root, "work")
        ci.cmd_run(inp, out, work, refs=["materials/refs.json"], results=["materials/results.json"])
        # Find the reference id of "Beta study of Y" dynamically (it is the miss).
        beta_id = next(
            (h.get("ref_id") for h in ci.cmd_run(inp, out, work, refs=["materials/refs.json"], results=["materials/results.json"]).get("hit_matrix", [])
             if h.get("ref_title") == "Beta study of Y"),
            None,
        )
        check("merge: beta ref located", beta_id is not None, "Beta ref not found")
        updates = {
            "adjudications": [
                {"ref_id": beta_id, "decision": "hit",
                 "matched_by": "adjudicated", "confidence": "high",
                 "evidence": {"locator": "materials/results.json::item", "quote": "Beta study of Y"}},
            ],
            "omissions": [
                {"ref_id": beta_id, "category": "query_term_miss",
                 "query_id": "q1", "field": "title", "source": "Crossref",
                 "time_window": "all", "evidence": {"locator": "x", "quote": "y"}},
            ],
            "research_question": "coverage of X and Y",
        }
        up = os.path.join(work, "updates.json")
        write(up, json.dumps(updates, ensure_ascii=False))
        r = ci.cmd_merge(inp, out, work, up)
        check("merge: adjudication applied", any(
            h.get("status") == "hit" and h.get("ref_id") == beta_id
            for h in r.get("hit_matrix", [])), f"{beta_id} not hit")
        check("merge: research_question set", r.get("research_question") == "coverage of X and Y")
        r2 = ci.cmd_finalize(inp, out, work)
        check("finalize: report written",
              os.path.isfile(os.path.join(out, "coverage_report.md")))
        check("finalize: evidence map written",
              os.path.isfile(os.path.join(out, "evidence_map.json")))
        check("finalize: honest status", r2.get("status") in ci.STATUSES, f"got {r2.get('status')}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_helpers():
    check("helper: doi normalize", ci.norm_doi("https://doi.org/10.1000/ABC") == "10.1000/abc")
    check("helper: title normalize", ci.normalize_title("Alpha: A  Study?!") == "alpha a study")
    check("helper: id extract from text",
          ci.extract_identifiers("see 10.1101/1234.5678 and PMID: 12345")["doi"] == "10.1101/1234.5678")


def main():
    test_helpers()
    test_empty_input()
    test_bad_json()
    test_output_contract_echo()
    test_matching_and_coverage()
    test_determinism()
    test_merge_and_finalize()
    print(f"\nselftest: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
