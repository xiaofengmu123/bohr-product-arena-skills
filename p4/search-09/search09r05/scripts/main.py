#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litqa-evidence 运行脚手架（契约优先 + 全程 change_log）。

只依赖 Python 标准库。职责：
  1. 发现 /input 下的 case.json、task.md 与材料文件；
  2. 递归定位 output_contract（任意键名/嵌套层级），逐字段解析默认值；
  3. 立即落盘一份字段齐备的 partial 骨架 result.json：case_id + status +
     原样回填的契约字段 + contract_alignment + 空数组结构 + 首条 change_log；
  4. validate 校验最终 result.json 的结构、契约对齐与 change_log 完整性。

用法（在 SKILL.md 所在目录下运行，相对路径）：
  python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py validate --input /input --output /app/output
  python3 -B scripts/selftest.py
"""

import argparse
import datetime
import json
import os
import re
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_INPUT = os.environ.get("SKILL_INPUT_DIR", "/input")
DEFAULT_OUTPUT = os.environ.get("SKILL_OUTPUT_DIR", "/app/output")
DEFAULT_WORK = os.environ.get("SKILL_WORK_DIR", "/app/work")
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB，超限跳过并记录
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
CONTRACT_KEY_RE = re.compile(r"^(output_?contract|output_?schema|output_?spec|contract)$", re.I)
MAX_DEPTH = 8

# ---------------------------------------------------------------------------
# 基础 I/O（容错，绝不抛异常到调用方）
# ---------------------------------------------------------------------------


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """以 utf-8-sig 优先读取文本；坏编码/超大文件降级并返回错误列表。"""
    errors = []
    p = Path(path)
    if not p.exists():
        return None, ["missing: %s" % p]
    try:
        size = p.stat().st_size
    except OSError as exc:
        return None, ["stat_failed: %s: %s" % (p, exc)]
    if size > max_bytes:
        return None, ["oversize(%d>%d): %s" % (size, max_bytes, p)]
    raw = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            raw = p.read_bytes().decode(enc)
            break
        except (UnicodeDecodeError, OSError) as exc:
            errors.append("decode(%s)_failed: %s: %s" % (enc, p, exc))
    if raw is None:
        return None, errors
    return raw, errors


def load_json_lenient(path):
    """读 JSON；坏 JSON 返回 None 并记录错误，绝不抛异常。"""
    text, errors = read_text(path)
    if text is None:
        return None, errors
    try:
        return json.loads(text), errors
    except json.JSONDecodeError as exc:
        return None, errors + ["bad_json: %s: %s" % (path, exc)]


def atomic_write_json(path, data):
    """先写临时文件再 rename，避免留下半截 JSON。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(str(tmp), str(p))


# ---------------------------------------------------------------------------
# 输入发现与契约定位
# ---------------------------------------------------------------------------


def discover_input(input_dir):
    """扫描输入目录，返回可识别的文件清单（不假设固定文件名）。"""
    root = Path(input_dir)
    found = {"case_json": [], "task_md": [], "materials": [], "other": []}
    if not root.is_dir():
        return found, ["input_dir_missing: %s" % root]
    try:
        entries = sorted(p for p in root.rglob("*") if p.is_file())
    except OSError as exc:
        return found, ["input_dir_read_failed: %s: %s" % (root, exc)]
    for p in entries:
        name = p.name.lower()
        if name == "case.json":
            found["case_json"].append(p)
        elif name == "task.md":
            found["task_md"].append(p)
        elif p.suffix.lower() in (".pdf", ".txt", ".md", ".json", ".xml", ".csv", ".html"):
            found["materials"].append(p)
        else:
            found["other"].append(p)
    return found, []


def find_contract(obj, path="", depth=0):
    """递归查找 output_contract（大小写不敏感，任意嵌套）。"""
    if depth > MAX_DEPTH:
        return None
    if isinstance(obj, dict):
        for key, value in obj.items():
            if CONTRACT_KEY_RE.match(str(key)):
                return value
            hit = find_contract(value, path + "/" + str(key), depth + 1)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            hit = find_contract(value, path + "[%d]" % idx, depth + 1)
            if hit is not None:
                return hit
    return None


def default_for_field(spec):
    """根据契约条目推断默认值：数组→[]，对象→{}，标量→null，显式 default 优先。"""
    if isinstance(spec, dict):
        if "default" in spec:
            return spec["default"]
        typ = str(spec.get("type", "")).lower()
        if typ in ("array", "list"):
            return []
        if typ in ("object", "map", "dict"):
            return {}
        if typ == "null":
            return None
        return None
    if isinstance(spec, list):
        return []
    return None


