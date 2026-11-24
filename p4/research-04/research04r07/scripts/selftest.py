# -*- coding: utf-8 -*-
"""Standalone self-test for the research-frontier-trend Skill.

Two modes:

  python3 -B scripts/selftest.py --result /app/output/result.json
      Validate an existing result.json against the contract (exit 0 if no
      FAIL-level issues).

  python3 -B scripts/selftest.py --smoke
      Build synthetic robustness cases in a temp dir and run the full
      pipeline against each: empty input, bad JSON, oversized file, missing
      materials, mixed encodings.  Each case must exit 0 and produce a valid
      result.json with an honest status.  This is the executable proof of the
      robustness variant.

Only the standard library is used.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl as core  # noqa: E402

MAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")


def _run(args):
    return subprocess.run(
        [sys.executable, "-B", MAIN] + args,
        capture_output=True, timeout=300,
    )


def _read_result(path):
    obj, err = core.load_json(path)
    return obj, err


def check_result(path):
    """Validate a result.json file; print a checklist and return pass/fail."""
    obj, err = _read_result(path)
    if err is not None:
        print("[FAIL] cannot load result.json: %s" % err)
        return False
    checks = core.validate_result(obj)
    fails = core.hard_failures(checks)
    for c in checks:
        mark = "ok  " if c["ok"] else "FAIL" if c["level"] == "FAIL" else "warn"
        print("[%s] %s %s" % (mark, c["check"], ("- " + c["detail"]) if c["detail"] else ""))
    return not fails


def _case(name, files):
    """Build one synthetic input dir; `files` is dict relpath->content."""
    root = tempfile.mkdtemp(prefix="selftest_")
    indir = os.path.join(root, "input")
    os.makedirs(indir)
    for rel, content in files.items():
        path = os.path.join(indir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = "wb" if isinstance(content, bytes) else "w"
        kwargs = {} if isinstance(content, bytes) else {"encoding": "utf-8"}
        with open(path, mode, **kwargs) as fh:
            fh.write(content)
    return root, indir


def smoke_case(title, files, expect_status, expect_exit=0):
    root, indir = _case(title, files)
    outdir = os.path.join(root, "output")
    workdir = os.path.join(root, "work")
    proc = _run(["--input", indir, "--output", outdir, "--work", workdir])
    obj, err = _read_result(os.path.join(outdir, "result.json"))
    ok = True
    problems = []
    if proc.returncode != expect_exit:
        ok = False
        problems.append("exit %d != %d" % (proc.returncode, expect_exit))
    if err is not None:
        ok = False
        problems.append("result.json invalid: %s" % err)
    if obj is not None and obj.get("status") != expect_status:
        ok = False
        problems.append("status %r != %r" % (obj.get("status"), expect_status))
    if obj is not None:
        checks = core.validate_result(obj)
        hard = core.hard_failures(checks)
        if hard:
            ok = False
            problems.append("%d hard validation failures" % len(hard))
    print("[%s] %s" % ("ok  " if ok else "FAIL", title) + (("  -> " + "; ".join(problems)) if problems else ""))
    shutil.rmtree(root, ignore_errors=True)
    return ok


def run_smoke():
    cases = [
        ("empty input directory", {}, "insufficient_evidence"),
        ("missing input path", None, "insufficient_evidence"),
        ("bad JSON case file", {
            "case.json": '{"case_id": "broken", "output_contract": {',  # invalid
            "task.md": "# task\n\nchallenge_id: `challenge-smoke-1`\n",
            "materials/a.txt": "Future work remains unclear. Year 2021 study A.",
        }, "partial"),
        ("oversized material file", {
            "case.json": json.dumps({"case_id": "case-big", "output_contract": {"expect": ["x"]}}),
            "task.md": "# task\n",
            "materials/big.txt": "Year 2020. Future work remains unclear. " + "y" * (core.MAX_TEXT_BYTES + 5000),
        }, "partial"),
        ("mixed encodings (gbk + utf-8)", {
            "case.json": json.dumps({"case_id": "case-enc"}),
            "task.md": "# task\n",
            "materials/gbk.txt": "未来工作仍需研究，2018 年有方法 A。".encode("gbk"),
            "materials/utf8.txt": "However, results conflict. 2020 study B.",
        }, "partial"),
        ("only task.md, no materials", {
            "task.md": "# task\nchallenge_id: `challenge-smoke-2`\n",
        }, "insufficient_evidence"),
    ]
    all_ok = True
    for title, files, expect_status in cases:
        if files is None:
            # missing input path: pass a nonexistent dir
            tmp = tempfile.mkdtemp(prefix="selftest_missing_")
            root = tmp
            indir = os.path.join(tmp, "does-not-exist")
        else:
            root, indir = _case(title, files)
        outdir = os.path.join(root, "output")
        workdir = os.path.join(root, "work")
        proc = _run(["--input", indir, "--output", outdir, "--work", workdir])
        obj, err = _read_result(os.path.join(outdir, "result.json"))
        ok = True
        problems = []
        if proc.returncode != 0:
            ok = False
            problems.append("exit %d != 0" % proc.returncode)
        if err is not None:
            ok = False
            problems.append("result.json invalid: %s" % err)
        if obj is not None and obj.get("status") != expect_status:
            ok = False
            problems.append("status %r != %r" % (obj.get("status"), expect_status))
        if obj is not None:
            hard = core.hard_failures(core.validate_result(obj))
            if hard:
                ok = False
                problems.append("%d hard validation failures" % len(hard))
        print("[%s] %s" % ("ok  " if ok else "FAIL", title)
              + (("  -> " + "; ".join(problems)) if problems else ""))
        all_ok = all_ok and ok
        shutil.rmtree(root, ignore_errors=True)
    return all_ok


def main(argv=None):
    parser = argparse.ArgumentParser(prog="selftest.py")
    parser.add_argument("--result", help="validate an existing result.json")
    parser.add_argument("--smoke", action="store_true", help="run robustness smoke cases")
    args = parser.parse_args(argv)

    if args.smoke:
        print("== robustness smoke test (synthetic inputs only, no real answers) ==")
        return 0 if run_smoke() else 1

    if args.result:
        print("== result.json contract check ==")
        return 0 if check_result(args.result) else 1

    # Default: try the runtime output path, then a local one.
    for candidate in ("/app/output/result.json", "output/result.json", "result.json"):
        if os.path.isfile(candidate):
            print("== result.json contract check (%s) ==" % candidate)
            return 0 if check_result(candidate) else 1
    print("[FAIL] no result.json found; pass --result PATH or --smoke")
    return 1


if __name__ == "__main__":
    sys.exit(main())
