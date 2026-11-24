#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""论文质量审查 Skill 共享模块（仅标准库）。

职责：
  1. 常量与枚举（六分面 facet、严重度、问题性质、判定、状态等）。
  2. 文本与材料抽取：UTF-8 容错读文件、段落切分、DOCX 段落抽取、
     PDF 文本层 best-effort 抽取（stdlib fallback，正式场景优先平台 pdf-text-tool）。
  3. 输出契约工具：递归骨架 skeleton_of、归一化、逐字段契约审计 audit_contract。
  4. finding 字段校验与逐字 quote 命中检查。

不依赖任何第三方包，供 scripts/main.py 与 scripts/selftest.py 复用。
"""
from __future__ import annotations

import io
import json
import re
import zipfile
import zlib
import xml.etree.ElementTree as ET
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量与枚举
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
ISSUE_TYPES = [
    "factual_error",
    "insufficient_evidence",
    "standards_issue",
    "suggestion",
    "source_conflict",
]
VERDICTS = ["confirmed", "to_review", "not_checked"]
CONFIDENCES = ["high", "medium", "low"]
STATUSES = ["ok", "partial", "insufficient_evidence", "input_error"]
OVERALL_VERDICTS = ["pass", "issues_found", "to_review", "not_checked", "inconclusive"]

TEXT_KINDS = {".txt", ".md", ".json", ".csv", ".tsv", ".tex", ".yaml", ".yml",
              ".bib", ".xml", ".html", ".log", ".rst", ".rtf"}
PDF_KINDS = {".pdf"}
DOCX_KINDS = {".docx"}
IMAGE_KINDS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff",
               ".webp", ".svg"}
MAX_TEXT_FILE = 2_000_000
MAX_DECODED_STREAM = 4_000_000

# 信封保留键：output_contract 同名键优先占据顶层，信封值在冲突时移入
# `_skill_envelope.<key>`，且冲突记入 contract_audit.collisions。
ENVELOPE_RESERVED = {
    "case_id", "status", "sources", "uncertainty", "pending_human", "errors",
    "degradations", "changes", "change_log", "runtime", "contract_audit",
    "result",
}
ENVELOPE_NS = "_skill_envelope"

FINDING_REQUIRED = {
    "finding_id", "facet", "severity", "issue_type", "verdict", "claim",
    "location", "quote", "rationale", "revision_suggestion", "confidence",
}
FINDING_OPTIONAL = {"contract_ref", "evidence"}


# ---------------------------------------------------------------------------
# 文本读取与段落切分
# ---------------------------------------------------------------------------
def read_text(path):
    """UTF-8（含 BOM）容错读取，返回 (text|None, problem|None)。"""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return None, f"read error: {exc}"
    if len(data) > MAX_TEXT_FILE:
        return None, f"file too large ({len(data)} bytes > {MAX_TEXT_FILE})"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc), None
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace"), "decoded with replacements"


def normalize_text(s):
    """把任意空白折叠成单个空格并去首尾，用于逐字 quote 命中比较。"""
    if s is None:
        return ""
    return re.sub(r"\s+", " ", s).strip()


def split_paragraphs(text, page=None):
    """按空行切段，输出带 locator 的段落对象列表。

    locator 形如 `p3#para2`（page=3，段内序号 2）；无页码时为 `para2`。
    """
    if not text:
        return []
    paras = []
    for i, chunk in enumerate(re.split(r"\n\s*\n+", text), 1):
        t = chunk.strip()
        if not t:
            continue
        loc = f"p{page}#para{i}" if page else f"para{i}"
        paras.append({
            "locator": loc,
            "page": page,
            "para_index": i,
            "text": t,
            "norm": normalize_text(t),
        })
    return paras


# ---------------------------------------------------------------------------
# DOCX 段落抽取（zipfile + ElementTree，仅标准库）
# ---------------------------------------------------------------------------
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extract_docx_paras(data: bytes):
    """从 docx 提取逐段文本，返回 (lines: list[str], problem: str|None)。"""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if "word/document.xml" not in z.namelist():
                return [], "docx missing word/document.xml"
            xml = z.read("word/document.xml")
    except Exception as exc:
        return [], f"docx unzip error: {exc}"
    try:
        root = ET.fromstring(xml)
    except Exception as exc:
        return [], f"docx xml parse error: {exc}"
    lines = []
    for p in root.iter(_W_NS + "p"):
        buf = []
        for t in p.iter(_W_NS + "t"):
            buf.append(t.text or "")
        lines.append("".join(buf))
    return lines, None


# ---------------------------------------------------------------------------
# PDF 文本层 best-effort 抽取（stdlib fallback）
# ---------------------------------------------------------------------------
def _decode_pdf_string(raw: bytes) -> str:
    out = bytearray()
    i, n = 0, len(raw)
    simple = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f",
              b"(": b"(", b")": b")", b"\\": b"\\"}
    while i < n:
        c = raw[i]
        if c == 0x5C and i + 1 < n:
            nxt = bytes([raw[i + 1]])
            if nxt in simple:
                out += simple[nxt]
                i += 2
                continue
            if 0x30 <= raw[i + 1] <= 0x37:
                j, val, cnt = i + 1, 0, 0
                while j < n and cnt < 3 and 0x30 <= raw[j] <= 0x37:
                    val = val * 8 + (raw[j] - 0x30)
                    j += 1
                    cnt += 1
                out.append(val & 0xFF)
                i = j
                continue
            i += 2
            continue
        out.append(c)
        i += 1
    try:
        return out.decode("utf-8", errors="replace")
    except Exception:
        return out.decode("latin-1", errors="replace")


def _extract_text_operators(data: bytes):
    """扫描 PDF 内容流中的 (string)Tj / (string)' / (string)" / [(..)..]TJ。"""
    chunks = []
    i, n = 0, len(data)
    text_ops = {b"Tj", b"TJ", b"'", b'"'}
    while i < n:
        if data[i] != 0x28:
            i += 1
            continue
        depth, k = 0, i
        while k < n:
            c = data[k]
            if c == 0x5C and k + 1 < n:
                k += 2
                continue
            if c == 0x28:
                depth += 1
            elif c == 0x29:
                depth -= 1
                if depth == 0:
                    k += 1
                    break
            k += 1
        if depth != 0:
            i += 1
            continue
        raw = data[i + 1:k - 1]
        m = k
        while m < n and data[m] in b" \t\r\n":
            m += 1
        op_end = m
        while op_end < n and data[op_end] not in b" \t\r\n[]<>/":
            op_end += 1
        if data[m:op_end] in text_ops:
            txt = _decode_pdf_string(raw).strip()
            if txt:
                chunks.append(txt)
        i = k
    return chunks


