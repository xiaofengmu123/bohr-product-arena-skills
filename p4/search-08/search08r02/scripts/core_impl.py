"""core_impl.py — implementation of the time-budgeted screening pipeline.

Design (variant: time budget + early write):
  * Every stage persists a checkpoint and rewrites result.json, so a valid
    partial deliverable exists at all times, not just a one-shot skeleton.
  * Stage budgets (sum = 900 s) and a model-request ledger live in
    /app/work/checkpoint.json; the `timebox` subcommand reports remaining
    budget and `log-request` records each analysis request honestly.
  * Evidence is validated as an exact substring of the record's title/abstract
    (whitespace- and diacritic-normalized), never inferred from labels.

Standard library only.
"""

from __future__ import annotations

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

# ---------------------------------------------------------------------------
# Budget contract (sums to 900 s)
# ---------------------------------------------------------------------------
TOTAL_BUDGET_S = 900
MODEL_REQUEST_BUDGET = 64
STAGE_BUDGETS = {
    "skeleton": 150,
    "protocol": 90,
    "normalize": 120,
    "screen": 330,
    "arbitrate": 60,
    "finalize": 90,
    "selfcheck": 60,
}
STAGE_ORDER = ["skeleton", "protocol", "normalize", "screen", "arbitrate", "finalize", "selfcheck"]
SUBCOMMANDS = ["skeleton", "protocol", "normalize", "screen", "arbitrate", "finalize", "selfcheck",
               "timebox", "log-request", "checkpoint"]

VALID_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
VALID_DECISIONS = {"include", "exclude", "uncertain"}
MAX_FILE_BYTES = 2 * 1024 * 1024
RECORD_LIST_KEYS = ("records", "papers", "references", "bibliography", "entries",
                    "documents", "citations", "items", "results", "studies")

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def read_text(path: str) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, OSError):
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def load_json(path, default=None):
    try:
        return json.loads(read_text(path))
    except Exception:
        return default


def write_json(path, obj) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def write_text(path, text) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig") as f:
        f.write(text)
    os.replace(tmp, path)


def norm_text(s) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def norm_title(t) -> str:
    if t is None:
        return ""
    t = norm_text(t)
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def norm_doi(d) -> str:
    if d is None:
        return ""
    d = str(d).strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    d = re.sub(r"^doi:\s*", "", d)
    return d.strip()


def title_similarity(a, b) -> float:
    na, nb = norm_title(a), norm_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def quote_matches(quote, haystack) -> bool:
    q = norm_text(quote)
    h = norm_text(haystack)
    if not q:
        return False
    return q in h


# ---------------------------------------------------------------------------
# Runtime context
# ---------------------------------------------------------------------------
class Context:
    def __init__(self, args):
        self.input_dir = args.input
        self.output_dir = args.output
        self.work_dir = args.work
        self.case_override = getattr(args, "case", None)
        self.quiet = getattr(args, "quiet", False)
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.work_dir, exist_ok=True)

    def p(self, rel):
        return os.path.join(self.work_dir, rel)

    def out(self, rel):
        return os.path.join(self.output_dir, rel)

    def log(self, msg):
        if not self.quiet:
            print(msg)

    def _new_checkpoint(self):
        return {
            "case_id": None,
            "started_at": now_iso(),
            "stages": {
                s: {"budget_s": STAGE_BUDGETS[s], "started": None, "finished": None,
                    "elapsed_s": None, "status": "pending", "input_count": None,
                    "output_count": None}
                for s in STAGE_ORDER
            },
            "model_requests": 0,
            "model_request_budget": MODEL_REQUEST_BUDGET,
            "elapsed_s": 0,
            "remaining_s": TOTAL_BUDGET_S,
            "errors": [],
            "degradations": [],
            "notes": [],
        }

    def load_checkpoint(self):
        cp = load_json(self.p("checkpoint.json"), None)
        if cp is None:
            cp = self._new_checkpoint()
        return cp

    def save_checkpoint(self, cp):
        write_json(self.p("checkpoint.json"), cp)

    def _recompute_budget(self, cp):
        started = cp.get("started_at")
        if not started:
            cp["elapsed_s"] = 0
            cp["remaining_s"] = TOTAL_BUDGET_S
            return
        try:
            elapsed = (_dt.datetime.now(_dt.timezone.utc)
                       - _dt.datetime.fromisoformat(started)).total_seconds()
        except Exception:
            elapsed = 0.0
        elapsed = max(0.0, elapsed)
        cp["elapsed_s"] = round(elapsed, 1)
        cp["remaining_s"] = round(max(0.0, TOTAL_BUDGET_S - elapsed), 1)

    def begin_stage(self, stage):
        cp = self.load_checkpoint()
        st = cp["stages"].setdefault(stage, {
            "budget_s": STAGE_BUDGETS.get(stage, 0), "started": None, "finished": None,
            "elapsed_s": None, "status": "pending", "input_count": None, "output_count": None})
        st["started"] = now_iso()
        st["status"] = "running"
        self.save_checkpoint(cp)
        return cp

    def end_stage(self, cp, stage, input_count=None, output_count=None, status="done"):
        st = cp["stages"].setdefault(stage, {})
        st["finished"] = now_iso()
        st["status"] = status
        if input_count is not None:
            st["input_count"] = input_count
        if output_count is not None:
            st["output_count"] = output_count
        try:
            start = _dt.datetime.fromisoformat(st.get("started") or st["finished"])
            end = _dt.datetime.fromisoformat(st["finished"])
            st["elapsed_s"] = max(0.0, round((end - start).total_seconds(), 1))
        except Exception:
            st["elapsed_s"] = None
        self._recompute_budget(cp)
        self.save_checkpoint(cp)
        return cp

    def record_error(self, stage, exc):
        cp = self.load_checkpoint()
        cp.setdefault("errors", []).append({
            "stage": stage, "at": now_iso(),
            "type": type(exc).__name__, "message": str(exc)})
        self.save_checkpoint(cp)

    def log_degradation(self, stage, msg):
        cp = self.load_checkpoint()
        cp.setdefault("degradations", []).append({"stage": stage, "at": now_iso(), "message": msg})
        self.save_checkpoint(cp)

    def load_result(self):
        return load_json(self.out("result.json"), None)

    def save_result(self, res):
        write_json(self.out("result.json"), res)

    def write_minimal_result(self):
        res = self.load_result()
        if res is None:
            res = {"case_id": "unknown", "status": "input_error"}
        res.setdefault("case_id", "unknown")
        if res.get("status") not in VALID_STATUS:
            res["status"] = "input_error"
        res.setdefault("change_log", []).append({"at": now_iso(), "event": "minimal_fallback_written"})
        self.save_result(res)


