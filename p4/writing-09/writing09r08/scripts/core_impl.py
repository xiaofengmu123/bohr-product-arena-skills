#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build or incrementally patch /app/output/result.json.

Build: write the minimal honest skeleton with case_id + every output_contract
field (typed placeholder) + status/sources/uncertainty/change_log. This is the
artifact the agent MUST land within the first 3 minutes.

Patch: deep-merge a patch file into the existing result.json, append a
change_log entry, and rewrite atomically so every incremental edit is durable.

Usage:
  python scripts/build_result.py --summary /app/work/input_summary.json --output-dir /app/output
  python scripts/build_result.py --result /app/output/result.json --patch /app/work/patch.json
"""
import argparse
import copy
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

DEFAULT_PLACEHOLDER = {
    "deliverable": {},
    "facts_sources": [],
    "version_boundaries": {},
    "sources": [],
    "uncertainty": [],
    "pending_human": [],
    "change_log": [],
    "limitations": [],
}


def now_ts():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def placeholder_for(field_name, declared_type):
    t = (declared_type or "").lower()
    if field_name in DEFAULT_PLACEHOLDER:
        return copy.deepcopy(DEFAULT_PLACEHOLDER[field_name])
    if t in ("list", "array"):
        return []
    if t in ("dict", "object", "map"):
        return {}
    return None


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def atomic_write(path, obj):
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def build(summary_path, output_dir):
    with open(summary_path, "r", encoding="utf-8-sig") as f:
        summary = json.load(f)
    os.makedirs(output_dir, exist_ok=True)

    contract = summary.get("output_contract", {})
    result = {
        "case_id": summary.get("case_id"),
        "status": "partial",
    }
    for field, spec in contract.items():
        result[field] = placeholder_for(field, spec.get("type"))
    result["sources"] = []
    result["uncertainty"] = []
    result["pending_human"] = []
    result["change_log"] = [{
        "ts": now_ts(),
        "action": "build_skeleton",
        "note": "minimal honest partial skeleton; refine incrementally",
    }]

    out = os.path.join(output_dir, "result.json")
    atomic_write(out, result)
    print(json.dumps({
        "ok": True,
        "status": "partial",
        "case_id": result["case_id"],
        "fields": list(result.keys()),
        "written": out,
    }, ensure_ascii=False, indent=2))
    return 0


def deep_merge(base, patch):
    """Recursively merge patch into base (dicts merge, everything else replaces)."""
    if isinstance(base, dict) and isinstance(patch, dict):
        for k, v in patch.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                deep_merge(base[k], v)
            else:
                base[k] = copy.deepcopy(v)
        return base
    return copy.deepcopy(patch)


def patch(result_path, patch_path):
    result = load_json(result_path)
    patch_obj = load_json(patch_path)

    if patch_obj.get("case_id") and patch_obj["case_id"] != result.get("case_id"):
        print("WARNING: patch case_id differs from result; keeping result case_id", file=sys.stderr)

    action = patch_obj.pop("_action", None) or "patch_update"
    note = patch_obj.pop("_note", None) or ""
    new_log = patch_obj.pop("change_log", None)

    body = {k: v for k, v in patch_obj.items() if k != "case_id"}
    deep_merge(result, body)

    result.setdefault("change_log", [])
    result["change_log"].append({"ts": now_ts(), "action": action, "note": note})
    if new_log and isinstance(new_log, list):
        result["change_log"].extend(new_log)

    atomic_write(result_path, result)
    print(json.dumps({"ok": True, "action": action, "written": result_path}, ensure_ascii=False, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Build or patch result.json")
    ap.add_argument("--summary", help="path to input_summary.json (build mode)")
    ap.add_argument("--output-dir", default="/app/output")
    ap.add_argument("--result", help="path to result.json (patch mode)")
    ap.add_argument("--patch", help="path to patch JSON (patch mode)")
    a = ap.parse_args()

    if a.patch:
        if not a.result:
            a.result = "/app/output/result.json"
        return patch(a.result, a.patch)
    if a.summary:
        return build(a.summary, a.output_dir)
    ap.error("provide --summary (build) or --patch (patch)")
    return 2


if __name__ == "__main__":
    sys.exit(main())
