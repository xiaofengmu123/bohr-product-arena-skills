# -*- coding: utf-8 -*-
"""core_impl.py — 纯逻辑实现（无 CLI、无墙钟副作用）。

设计取舍（与“三脚本流程”的参考实现明显不同）：
1. 单一中间表示：所有事实、来源、冲突、修改记录集中进一份版本化的
   `work/ledger.json`（证据台账）。`evidence_map.json` 与 `change_log.json`
   不再由 Agent 手工维护，而是由 verify 从台账**派生**，避免两份记录漂移。
2. 合同先行：`discover` 先把 case.json 的 output_contract 归一化为
   `work/contract_spec.json`，`scaffold` 从规格生成骨架，保证合同字段一个不漏；
   `verify` 先校验合同合规，再校验内容完整性。
3. 确定性：骨架不含墙钟时间、键序固定（sort_keys）、材料扫描按字典序，
   同一输入两次运行字节一致。运行时只记录在 work/ 侧，不污染 result.json。

仅依赖 Python 3.9+ 标准库。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys

__all__ = [
    "MAX_MATERIAL_BYTES",
    "CONTRACT_KEYS",
    "STANDARD_FIELDS",
    "STATUSES",
    "ENCODINGS",
    "read_text",
    "load_json",
    "dumps",
    "write_text",
    "ensure_dir",
    "stable_id",
    "find_case_id",
    "find_contract",
    "normalize_contract",
    "placeholder",
    "scan_input",
    "initial_status",
    "emit_skeleton",
    "validate_result",
    "derive_evidence_map",
    "derive_change_log",
    "derive_preserved_facts",
    "determine_status",
    "empty_ledger",
]

MAX_MATERIAL_BYTES = 2 * 1024 * 1024  # 单个材料 2MB 上限
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")

# output_contract 的发现优先级（先顶层、再递归嵌套）。
CONTRACT_KEYS = (
    "output_contract",
    "output",
    "expected_output",
    "fields",
    "required_fields",
    "result_fields",
    "contract",
    "output_schema",
)

# result.json 的标准附加字段（合同字段同名时不覆盖合同，case_id/status 除外）。
STANDARD_FIELDS = (
    "sources",
    "uncertainty",
    "change_log",
    "artifacts",
    "pending_human",
    "errors",
    "degradations",
    "runtime",
    "contract_fields",
)

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")


# --------------------------------------------------------------------------- #
# 基础 IO（容错、确定性）
# --------------------------------------------------------------------------- #
def ensure_dir(path: str) -> str:
    if path:
        os.makedirs(path, exist_ok=True)
    return path


def read_text(path: str, max_bytes: int = MAX_MATERIAL_BYTES):
    """返回 (text|None, size|None, error|None)。超限/坏编码均降级不抛异常。"""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, None, "unreadable:%s" % exc
    if size > max_bytes:
        return None, size, "too_large"
    for enc in ENCODINGS:
        try:
            with open(path, "r", encoding=enc, errors="strict") as fh:
                return fh.read(), size, None
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError as exc:
            return None, size, "read_error:%s" % exc
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        return raw.decode("utf-8", "replace"), size, "replaced_encoding"
    except OSError as exc:
        return None, size, "read_error:%s" % exc


def load_json(path: str, default=None):
    """返回 (obj, error_dict|None)。坏 JSON 返回 default 并附错误。"""
    text, size, err = read_text(path)
    if err:
        return default, {"path": path, "error": err, "bytes": size}
    if text is None:
        return default, {"path": path, "error": err or "empty"}
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return default, {"path": path, "error": "bad_json:%s" % exc, "bytes": size}


def dumps(obj) -> str:
    """确定性序列化：固定键序、保留中文、末尾换行。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def write_text(path: str, text: str) -> str:
    d = os.path.dirname(path)
    if d:
        ensure_dir(d)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def stable_id(text: str) -> str:
    """由内容生成稳定短 ID，用于 claim/source 的确定性编号。"""
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:10]


# --------------------------------------------------------------------------- #
# 输入发现：case_id / 合同 / 材料清单
# --------------------------------------------------------------------------- #
def find_case_id(case_obj) -> object:
    if isinstance(case_obj, dict):
        for key in ("case_id", "id", "challenge_id", "task_id", "caseId"):
            value = case_obj.get(key)
            if value not in (None, "", [], {}):
                return value
    return None


