#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core_impl.py — 事实优先的事实库（factbase）与结果契约引擎。

设计取舍（与"编译优先"参考包明显不同）：
  1. 中间表示：所有数字、单位、引用、符号、论断、表格单元先抽取为
     带类型的 fact（fact_id / type / locator / quote），写作阶段只消费事实库，
     不再重新解析原材料。
  2. 校验顺序反转：先校验事实与契约字段（缺口在写 .tex 之前就暴露），
     再合成稿件、再进入编译循环。避免"编译通过但事实错误"。
  3. 冲突自动标记：同一 locator / 同一度量出现两个不同值 => source_conflict。

仅依赖 Python 标准库。所有函数对非致命失败返回 (None, error) 而不是抛异常，
保证空输入 / 坏 JSON / 坏编码下都能落盘一版合法 result.json。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB
TEXT_EXTS = {
    ".md", ".tex", ".bib", ".txt", ".csv", ".json", ".yaml", ".yml",
    ".rst", ".cls", ".sty", ".log", ".aux", ".bbl", ".cfg", ".lst",
}
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
CASE_ID_KEYS = ("case_id", "id", "challenge_id", "benchmarkId", "armiId")
FACT_TYPES = ("numeric", "unit", "citation", "ref", "symbol", "claim", "table_cell")

# 数字：千分位、整数、小数、科学计数法（单一综合模式，避免漏掉普通整数）。
_NUM_RE = re.compile(
    r"(?<![\w.])[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?(?![\d,])",
)
_UNIT_RE = re.compile(
    r"\b(?:kg|g|mg|µg|s|ms|min|h|m|cm|mm|nm|µm|km|L|mL|mol|mmol|"
    r"eV|keV|MeV|GeV|J|kJ|W|kW|MW|Pa|kPa|MPa|K|°C|%|V|A|S|mS|µS|Ω|"
    r"Hz|kHz|MHz|GHz|Nm|rad|sr|cd|Bq|Gy|Sv)\b"
)
_CITE_CMD_RE = re.compile(r"\\(?:cite|citet|citep|citeauthor|citeyear|Citet|Citep)\s*\{([^}]*)\}")
_REF_CMD_RE = re.compile(r"\\(?:ref|eqref|pageref|autoref|cref)\s*\{([^}]*)\}")
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"'<>]+")
_ARXIV_RE = re.compile(r"\barXiv:\d{4}\.\d{4,5}(?:v\d+)?")
_PMID_RE = re.compile(r"\bPMID:\s*\d+")
_AUTHYEAR_RE = re.compile(r"\(([A-Z][A-Za-z'\-]+(?:\s+(?:and|&)\s+[A-Z][A-Za-z'\-]+)?,\s*\d{4}[a-z]?)\)")
_BRACKET_CITE_RE = re.compile(r"\[(\d{1,3}(?:[,\-\s]+\d{1,3})*)\]")
_GREEK_RE = re.compile(r"\\[a-zA-Z]+\b")


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def reconfigure_stdio():
    for stream in (__import__("sys").stdout, __import__("sys").stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def read_text(path):
    """容错读取 UTF-8 文本。返回 (text, error)。"""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, "read_error: %s" % exc
    if len(raw) > MAX_FILE_BYTES:
        return None, "skipped: exceeds 2MiB"
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "decoded_with_replacement"


def read_json(path):
    """容错 JSON 读取（支持被截断的 JSON，取最长合法前缀）。"""
    text, err = read_text(path)
    if text is None:
        return None, [err or "unreadable"]
    try:
        return json.loads(text), []
    except json.JSONDecodeError as exc:
        cut = text[:exc.pos]
        try:
            return json.loads(cut), ["json_truncated_at_%d" % exc.pos]
        except Exception:
            return None, ["bad_json: %s" % exc]


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # 原子替换，避免留下不完整 result.json
    return path


def discover_input(root):
    files = []
    if not os.path.isdir(root):
        return files
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            ext = os.path.splitext(name)[1].lower()
            files.append({
                "path": full,
                "rel": os.path.relpath(full, root).replace("\\", "/"),
                "size": size,
                "ext": ext,
                "is_text": ext in TEXT_EXTS,
            })
    return files


def find_case_id(obj):
    if not isinstance(obj, dict):
        return None
    for key in CASE_ID_KEYS:
        val = obj.get(key)
        if val not in (None, "", [], {}):
            return str(val)
    return None


def contract_keys(obj):
    """返回 output_contract 的字段清单，保持顺序；也支持 {"fields": [...]} 等变体。"""
    if not isinstance(obj, dict):
        return []
    contract = obj.get("output_contract")
    keys = []
    if isinstance(contract, dict):
        if isinstance(contract.get("fields"), list):
            keys = [str(f) for f in contract["fields"] if isinstance(f, str)]
        else:
            keys = list(contract.keys())
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                keys.extend(str(k) for k in item.keys())
    # 去重保序
    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def build_skeleton(root):
    """由 /input/case.json 构造 result.json 骨架。返回 (skeleton, keys)。"""
    case_path = os.path.join(root, "case.json")
    notes = []
    case_id = None
    keys = []
    if os.path.isfile(case_path):
        obj, errs = read_json(case_path)
        notes.extend(errs)
        case_id = find_case_id(obj)
        keys = contract_keys(obj)
        if not keys:
            notes.append("output_contract_not_found: metadata-only result")
    else:
        notes.append("case.json_not_found_in:" + root)

    if case_id is None:
        case_id = "unknown"
        notes.append("case_id_unknown: used 'unknown'")

    skeleton = {"case_id": case_id}
    for key in keys:
        skeleton[key] = None
    skeleton.update({
        "status": "partial" if keys else "input_error",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "time": utcnow(),
            "step": "bootstrap",
            "note": "result skeleton written before analysis; contract fields default to null",
        }],
        "errors": notes,
        "degradations": [],
        "runtime": {"started_at": utcnow(), "producer": "core_impl.py"},
    })
    return skeleton, keys


