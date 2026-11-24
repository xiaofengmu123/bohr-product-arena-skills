#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litqa-staged 运行脚手架（确定性、只依赖标准库）。

职责：
  1. 发现 /input 下的 case.json、task.md 与材料文件（不假设固定文件名/键名）；
  2. 从 case.json 中递归定位 output_contract（任意命名/嵌套层级，键名大小写不敏感）；
  3. **最先**写出一份合法 partial 结果骨架 result.json：含 case_id、原样回填的
     output_contract 全部字段，以及 status/sources/uncertainty/change_log 等结构字段，
     保证任何中途异常都不留下空产物（P0 早落盘）；
  4. 校验最终 result.json 的结构与证据完整性，输出 error/warning 级问题清单。

用法（在 SKILL.md 所在目录下运行，均用相对路径）：
  python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py validate --input /input --output /app/output
  python3 -B scripts/main.py --help

不依赖任何第三方包；确定性（无随机数、固定排序、固定阈值）。
"""

import argparse
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
CONTRACT_KEY_RE = re.compile(r"^(output_?contract|output_?schema|contract|schema)$", re.I)

# result.json 中由本 Skill 定义的结构字段，用于检测与契约字段的命名冲突。
RESERVED_KEYS = {
    "case_id", "status", "assessment", "grades", "retrieval", "candidates",
    "screening", "evidence_records", "omissions", "sources", "uncertainty",
    "conflicts", "pending_human", "errors", "degradations", "limitations",
    "change_log", "budget", "runtime", "research_question",
}

# validate 要求 result.json 至少具备的结构字段。
REQUIRED_RESULT_KEYS = {
    "case_id", "status", "assessment", "sources", "uncertainty",
    "pending_human", "change_log", "runtime",
}

# assessment 中每条评价必须包含的字段（缺 quote/locator 不算 error，弃权允许空）。
REQUIRED_ASSESSMENT_KEYS = {
    "dimension", "checkpoint", "verdict", "judgment", "locator", "quote", "basis",
}


# ---------------------------------------------------------------------------
# 基础 I/O（容错）
# ---------------------------------------------------------------------------


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """以 utf-8-sig 优先读取文本；坏编码/超大文件降级并返回错误列表，绝不抛异常。"""
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
    """读 JSON；坏 JSON 返回 (None, errors)，绝不抛异常。"""
    text, errors = read_text(path)
    if text is None:
        return None, errors
    try:
        return json.loads(text), errors
    except json.JSONDecodeError as exc:
        return None, errors + ["bad_json: %s: %s" % (path, exc)]


def atomic_write_json(path, data):
    """先写临时文件再 replace，避免留下半截 JSON。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(str(tmp), str(p))


