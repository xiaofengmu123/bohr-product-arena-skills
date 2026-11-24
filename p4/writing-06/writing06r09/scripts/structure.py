#!/usr/bin/env python3
"""结构分析辅助：分段、候选论断、近义冗余检测、逐字定位。

用法：
  python scripts/structure.py <file> [--out /app/work/structure.json]

读取 UTF-8（含 BOM）文本，输出 JSON 报告：segments（章节/段落/句子定位）、
claims（候选论断句）、redundancy（近义句/段落候选对）、sections、文本统计。
候选结果仅供模型核对，不自动判定缺陷。
"""
import argparse
import json
import os
import re
import sys

# 仅用于浮现"候选"锚点，模型必须逐条核对原文后确认；不自动下结论。
CLAIM_MARKERS = [
    "结果表明", "证实", "表明", "证明", "提示", "意味着", "导致", "引起",
    "显著", "相关", "因果", "结论", "we find", "we show", "suggests",
    "indicates", "demonstrates", "significant", "conclude", "result",
    "implies", "therefore", "thus", "hence", "because", "however",
]
CAUSAL_MARKERS = [
    "导致", "引起", "因为", "由于", "使得", "因此", "从而", "causes",
    "caused", "leads to", "due to", "because", "therefore", "thus",
]
STRONG_MARKERS = [
    "必须", "一定", "必然", "所有", "唯一", "证明", "证实", "establish",
    "prove", "must", "always", "never", "definitively", "首次", "首个",
]


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def split_paragraphs(text):
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def split_sentences(para):
    return [s.strip() for s in re.split(r"(?<=[。！？.!?])\s*", para) if s.strip()]


def detect_sections(text):
    sections = []
    for i, line in enumerate(text.splitlines()):
        m = re.match(r"^(#{1,6})\s+(.+)$", line) or re.match(r"^\\(section|subsection|chapter)\{([^}]*)\}", line)
        if m:
            sections.append({"line": i + 1, "title": m.group(2).strip()})
    return sections


def norm_tokens(s):
    # 拉丁/数字按词切分；CJK 逐字切分，避免整句被折叠成单个 token。
    return set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", s.lower()))


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def detect_redundancy(paras, threshold=0.7):
    pairs = []
    toks = [norm_tokens(p) for p in paras]
    for i in range(len(paras)):
        for j in range(i + 1, len(paras)):
            if len(toks[i]) < 8 or len(toks[j]) < 8:
                continue
            sim = jaccard(toks[i], toks[j])
            if sim >= threshold:
                pairs.append({
                    "a": {"paragraph": i, "preview": paras[i][:120]},
                    "b": {"paragraph": j, "preview": paras[j][:120]},
                    "jaccard": round(sim, 3),
                })
    pairs.sort(key=lambda x: -x["jaccard"])
    return pairs


def extract_claims(paras):
    claims = []
    for pi, para in enumerate(paras):
        for si, sent in enumerate(split_sentences(para)):
            low = sent.lower()
            hits = [m for m in CLAIM_MARKERS if m in low]
            strong = [m for m in STRONG_MARKERS if m in low]
            causal = [m for m in CAUSAL_MARKERS if m in low]
            if hits:
                claims.append({
                    "locator": "para[%d] sent[%d]" % (pi, si),
                    "paragraph": pi,
                    "sentence": si,
                    "text": sent,
                    "markers": hits,
                    "strong_markers": strong,
                    "causal_markers": causal,
                })
    return claims


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--out", default="/app/work/structure.json")
    a = ap.parse_args(argv)

    text = read_text(a.file)
    paras = split_paragraphs(text)
    sentences = []
    for p in paras:
        sentences.extend(split_sentences(p))

    claims = extract_claims(paras)[:200]
    redundancy = detect_redundancy(paras)[:100]
    report = {
        "file": a.file,
        "chars": len(text),
        "paragraphs": len(paras),
        "sentences": len(sentences),
        "sections": detect_sections(text),
        "claims": claims,
        "redundancy": redundancy,
    }

    out = a.out
    out_dir = os.path.dirname(out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "ok": True,
        "out": out,
        "file": report["file"],
        "chars": report["chars"],
        "paragraphs": report["paragraphs"],
        "sentences": report["sentences"],
        "sections": len(report["sections"]),
        "n_claims": len(claims),
        "n_redundancy": len(redundancy),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