def extract_pdf_text(data: bytes):
    """Best-effort 抽取 PDF 文本层，返回 (pages: list[str], problems: list[str])。

    这是 stdlib 兜底实现，可能漏字形或错排；正式案例应优先使用平台
    pdf-text-tool 生成逐页文本后以 --rescan 重跑 init。
    """
    problems = []
    if not data.startswith(b"%PDF"):
        return [], ["not a PDF (missing %PDF header)"]
    streams = []
    i = 0
    while True:
        s = data.find(b"stream", i)
        if s < 0:
            break
        j = s + 6
        if data[j:j + 2] == b"\r\n":
            j += 2
        elif data[j:j + 1] in (b"\n", b"\r"):
            j += 1
        e = data.find(b"endstream", j)
        if e < 0:
            break
        streams.append(data[j:e])
        i = e + 9
    texts = []
    for raw in streams:
        dec = raw
        try:
            dec = zlib.decompress(raw)
        except Exception:
            dec = raw
        if len(dec) > MAX_DECODED_STREAM:
            dec = dec[:MAX_DECODED_STREAM]
        for chunk in _extract_text_operators(dec):
            texts.append(chunk)
    if not texts:
        return [], problems + ["no text layer extracted from streams"]
    joined = "\n".join(texts)
    pages = [p for p in joined.split("\f") if p.strip()]
    if not pages:
        pages = [joined]
    problems.append(
        "stdlib fallback PDF extraction (best-effort); prefer pdf-text-tool "
        "for verbatim quotes")
    return pages, problems


# ---------------------------------------------------------------------------
# 输出契约工具
# ---------------------------------------------------------------------------
def normalize_contract(contract):
    """把 output_contract 归一化为 dict。

    - dict：原样返回。
    - list：元素为 str 视为字段名（值 None）；元素为 dict 则并入；
      其余元素按序用索引名兜底。
    - None/其他：返回 {}。
    """
    if isinstance(contract, dict):
        return contract
    if isinstance(contract, list):
        d = {}
        for item in contract:
            if isinstance(item, str):
                d[item] = None
            elif isinstance(item, dict):
                d.update(item)
            else:
                d[str(len(d))] = item
        return d
    return {}


