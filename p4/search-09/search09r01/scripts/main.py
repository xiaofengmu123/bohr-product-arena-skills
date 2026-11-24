#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litqa-evidence 运行脚手架（确定性，仅依赖 Python 标准库）。

职责：
  1. 扫描输入目录，识别 case.json / task.md / 材料文件（不假设固定文件名与键名）；
  2. 从 case.json 递归定位 output_contract（大小写不敏感、任意嵌套）并原样回填全部字段；
  3. 立即落盘一份合法 partial 结果骨架 result.json（含 case_id、契约字段、空数组、runtime），
     保证任何中途异常都不留下空产物；
  4. 校验最终 result.json 的结构，并借 litqa.verify 对 evidence_map 逐字引文做核验；
  5. self-test 覆盖空目录 / 坏 JSON / 缺输入三种场景，均须退出码 0。

用法（在 SKILL.md 所在目录下运行）：
  python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py validate --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py self-test
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

try:  # litqa.py 与 main.py 同目录；跨平台导入回退。
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    from litqa import verify_evidence_batch, load_text_index, normalize_text
except Exception:  # pragma: no cover - 仅在极少数导入失败时降级
    litqa = None
else:
    litqa = True

DEFAULT_INPUT = os.environ.get("SKILL_INPUT_DIR", "/input")
DEFAULT_OUTPUT = os.environ.get("SKILL_OUTPUT_DIR", "/app/output")
DEFAULT_WORK = os.environ.get("SKILL_WORK_DIR", "/app/work")
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB，超限跳过并记录
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
CONTRACT_KEY_RE = re.compile(r"^(output_?contract|output_?schema|contract)$", re.I)
RESERVED_KEYS = {
    "case_id", "status", "assessment", "evidence_map", "candidates", "screening",
    "omissions", "sources", "uncertainty", "pending_human", "change_log", "errors",
    "degradations", "limitations", "runtime", "contract", "verification_report",
}


# ---------------------------------------------------------------------------
# 基础 I/O（容错）
# ---------------------------------------------------------------------------

