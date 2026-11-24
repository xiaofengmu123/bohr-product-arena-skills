"""core_impl.py -- deterministic literature-coverage & omission-analysis engine.

Pure Python standard library. No network access, no third-party packages.
The engine only ever reads files that are present on disk; every conclusion it
writes carries a locator (file + position) and, where available, a verbatim
quote. Missing data is written as ``not_reported``, never invented.

Thresholds are documented in references/coverage-method.md and must stay in
sync with that file.
"""

import csv
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timezone

VERSION = "1.1.0"
BUDGET_SECONDS = 900
BUDGET_REQUESTS = 64
MAX_FILE_BYTES = 2 * 1024 * 1024
TITLE_FUZZY = 0.88
TITLE_ADJUDICATION = 0.70
YEAR_TOLERANCE = 1
DEDUP_TITLE = 0.95

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

# --------------------------------------------------------------------------
# identifiers
# --------------------------------------------------------------------------
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>()\[\],;]+)", re.IGNORECASE)
PMID_RE = re.compile(r"\b(?:PMID|pmid)\s*:?\s*(\d{1,9})\b")
PMCID_RE = re.compile(r"\b(?:PMCID|pmcid)\s*:?\s*(PMC\d{1,10})\b", re.IGNORECASE)
ARXIV_RE = re.compile(
    r"\barXiv:(\d{4}\.\d{4,5})(?:v\d+)?\b"
    r"|\b(?:arXiv:)?(?:[a-z-]+\.)?(\d{4}\.\d{4,5})(?:v\d+)?\b",
    re.IGNORECASE,
)
RETRACT_RE = re.compile(
    r"\b(retract(?:ed|ion)?|withdraw(?:n|al)?)\b", re.IGNORECASE
)
UNAVAILABLE_RE = re.compile(
    r"\b(no full text|full[- ]?text (?:unavailable|not available)|"
    r"access denied|permission (?:denied|required)|subscription (?:required|wall))\b",
    re.IGNORECASE,
)

# Keys that mark a JSON dict as a bibliographic record.
ID_KEYS = ("DOI", "doi", "PMID", "pmid", "PMCID", "pmcid", "arxiv", "arXiv")
TITLE_KEYS = (
    "title", "Title", "TI", "article_title", "articleTitle",
    "booktitle", "publication_title", "container-title",
)
YEAR_KEYS = (
    "year", "Year", "pubYear", "pubyear", "publicationYear",
    "date", "issued", "year_published",
)
AUTHOR_KEYS = (
    "author", "authors", "authorString", "author_string", "creator",
    "creators", "inventor", "contributors",
)

CONTAINER_KEYS = ("message", "result", "results", "items", "records",
                  "hits", "data", "list", "works", "papers", "documents")


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha1_text(s):
    return hashlib.sha1(s.encode("utf-8", "replace")).hexdigest()


def norm_ws(s):
    return " ".join(str(s).split()) if s is not None else ""


def normalize_title(t):
    if t is None:
        return ""
    s = unicodedata.normalize("NFKD", str(t))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"&amp;", "&", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", s)
    return " ".join(s.split())


def title_similarity(a, b):
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def norm_doi(doi):
    if doi is None:
        return None
    s = str(doi).strip().rstrip(".,;")
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^doi\s*:\s*", "", s, flags=re.IGNORECASE)
    s = s.lower()
    return s or None


def norm_id(value, kind):
    if value is None:
        return None
    s = norm_ws(value).strip().rstrip(".,;")
    if not s:
        return None
    if kind == "doi":
        return norm_doi(s)
    if kind in ("pmid", "pmcid"):
        s = s.upper()
        s = re.sub(r"^PMID\s*:?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"^PMCID\s*:?\s*", "", s, flags=re.IGNORECASE)
        m = re.search(r"(PMC\d+|\d{1,9})", s)
        return m.group(1) if m else None
    if kind == "arxiv":
        m = re.search(r"(\d{4}\.\d{4,5})", s)
        return m.group(1) if m else None
    return s


def first_author_surname(authors):
    if not authors:
        return None
    first = authors[0]
    if isinstance(first, dict):
        for k in ("family", "surname", "last", "lastname", "name"):
            if k in first and first.get(k):
                return norm_ws(first.get(k)).lower()
        return None
    s = norm_ws(first)
    if not s:
        return None
    # "Smith, J." -> "smith"; "J. Smith" -> "smith"
    if "," in s:
        return s.split(",")[0].strip().rstrip(".").lower()
    parts = [p.strip().rstrip(".") for p in s.split() if p.strip()]
    if not parts:
        return s.lower()
    # "Smith J" (surname first) vs "J Smith" (given first): when the last
    # token is a one-letter initial, the surname is the first token.
    if len(parts) >= 2 and len(parts[-1]) == 1 and parts[-1].isalpha():
        return parts[0].lower()
    return parts[-1].lower()


def extract_identifiers(text):
    """Return normalized identifier dict found in arbitrary text."""
    out = {"doi": None, "pmid": None, "pmcid": None, "arxiv": None}
    if not text:
        return out
    m = DOI_RE.search(text)
    if m:
        out["doi"] = norm_doi(m.group(1))
    m = PMID_RE.search(text)
    if m:
        out["pmid"] = norm_id(m.group(1), "pmid")
    m = PMCID_RE.search(text)
    if m:
        out["pmcid"] = norm_id(m.group(1), "pmcid")
    m = ARXIV_RE.search(text)
    if m:
        g = next((g for g in m.groups() if g), None)
        out["arxiv"] = norm_id(g, "arxiv") if g else None
    return out


def read_text_robust(path, max_bytes=MAX_FILE_BYTES):
    """Read a text file as UTF-8(-sig) with tolerant fallback.

    Returns (text, error). Oversized files return (None, 'oversized').
    """
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, f"stat_error: {e}"
    if size > max_bytes:
        return None, "oversized"
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        return None, f"read_error: {e}"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), None
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace"), "bad_encoding"


