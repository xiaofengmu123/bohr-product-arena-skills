#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic evidence extraction for the academic compliance audit skill.

Standard library only. This module never emits a verdict: it builds a source
index and candidate *observations* that the reviewing agent must judge one by
one. For the same input bytes the output is byte-for-byte reproducible.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl",
    ".xml", ".html", ".htm", ".bib", ".tex", ".rst", ".yaml", ".yml",
    ".log", ".ini", ".cfg", ".rtf",
}
PDF_SUFFIXES = {".pdf"}
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_OBS_PER_KIND = 300
MAX_QUOTE_LEN = 400

# ---------------------------------------------------------------------------
# Fallback review facets. The runtime case.json may carry its own concrete
# facets (review_facets / facets / checklist); those take precedence so each
# case is audited against the dimensions it actually specifies.
# ---------------------------------------------------------------------------
DEFAULT_FACETS = [
    {"id": "quality", "name": "通用质量", "definition": "表述清晰、结构完整、术语一致、图表编号与正文一致。", "hint_terms": []},
    {"id": "citation", "name": "引用规范", "definition": "被引文献真实存在、元数据一致、正文论断与引用一一对应、格式统一。", "hint_terms": ["et al", "doi", "http", "references"]},
    {"id": "fact", "name": "事实与数据", "definition": "数值、事实陈述与材料内部或可核对外部来源一致，无张冠李戴。", "hint_terms": []},
    {"id": "method", "name": "方法", "definition": "实验/分析方法交代充分，样本、流程与可复现性描述完整。", "hint_terms": ["method", "protocol", "pipeline", "algorithm", "setup"]},
    {"id": "statistics", "name": "统计", "definition": "统计检验、效应量、置信区间、样本量与显著性表述正确完整。", "hint_terms": ["p-value", "confidence interval", "significance", "anova", "t-test"]},
    {"id": "logic", "name": "逻辑与论证", "definition": "论证链成立，结论有证据支撑，无循环论证或过度推广。", "hint_terms": []},
    {"id": "academic_integrity", "name": "学术诚信", "definition": "数据/图像复用、重复发表、署名、利益冲突、基金与致谢一致性。", "hint_terms": ["conflict", "funding", "grant", "acknowledg", "author"]},
    {"id": "privacy", "name": "隐私与数据合规", "definition": "可识别个人信息、受限数据、未公开数据的授权与脱敏。", "hint_terms": ["patient", "participant", "email", "consent", "id number"]},
    {"id": "copyright", "name": "版权与许可", "definition": "图片、表格、代码、数据集等第三方材料的来源、许可与复用条件。", "hint_terms": ["figure", "reproduced", "adapted", "permission", "license", "copyright"]},
    {"id": "ethics", "name": "伦理审查", "definition": "涉及人/动物的研究说明知情同意、伦理批件与适用范围。", "hint_terms": ["consent", "ethics", "irb", "approval", "animal", "helsinki"]},
    {"id": "submission", "name": "投稿要求", "definition": "目标期刊/会议指南条目与稿件逐项映射，报告缺失、超限与冲突。", "hint_terms": ["journal", "submission", "guidelines", "word limit", "format"]},
]
FACET_BY_ID = {f["id"]: f for f in DEFAULT_FACETS}


def normalize(s: str) -> str:
    """Normalize text for matching: NFC, full-width -> half-width, collapse
    whitespace. This makes quote verification tolerant of line breaks, full-
    width punctuation and width differences without hiding real edits."""
    s = unicodedata.normalize("NFC", s or "")
    out = []
    for ch in s:
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def _tokens(s: str):
    return re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", s or "")


def verify_quote(text: str, quote: str):
    """Return (found, ratio, mode).

    mode is one of exact / token / miss. Token mode requires >=80% of the
    quote's alphanumeric/CJK tokens to appear somewhere in the file, which
    catches minor transcription or hyphenation drift while still rejecting
    quotes that are not supported by the material."""
    tn, qn = normalize(text), normalize(quote)
    if not qn:
        return False, 0.0, "empty"
    if qn in tn:
        return True, 1.0, "exact"
    qt, tt = _tokens(qn), set(_tokens(tn))
    if not qt:
        return False, 0.0, "no_tokens"
    found = sum(1 for t in qt if t in tt)
    ratio = found / len(qt)
    return ratio >= 0.8, ratio, "token"


