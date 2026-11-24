#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py -- cross-lingual literature search: shared library.

The orchestration entry point is `main.py`; this module holds the pure,
standard-library helpers it (and `selftest.py`) use so that the CLI stays
small and the behaviour is testable without touching the live /input,
/app/output, /app/work paths.

Design principle: every conclusion in the final deliverable is a *projection*
of two structured intermediate artifacts:

  /app/work/plan.json     -- research question, language routing,
                             terminology table (term -> basis -> verification),
                             per-language queries (service, version,
                             original/translation pairs).
  /app/work/evidence.json -- append-only evidence ledger: each hit has a
                             locator + verbatim quote + language + version
                             + relevance basis; dedup decisions and
                             missing/conflict notes live here too.

`result.json` is derived from plan.json + evidence.json (via `assemble`),
carries their SHA-256 hashes, and can be rebuilt deterministically from the
same intermediates. Nothing important is written by hand into result.json.
"""

import hashlib
import json
import os
import re
import tempfile

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_FILE_SIZE = 2 * 1024 * 1024          # skip single files larger than 2 MB
ALLOWED_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")

# Standard fields always added to result.json (never renamed / removed).
STANDARD_FIELDS = [
    "status",
    "sources",
    "uncertainty",
    "change_log",
    "errors",
    "degradations",
    "pending_human",
    "runtime",
]

# Canonical semantic fields projected from the intermediate artifacts. They
# are added even when the case contract does not name them, because the task
# rubric requires them for independent verification.
SEMANTIC_FIELDS = [
    "research_question",
    "terminology_table",
    "queries",
    "results",
    "evidence_map",
    "dedup",
    "missing",
    "conflicts",
]

# Contract fields whose empty value should be a list rather than null.
LIST_SEMANTIC = {
    "results", "sources", "queries", "terminology_table", "missing",
    "conflicts", "evidence_map", "dedup", "candidates", "literature",
    "uncertainty", "errors", "degradations", "pending_human", "change_log",
}


# --------------------------------------------------------------------------
# Text / JSON loading (tolerant, never crashes)
# --------------------------------------------------------------------------


def read_text(path):
    """Return (text, error). UTF-8 first, then a lossless latin-1 fallback.

    A non-UTF-8 file is not an error that aborts the run: it is returned with
    an explanatory tag so the caller can record it in `degradations`.
    """
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return "", "stat_error:%s" % exc
    if size > MAX_FILE_SIZE:
        return "", "too_large:%d" % size
    for enc in ("utf-8-sig", "utf-8"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), None
        except (UnicodeDecodeError, OSError):
            continue
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        return raw.decode("latin-1"), "non_utf8_latin1_fallback"
    except OSError as exc:
        return "", "read_error:%s" % exc


def safe_json_load(path):
    """Return (obj_or_None, error). Bad JSON is reported, never raised."""
    text, err = read_text(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001 -- any JSON error is tolerable
        return None, "bad_json:%s" % exc


def load_json_if_exists(path):
    """Return (obj, err). Missing file yields ({}, None)."""
    if not os.path.isfile(path):
        return {}, None
    return safe_json_load(path)


# --------------------------------------------------------------------------
# Input discovery (do not assume fixed file names)
# --------------------------------------------------------------------------


def discover_inputs(input_dir):
    """Return {'case': path|None, 'task': path|None, 'materials': [paths]}."""
    found = {"case": None, "task": None, "materials": []}
    if not os.path.isdir(input_dir):
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
    matdir = os.path.join(input_dir, "materials")
    if os.path.isdir(matdir):
        try:
            found["materials"] = [
                os.path.join(matdir, n)
                for n in sorted(os.listdir(matdir))
                if os.path.isfile(os.path.join(matdir, n))
            ]
        except OSError:
            found["materials"] = []
    return found


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
    """Extract the balanced JSON object whose '{' begins at index `start`."""
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
                    return json.loads(fragment)
                except Exception:  # noqa: BLE001
                    continue
    return None


def contract_fields(contract):
    """Return the ordered top-level field-name list for a contract."""
    if isinstance(contract, dict):
        return list(contract.keys())
    if isinstance(contract, list):
        return [str(x) for x in contract if isinstance(x, str)]
    return []


def find_case_id(case, task_text=""):
    """Extract case_id, preferring explicit case fields over the challenge id."""
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


# --------------------------------------------------------------------------
# Merging and (deterministic) serialisation
# --------------------------------------------------------------------------


def deep_merge(base, patch):
    """Dicts merge recursively; lists and scalars are replaced wholesale."""
    if isinstance(base, dict) and isinstance(patch, dict):
        out = dict(base)
        for key, value in patch.items():
            if key in out:
                out[key] = deep_merge(out[key], value)
            else:
                out[key] = value
        return out
    return patch


def canonical_dump(obj):
    """Stable, whitespace-independent JSON string (sorted keys, UTF-8)."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2)


def sha256_of(obj):
    """Content hash of an object's canonical JSON form (audit anchor)."""
    return hashlib.sha256(canonical_dump(obj).encode("utf-8")).hexdigest()


