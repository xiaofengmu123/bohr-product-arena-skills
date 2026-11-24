#!/usr/bin/env python3
"""Self-test for the fact-traceable academic-writing skill package.

Runs without network or third-party packages. Verifies:
  1. SKILL.md exists with valid YAML frontmatter (name / description / version).
  2. scripts import and `main.py --help` exits 0.
  3. Empty input dir -> exit 0, valid result.json, status insufficient_evidence.
  4. Bad case.json -> errors recorded, process does not crash.
  5. Missing input dir -> exit 0, valid result.json skeleton.
  6. result.json has the required schema keys.

Usage:  python3 scripts/selftest.py   (exit 0 = pass, 1 = fail)
"""

import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import core_impl as ci  # noqa: E402

REQUIRED_RESULT_KEYS = {"case_id", "status", "sources", "uncertainty",
                        "pending_human", "change_log", "errors", "degradations",
                        "runtime"}


def _check(name, cond, detail=""):
    print("%-42s %s%s" % (name, "PASS" if cond else "FAIL",
                           ("  # " + detail) if detail else ""))
    return bool(cond)


def check_frontmatter():
    path = os.path.join(ROOT, "SKILL.md")
    if not os.path.isfile(path):
        return _check("SKILL.md exists", False, path)
    text, err = ci.read_text(path)
    if err:
        return _check("SKILL.md readable", False, err)
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return _check("SKILL.md frontmatter present", False)
    fm = m.group(1)
    ok = True
    for key in ("name:", "description:", "version:"):
        if key not in fm:
            ok = False
    return _check("SKILL.md frontmatter name/description/version", ok)


def _run_main(args, cwd):
    return subprocess.run(
        [sys.executable, os.path.join(HERE, "main.py")] + args,
        cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace")


def check_help():
    r = _run_main(["--help"], HERE)
    return _check("main.py --help exits 0", r.returncode == 0, "rc=%d" % r.returncode)


def check_empty_input():
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "input")
        out = os.path.join(td, "output")
        work = os.path.join(td, "work")
        os.makedirs(inp)
        r = _run_main(["init", "--input", inp, "--output", out, "--work", work], HERE)
        res, err = ci.read_json(os.path.join(out, "result.json"))
        valid = err is None and isinstance(res, dict)
        status_ok = valid and res.get("status") == "insufficient_evidence"
        return _check("empty input -> insufficient_evidence", r.returncode == 0 and valid and status_ok,
                      "rc=%d status=%s" % (r.returncode, res.get("status") if valid else "?"))
    return _check("empty input -> insufficient_evidence", False)


def check_bad_json():
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "input")
        out = os.path.join(td, "output")
        work = os.path.join(td, "work")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            f.write("{ this is not valid json ")
        r = _run_main(["init", "--input", inp, "--output", out, "--work", work], HERE)
        res, err = ci.read_json(os.path.join(out, "result.json"))
        valid = err is None and isinstance(res, dict)
        degraded = valid and bool(res.get("errors") or res.get("degradations"))
        return _check("bad case.json -> recorded, no crash", r.returncode == 0 and valid and degraded,
                      "rc=%d valid=%s degraded=%s" % (r.returncode, valid, degraded))
    return _check("bad case.json -> recorded, no crash", False)


def check_missing_input():
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "no-such-input")
        out = os.path.join(td, "output")
        work = os.path.join(td, "work")
        r = _run_main(["init", "--input", inp, "--output", out, "--work", work], HERE)
        res, err = ci.read_json(os.path.join(out, "result.json"))
        valid = err is None and isinstance(res, dict)
        return _check("missing input dir -> valid skeleton", r.returncode == 0 and valid,
                      "rc=%d valid=%s" % (r.returncode, valid))
    return _check("missing input dir -> valid skeleton", False)


def check_schema():
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "input")
        out = os.path.join(td, "output")
        work = os.path.join(td, "work")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "selftest-case",
                       "output_contract": {"deliverable": "manuscript.md",
                                           "fact_source_map": "evidence_map.json"}},
                      f)
        _run_main(["init", "--input", inp, "--output", out, "--work", work], HERE)
        res, err = ci.read_json(os.path.join(out, "result.json"))
        missing = [k for k in REQUIRED_RESULT_KEYS if not (res or {}).get(k) is not None] \
            if isinstance(res, dict) else list(REQUIRED_RESULT_KEYS)
        keys_ok = err is None and isinstance(res, dict) and not missing
        contract_ok = keys_ok and "deliverable" in res and "fact_source_map" in res
        return _check("result.json schema + contract fields", keys_ok and contract_ok,
                      "missing=%s" % (missing or "none"))
    return _check("result.json schema + contract fields", False)


def main():
    checks = [
        check_frontmatter,
        check_help,
        check_empty_input,
        check_bad_json,
        check_missing_input,
        check_schema,
    ]
    results = []
    for fn in checks:
        try:
            results.append(fn())
        except Exception as e:  # noqa: BLE001
            print("%-42s FAIL  # exception: %s" % (fn.__name__, e))
            results.append(False)
    passed = sum(results)
    print("\n%d/%d checks passed" % (passed, len(results)))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
