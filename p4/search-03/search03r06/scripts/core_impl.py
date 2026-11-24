"""core_impl.py — implementation of the auditable literature-screening pipeline.

Design ("intermediate representation & auditable decisions"):
  The pipeline separates three concerns so every final decision can be traced
  from (plan rule) x (material evidence) and never from a black box:

    1. plan.json           — the compiled, machine-checkable screening plan.
                             Criteria are normalized into named predicates;
                             tier gates, tie-break order, dedupe rules and
                             conflict rules are written down explicitly.
    2. scaffold.json       — deterministic, reproducible signals only:
                             metadata completeness, lexical term overlap,
                             study-type hints, and duplicate/version clustering.
    3. evidence_ledger.json — per-record semantic judgments filled by the Agent:
                             relevance / tier / priority_score / reasons /
                             evidence (locator + verbatim quote) / confidence /
                             conflicts. This is the ONLY place semantic
                             judgments live.

  finalize() merges the three and rejects decisions that are inconsistent with
  the plan or lack the required evidence locator/quote. Nothing is invented:
  an unfilled record stays relevance="unknown", tier="pending", score=null.

Pure Python 3 standard library only. Imported by main.py and selftest.py.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import sys
import unicodedata

VERSION = "1.1.0"

RELEVANCE_ENUM = ("direct", "indirect", "irrelevant", "unknown")
TIER_ENUM = ("retain", "pending", "exclude")
CONFIDENCE_ENUM = ("high", "medium", "low")
STATUS_ENUM = ("ok", "partial", "insufficient_evidence", "input_error")

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB per input text file
MAX_CANDIDATES = 5000            # hard safety cap; beyond this we degrade honestly

STOPWORDS = set(
    "a an the and or of to in for on with by at from as is are was were be been being "
    "this that these those it its into over under between among not no such than then "
    "but if also can may will would should could".split()
)

VERSION_MARKERS = (
    "preprint", "pre-print", "pre print", "published version", "final version",
    "revised", "version 2", "version 3", "v2", "v3", "author accepted manuscript",
)

STUDY_TYPE_PATTERNS = (
    ("systematic_review", r"systematic\s+review"),
    ("meta_analysis", r"meta[- ]?analysis"),
    ("randomized_trial", r"randomi[sz]ed\s+(controlled\s+)?(clinical\s+)?trial|\bRCT\b"),
    ("cohort", r"\bcohort\b"),
    ("case_control", r"case[- ]?control"),
    ("cross_sectional", r"cross[- ]?sectional"),
    ("case_report", r"case\s+report|\bcase\s+series\b"),
    ("protocol", r"\bprotocol\b"),
    ("preprint", r"\bpreprint\b"),
    ("editorial", r"\beditorial\b"),
    ("commentary", r"\bcomment(?:ary)?\b|\bletter\s+to\s+the\s+editor\b"),
    ("review", r"\breview\b"),
)

# --------------------------------------------------------------------------- #
# Robust text / JSON reading
# --------------------------------------------------------------------------- #

def read_text(path, max_bytes=MAX_FILE_BYTES):
    """Read a UTF-8 text file, tolerating BOM / broken encodings / oversize.

    Returns (text or None, degradation message or None).
    """
    if not os.path.isfile(path):
        return None, "missing file: %s" % path
    size = os.path.getsize(path)
    if size > max_bytes:
        return None, "skipped oversize file (>%d bytes): %s" % (max_bytes, path)
    raw = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            raw = data.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if raw is None:
        return None, "undecodable file: %s" % path
    return raw, None


def load_json(path):
    """Load JSON with BOM/comment tolerance. Returns (obj, err) tuple."""
    text, err = read_text(path)
    if err is not None:
        return None, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        # Retry after stripping a UTF-8 BOM and trailing commas (common hand edits).
        try:
            cleaned = re.sub(r",\s*([\]}])", r"\1", text.lstrip("\ufeff"))
            return json.loads(cleaned), None
        except json.JSONDecodeError:
            return None, "bad JSON in %s: %s" % (path, exc)


def write_json(path, obj):
    """Atomically write pretty JSON (UTF-8, no BOM)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Field discovery ("discover first, assume second")
# --------------------------------------------------------------------------- #

CANDIDATE_CONTAINER_KEYS = (
    "papers", "candidates", "records", "results", "items", "articles",
    "documents", "references", "literature", "hits", "entries",
    "search_results", "query_results", "ranked_results", "documents",
)

