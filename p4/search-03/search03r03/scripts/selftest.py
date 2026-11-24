#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py —— 确定性自检：同输入两次运行，决策内容与指纹必须一致。

覆盖：
  1) 双跑确定性：discover → screen → (确定性模拟填充) → finalize 两次，比较决策内容。
  2) 空输入目录：退出码 0，写 insufficient_evidence/input_error，不崩溃。
  3) 坏 JSON：捕获并记录，仍产出合法 result.json，不崩溃。
  4) 缺失元数据（摘要/年份）：对应字段 null、relevance=unknown、confidence=low。

仅依赖标准库；直接 import 同目录 main.py 复用真实流水线。
退出码 0 = 全部通过；非 0 = 存在失败项。
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import main  # noqa: E402


def _mk_case():
    return {
        "case_id": "selftest_case",
        "research_question": "Does anticoagulation improve survival in sepsis-induced coagulopathy?",
        "inclusion_criteria": ["randomized trials", "adult patients with sepsis"],
        "exclusion_criteria": ["pediatric", "animal studies"],
        "output_contract": {
            "ranked_results": "list",
            "method_notes": "string",
            "limitations": "list",
        },
        "papers": [
            {"title": "Anticoagulant therapy for sepsis-associated disseminated intravascular coagulation: a randomized trial",
             "authors": "Smith J, Lee K", "year": 2022, "venue": "Crit Care Med",
             "doi": "10.1000/example.2022.001", "abstract": "A randomized trial of anticoagulation in adult sepsis patients.",
             "type": "journal"},
            {"title": "Anticoagulant therapy for sepsis-associated disseminated intravascular coagulation",
             "authors": "Smith J, Lee K", "year": 2021, "venue": "medRxiv",
             "doi": "10.1101/example.2021.001", "abstract": "Preprint of the randomized trial in sepsis.",
             "type": "preprint"},
            {"title": "Pediatric sepsis management in low-resource settings",
             "authors": "Garcia M", "year": 2019, "venue": "Pediatrics",
             "doi": "10.1000/example.2019.003", "abstract": "Review of pediatric sepsis care.",
             "type": "journal"},
            {"title": "Vitamin C in sepsis: a narrative review",
             "authors": "Chen W", "year": None, "venue": None,
             "doi": None, "abstract": None, "type": "review"},
        ],
    }


def _write_case(root):
    os.makedirs(os.path.join(root, "input"), exist_ok=True)
    with open(os.path.join(root, "input", "case.json"), "w", encoding="utf-8") as f:
        json.dump(_mk_case(), f, ensure_ascii=False, sort_keys=True)
    with open(os.path.join(root, "input", "task.md"), "w", encoding="utf-8") as f:
        f.write("# 文献筛选与智能排序（自检 fixture）\n")
    os.makedirs(os.path.join(root, "input", "materials"), exist_ok=True)
    with open(os.path.join(root, "input", "materials", "note1.txt"), "w", encoding="utf-8") as f:
        f.write("Deterministic fixture material.\n")


def _deterministic_fill(work_dir, output_dir):
    """模拟 Agent 判断：确定性规则填充 result.json（不代表真实模型判断）。"""
    rp = os.path.join(output_dir, "result.json")
    with open(rp, "r", encoding="utf-8") as f:
        result = json.load(f)
    scp = os.path.join(work_dir, "scaffold.json")
    with open(scp, "r", encoding="utf-8") as f:
        scaffold = json.load(f)
    base = {r["record_id"]: r for r in scaffold["records"]}

    for r in result["results"]:
        rid = r["record_id"]
        b = base[rid]
        title = (r.get("title") or "").lower()
        abstract = (r.get("abstract") or "").lower()
        if "pediatric" in title + abstract or "animal" in title + abstract:
            r["relevance"], r["tier"] = "irrelevant", "exclude"
            r["priority_score"], r["confidence"] = 0.05, "high"
        elif "anticoagul" in title + abstract:
            r["relevance"], r["tier"] = "direct", "retain"
            r["priority_score"] = round(0.9 - 0.1 * b["is_preprint"], 4)
            r["confidence"] = "high"
        elif not r.get("abstract") or not r.get("year"):
            r["relevance"], r["tier"] = "unknown", "pending"
            r["priority_score"], r["confidence"] = 0.5, "low"
        else:
            r["relevance"], r["tier"] = "indirect", "pending"
            r["priority_score"], r["confidence"] = 0.5, "medium"
        r["reasons"] = ["确定性命中词面信号 %s" % ",".join(b["lexical_hits"][:3])]
        quote = (r.get("abstract") or r.get("title") or "")[:40]
        r["evidence"] = [{"locator": "case.json#papers", "quote": quote}] if quote else []
        r["criteria_matched"] = ["inclusion_criteria" if r["tier"] == "retain" else "exclusion_criteria"]
    # 填充 output_contract 字段
    result["ranked_results"] = [r["record_id"] for r in result["results"]]
    result["method_notes"] = "deterministic selftest fill"
    result["limitations"] = ["selftest fixture"]
    with open(rp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, sort_keys=True)


def _run_pipeline(root):
    i = os.path.join(root, "input")
    w = os.path.join(root, "work")
    o = os.path.join(root, "output")
    main.run_discover(i, w, o)
    main.run_screen(w, o)
    _deterministic_fill(w, o)
    rep = main.run_finalize(w, o)
    with open(os.path.join(o, "result.json"), "r", encoding="utf-8") as f:
        result = json.load(f)
    return result, rep


