#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — self-check for the library-document-repair skill.

Creates throw-away fixtures under the system temp directory and exercises the
pipeline (full run, skeleton, check, empty input, bad JSON, missing case).
No pre-written test products or benchmark scores are produced; fixtures are
generated at runtime and removed afterwards.

Run from the package root:

    python3 -B scripts/selftest.py

Exit code 0 means all checks passed.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.join(HERE, "core_impl.py")


def run_py(args, cwd):
    return subprocess.run(
        [sys.executable, "-B", CORE] + args, cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def make_fixture(root, files, case_json=None):
    os.makedirs(root, exist_ok=True)
    for rel, content in files.items():
        full = os.path.join(root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        mode = "wb" if isinstance(content, bytes) else "w"
        kwargs = {} if isinstance(content, bytes) else {"encoding": "utf-8"}
        with open(full, mode, **kwargs) as fh:
            fh.write(content)
    if case_json is not None:
        with open(os.path.join(root, "case.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(case_json, fh, ensure_ascii=False)


def check_ok(proc, label):
    if proc.returncode != 0:
        print("FAIL %s: exit=%d\n%s\n%s" % (label, proc.returncode,
                                            proc.stdout, proc.stderr))
        return False
    print("PASS %s" % label)
    return True


def main():
    failures = 0
    tmp = tempfile.mkdtemp(prefix="librepair_selftest_")
    try:
        # ---- 1. full pipeline on a synthetic fixture ------------------ #
        inp = os.path.join(tmp, "input1")
        out = os.path.join(tmp, "output1")
        work = os.path.join(tmp, "work1")
        make_fixture(inp, {
            "notes.txt": "version note: report_v2 replaces report_v1\n",
            "data/report_v1.txt": "alpha beta gamma\n",
            "data/report_v2.txt": "alpha beta gamma delta\n",
            "data/copy_of_report_v2.txt": "alpha beta gamma delta\n",
            "meta/library.csv":
                "title,author,year,file\nR1,Alice,2023,data/report_v1.txt\n",
        }, case_json={
            "case_id": "selftest-001",
            "output_contract": {
                "summary": {"type": "string"},
                "items": {"type": "array"},
            },
        })
        proc = run_py(["--input-root", inp, "--output-dir", out,
                       "--work-dir", work], HERE)
        if not check_ok(proc, "full-run"):
            failures += 1
        else:
            rpath = os.path.join(out, "result.json")
            with open(rpath, "r", encoding="utf-8") as fh:
                r = json.load(fh)
            if r.get("case_id") != "selftest-001":
                print("FAIL case_id: %r" % r.get("case_id"))
                failures += 1
            if r.get("status") != "ok":
                print("FAIL status: %r" % r.get("status"))
                failures += 1
            if "summary" not in r or "items" not in r:
                print("FAIL contract fields not echoed")
                failures += 1
            for art in ("library_manifest.csv", "directory_tree.txt",
                        "dedup_version.json", "recovery_report.json",
                        "library_index.json", "library_manifest.md",
                        "operation_log.jsonl"):
                if not os.path.isfile(os.path.join(out, art)):
                    print("FAIL missing artifact %s" % art)
                    failures += 1
            dupes = r["computed"]["duplicate_groups"]
            if not any(g["count"] >= 2 for g in dupes):
                print("FAIL expected a duplicate group")
                failures += 1

        # ---- 2. skeleton mode ----------------------------------------- #
        out2 = os.path.join(tmp, "output2")
        proc = run_py(["--input-root", inp, "--output-dir", out2,
                       "--work-dir", os.path.join(tmp, "work2"),
                       "--skeleton-only"], HERE)
        if not check_ok(proc, "skeleton-only"):
            failures += 1
        else:
            with open(os.path.join(out2, "result.json"), "r",
                      encoding="utf-8") as fh:
                r2 = json.load(fh)
            if r2.get("status") not in ("partial", "input_error"):
                print("FAIL skeleton status: %r" % r2.get("status"))
                failures += 1

        # ---- 3. check passes on the full result ------------------------ #
        proc = run_py(["--check", os.path.join(out, "result.json"),
                       "--case-json", os.path.join(inp, "case.json")], HERE)
        if not check_ok(proc, "check"):
            failures += 1

        # ---- 4. empty input -------------------------------------------- #
        inp3 = os.path.join(tmp, "input3")
        out3 = os.path.join(tmp, "output3")
        make_fixture(inp3, {}, case_json={"case_id": "selftest-empty"})
        proc = run_py(["--input-root", inp3, "--output-dir", out3,
                       "--work-dir", os.path.join(tmp, "work3")], HERE)
        if not check_ok(proc, "empty-input"):
            failures += 1
        else:
            with open(os.path.join(out3, "result.json"), "r",
                      encoding="utf-8") as fh:
                r3 = json.load(fh)
            if r3.get("status") != "insufficient_evidence":
                print("FAIL empty-input status: %r" % r3.get("status"))
                failures += 1

        # ---- 5. bad JSON case ------------------------------------------ #
        inp4 = os.path.join(tmp, "input4")
        out4 = os.path.join(tmp, "output4")
        make_fixture(inp4, {"a.txt": "hello\n"})
        with open(os.path.join(inp4, "case.json"), "w",
                  encoding="utf-8") as fh:
            fh.write("{ not valid json ")
        proc = run_py(["--input-root", inp4, "--output-dir", out4,
                       "--work-dir", os.path.join(tmp, "work4")], HERE)
        if not check_ok(proc, "bad-json-case"):
            failures += 1
        else:
            with open(os.path.join(out4, "result.json"), "r",
                      encoding="utf-8") as fh:
                r4 = json.load(fh)
            if r4.get("status") != "partial":
                print("FAIL bad-json status: %r" % r4.get("status"))
                failures += 1
            if not r4.get("errors"):
                print("FAIL bad-json should record errors")
                failures += 1

        # ---- 6. missing case.json -------------------------------------- #
        inp5 = os.path.join(tmp, "input5")
        out5 = os.path.join(tmp, "output5")
        make_fixture(inp5, {"a.txt": "hello\n"})
        proc = run_py(["--input-root", inp5, "--output-dir", out5,
                       "--work-dir", os.path.join(tmp, "work5")], HERE)
        if not check_ok(proc, "missing-case"):
            failures += 1
        else:
            with open(os.path.join(out5, "result.json"), "r",
                      encoding="utf-8") as fh:
                r5 = json.load(fh)
            if r5.get("status") != "partial":
                print("FAIL missing-case status: %r" % r5.get("status"))
                failures += 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("selftest: %d failure(s)" % failures)
        return 1
    print("selftest: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
