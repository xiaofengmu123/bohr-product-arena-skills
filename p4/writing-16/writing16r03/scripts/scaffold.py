#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scaffold.py —— 输入发现 + 早期结果骨架（先落盘一版合法 partial 结果）。

职责：
1. 扫描 /input，识别 case.json、task.md 与科研材料，不假设固定文件名/键名。
2. 解析 case.json（BOM/坏编码/坏 JSON 容错），提取 case_id 与 output_contract。
3. 在 /app/output 写出合法的 result.json 骨架：保留 output_contract 全部键，
   缺值给 null/[]/""；并附带 status/sources/uncertainty/change_log 等字段。
4. 在 /app/work 写 manifest.json，记录发现的输入文件与解析状态。

只用 Python 标准库；默认参数 --input /input --output /app/output --work /app/work。
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文件 2MB 上限
TEXT_EXTS = {".md", ".txt", ".json", ".jsonl", ".csv", ".tsv",
             ".yaml", ".yml", ".tex", ".xml", ".html", ".rst"}
PDF_EXT = ".pdf"
SKIP_DIRS = {".git", "__pycache__", ".parser"}

DEFAULT_CONTRACT = {
    "deliverable": {"type": "string", "required": True},
    "evidence_map": {"type": "array", "required": True},
    "change_record": {"type": "array", "required": True},
}


def read_text_robust(path: Path):
    """按 utf-8-sig -> utf-8 -> latin-1 依次尝试；全部失败返回 (None, 错误)。"""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return None, "undecodable"


def load_json_robust(path: Path):
    """返回 (data, warning)。坏 JSON 返回 (None, 'bad-json:...')。"""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return json.loads(raw.decode(enc)), None
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
    try:
        return json.loads(raw.decode("latin-1")), "decoded-as-latin-1"
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass
    try:
        return json.loads(raw.decode("utf-8-sig", errors="replace")), "decoded-with-replacement"
    except json.JSONDecodeError as e:
        return None, "bad-json:" + str(e)


