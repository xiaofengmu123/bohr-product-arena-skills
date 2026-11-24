#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patentlib.py — 专利与技术文献检索 Skill 的核心工具库（纯标准库，确定性）。

能力清单：
  * 容错 JSON / 文本读取：utf-8-sig 优先，坏编码降级，单文件 2MB 上限。
  * 输入发现：case.json / task.md / notes.md / materials / CSV / PDF / 图片。
  * 骨架构建：回填 output_contract 全部字段（缺值 null/[]），写入预算账本。
  * 专利号规范化与抽取：US / WO / EP / CN / JP / KR / GB / DE / FR 等。
  * 同族归并：共用申请号 / 优先权号 / 公开号 union-find，观测族口径。
  * 结果校验、checkpoint 增量落盘、report.md 生成。

确定性约定：同一输入重复运行，规范化与归并结果一致（不使用随机数、不依赖哈希顺序）。
"""

import json
import os
import re
from datetime import datetime, timezone

MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文件读取上限 2MB

STATUS_OK = {"ok", "partial", "insufficient_evidence", "input_error"}

# 阶段预算：总 900 秒 / 64 次模型请求，前 180 秒（3 分钟）必须先落盘 partial 骨架。
# 各阶段预算之和 62 ≤ 64，预留 2 次请求作为冲突核验 / 异常处理的余量。
PHASE_BUDGET = [
    {"name": "P0 scaffold",    "start_s": 0,   "end_s": 180, "max_requests": 4,
     "must_save": True,  "deliverable": "partial result.json 骨架（3 分钟内必落盘）"},
    {"name": "P1 scope",       "start_s": 180, "end_s": 300, "max_requests": 6,
     "must_save": False, "deliverable": "research_question / retrieval_scope / authorization"},
    {"name": "P2 numbers",     "start_s": 300, "end_s": 540, "max_requests": 20,
     "must_save": False, "deliverable": "patent_records / families / legal_status"},
    {"name": "P3 topic",       "start_s": 540, "end_s": 780, "max_requests": 24,
     "must_save": False, "deliverable": "literature / evidence / sources"},
    {"name": "P4 finalize",    "start_s": 780, "end_s": 900, "max_requests": 8,
     "must_save": True,  "deliverable": "final result.json + report.md + validate=VALID"},
]

# 无契约时的默认 schema（Agent 在此基础上增量填充）
DEFAULT_SCHEMA_KEYS = [
    "research_question", "retrieval_scope",
    "patent_records", "families", "legal_status", "literature", "authorization",
    "evidence", "sources", "uncertainty", "pending_human",
    "errors", "degradations", "limitations", "change_log",
]

# 可识别的国家/组织代码（用于规范化与字段抽取）
COUNTRY_CODES = {
    "US", "WO", "EP", "CN", "JP", "KR", "GB", "DE", "FR", "CA", "AU", "IN",
    "TW", "RU", "BR", "IT", "ES", "NL", "SE", "CH", "AT", "BE", "DK", "FI",
    "IE", "LU", "MC", "NO", "PT", "ZA", "MX", "SG", "HK", "IL",
}

_KIND_APPLICATION = ("A", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9")
_KIND_GRANT = ("B", "B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B9",
               "C", "C1", "C2", "C3", "C4", "C5", "C6", "C8", "C9")
_KIND_UTILITY = ("U", "U1", "U2", "Y", "Y1", "Y2")


def now_iso():
    """UTC 当前时间，ISO-8601（含时区，秒精度）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def elapsed_since(started_iso):
    """自 started_iso 起经过的秒数；无法解析时返回 0.0。"""
    if not started_iso:
        return 0.0
    try:
        s0 = datetime.fromisoformat(str(started_iso).replace("Z", "+00:00"))
        return max(0.0, (datetime.now(timezone.utc) - s0).total_seconds())
    except (ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------------------
# 容错读写
# ---------------------------------------------------------------------------
def load_json(path):
    """容错读 JSON；返回 (obj, err)。err 为 None 表示成功，否则为 {error: str}。"""
    if not os.path.exists(path):
        return None, {"error": "file not found: %s" % path}
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, {"error": "stat failed: %s" % e}
    if size > MAX_FILE_BYTES:
        return None, {"error": "file exceeds %d bytes" % MAX_FILE_BYTES}

    raw = None
    last_err = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                raw = fh.read()
            break
        except UnicodeError as e:
            last_err = e
            continue
        except OSError as e:
            return None, {"error": str(e)}
    if raw is None:
        return None, {"error": "cannot decode JSON bytes: %s" % last_err}
    try:
        return json.loads(raw), None
    except ValueError as e:
        return None, {"error": "invalid JSON: %s" % e}


def read_text(path, limit=MAX_FILE_BYTES):
    """容错读文本；返回 (text, encoding, err)。"""
    if not os.path.exists(path):
        return "", None, {"error": "file not found: %s" % path}
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return "", None, {"error": "stat failed: %s" % e}
    if size > limit:
        return "", None, {"error": "file exceeds %d bytes" % limit}
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), enc, None
        except UnicodeError:
            continue
        except OSError as e:
            return "", None, {"error": str(e)}
    return "", None, {"error": "cannot decode text bytes"}


