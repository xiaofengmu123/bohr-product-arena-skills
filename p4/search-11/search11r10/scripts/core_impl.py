#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — 专利与技术文献检索 Skill 的领域实现库（纯标准库）。

架构说明（与单文件 CLI 参考实现刻意不同）：
  1. 输入发现 -> 类型化中间表示（dataclass PatentRecord / Evidence）-> 校验 -> 落盘。
  2. 同族归并使用「并查集 + 传递闭包」：凡共享申请号或优先权号的记录被
     传递合并，而不是按单一键简单分组；每组记录 basis（用了哪个键连起来）。
  3. output_contract 支持两种形态：扁平字段表 与 JSON-Schema（含 properties），
     均被原样回填；schema 形态时提取 properties 键作为必回填字段。
  4. validate_result 按固定顺序检查：对象性 -> 顶层键 -> status 取值 ->
     契约回填 -> evidence 的 locator/quote 配对 -> patent_records 的 record_id
     -> runtime.started_at。finalize 以校验结果作为收尾门槛。

所有函数不读网络、不写除 output/work 之外的路径；同一输入重复运行结果一致。
"""

import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

MAX_TEXT_BYTES = 2 * 1024 * 1024
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
REQUIRED_KEYS = [
    "case_id", "status", "research_question", "retrieval_scope",
    "patent_records", "families", "legal_status", "literature",
    "authorization", "evidence", "sources", "uncertainty",
    "pending_human", "errors", "degradations", "limitations",
    "change_log", "runtime",
]


# --------------------------------------------------------------------------- #
# 基础 IO（容错，不崩溃）
# --------------------------------------------------------------------------- #

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path):
    """容错读取 JSON；坏 JSON 返回 (None, err)。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except (OSError, ValueError) as e:
        return None, {"error": str(e)}


def read_text(path, max_bytes=MAX_TEXT_BYTES):
    """utf-8-sig 优先、gb18030/latin-1 降级的容错文本读取。"""
    enc = None
    err = None
    text = ""
    try:
        size = os.path.getsize(path)
        if size > max_bytes:
            err = {"error": "file exceeds %d bytes" % max_bytes, "size": size}
            return "", None, err
        raw = open(path, "rb").read()
        for candidate in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
            try:
                text = raw.decode(candidate)
                enc = candidate
                err = None
                break
            except UnicodeDecodeError:
                continue
        if enc is None:
            text = raw.decode("latin-1", errors="replace")
            enc = "latin-1"
            err = {"error": "undecodable text; latin-1 replace fallback"}
    except OSError as e:
        err = {"error": str(e)}
    return text, enc, err


def discover_input(input_dir):
    """扫描输入目录；不假设固定文件名，递归 materials/ 等子目录。"""
    files = []
    if not os.path.isdir(input_dir):
        return files
    for root, _dirs, names in os.walk(input_dir):
        for name in sorted(names):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            ext = os.path.splitext(name)[1].lower()
            if name == "case.json":
                kind = "case"
            elif ext == ".json":
                kind = "data"
            elif ext in (".md", ".txt", ".text", ".log"):
                kind = "text"
            elif ext in (".csv", ".tsv"):
                kind = "table"
            elif ext == ".pdf":
                kind = "pdf"
            elif ext in (".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff",
                         ".bmp", ".webp", ".svg"):
                kind = "image"
            else:
                kind = "other"
            files.append({"path": full, "rel": rel, "kind": kind, "size": size})
    files.sort(key=lambda f: f["rel"])
    return files


# --------------------------------------------------------------------------- #
# 专利号规范化与抽取
# --------------------------------------------------------------------------- #

