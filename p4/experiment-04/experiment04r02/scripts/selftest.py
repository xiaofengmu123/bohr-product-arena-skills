"""selftest.py - deterministic self-test for the plan-comparison skill.

Runs, in order:
  1. end-to-end demo (clearly marked EXAMPLE) + validate,
  2. empty input directory -> honest input_error/insufficient_evidence, exit 0,
  3. broken case.json -> errors/degradations recorded, exit 0,
  4. determinism: re-run assemble on the same demo, compare the stable core.

Standard library only. Exit code 0 = all checks pass.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl as ci  # noqa: E402

VOLATILE = ("runtime",)


def _strip_volatile(obj):
    if isinstance(obj, dict):
        return {k: _strip_volatile(v) for k, v in obj.items() if k not in VOLATILE}
    if isinstance(obj, list):
        return [_strip_volatile(v) for v in obj]
    return obj


def _normalize_result(path):
    r = json.loads(ci.read_text(path))
    # change_log timestamps are wall-clock metadata, not comparison content
    for entry in r.get("change_log") or []:
        entry.pop("time", None)
    return json.dumps(_strip_volatile(r), sort_keys=True, ensure_ascii=False)


def _run(cmd, args, expect=0):
    code = ci.main([cmd] + args)
    assert code == expect, "command %r exited %d, expected %d" % (cmd, code, expect)


def test_demo():
    base = Path(tempfile.mkdtemp(prefix="selftest_demo_"))
    inp, out, work = base / "input", base / "output", base / "work"
    ci.write_demo_input(str(inp))
    a = ["--input", str(inp), "--output", str(out), "--work", str(work)]
    _run("inspect", a)
    r1 = json.loads(ci.read_text(str(out / "result.json")))
    assert r1.get("case_id") == "EXAMPLE-001", r1.get("case_id")
    assert r1.get("status") in {"partial", "insufficient_evidence", "input_error"}
    ci.write_demo_analysis(str(work))
    _run("assemble", a)
    code = ci.main(["validate", str(out / "result.json"), "--output", str(out), "--work", str(work)])
    assert code == 0, "validate failed on demo output"
    r2 = json.loads(ci.read_text(str(out / "result.json")))
    assert r2.get("selection", {}).get("chosen"), "no chosen plan in demo"
    assert r2.get("comparison_matrix", {}).get("data"), "no matrix in demo"
    pdf = Path(out / "comparison_matrix.pdf").read_bytes()
    assert pdf[:5] == b"%PDF-" and b"%%EOF" in pdf[-32:], "PDF markers missing"
    print("[PASS] demo end-to-end + validate")
    return str(out), str(work), a


def test_empty():
    base = Path(tempfile.mkdtemp(prefix="selftest_empty_"))
    inp, out, work = base / "empty", base / "output", base / "work"
    inp.mkdir()
    a = ["--input", str(inp), "--output", str(out), "--work", str(work)]
    _run("inspect", a)
    r = json.loads(ci.read_text(str(out / "result.json")))
    assert r.get("status") in {"input_error", "insufficient_evidence"}, r.get("status")
    print("[PASS] empty input -> honest status=%s" % r.get("status"))


def test_bad_json():
    base = Path(tempfile.mkdtemp(prefix="selftest_badjson_"))
    inp, out, work = base / "input", base / "output", base / "work"
    inp.mkdir()
    (inp / "case.json").write_text("{ this is not valid json ", encoding="utf-8")
    (inp / "materials").mkdir()
    (inp / "materials" / "note.txt").write_text("some material text", encoding="utf-8")
    a = ["--input", str(inp), "--output", str(out), "--work", str(work)]
    _run("inspect", a)
    r = json.loads(ci.read_text(str(out / "result.json")))
    assert r.get("errors") or r.get("status") in {"input_error", "insufficient_evidence"}, r
    print("[PASS] broken JSON -> errors recorded, no crash")


def test_determinism():
    out, work, a = test_demo()
    first = _normalize_result(os.path.join(out, "result.json"))
    ci.main(["assemble"] + a)
    second = _normalize_result(os.path.join(out, "result.json"))
    assert first == second, "repeated assemble produced different stable core"
    print("[PASS] determinism: repeated assemble identical (modulo wall-clock metadata)")


def main():
    print("=== plan-comparison skill self-test ===")
    test_empty()
    test_bad_json()
    test_determinism()  # includes demo + validate
    print("=== all checks passed ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
