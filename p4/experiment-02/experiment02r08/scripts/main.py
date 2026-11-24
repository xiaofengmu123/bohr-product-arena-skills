#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Feasibility Gate Review — S0 输入发现/骨架落盘 + 增量合并。

    python3 scripts/main.py [--input /input --output /app/output --work /app/work]
    python3 scripts/main.py merge --patch <patch.json>

职责：容错读取 /input，立即落盘合法 partial result.json，生成 case_ledger.json 与
input_inventory.json；merge 模式深合并补丁并追加 change_log 修订。仅用标准库，
无随机、无墙钟，同输入两次运行骨架一致，空目录/坏 JSON/超限文件均退出码 0。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_BYTES = 2 * 1024 * 1024          # 单文本材料上限 2MB
MAX_ITEMS = 60                        # 归一化时每个列表最多保留条目数
MAX_DEPTH = 6                         # 递归发现键名的最大深度

TEXT_EXT = {".md", ".txt", ".json", ".jsonl", ".csv", ".tsv", ".yaml", ".yml"}
CASE_NAMES = ("case.json", "case.jsonl", "input.json")

CASE_ID_KEYS = ("case_id", "caseid", "id", "challenge_id", "armiid", "slug")
GOAL_KEYS = ("research_goal", "goal", "objective", "research_question", "question")
PLAN_KEYS = ("plans", "candidate_plans", "plan_versions", "variants", "proposals", "solutions")
CONTRACT_KEYS = ("contracts", "resource_contracts", "bookings", "quotes", "pricing", "supply", "equipment")
PARAM_KEYS = ("parameters", "parameter_domain", "parameter_domains", "domains", "specs")
SAMPLE_KEYS = ("samples", "sample", "specimens", "cohort", "subjects")
ETHICS_KEYS = ("ethics", "safety", "compliance", "approvals", "permits", "irb")
SOURCE_KEYS = ("sources", "references", "literature", "citations", "evidence")
CONTRACT_SPEC_KEYS = ("output_contract", "output_schema", "result_contract")


# ---- 文本与 JSON 容错读取 ----
def decode_bytes(raw: bytes):
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "decode_replaced"


def read_text(path: str, limit: int = MAX_BYTES):
    """返回 (text, warnings)；任何异常均降级为 None/警告，不抛出。"""
    if not os.path.exists(path):
        return None, ["missing: " + path]
    try:
        if os.path.islink(path):
            return None, ["skipped_symlink: " + path]
        size = os.path.getsize(path)
        if size > limit:
            return None, ["oversize(%dB > %dB): %s" % (size, limit, path)]
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, ["unreadable(%s): %s" % (exc, path)]
    text, warn = decode_bytes(raw)
    warnings = [] if not warn else [warn + ": " + path]
    return text, warnings


def load_json(path: str):
    """读取 JSON（含 JSONL 回退）。返回 (obj, warnings)。"""
    text, warnings = read_text(path)
    if text is None:
        return None, warnings
    try:
        return json.loads(text), warnings
    except json.JSONDecodeError as exc:
        # JSONL 回退：逐行解析，至少一条成功即视为记录集。
        records = []
        for ln in text.splitlines():
            ln = ln.strip()
            if not ln or ln.startswith(("#", "//")):
                continue
            try:
                records.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        if records:
            return {"records": records}, warnings + ["jsonl_fallback(%d): %s" % (len(records), path)]
        return None, warnings + ["json_error(%s@line%d): %s" % (exc.msg, exc.lineno, path)]


