#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scaffold.py — 先落盘一份合法的 partial 结果骨架（"早落盘"原则）。

仅使用 Python 标准库。任何可恢复的输入问题都不抛异常、不崩溃：
- 空输入目录 / 缺 case.json  -> status=insufficient_evidence
- 坏 JSON / 坏编码          -> 记入 errors/degradations 后继续
- 未知契约字段             -> 原样保留，缺值填 null，不改名、不删除

退出码恒为 0；真实状态由 result.json 的 status 字段承载。

用法:
    python3 -B scripts/scaffold.py --input /input --output /app/output --work /app/work
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
TEXT_MAX_BYTES = 2_000_000  # 单文件文本读取上限，超限跳过并记录
TEXT_SUFFIXES = {".md", ".txt", ".json", ".csv", ".tsv", ".tex", ".html", ".xml",
                 ".yaml", ".yml", ".log"}
BINARY_SUFFIXES = {".pdf", ".docx", ".pptx", ".xlsx",
                   ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def read_bytes(path: str, max_bytes: int = TEXT_MAX_BYTES):
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, "stat failed: %s" % exc.__class__.__name__
    if size > max_bytes:
        return None, "skipped (size %d > %d bytes)" % (size, max_bytes)
    try:
        with open(path, "rb") as fh:
            return fh.read(), None
    except OSError as exc:
        return None, "read failed: %s" % exc.__class__.__name__


def read_text(path: str, max_bytes: int = TEXT_MAX_BYTES):
    raw, err = read_bytes(path, max_bytes)
    if err:
        return None, err
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), None
        except (UnicodeDecodeError, UnicodeError):
            continue
    return None, "undecodable text"


def read_json(path: str, max_bytes: int = TEXT_MAX_BYTES):
    text, err = read_text(path, max_bytes)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except (ValueError, TypeError) as exc:
        return None, "bad json: %s" % exc.__class__.__name__


def discover_case_id(case) -> str:
    if not isinstance(case, dict):
        return ""
    for key in ("case_id", "caseId", "id", "task_id", "taskId", "case"):
        value = case.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            return str(value)
    return ""


def discover_output_contract(case):
    """返回 (contract, error)。contract 可为 dict 或 list 或 None。"""
    if not isinstance(case, dict):
        return None, "case.json is not an object"
    for key in ("output_contract", "outputContract", "output_schema", "outputSchema",
                "result_schema", "resultSchema", "contract", "outputs",
                "output_fields", "outputFields"):
        if key in case:
            value = case[key]
            if isinstance(value, (dict, list)):
                return value, None
            return None, "output contract under '%s' is not dict/list" % key
    return None, "no output_contract key found in case.json"


def contract_keys(contract) -> list:
    """把 dict / list 契约规约为字段名列表（用于回填与校验）。"""
    keys = []
    if isinstance(contract, dict):
        keys = list(contract.keys())
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                for cand in ("name", "key", "field", "field_id"):
                    value = item.get(cand)
                    if isinstance(value, str) and value.strip():
                        keys.append(value.strip())
                        break
    ordered, seen = [], set()
    for key in keys:
        if isinstance(key, str) and key and key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


def scan_inputs(input_dir: str, skip_dirs=None):
    found, notes = [], []
    skip = {os.path.abspath(d) for d in (skip_dirs or []) if d}
    if not os.path.isdir(input_dir):
        return found, ["input dir missing: %s" % input_dir]
    for root, dirs, files in os.walk(input_dir):
        dirs[:] = [d for d in dirs
                   if os.path.abspath(os.path.join(root, d)) not in skip]
        for name in sorted(files):
            path = os.path.join(root, name)
            rel = os.path.relpath(path, input_dir).replace("\\", "/")
            try:
                size = os.path.getsize(path)
            except OSError as exc:
                notes.append("stat %s: %s" % (rel, exc.__class__.__name__))
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in TEXT_SUFFIXES:
                found.append({"path": rel, "bytes": size, "abs": os.path.abspath(path),
                              "kind": "text", "readable": size <= TEXT_MAX_BYTES})
            elif ext in BINARY_SUFFIXES:
                found.append({"path": rel, "bytes": size, "abs": os.path.abspath(path),
                              "kind": ext.lstrip("."), "readable": False})
            else:
                notes.append("ignored unsupported type: %s" % rel)
    return found, notes


def build_skeleton(case_id, contract, found, errors, degradations, input_dir) -> dict:
    result = {"case_id": case_id or ""}
    for key in contract_keys(contract):
        result[key] = None
    result["status"] = "partial" if (case_id or found) else "insufficient_evidence"
    result["artifacts"] = {}
    result["sources"] = [
        {"path": f["path"], "kind": f["kind"], "bytes": f["bytes"]} for f in found]
    result["uncertainty"] = []
    result["change_log"] = [
        {"t": now_iso(), "action": "scaffold",
         "detail": "early partial skeleton written before analysis"}]
    result["limitations"] = []
    result["pending_human"] = []
    result["errors"] = list(errors)
    result["degradations"] = list(degradations)
    result["runtime"] = {"started": now_iso(), "input_dir": input_dir, "scaffold": True}
    return result


def write_json(path: str, obj) -> bool:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        return True
    except OSError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Write early partial result skeleton.")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--work", default="/app/work")
    args = parser.parse_args()

    errors, degradations = [], []

    case_path = os.path.join(args.input, "case.json")
    task_path = os.path.join(args.input, "task.md")

    case, case_err = read_json(case_path)
    if case_err:
        errors.append("case.json: %s" % case_err)
    task_text, task_err = read_text(task_path)
    if task_err:
        degradations.append("task.md: %s" % task_err)

    case_id = discover_case_id(case)
    contract, contract_err = discover_output_contract(case)
    if contract_err:
        degradations.append(contract_err)

    # 先扫描输入、再创建 output/work，避免二者落在 input 内时自污染
    found, scan_notes = scan_inputs(args.input, skip_dirs=[args.output, args.work])
    errors.extend(scan_notes)

    result = build_skeleton(case_id, contract, found, errors, degradations, args.input)

    discovery = {
        "case_id": case_id,
        "contract": contract,
        "contract_keys": contract_keys(contract),
        "inputs": found,
        "task_text": task_text if not task_err else None,
        "task_has_text": not task_err,
        "scanned_at": now_iso(),
    }
    spec_skeleton = {
        "title": case_id or "",
        "case_id": case_id or "",
        "meta": {"purpose": "", "audience": "", "scope": ""},
        "root": {"id": "n1", "text": "", "kind": "root",
                 "evidence": None, "children": []},
        "relations": [],
        "limitations": [],
        "pending_human": [],
    }

    ok = write_json(os.path.join(args.work, "discovery.json"), discovery)
    ok = write_json(os.path.join(args.work, "map_spec.skeleton.json"), spec_skeleton) and ok
    ok = write_json(os.path.join(args.output, "result.json"), result) and ok

    print(json.dumps({
        "case_id": case_id,
        "status": result["status"],
        "contract_keys": contract_keys(contract),
        "input_files": len(found),
        "errors": len(errors),
        "degradations": len(degradations),
        "result_json": os.path.join(args.output, "result.json"),
        "wrote_all": ok,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
