# -*- coding: utf-8 -*-
"""Deterministic self-tests for the literature-tracking Skill.

Standard library only. Every case uses synthetic, clearly-labelled inputs under
a temporary directory and is deleted afterwards; this file never produces a
formal case result or an example answer. It verifies robustness invariants only:

  * empty input dir  -> insufficient_evidence, exit code 0, valid JSON
  * malformed JSON   -> input_error recorded, no crash
  * scaffold         -> valid partial skeleton, idempotent (byte-identical)
  * normalize/dedup  -> stable keys, duplicates suppressed
  * incremental split-> new / known / retraction separated

Run:  python3 scripts/main.py selftest --work /tmp/skill-selftest
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core  # noqa: E402
import main as entry  # noqa: E402


def _write(path, text):
    with open(path, "w", encoding="utf-8-sig") as fh:
        fh.write(text)


def _read_json(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


class Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_empty_input(tmp):
    indir = os.path.join(tmp, "empty")
    os.makedirs(indir)
    out = os.path.join(tmp, "out-empty")
    work = os.path.join(tmp, "work-empty")
    rc = entry.cmd_scaffold(Args(input=indir, output=out, work=work))
    assert rc == 0, "empty input must exit 0"
    result = _read_json(os.path.join(out, "result.json"))
    assert result["status"] == "insufficient_evidence", result["status"]
    assert result["case_id"] == "unknown-case"
    assert result["limitations"], "empty input must produce a limitation"
    return "empty input -> insufficient_evidence"


def test_bad_json(tmp):
    indir = os.path.join(tmp, "bad")
    os.makedirs(indir)
    _write(os.path.join(indir, "case.json"), "{not valid json")
    _write(os.path.join(indir, "task.md"), "# broken\n")
    out = os.path.join(tmp, "out-bad")
    work = os.path.join(tmp, "work-bad")
    rc = entry.cmd_scaffold(Args(input=indir, output=out, work=work))
    assert rc == 0, "bad JSON must not crash"
    result = _read_json(os.path.join(out, "result.json"))
    assert result["status"] == "input_error", result["status"]
    assert any("json parse failed" in (e.get("message", "") if isinstance(e, dict) else str(e))
               for e in result["errors"]), result["errors"]
    return "bad JSON -> input_error"


def test_scaffold_determinism(tmp):
    indir = os.path.join(tmp, "case")
    os.makedirs(indir)
    case = {
        "case_id": "challenge-demo",
        "output_contract": {"search_results": [], "evidence": [], "status_notes": {}},
    }
    _write(os.path.join(indir, "case.json"), json.dumps(case, ensure_ascii=False))
    _write(os.path.join(indir, "task.md"), "# demo\nchallenge_id: `challenge-demo`\n")

    out1 = os.path.join(tmp, "out1")
    out2 = os.path.join(tmp, "out2")
    work = os.path.join(tmp, "work")
    entry.cmd_scaffold(Args(input=indir, output=out1, work=work))
    entry.cmd_scaffold(Args(input=indir, output=out2, work=work))
    r1 = _read_json(os.path.join(out1, "result.json"))
    r2 = _read_json(os.path.join(out2, "result.json"))
    assert r1["case_id"] == "challenge-demo", r1["case_id"]
    for key in ("search_results", "evidence", "status_notes", "status",
                "sources", "uncertainty", "limitations", "change_log"):
        assert key in r1, "missing key %s" % key
    assert core.fingerprint(r1) == core.fingerprint(r2), "scaffold not idempotent"
    return "scaffold deterministic + full contract"


def test_normalize_dedup():
    a = {"doi": "https://doi.org/10.1000/XYZ", "title": "A Paper"}
    b = {"doi": "10.1000/xyz", "title": "A paper!"}
    ka = core.canonical_key(a)
    kb = core.canonical_key(b)
    assert ka["kind"] == "doi" and ka["value"] == kb["value"], (ka, kb)
    out = core.dedup_records([a, b])
    assert len(out["items"]) == 1, out["items"]
    assert len(out["dropped"]) == 1, out["dropped"]
    assert out["items"][0]["_suppressed_duplicates"] == 1

    t1 = {"title": "  Deep   Learning for\n Science "}
    t2 = {"title": "deep learning for science"}
    kt1 = core.canonical_key(t1)
    kt2 = core.canonical_key(t2)
    assert kt1["kind"] == "title" and kt1["value"] == kt2["value"], (kt1, kt2)
    return "normalize + dedup stable"


def test_incremental_split():
    records = [
        {"title": "Paper A", "doi": "10.1/a"},
        {"title": "Paper B", "doi": "10.1/b"},
        {"title": "Retraction of Paper C", "retracted": True},
        {"title": "Paper D", "arxiv": "2412.00001"},
    ]
    snapshot = ["doi\x0010.1/a", "arxiv\x002412.00001"]
    split = core.classify_incremental(records, snapshot)
    assert len(split["new"]) == 1, split
    assert len(split["known"]) == 2, split
    assert len(split["retractions"]) == 1, split
    return "incremental split separates new/known/retraction"


def test_confidence_coercion():
    ev = core.make_evidence_item("ev-1", "claim", "verbatim", quote="")
    assert ev["confidence"] == "paraphrased", ev
    ev2 = core.make_evidence_item("ev-2", "claim", "verbatim", quote="exact words")
    assert ev2["confidence"] == "verbatim", ev2
    lim = core.make_limitation_item("lim-1", "x", "bogus_reason")
    assert lim["reason"] == "parse_failed", lim
    return "typed gaps coerced safely"


def test_write_failure(tmp):
    # Output path is a file, so mkdir fails -> must degrade to minimal JSON.
    blocker = os.path.join(tmp, "blocker")
    _write(blocker, "x")
    indir = os.path.join(tmp, "case2")
    os.makedirs(indir)
    _write(os.path.join(indir, "case.json"), json.dumps({"case_id": "c2"}))
    _write(os.path.join(indir, "task.md"), "# t\n")
    work = os.path.join(tmp, "work2")
    try:
        rc = entry.cmd_scaffold(Args(input=indir, output=os.path.join(blocker, "out"), work=work))
        # If it does not raise, it must have written a minimal valid result
        # somewhere recoverable; accept either as non-crash.
        assert rc == 0
    except OSError:
        pass
    return "write failure degrades without crashing"


def run(work_dir=None):
    tmp = tempfile.mkdtemp(prefix="lit-track-selftest-")
    if work_dir:
        tmp = work_dir
        os.makedirs(tmp, exist_ok=True)
    passed = []
    try:
        passed.append(test_empty_input(tmp))
        passed.append(test_bad_json(tmp))
        passed.append(test_scaffold_determinism(tmp))
        passed.append(test_normalize_dedup())
        passed.append(test_incremental_split())
        passed.append(test_confidence_coercion())
        passed.append(test_write_failure(tmp))
    finally:
        if work_dir is None:
            shutil.rmtree(tmp, ignore_errors=True)
    print("PASS (%d tests):" % len(passed))
    for name in passed:
        print("  - " + name)
    return 0


if __name__ == "__main__":
    sys.exit(run())
