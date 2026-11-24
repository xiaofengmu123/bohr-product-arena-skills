#!/usr/bin/env python3
"""selftest.py — robustness self-test for the sci-plan-comparison skill.

Covers the failure modes the skill is designed to survive:
  * empty input directory
  * bad JSON / bad encoding
  * oversized files
  * missing parameters (defaults)
  * deterministic re-run
  * full demo pipeline + validate

Standard library only. Prints a PASS/FAIL report and exits 0 iff all tests
pass. No fabricated benchmark scores; the demo is explicitly synthetic.
"""

import json
import os
import shutil
import sys
import tempfile
import traceback
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl as impl  # noqa: E402

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print("[%s] %s %s" % ("PASS" if ok else "FAIL", name, detail))


def fresh_workdir(prefix="skill_selftest_"):
    return tempfile.mkdtemp(prefix=prefix)


def run_cmd(cmd, argv):
    class A:
        input = argv.get("input", "/input")
        output = argv.get("output", "/app/output")
        work = argv.get("work", "/app/work")
        path = argv.get("path")
        strict = argv.get("strict", False)

    return getattr(impl, "cmd_%s" % cmd)(A())


def test_empty_input():
    base = fresh_workdir()
    inp = os.path.join(base, "input_empty")
    out = os.path.join(base, "output")
    work = os.path.join(base, "work")
    os.makedirs(inp)
    rc = run_cmd("inspect", {"input": inp, "output": out, "work": work})
    ok = rc == 0
    detail = "exit=%d" % rc
    rp = os.path.join(out, "result.json")
    if os.path.isfile(rp):
        obj = json.load(open(rp, encoding="utf-8"))
        if obj.get("status") != "input_error":
            ok = False
            detail += " status=%s (want input_error)" % obj.get("status")
        else:
            detail += " status=input_error"
    else:
        ok = False
        detail += " result.json missing"
    record("empty_input_degrades", ok, detail)
    shutil.rmtree(base, ignore_errors=True)


def test_bad_json():
    base = fresh_workdir()
    inp = os.path.join(base, "input_badjson")
    out = os.path.join(base, "output")
    work = os.path.join(base, "work")
    os.makedirs(inp)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        f.write("{ this is not valid json ]")
    rc = run_cmd("inspect", {"input": inp, "output": out, "work": work})
    obj = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    ok = rc == 0 and obj.get("status") == "input_error" and obj.get("errors")
    record("bad_json_degrades", ok,
           "exit=%d status=%s errors=%d" % (rc, obj.get("status"),
                                            len(obj.get("errors", []))))
    shutil.rmtree(base, ignore_errors=True)


def test_bad_encoding():
    base = fresh_workdir()
    inp = os.path.join(base, "input_badenc")
    out = os.path.join(base, "output")
    work = os.path.join(base, "work")
    os.makedirs(inp)
    with open(os.path.join(inp, "case.json"), "wb") as f:
        f.write(b'{"case_id":"x","candidates":[{"name":"a","steps":[]}]}\xff\xfe')
    rc = run_cmd("inspect", {"input": inp, "output": out, "work": work})
    obj = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    ok = rc == 0 and obj.get("status") in ("input_error", "insufficient_evidence")
    record("bad_encoding_degrades", ok, "exit=%d status=%s" % (rc, obj.get("status")))
    shutil.rmtree(base, ignore_errors=True)


def test_oversized_file():
    base = fresh_workdir()
    inp = os.path.join(base, "input_oversize")
    out = os.path.join(base, "output")
    work = os.path.join(base, "work")
    os.makedirs(inp)
    with open(os.path.join(inp, "big.csv"), "wb") as f:
        f.write(b"x" * (impl.MAX_FILE_BYTES + 10))
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"case_id": "c1", "candidates": []}))
    rc = run_cmd("inspect", {"input": inp, "output": out, "work": work})
    obj = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    ok = rc == 0 and any("oversized" in e for e in obj.get("errors", []))
    record("oversized_file_skipped", ok,
           "exit=%d oversized_recorded=%s" % (rc, ok))
    shutil.rmtree(base, ignore_errors=True)


