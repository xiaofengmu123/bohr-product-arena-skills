#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py —— 证据台账式论文质量审查的共享实现（仅标准库）。

职责：
- 常量与枚举、六面 × 检查锚点
- 文本读取（BOM / 坏编码降级）
- 段落切分与 locator 生成
- PDF / DOCX 尽力抽取（纯标准库；失败时如实降级，绝不伪造页码或内容）
- 输出契约回填（原样回填全部键）
- finding 校验（quote 逐字命中、字段、枚举、覆盖）
- result.json 信封与 verdict_summary 组装

设计要点：所有抽取都是"尽力而为"，读不出来就返回 problems，由上层写进
degradations / limitations，不编造文本、页码或数值。
"""
from __future__ import annotations

import copy
import io
import json
import re
import zipfile
import zlib
from pathlib import Path
from xml.etree import ElementTree as ET

# ---------------- 常量与枚举 ----------------
FACETS = ("fact_check", "citation_support", "method_design",
          "statistics_report", "argument_chain", "academic_norms")
SEVERITIES = ("critical", "major", "minor", "suggestion")
ISSUE_TYPES = ("factual_error", "insufficient_evidence",
               "standards_issue", "suggestion")
VERDICTS = ("confirmed", "to_review", "not_checked")
CONFIDENCES = ("high", "medium", "low")
STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
OVERALL_VERDICTS = ("reject", "major_revision", "minor_revision",
                    "accept", "inconclusive")

TEXT_KINDS = {".txt", ".md", ".markdown", ".rst", ".tex", ".csv", ".tsv",
              ".json", ".jsonl", ".xml", ".yaml", ".yml"}
PDF_KINDS = {".pdf"}
DOCX_KINDS = {".docx"}
IMAGE_KINDS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff",
               ".webp", ".svg"}
MAX_TEXT_FILE = 2_000_000  # 单材料文件字节上限，超过则跳过并记录

SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}

# 六面 × 检查锚点（与 references/review-checklist.md 保持一致）
ANCHORS = {
    "fact_check": ["FCT-1", "FCT-2", "FCT-3", "FCT-4"],
    "citation_support": ["CIT-1", "CIT-2", "CIT-3", "CIT-4"],
    "method_design": ["MET-1", "MET-2", "MET-3", "MET-4", "MET-5"],
    "statistics_report": ["STA-1", "STA-2", "STA-3", "STA-4", "STA-5"],
    "argument_chain": ["ARG-1", "ARG-2", "ARG-3", "ARG-4", "ARG-5"],
    "academic_norms": ["NOR-1", "NOR-2", "NOR-3", "NOR-4", "NOR-5"],
}

# finding 必填字段
REQUIRED_FINDING_KEYS = ("finding_id", "facet", "severity", "issue_type",
                         "verdict", "claim", "location", "quote",
                         "rationale", "revision_suggestion", "confidence",
                         "evidence")


# ---------------- 文本读取 ----------------
def read_text(path):
    """读取文本文件，BOM / 坏编码降级；返回 (text, problem)。problem 为 '' 表示正常。"""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        return None, f"read error: {exc}"
    if not data:
        return "", "empty file"
    if data[:3] == b"\xef\xbb\xbf":
        data = data[3:]
    elif data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace"), "utf-16 BOM"
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc), ("" if enc == "utf-8" else f"decoded as {enc}")
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace"), "decoded with replacement chars"


def norm_text(s):
    """白空格归一：连续空白折叠为单空格并去首尾。用于逐字命中比较。"""
    return re.sub(r"\s+", " ", s or "").strip()


# ---------------- 段落与 locator ----------------
def split_paragraphs(text, page=None):
    """按空行切段；无空行时按行切。locator 为 paraN 或 pN#paraN。"""
    if not text:
        return []
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text)]
    blocks = [b for b in blocks if b]
    if len(blocks) >= 2:
        units = blocks
    else:
        units = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out = []
    for seq, u in enumerate(units, 1):
        locator = f"p{page}#para{seq}" if page is not None else f"para{seq}"
        out.append({"locator": locator, "page": page, "seq": seq, "text": u})
    return out