def sha1_hex(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def discover(input_dir: Path):
    """扫描输入目录，返回 (files, skipped, degradations)。"""
    files = []
    skipped = []
    degradations = []
    if not input_dir.exists():
        return files, skipped, ["input_dir_missing:" + str(input_dir)]
    for p in sorted(input_dir.rglob("*")):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size > MAX_FILE_BYTES:
            skipped.append({"path": str(p), "reason": "size_exceeds_2MB", "bytes": size})
            continue
        rel = str(p.relative_to(input_dir))
        kind = "case" if p.name.lower() == "case.json" else (
            "task" if p.name.lower() == "task.md" else (
                "pdf" if p.suffix.lower() == PDF_EXT else (
                    "text" if p.suffix.lower() in TEXT_EXTS else "other")))
        files.append({"path": str(p), "rel": rel, "kind": kind, "bytes": size})
    return files, skipped, degradations


def init_value(spec):
    """根据 output_contract 的类型提示初始化空值；未知一律 null。"""
    if isinstance(spec, dict):
        t = str(spec.get("type", "")).lower()
        if "array" in t or "list" in t:
            return []
        if "object" in t or "dict" in t or "map" in t:
            return {}
        if "string" in t or "text" in t or "path" in t:
            return ""
        if "number" in t or "int" in t or "float" in t or "bool" in t:
            return None
        return None
    if isinstance(spec, list):
        return []
    if isinstance(spec, str):
        return ""
    return None


def extract_contract(case_data, task_text):
    """优先取 case.json 的 output_contract/outputContract；否则取 task.md；再否则用默认。"""
    if isinstance(case_data, dict):
        for key in ("output_contract", "outputContract", "output"):
            v = case_data.get(key)
            if isinstance(v, (dict, list)) and v:
                return v, "case.json:" + key
    if task_text:
        m = re.search(r"output_contract\s*[：:]\s*(\{.*?\})", task_text, re.S)
        if m:
            try:
                return json.loads(m.group(1)), "task.md:output_contract"
            except json.JSONDecodeError:
                pass
    return DEFAULT_CONTRACT, "default"


def contract_fields(contract):
    """返回 (ordered_field_names, spec_map)。"""
    if isinstance(contract, dict):
        return list(contract.keys()), contract
    if isinstance(contract, list):
        names = []
        for item in contract:
            if isinstance(item, dict) and item.get("name"):
                names.append(item["name"])
            elif isinstance(item, str):
                names.append(item)
        return names, {}
    return list(DEFAULT_CONTRACT.keys()), DEFAULT_CONTRACT


def extract_case_id(case_data, task_text):
    if isinstance(case_data, dict):
        for key in ("case_id", "id", "challenge_id", "challengeId"):
            v = case_data.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    if task_text:
        m = re.search(r"challenge_id[：:]\s*`?([A-Za-z0-9\-_.]+)`?", task_text)
        if m:
            return m.group(1)
        m = re.search(r"case_id[：:]\s*`?([A-Za-z0-9\-_.]+)`?", task_text)
        if m:
            return m.group(1)
    return "unknown"


def main():
    ap = argparse.ArgumentParser(description="科研成果转化写作：输入发现与结果骨架")
    ap.add_argument("--input", default="/input", help="输入目录（默认 /input）")
    ap.add_argument("--output", default="/app/output", help="输出目录（默认 /app/output）")
    ap.add_argument("--work", default="/app/work", help="工作目录（默认 /app/work）")
    args = ap.parse_args()

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    work_dir = Path(args.work)
    for d in (output_dir, work_dir):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            print("FATAL: cannot create dir", d, e, file=sys.stderr)
            sys.exit(1)

    start = time.time()
    errors, degradations = [], []
    files, skipped, disc_degrad = discover(input_dir)
    degradations.extend(disc_degrad)

    case_data, task_text = None, None
    case_path, task_path = None, None
    for f in files:
        if f["kind"] == "case" and case_path is None:
            case_path = Path(f["path"])
        if f["kind"] == "task" and task_path is None:
            task_path = Path(f["path"])

    if case_path is not None:
        case_data, warn = load_json_robust(case_path)
        if case_data is None:
            errors.append("case.json 无法解析: " + str(warn))
            degradations.append("case_json:" + str(warn))
        elif warn:
            degradations.append("case_json:" + str(warn))
    if task_path is not None:
        task_text, enc = read_text_robust(task_path)
        if task_text is None:
            degradations.append("task.md 无法解码")
    else:
        degradations.append("task.md 未发现")

    contract, contract_src = extract_contract(case_data, task_text)
    names, spec_map = contract_fields(contract)
    case_id = extract_case_id(case_data, task_text)

    result = {"case_id": case_id}
    for name in names:
        spec = spec_map.get(name) if isinstance(spec_map, dict) else None
        result[name] = init_value(spec)

    material_files = [f for f in files if f["kind"] in ("pdf", "text")]
    sources = [
        {"path": f["rel"], "kind": f["kind"], "bytes": f["bytes"]}
        for f in material_files
    ]

    # 状态判定：空目录/无材料 -> insufficient_evidence；case 损坏且无材料 -> input_error；
    # 其余 -> partial 骨架（随后增量改进）。
    if not files:
        status = "insufficient_evidence"
    elif not material_files:
        status = "input_error" if (case_path is not None and case_data is None) else "insufficient_evidence"
    else:
        status = "partial"

    result["status"] = status
    result["sources"] = sources
    result["uncertainty"] = [
        "output_contract 来源: " + contract_src,
        "本文件是早期骨架；status=partial 表示尚未完成最终改写，需按 SKILL.md 流程增量改进",
    ]
    result["change_log"] = [{
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(start)),
        "step": "scaffold",
        "action": "write result skeleton",
        "status": status,
        "contract_source": contract_src,
    }]
    result["errors"] = errors
    result["degradations"] = degradations
    result["pending_human"] = []
    result["runtime"] = {
        "tool": "scaffold.py",
        "start_ts": start,
        "elapsed_s": round(time.time() - start, 3),
    }

    manifest = {
        "input_dir": str(input_dir),
        "case_id": case_id,
        "contract_source": contract_src,
        "contract_fields": names,
        "files": files,
        "skipped": skipped,
        "errors": errors,
        "degradations": degradations,
    }

    result_path = output_dir / "result.json"
    manifest_path = work_dir / "manifest.json"
    try:
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        print("FATAL: write failed", e, file=sys.stderr)
        sys.exit(1)

    print("case_id:", case_id)
    print("status:", status)
    print("contract_source:", contract_src)
    print("contract_fields:", names)
    print("materials:", len(material_files), "files:", len(files), "skipped:", len(skipped))
    print("wrote:", result_path, manifest_path)
    print("result_json_sha1:", sha1_hex(result_path.read_bytes()))
    sys.exit(0)


if __name__ == "__main__":
    main()
