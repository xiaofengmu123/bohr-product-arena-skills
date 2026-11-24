#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rules.py — 纯函数规则：模板、状态语义、契约回填与机械校验。

不读写文件、不依赖外部工具；仅提供可离线单测的纯逻辑，
供 main.py / libcore.py 调用。所有输出必须字典序/列表序稳定，
保证同输入两次运行除 runtime 外逐字节一致。
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata

# ------------------------------------------------------------------ 状态语义
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
CLAIM_KINDS = ("alias", "version", "metadata", "correction", "query_answer",
               "operation", "conflict", "recovery")

# 输出契约的兜底字段：case.json 无 output_contract 时按此回填，
# 对应题面「资料结果 / 操作证据 / 失败与恢复」三张交付表。
DEFAULT_OUTPUT_CONTRACT = (
    "inventory", "metadata", "versions", "duplicates", "conflicts",
    "operations", "change_log", "search", "evidence_map", "sources",
)

# 信封字段：与 output_contract 字段合并后写入 result.json。
ENVELOPE_KEYS = (
    "case_id", "status", "result", "claims", "uncertainties",
    "sources", "uncertainty", "change_log", "pending_human",
    "errors", "degradations", "runtime",
)
_LIST_ENVELOPE = frozenset((
    "claims", "uncertainties", "sources", "change_log",
    "pending_human", "errors", "degradations",
))

# 时间预算预案（秒）；模型请求预算 64 次，每次含推理 ≤16384 token。
BUDGET_SECONDS = 900
MODEL_REQUESTS = 64
EARLY_SKELETON_SECONDS = 180

PHASE_PLAN = [
    {"name": "init",      "limit": 60,  "self_deliver": True,  "desc": "扫描 /input，立即写 partial 骨架"},
    {"name": "show",      "limit": 30,  "self_deliver": False, "desc": "打印带 locator 的盘点"},
    {"name": "draft",     "limit": 700, "self_deliver": True,  "desc": "模型只补语义解释，每条带证据"},
    {"name": "check",     "limit": 30,  "self_deliver": False, "desc": "机械校验 draft 结构 + 逐字命中"},
    {"name": "build",     "limit": 60,  "self_deliver": True,  "desc": "合并确定性结果，无条件收口"},
    {"name": "selfcheck", "limit": 30,  "self_deliver": False, "desc": "复检 /app/output 产物"},
]

# ------------------------------------------------------------------ 模板
DRAFT_TEMPLATE = {
    "case_id": "",
    "notes": [],   # {target, kind, text, basis, confidence, evidence[]}
    "queries": [],  # {query_id, answer, status, evidence[]}
    "operations": [],  # 模型对不确定操作的裁定/注释，含 evidence
}

NOTE_TEMPLATE = {
    "target": "F001", "kind": "alias", "text": "", "basis": "",
    "confidence": "explicit",  # explicit | inferred | heuristic
    "evidence": [],  # {source_id, locator, quote}
}

QUERY_TEMPLATE = {
    "query_id": "Q1", "answer": "",
    "status": "answered",  # answered | no_direct_match | insufficient_evidence
    "evidence": [],
}

CHECK_REPORT_TEMPLATE = {
    "ok": True,
    "errors": [],
    "warnings": [],
    "note_count": 0,
    "query_count": 0,
}

# ------------------------------------------------------------------ 文本归一
def nfkc(s: str) -> str:
    """NFKC 归一 + 去零宽字符，用于跨平台 determinism。"""
    if not isinstance(s, str):
        s = str(s)
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\ufeff", "").replace("\u200b", "")
    return s


def canonical_quote(q: str) -> str:
    """证据 quote 的逐字判等前的归一分词：去掉空白差异，保留字符本身。"""
    if q is None:
        return ""
    return re.sub(r"\s+", " ", nfkc(q)).strip()


def normalize_stem(name: str) -> str:
    """归一化 stem，用于候选版本分组（宽松：去常见版本/副本/日期噪声）。

    仅作为启发式分组键，不作为确认；确认层级由 draft + 证据决定。
    """
    s = nfkc(name)
    s = re.sub(r"\.[A-Za-z0-9]{1,8}$", "", s)          # 去扩展名
    s = re.sub(r"\([0-9]+\)", "", s)                     # Windows 副本 (1)
    s = re.sub(r"\bcopy\b|\b副本\b|\(\s*副本\s*\)", "", s, flags=re.I)
    s = re.sub(r"[\s._-]*v?\d+(?:\.\d+){0,3}[\s._-]*", " ", s, flags=re.I)  # v2 / 2.0
    s = re.sub(r"\b(final|draft|rev\d*|revision|latest|copy)\b", " ", s, flags=re.I)
    s = re.sub(r"[0-9]{4}[-_.]?[01]?[0-9][-_.]?[0-3]?[0-9]", " ", s)  # 日期
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", s.lower())
    s = re.sub(r"\s+", " ", s).strip()
    return s


def version_tokens(name: str) -> list:
    """从原名提取版本标记，用于判断是否存在可解释的版本顺序。"""
    s = nfkc(name)
    toks = re.findall(r"(?:v|rev|version|版本)\s*[\d]+(?:\.[\d]+)?", s, re.I)
    toks += re.findall(r"\b(final|draft|latest)\b", s, re.I)
    return [t.lower() for t in toks]


# ------------------------------------------------------------------ 证据
def evidence_ok(ev: dict) -> bool:
    if not isinstance(ev, dict):
        return False
    if not isinstance(ev.get("source_id"), str) or not ev["source_id"]:
        return False
    if not isinstance(ev.get("locator", ""), str) or not ev["locator"]:
        return False
    return "quote" in ev


