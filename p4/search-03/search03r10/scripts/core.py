"""core.py — deterministic evidence-ledger pipeline for literature screening.

Architecture (differs from a single "edit result.json in place" flow):

  discover   -> case_normalized.json + case_meta.json + decisions.jsonl (empty)
                + a legal partial result.json skeleton (written first, < 3 min).
  signal     -> candidates.json (metadata + deterministic lexical signals)
                + scaffold.json (dedupe groups / conflicts; lexical prior only).
  [Agent]    -> appends one schema-valid JSON object per candidate to
                decisions.jsonl (append-only judgment ledger; never edits
                result.json directly).
  merge      -> validates every ledger line, reconciles duplicates/missing
                records, derives an explainable priority_score from documented
                components, sorts deterministically, and only then assembles
                result.json (validate-then-merge ordering).
  finalize   -> re-validates the assembled artifact and writes selfcheck.json.

Pure Python standard library (Python 3). All steps are idempotent under the
same input directory; the tie-break key is fixed so repeated runs match.
"""

import json
import os
import re
import time
import unicodedata
from datetime import datetime, timezone

SCORE_BASE = {"direct": 0.70, "indirect": 0.40, "irrelevant": 0.05, "unknown": 0.10}
EVIDENCE_BONUS = 0.15
RECENCY_RECENT_YEAR = 2018
RECENCY_BONUS = 0.10
RECENCY_OLD_BONUS = 0.04

RELEVANCE_ENUM = ("direct", "indirect", "irrelevant", "unknown")
TIER_ENUM = ("retain", "pending", "exclude")
CONFIDENCE_ENUM = ("high", "medium", "low", "unknown")
STATUS_ENUM = ("ok", "partial", "insufficient_evidence", "input_error")
TIER_RANK = {"retain": 0, "pending": 1, "exclude": 2}

_CANDIDATE_KEYS = (
    "papers", "candidates", "records", "documents", "articles", "items",
    "hits", "literature", "results",
)
_QUESTION_KEYS = (
    "research_question", "question", "query", "topic", "task", "objective",
    "goal", "research_topic",
)
_INCLUSION_KEYS = ("inclusion_criteria", "inclusion", "include_criteria", "include")
_EXCLUSION_KEYS = ("exclusion_criteria", "exclusion", "exclude_criteria", "exclude")
_CRITERIA_KEYS = ("criteria", "screening_criteria", "eligibility_criteria", "eligibility")
_CONTRACT_KEYS = (
    "output_contract", "output_schema", "expected_fields", "output_fields",
    "result_schema", "result_format",
)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    print("[%s] %s" % (now_iso(), msg))


def _read_text(path):
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise IOError("cannot decode %s" % path)


def read_json_tolerant(path):
    """Return (obj, ok, error). Never raises for parse/decode problems."""
    if not os.path.isfile(path):
        return None, False, "missing file"
    try:
        text = _read_text(path)
        return json.loads(text), True, None
    except Exception as exc:  # noqa: BLE001
        return None, False, "parse error: %s" % exc


def write_json(path, obj):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load(work_dir, name):
    obj, ok, err = read_json_tolerant(os.path.join(work_dir, name))
    if not ok:
        raise RuntimeError("cannot load %s: %s" % (name, err))
    return obj


def append_run_log(work_dir, step, action, detail=""):
    p = os.path.join(work_dir, "run_log.json")
    rows = []
    if os.path.isfile(p):
        try:
            rows = json.loads(_read_text(p))
            if not isinstance(rows, list):
                rows = []
        except Exception:  # noqa: BLE001
            rows = []
    rows.append({"ts": now_iso(), "step": step, "action": action, "detail": detail})
    write_json(p, rows)
    return rows


def _first(obj, keys):
    if not isinstance(obj, dict):
        return None
    for k in keys:
        if k in obj and obj[k] not in (None, "", [], {}):
            return obj[k]
    return None


def _coerce_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, (str, int, float, bool, dict)):
        return [v]
    try:
        return list(v)
    except TypeError:
        return []


def _coerce_str_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v).strip()
    return [s] if s else []