# ---------------------------------------------------------------------------
# Input discovery & record parsing
# ---------------------------------------------------------------------------
def discover_input(ctx):
    inv = {"files": [], "case_path": None, "task_path": None, "record_sources": [], "skipped": []}
    root = ctx.input_dir
    if not os.path.isdir(root):
        return inv
    for dirpath, _dirs, files in os.walk(root):
        for name in sorted(files):
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            if size > MAX_FILE_BYTES:
                inv["skipped"].append({"file": rel, "reason": "oversize", "bytes": size})
                continue
            inv["files"].append({"file": rel, "bytes": size})
            low = name.lower()
            if low == "case.json" and inv["case_path"] is None:
                inv["case_path"] = path
            elif low == "task.md" and inv["task_path"] is None:
                inv["task_path"] = path
            elif low.endswith((".csv", ".tsv", ".ris", ".bib", ".json", ".jsonl", ".txt", ".md")):
                inv["record_sources"].append({"file": rel, "path": path,
                                              "ext": os.path.splitext(low)[1]})
    return inv


def _pick(obj, keys):
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key.lower() in keys:
                return val
    return None


def _as_str(v):
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v).strip()


def _is_record(d):
    return isinstance(d, dict) and any(k.lower() in ("title", "ti", "doi", "do", "abstract", "ab") for k in d)


def normalize_record(raw, source_file, index):
    raw = raw if isinstance(raw, dict) else {}
    title = _as_str(_pick(raw, ("title", "ti", "name", "t")))
    doi = _as_str(_pick(raw, ("doi", "do", "electronic_id", "url_doi")))
    abstract = _as_str(_pick(raw, ("abstract", "ab", "summary", "n2")))
    year = _as_str(_pick(raw, ("year", "py", "date", "publication_year")))
    journal = _as_str(_pick(raw, ("journal", "jo", "t2", "source", "venue", "container_title")))
    authors = _pick(raw, ("authors", "au", "author", "creator"))
    if isinstance(authors, str):
        authors = [a.strip() for a in re.split(r"[;,]| and ", authors) if a.strip()]
    elif isinstance(authors, list):
        authors = [a.get("name", a.get("family", "")) if isinstance(a, dict) else str(a) for a in authors]
    else:
        authors = []
    authors = [str(a).strip() for a in authors if str(a).strip()]
    rid = _as_str(_pick(raw, ("id", "record_id", "uuid", "key", "uid"))) or f"rec-{index + 1:03d}"
    return {
        "record_id": rid,
        "title": title,
        "doi": doi,
        "abstract": abstract,
        "year": year,
        "journal": journal,
        "authors": authors,
        "source_file": source_file,
        "locator": f"records[{index}]" if source_file == "case.json" else f"{source_file}#{index + 1}",
        "raw": raw,
    }


def find_record_lists(obj, out, source_file):
    if isinstance(obj, list):
        for i, item in enumerate(obj):
            if _is_record(item):
                out.append(normalize_record(item, source_file, i))
        return
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key.lower() in RECORD_LIST_KEYS and isinstance(val, list):
                for i, item in enumerate(val):
                    if _is_record(item):
                        out.append(normalize_record(item, source_file, i))
            elif isinstance(val, (dict, list)):
                find_record_lists(val, out, source_file)