def atomic_write_text(path, text):
    """写文本文件（原子替换）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(str(tmp), str(p))


# ---------------------------------------------------------------------------
# 输入发现与契约定位
# ---------------------------------------------------------------------------


def discover_input(input_dir):
    """扫描输入目录，返回可识别文件清单；不假设固定文件名。"""
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
        elif "material" in name or p.suffix.lower() in (
            ".pdf", ".txt", ".md", ".json", ".xml", ".csv", ".tsv", ".jsonl",
        ):
            found["materials"].append(p)
        else:
            found["other"].append(p)
    return found, []


def find_contract(obj, path="", depth=0):
    """递归查找 output_contract（大小写不敏感，任意嵌套）。"""
    if depth > 6:
        return None, path
    if isinstance(obj, dict):
        for key, value in obj.items():
            if CONTRACT_KEY_RE.match(str(key)):
                return value, (path + "/" + str(key))
            hit, hit_path = find_contract(value, path + "/" + str(key), depth + 1)
            if hit is not None:
                return hit, hit_path
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            hit, hit_path = find_contract(value, path + "[%d]" % idx, depth + 1)
            if hit is not None:
                return hit, hit_path
    return None, path


def _default_for(value):
    """根据契约里的类型描述给出默认值；无法判断则 None。"""
    if isinstance(value, dict):
        typ = str(value.get("type", "")).lower()
        if "array" in typ or "list" in typ:
            return []
        if "object" in typ or "dict" in typ or "map" in typ:
            return {}
        if "null" in typ:
            return None
        if "number" in typ or "integer" in typ:
            return None
        if "boolean" in typ or "bool" in typ:
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
        return {str(k): _default_for(v) for k, v in source.items()}
    if isinstance(contract, list):
        out = {}
        for item in contract:
            if isinstance(item, str):
                out[str(item)] = None
            elif isinstance(item, dict) and "name" in item:
                out[str(item["name"])] = _default_for(item)
        return out
    return {}


def extract_case_id(data, input_dir):
    """从 case.json 取 case_id，缺失则退回目录名。"""
    if isinstance(data, dict):
        for key in ("case_id", "caseId", "caseID", "id"):
            if key in data and isinstance(data[key], (str, int)):
                return str(data[key])
    try:
        return Path(input_dir).resolve().name or "unknown"
    except OSError:
        return "unknown"


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# 骨架与结果
# ---------------------------------------------------------------------------


def build_skeleton(case_id, contract, contract_path=None, notes=None):
    """构造一份合法 partial 骨架（P0 早落盘目标）。"""
    now = time.time()
    result = {}
    change_log = []

    # 1) 原样回填契约字段（放顶层，保持键名不变；冲突另行记录）。
    contract_fields = contract_defaults(contract)
    for key, value in contract_fields.items():
        result[key] = value
        if key.lower() in RESERVED_KEYS or key in RESERVED_KEYS:
            change_log.append({
                "time_utc": utc_now(),
                "event": "contract_key_collision",
                "detail": "契约字段 %r 与本 Skill 结构字段同名，结构字段将覆盖；契约值已保留在 sources.note" % key,
            })

    # 2) 结构字段。
    result.update({
        "case_id": case_id,
        "status": "partial",
        "research_question": {"stated": None, "scope": None, "note": "P0 骨架：待 P1 从 task.md 识别"},
        "retrieval": {
            "scope": {},
            "query_log": [],
            "reference_graph": {"nodes": [], "edges": [], "note": "仅基于本地材料构造，不做 live 检索"},
        },
        "assessment": [],
        "grades": {"summary": {}, "rules": "见 references/rubric.md 分级阈值"},
        "sources": [],
        "uncertainty": [],
        "conflicts": [],
        "pending_human": [],
        "evidence_records": [],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "change_log": change_log,
        "budget": {
            "time_budget_s": 900,
            "request_budget": 64,
            "elapsed_s": None,
            "requests_used": None,
            "phase": "P0",
            "checkpoints": [],
        },
        "runtime": {
            "start_epoch": now,
            "skeleton_epoch": now,
            "finish_epoch": None,
        },
    })
    result["change_log"].append({
        "time_utc": utc_now(),
        "event": "skeleton_written",
        "detail": "P0 早落盘 partial 骨架；契约字段 %d 项，contract_path=%s" % (
            len(contract_fields), contract_path or "none"),
    })
    if notes:
        for n in notes:
            result["limitations"].append({"type": "skeleton_note", "detail": n})
    return result


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def _fmt_problem(level, code, msg):
    return {"level": level, "code": code, "message": msg}


def validate_result(result, input_dir, output_dir):
    """对 result.json 做结构校验，返回 (problems, summary)。"""
    problems = []
    summary = {"error": 0, "warning": 0, "info": 0}

    if not isinstance(result, dict):
        problems.append(_fmt_problem("error", "not_object", "result.json 顶层必须是对象"))
        summary["error"] += 1
        return problems, summary

    # case_id
    cid = result.get("case_id")
    if not cid or not isinstance(cid, str) or not cid.strip():
        problems.append(_fmt_problem("error", "missing_case_id", "缺少非空 case_id"))
        summary["error"] += 1

    # status
    status = result.get("status")
    if status not in ALLOWED_STATUS:
        problems.append(_fmt_problem("error", "bad_status",
                                     "status=%r 不在 %s" % (status, sorted(ALLOWED_STATUS))))
        summary["error"] += 1

    # 必需结构字段
    missing = [k for k in REQUIRED_RESULT_KEYS if k not in result]
    if missing:
        problems.append(_fmt_problem("error", "missing_keys",
                                     "缺少必需结构字段: %s" % ", ".join(sorted(missing))))
        summary["error"] += len(missing)
    else:
        # 结构字段类型粗检
        for key in ("assessment", "sources", "uncertainty", "change_log"):
            if not isinstance(result.get(key), list):
                problems.append(_fmt_problem("error", "bad_type_%s" % key,
                                             "%s 必须是列表" % key))
                summary["error"] += 1

    # assessment 逐条字段
    assessment = result.get("assessment", [])
    if isinstance(assessment, list):
        for i, item in enumerate(assessment):
            if not isinstance(item, dict):
                problems.append(_fmt_problem("error", "bad_assessment_item",
                                             "assessment[%d] 不是对象" % i))
                summary["error"] += 1
                continue
            miss = [k for k in REQUIRED_ASSESSMENT_KEYS if k not in item]
            if miss:
                problems.append(_fmt_problem("error", "assessment_missing_field",
                                             "assessment[%d] 缺字段: %s" % (i, ", ".join(miss))))
                summary["error"] += 1
            # 有 quote 无 locator（或反之）时按 warning 提示，弃权允许两者都空
            q = item.get("quote", "").strip() if isinstance(item.get("quote"), str) else ""
            lo = item.get("locator", "").strip() if isinstance(item.get("locator"), str) else ""
            if bool(q) != bool(lo):
                problems.append(_fmt_problem("warning", "quote_locator_pair",
                                             "assessment[%d] quote/locator 应成对出现（弃权应都为空）" % i))
                summary["warning"] += 1

    # status 与内容自洽
    if status == "ok":
        if not isinstance(assessment, list) or len(assessment) == 0:
            problems.append(_fmt_problem("error", "ok_without_assessment",
                                         "status=ok 但 assessment 为空"))
            summary["error"] += 1
    if status in ("ok", "partial") and not isinstance(result.get("sources"), list):
        problems.append(_fmt_problem("warning", "no_sources", "sources 缺失或非列表"))
        summary["warning"] += 1

    # change_log 至少一条
    if not isinstance(result.get("change_log"), list) or len(result["change_log"]) == 0:
        problems.append(_fmt_problem("warning", "empty_change_log", "change_log 为空"))
        summary["warning"] += 1

    # budget / runtime 存在性
    for key in ("budget", "runtime"):
        if key not in result:
            problems.append(_fmt_problem("warning", "missing_%s" % key, "缺少 %s 字段" % key))
            summary["warning"] += 1

    return problems, summary


# ---------------------------------------------------------------------------
# 命令：skeleton
# ---------------------------------------------------------------------------


def cmd_skeleton(args):
    found, errs = discover_input(args.input)
    degradations = list(errs)

    case_json = found["case_json"][0] if found["case_json"] else None
    task_md = found["task_md"][0] if found["task_md"] else None

    data, jerrs = (load_json_lenient(case_json) if case_json else (None, []))
    degradations.extend(jerrs)

    contract = None
    contract_path = None
    if isinstance(data, dict):
        contract, contract_path = find_contract(data)

    case_id = extract_case_id(data, args.input)

    notes = []
    if not case_json:
        notes.append("未发现 case.json；case_id 回退为目录名，契约字段无法回填")
        degradations.append("no_case_json")
    if task_md is None:
        notes.append("未发现 task.md")
        degradations.append("no_task_md")
    if not found["materials"]:
        notes.append("未发现材料文件，检索与评价将以 insufficient_evidence 收尾")
    if contract is None:
        notes.append("case.json 中未发现 output_contract，按无契约字段处理")

    skeleton = build_skeleton(case_id, contract, contract_path, notes)
    for d in degradations:
        if not any(x.get("detail") == d for x in skeleton["degradations"]):
            skeleton["degradations"].append({"type": "input", "detail": d})

    out_json = Path(args.output) / "result.json"
    atomic_write_json(out_json, skeleton)

    # 同时写一份最小可读文本件（早落盘，便于 inspect）
    if args.work:
        work_dir = Path(args.work)
        work_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(work_dir / "discovered_input.json",
                          json.dumps({"case_json": [str(p) for p in found["case_json"]],
                                      "task_md": [str(p) for p in found["task_md"]],
                                      "materials": [str(p) for p in found["materials"]],
                                      "other": [str(p) for p in found["other"]],
                                      "contract_path": contract_path,
                                      "degradations": degradations},
                                     ensure_ascii=False, indent=2))

    print("skeleton written: %s" % out_json)
    print("status=%s case_id=%s contract_fields=%d materials=%d" % (
        skeleton["status"], case_id, len(contract_defaults(contract)), len(found["materials"])))
    return 0


# ---------------------------------------------------------------------------
# 命令：validate
# ---------------------------------------------------------------------------


def cmd_validate(args):
    out_json = Path(args.output) / "result.json"
    data, errs = load_json_lenient(out_json)
    if data is None:
        print("FATAL: 无法读取 result.json: %s" % "; ".join(errs))
        return 2

    problems, summary = validate_result(data, args.input, args.output)
    for p in problems:
        print("[%s] %s: %s" % (p["level"], p["code"], p["message"]))

    print("validate summary: error=%d warning=%d info=%d" % (
        summary["error"], summary["warning"], summary["info"]))
    # 有 error 级问题返回 1；仅 warning 返回 0。
    return 1 if summary["error"] else 0


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="litqa-staged 脚手架：早落盘骨架 + 结构校验（确定性、标准库）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_s = sub.add_parser("skeleton", help="立即写 partial 结果骨架")
    p_s.add_argument("--input", default=DEFAULT_INPUT)
    p_s.add_argument("--output", default=DEFAULT_OUTPUT)
    p_s.add_argument("--work", default=DEFAULT_WORK)
    p_s.set_defaults(func=cmd_skeleton)

    p_v = sub.add_parser("validate", help="校验最终 result.json 结构")
    p_v.add_argument("--input", default=DEFAULT_INPUT)
    p_v.add_argument("--output", default=DEFAULT_OUTPUT)
    p_v.set_defaults(func=cmd_validate)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
