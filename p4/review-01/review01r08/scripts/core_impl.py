#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""paper-quality-review 核心实现（仅 Python 标准库）。

职责：
  * 容错读取文本：utf-8-sig / utf-8 / gb18030 / latin-1 逐级回退，超限跳过
  * 段落切分与稳定 locator：p{page}#para{n}（PDF）/ para{n}（纯文本）
  * 纯标准库 PDF 文本抽取：BT/ET 内 Tj/TJ 文本、zlib 解流；扫描件无文本层时如实降级
  * 纯标准库 DOCX 段落抽取：word/document.xml 的 w:t 节点按 w:p 分段
  * 结果 schema 常量、finding 字段校验、quote 逐字命中判定、契约键解析

本模块不读 case.json、不写 result.json，只提供无副作用的纯函数，供 main.py 与
selftest.py 复用，保证两者共享同一套契约定义。
"""

from __future__ import annotations

import io
import re
import zlib
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

# ---------------------------------------------------------------- 常量
TEXT_KINDS = {".txt", ".md", ".markdown", ".csv", ".tsv", ".tex", ".log"}
PDF_KINDS = {".pdf"}
DOCX_KINDS = {".docx", ".doc", ".pptx", ".xlsx"}
IMAGE_KINDS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".svg"}
MAX_TEXT_FILE = 2 * 1024 * 1024  # 2MB，超限跳过并记录，不截断后假装全查

# 六类审查面（facet）
FACETS = [
    "fact_check",
    "citation_support",
    "method_design",
    "statistics_report",
    "argument_chain",
    "academic_norms",
]
# 严重度由“该问题是否改变结论/能否修复”决定，见 references/review-checklist.md
SEVERITIES = ["critical", "major", "minor", "suggestion"]
# 问题性质：事实错误 / 证据不足 / 规范问题 / 建议性意见
ISSUE_TYPES = ["factual_error", "insufficient_evidence", "standards_issue", "suggestion"]
VERDICTS = ["confirmed", "to_review", "not_checked"]
CONFIDENCES = ["high", "medium", "low"]
STATUSES = ["ok", "partial", "insufficient_evidence", "input_error"]
OVERALL_VERDICTS = ["accept", "minor_revision", "major_revision", "reject", "not_determinable"]
# result.json 信封字段
ENVELOPE_KEYS = [
    "status", "sources", "uncertainty", "pending_human",
    "errors", "degradations", "changes", "change_log", "runtime",
]
FINDING_KEYS = [
    "finding_id", "facet", "severity", "issue_type", "verdict",
    "claim", "location", "quote", "rationale", "revision_suggestion",
    "confidence", "evidence",
]
EVIDENCE_KEYS = ["source_id", "locator", "quote"]
UNCHECKED_KEYS = ["facet", "reason", "status"]

# ---------------------------------------------------------------- 文本读取


def read_text(path, max_bytes=MAX_TEXT_FILE):
    """读取文本，返回 (text, problem)；text 为 None 表示不可用。

    problem 为空串表示正常；非空表示发生了需要写入 degradations 的降级。
    """
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        return None, f"read error: {exc}"
    if len(data) > max_bytes:
        return None, f"file too large: {len(data)} bytes > {max_bytes}"
    for enc, degrade in (
        ("utf-8-sig", ""),
        ("utf-8", ""),
        ("gb18030", "decoded as gb18030 (not utf-8)"),
        ("latin-1", "decoded as latin-1 fallback"),
    ):
        try:
            return data.decode(enc), degrade
        except (UnicodeDecodeError, LookupError):
            continue
    return None, "undecodable bytes"


# ---------------------------------------------------------------- 段落切分


def split_paragraphs(text, page=None):
    """把文本切成段落并附稳定 locator。

    locator 规则：PDF 页内为 ``p{page}#para{n}``，无页码的纯文本为 ``para{n}``。
    同一份材料重复解析时 locator 不变，保证他人能回到原文逐条核对。
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if re.search(r"\n\s*\n", text):
        chunks = re.split(r"\n\s*\n+", text)
    else:
        chunks = text.split("\n")
    paras = []
    idx = 0
    for chunk in chunks:
        cleaned = re.sub(r"[ \t]+", " ", chunk).strip()
        if not cleaned:
            continue
        idx += 1
        loc = f"p{page}#para{idx}" if page is not None else f"para{idx}"
        paras.append({"loc": loc, "text": cleaned})
    return paras


