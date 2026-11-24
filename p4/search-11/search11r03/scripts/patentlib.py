# -*- coding: utf-8 -*-
"""patentlib — 专利与技术文献检索 Skill 的纯标准库工具库。

职责：
  * 容错输入发现与文本读取（utf-8-sig、2MB 上限、坏 JSON/坏编码降级）
  * 专利号规范化与号码类型判定（公开号/申请号/优先权号/授权号）
  * 从文本抽取专利号候选
  * 按共用优先权号/申请号归并同族（始终标 observed 且 exhaustive=false）
  * 骨架构建、结果校验、确定性 canonical 序列化

本文件不发起任何网络请求，只做确定性本地处理；同一输入重复运行，
除 runtime 时间戳外的分析字段结果一致（由 canonicalize() 保证排序稳定）。
"""

import json
import os
import re
import unicodedata
from datetime import datetime, timezone

MAX_TEXT_BYTES = 2 * 1024 * 1024  # 2MB

# WIPO ST.16 常见 kind code 首字母（A=申请公开, B/C=授权/更正, U=实用新型等）
KIND_LETTERS = set("ABCEFGHSTUY")

# 各国/组织号码的常见结构（用于判定号码类型，不用于外部检索）
COUNTRY_HINTS = {
    "US": "year+serial or series/serial; A=app publication, B=patent grant, 60/61/62=provisional",
    "WO": "PCT publication: year/6-7 digit serial + A1/A2/A3",
    "EP": "7-digit publication or year+6-digit + A/B",
    "CN": "application/publication 9-13 digits, suffix A/B/U",
    "JP": "application year+serial; publication year+serial + A/B",
}

STATUS_ENUM = ("ok", "partial", "insufficient_evidence", "input_error")


