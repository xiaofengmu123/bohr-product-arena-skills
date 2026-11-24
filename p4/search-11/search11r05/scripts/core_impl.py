# -*- coding: utf-8 -*-
"""core_impl.py — 专利与技术文献检索 Skill 的实现库（纯标准库，确定性运行）。

职责划分：
  1. 容错读取与输入发现（utf-8-sig / gb18030 / latin-1 逐级降级，2MB 上限）
  2. output_contract 深拷贝回填：把所有契约字段按"缺值给 null/[]/{}"镜像进
     result.json，并生成 contract_coverage（字段逐一对齐清单）
  3. change_log 纪律：任何结构变更都必须经由 record_change 追加有序条目
  4. 专利号规范化 / 抽取 / 同族归并（observed，非穷尽）
  5. 结果校验：契约对齐 + 字段合法性 + change_log 完整性，供 validate 使用

所有对外函数均不抛异常（调用方负责降级）；同一输入重复运行结果一致。
"""

import json
import os
import re
from datetime import datetime

MAX_TEXT_BYTES = 2 * 1024 * 1024  # 2MB 单文件上限
ALLOWED_STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

# 常见 ST.3 国家/地区代码（覆盖主要专利局，仅作规范化时的国别识别）
COUNTRY_CODES = {
    "US", "EP", "WO", "CN", "JP", "KR", "DE", "FR", "GB", "CA", "AU", "IN",
    "RU", "TW", "HK", "SG", "IL", "IT", "ES", "NL", "SE", "CH", "AT", "BE",
    "DK", "FI", "NO", "PT", "GR", "IE", "LU", "MC", "BR", "MX", "AR", "CL",
    "ZA", "NZ", "MY", "ID", "TH", "VN", "PH", "PL", "CZ", "HU", "RO", "BG",
    "SK", "SI", "HR", "RS", "UA", "TR", "SA", "AE", "EG", "MA",
}

# 文献种类代码首字母 -> 号码类别（A=公开/申请公开，B/U/Y/S=授权）
KIND_LETTERS = "ABCUYSMPHTDEG"
_APPLICATION_KINDS = "AH"     # 简化：A 类多为公开（含申请公开），H 为更正
_GRANT_KINDS = "BUYSC"        # B=授权公开, U/Y=实用新型授权, S=外观/设计, C=授权后更正

TEXT_EXT = {".md", ".txt", ".json", ".csv", ".tsv", ".xml", ".html", ".yaml", ".yml"}
TABLE_EXT = {".csv", ".tsv"}
PDF_EXT = {".pdf"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".webp", ".bmp"}

_MISSING = object()


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_iso(s):
    if not s:
        raise ValueError("empty timestamp")
    s = str(s).replace("Z", "+00:00")
    return datetime.fromisoformat(s)


def elapsed_seconds(start, end):
    try:
        d = _parse_iso(end) - _parse_iso(start)
        return round(max(0.0, d.total_seconds()), 1)
    except Exception:
        return None


def deep_copy(obj):
    try:
        return json.loads(json.dumps(obj, ensure_ascii=False))
    except Exception:
        return obj


# ----------------------------------------------------------------------------
# 1. 读取与输入发现
# ----------------------------------------------------------------------------
def read_text(path, max_bytes=MAX_TEXT_BYTES):
    """容错读取文本：utf-8-sig -> utf-8 -> gb18030 -> latin-1 逐级降级。"""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return "", None, {"error": "stat_failed", "reason": str(exc)}
    if size > max_bytes:
        return "", None, {"error": "size_exceeded", "size": size, "limit": max_bytes}
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return "", None, {"error": "read_failed", "reason": str(exc)}
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), enc, None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8(replace)", \
        {"error": "encoding_fallback", "reason": "all decodings failed; used replacement"}


def load_json(path):
    text, _enc, err = read_text(path)
    if err and not text:
        return None, err
    try:
        return json.loads(text), None
    except Exception as exc:
        return None, {"error": "bad_json", "reason": str(exc), "path": path}


def _classify_file(name):
    ext = os.path.splitext(name)[1].lower()
    if name == "case.json" or name.endswith(".case.json"):
        return "case"
    if ext in PDF_EXT:
        return "pdf"
    if ext in IMG_EXT:
        return "image"
    if ext in TABLE_EXT:
        return "table"
    if ext in TEXT_EXT or name in ("task.md", "notes.md", "README.md", "materials.md"):
        return "text"
    return "other"