def write_json(path, obj):
    """写 JSON（UTF-8，缩进 2，不转义非 ASCII）。"""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 输入发现
# ---------------------------------------------------------------------------
_TEXT_EXT = {".md", ".txt", ".json", ".jsonl", ".csv", ".tsv", ".yaml", ".yml"}
_TABLE_EXT = {".csv", ".tsv"}
_PDF_EXT = {".pdf"}
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".gif", ".bmp"}


def discover_input(input_dir):
    """扫描输入目录，返回文件清单（按相对路径排序，确定性）。"""
    files = []
    if not os.path.isdir(input_dir):
        return files
    for root, dirs, names in os.walk(input_dir):
        dirs[:] = sorted(dirs)
        for name in sorted(names):
            p = os.path.join(root, name)
            rel = os.path.relpath(p, input_dir).replace("\\", "/")
            ext = os.path.splitext(name)[1].lower()
            base = name.lower()
            try:
                size = os.path.getsize(p)
            except OSError:
                size = -1
            if base in ("case.json", "input.json", "case_data.json"):
                kind = "case"
            elif base == "task.md":
                kind = "task"
            elif base in ("notes.md", "readme.md", "note.md"):
                kind = "note"
            elif ext in _TABLE_EXT:
                kind = "table"
            elif ext in _PDF_EXT:
                kind = "pdf"
            elif ext in _IMAGE_EXT:
                kind = "image"
            elif ext in _TEXT_EXT:
                kind = "text"
            else:
                kind = "other"
            files.append({"path": p, "rel": rel, "kind": kind, "size": size})
    return files


# ---------------------------------------------------------------------------
# 骨架构建与契约回填
# ---------------------------------------------------------------------------
def _default_value(v):
    """按契约值类型给缺省：list→[]，dict→递归，其余→None。"""
    if isinstance(v, list):
        return []
    if isinstance(v, dict):
        return {k: _default_value(vv) for k, vv in v.items()}
    return None


def build_skeleton(case_obj, contract):
    """构建 partial 结果骨架：回填 case_id / 契约字段 / 预算账本 / 基础状态字段。"""
    started = now_iso()
    result = {
        "case_id": "unknown",
        "status": "partial",
        "runtime": {
            "started_at": started,
            "finished_at": None,
            "elapsed_seconds": None,
            "last_saved_at": started,
            "checkpoints": [],
        },
        "budget": {
            "total_seconds": 900,
            "model_requests_limit": 64,
            "early_save_seconds": 180,
            "used_model_requests": 0,
            "phases": [dict(p) for p in PHASE_BUDGET],
            "ledger": [],
        },
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "change_log": [],
        "evidence": [],
        "sources": [],
    }

    if isinstance(case_obj, dict):
        cid = (case_obj.get("case_id") or case_obj.get("caseId")
               or case_obj.get("id") or case_obj.get("challenge_id"))
        if cid:
            result["case_id"] = str(cid)
        if case_obj.get("research_question") is not None:
            result["research_question"] = case_obj["research_question"]
        if case_obj.get("task") is not None:
            result["task_hint"] = str(case_obj["task"])[:4000]

    result["contract_fields"] = []
    if isinstance(contract, dict):
        for k, v in contract.items():
            result.setdefault(k, _default_value(v))
        result["contract_fields"] = sorted(contract.keys())
    elif isinstance(contract, list):
        for k in contract:
            if isinstance(k, str) and k:
                result.setdefault(k, None)
        result["contract_fields"] = sorted(str(k) for k in contract if isinstance(k, str))
    else:
        for k in DEFAULT_SCHEMA_KEYS:
            result.setdefault(k, [] if k.endswith("s") or k in (
                "patent_records", "families", "legal_status", "literature",
                "authorization", "evidence", "sources", "uncertainty",
                "pending_human", "errors", "degradations", "limitations") else None)
        result["contract_fields"] = list(DEFAULT_SCHEMA_KEYS)

    result["change_log"].append({
        "at": started,
        "action": "scaffold",
        "note": "early partial skeleton written (3-minute rule)",
    })
    return result


