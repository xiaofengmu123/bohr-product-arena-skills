#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""crosslang.py — shared core library for the cross-lingual literature-search
Skill. Standard library only; deterministic and crash-safe.

Responsibilities:
  - tolerant text/JSON loading (UTF-8 with BOM, bad-encoding fallback, size cap)
  - input discovery (never assume fixed file names or keys)
  - output_contract extraction from case.json or task.md free text
  - case_id extraction
  - deep merge with log-style append for change_log
  - atomic result writing
  - result validation against contract + standard fields
  - coarse script detection used only as a *hint* (never as a language verdict)
"""

import json
import os
import re
import unicodedata

MAX_TEXT_BYTES = 2 * 1024 * 1024
STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}

# Fields that must exist on every deliverable, in addition to echoed contract
# fields. These mirror the runtime contract in task.md.
STANDARD_FIELDS = [
    "case_id",
    "status",
    "research_question",
    "terminology_table",
    "queries",
    "results",
    "evidence_map",
    "dedup",
    "missing",
    "conflicts",
    "sources",
    "uncertainty",
    "limitations",
    "pending_human",
    "errors",
    "degradations",
    "change_log",
    "runtime",
]


# --------------------------------------------------------------------------
# Tolerant I/O
# --------------------------------------------------------------------------


def read_text(path, max_bytes=MAX_TEXT_BYTES):
    """Return (text|None, error|None). Never raises on unreadable/bad files."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(max_bytes + 1)
    except OSError as exc:  # noqa: BLE001
        return None, "read_error:%s" % exc
    if len(raw) > max_bytes:
        return None, "too_large:%d" % len(raw)
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "bad_encoding_replaced"


def safe_json_load(path):
    """Return (obj|None, error|None)."""
    text, err = read_text(path)
    if text is None:
        return None, err
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001
        return None, "bad_json:%s" % exc


