#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bootstrap: 输入发现 -> 材料清单 -> partial 结果骨架。

Step 0 的确定性实现，只用 Python 标准库。目标：
1. 不假设输入文件名/键名：扫描输入目录，识别 case.json / task.md / 材料文件；
2. 对坏编码、坏 JSON、缺输入、空目录都容错，不崩溃；
3. 在数秒内把一版合法 partial 结果落盘到 /app/output/result.json；
4. 把 /input/case.json 的 output_contract 原样回填为 result.json 顶层字段。

CLI: python3 scripts/bootstrap.py --input-dir /input --output-dir /app/output --work-dir /app/work
可作为模块导入：from bootstrap import run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文件 2MB 上限，超限跳过并记录
MAX_SCAN_DEPTH = 3

RESERVED_KEYS = {
    "case_id", "status", "sources", "uncertainty", "pending_human",
    "change_log", "errors", "degradations", "runtime",
}

DEFAULT_CONTRACT = {
    "deliverable": "manuscript.md",
    "fact_source_map": "fact_source_map.json",
    "version_limits": "version_limits.json",
}

EXCLUDE_BASENAMES = {"case.json", "task.md", "result.json", "fact_source_map.json", "version_limits.json"}

_EXT_KIND = {
    ".pdf": "pdf",
    ".txt": "text",
    ".md": "text",
    ".markdown": "text",
    ".csv": "csv",
    ".tsv": "csv",
    ".xlsx": "spreadsheet",
    ".xls": "spreadsheet",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".tif": "image",
    ".tiff": "image",
    ".gif": "image",
    ".bmp": "image",
    ".docx": "doc",
    ".doc": "doc",
    ".pptx": "slides",
    ".ppt": "slides",
    ".json": "data",
    ".xml": "data",
    ".py": "code",
    ".sh": "code",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text_safe(path: str):
    """按 utf-8-sig -> utf-8 -> latin-1 依次尝试，返回 (text, error)。"""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), None
        except UnicodeDecodeError:
            continue
        except OSError as exc:  # 权限/不存在等
            return None, str(exc)
    return None, "无法以候选编码读取文件"


def read_json_safe(path: str):
    text, err = read_text_safe(path)
    if err is not None:
        return None, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, "JSON 解析失败: %s" % exc


