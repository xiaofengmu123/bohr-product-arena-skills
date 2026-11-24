#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litqa-trace-mini 运行脚手架（仅标准库，确定性）。

职责：
  1. 发现 /input 下的 case.json、task.md 与材料文件（不假设固定文件名）；
  2. 从 case.json 递归定位 output_contract（任意嵌套、大小写不敏感），原样回填全部字段；
  3. 第一时间写出合法 partial 骨架 result.json，保证中途异常也不留空产物；
  4. inspect 打印输入盘点；validate 校验最终 result.json；self-test 跑三种异常场景。

用法（在 SKILL.md 所在目录下）：
  python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py inspect  --input /input
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

DEFAULT_INPUT = os.environ.get("SKILL_INPUT_DIR", "/input")
DEFAULT_OUTPUT = os.environ.get("SKILL_OUTPUT_DIR", "/app/output")
DEFAULT_WORK = os.environ.get("SKILL_WORK_DIR", "/app/work")
MAX_FILE_BYTES = 2 * 1024 * 1024
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
CONTRACT_KEY_RE = re.compile(r"^(output_?contract|output_?schema|contract)$", re.I)
REQUIRED_TOP = ["case_id", "status", "assessment", "sources", "uncertainty", "change_log"]
ASSESSMENT_KEYS = {"dimension", "checkpoint", "verdict", "judgment", "locator", "quote", "basis"}


# ---------------------------------------------------------------------------
# 基础 I/O（容错，不抛异常）
# ---------------------------------------------------------------------------

def read_text(path, max_bytes=MAX_FILE_BYTES):
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
    text, errors = read_text(path)
    if text is None:
        return None, errors
    try:
        return json.loads(text), errors
    except (json.JSONDecodeError, ValueError) as exc:
        return None, errors + ["bad_json: %s: %s" % (path, exc)]


def atomic_write_json(path, data):
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return ["mkdir_failed: %s: %s" % (p.parent, exc)]
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(str(tmp), str(p))
        return []
    except OSError as exc:
        return ["write_failed: %s: %s" % (p, exc)]


# ---------------------------------------------------------------------------
# 输入发现与契约定位
# ---------------------------------------------------------------------------

def discover_input(input_dir):
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
        elif "material" in name or p.suffix.lower() in (".pdf", ".txt", ".md", ".json", ".xml", ".csv"):
            found["materials"].append(str(p))
        else:
            found["other"].append(str(p))
    return found, []


def find_contract(obj, depth=0):
    if depth > 8 or obj is None:
        return None
    if isinstance(obj, dict):
        for key, value in obj.items():
            if CONTRACT_KEY_RE.match(str(key)):
                return value
        for value in obj.values():
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


def contract_fields(contract):
    """返回契约的顶层字段名->默认值；不改名、不删未知键。"""
    if isinstance(contract, str):
        try:
            contract = json.loads(contract)
        except Exception:
            return {}
    if isinstance(contract, dict):
        props = contract.get("properties")
        source = props if isinstance(props, dict) else contract
        return {str(k): _default_for(v) for k, v in source.items()}
    if isinstance(contract, list):
        return {str(k): None for k in contract if isinstance(k, str)}
    return {}


def extract_case_id(data, input_dir):
    if isinstance(data, dict):
        for key in ("case_id", "caseId", "id"):
            if key in data and isinstance(data[key], (str, int)):
                return str(data[key])
    return "unknown"


# ---------------------------------------------------------------------------
# 骨架与运行
# ---------------------------------------------------------------------------

def build_skeleton(case_id, contract, notes=None):
    now = time.time()
    result = {"case_id": case_id, "status": "partial"}
    result.update(contract_fields(contract))
    result.update({
        "assessment": [],
        "candidates": [],
        "screening": [],
        "evidence_records": [],
        "omissions": [],
        "sources": [],
        "text_index": {},
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "event": "skeleton",
            "detail": notes or "initial partial skeleton",
        }],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "runtime": {"start_epoch": now, "skeleton_epoch": now, "finish_epoch": None},
    })
    return result


def run_skeleton(input_dir, output_dir, work_dir):
    for d in (work_dir, output_dir):
        try:
            Path(d).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    found, errs = discover_input(input_dir)
    notes = list(errs)
    data = None
    cj = found["case_json"][0] if found["case_json"] else None
    if cj:
        data, jerrs = load_json_lenient(cj)
        notes += jerrs
    else:
        notes.append("case_json_missing")
    case_id = "unknown"
    contract = None
    if data is not None:
        case_id = extract_case_id(data, input_dir)
        contract = find_contract(data)
        if contract is None:
            notes.append("no_output_contract_found")
    has_materials = bool(found["materials"])
    if not Path(input_dir).is_dir():
        status = "input_error"
    elif cj is None and not has_materials:
        status = "insufficient_evidence"
    elif cj is None:
        status = "input_error"
    elif data is None:
        status = "input_error"
    else:
        status = "partial"
    result = build_skeleton(case_id, contract, "; ".join(notes) if notes else None)
    result["status"] = status
    result["errors"] = [n for n in notes if n.startswith(("missing:", "bad_json", "decode"))]
    result["degradations"] = [n for n in notes if not n.startswith(("missing:", "bad_json", "decode"))]
    werr = atomic_write_json(Path(output_dir) / "result.json", result)
    atomic_write_json(Path(work_dir) / "skeleton.json", result)
    return {
        "case_id": case_id,
        "status": status,
        "contract_fields": sorted(contract_fields(contract).keys()) if contract is not None else [],
        "wrote": str(Path(output_dir) / "result.json"),
        "notes": notes,
        "write_errors": werr,
    }


