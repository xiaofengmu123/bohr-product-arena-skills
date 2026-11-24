#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — shared helpers for the cross-lingual literature-search skill.

Standard library only. Provides:
  * tolerant text / JSON reading (BOM, bad encoding, truncated JSON),
  * input discovery (no fixed file-name assumptions),
  * output_contract extraction from case.json or task.md,
  * atomic JSON writes and deep-merge patching,
  * evidence-map validation (locator + verbatim quote for every claim),
  * cross-language dedup and language-tag helpers.

This module is intentionally side-effect free; main.py drives the CLI.
"""

import hashlib
import json
import os
import re
import unicodedata

_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")

STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")

STANDARD_FIELDS = [
    "case_id",
    "status",
    "research_question",
    "terminology_table",
    "queries",
    "results",
    "evidence_map",
    "dedup",
    "sources",
    "uncertainty",
    "conflicts",
    "missing",
    "pending_human",
    "errors",
    "degradations",
    "change_log",
    "runtime",
]


# --------------------------------------------------------------------------
# Text / JSON IO
# --------------------------------------------------------------------------

def read_text(path, limit=None):
    """Read a text file with fallback encodings. Returns (text, error_or_none)."""
    for enc in _ENCODINGS:
        try:
            with open(path, "r", encoding=enc) as fh:
                data = fh.read() if limit is None else fh.read(limit)
            return data, None
        except UnicodeDecodeError:
            continue
        except Exception as exc:  # noqa: BLE001
            return "", str(exc)
    return "", "decode_error"


def safe_json_load(path):
    """Load JSON tolerantly. Returns (obj_or_none, error_or_none)."""
    text, err = read_text(path)
    if err:
        return None, err
    text = text.lstrip("\ufeff").lstrip()
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001
        # Salvage a truncated object by trimming to the last closing brace.
        idx = text.rfind("}")
        if idx > 0:
            try:
                return json.loads(text[:idx + 1]), None
            except Exception:  # noqa: BLE001
                pass
        return None, str(exc)


def atomic_write_json(path, obj):
    """Atomically write a JSON file (temp file + os.replace)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def deep_merge(base, patch):
    """Deep-merge patch into base. Dicts merge recursively; lists/scalars replace."""
    if isinstance(base, dict) and isinstance(patch, dict):
        out = dict(base)
        for key, value in patch.items():
            if key in out and isinstance(out[key], dict) and isinstance(value, dict):
                out[key] = deep_merge(out[key], value)
            else:
                out[key] = value
        return out
    return patch


# --------------------------------------------------------------------------
# Input discovery
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
            if found["case"] is None:
                found["case"] = full
        elif low == "task.md" or (low.endswith(".md") and "task" in low):
            if found["task"] is None:
                found["task"] = full
    materials = os.path.join(input_dir, "materials")
    if os.path.isdir(materials):
        found["materials"] = [
            os.path.join(materials, n)
            for n in sorted(os.listdir(materials))
            if os.path.isfile(os.path.join(materials, n))
        ]
    return found


def load_inputs(input_dir):
    """Return (found, case_dict, task_text, case_error)."""
    found = discover_inputs(input_dir)
    case, case_err = {}, None
    if found["case"]:
        obj, err = safe_json_load(found["case"])
        if obj is None:
            case, case_err = {}, "bad_json:%s" % err
        else:
            case = obj
    else:
        case_err = "case.json_not_found"
    task_text = ""
    if found["task"]:
        text, err = read_text(found["task"])
        task_text = text
        if err and not case_err:
            case_err = "task.md_read_error:%s" % err
    return found, case, task_text, case_err


# --------------------------------------------------------------------------
# Contract / case_id extraction
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
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        i += 1
    return None


def find_contract_from_text(text):
    """Locate an inline output_contract JSON object inside free text (task.md)."""
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


def contract_top_fields(contract):
    """Ordered list of top-level field names for a contract dict."""
    if isinstance(contract, dict):
        return list(contract.keys())
    if isinstance(contract, list):
        names = []
        for item in contract:
            if isinstance(item, dict):
                for key in item.keys():
                    if key not in names:
                        names.append(key)
            elif isinstance(item, str):
                if item not in names:
                    names.append(item)
        return names
    return []


# --------------------------------------------------------------------------
# Evidence helpers
# --------------------------------------------------------------------------

def hash_text(text):
    """Return a 12-hex-digit prefix of sha1 for a text fragment (verifiability)."""
    if text is None:
        return None
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def normalize_title(title):
    """Normalize a title for dedup comparison (lowercase, strip diacritics/punct)."""
    if not title:
        return ""
    text = unicodedata.normalize("NFKD", str(title))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_doi(doi):
    """Normalize a DOI string for dedup comparison."""
    if not doi:
        return ""
    return re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", str(doi).strip().lower())


def quote_present(text, quote):
    """Check whether `quote` appears verbatim in `text` (whitespace-tolerant)."""
    if not text or not quote:
        return False
    text = re.sub(r"\s+", " ", text)
    quote = re.sub(r"\s+", " ", quote).strip()
    if not quote:
        return False
    return quote in text


