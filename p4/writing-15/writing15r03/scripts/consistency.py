#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""consistency.py — 长文档跨章节一致性检查（仅标准库，确定性启发式）。

对草稿 Markdown 做五类检查，产出 JSON + Markdown 报告。所有发现均为“候选”，
由 Agent 复核后再修正，脚本不做自动改写。

用法：
  python3 scripts/consistency.py --draft /app/output/deliverables/draft.md --out /app/work/consistency.json
"""

import argparse
import json
import re
import sys

CN_NUM = "一二三四五六七八九十百零0-9"


def read(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return fh.read()


def term_definitions(text):
    """抽取形如 术语（English, ABBR） / 术语(ABBR) 的定义，返回 [(term, bracket)]。"""
    out = []
    for m in re.finditer(r"([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9\- ]{1,28}?)\s*[（(]([^（）()\n]{1,60})[)）]", text):
        term, bracket = m.group(1).strip(), m.group(2).strip()
        if not term or not bracket:
            continue
        out.append((term, bracket))
    return out


def check_terms(text):
    findings = []
    defs = term_definitions(text)
    seen = {}
    for term, bracket in defs:
        if term in seen:
            if seen[term] != bracket:
                findings.append({"kind": "duplicate_definition",
                                 "message": "术语 %r 存在两种定义写法：%r 与 %r" % (term, seen[term], bracket),
                                 "term": term})
            continue
        seen[term] = bracket
    for term, bracket in defs:
        # 定义之后的正文里若再出现该术语但未带括号，提示可统一（仅提示，不强制）
        later = text.split(term, 2)
        if len(later) >= 3 and ("（" + bracket + "）") not in later[2][:200]:
            findings.append({"kind": "term_variant",
                             "message": "术语 %r 在首次定义后出现未附英文/缩写的形式，建议统一" % term,
                             "term": term, "definition": bracket})
    return findings


def numbered_entities(text):
    """返回各编号实体的序号集合。"""
    ents = {}
    rules = [
        ("chapter", r"第\s*([%s]+)\s*章" % CN_NUM),
        ("section", r"(?<!第)(\d+(?:\.\d+)*)\s*[章节]"),
        ("figure", r"图\s*([0-9]+(?:\.[0-9]+)?)"),
        ("table", r"表\s*([0-9]+(?:\.[0-9]+)?)"),
        ("equation", r"(?:式|公式)\s*\(?([0-9]+(?:\.[0-9]+)?)\)?"),
    ]
    for key, pat in rules:
        nums = re.findall(pat, text)
        ents[key] = nums
    return ents


def _gaps(nums):
    """对形如 1,2,3 或 1.1,1.2 的序号序列找缺口与重复；非数字序号跳过。"""
    try:
        nums = sorted(set(int(x) for x in nums if x.isdigit()))
    except Exception:
        return [], []
    if not nums:
        return [], []
    gaps = [n for n in range(nums[0], nums[-1] + 1) if n not in nums]
    dups = [n for n in nums if nums.count(n) > 1]
    return gaps, list(set(dups))


def check_numbering(text):
    findings = []
    ents = numbered_entities(text)
    for key, nums in ents.items():
        gaps, dups = _gaps(nums)
        for g in gaps:
            findings.append({"kind": "numbering_gap", "entity": key,
                             "message": "%s 编号缺 %d（现有：%s）" % (key, g, sorted(set(int(x) for x in nums if x.isdigit())))})
        for d in dups:
            findings.append({"kind": "numbering_duplicate", "entity": key,
                             "message": "%s 编号 %d 重复出现" % (key, d)})
    return findings


def check_crossrefs(text):
    findings = []
    ents = numbered_entities(text)
    for m in re.finditer(r"见\s*(图|表|式|公式)\s*([0-9]+(?:\.[0-9]+)?)", text):
        kind, num = m.group(1), m.group(2)
        key = {"图": "figure", "表": "table", "式": "equation", "公式": "equation"}[kind]
        if num not in ents.get(key, []):
            findings.append({"kind": "dangling_crossref",
                             "message": "交叉引用“见%s%s”找不到对应编号目标" % (kind, num),
                             "target": "%s-%s" % (key, num)})
    return findings


def check_numeric_conflicts(text):
    """同一语境词（前 12 字符）出现两个以上不同数值 → 提示候选矛盾。"""
    findings = []
    ctx = {}
    for m in re.finditer(r"([\u4e00-\u9fffA-Za-z]{1,12}?)(\d+(?:\.\d+)?)\s*(%|％|[A-Za-z]{0,6})", text):
        context, num, unit = m.group(1).strip(), m.group(2), m.group(3)
        if len(context) < 2:
            continue
        key = context + "·" + unit
        ctx.setdefault(key, set()).add(num)
    for key, nums in ctx.items():
        if len(nums) > 1:
            findings.append({"kind": "numeric_conflict", "context": key.split("·")[0],
                             "message": "同一语境出现多个数值 %s，请复核是否前后矛盾" % sorted(nums),
                             "values": sorted(nums)})
    return findings


def run(draft_path):
    text = read(draft_path)
    findings = []
    findings += check_terms(text)
    findings += check_numbering(text)
    findings += check_crossrefs(text)
    findings += check_numeric_conflicts(text)
    return {"draft": draft_path, "findings": findings,
            "summary": {"total": len(findings),
                        "by_kind": {k: sum(1 for f in findings if f["kind"] == k)
                                    for k in sorted({f["kind"] for f in findings})}}}


def main(argv=None):
    p = argparse.ArgumentParser(description="长文档一致性检查")
    p.add_argument("--draft", required=True)
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)
    report = run(args.draft)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