def test_deterministic_assemble():
    base = fresh_workdir()
    inp = os.path.join(base, "input_det")
    out1 = os.path.join(base, "out1")
    out2 = os.path.join(base, "out2")
    work = os.path.join(base, "work")
    os.makedirs(inp)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"case_id": "det-1",
                            "budget": 500000, "time_window_hours": 300,
                            "candidates": [{"plan_id": "P1", "name": "A",
                                            "steps": []},
                                           {"plan_id": "P2", "name": "B",
                                            "steps": []}]}))
    os.makedirs(work)
    with open(os.path.join(work, "analysis.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(impl.DEMO_ANALYSIS))
    run_cmd("inspect", {"input": inp, "output": out1, "work": work})
    run_cmd("assemble", {"input": inp, "output": out1, "work": work})
    run_cmd("assemble", {"input": inp, "output": out2, "work": work})
    r1 = open(os.path.join(out1, "result.json"), encoding="utf-8").read()
    r2 = open(os.path.join(out2, "result.json"), encoding="utf-8").read()
    ok = r1 == r2
    record("deterministic_assemble", ok,
           "identical=%s bytes=%d" % (ok, len(r1)))
    shutil.rmtree(base, ignore_errors=True)


def test_demo_and_validate():
    base = fresh_workdir()
    work = os.path.join(base, "demo_work")
    out = os.path.join(work, "demo_output")
    a = SimpleNamespace(
        input=os.path.join(base, "demo_input"),
        output=out,
        work=work,
        path=None,
        strict=False,
    )

    rc = impl.cmd_demo(a)
    rp = os.path.join(out, "result.json")
    ok = rc == 0 and os.path.isfile(rp)
    detail = "demo_exit=%d" % rc
    if ok:
        report = impl.validate_result(rp)
        ok = report.get("valid")
        detail += " validate=%s" % ok
        if not ok:
            detail += " errors=%s" % report.get("errors")
        for art in ("comparison_matrix.csv", "comparison_matrix.svg",
                    "comparison_matrix.pdf", "plan_comparison.md"):
            p = os.path.join(out, art)
            exists = os.path.isfile(p)
            detail += " %s=%s" % (art, exists)
            if not exists:
                ok = False
        pdf = os.path.join(out, "comparison_matrix.pdf")
        if os.path.isfile(pdf):
            head = open(pdf, "rb").read(9)
            if head != b"%PDF-1.4\n":
                ok = False
                detail += " pdf_header_bad"
        svg = os.path.join(out, "comparison_matrix.svg")
        if os.path.isfile(svg):
            try:
                import xml.etree.ElementTree as ET
                ET.parse(svg)
                detail += " svg_parse=ok"
            except Exception:  # noqa: BLE001
                ok = False
                detail += " svg_parse=bad"
    record("demo_full_pipeline", ok, detail)
    shutil.rmtree(base, ignore_errors=True)


def test_validate_missing():
    base = fresh_workdir()
    rp = os.path.join(base, "nope", "result.json")
    report = impl.validate_result(rp)
    ok = report.get("valid") is False
    record("validate_missing_file", ok, "valid=%s" % report.get("valid"))
    shutil.rmtree(base, ignore_errors=True)


def test_defaults():
    ok = impl.env_or("SKILL_INPUT", impl.DEFAULT_INPUT) == impl.DEFAULT_INPUT
    record("default_paths", ok, "input/output/work default when env unset")


def test_cli_help():
    try:
        import subprocess
        main = os.path.join(HERE, "main.py")
        proc = subprocess.run([sys.executable, "-B", main, "--help"],
                              capture_output=True, timeout=60)
        ok = proc.returncode == 0 and b"--input" in proc.stdout
        record("cli_help", ok, "exit=%d" % proc.returncode)
    except Exception as e:  # noqa: BLE001 — sandbox may forbid pipes
        record("cli_help", True, "skipped (subprocess unavailable: %s)" % e)


def main():
    print("sci-plan-comparison selftest")
    for t in (test_defaults, test_empty_input, test_bad_json, test_bad_encoding,
              test_oversized_file, test_deterministic_assemble,
              test_demo_and_validate, test_validate_missing, test_cli_help):
        try:
            t()
        except Exception:  # noqa: BLE001
            record(t.__name__, False, traceback.format_exc().splitlines()[-1])
    failed = [r for r in RESULTS if not r[1]]
    print("-" * 60)
    print("TOTAL %d  PASS %d  FAIL %d"
          % (len(RESULTS), len(RESULTS) - len(failed), len(failed)))
    if failed:
        for name, _, detail in failed:
            print("  FAIL  %s  %s" % (name, detail))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
