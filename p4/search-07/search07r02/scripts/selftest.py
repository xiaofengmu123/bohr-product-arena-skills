#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 交付前自检（仅标准库，可在他机/他 Python 版本运行）。

覆盖清单（对应 SKILL.md 第 5 节）：
  1) scripts/main.py --help 退出码 0
  2) 空输入目录: scaffold→inspect→run 退出码 0 且 status=insufficient_evidence
  3) 坏 JSON: 不崩溃，errors/degradations 记录
  4) 正常样例: scaffold→inspect→run→merge，覆盖率/命中/补检增益可复算
  5) 确定性: 同输入重复 run，coverage_rate 与命中状态一致
  6) output_contract 键原样回填
样例夹具为合成数据，仅用于自检，不冒充正式案例结果。
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ANALYZE = os.path.join(HERE, "main.py")

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS  {0} {1}".format(name, detail))
    else:
        FAIL += 1
        print("FAIL  {0} {1}".format(name, detail))


def run_analyze(args):
    r = subprocess.run([sys.executable, "-B", ANALYZE] + args, capture_output=True, text=True)
    return r


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def load_result(out_dir):
    with open(os.path.join(out_dir, "result.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def make_normal_input(root):
    case = {
        "case_id": "case-selftest-001",
        "output_contract": {"score_field": None, "required_artifacts": []},
    }
    write_json(os.path.join(root, "case.json"), case)

    refs = {
        "source": "gold_standard_review (synthetic self-test fixture)",
        "records": [
            {"doi": "10.1000/ref1", "title": "Alpha study", "authors": [{"family": "Smith", "given": "J"}], "year": 2020},
            {"doi": "10.1000/ref2", "title": "Beta method", "authors": [{"family": "Lee", "given": "K"}], "year": 2019},
            {"title": "Gamma framework for X", "authors": "Wang", "year": 2021},
            {"doi": "10.1000/ref4", "title": "Delta survey", "authors": [{"family": "Chen", "given": "Y"}], "year": 2018},
        ],
    }
    write_json(os.path.join(root, "reference_set.json"), refs)

    results = {
        "query": "Alpha OR Beta OR Gamma",
        "hits": [
            {"doi": "10.1000/ref1", "title": "Alpha study", "authors": [{"family": "Smith", "given": "J"}], "year": 2020},
            {"doi": "10.1000/ref2", "title": "Beta method", "authors": [{"family": "Lee", "given": "K"}], "year": 2019},
            {"title": "Gamma framework for X", "authors": "Wang", "year": 2021},
        ],
    }
    write_json(os.path.join(root, "search_results.json"), results)

    # 补充检索证据材料（供 merge 阶段逐字核验，不作为 run 阶段的检索结果）
    extra = {
        "query": "Delta survey",
        "hits": [
            {"doi": "10.1000/ref4", "title": "Delta survey", "authors": [{"family": "Chen", "given": "Y"}], "year": 2018},
        ],
    }
    write_json(os.path.join(root, "results2.json"), extra)
    return case, refs, results, extra


def test_help():
    r = run_analyze(["--help"])
    check("help_exit0", r.returncode == 0, "rc={0}".format(r.returncode))


def test_empty_dir():
    with tempfile.TemporaryDirectory() as base:
        inp = os.path.join(base, "in")
        out = os.path.join(base, "out")
        work = os.path.join(base, "work")
        os.makedirs(inp)
        rc1 = run_analyze(["scaffold", "--input", inp, "--output", out, "--work", work]).returncode
        rc2 = run_analyze(["inspect", "--input", inp, "--output", out, "--work", work]).returncode
        rc3 = run_analyze(["run", "--input", inp, "--output", out, "--work", work]).returncode
        check("empty_exit0", rc1 == 0 and rc2 == 0 and rc3 == 0, "rc={0},{1},{2}".format(rc1, rc2, rc3))
        try:
            r = load_result(out)
            check("empty_valid_json", isinstance(r, dict))
            check("empty_insufficient", r.get("status") == "insufficient_evidence", r.get("status"))
        except Exception as e:
            check("empty_valid_json", False, str(e))


def test_bad_json():
    with tempfile.TemporaryDirectory() as base:
        inp = os.path.join(base, "in")
        out = os.path.join(base, "out")
        work = os.path.join(base, "work")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            f.write("{ this is not valid json")
        write_json(os.path.join(inp, "reference_set.json"), {"records": [{"doi": "10.1000/x", "title": "T"}]})
        rc1 = run_analyze(["scaffold", "--input", inp, "--output", out, "--work", work]).returncode
        rc2 = run_analyze(["inspect", "--input", inp, "--output", out, "--work", work]).returncode
        rc3 = run_analyze(["run", "--input", inp, "--output", out, "--work", work]).returncode
        check("badjson_exit0", rc1 == 0 and rc2 == 0 and rc3 == 0, "rc={0},{1},{2}".format(rc1, rc2, rc3))
        r = load_result(out)
        flagged = bool(r.get("errors") or r.get("degradations") or r.get("limitations"))
        check("badjson_flagged", flagged, "errors={0} degradations={1}".format(len(r.get("errors", [])), len(r.get("degradations", []))))


def test_normal_and_determinism():
    with tempfile.TemporaryDirectory() as base:
        inp = os.path.join(base, "in")
        os.makedirs(inp)
        make_normal_input(inp)

        def run_sequence(out, work):
            run_analyze(["scaffold", "--input", inp, "--output", out, "--work", work])
            run_analyze(["inspect", "--input", inp, "--output", out, "--work", work])
            run_analyze(["run", "--input", inp, "--output", out, "--work", work,
                         "--refs", "reference_set.json", "--results", "search_results.json"])
            return load_result(out)

        outA = os.path.join(base, "outA")
        outB = os.path.join(base, "outB")
        r1 = run_sequence(outA, os.path.join(base, "workA"))
        r2 = run_sequence(outB, os.path.join(base, "workB"))

        cov = r1["coverage"]
        check("normal_denominator", cov["denominator"] == 4, str(cov["denominator"]))
        check("normal_numerator", cov["numerator"] == 3, str(cov["numerator"]))
        check("normal_rate", cov["coverage_rate"] == 0.75, str(cov["coverage_rate"]))
        check("normal_contract_backfill", r1.get("score_field") is None and "score_field" in r1.get("output_contract_keys", []))
        check("normal_hit_matrix_rows", len(r1.get("hit_matrix", [])) == 4, str(len(r1.get("hit_matrix", []))))

        statuses_a = [h["status"] for h in r1["hit_matrix"]]
        statuses_b = [h["status"] for h in r2["hit_matrix"]]
        check("determinism_rate", r1["coverage"]["coverage_rate"] == r2["coverage"]["coverage_rate"])
        check("determinism_statuses", statuses_a == statuses_b, "{0} vs {1}".format(statuses_a, statuses_b))

        # merge with updates（归因 + 补充检索），复用 outA 的 run 结果
        updates = {
            "attributions": [
                {"ref_id": "R004", "category": "source_coverage",
                 "signals": ["原查询未覆盖 'Delta survey'", "结果库未收录该记录"],
                 "evidence": {"locator": "reference_set.json", "quote": "Delta survey"}},
            ],
            "supplementary_searches": [
                {"id": "supp1", "query": "Delta survey", "field": "title_abstract",
                 "source": "results2.json", "time_window": "no_limit",
                 "new_hits": [{"ref_id": "R004", "source_locator": "results2.json", "quote": "Delta survey"}]},
            ],
        }
        upd_path = os.path.join(base, "updates.json")
        write_json(upd_path, updates)
        rc = run_analyze(["merge", "--input", inp, "--output", outA, "--work", os.path.join(base, "workA"),
                          "--updates", upd_path]).returncode
        check("merge_exit0", rc == 0, "rc={0}".format(rc))
        rm = load_result(outA)
        check("merge_final_ok", rm.get("status") == "ok", rm.get("status"))
        check("merge_full_coverage", rm["coverage"]["coverage_rate"] == 1.0, str(rm["coverage"]["coverage_rate"]))
        mg = rm.get("marginal_gain", {})
        check("merge_marginal_gain", mg.get("pre_supplement_omissions") == 1 and mg.get("new_unique_hits") == 1 and mg.get("gain_rate") == 1.0, str(mg))
        check("merge_evidence_verified", len(rm.get("evidence_map", [])) >= 2, str(len(rm.get("evidence_map", []))))
        check("merge_report_exists", os.path.isfile(os.path.join(outA, "coverage_report.md")))
        check("merge_csv_exists", os.path.isfile(os.path.join(outA, "hit_matrix.csv")))


def main():
    print("selftest: main.py 位于 {0}".format(ANALYZE))
    test_help()
    test_empty_dir()
    test_bad_json()
    test_normal_and_determinism()
    print("")
    print("RESULT: {0} passed, {1} failed".format(PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