def classify_material(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    return _EXT_KIND.get(ext, "unknown")


def default_contract_value(name: str):
    n = name.lower()
    if any(s in n for s in ("deliverable", "manuscript", "draft", "final", "paper", "chapter")):
        return "manuscript.md"
    if any(s in n for s in ("fact_source", "evidence", "source_map", "mapping", "fact")):
        return "fact_source_map.json"
    if any(s in n for s in ("version", "limit", "change", "diff", "boundary")):
        return "version_limits.json"
    return None


def parse_output_contract(case: dict) -> dict:
    """从 case.json 提取 output_contract 并映射为 result.json 顶层字段。"""
    oc = case.get("output_contract") if isinstance(case, dict) else None
    contract = {}
    if isinstance(oc, dict):
        for k, v in oc.items():
            key = str(k)
            value = v if v not in (None, "") else default_contract_value(key)
            contract[key] = value
    elif isinstance(oc, list):
        for item in oc:
            key = str(item)
            contract[key] = default_contract_value(key)
    else:
        contract.update(DEFAULT_CONTRACT)
    if not contract:
        contract.update(DEFAULT_CONTRACT)
    # 保留已知字段的路径占位，若契约未显式给出
    for key, default in DEFAULT_CONTRACT.items():
        if key in contract and contract[key] in (None, ""):
            contract[key] = default
    return contract


def discover_materials(input_dir: str):
    """扫描输入目录（非递归到 MAX_SCAN_DEPTH），返回材料清单与错误列表。"""
    materials = []
    errors = []
    seen = set()
    if not os.path.isdir(input_dir):
        return materials, ["输入目录不存在: %s" % input_dir]

    base = os.path.abspath(input_dir)
    for root, dirs, files in os.walk(base):
        depth = root[len(base):].count(os.sep)
        if depth >= MAX_SCAN_DEPTH:
            dirs[:] = []
            continue
        dirs.sort()
        for name in sorted(files):
            if name.lower() in EXCLUDE_BASENAMES:
                continue
            full = os.path.join(root, name)
            try:
                size = os.path.getsize(full)
            except OSError as exc:
                errors.append("无法读取大小 %s: %s" % (full, exc))
                continue
            rel = os.path.relpath(full, base).replace("\\", "/")
            key = rel.lower()
            if key in seen:
                continue
            seen.add(key)
            if size > MAX_FILE_BYTES:
                errors.append("材料超 2MB 已跳过: %s (%d bytes)" % (rel, size))
                continue
            materials.append({
                "path": rel,
                "abs_path": full,
                "kind": classify_material(name),
                "size_bytes": size,
                "status": "listed",
            })
    # 稳定分配材料 ID（M01, M02, …），供稿件 [S:ID] 与证据目录引用
    for i, m in enumerate(materials, 1):
        m["id"] = "M%02d" % i
    return materials, errors


def find_input_files(input_dir: str):
    """定位 case.json 与 task.md，返回 (case_path, task_path, errors)。"""
    case_path = os.path.join(input_dir, "case.json")
    task_path = os.path.join(input_dir, "task.md")
    if not os.path.isfile(case_path):
        # 容忍大小写/嵌套一层
        alt = os.path.join(input_dir, "Case.json")
        if os.path.isfile(alt):
            case_path = alt
    errors = []
    if not os.path.isfile(case_path):
        errors.append("未找到 /input/case.json")
    if not os.path.isfile(task_path):
        errors.append("未找到 /input/task.md（不阻断，继续处理可用材料）")
    return case_path, task_path, errors


def extract_case_id(case: dict, case_path: str):
    for key in ("id", "case_id", "challenge_id", "task_id"):
        val = case.get(key)
        if val:
            return str(val)
    base = os.path.basename(case_path)
    if base.lower().endswith(".json"):
        return os.path.splitext(base)[0]
    return "unknown"


def write_json(path: str, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def write_placeholder(path: str, kind: str, case_id: str):
    """写最小合法的可编辑占位产物（明确标记为骨架，非预制答案）。"""
    if kind.endswith(".json"):
        write_json(path, {
            "case_id": case_id,
            "status": "pending",
            "note": "bootstrap 生成的骨架占位，待证据抽取与撰写步骤填充",
            "items": [],
        })
    elif kind.endswith(".md"):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# manuscript.md\n\n> bootstrap 生成的骨架占位文件，"
                     "正式章节在证据抽取与撰写步骤中增量填充，勿视为成稿。\n")


def build_skeleton(case_id: str, contract: dict, errors: list, started: str) -> dict:
    result = {
        "case_id": case_id,
        "status": "in_progress",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [
            {"ts": started, "step": "bootstrap", "note": "先落盘 partial 骨架；契约字段已回填"}
        ],
        "errors": [],
        "degradations": [],
        "runtime": {
            "started_at": started,
            "time_budget_s": 900,
            "request_budget": 64,
            "token_budget_per_response": 16384,
        },
    }
    for key, value in contract.items():
        if key in RESERVED_KEYS:
            result["degradations"].append("契约字段 %r 与保留键冲突，已丢弃" % key)
            continue
        result[key] = value
    for e in errors:
        result["errors"].append(e)
    return result


def run(input_dir: str = DEFAULT_INPUT, output_dir: str = DEFAULT_OUTPUT,
        work_dir: str = DEFAULT_WORK):
    """执行 bootstrap，返回 (summary, exit_code)。"""
    started = now_iso()
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    case_path, task_path, find_errors = find_input_files(input_dir)
    case, case_err = (None, None)
    if os.path.isfile(case_path):
        case, case_err = read_json_safe(case_path)

    errors = list(find_errors)
    if case_err is not None:
        errors.append("case.json 读取失败: %s" % case_err)

    if os.path.isfile(case_path) and case is not None and isinstance(case, dict):
        case_id = extract_case_id(case, case_path)
        contract = parse_output_contract(case)
    else:
        case_id = "unknown"
        contract = dict(DEFAULT_CONTRACT)

    materials, mat_errors = discover_materials(input_dir)
    errors.extend(mat_errors)

    # 判断状态
    if not os.path.isdir(input_dir):
        status = "input_error"
    elif not os.path.isfile(case_path):
        status = "input_error"
    elif case is None and case_err is not None:
        status = "input_error"
    elif not materials:
        status = "insufficient_evidence"
    else:
        status = "in_progress"

    result = build_skeleton(case_id, contract, errors, started)
    result["status"] = status
    if status == "insufficient_evidence":
        result["uncertainty"].append("未发现任何可解析材料文件")
    if status == "input_error":
        result["uncertainty"].append("输入缺失或不可解析，已落盘最小合法结果，不编造数据")

    # 材料清单 -> /app/work/material_manifest.json
    manifest = {
        "schema": "material-manifest/v1",
        "case_id": case_id,
        "input_dir": os.path.abspath(input_dir),
        "case_path": case_path if os.path.isfile(case_path) else None,
        "task_path": task_path if os.path.isfile(task_path) else None,
        "materials": materials,
        "errors": errors,
    }
    write_json(os.path.join(work_dir, "material_manifest.json"), manifest)

    # 证据目录（中间表示 IR）-> /app/work/evidence_catalog.json
    catalog = {
        "schema": "evidence-catalog/v1",
        "case_id": case_id,
        "updated_at": started,
        "materials": materials,
        "claims": [],
        "citations": [],
        "conflicts": [],
        "gaps": [],
    }
    write_json(os.path.join(work_dir, "evidence_catalog.json"), catalog)

    # 契约占位产物（先落盘一版可打开的最小文件）
    for key, value in contract.items():
        if isinstance(value, str) and (value.endswith(".json") or value.endswith(".md")):
            target = os.path.join(output_dir, value)
            if not os.path.exists(target):
                try:
                    write_placeholder(target, value, case_id)
                except OSError as exc:
                    result["degradations"].append("占位产物写入失败 %s: %s" % (value, exc))

    result_path = os.path.join(output_dir, "result.json")
    write_json(result_path, result)

    summary = {
        "case_id": case_id,
        "status": status,
        "contract_fields": sorted(contract.keys()),
        "material_count": len(materials),
        "result_path": result_path,
        "manifest_path": os.path.join(work_dir, "material_manifest.json"),
        "catalog_path": os.path.join(work_dir, "evidence_catalog.json"),
        "errors": errors,
    }
    return summary, 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Step 0：输入发现 + partial 结果骨架")
    ap.add_argument("--input-dir", default=DEFAULT_INPUT)
    ap.add_argument("--output-dir", default=DEFAULT_OUTPUT)
    ap.add_argument("--work-dir", default=DEFAULT_WORK)
    args = ap.parse_args(argv)
    summary, code = run(args.input_dir, args.output_dir, args.work_dir)
    print(json.dumps(summary, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