_COUNTRY_CODES = {
    "US", "WO", "EP", "CN", "JP", "KR", "DE", "GB", "FR", "CA", "AU",
    "TW", "RU", "IN", "BR", "CH", "NL", "SE", "FI", "DK", "AT", "BE",
    "IT", "ES", "MX", "IL", "SG", "HK", "ZA",
}
# 公开号 kind code 常见集合（按长度降序，避免 B1 被 B 抢先）。
_KIND_TOKENS = (
    "A1", "A2", "A3", "A4", "A8", "A9",
    "B1", "B2", "B3", "B4", "B5", "B6", "B8", "B9",
    "C1", "C2", "C3", "C4", "C5", "C9",
    "Y1", "Y2", "U", "S", "E", "T", "D",
)
_PREFIXES = (
    "PUB. NO.", "PUB NO", "PUBLICATION NO.", "APPLICATION NO.", "APP. NO.",
    "PRIORITY NO.", "PRIORITY", "PATENT NO.", "PATENT NUMBER", "PATENT",
    "USPTO", "ESPACENET", "PATENTSCOPE",
)
_SEPARATORS = re.compile(r"[\s,./\-_]+")


def _strip_kind(rest):
    """把末尾 kind code 从号码主体中分离；返回 (core, kind)。"""
    up = rest.upper()
    for tok in _KIND_TOKENS:
        if not up.endswith(tok):
            continue
        prev = up[: -len(tok)]
        if not prev:
            continue
        # 允许紧贴数字（US12345678B2）或分隔符（3 456 789 A1）。
        if prev[-1].isdigit() or prev[-1] in " ,./-_":
            return rest[: -len(tok)].strip(" /.-_"), tok
    return rest, None


def normalize_patent_number(raw):
    """规范化一个专利号，返回可复核的 dict。

    字段：raw / country / number / kind / normalized / number_type / confidence。
    number_type ∈ {publication, application, priority_or_other}。
    """
    out = {
        "raw": None, "country": None, "number": None, "kind": None,
        "normalized": None, "number_type": None, "confidence": 0.0,
        "notes": [],
    }
    if raw is None or str(raw).strip() == "":
        out["notes"].append("empty_input")
        return out
    s = str(raw).strip()
    out["raw"] = s
    up = s.upper()
    for pfx in _PREFIXES:
        if up.startswith(pfx):
            up = up[len(pfx):].lstrip(" :")
            break

    # 国别/机构代码；PCT 申请号单独标记（区别于 WO 公开号）。
    cc = None
    was_pct = False
    m = re.match(r"^([A-Z]{2})", up)
    if m and m.group(1) in _COUNTRY_CODES:
        cc = m.group(1)
        rest = up[2:].lstrip(" :/")
    elif up.startswith("PCT/"):
        cc = "WO"
        was_pct = True
        rest = up[4:].lstrip(" :/")
    elif up.startswith("WO"):
        cc = "WO"
        rest = up[2:].lstrip(" :/")
    else:
        rest = up

    core, kind = _strip_kind(rest)
    digits = re.sub(r"[^0-9]", "", core)

    if not digits:
        out["notes"].append("no_digits")
        out["confidence"] = 0.0
        return out
    if cc is None:
        out["notes"].append("country_unknown")
        out["confidence"] = 0.4

    # 号码类型判定
    if cc == "WO":
        if was_pct:
            number_type = "application"      # PCT/US2020/012345
        elif re.match(r"^\d{4}/\d{3,7}$", core):
            number_type = "publication"      # WO 2023/123456 A1
        elif "/" in core:
            number_type = "application"
        else:
            number_type = "publication" if kind else "priority_or_other"
    elif "/" in core:
        number_type = "application"          # 如 US 17/123,456
    elif re.search(r"\.\d$", core) and len(digits) in (8, 12, 13, 14):
        number_type = "application"          # 如 CN 202010123456.7 / EP 20345678.9
    elif kind is not None:
        number_type = "publication"
    else:
        number_type = "priority_or_other"

    normalized = cc + ":" + digits if cc else digits
    if kind:
        normalized += ":" + kind

    confidence = 0.9 if (cc and kind) else (0.8 if cc else 0.5)
    if number_type == "priority_or_other":
        confidence = min(confidence, 0.6)

    out.update({
        "country": cc, "number": digits, "kind": kind,
        "normalized": normalized, "number_type": number_type,
        "confidence": confidence,
    })
    return out