# --------------------------------------------------------------------------
# record parsing
# --------------------------------------------------------------------------
def _pick(obj, keys):
    for k in keys:
        if isinstance(obj, dict) and k in obj and obj.get(k) not in (None, ""):
            v = obj.get(k)
            if isinstance(v, list):
                v = v[0] if v else None
            if v is None:
                continue
            return v
    return None


def _dict_to_record(obj, locator, kind="json"):
    title = _pick(obj, TITLE_KEYS)
    title = norm_ws(title) if title is not None else None

    ids = {}
    doi = _pick(obj, ("DOI", "doi"))
    ids["doi"] = norm_id(doi, "doi") if doi is not None else None
    pmid = _pick(obj, ("PMID", "pmid"))
    ids["pmid"] = norm_id(pmid, "pmid") if pmid is not None else None
    pmcid = _pick(obj, ("PMCID", "pmcid"))
    ids["pmcid"] = norm_id(pmcid, "pmcid") if pmcid is not None else None
    arxiv = _pick(obj, ("arxiv", "arXiv", "ARXIV"))
    ids["arxiv"] = norm_id(arxiv, "arxiv") if arxiv is not None else None

    authors = None
    for k in AUTHOR_KEYS:
        if isinstance(obj, dict) and k in obj and obj.get(k):
            v = obj.get(k)
            if isinstance(v, list):
                authors = v
            elif isinstance(v, str):
                authors = [a.strip() for a in re.split(r"[;,]", v) if a.strip()]
            elif isinstance(v, dict):
                authors = [v]
            if authors:
                break

    year = _pick(obj, YEAR_KEYS)
    if isinstance(year, dict):
        # Crossref 'issued': {'date-parts': [[2018]]}
        for k in ("date-parts", "date-parts-list"):
            pass
        year = year.get("date-parts") or year.get("date_parts")
    if isinstance(year, (list, tuple)):
        year = year[0] if year else None
    if isinstance(year, (list, tuple)):
        year = year[0] if year else None
    if isinstance(year, str):
        m = re.search(r"(18|19|20)\d{2}", year)
        year = m.group(0) if m else year
    if year is not None and not isinstance(year, (str, int)):
        year = None

    title_quote = norm_ws(obj.get("title")) if isinstance(obj.get("title"), str) else None
    return {
        "title": title,
        "title_norm": normalize_title(title),
        "identifiers": ids,
        "authors": authors,
        "first_author_surname": first_author_surname(authors),
        "year": str(year) if year is not None else None,
        "source_locator": locator,
        "source_file": locator.split("::")[0] if "::" in locator else locator,
        "raw": obj if kind == "json" else None,
        "quote": title_quote,
    }


def _walk_json(node, locator, out, depth=0):
    if depth > 6 or node is None:
        return
    if isinstance(node, dict):
        has_title = any(k in node and node.get(k) for k in TITLE_KEYS)
        has_id = any(k in node and node.get(k) for k in ID_KEYS)
        if has_title or has_id:
            # Only treat as a record if it has more than a bare wrapper.
            if has_title or has_id and len(node) >= 2:
                out.append(_dict_to_record(node, locator, "json"))
        for k, v in node.items():
            _walk_json(v, locator, out, depth + 1)
    elif isinstance(node, list):
        for item in node:
            _walk_json(item, locator, out, depth + 1)


def parse_json_file(path, locator):
    text, err = read_text_robust(path)
    if err:
        return [], [f"{path}: {err}"]
    try:
        data = json.loads(text)
    except ValueError as e:
        return [], [f"{path}: bad_json: {e}"]
    records = []
    _walk_json(data, locator, records)
    return records, []


