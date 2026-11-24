#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""paper-structure-review 的共享实现（仅 Python 标准库）。

本模块承担三件事，全部为确定性的机械步骤，不做主观判断，也绝不生成科学结论：
  1. ensure_skeleton  —— 读 case.json/task.md，立即落盘一版合法的 partial result.json；
  2. extract_evidence —— 扫描输入目录，抽取"候选锚点"（章节、段落、论断句、数字、
                        重复片段），供模型核对后引用，模型只核对、不凭空生成；
  3. verify_contract  —— 校验 result.json 的契约完整性与 locator 可回溯性。

设计原则：BOM/坏编码/坏 JSON/超大文件/空目录都不崩溃，全部降级并记录；同一输入
重复运行结果一致（无随机性、无时间依赖字段以外的差异）。
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone

MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文本文件 2MB 上限，超限跳过并记录
_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")

TEXT_EXT = {".md", ".txt", ".tex", ".json", ".csv", ".tsv", ".xml",
            ".html", ".htm", ".rst", ".yaml", ".yml", ".bib", ".doc", ".docx"}
PDF_EXT = {".pdf"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".bmp", ".webp", ".svg"}
TABLE_EXT = {".csv", ".tsv", ".xlsx", ".xls"}

# 六维结构诊断的稳定键名与中文标签（SKILL.md 与 result.json 共用）。
DIMENSIONS = (
    ("research_question", "研究问题与贡献主张对齐"),
    ("section_structure", "章节结构与叙事顺序"),
    ("argument_chain", "论证链与论据对应"),
    ("paragraph_relations", "段落关系"),
    ("redundancy", "重复内容"),
    ("conclusion_boundary", "结论边界"),
)

# 默认输出契约字段（case.json 未显式给出 output_contract 时使用）。
DEFAULT_CONTRACT_FIELDS = [
    "diagnosis", "edit_plan", "source_map", "sources",
    "uncertainty", "pending_human", "version_boundary",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_text(path: str):
    """容错读文本：返回 (text, note)。失败返回 (None, 原因)。"""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, "unreadable: %s" % exc
    if size > MAX_FILE_BYTES:
        return None, "oversized: %d bytes > %d" % (size, MAX_FILE_BYTES)
    last_err = None
    for enc in _ENCODINGS:
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), "encoding=%s" % enc
        except (UnicodeDecodeError, UnicodeError) as exc:
            last_err = exc
        except OSError as exc:
            return None, "unreadable: %s" % exc
    return None, "undecodable: %s" % (last_err or "unknown")


def read_json(path: str):
    """容错读 JSON：失败返回 (None, 原因)，不抛异常。"""
    text, note = read_text(path)
    if text is None:
        return None, note
    try:
        return json.loads(text), note
    except json.JSONDecodeError as exc:
        return None, "bad-json: %s" % exc


