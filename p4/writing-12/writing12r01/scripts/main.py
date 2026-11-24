#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py — LaTeX 写作 Skill 的引导、回填、收尾与自检。

职责：
  1. 扫描 /input，发现 case.json / task.md 与材料文件；
  2. 抽取 case_id 与 output_contract 字段清单；
  3. 立即写入一版合法的 /app/output/result.json 骨架（先落盘，再增量改进）；
  4. finalize / selfcheck 校验 result.json 满足输出契约与证据溯源要求。

仅依赖 Python 标准库。bootstrap/discover/finalize 对非致命失败一律退出码 0；
selfcheck 在发现问题时退出码 1，以便调用方据此决定是否继续。
"""
from __future__ import annotations

import argparse
import hashlib
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
REQUIRED_META = ("case_id", "status", "sources", "uncertainty", "change_log")


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reconfigure_stdio():
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


def _recover_json(text, pos, max_attempts=240):
    """Bounded attempt to recover a valid JSON prefix from a truncated document.

    Walks backward from the parse-error position trying ``prefix + closer``, where
    ``closer`` closes unbalanced brackets. Returns the parsed object or None.
    Recovery is honest metadata salvage (never scientific data) and is recorded
    in the caller's error list.
    """
    if len(text) > 256 * 1024:  # do not spend time recovering huge inputs
        return None
    closers = ("", "}", "}}", "]", "]}", "}]")
    end = min(len(text), pos + 1)
    attempts = 0
    for k in range(end, max(0, end - 160) - 1, -1):
        prefix = text[:k]
        for closer in closers:
            if attempts >= max_attempts:
                return None
            attempts += 1
            try:
                return json.loads(prefix + closer)
            except Exception:
                continue
    return None


def read_json(path):
    """Tolerant JSON read. Returns (obj, errors_list)."""
    text, err = read_text(path)
    if text is None:
        return None, [err or "unreadable"]
    try:
        return json.loads(text), []
    except json.JSONDecodeError as exc:
        recovered = _recover_json(text, exc.pos)
        if recovered is not None:
            return recovered, ["json_recovered_at_%d" % exc.pos]
        cut = text[:exc.pos]
        try:
            return json.loads(cut), ["json_truncated_at_%d" % exc.pos]
        except Exception:
            return None, ["bad_json: %s" % exc]


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
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


def empty_evidence_map():
    return {"schema_version": "1.0", "claims": [], "conflicts": []}


def build_skeleton(root):
    """Build result.json skeleton from /input/case.json. Returns (skeleton, keys, notes)."""
    case_path = os.path.join(root, "case.json")
    notes = []
    case_id = None
    keys = []
    if os.path.isfile(case_path):
        obj, errs = read_json(case_path)
        notes.extend(errs)
        case_id = find_case_id(obj)
        keys = contract_keys(obj)
        if not keys:
            notes.append("output_contract_not_found: metadata-only result")
    else:
        notes.append("case.json_not_found_in:" + root)

    if case_id is None:
        case_id = "unknown"
        notes.append("case_id_unknown: used 'unknown'")

    started = utcnow()
    skeleton = {"case_id": case_id}
    for key in keys:
        skeleton[key] = None
    skeleton.update({
        "status": "partial" if keys else "input_error",
        "artifacts": [],
        "evidence_map": empty_evidence_map(),
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "time": started,
            "step": "bootstrap",
            "note": "result skeleton written before analysis; contract fields default to null",
        }],
        "errors": notes,
        "degradations": [],
        "runtime": {"started_at": started, "producer": "main.py"},
    })
    return skeleton, keys, notes


def cmd_bootstrap(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    skeleton, keys, notes = build_skeleton(args.input)
    write_json(os.path.join(args.output, "result.json"), skeleton)
    inventory = {
        "input_root": args.input,
        "contract_keys": keys,
        "case_id": skeleton["case_id"],
        "files": discover_input(args.input),
        "generated_at": utcnow(),
        "notes": notes,
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


def cmd_discover(args):
    files = discover_input(args.input)
    print(json.dumps({"input_root": args.input, "files": files},
                     ensure_ascii=False, indent=2))
    return 0


def _load_result(args):
    result_path = os.path.join(args.output, "result.json")
    obj, errs = read_json(result_path)
    return result_path, obj, errs


def _load_contract_keys(args):
    inv_path = os.path.join(args.work, "inventory.json")
    if os.path.isfile(inv_path):
        inv, _ = read_json(inv_path)
        if isinstance(inv, dict) and isinstance(inv.get("contract_keys"), list):
            return inv["contract_keys"]
    _, keys, _ = build_skeleton(args.input)
    return keys


def cmd_finalize(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    result_path, obj, errs = _load_result(args)
    if obj is None or not isinstance(obj, dict):
        # Rebuild a minimal valid result rather than leaving nothing.
        skeleton, keys, notes = build_skeleton(args.input)
        obj = skeleton
        errs = list(notes)
    keys = _load_contract_keys(args)

    if obj.get("case_id") in (None, "", "unknown"):
        obj["case_id"] = "unknown"
        errs.append("case_id_unknown_at_finalize")
    for key in keys:
        if key not in obj:
            obj[key] = None
    for key in REQUIRED_META:
        if key not in obj:
            if key == "change_log":
                obj[key] = []
            elif key == "status":
                obj[key] = "partial" if keys else "input_error"
            else:
                obj[key] = []
    if "evidence_map" not in obj or not isinstance(obj["evidence_map"], dict):
        obj["evidence_map"] = empty_evidence_map()
    if "pending_human" not in obj:
        obj["pending_human"] = []
    if "errors" not in obj:
        obj["errors"] = []
    if "degradations" not in obj:
        obj["degradations"] = []

    obj.setdefault("change_log", []).append({
        "time": utcnow(),
        "step": "finalize",
        "note": "contract keys ensured; errors=%d" % len(errs),
    })
    if errs and "errors" in obj:
        for e in errs:
            if e not in obj["errors"]:
                obj["errors"].append(e)

    write_json(result_path, obj)
    print(json.dumps({
        "result_path": result_path,
        "status": obj.get("status"),
        "contract_keys": keys,
        "claim_count": len(obj.get("evidence_map", {}).get("claims", [])),
    }, ensure_ascii=False, indent=2))
    return 0


def _validate(obj, keys):
    """Return list of problem strings. Empty list = pass."""
    problems = []
    if not isinstance(obj, dict):
        return ["result is not a JSON object"]
    if obj.get("status") not in ALLOWED_STATUS:
        problems.append("status invalid or missing")
    if obj.get("case_id") in (None, ""):
        problems.append("case_id missing")
    for key in keys:
        if key not in obj:
            problems.append("missing contract key: %s" % key)
    for key in ("sources", "uncertainty", "change_log", "pending_human"):
        if key not in obj or not isinstance(obj[key], list):
            problems.append("%s missing or not a list" % key)
    em = obj.get("evidence_map")
    if not isinstance(em, dict):
        problems.append("evidence_map missing")
    else:
        for i, claim in enumerate(em.get("claims", [])):
            if not isinstance(claim, dict):
                problems.append("evidence_map.claims[%d] not object" % i)
                continue
            for f in ("id", "type", "statement", "locator", "quote", "status"):
                if f not in claim or claim[f] in (None, ""):
                    problems.append("claim[%d] missing %s" % (i, f))
            if claim.get("status") not in {"verified", "pending_human", "conflict", "gap"}:
                problems.append("claim[%d] invalid status" % i)
    return problems


def cmd_selfcheck(args):
    result_path, obj, errs = _load_result(args)
    if obj is None:
        obj = {}
    keys = _load_contract_keys(args)
    problems = _validate(obj, keys)
    if errs:
        problems.append("result.json read issues: " + "; ".join(errs))
    report = {
        "result_path": result_path,
        "case_id": obj.get("case_id") if isinstance(obj, dict) else None,
        "status": obj.get("status") if isinstance(obj, dict) else None,
        "contract_keys": keys,
        "claim_count": len(obj.get("evidence_map", {}).get("claims", [])) if isinstance(obj, dict) else 0,
        "problems": problems,
        "ok": not problems,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="LaTeX 写作 Skill 引导/回填/自检",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add_io(sp):
        sp.add_argument("--input", default="/input")
        sp.add_argument("--output", default="/app/output")
        sp.add_argument("--work", default="/app/work")

    sp = sub.add_parser("bootstrap", help="write result.json skeleton + inventory")
    add_io(sp)
    sp.set_defaults(func=cmd_bootstrap)

    sp = sub.add_parser("discover", help="list input files")
    sp.add_argument("--input", default="/input")
    sp.set_defaults(func=cmd_discover)

    sp = sub.add_parser("finalize", help="ensure contract keys & valid result")
    add_io(sp)
    sp.set_defaults(func=cmd_finalize)

    sp = sub.add_parser("selfcheck", help="validate result.json; exit 1 on problems")
    add_io(sp)
    sp.set_defaults(func=cmd_selfcheck)
    return p


def main(argv=None):
    reconfigure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error": "interrupted"}))
        return 0
    except Exception as exc:  # never crash the harness
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}))
        return 0


if __name__ == "__main__":
    sys.exit(main())