_PATENT_TOKEN_RE = re.compile(
    r"\b(?:(?:US|WO|EP|CN|JP|KR|DE|GB|FR|CA|AU|TW|RU|IN|PCT)/?)\s?"
    r"(?:[0-9][0-9A-Z]*[,\s./\-]*)+(?:[A-Z][0-9]?)?(?!\w)",
    re.IGNORECASE,
)


def extract_numbers(text):
    """从文本抽取专利号候选；返回 [{raw, start, end, normalized, number_type}]。"""
    hits = []
    if not text:
        return hits
    for m in _PATENT_TOKEN_RE.finditer(text):
        cand = m.group(0).strip()
        if len(cand) < 5:
            continue
        norm = normalize_patent_number(cand)
        if norm["normalized"] is None:
            continue
        digits = norm["number"] or ""
        # 过滤短数字年份等噪声：数字少于 5 位且无分隔符/kind 则跳过。
        if len(digits) < 5 and not any(ch in cand for ch in "/,."):
            continue
        hits.append({
            "raw": cand,
            "start": m.start(),
            "end": m.end(),
            "normalized": norm["normalized"],
            "number_type": norm["number_type"],
        })
    return hits


# --------------------------------------------------------------------------- #
# 类型化中间表示
# --------------------------------------------------------------------------- #

@dataclass
class PatentRecord:
    record_id: str = ""
    publication_number: str = None
    application_number: str = None
    priority_number: str = None
    title: str = None
    applicant: str = None
    claims: str = None
    locator: str = None
    quote: str = None
    confidence: float = 1.0

    def number_keys(self):
        """返回用于同族归并的规范化键集合。"""
        keys = []
        for value in (self.application_number, self.priority_number):
            if value:
                n = normalize_patent_number(value)
                if n["normalized"]:
                    keys.append(n["normalized"])
        # 公开号单独兜底：仅在没有申请号/优先权号时参与连接。
        if not keys and self.publication_number:
            n = normalize_patent_number(self.publication_number)
            if n["normalized"]:
                keys.append(n["normalized"])
        return keys


@dataclass
class Evidence:
    locator: str = ""
    quote: str = ""
    note: str = ""
    source_conflict: bool = False


def record_to_ir(item):
    if isinstance(item, PatentRecord):
        return item
    d = item if isinstance(item, dict) else {}
    return PatentRecord(
        record_id=d.get("record_id", ""),
        publication_number=_first(d.get("publication_number")),
        application_number=_first(d.get("application_number")),
        priority_number=_first(d.get("priority_number")),
        title=d.get("title"),
        applicant=d.get("applicant"),
        claims=d.get("claims"),
        locator=d.get("locator"),
        quote=d.get("quote"),
        confidence=float(d.get("confidence", 1.0) or 1.0),
    )


def _first(value):
    """兼容 dict{raw,normalized}、list、str 三种字段形态。"""
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get("raw") or value.get("normalized")
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


# --------------------------------------------------------------------------- #
# 同族归并（并查集传递闭包）
# --------------------------------------------------------------------------- #

def group_families(records):
    """按共用申请号/优先权号做传递闭包归并，返回 families 列表。"""
    recs = [record_to_ir(r) for r in (records or [])]
    n = len(recs)
    if n == 0:
        return {"families": [], "observed": True, "exhaustive": False,
                "algorithm": "union-find-transitive-closure"}

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

    # key -> 记录下标集合；记录 basis（键来源字段）。
    key_map = {}

    def add_key(key, idx, fieldname):
        if key:
            key_map.setdefault(key, []).append((idx, fieldname))

    for i, rec in enumerate(recs):
        for value, fieldname in (
            (rec.application_number, "application_number"),
            (rec.priority_number, "priority_number"),
        ):
            norm = normalize_patent_number(value) if value else None
            if norm and norm["normalized"]:
                add_key(norm["normalized"], i, fieldname)

    for key, hits in key_map.items():
        first_idx = hits[0][0]
        for idx, _f in hits[1:]:
            union(first_idx, idx)

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    families = []
    for root in sorted(groups):
        idxs = groups[root]
        basis = []
        seen = set()
        for key, hits in key_map.items():
            hit_idx = {h[0] for h in hits}
            if set(idxs) & hit_idx:
                for idx, f in hits:
                    if idx in idxs and f not in seen:
                        seen.add(f)
                        basis.append(f)
        family = {
            "family_id": "F%03d" % len(families),
            "members": [
                {
                    "record_id": recs[i].record_id or ("R%03d" % i),
                    "publication_number": recs[i].publication_number,
                    "application_number": recs[i].application_number,
                    "priority_number": recs[i].priority_number,
                } for i in idxs
            ],
            "family_type": "observed",
            "exhaustive": False,
            "basis": sorted(seen) or ["singleton"],
        }
        families.append(family)

    return {"families": families, "observed": True, "exhaustive": False,
            "algorithm": "union-find-transitive-closure"}


