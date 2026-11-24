#!/usr/bin/env python3
"""Skill entry point: discover input -> scaffold result.json (partial) -> inventory.

This script is the first thing the Agent should run (within the first 3 minutes).
It writes a legally-shaped ``result.json`` skeleton so that any later failure still
leaves an inspectable partial deliverable. It is idempotent: re-running merges into
the existing file instead of rebuilding it, so later incremental writes are safe.

Only the Python standard library is used.
"""
import argparse
import json
import os
import sys
import time

DEFAULT_CASE = "/input/case.json"
DEFAULT_TASK = "/input/task.md"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

RESERVED_STATUSES = {"partial", "complete", "failed", "insufficient_evidence", "input_error"}

FALLBACK_CONTRACT_KEYS = ("deliverable", "fact_source_map", "version_boundary")


def read_json(path):
    """Read JSON tolerating BOM, whitespace, and bad encodings.

    Returns (obj_or_None, error_string_or_None).
    """
    if not path or not os.path.exists(path):
        return None, "missing_file"
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, "read_error:%s" % exc
    text = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        return None, "undecodable"
    try:
        return json.loads(text), None
    except ValueError as exc:
        return None, "bad_json:%s" % exc


def read_text(path, limit=2000000):
    """Read a UTF-8 (BOM-tolerant) text file up to a size limit."""
    if not path or not os.path.exists(path):
        return None
    try:
        size = os.path.getsize(path)
        if size > limit:
            return None
        with open(path, "rb") as fh:
            raw = fh.read(limit + 1)
    except OSError:
        return None
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def discover_case_id(data):
    if not isinstance(data, dict):
        return None
    for key in ("case_id", "caseId", "id", "challenge_id", "challengeId"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return None


def discover_contract(data):
    """Return the output_contract dict plus its origin.

    - Prefer an explicit ``output_contract`` object.
    - Otherwise look for the three delivery keys directly.
    - Missing values are represented as None so callers can see the shape.
    """
    if not isinstance(data, dict):
        return {}, "absent"
    contract = data.get("output_contract")
    if isinstance(contract, dict):
        return dict(contract), "output_contract"
    if isinstance(contract, list):
        # Some cases use a list of required keys; materialise as None values.
        out = {}
        for item in contract:
            if isinstance(item, str):
                out[item] = data.get(item)
            elif isinstance(item, dict) and "key" in item:
                out[item["key"]] = data.get(item["key"])
        return out, "output_contract.list"
    # Fallback: the three delivery fields described in the topic.
    fallback = {key: data.get(key) for key in FALLBACK_CONTRACT_KEYS}
    if any(v is not None for v in fallback.values()):
        return fallback, "fallback_three_fields"
    return {key: None for key in FALLBACK_CONTRACT_KEYS}, "fallback_empty"


def list_input_files(root, limit=200):
    """Return a sorted list of relative paths under an input root."""
    found = []
    if not root or not os.path.isdir(root):
        return found
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(dirnames)
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            found.append(rel)
            if len(found) >= limit:
                return found
    return found


def make_change_log_entry(action, detail):
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "action": action,
        "detail": detail,
    }


def empty_uncertainty():
    return {
        "claims_requiring_confirmation": [],
        "conflicting_evidence": [],
        "material_gaps": [],
        "limitations": [],
    }


def merge_result(existing, new_fields):
    """Merge scaffold fields into an existing result without discarding work.

    Lists that carry incremental user work (change_log, sources, pending_human,
    uncertainty sub-lists) are preserved when they already exist and non-empty.
    Scalar status is only advanced by the Agent, never by the scaffold.
    """
    if not isinstance(existing, dict):
        existing = {}
    merged = dict(new_fields)
    # Keep everything the Agent already wrote.
    for key, value in existing.items():
        if key not in merged:
            merged[key] = value
            continue
        if key == "change_log":
            if isinstance(value, list) and value:
                merged[key] = value
        elif key == "sources":
            if isinstance(value, list) and value:
                merged[key] = value
        elif key == "pending_human":
            if isinstance(value, list) and value:
                merged[key] = value
        elif key == "uncertainty":
            if isinstance(value, dict):
                base = merged.get(key) or {}
                for sub in base:
                    if sub not in value:
                        value[sub] = base[sub]
                merged[key] = value
    # Ensure the change_log carries a bootstrap entry exactly once.
    if not isinstance(merged.get("change_log"), list):
        merged["change_log"] = []
    if not any(e.get("action") == "bootstrap" for e in merged["change_log"] if isinstance(e, dict)):
        merged["change_log"].insert(0, make_change_log_entry(
            "bootstrap", "result skeleton written by main.py (idempotent merge)"))
    # Do not let the scaffold downgrade an already-set status.
    if existing.get("status") in RESERVED_STATUSES:
        merged["status"] = existing["status"]
    return merged


