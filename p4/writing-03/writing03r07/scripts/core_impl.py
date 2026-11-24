#!/usr/bin/env python3
"""Core implementation for the academic-format-adaptation skill.

Pure standard library (Python 3.8+). Every function degrades gracefully
instead of raising on: empty input directories, malformed JSON, bad file
encodings, missing/unknown parameters, oversized files and write failures.

The module is deterministic: it uses no randomness and sorts file discovery,
so two runs over the same inputs produce the same content-derived fields.
Only `change_log[].ts` and `runtime.started_at` are wall-clock metadata.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_TEXT_BYTES = 2 * 1024 * 1024        # per text material: 2 MiB
MAX_JSON_BYTES = 8 * 1024 * 1024        # per JSON document: 8 MiB
MAX_MATERIALS = 64                      # cap scanned files per run

TEXT_SUFFIXES = (
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tex", ".bib",
    ".json", ".xml", ".html", ".htm", ".log",
)
OFFICE_SUFFIXES = (".docx", ".doc", ".odt", ".pptx", ".ppt", ".xlsx", ".xls")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
SKIP_PREFIXES = (".", "~")

CASE_ID_KEYS = ("case_id", "caseId", "challenge_id", "id", "armiId")

RESERVED_FIELDS = (
    "status", "sources", "uncertainty", "pending_human",
    "change_log", "degradations", "errors", "runtime",
)

# Four-question structures required by this skill's topic (题目四问).
TOPIC_FIELDS = ("template_spec", "format_checklist", "content_integrity", "export_verification")

FALLBACK_CONTRACT = {
    "deliverable": None,
    "fact_source_map": [],
    "version_boundary": None,
}

PLACEHOLDER_RE = re.compile(
    r"\b(TODO|TBD|FIXME|PLACEHOLDER)\b|\bXXX\b|待补|待填", re.IGNORECASE
)

_STATUS_VALUES = ("partial", "complete", "failed")


def utcnow() -> str:
    """Wall-clock timestamp in ISO-8601 UTC (change_log / runtime metadata)."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------- #
# Robust input readers
# --------------------------------------------------------------------------- #
def read_text_safe(path: str, max_bytes: int = MAX_TEXT_BYTES):
    """Return (text, degradation_dict_or_None). Never raises.

    Reads with utf-8-sig (strips BOM) and errors="replace", so bad encodings
    degrade to replacement characters instead of crashing. Oversized files are
    skipped with a structured degradation record.
    """
    if not path:
        return None, {"path": path, "issue": "missing_path"}
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, {"path": path, "issue": "stat_error", "detail": str(exc)}
    if size > max_bytes:
        return None, {
            "path": path, "issue": "oversized",
            "size": size, "limit": max_bytes,
        }
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return fh.read(), None
    except (OSError, UnicodeError) as exc:
        return None, {"path": path, "issue": "read_error", "detail": str(exc)}


def load_json_safe(path: str, max_bytes: int = MAX_JSON_BYTES):
    """Return (data, error_or_None). Never raises. Tolerates BOM/empty/bad JSON."""
    text, err = read_text_safe(path, max_bytes)
    if err is not None:
        return None, err
    text = text.lstrip("\ufeff")
    if not text.strip():
        return None, {"path": path, "issue": "empty_json"}
    try:
        return json.loads(text), None
    except (ValueError, TypeError) as exc:
        return None, {"path": path, "issue": "bad_json", "detail": str(exc)}


def write_json_safe(path: str, obj) -> dict or None:
    """Atomically write JSON (tmp + os.replace). Returns an error dict or None."""
    if not path:
        return {"path": path, "issue": "missing_path"}
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
        return None
    except (OSError, TypeError, ValueError) as exc:
        return {"path": path, "issue": "write_error", "detail": str(exc)}


# --------------------------------------------------------------------------- #
# Input discovery
# --------------------------------------------------------------------------- #
def discover_files(root: str):
    """Return sorted [(abspath, relpath, size)]; missing dir -> []."""
    out = []
    if not root or not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(SKIP_PREFIXES))
        for name in filenames:
            if name.startswith(SKIP_PREFIXES):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            out.append((full, rel, size))
        if len(out) > MAX_MATERIALS * 4:
            break
    out.sort(key=lambda t: t[1].replace("\\", "/"))
    return out


def find_named(root: str, *names):
    """First file whose basename equals one of `names` (case-insensitive)."""
    wanted = {n.lower() for n in names}
    for full, rel, _size in discover_files(root):
        if os.path.basename(rel).lower() in wanted:
            return full
    return None


def classify_file(path: str) -> str:
    base = os.path.basename(path).lower()
    if base.endswith(".pdf"):
        return "pdf"
    if base.endswith(OFFICE_SUFFIXES):
        return "office"
    if base.endswith((".tex", ".bib")):
        return "latex"
    if base.endswith(TEXT_SUFFIXES):
        return "text"
    if base.endswith(IMAGE_SUFFIXES):
        return "image"
    return "other"


