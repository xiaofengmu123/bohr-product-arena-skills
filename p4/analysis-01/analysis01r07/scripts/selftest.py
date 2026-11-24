#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone self-test for the sci-data-cleaning-robust skill.

Runs four deterministic scenarios without touching the user's files and exits 0
when all assertions pass:

  A. empty input directory            -> insufficient_evidence, exit 0
  B. corrupt case.json + valid table  -> graceful degradation, exit 0
  C. missing parameters               -> defaults resolve, exit 0
  D. synthetic case (GBK + sentinel -999 + duplicates + unit hints)
                                      -> full pipeline produces valid artifacts
                                      and a result.json with every contract key

Standard library only. All temp files live under tempfile.mkdtemp() and are
removed afterwards.
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import main as mainmod  # noqa: E402
import core_impl as core  # noqa: E402


def _run(argv):
    """Run mainmod.main with a captured exit code (never raises)."""
    try:
        code = mainmod.main(argv)
        return code if isinstance(code, int) else 0
    except BaseException:  # self-test should surface real bugs
        return -1


def _result_json(out):
    p = os.path.join(out, "result.json")
    if not os.path.isfile(p):
        return None
    with open(p, "r", encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except ValueError:
            return None


def scenario_empty(base):
    inp = os.path.join(base, "empty_in")
    out = os.path.join(base, "empty_out")
    work = os.path.join(base, "empty_work")
    os.makedirs(inp, exist_ok=True)
    c1 = _run(["--phase", "bootstrap", "--input", inp, "--output", out,
               "--work", work])
    c2 = _run(["--phase", "full", "--input", inp, "--output", out,
               "--work", work])
    r = _result_json(out)
    assert c1 == 0 and c2 == 0, "empty: non-zero exit (%s,%s)" % (c1, c2)
    assert r and r.get("status") == "insufficient_evidence", "empty: status"
    assert isinstance(r.get("pending_human"), list), "empty: pending_human"


def scenario_badjson(base):
    inp = os.path.join(base, "badjson_in")
    out = os.path.join(base, "badjson_out")
    work = os.path.join(base, "badjson_work")
    os.makedirs(inp, exist_ok=True)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{ not valid json !!")
    with open(os.path.join(inp, "data.csv"), "w", encoding="utf-8") as fh:
        fh.write("id,v1\n1,0.5\n2,\n")
    c = _run(["--phase", "full", "--input", inp, "--output", out,
              "--work", work])
    r = _result_json(out)
    assert c == 0, "badjson: non-zero exit"
    assert r is not None, "badjson: result.json missing"
    assert r.get("case_id"), "badjson: case_id fallback missing"
    assert isinstance(r.get("errors"), list), "badjson: errors not list"


def scenario_missing_params(base):
    # No --input/--output/--work: defaults must resolve and exit 0.
    out = os.path.join(base, "noparam_out")
    work = os.path.join(base, "noparam_work")
    c = _run(["--phase", "bootstrap", "--output", out, "--work", work])
    assert c == 0, "missing params: non-zero exit"
    r = _result_json(out)
    assert r is not None, "missing params: result.json missing"
    assert r.get("status") in ("partial", "insufficient_evidence",
                               "input_error"), "missing params: status"


def scenario_synthetic(base):
    inp = os.path.join(base, "syn_in")
    out = os.path.join(base, "syn_out")
    work = os.path.join(base, "syn_work")
    os.makedirs(inp, exist_ok=True)

    contract = {
        "cleaned_table_path": "string",
        "quality_report_path": "string",
        "data_profile_path": "string",
        "evidence_map_path": "string",
        "novel_field": "object",   # unknown key must be preserved
    }
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "syn-case-001",
                   "output_contract": contract}, fh, ensure_ascii=False)

    # GBK-encoded table with sentinel missing, zero, below-LOD, duplicates.
    rows = "sample_id,conc_mgL,flag\n"
    rows += "A01,0.50,ok\n"
    rows += "A02,-999,missing\n"
    rows += "A03,0,zero\n"
    rows += "A04,<0.01,below_lod\n"
    rows += "a01,0.50,dup\n"   # near-duplicate of A01 (case-only difference)
    with open(os.path.join(inp, "data.csv"), "wb") as fh:
        fh.write(rows.encode("gb18030"))

    with open(os.path.join(inp, "data_dictionary.md"), "w",
              encoding="utf-8") as fh:
        fh.write("# 字典\n- sample_id: 样品编号（主键）\n"
                 "- conc_mgL: 浓度 mg/L，范围 0~10\n"
                 "- flag: 备注\n")
    with open(os.path.join(inp, "qc_rules.md"), "w", encoding="utf-8") as fh:
        fh.write("# QC\n- -999 表示录入缺失\n- <LOD 表示低于检出限\n"
                 "- conc 合理范围 0~10 mg/L；换算 μg/mL = mg/L\n")
    with open(os.path.join(inp, "cleaning_workorder.md"), "w",
              encoding="utf-8") as fh:
        fh.write("# 工单\n1 区分未检测/低于检出限/录入缺失/真值0\n"
                 "2 按 sample_id 去重\n3 保留 mg/L，附 μg/mL 换算\n")

    c1 = _run(["--phase", "bootstrap", "--input", inp, "--output", out,
               "--work", work])
    c2 = _run(["--phase", "full", "--input", inp, "--output", out,
               "--work", work])
    c3 = _run(["--phase", "selfcheck", "--input", inp, "--output", out,
               "--work", work])
    assert c1 == 0 and c2 == 0 and c3 == 0, "syn: non-zero exit"

    r = _result_json(out)
    assert r and r.get("case_id") == "syn-case-001", "syn: case_id"
    for k in contract:
        assert k in r, "syn: contract key lost: %s" % k
    assert r.get("novel_field") is None, "syn: unknown key should be null"

    cleaned = os.path.join(out, "cleaned_data.csv")
    assert os.path.isfile(cleaned), "syn: cleaned_data.csv missing"
    with open(cleaned, "r", encoding="utf-8") as fh:
        text = fh.read()
    assert "sample_id" in text and "conc_mgL" in text, "syn: headers"
    assert "__qc_flag" in text, "syn: qc flags missing"

    prof_p = os.path.join(out, "data_profile.json")
    assert os.path.isfile(prof_p), "syn: data_profile missing"
    with open(prof_p, "r", encoding="utf-8") as fh:
        profile = json.load(fh)
    cats = profile["columns"]["conc_mgL"]["categories"]
    assert cats["missing"] == 1, "syn: missing count"
    assert cats["zero"] == 1, "syn: zero count"
    assert cats["below_lod"] == 1, "syn: below_lod count"

    sc = os.path.join(out, "selfcheck.json")
    assert os.path.isfile(sc), "syn: selfcheck.json missing"
    with open(sc, "r", encoding="utf-8") as fh:
        schk = json.load(fh)
    assert schk.get("overall") == "pass", "syn: selfcheck overall: %s" % schk


def main():
    base = tempfile.mkdtemp(prefix="scdrobust_selftest_")
    passed = 0
    failures = []
    try:
        for name, fn in (("empty", scenario_empty),
                         ("badjson", scenario_badjson),
                         ("missing_params", scenario_missing_params),
                         ("synthetic", scenario_synthetic)):
            try:
                fn(base)
                passed += 1
                print("PASS  %s" % name)
            except AssertionError as exc:
                failures.append((name, str(exc)))
                print("FAIL  %s: %s" % (name, exc))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    print("SELFTEST %d/4 passed" % passed)
    if failures:
        for name, msg in failures:
            print("  - %s: %s" % (name, msg))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
