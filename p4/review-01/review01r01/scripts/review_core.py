#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""paper-quality-review Skill 核心库（仅 Python 标准库）。

职责：文本读取/段落定位/PDF/DOCX 抽取、输出契约与字段校验、
逐字 quote 命中校验、evidence_map 派生。不包含 CLI，供 main.py 与
selftest.py 复用。所有函数不抛异常到进程级，解析问题以 problems 列表返回。
"""
from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

# ---------------------------------------------------------------------------
# 常量：六类审查面与判定枚举（与 references/review-checklist.md 一致）
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
OVERALL_VERDICTS = ["publishable", "minor_revision", "major_revision", "reject", "unable_to_judge"]
EVIDENCE_ROLES = [
    "supporting_citation",   # 被引文献的主张与本文论断的对照
    "data_point",            # 摘要/正文/图表中的数值或数据
    "method_detail",         # 方法、对照、样本、指标等细节
    "definition",            # 术语与定义前后一致性
    "internal_consistency",  # 同一稿件内部两处表述的对比
    "figure_claim",          # 图表声明及其来源
]
RELATIONS = ["supports", "contradicts", "unverifiable", "not_found"]
CONFLICT_KEYS = ["locator_a", "quote_a", "locator_b", "quote_b", "kind"]

MAX_TEXT_FILE = 2_000_000  # 单文件字节上限，超限跳过并记录

TEXT_KINDS = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl", ".bib", ".tex", ".rst", ".yaml", ".yml"}
PDF_KINDS = {".pdf"}
DOCX_KINDS = {".docx"}
IMAGE_KINDS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".bmp", ".svg"}

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


# ---------------------------------------------------------------------------
# 文本读取（容错：BOM / 多编码降级）
# ---------------------------------------------------------------------------
def read_text(path: Path):
    """返回 (text_or_None, problem_or_None)。"""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return None, f"read error: {exc}"
    if len(data) > MAX_TEXT_FILE:
        return None, f"file too large ({len(data)} bytes > {MAX_TEXT_FILE})"
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            return data.decode(enc), None
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace"), "undecodable; used replacement chars"


def norm(s):
    """逐字匹配用归一化：折叠全部空白为单个空格。"""
    return re.sub(r"\s+", " ", (s or "")).strip()


# ---------------------------------------------------------------------------
# 段落切分与 locator（p 页 # para 段）
# ---------------------------------------------------------------------------
def split_paragraphs(text, page=None):
    blocks = re.split(r"\n\s*\n|\r\n\s*\r\n", text)
    paras = []
    for i, block in enumerate(blocks, 1):
        t = block.strip()
        if not t:
            continue
        loc = f"p{page}#para{i}" if page else f"para{i}"
        paras.append({"locator": loc, "page": page, "text": t})
    return paras


def locator_index(paragraphs):
    """locator -> 归一化文本。"""
    return {p["locator"]: norm(p["text"]) for p in paragraphs}


# ---------------------------------------------------------------------------
# PDF / DOCX 抽取（PDF 无标准库解析器：调用 pdftotext，失败则如实降级）
# ---------------------------------------------------------------------------
def extract_pdf_text(data: bytes):
    """返回 (pages: list[str], problems: list[str])；每页以 \\f 分隔。"""
    exe = shutil.which("pdftotext")
    if not exe:
        return [], ["pdftotext not found; run platform pdf-text-tool and save /app/work/text/<sid>.txt, then re-run init"]
    try:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.pdf"
            src.write_bytes(data)
            proc = subprocess.run(
                [exe, "-layout", str(src), "-"],
                capture_output=True, timeout=120,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], [f"pdftotext error: {exc}"]
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace")[:300]
        return [], [f"pdftotext rc={proc.returncode}: {err}"]
    text = proc.stdout.decode("utf-8", "replace")
    pages = [pg for pg in text.split("\f")]
    return pages, []


def extract_docx_paras(data: bytes):
    """返回 (paragraph_texts: list[str], problem_or_None)。"""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml")
    except Exception as exc:
        return [], f"docx unzip/read error: {exc}"
    try:
        root = ET.fromstring(xml)
    except Exception as exc:
        return [], f"docx XML parse error: {exc}"
    lines = []
    for p in root.iter(f"{{{W_NS}}}p"):
        texts = [t.text or "" for t in p.iter(f"{{{W_NS}}}t")]
        line = "".join(texts)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return lines, None


# ---------------------------------------------------------------------------
# 契约与 finding 校验
# ---------------------------------------------------------------------------
def contract_keys_of(contract):
    """从 output_contract（dict 或 list）推导必须回填的顶层键。"""
    keys = []
    if isinstance(contract, dict):
        keys = [str(k) for k in contract.keys()]
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, dict):
                k = item.get("name") or item.get("key") or item.get("field")
                if k:
                    keys.append(str(k))
            elif isinstance(item, str):
                keys.append(item)
    return keys


def merge_contract(result: dict, contract):
    """把 output_contract 的全部字段并入 result；未知键写 null/[]，不改名不删除。"""
    if isinstance(contract, dict):
        result.setdefault("output_contract", contract)
        for k, spec in contract.items():
            if k not in result:
                if isinstance(spec, list) or (isinstance(spec, dict) and spec.get("type") == "list"):
                    result[k] = []
                else:
                    result[k] = None
    elif isinstance(contract, list):
        result.setdefault("output_contract", contract)
        for item in contract:
            k = None
            if isinstance(item, dict):
                k = item.get("name") or item.get("key") or item.get("field")
            elif isinstance(item, str):
                k = item
            if k and k not in result:
                result[k] = None
    return result


def validate_finding(f, problems, idx=None):
    """校验单条 finding；idx 为问题序号（从 1 起），problems 就地追加。"""
    tag = f"finding[{f.get('finding_id', '?')}]"
    for key in ("finding_id", "facet", "severity", "issue_type", "verdict",
                "claim", "location", "quote", "rationale",
                "revision_suggestion", "confidence"):
        if key not in f or f[key] in (None, ""):
            problems.append(f"{tag}: missing/empty field '{key}'")
    if f.get("facet") not in FACETS:
        problems.append(f"{tag}: facet not in {FACETS}")
    if f.get("severity") not in SEVERITIES:
        problems.append(f"{tag}: severity not in {SEVERITIES}")
    if f.get("issue_type") not in ISSUE_TYPES:
        problems.append(f"{tag}: issue_type not in {ISSUE_TYPES}")
    if f.get("verdict") not in VERDICTS:
        problems.append(f"{tag}: verdict not in {VERDICTS}")
    if f.get("confidence") not in CONFIDENCES:
        problems.append(f"{tag}: confidence not in {CONFIDENCES}")
    ev = f.get("evidence", [])
    if not isinstance(ev, list):
        problems.append(f"{tag}: evidence must be a list")
    else:
        for e in ev:
            if not isinstance(e, dict):
                problems.append(f"{tag}: evidence item not object")
                continue
            for key in ("evid_id", "role", "source_id", "locator", "quote", "relation"):
                if not e.get(key):
                    problems.append(f"{tag}: evidence item missing '{key}'")
            if e.get("role") not in EVIDENCE_ROLES:
                problems.append(f"{tag}: evidence role '{e.get('role')}' not in {EVIDENCE_ROLES}")
            if e.get("relation") not in RELATIONS:
                problems.append(f"{tag}: evidence relation '{e.get('relation')}' not in {RELATIONS}")
    for c in f.get("conflicts", []) or []:
        for key in CONFLICT_KEYS:
            if not c.get(key):
                problems.append(f"{tag}: conflict item missing '{key}'")
    return tag


def check_quote_hits(f, all_paras):
    """逐字命中校验。返回 {"ok":bool,"detail":str,"hit_locators":[...]}。

    命中规则：claim.quote 与 evidence[].quote 的归一化文本必须作为子串
    出现在该 source_id 的某个段落的归一化文本中；location 可解析时须落在
    该段落，否则作为 warning 记录。
    """
    by_sid = {}
    for sid, paras in (all_paras or {}).items():
        by_sid[sid] = {p["locator"]: norm(p["text"]) for p in paras}
    hits = []
    detail = []
    bad = False
    # 主论断 quote：必须逐字命中，且落在声明的 location
    q = f.get("quote")
    loc = f.get("location") or ""
    for sid, idx in by_sid.items():
        for ploc, ptext in idx.items():
            if q and norm(q) in ptext:
                hits.append(ploc)
    if not q:
        detail.append("claim quote empty")
        bad = True
    elif not hits:
        detail.append("claim quote NOT FOUND verbatim")
        bad = True
    elif loc and loc not in hits:
        detail.append(f"claim quote hit at {hits} but not at stated location '{loc}'")
        bad = True
    # 证据 quote：必须逐字命中，且落在声明的 source_id + locator
    for e in f.get("evidence", []) or []:
        eq = e.get("quote")
        esid = e.get("source_id")
        eloc = e.get("locator") or ""
        idx = by_sid.get(esid, {})
        ehit = [ploc for ploc, pt in idx.items() if eq and norm(eq) in pt]
        if not eq:
            detail.append(f"{e.get('evid_id','?')}: evidence quote empty")
            bad = True
        elif not ehit:
            detail.append(f"{e.get('evid_id','?')}: evidence quote NOT FOUND in {esid}")
            bad = True
        elif eloc and eloc not in ehit:
            detail.append(f"{e.get('evid_id','?')}: hit at {ehit} but not at stated locator '{eloc}'")
            bad = True
    return {"ok": not bad, "detail": "; ".join(detail) or "all quotes hit verbatim",
            "hit_locators": hits}


def derive_evidence_map(findings):
    """从 findings 自动派生顶层 evidence_map，保证与 finding.evidence 一致。"""
    em = {}
    for f in findings:
        fid = f.get("finding_id") or "F?"
        items = []
        relcount = {r: 0 for r in RELATIONS}
        for e in f.get("evidence", []) or []:
            rel = e.get("relation")
            if rel in relcount:
                relcount[rel] += 1
            items.append({
                "evid_id": e.get("evid_id"),
                "role": e.get("role"),
                "source_id": e.get("source_id"),
                "locator": e.get("locator"),
                "quote": e.get("quote"),
                "relation": rel,
            })
        em[fid] = {
            "facet": f.get("facet"),
            "claim": {"locator": f.get("location"), "quote": f.get("quote")},
            "evidence_count": len(items),
            "relations": relcount,
            "items": items,
            "conflicts": f.get("conflicts", []),
        }
    return em


def derive_verdict_summary(findings, unchecked):
    from collections import Counter
    summary = {
        "totals": {"findings": len(findings), "unchecked": len(unchecked)},
        "by_facet": {facet: 0 for facet in FACETS},
        "by_severity": {s: 0 for s in SEVERITIES},
        "by_issue_type": {t: 0 for t in ISSUE_TYPES},
        "by_verdict": {v: 0 for v in VERDICTS},
        "coverage": {
            "checked_facets": sorted({f.get("facet") for f in findings if f.get("facet") in FACETS}),
            "unchecked_facets": sorted({u.get("facet") for u in unchecked if u.get("facet") in FACETS}),
        },
    }
    for f in findings:
        if f.get("facet") in summary["by_facet"]:
            summary["by_facet"][f["facet"]] += 1
        if f.get("severity") in summary["by_severity"]:
            summary["by_severity"][f["severity"]] += 1
        if f.get("issue_type") in summary["by_issue_type"]:
            summary["by_issue_type"][f["issue_type"]] += 1
        if f.get("verdict") in summary["by_verdict"]:
            summary["by_verdict"][f["verdict"]] += 1
    return summary
