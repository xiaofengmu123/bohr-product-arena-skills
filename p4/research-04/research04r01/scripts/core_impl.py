# -*- coding: utf-8 -*-
"""core_impl.py — deterministic scaffolding for research-frontier evidence analysis.

Standard library only. Everything written is UTF-8. This module performs the
mechanical part of the workflow (discovery, extraction, deterministic counting,
contract echo, patch merging and self-check); the semantic verification of
candidates into conclusions is done by the agent following SKILL.md.

Design principle (evidence-traceability variant): every conclusion lives in a
``claims[]`` list and carries ``evidence_ids``; every evidence item carries a
reverse pointer ``used_by_claims`` plus ``locator``, verbatim ``quote``,
``quote_hash``, ``span``, ``source_kind`` and ``retrieval_note``. The index is
bidirectional so a reviewer can go claim -> evidence -> exact source line, or
source line -> evidence -> claims, without reopening the whole corpus.
"""

import os
import re
import sys
import json
import csv
import shutil
import hashlib
import argparse
import subprocess
import datetime
from collections import Counter, OrderedDict

ISO_TS = "%Y-%m-%dT%H:%M:%S%z"
MAX_FILE_BYTES = 2 * 1024 * 1024  # per-file skip threshold
MAX_FILES = 200                   # per-run scan cap (record degradation)
TEXT_EXT = {".txt", ".md", ".markdown", ".json", ".csv", ".xml", ".html",
            ".htm", ".tex", ".bib", ".ris", ".yaml", ".yml"}
PDF_EXT = {".pdf"}
# Task-control files are not literature material and must never enter the corpus.
CONTROL_FILES = {"case.json", "task.md", "input.json", "case.md", "task.json"}

STATUS_ENUM = {"ok", "partial", "insufficient_evidence", "input_error"}
SUPPORT_ENUM = {"reported", "not_reported", "inferred"}
CONF_ENUM = {"high", "medium", "low"}
EVID_STATUS_ENUM = {"reported", "not_reported", "source_conflict", "inferred"}
EVID_KIND_ENUM = {"fact", "inference", "unknown", "conflict"}
EVENT_TYPE_ENUM = {"emergence", "merge", "split", "decline", "terminology_drift"}
GAP_TYPE_ENUM = {"open_question", "method_failure", "unresolved_conflict",
                 "coverage_absence"}
WORK_KIND_ENUM = {"origin", "method", "milestone", "benchmark"}

# Sentence-level cue words used only to raise *candidates* for the agent to verify.
GAP_CUES = ["future work", "further work", "open question", "open problem",
            "remains unclear", "remains unknown", "not yet", "unknown",
            "limitation", "lacks", "under-explored", "underexplored", "有待",
            "尚未", "仍不清楚", "仍未知", "未解决", "尚缺", "不足", "遗留", "空白"]
CONFLICT_CUES = ["however", "in contrast", "versus", "discrepan",
                 "inconsisten", "contradict", "disagree", "on the other hand",
                 "相反", "不一致", "矛盾", "争议", "分歧", "冲突", "并不一致"]

STOPWORDS = {"the", "and", "for", "with", "that", "this", "from", "were",
             "are", "was", "have", "has", "been", "which", "their", "these",
             "those", "into", "using", "based", "between", "through", "study",
             "results", "method", "methods", "analysis", "data", "show",
             "shown", "fig", "figure", "table", "paper", "research"}

FIXED_KEYS = [
    "case_id", "status", "research_question", "protocol", "time_trend",
    "topic_evolution", "representative_works", "research_gaps", "evidence_map",
    "conflicts", "sources", "uncertainty", "pending_human", "change_log",
    "limitations", "errors", "degradations", "runtime", "artifacts",
]


# --------------------------------------------------------------------------- #
# small utilities
# --------------------------------------------------------------------------- #
def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")


def norm_text(s):
    """Whitespace-collapsing normalisation used for quote matching."""
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s)).strip()


def quote_hash(q):
    return hashlib.sha1(norm_text(q).encode("utf-8")).hexdigest()[:12]


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """Read text with BOM/bad-encoding fallback. Returns (text, encoding, error)."""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return "", None, "stat failed: %s" % e
    if size > max_bytes:
        return "", None, "skipped: size %d > %d" % (size, max_bytes)
    data = None
    err = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                data = fh.read()
            return data, enc, None
        except (UnicodeDecodeError, UnicodeError) as e:
            err = "%s: %s" % (enc, e)
        except OSError as e:
            return "", None, "read failed: %s" % e
    return "", None, "decode failed (%s)" % (err or "no encoding")


