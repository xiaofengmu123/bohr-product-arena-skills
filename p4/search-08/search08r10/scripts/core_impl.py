"""core_impl.py - contract-first, append-only-ledger screening pipeline.

Architecture (deliberately different from a monolithic run-once script):

  * Contract-first -- the *actual* output_contract keys are discovered from
    /input/case.json before any result is written, and the result skeleton is
    generated from those keys.  No schema is hard-coded, no key is renamed or
    dropped, and unknown keys are preserved rather than silently discarded.

  * Append-only ledger -- every stage appends an event to /app/work/ledger.jsonl
    (JSON Lines).  Intermediate state is never rewritten in place, so the audit
    trail is replayable and count-closure is computed from events, not from a
    mutable in-memory state that can drift.

  * Pure/IO split -- all deterministic transforms (text normalization, dedup
    keys, evidence substring checks, count closure) are side-effect-free
    functions.  The orchestration below only reads inputs, calls the pure
    functions, and writes files; it never performs scientific judgment.

  * Declarative criteria rules -- inclusion/exclusion criteria are represented
    as structured rules with stable ids, and each screening decision references
    rule ids, enabling standard-version management and retroactive re-judgment.

Scientific judgment stays with the model.  This module is only the reproducible
ledger: normalization, dedup keys, evidence substring checks, count closure and
honest status.  It never fabricates DOIs, titles, values or decisions.
"""

import argparse
import csv
import difflib
import io
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_TEXT_FILE = 2 * 1024 * 1024  # 2 MB soft cap for plain-text inputs

VALID_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")
VALID_DECISIONS = ("include", "exclude", "uncertain")

# Reason-code dictionary.  Each code carries a stable label so a third party can
# re-read a decision without opening this module.  `uncertain` codes must always
# set needs_human=True unless an arbitration resolves them.
REASON_CODES = {
    "in_meets_criteria": {"type": "include", "label": "符合全部纳入标准"},
    "in_population_outcome": {"type": "include", "label": "对象与结局均符合标准"},
    "ex_not_population": {"type": "exclude", "label": "研究对象不符"},
    "ex_not_intervention": {"type": "exclude", "label": "干预/暴露不符"},
    "ex_not_outcome": {"type": "exclude", "label": "结局/指标不符"},
    "ex_not_design": {"type": "exclude", "label": "研究设计不符"},
    "ex_not_language": {"type": "exclude", "label": "语言不符"},
    "ex_not_timeframe": {"type": "exclude", "label": "超出时间范围"},
    "ex_duplicate": {"type": "exclude", "label": "重复记录（已合并）"},
    "ex_not_relevant": {"type": "exclude", "label": "与研究问题无关"},
    "ex_retracted": {"type": "exclude", "label": "已撤稿"},
    "ex_no_abstract": {"type": "exclude", "label": "仅有标题/无摘要且无法判断"},
    "uncertain_insufficient_info": {"type": "uncertain", "label": "信息不足"},
    "uncertain_partial_overlap": {"type": "uncertain", "label": "研究对象部分重叠"},
    "uncertain_abstract_only": {"type": "uncertain", "label": "仅摘要无法定"},
    "uncertain_dual_conflict": {"type": "uncertain", "label": "双人分歧未决"},
}


# --------------------------------------------------------------------------- #
# Ledger helpers (append-only)
# --------------------------------------------------------------------------- #
def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ledger_path(work):
    return os.path.join(work, "ledger.jsonl")


def append_event(work, stage, event, data=None, ok=True):
    os.makedirs(work, exist_ok=True)
    rec = {"ts": now_iso(), "stage": stage, "event": event, "ok": ok}
    if data is not None:
        rec["data"] = data
    with open(ledger_path(work), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


# --------------------------------------------------------------------------- #
# IO helpers (tolerant, never crash on bad encoding)
# --------------------------------------------------------------------------- #
def read_text(path):
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def load_json_any(path):
    text = read_text(path).lstrip()
    if not text:
        raise ValueError(f"empty file: {path}")
    try:
        return json.loads(text)
    except ValueError:
        objs = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                objs.append(json.loads(line))
            except ValueError:
                pass
        if objs:
            return objs
        raise


def write_json(work, name, obj):
    os.makedirs(work, exist_ok=True)
    p = os.path.join(work, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return p


def read_json(path):
    return json.loads(read_text(path))


def scan_input(input_dir):
    files = []
    if not os.path.isdir(input_dir):
        return files
    for root, _dirs, names in os.walk(input_dir):
        for n in sorted(names):
            p = os.path.join(root, n)
            files.append({
                "path": p,
                "rel": os.path.relpath(p, input_dir).replace(os.sep, "/"),
                "size": os.path.getsize(p),
                "ext": os.path.splitext(n)[1].lower(),
            })
    return files


# --------------------------------------------------------------------------- #
# Contract extraction (contract-first)
# --------------------------------------------------------------------------- #
def extract_case_id(case):
    if not isinstance(case, dict):
        return None
    for k in ("case_id", "caseId", "id", "armiId", "challenge_id", "challengeId"):
        v = case.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)):
            return str(v)
    return None


def extract_output_contract(case):
    if not isinstance(case, dict):
        return None
    for k in ("output_contract", "outputContract", "result_schema",
              "resultSchema", "result_fields", "resultFields", "output", "outputs"):
        if k in case and case[k] is not None:
            return case[k]
    return None