def check_determinism():
    root = tempfile.mkdtemp(prefix="selselftest_")
    try:
        _write_case(root)
        # 两次独立运行（不同 work/output 目录，相同 input）
        r1, rep1 = _run_pipeline(root)
        os.makedirs(os.path.join(root, "work2"), exist_ok=True)
        os.makedirs(os.path.join(root, "output2"), exist_ok=True)
        w2 = os.path.join(root, "work2")
        o2 = os.path.join(root, "output2")
        main.run_discover(os.path.join(root, "input"), w2, o2)
        main.run_screen(w2, o2)
        _deterministic_fill(w2, o2)
        rep2 = main.run_finalize(w2, o2)
        with open(os.path.join(o2, "result.json"), "r", encoding="utf-8") as f:
            r2 = json.load(f)

        ok = True
        msgs = []
        if main.canonical(main.decision_content(r1)) != main.canonical(main.decision_content(r2)):
            ok = False
            msgs.append("决策内容两次运行不一致")
        if r1.get("decision_fingerprint") != r2.get("decision_fingerprint"):
            ok = False
            msgs.append("decision_fingerprint 不一致")
        # rank 顺序须确定且连续 1..N
        ranks = [r["rank"] for r in r1["results"]]
        if sorted(ranks) != list(range(1, len(ranks) + 1)):
            ok = False
            msgs.append("rank 未构成 1..N 连续序列：%s" % ranks)
        # tier 顺序 retain -> pending -> exclude
        order = [main.TIER_ORDER[r["tier"]] for r in r1["results"]]
        if any(a > b for a, b in zip(order, order[1:])):
            ok = False
            msgs.append("tier 顺序违反 retain->pending->exclude")
        # 去重候选聚类应把两篇 anticoagulant 归入同组
        groups = main.load_json_file(os.path.join(root, "work", "scaffold.json"))[0]["dedupe_candidates"]
        if not any(len(g["members"]) >= 2 for g in groups):
            ok = False
            msgs.append("未识别出重复/版本聚类")
        return ok, msgs, r1
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_empty_input():
    root = tempfile.mkdtemp(prefix="selftest_empty_")
    try:
        i = os.path.join(root, "input"); os.makedirs(i, exist_ok=True)
        w = os.path.join(root, "work"); o = os.path.join(root, "output")
        result = main.run_discover(i, w, o)
        ok = result.get("status") in ("input_error", "insufficient_evidence")
        ok = ok and os.path.exists(os.path.join(o, "result.json"))
        msg = [] if ok else ["空输入未诚实降级：status=%s" % result.get("status")]
        return ok, msg
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_bad_json():
    root = tempfile.mkdtemp(prefix="selftest_badjson_")
    try:
        i = os.path.join(root, "input"); os.makedirs(i, exist_ok=True)
        with open(os.path.join(i, "case.json"), "w", encoding="utf-8") as f:
            f.write("{ this is not valid json !!!")
        with open(os.path.join(i, "task.md"), "w", encoding="utf-8") as f:
            f.write("# fallback task\n")
        w = os.path.join(root, "work"); o = os.path.join(root, "output")
        result = main.run_discover(i, w, o)
        ok = os.path.exists(os.path.join(o, "result.json"))
        ok = ok and any("case_json_unreadable" in e for e in result.get("errors", []))
        msg = [] if ok else ["坏 JSON 未记录到 errors 或未落盘 result.json"]
        return ok, msg
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_missing_metadata():
    root = tempfile.mkdtemp(prefix="selftest_missing_")
    try:
        _write_case(root)
        i = os.path.join(root, "input"); w = os.path.join(root, "work"); o = os.path.join(root, "output")
        result = main.run_discover(i, w, o)
        # 第 4 篇缺摘要/年份/DOI：应 pending_human 标注，且字段为 null/unknown/low
        rec4 = [r for r in result["results"] if r["record_id"] == "rec_0003"]
        ok = len(rec4) == 1
        r4 = rec4[0] if ok else {}
        ok = ok and r4.get("abstract") is None and r4.get("year") is None
        ok = ok and r4.get("relevance") == "unknown" and r4.get("confidence") == "low"
        ok = ok and any("rec_0003" in p for p in result.get("pending_human", []))
        msg = [] if ok else ["缺失元数据未被诚实标注"]
        return ok, msg
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main_selftest():
    checks = [
        ("双跑确定性（含 rank/tier 顺序/去重聚类）", check_determinism),
        ("空输入目录诚实降级", check_empty_input),
        ("坏 JSON 容错且落盘", check_bad_json),
        ("缺失元数据诚实标注", check_missing_metadata),
    ]
    failures = 0
    print("== selftest: literature-screening-ranking 确定性自检 ==")
    for name, fn in checks:
        try:
            ok, msgs, *_ = fn()
        except Exception as e:  # noqa: BLE001
            ok, msgs = False, ["异常：%s" % e]
        status = "PASS" if ok else "FAIL"
        print("[%s] %s" % (status, name))
        for m in msgs:
            print("      - %s" % m)
        if not ok:
            failures += 1
    print("== 结果：%d/%d 通过 ==" % (len(checks) - failures, len(checks)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main_selftest())
