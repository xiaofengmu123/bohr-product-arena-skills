#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py -- deterministic coverage/omission analysis pipeline.

Stdlib only. Design goal (variant focus): *self-check and determinism*.
- Same /input directory + same updates file => byte-identical result.json.
- No wall-clock timestamps, no random IDs, no dependence on file order.
- Every key conclusion gets a locator + verbatim quote via the updates file.

Subcommands: scaffold | inspect | run | merge | selftest
Run `python scripts/main.py <subcommand> --help` for options.
"""
import argparse
import csv
import difflib
import glob
import hashlib
import io
import json
import os
import re
import sys
import unicodedata

# ---------------------------------------------------------------------------
# Frozen method thresholds (keep in sync with references/coverage-method.md)
# ---------------------------------------------------------------------------
TITLE_FUZZY = 0.88          # >= this + year + first-author match => auto hit
TITLE_ADJUDICATION = 0.70   # >= this but below TITLE_FUZZY => human adjudicate
YEAR_TOLERANCE = 1          # |ref_year - result_year| <= this counts as match
MAX_FILE_BYTES = 2 * 1024 * 1024   # files larger than this are skipped
MIN_IDENTIFIER_LEN = 4      # identifiers shorter than this are not treated as matches

BUDGET_SECONDS = 900
MODEL_REQUEST_BUDGET = 64
MAX_RESPONSE_TOKENS = 16384

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

# Top-level keys owned by the result schema (contract keys never overwrite these).
CORE_KEYS = {
    "case_id", "status", "research_question", "search_scope", "reference_set",
    "search_sessions", "coverage", "hit_matrix", "omissions",
    "supplementary_searches", "marginal_gain", "residual_omissions",
    "evidence_map", "sources", "uncertainty", "change_log", "limitations",
    "pending_human", "errors", "degradations", "runtime", "output_contract",
    "_output_contract_schema",
}

LIST_HINT = (
    "list", "array", "items", "records", "entries", "results", "candidates",
    "hits", "omissions", "sessions", "sources", "errors", "degradations",
    "evidence", "uncertainty", "limitations", "pending", "supplement",
    "matrix", "map", "papers", "documents", "publications",
)

RECORD_LIST_KEYS = (
    "references", "reference_set", "records", "items", "entries", "results",
    "documents", "hits", "papers", "articles", "publications", "candidates",
)

IDENTIFIER_KEYS = (
    "doi", "pmid", "pmcid", "arxiv_id", "arxiv", "isbn", "pii", "accession",
    "identifier", "id", "external_id",
)

TITLE_KEYS = ("title", "Title", "article_title", "paper_title", "name", "headline")

AUTHOR_KEYS = ("authors", "author", "creators", "creator", "contributors")

RETRACTION_HINT = ("retract", "withdrawn", "withdrawal", "expression of concern")


# ---------------------------------------------------------------------------
# Robust IO helpers
# ---------------------------------------------------------------------------
def read_text_robust(path):
    """Read a UTF-8 text file tolerating BOM and encoding errors."""
    data = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc), None
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "undecodable-encoding"


def load_json_robust(path):
    """Load JSON; return (obj, error_string_or_None)."""
    text, read_err = read_text_robust(path)
    if read_err:
        return None, read_err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, "bad-json:%s:%s" % (exc.lineno, exc.msg)
    except Exception as exc:  # pragma: no cover - defensive
        return None, "json-error:%s" % exc


def write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def write_text(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="\n") as fh:
        fh.write(text)


def norm_identifier(value):
    if value is None:
        return None
    t = str(value).strip().lower()
    t = t.rstrip(".,;")
    t = re.sub(r"^https?://(dx\.)?doi\.org/", "", t)
    t = re.sub(r"^https?://", "", t)
    t = t.strip()
    return t or None


def norm_text(value):
    if value is None:
        return ""
    s = unicodedata.normalize("NFKC", str(value))
    s = s.lower()
    s = re.sub(r"[^\w\u4e00-\u9fff]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _to_int(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1000 <= value <= 2100 else None
    if isinstance(value, float):
        return int(value) if value.is_integer() and 1000 <= value <= 2100 else None
    if isinstance(value, str):
        m = re.search(r"(19|20)\d{2}", value)
        return int(m.group()) if m else None
    return None


def _year_from_value(value):
    if isinstance(value, dict):
        for key in ("year", "date-parts", "date_parts"):
            if key in value:
                if key in ("date-parts", "date_parts"):
                    try:
                        return int(value[key][0][0])
                    except (IndexError, TypeError, ValueError):
                        continue
                y = _to_int(value[key])
                if y is not None:
                    return y
        return None
    if isinstance(value, list):
        for item in value:
            y = _year_from_value(item)
            if y is not None:
                return y
        return None
    return _to_int(value)


def get_year(raw):
    for key in ("year", "pub_year", "publication_year", "published_year"):
        y = _to_int(raw.get(key))
        if y is not None:
            return y
    for key in ("date", "published", "publication_date", "issued", "pub_date",
                "created", "date_parts"):
        y = _year_from_value(raw.get(key))
        if y is not None:
            return y
    return None


def collect_identifiers(raw):
    found = []
    for key in IDENTIFIER_KEYS:
        value = raw.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, list):
            for item in value:
                n = norm_identifier(item)
                if n:
                    found.append(n)
        else:
            n = norm_identifier(value)
            if n:
                found.append(n)
    unique = []
    seen = set()
    for n in found:
        if n not in seen:
            seen.add(n)
            unique.append(n)
    return sorted(unique)


def get_title(raw):
    for key in TITLE_KEYS:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def get_authors(raw):
    for key in AUTHOR_KEYS:
        value = raw.get(key)
        if value:
            if isinstance(value, list):
                return value
            if isinstance(value, str):
                return [value]
    return None


def first_author_last(authors):
    if not authors:
        return None
    a = authors[0]
    if isinstance(a, dict):
        last = (a.get("family") or a.get("last") or a.get("lastname")
                or a.get("surname") or a.get("name"))
        if last:
            return norm_text(last) or None
        return None
    if isinstance(a, str):
        s = a.strip()
        if "," in s:
            last = s.split(",", 1)[0]
        else:
            parts = s.split()
            last = parts[-1] if parts else s
        return norm_text(last) or None
    return None


def has_retraction_marker(raw):
    for key, value in raw.items():
        if isinstance(value, str) and any(h in value.lower() for h in RETRACTION_HINT):
            return True
    status = str(raw.get("status", "")).lower()
    availability = str(raw.get("availability", "")).lower()
    return any(h in status or h in availability for h in RETRACTION_HINT)


def normalize_record(raw, source, index):
    """Reduce a heterogeneous raw record to a canonical, sortable dict."""
    if not isinstance(raw, dict):
        raw = {"raw_value": raw}
    title = get_title(raw)
    authors = get_authors(raw)
    year = get_year(raw)
    identifiers = collect_identifiers(raw)
    rec = {
        "raw_index": index,
        "source": source,
        "title": title,
        "title_norm": norm_text(title) if title else "",
        "authors": authors,
        "first_author_last": first_author_last(authors),
        "year": year,
        "identifiers": identifiers,
        "retracted": has_retraction_marker(raw),
        "raw": raw,
    }
    return rec


def extract_records(obj):
    """Find the list of records inside a JSON object/list."""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for key in RECORD_LIST_KEYS:
            if isinstance(obj.get(key), list):
                return obj[key]
        for key, value in obj.items():
            if isinstance(value, list) and value and all(isinstance(x, dict) for x in value):
                return value
    return None


def dedup_records(records):
    """Return unique records (first occurrence wins) plus a duplicates report."""
    unique = []
    seen = set()
    dups = []
    for rec in records:
        key_parts = rec["identifiers"][:1]
        if key_parts:
            key = "id:" + key_parts[0]
        elif rec["title_norm"]:
            key = "t:%s|%s|%s" % (rec["title_norm"], rec["year"] or "", rec["first_author_last"] or "")
        else:
            key = "raw:%d:%s" % (rec["raw_index"], rec["source"])
        if key in seen:
            dups.append({"key": key, "raw_index": rec["raw_index"], "source": rec["source"]})
        else:
            seen.add(key)
            unique.append(rec)
    return unique, dups


def sort_key(rec):
    return (rec.get("title_norm") or "", tuple(rec.get("identifiers") or []),
            rec.get("first_author_last") or "", rec.get("year") or 0,
            rec.get("source") or "", rec.get("raw_index") or 0)


def stable_sort(records):
    return sorted(records, key=sort_key)


# ---------------------------------------------------------------------------
# Scaffold
# ---------------------------------------------------------------------------
def find_case_json(input_dir):
    case_path = os.path.join(input_dir, "case.json")
    if os.path.isfile(case_path):
        return case_path
    candidates = []
    for path in glob.glob(os.path.join(input_dir, "**", "*.json"), recursive=True):
        obj, err = load_json_robust(path)
        if err is None and isinstance(obj, dict):
            if any(k in obj for k in ("case_id", "id", "challenge_id", "output_contract")):
                candidates.append(path)
    return candidates[0] if candidates else None


def derive_case_id(case_obj, input_dir):
    for key in ("case_id", "id", "challenge_id", "armiId"):
        value = case_obj.get(key) if isinstance(case_obj, dict) else None
        if value not in (None, ""):
            return str(value)
    task_path = os.path.join(input_dir, "task.md")
    if os.path.isfile(task_path):
        digest = hashlib.sha256(open(task_path, "rb").read()).hexdigest()[:12]
        return "hash-" + digest
    return "unknown"


def is_list_like(key):
    k = str(key).lower()
    return any(h in k for h in LIST_HINT)


def empty_runtime():
    return {
        "budget_seconds": BUDGET_SECONDS,
        "model_request_budget": MODEL_REQUEST_BUDGET,
        "max_response_tokens": MAX_RESPONSE_TOKENS,
        "deterministic": True,
        "note": "wall-clock duration omitted so same-input reruns stay byte-identical",
    }


def base_result(case_id, contract_keys, status="partial"):
    result = {}
    result["case_id"] = case_id
    result["status"] = status
    # Echo the contract keys verbatim (unknown keys preserved, values null/[]).
    contract = {k: ([] if is_list_like(k) else None) for k in sorted(contract_keys)}
    result["output_contract"] = contract
    # Flatten non-colliding contract keys to the top level for harness visibility.
    for k in sorted(contract_keys):
        if k not in CORE_KEYS and k not in result:
            result[k] = contract[k]
    # Core closure fields.
    result["research_question"] = None
    result["search_scope"] = None
    result["reference_set"] = {
        "version": None,
        "total": 0,
        "eligible": 0,
        "invalid": 0,
        "records": [],
    }
    result["search_sessions"] = []
    result["coverage"] = {
        "denominator": None,
        "numerator": None,
        "coverage_rate": None,
        "omission_rate": None,
        "hit_rule": (
            "identifier exact -> title+first-author+year (title_similarity>="
            + str(TITLE_FUZZY) + ") -> adjudicate (>=" + str(TITLE_ADJUDICATION) + ")"
        ),
    }
    result["hit_matrix"] = []
    result["omissions"] = []
    result["supplementary_searches"] = []
    result["marginal_gain"] = {
        "before": None, "new_hits": None, "after": None, "rate": None,
    }
    result["residual_omissions"] = []
    result["evidence_map"] = []
    result["sources"] = []
    result["uncertainty"] = []
    result["change_log"] = []
    result["limitations"] = []
    result["pending_human"] = []
    result["errors"] = []
    result["degradations"] = []
    result["runtime"] = empty_runtime()
    return result


def cmd_scaffold(args):
    input_dir = args.input or DEFAULT_INPUT
    output_dir = args.output or DEFAULT_OUTPUT
    work_dir = args.work or DEFAULT_WORK
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    errors = []
    case_path = find_case_json(input_dir)
    case_obj = None
    contract_keys = []
    if case_path is None:
        status = "input_error"
        errors.append("no case.json found under %s" % input_dir)
    else:
        case_obj, err = load_json_robust(case_path)
        if err:
            status = "input_error"
            errors.append("case.json unreadable: %s" % err)
        elif not isinstance(case_obj, dict):
            status = "input_error"
            errors.append("case.json is not a JSON object")
        else:
            status = "partial"
            contract = case_obj.get("output_contract")
            if isinstance(contract, dict):
                contract_keys = list(contract.keys())
            elif contract not in (None, ""):
                errors.append("output_contract is not a dict; echoed as-is")

    case_id = derive_case_id(case_obj, input_dir)
    result = base_result(case_id, contract_keys, status=status)
    result["_output_contract_schema"] = (case_obj or {}).get("output_contract", None)
    result["errors"] = errors
    result["change_log"] = [{
        "step": "scaffold",
        "status": status,
        "summary": "wrote partial result skeleton with contract keys backfilled",
    }]
    write_json(os.path.join(output_dir, "result.json"), result)
    write_json(os.path.join(work_dir, "scaffold.json"),
               {"case_id": case_id, "case_path": case_path, "contract_keys": contract_keys})
    print("scaffold ok; status=%s case_id=%s" % (status, case_id))
    return 0


# ---------------------------------------------------------------------------
# Inspect
# ---------------------------------------------------------------------------
def cmd_inspect(args):
    input_dir = args.input or DEFAULT_INPUT
    output_dir = args.output or DEFAULT_OUTPUT
    work_dir = args.work or DEFAULT_WORK
    os.makedirs(work_dir, exist_ok=True)
    files = []
    errors = []
    for root, _dirs, names in os.walk(input_dir):
        for name in names:
            path = os.path.join(root, name)
            rel = os.path.relpath(path, input_dir).replace("\\", "/")
            try:
                size = os.path.getsize(path)
            except OSError as exc:
                errors.append("%s: %s" % (rel, exc))
                continue
            if size > MAX_FILE_BYTES:
                files.append({"path": rel, "size_bytes": size, "kind": "skipped",
                              "note": "larger than 2MB"})
                continue
            ext = os.path.splitext(name)[1].lower().lstrip(".")
            kind = {"json": "json", "jsonl": "jsonl", "csv": "csv",
                    "md": "text", "txt": "text", "yaml": "text", "yml": "text",
                    "pdf": "pdf"}.get(ext, "other")
            entry = {"path": rel, "size_bytes": size, "kind": kind, "records": None,
                     "error": None}
            if kind == "json":
                obj, err = load_json_robust(path)
                if err:
                    entry["error"] = err
                    errors.append("%s: %s" % (rel, err))
                else:
                    recs = extract_records(obj)
                    entry["records"] = len(recs) if recs is not None else None
            elif kind == "jsonl":
                count, err = count_jsonl(path)
                entry["records"] = count
                entry["error"] = err
                if err:
                    errors.append("%s: %s" % (rel, err))
            elif kind == "csv":
                count, err = count_csv(path)
                entry["records"] = count
                entry["error"] = err
                if err:
                    errors.append("%s: %s" % (rel, err))
            files.append(entry)
    files.sort(key=lambda f: f["path"])
    inspect = {"input_dir": input_dir, "files": files, "errors": sorted(set(errors)),
               "hint": "identify which file is the frozen reference set and which are search/query responses"}
    write_json(os.path.join(work_dir, "inspect.json"), inspect)
    print("inspect ok; %d files, %d errors" % (len(files), len(inspect["errors"])))
    return 0


def count_jsonl(path):
    count = 0
    err = None
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    count += 1
    except Exception as exc:
        err = "jsonl-error:%s" % exc
    return count, err


def count_csv(path):
    count = 0
    err = None
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            for _row in reader:
                count += 1
    except Exception as exc:
        err = "csv-error:%s" % exc
    return count, err


# ---------------------------------------------------------------------------
# Run: build frozen reference set + hit matrix + coverage core
# ---------------------------------------------------------------------------
def load_records_from_spec(spec, default_patterns, input_dir):
    """Resolve --refs/--results (file or glob) into a list of raw records."""
    paths = []
    if spec:
        for token in spec.split(","):
            token = token.strip()
            if not token:
                continue
            if os.path.isabs(token):
                paths.extend(glob.glob(token))
            else:
                paths.extend(glob.glob(os.path.join(input_dir, token)))
    else:
        for pat in default_patterns:
            paths.extend(glob.glob(os.path.join(input_dir, pat)))
            paths.extend(glob.glob(os.path.join(input_dir, "**", pat), recursive=True))
    paths = sorted(set(p for p in paths if os.path.isfile(p)))
    records = []
    load_errors = []
    for path in paths:
        rel = os.path.relpath(path, input_dir).replace("\\", "/")
        ext = os.path.splitext(path)[1].lower()
        if ext == ".json":
            obj, err = load_json_robust(path)
            if err:
                load_errors.append("%s: %s" % (rel, err))
                continue
            recs = extract_records(obj)
            if recs is None:
                load_errors.append("%s: no record list found" % rel)
                continue
        elif ext == ".jsonl":
            recs, err = load_jsonl_records(path)
            if err:
                load_errors.append("%s: %s" % (rel, err))
                continue
        elif ext == ".csv":
            recs, err = load_csv_records(path)
            if err:
                load_errors.append("%s: %s" % (rel, err))
                continue
        else:
            load_errors.append("%s: unsupported format" % rel)
            continue
        for i, raw in enumerate(recs):
            records.append(normalize_record(raw, rel, i))
    return records, load_errors, [os.path.relpath(p, input_dir).replace("\\", "/") for p in paths]


def load_jsonl_records(path):
    recs = []
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    recs.append(json.loads(line))
    except Exception as exc:
        return [], "jsonl-error:%s" % exc
    return recs, None


def load_csv_records(path):
    recs = []
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                recs.append(dict(row))
    except Exception as exc:
        return [], "csv-error:%s" % exc
    return recs, None


def shared_identifier(a, b):
    ai = {i for i in a["identifiers"] if len(i) >= MIN_IDENTIFIER_LEN}
    bi = {i for i in b["identifiers"] if len(i) >= MIN_IDENTIFIER_LEN}
    return sorted(ai & bi)


def match_records(ref, results):
    """Return (status, matched_by, matched_result_or_None, details)."""
    best = {"status": "miss", "matched_by": None, "result": None,
            "title_similarity": None, "year_match": None, "author_match": None,
            "matched_identifier": None, "candidate_title": None}

    shared = None
    shared_with = []
    for res in results:
        ids = shared_identifier(ref, res)
        if ids:
            shared = ids[0]
            shared_with.append(res)

    if shared_with:
        titles = sorted(set(r["title_norm"] for r in shared_with if r["title_norm"]))
        if len(titles) <= 1:
            best.update({"status": "hit", "matched_by": "identifier",
                         "result": shared_with[0], "matched_identifier": shared,
                         "candidate_title": shared_with[0]["title"]})
            return best
        # same identifier, conflicting titles -> adjudicate with conflict flag
        best.update({"status": "adjudicate", "matched_by": "identifier_conflict",
                     "result": shared_with[0], "matched_identifier": shared,
                     "candidate_title": None,
                     "conflict": {"n_results": len(shared_with),
                                  "titles": titles[:5]}})
        return best

    if ref["title_norm"]:
        for res in results:
            if not res["title_norm"]:
                continue
            ratio = difflib.SequenceMatcher(None, ref["title_norm"],
                                            res["title_norm"]).ratio()
            if best["title_similarity"] is None or ratio > best["title_similarity"]:
                year_match = None
                author_match = None
                if ref["year"] is not None and res["year"] is not None:
                    year_match = abs(ref["year"] - res["year"]) <= YEAR_TOLERANCE
                if ref["first_author_last"] and res["first_author_last"]:
                    author_match = ref["first_author_last"] == res["first_author_last"]
                best.update({"title_similarity": ratio, "year_match": year_match,
                             "author_match": author_match, "result": res,
                             "candidate_title": res["title"]})

    ratio = best["title_similarity"]
    if ratio is not None and ratio >= TITLE_FUZZY:
        if best["year_match"] in (True, None) and best["author_match"] in (True, None):
            best.update({"status": "hit", "matched_by": "title_author_year"})
            return best
        best.update({"status": "adjudicate", "matched_by": "title_disagreement"})
        return best
    if ratio is not None and ratio >= TITLE_ADJUDICATION:
        best.update({"status": "adjudicate", "matched_by": "title_near_threshold"})
        return best
    return best


def freeze_reference_set(refs):
    frozen = []
    invalid_reasons = {}
    for i, ref in enumerate(refs):
        rec_id = "ref-%03d" % (i + 1)
        valid = True
        reason = None
        if ref["retracted"]:
            valid, reason = False, "retracted_or_unavailable"
        elif not ref["identifiers"] and not ref["title_norm"]:
            valid, reason = False, "no_identifier_no_title"
        record = {
            "ref_id": rec_id,
            "source": ref["source"],
            "title": ref["title"],
            "identifiers": ref["identifiers"],
            "first_author_last": ref["first_author_last"],
            "year": ref["year"],
            "valid": valid,
            "invalid_reason": reason,
        }
        frozen.append(record)
        if reason:
            invalid_reasons[rec_id] = reason
    version = hashlib.sha256(
        json.dumps(frozen, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    return frozen, version, invalid_reasons


def cmd_run(args):
    input_dir = args.input or DEFAULT_INPUT
    output_dir = args.output or DEFAULT_OUTPUT
    work_dir = args.work or DEFAULT_WORK
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    result_path = os.path.join(output_dir, "result.json")
    result = None
    if os.path.isfile(result_path):
        result, err = load_json_robust(result_path)
        if err:
            result = base_result(derive_case_id(None, input_dir), [], status="partial")
    if result is None:
        result = base_result(derive_case_id(None, input_dir), [], status="partial")

    refs_raw, ref_errors, ref_paths = load_records_from_spec(
        args.refs, ("reference*.json", "refs*.json", "reference_set*.json"), input_dir)
    results_raw, res_errors, res_paths = load_records_from_spec(
        args.results, ("results*.json", "search*.json", "query_response*.json"), input_dir)

    errors = sorted(set(ref_errors + res_errors))
    degradations = []

    if not refs_raw:
        result["status"] = "insufficient_evidence"
        result["errors"] = result.get("errors", []) + errors
        result["limitations"] = list(result.get("limitations", [])) + [
            "no reference set found; run with --refs pointing at the frozen reference records"]
        result["change_log"] = result.get("change_log", []) + [{
            "step": "run", "status": "insufficient_evidence",
            "summary": "no reference records could be loaded"}]
        write_json(result_path, result)
        print("run ok (insufficient_evidence); no reference records loaded")
        return 0

    refs = stable_sort(refs_raw)
    results = stable_sort(results_raw)
    refs_unique, dups = dedup_records(refs)
    results_unique, res_dups = dedup_records(results)
    if dups:
        degradations.append({"type": "duplicate_reference", "count": len(dups),
                             "detail": dups})
    if res_dups:
        degradations.append({"type": "duplicate_result", "count": len(res_dups)})

    frozen, version, invalid_reasons = freeze_reference_set(refs_unique)

    hit_matrix = []
    statuses = {}
    omissions = []
    for ref, frec in zip(refs_unique, frozen):
        ref_id = frec["ref_id"]
        if not frec["valid"]:
            statuses[ref_id] = "invalid"
            row = {
                "ref_id": ref_id, "status": "invalid", "matched_by": None,
                "candidate_title": None, "title_similarity": None,
                "year_match": None, "author_match": None, "matched_identifier": None,
                "invalid_reason": frec["invalid_reason"],
                "source": frec["source"], "title": frec["title"],
            }
            hit_matrix.append(row)
            continue
        match = match_records(ref, results_unique)
        statuses[ref_id] = match["status"]
        row = {
            "ref_id": ref_id, "status": match["status"], "matched_by": match["matched_by"],
            "candidate_title": match["candidate_title"],
            "title_similarity": None if match["title_similarity"] is None
                else round(match["title_similarity"], 4),
            "year_match": match["year_match"], "author_match": match["author_match"],
            "matched_identifier": match["matched_identifier"],
            "invalid_reason": None, "source": frec["source"], "title": frec["title"],
        }
        if "conflict" in match:
            row["conflict"] = match["conflict"]
            degradations.append({"type": "source_conflict", "ref_id": ref_id,
                                 "detail": match["conflict"]})
        hit_matrix.append(row)
        if match["status"] in ("miss", "adjudicate"):
            omissions.append({
                "ref_id": ref_id, "title": frec["title"],
                "identifiers": frec["identifiers"], "status": match["status"],
                "matched_by": match["matched_by"], "category": None,
                "signals": [], "evidence": None, "query": None, "field": None,
                "source": None, "time_window": None, "residual": None,
            })

    hit_matrix.sort(key=lambda r: r["ref_id"])
    omissions.sort(key=lambda o: o["ref_id"])

    total = len(frozen)
    eligible = sum(1 for f in frozen if f["valid"])
    invalid = total - eligible
    hit = sum(1 for v in statuses.values() if v == "hit")
    adj = sum(1 for v in statuses.values() if v == "adjudicate")
    miss = sum(1 for v in statuses.values() if v == "miss")
    denominator = eligible
    numerator = hit
    coverage_rate = (numerator / denominator) if denominator else None
    omission_rate = (1 - coverage_rate) if coverage_rate is not None else None

    result["reference_set"] = {
        "version": version, "total": total, "eligible": eligible, "invalid": invalid,
        "invalid_reasons": invalid_reasons, "records": frozen,
    }
    result["hit_matrix"] = hit_matrix
    result["omissions"] = omissions
    result["coverage"] = {
        "denominator": denominator, "numerator": numerator,
        "coverage_rate": coverage_rate, "omission_rate": omission_rate,
        "hit_rule": (
            "identifier exact -> title+first-author+year (title_similarity>="
            + str(TITLE_FUZZY) + ", year_tolerance=" + str(YEAR_TOLERANCE)
            + ") -> adjudicate (title_similarity>=" + str(TITLE_ADJUDICATION) + ")"
        ),
        "status_counts": {"hit": hit, "adjudicate": adj, "miss": miss, "invalid": invalid},
    }
    result["errors"] = sorted(set(result.get("errors", []) + errors))
    result["degradations"] = sorted(result.get("degradations", []) + degradations,
                                    key=lambda d: json.dumps(d, sort_keys=True))
    result["sources"] = sorted(set(result.get("sources", []) + ref_paths + res_paths))
    if result["status"] not in ("input_error", "insufficient_evidence"):
        result["status"] = "partial"
    result["change_log"] = result.get("change_log", []) + [{
        "step": "run", "status": result["status"],
        "summary": ("froze %d refs (eligible=%d), hit=%d, adjudicate=%d, miss=%d"
                    % (total, eligible, hit, adj, miss)),
    }]

    write_json(result_path, result)
    write_json(os.path.join(work_dir, "reference_set.json"),
               {"version": version, "records": frozen})
    write_json(os.path.join(work_dir, "coverage_core.json"),
               result["coverage"])
    write_hit_matrix_csv(os.path.join(work_dir, "hit_matrix.csv"), hit_matrix)
    print("run ok; total=%d eligible=%d hit=%d adjudicate=%d miss=%d" %
          (total, eligible, hit, adj, miss))
    return 0


def write_hit_matrix_csv(path, rows):
    columns = ["ref_id", "status", "matched_by", "candidate_title",
               "title_similarity", "year_match", "author_match",
               "matched_identifier", "invalid_reason", "source", "title"]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c) for c in columns})


# ---------------------------------------------------------------------------
# Merge: apply human adjudication / attribution / supplementary searches
# ---------------------------------------------------------------------------
def cmd_merge(args):
    output_dir = args.output or DEFAULT_OUTPUT
    work_dir = args.work or DEFAULT_WORK
    os.makedirs(output_dir, exist_ok=True)

    result_path = os.path.join(output_dir, "result.json")
    result, err = load_json_robust(result_path)
    if err or result is None:
        result = base_result(derive_case_id(None, output_dir), [], status="partial")

    updates_path = args.updates
    if not updates_path or not os.path.isfile(updates_path):
        print("merge aborted: --updates file not found: %s" % updates_path)
        return 1
    updates, uerr = load_json_robust(updates_path)
    if uerr or not isinstance(updates, dict):
        print("merge aborted: bad updates file: %s" % uerr)
        return 1

    frozen = result.get("reference_set", {}).get("records", [])
    by_id = {r["ref_id"]: r for r in frozen}
    hit_matrix = {r["ref_id"]: r for r in result.get("hit_matrix", [])}
    omissions = {o["ref_id"]: o for o in result.get("omissions", [])}

    # Adjudications: resolve pending records to hit/miss/adjudicate.
    for adj in updates.get("adjudications", []):
        ref_id = adj.get("ref_id")
        if ref_id not in by_id:
            continue
        status = adj.get("status")
        if status not in ("hit", "miss", "adjudicate"):
            continue
        row = hit_matrix.get(ref_id)
        if row:
            row["status"] = status
            row["matched_by"] = adj.get("matched_by") or row.get("matched_by") or "adjudicated"
            row["adjudication_reason"] = adj.get("reason")
            if adj.get("evidence"):
                row["evidence"] = adj["evidence"]

    # Omission attribution: assign concrete query/field/source/time-window causes.
    for attr in updates.get("omission_attributions", []):
        ref_id = attr.get("ref_id")
        if ref_id in omissions:
            o = omissions[ref_id]
            for k in ("category", "signals", "evidence", "query", "field",
                      "source", "time_window", "residual"):
                if k in attr and attr[k] is not None:
                    o[k] = attr[k]

    # Supplementary searches: only counts new_hits that exist in the frozen set.
    supplements = list(result.get("supplementary_searches", []))
    new_hit_ids = []
    for sup in updates.get("supplementary_searches", []):
        n = {"id": sup.get("id"), "query": sup.get("query"),
             "fields": sup.get("fields", []), "sources": sup.get("sources", []),
             "time_window": sup.get("time_window"), "new_hits": [],
             "rationale": sup.get("rationale")}
        for ref_id in sorted(set(sup.get("new_hits", []))):
            if ref_id in by_id and ref_id not in new_hit_ids:
                n["new_hits"].append(ref_id)
                new_hit_ids.append(ref_id)
        supplements.append(n)

    eligible_ids = [r["ref_id"] for r in frozen if r.get("valid", True)]
    # Capture pre-supplement omission count (after adjudications, before promotion).
    omissions_before_supp = sum(
        1 for rid in eligible_ids
        if hit_matrix.get(rid, {}).get("status") in ("miss", "adjudicate"))

    for ref_id in new_hit_ids:
        row = hit_matrix.get(ref_id)
        if row:
            row["status"] = "hit"
            row["matched_by"] = "supplementary"

    # Recompute coverage + marginal gain from the merged hit_matrix.
    denominator = len(eligible_ids)
    numerator = sum(1 for rid in eligible_ids
                    if hit_matrix.get(rid, {}).get("status") == "hit")
    coverage_rate = (numerator / denominator) if denominator else None
    omission_rate = (1 - coverage_rate) if coverage_rate is not None else None

    om_list = [omissions[r["ref_id"]] for r in frozen
               if r.get("valid", True) and hit_matrix.get(r["ref_id"], {}).get("status")
               in ("miss", "adjudicate")]
    om_list.sort(key=lambda o: o["ref_id"])
    residual = []
    for o in om_list:
        residual.append({
            "ref_id": o["ref_id"], "title": o["title"], "category": o["category"],
            "residual": o["residual"],
            "remedy": (o.get("evidence") or {}).get("locator") if isinstance(o.get("evidence"), dict) else None,
        })

    before = omissions_before_supp
    after = len(om_list)
    marginal_gain = {
        "before": before, "new_hits": len(new_hit_ids), "after": after,
        "rate": (len(new_hit_ids) / before) if before else None,
        "verified_new_hits": new_hit_ids,
    }

    result["supplementary_searches"] = supplements
    result["marginal_gain"] = marginal_gain
    result["residual_omissions"] = residual
    result["omissions"] = [omissions[r["ref_id"]] for r in frozen
                           if r["ref_id"] in omissions
                           and hit_matrix.get(r["ref_id"], {}).get("status")
                           in ("miss", "adjudicate")]
    result["omissions"].sort(key=lambda o: o["ref_id"])
    result["hit_matrix"] = [hit_matrix[r["ref_id"]] for r in frozen]
    result["hit_matrix"].sort(key=lambda r: r["ref_id"])
    result["coverage"] = {
        "denominator": denominator, "numerator": numerator,
        "coverage_rate": coverage_rate, "omission_rate": omission_rate,
        "hit_rule": result.get("coverage", {}).get("hit_rule", ""),
        "status_counts": {
            "hit": numerator, "adjudicate": sum(1 for v in hit_matrix.values()
                                                if v.get("status") == "adjudicate"),
            "miss": sum(1 for v in hit_matrix.values() if v.get("status") == "miss"),
            "invalid": sum(1 for v in hit_matrix.values() if v.get("status") == "invalid"),
        },
    }

    # Agent-supplied narrative fields (deterministic, sorted where possible).
    for key in ("research_question", "search_scope"):
        if key in updates and updates[key] is not None:
            result[key] = updates[key]
    for key in ("search_sessions", "sources", "evidence_map", "uncertainty",
                "pending_human", "limitations"):
        if key in updates:
            value = updates[key]
            if isinstance(value, list):
                result[key] = sorted(value, key=lambda x: json.dumps(x, sort_keys=True,
                                                                     ensure_ascii=False))
            elif value is not None:
                result[key] = value
    result["sources"] = sorted(set(result.get("sources", [])))

    # Contract fill: set values on the echoed contract keys + flattened keys.
    fill = updates.get("contract_fill", {})
    if isinstance(fill, dict):
        contract = result.get("output_contract", {})
        for k, v in fill.items():
            if k in contract:
                contract[k] = v
            if k not in CORE_KEYS:
                result[k] = v
        result["output_contract"] = contract

    # Status resolution.
    pending = result.get("pending_human", [])
    errors = result.get("errors", [])
    if not frozen:
        status = "insufficient_evidence"
    elif errors:
        status = "partial"
    elif result.get("research_question") and not pending and not residual:
        status = "ok"
    else:
        status = "partial"
    if updates.get("status_hint") in ("ok", "partial", "insufficient_evidence", "input_error"):
        status = updates["status_hint"]
    result["status"] = status

    result["change_log"] = result.get("change_log", []) + [{
        "step": "merge", "status": status,
        "summary": ("applied %d adjudications, %d attributions, %d supplements; "
                    "coverage_rate=%s" % (len(updates.get("adjudications", [])),
                                          len(updates.get("omission_attributions", [])),
                                          len(supplements), coverage_rate)),
    }]

    write_json(result_path, result)
    write_json(os.path.join(work_dir, "merge.json"),
               {"marginal_gain": marginal_gain, "residual": residual,
                "status": status})
    print("merge ok; coverage_rate=%s marginal_gain=%s" % (coverage_rate, marginal_gain))
    return 0


# ---------------------------------------------------------------------------
# Selftest: run the pipeline on a synthetic fixture and assert determinism
# ---------------------------------------------------------------------------
SELF_FIXTURE = {
    "case.json": {
        "case_id": "selftest-case-0001",
        "output_contract": {"coverage": "object", "candidate_list": "list"},
    },
    "task.md": "# selftest task\n",
    "references.json": [
        {"doi": "10.1000/ref-001", "title": "Alpha therapy for beta disease",
         "authors": [{"family": "Smith", "given": "A."}], "year": 2020},
        {"doi": "10.1000/ref-002", "title": "Gamma imaging of delta lesions",
         "authors": [{"family": "Jones", "given": "B."}], "year": 2019},
        {"title": "An omitted review of epsilon pathways",
         "authors": [{"family": "Lee", "given": "C."}], "year": 2018},
        {"doi": "10.1000/ref-004", "title": "Retracted study of zeta",
         "authors": [{"family": "Kim", "given": "D."}], "year": 2017,
         "status": "retracted"},
    ],
    "results.json": [
        {"doi": "10.1000/ref-001", "title": "Alpha therapy for beta disease",
         "authors": [{"family": "Smith", "given": "A."}], "year": 2020},
    ],
    "bad.json": "{not valid json",
}


def cmd_selftest(args):
    import shutil
    import subprocess
    import tempfile

    base = os.path.dirname(os.path.abspath(__file__))
    main_py = os.path.join(base, "main.py")
    results = []

    def run_cli(workroot, argv, timeout=120):
        cmd = [sys.executable, "-B", main_py] + argv
        proc = subprocess.run(cmd, cwd=workroot, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr

    def check(name, cond, detail=""):
        results.append((name, bool(cond), detail))

    # 1. --help exit code 0
    rc, out, err = run_cli(base, ["--help"])
    check("main --help exit 0", rc == 0, "rc=%d" % rc)

    tmp = tempfile.mkdtemp(prefix="coverage-selftest-")
    try:
        input_dir = os.path.join(tmp, "input")
        output_dir = os.path.join(tmp, "output")
        work_dir = os.path.join(tmp, "work")
        os.makedirs(input_dir)
        raw_text_files = {"task.md", "bad.json"}
        for name, content in SELF_FIXTURE.items():
            if name in raw_text_files:
                write_text(os.path.join(input_dir, name), content)
            else:
                write_json(os.path.join(input_dir, name), content)

        # 2. full pipeline twice -> identical result.json
        def full_run(outd, workd, tag):
            argv = ["scaffold", "--input", input_dir, "--output", outd, "--work", workd]
            run_cli(tmp, argv)
            run_cli(tmp, ["inspect", "--input", input_dir, "--output", outd, "--work", workd])
            run_cli(tmp, ["run", "--input", input_dir, "--output", outd, "--work", workd,
                          "--refs", "references.json", "--results", "results.json"])
            updates = {
                "research_question": "selftest question",
                "omission_attributions": [
                    {"ref_id": "ref-003", "category": "query_term_gap",
                     "signals": ["no synonym"], "query": "alpha AND beta",
                     "field": "title_abstract", "source": "PubMed", "time_window": "2015-2025"},
                ],
                "supplementary_searches": [
                    {"id": "sup-1", "query": "epsilon pathways", "fields": ["title_abstract"],
                     "sources": ["PubMed"], "time_window": "2015-2025",
                     "new_hits": ["ref-003"]},
                ],
            }
            updates_path = os.path.join(workd, "updates.json")
            write_json(updates_path, updates)
            run_cli(tmp, ["merge", "--output", outd, "--work", workd, "--updates", updates_path])
            return open(os.path.join(outd, "result.json"), "rb").read()

        blob1 = full_run(output_dir, work_dir, "first")
        output2 = os.path.join(tmp, "output2")
        work2 = os.path.join(tmp, "work2")
        blob2 = full_run(output2, work2, "second")
        check("determinism: two runs byte-identical", blob1 == blob2,
              "len1=%d len2=%d" % (len(blob1), len(blob2)))

        obj = json.loads(blob1.decode("utf-8"))
        check("result.json has case_id + status", bool(obj.get("case_id") and obj.get("status")))
        contract = obj.get("output_contract", {})
        check("contract keys backfilled", "coverage" in contract
              and contract.get("candidate_list") == [])
        check("coverage_rate numeric", isinstance(obj.get("coverage", {}).get("coverage_rate"), (int, float)))

        # 3. empty input dir -> insufficient_evidence, no crash
        empty = os.path.join(tmp, "empty")
        os.makedirs(empty)
        out3 = os.path.join(tmp, "out3"); work3 = os.path.join(tmp, "work3")
        run_cli(tmp, ["scaffold", "--input", empty, "--output", out3, "--work", work3])
        run_cli(tmp, ["run", "--input", empty, "--output", out3, "--work", work3])
        obj3 = json.load(open(os.path.join(out3, "result.json"), encoding="utf-8"))
        check("empty input -> insufficient_evidence", obj3.get("status") == "insufficient_evidence",
              obj3.get("status"))

        # 4. bad JSON recorded, no crash
        rc4, _, _ = run_cli(tmp, ["inspect", "--input", input_dir, "--output", output_dir,
                                  "--work", work_dir])
        insp = json.load(open(os.path.join(work_dir, "inspect.json"), encoding="utf-8"))
        has_bad = any("bad.json" in e for e in insp.get("errors", []))
        check("bad JSON recorded in inspect errors", rc4 == 0 and has_bad,
              "rc=%d errors=%d" % (rc4, len(insp.get("errors", []))))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    for name, ok, detail in results:
        print("[%s] %s %s" % ("PASS" if ok else "FAIL", name, detail))
    print("selftest %d/%d passed" % (passed, total))
    return 0 if passed == total else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser():
    parser = argparse.ArgumentParser(
        prog="scripts/main.py",
        description="Deterministic coverage/omission analysis (stdlib only).")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scaffold", help="write partial result.json skeleton (run first, <3 min)")
    p.add_argument("--input"); p.add_argument("--output"); p.add_argument("--work")
    p.set_defaults(func=cmd_scaffold)

    p = sub.add_parser("inspect", help="inventory /input files and parse errors")
    p.add_argument("--input"); p.add_argument("--output"); p.add_argument("--work")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("run", help="freeze reference set + build hit matrix + coverage")
    p.add_argument("--input"); p.add_argument("--output"); p.add_argument("--work")
    p.add_argument("--refs", help="reference-set file or glob, comma separated")
    p.add_argument("--results", help="search-result file or glob, comma separated")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("merge", help="apply adjudication/attribution/supplementary updates")
    p.add_argument("--output"); p.add_argument("--work")
    p.add_argument("--updates", help="path to updates JSON file")
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser("selftest", help="run built-in self-test (determinism + robustness)")
    p.set_defaults(func=cmd_selftest)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
