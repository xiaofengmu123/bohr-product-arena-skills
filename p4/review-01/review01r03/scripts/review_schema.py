#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""论文质量审查 Skill 的共享实现（仅标准库）。

集中存放：枚举与字段常量、编码容错读取、段落切分、PDF/DOCX 文本抽取、
输出契约键扫描、finding 形状校验、逐字引用匹配、确定性 JSON 序列化。

设计要点（确定性）：
- 所有文本按 utf-8-sig 优先、gb18030/latin-1 回退读取；
- 段落切分与 locator 生成规则固定，show/check/build 三处共用同一实现；
- JSON 使用 ensure_ascii=False + indent=2 + sort_keys=False，键序由代码
  显式决定，保证同一输入两次运行主产物字节一致（易变量只进 runtime）。
"""
from __future__ import annotations

import io
import json
import re
import sys
import zlib
from pathlib import Path

# ---------------------------------------------------------------------------
# 枚举与常量
# ---------------------------------------------------------------------------
FACETS = [
    "fact_check",
    "citation_support",
    "method_design",
    "statistics_report",
    "argument_chain",
    "academic_norms",
]
SEVERITIES = ["critical", "major", "minor", "suggestion"]
ISSUE_TYPES = ["factual_error", "insufficient_evidence", "standards_issue", "suggestion"]
VERDICTS = ["confirmed", "to_review", "not_checked"]
CONFIDENCES = ["high", "medium", "low"]
STATUSES = ["ok", "partial", "insufficient_evidence", "input_error"]
OVERALL_VERDICTS = ["pass", "issues", "to_review", "incomplete"]

FINDING_KEYS = [
    "finding_id", "facet", "severity", "issue_type", "verdict", "claim",
    "location", "quote", "rationale", "revision_suggestion", "confidence",
    "evidence",
]
ENVELOPE_KEYS = [
    "status", "sources", "uncertainty", "pending_human", "errors",
    "degradations", "changes", "change_log", "runtime",
]
RESERVED_KEYS = set(ENVELOPE_KEYS + ["case_id", "result", "output_contract"])

FACET_ORDER = {f: i for i, f in enumerate(FACETS)}

MAX_TEXT_FILE = 2 * 1024 * 1024  # 2MB

TEXT_KINDS = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".tex",
    ".yaml", ".yml", ".bib", ".rtf", ".html", ".htm",
}
PDF_KINDS = {".pdf"}
DOCX_KINDS = {".docx"}
IMAGE_KINDS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff",
               ".webp", ".svg", ".pdf"}


# ---------------------------------------------------------------------------
# 文本读取与规范化
# ---------------------------------------------------------------------------
def read_text(path):
    """读取文本文件，返回 (text, problem)；text 为 None 表示完全不可读。"""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        return None, "read error: %s" % exc
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "undecodable; replaced"


def normalize_ws(s):
    """折叠所有空白为单个空格并去首尾，用于逐字匹配与长度判断。"""
    return re.sub(r"\s+", " ", s or "").strip()


def split_paragraphs(text, page=None):
    """按空行切段；返回 [{"locator","page","text","norm"}, ...]。

    locator 规则固定：PDF 为 ``p<page>#para<n>``，文本/DOCX 为 ``para<n>``。
    段落内换行保留在 text 中，norm 用于逐字匹配（quote 可跨行不跨段）。
    """
    paras = []
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n+", raw)
    n = 0
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        n += 1
        locator = ("p%d#para%d" % (page, n)) if page else ("para%d" % n)
        paras.append({
            "locator": locator,
            "page": page,
            "text": block,
            "norm": normalize_ws(block),
        })
    return paras


def paras_from_text(text, paged):
    """由材料全文重建带 locator 的段落列表。paged=True 时以 ``\\f`` 分页。"""
    pages = text.split("\f") if (paged and "\f" in text) else [text]
    out = []
    for pi, pg in enumerate(pages, 1):
        out.extend(split_paragraphs(pg, page=pi if paged else None))
    return out


# ---------------------------------------------------------------------------
# PDF 文本抽取（纯标准库回退实现；优先使用平台 pdf-text-tool）
# ---------------------------------------------------------------------------
_ESCAPED_PDF_STR = re.compile(r"\((?:[^()\\]|\\.)*\)")
_STREAM_RE = re.compile(rb"stream\r?\n(.*?)endstream", re.DOTALL)


def _unescape_pdf(s):
    s = s.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
    s = re.sub(r"\\([0-7]{1,3})", lambda m: chr(int(m.group(1), 8)), s)
    s = s.replace("\\n", "\n").replace("\\r", "").replace("\\t", " ")
    s = s.replace("\\b", "").replace("\\f", "\f")
    return s


def _pdf_stream_text(s):
    chunks = []
    for m in _ESCAPED_PDF_STR.finditer(s):
        end = m.end()
        if re.match(r"\s*Tj", s[end:end + 8]):
            chunks.append(_unescape_pdf(m.group(0)[1:-1]))
    for m in re.finditer(r"\[((?:[^\[\]\\]|\\.)*)\]\s*TJ", s):
        inner = m.group(1)
        for m2 in _ESCAPED_PDF_STR.finditer(inner):
            chunks.append(_unescape_pdf(m2.group(0)[1:-1]))
    return " ".join(c for c in chunks if c)


def extract_pdf_text(data):
    """回退抽取 PDF 文本层。返回 (pages, problems)。扫描件/无文本层返回空。"""
    if isinstance(data, str):
        data = data.encode("latin-1", "replace")
    if not data.startswith(b"%PDF"):
        return [], ["not a PDF (missing %PDF header)"]
    pages, problems = [], []
    for m in _STREAM_RE.finditer(data):
        raw = m.group(1).rstrip(b"\r\n ")
        try:
            payload = zlib.decompress(raw)
        except zlib.error:
            payload = raw
        text = _pdf_stream_text(payload.decode("latin-1", errors="replace"))
        if text.strip():
            pages.append(text)
    if not pages:
        problems.append("no extractable text layer; scanned or unsupported encoding")
    return pages, problems


def extract_docx_paras(data):
    """抽取 DOCX 段落文本。返回 (lines, problem)。"""
    try:
        import zipfile
        z = zipfile.ZipFile(io.BytesIO(data))
        xml = z.read("word/document.xml")
    except Exception as exc:
        return [], "docx unzip error: %s" % exc
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml)
    except Exception as exc:
        return [], "docx XML parse error: %s" % exc
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"
    T = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
    lines = []
    for p in root.iter(W):
        parts = [t.text or "" for t in p.iter(T)]
        lines.append("".join(parts))
    return lines, None


# ---------------------------------------------------------------------------
# 输出契约
# ---------------------------------------------------------------------------
def contract_keys_of(contract):
    """从 output_contract（dict 或 list）提取需回填的键名，保持出现顺序去重。"""
    keys = []
    if isinstance(contract, dict):
        keys = [str(k) for k in contract.keys()]
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                keys.extend(str(k) for k in item.keys())
    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def looks_list(key):
    kl = str(key).lower()
    return kl.endswith(("s", "list", "items")) or "list" in kl or "items" in kl


# ---------------------------------------------------------------------------
# 校验与匹配
# ---------------------------------------------------------------------------
def validate_finding(f):
    """校验单条 finding 的形状与枚举。返回 (problems, warnings) 两个列表。"""
    problems, warnings = [], []
    if not isinstance(f, dict):
        return ["finding is not an object"], []
    for k in FINDING_KEYS:
        if k not in f:
            problems.append("missing key: %s" % k)
    checks = [
        ("facet", FACETS),
        ("severity", SEVERITIES),
        ("issue_type", ISSUE_TYPES),
        ("verdict", VERDICTS),
        ("confidence", CONFIDENCES),
    ]
    for key, allowed in checks:
        if key in f and f.get(key) not in allowed:
            problems.append("bad %s: %r" % (key, f.get(key)))
    if "finding_id" in f and not str(f.get("finding_id", "")).strip():
        problems.append("empty finding_id")
    quote = f.get("quote")
    if not quote or not str(quote).strip():
        problems.append("empty quote")
    elif len(normalize_ws(str(quote))) < 12:
        warnings.append("quote <12 chars after normalization; weak anchor")
    loc = f.get("location")
    if loc is not None and "para" not in str(loc):
        warnings.append("location has no para anchor: %r" % loc)
    ev = f.get("evidence")
    if ev is not None:
        if not isinstance(ev, list):
            problems.append("evidence is not a list")
        else:
            for i, e in enumerate(ev):
                if not isinstance(e, dict):
                    problems.append("evidence[%d] not an object" % i)
    return problems, warnings


def find_quote_verbatim(quote, text_lookup):
    """在 text_lookup（source_id -> [norm 段落, ...]）中找逐字命中，返回 source_id 或 None。"""
    q = normalize_ws(quote)
    if not q:
        return None
    for sid, paras in text_lookup.items():
        for p in paras:
            if q in p:
                return sid
    return None


def finding_sort_key(f):
    return (FACET_ORDER.get(f.get("facet"), 99), str(f.get("finding_id", "")))


# ---------------------------------------------------------------------------
# 确定性序列化
# ---------------------------------------------------------------------------
def canonical_dumps(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False)


if __name__ == "__main__":  # 便于本地冒烟：打印枚举与版本
    print("paper-quality-review review_schema ok")
    print("facets:", " ".join(FACETS))
    print("python:", sys.version.split()[0])