def atomic_write_json(path, obj):
    """Write JSON via temp file + os.replace so a crash never leaves a partial
    result on disk."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path


# --------------------------------------------------------------------------
# Input discovery — discover, then assume
# --------------------------------------------------------------------------


def discover_inputs(input_dir):
    """Return {'case': path|None, 'task': path|None, 'materials': [paths]}."""
    found = {"case": None, "task": None, "materials": []}
    if not os.path.isdir(input_dir):
        return found
    try:
        names = sorted(os.listdir(input_dir))
    except OSError:  # noqa: BLE001
        return found
    for name in names:
        full = os.path.join(input_dir, name)
        if os.path.isdir(full):
            continue
        low = name.lower()
        if low == "case.json" or low.endswith(".case.json"):
            if found["case"] is None:
                found["case"] = full
        elif low == "task.md" or (low.endswith(".md") and "task" in low):
            if found["task"] is None:
                found["task"] = full
    materials = os.path.join(input_dir, "materials")
    if os.path.isdir(materials):
        try:
            found["materials"] = [
                os.path.join(materials, n)
                for n in sorted(os.listdir(materials))
                if os.path.isfile(os.path.join(materials, n))
            ]
        except OSError:  # noqa: BLE001
            pass
    return found


def load_case(input_dir):
    """Return (found, case_dict, task_text, case_error)."""
    found = discover_inputs(input_dir)
    case = {}
    case_err = None
    if found["case"]:
        case, err = safe_json_load(found["case"])
        if case is None or not isinstance(case, dict):
            case, case_err = (case or {}), "bad_json_or_nonobject:%s" % err
    else:
        case_err = "case.json_not_found"
    task_text = ""
    if found["task"]:
        task_text, terr = read_text(found["task"])
        if task_text is None:
            case_err = (case_err or "") + " task.md_read_error:%s" % terr
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
            result = find_contract(value, _depth + 1)
            if result is not None:
                return result
    elif isinstance(obj, list) and _depth < 4:
        for value in obj:
            result = find_contract(value, _depth + 1)
            if result is not None:
                return result
    return None


def _extract_balanced(text, start):
    """Return the balanced JSON object whose '{' begins at index `start`."""
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
    for match in re.finditer(r"output_contract", text, re.I):
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
                return parsed
    return None


def find_case_id(case, task_text=""):
    """Extract case_id, preferring explicit case fields over challenge id."""
    if isinstance(case, dict):
        for key in ("case_id", "caseId", "case_identifier"):
            if case.get(key):
                return str(case[key])
    if task_text:
        m = re.search(r"case[_ -]?id[\"'':=\s]+([A-Za-z0-9_\-\.]+)", task_text, re.I)
        if m:
            return m.group(1)
        m = re.search(r"challenge[_ -]?id[\"'':=\s`]+([A-Za-z0-9_\-\.]+)", task_text, re.I)
        if m:
            return m.group(1)
    if isinstance(case, dict):
        for key in ("challenge_id", "challengeId", "id"):
            if case.get(key):
                return str(case[key])
    return "unknown_case"


def contract_top_fields(contract):
    """Return ordered top-level field names for a contract."""
    if isinstance(contract, dict):
        return list(contract.keys())
    if isinstance(contract, list):
        return [str(x) for x in contract if isinstance(x, str)]
    return []


def contract_skeleton(contract):
    """Echo every contract field with null/[] defaults, without renaming or
    dropping unknown keys."""
    if isinstance(contract, dict):
        out = {}
        for key, value in contract.items():
            if isinstance(value, list):
                out[key] = []
            elif isinstance(value, dict):
                out[key] = contract_skeleton(value)
            else:
                out[key] = None
        return out
    if isinstance(contract, list):
        return {str(x): [] for x in contract if isinstance(x, str)}
    return {}


# --------------------------------------------------------------------------
# Skeleton, merge, validation
# --------------------------------------------------------------------------


def build_skeleton(case, task_text, case_err, contract):
    """Build the first legal partial deliverable. Always returns a dict."""
    case_id = find_case_id(case, task_text)
    has_any = bool(case) or bool(task_text.strip())
    if not has_any:
        status = "input_error"
    else:
        status = "partial"
    skeleton = {}
    for key, value in contract_skeleton(contract).items():
        skeleton[key] = value
    skeleton.update({
        "case_id": case_id,
        "status": status,
        "research_question": None,
        "terminology_table": [],
        "queries": [],
        "results": [],
        "evidence_map": [],
        "dedup": [],
        "missing": [],
        "conflicts": [],
        "sources": [],
        "uncertainty": [],
        "limitations": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "change_log": [
            {
                "step": "init",
                "status": status,
                "note": "partial skeleton written; remaining phases fill it incrementally",
            }
        ],
        "runtime": {
            "budget_seconds": 900,
            "budget_requests": 64,
            "phase_plan": [
                "init<60s", "read_scope<180s", "terms<300s",
                "queries<600s", "identify<720s", "align<840s", "finalize<900s",
            ],
        },
    })
    if case_err:
        skeleton["errors"].append(
            {"field": "input", "kind": "parse_or_missing", "detail": case_err}
        )
        if not has_any:
            skeleton["limitations"].append(
                {"kind": "no_input", "detail": "no parseable case.json or task.md"}
            )
    return skeleton


def deep_merge(base, patch, append_keys=("change_log",)):
    """Recursive merge. Lists are replaced wholesale except log-style keys,
    which are appended. Dicts recurse."""
    for key, value in patch.items():
        if (
            key in append_keys
            and isinstance(base.get(key), list)
            and isinstance(value, list)
        ):
            base[key] = list(base[key]) + list(value)
        elif key in base and isinstance(base[key], dict) and isinstance(value, dict):
            deep_merge(base[key], value, append_keys)
        else:
            base[key] = value
    return base


def validate_result(obj, contract_fields=None, standard_fields=STANDARD_FIELDS):
    """Return a list of issue strings; empty list means clean."""
    issues = []
    if not isinstance(obj, dict):
        return ["not_a_dict"]
    if not obj.get("case_id"):
        issues.append("missing:case_id")
    if obj.get("status") not in STATUSES:
        issues.append("illegal:status")
    for field in standard_fields:
        if field not in obj:
            issues.append("missing_standard:%s" % field)
    for field in (contract_fields or []):
        if field not in obj:
            issues.append("missing_contract:%s" % field)
    return issues


# --------------------------------------------------------------------------
# Coarse script detection — hint only, never a language verdict
# --------------------------------------------------------------------------


def script_hint(text):
    """Return a coarse script label used only to flag candidates. Latin-script
    languages (pt/en/es/de/fr) are NOT separable by this check; the caller
    must use publisher/DOI/abstract evidence to assign the exact language."""
    if not text:
        return "empty"
    counts = {"han": 0, "kana": 0, "cyrillic": 0, "arabic": 0, "latin": 0}
    for char in text:
        if char.isspace() or char.isdigit():
            continue
        name = unicodedata.name(char, "")
        if "CJK" in name or "IDEOGRAPH" in name:
            counts["han"] += 1
        elif "HIRAGANA" in name or "KATAKANA" in name:
            counts["kana"] += 1
        elif "CYRILLIC" in name:
            counts["cyrillic"] += 1
        elif "ARABIC" in name:
            counts["arabic"] += 1
        elif "LATIN" in name:
            counts["latin"] += 1
    scripts = [k for k, v in counts.items() if v > 0]
    if not scripts:
        return "unknown"
    return "+".join(sorted(scripts, key=lambda k: -counts[k]))
