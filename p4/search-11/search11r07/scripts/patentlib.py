#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patentlib.py — 专利检索 Skill 的核心工具库（纯标准库、确定性、容错优先）。

设计原则：
1. 只依赖 Python 标准库；同一输入重复运行，除时间戳外的分析字段结果一致。
2. 所有读取与解析都容错：坏编码、坏 JSON、超大文件、缺字段、缺参数都不抛异常，
   而是返回「(值, 错误说明)」并把降级信息留给上层记录，绝不崩溃。
3. 专利号规范化与同族归并只做可复核的机械规则，不推断官方登记口径；
   无法判定的口径显式标注 confidence 与 uncertainty，而不是硬猜。
4. 本模块不联网、不访问受限库；实际检索由 Agent 使用授权的检索工具完成，
   脚本只负责「发现输入 → 规范化 → 归并 → 校验 → 落盘」这条确定性链。
"""

import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone

# 单文本文件读取上限（字节）。超过则只读前 N 字节并记录截断，避免内存失控。
MAX_TEXT_BYTES = 2 * 1024 * 1024

# 允许的顶层 status 取值（诚实状态机）。
STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")

# 常见专利局/组织的两字母代码（用于号码识别，避免把 No./Dr. 之类误判为号码）。
AUTHORITIES = frozenset({
    "US", "EP", "WO", "CN", "JP", "KR", "DE", "GB", "FR", "CA", "AU", "IN",
    "RU", "TW", "CH", "NL", "SE", "IT", "ES", "AT", "BE", "DK", "FI", "IE",
    "LU", "MC", "PT", "GR", "MX", "BR", "SG", "HK", "IL", "NO", "PL", "CZ",
    "HU", "TR", "ZA", "AR",
})

# 类别码（WIPO ST.16 常见取值）。kind code 决定公开号/授权号等口径。
KIND_CODES = ("A1", "A2", "A9", "B1", "B2", "C1", "C2", "U", "U1", "Y", "S")


def now_iso():
    """UTC 时间戳，ISO 8601 带 Z 后缀。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_diff(start, end):
    """求两个 ISO 时间戳的秒差；解析失败返回 0.0。"""
    try:
        s0 = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        s1 = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        return max(0.0, (s1 - s0).total_seconds())
    except (ValueError, TypeError):
        return 0.0


# --------------------------------------------------------------------------- #
# 容错读取
# --------------------------------------------------------------------------- #

def read_text(path, max_bytes=MAX_TEXT_BYTES):
    """容错读取文本文件。

    返回 (text, encoding, error) 三元组；绝不抛异常。解码顺序：
    utf-8-sig → utf-8 →（有 BOM 时）utf-16 → latin-1（兜底永不失败）。
    超过 max_bytes 时只读前 max_bytes 字节并记录 truncated。
    """
    enc = None
    err = None
    try:
        with open(path, "rb") as fh:
            raw = fh.read(max_bytes + 1)
    except OSError as e:
        return "", None, {"error": "read_failed", "reason": str(e), "truncated": False}

    truncated = len(raw) > max_bytes
    if truncated:
        raw = raw[:max_bytes]
        err = {"error": "file_too_large", "truncated": True, "limit_bytes": max_bytes}

    text = None
    for candidate in ("utf-8-sig", "utf-8"):
        try:
            text = raw.decode(candidate)
            enc = candidate
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    if text is None and raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            text = raw.decode("utf-16")
            enc = "utf-16"
        except (UnicodeDecodeError, UnicodeError):
            text = None
    if text is None:
        text = raw.decode("latin-1", errors="replace")
        enc = "latin-1"
        if err is None:
            err = {"error": "encoding_fallback", "reason": "decoded as latin-1"}
        else:
            err.setdefault("error", "encoding_fallback")
    return text, enc, err


def load_json(path, max_bytes=MAX_TEXT_BYTES):
    """容错读取 JSON。返回 (obj, error_or_None)。

    - 处理 BOM、首尾空白、尾随多余内容（raw_decode 取首个合法对象）。
    - 超大文件截断导致解析失败时，error 中标记 truncated。
    """
    text, _enc, err = read_text(path, max_bytes)
    if err and not text:
        return None, {"error": "unreadable", "reason": str(err)}
    s = text.lstrip("\ufeff").strip()
    if not s:
        return None, {"error": "empty_file"}
    obj = None
    try:
        obj = json.loads(s)
    except ValueError as e:
        try:
            obj, _end = json.JSONDecoder().raw_decode(s)
            err = {"error": "trailing_content_ignored", "reason": str(e)}
        except ValueError:
            return None, {
                "error": "invalid_json",
                "reason": str(e),
                "truncated": bool(err and err.get("truncated")),
            }
    if err and err.get("truncated"):
        return obj, {"error": "json_truncated", "reason": "file exceeded limit; parsed partial prefix"}
    return obj, None


