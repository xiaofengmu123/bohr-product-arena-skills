#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — shared, standard-library-only helpers for the cross-lingual
literature search Skill. ``main.py`` and ``selftest.py`` import from here.

Responsibilities
  * tolerant text / JSON reading (UTF-8 BOM, bad encodings, oversize files)
  * input discovery (case.json, task.md, materials) without fixed-name assumptions
  * output_contract extraction from JSON objects or free text
  * case_id resolution
  * atomic JSON writes and deterministic deep-merge patches
  * result validation against the resolved contract

No third-party dependencies. No randomness: all list orderings derive from
sorted discovery so repeated runs are stable.
"""

import json
import os
import re
import tempfile
import time

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_FILE_BYTES = 2 * 1024 * 1024
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")

STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")

# Top-level keys that should be initialised as a list when a contract does not
# supply an explicit value.
LIST_KEYS = {
    "terminology_table", "queries", "results", "evidence_map", "missing",
    "conflicts", "sources", "uncertainty", "pending_human", "errors",
    "degradations", "change_log",
}
# Top-level keys initialised as a dict.
DICT_KEYS = {"research_question", "dedup", "runtime"}

# Default "main result" contract used when the case provides no output_contract.
DEFAULT_CONTRACT = {
    "research_question": {
        "question": None,
        "mode": None,                 # known_item | topic_discovery | undetermined
        "source_language": None,
        "target_languages": [],
        "scope": None,
    },
    "terminology_table": [],
    "queries": [],
    "results": [],
    "evidence_map": [],
    "dedup": {"groups": [], "basis": [], "notes": []},
    "missing": [],
    "conflicts": [],
}


# --------------------------------------------------------------------------
# Text and JSON reading (tolerant)
# --------------------------------------------------------------------------

def read_text(path):
    """Return ``(text_or_None, error_or_None)`` for a UTF-8 text file.

    Tries several decoders in order; oversize files are refused and reported
    rather than read into memory.
    """
    if not os.path.isfile(path):
        return None, "not_found"
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, "stat_error:%s" % exc
    if size > MAX_FILE_BYTES:
        return None, "too_large:%d" % size
    raw = None
    last_err = None
    for enc in ENCODINGS:
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
            return raw.decode(enc), None
        except UnicodeError as exc:  # try next decoder
            last_err = exc
        except OSError as exc:
            return None, "read_error:%s" % exc
    return None, "decode_error:%s" % last_err


def safe_json_load(path):
    """Return ``(obj_or_None, error_or_None)``."""
    text, err = read_text(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001 - any parse failure degrades cleanly
        return None, "bad_json:%s" % exc


# --------------------------------------------------------------------------
# Input discovery (do not assume fixed file names)
# --------------------------------------------------------------------------

def discover_inputs(input_dir):
    """Return ``{'case', 'task', 'materials', 'input_dir'}``.

    ``case``/``task`` are the first matching paths; ``materials`` is every other
    file found anywhere under ``input_dir`` (including ``materials/``). All
    listings are sorted for deterministic ordering.
    """
    found = {"case": None, "task": None, "materials": [], "input_dir": input_dir}
    if not os.path.isdir(input_dir):
        return found

    def walk(base):
        out = []
        try:
            names = sorted(os.listdir(base))
        except OSError:
            return out
        for name in names:
            full = os.path.join(base, name)
            if os.path.isdir(full):
                out.extend(walk(full))
            else:
                out.append(full)
        return out

    for full in walk(input_dir):
        name = os.path.basename(full).lower()
        if name == "case.json" or name.endswith(".case.json"):
            found["case"] = found["case"] or full
        elif name == "task.md" or (name.endswith(".md") and "task" in name):
            found["task"] = found["task"] or full
        else:
            found["materials"].append(full)
    return found


def load_case(input_dir):
    """Return ``(found, case_dict, task_text, errors_list)``."""
    found = discover_inputs(input_dir)
    case = {}
    errors = []
    if found["case"]:
        case, err = safe_json_load(found["case"])
        if case is None:
            errors.append("bad_json:%s" % err)
        elif not isinstance(case, dict):
            errors.append("case_not_object")
            case = {}
    else:
        errors.append("case.json_not_found")
    task_text = ""
    if found["task"]:
        text, err = read_text(found["task"])
        if err:
            errors.append("task.md_read_error:%s" % err)
        else:
            task_text = text or ""
    return found, case, task_text, errors


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
            found = find_contract(value, _depth + 1)
            if found is not None:
                return found
    elif isinstance(obj, list) and _depth < 4:
        for value in obj:
            found = find_contract(value, _depth + 1)
            if found is not None:
                return found
    return None


def _extract_balanced(text, start):
    """Extract the balanced JSON object whose ``{`` begins at index ``start``."""
    depth = 0
    i = start
    in_string = False
    escaped = False
    while i < len(text):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        else:
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        i += 1
    return None


def find_contract_from_text(text):
    """Locate an inline output_contract JSON object in free text (task.md)."""
    if not text:
        return None
    for match in re.finditer(r"output[_-]?contract", text, re.I):
        idx = match.end()
        while idx < len(text) and text[idx] in " \t\r\n:=":
            idx += 1
        if idx < len(text) and text[idx] == "{":
            fragment = _extract_balanced(text, idx)
            if fragment:
                try:
                    parsed = json.loads(fragment)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(parsed, dict):
                    return parsed
    return None


def resolve_contract(case, task_text):
    """Return the dict of required top-level fields for the result.

    Priority: explicit contract in case.json, then inline contract in task.md,
    then the default contract. A list contract is treated as field names.
    """
    contract = find_contract(case)
    if contract is None:
        contract = find_contract_from_text(task_text)
    if isinstance(contract, dict) and contract:
        return dict(contract)
    if isinstance(contract, list):
        names = [str(x) for x in contract if isinstance(x, str)]
        if names:
            return {name: None for name in names}
    return dict(DEFAULT_CONTRACT)


# --------------------------------------------------------------------------
# case_id resolution
# --------------------------------------------------------------------------

def find_case_id(case, task_text=""):
    """Prefer explicit case fields, then task.md, then challenge id fallback."""
    if isinstance(case, dict):
        for key in ("case_id", "caseId", "case_identifier"):
            value = case.get(key)
            if value:
                return str(value)
    if task_text:
        match = re.search(r"case[_ -]?id[\"'':=\s]+([A-Za-z0-9_\-\.]+)", task_text, re.I)
        if match:
            return match.group(1)
    if isinstance(case, dict):
        for key in ("challenge_id", "challengeId", "id"):
            value = case.get(key)
            if value:
                return str(value)
    return "unknown_case"


# --------------------------------------------------------------------------
# Skeleton construction
# --------------------------------------------------------------------------

def default_value_for(key):
    if key in LIST_KEYS:
        return []
    if key in DICT_KEYS:
        return {}
    return None


def standard_fields():
    """Fields that every result.json carries in addition to the contract."""
    return {
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "schema_version": "2.0.0",
        "runtime": {
            "started_at": None,
            "finished_at": None,
            "last_patch_at": None,
            "model_requests_used": None,
            "budget_seconds": 900,
            "budget_requests": 64,
        },
    }


def build_skeleton(case_id, contract):
    """Assemble a legal partial result skeleton in a stable key order.

    Contract keys are preserved exactly; a non-null contract value is kept
    as-is (原样回填), while a null/missing value receives ``null`` (or ``[]``
    for list-typed keys, ``{}`` for dict-typed keys). Standard fields are then
    added without overriding anything the contract already supplied.
    """
    result = {}
    result["case_id"] = case_id
    result["status"] = "partial"
    for key, value in contract.items():
        if key in ("case_id", "status"):
            continue
        if value is not None:
            result[key] = value
        else:
            result[key] = default_value_for(key)
    for key, value in standard_fields().items():
        if key in ("case_id", "status"):
            continue
        result.setdefault(key, value)
    return result


# --------------------------------------------------------------------------
# Deterministic writes and patches
# --------------------------------------------------------------------------

def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def atomic_write_json(path, obj):
    """Write JSON via a temp file + atomic replace so no half file persists."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".result-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def deep_merge(base, patch):
    """Merge ``patch`` into ``base``: dicts merge recursively, lists and scalars
    are replaced wholesale (deterministic patch semantics)."""
    if isinstance(patch, dict) and isinstance(base, dict):
        out = dict(base)
        for key, value in patch.items():
            out[key] = deep_merge(out[key], value) if key in out else value
        return out
    return patch


def read_result(path):
    obj, err = safe_json_load(path)
    if err or not isinstance(obj, dict):
        return None, err or "result_not_object"
    return obj, None


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def validate_result(result, contract_keys=None):
    """Return a list of issue strings; empty list means the result is legal."""
    issues = []
    if not isinstance(result, dict):
        return ["result_not_object"]
    if not result.get("case_id"):
        issues.append("missing:case_id")
    if result.get("status") not in STATUS_VALUES:
        issues.append("invalid:status:%r" % result.get("status"))
    keys = contract_keys or list(DEFAULT_CONTRACT.keys())
    for key in keys:
        if key not in result:
            issues.append("missing_contract_field:%s" % key)
    for key in ("sources", "uncertainty", "change_log"):
        if key not in result:
            issues.append("missing_standard_field:%s" % key)
    return issues
