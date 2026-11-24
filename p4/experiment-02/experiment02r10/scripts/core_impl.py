#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — 实验方案可行性审查引擎（纯 Python 标准库，>=3.8）。

架构约定（本 Skill 的核心取舍）：
  1. IR-first：原始 /input 先归一到 case_record（中间表示），审查与算术只作用在 IR 上。
  2. 证据账本：EvidenceLedger 对 (locator, quote) 去重，同一定位只保留一份，供复核。
  3. 确定性：不写墙钟时间、不 import random、不依赖遍历顺序之外的随机性；相同输入产生相同骨架。

本模块不直接读写 /app/output；main.py 负责编排，selfcheck.py 负责分层校验。
"""

from __future__ import annotations

import argparse
import json
import os
import re

MAX_FILE_BYTES = 2 * 1024 * 1024
TEXT_EXTS = {".md", ".txt", ".json", ".jsonl", ".csv", ".tsv", ".yaml", ".yml", ".xml"}
CASE_NAMES = ("case.json", "case.jsonl")
CASE_ID_KEYS = ("case_id", "id", "challenge_id", "caseId", "armiId")
RESERVED_UNKNOWN = "unknown"

VERDICTS = ("ok", "conditional", "blocked", "unconfirmed", "missing", "conflict")
RESOURCE_STATES = ("已具备", "可外协", "需采购", "不具备", "未知")
STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
DIMENSIONS = (
    "assumptions_variables",
    "controls",
    "equipment_materials",
    "time_cost",
    "parameter_ranges",
    "risks_exceptions",
    "ethics_safety",
)


# --------------------------------------------------------------------------- #
# 文本与输入发现
# --------------------------------------------------------------------------- #
def decode_bytes(raw: bytes):
    """按 utf-8-sig → utf-8 → gb18030 → replace 兜底解码，返回 (text, warning)。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "fallback_decode(replace)"


def read_text(path: str, max_bytes: int = MAX_FILE_BYTES):
    """读取文本文件，返回 (text_or_None, warning_or_empty)。缺文件/超限/坏编码均降级。"""
    if not os.path.exists(path):
        return None, "missing"
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, "unreadable: %s" % exc
    if size > max_bytes:
        return None, "oversize(%d > %d)" % (size, max_bytes)
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, "unreadable: %s" % exc
    return decode_bytes(raw)


def discover_inputs(input_dir: str):
    """扫描输入目录，返回 (files, notes)。files 项含 relpath/abspath/size/ext/status。"""
    files, notes = [], []
    if not os.path.isdir(input_dir):
        return files, ["input_dir_missing: %s" % input_dir]
    for root, dirs, names in os.walk(input_dir):
        dirs.sort()
        for name in sorted(names):
            path = os.path.join(root, name)
            try:
                if os.path.islink(path):
                    notes.append("skipped_symlink: %s" % path)
                    continue
                size = os.path.getsize(path)
            except OSError as exc:
                notes.append("unreadable: %s: %s" % (path, exc))
                continue
            rel = os.path.relpath(path, input_dir).replace("\\", "/")
            ext = os.path.splitext(name)[1].lower()
            status = "ok"
            if size > MAX_FILE_BYTES and ext in TEXT_EXTS:
                status = "oversize"
                notes.append("oversize: %s (%d bytes)" % (rel, size))
            files.append({
                "relpath": rel, "abspath": path, "size": size,
                "ext": ext, "status": status,
            })
    return files, notes


def _locate_case_file(files):
    for item in files:
        if item["status"] == "ok" and item["relpath"].endswith("/case.json"):
            return item
    for item in files:
        if item["status"] == "ok" and os.path.basename(item["relpath"]) in CASE_NAMES:
            return item
    return None


def load_case(input_dir: str, files):
    """读取并解析 case.json。返回 (data_or_None, error_or_None)。"""
    item = _locate_case_file(files)
    if item is None:
        return None, "case_file_missing"
    text, warn = read_text(item["abspath"])
    if text is None:
        return None, "case_unreadable: %s" % warn
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        # 兼容 JSONL：逐行解析，取首个合法 JSON 对象。
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj, None
        return None, "case_json_invalid: %s" % exc


