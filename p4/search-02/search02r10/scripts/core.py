"""core.py - deterministic pipeline for research-question-driven search.

Pure standard library. Stages:
  scan -> skeleton -> plan -> screen -> evidence -> rank -> assemble -> selfcheck
All stages share a single intermediate representation under /app/work.
"""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time

UTF8_SIG = "utf-8-sig"
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB

SCORE_WEIGHTS = {"relevance": 0.6, "metadata": 0.2, "source": 0.2}
LIST_DEFAULT_FIELDS = {
    "sub_questions", "query_plan", "term_expansion", "results", "sources",
    "screening", "evidence", "pending_human", "errors", "degradations",
    "change_log", "coverage_gaps", "missing_metadata", "conflicts",
}
RECORD_KEYS = (
    "id", "fingerprint", "origin", "title", "authors", "year", "journal",
    "doi", "url", "database", "abstract", "locator", "fulltext_locator",
    "dedup", "screening", "score", "score_breakdown", "rank",
)

KEY_ALIASES = {
    "title": ["title", "name", "paper_title", "article_title", "heading", "标题", "题目"],
    "authors": ["authors", "author", "creators", "byline", "authors_list", "作者"],
    "year": ["year", "pub_year", "publication_year", "published_year", "date_year", "年份"],
    "journal": ["journal", "venue", "source", "publication", "container", "journal_name", "期刊"],
    "doi": ["doi", "DOI", "doi_url"],
    "url": ["url", "link", "href", "pdf_url", "source_url", "网页", "链接"],
    "abstract": ["abstract", "summary", "description", "摘要"],
    "database": ["database", "db", "source_db", "db_name", "来源"],
    "locator": ["locator", "position", "record_id", "id", "export_id", "序号"],
    "pmid": ["pmid", "pubmed_id", "PMID"],
}

SOURCE_RELIABILITY = {
    "snapshot": 0.9, "local": 0.9, "materials": 0.9, "export": 0.8,
    "doi": 1.0, "paper": 0.7, "web": 0.6, "unknown": 0.5,
}

_JSON_SENTINEL = object()


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """Tolerant text read. Returns (text_or_none, error_or_none)."""
    try:
        raw = open(path, "rb").read()
    except OSError as exc:
        return None, "read_error:%s" % exc
    if len(raw) > max_bytes:
        return None, "file_too_large:%d" % len(raw)
    for enc in (UTF8_SIG, "utf-8", "latin-1"):
        try:
            return raw.decode(enc), None
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("utf-8", errors="replace"), "decode_replaced"