def atomic_write_json(path, obj):
    """Write JSON via temp file + atomic replace; never leaves a half file."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=directory, text=True)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return True, None
    except Exception as exc:  # noqa: BLE001
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False, "write_error:%s" % exc


# --------------------------------------------------------------------------
# Skeleton construction (contract echoed, standard fields present)
# --------------------------------------------------------------------------


def empty_contract_value(field_name):
    """Choose an honest empty value: [] for list-like fields, else null."""
    if field_name in LIST_SEMANTIC:
        return []
    return None


def build_skeleton(case_id, contract, case_error=None):
    """Return a legal partial result skeleton.

    All contract fields are reserved (never renamed / dropped); standard
    fields are added; status starts as `partial` and is downgraded by the
    caller when the input is missing or corrupt.
    """
    result = {"case_id": case_id}
    for field in contract_fields(contract):
        result[field] = empty_contract_value(field)
    for field in STANDARD_FIELDS:
        result.setdefault(field, [] if field != "runtime" else 0)
    result.setdefault("status", "partial")
    result["change_log"] = []
    if case_error:
        result.setdefault("errors", []).append(case_error)
    return result


def empty_plan():
    """Intermediate plan artifact skeleton (the terminology/query ledger)."""
    return {
        "research_question": None,
        "source_language": None,
        "target_languages": [],
        "scope": None,
        "known_item_hint": None,
        "terminology_table": [],
        "queries": [],
    }


def empty_evidence():
    """Intermediate evidence artifact skeleton (the hit/claim ledger)."""
    return {
        "hits": [],
        "evidence_map": [],
        "dedup": [],
        "missing": [],
        "conflicts": [],
    }


# --------------------------------------------------------------------------
# Projection: plan.json + evidence.json -> result.json (deterministic)
# --------------------------------------------------------------------------


def _append_change_log(entries, op, summary, fields):
    """Append a monotonic, wall-clock-free change record."""
    entry = {"seq": len(entries) + 1, "op": op, "summary": summary,
             "fields": list(fields)}
    return entries + [entry]


def assemble(state, plan, evidence, current):
    """Rebuild result.json from the intermediates, preserving the contract.

    `current` carries the latest manually-patched standard fields (status,
    uncertainty, change_log, runtime, artifacts). Contract fields and the
    canonical semantic fields are projected from plan/evidence so the final
    deliverable is a faithful, hash-anchored view of the intermediates.
    """
    result = {"case_id": state.get("case_id", "unknown_case")}
    # Reserve and carry every contract field.
    for field in state.get("contract_fields", []):
        result[field] = current.get(field, empty_contract_value(field))
    # Standard fields: keep current values, defaulting to honest empties.
    for field in STANDARD_FIELDS:
        result[field] = current.get(field, [] if field != "runtime" else 0)
    # Project canonical semantic fields from the intermediates.
    plan_map = {
        "research_question": plan.get("research_question"),
        "terminology_table": plan.get("terminology_table", []),
        "queries": plan.get("queries", []),
    }
    for field in ("source_language", "target_languages", "scope", "known_item_hint"):
        if field not in result:
            result[field] = plan.get(field)
    evidence_map = {
        "results": evidence.get("hits", []),
        "evidence_map": evidence.get("evidence_map", []),
        "dedup": evidence.get("dedup", []),
        "missing": evidence.get("missing", []),
        "conflicts": evidence.get("conflicts", []),
    }
    for field in SEMANTIC_FIELDS:
        if field in plan_map:
            result[field] = plan_map[field]
        elif field in evidence_map:
            result[field] = evidence_map[field]
    # Audit anchors: prove result.json is derived from these intermediates.
    result["intermediate"] = {
        "plan": "/app/work/plan.json",
        "plan_sha256": sha256_of(plan),
        "evidence": "/app/work/evidence.json",
        "evidence_sha256": sha256_of(evidence),
        "state": "/app/work/state.json",
    }
    result["runtime"] = current.get("runtime", 0)
    return result


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def validate_result(result, contract):
    """Return a list of human-readable issues (empty list == valid)."""
    issues = []
    if not isinstance(result, dict):
        return ["result is not a JSON object"]
    if not result.get("case_id"):
        issues.append("case_id missing or empty")
    status = result.get("status")
    if status not in ALLOWED_STATUS:
        issues.append("status %r not in %s" % (status, list(ALLOWED_STATUS)))
    for field in contract_fields(contract):
        if field not in result:
            issues.append("contract field missing: %s" % field)
    for field in STANDARD_FIELDS:
        if field not in result:
            issues.append("standard field missing: %s" % field)
    # Light evidence checks: every non-empty hit must be traceable.
    for i, hit in enumerate(result.get("results", []) or []):
        if not isinstance(hit, dict):
            issues.append("results[%d] is not an object" % i)
            continue
        if not (hit.get("locator") or hit.get("source") or hit.get("url") or hit.get("doi")):
            issues.append("results[%d] missing locator/source" % i)
        if not hit.get("quote"):
            issues.append("results[%d] missing verbatim quote" % i)
    return issues
