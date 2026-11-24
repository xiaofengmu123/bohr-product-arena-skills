#!/usr/bin/env python3
"""skeleton.py — 执行流程第 0 步：立即落盘合法 result.json 骨架与 state.json。

设计目标：前 3 分钟内完成；此后任何异常都不会留下不完整产物。
只依赖 Python 标准库，确定性执行；--input/--output/--work 缺省为
/input、/app/output、/app/work。
"""
import argparse
import datetime
import json
import os
import time

ALLOWED_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")

# output_contract 缺失时的默认字段清单（键值表示占位符；最终产物路径相对 /app/output/）
DEFAULT_CONTRACT = {
    "manuscript": "manuscript.md",
    "evidence_map": "evidence_map.json",
    "preserved_facts": "preserved_facts.json",
}

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")


def read_text(path):
    """按多编码容错读取文本文件，失败返回 None。"""
    for enc in ENCODINGS:
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError:
            return None
    return None


def load_json(path):
    """读取并解析 JSON；坏 JSON / 坏编码 / 文件缺失一律返回 None（不崩溃）。"""
    raw = read_text(path)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def placeholder_for(value):
    """依据 output_contract 字段声明，返回对应空值占位（None / [] / {}）。"""
    if value is None:
        return None
    if isinstance(value, dict):
        return {}
    if isinstance(value, list):
        return []
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("array", "list", "string[]", "array<string>"):
            return []
        if s in ("object", "dict", "map", "record"):
            return {}
        if s in ("string", "text", "str", "number", "int", "integer",
                 "float", "double", "bool", "boolean"):
            return None
        try:
            return placeholder_for(json.loads(value))
        except (ValueError, TypeError):
            return None
    return None


def normalize_contract(value):
    """把 output_contract 归一化成 dict，键名原样保留、不改名不删键。"""
    if value is None:
        return dict(DEFAULT_CONTRACT)
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return {str(k): None for k in value}
    if isinstance(value, str):
        try:
            return normalize_contract(json.loads(value))
        except (ValueError, TypeError):
            return {value: None}
    return dict(DEFAULT_CONTRACT)


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")


def main(argv=None):
    ap = argparse.ArgumentParser(description="落盘 result.json 骨架与 state.json")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args(argv)

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    start_epoch = time.time()
    start_iso = now_iso()

    case_path = os.path.join(args.input, "case.json")
    task_path = os.path.join(args.input, "task.md")
    case = load_json(case_path)
    task = read_text(task_path)

    errors = []
    degradations = []

    if case is None:
        case_id = "unknown"
        status = "input_error"
        errors.append("无法读取 /input/case.json 或内容不是合法 JSON")
    else:
        case_id = case.get("case_id") or case.get("id") or case.get("challenge_id") or "unknown"
        status = "partial"

    contract = normalize_contract(case.get("output_contract") if case else None)

    result = {"case_id": case_id}
    for key, decl in contract.items():
        if key in result:
            errors.append("output_contract 字段 %r 与标准字段冲突，保留标准字段" % key)
            continue
        result[key] = placeholder_for(decl)

    result.update({
        "status": status,
        "artifacts": [],
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "errors": errors,
        "degradations": degradations,
        "limitations": [],
        "pending_human": [],
        "runtime": {"start": start_iso, "end": None, "seconds": None},
    })
    result["change_log"].append({
        "step": 0,
        "action": "落盘 result.json 骨架与 state.json",
        "status": status,
        "timestamp": start_iso,
    })

    out_path = os.path.join(args.output, "result.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    state = {
        "case_id": case_id,
        "status": status,
        "contract_keys": sorted(contract.keys()),
        "case_json_ok": case is not None,
        "task_md_ok": task is not None,
        "errors": errors,
        "started_at": start_iso,
        "start_epoch": start_epoch,
    }
    with open(os.path.join(args.work, "state.json"), "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)

    print(json.dumps({
        "ok": True,
        "case_id": case_id,
        "status": status,
        "contract_keys": sorted(contract.keys()),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
