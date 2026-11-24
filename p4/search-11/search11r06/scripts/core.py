#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.py — 专利与技术文献检索 Skill 的核心库（纯标准库）。

职责：
  1. 容错输入发现（扫描 /input，识别 case/task/materials/PDF/表格，utf-8-sig 容错，
     单文件 2MB 上限）；
  2. 专利号规范化与 OCR 边界感知的号码抽取；
  3. 按共用申请号/优先权号做同族归并（口径固定为 observed，exhaustive=false）；
  4. 结果骨架构建（output_contract 原样回填，缺值 null/[]）；
  5. 中间表示：plan.json（检索计划）/ records.json（规范化专利记录模型）/
     evidence_map.json（结论-证据对齐），并带 SHA-256 溯源链；
  6. 契约校验与 report.md 生成。

设计取舍（相对基线包的差异）：
  * 所有检索行动先落 plan.json，再动手；plan 是"意图 + 数据源分级 + 预算"的机器可读
    中间表示，评审者可据此复跑"为什么搜这些、用什么库、什么权限"。
  * 每条结论经 evidence_map 对齐 locator + 逐字 quote，冲突标 source_conflict。
  * 官方法律状态未知时默认 unknown 并写 staleness_risk，绝不推断"有效"。
  * 同族只标注 observed（本语料可见的连接），不冒充 simple/extended 全量族。
  * 所有函数确定性：同一输入重复运行结果一致（无随机数、无时间敏感排序差异）。
"""

import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone

MAX_FILE_SIZE = 2 * 1024 * 1024  # 2MB

COUNTRY_CODES = {
    "US", "EP", "WO", "CN", "JP", "KR", "DE", "GB", "FR", "CA", "AU", "IN",
    "RU", "TW", "ES", "IT", "NL", "SE", "CH", "AT", "BE", "DK", "FI", "IE",
    "LU", "MC", "PT", "GR", "CZ", "PL", "HU", "RO", "SK", "NO", "IS", "TR",
    "BR", "MX", "AR", "CL", "CO", "ZA", "IL", "SG", "MY", "PH", "TH", "ID",
    "VN", "HK", "MO", "NZ",
}

# 公开/授权文献类型代码（kind code）；用于区分 publication / grant。
KIND_CODES = {
    "A", "A1", "A2", "A3", "A8", "A9",
    "B", "B1", "B2", "B8", "B9",
    "C", "C1", "C2", "C8", "C9",
    "U", "U1", "Y", "Y1", "T", "S", "E", "F", "P",
}

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}

NUMBER_TYPES = ("publication", "application", "priority", "grant", "unknown")


# --------------------------------------------------------------------------- #
# 时间与哈希（确定性）
# --------------------------------------------------------------------------- #
def now_iso():
    """UTC 秒级 ISO 时间戳；用于 runtime 与 change_log。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def short_hash(text):
    if text is None:
        text = ""
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:8]


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _elapsed(started, finished):
    """解析两个 ISO 时间戳，返回秒（容错，失败返回 0.0）。"""
    try:
        s0 = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        s1 = datetime.fromisoformat(str(finished).replace("Z", "+00:00"))
        return round(max(0.0, (s1 - s0).total_seconds()), 1)
    except (ValueError, TypeError):
        return 0.0


# --------------------------------------------------------------------------- #
# 输入发现与容错读取
# --------------------------------------------------------------------------- #
def discover_input(input_dir):
    """递归扫描输入目录，返回按 rel 路径排序的文件描述列表。"""
    files = []
    if not input_dir or not os.path.isdir(input_dir):
        return files
    for root, _dirs, fnames in os.walk(input_dir):
        for name in sorted(fnames):
            full = os.path.join(root, name)
            if not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, input_dir).replace(os.sep, "/")
            lower = name.lower()
            if lower == "case.json":
                kind = "case"
            elif lower in ("task.md", "task.txt", "task.json"):
                kind = "task"
            elif lower == "notes.md":
                kind = "notes"
            elif lower.endswith(".md") or lower.endswith(".txt"):
                kind = "text"
            elif lower.endswith(".json"):
                kind = "json"
            elif lower.endswith(".csv") or lower.endswith(".tsv"):
                kind = "table"
            elif lower.endswith(".pdf"):
                kind = "pdf"
            elif lower.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".bmp")):
                kind = "image"
            else:
                kind = "other"
            files.append({
                "path": full, "rel": rel,
                "size": os.path.getsize(full), "kind": kind,
            })
    return files


