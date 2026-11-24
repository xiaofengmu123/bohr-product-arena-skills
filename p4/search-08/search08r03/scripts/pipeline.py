"""Deterministic pipeline for systematic literature search and screening.

This module implements the reproducible "ledger" of the Skill: input
discovery, output_contract preservation, bibliography parsing, duplicate
detection, evidence verification and count closure. Scientific judgments are
made by the model and written to work/judgments.json; this module never
fabricates decisions, DOIs, titles or numbers.

Design contract (the "self-check & determinism" variant):

* Every file is written with canonical JSON (sort_keys, no wall-clock
  timestamps). The same /input and the same work state produce byte-identical
  results, verified by ``selfcheck``.
* Time is recorded as a content-addressed ``run_id`` plus a monotonic step
  sequence, never as a wall clock, so two runs are literally identical.
* Scripts only do the reproducible bookkeeping; science is the model's job.
  Anything the model cannot decide is marked ``uncertain`` / ``pending_human``.

Standard library only.
"""

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_TEXT_BYTES = 2 * 1024 * 1024  # skip single text files larger than 2 MB

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"

OUTPUT_CONTRACT_KEYS = (
    "output_contract",
    "outputContract",
    "result_schema",
    "result_fields",
    "output",
)

# Recognized text/record file kinds.
JSON_KINDS = {".json"}
JSONL_KINDS = {".jsonl", ".ndjson"}
CSV_KINDS = {".csv", ".tsv"}
RIS_KINDS = {".ris", ".txt"}  # .txt is checked heuristically for RIS below
BIB_KINDS = {".bib"}
MD_KINDS = {".md", ".markdown", ".txt"}
SKIP_KINDS = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".zip"}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def canonical_json(obj):
    """Canonical, byte-stable JSON serialization (deterministic)."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def sha256_hex(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def dump_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(canonical_json(obj))


def read_text(path, max_bytes=MAX_TEXT_BYTES):
    """Read text with encoding fallback; returns (text, note)."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        return None, {"error": str(exc)}
    if len(data) > max_bytes:
        return None, {"skipped": "oversize", "size": len(data)}
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(enc), None
        except UnicodeDecodeError:
            continue
    for enc in ("gb18030",):
        try:
            return data.decode(enc), {"encoding": enc, "degraded": "non_utf8"}
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), {
        "encoding": "replacement", "degraded": "undecodable"}


