# -*- coding: utf-8 -*-
"""core_impl.py - implementation for the literature screening & ranking skill.

Subcommands: discover / screen / finalize / selftest.
Pure Python standard library (3.8+). Deterministic: same input -> same output.

Design principle
----------------
1. "Discover first, assume later": scan the input directory, do not assume
   fixed file names or key names.
2. "Skeleton before judgment": discover always writes a valid partial
   result.json within seconds, with one full-field stub per candidate, so an
   interrupted run never leaves an empty or structurally incomplete product.
3. "Contract fidelity": every key of output_contract / output_schema /
   expected_fields is echoed verbatim into result.json; unknown keys are never
   renamed or dropped.
4. "Evidence over inference": lexical signals (screen) are priors only and are
   never used as final relevance/priority. Final judgments come from the Agent
   reading actual materials, each backed by locator + verbatim quote.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
STATUS_ENUM = ("ok", "partial", "insufficient_evidence", "input_error")
RELEVANCE_ENUM = ("direct", "indirect", "irrelevant", "unknown")
TIER_ENUM = ("retain", "pending", "exclude")
CONFIDENCE_ENUM = ("high", "medium", "low")
TIER_ORDER = {"retain": 0, "pending": 1, "exclude": 2}

RESULT_FIELDS = [
    "record_id", "title", "authors", "year", "venue", "doi", "url",
    "source_type", "relevance", "tier", "priority_score", "rank",
    "reasons", "evidence", "confidence", "criteria_matched", "conflict",
    "dedupe",
]

TOP_LEVEL_REQUIRED = [
    "case_id", "status", "results", "sources", "uncertainty", "change_log",
]

CANDIDATE_KEYS = [
    "papers", "candidates", "records", "documents", "items", "references",
    "literature", "articles", "pubs", "results", "entries", "hits", "corpus",
]

CONTRACT_KEYS = [
    "output_contract", "output_schema", "expected_fields", "contract",
    "output", "result_schema", "output_format", "required_fields",
]

RQ_KEYS = [
    "research_question", "question", "query", "topic", "research_goal",
    "goal", "research_topic", "rq", "research_question_en",
    "research_question_zh", "problem_statement",
]

CRITERIA_KEYS = [
    "inclusion_criteria", "exclusion_criteria", "criteria", "inclusion",
    "exclusion", "include_criteria", "exclude_criteria", "eligibility",
    "inclusion_exclusion", "screening_criteria",
]

ID_KEYS = [
    "id", "record_id", "pmid", "pmc_id", "pubmed_id", "doi", "arxiv_id",
    "index", "key", "uuid", "accession",
]

MATERIAL_EXTS = {
    ".pdf": "pdf", ".md": "markdown", ".markdown": "markdown",
    ".jsonl": "jsonl", ".txt": "text", ".json": "json", ".csv": "csv",
}

SIZE_LIMIT = 2 * 1024 * 1024  # 2 MB per text source

_TOKEN_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]+")
_STOP = set((
    "a an the of and or in on for to with by from as is are was were be been "
    "this that these those it its at into using based study studies article "
    "paper papers method methods result results analysis data model models"
).split())


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _now():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_text(path):
    """Read a text file tolerantly (BOM, bad encoding)."""
    if not os.path.isfile(path):
        return None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, ValueError, OSError):
            continue
    return None


def _load_json_tolerant(path):
    raw = _read_text(path)
    if raw is None:
        return None, "unreadable"
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as exc:
        return None, "bad_json:%s" % exc


def _write_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    return path


def _file_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _pick_by_basename(files, names):
    wanted = [n.lower() for n in names]
    for fp in files:
        if os.path.basename(fp).lower() in wanted:
            return fp
    return None


def _as_str(value, keys, default=None):
    for k in keys:
        v = value.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return str(v)
    return default


def _tokens(text):
    if not text:
        return set()
    out = set()
    for m in _TOKEN_RE.findall(str(text).lower()):
        if m in _STOP:
            continue
        out.add(m)
    return out


# --------------------------------------------------------------------------
# Candidate / contract extraction
# --------------------------------------------------------------------------
def _find_candidate_container(case):
    if not isinstance(case, dict):
        return None, None
    for k in CANDIDATE_KEYS:
        if k in case and isinstance(case[k], (list, dict)):
            return case[k], k
    for k, v in case.items():
        if isinstance(v, dict):
            for kk in CANDIDATE_KEYS:
                if kk in v and isinstance(v[kk], (list, dict)):
                    return v[kk], "%s.%s" % (k, kk)
    return None, None


def _flatten_records(container):
    """Return a list of candidate dicts from a list/dict-shaped container."""
    if isinstance(container, list):
        return [c for c in container if isinstance(c, dict)]
    if isinstance(container, dict):
        # {"records": [...]} style
        for k in ("records", "items", "list", "data"):
            v = container.get(k)
            if isinstance(v, list):
                return [c for c in v if isinstance(c, dict)]
        # {id: record} style -> inject id if absent
        out = []
        for k, v in container.items():
            if isinstance(v, dict):
                rec = dict(v)
                if not any(kk in rec for kk in ID_KEYS):
                    rec.setdefault("id", k)
                out.append(rec)
        return out
    return []


def _extract_year(raw):
    for k in ("year", "pubdate", "date", "publication_year",
              "published_year", "created", "publication_date"):
        v = raw.get(k)
        if v is None:
            continue
        m = re.search(r"(19|20)\d{2}", str(v))
        if m:
            return int(m.group(0))
    return None


def _norm_authors(raw):
    v = raw.get("authors", raw.get("author", raw.get("creators")))
    if v is None:
        return []
    if isinstance(v, list):
        out = []
        for a in v:
            if isinstance(a, str):
                out.append(a.strip())
            elif isinstance(a, dict):
                nm = a.get("name") or a.get("full_name")
                if not nm:
                    g = a.get("given", "") or a.get("first", "")
                    f = a.get("family", "") or a.get("last", "")
                    nm = ("%s %s" % (g, f)).strip()
                if nm:
                    out.append(nm)
        return [x for x in out if x]
    if isinstance(v, str):
        parts = re.split(r";|\n|,| and |&", v)
        return [p.strip() for p in parts if p.strip()]
    return []


def _pick_record_id(raw, idx):
    for k in ID_KEYS:
        v = raw.get(k)
        if v not in (None, ""):
            return str(v)
    return "rec_%04d" % idx


def _normalize_record(raw, idx):
    if isinstance(raw, str):
        raw = {"title": raw}
    if not isinstance(raw, dict):
        raw = {"title": str(raw)}
    rid = _pick_record_id(raw, idx)
    title = _as_str(raw, ("title", "name", "headline", "paper_title",
                          "publication_title"))
    abstract = _as_str(raw, ("abstract", "summary", "description", "content"))
    year = _extract_year(raw)
    venue = _as_str(raw, ("venue", "journal", "source", "container_title",
                          "publication", "publisher", "booktitle"))
    doi = _as_str(raw, ("doi", "DOI"))
    url = _as_str(raw, ("url", "link", "pdf_url", "landing_url",
                        "fulltext_url"))
    source_type = _as_str(raw, ("source_type", "type", "publication_type",
                                "document_type", "kind"), "unknown")
    authors = _norm_authors(raw)
    return {
        "record_id": rid,
        "title": title,
        "authors": authors,
        "year": year,
        "venue": venue,
        "doi": doi,
        "url": url,
        "abstract": abstract,
        "source_type": source_type,
        "_raw": raw,
    }


def _extract_contract(case):
    if not isinstance(case, dict):
        return None, None
    for k in CONTRACT_KEYS:
        if k in case and case[k] not in (None, "", [], {}):
            return case[k], k
    return None, None


def _contract_fields(contract):
    if isinstance(contract, dict):
        return list(contract.keys())
    if isinstance(contract, list):
        out = []
        for item in contract:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                for fk in ("name", "key", "field", "id"):
                    if item.get(fk):
                        out.append(str(item[fk]))
                        break
        return out
    if isinstance(contract, str):
        try:
            return _contract_fields(json.loads(contract))
        except Exception:
            return [contract]
    return []


def _contract_default(field, contract):
    spec = None
    if isinstance(contract, dict):
        spec = contract.get(field)
    if isinstance(spec, dict):
        t = str(spec.get("type", "")).lower()
        if "list" in t or "array" in t or "seq" in t:
            return []
    if isinstance(spec, str) and any(
            w in spec.lower() for w in ("list", "array", "seq", "records")):
        return []
    return None


def _extract_research_question(case):
    if not isinstance(case, dict):
        return None
    for k in RQ_KEYS:
        if k in case and case[k]:
            v = case[k]
            if isinstance(v, str):
                return v.strip()
            if isinstance(v, dict):
                for kk in ("text", "question", "value", "zh", "en"):
                    if v.get(kk):
                        return str(v[kk]).strip()
    for k, v in case.items():
        if isinstance(v, dict):
            for kk in RQ_KEYS:
                if v.get(kk) and isinstance(v[kk], str):
                    return v[kk].strip()
    return None


def _extract_criteria(case):
    out = {}
    if not isinstance(case, dict):
        return out
    for k in CRITERIA_KEYS:
        if k in case and case[k] not in (None, "", [], {}):
            out[k] = case[k]
    return out


# --------------------------------------------------------------------------
# result stubs
# --------------------------------------------------------------------------
def _result_stub(rec, rank):
    return {
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
        "priority_score": None,
        "rank": rank,
        "reasons": [],
        "evidence": [],
        "confidence": "low",
        "criteria_matched": [],
        "conflict": None,
        "dedupe": None,
    }


# --------------------------------------------------------------------------
# discover
# --------------------------------------------------------------------------
def _write_emergency_skeleton(args, error):
    out = args.output
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "result.json")
    if not os.path.exists(path):
        _write_json(path, {
            "case_id": "unknown",
            "status": "input_error",
            "results": [],
            "dedupe_groups": [],
            "evidence": [],
            "status_notes": "discover failed before a normal skeleton could be written",
            "omissions": [],
            "sources": [],
            "uncertainty": [],
            "pending_human": [{"issue": "discover crashed", "detail": error}],
            "errors": [{"step": "discover", "detail": error}],
            "degradations": [],
            "change_log": [{"ts": _now(), "step": "discover",
                            "action": "emergency_skeleton", "detail": error}],
            "runtime": {"started": _now(), "steps": []},
            "contract": {"discovered_from": None, "fields": [], "alignment": {}},
        })


def _discover_impl(args):
    inp, work, out = args.input, args.work, args.output
    os.makedirs(work, exist_ok=True)
    os.makedirs(out, exist_ok=True)

    change_log = [{"ts": _now(), "step": "discover", "action": "start",
                   "detail": "scan input directory"}]
    degradations, errors = [], []

    input_files = []
    for root, dirs, files in os.walk(inp):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in files:
            if not fn.startswith("."):
                input_files.append(os.path.join(root, fn))
    input_files.sort()

    case_path = _pick_by_basename(input_files, ["case.json", "input.json"])
    task_path = _pick_by_basename(input_files, ["task.md", "task.txt"])

    sources = []
    case_obj, case_text, case_err = None, None, None
    if case_path:
        case_text = _read_text(case_path)
        case_obj, case_err = _load_json_tolerant(case_path)
        sources.append({"path": case_path, "kind": "case_json", "role": "primary"})
        if case_obj is None:
            degradations.append({"path": case_path, "issue": case_err,
                                 "action": "fallback_to_task_text"})
    task_text = None
    if task_path:
        task_text = _read_text(task_path) or ""
        sources.append({"path": task_path, "kind": "task_md",
                        "role": "instructions"})

    materials = []
    for fp in input_files:
        if fp in (case_path, task_path):
            continue
        ext = os.path.splitext(fp)[1].lower()
        kind = MATERIAL_EXTS.get(ext, "other")
        size = _file_size(fp)
        if size > SIZE_LIMIT:
            degradations.append({"path": fp, "issue": "oversize",
                                 "action": "skip"})
            continue
        materials.append({"path": fp, "kind": kind, "role": "material",
                          "size": size})

    # case identity
    case_id = None
    if isinstance(case_obj, dict):
        case_id = _as_str(case_obj, ("case_id", "caseId", "id", "challenge_id"))
    if not case_id and task_text:
        m = re.search(r"challenge[-_` ]*id[:：\s]+`?([A-Za-z0-9\-_]+)", task_text)
        if m:
            case_id = m.group(1)
    if not case_id:
        digest = _hash_str("|".join(input_files))
        case_id = "unknown_%s" % digest[:8]

    rq = _extract_research_question(case_obj) if isinstance(case_obj, dict) else None
    criteria = _extract_criteria(case_obj) if isinstance(case_obj, dict) else {}

    container, container_loc = _find_candidate_container(case_obj)
    raw_records = _flatten_records(container) if container is not None else []
    candidates = [_normalize_record(r, i) for i, r in enumerate(raw_records)]

    contract, contract_src = _extract_contract(case_obj)
    contract_fields = _contract_fields(contract)

    # normalised file
    normalized = {
        "case_id": case_id,
        "research_question": rq,
        "criteria": criteria,
        "candidate_container": container_loc,
        "candidates": candidates,
        "candidate_count": len(candidates),
        "output_contract": contract,
        "output_contract_source": contract_src,
        "output_contract_fields": contract_fields,
        "materials": materials,
        "sources": sources,
        "input_files": input_files,
        "degradations": degradations,
        "errors": [{"step": "discover", "detail": e} for e in errors],
    }
    _write_json(os.path.join(work, "case_normalized.json"), normalized)

    # status honesty
    if case_obj is None and not candidates:
        status = "insufficient_evidence"
        status_notes = "case.json missing or unparseable; no structured candidates"
    elif not candidates:
        status = "insufficient_evidence"
        status_notes = "no candidates found in case.json"
    else:
        status = "partial"
        status_notes = "skeleton written; judgments pending"

    omissions = []
    if not candidates:
        omissions.append({"what": "candidate records",
                          "why": case_err or "empty candidate container",
                          "fix": "supply case.json with a candidate list"})

    # result skeleton
    result = dict()
    for f in contract_fields:
        result[f] = _contract_default(f, contract)

    result["case_id"] = case_id
    result["status"] = status
    result["results"] = [_result_stub(c, i + 1) for i, c in enumerate(candidates)]
    result["dedupe_groups"] = []
    result["evidence"] = []
    result["status_notes"] = status_notes
    result["omissions"] = omissions
    result["sources"] = sources
    result["uncertainty"] = []
    result["pending_human"] = []
    result["errors"] = [{"step": "discover", "detail": e} for e in errors]
    result["degradations"] = degradations
    result["change_log"] = change_log
    result["runtime"] = {"started": _now(), "steps": ["discover"]}
    result["contract"] = {
        "discovered_from": contract_src,
        "fields": contract_fields,
        "alignment": {f: f for f in contract_fields},
    }

    result["change_log"].append({
        "ts": _now(), "step": "discover", "action": "write_skeleton",
        "detail": "case_id=%s candidates=%d contract_fields=%d" % (
            case_id, len(candidates), len(contract_fields))})

    _write_json(os.path.join(out, "result.json"), result)
    print("discover: case_id=%s candidates=%d status=%s contract_fields=%d "
          "materials=%d degradations=%d" % (
              case_id, len(candidates), status, len(contract_fields),
              len(materials), len(degradations)))
    return 0


def _hash_str(s):
    import hashlib
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# screen
# --------------------------------------------------------------------------
def _norm_title(s):
    s = (s or "").lower()
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _norm_doi(s):
    s = (s or "").strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    return s


def _first_author_key(rec):
    authors = rec.get("authors") or []
    first = ""
    if authors:
        a = authors[0]
        first = str(a).strip() if not isinstance(a, dict) else \
            str(a.get("name") or a.get("family") or "").strip()
    surname = (first.split()[-1] if first else "").lower()
    return surname


def _base_score(q_tokens, rec):
    title_tok = _tokens(rec.get("title") or "")
    abstract_tok = _tokens(rec.get("abstract") or "")
    all_tok = title_tok | abstract_tok
    if not q_tokens:
        base = 0.5
    else:
        overlap = len(q_tokens & all_tok)
        ratio = overlap / float(len(q_tokens))
        base = 0.15 + 0.65 * min(1.0, ratio) + 0.1 * (1 if abstract_tok else 0)
    return round(min(0.8, base), 3)


def _cluster_dedupe(candidates):
    n = len(candidates)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    key_map = {}
    for i, c in enumerate(candidates):
        keys = []
        nt = _norm_title(c.get("title"))
        if nt:
            keys.append(("title", nt))
        nd = _norm_doi(c.get("doi"))
        if nd:
            keys.append(("doi", nd))
        fa = _first_author_key(c)
        if fa and nt:
            keys.append(("author_title", fa + "|" + nt[:40]))
        for k in keys:
            if k in key_map:
                union(i, key_map[k])
            else:
                key_map[k] = i

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    clusters = []
    for gid, idxs in enumerate(sorted(groups.values(), key=lambda x: min(x))):
        if len(idxs) > 1:
            rep = min(idxs)
            clusters.append({
                "group_id": "dup_%03d" % gid,
                "members": [candidates[i]["record_id"] for i in sorted(idxs)],
                "representative": candidates[rep]["record_id"],
                "key_type": "title/doi/author_title exact match",
                "decision": None,
                "note": "version/duplicate cluster; decide primary vs duplicate",
            })
    return clusters


def _criteria_text(criteria):
    parts = []
    for v in criteria.values():
        if isinstance(v, list):
            for x in v:
                if isinstance(x, str):
                    parts.append(x)
                elif isinstance(x, dict):
                    parts.append(str(x.get("text") or x.get("criterion") or ""))
        elif isinstance(v, str):
            parts.append(v)
        elif isinstance(v, dict):
            parts.append(str(v.get("text") or v.get("criterion") or ""))
    return " ".join(parts)


def _screen_impl(args):
    work, out = args.work, args.output
    norm_path = os.path.join(work, "case_normalized.json")
    normalized, err = _load_json_tolerant(norm_path)
    if normalized is None:
        print("[error] screen: cannot read %s (%s)" % (norm_path, err))
        return 1

    candidates = normalized.get("candidates", [])
    rq = normalized.get("research_question") or ""
    criteria = normalized.get("criteria") or {}
    q_tokens = _tokens(rq + " " + _criteria_text(criteria))

    records = []
    for i, c in enumerate(candidates):
        title_tok = _tokens(c.get("title") or "")
        abstract_tok = _tokens(c.get("abstract") or "")
        overlap = sorted(q_tokens & (title_tok | abstract_tok))
        records.append({
            "record_id": c["record_id"],
            "base_score": _base_score(q_tokens, c),
            "overlap_tokens": overlap,
            "overlap_count": len(overlap),
            "has_title": bool(c.get("title")),
            "has_abstract": bool(c.get("abstract")),
            "has_year": c.get("year") is not None,
            "has_doi": bool(c.get("doi")),
            "norm_title": _norm_title(c.get("title")),
        })

    clusters = _cluster_dedupe(candidates)

    scaffold = {
        "query_tokens": sorted(q_tokens),
        "records": records,
        "dedupe_groups": clusters,
        "tie_break": [
            "priority_score desc", "evidence_completeness desc",
            "year desc", "norm_title asc", "record_id asc",
        ],
        "note": ("base_score is a deterministic lexical prior only; it must "
                 "NOT be used as the final priority_score. Final relevance/tier/"
                 "priority come from the Agent reading actual materials."),
    }
    _write_json(os.path.join(work, "scaffold.json"), scaffold)

    # Pre-fill result.json dedupe groups + per-record dedupe role.
    result_path = os.path.join(out, "result.json")
    result, rerr = _load_json_tolerant(result_path)
    if result is not None:
        dedupe_map = {}
        for g in clusters:
            rep = g["representative"]
            for m in g["members"]:
                role = "primary_candidate" if m == rep else "duplicate_candidate"
                dedupe_map[m] = {"group_id": g["group_id"], "role": role,
                                 "representative": rep, "decision": None}
        for r in result.get("results", []):
            r["dedupe"] = dedupe_map.get(r.get("record_id"))
        result["dedupe_groups"] = clusters
        result.setdefault("change_log", []).append({
            "ts": _now(), "step": "screen", "action": "scaffold_dedupe",
            "detail": "clusters=%d records=%d" % (len(clusters), len(records))})
        _write_json(result_path, result)

    print("screen: records=%d clusters=%d query_tokens=%d scaffold=%s" % (
        len(records), len(clusters), len(q_tokens),
        os.path.join(work, "scaffold.json")))
    return 0


# --------------------------------------------------------------------------
# finalize
# --------------------------------------------------------------------------
def _check_ordering(results):
    warn = []
    for i in range(1, len(results)):
        a, b = results[i - 1], results[i]
        oa = TIER_ORDER.get(a.get("tier"), 1)
        ob = TIER_ORDER.get(b.get("tier"), 1)
        if oa > ob:
            warn.append("tier order break at %s: '%s' placed before '%s'" % (
                b.get("record_id"), a.get("tier"), b.get("tier")))
            break
    return warn


def _check_priority_monotonic(results):
    warn = []
    for i in range(1, len(results)):
        a, b = results[i - 1], results[i]
        if a.get("tier") == b.get("tier"):
            pa, pb = a.get("priority_score"), b.get("priority_score")
            if pa is not None and pb is not None and pa < pb:
                warn.append("priority not monotonic within tier at %s" %
                            b.get("record_id"))
                break
    return warn


def _finalize_impl(args):
    out, work = args.output, args.work
    result_path = os.path.join(out, "result.json")
    norm_path = os.path.join(work, "case_normalized.json")
    result, rerr = _load_json_tolerant(result_path)
    normalized, nerr = _load_json_tolerant(norm_path)

    errors, warnings = [], []

    if result is None:
        errors.append("result.json unreadable: %s" % rerr)
    if normalized is None:
        warnings.append("case_normalized.json unreadable: %s" % nerr)

    if result is not None:
        for k in TOP_LEVEL_REQUIRED:
            if k not in result:
                errors.append("missing top-level field: %s" % k)
        if result.get("status") not in STATUS_ENUM:
            errors.append("status not in %s: %r" % (
                STATUS_ENUM, result.get("status")))

        # contract fidelity
        contract_fields = result.get("contract", {}).get("fields", [])
        if normalized:
            contract_fields = normalized.get("output_contract_fields", [])
        for f in contract_fields:
            if f not in result:
                errors.append("missing output_contract field: %s" % f)

        # candidate coverage
        expected_ids = set()
        if normalized:
            expected_ids = {c["record_id"]
                            for c in normalized.get("candidates", [])}
        got_ids = [r.get("record_id") for r in result.get("results", [])]
        got_set = set(got_ids)
        missing = sorted(expected_ids - got_set)
        extra = sorted(got_set - expected_ids)
        dup = sorted({x for x in got_ids if got_ids.count(x) > 1})
        if missing:
            errors.append("missing candidate records: %s" %
                          ", ".join(missing[:8]))
        if extra:
            warnings.append("extra records not in candidates: %s" %
                            ", ".join(extra[:8]))
        if dup:
            errors.append("duplicate record_id in results: %s" %
                          ", ".join(dup))

        # per-record checks
        n_unknown = 0
        for r in result.get("results", []):
            rid = r.get("record_id", "?")
            for k in RESULT_FIELDS:
                if k not in r:
                    errors.append("%s: missing field %s" % (rid, k))
            if r.get("relevance") not in RELEVANCE_ENUM:
                errors.append("%s: relevance not in %s: %r" % (
                    rid, RELEVANCE_ENUM, r.get("relevance")))
            if r.get("tier") not in TIER_ENUM:
                errors.append("%s: tier not in %s: %r" % (
                    rid, TIER_ENUM, r.get("tier")))
            if r.get("confidence") not in CONFIDENCE_ENUM:
                warnings.append("%s: confidence not in %s" % (
                    rid, CONFIDENCE_ENUM))
            ps = r.get("priority_score")
            if ps is not None:
                try:
                    if not (0.0 <= float(ps) <= 1.0):
                        errors.append("%s: priority_score out of [0,1]: %r" % (
                            rid, ps))
                except (TypeError, ValueError):
                    errors.append("%s: priority_score non-numeric: %r" % (rid, ps))
            if r.get("relevance") == "unknown":
                n_unknown += 1
            # evidence requirement for judged-retain records
            if r.get("tier") == "retain" and r.get("relevance") in (
                    "direct", "indirect"):
                ev = r.get("evidence") or []
                bad = [e for e in ev if not (isinstance(e, dict)
                       and e.get("locator") and e.get("quote"))]
                if not ev or bad:
                    errors.append("%s: retain record lacks locator+quote "
                                  "evidence" % rid)

        # ordering / monotonicity
        warnings.extend(_check_ordering(result.get("results", [])))
        warnings.extend(_check_priority_monotonic(result.get("results", [])))

        # rank sequence
        ranks = [r.get("rank") for r in result.get("results", [])]
        if ranks and sorted(ranks) != list(range(1, len(ranks) + 1)):
            warnings.append("rank values are not a consecutive 1..N sequence")

        # status honesty: 'ok' requires zero errors
        if result.get("status") == "ok" and errors:
            warnings.append("status='ok' but %d errors remain" % len(errors))
        # unknown relevance should be explained in uncertainty
        if n_unknown and not result.get("uncertainty"):
            warnings.append("%d records with relevance=unknown but no "
                            "uncertainty notes" % n_unknown)

        # aggregate a top-level evidence index from per-record evidence
        agg = []
        for r in result.get("results", []):
            for e in (r.get("evidence") or []):
                if isinstance(e, dict):
                    agg.append({
                        "record_id": r.get("record_id"),
                        "locator": e.get("locator"),
                        "quote": e.get("quote"),
                        "tier": r.get("tier"),
                    })
        result["evidence"] = agg

        # contract fields echoed but still empty: prompt the Agent to verify
        # whether they should mirror the filled 'results' list.
        if result.get("results"):
            for f in contract_fields:
                if f in result and result[f] in (None, "", []):
                    warnings.append(
                        "contract field '%s' echoed but empty; verify whether "
                        "it should mirror 'results'" % f)

        # append finalize step to change_log (idempotent marker)
        result.setdefault("change_log", []).append({
            "ts": _now(), "step": "finalize", "action": "selfcheck",
            "detail": "errors=%d warnings=%d" % (len(errors), len(warnings))})
        runtime = result.setdefault("runtime", {})
        runtime["finished"] = _now()
        runtime["steps"] = list(dict.fromkeys(
            (runtime.get("steps") or []) + ["finalize"]))
        _write_json(result_path, result)

    selfcheck = {
        "errors": len(errors),
        "warnings": len(warnings),
        "errors_list": errors,
        "warnings_list": warnings,
        "ok": len(errors) == 0,
        "generated": _now(),
    }
    _write_json(os.path.join(work, "selfcheck.json"), selfcheck)

    print("finalize: errors=%d warnings=%d selfcheck=%s" % (
        len(errors), len(warnings), os.path.join(work, "selfcheck.json")))
    return 0


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------
def _selftest_impl(_args):
    tmp = tempfile.mkdtemp(prefix="litscreen_selftest_")
    results = []
    try:
        inp = os.path.join(tmp, "input")
        work = os.path.join(tmp, "work")
        out = os.path.join(tmp, "output")
        os.makedirs(inp)

        fixture = {
            "case_id": "selftest_case",
            "research_question": ("Does drug X reduce all-cause mortality in "
                                  "adults with heart failure?"),
            "inclusion_criteria": ["randomized controlled trial", "adults",
                                   "heart failure", "mortality outcome"],
            "exclusion_criteria": ["animal study", "pediatric population"],
            "output_contract": {
                "retrieved_records": "list",
                "screening_decisions": "list",
                "evidence_map": "list",
            },
            "papers": [
                {"id": "p1",
                 "title": ("Randomized trial of drug X on mortality in "
                           "heart failure"),
                 "authors": ["Smith J", "Doe A"], "year": 2021,
                 "journal": "NEJM", "doi": "10.1000/xyz1",
                 "abstract": ("RCT of drug X versus placebo in 5000 adults "
                              "with heart failure; primary outcome all-cause "
                              "mortality.")},
                {"id": "p2",
                 "title": "Drug X in a mouse model of cardiomyopathy",
                 "authors": ["Lee K"], "year": 2020,
                 "journal": "J Mol Cell", "doi": "10.1000/xyz2",
                 "abstract": ("We administered drug X to mice and measured "
                              "cardiac function and fibrosis.")},
                {"id": "p3",
                 "title": ("Randomized trial of drug X on mortality in "
                           "heart failure"),
                 "authors": ["Smith J", "Doe A", "Roe B"], "year": 2022,
                 "journal": "JACC", "doi": "10.1000/xyz1",
                 "abstract": ("Published version of the same RCT; 24-month "
                              "all-cause mortality results.")},
            ],
        }
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(fixture, fh, ensure_ascii=False)
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# selftest task\n筛选并排序候选文献，保留依据。\n")

        ns = argparse.Namespace(input=inp, work=work, output=out)
        rc = _discover_impl(ns)
        results.append(("discover runs", rc == 0))

        res_path = os.path.join(out, "result.json")
        res, _ = _load_json_tolerant(res_path)
        results.append(("skeleton written", res is not None))
        results.append(("status partial", res and res.get("status") == "partial"))
        results.append(("case_id", res and res.get("case_id") == "selftest_case"))
        results.append(("contract echoed", res and all(
            k in res for k in ("retrieved_records", "screening_decisions",
                               "evidence_map"))))
        results.append(("3 stubs", res and len(res.get("results", [])) == 3))

        _screen_impl(argparse.Namespace(work=work, output=out))
        scaf, _ = _load_json_tolerant(os.path.join(work, "scaffold.json"))
        results.append(("scaffold written", scaf is not None))
        # reload result.json: screen wrote dedupe_groups onto disk after res was read
        res = _load_json_tolerant(res_path)[0] or res
        groups = (res.get("dedupe_groups") or [])
        results.append(("dedupe cluster found", len(groups) == 1 and
                        set(groups[0]["members"]) == {"p1", "p3"}))

        # Simulate Agent filling judgments (2 retain, 1 exclude).
        if res:
            for r in res["results"]:
                rid = r["record_id"]
                if rid in ("p1", "p3"):
                    r["relevance"] = "direct"; r["tier"] = "retain"
                    r["priority_score"] = 0.9 if rid == "p1" else 0.85
                    r["reasons"] = ["RCT in adults with heart failure; "
                                    "mortality outcome"]
                    r["evidence"] = [{"locator": "case.json#papers", "quote":
                                      "primary outcome all-cause mortality"}]
                    r["confidence"] = "high"
                    r["criteria_matched"] = ["randomized controlled trial",
                                             "adults", "mortality outcome"]
                else:
                    r["relevance"] = "irrelevant"; r["tier"] = "exclude"
                    r["priority_score"] = 0.05
                    r["reasons"] = ["animal study, excluded by criteria"]
                    r["evidence"] = [{"locator": "case.json#papers", "quote":
                                      "administered drug X to mice"}]
                    r["confidence"] = "high"
                    r["criteria_matched"] = []
            # reorder to retain -> exclude and reassign consecutive ranks
            order = {"retain": 0, "pending": 1, "exclude": 2}
            res["results"].sort(key=lambda r: (
                order.get(r.get("tier"), 1),
                -(float(r["priority_score"])
                  if r.get("priority_score") is not None else 0.0),
            ))
            for i, r in enumerate(res["results"], 1):
                r["rank"] = i
            _write_json(res_path, res)

        _finalize_impl(argparse.Namespace(output=out, work=work))
        sc, _ = _load_json_tolerant(os.path.join(work, "selfcheck.json"))
        results.append(("finalize errors=0", sc is not None and sc.get("errors") == 0))

        # empty input -> insufficient_evidence
        empty_in = os.path.join(tmp, "empty_in")
        os.makedirs(empty_in)
        ns2 = argparse.Namespace(input=empty_in, work=os.path.join(tmp, "w2"),
                                 output=os.path.join(tmp, "o2"))
        _discover_impl(ns2)
        r2, _ = _load_json_tolerant(os.path.join(tmp, "o2", "result.json"))
        results.append(("empty input honest", r2 is not None and
                        r2.get("status") == "insufficient_evidence"))

        # bad JSON -> degrade, no crash
        bad_in = os.path.join(tmp, "bad_in")
        os.makedirs(bad_in)
        with open(os.path.join(bad_in, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        ns3 = argparse.Namespace(input=bad_in, work=os.path.join(tmp, "w3"),
                                 output=os.path.join(tmp, "o3"))
        _discover_impl(ns3)
        r3, _ = _load_json_tolerant(os.path.join(tmp, "o3", "result.json"))
        results.append(("bad json degrades", r3 is not None and
                        (r3.get("degradations") or r3.get("status") in (
                            "insufficient_evidence", "input_error"))))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    passed = [n for n, ok in results if ok]
    failed = [n for n, ok in results if not ok]
    for n, ok in results:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", n))
    print("selftest: %d/%d passed" % (len(passed), len(results)))
    return 0 if not failed else 1


# --------------------------------------------------------------------------
# command dispatch
# --------------------------------------------------------------------------
def _build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Literature screening & ranking skill (stdlib only)")
    sub = p.add_subparsers(dest="command")

    d = sub.add_parser("discover", help="discover inputs and write skeleton")
    d.add_argument("--input", default="/input")
    d.add_argument("--work", default="/app/work")
    d.add_argument("--output", default="/app/output")

    s = sub.add_parser("screen", help="lexical signals + dedup scaffolding")
    s.add_argument("--work", default="/app/work")
    s.add_argument("--output", default="/app/output")

    f = sub.add_parser("finalize", help="validate result.json + selfcheck")
    f.add_argument("--output", default="/app/output")
    f.add_argument("--work", default="/app/work")

    sub.add_parser("selftest", help="run self-test on a synthetic fixture")
    return p


def main(argv):
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    cmd = args.command
    try:
        if cmd == "discover":
            return _discover_impl(args)
        if cmd == "screen":
            return _screen_impl(args)
        if cmd == "finalize":
            return _finalize_impl(args)
        if cmd == "selftest":
            return _selftest_impl(args)
        parser.print_help()
        return 2
    except Exception as exc:  # never crash the pipeline
        print("[error] %s failed: %s" % (cmd, exc), file=sys.stderr)
        if cmd == "discover":
            _write_emergency_skeleton(args, str(exc))
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