# ---- 输入发现 ----
def discover(input_dir: str):
    files, warnings = [], []
    if not os.path.isdir(input_dir):
        return files, ["input_dir_missing: " + input_dir] + warnings
    for root, dirs, names in os.walk(input_dir):
        dirs.sort()
        for name in sorted(names):
            path = os.path.join(root, name)
            try:
                if os.path.islink(path):
                    warnings.append("skipped_symlink: " + path)
                    continue
                size = os.path.getsize(path)
            except OSError as exc:
                warnings.append("unreadable(%s): %s" % (exc, path))
                continue
            rel = os.path.relpath(path, input_dir).replace("\\", "/")
            ext = os.path.splitext(name)[1].lower()
            status = "ok" if not (size > MAX_BYTES and ext in TEXT_EXT) else "oversize"
            if status == "oversize":
                warnings.append("oversize: %s (%dB)" % (rel, size))
            files.append({"relpath": rel, "abspath": path, "bytes": size,
                          "ext": ext, "status": status})
    return files, warnings


# ---- 归一化发现器 ----
def render(path):
    out = ""
    for part in path:
        if isinstance(part, int):
            out += "[%d]" % part
        else:
            out += (("" if out == "" else ".") + str(part))
    return out


def walk_find(obj, keys, depth=MAX_DEPTH):
    """宽度优先，返回第一个 (pointer, value)。键名不区分大小写。"""
    queue = [([], obj, 0)]
    keyset = {k.lower() for k in keys}
    while queue:
        path, node, d = queue.pop(0)
        if d > depth or node is None:
            continue
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str) and k.lower() in keyset and _meaningful(v):
                    return render(path + [k]), v
            for k, v in node.items():
                if isinstance(v, (dict, list)):
                    queue.append((path + [k], v, d + 1))
        elif isinstance(node, list):
            for i, v in enumerate(node[:MAX_ITEMS]):
                if isinstance(v, (dict, list)):
                    queue.append((path + [i], v, d + 1))
    return None, None


def _meaningful(v):
    return v not in (None, "", [], {})


def as_list(v):
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        items = [x for x in v.values() if isinstance(x, dict)]
        if items:
            return items
        return [v] if v else []
    return [v] if v not in (None, "") else []


def first_str(d, keys, fallback=""):
    for k in keys:
        if k in d and isinstance(d[k], str) and d[k].strip():
            return d[k].strip()
    return fallback if isinstance(fallback, str) else ""


def to_num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace("￥", "").replace("¥", "").replace("$", "")
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        if m:
            try:
                return float(m.group(0))
            except ValueError:
                return None
    return None


def norm_steps(value):
    steps = []
    for i, item in enumerate(as_list(value)[:MAX_ITEMS]):
        if isinstance(item, str):
            steps.append({"ptr": "[%d]" % i, "name": item, "durations": []})
            continue
        if not isinstance(item, dict):
            continue
        dur = []
        for k, v in item.items():
            if isinstance(k, str) and re.search(r"(duration|time|hour|hr|day|week|minute|min|turnaround|lead|incubat|hands|occup|wait|耗时|时长|占用|等待|周期|工时|机时|_h$|_hrs$|_hours$|_min$|_d$|_day$|_week$)", k, re.I):
                n = to_num(v)
                if n is not None:
                    dur.append({"key": k, "raw": v, "hours": None, "value": n})
        steps.append({"ptr": "[%d]" % i, "name": first_str(item, ("name", "step", "title", "label")),
                      "durations": dur, "params": flatten_scalars(item),
                      "raw_keys": sorted(k for k in item.keys())[:40]})
    return steps


def flatten_scalars(d, prefix="", depth=0):
    """把一层内嵌的数值/短字符串标量拍平为 {key: value}，供参数域匹配。"""
    out = {}
    if not isinstance(d, dict) or depth > 1:
        return out
    for k, v in d.items():
        if isinstance(k, bool):
            continue
        full = (prefix + "." + str(k)) if prefix else str(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[full] = v
        elif isinstance(v, str) and 0 < len(v) <= 40:
            out[full] = v
        elif isinstance(v, dict):
            out.update(flatten_scalars(v, full, depth + 1))
    return out


def collect_scalars(obj, out=None, path=None, depth=0):
    """递归收集案例层面的标量（含路径），供工期窗口/预算上限发现。"""
    if out is None:
        out = []
    if path is None:
        path = []
    if depth > MAX_DEPTH or obj is None:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and not k.startswith("_"):
                collect_scalars(v, out, path + [k], depth + 1)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:MAX_ITEMS]):
            collect_scalars(v, out, path + [i], depth + 1)
    elif isinstance(obj, (int, float, str)) and not isinstance(obj, bool):
        if len(str(obj)) <= 60:
            out.append({"ptr": render(path), "key": str(path[-1]) if path else "",
                        "value": obj})
        if len(out) > 400:
            out.pop(0)
    return out


