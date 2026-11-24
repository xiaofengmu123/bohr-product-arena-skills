#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Output-contract mirror engine for the slide skill.

This module is the core of the "output-contract-complete" variant.  Its job is
to treat the *actual* ``output_contract`` from the input as the single source of
truth for ``result.json``:

1. discover it from ``/input/case.json`` (any shape: dict, list, string, absent);
2. mirror every field into a complete skeleton with typed null/empty placeholders;
3. idempotently merge incremental edits without ever dropping a declared field;
4. validate the final result field-by-field and emit a ``contract_report.json``.

Only the Python standard library is used.  No scientific data or benchmark
scores are ever invented here.
"""

import copy
import json
import os
import time

__all__ = [
    "SCALAR_TYPES", "DEFAULT_CONTRACT", "ITEM_SCHEMAS", "ENVELOPE_KEYS",
    "utc_now", "read_text", "write_json", "load_json",
    "discover_contract", "materialize", "build_skeleton", "deep_merge",
    "append_change", "walk_leaves", "get_path", "validate_result",
]

SCALAR_TYPES = ("string", "number", "integer", "boolean", "any", "null", "object")

# The runtime contract guarantees the editable deck + evidence map + render
# record as the three deliverables.  ``!`` marks a leaf that must be non-null /
# non-empty in the final result.
DEFAULT_CONTRACT = {
    "presentation": {
        "pptx_path": "!string",
        "pdf_path": "string",
        "png_paths": "list",
        "slide_count": "integer",
        "page_plan": "list",
    },
    "evidence_map": "!list",
    "render_record": {
        "source_files": "list",
        "template_file": "string",
        "layouts_used": "list",
        "fonts": "list",
        "exports": "list",
        "integrity": "object",
        "media": "list",
    },
}

# Item schemas for list-valued fields.  Used for validation hints and to make
# the field semantics explicit in the documentation.
ITEM_SCHEMAS = {
    "presentation.page_plan": {
        "index": "!integer",
        "title": "string",
        "claim": "!string",
        "layout": "string",
        "notes": "string",
        "refs": "list",
    },
    "evidence_map": {
        "page": "!integer",
        "claim": "!string",
        "refs": "list",
        "evidence_status": "string",
    },
    "render_record.exports": {
        "kind": "string",
        "path": "string",
        "ok": "boolean",
    },
    "render_record.fonts": {
        "role": "string",
        "name": "!string",
        "available": "boolean",
        "fallback": "string",
    },
    "change_log": {
        "ts": "!string",
        "code": "!string",
        "detail": "string",
        "page": "string",
    },
    "uncertainty": {
        "code": "!string",
        "page": "string",
        "detail": "!string",
    },
    "sources": {
        "path": "!string",
        "kind": "string",
        "note": "string",
    },
}

# Envelope fields written by the skill in addition to the contract fields.
ENVELOPE_KEYS = [
    "case_id", "status", "sources", "uncertainty", "change_log",
    "pending_human", "errors", "degradations", "runtime",
]

PATH_LEAF_SUFFIXES = ("_path", "_file")


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path):
    """Read a UTF-8 text file with graceful encoding fallbacks."""
    if not path or not os.path.isfile(path):
        return None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    return None


def write_json(path, obj):
    """Atomically write JSON; never leave a truncated file behind."""
    if not path:
        return False
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def load_json(path):
    """Load JSON tolerantly; return None on missing/empty/broken file."""
    text = read_text(path)
    if text is None:
        return None
    text = text.lstrip("\ufeff").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _normalise_contract(contract):
    """Coerce a user-provided output_contract into the schema dialect used here."""
    if isinstance(contract, dict):
        return copy.deepcopy(contract)
    if isinstance(contract, (list, tuple)):
        return {str(k): "any" for k in contract}
    if isinstance(contract, str) and contract:
        return {contract: "any"}
    return copy.deepcopy(DEFAULT_CONTRACT)


def discover_contract(case_json, task_text=""):
    """Return the contract to mirror.  Prefer case.json's own output_contract."""
    if isinstance(case_json, dict) and case_json.get("output_contract"):
        return _normalise_contract(case_json["output_contract"])
    return copy.deepcopy(DEFAULT_CONTRACT)


def materialize(node):
    """Recursively build typed placeholders from a contract schema."""
    if isinstance(node, dict):
        return {key: materialize(value) for key, value in node.items()}
    if isinstance(node, (list, tuple)):
        return []
    if isinstance(node, str):
        typ = node[1:] if node.startswith("!") else node
        if typ == "list":
            return []
        if typ == "object":
            return {}
        return None
    return None


def build_skeleton(case_id, contract):
    """Build a complete result.json skeleton: every contract field present."""
    result = {key: [] if key in ("sources", "uncertainty", "change_log",
                                 "pending_human", "errors", "degradations")
              else ({} if key == "runtime" else None)
              for key in ENVELOPE_KEYS}
    result["case_id"] = case_id or "unknown"
    result["status"] = "in_progress"
    result["runtime"] = {"started_at": utc_now(), "requests_used": 0}
    for key, value in materialize(contract).items():
        result[key] = value
    append_change(result, "bootstrap", "result skeleton created; all contract fields mirrored")
    return result


