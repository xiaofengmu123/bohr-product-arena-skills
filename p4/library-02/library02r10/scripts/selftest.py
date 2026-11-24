# -*- coding: utf-8 -*-
"""selftest.py — self-contained smoke tests for the skill package.

Runs the pipeline against temporary synthetic inputs (clearly labelled as
examples, never as formal case results) and verifies the robustness contract:

  * full run produces result.json + all editable artifacts;
  * ``--check`` passes and reports ``case_id`` + honest status;
  * empty input dir -> ``insufficient_evidence``, exit 0, no crash;
  * missing case.json -> ``input_error``;
  * bad JSON / bad encoding -> recorded in errors/degradations, not a crash.

Exit code 0 means all checks passed. Only the standard library is used.
"""

import json
import os
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append(name)
        print("  FAIL  %s  %s" % (name, detail))


def make_case(root):
    """Create a small synthetic library (example, not a formal case)."""
    inp = os.path.join(root, "input")
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    os.makedirs(os.path.join(inp, "sub", "empty_dir"), exist_ok=True)
    os.makedirs(out, exist_ok=True)
    os.makedirs(work, exist_ok=True)

    def w(rel, text):
        p = os.path.join(inp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)

    w("case.json", json.dumps({
        "case_id": "selftest-case",
        "output_contract": {"manifest": "list", "summary": "object"},
        "questions": ["how many files", "duplicate files"],
    }, ensure_ascii=False))
    w("task.md", "# 自检样例\nchallenge_id: `selftest-0000`\n")
    w("report_v1.md", "# 方法\n1. 取样\n这里是方法学段落内容。\n")
    w("report_v2_final.md", "# 方法\n1. 取样\n这里是方法学段落内容，略有改动。\n")
    w("report_v2_final copy.md", "# 方法\n1. 取样\n这里是方法学段落内容，略有改动。\n")
    w("notes.md", "实验记录：缓冲液配方见第 3 版。\n")
    w("catalog.csv", "filename,title,year\nreport_v1.md,First,2020\n"
                     "report_v2_final.md,Second,2021\nmissing.md,Ghost,2019\n")
    w("bad.json", "{ this is not json")
    w("bad-enc.txt", "\xff\xfe\x00garbage")
    # a broken symlink (skipped silently if the OS forbids it)
    try:
        os.symlink("nonexistent.pdf", os.path.join(inp, "broken.pdf"))
    except (OSError, NotImplementedError):
        pass
    return inp, out, work


def run_case(inp, out, work, extra=None):
    argv = ["--input", inp, "--output", out, "--work", work]
    if extra:
        argv += extra
    return core.main(argv)


def main():
    print("== document-library skill selftest ==")
    base = tempfile.mkdtemp(prefix="libskill_test_")

    # 1) full run
    inp, out, work = make_case(base)
    rc = run_case(inp, out, work)
    check("full run exit 0", rc == 0, "rc=%s" % rc)

    result_path = os.path.join(out, "result.json")
    check("result.json exists", os.path.isfile(result_path))
    result = json.load(open(result_path, encoding="utf-8"))
    check("case_id present", bool(result.get("case_id")))
    check("status ok/partial", result.get("status") in ("ok", "partial"),
          result.get("status"))
    check("contract field echoed",
          "manifest" in result and "summary" in result,
          "keys=%s" % sorted(result.keys())[:10])
    for name in ("library_manifest.csv", "directory_tree.txt",
                 "dedup_version.json", "recovery_report.json",
                 "library_index.json", "library_manifest.md",
                 "operation_log.jsonl"):
        check("artifact %s" % name, os.path.isfile(os.path.join(out, name)))

    check("dup group detected",
          len(result.get("computed", {}).get("duplicate_groups", [])) >= 1,
          "groups=%s" % result.get("computed", {}).get("duplicate_groups", [])[:1])
    check("version candidate detected",
          len(result.get("computed", {}).get("version_candidates", [])) >= 1)
    check("answers produced",
          len(result.get("computed", {}).get("answers", [])) >= 1)

    # 2) --check
    rc2 = core.main(["--check", "--result", result_path, "--case-json",
                     os.path.join(inp, "case.json")])
    check("--check passes", rc2 == 0, "rc=%s" % rc2)

    # 3) empty input dir (with a valid case.json -> insufficient_evidence)
    empty_root = tempfile.mkdtemp(prefix="libskill_empty_", dir=base)
    ein = os.path.join(empty_root, "input")
    eout = os.path.join(empty_root, "output")
    ework = os.path.join(empty_root, "work")
    os.makedirs(ein)
    os.makedirs(eout)
    os.makedirs(ework)
    with open(os.path.join(ein, "case.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"case_id": "empty-case",
                             "output_contract": {"manifest": "list"}}))
    rc3 = core.main(["--input", ein, "--output", eout, "--work", ework])
    eres = json.load(open(os.path.join(eout, "result.json"), encoding="utf-8"))
    check("empty dir exit 0", rc3 == 0, "rc=%s" % rc3)
    check("empty dir status insufficient_evidence",
          eres.get("status") == "insufficient_evidence", eres.get("status"))

    # 4) missing case.json
    miss_root = tempfile.mkdtemp(prefix="libskill_nocase_", dir=base)
    min_ = os.path.join(miss_root, "input")
    mout = os.path.join(miss_root, "output")
    mwork = os.path.join(miss_root, "work")
    os.makedirs(min_)
    os.makedirs(mout)
    os.makedirs(mwork)
    with open(os.path.join(min_, "some.txt"), "w", encoding="utf-8") as fh:
        fh.write("hello\n")
    rc4 = core.main(["--input", min_, "--output", mout, "--work", mwork])
    mres = json.load(open(os.path.join(mout, "result.json"), encoding="utf-8"))
    check("missing case exit 0", rc4 == 0, "rc=%s" % rc4)
    check("missing case status input_error",
          mres.get("status") == "input_error", mres.get("status"))

    print("\n== summary: %d pass, %d fail ==" % (len(PASS), len(FAIL)))
    for name in FAIL:
        print("FAILED: %s" % name)
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(2)
