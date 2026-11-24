#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pipeline.py — contract-first systematic literature screening pipeline.

Subcommands (run from the Skill root, e.g. /skills/weekly-skill):
  skeleton   scan /input, echo output_contract, write partial result + work files
  normalize  deduplicate records, emit dedup.json + judgments template
  finalize   merge judgments, verify evidence quotes, close counts, write outputs
  selfcheck  verify invariants and report PASS/FAIL

Design: the case.json `output_contract` is the single source of truth. The
contract is echoed recursively into result.json and every leaf path is recorded
in work/contract.json so finalize/selfcheck can prove field-for-field alignment.
change_log is an append-only ledger with monotonic sequence numbers/timestamps.

Standard library only. Defaults: --input /input --output /app/output --work /app/work.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULTS = {"input": "/input", "output": "/app/output", "work": "/app/work"}

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFF = "insufficient_evidence"
STATUS_INPUT_ERR = "input_error"

CONTRACT_KEYS = ["output_contract", "outputContract", "result_schema", "result_fields", "output"]
MAX_FILE_BYTES = 2 * 1024 * 1024

TITLE_KEYS = ["title", "ti", "t1", "题名", "标题"]
DOI_KEYS = ["doi", "do", "Doi", "DOI", "10.5281"]
AUTHOR_KEYS = ["authors", "author", "au", "作者"]
ABSTRACT_KEYS = ["abstract", "ab", "n2", "摘要"]
YEAR_KEYS = ["year", "py", "date", "年份", "发表年"]
JOURNAL_KEYS = ["journal", "jo", "source", "期刊", "来源"]
ID_KEYS = ["record_id", "id", "uid", "pmid", "accession", "编号"]


# --------------------------------------------------------------------------- #
# generic helpers
# --------------------------------------------------------------------------- #
def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def jdump(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def jload(path, default=None):
    if not path or not os.path.isfile(path):
        return default
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        return json.load(f)


def read_text(path):
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        return f.read()


def norm_text(s):
    if s is None:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def norm_title(s):
    return norm_text(s)


def similarity(a, b):
    na, nb = norm_title(a), norm_title(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def first_nonempty(record, keys):
    for k in keys:
        v = record.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)):
            return str(v)
    return None


# --------------------------------------------------------------------------- #
# change_log: append-only ledger
# --------------------------------------------------------------------------- #
def make_change_entry(action, detail, before=None, after=None, level="info"):
    entry = {
        "seq": None,  # filled on append
        "ts": now_iso(),
        "action": action,
        "detail": detail,
        "level": level,
    }
    if before is not None:
        entry["before"] = before
    if after is not None:
        entry["after"] = after
    return entry


def append_change(log, entry):
    log = list(log or [])
    entry = dict(entry)
    entry["seq"] = len(log) + 1
    log.append(entry)
    return log


def change_log_monotonic(log):
    if not log:
        return True
    seqs = [e.get("seq") for e in log]
    tss = [e.get("ts") for e in log]
    return seqs == list(range(1, len(log) + 1)) and all(
        tss[i] <= tss[i + 1] for i in range(len(tss) - 1) if tss[i] and tss[i + 1]
    )


# --------------------------------------------------------------------------- #
# contract echo: single source of truth
# --------------------------------------------------------------------------- #
def locate_contract(case_data):
    """Return the contract object or None. Search top level first, then recurse."""
    if not isinstance(case_data, dict):
        return None
    for key in CONTRACT_KEYS:
        if key in case_data:
            v = case_data[key]
            if isinstance(v, str):
                try:
                    v = json.loads(v)
                except Exception:
                    return None
            if isinstance(v, dict):
                return v
    # recursive fallback: first dict found under a contract-ish key anywhere
    found = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in CONTRACT_KEYS and isinstance(v, dict):
                    found.append(v)
                walk(v, path + [k])
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, path + [i])

    walk(case_data, [])
    return found[0] if found else None


