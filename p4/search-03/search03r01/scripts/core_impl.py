# -*- coding: utf-8 -*-
"""core_impl.py -- discover / screen / finalize / selftest implementation.

Deterministic, standard-library-only backend for the literature screening
Skill.  Three stages mirror the SKILL.md workflow:

  discover  -> parse /input, write a *partial* result.json skeleton first,
               plus case_normalized.json (input discovery, tolerant decode)
  screen    -> deterministic lexical priors + dedup clustering + missing
               metadata flags -> scaffold.json (prior only, NOT final score)
  finalize  -> contract validation, evidence integrity (quote_hash, verified),
               deterministic rank + tie-break, evidence_map, selfcheck.json
  selftest  -> robustness dry-runs (empty / bad-json / missing dir) + finalize

The Agent is the only component allowed to assign relevance/tier/priority;
scripts never guess semantics.  base_score from screen is explicitly a
lexical prior and is never written as a final priority_score.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

READ_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "gbk", "latin-1")
MAX_TEXT_FILE = 2 * 1024 * 1024  # 2 MB per text file, else skip + record
VALID_RELEVANCE = ("direct", "indirect", "irrelevant", "unknown")
VALID_TIER = ("retain", "pending", "exclude")
TIER_ORDER = {"retain": 0, "pending": 1, "exclude": 2}
VALID_CONFIDENCE = ("high", "medium", "low", None)
VALID_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")

CANDIDATE_KEYS = (
    "papers", "candidates", "documents", "records", "items", "results",
    "references", "literature", "documents_list", "papers_list",
    "candidates_list", "records_list", "literature_list",
)
RECORD_ID_KEYS = ("record_id", "id", "paper_id", "doc_id", "index", "uid",
                  "key", "pmid", "pubmed_id")
TITLE_KEYS = ("title", "paper_title", "name", "headline", "document_title")
AUTHOR_KEYS = ("authors", "author", "creator", "creators")
YEAR_KEYS = ("year", "date", "pubdate", "publication_year",
             "published_year", "year_published")
VENUE_KEYS = ("venue", "journal", "source", "publication", "conference",
              "publisher", "journal_name")
DOI_KEYS = ("doi", "DOI", "digital_object_identifier")
URL_KEYS = ("url", "link", "pdf_url", "arxiv_url", "arxiv", "href")
ABSTRACT_KEYS = ("abstract", "summary", "description", "text", "snippet",
                 "abstract_text", "content")
TYPE_KEYS = ("source_type", "type", "doc_type", "document_type",
             "publication_type", "pubtype")

CASE_ID_KEYS = ("case_id", "id", "challenge_id", "task_id", "caseId")
QUESTION_KEYS = ("research_question", "researchQuestion", "question", "topic",
                 "query", "objective", "research_query", "research_topic")
CRITERIA_KEYS = ("criteria", "inclusion_criteria", "exclusion_criteria",
                 "screening_criteria", "eligibility", "inclusion",
                 "exclusion", "inclusion_exclusion_criteria")
OUTPUT_CONTRACT_KEYS = ("output_contract", "output_schema", "expected_fields",
                        "output_fields", "output_contract_fields")

MATERIAL_EXT = (".pdf", ".md", ".markdown", ".txt", ".jsonl", ".json", ".csv")


# ---------------------------------------------------------------------------
# small utilities
# ---------------------------------------------------------------------------

def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def pick(d, keys):
    """Return the first present, non-None value among *keys* in dict *d*."""
    if not isinstance(d, dict):
        return None
    for k in keys:
        if k in d and d.get(k) is not None:
            return d[k]
    return None


def read_text_safe(path):
    """Read a text file tolerantly.  Returns (text, error_tuple_or_None)."""
    try:
        data = open(path, "rb").read()
    except OSError as exc:
        return None, ("read_error", "%s: %s" % (path, exc))
    if len(data) > MAX_TEXT_FILE:
        return None, ("file_too_large",
                      "%s exceeds %d bytes" % (os.path.basename(path),
                                               MAX_TEXT_FILE))
    for enc in READ_ENCODINGS:
        try:
            return data.decode(enc), None
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("latin-1", errors="replace"), ("fallback_encoding",
                                                      os.path.basename(path))


def load_json_safe(path):
    """Load JSON tolerantly.  Returns (obj_or_None, error_list)."""
    text, err = read_text_safe(path)
    if err is not None:
        return None, [err]
    try:
        return json.loads(text), []
    except (json.JSONDecodeError, ValueError) as exc:
        return None, [("bad_json", "%s: %s" % (os.path.basename(path), exc))]


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    return path


def log(msg):
    print(msg, flush=True)


def norm_title(title):
    """Deterministic title normalisation for dedup only."""
    t = title or ""
    t = t.lower()
    t = re.sub(r"\b(arxiv\s*:?\s*)?\d{4}\.\d{4,5}(v\d+)?\b", " ", t)
    t = re.sub(r"\b(preprint|pre-print|preprint version|accepted "
               r"manuscript|published version)\b", " ", t)
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def norm_doi(doi):
    if not doi:
        return None
    d = str(doi).strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    d = re.sub(r"^doi\s*:\s*", "", d)
    return d or None


def sha16(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def normalize_quote(q):
    """Whitespace-normalise a quote for hash / verbatim lookup stability."""
    return re.sub(r"\s+", " ", (q or "").strip())


# ---------------------------------------------------------------------------
# input discovery / normalisation
# ---------------------------------------------------------------------------

def walk_files(directory):
    files = []
    if not os.path.isdir(directory):
        return files
    for root, _dirs, names in os.walk(directory):
        for n in names:
            files.append(os.path.join(root, n))
    return files


def is_material(path):
    return path.lower().endswith(MATERIAL_EXT)


def find_by_name(files, name):
    for f in files:
        if os.path.basename(f).lower() == name.lower():
            return f
    return None


def _list_of_dicts(v):
    return isinstance(v, list) and v and all(isinstance(x, dict) for x in v)


def _looks_like_paper(d):
    return (isinstance(d, dict)
            and sum(1 for k in (TITLE_KEYS + AUTHOR_KEYS + DOI_KEYS
                                + ABSTRACT_KEYS + YEAR_KEYS)
                    if k in d and d.get(k) is not None) >= 2)


def find_candidate_list(node, depth=0):
    """Bounded recursive search for a list of paper-like dicts."""
    if depth > 5 or node is None:
        return None
    if isinstance(node, list):
        if _list_of_dicts(node) and _looks_like_paper(node[0]):
            return node
        for item in node:
            found = find_candidate_list(item, depth + 1)
            if found:
                return found
        return None
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("output_contract", "output_schema", "expected_fields",
                       "criteria", "inclusion", "exclusion"):
                continue
            found = find_candidate_list(value, depth + 1)
            if found:
                return found
    return None


def extract_candidates(case):
    if not isinstance(case, dict):
        return [], None
    for key in CANDIDATE_KEYS:
        v = case.get(key)
        if isinstance(v, list) and v:
            return v, key
    nested = find_candidate_list(case)
    return (nested or []), None


def coerce_year(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    m = re.search(r"(19|20)\d{2}", str(v))
    return int(m.group()) if m else None


def coerce_authors(v):
    if v is None:
        return None
    if isinstance(v, list):
        out = []
        for a in v:
            if isinstance(a, str):
                out.append(a)
            elif isinstance(a, dict):
                out.append(a.get("name") or a.get("author")
                           or str(a))
        return out or None
    if isinstance(v, str):
        parts = [p.strip() for p in re.split(r"[;,]", v) if p.strip()]
        return parts or [v]
    return str(v)


def normalize_candidate(raw, idx, source_key):
    rec = {"_source_key": source_key, "_source_index": idx}
    if isinstance(raw, str):
        rec["title"] = raw
    elif isinstance(raw, dict):
        rec.update(raw)  # keep original fields visible for evidence locators
        rid = pick(raw, RECORD_ID_KEYS)
        rec["record_id"] = str(rid) if rid is not None else "rec_%04d" % idx
        rec["title"] = pick(raw, TITLE_KEYS)
        rec["authors"] = coerce_authors(pick(raw, AUTHOR_KEYS))
        rec["year"] = coerce_year(pick(raw, YEAR_KEYS))
        rec["venue"] = pick(raw, VENUE_KEYS)
        rec["doi"] = pick(raw, DOI_KEYS)
        rec["url"] = pick(raw, URL_KEYS)
        rec["abstract"] = pick(raw, ABSTRACT_KEYS)
        rec["source_type"] = pick(raw, TYPE_KEYS)
    else:
        rec["title"] = str(raw)
    rec.setdefault("record_id", "rec_%04d" % idx)
    locator_base = "case.json"
    if source_key:
        locator_base = "case.json#%s[%d]" % (source_key, idx)
    rec["_locator_prefix"] = locator_base
    for field in ("title", "authors", "year", "venue", "doi", "url",
                  "abstract", "source_type"):
        rec.setdefault(field, None)
    return rec


def normalize_case(case, task_text, case_path, input_files, sources,
                   degradations):
    norm = {
        "case_id": None,
        "research_question": None,
        "criteria": None,
        "criteria_raw": None,
        "output_contract": None,
        "output_contract_keys": [],
        "candidates": [],
        "candidate_source_key": None,
        "raw_keys": [],
        "task_md_headline": "",
    }
    if isinstance(case, dict):
        norm["raw_keys"] = sorted(case.keys())
        norm["case_id"] = pick(case, CASE_ID_KEYS)
        norm["research_question"] = pick(case, QUESTION_KEYS)
        norm["criteria_raw"] = pick(case, CRITERIA_KEYS)
        oc = pick(case, OUTPUT_CONTRACT_KEYS)
        norm["output_contract"] = oc
        norm["output_contract_keys"] = output_contract_keys(oc)
        candidates, source_key = extract_candidates(case)
        norm["candidate_source_key"] = source_key
        norm["candidates"] = [
            normalize_candidate(c, i, source_key)
            for i, c in enumerate(candidates)
        ]
    if task_text:
        for line in task_text.splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                norm["task_md_headline"] = s
                break
    if norm["case_id"] is None:
        norm["case_id"] = os.path.basename(case_path) if case_path else "unknown"
    if norm["research_question"] is None and task_text:
        m = re.search(r"(?:研究问题|科研问题|问题)[：:]\s*(.+)", task_text)
        if m:
            norm["research_question"] = m.group(1).strip()
    if not norm["candidates"]:
        degradations.append(("no_candidates", "no candidate list found"))
    return norm


def output_contract_keys(oc):
    keys = []
    if isinstance(oc, dict):
        keys = [k for k in oc.keys()]
    elif isinstance(oc, list):
        for item in oc:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                keys.append(item.get("name") or item.get("key")
                            or item.get("field"))
    return [k for k in keys if k]


def contract_placeholder(oc, key):
    """Backfill an output_contract key without inventing content."""
    if isinstance(oc, dict):
        v = oc.get(key)
        if isinstance(v, list):
            return []
        if isinstance(v, dict):
            return {}
        return None
    return None


def skeleton_record(rec):
    return {
        "record_id": rec.get("record_id"),
        "title": rec.get("title"),
        "authors": rec.get("authors"),
        "year": rec.get("year"),
        "venue": rec.get("venue"),
        "doi": rec.get("doi"),
        "url": rec.get("url"),
        "abstract": rec.get("abstract"),
        "source_type": rec.get("source_type"),
        "relevance": "unknown",
        "tier": "pending",
        "priority_score": None,
        "rank": None,
        "reasons": [],
        "evidence": [],
        "confidence": None,
        "criteria_matched": [],
        "conflict": None,
        "dedupe": None,
        "decision_path": [],
        "_locator_prefix": rec.get("_locator_prefix"),
    }


def build_skeleton(norm):
    result = {
        "case_id": norm["case_id"],
        "status": "partial",
    }
    oc = norm["output_contract"]
    for key in norm["output_contract_keys"]:
        result[key] = contract_placeholder(oc, key)
    result["results"] = [skeleton_record(r) for r in norm["candidates"]]
    result["evidence_map"] = empty_evidence_map()
    result["dedupe_groups"] = []
    result["sources"] = []
    result["uncertainty"] = []
    result["change_log"] = [
        {"ts": now_iso(), "step": "discover",
         "action": "partial_skeleton_written"}
    ]
    result["errors"] = []
    result["degradations"] = []
    result["pending_human"] = []
    result["runtime"] = {
        "discovered_at": now_iso(),
        "budget_seconds": 900,
        "budget_model_requests": 64,
    }
    return result


def empty_evidence_map():
    return {
        "evidence_items": {},
        "coverage": {
            "total_records": 0,
            "records_with_reasons": 0,
            "total_reasons": 0,
            "reasons_with_evidence": 0,
            "reasons_without_evidence": 0,
            "total_evidence_items": 0,
            "orphan_evidence_items": 0,
            "quote_verified_count": 0,
            "quote_unverified_count": 0,
            "quote_unchecked_count": 0,
        },
        "unsupported_claims": [],
        "orphan_evidence": [],
    }


def run_discover(argv):
    p = argparse.ArgumentParser(
        prog="discover",
        description="Discover /input, tolerate bad encoding/JSON, write a "
                    "partial result.json skeleton first.")
    p.add_argument("--input", default="/input")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--output", default="/app/output")
    a = p.parse_args(argv)
    os.makedirs(a.work, exist_ok=True)
    os.makedirs(a.output, exist_ok=True)

    files = walk_files(a.input)
    case_path = find_by_name(files, "case.json")
    task_path = find_by_name(files, "task.md")
    material_files = [f for f in files
                      if is_material(f)
                      and f not in (case_path, task_path)]

    sources = []
    for f in (case_path, task_path):
        if f:
            sources.append({"path": f, "kind": "json" if f.endswith(".json")
                            else "markdown",
                            "role": "task_input"})
    for f in material_files:
        sources.append({"path": f, "kind": "material", "role": "evidence"})

    degradations = []
    errors = []

    case = None
    if case_path:
        case, errs = load_json_safe(case_path)
        if errs:
            for e in errs:
                degradations.append(
                    {"type": e[0], "detail": e[1], "step": "discover"})
    else:
        degradations.append({"type": "missing_case_json",
                             "detail": "no case.json under /input",
                             "step": "discover"})

    task_text = None
    if task_path:
        txt, err = read_text_safe(task_path)
        if err:
            degradations.append({"type": err[0], "detail": err[1],
                                 "step": "discover"})
        task_text = txt

    norm = normalize_case(case, task_text, case_path, files, sources,
                          degradations)
    norm["material_files"] = material_files
    norm["case_path"] = case_path
    norm["task_path"] = task_path

    skeleton = build_skeleton(norm)
    skeleton["sources"] = sources
    skeleton["degradations"] = [d for d in degradations]
    if not norm["candidates"] and case is None and task_text is None:
        skeleton["status"] = "insufficient_evidence"
        skeleton["pending_human"].append(
            "no input files found; nothing to screen")

    write_json(os.path.join(a.work, "case_normalized.json"), norm)
    write_json(os.path.join(a.work, "discovery.json"),
               {"case_id": norm["case_id"], "sources": sources,
                "degradations": degradations, "errors": errors})
    write_json(os.path.join(a.output, "result.json"), skeleton)

    log("discover: case_id=%s candidates=%d sources=%d degradations=%d "
        "-> skeleton written" % (norm["case_id"], len(norm["candidates"]),
                                 len(sources), len(degradations)))
    return 0


# ---------------------------------------------------------------------------
# screen (deterministic lexical prior + dedup + missing metadata)
# ---------------------------------------------------------------------------

def tokenize_terms(text):
    if not text:
        return set()
    t = text.lower()
    t = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", t, flags=re.UNICODE)
    terms = {w for w in re.split(r"\s+", t) if len(w) >= 3}
    return terms


def lexical_overlap(question_terms, criteria_terms, text):
    t = text or ""
    t = t.lower()
    t = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", t, flags=re.UNICODE)
    words = set(re.split(r"\s+", t))
    terms = set(question_terms) | set(criteria_terms)
    if not terms:
        return 0.0
    return min(1.0, round(len(terms & words) / len(terms), 3))


def missing_metadata(rec):
    missing = []
    for field in ("title", "authors", "year", "venue", "doi", "abstract"):
        if rec.get(field) in (None, "", []):
            missing.append(field)
    return missing


def cluster_dedup(candidates):
    groups = []
    seen = set()
    for i, a in enumerate(candidates):
        if a["record_id"] in seen:
            continue
        members = [a["record_id"]]
        basis = []
        for j, b in enumerate(candidates):
            if i == j or b["record_id"] in seen:
                continue
            da, db = norm_doi(a.get("doi")), norm_doi(b.get("doi"))
            if da and db and da == db:
                basis.append("doi_match")
                members.append(b["record_id"])
                seen.add(b["record_id"])
            elif (norm_title(a.get("title")) == norm_title(b.get("title"))
                  and norm_title(a.get("title"))):
                basis.append("title_match")
                members.append(b["record_id"])
                seen.add(b["record_id"])
        if len(members) > 1:
            groups.append({
                "members": sorted(members),
                "basis": sorted(set(basis)),
                "suggested": "merge_or_keep_primary",
                "note": "Agent must decide primary vs duplicate; script only "
                        "clusters deterministically",
            })
        seen.add(a["record_id"])
    return groups


def run_screen(argv):
    p = argparse.ArgumentParser(
        prog="screen",
        description="Deterministic lexical priors + dedup clustering + "
                    "missing-metadata flags. Prior only; never final score.")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--output", default="/app/output")
    a = p.parse_args(argv)

    norm_path = os.path.join(a.work, "case_normalized.json")
    norm, errs = load_json_safe(norm_path)
    if norm is None:
        log("screen: cannot read %s" % norm_path)
        return 1

    q_terms = tokenize_terms(norm.get("research_question"))
    crit_text = ""
    if isinstance(norm.get("criteria_raw"), dict):
        crit_text = json.dumps(norm["criteria_raw"], ensure_ascii=False)
    elif isinstance(norm.get("criteria_raw"), list):
        crit_text = " ".join(str(x) for x in norm["criteria_raw"])
    elif isinstance(norm.get("criteria_raw"), str):
        crit_text = norm["criteria_raw"]
    c_terms = tokenize_terms(crit_text)

    scaffold = {"records": [], "dedupe_groups": [], "missing_metadata": []}
    for rec in norm.get("candidates", []):
        text = " ".join(str(x) for x in (rec.get("title"), rec.get("abstract"))
                        if x)
        base = lexical_overlap(q_terms, c_terms, text)
        missing = missing_metadata(rec)
        scaffold["records"].append({
            "record_id": rec["record_id"],
            "base_score": base,
            "missing_metadata": missing,
            "lexical_prior_note": "base_score is a keyword prior only; the "
                                  "Agent must override with semantic judgment",
        })
        if missing:
            scaffold["missing_metadata"].append(
                {"record_id": rec["record_id"], "missing": missing})

    scaffold["dedupe_groups"] = cluster_dedup(norm.get("candidates", []))

    write_json(os.path.join(a.work, "scaffold.json"), scaffold)
    log("screen: records=%d dedupe_groups=%d missing_metadata=%d "
        "-> scaffold.json (priors only)"
        % (len(scaffold["records"]), len(scaffold["dedupe_groups"]),
           len(scaffold["missing_metadata"])))
    return 0


# ---------------------------------------------------------------------------
# finalize (contract validation + evidence integrity + deterministic rank)
# ---------------------------------------------------------------------------

def _wrap_reasons(reasons):
    out = []
    for i, r in enumerate(reasons or []):
        if isinstance(r, str):
            out.append({"reason_id": "r_%04d" % i, "text": r,
                        "criteria_ids": [], "evidence_ids": []})
        elif isinstance(r, dict):
            r = dict(r)
            r.setdefault("reason_id", "r_%04d" % i)
            r.setdefault("text", "")
            r.setdefault("criteria_ids", [])
            r.setdefault("evidence_ids", [])
            out.append(r)
        else:
            out.append({"reason_id": "r_%04d" % i, "text": str(r),
                        "criteria_ids": [], "evidence_ids": []})
    return out


def _norm_evidence(ev):
    if not isinstance(ev, dict):
        return None
    return {
        "evidence_id": ev.get("evidence_id"),
        "locator": ev.get("locator"),
        "field": ev.get("field"),
        "quote": ev.get("quote"),
        "quote_hash": ev.get("quote_hash"),
        "claim_refs": ev.get("claim_refs") or [],
        "verified": ev.get("verified"),
    }


def build_verification_corpus(norm, result):
    """Collect every readable text the quotes may be compared against.

    Includes: material files (md/txt/jsonl/csv), the raw case.json and
    task.md, and each record's own title/abstract (metadata carried from the
    input).  Returns a dict name -> normalised text.
    """
    corpus = {}
    for f in ((norm or {}).get("material_files") or []) + [
            (norm or {}).get("case_path"), (norm or {}).get("task_path")]:
        if not f:
            continue
        if not str(f).lower().endswith((".md", ".txt", ".jsonl", ".json",
                                        ".csv")):
            continue
        text, err = read_text_safe(f)
        if not err and text:
            corpus["file:" + os.path.basename(str(f))] = normalize_quote(text)
    for rec in result.get("results", []):
        blob = " ".join(str(x) for x in (rec.get("title"), rec.get("abstract"))
                        if x)
        corpus["record:" + str(rec.get("record_id"))] = normalize_quote(blob)
    return corpus


def verify_quotes_fast(quotes, corpus):
    results = []
    for q in quotes:
        nq = normalize_quote(q)
        if not nq:
            results.append(None)
            continue
        if not corpus:
            results.append(None)
            continue
        found = any(nq in text for text in corpus.values())
        results.append(found)
    return results


def evidence_completeness(rec):
    reasons = rec.get("reasons") or []
    evidence = rec.get("evidence") or []
    if reasons and evidence:
        return 1
    return 0


def compute_rank_key(rec):
    tier = rec.get("tier")
    t = TIER_ORDER.get(tier, 3)
    score = rec.get("priority_score")
    if not isinstance(score, (int, float)):
        score = -1.0
    return (t, -float(score), -evidence_completeness(rec),
            -(int(rec.get("year") or 0)),
            norm_title(rec.get("title") or ""),
            str(rec.get("record_id") or ""))


def run_finalize(argv):
    p = argparse.ArgumentParser(
        prog="finalize",
        description="Validate result.json, verify evidence integrity, "
                    "recompute deterministic rank, write selfcheck.json.")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    a = p.parse_args(argv)

    result_path = os.path.join(a.output, "result.json")
    norm_path = os.path.join(a.work, "case_normalized.json")
    result, errs = load_json_safe(result_path)
    if result is None:
        log("finalize: cannot read result.json (%s)" % (errs,))
        return 1
    norm, _ = load_json_safe(norm_path)

    errors = []
    warnings = []

    # --- coverage: every candidate present ---
    norm_ids = [c["record_id"] for c in (norm or {}).get("candidates", [])]
    result_ids = [r.get("record_id") for r in result.get("results", [])]
    for rid in norm_ids:
        if rid not in result_ids:
            errors.append({"type": "coverage_missing",
                           "detail": "candidate %s missing from results"
                           % rid})
    for rid in result_ids:
        if norm_ids and rid not in norm_ids:
            warnings.append({"type": "unknown_record",
                             "detail": "result %s not in candidates" % rid})

    # --- field validation per record ---
    all_evidence_quotes = []
    quote_rec_map = []
    for rec in result.get("results", []):
        rid = rec.get("record_id")
        rel = rec.get("relevance")
        tier = rec.get("tier")
        score = rec.get("priority_score")
        if rel not in VALID_RELEVANCE:
            errors.append({"type": "invalid_relevance",
                           "detail": "%s relevance=%r" % (rid, rel)})
        if tier not in VALID_TIER:
            errors.append({"type": "invalid_tier",
                           "detail": "%s tier=%r" % (rid, tier)})
        if score is not None and not (0.0 <= float(score) <= 1.0):
            errors.append({"type": "priority_out_of_range",
                           "detail": "%s score=%r" % (rid, score)})
        if tier == "exclude" and isinstance(score, (int, float)) and score > 0.1:
            warnings.append({"type": "exclude_with_score",
                             "detail": "%s tier=exclude but score=%r"
                             % (rid, score)})
        if tier == "retain" and rel == "irrelevant":
            errors.append({"type": "retain_irrelevant",
                           "detail": "%s tier=retain but relevance=irrelevant"
                           % rid})
        if tier == "exclude" and rel in ("direct", "indirect"):
            warnings.append({"type": "exclude_relevant",
                             "detail": "%s tier=exclude but relevance=%s"
                             % (rid, rel)})
        if rel == "unknown" and tier != "pending":
            warnings.append({"type": "unknown_not_pending",
                             "detail": "%s relevance=unknown but tier=%s"
                             % (rid, tier)})

        rec["reasons"] = _wrap_reasons(rec.get("reasons"))
        norm_ev = [_norm_evidence(e) for e in (rec.get("evidence") or [])]
        norm_ev = [e for e in norm_ev if e is not None]
        rec["evidence"] = norm_ev

        # evidence_id assignment
        used_ids = set()
        for i, e in enumerate(rec["evidence"]):
            if not e.get("evidence_id"):
                e["evidence_id"] = "ev_%s_%03d" % (rid, i)
            used_ids.add(e["evidence_id"])

        # reason -> evidence linkage
        reason_ids = {r["reason_id"] for r in rec["reasons"]}
        ev_ids = {e["evidence_id"] for e in rec["evidence"]}
        supported = 0
        total = 0
        for r in rec["reasons"]:
            total += 1
            links = [x for x in r.get("evidence_ids", []) if x in ev_ids]
            r["evidence_ids"] = links
            if links:
                supported += 1
            else:
                warnings.append({"type": "reason_without_evidence",
                                 "detail": "%s %s has no evidence"
                                 % (rid, r["reason_id"])})
        if total and total == supported:
            pass
        # reverse-link evidence -> claim_refs
        ref_map = {}
        for r in rec["reasons"]:
            for eid in r.get("evidence_ids", []):
                ref_map.setdefault(eid, []).append(r["reason_id"])
        for e in rec["evidence"]:
            if not e.get("claim_refs"):
                e["claim_refs"] = ref_map.get(e["evidence_id"], [])
            if e.get("locator") is None:
                errors.append({"type": "evidence_missing_locator",
                               "detail": "%s %s" % (rid, e.get("evidence_id"))})
            if not normalize_quote(e.get("quote")):
                errors.append({"type": "evidence_missing_quote",
                               "detail": "%s %s" % (rid, e.get("evidence_id"))})

        for e in rec["evidence"]:
            e["quote_hash"] = sha16(normalize_quote(e.get("quote")))
            all_evidence_quotes.append(e.get("quote"))
            quote_rec_map.append((rid, e))

    # --- evidence integrity ---
    corpus = build_verification_corpus(norm, result)
    verified = verify_quotes_fast(all_evidence_quotes, corpus)
    for (rid, e), v in zip(quote_rec_map, verified):
        e["verified"] = v

    # --- build evidence_map ---
    em = empty_evidence_map()
    for rec in result.get("results", []):
        for e in rec.get("evidence", []):
            eid = e["evidence_id"]
            em["evidence_items"][eid] = {
                "record_id": rec.get("record_id"),
                "locator": e.get("locator"),
                "field": e.get("field"),
                "quote": e.get("quote"),
                "quote_hash": e.get("quote_hash"),
                "verified": e.get("verified"),
                "claim_refs": e.get("claim_refs") or [],
            }
    referenced = set()
    for rec in result.get("results", []):
        for r in rec.get("reasons", []):
            referenced.update(r.get("evidence_ids", []))
    em["orphan_evidence"] = [eid for eid in em["evidence_items"]
                             if eid not in referenced]
    unsupported = []
    for rec in result.get("results", []):
        for r in rec.get("reasons", []):
            if not r.get("evidence_ids"):
                unsupported.append({"record_id": rec.get("record_id"),
                                    "reason_id": r["reason_id"],
                                    "text": r.get("text")})
    em["unsupported_claims"] = unsupported

    cov = em["coverage"]
    cov["total_records"] = len(result.get("results", []))
    cov["records_with_reasons"] = sum(
        1 for r in result.get("results", []) if r.get("reasons"))
    cov["total_reasons"] = sum(len(r.get("reasons", []))
                               for r in result.get("results", []))
    cov["reasons_with_evidence"] = sum(
        1 for r in result.get("results", [])
        for x in r.get("reasons", []) if x.get("evidence_ids"))
    cov["reasons_without_evidence"] = (cov["total_reasons"]
                                       - cov["reasons_with_evidence"])
    cov["total_evidence_items"] = len(em["evidence_items"])
    cov["orphan_evidence_items"] = len(em["orphan_evidence"])
    cov["quote_verified_count"] = sum(
        1 for e in result.get("results", [])
        for x in e.get("evidence", []) if x.get("verified") is True)
    cov["quote_unverified_count"] = sum(
        1 for e in result.get("results", [])
        for x in e.get("evidence", []) if x.get("verified") is False)
    cov["quote_unchecked_count"] = sum(
        1 for e in result.get("results", [])
        for x in e.get("evidence", []) if x.get("verified") is None)
    result["evidence_map"] = em

    # --- deterministic rank + tie-break ---
    ranked = sorted(result.get("results", []), key=compute_rank_key)
    for i, rec in enumerate(ranked):
        rec["rank"] = i + 1
    result["results"] = ranked

    # --- status honesty ---
    if not result.get("results"):
        result["status"] = "insufficient_evidence"
    elif any(r.get("relevance") == "unknown"
             for r in result.get("results", [])):
        if result.get("status") == "ok":
            result["status"] = "partial"
            warnings.append({"type": "status_downgraded",
                             "detail": "unknown relevance remains -> partial"})
    elif errors:
        if result.get("status") == "ok":
            result["status"] = "partial"
    if result.get("status") not in VALID_STATUS:
        result["status"] = "partial"

    result["change_log"] = (result.get("change_log") or []) + [
        {"ts": now_iso(), "step": "finalize",
         "action": "contract_validation_and_evidence_integrity_check"}
    ]

    write_json(result_path, result)
    selfcheck = {"errors": errors, "warnings": warnings,
                 "error_count": len(errors), "warning_count": len(warnings),
                 "checked_at": now_iso()}
    write_json(os.path.join(a.output, "selfcheck.json"), selfcheck)

    log("finalize: errors=%d warnings=%d evidence_items=%d verified=%d/"
        "unverified=%d/unchecked=%d" % (
            len(errors), len(warnings), len(em["evidence_items"]),
            cov["quote_verified_count"], cov["quote_unverified_count"],
            cov["quote_unchecked_count"]))
    return 0


# ---------------------------------------------------------------------------
# selftest (robustness dry-runs + finalize check)
# ---------------------------------------------------------------------------

def run_selftest(argv):
    p = argparse.ArgumentParser(
        prog="selftest",
        description="Robustness dry-runs (empty/bad-json/missing dir) plus a "
                    "finalize check of the current output.")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    a = p.parse_args(argv)

    report = {"checks": [], "passed": 0, "failed": 0}
    base = tempfile.mkdtemp(prefix="selftest_")

    def case(name):
        d = os.path.join(base, name)
        os.makedirs(d, exist_ok=True)
        return d

    # 1. empty input dir -> insufficient_evidence, exit 0
    d = case("empty")
    rc = run_discover(["--input", d, "--work", os.path.join(d, "work"),
                       "--output", os.path.join(d, "output")])
    res, _ = load_json_safe(os.path.join(d, "output", "result.json"))
    ok = rc == 0 and res and res.get("status") == "insufficient_evidence"
    report["checks"].append({"name": "empty_input", "ok": ok})
    report["passed" if ok else "failed"] += 1

    # 2. bad JSON -> degradation recorded, continue (skeleton still written)
    d = case("badjson")
    open(os.path.join(d, "case.json"), "w", encoding="utf-8").write(
        "{not valid json")
    open(os.path.join(d, "task.md"), "w", encoding="utf-8").write(
        "# test\n研究问题：测试主题")
    rc = run_discover(["--input", d, "--work", os.path.join(d, "work"),
                       "--output", os.path.join(d, "output")])
    res, _ = load_json_safe(os.path.join(d, "output", "result.json"))
    ok = rc == 0 and res and res.get("status") in ("partial", "input_error") \
        and res.get("degradations")
    report["checks"].append({"name": "bad_json", "ok": ok})
    report["passed" if ok else "failed"] += 1

    # 3. missing input dir -> input_error recorded, exit 0
    d = case("missing")
    rc = run_discover(["--input", os.path.join(d, "nope"),
                       "--work", os.path.join(d, "work"),
                       "--output", os.path.join(d, "output")])
    res, _ = load_json_safe(os.path.join(d, "output", "result.json"))
    ok = rc == 0 and res and res.get("status") in (
        "insufficient_evidence", "input_error")
    report["checks"].append({"name": "missing_dir", "ok": ok})
    report["passed" if ok else "failed"] += 1

    # 4. finalize current output if present
    result_path = os.path.join(a.output, "result.json")
    if os.path.exists(result_path):
        rc = run_finalize(["--output", a.output, "--work", a.work])
        report["checks"].append({"name": "finalize", "ok": rc == 0})
        report["passed" if rc == 0 else "failed"] += 1
        sc, _ = load_json_safe(os.path.join(a.output, "selfcheck.json"))
        if sc and sc.get("error_count", 0) > 0:
            report["checks"].append(
                {"name": "finalize_errors_zero", "ok": False,
                 "detail": sc.get("errors")})
            report["failed"] += 1
        else:
            report["checks"].append({"name": "finalize_errors_zero", "ok": True})
            report["passed"] += 1
    else:
        report["checks"].append({"name": "finalize", "ok": False,
                                 "detail": "no result.json yet"})
        report["failed"] += 1

    shutil.rmtree(base, ignore_errors=True)
    log("selftest: passed=%d failed=%d" % (report["passed"], report["failed"]))
    for c in report["checks"]:
        log("  [%s] %s" % ("PASS" if c["ok"] else "FAIL", c["name"]))
    return 0 if report["failed"] == 0 else 1


# ---------------------------------------------------------------------------
# command dispatch (used by main.py)
# ---------------------------------------------------------------------------

COMMANDS = {
    "discover": run_discover,
    "screen": run_screen,
    "finalize": run_finalize,
    "selftest": run_selftest,
}


def run_command(cmd, argv):
    if cmd in COMMANDS:
        return COMMANDS[cmd](argv)
    return 2