TITLE_KEYS = ("title", "name", "paper_title", "article_title", "heading")
RESEARCH_QUESTION_KEYS = (
    "research_question", "question", "query", "research_topic", "objective",
    "research_goal", "topic", "clinical_question", "pico_question",
)
CRITERIA_KEYS = (
    "inclusion_criteria", "exclusion_criteria", "inclusion", "exclusion",
    "criteria", "include_criteria", "exclude_criteria", "eligibility",
    "eligibility_criteria", "inclusion_exclusion",
)
CONTRACT_KEYS = ("output_contract", "output_schema", "expected_fields", "required_fields")
CASE_ID_KEYS = ("case_id", "id", "challenge_id", "task_id", "caseId", "query_id")


def _has_title_like(d):
    if not isinstance(d, dict):
        return False
    for k in TITLE_KEYS:
        if k in d and isinstance(d.get(k), str) and d.get(k).strip():
            return True
    return False


def _iter_candidate_lists(node, depth=0):
    """Yield every list of dicts that looks like a candidate list."""
    if depth > 6:
        return
    if isinstance(node, dict):
        for value in node.values():
            yield from _iter_candidate_lists(value, depth + 1)
    elif isinstance(node, list):
        dicts = [x for x in node if isinstance(x, dict)]
        if dicts and sum(1 for x in dicts if _has_title_like(x)) >= 1:
            yield node
        for item in node:
            if isinstance(item, (dict, list)):
                yield from _iter_candidate_lists(item, depth + 1)


def find_candidates(data):
    """Return the best candidate list (list of dicts) or []."""
    if not isinstance(data, dict):
        return []
    # Prefer well-known container keys first.
    for key in CANDIDATE_CONTAINER_KEYS:
        value = data.get(key)
        if isinstance(value, list) and any(_has_title_like(x) for x in value if isinstance(x, dict)):
            return value
        if isinstance(value, dict):
            for sub_key in CANDIDATE_CONTAINER_KEYS:
                sub = value.get(sub_key)
                if isinstance(sub, list) and any(_has_title_like(x) for x in sub if isinstance(x, dict)):
                    return sub
    # Fall back to the largest title-bearing list anywhere.
    best = []
    for lst in _iter_candidate_lists(data):
        if len(lst) > len(best):
            best = lst
    return best


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _normalize_criterion(raw, idx, default_kind="criteria"):
    if isinstance(raw, dict):
        text = raw.get("text") or raw.get("description") or raw.get("criterion") or raw.get("content") or ""
        kind = raw.get("kind") or raw.get("type") or default_kind
        label = raw.get("label") or raw.get("name") or raw.get("id")
    else:
        text = str(raw)
        kind = default_kind
        label = None
    if not str(text).strip():
        return None
    return {
        "id": "c%02d" % (idx + 1),
        "kind": str(kind),
        "text": str(text).strip(),
        "label": str(label).strip() if label else None,
    }


def find_criteria(data):
    """Extract explicit inclusion/exclusion criteria if present."""
    if not isinstance(data, dict):
        return []
    criteria = []
    idx = 0
    for key in CRITERIA_KEYS:
        value = data.get(key)
        if value is None:
            continue
        kind = "exclusion" if ("exclu" in key or key in ("exclude_criteria", "exclusion")) else "inclusion"
        for raw in _as_list(value):
            norm = _normalize_criterion(raw, idx, kind)
            if norm:
                criteria.append(norm)
                idx += 1
    return criteria


def find_research_question(data):
    if not isinstance(data, dict):
        return ""
    for key in RESEARCH_QUESTION_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for sub in ("text", "question", "topic", "title"):
                if isinstance(value.get(sub), str) and value[sub].strip():
                    return value[sub].strip()
    return ""


def find_case_id(data, task_text=""):
    if isinstance(data, dict):
        for key in CASE_ID_KEYS:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    m = re.search(r"challenge[_-]?id[:\s]*`?([A-Za-z0-9._-]+)", task_text or "")
    if m:
        return m.group(1)
    return "case-" + hashlib.sha1((task_text or "empty").encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------- #
# Text normalization / lexical signals (deterministic only)
# --------------------------------------------------------------------------- #

def norm_text(s):
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s)).lower()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in s).split())


def tokens(s):
    return [t for t in norm_text(s).split() if t and t not in STOPWORDS and len(t) > 1]


def detect_study_type(text):
    t = norm_text(text)
    found = []
    for label, pattern in STUDY_TYPE_PATTERNS:
        if re.search(pattern, t):
            found.append(label)
    return found


def norm_title_key(title):
    """Version-insensitive title key for dedupe clustering."""
    t = norm_text(title)
    for marker in VERSION_MARKERS:
        t = re.sub(r"\b" + re.escape(norm_text(marker)) + r"\b", " ", t)
    return " ".join(t.split())


