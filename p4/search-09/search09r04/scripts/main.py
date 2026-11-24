#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litqa-veritas 运行脚手架。

职责（确定性、只依赖标准库）：
  1. 发现 /input 下的 case.json、task.md 与材料文件；
  2. 从 case.json 中定位 output_contract（任意命名/嵌套层级，大小写不敏感）；
  3. 立即写出一份合法的 partial 结果骨架 result.json（含 case_id、
     原样回填的 output_contract 全部字段、status/sources/uncertainty/
     limitations/change_log 等），保证中途异常也不留空产物；
  4. 校验最终 result.json 的结构与证据完整性。

用法（在 SKILL.md 所在目录下运行）：
  python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py validate --input /input --output /app/output
  python3 -B scripts/main.py self-test
"""

import argparse
import json
import os
import re
import sys
import tempfile
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
CONTRACT_KEY_RE = re.compile(r"^(output_?contract|output_?schema|contract)$", re.I)
EVIDENCE_LEVELS = {"direct", "paraphrase", "inference", "absent"}
VERDICTS = {
    "low", "moderate", "high", "unclear", "insufficient_evidence",
    "present", "absent", "not_reported", "unable_to_assess",
    "low_risk", "some_concerns", "high_risk", "critical_risk",
}

# ---------------------------------------------------------------------------
# 基础 I/O（容错）
# ---------------------------------------------------------------------------


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """以 utf-8-sig 优先读取文本，坏编码/超大文件降级并返回错误列表。"""
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
    material_suffixes = (".pdf", ".txt", ".md", ".json", ".xml", ".csv", ".tsv")
    for p in entries:
        name = p.name.lower()
        if name == "case.json":
            found["case_json"].append(p)
        elif name == "task.md":
            found["task_md"].append(p)
        elif "material" in name or p.suffix.lower() in material_suffixes:
            found["materials"].append(p)
        else:
            found["other"].append(p)
    return found, []


def find_contract(obj, depth=0):
    """递归查找 output_contract（大小写不敏感，任意嵌套）。"""
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


def contract_keys(contract):
    """返回契约顶层键名列表（原样、有序）；供骨架回填与校验使用。"""
    if isinstance(contract, dict):
        props = contract.get("properties")
        source = props if isinstance(props, dict) else contract
        return [str(k) for k in source.keys()]
    if isinstance(contract, list):
        return [str(k) for k in contract if isinstance(k, str)]
    return []


def contract_defaults(contract):
    """把契约字段原样回填为顶层默认值；不改名、不删未知键。"""
    if isinstance(contract, dict):
        props = contract.get("properties")
        source = props if isinstance(props, dict) else contract
        return {str(k): _default_for(v) for k, v in source.items()}
    if isinstance(contract, list):
        return {str(k): None for k in contract if isinstance(k, str)}
    return {}


def extract_case_id(data, input_dir):
    """从 case.json 取 case_id，缺失则退回目录名。"""
    if isinstance(data, dict):
        for key in ("case_id", "caseId", "id"):
            if key in data and isinstance(data[key], (str, int)):
                return str(data[key])
    try:
        return Path(input_dir).resolve().name or "unknown"
    except OSError:
        return "unknown"


# ---------------------------------------------------------------------------
# 骨架与结果
# ---------------------------------------------------------------------------


def now_utc_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def build_skeleton(case_id, contract, notes=None):
    """构造一份合法 partial 骨架。"""
    now = time.time()
    result = {
        "case_id": case_id,
        "status": "partial",
    }
    # 原样回填契约字段（放在顶层；结构字段随后追加）
    result.update(contract_defaults(contract))
    result.update({
        "research_question": None,
        "retrieval": {
            "scope": {"design_types": [], "inclusion": [], "exclusion": []},
            "queries": [],
            "candidates": [],
            "screening": [],
            "ranked": [],
            "omissions": [],
        },
        "assessment": [],
        "grades": {"per_dimension": {}, "per_source": {}, "overall": "insufficient_evidence"},
        "sources": [],
        "uncertainty": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "change_log": [
            {"time_utc": now_utc_iso(), "event": "skeleton", "detail": "partial 骨架落盘"}
        ],
        "runtime": {"start_epoch": now, "skeleton_epoch": now, "finish_epoch": None},
    })
    if notes:
        result["errors"].extend(notes)
    return result


def skeleton_cmd(args):
    """P0：立即落盘 partial 骨架。"""
    input_dir = args.input or DEFAULT_INPUT
    output_dir = args.output or DEFAULT_OUTPUT
    work_dir = args.work or DEFAULT_WORK
    for d in (output_dir, work_dir):
        try:
            Path(d).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            # 写盘失败也要给出最小合法产物（尽量写到可写目录）
            print("warn: mkdir %s failed: %s" % (d, exc), file=sys.stderr)

    found, discover_errors = discover_input(input_dir)
    case_json_paths = found["case_json"]
    data = None
    read_errors = []
    if case_json_paths:
        data, read_errors = load_json_lenient(case_json_paths[0])

    case_id = extract_case_id(data, input_dir)
    contract = find_contract(data) if data is not None else None
    notes = discover_errors + read_errors
    if contract is None:
        notes.append("output_contract_not_found")

    result = build_skeleton(case_id, contract, notes)
    out_path = Path(output_dir) / "result.json"
    try:
        atomic_write_json(out_path, result)
    except OSError as exc:
        # 尽力写最小产物
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump({
                    "case_id": case_id,
                    "status": "input_error",
                    "errors": ["write_failed: %s" % exc],
                    "runtime": {"start_epoch": time.time(), "skeleton_epoch": None, "finish_epoch": None},
                }, fh, ensure_ascii=False, indent=2)
        except OSError:
            pass
        print("error: write result.json failed: %s" % exc, file=sys.stderr)
        return 1

    print("skeleton written: %s (case_id=%s, status=%s)"
          % (out_path, case_id, result["status"]))
    return 0


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def _list_of_dicts(value):
    return isinstance(value, list) and all(isinstance(x, dict) for x in value)


def validate_cmd(args):
    """校验最终 result.json 的结构与证据完整性；error 级问题返回非零。"""
    input_dir = args.input or DEFAULT_INPUT
    output_dir = args.output or DEFAULT_OUTPUT
    errors = []
    warnings = []

    found, discover_errors = discover_input(input_dir)
    errors.extend("input:%s" % e for e in discover_errors)

    case_json_paths = found["case_json"]
    contract = None
    contract_key_names = []
    if case_json_paths:
        data, read_errors = load_json_lenient(case_json_paths[0])
        errors.extend("case_json:%s" % e for e in read_errors)
        if data is not None:
            contract = find_contract(data)
            contract_key_names = contract_keys(contract)

    result_path = Path(output_dir) / "result.json"
    if not result_path.exists():
        print("ERROR result.json missing: %s" % result_path)
        return 2
    result, read_errors = load_json_lenient(result_path)
    errors.extend("result_json:%s" % e for e in read_errors)
    if result is None:
        print("ERROR result.json unparseable: %s" % result_path)
        return 2

    # case_id
    if not isinstance(result.get("case_id"), str) or not result["case_id"].strip():
        errors.append("result: case_id missing/empty")

    # status
    status = result.get("status")
    if status not in ALLOWED_STATUS:
        errors.append("result: status invalid: %r" % status)

    # output_contract 字段回填
    for key in contract_key_names:
        if key not in result:
            errors.append("result: contract field missing: %s" % key)

    # 结构字段
    for field in ("sources", "uncertainty", "change_log", "errors",
                  "degradations", "pending_human", "limitations"):
        if field not in result or not isinstance(result[field], list):
            errors.append("result: field missing or not list: %s" % field)

    if not isinstance(result.get("assessment"), list):
        errors.append("result: assessment missing or not list")

    # assessment 逐条校验
    required_ae = ("id", "source_id", "dimension", "checkpoint", "verdict",
                   "evidence_level", "judgment", "locator", "quote")
    if isinstance(result.get("assessment"), list):
        for i, item in enumerate(result["assessment"]):
            if not isinstance(item, dict):
                errors.append("assessment[%d]: not object" % i)
                continue
            for k in required_ae:
                if k not in item:
                    errors.append("assessment[%d]: missing key %s" % (i, k))
            lvl = item.get("evidence_level")
            if lvl is not None and lvl not in EVIDENCE_LEVELS:
                warnings.append("assessment[%d]: unknown evidence_level %r" % (i, lvl))
            if lvl == "direct" and not str(item.get("quote") or "").strip():
                warnings.append("assessment[%d]: evidence_level=direct but empty quote" % i)
            if lvl == "absent" and item.get("verdict") not in (
                    "insufficient_evidence", "unable_to_assess", "not_reported"):
                warnings.append(
                    "assessment[%d]: evidence_level=absent 建议 verdict=insufficient_evidence"
                    % i)
            if item.get("verdict") in ("insufficient_evidence", "unable_to_assess") \
                    and lvl not in (None, "absent"):
                warnings.append(
                    "assessment[%d]: verdict %r 建议 evidence_level=absent"
                    % (i, item.get("verdict")))

    # runtime
    runtime = result.get("runtime")
    if not isinstance(runtime, dict):
        errors.append("result: runtime missing or not object")

    # 输出报告
    for e in errors:
        print("ERROR " + e)
    for w in warnings:
        print("WARN  " + w)
    print("validate: %d errors, %d warnings" % (len(errors), len(warnings)))
    return 0 if not errors else 1


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------


def self_test_cmd(args):
    """三场景自检：空目录 / 坏 JSON / 缺输入。全部退出码 0。"""
    base = Path(tempfile.mkdtemp(prefix="litqa_selftest_"))
    failures = []
    try:
        # 场景 1：空输入目录
        d1_in = base / "s1_input"
        d1_out = base / "s1_output"
        d1_work = base / "s1_work"
        d1_in.mkdir(parents=True)
        rc = skeleton_cmd(argparse.Namespace(
            input=str(d1_in), output=str(d1_out), work=str(d1_work)))
        if rc != 0 or not (d1_out / "result.json").exists():
            failures.append("s1_empty_input")
        else:
            r1, _ = load_json_lenient(d1_out / "result.json")
            if r1 is None or r1.get("status") not in ALLOWED_STATUS:
                failures.append("s1_status")

        # 场景 2：坏 JSON（case.json 内容非法）
        d2_in = base / "s2_input"
        d2_out = base / "s2_output"
        d2_work = base / "s2_work"
        d2_in.mkdir(parents=True)
        (d2_in / "case.json").write_text("{ not valid json ", encoding="utf-8")
        rc = skeleton_cmd(argparse.Namespace(
            input=str(d2_in), output=str(d2_out), work=str(d2_work)))
        if rc != 0 or not (d2_out / "result.json").exists():
            failures.append("s2_bad_json")
        else:
            r2, _ = load_json_lenient(d2_out / "result.json")
            if r2 is None or r2.get("status") != "partial":
                failures.append("s2_status")

        # 场景 3：缺输入目录
        d3_in = base / "s3_input_missing"
        d3_out = base / "s3_output"
        d3_work = base / "s3_work"
        rc = skeleton_cmd(argparse.Namespace(
            input=str(d3_in), output=str(d3_out), work=str(d3_work)))
        if rc != 0 or not (d3_out / "result.json").exists():
            failures.append("s3_missing_input")
        else:
            r3, _ = load_json_lenient(d3_out / "result.json")
            if r3 is None or r3.get("status") != "partial":
                failures.append("s3_status")

        # 场景 4（附加）：带 output_contract 的合法 case.json，验证回填
        d4_in = base / "s4_input"
        d4_out = base / "s4_output"
        d4_work = base / "s4_work"
        d4_in.mkdir(parents=True)
        (d4_in / "case.json").write_text(json.dumps({
            "case_id": "demo-case",
            "output_contract": {
                "properties": {
                    "summary": {"type": "string"},
                    "items": {"type": "array"},
                }
            },
        }, ensure_ascii=False), encoding="utf-8")
        rc = skeleton_cmd(argparse.Namespace(
            input=str(d4_in), output=str(d4_out), work=str(d4_work)))
        r4, _ = load_json_lenient(d4_out / "result.json")
        if r4 is None or "summary" not in r4 or "items" not in r4 \
                or r4.get("case_id") != "demo-case":
            failures.append("s4_contract_echo")
        # 校验场景 4 最终骨架
        rc4 = validate_cmd(argparse.Namespace(input=str(d4_in), output=str(d4_out)))
        if rc4 != 0:
            failures.append("s4_validate")
    finally:
        import shutil
        shutil.rmtree(str(base), ignore_errors=True)

    if failures:
        print("self-test FAILED: %s" % ", ".join(failures))
        return 1
    print("self-test OK: empty-dir / bad-json / missing-input / contract-echo all pass")
    return 0


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def build_parser():
    p = argparse.ArgumentParser(prog="main.py", description="litqa-veritas 脚手架")
    sub = p.add_subparsers(dest="cmd", required=True)
    sk = sub.add_parser("skeleton", help="落盘 partial 结果骨架")
    sk.add_argument("--input", default=None)
    sk.add_argument("--output", default=None)
    sk.add_argument("--work", default=None)
    sk.set_defaults(func=skeleton_cmd)
    va = sub.add_parser("validate", help="校验最终 result.json")
    va.add_argument("--input", default=None)
    va.add_argument("--output", default=None)
    va.set_defaults(func=validate_cmd)
    st = sub.add_parser("self-test", help="三场景自检")
    st.set_defaults(func=self_test_cmd)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