def echo_contract(contract):
    """Recursively build a placeholder that preserves key names and container shape.

    Returns (echo, paths) where paths is a flat list of dotted paths to leaves.
    Scalars (str/bool/int/float/None) become None; lists become []; dicts recurse.
    """
    paths = []

    def rec(node, prefix):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                p = f"{prefix}.{k}" if prefix else str(k)
                out[k], sub = rec(v, p)
                if not sub:
                    paths.append(p)
            return out, True
        if isinstance(node, list):
            # A list in the contract is a container to be filled at runtime;
            # verify its presence as a list, not the inner template structure.
            if prefix:
                paths.append(prefix)
            return [], True
        return None, False

    echoed, _ = rec(contract, "")
    return echoed, paths


def set_path(obj, path, value):
    """Set a dotted path (list index segments marked '[]' are skipped: list stays [])."""
    parts = path.split(".")
    cur = obj
    for part in parts[:-1]:
        if part == "[]":
            continue
        if isinstance(cur, dict) and part not in cur:
            cur[part] = {}
        elif not isinstance(cur.get(part), (dict, list)):
            cur[part] = {}
        cur = cur[part]
    last = parts[-1]
    if last == "[]":
        return
    if isinstance(cur, dict):
        cur[last] = value


def path_exists(obj, path):
    """True if the dotted path is present (even when its value is null)."""
    parts = [p for p in path.split(".") if p and p != "[]"]
    cur = obj
    for part in parts:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.lstrip("-").isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return False
    return True


def verify_contract(result, paths):
    missing = [p for p in paths if not path_exists(result, p)]
    return missing


# --------------------------------------------------------------------------- #
# input discovery
# --------------------------------------------------------------------------- #
def find_input_files(input_dir):
    """Walk input dir, return list of (relpath, abspath). Skip oversized files."""
    files = []
    if not os.path.isdir(input_dir):
        return files
    for root, _, names in os.walk(input_dir):
        for name in names:
            ap = os.path.join(root, name)
            try:
                size = os.path.getsize(ap)
            except OSError:
                size = 0
            rel = os.path.relpath(ap, input_dir)
            files.append({"relpath": rel, "abspath": ap, "size": size,
                          "skipped": size > MAX_FILE_BYTES})
    files.sort(key=lambda f: f["relpath"])
    return files


def find_case_file(files, input_dir):
    for f in files:
        base = os.path.basename(f["relpath"]).lower()
        if base in ("case.json", "case.jsonl") or (base == "case.json"):
            return f
        if f["relpath"].lower().endswith("case.json"):
            return f
    return None


def load_case(case_file):
    if not case_file:
        return None, "no case.json found"
    path = case_file["abspath"]
    if case_file["relpath"].lower().endswith(".jsonl"):
        text = read_text(path)
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line), None
            except Exception as e:
                return None, f"bad jsonl in {path}: {e}"
        return None, "empty jsonl"
    try:
        return jload(path), None
    except Exception as e:
        return None, f"bad json in {path}: {e}"


def find_task_file(files):
    for f in files:
        if os.path.basename(f["relpath"]).lower() == "task.md":
            return f
    return None


# --------------------------------------------------------------------------- #
# record extraction (mechanical only; no scientific judgment)
# --------------------------------------------------------------------------- #
def pick(record, keys):
    return first_nonempty(record, keys)


def normalize_record(raw, source_file, idx):
    rid = pick(raw, ID_KEYS) or f"{source_file}#{idx}"
    title = pick(raw, TITLE_KEYS) or "not_reported"
    doi = pick(raw, DOI_KEYS) or None
    abstract = pick(raw, ABSTRACT_KEYS) or ""
    authors = pick(raw, AUTHOR_KEYS) or ""
    year = pick(raw, YEAR_KEYS) or ""
    journal = pick(raw, JOURNAL_KEYS) or ""
    return {
        "record_id": str(rid),
        "title": title,
        "authors": authors,
        "doi": doi,
        "year": str(year),
        "journal": journal,
        "abstract": abstract,
        "source_file": source_file,
        "locator": source_file,
    }