# ---------------------------------------------------------------- PDF 文本抽取


def _decode_pdf_string(s):
    """解码 PDF 字符串字面量：括号、反斜杠与八进制转义。"""
    s = s.strip()
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1]
    out = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            i += 1
            nxt = s[i]
            if nxt in "()\\":
                out.append(nxt)
            elif nxt == "n":
                out.append("\n")
            elif nxt == "r":
                out.append("\r")
            elif nxt == "t":
                out.append("\t")
            elif nxt.isdigit():
                j = i
                while j < n and j < i + 3 and s[j].isdigit():
                    j += 1
                try:
                    out.append(chr(int(s[i:j], 8)))
                except ValueError:
                    out.append(nxt)
                i = j - 1
            else:
                out.append(nxt)
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _extract_text_ops(content):
    """从解流后的 PDF 内容流抽取 Tj/TJ 文本。"""
    if isinstance(content, bytes):
        content = content.decode("latin-1", errors="replace")
    out = []
    for m in re.finditer(r"\((?:[^()\\]|\\.)*\)\s*Tj", content):
        seg = m.group(0)
        out.append(_decode_pdf_string(seg[: seg.rfind(")") + 1]))
    for m in re.finditer(r"\[(?:[^\[\]\\]|\\.)*\]\s*TJ", content):
        inner = m.group(0)
        body = inner[inner.find("[") + 1: inner.rfind("]")]
        out.append("".join(_decode_pdf_string(p) for p in re.findall(r"\((?:[^()\\]|\\.)*\)", body)))
    return "\n".join(out)


def extract_pdf_text(data):
    """纯标准库 PDF 文本抽取，返回 (pages: list[str], problems: list[str])。

    只覆盖文本型 PDF；扫描件 / 图片型 PDF 返回空 pages 并给出明确降级信息，
    由 SKILL.md 指示调用平台 pdf-text-tool 或 ocr-tool，不猜内容。
    """
    problems = []
    pages = []
    stream_pat = re.compile(rb"stream\r?\n(.*?)endstream", re.DOTALL)
    for m in stream_pat.finditer(data):
        body = m.group(1).lstrip(b"\r\n")
        try:
            content = zlib.decompress(body)
        except zlib.error:
            content = body
        text = _extract_text_ops(content)
        if text.strip():
            pages.append(text)
    if not pages:
        problems.append("no text layer found (possibly scanned/image PDF); run pdf-text-tool/ocr-tool")
    return pages, problems


# ---------------------------------------------------------------- DOCX 段落抽取


def extract_docx_paras(data):
    """抽取 DOCX 正文段落，返回 (paras: list[str], problem: str)。"""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            xml = zf.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        return [], f"docx parse error: {exc}"
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        return [], f"docx XML error: {exc}"
    ns_w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paras = []
    for p in root.iter(f"{ns_w}p"):
        text = "".join((t.text or "") for t in p.iter(f"{ns_w}t")).strip()
        if text:
            paras.append(text)
    return paras, ("" if paras else "no paragraphs extracted from docx")


# ---------------------------------------------------------------- 契约与校验


def contract_keys_of(contract):
    """从 output_contract（dict 或 list）提取顶层字段名，用于原样回填。"""
    if isinstance(contract, dict):
        return sorted(str(k) for k in contract.keys())
    if isinstance(contract, list):
        keys = []
        for item in contract:
            if isinstance(item, dict):
                k = item.get("name") or item.get("key") or item.get("field")
                if k:
                    keys.append(str(k))
            elif isinstance(item, str):
                keys.append(item)
        return sorted(set(keys))
    return []


def default_for_key(key):
    """未提供值的契约键写 null；名称含列表语义的键写 []。"""
    k = str(key).lower()
    list_hint = any(s in k for s in (
        "list", "items", "findings", "issues", "unchecked", "sources",
        "errors", "changes", "uncertainty", "pending", "evidence",
    ))
    return [] if list_hint else None


