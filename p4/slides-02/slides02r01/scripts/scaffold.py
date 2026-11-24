#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scaffold.py — 落盘 result.json 骨架（前 3 分钟内先调用）。

只依赖 Python 标准库，不依赖当前工作目录。默认从 /input/case.json 读取，
产物写 /app/output/result.json 与 /app/work/case_summary.json；
目录可用 INPUT_DIR / WORK_DIR / OUTPUT_DIR 环境变量覆盖。
坏 JSON / 坏编码 / 空输入都不崩溃，仍写一份合法 partial 结果。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

DEFAULT_CONTRACT = [
    "presentation_file", "preview_files", "speaker_notes",
    "page_plan", "evidence_map", "version_records",
]

RESERVED = ["case_id", "status", "sources", "evidence_map",
            "uncertainty", "pending_human", "change_log"]

MAX_TEXT = 2_000_000


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_json(path):
    """utf-8-sig 容错读取 JSON；坏文件返回 (None, 错误信息)。"""
    if not path or not os.path.isfile(path):
        return None, "missing: %s" % (path or "<none>")
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f), None
    except Exception as e:  # noqa: BLE001
        return None, "bad json: %s (%s)" % (path, e)


def read_text(path, limit=MAX_TEXT):
    """容错读文本，超限跳过。"""
    if not path or not os.path.isfile(path):
        return None, "missing"
    size = os.path.getsize(path)
    if size > limit:
        return None, "too_large:%d>%d" % (size, limit)
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return f.read(), None
    except Exception as e:  # noqa: BLE001
        return None, "read_error:%s" % e


def extract_contract(case):
    """从 case.json 抽取 output_contract 字段名列表；缺省用默认集合。"""
    if isinstance(case, dict) and "output_contract" in case:
        oc = case["output_contract"]
        if isinstance(oc, list):
            fields = [str(x) for x in oc if isinstance(x, str)]
            if fields:
                return fields
        if isinstance(oc, dict):
            for key in ("fields", "keys", "required"):
                if isinstance(oc.get(key), list):
                    fields = [str(x) for x in oc[key] if isinstance(x, str)]
                    if fields:
                        return fields
            fields = [str(k) for k in oc.keys() if k != "type"]
            if fields:
                return fields
    return list(DEFAULT_CONTRACT)


def build_result(case_id, contract_fields, errors):
    result = {"case_id": case_id, "status": "partial"}
    for f in contract_fields:
        result.setdefault(f, None)
    for r in RESERVED:
        if r not in result:
            result[r] = None
    result["sources"] = []
    result["evidence_map"] = []
    result["uncertainty"] = {"unresolved": [], "missing_artifacts": [], "notes": ""}
    result["pending_human"] = []
    result["change_log"] = [{
        "t": now_iso(), "step": "scaffold", "action": "init_partial_skeleton",
        "status": "partial", "reason": "骨架先落盘，后续增量改进",
    }]
    result["self_check"] = {"run": False, "gaps": [], "checked_at": None}
    result["runtime"] = {"started_at": now_iso(), "budget_s": 900, "budget_requests": 64}
    if errors:
        result["errors"] = list(errors)
    return result


def write_atomic(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description="落盘 result.json 骨架")
    ap.add_argument("--input", default=None, help="case.json 路径")
    ap.add_argument("--output", default=None, help="result.json 输出路径")
    ap.add_argument("--work", default=None, help="工作目录（写 case_summary.json）")
    args = ap.parse_args(argv)

    input_dir = os.environ.get("INPUT_DIR", "/input")
    output_dir = os.environ.get("OUTPUT_DIR", "/app/output")
    work_dir = os.environ.get("WORK_DIR", "/app/work")

    case_path = args.input or os.path.join(input_dir, "case.json")
    task_path = os.path.join(input_dir, "task.md")
    result_path = args.output or os.path.join(output_dir, "result.json")

    errors = []
    case, err = load_json(case_path)
    if err:
        errors.append("case.json: " + err)
    task_text, terr = read_text(task_path)
    if terr:
        errors.append("task.md: " + terr)

    case_id = "unknown"
    if isinstance(case, dict):
        for k in ("case_id", "id", "challenge_id"):
            if case.get(k):
                case_id = str(case[k])
                break
    elif isinstance(case, str):
        case_id = case

    contract_fields = extract_contract(case) if isinstance(case, dict) else list(DEFAULT_CONTRACT)
    result = build_result(case_id, contract_fields, errors)

    write_atomic(result_path, result)

    summary = {
        "case_id": case_id,
        "contract_fields": contract_fields,
        "errors": errors,
        "task_has_content": bool(task_text) if task_text is not None else False,
        "generated_at": now_iso(),
    }
    os.makedirs(work_dir, exist_ok=True)
    summary_path = os.path.join(work_dir, "case_summary.json")
    write_atomic(summary_path, summary)

    print(json.dumps({
        "result_path": result_path,
        "summary_path": summary_path,
        "case_id": case_id,
        "contract_fields": contract_fields,
        "errors": errors,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