def norm_plans(value, ptr):
    plans = []
    items = as_list(value)
    for i, item in enumerate(items[:MAX_ITEMS]):
        if not isinstance(item, dict):
            plans.append({"plan_id": "plan%d" % i, "label": str(item)[:80], "ptr": ptr + "[%d]" % i,
                          "steps": [], "params": {}, "budget": None, "raw_keys": []})
            continue
        budget = None
        for k in ("budget", "cost_cap", "budget_cap", "总预算", "预算"):
            if k in item:
                budget = to_num(item[k])
                break
        plans.append({
            "plan_id": first_str(item, ("plan_id", "id", "name", "label", "title", "version"), "plan%d" % i),
            "label": first_str(item, ("label", "title", "name", "version"), ""),
            "ptr": ptr + "[%d]" % i,
            "steps": norm_steps(item.get("steps") or item.get("procedure") or item.get("protocol") or []),
            "params": flatten_scalars(item),
            "budget": budget,
            "raw_keys": sorted(k for k in item.keys())[:40],
        })
    return plans


def norm_contracts(value, ptr):
    """归一化资源/价格/预约合同。兼容 list、dict-of-dict、dict-of-list 三种形态。"""
    collected = []

    def rec(node, p, cat, depth):
        if depth > 3 or node is None:
            return
        if isinstance(node, list):
            for i, x in enumerate(node[:MAX_ITEMS]):
                if isinstance(x, dict):
                    collected.append((p + "[%d]" % i, x, cat))
                else:
                    rec(x, p + "[%d]" % i, cat, depth + 1)
        elif isinstance(node, dict):
            item_keys = {"name", "item", "model", "sku", "price", "unit_price", "cost",
                         "fee", "qty", "quantity", "count", "capacity_h", "available_hours",
                         "lead_days", "status", "availability"}
            if item_keys & set(node.keys()):
                collected.append((p, node, cat))
            else:
                for k, v in node.items():
                    rec(v, (p + "." + str(k)) if p else str(k), str(k), depth + 1)

    rec(value, ptr or "contracts", "", 0)
    items = []
    for p, item, cat in collected:
        items.append({
            "ptr": p,
            "name": first_str(item, ("name", "item", "model", "sku", "title", "label"), "item"),
            "kind": first_str(item, ("kind", "type", "category"), cat or ""),
            "status": first_str(item, ("status", "state", "availability"), ""),
            "price": to_num(item.get("price")) or to_num(item.get("unit_price")) or to_num(item.get("cost")) or to_num(item.get("fee")) or to_num(item.get("amount")),
            "qty": to_num(item.get("qty")) or to_num(item.get("quantity")) or to_num(item.get("count")) or to_num(item.get("n")),
            "lead_days": to_num(item.get("lead_days")) or to_num(item.get("lead_time")) or to_num(item.get("货期")),
            "capacity_h": to_num(item.get("capacity_h")) or to_num(item.get("available_hours")) or to_num(item.get("slots")),
            "raw_keys": sorted(k for k in item.keys())[:40],
        })
    return items


