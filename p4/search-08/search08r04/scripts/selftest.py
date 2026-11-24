#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 离线自检：用临时 fixture 跑通 skeleton/normalize/finalize/selfcheck 的降级路径。

不依赖 /input、/app/output（可在任意机器离线运行）；只用标准库 + core_impl。
退出码：全部 PASS 为 0，任一 FAIL 为 1。演示的是“接口行为”，不冒充正式案例结果。
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl as ci  # noqa: E402


PASS = 0
FAIL = 0


def report(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("[selftest] PASS {}".format(name))
    else:
        FAIL += 1
        print("[selftest] FAIL {} {}".format(name, detail))


def _run(argv):
    import argparse
    return ci.main(argv)


def _make_case(tmp, case_obj=None, records=None, task_text=None):
    inp = os.path.join(tmp, "input")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    os.makedirs(inp)
    if case_obj is not None:
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            json.dump(case_obj, f, ensure_ascii=False)
    if task_text is not None:
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
            f.write(task_text)
    if records is not None:
        with open(os.path.join(inp, "records.jsonl"), "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return inp, out, work


def test_empty_input():
    tmp = tempfile.mkdtemp(prefix="selskel_")
    try:
        inp = os.path.join(tmp, "input")
        os.makedirs(inp)  # 空目录
        out, work = os.path.join(tmp, "output"), os.path.join(tmp, "work")
        code = _run(["skeleton", "--input", inp, "--output", out, "--work", work])
        r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        report("空输入目录 -> insufficient_evidence 且退出码 0",
               code == 0 and r.get("status") == "insufficient_evidence",
               "code={} status={}".format(code, r.get("status")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_bad_json():
    tmp = tempfile.mkdtemp(prefix="selbadjson_")
    try:
        inp, out, work = _make_case(tmp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            f.write("{not valid json")
        code = _run(["skeleton", "--input", inp, "--output", out, "--work", work])
        r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        degs = json.load(open(os.path.join(work, "degradations.json"), encoding="utf-8"))
        has_bad = any("bad_json" in str(d.get("reason", "")) for d in degs)
        report("坏 JSON -> 记录 degradations 不崩溃",
               code == 0 and has_bad and r.get("status") in ("partial", "insufficient_evidence"),
               "code={} status={}".format(code, r.get("status")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_bad_encoding():
    tmp = tempfile.mkdtemp(prefix="selbenc_")
    try:
        inp = os.path.join(tmp, "input")
        os.makedirs(inp)
        # 以 GBK 字节写 case.json（非法 UTF-8），应被 gb18030 回退读取
        case = {"case_id": "enc-1", "output_contract": {"answer": None},
                "research_question": "中文研究问题"}
        raw = json.dumps(case, ensure_ascii=False).encode("gbk")
        with open(os.path.join(inp, "case.json"), "wb") as f:
            f.write(raw)
        out, work = os.path.join(tmp, "output"), os.path.join(tmp, "work")
        code = _run(["skeleton", "--input", inp, "--output", out, "--work", work])
        r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        report("坏编码（GBK）-> 容错读取且保留契约键",
               code == 0 and r.get("case_id") == "enc-1" and "answer" in r,
               "code={} case_id={}".format(code, r.get("case_id")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_roundtrip_and_dedup():
    tmp = tempfile.mkdtemp(prefix="selround_")
    try:
        case = {
            "case_id": "rt-1",
            "output_contract": {"candidates": [], "screening": {}, "notes": None},
            "research_question": "测试问题",
            "inclusion_criteria": ["纳入标准 A"],
            "exclusion_criteria": ["排除标准 B"],
            "query": "TEST AND QUERY",
            "criteria_version": "v1",
        }
        records = [
            {"title": "Alpha Study", "doi": "10.1000/alpha", "abstract": "Population of adults with disease X."},
            {"title": "Alpha Study", "doi": "10.1000/alpha", "abstract": "Duplicate export of same study."},
            {"title": "Beta Study", "doi": None, "abstract": "Wrong population: children."},
            {"title": "Gamma Study", "doi": "10.1000/gamma", "abstract": "On target intervention."},
        ]
        inp, out, work = _make_case(tmp, case_obj=case, records=records)
        _run(["skeleton", "--input", inp, "--output", out, "--work", work])
        _run(["normalize", "--input", inp, "--output", out, "--work", work])
        # 读唯一记录，按标题映射 record_id（record_id 由确定性排序分配，不写死）
        uniq = json.load(open(os.path.join(work, "unique_records.json"), encoding="utf-8"))
        by_title = {r["title"]: r["record_id"] for r in uniq}
        judgments = {
            "criteria_version": "v1",
            "judgments": [
                {"record_id": by_title["Alpha Study"], "decision": "include", "reason_code": "in_topic_match",
                 "evidence_quote": "Population of adults with disease X."},
                {"record_id": by_title["Beta Study"], "decision": "exclude", "reason_code": "ex_wrong_population",
                 "evidence_quote": "Wrong population: children."},
                {"record_id": by_title["Gamma Study"], "decision": "include", "reason_code": "in_intervention",
                 "evidence_quote": "On target intervention."},
            ],
        }
        with open(os.path.join(work, "judgments.json"), "w", encoding="utf-8") as f:
            json.dump(judgments, f, ensure_ascii=False)
        _run(["finalize", "--input", inp, "--output", out, "--work", work])
        r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        ds = r["deduplication"]["summary"]
        ss = r["screening"]["summary"]
        audit = r["audit"]
        report("去重合并同一研究（retrieved=4 merged=1 unique=3）",
               ds["retrieved"] == 4 and ds["merged"] == 1 and ds["unique"] == 3,
               str(ds))
        report("计数闭合（2 include + 1 exclude == 3 unique）",
               ss["included"] == 2 and ss["excluded"] == 1 and ss["uncertain"] == 0 and audit["count_closed"],
               str(ss))
        report("证据逐字命中均 verified",
               all(x.get("evidence_verified") for x in r["screening"]["records"]))
        code = _run(["selfcheck", "--input", inp, "--output", out, "--work", work])
        report("selfcheck 全部 PASS（退出码 0）", code == 0, "code={}".format(code))
        report("output_contract 字段原样回填", all(k in r for k in ("candidates", "screening", "notes")))
        # 确定性：normalize 重跑结果一致
        d1 = json.load(open(os.path.join(work, "dedup.json"), encoding="utf-8"))
        _run(["normalize", "--input", inp, "--output", out, "--work", work])
        d2 = json.load(open(os.path.join(work, "dedup.json"), encoding="utf-8"))
        report("重复运行去重结果确定", d1["summary"] == d2["summary"], str(d1["summary"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_evidence_mismatch():
    tmp = tempfile.mkdtemp(prefix="selevid_")
    try:
        case = {"case_id": "ev-1", "output_contract": {"result": None},
                "research_question": "Q", "inclusion_criteria": ["A"],
                "exclusion_criteria": ["B"], "query": "Q", "criteria_version": "v1"}
        records = [{"title": "Delta", "doi": "10.0/d", "abstract": "Actual abstract text."}]
        inp, out, work = _make_case(tmp, case_obj=case, records=records)
        _run(["skeleton", "--input", inp, "--output", out, "--work", work])
        _run(["normalize", "--input", inp, "--output", out, "--work", work])
        judgments = {"judgments": [{"record_id": "rec-001", "decision": "include",
                                    "reason_code": "in_topic_match",
                                    "evidence_quote": "Fabricated quote not present."}]}
        with open(os.path.join(work, "judgments.json"), "w", encoding="utf-8") as f:
            json.dump(judgments, f, ensure_ascii=False)
        _run(["finalize", "--input", inp, "--output", out, "--work", work])
        r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        rec = r["screening"]["records"][0]
        has_mismatch = any(d.get("type") == "evidence_mismatch" for d in r["degradations"])
        report("证据片段未命中 -> 记 degradations 且 evidence_verified=false",
               has_mismatch and rec.get("evidence_verified") is False,
               "verified={}".format(rec.get("evidence_verified")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_empty_input()
    test_bad_json()
    test_bad_encoding()
    test_roundtrip_and_dedup()
    test_evidence_mismatch()
    print("[selftest] {} passed, {} failed".format(PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
