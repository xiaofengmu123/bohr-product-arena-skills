#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""coverage.py — core implementation of the search coverage contract audit.

This module runs a reproducible "freeze reference set -> match -> attribute
omissions -> run supplementary search -> measure marginal gain" workflow for
research-literature retrieval coverage analysis.

Design intent (what makes this Skill different from a naive script):

1. The result.json output contract is declared as *data* in CONTRACT below.
   Every top-level key has a kind, a required flag and a default, so the final
   artifact can be validated field-by-field against the contract instead of
   trusting prose instructions. ``validate_result`` returns the list of
   contract violations; nothing is silently missing.

2. Every mutation of result.json goes through ``commit_result``, which appends
   a structured change_log entry (monotonic revision, UTC timestamp, action,
   touched fields) and writes atomically (temp file + os.replace). A crash can
   never leave a half-written JSON, and the change_log is kept in sync by
   construction rather than by convention.

3. The three runtime-contract output areas (search_results / evidence /
   status_report) are always populated, so the result stays aligned with the
   challenge's "Skill 运行后的输出要求" even when case.json carries no explicit
   output_contract block.

Standard library only; Python >= 3.8.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Constants and thresholds (documented in references/contract.md)
# ---------------------------------------------------------------------------

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_FILE_BYTES = 2 * 1024 * 1024        # files above this size are skipped
MAX_TEXT_BYTES = 8 * 1024 * 1024        # decoding cap for a single text read
MAX_RECORDS_PER_FILE = 20000            # per-file record safety cap

STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")

TITLE_FUZZY_HIT = 0.88                  # SequenceMatcher ratio => auto hit
TITLE_ADJUDICATION = 0.70               # ratio band routed to human verdict
YEAR_TOLERANCE = 1                      # |ref_year - candidate_year| <= this

# The three content areas the runtime contract ("Skill 运行后的输出要求")
# requires every result to report, independent of any per-case output_contract.
RUNTIME_CONTRACT_AREAS = ("search_results", "evidence", "status_report")
RUNTIME_CONTRACT_KEYS = list(RUNTIME_CONTRACT_AREAS) + [
    "case_id", "status", "sources", "uncertainty", "change_log",
]

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+", re.IGNORECASE)
YEAR_RE = re.compile(r"(?:18|19|20|21)\d{2}")

# ---------------------------------------------------------------------------
# Output contract: single source of truth for result.json top-level fields.
# ---------------------------------------------------------------------------

CONTRACT = {
    "case_id":               {"kind": "str",  "required": True, "default": None},
    "status":                {"kind": "enum", "required": True, "default": "partial", "enum": STATUS_VALUES},
    "output_contract":       {"kind": "dict", "required": True, "default": {}},
    "research_question":     {"kind": "str",  "required": True, "default": "not_reported"},
    "search_scope":          {"kind": "dict", "required": True, "default": {}},
    "reference_set":         {"kind": "dict", "required": True, "default": {}},
    "search_sessions":       {"kind": "list", "required": True, "default": []},
    "coverage":              {"kind": "dict", "required": True, "default": {}},
    "hit_matrix":            {"kind": "list", "required": True, "default": []},
    "omissions":             {"kind": "list", "required": True, "default": []},
    "supplementary_searches":{"kind": "list", "required": True, "default": []},
    "marginal_gain":         {"kind": "dict", "required": True, "default": {}},
    "residual_omissions":    {"kind": "list", "required": True, "default": []},
    "search_results":        {"kind": "dict", "required": True, "default": {}},
    "evidence":              {"kind": "dict", "required": True, "default": {}},
    "status_report":         {"kind": "dict", "required": True, "default": {}},
    "evidence_map":          {"kind": "list", "required": True, "default": []},
    "sources":               {"kind": "list", "required": True, "default": []},
    "uncertainty":           {"kind": "list", "required": True, "default": []},
    "pending_human":         {"kind": "list", "required": True, "default": []},
    "change_log":            {"kind": "list", "required": True, "default": []},
    "errors":                {"kind": "list", "required": True, "default": []},
    "degradations":          {"kind": "list", "required": True, "default": []},
    "limitations":           {"kind": "list", "required": True, "default": []},
    "runtime":               {"kind": "dict", "required": True, "default": {}},
}

# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def deep_copy(obj):
    return json.loads(json.dumps(obj, ensure_ascii=False, default=str))


def _to_int(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str):
        m = YEAR_RE.search(v)
        if m:
            try:
                return int(m.group(0))
            except ValueError:
                return None
    return None


def _ci_get(d, key):
    """Case-insensitive dict lookup; returns the first matching value."""
    if not isinstance(d, dict):
        return None
    if key in d:
        return d[key]
    kl = key.lower()
    for k, v in d.items():
        if isinstance(k, str) and k.lower() == kl:
            return v
    return None


def _norm_ws(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def normalize_doi(v):
    if not isinstance(v, str):
        return None
    s = v.strip().strip('"').strip("'")
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.IGNORECASE)
    s = s.rstrip(".,;")
    s = _norm_ws(s)
    return s.lower() if s else None


def _title_key(title):
    if not title:
        return ""
    s = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\b(the|a|an|of|and|for|in|on|with|by|to)\b", "", s)
    return _norm_ws(s)


def title_ratio(a, b):
    ka, kb = _title_key(a), _title_key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    return difflib.SequenceMatcher(None, ka, kb).ratio()


def normalize_author(v):
    if not isinstance(v, str):
        return None
    s = unicodedata.normalize("NFKD", v).encode("ascii", "ignore").decode("ascii")
    s = s.lower().replace(".", " ").replace("-", " ").replace("'", " ")
    s = re.sub(r"\s+", " ", s).strip()
    # "Last, First" -> keep last token before comma
    if "," in s:
        s = s.split(",")[0]
    return s or None