def _norm_title(s):
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s)).lower()
    return re.sub(r"[^a-z0-9]+", "", s)


def _norm_doi(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", str(s)).strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)
    return s.strip().lower()


def _coerce_year(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    m = re.search(r"(18|19|20)\d{2}", str(v))
    if m:
        try:
            return int(m.group(0))
        except ValueError:
            return None
    return None


def _authors(raw):
    if raw is None:
        return None
    if isinstance(raw, str):
        out = [a.strip() for a in raw.split(";") if a.strip()]
        return out or None
    if isinstance(raw, list):
        out = [str(a).strip() for a in raw if str(a).strip()]
        return out or None
    return None


def normalize_record(raw, idx):
    rec = {}
    if isinstance(raw, dict):
        rid = raw.get("record_id") or raw.get("id") or raw.get("uid") or \
            raw.get("paper_id") or raw.get("pmid") or raw.get("source_id")
        rec["record_id"] = str(rid) if rid not in (None, "") else ("rec_%04d" % idx)
        rec["title"] = raw.get("title") or raw.get("name")
        rec["authors"] = _authors(raw.get("authors") or raw.get("author") or raw.get("author_list"))
        rec["year"] = _coerce_year(raw.get("year") or raw.get("pub_year") or
                                   raw.get("publication_year") or raw.get("date"))
        rec["venue"] = raw.get("venue") or raw.get("journal") or raw.get("source") or \
            raw.get("container") or raw.get("publication")
        rec["doi"] = raw.get("doi") or raw.get("DOI")
        rec["url"] = raw.get("url") or raw.get("link") or raw.get("pdf_url")
        rec["source_type"] = raw.get("source_type") or raw.get("type") or raw.get("document_type")
        rec["abstract"] = raw.get("abstract") or raw.get("summary")
    else:
        rec["record_id"] = "rec_%04d" % idx
        rec["title"] = str(raw)
        rec["authors"] = None
        rec["year"] = None
        rec["venue"] = None
        rec["doi"] = None
        rec["url"] = None
        rec["source_type"] = None
        rec["abstract"] = None
    return rec


def discover(input_dir, work_dir, output_dir):
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    t0 = time.time()
    errors, degradations, sources = [], [], []

    case_path = os.path.join(input_dir, "case.json")
    task_path = os.path.join(input_dir, "task.md")
    case_raw, ok, err = read_json_tolerant(case_path)
    if not ok:
        errors.append({"at": "input/case.json", "detail": err})
    if os.path.isfile(case_path):
        sources.append({"path": "input/case.json", "kind": "case", "role": "primary_input"})
    if os.path.isfile(task_path):
        sources.append({"path": "input/task.md", "kind": "task", "role": "instructions"})

    mat_dir = os.path.join(input_dir, "materials")
    for base in (input_dir, mat_dir):
        if not os.path.isdir(base):
            continue
        for fn in sorted(os.listdir(base)):
            low = fn.lower()
            if not low.endswith((".pdf", ".md", ".txt", ".json", ".jsonl")):
                continue
            full = os.path.join(base, fn)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            if size > 2 * 1024 * 1024:
                degradations.append({"at": fn, "detail": "file larger than 2MB, skipped", "action": "skip"})
                continue
            rel = os.path.relpath(full, input_dir).replace("\\", "/")
            sources.append({"path": rel, "kind": "material", "role": "evidence_source"})

    case_dict = case_raw if isinstance(case_raw, dict) else {}
    question = _first(case_dict, _QUESTION_KEYS)
    if question is None:
        question = case_dict.get("title")
    if question is not None:
        question = str(question)

    criteria = {}
    incl = _coerce_list(_first(case_dict, _INCLUSION_KEYS))
    excl = _coerce_list(_first(case_dict, _EXCLUSION_KEYS))
    if not incl and not excl:
        c = _first(case_dict, _CRITERIA_KEYS)
        if isinstance(c, dict):
            incl = _coerce_list(c.get("inclusion") or c.get("include"))
            excl = _coerce_list(c.get("exclusion") or c.get("exclude"))
    criteria = {"inclusion": [str(x) for x in incl], "exclusion": [str(x) for x in excl]}

    pool = _first(case_dict, _CANDIDATE_KEYS)
    if isinstance(pool, dict):
        inner = None
        for k in _CANDIDATE_KEYS:
            if k in pool:
                inner = pool[k]
                break
        if inner is None:
            inner = [pool]
        pool = inner
    raw_cands = _coerce_list(pool)
    candidates = []
    for idx, raw in enumerate(raw_cands):
        rec = normalize_record(raw, idx)
        rec.pop("_raw", None)
        candidates.append(rec)
    if not candidates and isinstance(case_dict.get("candidates"), dict):
        for i, (k, v) in enumerate(case_dict.get("candidates").items()):
            rec = normalize_record(v, i)
            if rec["record_id"].startswith("rec_"):
                rec["record_id"] = str(k)
            candidates.append(rec)

    case_id = str(case_dict.get("case_id") or case_dict.get("id") or
                  case_dict.get("challenge_id") or "unknown_case")
    if case_id in ("", "None"):
        case_id = "unknown_case"

    contract = {}
    for k in _CONTRACT_KEYS:
        if k in case_dict and case_dict[k] not in (None, "", []):
            contract[k] = case_dict[k]

    omissions = []
    status = "partial"
    status_notes = ""
    if not candidates:
        status = "insufficient_evidence"
        omissions.append("candidate set not found in case.json under any of: %s" % ", ".join(_CANDIDATE_KEYS))
        status_notes = "no candidates discovered; check /input/case.json structure or task.md."

    # Seed per-record skeleton (legal partial artifact, written immediately).
    results = []
    for i, rec in enumerate(candidates):
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
            "rank": i + 1,
            "reasons": [],
            "criteria_matched": [],
            "evidence": [],
            "confidence": "unknown",
            "score_components": {"base": SCORE_BASE["unknown"], "evidence_bonus": 0.0, "recency_bonus": 0.0},
            "conflict": None,
            "dedupe": None,
        })

    norm = {
        "case_id": case_id,
        "research_question": question,
        "criteria": criteria,
        "output_contract": contract,
        "candidates": candidates,
        "sources": sources,
        "errors": errors,
        "degradations": degradations,
        "task_md_seen": os.path.isfile(task_path),
        "generated_at": now_iso(),
        "_started_epoch": t0,
    }
    write_json(os.path.join(work_dir, "case_normalized.json"), norm)
    write_json(os.path.join(work_dir, "case_meta.json"), {
        "case_id": case_id,
        "status_override": None,
        "status_notes": "",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "omissions": [],
    })
    # Empty append-only judgment ledger (Agent appends one JSON object per line).
    with open(os.path.join(work_dir, "decisions.jsonl"), "w", encoding="utf-8") as fh:
        fh.write("")

    run_rows = append_run_log(work_dir, "discover", "skeleton",
                              "candidates=%d status=%s" % (len(candidates), status))

    skeleton = {
        "case_id": case_id,
        "status": status,
        "research_question": question,
        "criteria": criteria,
        "results": results,
        "dedupe_groups": [],
        "conflicts": [],
        "sources": sources,
        "uncertainty": [],
        "omissions": omissions,
        "pending_human": [],
        "errors": errors,
        "degradations": degradations,
        "change_log": run_rows,
        "status_notes": status_notes,
        "runtime": {"started_at": norm["generated_at"], "finished_at": None, "seconds": None,
                    "budget_note": "authoritative budgets recorded by platform; values here are script-observed"},
    }
    for k, v in contract.items():
        skeleton[k] = v
    write_json(os.path.join(output_dir, "result.json"), skeleton)
    log("discover: case_id=%s candidates=%d status=%s" % (case_id, len(candidates), status))
    return skeleton