def parse_ris(text, source_file):
    records, cur = [], {}
    for line in text.splitlines():
        line = line.rstrip("\r\n")
        if not line.strip():
            continue
        if line.startswith("TY  -"):
            cur = {}
        elif line.startswith("ER  -"):
            if cur:
                records.append(normalize_record(cur, source_file, len(records) + 1))
            cur = {}
        elif len(line) >= 6 and line[2:4] == "  " and line[4] == "-":
            tag, val = line[:2].upper(), line[6:].strip()
            if tag == "TI":
                cur.setdefault("title", val)
            elif tag == "T1":
                cur["title"] = val
            elif tag == "AB":
                cur["abstract"] = (cur.get("abstract", "") + " " + val).strip()
            elif tag == "AU":
                cur["authors"] = (cur.get("authors", "") + "; " + val).strip("; ")
            elif tag == "DO":
                cur["doi"] = val
            elif tag in ("PY", "Y1"):
                cur["year"] = val[:4]
            elif tag in ("JO", "T2"):
                cur.setdefault("journal", val)
            elif tag in ("AN", "ID", "PMID"):
                cur.setdefault("record_id", val)
    if cur:
        records.append(normalize_record(cur, source_file, len(records) + 1))
    return records


def parse_bibtex(text, source_file):
    records = []
    # strip comments
    text = re.sub(r"(?m)^\s*%.*$", "", text)
    for m in re.finditer(r"@\w+\s*\{([^,]+),", text):
        key = m.group(1).strip()
        # take a slice until next @ or EOF
        start = m.end()
        nxt = text.find("@", start)
        body = text[start:nxt] if nxt != -1 else text[start:]
        rec = {"record_id": key}
        for field, val in re.findall(r"(\w+)\s*=\s*[{\"](.*?)[}\"]\s*,?", body, re.S):
            lf = field.lower()
            val = re.sub(r"\s+", " ", val).strip()
            if lf in ("title",):
                rec["title"] = val
            elif lf in ("abstract",):
                rec["abstract"] = val
            elif lf in ("author",):
                rec["authors"] = val.replace(" and ", "; ")
            elif lf in ("doi",):
                rec["doi"] = val
            elif lf in ("year", "date"):
                rec["year"] = re.sub(r"[^0-9].*$", "", val)
            elif lf in ("journal", "booktitle"):
                rec["journal"] = val
        if "title" in rec:
            records.append(normalize_record(rec, source_file, len(records) + 1))
    return records


def parse_csv_tsv(text, source_file):
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return []
    delim = "\t" if "\t" in lines[0] else ","
    try:
        reader = csv.DictReader(lines, delimiter=delim)
    except Exception:
        delim = "\t"
        reader = csv.DictReader(lines, delimiter=delim)
    records = []
    for row in reader:
        records.append(normalize_record(row, source_file, len(records) + 1))
    return records


def records_from_json(node, source_file, records, _seen):
    if isinstance(node, dict):
        if any(k in node for k in TITLE_KEYS) or "title" in node:
            records.append(normalize_record(node, source_file, len(records) + 1))
            _seen.add(id(node))
            return
        for v in node.values():
            records_from_json(v, source_file, records, _seen)
    elif isinstance(node, list):
        # a list of dicts that look like records
        if node and all(isinstance(x, dict) and ("title" in x or any(k in x for k in TITLE_KEYS)) for x in node[:10]):
            for x in node:
                records.append(normalize_record(x, source_file, len(records) + 1))
            return
        for item in node:
            records_from_json(item, source_file, records, _seen)


def extract_records_from_file(f, input_dir):
    path = f["abspath"]
    ext = os.path.splitext(path)[1].lower()
    src = f["relpath"]
    try:
        text = read_text(path)
    except Exception as e:
        return [], [f"unreadable {src}: {e}"]
    recs, warns = [], []
    try:
        if ext in (".ris",):
            recs = parse_ris(text, src)
        elif ext in (".bib",):
            recs = parse_bibtex(text, src)
        elif ext in (".csv", ".tsv", ".tab"):
            recs = parse_csv_tsv(text, src)
        elif ext in (".json", ".jsonl", ".ndjson"):
            if ext == ".jsonl" or ext == ".ndjson":
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        records_from_json(obj, src, recs, set())
                    except Exception as e:
                        warns.append(f"bad jsonl line in {src}: {e}")
            else:
                obj = json.loads(text)
                records_from_json(obj, src, recs, set())
        # .md/.txt notes: no record extraction by default; Agent reads them.
    except Exception as e:
        warns.append(f"parse error in {src}: {e}")
    return recs, warns


