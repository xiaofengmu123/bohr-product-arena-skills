#!/usr/bin/env python3
"""resultlib.py — standard-library-only helpers for the evidence-centric
systematic-review Skill.

Responsibilities
----------------
* safe JSON / text / JSONL loading with encoding fallback (utf-8-sig ->
  gb18030 -> latin-1) so bad encodings never crash the run;
* input-directory discovery that does not assume fixed file names;
* output-contract discovery from ``case.json`` so every contract key is
  back-filled exactly once;
* skeleton construction (an early valid ``result.json``), deep merge and
* deterministic, reproducible ordering helpers.

No third-party dependencies. Paths are joined with ``os.path`` and are
therefore portable between the Linux runtime and any development machine.
"""

import copy
import hashlib
import json
import os
import re
import time

# --------------------------------------------------------------------------- #
# Status vocabulary
# --------------------------------------------------------------------------- #
STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
STATUSES = (STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR)

# --------------------------------------------------------------------------- #
# Default domain contract. These are the fields a complete systematic review
# must expose. ``case.json`` may declare additional ``output_contract`` keys;
# those are added on top and are never renamed or dropped.
# --------------------------------------------------------------------------- #
DEFAULT_CONTRACT = {
    "review_question": None,
    "subquestions": [],
    "search_strategy": {"query_blocks": []},
    "inclusion_criteria": [],
    "exclusion_criteria": [],
    "screening_records": [],
    "prisma_flow": {
        "identified": 0,
        "deduplicated": 0,
        "screened_title_abstract": 0,
        "excluded_title_abstract": 0,
        "screened_fulltext": 0,
        "excluded_fulltext": 0,
        "included": 0,
    },
    "quality_appraisal": [],
    "synthesis": {"conclusions": [], "conflicts": [], "gaps": []},
    "evidence_map": {
        "evidence": [],
        "by_source": {},
        "by_conclusion": {},
        "conflicts": [],
    },
    # honest / runtime fields
    "uncertainty": {},
    "status": STATUS_PARTIAL,
    "sources": [],
    "change_log": [],
    "limitations": [],
    "errors": [],
    "degradations": [],
    "pending_human": [],
    "runtime_seconds": None,
}

# Recognised material extensions. Order is not significant; discovery sorts.
MATERIAL_EXTS = (
    ".pdf", ".md", ".txt", ".csv", ".json", ".jsonl", ".xml", ".html",
    ".htm", ".tex", ".bib", ".ris", ".enw", ".png", ".jpg", ".jpeg",
    ".tif", ".tiff", ".docx", ".pptx", ".xlsx", ".odt",
)
CASE_NAMES = ("case.json", "case.jsonl", "case.yaml", "case.yml")
TASK_NAMES = ("task.md", "task.txt")

# --------------------------------------------------------------------------- #
# Encoding-safe IO
# --------------------------------------------------------------------------- #


def _decode_bytes(raw):
    """Decode bytes trying several encodings; never raise.

    Returns ``(text, errors)``. ``errors`` is a list of human-readable notes.
    """
    for enc in ("utf-8-sig", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), []
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "replace"), ["undecodable bytes replaced"]


def load_json(path):
    """Load a JSON file. Returns ``(obj, errors, degradations)``.

    ``obj`` is ``None`` when the file is missing or unparseable.
    """
    errors, degradations = [], []
    if not path or not os.path.exists(path):
        return None, ["file not found: %s" % (path,)], []
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
        text, errs = _decode_bytes(raw)
        errors.extend(errs)
        return json.loads(text), errors, degradations
    except Exception as exc:  # noqa: BLE001 - we must never crash
        return None, errors + ["%s: %s" % (path, type(exc).__name__)], degradations


def load_text(path, max_bytes=3 * 1024 * 1024):
    """Read a text material. Returns ``(text, errors, degradations)``."""
    errors, degradations = [], []
    if not path or not os.path.exists(path):
        return "", ["file not found: %s" % (path,)], []
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    if size > max_bytes:
        degradations.append("truncated %s (%d bytes > %d)" % (path, size, max_bytes))
    with open(path, "rb") as handle:
        raw = handle.read(max_bytes)
    text, errs = _decode_bytes(raw)
    errors.extend(errs)
    return text, errors, degradations


def load_jsonl(path):
    """Load a JSONL file tolerantly, skipping bad lines.

    Returns ``(records, errors, degradations)``. Only dict rows are kept.
    """
    records, errors, degradations = [], [], []
    if not path or not os.path.exists(path):
        return records, ["file not found: %s" % (path,)], []
    with open(path, "rb") as handle:
        raw = handle.read()
    text, errs = _decode_bytes(raw)
    errors.extend(errs)
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                records.append(obj)
            else:
                errors.append("%s:%d not a JSON object" % (path, lineno))
        except Exception as exc:  # noqa: BLE001
            errors.append("%s:%d %s" % (path, lineno, type(exc).__name__))
    return records, errors, degradations


