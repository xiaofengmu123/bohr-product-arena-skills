"""Offline self-test for the deterministic screening pipeline.

Runs with synthetic fixtures only (no real case data), so it can be executed
anywhere, including before submission. Verifies the robustness and
determinism contracts:

* empty input -> insufficient_evidence, exit 0
* bad JSON / bad encoding -> degradations, no crash
* DOI dedup merges same study, similar titles are only flagged
* evidence quote verification (exact match)
* count closure
* two independent runs produce byte-identical result.json
* the selfcheck subcommand passes on a clean, well-formed work state

Usage:
    python3 -B scripts/selftest.py
Exits 0 when all checks pass, 1 otherwise.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pipeline  # noqa: E402

PASS = 0
FAIL = 0


def report(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  PASS %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s %s" % (name, ("-- " + detail) if detail else ""))


def write_case(inp, case, extra_files=None):
    inp.mkdir(parents=True, exist_ok=True)
    (inp / "case.json").write_text(json.dumps(case, ensure_ascii=False),
                                   encoding="utf-8")
    for name, content, mode in (extra_files or []):
        fp = inp / name
        fp.parent.mkdir(parents=True, exist_ok=True)
        if mode == "b":
            fp.write_bytes(content)
        else:
            fp.write_text(content, encoding="utf-8")


def scaffold(inp, wd, od):
    """Run skeleton -> normalize -> finalize on a synthetic input."""
    rc = pipeline.cmd_skeleton(inp, od, wd)
    pipeline.cmd_normalize(inp, od, wd)
    pipeline.cmd_finalize(inp, od, wd)
    return rc


def author_judgments(wd, assign):
    """Apply a per-record judgment function (record_id -> judgment dict)."""
    judgments = pipeline.load_json(wd / "judgments.json") or []
    for j in judgments:
        upd = assign(j)
        if upd:
            j.update(upd)
    pipeline.dump_json(wd / "judgments.json", judgments)


def test_empty_input():
    print("[empty input]")
    with tempfile.TemporaryDirectory() as tmp:
        inp = Path(tmp) / "input"
        inp.mkdir()
        wd = Path(tmp) / "work"
        od = Path(tmp) / "output"
        rc = pipeline.cmd_skeleton(inp, od, wd)
        result = pipeline.load_json(od / "result.json")
        report("exit 0", rc == 0)
        report("status insufficient_evidence",
               result and result.get("status") ==
               pipeline.STATUS_INSUFFICIENT)


def test_bad_json_and_encoding():
    print("[bad json / bad encoding]")
    case = {"case_id": "c-bad", "output_contract": {"candidates": None}}
    extra = [("broken.json", "{ not json", "t"),
             ("raw.bin", b"\xff\xfe\x00\xffbinary", "b"),
             ("records.jsonl",
              '{"title": "A valid record", "doi": "10.1/ok"}\n', "t")]
    with tempfile.TemporaryDirectory() as tmp:
        inp = Path(tmp) / "input"
        wd = Path(tmp) / "work"
        od = Path(tmp) / "output"
        write_case(inp, case, extra)
        rc = scaffold(inp, wd, od)
        result = pipeline.load_json(od / "result.json")
        report("exit 0", rc == 0)
        report("has degradations", bool(result and result.get("degradations")))
        report("kept valid record",
               result and len(result["screening"]["records"]) >= 1)


def test_dedup_and_evidence():
    print("[dedup & evidence]")
    case = {"case_id": "c-dedup",
            "output_contract": {"candidate_set": None, "n_excluded": None},
            "research_question": "Test question"}
    records = [
        {"title": "Efficacy of Drug X in Depression", "doi": "10.1/a",
         "abstract": "A randomized trial of Drug X for depression.",
         "year": "2020"},
        {"title": "Efficacy of Drug X in Depression (final version)",
         "doi": "10.1/a",
         "abstract": "A randomized trial of Drug X for depression.",
         "year": "2021"},
        {"title": "Efficacy of Drug X in Depression study",
         "doi": "10.2/b",
         "abstract": "Observational study of Drug X.",
         "year": "2022"},
    ]
    lines = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    with tempfile.TemporaryDirectory() as tmp:
        inp = Path(tmp) / "input"
        wd = Path(tmp) / "work"
        od = Path(tmp) / "output"
        write_case(inp, case, [("records.jsonl", lines, "t")])
        rc = scaffold(inp, wd, od)
        result = pipeline.load_json(od / "result.json")
        report("exit 0", rc == 0)
        dedup = result.get("deduplication", {})
        report("doi merge", dedup.get("n_input") == 3 and
               dedup.get("n_merged") == 1 and dedup.get("n_unique") == 2,
               "n_input=%s n_merged=%s n_unique=%s" % (
                   dedup.get("n_input"), dedup.get("n_merged"),
                   dedup.get("n_unique")))
        report("possible_duplicate flagged",
               len(dedup.get("possible_duplicates", [])) >= 1)

        by_title = {pipeline.normalize_title(r["title"]): r for r in records}

        def assign(j):
            src = by_title.get(pipeline.normalize_title(j.get("title", "")))
            if src is None:
                return None
            if src["doi"] == "10.1/a":
                return {"decision": "include", "reason_code": "in_population",
                        "evidence_locator": "abstract",
                        "evidence_quote": src["abstract"], "needs_human": False}
            return {"decision": "exclude", "reason_code": "ex_unrelated",
                    "evidence_locator": "title",
                    "evidence_quote": src["title"], "needs_human": False}

        author_judgments(wd, assign)
        pipeline.cmd_finalize(inp, od, wd)
        result = pipeline.load_json(od / "result.json")
        report("count closure", result["audit"]["closure"])
        verified = all(r["evidence_verified"]
                       for r in result["screening"]["records"]
                       if r["decision"] != "uncertain")
        report("evidence verified", verified)


def test_determinism():
    print("[determinism]")
    case = {"case_id": "c-det", "output_contract": {"results": None,
                                                    "question": None}}
    records = [
        {"title": "Paper One", "doi": "10.3/x", "abstract": "Abstract one."},
        {"title": "Paper Two", "doi": "10.3/y", "abstract": "Abstract two."},
    ]
    lines = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)

    def assign(j):
        return {"decision": "include", "reason_code": "in_population",
                "evidence_locator": "abstract",
                "evidence_quote": j.get("abstract", ""), "needs_human": False}

    with tempfile.TemporaryDirectory() as tmp:
        inp = Path(tmp) / "input"
        write_case(inp, case, [("records.jsonl", lines, "t")])
        wd1, od1 = Path(tmp) / "w1", Path(tmp) / "o1"
        wd2, od2 = Path(tmp) / "w2", Path(tmp) / "o2"
        scaffold(inp, wd1, od1)
        author_judgments(wd1, assign)
        pipeline.cmd_finalize(inp, od1, wd1)
        r1 = pipeline.load_json(od1 / "result.json")

        scaffold(inp, wd2, od2)
        author_judgments(wd2, assign)
        pipeline.cmd_finalize(inp, od2, wd2)
        r2 = pipeline.load_json(od2 / "result.json")

        report("two runs byte-identical",
               pipeline.canonical_json(r1) == pipeline.canonical_json(r2))


def test_selfcheck_runner():
    print("[selfcheck subcommand]")
    case = {"case_id": "c-sc", "output_contract": {"records": None}}
    records = [{"title": "Solo", "doi": "10.4/z", "abstract": "Solo abstract."}]
    lines = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    with tempfile.TemporaryDirectory() as tmp:
        inp = Path(tmp) / "input"
        wd = Path(tmp) / "work"
        od = Path(tmp) / "output"
        write_case(inp, case, [("records.jsonl", lines, "t")])
        scaffold(inp, wd, od)
        author_judgments(wd, lambda j: {
            "decision": "include", "reason_code": "in_population",
            "evidence_locator": "abstract", "evidence_quote": "Solo abstract.",
            "needs_human": False})
        pipeline.cmd_finalize(inp, od, wd)
        sc = pipeline.cmd_selfcheck(inp, od, wd)
        report("selfcheck passes", sc == 0)


def main():
    print("selftest: deterministic-literature-screening")
    test_empty_input()
    test_bad_json_and_encoding()
    test_dedup_and_evidence()
    test_determinism()
    test_selfcheck_runner()
    print("summary: %d PASS, %d FAIL" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
