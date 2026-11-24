#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill 包自检（selftest）：在临时目录构造三类最小输入，验证
scaffold / render / check 不崩溃、产物合法、契约回填与交叉引用正确。

运行：python3 -B scripts/selftest.py
退出码：0=全部通过，1=任一断言失败。
仅用标准库；不依赖 pdftotext（无 PDF 输入）。
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core  # noqa: E402

FAILURES = []


def check(cond, msg):
    if cond:
        print("  PASS  " + msg)
    else:
        print("  FAIL  " + msg)
        FAILURES.append(msg)


def read_json_quiet(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def run_scaffold(base):
    inp = os.path.join(base, "input")
    out = os.path.join(base, "output")
    work = os.path.join(base, "work")
    os.makedirs(inp, exist_ok=True)
    result, problems = core.scaffold(inp, out, work)
    return inp, out, work, result, problems


def scenario_normal():
    print("== 场景1：正常输入（contract + 一篇 txt 材料 + 完整 IR）==")
    base = tempfile.mkdtemp(prefix="litrev_norm_")
    inp = os.path.join(base, "input")
    os.makedirs(os.path.join(inp, "materials"), exist_ok=True)
    write(os.path.join(inp, "case.json"), json.dumps({
        "case_id": "demo-001",
        "research_question": "某干预对结局 X 是否有效？",
        "output_contract": {
            "type": "object",
            "properties": {
                "research_question": {"type": ["string", "null"]},
                "screening_log": {"type": "array"},
                "evidence_table": {"type": "array"},
                "themes": {"type": "array"},
                "review_draft": {"type": ["string", "null"]},
                "references": {"type": "array"},
            },
        },
    }, ensure_ascii=False))
    write(os.path.join(inp, "materials", "paper_a.txt"),
          "Study A: 干预组与对照组相比，结局 X 显著改善（n=120）。\n结论：干预有效。\n")
    out = os.path.join(base, "output")
    work = os.path.join(base, "work")
    result, problems = core.scaffold(inp, out, work)

    check(result.get("case_id") == "demo-001", "case_id 正确")
    check(result.get("status") == "partial", "初始 status=partial")
    check("screening_log" in result and isinstance(result["screening_log"], list), "contract 字段 screening_log 回填")
    check("evidence_table" in result and isinstance(result["evidence_table"], list), "contract 字段 evidence_table 回填")
    check("research_question" in result and result["research_question"] == "某干预对结局 X 是否有效？", "research_question 回填")
    check("review_draft" in result, "contract 字段 review_draft 存在")
    check("sources" in result and len(result["sources"]) == 1, "inventory 1 条材料")
    check(all(k in result for k in ("status", "uncertainty", "pending_human", "change_log")), "诚实字段齐备")
    check(os.path.isfile(os.path.join(out, "result.json")), "result.json 已落盘")

    # Agent 填 IR 片段
    write(os.path.join(work, "screening.json"), json.dumps({
        "criteria": {"research_object": "干预", "inclusion": ["RCT"], "exclusion": ["未报告结局X"], "dedup_rule": "同 sha256"},
        "decisions": [{"source_id": "S01", "decision": "include", "reason": "RCT 且报告结局 X", "locator": "paper_a.txt"}],
    }, ensure_ascii=False))
    with open(os.path.join(work, "evidence.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "evidence_id": "E01", "source_id": "S01", "locator": "paper_a.txt#L1",
            "quote": "结局 X 显著改善（n=120）", "claim": "干预与结局 X 改善相关",
            "strength": "suggestive", "field": "result", "notes": "",
        }, ensure_ascii=False) + "\n")
    write(os.path.join(work, "claims.json"), json.dumps({
        "paragraphs": [{"heading": "结果", "claims": [
            {"claim_id": "C1", "text": "单一 RCT 提示干预与结局 X 改善相关", "evidence_ids": ["E01"], "strength": "suggestive"},
        ]}],
    }, ensure_ascii=False))
    write(os.path.join(work, "themes.json"), json.dumps({
        "themes": [{"theme_id": "T1", "title": "干预有效性", "summary": "来自 S01", "supporting_sources": ["S01"], "subthemes": [], "boundary": "仅一来源"}],
    }, ensure_ascii=False))
    write(os.path.join(work, "conflicts_gaps.json"), json.dumps({
        "conflicts": [], "gaps": [{"gap_id": "G1", "description": "缺乏多中心证据", "basis": "inventory 仅 S01"}],
    }, ensure_ascii=False))
    meta = read_json_quiet(os.path.join(work, "meta.json"))
    meta["status"] = "ok"
    meta["change_log"].append({"ts": core.now_ts(), "event": "selftest 填全 IR"})
    write(os.path.join(work, "meta.json"), json.dumps(meta, ensure_ascii=False))

    result2, problems2 = core.render_all(work, out)
    check(result2.get("status") == "ok", "填全后 status=ok（无交叉引用问题）")
    check(not problems2, "render 无问题（%s）" % problems2 if problems2 else "render 无问题")
    review = open(os.path.join(out, "review.md"), encoding="utf-8").read()
    check("[E01]" in review, "review.md 含 [E01] 标记")
    check(os.path.isfile(os.path.join(out, "evidence_table.jsonl")), "evidence_table.jsonl 存在")
    check(os.path.isfile(os.path.join(out, "screening_log.json")), "screening_log.json 存在")
    check(core.check(work, out) == [], "check 通过")

    # 故意制造脱节引用，验证 check 能拦截
    write(os.path.join(work, "claims.json"), json.dumps({
        "paragraphs": [{"heading": "结果", "claims": [
            {"claim_id": "C1", "text": "引用不存在的证据", "evidence_ids": ["E99"], "strength": "suggestive"},
        ]}],
    }, ensure_ascii=False))
    problems3 = core.check(work, out)
    check(any("E99" in x for x in problems3), "check 拦截脱节引用 E99")
    shutil.rmtree(base, ignore_errors=True)


def scenario_empty():
    print("== 场景2：空输入目录 ==")
    base = tempfile.mkdtemp(prefix="litrev_empty_")
    inp = os.path.join(base, "input")
    os.makedirs(inp, exist_ok=True)
    out = os.path.join(base, "output")
    work = os.path.join(base, "work")
    result, problems = core.scaffold(inp, out, work)
    check(result.get("status") == "insufficient_evidence", "空输入 status=insufficient_evidence")
    check(os.path.isfile(os.path.join(out, "result.json")), "result.json 仍落盘")
    shutil.rmtree(base, ignore_errors=True)


def scenario_bad_case():
    print("== 场景3：坏 case.json（容错不崩溃）==")
    base = tempfile.mkdtemp(prefix="litrev_bad_")
    inp = os.path.join(base, "input")
    os.makedirs(inp, exist_ok=True)
    write(os.path.join(inp, "case.json"), "{ not valid json")
    out = os.path.join(base, "output")
    work = os.path.join(base, "work")
    result, problems = core.scaffold(inp, out, work)
    check(os.path.isfile(os.path.join(out, "result.json")), "result.json 落盘且可解析")
    check(result.get("status") in core.ALLOWED_STATUS, "status 合法")
    check("sources" in result, "sources 字段存在")
    shutil.rmtree(base, ignore_errors=True)


def main():
    print("lit-review-synthesis skill 自检开始")
    scenario_normal()
    scenario_empty()
    scenario_bad_case()
    print("")
    if FAILURES:
        print("自检失败 %d 项" % len(FAILURES))
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("自检全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