# ---------------------------------------------------------------------------
# 专利号规范化与抽取
# ---------------------------------------------------------------------------
def _canonical_country(code):
    if not code:
        return None
    c = code.upper().strip()
    if c == "USA":
        return "US"
    if c == "UK" or c == "GBR":
        return "GB"
    if c in COUNTRY_CODES:
        return c
    if len(c) >= 2 and c[:2].isalpha() and c[:2].isupper():
        return c[:2]
    return None


def _norm_result(raw, country, digits, kind, ntype, confidence, ok, notes, check_digit=None):
    normalized = None
    if country and digits:
        normalized = "%s%s" % (country, digits)
        if check_digit:
            normalized += "." + check_digit
        if kind:
            normalized += kind
    return {
        "raw": raw,
        "normalized": normalized,
        "country": country,
        "digits": digits,
        "kind_code": kind or None,
        "number_type": ntype,
        "confidence": confidence,
        "format_ok": ok,
        "notes": notes,
    }


def _classify_us(body):
    """对 US 编号主体分类。返回 (number_type, kind_code)。"""
    m = re.match(r'^\s*(\d{4})[/\s](\d{6,8})\s*([A-Z]\d?)?\s*$', body, re.I)
    if m:
        return "publication", (m.group(3) or "A1").upper()
    m = re.match(r'^\s*(6[0-9])[/\s](\d{3},?\d{3})\s*$', body)
    if m:
        return "priority", None  # 临时申请（provisional）
    m = re.match(r'^\s*(\d{2})[/\s](\d{3},?\d{3})\s*$', body)
    if m:
        return "application", None  # 申请序列号（serial number）
    m = re.match(r'^\s*(\d{1,3}(?:,\d{3}){1,3}|\d{6,8})\s*([A-Z]\d?)?\s*$', body)
    if m:
        kc = (m.group(2) or "").upper()
        if kc.startswith(("B", "C")):
            return "grant", kc
        if kc.startswith("A"):
            return "publication", kc
        # 无 kind 码的纯数字：US 专利号（授权号）是 1–8 位无逗号；
        # 带逗号分组且 6 位以上时倾向授权号，但标 medium 置信度交由上层核验。
        return "grant", kc or None
    return "unknown", None


def _classify_wo(body):
    m = re.match(r'^\s*(\d{4})[/\s](\d{6,7})\s*([A-Z]\d?)?\s*$', body, re.I)
    if m:
        return "publication", (m.group(3) or "A1").upper()
    return "unknown", None


def _classify_ep(body):
    m = re.match(r'^\s*(\d{1,4})[\s.]?(\d{3})[\s.]?(\d{3})\s*([A-Z]\d?)?\s*$', body, re.I)
    if m:
        return "publication", (m.group(4) or "A1").upper()
    m = re.match(r'^\s*(\d{7,8})\s*([A-Z]\d?)?\s*$', body)
    if m:
        return "publication", (m.group(2) or "A1").upper()
    return "unknown", None


def _classify_cn(body):
    # CN 申请号带校验位（.X），公开号 9 位无校验位；kind A=发明公开 B/C=授权 U/Y=实用新型
    m = re.match(r'^\s*(\d{9})(?:\.\d)?\s*([A-Z]\d?)?\s*$', body, re.I)
    if m:
        kc = (m.group(2) or "").upper()
        has_check = "." in body
        if kc.startswith(("B", "C")):
            return "grant", kc
        if kc.startswith(("U", "Y")):
            return "utility_model", kc
        if kc.startswith("A"):
            return "publication", kc
        # 无 kind 码：带校验位是申请号，否则倾向公开号
        return ("application" if has_check else "publication"), kc or None
    m = re.match(r'^\s*(\d{8,9})\s*([A-Z]\d?)?\s*$', body)
    if m:
        kc = (m.group(2) or "").upper()
        ntype = "grant" if kc.startswith(("B", "C")) else ("utility_model" if kc.startswith(("U", "Y")) else "publication")
        return ntype, kc or None
    return "unknown", None


