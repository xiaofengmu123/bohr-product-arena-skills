#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""discover_input.py — 稳健发现输入，并先落盘一版 result.json 结果骨架。

用途：在正式分析前，扫描 /input 目录，读取 case.json 与 task.md，抽取
case_id 与题目 output_contract 的全部字段，立即写出合法的最小结果骨架
（status=partial），保证任何后续异常都不会留下"空输出"。

只依赖 Python 标准库。退出码恒为 0；失败信息写入 errors/degradations，
不抛异常、不崩溃。

用法:
  python3 scripts/discover_input.py [--input /input] [--output /app/output] [--work /app/work]
"""

import argparse
import json
import os
import sys
import time
import traceback

MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文本文件 2MB 上限

CONTRACT_KEYS = (
    "output_contract", "outputContract",
    "output_schema", "outputSchema",
    "contract", "outputs", "output",
)

# 未在 case.json 中显式给出 output_contract 时的保守默认字段。
# 依据题目文档"Skill 运行后的输出要求"三组字段推导，仅作骨架占位。
DEFAULT_CONTRACT_FIELDS = {
    "target_artifact": None,
    "fact_source_mapping": None,
    "version_boundary": None,
}


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def read_text(path):
    """按 utf-8-sig / utf-8 / latin-1 顺序容错读取文本，永不抛异常。"""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), enc, None
        except Exception as exc:  # noqa: BLE001
            last = exc
    return None, None, str(last)


def load_json(path):
    txt, enc, err = read_text(path)
    if txt is None:
        return None, enc, ["read_failed: %s" % err]
    for candidate in (txt, txt.lstrip("\ufeff")):
        try:
            return json.loads(candidate), enc, []
        except Exception as exc:  # noqa: BLE001
            last = exc
    return None, enc, ["json_parse_failed: %s" % last]


def discover_files(input_dir):
    """递归发现输入目录下的文本/材料文件，记录大小与跳过项。"""
    found, skipped = [], []
    if not os.path.isdir(input_dir):
        return found, skipped, ["input_dir_missing: %s" % input_dir]
    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            full = os.path.join(root, name)
            try:
                size = os.path.getsize(full)
            except OSError as exc:
                skipped.append({"path": full, "reason": str(exc)})
                continue
            if size > MAX_FILE_BYTES:
                skipped.append({"path": full, "reason": "exceeds_%dB" % MAX_FILE_BYTES})
                continue
            found.append({
                "path": full,
                "rel": os.path.relpath(full, input_dir),
                "size": size,
                "name": name,
            })
    return found, skipped, []


def find_contract(obj, depth=0):
    """在 case.json 对象内浅层查找 output_contract 定义。"""
    if depth > 3 or not isinstance(obj, dict):
        return None
    for key in CONTRACT_KEYS:
        if key in obj and isinstance(obj[key], (dict, list)):
            return obj[key]
    for key in ("task", "case", "submission", "requirements"):
        if key in obj and isinstance(obj[key], dict):
            hit = find_contract(obj[key], depth + 1)
            if hit is not None:
                return hit
    return None


def default_for_value(value):
    """据给定值/描述推断空占位：列表->[]，对象->{}，其余->null。"""
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        if "type" in value:  # 可能是 JSON Schema 片段
            t = value.get("type")
            if t == "array":
                return []
            if t == "object":
                return {}
            return None
        return {}
    if isinstance(value, str):
        return None
    return None


def contract_fields(contract):
    """把 output_contract 归一化为 (字段名, 默认值) 的有序列表，保留全部键。"""
    if isinstance(contract, dict):
        if "properties" in contract and isinstance(contract["properties"], dict):
            props = contract["properties"]
            required = set(contract.get("required", []) or [])
            items = []
            for key in sorted(props, key=lambda k: (k not in required, k)):
                items.append((key, default_for_value(props[key])))
            return items
        return [(k, default_for_value(v)) for k, v in contract.items()]
    if isinstance(contract, list):
        return [(str(i), None) for i in range(len(contract))]
    return []


def extract_case_id(case, input_dir, files):
    if isinstance(case, dict):
        for key in ("case_id", "caseId", "id", "challenge_id"):
            if key in case and case[key] not in (None, ""):
                return str(case[key])
    # 兜底：case.json 文件名或输入目录名
    for f in files:
        if f["name"] == "case.json":
            return os.path.splitext(f["name"])[0]
    return os.path.basename(os.path.normpath(input_dir)) or "unknown"


def main():
    ap = argparse.ArgumentParser(description="发现输入并写出结果骨架")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args()

    errors, degradations = [], []
    started = time.time()
    runtime = {"started_at": now_iso(), "seconds": None}

    # 1) 建目录（尽力而为）
    for d in (args.work, args.output):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as exc:
            errors.append("mkdir_failed:%s:%s" % (d, exc))

    # 2) 发现输入文件
    files, skipped, derr = discover_files(args.input)
    errors.extend(derr)

    # 3) 读 case.json 与 task.md
    case, case_enc, case_err = None, None, []
    task_text, task_enc, task_err = None, None, []
    for f in files:
        if f["name"] == "case.json" and case is None:
            case, case_enc, case_err = load_json(f["path"])
            if case_err:
                errors.append("case.json:%s" % ";".join(case_err))
        if f["name"].lower() in ("task.md", "task.txt") and task_text is None:
            task_text, task_enc, task_err = read_text(f["path"])
            if task_err:
                errors.append("task.md:%s" % task_err)

    # 4) 抽取 case_id
    case_id = extract_case_id(case, args.input, files)

    # 5) 抽取 output_contract
    contract = find_contract(case) if isinstance(case, dict) else None
    contract_source = "case.json"
    cfields = contract_fields(contract) if contract is not None else []
    if not cfields:
        cfields = [(k, v) for k, v in DEFAULT_CONTRACT_FIELDS.items()]
        contract_source = "derived_default"
        degradations.append(
            "output_contract_not_found_in_case.json; using derived default fields "
            "(target_artifact/fact_source_mapping/version_boundary)"
        )

    # 6) 据输入发现情况确定初始 status（诚实，先落盘）
    if not os.path.isdir(args.input):
        initial_status = "input_error"
        degradations.append("input_dir_missing:%s" % args.input)
    elif not files:
        initial_status = "insufficient_evidence"
        degradations.append("no_input_files_found")
    elif case is None and task_text is None:
        initial_status = "insufficient_evidence"
        degradations.append("no_parseable_case.json_or_task.md")
    else:
        initial_status = "partial"

    # 7) 写结果骨架（先落盘，再分析）
    result = {"case_id": case_id}
    for key, default in cfields:
        result[key] = default
    result.update({
        "status": initial_status,
        "sources": [],
        "uncertainty": [],
        "change_log": [{
            "at": now_iso(),
            "event": "skeleton_written",
            "detail": "initial honest partial skeleton; contract_source=%s" % contract_source,
        }],
        "errors": errors,
        "degradations": degradations,
        "runtime": runtime,
    })

    result_path = os.path.join(args.output, "result.json")
    try:
        with open(result_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
        result["runtime"]["result_json"] = result_path
    except OSError as exc:
        errors.append("write_result_failed:%s" % exc)

    # 8) 写工作区摘要（供后续步骤引用）
    summary = {
        "case_id": case_id,
        "input_dir": args.input,
        "output_dir": args.output,
        "work_dir": args.work,
        "contract_source": contract_source,
        "contract_fields": [k for k, _ in cfields],
        "files": files,
        "skipped_files": skipped,
        "case_json_encoding": case_enc,
        "task_encoding": task_enc,
        "errors": errors,
        "degradations": degradations,
    }
    try:
        with open(os.path.join(args.work, "input_summary.json"), "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        errors.append("write_summary_failed:%s" % exc)

    # 9) 重新落盘最终骨架（带完整 errors/runtime）
    result["errors"] = errors
    result["runtime"]["seconds"] = round(time.time() - started, 2)
    try:
        with open(result_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        errors.append("rewrite_result_failed:%s" % exc)

    # 恒为 0：任何问题都已在 JSON 中如实记录
    print(json.dumps({
        "case_id": case_id,
        "result_json": result_path,
        "contract_source": contract_source,
        "n_files": len(files),
        "n_errors": len(errors),
        "n_degradations": len(degradations),
    }, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 最后兜底，绝不让进程崩溃
        print(json.dumps({"fatal": traceback.format_exc()}, ensure_ascii=False))
        sys.exit(0)