# --------------------------------------------------------------------------- #
# 骨架构建与契约回填
# --------------------------------------------------------------------------- #

def _contract_field_map(contract):
    """从 output_contract 提取需回填的字段。

    支持两种形态：
      * 扁平字段表（不含 type/properties）——逐键回填其默认值；
      * JSON-Schema（含 properties）——提取 properties 的键，缺省值给 null。
    """
    if not isinstance(contract, dict):
        return {}
    if "properties" not in contract and "type" not in contract:
        return {k: v for k, v in contract.items()}
    props = contract.get("properties")
    if isinstance(props, dict):
        return {k: None for k in props}
    return {}


def build_skeleton(case_obj, contract):
    case_id = None
    if isinstance(case_obj, dict):
        case_id = (case_obj.get("case_id") or case_obj.get("id")
                   or case_obj.get("caseId"))
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
        "runtime": {"started_at": now_iso()},
    }

    contract_keys = []
    for k, v in _contract_field_map(contract).items():
        result.setdefault(k, v if v is not None else None)
        contract_keys.append(k)
    result["_contract_keys"] = sorted(contract_keys)

    result["change_log"].append({
        "at": now_iso(), "event": "scaffold",
        "detail": "partial skeleton written; contract fields echoed",
    })
    if case_id == "unknown":
        result["uncertainty"].append({
            "item": "case_id_unknown",
            "reason": "no case_id found in case.json",
        })
    return result