def _classify_jp(body):
    m = re.match(r'^\s*(\d{4})-?(\d{6})\s*([A-Z]\d?)?\s*$', body, re.I)
    if m:
        kc = (m.group(3) or "").upper()
        ntype = "publication" if kc.startswith(("A", "B", "C", "U", "Y")) else "publication"
        return ntype, kc or None
    return "unknown", None


def _classify_kr(body):
    m = re.match(r'^\s*(?:10)?-?(\d{4})-?(\d{4,6})\s*([A-Z]\d?)?\s*$', body, re.I)
    if m:
        kc = (m.group(3) or "").upper()
        return "publication", kc or None
    return "unknown", None


def _classify_generic(body, kind):
    """通用分类：依 kind 码判定类型，无 kind 码时未知。"""
    kc = (kind or "").upper()
    if kc in _KIND_APPLICATION:
        return "publication", kc
    if kc in _KIND_GRANT:
        return "grant", kc
    if kc in _KIND_UTILITY:
        return "utility_model", kc
    return "unknown", kind or None


def normalize_patent_number(text):
    """规范化单个专利号。返回结构化 dict（含 raw / normalized / number_type / confidence）。"""
    raw = (text or "").strip()
    if not raw:
        return _norm_result(raw, None, None, None, "unknown", "none", False, ["empty input"])

    # 分离国家代码与编号主体
    country = None
    body = raw
    m = re.match(r'^\s*([A-Za-z]{2,3})\b(.*)$', raw)
    if m:
        cc = _canonical_country(m.group(1))
        if cc:
            country = cc
            body = m.group(2).strip()
    if country is None:
        m2 = re.search(r'\b(USA?|WO|EP|CN|JP|KR|GB|DE|FR|CA|AU|IN|TW|RU|BR|IT|ES|NL|SE|CH)\b', raw, re.I)
        if m2:
            country = _canonical_country(m2.group(1))

    if country is None:
        return _norm_result(raw, None, None, None, "unknown", "none", False,
                            ["no recognized country code"])

    # 提取数字核心与 kind 码：先剥离结尾 kind 码，避免其数字混入数字核心
    kind = None
    numeric_body = body
    km = re.search(r'([A-Za-z])(\d?)\s*$', body)
    if km and len(body.strip()) > 1:
        kind = (km.group(1) + (km.group(2) or "")).upper()
        numeric_body = body[:km.start()].strip()

    check_digit = None
    if "." in numeric_body:
        # CN 等申请号带校验位（如 CN 109876543.2）：校验位单独保留，不混入数字核心
        base, _, check = numeric_body.partition(".")
        digits_all = re.sub(r'[^0-9]', '', base)
        check_digit = re.sub(r'[^0-9]', '', check) or None
    else:
        digits_all = re.sub(r'[^0-9]', '', numeric_body)
    if not digits_all:
        return _norm_result(raw, country, None, kind, "unknown", "none", False,
                            ["no digits found after country code"])

    # 分类
    if country == "US":
        ntype, kind2 = _classify_us(body)
        kind = kind2 or kind
    elif country == "WO":
        ntype, kind2 = _classify_wo(body)
        kind = kind2 or kind
    elif country == "EP":
        ntype, kind2 = _classify_ep(body)
        kind = kind2 or kind
    elif country == "CN":
        ntype, kind2 = _classify_cn(body)
        kind = kind2 or kind
    elif country == "JP":
        ntype, kind2 = _classify_jp(body)
        kind = kind2 or kind
    elif country == "KR":
        ntype, kind2 = _classify_kr(body)
        kind = kind2 or kind
    else:
        ntype, kind2 = _classify_generic(body, kind)
        kind = kind2 or kind

    # 置信度：有明确 kind 码且成功分类 → high；否则 medium/low
    if ntype == "unknown":
        confidence = "low"
    elif kind and kind.upper() in _KIND_APPLICATION + _KIND_GRANT + _KIND_UTILITY:
        confidence = "high"
    else:
        confidence = "medium"

    ok = bool(digits_all and country and ntype != "unknown")
    return _norm_result(raw, country, digits_all, kind, ntype, confidence, ok, [], check_digit)


