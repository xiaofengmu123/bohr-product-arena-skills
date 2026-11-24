#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litqa-plan 运行脚手架：先落盘、再分析、可复核。

本文件是"文献研究质量评价 + 可追溯科研检索" Skill 的运行脚手架，职责：
  1. 发现 /input 下的 case.json、task.md 与材料文件（不假设固定文件名/键名）；
  2. 递归定位 output_contract（键名大小写不敏感、任意嵌套），原样回填全部字段，
     缺值给 null/[]，不改名、不删未知键；
  3. 第一时间写出合法的 partial 结果骨架 result.json，并预置中间表示模板
     plan.json / assessment_matrix.json，保证任何中途异常都不留空产物；
  4. 校验最终 result.json 的结构完整性与证据引用。

只依赖 Python 标准库，确定性运行（无随机数、固定阈值、固定定位）。

用法（在 SKILL.md 所在目录下，用相对路径调用）：
  python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py validate --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py --help
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

DEFAULT_INPUT = os.environ.get("SKILL_INPUT_DIR", "/input")
DEFAULT_OUTPUT = os.environ.get("SKILL_OUTPUT_DIR", "/app/output")
DEFAULT_WORK = os.environ.get("SKILL_WORK_DIR", "/app/work")
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB，超限跳过并记录
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
CONTRACT_KEY_RE = re.compile(r"^(output_?contract|output_?schema|contract)$", re.I)
TEXT_SUFFIXES = {".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml",
                 ".bib", ".ris", ".rtf", ".htm", ".html", ".tex"}

SCHEMA_PLAN = "litqa.plan/1.0"
SCHEMA_MATRIX = "litqa.assessment_matrix/1.0"
SCHEMA_INDEX = "litqa.evidence_index/1.0"
SCHEMA_RESULT = "litqa.result/1.0"

# 结果骨架的结构字段（与 output_contract 回填字段并列，二者不冲突：先回填契约，再 setdefault）
STRUCTURAL_DEFAULTS = {
    "research_question": {},
    "retrieval_plan": {},
    "candidates": [],
    "screening": [],
    "evidence_records": [],
    "omissions": [],
    "assessment": [],
    "grade": {},
    "sources": [],
    "uncertainty": [],
    "pending_human": [],
    "errors": [],
    "degradations": [],
    "limitations": [],
    "change_log": [],
    "intermediates": {},
    "verification": {},
}


def now_utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def now_epoch():
    return time.time()


# ---------------------------------------------------------------------------
# 基础 I/O（容错）
# ---------------------------------------------------------------------------

def read_text(path, max_bytes=MAX_FILE_BYTES):
    """以 utf-8-sig 优先读取文本；坏编码/超大文件降级并返回错误列表，不抛异常。"""
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
    """扫描输入目录，返回可识别文件清单（不假设固定文件名）。"""
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
        elif p.suffix.lower() in (".pdf", ".txt", ".md", ".json", ".xml", ".csv",
                                  ".bib", ".ris", ".tsv") or "material" in name:
            found["materials"].append(p)
        else:
            found["other"].append(p)
    return found, []


def find_contract(obj, path="", depth=0):
    """递归查找 output_contract（大小写不敏感，任意嵌套，深度上限 6）。"""
    if depth > 6:
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


def _default_for(value):
    """根据契约里的类型描述给出默认值；无法判断则 None。"""
    if isinstance(value, dict):
        typ = str(value.get("type", "")).lower()
        if "array" in typ:
            return []
        if "object" in typ:
            return {}
        if "null" in typ:
            return None
        return None
    if isinstance(value, list):
        return []
    return None


def contract_defaults(contract):
    """把契约字段原样回填为顶层默认值；不改名、不删未知键。"""
    if isinstance(contract, dict):
        props = contract.get("properties")
        source = props if isinstance(props, dict) else contract
        if isinstance(source, dict):
            return {str(k): _default_for(v) for k, v in source.items()}
        return {}
    if isinstance(contract, list):
        return {str(k): None for k in contract if isinstance(k, str)}
    if isinstance(contract, str):
        return {contract: None}
    return {}


def extract_case_id(data, input_dir):
    """从 case.json 取 case_id，缺失则退回目录名/文件名。"""
    if isinstance(data, dict):
        for key in ("case_id", "caseId", "id"):
            if key in data and isinstance(data[key], (str, int)):
                return str(data[key])
    p = Path(input_dir)
    return p.name or "unknown"