def metadata_completeness(rec):
    fields = ("title", "authors", "year", "venue", "doi", "abstract")
    present = 0
    for f in fields:
        v = rec.get(f)
        if v not in (None, "", [], {}):
            present += 1
    return present / len(fields)


def lexical_signals(rec, research_question):
    q_terms = set(tokens(research_question))
    title_terms = set(tokens(rec.get("title")))
    abstract_terms = set(tokens(rec.get("abstract")))
    title_hits = sorted(q_terms & title_terms)
    abstract_hits = sorted(q_terms & abstract_terms)
    all_text = norm_text(" ".join(str(rec.get(k) or "") for k in ("title", "abstract")))
    return {
        "title_term_hits": title_hits,
        "abstract_term_hits": abstract_hits,
        "title_hit_count": len(title_hits),
        "abstract_hit_count": len(abstract_hits),
        "study_type_hints": detect_study_type(all_text),
        "query_term_count": len(q_terms),
    }


def lexical_base_score(sig):
    """Deterministic lexical prior in [0,1]. NOT a relevance judgment."""
    score = 0.0
    if sig["title_hit_count"] > 0:
        score += 0.35
    if sig["abstract_hit_count"] > 0:
        score += 0.25
    hints = sig["study_type_hints"]
    if any(h in ("systematic_review", "meta_analysis", "randomized_trial") for h in hints):
        score += 0.15
    elif any(h in ("cohort", "case_control", "cross_sectional") for h in hints):
        score += 0.10
    return round(min(score, 0.80), 3)


# --------------------------------------------------------------------------- #
# Dedupe clustering (deterministic)
# --------------------------------------------------------------------------- #

def _doi_key(rec):
    d = rec.get("doi")
    if isinstance(d, str) and d.strip():
        return norm_text(d)
    return ""


def cluster_dedupe(records):
    """Group records into duplicate/version clusters.

    Rules (in order):
      R1 identical non-empty DOI
      R2 identical normalized title
      R3 identical version-insensitive normalized title (preprint vs published)
    Within a group, prefer a formally published record as `primary`; otherwise
    the highest metadata completeness; tie-break by earliest record_id.
    """
    n = len(records)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    dois = {}
    titles = {}
    vtitles = {}
    for i, rec in enumerate(records):
        dk = _doi_key(rec)
        if dk:
            if dk in dois:
                union(i, dois[dk])
            else:
                dois[dk] = i
        tk = norm_text(rec.get("title"))
        if tk:
            if tk in titles:
                union(i, titles[tk])
            else:
                titles[tk] = i
        vtk = norm_title_key(rec.get("title"))
        if vtk:
            if vtk in vtitles:
                union(i, vtitles[vtk])
            else:
                vtitles[vtk] = i

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    dedupe_groups = []
    rec_dedupe = [None] * n
    for root in sorted(groups):
        members = groups[root]
        if len(members) < 2:
            continue
        gid = "dg%02d" % (len(dedupe_groups) + 1)
        # Choose primary: published-like, then completeness, then record_id.
        def published_like(i):
            rec = records[i]
            st = norm_text(rec.get("source_type") or "")
            has_venue = bool(rec.get("venue"))
            has_doi = bool(_doi_key(rec))
            return ("preprint" not in st and (has_venue or has_doi))
        ordered = sorted(
            members,
            key=lambda i: (
                not published_like(i),
                -metadata_completeness(records[i]),
                records[i].get("record_id", "zzzz"),
            ),
        )
        primary = ordered[0]
        reason = "identical DOI/title" if (_doi_key(records[primary]) and any(
            _doi_key(records[i]) == _doi_key(records[primary]) for i in members if i != primary
        )) else "normalized/version-insensitive title match"
        for j, i in enumerate(ordered):
            rec_dedupe[i] = {
                "group_id": gid,
                "role": "primary" if i == primary else "version_variant",
                "primary_record_id": records[primary].get("record_id"),
            }
        dedupe_groups.append({
            "group_id": gid,
            "record_ids": [records[i].get("record_id") for i in ordered],
            "primary_record_id": records[primary].get("record_id"),
            "reason": reason,
            "ruling": "keep primary, demote variants",
            "needs_human": True,
        })
    return rec_dedupe, dedupe_groups


# --------------------------------------------------------------------------- #
# Pipeline steps
# --------------------------------------------------------------------------- #

def _ensure_dirs(work_dir, output_dir):
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)


def _append_log(work_dir, step, action, details=None):
    path = os.path.join(work_dir, "change_log.json")
    log = []
    if os.path.isfile(path):
        obj, _ = load_json(path)
        if isinstance(obj, list):
            log = obj
    entry = {"ts": now_iso(), "step": step, "action": action}
    if details:
        entry["details"] = details
    log.append(entry)
    write_json(path, log)
    return log