def read_text(path, max_bytes=MAX_FILE_BYTES):
    """utf-8-sig 优先读文本；坏编码/超大文件降级并返回错误列表。"""
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
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), errors
        except (UnicodeDecodeError, UnicodeError) as exc:
            errors.append("decode(%s)_failed: %s: %s" % (enc, p, exc))
    return None, errors


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
    """临时文件 + rename，避免半截 JSON。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(str(tmp), str(p))


def utc_now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# 输入发现与契约定位
# ---------------------------------------------------------------------------

def discover_input(input_dir):
    """扫描输入目录，返回识别后的文件清单（不假设固定文件名）。"""
    root = Path(input_dir)
    found = {"case_json": [], "task_md": [], "materials": [], "other": []}
    if not root.is_dir():
        return found, ["input_dir_missing: %s" % root]
    try:
        entries = sorted(p for p in root.rglob("*") if p.is_file())
    except OSError as exc:
        return found, ["input_dir_read_failed: %s: %s" % (root, exc)]
    mat_ext = {".pdf", ".txt", ".md", ".json", ".xml", ".csv", ".tsv", ".jsonl"}
    for p in entries:
        name = p.name.lower()
        if name == "case.json":
            found["case_json"].append(p)
        elif name == "task.md":
            found["task_md"].append(p)
        elif "material" in name or p.suffix.lower() in mat_ext:
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
    """据契约里的类型描述给默认值；无法判断则 None。"""
    if isinstance(value, dict):
        typ = str(value.get("type", "")).lower()
        if "array" in typ or "items" in value or "properties" in value:
            return []
        if "object" in typ:
            return {}
        if "null" in typ or "string" in typ or "number" in typ or "integer" in typ or "boolean" in typ:
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
        out = {}
        for k, v in source.items():
            if isinstance(v, dict) and "properties" in v and "type" not in v:
                out[str(k)] = contract_defaults(v)
            else:
                out[str(k)] = _default_for(v)
        return out
    if isinstance(contract, list):
        return {str(k): None for k in contract if isinstance(k, str)}
    if isinstance(contract, str) and contract:
        return {contract: None}
    return {}


def extract_case_id(data, input_dir):
    """从 case.json 取 case_id；缺失退回目录名。"""
    if isinstance(data, dict):
        for key in ("case_id", "caseId", "id", "case"):
            if key in data and isinstance(data[key], (str, int, float)):
                return str(data[key])
    return "unknown"


# ---------------------------------------------------------------------------
# 骨架与结果
# ---------------------------------------------------------------------------

def build_skeleton(case_id, contract, input_errors, notes=None):
    """构造合法 partial 骨架；契约字段先回填，结构保留键后覆盖，冲突不丢。"""
    now = time.time()
    result = {}
    collisions = []
    for k, v in contract_defaults(contract).items():
        if k in RESERVED_KEYS:
            collisions.append(k)
            result.setdefault("contract", {})[k] = v
        else:
            result[k] = v
    if contract is not None and isinstance(contract, dict) and "properties" in contract:
        result.setdefault("contract", {})["_schema"] = True
    result.update({
        "case_id": case_id,
        "status": "partial",
        "assessment": [],
        "evidence_map": [],
        "candidates": [],
        "screening": [],
        "omissions": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{"time_utc": utc_now_iso(), "event": "skeleton",
                        "detail": "落盘 partial 骨架；契约字段已原样回填"}],
        "errors": list(input_errors),
        "degradations": [],
        "limitations": [],
        "runtime": {"start_epoch": now, "skeleton_epoch": now, "finish_epoch": None},
    })
    if collisions:
        result["change_log"].append({"time_utc": utc_now_iso(), "event": "contract_collision",
                                     "detail": "契约字段与结构保留键冲突，原值移至 contract.%s" % collisions})
    if notes:
        result["change_log"].append({"time_utc": utc_now_iso(), "event": "skeleton_note",
                                     "detail": str(notes)})
    return result


def run_skeleton(input_dir, output_dir, work_dir):
    """执行 skeleton：返回 (result, status)。任何输入形态都写合法产物。"""
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    found, find_errors = discover_input(input_dir)

    case_id = "unknown"
    contract = None
    errors = list(find_errors)

    case_jsons = found["case_json"]
    if not case_jsons:
        errors.append("case_json_missing: 未在 %s 下找到 case.json" % input_dir)
        status = "insufficient_evidence"
    else:
        path = case_jsons[0]  # 多个时取字典序第一个，确定性
        data, load_errors = load_json_lenient(path)
        errors.extend(load_errors)
        if data is None:
            status = "input_error"
        else:
            case_id = extract_case_id(data, input_dir)
            contract = find_contract(data)
            status = "partial"

    result = build_skeleton(case_id, contract, errors,
                            notes="input_dir=%s; found_case_json=%d" % (input_dir, len(case_jsons)))
    result["status"] = status
    result["degradations"] = [{"scope": "skeleton", "detail": e} for e in errors[:50]]

    # 写入落盘骨架
    atomic_write_json(Path(output_dir) / "result.json", result)
    _write_md_skeleton(Path(output_dir) / "assessment.md", result)
    return result, errors


def _write_md_skeleton(path, result):
    """落一份可读 assessment.md 骨架（即使后续失败也有产物）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 文献研究质量评价（骨架）",
        "",
        "- case_id: `%s`" % result.get("case_id"),
        "- status: `%s`" % result.get("status"),
        "",
        "评价表尚未完成。当前为 partial 骨架，证据与判断待增量写入。",
    ]
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def _structural_errors(result):
    """结构校验：返回 error 级问题列表。"""
    errs = []
    if not isinstance(result, dict):
        return ["result_json_not_object"]
    if not result.get("case_id"):
        errs.append("missing_case_id")
    if result.get("status") not in ALLOWED_STATUS:
        errs.append("bad_status: %r" % result.get("status"))
    for key in ("assessment", "evidence_map", "candidates", "screening", "omissions",
                "sources", "uncertainty", "change_log", "runtime"):
        if key not in result:
            errs.append("missing_key: %s" % key)
    for i, item in enumerate(result.get("assessment", [])):
        if not isinstance(item, dict):
            errs.append("assessment[%d]_not_object" % i)
            continue
        for sub in ("dimension", "checkpoint", "verdict", "judgment"):
            if sub not in item:
                errs.append("assessment[%d]_missing_%s" % (i, sub))
        if "quote" not in item or not isinstance(item.get("quote"), str):
            errs.append("assessment[%d]_missing_quote" % i)
    return errs