def discover_input(input_dir):
    files = []
    if not input_dir or not os.path.isdir(input_dir):
        return files
    for root, _dirs, names in os.walk(input_dir):
        for name in names:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir).replace("\\", "/")
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            files.append({
                "rel": rel, "path": full, "name": name,
                "kind": _classify_file(name),
                "size": size,
                "ext": os.path.splitext(name)[1].lower(),
            })
    files.sort(key=lambda f: (0 if f["kind"] == "case" else 1, f["rel"]))
    return files


# ----------------------------------------------------------------------------
# 2. output_contract 镜像 + 字段对齐清单（本版重点：契约完整性）
# ----------------------------------------------------------------------------
def resolve_default(node):
    """按契约节点回填缺省：scalar -> null，array -> []，object -> {}（递归）。

    同时兼容两种契约写法：
      * 形状式：{"case_id": "", "patent_records": []}
      * schema 式：{"case_id": {"type": "string"}, "patent_records": {"type": "array"}}
    """
    if isinstance(node, list):
        return []
    if isinstance(node, dict):
        if "type" in node:
            t = node.get("type")
            if t == "array":
                return []
            if t == "object":
                props = node.get("properties")
                out = {}
                if isinstance(props, dict):
                    for k, v in props.items():
                        out[k] = resolve_default(v)
                return out
            return None
        return {k: resolve_default(v) for k, v in node.items()}
    return None


def flatten_contract(node, prefix=""):
    """把契约展平为字段清单 [{path, type, required}]，供 contract_coverage 使用。"""
    out = []
    if isinstance(node, dict):
        if "type" in node:
            t = node.get("type")
            path = prefix or "."
            out.append({"path": path, "type": t,
                        "required": bool(node.get("required", True))})
            if t == "object" and isinstance(node.get("properties"), dict):
                for k, v in node["properties"].items():
                    nxt = prefix + "." + k if prefix else k
                    out.extend(flatten_contract(v, nxt))
            if t == "array" and isinstance(node.get("items"), dict):
                nxt = (prefix or "items") + "[]"
                out.extend(flatten_contract(node["items"], nxt))
        else:
            if prefix:
                out.append({"path": prefix, "type": "object", "required": True})
            for k, v in node.items():
                nxt = prefix + "." + k if prefix else k
                out.extend(flatten_contract(v, nxt))
    elif isinstance(node, list):
        out.append({"path": (prefix or "items") + "[]", "type": "array",
                    "required": True})
    else:
        t = "string"
        if isinstance(node, bool):
            t = "boolean"
        elif isinstance(node, int):
            t = "integer"
        elif isinstance(node, float):
            t = "number"
        elif node is None:
            t = "null"
        out.append({"path": prefix or ".", "type": t, "required": True})
    return out


def _navigate(obj, path):
    """沿点路径 / [] 导航，返回 [(reached, value), ...]；[] 对列表逐项展开。"""
    if path in ("", ".", "$"):
        return [(True, obj)]
    tokens = []
    for part in path.split("."):
        if not part:
            continue
        if part.endswith("[]"):
            tokens.append(part[:-2])
            tokens.append("[]")
        else:
            tokens.append(part)
    results = [(True, obj)]
    for tok in tokens:
        nxt = []
        for ok, cur in results:
            if not ok:
                continue
            if tok == "[]":
                if isinstance(cur, list):
                    for item in cur:
                        nxt.append((True, item))
                else:
                    nxt.append((False, None))
            else:
                if isinstance(cur, dict) and tok in cur:
                    nxt.append((True, cur[tok]))
                else:
                    nxt.append((False, None))
        results = nxt
        if not results:
            break
    return results


def _is_filled(value):
    if value is None:
        return False
    if isinstance(value, (list, dict, str)) and len(value) == 0:
        return False
    return True


def _type_matches(value, t):
    if t in ("string", "str"):
        return isinstance(value, str)
    if t in ("number", "float"):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t in ("integer", "int"):
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "array":
        return isinstance(value, list)
    if t == "object":
        return isinstance(value, dict)
    if t == "null":
        return value is None
    return True


