#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验方案可行性审查 — 输入发现与 partial 结果骨架写入器。

本脚本是整个 Skill 的确定性“落盘锚点”：在审查最初时刻读取 /input 下的原始输入
（case.json、task.md 及其余材料），立即写出结构合法、内容诚实的 partial result.json
到 /app/output/result.json，保证后续任何中途异常都不会留下不完整产物。

设计要点（与“降级与不确定性”变体一致）：
- 仅依赖 Python 标准库，Python >= 3.8；python3 与 python 均可运行。
- 确定性：相同输入产出相同骨架（不写墙钟时间戳，runtime.started_at 为 null）。
- 容错：BOM、坏编码、坏 JSON、超限文件均降级记录，进程不崩溃，退出码恒为 0。
- 契约回填：若 case.json 含 output_contract，其全部字段名原样回填进 result.json
  顶层（缺值给 null / []，不更名、不删键），并把回填映射记入 contract 字段。
- 缺数据不编造：所有占位值用 null / [] 表达“未知 / 未填”，绝不写示例答案。
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def _force_utf8_stdio():
    """强制 stdout/stderr 为 UTF-8（Linux 上无操作，保证输出编码确定）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2 * 1024 * 1024  # 单个文本材料上限 2MB

CASE_FILE_NAMES = ("case.json", "case.jsonl")
TEXT_EXTS = {".md", ".txt", ".json", ".jsonl", ".csv", ".yaml", ".yml"}

STATUS_ENUM = ("ok", "partial", "insufficient_evidence", "input_error")

CASE_ID_KEYS = ("case_id", "id", "challenge_id", "caseId", "armiId")


def base_result():
    """返回一份全新的结果骨架（避免可变默认值共享）。"""
    return {
        "case_id": None,
        "challenge_id": None,
        "task_title": None,
        "status": "partial",
        "overall_conclusion": {
            "verdict": None,       # executable | conditional | blocked | unconfirmed
            "summary": None,
            "conditions": [],
            "blocks": [],
            "notes": [],
        },
        "experiment_plan": {
            "research_question": None,
            "assumptions": [],
            "variables": [],
            "controls": [],
            "steps": [],
            "parameters": [],
            "resources": [],
            "interpretation_criteria": [],
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
        # 本版强化：限制与不确定性作为一等对象显式登记。
        "limitations": {
            "evidence_scope": [],
            "arithmetic_uncertainty": [],
            "source_quality": [],
            "missing_contract": [],
            "conflicting_sources": [],
            "unverifiable_claims": [],
        },
        "uncertainty": [],
        "pending_human": [],
        "sources": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "runtime": {"started_at": None, "seconds": 0.0, "requests": 0},
        "artifacts": [],
        "contract": {"source": None, "echoed_fields": []},
    }


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """读取文本文件，返回 (text, warnings)。BOM/坏编码/超限/缺失均降级。"""
    if not os.path.exists(path):
        return None, [f"missing: {path}"]
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, [f"unreadable: {path}: {exc}"]
    if size > max_bytes:
        return None, [f"oversize({size} bytes > {max_bytes}): {path}"]
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, [f"unreadable: {path}: {exc}"]

    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc), []
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), [f"fallback_decode(replace): {path}"]


def discover_inputs(input_dir):
    """扫描输入目录，返回 (files, notes)。不假设固定文件名/键名。"""
    files, notes = [], []
    if not os.path.isdir(input_dir):
        notes.append(f"input_dir_missing: {input_dir}")
        return files, notes
    for root, dirs, names in os.walk(input_dir):
        dirs.sort()
        for name in sorted(names):
            path = os.path.join(root, name)
            try:
                if os.path.islink(path):
                    notes.append(f"skipped_symlink: {path}")
                    continue
                size = os.path.getsize(path)
            except OSError as exc:
                notes.append(f"unreadable: {path}: {exc}")
                continue
            rel = os.path.relpath(path, input_dir).replace("\\", "/")
            ext = os.path.splitext(name)[1].lower()
            status = "ok"
            if size > MAX_FILE_BYTES and ext in TEXT_EXTS:
                status = "oversize"
                notes.append(f"oversize: {rel} ({size} bytes)")
            files.append({
                "relpath": rel, "abspath": path, "size": size,
                "ext": ext, "status": status,
            })
    return files, notes


def extract_case_id(data, files):
    """识别 case_id；失败时从文件名派生，再退化为 unknown。"""
    if isinstance(data, dict):
        for key in CASE_ID_KEYS:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, (int, float)):
                return str(value)
    for item in files:
        base = item["relpath"].rsplit("/", 1)[-1]
        if item["status"] == "ok" and base in CASE_FILE_NAMES:
            return "derived:" + base.split(".")[0]
    return "unknown"


def extract_meta(data):
    """从 case.json 提取 challenge_id 与标题（可能缺失，用 None）。"""
    challenge_id, title = None, None
    if isinstance(data, dict):
        for key in ("challenge_id", "challengeId"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                challenge_id = value.strip()
                break
        for key in ("title", "task_title", "name"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                title = value.strip()
                break
    return challenge_id, title


def infer_kind(spec):
    """由 output_contract 字段说明推断缺省类型。"""
    if isinstance(spec, str):
        s = spec.strip().lower()
        if s in ("string", "str", "text", "markdown", "md"):
            return "string"
        if s in ("number", "int", "integer", "float", "numeric"):
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
        if "type" in spec:
            return infer_kind(spec["type"])
        if "default" in spec:
            return infer_kind(spec["default"])
        return "object"
    return "string"


def default_for_kind(kind):
    """缺省占位值：数组给 []，标量给 null，表达“未填”，不编造。"""
    return {"array": [], "object": None}.get(kind, None)


def normalize_contract(raw):
    """把 output_contract 归一化为 {field: spec}；无法识别则返回空 dict。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        out = {}
        for item in raw:
            if isinstance(item, dict) and "name" in item:
                out[item["name"]] = item.get("type", item.get("default", "string"))
        return out
    return {}


