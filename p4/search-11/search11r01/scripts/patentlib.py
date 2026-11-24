#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patentlib.py — 专利与技术文献检索 Skill 的核心工具库（纯标准库）。

职责：
  * 容错读取文本（utf-8-sig / utf-8 / gb18030 / latin-1 降级，单文件 2MB 上限）
  * 容错读取 JSON（坏 JSON 不抛异常，返回 error 字典）
  * 扫描输入目录，按 kind 分类（case / pdf / image / table / text / other）
  * 专利号规范化（公开号 / 申请号 / 授权号 / 优先权号），区分 kind code 与号码主体
  * 从自由文本/OCR 文本抽取专利号候选（附 locator=char 区间 + 上下文 quote）
  * 按共用申请号 / 优先权号归并同族（observed，非穷尽）
  * 构建 result.json 骨架（回填 output_contract 全部键，早落盘）
  * 证据溯源：reconcile_evidence 在 evidence_map 与 evidence 两种视图间互建，
    为每条证据补齐 evidence_id / quote_sha256 / verification_status / locator_type
  * 校验 result.json，生成 report.md

分析字段确定性：同一输入重复运行，除 runtime 时间戳外，分析字段结果一致；
本库不使用随机数，遍历顺序固定。
"""

import hashlib
import json
import os
import re

from datetime import datetime, timezone

MAX_TEXT_BYTES = 2 * 1024 * 1024  # 单文件 2MB 上限，超限跳过并记录

# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def now_iso():
    """UTC ISO-8601 秒级时间戳。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_hex(text):
    """对规范化后的文本（压缩空白）计算 sha256，用于防篡改/可复核。"""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def safe_size(path):
    try:
        return os.path.getsize(path)
    except Exception:
        return -1


def read_text(path, max_bytes=MAX_TEXT_BYTES):
    """容错读取文本文件，返回 (text, encoding, error_or_None)。

    编码尝试顺序：utf-8-sig -> utf-8 -> gb18030 -> latin-1(replace)。
    超限/读取失败时返回空文本与 error 字典，不抛异常。
    """
    text, enc, err = "", None, None
    try:
        size = os.path.getsize(path)
        if size > max_bytes:
            return "", None, {
                "error": "file_too_large",
                "reason": "size %d bytes > limit %d" % (size, max_bytes),
            }
        with open(path, "rb") as fh:
            raw = fh.read()
        for cand in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
            try:
                text = raw.decode(cand)
                enc = cand
                break
            except UnicodeDecodeError:
                continue
        if enc is None:
            text = raw.decode("latin-1", errors="replace")
            enc = "latin-1(replace)"
            err = {"error": "undecodable", "reason": "decoded latin-1 with replacement"}
    except Exception as exc:
        err = {"error": "read_failed", "reason": str(exc)}
    return text, enc, err


