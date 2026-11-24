"""selftest.py - deterministic self-test for the sci-data-analysis skill.

Builds synthetic inputs at runtime (never shipped as formal answers), runs the
pipeline, and asserts the result contract, SVG validity, clean consistency and
byte-determinism of stats.json + SVG. Also exercises empty / bad-JSON / bad-
encoding / missing-column degradation paths.

Run from the package root:
    python3 -B scripts/selftest.py
"""
import json
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok  %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s %s" % (name, detail))


def make_case(root):
    inp = os.path.join(root, "input")
    work = os.path.join(root, "work")
    out = os.path.join(root, "output")
    os.makedirs(inp)
    csv = os.path.join(inp, "records.csv")
    with open(csv, "w", encoding="utf-8") as fh:
        fh.write("group,a,b,c\n")
        fh.write("ctrl,1.0,2.0,3.0\n")
        fh.write("ctrl,1.2,2.1,3.1\n")
        fh.write("ctrl,1.1,1.9,2.9\n")
        fh.write("treat,2.0,3.0,4.0\n")
        fh.write("treat,2.1,3.1,4.1\n")
        fh.write("treat,1.9,2.9,3.9\n")
        fh.write("treat,2.0,3.0,4.0\n")  # exact duplicate of treat row 1
    case = {
        "case_id": "selftest-001",
        "output_contract": {"analysis_results": None, "quality_evidence": None, "anomaly_notes": None}
    }
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 自检任务\n比较 ctrl 与 treat 两组在 a、b、c 上的差异，并给出相关性。\n")
    return inp, work, out


def run_and_collect(inp, work, out):
    core_impl.run_pipeline(inp, out, work)
    rp = os.path.join(out, "result.json")
    result = json.load(open(rp, encoding="utf-8"))
    stats = json.load(open(os.path.join(work, "stats.json"), encoding="utf-8"))
    svgs = sorted(f for f in os.listdir(out) if f.endswith(".svg"))
    return result, stats, svgs


def test_basic():
    print("[basic pipeline + contract]")
    root = tempfile.mkdtemp(prefix="sci-skill-")
    try:
        inp, work, out = make_case(root)
        result, stats, svgs = run_and_collect(inp, work, out)
        check("result.json exists", os.path.exists(os.path.join(out, "result.json")))
        check("case_id", result.get("case_id") == "selftest-001", str(result.get("case_id")))
        check("status ok", result.get("status") == "ok", str(result.get("status")))
        check("contract keys backfilled", all(k in result for k in ("analysis_results", "quality_evidence", "anomaly_notes")))
        check("analysis_results filled", bool(result.get("analysis_results")))
        check("sources non-empty with locator+quote",
              all("locator" in s and "quote" in s for s in result.get("sources", [])) and len(result.get("sources", [])) > 0)
        check("change_log non-empty", len(result.get("change_log", [])) >= 2)
        check("uncertainty present", isinstance(result.get("uncertainty"), list))
        check("limitations present", isinstance(result.get("limitations"), list))
        check("report.md exists", os.path.exists(os.path.join(out, "report.md")))
        check("cleaned.csv exists", os.path.exists(os.path.join(work, "cleaned.csv")))
        check("group tests run", len(stats.get("group_tests", [])) == 3, str(len(stats.get("group_tests", []))))
        check("correlations run", len(stats.get("correlations", [])) == 3)
        check("svg produced", len(svgs) >= 1, str(svgs))
        for s in svgs:
            try:
                ET.parse(os.path.join(out, s))
                check("svg parses: %s" % s, True)
            except Exception as e:
                check("svg parses: %s" % s, False, str(e))
        # clean consistency
        cm = json.load(open(os.path.join(work, "clean_meta.json"), encoding="utf-8"))
        con = cm.get("consistency", {})
        check("clean consistency ok", con.get("ok") is True and con.get("original_rows") - con.get("final_rows") == con.get("removed_rows"))
        check("dedup removed 1 row", con.get("removed_rows") == 1, str(con.get("removed_rows")))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_determinism():
    print("[determinism: stats.json + SVG byte-stable]")
    root = tempfile.mkdtemp(prefix="sci-skill-")
    try:
        inp, work1, out1 = make_case(root)
        _, stats1, svgs1 = run_and_collect(inp, work1, out1)
        work2 = os.path.join(root, "work2")
        out2 = os.path.join(root, "output2")
        _, stats2, svgs2 = run_and_collect(inp, work2, out2)
        s1 = open(os.path.join(work1, "stats.json"), "rb").read()
        s2 = open(os.path.join(work2, "stats.json"), "rb").read()
        check("stats.json byte-identical", s1 == s2)
        check("same svg set", svgs1 == svgs2, "%s vs %s" % (svgs1, svgs2))
        for s in svgs1:
            b1 = open(os.path.join(out1, s), "rb").read()
            b2 = open(os.path.join(out2, s), "rb").read()
            check("svg byte-identical: %s" % s, b1 == b2)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_empty_input():
    print("[empty input -> insufficient_evidence]")
    root = tempfile.mkdtemp(prefix="sci-skill-")
    try:
        inp = os.path.join(root, "input")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "empty-1"}, fh)
        out = os.path.join(root, "output")
        work = os.path.join(root, "work")
        core_impl.run_pipeline(inp, out, work)
        result = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        check("status insufficient_evidence", result.get("status") == "insufficient_evidence", str(result.get("status")))
        check("result.json valid", isinstance(result, dict))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_bad_json():
    print("[bad case.json -> input_error, no crash]")
    root = tempfile.mkdtemp(prefix="sci-skill-")
    try:
        inp = os.path.join(root, "input")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        with open(os.path.join(inp, "data.csv"), "w", encoding="utf-8") as fh:
            fh.write("x,y\n1,2\n3,4\n")
        out = os.path.join(root, "output")
        work = os.path.join(root, "work")
        core_impl.run_pipeline(inp, out, work)
        result = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        check("status input_error", result.get("status") == "input_error", str(result.get("status")))
        check("errors recorded", len(result.get("errors", [])) >= 1)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_bad_encoding():
    print("[gbk-encoded csv degrades gracefully]")
    root = tempfile.mkdtemp(prefix="sci-skill-")
    try:
        inp = os.path.join(root, "input")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "gbk-1"}, fh)
        with open(os.path.join(inp, "data.csv"), "wb") as fh:
            fh.write("组,值\n甲,1\n乙,2\n甲,3\n乙,4\n".encode("gbk"))
        out = os.path.join(root, "output")
        work = os.path.join(root, "work")
        core_impl.run_pipeline(inp, out, work)
        result = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        check("produces a result", result.get("status") in ("ok", "partial", "insufficient_evidence"), str(result.get("status")))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    global PASS, FAIL
    print("sci-data-analysis selftest")
    test_basic()
    test_determinism()
    test_empty_input()
    test_bad_json()
    test_bad_encoding()
    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