def read_text(path, limit=MAX_FILE_SIZE):
    """容错读文本：超限跳过、utf-8-sig 优先、失败降级不抛异常。"""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return "", None, {"error": "stat_failed", "reason": str(e)}
    if size > limit:
        return "", None, {"error": "file_too_large", "size": size, "limit": limit}
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        return "", None, {"error": "read_failed", "reason": str(e)}
    text = None
    enc = None
    for candidate in ("utf-8-sig", "utf-8", "gb18030", "latin-1", "cp1252"):
        try:
            text = raw.decode(candidate)
            enc = candidate
            break
        except (UnicodeDecodeError, ValueError):
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
        enc = "utf-8-replace"
        return text, enc, {"error": "decode_fallback"}
    return text, enc, None


def load_json(path):
    """容错读 JSON；坏 JSON 返回 (None, err)，不抛异常。"""
    if not os.path.exists(path):
        return None, {"error": "not_found", "path": path}
    text, _enc, err = read_text(path)
    if err and not text:
        return None, err
    try:
        return json.loads(text), None
    except (json.JSONDecodeError, ValueError) as e:
        return None, {"error": "json_decode_failed", "reason": str(e)}


# --------------------------------------------------------------------------- #
# 契约回填与结果骨架
# --------------------------------------------------------------------------- #
def coerce_default(spec):
    """把 output_contract 的键规格映射为中性缺省值（缺值给 null/[]）。"""
    if isinstance(spec, list):
        return []
    if isinstance(spec, dict):
        if "default" in spec:
            return spec["default"]
        t = str(spec.get("type", "")).lower()
        if t in ("array", "list"):
            return []
        if t in ("object", "dict"):
            return {}
        return None
    t = str(spec).strip().lower()
    if t in ("array", "list", "[]"):
        return []
    if t in ("object", "dict", "{}"):
        return {}
    if t in ("string", "str", "text"):
        return ""
    if t in ("number", "integer", "int", "float", "bool", "boolean"):
        return None
    return None


def _resolve_case_id(case_obj):
    if not isinstance(case_obj, dict):
        return "unknown"
    for k in ("case_id", "caseId", "id"):
        v = case_obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)):
            return str(v)
    return "unknown"


def build_skeleton(case_obj, contract):
    """构建合法 partial 骨架：case_id/status + 全部默认字段 + 契约键原样回填。"""
    now = now_iso()
    case_id = _resolve_case_id(case_obj)
    result = {
        "case_id": case_id,
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
        "runtime": {"started_at": now},
        "intermediate_artifacts": [],
    }
    if isinstance(contract, dict):
        for key, spec in contract.items():
            if key not in result:
                result[key] = coerce_default(spec)
    result["change_log"].append({
        "at": now, "event": "scaffold", "detail": "partial skeleton committed early",
    })
    return result


# --------------------------------------------------------------------------- #
# 专利号规范化
# --------------------------------------------------------------------------- #
def _clean_punct(num):
    return re.sub(r"[^0-9A-Z/.]", "", num)