def _find_contract_node(obj, depth: int = 0):
    if depth > 6 or not isinstance(obj, dict):
        return None
    for key in CONTRACT_KEYS:
        if key in obj and isinstance(obj[key], (dict, list, str)):
            return obj[key]
    for value in obj.values():
        if isinstance(value, dict):
            found = _find_contract_node(value, depth + 1)
            if found is not None:
                return found
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    found = _find_contract_node(item, depth + 1)
                    if found is not None:
                        return found
    return None


def find_contract(case_obj):
    if not isinstance(case_obj, dict):
        return None
    return _find_contract_node(case_obj)


def _kind_of(value) -> str:
    if isinstance(value, dict):
        return "dict"
    if isinstance(value, list):
        return "list"
    if isinstance(value, (bool, int, float, str)) or value is None:
        return "str"
    return "unknown"


def normalize_contract(contract) -> list:
    """归一化合同为 [{name, kind}]。kind ∈ str|list|dict|unknown。"""
    fields = []
    if isinstance(contract, dict):
        for key, value in contract.items():
            fields.append({"name": key, "kind": _kind_of(value)})
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str) and item.strip():
                fields.append({"name": item.strip(), "kind": "unknown"})
            elif isinstance(item, dict):
                for key, value in item.items():
                    fields.append({"name": key, "kind": _kind_of(value)})
    elif isinstance(contract, str) and contract.strip():
        fields.append({"name": contract.strip(), "kind": "unknown"})
    return fields


def placeholder(kind: str):
    if kind == "list":
        return []
    if kind == "dict":
        return {}
    return None


def _file_kind(name: str, rel: str) -> str:
    low = name.lower()
    if name == "case.json":
        return "case"
    if name == "task.md":
        return "task"
    if low.endswith(".pdf"):
        return "pdf"
    if low.endswith(".bib"):
        return "bib"
    if low.endswith(".md") or low.endswith(".markdown"):
        return "markdown"
    if low.endswith(".json"):
        return "json"
    if low.endswith((".csv", ".tsv")):
        return "table"
    if low.endswith(".txt"):
        return "text"
    if low.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")):
        return "image"
    if low.endswith((".docx", ".pptx", ".xlsx")):
        return "office"
    return "other"


def scan_input(input_dir: str, max_bytes: int = MAX_MATERIAL_BYTES) -> dict:
    """扫描输入目录，返回确定性材料清单。空目录/缺目录也返回合法清单。"""
    manifest = {
        "input_dir": input_dir,
        "files": [],
        "degradations": [],
        "case_file": None,
        "task_file": None,
    }
    if not os.path.isdir(input_dir):
        manifest["degradations"].append({"locator": input_dir, "issue": "input_dir_missing"})
        return manifest
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        files.sort()
        for name in files:
            path = os.path.join(root, name)
            rel = os.path.relpath(path, input_dir).replace("\\", "/")
            try:
                size = os.path.getsize(path)
            except OSError as exc:
                manifest["degradations"].append({"locator": rel, "issue": "stat_error:%s" % exc})
                continue
            too_large = size > max_bytes
            entry = {
                "locator": rel,
                "path": path,
                "bytes": size,
                "kind": _file_kind(name, rel),
                "too_large": too_large,
            }
            manifest["files"].append(entry)
            if name == "case.json":
                manifest["case_file"] = rel
            if name == "task.md":
                manifest["task_file"] = rel
            if too_large:
                manifest["degradations"].append(
                    {"locator": rel, "issue": "too_large", "bytes": size, "limit": max_bytes}
                )
    return manifest


# --------------------------------------------------------------------------- #
# 骨架生成与台账
# --------------------------------------------------------------------------- #
def empty_ledger(case_id) -> dict:
    return {
        "schema_version": 1,
        "case_id": case_id,
        "claims": [],
        "sources": [],
        "changes": [],
        "conflicts": [],
    }


def initial_status(case_id, manifest, case_error=None) -> str:
    """由输入状态给出骨架的诚实初始 status。确定性。"""
    files = (manifest or {}).get("files", [])
    has_case_file = any(f.get("kind") == "case" for f in files)
    material = [f for f in files if f.get("kind") not in ("case", "task")]
    if case_error is not None:
        return "input_error"
    if not has_case_file and not material:
        return "insufficient_evidence"
    if case_id is None and not material:
        return "insufficient_evidence"
    return "partial"


