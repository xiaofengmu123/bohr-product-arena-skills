#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""论文质量审查 Skill：解析与抽取内核（仅 Python 标准库）。

被 scripts/main.py 导入。职责：
  - 六类分面与枚举常量（单一事实来源）
  - 文本读取、段落切分、locator 生成
  - PDF（FlateDecode 流）与 DOCX（zip+XML）best-effort 文本抽取
  - 材料抽取与 /app/work/text/<sid>.txt 缓存复用
"""
from __future__ import annotations

import io
import re
import zipfile
import zlib
from pathlib import Path
from xml.etree import ElementTree as ET

# --------------------------------------------------------------------------- #
# 常量：六类分面与枚举
# --------------------------------------------------------------------------- #
FACETS = ("fact_check", "citation_support", "method_design",
          "statistics_report", "argument_chain", "academic_norms")
SEVERITIES = ("critical", "major", "minor", "suggestion")
ISSUE_TYPES = ("factual_error", "insufficient_evidence", "standards_issue", "suggestion")
VERDICTS = ("confirmed", "to_review", "not_checked")
CONFIDENCES = ("high", "medium", "low")
STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
OVERALL_VERDICTS = ("pass", "minor_revision", "major_revision", "reject",
                    "insufficient_evidence", "not_assessed")
ENVELOPE_KEYS = ("status", "report", "sources", "uncertainty", "pending_human",
                 "errors", "degradations", "changes", "change_log", "runtime")
FINDING_KEYS = ("finding_id", "facet", "severity", "issue_type", "verdict",
                "claim", "location", "quote", "rationale",
                "revision_suggestion", "confidence", "evidence")

MAX_TEXT_FILE = 2 * 1024 * 1024
TEXT_KINDS = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl",
              ".xml", ".html", ".htm", ".tex", ".rst", ".yaml", ".yml", ".log"}
PDF_KINDS = {".pdf"}
DOCX_KINDS = {".docx"}
IMAGE_KINDS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff",
               ".webp", ".svg", ".emf", ".eps"}

_WS = re.compile(r"\s+")


# --------------------------------------------------------------------------- #
# 文本读取与段落切分
# --------------------------------------------------------------------------- #
def read_text(path, max_bytes=MAX_TEXT_FILE):
    try:
        data = path.read_bytes()
    except OSError as exc:
        return None, f"read error: {exc}"
    if len(data) > max_bytes:
        return None, f"file too large ({len(data)} > {max_bytes} bytes)"
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc), None
        except (UnicodeDecodeError, ValueError):
            continue
    return None, "undecodable text"


def normalize(s):
    return _WS.sub(" ", s or "").strip()


def split_paragraphs(text, page=None):
    """按空行切段；locator 形如 para7 或 p3#para2。"""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n[ \t]*\n", text)
    paras = []
    num = 0
    for b in blocks:
        b = b.strip()
        if not b:
            continue
        num += 1
        loc = f"p{page}#para{num}" if page else f"para{num}"
        paras.append({"locator": loc, "page": page, "num": num,
                      "text": b, "norm": normalize(b)})
    return paras


def classify_kind(name):
    ext = Path(name).suffix.lower()
    if ext in PDF_KINDS:
        return "pdf"
    if ext in DOCX_KINDS:
        return "docx"
    if ext in IMAGE_KINDS:
        return "image"
    if ext in TEXT_KINDS:
        return "text"
    return "other"


# --------------------------------------------------------------------------- #
# PDF 文本抽取（仅标准库，best-effort；中文 CID 字体请用平台 pdf-text-tool）
# --------------------------------------------------------------------------- #
_PDF_TOK = re.compile(
    rb"\(((?:[^()\\]|\\.)*)\)\s*Tj|"
    rb"\[((?:[^\[\]]*))\]\s*TJ|"
    rb"<([0-9A-Fa-f\s]+)>\s*Tj|"
    rb"(BT|ET|T\*)",
    re.S)
_PDF_STR = re.compile(rb"\(((?:[^()\\]|\\.)*)\)")
_PDF_STREAM = re.compile(rb"stream\r?\n", re.I)


def _pdf_unescape(b):
    out = bytearray()
    i = 0
    n = len(b)
    while i < n:
        c = b[i]
        if c == 0x5C and i + 1 < n:
            x = b[i + 1]
            if x == 0x6E:
                out.append(0x0A); i += 2
            elif x == 0x72:
                out.append(0x0D); i += 2
            elif x == 0x74:
                out.append(0x09); i += 2
            elif x == 0x62:
                out.append(0x08); i += 2
            elif x == 0x66:
                out.append(0x0C); i += 2
            elif x in (0x28, 0x29, 0x5C):
                out.append(x); i += 2
            elif 0x30 <= x <= 0x37:
                j = i + 1
                val = 0
                k = 0
                while j < n and k < 3 and 0x30 <= b[j] <= 0x37:
                    val = val * 8 + (b[j] - 0x30)
                    j += 1
                    k += 1
                out.append(val & 0xFF)
                i = j
            elif x in (0x0D, 0x0A):
                i += 2
                if i < n and b[i] == 0x0A:
                    i += 1
            else:
                out.append(x); i += 2
        else:
            out.append(c); i += 1
    return _decode_pdf_string(bytes(out))