def extract_case_id(data):
    if isinstance(data, dict):
        for key in CASE_ID_KEYS:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return str(value)
    return RESERVED_UNKNOWN


def extract_contract(data):
    if isinstance(data, dict):
        contract = data.get("output_contract")
        if isinstance(contract, dict):
            return contract
    return {}


# --------------------------------------------------------------------------- #
# IR：中间表示构建
# --------------------------------------------------------------------------- #
_FIELD_HINTS = {
    "research_goal": ("research_goal", "research_question", "goal", "objective", "aim",
                      "研究目标", "研究问题", "目标"),
    "plans": ("plans", "candidate_plans", "plan", "candidates", "versions", "方案"),
    "resources": ("resources", "equipment", "materials", "contracts", "procurement",
                  "devices", "设备", "材料", "资源", "合同"),
    "parameters": ("parameter_domains", "parameters", "domains", "ranges", "参数", "参数域"),
    "samples": ("samples", "specimens", "sample_info", "cohort", "样本", "样品"),
    "ethics": ("ethics", "safety", "approval", "ethics_safety", "伦理", "安全", "审批"),
}


def _find_value(obj, keys):
    """在 dict 的值中按候选键名查找第一处命中，返回 (value, locator)。"""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in keys:
                return value, key
            found, path = _find_value(value, keys)
            if found is not None:
                return found, "%s.%s" % (key, path)
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            found, path = _find_value(value, keys)
            if found is not None:
                return found, "[%d].%s" % (idx, path)
    return None, None


def _bounded(value, depth=0, max_items=80):
    """递归快照，限制深度与条数，避免超大结构撑爆 IR。"""
    if depth > 5:
        return "[truncated:depth]"
    if isinstance(value, dict):
        out = {}
        for idx, (k, v) in enumerate(value.items()):
            if idx >= max_items:
                out["[truncated]"] = "…"
                break
            out[k] = _bounded(v, depth + 1, max_items)
        return out
    if isinstance(value, list):
        out = []
        for idx, v in enumerate(value):
            if idx >= max_items:
                out.append("[truncated]")
                break
            out.append(_bounded(v, depth + 1, max_items))
        return out
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def build_case_record(input_dir: str, files, data, notes):
    """由原始输入构建中间表示 case_record。"""
    field_blocks = {}
    for name, keys in _FIELD_HINTS.items():
        value, locator = _find_value(data, keys) if isinstance(data, dict) else (None, None)
        field_blocks[name] = {
            "locator": ("case.json#%s" % locator) if locator else None,
            "snapshot": _bounded(value) if value is not None else None,
        }

    materials = []
    for item in files:
        if item["relpath"] == "case.json":
            continue
        if item["ext"] == ".md":
            text, _ = read_text(item["abspath"])
            materials.append({
                "relpath": item["relpath"], "size": item["size"],
                "heading": (text.splitlines()[0][:120] if text and text.splitlines() else ""),
            })

    # 归一：plans 若为单个对象，包成单元素列表，便于后续按版本遍历。
    plans_block = field_blocks.get("plans") or {}
    if isinstance(plans_block.get("snapshot"), dict):
        plans_block["snapshot"] = [plans_block["snapshot"]]
        field_blocks["plans"] = plans_block

    return {
        "case_id": extract_case_id(data),
        "output_contract": extract_contract(data),
        "title": data.get("title") if isinstance(data, dict) else None,
        "field_blocks": field_blocks,
        "raw_snapshot": _bounded(data) if isinstance(data, (dict, list)) else None,
        "materials": materials,
        "input_files": [
            {"relpath": f["relpath"], "size": f["size"], "status": f["status"]}
            for f in files
        ],
        "input_notes": notes,
    }


# --------------------------------------------------------------------------- #
# 结果骨架
# --------------------------------------------------------------------------- #
def _empty_review_item(dimension):
    return {
        "id": None, "dimension": dimension, "claim": None,
        "verdict": "missing", "resource_state": None,
        "conditions": [], "evidence": [],
        "confidence": None, "needs_expert": False, "notes": [],
    }


