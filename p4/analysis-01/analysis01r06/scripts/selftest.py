#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone self-test for the sci-data-cleaning skill.

Runs three isolated scenarios in temporary directories and checks that the
pipeline exits cleanly, writes honest statuses and produces artifacts whose
reported numbers can be re-derived.  Standard library only, deterministic.
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core
import main as cli


def _mk(path, content, name):
    full = os.path.join(path, name)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return full


def scenario_empty():
    """Empty input directory -> honest insufficient_evidence, exit 0."""
    base = tempfile.mkdtemp(prefix="sdc_selftest_empty_")
    inp = os.path.join(base, "input")
    out = os.path.join(base, "output")
    work = os.path.join(base, "work")
    os.makedirs(inp)
    cli.main(["--phase", "bootstrap", "--input", inp, "--output", out, "--work", work])
    cli.main(["--phase", "full", "--input", inp, "--output", out, "--work", work])
    r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    ok = r.get("status") == "insufficient_evidence" and r.get("case_id")
    shutil.rmtree(base, ignore_errors=True)
    return ok, "status=%s" % r.get("status")


def scenario_bad_json():
    """Malformed case.json -> graceful degradation, exit 0."""
    base = tempfile.mkdtemp(prefix="sdc_selftest_bad_")
    inp = os.path.join(base, "input")
    out = os.path.join(base, "output")
    work = os.path.join(base, "work")
    os.makedirs(inp)
    _mk(inp, "{ not valid json", "case.json")
    _mk(inp, "a,b\n1,2\n", "data.csv")
    cli.main(["--phase", "bootstrap", "--input", inp, "--output", out, "--work", work])
    r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    ok = r.get("case_id") is not None and isinstance(r.get("errors", []), list)
    shutil.rmtree(base, ignore_errors=True)
    return ok, "case_id=%s errors=%d" % (r.get("case_id"), len(r.get("errors", [])))


def scenario_minimal():
    """Minimal valid case with encoding fault + dictionary + rules + workorder."""
    base = tempfile.mkdtemp(prefix="sdc_selftest_min_")
    inp = os.path.join(base, "input")
    out = os.path.join(base, "output")
    work = os.path.join(base, "work")
    os.makedirs(inp)
    _mk(inp, json.dumps({
        "case_id": "selftest-1",
        "output_contract": {
            "cleaned_table": None,
            "quality_report": None,
            "issues": [],
        },
    }, ensure_ascii=False), "case.json")
    _mk(inp, "# 数据清洗任务\n完成缺失值、异常值、重复记录、格式与单位转换、质量报告。",
        "task.md")
    # data with GBK-encoded header injection + missing sentinels + duplicates
    data = ("sample_id,glucose_mg_L,notes\n"
            "S-01,5.2,\u6b63\u5e38\n"
            "S-01,5.2,repeat\n"
            "S-02,-999,missing\n"
            "S-03,<LOD,below\n"
            "S-04,0,zero\n"
            "S-05,99.9,anomaly\n")
    _mk(inp, data, "data.csv")
    _mk(inp, "field: glucose_mg_L; unit: mg/L; semantic: blood glucose\n"
             "field: sample_id; semantic: sample identifier\n", "dictionary.csv")
    _mk(inp, "glucose_mg_L unit mg/L range 0-20\n", "qc_rules.csv")
    _mk(inp, "convert glucose_mg_L to mg/L; classify -999 as missing; "
             "dedup by sample_id\n", "cleaning_workorder.csv")
    cli.main(["--phase", "bootstrap", "--input", inp, "--output", out, "--work", work])
    cli.main(["--phase", "full", "--input", inp, "--output", out, "--work", work])
    cli.main(["--phase", "selfcheck", "--input", inp, "--output", out, "--work", work])

    r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    sc = json.load(open(os.path.join(out, "selfcheck.json"), encoding="utf-8"))
    artifacts_ok = all(os.path.isfile(os.path.join(out, n)) for n in
                       ("cleaned_data.csv", "raw_snapshot.csv", "plan.json",
                        "field_model.json", "quality_report.md", "evidence_map.json"))
    plan = json.load(open(os.path.join(out, "plan.json"), encoding="utf-8"))
    model = json.load(open(os.path.join(out, "field_model.json"), encoding="utf-8"))
    contract_ok = ("cleaned_table" in r and "quality_report" in r and "issues" in r)
    # duplicates removed: S-01 appears twice -> 1 removed
    dup_ok = model.get("n_duplicates_removed") == 1
    ok = (r.get("case_id") == "selftest-1" and sc.get("ok") and artifacts_ok
          and contract_ok and dup_ok and len(plan.get("fields", [])) >= 3)
    shutil.rmtree(base, ignore_errors=True)
    return ok, ("case_id=%s selfcheck=%s dup_removed=%d" % (
        r.get("case_id"), sc.get("ok"), model.get("n_duplicates_removed")))


def run():
    scenarios = [
        ("empty input", scenario_empty),
        ("bad case.json", scenario_bad_json),
        ("minimal valid case", scenario_minimal),
    ]
    all_ok = True
    for name, fn in scenarios:
        try:
            ok, detail = fn()
            all_ok = all_ok and ok
            print("SELFTEST %-18s %s  %s" % ("[" + name + "]", "PASS" if ok else "FAIL", detail))
        except Exception as e:
            all_ok = False
            print("SELFTEST %-18s ERROR %s: %s" % ("[" + name + "]", type(e).__name__, e))
    print("SELFTEST overall:", "PASS" if all_ok else "FAIL")
    return all_ok


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
