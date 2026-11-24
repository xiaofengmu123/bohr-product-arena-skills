# -*- coding: utf-8 -*-
"""core_impl.py - implementation for the systematic literature screening skill.

Scientific judgments belong to the model; this module owns only the
reproducible ledger: input discovery, output-contract backfill, record
normalization, deduplication, evidence-quote verification, count closure and
deterministic serialization.

Standard library only. Same input -> same dedup keys, clusters, counts and
evidence verification. Timestamps are the only intentionally non-deterministic
audit fields.
"""

import argparse
import csv
import datetime as _dt
import difflib
import io
import json
import os
import re
import sys
import unicodedata

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_SIZE = 2 * 1024 * 1024  # 2MB

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFF = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"

# Keys that may hold the record list inside a JSON case file.
RECORD_KEYS = (
    "records", "papers", "literature", "articles", "references",
    "items", "documents", "candidates", "search_results", "results",
)
# Keys that may hold the output contract inside case.json.
CONTRACT_KEYS = (
    "output_contract", "outputContract", "result_schema", "result_fields",
    "output", "output_schema", "output_format",
)
# Keys that must not be treated as record containers during recursive search.
SKIP_KEYS = set(CONTRACT_KEYS) | {
    "scoring", "resources", "tags", "figures", "datasets", "figureData",
    "reviewStatus", "visibility", "status", "origin", "title", "title_zh",
    "submittedBy", "attempts", "difficulty", "armiId", "benchmarkId",
    "contentGated", "hackathon", "hackathonSeasonId", "roundEndAt",
    "roundId", "roundSeq", "roundStartAt", "hasContent", "id", "content",
    "disc", "grader_name", "protocol_url", "strategy", "expected_metrics",
    "formula_summary", "score_range",
}

RECORD_FIELD_ALIASES = {
    "record_id": ("record_id", "recordId", "id", "uid", "key", "pmid", "pmc_id"),
    "title": ("title", "Title", "ti", "TI"),
    "doi": ("doi", "DOI", "Doi", "doi_link", "url_doi"),
    "abstract": ("abstract", "Abstract", "ab", "AB", "summary", "abstract_text"),
    "authors": ("authors", "author", "Authors", "au", "AU"),
    "year": ("year", "Year", "py", "PY", "publication_year", "date"),
    "journal": ("journal", "journal_title", "source", "venue", "JO", "jo", "publication"),
}


def _utcnow():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def ws_norm(text):
    """Collapse every whitespace run to a single space and strip ends."""
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def normalize_title(title):
    """Canonical title key for dedup: NFC, lower, punctuation stripped."""
    if not title:
        return ""
    t = unicodedata.normalize("NFC", str(title))
    t = t.lower()
    t = t.replace("\u2010", "-").replace("\u2011", "-")
    t = re.sub(r"[\u2013\u2014\u2012]", "-", t)
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def normalize_doi(doi):
    """Canonical DOI key: lower, strip URL prefixes and trailing punctuation."""
    if not doi:
        return ""
    d = str(doi).strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    d = re.sub(r"^doi:\s*", "", d)
    d = d.strip().rstrip(".,;")
    return d


def title_similarity(a, b):
    a = normalize_title(a)
    b = normalize_title(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def read_text(path):
    """Read a text file with encoding fallbacks. Returns (text, encoding) or (None, None)."""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc, errors="strict") as fh:
                return fh.read(), enc
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError:
            return None, None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(), "utf-8(replace)"
    except OSError:
        return None, None
    return None, None


def load_json_any(text, locator):
    """Parse JSON, tolerating BOM and trailing garbage. Returns (obj, error)."""
    if text is None:
        return None, "unreadable"
    try:
        return json.loads(text), None
    except Exception as exc:
        pass
    # Retry with BOM stripped.
    try:
        return json.loads(text.lstrip("\ufeff")), None
    except Exception as exc:
        return None, "json_error: %s" % exc


def load_json_file(path):
    """Read a JSON file tolerating a UTF-8 BOM. Returns (obj, error)."""
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except (OSError, ValueError) as exc:
        return None, "json_error: %s" % exc


def _looks_like_record(d):
    return isinstance(d, dict) and any(
        k in d for k in ("title", "doi", "abstract", "authors", "TI", "AB", "AU")
    )


def collect_records(node, path, out):
    """Recursively locate record-like dicts, yielding (record, locator-pointer)."""
    if isinstance(node, list):
        if node and all(isinstance(x, dict) for x in node) and any(_looks_like_record(x) for x in node):
            for i, x in enumerate(node):
                out.append((x, "%s[%d]" % (path, i)))
            return
        for i, x in enumerate(node):
            collect_records(x, "%s[%d]" % (path, i), out)
    elif isinstance(node, dict):
        if _looks_like_record(node):
            out.append((node, path))
            return
        for k, v in node.items():
            if k in SKIP_KEYS:
                continue
            collect_records(v, "%s.%s" % (path, k), out)


def get_field(record, aliases, default=None):
    for a in aliases:
        if a in record:
            return record[a]
    return default