def load_json(path):
    """容错读取 JSON，返回 (obj, error_or_None)。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except Exception as exc:
        return None, {"error": "json_parse_failed", "reason": str(exc)}


def discover_input(input_dir):
    """扫描输入目录，返回文件清单（按 rel 排序，确定性）。"""
    files = []
    if not input_dir or not os.path.isdir(input_dir):
        return files
    try:
        names = sorted(os.listdir(input_dir))
    except Exception:
        return files
    for name in names:
        p = os.path.join(input_dir, name)
        if not os.path.isfile(p):
            continue
        low = name.lower()
        if low == "case.json":
            kind = "case"
        elif low.endswith(".pdf"):
            kind = "pdf"
        elif low.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff",
                            ".gif", ".webp", ".bmp")):
            kind = "image"
        elif low.endswith((".csv", ".tsv")):
            kind = "table"
        elif low.endswith((".md", ".txt", ".json", ".xml", ".yaml",
                            ".yml", ".html", ".htm")):
            kind = "text"
        else:
            kind = "other"
        files.append({"rel": name, "path": p, "kind": kind, "size": safe_size(p)})
    return files


# ---------------------------------------------------------------------------
# 专利号规范化
# ---------------------------------------------------------------------------

COUNTRY_CODES = (
    r"PCT|US|WO|EP|CN|JP|KR|DE|GB|FR|CA|AU|IN|RU|TW|ES|IT|NL|SE|CH|AT|BE|DK|"
    r"FI|NO|PL|BR|MX|IL|ZA|SG|HK"
)
# 常见 kind code 字母（WIPO ST.16 常用子集）
KIND_LETTERS = set("ABCDEFGHIKLMNPRSTUY")
# 数字主体：可选两字母受理局 + 斜杠（如 PCT/US），后跟至少一个数字
BODY_RE = r"(?:[A-Z]{2}/)?\d[\d\s,./\-]{2,}"
PATENT_TOKEN_RE = re.compile(
    r"\b(" + COUNTRY_CODES + r")\s*(" + BODY_RE + r")"
    r"(?:\s*([A-Z])(\d)?)?(?=\s|$|[^A-Za-z0-9])",
    re.I,
)

KIND_GRANT = {"B1", "B2", "B3", "B4", "B8", "B9", "C"}
KIND_PUB = {"A1", "A2", "A3", "A4", "A8", "A9", "A", "U", "Y", "T"}


def _classify_kind(kind):
    if not kind:
        return None
    if kind in KIND_GRANT:
        return "grant"
    if kind in KIND_PUB:
        return "publication"
    return None


def normalize_patent_number(raw):
    """规范化一个专利号，返回结构化结果（确定性）。"""
    src = (raw or "").strip()
    out = {
        "raw": src,
        "normalized": None,
        "country": None,
        "number_body": None,
        "kind_code": None,
        "number_type": "unknown",
        "type_candidates": [],
        "confidence": "low",
        "issues": [],
    }
    if not src:
        out["issues"].append("empty_input")
        return out

    s = src.upper()

    # 显式标签（公开号/申请号/授权号/优先权号），仅作类型提示
    label = None
    lm = re.search(
        r"(公开号|申请号|授权号|优先权号|优先权|公告号|申请日|"
        r"PUBLICATION\s*NO\.?|APPLICATION\s*NO\.?|GRANT\s*NO\.?|"
        r"PRIORITY\s*NO\.?)",
        s,
    )
    if lm:
        label = lm.group(1)

    cm = re.search(r"\b(" + COUNTRY_CODES + r")\b", s)
    if not cm:
        out["issues"].append("no_country_code")
        country = None
        rest = s
    else:
        country = cm.group(1)
        rest = s[cm.end():]

    # 尾部 kind code
    kind = None
    km = re.search(r"(?:\s*([A-Z])(\d)?)\s*$", rest)
    if km:
        letter, digit = km.group(1), (km.group(2) or "")
        body_candidate = rest[:km.start()]
        if letter in KIND_LETTERS and re.search(r"\d", body_candidate):
            kind = letter + digit
            rest = body_candidate

    # 号码主体：去分隔符，保留字母数字
    base = re.sub(r"[^0-9A-Z]", "", rest)
    if not re.search(r"\d", base):
        out["issues"].append("no_digits")
        return out

    normalized = (country or "") + base + (kind or "")
    out["country"] = country
    out["number_body"] = (country or "") + base
    out["kind_code"] = kind
    out["normalized"] = normalized

    slash = "/" in src
    provisional = "62/" in src.replace(" ", "")

    kind_type = _classify_kind(kind)
    if label:
        if "优先" in label or "PRIORITY" in label:
            out["number_type"] = "priority"
            out["type_candidates"].append(
                {"type": "priority", "reason": "explicit label", "confidence": "high"})
        elif "申请" in label or "APPLICATION" in label:
            out["number_type"] = "application"
            out["type_candidates"].append(
                {"type": "application", "reason": "explicit label", "confidence": "high"})
        elif "公开" in label or "公告" in label or "PUBLICATION" in label or "GRANT" in label:
            out["number_type"] = kind_type or "publication"
            out["type_candidates"].append(
                {"type": out["number_type"], "reason": "explicit label", "confidence": "high"})
    elif country == "PCT":
        out["number_type"] = "application"
        out["type_candidates"].append(
            {"type": "application", "reason": "PCT application", "confidence": "high"})
    elif kind_type == "grant":
        out["number_type"] = "grant"
        out["type_candidates"].append(
            {"type": "grant", "reason": "kind code %s" % kind, "confidence": "high"})
    elif kind_type == "publication":
        out["number_type"] = "publication"
        out["type_candidates"].append(
            {"type": "publication", "reason": "kind code %s" % kind, "confidence": "high"})
    elif provisional:
        out["number_type"] = "provisional_priority"
        out["type_candidates"].append(
            {"type": "provisional_priority", "reason": "62/ provisional", "confidence": "medium"})
        out["type_candidates"].append(
            {"type": "application", "reason": "serial format", "confidence": "low"})
    elif slash:
        out["number_type"] = "application"
        out["type_candidates"].append(
            {"type": "application", "reason": "serial format (slash)", "confidence": "medium"})
        if country == "WO":
            out["number_type"] = "publication"
            out["type_candidates"] = [
                {"type": "publication", "reason": "WO publication format", "confidence": "high"}]
    else:
        out["number_type"] = "application"
        out["type_candidates"].append(
            {"type": "application", "reason": "bare numeric application number", "confidence": "low"})

    if country and kind:
        out["confidence"] = "high"
    elif country:
        out["confidence"] = "medium"
    else:
        out["confidence"] = "low"
    return out


def extract_numbers(text):
    """从文本抽取专利号候选，返回带 locator(char 区间) 与上下文 quote 的清单。"""
    hits = []
    if not text:
        return hits
    for m in PATENT_TOKEN_RE.finditer(text):
        raw = m.group(0).strip()
        norm = normalize_patent_number(raw)
        start, end = m.start(), m.end()
        ctx_start = max(0, start - 40)
        ctx_end = min(len(text), end + 40)
        hits.append({
            "raw": raw,
            "normalized": norm,
            "locator": "input_text#%d-%d" % (start, end),
            "context": text[ctx_start:ctx_end],
            "confidence": norm.get("confidence", "low"),
        })
    return hits


# ---------------------------------------------------------------------------
# 同族归并
# ---------------------------------------------------------------------------

def _norm_key(value):
    """把字段值转成可比较的规范化键。"""
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("normalized") or value.get("raw")
    if value is None:
        return None
    s = re.sub(r"[^0-9A-Z]", "", str(value).upper())
    return s or None


def group_families(records):
    """按共用申请号/优先权号归并同族（observed，非穷尽）。

    records 为列表，每个元素需含 record_id（或 id），以及可选的
    application_number / priority_number / normalized / raw 等字段。
    归并键：application_number、priority_number（规范化后比较）。
    """
    if not isinstance(records, list):
        records = [records]
    idx = {}
    edges = []  # (a, b, basis)
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            continue
        rid = rec.get("record_id") or rec.get("id") or ("r%d" % (i + 1))
        idx[i] = rid
    # 构建邻接
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
            parent[ra] = rb

    keys = {}
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            continue
        for field in ("application_number", "priority_number", "application_no",
                      "priority_no", "application", "priority"):
            key = _norm_key(rec.get(field))
            if not key:
                continue
            if key not in keys:
                keys[key] = i
            else:
                edges.append((keys[key], i, field))
                union(keys[key], i)

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    families = []
    for fid, members in enumerate(sorted(groups.values(),
                                         key=lambda m: min(m)), 1):
        member_ids = [idx[m] for m in members]
        bases = sorted({e[2] for e in edges
                        if find(e[0]) == find(members[0]) and e[1] in members})
        families.append({
            "family_id": "F%d" % fid,
            "family_type": "observed",
            "members": member_ids,
            "member_count": len(member_ids),
            "linkage_basis": bases,
            "basis_note": ("observed co-linkage via normalized application/priority "
                           "number; not an authoritative INPADOC simple-family"),
            "exhaustive": False,
        })
    return {"families": families, "observed": True, "exhaustive": False}


# ---------------------------------------------------------------------------
# 骨架 / 证据溯源 / 校验 / 报告
# ---------------------------------------------------------------------------

DEFAULT_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")


def build_skeleton(case_obj, contract):
    """构建并落盘前的 result.json 骨架（早落盘 partial）。"""
    result = {
        "case_id": None,
        "status": "partial",
        "research_question": None,
        "retrieval_scope": None,
        "patent_records": [],
        "families": [],
        "legal_status": [],
        "literature": [],
        "authorization": [],
        "conclusions": [],
        "evidence": [],
        "evidence_map": {},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "artifacts": [],
        "input_inventory": [],
        "change_log": [],
        "runtime": {
            "started_at": now_iso(),
            "finished_at": None,
            "elapsed_seconds": None,
            "model_requests": None,
        },
    }
    cid = None
    if isinstance(case_obj, dict):
        cid = case_obj.get("case_id") or case_obj.get("caseId") or case_obj.get("id")
    result["case_id"] = cid or "unknown"
    if not cid:
        result["uncertainty"].append({
            "item": "case_id_missing",
            "reason": "case.json lacks case_id; recorded as 'unknown'",
        })

    if isinstance(contract, dict):
        for key in contract:
            result.setdefault(key, None)

    result["change_log"].append({
        "ts": now_iso(),
        "action": "scaffold",
        "detail": "initial partial skeleton written",
    })
    return result


def reconcile_evidence(result):
    """在 evidence_map（细结构）与 evidence（扁平列表）间互建并交叉关联。

    规则：
      * evidence_map 为 {evidence_id: {…}}；evidence 为 [ {…} ]。
      * 有 map 无 list 时按 map 展平；有 list 无 map 时按 evidence_id（缺省 E1..）建 map。
      * 每条证据补齐 evidence_id、quote_sha256、verification_status、locator_type。
      * conclusions[] 里每个条目补齐 conclusion_id，并回填其 evidence_ids 到 map 条目。
    """
    ev_map = result.get("evidence_map")
    ev_list = result.get("evidence")

    def _finalize_entry(e, eid):
        e = dict(e) if isinstance(e, dict) else {"detail": e}
        e.setdefault("evidence_id", eid)
        if "quote" in e and isinstance(e.get("quote"), str):
            e.setdefault("quote_sha256", sha256_hex(e["quote"]))
        e.setdefault("verification_status", "unverified")
        e.setdefault("locator_type", "unspecified")
        e.setdefault("locator", None)
        e.setdefault("source", None)
        return e

    if isinstance(ev_map, dict) and ev_map:
        flat, rebuilt = [], {}
        for i, (eid, e) in enumerate(sorted(ev_map.items(),
                                            key=lambda kv: str(kv[0])), 1):
            e = _finalize_entry(e, eid or ("E%d" % i))
            flat.append(e)
            rebuilt[e["evidence_id"]] = e
        result["evidence"] = flat
        result["evidence_map"] = rebuilt
    elif isinstance(ev_list, list) and ev_list:
        rebuilt = {}
        for i, e in enumerate(ev_list, 1):
            if not isinstance(e, dict):
                continue
            eid = e.get("evidence_id") or ("E%d" % i)
            e = _finalize_entry(e, eid)
            rebuilt[eid] = e
        result["evidence"] = list(rebuilt.values())
        result["evidence_map"] = rebuilt

    # conclusions 回填
    cons = result.get("conclusions")
    if isinstance(cons, list):
        for i, c in enumerate(cons, 1):
            if not isinstance(c, dict):
                continue
            c.setdefault("conclusion_id", "C%d" % i)
            c.setdefault("strength", "unverified")
            c.setdefault("status", "unverified")
            ids = c.get("evidence_ids")
            if not isinstance(ids, list):
                ids = []
            c["evidence_ids"] = ids
            for eid in ids:
                e = result.get("evidence_map", {}).get(eid)
                if isinstance(e, dict):
                    e.setdefault("conclusion_id", c["conclusion_id"])
    return result


def validate_result(result, contract=None):
    """校验 result.json，返回问题列表（空列表=通过）。"""
    problems = []
    if not isinstance(result, dict):
        return ["result is not a JSON object"]
    if not result.get("case_id"):
        problems.append("case_id missing or empty")
    if result.get("status") not in DEFAULT_STATUS:
        problems.append("invalid status: %r" % result.get("status"))
    for key in ("sources", "uncertainty", "pending_human",
                "errors", "degradations", "limitations", "change_log"):
        if key not in result:
            problems.append("missing key: %s" % key)
        elif not isinstance(result[key], list):
            problems.append("key %s must be a list" % key)
    if "evidence" not in result and "evidence_map" not in result:
        problems.append("no evidence/evidence_map key")
    if "runtime" not in result or not isinstance(result.get("runtime"), dict):
        problems.append("runtime missing or not an object")
    if isinstance(contract, dict):
        for key in contract:
            if key not in result:
                problems.append("output_contract key missing: %s" % key)
    return problems


def render_report(result):
    """由 result.json 生成人类可读的 report.md（Markdown 字符串）。"""
    lines = []
    lines.append("# 专利与技术文献检索报告")
    lines.append("")
    lines.append("- case_id: `%s`" % result.get("case_id"))
    lines.append("- status: `%s`" % result.get("status"))
    lines.append("- research_question: %s" % (result.get("research_question") or "（未填写）"))
    lines.append("- retrieval_scope: %s" % (result.get("retrieval_scope") or "（未填写）"))
    lines.append("")
    lines.append("## 主产物")
    for a in result.get("artifacts") or []:
        if isinstance(a, dict):
            lines.append("- %s (%s)" % (a.get("path"), a.get("note", "")))
        else:
            lines.append("- %s" % a)
    lines.append("")
    lines.append("## 计数")
    lines.append("- patent_records: %d" % len(result.get("patent_records") or []))
    lines.append("- families: %d" % len(result.get("families") or []))
    lines.append("- legal_status: %d" % len(result.get("legal_status") or []))
    lines.append("- literature: %d" % len(result.get("literature") or []))
    lines.append("- conclusions: %d" % len(result.get("conclusions") or []))
    lines.append("- evidence: %d" % len(result.get("evidence") or []))
    lines.append("- sources: %d" % len(result.get("sources") or []))
    lines.append("")
    lines.append("## 证据溯源（evidence_map）")
    em = result.get("evidence_map") or {}
    if em:
        for eid in sorted(em.keys(), key=str):
            e = em[eid] if isinstance(em[eid], dict) else {"detail": em[eid]}
            lines.append("- **%s** [%s] %s" % (
                e.get("evidence_id", eid),
                e.get("verification_status", "unverified"),
                (e.get("claim_brief") or "")[:120]))
            lines.append("  - locator: `%s`" % e.get("locator"))
            quote = e.get("quote") or ""
            lines.append("  - quote: `%s`" % (quote[:200] + ("…" if len(quote) > 200 else "")))
            lines.append("  - quote_sha256: `%s`" % e.get("quote_sha256"))
    else:
        lines.append("（无证据记录）")
    lines.append("")
    lines.append("## 不确定性与待办")
    lines.append("- uncertainty: %d 条" % len(result.get("uncertainty") or []))
    lines.append("- pending_human: %d 条" % len(result.get("pending_human") or []))
    lines.append("- limitations: %d 条" % len(result.get("limitations") or []))
    for item in (result.get("pending_human") or []):
        if isinstance(item, dict):
            lines.append("  - %s: %s" % (item.get("item"), item.get("reason")))
    lines.append("")
    lines.append("## 运行信息")
    rt = result.get("runtime") or {}
    lines.append("- started_at: %s" % rt.get("started_at"))
    lines.append("- finished_at: %s" % rt.get("finished_at"))
    lines.append("- elapsed_seconds: %s" % rt.get("elapsed_seconds"))
    lines.append("")
    lines.append("> 本报告由 finalize 自动生成；逐字证据以 result.json 的 evidence_map 为准。")
    return "\n".join(lines) + "\n"