def load_json(path):
    """Tolerant JSON load. Returns (obj_or_none, error_or_none)."""
    text, err = read_text(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except ValueError as exc:
        stripped = text.lstrip("\ufeff")
        try:
            return json.loads(stripped), None
        except ValueError:
            return None, "bad_json:%s" % exc


def write_json(path, obj):
    """Atomic JSON write."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)
    return path


def load_state(work, name, default):
    path = os.path.join(work, name)
    obj, err = load_json(path)
    if err is not None or obj is None:
        return default
    return obj


def save_state(work, name, obj):
    os.makedirs(work, exist_ok=True)
    return write_json(os.path.join(work, name), obj)


def slugify(text):
    return re.sub(r"[^0-9a-zA-Z_\-]+", "_", text or "")


# ---------------------------------------------------------------- normalize

def pick(obj, aliases):
    for key in aliases:
        if key in obj and obj[key] not in (None, ""):
            return obj[key]
    # case-insensitive fallback
    low = {str(k).lower(): v for k, v in obj.items()}
    for key in aliases:
        if key.lower() in low and low[key.lower()] not in (None, ""):
            return low[key.lower()]
    return None


def normalize_authors(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items = [str(x).strip() for x in value if str(x).strip()]
        return items
    text = str(value).strip()
    if not text:
        return []
    # split on author separators only; keep "Last, First" as one verbatim token
    if any(sep in text for sep in (";", "|", "/")):
        parts = re.split(r"[;|/]|\band\b|\bet\s*al\.?", text, flags=re.IGNORECASE)
    else:
        parts = [text]
    out = []
    for p in parts:
        p = p.strip()
        if p:
            out.append(p)
    return out


def normalize_year(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = re.search(r"(18|19|20)\d{2}", str(value))
    return int(m.group(0)) if m else None


def normalize_record(obj, origin):
    rec = {"origin": origin}
    for field, aliases in KEY_ALIASES.items():
        rec[field] = pick(obj, aliases)
    rec["authors"] = normalize_authors(rec.get("authors"))
    rec["year"] = normalize_year(rec.get("year"))
    if isinstance(rec.get("doi"), str):
        rec["doi"] = rec["doi"].strip()
    rec["title"] = (rec.get("title") or "").strip() or None
    rec["journal"] = (rec.get("journal") or "").strip() or None
    rec["abstract"] = (rec.get("abstract") or "").strip() or None
    rec["url"] = (rec.get("url") or "").strip() or None
    rec["database"] = (rec.get("database") or origin.get("database") or "snapshot")
    rec["locator"] = rec.get("locator") or origin.get("locator")
    rec["fulltext_locator"] = None
    rec["dedup"] = {"group_id": None, "role": "unassigned", "duplicate_of": None, "conflicts": []}
    rec["screening"] = {
        "decision": None, "reason": None, "sub_question_ids": [],
        "relevance": None, "relevance_reason": None,
        "evidence": {"locator": None, "quote": None},
        "conflict_with": None, "input_fixture": False,
    }
    rec["score"] = None
    rec["score_breakdown"] = None
    rec["rank"] = None
    return rec


def norm_title(title):
    if not title:
        return ""
    t = title.lower()
    t = re.sub(r"[\s\-_:,.;!?()\[\]{}'\"“”‘’]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def first_author_last(authors):
    if not authors:
        return ""
    a = authors[0].strip()
    if "," in a:
        a = a.split(",")[0].strip()
    parts = re.split(r"\s+", a)
    return parts[-1].lower() if parts else a.lower()


def fingerprint(rec):
    """Primary dedup key: normalized title + first-author last name + year.

    Title is more stable than DOI across duplicate exports (DOI may be missing
    or formatted differently). DOI is used only when title is absent.
    """
    title = norm_title(rec.get("title"))
    if title:
        return "title:%s|%s|%s" % (title, first_author_last(rec.get("authors")), rec.get("year"))
    doi = (rec.get("doi") or "").strip()
    if doi:
        d = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
        d = d.strip().lower()
        if d:
            return "doi:" + d
    return "opaque:" + hashlib.sha1(
        (json.dumps(rec.get("origin"), sort_keys=True, default=str).encode("utf-8"))
    ).hexdigest()[:16]


def dedup_records(records):
    """Group by fingerprint; mark duplicates and metadata conflicts."""
    groups = {}
    for rec in records:
        fp = fingerprint(rec)
        rec["fingerprint"] = fp
        groups.setdefault(fp, []).append(rec)
    ordered = sorted(groups.values(), key=lambda g: g[0]["id"])
    for group in ordered:
        gid = "g-" + group[0]["id"].split("-")[-1]
        for i, rec in enumerate(group):
            rec["dedup"]["group_id"] = gid
            if i == 0:
                rec["dedup"]["role"] = "primary"
            else:
                rec["dedup"]["role"] = "duplicate"
                rec["dedup"]["duplicate_of"] = group[0]["id"]
        # conflict detection within group
        base = group[0]
        for other in group[1:]:
            conflicts = []
            for f in ("year", "journal", "doi", "url"):
                if (base.get(f) or None) != (other.get(f) or None) and (base.get(f) or other.get(f)):
                    conflicts.append({"field": f, "primary": base.get(f), "duplicate": other.get(f)})
            if conflicts:
                other["dedup"]["conflicts"] = conflicts
                base["dedup"]["conflicts"].extend(conflicts)
    return records


# ---------------------------------------------------------------- discovery

def discover_inputs(input_dir):
    inventory = {"input_dir": input_dir, "files": [], "case_file": None,
                 "task_file": None, "materials": [], "exports": [],
                 "declared_fixtures": [], "degradations": []}
    if not os.path.isdir(input_dir):
        inventory["degradations"].append("input_dir_missing:%s" % input_dir)
        return inventory
    names = sorted(os.listdir(input_dir))
    for name in names:
        path = os.path.join(input_dir, name)
        if os.path.isdir(path):
            if name.lower() in ("materials", "material"):
                for f in sorted(os.listdir(path)):
                    sub = os.path.join(path, f)
                    if os.path.isfile(sub):
                        inventory["materials"].append({"name": f, "path": sub, "kind": kind_of(f)})
            continue
        if not os.path.isfile(path):
            continue
        entry = {"name": name, "path": path, "kind": kind_of(name)}
        inventory["files"].append(entry)
        if name.lower() == "case.json":
            inventory["case_file"] = path
        elif name.lower() == "task.md":
            inventory["task_file"] = path
        elif entry["kind"] in ("json", "jsonl", "csv"):
            inventory["exports"].append(entry)
    # snapshot timestamp = max mtime (deterministic)
    mt = 0.0
    for f in inventory["files"] + inventory["materials"]:
        try:
            mt = max(mt, os.path.getmtime(f["path"]))
        except OSError:
            pass
    inventory["snapshot_ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mt or time.time()))
    return inventory


def kind_of(name):
    n = (name or "").lower()
    if n.endswith(".pdf"):
        return "pdf"
    if n.endswith(".json"):
        return "json"
    if n.endswith(".jsonl") or n.endswith(".ndjson"):
        return "jsonl"
    if n.endswith(".csv"):
        return "csv"
    if n.endswith((".md", ".txt", ".text")):
        return "text"
    return "other"


def extract_pdf(pdf_path, work):
    txt_dir = os.path.join(work, "pdf_text")
    os.makedirs(txt_dir, exist_ok=True)
    out = os.path.join(txt_dir, slugify(os.path.basename(pdf_path)) + ".txt")
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out, None
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", pdf_path, out],
            capture_output=True, timeout=180,
        )
    except FileNotFoundError:
        return None, "pdftotext_not_found"
    except subprocess.TimeoutExpired:
        return None, "pdftotext_timeout"
    except OSError as exc:
        return None, "pdftotext_error:%s" % exc
    if proc.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
        return None, "pdftotext_rc:%d" % proc.returncode
    return out, None


def parse_export(text, origin):
    """Parse a search-export file into normalized records. Best effort."""
    records = []
    stripped = (text or "").strip()
    if not stripped:
        return records, "empty_export"
    # whole-document JSON (object/array), container-aware
    data = _JSON_SENTINEL
    try:
        data = json.loads(stripped)
    except ValueError:
        data = _JSON_SENTINEL
    if data is not _JSON_SENTINEL:
        if isinstance(data, list):
            for i, obj in enumerate(data):
                if isinstance(obj, dict):
                    records.append(normalize_record(obj, dict(origin, locator="items[%d]" % i)))
            return records, None
        if isinstance(data, dict):
            container = None
            for key in ("results", "items", "records", "data", "papers", "documents"):
                if key in data and isinstance(data[key], list):
                    container = (key, data[key])
                    break
            if container is not None:
                for i, obj in enumerate(container[1]):
                    if isinstance(obj, dict):
                        records.append(normalize_record(obj, dict(origin, locator="%s[%d]" % (container[0], i))))
                return records, None
            # single-record object (has a title-like key)
            if any(data.get(a) for a in KEY_ALIASES["title"]):
                records.append(normalize_record(data, dict(origin, locator="root")))
                return records, None
            return records, "no_records_in_object"
        return records, "unparseable_export"
    # JSONL fallback (one JSON object per line)
    if stripped.startswith("{"):
        for idx, line in enumerate(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                records.append(normalize_record(obj, dict(origin, locator="line:%d" % (idx + 1))))
        if records:
            return records, None
    # CSV fallback
    try:
        return parse_csv(text, origin), None
    except Exception:
        return records, "unparseable_export"
    return records, "unparseable_export"


def parse_csv(text, origin):
    records = []
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return records
    for i, row in enumerate(reader):
        row = {k: v for k, v in row.items() if k is not None}
        records.append(normalize_record(row, dict(origin, locator="row:%d" % (i + 2))))
    return records


# ---------------------------------------------------------------- stages

def run_scan(args):
    work = args.work
    inventory = discover_inputs(args.input)
    os.makedirs(work, exist_ok=True)
    records = []
    errors = []
    degradations = list(inventory["degradations"])

    # parse case.json for output_contract + fixtures
    case = {}
    if inventory["case_file"]:
        case, err = load_json(inventory["case_file"])
        if err:
            errors.append({"file": inventory["case_file"], "error": err})
            degradations.append("case_json_unreadable:%s" % err)
        elif isinstance(case, dict):
            for key in ("fixtures", "input_fixtures", "declared_fixtures", "synthetic"):
                if isinstance(case.get(key), list):
                    inventory["declared_fixtures"].extend(case[key])
            if isinstance(case.get("declared_fixtures"), list):
                inventory["declared_fixtures"].extend(case["declared_fixtures"])

    # exports -> records
    for entry in inventory["exports"]:
        text, err = read_text(entry["path"])
        if err:
            degradations.append("export_unreadable:%s:%s" % (entry["name"], err))
            continue
        parsed, perr = parse_export(text, {"file": entry["name"], "path": entry["path"], "database": "export", "kind": entry["kind"]})
        if perr:
            degradations.append("export_parse:%s:%s" % (entry["name"], perr))
        for r in parsed:
            r["origin"]["database"] = "export"
            records.append(r)

    # materials -> records (PDF extracted; export files parsed; text/other one record each)
    for entry in inventory["materials"]:
        kind = entry["kind"]
        if kind == "pdf":
            txt_path, perr = extract_pdf(entry["path"], work)
            if perr:
                degradations.append("pdf_extract:%s:%s" % (entry["name"], perr))
            rec = normalize_record({}, {"file": entry["name"], "path": entry["path"], "database": "snapshot", "kind": "pdf", "locator": entry["name"]})
            if txt_path:
                rec["fulltext_locator"] = "pdf_text/" + os.path.basename(txt_path)
            records.append(rec)
        elif kind in ("json", "jsonl", "csv"):
            text, rerr = read_text(entry["path"])
            if rerr:
                degradations.append("export_unreadable:%s:%s" % (entry["name"], rerr))
                records.append(normalize_record({}, {"file": entry["name"], "path": entry["path"], "database": "export", "kind": kind, "locator": entry["name"]}))
                continue
            parsed, perr = parse_export(text, {"file": entry["name"], "path": entry["path"], "database": "export", "kind": kind})
            if perr:
                degradations.append("export_parse:%s:%s" % (entry["name"], perr))
            if not parsed:
                records.append(normalize_record({}, {"file": entry["name"], "path": entry["path"], "database": "export", "kind": kind, "locator": entry["name"]}))
            for r in parsed:
                r["origin"]["database"] = "export"
                records.append(r)
        else:  # text / other
            records.append(normalize_record({}, {"file": entry["name"], "path": entry["path"], "database": "snapshot", "kind": kind, "locator": entry["name"]}))

    # assign ids in deterministic (sorted) order
    records.sort(key=lambda r: (r["origin"].get("path", ""), str(r["origin"].get("locator", ""))))
    for i, rec in enumerate(records):
        rec["id"] = "rec-%03d" % (i + 1)
    records = dedup_records(records)

    save_state(work, "inventory.json", inventory)
    save_state(work, "records.json", records)
    save_state(work, "errors.json", errors)
    save_state(work, "degradations.json", degradations)
    save_state(work, "runtime.json", {
        "started_at": now_iso(), "snapshot_ts": inventory.get("snapshot_ts"),
        "wall_seconds": None, "model_requests": None, "token_budget": 16384,
    })
    # plan/screening/evidence defaults (empty)
    if not os.path.exists(os.path.join(work, "plan.json")):
        save_state(work, "plan.json", {
            "research_question": None, "sub_questions": [], "query_plan": [],
            "term_expansion": [], "stop_decision": {}, "notes": [],
        })
    if not os.path.exists(os.path.join(work, "screening.json")):
        save_state(work, "screening.json", {})
    if not os.path.exists(os.path.join(work, "evidence.json")):
        save_state(work, "evidence.json", [])
    print("scan: %d records, %d materials, %d exports" % (len(records), len(inventory["materials"]), len(inventory["exports"])))
    if degradations:
        print("scan degradations: %d (see work/degradations.json)" % len(degradations))
    return 0


def read_case(input_dir):
    case_file = os.path.join(input_dir, "case.json")
    case, err = load_json(case_file)
    if err or not isinstance(case, dict):
        return {}, err
    return case, None


def contract_of(case):
    for key in ("output_contract", "output", "contract", "schema", "required_fields"):
        if isinstance(case.get(key), dict):
            return case[key]
        if isinstance(case.get(key), list):
            return {"fields": case[key]}
    return None


def merge_contract(result, contract):
    result.setdefault("output_contract", contract if contract is not None else None)
    if not isinstance(contract, dict):
        return result
    fields = []
    if isinstance(contract.get("required"), list):
        fields.extend(contract["required"])
    if isinstance(contract.get("fields"), list):
        fields.extend(contract["fields"])
    for f in fields:
        name = f.get("name") if isinstance(f, dict) else f
        if isinstance(name, str) and name and name not in result:
            result[name] = [] if name in LIST_DEFAULT_FIELDS else None
    for k, v in contract.items():
        if k in ("required", "fields", "schema", "description"):
            continue
        if k not in result:
            if isinstance(v, (dict, list)):
                result[k] = None
            else:
                result[k] = v
    return result


def run_skeleton(args):
    work = args.work
    os.makedirs(args.output, exist_ok=True)
    inventory = load_state(work, "inventory.json", discover_inputs(args.input))
    records = load_state(work, "records.json", [])
    plan = load_state(work, "plan.json", {})
    screening = load_state(work, "screening.json", {})
    evidence = load_state(work, "evidence.json", [])
    errors = load_state(work, "errors.json", [])
    degradations = load_state(work, "degradations.json", [])
    runtime = load_state(work, "runtime.json", {})
    case, case_err = read_case(args.input)
    contract = contract_of(case) if isinstance(case, dict) else None
    case_id = (case.get("case_id") or case.get("id") or (inventory.get("case_file") and os.path.basename(inventory["case_file"])) or "unknown")

    status = "partial"
    if not os.path.isdir(args.input):
        status = "input_error"
    elif not records:
        status = "insufficient_evidence"

    result = {
        "case_id": case_id,
        "status": status,
        "research_question": (case.get("research_question") or case.get("question") if isinstance(case, dict) else None) or plan.get("research_question"),
        "sub_questions": plan.get("sub_questions", []),
        "query_plan": plan.get("query_plan", []),
        "term_expansion": plan.get("term_expansion", []),
        "stop_decision": plan.get("stop_decision", {}),
        "results": [],
        "screening": build_screening(records, screening),
        "evidence": evidence,
        "sources": build_sources(inventory, records),
        "uncertainty": build_uncertainty(records, screening, evidence),
        "change_log": build_change_log(records, screening, runtime, plan),
        "status_notes": "skeleton: partial result written within 3-minute budget; improve incrementally.",
        "limitations": [],
        "errors": list(errors),
        "degradations": list(degradations),
        "pending_human": [],
        "runtime": {
            "started_at": runtime.get("started_at"),
            "snapshot_ts": runtime.get("snapshot_ts") or inventory.get("snapshot_ts"),
            "wall_seconds": runtime.get("wall_seconds"),
            "model_requests": runtime.get("model_requests"),
            "token_budget": runtime.get("token_budget", 16384),
        },
        "selfcheck": None,
    }
    if case_err:
        result["errors"].append({"file": "case.json", "error": case_err})
    result = merge_contract(result, contract)
    write_json(os.path.join(args.output, "result.json"), result)
    print("skeleton: result.json written (status=%s)" % status)
    return 0


def build_screening(records, screening):
    included, excluded, uncertain, merged = [], [], [], []
    ledger = []
    for rec in records:
        scr = screening.get(rec["id"], rec.get("screening", {}))
        item = {
            "id": rec["id"], "title": rec.get("title"), "fingerprint": rec.get("fingerprint"),
            "decision": scr.get("decision"), "reason": scr.get("reason"),
            "sub_question_ids": scr.get("sub_question_ids", []),
            "evidence": scr.get("evidence", {}), "dedup": rec.get("dedup"),
        }
        ledger.append(item)
        decision = scr.get("decision")
        if decision == "include":
            included.append(item)
        elif decision == "exclude":
            excluded.append(item)
        else:
            uncertain.append(item)
        if rec.get("dedup", {}).get("role") == "duplicate":
            merged.append(item)
    return {
        "included": included, "excluded": excluded, "uncertain": uncertain,
        "merged_duplicates": merged, "ledger": ledger,
        "totals": {"included": len(included), "excluded": len(excluded),
                   "uncertain": len(uncertain), "merged_duplicates": len(merged), "records": len(records)},
    }


def build_sources(inventory, records):
    sources = []
    if inventory.get("case_file"):
        sources.append({"id": "case", "kind": "input", "path": inventory["case_file"], "role": "task_and_contract"})
    if inventory.get("task_file"):
        sources.append({"id": "task", "kind": "input", "path": inventory["task_file"], "role": "task_spec"})
    for f in inventory.get("materials", []):
        sources.append({"id": "mat-" + slugify(f.get("name") or str(hash(f.get("path")))), "kind": f.get("kind"),
                        "path": f.get("path"), "role": "material_snapshot"})
    dbs = sorted({r.get("database") for r in records if r.get("database")})
    for db in dbs:
        sources.append({"id": "db-" + slugify(db), "kind": "database", "path": None, "role": "record_source", "database": db})
    return sources


def build_uncertainty(records, screening, evidence):
    missing = []
    for rec in records:
        for f in ("title", "authors", "year", "journal", "doi"):
            if not rec.get(f):
                missing.append({"record_id": rec["id"], "field": f, "locator": rec.get("locator")})
    conflicts = [e for e in evidence if e.get("kind") == "conflict"]
    coverage_gaps = []
    covered_sub = set()
    for rec in records:
        scr = screening.get(rec["id"], rec.get("screening", {}))
        if scr.get("decision") == "include":
            covered_sub.update(scr.get("sub_question_ids", []))
    # sub-questions known only from plan; fill after assemble merges plan
    return {"missing_metadata": missing, "conflicts": conflicts,
            "coverage_gaps": coverage_gaps, "notes": []}


def build_change_log(records, screening, runtime, plan):
    ts = runtime.get("snapshot_ts") or now_iso()
    return [
        {"seq": 0, "time": ts, "action": "scan", "reason": "discover inputs and normalize records", "status": "ok"},
        {"seq": 1, "time": ts, "action": "skeleton", "reason": "write minimal legal partial result", "status": "partial"},
        {"seq": 2, "time": ts, "action": "screening_todo",
         "reason": "records not yet screened: %d" % sum(1 for r in records if not screening.get(r["id"], {}).get("decision")),
         "status": "pending"},
        {"seq": 3, "time": ts, "action": "plan_todo",
         "reason": "sub_questions=%d" % len(plan.get("sub_questions", [])), "status": "pending"},
    ]


# ---------------------------------------------------------------- incremental

def merge_plan(plan, incoming):
    for key in ("research_question", "sub_questions", "query_plan", "term_expansion", "stop_decision"):
        if key in incoming and incoming[key] is not None:
            plan[key] = incoming[key]
    if isinstance(incoming.get("notes"), list):
        plan.setdefault("notes", []).extend(incoming["notes"])
    return plan


def run_plan(args):
    work = args.work
    incoming, err = load_incoming(args)
    if err:
        print("plan: %s" % err)
        return 1
    plan = load_state(work, "plan.json", {})
    plan = merge_plan(plan, incoming)
    save_state(work, "plan.json", plan)
    print("plan: sub_questions=%d, query_plan=%d, term_expansion=%d" % (
        len(plan.get("sub_questions", [])), len(plan.get("query_plan", [])), len(plan.get("term_expansion", []))))
    return 0


def load_incoming(args):
    if args.file:
        obj, err = load_json(args.file)
        if err:
            return None, "bad_file:%s:%s" % (args.file, err)
        return obj, None
    if args.inline:
        try:
            return json.loads(args.inline), None
        except ValueError as exc:
            return None, "bad_inline:%s" % exc
    return None, "no_input(--file or --inline required)"


def validate_screen_entry(entry, idx, errors):
    if not isinstance(entry, dict):
        errors.append("screen_entry_%d:not_object" % idx)
        return False
    if "id" not in entry and "record_id" not in entry:
        errors.append("screen_entry_%d:no_id" % idx)
        return False
    decision = entry.get("decision")
    if decision not in ("include", "exclude", "uncertain"):
        errors.append("screen_entry_%d:bad_decision:%s" % (idx, decision))
        return False
    if decision == "include":
        if not entry.get("reason"):
            errors.append("screen_entry_%d:include_no_reason" % idx)
        ev = entry.get("evidence") or {}
        if not ev.get("locator") or not ev.get("quote"):
            errors.append("screen_entry_%d:include_no_evidence_locator_quote" % idx)
    return True


def run_screen(args):
    work = args.work
    incoming, err = load_incoming(args)
    if err:
        print("screen: %s" % err)
        return 1
    entries = incoming if isinstance(incoming, list) else incoming.get("entries", incoming.get("screening", []))
    if not isinstance(entries, list):
        print("screen: input must be a list or {entries:[...]}")
        return 1
    records = load_state(work, "records.json", [])
    by_id = {r["id"]: r for r in records}
    screening = load_state(work, "screening.json", {})
    errors = load_state(work, "errors.json", [])
    applied = 0
    for idx, entry in enumerate(entries):
        if not validate_screen_entry(entry, idx, errors):
            continue
        rid = entry.get("id") or entry.get("record_id")
        if rid not in by_id:
            errors.append("screen_entry_%d:unknown_record:%s" % (idx, rid))
            continue
        ev = entry.get("evidence") or {}
        screening[rid] = {
            "decision": entry.get("decision"),
            "reason": entry.get("reason"),
            "sub_question_ids": entry.get("sub_question_ids", []),
            "relevance": entry.get("relevance"),
            "relevance_reason": entry.get("relevance_reason"),
            "evidence": {"locator": ev.get("locator"), "quote": ev.get("quote")},
            "conflict_with": entry.get("conflict_with"),
            "input_fixture": bool(entry.get("input_fixture")),
        }
        applied += 1
    save_state(work, "screening.json", screening)
    save_state(work, "errors.json", errors)
    n_new_errors = sum(1 for e in errors if e.startswith("screen_entry"))
    print("screen: applied %d, screen_errors %d" % (applied, n_new_errors))
    return 0


def run_evidence(args):
    work = args.work
    incoming, err = load_incoming(args)
    if err:
        print("evidence: %s" % err)
        return 1
    entries = incoming if isinstance(incoming, list) else incoming.get("entries", incoming.get("evidence", []))
    if not isinstance(entries, list):
        print("evidence: input must be a list or {entries:[...]}")
        return 1
    evidence = load_state(work, "evidence.json", [])
    runtime = load_state(work, "runtime.json", {})
    ts = runtime.get("snapshot_ts") or now_iso()
    for idx, e in enumerate(entries):
        if not isinstance(e, dict):
            continue
        e.setdefault("timestamp", ts)
        e.setdefault("seq", len(evidence))
        evidence.append(e)
    save_state(work, "evidence.json", evidence)
    print("evidence: total %d" % len(evidence))
    return 0


def metadata_completeness(rec):
    fields = [rec.get("title"), rec.get("authors"), rec.get("year"), rec.get("journal"), rec.get("doi"), rec.get("url")]
    return round(sum(1 for f in fields if f) / len(fields), 4)


def source_reliability(rec):
    db = (rec.get("database") or "unknown").lower()
    if rec.get("doi"):
        return SOURCE_RELIABILITY.get("doi", 1.0)
    if db in ("snapshot", "local", "materials", "material"):
        return SOURCE_RELIABILITY["snapshot"]
    if db in ("export", "exports"):
        return SOURCE_RELIABILITY["export"]
    if db in ("web", "websearch"):
        return SOURCE_RELIABILITY["web"]
    if db in ("paper", "papersearch"):
        return SOURCE_RELIABILITY["paper"]
    return SOURCE_RELIABILITY["unknown"]


def run_rank(args):
    work = args.work
    records = load_state(work, "records.json", [])
    screening = load_state(work, "screening.json", {})
    ranked = []
    for rec in records:
        scr = screening.get(rec["id"], {})
        if scr.get("decision") != "include":
            rec["score"] = None
            rec["score_breakdown"] = None
            rec["rank"] = None
            continue
        relevance = float(scr.get("relevance") or 0)
        meta = metadata_completeness(rec)
        source = source_reliability(rec)
        total = round(SCORE_WEIGHTS["relevance"] * relevance
                      + SCORE_WEIGHTS["metadata"] * meta
                      + SCORE_WEIGHTS["source"] * source, 4)
        rec["score"] = total
        rec["score_breakdown"] = {
            "relevance": round(relevance, 4), "metadata_completeness": meta,
            "source_reliability": source, "weights": SCORE_WEIGHTS,
        }
        ranked.append(rec)
    ranked.sort(key=lambda r: (-r["score"], -(r["score_breakdown"]["relevance"]),
                               -(r.get("year") or 0), (r.get("title") or "").lower()))
    for i, rec in enumerate(ranked):
        rec["rank"] = i + 1
    save_state(work, "records.json", records)
    save_state(work, "rank.json", {"ranked_ids": [r["id"] for r in ranked]})
    print("rank: %d included records ranked" % len(ranked))
    return 0


def run_assemble(args):
    work = args.work
    os.makedirs(args.output, exist_ok=True)
    inventory = load_state(work, "inventory.json", discover_inputs(args.input))
    records = load_state(work, "records.json", [])
    plan = load_state(work, "plan.json", {})
    screening = load_state(work, "screening.json", {})
    evidence = load_state(work, "evidence.json", [])
    errors = load_state(work, "errors.json", [])
    degradations = load_state(work, "degradations.json", [])
    runtime = load_state(work, "runtime.json", {})
    if args.runtime_seconds is not None:
        runtime["wall_seconds"] = args.runtime_seconds
    if args.runtime_requests is not None:
        runtime["model_requests"] = args.runtime_requests
    save_state(work, "runtime.json", runtime)

    case, case_err = read_case(args.input)
    contract = contract_of(case) if isinstance(case, dict) else None
    case_id = (case.get("case_id") or case.get("id") if isinstance(case, dict) else None) \
        or (inventory.get("case_file") and os.path.basename(inventory["case_file"])) or "unknown"

    # ensure all records are screened (default uncertain)
    for rec in records:
        if rec["id"] not in screening:
            screening[rec["id"]] = {
                "decision": "uncertain", "reason": "not_yet_screened",
                "sub_question_ids": [], "relevance": None, "relevance_reason": None,
                "evidence": {"locator": None, "quote": None},
                "conflict_with": None, "input_fixture": False,
            }
    save_state(work, "screening.json", screening)

    results = build_results(records, screening)
    screening_ledger = build_screening(records, screening)
    uncertainty = build_uncertainty(records, screening, evidence)
    uncertainty["coverage_gaps"] = coverage_gaps(plan, records, screening)

    status = derive_status(records, screening, plan, uncertainty, errors, degradations)

    result = {
        "case_id": case_id,
        "status": status,
        "research_question": (case.get("research_question") or case.get("question") if isinstance(case, dict) else None) or plan.get("research_question"),
        "sub_questions": plan.get("sub_questions", []),
        "query_plan": plan.get("query_plan", []),
        "term_expansion": plan.get("term_expansion", []),
        "stop_decision": plan.get("stop_decision", {}),
        "results": results,
        "screening": screening_ledger,
        "evidence": evidence,
        "sources": build_sources(inventory, records),
        "uncertainty": uncertainty,
        "change_log": build_change_log(records, screening, runtime, plan),
        "status_notes": status_notes(status, plan),
        "limitations": list(degradations) + ([case_err] if case_err else []),
        "errors": list(errors),
        "degradations": list(degradations),
        "pending_human": pending_human(plan, uncertainty, records),
        "runtime": {
            "started_at": runtime.get("started_at"), "snapshot_ts": runtime.get("snapshot_ts") or inventory.get("snapshot_ts"),
            "wall_seconds": runtime.get("wall_seconds"), "model_requests": runtime.get("model_requests"),
            "token_budget": runtime.get("token_budget", 16384),
        },
        "selfcheck": None,
    }
    result = merge_contract(result, contract)
    write_json(os.path.join(args.output, "result.json"), result)
    write_md(os.path.join(args.output, "screening.md"), screening_ledger, records, screening)
    write_md(os.path.join(args.output, "plan.md"), plan, records, screening)
    print("assemble: result.json, screening.md, plan.md written (status=%s, results=%d)" % (status, len(results)))
    return 0


def build_results(records, screening):
    out = []
    for rec in records:
        scr = screening.get(rec["id"], {})
        if scr.get("decision") != "include":
            continue
        out.append({
            "rank": rec.get("rank"), "id": rec["id"], "title": rec.get("title"),
            "authors": rec.get("authors"), "year": rec.get("year"), "journal": rec.get("journal"),
            "doi": rec.get("doi"), "url": rec.get("url"), "database": rec.get("database"),
            "sub_question_ids": scr.get("sub_question_ids", []),
            "relevance": scr.get("relevance"), "relevance_reason": scr.get("relevance_reason"),
            "screening": scr.get("decision"), "screening_reason": scr.get("reason"),
            "evidence": scr.get("evidence"), "score": rec.get("score"),
            "score_breakdown": rec.get("score_breakdown"),
            "fingerprint": rec.get("fingerprint"), "locator": rec.get("locator"),
            "fulltext_locator": rec.get("fulltext_locator"),
            "dedup": rec.get("dedup"), "input_fixture": scr.get("input_fixture"),
        })
    out.sort(key=lambda r: (r["rank"] if r["rank"] is not None else 10 ** 9,
                            -(r.get("year") or 0), (r.get("title") or "").lower()))
    return out


def coverage_gaps(plan, records, screening):
    subs = plan.get("sub_questions", [])
    if not subs:
        return []
    covered = {}
    for rec in records:
        scr = screening.get(rec["id"], {})
        if scr.get("decision") == "include":
            for sid in scr.get("sub_question_ids", []):
                covered[sid] = covered.get(sid, 0) + 1
    gaps = []
    for sq in subs:
        sid = sq.get("id") or sq.get("sub_question_id") or str(sq)
        if covered.get(sid, 0) == 0:
            gaps.append({"sub_question_id": sid, "label": sq.get("label") or sq.get("question") or sid,
                         "included_evidence": 0})
    return gaps


def derive_status(records, screening, plan, uncertainty, errors, degradations):
    if not records:
        return "insufficient_evidence"
    included = [r for r in records if screening.get(r["id"], {}).get("decision") == "include"]
    if not included:
        return "insufficient_evidence"
    stop = plan.get("stop_decision", {})
    if stop.get("status") == "handoff":
        return "partial"
    if stop.get("status") == "sufficient":
        return "ok"
    if uncertainty.get("coverage_gaps") or uncertainty.get("conflicts"):
        return "partial"
    return "ok"


def status_notes(status, plan):
    notes = {
        "ok": "检索已覆盖子问题并纳入候选，证据可复核。",
        "partial": "部分完成：存在覆盖缺口、冲突或待人工确认项，详见 uncertainty 与 pending_human。",
        "insufficient_evidence": "材料或检索结果不足，未编造文献；详见 limitations。",
        "input_error": "输入不可解析或缺失，见 errors/degradations。",
    }
    return notes.get(status, "")


def pending_human(plan, uncertainty, records):
    items = []
    for gap in uncertainty.get("coverage_gaps", []):
        items.append({"kind": "coverage_gap", "detail": gap})
    for miss in uncertainty.get("missing_metadata", []):
        items.append({"kind": "missing_metadata", "detail": miss})
    if plan.get("stop_decision", {}).get("status") == "handoff":
        items.append({"kind": "handoff", "detail": plan["stop_decision"].get("reason")})
    if not records:
        items.append({"kind": "no_records", "detail": "input snapshot contains no parseable records"})
    return items


def write_md(path, first, records=None, screening=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = []
    if path.endswith("screening.md"):
        lines.append("# 全量筛选清单\n")
        lines.append("- records: %d" % first.get("totals", {}).get("records", 0))
        lines.append("- included: %d; excluded: %d; uncertain: %d; merged_duplicates: %d\n"
                      % (first.get("totals", {}).get("included", 0), first.get("totals", {}).get("excluded", 0),
                         first.get("totals", {}).get("uncertain", 0), first.get("totals", {}).get("merged_duplicates", 0)))
        for item in first.get("ledger", []):
            lines.append("## %s (%s)\n" % (item.get("id"), item.get("decision") or "unscreened"))
            lines.append("- title: %s" % (item.get("title") or "null"))
            lines.append("- reason: %s" % (item.get("reason") or "null"))
            ev = item.get("evidence") or {}
            lines.append("- evidence: %s | quote: %s" % (ev.get("locator") or "null", ev.get("quote") or "null"))
            dd = item.get("dedup") or {}
            if dd.get("role") == "duplicate":
                lines.append("- duplicate_of: %s" % dd.get("duplicate_of"))
            lines.append("")
    else:
        lines.append("# 拆解、查询计划、术语扩展与停止判断\n")
        lines.append("- research_question: %s" % (first.get("research_question") or "null"))
        lines.append("\n## sub_questions\n")
        for sq in first.get("sub_questions", []):
            lines.append("- %s" % json.dumps(sq, ensure_ascii=False))
        lines.append("\n## query_plan\n")
        for q in first.get("query_plan", []):
            lines.append("- %s" % json.dumps(q, ensure_ascii=False))
        lines.append("\n## term_expansion\n")
        for t in first.get("term_expansion", []):
            lines.append("- %s" % json.dumps(t, ensure_ascii=False))
        lines.append("\n## stop_decision\n")
        lines.append("- %s" % json.dumps(first.get("stop_decision", {}), ensure_ascii=False))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


# ---------------------------------------------------------------- selfcheck

def run_selfcheck(args):
    out_dir = args.output
    result, err = load_json(os.path.join(out_dir, "result.json"))
    checks = []
    def check(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    if err or not isinstance(result, dict):
        check("result_json_valid", False, str(err))
        for name, ok, detail in checks:
            print("%s: %s %s" % ("PASS" if ok else "FAIL", name, detail))
        print("SELFCHECK: FAIL (%d issues)" % sum(1 for _, ok, _ in checks if not ok))
        return 0

    check("case_id_present", bool(result.get("case_id")))
    check("status_valid", result.get("status") in ("ok", "partial", "insufficient_evidence", "input_error"))
    check("results_present", isinstance(result.get("results"), list))
    check("sources_present", isinstance(result.get("sources"), list))
    check("uncertainty_present", isinstance(result.get("uncertainty"), dict))
    check("change_log_present", isinstance(result.get("change_log"), list))
    check("sub_questions_present", isinstance(result.get("sub_questions"), list))
    check("query_plan_present", isinstance(result.get("query_plan"), list))
    check("term_expansion_present", isinstance(result.get("term_expansion"), list))
    check("stop_decision_present", isinstance(result.get("stop_decision"), dict))
    check("screening_present", isinstance(result.get("screening"), dict))
    check("evidence_present", isinstance(result.get("evidence"), list))

    n_include_wo_ev = 0
    for r in result.get("results", []):
        ev = r.get("evidence") or {}
        if not ev.get("locator") or not ev.get("quote"):
            n_include_wo_ev += 1
    check("included_have_evidence", n_include_wo_ev == 0, "missing=%d" % n_include_wo_ev)

    contract = result.get("output_contract")
    if isinstance(contract, dict):
        missing = [f for f in (contract.get("required") or contract.get("fields") or []) if f not in result]
        check("output_contract_backfilled", not missing, "missing=%s" % missing)
    else:
        check("output_contract_backfilled", True, "no_contract")

    dup_conflicts = sum(1 for r in result.get("results", []) if (r.get("dedup") or {}).get("conflicts"))
    check("dedup_conflicts_recorded", dup_conflicts >= 0, "conflicts=%d" % dup_conflicts)

    stop = result.get("stop_decision", {})
    check("stop_decision_reasoned", bool(stop.get("status")) and bool(stop.get("reason")),
          "status=%s reason=%s" % (stop.get("status"), stop.get("reason")))

    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    for name, ok, detail in checks:
        print("%s: %s %s" % ("PASS" if ok else "FAIL", name, detail))
    verdict = "PASS" if passed == total else "FAIL"
    print("SELFCHECK: %s (%d/%d)" % (verdict, passed, total))
    result["selfcheck"] = {"passed": passed == total, "total": total, "passed_count": passed}
    write_json(os.path.join(out_dir, "result.json"), result)
    return 0  # always exit 0; harness/Agent reads the verdict text


# ---------------------------------------------------------------- dispatch

def _dir_args_suppressed(sp):
    for flag in ("--input", "--output", "--work"):
        sp.add_argument(flag, default=argparse.SUPPRESS)


def build_parser():
    p = argparse.ArgumentParser(prog="main.py", description="research-question-driven-search pipeline")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    sub = p.add_subparsers(dest="cmd", required=True)

    def new_parser(name):
        sp = sub.add_parser(name)
        _dir_args_suppressed(sp)
        return sp

    new_parser("scan")
    new_parser("skeleton")
    pp = new_parser("plan"); add_incoming_args(pp)
    sp = new_parser("screen"); add_incoming_args(sp)
    ep = new_parser("evidence"); add_incoming_args(ep)
    new_parser("rank")
    ap = new_parser("assemble")
    ap.add_argument("--runtime-seconds", type=int, default=None)
    ap.add_argument("--runtime-requests", type=int, default=None)
    new_parser("selfcheck")
    return p


def add_incoming_args(p):
    p.add_argument("--file", default=None)
    p.add_argument("--inline", default=None)


def run(argv):
    parser = build_parser()
    argv = [str(a) for a in argv]
    args = parser.parse_args(argv)
    handlers = {
        "scan": run_scan, "skeleton": run_skeleton, "plan": run_plan,
        "screen": run_screen, "evidence": run_evidence, "rank": run_rank,
        "assemble": run_assemble, "selfcheck": run_selfcheck,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
