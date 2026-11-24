#!/usr/bin/env python3
"""discover.py — 发现输入、解析契约、先写合法 result.json 骨架。

用途：在任何写作/改稿动作之前，先落盘一份合法的 partial 结果，
保证中途异常也不会留下不完整产物。只依赖 Python 标准库。

用法：
    python3 -B scripts/discover.py --input /input --output /app/output --work /app/work
"""
import argparse
import json
import os
import sys
from pathlib import Path

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB 上限


def read_text_tolerant(path):
    """按 utf-8-sig 优先读取，坏编码降级，绝不抛异常。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError:
            return None
    try:
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None


def load_json_tolerant(path):
    text = read_text_tolerant(path)
    if text is None:
        return None, "cannot_read"
    text = text.lstrip("\ufeff")
    try:
        return json.loads(text), "ok"
    except json.JSONDecodeError as exc:
        return None, "bad_json: %s" % exc
    except (TypeError, ValueError) as exc:
        return None, "bad_json: %s" % exc


def first_of(mapping, *keys):
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def find_case_id(data, task_text=None):
    if isinstance(data, dict):
        for key in ("case_id", "caseId", "id", "challenge_id", "challengeId", "case"):
            val = data.get(key)
            if isinstance(val, str) and val:
                return val
        # 一层嵌套
        for nested_key in ("task", "meta", "metadata", "case"):
            nested = data.get(nested_key)
            if isinstance(nested, dict):
                for key in ("case_id", "caseId", "id"):
                    val = nested.get(key)
                    if isinstance(val, str) and val:
                        return val
    return None


def find_output_contract(data):
    """尽量健壮地定位 output_contract：顶层或一层嵌套。"""
    candidates = []
    if isinstance(data, dict):
        for key in ("output_contract", "outputContract", "contract", "output",
                    "result_fields", "result_schema", "output_schema"):
            if key in data:
                candidates.append(data[key])
        for nested_key in ("task", "spec", "output", "result"):
            nested = data.get(nested_key)
            if isinstance(nested, dict):
                for key in ("output_contract", "outputContract", "contract",
                            "fields", "schema"):
                    if key in nested:
                        candidates.append(nested[key])
    for cand in candidates:
        if isinstance(cand, (dict, list)) and cand:
            return cand
        if isinstance(cand, dict) and not cand:
            return cand
    # 返回第一个非空或空 dict
    for cand in candidates:
        if isinstance(cand, (dict, list)):
            return cand
    return None


def infer_empty(schema):
    """根据 schema 结构推断空值，保持字段可编辑。"""
    if isinstance(schema, dict):
        if "default" in schema and schema["default"] is not None:
            return schema["default"]
        t = str(schema.get("type", "")).lower()
        if t in ("array", "list") or "items" in schema:
            return []
        if t in ("object", "dict", "map"):
            return {}
        if "example" in schema:
            ex = schema["example"]
            if isinstance(ex, list):
                return []
            if isinstance(ex, dict):
                return {}
        if "enum" in schema and isinstance(schema["enum"], list) and schema["enum"]:
            return schema["enum"][0]
        return None
    if isinstance(schema, list):
        return []
    if isinstance(schema, (str, int, float, bool)):
        return None
    return None


def build_contract_fields(contract):
    """把契约字段全部回填为空值，不改名、不删未知键。"""
    if isinstance(contract, dict):
        fields = {}
        for key, val in contract.items():
            fields[key] = infer_empty(val)
        return fields
    if isinstance(contract, list):
        fields = {}
        for item in contract:
            if isinstance(item, str):
                fields[item] = None
            elif isinstance(item, dict) and "name" in item:
                fields[item["name"]] = infer_empty(item)
            elif isinstance(item, dict):
                for k in item:
                    fields.setdefault(k, infer_empty(item[k]))
        return fields
    return {}


def scan_input(input_dir, degradations):
    """扫描输入目录，返回清单，超限文件记录但不读。"""
    manifest = []
    root = Path(input_dir)
    if not root.exists():
        return manifest
    for path in sorted(root.rglob("*")):
        if path.is_file():
            size = path.stat().st_size
            rel = path.relative_to(root).as_posix()
            entry = {"path": rel, "bytes": size}
            if size > MAX_FILE_BYTES:
                entry["skipped"] = "oversize"
                degradations.append("oversize_skipped: %s (%d bytes)" % (rel, size))
            manifest.append(entry)
    return manifest


def build_skeleton(case_id, contract_fields, manifest, errors, degradations,
                   notes=None):
    skeleton = {
        "case_id": case_id,
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "evidence_map": [],
        "artifacts": [],
        "pending_human": [],
        "errors": list(errors),
        "degradations": list(degradations),
        "limitations": [],
        "runtime": {"seconds": None, "requests": None, "note":
                    "filled at finalize from platform-recorded values"},
    }
    if notes:
        skeleton["_discover_notes"] = notes
    # 契约字段直接并入顶层，保证不缺键
    for key, val in contract_fields.items():
        if key not in skeleton:
            skeleton[key] = val
    return skeleton


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="发现输入、解析 output_contract、先落盘 result.json 骨架")
    parser.add_argument("--input", default="/input",
                        help="输入目录（默认 /input）")
    parser.add_argument("--output", default="/app/output",
                        help="输出目录（默认 /app/output）")
    parser.add_argument("--work", default="/app/work",
                        help="工作目录（默认 /app/work）")
    args = parser.parse_args(argv)

    errors = []
    degradations = []
    notes = []

    out_dir = Path(args.output)
    work_dir = Path(args.work)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        work_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        errors.append("cannot_create_dirs: %s" % exc)

    manifest = scan_input(args.input, degradations)
    notes.append("input_files_found=%d" % len(manifest))

    case_path = Path(args.input) / "case.json"
    data = None
    if case_path.exists():
        data, status = load_json_tolerant(case_path)
        if status != "ok":
            errors.append("case_json: %s" % status)
    else:
        errors.append("case_json: missing at %s" % case_path)

    task_text = None
    task_path = Path(args.input) / "task.md"
    if task_path.exists():
        task_text = read_text_tolerant(task_path)
    else:
        # 当前目录也可能有 task.md
        alt = Path(args.input).resolve().parent / "task.md"
        if alt.exists():
            task_text = read_text_tolerant(alt)

    case_id = find_case_id(data) if isinstance(data, dict) else None
    contract = find_output_contract(data) if isinstance(data, dict) else None
    contract_fields = build_contract_fields(contract) if contract is not None else {}

    if contract is None:
        notes.append("output_contract: not_found_in_case_json (empty contract fields)")
    else:
        notes.append("output_contract_fields=%d" % len(contract_fields))

    # 保存原始契约副本供后续脚本/Agent 参考
    if contract is not None:
        try:
            with open(work_dir / "output_contract.json", "w", encoding="utf-8") as fh:
                json.dump(contract, fh, ensure_ascii=False, indent=2)
        except OSError as exc:
            errors.append("cannot_write_contract_snapshot: %s" % exc)

    # 保存文件清单
    try:
        with open(work_dir / "manifest.json", "w", encoding="utf-8") as fh:
            json.dump({"input": manifest, "notes": notes, "errors": errors},
                      fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        errors.append("cannot_write_manifest: %s" % exc)

    if case_id is None:
        errors.append("case_id: not_found")

    status = "partial"
    if not manifest or data is None:
        status = "insufficient_evidence"
    if case_id is None and (not manifest):
        status = "input_error"

    skeleton = build_skeleton(case_id, contract_fields, manifest,
                              errors, degradations, notes)
    skeleton["status"] = status

    result_path = out_dir / "result.json"
    try:
        with open(result_path, "w", encoding="utf-8") as fh:
            json.dump(skeleton, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        # 写盘失败也要尽量打印，不让进程崩溃
        print(json.dumps({"case_id": case_id, "status": "input_error",
                          "errors": ["cannot_write_result: %s" % exc]},
                         ensure_ascii=False, indent=2))
        return 0

    summary = {
        "case_id": case_id,
        "status": status,
        "contract_fields": len(contract_fields),
        "input_files": len(manifest),
        "errors": errors,
        "result_path": str(result_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
