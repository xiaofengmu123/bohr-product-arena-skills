#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""evidence.py — 事实与来源映射生成（仅标准库，确定性）。

从草稿中抽取带引用标记/数字/术语的论断，按句归并，输出 fact_source_map 候选；
结合 sources.json（Agent 在 P1 生成的源文件清单）把 [n] 引用映射到具体源文件。
未带任何引用标记且含数字的论断标记为 missing_source，由 Agent 复核补证。

用法：
  python3 scripts/evidence.py --draft /app/output/deliverables/draft.md \
      --sources /app/work/sources.json --out /app/work/evidence.json
"""

import argparse
import json
import re
import sys

CITATION_RE = re.compile(r"\[(\d+(?:[,\-]\s*\d+)*)\]|\(?\b([A-Z][A-Za-z]+(?:\s+(?:et\s+al\.?|and|[A-Z][A-Za-z]+))?,\s*\d{4}[a-z]?)\)?|10\.\d{4,9}/[^\s)\]>,;]+")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％)?")


def read(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return fh.read()


def load_sources(path):
    if not path:
        return []
    try:
        data = json.loads(read(path))
    except (ValueError, OSError):
        return []
    if isinstance(data, dict):
        data = data.get("sources", [])
    return [s for s in data if isinstance(s, dict)]


def split_sentences(text):
    # 按句末标点切分，保留原始句子；去空行
    parts = re.split(r"(?<=[。！？；\n])", text)
    out = []
    for s in parts:
        s = s.strip()
        if s:
            out.append(s)
    return out


def detect_citations(sentence):
    """返回 (refs, has_citation_marker)。refs 形如 [1,3] 或作者年份。"""
    refs = []
    for m in CITATION_RE.finditer(sentence):
        refs.append(m.group(0))
    return refs, bool(refs)


def classify(sentence):
    low = sentence
    if re.search(r"\d+(?:\.\d+)?\s*(?:%|％)", low) or re.search(r"\b\d+\b", low):
        return "number"
    if "（" in low and re.search(r"[A-Za-z]", low):
        return "term"
    return "claim"


def run(draft_path, sources_path):
    text = read(draft_path)
    source_by_idx = {}
    for i, s in enumerate(load_sources(sources_path), start=1):
        source_by_idx[str(i)] = {"id": s.get("id", "src-%03d" % i),
                                 "path": s.get("path", "?"),
                                 "type": s.get("type", "?")}

    entries = []
    for idx, sent in enumerate(split_sentences(text), start=1):
        refs, has_cite = detect_citations(sent)
        if not (refs or NUMBER_RE.search(sent)):
            continue
        entry = {
            "id": "F%03d" % idx,
            "type": classify(sent),
            "content": sent,
            "locator": None,
            "quote": sent,
            "status": "mapped" if has_cite else "missing_source",
            "note": None,
        }
        # 把 [n] 引用映射到源文件
        bracket_nums = re.findall(r"\[(\d+(?:[,\-]\s*\d+)*)\]", sent)
        locators = []
        for grp in bracket_nums:
            for n in re.split(r"[,\-]", grp):
                n = n.strip()
                if n in source_by_idx:
                    locators.append("%s (%s)" % (source_by_idx[n]["path"], source_by_idx[n]["type"]))
        if locators:
            entry["locator"] = "; ".join(sorted(set(locators)))
            entry["status"] = "mapped"
        elif refs:
            # 非编号引用（作者年份/DOI）保留引用串，待 Agent 定位
            entry["locator"] = "citation:" + "; ".join(refs)
            entry["status"] = "mapped"
        if entry["status"] == "missing_source":
            entry["note"] = "含数字/统计但无引用标记，需 Agent 补证来源或删除该断言"
        entries.append(entry)

    return {"fact_source_map": entries,
            "summary": {"total": len(entries),
                        "mapped": sum(1 for e in entries if e["status"] == "mapped"),
                        "missing_source": sum(1 for e in entries if e["status"] == "missing_source")}}


def main(argv=None):
    p = argparse.ArgumentParser(description="事实与来源映射生成")
    p.add_argument("--draft", required=True)
    p.add_argument("--sources", default=None)
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)
    report = run(args.draft, args.sources)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