# --------------------------------------------------------------------------
# 容错读取
# --------------------------------------------------------------------------
def now_iso():
    """UTC ISO-8601 时间戳（运行时字段，不计入确定性比较）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_unicode(s):
    """半角化、统一空白与连字符，供规范化前预处理。"""
    if s is None:
        return ""
    s = str(s)
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u2013", "-").replace("\u2014", "-")  # en/em dash
    s = s.replace("\u00a0", " ").replace("\u2009", " ")  # nbsp/thin space
    return s


def read_text(path):
    """按 utf-8-sig 容错读取文本文件。返回 (text, encoding, error_or_None)。

    超过 2MB 跳过并返回空文本 + 错误；坏编码逐级回退到 utf-8/latin-1。
    """
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return "", None, {"error": "stat_failed", "detail": str(e)}
    if size > MAX_TEXT_BYTES:
        return "", None, {"error": "too_large", "detail": "size %d > 2MB" % size}
    last_err = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), enc, None
        except (UnicodeDecodeError, OSError) as e:
            last_err = {"error": "decode_failed", "encoding": enc, "detail": str(e)}
            continue
    return "", None, last_err


def load_json(path):
    """容错读 JSON，返回 (obj, err)。坏 JSON 返回 (None, err) 不抛异常。"""
    text, _enc, rerr = read_text(path)
    if rerr:
        return None, rerr
    try:
        return json.loads(text), None
    except ValueError as e:
        return None, {"error": "bad_json", "detail": str(e)}


def _classify(path):
    """按扩展名与文件名给输入文件分类（只做本地判断，不假设平台工具）。"""
    base = os.path.basename(path).lower()
    ext = os.path.splitext(base)[1].lower()
    if base == "case.json":
        return "case"
    if ext == ".json":
        return "json"
    if ext in (".md", ".txt", ".rst"):
        return "text"
    if ext in (".csv", ".tsv"):
        return "table"
    if ext == ".pdf":
        return "pdf"
    if ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".bmp"):
        return "image"
    if base in ("notes.md", "task.md", "materials"):
        return "text"
    return "other"


def discover_input(input_dir):
    """扫描输入目录，返回文件清单（相对路径、分类、字节数）。不递归进入符号链接。"""
    files = []
    if not input_dir or not os.path.isdir(input_dir):
        return files
    try:
        names = sorted(os.listdir(input_dir))
    except OSError:
        return files
    for name in names:
        full = os.path.join(input_dir, name)
        if os.path.islink(full):
            continue
        if os.path.isdir(full):
            # 一级子目录（如 materials/）内的文本/表格也纳入
            try:
                sub = sorted(os.listdir(full))
            except OSError:
                continue
            for subname in sub:
                subfull = os.path.join(full, subname)
                if os.path.isfile(subfull) and not os.path.islink(subfull):
                    try:
                        size = os.path.getsize(subfull)
                    except OSError:
                        size = 0
                    files.append({
                        "rel": os.path.join(name, subname).replace("\\", "/"),
                        "path": subfull,
                        "kind": _classify(subname),
                        "size": size,
                    })
            continue
        try:
            size = os.path.getsize(full)
        except OSError:
            size = 0
        files.append({
            "rel": name,
            "path": full,
            "kind": _classify(name),
            "size": size,
        })
    return files


# --------------------------------------------------------------------------
# 专利号规范化
# --------------------------------------------------------------------------
_NUM = r"[0-9]"
_KIND_RE = re.compile(r"\s*([A-Z])(\d{0,2})$", re.IGNORECASE)


def _split_kind(core):
    """从字符串尾部剥离 kind code，返回 (剩余核心, kind_or_None)。"""
    m = _KIND_RE.search(core)
    if not m:
        return core, None
    letter = m.group(1).upper()
    if letter not in KIND_LETTERS:
        return core, None
    digit = m.group(2) or ""
    # 避免把纯数字结尾误判为 kind（kind 必须紧邻且有 0-2 位数字）
    return core[: m.start()].strip(), letter + digit


def _digits_only(s):
    return re.sub(r"\D", "", s)


def normalize_patent_number(raw):
    """规范化一个专利号字符串，返回 dict（确定性）。

    字段：raw, country, number, kind, number_type, normalized, confidence, notes
    number_type ∈ {publication, application, priority, grant, unknown}
    """
    out = {
        "raw": raw if raw is not None else "",
        "country": None,
        "number": None,
        "kind": None,
        "number_type": "unknown",
        "normalized": None,
        "confidence": "low",
        "notes": [],
    }
    s = _clean_unicode(raw).strip()
    if not s:
        out["notes"].append("empty_input")
        return out
    s = re.sub(r"\s+", " ", s)

    # 1) 尾部 kind code
    core, kind = _split_kind(s)
    out["kind"] = kind

    # 2) 开头 2 字母国家/组织代码
    m = re.match(r"^([A-Za-z]{2})(?:[\s./-]+)(.*)$", core)
    if m:
        out["country"] = m.group(1).upper()
        core = m.group(2).strip()
    else:
        m2 = re.match(r"^([A-Za-z]{2})$", core)
        if m2:
            out["country"] = m2.group(1).upper()
            core = ""

    body = _digits_only(core)
    if not body:
        out["notes"].append("no_numeric_body")
        return out

    # 3) 判断号码结构（先去掉分组逗号与空格，再匹配结构）
    core_clean = core.replace(" ", "").replace(",", "")
    year_serial = re.match(r"^(\d{4})[/-](\d{1,7})$", core_clean)
    series_serial = re.match(r"^(\d{1,2})/(\d{1,7})$", core_clean)
    plain = re.match(r"^\d+$", body)

    number = body
    ntype = "unknown"
    conf = "medium"
    notes = []

    if year_serial:
        year, serial = year_serial.group(1), year_serial.group(2)
        number = year + serial
        if out["country"] == "WO":
            ntype = "publication"
            conf = "high" if kind else "medium"
            notes.append("PCT publication: year/serial")
        elif out["country"] == "US":
            # 美国申请公开号：4 位年 + 7 位序号
            ntype = "publication"
            conf = "high" if kind else "medium"
            notes.append("US application publication: year+7-digit serial")
        elif out["country"] == "EP":
            ntype = "publication"
            conf = "medium"
            notes.append("EP publication: year+serial format")
        else:
            ntype = "publication"
            conf = "medium"
            notes.append("year+serial publication-like; verify against official register")
    elif series_serial:
        series = series_serial.group(1)
        number = body  # 保留系列+序号数字
        if out["country"] == "US" and series in ("60", "61", "62"):
            ntype = "priority"
            conf = "high"
            notes.append("US provisional application serial %s (commonly a priority claim)" % series)
        else:
            ntype = "application"
            conf = "high" if out["country"] == "US" else "medium"
            notes.append("application serial number (series/serial)")
    elif plain:
        if kind:
            letter = kind[0]
            if letter in ("B", "C"):
                ntype = "grant"
                conf = "high"
                notes.append("grant/publication with kind %s" % kind)
            elif letter == "A":
                ntype = "publication"
                conf = "high"
                notes.append("application publication with kind %s" % kind)
            else:
                ntype = "publication"
                conf = "medium"
                notes.append("publication with kind %s" % kind)
        else:
            # 无 kind 的纯数字：无法从本地确定性判定，标 unknown
            ntype = "unknown"
            conf = "low"
            notes.append("no kind code; ambiguous between application and publication")
    else:
        notes.append("unrecognized_structure")

    out["number"] = number
    out["number_type"] = ntype
    out["confidence"] = conf
    out["notes"] = notes
    out["normalized"] = _canonical_number(out)
    return out


def _canonical_number(o):
    parts = []
    if o["country"]:
        parts.append(o["country"])
    if o["number"]:
        parts.append(o["number"])
    if o["kind"]:
        parts.append(o["kind"])
    if not parts:
        return None
    return "".join(parts).upper()


# --------------------------------------------------------------------------
# 号码抽取
# --------------------------------------------------------------------------
_PATENT_RE = re.compile(
    r"\b([A-Z]{2})\s*([0-9][0-9,/\s\.\-]{3,30}?)\s*([A-Z][0-9]?)?\b",
    re.IGNORECASE,
)


def extract_numbers(text):
    """从文本抽取专利号候选（不保证为专利号，需逐条人工核对）。"""
    if not text:
        return []
    hits = []
    seen = set()
    for m in _PATENT_RE.finditer(text):
        country = m.group(1).upper()
        core = m.group(2)
        kind = m.group(3)
        raw = m.group(0).strip()
        norm = normalize_patent_number(raw)
        if norm["number"] is None:
            continue
        key = norm["normalized"]
        if key in seen:
            continue
        seen.add(key)
        hits.append({
            "raw": raw,
            "span": [m.start(), m.end()],
            "country": norm["country"],
            "number": norm["number"],
            "kind": norm["kind"],
            "number_type": norm["number_type"],
            "normalized": norm["normalized"],
            "confidence": norm["confidence"],
        })
    return hits


# --------------------------------------------------------------------------
# 同族归并
# --------------------------------------------------------------------------
def _record_ids(record):
    for k in ("record_id", "id"):
        if isinstance(record, dict) and record.get(k):
            return record[k]
    return None


def _numbers_of(record, key):
    """从 record 的指定键取号码列表（接受字符串或 list[dict|str]）。"""
    v = record.get(key) if isinstance(record, dict) else None
    out = []
    if isinstance(v, str):
        out.append(v)
    elif isinstance(v, list):
        for item in v:
            if isinstance(item, dict):
                for k in ("normalized", "number", "raw"):
                    if item.get(k):
                        out.append(str(item[k]))
                        break
            elif item:
                out.append(str(item))
    return out


def _norm_key(text):
    return normalize_patent_number(text)["normalized"]


def group_families(records):
    """按共用优先权号（其次申请号）归并同族。

    始终返回 exhaustive=false；family_type 依据证据强度：
      simple   — 所有成员共享同一优先权号
      extended — 经优先权号链连接（至少两跳）
      observed — 仅有观测到的一跳链接或证据不足
    """
    recs = [r for r in (records or []) if isinstance(r, dict)]
    parent = list(range(len(recs)))
    links = []  # (i, j, basis, number)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # 优先权号
    prio_index = {}
    for i, r in enumerate(recs):
        for num in _numbers_of(r, "priority_numbers") + _numbers_of(r, "priority_number"):
            key = _norm_key(num)
            if not key:
                continue
            if key in prio_index:
                links.append((prio_index[key], i, "priority", key))
                union(prio_index[key], i)
            else:
                prio_index[key] = i

    # 申请号（用于无优先权号时）
    app_index = {}
    for i, r in enumerate(recs):
        for num in _numbers_of(r, "application_numbers") + _numbers_of(r, "application_number"):
            key = _norm_key(num)
            if not key:
                continue
            if key in app_index:
                links.append((app_index[key], i, "application", key))
                union(app_index[key], i)
            else:
                app_index[key] = i

    groups = {}
    for i in range(len(recs)):
        groups.setdefault(find(i), []).append(i)

    families = []
    for members in groups.values():
        member_ids = [(_record_ids(recs[i]) or i) for i in sorted(members)]
        basis = sorted({("%s:%s" % (b, n)) for (a, _b, b, n) in links if find(a) in members})
        # family_type：全部共享同一优先权号 → simple；仅靠申请号/存在链 → extended/observed
        prio_bases = [n for (a, _b, b, n) in links if b == "priority" and find(a) in members]
        if prio_bases and len(set(prio_bases)) == 1:
            ftype = "simple"
        elif len(links) > 0 and len(member_ids) > 1 and any(
            b == "priority" for (a, _b, b, _n) in links if find(a) in members
        ):
            ftype = "extended"
        else:
            ftype = "observed"
        families.append({
            "members": member_ids,
            "family_type": ftype,
            "basis": basis,
            "exhaustive": False,
        })
    families.sort(key=lambda f: ",".join(map(str, f["members"])))
    return families


# --------------------------------------------------------------------------
# 骨架与校验
# --------------------------------------------------------------------------
def _default_for_contract_value(v):
    """按契约值类型推断缺省值。"""
    if isinstance(v, list):
        return []
    if isinstance(v, dict):
        if v.get("type") == "array":
            return []
        return {}
    return None


def build_skeleton(case_obj, contract):
    """构建初始 partial 骨架，回填 output_contract 全部键。"""
    case_obj = case_obj if isinstance(case_obj, dict) else {}
    contract = contract if isinstance(contract, dict) else {}

    case_id = (case_obj.get("case_id") or contract.get("case_id") or
               case_obj.get("id") or None)
    if not case_id:
        case_id = "unknown"

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
        "runtime": {"started_at": now_iso(), "elapsed_seconds": None, "finished_at": None},
    }
    if not case_obj.get("case_id") and case_id == "unknown":
        result["uncertainty"].append({
            "item": "case_id_missing",
            "reason": "case.json lacks case_id; set to unknown",
        })

    # 回填 output_contract 的每个键（不改名、不删未知键）
    result["_contract_keys"] = sorted(contract.keys())
    for k, v in contract.items():
        if k not in result:
            result[k] = _default_for_contract_value(v)

    result["change_log"].append({
        "at": now_iso(),
        "step": "scaffold",
        "action": "wrote initial partial skeleton",
    })
    return result


def canonicalize(obj):
    """确定性序列化（排序键、紧凑分隔符），用于同一输入两次运行的比较。"""
    def strip_runtime(o):
        if isinstance(o, dict):
            d = dict(o)
            d.pop("runtime", None)
            for k, v in list(d.items()):
                d[k] = strip_runtime(v)
            return d
        if isinstance(o, list):
            return [strip_runtime(x) for x in o]
        return o
    return json.dumps(strip_runtime(obj), ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"))


def validate(result):
    """校验 result.json。返回 (issues, ok)。issues 每项含 level/item/message。"""
    issues = []
    if not isinstance(result, dict):
        return [{"level": "error", "item": "root_not_object",
                 "message": "result.json must be a JSON object"}], False
    if not result.get("case_id"):
        issues.append({"level": "error", "item": "case_id_missing",
                       "message": "case_id must be non-empty"})
    if result.get("status") not in STATUS_ENUM:
        issues.append({"level": "error", "item": "status_invalid",
                       "message": "status must be one of %s" % (STATUS_ENUM,)})
    for key in ("sources", "uncertainty", "pending_human", "change_log", "evidence"):
        v = result.get(key)
        if v is not None and not isinstance(v, list):
            issues.append({"level": "warn", "item": "field_not_list",
                           "message": "%s should be a list" % key})
    for i, ev in enumerate(result.get("evidence", []) or []):
        if not isinstance(ev, dict) or not ev.get("locator") or not ev.get("quote"):
            issues.append({"level": "warn", "item": "evidence_incomplete",
                           "message": "evidence[%d] lacks locator/quote" % i})
    for i, rec in enumerate(result.get("patent_records", []) or []):
        if isinstance(rec, dict) and not rec.get("record_id"):
            issues.append({"level": "warn", "item": "record_id_missing",
                           "message": "patent_records[%d] lacks record_id" % i})
    ok = not any(x["level"] == "error" for x in issues)
    return issues, ok