def _first_author_key(rec):
    auths = rec.get("authors")
    if not auths:
        return None
    a0 = str(auths[0]).strip()
    if not a0:
        return None
    parts = a0.split()
    return (parts[-1] if parts else a0).lower()


def signal(work_dir, output_dir):
    norm = _load(work_dir, "case_normalized.json")
    candidates = norm.get("candidates", [])
    question = norm.get("research_question") or ""
    q_tokens = set(re.findall(r"[a-z0-9]+", question.lower()))
    q_tokens = {t for t in q_tokens if len(t) >= 3}

    signals = []
    for rec in candidates:
        title = rec.get("title") or ""
        title_norm = _norm_title(title)
        doi_norm = _norm_doi(rec.get("doi") or "")
        abs_text = rec.get("abstract") or ""
        hits = [t for t in sorted(q_tokens) if t in title_norm]
        signals.append({
            "record_id": rec["record_id"],
            "title": rec["title"],
            "authors": rec["authors"],
            "year": rec["year"],
            "venue": rec["venue"],
            "doi": rec["doi"],
            "url": rec["url"],
            "source_type": rec["source_type"],
            "abstract_present": bool(abs_text.strip()),
            "title_norm": title_norm,
            "doi_norm": doi_norm,
            "first_author": _first_author_key(rec),
            "keyword_hits": hits,
            "lexical_overlap": round(len(hits) / len(q_tokens), 3) if q_tokens else 0.0,
        })
    write_json(os.path.join(work_dir, "candidates.json"), signals)

    dedupe_groups = []
    used = set()
    gid = 0
    by_doi = {}
    for s in signals:
        if s["doi_norm"]:
            by_doi.setdefault(s["doi_norm"], []).append(s["record_id"])
    for doi, ids in by_doi.items():
        if len(ids) > 1:
            gid += 1
            dedupe_groups.append({
                "group_id": "dgrp_%03d" % gid, "kind": "same_doi",
                "primary_record_id": ids[0], "members": sorted(set(ids)),
                "decision": "同一 DOI，判为同一作品重复导出；保留 primary，其余标注 duplicate。",
            })
            used.update(ids)
    by_title = {}
    for s in signals:
        if s["title_norm"] and s["record_id"] not in used:
            by_title.setdefault(s["title_norm"], []).append(s["record_id"])
    for title, ids in by_title.items():
        if len(ids) > 1:
            gid += 1
            dedupe_groups.append({
                "group_id": "dgrp_%03d" % gid, "kind": "title_match",
                "primary_record_id": ids[0], "members": sorted(set(ids)),
                "decision": "标题归一化后相同，疑为预印本与正式版或同一研究多报道；保留 primary，其余 duplicate，版本归属待证据确认。",
            })
            used.update(ids)

    conflicts = []
    by_key = {}
    for s in signals:
        if s["first_author"] and s["year"] is not None:
            by_key.setdefault((s["first_author"], s["year"]), []).append(s["record_id"])
    seen_pairs = set()
    for key, ids in by_key.items():
        if len(ids) < 2:
            continue
        ids = sorted(set(ids))
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                pair = (ids[a], ids[b])
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                sa = next(s for s in signals if s["record_id"] == ids[a])
                sb = next(s for s in signals if s["record_id"] == ids[b])
                if sa["title_norm"] == sb["title_norm"] or (sa["doi_norm"] and sa["doi_norm"] == sb["doi_norm"]):
                    continue
                conflicts.append({
                    "type": "possible_same_study", "record_ids": list(pair),
                    "note": "同一第一作者同年多篇，标题与 DOI 均不同，可能为同一研究多报道；弱信号不自动合并。",
                    "resolution": "pending_human", "status": "pending_human",
                })

    scaffold = {
        "q_tokens": sorted(q_tokens),
        "dedupe_groups": dedupe_groups,
        "conflicts": conflicts,
        "lexical_prior_note": "lexical_overlap 仅作词面先验，供 Agent 参考；不得直接作为最终 priority_score。",
    }
    write_json(os.path.join(work_dir, "scaffold.json"), scaffold)
    append_run_log(work_dir, "signal", "scaffold",
                   "dedupe_groups=%d conflicts=%d" % (len(dedupe_groups), len(conflicts)))
    log("signal: dedupe_groups=%d conflicts=%d" % (len(dedupe_groups), len(conflicts)))
    return scaffold