# ---------------------------------------------------------------------------
# 骨架与结果
# ---------------------------------------------------------------------------

def plan_skeleton(case_id, status="partial"):
    """plan.json 模板（研究问题 + 检索计划 + 检索记录）。"""
    return {
        "schema": SCHEMA_PLAN,
        "case_id": case_id,
        "status": status,
        "research_question": {
            "text": None, "scope": None, "population": None,
            "intervention_exposure": None, "comparator": None, "outcome": None,
            "study_type": None, "confidence": None,
        },
        "retrieval_plan": {
            "query": None, "sources": [], "date_range": None, "language": [],
            "inclusion": [], "exclusion": [], "sort": None, "dedup_rule": None,
        },
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
    }


def matrix_skeleton(case_id, status="partial"):
    """assessment_matrix.json 模板（维度—判断—证据—等级 行式矩阵）。"""
    return {
        "schema": SCHEMA_MATRIX,
        "case_id": case_id,
        "status": status,
        "document_ref": None,
        "rubric_version": "references/rubric.md",
        "rows": [],
    }


def build_skeleton(case_id, contract, status, issues, input_dir):
    """构造一份合法 partial 结果骨架（含契约回填与空结构字段）。"""
    t0 = now_epoch()
    result = {
        "schema": SCHEMA_RESULT,
        "case_id": case_id,
        "status": status,
    }
    # 1) 原样回填契约字段（放在顶层，随后不覆盖）
    result.update(contract_defaults(contract))
    # 2) 结构字段 setdefault（契约若已定义同名键则保留契约默认值）
    for key, default in STRUCTURAL_DEFAULTS.items():
        result.setdefault(key, default)
    # 3) case_id / status 必须为真实值
    result["case_id"] = case_id
    result["status"] = status
    result["runtime"] = {
        "start_epoch": t0,
        "skeleton_epoch": t0,
        "finish_epoch": None,
    }
    result["change_log"] = [
        {
            "time_utc": now_utc(),
            "event": "skeleton",
            "detail": "partial 骨架已落盘；status=%s；input=%s" % (status, input_dir),
        }
    ]
    if issues:
        result["errors"] = list(issues)
    return result


def write_skeleton(input_dir, output_dir, work_dir):
    """执行 skeleton：发现输入、解析契约、写 result.json 与中间表示模板。"""
    issues = []
    out = Path(output_dir)
    work = Path(work_dir)
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    case_json_path = None
    data = None
    if Path(input_dir).is_dir():
        found, found_issues = discover_input(input_dir)
        issues.extend(found_issues)
        if found["case_json"]:
            case_json_path = found["case_json"][0]
            data, load_issues = load_json_lenient(case_json_path)
            issues.extend(load_issues)
    else:
        issues.append("input_dir_missing: %s" % input_dir)

    case_id = extract_case_id(data, input_dir)
    contract = find_contract(data) if isinstance(data, (dict, list)) else None

    # 状态判定：诚实反映当前可解析程度
    if not Path(input_dir).is_dir():
        status = "input_error"
    elif case_json_path is None:
        status = "insufficient_evidence"
    elif data is None:
        status = "partial"  # 坏 JSON：材料可能仍存在，先 partial 并记录错误
    else:
        status = "partial"

    result = build_skeleton(case_id, contract, status, issues, input_dir)
    atomic_write_json(out / "result.json", result)
    atomic_write_json(work / "plan.json", plan_skeleton(case_id, status))
    atomic_write_json(work / "assessment_matrix.json", matrix_skeleton(case_id, status))

    return result, issues


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

REQUIRED_ROW_FIELDS = ("dimension", "checkpoint", "verdict", "judgment", "locator", "quote")
KNOWN_VERDICTS = {
    "low_risk", "moderate_risk", "high_risk", "critical_risk",
    "adequate", "partial", "inadequate", "not_reported",
    "insufficient_evidence", "not_applicable", "definite", "possible", "absent_sign",
    "present", "absent", "unclear", "reported",
}


