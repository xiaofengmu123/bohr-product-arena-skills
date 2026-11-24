#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patentlib.py — 专利与技术文献检索 Skill 的核心工具库（纯标准库）。

职责：
  容错读写、输入发现、结果骨架、专利号规范化（多解释 + 置信度）、
  专利号抽取、同族归并（含拒绝合并记录）、结果校验、报告生成。

本包变体「降级与不确定性显式化」的三条硬约束：
  1) 号码规范化不确定时返回全部候选解释，绝不静默择一；
  2) 同族只按"共用申请号/优先权号"归并，绝不按关键词/主题相似合并，
     并显式记录被拒绝的候选合并及理由；
  3) 法律状态默认 unknown，未查官方登记簿即不推断"有效"；
     每条证据带 locator + quote + strength（直接引用/推导/断言/未验证）。
"""

import json
import os
import re
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

MAX_TEXT_BYTES = 2 * 1024 * 1024  # 2MB，与输入契约一致


def now_iso():
    """UTC 时间戳（秒精度），跨机器可复现。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_text(path, max_bytes=MAX_TEXT_BYTES):
    """容错读文本：utf-8-sig 优先，坏编码降级不崩溃。

    返回 (text, encoding, err_dict_or_None)。
    """
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return "", None, {"error": "stat failed: %s" % e}
    if size > max_bytes:
        return "", None, {"error": "file exceeds %d bytes, skipped" % max_bytes}
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        return "", None, {"error": "open failed: %s" % e}
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), enc, None
        except UnicodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8(replace)", {
        "error": "undecodable bytes replaced"
    }