def pick(record, keys):
    """Return the first present, non-empty value among candidate keys."""
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def clean_text(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = "; ".join(str(v) for v in value)
    return str(value).strip()


# ---------------------------------------------------------------------------
# Normalization (deterministic)
# ---------------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def normalize_title(title):
    if not title:
        return ""
    text = unicodedata.normalize("NFKC", clean_text(title))
    text = text.lower()
    text = _WS_RE.sub(" ", text)
    text = _PUNCT_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def normalize_doi(doi):
    if not doi:
        return ""
    value = clean_text(doi).strip()
    value = re.sub(r"^https?://(dx\.)?doi\.org/", "", value, flags=re.I)
    value = value.strip().lower().rstrip(".,;")
    return value


def dedup_key(record):
    parts = []
    doi = normalize_doi(record.get("doi"))
    title = normalize_title(record.get("title"))
    if doi:
        parts.append("doi:" + doi)
    if title:
        parts.append("title:" + title)
    if not parts:
        pmid = clean_text(record.get("pmid"))
        url = clean_text(record.get("url"))
        fallback = pmid or url or ("raw:" + canonical_json(record.get("raw", {})))
        parts.append("fallback:" + normalize_title(fallback))
    return "|".join(parts)


def title_similarity(a, b):
    import difflib
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------------------
# Input discovery & output_contract
# ---------------------------------------------------------------------------

def input_fingerprint(input_dir):
    """Content-addressed fingerprint of the whole /input directory."""
    input_dir = Path(input_dir)
    digest = hashlib.sha256()
    if not input_dir.is_dir():
        digest.update(b"<missing>")
        return digest.hexdigest()
    relpaths = sorted(
        str(p.relative_to(input_dir)) for p in input_dir.rglob("*") if p.is_file())
    for rel in relpaths:
        fp = input_dir / rel
        try:
            content_hash = sha256_hex(fp.read_bytes())
            size = fp.stat().st_size
        except OSError:
            content_hash = "<unreadable>"
            size = -1
        digest.update(rel.encode("utf-8", "replace"))
        digest.update(b"\x00")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\x00")
        digest.update(content_hash.encode("ascii"))
        digest.update(b"\x00")
    return digest.hexdigest()


def classify_file(suffix, first_bytes, name):
    lower = name.lower()
    if suffix in JSON_KINDS:
        return "json"
    if suffix in JSONL_KINDS:
        return "jsonl"
    if suffix in CSV_KINDS:
        return "csv"
    if suffix in BIB_KINDS:
        return "bib"
    if suffix in RIS_KINDS or suffix in MD_KINDS:
        head = first_bytes[:400].decode("utf-8", "replace")
        if head.lstrip().startswith("TY") and re.search(r"TY\s+-\s+", head):
            return "ris"
        if head.lstrip().startswith("@"):
            return "bib"
        return "text"
    if suffix in SKIP_KINDS:
        return "skip"
    return "text"


def scan_input(input_dir):
    input_dir = Path(input_dir)
    files = []
    warnings = []
    if not input_dir.is_dir():
        warnings.append("input directory missing: " + str(input_dir))
        return {"input_dir": str(input_dir), "files": files, "warnings": warnings}

    relpaths = sorted(
        str(p.relative_to(input_dir)) for p in input_dir.rglob("*") if p.is_file())
    for rel in relpaths:
        fp = input_dir / rel
        try:
            size = fp.stat().st_size
        except OSError as exc:
            files.append({"path": rel, "kind": "unreadable",
                          "size_bytes": -1, "error": str(exc)})
            continue
        suffix = Path(rel).suffix.lower()
        try:
            head = fp.open("rb").read(512)
        except OSError:
            head = b""
        kind = classify_file(suffix, head, rel)
        entry = {
            "path": rel,
            "kind": kind,
            "size_bytes": size,
            "sha256": None,
            "oversize": size > MAX_TEXT_BYTES and kind != "skip",
        }
        if entry["oversize"]:
            warnings.append("oversize file skipped: " + rel)
        files.append(entry)
    files.sort(key=lambda e: e["path"])
    return {"input_dir": str(input_dir), "files": files, "warnings": warnings}


def find_output_contract(case_data):
    """Extract output_contract verbatim from the case file, if present."""
    if not isinstance(case_data, dict):
        return {}
    for key in OUTPUT_CONTRACT_KEYS:
        if key in case_data:
            value = case_data[key]
            if isinstance(value, dict):
                return value
            if isinstance(value, list):
                return {str(item): None for item in value if isinstance(item, str)}
            return {key: None}
    # Fallback: nested under a couple of common wrappers.
    for wrapper in ("task", "requirements", "contract"):
        inner = case_data.get(wrapper)
        if isinstance(inner, dict):
            found = find_output_contract(inner)
            if found:
                return found
    return {}


def extract_case_meta(inventory, input_dir):
    """Find case.json / task.md and pull case_id and output_contract."""
    input_dir = Path(input_dir)
    meta = {"case_id": "unknown", "output_contract": {}, "research_question": None,
            "case_files": []}
    for entry in inventory.get("files", []):
        name = Path(entry["path"]).name.lower()
        if name == "case.json" or name.endswith(".case.json"):
            text, note = read_text(input_dir / entry["path"])
            if note:
                meta.setdefault("degradations", []).append(
                    {"file": entry["path"], "note": note})
            data = None
            if text:
                try:
                    data = json.loads(text)
                except ValueError as exc:
                    meta.setdefault("degradations", []).append(
                        {"file": entry["path"], "error": "bad json: " + str(exc)})
            if isinstance(data, dict):
                for ck in ("case_id", "caseId", "id"):
                    if data.get(ck):
                        meta["case_id"] = str(data[ck])
                        break
                meta["output_contract"] = find_output_contract(data)
                meta["research_question"] = pick(data, (
                    "research_question", "researchQuestion", "question",
                    "research_question_zh", "topic"))
                meta["case_files"].append(entry["path"])
        elif name == "task.md":
            meta["case_files"].append(entry["path"])
    return meta


# ---------------------------------------------------------------------------
# Bibliography parsing
# ---------------------------------------------------------------------------

def _records_from_dict(data, base):
    """Extract a record list from a JSON object.

    For a case.json, only explicit record-list keys or list-valued keys whose
    elements look like records are considered; the case object itself is never
    treated as a single record.
    """
    explicit = ("records", "data", "items", "papers", "results",
                "publications", "entries", "candidates", "bibliography")
    for list_key in explicit:
        value = data.get(list_key)
        if isinstance(value, list) and value:
            return value
    # Generic: any top-level list of dicts carrying a title or DOI.
    for value in data.values():
        if (isinstance(value, list) and value and
                any(isinstance(v, dict) and
                    (any(k in v for k in _TITLE_KEYS) or
                     any(k in v for k in _DOI_KEYS)) for v in value[:20])):
            return value
    if base != "case.json" and any(
            k in data for k in _TITLE_KEYS + _DOI_KEYS):
        return [data]
    return None


def parse_ris(text):
    records = []
    current = {}
    last_tag = None
    for raw in text.splitlines():
        line = raw.rstrip("\r\n")
        if not line.strip():
            continue
        if line.strip() == "ER":
            if current:
                records.append(current)
            current = {}
            last_tag = None
            continue
        match = re.match(r"^([A-Z0-9]{2})\s*-\s?(.*)$", line)
        if match:
            tag, value = match.group(1), match.group(2).strip()
            if tag == "TY" and current:
                records.append(current)
                current = {}
            current[tag] = value
            last_tag = tag
        elif line.startswith("  ") and last_tag:
            current[last_tag] = (current.get(last_tag, "") + " " +
                                 line.strip()).strip()
    if current:
        records.append(current)
    return records


def _parse_bib_fields(text, i):
    fields = {}
    n = len(text)
    while i < n:
        while i < n and text[i] in " \t\r\n,":
            i += 1
        if i >= n:
            break
        if text[i] == "}":
            return fields, i + 1
        match = re.compile(r"([A-Za-z][A-Za-z0-9_\-]*)\s*=").match(text, i)
        if not match:
            i += 1
            continue
        name = match.group(1).lower()
        i = match.end()
        while i < n and text[i] in " \t\r\n":
            i += 1
        if i < n and text[i] == "{":
            depth = 0
            j = i
            while j < n:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            fields[name] = text[i + 1:j]
            i = j + 1
        elif i < n and text[i] == '"':
            j = i + 1
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    j += 1
                j += 1
            fields[name] = text[i + 1:j]
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in ",}":
                j += 1
            fields[name] = text[i:j].strip()
            i = j
    return fields, i


def parse_bibtex(text):
    records = []
    i = 0
    n = len(text)
    while i < n:
        start = text.find("@", i)
        if start == -1:
            break
        match = re.compile(r"@([A-Za-z]+)\s*\{", re.S).match(text, start)
        if not match:
            i = start + 1
            continue
        i = match.end()
        comma = text.find(",", i)
        if comma == -1:
            break
        key = text[i:comma].strip()
        fields, i = _parse_bib_fields(text, comma + 1)
        fields["_type"] = match.group(1)
        fields["_key"] = key
        records.append(fields)
    return records


_TITLE_KEYS = ("title", "TI", "Title", "ti", "article_title", "paper_title",
               "name", "booktitle")
_DOI_KEYS = ("doi", "DO", "DOI", "Doi", "digital_object_identifier")
_ABSTRACT_KEYS = ("abstract", "AB", "summary", "Abstract", "abstract_text")
_AUTHOR_KEYS = ("authors", "AU", "author", "Authors", "author_list", "creator")
_YEAR_KEYS = ("year", "PY", "publication_year", "Year", "date", "published_year")
_JOURNAL_KEYS = ("journal", "JO", "container", "journal_name", "source",
                 "publication", "venue", "booktitle")
_PMID_KEYS = ("pmid", "PMID", "pubmed_id")
_URL_KEYS = ("url", "UR", "URL", "link")


def coerce_record(raw, source_file, index):
    if not isinstance(raw, dict):
        raw = {"value": raw}
    title = clean_text(pick(raw, _TITLE_KEYS))
    doi = clean_text(pick(raw, _DOI_KEYS))
    abstract = clean_text(pick(raw, _ABSTRACT_KEYS))
    authors = clean_text(pick(raw, _AUTHOR_KEYS))
    year = clean_text(pick(raw, _YEAR_KEYS))
    journal = clean_text(pick(raw, _JOURNAL_KEYS))
    pmid = clean_text(pick(raw, _PMID_KEYS))
    url = clean_text(pick(raw, _URL_KEYS))
    notes = clean_text(pick(raw, ("notes", "note", "comment")))
    return {
        "title": title,
        "doi": doi,
        "abstract": abstract,
        "authors": authors,
        "year": year,
        "journal": journal,
        "pmid": pmid,
        "url": url,
        "notes": notes,
        "source_file": source_file,
        "source_index": index,
        "raw": raw,
    }


def parse_records_from_files(inventory, input_dir):
    input_dir = Path(input_dir)
    raw_records = []
    parse_notes = []
    for entry in inventory.get("files", []):
        kind = entry.get("kind")
        rel = entry["path"]
        base = Path(rel).name.lower()
        if base == "task.md":
            continue  # task instructions, not a bibliography
        if kind in ("skip", "unreadable"):
            continue
        if entry.get("oversize"):
            parse_notes.append({"file": rel, "skipped": "oversize"})
            continue
        text, note = read_text(input_dir / rel)
        if note:
            parse_notes.append({"file": rel, "note": note})
        if not text:
            parse_notes.append({"file": rel, "skipped": "unreadable_or_empty"})
            continue
        items = None
        try:
            if kind == "json":
                data = json.loads(text)
                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict):
                    items = _records_from_dict(data, base)
            elif kind == "jsonl":
                items = []
                for line in text.splitlines():
                    line = line.strip()
                    if line:
                        items.append(json.loads(line))
            elif kind == "csv":
                sample = text[:4096]
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
                except csv.Error:
                    dialect = csv.excel_tab if "\t" in sample else csv.excel
                reader = csv.DictReader(io.StringIO(text), dialect=dialect)
                items = [dict(row) for row in reader]
            elif kind == "ris":
                items = parse_ris(text)
            elif kind == "bib":
                items = parse_bibtex(text)
            elif kind == "text":
                # Best-effort: DOI/PMID/title lines; otherwise leave to model.
                items = _scan_text_records(text)
        except (ValueError, csv.Error, json.JSONDecodeError) as exc:
            parse_notes.append({"file": rel, "error": str(exc)})
            continue
        if not items:
            parse_notes.append({"file": rel, "note": "no records parsed"})
            continue
        for idx, item in enumerate(items):
            try:
                raw_records.append(coerce_record(item, rel, idx))
            except Exception as exc:  # noqa: BLE001 - never crash the ledger
                parse_notes.append(
                    {"file": rel, "index": idx, "error": str(exc)})
    parse_notes.sort(key=lambda x: canonical_json(x))
    return raw_records, parse_notes


