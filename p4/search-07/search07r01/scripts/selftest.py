# -*- coding: utf-8 -*-
"""selftest.py - deterministic local self-test for the coverage Skill.

Runs three mandatory failure states plus one synthetic normal case against a
temporary directory, asserting:
  * exit code 0 for empty input / bad JSON / missing reference set
  * result.json is legal JSON and carries case_id + status
  * identical input twice yields identical coverage numbers (no randomness)
  * --help exits 0 and lists all four subcommands

The synthetic normal case is explicitly a *self-test fixture*, not a formal
case result. Run from the package root:

    python scripts/selftest.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(args, cwd=None):
    return subprocess.run([sys.executable, "-B", MAIN] + args,
                          cwd=cwd or HERE, capture_output=True, text=True)


def make_case(base, name, files):
    d = os.path.join(base, name)
    os.makedirs(d, exist_ok=True)
    for rel, content in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(content)
    return d


SAMPLE_REFS = json.dumps({
    "references": [
        {"title": "Vitamin D and bone mineral density in elderly women",
         "doi": "10.1000/xyz123", "first_author": "Smith", "year": 2018},
        {"title": "Calcium supplementation and fracture risk: a meta-analysis",
         "doi": "10.1000/abc456", "first_author": "Jones", "year": 2020},
        {"title": "Effects of bisphosphonates on bone turnover markers",
         "pmid": "12345678", "first_author": "Lee", "year": 2019},
    ]
})

SAMPLE_RESULTS = json.dumps({
    "message": {"items": [
        {"title": "Vitamin D and bone mineral density in elderly women",
         "DOI": "10.1000/xyz123", "author": [{"family": "Smith", "given": "A"}],
         "issued": {"date-parts": [[2018]]}},
        {"title": "Calcium supplementation and fracture risk a meta analysis",
         "DOI": "10.1000/abc456", "author": [{"family": "Jones", "given": "B"}],
         "issued": {"date-parts": [[2020]]}},
    ]}
})

SAMPLE_CASE = json.dumps({
    "case_id": "selftest-case-1",
    "challenge_id": "challenge-selftest",
    "research_question": "Which trials cover osteoporosis pharmacotherapy?",
    "output_contract": {"summary": None, "coverage_list": []}
})

SAMPLE_TASK = "# selftest task\nchallenge_id: challenge-selftest\n"


def check_result(outdir):
    p = os.path.join(outdir, "result.json")
    assert os.path.isfile(p), "result.json missing"
    with open(p, "r", encoding="utf-8") as fh:
        r = json.load(fh)
    assert r.get("case_id"), "case_id missing"
    assert r.get("status") in {"ok", "partial", "insufficient_evidence",
                               "input_error"}, "bad status"
    assert "sources" in r and "uncertainty" in r and "change_log" in r
    assert "evidence_map" in r
    assert "coverage" in r and "hit_matrix" in r and "omissions" in r
    return r


def main():
    base = tempfile.mkdtemp(prefix="cov-selftest-")
    fails = []
    try:
        # 0) help
        p = run(["--help"])
        if p.returncode != 0:
            fails.append("help exit %d" % p.returncode)
        for sub in ("scaffold", "inspect", "run", "merge"):
            if sub not in p.stdout:
                fails.append("help missing subcommand %s" % sub)

        # 1) empty input directory
        empty_in = make_case(base, "empty", {})
        empty_out = os.path.join(base, "empty-out")
        empty_work = os.path.join(base, "empty-work")
        p = run(["scaffold", "--input", empty_in, "--output", empty_out,
                 "--work", empty_work])
        if p.returncode != 0:
            fails.append("empty scaffold exit %d" % p.returncode)
        r = check_result(empty_out)
        if r["status"] != "insufficient_evidence":
            fails.append("empty status=%s" % r["status"])

        # 2) bad JSON material
        bad_in = make_case(base, "badjson", {
            "case.json": SAMPLE_CASE,
            "refs.json": "{ this is not valid json",
        })
        bad_out = os.path.join(base, "bad-out")
        bad_work = os.path.join(base, "bad-work")
        p = run(["scaffold", "--input", bad_in, "--output", bad_out,
                 "--work", bad_work])
        p2 = run(["inspect", "--input", bad_in, "--output", bad_out,
                  "--work", bad_work])
        p3 = run(["run", "--input", bad_in, "--output", bad_out,
                  "--work", bad_work])
        if p.returncode or p2.returncode or p3.returncode:
            fails.append("badjson exit %d/%d/%d" % (p.returncode, p2.returncode,
                                                     p3.returncode))
        r = check_result(bad_out)
        if not r.get("errors"):
            fails.append("badjson errors not recorded")

        # 3) normal case + determinism + merge attribution path
        norm_in = make_case(base, "normal", {
            "case.json": SAMPLE_CASE,
            "task.md": SAMPLE_TASK,
            "refs.json": SAMPLE_REFS,
            "crossref-results.json": SAMPLE_RESULTS,
        })
        for attempt in (1, 2):
            out = os.path.join(base, "normal-out-%d" % attempt)
            work = os.path.join(base, "normal-work-%d" % attempt)
            for cmd in (["scaffold"], ["inspect"], ["run"],
                        ["run", "--refs", "refs.json", "--results", "crossref-results.json"]):
                p = run(cmd + ["--input", norm_in, "--output", out,
                               "--work", work])
                if p.returncode != 0:
                    fails.append("normal %s exit %d: %s" % (cmd[0], p.returncode,
                                                             p.stderr[-400:]))
            r = check_result(out)
            cov = r["coverage"]
            if cov.get("denominator") != 3:
                fails.append("denominator=%s expected 3" % cov.get("denominator"))
            if cov.get("numerator") != 2:
                fails.append("numerator=%s expected 2" % cov.get("numerator"))
            if len(r["hit_matrix"]) != 3:
                fails.append("hit_matrix len=%d expected 3" % len(r["hit_matrix"]))
            if attempt == 1:
                first_sig = (cov.get("coverage_rate"), [h["status"] for h in r["hit_matrix"]])
            else:
                if (cov.get("coverage_rate"), [h["status"] for h in r["hit_matrix"]]) != first_sig:
                    fails.append("determinism broken between runs")

        # merge: attribute the single omission (bisphosphonates, R003) and
        # verify the BOM-tolerant updates reader applies it + status becomes ok
        updates = json.dumps({
            "attributions": [{
                "ref_id": "R003",
                "category": "source_not_indexed",
                "query": "bisphosphonate", "field": "title",
                "source": "Crossref", "time_window": "all",
                "evidence": {"locator": {"file": "refs.json", "path": "record.title"},
                             "quote": "Effects of bisphosphonates on bone turnover markers"},
            }],
            "supplementary_searches": [],
        })
        upd_path = os.path.join(base, "updates-bom.json")
        with open(upd_path, "w", encoding="utf-8-sig") as fh:
            fh.write(updates)
        m_out = os.path.join(base, "normal-out-1")
        m_work = os.path.join(base, "normal-work-1")
        p = run(["merge", "--input", norm_in, "--output", m_out,
                 "--work", m_work, "--updates", upd_path])
        if p.returncode != 0:
            fails.append("merge exit %d: %s" % (p.returncode, p.stderr[-400:]))
        r = check_result(m_out)
        if r["status"] != "ok":
            fails.append("merge status=%s expected ok (attribution applied)" % r["status"])
        if not r["omissions"][0].get("attribution"):
            fails.append("merge attribution not applied")
        if r["omissions"][0]["attribution"]["category"] != "source_not_indexed":
            fails.append("merge wrong category")
    except Exception as exc:  # pragma: no cover
        fails.append("selftest crashed: %r" % exc)
    finally:
        shutil.rmtree(base, ignore_errors=True)

    if fails:
        print("SELFTEST FAILED:")
        for f in fails:
            print("  -", f)
        return 1
    print("SELFTEST OK: help, empty-input, bad-JSON, normal-case, determinism")
    return 0


if __name__ == "__main__":
    sys.exit(main())
