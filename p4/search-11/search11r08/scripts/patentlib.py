#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patentlib.py — 专利与技术文献检索 Skill 的核心工具库（纯标准库）。

职责：
  * 容错读取输入目录（不假设固定文件名/键名）
  * 专利号/申请号/优先权号规范化与类型判别
  * 从文本（含 OCR 粘连文本）抽取专利号候选
  * 按共用申请号/优先权号归并同族并标注口径
  * 生成 partial 结果骨架、契约回填与结果校验

设计约束：确定性（同一输入两次运行结果一致）、可降级（坏 JSON/坏编码
不崩溃）、可复核（每条归一化结果携带 raw、normalized、number_type 与
confidence）。本模块不执行任何网络请求，只做离线文本处理与结构化校验。
"""

import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# 常量与默认值
# ---------------------------------------------------------------------------

TEXT_LIMIT_BYTES = 2 * 1024 * 1024  # 单文件 2MB 上限

# 常见国家/组织代码（用于号码前缀识别）
COUNTRY_CODES = (
    "US", "WO", "EP", "CN", "JP", "KR", "DE", "GB", "FR", "CA", "AU",
    "TW", "IN", "RU", "BR", "IT", "ES", "NL", "SE", "CH", "AT", "BE",
    "DK", "FI", "NO", "PL", "PT", "IE", "IL", "ZA", "MX", "AR", "CL",
    "NZ", "SG", "HK", "MY", "PH", "TH", "ID", "VN",
)

# 专利号中常见 kind code（授权/公开类型）
KIND_CODES = (
    "A1", "A2", "A3", "A9", "B1", "B2", "B9", "C1", "C2", "C9",
    "T", "U", "S", "Y", "E", "P",
)

DEFAULT_SCHEMA_KEYS = (
    "case_id", "status", "research_question", "retrieval_scope",
    "patent_records", "families", "legal_status", "literature",
    "authorization", "evidence", "sources", "uncertainty",
    "pending_human", "errors", "degradations", "limitations",
    "change_log", "runtime",
)

# 各类字段的默认值（骨架/契约回填时使用）
FIELD_DEFAULTS = {
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
}


# ---------------------------------------------------------------------------
# 时间与容错 I/O
# ---------------------------------------------------------------------------

def now_iso():
    """返回带时区的 ISO-8601 时间串（确定性由系统时钟保证）。"""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_json(path):
    """容错读取 JSON：支持 utf-8-sig、BOM，坏 JSON 返回 err 而非抛异常。"""
    try:
        raw = open(path, "rb").read()
    except OSError as exc:
        return None, {"error": "cannot open file: %s" % exc, "path": path}
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text), None
    except (json.JSONDecodeError, ValueError) as exc:
        return None, {"error": "invalid JSON: %s" % exc, "path": path}


def read_text(path, max_bytes=TEXT_LIMIT_BYTES):
    """容错读取文本文件，返回 (text, encoding, err)。超限跳过并记录。"""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return "", None, {"error": "stat failed: %s" % exc}
    if size > max_bytes:
        return "", None, {"error": "file exceeds %d bytes" % max_bytes,
                          "size": size}
    try:
        raw = open(path, "rb").read()
    except OSError as exc:
        return "", None, {"error": "cannot open file: %s" % exc}
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    encoding = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc)
            encoding = enc
            break
        except UnicodeDecodeError:
            continue
    if encoding is None:
        text = raw.decode("utf-8", errors="replace")
        encoding = "utf-8(replace)"
    # 统一换行，方便后续正则与定位
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text, encoding, None


def discover_input(input_dir):
    """扫描输入目录，返回文件清单（dict 列表），不假设固定文件名。

    识别规则：
      * case.json / 任意含 "case" 且以 .json 结尾 → kind="case"
      * .pdf → "pdf"；.png/.jpg/.jpeg/.tif/.tiff → "image"
      * .csv/.tsv → "table"；.md/.txt/.json/.yaml/.yml → "text"
      * 其余 → "other"（保留大小与相对路径）
    """
    files = []
    if not input_dir or not os.path.isdir(input_dir):
        return files
    for root, _dirs, names in os.walk(input_dir):
        for name in names:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir).replace(os.sep, "/")
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            lower = name.lower()
            if lower == "case.json":
                kind = "case"
            elif lower.endswith(".pdf"):
                kind = "pdf"
            elif lower.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")):
                kind = "image"
            elif lower.endswith((".csv", ".tsv")):
                kind = "table"
            elif lower.endswith((".md", ".txt", ".json", ".yaml", ".yml")):
                kind = "text"
            else:
                kind = "other"
            files.append({"rel": rel, "path": full, "kind": kind, "size": size})
    files.sort(key=lambda f: (f["rel"], f["kind"]))
    return files


# ---------------------------------------------------------------------------
# 专利号规范化
# ---------------------------------------------------------------------------

def _clean(text):
    """规整空白、全角字符与 Unicode 兼容字符。"""
    text = unicodedata.normalize("NFKC", text or "")
    return text.strip()


def _split_kind(text):
    """从号码末尾剥离 kind code（如 A1/B2），返回 (body, kind)。

    支持两类形态：
      1. 空格分隔：`US 10,123,456 B2` → body="US 10,123,456", kind="B2"
      2. 直接粘连（OCR 合并边界）：`WO2020/123456A1` → body="WO2020/123456", kind="A1"
    仅当末尾字母/字母+数字前一位是数字时才剥离，避免误伤申请号校验位。
    """
    text = (text or "").strip()
    # 空格分隔的 kind code
    m = re.search(r"\s+([A-Z][0-9]?)$", text)
    if m and (m.group(1) in KIND_CODES or re.fullmatch(r"[A-Z][0-9]", m.group(1))):
        return text[:m.start()].strip(), m.group(1).upper()
    # 粘连的 kind code：前一位必须是数字
    m2 = re.search(r"([A-Z][0-9]?)$", text)
    if m2 and m2.start() > 0 and text[m2.start() - 1].isdigit():
        candidate = m2.group(1).upper()
        if candidate in KIND_CODES or re.fullmatch(r"[A-Z][0-9]?", candidate):
            return text[:m2.start()].strip(), candidate
    return text.strip(), ""


def normalize_patent_number(raw):
    """规范化一个专利号字符串，返回结构化 dict。

    返回值字段：
      raw, normalized, country, doc_number, kind, number_type, confidence,
      note（当判别不确定时说明理由）

    number_type ∈ {publication, application, priority, unknown}
    confidence ∈ {high, medium, low}
    不判定的情况写 unknown 与 low，不硬猜。
    """
    raw = (raw or "").strip()
    result = {
        "raw": raw,
        "normalized": "",
        "country": "",
        "doc_number": "",
        "kind": "",
        "number_type": "unknown",
        "confidence": "low",
        "note": "",
    }
    if not raw:
        result["note"] = "empty input"
        return result

    cleaned = _clean(raw)
    # 去掉常见标点（保留 / 与 . 用于申请号判断）
    compact = re.sub(r"[\s,，;；:_\-]+", "", cleaned).upper()

    body, kind = _split_kind(re.sub(r"[\s,，;；]+", " ", cleaned))
    body_compact = re.sub(r"[\s,，;；:_\-]+", "", body).upper()

    # ---- PCT 国际申请号：PCT/CCYYYY/NNNNNN ----
    m_pct = re.match(r"^PCT/([A-Z]{2})(\d{4})/(\d{1,7})$", body_compact)
    if m_pct:
        country = m_pct.group(1)
        core = m_pct.group(2) + m_pct.group(3).zfill(6)
        result.update({
            "normalized": "PCT" + country + core + (kind or ""),
            "country": country,
            "doc_number": "PCT" + country + core,
            "kind": kind,
            "number_type": "application",
            "confidence": "high",
            "note": "PCT international application number",
        })
        return result

    country = ""
    m = re.match(r"^([A-Z]{2})(.+)$", body_compact)
    if m and m.group(1) in COUNTRY_CODES:
        country = m.group(1)
        digits = m.group(2)
    else:
        digits = body_compact

    # 去除正文中残留的斜杠/点，仅保留数字与字母
    core = re.sub(r"[^0-9A-Z]", "", digits)
    if not re.search(r"[0-9]", core):
        result["note"] = "no digits found; cannot normalize"
        return result

    # ---- 判别类型 ----
    number_type = "unknown"
    confidence = "low"
    note = ""

    has_slash = "/" in digits
    has_dot = "." in digits or re.search(r"\d\.\d", digits)

    if country == "WO" or (has_slash and country == "US" and re.match(r"^\d{4}/", digits)):
        # WO/YYYY/NNNNNN 或 US YYYY/NNNNNN（PCT 公开）
        m4 = re.match(r"^(\d{4})/(\d{1,7})([A-Z][0-9]?)?$", digits)
        if m4:
            number_type = "publication"
            confidence = "high"
            core = m4.group(1) + m4.group(2).zfill(6)
            note = "publication with 4-digit year + serial"
        elif re.match(r"^[A-Z]{2}\d{4}/\d{1,7}$", digits) and country == "WO":
            number_type = "publication"
            confidence = "medium"
    elif has_slash and re.match(r"^\d{1,2}/\d{3,8}$", digits):
        # US 申请号/临时申请号：NN/NNNNNN
        first = digits.split("/")[0]
        if first in ("61", "62"):
            number_type = "priority"
            confidence = "high"
            note = "US provisional application number (61/62 series)"
        else:
            number_type = "application"
            confidence = "high"
            note = "US application serial number"
        core = digits.replace("/", "")
    elif country == "EP" and (has_dot or re.fullmatch(r"\d{8}", core)):
        number_type = "application"
        confidence = "high"
        note = "EP application number"
    elif country == "CN" and re.fullmatch(r"\d{13}", core) and core[:4] >= "2000":
        # CN 申请号：4 位年份 + 8 位序号 + 1 位校验
        number_type = "application"
        confidence = "high"
        note = "CN application number (13 digits)"
    elif country == "CN" and re.fullmatch(r"\d{9}", core):
        # CN 公开号（9 位数字，首 1 位专利类型 + 8 位序号）
        number_type = "publication"
        confidence = "high"
        note = "CN publication number (9 digits)"
    elif re.fullmatch(r"\d{7,13}", core):
        # 纯数字，无法区分公开/申请，保守标 publication（最常见为公开号）
        number_type = "publication"
        confidence = "medium"
        note = "numeric-only; publication assumed, verify against official register"
    else:
        number_type = "unknown"
        confidence = "low"
        note = "unrecognized format; needs manual verification"

    normalized = country + core + (kind or "")
    result.update({
        "normalized": normalized,
        "country": country,
        "doc_number": core,
        "kind": kind,
        "number_type": number_type,
        "confidence": confidence,
        "note": note,
    })
    return result


# 专利号候选正则（宽松匹配，避免漏检 OCR 粘连，再由 normalize 判定）
NUMBER_PATTERNS = [
    # WO/EP/CN/US 公开号
    re.compile(r"\b(?:WO|EP|CN|US|JP|KR|DE|GB|FR|CA|AU|TW)\s*\d{1,4}(?:[\s,/]\d{2,8}){1,3}\s*[A-Z]\d?\b", re.I),
    # US 申请号 16/123,456
    re.compile(r"\b\d{2}/\d{3,6}(?:,\d{3})?\b"),
    # 纯数字公开号（US 10,123,456）
    re.compile(r"\b\d{1,2},\d{3},\d{3}\b"),
    # CN 13 位申请号
    re.compile(r"\b\d{13}\b"),
    # CN 9 位公开号
    re.compile(r"\b\d{9}\b"),
]


def extract_numbers(text):
    """从文本抽取专利号候选（含 OCR 粘连文本），返回 hit 列表。

    每个 hit：{raw, normalized, start, end, confidence}
    通过 normalize_patent_number 统一判定，保留上下文片段供人工核验。
    """
    text = _clean(text or "")
    seen = {}
    hits = []
    kept_spans = []  # 已保留的更大匹配区间，用于抑制重叠子串
    for pattern in NUMBER_PATTERNS:
        for m in pattern.finditer(text):
            raw = m.group(0).strip()
            key = re.sub(r"\s+", "", raw).upper()
            if key in seen:
                continue
            # 若该匹配完全落在已保留区间内，跳过（避免 "10,123,456" 重复）
            contained = any(s <= m.start() and m.end() <= e
                            for s, e in kept_spans)
            if contained:
                continue
            seen[key] = True
            kept_spans.append((m.start(), m.end()))
            norm = normalize_patent_number(raw)
            if norm["number_type"] == "unknown" and not re.search(r"\d{4}", raw):
                continue
            ctx_start = max(0, m.start() - 40)
            ctx_end = min(len(text), m.end() + 40)
            hits.append({
                "raw": raw,
                "normalized": norm["normalized"],
                "number_type": norm["number_type"],
                "confidence": norm["confidence"],
                "country": norm["country"],
                "kind": norm["kind"],
                "start": m.start(),
                "end": m.end(),
                "context": text[ctx_start:ctx_end].replace("\n", " "),
            })
    # 按出现位置排序，保持文档顺序
    hits.sort(key=lambda h: (h["start"], h["end"]))
    return hits


# ---------------------------------------------------------------------------
# 同族归并
# ---------------------------------------------------------------------------

def _family_key(record):
    """提取归并键：优先用 priority/application 号，其次用 publication 号。"""
    for field in ("priority_number", "application_number", "publication_number"):
        val = record.get(field) or record.get(field + "_normalized")
        if val:
            if isinstance(val, dict):
                val = val.get("normalized") or val.get("raw")
            key = re.sub(r"\s+", "", str(val)).upper()
            if key:
                return field, key
    return None, None


def group_families(records):
    """按共用申请号/优先权号归并同族，返回 family 列表。

    口径：observed（仅基于给定记录观察到的共用号码），exhaustive=False，
    不推断未提供成员的完整族谱。归并键优先 priority > application >
    publication；无法归并的每条记录单独成族并标 family_type="single"。
    """
    records = records or []
    if not isinstance(records, list):
        records = [records]

    groups = {}          # key -> list of record indices
    singles = []         # 无共享键的记录
    key_map = []         # 每条记录的归并键信息

    for idx, rec in enumerate(records):
        if not isinstance(rec, dict):
            singles.append(idx)
            key_map.append((None, None))
            continue
        field, key = _family_key(rec)
        key_map.append((field, key))
        if key:
            groups.setdefault(key, []).append(idx)
        else:
            singles.append(idx)

    families = []
    used = set()
    # 优先输出多成员族
    for key, indices in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(indices) <= 1:
            continue
        members = []
        basis = None
        for i in indices:
            used.add(i)
            field, _k = key_map[i]
            members.append({
                "record_id": records[i].get("record_id", "rec-%d" % i),
                "publication_number": records[i].get("publication_number"),
                "application_number": records[i].get("application_number"),
                "priority_number": records[i].get("priority_number"),
            })
            basis = field
        families.append({
            "family_id": "fam-%d" % (len(families) + 1),
            "family_type": "observed",
            "exhaustive": False,
            "basis": basis,
            "key": key,
            "members": members,
            "note": "grouped by shared %s within provided records only; "
                    "not an exhaustive legal family" % (basis or "number"),
        })

    # 单成员 / 无法归并的记录
    for i in range(len(records)):
        if i in used:
            continue
        field, key = key_map[i]
        rec = records[i] if isinstance(records[i], dict) else {}
        families.append({
            "family_id": "fam-%d" % (len(families) + 1),
            "family_type": "single",
            "exhaustive": False,
            "basis": field,
            "key": key,
            "members": [{
                "record_id": rec.get("record_id", "rec-%d" % i),
                "publication_number": rec.get("publication_number"),
                "application_number": rec.get("application_number"),
                "priority_number": rec.get("priority_number"),
            }],
            "note": "no shared application/priority number observed in provided records",
        })
    return families


# ---------------------------------------------------------------------------
# 骨架构建与契约回填
# ---------------------------------------------------------------------------

def build_skeleton(case_obj, contract):
    """构建 partial 结果骨架。

    - case_id：优先 case_obj.case_id / case_obj.caseId，否则 "unknown"。
    - status：默认 "partial"（scaffold 阶段尚未完成分析）。
    - 若提供 output_contract，其全部字段原样回填（缺值给 null/[]，不改名、
      不删未知键），再叠加默认 schema 键，保证两者字段都保留。
    """
    case_id = None
    if isinstance(case_obj, dict):
        case_id = (case_obj.get("case_id") or case_obj.get("caseId")
                   or case_obj.get("id"))

    result = {
        "case_id": case_id or "unknown",
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

    if not case_id:
        result["uncertainty"].append({
            "item": "case_id_missing",
            "reason": "case_id not present in case.json; set to 'unknown'",
        })

    # 契约字段原样回填（不改名、不删未知键）
    if isinstance(contract, dict):
        for key, value in contract.items():
            if key in result:
                continue
            if isinstance(value, (list, dict)):
                result[key] = value
            elif value is None:
                result[key] = None
            else:
                result[key] = value

    # 默认 schema 键兜底（缺则填默认值）
    for key, default in FIELD_DEFAULTS.items():
        result.setdefault(key, default)

    return result


# ---------------------------------------------------------------------------
# 结果校验
# ---------------------------------------------------------------------------

def validate_result(result, contract=None):
    """校验 result.json 合法性，返回 (ok, errors)。

    必查项：
      * result 为 dict
      * case_id 非空
      * status ∈ {ok, partial, insufficient_evidence, input_error}
      * sources/uncertainty/change_log 为 list
      * 若提供契约，检查契约的全部顶层键都出现在 result 中
    """
    errors = []
    if not isinstance(result, dict):
        return False, ["result is not a JSON object"]

    if not result.get("case_id"):
        errors.append("case_id is empty")

    status = result.get("status")
    allowed = {"ok", "partial", "insufficient_evidence", "input_error"}
    if status not in allowed:
        errors.append("invalid status: %r (allowed %s)"
                      % (status, sorted(allowed)))

    for key in ("sources", "uncertainty", "change_log", "errors",
                "degradations", "limitations", "pending_human"):
        if key in result and not isinstance(result[key], list):
            errors.append("%s must be a list" % key)

    if isinstance(contract, dict):
        for key in contract:
            if key not in result:
                errors.append("output_contract key missing: %s" % key)

    return (len(errors) == 0), errors


def write_result(path, result):
    """以 utf-8 + ensure_ascii=False + indent=2 写 result.json。"""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)


def append_change(result, item):
    """向 change_log 追加一条变更记录，避免重复代码。"""
    result.setdefault("change_log", []).append(item)
    return result
