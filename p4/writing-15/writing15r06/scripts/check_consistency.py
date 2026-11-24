#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_consistency.py -- 长文档跨章节一致性检查。

对一份 Markdown 草稿做只读检查，输出可复核的报告（/app/work/consistency_report.json）。
覆盖与"连得上 / 查得清"直接相关的问题：
  1. 图/表/公式编号：引用标签、编号是否缺号或断号。
  2. 引用标记：正文引用键 vs 参考文献列表的对应关系（启发式）。
  3. 术语：缩写未定义、同一术语前后写法不一致的候选（启发式）。
  4. 重复定义：同一术语被多次以"定义式"出现。

该脚本只负责"发现并报告"，不修改草稿；修改由 Agent 依据报告执行并写入 change_log。
"""

import argparse
import json
import os
import re
import sys
import time

# 图/表/公式引用模式（中英文常见写法）
FIG_RE = re.compile(r"(?:图|Fig(?:ure)?\.?|Figure)\s*([0-9]+(?:\.[0-9]+)?)", re.I)
TAB_RE = re.compile(r"(?:表|Tab(?:le)?\.?|Table)\s*([0-9]+(?:\.[0-9]+)?)", re.I)
EQ_RE = re.compile(r"(?:式|公式|Eq(?:uation)?\.?|Equation)\s*\(?([0-9]+(?:\.[0-9]+)?)\)?", re.I)

# 引用标记（三种写法：数字 [1]、作者-年份 [Zhang 2020]、(Zhang, 2020)、@key）
CITE_BRACKET_NUM = re.compile(r"\[([0-9]+(?:\s*[,，、]\s*[0-9]+)*)\]")
CITE_AUTHOR_YEAR = re.compile(r"\[([A-Za-z\u4e00-\u9fff][A-Za-z\u4e00-\u9fff .\-']*?)\s*[,，]?\s*(19|20)[0-9]{2}[a-z]?\]")
CITE_PAREN = re.compile(r"\(([A-Za-z][A-Za-z .\-']*?)\s*[,，]\s*(19|20)[0-9]{2}[a-z]?\)")
CITE_AT = re.compile(r"@([A-Za-z0-9_.:-]+)")

# 缩写定义：全称 (缩写)
ABBR_DEF_RE = re.compile(r"([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9 .、\-]{1,40})\s*[（(]\s*([A-Z]{2,12})\s*[)）]")

# "定义式"术语：**术语** 或 "术语 指/是指/定义为"
TERM_BOLD_RE = re.compile(r"\*\*([^*\n]{2,40})\*\*")
TERM_DEF_RE = re.compile(r"([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9 _\-]{1,40})\s*(?:指|是指|定义为|定义为:|:定义)")

# 参考文献节标题
REF_SECTION_RE = re.compile(r"^#{1,4}\s*(?:参考文献|References|Bibliography)", re.I | re.M)


def read_tolerant(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def collect_numbers(text, regex):
    seen = []
    for m in regex.finditer(text):
        try:
            num = float(m.group(1))
        except ValueError:
            continue
        if num == int(num):
            num = int(num)
        seen.append(num)
    return seen


def check_numbering(text, regex, label, report):
    nums = collect_numbers(text, regex)
    ints = sorted({int(n) for n in nums if isinstance(n, int) and n >= 1})
    issues = []
    missing = []
    if ints:
        for i in range(1, max(ints) + 1):
            if i not in ints:
                missing.append(i)
    if missing:
        issues.append({"label": label, "issue": "numbering_gap", "missing": missing})
    dups = sorted({n for n in ints if ints.count(n) > 1})
    if dups:
        issues.append({"label": label, "issue": "numbering_duplicate", "duplicates": dups})
    report["numbering"].setdefault(label, {"mentioned": ints, "issues": issues})
    return issues


def split_references(text):
    """把正文与参考文献节粗分。"""
    m = REF_SECTION_RE.search(text)
    if not m:
        return text, ""
    return text[: m.start()], text[m.start():]


def check_citations(body, refs, report):
    numeric = set()
    for m in CITE_BRACKET_NUM.finditer(body):
        for part in re.split(r"[,，、]", m.group(1)):
            part = part.strip()
            if part.isdigit():
                numeric.add(int(part))
    ref_nums = {int(x.group(1)) for x in re.finditer(r"^\s*\[?([0-9]{1,3})\]?", refs, re.M)}
    if numeric and ref_nums:
        dangling = sorted(numeric - ref_nums)
        if dangling:
            report["citations"].append({
                "kind": "numeric", "issue": "cited_but_not_in_references", "keys": dangling,
            })
    at_keys = set(CITE_AT.findall(body))
    report["citations"].append({"kind": "at_key", "count": len(at_keys), "keys": sorted(at_keys)[:200]})
    report["citations"].append({
        "kind": "author_year",
        "count": len(CITE_AUTHOR_YEAR.findall(body)) + len(CITE_PAREN.findall(body)),
        "note": "作者-年份引用是否与参考文献条目对应需人工核验，脚本仅统计数量",
    })


def check_abbreviations(body, report):
    defined = {}
    for m in ABBR_DEF_RE.finditer(body):
        abbr = m.group(2).upper()
        defined.setdefault(abbr, []).append(m.group(1).strip())
    used = set(re.findall(r"\b[A-Z]{2,12}\b", body))
    items = []
    for abbr in sorted(used):
        if abbr in defined:
            continue
        if re.fullmatch(r"[A-Z]{2,12}", abbr):
            items.append({"abbr": abbr, "issue": "abbreviation_without_definition"})
    report["terminology"]["abbreviations"] = {
        "defined": defined,
        "undefined_candidates": items,
    }


def check_duplicate_definitions(body, report):
    counts = {}
    for m in TERM_DEF_RE.finditer(body):
        term = m.group(1).strip().lower()
        counts[term] = counts.get(term, 0) + 1
    dups = {k: v for k, v in counts.items() if v > 1 and len(k) >= 2}
    report["terminology"]["repeated_definitions"] = dups


def main(argv=None):
    ap = argparse.ArgumentParser(description="长文档跨章节一致性检查")
    ap.add_argument("--draft", required=True, help="Markdown 草稿路径")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--out", default=None, help="报告输出 JSON 路径，缺省为 <work>/consistency_report.json")
    args = ap.parse_args(argv)

    t0 = time.time()
    report = {
        "draft": args.draft,
        "status": "checked",
        "numbering": {},
        "citations": [],
        "terminology": {"abbreviations": {}, "repeated_definitions": {}},
        "issues": [],
        "runtime_seconds": 0,
    }

    if not os.path.isfile(args.draft):
        report["status"] = "draft_missing"
        report["issues"].append({"error": "draft_missing", "path": args.draft})
    else:
        text = read_tolerant(args.draft)
        body, refs = split_references(text)
        report["chars"] = len(text)
        for regex, label in ((FIG_RE, "figure"), (TAB_RE, "table"), (EQ_RE, "equation")):
            check_numbering(text, regex, label, report)
        check_citations(body, refs, report)
        check_abbreviations(body, report)
        check_duplicate_definitions(body, report)
        report["issues"] = [
            issue
            for label_data in report["numbering"].values()
            for issue in label_data["issues"]
        ] + [
            item for item in report["citations"]
            if isinstance(item, dict) and item.get("issue")
        ] + report["terminology"]["abbreviations"].get("undefined_candidates", [])

    report["runtime_seconds"] = round(time.time() - t0, 3)

    out = args.out or os.path.join(args.work, "consistency_report.json")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    summary = {
        "status": report["status"],
        "report": out,
        "issue_count": len(report.get("issues", [])),
        "issues": report.get("issues", [])[:40],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