def deep_merge(base, patch):
    """Merge patch into base without dropping base keys. Lists are append-only
    for log-like fields and replaced otherwise. Returns a new merged object."""
    merged = copy.deepcopy(base) if isinstance(base, (dict, list)) else base
    if not isinstance(merged, dict) or not isinstance(patch, dict):
        return copy.deepcopy(patch) if patch is not None else merged
    for key, value in patch.items():
        if key not in merged:
            merged[key] = copy.deepcopy(value)
            continue
        if isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        elif isinstance(merged[key], list) and isinstance(value, list):
            if key in ("change_log", "uncertainty", "sources", "errors",
                       "degradations", "pending_human"):
                merged[key] = merged[key] + copy.deepcopy(value)
            else:
                merged[key] = copy.deepcopy(value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def append_change(result, code, detail="", page=None):
    entry = {"ts": utc_now(), "code": code, "detail": detail or ""}
    if page is not None:
        entry["page"] = page
    result.setdefault("change_log", [])
    result["change_log"].append(entry)
    return result


def walk_leaves(node, path="", required=False):
    """Yield (path, required, type_name) for every contract leaf."""
    if isinstance(node, dict):
        for key, value in node.items():
            child = "{}.{}".format(path, key) if path else key
            for item in walk_leaves(value, child, required):
                yield item
    elif isinstance(node, (list, tuple)):
        yield path, required, "list"
    elif isinstance(node, str):
        is_req = node.startswith("!")
        typ = node[1:] if is_req else node
        yield path, required or is_req, typ
    else:
        yield path, required, "any"


def get_path(obj, dotted):
    """Read a dotted path like 'presentation.pptx_path'."""
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _is_empty(value):
    if value is None:
        return True
    if isinstance(value, (list, dict, str)) and len(value) == 0:
        return True
    return False


def _check_file(path, base_dir):
    if not isinstance(path, str) or not path:
        return "not_applicable"
    candidates = [path]
    if base_dir and not os.path.isabs(path):
        candidates.append(os.path.join(base_dir, path))
    return "ok" if any(os.path.exists(c) for c in candidates) else "missing"


def _walk_actual_leaves(result, node, path="", base_dir="", findings=None):
    """Walk result under a contract subtree and collect per-leaf findings."""
    findings = [] if findings is None else findings
    for leaf_path, required, typ in walk_leaves(node, path):
        value = get_path(result, leaf_path)
        present = leaf_path in result if "." not in leaf_path else (
            get_path(result, leaf_path) is not None or _path_key_exists(result, leaf_path))
        # simpler presence check: walk nested keys directly
        present = _path_key_exists(result, leaf_path)
        verdict = "ok"
        problem = None
        if not present:
            verdict = "missing"
            problem = "field absent"
        elif required and _is_empty(value):
            verdict = "null_required" if value is None else "empty_required"
            problem = "required field is empty/null"
        elif typ in ("number", "integer") and value is not None and not isinstance(value, (int, float)):
            verdict = "type_mismatch"
            problem = "expected numeric, got {}".format(type(value).__name__)
        elif typ == "list" and value is not None and not isinstance(value, list):
            verdict = "type_mismatch"
            problem = "expected list, got {}".format(type(value).__name__)
        finding = {
            "path": leaf_path, "required": required, "type": typ,
            "present": present, "value": value, "verdict": verdict,
        }
        if problem:
            finding["problem"] = problem
        if typ == "string" and leaf_path.endswith(PATH_LEAF_SUFFIXES):
            finding["file_check"] = _check_file(value, base_dir)
        if typ == "list" and isinstance(value, list) and leaf_path in ITEM_SCHEMAS:
            finding["item_check"] = _check_items(leaf_path, value)
        findings.append(finding)
    return findings


def _path_key_exists(result, dotted):
    cur = result
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


def _check_items(leaf_path, items):
    schema = ITEM_SCHEMAS[leaf_path]
    required_keys = [k for k, v in schema.items()
                     if isinstance(v, str) and v.startswith("!")]
    bad = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            bad.append("item[{}] not an object".format(idx))
            continue
        missing = [k for k in required_keys if k not in item or _is_empty(item.get(k))]
        if missing:
            bad.append("item[{}] missing {}".format(idx, ",".join(missing)))
    return {"ok": not bad, "problems": bad}


def validate_result(result, contract, base_dir=""):
    """Validate result against contract. Returns (report, ok, error_count)."""
    findings = _walk_actual_leaves(result, contract, base_dir=base_dir)
    missing = [f for f in findings if f["verdict"] in ("missing", "null_required",
                                                        "empty_required", "type_mismatch")]
    file_missing = [f for f in findings if f.get("file_check") == "missing"]
    item_bad = [f for f in findings if f.get("item_check") and not f["item_check"]["ok"]]
    error_count = len(missing) + len(file_missing) + len(item_bad)
    ok = error_count == 0
    report = {
        "checked_at": utc_now(),
        "contract_fields": len(findings),
        "ok": ok,
        "errors": error_count,
        "missing_or_empty": missing,
        "missing_files": file_missing,
        "bad_items": item_bad,
        "findings": findings,
    }
    return report, ok, error_count
