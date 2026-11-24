#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py -- deterministic contract engine for cross-lingual known-item search.

Responsibilities
  * discover /input (case.json, task.md, materials/) without assuming fixed names
  * extract output_contract from case.json or inline from task.md
  * build an atomic, legal partial skeleton that backfills every contract field
  * deep-merge incremental patches and maintain a deterministic change_log
  * validate contract-field alignment and JSON legality

Standard library only. Deterministic: no wall-clock in result.json, no random
ordering. Every mutation goes through an ordered, counter-based change_log.
"""

import json
import math
import os
import re
import sys

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

ALLOWED_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")

# Always present regardless of whether an output_contract exists.
STANDARD_KEYS = [
    "case_id", "status", "sources", "uncertainty", "change_log",
    "errors", "degradations", "pending_human", "runtime",
]

# Semantic fallback fields used only when the input carries no output_contract.
DEFAULT_SEMANTIC_FIELDS = [
    "research_question", "terminology_table", "queries", "results",
    "evidence_map", "dedup", "missing", "conflicts",
]

_DEFAULT_BY_FIELD = {
    "research_question": {},
    "terminology_table": [],
    "queries": [],
    "results": [],
    "evidence_map": [],
    "dedup": [],
    "missing": [],
    "conflicts": [],
}


# --------------------------------------------------------------------------
# Text / JSON reading (BOM and bad-encoding tolerant, never crashes)
# --------------------------------------------------------------------------


def read_text(path, limit=2 * 1024 * 1024):
    """Return (text, warning). Skip files over `limit` bytes with a warning."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return "", "stat_error:%s" % exc
    if size > limit:
        return "", "too_large:%d>%d" % (size, limit)
    data = None
    last_err = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
            data = raw.decode(enc)
            last_err = None
            break
        except (UnicodeDecodeError, OSError) as exc:
            last_err = "%s:%s" % (enc, exc)
    if data is None:
        # Last resort: replace so the process never dies on undecodable bytes.
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
            data = raw.decode("utf-8", errors="replace")
            last_err = "replaced:" + (last_err or "")
        except OSError as exc:
            return "", "read_error:%s" % exc
    return data, (last_err or "")


def safe_json_load(path):
    """Return (obj, err_str). obj is None on failure; err_str '' on success."""
    text, warn = read_text(path)
    if not text and warn:
        return None, warn
    try:
        return json.loads(text), ""
    except ValueError as exc:
        return None, "bad_json:%s" % exc


# --------------------------------------------------------------------------
# Input discovery
# --------------------------------------------------------------------------


def discover_inputs(input_dir):
    found = {"case": None, "task": None, "materials": []}
    if not input_dir or not os.path.isdir(input_dir):
        return found
    try:
        names = sorted(os.listdir(input_dir))
    except OSError:
        return found
    for name in names:
        full = os.path.join(input_dir, name)
        if os.path.isdir(full):
            continue
        low = name.lower()
        if low == "case.json" or low.endswith(".case.json"):
            found["case"] = found["case"] or full
        elif low == "task.md" or (low.endswith(".md") and "task" in low):
            found["task"] = found["task"] or full
    mat = os.path.join(input_dir, "materials")
    if os.path.isdir(mat):
        found["materials"] = [
            os.path.join(mat, n) for n in sorted(os.listdir(mat))
            if os.path.isfile(os.path.join(mat, n))
        ]
    return found


def load_case(input_dir):
    """Return (found, case_dict, task_text, case_error)."""
    found = discover_inputs(input_dir)
    case, case_err = {}, None
    if found["case"]:
        obj, err = safe_json_load(found["case"])
        if obj is None:
            case, case_err = {}, "bad_json:" + err
        else:
            case = obj if isinstance(obj, dict) else {}
            if not isinstance(obj, dict):
                case_err = "case_not_object"
    else:
        case_err = "case.json_not_found"
    task_text = ""
    if found["task"]:
        txt, warn = read_text(found["task"])
        task_text = txt
        if warn:
            case_err = (case_err + " task.md:" + warn).strip() if case_err else ("task.md:" + warn)
    return found, case, task_text, case_err


# --------------------------------------------------------------------------
# Contract extraction
# --------------------------------------------------------------------------


def find_contract(obj, _depth=0):
    """Recursively locate output_contract / outputContract in a JSON object."""
    if isinstance(obj, dict):
        for key in ("output_contract", "outputContract", "output_contract_fields"):
            if key in obj and isinstance(obj[key], (dict, list)):
                return obj[key]
        for value in obj.values():
            hit = find_contract(value, _depth + 1)
            if hit is not None:
                return hit
    elif isinstance(obj, list) and _depth < 4:
        for value in obj:
            hit = find_contract(value, _depth + 1)
            if hit is not None:
                return hit
    return None


def _extract_balanced(text, start):
    depth, i, in_str, esc = 0, start, False, False
    while i < len(text):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        i += 1
    return None


def find_contract_from_text(text):
    if not text:
        return None
    for m in re.finditer(r"output_contract", text, re.I):
        idx = m.end()
        while idx < len(text) and text[idx] in " \t\r\n:=":
            idx += 1
        if idx < len(text) and text[idx] == "{":
            frag = _extract_balanced(text, idx)
            if frag:
                try:
                    return json.loads(frag)
                except ValueError:
                    continue
    return None


def find_case_id(case, task_text=""):
    if isinstance(case, dict):
        for key in ("case_id", "caseId", "case_identifier"):
            if case.get(key):
                return str(case[key])
    if task_text:
        m = re.search(r"case[_ -]?id[\"'':=\s]+([A-Za-z0-9_\-\.]+)", task_text, re.I)
        if m:
            return m.group(1)
    if isinstance(case, dict):
        for key in ("challenge_id", "challengeId", "id"):
            if case.get(key):
                return str(case[key])
    return "unknown_case"