def discover_records(files, input_dir):
    records, warnings = [], []
    for f in files:
        if f["skipped"]:
            warnings.append(f"skipped oversized file {f['relpath']} ({f['size']} bytes)")
            continue
        base = os.path.basename(f["relpath"]).lower()
        if base in ("case.json", "task.md", "notes.md"):
            continue
        recs, warns = extract_records_from_file(f, input_dir)
        records.extend(recs)
        warnings.extend(warns)
    # case.json may itself carry a records list; discover there too
    cf = find_case_file(files, input_dir)
    if cf:
        data, _ = load_case(cf)
        if isinstance(data, dict):
            for key in ("records", "papers", "articles", "entries", "results", "items"):
                if isinstance(data.get(key), list):
                    for x in data[key]:
                        if isinstance(x, dict):
                            records.append(normalize_record(x, "case.json", len(records) + 1))
    return records, warnings


# --------------------------------------------------------------------------- #
# deduplication
# --------------------------------------------------------------------------- #
def dedup_key(record):
    doi = (record.get("doi") or "").strip().lower()
    if doi:
        return ("doi", doi)
    return ("title", norm_title(record.get("title", "")))


def cluster_records(records):
    """Group records; merge same study by DOI or identical normalized title."""
    clusters = []
    index = {}  # key -> cluster index
    for rec in records:
        doi = (rec.get("doi") or "").strip().lower()
        ntitle = norm_title(rec.get("title", ""))
        merged = False
        if doi:
            k = ("doi", doi)
            if k in index:
                clusters[index[k]]["records"].append(rec)
                clusters[index[k]]["merge_reason"].append("same DOI")
                merged = True
        if not merged and ntitle:
            k = ("title", ntitle)
            if k in index:
                clusters[index[k]]["records"].append(rec)
                clusters[index[k]]["merge_reason"].append("identical normalized title")
                merged = True
        if not merged:
            c = {"cluster_id": len(clusters) + 1, "records": [rec],
                 "merge_reason": [], "possible_duplicate_of": []}
            if doi:
                index[("doi", doi)] = len(clusters)
            if ntitle:
                index[("title", ntitle)] = len(clusters)
            clusters.append(c)
    # similarity pass for possible duplicates (0.85-0.95), never auto-merge
    for i in range(len(clusters)):
        for j in range(i + 1, len(clusters)):
            a = clusters[i]["records"][0]
            b = clusters[j]["records"][0]
            if dedup_key(a)[0] == "doi" and dedup_key(b)[0] == "doi":
                continue
            s = similarity(a.get("title", ""), b.get("title", ""))
            if 0.85 <= s < 0.95:
                clusters[i]["possible_duplicate_of"].append(clusters[j]["cluster_id"])
                clusters[j]["possible_duplicate_of"].append(clusters[i]["cluster_id"])
    return clusters


