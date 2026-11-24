#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litqa-audit 运行脚手架（纯标准库、确定性）。

职责：
  1. 扫描 /input，发现 case.json、task.md 与材料文件（不假设固定文件名）；
  2. 从 case.json 递归定位 output_contract（键名大小写不敏感），原样回填全部
     字段（缺值 null/[]，不改名、不删未知键）；
  3. 立即写出合法 partial 骨架 result.json（含 case_id、契约字段、status、
     sources/uncertainty/pending_human/change_log/runtime），保证中途异常也不留
     空产物；
  4. validate 校验最终 result.json 结构、契约回填与证据字段完整性。

确定性：run_id 由输入文件内容的 SHA-256 派生，不记录墙钟时间；所有 JSON 经
sort_keys 原子写盘。同一输入两次运行产物逐字节一致。

用法（在 SKILL.md 所在目录下运行）：
  python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py validate --input /input --output /app/output
  python3 -B scripts/main.py self-test
"""

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from litqa import (  # noqa: E402
    atomic_write_json,
    canonical_key,
    read_json_lenient,
    upsert_change_log,
    upsert_phase,
)

DEFAULT_INPUT = os.environ.get("SKILL_INPUT_DIR", "/input")
DEFAULT_OUTPUT = os.environ.get("SKILL_OUTPUT_DIR", "/app/output")
DEFAULT_WORK = os.environ.get("SKILL_WORK_DIR", "/app/work")
MAX_FILE_BYTES = 2 * 1024 * 1024
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
CONTRACT_KEY_RE = re.compile(
    r"^(output_?contract|output_?schema|contract|interface)$", re.I)
MATERIAL_SUFFIXES = {".pdf", ".txt", ".md", ".json", ".xml", ".csv", ".html"}


# ---------------------------------------------------------------------------
# 输入发现与契约定位
# ---------------------------------------------------------------------------

def discover_input(input_dir):
    """扫描输入目录，返回可识别文件清单（确定性排序）。"""
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
            found["case_json"].append(str(p))
        elif name == "task.md":
            found["task_md"].append(str(p))
        elif p.suffix.lower() in MATERIAL_SUFFIXES:
            found["materials"].append(str(p))
        else:
            found["other"].append(str(p))
    return found, []


def find_contract(obj, depth=0):
    """递归查找 output_contract（大小写不敏感，任意嵌套，深度上限 6）。"""
    if depth > 6:
        return None
    if isinstance(obj, dict):
        for key, value in obj.items():
            if CONTRACT_KEY_RE.match(str(key)):
                return value
            hit = find_contract(value, depth + 1)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for value in obj:
            hit = find_contract(value, depth + 1)
            if hit is not None:
                return hit
    return None


def _default_for(spec):
    """按契约里的类型描述给默认值；无法判断则 None。"""
    if isinstance(spec, dict):
        typ = str(spec.get("type", "")).lower()
        if "array" in typ:
            return []
        if "object" in typ:
            props = spec.get("properties")
            if isinstance(props, dict):
                return {str(k): _default_for(v) for k, v in props.items()}
            return {}
        if "null" in typ:
            return None
        return None
    if isinstance(spec, list):
        return []
    return None


def contract_defaults(contract):
    """把契约字段原样回填为顶层默认值；不改名、不删未知键。"""
    if isinstance(contract, dict):
        props = contract.get("properties")
        if isinstance(props, dict):
            return {str(k): _default_for(v) for k, v in sorted(props.items())}
        return {str(k): _default_for(v) for k, v in sorted(contract.items())}
    if isinstance(contract, list):
        return {str(k): None for k in contract if isinstance(k, str)}
    return {}


def extract_case_id(data, input_dir):
    """从 case.json 取 case_id；缺失则退回文件名/目录名。"""
    if isinstance(data, dict):
        for key in ("case_id", "caseId", "id"):
            if key in data and isinstance(data[key], (str, int)):
                return str(data[key])
    return Path(input_dir).name or "unknown"


def compute_run_id(input_dir, files):
    """由输入文件内容 SHA-256 派生稳定 run_id（不依赖墙钟）。"""
    h = hashlib.sha256()
    for f in files:
        try:
            rel = os.path.relpath(f, input_dir).replace("\\", "/")
            h.update(rel.encode("utf-8"))
            h.update(b"\x00")
            with open(f, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    h.update(chunk)
            h.update(b"\x00")
        except OSError:
            continue
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# 骨架
# ---------------------------------------------------------------------------

def build_skeleton(case_id, contract_defaults_, run_id, notes=None):
    """构造合法 partial 骨架（空数组 + 契约字段 + 状态/日志/runtime）。"""
    result = {"case_id": case_id, "status": "partial"}
    result.update(contract_defaults_)
    result.update({
        "research_question": None,
        "search_scope": None,
        "assessment": [],
        "grades": [],
        "overall_grade": None,
        "candidates": [],
        "screening": [],
        "evidence_records": [],
        "omissions": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "change_log": [],
        "runtime": {
            "run_id": run_id,
            "deterministic": True,
            "phases": [],
        },
    })
    upsert_change_log(result, "skeleton", "skeleton_written",
                      "partial skeleton written before analysis")
    upsert_phase(result, "skeleton", "done")
    if notes:
        result["limitations"] = sorted(set(notes))
    return result


def cmd_skeleton(args):
    """立即落盘 partial 骨架；任何输入异常都不抛、退出码 0。"""
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    input_path = Path(args.input)
    found, derrs = discover_input(args.input)
    errors = list(derrs)
    degradations = []

    case_id = "unknown"
    contract = None
    case_data = None
    case_file = found["case_json"][0] if found["case_json"] else None

    if not input_path.is_dir():
        status = "input_error"
        errors.append("input_dir_missing")
    elif case_file is None:
        status = "insufficient_evidence"
        errors.append("case_json_missing")
    else:
        case_data, cerrs = read_json_lenient(case_file)
        errors.extend(cerrs)
        if case_data is None:
            status = "input_error"
            errors.append("case_json_unparsable")
        else:
            case_id = extract_case_id(case_data, args.input)
            contract = find_contract(case_data)
            status = "partial"

    all_files = sorted(found["case_json"] + found["task_md"] +
                       found["materials"] + found["other"])
    run_id = compute_run_id(args.input, all_files)
    if not found["materials"]:
        degradations.append("no_material_files_detected")
    cdefs = contract_defaults(contract) if contract is not None else {}

    result = build_skeleton(case_id, cdefs, run_id)
    result["status"] = status
    result["errors"] = sorted(set(errors))
    result["degradations"] = sorted(set(degradations))

    sources = []
    if case_file:
        sources.append({"id": "case_json", "type": "input",
                        "path_or_uri": case_file, "locator": "", "quote": "",
                        "note": ""})
    if found["task_md"]:
        sources.append({"id": "task_md", "type": "input",
                        "path_or_uri": found["task_md"][0], "locator": "",
                        "quote": "", "note": ""})
    for m in found["materials"]:
        sources.append({"id": Path(m).name, "type": "material",
                        "path_or_uri": m, "locator": "", "quote": "",
                        "note": ""})
    result["sources"] = sorted(sources, key=canonical_key)

    atomic_write_json(out / "result.json", result)
    atomic_write_json(work / "discovery.json",
                      {"found": found, "run_id": run_id})
    # 尽早给出可读文本交付件骨架，便于后续增量替换
    (out / "assessment.md").write_text(
        "# 文献研究质量评价（骨架）\n\n- case_id: %s\n- status: %s\n\n"
        "评价表将在 P2 阶段增量填充；证据锚点见 result.json 的 assessment。\n"
        % (case_id, status), encoding="utf-8")
    print(json.dumps({"case_id": case_id, "status": status,
                      "contract_fields": sorted(cdefs),
                      "run_id": run_id,
                      "result": str(out / "result.json")}, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def cmd_validate(args):
    """校验 result.json 结构与契约回填；error 级问题退出码 1。"""
    out = Path(args.output)
    result_file = out / "result.json"
    errors, warnings = [], []
    result = None
    if not result_file.is_file():
        errors.append("result.json_missing")
    else:
        result, rerrs = read_json_lenient(result_file)
        errors.extend(rerrs)
        if result is not None:
            if not result.get("case_id"):
                errors.append("case_id_missing")
            if result.get("status") not in ALLOWED_STATUS:
                errors.append("status_invalid: %s" % result.get("status"))
            for key in ("assessment", "sources", "uncertainty",
                        "pending_human", "change_log", "limitations"):
                if not isinstance(result.get(key), list):
                    errors.append("field_not_list: %s" % key)
            # 契约字段回填：与 case.json 的契约对照，缺字段报 error
            found, _ = discover_input(args.input)
            case_file = found["case_json"][0] if found["case_json"] else None
            if case_file:
                case_data, _ = read_json_lenient(case_file)
                if case_data is not None:
                    contract = find_contract(case_data)
                    if contract is not None:
                        for k in contract_defaults(contract):
                            if k not in result:
                                errors.append("contract_field_missing: %s" % k)
            # assessment 条目字段完整性
            for i, e in enumerate(result.get("assessment", [])):
                for key in ("dimension", "checkpoint", "verdict", "locator",
                            "quote"):
                    if key not in e:
                        errors.append("assessment[%d].missing_%s" % (i, key))
                if e.get("verdict") not in (
                        "clear_risk", "possible_risk", "no_risk_indicator",
                        "insufficient_evidence"):
                    warnings.append("assessment[%d].verdict_unrestricted" % i)
    report = {"ok": not errors, "errors": sorted(set(errors)),
              "warnings": sorted(set(warnings)),
              "result_file": str(result_file)}
    atomic_write_json(out / "validate_report.json", report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


# ---------------------------------------------------------------------------
# self-test（委托给 selftest.py，单一事实来源）
# ---------------------------------------------------------------------------

def cmd_self_test(args):
    import selftest
    return selftest.main(argv=[])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(description="litqa-audit 运行脚手架")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp_sk = sub.add_parser("skeleton", help="立即落盘 partial 骨架")
    sp_sk.add_argument("--input", default=DEFAULT_INPUT)
    sp_sk.add_argument("--output", default=DEFAULT_OUTPUT)
    sp_sk.add_argument("--work", default=DEFAULT_WORK)

    sp_v = sub.add_parser("validate", help="校验最终 result.json")
    sp_v.add_argument("--input", default=DEFAULT_INPUT)
    sp_v.add_argument("--output", default=DEFAULT_OUTPUT)

    sub.add_parser("self-test", help="三场景自检（空目录/坏JSON/缺输入 + 确定性）")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "skeleton":
        cmd_skeleton(args)
        return 0
    if args.cmd == "validate":
        return cmd_validate(args)
    if args.cmd == "self-test":
        return cmd_self_test(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
