#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-check: validate result.json + robustness smoke tests.

Two modes:
  1) --out PATH   : validate an existing result.json (exit 0 = pass).
  2) --smoke      : run empty-input / bad-JSON / missing-input / oversized-file
                    scenarios in a temp dir and assert graceful degradation.

Standard library only. Run from the skill root:
  python -B scripts/selftest.py --out /app/output/result.json
  python -B scripts/selftest.py --smoke
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import skeleton  # noqa: E402

VALID_STATUS = {"in_progress", "done", "partial", "insufficient_evidence",
                "input_error", "failed"}
REQUIRED_KEYS = ["case_id", "status", "sources", "uncertainty", "change_log"]
CONTRACT_KEYS = ["artifact", "fact_source_map", "version_boundary"]


def validate_result(path: str):
    """Return (ok, problems)."""
    obj, err = skeleton.load_json(path)
    if obj is None:
        return False, ["cannot_load:" + str(err)]
    problems = []
    for k in REQUIRED_KEYS:
        if k not in obj:
            problems.append("missing_key:%s" % k)
    if obj.get("status") not in VALID_STATUS:
        problems.append("bad_status:%s" % obj.get("status"))
    if obj.get("case_id") is None and obj.get("status") not in ("in_progress",):
        # case_id may be None only when input unavailable; still record, not fail
        pass
    if not isinstance(obj.get("sources", []), list):
        problems.append("sources_not_list")
    if not isinstance(obj.get("change_log", []), list):
        problems.append("change_log_not_list")
    if not isinstance(obj.get("uncertainty", []), list):
        problems.append("uncertainty_not_list")
    for k in CONTRACT_KEYS:
        if k not in obj:
            problems.append("missing_contract_key:%s" % k)
    return (not problems), problems


def run_scenario(name, setup):
    """Run one robustness scenario; return (ok, detail)."""
    tmp = tempfile.mkdtemp(prefix="grant_skill_%s_" % name)
    try:
        setup(tmp)
        out = os.path.join(tmp, "out", "result.json")
        code = os.system(
            "%s -B %s --input %s --output %s --work %s >nul 2>nul"
            % (sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            "main.py"),
               os.path.join(tmp, "input"), os.path.join(tmp, "out"),
               os.path.join(tmp, "work"))
        )
        if code != 0:
            return False, {"scenario": name, "reason": "exit_code_%d" % code}
        obj, err = skeleton.load_json(out)
        if obj is None:
            return False, {"scenario": name, "reason": "no_result:%s" % err}
        return True, {"scenario": name, "status": obj.get("status"),
                      "errors": obj.get("errors"), "degradations": obj.get("degradations")}
    except Exception as exc:  # noqa: BLE001
        return False, {"scenario": name, "reason": "exception:%s" % exc}
    finally:
        # best-effort cleanup
        for root, dirs, files in os.walk(tmp, topdown=False):
            for f in files:
                try:
                    os.remove(os.path.join(root, f))
                except OSError:
                    pass
            for d in dirs:
                try:
                    os.rmdir(os.path.join(root, d))
                except OSError:
                    pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass


def _write(path, content, binary=False):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    mode = "wb" if binary else "w"
    with open(path, mode, encoding=None if binary else "utf-8") as fh:
        fh.write(content)


def setup_empty(tmp):
    os.makedirs(os.path.join(tmp, "input"), exist_ok=True)


def setup_bad_json(tmp):
    d = os.path.join(tmp, "input")
    os.makedirs(d, exist_ok=True)
    _write(os.path.join(d, "case.json"), "{ not valid json")


def setup_missing(tmp):
    # no input dir at all
    pass


def setup_oversized(tmp):
    d = os.path.join(tmp, "input")
    os.makedirs(d, exist_ok=True)
    _write(os.path.join(d, "case.json"), json.dumps({"case_id": "t1", "id": "t1"}))
    with open(os.path.join(d, "big.txt"), "wb") as fh:
        fh.write(b"x" * (skeleton.MAX_FILE_BYTES + 10))


def smoke():
    scenarios = [
        ("empty_input", setup_empty),
        ("bad_json", setup_bad_json),
        ("missing_input", setup_missing),
        ("oversized_file", setup_oversized),
    ]
    results = []
    all_ok = True
    for name, fn in scenarios:
        ok, detail = run_scenario(name, fn)
        all_ok = all_ok and ok
        results.append(detail)
    return all_ok, results


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description="Grant Skill self-check")
    ap.add_argument("--out", default=None, help="validate existing result.json")
    ap.add_argument("--smoke", action="store_true", help="run robustness smoke tests")
    a = ap.parse_args(argv)

    if a.smoke:
        ok, results = smoke()
        print(json.dumps({"smoke_ok": ok, "results": results}, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    path = a.out
    if not path:
        # default to /app/output/result.json or output/result.json
        path = "/app/output/result.json" if os.path.isdir("/app") else "output/result.json"
    ok, problems = validate_result(path)
    print(json.dumps({"selftest_ok": ok, "out": path, "problems": problems},
                     ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