# 抽取用的正则（宽松匹配，供 extract 后逐条 normalize 复核）
_EXTRACT_PATTERNS = [
    re.compile(r'\b(?:US|USA?)\s*\d{1,4}/\d{6,8}(?:\s*[A-Z]\d?)?\b', re.I),
    re.compile(r'\b(?:US|USA?)\s*\d{1,3}(?:,\d{3})+(?:\s*[A-Z]\d?)?\b', re.I),
    re.compile(r'\bWO\s*\d{4}/\d{6,7}(?:\s*[A-Z]\d?)?\b', re.I),
    re.compile(r'\bEP\s*\d{1,4}(?:\s?\d{3}){2}(?:\s*[A-Z]\d?)?\b', re.I),
    re.compile(r'\bCN\s*\d{9}(?:\.\d)?(?:\s*[A-Z])?\b', re.I),
    re.compile(r'\bJP\s*\d{4}-?\d{6}(?:\s*[A-Z])?\b', re.I),
    re.compile(r'\bKR\s*(?:10-)?\d{4}-?\d{4,6}(?:\s*[A-Z])?\b', re.I),
    re.compile(r'\b(?:GB|DE|FR|CA|AU|IN|TW|RU|BR|IT|ES|NL|SE|CH)\s*\d{6,10}(?:\s*[A-Z]\d?)?\b', re.I),
]


def extract_numbers(text):
    """从文本抽取专利号候选，逐条返回 normalize 结果（不重复、保序）。"""
    hits = []
    seen = set()
    for pat in _EXTRACT_PATTERNS:
        for mm in pat.finditer(text or ""):
            cand = mm.group(0).strip()
            norm = normalize_patent_number(cand)
            key = norm.get("normalized") or cand.upper()
            if key in seen:
                continue
            seen.add(key)
            hits.append({
                "match": cand,
                "span": [mm.start(), mm.end()],
                "normalized": norm,
            })
    hits.sort(key=lambda h: h["span"][0])
    return hits


# ---------------------------------------------------------------------------
# 同族归并（观测族口径）
# ---------------------------------------------------------------------------
def _norm_key(val):
    """把记录中的号码字段转成可比较的 (country, normalized) 键。"""
    if isinstance(val, dict):
        if val.get("normalized"):
            return (val.get("country"), val["normalized"])
        if val.get("raw"):
            n = normalize_patent_number(str(val["raw"]))
            return (n.get("country"), n.get("normalized")) if n.get("normalized") else None
        return None
    if isinstance(val, str) and val.strip():
        n = normalize_patent_number(val)
        return (n.get("country"), n.get("normalized")) if n.get("normalized") else None
    return None


# 参与归并的字段（按可靠度排序）
_FAMILY_FIELDS = ("priority_number", "application_number", "publication_number", "grant_number")


def group_families(records):
    """按共用优先权号/申请号/公开号归并同族。

    规则：两个记录只要在任一归并字段上规范化值相同，即视为同一观测族（union-find）。
    结果恒标 family_type=observed 且 exhaustive=false（观测到的链接不构成穷尽同族）。
    """
    records = [r for r in (records or []) if isinstance(r, dict)]
    n = len(records)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    basis = {}  # (country, normalized) -> (field, first_record_index)
    for field in _FAMILY_FIELDS:
        for i, rec in enumerate(records):
            key = _norm_key(rec.get(field))
            if not key:
                continue
            if key in basis:
                prev_field, prev_idx = basis[key]
                union(prev_idx, i)
            else:
                basis[key] = (field, i)

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    families = []
    for _root, idxs in sorted(groups.items(), key=lambda kv: min(kv[1])):
        members = [records[i] for i in idxs]
        shared = []
        for field in _FAMILY_FIELDS:
            keys = {}
            for m in members:
                k = _norm_key(m.get(field))
                if k:
                    keys.setdefault(k, []).append(m.get("record_id", "?"))
            for k, ids in keys.items():
                if len(ids) > 1:
                    shared.append({"field": field, "normalized": k[1], "record_ids": ids})
        families.append({
            "family_id": "F%d" % (len(families) + 1),
            "member_count": len(members),
            "members": members,
            "family_type": "observed",
            "exhaustive": False,
            "basis": shared or ["single_record_no_shared_link"],
        })
    return families