def resolve_contract_fields(contract):
    """把契约解析为有序字段列表（name/default/required）+ 元信息。

    支持 JSON Schema（properties/required）、平铺 dict、字段名 list、
    或 [{"name":…}] 列表；无法识别的键原样保留到 unknown_keys。
    """
    fields = []
    meta = {"shape": "none", "unknown_keys": [], "required": []}
    if isinstance(contract, str):
        txt = contract.strip()
        if txt.startswith(("{", "[")):
            try:
                contract = json.loads(txt)
            except json.JSONDecodeError:
                meta["shape"] = "unparsable_string"
                return fields, meta
    if isinstance(contract, dict):
        if "properties" in contract and isinstance(contract["properties"], dict):
            meta["shape"] = "json_schema"
            required = contract.get("required")
            required = required if isinstance(required, list) else []
            meta["required"] = [str(r) for r in required]
            for k, v in contract["properties"].items():
                name = str(k)
                fields.append({"name": name, "default": default_for_field(v),
                               "required": name in meta["required"]})
        else:
            meta["shape"] = "flat_dict"
            for k, v in contract.items():
                fields.append({"name": str(k), "default": default_for_field(v), "required": False})
    elif isinstance(contract, list):
        meta["shape"] = "list"
        for item in contract:
            if isinstance(item, str):
                fields.append({"name": item, "default": None, "required": False})
            elif isinstance(item, dict):
                name = item.get("name") or item.get("field") or item.get("key") or item.get("id")
                if name is not None:
                    fields.append({"name": str(name), "default": default_for_field(item),
                                   "required": bool(item.get("required", False))})
                else:
                    meta["unknown_keys"].append(item)
            else:
                meta["unknown_keys"].append(item)
    else:
        meta["shape"] = "scalar"
    return fields, meta


def extract_case_id(data, input_dir):
    """从 case.json 取 case_id；缺失则退回目录名/未知。"""
    if isinstance(data, dict):
        for key in ("case_id", "caseId", "id"):
            if key in data and isinstance(data[key], (str, int)):
                return str(data[key])
    p = Path(input_dir)
    return str(p.name) if p.name else "unknown"


# ---------------------------------------------------------------------------
# change_log（本版的一等公民：结构化审计轨迹）
# ---------------------------------------------------------------------------


def utcnow_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_change_event(seq, event, stage, reason, detail=None):
    return {"seq": seq, "time_utc": utcnow_iso(), "event": event,
            "stage": stage, "reason": reason, "detail": detail or {}}


def append_change_log(result, event, stage, reason, detail=None):
    cl = result.setdefault("change_log", [])
    cl.append(make_change_event(len(cl) + 1, event, stage, reason, detail))
    return result


# ---------------------------------------------------------------------------
# 骨架与结果
# ---------------------------------------------------------------------------


def build_skeleton(case_id, contract, contract_path):
    """构造一份字段齐备的 partial 骨架（契约字段逐字回填）。"""
    now = time.time()
    fields, meta = resolve_contract_fields(contract)
    result = {"case_id": case_id, "status": "partial"}
    for f in fields:
        result[f["name"]] = f["default"]
    result["contract_alignment"] = {
        "status": "ok" if fields else ("not_found" if contract is None else "empty"),
        "contract_path": contract_path,
        "shape": meta["shape"],
        "field_count": len(fields),
        "fields": [{"name": f["name"], "default": f["default"], "required": f["required"]} for f in fields],
        "unknown_keys_preserved": meta["unknown_keys"],
        "required_fields": meta.get("required", []),
    }
    result["assessment"] = []
    result["candidates"] = []
    result["screening"] = []
    result["evidence_records"] = []
    result["omissions"] = []
    result["grade"] = None
    result["sources"] = []
    result["uncertainty"] = []
    result["pending_human"] = []
    result["errors"] = []
    result["degradations"] = []
    result["limitations"] = []
    result["change_log"] = [
        make_change_event(1, "skeleton_written", "P0",
                          "先落盘字段齐备的 partial 骨架，防止中途异常留下空产物")
    ]
    result["runtime"] = {"start_epoch": now, "skeleton_epoch": now, "finish_epoch": None}
    if contract is not None:
        append_change_log(result, "contract_discovered", "P0",
                          "定位并逐字段回填 output_contract",
                          {"path": contract_path, "field_count": len(fields), "shape": meta["shape"]})
    return result