def safe_json_load(path):
    """Return (obj, error). Never raises."""
    text, enc, err = read_text(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, "bad JSON: %s" % e


def write_json(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


# --------------------------------------------------------------------------- #
# discovery & preparation
# --------------------------------------------------------------------------- #
def discover_input(input_dir):
    """Walk input_dir; return (files, too_many_flag, skipped_big)."""
    files = []
    skipped_big = []
    for root, dirs, names in os.walk(input_dir):
        dirs.sort()
        for n in sorted(names):
            p = os.path.join(root, n)
            if os.path.isfile(p):
                try:
                    size = os.path.getsize(p)
                except OSError:
                    continue
                if size > MAX_FILE_BYTES:
                    skipped_big.append({"path": p, "size": size})
                    continue
                files.append({"path": p, "size": size,
                              "rel": os.path.relpath(p, input_dir)})
    too_many = len(files) > MAX_FILES
    if too_many:
        files = files[:MAX_FILES]
    return files, too_many, skipped_big


def read_task_md(input_dir):
    for cand in ("task.md", "TASK.md", "Task.md"):
        p = os.path.join(input_dir, cand)
        if os.path.isfile(p):
            text, enc, err = read_text(p)
            return {"path": p, "text": text or "", "encoding": enc, "error": err}
    return None


def read_case(input_dir):
    for cand in ("case.json", "case.JSON", "input.json"):
        p = os.path.join(input_dir, cand)
        if os.path.isfile(p):
            obj, err = safe_json_load(p)
            return {"path": p, "obj": obj, "error": err}
    return None


def extract_case_id(case, task_text):
    if isinstance(case, dict):
        for k in ("case_id", "id", "challenge_id", "task_id"):
            if case.get(k):
                return str(case[k])
    if task_text:
        m = re.search(r"challenge[-_]?id[:：\s]+([A-Za-z0-9\-_]+)", task_text, re.I)
        if m:
            return m.group(1)
    return "unknown"


def get_output_contract(case):
    """Return the output_contract dict if present, else {}."""
    if isinstance(case, dict):
        c = case.get("output_contract")
        if isinstance(c, dict):
            return c
    return {}


# --------------------------------------------------------------------------- #
# material normalisation
# --------------------------------------------------------------------------- #
def _pdf_to_text(pdf_path, out_path):
    """Best-effort PDF text extraction; returns (ok, note). Never fabricates."""
    cmd = None
    if shutil.which("pdftotext"):
        cmd = ["pdftotext", "-layout", pdf_path, out_path]
    elif os.environ.get("PDF_TEXT_TOOL"):
        tool = os.environ["PDF_TEXT_TOOL"]
        cmd = [tool, pdf_path, out_path]
    if not cmd:
        return False, "no pdf text tool available"
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=120)
    except Exception as e:  # noqa: BLE001
        return False, "pdf tool failed: %s" % e
    if r.returncode != 0 or not os.path.exists(out_path):
        return False, "pdf tool exit %d" % r.returncode
    if os.path.getsize(out_path) == 0:
        return False, "pdf tool produced empty text"
    return True, "pdftotext"


def iter_metadata_records(text, ext):
    """If the material is JSON/CSV metadata, yield record dicts (best effort)."""
    if ext == ".json":
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return
        if isinstance(obj, list):
            for it in obj:
                if isinstance(it, dict):
                    yield it
        elif isinstance(obj, dict):
            for key in ("records", "documents", "papers", "items", "results", "data"):
                v = obj.get(key)
                if isinstance(v, list):
                    for it in v:
                        if isinstance(it, dict):
                            yield it
                    return
            yield obj
    elif ext == ".csv":
        try:
            reader = csv.DictReader(text.splitlines())
            for row in reader:
                yield row
        except Exception:  # noqa: BLE001
            return


def _first_value(rec, keys):
    for k in keys:
        v = rec.get(k)
        if v is not None and str(v).strip() != "":
            return v
    return None


def _extract_year(rec, text):
    """Year precedence: metadata -> bracket near title -> first 19xx/20xx. """
    val = _first_value(rec, ("year", "Year", "publication_year", "published_year",
                             "date", "pub_date", "publication_date"))
    if val is not None:
        s = str(val)
        m = re.search(r"(19|20)\d{2}", s)
        if m:
            return int(m.group(0)), "metadata(%s)" % s[:40]
    m = re.search(r"[\[(（](19|20)\d{2}[\]\)）]", text)
    if m:
        return int(m.group(1)), "bracket-near-title"
    m = re.search(r"\b(19|20)\d{2}\b", text[:2000])
    if m:
        return int(m.group(0)), "text-head"
    return None, None


def _extract_citations(rec):
    val = _first_value(rec, ("citations", "citation_count", "cited_by",
                             "times_cited", "citedByCount", "cited_by_count"))
    if val is None:
        return None
    s = str(val).replace(",", "").strip()
    m = re.search(r"\d+", s)
    if m:
        return int(m.group(0))
    return None


def _split_sentences(text):
    """Naive but deterministic sentence splitter returning (text, line_no)."""
    out = []
    for i, raw in enumerate(text.splitlines(), 1):
        # split long lines on sentence-ending punctuation as well
        for part in re.split(r"(?<=[。！？!?.;；])\s+", raw):
            s = part.strip()
            if s:
                out.append((s, i))
    return out


def extract_term_candidates(text, meta_keywords):
    """Return list of (term, count) candidates; keywords first, then token freq."""
    if meta_keywords:
        kws = meta_keywords
        if isinstance(kws, str):
            kws = re.split(r"[;,，、]", kws)
        cands = []
        for k in kws:
            k = norm_text(k)
            if k:
                cands.append((k, text.count(k)))
        if cands:
            return cands
    ascii_tokens = re.findall(r"[A-Za-z][A-Za-z0-9\-]{3,}", text)
    ascii_counter = Counter(t.lower() for t in ascii_tokens
                            if t.lower() not in STOPWORDS)
    cjk_tokens = re.findall(r"[\u4e00-\u9fff]{2,6}", text)
    cjk_counter = Counter(cjk_tokens)
    pairs = []
    for term, n in ascii_counter.most_common(30):
        pairs.append((term, n))
    for term, n in cjk_counter.most_common(30):
        pairs.append((term, n))
    return pairs


def _title_from_rec(rec, text):
    val = _first_value(rec, ("title", "Title", "name", "headline"))
    if val:
        return norm_text(val)[:300]
    first = next((l.strip() for l in text.splitlines() if l.strip()), "")
    first = re.sub(r"^#+\s*", "", first).strip()
    return first[:300] or None


def normalize_materials(input_dir, work_dir):
    """Write normalised text into work/materials/, return (docs, degradations)."""
    files, too_many, skipped_big = discover_input(input_dir)
    mat_dir = os.path.join(work_dir, "materials")
    os.makedirs(mat_dir, exist_ok=True)
    degradations = []
    if too_many:
        degradations.append("input has more than %d files; kept first %d" %
                            (MAX_FILES, MAX_FILES))
    for s in skipped_big:
        degradations.append("skipped oversized file %s (%d bytes)" %
                            (s["path"], s["size"]))

    docs = []
    counter = 0
    for f in files:
        rel = f["rel"]
        ext = os.path.splitext(f["path"])[1].lower()
        if os.path.basename(f["path"]).lower() in CONTROL_FILES:
            continue
        if ext in PDF_EXT:
            counter += 1
            doc_id = "mat_%03d" % counter
            txt_path = os.path.join(mat_dir, "%s.txt" % doc_id)
            ok, note = _pdf_to_text(f["path"], txt_path)
            if not ok:
                degradations.append("PDF not parsed (%s): %s" % (rel, note))
                docs.append({"doc_id": doc_id, "path": f["path"], "rel": rel,
                             "kind": "pdf", "title": None, "year": None,
                             "year_source": None, "citations": None, "n_chars": 0,
                             "parsed": False, "note": note, "locator": None})
                continue
            text, enc, err = read_text(txt_path)
            if err:
                text, enc = "", None
            docs.append({"doc_id": doc_id, "path": f["path"], "rel": rel,
                         "kind": "pdf", "title": None, "year": None,
                         "year_source": None, "citations": None, "n_chars": len(text),
                         "parsed": True, "note": "pdf->%s" % enc,
                         "locator": "materials/%s.txt" % doc_id})
            continue
        if ext not in TEXT_EXT:
            degradations.append("ignored non-text/non-pdf file %s (%s)" % (rel, ext))
            continue
        text, enc, err = read_text(f["path"])
        if err:
            degradations.append("cannot read %s: %s" % (rel, err))
            continue
        records = list(iter_metadata_records(text, ext))
        if ext in (".json", ".csv") and records and len(records) >= 1 and \
                (ext == ".json" or len(records) >= 2):
            # metadata mode: one doc per record, but keep the raw text too
            for rec in records:
                counter += 1
                doc_id = "mat_%03d" % counter
                rec_text = json.dumps(rec, ensure_ascii=False) if isinstance(rec, dict) else str(rec)
                norm_path = os.path.join(mat_dir, "%s.txt" % doc_id)
                with open(norm_path, "w", encoding="utf-8") as fh:
                    fh.write(rec_text + "\n")
                year, ysrc = _extract_year(rec, rec_text)
                docs.append({"doc_id": doc_id, "path": f["path"], "rel": rel,
                             "kind": ext.lstrip("."), "title": _title_from_rec(rec, rec_text),
                             "year": year, "year_source": ysrc,
                             "citations": _extract_citations(rec), "n_chars": len(rec_text),
                             "parsed": True, "note": "metadata record",
                             "locator": "materials/%s.txt" % doc_id})
            continue
        # single-document mode
        counter += 1
        doc_id = "mat_%03d" % counter
        norm_path = os.path.join(mat_dir, "%s.txt" % doc_id)
        with open(norm_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        year, ysrc = _extract_year({}, text)
        docs.append({"doc_id": doc_id, "path": f["path"], "rel": rel,
                     "kind": ext.lstrip("."), "title": _title_from_rec({}, text),
                     "year": year, "year_source": ysrc, "citations": None,
                     "n_chars": len(text), "parsed": True, "note": "text(%s)" % enc,
                     "locator": "materials/%s.txt" % doc_id})
    return docs, degradations


# --------------------------------------------------------------------------- #
# deterministic analysis
# --------------------------------------------------------------------------- #
def find_candidate_sentences(docs, mat_dir, cue_words, label):
    """Return candidate dicts with locator + verbatim sentence. """
    out = []
    for d in docs:
        if not d["parsed"] or not d["locator"]:
            continue
        path = os.path.join(mat_dir, os.path.basename(d["locator"]))
        text, _, _ = read_text(path)
        for sent, line in _split_sentences(text):
            low = sent.lower()
            if any(c in low for c in cue_words):
                if len(sent) > 2000:
                    continue
                out.append({"doc_id": d["doc_id"], "kind": label, "text": sent,
                            "locator": "%s#L%d" % (d["locator"], line),
                            "year": d["year"], "machine": True})
    return out


def analyze_documents(docs, mat_dir):
    """Deterministic extraction; returns a dict of machine baseline pieces."""
    year_counter = Counter()
    year_docs = {}
    for d in docs:
        if d["year"] is not None and d["parsed"]:
            year_counter[d["year"]] += 1
            year_docs.setdefault(d["year"], []).append(d["doc_id"])
    distribution = [{"year": y, "count": year_counter[y],
                     "doc_ids": sorted(year_docs[y])}
                    for y in sorted(year_counter)]

    windows = _auto_windows(distribution)

    # term candidates aggregated across docs
    term_counter = Counter()
    doc_texts = {}
    for d in docs:
        if not d["parsed"] or not d["locator"]:
            continue
        path = os.path.join(mat_dir, os.path.basename(d["locator"]))
        text, _, _ = read_text(path)
        doc_texts[d["doc_id"]] = (d, text)
        for term, n in extract_term_candidates(text, None):
            term_counter[term] += n
    term_candidates = [{"term": t, "count": n, "machine": True}
                       for t, n in term_counter.most_common(60)]

    gap_cands = find_candidate_sentences(docs, mat_dir, GAP_CUES, "gap")
    conflict_cands = find_candidate_sentences(docs, mat_dir, CONFLICT_CUES, "conflict")

    # representative candidates: metadata-cited docs, then earliest-per-year
    cited = sorted([d for d in docs if d["citations"] is not None and d["parsed"]],
                   key=lambda d: (-d["citations"], d["year"] or 0))
    rep_cands = [{"doc_id": d["doc_id"], "title": d["title"], "year": d["year"],
                  "citations": d["citations"], "candidate_kind": "milestone",
                  "machine": True} for d in cited[:15]]
    seen = {d["doc_id"] for d in docs if not d["parsed"]}
    if len(rep_cands) < 5:
        for y in sorted(year_docs):
            for doc_id in year_docs[y]:
                if doc_id not in seen:
                    d = next(x for x in docs if x["doc_id"] == doc_id)
                    rep_cands.append({"doc_id": d["doc_id"], "title": d["title"],
                                      "year": d["year"], "citations": None,
                                      "candidate_kind": "method", "machine": True})
                    seen.add(doc_id)
                    break
            if len(rep_cands) >= 15:
                break

    return {"distribution": distribution, "windows": windows,
            "term_candidates": term_candidates,
            "gap_candidates": gap_cands[:40],
            "conflict_candidates": conflict_cands[:40],
            "representative_candidates": rep_cands[:15],
            "turning_points": _turning_points(distribution)}


def _auto_windows(distribution):
    years = [d["year"] for d in distribution]
    if not years:
        return []
    lo, hi = min(years), max(years)
    if hi - lo < 3:
        return [{"id": "W1", "period": "%d-%d" % (lo, hi),
                 "basis": "single window (span < 3 years)", "machine": True}]
    span = hi - lo + 1
    n = 4 if span >= 12 else 3 if span >= 8 else 2
    step = span // n
    if span % n:
        step += 1
    wins = []
    start = lo
    i = 0
    while start <= hi and i < 6:
        end = min(start + step - 1, hi)
        i += 1
        wins.append({"id": "W%d" % i, "period": "%d-%d" % (start, end),
                     "basis": "automatic equal-width window", "machine": True})
        start = end + 1
    return wins


def _turning_points(distribution):
    pts = []
    if len(distribution) < 3:
        return pts
    deltas = []
    for a, b in zip(distribution, distribution[1:]):
        deltas.append((b["year"], b["count"] - a["count"]))
    for i in range(1, len(deltas)):
        prev_sign = 1 if deltas[i - 1][1] > 0 else -1 if deltas[i - 1][1] < 0 else 0
        cur_sign = 1 if deltas[i][1] > 0 else -1 if deltas[i][1] < 0 else 0
        if prev_sign and cur_sign and prev_sign != cur_sign and \
                abs(deltas[i][1]) >= 2:
            pts.append({"year": deltas[i][0], "direction":
                        "rise" if cur_sign > 0 else "fall",
                        "delta": deltas[i][1], "machine": True,
                        "evidence_ids": []})
    return pts


# --------------------------------------------------------------------------- #
# skeleton & contract echo
# --------------------------------------------------------------------------- #
def empty_claim():
    return {"id": "", "claim": "", "support": "not_reported", "evidence_ids": [],
            "confidence": "low", "rationale": None}


def skeleton_result(case_id, contract, degradations):
    result = OrderedDict()
    result["case_id"] = case_id
    result["status"] = "partial"
    result["research_question"] = {"question": None, "scope": None,
                                   "sub_questions": [], "domain_note": None}
    result["protocol"] = {"inclusion_criteria": [], "exclusion_criteria": [],
                          "search_scope": None, "date_definition_note": None,
                          "dedup_rules": [], "screening_steps": []}
    result["time_trend"] = {"unit_note": None, "year_field_note": None,
                            "distribution": [], "windows": [],
                            "turning_points": [], "claims": [],
                            "machine_candidates": []}
    result["topic_evolution"] = {"stage_divisions": [], "term_mapping": [],
                                 "events": [], "claims": [], "machine_candidates": []}
    result["representative_works"] = {"criteria": [], "stages": [], "claims": [],
                                      "machine_candidates": []}
    result["research_gaps"] = {"gaps": [], "excluded_not_gaps": [], "claims": [],
                               "machine_candidates": []}
    result["evidence_map"] = {"schema_version": "1.0", "items": [],
                              "index": {"by_claim": {}, "by_doc": {}, "by_status": {}},
                              "locators_resolved": True, "unresolved": []}
    result["conflicts"] = {"items": [], "resolution_note": None}
    result["sources"] = []
    result["uncertainty"] = []
    result["pending_human"] = []
    result["change_log"] = []
    result["limitations"] = []
    result["errors"] = []
    result["degradations"] = list(degradations)
    result["runtime"] = {"started_at": now_iso(), "finished_at": None,
                         "elapsed_seconds": None, "stages": {}}
    result["artifacts"] = {"report": "report.md",
                           "evidence_map": "evidence_map.json",
                           "tables": ["tables/documents.csv",
                                      "tables/year_distribution.csv",
                                      "tables/term_evolution.csv",
                                      "tables/evidence.csv"]}
    # echo output_contract fields verbatim at top level (fixed keys win on
    # collision; unknown contract keys are preserved, never renamed/deleted)
    for k, v in contract.items():
        if k not in result:
            result[k] = v
    result["change_log"].append({
        "time": now_iso(), "action": "prepare", "reason": "initial partial skeleton",
        "before": None, "after": "skeleton with output_contract echoed"})
    return result


def fill_machine_baseline(result, baseline, docs):
    result["time_trend"]["distribution"] = baseline["distribution"]
    result["time_trend"]["windows"] = baseline["windows"]
    result["time_trend"]["turning_points"] = baseline["turning_points"]
    result["time_trend"]["machine_candidates"] = baseline["turning_points"]
    result["topic_evolution"]["machine_candidates"] = baseline["term_candidates"]
    result["representative_works"]["machine_candidates"] = \
        baseline["representative_candidates"]
    result["research_gaps"]["machine_candidates"] = baseline["gap_candidates"]
    result["conflicts"]["items"] = [{"id": "CF-%03d" % (i + 1),
                                     "topic": None, "candidate": True,
                                     "sides": [{"source": c["doc_id"],
                                                "locator": c["locator"],
                                                "quote": c["text"]}],
                                     "resolution": None}
                                    for i, c in enumerate(baseline["conflict_candidates"])]
    result["sources"] = [{"doc_id": d["doc_id"], "path": d["rel"], "kind": d["kind"],
                          "title": d["title"], "year": d["year"],
                          "year_source": d["year_source"], "citations": d["citations"],
                          "n_chars": d["n_chars"], "parsed": d["parsed"],
                          "note": d["note"], "locator": d["locator"]} for d in docs]
    result["change_log"].append({
        "time": now_iso(), "action": "analyze", "reason": "deterministic baseline",
        "before": "empty arrays", "after": "machine candidates + year distribution"})


# --------------------------------------------------------------------------- #
# patch merge (incremental agent edits)
# --------------------------------------------------------------------------- #
def deep_merge(target, patch):
    """Recursive merge. Lists of dicts with 'id' are upserted by id."""
    if isinstance(target, dict) and isinstance(patch, dict):
        for k, v in patch.items():
            if k in target and isinstance(target[k], dict) and isinstance(v, dict):
                deep_merge(target[k], v)
            elif k in target and isinstance(target[k], list) and isinstance(v, list) \
                    and v and all(isinstance(x, dict) and "id" in x for x in v):
                _upsert_list(target[k], v)
            else:
                target[k] = v
    elif isinstance(target, list) and isinstance(patch, list) and patch \
            and all(isinstance(x, dict) and "id" in x for x in patch):
        _upsert_list(target, patch)
    return target


def _upsert_list(target, patch_items):
    ids = {x.get("id"): i for i, x in enumerate(target) if isinstance(x, dict)}
    for item in patch_items:
        iid = item.get("id")
        if iid in ids:
            deep_merge(target[ids[iid]], item)
        else:
            target.append(item)


def apply_patches(work_dir, result):
    """Merge work/patches/*.json into result (deterministic filename order)."""
    patch_dir = os.path.join(work_dir, "patches")
    if not os.path.isdir(patch_dir):
        return result, 0
    names = sorted(n for n in os.listdir(patch_dir) if n.endswith(".json"))
    applied = 0
    for n in names:
        p = os.path.join(patch_dir, n)
        obj, err = safe_json_load(p)
        if err:
            result.setdefault("errors", []).append("patch %s: %s" % (n, err))
            continue
        if isinstance(obj, dict):
            deep_merge(result, obj)
            applied += 1
            result.setdefault("change_log", []).append({
                "time": now_iso(), "action": "merge_patch",
                "reason": "applied incremental patch", "before": None, "after": n})
    return result, applied


def rebuild_evidence_index(ev):
    """Rebuild bidirectional index from items (idempotent)."""
    items = ev.get("items", [])
    by_claim, by_doc, by_status = {}, {}, {}
    for it in items:
        iid = it.get("id")
        if not iid:
            continue
        if it.get("quote") and not it.get("quote_hash"):
            it["quote_hash"] = quote_hash(it["quote"])
        for c in it.get("used_by_claims", []):
            by_claim.setdefault(c, []).append(iid)
        d = it.get("doc_id")
        if d:
            by_doc.setdefault(d, []).append(iid)
        by_status.setdefault(it.get("status", "inferred"), []).append(iid)
    ev["index"] = {"by_claim": by_claim, "by_doc": by_doc, "by_status": by_status}
    ev["locators_resolved"] = all(not it.get("unresolved") for it in items)


# --------------------------------------------------------------------------- #
# self-check
# --------------------------------------------------------------------------- #
def _check(name, ok, detail=""):
    return {"check": name, "status": "PASS" if ok else "FAIL", "detail": detail}


def selfcheck(result, work_dir):
    """Return (report, ok_all). Never raises."""
    checks = []
    required = FIXED_KEYS
    for k in required:
        if k in result:
            checks.append(_check("key:%s" % k, True))
        else:
            checks.append(_check("key:%s" % k, False, "missing top-level key"))

    checks.append(_check("case_id", bool(result.get("case_id")),
                         "case_id is %r" % result.get("case_id")))
    st = result.get("status")
    checks.append(_check("status_enum", st in STATUS_ENUM, "got %r" % st))

    tt = result.get("time_trend", {})
    checks.append(_check("time_trend.distribution", isinstance(tt.get("distribution"), list)))
    te = result.get("topic_evolution", {})
    checks.append(_check("topic_evolution.present",
                         bool(te.get("stage_divisions") or te.get("term_mapping") or te.get("events") or te.get("claims"))))
    rw = result.get("representative_works", {})
    checks.append(_check("representative_works.present",
                         bool(rw.get("stages") or rw.get("claims") or rw.get("criteria"))))
    rg = result.get("research_gaps", {})
    checks.append(_check("research_gaps.present",
                         bool(rg.get("gaps") or rg.get("claims"))))

    # enum validation on nested arrays
    enum_errs = []
    for it in result.get("evidence_map", {}).get("items", []):
        if it.get("status") not in EVID_STATUS_ENUM:
            enum_errs.append("evidence %s status" % it.get("id"))
        if it.get("confidence") not in CONF_ENUM:
            enum_errs.append("evidence %s confidence" % it.get("id"))
        if it.get("kind") not in EVID_KIND_ENUM:
            enum_errs.append("evidence %s kind" % it.get("id"))
    for it in result.get("time_trend", {}).get("claims", []):
        if it.get("support") not in SUPPORT_ENUM:
            enum_errs.append("claim %s support" % it.get("id"))
    for it in result.get("topic_evolution", {}).get("events", []):
        if it.get("type") not in EVENT_TYPE_ENUM:
            enum_errs.append("event %s type" % it.get("id"))
    for it in result.get("research_gaps", {}).get("gaps", []):
        if it.get("type") not in GAP_TYPE_ENUM:
            enum_errs.append("gap %s type" % it.get("id"))
    for stg in result.get("representative_works", {}).get("stages", []):
        for w in stg.get("works", []):
            if w.get("kind") not in WORK_KIND_ENUM:
                enum_errs.append("work %s kind" % w.get("doc_id"))
    checks.append(_check("enum_values", not enum_errs, "; ".join(enum_errs[:5])))

    # quote resolvability
    unres = []
    ev_items = result.get("evidence_map", {}).get("items", [])
    for it in ev_items:
        loc = it.get("locator")
        q = it.get("quote")
        if not loc or not q:
            unres.append(it.get("id", "?") + " missing locator/quote")
            continue
        src = loc.split("#", 1)[0]
        src_path = os.path.join(work_dir, src)
        if not os.path.isfile(src_path):
            unres.append("%s source not found: %s" % (it.get("id", "?"), src))
            continue
        text, _, _ = read_text(src_path)
        if norm_text(q) and norm_text(q) not in norm_text(text):
            unres.append("%s quote not located" % it.get("id", "?"))
    checks.append(_check("quotes_resolvable", not unres, "; ".join(unres[:5])))

    # evidence backlinks from claims
    missing = []
    ev_ids = {it.get("id") for it in ev_items}
    def _walk_claims(node):
        if isinstance(node, dict):
            if isinstance(node.get("claims"), list):
                for c in node["claims"]:
                    for eid in c.get("evidence_ids", []):
                        if eid not in ev_ids:
                            missing.append("%s -> %s" % (c.get("id", "?"), eid))
            for v in node.values():
                _walk_claims(v)
        elif isinstance(node, list):
            for v in node:
                _walk_claims(v)
    for k in ("time_trend", "topic_evolution", "representative_works", "research_gaps"):
        _walk_claims(result.get(k, {}))
    checks.append(_check("evidence_backlinks", not missing, "; ".join(missing[:5])))

    # no fabricated benchmark score
    def _scan(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                if k.lower() in ("benchmark_score", "final_score", "evaluation_score",
                                 "grader_score") and isinstance(v, (int, float)):
                    return path + "/" + k
                r = _scan(v, path + "/" + k)
                if r:
                    return r
        elif isinstance(node, list):
            for i, v in enumerate(node):
                r = _scan(v, "%s[%d]" % (path, i))
                if r:
                    return r
        return None
    fake = _scan(result)
    checks.append(_check("no_fabricated_score", fake is None,
                         "suspicious score key at %s" % fake))

    ok_all = all(c["status"] == "PASS" for c in checks)
    return {"ok": ok_all, "checks": checks, "time": now_iso()}, ok_all


def compute_status(result):
    if result.get("status") == "input_error":
        return "input_error"
    parsed = [s for s in result.get("sources", []) if s.get("parsed")]
    if not parsed:
        return "insufficient_evidence"
    tt = result.get("time_trend", {})
    te = result.get("topic_evolution", {})
    rw = result.get("representative_works", {})
    rg = result.get("research_gaps", {})
    ev = result.get("evidence_map", {})
    have_products = bool(tt.get("distribution") or tt.get("claims")) and \
        bool(te.get("stage_divisions") or te.get("events") or te.get("claims")) and \
        bool(rw.get("stages") or rw.get("claims")) and \
        bool(rg.get("gaps") or rg.get("claims"))
    verified_claims = (len(tt.get("claims", [])) + len(te.get("claims", [])) +
                       len(rw.get("claims", [])) + len(rg.get("claims", []))) > 0
    has_evidence = len(ev.get("items", [])) > 0
    unresolved_machine = any(p.get("machine_candidates")
                             for p in (tt, te, rw, rg))
    if have_products and verified_claims and has_evidence and not unresolved_machine:
        return "ok"
    if have_products:
        return "partial"
    return "insufficient_evidence"


# --------------------------------------------------------------------------- #
# report & tables (editable artifacts)
# --------------------------------------------------------------------------- #
def _md_section(title, lines):
    return "## %s\n\n" % title + ("\n".join(lines) if lines else "_（空）_") + "\n"


def build_report(result):
    L = []
    L.append("# 研究前沿与趋势分析报告\n")
    L.append("- case_id: `%s`" % result.get("case_id"))
    L.append("- status: `%s`" % result.get("status"))
    L.append("- 生成时间: %s" % result.get("runtime", {}).get("finished_at"))
    L.append("")
    rq = result.get("research_question", {})
    L.append("## 研究问题\n")
    L.append("- 问题: %s" % (rq.get("question") or "（未填写）"))
    L.append("- 范围: %s" % (rq.get("scope") or "（未填写）"))
    L.append("- 子问题: %s" % ("；".join(rq.get("sub_questions", [])) or "（未填写）"))
    L.append("")
    pr = result.get("protocol", {})
    L.append("## 检索与纳排协议\n")
    L.append("- 检索范围: %s" % (pr.get("search_scope") or "（未填写）"))
    L.append("- 年份口径: %s" % (pr.get("date_definition_note") or "（未填写）"))
    L.append("- 纳入: %s" % ("；".join(pr.get("inclusion_criteria", [])) or "（未填写）"))
    L.append("- 排除: %s" % ("；".join(pr.get("exclusion_criteria", [])) or "（未填写）"))
    L.append("")
    tt = result.get("time_trend", {})
    L.append("## 时间趋势\n")
    L.append("- 统计口径: %s" % (tt.get("unit_note") or tt.get("year_field_note") or "（未填写）"))
    for d in tt.get("distribution", []):
        L.append("- %d 年：%d 篇（%s）" % (d["year"], d["count"], ", ".join(d.get("doc_ids", []))))
    L.append("")
    L.append("## 主题演化\n")
    te = result.get("topic_evolution", {})
    for e in te.get("events", []):
        L.append("- [%s] %s（%s）：%s" % (e.get("type"), e.get("term"), e.get("period"), e.get("cause")))
    if not te.get("events"):
        L.append("_（空）_")
    L.append("")
    L.append("## 代表性工作\n")
    rw = result.get("representative_works", {})
    for s in rw.get("stages", []):
        L.append("- %s（%s）" % (s.get("stage"), s.get("period")))
        for w in s.get("works", []):
            L.append("  - [%s] %s（%s，%s）" % (w.get("kind"), w.get("title"),
                                               w.get("year"), w.get("doc_id")))
    if not rw.get("stages"):
        L.append("_（空）_")
    L.append("")
    L.append("## 研究空白\n")
    rg = result.get("research_gaps", {})
    for g in rg.get("gaps", []):
        L.append("- [%s] %s" % (g.get("type"), g.get("description")))
        L.append("  - 排除证据: %s" % (g.get("exclusion_evidence") or "（未填写）"))
    if not rg.get("gaps"):
        L.append("_（空）_")
    L.append("")
    L.append("## 证据映射\n")
    for it in result.get("evidence_map", {}).get("items", []):
        L.append("- `%s` %s（%s）%s → %s" % (it.get("id"), it.get("status"),
                                              it.get("confidence"), it.get("locator"),
                                              it.get("claim", "")[:80]))
    L.append("")
    L.append("## 冲突与不确定性\n")
    for c in result.get("conflicts", {}).get("items", []):
        L.append("- %s：%s" % (c.get("id"), c.get("resolution") or "（未决）"))
    for u in result.get("uncertainty", []):
        L.append("- 不确定：%s" % u)
    L.append("")
    L.append("## 边界与待人工核验\n")
    for x in result.get("limitations", []):
        L.append("- 限制：%s" % x)
    for x in result.get("pending_human", []):
        L.append("- 待人工：%s" % x)
    L.append("")
    L.append("## 变更记录\n")
    for c in result.get("change_log", []):
        L.append("- %s %s：%s" % (c.get("time"), c.get("action"), c.get("reason")))
    return "\n".join(L) + "\n"


def write_tables(result, work_dir, out_dir):
    tdir = os.path.join(out_dir, "tables")
    os.makedirs(tdir, exist_ok=True)
    docs_rows = [[s.get("doc_id"), s.get("path"), s.get("kind"), s.get("title"),
                  s.get("year"), s.get("year_source"), s.get("citations"),
                  s.get("n_chars"), s.get("parsed"), s.get("note")]
                 for s in result.get("sources", [])]
    write_csv(os.path.join(tdir, "documents.csv"),
              ["doc_id", "path", "kind", "title", "year", "year_source",
               "citations", "n_chars", "parsed", "note"], docs_rows)
    dist = result.get("time_trend", {}).get("distribution", [])
    write_csv(os.path.join(tdir, "year_distribution.csv"),
              ["year", "count", "doc_ids"],
              [[d["year"], d["count"], ";".join(d.get("doc_ids", []))] for d in dist])
    tm = result.get("topic_evolution", {}).get("term_mapping", [])
    write_csv(os.path.join(tdir, "term_evolution.csv"),
              ["canonical", "variants", "first_year", "last_year"],
              [[t.get("canonical"), ";".join(t.get("variants", [])),
                t.get("first_year"), t.get("last_year")] for t in tm])
    ev = result.get("evidence_map", {}).get("items", [])
    write_csv(os.path.join(tdir, "evidence.csv"),
              ["id", "doc_id", "claim", "locator", "status", "confidence", "kind",
               "quote"],
              [[i.get("id"), i.get("doc_id"), i.get("claim"), i.get("locator"),
                i.get("status"), i.get("confidence"), i.get("kind"), i.get("quote")]
               for i in ev])


# --------------------------------------------------------------------------- #
# top-level pipeline
# --------------------------------------------------------------------------- #
def run_pipeline(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    started = now_iso()
    case = read_case(args.input)
    task = read_task_md(args.input)
    case_id = "unknown"
    contract = {}
    if case and case["obj"] is not None:
        case_id = extract_case_id(case["obj"], (task or {}).get("text", ""))
        contract = get_output_contract(case["obj"])
    elif task:
        case_id = extract_case_id({}, task.get("text", ""))
    else:
        result = skeleton_result("unknown", {}, ["no case.json and no task.md"])
        result["status"] = "input_error"
        result["errors"].append("input contract not parseable")
        _finalize_and_write(result, args, started)
        return result

    docs, degradations = normalize_materials(args.input, args.work)
    result = skeleton_result(case_id, contract, degradations)
    baseline = analyze_documents(docs, os.path.join(args.work, "materials"))
    fill_machine_baseline(result, baseline, docs)

    # manifest for the agent
    manifest = {"case_id": case_id, "docs": docs, "degradations": degradations,
                "baseline": baseline}
    write_json(os.path.join(args.work, "manifest.json"), manifest)

    result, applied = apply_patches(args.work, result)
    if applied:
        result["change_log"].append({
            "time": now_iso(), "action": "apply_patches",
            "reason": "merged incremental agent patches", "before": None,
            "after": "%d patch(es)" % applied})

    rebuild_evidence_index(result["evidence_map"])
    _finalize_and_write(result, args, started)
    return result


def _finalize_and_write(result, args, started):
    rep, ok_all = selfcheck(result, args.work)
    # only auto-finalize status when there are no agent patches yet
    if result.get("status") in (None, "partial") and not os.path.isdir(
            os.path.join(args.work, "patches")):
        result["status"] = compute_status(result)
    if rep and not rep.get("ok"):
        for c in rep["checks"]:
            if c["status"] == "FAIL" and "quote" in c["check"]:
                result.setdefault("uncertainty", []).append(
                    "selfcheck unresolved quote: %s" % c["detail"])
    finished = now_iso()
    result["runtime"]["finished_at"] = finished
    result["runtime"]["started_at"] = result["runtime"].get("started_at") or started
    try:
        t0 = datetime.datetime.fromisoformat(result["runtime"]["started_at"])
        t1 = datetime.datetime.fromisoformat(finished)
        result["runtime"]["elapsed_seconds"] = round((t1 - t0).total_seconds(), 2)
    except (ValueError, TypeError):
        result["runtime"]["elapsed_seconds"] = None
    result["runtime"]["stages"]["selfcheck_ok"] = bool(rep.get("ok"))

    write_json(os.path.join(args.output, "result.json"), result)
    write_json(os.path.join(args.output, "evidence_map.json"), result["evidence_map"])
    with open(os.path.join(args.output, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(build_report(result))
    write_tables(result, args.work, args.output)
    write_json(os.path.join(args.work, "selfcheck.json"), rep)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="main.py",
                                 description="research-frontier evidence analysis")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--stage", choices=["all"], default="all",
                    help="reserved for future granular stages")
    args = ap.parse_args(argv)
    run_pipeline(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
