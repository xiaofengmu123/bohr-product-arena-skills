#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scaffold.py — bootstrap a valid partial result.json early, then never overwrite improvements.

Shared helpers (load_json / extract_case_id / extract_contract / contract_keys /
build_skeleton) are importable by main.py and selftest.py. Standard library only.
"""
import argparse
import json
import os
import sys

# Contract keys used when case.json declares no explicit output contract.
DEFAULT_CONTRACT_KEYS = ["deliverable", "sources", "change_log"]

# Natural default values for well-known keys. Unknown contract keys default to None.
KNOWN_DEFAULTS = {
    "deliverable": None,
    "sources": [],
    "change_log": [],
    "status": "in_progress",
    "uncertainty": [],
    "pending_human": [],
    "limitations": [],
    "errors": [],
    "degradations": [],
    "runtime": {},
    "claims": [],
}

# Keys that must always exist in the final result, regardless of the contract.
ALWAYS_KEYS = ["sources", "uncertainty", "pending_human", "change_log", "limitations"]


def _read_text(path):
    """Read a UTF-8 text file tolerating BOM and common encoding issues."""
    if not path or not os.path.exists(path):
        return None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, OSError):
            continue
    return None


def load_json(path):
    """Load JSON with BOM/bad-encoding tolerance; return None on any failure."""
    if not path or not os.path.exists(path):
        return None
    text = _read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        # Try to salvage the longest leading valid JSON prefix (truncated file case).
        return _salvage_json(text)


def _salvage_json(text):
    for end in range(len(text), 0, -1):
        chunk = text[:end]
        try:
            return json.loads(chunk)
        except (ValueError, TypeError):
            continue
    return None


def extract_case_id(data):
    """case_id > caseId > id, per the runtime contract."""
    if not isinstance(data, dict):
        return None
    for key in ("case_id", "caseId", "id"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return None


def extract_contract(data):
    """Find the output-contract dict in case.json, or None.

    Looks in output_contract / outputContract / contract / result_schema, both at
    top level and nested inside input / task / spec / schema.
    """
    if not isinstance(data, dict):
        return None
    for key in ("output_contract", "outputContract", "contract", "result_schema"):
        if isinstance(data.get(key), dict):
            return data[key]
    for container in ("input", "task", "spec", "schema"):
        sub = data.get(container)
        if isinstance(sub, dict):
            for key in ("output_contract", "outputContract", "contract", "result_schema"):
                if isinstance(sub.get(key), dict):
                    return sub[key]
    return None


def contract_keys(contract):
    """Return the ordered list of keys to echo from the contract dict.

    Handles JSON-schema style contracts (``properties``) as well as flat dicts.
    """
    if not isinstance(contract, dict):
        return list(DEFAULT_CONTRACT_KEYS)
    keys = []
    if isinstance(contract.get("properties"), dict):
        keys.extend(contract["properties"].keys())
    keys.extend(k for k in contract.keys() if k not in ("properties", "required", "$schema"))
    # Preserve order, drop duplicates, keep case_id/status out of the echo list.
    seen = set()
    ordered = []
    for k in keys:
        if k not in seen and k not in ("case_id", "status"):
            seen.add(k)
            ordered.append(k)
    if not ordered:
        ordered = list(DEFAULT_CONTRACT_KEYS)
    return ordered


def build_skeleton(case_id, keys, status="in_progress"):
    """Build a legal partial result dict that always carries the required fields."""
    skeleton = {"case_id": case_id or "unknown", "status": status}
    for key in keys:
        skeleton[key] = KNOWN_DEFAULTS.get(key)
    for key in DEFAULT_CONTRACT_KEYS:
        skeleton.setdefault(key, KNOWN_DEFAULTS.get(key))
    for key in ALWAYS_KEYS:
        skeleton.setdefault(key, KNOWN_DEFAULTS.get(key, []))
    skeleton.setdefault("errors", [])
    skeleton.setdefault("degradations", [])
    skeleton.setdefault("runtime", {})
    return skeleton


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _append_change_log(result, action, detail):
    log = result.get("change_log")
    if not isinstance(log, list):
        log = []
        result["change_log"] = log
    log.append({
        "time": _utc_now(),
        "action": action,
        "detail": detail,
    })


def _utc_now():
    try:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return ""


def cmd_scaffold(case_path, out_dir, work_dir):
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    result_path = os.path.join(out_dir, "result.json")
    existing = load_json(result_path)
    if existing and existing.get("status") not in (None, "in_progress"):
        return {"ok": True, "skipped": True,
                "reason": "existing non-in_progress result preserved"}

    data = load_json(case_path)
    if data is None:
        skeleton = build_skeleton(None, [], status="input_error")
        skeleton["errors"].append("case.json missing or unreadable at %s" % case_path)
        skeleton["limitations"].append("no case.json: cannot determine case_id or output contract")
        write_json(result_path, skeleton)
        return {"ok": True, "status": "input_error", "case_id": None,
                "reason": "case.json unreadable; wrote input_error skeleton"}

    case_id = extract_case_id(data)
    contract = extract_contract(data)
    keys = contract_keys(contract)
    skeleton = build_skeleton(case_id, keys, status="in_progress")
    _append_change_log(skeleton, "scaffold",
                       "read case.json; case_id=%s; contract_keys=%s" % (case_id, keys))
    write_json(result_path, skeleton)
    return {"ok": True, "status": "in_progress", "case_id": case_id,
            "contract_keys": keys, "result": result_path}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bootstrap a valid partial result.json early.")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--out", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args(argv)
    summary = cmd_scaffold(args.case, args.out, args.work)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