def refresh_contract_coverage(result, contract=None):
    """重算 contract_coverage：逐个字段回填 filled / value_source / 计数。"""
    cov = result.get("contract_coverage") or {}
    if not isinstance(contract, dict):
        cov.update({"contract_source": "none", "total_fields": 0,
                    "filled": 0, "missing": 0, "fields": []})
        result["contract_coverage"] = cov
        return result
    fields = flatten_contract(contract)
    filled = missing = 0
    for f in fields:
        reached = _navigate(result, f["path"])
        ok_values = [v for ok, v in reached if ok]
        # 任一导航失败即视为缺失；数组空则视为未填充
        present = len(ok_values) == len(reached) and any(
            _is_filled(v) for v in ok_values)
        type_ok = all(_type_matches(v, f["type"]) for v in ok_values)
        if present:
            filled += 1
        else:
            missing += 1
        if present and type_ok:
            f["value_source"] = "derived"
            f["ok"] = True
        elif present and not type_ok:
            f["value_source"] = "derived"
            f["ok"] = False
            f["type_mismatch"] = True
        else:
            f["value_source"] = "default"
            f["ok"] = False
            f["type_mismatch"] = False
    cov.update({
        "contract_source": "case.json#output_contract",
        "total_fields": len(fields),
        "filled": filled,
        "missing": missing,
        "fields": sorted(fields, key=lambda x: x["path"]),
    })
    result["contract_coverage"] = cov
    return result


# ----------------------------------------------------------------------------
# 3. change_log 纪律
# ----------------------------------------------------------------------------
def record_change(result, action, detail, status_after=None):
    """所有结构变更的唯一入口：追加有序 change_log 条目，禁止旁路改结构。"""
    if not isinstance(result, dict):
        return result
    log = result.setdefault("change_log", [])
    if not isinstance(log, list):
        log = []
        result["change_log"] = log
    seq = len(log) + 1
    entry = {"seq": seq, "at": now_iso(), "action": action, "detail": detail}
    if status_after:
        entry["status_after"] = status_after
        result["status"] = status_after
    log.append(entry)
    return result


# ----------------------------------------------------------------------------
# 4. 骨架构建
# ----------------------------------------------------------------------------
def build_skeleton(case_obj, contract=None):
    case_id = "unknown"
    if isinstance(case_obj, dict):
        case_id = case_obj.get("case_id") or case_obj.get("caseId") \
            or case_obj.get("id") or case_id
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
        "contract_coverage": {
            "contract_source": "none", "total_fields": 0,
            "filled": 0, "missing": 0, "fields": [],
        },
        "input_inventory": [],
        "runtime": {
            "started_at": now_iso(), "finished_at": None, "elapsed_seconds": None,
        },
    }
    # 契约字段原样回填：不与核心受管字段（case_id/status/change_log/runtime 等）冲突
    if isinstance(contract, dict):
        mirrored = resolve_default(contract)
        protected = {"case_id", "status", "sources", "uncertainty", "pending_human",
                     "change_log", "runtime", "errors", "degradations", "limitations",
                     "evidence", "patent_records", "families", "legal_status",
                     "literature", "authorization", "input_inventory",
                     "contract_coverage", "research_question", "retrieval_scope"}
        for k, v in mirrored.items():
            if k in protected and k in result:
                continue  # 受管字段以 Skill 实际值优先，契约对齐在 coverage 中记录
            result[k] = v
        refresh_contract_coverage(result, contract)
    record_change(result, "scaffold",
                  "skeleton written; contract mirror source=%s" %
                  (result["contract_coverage"]["contract_source"]))
    return result


# ----------------------------------------------------------------------------
# 5. 专利号规范化
# ----------------------------------------------------------------------------
def _strip_label(s):
    pat = re.compile(
        r'(?i)\b(publication|pub\.?|patent|grant(?:ed)?|application|appl\.?|'
        r'serial|priority|prior\.?|filing)\s*(no\.?|number|num\.?|#)?\s*[:#]?\s*'
    )
    return pat.sub("", s).strip()