# --------------------------------------------------------------------------- #
# Contract handling
# --------------------------------------------------------------------------- #
def extract_case_id(case_data):
    """Best-effort case_id from common keys; None when unavailable."""
    if not isinstance(case_data, dict):
        return None
    for key in CASE_ID_KEYS:
        val = case_data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, (int, float)) and key in ("id",):
            return str(val)
    return None


def extract_case_id_from_text(text):
    """Fallback: parse `case_id` / `challenge_id` from prose (task.md)."""
    if not isinstance(text, str):
        return None
    m = re.search(r"\bcase[_ ]?id\s*[:=]\s*['\"]?([A-Za-z0-9_.\-]+)", text, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"\bchallenge[_ ]?id\s*[:=]\s*['\"]?([A-Za-z0-9_.\-]+)", text, re.IGNORECASE)
    return m.group(1) if m else None


def extract_output_contract(case_data):
    """Return case.json's `output_contract` if it is a non-empty dict, else None."""
    if isinstance(case_data, dict):
        oc = case_data.get("output_contract")
        if isinstance(oc, dict) and oc:
            return oc
    return None


# --------------------------------------------------------------------------- #
# Result skeleton + idempotent incremental merge
# --------------------------------------------------------------------------- #
def empty_uncertainty():
    return {
        "claims_requiring_confirmation": [],
        "conflicting_evidence": [],
        "material_gaps": [],
        "limitations": [],
    }


def build_skeleton(case_id, contract):
    """Build a minimal valid result.json.

    Copies every key of `output_contract` verbatim (values pass through
    untouched), then adds reserved fields and the topic's four-question
    structures. Missing values are represented as null/[] rather than dropped.
    """
    result = {"case_id": case_id or "unknown_case"}

    # 原样回填 output_contract 全部键；缺契约时用三段式兜底。
    source = contract if isinstance(contract, dict) and contract else FALLBACK_CONTRACT
    for key, val in source.items():
        result[key] = val if val is not None else ([] if _contract_key_is_list(key) else None)

    result.setdefault("status", "partial")
    result.setdefault("sources", [])
    result.setdefault("uncertainty", empty_uncertainty())
    result.setdefault("pending_human", [])
    result.setdefault("change_log", [])
    result.setdefault("degradations", [])
    result.setdefault("errors", [])
    result.setdefault("runtime", {})

    # 题目四问的核对结构：认得准/排得对/转得全/交得出。
    result.setdefault("template_spec", [])
    result.setdefault("format_checklist", [])
    result.setdefault("content_integrity", [])
    result.setdefault("export_verification", None)
    return result


def _contract_key_is_list(key: str) -> bool:
    return key in ("fact_source_map", "template_spec", "format_checklist", "content_integrity")


def record_change(result, action, detail):
    """Append one change_log entry. Returns the updated result."""
    if isinstance(result, dict):
        result.setdefault("change_log", []).append(
            {"ts": utcnow(), "action": action, "detail": detail}
        )
    return result


def _is_nonempty(val) -> bool:
    if val is None:
        return False
    if isinstance(val, (str, list, dict, tuple)):
        return len(val) > 0
    return True


def _item_identity(item):
    """Stable identity for list-of-dict merging (id/claim_id/item_id/unit_id)."""
    if isinstance(item, dict):
        for k in ("id", "claim_id", "item_id", "unit_id", "source_id"):
            if item.get(k) is not None:
                return (k, item[k])
    return None


def merge_lists(old, new):
    """Union of two lists: append new items not already present by identity."""
    out = list(old)
    seen = set()
    for item in out:
        ident = _item_identity(item)
        if ident is not None:
            seen.add(ident)
    for item in new:
        ident = _item_identity(item)
        if ident is not None and ident in seen:
            continue
        out.append(item)
        if ident is not None:
            seen.add(ident)
    return out


def merge_result(existing, incoming, preserve=True):
    """Idempotent incremental merge: never clobber non-empty existing values.

    Existing agent-written content wins; missing keys and empty values are
    filled from the incoming skeleton. Lists are unioned by item identity.
    """
    if not isinstance(existing, dict):
        return incoming
    if not isinstance(incoming, dict):
        return existing
    merged = dict(existing)
    for key, val in incoming.items():
        if key not in merged:
            merged[key] = val
        elif preserve and _is_nonempty(merged[key]):
            if isinstance(merged[key], list) and isinstance(val, list):
                merged[key] = merge_lists(merged[key], val)
            # else keep existing value
        else:
            merged[key] = val
    return merged


