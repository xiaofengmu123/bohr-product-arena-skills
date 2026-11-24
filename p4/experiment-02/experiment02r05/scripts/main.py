#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验方案可行性审查 — 输入发现、契约回填与结果骨架写入器（main.py）

职责：在审查最初时刻读取 /input 下的原始输入（case.json、task.md 及材料），
立即写出一份"结构合法、内容诚实"的 partial result.json 到 /app/output/result.json，
保证后续任何中途异常都不会留下不完整产物。

设计取舍（contract-first）：
- 不写死固定 schema。若 case.json 含 output_contract，则递归回填其全部字段；
  否则回退到本任务标准契约（experiment_plan / evidence_and_risks / confirmation）。
- 幂等：已存在 result.json 时只补齐缺失契约字段并保留已填内容，用于增量改进。
- 确定性：相同输入产生相同骨架（不写墙钟时间戳）。
- 容错：BOM、坏编码、坏 JSON、超限文件均降级记录，不崩溃。
仅依赖 Python 标准库；Python >= 3.8。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2 * 1024 * 1024  # 单个文本材料上限 2MB
SCHEMA_VERSION = "2.0.0"

CASE_FILE_NAMES = ("case.json", "case.jsonl")
CASE_ID_KEYS = ("case_id", "id", "challenge_id", "caseId", "armiId")

# 任务标准契约：与题目“Skill 运行后的输出要求”三个内容块一一对应。
STANDARD_CONTRACT = {
    "experiment_plan": {
        "research_question": "",
        "assumptions": [],
        "variables": [],
        "controls": [],
        "steps": [],
        "parameters": [],
        "resources": [],
        "interpretation_criteria": [],
    },
    "evidence_and_risks": {
        "evidence": [],
        "risks": [],
        "exception_handling": [],
    },
    "confirmation": {
        "safety": [],
        "ethics": [],
        "equipment_availability": [],
        "expert_confirmation": [],
    },
}

BASE_META = {
    "case_id": None,
    "challenge_id": None,
    "task_title": None,
    "status": "partial",
    "overall_conclusion": {
        "verdict": None,
        "summary": None,
        "conditions": [],
        "blocks": [],
        "notes": [],
    },
    "review": {
        "assumptions_variables": [],
        "controls": [],
        "equipment_materials": [],
        "time_cost": [],
        "parameter_ranges": [],
        "risks_exceptions": [],
        "ethics_safety": [],
    },
    "plan_comparison": [],
    "sources": [],
    "uncertainty": [],
    "pending_human": [],
    "errors": [],
    "degradations": [],
    "change_log": [],
    "contract_fidelity": {},
    "runtime": {
        "started_at": None,
        "seconds": 0.0,
        "requests": 0,
    },
    "artifacts": [],
}


def read_text(path: str, max_bytes: int = MAX_FILE_BYTES):
    """读取文本文件，返回 (text, warnings)。BOM/坏编码/超限/缺失均降级。"""
    if not os.path.exists(path):
        return None, ["missing: %s" % path]
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, ["unreadable: %s: %s" % (path, exc)]
    if size > max_bytes:
        return None, ["oversize(%d bytes > %d): %s" % (size, max_bytes, path)]
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, ["unreadable: %s: %s" % (path, exc)]
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc), []
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), ["fallback_decode(replace): %s" % path]


def discover_inputs(input_dir: str):
    """扫描输入目录，返回 (files, notes)。files 项含 relpath/abspath/size/ext/status。"""
    files, notes = [], []
    if not os.path.isdir(input_dir):
        notes.append("input_dir_missing: %s" % input_dir)
        return files, notes
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
            if size > MAX_FILE_BYTES and ext in (".md", ".txt", ".json", ".csv", ".jsonl"):
                status = "oversize"
                notes.append("oversize: %s (%d bytes)" % (rel, size))
            files.append({"relpath": rel, "abspath": path, "size": size,
                          "ext": ext, "status": status})
    return files, notes


def extract_case_id(data, files):
    """从 case.json 识别 case_id；失败时从文件名派生，再退化为 unknown。"""
    if isinstance(data, dict):
        for key in CASE_ID_KEYS:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, (int, float)):
                return str(value)
    for item in files:
        if item["status"] == "ok":
            base = item["relpath"].rsplit("/", 1)[-1]
            if base in CASE_FILE_NAMES:
                return "derived:%s" % base.split(".")[0]
    return "unknown"


def infer_kind(spec):
    """由 output_contract 的字段说明推断类型。"""
    if isinstance(spec, bool):
        return "boolean"
    if isinstance(spec, (int, float)):
        return "number"
    if isinstance(spec, (list, tuple)):
        return "array"
    if isinstance(spec, dict):
        if "type" in spec:
            t = spec["type"]
            if isinstance(t, str):
                s = t.strip().lower()
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
                if s in ("null", "none"):
                    return "null"
            return "string"
        if "default" in spec:
            return infer_kind(spec["default"])
        if "properties" in spec or "fields" in spec:
            return "object"
        if "items" in spec:
            return "array"
        return "object"
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
        if s in ("null", "none"):
            return "null"
        return "string"
    return "string"