def norm_params(value, ptr):
    items = []
    for i, item in enumerate(as_list(value)[:MAX_ITEMS]):
        if not isinstance(item, dict):
            continue
        items.append({
            "ptr": ptr + "[%d]" % i,
            "name": first_str(item, ("name", "param", "key", "parameter", "title"), "param%d" % i),
            "value": to_num(item.get("value")) or to_num(item.get("plan_value")) or to_num(item.get("nominal")),
            "min": to_num(item.get("min")), "max": to_num(item.get("max")),
            "unit": first_str(item, ("unit",), ""),
            "allowed": item.get("allowed") if isinstance(item.get("allowed"), list) else None,
            "raw_keys": sorted(k for k in item.keys())[:40],
        })
    return items


def norm_samples(value, ptr):
    items = []
    for i, item in enumerate(as_list(value)[:MAX_ITEMS]):
        if not isinstance(item, dict):
            continue
        items.append({
            "ptr": ptr + "[%d]" % i,
            "name": first_str(item, ("name", "type", "group", "label"), "sample%d" % i),
            "required": to_num(item.get("required")) or to_num(item.get("needed")) or to_num(item.get("target_n")) or to_num(item.get("n")),
            "available": to_num(item.get("available")) or to_num(item.get("stock")) or to_num(item.get("collected")) or to_num(item.get("count")),
            "unit": first_str(item, ("unit",), ""),
            "storage": first_str(item, ("storage", "storage_condition"), ""),
            "approval": first_str(item, ("approval", "approval_status", "irb_status"), ""),
            "raw_keys": sorted(k for k in item.keys())[:40],
        })
    return items


def norm_sources(value):
    out = []
    for i, item in enumerate(as_list(value)[:MAX_ITEMS]):
        if isinstance(item, str):
            out.append({"ptr": "[%d]" % i, "id": "", "title": item[:160]})
        elif isinstance(item, dict):
            out.append({
                "ptr": "[%d]" % i,
                "id": first_str(item, ("id", "doi", "url", "citation_key"), ""),
                "title": first_str(item, ("title", "name", "citation", "reference"), ""),
                "locator": first_str(item, ("locator", "file", "path"), ""),
            })
    return out


def extract_case_id(data, files):
    if isinstance(data, dict):
        for key in CASE_ID_KEYS:
            v = data.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, (int, float)):
                return str(v)
    for f in files:
        base = f["relpath"].rsplit("/", 1)[-1]
        if base in CASE_NAMES:
            return "derived:" + os.path.splitext(base)[0]
    return "unknown"


def contract_default(spec):
    """按 output_contract 的字段说明给出类型缺省值：缺值不删键、不改名。"""
    if isinstance(spec, dict):
        t = str(spec.get("type", spec.get("kind", ""))).lower()
        if "default" in spec:
            return spec["default"]
        if "bool" in t:
            return False
        if any(x in t for x in ("int", "float", "num", "double")):
            return None
        if any(x in t for x in ("array", "list")):
            return []
        if any(x in t for x in ("object", "dict", "map")):
            return {}
        return ""
    if isinstance(spec, str):
        s = spec.strip().lower()
        if s in ("boolean", "bool"):
            return False
        if s in ("number", "integer", "int", "float", "numeric"):
            return None
        if s in ("array", "list"):
            return []
        if s in ("object", "dict", "map"):
            return {}
        return ""
    if isinstance(spec, bool):
        return False
    if isinstance(spec, (int, float)):
        return None
    if isinstance(spec, list):
        return []
    if isinstance(spec, dict):
        return {}
    return ""


def extract_output_contract(data):
    ptr, spec = walk_find(data, CONTRACT_SPEC_KEYS)
    if not isinstance(spec, dict):
        return None, None
    fields = {k: contract_default(v) for k, v in spec.items() if not k.startswith("_")}
    return ptr, fields