def _scan_text_records(text):
    """Very conservative text fallback: only DOI-bearing lines become records."""
    items = []
    doi_re = re.compile(r"\b10\.\d{4,9}/[^\s\"'<>]+")
    for line in text.splitlines():
        found = doi_re.search(line)
        if found:
            doi = found.group(0).rstrip(".,;")
            items.append({"doi": doi, "notes": line.strip(),
                          "_raw_line": line.rstrip("\r\n")})
    return items


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def assign_record_ids(raw_records):
    width = max(2, len(str(len(raw_records))))
    for i, record in enumerate(raw_records, start=1):
        record["record_id"] = "r" + str(i).zfill(width)
    return raw_records


class _UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def build_dedup(records):
    records = assign_record_ids(list(records))
    n = len(records)
    uf = _UnionFind(n)

    # Group by DOI (sufficient alone) and by normalized title (used when DOI
    # is missing or differs). Transitive closure handles preprint vs. formal
    # versions that share a DOI but differ in title.
    doi_map = {}
    title_map = {}
    for i, record in enumerate(records):
        doi = normalize_doi(record.get("doi"))
        title = normalize_title(record.get("title"))
        if doi:
            doi_map.setdefault(doi, []).append(i)
        if title:
            title_map.setdefault(title, []).append(i)
    for group in list(doi_map.values()) + list(title_map.values()):
        for j in group[1:]:
            uf.union(group[0], j)

    by_root = {}
    for i in range(n):
        by_root.setdefault(uf.find(i), []).append(i)

    clusters = []
    singletons = []
    removed = 0
    cluster_id = 0
    # Deterministic order: by the smallest record index in each group.
    for root in sorted(by_root, key=lambda r: min(by_root[r])):
        group = [records[i] for i in sorted(by_root[root])]
        group.sort(key=lambda r: r["record_id"])
        rep = group[0]
        if len(group) == 1:
            singletons.append(rep["record_id"])
            continue
        members = [r["record_id"] for r in group]
        removed += len(group) - 1
        cluster_id += 1
        dois = [normalize_doi(r.get("doi")) for r in group
                if normalize_doi(r.get("doi"))]
        titles = [normalize_title(r.get("title")) for r in group
                  if normalize_title(r.get("title"))]
        reasons = []
        doi_counts = {}
        for d in dois:
            doi_counts[d] = doi_counts.get(d, 0) + 1
        if any(c >= 2 for c in doi_counts.values()):
            reasons.append("doi_match")
        title_counts = {}
        for t in titles:
            title_counts[t] = title_counts.get(t, 0) + 1
        if any(c >= 2 for c in title_counts.values()):
            reasons.append("title_match")
        clusters.append({
            "cluster_id": "c" + str(cluster_id).zfill(2),
            "representative": rep["record_id"],
            "members": members,
            "merge_reason": "_and_".join(reasons) if reasons
                            else "fallback_key_match",
            "evidence": {
                "doi": rep.get("doi") or None,
                "normalized_title": titles[0] if titles else None,
                "member_titles": {r["record_id"]: r.get("title") or None
                                  for r in group},
            },
        })

    # possible duplicates across distinct merged keys
    reps = [records[min(by_root[root])] for root in
            sorted(by_root, key=lambda r: min(by_root[r]))]
    possible = []
    for i in range(len(reps)):
        for j in range(i + 1, len(reps)):
            a, b = reps[i], reps[j]
            sim = title_similarity(normalize_title(a.get("title")),
                                   normalize_title(b.get("title")))
            if 0.85 <= sim < 1.0:
                possible.append({
                    "a": a["record_id"], "b": b["record_id"],
                    "title_similarity": round(sim, 4),
                    "note": "possible_duplicate_not_merged",
                })
    possible.sort(key=lambda p: (p["a"], p["b"]))
    return {
        "n_input": len(records),
        "n_merged": removed,
        "n_unique": len(records) - removed,
        "clusters": clusters,
        "possible_duplicates": possible,
        "singletons": singletons,
        "records": records,
    }


