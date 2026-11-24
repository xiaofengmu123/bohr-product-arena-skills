#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科研成果转化写作 Skill：证据锚点抽取（仅标准库）。

从源材料（manifest.json 记录的文本 / PDF 抽取文本）中抽取"必须保留"的
证据锚点，供 Agent 在改稿时逐项核对、供 verify.py 做一致性检查：

  - 数值（含单位、百分数、样本量 n/N）
  - 引用（[12]、Author, Year、DOI、PMID、URL）
  - 图表引用（Fig. 1 / Table 2 / 图1 / 表2）
  - 缩写（Full Term (ABBR) 首次定义）
  - 比较基准（vs. / 相比 / 对照组 / 与…相比）
  - 论断强度词（因果 vs 相关 / 表明 vs 提示）

只做"抽取与定位"，不做任何事实补造。输出 /app/work/evidence_anchors.json。

用法：
  python3 scripts/evidence.py --work /app/work [--output /app/work/evidence_anchors.json]
"""

import argparse
import json
import os
import re
import sys
from collections import OrderedDict

DEFAULT_WORK = "/app/work"
MAX_TEXT_BYTES = 2 * 1024 * 1024


def utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_text(path):
    data = None
    with open(path, "rb") as fh:
        data = fh.read(MAX_TEXT_BYTES + 1)
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            return data.decode(enc), enc, []
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace"), "latin-1(replace)", ["decode_replace"]


# ---- 抽取规则（正则）----
NUM_WITH_UNIT = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
    r"(?:\s*(?:%|‰|mg|μg|ng|g|kg|mm|cm|m|km|mL|L|μL|s|ms|min|h|hr|days?|years?|℃|°C|°F|Hz|kHz|MHz|GHz|Pa|kPa|MPa|mmHg|M|mM|μM|nM|mol|Da|kDa|kWh|元|万|亿|倍|IU|CFU))?",
    re.IGNORECASE,
)

SAMPLE_SIZE = re.compile(
    r"(?<![A-Za-z0-9_])(?:n|N)\s*=\s*\d{1,9}(?![A-Za-z0-9_])"
)

CITATION = re.compile(
    r"(\[[0-9]{1,3}(?:\s*[-–,]\s*[0-9]{1,3})*\])"           # [12] / [12-14]
    r"|(\b[A-Z][A-Za-z'’\-]+\s+(?:et\s+al\.?|and|&)\s+[A-Z][A-Za-z'’\-]+\.?\s*,?\s*(?:19|20)\d{2}[a-z]?\b)"  # Author et al., 2020
    r"|(\b(?:19|20)\d{2}[a-z]?\))"                            # 括号年份
    r"|(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)"                    # DOI
    r"|(PMID[:# ]?\s*\d{5,9})"                                # PMID
    r"|(https?://(?:dx\.)?doi\.org/[^\s)\]>,;]+)"            # DOI URL
)

FIGURE_REF = re.compile(
    r"(?<![A-Za-z])((?:Fig(?:ure)?\.?|Table|Fig|图|表|Figure)\s*\.?\s*[0-9IVXLC]+(?:[a-z]?|[A-Z]?))",
    re.IGNORECASE,
)

ABBR_DEF = re.compile(
    r"([A-Z][A-Za-z0-9\-/ ]{1,60}?)\s*\(([A-Z]{2,12})\)"
)

COMPARISON = re.compile(
    r"(vs\.?|versus|compared\s+with|compared\s+to|相对|相比|对照|与.{0,12}相比|较.{0,10}(?:提高|降低|增加|减少))",
    re.IGNORECASE,
)

STRENGTH_WEAK = re.compile(
    r"(可能|或许|提示|初步|尚需|有待|相关|关联|可能与|suggest|may|could|might|"
    r"potentially|preliminary|associated|correlat|warrants|needs?\s+further)",
    re.IGNORECASE,
)
STRENGTH_STRONG = re.compile(
    r"(证实|证明|必然|导致|引起|确立|因果|确定|proves?|proven|causes?|caused|"
    r"establishes?|demonstrat(?:e|es|ed)|confirms?|definitiv)",
    re.IGNORECASE,
)


def _matches(pattern, text):
    out = []
    for m in pattern.finditer(text):
        start = m.start()
        snippet = text[max(0, start - 60): start + 120].replace("\n", " ")
        out.append({"match": m.group(0), "offset": start, "context": snippet})
    return out


def extract_anchors(text, locator):
    """对一段文本做全部锚点抽取，返回统一结构。"""
    return {
        "locator": locator,
        "chars": len(text or ""),
        "numbers": _matches(NUM_WITH_UNIT, text),
        "sample_sizes": _matches(SAMPLE_SIZE, text),
        "citations": _matches(CITATION, text),
        "figure_refs": _matches(FIGURE_REF, text),
        "abbreviations": _matches(ABBR_DEF, text),
        "comparisons": _matches(COMPARISON, text),
        "strength_weak": _matches(STRENGTH_WEAK, text),
        "strength_strong": _matches(STRENGTH_STRONG, text),
    }


def dedup(items):
    seen = OrderedDict()
    for it in items:
        key = it["match"]
        if key not in seen:
            seen[key] = it
    return list(seen.values())


def load_manifest(work_dir):
    path = os.path.join(work_dir, "manifest.json")
    if not os.path.exists(path):
        return None, "manifest_not_found:%s" % path
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh), None


def source_texts(manifest):
    """从 manifest 中提取可读源材料文本，返回 [(locator, text)]。

    排除 case.json 与 task.md（它们是任务指令，不是科研源材料），
    避免把篇幅上限、引用编号等任务元数据误当事实锚点。
    """
    skip = set()
    for key in ("case_json", "task_md"):
        p = manifest.get(key)
        if p:
            skip.add(os.path.normpath(p))
    out = []
    for rec in manifest.get("files", []):
        if os.path.normpath(rec["path"]) in skip:
            continue
        if rec.get("kind") == "text":
            text, _enc, _errs = read_text(rec["path"])
            out.append((rec["rel"] + "#text", text))
        elif rec.get("extracted_text_path"):
            ep = rec["extracted_text_path"]
            if os.path.exists(ep):
                text, _enc, _errs = read_text(ep)
                out.append((rec["rel"] + "#pdf", text))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="证据锚点抽取")
    ap.add_argument("--work", default=DEFAULT_WORK, help="工作目录（默认 /app/work）")
    ap.add_argument("--output", default=None, help="输出 JSON 路径（默认 <work>/evidence_anchors.json）")
    args = ap.parse_args(argv)

    manifest, err = load_manifest(args.work)
    if err:
        sys.stderr.write("[evidence] %s\n" % err)
        return 2

    anchors = {"generated_at": utcnow(), "case_id": manifest.get("case_id"), "sources": []}
    for locator, text in source_texts(manifest):
        a = extract_anchors(text, locator)
        # 各类目跨来源去重，保留来源与上下文
        anchors["sources"].append(a)

    # 汇总去重视图，方便 Agent/verify 快速核对
    summary = {k: [] for k in ("numbers", "sample_sizes", "citations", "figure_refs",
                               "abbreviations", "comparisons", "strength_weak", "strength_strong")}
    for src in anchors["sources"]:
        for key in summary:
            for it in src[key]:
                summary[key].append({**it, "locator": src["locator"]})
    for key in summary:
        summary[key] = dedup(summary[key])
    anchors["summary"] = summary

    out_path = args.output or os.path.join(args.work, "evidence_anchors.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(anchors, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, out_path)

    sys.stderr.write("[evidence] %d sources -> %s\n" % (len(anchors["sources"]), out_path))
    for key, items in summary.items():
        sys.stderr.write("[evidence] %-14s %d\n" % (key, len(items)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