def parse_pdf_file(path, locator):
    """Extract text via pdftotext if available; never fabricates on failure."""
    try:
        proc = subprocess.run(
            ["pdftotext", "-enc", "UTF-8", path, "-"],
            capture_output=True, timeout=60,
        )
    except FileNotFoundError:
        return [], [f"{path}: pdftotext_not_found"]
    except Exception as e:  # noqa: BLE001
        return [], [f"{path}: pdftotext_error: {e}"]
    if proc.returncode != 0:
        return [], [f"{path}: pdftotext_exit_{proc.returncode}"]
    text = proc.stdout.decode("utf-8", "replace")
    return text_to_records(text, locator, "pdf"), []


def text_to_records(text, locator, kind="text"):
    """Extract records from plain text by identifier + title line heuristics."""
    records = []
    if not text:
        return records
    ids = extract_identifiers(text)
    # A line that looks like a reference heading with a year.
    for i, line in enumerate(text.splitlines()):
        line = line.strip()
        if len(line) < 8 or len(line) > 400:
            continue
        has_year = re.search(r"(18|19|20)\d{2}", line)
        has_id = extract_identifiers(line)
        if has_id["doi"] or has_id["pmid"] or has_id["pmcid"] or has_id["arxiv"]:
            m = re.search(r"(18|19|20)\d{2}", line)
            rec = {
                "title": line,
                "title_norm": normalize_title(line),
                "identifiers": has_id,
                "authors": None,
                "first_author_surname": None,
                "year": m.group(0) if m else None,
                "source_locator": f"{locator}:line{i+1}",
                "source_file": locator,
                "raw": None,
                "quote": line,
            }
            records.append(rec)
        elif has_year and ("." in line or "(" in line):
            # Plausible citation line; keep as low-confidence candidate.
            rec = {
                "title": line,
                "title_norm": normalize_title(line),
                "identifiers": {"doi": None, "pmid": None, "pmcid": None, "arxiv": None},
                "authors": None,
                "first_author_surname": None,
                "year": has_year.group(0),
                "source_locator": f"{locator}:line{i+1}",
                "source_file": locator,
                "raw": None,
                "quote": line,
            }
            records.append(rec)
    # Fall back: if no per-line records, synthesize one from whole-text ids.
    if not records and any(ids.values()):
        records.append({
            "title": None,
            "title_norm": "",
            "identifiers": ids,
            "authors": None,
            "first_author_surname": None,
            "year": None,
            "source_locator": locator,
            "source_file": locator,
            "raw": None,
            "quote": None,
        })
    return records