def parse_ris(text):
    recs, cur = [], {}
    for line in text.splitlines():
        if line.startswith("ER  -") or line.strip() == "ER":
            if cur:
                recs.append(cur)
            cur = {}
        elif len(line) >= 6 and line[2:6] == "  - ":
            tag, val = line[:2].upper(), line[6:].strip()
            if tag == "TI":
                cur["title"] = val
            elif tag == "DO":
                cur["doi"] = val
            elif tag == "AB":
                cur["abstract"] = (cur.get("abstract", "") + " " + val).strip()
            elif tag == "PY":
                cur["year"] = val
            elif tag in ("JO", "T2"):
                cur["journal"] = val
            elif tag in ("AU", "A1"):
                cur.setdefault("authors", []).append(val)
    if cur:
        recs.append(cur)
    return recs


def parse_bib(text):
    recs = []
    for ent in re.findall(r"@\w+\s*\{[^@]*", text, flags=re.DOTALL):
        cur = {}
        m = re.search(r"@\w+\s*\{\s*([^,]+),", ent)
        if m:
            cur["id"] = m.group(1).strip()
        for field in ("title", "doi", "abstract", "year", "journal", "author"):
            m = re.search(r"\b" + field + r"\s*=\s*[{\"]([^}\"]*)[}\"]",
                          ent, flags=re.IGNORECASE | re.DOTALL)
            if m:
                cur[field] = re.sub(r"\s+", " ", m.group(1)).strip()
        if cur.get("author"):
            cur["authors"] = [a.strip() for a in re.split(r"\s+and\s+", cur["author"])]
        if any(k in cur for k in ("title", "doi")):
            recs.append(cur)
    return recs


def parse_delimited(text, ext):
    sample = text[:8192]
    delim = "\t" if ext == ".tsv" else ","
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
        delim = dialect.delimiter
    except Exception:
        pass
    recs = []
    try:
        reader = csv.DictReader(io.StringIO(text), delimiter=delim)
        for row in reader:
            if row:
                recs.append(dict((k.strip().lower(), (v or "").strip()) for k, v in row.items()))
    except Exception:
        pass
    return recs


