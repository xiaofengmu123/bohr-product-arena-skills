#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py — LaTeX 写作 Skill 的契约引导、增量回填与自检。

设计取向：契约先行（contract-first）。bootstrap 立即把 output_contract 的
全部字段以 null 写入 /app/output/result.json，并生成 contract_report 填表；
contract-set 让 Agent 逐字段回填真实值并自动追加 change_log；finalize /
selfcheck 校验契约完整性与 JSON 合法性。

仅依赖 Python 标准库。bootstrap/discover/contract-set/status/finalize 对
非致命失败一律退出码 0（写 degraded 结果）；selfcheck 发现问题时退出码 1，
以便调用方据此决定是否继续。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB
TEXT_EXTS = {
    ".md", ".tex", ".bib", ".txt", ".csv", ".json", ".yaml", ".yml",
    ".rst", ".cls", ".sty", ".log", ".aux", ".bbl", ".cfg", ".lst",
}
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
CASE_ID_KEYS = ("case_id", "id", "challenge_id", "benchmarkId", "armiId")
CONTRACT_KEYS = ("output_contract", "outputContract", "contract", "output_contract_fields")
# 元数据键：不与契约字段混淆，finalize/selfcheck 保证它们始终合法。
RESERVED = {
    "case_id", "status", "artifacts", "sources", "uncertainty",
    "pending_human", "change_log", "errors", "degradations", "limitations",
    "contract_report", "runtime",
}


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reconfigure_stdio():
    """确保 stdout/stderr 为 UTF-8，避免非 UTF-8 终端下 json 输出崩溃。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def read_text(path):
    """Read UTF-8 text tolerating BOM/encoding issues. Returns (text, error)."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, "read_error: %s" % exc
    if len(raw) > MAX_FILE_BYTES:
        return None, "skipped: exceeds 2MiB"
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "decoded_with_replacement"


def read_json(path):
    """Tolerant JSON read. Returns (obj, errors_list)."""
    text, err = read_text(path)
    if text is None:
        return None, [err or "unreadable"]
    try:
        return json.loads(text), []
    except json.JSONDecodeError as exc:
        cut = text[:exc.pos]
        try:
            return json.loads(cut), ["json_truncated_at_%d" % exc.pos]
        except Exception:
            return None, ["bad_json: %s" % exc]


def write_json(path, obj):
    """Atomic JSON write: dump to temp file then rename."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def discover_input(root):
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            ext = os.path.splitext(name)[1].lower()
            files.append({
                "path": full,
                "rel": os.path.relpath(full, root).replace("\\", "/"),
                "size": size,
                "ext": ext,
                "is_text": ext in TEXT_EXTS,
            })
    return files


def find_case_id(obj):
    if not isinstance(obj, dict):
        return None
    for key in CASE_ID_KEYS:
        val = obj.get(key)
        if val not in (None, "", [], {}):
            return str(val)
    return None


def contract_key_of(raw):
    """Extract a field name from an output_contract list item dict."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        for k in ("name", "key", "field", "id"):
            if k in raw and raw[k] not in (None, "", [], {}):
                return str(raw[k])
        # dict with exactly one entry: that entry is the field name
        if len(raw) == 1:
            return str(next(iter(raw)))
    return None


def parse_contract(obj):
    """Return ordered [(key, meta)] from output_contract in any common shape."""
    if not isinstance(obj, dict):
        return []
    contract = obj.get("output_contract", obj.get("outputContract", obj.get("contract")))
    result = []
    if isinstance(contract, dict):
        for key, spec in contract.items():
            if key in (None, ""):
                continue
            meta = {"spec": spec}
            if isinstance(spec, dict):
                meta.update({"type_hint": spec.get("type"), "default": spec.get("default")})
            result.append((str(key), meta))
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                result.append((item, {"spec": None}))
            elif isinstance(item, dict):
                key = contract_key_of(item)
                if key:
                    meta = {"spec": item}
                    meta.update({"type_hint": item.get("type"), "default": item.get("default")})
                    result.append((key, meta))
    return result