def default_for_kind(kind):
    return {
        "string": "",
        "number": None,
        "integer": None,
        "boolean": False,
        "array": [],
        "object": {},
        "null": None,
    }.get(kind, None)


def build_skeleton_from_spec(spec):
    """递归构造契约骨架：object 按 properties/fields 展开；array 保持 []（不预填元素）。"""
    kind = infer_kind(spec)
    if kind == "object" and isinstance(spec, dict):
        props = spec.get("properties") or spec.get("fields")
        if isinstance(props, dict):
            return {k: build_skeleton_from_spec(v) for k, v in props.items()}
        return {}
    if kind == "array":
        return []
    return default_for_kind(kind)


def resolve_contract(case_data):
    """返回 (contract, fidelity)。contract 为 {字段名: 骨架值}。
    支持三种 output_contract 形态：dict（字段名→说明）、list of {name,type}、
    list of 字段名字符串；缺省回退标准契约。"""
    if isinstance(case_data, dict) and "output_contract" in case_data:
        raw = case_data.get("output_contract")
        if isinstance(raw, dict):
            contract, fields = {}, []
            for key, spec in raw.items():
                value = build_skeleton_from_spec(spec)
                contract[key] = value
                fields.append({
                    "name": key, "type": infer_kind(spec),
                    "status": "schema_built" if isinstance(spec, dict) else "echoed",
                })
            fidelity = {
                "source": "case.json#output_contract",
                "field_count": len(contract),
                "fields": fields,
                "unknown_fields_preserved": True,
                "renamed_fields": [],
                "dropped_fields": [],
            }
            return contract, fidelity
        if isinstance(raw, list):
            contract, fields = {}, []
            for item in raw:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("field") or item.get("key")
                    if name:
                        contract[name] = build_skeleton_from_spec(item)
                        fields.append({"name": name, "type": infer_kind(item),
                                       "status": "schema_built"})
                elif isinstance(item, str) and item.strip():
                    contract[item.strip()] = ""
                    fields.append({"name": item.strip(), "type": "string",
                                   "status": "echoed"})
            fidelity = {
                "source": "case.json#output_contract(list)",
                "field_count": len(contract),
                "fields": fields,
                "unknown_fields_preserved": True,
                "renamed_fields": [],
                "dropped_fields": [],
            }
            return contract, fidelity
    # 回退标准契约
    contract = json.loads(json.dumps(STANDARD_CONTRACT))
    fields = [{"name": k, "type": infer_kind(v), "status": "schema_built"}
              for k, v in contract.items()]
    fidelity = {
        "source": "task_standard_fallback",
        "field_count": len(contract),
        "fields": fields,
        "unknown_fields_preserved": True,
        "renamed_fields": [],
        "dropped_fields": [],
        "note": "case.json 未提供 output_contract，采用题目标准契约",
    }
    return contract, fidelity


def merge_preserving(existing, incoming):
    """把 incoming 中缺失的键补齐到 existing，已存在的键保留现有内容（增量不覆盖）。"""
    if not isinstance(existing, dict):
        return existing
    for key, value in incoming.items():
        if key not in existing:
            existing[key] = value
        elif isinstance(value, dict) and isinstance(existing.get(key), dict):
            merge_preserving(existing[key], value)
    return existing


def digest(obj):
    payload = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def build_skeleton(case_data, files, case_id, status):
    """构造完整 result.json 骨架（契约字段 + 元信息 + 审查主产物）。"""
    contract, fidelity = resolve_contract(case_data)
    skeleton = dict(contract)  # 契约字段先放顶层
    meta = json.loads(json.dumps(BASE_META))
    meta["case_id"] = case_id
    meta["status"] = status
    meta["challenge_id"] = extract_challenge_id(case_data)
    meta["task_title"] = extract_title(case_data)
    meta["contract_fidelity"] = fidelity
    meta["change_log"] = [{
        "step": "skeleton",
        "action": "写入初始 partial 骨架",
        "reason": "执行流程规定：前 3 分钟内必须先落盘一版结果骨架，之后只做增量改进",
        "hash": digest({k: skeleton.get(k) for k in contract}),
    }]
    skeleton.update(meta)
    return skeleton


def extract_challenge_id(case_data):
    if isinstance(case_data, dict):
        for key in ("challenge_id", "challengeId"):
            v = case_data.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def extract_title(case_data):
    if isinstance(case_data, dict):
        for key in ("title", "task_title", "name"):
            v = case_data.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def load_json_file(path):
    text, warns = read_text(path)
    if text is None:
        return None, warns
    try:
        return json.loads(text), warns
    except ValueError as exc:
        return None, warns + ["bad_json: %s: %s" % (path, exc)]


