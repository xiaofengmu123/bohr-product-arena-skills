#!/usr/bin/env python3
"""selftest.py — package self-test for sci-analysis-checkpoint.

Exercises the four required robustness scenarios and verifies the invariant
"exit code 0 + a legal result.json is always produced":
  1. empty input directory   -> status insufficient_evidence
  2. bad JSON case.json      -> status input_error + errors recorded
  3. bad-encoding case.json  -> tolerant decode, contract still discovered
  4. missing/unknown args    -> exit 0 (defaults used, unknown flags ignored)

Run from the package root:
    python3 -B scripts/selftest.py
Exit code 0 means all scenarios passed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

MAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")


def run(*argv):
    return subprocess.run(
        [sys.executable, "-B", MAIN, *argv],
        capture_output=True,
        text=True,
        timeout=180,
    )


def check(name, cond, detail=""):
    global results  # noqa: PLW0603
    results.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL") + "  " + name + ("  " + detail if detail else ""))


results = []

tmp = tempfile.mkdtemp(prefix="sci_selftest_")
try:
    # 1. empty input
    d = os.path.join(tmp, "empty")
    os.makedirs(d)
    r = run("init", "--input", d, "--output", os.path.join(d, "out"), "--work", os.path.join(d, "work"))
    rp = os.path.join(d, "out", "result.json")
    ok = r.returncode == 0 and os.path.exists(rp)
    if ok:
        rr = json.load(open(rp, encoding="utf-8"))
        ok = rr.get("status") == "insufficient_evidence" and "case_id" in rr
    check("empty_input_exits0_legal_result", ok, f"rc={r.returncode}")

    # 2. bad JSON
    d = os.path.join(tmp, "badjson")
    os.makedirs(d)
    with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{this is not valid json")
    with open(os.path.join(d, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# task\n")
    r = run("init", "--input", d, "--output", os.path.join(d, "out"), "--work", os.path.join(d, "work"))
    rp = os.path.join(d, "out", "result.json")
    ok = r.returncode == 0 and os.path.exists(rp)
    if ok:
        rr = json.load(open(rp, encoding="utf-8"))
        ok = rr.get("status") == "input_error" and bool(rr.get("errors"))
    check("bad_json_exits0_legal_result", ok, f"rc={r.returncode}")

    # 3. bad encoding (invalid UTF-8 bytes inside a JSON string value)
    d = os.path.join(tmp, "badenc")
    os.makedirs(d)
    raw = b'{"case_id": "c\xff\xfe", "output_contract": {"clean_table": {"type": "string"}, "stats": {"type": "object"}}}'
    with open(os.path.join(d, "case.json"), "wb") as fh:
        fh.write(raw)
    with open(os.path.join(d, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# task\n")
    r = run("init", "--input", d, "--output", os.path.join(d, "out"), "--work", os.path.join(d, "work"))
    rp = os.path.join(d, "out", "result.json")
    ok = r.returncode == 0 and os.path.exists(rp)
    if ok:
        rr = json.load(open(rp, encoding="utf-8"))
        ok = rr.get("status") == "partial" and "clean_table" in rr and "stats" in rr
    check("bad_encoding_exits0_legal_result", ok, f"rc={r.returncode}")

    # 4. missing / unknown args
    r_help = run("--help")
    d = os.path.join(tmp, "args")
    os.makedirs(d)
    r_unk = run(
        "init", "--bogus-flag", "1",
        "--input", d, "--output", os.path.join(d, "out"), "--work", os.path.join(d, "work"),
    )
    check(
        "missing_unknown_args_exit0",
        r_help.returncode == 0 and r_unk.returncode == 0,
        f"help_rc={r_help.returncode} unknown_rc={r_unk.returncode}",
    )

    # 5. checkpoint + validate round trip on a small synthetic profile
    d = os.path.join(tmp, "roundtrip")
    os.makedirs(d)
    with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "case-x", "output_contract": {"clean_table": {"type": "string"}}}, fh)
    with open(os.path.join(d, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# task\n")
    with open(os.path.join(d, "data.csv"), "w", encoding="utf-8") as fh:
        fh.write("x,y\n1,2\n2,4\n3,\n")
    run("init", "--input", d, "--output", os.path.join(d, "out"), "--work", os.path.join(d, "work"))
    run("profile", "--input", d, "--output", os.path.join(d, "out"))
    run("checkpoint", "--phase", "P1", "--note", "roundtrip", "--work", os.path.join(d, "work"), "--output", os.path.join(d, "out"))
    run("set", "--field", "clean_table", "--value", "clean.csv", "--output", os.path.join(d, "out"), "--work", os.path.join(d, "work"))
    run("evidence", "--locator", "data.csv#L2", "--quote", "1,2", "--role", "test", "--output", os.path.join(d, "out"), "--work", os.path.join(d, "work"))
    r_val = run("validate", "--input", d, "--output", os.path.join(d, "out"))
    rr = json.load(open(os.path.join(d, "out", "result.json"), encoding="utf-8"))
    ok = (
        r_val.returncode == 0
        and rr.get("clean_table") == "clean.csv"
        and len(rr.get("sources", [])) == 1
        and len(rr.get("change_log", [])) >= 2
    )
    check("checkpoint_set_evidence_validate_roundtrip", ok, f"rc={r_val.returncode}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

fails = [x for x in results if not x[1]]
print(f"selftest: {len(results) - len(fails)}/{len(results)} passed")
sys.exit(0 if not fails else 1)