def _coerce_authors(value):
    if value is None:
        return None
    if isinstance(value, list):
        return [str(x) for x in value]
    s = str(value)
    if not s:
        return None
    if ";" in s:
        return [x.strip() for x in s.split(";") if x.strip()]
    return [x.strip() for x in s.split(" and ") if x.strip()]


def normalize_record(raw, source_file, pointer):
    """Map a raw dict to the internal record schema."""
    title = get_field(raw, RECORD_FIELD_ALIASES["title"], "")
    doi = get_field(raw, RECORD_FIELD_ALIASES["doi"], "")
    abstract = get_field(raw, RECORD_FIELD_ALIASES["abstract"], "")
    authors = _coerce_authors(get_field(raw, RECORD_FIELD_ALIASES["authors"]))
    year = get_field(raw, RECORD_FIELD_ALIASES["year"], None)
    journal = get_field(raw, RECORD_FIELD_ALIASES["journal"], None)
    record_id = get_field(raw, RECORD_FIELD_ALIASES["record_id"], None)
    return {
        "record_id": str(record_id) if record_id is not None else None,
        "title": ws_norm(title) if title else "",
        "doi": str(doi).strip() if doi else "",
        "abstract": ws_norm(abstract) if abstract else "",
        "authors": authors,
        "year": str(year) if year is not None else None,
        "journal": journal,
        "source_locator": {
            "file": source_file,
            "pointer": pointer,
        },
        "raw": {k: (v if isinstance(v, (str, int, float, bool, list)) else str(v))
                for k, v in raw.items() if k in ("title", "doi", "abstract", "authors", "year", "journal", "id", "record_id")},
    }


def parse_csv_records(text, source_file):
    """Parse CSV/TSV text into raw record dicts. Returns list of (raw, pointer)."""
    out = []
    if text is None:
        return out
    try:
        sample = text[:2048]
        delim = "\t" if sample.count("\t") > sample.count(",") else ","
        reader = csv.DictReader(io.StringIO(text), delimiter=delim)
        for i, row in enumerate(reader):
            if not any((row.get(k) or "").strip() for k in row):
                continue
            out.append((dict(row), "line_%d" % (i + 2)))
    except Exception:
        return out
    return out


def parse_ris_records(text, source_file):
    """Parse RIS text into raw record dicts."""
    out = []
    if not text:
        return out
    cur = None
    order = 0
    for line in text.splitlines():
        line = line.rstrip("\r")
        if line.startswith("TY  -"):
            if cur:
                out.append((cur, "record_%d" % order))
                order += 1
            cur = {}
            cur["RIS_type"] = line[6:].strip()
        elif cur is not None and line.startswith("TI  -"):
            cur["title"] = (cur.get("title", "") + " " + line[6:].strip()).strip()
        elif cur is not None and line.startswith("AB  -"):
            cur["abstract"] = (cur.get("abstract", "") + " " + line[6:].strip()).strip()
        elif cur is not None and line.startswith("AU  -"):
            cur.setdefault("authors", []).append(line[6:].strip())
        elif cur is not None and line.startswith("DO  -"):
            cur["doi"] = line[6:].strip()
        elif cur is not None and line.startswith("PY  -"):
            cur["year"] = line[6:].strip()
        elif cur is not None and line.startswith("JO  -") or (cur is not None and line.startswith("T2  -")):
            cur["journal"] = line[6:].strip()
    if cur:
        out.append((cur, "record_%d" % order))
    return out


def parse_bib_records(text, source_file):
    """Lightweight BibTeX parser for @article/@inproceedings entries."""
    out = []
    if not text:
        return out
    pattern = re.compile(
        r"@(?P<type>\w+)\s*\{\s*(?P<key>[^,\s]+)\s*,(?P<body>.*?)\n\s*\}",
        re.DOTALL,
    )
    field = re.compile(r"(?P<name>\w+)\s*=\s*(?:\{(?P<braced>.*?)\}|\"(?P<quoted>.*?)\")", re.DOTALL)
    order = 0
    for m in pattern.finditer(text):
        body = m.group("body")
        raw = {"bib_key": m.group("key"), "bib_type": m.group("type")}
        for fm in field.finditer(body):
            name = fm.group("name").lower()
            value = fm.group("braced") if fm.group("braced") is not None else fm.group("quoted")
            value = re.sub(r"\s+", " ", value or "").strip()
            if name == "title":
                raw["title"] = value
            elif name == "abstract":
                raw["abstract"] = value
            elif name == "doi":
                raw["doi"] = value
            elif name == "author":
                raw["authors"] = value
            elif name == "year":
                raw["year"] = value
            elif name == "journal":
                raw["journal"] = value
        out.append((raw, "bib_%s" % m.group("key")))
        order += 1
    return out