# --------------------------------------------------------------------------
# Language heuristic (fallback only; final tags must come from evidence)
# --------------------------------------------------------------------------

_PT_MARKERS = [
    "pesquisa", "artigo", "resumo", "palavras-chave", "introdução",
    "métodos", "resultados", "discussão", "conclusão", "não", "como",
    "para", "uma", "dos", "das", "ção", "ções", "são", "estudo",
]
_EN_MARKERS = [
    "research", "article", "abstract", "keywords", "introduction",
    "methods", "results", "discussion", "conclusion", "the", "of",
    "and", "study", "this", "with",
]


def detect_language(text):
    """Heuristic script/language tag from a text sample. Returns a 2-char code
    or 'undetermined'. This is a fallback: authoritative tags require evidence."""
    if not text:
        return "undetermined"
    sample = str(text)[:4000].lower()
    scripts = {
        "zh": len(re.findall(r"[\u4e00-\u9fff]", sample)),
        "ja": len(re.findall(r"[\u3040-\u30ff]", sample)),
        "ko": len(re.findall(r"[\uac00-\ud7af]", sample)),
        "ru": len(re.findall(r"[\u0400-\u04ff]", sample)),
        "ar": len(re.findall(r"[\u0600-\u06ff]", sample)),
    }
    best_script = max(scripts, key=scripts.get) if any(scripts.values()) else None
    if best_script and scripts[best_script] > 0:
        return best_script
    pt = sum(sample.count(w) for w in _PT_MARKERS)
    en = sum(sample.count(w) for w in _EN_MARKERS)
    if pt or en:
        return "pt" if pt > en else "en"
    return "undetermined"


# --------------------------------------------------------------------------
# Skeleton and validation
# --------------------------------------------------------------------------

def default_empty_for(contract):
    """Build the default value for one contract field (dict->dict, list->[])."""
    if isinstance(contract, dict):
        return {key: default_empty_for(val) for key, val in contract.items()}
    if isinstance(contract, list):
        return []
    return None


def build_skeleton(case_id, contract):
    """Build the minimal legal result skeleton honoring the contract."""
    result = {"case_id": case_id, "status": "partial"}
    if isinstance(contract, dict):
        for key, val in contract.items():
            result[key] = default_empty_for(val)
    elif isinstance(contract, list):
        for key in contract_top_fields(contract):
            result[key] = []
    for field in STANDARD_FIELDS:
        if field not in result:
            if field in ("results", "sources", "errors", "degradations",
                         "pending_human", "missing", "conflicts", "change_log",
                         "terminology_table", "queries", "dedup", "evidence_map",
                         "uncertainty"):
                result[field] = []
            elif field in ("research_question", "runtime"):
                result[field] = {}
            else:
                result[field] = None
    return result


def validate_result(result):
    """Validate result.json; return (issues_list, is_ok)."""
    issues = []
    if not isinstance(result, dict):
        return ["root_must_be_object"], False
    if not result.get("case_id"):
        issues.append("missing_case_id")
    if result.get("status") not in STATUS_VALUES:
        issues.append("invalid_status:%s" % result.get("status"))
    for field in STANDARD_FIELDS:
        if field not in result:
            issues.append("missing_standard_field:%s" % field)
    # Contract fields recorded at init time must still be present.
    for key in result.get("_contract_keys") or []:
        if key not in result:
            issues.append("missing_contract_field:%s" % key)
    # Evidence-map structural checks (the graded core of this skill).
    em = result.get("evidence_map")
    if not isinstance(em, list):
        issues.append("evidence_map_must_be_list")
    else:
        seen = set()
        for i, entry in enumerate(em):
            if not isinstance(entry, dict):
                issues.append("evidence_map[%d]_not_object" % i)
                continue
            for req in ("evidence_id", "claim", "locator", "quote"):
                if not entry.get(req):
                    issues.append("evidence_map[%d]_missing_%s" % (i, req))
            if entry.get("locator") is not None and not isinstance(entry.get("locator"), dict):
                issues.append("evidence_map[%d]_locator_not_object" % i)
            elif isinstance(entry.get("locator"), dict):
                if not entry["locator"].get("source"):
                    issues.append("evidence_map[%d]_locator_missing_source" % i)
            eid = entry.get("evidence_id")
            if eid in seen:
                issues.append("evidence_map_duplicate_id:%s" % eid)
            seen.add(eid)
    # Every result carrying a language/version/relevance claim needs evidence.
    results = result.get("results")
    if isinstance(results, list):
        for i, item in enumerate(results):
            if not isinstance(item, dict):
                continue
            claimed = any(item.get(k) for k in ("language", "version", "relevance"))
            if claimed and not any(
                isinstance(e, dict) and isinstance(e.get("target"), dict)
                and e["target"].get("results_index") == i for e in em
            ):
                issues.append("result[%d]_has_claim_but_no_evidence" % i)
    return issues, len(issues) == 0
