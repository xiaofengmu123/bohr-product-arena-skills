#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patentlib.py — 专利与技术文献检索 Skill 的纯标准库核心。

提供：容错文件读取、输入发现、结果骨架、号码规范化/抽取、同族归并、
结果校验与报告渲染。所有分析函数确定性：相同输入产生相同输出。
"""

import json
import os
import re
from datetime import datetime, timezone

MAX_FILE_BYTES = 2 * 1024 * 1024
STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

# 常见国家/组织专利代码（大写两位）
COUNTRY_CODES = {
    "US", "EP", "WO", "CN", "JP", "KR", "DE", "FR", "GB", "CA", "AU",
    "IN", "RU", "TW", "ES", "IT", "NL", "SE", "CH", "AT", "BE", "DK",
    "FI", "NO", "PL", "PT", "IE", "ZA", "BR", "MX", "SG", "HK", "IL",
}
KIND_CODES = {
    "A", "A1", "A2", "A3", "A9", "B", "B1", "B2", "B9",
    "C", "C1", "C2", "U", "S", "Y", "T", "E", "D",
}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def read_text(path):
    """utf-8-sig 容错读取；超限/失败返回降级结果。"""
    try:
        size = os.path.getsize(path)
        if size > MAX_FILE_BYTES:
            return "", "utf-8-sig", {"error": "file_too_large", "bytes": size}
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return fh.read(), "utf-8-sig", None
    except OSError as exc:
        return "", None, {"error": str(exc)}


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return json.load(fh), None
    except (OSError, ValueError) as exc:
        return None, {"error": str(exc)}


def classify_file(name, rel):
    low = name.lower()
    if low == "case.json":
        return "case"
    if low == "task.md":
        return "task"
    if low == "notes.md":
        return "notes"
    if low.endswith(".json"):
        return "text"
    if low.endswith((".csv", ".tsv")):
        return "table"
    if low.endswith((".md", ".txt", ".xml", ".html", ".htm")):
        return "text"
    if low.endswith(".pdf"):
        return "pdf"
    if low.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".webp")):
        return "image"
    return "other"


def discover_input(root):
    """扫描输入目录，返回稳定排序的文件描述列表。"""
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace("\\", "/")
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            out.append({
                "path": full, "rel": rel, "name": name,
                "size": size, "kind": classify_file(name, rel),
            })
    out.sort(key=lambda f: f["rel"])
    return out


def build_skeleton(case_obj, contract=None):
    """构造合法 partial 骨架；contract 字段原样回填（缺值 null）。"""
    cid = None
    if isinstance(case_obj, dict):
        cid = case_obj.get("case_id") or case_obj.get("caseId") or case_obj.get("id")
    result = {
        "case_id": cid or "unknown",
        "status": "partial",
        "research_question": None,
        "retrieval_scope": None,
        "patent_records": [],
        "families": [],
        "legal_status": [],
        "literature": [],
        "authorization": [],
        "evidence": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "change_log": [],
        "runtime": {"started_at": now_iso()},
    }
    if isinstance(contract, dict):
        for key, value in contract.items():
            result[key] = value
    return result


def normalize_patent_number(text):
    """规范化一个专利号。返回 raw/normalized/country/kind/number_type/valid。"""
    raw = (text or "").strip()
    if not raw:
        return {"raw": "", "normalized": None, "country": None, "kind": None,
                "number_type": "unknown", "valid": False, "notes": ["empty_input"]}
    s = raw
    for ch in ", ()[]；;，。":
        s = s.replace(ch, "")
    s = re.sub(r"\s+", "", s)
    country = None
    m = re.match(r"^([A-Za-z]{2})", s)
    if m and m.group(1).upper() in COUNTRY_CODES:
        country = m.group(1).upper()
        s = s[2:]
    kind = None
    m = re.search(r"([A-Za-z]\d|[A-Za-z])$", s)
    if m:
        cand = m.group(1).upper()
        if cand in KIND_CODES:
            kind = cand
            s = s[:m.start()]
    body = s
    valid = bool(re.search(r"\d", body))
    notes = []
    if not valid:
        notes.append("no_digits")
    number_type = "publication"
    if kind in ("B", "B1", "B2", "C", "C1", "C2"):
        number_type = "grant"
    elif "/" in body and country == "US" and not kind:
        number_type = "application"
    elif "-" in body and country == "JP":
        number_type = "application"
    normalized = (country or "") + body + (kind or "") if valid else None
    # compact：去除 / . - 等分隔符后的规范等值键，用于同族/去重比对
    compact = (country or "") + re.sub(r"\D", "", body) + (kind or "") if valid else None
    return {
        "raw": raw, "normalized": normalized, "compact": compact,
        "country": country, "kind": kind, "number": body,
        "number_type": number_type, "valid": valid, "notes": notes,
    }


_EXTRACT_PATTERNS = (
    r"\b([A-Z]{2}\s*\d[\d,/\-\.]{2,}(?:\s*[A-Z][0-9]?)?)",
    r"\b(\d+(?:[/\-\.]\d+)+(?:\s*[A-Z][0-9]?)?)",
    r"\b(\d{6,}(?:\s*[A-Z][0-9]?)?)",
)


def extract_numbers(text):
    """从文本抽取专利号候选；返回 raw/span/normalized 列表（去重）。"""
    hits, seen = [], set()
    for pat in _EXTRACT_PATTERNS:
        for m in re.finditer(pat, text or ""):
            raw = m.group(1)
            n = normalize_patent_number(raw)
            key = n.get("normalized") or raw
            if key in seen:
                continue
            seen.add(key)
            hits.append({
                "raw": raw, "span": [m.start(), m.end()],
                "normalized": n.get("normalized"), "country": n.get("country"),
                "kind": n.get("kind"), "number_type": n.get("number_type"),
                "valid": n.get("valid"),
            })
    return hits


def _norm_field(record, key):
    val = record.get(key)
    if isinstance(val, dict):
        return val.get("normalized") or val.get("raw")
    return val


def group_families(records):
    """按共用优先权号/申请号归并同族；仅观察语料，exhaustive:false。"""
    families, used = [], set()
    for i, rec in enumerate(records):
        if i in used:
            continue
        rid = rec.get("record_id") or "rec-%d" % i
        basis, group_key = None, None
        for field in ("priority_number", "application_number"):
            v = _norm_field(rec, field)
            if v:
                n = normalize_patent_number(str(v))
                if n.get("compact"):
                    basis, group_key = field, n["compact"]
                    break
        members = [rid]
        if group_key:
            for j, rec2 in enumerate(records):
                if j == i or j in used:
                    continue
                v2 = _norm_field(rec2, basis)
                if v2 and normalize_patent_number(str(v2)).get("compact") == group_key:
                    members.append(rec2.get("record_id") or "rec-%d" % j)
                    used.add(j)
        used.add(i)
        ftype = "simple" if basis == "priority_number" else (
            "extended" if basis == "application_number" else "observed")
        families.append({
            "family_id": "fam-%03d" % len(families),
            "family_type": ftype,
            "basis_field": basis or "none",
            "basis_normalized": group_key,
            "members": members,
            "exhaustive": False,
        })
    return families


def validate_result(result):
    """校验 result.json 结构；返回 (ok, issues)。"""
    issues = []
    if not isinstance(result, dict):
        return False, ["result is not a JSON object"]
    for key in ("case_id", "status", "sources", "uncertainty", "change_log"):
        if key not in result:
            issues.append("missing key: %s" % key)
    if result.get("status") not in STATUSES:
        issues.append("invalid status: %r" % result.get("status"))
    if not result.get("case_id"):
        issues.append("case_id empty")
    for key in ("sources", "uncertainty", "pending_human", "errors",
                "degradations", "limitations", "change_log"):
        if key in result and not isinstance(result[key], list):
            issues.append("key %s must be a list" % key)
    return (len(issues) == 0), issues


def render_report(result):
    lines = [
        "# 检索结果报告", "",
        "- case_id: %s" % result.get("case_id"),
        "- status: %s" % result.get("status"),
        "- patent_records: %d" % len(result.get("patent_records", [])),
        "- families: %d" % len(result.get("families", [])),
        "- literature: %d" % len(result.get("literature", [])),
        "- sources: %d" % len(result.get("sources", [])),
        "- uncertainty: %d" % len(result.get("uncertainty", [])),
        "- limitations: %d" % len(result.get("limitations", [])),
        "",
    ]
    for item in result.get("limitations", [])[:20]:
        lines.append("- 限制：%s" % json.dumps(item, ensure_ascii=False))
    return "\n".join(lines)
