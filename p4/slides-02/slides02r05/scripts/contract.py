#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""契约发现与 result.json 字段对齐（仅 Python 标准库，无第三方依赖）。

本模块只负责“输出契约”这一件事，是整个 Skill 的核心差异点：
1. 从 case.json 发现 output_contract（兼容多种键名与结构）；
2. 按契约构建/对齐 result.json 骨架：字段逐一对齐，缺值显式给 null/[]/{}；
3. 校验 result.json 是否覆盖契约全部字段、保留字段是否齐全；
4. 增量合并：已有字段不覆盖、未知键不删除，保证重复运行结果一致。

可用作独立脚本调试：python3 scripts/contract.py /input/case.json
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# 无论契约如何，result.json 顶层都保留这些字段（保留字段）。
RESERVED_FIELDS = (
    "case_id",
    "status",
    "sources",
    "uncertainty",
    "pending_human",
    "change_log",
    "errors",
    "degradations",
    "runtime",
)

STATUS_VALUES = (
    "partial",
    "complete",
    "failed",
    "insufficient_evidence",
    "input_error",
)

# case.json 缺失 output_contract 时使用；字段名来自题目“交付要求”与运行契约。
DEFAULT_CONTRACT = {
    "presentation_file": "string",
    "preview_files": "array",
    "speaker_notes": "object",
    "page_plan": "object",
    "evidence_map": "array",
    "version_records": "array",
}

# 可能的契约键名，按优先级排列。
CONTRACT_KEYS = ("output_contract", "outputContract", "contract")


def utcnow() -> str:
    """UTC 时间戳（ISO 8601，秒精度，用于 change_log/runtime）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _reconfigure_stdio() -> None:
    """Windows 控制台默认编码可能非 UTF-8，输出中文前先兜底。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def load_json(path):
    """读取 JSON，容错 BOM/坏编码/坏 JSON。

    返回 (data, err)：err 为 None 表示成功；"missing" 表示文件不存在；
    "bad_json" 表示不是合法 JSON；"unreadable" 表示读盘失败。
    """
    p = Path(path)
    if not p.exists():
        return None, "missing"
    try:
        raw = p.read_bytes()
    except OSError:
        return None, "unreadable"
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = None
    for enc in ("utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("latin-1", errors="replace")
    try:
        return json.loads(text), None
    except ValueError:
        return None, "bad_json"


def _is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, (str, list, dict, tuple, set)) and len(value) == 0:
        return True
    return False


def _case_id(case) -> str:
    if isinstance(case, dict):
        for key in ("case_id", "id", "caseId", "caseID"):
            value = case.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()
    return ""


def empty_value(hint) -> object:
    """按契约声明的类型提示给缺省空值。"""
    if not isinstance(hint, str):
        hint = str(hint)
    h = hint.lower()
    if "array" in h or "list" in h:
        return []
    if "object" in h or "dict" in h or "map" in h:
        return {}
    return None


def discover_contract(case):
    """从 case 中发现 output_contract，返回 (contract_dict, note)。

    contract_dict 形如 {字段名: 类型提示}；类型提示用于决定缺省空值。
    支持三种形状：字段名数组、{字段: 类型} 对象、含 "fields" 键的对象。
    """
    if not isinstance(case, dict):
        return dict(DEFAULT_CONTRACT), "case.json 非对象，使用默认契约"
    for key in CONTRACT_KEYS:
        raw = case.get(key)
        if raw is None:
            continue
        return _normalize_contract(raw), f"从 case.json 的 {key} 发现契约"
    return dict(DEFAULT_CONTRACT), "case.json 未声明 output_contract，使用默认契约"


def _normalize_contract(raw):
    if isinstance(raw, list):
        return {str(f): "any" for f in raw if str(f).strip()}
    if isinstance(raw, dict):
        if isinstance(raw.get("fields"), (list, dict)):
            return _normalize_contract(raw["fields"])
        out = {}
        for field, hint in raw.items():
            if isinstance(field, (str, int)):
                out[str(field)] = str(hint) if hint is not None else "any"
        return out
    return dict(DEFAULT_CONTRACT)


