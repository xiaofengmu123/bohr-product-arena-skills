#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — offline self-test for the deep-research skill package.

Run with:  python3 -B scripts/selftest.py

Builds isolated inputs in a temporary directory and exercises the runtime
helpers end to end. It never reads the user's real cases and never fabricates
benchmark scores. Exit code 0 means all hard checks passed; 1 means a
regression was found.
"""

import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as C  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("%-28s %s  %s" % (name, "PASS" if ok else "FAIL", detail))


def run_cli(args, cwd=None):
    proc = subprocess.run(
        [sys.executable, "-B", MAIN] + args,
        cwd=cwd or HERE, capture_output=True, text=True, timeout=120)
    return proc


def write_file(path, content, mode="w", encoding="utf-8"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode, encoding=encoding) as fh:
        fh.write(content)


# ---------------------------------------------------------------------------
def test_empty_input(tmp):
    in_dir = os.path.join(tmp, "empty")
    os.makedirs(in_dir)
    out_dir = os.path.join(tmp, "empty-out")
    work_dir = os.path.join(tmp, "empty-work")
    proc = run_cli(["skeleton", "--input", in_dir, "--output", out_dir,
                    "--work", work_dir])
    ok = proc.returncode == 0
    result, errs, _ = C.load_json(os.path.join(out_dir, "result.json"))
    ok = ok and isinstance(result, dict)
    ok = ok and result.get("status") == C.STATUS_INSUFFICIENT
    ok = ok and bool(result.get("limitations"))
    record("empty input -> insufficient_evidence", ok,
           "status=%s" % (result.get("status") if isinstance(result, dict) else None))


def test_bad_json(tmp):
    in_dir = os.path.join(tmp, "badjson")
    write_file(os.path.join(in_dir, "case.json"), "{ this is not json")
    write_file(os.path.join(in_dir, "task.md"), "# task\n")
    out_dir = os.path.join(tmp, "badjson-out")
    work_dir = os.path.join(tmp, "badjson-work")
    proc = run_cli(["skeleton", "--input", in_dir, "--output", out_dir,
                    "--work", work_dir])
    result, errs, _ = C.load_json(os.path.join(out_dir, "result.json"))
    ok = proc.returncode == 0 and isinstance(result, dict)
    ok = ok and result.get("status") == C.STATUS_INPUT_ERROR
    ok = ok and bool(result.get("errors") or result.get("degradations"))
    record("bad case.json -> input_error", ok,
           "status=%s" % (result.get("status") if isinstance(result, dict) else None))


def test_bad_encoding(tmp):
    in_dir = os.path.join(tmp, "badenc")
    write_file(os.path.join(in_dir, "case.json"),
               json.dumps({"id": "case-badenc", "task": "encoding case"},
                          ensure_ascii=False))
    write_file(os.path.join(in_dir, "task.md"), "# task\n", encoding="utf-8")
    raw = b"header\xff\xfe\xfa\x9c data line"
    with open(os.path.join(in_dir, "materials-note.txt"), "wb") as fh:
        fh.write(raw)
    inv = C.discover_input(in_dir)
    mat = inv["materials"]
    text, enc, degs = ("", "", [])
    if mat:
        text, enc, degs = C.read_text(mat[0]["path"])
    record("bad encoding -> read with degradation", bool(text) and bool(degs),
           "enc=%s degradations=%d" % (enc, len(degs)))


def test_contract_roundtrip(tmp):
    in_dir = os.path.join(tmp, "roundtrip")
    contract = {
        "required_answer": None,
        "evidence_table": [],
        "custom_score": 0,
        "nested": {"alpha": None, "beta": []},
    }
    case = {"armiId": "CASE-ROUNDTRIP-1",
            "output_contract": contract,
            "task": "test"}
    write_file(os.path.join(in_dir, "case.json"),
               json.dumps(case, ensure_ascii=False))
    write_file(os.path.join(in_dir, "task.md"), "# task\n")
    write_file(os.path.join(in_dir, "materials", "a.txt"),
               "The trial reported a 12% reduction in relapses (p=0.03).\n",
               encoding="utf-8")
    out_dir = os.path.join(tmp, "roundtrip-out")
    work_dir = os.path.join(tmp, "roundtrip-work")

    proc = run_cli(["skeleton", "--input", in_dir, "--output", out_dir,
                    "--work", work_dir])
    result, errs, _ = C.load_json(os.path.join(out_dir, "result.json"))
    contract_ok = all(k in result for k in contract)
    contract_ok = contract_ok and result.get("case_id") == "CASE-ROUNDTRIP-1"
    contract_ok = contract_ok and isinstance(result.get("nested"), dict)
    contract_ok = contract_ok and "beta" in (result.get("nested") or {})

    payload = {
        "status": C.STATUS_OK,
        "research_product": {
            "type": "synthesis",
            "question": "Effect of the trial?",
            "summary": "The trial reported a reduction in relapses.",
            "conclusions": [{
                "id": "C1",
                "text": "The trial reported a 12% reduction in relapses.",
                "evidence_ids": ["E1"],
                "strength": "established",
            }],
        },
        "evidence_matrix": [{
            "id": "E1", "subquestion": "SQ1",
            "source": "materials/a.txt", "locator": "line 1",
            "quote": "12% reduction in relapses",
            "claim": "12% reduction reported",
            "judgment": "supports",
            "reason": "verbatim in source",
            "conclusion_ids": ["C1"],
            "claim_type": "fact",
            "verification": "verbatim",
        }],
        "uncertainty": {
            "conflicts": [], "biases": [],
            "gaps": [{"id": "G1", "question": "long-term safety",
                      "kind": "not_searched",
                      "search_evidence": {"searched": False}}],
            "undetermined": [],
        },
        "sources": [{"id": "S1", "path": "materials/a.txt", "role": "primary"}],
        "limitations": [],
    }
    payload_path = os.path.join(work_dir, "payload.json")
    C.write_json(payload_path, payload)
    run_cli(["merge", "--input", in_dir, "--output", out_dir, "--work", work_dir,
             "--payload", payload_path])
    run_cli(["finalize", "--input", in_dir, "--output", out_dir, "--work", work_dir])

    result, errs, _ = C.load_json(os.path.join(out_dir, "result.json"))
    checks = C.selfcheck(result, in_dir, work_dir)
    summary = C.selfcheck_summary(checks)
    ok = contract_ok and summary["ok"]
    ok = ok and len(result.get("evidence_matrix") or []) == 1
    ok = ok and os.path.exists(os.path.join(out_dir, "report.md"))
    ok = ok and os.path.exists(os.path.join(out_dir, "evidence_map.json"))
    record("contract roundtrip + merge + finalize", ok,
           "case_id=%s checks=%d/%d" % (result.get("case_id"),
                                        summary["passed"], summary["total"]))


def test_quote_guard(tmp):
    in_dir = os.path.join(tmp, "quoteguard")
    write_file(os.path.join(in_dir, "case.json"),
               json.dumps({"id": "case-quote"}, ensure_ascii=False))
    write_file(os.path.join(in_dir, "task.md"), "# task\n")
    write_file(os.path.join(in_dir, "materials", "paper.txt"),
               "The median survival was 8.4 months.\n")
    result = {
        "case_id": "case-quote",
        "status": C.STATUS_PARTIAL,
        "research_product": {"type": "", "question": "", "summary": "",
                             "conclusions": []},
        "evidence_matrix": [
            {"id": "E1", "source": "materials/paper.txt", "locator": "line 1",
             "quote": "The median survival was 8.4 months.",
             "claim": "median survival 8.4 months",
             "judgment": "supports", "verification": "verbatim"},
            {"id": "E2", "source": "materials/paper.txt", "locator": "line 1",
             "quote": "The median survival was 99 months.",
             "claim": "fabricated number",
             "judgment": "supports", "verification": "verbatim"},
        ],
        "uncertainty": {"conflicts": [], "biases": [], "gaps": [],
                        "undetermined": []},
        "sources": [], "change_log": [], "limitations": [],
        "errors": [], "degradations": [], "pending_human": [],
        "runtime_seconds": 0,
    }
    vq = C.verify_quotes(result, in_dir)
    record("quote guard catches non-matching quote", vq["failures"] == ["E2"],
           "failures=%s" % vq["failures"])


def test_determinism(tmp):
    in_dir = os.path.join(tmp, "det")
    write_file(os.path.join(in_dir, "case.json"),
               json.dumps({"id": "case-det"}, ensure_ascii=False))
    write_file(os.path.join(in_dir, "task.md"), "# task\n")
    write_file(os.path.join(in_dir, "materials", "b.txt"), "x\n")
    o1, w1 = os.path.join(tmp, "det-o1"), os.path.join(tmp, "det-w1")
    o2, w2 = os.path.join(tmp, "det-o2"), os.path.join(tmp, "det-w2")
    run_cli(["skeleton", "--input", in_dir, "--output", o1, "--work", w1])
    run_cli(["skeleton", "--input", in_dir, "--output", o2, "--work", w2])
    r1, _, _ = C.load_json(os.path.join(o1, "result.json"))
    r2, _, _ = C.load_json(os.path.join(o2, "result.json"))
    # strip timestamps before comparing
    for r in (r1, r2):
        r["change_log"] = [{k: v for k, v in c.items() if k != "ts"}
                           for c in r.get("change_log", [])]
    record("deterministic skeleton output", r1 == r2, "")


def main():
    tmp = tempfile.mkdtemp(prefix="research-skill-selftest-")
    print("selftest workdir:", tmp)
    try:
        test_empty_input(tmp)
        test_bad_json(tmp)
        test_bad_encoding(tmp)
        test_contract_roundtrip(tmp)
        test_quote_guard(tmp)
        test_determinism(tmp)
    finally:
        # Keep the directory for inspection on failure; remove on success.
        if all(ok for _name, ok, _d in RESULTS):
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    hard_fail = [n for n, ok, _d in RESULTS if not ok]
    print("\n%d passed, %d failed" %
          (sum(1 for _n, ok, _d in RESULTS if ok), len(hard_fail)))
    if hard_fail:
        print("FAILED:", ", ".join(hard_fail))
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
