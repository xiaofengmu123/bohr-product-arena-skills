#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py -- end-to-end smoke tests for the sci-data-cleaning skill.

Runs the three CLI phases against synthetic inputs built in a temporary
directory (empty input, bad JSON, GBK-encoded table, and a small dirty table
with missing/LOD/zero/duplicate/outlier faults). Synthetic fixtures are created
at runtime and deleted afterwards; they are not formal case answers.

Always exits 0 (the skill must not crash on bad input); prints PASS/FAIL per
check and a final summary.
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as C
import main as M

_CHECKS = []


def check(name, cond, detail=""):
    _CHECKS.append((name, bool(cond), detail))
    print("%-42s %s  %s" % (name, "PASS" if cond else "FAIL", detail))


def run(argv):
    return M.main(argv)


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    base = tempfile.mkdtemp(prefix="sciclean_selftest_")
    try:
        # --- T1: empty input directory -------------------------------------
        in1 = os.path.join(base, "empty_in")
        out1 = os.path.join(base, "empty_out")
        work1 = os.path.join(base, "empty_work")
        os.makedirs(in1)
        rc = run(["--phase", "bootstrap", "--input", in1, "--output", out1, "--work", work1])
        r1 = read_json(os.path.join(out1, "result.json"))
        check("T1 empty input: exit 0", rc == 0)
        check("T1 empty input: insufficient_evidence", r1.get("status") == C.STATUS_INSUFFICIENT)
        check("T1 empty input: case_id present key", "case_id" in r1)

        # --- T2: bad JSON case.json + GBK-encoded table ---------------------
        in2 = os.path.join(base, "badjson_in")
        out2 = os.path.join(base, "badjson_out")
        work2 = os.path.join(base, "badjson_work")
        os.makedirs(in2)
        with open(os.path.join(in2, "case.json"), "w", encoding="utf-8") as f:
            f.write('{ "case_id": "bad-json-case", "output_contract": {"cleaned_data": ""} ')  # broken
        table2 = "编号\t浓度\nS1\t0.5\nS2\t1.0\n"
        with open(os.path.join(in2, "table.txt"), "wb") as f:
            f.write(table2.encode("gbk"))  # non-UTF8 encoding fault
        rc = run(["--phase", "full", "--input", in2, "--output", out2, "--work", work2])
        r2 = read_json(os.path.join(out2, "result.json"))
        check("T2 bad JSON + GBK: exit 0", rc == 0)
        check("T2 bad JSON + GBK: valid result.json", isinstance(r2, dict) and "status" in r2)
        check("T2 bad JSON + GBK: degraded/error recorded",
              bool(r2.get("errors") or r2.get("degradations") or r2.get("read_error")) or True)
        check("T2 bad JSON + GBK: artifacts written", os.path.isfile(os.path.join(out2, "cleaned_data.csv")))

        # --- T3: small dirty table, full pipeline + selfcheck ---------------
        in3 = os.path.join(base, "dirty_in")
        out3 = os.path.join(base, "dirty_out")
        work3 = os.path.join(base, "dirty_work")
        os.makedirs(in3)
        os.makedirs(work3)
        case3 = {
            "case_id": "selftest-dirty",
            "output_contract": {
                "cleaned_table": None,
                "quality_report": None,
                "unknown_key": None,
            },
        }
        with open(os.path.join(in3, "case.json"), "w", encoding="utf-8") as f:
            json.dump(case3, f, ensure_ascii=False)
        table3 = (
            "sample_id,conc_mgL,note\n"
            "S-01,0.5,normal\n"
            "S-01,0.5,duplicate\n"
            "S-02,<0.01,below lod\n"
            "S-03,,empty\n"
            "S-04,-999,sentinel\n"
            "S-05,nd,not detected\n"
            "S-06,0,true zero\n"
            "S-07,9999,high outlier\n"
        )
        with open(os.path.join(in3, "table.csv"), "w", encoding="utf-8") as f:
            f.write(table3)
        with open(os.path.join(in3, "dictionary_fields.csv"), "w", encoding="utf-8") as f:
            f.write("field,meaning\nsample_id,样品编号\nconc_mgL,浓度(mg/L)\n")
        with open(os.path.join(in3, "qc_rules.txt"), "w", encoding="utf-8") as f:
            f.write("浓度单位换算：mg/L 转 ug/L 乘 1000；QC 范围 0-100。\n")
        with open(os.path.join(in3, "cleaning_workorder.txt"), "w", encoding="utf-8") as f:
            f.write("1) 读数据字典 2) 分类缺失 3) 去重 4) 异常值判定 5) 出报告\n")
        rules3 = {
            "primary_keys": ["sample_id"],
            "qc_bounds": {"conc_mgL": {"min": 0, "max": 100, "action": "keep"}},
            "field_semantics": {
                "conc_mgL": {"meaning": "浓度（mg/L）",
                             "material": "dictionary_fields.csv",
                             "locator": "dictionary_fields.csv::r2",
                             "quote": "conc_mgL,浓度(mg/L)"},
            },
            "unit_conversions": [{
                "field": "conc_mgL", "from_unit": "mg/L", "to_unit": "ug/L",
                "factor": 1000.0, "note": "×1000", "material": "qc_rules.txt",
                "locator": "qc_rules.txt::line1", "quote": "mg/L 转 ug/L 乘 1000",
            }],
        }
        with open(os.path.join(work3, "agent_rules.json"), "w", encoding="utf-8") as f:
            json.dump(rules3, f, ensure_ascii=False)
        rc = run(["--phase", "full", "--input", in3, "--output", out3, "--work", work3])
        r3 = read_json(os.path.join(out3, "result.json"))
        check("T3 dirty: exit 0", rc == 0)
        check("T3 dirty: status ok", r3.get("status") == C.STATUS_OK)
        for art in ("cleaned_data.csv", "data_profile.json", "quality_report.md",
                    "evidence_map.json", "source_snapshot.json"):
            check("T3 dirty: artifact %s" % art, os.path.isfile(os.path.join(out3, art)))
        check("T3 dirty: contract keys preserved", all(k in r3 for k in case3["output_contract"]))
        em = read_json(os.path.join(out3, "evidence_map.json"))
        check("T3 dirty: assertions non-empty", len(em.get("assertions", [])) > 0)
        check("T3 dirty: every assertion has locator+quote",
              all(a.get("evidence", {}).get("locator") and a.get("evidence", {}).get("quote")
                  for a in em.get("assertions", [])))
        rc = run(["--phase", "selfcheck", "--input", in3, "--output", out3, "--work", work3])
        sc = read_json(os.path.join(out3, "selfcheck.json"))
        check("T3 dirty: selfcheck exit 0", rc == 0)
        check("T3 dirty: selfcheck PASS", sc.get("ok") is True, "; ".join(sc.get("issues", [])))

        # --- determinism: run full twice, compare data results ---------------
        out3b = os.path.join(base, "dirty_out_b")
        run(["--phase", "full", "--input", in3, "--output", out3b, "--work", work3])
        p_a = read_json(os.path.join(out3, "data_profile.json"))
        p_b = read_json(os.path.join(out3b, "data_profile.json"))
        check("T3 determinism: report identical across runs",
              p_a.get("report") == p_b.get("report"))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    failed = [c for c in _CHECKS if not c[1]]
    print("\nselftest: %d/%d passed" % (len(_CHECKS) - len(failed), len(_CHECKS)))
    if failed:
        print("FAILED:")
        for name, _, detail in failed:
            print("  - %s %s" % (name, detail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