def emit_skeleton(case_id, contract_fields: list, status: str = "partial") -> dict:
    """从合同规格生成 result.json 骨架，合同字段全量占位。确定性。"""
    result = {}
    for field in contract_fields or []:
        result[field.get("name")] = placeholder(field.get("kind", "unknown"))
    # case_id / status 是元数据，须为真实值，即使合同恰有同名字段。
    result["case_id"] = case_id
    result["status"] = status if status in STATUSES else "partial"
    std = {
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "artifacts": {},
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "runtime": {"start_epoch": None, "seconds_elapsed": 0, "model_requests_used": None},
        "contract_fields": [f.get("name") for f in (contract_fields or [])],
    }
    for key, value in std.items():
        result.setdefault(key, value)
    return result


# --------------------------------------------------------------------------- #
# 校验与派生
# --------------------------------------------------------------------------- #
def derive_evidence_map(ledger: dict) -> dict:
    """从台账 sources 派生 evidence_map.json。"""
    mapping = {}
    for source in (ledger or {}).get("sources", []):
        key = source.get("key")
        if key:
            mapping[key] = {
                "locator": source.get("locator"),
                "quote": source.get("quote"),
                "kind": source.get("kind"),
                "status": source.get("status", "supported"),
            }
    return mapping


def derive_change_log(ledger: dict) -> list:
    """从台账 changes 派生 change_log.json。"""
    return list((ledger or {}).get("changes", []))


def derive_preserved_facts(ledger: dict) -> list:
    """从台账派生 preserved_facts.json：数值/单位保全清单（含逐字 quote）。"""
    out = []
    for claim in (ledger or {}).get("claims", []):
        quote = claim.get("quote") or ""
        if claim.get("kind") == "number" or re.search(r"\d", quote):
            out.append({
                "id": claim.get("id"),
                "section": claim.get("section"),
                "locator": claim.get("locator"),
                "quote": quote,
                "value": claim.get("claim"),
            })
    return out


def _manuscript_anchors(text: str) -> set:
    if not text:
        return set()
    return {m[1:-1] for m in re.findall(r"\[S\d+\]", text)}


def validate_result(result: dict, contract_fields: list, ledger=None, manuscript_text=None) -> dict:
    """校验 result.json 与稿件/台账的一致性。errors 为硬伤，warnings 为待补。"""
    report = {"errors": [], "warnings": [], "ok": False}

    # 1) 合同字段回填：缺一即硬伤。
    names = {f.get("name") for f in (contract_fields or [])}
    for name in sorted(names):
        if name not in result:
            report["errors"].append("missing_contract_field:%s" % name)

    # 2) case_id 与 status。
    if result.get("case_id") in (None, "", [], {}):
        report["warnings"].append("case_id_missing")
    if result.get("status") not in STATUSES:
        report["errors"].append("invalid_status")

    # 3) 标准字段存在性（软提醒）。
    for key in ("sources", "uncertainty", "change_log", "pending_human", "errors", "degradations"):
        if key not in result:
            report["warnings"].append("missing_standard_field:%s" % key)

    # 4) 稿件四段结构与证据锚点。
    if manuscript_text:
        for marker in ("研究背景", "相关工作", "研究空白", "贡献"):
            if marker not in manuscript_text:
                report["warnings"].append("manuscript_missing_section:%s" % marker)
        anchors = _manuscript_anchors(manuscript_text)
        source_keys = {s.get("key") for s in ((ledger or {}).get("sources") or [])}
        for anchor in sorted(anchors):
            if anchor not in source_keys:
                report["warnings"].append("unmapped_anchor:%s" % anchor)

    # 5) 台账质量：来源必须有逐字 quote + locator。
    if ledger is not None:
        for source in ledger.get("sources", []):
            if not source.get("quote"):
                report["warnings"].append("source_missing_quote:%s" % source.get("key", "?"))
            if not source.get("locator"):
                report["warnings"].append("source_missing_locator:%s" % source.get("key", "?"))

    report["ok"] = not report["errors"]
    return report


def determine_status(has_materials: bool, ledger, manuscript_text: str, had_input_error: bool) -> str:
    if had_input_error and not has_materials and not (ledger or {}).get("sources"):
        return "input_error"
    if not has_materials:
        return "insufficient_evidence"
    sources = (ledger or {}).get("sources", [])
    claims = (ledger or {}).get("claims", [])
    if manuscript_text and sources and len(claims) >= 4:
        open_gaps = [c for c in claims if c.get("status") == "gap"]
        if not open_gaps:
            return "ok"
    return "partial"
