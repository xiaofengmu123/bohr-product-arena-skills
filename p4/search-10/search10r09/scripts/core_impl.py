#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — cross-lingual literature search skill: pure-logic helpers.

Standard library only. Holds input discovery, contract extraction, safe
text/JSON loading, deep merge, atomic write, language/version helpers and
deterministic utilities. main.py and selftest.py import this module; nothing
else is required.
"""

import json
import os
import re
import time
import unicodedata

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB per text file

STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")
VERSION_VOCAB = (
    "original", "full_translation", "bilingual",
    "abstract_only", "duplicate", "undetermined",
)
LANGUAGE_VOCAB = ("pt", "en", "bilingual", "undetermined")

# Standard fields every result carries even when a case contract exists.
STANDARD_FIELDS = (
    "case_id", "status", "sources", "uncertainty", "change_log",
    "pending_human", "errors", "degradations", "runtime",
)


def utc_now():
    """Current UTC timestamp in ISO-8601 second precision."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------
# Safe file / text / JSON loading
# --------------------------------------------------------------------------


def read_text(path):
    """Read `path` as text trying several encodings; return (text, encoding).

    Never crashes on broken encoding: the final fallback decodes with
    errors=replace. Raises ValueError when the file exceeds MAX_FILE_BYTES.
    """
    size = os.path.getsize(path) if os.path.exists(path) else 0
    if size > MAX_FILE_BYTES:
        raise ValueError("file_too_large:%d" % size)
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def safe_json_load(path):
    """Load JSON from `path`; on any failure return (None, message)."""
    try:
        text, _enc = read_text(path)
    except Exception as exc:  # noqa: BLE001
        return None, "read_error:%s" % exc
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001
        return None, "bad_json:%s" % exc


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
    materials = os.path.join(input_dir, "materials")
    if os.path.isdir(materials):
        found["materials"] = [
            os.path.join(materials, n)
            for n in sorted(os.listdir(materials))
            if os.path.isfile(os.path.join(materials, n))
        ]
    return found


def load_case(input_dir):
    """Return (found, case_dict, task_text, case_error)."""
    found = discover_inputs(input_dir)
    case = {}
    case_err = None
    if found["case"]:
        case, err = safe_json_load(found["case"])
        if case is None:
            case, case_err = {}, "bad_json:%s" % err
    else:
        case_err = "case.json_not_found"
    task_text = ""
    if found["task"]:
        try:
            task_text, _enc = read_text(found["task"])
        except Exception as exc:  # noqa: BLE001
            case_err = (case_err or "") + " task.md_read_error:%s" % exc
    return found, case, task_text, case_err


# --------------------------------------------------------------------------
# Contract extraction (output_contract may live in case.json or task.md)
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
    if isinstance(case, dict):
        for key in ("challenge_id", "challengeId", "id"):
            if case.get(key):
                return str(case[key])
    return "unknown_case"


def contract_fields(contract):
    """Return ordered list of top-level field names for a contract."""
    if isinstance(contract, dict):
        return list(contract.keys())
    if isinstance(contract, list):
        return [k for k in contract if isinstance(k, str)]
    return []


# --------------------------------------------------------------------------
# Skeleton / result construction
# --------------------------------------------------------------------------


def build_skeleton(case_id, contract, found, case_err, now=None):
    """Build the legal partial result skeleton (no search yet)."""
    now = now or utc_now()
    found = found or {}
    errors = []
    degradations = []
    missing_case = not found.get("case")
    status = "input_error" if missing_case else "partial"
    if case_err:
        errors.append(str(case_err))
        if "too_large" in str(case_err):
            degradations.append(str(case_err))

    result = {
        "case_id": case_id,
        "status": status,
        "research_question": {
            "question": None,
            "source_language": None,
            "target_languages": [],
            "scope": None,
            "known_item": None,
        },
        "terminology_table": [],
        "queries": [],
        "works": [],
        "results": [],
        "dedup": [],
        "evidence_map": [],
        "missing": [],
        "conflicts": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": errors,
        "degradations": degradations,
        "change_log": [],
        "runtime": {
            "started_at": now,
            "seconds": None,
            "model_requests": None,
            "notes": [],
        },
    }

    # Echo every contract field verbatim (missing -> null), never rename/drop.
    for key in contract_fields(contract):
        if key not in result:
            result[key] = None

    result["change_log"].append(
        {"seq": 0, "time": now, "action": "init", "detail": "skeleton written"}
    )
    return result


def deep_merge(base, patch):
    """Deep-merge `patch` into `base`: dicts recurse, lists replace wholesale."""
    if isinstance(base, dict) and isinstance(patch, dict):
        out = dict(base)
        for key, value in patch.items():
            if key in out and isinstance(out[key], dict) and isinstance(value, dict):
                out[key] = deep_merge(out[key], value)
            else:
                out[key] = value
        return out
    return patch