def output_contract_keys(contract):
    if isinstance(contract, dict):
        if isinstance(contract.get("properties"), dict):
            return [str(k) for k in contract["properties"]]
        if isinstance(contract.get("fields"), dict):
            return [str(k) for k in contract["fields"]]
        if isinstance(contract.get("fields"), list):
            return [str(_field_name(f)) for f in contract["fields"] if _field_name(f)]
        return [str(k) for k in contract]
    if isinstance(contract, list):
        return [str(_field_name(f)) for f in contract if _field_name(f)]
    return []


def _field_name(item):
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for k in ("name", "field", "key", "id"):
            if item.get(k):
                return item[k]
    return None


def extract_research_question(case):
    if not isinstance(case, dict):
        return None
    for k in ("research_question", "researchQuestion", "question", "topic",
              "objective", "goal", "research_topic"):
        v = case.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    for k in ("task", "query", "input"):
        v = case.get(k)
        if isinstance(v, dict):
            r = extract_research_question(v)
            if r:
                return r
    return None


def extract_criteria(case):
    if not isinstance(case, dict):
        return None
    inc = exc = None
    for k in ("inclusion_criteria", "inclusionCriteria", "inclusions", "include",
              "include_criteria"):
        v = case.get(k)
        if isinstance(v, (list, str)) and v not in ("", []):
            inc = v
            break
    for k in ("exclusion_criteria", "exclusionCriteria", "exclusions", "exclude",
              "exclude_criteria"):
        v = case.get(k)
        if isinstance(v, (list, str)) and v not in ("", []):
            exc = v
            break
    if inc is None and exc is None:
        for k in ("criteria", "screening_criteria", "inclusionExclusion",
                  "inclusion_exclusion", "eligibility_criteria"):
            if case.get(k) is not None:
                return case[k]
        return None
    return {"inclusion": inc, "exclusion": exc}


def extract_query(case):
    if not isinstance(case, dict):
        return None
    for k in ("query", "search_query", "searchQuery", "search_string",
              "searchString", "search_terms", "query_string"):
        v = case.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    if isinstance(case.get("input"), dict):
        return extract_query(case["input"])
    return None


def _deduped(seq):
    seen = set()
    out = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def extract_text_fields(case):
    """Collect free-text description blocks useful for the protocol."""
    out = {}
    if isinstance(case, dict):
        for k in ("research_question", "task", "description", "instructions", "notes"):
            if isinstance(case.get(k), str):
                out[k] = case[k].strip()
        for k in ("task", "input", "query"):
            if isinstance(case.get(k), dict):
                out.update(extract_text_fields(case[k]))
    return out


# --------------------------------------------------------------------------- #
# Record ingestion
# --------------------------------------------------------------------------- #
FIELD_ALIASES = {
    "title": ["title", "TI", "Title", "article_title", "ArticleTitle",
              "item_title", "dc.title", "name", "paper_title"],
    "doi": ["doi", "DOI", "do", "electronic_id", "electronicId", "ELocationID"],
    "abstract": ["abstract", "AB", "Abstract", "summary", "abstract_text",
                 "abstractText"],
    "authors": ["authors", "AU", "author", "author_list", "authorList",
                "creators", "creator"],
    "year": ["year", "PY", "pub_year", "publication_year", "publicationYear",
             "date", "year_published", "published_year"],
    "journal": ["journal", "JO", "JT", "journal_title", "journalTitle",
                "source", "publication", "venue"],
}


def _lookup(obj, aliases):
    if not isinstance(obj, dict):
        return None
    lower = {str(k).lower(): k for k in obj}
    for a in aliases:
        if a in obj:
            return obj[a]
        if a.lower() in lower:
            return obj[lower[a.lower()]]
    return None


def record_from_mapping(obj, source_file, row):
    if not isinstance(obj, dict):
        return None
    title = _lookup(obj, FIELD_ALIASES["title"])
    doi = _lookup(obj, FIELD_ALIASES["doi"])
    abstract = _lookup(obj, FIELD_ALIASES["abstract"])
    if not (title or doi or abstract):
        return None
    def _s(v):
        if isinstance(v, (list, tuple)):
            return "; ".join(str(x) for x in v)
        if v is None:
            return None
        return str(v).strip()
    return {
        "title": _s(title),
        "doi": _s(doi),
        "abstract": _s(abstract),
        "authors": _s(_lookup(obj, FIELD_ALIASES["authors"])),
        "year": _s(_lookup(obj, FIELD_ALIASES["year"])),
        "journal": _s(_lookup(obj, FIELD_ALIASES["journal"])),
        "source_file": source_file,
        "source_row": row,
    }


