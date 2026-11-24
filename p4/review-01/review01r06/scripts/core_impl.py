#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""论文质量审查 Skill 的共享实现：常量、文本抽取、Schema 校验。

仅依赖 Python 标准库。本模块不依赖安装路径，可由 main.py / selftest.py
在任意目录通过同目录导入使用。
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import zipfile
import zlib
import xml.etree.ElementTree as ET
from pathlib import Path

sys.dont_write_bytecode = True  # 运行时不落盘 __pycache__/.pyc

# ---------------------------------------------------------------------------
# 枚举与常量（与 references/output-schema.md 保持一致）
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
ISSUE_TYPES = ["factual_error", "insufficient_evidence", "standards_issue",
               "suggestion"]
VERDICTS = ["confirmed", "to_review", "not_checked"]
CONFIDENCES = ["high", "medium", "low"]
STATUSES = ["ok", "partial", "insufficient_evidence", "input_error"]
OVERALL_VERDICTS = ["reject", "major_revision", "minor_revision",
                    "accept", "undetermined"]

ENVELOPE_KEYS = [
    "status", "sources", "uncertainty", "pending_human", "errors",
    "degradations", "changes", "change_log", "runtime",
]

MAX_TEXT_FILE = 2 * 1024 * 1024  # 2 MB，超限跳过并记录 degradation

TEXT_KINDS = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl",
              ".xml", ".html", ".htm", ".tex", ".bib", ".ris", ".enl",
              ".log", ".yaml", ".yml", ".rst"}
PDF_KINDS = {".pdf"}
DOCX_KINDS = {".docx"}
IMAGE_KINDS = {".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".bmp",
               ".webp", ".svg"}

FACET_KEYWORDS = {
    "fact_check": ["事实", "fact", "一致", "数据", "数值", "矛盾", "冲突",
                   "consist", "inconsist", "abstract"],
    "citation_support": ["引用", "citation", "reference", "文献", "cite",
                         "bibliograph", "support"],
    "method_design": ["方法", "method", "对照", "control", "样本", "sample",
                      "设计", "design", "实验", "experiment", "baseline"],
    "statistics_report": ["统计", "statistic", "显著性", "significance",
                          "误差", "置信", "p 值", "p值", "p-value",
                          "置信区间", "confidence interval", " p <", " p≤",
                          " p ≤", "error bar", "effect size"],
    "argument_chain": ["论证", "逻辑", "argument", "logic", "因果", "causal",
                       "结论", "conclusion", "假设", "hypothesis",
                       "外推", "generaliz"],
    "academic_norms": ["规范", "伦理", "ethics", "利益冲突", "conflict of "
                       "interest", "数据可用", "data availab", "版权",
                       "copyright", "声明", "statement", "compliance"],
}


# ---------------------------------------------------------------------------
# 文本读取与段落切分
# ---------------------------------------------------------------------------
def read_text(path: Path):
    """读取文本文件，返回 (text, problem)。多编码容错，超限返回 None。"""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return None, f"read error: {exc}"
    if len(data) > MAX_TEXT_FILE:
        return None, f"file {len(data)} bytes > {MAX_TEXT_FILE} bytes"
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc), None
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("latin-1"), "fallback latin-1 decode"


def normalize_ws(text: str) -> str:
    """折叠所有空白（含换行/换页）为单个空格，用于逐字命中校验。"""
    return re.sub(r"\s+", " ", text or "").strip()


def split_paragraphs(text: str, page=None):
    """按空行分段落；段内行合并为单行文本。返回 [{text,page}]。"""
    paras = []
    buf = []
    for line in (text or "").splitlines():
        if line.strip():
            buf.append(line.strip())
        else:
            if buf:
                paras.append({"text": " ".join(buf), "page": page})
                buf = []
    if buf:
        paras.append({"text": " ".join(buf), "page": page})
    return paras


# ---------------------------------------------------------------------------
# PDF 文本抽取：优先 pdftotext（外部工具），失败退回纯标准库 zlib 抽取
# ---------------------------------------------------------------------------
def _pdftotext_subprocess(data: bytes):
    try:
        proc = subprocess.run(
            ["pdftotext", "-enc", "UTF-8", "-", "-"],
            input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=120, check=False)
        if proc.returncode == 0 and proc.stdout.strip():
            text = proc.stdout.decode("utf-8", errors="replace")
            pages = text.split("\f")
            return [p for p in pages if p.strip()], []
    except Exception as exc:  # 工具缺失/超时/任何异常都不崩溃
        return [], [f"pdftotext unavailable: {exc}"]
    return [], []


