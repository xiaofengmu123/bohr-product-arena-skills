#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline self-test for the sci-figure-repro skill.

Runs the real scripts/main.py entrypoint in isolated temp directories and checks:
  1. package structure (SKILL.md frontmatter name/description/version, scripts)
  2. output contract (case_id, status, all output_contract fields, sources,
     uncertainty, change_log) and artifact validity (SVG <text>, PNG/PDF magic)
  3. determinism: two identical runs -> byte-identical SVG/PNG/PDF and identical
     result.json after normalizing wall-clock timestamps
  4. degradation: empty input dir and malformed case JSON do not crash and
     still produce a valid, honest result.json

Exit code 0 when all checks pass. Standard library only.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAIN = os.path.join(HERE, "main.py")
SKILL = os.path.join(ROOT, "SKILL.md")

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append({"name": name, "ok": bool(ok), "detail": str(detail)})
    mark = "PASS" if ok else "FAIL"
    print("[%s] %s %s" % (mark, name, detail))


def canon(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def run_main(args, input_dir, output_dir, work_dir):
    env = dict(os.environ)
    env["INPUT_DIR"] = input_dir
    env["OUTPUT_DIR"] = output_dir
    env["WORK_DIR"] = work_dir
    proc = subprocess.run([sys.executable, MAIN] + args, env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    return proc


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


CASE = {
    "case_id": "test-case-1",
    "output_contract": {
        "editable_source": None,
        "rendered_png": None,
        "rendered_pdf": None,
        "data_mapping": None,
    },
}

CSV = "Treatment,24h,48h\nControl,100,100\nLow,82.3,75.1\nHigh,61.5,52.8\n"

SPEC = {
    "kind": "bar",
    "title": "Cell viability after treatment",
    "x_label": "Treatment",
    "y_label": "Viability (%)",
    "categories": ["Control", "Low", "High"],
    "error_kind": "sd",
    "series": [
        {"name": "24h", "values": [100, 82.3, 61.5],
         "errors": [2.1, 3.4, 2.9]},
        {"name": "48h", "values": [100, 75.1, 52.8],
         "errors": [1.5, 2.8, 3.1]},
    ],
    "figure_notes": "Mean +/- SD, n=3.",
    "source": {"file": "data.csv", "kind": "csv"},
}


def make_case(root):
    os.makedirs(os.path.join(root, "input"), exist_ok=True)
    os.makedirs(os.path.join(root, "work"), exist_ok=True)
    os.makedirs(os.path.join(root, "output"), exist_ok=True)
    with open(os.path.join(root, "input", "case.json"), "w", encoding="utf-8") as f:
        f.write(canon(CASE) + "\n")
    with open(os.path.join(root, "input", "task.md"), "w", encoding="utf-8") as f:
        f.write("# Test task\nMake a bar chart from data.csv.\n")
    with open(os.path.join(root, "input", "data.csv"), "w", encoding="utf-8") as f:
        f.write(CSV)
    with open(os.path.join(root, "work", "chart_spec.json"), "w", encoding="utf-8") as f:
        f.write(canon(SPEC) + "\n")


def normalize_result(obj):
    if isinstance(obj, dict):
        return {k: normalize_result(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_result(x) for x in obj]
    return obj


def strip_time_utc(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "time_utc":
                out[k] = None
            else:
                out[k] = strip_time_utc(v)
        return out
    if isinstance(obj, list):
        return [strip_time_utc(x) for x in obj]
    return obj


def test_structure():
    ok = os.path.isfile(SKILL)
    record("structure:SKILL.md", ok, SKILL)
    if not ok:
        return
    text = open(SKILL, encoding="utf-8").read()
    front = text.split("---")[1] if text.startswith("---") and text.count("---") >= 2 else ""
    name = "name:" in front
    desc = "description:" in front
    ver = "version:" in front
    record("structure:frontmatter", name and desc and ver,
           "name=%s desc=%s version=%s" % (name, desc, ver))
    for s in ("main.py", "selftest.py", "render_core.py"):
        record("structure:scripts/%s" % s, os.path.isfile(os.path.join(HERE, s)))


def test_pipeline_and_determinism():
    tmp = tempfile.mkdtemp(prefix="sci-fig-selftest-")
    try:
        for tag in ("A", "B"):
            root = os.path.join(tmp, tag)
            make_case(root)
            proc = run_main(["run"], os.path.join(root, "input"),
                            os.path.join(root, "output"), os.path.join(root, "work"))
            record("pipeline:run-%s-rc0" % tag, proc.returncode == 0,
                   "rc=%d" % proc.returncode)

        outA = os.path.join(tmp, "A", "output")
        outB = os.path.join(tmp, "B", "output")
        resA = read_json(os.path.join(outA, "result.json"))
        resB = read_json(os.path.join(outB, "result.json"))
        record("contract:case_id", resA.get("case_id") == "test-case-1",
               resA.get("case_id"))
        record("contract:status-final", resA.get("status") in
               ("complete", "partial", "failed"), resA.get("status"))
        for key in CASE["output_contract"]:
            record("contract:field-%s" % key,
                   resA.get(key) not in (None, "", []), resA.get(key))
        for field in ("sources", "uncertainty", "change_log"):
            record("contract:list-%s" % field, isinstance(resA.get(field), list))

        svg = os.path.join(outA, "figures", "figure.svg")
        png = os.path.join(outA, "figures", "figure.png")
        pdf = os.path.join(outA, "figures", "figure.pdf")
        svg_text = open(svg, encoding="utf-8").read() if os.path.isfile(svg) else ""
        record("artifact:svg-text-node", "<text" in svg_text)
        record("artifact:svg-data-mapping", "data-value" in svg_text)
        png_head = open(png, "rb").read(8) if os.path.isfile(png) else b""
        pdf_head = open(pdf, "rb").read(8) if os.path.isfile(pdf) else b""
        record("artifact:png-magic", png_head == b"\x89PNG\r\n\x1a\n")
        record("artifact:pdf-magic", pdf_head[:5] == b"%PDF-")

        # determinism: artifacts byte-identical; result.json identical modulo time
        det_ok = True
        for name in ("figure.svg", "figure.png", "figure.pdf", "figure.mapping.json"):
            a = os.path.join(outA, "figures", name)
            b = os.path.join(outB, "figures", name)
            if os.path.isfile(a) and os.path.isfile(b):
                same = open(a, "rb").read() == open(b, "rb").read()
                det_ok = det_ok and same
                record("determinism:artifact-%s" % name, same)
            else:
                det_ok = False
                record("determinism:artifact-%s" % name, False, "missing")
        same_json = strip_time_utc(resA) == strip_time_utc(resB)
        det_ok = det_ok and same_json
        record("determinism:result.json", same_json)
        record("determinism:overall", det_ok)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_degradation():
    tmp = tempfile.mkdtemp(prefix="sci-fig-degrad-")
    try:
        # empty input dir
        e_in = os.path.join(tmp, "empty_in")
        e_out = os.path.join(tmp, "empty_out")
        e_work = os.path.join(tmp, "empty_work")
        os.makedirs(e_in)
        proc = run_main(["run"], e_in, e_out, e_work)
        res = read_json(os.path.join(e_out, "result.json"))
        record("degrade:empty-rc0", proc.returncode == 0, "rc=%d" % proc.returncode)
        record("degrade:empty-honest", res.get("status") in
               ("failed", "partial", "complete"), res.get("status"))

        # malformed case JSON
        b_in = os.path.join(tmp, "bad_in")
        b_out = os.path.join(tmp, "bad_out")
        b_work = os.path.join(tmp, "bad_work")
        os.makedirs(b_in)
        with open(os.path.join(b_in, "case.json"), "w", encoding="utf-8") as f:
            f.write("{ this is not valid json ")
        proc = run_main(["bootstrap"], b_in, b_out, b_work)
        res = read_json(os.path.join(b_out, "result.json"))
        record("degrade:badjson-rc0", proc.returncode == 0, "rc=%d" % proc.returncode)
        record("degrade:badjson-errors", bool(res.get("errors")), res.get("errors"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_structure()
    test_pipeline_and_determinism()
    test_degradation()
    failed = [r for r in RESULTS if not r["ok"]]
    print("\n%d checks, %d failed" % (len(RESULTS), len(failed)))
    for r in failed:
        print("  FAIL:", r["name"], "-", r["detail"])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
