#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""论文质量审查 Skill 核心实现（仅标准库）。

职责：输入扫描与索引、PDF/DOCX/TXT 文本抽取、机械预审（确定性）、
draft 校验（quote 逐字命中）、结果组装与报告生成、时间预算规划。

本模块只做确定性计算，不做任何模型推理；所有结论要么来自材料逐字片段，
要么显式标注 to_review / not_checked。任何异常都被上层捕获并降级。
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import time
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

FACET_CN = {
    "fact_check": "事实核验",
    "citation_support": "引用支持",
    "method_design": "方法设计",
    "statistics_report": "统计报告",
    "argument_chain": "论证链",
    "academic_norms": "学术规范",
}

SEVERITIES = ["critical", "major", "minor", "suggestion"]
ISSUE_TYPES = ["factual_error", "insufficient_evidence", "standards_issue", "suggestion"]
VERDICTS = ["confirmed", "to_review", "not_checked"]
CONFIDENCES = ["high", "medium", "low"]
STATUSES = ["ok", "partial", "insufficient_evidence", "input_error"]
OVERALL_VERDICTS = ["pass", "revision", "reject", "unable_to_judge"]

# result.json 顶层由本 Skill 拥有的字段；题目 output_contract 的其余键原样回填。
RESERVED_KEYS = {
    "case_id", "status", "sources", "uncertainty", "pending_human",
    "errors", "degradations", "changes", "change_log", "runtime",
    "verdict_summary", "result", "products", "findings", "unchecked_items",
}

TEXT_KINDS = {".txt", ".md", ".markdown", ".json", ".jsonl", ".csv", ".tsv",
              ".xml", ".html", ".htm", ".tex", ".yaml", ".yml", ".rst"}
PDF_KINDS = {".pdf"}
DOCX_KINDS = {".docx"}
IMAGE_KINDS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".svg"}

MAX_TEXT_FILE = 2 * 1024 * 1024  # 2MB，单文件超限跳过并记录，不截断假装全查

# 时间预算（秒）与阶段目标；总预算 900 秒 / 64 次模型请求。
BUDGET_SECONDS = 900
REQUEST_BUDGET = 64
EARLY_LANDING_SECONDS = 180  # 前 3 分钟必须先落盘 partial 骨架
FINALIZE_SECONDS = 780      # 780 秒必须 finalize
PHASES = [
    ("init", 0, EARLY_LANDING_SECONDS,
     "扫描 /input、机械预审、落盘合法 partial result.json 骨架"),
    ("read", EARLY_LANDING_SECONDS, 420,
     "通读 case.json 的 output_contract/task_instruction 与材料原文，确认六类 facet 相关性"),
    ("facet_audit", 420, 660,
     "逐 facet 亲自推理，写 /app/work/draft.json 的 findings"),
    ("check_build", 660, FINALIZE_SECONDS,
     "check 校验逐字 quote 与枚举，build 生成 review_report.md + 最终 result.json"),
    ("finalize", FINALIZE_SECONDS, BUDGET_SECONDS,
     "selfcheck 复检、补齐 unchecked_items/pending_human、硬停"),
]

# ---------------------------------------------------------------------------
# 文本读取与规整
# ---------------------------------------------------------------------------


def read_text(path: Path):
    """按多种编码尝试读取文本文件，返回 (text, problem)。永不抛异常。"""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return None, f"read error: {exc}"
    if len(data) > MAX_TEXT_FILE:
        return None, f"file too large ({len(data)} bytes > {MAX_TEXT_FILE})"
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc), None
        except (UnicodeDecodeError, UnicodeError):
            continue
    return data.decode("utf-8", errors="replace"), "decoded with errors=replace"


def normalize(text: str) -> str:
    """折叠全部空白为单个空格，用于逐字命中比较（quote 与原文同规整）。"""
    return re.sub(r"\s+", " ", text or "").strip()