# --------------------------------------------------------------------------- #
# skeleton
# --------------------------------------------------------------------------- #
def cmd_skeleton(args):
    inp = args.input or DEFAULTS["input"]
    out = args.output or DEFAULTS["output"]
    work = args.work or DEFAULTS["work"]
    os.makedirs(work, exist_ok=True)
    os.makedirs(out, exist_ok=True)

    errors, degradations, warnings = [], [], []
    change_log = []
    files = find_input_files(inp)
    if not files:
        # empty input -> honest insufficient_evidence
        result = {
            "case_id": "unknown",
            "status": STATUS_INSUFF,
            "sources": [], "uncertainty": [], "pending_human": [],
            "errors": ["empty input directory: no files found"],
            "degradations": [], "runtime": {"started": now_iso()},
            "change_log": append_change([], make_change_entry(
                "skeleton", "empty input directory", level="warn")),
        }
        jdump(os.path.join(out, "result.json"), result)
        jdump(os.path.join(work, "inventory.json"), {"files": [], "warnings": []})
        jdump(os.path.join(work, "contract.json"), {"contract": None, "paths": []})
        print("skeleton: empty input -> insufficient_evidence")
        return 0

    case_file = find_case_file(files, inp)
    case_data, case_err = load_case(case_file)
    if case_err:
        errors.append(case_err)
        case_data = None

    task_file = find_task_file(files)
    task_text = read_text(task_file["abspath"]) if task_file else ""

    # case_id
    case_id = "unknown"
    if isinstance(case_data, dict):
        for k in ("case_id", "caseId", "id", "challenge_id"):
            if case_data.get(k):
                case_id = str(case_data[k])
                break
    if case_id == "unknown" and task_text:
        m = re.search(r"challenge_id[:：`]+\s*([A-Za-z0-9\-]+)", task_text)
        if m:
            case_id = m.group(1)

    # contract echo
    contract = locate_contract(case_data) if isinstance(case_data, dict) else None
    contract_echo, contract_paths = echo_contract(contract) if contract else ({}, [])
    if contract is None:
        errors.append("no output_contract found in case.json")

    # research question / criteria (best-effort string extraction)
    research_question = None
    if isinstance(case_data, dict):
        research_question = case_data.get("research_question") or case_data.get("question") \
            or case_data.get("researchQuestion")
    inclusion, exclusion = [], []
    if isinstance(case_data, dict):
        for k in ("inclusion_criteria", "inclusion", "include_criteria"):
            if isinstance(case_data.get(k), list):
                inclusion = case_data[k]
                break
        for k in ("exclusion_criteria", "exclusion", "exclude_criteria"):
            if isinstance(case_data.get(k), list):
                exclusion = case_data[k]
                break

    records, rec_warnings = discover_records(files, inp)
    warnings.extend(rec_warnings)

    inventory = {
        "input_dir": inp,
        "files": files,
        "case_file": case_file["relpath"] if case_file else None,
        "task_file": task_file["relpath"] if task_file else None,
        "warnings": warnings,
        "n_records_extracted": len(records),
    }

    # result skeleton with contract echo at top level
    result = dict(contract_echo)
    result["case_id"] = case_id
    result["status"] = STATUS_PARTIAL
    result.setdefault("sources", [])
    result.setdefault("uncertainty", [])
    result.setdefault("pending_human", [])
    result.setdefault("errors", errors)
    result.setdefault("degradations", degradations)
    result.setdefault("runtime", {"started": now_iso()})
    result["change_log"] = append_change([], make_change_entry(
        "skeleton", "contract echoed, records extracted", None,
        {"contract_fields": len(contract_paths), "records": len(records)}))

    # screening-specific standard keys (won't overwrite contract keys)
    if "research_question" not in result:
        result["research_question"] = research_question
    if "inclusion_criteria" not in result:
        result["inclusion_criteria"] = inclusion
    if "exclusion_criteria" not in result:
        result["exclusion_criteria"] = exclusion
    if "criteria_version" not in result:
        result["criteria_version"] = "v1.0.0"
    if "screening" not in result:
        result["screening"] = {"records": []}
    if "deduplication" not in result:
        result["deduplication"] = {"clusters": [], "n_unique": 0}
    if "conflicts" not in result:
        result["conflicts"] = []
    if "audit" not in result:
        result["audit"] = {}

    jdump(os.path.join(out, "result.json"), result)
    jdump(os.path.join(work, "inventory.json"), inventory)
    jdump(os.path.join(work, "records.json"),
          {"records": records, "count": len(records)})
    jdump(os.path.join(work, "protocol.json"), {
        "case_id": case_id,
        "research_question": research_question,
        "inclusion_criteria": inclusion,
        "exclusion_criteria": exclusion,
        "criteria_version": "v1.0.0",
        "frozen": False,
    })
    jdump(os.path.join(work, "contract.json"), {
        "contract": contract, "paths": contract_paths})
    print(f"skeleton: case={case_id} records={len(records)} "
          f"contract_fields={len(contract_paths)} status=partial")
    return 0


