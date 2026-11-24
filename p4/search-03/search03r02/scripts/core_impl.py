#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — literature screening & ranking pipeline (Python 3 stdlib only).

Subcommands (via `main.py`):
    discover   Scan the input directory for case.json / task.md / materials,
               parse them tolerantly, write case_normalized.json and an EARLY
               partial result.json skeleton (full coverage, relevance=unknown).
    screen     Deterministic lexical signals + dedupe clustering -> scaffold.json.
               Never assigns final relevance/priority; those come from the Agent.
    finalize   Validate result.json against the contract, write selfcheck.json,
               stamp runtime/change_log, echo the output_contract fields.

The pipeline records a UTC timestamp in change_log at every stage so the
"write a partial skeleton within the first 3 minutes, then improve
incrementally" budget can be audited. All deterministic outputs are identical
for identical inputs: fixed tie-breaks, no randomness, sorted iteration order.

Only the Python standard library is used. Text is read as utf-8-sig with
fallbacks; oversized files (>2 MB) are skipped and recorded in degradations.
"""

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Constants and environment
# ---------------------------------------------------------------------------
DEFAULT_INPUT = "/input"
DEFAULT_WORK = "/app/work"
DEFAULT_OUTPUT = "/app/output"
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB per text file

ALLOWED_RELEVANCE = {"direct", "indirect", "irrelevant", "unknown"}
ALLOWED_TIER = {"retain", "pending", "exclude"}
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}

MATERIAL_EXTS = {".pdf", ".md", ".markdown", ".txt", ".json", ".jsonl", ".csv", ".tsv"}

# Field-name candidates used by discovery. The pipeline never assumes a fixed
# schema; it tries these names in order and keeps the first non-empty hit.
ID_KEYS = ["record_id", "id", "paper_id", "pid", "key", "index", "doc_id"]
TITLE_KEYS = ["title", "name", "paper_title", "document_title"]
AUTHOR_KEYS = ["authors", "author", "creators", "author_list"]
YEAR_KEYS = ["year", "pub_year", "publication_year", "date", "publication_date"]
VENUE_KEYS = ["venue", "journal", "conference", "source", "publisher", "publication"]
DOI_KEYS = ["doi", "DOI"]
URL_KEYS = ["url", "link", "href", "pdf_url", "web_url"]
ABSTRACT_KEYS = ["abstract", "summary", "description", "excerpt", "text", "content"]
TYPE_KEYS = ["source_type", "type", "kind", "doc_type", "publication_type"]
KEYWORD_KEYS = ["keywords", "key_words", "tags", "mesh_terms"]

QUESTION_KEYS = ["research_question", "question", "query", "task", "topic", "objective",
                 "research_topic", "pico_question", "search_query"]
CRITERIA_KEYS = ["criteria", "inclusion", "exclusion", "inclusion_criteria",
                 "exclusion_criteria", "include", "exclude", "inclusion_exclusion",
                 "eligibility", "selection_criteria", "standards", "annotations"]
CONTRACT_KEYS = ["output_contract", "output_schema", "expected_fields", "result_schema",
                 "output_fields"]
CASE_ID_KEYS = ["case_id", "id", "challenge_id", "task_id", "instance_id"]

# Plural-sounding names get an empty-list default when echoing output_contract;
# everything else defaults to null.
_LIST_HINT = re.compile(r"(results?|papers?|items?|records?|candidates?|sources?|"
                        r"evidence|reasons?|uncertainty|errors?|warnings?|degradations?|"
                        r"omissions?|pending_human|change_log|dedupe_groups?|list|array)", re.I)


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def monotonic_seconds():
    return time.monotonic()


# ---------------------------------------------------------------------------
# Text / JSON reading (tolerant)
# ---------------------------------------------------------------------------
def read_text(path):
    """Read a text file as utf-8-sig, falling back to utf-8 then latin-1.

    Returns (text, encoding_used, error). Never raises.
    """
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, None, "read_error: %s" % exc
    if len(raw) > MAX_FILE_BYTES:
        return None, None, "oversized(%d bytes > %d)" % (len(raw), MAX_FILE_BYTES)
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), enc, None
        except UnicodeDecodeError:
            continue
    try:
        return raw.decode("latin-1"), "latin-1", "fallback_latin1"
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace"), "utf-8-replace", "lossy_decode"


def load_json_file(path):
    """Parse JSON with utf-8-sig tolerance; return (obj, error)."""
    text, _enc, err = read_text(path)
    if err:
        return None, err
    if text is None:
        return None, "unreadable"
    try:
        return json.loads(text), None
    except ValueError as exc:
        # Try stripping a BOM the hard way and trailing garbage.
        try:
            return json.loads(text.lstrip("\ufeff")), None
        except ValueError:
            return None, "bad_json: %s" % exc


def find_files(root, exts=None):
    """Recursively list regular files under root (sorted for determinism)."""
    out = []
    if not root or not os.path.isdir(root):
        return out
    for base, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(base, name)
            if os.path.isfile(full):
                if exts is None or os.path.splitext(name)[1].lower() in exts:
                    out.append(full)
    return sorted(out)


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------
def norm_text(s):
    if s is None:
        return ""
    if isinstance(s, (list, tuple)):
        s = " ".join(str(x) for x in s)
    s = str(s)
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_title(s):
    return re.sub(r"[^a-z0-9]+", "", norm_text(s).lower())


def norm_doi(s):
    d = norm_text(s).lower()
    d = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", d)
    d = re.sub(r"^doi:\s*", "", d)
    return d.strip()


def tokenize(s):
    return set(re.findall(r"[a-z0-9][a-z0-9\-']{1,}", norm_text(s).lower()))


def extract_year(raw, rec):
    if raw is not None:
        m = re.search(r"(18|19|20)\d{2}", norm_text(raw))
        if m:
            return int(m.group(0))
    for key in YEAR_KEYS:
        if key in rec:
            m = re.search(r"(18|19|20)\d{2}", norm_text(rec.get(key)))
            if m:
                return int(m.group(0))
    return None


def first_hit(rec, keys):
    for key in keys:
        if key in rec and rec[key] not in (None, "", [], {}):
            return rec[key]
    return None


def authors_to_list(raw):
    if raw is None:
        return []
    if isinstance(raw, list):
        out = []
        for item in raw:
            if isinstance(item, dict):
                for k in ("name", "full_name", "author", "given", "family"):
                    if item.get(k):
                        out.append(norm_text(item[k]))
                        break
            else:
                v = norm_text(item)
                if v:
                    out.append(v)
        return [a for a in out if a]
    if isinstance(raw, dict):
        return authors_to_list([raw])
    return [a for a in re.split(r"[;,]\s*|\s+and\s+|\s+&\s+", norm_text(raw)) if a]


def normalize_record(raw, idx, source_file, source_kind):
    """Normalize one raw candidate dict into the canonical record shape."""
    if not isinstance(raw, dict):
        raw = {"title": norm_text(raw)}
    rid = first_hit(raw, ID_KEYS)
    if rid is None:
        rid = "rec_%04d" % idx
    else:
        rid = norm_text(rid)
    title = norm_text(first_hit(raw, TITLE_KEYS)) or None
    year = extract_year(first_hit(raw, YEAR_KEYS), raw)
    authors = authors_to_list(first_hit(raw, AUTHOR_KEYS))
    venue = norm_text(first_hit(raw, VENUE_KEYS)) or None
    doi = norm_doi(first_hit(raw, DOI_KEYS)) or None
    url = norm_text(first_hit(raw, URL_KEYS)) or None
    source_type = norm_text(first_hit(raw, TYPE_KEYS)) or None
    abstract = norm_text(first_hit(raw, ABSTRACT_KEYS)) or None
    keywords = first_hit(raw, KEYWORD_KEYS)
    if isinstance(keywords, list):
        keywords = [norm_text(k) for k in keywords if norm_text(k)]
    else:
        keywords = [k for k in re.split(r"[;,]\s*", norm_text(keywords)) if k]
    locator = "%s#%s" % (os.path.basename(source_file) if source_file else source_kind,
                         rid) if source_kind == "case_json" else \
              "%s:%s" % (source_file or source_kind, rid)
    return {
        "record_id": rid,
        "title": title,
        "authors": authors,
        "year": year,
        "venue": venue,
        "doi": doi,
        "url": url,
        "source_type": source_type,
        "abstract": abstract,
        "keywords": keywords,
        "source_file": source_file,
        "source_kind": source_kind,
        "locator": locator,
        "raw": raw,
    }


def discover_records(case_obj, case_path):
    """Extract candidate records from a parsed case.json object.

    Returns (records, candidates_container_locator). Handles lists directly or
    objects that wrap a list under a common key.
    """
    if isinstance(case_obj, list):
        return [normalize_record(r, i, case_path, "case_json")
                for i, r in enumerate(case_obj)], "$"
    if isinstance(case_obj, dict):
        cand_keys = ["papers", "candidates", "records", "results", "items", "documents",
                     "literature", "publications", "references", "entries", "docs"]
        for key in cand_keys:
            val = case_obj.get(key)
            if isinstance(val, list):
                loc = "#%s" % key
                return [normalize_record(r, i, case_path, "case_json")
                        for i, r in enumerate(val)], loc
        # Fall back: any list whose items look like paper dicts.
        for key, val in case_obj.items():
            if isinstance(val, list) and val and isinstance(val[0], dict) and \
                    (TITLE_KEYS[0] in val[0] or "title" in val[0] or "id" in val[0]):
                return [normalize_record(r, i, case_path, "case_json")
                        for i, r in enumerate(val)], "#" + key
    return [], None


def extract_question(case_obj, task_text):
    if isinstance(case_obj, dict):
        for key in QUESTION_KEYS:
            if case_obj.get(key):
                return norm_text(case_obj[key])
    # fall back to task.md headline / first lines
    if task_text:
        lines = [l.strip() for l in task_text.splitlines() if l.strip()]
        for l in lines:
            if l.startswith("#"):
                return l.lstrip("# ").strip()
        if lines:
            return lines[0][:200]
    return None


def extract_criteria(case_obj, task_text):
    """Return (criteria_text, criteria_obj) — never fabricate when missing."""
    obj = None
    txt = ""
    if isinstance(case_obj, dict):
        for key in CRITERIA_KEYS:
            if case_obj.get(key):
                val = case_obj[key]
                if isinstance(val, (dict, list)):
                    obj = val
                txt += norm_text(val) + "\n"
        # nested criteria block
        for outer in ("inclusion_exclusion", "eligibility", "annotation_guide"):
            if isinstance(case_obj.get(outer), dict):
                for k, v in case_obj[outer].items():
                    txt += "%s: %s\n" % (k, norm_text(v))
    if not txt and task_text:
        # capture an explicit "纳入/排除" section if present, else null
        m = re.search(r"(纳入[^\n]*标准|排除[^\n]*标准|纳排[^\n]*)", task_text)
        if m:
            txt = m.group(0)
    return (txt.strip() or None, obj)


def extract_contract(case_obj):
    """Extract output_contract / output_schema; return dict of field->default."""
    if not isinstance(case_obj, dict):
        return {}
    raw = None
    for key in CONTRACT_KEYS:
        if case_obj.get(key) is not None:
            raw = case_obj[key]
            break
    if raw is None:
        return {}
    fields = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(v, dict):
                # could be {"type": "...", "description": "..."}
                spec = v.get("type") or v.get("schema") or v.get("kind")
                if spec and re.search(r"array|list", str(spec), re.I):
                    fields[k] = []
                elif _LIST_HINT.search(str(k)):
                    fields[k] = []
                else:
                    fields[k] = None
            else:
                fields[k] = [] if _LIST_HINT.search(str(k)) else None
    elif isinstance(raw, list):
        for k in raw:
            name = k if isinstance(k, str) else norm_text(k)
            fields[name] = [] if _LIST_HINT.search(name) else None
    elif isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return extract_contract({"output_contract": parsed})
        except ValueError:
            fields[raw] = None
    return fields


def extract_case_id(case_obj, task_text):
    if isinstance(case_obj, dict):
        for key in CASE_ID_KEYS:
            if case_obj.get(key):
                return norm_text(case_obj[key])
    if task_text:
        m = re.search(r"challenge[_\- ]?id[:\s]+([A-Za-z0-9\-_.]+)", task_text, re.I)
        if m:
            return m.group(1)
    return "unknown_case"


# ---------------------------------------------------------------------------
# Discovery stage
# ---------------------------------------------------------------------------
def scan_input(input_dir):
    """Enumerate case.json, task.md, and material files."""
    all_files = find_files(input_dir)
    case_path = None
    task_path = None
    materials = []
    for f in all_files:
        base = os.path.basename(f).lower()
        if base == "case.json" and case_path is None:
            case_path = f
        elif base == "task.md" and task_path is None:
            task_path = f
        elif os.path.splitext(f)[1].lower() in MATERIAL_EXTS and \
                base not in ("case.json", "task.md"):
            materials.append(f)
    return case_path, task_path, materials


def build_sources(case_path, task_path, materials):
    sources = []
    for path, kind, role in [(case_path, "case_json", "任务与候选集"),
                             (task_path, "task_md", "题目说明")]:
        if path:
            try:
                size = os.path.getsize(path)
            except OSError:
                size = None
            sources.append({"path": path, "kind": kind, "role": role, "bytes": size})
    for path in materials:
        ext = os.path.splitext(path)[1].lower()
        try:
            size = os.path.getsize(path)
        except OSError:
            size = None
        sources.append({"path": path, "kind": "material", "role": ext.lstrip("."),
                        "bytes": size})
    return sources


def run_discover(input_dir, work_dir, output_dir):
    """Run the discovery stage and write an EARLY partial result.json."""
    t0 = monotonic_seconds()
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    degradations = []
    errors = []

    case_path, task_path, materials = scan_input(input_dir)
    task_text = None
    if task_path:
        task_text, _enc, terr = read_text(task_path)
        if terr:
            degradations.append({"scope": "task.md", "detail": terr,
                                 "path": task_path})

    case_obj = None
    if case_path:
        case_obj, cerr = load_json_file(case_path)
        if cerr:
            errors.append({"scope": "case.json", "detail": cerr, "path": case_path})
            degradations.append({"scope": "case.json", "detail": cerr,
                                 "path": case_path})
            case_obj = None

    # ---- even if case.json is broken, still produce a valid partial result ----
    records = []
    container_locator = None
    if case_obj is not None:
        records, container_locator = discover_records(case_obj, case_path)
    elif task_text:
        # best effort: no candidates discoverable from prose without fabricating
        records = []

    question = extract_question(case_obj, task_text)
    criteria_text, criteria_obj = extract_criteria(case_obj, task_text)
    contract_fields = extract_contract(case_obj)
    case_id = extract_case_id(case_obj, task_text)

    # ---- Early partial skeleton: full coverage, relevance=unknown ----
    results = []
    for i, rec in enumerate(records):
        results.append({
            "record_id": rec["record_id"],
            "title": rec["title"],
            "authors": rec["authors"],
            "year": rec["year"],
            "venue": rec["venue"],
            "doi": rec["doi"],
            "url": rec["url"],
            "source_type": rec["source_type"],
            "relevance": "unknown",
            "tier": "pending",
            "priority_score": 0.0,
            "rank": i,
            "reasons": [],
            "evidence": [],
            "confidence": "low",
            "criteria_matched": [],
            "conflict": None,
            "dedupe": None,
            "locator": rec["locator"],
            "source_file": rec["source_file"],
        })

    if case_path and case_obj is None:
        # case.json exists but could not be parsed: report honestly as input error
        status = "input_error"
    elif not case_obj and not task_text:
        status = "insufficient_evidence"
    elif not records:
        status = "insufficient_evidence"
    elif errors:
        status = "input_error"
    else:
        status = "partial"

    result = {
        "case_id": case_id,
        "status": status,
        "task": {
            "research_question": question,
            "criteria": criteria_text,
            "criteria_obj": criteria_obj,
            "candidate_count": len(records),
            "container_locator": container_locator,
        },
        "results": results,
        "dedupe_groups": [],
        "sources": build_sources(case_path, task_path, materials),
        "uncertainty": [],
        "change_log": [{
            "ts": utcnow(),
            "step": "discover",
            "action": "partial_skeleton_written",
            "detail": "coverage=%d candidates, relevance all unknown; early partial "
                      "result on disk within budget" % len(records),
            "elapsed_s": round(monotonic_seconds() - t0, 2),
        }],
        "errors": errors,
        "degradations": degradations,
        "pending_human": [],
        "runtime": {
            "started_at": utcnow(),
            "updated_at": utcnow(),
            "stages": {"discover_done_at": utcnow()},
        },
        "omissions": [],
        "status_notes": "",
        "output_contract": contract_fields,
    }
    # echo contract fields at top level (missing -> null/[])
    for key, default in contract_fields.items():
        if key not in result:
            result[key] = default

    # normalized records (full abstracts) for the Agent to read
    normalized = {
        "case_id": case_id,
        "research_question": question,
        "criteria": criteria_text,
        "criteria_obj": criteria_obj,
        "records": records,
        "container_locator": container_locator,
        "materials": materials,
        "case_path": case_path,
        "task_path": task_path,
    }

    _write_json(os.path.join(work_dir, "case_normalized.json"), normalized)
    _write_json(os.path.join(output_dir, "result.json"), result)
    return result


# ---------------------------------------------------------------------------
# Screen stage (deterministic scaffold)
# ---------------------------------------------------------------------------
def run_screen(work_dir, output_dir):
    norm_path = os.path.join(work_dir, "case_normalized.json")
    if not os.path.isfile(norm_path):
        return {"error": "case_normalized.json missing; run discover first"}
    normalized = _read_json(norm_path) or {}
    records = normalized.get("records", [])
    question = normalized.get("research_question") or ""
    q_tokens = tokenize(question)

    # lexical signals (never final scores)
    for rec in records:
        title = rec.get("title") or ""
        abstract = rec.get("abstract") or ""
        t_tokens = tokenize(title)
        a_tokens = tokenize(abstract)
        title_overlap = (len(q_tokens & t_tokens) / len(q_tokens)) if q_tokens else 0.0
        abstract_overlap = (len(q_tokens & a_tokens) / len(q_tokens)) if q_tokens else 0.0
        # deterministic weighted lexical prior, clipped to [0, 1]
        base_score = round(min(1.0, 0.6 * title_overlap + 0.4 * abstract_overlap), 4)
        # evidence completeness: fraction of key metadata present
        completeness = 0.0
        for field in ("title", "authors", "year", "venue", "doi", "abstract"):
            val = rec.get(field)
            if val not in (None, "", []):
                completeness += 1.0
        rec["_signal"] = {
            "title_overlap": round(title_overlap, 4),
            "abstract_overlap": round(abstract_overlap, 4),
            "base_score": base_score,
            "evidence_completeness": round(completeness / 6.0, 4),
            "has_doi": bool(rec.get("doi")),
            "has_abstract": bool(rec.get("abstract")),
            "has_year": rec.get("year") is not None,
        }

    dedupe_groups = _build_dedupe_groups(records)

    scaffold = {
        "case_id": normalized.get("case_id"),
        "records_with_signals": records,
        "dedupe_groups": dedupe_groups,
        "tie_break_keys": ["priority_score desc", "evidence_completeness desc",
                           "year desc", "norm_title asc", "record_id asc"],
        "note": "base_score is a lexical prior ONLY; final priority_score must come "
                "from the Agent's semantic judgment with evidence.",
    }
    _write_json(os.path.join(work_dir, "scaffold.json"), scaffold)

    # copy dedupe_groups into result.json early (deterministic part of dedupe)
    result_path = os.path.join(output_dir, "result.json")
    result = _read_json(result_path) or {}
    result["dedupe_groups"] = dedupe_groups
    result["runtime"] = result.get("runtime") or {}
    result["runtime"]["updated_at"] = utcnow()
    result["runtime"]["stages"] = result["runtime"].get("stages") or {}
    result["runtime"]["stages"]["screen_done_at"] = utcnow()
    cl = result.setdefault("change_log", [])
    cl.append({"ts": utcnow(), "step": "screen", "action": "scaffold_written",
               "detail": "lexical signals + %d dedupe group(s)" % len(dedupe_groups)})
    _write_json(result_path, result)
    return scaffold


def _build_dedupe_groups(records):
    """Cluster records by normalized title OR normalized DOI (transitive closure)."""
    parent = {r["record_id"]: r["record_id"] for r in records}
    rid_set = set(parent)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    buckets = {}
    for rec in records:
        nt = norm_title(rec.get("title") or "")
        if nt:
            buckets.setdefault(("title", nt), []).append(rec["record_id"])
        nd = norm_doi(rec.get("doi") or "")
        if nd:
            buckets.setdefault(("doi", nd), []).append(rec["record_id"])
    for key, rids in buckets.items():
        if len(rids) > 1:
            for i in range(1, len(rids)):
                union(rids[0], rids[i])

    groups = {}
    for rid in sorted(rid_set):
        root = find(rid)
        groups.setdefault(root, []).append(rid)
    groups = {k: sorted(v) for k, v in groups.items() if len(v) > 1}

    by_id = {r["record_id"]: r for r in records}
    out = []
    gid = 0
    for root in sorted(groups, key=lambda k: groups[k][0]):
        members = groups[root]
        gid += 1
        # canonical preference: published (venue) > higher year > has DOI > record_id
        def rank_key(rid):
            r = by_id.get(rid, {})
            return (1 if r.get("venue") else 0,
                    r.get("year") if r.get("year") is not None else 0,
                    1 if r.get("doi") else 0,
                    rid)
        canonical = max(members, key=rank_key)
        kinds = []
        for rid in members:
            r = by_id.get(rid, {})
            if r.get("venue") and not r.get("source_type", "").lower().startswith("pre"):
                kinds.append("published")
            else:
                kinds.append("preprint_or_unverified")
        kind = "version_variant" if set(kinds) > {"published", "preprint_or_unverified"} \
            else "duplicate"
        out.append({
            "group_id": "dg_%04d" % gid,
            "records": members,
            "kind": kind,
            "canonical_record_id": canonical,
            "decision": "treat %s as canonical; mark others as duplicate_of=%s "
                        "until Agent adjudicates conflicting metadata" % (canonical, canonical),
            "reason": "same normalized title/DOI cluster (deterministic scaffold)",
        })
    return out


# ---------------------------------------------------------------------------
# Finalize stage (validation + selfcheck)
# ---------------------------------------------------------------------------
def run_finalize(work_dir, output_dir):
    result_path = os.path.join(output_dir, "result.json")
    norm_path = os.path.join(work_dir, "case_normalized.json")
    result = _read_json(result_path)
    normalized = _read_json(norm_path) or {}

    errors = []
    warnings = []

    if result is None:
        errors.append("result.json missing or unparseable")
        selfcheck = {"errors": errors, "warnings": warnings,
                     "summary": "result.json absent; nothing to validate"}
        _write_json(os.path.join(output_dir, "selfcheck.json"), selfcheck)
        return errors, warnings

    # 1. case_id + status
    if not result.get("case_id"):
        errors.append("case_id missing/empty")
    status = result.get("status")
    if status not in ALLOWED_STATUS:
        errors.append("status '%s' not in %s" % (status, sorted(ALLOWED_STATUS)))

    # 2. results coverage vs normalized records
    results = result.get("results")
    if not isinstance(results, list):
        errors.append("results must be a list")
        results = []
    expected_ids = [r.get("record_id") for r in normalized.get("records", [])]
    present_ids = [r.get("record_id") for r in results if isinstance(r, dict)]
    missing = [rid for rid in expected_ids if rid not in present_ids]
    extra = [rid for rid in present_ids if rid not in expected_ids]
    if missing:
        errors.append("missing records in results: %s" % ", ".join(map(str, missing)))
        omissions = result.setdefault("omissions", [])
        if not isinstance(omissions, list):
            omissions = []
        for rid in missing:
            if not any(o.get("record_id") == rid for o in omissions):
                omissions.append({"record_id": rid, "reason": "not_covered_by_results"})
    if extra:
        warnings.append("extra records not in normalized input: %s" %
                        ", ".join(map(str, extra)))

    # 3. per-record validation
    unknown_count = 0
    for i, rec in enumerate(results):
        if not isinstance(rec, dict):
            errors.append("results[%d] is not a dict" % i)
            continue
        rid = rec.get("record_id")
        loc = "results[%s]" % rid
        if not rid:
            errors.append("results[%d] missing record_id" % i)
        rel = rec.get("relevance")
        if rel not in ALLOWED_RELEVANCE:
            errors.append("%s: relevance '%s' not in %s" %
                          (loc, rel, sorted(ALLOWED_RELEVANCE)))
        if rel == "unknown":
            unknown_count += 1
        tier = rec.get("tier")
        if tier not in ALLOWED_TIER:
            errors.append("%s: tier '%s' not in %s" % (loc, tier, sorted(ALLOWED_TIER)))
        score = rec.get("priority_score")
        if not isinstance(score, (int, float)) or not (0.0 <= float(score) <= 1.0):
            errors.append("%s: priority_score %r outside [0,1]" % (loc, score))
        reasons = rec.get("reasons")
        if not isinstance(reasons, list):
            errors.append("%s: reasons must be a list" % loc)
        elif rel not in ("unknown",) and not reasons:
            warnings.append("%s: relevance=%s but no reasons" % (loc, rel))
        evidence = rec.get("evidence")
        if not isinstance(evidence, list):
            errors.append("%s: evidence must be a list" % loc)
        else:
            for j, ev in enumerate(evidence):
                if not isinstance(ev, dict) or not ev.get("locator") or \
                        not ev.get("quote"):
                    warnings.append("%s: evidence[%d] missing locator/quote" % (loc, j))
        conf = rec.get("confidence")
        if conf not in ALLOWED_CONFIDENCE:
            warnings.append("%s: confidence '%s' not in %s" %
                            (loc, conf, sorted(ALLOWED_CONFIDENCE)))
        if tier == "exclude" and rel not in ("irrelevant", "unknown"):
            warnings.append("%s: tier=exclude but relevance=%s (verify consistency)"
                            % (loc, rel))

    # 4. status honesty
    if unknown_count > 0 and status == "ok":
        errors.append("status=ok but %d record(s) still relevance=unknown" % unknown_count)
    if not expected_ids and status not in ("insufficient_evidence", "input_error"):
        errors.append("no candidates but status=%s (should be insufficient_evidence)"
                      % status)
    if expected_ids and status == "insufficient_evidence" and unknown_count == 0:
        warnings.append("status=insufficient_evidence but candidates were judged; "
                        "verify whether evidence is really insufficient")

    # 5. required top-level fields present
    for field in ("sources", "uncertainty", "change_log", "pending_human"):
        if field not in result or result[field] is None:
            result[field] = [] if field != "change_log" else result.get("change_log", [])
            warnings.append("missing top-level field '%s'; defaulted" % field)
    if not result.get("change_log"):
        warnings.append("change_log empty")

    # 6. echo output_contract fields (missing -> null/[])
    contract = result.get("output_contract") or {}
    if not isinstance(contract, dict):
        contract = {}
    for key, default in contract.items():
        if key not in result:
            result[key] = default

    # stamp runtime + finalize change_log entry
    result["runtime"] = result.get("runtime") or {}
    result["runtime"]["updated_at"] = utcnow()
    result["runtime"]["stages"] = result["runtime"].get("stages") or {}
    result["runtime"]["stages"]["finalize_done_at"] = utcnow()
    result["runtime"]["errors"] = len(errors)
    result["runtime"]["warnings"] = len(warnings)
    result["errors"] = (result.get("errors") or []) + errors
    result["change_log"] = result.get("change_log") or []
    result["change_log"].append({
        "ts": utcnow(), "step": "finalize", "action": "validated",
        "detail": "errors=%d warnings=%d" % (len(errors), len(warnings)),
    })

    _write_json(result_path, result)

    selfcheck = {
        "errors": errors,
        "warnings": warnings,
        "summary": "errors=%d warnings=%d" % (len(errors), len(warnings)),
        "coverage": {"expected": len(expected_ids), "present": len(present_ids),
                     "missing": missing},
        "status": status,
        "unknown_count": unknown_count,
    }
    _write_json(os.path.join(output_dir, "selfcheck.json"), selfcheck)
    return errors, warnings


# ---------------------------------------------------------------------------
# JSON write/read helpers
# ---------------------------------------------------------------------------
def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def _read_json(path):
    obj, err = load_json_file(path)
    return obj


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _resolve_dirs(args):
    inp = args.input or os.environ.get("INPUT_DIR") or DEFAULT_INPUT
    work = args.work or os.environ.get("WORK_DIR") or DEFAULT_WORK
    out = args.output or os.environ.get("OUTPUT_DIR") or DEFAULT_OUTPUT
    return inp, work, out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Literature screening & ranking pipeline (stdlib only).")
    parser.add_argument("command", nargs="?", default="all",
                        choices=["discover", "screen", "finalize", "all"],
                        help="stage to run (default: all)")
    parser.add_argument("--input", default=None, help="input dir (default /input)")
    parser.add_argument("--work", default=None, help="work dir (default /app/work)")
    parser.add_argument("--output", default=None, help="output dir (default /app/output)")
    args = parser.parse_args(argv)

    inp, work, out = _resolve_dirs(args)

    # create dirs best-effort; never crash the harness on a read-only path
    for d in (work, out):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as exc:
            sys.stderr.write("warning: cannot create %s: %s\n" % (d, exc))

    if args.command in ("discover", "all"):
        run_discover(inp, work, out)
    if args.command in ("screen", "all"):
        run_screen(work, out)
    if args.command in ("finalize", "all"):
        errors, warnings = run_finalize(work, out)
        print("finalize: errors=%d warnings=%d" % (len(errors), len(warnings)))
        if warnings:
            for w in warnings:
                print("  warning: %s" % w)
        if errors:
            for e in errors:
                print("  error: %s" % e)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