def _paren_end(s: str, start: int) -> int:
    depth = 0
    i = start
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n - 1


def _pdf_unescape(s: str) -> str:
    out = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            nxt = s[i + 1]
            mapping = {"n": "\n", "r": "\r", "t": "\t", "b": "\b",
                       "f": "\f", "(": "(", ")": ")", "\\": "\\"}
            if nxt in mapping:
                out.append(mapping[nxt])
                i += 2
                continue
            if nxt in "01234567":
                oct_digits = s[i + 1:i + 4]
                try:
                    out.append(chr(int(oct_digits, 8)))
                    i += 4
                    continue
                except ValueError:
                    pass
            out.append(nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _hex_string(s: str) -> str:
    s = re.sub(r"\s+", "", s)
    if len(s) % 2:
        s += "0"
    try:
        raw = bytes.fromhex(s)
    except ValueError:
        return ""
    for enc in ("utf-16-be", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _content_stream_text(content: bytes) -> str:
    try:
        s = content.decode("latin-1")
    except Exception:
        return ""
    out = []
    i = 0
    n = len(s)
    in_text = False
    while i < n:
        if s[i] == "B" and s.startswith("BT", i):
            in_text = True
            i += 2
            continue
        if s[i] == "E" and s.startswith("ET", i):
            in_text = False
            i += 2
            continue
        if not in_text:
            i += 1
            continue
        if s[i] == "(":
            j = _paren_end(s, i)
            out.append(_pdf_unescape(s[i + 1:j]))
            i = j + 1
            continue
        if s[i] == "<" and (i + 1 >= n or s[i + 1] != "<"):
            j = s.find(">", i)
            if j > i:
                out.append(_hex_string(s[i + 1:j]))
                i = j + 1
                continue
        if s.startswith("TJ", i) or s.startswith("Tj", i) or \
                s.startswith("T*", i) or s.startswith("Td", i) or \
                s.startswith("TD", i) or s.startswith("'", i):
            out.append("\n")
            i += 2
            continue
        i += 1
    text = "".join(out)
    text = re.sub(r"\n{2,}", "\n", text)
    return text


def _extract_pdf_text_pure(data: bytes):
    problems = []
    chunks = []
    for m in re.finditer(rb"stream\r?\n", data):
        start = m.end()
        end = data.find(b"endstream", start)
        if end < 0:
            continue
        raw = data[start:end].lstrip(b"\r\n").rstrip(b"\r\n")
        content = raw
        try:
            content = zlib.decompress(raw)
        except Exception:
            pass  # 非 Flate 流，按原样处理
        text = _content_stream_text(content)
        if text.strip():
            chunks.append(text)
    if not chunks:
        return [], ["pure-python fallback found no text layer"]
    joined = "\n".join(chunks)
    pages = [p for p in joined.split("\f") if p.strip()] or [joined]
    if len(pages) == 1:
        problems.append("pure-python fallback: page boundaries approximate")
    return pages, problems


def extract_pdf_text(data: bytes):
    """返回 (pages: list[str], problems: list[str])。页间以 \\f 分隔。"""
    pages, problems = _pdftotext_subprocess(data)
    if pages:
        return pages, problems
    pages2, problems2 = _extract_pdf_text_pure(data)
    return pages2, problems + problems2


# ---------------------------------------------------------------------------
# DOCX 文本抽取（zipfile + ElementTree，仅标准库）
# ---------------------------------------------------------------------------
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_xml_paras(root):
    lines = []
    for p in root.iter(_W_NS + "p"):
        texts = [t.text or "" for t in p.iter(_W_NS + "t")]
        line = "".join(texts).strip()
        if line:
            lines.append(line)
    return lines


def extract_docx_paras(data: bytes):
    """返回 (lines: list[str], problem: str|None)。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        names = set(zf.namelist())
        target = "word/document.xml"
        if target not in names:
            return [], "docx missing word/document.xml"
        root = ET.fromstring(zf.read(target))
        lines = _docx_xml_paras(root)
        for extra in ("word/footnotes.xml", "word/endnotes.xml"):
            if extra in names:
                try:
                    lines += _docx_xml_paras(ET.fromstring(zf.read(extra)))
                except Exception:
                    pass
        return lines, None
    except Exception as exc:
        return [], f"docx parse error: {exc}"


# ---------------------------------------------------------------------------
# output_contract 处理与 finding 校验
# ---------------------------------------------------------------------------
def contract_keys_of(contract):
    """返回 contract（dict 或 list[dict]）中出现的全部键名，保持顺序。"""
    keys = []
    if isinstance(contract, dict):
        keys = [str(k) for k in contract.keys()]
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, dict):
                for k in item.keys():
                    if str(k) not in keys:
                        keys.append(str(k))
    return keys


def blank_value_for(contract, key):
    """按未知键类型给 null 或 [] 占位（回填未知键时使用）。"""
    if isinstance(contract, dict):
        val = contract.get(key)
        if isinstance(val, list):
            return []
        if isinstance(val, dict):
            return {}
    return None


def merge_contract_into(result, contract):
    """把 output_contract 原样回填到 result 顶层。

    契约键先写入；信封键后写入。若契约键与信封键重名，信封键生效，
    并在 changes 中记录被保留的契约值。返回 changes 列表。
    """
    changes = []
    envelope_set = set(ENVELOPE_KEYS)
    if isinstance(contract, dict):
        for k, v in contract.items():
            k = str(k)
            if k in envelope_set:
                changes.append({
                    "field": k,
                    "from": v,
                    "to": None,
                    "reason": "contract key collides with envelope key; "
                              "envelope value takes precedence",
                })
                continue
            result[k] = v
    elif isinstance(contract, list):
        result["output_contract"] = contract  # 保留原始结构
        for item in contract:
            if isinstance(item, dict):
                for k in item.keys():
                    k = str(k)
                    if k not in envelope_set and k not in result:
                        result[k] = blank_value_for(item, k)
    return changes


def validate_finding(finding, index):
    """校验单条 finding，返回问题列表（字符串）。index 需提供 para_texts。"""
    problems = []
    if not isinstance(finding, dict):
        return ["finding is not a dict"]
    required = ["finding_id", "facet", "severity", "issue_type", "verdict",
                "claim", "location", "quote", "rationale",
                "revision_suggestion", "confidence"]
    for req in required:
        if req not in finding or finding[req] in (None, ""):
            problems.append(f"missing/empty {req}")
    if finding.get("facet") not in FACETS:
        problems.append(f"bad facet: {finding.get('facet')}")
    if finding.get("severity") not in SEVERITIES:
        problems.append(f"bad severity: {finding.get('severity')}")
    if finding.get("issue_type") not in ISSUE_TYPES:
        problems.append(f"bad issue_type: {finding.get('issue_type')}")
    if finding.get("verdict") not in VERDICTS:
        problems.append(f"bad verdict: {finding.get('verdict')}")
    if finding.get("confidence") not in CONFIDENCES:
        problems.append(f"bad confidence: {finding.get('confidence')}")
    # quote 必须逐字命中原文（归一化空白后子串匹配）
    quote = finding.get("quote") or ""
    if quote and not quote_hit(index, quote):
        problems.append("quote not found verbatim in material text")
    evidence = finding.get("evidence")
    if evidence is not None and not isinstance(evidence, list):
        problems.append("evidence must be a list")
    else:
        for ev in (evidence or []):
            if not isinstance(ev, dict) or "quote" not in ev or "source_id" not in ev:
                problems.append("evidence item missing source_id/quote")
                continue
            if not quote_hit(index, ev.get("quote") or ""):
                problems.append(f"evidence quote not found: {ev.get('quote')!r}")
    return problems


def quote_hit(index, quote):
    """判断 quote 是否逐字（空白归一化后）出现在任一材料段落中。"""
    q = normalize_ws(quote)
    if not q:
        return False
    for p in index.get("para_texts", []):
        if q in normalize_ws(p):
            return True
    return False


def utcnow_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 模板与报告渲染（供 main.py 调用）
# ---------------------------------------------------------------------------
def re_search_stat(low: str) -> bool:
    return bool(re.search(r"\bp\s*[<≤=]\s*0?\.\d+", low)
                or "confidence interval" in low
                or "±" in low or "standard deviation" in low
                or re.search(r"\bn\s*=\s*\d+", low))


def auto_facet_applicability(task_text: str, paras: list):
    """用关键词给六类 facet 做初始适用性猜测，模型随后在 plan.json 定稿。"""
    task_low = (task_text or "").lower()
    sample = normalize_ws(" ".join(p.get("text", "") for p in paras[:400]))
    facets = []
    for facet in FACETS:
        keywords = FACET_KEYWORDS.get(facet, [])
        applicable = "maybe"
        if any(k.lower() in task_low for k in keywords):
            applicable = "yes"
        else:
            low = sample.lower()
            if facet == "statistics_report" and re_search_stat(low):
                applicable = "yes"
            if facet == "citation_support" and \
                    (re.search(r"\[\d{1,3}\]", sample)
                     or re.search(r"\([A-Z][a-z]+[^)]{0,60}\d{4}\)", sample)):
                applicable = "yes"
        facets.append({
            "facet": facet,
            "applicable": applicable,
            "rationale": "auto-detected by keyword scan; refine after reading",
            "anchors": [],
            "done": False,
        })
    return facets


def make_draft_template(case_id):
    return {
        "case_id": case_id,
        "verdict_summary": {
            "overall_verdict": "undetermined",
            "summary": "",
            "counts": {"critical": 0, "major": 0, "minor": 0,
                       "suggestion": 0, "total": 0},
            "coverage": {f: "unchecked" for f in FACETS},
        },
        "findings": [],
        "unchecked_items": [
            {"facet": f, "reason": "尚未开始分面审查", "status": "unchecked"}
            for f in FACETS
        ],
    }


def make_partial_result(case_id, output_contract, contract_keys):
    result = {"case_id": case_id}
    if isinstance(output_contract, dict):
        for k in output_contract.keys():
            result[str(k)] = output_contract[k]
    elif isinstance(output_contract, list):
        result["output_contract"] = output_contract
        for k in contract_keys:
            result.setdefault(k, None)
    else:
        for k in contract_keys:
            result[k] = None
    result.update({
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "changes": [],
        "change_log": [],
        "runtime": {"started_at": utcnow_iso(), "finished_at": None,
                    "seconds": None, "finalized": False},
        "result": {
            "verdict_summary": {
                "overall_verdict": "undetermined", "summary": "",
                "counts": {"critical": 0, "major": 0, "minor": 0,
                           "suggestion": 0, "total": 0},
                "coverage": {f: "unchecked" for f in FACETS},
            },
            "findings": [],
            "unchecked_items": [],
            "evidence_map": [],
        },
    })
    return result


def render_report(case_id, vs, findings, unchecked, status, degradations):
    lines = ["# 论文质量审查报告", ""]
    lines.append(f"- case_id: {case_id}")
    lines.append(f"- status: {status}")
    lines.append(f"- overall_verdict: {vs['overall_verdict']}")
    lines.append(f"- counts: {json.dumps(vs['counts'], ensure_ascii=False)}")
    lines.append(f"- coverage: {json.dumps(vs['coverage'], ensure_ascii=False)}")
    if vs.get("summary"):
        lines += ["", "## 总评", "", vs["summary"]]
    if degradations:
        lines += ["", "## 降级与缺口", ""]
        lines += [f"- {d}" for d in degradations]
    lines += ["", "## 问题清单", ""]
    by_facet = {}
    for f in findings:
        if isinstance(f, dict):
            by_facet.setdefault(f.get("facet", "?"), []).append(f)
    for facet in FACETS:
        items = by_facet.get(facet, [])
        lines += [f"### {facet} ({len(items)})", ""]
        if not items:
            lines += ["（无 finding）", ""]
            continue
        for f in items:
            lines += [
                f"#### {f.get('finding_id')} "
                f"[{f.get('severity')}/{f.get('issue_type')}/"
                f"{f.get('verdict')}/{f.get('confidence')}]", "",
                f"- 论断: {f.get('claim')}",
                f"- 定位: `{f.get('location')}`",
                f"> 原文: {f.get('quote')}", "",
                f"- 依据: {f.get('rationale')}",
                f"- 建议: {f.get('revision_suggestion')}",
            ]
            if f.get("evidence"):
                lines.append("- 证据:")
                for ev in f["evidence"]:
                    lines.append(f"  - `{ev.get('source_id')} "
                                 f"{ev.get('locator')}`: {ev.get('quote')}")
            lines.append("")
    if unchecked:
        lines += ["## 未检查项", ""]
        for u in unchecked:
            if isinstance(u, dict):
                lines.append(f"- {u.get('facet')} ({u.get('status')}): "
                             f"{u.get('reason')}")
    lines += ["", "## 复核方式", "",
              "逐条 finding 的证据 quote 均已对 `/input` 材料做过空白归一化后的"
              "逐字子串命中校验，可回到原文定位复核；证据映射见 "
              "`work/evidence_map.json`。"]
    return "\n".join(lines) + "\n"
