#!/usr/bin/env python3
"""Shared defensive runtime library for the systematic-review skill.

Design principle (robustness variant #鲁棒性):
  * No function raises on input/IO/parse failures. Risky operations return a
    (value, errors, degradations) triple; callers decide how to degrade.
  * Every failure path leads to a *minimal valid artifact*, never a crash and
    never a partially written JSON file (atomic replace via temp file).
  * Deterministic output: sorted keys, fixed field order, content-derived
    evidence ids, so the same input produces the same ids and layout twice.
  * Contract-preserving: output_contract keys discovered from case.json are
    filled in the skeleton and never silently dropped or renamed.

This module is standard library only and resolves its own location with
``__file__``, so it does not depend on the current working directory.
"""

import hashlib
import json
import os
import sys
import tempfile
import time

# ---------------------------------------------------------------------------
# Status vocabulary
# ---------------------------------------------------------------------------
STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
VALID_STATUSES = (STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR)

# ---------------------------------------------------------------------------
# Limits and encodings
# ---------------------------------------------------------------------------
DEFAULT_MAX_TEXT_BYTES = 2 * 1024 * 1024  # 2 MB per text file
MAX_INVENTORY_FILES = 5000                # cap pathological input trees
ENCODINGS = ("utf-8-sig", "gb18030")  # latin-1 is the lossless last resort below

TEXT_EXTS = {".md", ".txt", ".csv", ".json", ".jsonl", ".xml", ".html",
             ".tex", ".bib", ".yaml", ".yml", ".tsv", ".rst"}
PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".bmp"}
OFFICE_EXTS = {".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls"}

# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------
# The canonical systematic-review domain contract. Values double as type
# declarations used by ``fill_contract_value`` (None -> null, [] -> []).
DEFAULT_CONTRACT = {
    "review_question": None,
    "subquestions": [],
    "search_strategy": None,
    "inclusion_criteria": [],
    "exclusion_criteria": [],
    "screening_records": [],
    "prisma_flow": None,
    "quality_appraisal": [],
    "synthesis": None,
    "evidence_map": [],
    "uncertainty": None,
}

# Honest top-level fields appended on top of the case contract.
HONEST_KEYS = ("case_id", "status", "sources", "uncertainty", "change_log",
               "limitations", "errors", "degradations", "pending_human",
               "runtime_seconds")

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def ensure_dir(path):
    if not path:
        return []
    try:
        os.makedirs(path, exist_ok=True)
        return []
    except OSError as exc:
        return ["cannot create directory %s: %s" % (path, exc)]


def epoch_now():
    return int(time.time())


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def sha12(text):
    if not text:
        return "unknown"
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]


def evidence_id(locator):
    """Stable content-derived id so repeat runs produce identical ids."""
    return "ev-" + sha12(locator or "")


# ---------------------------------------------------------------------------
# Reading / writing (never raises)
# ---------------------------------------------------------------------------
def _read_bytes(path, max_bytes=DEFAULT_MAX_TEXT_BYTES):
    errors, degradations = [], []
    try:
        with open(path, "rb") as handle:
            raw = handle.read(max_bytes + 1)
    except (OSError, IOError) as exc:
        return None, ["cannot read %s: %s" % (path, exc)], degradations
    if len(raw) > max_bytes:
        degradations.append(
            "file %s exceeds %d bytes; reading first %d bytes"
            % (path, max_bytes, max_bytes))
        raw = raw[:max_bytes]
    return raw, errors, degradations


def load_text(path, max_bytes=DEFAULT_MAX_TEXT_BYTES):
    """Read a text file with encoding fallback; never raises.

    Returns (text, errors, degradations). ``text`` is None on total failure.
    """
    raw, errors, degradations = _read_bytes(path, max_bytes)
    if raw is None:
        return None, errors, degradations
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), errors, degradations
        except (UnicodeDecodeError, LookupError):
            continue
    # latin-1 is lossless but signals the file is neither UTF-8 nor GB18030.
    degradations.append(
        "file %s is not valid UTF-8/GB18030; decoded as latin-1" % path)
    return raw.decode("latin-1", errors="replace"), errors, degradations