def parse_jsonl_records(text, source_file):
    out = []
    if not text:
        return out
    for i, line in enumerate(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        obj, err = load_json_any(line, "%s:line_%d" % (source_file, i + 1))
        if err is None and isinstance(obj, dict) and _looks_like_record(obj):
            out.append((obj, "line_%d" % (i + 1)))
    return out


# ---------------------------------------------------------------------------
# Input discovery
# ---------------------------------------------------------------------------

def discover_input(input_dir):
    """Scan the input directory and return an inventory dict."""
    inventory = {
        "input_dir": input_dir,
        "files": [],
        "skipped": [],
        "case_json": None,
        "task_md": None,
        "notes_md": None,
        "materials_dir": None,
        "record_files": [],
        "degradations": [],
    }
    if not os.path.isdir(input_dir):
        inventory["degradations"].append("input_dir_missing: %s" % input_dir)
        return inventory
    for root, dirs, files in os.walk(input_dir):
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            if size > MAX_FILE_SIZE:
                inventory["skipped"].append({"file": rel, "reason": "size_gt_2MB", "size": size})
                continue
            lower = name.lower()
            entry = {"file": rel, "path": full, "size": size}
            if lower == "case.json":
                inventory["case_json"] = entry
            elif lower == "task.md":
                inventory["task_md"] = entry
            elif lower in ("notes.md", "note.md", "notes.txt"):
                inventory["notes_md"] = entry
            elif lower.endswith((".csv", ".tsv", ".ris", ".bib", ".json", ".jsonl")):
                inventory["record_files"].append(entry)
            elif lower in ("resources.yaml", "environment.yaml", "resources.yml"):
                entry["role"] = "environment"
                inventory["files"].append(entry)
            elif lower.endswith(".pdf"):
                entry["role"] = "pdf"
                inventory["files"].append(entry)
            else:
                inventory["files"].append(entry)
        for d in dirs:
            if d.lower() in ("materials", "material", "papers", "docs"):
                inventory["materials_dir"] = os.path.join(root, d)
    inventory["files"] = sorted(inventory["files"], key=lambda x: x["file"])
    inventory["record_files"] = sorted(inventory["record_files"], key=lambda x: x["file"])
    return inventory


def extract_output_contract(case_data, task_text):
    """Locate and return the output contract object (or None)."""
    if isinstance(case_data, dict):
        for key in CONTRACT_KEYS:
            if key in case_data and case_data[key] is not None:
                return case_data[key]
    if task_text:
        # Look for a JSON block labelled output_contract / result_schema.
        m = re.search(
            r"(?:output_contract|outputContract|result_schema|result_fields)\s*[:=]\s*(\{.*?\})\s*$",
            task_text,
            re.MULTILINE | re.DOTALL,
        )
        if m:
            obj, err = load_json_any(m.group(1), "task.md#output_contract")
            if err is None:
                return obj
    return None


def infer_contract_template(contract):
    """Preserve every contract key, filling null/[] placeholders without renaming."""
    if isinstance(contract, dict):
        out = {}
        for k, v in contract.items():
            if isinstance(v, list):
                out[k] = []
            elif isinstance(v, dict):
                out[k] = infer_contract_template(v) if v else {}
            elif isinstance(v, str) and (v.startswith("[") or v in ("list", "array", "records", "results", "items")):
                out[k] = []
            else:
                out[k] = None
        return out
    if isinstance(contract, list):
        if contract and all(isinstance(x, str) for x in contract):
            return {k: None for k in contract}
        return [infer_contract_template(x) if isinstance(x, (dict, list)) else None for x in contract]
    return {}


def extract_case_meta(case_data, task_text):
    """Pull case_id, research question and criteria text from available inputs."""
    meta = {
        "case_id": "unknown",
        "research_question": None,
        "inclusion_criteria": [],
        "exclusion_criteria": [],
        "search_query": None,
        "criteria_version": None,
        "raw_contract_locator": None,
    }
    if isinstance(case_data, dict):
        for key in ("case_id", "caseId", "caseID"):
            if key in case_data and case_data[key]:
                meta["case_id"] = str(case_data[key])
                break
        meta["research_question"] = (
            case_data.get("research_question")
            or case_data.get("researchQuestion")
            or case_data.get("question")
            or case_data.get("research_question_text")
        )
        meta["inclusion_criteria"] = case_data.get("inclusion_criteria") or case_data.get("inclusionCriteria") or []
        meta["exclusion_criteria"] = case_data.get("exclusion_criteria") or case_data.get("exclusionCriteria") or []
        meta["search_query"] = case_data.get("search_query") or case_data.get("query") or case_data.get("search_string")
        meta["criteria_version"] = case_data.get("criteria_version") or case_data.get("criteriaVersion")
    if meta["case_id"] == "unknown" and isinstance(case_data, dict) and case_data.get("id"):
        meta["case_id"] = str(case_data.get("id"))
    if task_text:
        m = re.search(r"challenge_id[:：\s`]+([A-Za-z0-9\-_]+)", task_text)
        if m and meta["case_id"] == "unknown":
            meta["case_id"] = m.group(1)
    return meta


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

def load_records(inventory):
    """Load and normalize records from case.json and record files. Returns (records, degradations)."""
    records = []
    degradations = []
    case_json = inventory.get("case_json")
    case_data = None
    if case_json:
        text, _enc = read_text(case_json["path"])
        if text is None:
            degradations.append("case.json unreadable: %s" % case_json["file"])
        else:
            obj, err = load_json_any(text, case_json["file"])
            if err:
                degradations.append("case.json parse error: %s" % err)
            else:
                case_data = obj
                collect_records(obj, case_json["file"], records)
    for entry in inventory.get("record_files", []):
        if entry is case_json:
            continue
        text, _enc = read_text(entry["path"])
        if text is None:
            degradations.append("record file unreadable: %s" % entry["file"])
            continue
        lower = entry["file"].lower()
        raw_list = []
        if lower.endswith(".csv") or lower.endswith(".tsv"):
            raw_list = parse_csv_records(text, entry["file"])
        elif lower.endswith(".ris"):
            raw_list = parse_ris_records(text, entry["file"])
        elif lower.endswith(".bib"):
            raw_list = parse_bib_records(text, entry["file"])
        elif lower.endswith(".jsonl"):
            raw_list = parse_jsonl_records(text, entry["file"])
        elif lower.endswith(".json"):
            obj, err = load_json_any(text, entry["file"])
            if err is None:
                collect_records(obj, entry["file"], raw_list)
        for raw, pointer in raw_list:
            records.append((raw, entry["file"], pointer))
    normalized = []
    seen = set()
    for item in records:
        if len(item) == 3:
            raw, src, pointer = item
        else:  # came from collect_records directly
            raw, pointer = item
            src = pointer
        rec = normalize_record(raw, src, pointer)
        # Fallback id by position so every record is addressable.
        idx = len(normalized)
        if not rec["record_id"]:
            rec["record_id"] = "rec_%04d" % idx
        key = (rec["record_id"], ws_norm(rec["title"]), normalize_doi(rec["doi"]))
        if key in seen:
            degradations.append("duplicate record_id detected, skipping: %s" % rec["record_id"])
            continue
        seen.add(key)
        normalized.append(rec)
    return normalized, degradations, case_data


def dedup(records):
    """Group records by DOI then normalized title; flag near-title possible duplicates."""
    clusters = []
    by_doi = {}
    by_title = {}
    for i, rec in enumerate(records):
        dk = normalize_doi(rec.get("doi"))
        if dk:
            by_doi.setdefault(dk, []).append(i)
        else:
            tk = normalize_title(rec.get("title"))
            if tk:
                by_title.setdefault(tk, []).append(i)
            else:
                clusters.append({"representative": i, "members": [i], "merge_reason": "single_unkeyed", "evidence": []})

    doi_used = set()
    for dk, idxs in by_doi.items():
        doi_used.update(idxs)
        rep = idxs[0]
        clusters.append({
            "representative": rep,
            "members": sorted(idxs),
            "merge_reason": "same_doi" if len(idxs) > 1 else "single",
            "evidence": [{"record_id": records[j]["record_id"], "doi": records[j]["doi"], "locator": records[j]["source_locator"]} for j in idxs],
        })
    title_used = set()
    for tk, idxs in by_title.items():
        # Records already claimed by a DOI cluster keep their own cluster.
        remaining = [j for j in idxs if j not in doi_used]
        if not remaining:
            continue
        title_used.update(remaining)
        rep = remaining[0]
        clusters.append({
            "representative": rep,
            "members": sorted(remaining),
            "merge_reason": "same_normalized_title" if len(remaining) > 1 else "single",
            "evidence": [{"record_id": records[j]["record_id"], "title": records[j]["title"], "locator": records[j]["source_locator"]} for j in remaining],
        })

    # Unclaimed records each get a singleton cluster.
    claimed = set()
    for c in clusters:
        claimed.update(c["members"])
    for i in range(len(records)):
        if i not in claimed:
            clusters.append({"representative": i, "members": [i], "merge_reason": "single", "evidence": []})

    clusters = sorted(clusters, key=lambda c: c["representative"])
    unique_records = []
    for c in clusters:
        rep = records[c["representative"]]
        # Prefer the member with both DOI and abstract as representative.
        best = c["representative"]
        for j in c["members"]:
            if records[j].get("doi") and records[j].get("abstract"):
                best = j
                break
        unique_records.append(records[best])
        c["representative_record_id"] = records[best]["record_id"]

    # Possible duplicates: similar but not merged titles across different clusters.
    possible = []
    titles = [(i, r["title"]) for i, r in enumerate(unique_records) if r["title"]]
    for a in range(len(titles)):
        for b in range(a + 1, len(titles)):
            ia, ta = titles[a]
            ib, tb = titles[b]
            sim = title_similarity(ta, tb)
            if 0.85 <= sim < 0.95:
                possible.append({
                    "record_a": unique_records[ia]["record_id"],
                    "record_b": unique_records[ib]["record_id"],
                    "similarity": round(sim, 4),
                    "action": "possible_duplicate",
                    "note": "not merged; needs human confirmation",
                })
    return clusters, unique_records, possible


# ---------------------------------------------------------------------------
# Evidence verification
# ---------------------------------------------------------------------------

def verify_quote(quote, candidates):
    """Check a quote appears verbatim in candidate texts. Returns verification dict.

    candidates: list of (field, text). Modes tried in order: exact substring,
    whitespace-normalized substring (case-sensitive), then casefolded.
    """
    if not quote or not str(quote).strip():
        return {"verified": False, "mode": "empty_quote", "matched_in": None}
    q = str(quote)
    q_ws = ws_norm(q)
    q_ws_fold = q_ws.lower()
    for field, text in candidates:
        if not text:
            continue
        t = str(text)
        if q in t:
            return {"verified": True, "mode": "exact", "matched_in": field}
        if q_ws in ws_norm(t):
            return {"verified": True, "mode": "ws_normalized", "matched_in": field}
        if q_ws_fold in ws_norm(t).lower():
            return {"verified": True, "mode": "ws_casefold", "matched_in": field}
    return {"verified": False, "mode": "not_found", "matched_in": None}


def record_text_candidates(rec):
    return [
        ("title", rec.get("title")),
        ("abstract", rec.get("abstract")),
        ("doi", rec.get("doi")),
        ("journal", rec.get("journal")),
    ]


def build_evidence_map(records, judgments_by_id, dedup_data):
    """Assemble a fine-grained evidence map keyed by evidence id."""
    emap = {}
    unique_records = dedup_data["unique_records"]

    def add_evidence(eid, record_id, field, quote, locator, texts):
        ver = verify_quote(quote, texts)
        emap[eid] = {
            "evidence_id": eid,
            "record_id": record_id,
            "field": field,
            "locator": locator,
            "quote": quote,
            "verification": ver,
        }
        return eid

    for rec in records:
        rid = rec["record_id"]
        jud = judgments_by_id.get(rid, {})
        refs = []
        q = jud.get("evidence_quote")
        if q:
            eid = "ev_%s_decision" % rid
            add_evidence(eid, rid, "decision", q, rec.get("source_locator"), record_text_candidates(rec))
            refs.append(eid)
        rc = jud.get("reason_code")
        if rc:
            eid = "ev_%s_reason_code" % rid
            add_evidence(eid, rid, "reason_code", rc, rec.get("source_locator"), record_text_candidates(rec))
            refs.append(eid)
        rec["evidence_refs"] = refs

    for c in dedup_data["clusters"]:
        if len(c["members"]) > 1:
            eid = "ev_dedup_%s" % c["representative_record_id"]
            quote = c["merge_reason"]
            loc = unique_records and None
            # Locate the representative record's source.
            rep_rec = next((r for r in records if r["record_id"] == c["representative_record_id"]), None)
            locator = rep_rec["source_locator"] if rep_rec else None
            emap[eid] = {
                "evidence_id": eid,
                "record_id": c["representative_record_id"],
                "field": "dedup_merge",
                "locator": locator,
                "quote": quote,
                "verification": {"verified": len(c["members"]) > 1, "mode": "structural", "matched_in": "cluster"},
                "cluster_members": [records[m]["record_id"] for m in c["members"]],
            }
    return emap


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def ensure_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except OSError as exc:
        return False


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=False)
    os.replace(tmp, path)


