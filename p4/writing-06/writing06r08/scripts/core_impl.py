#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""结构诊断辅助引擎：容错读取、材料盘点、章节切分、论断/证据锚点抽取、
冗余检测、结论边界与闭环检查、result.json 骨架构建。

仅依赖 Python 标准库；所有输出确定（固定遍历顺序、无随机）。
本模块只产出"候选锚点 + 定位 + 逐字引用"，供模型核对，不生成最终结论。
"""
import json
import os
import re
import unicodedata
from collections import Counter
from pathlib import Path

MAX_FILE = 2 * 1024 * 1024  # 单文件 2MB 上限，超限跳过并记录

TEXT_EXTS = {".txt", ".md", ".markdown", ".tex", ".json", ".csv", ".tsv",
             ".bib", ".html", ".xml", ".rst", ".muse"}
PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff",
              ".bmp", ".svg"}
OFFICE_EXTS = {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".odt"}
KNOWN_EXTS = TEXT_EXTS | PDF_EXTS | IMAGE_EXTS | OFFICE_EXTS

# ---------------------------------------------------------------------------
# 容错 IO
# ---------------------------------------------------------------------------

def safe_read(path):
    """按 utf-8-sig / utf-8 / gb18030 / latin-1 顺序读取，坏编码降级。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError:
            return None
    return None


def parse_json_tolerant(text):
    """解析 JSON，BOM/坏 JSON 降级：尝试截取最后一个完整对象。"""
    if text is None:
        return None, "empty"
    t = text.lstrip("\ufeff").strip()
    if not t:
        return None, "empty"
    try:
        return json.loads(t), "ok"
    except json.JSONDecodeError as exc:
        # 尝试从末尾截断到最后一个 '}' 或 ']'
        for end in range(len(t), 0, -1):
            if t[end - 1] in ("}", "]"):
                try:
                    return json.loads(t[:end]), "truncated_fixed"
                except json.JSONDecodeError:
                    continue
        return None, "bad_json: %s" % exc


def discover_materials(input_dir):
    """扫描输入目录，分类文件；返回 (files, degradations)。确定性顺序。"""
    files = []
    degradations = []
    root = Path(input_dir)
    if not root.exists():
        return files, ["input_dir_not_found: %s" % input_dir]
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            degradations.append("stat_failed: %s" % path)
            continue
        ext = path.suffix.lower()
        kind = ("text" if ext in TEXT_EXTS else
                "pdf" if ext in PDF_EXTS else
                "image" if ext in IMAGE_EXTS else
                "office" if ext in OFFICE_EXTS else
                "other")
        if size > MAX_FILE:
            degradations.append("skipped_over_2MB: %s (%d bytes)" % (path.name, size))
            continue
        entry = {"path": str(path), "name": path.name, "ext": ext,
                 "kind": kind, "size": size}
        if kind == "text":
            entry["text"] = safe_read(path)
            if entry["text"] is None:
                degradations.append("unreadable_text: %s" % path.name)
        files.append(entry)
    return files, degradations

# ---------------------------------------------------------------------------
# 章节 / 段落 / 句子切分
# ---------------------------------------------------------------------------

HEADING_RE = re.compile(
    r"^\s{0,3}(?:"
    r"#{1,6}\s+\S.*"
    r"|\d{1,3}(?:\.\d{1,3}){0,4}\s+\S.*"
    r"|(?:Abstract|Introduction|Background|Related\s+Work|Methods?|"
    r"Materials?|Results?|Experiments?|Evaluation|Discussion|Conclusion|"
    r"Limitations?|Future\s+Work|References|Acknowledg\w*|Appendix|"
    r"摘要|引言|背景|相关工作|方法|材料|结果|实验|评估|讨论|结论|局限|展望|参考文献|致谢|附录)"
    r")\b.*$",
    re.IGNORECASE)

_SENT_SPLIT = re.compile(r"[^。！？!?；;]*[。！？!?；;]|[^。！？!?；;]+$")