def build_template(dedup):
    """One judgment slot per unique (post-dedup) record."""
    kept_ids = set(dedup["singletons"])
    for cluster in dedup["clusters"]:
        kept_ids.add(cluster["representative"])
    template = []
    for record in dedup["records"]:
        if record["record_id"] in kept_ids:
            template.append({
                "record_id": record["record_id"],
                "decision": "uncertain",
                "reason_code": "pending",
                "evidence_quote": "",
                "evidence_locator": "",
                "reviewer_a": {"decision": None, "reason_code": None,
                               "evidence_quote": None},
                "reviewer_b": {"decision": None, "reason_code": None,
                               "evidence_quote": None},
                "conflict": {"is_conflict": False, "type": None, "detail": None},
                "arbitration": {"decision": None, "rationale": None},
                "needs_human": True,
            })
    template.sort(key=lambda j: j["record_id"])
    return template


# ---------------------------------------------------------------------------
# Evidence verification
# ---------------------------------------------------------------------------

def verify_evidence(quote, record, locator):
    if not quote:
        return False, None, "empty_quote"
    haystacks = []
    if locator == "title":
        haystacks.append(record.get("title") or "")
    elif locator == "abstract":
        haystacks.append(record.get("abstract") or "")
    elif locator in ("notes", "fulltext", "source"):
        haystacks.append(record.get("notes") or "")
        haystacks.append(record.get("abstract") or "")
    else:
        haystacks = [record.get("title") or "", record.get("abstract") or "",
                     record.get("notes") or ""]
    for hay in haystacks:
        if quote in hay:
            return True, hay, "exact"
    # Whitespace-normalized fallback (still verbatim modulo whitespace).
    flat_quote = _WS_RE.sub(" ", quote).strip()
    for hay in haystacks:
        flat_hay = _WS_RE.sub(" ", hay)
        if flat_quote in flat_hay:
            return True, hay, "whitespace_normalized"
    return False, None, "mismatch"