# --------------------------------------------------------------------------- #
# normalize
# --------------------------------------------------------------------------- #
def cmd_normalize(args):
    out = args.output or DEFAULTS["output"]
    work = args.work or DEFAULTS["work"]
    records_blob = jload(os.path.join(work, "records.json"), {"records": []})
    records = records_blob.get("records", [])
    clusters = cluster_records(records)

    dedup = {
        "n_input": len(records),
        "n_unique": len(clusters),
        "n_merged": len(records) - len(clusters),
        "clusters": clusters,
    }
    jdump(os.path.join(work, "dedup.json"), dedup)

    template = []
    for c in clusters:
        primary = c["records"][0]
        template.append({
            "record_id": primary["record_id"],
            "cluster_id": c["cluster_id"],
            "title": primary["title"],
            "doi": primary["doi"],
            "abstract": primary["abstract"],
            "decision": "",
            "reason_code": "",
            "evidence_quote": "",
            "evidence_verified": None,
            "reviewer_a": {"decision": None, "reason_code": None},
            "reviewer_b": {"decision": None, "reason_code": None},
            "conflict": {"is_conflict": False, "type": None, "detail": None},
            "arbitration": {"outcome": None, "rationale": None, "arbiter": None},
            "needs_human": False,
        })
    jdump(os.path.join(work, "judgments_template.json"), {"records": template})
    # don't overwrite an existing judgments.json the agent already filled
    if not os.path.isfile(os.path.join(work, "judgments.json")):
        jdump(os.path.join(work, "judgments.json"), {"records": template, "filled": False})
    print(f"normalize: input={len(records)} unique={len(clusters)} merged={len(records)-len(clusters)}")
    return 0


# --------------------------------------------------------------------------- #
# finalize
# --------------------------------------------------------------------------- #
def verify_quote(quote, record):
    hay = norm_text((record.get("title", "") or "") + " " + (record.get("abstract", "") or ""))
    needle = norm_text(quote)
    return bool(needle) and needle in hay


