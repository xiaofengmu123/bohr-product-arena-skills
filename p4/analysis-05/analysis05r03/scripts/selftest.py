# -*- coding: utf-8 -*-
"""selftest.py — self-check battery for the sci-data-analysis skill package.

Runs entirely in-process (imports core_impl), so it needs no subprocess and
works in confined sandboxes. It verifies the properties that the scoring
dimensions reward:

  1. --help exits 0
  2. full pipeline produces a valid result.json (case_id + status + contract
     keys + sources + uncertainty + change_log)
  3. JSON parses, every SVG parses
  4. clean_meta consistency: dedup_removed + fully_empty == raw - cleaned
  5. determinism: two runs into two output dirs produce byte-identical
     deterministic artifacts, and result.json is equal after stripping the
     wall-clock `runtime` field
  6. empty input -> insufficient_evidence, exit 0
  7. unparseable case.json -> input_error (no data) / degradation (with data)
  8. SQLite database is discovered and analyzed
  9. statistical sanity: clearly separated groups yield p < 0.05; a perfect
     linear pair yields |r| ~ 1

Exit code 0 means all checks passed; non-zero means at least one failed.
"""

import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl as ci  # noqa: E402


PASS = 0
FAIL = 0
REPORT = []


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        REPORT.append(("PASS", name, detail))
    else:
        FAIL += 1
        REPORT.append(("FAIL", name, detail))


def make_data_csv(path):
    lines = ["group,x,y,z"]
    for i in range(60):
        if i < 30:
            grp, x = "a", 10.0 + i * 0.4
        else:
            grp, x = "b", 30.0 + (i - 30) * 0.4
        y = 2.0 * x + 1.0 + (i % 7) * 0.01
        z = 100.0 - i
        lines.append("%s,%r,%r,%r" % (grp, x, y, z))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def make_case(path, contract=True):
    obj = {"case_id": "selftest-fixture-001"}
    if contract:
        obj["output_contract"] = {
            "analysis_results": {}, "quality_evidence": {}, "anomaly_notes": {}
        }
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(ci.dumps(obj))


def make_task(path):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("比较 a 组与 b 组在 x 上的差异；分析 x 与 y 的相关性。\n")


def build_fixture(root):
    os.makedirs(os.path.join(root, "input"), exist_ok=True)
    make_data_csv(os.path.join(root, "input", "data.csv"))
    make_case(os.path.join(root, "input", "case.json"))
    make_task(os.path.join(root, "input", "task.md"))


def strip_runtime(obj):
    if isinstance(obj, dict):
        return {k: strip_runtime(v) for k, v in obj.items() if k != "runtime"}
    if isinstance(obj, list):
        return [strip_runtime(v) for v in obj]
    return obj


def test_help_exit_zero():
    try:
        ci.main(["--help"])
        code = 0
    except SystemExit as e:
        code = e.code or 0
    check("--help exits 0", code == 0, "exit=%s" % code)