def normalize_patent_number(raw):
    """规范化单个专利号/申请号/优先权号，返回结构化记录。

    返回字段：raw / country / number / kind_code / number_type / normalized /
    status / confidence / notes。无法识别时 status != ok，绝不抛出。
    """
    if raw is None:
        return {"raw": None, "status": "error", "reason": "null_input"}
    original = str(raw).strip()
    if not original:
        return {"raw": original, "status": "error", "reason": "empty_input"}
    text = unicodedata.normalize("NFKC", original).upper()
    text = text.replace("\u200b", "").replace("\u00a0", " ").replace("\u2011", "-")
    notes = []

    is_application_marker = bool(re.search(r"\b(APPLICATION|APPL|APPLN|APP)\b", text))
    is_priority_marker = bool(re.search(r"\b(PRIORITY|PRIOR|PR|PROV)\b", text))
    text = re.sub(r"\b(APPLICATION|APPL|APPLN|APP)\b", " ", text)
    text = re.sub(r"\b(PRIORITY|PRIOR|PR|PROV|PROVISIONAL)\b", " ", text)

    country = None
    m = re.match(r"^\s*([A-Z]{2})\b", text)
    if m and m.group(1) in COUNTRY_CODES:
        country = m.group(1)
        text = text[m.end():].strip()
    if country is None:
        m = re.match(r"^\s*PCT/?", text)
        if m:
            country = "WO"
            text = text[m.end():].strip()
    if country is None:
        m = re.match(r"^\s*([A-Z]{2})(\d)", text)
        if m and m.group(1) in COUNTRY_CODES:
            country = m.group(1)
            text = text[2:].strip()
    if country is None:
        return {"raw": original, "status": "unrecognized", "reason": "no_country_code"}

    # 尾部类型代码（A1/B2/U/…）
    kind = None
    m = re.search(r"\s*([A-Z]\d?)\s*$", text)
    if m and m.group(1) in KIND_CODES:
        kind = m.group(1)
        text = text[:m.start()].strip()
    elif re.fullmatch(r"[A-Z]", text):
        if text in KIND_CODES:
            kind = text
            text = ""

    number = _clean_punct(text)
    number = re.sub(r"[^0-9/.]", "", number)
    number = re.sub(r"\s+", "", number)
    if number in ("", "/", "."):
        return {"raw": original, "status": "unrecognized", "reason": "no_number"}

    has_slash = "/" in number
    has_dot = "." in number

    if kind:
        normalized = "%s%s%s" % (country, number, kind)
    else:
        normalized = "%s%s" % (country, number)

    # 类型推断：类型代码优先于上下文标记。
    if kind in ("B", "B1", "B2", "B8", "B9", "C", "C1", "C2", "C8", "C9"):
        number_type = "grant"
    elif kind in ("U", "U1", "Y", "Y1"):
        number_type = "grant"
    elif kind:
        number_type = "publication"
    elif is_priority_marker:
        number_type = "priority"
    elif is_application_marker or has_slash or has_dot:
        number_type = "application"
    else:
        number_type = "publication"
        notes.append("bare_number_assumed_publication")

    confidence = "high"
    if not kind and not has_slash and not has_dot:
        confidence = "medium"
        notes.append("no_kind_or_separator")
    if "PROV" in original.upper() or is_priority_marker:
        notes.append("possibly_provisional")

    return {
        "raw": original,
        "country": country,
        "number": number,
        "kind_code": kind,
        "number_type": number_type,
        "normalized": normalized,
        "status": "ok",
        "confidence": confidence,
        "notes": notes,
    }


def normalize_record(value):
    """规范化 records 输入中的一个值：可以是字符串或已有结构化记录。"""
    if isinstance(value, str):
        return normalize_patent_number(value)
    if isinstance(value, dict):
        if value.get("normalized") and value.get("status") == "ok":
            return value
        raw = value.get("raw") or value.get("number") or value.get("value")
        rec = normalize_patent_number(raw)
        rec["record_id"] = value.get("record_id")
        return rec
    return normalize_patent_number(value)