def load_json(path, max_bytes=DEFAULT_MAX_TEXT_BYTES):
    """Load a JSON file with encoding fallback; never raises.

    Returns (object, errors, degradations). ``object`` is None on failure.
    """
    errors, degradations = [], []
    if not path or not os.path.exists(path):
        return None, ["file missing: %s" % (path,)], degradations
    text, errs, degs = load_text(path, max_bytes)
    errors.extend(errs)
    degradations.extend(degs)
    if text is None:
        return None, errors, degradations
    try:
        return json.loads(text), errors, degradations
    except (ValueError, TypeError) as exc:
        return None, errors + ["bad JSON in %s: %s" % (path, exc)], degradations


def read_jsonl(path, max_bytes=DEFAULT_MAX_TEXT_BYTES):
    """Read a JSON-lines file line by line; bad lines are skipped, never fatal."""
    records, errors, degradations = [], [], []
    text, errs, degs = load_text(path, max_bytes)
    errors.extend(errs)
    degradations.extend(degs)
    if text is None:
        return records, errors, degradations
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError) as exc:
            errors.append("bad JSONL line %d in %s: %s" % (lineno, path, exc))
            continue
        if isinstance(obj, dict):
            records.append(obj)
        else:
            degradations.append("non-object JSONL line %d in %s ignored" % (lineno, path))
    return records, errors, degradations


def write_json(path, obj):
    """Atomically write JSON (temp file + os.replace); never raises.

    Returns a list of error strings (empty on success).
    """
    if not path:
        return ["write_json: empty path"]
    parent = os.path.dirname(path) or "."
    errors = ensure_dir(parent)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=parent)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(obj, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
        tmp = None
    except (OSError, IOError, TypeError, ValueError) as exc:
        errors.append("write_json failed for %s: %s" % (path, exc))
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return errors


def write_text(path, text):
    """Write a UTF-8 text file; never raises. Returns a list of errors."""
    if not path:
        return ["write_text: empty path"]
    parent = os.path.dirname(path) or "."
    errors = ensure_dir(parent)
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text if text is not None else "")
    except (OSError, IOError) as exc:
        errors.append("write_text failed for %s: %s" % (path, exc))
    return errors


# ---------------------------------------------------------------------------
# Contract discovery and skeleton
# ---------------------------------------------------------------------------
def fill_contract_value(declaration):
    """Map a contract type declaration to a neutral empty value."""
    if isinstance(declaration, dict):
        decl_type = str(declaration.get("type", "")).lower()
        if "array" in decl_type or "list" in decl_type:
            return []
        if "object" in decl_type or "dict" in decl_type:
            return {}
        return None
    if isinstance(declaration, str):
        low = declaration.lower()
        if "array" in low or "list" in low or "[]" in low:
            return []
        if "object" in low or "dict" in low:
            return {}
        return None
    if isinstance(declaration, (list, tuple)):
        return []
    return None


def _find_output_contract(case_obj):
    if not isinstance(case_obj, dict):
        return None
    oc = case_obj.get("output_contract")
    if isinstance(oc, dict):
        return oc
    for key in ("task", "output", "spec", "requirements"):
        sub = case_obj.get(key)
        if isinstance(sub, dict):
            oc = sub.get("output_contract")
            if isinstance(oc, dict):
                return oc
    return None


def build_contract(case_obj):
    """Return {field: type_declaration} for the case's output_contract."""
    oc = _find_output_contract(case_obj)
    if isinstance(oc, dict) and oc:
        return {str(k): v for k, v in oc.items()}
    return dict(DEFAULT_CONTRACT)