def build_skeleton(root):
    """Build result.json skeleton. Returns (skeleton, keys)."""
    case_path = os.path.join(root, "case.json")
    notes = []
    case_id = None
    keys = []
    if os.path.isfile(case_path):
        obj, errs = read_json(case_path)
        notes.extend(errs)
        case_id = find_case_id(obj)
        keys = parse_contract(obj)
        if not keys:
            notes.append("output_contract_not_found: metadata-only result")
    else:
        notes.append("case.json_not_found_in:" + root)

    if case_id is None:
        case_id = "unknown"
        notes.append("case_id_unknown: used 'unknown'")

    skeleton = {"case_id": case_id}
    contract_report = {}
    for key, meta in keys:
        skeleton[key] = None
        contract_report[key] = {
            "state": "gap", "set_at": None, "source": None,
            "type_hint": meta.get("type_hint"), "default": meta.get("default"),
        }
    skeleton.update({
        "status": "partial" if keys else "input_error",
        "artifacts": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "time": utcnow(),
            "step": "bootstrap",
            "note": "contract skeleton written before analysis; all contract fields default to null",
        }],
        "errors": notes,
        "degradations": [],
        "limitations": [],
        "contract_report": contract_report,
        "runtime": {"started_at": utcnow(), "producer": "main.py"},
    })
    return skeleton, keys


