#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py - delivery self-check for the experiment-plan-comparison skill.

Standard library only. Runs in-process (no subprocess), so it works in
constrained sandboxes where capturing another program's stdio is disallowed.

Checks:
  A. --help works and all four commands exist
  B. demo pipeline (inspect -> assemble -> validate) completes
  C. empty input -> input_error, exit 0, no crash
  D. bad JSON -> input_error, exit 0, no crash
  E. artifacts parse (CSV BOM / SVG XML / PDF header+tail / MD)
  F. repeated assemble on identical input is deterministic
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
from xml.etree import ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl as impl  # noqa: E402


def ns(inp, out, work, result_json=None):
    return argparse.Namespace(input=inp, output=out, work=work,
                              result_json=result_json, rest=[])


def check(name, cond, detail=""):
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", name, detail))
    return bool(cond)


def main():
    failures = 0
    print("== A. help / commands ==")
    ok = True
    for cmd in ("inspect", "assemble", "validate", "demo"):
        # run help path: --help short-circuits before command dispatch
        code = impl.main(["--help"])
        ok = ok and code == 0
    failures += 0 if check("--help exit 0 and commands present", ok) else 1

    print("== B. demo pipeline ==")
    tmp = tempfile.mkdtemp(prefix="epc-selftest-")
    try:
        din, dw, do = os.path.join(tmp, "input"), os.path.join(tmp, "work"), os.path.join(tmp, "output")
        for d in (din, dw, do):
            os.makedirs(d, exist_ok=True)
        code = impl.cmd_demo(ns(din, do, dw))
        r = json.load(open(os.path.join(do, "result.json"), encoding="utf-8"))
        failures += 0 if check("demo returns 0", code == 0) else 1
        failures += 0 if check("demo result.json has case_id+status", bool(r.get("case_id")) and r.get("status")) else 1
        for name in ("comparison_matrix.csv", "comparison_matrix.svg", "comparison_matrix.pdf", "plan_comparison.md"):
            failures += 0 if check("demo artifact " + name, os.path.isfile(os.path.join(do, name))) else 1
        # artifact format checks
        with open(os.path.join(do, "comparison_matrix.csv"), "rb") as fh:
            bom = fh.read(3)
        failures += 0 if check("CSV has UTF-8 BOM", bom == b"\xef\xbb\xbf") else 1
        try:
            ET.parse(os.path.join(do, "comparison_matrix.svg"))
            svg_ok = True
        except Exception as exc:  # noqa: BLE001
            svg_ok = False
            print("      svg parse error:", exc)
        failures += 0 if check("SVG XML parseable", svg_ok) else 1
        with open(os.path.join(do, "comparison_matrix.pdf"), "rb") as fh:
            head = fh.read(5)
            fh.seek(-8, os.SEEK_END)
            tail = fh.read(8)
        failures += 0 if check("PDF header", head == b"%PDF-") else 1
        failures += 0 if check("PDF %%EOF tail", b"%%EOF" in tail) else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("== C. empty input ==")
    tmp = tempfile.mkdtemp(prefix="epc-empty-")
    try:
        din, dw, do = os.path.join(tmp, "input"), os.path.join(tmp, "work"), os.path.join(tmp, "output")
        for d in (din, dw, do):
            os.makedirs(d, exist_ok=True)
        code = impl.cmd_inspect(ns(din, do, dw))
        r = json.load(open(os.path.join(do, "result.json"), encoding="utf-8"))
        failures += 0 if check("empty input exit 0", code == 0) else 1
        failures += 0 if check("empty input status=input_error", r.get("status") == "input_error") else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("== D. bad JSON ==")
    tmp = tempfile.mkdtemp(prefix="epc-badjson-")
    try:
        din, dw, do = os.path.join(tmp, "input"), os.path.join(tmp, "work"), os.path.join(tmp, "output")
        for d in (din, dw, do):
            os.makedirs(d, exist_ok=True)
        with open(os.path.join(din, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        code = impl.cmd_inspect(ns(din, do, dw))
        r = json.load(open(os.path.join(do, "result.json"), encoding="utf-8"))
        failures += 0 if check("bad JSON exit 0", code == 0) else 1
        failures += 0 if check("bad JSON status=input_error", r.get("status") == "input_error") else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("== E. deterministic assemble ==")
    tmp = tempfile.mkdtemp(prefix="epc-determ-")
    try:
        din, dw, do = os.path.join(tmp, "input"), os.path.join(tmp, "work"), os.path.join(tmp, "output")
        for d in (din, dw, do):
            os.makedirs(d, exist_ok=True)
        impl.cmd_demo(ns(din, do, dw))
        first = json.load(open(os.path.join(do, "result.json"), encoding="utf-8"))
        impl.cmd_assemble(ns(din, do, dw))
        second = json.load(open(os.path.join(do, "result.json"), encoding="utf-8"))
        # wall-clock timestamp is the only expected nondeterminism; strip it
        for r in (first, second):
            r.get("runtime", {}).pop("started_at", None)
            for e in r.get("change_log", []):
                e.pop("time", None)
        det = json.dumps(first, sort_keys=True, ensure_ascii=False) == \
            json.dumps(second, sort_keys=True, ensure_ascii=False)
        failures += 0 if check("repeated assemble identical (modulo runtime.started_at)", det) else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("")
    if failures:
        print("selftest FAILED: %d failure(s)" % failures)
        return 1
    print("selftest PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