def read_decisions(work_dir):
    p = os.path.join(work_dir, "decisions.jsonl")
    rows, errors = [], []
    if not os.path.isfile(p):
        return rows, errors
    text = _read_text(p)
    for ln, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception as exc:  # noqa: BLE001
            errors.append({"at": "decisions.jsonl#%d" % ln, "detail": "bad JSON: %s" % exc})
            continue
        rows.append(obj)
    return rows, errors


def _resolve_annotations(rid, scaffold):
    dedupe = None
    conflict = None
    if scaffold:
        for g in scaffold.get("dedupe_groups", []):
            if rid in g.get("members", []):
                role = "primary" if g.get("primary_record_id") == rid else "duplicate"
                dedupe = {"group_id": g.get("group_id"), "kind": g.get("kind"), "role": role,
                          "canonical_record_id": g.get("primary_record_id")}
                break
        for c in scaffold.get("conflicts", []):
            if rid in c.get("record_ids", []):
                conflict = {
                    "type": c.get("type"),
                    "with_record_id": [x for x in c.get("record_ids", []) if x != rid],
                    "resolution": c.get("resolution"),
                    "status": c.get("status", "pending_human"),
                }
    return dedupe, conflict


def merge(work_dir, output_dir):
    norm = _load(work_dir, "case_normalized.json")
    candidates = _load(work_dir, "candidates.json")
    scaffold, _, _ = read_json_tolerant(os.path.join(work_dir, "scaffold.json"))
    meta = _load(work_dir, "case_meta.json")
    decisions, d_errors = read_decisions(work_dir)

    errors = list(norm.get("errors", [])) + d_errors
    warnings = []
    known_ids = {c["record_id"] for c in candidates}
    decision_by = {}
    dup_seen = {}
    for ln, d in enumerate(decisions, 1):
        if not isinstance(d, dict):
            errors.append({"at": "decisions.jsonl#%d" % ln, "detail": "not an object"})
            continue
        rid = str(d.get("record_id", ""))
        if not rid or rid not in known_ids:
            errors.append({"at": "decisions.jsonl#%d" % ln,
                           "detail": "record_id %r not in candidate set" % rid})
            continue
        if rid in decision_by:
            dup_seen[rid] = dup_seen.get(rid, 1) + 1
            warnings.append("record_id %s: multiple ledger lines; keeping last (line %d)" % (rid, ln))
        decision_by[rid] = d

    results = []
    uncertainties = []
    for rec in candidates:
        rid = rec["record_id"]
        d = decision_by.get(rid)
        relevance = d.get("relevance") if d else "unknown"
        tier = d.get("tier") if d else "pending"
        confidence = d.get("confidence") if d else "unknown"
        if relevance not in RELEVANCE_ENUM:
            warnings.append("%s: relevance=%r invalid, coerced to unknown" % (rid, relevance))
            relevance = "unknown"
        if tier not in TIER_ENUM:
            warnings.append("%s: tier=%r invalid, coerced to pending" % (rid, tier))
            tier = "pending"
        if confidence not in CONFIDENCE_ENUM:
            confidence = "unknown"
        if tier == "exclude" and relevance != "irrelevant":
            warnings.append("%s: tier=exclude but relevance=%r (expect irrelevant)" % (rid, relevance))
        if tier == "retain" and relevance in ("irrelevant", "unknown"):
            warnings.append("%s: tier=retain but relevance=%r" % (rid, relevance))

        reasons = _coerce_str_list(d.get("reasons")) if d else []
        criteria_matched = _coerce_str_list(d.get("criteria_matched")) if d else []
        ev_clean = []
        ev_raw = d.get("evidence") if d else []
        if isinstance(ev_raw, list):
            for e in ev_raw:
                if isinstance(e, dict) and str(e.get("locator", "")).strip() and str(e.get("quote", "")).strip():
                    ev_clean.append({"locator": str(e["locator"]).strip(), "quote": str(e["quote"]).strip()})
                else:
                    warnings.append("%s: evidence entry lacks locator or quote, dropped" % rid)

        base = SCORE_BASE.get(relevance, SCORE_BASE["unknown"])
        ev_bonus = EVIDENCE_BONUS if ev_clean else 0.0
        year = rec.get("year")
        if relevance == "irrelevant":
            recency = 0.0
        elif year is not None:
            recency = RECENCY_BONUS if year >= RECENCY_RECENT_YEAR else RECENCY_OLD_BONUS
        else:
            recency = 0.0
        score = round(min(base + ev_bonus + recency, 1.0), 3)

        if relevance != "irrelevant" and tier in ("retain", "pending") and not ev_clean:
            uncertainties.append({"record_id": rid, "note": "relevance=%s but no verbatim evidence recorded" % relevance})
        if year is None:
            uncertainties.append({"record_id": rid, "note": "year missing"})
        if not rec.get("doi") and relevance in ("direct", "indirect"):
            uncertainties.append({"record_id": rid, "note": "DOI missing for retained-relevant record"})

        dedupe, conflict = _resolve_annotations(rid, scaffold)
        results.append({
            "record_id": rid, "title": rec["title"], "authors": rec["authors"],
            "year": rec["year"], "venue": rec["venue"], "doi": rec["doi"], "url": rec["url"],
            "source_type": rec["source_type"], "relevance": relevance, "tier": tier,
            "priority_score": score, "reasons": reasons, "criteria_matched": criteria_matched,
            "evidence": ev_clean, "confidence": confidence,
            "score_components": {"base": base, "evidence_bonus": ev_bonus, "recency_bonus": recency},
            "conflict": conflict, "dedupe": dedupe,
        })

    def sort_key(r):
        return (TIER_RANK[r["tier"]], -r["priority_score"], -len(r["evidence"]),
                -(r["year"] or 0), _norm_title(r["title"]), r["record_id"])

    results.sort(key=sort_key)
    for i, r in enumerate(results, 1):
        r["rank"] = i

    judged = len(decision_by)
    unjudged = [c["record_id"] for c in candidates if c["record_id"] not in decision_by]
    omissions = list(meta.get("omissions", []))
    if unjudged:
        omissions.append("no per-record decision recorded for: %s" % ", ".join(unjudged))

    if not candidates:
        status = "insufficient_evidence"
    elif unjudged or errors:
        status = "partial"
    else:
        status = "ok"
    if meta.get("status_override") in STATUS_ENUM:
        status = meta["status_override"]

    sources = list(norm.get("sources", []))
    seen_src = {(s.get("path"), s.get("kind")) for s in sources}
    for s in meta.get("sources", []):
        if (s.get("path"), s.get("kind")) not in seen_src:
            sources.append(s)
            seen_src.add((s.get("path"), s.get("kind")))

    uncertainty = list(meta.get("uncertainty", [])) + uncertainties
    pending_human = list(meta.get("pending_human", []))
    change_log = append_run_log(work_dir, "merge", "assemble",
                                "judged=%d/%d status=%s" % (judged, len(candidates), status))

    started = norm.get("generated_at")
    seconds = None
    if norm.get("_started_epoch") is not None:
        seconds = round(time.time() - norm["_started_epoch"], 1)

    out = {
        "case_id": norm["case_id"], "status": status,
        "research_question": norm.get("research_question"),
        "criteria": norm.get("criteria", {"inclusion": [], "exclusion": []}),
        "results": results,
        "dedupe_groups": scaffold.get("dedupe_groups", []) if scaffold else [],
        "conflicts": scaffold.get("conflicts", []) if scaffold else [],
        "sources": sources,
        "uncertainty": uncertainty,
        "omissions": omissions,
        "pending_human": pending_human,
        "errors": errors,
        "degradations": norm.get("degradations", []),
        "change_log": change_log,
        "status_notes": meta.get("status_notes", ""),
        "runtime": {"started_at": started, "finished_at": now_iso(), "seconds": seconds,
                    "budget_note": "authoritative budgets recorded by platform; values here are script-observed"},
    }
    for k, v in (norm.get("output_contract") or {}).items():
        out[k] = v
    write_json(os.path.join(output_dir, "result.json"), out)
    log("merge: status=%s results=%d errors=%d warnings=%d" % (status, len(results), len(errors), len(warnings)))
    return out