def cmd_bootstrap(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    skeleton, keys = build_skeleton(args.input)
    write_json(os.path.join(args.output, "result.json"), skeleton)
    inventory = {
        "input_root": args.input,
        "contract_keys": [k for k, _ in keys],
        "files": discover_input(args.input),
        "generated_at": utcnow(),
    }
    write_json(os.path.join(args.work, "inventory.json"), inventory)
    print(json.dumps({
        "case_id": skeleton["case_id"],
        "status": skeleton["status"],
        "contract_keys": [k for k, _ in keys],
        "file_count": len(inventory["files"]),
        "result_path": os.path.join(args.output, "result.json"),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_discover(args):
    files = discover_input(args.input)
    print(json.dumps({"input_root": args.input, "files": files},
                     ensure_ascii=False, indent=2))
    return 0


def _load_result(result_path):
    if not os.path.isfile(result_path):
        return None, ["result.json_not_found:" + result_path]
    obj, errs = read_json(result_path)
    if obj is None:
        return None, errs
    return obj, errs


def _load_contract_keys(input_root, work):
    """Re-derive contract keys from case.json; fallback to inventory.json."""
    keys = []
    case_path = os.path.join(input_root, "case.json")
    if os.path.isfile(case_path):
        obj, _ = read_json(case_path)
        if isinstance(obj, dict):
            keys = parse_contract(obj)
    if not keys:
        inv, _ = read_json(os.path.join(work, "inventory.json"))
        if isinstance(inv, dict):
            keys = [(k, {}) for k in inv.get("contract_keys", []) if k]
    return keys


def cmd_contract_set(args):
    result_path = os.path.join(args.output, "result.json")
    result, errs = _load_result(result_path)
    if result is None:
        # 无骨架时先构造最小结构再回填，保证进程不崩。
        result = {"case_id": "unknown", "contract_report": {},
                  "change_log": [], "sources": [], "uncertainty": [],
                  "pending_human": [], "errors": errs}
    if not isinstance(result, dict):
        result = {"errors": ["result.json not an object"]}

    value = args.value
    if args.value is not None:
        try:
            value = json.loads(args.value)
        except json.JSONDecodeError:
            pass  # 保留为字符串

    result[args.key] = value
    report = result.setdefault("contract_report", {})
    if isinstance(report, dict):
        report[args.key] = {
            "state": "gap" if value is None else "filled",
            "set_at": utcnow(),
            "source": args.source,
            "value_summary": summary_of(value),
        }
    result.setdefault("change_log", [])
    if isinstance(result.get("change_log"), list):
        result["change_log"].append({
            "time": utcnow(),
            "step": "contract-set",
            "key": args.key,
            "value_summary": summary_of(value),
            "source": args.source,
            "note": args.note or "",
        })
    write_json(result_path, result)
    print(json.dumps({"key": args.key, "state": "gap" if value is None else "filled",
                      "result_path": result_path}, ensure_ascii=False, indent=2))
    return 0


def summary_of(value):
    if value is None:
        return "<null>"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)[:200]
    return str(value)[:200]


def cmd_status(args):
    result, errs = _load_result(os.path.join(args.output, "result.json"))
    if result is None:
        print(json.dumps({"status": "input_error", "errors": errs}, ensure_ascii=False, indent=2))
        return 0
    keys = _load_contract_keys(args.input, args.work)
    report = result.get("contract_report", {})
    lines = []
    for key, _ in keys:
        state = report.get(key, {}).get("state") if isinstance(report, dict) else None
        lines.append({"key": key, "state": state,
                      "has_value": result.get(key) is not None})
    print(json.dumps({"case_id": result.get("case_id"), "status": result.get("status"),
                      "contract_keys": lines,
                      "change_log_count": len(result.get("change_log", [])),
                      "errors": errs}, ensure_ascii=False, indent=2))
    return 0


def cmd_finalize(args):
    result_path = os.path.join(args.output, "result.json")
    result, errs = _load_result(result_path)
    if result is None:
        result = {"case_id": "unknown", "errors": errs}
    keys = _load_contract_keys(args.input, args.work)
    # 补齐契约键（值为 null），保证"不得省略"。
    report = result.setdefault("contract_report", {})
    for key, _ in keys:
        if key not in result:
            result[key] = None
        if isinstance(report, dict) and key not in report:
            report[key] = {"state": "gap", "set_at": None, "source": None}
    # 补齐保留元数据键。
    for k, default in {
        "sources": [], "uncertainty": [], "pending_human": [], "change_log": [],
        "artifacts": [], "errors": [], "degradations": [], "limitations": [],
    }.items():
        result.setdefault(k, default)
    if result.get("status") not in ALLOWED_STATUS:
        result["status"] = "partial"
    runtime = result.setdefault("runtime", {})
    if isinstance(runtime, dict):
        runtime["ended_at"] = utcnow()
    result["change_log"].append({
        "time": utcnow(), "step": "finalize",
        "note": "contract keys ensured present; ended_at recorded",
    })
    write_json(result_path, result)
    # 打印 finalize 摘要，便于调用方确认。
    filled = sum(1 for _, m in report.items()
                 if isinstance(m, dict) and m.get("state") == "filled")
    print(json.dumps({"case_id": result.get("case_id"),
                      "status": result.get("status"),
                      "contract_total": len(keys), "contract_filled": filled,
                      "result_path": result_path}, ensure_ascii=False, indent=2))
    return 0


def cmd_selfcheck(args):
    result_path = os.path.join(args.output, "result.json")
    problems = []
    result, errs = _load_result(result_path)
    if result is None:
        problems.append("result.json_unreadable_or_invalid_json")
        problems.extend(errs)
        print(json.dumps({"ok": False, "problems": problems}, ensure_ascii=False, indent=2))
        return 1
    if not isinstance(result, dict):
        problems.append("result.json_not_object")
    else:
        keys = _load_contract_keys(args.input, args.work)
        for key, _ in keys:
            if key not in result:
                problems.append("missing_contract_key:%s" % key)
        for rk in ("case_id", "status", "sources", "uncertainty",
                   "pending_human", "change_log"):
            if rk not in result:
                problems.append("missing_metadata_key:%s" % rk)
        if result.get("status") not in ALLOWED_STATUS:
            problems.append("invalid_status:%s" % result.get("status"))
        if not isinstance(result.get("change_log"), list) or not result.get("change_log"):
            problems.append("empty_change_log")
        # 契约字段有值但 contract_report 未标记 filled 时提示，防止绕过 contract-set。
        report = result.get("contract_report")
        if isinstance(report, dict):
            for key, _ in keys:
                has_value = result.get(key) is not None
                state = report.get(key, {}).get("state") if isinstance(report.get(key), dict) else None
                if has_value and state != "filled":
                    problems.append("contract_key_filled_without_report:%s" % key)
    ok = not problems
    print(json.dumps({"ok": ok, "case_id": result.get("case_id"),
                      "status": result.get("status"),
                      "problems": problems, "notes": errs},
                     ensure_ascii=False, indent=2))
    return 0 if ok else 1


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="LaTeX 写作 Skill：契约引导、增量回填与自检（仅标准库）")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_io(sp):
        sp.add_argument("--input", default="/input")
        sp.add_argument("--output", default="/app/output")
        sp.add_argument("--work", default="/app/work")

    add_io(sub.add_parser("bootstrap", help="扫描输入并写入 result.json 契约骨架"))
    sp = sub.add_parser("discover", help="扫描 /input 并列出文件")
    sp.add_argument("--input", default="/input")

    sp = sub.add_parser("contract-set", help="回填一个契约字段并追加 change_log")
    sp.add_argument("--key", required=True)
    sp.add_argument("--value", default=None)
    sp.add_argument("--source", default=None)
    sp.add_argument("--note", default=None)
    sp.add_argument("--output", default="/app/output")
    sp.add_argument("--work", default="/app/work")

    add_io(sub.add_parser("status", help="打印契约字段填表状态"))
    add_io(sub.add_parser("finalize", help="补齐契约键与元数据并记录收尾时间"))
    add_io(sub.add_parser("selfcheck", help="校验 result.json 契约完整性（失败退出码 1）"))
    return p


def main(argv=None):
    reconfigure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "bootstrap": cmd_bootstrap,
        "discover": cmd_discover,
        "contract-set": cmd_contract_set,
        "status": cmd_status,
        "finalize": cmd_finalize,
        "selfcheck": cmd_selfcheck,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
