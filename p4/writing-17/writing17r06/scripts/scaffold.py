#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scaffold.py — 先落盘一份合法 partial 结果骨架（早落盘原则）。

仅使用 Python 标准库。任何可恢复的输入问题都不抛异常、不崩溃：
- 空输入目录 / 缺 case.json  -> status=insufficient_evidence
- 坏 JSON / 坏编码          -> 记录到 errors/degradations 后继续
- 未知字段                 -> 原样保留，不改名、不删除

退出码恒为 0；真实状态由 result.json 的 status 字段承载。

用法:
    python3 -B scripts/scaffold.py --input /input --output /app/output --work /app/work
"""
import argparse
import json
import os
import sys
import time
import hashlib

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
TEXT_MAX_BYTES = 2_000_000  # 单文件文本读取上限，超限跳过并记录


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def read_text(path, max_bytes=TEXT_MAX_BYTES):
    """utf-8-sig 容错读取；坏编码降级为 latin-1，失败返回 (None, error)。"""
    if not os.path.isfile(path):
        return None, "missing file"
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, "stat failed: %s" % e
    if size > max_bytes:
        return None, "skipped (size %d > %d bytes)" % (size, max_bytes)
    raw = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
            return raw.decode(enc), None
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError as e:
            return None, "read failed: %s" % e
    return None, "undecodable text"


def read_json(path, max_bytes=TEXT_MAX_BYTES):
    """读取并解析 JSON；返回 (data, error)。BOM / 坏 JSON 均不崩溃。"""
    text, err = read_text(path, max_bytes)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except (ValueError, TypeError) as e:
        return None, "bad json: %s" % e


def discover_case_id(case):
    """从 case.json 各可能键名发现 case_id。"""
    if not isinstance(case, dict):
        return None
    for key in ("case_id", "caseId", "id", "task_id", "taskId", "case"):
        if key in case and isinstance(case[key], (str, int)):
            return str(case[key])
    return None


def discover_output_contract(case):
    """发现输出契约。返回 (contract_object_or_None, error)。

    契约可能是 dict（字段->说明）或 list（字段名列表）。
    未知键名不猜测：只在候选键里找。
    """
    if not isinstance(case, dict):
        return None, "case.json is not an object"
    for key in ("output_contract", "outputContract", "output_schema",
                "outputSchema", "result_schema", "resultSchema",
                "contract", "outputs", "output_fields", "outputFields"):
        if key in case:
            val = case[key]
            if isinstance(val, (dict, list)):
                return val, None
            return None, "output contract under '%s' is not dict/list" % key
    return None, "no output_contract key found in case.json"


def contract_keys(contract):
    """把契约规约为字段名列表（供回填与校验使用）。"""
    if isinstance(contract, dict):
        return list(contract.keys())
    if isinstance(contract, list):
        return [str(k) for k in contract]
    return []


def scan_inputs(input_dir, skip_dirs=None):
    """递归扫描输入目录，列出可处理的材料文件与大小。

    skip_dirs: 绝对路径集合，跳过（避免 output/work 落在 input 内时自污染）。
    """
    found = []
    errors = []
    skip_dirs = {os.path.abspath(d) for d in (skip_dirs or [])}
    if not os.path.isdir(input_dir):
        return found, ["input dir missing: %s" % input_dir]
    for root, dirs, files in os.walk(input_dir):
        dirs[:] = [d for d in dirs
                   if os.path.abspath(os.path.join(root, d)) not in skip_dirs]
        for name in sorted(files):
            p = os.path.join(root, name)
            rel = os.path.relpath(p, input_dir).replace("\\", "/")
            try:
                size = os.path.getsize(p)
            except OSError as e:
                errors.append("stat %s: %s" % (rel, e))
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in (".pdf", ".md", ".txt", ".json", ".csv", ".tex",
                       ".docx", ".pptx", ".xlsx", ".html", ".xml",
                       ".png", ".jpg", ".jpeg"):
                found.append({"path": rel, "bytes": size,
                              "abs": os.path.abspath(p),
                              "readable_text": ext in
                              (".md", ".txt", ".json", ".csv", ".tex",
                               ".html", ".xml") and size <= TEXT_MAX_BYTES})
            else:
                errors.append("ignored unsupported type: %s" % rel)
    return found, errors


def build_skeleton(case_id, contract, found, errors, degradations, input_dir):
    result = {}
    result["case_id"] = case_id or ""
    # 原样回填输出契约全部键，缺值给 null，不改名、不删未知键
    for k in contract_keys(contract):
        result[k] = None
    result["status"] = "partial" if (case_id or found) else "insufficient_evidence"
    result["artifacts"] = {}
    # 材料来源：排除契约/任务文件本身
    result["sources"] = [f["path"] for f in found
                         if not f["path"].startswith(("case.", "task."))]
    result["uncertainty"] = []
    result["change_log"] = [
        {"t": now_iso(), "action": "scaffold",
         "detail": "early partial skeleton written before analysis"}
    ]
    result["limitations"] = []
    result["pending_human"] = []
    result["errors"] = list(errors)
    result["degradations"] = list(degradations)
    result["runtime"] = {"started": now_iso(), "input_dir": input_dir,
                         "scaffold": True}
    return result


def main():
    ap = argparse.ArgumentParser(description="Write early partial result skeleton.")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args()

    errors, degradations = [], []

    case_path = os.path.join(args.input, "case.json")
    task_path = os.path.join(args.input, "task.md")

    case, err = read_json(case_path)
    if err:
        errors.append("case.json: %s" % err)
    task_text, terr = read_text(task_path)
    if terr:
        degradations.append("task.md: %s" % terr)

    case_id = discover_case_id(case)
    contract, cerr = discover_output_contract(case)
    if cerr:
        degradations.append(cerr)

    # 先扫描输入，再创建 output/work，避免二者落在 input 内时自污染
    found, scan_errs = scan_inputs(
        args.input, skip_dirs=[args.output, args.work])
    errors.extend(scan_errs)

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    result = build_skeleton(case_id, contract, found, errors,
                            degradations, args.input)

    # discovery：供后续 build/validate 与 Agent 使用
    discovery = {
        "case_id": case_id,
        "contract": contract,
        "contract_keys": contract_keys(contract),
        "inputs": found,
        "task_text": task_text if not terr else None,
        "task_has_text": not terr,
    }
    with open(os.path.join(args.work, "discovery.json"), "w",
              encoding="utf-8") as fh:
        json.dump(discovery, fh, ensure_ascii=False, indent=2)

    # map_spec 骨架模板：Agent 按此填充语义内容
    spec_skeleton = {
        "title": case_id or "",
        "meta": {"purpose": "", "audience": "", "scope": ""},
        "root": {"id": "n1", "text": "", "kind": "root",
                 "evidence": None, "children": []},
        "relations": [],
        "limitations": [],
        "pending_human": [],
    }
    with open(os.path.join(args.work, "map_spec.skeleton.json"), "w",
              encoding="utf-8") as fh:
        json.dump(spec_skeleton, fh, ensure_ascii=False, indent=2)

    out_path = os.path.join(args.output, "result.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    print(json.dumps({
        "case_id": case_id,
        "status": result["status"],
        "contract_keys": result["status"] and contract_keys(contract),
        "input_files": len(found),
        "errors": len(errors),
        "degradations": len(degradations),
        "result_json": out_path,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