def build_skeleton(case_path, task_path, work, input_roots):
    case_data, case_err = read_json(case_path)
    task_text = read_text(task_path)
    case_id = discover_case_id(case_data) if case_data else None
    contract, contract_origin = discover_contract(case_data) if case_data else ({}, "absent")
    # Ensure the three-field fallback shape exists even when case.json is
    # missing or unreadable, so the skeleton keeps an honest, complete shape.
    if not contract:
        contract = {key: None for key in FALLBACK_CONTRACT_KEYS}
        contract_origin = "fallback_empty"

    skeleton = {
        "case_id": case_id,
        "status": "partial",
        # Contract fields are flattened to top level (required by the runtime),
        # while the original shape is retained under ``contract`` for traceability.
        "contract": {
            "origin": contract_origin,
            "fields": list(contract.keys()),
        },
        "sources": [],
        "uncertainty": empty_uncertainty(),
        "pending_human": [],
        "errors": [] if case_err else [],
        "degradations": [],
        "change_log": [],
        "runtime": {
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "budget_seconds": 900,
            "budget_requests": 64,
            "tokens_per_response": 16384,
        },
        "input": {
            "case_path": case_path,
            "task_path": task_path,
            "case_read_error": case_err,
            "task_available": bool(task_text),
            "input_roots": list(input_roots),
            "files": {},
        },
    }
    # Flatten contract fields into the skeleton.
    for key, value in contract.items():
        if key in skeleton:
            skeleton[key] = value  # case_id/status etc. cannot be overridden this way
        else:
            skeleton[key] = value
    if contract_origin == "absent":
        skeleton["degradations"].append("output_contract not found in case.json; used fallback fields")

    if case_err:
        skeleton["errors"].append("case_read:%s" % case_err)
        skeleton["degradations"].append("case.json unreadable or invalid: %s" % case_err)

    files_by_root = {}
    for root in input_roots:
        files_by_root[root] = list_input_files(root)
    skeleton["input"]["files"] = files_by_root

    # Persist an inventory into the work directory for later quote-locatability checks.
    inventory = {
        "case_id": case_id,
        "contract_origin": contract_origin,
        "contract_fields": list(contract.keys()),
        "input_files": files_by_root,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return skeleton, inventory


def main(argv=None):
    ap = argparse.ArgumentParser(description="Scaffold /app/output/result.json")
    ap.add_argument("--case", default=os.environ.get("SKILL_CASE", DEFAULT_CASE))
    ap.add_argument("--task", default=os.environ.get("SKILL_TASK", DEFAULT_TASK))
    ap.add_argument("--output", default=os.environ.get("SKILL_OUTPUT", DEFAULT_OUTPUT))
    ap.add_argument("--work", default=os.environ.get("SKILL_WORK", DEFAULT_WORK))
    ap.add_argument("--material-root", action="append", default=None,
                    help="input root to scan; repeatable, defaults to /input and /app/work")
    a = ap.parse_args(argv)

    roots = a.material_root or [DEFAULT_CASE.rsplit("/", 1)[0] if "/" in DEFAULT_CASE else "/input",
                                a.work]

    out_dir = a.output if not a.output.endswith(".json") else os.path.dirname(a.output) or "."
    result_path = a.output if a.output.endswith(".json") else os.path.join(a.output, "result.json")

    try:
        os.makedirs(out_dir, exist_ok=True)
        os.makedirs(a.work, exist_ok=True)
    except OSError as exc:
        print(json.dumps({"ok": False, "error": "mkdir:%s" % exc}, ensure_ascii=False))
        return 1

    skeleton, inventory = build_skeleton(a.case, a.task, a.work, roots)

    # Idempotent merge.
    existing, _ = read_json(result_path)
    final = merge_result(existing, skeleton)

    try:
        with open(result_path, "w", encoding="utf-8") as fh:
            json.dump(final, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        print(json.dumps({"ok": False, "error": "write_result:%s" % exc}, ensure_ascii=False))
        return 1

    try:
        inv_path = os.path.join(a.work, "inventory.json")
        with open(inv_path, "w", encoding="utf-8") as fh:
            json.dump(inventory, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass  # inventory is best-effort; result.json is the contract.

    print(json.dumps({
        "ok": True,
        "case_id": skeleton["case_id"],
        "status": final.get("status"),
        "contract_fields": skeleton["contract"]["fields"],
        "result_path": result_path,
        "inventory_path": os.path.join(a.work, "inventory.json"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