def _base_result():
    return {
        "case_id": None, "challenge_id": None, "task_title": None,
        "status": "partial",
        "overall_conclusion": {
            "verdict": None, "summary": None,
            "conditions": [], "blocks": [], "notes": [],
        },
        "experiment_plan": {
            "research_question": None, "assumptions": [], "variables": [],
            "controls": [], "steps": [], "parameters": [],
            "resources": [], "interpretation_criteria": [],
        },
        "review": {d: [] for d in DIMENSIONS},
        "plan_comparison": [],
        "evidence_and_risks": {"evidence": [], "risks": [], "exception_handling": []},
        "confirmation": {
            "safety": [], "ethics": [],
            "equipment_availability": [], "expert_confirmation": [],
        },
        "sources": [], "uncertainty": [], "pending_human": [],
        "errors": [], "degradations": [], "change_log": [
            {"step": "skeleton", "action": "写入初始 partial 骨架",
             "reason": "执行流程规定：前 3 分钟内必须先落盘一版结果骨架，之后只做增量改进"}
        ],
        "runtime": {"started_at": None, "seconds": 0.0, "requests": 0},
        "artifacts": [],
    }


def _infer_kind(spec):
    if isinstance(spec, str):
        s = spec.strip().lower()
        if s in ("string", "str", "text", "markdown", "md"):
            return "string"
        if s in ("number", "int", "float", "numeric", "integer"):
            return "number"
        if s in ("array", "list"):
            return "array"
        if s in ("object", "dict", "map"):
            return "object"
        if s in ("boolean", "bool"):
            return "boolean"
        return "string"
    if isinstance(spec, bool):
        return "boolean"
    if isinstance(spec, (int, float)):
        return "number"
    if isinstance(spec, (list, tuple)):
        return "array"
    if isinstance(spec, dict):
        for k in ("type", "default", "example"):
            if k in spec:
                return _infer_kind(spec[k])
        return "object"
    return "string"


def _default_for_kind(kind):
    return {
        "string": "", "number": None, "array": [],
        "object": {}, "boolean": None,
    }.get(kind, "")


def echo_contract_keys(result, contract):
    """把 output_contract 的全部字段名原样回填到结果顶层（缺值按类型给默认）。"""
    added = []
    if not isinstance(contract, dict):
        return added
    for key, spec in contract.items():
        if key in result:
            continue  # 保留已存在的基础字段，不覆盖
        value = _default_for_kind(_infer_kind(spec))
        result[key] = value
        added.append(key)
    return added


def make_skeleton(case_record):
    """由 IR 确定性生成合法 partial 骨架。"""
    result = _base_result()
    result["case_id"] = case_record.get("case_id") or RESERVED_UNKNOWN
    if isinstance(case_record.get("output_contract"), dict):
        result["output_contract"] = case_record.get("output_contract")
    echo_contract_keys(result, case_record.get("output_contract") or {})

    material_note = "materials=%d" % len(case_record.get("materials", []))
    file_note = "input_files=%d" % len(case_record.get("input_files", []))
    result["degradations"].append({
        "kind": "skeleton", "message": "partial 骨架，待 S1–S5 增量回填",
        "notes": [material_note, file_note],
    })
    return result


# --------------------------------------------------------------------------- #
# 确定性算术
# --------------------------------------------------------------------------- #
_DURATION_PATTERN = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>h|hr|hrs|hour|hours|min|mins|minute|minutes|d|day|days|w|week|weeks|小时|分钟|天|周|秒|s)"
)


def parse_duration(text):
    """解析 '2h'/'30 min'/'1.5 小时' 等，返回总分钟数；无法解析返回 None。"""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text)
    if not isinstance(text, str):
        return None
    s = text.strip().lower()
    if not s:
        return None
    match = _DURATION_PATTERN.search(s)
    if not match:
        try:
            return float(s)
        except ValueError:
            return None
    value = float(match.group("value"))
    unit = match.group("unit")
    factors = {
        "h": 60, "hr": 60, "hrs": 60, "hour": 60, "hours": 60,
        "min": 1, "mins": 1, "minute": 1, "minutes": 1,
        "d": 1440, "day": 1440, "days": 1440,
        "w": 10080, "week": 10080, "weeks": 10080,
        "s": 1 / 60, "小时": 60, "分钟": 1, "天": 1440, "周": 10080, "秒": 1 / 60,
    }
    return value * factors.get(unit, 1)


