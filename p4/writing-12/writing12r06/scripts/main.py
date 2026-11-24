#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — LaTeX 写作 Skill 的引导、发现、归约与自检。

职责：
  1. 扫描 /input，发现 case.json / task.md 与材料文件；
  2. 抽取 case_id 与 output_contract 字段清单；
  3. 立即写入一版合法的 /app/output/result.json 骨架（先落盘，再增量改进）；
  4. finalize 由中间表示（plan/fact_pool/compile_report）归约出终版 result.json；
  5. selfcheck 校验 result.json 满足输出契约。

仅依赖 Python 标准库。bootstrap/discover/finalize 对非致命失败一律退出码 0；
selfcheck 在发现问题时退出码 1，以便调用方据此决定是否继续。
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
    ".rst", ".cls", ".sty", ".log", ".aux", ".bbl", ".cfg", ".lst", ".fls",
}
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
CASE_ID_KEYS = ("case_id", "id", "challenge_id", "benchmarkId", "armiId")


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reconfigure_stdio() -> None:
    """确保 stdout/stderr 为 UTF-8，避免非 UTF-8 终端下 json 输出崩溃。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def read_text(path: str):
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


def read_json(path: str):
    """Tolerant JSON read. Returns (obj, errors_list)."""
    text, err = read_text(path)
    if text is None:
        return None, [err or "unreadable"]
    try:
        return json.loads(text), []
    except json.JSONDecodeError as exc:
        cut = text[: exc.pos]
        try:
            return json.loads(cut), ["json_truncated_at_%d" % exc.pos]
        except Exception:
            return None, ["bad_json: %s" % exc]


def write_json(path: str, obj) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    return path


def discover_input(root: str):
    files = []
    if not os.path.isdir(root):
        return files
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


def contract_keys(obj):
    """Return output_contract keys to echo back verbatim, preserving order."""
    if not isinstance(obj, dict):
        return []
    contract = obj.get("output_contract")
    keys = []
    if isinstance(contract, dict):
        keys = list(contract.keys())
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                keys.extend(list(item.keys()))
    return keys


def _read_case(root: str):
    """Return (case_obj, case_id, contract_keys, notes)."""
    case_path = os.path.join(root, "case.json")
    notes = []
    case_id = None
    keys = []
    case_obj = None
    if os.path.isfile(case_path):
        case_obj, errs = read_json(case_path)
        notes.extend(errs)
        case_id = find_case_id(case_obj)
        keys = contract_keys(case_obj)
        if not keys:
            notes.append("output_contract_not_found: metadata-only result")
    else:
        notes.append("case.json_not_found_in:" + root)

    if case_id is None:
        case_id = "unknown"
        notes.append("case_id_unknown: used 'unknown'")
    return case_obj, case_id, keys, notes


def build_skeleton(root: str):
    """Build result.json skeleton from /input/case.json. Returns (skeleton, keys)."""
    _, case_id, keys, notes = _read_case(root)
    skeleton = {"case_id": case_id}
    for key in keys:
        skeleton[key] = None
    skeleton.update({
        "status": "partial" if keys else "input_error",
        "artifacts": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "time": utcnow(),
            "step": "bootstrap",
            "note": "result skeleton written before analysis; contract fields default to null",
        }],
        "errors": notes,
        "degradations": [],
        "runtime": {"started_at": utcnow(), "producer": "main.py", "ir_status": "bootstrap"},
    })
    return skeleton, keys


def cmd_bootstrap(args) -> int:
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    skeleton, keys = build_skeleton(args.input)
    write_json(os.path.join(args.output, "result.json"), skeleton)
    inventory = {
        "input_root": args.input,
        "contract_keys": keys,
        "files": discover_input(args.input),
        "generated_at": utcnow(),
    }
    write_json(os.path.join(args.work, "inventory.json"), inventory)
    print(json.dumps({
        "case_id": skeleton["case_id"],
        "status": skeleton["status"],
        "contract_keys": keys,
        "file_count": len(inventory["files"]),
        "result_path": os.path.join(args.output, "result.json"),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_discover(args) -> int:
    files = discover_input(args.input)
    print(json.dumps({"input_root": args.input, "files": files},
                     ensure_ascii=False, indent=2))
    return 0


def _load_work_json(path, default):
    obj, _ = read_json(path)
    return obj if isinstance(obj, dict) else default


def _compute_status(result, keys) -> str:
    """Honest status derived from contract completion and errors."""
    notes = result.get("errors", [])
    if any(n.startswith("bad_json") or n.startswith("case.json_not_found") for n in notes):
        # input unparseable dominates, but only if nothing was produced
        filled = sum(1 for k in keys if result.get(k) not in (None, "", []))
        if filled == 0:
            return "input_error"
    filled = sum(1 for k in keys if result.get(k) not in (None, "", []))
    total = len(keys)
    if not keys:
        return result.get("status", "input_error")
    if filled == total and not result.get("degradations"):
        return "ok"
    if filled == 0:
        return "insufficient_evidence" if not notes else "input_error"
    return "partial"


def cmd_finalize(args) -> int:
    os.makedirs(args.output, exist_ok=True)
    result_path = os.path.join(args.output, "result.json")
    result, errs = read_json(result_path)
    if not isinstance(result, dict):
        _, case_id, keys, notes = _read_case(args.input)
        result, _ = build_skeleton(args.input)
        errs.extend(notes)
    keys = [k for k in result.keys()
            if k not in ("case_id", "status", "artifacts", "sources", "uncertainty",
                         "pending_human", "change_log", "errors", "degradations", "runtime")]
    # Ensure contract keys present even if skeleton was written from stale contract.
    _, case_id, contract_keys, notes = _read_case(args.input)
    for k in contract_keys:
        result.setdefault(k, None)
    keys = contract_keys or keys

    result.setdefault("case_id", case_id)
    result.setdefault("artifacts", [])
    result.setdefault("sources", [])
    result.setdefault("uncertainty", [])
    result.setdefault("pending_human", [])
    result.setdefault("change_log", [])
    result.setdefault("errors", [])
    result.setdefault("degradations", [])
    result.setdefault("runtime", {})

    # Merge intermediate representations if present (reduce already did the heavy lift;
    # finalize is idempotent and only re-derives status + change_log marker).
    result["errors"] = _dedupe(list(result.get("errors", [])) + notes)
    result["status"] = _compute_status(result, keys)
    result["change_log"] = list(result.get("change_log", [])) + [{
        "time": utcnow(),
        "step": "finalize",
        "note": "contract keys re-checked: %d/%d filled; status=%s" % (
            sum(1 for k in keys if result.get(k) not in (None, "", [])),
            len(keys), result["status"]),
    }]
    result["runtime"]["finalized_at"] = utcnow()
    write_json(result_path, result)
    print(json.dumps({
        "status": result["status"],
        "filled_contract_keys": sum(1 for k in keys if result.get(k) not in (None, "", [])),
        "total_contract_keys": len(keys),
        "result_path": result_path,
    }, ensure_ascii=False, indent=2))
    return 0


def _dedupe(items):
    seen = set()
    out = []
    for it in items:
        key = json.dumps(it, ensure_ascii=False, sort_keys=True) if not isinstance(it, str) else it
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def _check_sources(sources):
    problems = []
    if not isinstance(sources, list):
        return ["sources is not a list"]
    for i, s in enumerate(sources):
        if not isinstance(s, dict):
            problems.append("sources[%d] not an object" % i)
            continue
        if not s.get("locator"):
            problems.append("sources[%d] missing locator" % i)
        if not s.get("quote"):
            problems.append("sources[%d] missing quote" % i)
    return problems


def cmd_selfcheck(args) -> int:
    result_path = os.path.join(args.output, "result.json")
    problems = []
    result, errs = read_json(result_path)
    if not isinstance(result, dict):
        problems.append("result.json not valid JSON object: %s" % ";".join(errs))
    else:
        _, case_id, contract_keys, notes = _read_case(args.input)
        if not result.get("case_id"):
            problems.append("missing case_id")
        if result.get("status") not in ALLOWED_STATUS:
            problems.append("invalid status: %r" % result.get("status"))
        for key in contract_keys:
            if key not in result:
                problems.append("contract key missing: %s" % key)
        for field in ("sources", "uncertainty", "change_log"):
            if field not in result:
                problems.append("missing field: %s" % field)
        problems.extend(_check_sources(result.get("sources", [])))

    # Validate IR consistency when present.
    ir_problems = []
    for name in ("plan.json", "fact_pool.json", "compile_report.json"):
        p = os.path.join(args.work, name)
        if os.path.isfile(p):
            obj, _ = read_json(p)
            if not isinstance(obj, dict) or "schema_version" not in obj:
                ir_problems.append("%s invalid (missing schema_version)" % name)

    report = {
        "result_path": result_path,
        "ok": not problems,
        "problems": problems,
        "ir_problems": ir_problems,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


def build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="LaTeX writing skill: bootstrap/discover/finalize/selfcheck",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name in ("bootstrap", "finalize", "selfcheck"):
        p = sub.add_parser(name)
        p.add_argument("--input", default="/input")
        p.add_argument("--output", default="/app/output")
        p.add_argument("--work", default="/app/work")

    p = sub.add_parser("discover")
    p.add_argument("--input", default="/input")
    return parser


def main(argv=None):
    reconfigure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "bootstrap": cmd_bootstrap,
        "discover": cmd_discover,
        "finalize": cmd_finalize,
        "selfcheck": cmd_selfcheck,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
