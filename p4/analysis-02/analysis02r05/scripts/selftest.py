"""selftest.py - self-check for the statistical analysis skill.

Runs against a synthetic demo input built in a temp directory (never ships
with prepared answers), then verifies:

  1. `skeleton` produces a parseable result.json with contract fields in order.
  2. `analyze` -> `emit` produces a result.json where every output_contract key
     is present and the envelope keys exist.
  3. change_log is non-empty when cleaning actions happened.
  4. Repeated runs are byte-identical (deterministic, no seed needed).
  5. Numeric spot checks: mean / sd / Welch t / Wilson CI against hand-computed
     reference values.

Exit code 0 on success, non-zero on failure.
"""

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
STATS = os.path.join(HERE, "stats.py")

sys.path.insert(0, HERE)
import stats  # noqa: E402
import main  # noqa: E402


def _run(cmd, input_dir, work_dir, output_dir):
    return subprocess.run(
        [sys.executable, "-B", MAIN, cmd,
         "--input", input_dir, "--work", work_dir, "--output", output_dir],
        capture_output=True, text=True, timeout=120)


def build_demo(input_dir):
    os.makedirs(input_dir, exist_ok=True)
    case = {
        "case_id": "demo-case-001",
        "task_instruction": "compare group A vs B on measure",
        "output_contract": {
            "result": {},
            "descriptive": [],
            "inferential": [],
            "effects": [],
            "quality": [],
            "interpretation": {"note": ""},
        },
    }
    with open(os.path.join(input_dir, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False)
    with open(os.path.join(input_dir, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# demo\n")
    # A: mean 10 sd 2, B: mean 13 sd 2.5; inject one missing, one duplicate, one outlier
    rows = []
    a_vals = [8, 9, 10, 11, 12, 13, 7, 10, 11, 12, 9, 10, 11, 10, 12, 8]
    b_vals = [11, 12, 13, 14, 15, 16, 10, 13, 14, 15, 12, 13, 14, 13, 15, 11]
    for v in a_vals:
        rows.append({"group": "A", "measure": v})
    for v in b_vals:
        rows.append({"group": "B", "measure": v})
    rows.append({"group": "A", "measure": 40})       # outlier (kept, flagged)
    rows.append({"group": "A", "measure": None})     # missing (excluded)
    rows.append({"group": "A", "measure": 8})        # duplicate row
    rows.append(dict(rows[-1]))                      # exact duplicate of previous
    with open(os.path.join(input_dir, "data.csv"), "w", encoding="utf-8", newline="") as fh:
        import csv
        w = csv.DictWriter(fh, fieldnames=["group", "measure"])
        w.writeheader()
        w.writerows(rows)


def check_contract_order(skeleton, contract):
    keys = list(skeleton.keys())
    ck = list(contract.keys())
    # case_id may be first; contract keys must keep relative order among themselves
    pos = [keys.index(k) for k in ck if k in keys]
    return pos == sorted(pos)


def run_all(tmp):
    input_dir = os.path.join(tmp, "input")
    work_dir = os.path.join(tmp, "work")
    out_dir = os.path.join(tmp, "output")
    build_demo(input_dir)

    r = _run("skeleton", input_dir, work_dir, out_dir)
    assert r.returncode == 0, r.stderr
    with open(os.path.join(out_dir, "result.json"), encoding="utf-8") as fh:
        skel = json.load(fh)
    assert skel["case_id"] == "demo-case-001"
    assert check_contract_order(skel, {"result": {}, "descriptive": [],
                                       "inferential": [], "effects": [],
                                       "quality": [], "interpretation": {}})

    r = _run("analyze", input_dir, work_dir, out_dir)
    assert r.returncode == 0, r.stderr
    with open(os.path.join(work_dir, "analysis.json"), encoding="utf-8") as fh:
        analysis = json.load(fh)
    assert analysis["cleaning"]["n_missing_measure"] == 1
    assert analysis["cleaning"]["n_duplicates_dropped"] >= 1
    assert analysis["cleaning"]["n_outliers_flagged"] >= 1
    assert analysis["change_log"], "change_log must record cleaning actions"

    r = _run("emit", input_dir, work_dir, out_dir)
    assert r.returncode == 0, r.stderr
    with open(os.path.join(out_dir, "result.json"), encoding="utf-8") as fh:
        result = json.load(fh)
    for k in ("result", "descriptive", "inferential", "effects", "quality",
              "interpretation"):
        assert k in result, "missing contract field %s" % k
    for k in ("case_id", "status", "sources", "uncertainty", "pending_human",
              "change_log"):
        assert k in result, "missing envelope field %s" % k
    assert result["status"] in ("ok", "partial")
    assert result["change_log"], "emitted change_log empty"

    r = _run("audit", input_dir, work_dir, out_dir)
    assert r.returncode == 0, r.stderr
    audit = json.loads(r.stdout)
    assert audit["ok"], audit

    return result


def spot_checks():
    x = [8, 9, 10, 11, 12]
    d = stats.describe(x)
    assert abs(d["mean"] - 10.0) < 1e-12
    assert abs(d["sd"] - math.sqrt(2.5)) < 1e-12
    assert abs(d["median"] - 10.0) < 1e-12

    a = [10, 11, 12, 13, 14]
    b = [13, 14, 15, 16, 17]
    w = stats.welch_t(a, b)
    assert abs(w["mean_diff"] - (-3.0)) < 1e-12
    t = stats.student_t(a, b)
    assert abs(t["statistic"] - (-3.0)) < 1e-12

    ci = stats.wilson_ci(5, 10)
    assert 0.2 <= ci["lower"] < 0.5 < ci["upper"] <= 0.8

    r = stats.pearson_r([1, 2, 3, 4, 5], [2, 4, 6, 8, 10])
    assert abs(r["point"] - 1.0) < 1e-12


def determinism_check(tmp):
    input_dir = os.path.join(tmp, "input")
    out1 = os.path.join(tmp, "out1")
    out2 = os.path.join(tmp, "out2")
    work = os.path.join(tmp, "work")
    for out in (out1, out2):
        _run("skeleton", input_dir, work, out)
        _run("analyze", input_dir, work, out)
        _run("emit", input_dir, work, out)
    with open(os.path.join(out1, "result.json"), "rb") as fh:
        b1 = fh.read()
    with open(os.path.join(out2, "result.json"), "rb") as fh:
        b2 = fh.read()
    assert b1 == b2, "non-deterministic output"


def main():
    tmp = tempfile.mkdtemp(prefix="skill-selftest-")
    try:
        spot_checks()
        run_all(tmp)
        determinism_check(tmp)
        print("selftest ok")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