# --------------------------------------------------------------------------- #
# 号码抽取（OCR 边界感知）
# --------------------------------------------------------------------------- #
def extract_numbers(text):
    """从自由文本/OCR 文本抽取专利号候选；合并边界只做候选，不擅自合并。

    WO/OCR 常把相邻号码粘连或把分隔符吃掉，本函数返回候选 + 位置 +
    ocr_uncertain 标记，供 Agent 逐条回官方库核对，绝不静默合并或拆分。
    """
    if not text:
        return []
    text = unicodedata.normalize("NFKC", str(text)).upper()
    results = []
    seen = set()
    pattern = re.compile(r"(?<![A-Z0-9])([A-Z]{2})\s*(\d[\d,/\s.]{3,28})")
    for m in pattern.finditer(text):
        cc = m.group(1)
        if cc not in COUNTRY_CODES:
            continue
        numpart = re.sub(r"[\s,]+$", "", m.group(2))
        end = m.end()
        kind = None
        km = re.match(r"\s*([A-Z]\d?)\b", text[end:end + 5])
        if km and km.group(1) in KIND_CODES:
            kind = km.group(1)
            raw = (cc + " " + numpart + " " + kind).strip()
        else:
            raw = (cc + " " + numpart).strip()
        norm = normalize_patent_number(raw)
        if norm.get("status") != "ok":
            continue
        key = norm["normalized"]
        if key in seen:
            continue
        seen.add(key)
        tight = (cc + numpart + (kind or "")).replace(" ", "").replace(",", "")
        ocr_uncertain = tight != norm["normalized"].replace(",", "")
        results.append({
            "raw": raw,
            "span": [m.start(), end + (len(kind) + 1 if kind else 0)],
            "normalized": norm["normalized"],
            "country": norm.get("country"),
            "number_type": norm.get("number_type"),
            "kind_code": norm.get("kind_code"),
            "confidence": norm.get("confidence"),
            "ocr_uncertain": ocr_uncertain,
        })
    return results


# --------------------------------------------------------------------------- #
# 同族归并（observed，非 exhaustive）
# --------------------------------------------------------------------------- #
def _norm_key(v):
    if v is None:
        return None
    s = unicodedata.normalize("NFKC", str(v)).upper()
    s = re.sub(r"[^A-Z0-9]", "", s)
    return s


def _field_values(rec, names):
    out = set()
    for name in names:
        v = rec.get(name)
        if isinstance(v, list):
            for x in v:
                k = _norm_key(x)
                if k:
                    out.add(k)
        elif v:
            k = _norm_key(v)
            if k:
                out.add(k)
    return out


def group_families(records):
    """按共用申请号或优先权号做并查集归并；结果口径为 observed。"""
    if not isinstance(records, list):
        records = [records] if records else []
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

    app_keys = [_field_values(r, ("application_number", "application", "app_no"))
                for r in records]
    pri_keys = [_field_values(r, ("priority_numbers", "priorities", "priority"))
                for r in records]

    for i in range(n):
        for j in range(i + 1, n):
            if (app_keys[i] & app_keys[j]) or (pri_keys[i] & pri_keys[j]):
                union(i, j)

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    families = []
    for members in sorted(groups.values(), key=lambda ms: min(ms)):
        fam = {
            "family_id": "fam-%02d" % (len(families) + 1),
            "member_ids": [records[i].get("record_id") or "rec-%d" % i for i in members],
            "family_type": "observed",
            "exhaustive": False,
            "basis": "shared_application_or_priority_number",
            "evidence": [],
        }
        families.append(fam)
    return families


# --------------------------------------------------------------------------- #
# 中间表示：plan.json
# --------------------------------------------------------------------------- #
FIVE_QUESTIONS = [
    {
        "id": "q_what_protected",
        "question": "这件专利保护什么（权利要求逐字引用，不越界概括）",
        "target_fields": ["claims"],
        "method": "从来源定位逐字引用权利要求文本，概括必须附原文 quote",
    },
    {
        "id": "q_family_relation",
        "question": "它与同族其他成员什么关系（simple/extended/observed 分开）",
        "target_fields": ["application_number", "priority_numbers", "family_ids"],
        "method": "按共用申请号/优先权号归并，口径标注 observed，exhaustive=false",
    },
    {
        "id": "q_legal_status",
        "question": "现在是否有效（状态 + 生效日 + 来源 + 查询日期 + 滞后风险）",
        "target_fields": ["legal_status"],
        "method": "仅采用官方登记来源；官方未知则 status=unknown 并写 staleness_risk",
    },
    {
        "id": "q_who_else",
        "question": "同一技术主题还有谁在做（专利文献 vs 技术文献分开）",
        "target_fields": ["literature", "patent_records"],
        "method": "分类号/申请人/引证组合检索，区分专利文献与技术文献的检索逻辑",
    },
    {
        "id": "q_authorization",
        "question": "这些信息是否有权访问与展示（绿/黄/红依赖分级与降级）",
        "target_fields": ["authorization"],
        "method": "逐源记录许可、可展示字段、留存期限；受限库不抓取、不绕过",
    },
]


