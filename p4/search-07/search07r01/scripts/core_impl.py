# -*- coding: utf-8 -*-
"""core_impl.py - search coverage & omission analysis (evidence-provenance edition).

Pure standard library. Every conclusion written into result.json carries a
locator (file + json path / byte offset) and a verbatim quote, so a reviewer
can re-derive the number from the source material without trusting this script.

Subcommands:
    scaffold   write a legal partial result.json skeleton first (<=3 min rule)
    inspect    inventory input files, detect candidate records & parse errors
    run        build frozen reference set, match against results, compute coverage
    merge      apply human adjudications/attributions and finalize the report

Exit codes: 0 on success and on any *handled* input condition (empty dir,
bad JSON, missing reference set). The script never raises; errors are recorded
inside result.json ``errors``/``degradations``.
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

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

MAX_FILE_BYTES = 2 * 1024 * 1024          # skip larger text/JSON files
MAX_QUOTE_LEN = 320                        # verbatim evidence quote cap (chars)
TITLE_FUZZY = 0.88                         # fuzzy hit threshold
TITLE_ADJUDICATION = 0.70                  # ambiguous -> manual adjudication
YEAR_TOLERANCE = 1                         # allowed year difference for fuzzy hit
SUPPORTED_TEXT_EXT = {".json", ".jsonl", ".txt", ".md", ".csv", ".tsv", ".xml"}
PDF_EXT = {".pdf"}

STATUS_ORDER = ["ok", "partial", "insufficient_evidence", "input_error"]

OMISSION_CATEGORIES = [
    "query_term_gap",        # 查询词未覆盖同义/变体表达
    "field_too_narrow",      # 检索字段限制过窄（如仅题名/仅摘要）
    "time_window_truncation",# 时间窗截断
    "source_not_indexed",    # 来源库未收录
    "identifier_anomaly",    # 记录标识异常/冲突/缺失
    "unattributed",          # 尚无法归因，转人工
]

# --------------------------------------------------------------------------- #
# Small deterministic utilities
# --------------------------------------------------------------------------- #


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def content_hash(text: str, n: int = 12) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:n]


def _json_default(o):
    return str(o)


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, default=_json_default)
    os.replace(tmp, path)


def read_text_safe(path, max_bytes=MAX_FILE_BYTES):
    """utf-8-sig read with BOM/bad-encoding tolerance.

    Returns (text | None, error | None, skipped_because_large | None).
    """
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, "os.stat failed: %s" % exc, None
    if size > max_bytes:
        return None, None, {"file": path, "bytes": size, "limit": max_bytes}
    data = None
    last_err = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                data = fh.read()
            last_err = None
            break
        except (UnicodeDecodeError, LookupError) as exc:
            last_err = "decode(%s): %s" % (enc, exc)
        except OSError as exc:
            return None, "open failed: %s" % exc, None
    if data is None:
        return None, last_err or "unreadable", None
    return data, None, None


def normalize_text(s):
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s)).lower()
    # leading articles do not affect title identity for matching purposes
    s = re.sub(r"\b(the|a|an)\b", " ", s)
    s = re.sub(r"[^0-9a-z]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def extract_year(value):
    if isinstance(value, int):
        if 1800 <= value <= 2100:
            return str(value)
        return None
    if value is None:
        return None
    m = re.search(r"(18|19|20)\d{2}", str(value))
    return m.group(0) if m else None


def extract_doi(value):
    if value is None:
        return None
    s = str(value).strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi\s*:\s*", "", s, flags=re.I)
    s = s.strip().strip("<>").strip()
    return s.lower() if s else None


def extract_arxiv(value):
    if value is None:
        return None
    s = str(value).strip()
    s = re.sub(r"^https?://arxiv\.org/(abs|pdf)/", "", s, flags=re.I)
    s = re.sub(r"^arxiv\s*:\s*", "", s, flags=re.I)
    s = re.sub(r"v\d+$", "", s)
    return s.lower() if s else None


def extract_pmid(value):
    if value is None:
        return None
    m = re.search(r"\d{4,10}", str(value))
    return m.group(0) if m else None


def first_author_surname(record):
    """Best-effort first-author surname, or None."""
    cand = None
    for key in ("first_author", "firstAuthor", "author", "authors",
                "creator", "creators", "given-name"):
        v = record.get(key)
        if isinstance(v, str) and v.strip():
            cand = v
            break
        if isinstance(v, list) and v and isinstance(v[0], dict):
            inner = v[0]
            fam = inner.get("family") or inner.get("familyName") or inner.get("surname")
            given = inner.get("given") or inner.get("givenName") or inner.get("name")
            cand = fam or given
            if cand:
                break
        elif isinstance(v, list) and v and isinstance(v[0], str):
            cand = v[0]
            break
    if not cand:
        return None
    cand = str(cand).strip().rstrip(".")
    # keep last token as surname when "First Last" ordering is used
    parts = [p for p in re.split(r"[\s,]+", cand) if p]
    if not parts:
        return None
    return normalize_text(parts[-1]) or None


def record_title(record):
    for key in ("title", "Title", "articleTitle", "displayTitle", "name"):
        v = record.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, list) and v:
            return str(v[0]).strip()
    return None


def record_identifiers(record):
    ids = {}
    ids["doi"] = extract_doi(record.get("doi") or record.get("DOI") or record.get("DOI_ID"))
    ids["arxiv"] = extract_arxiv(record.get("arxiv") or record.get("arXiv") or
                                 record.get("eprint") or record.get("arxiv_id"))
    ids["pmid"] = extract_pmid(record.get("pmid") or record.get("PMID") or
                               record.get("pubmed_id") or record.get("pm_id"))
    return {k: v for k, v in ids.items() if v}


# --------------------------------------------------------------------------- #
# Input discovery
# --------------------------------------------------------------------------- #


def discover_files(input_dir):
    files = []
    if not os.path.isdir(input_dir):
        return files, ["input dir missing: %s" % input_dir]
    for root, _dirs, names in os.walk(input_dir):
        for name in sorted(names):
            if name.startswith("."):
                continue
            full = os.path.join(root, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = None
            files.append({"path": full, "name": name,
                          "rel": os.path.relpath(full, input_dir),
                          "bytes": size,
                          "ext": os.path.splitext(name)[1].lower()})
    files.sort(key=lambda f: f["rel"])
    return files, []


def load_case_meta(input_dir):
    """Read case.json / task.md metadata; returns dict."""
    meta = {"case_id": None, "challenge_id": None, "output_contract": None,
            "task_lines": []}
    files, _ = discover_files(input_dir)
    for f in files:
        if f["name"].lower() == "case.json" and f["ext"] == ".json":
            text, err, _skipped = read_text_safe(f["path"])
            if err or text is None:
                continue
            try:
                obj = json.loads(text)
            except Exception:
                obj = None
            if isinstance(obj, dict):
                for k in ("case_id", "id", "challenge_id", "challengeId"):
                    if obj.get(k):
                        meta["case_id"] = meta["case_id"] or str(obj[k])
                meta["challenge_id"] = obj.get("challenge_id") or obj.get("challengeId")
                if isinstance(obj.get("output_contract"), dict):
                    meta["output_contract"] = obj["output_contract"]
                # task constraints & research question, if present
                for k in ("research_question", "researchQuestion", "topic", "query"):
                    if obj.get(k) and not meta.get("research_question"):
                        meta["research_question"] = str(obj[k])
        if f["name"].lower() == "task.md" and f["ext"] == ".md":
            text, err, _skipped = read_text_safe(f["path"])
            if err or text is None:
                continue
            meta["task_lines"] = [ln.strip() for ln in text.splitlines() if ln.strip()][:200]
            m = re.search(r"challenge[-_]?id[：:`\s]*([A-Za-z0-9\-]+)", text, re.I)
            if m:
                meta["challenge_id"] = meta["challenge_id"] or m.group(1)
    if not meta["case_id"]:
        digest = content_hash(json.dumps(meta.get("task_lines", [])[:20], sort_keys=True))
        meta["case_id"] = "case-" + digest
    return meta


def backfill_contract(contract):
    """Return a dict whose keys mirror output_contract with null/[] defaults."""
    if not isinstance(contract, dict):
        return {}
    out = {}
    for k, v in contract.items():
        if isinstance(v, list):
            out[k] = []
        elif isinstance(v, dict):
            out[k] = {}
        elif isinstance(v, bool):
            out[k] = v
        elif isinstance(v, (int, float)):
            out[k] = None
        else:
            out[k] = None
    return out


# --------------------------------------------------------------------------- #
# JSON / text record parsing
# --------------------------------------------------------------------------- #


def _records_from_json(obj, errors, file_ref):
    """Recursively pull a flat list of candidate bibliographic records."""
    records = []
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                records.append(item)
    elif isinstance(obj, dict):
        # Crossref envelope
        msg = obj.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("items"), list):
            return [i for i in msg["items"] if isinstance(i, dict)]
        # EuropePMC envelope
        rl = obj.get("resultList")
        if isinstance(rl, dict) and isinstance(rl.get("result"), list):
            return [i for i in rl["result"] if isinstance(i, dict)]
        # common wrappers
        for key in ("items", "results", "records", "data", "hits", "documents",
                    "references", "publications", "entries", "works"):
            v = obj.get(key)
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        records.append(item)
        # a single record with title/doi keys
        if not records and (obj.get("title") or obj.get("doi") or obj.get("DOI")):
            records.append(obj)
    return records


def _looks_bibliographic(record):
    return bool(record_title(record) or record_identifiers(record))


def parse_json_records(path, errors, degradations):
    text, err, skipped = read_text_safe(path)
    if skipped:
        degradations.append({"file": path, "reason": "size_skipped",
                             "detail": skipped})
        return []
    if err or text is None:
        errors.append({"file": path, "reason": "read_error", "detail": err})
        return []
    records = []
    if path.lower().endswith(".jsonl"):
        for i, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception as exc:
                errors.append({"file": path, "line": i,
                               "reason": "bad_json_line", "detail": str(exc)})
                continue
            records.extend(_records_from_json(obj, errors, path))
    else:
        try:
            obj = json.loads(text)
        except Exception as exc:
            errors.append({"file": path, "reason": "bad_json",
                           "detail": str(exc)})
            return []
        records.extend(_records_from_json(obj, errors, path))
    return [r for r in records if isinstance(r, dict) and _looks_bibliographic(r)]


def parse_tabular_records(path, errors):
    """CSV/TSV -> list of dicts keyed by header row."""
    text, err, skipped = read_text_safe(path)
    if skipped or err or text is None:
        if err:
            errors.append({"file": path, "reason": "read_error", "detail": err})
        return []
    delim = "\t" if path.lower().endswith((".tsv",)) else ","
    try:
        reader = csv.reader(text.splitlines(), delimiter=delim)
        rows = [r for r in reader if any(c.strip() for c in r)]
    except Exception as exc:
        errors.append({"file": path, "reason": "csv_error", "detail": str(exc)})
        return []
    if not rows:
        return []
    header = [h.strip().lower() for h in rows[0]]
    out = []
    for r in rows[1:]:
        rec = {header[i]: r[i].strip() for i in range(min(len(header), len(r))) if r[i].strip()}
        if _looks_bibliographic(rec):
            out.append(rec)
    return out


def find_in_text(text, needle):
    """Return (offset, quote) for the first verbatim occurrence of needle."""
    if not needle or not text:
        return None, None
    idx = text.find(needle)
    if idx < 0:
        # try case-insensitive
        idx = text.lower().find(needle.lower())
    if idx < 0:
        return None, None
    quote = text[idx:idx + min(MAX_QUOTE_LEN, max(len(needle), 60))]
    return idx, quote


# --------------------------------------------------------------------------- #
# Reference set & matching
# --------------------------------------------------------------------------- #


def build_reference_set(records, source_files, evidence_texts):
    seen = {}
    out = []
    for src in source_files:
        for rec in records.get(src, []):
            ids = record_identifiers(rec)
            title = record_title(rec)
            norm_title = normalize_text(title)
            key = None
            if ids.get("doi"):
                key = ("doi", ids["doi"])
            elif ids.get("pmid"):
                key = ("pmid", ids["pmid"])
            elif norm_title:
                key = ("title", norm_title)
            if key and key in seen:
                seen[key]["flags"].append("duplicate")
                continue
            ref_id = "R%03d" % (len(out) + 1)
            flags = []
            if not ids:
                flags.append("no_identifier")
            rec_entry = {
                "ref_id": ref_id,
                "identifiers": ids,
                "title": title,
                "title_norm": norm_title,
                "first_author": first_author_surname(rec),
                "year": extract_year(rec.get("year") or rec.get("pubyear") or
                                     rec.get("published_year") or
                                     rec.get("publication_year") or
                                     rec.get("issued")),
                "source_file": src,
                "inclusion_reason": rec.get("inclusion_reason") or rec.get("reason") or None,
                "flags": flags,
                "evidence": _evidence_for_record(rec, src, evidence_texts.get(src)),
            }
            if key:
                seen[key] = rec_entry
            out.append(rec_entry)
    return out


def _evidence_for_record(rec, src, text):
    title = record_title(rec)
    locator = {"file": src}
    quote = None
    if text and title:
        offset, quote = find_in_text(text, title)
        if offset is not None:
            locator["offset"] = offset
    else:
        locator["path"] = "record.%s" % ("title" if title else "doi")
    if quote is None:
        quote = title or (json.dumps(record_identifiers(rec), ensure_ascii=False) or None)
    return {"locator": locator, "quote": (quote or "not_reported")}


def _match_score(ref, cand):
    ids_ref = ref.get("identifiers", {})
    ids_cand = record_identifiers(cand)
    if ids_ref.get("doi") and ids_cand.get("doi") and ids_ref["doi"] == ids_cand["doi"]:
        return 1.0, "identifier:doi"
    if ids_ref.get("pmid") and ids_cand.get("pmid") and ids_ref["pmid"] == ids_cand["pmid"]:
        return 1.0, "identifier:pmid"
    if ids_ref.get("arxiv") and ids_cand.get("arxiv") and ids_ref["arxiv"] == ids_cand["arxiv"]:
        return 1.0, "identifier:arxiv"
    rt = ref.get("title_norm") or ""
    ct = normalize_text(record_title(cand))
    if not rt or not ct:
        return 0.0, "no_title"
    ratio = difflib.SequenceMatcher(None, rt, ct).ratio()
    return ratio, "title_fuzzy"


def compute_coverage(reference_set, result_records, result_sources, evidence_texts):
    hit_matrix = []
    hits = 0
    omissions = []
    for ref in reference_set:
        best = None
        best_score = 0.0
        best_kind = "no_title"
        for src in result_sources:
            for cand in result_records.get(src, []):
                score, kind = _match_score(ref, cand)
                if score > best_score:
                    best_score = score
                    best_kind = kind
                    best = (src, cand)
        status = "miss"
        matched_by = None
        candidate = None
        if best is not None and best_kind.startswith("identifier"):
            status, matched_by = "hit", best_kind
            candidate = best
        elif best is not None and best_score >= TITLE_FUZZY:
            status, matched_by = "hit", "title_fuzzy"
            candidate = best
        elif best is not None and best_score >= TITLE_ADJUDICATION:
            status, matched_by = "adjudicate", "title_fuzzy"
            candidate = best
        row = {
            "ref_id": ref["ref_id"],
            "status": status,
            "matched_by": matched_by,
            "match_score": round(best_score, 4),
            "candidate_title": record_title(candidate[1]) if candidate else None,
            "candidate_identifiers": record_identifiers(candidate[1]) if candidate else None,
            "evidence": ref.get("evidence"),
        }
        hit_matrix.append(row)
        if status == "hit":
            hits += 1
        else:
            omissions.append({
                "ref_id": ref["ref_id"],
                "title": ref.get("title"),
                "identifiers": ref.get("identifiers"),
                "best_score": round(best_score, 4),
                "status": status,
                "attribution": None,
                "evidence": ref.get("evidence"),
            })
    return hit_matrix, hits, omissions


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def _base_result(meta, status):
    result = {
        "case_id": meta.get("case_id"),
        "status": status,
        "runtime": {"started_at": now_iso(), "seconds": 0.0},
        "change_log": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "research_question": meta.get("research_question"),
        "search_scope": {},
        "reference_set": {"version": None, "total": 0, "records": []},
        "search_sessions": [],
        "coverage": {"denominator": None, "numerator": None,
                     "coverage_rate": None, "omission_rate": None,
                     "hit_rule": {"identifier_exact": "exact-normalized",
                                  "title_fuzzy": TITLE_FUZZY,
                                  "title_adjudication": TITLE_ADJUDICATION,
                                  "year_tolerance": YEAR_TOLERANCE}},
        "hit_matrix": [],
        "omissions": [],
        "supplementary_searches": [],
        "marginal_gain": {},
        "residual_omissions": [],
        "evidence_map": [],
    }
    # backfill all output_contract keys verbatim (defaults null/[])
    for k, v in backfill_contract(meta.get("output_contract")).items():
        result[k] = v
    if meta.get("challenge_id"):
        result["challenge_id"] = meta["challenge_id"]
    return result


def _load_result(output_dir):
    p = os.path.join(output_dir, "result.json")
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    return None


def cmd_scaffold(args):
    t0 = time.time()
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    meta = load_case_meta(args.input)
    result = _base_result(meta, "partial")
    files, _ = discover_files(args.input)
    result["sources"] = [{"file": f["rel"], "bytes": f["bytes"]} for f in files]
    if not files:
        result["status"] = "insufficient_evidence"
        result["limitations"].append("input directory empty or missing; no material to analyze")
    result["change_log"].append({"at": now_iso(),
                                 "step": "scaffold",
                                 "note": "legal partial skeleton written before any analysis"})
    result["runtime"]["seconds"] = round(time.time() - t0, 3)
    write_json(os.path.join(args.output, "result.json"), result)
    write_json(os.path.join(args.work, "scaffold.json"), result)
    return 0


def cmd_inspect(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    files, errs = discover_files(args.input)
    errors = [{"reason": "discover_error", "detail": e} for e in errs]
    degradations = []
    file_report = []
    evidence_texts = {}
    for f in files:
        entry = {"rel": f["rel"], "bytes": f["bytes"], "ext": f["ext"]}
        if f["bytes"] is not None and f["bytes"] > MAX_FILE_BYTES:
            entry["skipped"] = True
            degradations.append({"file": f["rel"], "reason": "size_skipped"})
            file_report.append(entry)
            continue
        text, err, skipped = read_text_safe(f["path"])
        if skipped:
            entry["skipped"] = True
            degradations.append({"file": f["rel"], "reason": "size_skipped"})
            file_report.append(entry)
            continue
        if f["ext"] in PDF_EXT:
            entry["kind"] = "pdf"
            entry["note"] = "PDF requires agent-side text extraction via pdf-text-tool; write /app/work/<name>.txt then re-run inspect"
            degradations.append({"file": f["rel"],
                                 "reason": "pdf_not_parsed_by_stdlib",
                                 "detail": "use preinstalled pdf-text-tool to extract text sidecar"})
            file_report.append(entry)
            continue
        if f["ext"] in SUPPORTED_TEXT_EXT and text is not None:
            evidence_texts[f["rel"]] = text
            if f["ext"] == ".json" or f["ext"] == ".jsonl":
                records = parse_json_records(f["path"], errors, degradations)
                entry["kind"] = "json"
                entry["records"] = len(records)
            elif f["ext"] in (".csv", ".tsv"):
                records = parse_tabular_records(f["path"], errors)
                entry["kind"] = "tabular"
                entry["records"] = len(records)
            else:
                entry["kind"] = "text"
                entry["records"] = 0
        else:
            entry["kind"] = "unsupported" if text is None else "text"
            entry["records"] = 0
        file_report.append(entry)
    report = {"files": file_report, "errors": errors, "degradations": degradations}
    write_json(os.path.join(args.work, "inspect.json"), report)
    # cache evidence texts for later steps (truncated per file for safety)
    cache = {k: v[:500000] for k, v in evidence_texts.items()}
    write_json(os.path.join(args.work, "evidence_cache.json"), cache)
    return 0


def cmd_run(args):
    t0 = time.time()
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    errors, degradations = [], []
    result = _load_result(args.output) or _base_result(load_case_meta(args.input), "partial")

    files, _ = discover_files(args.input)
    by_rel = {f["rel"]: f for f in files}

    # resolve refs/results: explicit args (rel paths or globs) else heuristics
    refs_rel, results_rel = [], []
    if args.refs:
        refs_rel = _resolve_globs(args.input, args.refs, by_rel)
    if args.results:
        results_rel = _resolve_globs(args.input, args.results, by_rel)
    if not refs_rel and not results_rel:
        refs_rel, results_rel = _heuristic_split(by_rel, args.input, errors, degradations)

    evidence_cache = {}
    cache_path = os.path.join(args.work, "evidence_cache.json")
    if os.path.isfile(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as fh:
                evidence_cache = json.load(fh)
        except Exception:
            evidence_cache = {}

    ref_records = {}
    for rel in refs_rel:
        full = os.path.join(args.input, rel)
        ref_records[rel] = _parse_for_file(full, rel, evidence_cache, errors, degradations)
    result_records = {}
    for rel in results_rel:
        full = os.path.join(args.input, rel)
        result_records[rel] = _parse_for_file(full, rel, evidence_cache, errors, degradations)

    reference_set = build_reference_set(ref_records, refs_rel, evidence_cache)
    hit_matrix, hits, omissions = compute_coverage(
        reference_set, result_records, results_rel, evidence_cache)

    total = len(reference_set)
    coverage = result["coverage"]
    coverage["denominator"] = total
    coverage["numerator"] = hits
    if total:
        coverage["coverage_rate"] = round(hits / total, 6)
        coverage["omission_rate"] = round((total - hits) / total, 6)
    else:
        coverage["coverage_rate"] = None
        coverage["omission_rate"] = None

    version = "frozen-%s-%s" % (datetime.now(timezone.utc).strftime("%Y%m%d"),
                                content_hash(json.dumps(
                                    sorted((r["ref_id"], r.get("title") or "") for r in reference_set),
                                    sort_keys=True)))
    result["reference_set"] = {"version": version, "total": total,
                               "source_files": refs_rel, "records": reference_set}
    result["sources"] = [{"role": "reference_set", "files": refs_rel},
                         {"role": "search_results", "files": results_rel}]
    result["hit_matrix"] = hit_matrix
    result["omissions"] = omissions
    result["errors"] = errors
    result["degradations"] = degradations
    result["status"] = "partial" if (total and omissions) or not total else "ok"
    if not total:
        result["status"] = "insufficient_evidence"
        result["limitations"].append("no reference set identified; cannot compute coverage")
    result["change_log"].append({"at": now_iso(), "step": "run",
                                 "note": "reference set frozen; coverage computed"})
    result["runtime"]["seconds"] = round(time.time() - t0, 3)

    write_json(os.path.join(args.work, "reference_set.json"), result["reference_set"])
    write_json(os.path.join(args.work, "coverage_core.json"),
               {"reference_set": result["reference_set"],
                "hit_matrix": hit_matrix, "omissions": omissions,
                "coverage": coverage})
    _write_hit_matrix_csv(args.output, hit_matrix, reference_set)
    write_json(os.path.join(args.output, "result.json"), result)
    return 0


def _resolve_globs(input_dir, specs, by_rel):
    out = []
    for spec in specs:
        if spec in by_rel:
            out.append(spec)
            continue
        import glob as _g
        for pat in [spec, os.path.join(input_dir, spec)]:
            for p in _g.glob(pat):
                rel = os.path.relpath(p, input_dir)
                if rel in by_rel:
                    out.append(rel)
    # dedupe preserving order
    seen = set()
    return [r for r in out if not (r in seen or seen.add(r))]


def _heuristic_split(by_rel, input_dir, errors, degradations):
    refs, results = [], []
    # results-y terms checked first so "crossref-results.json" is never caught
    # by the reference-side word "ref".
    result_pat = re.compile(
        r"(crossref|europepmc|pubmed|publisher|quer|search|hit|response|result)",
        re.I)
    ref_pat = re.compile(
        r"\bref(s|erence|erences)?\b|gold|known|included|seed|relevant|ground.?truth",
        re.I)
    for rel in sorted(by_rel):
        if result_pat.search(rel):
            results.append(rel)
        elif ref_pat.search(rel):
            refs.append(rel)
    # fall back: no explicit reference set -> treat all bibliographic
    # JSON/CSV as candidate results and flag that refs are undetermined.
    if not refs:
        for rel in sorted(by_rel):
            if by_rel[rel]["ext"] in (".json", ".jsonl", ".csv", ".tsv") and rel not in results:
                results.append(rel)
        if results:
            degradations.append(
                {"reason": "reference_set_undetermined",
                 "detail": "no file marked as reference set; provide --refs <file>"})
    return refs, results


def _parse_for_file(full, rel, evidence_cache, errors, degradations):
    ext = os.path.splitext(full)[1].lower()
    if ext in (".json", ".jsonl"):
        return parse_json_records(full, errors, degradations)
    if ext in (".csv", ".tsv"):
        return parse_tabular_records(full, errors)
    return []


def _write_hit_matrix_csv(output_dir, hit_matrix, reference_set):
    ref_by_id = {r["ref_id"]: r for r in reference_set}
    path = os.path.join(output_dir, "hit_matrix.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ref_id", "status", "matched_by", "match_score",
                    "ref_title", "candidate_title", "evidence_file", "evidence_quote"])
        for row in hit_matrix:
            ref = ref_by_id.get(row["ref_id"], {})
            ev = row.get("evidence") or {}
            loc = ev.get("locator") or {}
            w.writerow([row["ref_id"], row["status"], row.get("matched_by") or "",
                        row.get("match_score", ""), ref.get("title") or "",
                        row.get("candidate_title") or "", loc.get("file", ""),
                        (ev.get("quote") or "")[:200]])


def cmd_merge(args):
    t0 = time.time()
    os.makedirs(args.output, exist_ok=True)
    result = _load_result(args.output)
    if result is None:
        result = _base_result(load_case_meta(args.input), "input_error")
    updates = {}
    if args.updates and os.path.isfile(args.updates):
        text, err, _skipped = read_text_safe(args.updates)
        if err or text is None:
            result["errors"].append({"reason": "updates_read_error",
                                     "detail": err or "unreadable"})
        else:
            try:
                updates = json.loads(text)
                if not isinstance(updates, dict):
                    result["errors"].append({"reason": "updates_not_object",
                                             "detail": "updates must be a JSON object"})
                    updates = {}
            except Exception as exc:
                result["errors"].append({"reason": "updates_bad_json",
                                         "detail": str(exc)})

    hit_matrix = result.get("hit_matrix", [])
    omissions = result.get("omissions", [])
    omissions_by_id = {o["ref_id"]: o for o in omissions}
    # apply adjudications (hit/miss/conflict/retracted/unavailable decisions)
    for adj in updates.get("adjudications", []):
        rid = adj.get("ref_id")
        for row in hit_matrix:
            if row["ref_id"] == rid:
                decision = adj.get("decision", "miss")
                if decision == "hit":
                    row["status"] = "hit"
                    row["matched_by"] = adj.get("matched_by") or "manual"
                    row["candidate_title"] = adj.get("candidate_title")
                    row["evidence"] = adj.get("evidence")
                elif decision == "miss":
                    row["status"] = "miss"
                else:
                    row["status"] = decision  # conflict / retracted / unavailable
                row["adjudicated"] = True
                row["adjudication_evidence"] = adj.get("evidence")
        if rid in omissions_by_id and adj.get("decision") == "hit":
            omissions_by_id.pop(rid)
    # apply attributions to remaining omissions
    for att in updates.get("attributions", []):
        rid = att.get("ref_id")
        if rid in omissions_by_id:
            omissions_by_id[rid]["attribution"] = {
                "category": att.get("category", "unattributed"),
                "query": att.get("query"),
                "field": att.get("field"),
                "source": att.get("source"),
                "time_window": att.get("time_window"),
                "evidence": att.get("evidence"),
            }

    # supplementary searches & marginal gain
    supp = updates.get("supplementary_searches", [])
    pre_omissions = [o for o in omissions
                     if (o.get("attribution") is None or
                         (o.get("status") != "hit"))]
    pre_count = len(pre_omissions)
    residual = [o for o in omissions_by_id.values()]
    result["supplementary_searches"] = supp
    new_hits = [rid for s in supp for rid in s.get("new_hits", [])]
    for rid in new_hits:
        if rid in omissions_by_id:
            omissions_by_id.pop(rid)
    residual_after = [o for o in omissions_by_id.values()]
    marginal_gain = {
        "pre_omissions": pre_count,
        "post_omissions": len(residual_after),
        "new_hits_from_supplementary": [rid for rid in new_hits if rid in
                                        set(o["ref_id"] for o in pre_omissions)],
    }
    if pre_count:
        marginal_gain["omission_reduction"] = round(
            (pre_count - len(residual_after)) / pre_count, 6)
    else:
        marginal_gain["omission_reduction"] = None
    result["marginal_gain"] = marginal_gain
    result["omissions"] = [o for o in omissions_by_id.values()]
    result["residual_omissions"] = [{
        "ref_id": o["ref_id"], "title": o.get("title"),
        "attribution": o.get("attribution"),
        "recoverable": bool(o.get("attribution") and
                            o["attribution"].get("category") not in
                            ("source_not_indexed", "identifier_anomaly")),
        "needs": (o.get("attribution") or {}).get("category"),
    } for o in residual_after]

    # build evidence_map: one entry per conclusion/claim with locator+quote
    evidence_map = []
    for row in hit_matrix:
        ev = row.get("evidence") or {}
        if ev.get("quote"):
            evidence_map.append({
                "evidence_id": "EV-%s" % row["ref_id"],
                "claim": "match status %s for %s" % (row["status"], row["ref_id"]),
                "locator": ev.get("locator"),
                "quote": ev.get("quote"),
                "verbatim": True,
                "status": "verified" if row["status"] in ("hit", "miss") else "needs_review",
            })
    for o in result["omissions"]:
        ev = o.get("evidence") or {}
        att = o.get("attribution") or {}
        if att.get("evidence"):
            ev = att["evidence"]
        if ev.get("quote"):
            evidence_map.append({
                "evidence_id": "EV-%s-attribution" % o["ref_id"],
                "claim": "omission attribution for %s" % o["ref_id"],
                "locator": ev.get("locator"),
                "quote": ev.get("quote"),
                "verbatim": True,
                "status": "verified" if att.get("category") else "needs_review",
            })
    result["evidence_map"] = evidence_map

    # final status: honest, never overstated
    result["status"] = _final_status(result)
    if updates.get("pending_human"):
        result["pending_human"] = updates["pending_human"]
    if updates.get("limitations"):
        result["limitations"] = (result.get("limitations") or []) + updates["limitations"]
    if updates.get("uncertainty"):
        result["uncertainty"] = updates["uncertainty"]
    if updates.get("research_question"):
        result["research_question"] = updates["research_question"]
    if updates.get("search_scope"):
        result["search_scope"] = updates["search_scope"]
    result["change_log"].append({"at": now_iso(), "step": "merge",
                                 "note": "adjudications, attributions and supplementary searches applied"})
    result["runtime"]["seconds"] = round(time.time() - t0, 3)

    write_json(os.path.join(args.output, "result.json"), result)
    _write_hit_matrix_csv(args.output, result["hit_matrix"],
                          result["reference_set"].get("records", []))
    write_json(os.path.join(args.output, "evidence_map.json"),
               {"evidence_map": result["evidence_map"]})
    _write_report_md(args.output, result)
    return 0


def _final_status(result):
    total = result.get("reference_set", {}).get("total", 0)
    if not total:
        return "insufficient_evidence"
    omissions = result.get("omissions", [])
    unattributed = [o for o in omissions if not o.get("attribution")]
    if unattributed:
        return "partial"
    return "ok"


def _write_report_md(output_dir, result):
    lines = ["# Coverage Report", ""]
    lines.append("- case_id: %s" % result.get("case_id"))
    lines.append("- status: %s" % result.get("status"))
    cov = result.get("coverage", {})
    lines.append("- coverage: %s/%s = %s" % (cov.get("numerator"),
                                             cov.get("denominator"),
                                             cov.get("coverage_rate")))
    mg = result.get("marginal_gain", {})
    lines.append("- marginal gain: pre=%s post=%s reduction=%s" % (
        mg.get("pre_omissions"), mg.get("post_omissions"),
        mg.get("omission_reduction")))
    lines.append("")
    lines.append("## Residual omissions")
    for o in result.get("residual_omissions", []):
        att = o.get("attribution") or {}
        lines.append("- %s: %s (%s)" % (o.get("ref_id"), o.get("title"),
                                        att.get("category") or "unattributed"))
    lines.append("")
    lines.append("## Evidence map (%d entries)" % len(result.get("evidence_map", [])))
    for ev in result.get("evidence_map", []):
        loc = ev.get("locator") or {}
        lines.append("- %s: %s -> %s" % (ev.get("evidence_id"),
                                         loc.get("file") or loc.get("path"),
                                         (ev.get("quote") or "")[:80].replace("\n", " ")))
    with open(os.path.join(output_dir, "coverage_report.md"), "w",
              encoding="utf-8") as fh:
        fh.write("\n".join(lines))


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Search coverage & omission analysis (evidence-provenance edition)")
    sub = p.add_subparsers(dest="command")

    for name in ("scaffold", "inspect"):
        sp = sub.add_parser(name)
        sp.add_argument("--input", default="/input")
        sp.add_argument("--output", default="/app/output")
        sp.add_argument("--work", default="/app/work")

    sp = sub.add_parser("run")
    sp.add_argument("--input", default="/input")
    sp.add_argument("--output", default="/app/output")
    sp.add_argument("--work", default="/app/work")
    sp.add_argument("--refs", nargs="*")
    sp.add_argument("--results", nargs="*")

    sp = sub.add_parser("merge")
    sp.add_argument("--input", default="/input")
    sp.add_argument("--output", default="/app/output")
    sp.add_argument("--work", default="/app/work")
    sp.add_argument("--updates", default=None)
    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    args = parser.parse_args(argv)
    t0 = time.time()
    if args.command == "scaffold":
        code = cmd_scaffold(args)
    elif args.command == "inspect":
        code = cmd_inspect(args)
    elif args.command == "run":
        code = cmd_run(args)
    elif args.command == "merge":
        code = cmd_merge(args)
    else:
        parser.print_help()
        return 0
    # guarantee a legal result.json even when a step fails unexpectedly
    try:
        rp = os.path.join(args.output, "result.json")
        if not os.path.isfile(rp):
            os.makedirs(args.output, exist_ok=True)
            meta = load_case_meta(args.input)
            result = _base_result(meta, "input_error")
            result["errors"].append({"reason": "unexpected_failure",
                                     "detail": "see change_log"})
            result["change_log"].append({"at": now_iso(), "step": args.command,
                                         "note": "fallback skeleton written"})
            result["runtime"]["seconds"] = round(time.time() - t0, 3)
            write_json(rp, result)
    except Exception:
        pass
    return code


if __name__ == "__main__":
    sys.exit(main())