# ---------------------------------------------------------------------------
# 校验与报告
# ---------------------------------------------------------------------------
_REQUIRED_TOP = ["case_id", "status", "sources", "uncertainty", "pending_human", "change_log"]


def validate_result(result, contract_fields=None):
    """校验 result.json。返回 (ok, errors, warnings)。"""
    errors, warnings = [], []
    if not isinstance(result, dict):
        return False, ["result.json is not a JSON object"], []
    for k in _REQUIRED_TOP:
        if k not in result:
            errors.append("missing required field: %s" % k)
    cid = result.get("case_id")
    if not cid or not str(cid).strip():
        errors.append("case_id is empty")
    elif str(cid).strip() == "unknown" and not any(
            (u or {}).get("item") == "case_id_unknown" for u in result.get("uncertainty", [])):
        warnings.append("case_id is 'unknown' but no uncertainty entry records it")
    if result.get("status") not in STATUS_OK:
        errors.append("invalid status: %r" % result.get("status"))
    for k in (contract_fields or []):
        if k not in result:
            errors.append("output_contract field missing: %s" % k)
    for i, ev in enumerate(result.get("evidence", []) or []):
        if isinstance(ev, dict) and not ev.get("locator") and not ev.get("quote"):
            warnings.append("evidence[%d] lacks locator+quote" % i)
    return (not errors), errors, warnings


def _render_section(lines, title, items, field_map):
    if not items:
        return
    lines.append("")
    lines.append("## %s" % title)
    lines.append("")
    for it in items:
        if not isinstance(it, dict):
            lines.append("- %s" % it)
            continue
        parts = []
        for key in field_map:
            v = it.get(key)
            if v not in (None, "", []):
                if isinstance(v, (dict, list)):
                    v = json.dumps(v, ensure_ascii=False)
                parts.append("%s=%s" % (key, v))
        lines.append("- " + ("; ".join(parts) if parts else json.dumps(it, ensure_ascii=False)))


def write_report(path, result):
    """生成人类可读 report.md（只摘要，不含受限库原文）。"""
    L = []
    L.append("# 专利与技术文献检索报告")
    L.append("")
    L.append("- case_id: `%s`" % result.get("case_id"))
    L.append("- status: `%s`" % result.get("status"))
    L.append("- generated_at: %s" % (result.get("runtime", {}).get("finished_at") or now_iso()))
    if result.get("research_question"):
        L.append("")
        L.append("## 研究问题")
        L.append("")
        L.append(str(result["research_question"]))
    if result.get("retrieval_scope"):
        L.append("")
        L.append("## 检索范围")
        L.append("")
        L.append(str(result["retrieval_scope"]))
    _render_section(L, "目标专利记录", result.get("patent_records"),
                    ["record_id", "publication_number", "number_type", "confidence"])
    _render_section(L, "同族归并", result.get("families"),
                    ["family_id", "member_count", "family_type", "exhaustive"])
    _render_section(L, "法律状态", result.get("legal_status"),
                    ["status", "effective_date", "as_of", "confidence"])
    _render_section(L, "同主题技术文献", result.get("literature"),
                    ["rank", "title", "year", "source"])
    _render_section(L, "证据", result.get("evidence"),
                    ["locator", "quote", "supports"])
    _render_section(L, "实际执行的检索", result.get("sources"),
                    ["engine", "query", "url", "retrieved_at"])
    _render_section(L, "不确定性", result.get("uncertainty"), ["item", "reason"])
    _render_section(L, "待人工复核", result.get("pending_human"), ["item", "why"])
    _render_section(L, "限制与降级", result.get("limitations"), ["item", "reason"])
    _render_section(L, "降级记录", result.get("degradations"), ["item", "reason"])
    L.append("")
    L.append("> 本报告由 finalize 生成，仅作可读摘要；以 result.json 为准。")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
