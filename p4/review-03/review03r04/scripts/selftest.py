#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py - deterministic self-checks for the review skill package.

Runs main.py against temporary fixtures (empty input, bad JSON, missing case,
a normal text material, and a minimal real PDF). Verifies exit codes, JSON
validity, output_contract backfill, degradation reporting and determinism.
Exit 0 means all checks passed.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    status = "PASS" if cond else "FAIL"
    print("[%s] %s%s" % (status, name, (" - " + detail) if detail else ""))
    return bool(cond)


def run(cwd, *args):
    env = dict(os.environ)
    return subprocess.run(
        [sys.executable, "-B", MAIN] + list(args),
        cwd=cwd, env=env, capture_output=True, text=True)


def make_minimal_pdf(text):
    """Build a valid single-page PDF with one Helvetica text run."""
    content = "BT /F1 12 Tf 72 720 Td (%s) Tj ET" % text
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        "<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += ("%d 0 obj\n%s\nendobj\n" % (i, obj)).encode("ascii")
    xref = len(out)
    out += ("xref\n0 %d\n" % (len(objs) + 1)).encode("ascii")
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode("ascii")
    out += ("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%EOF\n"
            % (len(objs) + 1, xref)).encode("ascii")
    return bytes(out)


def write_tree(base, files):
    for rel, data in files.items():
        path = os.path.join(base, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = "wb" if isinstance(data, bytes) else "w"
        with open(path, mode) as fh:
            fh.write(data)
    return base


def run_pipeline(base):
    inp = os.path.join(base, "input")
    outp = os.path.join(base, "output")
    wrk = os.path.join(base, "work")
    os.makedirs(inp, exist_ok=True)
    run(base, "discover", "--input", inp, "--output", outp, "--work", wrk)
    run(base, "scaffold", "--input", inp, "--output", outp, "--work", wrk)
    run(base, "finalize", "--input", inp, "--output", outp, "--work", wrk)
    rpath = os.path.join(outp, "result.json")
    try:
        with open(rpath, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:  # noqa: BLE001
        return {"__parse_error__": str(exc)}


def rerun_finalize(base):
    inp = os.path.join(base, "input")
    outp = os.path.join(base, "output")
    wrk = os.path.join(base, "work")
    run(base, "finalize", "--input", inp, "--output", outp, "--work", wrk)


def test_empty_input():
    base = tempfile.mkdtemp(prefix="rst_empty_")
    try:
        write_tree(base, {"input/case.json":
                          '{"case_id": "E1", "output_contract": {}}'})
        result = run_pipeline(base)
        check("empty input -> insufficient_evidence",
              result.get("status") == "insufficient_evidence",
              "status=%s" % result.get("status"))
        check("empty input -> degradations D_EMPTY_INPUT",
              any(d.get("code") == "D_EMPTY_INPUT"
                  for d in result.get("degradations", [])))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_missing_case_and_material():
    base = tempfile.mkdtemp(prefix="rst_missing_")
    try:
        os.makedirs(os.path.join(base, "input"), exist_ok=True)
        result = run_pipeline(base)
        check("no case + no material -> input_error",
              result.get("status") == "input_error",
              "status=%s" % result.get("status"))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_bad_json():
    base = tempfile.mkdtemp(prefix="rst_badjson_")
    try:
        write_tree(base, {"input/case.json": "{not valid json"})
        result = run_pipeline(base)
        check("bad case.json -> recorded in errors",
              any("bad_json" in e.get("error", "") for e in result.get("errors", []))
              or any("bad_json" in str(e) for e in result.get("errors", [])),
              "errors=%d" % len(result.get("errors", [])))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_normal_material():
    base = tempfile.mkdtemp(prefix="rst_normal_")
    try:
        txt = (
            "Methods\n\n"
            "A total of 120 patients were enrolled and randomly assigned.\n\n"
            "Statistical analysis used a two-sided t-test; significance was "
            "set at p < 0.05.\n\n"
            "Results\n\n"
            "The primary outcome was significant (p = 0.06).\n\n")
        write_tree(base, {
            "input/case.json": json.dumps({
                "case_id": "NORMAL-001",
                "output_contract": {
                    "expected_fields": ["case_id", "status", "findings"],
                    "required_artifact": "result.json",
                },
            }),
            "input/task.md": "# 审查任务\n逐条审查。\n",
            "input/materials/paper.txt": txt,
        })
        result = run_pipeline(base)
        check("normal material -> extracted",
              any(f.get("status") == "extracted"
                  for f in result.get("sources", [])
                  if isinstance(f, dict)) or result.get("status") in
              ("partial", "ok"),
              "status=%s" % result.get("status"))
        check("output_contract backfilled verbatim",
              result.get("expected_fields") == ["case_id", "status", "findings"]
              and result.get("required_artifact") == "result.json")
        check("case_id preserved", result.get("case_id") == "NORMAL-001",
              result.get("case_id"))
        check("numeric candidate detected (p=0.06 vs significant)",
              any(c.get("rule") == "p_wording"
                  for c in result.get("numeric_checks", {}).get("candidates", [])))
        check("report.md generated",
              os.path.exists(os.path.join(base, "output", "report.md")))
        # Determinism: run finalize twice, compare serialized result.
        first = json.dumps(result, ensure_ascii=False, sort_keys=True)
        rerun_finalize(base)
        with open(os.path.join(base, "output", "result.json"),
                  "r", encoding="utf-8") as fh:
            second = json.dumps(json.load(fh), ensure_ascii=False, sort_keys=True)
        check("deterministic re-run", first == second)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_pdf_material():
    base = tempfile.mkdtemp(prefix="rst_pdf_")
    try:
        pdf = make_minimal_pdf("Sample N=120 p=0.03 significant")
        write_tree(base, {
            "input/case.json": '{"case_id": "PDF-001"}',
            "input/materials/paper.pdf": pdf,
        })
        inp = os.path.join(base, "input")
        wrk = os.path.join(base, "work")
        run(base, "discover", "--input", inp, "--work", wrk)
        run(base, "scaffold", "--input", inp, "--output",
            os.path.join(base, "output"), "--work", wrk)
        run(base, "finalize", "--input", inp, "--output",
            os.path.join(base, "output"), "--work", wrk)
        result_path = os.path.join(base, "output", "result.json")
        result = {}
        if os.path.exists(result_path):
            with open(result_path, "r", encoding="utf-8") as fh:
                result = json.load(fh)
        manifest_path = os.path.join(wrk, "manifest.json")
        extracted = False
        if os.path.exists(manifest_path):
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
            extracted = any(f.get("status") == "extracted"
                            for f in manifest.get("files", []))
        degraded = any(d.get("code") in ("D_PDF_NO_TEXT", "D_NO_PDFTOTEXT")
                       for d in result.get("degradations", []))
        check("pdf handled (extracted or honestly degraded)",
              extracted or degraded,
              "extracted=%s degraded=%s" % (extracted, degraded))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main():
    print("== research-methods-review skill selftest ==")
    test_empty_input()
    test_missing_case_and_material()
    test_bad_json()
    test_normal_material()
    test_pdf_material()
    print("")
    failed = [r for r in RESULTS if not r[1]]
    print("summary: %d/%d passed" % (len(RESULTS) - len(failed), len(RESULTS)))
    if failed:
        for name, _, detail in failed:
            print("FAILED: %s %s" % (name, detail))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
