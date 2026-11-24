#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Write the first legal result.json (status=partial, contract fields null).

Must run within the first 3 minutes of the task. Never leaves a half-written
file: writes to a temp file then atomically replaces. On write failure it
degrades to a minimal legal result and still exits 0.
"""
import argparse
import json
import os
import sys


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    return ap


def _load_json(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError):
        return None


def _default_value(shape):
    if shape == "list":
        return []
    if shape == "object":
        return {}
    return None


def atomic_write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def run(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)

    manifest = _load_json(os.path.join(args.work, "input_manifest.json")) or {}
    case_id = manifest.get("case_id")
    fields = manifest.get("contract_fields") or []

    result = {"case_id": case_id}

    # Backfill contract fields first, in original order, with honest nulls.
    for fd in fields:
        name = fd.get("name") if isinstance(fd, dict) else str(fd)
        if name and name not in result:
            if isinstance(fd, dict) and "default" in fd:
                result[name] = fd["default"]
            else:
                result[name] = _default_value(fd.get("shape") if isinstance(fd, dict) else "scalar")

    result.update({
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "artifacts": [],
        "pending_human": [],
        "errors": list(manifest.get("errors") or []),
        "degradations": list(manifest.get("degradations") or []),
        "runtime": {"seconds": None, "model_requests": None, "note": "measured at final write"},
    })

    if manifest.get("input_error"):
        result["status"] = "input_error"

    try:
        atomic_write_json(os.path.join(args.output, "result.json"), result)
    except OSError as exc:
        minimal = {
            "case_id": case_id,
            "status": "input_error",
            "sources": [], "uncertainty": [], "change_log": [],
            "artifacts": [], "pending_human": [],
            "errors": [{"reason": "write_failed", "detail": str(exc)}],
            "degradations": [],
            "runtime": {"seconds": None, "model_requests": None, "note": "write_failed"},
        }
        try:
            atomic_write_json(os.path.join(args.output, "result.json"), minimal)
        except OSError as exc2:
            print(f"emit_skeleton.py: fatal write failure: {exc2}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(run())