# ---------------- PDF 尽力抽取（纯标准库） ----------------
def _decode_pdf_string(raw: bytes) -> str:
    out = bytearray()
    i, n = 0, len(raw)
    escapes = {ord("n"): 10, ord("r"): 13, ord("t"): 9,
               ord("b"): 8, ord("f"): 12}
    while i < n:
        b = raw[i]
        if b == 0x5C and i + 1 < n:  # 反斜杠转义
            nxt = raw[i + 1]
            if nxt in escapes:
                out.append(escapes[nxt])
                i += 2
                continue
            if nxt in (0x28, 0x29, 0x5C):
                out.append(nxt)
                i += 2
                continue
            if 0x30 <= nxt <= 0x37:  # 八进制
                val, j = 0, i + 1
                for _ in range(3):
                    if j < n and 0x30 <= raw[j] <= 0x37:
                        val = val * 8 + (raw[j] - 0x30)
                        j += 1
                    else:
                        break
                out.append(val & 0xFF)
                i = j
                continue
            out.append(nxt)
            i += 2
            continue
        out.append(b)
        i += 1
    for enc in ("utf-8", "utf-16-be", "latin-1"):
        try:
            return bytes(out).decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return bytes(out).decode("latin-1", "replace")


def _iter_pdf_strings(chunk: bytes):
    """产出 BT/ET 块内所有 (..) 字面字符串。"""
    i, n = 0, len(chunk)
    while i < n:
        if chunk[i:i + 1] == b"(":
            depth, j = 0, i
            while j < n:
                c = chunk[j]
                if c == 0x5C:
                    j += 2
                    continue
                if c == 0x28:
                    depth += 1
                elif c == 0x29:
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
                j += 1
            if j > i + 1:
                yield _decode_pdf_string(chunk[i + 1:j - 1])
            i = j
        else:
            i += 1


def _iter_hex_strings(chunk: bytes):
    """产出 <hex> CID 字符串（UTF-16BE），尽力而为。"""
    for m in re.finditer(rb"<([0-9a-fA-F\s]+)>", chunk):
        hs = re.sub(rb"\s", b"", m.group(1))
        if not hs:
            continue
        if len(hs) % 2:
            hs += b"0"
        try:
            yield bytes.fromhex(hs.decode("ascii")).decode("utf-16-be", "replace")
        except (ValueError, UnicodeDecodeError):
            continue


def extract_pdf_text(data: bytes):
    """尽力抽取 PDF 文本，返回 (pages_text_list, problems)。

    纯标准库只能做流级提取，无法可靠映射页码：页码缺失时以单段返回并如实标注，
    由上层引导 Agent 改用平台 pdf-text-tool 获取带页码的文本。"""
    problems = []
    chunks = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.DOTALL):
        raw = m.group(1)
        try:
            decoded = zlib.decompress(raw)
        except Exception:
            decoded = raw
        for bt in re.finditer(rb"BT(.*?)ET", decoded, re.DOTALL):
            chunk = bt.group(1)
            parts = [t for t in _iter_pdf_strings(chunk) if t.strip()]
            parts += [t for t in _iter_hex_strings(chunk) if t.strip()]
            line = "".join(parts).strip()
            if line:
                chunks.append(line)
    if not chunks:
        problems.append("no extractable text layer: likely scanned or CID-encoded; "
                        "use pdf-text-tool/OCR, do not guess content")
        return [], problems
    problems.append("stdlib best-effort extraction: page mapping unavailable; "
                    "prefer pdf-text-tool for page-accurate locators")
    return ["\n\n".join(chunks)], problems