def finalize(work_dir, output_dir):
    result, ok, err = read_json_tolerant(os.path.join(output_dir, "result.json"))
    if not ok:
        write_json(os.path.join(output_dir, "selfcheck.json"),
                   {"errors": [err], "warnings": [], "checks": [], "pass": False})
        log("finalize: FATAL result.json unreadable: %s" % err)
        return {"errors": [err], "warnings": [], "checks": [], "pass": False}
    norm = _load(work_dir, "case_normalized.json")
    expected = {c["record_id"] for c in norm.get("candidates", [])}

    errors, warnings, checks = [], [], []
    def check(cid, name, ok, detail=""):
        checks.append({"id": cid, "name": name, "ok": bool(ok), "detail": detail})
        if not ok:
            errors.append({"at": "result.json", "detail": "%s: %s" % (name, detail)})

    check("C1", "case_id present", bool(result.get("case_id")))
    check("C2", "status valid", result.get("status") in STATUS_ENUM,
          "status=%r" % result.get("status"))

    res = result.get("results")
    check("A1", "results is list", isinstance(res, list))
    got = set()
    if isinstance(res, list):
        got = {r.get("record_id") for r in res if isinstance(r, dict)}
    check("A2", "coverage complete", expected and expected.issubset(got),
          "missing=%s" % sorted(expected - got))
    check("A3", "no unknown records", got.issubset(expected) if expected else True,
          "extra=%s" % sorted(got - expected))

    req_fields = ("record_id", "title", "authors", "year", "venue", "doi", "url",
                  "source_type", "relevance", "tier", "priority_score", "rank",
                  "reasons", "criteria_matched", "evidence", "confidence")
    if isinstance(res, list):
        for r in res:
            if not isinstance(r, dict):
                check("A4", "record is object", False, "non-object record")
                continue
            miss = [f for f in req_fields if f not in r]
            if miss:
                check("A4", "record fields complete", False,
                      "%s missing %s" % (r.get("record_id"), miss))
            if r.get("relevance") not in RELEVANCE_ENUM:
                check("A5", "relevance enum", False, "%s: %r" % (r.get("record_id"), r.get("relevance")))
            if r.get("tier") not in TIER_ENUM:
                check("A6", "tier enum", False, "%s: %r" % (r.get("record_id"), r.get("tier")))
            if not isinstance(r.get("priority_score"), (int, float)) or not (0.0 <= float(r["priority_score"]) <= 1.0):
                check("A7", "priority_score in [0,1]", False, "%s: %r" % (r.get("record_id"), r.get("priority_score")))
            ev = r.get("evidence")
            if r.get("tier") in ("retain", "pending") and r.get("relevance") != "unknown":
                if not (isinstance(ev, list) and ev and all(
                        isinstance(e, dict) and str(e.get("locator", "")).strip() and str(e.get("quote", "")).strip()
                        for e in ev)):
                    check("A8", "evidence for judged records", False,
                          "%s: missing verbatim locator/quote" % r.get("record_id"))
    else:
        check("A4", "record fields complete", False, "results not a list")

    if expected and not got.issubset(expected):
        check("A2b", "coverage direction", False)
    # Honest status cross-check.
    if result.get("status") == "ok" and (errors or result.get("errors")):
        check("D4", "status honest with errors", False, "status=ok but blocking errors present")

    # Top-level contract keys.
    for k in ("sources", "uncertainty", "pending_human", "change_log"):
        check("B%s" % k, "%s present" % k, isinstance(result.get(k), list), "missing %s" % k)

    selfcheck = {
        "errors": errors, "warnings": warnings, "checks": checks,
        "pass": len(errors) == 0,
        "generated_at": now_iso(),
        "note": "errors list below is about the assembled result.json contract, not fabricated scores.",
    }
    write_json(os.path.join(work_dir, "selfcheck.json"), selfcheck)
    write_json(os.path.join(output_dir, "selfcheck.json"), selfcheck)
    append_run_log(work_dir, "finalize", "selfcheck", "errors=%d" % len(errors))
    log("finalize: errors=%d warnings=%d pass=%s" % (len(errors), len(warnings), selfcheck["pass"]))
    return selfcheck