def run_skeleton(input_dir, output_dir, work_dir):
    """skeleton 命令入口：发现输入→解析契约→落盘骨架。绝不抛异常。"""
    errors = []
    out = Path(output_dir)
    work = Path(work_dir)
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    found, errs = discover_input(input_dir)
    errors.extend(errs)

    data = None
    contract = None
    contract_path = None
    if found["case_json"]:
        cj = found["case_json"][0]
        data, cerrs = load_json_lenient(str(cj))
        errors.extend(cerrs)
        if data is None:
            errors.append("case_json_unparsable: %s" % cj)
        else:
            contract = find_contract(data)
            contract_path = "case.json"
    else:
        errors.append("missing: case.json")
    if not found["task_md"]:
        errors.append("missing: task.md")
    if not found["materials"]:
        errors.append("no_materials_found")

    case_id = extract_case_id(data if data is not None else {}, input_dir)
    result = build_skeleton(case_id, contract, contract_path)
    result["errors"] = errors
    if errors:
        result["degradations"] = [{"scope": "skeleton", "detail": e} for e in errors]

    # 骨架阶段的诚实 status
    if not found["case_json"] or data is None:
        result["status"] = "input_error"
    elif not found["materials"]:
        result["status"] = "insufficient_evidence"
    else:
        result["status"] = "partial"

    atomic_write_json(str(out / "result.json"), result)
    return result


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def validate_result(result, case_data, problems):
    """在 problems 中追加结构/契约/一致性检查结果（level=error|warning）。"""
    if not isinstance(result, dict):
        problems.append({"level": "error", "code": "result_not_object", "message": "result.json 顶层必须是对象"})
        return problems
    if not result.get("case_id"):
        problems.append({"level": "error", "code": "missing_case_id", "message": "缺少 case_id"})
    status = result.get("status")
    if status not in ALLOWED_STATUS:
        problems.append({"level": "error", "code": "bad_status", "message": "status 非法: %r" % (status,)})
    for key in ("sources", "uncertainty", "change_log", "runtime"):
        if key not in result:
            problems.append({"level": "error", "code": "missing_%s" % key, "message": "缺少必填字段 %s" % key})
    cl = result.get("change_log")
    if not isinstance(cl, list) or not cl:
        problems.append({"level": "error", "code": "empty_change_log", "message": "change_log 必须为非空数组"})

    # 契约逐字段对齐检查
    contract = find_contract(case_data) if isinstance(case_data, dict) else None
    if contract is not None:
        fields, meta = resolve_contract_fields(contract)
        for f in fields:
            if f["name"] not in result:
                problems.append({"level": "error", "code": "missing_contract_field",
                                 "message": "契约字段缺失: %s" % f["name"]})
        ca = result.get("contract_alignment")
        if not isinstance(ca, dict) or ca.get("status") != "ok":
            problems.append({"level": "warning", "code": "contract_alignment_mismatch",
                             "message": "contract_alignment 未标记为 ok"})
        elif ca.get("field_count") != len(fields):
            problems.append({"level": "warning", "code": "contract_alignment_count",
                             "message": "contract_alignment.field_count 与契约不一致"})

    # assessment 结构
    for i, item in enumerate(result.get("assessment") or []):
        for k in ("dimension", "checkpoint", "verdict", "judgment"):
            if not item.get(k):
                problems.append({"level": "warning", "code": "assessment_item_incomplete",
                                 "message": "assessment[%d] 缺 %s" % (i, k)})
        if item.get("judgment") and not item.get("quote"):
            problems.append({"level": "warning", "code": "judgment_without_quote",
                             "message": "assessment[%d] 有判断无逐字证据" % i})

    # 一致性
    if result.get("status") == "ok" and not (result.get("assessment") or result.get("candidates")):
        problems.append({"level": "warning", "code": "ok_but_empty",
                         "message": "status=ok 但 assessment 与 candidates 均为空"})
    return problems


def run_validate(input_dir, output_dir):
    """validate 命令入口。返回 (has_errors, problems)，退出码据此决定。"""
    problems = []
    out = Path(output_dir)
    rp = out / "result.json"
    if not rp.exists():
        problems.append({"level": "error", "code": "result_missing", "message": "result.json 不存在"})
    else:
        result, errs = load_json_lenient(str(rp))
        if result is None:
            problems.append({"level": "error", "code": "result_unparsable", "message": "result.json 无法解析"})
            problems.extend({"level": "error", "code": "parse_error", "message": e} for e in errs)
        else:
            data = None
            found, _ = discover_input(input_dir)
            if found["case_json"]:
                data, _ = load_json_lenient(str(found["case_json"][0]))
            validate_result(result, data, problems)
    has_errors = any(p["level"] == "error" for p in problems)
    atomic_write_json(str(out / "validation_report.json"),
                      {"has_errors": has_errors, "problems": problems})
    print(json.dumps({"has_errors": has_errors, "problems": problems},
                     ensure_ascii=False, indent=2))
    return has_errors, problems


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(description="litqa-evidence 运行脚手架")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("skeleton", "validate"):
        sp = sub.add_parser(name)
        sp.add_argument("--input", default=DEFAULT_INPUT)
        sp.add_argument("--output", default=DEFAULT_OUTPUT)
        if name == "skeleton":
            sp.add_argument("--work", default=DEFAULT_WORK)
    args = parser.parse_args(argv)
    if args.command == "skeleton":
        run_skeleton(args.input, args.output, args.work)
        return 0
    if args.command == "validate":
        has_errors, _ = run_validate(args.input, args.output)
        return 1 if has_errors else 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
