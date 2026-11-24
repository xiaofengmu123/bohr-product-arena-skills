#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""result.json lifecycle + input discovery. Standard library only.

Subcommands:
  discover   --input DIR                      scan input directory, report findings as JSON
  init       --case CASE.json --out result.json [--work DIR] [--title T]
  merge      --out result.json --patch PATCH.json | --patch-json '{"...": ...}'
  finalize   --out result.json --status complete|partial|failed [--artifact SRC...]

Paths are resolved relative to the current working directory; the platform runs
this from /skills/weekly-skill and passes absolute /input, /app/output, /app/work.
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

BUDGET = {
    "total_seconds": 900,
    "total_requests": 64,
    "early_drop_deadline_seconds": 180,
    "phases": [
        {"id": "p1", "name": "read_input", "seconds": 60, "requests": 6, "deadline": 60},
        {"id": "p2", "name": "drop_skeleton", "seconds": 60, "requests": 2, "deadline": 120},
        {"id": "p3", "name": "extract_ir", "seconds": 180, "requests": 16, "deadline": 300},
        {"id": "p4", "name": "build_svg", "seconds": 180, "requests": 12, "deadline": 480},
        {"id": "p5", "name": "render", "seconds": 120, "requests": 8, "deadline": 600},
        {"id": "p6", "name": "selftest", "seconds": 120, "requests": 10, "deadline": 720},
        {"id": "p7", "name": "finalize", "seconds": 180, "requests": 10, "deadline": 900},
    ],
}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path):
    """Read UTF-8 text tolerantly: BOM, bad encoding, unreadable file."""
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    for enc in ("utf-8-sig", "utf-8"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def read_json(path):
    txt = read_text(path)
    try:
        return json.loads(txt)
    except json.JSONDecodeError as exc:
        return {"_parse_error": str(exc), "_raw_prefix": txt[:500]}


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def merge_value(old, new):
    """Deterministic deep merge; lists upsert dict items by id/key/path/from."""
    if isinstance(old, dict) and isinstance(new, dict):
        out = dict(old)
        for key, value in new.items():
            out[key] = merge_value(out.get(key), value)
        return out
    if isinstance(old, list) and isinstance(new, list):
        out = list(old)
        for item in new:
            if isinstance(item, dict):
                identity = next(
                    (k for k in ("id", "key", "path", "from") if k in item and isinstance(item[k], str)),
                    None,
                )
                if identity is not None:
                    replaced = False
                    for i, existing in enumerate(out):
                        if isinstance(existing, dict) and existing.get(identity) == item[identity]:
                            out[i] = merge_value(existing, item)
                            replaced = True
                            break
                    if not replaced:
                        out.append(item)
                else:
                    out.append(item)
            elif item not in out:
                out.append(item)
        return out
    return new


def discover(args):
    root = os.path.abspath(args.input)
    report = {"root": root, "exists": os.path.isdir(root), "files": [], "issues": []}
    if not report["exists"]:
        return report
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            size = os.path.getsize(full)
            if size > 2 * 1024 * 1024:
                report["issues"].append({"path": rel, "issue": "size_over_2MB", "size": size})
                continue
            entry = {"path": rel, "size": size}
            try:
                head = open(full, "rb").read(16)
                if head.startswith(b"%PDF"):
                    entry["kind"] = "pdf"
                elif head.startswith(b"\xef\xbb\xbf"):
                    entry["kind"] = "utf8_bom"
                elif head[:2] == b"\xff\xfe" or head[:2] == b"\xfe\xff":
                    entry["kind"] = "utf16"
                else:
                    entry["kind"] = "text_or_binary"
            except OSError as exc:
                entry["kind"] = "unreadable"
                entry["error"] = str(exc)
            if name == "case.json":
                try:
                    data = read_json(full)
                    entry["case"] = {
                        "case_id": data.get("id") or data.get("case_id") or data.get("challenge_id"),
                        "has_output_contract": isinstance(data.get("output_contract"), dict),
                        "output_contract_keys": list(data.get("output_contract", {}).keys()),
                        "parse_error": data.get("_parse_error"),
                    }
                except Exception as exc:  # noqa: BLE001 - discovery must not crash
                    entry["case"] = {"error": str(exc)}
            report["files"].append(entry)
    return report


def extract_case_id(case_data, case_path):
    for key in ("case_id", "id", "challenge_id"):
        if isinstance(case_data.get(key), str) and case_data[key]:
            return case_data[key]
    base = os.path.basename(case_path)
    if base and base not in ("case.json",):
        return base
    return "unknown"


def init(args):
    os.makedirs(args.out_dir, exist_ok=True)
    case_path = os.path.abspath(args.case)
    case_data = read_json(case_path) if os.path.exists(case_path) else {}
    case_id = extract_case_id(case_data, case_path)
    contract = case_data.get("output_contract")
    if not isinstance(contract, dict) or not contract:
        contract = {
            "entities": [],
            "relations": [],
            "data_encoding": [],
            "legend_annotations": [],
            "editable_sources": [],
            "exports": [],
        }
    # Echo every declared field verbatim; empty list for list fields, null otherwise.
    echo = {}
    for key in contract.keys():
        echo[key] = [] if isinstance(contract.get(key), list) else (contract.get(key) if contract.get(key) is not None else None)

    result = {
        "case_id": case_id,
        "title": args.title or case_data.get("title") or "",
        "status": "in_progress",
        "started_at": now_iso(),
        "ended_at": None,
        "budget": BUDGET,
        "phase": "p2",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "checks": [],
        "artifacts": [],
        "change_log": [{"ts": now_iso(), "phase": "p2", "action": "init: partial skeleton dropped"}],
    }
    result.update(echo)
    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    write_json(out_path, result)
    return {"ok": True, "out": out_path, "case_id": case_id, "contract_keys": list(echo.keys())}


def load_patch(args):
    if args.patch_json:
        try:
            return json.loads(args.patch_json)
        except json.JSONDecodeError as exc:
            return {"_patch_error": str(exc)}
    if args.patch:
        return read_json(args.patch)
    return {}


def merge(args):
    out_path = os.path.abspath(args.out)
    if not os.path.exists(out_path):
        return {"ok": False, "error": "result.json missing; run init first", "out": out_path}
    result = read_json(out_path)
    patch = load_patch(args)
    if "_patch_error" in patch:
        return {"ok": False, "error": patch["_patch_error"], "out": out_path}
    result = merge_value(result, patch)
    result["updated_at"] = now_iso()
    write_json(out_path, result)
    return {"ok": True, "out": out_path, "merged_keys": sorted(patch.keys())}


def finalize(args):
    out_path = os.path.abspath(args.out)
    if not os.path.exists(out_path):
        return {"ok": False, "error": "result.json missing", "out": out_path}
    result = read_json(out_path)
    status = args.status
    if status not in ("complete", "partial", "failed", "in_progress"):
        return {"ok": False, "error": "invalid status", "out": out_path}
    result["status"] = status
    result["ended_at"] = now_iso()
    started = result.get("started_at")
    if started:
        try:
            t0 = datetime.fromisoformat(started.replace("Z", "+00:00"))
            t1 = datetime.now(timezone.utc)
            result["runtime_seconds"] = max(0, int((t1 - t0).total_seconds()))
        except ValueError:
            pass
    artifacts = result.setdefault("artifacts", [])
    for src in args.artifact or []:
        src = os.path.abspath(src)
        if not os.path.exists(src):
            result.setdefault("limitations", []).append(
                {"item": src, "issue": "artifact_not_found", "ts": now_iso()}
            )
            continue
        dst_dir = os.path.dirname(out_path)
        dst = os.path.join(dst_dir, os.path.basename(src))
        try:
            shutil.copy2(src, dst)
        except shutil.SameFileError:
            dst = src
        except OSError as exc:
            result.setdefault("limitations", []).append(
                {"item": src, "issue": "copy_failed", "error": str(exc), "ts": now_iso()}
            )
            continue
        if not any(a.get("path") == dst for a in artifacts if isinstance(a, dict)):
            artifacts.append({"path": dst, "format": os.path.splitext(dst)[1].lstrip(".")})
    result.setdefault("change_log", []).append(
        {"ts": now_iso(), "phase": "p7", "action": f"finalize: status={status}"}
    )
    write_json(out_path, result)
    return {"ok": True, "out": out_path, "status": status, "artifacts": len(artifacts)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="result.json lifecycle + input discovery")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_discover = sub.add_parser("discover", help="scan input directory")
    p_discover.add_argument("--input", default=DEFAULT_INPUT)
    p_discover.set_defaults(func=lambda a: discover(a))

    p_init = sub.add_parser("init", help="drop partial result skeleton")
    p_init.add_argument("--case", default=os.path.join(DEFAULT_INPUT, "case.json"))
    p_init.add_argument("--out", default=os.path.join(DEFAULT_OUTPUT, "result.json"))
    p_init.add_argument("--out-dir", dest="out_dir", default=DEFAULT_OUTPUT)
    p_init.add_argument("--title", default=None)
    p_init.set_defaults(func=lambda a: init(a))

    p_merge = sub.add_parser("merge", help="merge a partial patch into result.json")
    p_merge.add_argument("--out", default=os.path.join(DEFAULT_OUTPUT, "result.json"))
    p_merge.add_argument("--patch", default=None)
    p_merge.add_argument("--patch-json", default=None)
    p_merge.set_defaults(func=lambda a: merge(a))

    p_fin = sub.add_parser("finalize", help="set status and register artifacts")
    p_fin.add_argument("--out", default=os.path.join(DEFAULT_OUTPUT, "result.json"))
    p_fin.add_argument("--status", required=True)
    p_fin.add_argument("--artifact", action="append", default=None)
    p_fin.set_defaults(func=lambda a: finalize(a))

    args = ap.parse_args(argv)
    try:
        result = args.func(args)
    except Exception as exc:  # noqa: BLE001 - CLI must not crash the agent
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
