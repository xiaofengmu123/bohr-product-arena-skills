#!/usr/bin/env python3
"""事实/术语/引用抽取与论断强度分级（仅标准库，确定性、无网络）。

供 main.py / check.py 导入，也可独立运行：
  python3 -B scripts/extract.py <文本文件>
"""
import json
import re
import sys
from pathlib import Path

SIZE_LIMIT = 2_000_000
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")


def read_text(path, max_bytes=SIZE_LIMIT):
    """按 utf-8-sig 优先读取并逐级降级；超过上限或不可读返回 None。"""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    if len(raw) > max_bytes:
        return None
    for enc in ENCODINGS:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def split_lines(text):
    return text.splitlines()


# ---------------------------------------------------------------- 抽取规则
# 注：左右边界用 ASCII 字母数字（而非 \b/\w），避免 CJK 字符与全角标点
# 被当作“单词字符”导致 %、周、例 等后接中文时匹配失败。
_NUMBER_UNIT = re.compile(
    r"(?<![0-9A-Za-z])(?:\d+(?:\.\d+)?|\.\d+)\s*(?:%|％|‰|mg|μg|µg|ng|pg|g|kg|"
    r"ml|μl|µl|nl|L|mM|μM|µM|nM|pM|M|mol|mmol|μmol|µmol|nmol|"
    r"mm|μm|µm|nm|cm|m|km|s|ms|min|h|hr|hour|day|week|month|year|"
    r"°C|℃|K|Hz|kHz|MHz|GHz|eV|keV|MeV|GeV|J|kJ|W|kW|MW|"
    r"×|x|fold|times|IU|U|bp|kb|Mb|Gb|Da|kDa|"
    r"周|天|日|年|月|岁|例|名|只|个|倍)(?![0-9A-Za-z])",
    re.IGNORECASE,
)

_SAMPLE_SIZE = re.compile(
    r"(?<![0-9A-Za-z])(?:n|N)\s*=\s*\d+(?:\s*[-,–]\s*\d+)?|"
    r"\d+\s*(?:patients|subjects|samples|participants|cases|mice|rats|cells|"
    r"individuals|specimens|例|名|只|个样本)(?![0-9A-Za-z])",
    re.IGNORECASE,
)

_STATISTIC = re.compile(
    r"(?:(?:95%|99%)\s*CI|CI)\s*[=:：]?\s*\[?[\d.,\-––\s]+\]?|"
    r"(?<![0-9A-Za-z])p\s*[<>=≤≥]\s*0?\.\d+|"
    r"(?<![0-9A-Za-z])p\s*=\s*0?\.\d+|"
    r"(?:\bOR\b|\bHR\b|\bRR\b|\bIRR\b|\bAUC\b)\s*=\s*[\d.]+(?:\s*\([^)]*\))?|"
    r"(?:odds\s+ratio|hazard\s+ratio|risk\s+ratio)\s*=\s*[\d.]+",
    re.IGNORECASE,
)

_CITATION = re.compile(
    r"\[(\d+(?:\s*[-,–]\s*\d+)*)\]|"
    r"\(([A-Z][A-Za-z&.,\- ]{0,60}?,?\s*\d{4}[a-z]?)\)|"
    r"\b[A-Z][a-z]+\s+(?:et\s+al\.?|and\s+[A-Z][a-z]+),?\s+\d{4}[a-z]?|"
    r"\b10\.\d{4,9}/[\w./\-()]+|"
    r"\b(?:arXiv|DOI|doi|PMID|pmid)\s*[:=]?\s*\S+",
    re.IGNORECASE,
)

_FIGURE = re.compile(
    r"(?:\bFig(?:ure)?s?\.?|\bTable|图|表|附录图|附录表|"
    r"Supplementary\s+(?:Fig(?:ure)?|Table))\s*\.?\s*\d+[a-zA-Z]?"
)

_ABBR1 = re.compile(r"\b([A-Z][A-Za-z0-9\-]{1,11})\s*[（(]([^()（）]{2,120})[）)]")
_ABBR2 = re.compile(r"([\u4e00-\u9fff][^()（）]{1,120})\s*[（(]([A-Z][A-Za-z0-9\-]{1,11})[）)]")

_SENT_SPLIT = re.compile(r"(?<=[。．！？；])|(?<=[.!?])(?=\s|$)")

