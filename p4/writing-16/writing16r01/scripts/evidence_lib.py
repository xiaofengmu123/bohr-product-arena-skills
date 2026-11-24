#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科研成果转化写作 Skill 共享库（仅 Python 标准库）。

职责：容错读取、文件发现、事实清单（数字/单位/引用/图表/关键论断）、
证据映射、论断强度分级、时间戳。所有函数确定性、无随机数、无网络。
"""
import datetime
import json
import os
import re
from pathlib import Path

MAX_FILE_BYTES = 2_000_000  # 2MB
TEXT_EXTS = {".md", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml", ".tex", ".xml", ".html"}
MAX_FACTS = 2000  # 总事实条目上限，防止超大材料爆炸


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path):
    """容错读取文本，返回 (text_or_None, degradation_or_None)。"""
    p = Path(path)
    if not p.exists():
        return None, "missing"
    try:
        size = p.stat().st_size
    except OSError:
        return None, "unreadable"
    if size > MAX_FILE_BYTES:
        return None, "too_large"
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), None
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), "undecodable"


def relpath(p, base):
    try:
        return os.path.relpath(str(p), str(base))
    except ValueError:
        return str(p)


def discover_files(input_dir):
    """发现输入目录下所有材料文件（不假设固定文件名），返回 Path 列表。"""
    root = Path(input_dir)
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file())


NUMBER_UNIT_RE = re.compile(
    r"(?<![\w.])"
    r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*"
    r"(?:%|‰|ppm|ppb|mg|μg|ug|g|kg|mL|ml|L|μL|ul|cm|mm|μm|um|nm|s|ms|min|h|hr|days?|weeks?|months?|years?|kDa|Da|M|mM|μM|uM|nM|pM|°C|℃|±)?"
    r"(?![A-Za-z])",
    re.IGNORECASE,
)

CITATION_RE = re.compile(r"\[(?:\d{1,3}(?:[-,]\s*\d{1,3})*)\]")

FIGURE_RE = re.compile(r"(?i)\b(?:Fig(?:ure)?\.?|Table|图|表|附图|附表)\s*\d+[a-z]?")

STRENGTH_PATTERNS = {
    "causal": ["导致", "引起", "造成", "诱发", "因果", "causal", "causation", "causative", "due to", "result in", "leads to", "lead to", "drive"],
    "firm": ["证实", "证明", "表明", "显示", "发现", "确认", "demonstrat", "confirm", "found", "showed", "show that", "prove"],
    "tentative": ["初步", "可能", "或许", "似乎", "暗示", "提示", "相关", "关联", "倾向于", "preliminary", "suggest", "may", "might", "indicate", "associat", "correlat", "potentially"],
}

NEG_CAUSAL_RE = re.compile(r"(?:非|不是|并非|无|without|no\s+|not\s+)\s*(?:因果|causal|causation)")


def classify_strength(sentence):
    """按动词/限定语给一句话分级，返回 (level, matched_word)。

    优先级：显式否定因果 → 因果 → 限定词（初步/可能/相关等）→ 确定动词。
    这样"初步表明"判为 tentative，"非因果"不误判为 causal。
    """
    text = sentence.lower() if isinstance(sentence, str) else ""
    if not text:
        return "unmarked", None
    neg = NEG_CAUSAL_RE.search(text)
    if neg:
        return "tentative", neg.group(0)
    for w in STRENGTH_PATTERNS["causal"]:
        if w in text:
            return "causal", w
    for w in STRENGTH_PATTERNS["tentative"]:
        if w in text:
            return "tentative", w
    for w in STRENGTH_PATTERNS["firm"]:
        if w in text:
            return "firm", w
    return "unmarked", None


def split_paragraphs(text):
    return [t.strip() for t in re.split(r"\n\s*\n|\r\n\s*\r\n", text) if t.strip()]


def build_fact_inventory(paths, input_dir):
    """从材料抽取可复核的事实条目（确定性）。返回 (entries, degradations)。"""
    entries = []
    degradations = []
    for p in paths:
        if p.suffix.lower() not in TEXT_EXTS:
            continue
        text, deg = read_text(p)
        if deg:
            degradations.append({"file": relpath(p, input_dir), "issue": deg})
            continue
        paras = split_paragraphs(text)
        for i, para in enumerate(paras):
            locator = f"{relpath(p, input_dir)}#p{i+1}"
            snippet = para[:400]
            for m in NUMBER_UNIT_RE.finditer(para):
                if len(entries) >= MAX_FACTS:
                    return entries, degradations
                entries.append({"id": f"E{len(entries)+1}", "kind": "number",
                                "value": m.group(0).strip(), "locator": locator, "quote": snippet})
            for m in CITATION_RE.finditer(para):
                if len(entries) >= MAX_FACTS:
                    return entries, degradations
                entries.append({"id": f"E{len(entries)+1}", "kind": "citation",
                                "value": m.group(0), "locator": locator, "quote": snippet})
            for m in FIGURE_RE.finditer(para):
                if len(entries) >= MAX_FACTS:
                    return entries, degradations
                entries.append({"id": f"E{len(entries)+1}", "kind": "figure",
                                "value": m.group(0), "locator": locator, "quote": snippet})
            for sent in re.split(r"(?<=[。！？.!?])\s*", para):
                level, matched = classify_strength(sent)
                if matched:
                    if len(entries) >= MAX_FACTS:
                        return entries, degradations
                    s = sent.strip()[:300]
                    entries.append({"id": f"E{len(entries)+1}", "kind": "claim",
                                    "value": s, "locator": locator, "quote": s, "strength": level})
    return entries, degradations