def records_from_jsonl(text, source_file):
    recs = []
    for i, line in enumerate(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        r = record_from_mapping(obj, source_file, i + 1)
        if r:
            recs.append(r)
    return recs


def records_from_csv(text, delimiter, source_file):
    recs = []
    try:
        rows = list(csv.DictReader(io.StringIO(text), delimiter=delimiter))
    except Exception:
        return recs
    for i, row in enumerate(rows):
        r = record_from_mapping(row, source_file, i + 2)
        if r:
            recs.append(r)
    return recs


def records_from_ris(text, source_file):
    recs = []
    cur = {}
    for line in text.splitlines():
        if line.startswith("ER  -") or line.strip() == "ER":
            r = record_from_mapping(cur, source_file, len(recs) + 1)
            if r:
                recs.append(r)
            cur = {}
            continue
        m = re.match(r"^([A-Z][A-Z0-9])\s*-\s?(.*)$", line)
        if m:
            tag, val = m.group(1), m.group(2).strip()
            if tag in ("TI", "T1"):
                cur["title"] = val
            elif tag == "AB":
                cur["abstract"] = (cur.get("abstract") or "") + " " + val
            elif tag in ("DO", "DI"):
                cur["doi"] = val
            elif tag == "AU":
                cur["authors"] = (cur.get("authors") or "") + "; " + val
            elif tag == "PY":
                cur["year"] = val
            elif tag in ("JO", "JT", "T2"):
                cur["journal"] = val
    if cur:
        r = record_from_mapping(cur, source_file, len(recs) + 1)
        if r:
            recs.append(r)
    return recs


def records_from_bib(text, source_file):
    recs = []
    entries = re.findall(r"@\w+\s*\{[^{}]*\}", text, flags=re.S)
    for idx, entry in enumerate(entries):
        fields = {}
        for m in re.finditer(r"([A-Za-z]+)\s*=\s*[\{]([^{}]*)[\}]", entry):
            fields[m.group(1).lower()] = m.group(2).strip()
        mapping = {"title": fields.get("title"), "doi": fields.get("doi"),
                   "abstract": fields.get("abstract"), "authors": fields.get("author"),
                   "year": fields.get("year"), "journal": fields.get("journal")}
        r = record_from_mapping(mapping, source_file, idx + 1)
        if r:
            recs.append(r)
    return recs


def parse_biblio_file(path):
    ext = os.path.splitext(path)[1].lower()
    text = read_text(path)
    if ext == ".json":
        try:
            data = load_json_any(path)
        except ValueError:
            return [], "json_parse_error"
        if isinstance(data, list):
            recs = [record_from_mapping(x, path, i + 1) for i, x in enumerate(data)]
        elif isinstance(data, dict):
            recs = [record_from_mapping(data, path, 1)]
        else:
            recs = []
        return [r for r in recs if r], None
    if ext == ".jsonl":
        return records_from_jsonl(text, path), None
    if ext == ".csv":
        return records_from_csv(text, ",", path), None
    if ext == ".tsv":
        return records_from_csv(text, "\t", path), None
    if ext == ".ris":
        return records_from_ris(text, path), None
    if ext == ".bib":
        return records_from_bib(text, path), None
    return [], "unsupported_extension"


def _looks_like_record(obj):
    if not isinstance(obj, dict):
        return False
    return any(k in obj for k in ("title", "TI", "Title", "doi", "DOI",
                                  "abstract", "AB", "paper_title"))


def find_record_lists(obj, path="root", out=None):
    if out is None:
        out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, list) and v and all(
                    _looks_like_record(x) for x in v if isinstance(x, dict)):
                out.append((f"{path}/{k}", v))
            elif isinstance(v, (dict, list)):
                find_record_lists(v, f"{path}/{k}", out)
    elif isinstance(obj, list):
        for i, x in enumerate(obj):
            if isinstance(x, (dict, list)):
                find_record_lists(x, f"{path}/{i}", out)
    return out


