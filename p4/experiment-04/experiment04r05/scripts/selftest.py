"""selftest.py - full self-test for the sci-plan-comparison-v2 skill.

Standard library only. Runs CLI, robustness, artifact and determinism checks in
isolated temp directories and exits nonzero on any failure. Outputs marked as
"demo/示例" are synthetic and never pretend to be real case results.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import core  # noqa: E402

PASS = []
FAIL = []


def check(name, condition, detail=""):
    if condition:
        PASS.append(name)
        print("  PASS  {}".format(name))
    else:
        FAIL.append(name)
        print("  FAIL  {}  {}".format(name, detail))


def _rmtree(path):
    if path and os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


def strip_volatile(obj):
    """Remove timestamp/seq fields so two runs can be compared deterministically."""
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if key in ("time", "started", "finished", "elapsed_hint"):
                continue
            out[key] = strip_volatile(value)
        return out
    if isinstance(obj, list):
        return [strip_volatile(v) for v in obj]
    return obj


def test_cli_help():
    print("[cli] --help")
    proc = subprocess.run(
        [sys.executable, "-B", os.path.join(HERE, "main.py"), "--help"],
        capture_output=True, text=True, timeout=60,
    )
    check("main.py --help exits 0", proc.returncode == 0, "rc={}".format(proc.returncode))
    out = proc.stdout + proc.stderr
    for sub in ("inspect", "assemble", "validate", "demo"):
        check("help mentions '{}'".format(sub), sub in out, out[:200])


def test_stdlib_only():
    print("[stdlib] no third-party imports")
    third_party = []
    for root, _dirs, files in os.walk(os.path.join(ROOT, "scripts")):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
            for match in re.finditer(r"^\s*(?:import|from)\s+([A-Za-z0-9_\.]+)", text, re.M):
                mod = match.group(1).split(".")[0]
                if mod not in ("__future__",) and mod not in sys.stdlib_module_names:
                    if mod not in ("core",):
                        third_party.append("{}:{}".format(name, mod))
    check("only stdlib imports", not third_party, "; ".join(sorted(set(third_party))))


def test_empty_input():
    print("[robust] empty input dir")
    tmp = tempfile.mkdtemp(prefix="sps_selftest_empty_")
    try:
        inp = os.path.join(tmp, "input")
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        os.makedirs(inp)
        result, _pack = core.run_inspect(inp, out, work)
        check("empty input -> status=input_error", result["status"] == core.STATUS_INPUT_ERROR,
              "status={}".format(result["status"]))
        check("empty input -> result.json written", os.path.isfile(os.path.join(out, "result.json")))
    finally:
        _rmtree(tmp)


def test_bad_json():
    print("[robust] bad JSON")
    tmp = tempfile.mkdtemp(prefix="sps_selftest_badjson_")
    try:
        inp = os.path.join(tmp, "input")
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ this is not json")
        result, _pack = core.run_inspect(inp, out, work)
        check("bad JSON -> status=input_error", result["status"] == core.STATUS_INPUT_ERROR,
              "status={}".format(result["status"]))
        check("bad JSON -> error recorded", any(e.get("kind") == "bad_json" for e in result["errors"]),
              "errors={}".format(result["errors"][:1]))
        check("bad JSON -> no exception", os.path.isfile(os.path.join(out, "result.json")))
    finally:
        _rmtree(tmp)


def test_demo_and_artifacts():
    print("[pipeline] demo + artifacts")
    tmp = tempfile.mkdtemp(prefix="sps_selftest_demo_")
    try:
        final = core.demo(tmp)
        out_json = os.path.join(tmp, "result.json")
        check("demo -> result.json exists", os.path.isfile(out_json))
        with open(out_json, "r", encoding="utf-8") as fh:
            result = json.load(fh)
        check("demo -> status valid", result.get("status") in core.STATUSES,
              "status={}".format(result.get("status")))
        check("demo -> contract_alignment present", isinstance(result.get("contract_alignment"), dict))
        alignment = result.get("contract_alignment", {})
        check("demo -> all contract fields filled",
              all(a.get("filled") for a in alignment.values()),
              str(alignment))
        check("demo -> change_log appended", len(result.get("change_log", [])) >= 2,
              "len={}".format(len(result.get("change_log", []))))
        check("demo -> chosen resolved", result.get("selection", {}).get("chosen") is not None)

        svg = os.path.join(tmp, "comparison_matrix.svg")
        csv_path = os.path.join(tmp, "comparison_matrix.csv")
        pdf = os.path.join(tmp, "comparison_matrix.pdf")
        md = os.path.join(tmp, "plan_comparison.md")
        check("SVG exists", os.path.isfile(svg))
        if os.path.isfile(svg):
            try:
                ET.parse(svg)
                check("SVG is XML-parseable", True)
            except Exception as exc:  # noqa: BLE001
                check("SVG is XML-parseable", False, str(exc))
        check("CSV exists", os.path.isfile(csv_path))
        if os.path.isfile(csv_path):
            with open(csv_path, "rb") as fh:
                head = fh.read(3)
            check("CSV has UTF-8 BOM", head == b"\xef\xbb\xbf", "head={!r}".format(head))
        check("MD exists", os.path.isfile(md))
        check("PDF exists", os.path.isfile(pdf))
        if os.path.isfile(pdf):
            with open(pdf, "rb") as fh:
                raw = fh.read()
            check("PDF starts with %PDF-", raw.startswith(b"%PDF-"))
            check("PDF ends with %%EOF", raw.rstrip().endswith(b"%%EOF"))
        valid, issues, _r = core.validate_result(out_json)
        check("demo -> validate valid", valid, "; ".join(issues[:5]))
        return result
    finally:
        _rmtree(tmp)


def test_determinism():
    print("[determinism] two runs identical (modulo timestamps)")
    d1 = tempfile.mkdtemp(prefix="sps_det1_")
    d2 = tempfile.mkdtemp(prefix="sps_det2_")
    try:
        core.demo(d1)
        core.demo(d2)
        with open(os.path.join(d1, "result.json"), "r", encoding="utf-8") as fh:
            r1 = json.load(fh)
        with open(os.path.join(d2, "result.json"), "r", encoding="utf-8") as fh:
            r2 = json.load(fh)
        check("two runs -> identical normalized result",
              strip_volatile(r1) == strip_volatile(r2))
    finally:
        _rmtree(d1)
        _rmtree(d2)


def main():
    print("sci-plan-comparison-v2 selftest")
    test_stdlib_only()
    test_cli_help()
    test_empty_input()
    test_bad_json()
    test_demo_and_artifacts()
    test_determinism()
    print("")
    print("passed={} failed={}".format(len(PASS), len(FAIL)))
    if FAIL:
        print("FAILED:")
        for name in FAIL:
            print("  - {}".format(name))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