def build_plan(case_id, question=None, scope=None, object_type=None):
    """构建检索计划中间表示（机器可读，评审可复跑）。"""
    return {
        "plan_id": "plan-" + short_hash(case_id or "unknown"),
        "case_id": case_id or "unknown",
        "created_at": now_iso(),
        "research_question": question,
        "retrieval_scope": scope,
        "object_type": object_type,  # number_lookup | topic_search | mixed
        "objects": [],
        "five_questions": FIVE_QUESTIONS,
        "data_source_tiers": [
            {
                "source": "official_registry",
                "tier": "green",
                "examples": ["USPTO", "Espacenet", "PATENTSCOPE", "国家局官方登记"],
                "basis": "公开登记数据，可引用字段与查询日期",
            },
            {
                "source": "publisher_preprint",
                "tier": "yellow",
                "examples": ["期刊页面", "arXiv 等预印本"],
                "basis": "元数据可引，注意版本与是否同行评议",
            },
            {
                "source": "restricted_commercial",
                "tier": "red",
                "examples": ["需登录的商业专利库", "付费全文"],
                "basis": "不登录、不抓取、不展示原文；仅记录降级方案",
            },
        ],
        "steps": [
            {"seq": 0, "name": "scaffold", "output": "/app/output/result.json (partial)"},
            {"seq": 1, "name": "plan", "output": "/app/work/plan.json"},
            {"seq": 2, "name": "normalize_extract", "output": "/app/work/records.json"},
            {"seq": 3, "name": "family", "output": "families[] in result.json"},
            {"seq": 4, "name": "topic_search", "output": "literature[] + sources[]"},
            {"seq": 5, "name": "evidence", "output": "/app/work/evidence_map.json"},
            {"seq": 6, "name": "finalize", "output": "result.json + report.md"},
        ],
        "budget": {
            "total_seconds": 900,
            "total_model_requests": 64,
            "tokens_per_response": 16384,
            "early_checkpoint_seconds": 180,
            "early_checkpoint_rule": "前 3 分钟内必须落盘一版合法 partial result.json，之后只做增量改进",
        },
        "early_checkpoint_committed": False,
    }


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #
def validate_result(result):
    """校验 result.json；返回 (ok, errors[])。校验点对应评分维度。"""
    errors = []
    if not isinstance(result, dict):
        return False, ["result is not a JSON object"]
    case_id = result.get("case_id")
    if not case_id or not str(case_id).strip():
        errors.append("case_id missing or empty")
    status = result.get("status")
    if status not in ALLOWED_STATUS:
        errors.append("status invalid: %r (allowed %s)" % (status, sorted(ALLOWED_STATUS)))
    for key in ("sources", "uncertainty", "change_log"):
        v = result.get(key)
        if not isinstance(v, list):
            errors.append("%s must be a list" % key)
    # 证据溯源：每个 evidence 条目必须有 locator + quote
    evidence = result.get("evidence", [])
    if not isinstance(evidence, list):
        errors.append("evidence must be a list")
    else:
        for i, ev in enumerate(evidence):
            if not isinstance(ev, dict):
                errors.append("evidence[%d] is not an object" % i)
                continue
            if not ev.get("locator"):
                errors.append("evidence[%d] missing locator" % i)
            if not ev.get("quote"):
                errors.append("evidence[%d] missing quote" % i)
    # 关键结论主产物字段存在
    for key in ("patent_records", "families", "legal_status", "literature",
                "authorization", "evidence", "sources"):
        if key not in result:
            errors.append("missing main field: %s" % key)
    # 禁止伪造分数
    for forbidden in ("score", "grader_score", "benchmark_score"):
        if forbidden in result:
            errors.append("fabricated field forbidden: %s" % forbidden)
    return (len(errors) == 0, errors)


