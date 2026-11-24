#!/usr/bin/env python3
"""record.py — incremental, non-destructive updates to /app/output/result.json.

After scaffold.py writes the early partial skeleton, every later change must be
an incremental edit, never a wholesale rewrite.  record.py appends/merges the
individual records the Skill needs for third-party verification:

    change    append a change_log entry (time-sorted audit trail)
    source    append an evidence source with locator + verbatim quote
    evidence  append a page-level claim with refs/locator/quote
    version   append a version snapshot (name, note, files)
    contract  set one output_contract field to a parsed value
    budget    record a stage transition / request & elapsed-time tally
    status    set status and note it in change_log

Only stdlib is used.  Paths are resolved like scaffold.py via
INPUT_DIR/WORK_DIR/OUTPUT_DIR (or --output), never from the current directory.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def load_json_text(path: Path):
    errors = []
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, [f"read failed: {exc}"]
    text = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return None, ["undecodable bytes"]
    try:
        return json.loads(text), errors
    except json.JSONDecodeError as exc:
        return None, [f"bad json: {exc}"]


def write_json_atomic(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def minimal_result():
    return {
        "case_id": "unknown",
        "status": "partial",
        "sources": [],
        "uncertainty": {"unresolved": [], "missing_artifacts": [], "notes": ""},
        "pending_human": [],
        "change_log": [],
        "runtime": {},
        "errors": [],
        "degradations": [],
    }


def load_result(output_dir: Path) -> dict:
    path = output_dir / "result.json"
    obj, errs = load_json_text(path)
    if obj is None or not isinstance(obj, dict):
        result = minimal_result()
        result["errors"].extend(errs or [])
        result["degradations"].append("result.json missing/invalid; re-seeded minimal")
        return result
    return obj


def save_result(output_dir: Path, result: dict) -> None:
    write_json_atomic(output_dir / "result.json", result)


def add_change(result: dict, step: str, action: str, status: str, reason: str) -> None:
    result.setdefault("change_log", []).append({
        "t": now_iso(), "step": step, "action": action,
        "status": status, "reason": reason,
    })


def parse_value(raw: str, as_json: bool):
    if as_json:
        return json.loads(raw)
    v = (raw or "").strip()
    if v == "" or v.lower() in ("null", "none"):
        return None
    if v.lower() == "true":
        return True
    if v.lower() == "false":
        return False
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if re.fullmatch(r"-?\d+\.\d+", v):
        return float(v)
    if v[:1] in "[{" and v[-1:] in "]}":
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return raw
    return raw


def cmd_change(args, output_dir: Path) -> None:
    result = load_result(output_dir)
    add_change(result, args.step, args.action, args.status, args.reason)
    save_result(output_dir, result)
    print(f"change logged: {args.step}/{args.action} status={args.status}")


def cmd_source(args, output_dir: Path) -> None:
    result = load_result(output_dir)
    item = {"ref": args.ref, "loc": args.loc, "kind": args.kind}
    if args.quote:
        item["quote"] = args.quote
    if args.used_in:
        item["used_in"] = [x.strip() for x in args.used_in.split(",") if x.strip()]
    result.setdefault("sources", []).append(item)
    save_result(output_dir, result)
    print(f"source logged: {args.ref} @ {args.loc}")


def cmd_evidence(args, output_dir: Path) -> None:
    result = load_result(output_dir)
    item = {
        "page": int(args.page),
        "role": args.role,
        "claim": args.claim,
        "refs": [x.strip() for x in args.refs.split(",") if x.strip()] if args.refs else [],
        "loc": args.loc,
    }
    if args.quote:
        item["quote"] = args.quote
    em = result.get("evidence_map")
    if isinstance(em, dict):
        em.setdefault(str(args.page), []).append(item)
    else:
        result.setdefault("evidence_map", []).append(item)
    save_result(output_dir, result)
    print(f"evidence logged: page {args.page} role={args.role}")


def cmd_version(args, output_dir: Path) -> None:
    result = load_result(output_dir)
    item = {
        "name": args.name,
        "t": now_iso(),
        "note": args.note,
        "files": [x.strip() for x in args.files.split(",") if x.strip()],
    }
    result.setdefault("version_records", []).append(item)
    save_result(output_dir, result)
    print(f"version logged: {args.name}")


def cmd_contract(args, output_dir: Path) -> None:
    result = load_result(output_dir)
    result[args.field] = parse_value(args.value, args.json)
    save_result(output_dir, result)
    print(f"contract field set: {args.field}")


def cmd_budget(args, output_dir: Path) -> None:
    result = load_result(output_dir)
    rt = result.setdefault("runtime", {})
    if args.requests is not None:
        rt["requests_used"] = int(rt.get("requests_used", 0)) + int(args.requests)
    if args.elapsed is not None:
        rt["elapsed_s"] = int(args.elapsed)
    rt["last_step"] = args.step
    add_change(result, args.step, "budget_update",
               "ok" if rt.get("requests_used", 0) <= int(rt.get("budget_requests", 64)) else "over_budget",
               f"step={args.step} requests+={args.requests or 0} elapsed={args.elapsed}")
    save_result(output_dir, result)
    print(f"budget updated: {args.step} requests_used={rt.get('requests_used')} elapsed_s={rt.get('elapsed_s')}")


def cmd_status(args, output_dir: Path) -> None:
    result = load_result(output_dir)
    result["status"] = args.status
    if args.note:
        u = result.setdefault("uncertainty", {})
        u["notes"] = (u.get("notes") or "") + ("" if not u.get("notes") else "\n") + args.note
    add_change(result, "status", f"set_status={args.status}",
               "ok", args.note or "status updated")
    save_result(output_dir, result)
    print(f"status set: {args.status}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--output", default=env("OUTPUT_DIR", "/app/output"))
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--output", dest="output", default=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("change", parents=[parent])
    p.add_argument("--step", required=True)
    p.add_argument("--action", required=True)
    p.add_argument("--status", default="ok")
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_change)

    p = sub.add_parser("source", parents=[parent])
    p.add_argument("--ref", required=True)
    p.add_argument("--loc", required=True)
    p.add_argument("--kind", default="other")
    p.add_argument("--quote", default=None)
    p.add_argument("--used-in", default=None)
    p.set_defaults(func=cmd_source)

    p = sub.add_parser("evidence", parents=[parent])
    p.add_argument("--page", required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--claim", required=True)
    p.add_argument("--refs", default=None)
    p.add_argument("--loc", required=True)
    p.add_argument("--quote", default=None)
    p.set_defaults(func=cmd_evidence)

    p = sub.add_parser("version", parents=[parent])
    p.add_argument("--name", required=True)
    p.add_argument("--note", default="")
    p.add_argument("--files", default="")
    p.set_defaults(func=cmd_version)

    p = sub.add_parser("contract", parents=[parent])
    p.add_argument("--field", required=True)
    p.add_argument("--value", default="")
    p.add_argument("--json", action="store_true", help="parse --value as raw JSON")
    p.set_defaults(func=cmd_contract)

    p = sub.add_parser("budget", parents=[parent])
    p.add_argument("--step", required=True)
    p.add_argument("--requests", type=int, default=None)
    p.add_argument("--elapsed", type=int, default=None)
    p.set_defaults(func=cmd_budget)

    p = sub.add_parser("status", parents=[parent])
    p.add_argument("--status", required=True)
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_status)

    args = ap.parse_args(argv)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    args.func(args, output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