def _touch_run_state(work_dir, key, value):
    path = os.path.join(work_dir, "run_state.json")
    state = {}
    if os.path.isfile(path):
        obj, _ = load_json(path)
        if isinstance(obj, dict):
            state = obj
    state[key] = value
    state.setdefault("started_at", now_iso())
    write_json(path, state)


def _iter_input_files(input_dir):
    """Yield (abs_path, rel_path) for every supported file under input_dir."""
    supported = {".json", ".jsonl", ".md", ".txt", ".csv", ".pdf"}
    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            if os.path.splitext(name)[1].lower() not in supported:
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir)
            yield full, rel


def discover(input_dir, work_dir, output_dir):
    """Step 1: discover inputs, normalize, compile plan, write result skeleton.

    Always exits 0. On any failure it writes a *legal* partial result and
    records errors/degradations; it never leaves an empty output directory.
    """
    _ensure_dirs(work_dir, output_dir)
    errors, degradations, pending_human, sources = [], [], [], []
    input_files = list(_iter_input_files(input_dir))
    if not input_files:
        errors.append("input directory has no readable files: %s" % input_dir)

    case_obj, case_err = None, "case.json not found"
    task_text = ""
    for full, rel in input_files:
        base = os.path.basename(rel).lower()
        if base == "case.json":
            case_obj, case_err = load_json(full)
            sources.append({"path": rel, "kind": "json", "role": "case"})
        elif base in ("task.md", "task.txt", "readme.md"):
            txt, _ = read_text(full)
            task_text = txt or ""
            sources.append({"path": rel, "kind": "markdown", "role": "task"})
        else:
            sources.append({"path": rel, "kind": "material", "role": "material"})

    input_error = False
    if case_obj is None:
        input_error = True
        degradations.append("case.json unreadable or absent: %s" % case_err)
        errors.append("cannot parse case.json; continuing from task.md only")

    data = case_obj if isinstance(case_obj, dict) else {}
    case_id = find_case_id(data, task_text)
    research_question = find_research_question(data)
    criteria = find_criteria(data)
    output_contract = None
    if isinstance(data, dict):
        for key in CONTRACT_KEYS:
            if key in data:
                output_contract = data[key]
                break

    raw_candidates = find_candidates(data)
    if not raw_candidates and not input_error:
        # The candidate list may live in task.md as a structured block; do not guess
        # content, but record the absence honestly.
        pending_human.append("no candidate list found in case.json; check task.md/materials")

    if len(raw_candidates) > MAX_CANDIDATES:
        degradations.append(
            "candidate list truncated: %d > %d" % (len(raw_candidates), MAX_CANDIDATES)
        )
        raw_candidates = raw_candidates[:MAX_CANDIDATES]

    candidates = []
    for i, raw in enumerate(raw_candidates):
        rid = raw.get("record_id") or raw.get("id") or raw.get("uid") or ("rec_%04d" % i)
        title = raw.get("title") or raw.get("paper_title") or raw.get("name")
        authors = raw.get("authors") or raw.get("author")
        if isinstance(authors, str):
            authors = [a.strip() for a in re.split(r";|,|\band\b", authors) if a.strip()]
        elif not isinstance(authors, list):
            authors = []
        year = raw.get("year") or raw.get("pub_year") or raw.get("publication_year")
        venue = raw.get("venue") or raw.get("journal") or raw.get("publication")
        doi = raw.get("doi") or raw.get("DOI")
        url = raw.get("url") or raw.get("link") or raw.get("href")
        abstract = raw.get("abstract") or raw.get("summary") or raw.get("description")
        source_type = raw.get("source_type") or raw.get("type") or raw.get("document_type") or "unknown"
        candidates.append({
            "record_id": str(rid),
            "title": title,
            "authors": authors,
            "year": year,
            "venue": venue,
            "doi": doi,
            "url": url,
            "abstract": abstract,
            "source_type": source_type,
            "source_file": "case.json",
        })

    case_norm = {
        "case_id": case_id,
        "research_question": research_question,
        "task_text": task_text,
        "criteria": criteria,
        "output_contract": output_contract,
        "candidates": candidates,
        "input_files": [{"path": s["path"], "kind": s["kind"], "role": s["role"]} for s in sources],
        "errors": errors,
        "degradations": degradations,
        "pending_human": pending_human,
        "input_error": input_error,
    }
    write_json(os.path.join(work_dir, "case_normalized.json"), case_norm)

    plan = compile_plan(case_id, research_question, criteria)
    write_json(os.path.join(work_dir, "plan.json"), plan)

    # Immediate partial skeleton: every candidate already present, judged unknown.
    skeleton_results = []
    for c in candidates:
        skeleton_results.append(make_record_result(c, None, None, None))
    skeleton = build_result_document(
        case_id=case_id,
        status=("insufficient_evidence" if not candidates else "partial"),
        research_question=research_question,
        results=skeleton_results,
        plan=plan,
        sources=sources,
        errors=errors,
        degradations=degradations,
        pending_human=pending_human,
        change_log=[],
    )
    skeleton["status_notes"] = [
        "partial skeleton written before any semantic judgment; improve incrementally"
    ] if candidates else ["no candidates found; nothing was fabricated"]
    write_json(os.path.join(output_dir, "result.json"), skeleton)

    _append_log(work_dir, "discover", "discovered inputs and wrote partial result skeleton",
                {"candidates": len(candidates), "criteria": len(criteria),
                 "input_files": len(input_files)})
    _touch_run_state(work_dir, "discover_at", now_iso())

    return {
        "case_id": case_id,
        "candidate_count": len(candidates),
        "criteria_count": len(criteria),
        "input_error": input_error,
        "errors": errors,
        "degradations": degradations,
    }