def load_json(path):
    """读 JSON；坏 JSON 返回 (None, err)，不抛异常。"""
    text, enc, err = read_text(path)
    if err and not text:
        return None, err
    try:
        return json.loads(text), None
    except ValueError as e:
        return None, {"error": "bad JSON: %s" % e, "encoding": enc}


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def deep_copy(x):
    return json.loads(json.dumps(x, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 输入发现
# ---------------------------------------------------------------------------

TEXT_EXTS = {".txt", ".md", ".json", ".csv", ".tsv", ".xml", ".html", ".htm",
             ".yaml", ".yml", ".log", ".rst", ".bib", ".ris", ".jsonl"}
TABLE_EXTS = {".csv", ".tsv", ".xlsx", ".xls"}
PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".bmp"}

_KIND_ORDER = {"case": 0, "json": 1, "text": 2, "table": 3,
               "pdf": 4, "image": 5, "other": 6}


def discover_input(input_dir):
    """递归扫描输入目录，不假设固定文件名/键名。"""
    files = []
    if not os.path.isdir(input_dir):
        return files
    for root, _dirs, names in os.walk(input_dir):
        for name in names:
            p = os.path.join(root, name)
            ext = os.path.splitext(name)[1].lower()
            rel = os.path.relpath(p, input_dir).replace("\\", "/")
            try:
                size = os.path.getsize(p)
            except OSError:
                size = -1
            low = name.lower()
            if low in ("case.json", "case.jsonl"):
                kind = "case"
            elif ext == ".json":
                kind = "case" if "case" in low else "json"
            elif ext in TEXT_EXTS:
                kind = "text"
            elif ext in TABLE_EXTS:
                kind = "table"
            elif ext in PDF_EXTS:
                kind = "pdf"
            elif ext in IMAGE_EXTS:
                kind = "image"
            else:
                kind = "other"
            files.append({"path": p, "rel": rel, "kind": kind,
                          "size": size, "ext": ext})
    files.sort(key=lambda f: (_KIND_ORDER.get(f["kind"], 6), f["rel"]))
    return files


# ---------------------------------------------------------------------------
# 专利号规范化：多解释 + 置信度，绝不静默择一
# ---------------------------------------------------------------------------

COUNTRY_CODES = {
    "US", "EP", "WO", "CN", "JP", "DE", "GB", "FR", "KR", "CA", "AU", "CH",
    "NL", "SE", "AT", "BE", "DK", "ES", "FI", "IE", "IT", "LU", "MC", "PT",
    "RU", "TW", "IN", "BR", "MX", "ZA", "IL", "NO", "SG", "HK", "DD", "SU",
    "GR", "HU", "PL", "RO", "TR", "UA", "VN", "TH", "MY", "PH", "ID", "AR",
    "CL", "CO", "PE", "UY", "EC", "EG", "MA", "SA", "AE", "NZ", "IS", "MT",
    "CY", "SI", "SK", "CZ", "BG", "HR", "RS", "EE", "LT", "LV",
}

KIND_RE = re.compile(r"\b([ABCUSYEPTH]\d?)\b", re.IGNORECASE)

_BRIDGE_WORD = (r"(?:Patent|Pat\.?|No\.?|Number|Num\.?|Application|Appl\.?"
                r"|Serial|Ser\.?|Publication|Pub\.?|Grant|Granted|Document|Doc\.?)")
_BRIDGE = r"(?:[.\s]*(?:%s[.\s]*)*)" % _BRIDGE_WORD

YEAR_SLASH_RE = re.compile(r"\b([A-Z]{2})%s(\d{4})/(\d{4,9})" % _BRIDGE,
                           re.IGNORECASE)
NUM_KIND_RE = re.compile(
    r"\b([A-Z]{2})%s(\d[\d,]{3,12})\s*([ABCUSYEPTH]\d?)\b" % _BRIDGE,
    re.IGNORECASE)
SERIES_SLASH_RE = re.compile(
    r"\b([A-Z]{2})%s(\d{2})/(\d[\d,]{2,9})\b" % _BRIDGE, re.IGNORECASE)

MARKER_RE = {
    "application": re.compile(r"\b(APPLICATION|APPL|SERIAL|SER\.?\s*NO|FILING|APP\.?\s*NO)\b",
                              re.IGNORECASE),
    "priority": re.compile(r"\b(PRIORITY|EARLIEST\s*PRIORITY|CLAIMS?\s*PRIORITY)\b",
                           re.IGNORECASE),
    "provisional": re.compile(r"\bPROVISIONAL\b", re.IGNORECASE),
    "grant": re.compile(r"\b(GRANT|PATENT\s*NO|PATENTED|GRANTED)\b", re.IGNORECASE),
}


def _clean(s):
    s = s.replace("U.S.", "US").replace("U. S.", "US")
    s = re.sub(r"\bUnited\s+States\b", "US", s, flags=re.IGNORECASE)
    return s.strip()


def _interp(cc, num, ntype, kind, year, conf, basis, series=None, warning=None):
    normalized = cc or ""
    if series:
        normalized += series + "/" + num
    elif year:
        normalized += year + "/" + num
    else:
        normalized += num
    normalized += (kind or "")
    key = None
    if series:
        key = "%s:%s/%s" % (cc, series, num)
    elif year:
        key = "%s:%s:%s" % (cc, year, num)
    else:
        key = "%s:%s" % (cc, num)
    out = {
        "normalized": normalized,
        "country": cc,
        "number_type": ntype,
        "kind_code": kind,
        "year": year,
        "number": num,
        "series": series,
        "confidence": conf,
        "basis": basis,
        "key": key,
    }
    if warning:
        out["warning"] = warning
    return out


def _interpret(raw):
    """对单个号码字符串给出全部候选解释，按置信度降序。"""
    s = _clean(raw)
    if not s:
        return []
    up = s.upper()
    out = []

    markers = {k: bool(v.search(up)) for k, v in MARKER_RE.items()}
    kind_m = KIND_RE.search(up)
    kind = kind_m.group(1).upper() if kind_m else None

    # A. 年份+斜杠（申请公开号 / 国际公开号）：US 2008/0123456 A1、WO 2008/123456 A1
    for m in YEAR_SLASH_RE.finditer(up):
        cc, yr, num = m.group(1).upper(), m.group(2), m.group(3).lstrip("0") or "0"
        if cc not in COUNTRY_CODES:
            continue
        k = kind
        if cc == "WO":
            ntype, conf = "international_publication", "high"
        elif cc == "US" and k and k.startswith("A"):
            ntype, conf = "publication", "high"
        elif cc == "US":
            ntype, conf = "publication", "medium"
        else:
            ntype, conf = "publication", "high"
        out.append(_interp(cc, num, ntype, k, yr, conf, "year_slash"))

    # B. 号码 + 种类代码（授权公告号/公开号）：US 12,345,678 B2
    for m in NUM_KIND_RE.finditer(up):
        cc = m.group(1).upper()
        if cc not in COUNTRY_CODES:
            continue
        num = re.sub(r"[,\s]", "", m.group(2))
        k = m.group(3).upper()
        if k.startswith("B"):
            ntype, conf = "grant_publication", "high"
        elif k.startswith("A"):
            ntype, conf = "publication", "high"
        elif k[0] in ("U", "Y"):
            ntype, conf = "utility_model", "high"
        elif k[0] == "S":
            ntype, conf = "design", "high"
        elif k[0] == "C":
            ntype, conf = "grant_publication", "medium"
        else:
            ntype, conf = "unknown", "medium"
        if markers["grant"] and ntype != "grant_publication":
            ntype = "grant_publication"
        out.append(_interp(cc, num, ntype, k, None, conf, "number_kind"))

    # C. 系列号+斜杠（申请号/临时申请/优先权）：US 12/345,678、US 60/123,456
    for m in SERIES_SLASH_RE.finditer(up):
        cc = m.group(1).upper()
        if cc not in COUNTRY_CODES:
            continue
        series = m.group(2)
        num = re.sub(r",", "", m.group(3))
        si = int(series)
        if cc == "US" and si in (60, 61, 62):
            ntype, conf = "provisional", "high"
        elif cc == "US":
            ntype, conf = "application", "high"
        else:
            ntype, conf = "application", "medium"
        if markers["priority"]:
            ntype, conf = "priority", "high"
        elif markers["provisional"]:
            ntype, conf = "provisional", "high"
        elif markers["application"]:
            ntype, conf = "application", "high"
        out.append(_interp(cc, num, ntype, kind, None, conf, "series_slash",
                           series=series))

    # 去重（同 normalized 保留置信度最高者）
    dedup = {}
    for it in out:
        key = it["normalized"]
        if key not in dedup or it["confidence"] == "high":
            dedup[key] = it
    out = sorted(dedup.values(), key=lambda x: {"high": 0, "medium": 1, "low": 2}[x["confidence"]])

    if not out:
        digits = re.sub(r"[^\d]", "", s) or None
        out = [_interp(None, digits, "unknown", kind, None, "low",
                       "unrecognized_format",
                       warning="无法识别为已知专利号格式，需人工核对或补充来源标注")]
    return out


def normalize_patent_number(raw):
    """规范化单个号码，返回全部候选解释与最佳解释。"""
    if isinstance(raw, dict):
        raw = raw.get("raw") or raw.get("number") or raw.get("text") or ""
    interps = _interpret(str(raw or ""))
    return {
        "raw": str(raw),
        "interpretations": interps,
        "best": interps[0] if interps else None,
        "ambiguous": len(interps) > 1,
        "count": len(interps),
    }


def extract_numbers(text):
    """从文本抽取专利号候选，带位置、上下文与规范化解释。"""
    text = _clean(text or "")
    hits = []
    seen = set()
    for pat in (YEAR_SLASH_RE, NUM_KIND_RE, SERIES_SLASH_RE):
        for m in pat.finditer(text or ""):
            s = m.group(0).strip()
            key = s.upper()
            if key in seen:
                continue
            seen.add(key)
            start = max(0, m.start() - 60)
            end = min(len(text), m.end() + 60)
            ctx = text[start:end].replace("\n", " ")
            norm = normalize_patent_number(s)
            hits.append({
                "match": s,
                "position": m.start(),
                "context": ctx,
                "best_interpretation": norm.get("best"),
                "ambiguous": norm.get("ambiguous", False),
                "interpretation_count": norm.get("count", 0),
            })
    hits.sort(key=lambda h: h["position"])
    return hits


def _role_key(it):
    """由解释推导 (role, key)，role ∈ application/priority/publication/None。"""
    cc = it.get("country") or "?"
    nt = it.get("number_type")
    num = it.get("number")
    series = it.get("series")
    yr = it.get("year")
    if nt in ("application", "priority", "provisional"):
        if series:
            key = "%s:%s/%s" % (cc, series, num)
        else:
            key = "%s:%s" % (cc, num)
        role = "application"
        if nt == "priority":
            role = "priority"
        return role, key
    if nt in ("publication", "grant_publication", "international_publication",
              "utility_model", "design"):
        key = "%s:%s:%s" % (cc, yr, num) if yr else "%s:%s" % (cc, num)
        return "publication", key
    return None, None


def _norm_records(records):
    """把 records 归一化为 {rid: {links:set, pubs:set, meta:dict}}。"""
    norm = {}
    for i, r in enumerate(records or []):
        if not isinstance(r, dict):
            continue
        rid = str(r.get("record_id") or r.get("id") or ("rec-%d" % (i + 1)))
        nums = r.get("numbers") or r.get("number") or []
        if isinstance(nums, str):
            nums = [nums]
        prio = r.get("priority_numbers") or r.get("priorities") or []
        if isinstance(prio, str):
            prio = [prio]
        links, pubs = set(), set()
        for raw in list(nums) + list(prio):
            n = normalize_patent_number(raw)
            for it in n.get("interpretations", []):
                role, key = _role_key(it)
                if role == "publication" and key:
                    pubs.add(key)
                elif role in ("application", "priority") and key:
                    links.add(key)
        norm[rid] = {"links": links, "pubs": pubs, "meta": r}
    return norm


def group_families(records):
    """按共用申请号/优先权号归并同族；绝不按主题/关键词合并。

    返回 {families, ungrouped, duplicates, rejected_merges,
          observed:true, exhaustive:false}。
    """
    norm = _norm_records(records)
    ids = sorted(norm.keys())
    parent = {r: r for r in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    key_ids = {}
    pub_ids = {}
    for rid, v in norm.items():
        for k in v["links"]:
            key_ids.setdefault(k, set()).add(rid)
        for k in v["pubs"]:
            pub_ids.setdefault(k, set()).add(rid)
    for k, grp in key_ids.items():
        grp = sorted(grp)
        for rid in grp[1:]:
            union(grp[0], rid)

    comps = {}
    for rid in ids:
        comps.setdefault(find(rid), []).append(rid)

    families = []
    for root in sorted(comps, key=lambda r: sorted(comps[r])[0]):
        members = sorted(comps[root])
        shared = set()
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                shared |= norm[members[i]]["links"] & norm[members[j]]["links"]
        if len(members) == 1:
            basis, detail, conf = "singleton", "未发现与其他记录共用申请号/优先权号", "n/a"
        elif shared:
            basis = "shared_application_or_priority_number"
            detail = "共同号码: " + ", ".join(sorted(shared))
            conf = "high"
        else:
            basis, detail, conf = "none", "无共用申请号/优先权号", "low"
        families.append({
            "family_id": "F%d" % (len(families) + 1),
            "members": members,
            "family_basis": basis,
            "basis_detail": detail,
            "shared_keys": sorted(shared),
            "family_type": "observed",
            "exhaustive": False,
            "confidence": conf,
        })

    duplicates = [{"publication_key": k, "records": sorted(v),
                   "note": "同一公开号，疑似重复记录"}
                  for k, v in pub_ids.items() if len(v) > 1]

    rejected = []
    for rid, v in norm.items():
        for b in (v["meta"].get("rejected_merge_with") or []):
            rejected.append({
                "a": rid, "b": str(b),
                "reason": v["meta"].get("rejected_merge_reason")
                          or "仅主题/关键词相似，无共用号码，不归并",
            })

    ungrouped = [f["members"][0] for f in families if len(f["members"]) == 1]
    return {
        "families": families,
        "ungrouped": ungrouped,
        "duplicates": duplicates,
        "rejected_merges": rejected,
        "observed": True,
        "exhaustive": False,
    }


# ---------------------------------------------------------------------------
# 结果骨架 / 契约回填 / 校验 / 报告
# ---------------------------------------------------------------------------

DEFAULT_RESULT = {
    "case_id": "unknown",
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
    "merge_rejected": [],
    "change_log": [],
    "degradation_tier": "green",
    "runtime": {"started_at": None},
}


def build_skeleton(case_obj, contract):
    """生成合法骨架：默认 schema 打底，output_contract 原样覆盖（保留键名）。"""
    result = deep_copy(DEFAULT_RESULT)
    result["runtime"]["started_at"] = now_iso()

    case_id = None
    if isinstance(case_obj, dict):
        case_id = case_obj.get("case_id") or case_obj.get("caseId") or case_obj.get("id")
    if case_id:
        result["case_id"] = str(case_id)
    else:
        result["uncertainty"].append({
            "item": "case_id_missing", "type": "field_missing",
            "reason": "case.json 未提供 case_id",
            "impact": "无法与白板对照追溯", "mitigation": "使用 unknown 并记录本项",
        })

    if isinstance(contract, dict):
        for k, v in contract.items():
            result[k] = deep_copy(v)

    result["change_log"].append({
        "at": now_iso(), "event": "scaffold",
        "detail": "写入初始骨架", "status": result["status"],
    })
    return result


STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}
TIERS = {"green", "yellow", "red"}


def validate_result(result):
    """校验 result.json 结构合法性，返回错误列表（空列表 = 合法）。"""
    errs = []
    if not isinstance(result, dict):
        return ["result 不是 JSON 对象"]
    if result.get("status") not in STATUSES:
        errs.append("status 取值非法: %r" % result.get("status"))
    if not result.get("case_id"):
        errs.append("case_id 缺失或为空")
    for key in ("sources", "uncertainty", "pending_human", "errors",
                "degradations", "limitations", "change_log", "evidence"):
        if not isinstance(result.get(key), list):
            errs.append("字段 %s 必须是数组" % key)
    for e in result.get("evidence", []):
        if not e.get("locator") or not e.get("quote"):
            errs.append("evidence 条目缺 locator 或 quote")
        if not e.get("strength"):
            errs.append("evidence 条目缺 strength（直接引用/推导/断言/未验证）")
    for s in result.get("sources", []):
        if not s.get("engine"):
            errs.append("sources 条目缺 engine")
        if "retrieved_at" not in s and "queried_at" not in s:
            errs.append("sources 条目缺查询时间")
    if result.get("degradation_tier") not in TIERS:
        errs.append("degradation_tier 取值非法: %r" % result.get("degradation_tier"))
    if "runtime" not in result:
        errs.append("缺 runtime")
    return errs


def apply_degradation(result, item, reason, tier=None):
    """记录一次降级并同步 degradation_tier（绿→黄→红，只升不降）。"""
    result.setdefault("degradations", []).append({
        "item": item, "reason": reason, "at": now_iso(),
    })
    order = {"green": 0, "yellow": 1, "red": 2}
    cur = result.get("degradation_tier", "green")
    if tier in order and order[tier] > order.get(cur, 0):
        result["degradation_tier"] = tier


def add_uncertainty(result, item, type_, reason, impact=None, mitigation=None):
    result.setdefault("uncertainty", []).append({
        "item": item, "type": type_, "reason": reason,
        "impact": impact, "mitigation": mitigation,
    })


def build_report(result):
    """生成可读 report.md 正文。"""
    L = []
    L.append("# 检索结果报告")
    L.append("")
    L.append("- case_id: `%s`" % result.get("case_id", "unknown"))
    L.append("- status: `%s`" % result.get("status", "unknown"))
    L.append("- degradation_tier: `%s`" % result.get("degradation_tier", "green"))
    L.append("- 生成时间: %s" % now_iso())
    L.append("")
    L.append("## 研究问题与检索范围")
    L.append("")
    L.append(str(result.get("research_question") or "not_reported"))
    L.append("")
    L.append(str(result.get("retrieval_scope") or "not_reported"))
    L.append("")
    L.append("## 主结果统计")
    L.append("")
    for k in ("patent_records", "families", "legal_status", "literature",
              "evidence", "sources"):
        v = result.get(k, [])
        L.append("- %s: %d" % (k, len(v) if isinstance(v, list) else 0))
    L.append("")
    L.append("## 不确定性")
    L.append("")
    if result.get("uncertainty"):
        for u in result["uncertainty"]:
            L.append("- **%s**（%s）: %s" % (u.get("item"), u.get("type"), u.get("reason")))
    else:
        L.append("- 无")
    L.append("")
    L.append("## 待人工复核")
    L.append("")
    if result.get("pending_human"):
        for p in result["pending_human"]:
            L.append("- %s" % json.dumps(p, ensure_ascii=False))
    else:
        L.append("- 无")
    L.append("")
    L.append("## 限制")
    L.append("")
    if result.get("limitations"):
        for x in result["limitations"]:
            L.append("- %s" % json.dumps(x, ensure_ascii=False))
    else:
        L.append("- 无")
    L.append("")
    L.append("## 变更记录")
    L.append("")
    for c in result.get("change_log", []):
        L.append("- %s %s (%s)" % (c.get("at"), c.get("event"), c.get("status", "")))
    return "\n".join(L) + "\n"