# --------------------------------------------------------------------------- #
# 证据图（中间表示：evidence_map.json）
# --------------------------------------------------------------------------- #
def build_evidence_map(result):
    """从 result['evidence'] 生成证据图 + 每条结论的可溯源哈希。"""
    evidence = result.get("evidence", []) if isinstance(result, dict) else []
    mapping = {"evidence_count": len(evidence), "items": [], "conflicts": []}
    for i, ev in enumerate(evidence):
        if not isinstance(ev, dict):
            continue
        item = {
            "id": ev.get("id") or "ev-%03d" % i,
            "locator": ev.get("locator"),
            "quote": ev.get("quote"),
            "claim": ev.get("claim"),
            "source_conflict": bool(ev.get("source_conflict")),
            "fingerprint": short_hash(
                str(ev.get("locator")) + "|" + str(ev.get("quote"))
            ),
        }
        mapping["items"].append(item)
        if item["source_conflict"]:
            mapping["conflicts"].append(item["id"])
    return mapping


def record_artifact(result, path, kind, note):
    """把中间产物登记进 result['intermediate_artifacts']（带哈希，幂等去重）。"""
    artifacts = result.setdefault("intermediate_artifacts", [])
    if not isinstance(artifacts, list):
        artifacts = []
        result["intermediate_artifacts"] = artifacts
    try:
        digest = file_sha256(path)
        size = os.path.getsize(path)
    except OSError:
        return result
    for art in artifacts:
        if art.get("path") == path and art.get("sha256") == digest:
            return result
    artifacts.append({
        "path": path, "kind": kind, "note": note,
        "sha256": digest, "size": size,
    })
    return result


# --------------------------------------------------------------------------- #
# report.md 生成
# --------------------------------------------------------------------------- #
def _md_value(v):
    if v is None:
        return "—"
    if isinstance(v, (dict, list)):
        return "```json\n%s\n```" % json.dumps(v, ensure_ascii=False, indent=2)
    return str(v)


def render_report(result):
    lines = []
    lines.append("# 专利与技术文献检索 — 结果报告")
    lines.append("")
    lines.append("- case_id: `%s`" % result.get("case_id", "unknown"))
    lines.append("- status: `%s`" % result.get("status", "unknown"))
    lines.append("- research_question: %s" % _md_value(result.get("research_question")))
    lines.append("- retrieval_scope: %s" % _md_value(result.get("retrieval_scope")))
    rt = result.get("runtime", {})
    if rt.get("finished_at"):
        lines.append("- runtime: %s → %s（%s s）" % (
            rt.get("started_at", "—"), rt.get("finished_at", "—"),
            rt.get("elapsed_seconds", "—")))
    lines.append("")
    for section, key in (
        ("专利记录", "patent_records"),
        ("同族", "families"),
        ("法律状态", "legal_status"),
        ("技术文献", "literature"),
        ("授权边界", "authorization"),
        ("证据", "evidence"),
        ("检索执行记录", "sources"),
        ("不确定性", "uncertainty"),
        ("待人工复核", "pending_human"),
        ("限制", "limitations"),
        ("降级", "degradations"),
        ("错误", "errors"),
    ):
        val = result.get(key, [])
        lines.append("## %s" % section)
        lines.append("")
        if isinstance(val, list) and len(val) == 0:
            lines.append("（无）")
        else:
            lines.append("```json")
            lines.append(json.dumps(val, ensure_ascii=False, indent=2))
            lines.append("```")
        lines.append("")
    lines.append("## 变更记录")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(result.get("change_log", []), ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    lines.append("> 本报告由 `scripts/main.py finalize` 生成，为可读视图；正式结果以 "
                 "`/app/output/result.json` 为准。所有结论均需按 `evidence[]` 的 "
                 "locator + quote 复核。")
    return "\n".join(lines)