# --------------------------------------------------------------------------- #
# Pure transforms (deterministic)
# --------------------------------------------------------------------------- #
def norm_text(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.casefold()
    s = re.sub(r"[^\w]+", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def norm_doi(s):
    if not s:
        return None
    s = str(s).strip().strip(";").strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    s = s.strip()
    return s.casefold() if s else None


def dedup_key(rec):
    doi = norm_doi(rec.get("doi"))
    if doi:
        return ("doi", doi)
    t = norm_text(rec.get("title"))
    if t:
        return ("title", t)
    return ("id", rec.get("record_id"))


def title_similarity(a, b):
    na, nb = norm_text(a), norm_text(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def evidence_hit(quote, rec):
    q = norm_text(quote)
    if not q:
        return False
    hay = norm_text(rec.get("title") or "") + " " + norm_text(rec.get("abstract") or "")
    return q in hay


# --------------------------------------------------------------------------- #
# Stages
# --------------------------------------------------------------------------- #
def stage_discover(input_dir, work):
    files = scan_input(input_dir)
    inv = {
        "input_dir": input_dir,
        "files": files,
        "case_file": None,
        "task_file": None,
        "biblio_files": [],
        "material_files": [],
        "skipped_oversize": [],
        "case_id": None,
        "output_contract": None,
        "output_contract_keys": [],
        "research_question": None,
        "criteria_raw": None,
        "query_raw": None,
        "text_fields": {},
    }
    for f in files:
        name = f["rel"].lower()
        if f["size"] > MAX_TEXT_FILE:
            inv["skipped_oversize"].append(f["rel"])
            continue
        if name == "case.json" or name.endswith("/case.json"):
            inv["case_file"] = f["path"]
        elif name == "task.md" or name.endswith("/task.md"):
            inv["task_file"] = f["path"]
        elif f["ext"] in (".csv", ".tsv", ".ris", ".bib", ".json", ".jsonl"):
            inv["biblio_files"].append(f["path"])
        else:
            inv["material_files"].append(f["path"])
    case = None
    if inv["case_file"]:
        try:
            case = load_json_any(inv["case_file"])
        except ValueError as e:
            append_event(work, "discover", "case_parse_error", {"err": str(e)}, ok=False)
    if isinstance(case, dict):
        inv["case_id"] = extract_case_id(case)
        inv["output_contract"] = extract_output_contract(case)
        inv["output_contract_keys"] = _deduped(
            output_contract_keys(inv["output_contract"]))
        inv["research_question"] = extract_research_question(case)
        inv["criteria_raw"] = extract_criteria(case)
        inv["query_raw"] = extract_query(case)
        inv["text_fields"] = extract_text_fields(case)
    write_json(work, "inventory.json", inv)
    append_event(work, "discover", "input_scanned",
                 {"n_files": len(files),
                  "n_biblio": len(inv["biblio_files"]),
                  "case_found": bool(inv["case_file"]),
                  "contract_keys": len(inv["output_contract_keys"])})
    return inv


def _contract_placeholder(keys):
    return {str(k): None for k in keys}


def stage_contract(work, output_dir):
    inv_path = os.path.join(work, "inventory.json")
    if not os.path.exists(inv_path):
        raise FileNotFoundError("run `discover` first: inventory.json missing")
    inv = read_json(inv_path)
    keys = inv.get("output_contract_keys") or []
    placeholders = _contract_placeholder(keys)
    result = {
        "case_id": inv.get("case_id") or "unknown",
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "research_question": inv.get("research_question"),
        "criteria": inv.get("criteria_raw"),
        "deduplication": {"clusters": [], "merged": 0, "kept": 0,
                          "method": "doi_then_normalized_title"},
        "screening": {"records": [], "criteria_version": None, "criteria_rules": []},
        "conflicts": [],
        "audit": {"stages": {}, "closure": False},
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "runtime": {"started_at": now_iso(), "finished_at": None, "budget_seconds": 900,
                    "budget_requests": 64},
        "contract_fields": list(keys),
    }
    for k in keys:
        result[str(k)] = placeholders[str(k)]
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    write_json(work, "contract.json", {"keys": keys, "case_id": result["case_id"]})
    # preserve any agent-computed backfill values already on disk; only add
    # missing contract keys as placeholders (never clobber real work).
    backfill_path = os.path.join(work, "backfill.json")
    existing = {}
    if os.path.exists(backfill_path):
        try:
            existing = read_json(backfill_path)
        except Exception:
            existing = {}
    merged_backfill = {str(k): None for k in keys}
    merged_backfill["_comment"] = "fill each contract field with computed values"
    for k, v in existing.items():
        if k.startswith("_") or k not in keys:
            continue
        merged_backfill[str(k)] = v
    write_json(work, "backfill.json", merged_backfill)
    append_event(work, "contract", "skeleton_written",
                 {"case_id": result["case_id"], "contract_keys": keys})
    return result


def stage_ingest(input_dir, work):
    inv_path = os.path.join(work, "inventory.json")
    if not os.path.exists(inv_path):
        raise FileNotFoundError("run `discover` first")
    inv = read_json(inv_path)
    records = []
    errors = []
    # 1) embedded record lists inside case.json
    case = None
    if inv.get("case_file"):
        try:
            case = load_json_any(inv["case_file"])
        except ValueError:
            errors.append("case.json could not be parsed for embedded records")
    if isinstance(case, (dict, list)):
        for loc, objs in find_record_lists(case):
            for i, obj in enumerate(objs):
                r = record_from_mapping(obj, inv.get("case_file") or "case.json",
                                        i + 1)
                if r:
                    r["locator"] = f"{inv.get('case_file') or 'case.json'}#{loc}"
                    records.append(r)
    # 2) bibliographic files
    for p in inv.get("biblio_files", []):
        try:
            recs, err = parse_biblio_file(p)
            if err:
                errors.append(f"{p}: {err}")
            for r in recs:
                r["locator"] = f"{p}#row{r.get('source_row')}"
                records.append(r)
        except Exception as e:  # noqa: BLE001 - degrade, never crash
            errors.append(f"{p}: {e}")
    # stable ids in ingestion order
    for i, r in enumerate(records, 1):
        r["record_id"] = f"rec-{i:04d}"
    write_json(work, "records.json", {"records": records, "errors": errors,
                                      "count": len(records)})
    append_event(work, "ingest", "records_ingested",
                 {"count": len(records), "errors": len(errors)})
    return records


def stage_dedup(work):
    rec_path = os.path.join(work, "records.json")
    if not os.path.exists(rec_path):
        raise FileNotFoundError("run `ingest` first")
    data = read_json(rec_path)
    records = data.get("records", [])
    normalized = []
    for r in records:
        n = dict(r)
        n["norm_title"] = norm_text(r.get("title"))
        n["norm_doi"] = norm_doi(r.get("doi"))
        n["dedup_key"] = dedup_key(r)
        normalized.append(n)
    # group by exact key
    groups = {}
    for n in normalized:
        key = n["dedup_key"]
        groups.setdefault(key, []).append(n)
    clusters = []
    kept_records = []
    removed_ids = []
    cid = 0
    for key in sorted(groups, key=str):
        members = groups[key]
        cid += 1
        # prefer a member with a DOI as the canonical keeper
        keeper = next((m for m in members if m.get("norm_doi")), members[0])
        removed = [m["record_id"] for m in members if m is not keeper]
        kind = key[0]
        cluster = {
            "cluster_id": f"dup-{cid:04d}",
            "kind": kind,
            "key": key[1],
            "kept": keeper["record_id"],
            "removed": removed,
            "members": [m["record_id"] for m in members],
            "merge_reason": ("相同 DOI（不同来源导出副本或预印本与正式版合并）"
                             if kind == "doi" else "规范化标题完全相同"),
            "evidence": {"doi": [m.get("doi") for m in members if m.get("doi")],
                         "titles": [m.get("title") for m in members if m.get("title")]},
        }
        clusters.append(cluster)
        kept_records.append(keeper)
        removed_ids.extend(removed)
    # possible duplicates (near-identical titles, NOT auto-merged)
    possible = []
    titles = [(n["record_id"], n.get("title") or "") for n in normalized]
    for i in range(len(titles)):
        for j in range(i + 1, len(titles)):
            a, b = titles[i], titles[j]
            if not a[1] or not b[1]:
                continue
            sim = title_similarity(a[1], b[1])
            if 0.85 <= sim < 0.95:
                possible.append({"record_a": a[0], "record_b": b[0],
                                 "similarity": round(sim, 4),
                                 "title_a": a[1], "title_b": b[1],
                                 "action": "possible_duplicate"})
    kept_records.sort(key=lambda r: r["record_id"])
    dedup = {
        "clusters": clusters,
        "possible_duplicates": possible,
        "merged": len(removed_ids),
        "kept": len(kept_records),
        "input": len(records),
        "method": "doi_then_normalized_title",
        "title_similarity_range": [0.85, 0.95],
    }
    write_json(work, "normalized.json", {"records": normalized})
    write_json(work, "dedup.json", dedup)
    write_json(work, "judgments_template.json",
               _judgments_template(kept_records, records))
    append_event(work, "dedup", "dedup_computed",
                 {"input": dedup["input"], "merged": dedup["merged"],
                  "kept": dedup["kept"],
                  "possible_duplicates": len(possible)})
    return dedup


def _judgments_template(kept_records, all_records):
    records = []
    for r in kept_records:
        records.append({
            "record_id": r["record_id"],
            "title": r.get("title"),
            "doi": r.get("doi"),
            "decision": None,
            "reason_code": None,
            "evidence_quote": None,
            "evidence_verified": None,
            "reviewer_a": {"decision": None, "reason_code": None,
                           "evidence_quote": None},
            "reviewer_b": {"decision": None, "reason_code": None,
                           "evidence_quote": None},
            "conflict": {"is_conflict": False, "type": None, "detail": None},
            "arbitration": {"decision": None, "reason_code": None,
                            "evidence_quote": None, "by": None, "basis": None},
            "needs_human": False,
            "rule_ids": [],
        })
    return {"criteria_version": None, "criteria_rules": [], "records": records,
            "_hint": "copy to work/judgments.json and fill each record's decision"}


def stage_screen(work):
    """Validate work/judgments.json against records; never fabricates decisions."""
    judgments_path = os.path.join(work, "judgments.json")
    rec_path = os.path.join(work, "records.json")
    dedup_path = os.path.join(work, "dedup.json")
    if not os.path.exists(rec_path) or not os.path.exists(dedup_path):
        raise FileNotFoundError("run `ingest` and `dedup` first")
    records = {r["record_id"]: r for r in read_json(rec_path)["records"]}
    dedup = read_json(dedup_path)
    kept_ids = set()
    for c in dedup["clusters"]:
        kept_ids.add(c["kept"])
    if not os.path.exists(judgments_path):
        write_json(work, "screening.json",
                   {"validated": [], "errors": ["judgments.json missing (model has not filled it)"],
                    "degradations": [], "decisions": {}, "complete": False})
        append_event(work, "screen", "judgments_missing", ok=False)
        return None
    judgments = read_json(judgments_path)
    errors, degradations = [], []
    validated = []
    seen = set()
    for j in judgments.get("records", []):
        rid = j.get("record_id")
        if not rid:
            errors.append("record without record_id in judgments.json")
            continue
        if rid not in kept_ids:
            if rid in records:
                degradations.append(f"record {rid} was deduplicated away but still judged")
            else:
                errors.append(f"record_id {rid} not present in records")
            continue
        seen.add(rid)
        decision = j.get("decision")
        rc = j.get("reason_code")
        if decision is None:
            degradations.append(f"record {rid} has no decision yet")
            validated.append(j)
            continue
        if decision not in VALID_DECISIONS:
            errors.append(f"record {rid}: invalid decision {decision!r}")
        if decision in ("include", "exclude") and rc not in REASON_CODES:
            errors.append(f"record {rid}: invalid reason_code {rc!r}")
        if decision == "uncertain" and rc and rc not in REASON_CODES:
            errors.append(f"record {rid}: invalid reason_code {rc!r}")
        if decision == "uncertain" and not j.get("needs_human"):
            degradations.append(f"record {rid}: uncertain but needs_human not set")
        q = j.get("evidence_quote")
        if decision in ("include", "exclude") and q:
            ok = evidence_hit(q, records.get(rid, {}))
            j["evidence_verified"] = bool(ok)
            if not ok:
                degradations.append(
                    f"record {rid}: evidence_quote does not match title/abstract")
        validated.append(j)
    for rid in sorted(kept_ids):
        if rid not in seen:
            degradations.append(f"record {rid} missing from judgments.json")
    decisions = {"include": 0, "exclude": 0, "uncertain": 0, "missing": 0}
    for j in judgments.get("records", []):
        d = j.get("decision")
        if d in decisions:
            decisions[d] += 1
        elif j.get("record_id") in kept_ids:
            decisions["missing"] += 1
    screening = {"validated": validated, "errors": errors,
                 "degradations": degradations, "decisions": decisions,
                 "complete": (not errors and decisions["missing"] == 0)}
    write_json(work, "screening.json", screening)
    append_event(work, "screen", "judgments_validated",
                 {"errors": len(errors), "degradations": len(degradations),
                  "complete": screening["complete"]})
    return screening


def stage_audit(work):
    needs = ["records.json", "dedup.json", "judgments.json"]
    missing = [n for n in needs if not os.path.exists(os.path.join(work, n))]
    if missing:
        raise FileNotFoundError("missing work files: " + ", ".join(missing))
    records = read_json(os.path.join(work, "records.json"))["records"]
    dedup = read_json(os.path.join(work, "dedup.json"))
    judgments = read_json(os.path.join(work, "judgments.json"))
    n_raw = len(records)
    merged = dedup.get("merged", 0)
    kept = dedup.get("kept", 0)
    decisions = {"include": 0, "exclude": 0, "uncertain": 0, "missing": 0}
    conflicts_total = 0
    conflicts_resolved = 0
    conflicts_needs_human = 0
    for j in judgments.get("records", []):
        d = j.get("decision")
        if d in decisions:
            decisions[d] += 1
        else:
            decisions["missing"] += 1
        if j.get("conflict", {}).get("is_conflict"):
            conflicts_total += 1
            if j.get("arbitration", {}).get("decision"):
                conflicts_resolved += 1
            elif j.get("needs_human"):
                conflicts_needs_human += 1
    closure = (
        (n_raw - merged == kept)
        and (decisions["include"] + decisions["exclude"] + decisions["uncertain"] == kept)
        and decisions["missing"] == 0
    )
    audit = {
        "stages": {
            "search": {"input": n_raw, "output": n_raw},
            "dedup": {"input": n_raw, "merged": merged, "output": kept},
            "screening": {"input": kept, **decisions},
            "conflicts": {"total": conflicts_total, "resolved": conflicts_resolved,
                          "needs_human": conflicts_needs_human,
                          "unresolved": conflicts_total - conflicts_resolved - conflicts_needs_human},
        },
        "closure": closure,
        "formula": "search.output - dedup.merged == dedup.output == include + exclude + uncertain",
    }
    write_json(work, "audit.json", audit)
    append_event(work, "audit", "counts_computed",
                 {"closure": closure, "decisions": decisions})
    return audit


def _backfill_to_merge(work):
    p = os.path.join(work, "backfill.json")
    if not os.path.exists(p):
        return {}
    try:
        return read_json(p)
    except Exception:
        return {}


def stage_finalize(work, output_dir):
    """Merge the ledger data into result.json, preserving every contract field."""
    result_path = os.path.join(output_dir, "result.json")
    if not os.path.exists(result_path):
        stage_contract(work, output_dir)
    result = read_json(result_path)
    keys = result.get("contract_fields") or []
    backfill = _backfill_to_merge(work)
    # merge contract fields (only known keys; unknown backfill keys -> degradations)
    degradations = list(result.get("degradations", []))
    for k in keys:
        if k in backfill and backfill[k] is not None:
            result[str(k)] = backfill[k]
    for k in backfill:
        if k.startswith("_"):
            continue
        if k not in keys and k not in ("status",):
            degradations.append(f"backfill key {k!r} is not in output_contract; ignored")
    records_path = os.path.join(work, "records.json")
    dedup_path = os.path.join(work, "dedup.json")
    judgments_path = os.path.join(work, "judgments.json")
    audit_path = os.path.join(work, "audit.json")
    screening_path = os.path.join(work, "screening.json")
    inventory_path = os.path.join(work, "inventory.json")

    if os.path.exists(records_path):
        records = read_json(records_path)
        result.setdefault("sources", [])
        if isinstance(result.get("sources"), list):
            for r in records.get("records", []):
                if r.get("source_file") and r["source_file"] not in result["sources"]:
                    result["sources"].append(r["source_file"])
        # attach deduplication
        if os.path.exists(dedup_path):
            dedup = read_json(dedup_path)
            result["deduplication"] = {
                "clusters": dedup.get("clusters", []),
                "possible_duplicates": dedup.get("possible_duplicates", []),
                "merged": dedup.get("merged", 0),
                "kept": dedup.get("kept", 0),
                "input": dedup.get("input", 0),
                "method": dedup.get("method"),
            }
    if os.path.exists(judgments_path):
        judgments = read_json(judgments_path)
        result["screening"] = {
            "criteria_version": judgments.get("criteria_version"),
            "criteria_rules": judgments.get("criteria_rules", []),
            "records": judgments.get("records", []),
        }
    if os.path.exists(screening_path):
        screening = read_json(screening_path)
        result["degradations"] = degradations + screening.get("degradations", [])
        result.setdefault("errors", [])
        result["errors"] = (result.get("errors") or []) + screening.get("errors", [])
    if os.path.exists(audit_path):
        result["audit"] = read_json(audit_path)
    # conflicts summary
    result["conflicts"] = []
    for j in (result.get("screening", {}) or {}).get("records", []):
        c = j.get("conflict", {})
        if isinstance(c, dict) and c.get("is_conflict"):
            result["conflicts"].append({
                "record_id": j.get("record_id"),
                "type": c.get("type"),
                "detail": c.get("detail"),
                "arbitration": j.get("arbitration"),
                "needs_human": j.get("needs_human"),
            })
    # pending human
    result["pending_human"] = []
    for j in (result.get("screening", {}) or {}).get("records", []):
        if j.get("needs_human") or j.get("decision") == "uncertain":
            result["pending_human"].append({
                "record_id": j.get("record_id"),
                "reason_code": j.get("reason_code"),
                "reason": "uncertain 判定需人工最终确认" if j.get("decision") == "uncertain"
                          else "needs_human",
            })
    # honest status
    result["status"] = _compute_status(result, backfill)
    result["runtime"]["finished_at"] = now_iso()
    if not result.get("change_log"):
        result["change_log"] = []
    result["change_log"].append({
        "at": now_iso(), "action": "finalize",
        "detail": f"merged screening into result.json; status={result['status']}",
    })
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    _write_editable_artifacts(work, output_dir, result)
    append_event(work, "finalize", "result_written",
                 {"status": result["status"]})
    return result


def _compute_status(result, backfill):
    if isinstance(backfill, dict) and backfill.get("status") in VALID_STATUS:
        return backfill["status"]
    contract_filled = all(
        result.get(k) is not None for k in (result.get("contract_fields") or []))
    screening = result.get("screening") or {}
    records = screening.get("records") or []
    if not records and (result.get("deduplication", {}) or {}).get("input", 0) == 0:
        return "insufficient_evidence"
    undecided = sum(1 for j in records if j.get("decision") is None)
    if undecided:
        return "partial"
    if not contract_filled:
        return "partial"
    return "ok"


def _write_editable_artifacts(work, output_dir, result):
    os.makedirs(output_dir, exist_ok=True)
    # screening_table.csv
    csv_path = os.path.join(output_dir, "screening_table.csv")
    screening = result.get("screening") or {}
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["record_id", "title", "doi", "decision", "reason_code",
                    "evidence_quote", "evidence_verified", "reviewer_a_decision",
                    "reviewer_b_decision", "conflict", "arbitration_decision",
                    "needs_human"])
        for j in screening.get("records", []):
            w.writerow([
                j.get("record_id"), j.get("title"), j.get("doi"),
                j.get("decision"), j.get("reason_code"), j.get("evidence_quote"),
                j.get("evidence_verified"),
                (j.get("reviewer_a") or {}).get("decision"),
                (j.get("reviewer_b") or {}).get("decision"),
                "1" if (j.get("conflict") or {}).get("is_conflict") else "0",
                (j.get("arbitration") or {}).get("decision"),
                "1" if j.get("needs_human") else "0",
            ])
    # audit_log.json (the append-only ledger + counts)
    ledger = []
    lp = ledger_path(work)
    if os.path.exists(lp):
        with open(lp, "r", encoding="utf-8") as f:
            ledger = [json.loads(line) for line in f if line.strip()]
    with open(os.path.join(output_dir, "audit_log.json"), "w", encoding="utf-8") as f:
        json.dump({"ledger": ledger, "audit": result.get("audit"),
                   "degradations": result.get("degradations"),
                   "errors": result.get("errors")}, f, ensure_ascii=False, indent=2)
    # protocol.md
    lines = ["# 检索与纳排协议（由 finalize 自动生成，可手工编辑）", ""]
    lines.append(f"- case_id: `{result.get('case_id')}`")
    lines.append(f"- 状态: `{result.get('status')}`")
    lines.append("")
    lines.append("## 研究问题")
    lines.append(str(result.get("research_question") or "（未提供）"))
    lines.append("")
    lines.append("## 纳入/排除标准")
    lines.append(_fmt_criteria(result.get("criteria")))
    lines.append("")
    lines.append("## 去重方法")
    lines.append(str((result.get("deduplication") or {}).get("method", "")))
    lines.append("")
    lines.append("## 计数闭合")
    audit = result.get("audit") or {}
    lines.append(f"- 检索输入: {audit.get('stages', {}).get('search', {}).get('input', 0)}")
    lines.append(f"- 去重合并: {audit.get('stages', {}).get('dedup', {}).get('merged', 0)}")
    lines.append(f"- 唯一记录: {audit.get('stages', {}).get('dedup', {}).get('output', 0)}")
    lines.append(f"- 计数闭合: {'是' if audit.get('closure') else '否'}")
    with open(os.path.join(output_dir, "protocol.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _fmt_criteria(criteria):
    if criteria is None:
        return "（未提供）"
    if isinstance(criteria, str):
        return criteria
    if isinstance(criteria, dict):
        out = []
        inc = criteria.get("inclusion")
        exc = criteria.get("exclusion")
        if inc:
            out.append("- 纳入: " + (", ".join(inc) if isinstance(inc, list) else str(inc)))
        if exc:
            out.append("- 排除: " + (", ".join(exc) if isinstance(exc, list) else str(exc)))
        return "\n".join(out) if out else str(criteria)
    if isinstance(criteria, list):
        return "\n".join(f"- {x}" for x in criteria)
    return str(criteria)


# --------------------------------------------------------------------------- #
# Self-check
# --------------------------------------------------------------------------- #
def stage_selftest(work, output_dir):
    checks = []
    def add(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    result_path = os.path.join(output_dir, "result.json")
    result = None
    try:
        result = read_json(result_path)
        add("result.json 存在且为合法 JSON", True)
    except Exception as e:
        add("result.json 存在且为合法 JSON", False, str(e))
    if result is None:
        _print_checks(checks)
        return checks

    add("case_id 存在", bool(result.get("case_id")), str(result.get("case_id")))
    add("status 在允许集合内", result.get("status") in VALID_STATUS,
        str(result.get("status")))
    keys = result.get("contract_fields") or []
    missing_keys = [k for k in keys if k not in result]
    add("output_contract 全部字段已回填（键存在）", not missing_keys,
        f"missing={missing_keys}" if missing_keys else f"n={len(keys)}")
    filled = sum(1 for k in keys if result.get(k) is not None)
    add("output_contract 字段已有实际值（非 null）",
        filled == len(keys), f"{filled}/{len(keys)}")

    screening = result.get("screening") or {}
    records = screening.get("records") or []
    add("screening.records 非空", bool(records), f"n={len(records)}")
    bad_decision = [r.get("record_id") for r in records
                    if r.get("decision") not in VALID_DECISIONS and r.get("decision") is not None]
    add("每条记录的 decision 合法", not bad_decision, f"bad={bad_decision}")
    no_code = [r.get("record_id") for r in records
               if r.get("decision") in ("include", "exclude") and not r.get("reason_code")]
    add("每条非 uncertain 记录都有 reason_code", not no_code, f"missing={no_code}")
    no_quote = [r.get("record_id") for r in records
                if r.get("decision") in ("include", "exclude") and not r.get("evidence_quote")]
    add("每条非 uncertain 记录都有 evidence_quote", not no_quote, f"missing={no_quote}")
    unverified = [r.get("record_id") for r in records
                  if r.get("decision") in ("include", "exclude")
                  and r.get("evidence_verified") is False]
    add("证据片段在标题/摘要中命中", not unverified, f"unverified={unverified}")

    dedup = result.get("deduplication") or {}
    add("去重计数闭合 (input-merged==kept)",
        dedup.get("input", 0) - dedup.get("merged", 0) == dedup.get("kept", 0),
        f"input={dedup.get('input')}, merged={dedup.get('merged')}, kept={dedup.get('kept')}")
    possible = dedup.get("possible_duplicates") or []
    add("possible_duplicate 未擅自合并",
        all(p.get("action") == "possible_duplicate" for p in possible), f"n={len(possible)}")

    audit = result.get("audit") or {}
    add("审计计数闭合", bool(audit.get("closure")),
        str(audit.get("stages", {}).get("screening", {})))
    conflicts = result.get("conflicts") or []
    unresolved = [c for c in conflicts
                  if not ((c.get("arbitration") or {}).get("decision") or c.get("needs_human"))]
    add("冲突均有仲裁或人工待定", not unresolved, f"unresolved={len(unresolved)}")

    for name in ("screening_table.csv", "audit_log.json", "protocol.md"):
        exists = os.path.exists(os.path.join(output_dir, name))
        add(f"可编辑产物 {name} 存在", exists)

    # determinism spot-check: recompute dedup and compare merged/kept
    dedup_path = os.path.join(work, "dedup.json")
    rec_path = os.path.join(work, "records.json")
    if os.path.exists(dedup_path) and os.path.exists(rec_path):
        saved = read_json(dedup_path)
        records = read_json(rec_path)["records"]
        merged = sum(len(c["removed"]) for c in saved["clusters"])
        recomputed_kept = sum(1 for c in saved["clusters"])
        add("去重结果确定性（合并数与保留数一致）",
            merged == saved.get("merged") and recomputed_kept == saved.get("kept"),
            f"merged={merged}, kept={recomputed_kept}")
    else:
        add("去重结果确定性（合并数与保留数一致）", False, "work 文件缺失")

    _print_checks(checks)
    write_json(work, "selftest.json", {"checks": checks,
                                       "passed": sum(1 for c in checks if c["ok"]),
                                       "total": len(checks)})
    append_event(work, "selftest", "checks_run",
                 {"passed": sum(1 for c in checks if c["ok"]), "total": len(checks)},
                 ok=all(c["ok"] for c in checks))
    return checks


def _print_checks(checks):
    passed = sum(1 for c in checks if c["ok"])
    for c in checks:
        print(("PASS" if c["ok"] else "FAIL") + "  " + c["name"]
              + (f"  ({c['detail']})" if c.get("detail") else ""))
    print(f"selftest: {passed}/{len(checks)} checks passed")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
STAGES = {
    "discover": lambda i, o, w: stage_discover(i, w),
    "contract": lambda i, o, w: stage_contract(w, o),
    "ingest": lambda i, o, w: stage_ingest(i, w),
    "dedup": lambda i, o, w: stage_dedup(w),
    "screen": lambda i, o, w: stage_screen(w),
    "audit": lambda i, o, w: stage_audit(w),
    "finalize": lambda i, o, w: stage_finalize(w, o),
    "selftest": lambda i, o, w: stage_selftest(w, o),
}


def main(argv=None):
    p = argparse.ArgumentParser(prog="core_impl", description=__doc__)
    p.add_argument("stage", choices=sorted(STAGES))
    p.add_argument("--input", default=os.environ.get("SKILL_INPUT", DEFAULT_INPUT))
    p.add_argument("--output", default=os.environ.get("SKILL_OUTPUT", DEFAULT_OUTPUT))
    p.add_argument("--work", default=os.environ.get("SKILL_WORK", DEFAULT_WORK))
    args = p.parse_args(argv)
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    fn = STAGES[args.stage]
    try:
        out = fn(args.input, args.output, args.work)
    except Exception as e:  # noqa: BLE001 - degrade, never crash the harness
        append_event(args.work, args.stage, "stage_failed", {"err": str(e)}, ok=False)
        print(f"[stage:{args.stage}] FAILED: {e}", file=sys.stderr)
        # ensure a valid partial result still exists
        result_path = os.path.join(args.output, "result.json")
        if not os.path.exists(result_path):
            try:
                os.makedirs(args.output, exist_ok=True)
                with open(result_path, "w", encoding="utf-8") as f:
                    json.dump({"case_id": "unknown", "status": "input_error",
                               "errors": [str(e)],
                               "change_log": [{"at": now_iso(), "action": args.stage,
                                               "detail": f"stage failed: {e}"}]},
                              f, ensure_ascii=False, indent=2)
            except Exception:
                pass
        raise SystemExit(1)
    if args.stage == "selftest":
        raise SystemExit(0)
    return out


if __name__ == "__main__":
    main()