# --------------------------------------------------------------------------- #
# 事实库（intermediate representation）                                        #
# --------------------------------------------------------------------------- #

def _locator(rel, line):
    return "%s#L%d" % (rel, line)


def extract_facts_from_text(text, rel):
    """把单份文本材料抽成带类型的事实列表（确定性输出）。"""
    facts = []
    lines = text.splitlines()
    used = set()  # (type, span) 去重

    def add(ftype, value, line_no, quote, start=None, end=None):
        key = (ftype, value, line_no)
        if key in used:
            return
        used.add(key)
        facts.append({
            "type": ftype,
            "value": value,
            "locator": _locator(rel, line_no),
            "quote": (quote or "").strip()[:200],
            "span": [start, end] if start is not None else None,
            "confidence": "verbatim",
        })

    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("%") or stripped.startswith("#"):
            continue
        # numeric + unit
        for m in _NUM_RE.finditer(line):
            unit = ""
            tail_start = m.end()
            tail = line[tail_start:tail_start + 16]
            um = _UNIT_RE.match(tail.lstrip())
            if um:
                lead = len(tail) - len(tail.lstrip())
                unit = um.group(0)
                unit_start = tail_start + lead
                unit_end = unit_start + len(unit)
                add("numeric", m.group(0) + " " + unit, idx, line, m.start(), unit_end)
                add("unit", unit, idx, line, unit_start, unit_end)
            else:
                add("numeric", m.group(0), idx, line, m.start(), m.end())
        # citations / refs
        for m in _CITE_CMD_RE.finditer(line):
            add("citation", m.group(1), idx, line, m.start(), m.end())
        for m in _REF_CMD_RE.finditer(line):
            add("ref", m.group(1), idx, line, m.start(), m.end())
        for m in _DOI_RE.finditer(line):
            add("citation", m.group(0), idx, line, m.start(), m.end())
        for m in _ARXIV_RE.finditer(line):
            add("citation", m.group(0), idx, line, m.start(), m.end())
        for m in _PMID_RE.finditer(line):
            add("citation", m.group(0), idx, line, m.start(), m.end())
        for m in _AUTHYEAR_RE.finditer(line):
            add("citation", m.group(1), idx, line, m.start(), m.end())
        for m in _BRACKET_CITE_RE.finditer(line):
            add("citation", m.group(1), idx, line, m.start(), m.end())
        # math symbols（含 LaTeX 命令的数学片段）
        for m in re.finditer(r"\$[^$]{1,120}\$", line):
            seg = m.group(0)
            if "\\" in seg or _GREEK_RE.search(seg):
                add("symbol", seg, idx, line, m.start(), m.end())
        # 论断（以句号分隔的陈述句，作为可溯源论断）
        for sent in re.split(r"(?<=[。.!?])\s+", stripped):
            s = sent.strip()
            if len(s) >= 20 and any(ch.isdigit() for ch in s):
                add("claim", s, idx, line)
    return facts


# 不作为科学材料参与事实抽取的文件（契约/指令/构建产物/配置）。
_NON_MATERIAL_BASENAMES = {"case.json", "task.md", "inventory.json"}
_NON_MATERIAL_EXTS = {".json", ".yaml", ".yml", ".log", ".aux", ".bbl",
                      ".cls", ".sty", ".cfg", ".lst", ".fls", ".out", ".toc"}


def _is_material(rec):
    base = os.path.basename(rec["path"]).lower()
    if base in _NON_MATERIAL_BASENAMES:
        return False
    return rec["ext"] not in _NON_MATERIAL_EXTS