def _runtime_default() -> dict:
    return {
        "started_at": None,
        "finished_at": None,
        "budget_seconds": 900,
        "budget_requests": 64,
        "max_tokens_per_response": 16384,
        "contract_source": "default",
    }


def _uncertainty_default() -> dict:
    return {"unresolved": [], "missing_artifacts": [], "notes": ""}


def align(existing, contract, case) -> dict:
    """增量对齐：以 existing 为底，补齐缺字段，不覆盖已有值、不删未知键。"""
    existing = existing if isinstance(existing, dict) else {}
    out = {}

    def take(name, default):
        return existing[name] if name in existing else default

    out["case_id"] = take("case_id", _case_id(case))
    out["status"] = take("status", "partial")
    for name in contract:
        out[name] = take(name, empty_value(contract[name]))
    out["sources"] = take("sources", [])
    out["uncertainty"] = take("uncertainty", _uncertainty_default())
    out["pending_human"] = take("pending_human", [])
    out["change_log"] = take("change_log", [])
    out["errors"] = take("errors", [])
    out["degradations"] = take("degradations", [])
    out["runtime"] = take("runtime", _runtime_default())
    for key, value in existing.items():
        if key not in out:
            out[key] = value
    return out


def nonempty_gaps(result, contract) -> list:
    """返回“必须非空但当前为空”的字段名（用于判断是否可标 complete）。"""
    if not isinstance(result, dict):
        return ["result.json"]
    gaps = []
    for name in ("case_id", "status"):
        if _is_empty(result.get(name)):
            gaps.append(name)
    for name in contract:
        if _is_empty(result.get(name)):
            gaps.append(name)
    return gaps


def validate(result, contract):
    """校验 result.json 结构。返回 (ok, issues, fields_report)。

    ok=False 仅表示“结构缺口”（缺字段/非法 status/缺 case_id）；
    字段为空的提示放在 fields_report 的 empty 标记里，由调用方决定是否阻断。
    """
    fields = []
    issues = []

    if not isinstance(result, dict):
        return False, ["result.json 不是 JSON 对象"], fields

    def mark(name, required, allow_empty):
        present = name in result
        value = result.get(name)
        empty = _is_empty(value)
        ok = present and (allow_empty or not empty)
        fields.append({
            "field": name,
            "required": required,
            "present": present,
            "empty": empty,
            "value_kind": _kind(value),
            "ok": ok,
        })
        if required and not present:
            issues.append(f"缺契约/必填字段: {name}")
        elif required and not empty is False and not allow_empty and empty:
            issues.append(f"必填字段为空: {name}")
        return ok

    mark("case_id", True, False)
    mark("status", True, False)
    if result.get("status") not in STATUS_VALUES:
        issues.append(f"非法 status: {result.get('status')!r}")

    for name in contract:
        mark(name, True, True)  # 契约字段“出现”即可；是否为空由 nonempty_gaps 判断

    for name in RESERVED_FIELDS:
        if name in ("case_id", "status"):
            continue
        mark(name, False, True)

    if not isinstance(result.get("change_log"), list):
        issues.append("change_log 必须是数组")
    if not isinstance(result.get("sources"), list):
        issues.append("sources 必须是数组")

    return len(issues) == 0, issues, fields


def _kind(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    return "str"


def main(argv=None) -> int:
    _reconfigure_stdio()
    args = list(sys.argv[1:] if argv is None else argv)
    path = args[0] if args else "/input/case.json"
    case, err = load_json(path)
    contract, note = discover_contract(case)
    skeleton = align({}, contract, case)
    print(json.dumps({
        "path": str(path),
        "load_error": err,
        "contract_note": note,
        "contract_fields": list(contract.keys()),
        "skeleton_top_level_fields": list(skeleton.keys()),
        "reserved_fields": list(RESERVED_FIELDS),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
