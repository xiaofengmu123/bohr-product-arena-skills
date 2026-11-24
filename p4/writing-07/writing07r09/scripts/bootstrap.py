#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bootstrap.py — 扫描输入、抽取合同字段、落盘 result.json 骨架。

纯标准库。发现 / 骨架二合一：一次调用即保证 /app/output/result.json
存在且为合法 JSON，满足“前 3 分钟内先落盘 partial 结果骨架”的硬要求。

用法：
    python3 -B scripts/bootstrap.py \
        [--input /input] [--output /app/output] [--work /app/work]

缺省值即运行时路径。空输入目录也会写出合法结果（insufficient_evidence）。
"""

import argparse
import json
import os
import sys
import time

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB，超限跳过并记 degradations

PRIORITY_KEYS = ["output_contract", "output", "expected_output",
                 "fields", "required_fields"]

TEXT_EXTENSIONS = {".json", ".md", ".bib", ".tex", ".txt", ".yml",
                   ".yaml", ".csv", ".tsv", ".rst"}

STANDARD_FIELDS = {
    "sources": [],
    "uncertainty": [],
    "change_log": [],
    "artifacts": [],
    "pending_human": [],
    "errors": [],
    "degradations": [],
    "runtime": {},
}


def read_text(path, errors_log):
    """按 utf-8-sig 读取，失败回退 latin-1 并记录降级。"""
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    # 理论不可达：latin-1 能解码任意字节，但保留兜底
    if errors_log is not None:
        errors_log.append("unreadable_encoding:%s" % os.path.basename(path))
    return "", "unreadable_encoding"


def discover_files(input_dir, errors_log):
    """递归列出输入文件，返回 (files, degradations)。

    files: [{name(相对路径), path(绝对), size, kind, text(或None)}]
    """
    files = []
    degradations = []
    if not os.path.isdir(input_dir):
        degradations.append({"item": "input_dir_missing",
                             "reason": "输入目录不存在: %s" % input_dir})
        return files, degradations

    for root, dirs, names in os.walk(input_dir):
        # 跳过隐藏在输入目录下的工作/产物目录，避免自我扫描
        dirs[:] = [d for d in dirs if d not in ("work", "output", "__pycache__")]
        for name in sorted(names):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir).replace(os.sep, "/")
            try:
                size = os.path.getsize(full)
            except OSError as exc:
                degradations.append({"item": rel, "reason": "stat失败: %s" % exc})
                continue
            if size > MAX_FILE_BYTES:
                degradations.append({"item": rel,
                                     "reason": "超过2MB上限，跳过"})
                continue
            ext = os.path.splitext(name)[1].lower()
            kind = "pdf" if ext == ".pdf" else (
                "text" if ext in TEXT_EXTENSIONS else "other")
            text = None
            if kind == "text":
                text, enc_err = read_text(os.path.join(root, name), None)
                if enc_err:
                    degradations.append({"item": rel,
                                         "reason": enc_err})
            files.append({"name": rel, "path": full, "size": size,
                          "kind": kind, "text": text})
    return files, degradations


def find_case(files):
    """优先取根目录/一层的 case.json；找不到返回 None。"""
    for f in files:
        if f["name"].lower().endswith("case.json") or \
           f["name"].lower() == "case.json":
            return f
    for f in files:
        if f["name"].lower().split("/")[-1] == "case.json":
            return f
    return None


def parse_case_json(case_file, errors_log):
    """解析 case.json，返回 (data, error)。坏 JSON 不崩溃。"""
    if case_file is None:
        return None, "case.json 不存在"
    try:
        return json.loads(case_file["text"] or "null"), None
    except json.JSONDecodeError as exc:
        errors_log.append("case_json_parse_error: %s" % exc)
        return None, "case.json 解析失败: %s" % exc


def find_contract(data, depth=0):
    """按优先级顺序在 JSON 中寻找输出合同（最多下钻两层）。"""
    if isinstance(data, dict):
        for key in PRIORITY_KEYS:
            if key in data:
                return data[key]
        if depth < 2:
            for value in data.values():
                if isinstance(value, (dict, list)):
                    found = find_contract(value, depth + 1)
                    if found is not None:
                        return found
    return None


def expand_contract(contract):
    """把合同结构转成待回填骨架。

    对象递归、列表回填 []、null 保持 null；标量（字符串/数字/布尔）
    原样保留为默认值或约束（如 "author-year"），由 Agent 复核后定稿。
    """
    if contract is None:
        return None
    if isinstance(contract, dict):
        return {k: expand_contract(v) for k, v in contract.items()}
    if isinstance(contract, list):
        return []
    return contract  # 标量默认值/约束，原样保留


def expand_contract_top(contract):
    """把合同数据展开为 result.json 顶层键字典。"""
    result = {}
    if isinstance(contract, dict):
        for key, value in contract.items():
            result[key] = expand_contract(value)
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                result[item] = None
            elif isinstance(item, dict):
                for key, value in item.items():
                    result[key] = expand_contract(value)
            # 其他类型无法命名，跳过（记入 caller 的 warnings）
    return result


def build_result(case_id, contract_data, status):
    """组装 result.json 骨架：合同字段优先，标准字段让位。"""
    result = {"case_id": case_id, "status": status}
    contract_fields = expand_contract_top(contract_data)
    for key, value in contract_fields.items():
        result[key] = value  # 合同字段与 case_id/status 同名时合同胜出
    for key, value in STANDARD_FIELDS.items():
        if key not in result:
            result[key] = value
    return result


def write_json(path, obj):
    """确定性写 JSON：UTF-8、无 BOM、稳定缩进、末尾换行。"""
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description="扫描输入并落盘结果骨架")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--work", default=DEFAULT_WORK)
    args = parser.parse_args(argv)

    errors_log = []
    degradations = []
    started = time.time()

    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)

    files, file_degradations = discover_files(args.input, errors_log)
    degradations.extend(file_degradations)

    case_file = find_case(files)
    case_data, case_error = parse_case_json(case_file, errors_log)

    # case_id：优先顶层 case_id，其次 id / challenge_id
    case_id = None
    if isinstance(case_data, dict):
        for key in ("case_id", "id", "challenge_id"):
            if isinstance(case_data.get(key), str):
                case_id = case_data[key]
                break

    contract = find_contract(case_data) if case_data else None

    # 是否还有可用的科研材料（排除 case.json / task.md）
    materials = [f for f in files
                 if f["name"].lower() != "case.json"
                 and not f["name"].lower().endswith("task.md")
                 and f["kind"] in ("text", "pdf")]

    if case_data is None and not materials:
        status = "insufficient_evidence"
    else:
        status = "partial"

    if case_error and case_id is None:
        errors_log.append(case_error)

    result = build_result(case_id, contract, status)
    # 把扫描阶段已知的降级/错误并入骨架（供校验与人工复核）
    if degradations:
        result["degradations"] = degradations
    if errors_log:
        result["errors"] = list(dict.fromkeys(errors_log))

    manifest = {
        "files": [{k: v for k, v in f.items() if k != "text"} for f in files],
        "case_id": case_id,
        "contract": contract,
        "contract_fields": list(expand_contract_top(contract).keys())
        if contract else [],
        "status": status,
        "degradations": degradations,
        "errors": errors_log,
    }

    write_json(os.path.join(args.work, "input_manifest.json"), manifest)
    write_json(os.path.join(args.work, "case_normalized.json"), {
        "case_id": case_id,
        "contract": contract,
        "contract_fields": manifest["contract_fields"],
    })
    write_json(os.path.join(args.output, "result.json"), result)

    print(json.dumps({"case_id": case_id, "status": status,
                      "files": len(files),
                      "contract_fields": manifest["contract_fields"],
                      "degradations": len(degradations),
                      "errors": len(errors_log),
                      "elapsed_s": round(time.time() - started, 2)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