def safe_write_json(path, obj):
    """原子写 JSON：先写临时文件再替换，避免留下半截产物。返回 (ok, err)。"""
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True, None
    except OSError as e:
        return False, {"error": "write_failed", "reason": str(e)}


# --------------------------------------------------------------------------- #
# 输入发现
# --------------------------------------------------------------------------- #

_EXT_KIND = {
    ".md": "text", ".txt": "text", ".rst": "text",
    ".json": "json",
    ".pdf": "pdf",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".tif": "image",
    ".tiff": "image", ".gif": "image", ".bmp": "image",
    ".csv": "table", ".tsv": "table",
    ".xlsx": "office", ".docx": "office", ".pptx": "office",
}


def _classify(name, path):
    low = name.lower()
    ext = os.path.splitext(low)[1]
    if low == "case.json":
        return "case"
    return _EXT_KIND.get(ext, "other")


def discover_input(input_dir):
    """递归扫描输入目录，返回文件清单（跳过符号链接，按路径排序确定性输出）。"""
    files = []
    if not input_dir or not os.path.isdir(input_dir):
        return files
    for root, dirs, names in os.walk(input_dir):
        dirs.sort()
        for name in sorted(names):
            p = os.path.join(root, name)
            try:
                if os.path.islink(p):
                    continue
                st = os.stat(p)
            except OSError:
                continue
            rel = os.path.relpath(p, input_dir).replace(os.sep, "/")
            files.append({
                "rel": rel,
                "path": p,
                "kind": _classify(name, p),
                "size": st.st_size,
            })
    return files


# --------------------------------------------------------------------------- #
# 骨架与契约回填
# --------------------------------------------------------------------------- #

def _contract_default(val):
    """回填 output_contract 键的缺省值：列表→[]，字典→{}，其余→null。"""
    if isinstance(val, list):
        return []
    if isinstance(val, dict):
        return {}
    return None


def build_skeleton(case_obj, contract=None):
    """构造最小合法 result.json 骨架（status=partial）。

    - case_id 优先取 case_obj.case_id / id / caseId，缺则 "unknown" 并写 uncertainty。
    - 若提供 output_contract，其全部键原样回填（不改名、不删键、不覆盖已存在主键）。
    """
    case_id = None
    if isinstance(case_obj, dict):
        case_id = case_obj.get("case_id") or case_obj.get("id") or case_obj.get("caseId")
    if not isinstance(case_id, str) or not case_id.strip():
        case_id = "unknown"

    result = {}
    result["case_id"] = case_id
    result["status"] = "partial"
    result["research_question"] = None
    result["retrieval_scope"] = None
    result["patent_records"] = []
    result["families"] = []
    result["legal_status"] = []
    result["literature"] = []
    result["authorization"] = []
    result["evidence"] = []
    result["sources"] = []
    result["uncertainty"] = []
    result["pending_human"] = []
    result["errors"] = []
    result["degradations"] = []
    result["limitations"] = []
    result["change_log"] = []
    result["runtime"] = {"started_at": now_iso()}

    if isinstance(contract, dict):
        for key, val in contract.items():
            if key in result:
                continue  # 主键不覆盖，交由 Agent 真实填充
            result[key] = _contract_default(val)
    else:
        result["output_contract"] = None

    result["change_log"].append({
        "at": now_iso(), "action": "scaffold",
        "detail": "initial partial skeleton; status=partial",
    })
    if case_id == "unknown":
        result["uncertainty"].append({
            "item": "case_id_unknown",
            "reason": "case.json missing or has no case_id/id key",
        })
    return result


def append_change(result, action, detail=""):
    """向 change_log 追加一条变更记录。"""
    entry = {"at": now_iso(), "action": action}
    if detail:
        entry["detail"] = detail
    result.setdefault("change_log", []).append(entry)
    return result


def finalize_result(result, status="ok"):
    """收尾：补 runtime 时间与耗时、写入 status、追加 change_log。"""
    rt = result.setdefault("runtime", {})
    started = rt.get("started_at") or now_iso()
    rt["started_at"] = started
    rt["finished_at"] = now_iso()
    rt["elapsed_seconds"] = round(_iso_diff(started, rt["finished_at"]), 1)
    if status in STATUS_VALUES:
        result["status"] = status
    append_change(result, "finalize", "finalized with status=%s" % result["status"])
    return result


# --------------------------------------------------------------------------- #
# 专利号规范化
# --------------------------------------------------------------------------- #

def _strip_prefix(s):
    """去除常见前缀词（publication no / application no / priority / serial 等）。"""
    pat = re.compile(
        r'^(patent|publication|publication\s+no|publication\s+number|'
        r'application|application\s+no|application\s+number|'
        r'priority|priority\s+no|priority\s+number|serial|serial\s+no|'
        r'grant|grant\s+no|patent\s+no)\s*[:#.\-]?\s*',
        re.IGNORECASE,
    )
    return pat.sub("", s).strip()