def compile_plan(case_id, research_question, criteria):
    """Compile the machine-checkable screening plan (the key intermediate artifact)."""
    plan_criteria = list(criteria)
    if not plan_criteria:
        plan_criteria.append({
            "id": "c00",
            "kind": "derived",
            "text": "Relevance to the stated research question (population/object, method, outcome, time, comparability).",
            "label": "research-question relevance (derived placeholder; refine from task.md)",
        })
    return {
        "plan_id": "plan-" + (case_id or "unknown"),
        "version": VERSION,
        "research_question": research_question,
        "criteria": plan_criteria,
        "tier_gates": {
            "retain": {"relevance": ["direct"], "note": "direct + enough evidence"},
            "pending": {"relevance": ["indirect", "unknown"], "note": "indirect, ambiguous, or evidence insufficient"},
            "exclude": {"relevance": ["irrelevant"], "note": "fails inclusion or meets exclusion"},
        },
        "tie_break": [
            "tier_rank asc (retain<pending<exclude)",
            "priority_score desc",
            "metadata_completeness desc",
            "year desc",
            "norm_title asc",
            "record_id asc",
        ],
        "dedupe_rules": [
            "R1: identical non-empty DOI merges records",
            "R2: identical normalized title merges records",
            "R3: version-insensitive title merges preprint/published variants",
            "primary = published-like, then highest metadata completeness, then earliest record_id",
        ],
        "conflict_rules": [
            "source_conflict: record all disagreeing values, do not silently pick one",
            "abstract_insufficient: relevance=unknown, tier=pending, confidence=low",
            "duplicate/version: demote variant, keep primary, note group_id",
            "label_disagreement: escalate to pending_human with both sides quoted",
        ],
        "score_bands": {
            "0.8-1.0": "core evidence, read first",
            "0.5-0.79": "method/context reference",
            "0.0-0.49": "background or likely exclude",
        },
    }


def scaffold(work_dir, output_dir):
    """Step 3: deterministic signals + dedupe + evidence-ledger template.

    No model requests. The `base_score` is a lexical prior only and is never
    used as a final relevance judgment.
    """
    _ensure_dirs(work_dir, output_dir)
    norm, err = load_json(os.path.join(work_dir, "case_normalized.json"))
    if err is not None or not isinstance(norm, dict):
        write_json(os.path.join(work_dir, "scaffold.json"), {"records": [], "dedupe_groups": [], "error": err})
        return {"error": err}
    records = norm.get("candidates") or []
    rq = norm.get("research_question") or ""

    rec_dedupe, dedupe_groups = cluster_dedupe(records)
    scaffold_records = []
    for i, rec in enumerate(records):
        sig = lexical_signals(rec, rq)
        base = lexical_base_score(sig)
        scaffold_records.append({
            "record_id": rec.get("record_id"),
            "metadata_completeness": round(metadata_completeness(rec), 3),
            "lexical": sig,
            "base_score": base,
            "dedupe": rec_dedupe[i],
        })
    scaffold_obj = {"records": scaffold_records, "dedupe_groups": dedupe_groups}
    write_json(os.path.join(work_dir, "scaffold.json"), scaffold_obj)

    # Evidence ledger template: semantic judgments to be filled by the Agent.
    ledger = {
        "case_id": norm.get("case_id"),
        "filled": False,
        "status_hint": None,
        "notes": [],
        "records": [
            {
                "record_id": rec.get("record_id"),
                "relevance": "unknown",
                "tier": "pending",
                "priority_score": None,
                "reasons": [],
                "evidence": [],
                "criteria_matched": [],
                "confidence": "low",
                "conflict": None,
                "dedupe_override": None,
                "pending_human": "",
            }
            for rec in records
        ],
    }
    write_json(os.path.join(work_dir, "evidence_ledger.json"), ledger)

    _append_log(work_dir, "scaffold", "built deterministic scaffold and evidence-ledger template",
                {"records": len(records), "dedupe_groups": len(dedupe_groups)})
    _touch_run_state(work_dir, "scaffold_at", now_iso())
    return {"records": len(records), "dedupe_groups": len(dedupe_groups)}