def sum_time(items):
    """累加时长。items 为 [{"label":..., "duration":...}]，返回带算式的结果。"""
    total, ok_rows, bad_rows = 0.0, [], []
    for idx, item in enumerate(items or []):
        if not isinstance(item, dict):
            bad_rows.append("item[%d]=%r" % (idx, item))
            continue
        minutes = parse_duration(item.get("duration_minutes", item.get("duration", item.get("time"))))
        label = item.get("label") or item.get("step") or "第%d项" % (idx + 1)
        if minutes is None:
            bad_rows.append(label)
            continue
        total += minutes
        ok_rows.append("%s(%smin)" % (label, _fmt(minutes)))
    formula = " + ".join(ok_rows) + (" = %s min" % _fmt(total) if ok_rows else "")
    return {
        "total_minutes": round(total, 2), "total_hours": round(total / 60.0, 3),
        "formula": formula, "parsed_items": len(ok_rows), "unparseable": bad_rows,
    }


def check_range(value, spec):
    """校验数值是否落于区间。spec 可为 {min,max,inclusive:bool} 或 (min,max)。"""
    if value is None:
        return {"in_range": None, "reason": "no_value", "spec": spec}
    try:
        value = float(value)
    except (TypeError, ValueError):
        return {"in_range": None, "reason": "value_not_numeric", "spec": spec}
    lo, hi, inclusive = None, None, True
    if isinstance(spec, dict):
        lo = spec.get("min")
        hi = spec.get("max")
        if "inclusive" in spec:
            inclusive = bool(spec["inclusive"])
    elif isinstance(spec, (list, tuple)) and len(spec) == 2:
        lo, hi = spec[0], spec[1]
    else:
        return {"in_range": None, "reason": "spec_format", "spec": spec}

    def _num(x):
        try:
            return float(x) if x is not None else None
        except (TypeError, ValueError):
            return None

    lo, hi = _num(lo), _num(hi)
    if lo is None and hi is None:
        return {"in_range": None, "reason": "no_bounds", "spec": spec}
    low_ok = (lo is None) or (value > lo if not inclusive else value >= lo)
    high_ok = (hi is None) or (value < hi if not inclusive else value <= hi)
    return {
        "value": value, "min": lo, "max": hi, "inclusive": inclusive,
        "in_range": bool(low_ok and high_ok),
        "reason": "",
        "formula": "%s <= %s <= %s" % (_fmt(lo), _fmt(value), _fmt(hi)) if (lo is not None and hi is not None) else None,
    }


def check_parameter_against_domain(value, domain):
    """判断方案参数值是否落在给出的有限参数域内。domain 可为 list/set 或 {min,max}。"""
    if isinstance(domain, dict) and ("min" in domain or "max" in domain):
        return check_range(value, domain)
    if isinstance(domain, (list, tuple, set)):
        values = [v for v in domain]
        return {
            "allowed": None if not values else (value in values),
            "value": value, "domain_size": len(values),
            "reason": "empty_domain" if not values else "",
        }
    return {"allowed": None, "value": value, "reason": "domain_format"}


def sum_cost(items):
    """累加成本。items 为 [{"label":..., "cost":..., "currency":...}]，仅对同币种求和。"""
    by_currency = {}
    bad = []
    for idx, item in enumerate(items or []):
        if not isinstance(item, dict):
            bad.append("item[%d]=%r" % (idx, item))
            continue
        cost = item.get("cost")
        try:
            cost = float(cost)
        except (TypeError, ValueError):
            bad.append(item.get("label") or "第%d项" % (idx + 1))
            continue
        cur = item.get("currency") or "CNY"
        by_currency.setdefault(cur, []).append(cost)
    totals = {c: round(sum(v), 2) for c, v in by_currency.items()}
    return {"totals": totals, "formula": "; ".join(
        "%s=%s" % (c, _fmt(v)) for c, v in totals.items()), "unparseable": bad}