def _decode_pdf_string(b):
    if b.startswith(b"\xfe\xff"):
        return b[2:].decode("utf-16-be", "replace")
    if b.startswith(b"\xff\xfe"):
        return b[2:].decode("utf-16-le", "replace")
    try:
        return b.decode("latin-1")
    except Exception:
        return b.decode("utf-8", "replace")


def _hex_text(hx):
    try:
        raw = bytes.fromhex(re.sub(rb"\s+", b"", hx).decode("ascii"))
    except Exception:
        return ""
    return _decode_pdf_string(raw)


def _content_text(data):
    parts = []
    for m in _PDF_TOK.finditer(data):
        if m.group(1) is not None:
            parts.append(_pdf_unescape(m.group(1)))
        elif m.group(2) is not None:
            for s in _PDF_STR.finditer(m.group(2)):
                parts.append(_pdf_unescape(s.group(1)))
        elif m.group(3) is not None:
            parts.append(_hex_text(m.group(3)))
        else:
            parts.append("\n")
    return "".join(parts)


def _inflate(raw):
    raw = raw.strip(b"\r\n")
    if not raw:
        return None
    try:
        return zlib.decompress(raw)
    except Exception:
        pass
    for cut in range(1, min(16, len(raw))):
        try:
            return zlib.decompress(raw[cut:])
        except Exception:
            continue
    return None


def extract_pdf_text(data):
    problems = []
    if not data.startswith(b"%PDF"):
        return [], ["not a PDF (missing %PDF header)"]
    streams = []
    for m in _PDF_STREAM.finditer(data):
        start = m.end()
        end = data.find(b"endstream", start)
        if end < 0:
            continue
        dec = _inflate(data[start:end])
        if dec is not None:
            streams.append(dec)
    if not streams:
        problems.append("no FlateDecode streams; may be scanned or uncompressed")
    pages = [t for t in (_content_text(s) for s in streams) if t.strip()]
    if not pages:
        problems.append("no text operators found; use platform pdf-text-tool")
    return pages, problems


# --------------------------------------------------------------------------- #
# DOCX 抽取（zip + XML，仅标准库）
# --------------------------------------------------------------------------- #
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extract_docx_paras(data):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except Exception as exc:
        return [], f"docx unzip error: {exc}"
    if "word/document.xml" not in z.namelist():
        return [], "word/document.xml missing"
    try:
        root = ET.fromstring(z.read("word/document.xml"))
    except Exception as exc:
        return [], f"docx xml error: {exc}"
    lines = []
    for p in root.iter(f"{_W_NS}p"):
        runs = [t.text or "" for t in p.iter(f"{_W_NS}t")]
        lines.append("".join(runs))
    if not lines:
        return [], "no paragraphs found in document.xml"
    return lines, None


# --------------------------------------------------------------------------- #
# 材料抽取与缓存复用
# --------------------------------------------------------------------------- #
def _write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def _paras_to_text(paras):
    if not paras:
        return ""
    pages = {}
    for p in paras:
        pages.setdefault(p.get("page") or 1, []).append(p["text"])
    return "\f".join("\n\n".join(pages[pg]) for pg in sorted(pages))


def extract_material(path, kind):
    if kind in ("image", "other"):
        return [], [f"{kind}: no text layer; needs OCR / external tool"]
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [], [f"read error: {exc}"]
    if kind == "pdf":
        pages, problems = extract_pdf_text(data)
        if not any(p.strip() for p in pages):
            return [], problems + ["no text extracted"]
        paras = []
        for pi, pg in enumerate(pages, 1):
            paras.extend(split_paragraphs(pg, page=pi))
        return paras, problems
    if kind == "docx":
        lines, problem = extract_docx_paras(data)
        if problem and not lines:
            return [], [problem]
        return split_paragraphs("\n\n".join(lines), page=None), \
            ([problem] if problem else [])
    text, problem = read_text(path)
    if text is None:
        return [], [problem or "unreadable"]
    return split_paragraphs(text, page=None), ([problem] if problem else [])


def extract_or_reuse(path, kind, work, sid, rescan):
    """优先复用 /app/work/text/<sid>.txt（Agent 可用平台 pdf-text-tool 预提取）。"""
    pre = work / "text" / f"{sid}.txt"
    if not rescan and pre.exists():
        text, problem = read_text(pre)
        if text is not None:
            pages = [p for p in text.split("\f") if p.strip()] or [text]
            paras = []
            for pi, pg in enumerate(pages, 1):
                paras.extend(split_paragraphs(pg, page=pi))
            problems = ([problem] if problem else []) + [f"reused pre-extracted {pre.name}"]
            if paras:
                return paras, problems
    paras, problems = extract_material(path, kind)
    try:
        _write_text(pre, _paras_to_text(paras))
    except Exception:
        pass
    return paras, problems