def make_record_result(candidate, judgment, scaffold_rec, default_rank):
    """Build one final result record from candidate metadata + judgment."""
    if judgment is None:
        judgment = {}
    rec = {
        "record_id": candidate.get("record_id"),
        "title": candidate.get("title"),
        "authors": candidate.get("authors"),
        "year": candidate.get("year"),
        "venue": candidate.get("venue"),
        "doi": candidate.get("doi"),
        "url": candidate.get("url"),
        "source_type": candidate.get("source_type"),
        "relevance": judgment.get("relevance", "unknown"),
        "tier": judgment.get("tier", "pending"),
        "priority_score": judgment.get("priority_score"),
        "rank": default_rank,
        "reasons": judgment.get("reasons") or [],
        "evidence": judgment.get("evidence") or [],
        "criteria_matched": judgment.get("criteria_matched") or [],
        "confidence": judgment.get("confidence", "low"),
        "conflict": judgment.get("conflict"),
        "dedupe": scaffold_rec.get("dedupe") if scaffold_rec else None,
        "metadata_completeness": scaffold_rec.get("metadata_completeness") if scaffold_rec else None,
        "lexical": scaffold_rec.get("lexical") if scaffold_rec else None,
    }
    if judgment.get("dedupe_override"):
        rec["dedupe"] = judgment["dedupe_override"]
    return rec


def build_result_document(case_id, status, research_question, results, plan, sources,
                          errors, degradations, pending_human, change_log,
                          extra_top_level=None):
    doc = {
        "case_id": case_id,
        "status": status,
        "research_question": research_question,
        "results": results,
        "evidence": [],
        "status_notes": [],
        "omissions": [],
        "dedupe_groups": [],
        "conflicts": [],
        "sources": sources,
        "uncertainty": [],
        "pending_human": pending_human,
        "errors": errors,
        "degradations": degradations,
        "change_log": change_log,
        "runtime": {"started_at": None, "finished_at": None, "elapsed_seconds": None},
    }
    # Passthrough of the case's own output_contract keys (never renamed/dropped).
    if extra_top_level:
        for key, value in extra_top_level.items():
            if key not in doc:
                doc[key] = value if value is not None else None
    return doc