def quote_in_text(quote: str, text: str) -> bool:
    """quote 是否在 text 中逐字出现（忽略空白差异）。缺失 quote 视为不可验证。"""
    q = canonical_quote(quote)
    if not q:
        return False
    t = re.sub(r"\s+", " ", nfkc(text))
    return q in t


# ------------------------------------------------------------------ 契约
def extract_contract(case) -> list:
    """output_contract 支持 dict 与 list 两种形态；缺失返回兜底列表。"""
    if not isinstance(case, dict):
        return list(DEFAULT_OUTPUT_CONTRACT)
    raw = case.get("output_contract")
    keys = []
    if isinstance(raw, dict):
        keys = [str(k) for k in raw.keys()]
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                for cand in ("name", "key", "field", "field_id", "id"):
                    if isinstance(item.get(cand), str):
                        keys.append(item[cand])
                        break
    if not keys:
        keys = list(DEFAULT_OUTPUT_CONTRACT)
    return keys


def derive_case_id(case) -> str:
    if isinstance(case, dict):
        for key in ("case_id", "id", "case", "challenge_id"):
            v = case.get(key)
            if v not in (None, ""):
                return nfkc(str(v))
    return "unknown"


def build_skeleton(case_id: str, contract_keys: list, status: str = "partial") -> dict:
    """先落盘的 partial 骨架：契约字段 + 信封字段齐备。"""
    out = {}
    for key in contract_keys:
        out[key] = None
    for key in ENVELOPE_KEYS:
        out[key] = [] if key in _LIST_ENVELOPE else None
    out["case_id"] = case_id
    out["status"] = status
    out["result"] = {"summary": {}, "groups": {}, "query_results": [], "operation_results": []}
    out["uncertainty"] = {"summary": "", "count": 0, "items": []}
    out["runtime"] = {"budget_seconds": BUDGET_SECONDS, "model_requests_budget": MODEL_REQUESTS,
                      "early_skeleton_seconds": EARLY_SKELETON_SECONDS, "phases": []}
    return out


_CONTRACT_RESOLVERS = {}


def _resolver(name: str):
    def deco(fn):
        _CONTRACT_RESOLVERS[nfkc(name)] = fn
        return fn
    return deco


def resolve_contract_value(key: str, lib: dict, draft: dict, result: dict):
    """把 output_contract 中的一个键回填为确定性产物值。未知键 -> None。"""
    k = nfkc(key)
    fn = _CONTRACT_RESOLVERS.get(k)
    if fn is None:
        # 常见别名再尝试一次
        alias_map = {"files": "inventory", "file_list": "inventory", "versions": "version_groups",
                     "duplicates": "alias_groups", "queries": "query_results",
                     "answers": "query_results", "questions": "query_results",
                     "logs": "operations", "ops": "operations"}
        fn = _CONTRACT_RESOLVERS.get(alias_map.get(k, ""))
    if fn is None:
        return None
    return fn(lib, draft, result)


@_resolver("inventory")
def _inv(lib, draft, result):
    return lib.get("files", [])

@_resolver("metadata")
def _meta(lib, draft, result):
    return lib.get("analysis", {}).get("metadata", {})

@_resolver("versions")
def _versions(lib, draft, result):
    return lib.get("analysis", {}).get("version_groups", [])

@_resolver("duplicates")
def _dups(lib, draft, result):
    return lib.get("analysis", {}).get("alias_groups", [])

@_resolver("conflicts")
def _conf(lib, draft, result):
    return lib.get("analysis", {}).get("conflicts", [])

@_resolver("operations")
def _ops(lib, draft, result):
    return result.get("change_log", [])

@_resolver("search")
def _search(lib, draft, result):
    return result.get("result", {}).get("query_results", [])

@_resolver("evidence_map")
def _evmap(lib, draft, result):
    return result.get("evidence_map", [])

@_resolver("sources")
def _sources(lib, draft, result):
    return result.get("sources", [])

@_resolver("change_log")
def _cl(lib, draft, result):
    return result.get("change_log", [])


# ------------------------------------------------------------------ 机械校验
def _validate_note(n, idx: int, known_ids: set, corpus, report: dict) -> None:
    for field in ("target", "kind", "text"):
        if not n.get(field):
            report["errors"].append(f"note[{idx}].{field} 缺失")
    if n.get("kind") not in CLAIM_KINDS:
        report["errors"].append(f"note[{idx}].kind 非法: {n.get('kind')}")
    if n.get("confidence") not in ("explicit", "inferred", "heuristic"):
        report["errors"].append(f"note[{idx}].confidence 非法")
    evs = n.get("evidence") or []
    if not evs:
        report["errors"].append(f"note[{idx}] 缺少 evidence")
    for e in evs:
        if not evidence_ok(e):
            report["errors"].append(f"note[{idx}] evidence 结构不完整")
            continue
        sid = e["source_id"]
        if sid not in known_ids:
            report["errors"].append(f"note[{idx}] source_id={sid} 不存在")
            continue
        if sid in corpus and "quote" in e:
            if not quote_in_text(e["quote"], corpus[sid]):
                report["warnings"].append(f"note[{idx}] quote 未逐字命中 source_id={sid}")


def validate_quote_strict(quote: str, sid: str, corpus: dict) -> bool:
    """供 check 使用：quote 必须是材料中可定位的逐字片段。"""
    txt = corpus.get(sid)
    if txt is None:
        return False
    return quote_in_text(quote, txt)


def stable_dump(data) -> str:
    """稳定 JSON 序列化：ensure_ascii=False、缩进 2、正斜杠不转义、稳定字典序。"""
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"