def cmd_skeleton(args):
    r = run_skeleton(args.input or DEFAULT_INPUT, args.output or DEFAULT_OUTPUT, args.work or DEFAULT_WORK)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


def cmd_inspect(args):
    input_dir = args.input or DEFAULT_INPUT
    found, errs = discover_input(input_dir)
    data = None
    case_id = "unknown"
    contract = None
    keys = []
    if found["case_json"]:
        data, jerrs = load_json_lenient(found["case_json"][0])
        errs += jerrs
        if data is not None:
            case_id = extract_case_id(data, input_dir)
            contract = find_contract(data)
            if contract is not None:
                keys = sorted(contract_fields(contract).keys())
    print(json.dumps({
        "input_dir": input_dir,
        "case_id": case_id,
        "files": found,
        "contract_fields": keys,
        "contract_kind": "dict" if isinstance(contract, dict) else ("list" if isinstance(contract, list) else ("str" if isinstance(contract, str) else "none")),
        "errors": errs,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_validate(args):
    input_dir = args.input or DEFAULT_INPUT
    output_dir = args.output or DEFAULT_OUTPUT
    res_path = Path(output_dir) / "result.json"
    errors = []
    warnings = []
    if not res_path.exists():
        errors.append("result_missing: %s" % res_path)
    else:
        result, jerrs = load_json_lenient(res_path)
        errors += [e for e in jerrs if e.startswith("bad_json")]
        if result is None:
            errors.append("result_not_parseable")
        else:
            for key in REQUIRED_TOP:
                if key not in result:
                    errors.append("missing_key:%s" % key)
            if result.get("status") not in ALLOWED_STATUS:
                errors.append("bad_status:%s" % result.get("status"))
            if not result.get("case_id"):
                errors.append("empty_case_id")
            found = discover_input(input_dir)[0]
            data = None
            if found.get("case_json"):
                data, _ = load_json_lenient(found["case_json"][0])
            if data is not None:
                contract = find_contract(data)
                for k in contract_fields(contract).keys():
                    if k not in result:
                        errors.append("missing_contract_field:%s" % k)
            for i, it in enumerate(result.get("assessment") or []):
                missing = [k for k in ASSESSMENT_KEYS if k not in it]
                if missing:
                    errors.append("assessment[%d]_missing:%s" % (i, ",".join(missing)))
                if not it.get("quote") and it.get("basis") not in ("not_reported", "insufficient_evidence"):
                    warnings.append("assessment[%d]_empty_quote_without_not_reported" % i)
            if (result.get("runtime") or {}).get("finish_epoch") is None:
                warnings.append("runtime.finish_epoch_unset")
    report = {"ok": not errors, "error_count": len(errors), "errors": errors, "warnings": warnings}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_self_test(args):
    import shutil
    base = Path(tempfile.mkdtemp(prefix="litqa_selftest_"))
    scenarios = []
    try:
        d1 = base / "empty_input"
        d1.mkdir()
        r1 = run_skeleton(str(d1), str(base / "out1"), str(base / "work1"))
        scenarios.append(("empty_input", r1["status"], (base / "out1" / "result.json").exists()))

        d2 = base / "badjson_input"
        d2.mkdir()
        (d2 / "case.json").write_text("{ not valid json", encoding="utf-8")
        r2 = run_skeleton(str(d2), str(base / "out2"), str(base / "work2"))
        scenarios.append(("bad_json", r2["status"], (base / "out2" / "result.json").exists()))

        r3 = run_skeleton(str(base / "no_such_dir"), str(base / "out3"), str(base / "work3"))
        scenarios.append(("missing_input", r3["status"], (base / "out3" / "result.json").exists()))
    finally:
        shutil.rmtree(base, ignore_errors=True)
    failures = []
    for name, status, exists in scenarios:
        if status not in ALLOWED_STATUS:
            failures.append("%s:bad_status=%s" % (name, status))
        if not exists:
            failures.append("%s:no_result" % name)
    print(json.dumps({
        "self_test": "PASS" if not failures else "FAIL",
        "scenarios": [{"name": n, "status": s, "result_exists": e} for n, s, e in scenarios],
        "failures": failures,
    }, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="main.py", description="litqa-trace-mini 运行脚手架")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sk = sub.add_parser("skeleton", help="立即落盘 partial 结果骨架")
    p_sk.add_argument("--input")
    p_sk.add_argument("--output")
    p_sk.add_argument("--work")

    p_in = sub.add_parser("inspect", help="盘点输入与契约字段")
    p_in.add_argument("--input")

    p_va = sub.add_parser("validate", help="校验 result.json 结构")
    p_va.add_argument("--input")
    p_va.add_argument("--output")

    sub.add_parser("self-test", help="三种异常场景自检")

    args = parser.parse_args(argv)
    if args.cmd == "skeleton":
        return cmd_skeleton(args)
    if args.cmd == "inspect":
        return cmd_inspect(args)
    if args.cmd == "validate":
        return cmd_validate(args)
    if args.cmd == "self-test":
        return cmd_self_test(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