def collect_records(ctx, inv):
    records, sources = [], []
    case_data = None
    if ctx.case_override and os.path.isfile(ctx.case_override):
        inv["case_path"] = ctx.case_override
    if inv["case_path"] and os.path.isfile(inv["case_path"]):
        case_data = load_json(inv["case_path"], None)
        if case_data is None:
            ctx.log_degradation("skeleton", "case.json unreadable as JSON")
        else:
            find_record_lists(case_data, records, "case.json")
    for src in inv["record_sources"]:
        try:
            text = read_text(src["path"])
        except OSError as e:
            ctx.log_degradation("skeleton", f"cannot read {src['file']}: {e}")
            continue
        raw_recs = []
        if src["ext"] == ".ris":
            raw_recs = parse_ris(text)
        elif src["ext"] == ".bib":
            raw_recs = parse_bib(text)
        elif src["ext"] in (".csv", ".tsv"):
            raw_recs = parse_delimited(text, src["ext"])
        elif src["ext"] == ".jsonl":
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    raw_recs.append(obj)
        elif src["ext"] == ".json":
            obj = load_json(src["path"], None)
            if isinstance(obj, list):
                raw_recs = [o for o in obj if isinstance(o, dict)]
            elif isinstance(obj, dict):
                find_record_lists(obj, raw_recs, src["file"])
        # .txt/.md are treated as notes, not auto-parsed
        for i, rr in enumerate(raw_recs):
            if isinstance(rr, dict):
                records.append(normalize_record(rr, src["file"], i))
    # de-duplicate identical (record_id, normalized title) pairs
    seen, deduped = set(), []
    for r in records:
        key = (r["record_id"], norm_title(r["title"]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    sources.append({"file": "case.json", "role": "case_metadata"})
    for s in inv["record_sources"]:
        sources.append({"file": s["file"], "role": "record_source", "ext": s["ext"]})
    return deduped, case_data, sources


# ---------------------------------------------------------------------------
# Contract / case extraction
# ---------------------------------------------------------------------------
def extract_contract(case_data):
    if not isinstance(case_data, dict):
        return {}
    for key in ("output_contract", "outputContract", "result_schema", "result_fields", "output"):
        if key in case_data and isinstance(case_data[key], (dict, list)):
            return case_data[key]
    for key, val in case_data.items():
        if isinstance(val, dict) and key.lower() in ("task", "schema", "contract", "output"):
            for k2 in ("output_contract", "outputContract", "result_schema", "result_fields", "fields"):
                if k2 in val and isinstance(val[k2], (dict, list)):
                    return val[k2]
    return {}


def contract_field_defaults(contract):
    fields = {}
    if isinstance(contract, dict):
        if isinstance(contract.get("fields"), dict):
            src = contract["fields"]
        elif isinstance(contract.get("properties"), dict):
            src = contract["properties"]
        else:
            src = contract
        for name, spec in src.items():
            if name in ("fields", "properties", "type", "description", "$schema", "title"):
                continue
            default = None
            if isinstance(spec, dict):
                if spec.get("type") == "array" or "items" in spec:
                    default = []
                elif "default" in spec:
                    default = spec["default"]
                elif spec.get("type") == "string":
                    default = ""
            elif isinstance(spec, list):
                default = []
            fields[name] = default
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                fields[item] = None
            elif isinstance(item, dict):
                name = item.get("name") or item.get("field") or item.get("key")
                if name:
                    fields[name] = [] if item.get("type") == "array" else None
    return fields


def apply_contract(res, contract):
    fields = contract_field_defaults(contract)
    for name, default in fields.items():
        res.setdefault(name, default)
    return res, fields


def extract_case_id(case_data, task_text):
    if isinstance(case_data, dict):
        for key in ("case_id", "caseId", "id"):
            if case_data.get(key):
                return str(case_data[key])
    if task_text:
        m = re.search(r"case[_\- ]?id[:\s]*[`\"']?([A-Za-z0-9_\-]+)", task_text, flags=re.IGNORECASE)
        if m:
            return m.group(1)
    return "unknown"


def extract_research_question(case_data):
    if isinstance(case_data, dict):
        for key in ("research_question", "researchQuestion", "question", "query", "search_query", "objective"):
            if case_data.get(key):
                return str(case_data[key])
    return ""


def extract_criteria(case_data):
    inc, exc = [], []
    if isinstance(case_data, dict):
        for key in ("inclusion_criteria", "inclusion", "include_criteria"):
            v = case_data.get(key)
            if isinstance(v, list):
                inc = [str(x) for x in v]
            elif v:
                inc = [str(v)]
        for key in ("exclusion_criteria", "exclusion", "exclude_criteria"):
            v = case_data.get(key)
            if isinstance(v, list):
                exc = [str(x) for x in v]
            elif v:
                exc = [str(v)]
    return inc, exc


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------
def dedup_records(records):
    n = len(records)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    doi_map, title_map = {}, {}
    for i, r in enumerate(records):
        d = norm_doi(r["doi"])
        t = norm_title(r["title"])
        if d:
            if d in doi_map:
                union(i, doi_map[d])
            else:
                doi_map[d] = i
        if t:
            if t in title_map:
                union(i, title_map[t])
            else:
                title_map[t] = i

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    clusters = []
    for gid, idxs in groups.items():
        idxs = sorted(idxs)
        rep = records[idxs[0]]
        clusters.append({
            "cluster_id": f"C{len(clusters) + 1:02d}",
            "representative": rep,
            "member_indices": idxs,
            "member_count": len(idxs),
            "merge_reason": "single" if len(idxs) == 1 else "exact_doi_or_title_match",
        })

    possible = []
    reps = [c["representative"] for c in clusters]
    for i in range(len(clusters)):
        for j in range(i + 1, len(clusters)):
            a, b = reps[i]["title"], reps[j]["title"]
            if not a or not b:
                continue
            sim = title_similarity(a, b)
            if 0.85 <= sim < 1.0:
                possible.append({
                    "cluster_a": clusters[i]["cluster_id"],
                    "cluster_b": clusters[j]["cluster_id"],
                    "similarity": round(sim, 3),
                    "title_a": a,
                    "title_b": b,
                    "action": "possible_duplicate_do_not_auto_merge",
                })
    return {
        "unique_count": len(clusters),
        "merged_count": n - len(clusters),
        "clusters": clusters,
        "possible_duplicates": possible,
    }


def build_template(dedup):
    entries = []
    for c in dedup["clusters"]:
        rep = c["representative"]
        entries.append({
            "cluster_id": c["cluster_id"],
            "record_id": rep["record_id"],
            "title": rep["title"],
            "doi": rep["doi"] or None,
            "decision": None,
            "reason_code": None,
            "evidence_quote": "",
            "evidence_locator": rep["locator"],
            "evidence_verified": None,
            "reviewer_a": None,
            "reviewer_b": None,
            "conflict": None,
            "arbitration": None,
            "needs_human": False,
            "notes": "",
        })
    return entries


def _record_text(r):
    return (r.get("title") or "") + " " + (r.get("abstract") or "") + " " + (r.get("full_text") or "")


# ---------------------------------------------------------------------------
# Stage commands
# ---------------------------------------------------------------------------
def cmd_skeleton(ctx):
    cp = ctx.begin_stage("skeleton")
    inv = discover_input(ctx)
    records, case_data, sources = collect_records(ctx, inv)
    contract = extract_contract(case_data)
    task_text = ""
    if inv["task_path"] and os.path.isfile(inv["task_path"]):
        task_text = read_text(inv["task_path"])
    case_id = extract_case_id(case_data, task_text)
    rq = extract_research_question(case_data)
    inc, exc = extract_criteria(case_data)
    contract_fields = contract_field_defaults(contract)

    cp = ctx.load_checkpoint()  # refresh degradations logged during collect
    if not inv["files"]:
        status = "insufficient_evidence"
    elif not records and inv["case_path"] is None and not inv["record_sources"]:
        status = "insufficient_evidence"
    else:
        status = "partial"

    res = {"case_id": case_id}
    res, _ = apply_contract(res, contract)
    res.update({
        "status": status,
        "artifacts": {
            "result": ctx.out("result.json"),
            "screening_table": ctx.out("screening_table.csv"),
            "audit_log": ctx.out("audit_log.json"),
            "protocol": ctx.out("protocol.md"),
        },
        "sources": sources,
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{"at": now_iso(), "event": "skeleton_written", "status": status}],
        "research_question": rq,
        "criteria": {"inclusion": inc, "exclusion": exc, "criteria_version": None, "frozen": False},
        "retrieval": {"query": rq, "databases": [], "date_range": {}, "languages": [],
                      "retrieval_date": None, "notes": "not_configured"},
        "deduplication": {"unique_count": len(records), "merged_count": 0,
                          "clusters": [], "possible_duplicates": []},
        "screening": {"records": [], "summary": {}},
        "conflicts": [],
        "audit": {
            "counts": {"retrieved": len(records), "merged": 0, "unique": len(records),
                       "include": 0, "exclude": 0, "uncertain": 0, "undecided": len(records),
                       "pending_human": 0, "closed": False},
            "closure_formula": "retrieved - merged == unique == include + exclude + uncertain (undecided must be 0)",
        },
        "errors": [],
        "degradations": list(cp.get("degradations", [])),
        "runtime": {
            "stage_budgets_s": STAGE_BUDGETS, "total_budget_s": TOTAL_BUDGET_S,
            "model_request_budget": MODEL_REQUEST_BUDGET,
            "model_requests_used": cp.get("model_requests", 0),
        },
        "output_contract_fields": sorted(contract_fields.keys()),
    })
    write_json(ctx.p("inventory.json"), inv)
    write_json(ctx.p("records.json"), records)
    protocol = {
        "research_question": rq,
        "criteria_version": None,
        "frozen": False,
        "inclusion": inc,
        "exclusion": exc,
        "retrieval": res["retrieval"],
        "dedup_rules": {"doi_exact": "merge", "title_exact": "merge",
                        "title_similarity_0.85_1.0": "possible_duplicate_no_auto_merge"},
        "reason_codes_ref": "references/reason_codes.md",
    }
    write_json(ctx.p("protocol.json"), protocol)
    ctx.save_result(res)
    cp["case_id"] = case_id
    ctx.end_stage(cp, "skeleton", input_count=0, output_count=len(records))
    ctx.log(f"skeleton: status={status} records={len(records)} case_id={case_id}")
    return 0


def cmd_protocol(ctx):
    cp = ctx.begin_stage("protocol")
    protocol = load_json(ctx.p("protocol.json"), {})
    if not protocol.get("frozen"):
        inv = discover_input(ctx)
        case_path = ctx.case_override or inv["case_path"]
        case_data = load_json(case_path, {}) if case_path else {}
        protocol.setdefault("research_question", extract_research_question(case_data))
        protocol.setdefault("inclusion", extract_criteria(case_data)[0])
        protocol.setdefault("exclusion", extract_criteria(case_data)[1])
        protocol.setdefault("retrieval", {"query": protocol.get("research_question", ""),
                                          "databases": [], "date_range": {}, "languages": [],
                                          "retrieval_date": None, "notes": "to_configure_by_agent"})
        protocol.setdefault("dedup_rules", {"doi_exact": "merge", "title_exact": "merge",
                                            "title_similarity_0.85_1.0": "possible_duplicate_no_auto_merge"})
        protocol.setdefault("frozen", False)
        protocol.setdefault("criteria_version", None)
        write_json(ctx.p("protocol.json"), protocol)
    res = ctx.load_result() or {}
    res["research_question"] = protocol.get("research_question", "")
    res["criteria"] = {
        "inclusion": protocol.get("inclusion", []),
        "exclusion": protocol.get("exclusion", []),
        "criteria_version": protocol.get("criteria_version"),
        "frozen": bool(protocol.get("frozen")),
    }
    res["retrieval"] = protocol.get("retrieval", res.get("retrieval", {}))
    res["change_log"] = res.get("change_log", [])
    res["change_log"].append({"at": now_iso(), "event": "protocol_checked",
                              "frozen": bool(protocol.get("frozen"))})
    ctx.save_result(res)
    ctx.end_stage(cp, "protocol", input_count=1, output_count=1)
    ctx.log(f"protocol: frozen={protocol.get('frozen')} version={protocol.get('criteria_version')}")
    return 0


def cmd_normalize(ctx):
    cp = ctx.begin_stage("normalize")
    records = load_json(ctx.p("records.json"), [])
    dedup = dedup_records(records)
    write_json(ctx.p("dedup.json"), dedup)
    write_json(ctx.p("judgments_template.json"), build_template(dedup))
    if load_json(ctx.p("judgments.json"), None) is None:
        write_json(ctx.p("judgments.json"), build_template(dedup))
    res = ctx.load_result() or {}
    res["deduplication"] = dedup
    res["change_log"] = res.get("change_log", [])
    res["change_log"].append({"at": now_iso(), "event": "normalize_done",
                              "unique": dedup["unique_count"], "merged": dedup["merged_count"]})
    ctx.save_result(res)
    ctx.end_stage(cp, "normalize", input_count=len(records), output_count=dedup["unique_count"])
    ctx.log(f"normalize: unique={dedup['unique_count']} merged={dedup['merged_count']} "
            f"possible_duplicates={len(dedup['possible_duplicates'])}")
    return 0


def cmd_screen(ctx):
    cp = ctx.begin_stage("screen")
    records = load_json(ctx.p("records.json"), [])
    judgments = load_json(ctx.p("judgments.json"), [])
    text_by_id = {r["record_id"]: _record_text(r) for r in records}
    filled = verified = 0
    for j in judgments:
        if j.get("decision") in VALID_DECISIONS:
            filled += 1
            hay = text_by_id.get(j.get("record_id"), "")
            if j.get("evidence_quote") and quote_matches(j["evidence_quote"], hay):
                j["evidence_verified"] = True
                verified += 1
            elif j.get("decision") == "uncertain" and not j.get("evidence_quote"):
                j["evidence_verified"] = None
            else:
                j["evidence_verified"] = False
    write_json(ctx.p("judgments.json"), judgments)
    ctx._recompute_budget(cp)
    res = ctx.load_result() or {}
    res["screening"] = {"records": judgments,
                        "summary": {"decided": filled, "total": len(judgments),
                                    "evidence_verified": verified}}
    res["runtime"] = {
        "stage_budgets_s": STAGE_BUDGETS, "total_budget_s": TOTAL_BUDGET_S,
        "model_request_budget": MODEL_REQUEST_BUDGET,
        "model_requests_used": cp.get("model_requests", 0),
        "elapsed_s": cp.get("elapsed_s", 0), "remaining_s": cp.get("remaining_s", TOTAL_BUDGET_S),
    }
    ctx.save_result(res)
    ctx.end_stage(cp, "screen", input_count=len(judgments), output_count=filled, status="in_progress")
    ctx.log(f"screen: {filled}/{len(judgments)} decided, {verified} evidence verified, "
            f"remaining_s={cp['remaining_s']}, model_requests={cp['model_requests']}/{MODEL_REQUEST_BUDGET}")
    return 0


def cmd_arbitrate(ctx):
    cp = ctx.begin_stage("arbitrate")
    judgments = load_json(ctx.p("judgments.json"), [])
    conflicts = []
    for j in judgments:
        ra, rb = j.get("reviewer_a"), j.get("reviewer_b")
        if not ra or not rb:
            j["conflict"] = {"is_conflict": False, "type": None}
            continue
        a_dec, b_dec = ra.get("decision"), rb.get("decision")
        a_rc, b_rc = ra.get("reason_code"), rb.get("reason_code")
        a_ev, b_ev = ra.get("evidence_quote"), rb.get("evidence_quote")
        if a_dec != b_dec:
            ctype = "decision"
        elif a_rc != b_rc:
            ctype = "reason_code"
        elif a_ev != b_ev:
            ctype = "evidence"
        else:
            j["conflict"] = {"is_conflict": False, "type": None}
            continue
        j["conflict"] = {"is_conflict": True, "type": ctype, "reviewer_a": ra, "reviewer_b": rb}
        conflicts.append({"record_id": j.get("record_id"), "cluster_id": j.get("cluster_id"),
                          "type": ctype, "reviewer_a": ra, "reviewer_b": rb,
                          "arbitration": j.get("arbitration")})
    write_json(ctx.p("judgments.json"), judgments)
    write_json(ctx.p("arbitration.json"), conflicts)
    unresolved = [c for c in conflicts if not c.get("arbitration")]
    res = ctx.load_result() or {}
    res["conflicts"] = conflicts
    res["screening"] = {"records": judgments,
                        "summary": res.get("screening", {}).get("summary", {})}
    if unresolved:
        res["pending_human"] = [{"record_id": c["record_id"], "reason": "unresolved_conflict"}
                                for c in unresolved]
    res["change_log"] = res.get("change_log", [])
    res["change_log"].append({"at": now_iso(), "event": "arbitrate_done",
                              "conflicts": len(conflicts), "unresolved": len(unresolved)})
    ctx.save_result(res)
    ctx.end_stage(cp, "arbitrate", input_count=len(judgments), output_count=len(conflicts))
    ctx.log(f"arbitrate: conflicts={len(conflicts)} unresolved={len(unresolved)}")
    return 0


def cmd_finalize(ctx):
    cp = ctx.begin_stage("finalize")
    records = load_json(ctx.p("records.json"), [])
    dedup = load_json(ctx.p("dedup.json"), {"clusters": [], "possible_duplicates": []})
    judgments = load_json(ctx.p("judgments.json"), [])
    protocol = load_json(ctx.p("protocol.json"), {})
    res = ctx.load_result() or {}

    text_by_id = {r["record_id"]: _record_text(r) for r in records}
    degradations = list((ctx.load_checkpoint()).get("degradations", []))
    evidence_mismatches = []
    for j in judgments:
        hay = text_by_id.get(j.get("record_id"), "")
        if j.get("decision") in ("include", "exclude") and j.get("evidence_quote"):
            ok = quote_matches(j["evidence_quote"], hay)
            j["evidence_verified"] = bool(ok)
            if not ok:
                evidence_mismatches.append(j.get("record_id"))
        elif j.get("decision") == "uncertain":
            j["evidence_verified"] = None if not j.get("evidence_quote") else bool(quote_matches(j["evidence_quote"], hay))
        else:
            j["evidence_verified"] = None

    raw_count = len(records)
    unique_count = dedup.get("unique_count", len(judgments))
    merged_count = dedup.get("merged_count", raw_count - unique_count)
    include = sum(1 for j in judgments if j.get("decision") == "include")
    exclude = sum(1 for j in judgments if j.get("decision") == "exclude")
    uncertain = sum(1 for j in judgments if j.get("decision") == "uncertain")
    undecided = sum(1 for j in judgments if j.get("decision") not in VALID_DECISIONS)
    pending_ids = [j.get("record_id") for j in judgments if j.get("needs_human") or j.get("decision") == "uncertain"]
    closed = (raw_count - merged_count == unique_count) and (unique_count == include + exclude + uncertain + undecided) and undecided == 0

    audit = {
        "counts": {
            "retrieved": raw_count, "merged": merged_count, "unique": unique_count,
            "include": include, "exclude": exclude, "uncertain": uncertain,
            "undecided": undecided, "pending_human": len(set(pending_ids)), "closed": bool(closed),
        },
        "closure_formula": "retrieved - merged == unique == include + exclude + uncertain (undecided must be 0)",
        "stages": cp.get("stages", {}),
        "evidence_mismatches": evidence_mismatches,
    }

    status = "ok" if closed and not evidence_mismatches else "partial"
    if raw_count == 0:
        status = "insufficient_evidence"

    res["status"] = status
    res["deduplication"] = dedup
    res["screening"] = {"records": judgments,
                        "summary": {"include": include, "exclude": exclude, "uncertain": uncertain,
                                    "undecided": undecided,
                                    "evidence_verified": sum(1 for j in judgments if j.get("evidence_verified"))}}
    res["audit"] = audit
    res["errors"] = cp.get("errors", [])
    res["degradations"] = degradations
    res["pending_human"] = [{"record_id": rid, "reason": "needs_human_or_uncertain"}
                            for rid in sorted(set(pending_ids))]
    res["runtime"] = {
        "stage_budgets_s": STAGE_BUDGETS, "total_budget_s": TOTAL_BUDGET_S,
        "model_request_budget": MODEL_REQUEST_BUDGET,
        "model_requests_used": cp.get("model_requests", 0),
        "elapsed_s": cp.get("elapsed_s", 0), "remaining_s": cp.get("remaining_s", TOTAL_BUDGET_S),
    }
    res["change_log"] = res.get("change_log", [])
    res["change_log"].append({"at": now_iso(), "event": "finalize_done", "status": status, "closed": bool(closed)})

    write_screening_csv(ctx, judgments)
    write_json(ctx.out("audit_log.json"), {
        "audit": audit, "change_log": res["change_log"], "runtime": res["runtime"],
        "degradations": degradations, "errors": res["errors"]})
    write_protocol_md(ctx, protocol, dedup, audit)
    ctx.save_result(res)
    ctx.end_stage(cp, "finalize", input_count=raw_count, output_count=unique_count)
    ctx.log(f"finalize: status={status} closed={closed} unique={unique_count} "
            f"include={include} exclude={exclude} uncertain={uncertain}")
    return 0


def write_screening_csv(ctx, judgments):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["cluster_id", "record_id", "title", "doi", "decision", "reason_code",
                "evidence_quote", "evidence_verified", "needs_human"])
    for j in judgments:
        w.writerow([
            j.get("cluster_id", ""), j.get("record_id", ""), j.get("title", ""),
            j.get("doi") or "", j.get("decision") or "", j.get("reason_code") or "",
            j.get("evidence_quote") or "",
            "" if j.get("evidence_verified") is None else ("true" if j.get("evidence_verified") else "false"),
            "true" if j.get("needs_human") else "false",
        ])
    os.makedirs(ctx.output_dir, exist_ok=True)
    with open(ctx.out("screening_table.csv"), "w", encoding="utf-8-sig", newline="") as f:
        f.write(buf.getvalue())