def atomic_write_json(path, obj):
    """Write JSON atomically (temp file + os.replace), never half-written."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_result(output_dir):
    """Read result.json from `output_dir`; return {} when missing/bad."""
    path = os.path.join(output_dir, "result.json")
    if not os.path.exists(path):
        return {}
    obj, err = safe_json_load(path)
    return obj if isinstance(obj, dict) else {}


def validate_result(result, contract=None):
    """Return list of issues (empty list == valid)."""
    issues = []
    if not isinstance(result, dict):
        return ["result_not_object"]
    if not result.get("case_id"):
        issues.append("missing_case_id")
    if result.get("status") not in STATUS_VALUES:
        issues.append("invalid_status:%s" % result.get("status"))
    for key in ("sources", "uncertainty", "change_log",
                "pending_human", "errors", "degradations", "runtime"):
        if key not in result:
            issues.append("missing_standard_field:%s" % key)
    for key in contract_fields(contract):
        if key not in result:
            issues.append("missing_contract_field:%s" % key)
    return issues


# --------------------------------------------------------------------------
# Language detection (heuristic first pass for PT / EN)
# --------------------------------------------------------------------------

_PT_DIACRITICS = ("ç", "ã", "õ")
_PT_WORDS = (
    "não", "para", "como", "uma", "estudo", "artigo", "revista", "resumo",
    "palavras-chave", "autores", "trabalho", "resultados", "métodos",
    "objetivo", "método", "conclusão", "introdução", "discussão", "dos",
)
_EN_WORDS = (
    "the", "and", "of", "with", "this", "study", "article", "abstract",
    "keywords", "authors", "results", "methods", "objective", "journal",
    "received", "published", "doi", "introduction", "discussion", "conclusion",
)


def _word_hits(text, words):
    s = " " + text + " "
    return sum(1 for w in words if (" " + w + " ") in s)


def detect_language(text):
    """Heuristic PT/EN detection; returns dict with language/confidence/evidence."""
    if not text or not str(text).strip():
        return {"language": "undetermined", "confidence": 0.0,
                "evidence": ["empty_text"], "pt_hits": 0, "en_hits": 0}
    t = str(text).lower()
    strong = sum(1 for m in _PT_DIACRITICS if m in t)
    pt_hits = strong * 3 + _word_hits(t, _PT_WORDS)
    en_hits = _word_hits(t, _EN_WORDS)
    total = pt_hits + en_hits
    if total == 0:
        return {"language": "undetermined", "confidence": 0.0,
                "evidence": ["no_markers"], "pt_hits": 0, "en_hits": 0}
    ratio = pt_hits / float(total)
    evidence = ["pt_diacritics:%d" % strong]
    if ratio >= 0.75:
        language, confidence = "pt", min(0.9, 0.55 + ratio * 0.4)
    elif ratio <= 0.25:
        language, confidence = "en", min(0.9, 0.55 + (1 - ratio) * 0.4)
    else:
        language, confidence = "undetermined", 0.3
        evidence.append("ambiguous_pt_en_ratio")
    return {"language": language, "confidence": round(confidence, 2),
            "evidence": evidence, "pt_hits": pt_hits, "en_hits": en_hits}


def classify_version(evidence):
    """Rule-based first-pass version classification; must be human-reconfirmed.

    `evidence` may carry: body_language, abstract_language,
    translation_statement, bilingual_journal, duplicate_of.
    """
    ev = evidence or {}
    body = ev.get("body_language")
    abstract = ev.get("abstract_language")
    translation = str(ev.get("translation_statement") or "").strip()
    bilingual = ev.get("bilingual_journal")
    duplicate = ev.get("duplicate_of")
    if duplicate:
        return "duplicate", "explicit_exact_duplicate_of:%s" % duplicate
    if translation.lower() not in ("", "none", "false", "null"):
        return "full_translation", "publisher_translation_statement"
    if bilingual:
        return "bilingual", "bilingual_journal_declared"
    if body and abstract and body != abstract:
        return "abstract_only", "body:%s_abstract:%s" % (body, abstract)
    if body and not abstract:
        return "original", "body_language:%s_no_translation_evidence" % body
    return "undetermined", "insufficient_evidence"


# --------------------------------------------------------------------------
# Dedup / normalization
# --------------------------------------------------------------------------


def normalize_doi(doi):
    """Normalize a DOI to its bare lowercase form (strip resolver/prefix)."""
    s = str(doi or "").strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = re.sub(r"^doi:\s*", "", s)
    return s.strip()


def normalize_title(title):
    """Lowercase, strip diacritics and punctuation, collapse whitespace."""
    s = unicodedata.normalize("NFKD", str(title or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def dedup_key(doi="", title="", first_author="", year=""):
    """Stable dedup key: DOI if present, else normalized title+author+year."""
    d = normalize_doi(doi)
    if d:
        return "doi:" + d
    t = normalize_title(title)
    a = re.sub(r"[^a-z]+", "", str(first_author or "").lower())[:20]
    y = re.sub(r"[^0-9]", "", str(year or ""))[:4]
    parts = [p for p in (t, a, y) if p]
    return ("title:" + "|".join(parts)) if parts else None