def finalize(work_dir, output_dir):
    """Step 6: merge plan x scaffold x evidence_ledger, validate, rank, write result.

    Returns (errors, warnings) with errors being contract/consistency violations.
    """
    _ensure_dirs(work_dir, output_dir)
    errors, warnings = [], []

    norm, e1 = load_json(os.path.join(work_dir, "case_normalized.json"))
    plan, e2 = load_json(os.path.join(work_dir, "plan.json"))
    scaffold_obj, e3 = load_json(os.path.join(work_dir, "scaffold.json"))
    ledger, e4 = load_json(os.path.join(work_dir, "evidence_ledger.json"))

    if e1 or not isinstance(norm, dict):
        errors.append("case_normalized.json missing: %s" % e1)
        norm = {}
    if e2 or not isinstance(plan, dict):
        errors.append("plan.json missing: %s" % e2)
        plan = {}
    if e3 or not isinstance(scaffold_obj, dict):
        warnings.append("scaffold.json missing: %s" % e3)
        scaffold_obj = {"records": [], "dedupe_groups": []}
    if e4 or not isinstance(ledger, dict):
        warnings.append("evidence_ledger.json missing or unparseable: %s" % e4)
        ledger = {"records": []}

    candidates = norm.get("candidates") or []
    case_id = norm.get("case_id")
    rq = norm.get("research_question") or ""
    plan_criteria = plan.get("criteria") or []
    criterion_ids = {c.get("id") for c in plan_criteria if isinstance(c, dict)}

    scaffold_by_id = {r.get("record_id"): r for r in scaffold_obj.get("records", []) if isinstance(r, dict)}
    ledger_by_id = {r.get("record_id"): r for r in ledger.get("records", []) if isinstance(r, dict)}

    # 1) Coverage: every candidate must be judged/kept.
    results = []
    seen = set()
    for i, cand in enumerate(candidates):
        rid = cand.get("record_id")
        seen.add(rid)
        judgment = ledger_by_id.get(rid, {})
        scaf = scaffold_by_id.get(rid, {})
        results.append(make_record_result(cand, judgment, scaf, i + 1))

    # 2) Validate judgments.
    for rec in results:
        rid = rec["record_id"]
        rel = rec["relevance"]
        tier = rec["tier"]
        if rel not in RELEVANCE_ENUM:
            errors.append("%s: relevance %r not in %s" % (rid, rel, RELEVANCE_ENUM))
        if tier not in TIER_ENUM:
            errors.append("%s: tier %r not in %s" % (rid, tier, TIER_ENUM))
        if rel == "unknown" and tier != "pending":
            warnings.append("%s: relevance=unknown should be tier=pending (got %s)" % (rid, tier))
        if rec["priority_score"] is not None:
            try:
                ps = float(rec["priority_score"])
                if not (0.0 <= ps <= 1.0):
                    errors.append("%s: priority_score %.3f out of [0,1]" % (rid, ps))
                rec["priority_score"] = round(ps, 3)
            except (TypeError, ValueError):
                errors.append("%s: priority_score not numeric: %r" % (rid, rec["priority_score"]))
        if rec["confidence"] not in CONFIDENCE_ENUM:
            warnings.append("%s: confidence %r not in %s" % (rid, rec["confidence"], CONFIDENCE_ENUM))
        for cm in rec["criteria_matched"]:
            if cm not in criterion_ids:
                warnings.append("%s: criteria_matched %r not found in plan.criteria" % (rid, cm))
        for ev in rec["evidence"]:
            if not isinstance(ev, dict) or not ev.get("locator") or not ev.get("quote"):
                warnings.append("%s: evidence entry missing locator or verbatim quote" % rid)

    # 3) Tier gate consistency (relevance -> tier).
    tier_rank = {"retain": 0, "pending": 1, "exclude": 2}
    for rec in results:
        rel, tier = rec["relevance"], rec["tier"]
        expected = "pending"
        if rel == "direct":
            expected = "retain"
        elif rel == "irrelevant":
            expected = "exclude"
        elif rel == "indirect":
            expected = "pending"
        if tier != expected:
            warnings.append(
                "%s: tier %s inconsistent with relevance %s (plan expects %s)" % (rec["record_id"], tier, rel, expected)
            )

    # 4) Deterministic ordering with explicit tie-break.
    def sort_key(rec):
        return (
            tier_rank.get(rec["tier"], 1),
            -(rec["priority_score"] if rec["priority_score"] is not None else -1.0),
            -(rec.get("metadata_completeness") if rec.get("metadata_completeness") is not None else 0.0),
            -(rec.get("year") if isinstance(rec.get("year"), (int, float)) else -1),
            norm_text(rec.get("title")),
            str(rec.get("record_id")),
        )

    results.sort(key=sort_key)
    for rank, rec in enumerate(results, 1):
        rec["rank"] = rank

    # 5) Flatten evidence for global audit.
    flat_evidence = []
    conflicts = []
    for rec in results:
        for ev in rec.get("evidence") or []:
            if isinstance(ev, dict):
                flat_evidence.append({
                    "record_id": rec["record_id"],
                    "criterion_id": ev.get("criterion_id"),
                    "locator": ev.get("locator"),
                    "quote": ev.get("quote"),
                })
        if rec.get("conflict"):
            conflicts.append({"record_id": rec["record_id"], "conflict": rec["conflict"]})

    # 6) Status: honest and conservative.
    judged = sum(1 for r in results if r["relevance"] != "unknown")
    has_error = bool(errors)
    if not candidates:
        status = "insufficient_evidence"
    elif has_error:
        status = "partial"
    elif judged == len(results) and len(results) > 0:
        status = "ok"
    else:
        status = "partial"
    if ledger.get("status_hint") in STATUS_ENUM:
        # Only allow the Agent to downgrade, never to mask errors.
        order = {"ok": 0, "partial": 1, "insufficient_evidence": 2, "input_error": 3}
        if order.get(ledger["status_hint"], 0) > order.get(status, 1):
            status = ledger["status_hint"]

    # 7) Omissions / uncertainty.
    omissions = []
    if judged < len(results):
        omissions.append({
            "note": "%d/%d records lack semantic judgment (relevance=unknown)" % (len(results) - judged, len(results)),
            "record_ids": [r["record_id"] for r in results if r["relevance"] == "unknown"],
        })
    uncertainty = []
    for rec in results:
        if rec["confidence"] == "low" or rec["relevance"] == "unknown":
            uncertainty.append({
                "record_id": rec["record_id"],
                "note": "low confidence or unknown relevance; needs human review",
            })
        missing = [f for f in ("year", "venue", "doi", "abstract") if rec.get(f) in (None, "", [])]
        if missing:
            uncertainty.append({"record_id": rec["record_id"], "note": "missing metadata: " + ", ".join(missing)})

    pending_human = list(norm.get("pending_human") or [])
    for rec in results:
        if rec["tier"] == "pending" and rec["relevance"] != "unknown":
            pending_human.append({
                "record_id": rec["record_id"],
                "note": "indirect relevance retained in pending tier for human confirmation",
            })

    # Carry forward input-level degradations recorded during discovery.
    carry_degradations = list(norm.get("degradations") or [])

    # 8) Assemble and write.
    change_log = []
    log_obj, _ = load_json(os.path.join(work_dir, "change_log.json"))
    if isinstance(log_obj, list):
        change_log = log_obj
    change_log.append({"ts": now_iso(), "step": "finalize", "action": "merged, validated and ranked results",
                       "details": {"errors": len(errors), "warnings": len(warnings)}})

    extra = {}
    if isinstance(norm, dict) and norm.get("output_contract"):
        extra = _contract_passthrough(norm["output_contract"])

    run_state = {}
    rs_obj, _ = load_json(os.path.join(work_dir, "run_state.json"))
    if isinstance(rs_obj, dict):
        run_state = rs_obj
    started_at = run_state.get("started_at")
    finished_at = now_iso()

    doc = build_result_document(
        case_id=case_id,
        status=status,
        research_question=rq,
        results=results,
        plan=plan,
        sources=norm.get("input_files") or [],
        errors=errors,
        degradations=carry_degradations,
        pending_human=pending_human,
        change_log=change_log,
        extra_top_level=extra,
    )
    doc["evidence"] = flat_evidence
    doc["dedupe_groups"] = scaffold_obj.get("dedupe_groups") or []
    doc["conflicts"] = conflicts
    doc["uncertainty"] = uncertainty
    doc["omissions"] = omissions
    doc["status_notes"] = ledger.get("notes") or []
    doc["runtime"] = {
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_seconds": _elapsed_seconds(started_at, finished_at),
        "tool": "literature-screening-ranking v%s" % VERSION,
    }
    write_json(os.path.join(output_dir, "result.json"), doc)

    selfcheck = {
        "errors": errors,
        "warnings": warnings,
        "status": status,
        "candidate_count": len(results),
        "judged_count": judged,
        "dedupe_group_count": len(doc["dedupe_groups"]),
        "evidence_count": len(flat_evidence),
        "contract_keys_present": sorted(extra.keys()),
    }
    write_json(os.path.join(output_dir, "selfcheck.json"), selfcheck)
    write_json(os.path.join(output_dir, "result_ledger.json"), ledger)  # audit copy of semantic judgments

    _append_log(work_dir, "finalize", "finalized result.json and selfcheck.json")
    _touch_run_state(work_dir, "finalize_at", finished_at)

    return errors, warnings, status