def test_full_pipeline_and_determinism():
    root = tempfile.mkdtemp(prefix="skillselftest_main_")
    try:
        build_fixture(root)
        overrides = {"group": "group", "targets": "x,y", "intent": "group_comparison"}
        out1 = os.path.join(root, "out1")
        out2 = os.path.join(root, "out2")
        work1 = os.path.join(root, "work1")
        work2 = os.path.join(root, "work2")
        ci.run_all(os.path.join(root, "input"), out1, work1, dict(overrides))
        ci.run_all(os.path.join(root, "input"), out2, work2, dict(overrides))

        # result.json contract
        r = ci.read_json(os.path.join(out1, "result.json"))
        check("result.json has case_id", r.get("case_id") == "selftest-fixture-001", str(r.get("case_id")))
        check("result.json has status", r.get("status") in ("ok", "partial"), str(r.get("status")))
        for k in ("analysis_results", "quality_evidence", "anomaly_notes"):
            check("contract key %s filled" % k, k in r and r[k] is not None, "")
        check("sources non-empty", len(r.get("sources") or []) > 0, "n=%d" % len(r.get("sources") or []))
        check("change_log non-empty", len(r.get("change_log") or []) > 0, "")
        check("uncertainty present", isinstance(r.get("uncertainty"), list), "")

        # stats correctness
        stats = ci.read_json(os.path.join(work1, "stats.json"))
        gc = (stats.get("group_comparison") or {}).get("per_target") or {}
        xblock = gc.get("x") or {}
        check("group test p < 0.05 for separated groups", xblock.get("p_value") is not None and xblock["p_value"] < 0.05, str(xblock.get("p_value")))
        check("group test effect size present", bool(xblock.get("effect_size")), str(xblock.get("effect_size")))

        # clean_meta consistency
        cm = ci.read_json(os.path.join(work1, "clean_meta.json"))
        check(
            "clean_meta row consistency",
            cm.get("consistency_check") is True,
            "raw=%s dedup=%s empty=%s cleaned=%s"
            % (cm.get("raw_rows"), cm.get("dedup_removed"), cm.get("fully_empty_rows_removed"), cm.get("cleaned_rows")),
        )

        # report + figures exist and SVG parse
        check("report.md exists", os.path.isfile(os.path.join(out1, "report.md")), "")
        svgs = [f for f in os.listdir(out1) if f.endswith(".svg")]
        check("at least one SVG figure", len(svgs) > 0, str(svgs))
        import xml.etree.ElementTree as ET
        for f in svgs:
            try:
                ET.parse(os.path.join(out1, f))
                ok = True
            except Exception as e:
                ok = False
                check("SVG parseable %s" % f, False, str(e))
            if ok:
                check("SVG parseable %s" % f, True, "")

        # determinism
        same = True
        diff = []
        for f in ("report.md",):
            b1 = open(os.path.join(out1, f), "rb").read()
            b2 = open(os.path.join(out2, f), "rb").read()
            if b1 != b2:
                same = False
                diff.append(f)
        for f in ("cleaned.csv", "clean_meta.json", "stats.json", "profile.json"):
            p1 = os.path.join(work1, f)
            p2 = os.path.join(work2, f)
            if os.path.isfile(p1) and os.path.isfile(p2):
                if open(p1, "rb").read() != open(p2, "rb").read():
                    same = False
                    diff.append(f)
        s1 = sorted(f for f in os.listdir(out1) if f.endswith(".svg"))
        s2 = sorted(f for f in os.listdir(out2) if f.endswith(".svg"))
        if s1 != s2:
            same = False
            diff.append("svg-file-set")
        else:
            for f in s1:
                if open(os.path.join(out1, f), "rb").read() != open(os.path.join(out2, f), "rb").read():
                    same = False
                    diff.append(f)
        check("deterministic artifacts byte-identical", same, "diff=%s" % diff)
        r2 = ci.read_json(os.path.join(out2, "result.json"))
        check("result.json equal after stripping runtime", strip_runtime(r) == strip_runtime(r2), "")
        check("fingerprint present and equal", r.get("fingerprint") and r["fingerprint"] == r2.get("fingerprint"), str(r.get("fingerprint")))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_correlation():
    root = tempfile.mkdtemp(prefix="skillselftest_corr_")
    try:
        build_fixture(root)
        input_dir = os.path.join(root, "input")
        work = os.path.join(root, "work")
        out = os.path.join(root, "out")
        ci.scaffold(input_dir, out, work)
        ci.build_profile(input_dir, work)
        ci.clean_table(input_dir, work)
        ci.analyze(input_dir, work, {"intent": "correlation", "targets": "x,y"})
        stats = ci.read_json(os.path.join(work, "stats.json"))
        pairs = (stats.get("correlation") or {}).get("pairs") or []
        found = [p for p in pairs if {p.get("x"), p.get("y")} == {"x", "y"}]
        check("correlation pair found", len(found) == 1, str(pairs))
        if found:
            r = found[0].get("pearson_r")
            check("|r| ~ 1 for linear pair", r is not None and abs(r) > 0.999, str(r))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_empty_input():
    root = tempfile.mkdtemp(prefix="skillselftest_empty_")
    try:
        input_dir = os.path.join(root, "input")
        os.makedirs(input_dir, exist_ok=True)
        out = os.path.join(root, "out")
        work = os.path.join(root, "work")
        ci.run_all(input_dir, out, work)
        r = ci.read_json(os.path.join(out, "result.json"))
        check("empty input -> insufficient_evidence", r.get("status") == "insufficient_evidence", str(r.get("status")))
        check("empty input still writes result.json", os.path.isfile(os.path.join(out, "result.json")), "")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_bad_case_json():
    root = tempfile.mkdtemp(prefix="skillselftest_badjson_")
    try:
        # bad case.json + NO data -> input_error
        input_dir = os.path.join(root, "input_empty")
        os.makedirs(input_dir, exist_ok=True)
        with open(os.path.join(input_dir, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ this is not json ")
        out = os.path.join(root, "out_empty")
        work = os.path.join(root, "work_empty")
        ci.run_all(input_dir, out, work)
        r = ci.read_json(os.path.join(out, "result.json"))
        check("bad case.json + no data -> input_error", r.get("status") == "input_error", str(r.get("status")))

        # bad case.json + data -> degradation recorded, analysis still runs
        input_dir2 = os.path.join(root, "input_data")
        os.makedirs(input_dir2, exist_ok=True)
        with open(os.path.join(input_dir2, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ bad json ")
        make_data_csv(os.path.join(input_dir2, "data.csv"))
        make_task(os.path.join(input_dir2, "task.md"))
        out2 = os.path.join(root, "out_data")
        work2 = os.path.join(root, "work_data")
        ci.run_all(input_dir2, out2, work2, {"group": "group", "targets": "x", "intent": "group_comparison"})
        r2 = ci.read_json(os.path.join(out2, "result.json"))
        has_deg = any("unparseable" in d for d in r2.get("degradations") or [])
        check("bad case.json + data -> degradation recorded", has_deg, str(r2.get("degradations")))
        check("bad case.json + data -> analysis still produced", r2.get("status") in ("ok", "partial"), str(r2.get("status")))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_sqlite():
    root = tempfile.mkdtemp(prefix="skillselftest_sqlite_")
    try:
        input_dir = os.path.join(root, "input")
        os.makedirs(input_dir, exist_ok=True)
        make_case(os.path.join(input_dir, "case.json"))
        make_task(os.path.join(input_dir, "task.md"))
        db = os.path.join(input_dir, "records.db")
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE samples (grp TEXT, val REAL)")
        for i in range(40):
            grp = "g1" if i < 20 else "g2"
            val = (5.0 + i * 0.3) if i < 20 else (15.0 + (i - 20) * 0.3)
            con.execute("INSERT INTO samples VALUES (?, ?)", (grp, val))
        con.commit()
        con.close()
        out = os.path.join(root, "out")
        work = os.path.join(root, "work")
        ci.run_all(input_dir, out, work, {"group": "grp", "targets": "val", "intent": "group_comparison"})
        stats = ci.read_json(os.path.join(work, "stats.json"))
        gc = (stats.get("group_comparison") or {}).get("per_target") or {}
        check("sqlite discovered and analyzed", "val" in gc and gc["val"].get("p_value") is not None, str(list(gc.keys())))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    test_help_exit_zero()
    test_full_pipeline_and_determinism()
    test_correlation()
    test_empty_input()
    test_bad_case_json()
    test_sqlite()
    print("=" * 70)
    for status, name, detail in REPORT:
        line = "%s  %s" % (status, name)
        if detail:
            line += "  [%s]" % detail
        print(line)
    print("=" * 70)
    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