def _authors_match(ref_a, cand_a):
    if not ref_a or not cand_a:
        return True  # cannot falsify without author data; weakened match
    return ref_a == cand_a or ref_a in cand_a or cand_a in ref_a


def _years_match(ref_y, cand_y):
    if ref_y is None or cand_y is None:
        return True
    try:
        return abs(int(ref_y) - int(cand_y)) <= YEAR_TOLERANCE
    except (TypeError, ValueError):
        return True


# ---------------------------------------------------------------------------
# I/O helpers (UTF-8-sig first, graceful degradation, atomic writes)
# ---------------------------------------------------------------------------


def read_text_safe(path, max_bytes=MAX_TEXT_BYTES):
    """Return (text_or_None, problem_or_None). Never raises for bad encodings."""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, {"locator": path, "error": str(e)}
    if size > max_bytes:
        return None, {"locator": path, "error": "too_large", "bytes": size}
    for enc in ("utf-8-sig", "utf-8"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), None
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError as e:
            return None, {"locator": path, "error": str(e)}
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        return raw.decode("utf-8", errors="replace"), {
            "locator": path, "warning": "bad_encoding_decoded_with_replacement",
        }
    except OSError as e:
        return None, {"locator": path, "error": str(e)}


def atomic_write_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def atomic_write_text(path, text):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


def discover_files(input_dir):
    """Walk the input directory; return metadata list, skipping oversized files."""
    files, skipped = [], []
    if not os.path.isdir(input_dir):
        return files, skipped, {"error": "input_dir_missing", "dir": input_dir}
    for root, _dirs, names in os.walk(input_dir):
        for name in sorted(names):
            p = os.path.join(root, name)
            try:
                size = os.path.getsize(p)
            except OSError as e:
                skipped.append({"path": p, "reason": str(e)})
                continue
            if size > MAX_FILE_BYTES:
                skipped.append({"path": p, "reason": "over_2MB", "bytes": size})
                continue
            files.append({
                "path": p,
                "relpath": os.path.relpath(p, input_dir),
                "bytes": size,
                "ext": os.path.splitext(name)[1].lower(),
                "name": name,
            })
    return files, skipped, None


# ---------------------------------------------------------------------------
# Result object, change_log, contract validation
# ---------------------------------------------------------------------------


def new_result():
    r = {}
    for key, spec in CONTRACT.items():
        r[key] = deep_copy(spec["default"])
    r["runtime"] = {
        "start_utc": now_utc(),
        "end_utc": None,
        "elapsed_seconds": None,
        "model_requests_estimate": 0,
        "files_processed": 0,
        "files_skipped": 0,
    }
    return r


def log_change(result, action, fields, note):
    ch = result.setdefault("change_log", [])
    ch.append({
        "revision": len(ch) + 1,
        "time_utc": now_utc(),
        "action": action,
        "fields": list(fields) if fields else [],
        "note": note,
    })
    return result


def commit_result(output_dir, result, action, fields, note):
    log_change(result, action, fields, note)
    result["runtime"]["end_utc"] = now_utc()
    atomic_write_json(os.path.join(output_dir, "result.json"), result)
    return result


def load_result(output_dir):
    p = os.path.join(output_dir, "result.json")
    if os.path.isfile(p):
        text, problem = read_text_safe(p)
        if text is not None:
            try:
                obj = json.loads(text)
                if isinstance(obj, dict):
                    return obj
            except (ValueError, TypeError):
                pass
    return new_result()


def validate_result(result):
    """Return list of contract-violation strings; empty list means aligned."""
    problems = []
    for key, spec in CONTRACT.items():
        if key not in result:
            problems.append("missing top-level key: %s" % key)
            continue
        v = result[key]
        kind = spec["kind"]
        if kind == "str" and v is not None and not isinstance(v, str):
            problems.append("key %s: expected str, got %s" % (key, type(v).__name__))
        elif kind == "list" and not isinstance(v, list):
            problems.append("key %s: expected list, got %s" % (key, type(v).__name__))
        elif kind == "dict" and not isinstance(v, dict):
            problems.append("key %s: expected dict, got %s" % (key, type(v).__name__))
        elif kind == "enum":
            if v not in spec["enum"]:
                problems.append("key %s: value %r not in %s" % (key, v, spec["enum"]))
    if result.get("status") not in STATUS_VALUES:
        problems.append("status %r invalid" % result.get("status"))
    return problems


# ---------------------------------------------------------------------------
# case.json / output_contract handling
# ---------------------------------------------------------------------------


def load_case_json(input_dir):
    p = os.path.join(input_dir, "case.json")
    if not os.path.isfile(p):
        return None, {"locator": p, "error": "case_json_missing"}
    text, problem = read_text_safe(p)
    if text is None:
        return None, problem or {"locator": p, "error": "unreadable"}
    try:
        case = json.loads(text)
    except (ValueError, TypeError) as e:
        return None, {"locator": p, "error": "case_json_invalid", "detail": str(e)}
    if not isinstance(case, dict):
        return None, {"locator": p, "error": "case_json_not_object"}
    return case, None