def safe_write_text(path, text, encoding="utf-8-sig"):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding=encoding, newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)


def run_skeleton(args):
    work = args.work
    out = args.output
    ensure_dir(work)
    ensure_dir(out)
    inventory = discover_input(args.input)
    change_log = [{"ts": _utcnow(), "step": "skeleton", "summary": "partial result skeleton written"}]
    errors = list(inventory.get("degradations", []))

    # Read case.json and task.md for contract + meta.
    case_data = None
    task_text = None
    if inventory.get("case_json"):
        text, _enc = read_text(inventory["case_json"]["path"])
        if text is not None:
            obj, err = load_json_any(text, inventory["case_json"]["file"])
            if err is None:
                case_data = obj
            else:
                errors.append("case.json parse error: %s" % err)
    if inventory.get("task_md"):
        task_text, _enc = read_text(inventory["task_md"]["path"])

    contract = extract_output_contract(case_data, task_text)
    contract_template = infer_contract_template(contract)
    meta = extract_case_meta(case_data, task_text)
    if contract is not None:
        meta["raw_contract_locator"] = "case.json#output_contract" if case_data else "task.md#output_contract"

    records, rec_degradations, case_data2 = load_records(inventory)
    errors.extend(rec_degradations)
    if case_data is None:
        case_data = case_data2

    # Determine honest status.
    has_input = bool(inventory.get("case_json") or inventory.get("task_md") or inventory.get("record_files") or inventory.get("files"))
    if not has_input:
        status = STATUS_INSUFF
    elif not records and not (contract or case_data or task_text):
        status = STATUS_INSUFF
    else:
        status = STATUS_PARTIAL

    # Build the result skeleton preserving all contract keys.
    result = {"case_id": meta["case_id"]}
    for k, v in contract_template.items():
        result[k] = v
    result["status"] = status
    result["sources"] = [{"file": f["file"], "size": f["size"], "role": "input"} for f in inventory["files"]]
    result["uncertainty"] = []
    result["pending_human"] = []
    result["change_log"] = change_log
    result["errors"] = errors
    result["degradations"] = list(inventory.get("degradations", []))
    result["runtime"] = {"started_at": _utcnow(), "budget_seconds": 900, "budget_requests": 64}

    # Work artifacts.
    write_json(os.path.join(work, "inventory.json"), inventory)
    write_json(os.path.join(work, "records.json"), {"records": records, "degradations": rec_degradations})
    write_json(os.path.join(work, "contract.json"), {"template": contract_template, "raw": contract})
    protocol = {
        "case_id": meta["case_id"],
        "research_question": meta["research_question"],
        "inclusion_criteria": meta["inclusion_criteria"],
        "exclusion_criteria": meta["exclusion_criteria"],
        "search_query": meta["search_query"],
        "criteria_version": meta["criteria_version"],
        "dedup_rules": ["doi_exact", "normalized_title_exact", "title_similarity_0.85_0.95_possible_only"],
        "reason_code_version": "1.0",
    }
    write_json(os.path.join(work, "protocol.json"), protocol)

    write_json(os.path.join(out, "result.json"), result)
    print(json.dumps({"command": "skeleton", "status": status, "records": len(records),
                      "contract_keys": len(contract_template), "result": os.path.join(out, "result.json")},
                     ensure_ascii=False, indent=2))
    return 0