def split_paragraphs(text: str, page=None):
    """按空行切段，保留段内换行，给出 locator（pN#paraM 或 paraN）。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n[ \t]*\n+", text)
    paras = []
    for block in blocks:
        b = block.strip(" \t\n")
        if not b:
            continue
        paras.append(b)
    out = []
    for i, p in enumerate(paras, 1):
        loc = f"p{page}#para{i}" if page else f"para{i}"
        out.append({"locator": loc, "page": page, "text": p})
    return out


# ---------------------------------------------------------------------------
# 文件分类与材料发现
# ---------------------------------------------------------------------------


def classify_kind(name: str) -> str:
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


def material_files(input_dir: Path):
    """扫描输入目录中除 case.json / task.md 外的全部材料文件。"""
    if not input_dir.exists():
        return []
    skip = {"case.json", "task.md"}
    items = [p for p in sorted(input_dir.rglob("*")) if p.is_file() and p.name not in skip]
    items = [p for p in items if not any(part.startswith(".") for part in p.parts)]
    return items


# ---------------------------------------------------------------------------
# PDF 文本抽取（标准库，尽力而为的兜底；平台 pdf-text-tool 可产出更准的页码）
# ---------------------------------------------------------------------------


def _pdf_unescape(s: str) -> str:
    """解析 PDF 字符串字面量的转义。"""
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1]
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            i += 1
            n = s[i]
            if n in "()\\":
                out.append(n)
            elif n in "nrtbf":
                out.append({"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}[n])
            elif n in "01234567":
                octs = n
                j = i + 1
                while j < len(s) and j < i + 3 and s[j] in "01234567":
                    octs += s[j]
                    j += 1
                out.append(chr(int(octs, 8) & 0xFF))
                i = j - 1
            else:
                out.append(n)
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _extract_text_ops(raw: bytes) -> str:
    """从单个内容流字节中抽出 (text) Tj 与 [...] TJ 文本算子。"""
    s = raw.decode("latin-1", "replace")
    out = []
    for m in re.finditer(r"\((?:[^()\\]|\\.)*\)\s*Tj", s):
        seg = m.group(0)
        open_paren = seg.find("(")
        close_paren = seg.rfind(")")
        if close_paren > open_paren:
            out.append(_pdf_unescape(seg[open_paren:close_paren + 1]))
    for m in re.finditer(r"\[((?:[^\[\]\\]|\\.)*)\]\s*TJ", s):
        for t in re.finditer(r"\((?:[^()\\]|\\.)*\)", m.group(1)):
            out.append(_pdf_unescape(t.group(0)))
    return "".join(out)


def extract_pdf_text(data: bytes):
    """返回 (pages: list[str], problems: list[str])。页面级定位尽力而为。"""
    problems = []
    if not data.startswith(b"%PDF"):
        return [], ["not a PDF (missing %PDF header)"]

    # 1) 解析所有内容流对象，按对象号索引解码文本
    stream_text = {}
    for m in re.finditer(rb"(\d+)\s+\d+\s+obj", data):
        num = int(m.group(1))
        s = data.find(b"stream", m.end())
        if s < 0:
            continue
        e = data.find(b"endstream", s)
        if e < 0:
            continue
        start = s + 6
        if data[start:start + 1] == b"\r":
            start += 1
            if data[start:start + 1] == b"\n":
                start += 1
        elif data[start:start + 1] == b"\n":
            start += 1
        raw = data[start:e].rstrip(b"\r\n")
        header = data[m.end():s].decode("latin-1", "replace")
        if "FlateDecode" in header or raw[:2] in (b"x\x9c", b"\x78\x9c"):
            try:
                raw = zlib.decompress(raw)
            except Exception:
                problems.append(f"stream {num}: zlib decompress failed")
        txt = _extract_text_ops(raw)
        if txt.strip():
            stream_text[num] = txt
    if not stream_text:
        return [], problems + ["no text extracted from content streams"]

    # 2) 把内容流按 /Type /Page 的 /Contents 引用归入各页
    pages = []
    for pm in re.finditer(rb"/Type\s*/Page\b", data):
        window = data[max(0, pm.start() - 4000):min(len(data), pm.end() + 6000)]
        cm = re.search(rb"/Contents\s*(\[[^\]]*\]|\d+\s+\d+\s+R)", window, re.S)
        refs = []
        if cm:
            refs = [int(r) for r in re.findall(rb"(\d+)\s+\d+\s+R", cm.group(1))]
        texts = [stream_text[r] for r in refs if r in stream_text]
        if texts:
            pages.append("\n".join(texts))
    if not pages:
        ordered = [stream_text[k] for k in sorted(stream_text)]
        pages = ["\n".join(ordered)]
        problems.append("page-level locator unavailable; text concatenated into one page")
    return pages, problems


def extract_docx_paras(data: bytes):
    """返回 (paragraphs: list[str], problem: str|None)。仅标准库解压 word/document.xml。"""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if "word/document.xml" not in z.namelist():
                return [], "word/document.xml missing"
            root = ET.fromstring(z.read("word/document.xml"))
    except Exception as exc:
        return [], f"docx parse error: {exc}"
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paras = []
    for p in root.iter(w + "p"):
        line = "".join(t.text or "" for t in p.iter(w + "t")).strip()
        if line:
            paras.append(line)
    return paras, None


def _extract_material(path: Path, kind: str):
    """把单个材料文件抽取为段落列表，返回 (paras, problems)。"""
    if kind in ("image", "other"):
        return [], [f"{kind}: no text layer; needs OCR / external tool"]
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [], [f"read error: {exc}"]
    if kind == "pdf":
        pages, problems = extract_pdf_text(data)
        paras = []
        for pi, pg in enumerate(pages, 1):
            paras.extend(split_paragraphs(pg, page=pi))
        if not paras:
            return [], problems + ["no paragraphs extracted"]
        return paras, problems
    if kind == "docx":
        lines, problem = extract_docx_paras(data)
        if problem and not lines:
            return [], [problem]
        text = "\n\n".join(lines)
        return split_paragraphs(text, page=None), ([problem] if problem else [])
    text, problem = read_text(path)
    if text is None:
        return [], [problem or "unreadable"]
    return split_paragraphs(text, page=None), ([problem] if problem else [])


# ---------------------------------------------------------------------------
# case.json 扫描与契约回填
# ---------------------------------------------------------------------------


def contract_keys_of(output_contract):
    """把 output_contract（dict 或 list）统一成 dict，未知键写 null/[]。"""
    if isinstance(output_contract, dict):
        return {k: v for k, v in output_contract.items()}
    if isinstance(output_contract, list):
        return {str(k): None for k in output_contract}
    return {}


def scan_input(input_dir: Path):
    state = {
        "case_id": None,
        "task_instruction": "",
        "output_contract": None,
        "contract_keys": [],
        "degradations": [],
        "errors": [],
        "case_json_bad": False,
        "case_json_missing": False,
    }
    case_path = input_dir / "case.json"
    if not case_path.exists():
        state["case_json_missing"] = True
        state["degradations"].append("case.json missing; contract unknown")
        return state
    text, problem = read_text(case_path)
    if text is None:
        state["errors"].append({"file": "case.json", "error": problem})
        state["degradations"].append("case.json unreadable")
        state["case_json_bad"] = True
        return state
    if problem:
        state["degradations"].append(f"case.json encoding: {problem}")
    try:
        case = json.loads(text)
    except Exception as exc:
        state["errors"].append({"file": "case.json", "error": f"bad JSON: {exc}"})
        state["degradations"].append("case.json bad JSON; contract unknown")
        state["case_json_bad"] = True
        return state
    if not isinstance(case, dict):
        state["errors"].append({"file": "case.json", "error": "top-level not an object"})
        state["case_json_bad"] = True
        return state
    state["case_id"] = case.get("case_id") or case.get("id") or case.get("task_id")
    state["task_instruction"] = (case.get("task_instruction") or case.get("task")
                                 or case.get("instruction") or "")
    state["output_contract"] = case.get("output_contract")
    state["contract_keys"] = sorted(contract_keys_of(state["output_contract"]).keys())
    return state


# ---------------------------------------------------------------------------
# 索引构建与预抽取文本落盘
# ---------------------------------------------------------------------------


def _dump_work_text(work_dir: Path, sid: str, paras):
    pages = {}
    for p in paras:
        pg = p["page"] if p["page"] is not None else 1
        pages.setdefault(pg, []).append(p["text"])
    ordered = [pages[k] for k in sorted(pages)]
    if len(ordered) == 1 and all(p["page"] is None for p in paras):
        content = "\n\n".join(ordered[0])
    else:
        content = "\f".join("\n\n".join(chunk) for chunk in ordered)
    d = work_dir / "text"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.txt").write_text(content, encoding="utf-8", newline="\n")


def build_index(input_dir: Path, work_dir: Path, rescan: bool = False):
    state = scan_input(input_dir)
    index = {
        "case_id": state["case_id"],
        "task_instruction": state["task_instruction"],
        "output_contract": state["output_contract"],
        "contract_keys": state["contract_keys"],
        "case_json_bad": state["case_json_bad"],
        "case_json_missing": state["case_json_missing"],
        "degradations": list(state["degradations"]),
        "errors": list(state["errors"]),
        "sources": [],
        "content_hash": None,
        "generated_at": time.time(),
    }
    paras_by_source = {}
    hasher = hashlib.sha256()
    for path in material_files(input_dir):
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        hasher.update(path.name.encode("utf-8", "replace"))
        hasher.update(str(size).encode("ascii"))
        sid = f"D{len(index['sources']) + 1}"
        kind = classify_kind(path.name)
        entry = {
            "source_id": sid, "kind": kind, "name": path.name,
            "path": str(path), "size": size, "pages": None,
            "paragraphs": 0, "problems": [], "locator_scheme": "D#paraN",
        }
        paras = []
        if size > MAX_TEXT_FILE:
            entry["problems"].append(f"size {size} > {MAX_TEXT_FILE}; skipped, not truncated")
            index["degradations"].append(f"{sid} {path.name} too large; skipped")
        elif kind in ("image", "other"):
            entry["problems"].append(f"{kind}: no text layer; needs OCR / external tool")
            index["degradations"].append(f"{sid} {path.name} is {kind}; no text layer")
        else:
            pre = work_dir / "text" / f"{sid}.txt"
            if not rescan and pre.exists():
                text, prob = read_text(pre)
                if text is not None:
                    pages = [pg for pg in text.split("\f") if pg.strip()] or [text]
                    for pi, pg in enumerate(pages, 1):
                        paras.extend(split_paragraphs(pg, page=pi if len(pages) > 1 else None))
                    entry["problems"].append(f"reused pre-extracted text {pre.name}")
                else:
                    paras, probs = _extract_material(path, kind)
                    entry["problems"].extend(probs)
            else:
                paras, probs = _extract_material(path, kind)
                entry["problems"].extend(probs)
        if paras:
            entry["paragraphs"] = len(paras)
            pages = sorted({p["page"] for p in paras if p["page"] is not None})
            entry["pages"] = pages if pages else None
            entry["locator_scheme"] = "D#pN#paraM" if entry.get("pages") else "D#paraN"
            _dump_work_text(work_dir, sid, paras)
            for p in paras:
                hasher.update(p["text"].encode("utf-8", "replace"))
        elif entry["problems"]:
            index["degradations"].append(f"{sid} {path.name}: " + "; ".join(entry["problems"]))
        index["sources"].append(entry)
        paras_by_source[sid] = paras
    index["content_hash"] = hasher.hexdigest()
    return index, paras_by_source


# ---------------------------------------------------------------------------
# 机械预审：确定性、保守、可复核；只在 init 时运行，为早落盘骨架注入真实内容
# ---------------------------------------------------------------------------

SECTION_MARKERS = {
    "abstract": ["abstract", "摘要", "内容提要", "概要"],
    "introduction": ["introduction", "引言", "背景", "前言", "研究背景"],
    "methods": ["method", "methods", "材料与方法", "方法", "实验方法", "研究设计", "实验设计"],
    "results": ["result", "results", "结果", "实验结果"],
    "discussion": ["discussion", "discussion and conclusion", "讨论", "结论与讨论"],
    "references": ["reference", "references", "参考文献", "bibliography", "文献"],
}

CITE_NUMERIC = re.compile(r"\[\s*\d+(?:\s*[-,–]\s*\d+)*(?:\s*,\s*\d+)*\s*\]")
CITE_AUTHYEAR = re.compile(
    r"\([A-Z][A-Za-z'’-]+(?:\s+(?:et\s+al\.?|and|&)\s+[A-Z][A-Za-z'’-]+)?,\s*\d{4}[a-z]?\)")


def _missing_sections(norm: str):
    low = norm.lower()
    return [sec for sec, markers in SECTION_MARKERS.items()
            if not any(mk in low for mk in markers)]


def _count_citation_markers(norm: str):
    return len(CITE_NUMERIC.findall(norm)) + len(CITE_AUTHYEAR.findall(norm))


def _numeric_conflicts(paras, sid: str):
    pat = re.compile(r"\b([Nn])\s*=\s*(\d+)\b")
    seen = {}
    for p in paras:
        for m in pat.finditer(p["text"]):
            label = m.group(1).lower()
            val = int(m.group(2))
            seen.setdefault(label, {}).setdefault(val, []).append(
                {"locator": p["locator"], "quote": m.group(0), "source_id": sid})
    conflicts = []
    for label, byval in seen.items():
        if len(byval) > 1:
            vals = sorted(byval)
            occ = [byval[v][0] for v in vals]
            conflicts.append({"label": label, "values": vals, "occurrences": occ})
    return conflicts


def mechanical_scan(index, paras_by_source):
    """返回 (findings, uncertainties)。只标记真实可定位的现象，结论一律保守。"""
    findings = []
    uncertainties = []
    nid = 1
    for src in index["sources"]:
        sid = src["source_id"]
        paras = paras_by_source.get(sid, [])
        if not paras:
            continue
        full = "\n".join(p["text"] for p in paras)
        norm = normalize(full)
        looks_full = len(paras) >= 8 and len(full) > 1500

        if looks_full:
            for sec in _missing_sections(norm):
                findings.append({
                    "finding_id": f"M{nid:02d}", "facet": "academic_norms",
                    "severity": "suggestion", "issue_type": "standards_issue",
                    "verdict": "to_review",
                    "claim": f"未检测到明显「{sec}」章节标题（若为报告/片段可忽略）",
                    "location": sid, "quote": "",
                    "rationale": "机械结构扫描：常见章节标题关键词未命中，非正文内容判断。",
                    "revision_suggestion": f"确认是否缺 {sec} 章节；若体裁非完整论文，写入 unchecked_items 说明不适用。",
                    "confidence": "low", "evidence": [], "mechanical": True,
                })
                nid += 1

        for c in _numeric_conflicts(paras, sid):
            vals = c["values"]
            occ = c["occurrences"]
            findings.append({
                "finding_id": f"M{nid:02d}", "facet": "fact_check",
                "severity": "major", "issue_type": "factual_error",
                "verdict": "to_review",
                "claim": f"同一符号 {c['label']!r} 在正文出现 {len(vals)} 个不同数值 {vals}，存在数值冲突",
                "location": " / ".join(f"{sid}#{o['locator']}" for o in occ),
                "quote": occ[0]["quote"],
                "rationale": "机械一致性扫描发现同一标识符被赋予不同数值，冲突本身已确认，孰对孰错待核。",
                "revision_suggestion": "核对两处上下文，统一正确数值或说明为不同群体/时点，消除歧义。",
                "confidence": "high",
                "evidence": occ,
                "mechanical": True,
            })
            nid += 1
            uncertainties.append({
                "item": f"{sid} {c['label']} 数值冲突 {vals}",
                "reason": "source_conflict",
                "resolution": "需回到材料上下文确定正确数值，不擅自取舍",
            })

        cite_count = _count_citation_markers(norm)
        if looks_full and cite_count == 0:
            findings.append({
                "finding_id": f"M{nid:02d}", "facet": "citation_support",
                "severity": "suggestion", "issue_type": "insufficient_evidence",
                "verdict": "to_review",
                "claim": "全文未检测到任何数字/作者-年份引用标记，关键论断的引用支撑无法核验",
                "location": sid, "quote": "",
                "rationale": "机械扫描在正文未命中引用标记；可能为无引用体裁或引用以其他格式书写。",
                "revision_suggestion": "逐条核对关键论断是否给出可定位引文；若体裁无需引用则说明不适用。",
                "confidence": "low", "evidence": [], "mechanical": True,
            })
            nid += 1
    return findings, uncertainties


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def count_findings(findings):
    c = {"total": len(findings)}
    for sev in SEVERITIES:
        c[sev] = sum(1 for f in findings if f.get("severity") == sev)
    for facet in FACETS:
        c[facet] = sum(1 for f in findings if f.get("facet") == facet)
    return c


def validate_finding(f, full_norm: str, paras):
    errors, warnings = [], []
    fid = f.get("finding_id", "?") if isinstance(f, dict) else "?"
    if not isinstance(f, dict):
        return [f"{fid}: finding is not an object"], []
    required = ["finding_id", "facet", "severity", "issue_type", "verdict",
                "claim", "location", "quote", "rationale", "revision_suggestion",
                "confidence"]
    for r in required:
        if f.get(r) in (None, ""):
            if r == "quote" and f.get("verdict") == "not_checked":
                continue
            errors.append(f"{fid}: missing field {r}")
    if f.get("facet") not in FACETS:
        errors.append(f"{fid}: facet {f.get('facet')!r} not in {FACETS}")
    if f.get("severity") not in SEVERITIES:
        errors.append(f"{fid}: severity {f.get('severity')!r} invalid")
    if f.get("issue_type") not in ISSUE_TYPES:
        errors.append(f"{fid}: issue_type {f.get('issue_type')!r} invalid")
    if f.get("verdict") not in VERDICTS:
        errors.append(f"{fid}: verdict {f.get('verdict')!r} invalid")
    if f.get("confidence") not in CONFIDENCES:
        errors.append(f"{fid}: confidence {f.get('confidence')!r} invalid")
    q = normalize(f.get("quote", ""))
    if q and f.get("verdict") != "not_checked":
        if q not in full_norm:
            errors.append(f"{fid}: quote not found verbatim in source text")
        elif not any(q in normalize(p.get("text", "")) for p in paras):
            warnings.append(f"{fid}: quote spans paragraphs (allowed only within one paragraph)")
    loc = f.get("location", "")
    if loc and not re.match(r"^D\d+(#.*)?$", loc):
        warnings.append(f"{fid}: location {loc!r} does not look like a locator")
    return errors, warnings


def validate_draft(draft, index, paras_by_source):
    report = {
        "errors": [], "warnings": [], "coverage": {},
        "checked_facets": [], "unhandled_facets": [], "n_a_facets": [],
        "counts": count_findings([]),
    }
    if not isinstance(draft, dict):
        report["errors"].append("draft is not an object")
        return report
    findings = draft.get("findings")
    if not isinstance(findings, list):
        report["errors"].append("findings missing or not a list")
        findings = []
    full_norm_by_sid = {}
    paras_by_sid = {}
    for sid, paras in paras_by_source.items():
        full_norm_by_sid[sid] = normalize("\n".join(p.get("text", "") for p in paras))
        paras_by_sid[sid] = paras
    seen = set()
    for f in findings:
        fid = f.get("finding_id", "?") if isinstance(f, dict) else "?"
        if fid in seen:
            report["errors"].append(f"{fid}: duplicate finding_id")
        seen.add(fid)
        sid = (f.get("location") or "").split("#")[0]
        e, w = validate_finding(f, full_norm_by_sid.get(sid, ""), paras_by_sid.get(sid, []))
        report["errors"].extend(e)
        report["warnings"].extend(w)
    cov = draft.get("coverage", {}) if isinstance(draft, dict) else {}
    for facet in FACETS:
        state = cov.get(facet, "not_checked")
        has = any(f.get("facet") == facet for f in findings)
        if state not in ("checked", "not_checked", "n/a"):
            report["errors"].append(f"coverage.{facet} invalid value {state!r}")
        if state == "n/a":
            report["coverage"][facet] = "n/a"
            report["n_a_facets"].append(facet)
        elif state == "checked":
            report["coverage"][facet] = "checked"
            report["checked_facets"].append(facet)
        else:
            report["coverage"][facet] = "not_checked"
            report["unhandled_facets"].append(facet)
        if has and state not in ("checked", "n/a"):
            report["warnings"].append(
                f"facet {facet} has findings but coverage={state}; set coverage to checked when audit is complete")
    report["counts"] = count_findings(findings)
    return report


def compute_summary(draft):
    findings = draft.get("findings", []) if isinstance(draft, dict) else []
    counts = count_findings(findings)
    coverage = {}
    checked = []
    unhandled = []
    n_a = []
    cov = draft.get("coverage", {}) if isinstance(draft, dict) else {}
    for facet in FACETS:
        has = any(f.get("facet") == facet for f in findings)
        state = cov.get(facet, "not_checked")
        if state == "n/a":
            coverage[facet] = "n/a"
            n_a.append(facet)
        elif state == "checked":
            coverage[facet] = "checked"
            checked.append(facet)
        else:
            coverage[facet] = "not_checked"
            unhandled.append(facet)
    return {
        "coverage": coverage,
        "counts": counts,
        "checked_count": len(checked),
        "unhandled_facets": unhandled,
        "n_a_facets": n_a,
    }


def compute_status(index, summary):
    if index.get("case_json_bad"):
        return "input_error"
    has_material = any(s.get("paragraphs") for s in index.get("sources", []))
    if not has_material:
        return "insufficient_evidence"
    if not summary["unhandled_facets"]:
        return "ok"
    return "partial"


# ---------------------------------------------------------------------------
# 报告与 result.json 组装
# ---------------------------------------------------------------------------


def _iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def build_report_md(draft, index, summary, status):
    findings = draft.get("findings", []) if isinstance(draft, dict) else []
    unchecked = draft.get("unchecked_items", []) if isinstance(draft, dict) else []
    uncertainties = draft.get("uncertainty", []) if isinstance(draft, dict) else []
    pending = draft.get("pending_human", []) if isinstance(draft, dict) else []
    overall = draft.get("overall_verdict", "unable_to_judge")
    L = []
    L.append("# 论文质量审查报告")
    L.append("")
    L.append(f"- case_id: {index.get('case_id') or '（未提供）'}")
    L.append(f"- status: {status}")
    L.append(f"- 总体结论: {overall}")
    L.append(f"- 生成时间: {_iso()}")
    L.append("")
    L.append("## 审查结论概要")
    L.append("")
    c = summary["counts"]
    L.append(f"- 问题总数 {c['total']}：critical {c['critical']} / major {c['major']} / minor {c['minor']} / suggestion {c['suggestion']}")
    L.append("- 分面覆盖：")
    for facet in FACETS:
        L.append(f"  - {FACET_CN[facet]}（{facet}）: {summary['coverage'].get(facet, 'not_checked')}，命中 {c.get(facet, 0)} 条")
    L.append("")
    L.append("## 问题清单（按分面）")
    L.append("")
    if not findings:
        L.append("（本稿未产出 finding；若状态为 partial，请见“未检查项”。）")
        L.append("")
    for facet in FACETS:
        ff = [f for f in findings if f.get("facet") == facet]
        if not ff:
            continue
        L.append(f"### {FACET_CN[facet]}（{facet}）")
        L.append("")
        for f in ff:
            L.append(f"- **{f.get('finding_id')}** [{f.get('severity')}/{f.get('issue_type')}/{f.get('verdict')}] 定位 `{f.get('location')}`")
            L.append(f"  - 问题：{f.get('claim')}")
            if f.get("quote"):
                L.append(f"  - 原文：`{f.get('quote')}`")
            L.append(f"  - 依据：{f.get('rationale')}")
            L.append(f"  - 建议：{f.get('revision_suggestion')}")
        L.append("")
    L.append("## 未检查项")
    L.append("")
    if unchecked:
        for u in unchecked:
            if isinstance(u, dict):
                L.append(f"- {FACET_CN.get(u.get('facet', ''), u.get('facet', ''))}：{u.get('reason', '')}")
            else:
                L.append(f"- {u}")
    else:
        L.append("（无）")
    L.append("")
    L.append("## 不确定项")
    L.append("")
    if uncertainties:
        for u in uncertainties:
            if isinstance(u, dict):
                L.append(f"- {u.get('item', '')}：{u.get('reason', '')}｜处置：{u.get('resolution', '')}")
            else:
                L.append(f"- {u}")
    else:
        L.append("（无）")
    L.append("")
    L.append("## 待人工复核")
    L.append("")
    if pending:
        for p in pending:
            L.append(f"- {p}")
    else:
        L.append("（无）")
    L.append("")
    return "\n".join(L)


def build_result(draft, index, summary, status, started_at, change_log):
    """组装最终 result.json。output_contract 全部键原样回填（保留键被信封覆盖）。"""
    result = {}
    contract = contract_keys_of(index.get("output_contract"))
    for k, v in contract.items():
        if k not in RESERVED_KEYS:
            result[k] = v
    result["case_id"] = index.get("case_id")
    result["status"] = status
    result["sources"] = [{
        "source_id": s["source_id"], "kind": s["kind"], "name": s["name"],
        "path": s["path"], "size": s["size"], "pages": s["pages"],
        "paragraphs": s["paragraphs"], "locator_scheme": s["locator_scheme"],
        "problems": s["problems"],
    } for s in index.get("sources", [])]
    result["uncertainty"] = list(draft.get("uncertainty", []) if isinstance(draft, dict) else [])
    result["pending_human"] = list(draft.get("pending_human", []) if isinstance(draft, dict) else [])
    result["errors"] = list(index.get("errors", [])) + list(draft.get("errors", []) if isinstance(draft, dict) else [])
    result["degradations"] = list(index.get("degradations", [])) + list(draft.get("degradations", []) if isinstance(draft, dict) else [])
    result["changes"] = list(draft.get("changes", []) if isinstance(draft, dict) else [])
    result["change_log"] = list(change_log or [])
    ended = time.time()
    result["runtime"] = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started_at)),
        "ended_at": _iso(),
        "elapsed_seconds": round(ended - started_at, 1),
        "time_budget_seconds": BUDGET_SECONDS,
        "request_budget": REQUEST_BUDGET,
        "early_landing_seconds": EARLY_LANDING_SECONDS,
        "finalize_seconds": FINALIZE_SECONDS,
        "status": status,
        "content_hash": index.get("content_hash"),
        "model_requests_used": None,  # 平台记录实际调用，不自行编造
    }
    result["verdict_summary"] = {
        "coverage": summary["coverage"],
        "counts": summary["counts"],
        "overall_verdict": draft.get("overall_verdict", "unable_to_judge") if isinstance(draft, dict) else "unable_to_judge",
    }
    result["result"] = {
        "findings": draft.get("findings", []) if isinstance(draft, dict) else [],
        "unchecked_items": draft.get("unchecked_items", []) if isinstance(draft, dict) else [],
    }
    result["products"] = {"review_report": "review_report.md"}
    return result


# ---------------------------------------------------------------------------
# 时间预算规划
# ---------------------------------------------------------------------------


def phase_at(elapsed: float):
    for name, start, end, goal in PHASES:
        if start <= elapsed < end:
            return {"phase": name, "start": start, "end": end, "goal": goal}
    return {"phase": "finalize", "start": FINALIZE_SECONDS, "end": BUDGET_SECONDS,
            "goal": "selfcheck 复检、补齐 unchecked/pending、硬停"}


def plan_text(index, draft, started_at):
    elapsed = max(0.0, time.time() - started_at)
    remaining = BUDGET_SECONDS - elapsed
    ph = phase_at(elapsed)
    summary = compute_summary(draft) if isinstance(draft, dict) else {"unhandled_facets": list(FACETS), "coverage": {}, "counts": {}}
    L = []
    L.append(f"已用 {elapsed:.0f}s / 剩余 {remaining:.0f}s（预算 {BUDGET_SECONDS}s，模型请求预算 {REQUEST_BUDGET}）")
    L.append(f"当前阶段: {ph['phase']}（{ph['start']}-{ph['end']}s）目标：{ph['goal']}")
    L.append("")
    L.append("阶段表：")
    for name, start, end, goal in PHASES:
        mark = " <- 当前" if name == ph["phase"] else ""
        L.append(f"  [{start:>3}-{end:>3}s] {name}: {goal}{mark}")
    L.append("")
    if not isinstance(draft, dict) or not draft.get("findings"):
        L.append("draft.json 尚无 findings（骨架或未开始）。")
    else:
        L.append(f"findings 总数 {len(draft.get('findings', []))}，分面覆盖：")
        for facet in FACETS:
            L.append(f"  - {FACET_CN[facet]}: {summary['coverage'].get(facet, 'not_checked')}")
    unhandled = summary.get("unhandled_facets", [])
    L.append("")
    if elapsed >= FINALIZE_SECONDS:
        L.append("【立即行动】已进入 finalize：马上 build + selfcheck，未完成项写入 unchecked_items/pending_human，status=partial 后停。")
    elif elapsed >= PHASES[3][1]:
        L.append(f"【下一步】剩余 facet：{', '.join(unhandled) or '无'}；完成剩余 finding 后立即 check + build。")
    elif elapsed >= PHASES[2][1]:
        L.append(f"【下一步】逐 facet 写 finding（quote 用 show 抄录），优先：{', '.join(unhandled) or '无'}。")
    elif elapsed >= EARLY_LANDING_SECONDS:
        L.append("【下一步】继续通读材料，确认六类 facet 中与本稿相关的部分。")
    else:
        L.append("【下一步】正在早落盘窗口：确保 init 已产出合法 partial result.json 骨架。")
    return "\n".join(L)