def normalize_patent_number(raw):
    if raw is None:
        return {"raw": None, "normalized": None, "number_type": "unknown",
                "country": None, "kind_code": None, "confidence": "low",
                "issues": ["null_input"]}
    if not isinstance(raw, str):
        raw = str(raw)
    original = raw.strip()
    if not original:
        return {"raw": original, "normalized": None, "number_type": "unknown",
                "country": None, "kind_code": None, "confidence": "low",
                "issues": ["empty_input"]}

    issues = []
    # 显式标签是申请/公开/授权的最强信号，先抽取再剥离
    low = original.lower()
    label_hint = None
    if re.search(r'\b(appl|application|serial|filing)\b', low):
        label_hint = "application"
    elif re.search(r'\b(grant|granted)\b', low):
        label_hint = "grant"
    elif re.search(r'\b(publication|pub)\b', low):
        label_hint = "publication"

    s = _strip_label(original)
    s = re.sub(r'[\s,]+', '', s).upper()
    if not s:
        return {"raw": original, "normalized": None, "number_type": "unknown",
                "country": None, "kind_code": None, "confidence": "low",
                "issues": ["empty_after_strip"]}

    # 国家代码（前 2 位大写字母）
    country = None
    m = re.match(r'^([A-Z]{2})', s)
    if m:
        code = m.group(1)
        if code in COUNTRY_CODES:
            country = code
            s = s[2:]
        # 若前缀不是已知代码，仍取前两位作为疑似国别，但降低置信度
        else:
            country = code
            s = s[2:]
            issues.append("country_code_unrecognized")

    # 尾部位种类代码（如 A1/B2/U 等），需防止误吞数字前的字母
    kind_code = None
    m = re.search(r'([A-Z])(\d?)$', s)
    if m and m.group(1) in KIND_LETTERS and len(s) > 2:
        kind_code = m.group(1) + (m.group(2) or "")
        s = s[:m.start()]

    core = s
    has_slash = "/" in core
    has_dot = "." in core
    digits = re.sub(r'[^0-9]', '', core)
    nd = len(digits)

    # 类别判定（按证据强度分级）
    number_type = "unknown"
    confidence = "low"
    if label_hint:
        number_type = label_hint
        confidence = "high"
    elif kind_code:
        first = kind_code[0]
        if first in _GRANT_KINDS:
            number_type = "grant"
            confidence = "high"
        elif first in _APPLICATION_KINDS or first == "A":
            number_type = "publication"
            confidence = "medium"
            issues.append("kind_A_may_be_application_or_publication")
    elif has_slash:
        if country == "WO":
            # WO 2020/123456：斜杠是年份/序号分隔，不是申请号标记
            number_type = "publication"
            confidence = "medium"
            issues.append("wo_slash_is_year_separator")
        else:
            number_type = "application"
            confidence = "high"
    elif has_dot:
        number_type = "application"
        confidence = "medium"
        issues.append("dot_style_inferred_application")
    elif country in ("WO", "EP", "CN", "IN", "KR", "JP") and nd >= 8:
        number_type = "publication"
        confidence = "medium"
    elif country == "US" and nd <= 8:
        number_type = "grant"
        confidence = "medium"
        issues.append("publication_vs_grant_ambiguous")
    else:
        number_type = "publication"
        issues.append("number_type_uncertain")

    # 规范化形：国别 + 数字主体（去分隔符）+ 种类代码
    body = re.sub(r'[^0-9A-Z]', '', core)
    normalized = (country or "") + body + (kind_code or "")

    if normalized and re.fullmatch(r'[A-Z]{2}[0-9A-Z]{4,16}', normalized) is None:
        issues.append("normalized_shape_irregular")

    return {
        "raw": original,
        "normalized": normalized or None,
        "number_type": number_type,
        "country": country,
        "kind_code": kind_code,
        "confidence": confidence,
        "issues": issues,
    }


# ----------------------------------------------------------------------------
# 6. 号码抽取
# ----------------------------------------------------------------------------
_EXTRACT_PAT = re.compile(
    r'\b([A-Z]{2})[\s.,/:;-]*'
    r'([0-9](?:[0-9.,/ -]*[0-9])?)'
    r'\s*([A-Z][0-9]?)?'
    r'(?=\s|[.,;:)\]}]|$)'
)


def extract_numbers(text):
    hits = []
    if not isinstance(text, str) or not text:
        return hits
    seen = set()
    for m in _EXTRACT_PAT.finditer(text):
        raw = m.group(0).strip()
        norm = normalize_patent_number(raw)
        key = norm.get("normalized")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        ctx_s = max(0, m.start() - 40)
        ctx_e = min(len(text), m.end() + 40)
        hits.append({
            "raw": raw,
            "normalized": norm.get("normalized"),
            "number_type": norm.get("number_type"),
            "country": norm.get("country"),
            "kind_code": norm.get("kind_code"),
            "confidence": norm.get("confidence"),
            "span": [m.start(), m.end()],
            "context": text[ctx_s:ctx_e],
        })
    return hits