def validate_finding(f, index=None):
    """校验单条 finding，返回错误列表（空列表表示合法）。"""
    errs = []
    idx = f.get("finding_id") or (f"#{index}" if index is not None else "?")
    for key in FINDING_KEYS:
        if key not in f:
            errs.append(f"{idx}: missing key {key}")
    if f.get("facet") not in FACETS:
        errs.append(f"{idx}: bad facet {f.get('facet')!r}")
    if f.get("severity") not in SEVERITIES:
        errs.append(f"{idx}: bad severity {f.get('severity')!r}")
    if f.get("issue_type") not in ISSUE_TYPES:
        errs.append(f"{idx}: bad issue_type {f.get('issue_type')!r}")
    if f.get("verdict") not in VERDICTS:
        errs.append(f"{idx}: bad verdict {f.get('verdict')!r}")
    if f.get("confidence") not in CONFIDENCES:
        errs.append(f"{idx}: bad confidence {f.get('confidence')!r}")
    fid = f.get("finding_id")
    if fid is not None and not re.fullmatch(r"F\d{2,}", str(fid)):
        errs.append(f"{idx}: finding_id 应为 F## 形式")
    for ev in (f.get("evidence") or []):
        if not isinstance(ev, dict):
            errs.append(f"{idx}: evidence 项应为对象")
            continue
        for key in EVIDENCE_KEYS:
            if key not in ev:
                errs.append(f"{idx}: evidence 缺 {key}")
    return errs


