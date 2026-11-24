#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py -- offline self-test for the cross-lingual known-item search skill.

Runs the whole pipeline on synthetic temporary inputs (never writes real case
outputs) and asserts the contract/robustness guarantees. Standard library only.

Usage:
    python3 -B scripts/selftest.py
Exit code 0 = all checks passed; 1 = at least one check failed.
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl as impl  # noqa: E402
import main as main_mod   # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS  %s" % name)
    else:
        FAIL += 1
        print("FAIL  %s %s" % (name, detail))


def write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def run_init(inp, out, work):
    return main_mod.main(["init", "--input", inp, "--output", out, "--work", work])


def test_contract_alignment(tmp):
    inp = os.path.join(tmp, "in_contract")
    out = os.path.join(tmp, "out_contract")
    work = os.path.join(tmp, "work_contract")
    os.makedirs(inp)
    contract = {
        "output_contract": {
            "research_question": {},
            "terminology_table": [],
            "queries": [],
            "results": [],
            "evidence_map": [],
            "dedup": [],
            "missing": [],
            "conflicts": [],
        },
        "case_id": "case-x1",
    }
    write(os.path.join(inp, "case.json"), json.dumps(contract, ensure_ascii=False))

    rc = run_init(inp, out, work)
    check("init with contract exit 0", rc == 0, "rc=%d" % rc)

    obj, _ = impl.load_result(out)
    check("case_id parsed", obj.get("case_id") == "case-x1")
    check("status partial", obj.get("status") == "partial")
    for f in contract["output_contract"]:
        check("contract field present: %s" % f, f in obj)
    check("change_log non-empty", isinstance(obj.get("change_log"), list) and obj["change_log"])
    check("contract_schema aligned", obj.get("contract_schema", {}).get("aligned") is True)
    for key in ("sources", "uncertainty", "pending_human", "runtime", "errors", "degradations"):
        check("standard field present: %s" % key, key in obj)
    check("deterministic skeleton fields", obj["change_log"][0]["fields"] == list(obj.keys()))

    issues = impl.validate_result(obj, contract["output_contract"], True)
    check("validate clean", issues == [], str(issues))
    rc = main_mod.main(["validate", "--input", inp, "--output", out, "--work", work])
    check("validate CLI exit 0", rc == 0, "rc=%d" % rc)


def test_patch_and_changelog(tmp):
    inp = os.path.join(tmp, "in_patch")
    out = os.path.join(tmp, "out_patch")
    work = os.path.join(tmp, "work_patch")
    os.makedirs(inp)
    write(os.path.join(inp, "case.json"),
          json.dumps({"output_contract": {"results": [], "missing": []}}))
    run_init(inp, out, work)

    patch = os.path.join(tmp, "patch.json")
    write(patch, json.dumps({
        "status": "ok",
        "results": [{"rank": 1, "title": "example", "locator": "materials/x#p1",
                     "quote": "verbatim"}],
        "_change": "step3 retrieve",
    }))
    rc = main_mod.main(["patch", "--output", out, "--work", work, "--patch", patch])
    check("patch CLI exit 0", rc == 0, "rc=%d" % rc)

    obj, _ = impl.load_result(out)
    check("status updated to ok", obj.get("status") == "ok")
    check("results merged", len(obj.get("results", [])) == 1)
    check("changelog has 2 entries", len(obj.get("change_log", [])) == 2)
    check("changelog note kept", obj["change_log"][-1].get("note") == "step3 retrieve")


def test_empty_input(tmp):
    inp = os.path.join(tmp, "in_empty")
    out = os.path.join(tmp, "out_empty")
    work = os.path.join(tmp, "work_empty")
    os.makedirs(inp)
    rc = run_init(inp, out, work)
    check("empty input exit 0", rc == 0, "rc=%d" % rc)
    obj, _ = impl.load_result(out)
    check("empty input status input_error", obj.get("status") == "input_error")
    check("empty input result exists", obj is not None)
    check("empty input no contract -> semantic fields",
          "research_question" in obj and "results" in obj)


def test_bad_json(tmp):
    inp = os.path.join(tmp, "in_bad")
    out = os.path.join(tmp, "out_bad")
    work = os.path.join(tmp, "work_bad")
    os.makedirs(inp)
    write(os.path.join(inp, "case.json"), "{not valid json")
    write(os.path.join(inp, "task.md"), "task text with case_id: case-b1")
    rc = run_init(inp, out, work)
    check("bad json exit 0", rc == 0, "rc=%d" % rc)
    obj, _ = impl.load_result(out)
    # Recoverable (case_id still found in task.md) => degrade but continue as partial.
    check("bad json recoverable -> partial", obj.get("status") == "partial")
    check("bad json recorded in errors", any("bad_json" in str(e) for e in obj.get("errors", [])))
    check("bad json recorded in degradations",
          any("input_degraded" in str(e) for e in obj.get("degradations", [])))
    check("case id from task.md", obj.get("case_id") == "case-b1")

    # Unrecoverable bad json (no task.md, no case_id) => input_error.
    inp2 = os.path.join(tmp, "in_bad2")
    out2 = os.path.join(tmp, "out_bad2")
    work2 = os.path.join(tmp, "work_bad2")
    os.makedirs(inp2)
    write(os.path.join(inp2, "case.json"), "{broken")
    rc = run_init(inp2, out2, work2)
    check("bad json unrecoverable exit 0", rc == 0, "rc=%d" % rc)
    obj2, _ = impl.load_result(out2)
    check("bad json unrecoverable -> input_error", obj2.get("status") == "input_error")


def test_inline_contract_from_task(tmp):
    inp = os.path.join(tmp, "in_inline")
    out = os.path.join(tmp, "out_inline")
    work = os.path.join(tmp, "work_inline")
    os.makedirs(inp)
    write(os.path.join(inp, "task.md"),
          'output_contract {"results": [], "dedup": []} case_id: case-c2')
    write(os.path.join(inp, "case.json"), json.dumps({"task": "x"}))
    rc = run_init(inp, out, work)
    check("inline contract exit 0", rc == 0, "rc=%d" % rc)
    obj, _ = impl.load_result(out)
    check("inline contract fields backfilled", "results" in obj and "dedup" in obj)
    check("inline contract present flag", obj.get("contract_schema", {}).get("present") is True)


def test_contract_field_types(tmp):
    # A list contract (field-name list) must also be backfilled.
    inp = os.path.join(tmp, "in_list")
    out = os.path.join(tmp, "out_list")
    work = os.path.join(tmp, "work_list")
    os.makedirs(inp)
    write(os.path.join(inp, "case.json"),
          json.dumps({"output_contract": ["results", "sources", "notes"]}))
    run_init(inp, out, work)
    obj, _ = impl.load_result(out)
    for f in ("results", "sources", "notes"):
        check("list contract field %s present" % f, f in obj)


def test_help():
    try:
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main_mod.main(["--help"])
        check("--help exit 0", rc == 0, "rc=%d" % rc)
    except SystemExit as exc:
        check("--help exit 0 (SystemExit)", exc.code == 0, "code=%r" % exc.code)


def main():
    tmp = tempfile.mkdtemp(prefix="clls_selftest_")
    try:
        test_help()
        test_contract_alignment(tmp)
        test_patch_and_changelog(tmp)
        test_empty_input(tmp)
        test_bad_json(tmp)
        test_inline_contract_from_task(tmp)
        test_contract_field_types(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 50)
    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
