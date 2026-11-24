#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""证据抽取与校验核心库（仅 Python 标准库）。

职责：
1. 鲁棒读取文本：utf-8-sig / utf-8 / gb18030 / latin-1 逐级降级，单文件 2MB 上限。
2. 段落/句子切分与 locator 索引（S{section}.P{para}.s{sentence}）。
3. 引文规范化与逐字回查（quote 必须是源文本的逐字片段）。
4. 论断/证据条目的数据模板与判定口径。

本库只做确定性计算，不产生科学结论；所有判定口径在 references/evidence_rubric.md。
"""

import os
import re
import unicodedata

MAX_FILE_BYTES = 2 * 1024 * 1024

DOC_KIND_BY_EXT = {
    ".pdf": "pdf",
    ".txt": "text",
    ".md": "markdown",
    ".markdown": "markdown",
    ".csv": "csv",
    ".tsv": "csv",
    ".json": "json",
    ".jsonl": "jsonl",
    ".xml": "xml",
    ".yaml": "yml",
    ".yml": "yml",
    ".tex": "latex",
}

CLAIM_KINDS = ("fact", "inference", "method", "comparison", "consensus", "causal", "recommendation")
SUPPORT_RELATIONS = ("direct", "indirect", "insufficient", "contradicts", "no_support")
EVIDENCE_TYPES = ("primary", "review", "guideline", "preprint", "dataset", "figure", "table", "other")
STRENGTHS = ("high", "moderate", "low", "insufficient")
VERDICTS = ("accept", "hedge", "reject", "pending_human")

# 与 SKILL.md / evidence_rubric.md 保持一致的 locator 正则
LOCATOR_RE = re.compile(r"^S\d+\.P\d+(\.s\d+)?$")


def now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_ws(s):
    """NFKC 归一化 + 折叠所有空白 + 折叠常见全半角差异，用于逐字比对容差。"""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", " ", s).strip()
    trans = str.maketrans({
        "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
        "\u2013": "-", "\u2014": "-", "\u2026": "...",
        "\u3001": ",", "\uff0c": ",", "\uff1a": ":", "\uff1b": ";",
    })
    return s.translate(trans)


def read_text_robust(path, max_bytes=MAX_FILE_BYTES):
    """读取文本文件，返回 (text, encoding)；失败返回 (None, reason)。"""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, "stat_error:%s" % e
    if size > max_bytes:
        return None, "too_large:%dB>%dB" % (size, max_bytes)

    raw = None
    used_enc = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                raw = fh.read()
            used_enc = enc
            break
        except UnicodeDecodeError:
            continue
        except OSError as e:
            return None, "read_error:%s" % e
    if raw is None:
        return None, "decode_failed"
    return raw, used_enc


def split_paragraphs(text):
    """按空行切分段落；段落内部换行折叠为空格（兼容 pdftotext 折行）。"""
    if not text:
        return []
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n[ \t]*\n+", text)
    out = []
    for b in blocks:
        b = re.sub(r"\n+", " ", b).strip()
        if b:
            out.append(b)
    return out


def split_sentences(para):
    """启发式按中文/英文句末标点切分；不含英文句点，避免小数点误切（记 limitations）。"""
    if not para:
        return []
    parts = re.split(r"(?<=[。！？!?；;])", para)
    return [p.strip() for p in parts if p.strip()]


_HEADING_MD = re.compile(r"^#{1,6}\s+\S")
_HEADING_CN = re.compile(r"^(第[一二三四五六七八九十百\d]+[章节部分][\s:：]|[一二三四五六七八九十]+、)\s*\S")
_HEADING_CAPS = re.compile(r"^[A-Z][A-Z0-9 ,&/\-]{2,59}$")


def _looks_like_heading(block):
    p = (block or "").strip()
    if not p or len(p) > 80:
        return False
    if _HEADING_MD.match(p):
        return True
    if _HEADING_CN.match(p):
        return True
    if _HEADING_CAPS.match(p) and not p.endswith((".", "?", "!", ":", ")")):
        return True
    return False


def build_locator_index(source_doc, text):
    """从文本建立 locator 索引。

    返回 {source_doc, paragraph_count, sections, locators}。
    locators: {"S1.P2.s1": "句子原文", ...}
    """
    paras = split_paragraphs(text)
    sections = []
    locators = {}
    cur = None
    sec_no = 0
    for p in paras:
        if _looks_like_heading(p):
            if cur is not None and (cur["paragraphs"] or cur["heading"]):
                sections.append(cur)
            sec_no += 1
            cur = {"id": "S%d" % sec_no, "heading": p, "paragraphs": []}
            continue
        if cur is None:
            sec_no += 1
            cur = {"id": "S%d" % sec_no, "heading": "", "paragraphs": []}
        pid = "P%d" % (len(cur["paragraphs"]) + 1)
        sents = split_sentences(p)
        cur["paragraphs"].append({"id": pid, "text": p, "sentences": sents})
        for si, stxt in enumerate(sents, 1):
            locators["%s.%s.s%d" % (cur["id"], pid, si)] = stxt
    if cur is not None and (cur["paragraphs"] or cur["heading"]):
        sections.append(cur)
    if not sections:
        sections = [{"id": "S1", "heading": "", "paragraphs": []}]
    return {
        "source_doc": source_doc,
        "paragraph_count": len(paras),
        "sections": sections,
        "locators": locators,
    }


def quote_in_text(text, quote, normalize=True):
    """逐字回查：quote 是否在 text 中（默认归一化空白与全半角）。"""
    if not text or not quote:
        return False
    if normalize:
        text = normalize_ws(text)
        quote = normalize_ws(quote)
    return quote in text


def source_key_candidates(src):
    """把 source_doc 扩展成多个可回查键，便于跨原文/抽取文本匹配。"""
    src = (src or "").strip().replace("\\", "/")
    keys = []
    if src:
        keys.append(src)
    base = os.path.basename(src)
    if base:
        keys.append(base)
    stem, _ext = os.path.splitext(base)
    if stem:
        keys.append(stem)
        keys.append(stem + ".pdf")
        keys.append(stem + ".txt")
        keys.append(stem + ".md")
    return list(dict.fromkeys(k for k in keys if k))


def new_claim_entry(claim_id="C1"):
    """论断条目模板：一条可独立判定的实质论断。"""
    return {
        "claim_id": claim_id,
        "claim": "",             # 成稿前的论断原文（逐字）
        "claim_kind": "",        # fact|inference|method|comparison|consensus|causal|recommendation
        "claim_scope": "",       # 适用人群/条件/时间范围，避免过度泛化
        "final_wording": "",     # 写入成稿时的最终表述（含限定语）
        "support": [],           # 一条论断可有多条来源
        "uncertainty": "",       # 结论边界/机制未明/冲突处如何限定
        "citation_refs": [],     # 成稿引用编号，如 ["[1]"]
        "status": "draft",       # draft|verified|pending_human|rejected
    }


def new_support_entry():
    """单条证据条目模板：claim → 具体来源的逐字支撑。"""
    return {
        "source_doc": "",        # 材料相对路径或文件名
        "locator": "",           # S{section}.P{para}.s{sentence}
        "quote": "",             # 源文本逐字片段（硬要求）
        "relation": "",          # direct|indirect|insufficient|contradicts|no_support
        "evidence_type": "",     # primary|review|guideline|preprint|dataset|figure|table|other
        "strength": "",          # high|moderate|low|insufficient
        "strength_rationale": "",  # 研究设计/样本量/重复验证/共识程度
        "verdict": "",           # accept|hedge|reject|pending_human
        "note": "",
    }


def validate_claim(entry, doc_text_by_source, known_locators=None):
    """校验一条论断，返回问题列表；doc_text_by_source 为 {key: 源文本}。

    硬性检查：claim 非空、至少一条来源、每条来源有 source_doc/quote，
    且 quote 必须与源文本逐字一致（归一化后命中）。
    known_locators：可选 set，若提供则校验 locator 确在 locator_index.json 中登记。
    """
    issues = []
    cid = entry.get("claim_id") or "?"
    if not (entry.get("claim") or "").strip():
        issues.append("%s: 缺 claim 文本" % cid)
    support = entry.get("support") or []
    if not support:
        issues.append("%s: 无 support，实质论断至少需要一条来源" % cid)
    for i, s in enumerate(support, 1):
        tag = "%s.s%d" % (cid, i)
        src = (s.get("source_doc") or "").strip()
        quote = (s.get("quote") or "").strip()
        loc = (s.get("locator") or "").strip()
        verdict = (s.get("verdict") or "").strip()
        if not src:
            issues.append("%s: 缺 source_doc" % tag)
        if not quote:
            issues.append("%s: 缺逐字 quote（证据溯源硬要求）" % tag)
        if loc and not LOCATOR_RE.match(loc):
            issues.append("%s: locator 格式非法 '%s'（应为 S1.P2.s3）" % (tag, loc))
        if loc and LOCATOR_RE.match(loc) and known_locators is not None and loc not in known_locators:
            issues.append("%s: locator '%s' 未在 locator_index.json 中登记，无法回查定位" % (tag, loc))
        if (loc and LOCATOR_RE.match(loc) and isinstance(known_locators, dict)
                and loc in known_locators and quote):
            ptext = known_locators[loc]
            if ptext and not quote_in_text(ptext, quote):
                issues.append("%s: locator '%s' 指向段落不含该 quote（定位与引文不一致）" % (tag, loc))
        if verdict and verdict not in VERDICTS:
            issues.append("%s: 非法 verdict '%s'" % (tag, verdict))
        if quote:
            matched = False
            for key in source_key_candidates(src):
                txt = doc_text_by_source.get(key)
                if txt and quote_in_text(txt, quote):
                    matched = True
                    break
            if not matched:
                issues.append("%s: quote 与源文本逐字不一致或来源不可回查（source_doc='%s'）" % (tag, src))
    return issues


def locator_paragraph_map(index):
    """从 locator_index 的文档列表构建 {locator: paragraph_text}。

    用于 validate 时校验 quote 确实位于所声称的 locator 指向段落。
    """
    m = {}
    for d in (index or {}).get("documents") or []:
        for sec in d.get("sections") or []:
            sid = sec.get("id")
            for para in sec.get("paragraphs") or []:
                pid = para.get("id")
                ptext = para.get("text") or ""
                sents = para.get("sentences") or []
                if not sents:
                    m["%s.%s" % (sid, pid)] = ptext
                for si in range(1, len(sents) + 1):
                    m["%s.%s.s%d" % (sid, pid, si)] = ptext
    return m


def extract_candidates(text, source_doc):
    """扫描文本中的可引用候选：含数字/统计/DOI/引文标记的句子。

    只做提示性抽取，帮助 Agent 定位值得核查的论断；不替代人工判定。
    """
    rows = []
    if not text:
        return rows
    for para_idx, para in enumerate(split_paragraphs(text), 1):
        for sent in split_sentences(para):
            has_num = re.search(r"\d+(\.\d+)?%?|p\s*[<>=]\s*0?\.\d+|n\s*=\s*\d+", sent)
            has_ref = re.search(r"\[\d+(?:[,\-]\d+)*\]|\(\d{4}\)|\([A-Z][A-Za-z]+ et al\., \d{4}\)|doi:?\s*10\.", sent, re.I)
            has_digits = re.search(r"\d", sent)
            if (has_num or has_ref) and has_digits:
                rows.append({
                    "source_doc": source_doc,
                    "locator": "S1.P%d" % para_idx,
                    "candidate_text": sent.strip(),
                    "why": [k for k, v in (("number", has_num), ("reference", has_ref)) if v],
                })
    return rows


if __name__ == "__main__":
    # 最小自测：仅验证库函数本身可运行，不依赖输入
    import json as _json
    sample = "# 摘要\n\n本研究纳入 120 例患者。总有效率为 85%。\n\n## 讨论\n\n结果与文献一致。"
    idx = build_locator_index("demo.txt", sample)
    print(_json.dumps({"paragraph_count": idx["paragraph_count"],
                       "locator_count": len(idx["locators"])}, ensure_ascii=False))