def _clean_digits(num):
    """去除分隔符（空格/逗号/点/连字符），保留数字与斜杠（WO 年份与美申号）。"""
    return re.sub(r"[\s,.\-]", "", num)


def _digits_only(num):
    return re.sub(r"\D", "", num)


def _kind_to_type(kind):
    if not kind:
        return None
    k = kind[0].upper()
    if k in ("B", "C"):
        return "grant"
    if k == "A":
        return "publication"
    if k in ("U", "Y"):
        return "utility_model"
    if k == "S":
        return "design"
    return "unknown"


def normalize_patent_number(raw):
    """规范化单个专利号。

    返回 dict：raw / normalized / authority / kind_code / number_type / valid /
    confidence / notes。number_type ∈ publication|application|priority|grant|
    utility_model|design|publication_or_grant|unknown；无法判定时标注低置信与原因。
    """
    out = {
        "raw": raw,
        "normalized": None,
        "authority": None,
        "kind_code": None,
        "number_type": "unknown",
        "valid": False,
        "confidence": "low",
        "notes": [],
    }
    if not isinstance(raw, str):
        out["notes"].append("input_not_string")
        return out

    s = unicodedata.normalize("NFKC", raw).replace("\u00a0", " ").strip()
    s = _strip_prefix(s)
    if not s:
        out["notes"].append("empty_after_prefix_strip")
        return out

    m = re.match(
        r'^(?P<code>[A-Z]{2})(?P<sep>[\s:：.\-]*)(?P<num>[0-9][0-9\s,.\/\-]{2,})'
        r'(?:\s*(?P<kind>[ABCUYST]\d?))?$',
        s,
        re.IGNORECASE,
    )
    if not m:
        # 无两字母局码：保留原始串，标注无法规范化
        out["notes"].append("no_authority_code")
        return out

    code = m.group("code").upper()
    if code not in AUTHORITIES:
        out["notes"].append("unrecognized_authority:%s" % code)
        return out

    num_raw = m.group("num").strip()
    kind = (m.group("kind") or "").upper() or None
    if kind and kind not in KIND_CODES:
        # 保留但不强判；例如 OCR 粘连出的伪类别码
        out["notes"].append("unusual_kind_code:%s" % kind)

    digits = _digits_only(num_raw)
    has_slash = "/" in num_raw
    has_dot = "." in num_raw
    compact = _clean_digits(num_raw)

    out["authority"] = code
    out["kind_code"] = kind
    out["normalized"] = code + compact + (kind or "")

    if len(digits) < 4:
        out["notes"].append("too_few_digits")
        return out

    out["valid"] = True
    out["confidence"] = "high" if kind else "medium"

    # number_type 判定
    if kind:
        t = _kind_to_type(kind)
        out["number_type"] = t or "unknown"
        return out

    # 无类别码时的启发式
    if code == "WO":
        out["number_type"] = "publication"  # WO 公开号形式 WO YYYY/NNNNNN
        out["confidence"] = "medium"
        out["notes"].append("wo_publication_heuristic")
    elif code == "US" and has_slash:
        first = num_raw.split("/", 1)[0]
        if first in ("61", "62"):
            out["number_type"] = "priority"
            out["notes"].append("us_provisional_priority_heuristic")
        else:
            out["number_type"] = "application"
            out["notes"].append("us_application_heuristic")
        out["confidence"] = "medium"
    elif code == "EP" and has_dot:
        out["number_type"] = "application"
        out["confidence"] = "medium"
        out["notes"].append("ep_application_heuristic")
    else:
        out["number_type"] = "publication_or_grant"
        out["confidence"] = "low"
        out["notes"].append("kind_code_missing; publication vs grant ambiguous")
    return out


# --------------------------------------------------------------------------- #
# 号码抽取
# --------------------------------------------------------------------------- #

_NUM_RE = re.compile(
    r'(?P<code>[A-Z]{2})\s*'
    r'(?P<num>[0-9][0-9\s,.\/\-]{3,})'
    r'(?:\s*(?P<kind>[ABCUYST]\d?))?',
    re.IGNORECASE,
)


def extract_numbers(text):
    """从文本中抽取专利号候选（含 OCR 粘连边界）。返回去重后的候选列表。"""
    if not isinstance(text, str):
        return []
    s = unicodedata.normalize("NFKC", text)
    hits = []
    seen = set()
    for m in _NUM_RE.finditer(s):
        code = m.group("code").upper()
        if code not in AUTHORITIES:
            continue
        raw = m.group(0).strip()
        norm = normalize_patent_number(raw)
        key = norm.get("normalized") or ("raw:" + raw.upper())
        if key in seen:
            continue
        seen.add(key)
        start, end = m.start(), m.end()
        ctx = s[max(0, start - 40):min(len(s), end + 40)]
        hits.append({
            "candidate": raw,
            "normalized": norm,
            "start": start,
            "end": end,
            "context": ctx,
            "confidence": norm.get("confidence"),
        })
    return hits


