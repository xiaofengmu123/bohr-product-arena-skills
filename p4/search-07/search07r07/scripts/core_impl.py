"""core_impl.py -- implementation of the search coverage analysis pipeline.

Standard library only. Designed for graceful degradation: every known failure
mode (empty input, bad JSON, bad encoding, missing params, oversized files,
unwritable output) is captured into errors/degradations and reported in a valid
result.json instead of crashing. All writes are atomic (temp file + os.replace)
so an interrupted run never leaves a half-written JSON.

Subcommands implemented here (dispatched by analyze.py):
  scaffold  -- write a minimal but valid result.json skeleton (status=partial)
  inspect   -- inventory /input, classify files, write work/inspect.json
  run       -- build frozen reference set, match search results, coverage
  merge     -- apply updates (adjudication/attribution/supplementary searches)
"""

import csv
import difflib
import fnmatch
import hashlib
import json
import os
import re
import sys
import time
import unicodedata

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_FILE_BYTES = 2 * 1024 * 1024  # skip files larger than 2 MiB
TITLE_FUZZY = 0.88                # title-only strong match threshold
TITLE_ADJUDICATION = 0.70         # below this -> miss; [0.70, 0.88) -> adjudicate
YEAR_TOLERANCE = 1

ID_KEYS = ("doi", "pmid", "pmcid", "pubmed_id", "arxiv", "arxiv_id",
           "scopus_id", "wos_id", "isbn", "issn", "eid", "pii", "id")
TITLE_KEYS = ("title", "article_title", "publication_title", "name", "headline")
AUTHOR_KEYS = ("authors", "author", "creators", "creator", "contributors")
YEAR_KEYS = ("year", "pub_year", "publication_year", "published_year",
             "pubdate", "date", "issued")
RETRACT_WORDS = ("retracted", "retraction", "withdrawn", "withdrawal")

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

REF_HINTS = ("reference", "ref", "gold", "standard", "known", "included",
             "inclusion", "guideline", "review", "seeds", "answer")
RESULT_HINTS = ("search", "query", "response", "result", "crossref",
                "europepmc", "publisher", "retrieval", "candidate", "scopus",
                "pubmed", "wos", "openalex")

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")


# ---------------------------------------------------------------------------
# Small safe I/O helpers
# ---------------------------------------------------------------------------

def _decode_bytes(data):
    """Decode bytes with BOM detection and a lossy last resort."""
    if not data:
        return "", "utf-8"
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    if data.startswith(b"\xff\xfe"):
        try:
            return data.decode("utf-16"), "utf-16-le"
        except UnicodeDecodeError:
            pass
    if data.startswith(b"\xfe\xff"):
        try:
            return data.decode("utf-16"), "utf-16-be"
        except UnicodeDecodeError:
            pass
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    # Latin-1 never fails; mark it so callers can flag the degradation.
    return data.decode("latin-1", errors="replace"), "latin-1(replace)"