# ----------------------------------------------------------------------------
# 7. 同族归并（observed，非穷尽）
# ----------------------------------------------------------------------------
def _rec_id(record):
    if not isinstance(record, dict):
        return None
    return (record.get("record_id") or record.get("publication_number_normalized")
            or record.get("normalized") or record.get("id"))


def _get_field(record, *names):
    for n in names:
        v = record.get(n)
        if isinstance(v, dict):
            v = v.get("normalized") or v.get("raw")
        if v:
            return v
    return None


def group_families(records):
    if not isinstance(records, list):
        records = [records]
    by_priority = {}
    by_application = {}
    for r in records:
        if not isinstance(r, dict):
            continue
        rid = _rec_id(r)
        if not rid:
            continue
        prio = _get_field(r, "priority_number", "priority")
        app = _get_field(r, "application_number", "application")
        if prio:
            by_priority.setdefault(prio, []).append(rid)
        if app:
            by_application.setdefault(app, []).append(rid)

    families = []
    used = set()
    for key in sorted(by_priority):
        members = sorted(set(by_priority[key]))
        if len(members) > 1:
            families.append({"family_type": "simple", "key": key, "key_type": "priority",
                             "members": members, "basis": "shared_priority_number"})
        else:
            families.append({"family_type": "observed_singleton", "key": key,
                             "key_type": "priority", "members": members,
                             "basis": "priority_no_other_member_in_corpus"})
        used.update(members)
    for key in sorted(by_application):
        members = sorted(set(by_application[key]))
        if all(m in used for m in members) and members:
            continue
        families.append({"family_type": "observed", "key": key,
                         "key_type": "application", "members": members,
                         "basis": "shared_application_number"})
        used.update(members)

    all_ids = {_rec_id(r) for r in records if isinstance(r, dict) and _rec_id(r)}
    for rid in sorted(all_ids - used):
        families.append({"family_type": "observed_singleton", "key": rid,
                         "key_type": "record_id", "members": [rid],
                         "basis": "no_priority_or_application_in_corpus"})
    families.sort(key=lambda f: (f.get("family_type", ""), f.get("key") or ""))
    return families


# ----------------------------------------------------------------------------
# 8. 校验
# ----------------------------------------------------------------------------
def validate_result(result, contract=None):
    """返回 (ok, issues)；issues 为 [{item, level, detail}]。"""
    issues = []
    if not isinstance(result, dict):
        return False, [{"item": "result_not_object", "level": "error",
                        "detail": "result.json 不是 JSON 对象"}]
    if not result.get("case_id"):
        issues.append({"item": "case_id_missing", "level": "error",
                       "detail": "case_id 为空"})
    if result.get("status") not in ALLOWED_STATUSES:
        issues.append({"item": "status_invalid", "level": "error",
                       "detail": "status=%r 不在 %s" % (result.get("status"),
                                                       ALLOWED_STATUSES)})
    for key in ("sources", "uncertainty", "pending_human", "errors",
                "degradations", "limitations", "change_log"):
        if not isinstance(result.get(key), list):
            issues.append({"item": key + "_not_list", "level": "error",
                           "detail": "%s 应为数组" % key})
    rt = result.get("runtime")
    if not isinstance(rt, dict) or not rt.get("started_at"):
        issues.append({"item": "runtime_incomplete", "level": "error",
                       "detail": "runtime 缺少 started_at"})
    # change_log 有序性
    log = result.get("change_log") or []
    prev_seq = 0
    for i, e in enumerate(log):
        if not isinstance(e, dict):
            issues.append({"item": "change_log_bad_entry", "level": "error",
                           "detail": "change_log[%d] 不是对象" % i})
            break
        if not e.get("at") or not e.get("action"):
            issues.append({"item": "change_log_incomplete_entry", "level": "warn",
                           "detail": "change_log[%d] 缺 at/action" % i})
        seq = e.get("seq")
        if isinstance(seq, int):
            if seq != prev_seq + 1:
                issues.append({"item": "change_log_seq_gap", "level": "warn",
                               "detail": "change_log seq 不连续：%d -> %d" % (prev_seq, seq)})
            prev_seq = seq
    # 契约对齐
    if isinstance(contract, dict):
        diff = diff_contract(contract, result)
        for d in diff:
            issues.append({"item": "contract_" + d.get("problem", "issue"),
                           "level": "error", "detail": "%s: %s" % (d.get("path"),
                                                                    d.get("problem"))})
    else:
        cov = result.get("contract_coverage") or {}
        if cov.get("contract_source") == "none":
            issues.append({"item": "contract_absent", "level": "info",
                           "detail": "case.json 未提供 output_contract，使用默认 schema"})
    ok = not any(i["level"] == "error" for i in issues)
    return ok, issues