# ---------------------------------------------------------------------------
# Skeleton / normalize / finalize / selfcheck
# ---------------------------------------------------------------------------

def build_result_skeleton(meta, status, reason):
    contract = meta.get("output_contract") or {}
    result = {key: None for key in contract.keys()}
    result.update({
        "case_id": meta.get("case_id", "unknown"),
        "status": status,
        "research_question": meta.get("research_question"),
        "inclusion_criteria": [],
        "exclusion_criteria": [],
        "criteria_version": None,
        "deduplication": {"n_input": 0, "n_merged": 0, "n_unique": 0,
                          "clusters": [], "possible_duplicates": []},
        "screening": {"records": [], "n_include": 0, "n_exclude": 0,
                      "n_uncertain": 0},
        "conflicts": [],
        "audit": {"search": {"n_input": 0},
                  "dedup": {"n_input": 0, "n_merged": 0, "n_unique": 0},
                  "screening": {"n_unique": 0, "n_include": 0, "n_exclude": 0,
                                "n_uncertain": 0},
                  "closure": False,
                  "closure_gaps": []},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "change_log": [{"step": "s0", "action": "skeleton",
                        "status": status, "reason": reason}],
        "runtime": {"seconds_budget": 900, "requests_budget": 64,
                    "deterministic": True, "run_id": None, "wall_clock": None},
    })
    return result


def cmd_skeleton(input_dir, output_dir, work_dir):
    input_dir, output_dir, work_dir = (Path(input_dir), Path(output_dir),
                                       Path(work_dir))
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    inventory = scan_input(input_dir)
    meta = extract_case_meta(inventory, input_dir)
    fingerprint = input_fingerprint(input_dir)
    run_id = fingerprint[:16]

    raw_records, parse_notes = parse_records_from_files(inventory, input_dir)
    degradations = list(meta.get("degradations", []))
    degradations.extend(parse_notes)
    degradations.extend({"warning": w} for w in inventory.get("warnings", []))

    has_case = any(Path(f["path"]).name.lower() in ("case.json", "task.md")
                   for f in inventory.get("files", []))
    if not inventory.get("files"):
        status = STATUS_INSUFFICIENT
        reason = "empty input directory"
    elif not has_case and not raw_records:
        status = STATUS_INPUT_ERROR
        reason = "no case.json/task.md and no bibliographic records found"
    else:
        status = STATUS_PARTIAL
        reason = "partial skeleton before analysis"

    # Persist work state.
    dump_json(work_dir / "inventory.json", {
        **inventory,
        "run_id": run_id,
        "fingerprint": fingerprint,
    })
    dump_json(work_dir / "records.json", {
        "run_id": run_id,
        "raw_records": raw_records,
        "parse_notes": parse_notes,
    })
    dump_json(work_dir / "protocol.json", {
        "run_id": run_id,
        "fingerprint": fingerprint,
        "case_id": meta["case_id"],
        "research_question": meta["research_question"],
        "inclusion_criteria": [],
        "exclusion_criteria": [],
        "criteria_version": None,
        "criteria_frozen": False,
        "output_contract": meta.get("output_contract", {}),
        "case_files": meta.get("case_files", []),
    })

    result = build_result_skeleton(meta, status, reason)
    result["runtime"]["run_id"] = run_id
    result["sources"] = [
        {"path": f["path"], "kind": f["kind"], "size_bytes": f["size_bytes"]}
        for f in inventory.get("files", [])]
    result["degradations"] = sorted(
        degradations, key=lambda d: canonical_json(d))
    result["errors"] = [] if status not in (STATUS_INPUT_ERROR,) else [
        {"step": "s0", "message": reason}]
    dump_json(output_dir / "result.json", result)
    print("skeleton ok status=%s run_id=%s files=%d records=%d" % (
        status, run_id, len(inventory.get("files", [])), len(raw_records)))
    return 0


def cmd_normalize(input_dir, output_dir, work_dir):
    work_dir = Path(work_dir)
    records_doc = load_json(work_dir / "records.json")
    protocol = load_json(work_dir / "protocol.json") or {}
    if not records_doc:
        print("normalize: work/records.json missing; run skeleton first")
        return 1
    dedup = build_dedup(records_doc.get("raw_records", []))
    template = build_template(dedup)
    dump_json(work_dir / "dedup.json", dedup)
    dump_json(work_dir / "judgments_template.json", template)
    # Seed judgments.json from template only if not already authored.
    if not (work_dir / "judgments.json").exists():
        dump_json(work_dir / "judgments.json", template)
    print("normalize ok n_input=%d n_merged=%d n_unique=%d possible_dup=%d" % (
        dedup["n_input"], dedup["n_merged"], dedup["n_unique"],
        len(dedup["possible_duplicates"])))
    return 0