def extract_facts(root):
    """扫描 /input 下的科学材料文本，构建事实库。返回 (factbase, notes)。"""
    facts, notes, files = [], [], []
    for rec in discover_input(root):
        if not rec["is_text"] or not _is_material(rec):
            continue
        text, err = read_text(rec["path"])
        if text is None:
            notes.append("%s: %s" % (rec["rel"], err))
            continue
        files.append(rec["rel"])
        facts.extend(extract_facts_from_text(text, rec["rel"]))

    # 全局唯一 fact_id（跨文件不冲突）
    for i, f in enumerate(facts, start=1):
        f["fact_id"] = "f%04d" % i

    # 保守冲突检测：同一 locator、同一单位出现不同数值量级才标 source_conflict。
    # （同一行出现多个不同数字/引用是常态，不算冲突。）
    by_slot = {}
    for f in facts:
        if f["type"] != "numeric":
            continue
        parts = f["value"].split(None, 1)
        unit = parts[1] if len(parts) == 2 else ""
        mag = parts[0]
        by_slot.setdefault((f["locator"], unit), []).append((mag, f["fact_id"]))
    conflicts = []
    for (loc, unit), items in by_slot.items():
        mags = {m for m, _ in items}
        if len(mags) > 1:
            conflicts.append({
                "locator": loc,
                "type": "numeric",
                "unit": unit,
                "values": sorted(mags),
                "fact_ids": sorted(fid for _, fid in items),
            })
    return {
        "generated_at": utcnow(),
        "source_files": sorted(files),
        "fact_count": len(facts),
        "facts": facts,
        "conflicts": conflicts,
    }, notes


def index_sources(root):
    """构建 source_index：每个文本文件的段落 / 行索引，供逐字引用。"""
    index = {"files": {}}
    for rec in discover_input(root):
        if not rec["is_text"]:
            continue
        text, err = read_text(rec["path"])
        if text is None:
            index["files"][rec["rel"]] = {"error": err, "lines": []}
            continue
        lines = text.splitlines()
        index["files"][rec["rel"]] = {
            "sha256": sha256_hex(text),
            "line_count": len(lines),
            "lines": lines,
        }
    return index


def quote_file(root, rel, start, end):
    """按 locator 取逐字片段。start/end 为 1-based 行号。"""
    rec = [r for r in discover_input(root) if r["rel"] == rel]
    if not rec:
        return None, "file_not_found:" + rel
    text, err = read_text(rec[0]["path"])
    if text is None:
        return None, err
    lines = text.splitlines()
    if start < 1:
        start = 1
    if end is None or end > len(lines):
        end = len(lines)
    if start > len(lines):
        return None, "line_out_of_range"
    return "\n".join(lines[start - 1:end]), None


# --------------------------------------------------------------------------- #
# 契约校验与收尾                                                               #
# --------------------------------------------------------------------------- #

def resolve_status(skeleton, keys, has_primary, has_material):
    """按证据强度诚实判定 status。"""
    if skeleton.get("case_id") in (None, "unknown"):
        return "input_error"
    filled = sum(1 for k in keys if skeleton.get(k) not in (None, "", []))
    if not has_material:
        return "insufficient_evidence"
    if not has_primary:
        return "partial"
    if filled >= len(keys) and keys:
        return "ok"
    if filled > 0:
        return "partial"
    return "partial"


def ensure_meta(skeleton):
    """补齐 result.json 的元数据键，避免缺失。"""
    for key, default in (
        ("sources", []), ("uncertainty", []), ("pending_human", []),
        ("change_log", []), ("errors", []), ("degradations", []),
    ):
        if key not in skeleton or not isinstance(skeleton[key], list):
            skeleton[key] = default
    if "runtime" not in skeleton or not isinstance(skeleton.get("runtime"), dict):
        skeleton["runtime"] = {}
    skeleton["runtime"].setdefault("finished_at", utcnow())
    return skeleton


def load_result(path):
    obj, errs = read_json(path)
    return obj, errs


def selfcheck(output, work, input_root):
    """校验 result.json 是否满足输出契约。返回 (report, exit_code)。"""
    result_path = os.path.join(output, "result.json")
    report = {"ok": True, "problems": [], "warnings": []}
    obj, errs = load_result(result_path)
    if obj is None:
        report["ok"] = False
        report["problems"].append("result.json_unreadable_or_invalid: %s" % errs)
        return report, 1
    if "case_id" not in obj:
        report["ok"] = False
        report["problems"].append("missing_case_id")
    if obj.get("status") not in ALLOWED_STATUS:
        report["ok"] = False
        report["problems"].append("invalid_or_missing_status")
    # 契约键齐全性：与 inventory 中记录的 contract_keys 比对
    inv_path = os.path.join(work, "inventory.json")
    keys = []
    if os.path.isfile(inv_path):
        inv, _ = read_json(inv_path)
        keys = inv.get("contract_keys", []) if isinstance(inv, dict) else []
    elif os.path.isdir(input_root):
        case_path = os.path.join(input_root, "case.json")
        if os.path.isfile(case_path):
            cobj, _ = read_json(case_path)
            keys = contract_keys(cobj) if isinstance(cobj, dict) else []
    for k in keys:
        if k not in obj:
            report["ok"] = False
            report["problems"].append("missing_contract_key:%s" % k)
    # sources 结构
    for i, s in enumerate(obj.get("sources", []) or []):
        if not isinstance(s, dict) or "locator" not in s or "quote" not in s:
            report["problems"].append("bad_source_entry:%d" % i)
            report["ok"] = False
    # 必需列表字段
    for k in ("sources", "uncertainty", "change_log"):
        if k not in obj or not isinstance(obj.get(k), list):
            report["problems"].append("missing_or_bad_list:%s" % k)
            report["ok"] = False
    if not report["ok"]:
        return report, 1
    return report, 0