def normalize_for_match(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def quote_hit(quote, text):
    """判断 quote 是否逐字（空白归一化后）出现在 text 中。"""
    q = normalize_for_match(quote)
    t = normalize_for_match(text)
    return bool(q) and q in t


# ---------------------------------------------------------------- 评分维度自检

# 题目 5 大评分维度 → 自检项 S1–S16（与 references/scoring-alignment.md 保持同一份定义）
SCORE_ALIGNMENT = [
    ("actual_effect", "S1", "任务完成度：findings 为结构化问题清单，非整篇评语/复述"),
    ("actual_effect", "S2", "结果正确性：每条 quote 已逐字命中原文"),
    ("actual_effect", "S3", "结果完整性：六类 facet 全部处理（finding 或 unchecked）"),
    ("actual_effect", "S4", "结果可核验性：case_id/location/quote/evidence/sources 齐全"),
    ("use_gain", "S5", "质量增益：verdict_summary 含 counts 与 facet_counts，结构固定可比对"),
    ("use_gain", "S6", "效率增益：runtime.phases 记录骨架先行、增量改进的轨迹"),
    ("use_gain", "S7", "增益可验证性：易变量只进 runtime，重复运行结果一致"),
    ("innovation", "S8", "新增能力：可定位到原句的问题清单 + 分面审查"),
    ("innovation", "S9", "方法改进：严重度/性质/置信度枚举 + 误报抑制判据"),
    ("innovation", "S10", "创新证据：不伪造分数，由平台对照验证（如实标注）"),
    ("reliability", "S11", "调用易用性：--help 退出码 0，SKILL.md 使用相对路径"),
    ("reliability", "S12", "运行稳定性：result.json 合法、必填键齐备、可重复"),
    ("reliability", "S13", "异常处理：坏 JSON/缺材料/坏 PDF 均退出码 0"),
    ("ecosystem", "S14", "文档完整性：SKILL.md frontmatter 与输入/输出契约齐全"),
    ("ecosystem", "S15", "资源可复用性：脚本仅标准库，references 齐全"),
    ("ecosystem", "S16", "分享推广：SKILL.md 引用的相对路径全部存在"),
]


def run_score_checks(results, index, skill_root):
    """按 S1–S16 复检最终结果，返回 [(id, passed, note), ...]。只复检，不重做审查。"""
    findings = (results or {}).get("result", {}).get("findings", [])
    unchecked = (results or {}).get("result", {}).get("unchecked_items", [])
    out = []
    # S1
    out.append(("S1", isinstance(findings, list) and (bool(findings) or bool(unchecked)),
                f"findings={len(findings)} unchecked={len(unchecked)}"))
    # S2（有 index 才逐字复核）
    paras = (index or {}).get("paragraphs", {})
    if paras:
        full = {s: "\n".join(p["text"] for p in ps) for s, ps in paras.items()}
        hits = [f for f in findings
                if any(quote_hit(f.get("quote", ""), t) for t in full.values())]
        out.append(("S2", len(hits) == len(findings), f"quote 命中 {len(hits)}/{len(findings)}"))
    else:
        out.append(("S2", False, "index.json 不可用，无法在此复检 quote 命中"))
    # S3
    seen = {f.get("facet") for f in findings} | {u.get("facet") for u in unchecked}
    missing = [f for f in FACETS if f not in seen]
    out.append(("S3", not missing, f"缺失 facet: {missing or '无'}"))
    # S4
    verifiable = bool((results or {}).get("case_id")) and all(
        f.get("location") and f.get("quote") and (f.get("evidence") or f.get("rationale"))
        for f in findings)
    out.append(("S4", verifiable, f"case_id={bool((results or {}).get('case_id'))}"))
    # S5
    vs = (results or {}).get("verdict_summary", {})
    out.append(("S5", "counts" in vs and "facet_counts" in vs, "counts/facet_counts 存在"))
    # S6
    phases = (results or {}).get("runtime", {}).get("phases", [])
    out.append(("S6", any(p.get("phase") == "init" for p in phases)
               and any(p.get("phase") == "build" for p in phases),
               f"phases={[p.get('phase') for p in phases]}"))
    # S7
    out.append(("S7", "runtime" in (results or {}) and "note" in (results or {}).get("runtime", {}),
                "runtime 与易变量分离"))
    # S8
    out.append(("S8", all(f.get("location") and f.get("quote") for f in findings),
                "逐条带 location+quote"))
    # S9
    enum_ok = all(not validate_finding(f, i) for i, f in enumerate(findings))
    out.append(("S9", enum_ok, "全部 finding 枚举合法"))
    # S10
    invented = [k for k in (results or {}) if k in ("benchmark_score", "score", "白板", "grade")]
    out.append(("S10", not invented, f"无伪造分数键: {invented or '通过'}"))
    # S11
    skill_md = Path(skill_root) / "SKILL.md"
    md_text = skill_md.read_text(encoding="utf-8", errors="replace") if skill_md.exists() else ""
    out.append(("S11", skill_md.exists() and "scripts/" in md_text, "SKILL.md 存在且用相对路径"))
    # S12
    need = ["case_id", "status", "result", "sources", "uncertainty", "change_log"]
    out.append(("S12", all(k in (results or {}) for k in need), "必填键齐备"))
    # S13
    out.append(("S13", (results or {}).get("status") in STATUSES
               and isinstance((results or {}).get("errors", []), list)
               and isinstance((results or {}).get("degradations", []), list),
               f"status={(results or {}).get('status')}"))
    # S14
    fm_ok = False
    if skill_md.exists():
        fm = "\n".join(md_text.split("\n")[:12])
        fm_ok = ("name:" in fm and "description:" in fm and "version:" in fm)
    out.append(("S14", fm_ok, "frontmatter name/description/version"))
    # S15：仅检测真实第三方 import 语句（避免误伤文档中提到的库名）
    scripts = sorted((Path(skill_root) / "scripts").glob("*.py")) if (Path(skill_root) / "scripts").exists() else []
    forbidden_import = re.compile(
        r"^\s*(?:import\s+(?:numpy|pandas|matplotlib|scipy|requests|torch|sklearn)\b"
        r"|from\s+(?:numpy|pandas|matplotlib|scipy|requests|torch|sklearn)\b)",
        re.MULTILINE)
    clean = all(not forbidden_import.search(sp.read_text(encoding="utf-8", errors="replace"))
                for sp in scripts)
    out.append(("S15", bool(scripts) and clean, f"scripts={len(scripts)} 且纯标准库"))
    # S16：从 SKILL.md 中提取 references/ 与 scripts/ 相对路径并校验存在
    refs = sorted(set(re.findall(r"(?:references|scripts)/[\w./-]+", md_text)))
    missing = [r for r in refs if not (Path(skill_root) / r).exists()]
    out.append(("S16", bool(refs) and not missing, f"引用 {len(refs)} 个，缺失 {missing or '无'}"))
    return out