def run_normalize(args):
    work = args.work
    ensure_dir(work)
    records_path = os.path.join(work, "records.json")
    if not os.path.isfile(records_path):
        print("error: records.json not found; run `skeleton` first", file=sys.stderr)
        return 1
    data, err = load_json_file(records_path)
    if err:
        print("error: %s" % err, file=sys.stderr)
        return 1
    records = data.get("records", [])
    clusters, unique_records, possible = dedup(records)
    dedup_data = {
        "input_count": len(records),
        "clusters": clusters,
        "unique_count": len(unique_records),
        "unique_records": unique_records,
        "possible_duplicates": possible,
        "merged_count": len(records) - len(unique_records),
    }
    write_json(os.path.join(work, "dedup.json"), dedup_data)
    # Judgment template keyed by record_id.
    template = []
    for rec in unique_records:
        template.append({
            "record_id": rec["record_id"],
            "title": rec["title"],
            "doi": rec["doi"],
            "decision": "",
            "reason_code": "",
            "evidence_quote": "",
            "reviewer_a": "",
            "reviewer_b": "",
            "conflict": {"is_conflict": False, "type": None, "detail": ""},
            "arbitration": "",
            "needs_human": False,
        })
    write_json(os.path.join(work, "judgments_template.json"), template)
    print(json.dumps({"command": "normalize", "input_count": len(records),
                      "unique_count": len(unique_records), "merged_count": len(records) - len(unique_records),
                      "possible_duplicates": len(possible)}, ensure_ascii=False, indent=2))
    return 0