def extract_case_id(case_obj):
    if not isinstance(case_obj, dict):
        return "unknown"
    for key in ("case_id", "caseId", "id", "armiId", "challenge_id", "challengeId"):
        value = case_obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value is not None and not isinstance(value, (dict, list)):
            return str(value)
    return "unknown"


def new_skeleton(case_id, contract, note=""):
    """Build a minimal valid result dict with every contract field filled."""
    result = {}
    for key, declaration in (contract or {}).items():
        result[str(key)] = fill_contract_value(declaration)
    result["case_id"] = case_id or "unknown"
    result["status"] = STATUS_PARTIAL
    result["sources"] = []
    result["uncertainty"] = {}
    result["change_log"] = [{
        "ts": iso_now(), "action": "skeleton",
        "detail": note or "initial partial skeleton",
    }]
    result["limitations"] = []
    result["errors"] = []
    result["degradations"] = []
    result["pending_human"] = []
    result["runtime_seconds"] = 0
    return result


def deep_merge(base, payload):
    """Recursive merge: dicts merge recursively, lists/scalars are replaced."""
    if not isinstance(base, dict):
        base = {}
    if not isinstance(payload, dict):
        return dict(base)
    out = dict(base)
    for key, value in payload.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# Input discovery
# ---------------------------------------------------------------------------
def _file_entry(path):
    entry = {"path": path, "kind": "other", "bytes": None, "oversized": False}
    try:
        entry["bytes"] = os.path.getsize(path)
    except OSError:
        pass
    if entry["bytes"] is not None and entry["bytes"] > DEFAULT_MAX_TEXT_BYTES:
        entry["oversized"] = True
    ext = os.path.splitext(path)[1].lower()
    if ext in PDF_EXTS:
        entry["kind"] = "pdf"
    elif ext in TEXT_EXTS:
        entry["kind"] = "text"
    elif ext in IMAGE_EXTS:
        entry["kind"] = "image"
    elif ext in OFFICE_EXTS:
        entry["kind"] = "office"
    return entry


def _add_material(path, inventory):
    entry = _file_entry(path)
    inventory["materials"].append(entry)
    if entry.get("oversized"):
        inventory["degradations"].append(
            "oversized file %s (%s bytes) exceeds %d-byte read limit; "
            "will be truncated when read" % (path, entry.get("bytes"),
                                             DEFAULT_MAX_TEXT_BYTES))


def _walk_dir(root, inventory):
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return
    for name in names:
        if inventory["total_files"] >= MAX_INVENTORY_FILES:
            inventory["degradations"].append(
                "file count capped at %d under %s" % (MAX_INVENTORY_FILES, root))
            return
        path = os.path.join(root, name)
        if os.path.isdir(path):
            _walk_dir(path, inventory)
        else:
            inventory["total_files"] += 1
            _add_material(path, inventory)


def _probe_encodings(inventory):
    """Decode text materials during discovery so bad encodings surface early."""
    for entry in inventory.get("materials", []):
        if not isinstance(entry, dict) or entry.get("kind") != "text":
            continue
        if entry.get("oversized"):
            continue
        text, errs, degs = load_text(entry.get("path"))
        inventory["degradations"].extend(degs)
        if errs:
            inventory["degradations"].append(
                "text material %s could not be decoded cleanly: %s"
                % (entry.get("path"), "; ".join(errs)))