def write_json_atomic(path: str, obj) -> str:
    """原子写 JSON（先写临时文件再替换），UTF-8、ensure_ascii=False。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def classify_material(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext in PDF_EXT:
        return "pdf"
    if ext in IMAGE_EXT:
        return "image"
    if ext in TABLE_EXT:
        return "table"
    if ext in TEXT_EXT:
        return "text"
    return "other"


def scan_input(input_dir: str):
    """扫描输入目录，返回 (文件清单列表, 错误列表)。不假设固定文件名。"""
    files, errors = [], []
    if not os.path.isdir(input_dir):
        errors.append("input_dir_missing: %s" % input_dir)
        return files, errors
    try:
        names = sorted(os.listdir(input_dir))
    except OSError as exc:
        errors.append("listdir_failed: %s" % exc)
        return files, errors
    for name in names:
        path = os.path.join(input_dir, name)
        if os.path.isfile(path):
            kind = classify_material(name)
            files.append({"path": path, "name": name, "kind": kind,
                          "size": _safe_size(path)})
    if not files:
        errors.append("empty_input_dir")
    return files, errors


def _safe_size(path: str):
    try:
        return os.path.getsize(path)
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# 结构性候选锚点抽取（机械规则，供模型核对，不构成诊断结论）
# --------------------------------------------------------------------------- #

_SECTION_RE = re.compile(
    r"^\s*(?:"
    r"(?:\d+(?:\.\d+){0,3})[\.\s、．]?\s+"                      # 1. / 1.1 / 1.1.1
    r"|(?:第[一二三四五六七八九十百0-9]+[章节部分])"              # 第X章/节/部分
    r"|(?:abstract|introduction|related\s+work|background|methods?|results?|"
    r"discussion|conclusion|limitations?|reference|acknowledg\w*)\b"
    r")", re.IGNORECASE)

_CLAIM_VERB_RE = re.compile(
    r"(表明|证实|显示|提示|发现|证明|揭示|暗示|支持|意味着|说明|推测|推断|"
    r"改善|提高|降低|增加|减少|显著|"
    r"demonstrat\w*|indicat\w*|show(?:s|ed|ing)?|suggest\w*|reveal\w*|"
    r"confirm\w*|imply|support\w*|improv\w*|increas\w*|decreas\w*|reduc\w*|"
    r"enhanc\w*|worsen\w*|correlat\w*|associat\w*|caus\w*)", re.IGNORECASE)

_NUM_RE = re.compile(
    r"(?:"
    r"\d+(?:\.\d+)?\s*(?:%|％|kg|g|mg|µg|μg|s|ms|min|h|mol|mmol|μmol|M|mm|cm|"
    r"nm|μm|℃|°C|K|例|个|倍|±|×|CI\b|OR\b|HR\b|RR\b|SD\b|SEM\b)"
    r"|\b(?:n|p|CI|OR|HR|RR|r|R)\s*[=<>≤≥]\s*\d+(?:\.\d+)?"
    r"|\b\d+(?:\.\d+)?[eE][+-]?\d+"
    r")", re.IGNORECASE)

# 相关性→因果等易越界的表述词（候选，供"结论边界"维核对）。
_BOUNDARY_RE = re.compile(
    r"(导致|引起|因果|caus\w*|因此|所以|证明|证实|显著(?:改善|提高|降低)|"
    r"从而|进而|决定|必然)", re.IGNORECASE)


def _split_paragraphs(text: str):
    """按空行/换行切段，返回带 1 基段号的列表。"""
    raw = re.split(r"\n\s*\n|\r\n\s*\r\n", text)
    out = []
    for block in raw:
        block = block.strip()
        if block:
            out.append(block)
    return out


def _split_sentences(block: str):
    """按常见句末标点粗切句，保留原句（不改变文字）。"""
    return [s.strip() for s in re.split(r"(?<=[。！？!?\.])\s+", block) if s.strip()]


def _locator_for(filename: str, para_idx: int, sent_idx: int | None) -> str:
    base = "%s#P%d" % (filename, para_idx + 1)
    if sent_idx is not None:
        base += "S%d" % (sent_idx + 1)
    return base


def extract_structural_features(filename: str, text: str):
    """从单文件文本抽取候选锚点。返回字典，全部为机械提取，非诊断结论。"""
    feats = {
        "file": filename,
        "chars": len(text),
        "paragraph_count": 0,
        "sections": [],
        "claim_sentences": [],
        "number_sentences": [],
        "redundancy_hints": [],
        "boundary_hints": [],
    }
    paragraphs = _split_paragraphs(text)
    feats["paragraph_count"] = len(paragraphs)
    current_section = None
    seen_sections = []
    for pi, block in enumerate(paragraphs):
        head = re.sub(r"^[#>\-\*\s·•]+", "", block)
        m = _SECTION_RE.match(head)
        if m and len(block) <= 120:
            current_section = block.strip()
            if current_section not in seen_sections:
                seen_sections.append(current_section)
            feats["sections"].append({"locator": _locator_for(filename, pi, None),
                                      "heading": current_section})
        sentences = _split_sentences(block)
        for si, sent in enumerate(sentences):
            loc = _locator_for(filename, pi, si)
            if _CLAIM_VERB_RE.search(sent):
                feats["claim_sentences"].append(
                    {"locator": loc, "quote": sent[:400], "section": current_section})
            if _NUM_RE.search(sent):
                feats["number_sentences"].append(
                    {"locator": loc, "quote": sent[:400],
                     "matches": _NUM_RE.findall(sent)[:12],
                     "section": current_section})
            if _BOUNDARY_RE.search(sent):
                feats["boundary_hints"].append(
                    {"locator": loc, "quote": sent[:400], "section": current_section})
    # 重复片段粗检：≥12 字的跨段重复（含跨节），供"重复内容"维核对。
    norm = {pi: re.sub(r"\s+", "", p) for pi, p in enumerate(paragraphs)}
    seen = {}
    for pi, np in norm.items():
        if len(np) < 20:
            continue
        for ln in (12, 16, 20):
            for start in range(0, max(1, len(np) - ln), ln):
                gram = np[start:start + ln]
                if gram in seen and seen[gram] != pi:
                    feats["redundancy_hints"].append({
                        "a": _locator_for(filename, seen[gram], None),
                        "b": _locator_for(filename, pi, None),
                        "fragment": gram[:80],
                    })
                    break
                seen[gram] = pi
            else:
                continue
            break
    return feats


# --------------------------------------------------------------------------- #
# 骨架与证据、校验
# --------------------------------------------------------------------------- #

def _empty_dimensions():
    return {key: {"status": "not_assessed", "issues": []} for key, _ in DIMENSIONS}


def _base_result(case_id, contract_fields):
    result = {
        "case_id": case_id,
        "status": "in_progress",
        "diagnosis": {"summary": "", "dimensions": _empty_dimensions()},
        "edit_plan": [],
        "source_map": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "version_boundary": {
            "material_gaps": [],
            "unconfirmed": [],
            "change_summary": "骨架落盘，尚未开始六维诊断。",
        },
        "change_log": [{
            "time": now_iso(),
            "action": "skeleton_written",
            "status": "in_progress",
        }],
        "runtime": {"started_at": now_iso(), "seconds": 0, "model_requests": 0},
    }
    # 题目 output_contract 的字段原样回填（缺值给 null/[]，不改名不删键）。
    for key in contract_fields:
        if key not in result:
            result[key] = None
    return result


def ensure_skeleton(case_path, task_path, output_dir, result_path):
    """立即落盘 partial 骨架。返回 {ok, case_id, note}。任何失败也尽力写最小产物。"""
    os.makedirs(output_dir, exist_ok=True)
    case_id = "unknown"
    contract_fields = list(DEFAULT_CONTRACT_FIELDS)
    notes = []
    if os.path.isfile(case_path):
        data, note = read_json(case_path)
        notes.append("case.json: %s" % note)
        if isinstance(data, dict):
            case_id = data.get("case_id") or data.get("id") or "unknown"
            oc = data.get("output_contract")
            if isinstance(oc, dict) and oc:
                contract_fields = list(oc.keys())
                notes.append("output_contract fields: %s" % ", ".join(contract_fields))
            else:
                notes.append("no explicit output_contract; using default fields")
    else:
        notes.append("case.json missing; using defaults")
    result = _base_result(case_id, contract_fields)
    write_json_atomic(result_path, result)
    return {"ok": True, "case_id": case_id, "note": " | ".join(notes),
            "result_path": result_path}


def extract_evidence(input_dir, work_dir):
    """扫描输入并抽取候选锚点 → /app/work/evidence.json。"""
    os.makedirs(work_dir, exist_ok=True)
    files, errors = scan_input(input_dir)
    evidence = {"files": files, "errors": errors, "features": [], "summary": {
        "text_files": 0, "pdf_files": 0, "image_files": 0, "table_files": 0,
        "claim_candidates": 0, "number_candidates": 0, "redundancy_hints": 0,
    }}
    for f in files:
        if f["kind"] not in ("text",):
            continue
        text, note = read_text(f["path"])
        if text is None:
            evidence["errors"].append("%s: %s" % (f["name"], note))
            continue
        feats = extract_structural_features(f["name"], text)
        evidence["features"].append(feats)
        evidence["summary"]["claim_candidates"] += len(feats["claim_sentences"])
        evidence["summary"]["number_candidates"] += len(feats["number_sentences"])
        evidence["summary"]["redundancy_hints"] += len(feats["redundancy_hints"])
    for f in files:
        evidence["summary"]["%s_files" % f["kind"]] = \
            evidence["summary"].get("%s_files" % f["kind"], 0) + 1
    out = os.path.join(work_dir, "evidence.json")
    write_json_atomic(out, evidence)
    return {"ok": True, "evidence_path": out, "summary": evidence["summary"],
            "errors": evidence["errors"]}


def verify_contract(result_path, input_dir):
    """校验 result.json：字段完整、locator 可回溯、status 诚实。返回报告。"""
    report = {"ok": True, "checks": [], "warnings": [], "errors": []}
    if not os.path.isfile(result_path):
        report["ok"] = False
        report["errors"].append("result.json missing: %s" % result_path)
        return report
    data, note = read_json(result_path)
    if data is None:
        report["ok"] = False
        report["errors"].append("result.json %s" % note)
        return report
    if not isinstance(data, dict):
        report["ok"] = False
        report["errors"].append("result.json is not an object")
        return report

    for key in ("case_id", "status", "sources", "uncertainty", "change_log"):
        ok = key in data
        report["checks"].append({"key": key, "present": ok})
        if not ok:
            report["errors"].append("missing key: %s" % key)
    status = data.get("status")
    if status not in ("complete", "partial", "failed", "in_progress"):
        report["warnings"].append("unexpected status: %r" % status)

    # locator 可回溯性：把 sources/source_map 中的 locator 文件名与输入目录比对。
    files_in_input, _ = scan_input(input_dir)
    available = {f["name"] for f in files_in_input}
    loc_items = []
    for key in ("source_map", "sources"):
        for item in (data.get(key) or []):
            if isinstance(item, dict):
                loc_items.append(item.get("locator") or item.get("source_ref") or "")
    unresolved = []
    for loc in loc_items:
        if not loc:
            continue
        fname = loc.split("#")[0].split(":")[0].strip()
        if fname and fname not in available and not os.path.isfile(os.path.join(input_dir, fname)):
            unresolved.append(loc)
    report["checks"].append({"key": "locator_traceability",
                             "checked": len(loc_items), "unresolved": len(unresolved)})
    if unresolved:
        report["warnings"].append("unresolved locators: %s" % ", ".join(unresolved[:10]))

    # 诚实性：status=complete 但六个维度均未评估，降级为警告。
    if status == "complete":
        dims = ((data.get("diagnosis") or {}).get("dimensions") or {})
        assessed = sum(1 for v in dims.values()
                       if isinstance(v, dict) and v.get("status") != "not_assessed")
        if assessed < len(DIMENSIONS):
            report["warnings"].append(
                "status=complete but only %d/%d dimensions assessed" % (assessed, len(DIMENSIONS)))

    report["ok"] = not report["errors"]
    return report