def split_sections(text):
    """按标题切章节，返回 [{heading, body}]。无标题时整体归入 '(preamble)'。"""
    sections = []
    cur = {"heading": "(preamble)", "lines": []}
    for line in text.splitlines():
        if HEADING_RE.match(line):
            if cur["lines"]:
                sections.append(cur)
            cur = {"heading": line.strip(), "lines": []}
        else:
            cur["lines"].append(line)
    if cur["lines"]:
        sections.append(cur)
    for s in sections:
        s["body"] = "\n".join(s["lines"])
    return sections


def split_paragraphs(body):
    """按空行切段落，返回非空段落文本列表。"""
    parts = re.split(r"\n\s*\n", body)
    return [p.strip() for p in parts if p.strip()]


def split_sentences(paragraph):
    """按中英文句末标点切句，保留非空句子。"""
    return [s.strip() for s in _SENT_SPLIT.findall(paragraph) if s.strip()]

# ---------------------------------------------------------------------------
# 论断与证据锚点抽取（候选，供模型核对）
# ---------------------------------------------------------------------------

CLAIM_RE = re.compile(
    r"\b(conclud\w*|suggest\w*|indicat\w*|demonstrat\w*|show\w*|"
    r"support\w*|confirm\w*|reveal\w*|associat\w*|correlat\w*|caus\w*|"
    r"result\w* in|led to|lead\w* to|propos\w*|hypothes\w*|therefore|"
    r"thus|hence|imply|implying|evidence|consistent)\b",
    re.IGNORECASE)

CLAIM_RE_ZH = re.compile(r"表明|显示|证实|提示|推断|推测|说明|导致|促进|相关|"
                         r"因果|证明|提出|认为|假设|揭示|支持|提示|意味着|因此|"
                         r"可见|结论|综上")

NUM_RE = re.compile(
    r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:%|‰)?"
    r"\s*(?:[µμnmkMGTP]?(?:m|mol|g|L|l|s|Hz|V|A|W|J|Pa|K|°C|℃|yr|year|day|h|min|"
    r"cells|fold|×)?)")
CITE_BRACKET_RE = re.compile(r"\[\d+(?:\s*[-,–]\s*\d+)*\]|\[\d+(?:\s*,\s*\d+)*\]")
CITE_AUTHYEAR_RE = re.compile(r"\([A-Z][\w'’& -]{1,40},?\s*(?:\d{4}[a-z]?)\)")
FIGTAB_RE = re.compile(r"(?:Fig(?:ure)?\.?\s*\d+|Table\s*\d+|图\s*\d+|表\s*\d+|"
                       r"Eq(?:uation)?\.?\s*\d+|公式\s*\d+)", re.IGNORECASE)
STAT_RE = re.compile(r"(?:p\s*[<>=]?\s*0?\.\d+|95%\s*C[I]|"
                     r"\b(?:OR|HR|RR|CI|SD|SEM|n)\s*[=：:]\s*-?\d+\.?\d*)",
                     re.IGNORECASE)


def _locator(sec_idx, sec, para_idx, sent_idx):
    sec_no = "" if sec_idx == 0 else str(sec_idx)
    head = sec["heading"].strip()
    head_short = head if len(head) <= 40 else head[:37] + "..."
    return "Sec%s:%s ¶%d s%d" % (sec_no, head_short, para_idx + 1, sent_idx + 1)


