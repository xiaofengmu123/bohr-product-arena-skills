#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py -- end-to-end checks for the cross-lingual skill package.

Runs the real `main.py` CLI in an isolated temporary directory and verifies:

  A. `--help` exits 0;
  B. `init` on a well-formed input writes a legal result.json skeleton and the
     plan.json / evidence.json intermediates, echoing every contract field;
  C. `plan` + `evidence` + `assemble` produce a deterministic, hash-anchored
     result.json whose semantic fields are projected from the intermediates;
  D. an empty input directory degrades to status=input_error (exit 0);
  E. a corrupt case.json is recorded in `errors` without crashing;
  F. `validate` reports no issues on the finished artifact;
  G. repeated assembly is byte-identical modulo the informational `runtime`.

Standard library only; nothing touches /input, /app/output or /app/work.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
PY = [sys.executable, "-B", MAIN]

CASES = {}


def run(*argv, cwd=None):
    proc = subprocess.run(
        PY + list(argv), cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    return proc


def make_case(base):
    """Write a realistic (but obviously synthetic) cross-lingual case input."""
    inp = os.path.join(base, "input")
    os.makedirs(os.path.join(inp, "materials"))
    case = {
        "case_id": "case-pten-0001",
        "task": "已知某葡萄牙语论文与其英文译本，请找回原文、译文、仅摘要版本并去重",
        "output_contract": {
            "research_question": None,
            "terminology_table": [],
            "queries": [],
            "results": [],
            "evidence_map": [],
            "dedup": [],
            "missing": [],
            "conflicts": [],
        },
    }
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 跨语言文献检索\n\ncase_id: case-pten-0001\n\n评分维度见平台。\n")
    with open(os.path.join(inp, "materials", "sample.txt"), "w", encoding="utf-8") as fh:
        fh.write("Exemplo de material em português.\n")
    return inp


def main():
    root = tempfile.mkdtemp(prefix="xlang_selftest_")
    failures = []
    try:
        # A. help
        if run("--help").returncode != 0:
            failures.append("A: --help exit != 0")

        # B/C/F/G. full happy path
        case_dir = os.path.join(root, "happy")
        inp = make_case(case_dir)
        out = os.path.join(case_dir, "output")
        work = os.path.join(case_dir, "work")
        if run("init", "--input", inp, "--output", out, "--work", work).returncode != 0:
            failures.append("B: init exit != 0")

        with open(os.path.join(inp, "plan_fragment.json"), "w", encoding="utf-8") as fh:
            json.dump({"research_question": "找回某葡萄牙语原文及其英文出版译本",
                       "source_language": "pt", "target_languages": ["en"],
                       "terminology_table": [
                           {"term": "piroptose", "language": "pt", "type": "headword",
                            "basis": "materials/sample.txt", "verification": "exact"}]},
                      fh, ensure_ascii=False)
        with open(os.path.join(inp, "evidence_fragment.json"), "w", encoding="utf-8") as fh:
            json.dump({"hits": [
                {"title": "Artigo Exemplo", "language": "pt", "version": "original",
                 "body_language": "pt", "doi": "10.0000/example",
                 "locator": "materials/sample.txt", "quote": "Exemplo de material"}]},
                      fh, ensure_ascii=False)

        if run("plan", "--work", work, "--output", out, "--plan",
               os.path.join(inp, "plan_fragment.json")).returncode != 0:
            failures.append("C: plan exit != 0")
        if run("evidence", "--work", work, "--output", out, "--evidence",
               os.path.join(inp, "evidence_fragment.json")).returncode != 0:
            failures.append("C: evidence exit != 0")
        if run("assemble", "--output", out, "--work", work).returncode != 0:
            failures.append("C: assemble exit != 0")

        with open(os.path.join(out, "result.json"), encoding="utf-8") as fh:
            result = json.load(fh)
        if result.get("case_id") != "case-pten-0001":
            failures.append("B: wrong case_id")
        for field in ("research_question", "terminology_table", "queries",
                      "results", "evidence_map", "dedup", "missing", "conflicts"):
            if field not in result:
                failures.append("B: contract field missing in result: %s" % field)
        if not result.get("intermediate", {}).get("evidence_sha256"):
            failures.append("B: missing intermediate evidence_sha256")
        if result.get("results") and not result["results"][0].get("quote"):
            failures.append("C: hit missing verbatim quote")

        # G. determinism: assemble twice and compare (ignore runtime field).
        run("assemble", "--output", out, "--work", work)
        with open(os.path.join(out, "result.json"), encoding="utf-8") as fh:
            result2 = json.load(fh)
        a = {k: v for k, v in result.items() if k != "runtime"}
        b = {k: v for k, v in result2.items() if k != "runtime"}
        if a != b:
            failures.append("G: non-deterministic assembly")

        # F. validate reports no issues.
        proc = run("validate", "--output", out, "--work", work)
        if "no issues" not in proc.stdout:
            failures.append("F: validate did not pass")

        # D. empty input degrades to input_error.
        empty = os.path.join(root, "empty")
        os.makedirs(os.path.join(empty, "input"))
        eout = os.path.join(empty, "output")
        ework = os.path.join(empty, "work")
        if run("init", "--input", os.path.join(empty, "input"),
               "--output", eout, "--work", ework).returncode != 0:
            failures.append("D: empty init exit != 0")
        with open(os.path.join(eout, "result.json"), encoding="utf-8") as fh:
            empty_result = json.load(fh)
        if empty_result.get("status") != "input_error":
            failures.append("D: empty input did not yield input_error")

        # E. corrupt case.json is recorded, not fatal.
        corrupt = os.path.join(root, "corrupt")
        cinp = make_case(corrupt)
        with open(os.path.join(cinp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ this is not valid json ")
        cout = os.path.join(corrupt, "output")
        cwork = os.path.join(corrupt, "work")
        if run("init", "--input", cinp, "--output", cout, "--work", cwork).returncode != 0:
            failures.append("E: corrupt init exit != 0")
        with open(os.path.join(cout, "result.json"), encoding="utf-8") as fh:
            corrupt_result = json.load(fh)
        if not corrupt_result.get("errors"):
            failures.append("E: corrupt case.json not recorded in errors")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    if failures:
        print("SELFTEST FAIL (%d):" % len(failures))
        for f in failures:
            print("  - %s" % f)
        return 1
    print("SELFTEST PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