def safe_read_text(path, max_bytes=MAX_FILE_BYTES):
    """Return (text_or_none, encoding_or_none, error_or_none)."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, None, "stat failed: %s" % exc
    if size > max_bytes:
        return None, None, "file too large (%d bytes > %d)" % (size, max_bytes)
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        return None, None, "read failed: %s" % exc
    if not data:
        return "", "utf-8", None
    text, enc = _decode_bytes(data)
    return text, enc, None


def safe_read_json(path, max_bytes=MAX_FILE_BYTES):
    """Return (obj_or_none, error_or_none, degradations list)."""
    text, enc, err = safe_read_text(path, max_bytes)
    if err is not None:
        return None, err, [err]
    degradations = []
    if enc and "replace" in enc:
        degradations.append("lossy decoding (%s) in %s" % (enc, path))
    if not text.strip():
        return None, "empty file: %s" % path, degradations
    try:
        obj = json.loads(text)
    except ValueError as exc:
        return None, "bad JSON: %s" % exc, degradations
    return obj, None, degradations


def ensure_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
        return None
    except OSError as exc:
        return "mkdir failed: %s" % exc


def write_json_atomic(path, obj):
    """Write JSON via temp file + os.replace; returns error or None."""
    parent = os.path.dirname(path)
    if parent:
        err = ensure_dir(parent)
        if err:
            return err
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except OSError as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return "write failed: %s" % exc
    return None


def write_text_atomic(path, text):
    parent = os.path.dirname(path)
    if parent:
        err = ensure_dir(parent)
        if err:
            return err
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8-sig") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except OSError as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return "write failed: %s" % exc
    return None


def write_csv_atomic(path, rows, fieldnames):
    parent = os.path.dirname(path)
    if parent:
        err = ensure_dir(parent)
        if err:
            return err
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        os.replace(tmp, path)
    except (OSError, ValueError) as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return "csv write failed: %s" % exc
    return None


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# Normalization / record extraction
# ---------------------------------------------------------------------------

def norm_key(value):
    """Lowercase, NFKC-fold, keep alphanumerics, collapse whitespace."""
    if value is None:
        return ""
    text = str(value)
    text = unicodedata.normalize("NFKC", text).lower()
    out = []
    for ch in text:
        out.append(ch if ch.isalnum() else " ")
    return re.sub(r"\s+", " ", "".join(out)).strip()


def norm_doi(value):
    text = str(value).strip().lower()
    text = re.sub(r"^https?://(dx\.)?doi\.org/", "", text)
    text = re.sub(r"^doi:\s*", "", text)
    return text.rstrip(".,;)")


def _parse_year(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"(19|20)\d{2}", str(value))
    return int(match.group(0)) if match else None


def extract_identifier(rec):
    for key in ID_KEYS:
        v = rec.get(key)
        if isinstance(v, str) and v.strip():
            return norm_doi(v) if key == "doi" else v.strip().lower()
    for key in ("url", "link", "uri"):
        v = rec.get(key)
        if isinstance(v, str):
            m = re.search(r"10\.\d{4,9}/[^\s\"'<>]+", v)
            if m:
                return norm_doi(m.group(0))
    return None


def extract_title(rec):
    for key in TITLE_KEYS:
        v = rec.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def extract_year(rec):
    for key in YEAR_KEYS:
        y = _parse_year(rec.get(key))
        if y:
            return y
    return None


def _author_str(v):
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        fam = v.get("family") or v.get("surname") or v.get("last_name")
        giv = v.get("given") or v.get("given_name") or v.get("first_name")
        name = v.get("name") or v.get("full_name")
        if fam and giv:
            return "%s %s" % (fam, giv)
        return name or fam or giv
    return None


def extract_first_author(rec):
    for key in AUTHOR_KEYS:
        if key not in rec:
            continue
        v = rec[key]
        if isinstance(v, str):
            return norm_key(v)
        if isinstance(v, dict):
            s = _author_str(v)
            return norm_key(s) if s else None
        if isinstance(v, list) and v:
            first = v[0]
            s = _author_str(first)
            if s:
                return norm_key(s)
    return None


def _looks_like_record(obj):
    if not isinstance(obj, dict):
        return False
    if extract_identifier(obj):
        return True
    title = extract_title(obj)
    if title:
        return extract_year(obj) is not None or extract_first_author(obj) is not None
    return False


def walk_records(obj, depth=0, out=None):
    if out is None:
        out = []
    if depth > 8 or obj is None:
        return out
    if isinstance(obj, dict):
        if _looks_like_record(obj):
            out.append(obj)
            return out
        for v in obj.values():
            walk_records(v, depth + 1, out)
    elif isinstance(obj, list):
        for v in obj:
            walk_records(v, depth + 1, out)
    return out


def title_similarity(a, b):
    na, nb = norm_key(a), norm_key(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def is_retracted(rec):
    for key in ("retracted", "retraction", "withdrawn"):
        v = rec.get(key)
        if v is True or (isinstance(v, str) and v.strip().lower() == "true"):
            return True
    status = str(rec.get("status", "")).lower()
    title = str(extract_title(rec) or "").lower()
    if any(w in status for w in RETRACT_WORDS):
        return True
    if title.startswith("retracted:") or title.startswith("withdrawn:"):
        return True
    return False


def locate_quote(text, target, window=90):
    """Find target in text, return verbatim context; else empty string."""
    if not text or not target:
        return ""
    idx = text.find(target)
    if idx < 0:
        idx = text.lower().find(str(target).lower())
    if idx < 0:
        return ""
    lo = max(0, idx - window)
    hi = min(len(text), idx + len(target) + window)
    return text[lo:hi]


# ---------------------------------------------------------------------------
# Scaffold: minimal valid result.json
# ---------------------------------------------------------------------------

def _content_hash(text):
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:12]


def extract_case_id(case_obj, case_path, task_text):
    for key in ("case_id", "id", "challenge_id"):
        v = case_obj.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    if case_obj.get("challengeId"):
        return str(case_obj["challengeId"]).strip()
    if case_obj.get("armiId"):
        return str(case_obj["armiId"]).strip()
    if task_text:
        m = re.search(r"challenge_id[:\s]*`?([A-Za-z0-9\-_.]+)`?", task_text)
        if m:
            return m.group(1)
    combined = (case_path or "") + (task_text or "")
    return "hash-" + _content_hash(combined) if combined else "unknown"


def build_scaffold(input_dir, output_dir, work_dir, logs):
    """Return (result_dict, case_obj, case_contract, task_text, errors, degrads)."""
    errors, degradations = [], []
    result = {
        "case_id": "unknown",
        "status": "partial",
        "research_question": None,
        "search_scope": None,
        "reference_set": {"version": None, "frozen_at": None, "records": []},
        "search_sessions": [],
        "coverage": {"denominator": 0, "numerator": 0, "coverage_rate": None,
                     "omission_rate": None, "hit_rule": None},
        "hit_matrix": [],
        "omissions": [],
        "supplementary_searches": [],
        "marginal_gain": None,
        "residual_omissions": [],
        "evidence_map": {},
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "limitations": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "runtime": {"started": now_iso(), "finished": None, "seconds": None},
    }

    case_path = None
    case_obj = {}
    case_contract = None
    task_text = ""

    for name in ("case.json", "task.md"):
        p = os.path.join(input_dir, name)
        if os.path.isfile(p):
            if name == "case.json":
                case_path = p
                obj, err, deg = safe_read_json(p)
                degradations.extend(deg)
                if err:
                    errors.append("case.json: %s" % err)
                elif isinstance(obj, dict):
                    case_obj = obj
                    case_contract = obj.get("output_contract")
            else:
                txt, _, err = safe_read_text(p)
                if err:
                    errors.append("task.md: %s" % err)
                else:
                    task_text = txt or ""

    result["case_id"] = extract_case_id(case_obj, case_path, task_text)
    if isinstance(case_contract, dict):
        for k in case_contract:
            result.setdefault(k, None)
    result["change_log"].append({
        "at": now_iso(),
        "action": "scaffold",
        "detail": "落盘最小合法结果骨架（status=partial）",
    })
    result["errors"] = errors
    result["degradations"] = degradations
    return result, case_obj, case_contract, task_text, errors, degradations


# ---------------------------------------------------------------------------
# Inspect
# ---------------------------------------------------------------------------

def inspect(input_dir, work_dir):
    files = []
    errors, degradations = [], []
    try:
        names = sorted(os.listdir(input_dir))
    except OSError as exc:
        names = []
        errors.append("listdir failed: %s" % exc)

    for name in names:
        p = os.path.join(input_dir, name)
        if not os.path.isfile(p):
            continue
        entry = {"name": name, "path": p, "size": None, "kind": "unknown",
                 "encoding": None, "records": 0, "error": None, "degraded": False}
        try:
            entry["size"] = os.path.getsize(p)
        except OSError as exc:
            entry["error"] = "stat failed: %s" % exc
            files.append(entry)
            continue

        low = name.lower()
        if low.endswith((".json", ".jsonl")):
            obj, err, deg = safe_read_json(p)
            if deg:
                entry["degraded"] = True
                degradations.append("%s: %s" % (name, "; ".join(deg)))
            if err:
                entry["error"] = err
                errors.append("%s: %s" % (name, err))
            else:
                entry["kind"] = "json"
                entry["records"] = len(walk_records(obj))
                if isinstance(obj, dict):
                    entry["encoding"] = "json-object"
        elif low.endswith((".txt", ".md", ".csv", ".tsv")):
            text, enc, err = safe_read_text(p)
            if err:
                entry["error"] = err
                errors.append("%s: %s" % (name, err))
            else:
                entry["kind"] = "text"
                entry["encoding"] = enc
                entry["records"] = _csv_like_rows(text)
        elif low.endswith((".pdf",)):
            entry["kind"] = "pdf"
        else:
            entry["kind"] = "binary-or-other"

        hint_low = low
        if any(h in hint_low for h in REF_HINTS):
            entry["hint"] = "reference"
        elif any(h in hint_low for h in RESULT_HINTS):
            entry["hint"] = "results"
        else:
            entry["hint"] = "unknown"
        files.append(entry)

    report = {
        "input_dir": input_dir,
        "file_count": len(files),
        "files": files,
        "errors": errors,
        "degradations": degradations,
    }
    write_json_atomic(os.path.join(work_dir, "inspect.json"), report)
    return report


def _csv_like_rows(text):
    try:
        reader = csv.reader(text.splitlines())
        rows = [r for r in reader if r]
        return len(rows) - 1 if rows else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Build reference set
# ---------------------------------------------------------------------------

def _classify_file(entry, refs_patterns=None, results_patterns=None):
    name = entry.get("name", "")
    if refs_patterns and any(fnmatch.fnmatch(name, pat) for pat in refs_patterns):
        return "reference"
    if results_patterns and any(fnmatch.fnmatch(name, pat)
                                for pat in results_patterns):
        return "results"
    hint = entry.get("hint", "unknown")
    if hint in ("reference", "results"):
        return hint
    return "unknown"


def collect_records(input_dir, inspect_report, refs_patterns=None,
                    results_patterns=None):
    """Return (ref_pairs, result_records, per_source_meta).

    ref_pairs is a list of (record_dict, source_file_name) so each reference
    record can be traced back to the exact source file it came from.
    """
    ref_pairs, result_records, meta = [], [], []
    for entry in inspect_report.get("files", []):
        p = entry["path"]
        if entry.get("kind") != "json":
            continue
        obj, err, deg = safe_read_json(p)
        if err or obj is None:
            continue
        recs = walk_records(obj)
        cls = _classify_file(entry, refs_patterns, results_patterns)
        if cls == "reference":
            ref_pairs.extend((r, entry["name"]) for r in recs)
        elif cls == "results":
            result_records.extend(recs)
        else:
            # Unknown: put in results by default but keep for human override.
            result_records.extend(recs)
        meta.append({"file": entry["name"], "kind": cls, "records": len(recs)})
    return ref_pairs, result_records, meta


def _record_key(rec):
    ident = extract_identifier(rec)
    if ident:
        return "id:" + ident
    title = norm_key(extract_title(rec))
    if title:
        return "title:" + title
    return "anon:" + hashlib.sha1(
        json.dumps(rec, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:10]


def canonical_record(rec, source_file):
    ident = extract_identifier(rec)
    return {
        "ref_id": None,
        "identifier": ident,
        "title": extract_title(rec),
        "title_norm": norm_key(extract_title(rec)),
        "first_author": extract_first_author(rec),
        "year": extract_year(rec),
        "source_file": source_file,
        "raw": rec,
        "flags": [],
    }


def build_reference_set(recs, source_files):
    """Dedupe, flag, freeze. Returns (reference_set_dict, raw_records list)."""
    seen_ids, seen_titles = {}, {}
    canonical = []
    for rec, src in recs:
        item = canonical_record(rec, src)
        ident = item["identifier"]
        title_n = item["title_norm"]
        dup_of = None
        if ident and ident in seen_ids:
            dup_of = seen_ids[ident]
            item["flags"].append("duplicate_of:" + dup_of)
        elif title_n and title_n in seen_titles:
            dup_of = seen_titles[title_n]
            item["flags"].append("duplicate_of:" + dup_of)
        else:
            rid = "r%03d" % (len(canonical) + 1)
            item["ref_id"] = rid
            if ident:
                seen_ids[ident] = rid
            if title_n:
                seen_titles[title_n] = rid
            if not ident:
                item["flags"].append("no_identifier")
            if is_retracted(rec):
                item["flags"].append("retracted_or_unavailable")
        item.pop("raw", None)
        canonical.append(item)

    # Identifier conflict: same normalized id pointing to different titles.
    id_to_title = {}
    for item in canonical:
        if item["identifier"] and item["ref_id"]:
            id_to_title.setdefault(item["identifier"], set()).add(item["title_norm"])
    for item in canonical:
        if item["identifier"] and item["ref_id"]:
            titles = id_to_title[item["identifier"]]
            if len(titles) > 1:
                if "source_conflict" not in item["flags"]:
                    item["flags"].append("source_conflict")

    canonical.sort(key=lambda r: (r["ref_id"] or "z", r["title_norm"] or ""))
    content = json.dumps(
        [{"id": r["ref_id"], "title": r["title_norm"], "idn": r["identifier"]}
         for r in canonical], sort_keys=True)
    version = hashlib.sha1(content.encode("utf-8")).hexdigest()[:12]
    return {
        "version": version,
        "frozen_at": now_iso(),
        "records": canonical,
        "source_files": sorted(set(source_files)),
    }


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def match_record(ref, results):
    """Return (status, matched_by, candidate)."""
    ref_idn = ref.get("identifier")
    best_title = None
    best_score = 0.0
    best_cand = None
    for cand in results:
        cidn = extract_identifier(cand)
        if ref_idn and cidn and ref_idn == cidn:
            return "hit", "identifier", cand
        ctitle = extract_title(cand)
        if not ctitle:
            continue
        sc = title_similarity(ref.get("title") or "", ctitle)
        if sc > best_score:
            best_score = sc
            best_title = ctitle
            best_cand = cand

    if best_cand is None:
        return "miss", None, None

    meta_ok = False
    if ref.get("year") is not None and extract_year(best_cand) is not None:
        if abs(ref["year"] - extract_year(best_cand)) <= YEAR_TOLERANCE:
            meta_ok = True
    fa_ref = ref.get("first_author")
    fa_cand = extract_first_author(best_cand)
    if fa_ref and fa_cand and fa_ref == fa_cand:
        meta_ok = True

    if best_score >= TITLE_FUZZY and meta_ok:
        return "hit", "title+meta", best_cand
    if best_score >= TITLE_ADJUDICATION:
        return "adjudicate", "title-fuzzy", best_cand
    return "miss", None, best_cand


def run_match(ref_set, result_records):
    hit_matrix = []
    omissions = []
    numerator = 0
    for ref in ref_set["records"]:
        if not ref.get("ref_id"):
            continue
        status, matched_by, cand = match_record(ref, result_records)
        row = {
            "ref_id": ref["ref_id"],
            "title": ref.get("title") or "",
            "identifier": ref.get("identifier") or "",
            "status": status,
            "matched_by": matched_by or "",
            "candidate_title": extract_title(cand) if cand else "",
            "candidate_identifier": extract_identifier(cand) if cand else "",
        }
        hit_matrix.append(row)
        if status == "hit":
            numerator += 1
        else:
            omissions.append({
                "ref_id": ref["ref_id"],
                "title": ref.get("title") or "",
                "identifier": ref.get("identifier") or "",
                "first_author": ref.get("first_author"),
                "year": ref.get("year"),
                "status": status,
                "best_candidate": {
                    "title": extract_title(cand) if cand else None,
                    "score": (title_similarity(ref.get("title") or "",
                                extract_title(cand) or "")
                              if cand and extract_title(cand) else None),
                },
                "category": None,
                "signals": [],
                "evidence": [],
            })

    denom = sum(1 for r in ref_set["records"] if r.get("ref_id"))
    coverage = {
        "denominator": denom,
        "numerator": numerator,
        "coverage_rate": round(numerator / denom, 4) if denom else None,
        "omission_rate": round((denom - numerator) / denom, 4) if denom else None,
        "hit_rule": ("identifier exact -> title+first_author+year fuzzy(>=%.2f) "
                     "-> adjudicate(>=%.2f) -> miss; year_tolerance=%d"
                     % (TITLE_FUZZY, TITLE_ADJUDICATION, YEAR_TOLERANCE)),
    }
    return hit_matrix, omissions, coverage


# ---------------------------------------------------------------------------
# Merge updates
# ---------------------------------------------------------------------------

def merge_updates(result, updates):
    """Apply adjudications, attributions, supplementary searches. Mutates result."""
    if not isinstance(updates, dict):
        return ["updates is not a dict"]

    hit_matrix = {r["ref_id"]: r for r in result["hit_matrix"]}
    for adj in updates.get("adjudications", []):
        rid = adj.get("ref_id")
        if rid in hit_matrix:
            hit_matrix[rid]["status"] = adj.get("status",
                                                hit_matrix[rid]["status"])
            hit_matrix[rid]["matched_by"] = adj.get("matched_by",
                                                    hit_matrix[rid]["matched_by"])
            if adj.get("candidate_title"):
                hit_matrix[rid]["candidate_title"] = adj["candidate_title"]
            if adj.get("candidate_identifier"):
                hit_matrix[rid]["candidate_identifier"] = adj["candidate_identifier"]
            if adj.get("note"):
                hit_matrix[rid]["note"] = adj["note"]

    # Recompute numerator from adjudicated matrix.
    result["coverage"]["numerator"] = sum(
        1 for r in result["hit_matrix"] if r["status"] == "hit")
    denom = result["coverage"]["denominator"]
    result["coverage"]["coverage_rate"] = round(
        result["coverage"]["numerator"] / denom, 4) if denom else None
    result["coverage"]["omission_rate"] = round(
        (denom - result["coverage"]["numerator"]) / denom, 4) if denom else None

    # Attribution: fill omissions.
    attr_map = {a.get("ref_id"): a for a in updates.get("attributions", [])}
    for om in result["omissions"]:
        a = attr_map.get(om["ref_id"])
        if a:
            om["category"] = a.get("category")
            om["signals"] = a.get("signals", [])
            om["evidence"] = a.get("evidence", [])

    # Supplementary searches: count new hits among previously-missed refs.
    before = len(result["omissions"])
    supp = updates.get("supplementary_searches", [])
    new_hits = []
    for s in supp:
        for rid in s.get("new_hits", []):
            if any(om["ref_id"] == rid for om in result["omissions"]):
                new_hits.append(rid)
    unique_new = sorted(set(new_hits))
    result["supplementary_searches"] = supp
    result["marginal_gain"] = {
        "omissions_before": before,
        "new_hits": len(unique_new),
        "new_hit_ref_ids": unique_new,
        "gain_rate": round(len(unique_new) / before, 4) if before else None,
    }
    result["residual_omissions"] = updates.get("residual_omissions", [])
    for om in result["omissions"]:
        if om["ref_id"] in unique_new:
            om["resolved_by_supplementary"] = True
    return []


# ---------------------------------------------------------------------------
# Subcommand entry points
# ---------------------------------------------------------------------------

def cmd_scaffold(input_dir, output_dir, work_dir):
    logs = []
    result, _case_obj, _contract, _task, errors, degrads = build_scaffold(
        input_dir, output_dir, work_dir, logs)
    err = write_json_atomic(os.path.join(output_dir, "result.json"), result)
    if err:
        result["errors"].append(err)
        write_json_atomic(os.path.join(output_dir, "result.json"), result)
    return result, errors, degrads


def cmd_inspect(input_dir, output_dir, work_dir):
    report = inspect(input_dir, work_dir)
    result, _c, _ct, _t, errors, degrads = build_scaffold(input_dir, output_dir,
                                                          work_dir, [])
    result["sources"] = [{"file": f["name"], "kind": f["kind"],
                          "hint": f.get("hint", "unknown"),
                          "records": f.get("records")} for f in report["files"]]
    result["errors"].extend(report["errors"])
    result["degradations"].extend(report["degradations"])
    result["change_log"].append({"at": now_iso(), "action": "inspect",
                                 "detail": "盘点 /input 共 %d 个文件" % report["file_count"]})
    write_json_atomic(os.path.join(output_dir, "result.json"), result)
    return report, result


def cmd_run(input_dir, output_dir, work_dir, refs_glob=None, results_glob=None):
    report = inspect(input_dir, work_dir)
    result, _c, _ct, _t, errors, degrads = build_scaffold(input_dir, output_dir,
                                                          work_dir, [])
    errors = list(result["errors"])
    degrads = list(result["degradations"])

    refs_patterns = _split_patterns(refs_glob)
    results_patterns = _split_patterns(results_glob)

    if report["file_count"] == 0:
        result["status"] = "insufficient_evidence"
        result["limitations"].append("输入目录为空：无材料可构建参考集或检索结果。")
        result["change_log"].append({"at": now_iso(), "action": "run",
                                     "detail": "空输入目录，标记 insufficient_evidence"})
        write_json_atomic(os.path.join(output_dir, "result.json"), result)
        return result

    ref_pairs, result_records, meta = collect_records(input_dir, report,
                                                      refs_patterns,
                                                      results_patterns)
    if not ref_pairs:
        result["status"] = "insufficient_evidence"
        result["limitations"].append(
            "未能从材料中识别出参考集记录（无 hint=reference 的 JSON 源）。"
            "请用 --refs 指定参考集文件，或用 --updates 补充归因。")
        result["change_log"].append({"at": now_iso(), "action": "run",
                                     "detail": "未识别到参考集，标记 insufficient_evidence"})
        write_json_atomic(os.path.join(output_dir, "result.json"), result)
        return result

    src_files = sorted({m["file"] for m in meta})
    ref_set = build_reference_set(ref_pairs, src_files)

    hit_matrix, omissions, coverage = run_match(ref_set, result_records)

    result["reference_set"] = ref_set
    result["hit_matrix"] = hit_matrix
    result["omissions"] = omissions
    result["coverage"] = coverage
    result["sources"] = [{"file": m["file"], "kind": m["kind"],
                          "records": m["records"]} for m in meta]
    result["status"] = "partial"
    result["change_log"].append({"at": now_iso(), "action": "run",
                                 "detail": "构建冻结参考集（版本 %s，%d 条）并完成命中判定"
                                 % (ref_set["version"], coverage["denominator"])})

    write_json_atomic(os.path.join(work_dir, "reference_set.json"), ref_set)
    write_json_atomic(os.path.join(work_dir, "coverage_core.json"),
                      {"coverage": coverage, "omissions": omissions})
    fieldnames = ["ref_id", "title", "identifier", "status", "matched_by",
                  "candidate_title", "candidate_identifier"]
    write_csv_atomic(os.path.join(output_dir, "hit_matrix.csv"), hit_matrix, fieldnames)

    result["errors"] = errors
    result["degradations"] = degrads
    write_json_atomic(os.path.join(output_dir, "result.json"), result)
    return result


def _split_patterns(glob_str):
    if not glob_str:
        return None
    parts = [p.strip() for p in str(glob_str).split(",") if p.strip()]
    return parts or None


def _records_of_file(input_dir, name):
    p = os.path.join(input_dir, name)
    obj, err, _deg = safe_read_json(p)
    if err or obj is None:
        return []
    return walk_records(obj)


def cmd_merge(output_dir, work_dir, updates_path):
    result_path = os.path.join(output_dir, "result.json")
    obj, err, deg = safe_read_json(result_path)
    if err:
        return {"status": "input_error", "errors": ["result.json: %s" % err]}
    updates, uerr, udeg = safe_read_json(updates_path)
    if uerr:
        obj["errors"].append("updates: %s" % uerr)
        obj["change_log"].append({"at": now_iso(), "action": "merge",
                                  "detail": "updates 文件不可读：%s" % uerr})
        write_json_atomic(result_path, obj)
        return obj
    msgs = merge_updates(obj, updates)
    obj["errors"].extend(msgs)
    obj["change_log"].append({"at": now_iso(), "action": "merge",
                              "detail": "应用裁决/归因/补检更新"})
    write_json_atomic(result_path, obj)
    return obj