# --------------------------------------------------------------------------- #
# 同族归并
# --------------------------------------------------------------------------- #

def _norm_identity(value):
    """把记录中的号码字段统一成可比身份键。"""
    if not value:
        return None
    if isinstance(value, dict):
        return value.get("normalized") or value.get("raw")
    if isinstance(value, str):
        n = normalize_patent_number(value)
        return n.get("normalized") or value.strip().upper()
    return str(value).strip().upper()


def group_families(records):
    """按共用申请号/优先权号/公开号归并同族（并查集）。

    - 只在已观察到的记录内归并，故 family_type 一律带 observed、exhaustive=false。
    - simple：成员公开号与优先权号完全一致（重复记录/单一文献）。
    - extended：成员共享申请号或优先权号但公开号不同（跨局/多成员）。
    - 输出确定性：family 按最小 record_id 排序，成员按 record_id 排序。
    """
    recs = [r for r in (records or []) if isinstance(r, dict)]
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, r in enumerate(recs):
        parent.setdefault(i, i)

    # 身份键 → 记录下标列表
    by_key = {}
    def _add(key, idx):
        if not key:
            return
        by_key.setdefault(key, []).append(idx)

    for i, r in enumerate(recs):
        _add(_norm_identity(r.get("publication_number")), i)
        _add(_norm_identity(r.get("application_number")), i)
        _add(_norm_identity(r.get("priority_number")), i)
        _add(_norm_identity(r.get("grant_number")), i)

    for idxs in by_key.values():
        first = idxs[0]
        for other in idxs[1:]:
            union(first, other)

    groups = {}
    for i in range(len(recs)):
        groups.setdefault(find(i), []).append(i)

    families = []
    for root in sorted(groups):
        idxs = sorted(groups[root], key=lambda x: str(recs[x].get("record_id") or x))
        members = []
        pub_set = set()
        pri_set = set()
        for idx in idxs:
            r = recs[idx]
            pub = _norm_identity(r.get("publication_number"))
            pri = _norm_identity(r.get("priority_number"))
            if pub:
                pub_set.add(pub)
            if pri:
                pri_set.add(pri)
            members.append({
                "record_id": r.get("record_id") or ("rec-%d" % idx),
                "authority": r.get("authority"),
                "publication_number": r.get("publication_number"),
                "application_number": r.get("application_number"),
                "priority_number": r.get("priority_number"),
                "kind_code": r.get("kind_code"),
            })
        if len(pub_set) <= 1 and len(pri_set) <= 1:
            ftype = "simple"
        else:
            ftype = "extended"
        families.append({
            "family_id": "fam-%02d" % (len(families) + 1),
            "family_type": ftype,
            "observed": True,
            "exhaustive": False,
            "members": members,
            "basis": {
                "shared_publication_numbers": sorted(pub_set),
                "shared_priority_numbers": sorted(pri_set),
                "note": "grouped within observed records only; INPADOC not consulted",
            },
        })
    return {"families": families, "observed": True, "exhaustive": False}


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #

REQUIRED_KEYS = (
    "case_id", "status", "research_question", "retrieval_scope",
    "patent_records", "families", "legal_status", "literature", "authorization",
    "evidence", "sources", "uncertainty", "pending_human", "errors",
    "degradations", "limitations", "change_log", "runtime",
)


def validate_result(result):
    """校验 result.json 结构。返回 (valid, issues)。不修改 result。

    证据条目须带 locator/quote；来源条目须带 engine/query；缺失时列 issue 但不硬失败。
    """
    issues = []
    if not isinstance(result, dict):
        return False, ["result is not an object"]
    if not result.get("case_id"):
        issues.append("case_id missing or empty")
    if result.get("status") not in STATUS_VALUES:
        issues.append("status invalid: %r" % result.get("status"))
    for key in REQUIRED_KEYS:
        if key not in result:
            issues.append("missing key: %s" % key)
    for i, ev in enumerate(result.get("evidence", []) or []):
        if not isinstance(ev, dict):
            issues.append("evidence[%d] not object" % i)
            continue
        if not ev.get("locator") and not ev.get("quote"):
            issues.append("evidence[%d] missing locator/quote" % i)
    for i, s in enumerate(result.get("sources", []) or []):
        if not isinstance(s, dict):
            issues.append("sources[%d] not object" % i)
            continue
        if not s.get("engine"):
            issues.append("sources[%d] missing engine" % i)
        if not s.get("query"):
            issues.append("sources[%d] missing query" % i)
    return (len(issues) == 0), issues