# ---------------- DOCX 抽取 ----------------
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def extract_docx_paras(data: bytes):
    """从 word/document.xml 抽取段落文本，返回 (lines, problem)。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception as exc:
        return [], f"docx unzip failed: {exc}"
    if "word/document.xml" not in zf.namelist():
        return [], "docx missing word/document.xml"
    try:
        root = ET.fromstring(zf.read("word/document.xml"))
    except Exception as exc:
        return [], f"docx XML parse failed: {exc}"
    lines = []
    for p in root.iter(f"{{{_W_NS}}}p"):
        parts = [t.text or "" for t in p.iter(f"{{{_W_NS}}}t")]
        line = "".join(parts).strip()
        if line:
            lines.append(line)
    if not lines:
        return [], "docx contains no paragraph text"
    return lines, ""


# ---------------- 输出契约回填 ----------------
def contract_keys_of(contract):
    """从 dict / list 契约提取键名（去重保序），用于覆盖核对。"""
    keys = []
    if isinstance(contract, dict):
        keys = list(contract.keys())
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                nm = item.get("name")
                if isinstance(nm, str):
                    keys.append(nm)
                else:
                    keys.extend(item.keys())
    return list(dict.fromkeys(keys))


def echo_contract(contract):
    """返回 (top_level_dict, raw_copy)。

    top_level_dict 将契约键平铺到 result.json 顶层（不改名、不删除），
    raw_copy 原样保留供复核；缺失/未知键写入 null。"""
    if isinstance(contract, dict):
        raw = copy.deepcopy(contract)
        return dict(raw), raw
    if isinstance(contract, list):
        raw = copy.deepcopy(contract)
        out = {}
        for item in contract:
            if isinstance(item, str):
                out.setdefault(item, None)
            elif isinstance(item, dict):
                nm = item.get("name")
                if isinstance(nm, str):
                    out.setdefault(nm, None)
                else:
                    for k, v in item.items():
                        out.setdefault(k, copy.deepcopy(v))
        return out, raw
    return {}, None


# ---------------- finding 校验 ----------------
def find_quote(quote, paragraphs):
    """在段落列表中查找逐字（白空格归一）命中的 quote，返回 (source_id, locator)。"""
    q = norm_text(quote)
    if not q:
        return None
    for p in paragraphs:
        if q in norm_text(p.get("text", "")):
            return (p.get("source_id"), p.get("locator"))
    return None


def validate_finding(f, paragraphs):
    """校验单条 finding，返回 (problems:list, matched:tuple|None)。

    quote 必须逐字命中某一自然段（跨行可、跨段不可，白空格归一）。
    evidence 中引用外部文献的条目不作本地命中要求（无法核验时标 to_review）。"""
    problems = []
    if not isinstance(f, dict):
        return ["finding is not an object"], None
    for k in REQUIRED_FINDING_KEYS:
        if k not in f:
            problems.append(f"missing key: {k}")
    for field, allowed in (("facet", FACETS), ("severity", SEVERITIES),
                           ("issue_type", ISSUE_TYPES), ("verdict", VERDICTS),
                           ("confidence", CONFIDENCES)):
        v = f.get(field)
        if v is not None and v not in allowed:
            problems.append(f"{field}={v!r} not in {allowed}")
    ev = f.get("evidence")
    if ev is not None and not isinstance(ev, list):
        problems.append("evidence must be a list")
    matched = None
    if "quote" in f:
        matched = find_quote(f.get("quote", ""), paragraphs)
        if matched is None:
            problems.append("quote not found verbatim in any source paragraph")
    return problems, matched


# ---------------- result.json 组装 ----------------
def empty_coverage():
    return {facet: {"status": "not_checked", "reason": "not yet reviewed",
                    "anchors": {a: {"status": "not_checked", "finding_ids": []}
                                for a in anchors}}
            for facet, anchors in ANCHORS.items()}


def empty_verdict_summary():
    return {"overall": "inconclusive",
            "counts": {s: 0 for s in SEVERITIES},
            "by_facet": {f: 0 for f in FACETS},
            "by_issue_type": {t: 0 for t in ISSUE_TYPES}}


def compute_verdict_summary(findings, overall=None):
    summary = empty_verdict_summary()
    for f in findings:
        sev = f.get("severity")
        if sev in summary["counts"]:
            summary["counts"][sev] += 1
        fac = f.get("facet")
        if fac in summary["by_facet"]:
            summary["by_facet"][fac] += 1
        it = f.get("issue_type")
        if it in summary["by_issue_type"]:
            summary["by_issue_type"][it] += 1
    if overall in OVERALL_VERDICTS:
        summary["overall"] = overall
    elif summary["counts"]["critical"]:
        summary["overall"] = "reject"
    elif summary["counts"]["major"]:
        summary["overall"] = "major_revision"
    elif summary["counts"]["minor"] or summary["counts"]["suggestion"]:
        summary["overall"] = "minor_revision"
    else:
        summary["overall"] = "accept"
    return summary


def build_result(case_id, status, contract, sources, findings, unchecked,
                 coverage, verdict_summary, uncertainty, pending_human,
                 errors, degradations, limitations, change_log, runtime,
                 artifacts):
    """组装完整 result.json 信封。契约键先平铺，再叠加信封键。"""
    out = {}
    top, raw = echo_contract(contract)
    out.update(top)
    out["case_id"] = case_id
    out["status"] = status
    out["output_contract"] = raw
    out["artifacts"] = artifacts
    out["sources"] = sources
    out["uncertainty"] = uncertainty
    out["pending_human"] = pending_human
    out["errors"] = errors
    out["degradations"] = degradations
    out["limitations"] = limitations
    out["change_log"] = change_log
    out["runtime"] = runtime
    out["result"] = {
        "findings": findings,
        "unchecked_items": unchecked,
        "coverage": coverage,
        "verdict_summary": verdict_summary,
    }
    return out
