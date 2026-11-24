#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for the skill scripts.

Runs skeleton.py / evidence.py / finalize.py against synthetic inputs inside a
fresh temporary directory and checks the contract holds.  No file is written
under outputs/ or /app; temp dirs are removed on completion.  Uses only the
Python standard library.

    python3 -B scripts/selftest.py
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKEL = os.path.join(HERE, "skeleton.py")
EVID = os.path.join(HERE, "evidence.py")
FIN = os.path.join(HERE, "finalize.py")


def run(args, **kw):
    return subprocess.run([sys.executable, "-B"] + args,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, **kw)


def fail(msg):
    print("FAIL  %s" % msg)
    return False


def ok(msg):
    print("OK    %s" % msg)
    return True


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def test_help():
    good = True
    for script in (SKEL, EVID, FIN):
        r = run([script, "--help"])
        if r.returncode != 0:
            good = fail("%s --help rc=%s" % (os.path.basename(script),
                                             r.returncode))
        else:
            ok("%s --help" % os.path.basename(script))
    return good


def test_empty_input():
    with tempfile.TemporaryDirectory() as tmp:
        inp = os.path.join(tmp, "in_empty")
        os.makedirs(inp)
        outp = os.path.join(tmp, "out")
        work = os.path.join(tmp, "work")
        r = run([SKEL, "--input", inp, "--output", outp, "--work", work])
        if r.returncode != 0:
            return fail("empty-input skeleton rc=%s" % r.returncode)
        res = read_json(os.path.join(outp, "result.json"))
        if res.get("status") != "input_error":
            return fail("empty-input status=%s, want input_error" % res.get("status"))
        return ok("empty input -> input_error (rc 0)")


def test_bad_json():
    with tempfile.TemporaryDirectory() as tmp:
        inp = os.path.join(tmp, "in_bad")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ this is not json")
        outp = os.path.join(tmp, "out")
        work = os.path.join(tmp, "work")
        run([SKEL, "--input", inp, "--output", outp, "--work", work])
        res = read_json(os.path.join(outp, "result.json"))
        if res.get("status") != "partial":
            return fail("bad-json status=%s, want partial" % res.get("status"))
        if not any("case.json" in str(e) for e in res.get("errors", [])):
            return fail("bad-json error not recorded")
        return ok("bad case.json -> partial + error (rc 0)")


def test_full_flow():
    with tempfile.TemporaryDirectory() as tmp:
        inp = os.path.join(tmp, "in_full")
        matdir = os.path.join(inp, "materials")
        os.makedirs(matdir)
        outp = os.path.join(tmp, "out")
        work = os.path.join(tmp, "work")

        case = {
            "case_id": "case-demo-001",
            "output_contract": {
                "research_product": "selection_review",
                "evidence_matrix": None,
                "boundary": None,
            },
            "materials": ["materials/paper_a.md"],
        }
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(case, fh)
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# task\n\nevaluate the question.\n")
        paper = ("# Paper A\n\nWe propose method X on dataset Y.\n"
                 "Baseline Z achieves 0.80; X achieves 0.85.\n"
                 "Limitations: small sample.\n")
        with open(os.path.join(matdir, "paper_a.md"), "w", encoding="utf-8") as fh:
            fh.write(paper)

        r = run([SKEL, "--input", inp, "--output", outp, "--work", work])
        if r.returncode != 0:
            return fail("full-flow skeleton rc=%s" % r.returncode)
        r = run([EVID, "--input", inp, "--work", work, "--output", outp])
        if r.returncode != 0:
            return fail("full-flow evidence rc=%s" % r.returncode)

        # Resolve the real source_id for the paper from the inventory.
        inv_path = os.path.join(work, "material_inventory.json")
        inv = read_json(inv_path)
        paper_sid = None
        for m in inv.get("materials", []):
            if m.get("path") == "materials/paper_a.md":
                paper_sid = m.get("source_id")
        if not paper_sid:
            return fail("could not resolve source_id for paper_a.md")

        # Simulate an agent-written fragment (synthetic test data only).
        fragdir = os.path.join(work, "fragments")
        os.makedirs(fragdir, exist_ok=True)
        fragment = {
            "research_question": {
                "status": "complete",
                "testable_statement": "On dataset Y, method X improves over Z.",
                "object": "method X",
                "variables": ["accuracy"],
                "baseline": "method Z",
                "expected_outcome": "X > Z",
                "falsification_condition": "X not > Z",
                "gaps": [],
                "claims": ["rq-1"],
            },
            "evidence_map": {"claims": [
                {
                    "claim_id": "rq-1",
                    "field": "research_question",
                    "claim": "Paper A reports X (0.85) over Z (0.80) on Y.",
                    "kind": "fact",
                    "verdict": "established",
                    "confidence": "high",
                    "evidence": [{
                        "source_id": paper_sid,
                        "locator": "materials/paper_a.md#L3",
                        "quote": "Baseline Z achieves 0.80; X achieves 0.85.",
                        "relation": "supports",
                        "quote_status": "verified",
                    }],
                    "rationale": "verbatim from source line 3",
                },
            ]},
        }
        with open(os.path.join(fragdir, "01_question.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(fragment, fh)

        r = run([FIN, "--input", inp, "--output", outp, "--work", work])
        if r.returncode != 0:
            return fail("full-flow finalize rc=%s (stderr=%s)"
                        % (r.returncode, r.stderr[:200]))
        res = read_json(os.path.join(outp, "result.json"))
        for key in ("case_id", "status", "output_contract", "research_question",
                    "evidence_map", "conflicts", "synthesis", "sources",
                    "uncertainty", "change_log", "pending_human", "errors",
                    "degradations", "runtime"):
            if key not in res:
                return fail("full-flow result.json missing %s" % key)
        if res.get("case_id") != "case-demo-001":
            return fail("case_id mismatch")
        # output_contract keys echoed to top level
        for ckey in ("research_product", "evidence_matrix", "boundary"):
            if ckey not in res:
                return fail("contract key not lifted to top level: %s" % ckey)
        claims = res["evidence_map"]["claims"]
        if not claims or claims[0]["claim_id"] != "rq-1":
            return fail("evidence_map.claims missing rq-1")
        if not os.path.isfile(os.path.join(outp, "report.md")):
            return fail("report.md missing")

        # Determinism light-check: second finalize run must not crash.
        r2 = run([FIN, "--input", inp, "--output", outp, "--work", work])
        if r2.returncode != 0:
            return fail("second finalize rc=%s" % r2.returncode)
        return ok("full flow -> merged result + report.md + contract echo")


def main():
    print("selftest: %s" % HERE)
    results = [
        test_help(),
        test_empty_input(),
        test_bad_json(),
        test_full_flow(),
    ]
    print("-" * 40)
    passed = sum(1 for x in results if x)
    print("selftest: %d/%d passed" % (passed, len(results)))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
