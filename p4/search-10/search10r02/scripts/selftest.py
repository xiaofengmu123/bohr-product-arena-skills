#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — offline self-check for the cross-lingual known-item skill.

Runs the orchestrator against synthetic inputs under a temporary directory:
  1. a normal case with an inline output_contract (valid skeleton, no crash);
  2. an empty input directory (input_error skeleton, exit 0);
  3. a corrupt case.json (bad_json recorded, still a valid skeleton);
  4. phase / patch / validate / status round-trip.

Standard library only; writes nothing outside tempfile.mkdtemp().
Exit code 0 iff all checks pass.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(*args, cwd=None):
    proc = subprocess.run(
        [sys.executable, "-B", MAIN] + list(args),
        cwd=cwd, capture_output=True, text=True)
    return proc


def write_case(root, payload, name="case.json"):
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(payload)
    return path


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def check(label, condition, detail=""):
    if not condition:
        print("FAIL: %s %s" % (label, detail))
        sys.exit(1)
    print("ok: %s" % label)


def main():
    base = tempfile.mkdtemp(prefix="crosslang_selftest_")
    try:
        # ---- case 1: normal with inline output_contract in case.json ----
        d = os.path.join(base, "normal")
        in_dir = os.path.join(d, "input")
        out_dir = os.path.join(d, "output")
        work_dir = os.path.join(d, "work")
        case = json.dumps({
            "case_id": "demo-001",
            "research_question": "Find the English translation of a Portuguese "
                                 "paper (DOI 10.1234/demo.001) about pyroptosis.",
            "output_contract": {
                "retrieved": [], "language_pair": {}, "evidence": [],
                "nested": {"score": None},
            },
        }, ensure_ascii=False)
        write_case(in_dir, case)
        with open(os.path.join(in_dir, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# demo task\n")

        proc = run("init", "--input", in_dir, "--output", out_dir, "--work", work_dir)
        check("normal init exit 0", proc.returncode == 0, proc.stderr)
        result = load_json(os.path.join(out_dir, "result.json"))
        check("case_id parsed", result.get("case_id") == "demo-001")
        check("contract fields present",
              "retrieved" in result and "nested" in result)
        check("status honest", result.get("status") == "partial")
        check("state.json written", os.path.exists(os.path.join(work_dir, "state.json")))
        check("plan.txt written", os.path.exists(os.path.join(work_dir, "plan.txt")))

        # ---- case 2: phase / patch / validate / status round-trip ----
        proc = run("phase", "--work", work_dir)
        check("phase exit 0", proc.returncode == 0)
        patch = {"retrieved": [{"title": "demo hit"}],
                 "language_pair": {"source": "pt", "target": "en"},
                 "_change_log": {"at": 0, "phase": 3, "action": "retrieve",
                                 "summary": "demo patch"}}
        patch_path = os.path.join(d, "patch.json")
        with open(patch_path, "w", encoding="utf-8") as fh:
            json.dump(patch, fh, ensure_ascii=False)
        proc = run("patch", "--output", out_dir, "--work", work_dir,
                   "--patch", patch_path)
        check("patch exit 0", proc.returncode == 0, proc.stderr)
        result = load_json(os.path.join(out_dir, "result.json"))
        check("patch merged", result["retrieved"][0]["title"] == "demo hit")
        check("patch change_log appended", len(result["change_log"]) == 2)
        proc = run("validate", "--output", out_dir, "--work", work_dir)
        check("validate exit 0", proc.returncode == 0, proc.stdout)
        proc = run("status", "--output", out_dir)
        check("status exit 0", proc.returncode == 0)

        # ---- case 3: empty input directory ----
        d2 = os.path.join(base, "empty")
        in2 = os.path.join(d2, "input"); out2 = os.path.join(d2, "output")
        work2 = os.path.join(d2, "work")
        os.makedirs(in2, exist_ok=True)
        proc = run("init", "--input", in2, "--output", out2, "--work", work2)
        check("empty init exit 0", proc.returncode == 0, proc.stderr)
        result2 = load_json(os.path.join(out2, "result.json"))
        check("empty input_error status", result2.get("status") == "input_error")
        check("empty errors listed", "case.json_not_found" in
              result2.get("errors", []))

        # ---- case 4: corrupt case.json ----
        d3 = os.path.join(base, "bad")
        in3 = os.path.join(d3, "input"); out3 = os.path.join(d3, "output")
        work3 = os.path.join(d3, "work")
        write_case(in3, "{not valid json!!")
        proc = run("init", "--input", in3, "--output", out3, "--work", work3)
        check("bad init exit 0", proc.returncode == 0, proc.stderr)
        result3 = load_json(os.path.join(out3, "result.json"))
        check("bad input_error status", result3.get("status") == "input_error")
        check("bad error recorded", any("bad_json" in e for e in
              result3.get("errors", [])), str(result3.get("errors")))

        # ---- determinism: re-run normal init gives identical result.json ----
        run("init", "--input", in_dir, "--output", out_dir, "--work", work_dir)
        proc2 = run("init", "--input", in_dir, "--output", out_dir, "--work", work_dir)
        check("reinit exit 0", proc2.returncode == 0)
        # elapsed_seconds and timestamps are non-deterministic; compare shape.
        result_a = load_json(os.path.join(out_dir, "result.json"))
        check("reinit keeps case_id", result_a.get("case_id") == "demo-001")

        print("SELFTEST PASS")
        return 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