def load_case_json(files):
    """读 case.json/case.jsonl，返回 (data, parse_error_text)。"""
    data, parse_error = None, None
    candidates = [f for f in files if f["relpath"].rsplit("/", 1)[-1] in CASE_FILE_NAMES
                  and f["status"] == "ok"]
    for f in candidates:
        text, _warns = read_text(f["abspath"])
        if text is None:
            continue
        try:
            data = json.loads(text)
            parse_error = None
            break
        except (json.JSONDecodeError, ValueError) as exc:
            parse_error = f"{f['relpath']}: {exc}"
    if data is None and parse_error is None and candidates:
        parse_error = "case.json present but empty/unreadable"
    return data, parse_error


def build_skeleton(case_data, parse_error, case_id, files, notes, task_text):
    """组装 partial 骨架，诚实标注状态、限制与降级。"""
    result = base_result()
    result["case_id"] = case_id
    challenge_id, title = extract_meta(case_data)
    result["challenge_id"] = challenge_id
    result["task_title"] = title

    # 1) 契约回填：output_contract 字段名原样回填到顶层。
    contract_raw = None
    if isinstance(case_data, dict):
        contract_raw = case_data.get("output_contract") or case_data.get("outputContract")
    contract = normalize_contract(contract_raw)
    for field, spec in contract.items():
        kind = infer_kind(spec)
        result[field] = default_for_kind(kind)  # 顶层原样回填
        result["contract"]["echoed_fields"].append({"name": field, "kind": kind})
    result["contract"]["source"] = "case.json#output_contract" if contract else None

    # 2) 输入级降级记录。
    degradations = []
    for n in notes:
        if n.startswith("oversize"):
            degradations.append({"scope": "input_file", "reason": n, "effect": "skipped_or_partial_read"})
    if parse_error:
        degradations.append({"scope": "input_json", "reason": parse_error, "effect": "case_data_unavailable"})
    result["degradations"] = degradations
    result["errors"] = [{"scope": "input", "message": n} for n in notes if not n.startswith("oversize")]

    # 3) 状态判定：无任何可读输入 -> insufficient_evidence；坏 JSON -> input_error；否则 partial。
    readable = [f for f in files if f["status"] == "ok"]
    if not files or not readable:
        result["status"] = "insufficient_evidence"
        result["overall_conclusion"]["verdict"] = "blocked"
        result["overall_conclusion"]["blocks"] = ["输入目录不存在或没有任何可读文件"]
        result["limitations"]["missing_contract"].append({
            "id": "lim-input-empty", "kind": "missing_contract",
            "claim": "无任何可读输入材料", "severity": "blocker",
            "affects": ["experiment_plan", "review"],
            "basis": "输入目录为空或全部文件缺失/超限", "resolution": "补齐 /input/case.json 与 /input/task.md",
            "needs_expert": False,
        })
    elif parse_error:
        result["status"] = "input_error"
        result["overall_conclusion"]["verdict"] = "blocked"
        result["overall_conclusion"]["blocks"] = ["case.json 无法解析，无法读取研究目标与方案"]
        result["limitations"]["missing_contract"].append({
            "id": "lim-json-broken", "kind": "missing_contract",
            "claim": "case.json 解析失败", "severity": "blocker",
            "affects": ["experiment_plan", "review", "plan_comparison"],
            "basis": parse_error, "resolution": "修复 JSON 语法或换用有效编码后重跑", "needs_expert": False,
        })
    else:
        result["status"] = "partial"
        result["overall_conclusion"]["verdict"] = None
        result["limitations"]["evidence_scope"].append({
            "id": "lim-skeleton", "kind": "evidence_scope",
            "claim": "当前为 S0 骨架，尚未完成七类约束逐条审查",
            "severity": "major", "affects": ["review"],
            "basis": "执行流程规定前 3 分钟先落盘 partial 骨架，之后只做增量改进",
            "resolution": "后续步骤按证据溯源规则逐条回填 review.*", "needs_expert": False,
        })

    result["change_log"].append({
        "step": "S0_skeleton", "action": "写入 partial 结果骨架",
        "reason": "执行流程硬规则：前 3 分钟内必须先落盘一版结果骨架，之后只做增量改进",
    })

    # 4) 输入清单（仅记事实，不含结论）。
    return result