# --------------------------------------------------------------------------- #
# Text extraction for quote verifiability
# --------------------------------------------------------------------------- #
def extract_text_materials(root, work_dir=None, degradations=None,
                           skip_names=("case.json", "task.md")):
    """Extract readable text from text/latex materials into a corpus dict.

    Binary inputs (PDF/Office/image) are recorded as "external tool required"
    degradation records; they are not fabricated into text. Optionally dumps
    normalized .txt copies to work_dir/extracted/ for reproducible re-checking.
    Task-control files (case.json/task.md) are excluded from the evidence corpus.
    """
    degradations = degradations if degradations is not None else []
    corpus = {}
    for full, rel, _size in discover_files(root):
        if os.path.basename(rel).lower() in {n.lower() for n in skip_names}:
            continue
        kind = classify_file(full)
        if kind in ("pdf", "office", "image", "other"):
            degradations.append({
                "path": rel, "issue": "binary_material_requires_external_tool",
                "tool": "pdf-text-tool/office-renderer/ocr-tool",
            })
            continue
        text, err = read_text_safe(full)
        if err is not None:
            degradations.append(err)
            continue
        corpus[rel.replace("\\", "/")] = text
        if work_dir:
            dump = os.path.join(work_dir, "extracted", _safe_stem(rel) + ".txt")
            werr = write_json_safe_placeholder(dump, text)
            if werr:
                degradations.append(werr)
    return corpus, degradations


def _safe_stem(rel):
    return re.sub(r"[^A-Za-z0-9_.\-]+", "_", rel.replace("\\", "/"))


def write_json_safe_placeholder(path, text):
    """Write a plain text file (not JSON); returns error dict or None."""
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return None
    except OSError as exc:
        return {"path": path, "issue": "write_error", "detail": str(exc)}


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #
def verify_quotes(result, corpus):
    """Check each quote/text snippet locates verbatim in some material text."""
    checked = located = 0
    unlocated = []
    for field in ("fact_source_map", "template_spec", "format_checklist", "content_integrity"):
        entries = result.get(field)
        if not isinstance(entries, list):
            continue
        for idx, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            quote = entry.get("quote") or entry.get("text") or entry.get("expected")
            locator = entry.get("locator") or entry.get("source_locator")
            if not isinstance(quote, str) or not quote.strip():
                continue
            checked += 1
            hit = any(quote in text for text in corpus.values()) if corpus else False
            if hit:
                located += 1
            else:
                unlocated.append({
                    "field": field, "index": idx,
                    "quote": quote[:120], "locator": locator,
                    "issue": "quote_not_located_in_text_materials",
                })
    return {"quotes_checked": checked, "quotes_located": located, "unlocated": unlocated}


def find_placeholders(obj, path="result"):
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            hits.extend(find_placeholders(v, path + "." + str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits.extend(find_placeholders(v, "%s[%d]" % (path, i)))
    elif isinstance(obj, str):
        for m in PLACEHOLDER_RE.finditer(obj):
            hits.append("%s :: %s" % (path, m.group(0)))
    return hits


def verify_result(result, case_data, material_roots, max_placeholder_hits=200):
    """Return a verification report dict. `ok` is False only on hard errors."""
    report = {
        "ok": True,
        "errors": [],
        "warnings": [],
        "checks": {},
        "quotes": {"quotes_checked": 0, "quotes_located": 0, "unlocated": []},
    }
    if not isinstance(result, dict):
        report["errors"].append("result is not a JSON object")
        report["ok"] = False
        return report

    # 1. case_id
    if not isinstance(result.get("case_id"), str) or not result.get("case_id").strip():
        report["errors"].append("missing case_id")
    case_id = extract_case_id(case_data)
    if case_id and result.get("case_id") and result.get("case_id") != case_id:
        report["warnings"].append(
            "case_id mismatch: result=%r case=%r" % (result.get("case_id"), case_id)
        )

    # 2. output_contract fields present
    contract = extract_output_contract(case_data) or FALLBACK_CONTRACT
    missing = [k for k in contract if k not in result]
    if missing:
        report["errors"].append("missing output_contract fields: %s" % missing)
    report["checks"]["contract_keys"] = list(contract.keys())
    report["checks"]["contract_keys_missing"] = missing

    # 3. reserved fields present
    missing_reserved = [f for f in RESERVED_FIELDS if f not in result]
    if missing_reserved:
        report["errors"].append("missing reserved fields: %s" % missing_reserved)

    # 4. topic four-question fields present
    missing_topic = [f for f in TOPIC_FIELDS if f not in result]
    if missing_topic:
        report["warnings"].append("missing topic fields: %s" % missing_topic)

    # 5. status value
    if result.get("status") not in _STATUS_VALUES:
        report["warnings"].append("unexpected status: %r" % result.get("status"))

    # 6. placeholder markers (warnings only: heuristic)
    hits = find_placeholders(result)
    if hits:
        report["warnings"].append("placeholder markers found (%d): %s" % (len(hits), hits[:10]))
        if len(hits) > max_placeholder_hits:
            report["errors"].append("excessive placeholder markers (%d)" % len(hits))

    # 7. quote locatability against extracted text corpus
    corpus = {}
    for root in material_roots or []:
        c, _ = extract_text_materials(root)
        corpus.update(c)
    report["quotes"] = verify_quotes(result, corpus)
    report["checks"]["text_materials_scanned"] = len(corpus)
    if report["quotes"]["unlocated"]:
        report["warnings"].append(
            "unlocated quotes: %d of %d (mark in uncertainty.limitations)"
            % (len(report["quotes"]["unlocated"]), report["quotes"]["quotes_checked"])
        )

    report["ok"] = not report["errors"]
    return report


def sha1_hex(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()