def write_protocol_md(ctx, protocol, dedup, audit):
    lines = ["# 筛选协议（protocol.md）", "",
             f"- criteria_version: {protocol.get('criteria_version') or 'not_set'}",
             f"- frozen: {protocol.get('frozen', False)}", "",
             "## 研究问题", protocol.get("research_question") or "（未填写）", "",
             "## 纳入标准"]
    for c in protocol.get("inclusion", []) or []:
        lines.append(f"- {c}")
    lines += ["", "## 排除标准"]
    for c in protocol.get("exclusion", []) or []:
        lines.append(f"- {c}")
    lines += ["", "## 去重",
              f"- 唯一记录数: {dedup.get('unique_count')}",
              f"- 合并数: {dedup.get('merged_count')}",
              f"- 待人工复核相似对: {len(dedup.get('possible_duplicates', []))}", "",
              "## 计数闭合",
              "```json", json.dumps(audit.get("counts", {}), ensure_ascii=False, indent=2), "```"]
    write_text(ctx.out("protocol.md"), "\n".join(lines) + "\n")


def cmd_selfcheck(ctx):
    cp = ctx.begin_stage("selfcheck")
    res = load_json(ctx.out("result.json"), None)
    records = load_json(ctx.p("records.json"), [])
    judgments = load_json(ctx.p("judgments.json"), [])
    dedup = load_json(ctx.p("dedup.json"), {})
    checks = []

    def check(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        print(("PASS" if ok else "FAIL") + "  " + name + ((" — " + detail) if detail and not ok else ""))

    check("result.json exists and parses", res is not None)
    check("case_id present", bool(res and res.get("case_id")))
    check("status valid", bool(res and res.get("status") in VALID_STATUS))
    contract_fields = res.get("output_contract_fields", []) if res else []
    check("contract fields echoed", bool(res) and all(f in res for f in contract_fields))
    if judgments:
        dec_ok = all(j.get("decision") in VALID_DECISIONS for j in judgments)
    else:
        dec_ok = res is not None and res.get("status") == "insufficient_evidence"
    check("all records decided", bool(dec_ok))
    text_by_id = {r["record_id"]: _record_text(r) for r in records}
    ev_ok = True
    for j in judgments:
        if j.get("decision") in ("include", "exclude") and j.get("evidence_quote"):
            if not quote_matches(j["evidence_quote"], text_by_id.get(j.get("record_id"), "")):
                ev_ok = False
                break
    check("evidence quotes verified", ev_ok)
    counts = res.get("audit", {}).get("counts", {}) if res else {}
    check("count closure", bool(counts.get("closed", False)))
    pos = dedup.get("possible_duplicates", [])
    check("possible_duplicates not auto-merged", all(p.get("action", "").endswith("no_auto_merge") for p in pos))
    check("stage budgets sum to 900s", sum(STAGE_BUDGETS.values()) == TOTAL_BUDGET_S)
    report = {"checks": checks, "all_pass": all(c["ok"] for c in checks), "at": now_iso()}
    write_json(ctx.p("selfcheck.json"), report)
    if res is not None:
        res["selfcheck"] = report
        ctx.save_result(res)
    ctx.end_stage(cp, "selfcheck", input_count=0, output_count=len(checks))
    ctx.log(f"selfcheck: {'ALL PASS' if report['all_pass'] else 'HAS FAILURES'}")
    return 0


def cmd_timebox(ctx):
    cp = ctx.load_checkpoint()
    ctx._recompute_budget(cp)
    ctx.save_checkpoint(cp)
    print(f"elapsed={cp['elapsed_s']}s remaining={cp['remaining_s']}s / {TOTAL_BUDGET_S}s")
    print(f"model_requests={cp.get('model_requests', 0)}/{MODEL_REQUEST_BUDGET}")
    for s in STAGE_ORDER:
        st = cp["stages"].get(s, {})
        print(f"  {s:12s} budget={st.get('budget_s', 0):>3}s status={st.get('status', 'pending')}")
    return 0


def cmd_log_request(ctx):
    cp = ctx.load_checkpoint()
    cp["model_requests"] = cp.get("model_requests", 0) + 1
    cp.setdefault("notes", []).append({"at": now_iso(), "event": "model_request_logged"})
    ctx._recompute_budget(cp)
    ctx.save_checkpoint(cp)
    print(f"model_requests={cp['model_requests']}/{MODEL_REQUEST_BUDGET} remaining_s={cp['remaining_s']}")
    return 0


def cmd_checkpoint(ctx):
    cp = ctx.load_checkpoint()
    print(json.dumps(cp, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(prog="main.py",
                                description="Time-budgeted systematic literature screening pipeline.")
    p.add_argument("subcommand", choices=SUBCOMMANDS)
    p.add_argument("--input", default="/input", help="input directory")
    p.add_argument("--output", default="/app/output", help="output directory")
    p.add_argument("--work", default="/app/work", help="working directory")
    p.add_argument("--case", default=None, help="explicit case.json path")
    p.add_argument("--quiet", action="store_true", help="suppress progress prints")
    return p


def dispatch(args):
    ctx = Context(args)
    handlers = {
        "skeleton": cmd_skeleton, "protocol": cmd_protocol, "normalize": cmd_normalize,
        "screen": cmd_screen, "arbitrate": cmd_arbitrate, "finalize": cmd_finalize,
        "selfcheck": cmd_selfcheck, "timebox": cmd_timebox, "log-request": cmd_log_request,
        "checkpoint": cmd_checkpoint,
    }
    handler = handlers.get(args.subcommand)
    if handler is None:
        print("unknown subcommand: " + str(args.subcommand), file=sys.stderr)
        return 2
    try:
        return handler(ctx)
    except Exception as exc:  # never crash the harness; degrade to a valid skeleton
        ctx.record_error(args.subcommand, exc)
        ctx.write_minimal_result()
        print(f"[error] {args.subcommand}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 0


def cli_main(argv=None):
    args = build_parser().parse_args(argv)
    return dispatch(args)


if __name__ == "__main__":
    raise SystemExit(cli_main())
