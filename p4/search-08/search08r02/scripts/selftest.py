"""selftest.py — offline self-test for the screening pipeline.

Runs three synthetic fixtures (empty input, bad JSON, normal case) and asserts
the runtime contract. These fixtures are synthetic examples, NOT formal case
answers; they exist only to prove the package runs.

Usage: python3 -B scripts/selftest.py
Exit code 0 means all checks passed, 1 means at least one failed.
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


def run(subcommand, input_dir, output_dir, work_dir):
    args = core_impl.build_parser().parse_args(
        [subcommand, "--input", input_dir, "--output", output_dir, "--work", work_dir, "--quiet"])
    return core_impl.dispatch(args)


def test_empty(tmp):
    d = os.path.join(tmp, "empty")
    os.makedirs(d)
    out = os.path.join(tmp, "out_empty")
    work = os.path.join(tmp, "work_empty")
    rc = run("skeleton", d, out, work)
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    assert rc == 0, f"empty skeleton rc={rc}"
    assert res["status"] == "insufficient_evidence", res["status"]
    assert res.get("case_id") in ("unknown", None), res.get("case_id")
    return True


def test_bad_json(tmp):
    d = os.path.join(tmp, "bad")
    os.makedirs(d)
    with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as f:
        f.write("{not valid json")
    out = os.path.join(tmp, "out_bad")
    work = os.path.join(tmp, "work_bad")
    rc = run("skeleton", d, out, work)
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    assert rc == 0, f"bad-json skeleton rc={rc}"
    assert res["status"] in ("partial", "input_error", "insufficient_evidence"), res["status"]
    cp = json.load(open(os.path.join(work, "checkpoint.json"), encoding="utf-8"))
    assert len(cp.get("degradations", [])) >= 1, "bad JSON should log a degradation"
    return True


def test_normal(tmp):
    d = os.path.join(tmp, "norm")
    os.makedirs(d)
    contract = {"candidate_list": None, "summary": None}
    papers = [
        {"title": "Effect of Drug A on Outcome B", "doi": "10.1000/abc.001",
         "abstract": "Randomised trial of drug A in adults.", "year": "2020"},
        {"title": "Effect of Drug A on Outcome B", "doi": "10.1000/abc.001",
         "abstract": "Duplicate export of the same trial.", "year": "2020"},
        {"title": "Animal study of drug A", "doi": "10.1000/zzz.003",
         "abstract": "Mouse model experiment.", "year": "2019"},
    ]
    case = {
        "case_id": "selftest-case-02",
        "research_question": "Drug A 的疗效",
        "inclusion_criteria": ["人群: 成人", "干预: Drug A"],
        "exclusion_criteria": ["动物研究"],
        "output_contract": contract,
        "records": papers,
    }
    with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as f:
        json.dump(case, f, ensure_ascii=False)
    out = os.path.join(tmp, "out_norm")
    work = os.path.join(tmp, "work_norm")

    assert run("skeleton", d, out, work) == 0
    assert run("protocol", d, out, work) == 0
    assert run("normalize", d, out, work) == 0

    jpath = os.path.join(work, "judgments.json")
    judgments = json.load(open(jpath, encoding="utf-8"))
    assert len(judgments) == 2, f"expected 2 unique records, got {len(judgments)}"
    for j in judgments:
        if "Animal" in j["title"]:
            j["decision"] = "exclude"
            j["reason_code"] = "ex_wrong_population"
            j["evidence_quote"] = "Mouse model experiment."
            j["evidence_locator"] = "abstract"
        else:
            j["decision"] = "include"
            j["reason_code"] = "in_population"
            j["evidence_quote"] = "Randomised trial of drug A in adults."
            j["evidence_locator"] = "abstract"
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(judgments, f, ensure_ascii=False, indent=2)

    assert run("arbitrate", d, out, work) == 0
    assert run("finalize", d, out, work) == 0

    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    assert res["status"] == "ok", res["status"]
    counts = res["audit"]["counts"]
    assert counts["unique"] == 2, counts
    assert counts["include"] == 1 and counts["exclude"] == 1, counts
    assert counts["closed"] is True, counts
    assert "candidate_list" in res and "summary" in res, "contract fields must be echoed"
    for artifact in ("screening_table.csv", "audit_log.json", "protocol.md"):
        assert os.path.exists(os.path.join(out, artifact)), artifact

    assert run("selfcheck", d, out, work) == 0
    sc = json.load(open(os.path.join(work, "selfcheck.json"), encoding="utf-8"))
    assert sc["all_pass"] is True, sc
    return True


def main():
    tmp = tempfile.mkdtemp(prefix="screening_selftest_")
    results = {}
    try:
        results["empty_input"] = test_empty(tmp)
        results["bad_json"] = test_bad_json(tmp)
        results["normal_case"] = test_normal(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    ok = all(results.values())
    print("SELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