def diff_contract(contract, result, path="$", issues=None):
    """递归核对契约字段是否全部存在于 result.json 且类型一致。"""
    if issues is None:
        issues = []
    if isinstance(contract, dict):
        if "type" in contract:
            t = contract["type"]
            actual = result
            if t == "array":
                if not isinstance(actual, list):
                    issues.append({"path": path, "problem": "type_mismatch",
                                   "expected": "array"})
            elif t == "object":
                if not isinstance(actual, dict):
                    issues.append({"path": path, "problem": "type_mismatch",
                                   "expected": "object"})
                else:
                    for k, v in (contract.get("properties") or {}).items():
                        child = actual.get(k, _MISSING)
                        if child is _MISSING:
                            issues.append({"path": path + "." + k, "problem": "missing"})
                        else:
                            diff_contract(v, child, path + "." + k, issues)
            else:
                if actual is not None and not _type_matches(actual, t):
                    issues.append({"path": path, "problem": "type_mismatch",
                                   "expected": t})
        else:
            if not isinstance(result, dict):
                issues.append({"path": path, "problem": "expected_object"})
                return issues
            for k, v in contract.items():
                child = result.get(k, _MISSING)
                if child is _MISSING:
                    issues.append({"path": path + "." + k, "problem": "missing"})
                else:
                    diff_contract(v, child, path + "." + k, issues)
    elif isinstance(contract, list):
        if not isinstance(result, list):
            issues.append({"path": path, "problem": "type_mismatch", "expected": "array"})
    return issues


# ----------------------------------------------------------------------------
# 9. report.md 生成
# ----------------------------------------------------------------------------
def render_report(result):
    if not isinstance(result, dict):
        return "# 专利与技术文献检索报告\n\n（result.json 缺失或非法）\n"
    lines = ["# 专利与技术文献检索报告", "",
             "- case_id: `%s`" % (result.get("case_id") or "unknown"),
             "- status: `%s`" % result.get("status"),
             "- 生成时间: %s" % now_iso(), ""]
    cov = result.get("contract_coverage") or {}
    if cov.get("contract_source") and cov.get("contract_source") != "none":
        lines += ["## 输出契约对齐", "",
                  "- 契约来源: `%s`" % cov.get("contract_source"),
                  "- 契约字段总数: %s，已填充: %s，缺失: %s" %
                  (cov.get("total_fields"), cov.get("filled"), cov.get("missing")), ""]
    for section, title in (("patent_records", "目标专利记录"),
                           ("families", "同族归并"),
                           ("legal_status", "法律状态"),
                           ("literature", "技术文献"),
                           ("evidence", "证据溯源"),
                           ("sources", "检索与核验来源"),
                           ("uncertainty", "不确定性"),
                           ("pending_human", "待人工复核"),
                           ("degradations", "降级说明"),
                           ("limitations", "限制")) :
        items = result.get(section)
        if isinstance(items, list):
            lines.append("## %s（%d 条）" % (title, len(items)))
            for it in items:
                if isinstance(it, dict):
                    brief = it.get("record_id") or it.get("title") or it.get("source") \
                        or it.get("query") or it.get("item") or it.get("reason") \
                        or it.get("detail") or str(it)[:120]
                else:
                    brief = str(it)[:120]
                lines.append("- %s" % brief)
            lines.append("")
    rt = result.get("runtime") or {}
    lines.append("## 运行信息")
    lines.append("- started_at: %s" % (rt.get("started_at") or "n/a"))
    lines.append("- finished_at: %s" % (rt.get("finished_at") or "n/a"))
    lines.append("- elapsed_seconds: %s" % (rt.get("elapsed_seconds") or "n/a"))
    lines.append("")
    return "\n".join(lines)