STRENGTH_MARKERS = {
    "established": [
        r"\bconfirm(?:ed|s)?\b", r"\bdemonstrat(?:e|es|ed|ing)\b",
        r"\bshow(?:n|s|ed|ing)?\b", r"\bprove(?:n|d|s)?\b",
        r"\bestablish(?:ed|es)?\b", r"\bfinds?\s+that\b",
        "证实", "表明", "证明", "显示", "发现",
    ],
    "causal": [
        r"\bcaus(?:e|es|ed|al|ation|ative)\b", r"\binduc(?:e|es|ed|ing)\b",
        r"\bleads?\s+to\b", r"\bresults?\s+in\b", r"\btrigger(?:s|ed)?\b",
        "导致", "引起", "诱发", "促使",
    ],
    "correlational": [
        r"\bassociat(?:e|es|ed|ion|ions)\b", r"\bcorrelat(?:e|es|ed|ion|ions)\b",
        r"\blinked\b", r"\brelated\b", "相关", "关联", "负相关", "正相关",
    ],
    "preliminary": [
        r"\bpreliminary\b", r"\bsuggest(?:s|ed|ing)?\b", r"\bindicat(?:e|es|ed|ing)\b",
        r"\bmay\b", r"\bmight\b", r"\bcould\b", r"\btentative\b",
        "初步", "提示", "可能", "似乎", "尚需", "有待", "尚不明确", "待验证",
    ],
    "hedged": [
        r"\bpossibly\b", r"\bperhaps\b", r"\blikely\b", r"\bunlikely\b",
        r"\bprobably\b", r"\bpotential(?:ly)?\b", "可能", "或许", "大概", "潜在",
    ],
}

_PRIORITY = ["established", "causal", "correlational", "preliminary", "hedged"]


def classify_claim(sentence):
    has = set()
    for cat, pats in STRENGTH_MARKERS.items():
        if any(re.search(p, sentence, re.IGNORECASE) for p in pats):
            has.add(cat)
    if not has:
        return "neutral"
    # 相关与因果同时出现时，取更保守的“相关”
    if "causal" in has and "correlational" in has:
        has.discard("causal")
    # 出现初步/不确定标记时，强论断降级为“初步”，避免把“可能/尚需”写成定论
    if ("preliminary" in has or "hedged" in has) and has & {"established", "causal"}:
        has.discard("established")
        has.discard("causal")
        has.add("preliminary")
    for cat in _PRIORITY:
        if cat in has:
            return cat
    return "neutral"


def _sentences(text):
    return [s for s in _SENT_SPLIT.split(text) if s.strip()]


# ---------------------------------------------------------------- 主入口
def extract_line(line, locator):
    """对单行文本抽取数值/样本/统计/引用/图表，返回 dict 列表。"""
    out = []
    for pat, typ in (
        (_NUMBER_UNIT, "number"),
        (_SAMPLE_SIZE, "sample_size"),
        (_STATISTIC, "statistic"),
        (_CITATION, "citation"),
        (_FIGURE, "figure_ref"),
    ):
        for m in pat.finditer(line):
            txt = m.group(0).strip()
            if txt:
                out.append({"type": typ, "quote": txt, "locator": locator,
                            "context": line.strip()})
    return out


def extract_terms(text, relpath):
    out = []
    for i, line in enumerate(split_lines(text), 1):
        loc = "%s#L%d" % (relpath, i)
        seen = set()
        for m in _ABBR1.finditer(line):
            key = (m.group(1).lower(), m.group(2).strip())
            if key in seen:
                continue
            seen.add(key)
            out.append({"type": "term", "abbr": m.group(1),
                        "expansion": m.group(2).strip(), "quote": m.group(0),
                        "locator": loc})
        for m in _ABBR2.finditer(line):
            key = (m.group(2).lower(), m.group(1).strip())
            if key in seen:
                continue
            seen.add(key)
            out.append({"type": "term", "abbr": m.group(2),
                        "expansion": m.group(1).strip(), "quote": m.group(0),
                        "locator": loc})
    return out


def extract_claims(text, relpath):
    out = []
    for i, line in enumerate(split_lines(text), 1):
        for sent in _sentences(line):
            s = sent.strip()
            if len(s) < 8:
                continue
            strength = classify_claim(s)
            has_cue = bool(_NUMBER_UNIT.search(s) or _CITATION.search(s)
                           or _FIGURE.search(s) or _STATISTIC.search(s)
                           or _SAMPLE_SIZE.search(s))
            if strength != "neutral" or has_cue:
                out.append({"type": "claim", "quote": s,
                            "locator": "%s#L%d" % (relpath, i),
                            "strength": strength})
    return out


def extract_all(text, relpath):
    """把单文件文本抽取为统一 evidence 列表。"""
    out = []
    for i, line in enumerate(split_lines(text), 1):
        out.extend(extract_line(line, "%s#L%d" % (relpath, i)))
    out.extend(extract_terms(text, relpath))
    out.extend(extract_claims(text, relpath))
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("--help", "-h"):
        print("usage: extract.py <file>", file=sys.stderr)
        sys.exit(0 if sys.argv[1:] and sys.argv[1] in ("--help", "-h") else 2)
    txt = read_text(sys.argv[1])
    if txt is None:
        print(json.dumps({"error": "unreadable_or_oversize"}, ensure_ascii=False))
        sys.exit(1)
    print(json.dumps(extract_all(txt, Path(sys.argv[1]).name),
                     ensure_ascii=False, indent=1))