def write_json(path, obj):
    """Write a JSON file deterministically (insertion order, UTF-8, 2-space)."""
    errors = []
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(obj, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except Exception as exc:  # noqa: BLE001
        errors.append("write %s failed: %s" % (path, type(exc).__name__))
    return errors


def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


def _classify(name, ext):
    if name in CASE_NAMES:
        return "case_json"
    if name in TASK_NAMES:
        return "task_md"
    if ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff"):
        return "image"
    if ext in (".docx", ".pptx", ".xlsx", ".odt"):
        return "office"
    if ext in MATERIAL_EXTS:
        return "material"
    return "other"


def discover_input(input_dir):
    """Walk the input directory and return a classification inventory."""
    inv = {
        "exists": False,
        "total_files": 0,
        "case_json": None,
        "task_md": None,
        "materials": [],
        "images": [],
        "office": [],
        "other": [],
        "oversized": [],
    }
    if not input_dir or not os.path.isdir(input_dir):
        return inv
    inv["exists"] = True
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        files.sort()
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            ext = os.path.splitext(name)[1].lower()
            kind = _classify(name, ext)
            entry = {"path": rel, "size": size}
            inv["total_files"] += 1
            if size > 2 * 1024 * 1024:
                inv["oversized"].append(entry)
            if kind == "case_json" and inv["case_json"] is None:
                inv["case_json"] = full
            elif kind == "task_md" and inv["task_md"] is None:
                inv["task_md"] = full
            elif kind == "material":
                inv["materials"].append(entry)
            elif kind == "image":
                inv["images"].append(entry)
            elif kind == "office":
                inv["office"].append(entry)
            elif kind == "other":
                inv["other"].append(entry)
    return inv


# --------------------------------------------------------------------------- #
# Contract / skeleton
# --------------------------------------------------------------------------- #


def extract_case_id(case_obj):
    if not isinstance(case_obj, dict):
        return ""
    for key in ("case_id", "caseId", "id", "armiId", "challenge_id"):
        value = case_obj.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _placeholder_for(spec):
    """Infer an empty placeholder from an output-contract type spec."""
    if isinstance(spec, str):
        token = spec.strip().lower()
        if token in ("list", "array", "[]"):
            return []
        if token in ("object", "dict", "map", "{}"):
            return {}
        return None
    if isinstance(spec, list):
        return []
    if isinstance(spec, dict):
        return {}
    return None


def discover_contract_spec(case_obj):
    """Return the raw ``output_contract`` spec and the extra keys it adds.

    ``extras`` maps each contract key not already present in DEFAULT_CONTRACT
    to an empty placeholder, preserving unknown keys without renaming them.
    """
    if not isinstance(case_obj, dict):
        return None, {}
    spec = case_obj.get("output_contract", case_obj.get("outputContract"))
    extras = {}
    if isinstance(spec, dict):
        for key, value in spec.items():
            if key not in DEFAULT_CONTRACT:
                extras[key] = _placeholder_for(value)
    elif isinstance(spec, list):
        for item in spec:
            key = item if isinstance(item, str) else None
            if key and key not in DEFAULT_CONTRACT:
                extras[key] = None
    return spec, extras


def new_skeleton(case_id, extras, note=""):
    """Build an early valid ``result.json`` object (status=partial)."""
    result = {"case_id": case_id or "unknown"}
    for key, value in DEFAULT_CONTRACT.items():
        result[key] = copy.deepcopy(value)
    for key, placeholder in (extras or {}).items():
        result[key] = copy.deepcopy(placeholder)
    result["status"] = STATUS_PARTIAL
    result["change_log"] = [
        {"ts": iso_now(), "action": "skeleton", "detail": note or "initial partial skeleton"}
    ]
    return result


def deep_merge(base, patch):
    """Recursively merge ``patch`` into ``base``; lists and scalars replace."""
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


# --------------------------------------------------------------------------- #
# Deterministic helpers
# --------------------------------------------------------------------------- #


def norm_text(text):
    """Normalise text for verbatim-quote comparison (collapse whitespace)."""
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def content_hash(text):
    return hashlib.sha1(norm_text(text).encode("utf-8")).hexdigest()[:12]


def sort_key(value):
    """Stable sort key usable on ids and strings."""
    return str(value or "")


def epoch_now():
    return time.time()


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