def check_sample(needed, available):
    """核对样本量。needed/available 可为数值或 {count,n}。返回短缺结论。"""
    def _n(x):
        if isinstance(x, (int, float)) and not isinstance(x, bool):
            return float(x)
        if isinstance(x, dict):
            return _n(x.get("count", x.get("n")))
        return None

    need, avail = _n(needed), _n(available)
    if need is None or avail is None:
        return {"sufficient": None, "reason": "unknown", "needed": need, "available": avail}
    return {
        "sufficient": bool(avail >= need),
        "shortfall": round(max(0.0, need - avail), 2),
        "formula": "available(%s) >= needed(%s)" % (_fmt(avail), _fmt(need)),
        "needed": need, "available": avail,
    }


def _fmt(x):
    if x is None:
        return "?"
    if isinstance(x, float):
        return ("%g" % x)
    return str(x)


# --------------------------------------------------------------------------- #
# 证据账本
# --------------------------------------------------------------------------- #
class EvidenceLedger:
    """按 (locator, quote) 去重的证据账本；同一定位只保留一份。"""

    def __init__(self):
        self._seen = set()
        self._items = []

    def add(self, locator, quote, kind="plan"):
        if not locator or not quote:
            return False
        key = (locator, quote)
        if key in self._seen:
            return False
        self._seen.add(key)
        self._items.append({"locator": locator, "quote": quote, "kind": kind})
        return True

    def items(self):
        return list(self._items)

    def __len__(self):
        return len(self._items)


# --------------------------------------------------------------------------- #
# 确定性自检
# --------------------------------------------------------------------------- #
def _selftest():
    ok = 0
    fail = 0

    def check(name, cond):
        nonlocal ok, fail
        if cond:
            ok += 1
        else:
            fail += 1
            print("SELFTEST FAIL: %s" % name)

    check("parse_duration('2h')==120", abs(parse_duration("2h") - 120) < 1e-9)
    check("parse_duration('30 min')==30", abs(parse_duration("30 min") - 30) < 1e-9)
    check("parse_duration('1.5 小时')==90", abs(parse_duration("1.5 小时") - 90) < 1e-9)
    st = sum_time([{"label": "a", "duration": "30 min"}, {"label": "b", "duration": "1h"}])
    check("sum_time total==90", abs(st["total_minutes"] - 90) < 1e-9)
    check("sum_time formula nonempty", bool(st["formula"]))
    rng = check_range(5, {"min": 0, "max": 10})
    check("check_range in", rng["in_range"] is True)
    rng2 = check_range(15, {"min": 0, "max": 10})
    check("check_range out", rng2["in_range"] is False)
    domain = check_parameter_against_domain(3, [1, 2, 3])
    check("domain hit", domain["allowed"] is True)
    sample = check_sample(10, 12)
    check("sample sufficient", sample["sufficient"] is True)
    check("sample shortfall", check_sample(10, 8)["shortfall"] == 2)
    cost = sum_cost([{"label": "x", "cost": 10, "currency": "CNY"},
                     {"label": "y", "cost": 5.5, "currency": "CNY"}])
    check("cost sum", cost["totals"] == {"CNY": 15.5})

    ledger = EvidenceLedger()
    check("ledger add", ledger.add("case.json#plan.0", "原文", "plan") is True)
    check("ledger dedup", ledger.add("case.json#plan.0", "原文", "plan") is False)
    check("ledger len", len(ledger) == 1)

    record = build_case_record("/input", [], {"id": "x", "title": "t"}, [])
    check("case_id x", record["case_id"] == "x")
    skel = make_skeleton(record)
    check("skeleton status partial", skel["status"] == "partial")
    check("skeleton case_id x", skel["case_id"] == "x")
    skel2 = make_skeleton(record)
    check("skeleton deterministic", json.dumps(skel, ensure_ascii=False, sort_keys=True)
          == json.dumps(skel2, ensure_ascii=False, sort_keys=True))

    print("SELFTEST %s: ok=%d fail=%d" % ("PASS" if fail == 0 else "FAIL", ok, fail))
    return 0 if fail == 0 else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="核心引擎自检")
    parser.add_argument("--selftest", action="store_true", help="运行确定性自检")
    args = parser.parse_args(argv)
    if args.selftest:
        return _selftest()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())