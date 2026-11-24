#!/usr/bin/env python3
"""Shared helpers for the systematic-review runtime package (standard library only).

All I/O is deterministic: same input files produce byte-identical JSON
(insertion-ordered dicts, ensure_ascii=False, two-space indent, trailing newline).
"""

import copy
import json
import os
import time

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
STATUSES = (STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR)

# Text decoding order: UTF-8 (with BOM) -> GB18030 (GBK superset) -> latin-1
# (never fails; produces mojibake but keeps the process alive and is recorded).
ENCODINGS = ("utf-8-sig", "gb18030", "latin-1")

# Materials we recognize while scanning the input directory.
MATERIAL_EXTS = {
    ".pdf", ".md", ".txt", ".csv", ".json", ".jsonl", ".xml", ".html", ".htm",
    ".tex", ".bib", ".ris", ".enw", ".docx", ".pptx", ".xlsx",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".tif", ".tiff",
}

MAX_FILE_BYTES = 2 * 1024 * 1024

# Canonical domain fields. Unknown contract fields are appended, never renamed.
DOMAIN_FIELDS = (
    "review_question", "subquestions", "search_strategy",
    "inclusion_criteria", "exclusion_criteria", "screening_records",
    "prisma_flow", "quality_appraisal", "synthesis", "evidence_map",
    "uncertainty",
)

FIELD_DEFAULTS = {
    "review_question": "",
    "subquestions": [],
    "search_strategy": {},
    "inclusion_criteria": [],
    "exclusion_criteria": [],
    "screening_records": [],
    "prisma_flow": {},
    "quality_appraisal": [],
    "synthesis": {},
    "evidence_map": [],
    "uncertainty": {},
}

# Honest bookkeeping fields appended after the contract fields.
HONEST_DEFAULTS = {
    "status": STATUS_PARTIAL,
    "sources": [],
    "change_log": [],
    "limitations": [],
    "errors": [],
    "degradations": [],
    "pending_human": [],
    "runtime_seconds": 0.0,
}


def epoch_now():
    return time.time()


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)
    return path


def decode_text(raw, path=""):
    """Decode bytes with graceful fallback. Returns (text, errors, degradations)."""
    errs, degs = [], []
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), errs, degs
        except (UnicodeDecodeError, LookupError):
            continue
    text = raw.decode("utf-8", "replace")
    degs.append("lossy decode (non UTF-8/GB18030 text): %s" % path)
    return text, errs, degs


def load_json(path):
    """Load one JSON document. Returns (obj, errors, degradations)."""
    if not path or not os.path.isfile(path):
        return None, ["missing file: %s" % path], []
    try:
        raw = open(path, "rb").read()
    except OSError as exc:
        return None, ["cannot read %s: %s" % (path, exc)], []
    text, errs, degs = decode_text(raw, path)
    try:
        obj = json.loads(text)
    except Exception as exc:  # noqa: BLE001 - keep running on any malformed input
        return None, ["bad JSON in %s: %s" % (path, exc)], degs
    return obj, errs, degs