def validate_dir(input_dir, output_dir, work_dir):
    """校验 result.json；返回 (errors, warnings)。error 级才应阻止交付。"""
    errors = []
    warnings = []
    result_path = Path(output_dir) / "result.json"
    if not result_path.exists():
        return ["result_json_missing: %s" % result_path], []

    result, load_issues = load_json_lenient(result_path)
    if result is None:
        return ["result_json_unreadable: %s" % result_path] + load_issues, []

    if not isinstance(result, dict):
        return ["result_json_not_object"], []

    # case_id / status
    if not result.get("case_id"):
        errors.append("case_id_missing_or_empty")
    if result.get("status") not in ALLOWED_STATUS:
        errors.append("status_invalid: %r" % result.get("status"))

    # 契约字段回填检查
    case_data = None
    if Path(input_dir).is_dir():
        found, _ = discover_input(input_dir)
        if found["case_json"]:
            case_data, _ = load_json_lenient(found["case_json"][0])
    contract = find_contract(case_data) if isinstance(case_data, (dict, list)) else None
    if contract is not None:
        for key in contract_defaults(contract):
            if key not in result:
                errors.append("contract_field_missing: %s" % key)

    # 结构字段
    for key in ("sources", "uncertainty", "change_log", "errors", "degradations",
                "limitations", "pending_human", "runtime"):
        if key not in result:
            errors.append("structural_field_missing: %s" % key)
        elif key == "runtime" and not isinstance(result[key], dict):
            errors.append("runtime_not_object")

    # 主产物：assessment 行式矩阵
    assessment = result.get("assessment")
    if assessment is None:
        errors.append("assessment_missing")
    elif not isinstance(assessment, list):
        errors.append("assessment_not_list")
    else:
        for i, row in enumerate(assessment):
            if not isinstance(row, dict):
                warnings.append("assessment[%d]_not_object" % i)
                continue
            for field in REQUIRED_ROW_FIELDS:
                if field not in row:
                    errors.append("assessment[%d]_missing_field_%s" % (i, field))
            if row.get("verdict") not in KNOWN_VERDICTS:
                warnings.append("assessment[%d]_verdict_not_in_vocab: %r"
                                % (i, row.get("verdict")))
            quote = row.get("quote")
            if isinstance(quote, str) and quote and not row.get("locator"):
                warnings.append("assessment[%d]_quote_without_locator" % i)

    # 若工作区存在证据索引，抽查 quote 是否命中（警告级，不硬性报错）
    idx_path = Path(work_dir) / "evidence_index.json"
    if idx_path.exists():
        idx, _ = load_json_lenient(idx_path)
        if isinstance(idx, dict):
            anchors = _index_anchor_map(idx)
            for i, row in enumerate(assessment):
                if not isinstance(row, dict):
                    continue
                quote = row.get("quote")
                loc = row.get("locator")
                if not quote or not loc:
                    continue
                rid = row.get("id", i)
                qn = re.sub(r"\s+", " ", str(quote)).strip()
                anchor = anchors.get(str(loc))
                if anchor is None:
                    warnings.append("assessment[%s]_locator_not_in_index: %s" % (rid, loc))
                elif qn not in anchor.get("norm", ""):
                    warnings.append("assessment[%s]_quote_not_in_anchor: %s" % (rid, loc))

    return errors, warnings


def _index_anchor_map(index):
    anchors = {}
    for doc in index.get("documents", []):
        for page in doc.get("pages", []):
            for a in page.get("anchors", []):
                anchors[str(a.get("locator"))] = a
    return anchors


def _print_validation(errors, warnings):
    if errors:
        print("VALIDATE: %d error(s)" % len(errors))
        for e in errors:
            print("  [error] %s" % e)
    else:
        print("VALIDATE: ok (no errors)")
    for w in warnings:
        print("  [warn ] %s" % w)


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------

def _common_args(parser):
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--work", default=DEFAULT_WORK)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="main.py", description="litqa-plan 脚手架")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_skel = sub.add_parser("skeleton", help="立即落盘 partial 骨架 + 中间表示模板")
    _common_args(p_skel)

    p_val = sub.add_parser("validate", help="校验最终 result.json")
    _common_args(p_val)

    args = parser.parse_args(argv)

    if args.cmd == "skeleton":
        result, issues = write_skeleton(args.input, args.output, args.work)
        print("skeleton: status=%s case_id=%s errors=%d"
              % (result["status"], result["case_id"], len(issues)))
        for issue in issues:
            print("  [note ] %s" % issue)
        return 0

    if args.cmd == "validate":
        errors, warnings = validate_dir(args.input, args.output, args.work)
        _print_validation(errors, warnings)
        return 0 if not errors else 1

    parser.error("unknown command: %s" % args.cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main())