def contract_top_fields(contract):
    """Ordered list of top-level field names for a contract (dict or list)."""
    if isinstance(contract, dict):
        return list(contract.keys())
    if isinstance(contract, list):
        out = []
        for item in contract:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                for key in item.keys():
                    out.append(key)
        return out
    return []


def _typename(hint):
    if hint is None:
        return "any"
    if isinstance(hint, dict):
        return "object"
    if isinstance(hint, list):
        return "array"
    if isinstance(hint, bool):
        return "boolean"
    if isinstance(hint, (int, float)):
        return "number"
    if isinstance(hint, str):
        return "string"
    return "any"


def _default_for(hint):
    if isinstance(hint, dict):
        return {}
    if isinstance(hint, list):
        return []
    return None


def _field_default(name, hint):
    if isinstance(hint, (dict, list)):
        return _default_for(hint)
    if name in _DEFAULT_BY_FIELD:
        return _DEFAULT_BY_FIELD[name]
    return None


# --------------------------------------------------------------------------
# Skeleton building
# --------------------------------------------------------------------------


def build_skeleton(case_id, contract, has_contract):
    result = {"case_id": case_id or "unknown_case", "status": "partial"}

    if has_contract and contract is not None:
        fields = contract_top_fields(contract)
        hint_map = contract if isinstance(contract, dict) else {}
        for f in fields:
            hint = hint_map.get(f) if isinstance(hint_map, dict) else None
            result[f] = _field_default(f, hint)
        schema = [
            {"name": f,
             "type": _typename(hint_map.get(f)) if isinstance(hint_map, dict) else "any",
             "backfilled_as": _field_default(f, hint_map.get(f)) if isinstance(hint_map, dict) else None}
            for f in fields
        ]
    else:
        for f in DEFAULT_SEMANTIC_FIELDS:
            result[f] = _DEFAULT_BY_FIELD[f]
        schema = [{"name": f, "type": _typename(_DEFAULT_BY_FIELD[f]),
                   "backfilled_as": _DEFAULT_BY_FIELD[f]} for f in DEFAULT_SEMANTIC_FIELDS]

    result["sources"] = []
    result["uncertainty"] = []
    result["change_log"] = []
    result["errors"] = []
    result["degradations"] = []
    result["pending_human"] = []
    result["runtime"] = {
        "budget_seconds": 900, "budget_requests": 64,
        "token_per_response": 16384,
        "note": "actual native runtime is recorded by the platform; budget is declared for determinism",
    }
    result["contract_schema"] = {
        "present": bool(has_contract),
        "aligned": True,
        "note": "each contract field is backfilled at top level; standard meta fields are always added",
        "fields": schema,
    }
    result["change_log"] = [{
        "seq": 0, "step": "init", "action": "skeleton_written",
        "fields": list(result.keys()), "status": "partial",
        "note": "legal partial skeleton on disk before any analysis",
    }]
    return result


# --------------------------------------------------------------------------
# JSON writing / merging
# --------------------------------------------------------------------------


def sanitize(value):
    """Replace non-finite floats with None so json.dumps(allow_nan=False) never fails."""
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def dumps_json(value):
    return json.dumps(sanitize(value), ensure_ascii=False, indent=2,
                      sort_keys=False, allow_nan=False)


def atomic_write_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(dumps_json(obj))
        fh.write("\n")
    os.replace(tmp, path)


def load_result(output_dir):
    path = os.path.join(output_dir, "result.json")
    if not os.path.isfile(path):
        return None, path
    obj, err = safe_json_load(path)
    if obj is None or not isinstance(obj, dict):
        return None, path
    return obj, path


def deep_merge(base, patch):
    for key, value in patch.items():
        if key.startswith("_") and key in ("_change", "_change_note", "_fields"):
            continue
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def apply_patch(base, patch):
    note = patch.pop("_change", None) or patch.pop("_change_note", None)
    fields = list(patch.keys())
    deep_merge(base, patch)
    logs = base.get("change_log")
    if not isinstance(logs, list):
        logs = []
        base["change_log"] = logs
    seq = max([e.get("seq", 0) for e in logs if isinstance(e, dict)] + [-1]) + 1
    entry = {"seq": seq, "action": "patch", "fields": fields,
             "status": base.get("status")}
    if note:
        entry["note"] = note
    logs.append(entry)
    return base


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def validate_result(obj, contract, has_contract):
    issues = []
    if not isinstance(obj, dict):
        return ["root_not_object"]
    if not obj.get("case_id"):
        issues.append("missing_case_id")
    if obj.get("status") not in ALLOWED_STATUS:
        issues.append("invalid_status:%r" % obj.get("status"))
    for key in ("sources", "uncertainty", "change_log", "pending_human"):
        if key not in obj:
            issues.append("missing_standard_field:" + key)
    if isinstance(obj.get("change_log"), list) and not obj["change_log"]:
        issues.append("change_log_empty")
    if has_contract and contract is not None:
        for f in contract_top_fields(contract):
            if f not in obj:
                issues.append("missing_contract_field:" + f)
    return issues


def summarize(obj):
    def count(key):
        v = obj.get(key)
        return len(v) if isinstance(v, (list, dict)) else (1 if v else 0)
    return {
        "case_id": obj.get("case_id"),
        "status": obj.get("status"),
        "results": count("results"),
        "sources": count("sources"),
        "uncertainty": count("uncertainty"),
        "pending_human": count("pending_human"),
        "errors": count("errors"),
        "degradations": count("degradations"),
        "change_log_entries": count("change_log"),
    }