def load_jsonl(path):
    """Load a JSON-lines file. Returns (list, errors, degradations)."""
    if not path or not os.path.isfile(path):
        return None, ["missing file: %s" % path], []
    try:
        raw = open(path, "rb").read()
    except OSError as exc:
        return None, ["cannot read %s: %s" % (path, exc)], []
    text, errs, degs = decode_text(raw, path)
    out = []
    for i, line in enumerate(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception as exc:  # noqa: BLE001
            errs.append("bad JSONL line %d in %s: %s" % (i + 1, path, exc))
    return out, errs, degs


def write_json(path, obj):
    """Deterministically write a JSON document. Returns list of errors."""
    ensure_dir(os.path.dirname(path))
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(obj, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        return []
    except OSError as exc:
        return ["cannot write %s: %s" % (path, exc)]


def deep_merge(base, patch):
    """Recursively merge ``patch`` into ``base`` (dicts merge, other types replace)."""
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return copy.deepcopy(patch)
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def discover_input(input_dir):
    """Scan an input directory without assuming fixed names or keys."""
    inv = {
        "exists": False, "dir": input_dir, "total_files": 0,
        "case_json": None, "task_md": None, "materials": [], "other": [],
    }
    if not input_dir or not os.path.isdir(input_dir):
        return inv
    inv["exists"] = True
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        files.sort()
        for fn in files:
            path = os.path.join(root, fn)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            rel = os.path.relpath(path, input_dir)
            entry = {"path": path, "rel": rel, "size": size,
                     "oversized": size > MAX_FILE_BYTES}
            inv["total_files"] += 1
            base = fn.lower()
            if base == "case.json":
                inv["case_json"] = path
            elif base == "task.md":
                inv["task_md"] = path
            elif os.path.splitext(base)[1] in MATERIAL_EXTS:
                inv["materials"].append(entry)
            else:
                inv["other"].append(entry)
    return inv


def extract_case_id(obj):
    """Find the case identifier across common key spellings."""
    for key in ("case_id", "caseId", "id", "armiId", "challenge_id"):
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return ""


def default_for_spec(spec):
    """Infer a safe default value from an output_contract field spec."""
    if isinstance(spec, str):
        lowered = spec.lower()
        if lowered in ("array", "list"):
            return []
        if lowered in ("object", "dict", "map"):
            return {}
        if lowered in ("string", "str", "text"):
            return ""
        return None
    if isinstance(spec, list):
        return []
    if isinstance(spec, dict):
        if "default" in spec:
            return copy.deepcopy(spec["default"])
        if isinstance(spec.get("type"), str):
            return default_for_spec(spec["type"])
        return {}
    return None


def build_contract(case_obj):
    """Return an ordered field->default map: domain fields + output_contract fields."""
    contract = {}
    for name in DOMAIN_FIELDS:
        contract[name] = copy.deepcopy(FIELD_DEFAULTS.get(name))
    oc = case_obj.get("output_contract") if isinstance(case_obj, dict) else None
    if isinstance(oc, dict):
        for key, spec in oc.items():
            if isinstance(key, str) and key:
                contract[key] = default_for_spec(spec)
    elif isinstance(oc, list):
        for item in oc:
            if isinstance(item, str) and item:
                contract[item] = None
            elif isinstance(item, dict):
                name = item.get("name") or item.get("key") or item.get("field")
                if isinstance(name, str) and name:
                    contract[name] = default_for_spec(
                        item.get("type") or item.get("default") or item)
    return contract


def new_skeleton(case_id, contract, note=None, status=STATUS_PARTIAL):
    """Build a valid partial result before any analysis is attempted."""
    result = {"case_id": case_id or "unknown"}
    for name, default in contract.items():
        result[name] = copy.deepcopy(default)
    for name, default in HONEST_DEFAULTS.items():
        result[name] = copy.deepcopy(default)
    result["status"] = status
    result["change_log"] = [{
        "ts": iso_now(), "action": "skeleton", "detail": note or "initial skeleton",
    }]
    return result


def decide_status(result):
    """Honest status inference from what is actually assembled."""
    has_q = bool(result.get("review_question"))
    has_sub = bool(result.get("subquestions"))
    has_crit = bool(result.get("inclusion_criteria")) or bool(result.get("exclusion_criteria"))
    has_screen = bool(result.get("screening_records"))
    has_concl = bool((result.get("synthesis") or {}).get("conclusions"))
    if has_q and has_sub and has_crit and has_screen and has_concl:
        return STATUS_OK
    if not has_q and not has_screen and not has_concl:
        if any("case.json" in (err or "") for err in result.get("errors", [])):
            return STATUS_INPUT_ERROR
        return STATUS_INSUFFICIENT
    return STATUS_PARTIAL


def compute_prisma(records):
    """Deterministically recompute PRISMA counts from screening records."""
    out = {
        "total_records": 0, "duplicates_removed": 0,
        "screened_title_abstract": 0, "excluded_title_abstract": 0,
        "screened_fulltext": 0, "excluded_fulltext": 0,
        "not_retrieved": 0, "included": 0, "unclear": 0,
    }
    for record in records:
        if not isinstance(record, dict):
            continue
        out["total_records"] += 1
        if record.get("duplicate_of"):
            out["duplicates_removed"] += 1
            continue
        stage = (record.get("stage") or "title_abstract").lower()
        decision = (record.get("decision") or "").lower()
        if stage in ("title_abstract", "title", "abstract", "tiab"):
            out["screened_title_abstract"] += 1
            if decision == "exclude":
                out["excluded_title_abstract"] += 1
        elif stage in ("full_text", "fulltext", "full"):
            out["screened_fulltext"] += 1
            if decision == "exclude":
                out["excluded_fulltext"] += 1
            elif decision == "not_retrieved":
                out["not_retrieved"] += 1
            elif decision == "include":
                out["included"] += 1
            elif decision == "unclear":
                out["unclear"] += 1
    return out