def cmd_finalize(args):
    inp = args.input or DEFAULTS["input"]
    out = args.output or DEFAULTS["output"]
    work = args.work or DEFAULTS["work"]
    degradations, errors = [], []

    records_blob = jload(os.path.join(work, "records.json"), {"records": []})
    records = records_blob.get("records", [])
    dedup = jload(os.path.join(work, "dedup.json"), {"clusters": []})
    clusters = dedup.get("clusters", [])
    judgments_blob = jload(os.path.join(work, "judgments.json"), {"records": []})
    judgments = judgments_blob.get("records", [])
    contract_meta = jload(os.path.join(work, "contract.json"), {"paths": []})
    contract_paths = contract_meta.get("paths", [])

    # map record_id -> original record (for quote verification)
    rec_by_id = {}
    for c in clusters:
        for r in c["records"]:
            rec_by_id[str(r["record_id"])] = r
    for r in records:
        rec_by_id.setdefault(str(r["record_id"]), r)

    screening_records = []
    include = exclude = uncertain = 0
    conflicts = []
    evidence_mismatch = 0

    for j in judgments:
        rid = str(j.get("record_id", ""))
        rec = rec_by_id.get(rid, {})
        unfilled = not j.get("decision")  # empty template = not yet judged
        decision = j.get("decision") or "uncertain"
        reason = j.get("reason_code") or "ex_unspecified"
        quote = j.get("evidence_quote") or ""

        if unfilled:
            decision = "uncertain"
            reason = "uncertain_unfilled"
            j["needs_human"] = True

        verified = None
        if decision in ("include", "exclude") and quote:
            verified = verify_quote(quote, rec)
            if not verified:
                evidence_mismatch += 1
                degradations.append(
                    f"evidence mismatch for {rid}: quote not found in title/abstract")
        elif decision == "uncertain":
            verified = None

        j["decision"] = decision
        j["reason_code"] = reason
        j["evidence_verified"] = verified
        if decision == "include":
            include += 1
        elif decision == "exclude":
            exclude += 1
        else:
            uncertain += 1

        cf = j.get("conflict") or {}
        if cf.get("is_conflict"):
            arb = j.get("arbitration") or {}
            conflicts.append({
                "record_id": rid, "type": cf.get("type"),
                "detail": cf.get("detail"), "arbitration": arb,
                "unresolved": not bool(arb.get("outcome")),
            })
            if not arb.get("outcome"):
                j["needs_human"] = True
                j["decision"] = "uncertain"

        screening_records.append(j)

    n_unique = len(clusters) if clusters else len(records)
    n_merged = dedup.get("n_merged", len(records) - n_unique)
    n_retrieved = dedup.get("n_input", len(records))

    # closure check
    closed = (n_retrieved - n_merged == n_unique) and (n_unique == include + exclude + uncertain)

    audit = {
        "steps": [
            {"step": "inventory", "input": None, "output": n_retrieved, "note": "records extracted"},
            {"step": "dedup", "input": n_retrieved, "output": n_unique,
             "merged": n_merged, "note": "clusters"},
            {"step": "screening", "input": n_unique,
             "output": {"include": include, "exclude": exclude, "uncertain": uncertain}},
            {"step": "conflict", "input": len(conflicts),
             "resolved": sum(1 for c in conflicts if not c["unresolved"]),
             "unresolved": sum(1 for c in conflicts if c["unresolved"])},
        ],
        "closure": {
            "retrieved": n_retrieved, "dedup_merged": n_merged, "unique": n_unique,
            "include": include, "exclude": exclude, "uncertain": uncertain,
            "closed": closed,
            "equation": f"{n_retrieved} - {n_merged} == {n_unique} == {include}+{exclude}+{uncertain}",
        },
    }

    # build final result: re-echo contract, then add standard keys
    result = jload(os.path.join(out, "result.json"), {})
    # re-echo contract placeholders so any missing contract leaf is present as null
    if contract_meta.get("contract") is not None:
        echo, _ = echo_contract(contract_meta["contract"])
        for k, v in echo.items():
            result.setdefault(k, v)
    result["case_id"] = result.get("case_id", "unknown")
    result["status"] = STATUS_OK if (closed and not errors and not degradations) else STATUS_PARTIAL
    if not judgments:
        result["status"] = STATUS_PARTIAL
    result["screening"] = {"records": screening_records}
    result["deduplication"] = dedup
    result["conflicts"] = conflicts
    result["audit"] = audit
    result["errors"] = list(errors)
    result["degradations"] = list(degradations)
    result.setdefault("sources", [])
    result.setdefault("uncertainty", [])
    result.setdefault("pending_human", [])
    if "runtime" not in result:
        result["runtime"] = {}
    result["runtime"]["finalized"] = now_iso()

    # append change_log entry (monotonic)
    result["change_log"] = append_change(
        result.get("change_log", []),
        make_change_entry(
            "finalize",
            f"merged judgments; evidence_mismatch={evidence_mismatch}; closure={closed}",
            None, {"include": include, "exclude": exclude, "uncertain": uncertain},
            level="error" if (evidence_mismatch or not closed) else "info"))

    jdump(os.path.join(out, "result.json"), result)

    # editable artifacts
    if screening_records:
        cols = ["record_id", "title", "doi", "decision", "reason_code",
                "evidence_quote", "evidence_verified", "needs_human"]
        with open(os.path.join(out, "screening_table.csv"), "w",
                  encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in screening_records:
                w.writerow({k: r.get(k) for k in cols})
    else:
        with open(os.path.join(out, "screening_table.csv"), "w",
                  encoding="utf-8-sig", newline="") as f:
            f.write("record_id,title,doi,decision,reason_code,evidence_quote,evidence_verified,needs_human\n")

    jdump(os.path.join(out, "audit_log.json"), {
        "audit": audit, "change_log": result["change_log"],
        "closure": audit["closure"]})

    protocol = jload(os.path.join(work, "protocol.json"), {})
    md_lines = ["# 检索与纳排协议", ""]
    md_lines.append(f"- case_id: {protocol.get('case_id', 'unknown')}")
    md_lines.append(f"- criteria_version: {protocol.get('criteria_version', 'v1.0.0')}")
    md_lines.append(f"- research_question: {protocol.get('research_question')}")
    md_lines.append(f"- inclusion_criteria: {json.dumps(protocol.get('inclusion_criteria', []), ensure_ascii=False)}")
    md_lines.append(f"- exclusion_criteria: {json.dumps(protocol.get('exclusion_criteria', []), ensure_ascii=False)}")
    md_lines.append("")
    md_lines.append(f"## 审计闭合\n\n{json.dumps(audit['closure'], ensure_ascii=False, indent=2)}")
    with open(os.path.join(out, "protocol.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines) + "\n")

    print(f"finalize: include={include} exclude={exclude} uncertain={uncertain} "
          f"closed={closed} mismatch={evidence_mismatch}")
    return 0


# --------------------------------------------------------------------------- #
# selfcheck
# --------------------------------------------------------------------------- #
def check(label, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    return ok


def cmd_selfcheck(args):
    out = args.output or DEFAULTS["output"]
    work = args.work or DEFAULTS["work"]
    result = jload(os.path.join(out, "result.json"))
    allok = True
    if result is None:
        print("[FAIL] result.json missing or unparsable")
        return 1
    allok &= check("result.json parses", True)
    allok &= check("case_id present", bool(result.get("case_id")),
                   str(result.get("case_id")))
    allok &= check("status present", bool(result.get("status")), str(result.get("status")))
    allok &= check("change_log present", isinstance(result.get("change_log"), list))
    allok &= check("change_log monotonic",
                   change_log_monotonic(result.get("change_log", [])))

    contract_meta = jload(os.path.join(work, "contract.json"), {"paths": []})
    paths = contract_meta.get("paths", [])
    missing = verify_contract(result, paths)
    allok &= check("contract fields aligned", not missing,
                   f"missing={missing}" if missing else f"{len(paths)} paths OK")

    screening = (result.get("screening") or {}).get("records", [])
    allok &= check("every record has decision+reason_code",
                   all(r.get("decision") and r.get("reason_code") for r in screening))
    bad_quotes = [r["record_id"] for r in screening
                  if r.get("evidence_verified") is False]
    allok &= check("no evidence mismatch", not bad_quotes, str(bad_quotes))

    audit = result.get("audit") or {}
    closure = audit.get("closure") or {}
    allok &= check("count closure", bool(closure.get("closed")),
                   closure.get("equation", ""))

    dup = result.get("deduplication") or {}
    merged_wrong = any(
        c.get("possible_duplicate_of") and not c.get("merge_reason")
        for c in dup.get("clusters", []))
    allok &= check("possible_duplicate not auto-merged", not merged_wrong)

    print(f"selfcheck: {'ALL PASS' if allok else 'HAS FAILURES'}")
    return 0 if allok else 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser():
    p = argparse.ArgumentParser(
        prog="pipeline.py",
        description="Contract-first systematic literature screening pipeline.")
    p.add_argument("command", choices=["skeleton", "normalize", "finalize", "selfcheck"])
    p.add_argument("--input", default=None, help="input dir (default /input)")
    p.add_argument("--output", default=None, help="output dir (default /app/output)")
    p.add_argument("--work", default=None, help="work dir (default /app/work)")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    fn = {
        "skeleton": cmd_skeleton,
        "normalize": cmd_normalize,
        "finalize": cmd_finalize,
        "selfcheck": cmd_selfcheck,
    }[args.command]
    try:
        return fn(args)
    except Exception as e:  # never crash the run; degrade honestly
        out = args.output or DEFAULTS["output"]
        work = args.work or DEFAULTS["work"]
        os.makedirs(out, exist_ok=True)
        os.makedirs(work, exist_ok=True)
        try:
            result = jload(os.path.join(out, "result.json"), {})
        except Exception:
            result = {}
        if not result:
            result = {"case_id": "unknown", "status": STATUS_INPUT_ERR}
        result["status"] = STATUS_INPUT_ERR
        result.setdefault("errors", []).append(f"{args.command} failed: {e}")
        result.setdefault("change_log", [])
        result["change_log"] = append_change(
            result["change_log"],
            make_change_entry(args.command, f"exception: {e}", level="error"))
        try:
            jdump(os.path.join(out, "result.json"), result)
        except Exception:
            pass
        print(f"[pipeline] {args.command} failed (degraded): {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
