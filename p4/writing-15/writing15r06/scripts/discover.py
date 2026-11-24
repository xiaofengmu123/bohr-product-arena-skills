#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""discover.py -- 输入发现、契约提取与结果骨架落盘。

职责：
1. 扫描输入目录，识别 case.json / task.md 及科研材料（md/txt/pdf/docx/...）。
2. 容错读取并解析 case.json，抽取 case_id 与 output_contract 字段。
3. 生成 /app/work/inventory.json（输入清单 + 契约 + 材料索引）。
4. 立即落盘一份合法的部分结果骨架 /app/output/result.json（status=partial），
   保证任何后续异常都不会留下不完整产物。

仅依赖 Python 标准库。缺省路径遵循运行契约：
  --input  /input
  --output /app/output
  --work   /app/work
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文件大小上限 2MB，超限跳过并记录

MATERIAL_EXT = {
    ".md", ".txt", ".tex", ".rst", ".org", ".markdown",
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx",
    ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml",
    ".html", ".htm", ".bib", ".xml", ".ipynb",
}

RESERVED_KEYS = {
    "case_id", "status", "sources", "uncertainty", "change_log",
    "errors", "degradations", "artifacts", "pending_human", "runtime",
    "output_contract", "output_contract_spec",
}


def read_text_tolerant(path):
    """按 utf-8-sig -> utf-8 -> latin-1 -> utf-8(replace) 顺序容错读取文本。"""
    raw = None
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw is None:
        return None, "empty"
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    try:
        return raw.decode("latin-1"), "latin-1"
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def parse_json_tolerant(text, path, errors):
    """解析 JSON，坏 JSON 时记录 error 并降级为 {}。"""
    for candidate in (text, text.lstrip("\ufeff")):
        try:
            return json.loads(candidate)
        except ValueError as exc:
            continue
    errors.append({"file": path, "error": "bad_json", "detail": str(exc)[:200]})
    return {}


def scan_input(input_dir, errors, degradations):
    """递归扫描输入目录，返回 (files, materials)。"""
    files = []
    materials = []
    if not os.path.isdir(input_dir):
        errors.append({"file": input_dir, "error": "input_dir_missing"})
        return files, materials
    for root, _dirs, names in os.walk(input_dir):
        for name in sorted(names):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir)
            try:
                size = os.path.getsize(full)
            except OSError as exc:
                degradations.append({"file": rel, "issue": "stat_failed", "detail": str(exc)[:120]})
                continue
            if size > MAX_FILE_BYTES:
                degradations.append({"file": rel, "issue": "too_large_skipped", "bytes": size})
                continue
            ext = os.path.splitext(name)[1].lower()
            if name in ("case.json", "task.md"):
                kind = "meta"
            elif ext in MATERIAL_EXT:
                kind = "material"
            else:
                kind = "other"
            files.append({"path": full, "rel": rel, "name": name, "ext": ext,
                          "bytes": size, "kind": kind})
            if kind == "material":
                materials.append({"rel": rel, "ext": ext, "bytes": size})
    return files, materials


def extract_case_id(case, task_text):
    """从 case.json 或 task.md 中抽取 case_id。"""
    for key in ("case_id", "caseId", "id", "challenge_id", "task_id", "taskId"):
        val = case.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    m = re.search(r"challenge[-_ ]?id[:：]\s*[`'\"]?([A-Za-z0-9._-]+)", task_text or "", re.I)
    if m:
        return m.group(1)
    m = re.search(r"\b(challenge-[A-Za-z0-9._-]+)", task_text or "", re.I)
    if m:
        return m.group(1)
    return None


def placeholder_for(spec):
    """根据契约字段描述推断占位值：列表类 -> []，其余 -> null。"""
    if isinstance(spec, dict):
        t = str(spec.get("type", "")).lower()
        if t in ("list", "array"):
            return []
        if t == "object":
            return {}
        desc = " ".join(str(v) for v in spec.values()).lower()
    else:
        t = str(spec or "").lower()
        desc = t
    if any(word in desc for word in ("list", "array", "列表", "数组", "记录", "映射")):
        return []
    return None


def get_contract(case):
    """抽取 output_contract，兼容常见键名。返回 (spec, is_explicit)。"""
    for key in ("output_contract", "outputContract", "output_schema"):
        val = case.get(key)
        if isinstance(val, dict):
            return val, True
        if isinstance(val, list):
            return {"fields": val}, True
        if isinstance(val, str) and val.strip():
            return {"fields": val.strip()}, True
    # 未显式给出时，使用运行契约约定的一组最小字段。
    return {"deliverable": "str", "evidence_map": "list"}, False


def build_skeleton(case_id, contract, status="partial"):
    """生成 result.json 骨架：echo 契约字段 + 元数据字段。"""
    result = {"case_id": case_id, "status": status}
    collisions = []
    for field, spec in contract.items():
        if field in RESERVED_KEYS:
            collisions.append(field)
            continue
        result[field] = placeholder_for(spec)
    result["output_contract"] = contract
    result["sources"] = []
    result["uncertainty"] = []
    result["change_log"] = []
    result["errors"] = []
    result["degradations"] = []
    result["artifacts"] = {}
    result["pending_human"] = []
    result["runtime"] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "stage": "skeleton",
        "note": "骨架由 discover.py 生成，后续仅做增量改进",
    }
    if collisions:
        result["errors"].append({"error": "reserved_field_collision", "fields": collisions})
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description="发现输入并落盘结果骨架")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args(argv)

    errors, degradations = [], []
    t0 = time.time()
    for d in (args.output, args.work):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as exc:
            errors.append({"file": d, "error": "mkdir_failed", "detail": str(exc)[:120]})

    files, materials = scan_input(args.input, errors, degradations)

    case = {}
    task_text = ""
    case_path = None
    for f in files:
        if f["name"] == "case.json":
            case_path = f["path"]
            text, enc = read_text_tolerant(f["path"])
            case = parse_json_tolerant(text, f["rel"], errors)
            if enc in ("latin-1", "utf-8-replace"):
                degradations.append({"file": f["rel"], "issue": "non_utf8_decoded", "encoding": enc})
        elif f["name"] == "task.md":
            text, enc = read_text_tolerant(f["path"])
            task_text = text or ""

    if case_path is None:
        errors.append({"error": "case_json_missing", "detail": "未找到 case.json"})

    case_id = extract_case_id(case, task_text)
    contract, explicit = get_contract(case)

    skeleton = build_skeleton(case_id, contract)
    skeleton["runtime"]["discover_seconds"] = round(time.time() - t0, 3)
    skeleton["errors"].extend(errors)
    skeleton["degradations"].extend(degradations)

    inventory = {
        "input_dir": args.input,
        "case_id": case_id,
        "output_contract": contract,
        "output_contract_explicit": explicit,
        "files": files,
        "materials": materials,
        "case_keys": sorted(case.keys()),
        "task_md_chars": len(task_text or ""),
        "errors": errors,
        "degradations": degradations,
    }

    inv_path = os.path.join(args.work, "inventory.json")
    out_path = os.path.join(args.output, "result.json")
    for path, obj in ((inv_path, inventory), (out_path, skeleton)):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)

    print(json.dumps({
        "case_id": case_id,
        "status": "partial",
        "inventory": inv_path,
        "result": out_path,
        "materials_count": len(materials),
        "contract_fields": sorted(contract.keys()),
        "errors": len(errors),
        "degradations": len(degradations),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