def discover_input(input_dir, probe=True):
    """Scan the input directory without assuming fixed names; never raises."""
    inv = {
        "input_dir": input_dir,
        "exists": False,
        "total_files": 0,
        "case_json": None,
        "task_md": None,
        "materials": [],
        "errors": [],
        "degradations": [],
    }
    if not input_dir or not os.path.isdir(input_dir):
        inv["errors"].append("input dir missing or not a directory: %s" % (input_dir,))
        return inv
    inv["exists"] = True
    try:
        names = sorted(os.listdir(input_dir))
    except OSError as exc:
        inv["errors"].append("cannot list %s: %s" % (input_dir, exc))
        return inv
    for name in names:
        path = os.path.join(input_dir, name)
        if name == "case.json":
            inv["case_json"] = path
            inv["total_files"] += 1
        elif name == "task.md":
            inv["task_md"] = path
            inv["total_files"] += 1
        elif os.path.isdir(path):
            _walk_dir(path, inv)
        else:
            inv["total_files"] += 1
            _add_material(path, inv)
    if probe:
        _probe_encodings(inv)
    return inv


# ---------------------------------------------------------------------------
# Deterministic assembly helpers
# ---------------------------------------------------------------------------
def dedupe(items):
    seen, out = set(), []
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else repr(item)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def build_evidence_map(cards):
    out, seen = [], set()
    for card in cards:
        if not isinstance(card, dict):
            continue
        locator = card.get("locator") or ""
        eid = card.get("id") or evidence_id(locator or str(card.get("claim", "")))
        if eid in seen:
            continue
        seen.add(eid)
        out.append({
            "id": eid,
            "record_id": card.get("record_id"),
            "claim": card.get("claim"),
            "locator": locator,
            "quote": card.get("quote") or "",
            "strength": card.get("strength") or "inference",
        })
    return out


def compute_prisma(screening):
    records = [r for r in screening if isinstance(r, dict)]
    identified = len({r.get("record_id", i) for i, r in enumerate(records)})
    deduped = len({r.get("record_id", i) for i, r in enumerate(records) if not r.get("duplicate_of")})
    ta_screened = sum(1 for r in records if r.get("stage") == "title_abstract")
    ta_excluded = sum(1 for r in records
                      if r.get("stage") == "title_abstract" and r.get("decision") == "exclude")
    ft_screened = sum(1 for r in records if r.get("stage") == "full_text")
    ft_excluded = sum(1 for r in records
                      if r.get("stage") == "full_text" and r.get("decision") == "exclude")
    included = sum(1 for r in records
                   if r.get("stage") == "full_text" and r.get("decision") == "include")
    return {
        "identified": identified,
        "after_deduplication": deduped,
        "screened_title_abstract": ta_screened,
        "excluded_title_abstract": ta_excluded,
        "screened_full_text": ft_screened,
        "excluded_full_text": ft_excluded,
        "included": included,
    }


def build_sources(inventory):
    if not isinstance(inventory, dict):
        return []
    sources = []
    if inventory.get("case_json"):
        sources.append({"path": inventory["case_json"], "kind": "case_json"})
    if inventory.get("task_md"):
        sources.append({"path": inventory["task_md"], "kind": "task_md"})
    for material in inventory.get("materials", []):
        if isinstance(material, dict):
            sources.append({
                "path": material.get("path"),
                "kind": material.get("kind", "other"),
                "bytes": material.get("bytes"),
            })
    return sources


def determine_status(result):
    if result.get("status") in (STATUS_INSUFFICIENT, STATUS_INPUT_ERROR):
        return result["status"]
    has_review = bool(result.get("review_question"))
    has_screening = bool(result.get("screening_records"))
    has_evidence = bool(result.get("evidence_map"))
    synthesis = result.get("synthesis")
    has_conclusions = bool(isinstance(synthesis, dict) and synthesis.get("conclusions"))
    if has_review and has_screening and has_evidence and has_conclusions:
        return STATUS_OK
    if has_review or has_screening or has_evidence:
        return STATUS_PARTIAL
    return STATUS_INSUFFICIENT


# ---------------------------------------------------------------------------
# Module entry point (when invoked directly): print a small self-description.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    print(json.dumps({
        "module": "resultlib",
        "statuses": list(VALID_STATUSES),
        "default_contract_fields": sorted(DEFAULT_CONTRACT.keys()),
    }, ensure_ascii=False, indent=2))
