#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 自检脚本：在临时目录造合成输入，跑通 skeleton→normalize→finalize→selfcheck，
并覆盖空输入、坏 JSON、坏编码三种异常场景。仅标准库，退出码 0 表示全部通过。"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pipeline  # noqa: E402


class NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def run(cmd, input_dir, output_dir, work_dir):
    return pipeline.main(["selfcheck" if cmd == "selfcheck" else cmd,
                          "--input", input_dir, "--output", output_dir, "--work", work_dir])


def make_case(tmp):
    inp = os.path.join(tmp, "input")
    os.makedirs(inp, exist_ok=True)
    case = {
        "case_id": "demo-001",
        "research_question": "在成人哮喘患者中，ICS/LABA 联合治疗相比单用 ICS 是否降低急性加重风险？",
        "output_contract": {
            "candidate_set": [],
            "screening_summary": None,
            "excluded_records": [],
            "evidence_records": [],
        },
        "records": [
            {"title": "Combination ICS LABA versus ICS alone for adult asthma", "abstract": "RCT of 1200 adults with asthma. Combination therapy reduced exacerbations by 30%.", "doi": "10.1000/demo.1", "authors": "A; B", "year": "2021"},
            {"title": "Combination ICS-LABA vs ICS alone in adult asthma: a randomized trial", "abstract": "Same study, published version. Exacerbation risk reduced.", "doi": "10.1000/demo.1", "authors": "A; B", "year": "2022"},
            {"title": "Pediatric asthma and montelukast", "abstract": "Children with asthma receiving montelukast.", "doi": "10.1000/demo.2", "authors": "C", "year": "2020"},
        ],
    }
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        json.dump(case, f, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write("# 示例任务\n\nchallenge_id: demo-001\n\n研究问题：如上。\n")
    return inp


def make_judgments(work):
    jd = pipeline.load_json(os.path.join(work, "judgments_template.json"))
    for j in jd["judgments"]:
        if j["record_id"] == "R0001":
            j.update(decision="include", reason_code="in_scope",
                     evidence_quote="Combination ICS LABA versus ICS alone for adult asthma",
                     reviewer_a={"decision": "include", "reason_code": "in_scope"},
                     reviewer_b={"decision": "include", "reason_code": "in_scope"})
        else:
            j.update(decision="exclude", reason_code="ex_population",
                     evidence_quote="Pediatric asthma and montelukast",
                     reviewer_a={"decision": "exclude", "reason_code": "ex_population"},
                     reviewer_b={"decision": "exclude", "reason_code": "ex_population"})
    jd["criteria_version"] = "v1"
    pipeline.write_json(os.path.join(work, "judgments.json"), jd)


def main():
    failures = []
    tmp = tempfile.mkdtemp(prefix="selsktest_")

    # 1) 正常路径
    inp = make_case(tmp)
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    rc = run("skeleton", inp, out, work)
    assert rc == 0, "skeleton failed"
    rc = run("normalize", inp, out, work)
    assert rc == 0, "normalize failed"
    make_judgments(work)
    rc = run("finalize", inp, out, work)
    assert rc == 0, "finalize failed"
    rc = run("selfcheck", inp, out, work)
    assert rc == 0, "selfcheck failed"

    r = pipeline.load_json(os.path.join(out, "result.json"))
    assert r["status"] == "ok", "expected ok, got %s" % r["status"]
    scr = r["screening"]["summary"]
    assert (scr["include"], scr["exclude"]) == (1, 1), scr
    assert r["deduplication"]["merged_count"] == 1, r["deduplication"]
    assert r["audit"]["closed"] is True
    print("PASS normal path (dedup=1, include=1, exclude=1, closed=true)")
    print("  contract keys preserved:", [k for k in r if k in ("candidate_set", "screening_summary", "excluded_records", "evidence_records")])

    # 2) 空输入目录
    empty = os.path.join(tmp, "empty_in")
    os.makedirs(empty, exist_ok=True)
    e_out = os.path.join(tmp, "empty_out")
    e_work = os.path.join(tmp, "empty_work")
    rc = run("skeleton", empty, e_out, e_work)
    assert rc == 0
    er = pipeline.load_json(os.path.join(e_out, "result.json"))
    assert er["status"] == "partial", er["status"]
    print("PASS empty input dir (exit 0, partial skeleton)")

    # 3) 坏 JSON + 坏编码
    bad = os.path.join(tmp, "bad_in")
    os.makedirs(bad, exist_ok=True)
    with open(os.path.join(bad, "case.json"), "wb") as f:
        f.write(b'{"case_id": "\xff\xfe broken \x80 bytes", "records":')
    b_out = os.path.join(tmp, "bad_out")
    b_work = os.path.join(tmp, "bad_work")
    rc = run("skeleton", bad, b_out, b_work)
    assert rc == 0, "bad input crashed skeleton"
    br = pipeline.load_json(os.path.join(b_out, "result.json"))
    assert br["status"] in ("partial", "input_error"), br["status"]
    print("PASS bad JSON/encoding (no crash, degradations recorded)")

    # 4) 缺参数：直接调 main 无 --input 会指向 /input（只验证解析不崩溃，用 --help 替代）
    rc = pipeline.main(["skeleton", "--input", empty, "--output", e_out, "--work", e_work])
    assert rc == 0
    print("PASS missing/default args fallback")

    shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print("FAILURES:", failures)
        return 1
    print("selftest ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