def _merge_judgment(template_entry, judged):
    merged = dict(template_entry)
    if not isinstance(judged, dict):
        return merged
    for key in ("decision", "reason_code", "evidence_quote", "evidence_locator",
                "needs_human"):
        if judged.get(key) is not None:
            merged[key] = judged[key]
    for key in ("reviewer_a", "reviewer_b", "conflict", "arbitration"):
        if isinstance(judged.get(key), dict):
            merged[key] = {**merged.get(key, {}), **judged[key]}
    return merged


def cmd_finalize(input_dir, output_dir, work_dir):
    output_dir, work_dir = Path(output_dir), Path(work_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    dedup = load_json(work_dir / "dedup.json")
    protocol = load_json(work_dir / "protocol.json") or {}
    template = load_json(work_dir / "judgments_template.json") or []
    judgments = load_json(work_dir / "judgments.json") or template
    mapping = load_json(work_dir / "contract_mapping.json") or {}
    contract = protocol.get("output_contract", {}) or {}

    run_id = protocol.get("run_id") or input_fingerprint(input_dir)[:16]

    record_by_id = {r["record_id"]: r for r in (dedup or {}).get("records", [])}
    judged_by_id = {j["record_id"]: j for j in judgments
                    if isinstance(j, dict) and j.get("record_id")}

    screening = []
    errors = []
    degradations = []
    for tpl in template:
        judged = judged_by_id.get(tpl["record_id"], tpl)
        merged = _merge_judgment(tpl, judged)
        record = record_by_id.get(tpl["record_id"], {})
        verified, hay, mode = verify_evidence(
            merged.get("evidence_quote") or "", record,
            merged.get("evidence_locator") or "")
        merged["evidence_verified"] = verified
        merged["evidence_match_mode"] = mode
        if merged.get("decision") not in ("include", "exclude", "uncertain"):
            merged["decision"] = "uncertain"
            merged["needs_human"] = True
        if merged.get("decision") != "uncertain" and not merged.get(
                "evidence_quote"):
            degradations.append(
                {"record_id": tpl["record_id"], "issue":
                 "decision_without_evidence_quote"})
            merged["needs_human"] = True
        if merged.get("decision") != "uncertain" and not verified:
            degradations.append(
                {"record_id": tpl["record_id"], "issue": "evidence_mismatch",
                 "mode": mode})
            merged["needs_human"] = True
        # Reviewer conflict synthesis: if both reviewers disagree -> conflict.
        ra = merged.get("reviewer_a", {})
        rb = merged.get("reviewer_b", {})
        if (ra.get("decision") and rb.get("decision") and
                ra.get("decision") != rb.get("decision")):
            merged["conflict"] = {
                "is_conflict": True,
                "type": "decision",
                "detail": "reviewer_a=%s reviewer_b=%s" % (
                    ra.get("decision"), rb.get("decision")),
            }
        if merged["decision"] == "uncertain":
            merged["needs_human"] = True
        # Enrich with record metadata for the output table.
        merged["title"] = record.get("title") or ""
        merged["doi"] = record.get("doi") or None
        merged["year"] = record.get("year") or None
        merged["journal"] = record.get("journal") or None
        screening.append(merged)

    screening.sort(key=lambda r: r["record_id"])

    n_include = sum(1 for r in screening if r["decision"] == "include")
    n_exclude = sum(1 for r in screening if r["decision"] == "exclude")
    n_uncertain = sum(1 for r in screening if r["decision"] == "uncertain")
    n_unique = len(screening)

    conflicts = [r for r in screening if r.get("conflict", {}).get("is_conflict")]
    conflicts.sort(key=lambda r: r["record_id"])
    uncertainty = [{"record_id": r["record_id"], "reason": r["reason_code"]}
                   for r in screening if r["decision"] == "uncertain"]
    pending_human = [{"record_id": r["record_id"],
                      "reason": r.get("reason_code", "pending"),
                      "action": "researcher confirmation required"}
                     for r in screening if r.get("needs_human")]

    n_input = (dedup or {}).get("n_input", 0)
    n_merged = (dedup or {}).get("n_merged", 0)
    dedup_n_unique = (dedup or {}).get("n_unique", 0)

    # Carry forward input-level degradations recorded by skeleton/normalize.
    inventory_doc = load_json(work_dir / "inventory.json") or {}
    records_doc = load_json(work_dir / "records.json") or {}
    for note in records_doc.get("parse_notes", []):
        degradations.append(note)
    for warn in inventory_doc.get("warnings", []):
        degradations.append({"warning": warn})

    gaps = []
    if n_unique != dedup_n_unique:
        gaps.append("unique_records(%d) != dedup.n_unique(%d)" % (
            n_unique, dedup_n_unique))
    if n_input - n_merged != dedup_n_unique:
        gaps.append("n_input - n_merged(%d) != dedup.n_unique(%d)" % (
            n_input - n_merged, dedup_n_unique))
    if n_include + n_exclude + n_uncertain != n_unique:
        gaps.append("include+exclude+uncertain(%d) != unique(%d)" % (
            n_include + n_exclude + n_uncertain, n_unique))
    closure = not gaps

    # Backfill output_contract fields.
    result = {key: None for key in contract.keys()}
    for key, spec in mapping.items():
        if key not in result:
            continue
        if isinstance(spec, dict) and "value" in spec:
            result[key] = spec["value"]
        elif isinstance(spec, str) and spec.startswith("ref:"):
            result[key] = _resolve_ref(spec[4:], screening, dedup, protocol)
    if n_input == 0:
        status = STATUS_INSUFFICIENT
    elif closure and n_uncertain == 0 and n_unique > 0:
        status = STATUS_OK
    else:
        status = STATUS_PARTIAL
    result.update({
        "case_id": protocol.get("case_id", "unknown"),
        "status": status,
        "research_question": protocol.get("research_question"),
        "inclusion_criteria": protocol.get("inclusion_criteria", []),
        "exclusion_criteria": protocol.get("exclusion_criteria", []),
        "criteria_version": protocol.get("criteria_version"),
        "deduplication": {
            "n_input": n_input, "n_merged": n_merged, "n_unique": dedup_n_unique,
            "clusters": (dedup or {}).get("clusters", []),
            "possible_duplicates": (dedup or {}).get("possible_duplicates", []),
        },
        "screening": {"records": screening, "n_include": n_include,
                      "n_exclude": n_exclude, "n_uncertain": n_uncertain},
        "conflicts": conflicts,
        "audit": {
            "search": {"n_input": n_input},
            "dedup": {"n_input": n_input, "n_merged": n_merged,
                      "n_unique": dedup_n_unique},
            "screening": {"n_unique": n_unique, "n_include": n_include,
                          "n_exclude": n_exclude, "n_uncertain": n_uncertain},
            "closure": closure,
            "closure_gaps": gaps,
        },
        "sources": [{"path": f["path"], "kind": f["kind"],
                     "size_bytes": f["size_bytes"]}
                    for f in (load_json(work_dir / "inventory.json") or {})
                    .get("files", [])],
        "uncertainty": uncertainty,
        "pending_human": pending_human,
        "errors": sorted(errors, key=lambda e: canonical_json(e)),
        "degradations": sorted(degradations, key=lambda d: canonical_json(d)),
        "change_log": [
            {"step": "s0", "action": "skeleton", "status": STATUS_PARTIAL},
            {"step": "s2", "action": "normalize"},
            {"step": "s3", "action": "screen", "note": "model judgments"},
            {"step": "s5", "action": "finalize", "closure": closure},
        ],
        "runtime": {"seconds_budget": 900, "requests_budget": 64,
                    "deterministic": True, "run_id": run_id, "wall_clock": None},
    })

    # Write editable artifacts (deterministic).
    dump_json(output_dir / "result.json", result)
    _write_screening_table(output_dir / "screening_table.csv", screening)
    dump_json(output_dir / "audit_log.json", {
        "run_id": run_id,
        "steps": [
            {"seq": 0, "action": "skeleton", "n_input": 0},
            {"seq": 2, "action": "normalize", "n_input": n_input,
             "n_merged": n_merged, "n_unique": dedup_n_unique},
            {"seq": 3, "action": "screen", "n_unique": n_unique},
            {"seq": 5, "action": "finalize", "closure": closure,
             "n_include": n_include, "n_exclude": n_exclude,
             "n_uncertain": n_uncertain},
        ],
        "closure": closure,
        "closure_gaps": gaps,
    })
    _write_protocol_md(output_dir / "protocol.md", protocol, dedup, closure,
                       n_include, n_exclude, n_uncertain)
    dump_json(output_dir / "evidence_map.json", _build_evidence_map(screening))
    print("finalize ok closure=%s n_input=%d n_unique=%d inc=%d exc=%d unc=%d" % (
        closure, n_input, n_unique, n_include, n_exclude, n_uncertain))
    return 0


def _resolve_ref(ref, screening, dedup, protocol):
    if ref in ("screening.records", "records", "candidate_set"):
        return screening
    if ref == "screening.include":
        return [r for r in screening if r["decision"] == "include"]
    if ref == "screening.exclude":
        return [r for r in screening if r["decision"] == "exclude"]
    if ref == "deduplication.clusters":
        return dedup.get("clusters", [])
    if ref in ("protocol.research_question", "research_question"):
        return protocol.get("research_question")
    if ref == "protocol.inclusion_criteria":
        return protocol.get("inclusion_criteria", [])
    if ref == "protocol.exclusion_criteria":
        return protocol.get("exclusion_criteria", [])
    return None


def _write_screening_table(path, screening):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["record_id", "title", "doi", "year", "decision",
                         "reason_code", "evidence_locator",
                         "evidence_verified", "evidence_quote",
                         "conflict", "needs_human"])
        for r in screening:
            writer.writerow([
                r.get("record_id"), r.get("title"), r.get("doi"),
                r.get("year"), r.get("decision"), r.get("reason_code"),
                r.get("evidence_locator"), r.get("evidence_verified"),
                r.get("evidence_quote", ""),
                r.get("conflict", {}).get("is_conflict", False),
                r.get("needs_human", False),
            ])