def skeleton_of(node):
    """递归生成契约节点的最小合法占位：dict→逐键，list→逐项，标量→None。"""
    if isinstance(node, dict):
        return {k: skeleton_of(v) for k, v in node.items()}
    if isinstance(node, list):
        return [skeleton_of(v) for v in node]
    return None


def flatten_skeleton(node, prefix=""):
    """把嵌套契约展平为 path→占位值，供逐字段审计与展示。"""
    out = {}
    if isinstance(node, dict):
        for k, v in node.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, (dict, list)):
                out.update(flatten_skeleton(v, key))
            else:
                out[key] = v
    elif isinstance(node, list):
        for idx, v in enumerate(node):
            key = f"{prefix}[{idx}]"
            if isinstance(v, (dict, list)):
                out.update(flatten_skeleton(v, key))
            else:
                out[key] = v
    else:
        out[prefix or "$"] = node
    return out


def _is_default(v):
    return v is None or v == [] or v == {}


def audit_contract(contract, values):
    """逐字段审计契约与回填值的一一对应关系。

    返回 dict：
      total_fields / satisfied / defaulted / absent / extra / per_field[]
    其中 `absent` 表示契约键在回填值中被改名或删除；`extra` 表示回填值中
    多出契约没有的顶层键。二者都说明未做到"不改名、不删除"。
    """
    report = {
        "total_fields": 0, "satisfied": 0, "defaulted": 0,
        "absent": 0, "extra": [], "per_field": [],
    }

    def walk(node, val, prefix):
        if isinstance(node, dict):
            for k, v in node.items():
                path = f"{prefix}.{k}" if prefix else str(k)
                if isinstance(val, dict) and k in val:
                    walk(v, val[k], path)
                else:
                    report["absent"] += 1
                    report["per_field"].append({"path": path, "status": "absent"})
        elif isinstance(node, list):
            for idx, v in enumerate(node):
                path = f"{prefix}[{idx}]"
                if isinstance(val, list) and idx < len(val):
                    walk(v, val[idx], path)
                else:
                    report["absent"] += 1
                    report["per_field"].append({"path": path, "status": "absent"})
        else:
            report["total_fields"] += 1
            if _is_default(val):
                report["defaulted"] += 1
                report["per_field"].append(
                    {"path": prefix or "$", "status": "defaulted"})
            else:
                report["satisfied"] += 1
                report["per_field"].append(
                    {"path": prefix or "$", "status": "satisfied"})

    walk(contract, values, "")

    if isinstance(contract, dict) and isinstance(values, dict):
        extra_keys = [str(k) for k in values.keys() if k not in contract]
        if extra_keys:
            report["extra"] = extra_keys
    return report


# ---------------------------------------------------------------------------
# finding 校验
# ---------------------------------------------------------------------------
def validate_finding(f):
    """校验单条 finding 的形状与枚举，返回问题列表（空列表 = 通过）。"""
    problems = []
    if not isinstance(f, dict):
        return ["finding is not an object"]
    missing = FINDING_REQUIRED - set(f.keys())
    if missing:
        problems.append("missing keys: " + ", ".join(sorted(missing)))
    enum_map = {
        "facet": FACETS,
        "severity": SEVERITIES,
        "issue_type": ISSUE_TYPES,
        "verdict": VERDICTS,
        "confidence": CONFIDENCES,
    }
    for key, allowed in enum_map.items():
        if key in f and f[key] not in allowed:
            problems.append(
                f"{key}={f[key]!r} not in {allowed}")
    if "finding_id" in f and not isinstance(f.get("finding_id"), str):
        problems.append("finding_id must be a string")
    for key in ("claim", "location", "quote", "rationale",
                "revision_suggestion"):
        if key in f and not isinstance(f.get(key), str):
            problems.append(f"{key} must be a string")
    return problems


# ---------------------------------------------------------------------------
# 逐字 quote 命中（基于 index 中的 paras）
# ---------------------------------------------------------------------------
def locate_quote(index, quote):
    """在 index 材料段落中查找逐字 quote，返回 (hit: bool, locator|None)。

    比较前折叠空白；quote 必须完整落在单个段落内（跨段不算命中）。
    """
    q = normalize_text(quote)
    if not q:
        return False, None
    for m in index.get("materials", []):
        for p in m.get("paras", []):
            if q in p.get("norm", ""):
                return True, f"{m['source_id']}::{p['locator']}"
    return False, None


def json_dumps(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2)
