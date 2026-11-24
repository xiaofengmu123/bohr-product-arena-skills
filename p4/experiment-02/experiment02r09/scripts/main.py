#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验方案可行性审查 — 输入发现与 partial 骨架写入器（main.py）。

职责：在审查最初时刻读取 /input 下的原始输入（case.json、task.md 及材料），
立即写出结构合法、内容诚实的 partial result.json 到 /app/output/result.json，
保证后续任何中途异常都不会留下不完整产物。

约定：
- 仅依赖 Python 标准库；Python >= 3.8。
- 缺省路径 --input/--output/--work 分别指向 /input、/app/output、/app/work。
- 确定性：相同输入产生相同骨架（不写墙钟时间戳）。
- 容错：BOM、坏编码、坏 JSON、超限文件均降级记录，不崩溃。
"""

from __future__ import annotations

import argparse
import json
import os

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2 * 1024 * 1024  # 单个文本材料上限 2MB

CASE_FILE_NAMES = ("case.json", "case.jsonl")
TEXT_EXTS = (".md", ".txt", ".json", ".csv", ".jsonl")
CASE_ID_KEYS = ("case_id", "id", "challenge_id", "caseId", "armiId")

# 题目要求 result.json 至少包含的基础契约字段（案例另附 output_contract 时再原样并入）。
BASE_RESULT = {
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
    "sources": [],
    "uncertainty": [],
    "pending_human": [],
    "limitations": [],
    "errors": [],
    "degradations": [],
    "change_log": [
        {
            "step": "skeleton",
            "action": "写入初始 partial 骨架",
            "reason": "执行流程规定：前 3 分钟内必须先落盘一版结果骨架，之后只做增量改进",
        }
    ],
    "runtime": {
        "started_at": None,
        "seconds": 0.0,
        "requests": 0,
    },
    "artifacts": [],
}


def read_text(path: str, max_bytes: int = MAX_FILE_BYTES):
    """读取文本文件，返回 (text|None, warnings)。BOM/坏编码/超限/缺失均降级。"""
    if not os.path.exists(path):
        return None, [f"missing: {path}"]
    try:
        if os.path.getsize(path) > max_bytes:
            return None, [f"oversize: {path}"]
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, [f"unreadable: {path}: {exc}"]

    # utf-8-sig 处理 BOM；严格 utf-8；gb18030 兼容常见中文旧编码；最后 replace 兜底。
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc), []
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), [f"fallback_decode(replace): {path}"]


def discover_inputs(input_dir: str):
    """扫描输入目录，返回 (files, notes)。files 项含 relpath/abspath/size/ext/status。"""
    files = []
    notes = []
    if not os.path.isdir(input_dir):
        return files, [f"input_dir_missing: {input_dir}"]
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
            files.append(
                {"relpath": rel, "abspath": path, "size": size, "ext": ext, "status": status}
            )
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
        if item["status"] == "ok" and item["relpath"].rsplit("/", 1)[-1] in CASE_FILE_NAMES:
            base = item["relpath"].rsplit("/", 1)[-1].split(".")[0]
            return f"derived:{base}"
    return "unknown"


def default_from_spec(spec):
    """由 output_contract 的字段说明推断缺省值（null/[]/{}/false/''），不更名不删键。"""
    if isinstance(spec, dict):
        t = str(spec.get("type", "")).strip().lower()
        if t in ("array", "list"):
            return []
        if t in ("object", "dict", "map"):
            return {}
        if t in ("number", "int", "float", "numeric", "integer"):
            return None
        if t in ("boolean", "bool"):
            return False
        if "default" in spec:
            return spec["default"]
        if "enum" in spec and isinstance(spec["enum"], list) and spec["enum"]:
            return None
        return None
    if isinstance(spec, (list, tuple)):
        return []
    if isinstance(spec, bool):
        return False
    if isinstance(spec, (int, float)):
        return None
    s = str(spec).strip().lower()
    if s in ("array", "list"):
        return []
    if s in ("object", "dict", "map"):
        return {}
    if s in ("number", "int", "float", "numeric", "integer"):
        return None
    if s in ("boolean", "bool"):
        return False
    return None


def merge_output_contract(result, case_data):
    """把 case.json 的 output_contract 全部键原样回填进 result（缺值降级，不更名不删键）。"""
    if not isinstance(case_data, dict):
        return result
    contract = case_data.get("output_contract")
    if isinstance(contract, dict):
        for key, spec in contract.items():
            if key not in result:
                result[key] = default_from_spec(spec)
    return result


def first_heading(md_text: str):
    """从 task.md 取首个标题作为 task_title；无则返回 None。"""
    if not md_text:
        return None
    for line in md_text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            title = s.lstrip("#").strip()
            if title:
                return title
        elif s:
            break
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="实验方案可行性审查：输入发现与 partial 骨架写入")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--work", default=DEFAULT_WORK)
    args = ap.parse_args(argv)

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    result = dict(BASE_RESULT)
    result["change_log"] = list(BASE_RESULT["change_log"])
    result["overall_conclusion"] = dict(BASE_RESULT["overall_conclusion"])
    result["experiment_plan"] = dict(BASE_RESULT["experiment_plan"])
    result["review"] = dict(BASE_RESULT["review"])
    result["evidence_and_risks"] = dict(BASE_RESULT["evidence_and_risks"])
    result["confirmation"] = dict(BASE_RESULT["confirmation"])
    result["runtime"] = dict(BASE_RESULT["runtime"])

    files, notes = discover_inputs(args.input)
    warnings = list(notes)

    # 定位 case 文件
    case_file = next(
        (f for f in files if f["status"] == "ok" and f["relpath"].rsplit("/", 1)[-1] in CASE_FILE_NAMES),
        None,
    )
    case_data = None
    case_parse_error = None
    if case_file is not None:
        text, w = read_text(case_file["abspath"])
        warnings.extend(w)
        if text is not None:
            try:
                case_data = json.loads(text)
            except (ValueError, TypeError) as exc:
                case_parse_error = f"{case_file['relpath']}: {exc}"
                warnings.append(f"bad_json: {case_parse_error}")

    task_text = None
    task_file = next(
        (f for f in files if f["status"] == "ok" and f["relpath"].rsplit("/", 1)[-1] == "task.md"),
        None,
    )
    if task_file is not None:
        task_text, w = read_text(task_file["abspath"])
        warnings.extend(w)

    result["case_id"] = extract_case_id(case_data, files)
    if isinstance(case_data, dict):
        result["challenge_id"] = case_data.get("challenge_id") or case_data.get("id")
        result["task_title"] = case_data.get("title") or case_data.get("task_title")
    if result["task_title"] is None:
        result["task_title"] = first_heading(task_text)

    merge_output_contract(result, case_data)

    # 状态判定：无可读输入 → insufficient_evidence；case.json 坏 → input_error；否则 partial。
    readable = [f for f in files if f["status"] == "ok"]
    if case_file is None and not readable:
        result["status"] = "insufficient_evidence"
        result["limitations"].append("输入目录无可读文件，无法开展审查")
    elif case_file is not None and case_parse_error is not None:
        result["status"] = "input_error"
        result["limitations"].append("case.json 无法解析，已按输入错误处置，仍保留契约骨架")
    else:
        result["status"] = "partial"

    for w in warnings:
        if w not in result["errors"]:
            result["errors"].append(w)

    # 写盘：主产物 + 输入清单；写盘失败仅记录，进程仍以 0 退出。
    out_path = os.path.join(args.output, "result.json")
    inv_path = os.path.join(args.output, "input_inventory.json")
    write_errors = []
    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        write_errors.append(f"write_failed: {out_path}: {exc}")

    inventory = {
        "input_dir": args.input,
        "case_file": case_file["relpath"] if case_file else None,
        "case_id": result["case_id"],
        "challenge_id": result["challenge_id"],
        "task_title": result["task_title"],
        "status": result["status"],
        "output_contract": (case_data.get("output_contract") if isinstance(case_data, dict) else None),
        "case_top_keys": sorted(case_data.keys()) if isinstance(case_data, dict) else [],
        "files": files,
        "notes": notes,
        "errors": result["errors"],
    }
    try:
        with open(inv_path, "w", encoding="utf-8") as fh:
            json.dump(inventory, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        write_errors.append(f"write_failed: {inv_path}: {exc}")

    print(json.dumps(
        {
            "status": result["status"],
            "case_id": result["case_id"],
            "files_scanned": len(files),
            "case_file": case_file["relpath"] if case_file else None,
            "output_contract_keys": sorted(
                case_data.get("output_contract").keys()
            ) if isinstance(case_data, dict) and isinstance(case_data.get("output_contract"), dict) else [],
            "result": out_path,
            "inventory": inv_path,
            "write_errors": write_errors,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
