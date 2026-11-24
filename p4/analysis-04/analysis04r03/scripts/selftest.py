#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for the Skill package (stdlib only, no /input or /app needed).

Run from the package root:

    python3 -B scripts/selftest.py

It builds a synthetic demo case in a temporary directory, runs the real
``main.py`` pipeline end-to-end, and asserts:

  1. ``--help`` exits 0.
  2. the pipeline produces a legal ``result.json`` with the required keys.
  3. all referenced artifacts exist and the SVG parses.
  4. determinism: wiping the output and re-running yields a byte-identical
     ``result.json`` (the non-deterministic wall-clock trace lives in a
     separate ``runtime.json``).
  5. four stability scenarios each exit 0: empty input, bad JSON, bad
     encoding, and missing input directory.

Exit code 0 = all passed, 1 = at least one failure.
"""

import os
import sys
import json
import subprocess
import tempfile
import shutil

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import core_impl as core  # noqa: E402

MAIN_PY = os.path.join(SCRIPT_DIR, "main.py")
REQUIRED_KEYS = ("case_id", "status", "sources", "uncertainty", "pending_human", "change_log")
VALID_STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}

PASS = []
FAIL = []


def record(name, condition, detail=""):
    if condition:
        PASS.append(name)
        print("PASS  %s" % name)
    else:
        FAIL.append(name)
        print("FAIL  %s  %s" % (name, detail))


def run_main(argv):
    return subprocess.run([sys.executable, "-B", MAIN_PY] + argv,
                          capture_output=True, text=True)


def read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def test_help():
    proc = run_main(["--help"])
    record("help_exit_zero", proc.returncode == 0, "rc=%d" % proc.returncode)


def test_demo_pipeline_and_determinism():
    root = tempfile.mkdtemp(prefix="skill-selftest-")
    try:
        case_root = os.path.join(root, "input")
        core.make_demo_case(case_root)
        out = os.path.join(root, "output")
        work = os.path.join(root, "work")

        proc = run_main(["--input", case_root, "--output", out, "--work", work, "--phase", "all"])
        record("pipeline_exit_zero", proc.returncode == 0,
               "rc=%d stderr=%s" % (proc.returncode, proc.stderr[:200]))
        result_path = os.path.join(out, "result.json")
        record("result_json_exists", os.path.isfile(result_path))
        result = read_json(result_path)
        for key in REQUIRED_KEYS:
            record("result_has_%s" % key, key in result)
        record("case_id_correct", result.get("case_id") == "selftest-demo-001",
               str(result.get("case_id")))
        record("status_valid", result.get("status") in VALID_STATUSES, str(result.get("status")))
        for name, path in (result.get("_artifacts") or {}).items():
            if isinstance(path, str) and path:
                record("artifact_exists_%s" % name, os.path.isfile(path), path)
        svg = (result.get("_artifacts") or {}).get("figure_svg")
        if svg and os.path.isfile(svg):
            try:
                core.ET.parse(svg)
                record("svg_parses", True)
            except Exception as exc:  # noqa: BLE001
                record("svg_parses", False, str(exc))
        png = (result.get("_artifacts") or {}).get("figure_png")
        if png and os.path.isfile(png):
            head = read_bytes(png)[:8]
            record("png_signature", head == b"\x89PNG\r\n\x1a\n", str(head))

        # Determinism: wipe output, re-run into the same directory, compare bytes.
        first = read_bytes(result_path)
        shutil.rmtree(out)
        proc2 = run_main(["--input", case_root, "--output", out, "--work", work, "--phase", "all"])
        record("rerun_exit_zero", proc2.returncode == 0, "rc=%d" % proc2.returncode)
        second = read_bytes(result_path)
        record("result_byte_identical", first == second,
               "len1=%d len2=%d" % (len(first), len(second)))
        record("runtime_json_separate", os.path.isfile(os.path.join(out, "runtime.json")))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _stability_case(case_root):
    """Build a single-file synthetic input under case_root and return argv."""
    os.makedirs(case_root, exist_ok=True)
    with open(os.path.join(case_root, "case.json"), "w", encoding="utf-8") as handle:
        handle.write('{"case_id": "stability-001", "output_contract": {"summary": null}}\n')
    with open(os.path.join(case_root, "task.md"), "w", encoding="utf-8") as handle:
        handle.write("# stability task\n")
    with open(os.path.join(case_root, "data.csv"), "w", encoding="utf-8") as handle:
        handle.write("a,b\n1,2\n3,\n")


def test_stability_scenarios():
    root = tempfile.mkdtemp(prefix="skill-stability-")
    try:
        # 1. empty input directory
        empty = os.path.join(root, "empty")
        os.makedirs(empty)
        out1 = os.path.join(root, "out1")
        p = run_main(["--input", empty, "--output", out1, "--work", os.path.join(root, "w1")])
        record("empty_input_exit_zero", p.returncode == 0, "rc=%d" % p.returncode)
        record("empty_input_status", os.path.isfile(os.path.join(out1, "result.json")))
        if os.path.isfile(os.path.join(out1, "result.json")):
            st = read_json(os.path.join(out1, "result.json")).get("status")
            record("empty_input_insufficient", st == "insufficient_evidence", str(st))

        # 2. bad JSON in case.json
        bad = os.path.join(root, "badjson")
        os.makedirs(bad)
        with open(os.path.join(bad, "case.json"), "w", encoding="utf-8") as handle:
            handle.write("{ this is not json ")
        out2 = os.path.join(root, "out2")
        p = run_main(["--input", bad, "--output", out2, "--work", os.path.join(root, "w2")])
        record("bad_json_exit_zero", p.returncode == 0, "rc=%d" % p.returncode)
        if os.path.isfile(os.path.join(out2, "result.json")):
            st = read_json(os.path.join(out2, "result.json")).get("status")
            record("bad_json_status_input_error", st == "input_error", str(st))

        # 3. bad encoding in a data file (invalid UTF-8, valid latin-1)
        enc = os.path.join(root, "badenc")
        os.makedirs(enc)
        with open(os.path.join(enc, "case.json"), "w", encoding="utf-8") as handle:
            handle.write('{"case_id": "enc-001"}\n')
        with open(os.path.join(enc, "weird.csv"), "wb") as handle:
            handle.write(b"a,b\n1,\xe9\n")  # \xe9 is invalid UTF-8, valid latin-1
        out3 = os.path.join(root, "out3")
        p = run_main(["--input", enc, "--output", out3, "--work", os.path.join(root, "w3")])
        record("bad_encoding_exit_zero", p.returncode == 0, "rc=%d" % p.returncode)

        # 4. missing input directory
        out4 = os.path.join(root, "out4")
        p = run_main(["--input", os.path.join(root, "does_not_exist"), "--output", out4,
                      "--work", os.path.join(root, "w4")])
        record("missing_input_exit_zero", p.returncode == 0, "rc=%d" % p.returncode)
        record("missing_input_result_exists", os.path.isfile(os.path.join(out4, "result.json")))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    test_help()
    test_demo_pipeline_and_determinism()
    test_stability_scenarios()
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