def scaffold(input_dir, output_dir, work_dir):
    """发现输入、读 case.json、回填契约、落盘 partial 骨架。"""
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    files = discover_input(input_dir)
    case_obj = None
    case_path = None
    case_err = None
    for f in files:
        if f["kind"] == "case":
            case_path = f["path"]
            case_obj, case_err = load_json(f["path"])
            break

    contract = None
    if isinstance(case_obj, dict):
        contract = case_obj.get("output_contract") or case_obj.get("outputContract")

    result = build_skeleton(case_obj, contract)
    if case_path is not None and not isinstance(case_obj, dict):
        result["errors"].append({
            "item": "case_json_unreadable",
            "reason": case_err["error"] if case_err else "case.json is not an object",
        })
        result["degradations"].append({
            "item": "case_json_fallback",
            "reason": "proceeded with case_id=unknown and default schema",
        })

    inventory = []
    for f in files:
        if f["kind"] in ("pdf", "image"):
            inventory.append({"rel": f["rel"], "kind": f["kind"],
                              "size": f["size"],
                              "note": "binary; read via platform tools"})
        elif f["kind"] in ("text", "table", "data"):
            text, enc, err = read_text(f["path"])
            inventory.append({"rel": f["rel"], "kind": f["kind"],
                              "size": f["size"], "encoding": enc,
                              "chars": len(text), "read_error": err})
        else:
            inventory.append({"rel": f["rel"], "kind": f["kind"],
                              "size": f["size"]})
    result.setdefault("input_inventory", inventory)

    if not files:
        result["status"] = "insufficient_evidence"
        result["uncertainty"].append({
            "item": "no_input_files",
            "reason": "input directory has no discoverable files",
        })

    out_path = os.path.join(output_dir, "result.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    state = {
        "case_path": case_path,
        "input_files": [f["rel"] for f in files],
        "contract": contract,
        "contract_keys": result["_contract_keys"],
        "started_at": result["runtime"]["started_at"],
    }
    with open(os.path.join(work_dir, "state.json"), "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)

    return result, out_path


# --------------------------------------------------------------------------- #
# 校验（固定顺序）与收尾
# --------------------------------------------------------------------------- #

def validate_result(result):
    problems = []
    if not isinstance(result, dict):
        return False, ["result is not an object"]
    for k in REQUIRED_KEYS:
        if k not in result:
            problems.append("missing top-level key: %s" % k)
    if result.get("status") not in ALLOWED_STATUS:
        problems.append("invalid status: %r" % result.get("status"))
    if not result.get("case_id"):
        problems.append("case_id empty")
    for k in result.get("_contract_keys", []):
        if k not in result:
            problems.append("output_contract field missing: %s" % k)
    for i, e in enumerate(result.get("evidence", []) or []):
        if not isinstance(e, dict) or not e.get("locator") or "quote" not in e:
            problems.append("evidence[%d] missing locator/quote" % i)
    for i, r in enumerate(result.get("patent_records", []) or []):
        if not isinstance(r, dict) or not r.get("record_id"):
            problems.append("patent_records[%d] missing record_id" % i)
    rt = result.get("runtime")
    if not isinstance(rt, dict) or not rt.get("started_at"):
        problems.append("runtime.started_at missing")
    return (len(problems) == 0, problems)


def assemble_report(result):
    """确定性生成 report.md 文本。"""
    lines = ["# 专利与技术文献检索报告", ""]
    lines.append("- case_id: `%s`" % result.get("case_id"))
    lines.append("- status: `%s`" % result.get("status"))
    lines.append("- research_question: `%s`" % result.get("research_question"))
    lines.append("- retrieval_scope: `%s`" % result.get("retrieval_scope"))
    lines.append("")
    for key, label in (
        ("patent_records", "专利记录"), ("families", "同族"),
        ("legal_status", "法律状态"), ("literature", "技术文献"),
        ("evidence", "证据"), ("sources", "检索记录"),
        ("uncertainty", "不确定性"), ("pending_human", "待人工复核"),
        ("errors", "错误"), ("degradations", "降级"),
        ("limitations", "限制"),
    ):
        val = result.get(key) or []
        lines.append("## %s（%d 条）" % (label, len(val)))
        if val:
            for i, item in enumerate(val):
                lines.append("- `%s[%d]` %s" % (key, i, json.dumps(
                    item, ensure_ascii=False)[:300]))
        lines.append("")
    lines.append("## change_log")
    for cl in result.get("change_log", []) or []:
        lines.append("- `%s` %s" % (cl.get("at"), cl.get("event")))
    lines.append("")
    lines.append("> 本报告由 finalize 确定性生成；以 result.json 为准。")
    return "\n".join(lines) + "\n"


def finalize(output_dir, status=None):
    path = os.path.join(output_dir, "result.json")
    if not os.path.exists(path):
        return (False, None, path, ["result.json not found"])
    result, err = load_json(path)
    if err:
        return (False, None, path, [err["error"]])
    if not isinstance(result, dict):
        return (False, None, path, ["result.json is not an object"])
    if status:
        result["status"] = status

    rt = result.setdefault("runtime", {})
    started = rt.get("started_at") or now_iso()
    finished = now_iso()
    rt["started_at"] = started
    rt["finished_at"] = finished
    try:
        s0 = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        s1 = datetime.fromisoformat(str(finished).replace("Z", "+00:00"))
        rt["elapsed_seconds"] = round(max(0.0, (s1 - s0).total_seconds()), 1)
    except ValueError:
        rt["elapsed_seconds"] = None

    result.setdefault("change_log", []).append({
        "at": finished, "event": "finalize",
        "detail": "runtime completed and report.md generated",
    })

    ok, problems = validate_result(result)

    with open(os.path.join(output_dir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(assemble_report(result))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    return (ok, result, path, problems)
