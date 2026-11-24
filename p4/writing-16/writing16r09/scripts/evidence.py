#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科研成果转化写作 Skill —— 证据与文本辅助函数（仅标准库）。

本文件不直接产出稿件：数字/缩写/论断强度的抽取结果只是"候选"，必须由
执行 Agent 回到原文核对后，才能写入 facts / terms / change_log。任何函数
都不得自动把抽取结果当作事实填入最终产物。
"""

import json
import re

MAX_TEXT_BYTES = 2 * 1024 * 1024


def read_text_any(path, max_bytes=MAX_TEXT_BYTES):
    """容错读取文本：utf-8-sig → utf-8 → latin-1，坏字节不崩溃。

    返回 (text, errors)。超限返回 ("", [too_large 提示])。
    """
    errors = []
    try:
        import os
        size = os.path.getsize(path)
        if size > max_bytes:
            return "", ["material too large (>{0} bytes), skipped".format(max_bytes)]
        with open(path, "rb") as fh:
            raw = fh.read()
        for enc in ("utf-8-sig", "utf-8"):
            try:
                return raw.decode(enc), errors
            except UnicodeDecodeError:
                continue
        errors.append("bad encoding, fallback to latin-1: " + path)
        return raw.decode("latin-1", errors="replace"), errors
    except OSError as exc:
        return "", ["read failed: {0}".format(exc)]


def load_json_any(path):
    """容错读取 JSON：去 BOM、截断容错、尾逗号清理。

    返回 (obj, errors)。失败返回 ({}, [error])。
    """
    text, errors = read_text_any(path)
    if not text.strip():
        return {}, errors + ["empty json: " + path]
    candidates = [text]
    if text.startswith("\ufeff"):
        candidates.append(text[1:])
    for cand in candidates:
        try:
            return json.loads(cand), errors
        except json.JSONDecodeError as exc:
            last = exc
    return {}, errors + ["bad json: {0}".format(last)]


_NUM_RE = re.compile(
    r"(?<![\w.])(?:[-+]?\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
    r"(?:\s*(?:%|‰|ppm|ppb|cm|mm|µm|nm|kg|g|mg|µg|s|ms|min|h|L|mL|µL|mol|M|mM|µM|nM|Hz|kHz|MHz|GHz|°C|℃|K|Pa|MPa|eV|Å|×10[+-]?\d+))?"
)
_ABBR_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9})\b")
_HEDGES = ["可能", "也许", "或许", "初步", "提示", "提示性", "倾向", "尚需", "有待", "建议进一步",
           "提示相关", "观察到相关", "可能相关", "或与", "推测", "似乎", "约", "大约", "接近"]
_BOOSTERS = ["证实", "证明", "表明", "确立", "毫无疑问", "明确", "显著", "决定性", "结论性"]
_CAUSAL = ["导致", "引起", "促进", "抑制", "使得", "造成", "提高", "降低", "引发", "驱动", "因果"]
_CORR = ["相关", "关联", "伴随", "correlat", "association", "associated"]


def sentence_windows(text, max_len=600):
    """按句切分（中英文句号/分号/换行）。

    `.` 只在后接空白或句尾时才当作句号，避免把小数 12.3 / 0.02 切开。
    """
    parts = re.split(r"(?<=[。；;！？!?])\s*|(?<=[.!?])(?=\s|$)|\n+", text)
    return [p.strip() for p in parts if p and len(p.strip()) > 1]


def locate_quote(text, needle, window=160):
    """在 text 中定位 needle，返回 {locator, quote} 的逐字片段。"""
    idx = text.find(needle)
    if idx < 0:
        return None
    lo = max(0, idx - window // 2)
    hi = min(len(text), idx + len(needle) + window // 2)
    snippet = text[lo:hi]
    return {"locator": "char:{0}".format(idx), "quote": snippet.strip()}


def extract_numbers(text):
    """候选数值+单位+所在句。仅供核对，不自动入稿。"""
    out = []
    for i, sent in enumerate(sentence_windows(text)):
        for m in _NUM_RE.finditer(sent):
            val = m.group(0).strip()
            if re.search(r"\d", val):
                out.append({"number": val, "sentence": sent, "sentence_index": i})
    return out


def find_abbreviations(text):
    """候选缩写（全大写 2-10 位），供首次展开核对。"""
    out = []
    for m in _ABBR_RE.finditer(text):
        abbr = m.group(1)
        if abbr not in {"A", "I", "V", "X"}:
            out.append({"abbr": abbr, "offset": m.start()})
    return out


def classify_claim_strength(text):
    """标注论断强度词汇：hedge / booster / causal / correlation。

    返回列表，仅作改写时核对"说得准"用，不替代人工判断。
    """
    out = []
    lowered = text.lower()
    for w in _HEDGES:
        for m in re.finditer(re.escape(w), text):
            out.append({"word": w, "level": "hedge", "offset": m.start()})
    for w in _BOOSTERS:
        for m in re.finditer(re.escape(w), text):
            out.append({"word": w, "level": "booster", "offset": m.start()})
    for w in _CAUSAL:
        for m in re.finditer(re.escape(w), text):
            out.append({"word": w, "level": "causal", "offset": m.start()})
    for w in _CORR:
        for m in re.finditer(re.escape(w), lowered):
            out.append({"word": w, "level": "correlation", "offset": m.start()})
    return out
