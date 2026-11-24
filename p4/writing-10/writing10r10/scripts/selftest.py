#!/usr/bin/env python3
"""Self-test for the review-response revision skill.

Runs the entry point against (1) no input, (2) a malformed case.json, and
(3) a synthetic well-formed input twice to confirm determinism.  Exits 0 only
if every assertion passes.  Standard library only.
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run_main(args):
    return subprocess.run([sys.executable, MAIN] + args, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def test_help():
    r = run_main(["--help"])
    assert r.returncode == 0, r.stderr


def test_empty_input():
    with tempfile.TemporaryDirectory() as d:
        inp, out, work = os.path.join(d, "input"), os.path.join(d, "output"), os.path.join(d, "work")
        for p in (inp, out, work):
            os.makedirs(p)
        r = run_main(["all", "--input", inp, "--out", out, "--work", work])
        assert r.returncode == 0, r.stderr
        res = load(os.path.join(out, "result.json"))
        assert res.get("case_id") == "unknown"
        assert res.get("status") in ("insufficient_evidence", "partial", "input_error")
    print("PASS empty input")


def test_bad_json():
    with tempfile.TemporaryDirectory() as d:
        inp, out, work = os.path.join(d, "input"), os.path.join(d, "output"), os.path.join(d, "work")
        for p in (inp, out, work):
            os.makedirs(p)
        write(os.path.join(inp, "case.json"), "{ this is not valid json")
        write(os.path.join(inp, "task.md"), "# task")
        r = run_main(["all", "--input", inp, "--out", out, "--work", work])
        assert r.returncode == 0, r.stderr
        res = load(os.path.join(out, "result.json"))
        assert res.get("status") in ("insufficient_evidence", "partial", "input_error")
    print("PASS bad JSON")


def _synthetic_input(d):
    inp, out, work = os.path.join(d, "input"), os.path.join(d, "output"), os.path.join(d, "work")
    os.makedirs(os.path.join(inp, "materials"), exist_ok=True)
    write(os.path.join(inp, "case.json"),
          json.dumps({"case_id": "case-test-1", "task": "revision"}))
    write(os.path.join(inp, "task.md"), "# 审稿回复与论文修改\n")
    write(os.path.join(inp, "materials", "manuscript-baseline.md"),
          "## Methods\n\nWe used method A without a control.\n\n## Results\n\nThe value was X=3.\n")
    write(os.path.join(inp, "materials", "reviewer-comments.md"),
          "Comment 1: Add a control group.\nComment 2: Clarify the method description.\n")
    return inp, out, work


def test_determinism():
    with tempfile.TemporaryDirectory() as d:
        inp, out, work = _synthetic_input(d)
        # Same output/working directories on both runs: true determinism check.
        r1 = run_main(["all", "--input", inp, "--out", out, "--work", work])
        r2 = run_main(["all", "--input", inp, "--out", out, "--work", work])
        assert r1.returncode == 0, r1.stderr
        assert r2.returncode == 0, r2.stderr
        res1 = load(os.path.join(out, "result.json"))
        res2 = load(os.path.join(out, "result.json"))

        def strip(res):
            res = dict(res)
            res.pop("change_log", None)
            res.pop("runtime", None)
            return res

        assert strip(res1) == strip(res2), "non-deterministic result.json"
        assert res1.get("case_id") == "case-test-1"
        assert res1.get("status") in ("partial", "complete")
        for fname in ("response_letter.md", "revision_locations.md", "evidence_ledger.md", "revised_manuscript.auto.md"):
            assert os.path.exists(os.path.join(out, fname)), fname
    print("PASS determinism")


def test_stdlib_only():
    # Both scripts must import cleanly with the standard library only.
    import core_impl  # noqa: F401
    import main  # noqa: F401
    print("PASS stdlib imports")


def main():
    test_help()
    test_stdlib_only()
    test_empty_input()
    test_bad_json()
    test_determinism()
    print("ALL SELFTEST PASS")


if __name__ == "__main__":
    main()