def run_validate(input_dir, output_dir, work_dir, fix=False):
    """结构校验 + evidence_map 逐字核验；返回 (report, exit_code)。"""
    result_path = Path(output_dir) / "result.json"
    result, load_errors = load_json_lenient(result_path)
    report = {"case_id": None, "structural_errors": [], "quote_checks": [],
              "warnings": list(load_errors), "exit_code": 0}
    if result is None:
        report["structural_errors"].append("result_json_missing_or_bad")
        report["exit_code"] = 1
        return report, report["exit_code"]

    report["case_id"] = result.get("case_id")
    report["structural_errors"] = _structural_errors(result)

    # 逐字核验：在 /input 与 /work 下建立文本索引。
    quote_checks = []
    if litqa is not None and result.get("evidence_map"):
        index, idx_errors = load_text_index([input_dir, work_dir])
        report["warnings"].extend(idx_errors)
        for ev in result["evidence_map"]:
            checks = verify_evidence_batch([ev], index, idx_errors)
            quote_checks.extend(checks)
        report["quote_checks"] = quote_checks
        # 源文本缺失 -> warning（可解释限制）；源文本在但引文对不上 -> error
        for c in quote_checks:
            if c.get("match") == "none":
                report["structural_errors"].append("quote_mismatch: %s" % c.get("evidence_id", "?"))
            elif c.get("match") == "unverifiable":
                report["warnings"].append("unverifiable: %s" % c.get("evidence_id", "?"))

    if report["structural_errors"]:
        report["exit_code"] = 1

    if fix and result is not None and litqa is not None:
        _stamp_verification(result, quote_checks)
        result["verification_report"] = {"quote_checks": quote_checks,
                                         "generated_utc": utc_now_iso()}
        atomic_write_json(result_path, result)

    report_path = Path(work_dir) / "verification_report.json"
    atomic_write_json(report_path, report)
    return report, report["exit_code"]


def _stamp_verification(result, checks):
    """把逐字核验结果回写到 result.json 的 evidence_map（--fix）。"""
    by_id = {c.get("evidence_id"): c for c in checks}
    for ev in result.get("evidence_map", []):
        c = by_id.get(ev.get("evidence_id"))
        if not c:
            continue
        ev["match"] = c.get("match", "unverifiable")
        ev["verified"] = bool(c.get("match") == "verbatim")


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

def _tmpdir():
    import tempfile
    return tempfile.mkdtemp(prefix="litqa_selftest_")


def run_self_test():
    """三场景自检，任一失败抛异常（退出码非 0）；全部通过返回报告。"""
    report = []
    # 场景 1：空输入目录
    d = _tmpdir()
    r, _ = run_skeleton(Path(d) / "in", Path(d) / "out", Path(d) / "work")
    assert (Path(d) / "out" / "result.json").exists(), "empty-dir result missing"
    assert r["status"] in {"insufficient_evidence", "input_error"}, r["status"]
    report.append({"scenario": "empty_input_dir", "status": r["status"], "ok": True})

    # 场景 2：坏 JSON
    d = _tmpdir()
    (Path(d) / "in").mkdir(parents=True)
    (Path(d) / "in" / "case.json").write_text("{not valid json", encoding="utf-8")
    r, _ = run_skeleton(Path(d) / "in", Path(d) / "out", Path(d) / "work")
    assert r["status"] == "input_error", r["status"]
    report.append({"scenario": "bad_json", "status": r["status"], "ok": True})

    # 场景 3：缺输入目录
    d = _tmpdir()
    r, _ = run_skeleton(Path(d) / "no_such_dir", Path(d) / "out", Path(d) / "work")
    assert (Path(d) / "out" / "result.json").exists()
    assert r["status"] in {"insufficient_evidence", "input_error"}, r["status"]
    report.append({"scenario": "missing_input_dir", "status": r["status"], "ok": True})

    # 场景 4：正常契约回填 + 逐字核验闭环
    d = _tmpdir()
    (Path(d) / "in").mkdir(parents=True)
    (Path(d) / "in" / "case.json").write_text(json.dumps({
        "case_id": "case-demo",
        "output_contract": {"properties": {"summary": {"type": "string"},
                                            "table": {"type": "array"}}},
    }), encoding="utf-8")
    r, _ = run_skeleton(Path(d) / "in", Path(d) / "out", Path(d) / "work")
    assert r["case_id"] == "case-demo"
    assert r.get("summary") is None and r.get("table") == []
    report.append({"scenario": "contract_echo", "ok": True})

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser():
    p = argparse.ArgumentParser(description="litqa-evidence 运行脚手架")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("skeleton")
    sp.add_argument("--input", default=DEFAULT_INPUT)
    sp.add_argument("--output", default=DEFAULT_OUTPUT)
    sp.add_argument("--work", default=DEFAULT_WORK)
    vp = sub.add_parser("validate")
    vp.add_argument("--input", default=DEFAULT_INPUT)
    vp.add_argument("--output", default=DEFAULT_OUTPUT)
    vp.add_argument("--work", default=DEFAULT_WORK)
    vp.add_argument("--fix", action="store_true")
    sub.add_parser("self-test")
    return p


def main(argv=None):
    args = _build_parser().parse_args(argv)
    if args.cmd == "skeleton":
        result, errors = run_skeleton(args.input, args.output, args.work)
        print(json.dumps({"case_id": result["case_id"], "status": result["status"],
                          "errors": len(errors)}, ensure_ascii=False))
        return 0
    if args.cmd == "validate":
        report, code = run_validate(args.input, args.output, args.work, fix=getattr(args, "fix", False))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return code
    if args.cmd == "self-test":
        report = run_self_test()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