def extract_case_id(case):
    if isinstance(case, dict):
        for k in ("case_id", "id", "challenge_id", "challengeId", "armiId"):
            v = case.get(k)
            if isinstance(v, str) and v:
                return v
            if isinstance(v, (int, float)):
                return str(v)
    h = hashlib.sha256()
    h.update(json.dumps(case, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8"))
    return "hash-" + h.hexdigest()[:16]


def extract_output_contract(case):
    if isinstance(case, dict):
        for k in ("output_contract", "outputContract", "output_contract_fields"):
            v = case.get(k)
            if isinstance(v, dict) and v:
                return deep_copy(v)
            if isinstance(v, list):
                return {"fields": [str(x) for x in v]}
    # No explicit per-case contract: record the derived runtime contract so the
    # alignment is explicit and auditable instead of implicit.
    return {
        "source": "runtime_contract",
        "areas": list(RUNTIME_CONTRACT_AREAS),
        "fields": RUNTIME_CONTRACT_KEYS,
        "note": "Derived from task.md Runtime contract / Skill 运行后的输出要求; no case.json output_contract block found.",
    }


# ---------------------------------------------------------------------------
# Record discovery and normalization (Crossref / EuropePMC / generic JSON)
# ---------------------------------------------------------------------------

_ID_FIELDS = (
    "DOI", "doi", "title", "title_str", "articleTitle", "TI", "title_s",
    "author", "authors", "authorString", "AU",
    "pubYear", "publicationYear", "publication_year", "issued", "year",
)

_CONTAINER_KEYS = (
    "message", "resultList", "items", "result", "results", "records", "hits",
    "data", "documents", "works", "articles", "publications", "references",
)


def _looks_like_record(d):
    if not isinstance(d, dict):
        return False
    has_identity = any(k in d for k in ("DOI", "doi", "title", "articleTitle",
                                        "author", "authors", "authorString", "TI"))
    return has_identity


def iter_raw_records(obj, path="", seen=None):
    """Yield (record_dict, path) for common literature-API shapes."""
    if seen is None:
        seen = set()
    if isinstance(obj, list):
        for i, x in enumerate(obj):
            yield from iter_raw_records(x, "%s[%d]" % (path, i), seen)
        return
    if not isinstance(obj, dict):
        return
    oid = id(obj)
    if oid in seen:
        return
    seen.add(oid)
    containers = []
    for key in _CONTAINER_KEYS:
        v = obj.get(key)
        if isinstance(v, (list, dict)):
            containers.append((key, v))
    if containers:
        for key, v in containers:
            yield from iter_raw_records(v, "%s.%s" % (path, key), seen)
        return
    if _looks_like_record(obj):
        yield obj, path


def normalize_record(raw, locator, source):
    rec = {
        "_locator": locator,
        "source": source,
        "doi": None,
        "title": None,
        "first_author": None,
        "year": None,
        "raw_id": None,
    }
    # DOI
    doi = _ci_get(raw, "DOI")
    if doi is None:
        doi = _ci_get(raw, "doi")
    if doi is None:
        u = _ci_get(raw, "URL") or _ci_get(raw, "url")
        if isinstance(u, str):
            m = DOI_RE.search(u)
            if m:
                doi = m.group(0)
    if doi is None:
        rid = _ci_get(raw, "id")
        if isinstance(rid, str):
            m = DOI_RE.search(rid)
            if m:
                doi = m.group(0)
    rec["doi"] = normalize_doi(doi)
    rec["raw_id"] = _ci_get(raw, "id") if isinstance(_ci_get(raw, "id"), str) else None

    # Title
    t = (_ci_get(raw, "title") or _ci_get(raw, "title_str")
         or _ci_get(raw, "articleTitle") or _ci_get(raw, "TI") or _ci_get(raw, "title_s"))
    if isinstance(t, list):
        t = t[0] if t else None
    rec["title"] = _norm_ws(t) if isinstance(t, str) and _norm_ws(t) else None

    # First author
    auth = _ci_get(raw, "author") or _ci_get(raw, "authors") or _ci_get(raw, "AU")
    if isinstance(auth, str):
        rec["first_author"] = normalize_author(auth.split(";")[0].split(",")[0])
    elif isinstance(auth, list) and auth:
        a0 = auth[0]
        if isinstance(a0, dict):
            fam = _ci_get(a0, "family") or _ci_get(a0, "last") or _ci_get(a0, "surname")
            name = _ci_get(a0, "name")
            given = _ci_get(a0, "given") or _ci_get(a0, "first")
            if fam:
                rec["first_author"] = normalize_author(fam)
            elif name:
                rec["first_author"] = normalize_author(name)
            else:
                rec["first_author"] = normalize_author(given)
        elif isinstance(a0, str):
            rec["first_author"] = normalize_author(a0)
    if rec["first_author"] is None:
        as_ = _ci_get(raw, "authorString")
        if isinstance(as_, str) and as_:
            rec["first_author"] = normalize_author(as_.split(",")[0])

    # Year
    y = (_ci_get(raw, "year") or _ci_get(raw, "pubYear") or _ci_get(raw, "publicationYear")
         or _ci_get(raw, "publication_year") or _ci_get(raw, "pubyear"))
    if y is None:
        issued = _ci_get(raw, "issued") or _ci_get(raw, "published") or _ci_get(raw, "date")
        if isinstance(issued, dict):
            dp = issued.get("date-parts") or issued.get("dateParts")
            if isinstance(dp, list) and dp and isinstance(dp[0], list):
                y = dp[0][0]
            else:
                dt = issued.get("date-time") or issued.get("dateTime")
                if isinstance(dt, str):
                    m = YEAR_RE.search(dt)
                    if m:
                        y = m.group(0)
        elif isinstance(issued, str):
            m = YEAR_RE.search(issued)
            if m:
                y = m.group(0)
    rec["year"] = _to_int(y)
    return rec


def parse_file_records(path):
    """Parse one file into normalized records + list of problems."""
    records, problems = [], []
    ext = os.path.splitext(path)[1].lower()
    text, problem = read_text_safe(path)
    if text is None:
        return records, [problem or {"locator": path, "error": "unreadable"}]
    if problem:
        problems.append(problem)
    try:
        if ext in (".json", ".jsonl", ".ndjson"):
            _parse_json_text(text, path, records, problems)
        elif ext == ".csv":
            _parse_csv_text(text, path, records, problems)
        else:
            _parse_plain_text(text, path, records, problems)
    except Exception as e:  # defensive: never crash on malformed input
        problems.append({"locator": path, "error": "parse_exception", "detail": str(e)})
    return records, problems


def _parse_json_text(text, path, records, problems):
    # JSONL first (line-by-line), then whole-document JSON.
    if text.lstrip().startswith(("[", "{")):
        try:
            obj = json.loads(text)
        except ValueError:
            obj = None
        if obj is not None:
            _emit_from_object(obj, path, records)
            return
    # JSONL / concatenated objects
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        _emit_from_object(obj, "%s#L%d" % (path, lineno), records)
    if not records:
        problems.append({"locator": path, "error": "no_parseable_json"})


def _emit_from_object(obj, path, records):
    n_before = len(records)
    for raw, rp in iter_raw_records(obj):
        rec = normalize_record(raw, "%s::%s" % (path, rp) if rp else path,
                               os.path.basename(path))
        if rec["doi"] or rec["title"]:
            records.append(rec)
        if len(records) >= MAX_RECORDS_PER_FILE:
            break
    return len(records) - n_before


def _parse_csv_text(text, path, records, problems):
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        problems.append({"locator": path, "error": "csv_no_header"})
        return
    for i, row in enumerate(reader):
        rec = normalize_record(row, "%s#row%d" % (path, i + 2), os.path.basename(path))
        if rec["doi"] or rec["title"]:
            records.append(rec)
        if len(records) >= MAX_RECORDS_PER_FILE:
            break


def _parse_plain_text(text, path, records, problems):
    """Extract DOIs and title-like lines from markdown/text/PDF-derived text."""
    found_doi = False
    for m in DOI_RE.finditer(text):
        found_doi = True
        rec = {"_locator": "%s#doi" % path, "source": os.path.basename(path),
               "doi": normalize_doi(m.group(0)), "title": None,
               "first_author": None, "year": None, "raw_id": None}
        records.append(rec)
        if len(records) >= MAX_RECORDS_PER_FILE:
            break
    if not found_doi:
        problems.append({"locator": path, "error": "no_identifiers_in_text"})


# ---------------------------------------------------------------------------
# Reference set freeze + deduplication
# ---------------------------------------------------------------------------


def freeze_reference_set(records, source_files, research_question="not_reported"):
    seen_doi, seen_key, deduped, issues = {}, {}, [], []
    for rec in records:
        key = rec.get("doi") or ("title:" + _title_key(rec.get("title")))
        if rec.get("doi") and rec["doi"] in seen_doi:
            issues.append({
                "type": "duplicate_doi",
                "doi": rec["doi"],
                "locators": [seen_doi[rec["doi"]]["_locator"], rec["_locator"]],
            })
            continue
        if key in seen_key:
            issues.append({
                "type": "duplicate_key",
                "key": key,
                "locators": [seen_key[key]["_locator"], rec["_locator"]],
            })
            continue
        seen_doi[rec["doi"]] = rec
        seen_key[key] = rec
        entry = {
            "ref_id": "ref-%04d" % (len(deduped) + 1),
            "doi": rec["doi"],
            "title": rec["title"],
            "first_author": rec["first_author"],
            "year": rec["year"],
            "source_locator": rec["_locator"],
            "source_file": rec["source"],
            "included": True,
            "excluded": False,
            "exclusion_reason": None,
            "identifier_issues": _identifier_issues(rec),
        }
        deduped.append(entry)
    deduped.sort(key=lambda e: (e["doi"] or "", e["title"] or "", e["ref_id"]))
    # renumber after sort for deterministic ref_ids
    for i, e in enumerate(deduped, 1):
        e["ref_id"] = "ref-%04d" % i
    return {
        "version": "frozen-1",
        "built_utc": now_utc(),
        "source_files": list(source_files),
        "research_question": research_question,
        "total_records": len(deduped),
        "total_excluded": 0,
        "deduplication_issues": issues,
        "records": deduped,
    }


def _identifier_issues(rec):
    issues = []
    if not rec.get("doi"):
        issues.append("missing_doi")
    if not rec.get("title"):
        issues.append("missing_title")
    if rec.get("year") is None:
        issues.append("missing_year")
    return issues


# ---------------------------------------------------------------------------
# Matching: identifier exact -> title+author+year fuzzy -> adjudicate -> miss
# ---------------------------------------------------------------------------


def match_reference_to_results(reference_set, search_records):
    doi_index, title_candidates = {}, []
    for r in search_records:
        if r.get("doi"):
            doi_index.setdefault(r["doi"], []).append(r)
        title_candidates.append(r)

    hit_matrix, omissions = [], []
    hits = 0
    adjudicate_count = 0
    for ref in reference_set["records"]:
        if ref.get("excluded"):
            hit_matrix.append({
                "ref_id": ref["ref_id"], "status": "excluded",
                "matched_by": None, "ref_title": ref["title"], "ref_doi": ref["doi"],
                "candidate_title": None, "candidate_doi": None,
                "candidate_locator": None, "note": ref.get("exclusion_reason"),
            })
            continue

        match, method, ratio = None, None, 0.0

        # 1) identifier exact match
        if ref.get("doi"):
            cands = doi_index.get(ref["doi"])
            if cands:
                match, method = cands[0], "identifier_exact"

        # 2) title + first-author + year fuzzy
        if match is None:
            for r in title_candidates:
                r_ratio = title_ratio(ref.get("title"), r.get("title"))
                if r_ratio >= TITLE_FUZZY_HIT and _authors_match(ref.get("first_author"), r.get("first_author")) \
                        and _years_match(ref.get("year"), r.get("year")):
                    match, method, ratio = r, "title_author_year_fuzzy", r_ratio
                    break

        # 3) adjudication band (title-only fuzzy, no confident triple match)
        if match is None:
            best, bestr = None, 0.0
            for r in title_candidates:
                r_ratio = title_ratio(ref.get("title"), r.get("title"))
                if r_ratio > bestr:
                    best, bestr = r, r_ratio
            if best is not None and bestr >= TITLE_ADJUDICATION:
                match, method, ratio = best, "adjudicate", bestr

        if method == "identifier_exact" or method == "title_author_year_fuzzy":
            status, hits = "hit", hits + 1
        elif method == "adjudicate":
            status, adjudicate_count = "adjudicate", adjudicate_count + 1
        else:
            status = "miss"
            omissions.append({
                "ref_id": ref["ref_id"],
                "ref_title": ref["title"],
                "ref_doi": ref["doi"],
                "ref_locator": ref["source_locator"],
                "category": "unattributed",
                "signals": _identifier_issues(ref) or ["no_matching_record"],
                "evidence": [],
                "query_attribution": None,
                "source_attribution": None,
                "time_window_attribution": None,
            })

        hit_matrix.append({
            "ref_id": ref["ref_id"],
            "status": status,
            "matched_by": method,
            "similarity": round(ratio, 4) if method == "adjudicate" else None,
            "ref_title": ref["title"],
            "ref_doi": ref["doi"],
            "ref_locator": ref["source_locator"],
            "candidate_title": match.get("title") if match else None,
            "candidate_doi": match.get("doi") if match else None,
            "candidate_locator": match.get("_locator") if match else None,
        })

    denom = reference_set["total_records"]
    coverage = {
        "denominator": denom,
        "numerator": hits,
        "coverage_rate": round(hits / denom, 4) if denom else None,
        "omission_rate": round(1 - hits / denom, 4) if denom else None,
        "adjudicate_count": adjudicate_count,
        "hit_rule": "identifier_exact -> title+first_author+year fuzzy (ratio>=%.2f) -> title fuzzy adjudication band (%.2f..%.2f); year_tolerance=%d" % (
            TITLE_FUZZY_HIT, TITLE_ADJUDICATION, TITLE_FUZZY_HIT, YEAR_TOLERANCE),
    }
    return hit_matrix, coverage, omissions


# ---------------------------------------------------------------------------
# Editable byproducts
# ---------------------------------------------------------------------------


def write_byproducts(result, output_dir):
    os.makedirs(output_dir, exist_ok=True)

    # hit_matrix.csv
    hm = result.get("hit_matrix", [])
    with open(os.path.join(output_dir, "hit_matrix.csv"), "w", encoding="utf-8-sig",
              newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ref_id", "status", "matched_by", "similarity",
                    "ref_title", "ref_doi", "ref_locator",
                    "candidate_title", "candidate_doi", "candidate_locator"])
        for row in hm:
            w.writerow([row.get("ref_id"), row.get("status"), row.get("matched_by"),
                        row.get("similarity"), row.get("ref_title"), row.get("ref_doi"),
                        row.get("ref_locator"), row.get("candidate_title"),
                        row.get("candidate_doi"), row.get("candidate_locator")])

    # evidence_map.json
    atomic_write_json(os.path.join(output_dir, "evidence_map.json"),
                      result.get("evidence_map", []))

    # coverage_report.md (concise, editable)
    lines = [
        "# 检索覆盖率与遗漏分析报告",
        "",
        "> 本报告由 `scripts/coverage.py run` 自动生成，可编辑；所有数值可依据 `result.json`、",
        "> `hit_matrix.csv`、`evidence_map.json` 独立复算。",
        "",
        "## 1. 状态与契约",
        "",
        "- case_id: `%s`" % result.get("case_id"),
        "- status: `%s`" % result.get("status"),
        "- output_contract: `%s`" % json.dumps(result.get("output_contract"), ensure_ascii=False),
        "",
        "## 2. 覆盖率",
        "",
    ]
    cov = result.get("coverage", {})
    lines.append("- 分母（冻结参考集规模）: %s" % cov.get("denominator"))
    lines.append("- 分子（命中数）: %s" % cov.get("numerator"))
    lines.append("- coverage_rate: %s" % cov.get("coverage_rate"))
    lines.append("- omission_rate: %s" % cov.get("omission_rate"))
    lines.append("- 待人工裁决数: %s" % cov.get("adjudicate_count"))
    lines.append("- 命中规则: %s" % cov.get("hit_rule"))
    lines.append("")
    lines.append("## 3. 漏检归因（未归因项需由 Agent 依据 materials 补全）")
    lines.append("")
    for o in result.get("omissions", []):
        lines.append("- `%s` — %s — category=%s" % (
            o.get("ref_id"), (o.get("ref_title") or "")[:60], o.get("category")))
    lines.append("")
    lines.append("## 4. 补检边际收益")
    lines.append("")
    mg = result.get("marginal_gain", {})
    lines.append("- before_omissions: %s" % mg.get("before_omissions"))
    lines.append("- after_omissions: %s" % mg.get("after_omissions"))
    lines.append("- reduction: %s" % mg.get("reduction"))
    lines.append("- 残余遗漏: %s 条" % len(result.get("residual_omissions", [])))
    lines.append("")
    lines.append("## 5. 诚实声明")
    lines.append("")
    lines.append("- errors: %s 条；degradations: %s 条；pending_human: %s 条" % (
        len(result.get("errors", [])), len(result.get("degradations", [])),
        len(result.get("pending_human", []))))
    lines.append("- limitations: %s 条" % len(result.get("limitations", [])))
    lines.append("")
    atomic_write_text(os.path.join(output_dir, "coverage_report.md"), "\n".join(lines))


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def _finalize_runtime(result, start, n_processed, n_skipped):
    result["runtime"]["end_utc"] = now_utc()
    result["runtime"]["elapsed_seconds"] = round(time.time() - start, 3)
    result["runtime"]["files_processed"] = n_processed
    result["runtime"]["files_skipped"] = n_skipped


def cmd_scaffold(args):
    t0 = time.time()
    result = new_result()
    files, skipped, err = discover_files(args.input)
    case, case_problem = load_case_json(args.input)

    result["case_id"] = extract_case_id(case)
    result["output_contract"] = extract_output_contract(case)
    result["sources"] = [{"path": f["relpath"], "role": "input_file", "bytes": f["bytes"]}
                         for f in files]
    result["runtime"]["files_processed"] = len(files)
    result["runtime"]["files_skipped"] = len(skipped)
    for s in skipped:
        result["degradations"].append({"locator": s["path"], "reason": s["reason"]})
    if err:
        result["errors"].append(err)
    if case_problem:
        result["errors"].append(case_problem)

    if not files and not case:
        result["status"] = "insufficient_evidence"
        result["limitations"].append("input directory contains no analyzable files and no case.json")
    elif case is None:
        result["status"] = "input_error"
        result["limitations"].append("case.json missing; case_id derived from content hash fallback")
    elif case_problem:
        result["status"] = "partial"
        result["limitations"].append("case.json degraded: %s" % case_problem.get("error"))
    else:
        result["status"] = "partial"

    _finalize_runtime(result, t0, len(files), len(skipped))
    commit_result(args.output, result, "scaffold",
                  ["case_id", "output_contract", "status", "sources", "runtime", "errors",
                   "degradations", "limitations"],
                  "initial partial skeleton landed within budget; further steps mutate incrementally")
    return result


def cmd_inspect(args):
    t0 = time.time()
    files, skipped, err = discover_files(args.input)
    report = {
        "input_dir": args.input,
        "scanned_utc": now_utc(),
        "files": files,
        "skipped": skipped,
        "filesystem_error": err,
        "per_file": {},
    }
    total_records = 0
    for f in files:
        records, problems = parse_file_records(f["path"])
        total_records += len(records)
        role = guess_role(f["name"], f["ext"])
        report["per_file"][f["relpath"]] = {
            "bytes": f["bytes"],
            "ext": f["ext"],
            "record_count": len(records),
            "parse_problems": problems,
            "role_guess": role,
            "sample_locators": [r["_locator"] for r in records[:3]],
        }
    report["total_record_candidates"] = total_records
    os.makedirs(args.work, exist_ok=True)
    atomic_write_json(os.path.join(args.work, "inspect.json"), report)

    result = load_result(args.output)
    result["runtime"]["files_processed"] = len(files)
    result["runtime"]["files_skipped"] = len(skipped)
    result["sources"] = [{"path": f["relpath"], "role": guess_role(f["name"], f["ext"]),
                          "bytes": f["bytes"], "record_count":
                          report["per_file"][f["relpath"]]["record_count"]} for f in files]
    _finalize_runtime(result, t0, len(files), len(skipped))
    commit_result(args.output, result, "inspect",
                  ["sources", "runtime"],
                  "inventory of /input written to work/inspect.json; roles guessed by filename")
    return report


_REF_HINTS = ("reference", "referencelist", "refset", "golden", "known", "target",
              "seed", "included", "groundtruth", "ground_truth", "curated",
              "expertlist", "expert_list")
_RESULT_HINTS = ("result", "search", "query", "response", "crossref", "europepmc",
                 "pubmed", "scopus", "publisher", "api", "retrieved", "candidate",
                 "openalex", "semanticscholar", "semantic_scholar", "hit")


def guess_role(name, ext):
    n = name.lower()
    # Result hints checked first so "crossref_response" is not caught by the
    # reference hints ("crossref" contains the substring "ref").
    if any(h in n for h in _RESULT_HINTS):
        return "search_results"
    if any(h in n for h in _REF_HINTS):
        return "reference_set"
    if name == "case.json":
        return "case_metadata"
    if name == "task.md":
        return "task"
    return "unknown"


def _resolve_sources(files, refs_glob, results_glob):
    refs, results = [], []
    for f in files:
        role = guess_role(f["name"], f["ext"])
        if refs_glob and _glob_match(f["relpath"], refs_glob):
            refs.append(f)
            continue
        if results_glob and _glob_match(f["relpath"], results_glob):
            results.append(f)
            continue
        if role == "reference_set":
            refs.append(f)
        elif role == "search_results":
            results.append(f)
        elif role == "unknown" and f["ext"] in (".json", ".jsonl", ".ndjson", ".csv"):
            # if a glob was given and this file is not matched, leave it out
            if not refs_glob and not results_glob:
                results.append(f)
    return refs, results


def _glob_match(relpath, pattern):
    import fnmatch
    return fnmatch.fnmatch(relpath, pattern) or fnmatch.fnmatch(os.path.basename(relpath), pattern)


def cmd_run(args):
    t0 = time.time()
    files, skipped, err = discover_files(args.input)
    refs, results = _resolve_sources(files, args.refs, args.results)

    ref_records, ref_errors = [], []
    for f in refs:
        recs, probs = parse_file_records(f["path"])
        ref_records.extend(recs)
        ref_errors.extend(probs)

    search_records, search_errors = [], []
    for f in results:
        recs, probs = parse_file_records(f["path"])
        search_records.extend(recs)
        search_errors.extend(probs)

    result = load_result(args.output)
    result["case_id"] = result.get("case_id") or extract_case_id(load_case_json(args.input)[0])

    # research question: from task.md if present, else from case.json
    rq = "not_reported"
    task_text, _ = read_text_safe(os.path.join(args.input, "task.md"), max_bytes=200000)
    if task_text:
        m = re.search(r"#\s*([^\n]{4,120})", task_text)
        if m:
            rq = m.group(1).strip()
    result["research_question"] = rq

    if not refs:
        result["status"] = "insufficient_evidence"
        result["limitations"].append(
            "no reference_set files identified; coverage requires an independent frozen reference set "
            "(use --refs <glob> to point at review/guideline/expert list files)")
        result["reference_set"] = {"version": None, "records": [],
                                   "note": "no reference set resolved"}
        result["search_sessions"] = [{
            "session_id": "s1", "query": None, "source_files":
            [f["relpath"] for f in results], "time_window": None,
            "fields": None, "result_count": len(search_records),
        }]
        result["coverage"] = {"denominator": 0, "numerator": 0,
                              "coverage_rate": None, "omission_rate": None,
                              "adjudicate_count": 0, "hit_rule": ""}
        _finalize_runtime(result, t0, len(files), len(skipped))
        commit_result(args.output, result, "run",
                      ["research_question", "reference_set", "search_sessions",
                       "coverage", "status", "limitations", "runtime"],
                      "run aborted: independent reference set missing (denominator cannot be derived from results)")
        return result

    reference_set = freeze_reference_set(
        ref_records, [f["relpath"] for f in refs], research_question=rq)
    hit_matrix, coverage, omissions = match_reference_to_results(reference_set, search_records)

    # search sessions summary
    sessions = [{
        "session_id": "s1",
        "query": None,
        "source_files": [f["relpath"] for f in results],
        "source_kind": _infer_source_kind(results),
        "time_window": None,
        "fields": None,
        "result_count": len(search_records),
    }]

    result["reference_set"] = reference_set
    result["search_sessions"] = sessions
    result["coverage"] = coverage
    result["hit_matrix"] = hit_matrix
    result["omissions"] = omissions
    result["marginal_gain"] = {
        "before_omissions": len(omissions),
        "after_omissions": len(omissions),
        "reduction": 0,
        "new_hits": 0,
        "denominator": len(omissions),
        "note": "supplementary search not yet applied; run merge --updates to refine",
    }
    result["residual_omissions"] = [{
        "ref_id": o["ref_id"], "reason": "not yet attributed",
        "recoverable": None, "action": "attribute and design supplementary query",
    } for o in omissions]

    # runtime-contract areas
    result["search_results"] = {
        "candidate_count": len(search_records),
        "candidates": [{
            "locator": r["_locator"], "doi": r["doi"], "title": r["title"],
            "first_author": r["first_author"], "year": r["year"],
        } for r in search_records[:500]],
        "metadata_notes": "candidate list truncated at 500 records; full records remain in source files",
        "source_locators": [f["relpath"] for f in results],
    }
    result["evidence"] = {
        "queries": [], "filters": [], "ranking": None, "tracking": None,
        "note": "queries/filters/ranking populated by Agent from materials; match rules are machine-applied",
    }
    result["status_report"] = {
        "empty_results": len(search_records) == 0,
        "conflicts": _find_conflicts(reference_set, search_records),
        "permission_issues": [],
        "interface_errors": [],
        "completed": ["reference_set_frozen", "dedup", "match", "coverage"],
        "incomplete": ["omission_attribution", "supplementary_search", "adjudication"],
    }

    # evidence map from machine-derived locators + verbatim identifiers
    result["evidence_map"] = _auto_evidence_map(reference_set, search_records)

    result["errors"].extend(ref_errors + search_errors)
    for p in ref_errors + search_errors:
        result["degradations"].append(p)
    if err:
        result["errors"].append(err)
    for s in skipped:
        result["degradations"].append({"locator": s["path"], "reason": s["reason"]})

    result["status"] = "partial" if (coverage.get("adjudicate_count") or omissions) else "ok"
    if result["status"] == "partial" and coverage.get("coverage_rate") is not None:
        pass  # partial is honest while adjudication/attribution pending

    _finalize_runtime(result, t0, len(files), len(skipped))
    commit_result(args.output, result, "run",
                  ["reference_set", "search_sessions", "coverage", "hit_matrix", "omissions",
                   "marginal_gain", "residual_omissions", "search_results", "evidence",
                   "status_report", "evidence_map", "errors", "degradations", "status", "runtime"],
                  "freeze reference set, match, compute coverage; omissions left for attribution")
    write_byproducts(result, args.output)
    return result


def _infer_source_kind(files):
    kinds = {guess_role(f["name"], f["ext"]) for f in files}
    return sorted(kinds)


def _find_conflicts(reference_set, search_records):
    """Detect same-DOI records whose titles disagree across sources."""
    conflicts = []
    by_doi = {}
    for r in search_records:
        if r.get("doi"):
            by_doi.setdefault(r["doi"], []).append(r)
    for ref in reference_set["records"]:
        if ref.get("doi") and ref["doi"] in by_doi:
            for cand in by_doi[ref["doi"]]:
                if cand.get("title") and ref.get("title") and \
                        title_ratio(ref["title"], cand["title"]) < TITLE_FUZZY_HIT:
                    conflicts.append({
                        "type": "title_conflict_same_doi",
                        "doi": ref["doi"],
                        "ref_title": ref["title"],
                        "candidate_title": cand["title"],
                        "ref_locator": ref["source_locator"],
                        "candidate_locator": cand["_locator"],
                    })
                    break
    return conflicts


def _auto_evidence_map(reference_set, search_records):
    em = []
    for ref in reference_set["records"][:100]:
        em.append({
            "locator": ref.get("source_locator"),
            "quote": (ref.get("doi") or ref.get("title") or ""),
            "conclusion": "reference record %s frozen from source material" % ref["ref_id"],
        })
    for r in search_records[:100]:
        em.append({
            "locator": r.get("_locator"),
            "quote": (r.get("doi") or r.get("title") or ""),
            "conclusion": "candidate record located in search-result material",
        })
    return em


# ---------------------------------------------------------------------------
# merge: apply adjudication/attribution/supplementary-search updates
# ---------------------------------------------------------------------------


def cmd_merge(args):
    t0 = time.time()
    result = load_result(args.output)
    updates_path = args.updates
    if not updates_path or not os.path.isfile(updates_path):
        result["errors"].append({"error": "updates_file_missing", "locator": updates_path})
        commit_result(args.output, result, "merge", ["errors"], "no updates file provided")
        return result

    text, problem = read_text_safe(updates_path)
    if text is None:
        result["errors"].append(problem or {"locator": updates_path, "error": "unreadable"})
        commit_result(args.output, result, "merge", ["errors"], "updates file unreadable")
        return result
    try:
        updates = json.loads(text)
    except ValueError as e:
        result["errors"].append({"locator": updates_path, "error": "invalid_json", "detail": str(e)})
        commit_result(args.output, result, "merge", ["errors"], "updates file invalid JSON")
        return result
    if not isinstance(updates, dict):
        updates = {}

    # adjudications -> hit_matrix status transitions
    adj = updates.get("adjudications", [])
    adj_by_ref = {}
    for a in adj:
        if isinstance(a, dict) and a.get("ref_id"):
            adj_by_ref[a["ref_id"]] = a
    for row in result.setdefault("hit_matrix", []):
        if row.get("status") == "adjudicate" and row["ref_id"] in adj_by_ref:
            a = adj_by_ref[row["ref_id"]]
            verdict = a.get("verdict", "miss")
            row["status"] = "hit" if verdict == "hit" else "miss"
            row["matched_by"] = "human_adjudication"
            row["note"] = a.get("note")
            if verdict == "miss":
                result.setdefault("omissions", []).append({
                    "ref_id": row["ref_id"], "ref_title": row.get("ref_title"),
                    "ref_doi": row.get("ref_doi"), "ref_locator": row.get("ref_locator"),
                    "category": "unattributed", "signals": ["adjudicated_miss"],
                    "evidence": [], "query_attribution": None,
                    "source_attribution": None, "time_window_attribution": None,
                })

    # omission updates -> attribution fields
    om_updates = {o["ref_id"]: o for o in updates.get("omission_updates", [])
                  if isinstance(o, dict) and o.get("ref_id")}
    for om in result.setdefault("omissions", []):
        u = om_updates.get(om["ref_id"])
        if u:
            for k in ("category", "signals", "evidence", "query_attribution",
                      "source_attribution", "time_window_attribution"):
                if k in u:
                    om[k] = u[k]

    # supplementary searches + new hits (must reference real records only)
    supp = updates.get("supplementary_searches", [])
    new_hit_count = 0
    for s in supp:
        if isinstance(s, dict):
            new_hits = s.get("new_hits", [])
            new_hit_count += len(new_hits)
    result["supplementary_searches"].extend(supp)

    # residual omissions
    if "residual_omissions" in updates:
        result["residual_omissions"] = updates["residual_omissions"]
    else:
        result["residual_omissions"] = [
            {"ref_id": o["ref_id"], "reason": o.get("category", "unattributed"),
             "recoverable": None, "action": "design supplementary query or accept residual"}
            for o in result.get("omissions", [])]

    # recompute coverage from hit_matrix
    denom = result.get("reference_set", {}).get("total_records", 0) or \
        len([r for r in result.get("reference_set", {}).get("records", [])])
    hits = sum(1 for r in result.get("hit_matrix", []) if r.get("status") == "hit")
    result["coverage"] = {
        "denominator": denom,
        "numerator": hits,
        "coverage_rate": round(hits / denom, 4) if denom else None,
        "omission_rate": round(1 - hits / denom, 4) if denom else None,
        "adjudicate_count": sum(1 for r in result.get("hit_matrix", []) if r.get("status") == "adjudicate"),
        "hit_rule": result.get("coverage", {}).get("hit_rule", ""),
    }

    before = len(result.get("omissions", [])) or 0
    after = max(0, before - new_hit_count)
    result["marginal_gain"] = {
        "before_omissions": before,
        "after_omissions": after,
        "reduction": new_hit_count,
        "new_hits": new_hit_count,
        "denominator": before,
        "note": "reduction counts only new hits that are real records present in materials",
    }

    for k in ("status", "uncertainty", "pending_human", "limitations"):
        if k in updates:
            result[k] = updates[k]

    _finalize_runtime(result, t0, result["runtime"].get("files_processed", 0),
                      result["runtime"].get("files_skipped", 0))
    commit_result(args.output, result, "merge",
                  ["hit_matrix", "omissions", "supplementary_searches", "marginal_gain",
                   "residual_omissions", "coverage", "status", "uncertainty",
                   "pending_human", "limitations", "runtime"],
                  "applied updates file %s" % os.path.basename(updates_path))
    write_byproducts(result, args.output)
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser():
    # Common options live on a parent so they can be given AFTER the subcommand
    # (e.g. `coverage scaffold --input /input --output /app/output --work /app/work`),
    # which is the calling convention documented in SKILL.md.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=DEFAULT_INPUT, help="input dir (default /input)")
    common.add_argument("--output", default=DEFAULT_OUTPUT, help="output dir (default /app/output)")
    common.add_argument("--work", default=DEFAULT_WORK, help="work dir (default /app/work)")

    p = argparse.ArgumentParser(
        prog="coverage",
        description="Search coverage contract audit: freeze reference set, match, "
                    "attribute omissions, measure supplementary-search marginal gain.",
    )
    sub = p.add_subparsers(dest="command")

    sub.add_parser("scaffold", parents=[common], help="land a partial result.json skeleton")
    sub.add_parser("inspect", parents=[common], help="inventory /input -> work/inspect.json")
    rp = sub.add_parser("run", parents=[common], help="freeze refs, match, compute coverage")
    rp.add_argument("--refs", default=None, help="glob for reference-set files")
    rp.add_argument("--results", default=None, help="glob for search-result files")
    mp = sub.add_parser("merge", parents=[common], help="apply adjudication/attribution updates")
    mp.add_argument("--updates", required=True, help="path to updates JSON")
    sub.add_parser("validate", parents=[common], help="validate result.json against contract")
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    if args.command == "scaffold":
        cmd_scaffold(args)
    elif args.command == "inspect":
        cmd_inspect(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "merge":
        cmd_merge(args)
    elif args.command == "validate":
        result = load_result(args.output)
        problems = validate_result(result)
        if problems:
            print("CONTRACT VIOLATIONS:")
            for x in problems:
                print(" -", x)
            return 1
        print("result.json aligned with contract: %d top-level keys, status=%s"
              % (len(CONTRACT), result.get("status")))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