# ---- 结果骨架 ----
RUBRIC_COVERAGE = {
    "actual_effect": {"weight": 40, "points": {
        "task_completion":  {"where": "review 七类各>=2条证据化 finding", "check": "C06"},
        "correctness":      {"where": "arithmetic.derivations 与 verdict=ok 的引文", "check": "C09,C10"},
        "completeness":     {"where": "review 中 missing/unconfirmed 与 pending_human", "check": "C11"},
        "verifiability":    {"where": "evidence[].locator+quote 逐字核验", "check": "C09"},
    }},
    "usability_gain": {"weight": 25, "points": {
        "quality":          {"where": "review + plan_comparison 的可复跑口径", "check": "C06,C13", "external": True},
        "efficiency":       {"where": "runtime.requests 与 change_log 修订链", "check": "C16"},
        "verifiability":    {"where": "replay_digest 同输入同口径复跑", "check": "C19"},
    }},
    "innovation_gain": {"weight": 15, "points": {
        "new_capability":   {"where": "arithmetic 容量门控 + 引文逐字核验", "check": "C12"},
        "method":           {"where": "references/rubric.md 的判定阈值与算术规则", "check": "C12"},
        "evidence":         {"where": "baseline_comparison 由平台提供", "check": "C21", "external": True},
    }},
    "reliability": {"weight": 10, "points": {
        "usability":        {"where": "SKILL.md 调用方式与 scripts --help", "check": "C01"},
        "stability":        {"where": "空目录/坏 JSON/超限/缺页均退出码0", "check": "C01,C18"},
        "exception":        {"where": "errors/degradations/limitations", "check": "C15"},
    }},
    "ecosystem": {"weight": 10, "points": {
        "documentation":    {"where": "SKILL.md + references/*.md", "check": "C17"},
        "reusability":      {"where": "references/checkitems.md 检查项库", "check": "C17"},
        "sharing":          {"where": "references/rubric.md 模板可复用", "check": "C17"},
    }},
}

BASELINE_COMPARISON = {
    "provided_by": "platform",
    "status": "not_available_in_skill_run",
    "note": "质量/效率增益由平台在固定案例上做基础Agent与SkillAgent对照；本Skill只交付可复跑口径、自检记录与产物，不生成对照分数。",
}