def _contract_passthrough(contract):
    """Echo a case's own output_contract keys so required fields always exist."""
    extra = {}
    if isinstance(contract, dict):
        fields = contract.get("fields") or contract.get("required") or contract.get("keys")
        if isinstance(fields, list):
            for f in fields:
                if isinstance(f, str):
                    extra[f] = None
        for key, value in contract.items():
            if key in ("fields", "required", "keys"):
                continue
            extra[key] = value
    elif isinstance(contract, list):
        for f in contract:
            if isinstance(f, str):
                extra[f] = None
    return extra


def _elapsed_seconds(started_at, finished_at):
    if not started_at:
        return None
    try:
        s = datetime.datetime.fromisoformat(started_at)
        e = datetime.datetime.fromisoformat(finished_at)
        return round((e - s).total_seconds(), 2)
    except ValueError:
        return None


def full_pipeline(input_dir, work_dir, output_dir):
    """Run discover -> scaffold -> finalize (deterministic only; no model).

    Produces a valid partial result where every record stays relevance=unknown.
    The Agent then edits evidence_ledger.json and re-runs finalize.
    """
    discover(input_dir, work_dir, output_dir)
    scaffold(work_dir, output_dir)
    errors, warnings, status = finalize(work_dir, output_dir)
    return {"status": status, "errors": errors, "warnings": warnings}


if __name__ == "__main__":
    # Minimal standalone smoke: print version; real entry point is main.py.
    print("core_impl.py v%s (import as a module or drive via scripts/main.py)" % VERSION)
    sys.exit(0)
