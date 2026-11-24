"""selftest.py — deterministic self-check for the robust data-analysis skill.

Run with:
    python3 -B scripts/selftest.py

The script builds disposable inputs in a temp directory, runs the pipeline
through core_impl.cli_main (no subprocess needed), and asserts that:

  1. --help / empty input / broken JSON / missing params / oversized files
     all return exit code 0 with a parseable result.json;
  2. a normal CSV and a SQLite database both run the full pipeline;
  3. repeated runs are byte-identical (determinism);
  4. constant columns and division-by-zero conditions degrade, not crash.
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl  # noqa: E402

PASS = 0
FAIL = 0


def report(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("PASS  %s" % name)
    else:
        FAIL += 1
        print("FAIL  %s  %s" % (name, detail))


def run_cli(args):
    try:
        rc = core_impl.cli_main(list(args))
    except Exception as exc:  # noqa: BLE001
        return 1, "exception: %s" % exc
    return rc, ""


def make_case(tmp, name):
    d = os.path.join(tmp, name)
    os.makedirs(os.path.join(d, "input"), exist_ok=True)
    os.makedirs(os.path.join(d, "output"), exist_ok=True)
    os.makedirs(os.path.join(d, "work"), exist_ok=True)
    return d


def read_result(out):
    p = os.path.join(out, "result.json")
    if not os.path.isfile(p):
        return None
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def iris_csv():
    import random
    random.seed(7)
    header = ["sepal_length", "sepal_width", "petal_length", "petal_width", "species"]
    rows = []
    for i in range(150):
        sp = ["setosa", "versicolor", "virginica"][i % 3]
        base = 0.0 if sp == "setosa" else (1.0 if sp == "versicolor" else 2.0)
        rows.append([
            "%.2f" % (4.3 + base * 0.9 + random.random()),
            "%.2f" % (2.0 + base * 0.5 + random.random() * 0.4),
            "%.2f" % (1.0 + base * 2.0 + random.random()),
            "%.2f" % (0.1 + base * 0.8 + random.random() * 0.3),
            sp,
        ])
    # duplicate one row and one missing cell to exercise cleaning
    rows.append(rows[0][:])
    rows[3][1] = ""
    return header, rows


def write_iris(tmp):
    import csv as _csv
    d = make_case(tmp, "iris")
    with open(os.path.join(d, "input", "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "selftest-iris",
                   "output_contract": {"analysis_results": None, "quality_evidence": None, "anomaly_notes": None}},
                  fh)
    with open(os.path.join(d, "input", "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# task\nCompare the three species groups on the four measurements.\n")
    with open(os.path.join(d, "input", "iris.csv"), "w", newline="", encoding="utf-8") as fh:
        w = _csv.writer(fh)
        w.writerow(iris_csv()[0])
        w.writerows(iris_csv()[1])
    return d


def write_sqlite(tmp, name="wine"):
    d = make_case(tmp, name)
    with open(os.path.join(d, "input", "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "selftest-" + name}, fh)
    with open(os.path.join(d, "input", "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# task\nAnalyze wine measurements by class.\n")
    conn = sqlite3.connect(os.path.join(d, "input", "wine.sqlite"))
    cur = conn.cursor()
    cur.execute("CREATE TABLE wine (alcohol REAL, malic_acid REAL, class INTEGER)")
    import random
    random.seed(3)
    for i in range(60):
        cls = i % 3 + 1
        cur.execute("INSERT INTO wine VALUES (?,?,?)", (11 + cls * 0.6 + random.random(),
                                                        1.5 + cls * 0.4 + random.random(), cls))
    conn.commit()
    conn.close()
    return d


def test_help():
    rc, err = run_cli(["--help"])
    report("help exits 0", rc == 0, err)


def test_empty_input():
    d = make_case(tmp, "empty")
    rc, err = run_cli(["run", "--input", os.path.join(d, "input"), "--output", os.path.join(d, "output"),
                       "--work", os.path.join(d, "work")])
    r = read_result(os.path.join(d, "output"))
    ok = rc == 0 and r is not None and r.get("status") in ("input_error", "insufficient_evidence") and "case_id" in r
    report("empty input -> honest status + valid json", ok, "rc=%s status=%s err=%s" % (rc, r.get("status") if r else None, err))


def test_no_data_with_case():
    d = make_case(tmp, "nodata")
    with open(os.path.join(d, "input", "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "selftest-nodata"}, fh)
    rc, err = run_cli(["run", "--input", os.path.join(d, "input"), "--output", os.path.join(d, "output"),
                       "--work", os.path.join(d, "work")])
    r = read_result(os.path.join(d, "output"))
    ok = rc == 0 and r is not None and r.get("status") == "insufficient_evidence"
    report("case.json present, no data -> insufficient_evidence", ok, "status=%s err=%s" % (r.get("status") if r else None, err))


def test_bad_json():
    d = make_case(tmp, "badjson")
    with open(os.path.join(d, "input", "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{not valid json")
    import csv as _csv
    with open(os.path.join(d, "input", "data.csv"), "w", newline="", encoding="utf-8") as fh:
        w = _csv.writer(fh)
        w.writerow(["a", "b"])
        w.writerow(["1", "2"])
        w.writerow(["3", "4"])
    rc, err = run_cli(["run", "--input", os.path.join(d, "input"), "--output", os.path.join(d, "output"),
                       "--work", os.path.join(d, "work")])
    r = read_result(os.path.join(d, "output"))
    ok = rc == 0 and r is not None and "errors" in r and r.get("status") in ("partial", "ok", "input_error")
    report("broken case.json -> recorded error, valid json", ok, "status=%s err=%s" % (r.get("status") if r else None, err))


def test_missing_params_defaults():
    d = make_case(tmp, "defaults")
    ok = (core_impl.DEFAULT_INPUT == "/input" and core_impl.DEFAULT_OUTPUT == "/app/output"
          and core_impl.DEFAULT_WORK == "/app/work")
    report("missing params default to /input /app/output /app/work", ok)
    # non-existent input dir must still exit 0 with a result.json
    rc, err = run_cli(["run", "--input", os.path.join(d, "input", "nope"), "--output", os.path.join(d, "output"),
                       "--work", os.path.join(d, "work")])
    r = read_result(os.path.join(d, "output"))
    report("non-existent input dir -> exit 0 + result.json", rc == 0 and r is not None, err)


def test_oversized_file():
    d = make_case(tmp, "big")
    with open(os.path.join(d, "input", "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "selftest-big"}, fh)
    with open(os.path.join(d, "input", "big.csv"), "wb") as fh:
        fh.write(b"a,b,c\n")
        fh.write(b"1,2,3\n" * (core_impl.TEXT_SIZE_CAP // 6 + 10))
    rc, err = run_cli(["run", "--input", os.path.join(d, "input"), "--output", os.path.join(d, "output"),
                       "--work", os.path.join(d, "work")])
    r = read_result(os.path.join(d, "output"))
    ok = rc == 0 and r is not None and r.get("degradations")
    report("oversized file -> skipped with degradation", ok, "deg=%s err=%s" % (r.get("degradations") if r else None, err))


def test_normal_csv():
    d = write_iris(tmp)
    rc, err = run_cli(["run", "--input", os.path.join(d, "input"), "--output", os.path.join(d, "output"),
                       "--work", os.path.join(d, "work")])
    r = read_result(os.path.join(d, "output"))
    stats = os.path.join(d, "work", "stats.json")
    ok_st = os.path.isfile(stats)
    svgs = [f for f in os.listdir(os.path.join(d, "output")) if f.endswith(".svg")] if os.path.isdir(os.path.join(d, "output")) else []
    svg_ok = all(_parse_svg(os.path.join(d, "output"), f) for f in svgs)
    ok = (rc == 0 and r is not None and r.get("status") == "ok" and ok_st and len(svgs) > 0 and svg_ok
          and r.get("analysis_results") is not None)
    report("normal CSV -> ok pipeline with stats + SVG figures", ok, "status=%s svgs=%d err=%s" % (r.get("status") if r else None, len(svgs), err))


def _parse_svg(out, f):
    try:
        ET.parse(os.path.join(out, f))
        return True
    except ET.ParseError:
        return False


def test_sqlite():
    d = write_sqlite(tmp)
    rc, err = run_cli(["run", "--input", os.path.join(d, "input"), "--output", os.path.join(d, "output"),
                       "--work", os.path.join(d, "work")])
    r = read_result(os.path.join(d, "output"))
    ok = rc == 0 and r is not None and r.get("status") in ("ok", "partial")
    report("SQLite input -> pipeline runs", ok, "status=%s err=%s" % (r.get("status") if r else None, err))


def test_determinism():
    d = write_iris(tmp)
    a = [os.path.join(d, "input"), os.path.join(d, "output"), os.path.join(d, "work")]
    run_cli(["run", "--input", a[0], "--output", a[1], "--work", a[2]])
    s1 = open(os.path.join(a[2], "stats.json"), "rb").read()
    # reuse same input, fresh work/output
    out2 = os.path.join(d, "output2")
    work2 = os.path.join(d, "work2")
    run_cli(["run", "--input", a[0], "--output", out2, "--work", work2])
    s2 = open(os.path.join(work2, "stats.json"), "rb").read()
    report("determinism: identical stats.json across runs", s1 == s2)


def test_constant_column():
    d = make_case(tmp, "const")
    import csv as _csv
    with open(os.path.join(d, "input", "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "selftest-const"}, fh)
    with open(os.path.join(d, "input", "const.csv"), "w", newline="", encoding="utf-8") as fh:
        w = _csv.writer(fh)
        w.writerow(["x", "group"])
        for i in range(30):
            w.writerow(["5", "A" if i % 2 == 0 else "B"])
    rc, err = run_cli(["run", "--input", os.path.join(d, "input"), "--output", os.path.join(d, "output"),
                       "--work", os.path.join(d, "work")])
    r = read_result(os.path.join(d, "output"))
    ok = rc == 0 and r is not None and r.get("status") in ("ok", "partial")
    report("constant column (zero variance) -> degraded not crash", ok, "status=%s err=%s" % (r.get("status") if r else None, err))


def test_query():
    d = write_sqlite(tmp, "wine_query")
    rc, err = run_cli(["query", "--input", os.path.join(d, "input"), "--work", os.path.join(d, "work"),
                       "--output", os.path.join(d, "output"), "--sql", "SELECT count(*) FROM wine"])
    q = os.path.join(d, "work", "query.json")
    ok = rc == 0 and os.path.isfile(q)
    if ok:
        with open(q, encoding="utf-8") as fh:
            qd = json.load(fh)
        ok = qd.get("status") == "ok" and qd.get("rows") == [[60]]
    report("query command runs against SQLite", ok, "err=%s" % err)


def main():
    global tmp
    tmp = tempfile.mkdtemp(prefix="skill-selftest-")
    try:
        test_help()
        test_empty_input()
        test_no_data_with_case()
        test_bad_json()
        test_missing_params_defaults()
        test_oversized_file()
        test_normal_csv()
        test_sqlite()
        test_determinism()
        test_constant_column()
        test_query()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