def build_skeleton(data, files, warnings, case_path):
    case_id = extract_case_id(data, files)
    challenge_id = None
    if isinstance(data, dict):
        challenge_id = data.get("challenge_id") or data.get("armiId") or None

    goal_ptr, goal = walk_find(data, GOAL_KEYS)
    if goal is None and isinstance(data, dict):
        goal = data.get("title")
        goal_ptr = "title"

    plans_ptr, plans_raw = walk_find(data, PLAN_KEYS)
    contracts_ptr, contracts_raw = walk_find(data, CONTRACT_KEYS)
    params_ptr, params_raw = walk_find(data, PARAM_KEYS)
    samples_ptr, samples_raw = walk_find(data, SAMPLE_KEYS)
    ethics_ptr, ethics_raw = walk_find(data, ETHICS_KEYS)
    sources_ptr, sources_raw = walk_find(data, SOURCE_KEYS)
    contract_ptr, contract_fields = extract_output_contract(data)

    ledger = {
        "case_id": case_id,
        "challenge_id": challenge_id,
        "task_title": data.get("title") if isinstance(data, dict) else None,
        "paths": {"case": case_path},
        "goal": {"ptr": goal_ptr, "value": (goal if isinstance(goal, (str, int, float)) else json.dumps(goal, ensure_ascii=False)[:2000])},
        "plans": norm_plans(plans_raw, plans_ptr or "plans"),
        "contracts": {"ptr": contracts_ptr, "items": norm_contracts(contracts_raw, contracts_ptr or "contracts")},
        "parameters": {"ptr": params_ptr, "items": norm_params(params_raw, params_ptr or "parameters")},
        "samples": {"ptr": samples_ptr, "items": norm_samples(samples_raw, samples_ptr or "samples")},
        "ethics": {"ptr": ethics_ptr, "value": ethics_raw if isinstance(ethics_raw, (str, int, float)) else json.dumps(ethics_raw, ensure_ascii=False)[:2000] if ethics_raw else None},
        "sources": norm_sources(sources_raw),
        "scalars": collect_scalars(data),
        "output_contract": {"ptr": contract_ptr, "fields": contract_fields or {}},
        "warnings": warnings,
        "missing": [],
    }

    # 缺数据缺口：七类中哪些没有任何材料支撑。
    for label, present in (
        ("plans", plans_raw is not None), ("contracts", contracts_raw is not None),
        ("parameters", params_raw is not None), ("samples", samples_raw is not None),
        ("ethics", ethics_raw is not None), ("sources", sources_raw is not None),
    ):
        if not present:
            ledger["missing"].append(label)

    # output_contract 原样回填（不改名、不删键、缺值给缺省）。
    merged = {}
    if contract_fields:
        for k, v in contract_fields.items():
            merged[k] = v

    base = {
        "schema_version": "1.1.0",
        "skill": {"name": "feasibility-gate-review", "version": "1.1.0", "ruleset": "seven-gate-v1"},
        "case_id": case_id,
        "challenge_id": challenge_id,
        "task_title": ledger["task_title"],
        "status": "partial",
        "research_question": {"text": ledger["goal"]["value"], "ptr": ledger["goal"]["ptr"]},
        "plan_set": [{"plan_id": p["plan_id"], "label": p["label"], "ptr": p["ptr"],
                      "n_steps": len(p["steps"]), "budget": p["budget"]} for p in ledger["plans"]],
        "review": {
            "assumptions_variables": [], "controls": [], "equipment_materials": [],
            "time_cost": [], "parameter_ranges": [], "risks_exceptions": [], "ethics_safety": [],
        },
        "arithmetic": {"plans": [], "derivations": [], "not_computable": []},
        "plan_comparison": [],
        "recommendation": {"verdict": None, "preferred_plan": None, "summary": "", "conditions": [], "blocks": []},
        "confirmation": {"safety": [], "ethics": [], "equipment_availability": [], "expert_confirmation": []},
        "evidence_index": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "rubric_coverage": RUBRIC_COVERAGE,
        "baseline_comparison": BASELINE_COMPARISON,
        "change_log": [
            {"rev": 1, "step": "S0", "action": "写入 partial 骨架", "reason": "硬规则：前3分钟内先落盘合法骨架，之后只做增量改进"}
        ],
        "runtime": {"generated_by": "scripts/main.py", "requests": 0, "seconds": 0},
        "replay_digest": None,
        "artifacts": [],
    }
    # 契约字段合并到顶层（若与既有键冲突，保留既有键值并记录）。
    for k, v in merged.items():
        if k not in base:
            base[k] = v
        else:
            base.setdefault("degradations", []).append("contract_key_collision:" + str(k))

    return base, ledger


# ---- 落盘 ----
def write_json(path, obj):
    """原子写：先写临时文件再替换；失败降级到最小合法 JSON，进程退出码 0。"""
    d = os.path.dirname(path)
    try:
        os.makedirs(d, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        os.replace(tmp, path)
        return True, []
    except OSError as exc:
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"case_id": "unknown", "status": "input_error",
                           "errors": ["write_failed: %s" % exc],
                           "change_log": [{"rev": 1, "step": "S0", "action": "降级最小合法 JSON"}]},
                          fh, ensure_ascii=False, indent=1)
        except OSError as exc2:
            sys.stderr.write("cannot write result: %s / %s\n" % (exc, exc2))
        return False, ["write_failed: %s" % exc]


# ---- 增量合并 ----
def deep_merge(base, patch):
    if isinstance(base, dict) and isinstance(patch, dict):
        out = dict(base)
        for k, v in patch.items():
            if isinstance(k, str) and k.startswith("_"):
                continue
            if k in out and isinstance(out[k], dict) and isinstance(v, dict):
                out[k] = deep_merge(out[k], v)
            else:
                out[k] = v
        return out
    return patch


