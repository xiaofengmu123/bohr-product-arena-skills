# -*- coding: utf-8 -*-
"""Self-test suite for academic-polish-03.

Builds synthetic fixtures inside a temporary directory (no pre-written answers)
and exercises the full scaffold / verify / finalize / selfcheck pipeline.

Asserts:
  1. empty input       -> insufficient_evidence, exit-safe (no crash)
  2. bad JSON          -> degraded but keeps a valid result
  3. bad encoding      -> degraded but keeps a valid result
  4. normal input      -> status ok; two finalize runs are byte-identical
  5. order independence-> reversed unit order yields identical canonical result
  6. mutation          -> fabricated number flagged as measurement_mismatch
  7. hedge             -> removed hedge without note -> warning; with note -> resolved
  8. CLI               -> --help exits 0

Exits non-zero on any failure. Standard library only; no network.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import main as M  # noqa: E402

SAMPLE_DRAFT = (
    "Cellular uptake of the conjugate reached 32.4 +/- 1.2 ug/mL within 6 h "
    "(p < 0.05, Fig. 1). These results may indicate that internalization is "
    "partially dependent on clathrin-mediated endocytosis [1, 12-14]."
)

PASS, FAIL = 0, 0


def report(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("PASS  %s" % name)
    else:
        FAIL += 1
        print("FAIL  %s  %s" % (name, detail))


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def make_ok_input(base):
    d = os.path.join(base, "ok_input")
    os.makedirs(os.path.join(d, "materials"), exist_ok=True)
    write(os.path.join(d, "case.json"), json.dumps({
        "id": "case-ok-1",
        "output_contract": {
            "deliverable": None,
            "fact_source_map": [],
            "version_boundary": [],
            "custom_key": "keep-me",
        },
    }, ensure_ascii=False))
    write(os.path.join(d, "task.md"),
          "# 任务\n润色英文摘要，保持限定强度、数据与单位。genre=journal, target_lang=en")
    write(os.path.join(d, "materials", "draft.md"), SAMPLE_DRAFT)
    return d


def fill_edits(work, full_revised=None, mutate_number=False,
               remove_hedge=False, hedge_note=False):
    edits = json.load(open(os.path.join(work, "edits.json"), encoding="utf-8"))
    for u in edits["units"]:
        rev = u["original"]
        if mutate_number and "32.4" in rev:
            rev = rev.replace("32.4", "32.0")
        if remove_hedge and "may indicate" in rev:
            rev = rev.replace("may indicate", "indicates")
        u["revised"] = rev
        u["edit_type"] = "no_change" if rev == u["original"] else "grammar"
        if any(c in u["original"] for c in ("32.4", "[1", "Fig.")):
            u["evidence"] = {"locator": "materials/draft.md#p1",
                             "quote": u["original"]}
        if hedge_note:
            u["hedge_note"] = "限定词已按原文保留，未加强/削弱结论"
    edits["full_revised"] = SAMPLE_DRAFT if full_revised is None else full_revised
    edits["fact_source_map"] = [
        {"fact": "32.4 ug/mL", "locator": "materials/draft.md#p1",
         "quote": "32.4 +/- 1.2 ug/mL"},
        {"fact": "Fig. 1", "locator": "materials/draft.md#p1", "quote": "Fig. 1"},
        {"fact": "[1, 12-14]", "locator": "materials/draft.md#p1",
         "quote": "[1, 12-14]"},
    ]
    with open(os.path.join(work, "edits.json"), "w", encoding="utf-8") as f:
        json.dump(edits, f, ensure_ascii=False, indent=2)


def canonical_result(out):
    return M.canonical(M.strip_volatile(
        json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))))


def test_empty(base):
    d = os.path.join(base, "empty_input")
    os.makedirs(d, exist_ok=True)
    out, work = os.path.join(base, "empty_out"), os.path.join(base, "empty_work")
    r = M.scaffold(d, out, work)
    ok = r.get("status") == "insufficient_evidence" or r.get("status") == "input_error"
    report("empty input -> honest status", ok, "status=%s" % r.get("status"))
    ok2 = os.path.isfile(os.path.join(out, "result.json"))
    report("empty input -> result.json exists", ok2)


def test_bad_json(base):
    d = os.path.join(base, "badjson_input")
    os.makedirs(os.path.join(d, "materials"), exist_ok=True)
    write(os.path.join(d, "case.json"), "{ this is not json")
    write(os.path.join(d, "materials", "draft.md"), SAMPLE_DRAFT)
    out, work = os.path.join(base, "badjson_out"), os.path.join(base, "badjson_work")
    r = M.scaffold(d, out, work)
    errs = json.load(open(os.path.join(work, "evidence_index.json"),
                          encoding="utf-8")).get("errors") or []
    report("bad JSON -> degraded, recorded", bool(errs), "errors=%r" % errs)
    report("bad JSON -> valid result.json",
           os.path.isfile(os.path.join(out, "result.json")))


def test_bad_encoding(base):
    d = os.path.join(base, "badenc_input")
    os.makedirs(os.path.join(d, "materials"), exist_ok=True)
    write(os.path.join(d, "case.json"),
          json.dumps({"id": "case-badenc", "text": ""}))
    raw = "draft with latin-1 bytes: caf\xe9 \xe4\xf6\xfc".encode("latin-1")
    with open(os.path.join(d, "materials", "draft.md"), "wb") as f:
        f.write(raw)
    out, work = os.path.join(base, "badenc_out"), os.path.join(base, "badenc_work")
    r = M.scaffold(d, out, work)
    report("bad encoding -> no crash",
           r.get("status") in ("partial", "insufficient_evidence", "input_error"),
           "status=%s" % r.get("status"))


def test_normal_and_determinism(base):
    inp = make_ok_input(base)
    out1, work = os.path.join(base, "ok_out1"), os.path.join(base, "ok_work")
    M.scaffold(inp, out1, work)
    fill_edits(work)
    rep = M.verify(work)
    ok_status = rep["stats"]["errors"] == 0
    report("normal input -> no conservation errors", ok_status,
           "errors=%d" % rep["stats"]["errors"])
    M.finalize(work, out1)
    r1 = json.load(open(os.path.join(out1, "result.json"), encoding="utf-8"))
    report("normal input -> status ok", r1.get("status") == "ok",
           "status=%s" % r1.get("status"))
    report("normal input -> manuscript exists",
           os.path.isfile(os.path.join(out1, "manuscript.md")))
    report("contract custom_key preserved", r1.get("custom_key") == "keep-me")

    out2 = os.path.join(base, "ok_out2")
    M.finalize(work, out2)
    report("determinism -> two finalize runs identical",
           canonical_result(out1) == canonical_result(out2))
    b1 = open(os.path.join(out1, "result.json"), "rb").read()
    b2 = open(os.path.join(out2, "result.json"), "rb").read()
    report("determinism -> result.json byte-identical", b1 == b2)

    # order independence: reverse unit order, finalize again
    edits = json.load(open(os.path.join(work, "edits.json"), encoding="utf-8"))
    edits["units"] = list(reversed(edits["units"]))
    with open(os.path.join(work, "edits.json"), "w", encoding="utf-8") as f:
        json.dump(edits, f, ensure_ascii=False, indent=2)
    M.verify(work)
    out3 = os.path.join(base, "ok_out3")
    M.finalize(work, out3)
    report("determinism -> order-independent result",
           canonical_result(out1) == canonical_result(out3))


def test_mutation(base):
    inp = make_ok_input(base)
    out, work = os.path.join(base, "mut_out"), os.path.join(base, "mut_work")
    M.scaffold(inp, out, work)
    fill_edits(work, mutate_number=True)
    rep = M.verify(work)
    codes = [e.get("code") for e in rep["errors"]]
    report("mutation -> fabricated number flagged",
           "measurement_mismatch" in codes, "codes=%r" % codes)


def test_hedge(base):
    inp = make_ok_input(base)
    work = os.path.join(base, "hedge_work")
    M.scaffold(inp, os.path.join(base, "hedge_out"), work)
    fill_edits(work, remove_hedge=True, hedge_note=False)
    rep = M.verify(work)
    codes = [w.get("code") for w in rep["warnings"]]
    report("hedge -> removal without note warns", "hedge_removed" in codes,
           "warnings=%r" % codes)

    work2 = os.path.join(base, "hedge_work2")
    M.scaffold(inp, os.path.join(base, "hedge_out2"), work2)
    fill_edits(work2, remove_hedge=True, hedge_note=True)
    rep2 = M.verify(work2)
    codes2 = [w.get("code") for w in rep2["warnings"]]
    resolved = [w.get("code") for w in rep2["resolved"]]
    report("hedge -> note resolves warning",
           "hedge_removed" not in codes2 and "hedge_removed" in resolved,
           "warnings=%r resolved=%r" % (codes2, resolved))


def test_cli(base):
    try:
        subprocess.run([sys.executable, "-B", os.path.join(HERE, "main.py"),
                        "--help"], check=True, capture_output=True)
        report("CLI -> --help exits 0", True)
    except Exception as e:
        report("CLI -> --help exits 0", False, repr(e))


def main():
    base = tempfile.mkdtemp(prefix="acadpolish_selftest_")
    try:
        test_empty(base)
        test_bad_json(base)
        test_bad_encoding(base)
        test_normal_and_determinism(base)
        test_mutation(base)
        test_hedge(base)
        test_cli(base)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print("\nselftest summary: %d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