def parse_any_file(path, locator):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return parse_pdf_file(path, locator)
    if ext == ".json":
        return parse_json_file(path, locator)
    if ext in (".jsonl",):
        text, err = read_text_robust(path)
        if err:
            return [], [f"{path}: {err}"]
        records = []
        for i, line in enumerate(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            tmp = []
            _walk_json(obj, f"{locator}:line{i+1}", tmp)
            records.extend(tmp)
        return records, []
    if ext in (".csv",):
        text, err = read_text_robust(path)
        if err:
            return [], [f"{path}: {err}"]
        return _parse_csv_text(text, locator), []
    if ext in (".txt", ".md", ".tsv", ""):
        text, err = read_text_robust(path)
        if err:
            return [], [f"{path}: {err}"]
        return text_to_records(text, locator, "text"), []
    return [], [f"{path}: unsupported_extension_{ext or 'none'}"]


def _parse_csv_text(text, locator):
    import io
    records = []
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except Exception:  # noqa: BLE001
        return records
    if not rows:
        return records
    header = [h.strip().lower() for h in rows[0]]
    idx = {name: i for i, name in enumerate(header) if name}
    for r, row in enumerate(rows[1:], start=2):
        if not row or all(not c.strip() for c in row):
            continue

        def cell(names):
            for n in names:
                if n in idx and idx[n] < len(row):
                    v = row[idx[n]].strip()
                    if v:
                        return v
            return None

        title = cell(("title", "ti", "article title", "article_title"))
        doi = cell(("doi",))
        pmid = cell(("pmid",))
        pmcid = cell(("pmcid",))
        year = cell(("year", "pubyear", "publication year", "pub_year", "date"))
        authors = cell(("authors", "author", "authorstring", "author_string"))
        ids = {
            "doi": norm_id(doi, "doi") if doi else None,
            "pmid": norm_id(pmid, "pmid") if pmid else None,
            "pmcid": norm_id(pmcid, "pmcid") if pmcid else None,
            "arxiv": None,
        }
        if not title and not any(ids.values()):
            continue
        records.append({
            "title": title,
            "title_norm": normalize_title(title),
            "identifiers": ids,
            "authors": [a.strip() for a in re.split(r"[;,]", authors) if a.strip()] if authors else None,
            "first_author_surname": first_author_surname(
                [a.strip() for a in re.split(r"[;,]", authors) if a.strip()]
            ) if authors else None,
            "year": year,
            "source_locator": f"{locator}:row{r}",
            "source_file": locator,
            "raw": None,
            "quote": title,
        })
    return records


# --------------------------------------------------------------------------
# reference set freeze + dedup + status
# --------------------------------------------------------------------------
def classify_record_status(rec, text_snippet=None):
    """Mark retracted / unavailable / conflict, else assessable."""
    hay = " ".join(filter(None, [rec.get("title") or "", text_snippet or ""]))
    if RETRACT_RE.search(hay):
        return "retracted"
    if UNAVAILABLE_RE.search(hay):
        return "unavailable"
    return "assessable"


def freeze_reference_set(records):
    """Dedup + freeze. Deterministic order: by (year, normalized title)."""
    merged = []
    seen_ids = {}
    for rec in records:
        status = classify_record_status(rec)
        dup_of = None
        # Key by identifier *value* (not by identifier type), otherwise every
        # DOI would collide on the shared key "doi".
        for key, val in rec["identifiers"].items():
            if val:
                idkey = f"{key}:{val}"
                if idkey in seen_ids:
                    dup_of = seen_ids[idkey]
                    break
        if dup_of is None:
            tn = rec.get("title_norm") or ""
            if tn:
                for j, other in enumerate(merged):
                    s = title_similarity(tn, other.get("title_norm") or "")
                    if s >= DEDUP_TITLE:
                        dup_of = j
                        break
        if dup_of is not None:
            # Record the duplicate, keep first occurrence.
            merged[dup_of].setdefault("duplicates", []).append(rec["source_locator"])
            continue
        for key, val in rec["identifiers"].items():
            if val:
                seen_ids[f"{key}:{val}"] = len(merged)
        rec["status"] = status
        rec["duplicates"] = []
        merged.append(rec)
    # deterministic sort key
    def sort_key(r):
        y = r.get("year") or "9999"
        t = r.get("title_norm") or ""
        return (y, t, r.get("source_locator") or "")
    merged.sort(key=sort_key)
    for i, rec in enumerate(merged):
        rec["ref_id"] = f"ref-{i+1:04d}"
        rec["inclusion_reason"] = (
            rec.get("inclusion_reason")
            or f"present in reference source {rec['source_file']}"
        )
    version = sha1_text(
        json.dumps(
            [{"t": r.get("title_norm"), "i": r.get("identifiers"), "y": r.get("year")}
             for r in merged],
            ensure_ascii=False, sort_keys=True,
        )
    )[:12]
    return merged, version


# --------------------------------------------------------------------------
# matching
# --------------------------------------------------------------------------
def match_one(ref, results):
    """Return best match dict for one reference record."""
    best = None
    for j, res in enumerate(results):
        matched_by = None
        confidence = None
        # 1) identifier exact
        for key in ("doi", "pmid", "pmcid", "arxiv"):
            rv = ref["identifiers"].get(key)
            sv = res["identifiers"].get(key)
            if rv and sv and rv == sv:
                matched_by = "identifier"
                confidence = "high"
                break
        if matched_by:
            best = {
                "ref_id": ref.get("ref_id"),
                "matched_by": matched_by,
                "confidence": confidence,
                "result_index": j,
                "result_locator": res.get("source_locator"),
                "similarity": 1.0,
                "ref_title": ref.get("title"),
                "result_title": res.get("title"),
            }
            break
        # 2) title + (first author | year)
        if ref.get("title_norm") and res.get("title_norm"):
            sim = title_similarity(ref["title_norm"], res["title_norm"])
            author_ok = (
                ref.get("first_author_surname")
                and res.get("first_author_surname")
                and ref["first_author_surname"] == res["first_author_surname"]
            )
            year_ok = False
            if ref.get("year") and res.get("year"):
                try:
                    year_ok = abs(int(ref["year"]) - int(res["year"])) <= YEAR_TOLERANCE
                except ValueError:
                    year_ok = ref["year"] == res["year"]
            if sim >= TITLE_FUZZY and (author_ok or year_ok):
                matched_by = "title_author_year"
                confidence = "high" if sim >= 0.95 else "medium"
            elif sim >= TITLE_ADJUDICATION:
                matched_by = "title_only_fuzzy"
                confidence = "low"
            if matched_by:
                candidate = {
                    "ref_id": ref.get("ref_id"),
                    "matched_by": matched_by,
                    "confidence": confidence,
                    "result_index": j,
                    "result_locator": res.get("source_locator"),
                    "similarity": round(sim, 4),
                    "ref_title": ref.get("title"),
                    "result_title": res.get("title"),
                }
                if best is None or candidate["similarity"] > best.get("similarity", 0):
                    best = candidate
    if best is None:
        best = {
            "ref_id": ref.get("ref_id"),
            "matched_by": "none",
            "confidence": "none",
            "result_index": None,
            "result_locator": None,
            "similarity": 0.0,
            "ref_title": ref.get("title"),
            "result_title": None,
        }
    return best


def compute_coverage(refs, match_rows):
    assessable = [r for r in refs if r.get("status") == "assessable"]
    excluded = {
        "unavailable": sum(1 for r in refs if r.get("status") == "unavailable"),
        "retracted": sum(1 for r in refs if r.get("status") == "retracted"),
        "conflict": sum(1 for r in refs if r.get("status") == "conflict"),
    }
    denominator = len(assessable)
    hit_rows = [m for m in match_rows if m["confidence"] in ("high", "medium")]
    high = sum(1 for m in match_rows if m["confidence"] == "high")
    adjudicated = [m for m in match_rows if m["confidence"] == "low"]
    numerator = len(hit_rows)
    rate = (numerator / denominator) if denominator else None
    lower = (high / denominator) if denominator else None
    upper = ((high + len(adjudicated)) / denominator) if denominator else None
    return {
        "denominator": denominator,
        "numerator": numerator,
        "coverage_rate": round(rate, 4) if rate is not None else None,
        "omission_rate": round(1 - rate, 4) if rate is not None else None,
        "hit_rule": (
            "identifier exact -> title+first-author+year fuzzy "
            f"(title_fuzzy={TITLE_FUZZY}) -> title-only adjudication "
            f"(threshold={TITLE_ADJUDICATION}, year_tolerance={YEAR_TOLERANCE})"
        ),
        "excluded": excluded,
        "uncertainty": {
            "coverage_lower": round(lower, 4) if lower is not None else None,
            "coverage_upper": round(upper, 4) if upper is not None else None,
            "n_adjudicated": len(adjudicated),
            "basis": (
                "lower counts only high-confidence (identifier/title-author-year "
                ">=0.95) hits; upper adds low-confidence title-only fuzzy matches. "
                "A non-zero gap marks decisions requiring human adjudication."
            ),
        },
    }


# --------------------------------------------------------------------------
# result.json skeleton + merge
# --------------------------------------------------------------------------
def echo_output_contract(case):
    """Return dict of output_contract fields filled with typed null/[]/{} defaults."""
    oc = case.get("output_contract")
    out = {}
    if isinstance(oc, list):
        for item in oc:
            if isinstance(item, dict) and item.get("name"):
                out[item["name"]] = _default_for_type(item.get("type"))
    elif isinstance(oc, dict):
        for name, desc in oc.items():
            out[name] = _default_for_type(str(desc))
    return out


def _default_for_type(t):
    t = (t or "").lower()
    if "array" in t or "数组" in t or "list" in t:
        return []
    if "object" in t or "对象" in t or "map" in t or "dict" in t:
        return {}
    return None


def resolve_case_id(case, input_dir):
    cid = (
        case.get("case_id")
        or case.get("id")
        or case.get("challenge_id")
    )
    if cid:
        return str(cid)
    h = hashlib.sha1(json.dumps(case, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
    return f"derived-{h}"


def skeleton(case, input_dir, status="partial"):
    cid = resolve_case_id(case, input_dir)
    contract_fields = echo_output_contract(case)
    base = {
        "case_id": cid,
        "status": status,
        "research_question": None,
        "search_scope": None,
        "reference_set": None,
        "search_sessions": [],
        "coverage": None,
        "hit_matrix": [],
        "omissions": [],
        "supplementary_searches": [],
        "marginal_gain": None,
        "residual_omissions": [],
        "evidence_map": {},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "runtime": {
            "started_at": now_iso(),
            "seconds": 0,
            "model_requests": 0,
            "budget_seconds": BUDGET_SECONDS,
            "budget_requests": BUDGET_REQUESTS,
            "engine_version": VERSION,
        },
    }
    # output_contract fields are added if absent; core resolved values (case_id,
    # status, coverage, hit_matrix) must never be clobbered by a null echo.
    for k, v in contract_fields.items():
        base.setdefault(k, v)
    return base


def _write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def log_change(result, content, reason):
    entry = {"time": now_iso(), "content": content, "reason": reason}
    result.setdefault("change_log", []).append(entry)
    return result


# --------------------------------------------------------------------------
# subcommands
# --------------------------------------------------------------------------
def cmd_scaffold(input_dir, output_dir, work_dir):
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    errors = []
    degradations = []
    case = {}
    case_path = os.path.join(input_dir, "case.json")
    if os.path.isfile(case_path):
        text, err = read_text_robust(case_path)
        if err:
            errors.append(f"case.json: {err}")
            degradations.append(f"case.json unreadable ({err}); proceeding with empty contract")
        else:
            try:
                case = json.loads(text)
            except ValueError as e:
                errors.append(f"case.json bad_json: {e}")
    else:
        errors.append("case.json missing in /input")

    result = skeleton(case, input_dir, status="input_error" if errors else "partial")
    result["errors"] = errors
    result["degradations"] = degradations
    log_change(result, "scaffold wrote a legal partial skeleton", "scaffold")
    _write_json(os.path.join(output_dir, "result.json"), result)
    return result


def _list_input_files(input_dir):
    files = []
    skipped = []
    if not os.path.isdir(input_dir):
        return files, skipped
    for root, _dirs, names in os.walk(input_dir):
        for name in sorted(names):
            p = os.path.join(root, name)
            if name.startswith("."):
                continue
            try:
                size = os.path.getsize(p)
            except OSError:
                skipped.append(f"{p}: stat_error")
                continue
            if size > MAX_FILE_BYTES:
                skipped.append(f"{p}: oversized_{size}b")
                continue
            files.append(p)
    files.sort()
    return files, skipped


def cmd_inspect(input_dir, output_dir, work_dir):
    files, skipped = _list_input_files(input_dir)
    summary = []
    for p in files:
        locator = p
        recs, errs = parse_any_file(p, locator)
        summary.append({
            "file": p,
            "bytes": os.path.getsize(p) if os.path.isfile(p) else None,
            "ext": os.path.splitext(p)[1].lower(),
            "candidate_records": len(recs),
            "with_identifier": sum(1 for r in recs if any(r["identifiers"].values())),
            "parse_errors": errs,
        })
    inspect = {
        "input_dir": input_dir,
        "files": summary,
        "skipped": skipped,
        "candidate_total": sum(s["candidate_records"] for s in summary),
        "note": (
            "Determine which file(s) are the independent reference set (known "
            "relevant literature) and which are search results / query responses. "
            "Then call `run --refs <glob> --results <glob>`."
        ),
    }
    os.makedirs(work_dir, exist_ok=True)
    _write_json(os.path.join(work_dir, "inspect.json"), inspect)
    return inspect


def _select_files(input_dir, patterns):
    """Expand optional --refs/--results patterns against input_dir."""
    if not patterns:
        return []
    import glob as _glob
    chosen = []
    for pat in patterns:
        if os.path.isabs(pat):
            matches = _glob.glob(pat)
        else:
            matches = _glob.glob(os.path.join(input_dir, pat))
        chosen.extend(matches)
    seen, uniq = set(), []
    for p in sorted(chosen):
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def cmd_run(input_dir, output_dir, work_dir, refs=None, results=None):
    errors = []
    degradations = []
    ref_files = _select_files(input_dir, refs)
    res_files = _select_files(input_dir, results)

    ref_records = []
    for p in ref_files:
        recs, errs = parse_any_file(p, p)
        ref_records.extend(recs)
        errors.extend(errs)
    res_records = []
    for p in res_files:
        recs, errs = parse_any_file(p, p)
        res_records.extend(recs)
        errors.extend(errs)

    if not ref_records and not res_records:
        # Do NOT guess which file is the reference set vs search results: that
        # would conflate the two and corrupt the denominator. Report honestly.
        result = _load_or_scaffold(input_dir, output_dir, work_dir)
        result["status"] = "insufficient_evidence"
        result["errors"].extend(errors)
        result["degradations"].extend(degradations)
        result.setdefault("limitations", []).append(
            "No reference-set or search-result records were parsed from the "
            "files selected by --refs/--results. Run `inspect` to identify "
            "which files are the independent reference set vs search results, "
            "then re-run with explicit globs."
        )
        log_change(result, "run parsed no records; marked insufficient_evidence", "run")
        _write_json(os.path.join(output_dir, "result.json"), result)
        return result

    if not ref_records:
        result = _load_or_scaffold(input_dir, output_dir, work_dir)
        result["status"] = "insufficient_evidence"
        result["errors"].extend(errors)
        result["degradations"].extend(degradations)
        result.setdefault("limitations", []).append(
            "No reference-set records could be parsed; coverage cannot be "
            "computed without an independent frozen reference set."
        )
        log_change(result, "run found no reference records; marked insufficient_evidence", "run")
        _write_json(os.path.join(output_dir, "result.json"), result)
        return result

    frozen_refs, version = freeze_reference_set(ref_records)
    match_rows = [match_one(r, res_records) for r in frozen_refs]
    coverage = compute_coverage(frozen_refs, match_rows)

    # hit_matrix statuses
    status_map = {
        "high": "hit", "medium": "hit", "low": "adjudicate", "none": "miss",
    }
    hit_matrix = []
    for r, m in zip(frozen_refs, match_rows):
        status = r.get("status")
        if status != "assessable":
            row_status = status  # unavailable / retracted / conflict
        else:
            row_status = status_map.get(m["confidence"], "miss")
        hit_matrix.append({
            "ref_id": r["ref_id"],
            "status": row_status,
            "matched_by": m["matched_by"],
            "confidence": m["confidence"],
            "similarity": m["similarity"],
            "ref_title": r.get("title"),
            "ref_identifiers": r["identifiers"],
            "ref_year": r.get("year"),
            "result_title": m.get("result_title"),
            "result_locator": m.get("result_locator"),
            "ref_source": r.get("source_locator"),
        })

    core = {
        "reference_set": {
            "version": version,
            "source": ref_files or None,
            "frozen_at": now_iso(),
            "denominator": coverage["denominator"],
            "records": [
                {k: r.get(k) for k in (
                    "ref_id", "title", "identifiers", "authors", "year",
                    "status", "inclusion_reason", "duplicates", "source_locator",
                )}
                for r in frozen_refs
            ],
        },
        "coverage": coverage,
        "hit_matrix": hit_matrix,
    }
    os.makedirs(work_dir, exist_ok=True)
    _write_json(os.path.join(work_dir, "coverage_core.json"), core)
    _write_json(os.path.join(output_dir, "reference_set.json"),
                core["reference_set"])
    _write_hit_matrix_csv(os.path.join(output_dir, "hit_matrix.csv"), hit_matrix)

    result = _load_or_scaffold(input_dir, output_dir, work_dir)
    result["reference_set"] = core["reference_set"]
    result["coverage"] = coverage
    result["hit_matrix"] = hit_matrix
    result["sources"] = sorted(set(ref_files + res_files))
    result["errors"] = errors
    result["degradations"] = degradations
    if coverage["denominator"] == 0 and any(r["status"] != "assessable" for r in frozen_refs):
        result.setdefault("limitations", []).append(
            "All reference records were unavailable/retracted/conflict; no assessable denominator."
        )
    result["status"] = "partial" if not errors else "partial"
    log_change(result, "run froze reference set, matched, computed coverage", "run")
    _write_json(os.path.join(output_dir, "result.json"), result)
    return result


def _write_hit_matrix_csv(path, rows):
    if not rows:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fields = ["ref_id", "status", "matched_by", "confidence", "similarity",
              "ref_year", "ref_title", "result_title", "result_locator"]
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fields})


def _load_or_scaffold(input_dir, output_dir, work_dir):
    out_path = os.path.join(output_dir, "result.json")
    if os.path.isfile(out_path):
        text, err = read_text_robust(out_path)
        if not err:
            try:
                return json.loads(text)
            except ValueError:
                pass
    return cmd_scaffold(input_dir, output_dir, work_dir)


def _load_updates(updates_path):
    text, err = read_text_robust(updates_path)
    if err:
        raise ValueError(f"updates file unreadable: {err}")
    return json.loads(text)


def cmd_merge(input_dir, output_dir, work_dir, updates_path):
    result = _load_or_scaffold(input_dir, output_dir, work_dir)
    updates = _load_updates(updates_path)

    # Deterministic field merge: updates override, arrays append.
    for key in (
        "research_question", "search_scope",
        "omissions", "supplementary_searches", "residual_omissions",
        "uncertainty", "limitations", "pending_human",
    ):
        if key not in updates:
            continue
        val = updates[key]
        if isinstance(val, list) and isinstance(result.get(key), list):
            result[key] = result[key] + val
        else:
            result[key] = val

    # adjudications: patch hit_matrix rows + recompute coverage if possible.
    if updates.get("adjudications"):
        adj = {a.get("ref_id"): a for a in updates["adjudications"] if a.get("ref_id")}
        for row in result.get("hit_matrix", []):
            if row.get("ref_id") in adj:
                a = adj[row["ref_id"]]
                decision = a.get("decision")
                if decision in ("hit", "miss"):
                    row["status"] = decision
                    row["matched_by"] = a.get("matched_by", row.get("matched_by"))
                    row["confidence"] = a.get("confidence", row.get("confidence"))
                    row["result_locator"] = a.get("evidence", {}).get("locator", row.get("result_locator"))
        result = _recompute_coverage_from_matrix(result)

    # marginal_gain
    if updates.get("marginal_gain") is not None:
        result["marginal_gain"] = updates["marginal_gain"]

    result["status"] = updates.get("status", result.get("status", "partial"))
    log_change(result, "merge applied agent adjudications/attribution", "merge")
    _write_json(os.path.join(output_dir, "result.json"), result)
    return result


def _recompute_coverage_from_matrix(result):
    cov = result.get("coverage")
    if not cov:
        return result
    matrix = result.get("hit_matrix", [])
    assessable = [r for r in matrix if r.get("status") not in (
        "unavailable", "retracted", "conflict")]
    hits = [r for r in assessable if r.get("status") == "hit"]
    adjudicate = [r for r in assessable if r.get("status") == "adjudicate"]
    denominator = len(assessable)
    numerator = len(hits)
    cov["denominator"] = denominator
    cov["numerator"] = numerator
    cov["coverage_rate"] = round(numerator / denominator, 4) if denominator else None
    cov["omission_rate"] = round(1 - numerator / denominator, 4) if denominator else None
    cov["uncertainty"] = {
        "coverage_lower": round(numerator / denominator, 4) if denominator else None,
        "coverage_upper": round((numerator + len(adjudicate)) / denominator, 4) if denominator else None,
        "n_adjudicated": len(adjudicate),
        "basis": cov.get("uncertainty", {}).get("basis", "recomputed after merge"),
    }
    return result


def cmd_finalize(input_dir, output_dir, work_dir):
    result = _load_or_scaffold(input_dir, output_dir, work_dir)
    # Write editable side artifacts.
    _write_evidence_map(os.path.join(output_dir, "evidence_map.json"), result)
    _write_report(os.path.join(output_dir, "coverage_report.md"), result)
    result.setdefault("runtime", {})["seconds"] = 0  # harness may overwrite
    result["status"] = _honest_status(result)
    log_change(result, "finalize wrote report/evidence map and set honest status", "finalize")
    _write_json(os.path.join(output_dir, "result.json"), result)
    return result


def _honest_status(result):
    if result.get("errors"):
        return "input_error"
    if result.get("coverage") is None or result.get("reference_set") is None:
        return "insufficient_evidence"
    if result.get("pending_human"):
        return "partial"
    matrix = result.get("hit_matrix", [])
    if any(r.get("status") == "adjudicate" for r in matrix):
        return "partial"
    if result.get("residual_omissions"):
        return "partial"
    return "ok"


def _write_evidence_map(path, result):
    em = {}
    for m in result.get("hit_matrix", []):
        em[m.get("ref_id", "?")] = {
            "ref_locator": m.get("ref_source"),
            "result_locator": m.get("result_locator"),
            "matched_by": m.get("matched_by"),
            "ref_title": m.get("ref_title"),
            "result_title": m.get("result_title"),
        }
    _write_json(path, em)


def _write_report(path, result):
    cov = result.get("coverage") or {}
    lines = [
        "# 检索覆盖率与遗漏分析报告",
        "",
        f"- case_id: `{result.get('case_id')}`",
        f"- status: `{result.get('status')}`",
        f"- 覆盖率: {cov.get('coverage_rate')}（命中 {cov.get('numerator')} / "
        f"可评估 {cov.get('denominator')}）",
        f"- 遗漏率: {cov.get('omission_rate')}",
        f"- 不确定区间: [{cov.get('uncertainty', {}).get('coverage_lower')}, "
        f"{cov.get('uncertainty', {}).get('coverage_upper')}]（裁决 {cov.get('uncertainty', {}).get('n_adjudicated')} 条）",
        "",
        "## 命中矩阵",
        "",
        "详见 `hit_matrix.csv`；逐条含 ref_id、状态、匹配依据、置信度。",
        "",
        "## 漏检归因",
        "",
    ]
    for om in result.get("omissions", []):
        lines.append(f"- {om.get('ref_id')}: {om.get('category')} "
                     f"（query={om.get('query_id')}, field={om.get('field')}, "
                     f"source={om.get('source')}, window={om.get('time_window')}）")
    if not result.get("omissions"):
        lines.append("- （无）")
    lines += ["", "## 残余遗漏", ""]
    for ro in result.get("residual_omissions", []):
        lines.append(f"- {ro.get('ref_id')}: {ro.get('reason')} "
                     f"（可补救={ro.get('remediable')}）")
    if not result.get("residual_omissions"):
        lines.append("- （无）")
    lines += ["", "## 限制与不确定性", ""]
    for li in result.get("limitations", []):
        lines.append(f"- {li}")
    for u in result.get("uncertainty", []):
        if isinstance(u, dict):
            lines.append(f"- {u.get('item', u)}")
        else:
            lines.append(f"- {u}")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------
def run_subcommand(args):
    start = time.time()
    input_dir = args.input if getattr(args, "input", None) else DEFAULT_INPUT
    output_dir = args.output if getattr(args, "output", None) else DEFAULT_OUTPUT
    work_dir = args.work if getattr(args, "work", None) else DEFAULT_WORK

    if args.command == "scaffold":
        result = cmd_scaffold(input_dir, output_dir, work_dir)
    elif args.command == "inspect":
        result = cmd_inspect(input_dir, output_dir, work_dir)
    elif args.command == "run":
        result = cmd_run(input_dir, output_dir, work_dir,
                         getattr(args, "refs", None),
                         getattr(args, "results", None))
    elif args.command == "merge":
        result = cmd_merge(input_dir, output_dir, work_dir,
                           args.updates)
    elif args.command == "finalize":
        result = cmd_finalize(input_dir, output_dir, work_dir)
    else:
        raise SystemExit(f"unknown command: {args.command}")

    if isinstance(result, dict):
        result.setdefault("runtime", {})["seconds"] = round(time.time() - start, 1)
    return result