def _build_evidence_map(screening):
    out = []
    for r in screening:
        if r.get("decision") != "uncertain" and r.get("evidence_quote"):
            out.append({
                "record_id": r["record_id"],
                "locator": r.get("evidence_locator") or "unknown",
                "quote": r.get("evidence_quote"),
                "verified": r.get("evidence_verified"),
                "match_mode": r.get("evidence_match_mode"),
            })
    out.sort(key=lambda e: e["record_id"])
    return out


def _write_protocol_md(path, protocol, dedup, closure, inc, exc, unc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 检索与纳排协议（协议冻结后生成，确定性输出）",
        "",
        "- case_id: `%s`" % protocol.get("case_id", "unknown"),
        "- criteria_version: `%s`" % (protocol.get("criteria_version") or
                                      "not_set"),
        "",
        "## 研究问题",
        "",
        "`%s`" % (protocol.get("research_question") or "not_reported"),
        "",
        "## 纳入标准",
        "",
    ]
    inc_criteria = protocol.get("inclusion_criteria") or []
    if inc_criteria:
        for c in inc_criteria:
            lines.append("- " + str(c))
    else:
        lines.append("- not_reported")
    lines += ["", "## 排除标准", ""]
    exc_criteria = protocol.get("exclusion_criteria") or []
    if exc_criteria:
        for c in exc_criteria:
            lines.append("- " + str(c))
    else:
        lines.append("- not_reported")
    lines += [
        "",
        "## 计数闭合",
        "",
        "- 检索输入 n_input: %d" % (dedup or {}).get("n_input", 0),
        "- 去重合并 n_merged: %d" % (dedup or {}).get("n_merged", 0),
        "- 唯一记录 n_unique: %d" % (dedup or {}).get("n_unique", 0),
        "- 纳入 %d / 排除 %d / 待定 %d" % (inc, exc, unc),
        "- closure: %s" % closure,
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def cmd_selfcheck(input_dir, output_dir, work_dir):
    input_dir, output_dir, work_dir = (Path(input_dir), Path(output_dir),
                                       Path(work_dir))
    failures = []
    checks = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        if not ok:
            failures.append(name)

    result = load_json(output_dir / "result.json")
    if result is None:
        check("result.json exists", False, "missing")
    else:
        check("result.json has case_id", bool(result.get("case_id")), "")
        check("result.json has status", bool(result.get("status")), "")
        check("result.json has change_log",
              isinstance(result.get("change_log"), list), "")
        audit = result.get("audit", {})
        check("audit closure", bool(audit.get("closure")),
              "; ".join(audit.get("closure_gaps", [])))

    # Evidence: every non-uncertain decision has a verified verbatim quote.
    dedup = load_json(work_dir / "dedup.json") or {}
    record_by_id = {r["record_id"]: r for r in dedup.get("records", [])}
    bad_evidence = []
    for rec in (result or {}).get("screening", {}).get("records", []):
        if rec.get("decision") in ("include", "exclude"):
            quote = rec.get("evidence_quote") or ""
            loc = rec.get("evidence_locator") or ""
            verified, _, mode = verify_evidence(quote, record_by_id.get(
                rec["record_id"], {}), loc)
            if not quote or not verified:
                bad_evidence.append(rec["record_id"])
    check("evidence quotes verified", not bad_evidence,
          "; ".join(bad_evidence[:5]))

    # Determinism: re-finalize from the same work state into a temp dir.
    try:
        with tempfile.TemporaryDirectory() as tmp:
            cmd_finalize(input_dir, tmp, work_dir)
            again = load_json(Path(tmp) / "result.json")
            if again is not None:
                check("determinism (re-finalize identical)",
                      canonical_json(result) == canonical_json(again), "")
            else:
                check("determinism (re-finalize identical)", False,
                      "re-finalize produced no result")
    except OSError as exc:
        check("determinism (re-finalize identical)", False, str(exc))

    # Input integrity: fingerprint unchanged since skeleton.
    protocol = load_json(work_dir / "protocol.json") or {}
    current_fp = input_fingerprint(input_dir)
    check("input fingerprint unchanged",
          protocol.get("fingerprint") == current_fp,
          "input changed since skeleton" if protocol.get("fingerprint")
          != current_fp else "")

    print("selfcheck: %d checks, %d failures" % (len(checks), len(failures)))
    for name, ok, detail in checks:
        print("  %s %s %s" % ("PASS" if ok else "FAIL", name,
                               ("-- " + detail) if detail else ""))
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Deterministic literature-screening pipeline (self-check "
                    "& determinism variant).")
    parser.add_argument("subcommand", choices=sorted(SUBCOMMANDS))
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--work", default=DEFAULT_WORK)
    args = parser.parse_args(argv)

    funcs = {
        "skeleton": cmd_skeleton,
        "normalize": cmd_normalize,
        "finalize": cmd_finalize,
        "selfcheck": cmd_selfcheck,
    }
    return funcs[args.subcommand](args.input, args.output, args.work)


SUBCOMMANDS = {"skeleton", "normalize", "finalize", "selfcheck"}

if __name__ == "__main__":
    raise SystemExit(main())