def extract_anchors(text):
    """返回 {sections, sentences, claims, evidence}，全部带 locator 与逐字 quote。"""
    sections_raw = split_sections(text)
    sections = []
    sentences = []
    claims = []
    evidence = []
    for si, sec in enumerate(sections_raw):
        sec_entry = {"index": si, "heading": sec["heading"],
                     "locator": "Sec%s:%s" % (("" if si == 0 else str(si)),
                                              sec["heading"][:40]),
                     "char_len": len(sec["body"])}
        sections.append(sec_entry)
        for pi, para in enumerate(split_paragraphs(sec["body"])):
            for qi, sent in enumerate(split_sentences(para)):
                loc = _locator(si, sec, pi, qi)
                sentences.append({"locator": loc, "text": sent})
                if CLAIM_RE.search(sent) or CLAIM_RE_ZH.search(sent):
                    claims.append({"locator": loc, "quote": sent,
                                   "marker": _first_marker(sent)})
                for m in NUM_RE.finditer(sent):
                    evidence.append({"type": "number", "value": m.group(0),
                                     "locator": loc, "quote": sent})
                for m in CITE_BRACKET_RE.finditer(sent):
                    evidence.append({"type": "citation", "value": m.group(0),
                                     "locator": loc, "quote": sent})
                for m in CITE_AUTHYEAR_RE.finditer(sent):
                    evidence.append({"type": "citation", "value": m.group(0),
                                     "locator": loc, "quote": sent})
                for m in FIGTAB_RE.finditer(sent):
                    evidence.append({"type": "figure_table", "value": m.group(0),
                                     "locator": loc, "quote": sent})
                for m in STAT_RE.finditer(sent):
                    evidence.append({"type": "statistic", "value": m.group(0),
                                     "locator": loc, "quote": sent})
    return {"sections": sections, "sentences": sentences,
            "claims": claims, "evidence": evidence}


def _first_marker(sent):
    for rx in (CLAIM_RE, CLAIM_RE_ZH):
        m = rx.search(sent)
        if m:
            return m.group(0)
    return None

# ---------------------------------------------------------------------------
# 冗余检测（跨段/跨章节近重复，仅给候选对）
# ---------------------------------------------------------------------------

def _normalize(s):
    s = unicodedata.normalize("NFKC", s.lower())
    s = re.sub(r"\d+(?:\.\d+)?%?", "#", s)
    s = re.sub(r"[^\w\u4e00-\u9fff#]", "", s)
    return s


def _shingles(s, k=4):
    s = re.sub(r"\s+", "", s)
    if len(s) < k:
        return {s} if s else set()
    return {s[i:i + k] for i in range(len(s) - k + 1)}


def detect_redundancy(sentences, threshold=0.62, max_pairs=2000):
    """返回近似重复句对（Jaccard>threshold），按相似度降序，控制候选数。"""
    pairs = []
    # 限制句数以控制 O(n^2)
    if len(sentences) > 600:
        sentences = sentences[:600]
    norm = [_normalize(s["text"]) for s in sentences]
    for i in range(len(sentences)):
        si = _shingles(norm[i])
        if not si:
            continue
        for j in range(i + 1, len(sentences)):
            sj = _shingles(norm[j])
            if not sj:
                continue
            jac = len(si & sj) / len(si | sj)
            if jac >= threshold:
                pairs.append({"a_locator": sentences[i]["locator"],
                              "a_quote": sentences[i]["text"],
                              "b_locator": sentences[j]["locator"],
                              "b_quote": sentences[j]["text"],
                              "similarity": round(jac, 3)})
    pairs.sort(key=lambda p: p["similarity"], reverse=True)
    return pairs[:max_pairs]

# ---------------------------------------------------------------------------
# 结论边界检查（因果/过度泛化/缺失局限，仅给候选）
# ---------------------------------------------------------------------------

CAUSAL_EN = re.compile(
    r"\b(caus\w*|due\s+to|owing\s+to|result\w*\s+in|lead\w*\s+to|driv\w*|"
    r"induc\w*|respons\w*\s+for|attribut\w*\s+to)\b", re.IGNORECASE)
CAUSAL_ZH = re.compile(r"因为|由于|导致|促使|促进|驱动|诱导|归因|因果|"
                       r"从而|进而|使得|致使|引起|诱发")
OVERGEN_EN = re.compile(
    r"\b(all|every|always|never|none|universally|invariably|definitively|"
    r"certainly|first|novel|unique|breakthrough)\b", re.IGNORECASE)
OVERGEN_ZH = re.compile(r"首次|首创|所有|总是|从未|无一|普遍|确定无疑|颠覆|"
                        r"革命性|彻底|完全|绝对|必然|无疑|决定性")