def run_merge(args):
    result_path = os.path.join(args.output, "result.json")
    if not os.path.exists(result_path):
        sys.stderr.write("result.json 不存在：请先运行 skeleton 模式\n")
        return 0
    try:
        with open(result_path, encoding="utf-8") as fh:
            result = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write("result.json 不可读: %s\n" % exc)
        return 0
    try:
        with open(args.patch, encoding="utf-8") as fh:
            patch = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write("补丁不可读: %s\n" % exc)
        return 0

    meta = patch.get("_meta") if isinstance(patch, dict) else None
    result = deep_merge(result, patch)

    log = result.setdefault("change_log", [])
    if not isinstance(log, list):
        log = []
        result["change_log"] = log
    rev = max((int(x.get("rev", 0)) for x in log if isinstance(x, dict)), default=0) + 1
    log.append({
        "rev": rev,
        "step": (meta or {}).get("step", "S2-S4") if isinstance(meta, dict) else "S2-S4",
        "action": (meta or {}).get("action", "增量合并") if isinstance(meta, dict) else "增量合并",
        "reason": (meta or {}).get("reason", "") if isinstance(meta, dict) else "",
    })
    write_json(result_path, result)
    print("merged rev=%d" % rev)
    return 0


# ---- 主流程 ----
def run_skeleton(args):
    files, warnings = discover(args.input)
    os.makedirs(args.work, exist_ok=True)

    case_path = None
    for f in files:
        base = f["relpath"].rsplit("/", 1)[-1].lower()
        if base in CASE_NAMES and f["status"] == "ok":
            case_path = f["relpath"]
            break
    if case_path is None:
        for f in files:
            if f["relpath"].lower().endswith(".json") and f["status"] == "ok":
                case_path = f["relpath"]
                break

    data = None
    parse_warnings = []
    if case_path is not None:
        data, parse_warnings = load_json(os.path.join(args.input, case_path))
        warnings.extend(parse_warnings)
    else:
        warnings.append("no_case_json")

    # 判空。
    readable = [f for f in files if f["status"] == "ok" and f["ext"] in TEXT_EXT]
    status = "partial"
    if data is None and not readable:
        status = "insufficient_evidence"
    elif case_path is not None and data is None:
        status = "input_error"
    elif data is None:
        status = "insufficient_evidence"

    data = data if isinstance(data, dict) else ({"records": data} if data is not None else None)
    if data is None and status == "insufficient_evidence" and readable:
        data = {}

    result, ledger = build_skeleton(data or {}, files, warnings, case_path)
    if status == "input_error":
        result["status"] = "input_error"
        result["errors"].append("case.json 无法解析：已按 input_error 处理")
    if status == "insufficient_evidence":
        result["status"] = "insufficient_evidence"
        result["uncertainty"].append("无任何可读输入或无法识别 case.json，缺口见 ledger.missing")

    ledger["missing"] = list(dict.fromkeys(ledger["missing"]))
    write_json(os.path.join(args.work, "case_ledger.json"), ledger)
    write_json(os.path.join(args.work, "input_inventory.json"), {"files": files, "warnings": warnings})

    ok, werr = write_json(os.path.join(args.output, "result.json"), result)
    if werr:
        result["errors"].extend(werr)

    print("case_id=%s status=%s plans=%d files=%d" % (
        result["case_id"], result["status"], len(result["plan_set"]), len(files)))
    return 0


def main():
    parser = argparse.ArgumentParser(description="Feasibility Gate Review 发现器/骨架写入器")
    parser.add_argument("mode", nargs="?", default="skeleton", choices=("skeleton", "merge"))
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--work", default=DEFAULT_WORK)
    parser.add_argument("--patch", default=None, help="merge 模式的 JSON 补丁路径")
    args = parser.parse_args()
    if args.mode == "merge":
        return run_merge(args)
    return run_skeleton(args)


if __name__ == "__main__":
    sys.exit(main())
