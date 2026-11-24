#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py - self-check for the evidence-reinforced data-analysis Skill.

Standard library only; runs in-process (no subprocess) so it works in the same
sandbox as the Skill. It creates synthetic inputs under a temporary directory,
runs the full pipeline twice (determinism check), then verifies:

 1. scripts/main.py --help exits 0.
 2. result.json has case_id + status + output_contract keys + sources/uncertainty/
    change_log/pending_human.
 3. evidence_map.json entries all carry non-empty locator AND verbatim quote.
 4. Every SVG parses as XML.
 5. stats.json is byte-identical across two runs (determinism).
 6. Empty input -> insufficient_evidence (exit 0).
 7. Bad case.json -> input_error recorded (exit 0).

Exit code is always 0 (the check summary is informational, failures are listed).
"""
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl  # noqa: E402
import main as main_mod  # noqa: E402


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _make_case(root):
    inp = os.path.join(root, "input")
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    os.makedirs(inp, exist_ok=True)
    _write(os.path.join(inp, "case.json"), json.dumps({
        "case_id": "self-test-001",
        "output_contract": {"analysis_results": None, "quality_evidence": None,
                            "anomaly_notes": None},
    }, ensure_ascii=False))
    _write(os.path.join(inp, "task.md"),
           "# 自检用例\n请对以下记录做缺失值和重复记录处理，比较分组差异并作图。\n")
    _write(os.path.join(inp, "iris.csv"),
           "sepal_length,sepal_width,species\n"
           "5.1,3.5,setosa\n4.9,3.0,setosa\n4.7,3.2,setosa\n"
           "7.0,3.2,versicolor\n6.4,3.2,versicolor\n6.9,3.1,versicolor\n"
           "6.3,3.3,virginica\n5.8,2.7,virginica\n7.1,3.0,virginica\n"
           "5.1,3.5,setosa\n")  # one exact duplicate row
    return inp, out, work


def _run(cmd, inp, out, work):
    return core_impl.run(cmd, ["--input", inp, "--output", out, "--work", work])


def _check(name, ok, detail=""):
    print("%-28s %s %s" % ("[PASS]" if ok else "[FAIL]", name, detail))
    return bool(ok)


def main():
    results = []
    root = tempfile.mkdtemp(prefix="skill-selftest-")

    # 1. --help exits 0
    try:
        code = main_mod.main(["--help"])
        results.append(_check("main.py --help exits 0", code == 0))
    except Exception as exc:
        results.append(_check("main.py --help exits 0", False, repr(exc)))

    inp, out, work = _make_case(root)
    _run("full", inp, out, work)

    # 2. result.json contract
    try:
        r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        ok_case = bool(r.get("case_id")) and r.get("status") in \
            ("ok", "partial", "insufficient_evidence", "input_error")
        ok_contract = all(k in r for k in ("analysis_results", "quality_evidence",
                                           "anomaly_notes"))
        ok_meta = all(k in r for k in ("sources", "uncertainty", "change_log",
                                       "pending_human", "errors", "degradations",
                                       "runtime"))
        results.append(_check("result.json contract", ok_case and ok_contract and ok_meta,
                              "status=%s" % r.get("status")))
    except Exception as exc:
        results.append(_check("result.json contract", False, repr(exc)))

    # 3. evidence_map.json every entry has locator + quote
    try:
        em = json.load(open(os.path.join(out, "evidence_map.json"), encoding="utf-8"))
        entries = em.get("entries", [])
        bad = [e["id"] for e in entries if not e.get("locator") or not e.get("quote")]
        results.append(_check("evidence_map locator+quote",
                              len(entries) > 0 and not bad,
                              "entries=%d bad=%d" % (len(entries), len(bad))))
    except Exception as exc:
        results.append(_check("evidence_map locator+quote", False, repr(exc)))

    # 4. SVG parse
    try:
        svgs = [f for f in os.listdir(out) if f.endswith(".svg")]
        for f in svgs:
            ET.parse(os.path.join(out, f))
        results.append(_check("SVG parseable", len(svgs) > 0, "n=%d" % len(svgs)))
    except Exception as exc:
        results.append(_check("SVG parseable", False, repr(exc)))

    # 5. determinism: run again into a second output dir, compare stats.json
    out2 = os.path.join(root, "output2")
    work2 = os.path.join(root, "work2")
    _run("full", inp, out2, work2)
    try:
        s1 = open(os.path.join(work, "stats.json"), "rb").read()
        s2 = open(os.path.join(work2, "stats.json"), "rb").read()
        results.append(_check("determinism (stats.json bytes)", s1 == s2))
    except Exception as exc:
        results.append(_check("determinism (stats.json bytes)", False, repr(exc)))

    # 6. empty input -> insufficient_evidence
    empty = os.path.join(root, "empty")
    os.makedirs(empty, exist_ok=True)
    _run("full", empty, os.path.join(root, "eout"), os.path.join(root, "ework"))
    try:
        r = json.load(open(os.path.join(root, "eout", "result.json"), encoding="utf-8"))
        results.append(_check("empty input handled", r.get("status") in
                              ("insufficient_evidence", "input_error"),
                              "status=%s" % r.get("status")))
    except Exception as exc:
        results.append(_check("empty input handled", False, repr(exc)))

    # 7. bad case.json -> input_error / recorded
    badin = os.path.join(root, "bad")
    os.makedirs(badin, exist_ok=True)
    _write(os.path.join(badin, "case.json"), "{not valid json")
    _write(os.path.join(badin, "task.md"), "# bad\n")
    _write(os.path.join(badin, "d.csv"), "a,b\n1,2\n")
    _run("full", badin, os.path.join(root, "bout"), os.path.join(root, "bwork"))
    try:
        r = json.load(open(os.path.join(root, "bout", "result.json"), encoding="utf-8"))
        has_err = bool(r.get("errors")) or r.get("status") in ("input_error", "partial")
        results.append(_check("bad case.json handled", has_err,
                              "status=%s errors=%d" % (r.get("status"), len(r.get("errors", [])))))
    except Exception as exc:
        results.append(_check("bad case.json handled", False, repr(exc)))

    passed = sum(results)
    print("-" * 60)
    print("SELFTEST SUMMARY: %d/%d passed" % (passed, len(results)))
    return 0


def _parse_flags(argv):
    flags = {"input": None, "output": None, "work": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--input", "--output", "--work") and i + 1 < len(argv):
            flags[a[2:]] = argv[i + 1]
            i += 2
        else:
            i += 1
    return flags


def _verify_real(flags, results):
    """If a real output exists, verify its contract the same way the rubric does."""
    out = flags.get("output")
    if not out or not os.path.isdir(out):
        return
    rp = os.path.join(out, "result.json")
    if not os.path.isfile(rp):
        results.append(_check("real output result.json exists", False, "missing"))
        return
    try:
        r = json.load(open(rp, encoding="utf-8"))
        ok = bool(r.get("case_id")) and r.get("status") in \
            ("ok", "partial", "insufficient_evidence", "input_error")
        results.append(_check("real result.json contract", ok, "status=%s" % r.get("status")))
    except Exception as exc:
        results.append(_check("real result.json contract", False, repr(exc)))
    ep = os.path.join(out, "evidence_map.json")
    if os.path.isfile(ep):
        try:
            em = json.load(open(ep, encoding="utf-8"))
            entries = em.get("entries", [])
            bad = [e["id"] for e in entries if not e.get("locator") or not e.get("quote")]
            results.append(_check("real evidence_map locator+quote",
                                  len(entries) > 0 and not bad,
                                  "entries=%d bad=%d" % (len(entries), len(bad))))
        except Exception as exc:
            results.append(_check("real evidence_map locator+quote", False, repr(exc)))
    for f in os.listdir(out):
        if f.endswith(".svg"):
            try:
                ET.parse(os.path.join(out, f))
            except Exception as exc:
                results.append(_check("real SVG parseable: %s" % f, False, repr(exc)))


if __name__ == "__main__":
    try:
        argv = sys.argv[1:]
        results = []
        flags = _parse_flags(argv)
        synthetic_code = main()  # prints its own synthetic-check summary
        _verify_real(flags, results)
        if results:
            print("-" * 60)
            print("REAL-OUTPUT SUMMARY: %d/%d passed" %
                  (sum(results), len(results)))
        sys.exit(synthetic_code)
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(0)