def load_judgments(work, unique_records):
    """Load model-filled judgments.json; tolerate missing/invalid entries."""
    path = os.path.join(work, "judgments.json")
    by_id = {}
    if not os.path.isfile(path):
        return by_id, []
    obj, err = load_json_file(path)
    if err:
        return by_id, ["judgments.json parse error: %s" % err]
    items = obj if isinstance(obj, list) else obj.get("judgments", obj.get("records", []))
    for it in items:
        if isinstance(it, dict):
            rid = it.get("record_id") or it.get("id")
            if rid:
                by_id[str(rid)] = it
    return by_id, []


def run_finalize(args):
    work = args.work
    out = args.output
    ensure_dir(out)
    errors = []

    def _read_json(path, default):
        if not os.path.isfile(path):
            return default, ["missing: %s" % path]
        obj, err = load_json_file(path)
        if err:
            return default, ["parse error %s: %s" % (path, err)]
        return obj, []

    inventory, e1 = _read_json(os.path.join(work, "inventory.json"), {})
    contract_meta, e2 = _read_json(os.path.join(work, "contract.json"), {})
    protocol, e3 = _read_json(os.path.join(work, "protocol.json"), {})
    records_meta, e4 = _read_json(os.path.join(work, "records.json"), {"records": []})
    dedup_data, e5 = _read_json(os.path.join(work, "dedup.json"), {})
    errors.extend(e1 + e2 + e3 + e4 + e5)

    records = records_meta.get("records", [])
    unique_records = dedup_data.get("unique_records", records)
    clusters = dedup_data.get("clusters", [])
    possible = dedup_data.get("possible_duplicates", [])
    judgments_by_id, jerr = load_judgments(work, unique_records)
    errors.extend(jerr)

    # Assemble screening records.
    screening = []
    included = excluded = pending = 0
    for rec in unique_records:
        rid = rec["record_id"]
        jud = judgments_by_id.get(rid, {})
        decision = jud.get("decision", "")
        reason_code = jud.get("reason_code", "")
        quote = jud.get("evidence_quote", "")
        ver = verify_quote(quote, record_text_candidates(rec)) if quote else {"verified": False, "mode": "no_quote", "matched_in": None}
        if decision == "include":
            included += 1
        elif decision == "exclude":
            excluded += 1
        else:
            pending += 1
        screening.append({
            "record_id": rid,
            "title": rec.get("title"),
            "doi": rec.get("doi"),
            "decision": decision or "pending",
            "reason_code": reason_code or None,
            "evidence_quote": quote or None,
            "evidence_verified": ver,
            "locator": rec.get("source_locator"),
            "reviewer_a": jud.get("reviewer_a"),
            "reviewer_b": jud.get("reviewer_b"),
            "conflict": jud.get("conflict") or {"is_conflict": False, "type": None, "detail": ""},
            "arbitration": jud.get("arbitration"),
            "needs_human": bool(jud.get("needs_human")),
        })

    # Evidence map (finer-grained).
    evidence_map = build_evidence_map(records, judgments_by_id, dedup_data)

    # Conflicts summary.
    conflicts = [s for s in screening if (s["conflict"] or {}).get("is_conflict")]

    # Audit with count closure.
    retrieved = len(records)
    merged = dedup_data.get("merged_count", len(records) - len(unique_records))
    unique = len(unique_records)
    closure = (retrieved - merged == unique) and (unique == included + excluded + pending)
    audit = {
        "steps": [
            {"step": "retrieval", "input": None, "output": retrieved, "operator": "skill_agent", "ts": _utcnow()},
            {"step": "dedup", "input": retrieved, "output": unique, "merged": merged, "operator": "skill_agent", "ts": _utcnow()},
            {"step": "screening", "input": unique, "output": {"included": included, "excluded": excluded, "pending": pending}, "operator": "skill_agent", "ts": _utcnow()},
        ],
        "closure": {
            "retrieved": retrieved,
            "merged": merged,
            "unique": unique,
            "included": included,
            "excluded": excluded,
            "pending": pending,
            "equation_1_holds": (retrieved - merged == unique),
            "equation_2_holds": (unique == included + excluded + pending),
            "closed": closure,
        },
    }

    # Result.json preserving the contract template.
    contract_template = contract_meta.get("template", {})
    result = {"case_id": protocol.get("case_id", inventory.get("case_id", "unknown"))}
    for k, v in contract_template.items():
        result[k] = v
    result["status"] = STATUS_OK if (closure and pending == 0 and not errors) else STATUS_PARTIAL
    result["research_question"] = protocol.get("research_question")
    result["inclusion_criteria"] = protocol.get("inclusion_criteria", [])
    result["exclusion_criteria"] = protocol.get("exclusion_criteria", [])
    result["search_query"] = protocol.get("search_query")
    result["criteria_version"] = protocol.get("criteria_version")
    result["deduplication"] = {
        "input_count": retrieved,
        "merged_count": merged,
        "unique_count": unique,
        "clusters": [{"representative": c.get("representative_record_id"), "members": [records[m]["record_id"] for m in c.get("members", [])], "merge_reason": c.get("merge_reason"), "evidence": c.get("evidence")} for c in clusters],
        "possible_duplicates": possible,
    }
    result["screening"] = {"records": screening, "counts": {"included": included, "excluded": excluded, "pending": pending}}
    result["conflicts"] = conflicts
    result["audit"] = audit
    result["evidence_map"] = evidence_map
    result["sources"] = inventory.get("files", [])
    result["uncertainty"] = [s for s in screening if s["decision"] == "pending" and s["reason_code"] and str(s["reason_code"]).startswith("un_")]
    result["pending_human"] = [{"record_id": s["record_id"], "reason": s["reason_code"], "action": "confirm_decision"} for s in screening if s["needs_human"] or s["decision"] == "pending"]
    result["change_log"] = [{"ts": _utcnow(), "step": "finalize", "summary": "merged judgments and wrote final result"}]
    result["errors"] = errors
    result["degradations"] = inventory.get("degradations", [])
    result["limitations"] = []
    if not errors and pending == 0 and closure:
        result["limitations"].append("screening_complete_but_requires_human_final_confirm_for_needs_human_entries")
    result["runtime"] = {"finished_at": _utcnow(), "budget_seconds": 900, "budget_requests": 64}

    write_json(os.path.join(out, "result.json"), result)

    # screening_table.csv (utf-8-sig).
    cols = ["record_id", "title", "doi", "decision", "reason_code", "evidence_quote", "evidence_verified", "locator", "needs_human"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for s in screening:
        w.writerow([
            s["record_id"], s["title"], s["doi"], s["decision"], s["reason_code"] or "",
            s["evidence_quote"] or "", json.dumps(s["evidence_verified"], ensure_ascii=False),
            json.dumps(s["locator"], ensure_ascii=False), s["needs_human"],
        ])
    safe_write_text(os.path.join(out, "screening_table.csv"), buf.getvalue(), "utf-8-sig")

    # audit_log.json
    write_json(os.path.join(out, "audit_log.json"), audit)

    # protocol.md
    lines = ["# 筛选协议（protocol）", "",
             "- case_id: `%s`" % result["case_id"],
             "- 研究问题: %s" % (result["research_question"] or "not_reported"),
             "- criteria_version: %s" % (result["criteria_version"] or "not_reported"),
             "", "## 纳入标准", ""]
    for c in result["inclusion_criteria"]:
        lines.append("- %s" % c)
    lines += ["", "## 排除标准", ""]
    for c in result["exclusion_criteria"]:
        lines.append("- %s" % c)
    lines += ["", "## 检索式", "", "```", (result["search_query"] or "not_reported"), "```",
              "", "## 去重规则", "", "- DOI 精确去重", "- 规范化标题精确去重",
              "- 标题相似度 0.85–0.95 仅标记 possible_duplicate，不自动合并",
              "", "## 计数闭合", "",
              "| 环节 | 输入 | 输出 |", "|---|---|---|",
              "| 检索 | - | %d |" % retrieved,
              "| 去重 | %d | %d（合并 %d）|" % (retrieved, unique, merged),
              "| 纳排 | %d | 纳入 %d / 排除 %d / 待定 %d |" % (unique, included, excluded, pending),
              "", "闭合: %s" % ("是" if closure else "否")]
    safe_write_text(os.path.join(out, "protocol.md"), "\n".join(lines) + "\n", "utf-8")

    print(json.dumps({"command": "finalize", "closure": closure, "included": included,
                      "excluded": excluded, "pending": pending, "status": result["status"]}, ensure_ascii=False, indent=2))
    return 0 if closure else 0  # finalize never crashes on open counts; selfcheck flags it.


def run_selfcheck(args):
    work = args.work
    out = args.output
    checks = []
    def check(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    result_path = os.path.join(out, "result.json")
    if not os.path.isfile(result_path):
        check("result.json exists", False, "missing %s" % result_path)
    else:
        result, rerr = load_json_file(result_path)
        if rerr:
            check("result.json parses", False, rerr)
            result = {}
        check("result.json exists", True)
        check("case_id present", bool(result.get("case_id")), str(result.get("case_id")))
        check("status valid", result.get("status") in STATUSES, str(result.get("status")))
        # Contract key preservation.
        contract_meta = {}
        cp = os.path.join(work, "contract.json")
        if os.path.isfile(cp):
            cm, _err = load_json_file(cp)
            contract_meta = cm or {}
        template = contract_meta.get("template", {})
        missing = [k for k in template if k not in result]
        check("output_contract keys preserved", not missing, "missing: %s" % missing if missing else "all present")
        # Records have decision + reason_code.
        recs = result.get("screening", {}).get("records", [])
        bad = [r["record_id"] for r in recs if not r.get("decision")]
        check("every record has decision", not bad, str(bad[:5]))
        no_code = [r["record_id"] for r in recs if r.get("decision") not in ("pending",) and not r.get("reason_code")]
        check("non-pending records have reason_code", not no_code, str(no_code[:5]))
        # Evidence quotes verified.
        unverified = [r["record_id"] for r in recs if r.get("decision") not in ("pending",) and r.get("evidence_quote") and not (r.get("evidence_verified") or {}).get("verified")]
        check("evidence quotes verified", not unverified, str(unverified[:5]))
        # Count closure.
        audit = result.get("audit", {}).get("closure", {})
        check("count closure closed", audit.get("closed", False), json.dumps(audit, ensure_ascii=False))
        # Conflicts arbitrated.
        unarb = [c["record_id"] for c in result.get("conflicts", []) if not c.get("arbitration")]
        check("conflicts arbitrated", not unarb, str(unarb[:5]))
        # Evidence map present.
        check("evidence_map present", bool(result.get("evidence_map")), "%d entries" % len(result.get("evidence_map", {})))

    for c in checks:
        print("[%s] %s%s" % ("PASS" if c["ok"] else "FAIL", c["check"],
                             (" - " + c["detail"]) if c["detail"] else ""))
    nfail = sum(1 for c in checks if not c["ok"])
    print("selfcheck: %d/%d PASS" % (len(checks) - nfail, len(checks)))
    return 0 if nfail == 0 else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Systematic literature screening: reproducible ledger + evidence tracing.",
    )
    parser.add_argument("command", nargs="?", default="selfcheck",
                        choices=("skeleton", "normalize", "finalize", "selfcheck", "all"))
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--work", default=DEFAULT_WORK)
    args = parser.parse_args(argv)

    commands = {
        "skeleton": run_skeleton,
        "normalize": run_normalize,
        "finalize": run_finalize,
        "selfcheck": run_selfcheck,
    }
    if args.command == "all":
        for cmd in ("skeleton", "normalize", "finalize", "selfcheck"):
            rc = commands[cmd](args)
            if rc != 0 and cmd != "selfcheck":
                print("stopping at %s (exit %d)" % (cmd, rc), file=sys.stderr)
                return rc
        return 0
    try:
        return commands[args.command](args)
    except Exception as exc:
        # Never let a crash leave no artifact: attempt a minimal degraded result.
        print("unhandled error in %s: %s" % (args.command, exc), file=sys.stderr)
        try:
            out = args.output
            ensure_dir(out)
            rp = os.path.join(out, "result.json")
            if not os.path.isfile(rp):
                write_json(rp, {
                    "case_id": "unknown", "status": STATUS_INPUT_ERROR,
                    "sources": [], "uncertainty": [], "change_log": [
                        {"ts": _utcnow(), "step": "crash_guard", "summary": "minimal degraded result"}],
                    "errors": [str(exc)], "degradations": [], "limitations": ["crash_guard_fallback"],
                })
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