def find_case_file(files):
    for item in files:
        base = item["relpath"].rsplit("/", 1)[-1]
        if base in CASE_FILE_NAMES and item["status"] == "ok":
            return item
    return None


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def write_report_skeleton(path, case_id, status, contract):
    lines = [
        "# 实验方案可行性审查报告（草稿）",
        "",
        "- case_id: `%s`" % case_id,
        "- status: `%s`" % status,
        "- schema_version: `%s`" % SCHEMA_VERSION,
        "",
        "## 待复核（S5 收口前为草稿，须与 result.json 同内容）",
        "",
        "以下区块由 Agent 按七类约束逐条填写；每条须带 locator + 逐字 quote。",
        "",
        "### 实验方案",
        "`%s`（见 result.json#experiment_plan，逐字段填：假设、变量、对照、步骤、参数、资源、判读标准）"
        % ("experiment_plan" if "experiment_plan" in contract else "见 output_contract"),
        "",
        "### 依据与风险",
        "见 result.json#evidence_and_risks。",
        "",
        "### 确认项",
        "见 result.json#confirmation。",
        "",
        "## 七类审查",
        "",
        "1. assumptions_variables",
        "2. controls",
        "3. equipment_materials",
        "4. time_cost",
        "5. parameter_ranges",
        "6. risks_exceptions",
        "7. ethics_safety",
        "",
        "## 证据定位说明",
        "",
        "locator 规范：`case.json#plan.<i>.steps[<j>]`、`case.json#contracts.<key>`、"
        "`materials/<name>.md#<标题或行>`、`sources/<name>.pdf#p<页>`、`task.md#<行>`。",
        "",
    ]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def main(argv=None):
    ap = argparse.ArgumentParser(description="可行性审查骨架写入器")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--work", default=DEFAULT_WORK)
    args = ap.parse_args(argv)

    for d in (args.output, args.work):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as exc:
            print(json.dumps({"fatal": "cannot_make_dir", "path": d, "error": str(exc)},
                             ensure_ascii=False))
            return 1

    files, notes = discover_inputs(args.input)
    inventory = {
        "input_dir": args.input,
        "files": files,
        "notes": notes,
        "schema_version": SCHEMA_VERSION,
    }
    inventory_path = os.path.join(args.work, "input_inventory.json")
    try:
        write_json(inventory_path, inventory)
    except OSError as exc:
        notes.append("inventory_write_failed: %s" % exc)

    case_item = find_case_file(files)
    case_data = None
    errors = list(notes)
    status = "partial"
    if case_item is None:
        status = "insufficient_evidence" if not files else "input_error"
        errors.append("no_case_file_found: expected case.json or case.jsonl")
    else:
        case_data, warns = load_json_file(case_item["abspath"])
        errors.extend(warns)
        if case_data is None:
            status = "input_error"
        elif isinstance(case_data, dict):
            if not case_data:
                status = "insufficient_evidence"
        else:
            status = "input_error"

    case_id = extract_case_id(case_data, files)
    skeleton = build_skeleton(case_data or {}, files, case_id, status)
    skeleton["errors"] = errors
    if not files:
        skeleton["degradations"].append("input_dir_empty_or_missing")

    result_path = os.path.join(args.output, "result.json")
    # 幂等：已有结果时只补齐缺失契约字段，保留已填内容。
    if os.path.exists(result_path):
        existing, warns = load_json_file(result_path)
        if isinstance(existing, dict):
            contract, _fid = resolve_contract(case_data or {})
            merged = merge_preserving(existing, contract)
            merged["case_id"] = merged.get("case_id") or case_id
            if merged.get("status") == "partial" and status == "ok":
                merged["status"] = "ok"
            if not merged.get("contract_fidelity"):
                merged["contract_fidelity"] = _fid
            skeleton = merged
            if warns:
                skeleton.setdefault("errors", []).extend(warns)
        else:
            errors.append("existing_result_invalid_json: rebuilt skeleton")

    try:
        write_json(result_path, skeleton)
    except OSError as exc:
        minimal = {"case_id": case_id, "status": "input_error",
                   "errors": ["result_write_failed: %s" % exc]}
        try:
            write_json(result_path, minimal)
        except OSError:
            pass
        print(json.dumps({"fatal": "result_write_failed", "error": str(exc)},
                         ensure_ascii=False))
        return 1

    report_path = os.path.join(args.output, "review_report.md")
    try:
        write_report_skeleton(report_path, case_id, status,
                              resolve_contract(case_data or {})[0])
        if "review_report.md" not in skeleton.get("artifacts", []):
            skeleton.setdefault("artifacts", []).append("review_report.md")
        write_json(result_path, skeleton)
    except OSError as exc:
        skeleton.setdefault("errors", []).append("report_write_failed: %s" % exc)
        write_json(result_path, skeleton)

    summary = {
        "case_id": case_id,
        "status": status,
        "contract_source": skeleton.get("contract_fidelity", {}).get("source"),
        "contract_fields": skeleton.get("contract_fidelity", {}).get("field_count"),
        "result_path": result_path,
        "input_files": len(files),
        "errors": skeleton.get("errors", []),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