# ---------------------------------------------------------------------------
# Robust IO
# ---------------------------------------------------------------------------
def read_text_robust(path: Path, max_bytes: int = MAX_TEXT_BYTES):
    """Decode a text file tolerantly; return (text_or_None, error_or_None)."""
    try:
        size = path.stat().st_size
    except OSError as e:
        return None, f"stat failed: {e}"
    if size > max_bytes:
        return None, f"size {size} exceeds {max_bytes} bytes; skipped"
    try:
        raw = path.read_bytes()
    except OSError as e:
        return None, f"read failed: {e}"
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1"):
        try:
            return raw.decode(enc), None
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), "decoded with replacement"


def read_json_robust(path: Path):
    """Return (obj_or_None, error_or_None). Never raises on malformed JSON."""
    text, err = read_text_robust(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except Exception as e:  # noqa: BLE001 - deliberate robustness boundary
        return None, f"invalid JSON: {e}"


# ---------------------------------------------------------------------------
# Candidate observations (no verdicts)
# ---------------------------------------------------------------------------
_P = lambda kind, facet, label, rx: (kind, facet, label, re.compile(rx))

PATTERNS = [
    _P("citation_marker", "citation", "方括号数字引用", r"\[(\d{1,3}(?:\s*[-,，]\s*\d{1,3})*)\]"),
    _P("author_year", "citation", "作者-年份引用", r"\([^()\n]{0,60}?(?:19|20)\d{2}[a-z]?\)"),
    _P("doi_url", "citation", "DOI/URL", r"(?:doi[:\s]?\s*10\.\d{4,9}/[^\s\)\]\"'>,;]+|https?://[^\s\)\]\"'<>]+)"),
    _P("stat_pvalue", "statistics", "p 值", r"p\s*[<>=≤≥]\s*0?\.\d{1,4}"),
    _P("stat_ci", "statistics", "置信区间", r"(?:\d{2})\s*%\s*C\.?I\.?(?:\s*[\[\(][^\]\)]{1,40}[\]\)])?"),
    _P("stat_n", "statistics", "样本量", r"\bn\s*=\s*\d[\d,]*\b"),
    _P("stat_test", "statistics", "统计方法", r"\b(?:t-?test|ANOVA|chi-?square|Fisher|Mann-?Whitney|Wilcoxon|Kaplan-?Meier|Cox|log-?rank|correlation|regression|odds ratio|hazard ratio|relative risk)\b"),
    _P("stat_signif", "statistics", "显著性表述", r"\b(?:significant|non-?significant|statistically significant)\b"),
    _P("ethics_consent", "ethics", "知情同意/批件", r"\b(?:informed consent|consent|IRB|IEC|ethics (?:committee|board|approval)|ethical approval|approval (?:number|No\.?))\b"),
    _P("ethics_subject", "ethics", "人/动物受试", r"\b(?:human subjects?|patients?|participants?|volunteers?|animals?|mice|mouse|rats?|primates?)\b"),
    _P("ethics_norm", "ethics", "伦理规范", r"\b(?:Declaration of Helsinki|3R|ARRIVE|Good Clinical Practice)\b"),
    _P("privacy_email", "privacy", "邮箱", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    _P("privacy_phone", "privacy", "电话", r"(?:\+?\d{1,3}[\s-]?)?\(?\d{3,4}\)?[\s-]?\d{3,4}[\s-]?\d{3,4}"),
    _P("privacy_cnid", "privacy", "身份证号", r"\b\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b"),
    _P("privacy_dob", "privacy", "出生日期", r"\b(?:19|20)\d{2}[年\-/](?:0?[1-9]|1[0-2])[月\-/](?:0?[1-9]|[12]\d|3[01])日?\b"),
    _P("copyright_fig", "copyright", "图/表提及", r"\b(?:Figure|Table|Scheme|Panel)\s+[A-Z]?\d+"),
    _P("copyright_reuse", "copyright", "复用声明", r"\b(?:reproduced|adapted from|modified from|reprinted|courtesy of|with permission)\b"),
    _P("copyright_license", "copyright", "许可类型", r"\b(?:CC BY|CC-BY|Creative Commons|license[d]?|All rights reserved|public domain|GPL|MIT license|BSD|Apache-?2\.0|©|copyright)\b"),
    _P("integrity_conflict", "academic_integrity", "利益冲突", r"\b(?:conflicts? of interest|competing interests?|disclosure statement)\b"),
    _P("integrity_funding", "academic_integrity", "基金/致谢", r"\b(?:funding|grant|acknowledg|supported by|this work was supported|co-first|contributed equally|corresponding author)\b"),
]


def _emit(obs_list, seen, kind, facet, label, path, ln, quote):
    quote = (quote or "").strip()
    if not quote:
        return
    key = (kind, normalize(quote), path)
    if key in seen:
        return
    seen.add(key)
    obs_list.append({
        "id": "obs-%04d" % (len(obs_list) + 1),
        "kind": kind,
        "facet": facet,
        "label": label,
        "locator": "%s#L%d" % (path, ln),
        "quote": quote[:MAX_QUOTE_LEN],
    })


def build_text_index(text: str):
    entries = []
    for i, line in enumerate(text.splitlines(), 1):
        if line.strip():
            entries.append({"line": i, "text": line})
    return entries


# ---------------------------------------------------------------------------
# Citation cross-map (deterministic; candidate evidence only)
# ---------------------------------------------------------------------------
REF_ENTRY_RE = re.compile(r"^\s*\[?(\d{1,3})\]?[\s\.、:：\-–]+\s*(.*)$")
BRACKET_CITE_RE = re.compile(r"\[(\d{1,3}(?:\s*[-,，]\s*\d{1,3})*)\]")
AUTHOR_YEAR_REF_RE = re.compile(r"^[A-Z][A-Za-zÀ-ÿ'\-]+,\s+[A-Z]\.(?:\s+[A-Z]\.)*\s*\(?(?:19|20)\d{2}[a-z]?\)?\.\s")


def _looks_like_ref(rest: str) -> bool:
    low = rest.lower()
    if "doi" in low or "http" in low or "10." in rest or "et al" in low:
        return True
    if re.search(r"[A-Z][A-Za-zÀ-ÿ'\-]+,?\s+[A-Z]\.", rest):
        return True
    if re.search(r"\((?:19|20)\d{2}[a-z]?\)\.", rest):
        return True
    return False


def _parse_num_group(raw: str):
    nums = set()
    for part in re.split(r"[,\uFF0C]", raw):
        part = part.strip()
        if not part:
            continue
        if re.fullmatch(r"\d{1,3}", part):
            nums.add(int(part))
            continue
        m = re.fullmatch(r"(\d{1,3})\s*[-\u2013]\s*(\d{1,3})", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a <= b and b - a <= 50:
                nums.update(range(a, b + 1))
    return nums


def analyze_citations(text: str):
    """Cross-map bracketed citations vs reference-list entries."""
    cited = set()
    refs = set()
    ay_cites = 0
    ay_refs = 0
    for line in text.splitlines():
        m = REF_ENTRY_RE.match(line)
        if m and _looks_like_ref(m.group(2)):
            refs.add(int(m.group(1)))
            continue
        for gm in BRACKET_CITE_RE.finditer(line):
            cited |= _parse_num_group(gm.group(1))
        ay_cites += len(re.findall(r"\([^()\n]{0,60}?(?:19|20)\d{2}[a-z]?\)", line))
        if AUTHOR_YEAR_REF_RE.match(line):
            ay_refs += 1
    return {
        "cited_number_count": len(cited),
        "reference_number_count": len(refs),
        "uncited_references": sorted(refs - cited),
        "uncited_citations": sorted(cited - refs),
        "author_year_citation_count": ay_cites,
        "author_year_reference_count": ay_refs,
    }


# ---------------------------------------------------------------------------
# Evidence assembly
# ---------------------------------------------------------------------------
def build_evidence(case_id: str, parsed_items, facets, degradations):
    files = []
    indexes = {}
    parsed_map = {}
    observations = []
    citation_summary = {}
    seen = set()

    for item in parsed_items:
        text = item.get("text") or ""
        path = item["path"]
        digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
        nlines = text.count("\n") + 1 if text else 0
        files.append({
            "path": path,
            "role": item.get("role", "material"),
            "chars": len(text),
            "lines": nlines,
            "sha256": digest[:16],
        })
        parsed_map[path] = text
        indexes[path] = build_text_index(text)

        for kind, facet, label, rx in PATTERNS:
            count = 0
            for ln, line in enumerate(text.splitlines(), 1):
                for m in rx.finditer(line):
                    _emit(observations, seen, kind, facet, label, path, ln, m.group(0))
                    count += 1
                    if count >= MAX_OBS_PER_KIND:
                        break
                if count >= MAX_OBS_PER_KIND:
                    break

        ca = analyze_citations(text)
        if ca["reference_number_count"] or ca["cited_number_count"]:
            citation_summary[path] = ca

    return {
        "case_id": case_id,
        "facets": facets,
        "files": files,
        "indexes": indexes,
        "parsed_map": parsed_map,
        "observations": observations,
        "citation_analysis": citation_summary,
        "degradations": list(degradations),
    }