def write_json_atomic(path, payload):
    """写 JSON（ensure_ascii=False）；失败返回 False 并尽量降级写最小产物。"""
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        return True
    except OSError as exc:
        minimal = {"case_id": payload.get("case_id"), "status": "input_error",
                   "errors": [{"scope": "write", "message": str(exc)}]}
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(minimal, fh, ensure_ascii=False, indent=2)
        except OSError:
            pass
        return False


def main(argv=None):
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description="实验方案可行性审查 - 输入发现与骨架写入")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--work", default=DEFAULT_WORK)
    args = ap.parse_args(argv)

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    files, notes = discover_inputs(args.input)
    case_data, parse_error = load_case_json(files)
    case_id = extract_case_id(case_data, files)

    task_text = None
    for f in files:
        if f["status"] == "ok" and f["relpath"].rsplit("/", 1)[-1] in ("task.md",):
            task_text, _ = read_text(f["abspath"])
            break

    result = build_skeleton(case_data, parse_error, case_id, files, notes, task_text)

    ok1 = write_json_atomic(os.path.join(args.output, "result.json"), result)
    inventory = {
        "case_id": case_id,
        "files": files,
        "notes": notes,
        "parse_error": parse_error,
        "status": result["status"],
    }
    write_json_atomic(os.path.join(args.work, "input_inventory.json"), inventory)

    summary = {
        "case_id": case_id,
        "status": result["status"],
        "input_files": len(files),
        "degradations": len(result["degradations"]),
        "result_written": ok1,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
