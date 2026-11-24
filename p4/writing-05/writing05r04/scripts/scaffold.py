#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scaffold.py — P0 骨架：输入发现 + 契约回显 + 早落盘 partial 结果。

用途（Agent 调用）
  在任何分析开始前，先把一版合法 result.json（status=in_progress）落盘。
  即使后续步骤失败，/app/output 下也始终存在可复核的 partial 产物。

契约要点
  - 原始输入固定 /input/case.json 与 /input/task.md；材料路径由 case.json 指明。
  - 脚本用相对路径调用：python scripts/scaffold.py（--input/--output/--work 可覆盖）。
  - 输出契约：回显 case.json 中 output_contract/outputContract/contract/result_schema
    的每个键（缺值给 null，不改名、不删未知键）；缺失时用默认契约补全。
  - 幂等：若 result.json 已存在且 status != in_progress（已完成的改进版），
    默认不覆盖，除非 --force。防止重跑丢掉已改进结果。

只依赖 Python 标准库。
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

DEFAULT_CONTRACT = {
    "deliverable": "目标产物：可编辑稿件/改稿/引用格式结果/结构化写作产物",
    "sources": "事实与来源映射：数字、术语、引用、图表与关键论断的来源及修改记录",
    "change_log": "版本与边界：修改差异、人工确认项、材料缺口与异常",
}

# result.json 最终必须含 status；skeleton 阶段用 in_progress。
FINAL_STATUS = ("complete", "partial", "failed")
CONTRACT_KEYS = ("output_contract", "outputContract", "contract", "result_schema")
MAX_FILE_BYTES = 2 * 1024 * 1024


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """容错读文本：utf-8-sig -> utf-8 -> gb18030；超限截断并记录。"""
    try:
        with open(path, "rb") as f:
            raw = f.read(max_bytes)
    except OSError as e:
        return None, False, None, "read_error: %s" % e
    truncated = len(raw) >= max_bytes
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc), truncated, enc, None
        except UnicodeDecodeError:
            continue
    return None, truncated, None, "undecodable_text"


def load_json(path):
    txt, _, _, err = read_text(path)
    if err or txt is None:
        return None, (err or "empty_or_unreadable")
    try:
        return json.loads(txt), None
    except json.JSONDecodeError as e:
        return None, "bad_json: %s" % e


def walk_input(inp):
    """列出输入目录下所有文件（含大小），不依赖固定文件名。"""
    out = []
    if not os.path.isdir(inp):
        return out, ["input_dir_missing: %s" % inp]
    for root, dirs, files in os.walk(inp):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in sorted(files):
            p = os.path.join(root, fn)
            try:
                size = os.path.getsize(p)
            except OSError:
                size = -1
            out.append({"path": p, "rel": os.path.relpath(p, inp),
                        "size": size, "over_limit": size > MAX_FILE_BYTES})
    return out, []


def extract_case_id(case):
    """case_id 优先级：case_id > caseId > id > armiId。"""
    if not isinstance(case, dict):
        return None
    for k in ("case_id", "caseId", "id", "armiId"):
        v = case.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)):
            return str(v)
    return None


def extract_contract(case):
    """回显题目 output_contract；无则用默认契约。"""
    if isinstance(case, dict):
        for k in CONTRACT_KEYS:
            v = case.get(k)
            if isinstance(v, dict):
                return {kk: vv for kk, vv in v.items()}
    return dict(DEFAULT_CONTRACT)


def build_skeleton(case_id, contract, case_path, task_path, inventory, errors):
    result = {
        "case_id": case_id,
        "status": "in_progress",
        "task": None,
        "deliverable": None,
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "change_log": [
            {"time": utcnow(), "action": "scaffold",
             "detail": "P0 骨架落盘：status=in_progress，回显输出契约，盘点输入目录"}
        ],
        "runtime": {
            "started_at": utcnow(), "duration_s": None,
            "budget_s": 900, "max_requests": 64, "requests_used": None
        },
        "_meta": {
            "case_json": case_path, "task_md": task_path,
            "contract_source": "case.json output_contract" if contract != DEFAULT_CONTRACT
                               else "default_contract",
            "input_files": [i["rel"] for i in inventory if not i["over_limit"]],
            "skipped_files": [i["rel"] for i in inventory if i["over_limit"]],
            "discovery_errors": errors,
        },
    }
    # 回显契约键：优先契约键，缺省补充核心键，不覆盖已有键。
    for k in contract:
        if k not in result:
            result[k] = None
    for k in ("deliverable", "sources", "change_log"):
        if k not in result:
            result[k] = None
    # 补齐运行时必填的结构字段（防止 final 阶段缺失）。
    if result.get("sources") is None:
        result["sources"] = []
    if result.get("change_log") is None:
        result["change_log"] = []
    return result


def main():
    ap = argparse.ArgumentParser(description="P0 骨架：早落盘 result.json 与输入发现")
    ap.add_argument("--input", default="/input", help="原始输入目录")
    ap.add_argument("--output", default="/app/output", help="产物目录")
    ap.add_argument("--work", default="/app/work", help="工作目录")
    ap.add_argument("--force", action="store_true",
                    help="即使已有非 in_progress 版本也覆盖")
    a = ap.parse_args()

    os.makedirs(a.output, exist_ok=True)
    os.makedirs(a.work, exist_ok=True)

    case_path = os.path.join(a.input, "case.json")
    task_path = os.path.join(a.input, "task.md")
    inventory, inv_errors = walk_input(a.input)
    errors = list(inv_errors)

    case, case_err = load_json(case_path) if os.path.isfile(case_path) else (None, "case_json_missing")
    if case_err:
        errors.append("case_json: %s" % case_err)

    case_id = extract_case_id(case) if isinstance(case, dict) else None
    contract = extract_contract(case) if isinstance(case, dict) else dict(DEFAULT_CONTRACT)

    # 幂等：已有非 in_progress 版本默认不覆盖。
    out_json = os.path.join(a.output, "result.json")
    if os.path.isfile(out_json) and not a.force:
        prev, prev_err = load_json(out_json)
        if prev_err is None and isinstance(prev, dict) \
           and prev.get("status") not in (None, "in_progress"):
            print(json.dumps({"ok": True, "skipped": True,
                              "reason": "existing_finalized_result_not_overwritten",
                              "status": prev.get("status")}, ensure_ascii=False))
            return 0

    result = build_skeleton(case_id, contract, case_path, task_path, inventory, errors)

    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 工作目录落盘发现结果，供后续脚本复用。
    discovery = {
        "case_id": case_id, "contract": contract, "errors": errors,
        "inventory": inventory, "generated_at": utcnow(),
    }
    with open(os.path.join(a.work, "discovery.json"), "w", encoding="utf-8") as f:
        json.dump(discovery, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "ok": True, "case_id": case_id, "status": "in_progress",
        "contract_keys": sorted(contract.keys()),
        "files_found": len(inventory), "errors": errors,
        "out": out_json,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
