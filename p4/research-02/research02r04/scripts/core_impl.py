#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — 标准库工具与契约解析（供 main.py / selftest.py 复用）。

设计目标：
- 坏编码、坏 JSON、缺文件、缺 pdftotext 都不崩溃；
- 所有函数确定性执行（固定顺序、无随机、无时间依赖的排序）；
- 只使用 Python 标准库，可在不同机器/不同 Python 3 版本复跑。
"""

import hashlib
import json
import os
from datetime import datetime, timezone

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
MAX_FILE_BYTES = 2 * 1024 * 1024

PDF_EXTS = {".pdf"}
TEXT_EXTS = {".txt", ".md", ".markdown"}
RECORD_EXTS = {".json", ".jsonl", ".csv", ".bib", ".ris", ".xml"}
MATERIAL_EXTS = PDF_EXTS | TEXT_EXTS | RECORD_EXTS
META_NAMES = {"case.json", "task.md", "result.json"}

# 无 output_contract 时的兜底字段；只作结构兜底，绝不据此编造内容。
DEFAULT_CONTRACT_FIELDS = {
    "research_question": None,
    "screening_criteria": None,
    "screening_log": [],
    "evidence_table": [],
    "themes": [],
    "review_draft": None,
    "conflicts_and_gaps": [],
    "references": [],
}

# 证据行必填字段（校验用）。
EVIDENCE_REQUIRED_FIELDS = [
    "evidence_id", "source_id", "locator", "quote", "claim", "strength", "field"
]
ALLOWED_STRENGTH = {"established", "suggestive", "disputed", "not_reported"}

# 强度 -> 措辞（写作规则；脚本仅用于校验，不生成正文）。
STRENGTH_VERBS = {
    "established": "表明/证实",
    "suggestive": "提示/初步表明",
    "disputed": "存在争议/结论不一致",
    "not_reported": "未见报告",
}

# 降级/局限类型（本版强调：局限与不确定性显式化）。
LIMITATION_KINDS = {
    "abstract_only", "no_full_text", "metadata_incomplete", "source_conflict",
    "tool_missing", "encoding_fallback", "out_of_scope", "duplicate_removed",
    "extraction_failed", "contract_missing", "size_skipped",
}


def now_ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    print("[lit-review] " + msg, flush=True)


def read_text(path):
    """按 utf-8-sig / utf-8 / latin-1 容错读取；返回 (text, encoding, error)。"""
    last = "未知错误"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read(), enc, None
        except UnicodeDecodeError as e:
            last = "编码失败(%s): %s" % (enc, e)
        except OSError as e:
            return None, None, str(e)
    return None, None, last


def read_json(path):
    """返回 (data, errors)。errors 为 list，空表示成功。"""
    text, _enc, err = read_text(path)
    if text is None:
        return None, ["无法读取 %s: %s" % (os.path.basename(path), err or "未知错误")]
    try:
        return json.loads(text), []
    except (ValueError, TypeError) as e:
        return None, ["JSON 解析失败 %s: %s" % (os.path.basename(path), e)]


def write_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def write_text(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def default_for_spec(spec):
    """按 schema/type 提示给数组字段 []、对象字段 {}、可空给 null，其余 null。"""
    if isinstance(spec, dict):
        t = spec.get("type")
        if isinstance(t, str):
            if t == "array":
                return []
            if t == "object":
                return {}
            return None
        if isinstance(t, list):
            tl = [str(x).lower() for x in t]
            if "null" in tl:
                return None
            if "array" in tl:
                return []
            if "object" in tl:
                return {}
            return None
        if "items" in spec:
            return []
        if "properties" in spec:
            return {}
    return None


def fields_from(v):
    """把多种 output_contract 形态归一化为 [(字段名, 默认值), ...]；无法解析返回 None。"""
    if isinstance(v, dict):
        props = v.get("properties")
        if isinstance(props, dict):
            return [(name, default_for_spec(props[name])) for name in props]
        fl = v.get("fields")
        if fl is not None:
            return fields_from(fl)
        # 视作 name -> spec 的映射（跳过 schema 元键）。
        out = []
        for name, spec in v.items():
            if name in ("title", "description", "type", "$schema"):
                continue
            out.append((name, default_for_spec(spec)))
        return out
    if isinstance(v, list):
        out = []
        for item in v:
            if isinstance(item, str):
                out.append((item, None))
            elif isinstance(item, dict):
                nm = (item.get("name") or item.get("field_id") or
                      item.get("id") or item.get("key") or item.get("field"))
                if nm:
                    out.append((nm, default_for_spec(item)))
        return out
    if isinstance(v, str) and v.strip():
        return [(v, None)]
    return None


def find_output_contract(node):
    """递归查找 output_contract / output_schema；找不到返回 None。"""
    if isinstance(node, dict):
        for k, v in node.items():
            if k.lower() in ("output_contract", "outputcontract",
                             "output_schema", "outputschema"):
                return v
        for v in node.values():
            r = find_output_contract(v)
            if r is not None:
                return r
    elif isinstance(node, list):
        for item in node:
            r = find_output_contract(item)
            if r is not None:
                return r
    return None


def extract_case_id(case, case_path):
    if isinstance(case, dict):
        for k in ("case_id", "caseId", "id", "challenge_id"):
            v = case.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    base = os.path.basename(case_path or "")
    if base.lower().endswith(".json"):
        base = base[:-5]
    return base or "unknown"


def extract_question(case):
    if not isinstance(case, dict):
        return None
    for k in ("research_question", "question", "query", "prompt", "title", "objective"):
        v = case.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    t = case.get("task")
    if isinstance(t, dict):
        for k in ("research_question", "question", "prompt", "description", "title", "objective"):
            v = t.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def parse_contract(case):
    """返回 (fields, spec)。fields 为 [(name, default)]；spec 为原始 contract 或 None。"""
    spec = find_output_contract(case)
    fields = fields_from(spec)
    if fields is None:
        fields = []
    return fields, spec


def empty_uncertainty():
    return {
        "conflicts": [],
        "gaps": [],
        "limitations": [],
        "unverifiable": [],
        "not_reported": [],
    }


def build_result(case_id, fields, question, status="partial", errors=None,
                 degradations=None, sources=None, uncertainty=None,
                 change_log=None):
    """把 contract 字段 + 诚实字段合成 result 字典；契约键名与默认类型原样保留。"""
    result = {}
    for name, default in fields:
        low = name.lower()
        val = default
        if question is not None and low in ("research_question", "question",
                                             "query", "prompt", "objective"):
            val = question
        elif case_id and low in ("case_id", "caseid", "id", "challenge_id"):
            val = case_id
        result[name] = val

    # 顶层 case_id 是运行时硬要求（若与契约重名，值一致，覆盖安全）。
    result["case_id"] = case_id

    honest = {
        "status": status,
        "sources": list(sources) if sources is not None else [],
        "uncertainty": uncertainty if uncertainty is not None else empty_uncertainty(),
        "change_log": list(change_log) if change_log is not None else [],
    }
    collisions = []
    for k, v in honest.items():
        if k in result:
            collisions.append("契约字段与诚实字段重名: %s（契约值被运行时字段覆盖）" % k)
        result[k] = v

    result["pending_human"] = []
    result["errors"] = list(errors or []) + collisions
    result["degradations"] = list(degradations or [])
    result["runtime"] = {"timestamp": now_ts(), "phase": "phase0"}
    return result


def validate_evidence_row(row):
    """校验证据行；返回错误列表（空 = 合法）。"""
    errs = []
    if not isinstance(row, dict):
        return ["证据行必须是对象"]
    for f in EVIDENCE_REQUIRED_FIELDS:
        if f not in row or row.get(f) in (None, ""):
            errs.append("证据行缺字段: %s" % f)
    st = row.get("strength")
    if st not in ALLOWED_STRENGTH:
        errs.append("strength 非法: %r（允许 %s）" % (st, sorted(ALLOWED_STRENGTH)))
    return errs


def append_change_log(change_log, step, action, detail=""):
    change_log.append({
        "ts": now_ts(),
        "step": step,
        "action": action,
        "detail": detail,
    })