def check_conclusion_boundary(conclusion_text):
    """对结论段做边界候选检查，返回因果句、泛化句、缺失局限提示。"""
    found = {"causal": [], "overgeneral": [], "missing_limitation_hint": None}
    paras = split_paragraphs(conclusion_text)
    for pi, para in enumerate(paras):
        for qi, sent in enumerate(split_sentences(para)):
            loc = "Conc ¶%d s%d" % (pi + 1, qi + 1)
            if CAUSAL_EN.search(sent) or CAUSAL_ZH.search(sent):
                found["causal"].append({"locator": loc, "quote": sent})
            if OVERGEN_EN.search(sent) or OVERGEN_ZH.search(sent):
                found["overgeneral"].append({"locator": loc, "quote": sent})
    if paras and not re.search(r"(limit\w*|局限|caveat|进一步|有待|尚需|目前|在本研究中)",
                               conclusion_text, re.IGNORECASE):
        found["missing_limitation_hint"] = "结论段未检出局限/范围限定词，需人工核对是否缺失限制说明"
    return found


def join_sections_bodies(sections, main, keywords):
    """把标题含指定关键词的章节正文按序拼接（用于引言/结论聚合）。"""
    bodies = []
    for s in sections:
        h = s["heading"].lower()
        if any(k in h for k in keywords):
            bodies.append(_section_body(main, s))
    return "\n\n".join(b for b in bodies if b.strip())


def _section_body(main, sec):
    """按 heading 在原文中定位并返回该章节正文（近似：标题行到下一标题）。"""
    lines = main.splitlines()
    out, capture = [], False
    for line in lines:
        if line.strip() == sec["heading"]:
            capture = True
            continue
        if capture and HEADING_RE.match(line):
            break
        if capture:
            out.append(line)
    return "\n".join(out)


def extract_questions(intro_text):
    """抽取引言/摘要中的研究问题句（含问号或问题标记）。"""
    out = []
    for pi, para in enumerate(split_paragraphs(intro_text)):
        for qi, sent in enumerate(split_sentences(para)):
            if "?" in sent or "？" in sent or re.search(
                    r"\b(question|whether|aim|objective|goal|本研究|问题|目的|目标)\b",
                    sent, re.IGNORECASE):
                out.append({"locator": "Intro ¶%d s%d" % (pi + 1, qi + 1),
                            "quote": sent})
    return out

# ---------------------------------------------------------------------------
# result.json 骨架
# ---------------------------------------------------------------------------

DEFAULT_CONTRACT_FIELDS = [
    "diagnosis", "edit_plan", "claim_evidence_map", "source_map", "version_boundary",
]


def load_case(case_json_path):
    """读取 case.json（容错），返回 (case, err)。"""
    text = safe_read(case_json_path)
    data, err = parse_json_tolerant(text)
    return data, err


def build_skeleton(case, contract_fields=None, now=None):
    """构建 result.json 骨架；所有 output_contract 字段回填空值。"""
    if not isinstance(case, dict):
        case = {}
    case_id = case.get("case_id") or case.get("id") or "unknown"
    fields = contract_fields or DEFAULT_CONTRACT_FIELDS
    result = {"case_id": case_id, "status": "in_progress"}
    for f in fields:
        result[f] = None
    result["sources"] = []
    result["uncertainty"] = []
    result["pending_human"] = []
    result["errors"] = []
    result["degradations"] = []
    result["change_log"] = [{
        "time": now or _now_iso(), "action": "skeleton_written",
        "status": "in_progress"}]
    result["runtime"] = {"started_at": now or _now_iso()}
    return result


def _now_iso():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def six_dimension_status():
    """六类结构检查的默认状态模板。"""
    return {
        "research_question_alignment": {"status": "unchecked", "findings": []},
        "section_structure": {"status": "unchecked", "findings": []},
        "argument_chain": {"status": "unchecked", "findings": []},
        "paragraph_relations": {"status": "unchecked", "findings": []},
        "redundancy": {"status": "unchecked", "findings": []},
        "conclusion_boundary": {"status": "unchecked", "findings": []},
    }
