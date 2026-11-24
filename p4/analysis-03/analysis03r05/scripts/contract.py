#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""输出契约对齐引擎（仅标准库）。

本模块是本题变体"输出契约完整"的核心实现：把 case.json 中的 output_contract
当作唯一事实来源，将其叶字段展平、回填、校验并报告填充进度，保证
/ app/output/result.json 与题目契约逐字段对齐，且不缺、不删、不改名、不改层级。

设计原则：
  * 契约键名不固定 —— 递归发现 output_contract/outputContract/contract/
    result_schema/expected_output 等别名；
  * 叶节点按类型三类：scalar（占位 null）、array（占位 []）、object（占位 {}）；
  * 回填只补齐缺失叶与类型不符叶，绝不覆盖 Agent 已写入的非空值；
  * 校验输出可机读 issue 列表，供 check 子命令与自检脚本直接消费。
"""
import re

RESERVED_KEYS = (
    "case_id", "status", "sources", "uncertainty", "pending_human",
    "change_log", "errors", "degradations", "runtime", "artifacts",
    "_contract_meta", "data_profile",
)

CONTRACT_KEY_ALIASES = (
    "output_contract", "outputcontract", "contract",
    "result_contract", "resultcontract", "result_schema",
    "resultschema", "expected_output", "expectedoutput",
)


def normalize_key(s):
    """把键名归一到小写字母数字，用于跨大小写/下划线/连字符匹配。"""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _alias_set():
    return {normalize_key(a) for a in CONTRACT_KEY_ALIASES}


def find_contract_candidates(obj, path=()):
    """递归发现所有疑似契约节点，返回 [(path_tuple, value), ...]。"""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            nk = normalize_key(k)
            p = path + (k,)
            if nk in _alias_set() and isinstance(v, dict):
                out.append((p, v))
            out.extend(find_contract_candidates(v, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(find_contract_candidates(v, path + ("[%d]" % i,)))
    return out


def flatten(obj, path=()):
    """把契约展平成叶节点列表 [(path_tuple, kind), ...]。

    kind ∈ {"scalar", "array", "object"}；空 dict 视为 object 叶，list 视为
    array 叶（数组内部元素结构未知，不逐元素展开）。
    """
    leaves = []
    if isinstance(obj, dict):
        if len(obj) == 0:
            leaves.append((path, "object"))
            return leaves
        for k, v in obj.items():
            leaves.extend(flatten(v, path + (k,)))
    elif isinstance(obj, list):
        leaves.append((path, "array"))
    else:
        leaves.append((path, "scalar"))
    return leaves


def path_str(path):
    """把键元组格式化为点分路径（键内不含 '.' 为前提；含点键用方括号回退）。"""
    parts = []
    for k in path:
        s = str(k)
        parts.append(s if "." not in s else "[%s]" % s)
    return ".".join(parts)


def empty_for(kind):
    """叶节点占位值。"""
    return {"scalar": None, "array": [], "object": {}}.get(kind, None)


def get_by_path(obj, path):
    """按键元组取路径值；返回 (value, found)。"""
    cur = obj
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None, False
        cur = cur[k]
    return cur, True


def set_by_path(obj, path, value):
    """按键元组写路径值，必要时补建中间 dict。"""
    cur = obj
    for k in path[:-1]:
        if not isinstance(cur.get(k), dict):
            cur[k] = {}
        cur = cur[k]
    cur[path[-1]] = value
    return obj


def discover_contract(case_obj):
    """从 case.json 对象中发现输出契约。

    返回 (contract, candidates, chosen_path)：
      * 无候选时 contract=None；
      * 多个候选时优先"浅层 + 叶节点多"，浅层优先于深层（输出契约通常在顶层）。
    """
    cands = find_contract_candidates(case_obj)
    if not cands:
        return None, [], None

    def depth(p):
        return len(p)

    cands_sorted = sorted(
        cands,
        key=lambda c: (depth(c[0]), -len(flatten(c[1]))),
    )
    chosen_path, contract = cands_sorted[0]
    return contract, cands_sorted, chosen_path


def backfill(result, contract):
    """把契约全部叶节点回填进 result，只补缺失/类型不符，不覆盖非空值。

    返回 (result, added_paths, leaves)。
    """
    leaves = flatten(contract)
    added = []
    for path, kind in leaves:
        val, ok = get_by_path(result, path)
        if not ok:
            set_by_path(result, path, empty_for(kind))
            added.append(path_str(path))
        elif kind == "array" and not isinstance(val, list):
            set_by_path(result, path, [])
            added.append(path_str(path) + " (array-fixed)")
        elif kind == "object" and not isinstance(val, dict):
            set_by_path(result, path, {})
            added.append(path_str(path) + " (object-fixed)")
    return result, added, leaves


def validate(result, contract):
    """校验 result 与契约的一致性，返回 issue 列表。

    每项 {"path", "issue"}；issue ∈ missing / expected_array / expected_object /
    extra_key。extra_key 仅检查顶层未在契约或保留键中的键。
    """
    issues = []
    leaves = flatten(contract)
    seen = set()
    for path, kind in leaves:
        ps = path_str(path)
        seen.add(path[0] if path else ps)
        val, ok = get_by_path(result, path)
        if not ok:
            issues.append({"path": ps, "issue": "missing"})
            continue
        if kind == "array" and not isinstance(val, list):
            issues.append({"path": ps, "issue": "expected_array"})
        elif kind == "object" and not isinstance(val, dict):
            issues.append({"path": ps, "issue": "expected_object"})
    if isinstance(result, dict):
        for k in result:
            if k not in seen and k not in RESERVED_KEYS:
                issues.append({"path": str(k), "issue": "extra_key"})
    return issues


def fill_report(result, contract):
    """统计契约填充进度：{total, filled, missing:[path,...]}。

    非空判断：值非 None、非空 list/dict 视为已填充。
    """
    leaves = flatten(contract)
    total = len(leaves)
    filled = 0
    missing = []
    for path, kind in leaves:
        val, ok = get_by_path(result, path)
        if not ok or val is None or (isinstance(val, (list, dict)) and len(val) == 0):
            missing.append(path_str(path))
        else:
            filled += 1
    return {"total": total, "filled": filled, "missing": missing}


def is_contract_complete(result, contract):
    """契约是否全部叶节点已填充非空。"""
    r = fill_report(result, contract)
    return r["total"] == r["filled"], r
