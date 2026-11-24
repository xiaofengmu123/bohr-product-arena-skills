#!/usr/bin/env python3
"""Self-test for the systematic-review skill (robustness variant).

Runs five scenarios in isolated temporary directories and checks that the
runtime helper degrades gracefully (exit code 0, valid result.json) instead of
crashing. Uses only the standard library and calls the command functions
in-process, so it works without spawning subprocesses.

Run from the skill root:  python -B scripts/selftest.py
Exit code 0 = all scenarios passed, 1 = at least one failed.
"""

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resultlib as R
import main as M


class _Args(object):
    def __init__(self, input_dir, output_dir, work_dir, payload=None):
        self.input = input_dir
        self.output = output_dir
        self.work = work_dir
        self.payload = payload


def _read_result(output_dir):
    obj, errors, _ = R.load_json(os.path.join(output_dir, "result.json"))
    return obj, errors


def scenario_empty_input():
    """Empty input directory -> insufficient_evidence, valid JSON, no crash."""
    root = tempfile.mkdtemp(prefix="selsr-empty-")
    args = _Args(root, os.path.join(root, "out"), os.path.join(root, "work"))
    M.cmd_skeleton(args)
    result, errors = _read_result(args.output)
    ok = (isinstance(result, dict)
          and result.get("status") == R.STATUS_INSUFFICIENT
          and not errors)
    return ok, "status=%s errors=%d" % (result.get("status") if isinstance(result, dict) else None, len(errors))


def scenario_bad_json():
    """Unparseable case.json -> input_error, valid JSON, no crash."""
    root = tempfile.mkdtemp(prefix="selsr-badjson-")
    with open(os.path.join(root, "case.json"), "wb") as handle:
        handle.write(b'{"case_id": "broken", "output_contract": ')
    args = _Args(root, os.path.join(root, "out"), os.path.join(root, "work"))
    M.cmd_skeleton(args)
    result, errors = _read_result(args.output)
    ok = (isinstance(result, dict)
          and result.get("status") == R.STATUS_INPUT_ERROR
          and bool(result.get("errors")))
    return ok, "status=%s" % (result.get("status") if isinstance(result, dict) else None)


def scenario_bad_encoding():
    """Undecodable bytes in a material -> degradation recorded, no crash."""
    root = tempfile.mkdtemp(prefix="selsr-badenc-")
    with open(os.path.join(root, "case.json"), "wb") as handle:
        handle.write(json.dumps({"case_id": "enc-1"}, ensure_ascii=False).encode("utf-8"))
    with open(os.path.join(root, "note.txt"), "wb") as handle:
        handle.write(b"\xff\xfe\x81\x00not-valid-utf8")
    args = _Args(root, os.path.join(root, "out"), os.path.join(root, "work"))
    M.cmd_skeleton(args)
    result, errors = _read_result(args.output)
    degradations = result.get("degradations", []) if isinstance(result, dict) else []
    ok = (isinstance(result, dict) and not errors and len(degradations) >= 1)
    return ok, "degradations=%d" % len(degradations)


def scenario_missing_params():
    """Nonexistent --input -> insufficient_evidence, no crash."""
    root = tempfile.mkdtemp(prefix="selsr-missing-")
    args = _Args(os.path.join(root, "does-not-exist"),
                 os.path.join(root, "out"), os.path.join(root, "work"))
    M.cmd_skeleton(args)
    result, errors = _read_result(args.output)
    ok = (isinstance(result, dict)
          and result.get("status") == R.STATUS_INSUFFICIENT
          and not errors)
    return ok, "status=%s" % (result.get("status") if isinstance(result, dict) else None)


def scenario_oversized_file():
    """File larger than the 2 MB limit -> truncated/flagged, no crash."""
    root = tempfile.mkdtemp(prefix="selsr-big-")
    with open(os.path.join(root, "case.json"), "wb") as handle:
        handle.write(json.dumps({"case_id": "big-1",
                                 "output_contract": {"review_question": None}},
                                ensure_ascii=False).encode("utf-8"))
    big_path = os.path.join(root, "materials")
    os.makedirs(big_path, exist_ok=True)
    with open(os.path.join(big_path, "huge.txt"), "wb") as handle:
        handle.write(b"x" * (3 * 1024 * 1024))  # 3 MB
    args = _Args(root, os.path.join(root, "out"), os.path.join(root, "work"))
    M.cmd_skeleton(args)
    inv = R.discover_input(args.input)
    flagged = any("exceeds" in d for d in inv.get("degradations", []))
    result, errors = _read_result(args.output)
    ok = (isinstance(result, dict) and flagged and not errors)
    return ok, "oversize_flagged=%s" % flagged


SCENARIOS = [
    ("empty_input", scenario_empty_input),
    ("bad_json", scenario_bad_json),
    ("bad_encoding", scenario_bad_encoding),
    ("missing_params", scenario_missing_params),
    ("oversized_file", scenario_oversized_file),
]


def run_selftests():
    results = []
    for name, func in SCENARIOS:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                passed, detail = func()
        except Exception as exc:  # noqa: BLE001 - a scenario crash is a failure
            passed, detail = False, "scenario raised: %r" % exc
        results.append({"scenario": name, "pass": passed, "detail": detail})
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(prog="selftest.py")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON summary")
    args = parser.parse_args(argv)
    results = run_selftests()
    all_ok = all(r["pass"] for r in results)
    if args.json:
        print(json.dumps({"ok": all_ok, "results": results},
                         ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for r in results:
            print("[%s] %-16s %s" % ("PASS" if r["pass"] else "FAIL", r["scenario"], r["detail"]))
        print("selftest:", "ALL PASS" if all_ok else "FAILURES PRESENT")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
