# -*- coding: utf-8 -*-
"""Deterministic, standard-library core shared by main.py and selftest.py.

This module deliberately contains **no scientific judgment**: it only does
input discovery, tolerant text/JSON loading, normalization, dedup/version-chain
clustering, plan building and safe result assembly. The Agent supplies the
substantive conclusions in findings.json; these scripts never invent
literature, metadata or sources.

Design highlight of this variant — *intermediate representation and audit*:
``plan.json`` is the structured, versioned bridge between raw materials and the
final result. ``finalize`` always consumes plan.json + findings.json +
dedup.json, so every conclusion in result.json can be replayed through the same
intermediate artifact. Re-running any subcommand is idempotent with respect to
its mechanical outputs.
"""

import json
import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "1.0.0"
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB per input file
DEFAULT_CONTRACT_KEYS = ["search_results", "evidence", "status_notes"]

# Meta fields always present in result.json, beyond case_id and the contract.
META_FIELDS = [
    "status", "sources", "uncertainty", "change_log",
    "errors", "degradations", "pending_human", "runtime",
    "schema_version", "plan_ref",
]

_CASE_ID_HINTS = ("case_id", "id", "challenge_id", "armiId", "armi_id")
_CASE_ID_RE = re.compile(
    r"challenge_id[:\s]*`([^`]+)`|challenge_id[:\s]*([\w.\-]+)", re.IGNORECASE)
_CONTRACT_KEYS = ("output_contract", "contract", "output_schema", "output",
                  "outputs", "result_schema", "output_fields")

# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def utcnow_iso():
    """Current UTC time as ISO-8601 with 'Z'."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def parse_datetime(value):
    """Best-effort normalization to UTC ISO string, or None.

    Naive datetimes are treated as UTC and marked with a trailing note by the
    caller when a source-level timezone hint is available. This function only
    normalizes; it never guesses which timezone a naive value belonged to.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(value)))
        except (ValueError, OSError, OverflowError):
            return None
    s = unicodedata.normalize("NFKC", str(value)).strip()
    if s.lower() in ("", "null", "none", "n/a", "na", "not_reported", "unknown"):
        return None
    s = s.replace("Z", "+00:00").replace(" UTC", "+00:00")
    aware_fmts = ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z")
    for fmt in aware_fmts:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    naive_fmts = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                  "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M",
                  "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d")
    for fmt in naive_fmts:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Tolerant I/O
# ---------------------------------------------------------------------------


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """Return (text, errors). Never raises on encoding/stat failures."""
    errors = []
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError as exc:
        return None, ["stat failed %s: %s" % (path, exc)]
    if size > max_bytes:
        return None, ["file too large (%d > %d): %s" % (size, max_bytes, path)]
    try:
        raw = p.read_bytes()
    except OSError as exc:
        return None, ["read failed %s: %s" % (path, exc)]
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), errors
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace"), errors + [
        "decoded %s with latin-1 fallback (non-UTF-8)" % path]


def load_json(path, max_bytes=MAX_FILE_BYTES):
    """Return (obj_or_None, errors). Tolerant to bad JSON."""
    text, errors = read_text(path, max_bytes)
    if text is None:
        return None, errors
    try:
        return json.loads(text), errors
    except (json.JSONDecodeError, ValueError) as exc:
        return None, errors + ["json parse failed %s: %s" % (path, exc)]


def dump_json(obj, path):
    """Deterministic, atomic JSON write (sorted keys, no ASCII escaping)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(p))
    return str(p)


def dump_text(text, path):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(p))
    return str(p)


# ---------------------------------------------------------------------------
# Input discovery and contract extraction
# ---------------------------------------------------------------------------


def discover_input(input_dir):
    """Return (case_path, task_path, material_paths, errors)."""
    root = Path(input_dir)
    if not root.exists():
        return None, None, [], ["input dir missing: %s" % root]
    case = task = None
    materials = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        name = p.name.lower()
        if name == "case.json":
            case = p
        elif name == "task.md":
            task = p
        else:
            materials.append(p)
    errors = []
    if case is None and task is None:
        errors.append("no case.json or task.md found under %s" % root)
    return case, task, materials, errors


def _case_id_from_obj(obj, depth=0):
    if depth > 6 or not isinstance(obj, dict):
        return None
    for k in _CASE_ID_HINTS:
        v = obj.get(k)
        if isinstance(v, (str, int)) and v not in ("", None):
            return str(v)
    for k in ("task", "spec", "input", "meta", "metadata"):
        found = _case_id_from_obj(obj.get(k), depth + 1)
        if found:
            return found
    return None


def _field_names(v):
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        return [str(x) for x in v if isinstance(x, str)]
    return None


def find_output_contract(obj, depth=0):
    """Locate required output field names; return list or None."""
    if depth > 8 or not isinstance(obj, dict):
        return None
    for key in _CONTRACT_KEYS:
        v = obj.get(key)
        if v is None:
            continue
        if isinstance(v, dict):
            req = v.get("required") or v.get("fields")
            if isinstance(req, list):
                names = [str(x) for x in req if isinstance(x, str)]
                if names:
                    return names
            props = v.get("properties")
            if isinstance(props, dict):
                return list(props.keys())
            keys = [k for k in v.keys() if k not in ("type", "title",
                                                     "description", "$schema")]
            if keys:
                return keys
            continue
        names = _field_names(v)
        if names:
            return names
    for k in ("task", "spec", "input", "meta", "metadata"):
        found = find_output_contract(obj.get(k), depth + 1)
        if found:
            return found
    return None


def extract_case(input_dir):
    """Return (case_id, contract_keys, data, errors)."""
    case_path, task_path, materials, errors = discover_input(input_dir)
    case_obj, task_text = None, None
    if case_path is not None:
        case_obj, errs = load_json(str(case_path))
        errors.extend(errs)
    if task_path is not None:
        task_text, errs = read_text(str(task_path))
        errors.extend(errs)

    case_id = _case_id_from_obj(case_obj)
    if not case_id and task_text:
        m = _CASE_ID_RE.search(task_text)
        if m:
            case_id = m.group(1) or m.group(2)
    if not case_id:
        case_id = "unknown-case"

    contract = find_output_contract(case_obj)
    if contract is None:
        contract = list(DEFAULT_CONTRACT_KEYS)

    data = {
        "case_obj": case_obj,
        "task_text": task_text,
        "case_path": str(case_path) if case_path else None,
        "task_path": str(task_path) if task_path else None,
        "materials": [str(m) for m in materials],
    }
    return case_id, contract, data, errors


def initial_status(data, errors):
    has_case = data.get("case_obj") is not None
    has_task = data.get("task_text") is not None
    malformed = any(("json parse failed" in e) or ("read failed" in e)
                    for e in errors)
    if not has_case and not has_task:
        return "input_error" if malformed else "insufficient_evidence"
    return "input_error" if malformed else "partial"


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]|_", re.UNICODE)
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>?#]+", re.IGNORECASE)
_ARXIV_NEW_RE = re.compile(r"(?:arxiv\s*:\s*)?(\d{4}\.\d{4,5})(?:v(\d+))?",
                           re.IGNORECASE)
_ARXIV_OLD_RE = re.compile(r"([a-z\-]+\.(?:[A-Z]{2}|\w+)/\d{7})", re.IGNORECASE)


def normalize_title(title):
    if title is None:
        return None
    t = unicodedata.normalize("NFKC", str(title)).casefold()
    t = _PUNCT_RE.sub(" ", t)
    t = _WS_RE.sub(" ", t).strip()
    return t or None


def normalize_doi(doi):
    if doi is None:
        return None
    d = unicodedata.normalize("NFKC", str(doi)).strip()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d, flags=re.IGNORECASE)
    d = re.sub(r"^doi\s*:\s*", "", d, flags=re.IGNORECASE)
    d = d.strip().rstrip(".,;:?!'\")]}> ")
    m = _DOI_RE.search(d)
    if m:
        out = m.group(0).lower().rstrip(".,;:?!'\")]}> ")
        return out or None
    return d.lower() or None


def arxiv_parts(value):
    """Return (full_id, base_id) for an arXiv identifier, or (None, None)."""
    if value is None:
        return None, None
    v = str(value).strip()
    m = _ARXIV_NEW_RE.search(v)
    if m:
        base = "arxiv:" + m.group(1).lower()
        full = base + ("v" + m.group(2) if m.group(2) else "")
        return full, base
    m2 = _ARXIV_OLD_RE.search(v)
    if m2:
        base = "arxiv:" + m2.group(1).lower()
        return base, base
    return None, None


def _first(record, keys):
    for k in keys:
        v = record.get(k)
        if v not in (None, "", []):
            return v
    return None


def record_identifiers(record):
    """Extract normalized identifiers from one record dict."""
    if not isinstance(record, dict):
        record = {}
    doi = normalize_doi(_first(record, ("doi", "DOI")))
    if doi is None:
        doi = normalize_doi(_first(record, ("url", "doi_url", "link")))
    arx_full, arx_base = arxiv_parts(_first(
        record, ("arxiv", "arxiv_id", "arXiv", "arxivId", "eprint")))
    title = normalize_title(_first(record, ("title", "name", "item_title")))
    return {"doi": doi, "arxiv_full": arx_full, "arxiv_base": arx_base,
            "title": title}


def canonical_key(record):
    """Stable canonical key: DOI > arXiv base > normalized title."""
    idn = record_identifiers(record)
    if idn["doi"]:
        return "doi:" + idn["doi"]
    if idn["arxiv_base"]:
        return idn["arxiv_base"]
    if idn["title"]:
        return "title:" + idn["title"]
    return None


def record_date(record):
    if not isinstance(record, dict):
        return None
    for k in ("published_date", "publication_date", "date", "created",
              "indexed_date", "published", "pubdate", "epub_date",
              "date_published", "issued"):
        d = parse_datetime(record.get(k))
        if d:
            return d
    return None


# ---------------------------------------------------------------------------
# Dedup and version chains (union-find over identifiers)
# ---------------------------------------------------------------------------

_RETRACT_MARKERS = ("retract", "withdrawn", "withdrawal", "撤稿", "撤回", "收回")
_CORRECTION_MARKERS = ("correction", "erratum", "corrigendum", "addendum",
                       "勘误", "更正", "修订", "revised", "updated",
                       "author correction")


def _marker_hit(record, markers):
    blob = " ".join(str(record.get(k, "")) for k in
                    ("title", "type", "status", "note", "notes",
                     "relation", "update_type", "abstract"))
    low = blob.casefold()
    return any(m in low for m in markers)


def classify_record(record):
    if _marker_hit(record, _RETRACT_MARKERS):
        return "retracted"
    if _marker_hit(record, _CORRECTION_MARKERS):
        return "correction"
    idn = record_identifiers(record)
    if idn["doi"]:
        return "published"
    if idn["arxiv_base"]:
        return "preprint"
    return "unknown"


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


def dedup_records(records):
    """Cluster records into canonical groups with version chains.

    Returns {"groups": [...], "summary": {...}}; deterministic and stable.
    """
    records = [r for r in records if isinstance(r, dict)]
    n = len(records)
    if n == 0:
        return {"groups": [], "summary": {
            "input_count": 0, "group_count": 0, "duplicates_removed": 0}}
    idents = [record_identifiers(r) for r in records]
    uf = _UnionFind(n)
    doi_map, arx_map, title_map = {}, {}, {}
    for i, idn in enumerate(idents):
        if idn["doi"]:
            if idn["doi"] in doi_map:
                uf.union(i, doi_map[idn["doi"]])
            doi_map[idn["doi"]] = i
        if idn["arxiv_base"]:
            if idn["arxiv_base"] in arx_map:
                uf.union(i, arx_map[idn["arxiv_base"]])
            arx_map[idn["arxiv_base"]] = i
    for i, idn in enumerate(idents):
        if idn["title"]:
            if idn["title"] in title_map:
                uf.union(i, title_map[idn["title"]])
            title_map[idn["title"]] = i

    buckets = {}
    for i in range(n):
        buckets.setdefault(uf.find(i), []).append(i)

    groups = []
    for root in sorted(buckets, key=lambda r: min(buckets[r])):
        idxs = sorted(buckets[root])
        recs = [records[i] for i in idxs]
        merged_idn = {}
        for i in idxs:
            for k, v in idents[i].items():
                if v:
                    merged_idn.setdefault(k, v)
        key = (("doi:" + merged_idn["doi"]) if merged_idn.get("doi")
               else (merged_idn.get("arxiv_base")
                     or ("title:" + merged_idn["title"]
                         if merged_idn.get("title") else None)))
        tagged = []
        for i in idxs:
            r = dict(records[i])
            r["_index"] = i
            r["_canonical_key"] = key
            r["_group_id"] = "g%03d" % root
            r["_record_class"] = classify_record(records[i])
            r["_date_norm"] = record_date(records[i])
            tagged.append(r)
        tagged.sort(key=lambda r: (
            r["_date_norm"] is None, r["_date_norm"] or "",
            _class_rank(r["_record_class"])))
        classes = {r["_record_class"] for r in tagged}
        group_class = ("retracted" if "retracted" in classes
                       else "correction" if "correction" in classes
                       else "published" if "published" in classes
                       else "preprint" if "preprint" in classes
                       else "unknown")
        groups.append({
            "canonical_key": key,
            "group_id": "g%03d" % root,
            "identifiers": merged_idn,
            "class": group_class,
            "member_count": len(tagged),
            "records": tagged,
            "version_chain": [
                {"index": r.get("_index"), "class": r["_record_class"],
                 "date": r["_date_norm"],
                 "canonical_key": r["_canonical_key"]}
                for r in tagged
            ],
        })
    return {"groups": groups, "summary": {
        "input_count": n, "group_count": len(groups),
        "duplicates_removed": n - len(groups)}}


def _class_rank(cls):
    order = {"retracted": 0, "correction": 1, "published": 2,
             "preprint": 3, "unknown": 4}
    return order.get(cls, 4)


# ---------------------------------------------------------------------------
# plan.json (structured intermediate representation)
# ---------------------------------------------------------------------------


def guess_kind(name):
    n = name.lower()
    if n.endswith(".pdf"):
        return "pdf"
    if n.endswith(".json"):
        return "json"
    if n.endswith((".md", ".txt")):
        return "text"
    if n.endswith(".csv"):
        return "csv"
    if n.endswith(".xml"):
        return "xml"
    if n.endswith((".bib", ".ris")):
        return "bibliography"
    return "other"


def build_plan(data, case_id, contract_keys):
    plan = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "contract_keys": contract_keys,
        "created_at": utcnow_iso(),
        "updated_at": utcnow_iso(),
        "subscription": {"query": None, "sources": [], "frequency": None,
                         "status": "pending_agent"},
        "window": {"timezone": "UTC", "start": None, "end": None,
                   "policy": "closed_inclusive", "status": "pending_agent"},
        "snapshot": {"last_window_end": None, "canonical_ids": [],
                     "counts": {}, "status": "pending_agent"},
        "dedup": {"key_priority": ["doi", "arxiv", "normalized_title"],
                  "title_normalization": "NFKC_casefold_punct_ws",
                  "union_find": True, "fuzzy_title_match": False},
        "evidence_spec": {"required_fields": ["locator", "quote"],
                          "conflict_policy": "source_conflict"},
        "reminders": {"suppress_duplicates": True, "suppress_reposts": True,
                      "suppress_out_of_window": True,
                      "classes": ["new", "update", "retraction", "correction"]},
        "materials": [],
        "gaps": [],
        "notes": [],
    }
    for i, m in enumerate(sorted(data.get("materials", []))):
        name = os.path.basename(m)
        try:
            size = os.path.getsize(m)
        except OSError:
            size = None
        plan["materials"].append({
            "source_id": "material_%03d" % i,
            "path": m,
            "name": name,
            "kind": guess_kind(name),
            "size_bytes": size,
            "needs_pdf_text_extraction": name.lower().endswith(".pdf"),
        })
    return plan


def merge_plan(existing, fresh):
    """Keep agent-filled semantic fields, refresh mechanical parts.

    Semantic fields are preserved whenever their status is no longer
    'pending_agent'; mechanical fields (materials, created/updated) refresh.
    """
    if not isinstance(existing, dict):
        return fresh
    for key in ("subscription", "window", "snapshot"):
        old = existing.get(key)
        if isinstance(old, dict) and old.get("status") not in (
                None, "pending_agent"):
            fresh[key] = old
    for key in ("notes", "gaps"):
        if existing.get(key):
            fresh[key] = existing.get(key)
    fresh["created_at"] = existing.get("created_at") or fresh["created_at"]
    fresh["updated_at"] = utcnow_iso()
    return fresh


# ---------------------------------------------------------------------------
# Result assembly
# ---------------------------------------------------------------------------


def _typed_default(key):
    if key in ("status_notes", "tracking", "dedup", "ranking", "sources",
               "uncertainty", "change_log", "errors", "degradations",
               "pending_human", "search_results", "evidence"):
        return [] if key in ("sources", "uncertainty", "change_log", "errors",
                             "degradations", "pending_human",
                             "search_results", "evidence", "ranking") else {}
    return None


def make_skeleton(case_id, contract_keys, status="partial"):
    result = {"case_id": case_id}
    for k in contract_keys:
        result.setdefault(k, _typed_default(k))
    result.setdefault("status", status)
    result.setdefault("sources", [])
    result.setdefault("uncertainty", [])
    result.setdefault("change_log", [])
    result.setdefault("errors", [])
    result.setdefault("degradations", [])
    result.setdefault("pending_human", [])
    result.setdefault("runtime", {})
    result.setdefault("schema_version", SCHEMA_VERSION)
    return result


def _deep_get(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for k in ("task", "spec", "input", "result", "data"):
            if isinstance(obj.get(k), dict):
                v = _deep_get(obj[k], key)
                if v is not None:
                    return v
    return None


def assemble_result(plan, findings, dedup, data, case_id, contract_keys,
                    errors, runtime_meta):
    findings = findings if isinstance(findings, dict) else {}
    result = make_skeleton(case_id, contract_keys)
    for k in contract_keys:
        v = _deep_get(findings, k)
        if v is None:
            v = _typed_default(k)
        result[k] = v

    for k in ("tracking", "dedup", "ranking", "sources", "uncertainty",
              "pending_human", "errors", "degradations"):
        v = findings.get(k)
        if v is None:
            v = _typed_default(k)
        result[k] = v

    # tracking: honor Agent's findings.tracking, else mechanical from dedup.
    if not findings.get("tracking") and isinstance(dedup, dict):
        snapshot_ids = set()
        snap = (plan or {}).get("snapshot") or {}
        for cid in snap.get("canonical_ids", []) or []:
            snapshot_ids.add(str(cid))
        groups = dedup.get("groups", []) or []
        result["tracking"] = {
            "new_items": [g["canonical_key"] for g in groups
                          if g["canonical_key"] and
                          g["canonical_key"] not in snapshot_ids],
            "updates": [],
            "retractions": [g["canonical_key"] for g in groups
                            if g["class"] == "retracted"],
            "corrections": [g["canonical_key"] for g in groups
                            if g["class"] == "correction"],
            "reminders": [],
            "_mechanical_only": True,
        }
    if not findings.get("dedup") and isinstance(dedup, dict):
        result["dedup"] = dedup.get("summary", {}) or {}

    # status: Agent finding wins if valid; otherwise fall back to input state.
    agent_status = findings.get("status")
    if agent_status in ("ok", "partial", "insufficient_evidence",
                        "input_error"):
        result["status"] = agent_status
    else:
        base = initial_status(data, errors)
        result["status"] = base if base in (
            "insufficient_evidence", "input_error") else "partial"

    if errors:
        result["errors"] = list(errors) + list(result.get("errors", []))

    result["plan_ref"] = plan.get("_path") if isinstance(plan, dict) else None
    result["runtime"] = dict(runtime_meta or {})
    result["change_log"] = list(findings.get("change_log", []) or [])
    return result


def final_status(result):
    if result.get("errors"):
        if any("json parse failed" in e or "read failed" in e
               for e in result["errors"]):
            return "input_error"
    return result.get("status", "partial")


# ---------------------------------------------------------------------------
# report.md rendering
# ---------------------------------------------------------------------------


def render_report(result, plan):
    lines = []
    lines.append("# 文献发现与追踪报告")
    lines.append("")
    lines.append("- case_id: `%s`" % result.get("case_id"))
    lines.append("- status: `%s`" % result.get("status"))
    lines.append("- schema_version: `%s`" % result.get("schema_version"))
    lines.append("")
    sub = (plan or {}).get("subscription") or {}
    win = (plan or {}).get("window") or {}
    snap = (plan or {}).get("snapshot") or {}
    lines.append("## 订阅与窗口")
    lines.append("")
    lines.append("- 查询式: `%s`" % (sub.get("query") or "未填"))
    lines.append("- 来源: %s" % ", ".join(sub.get("sources") or []) or "未填")
    lines.append("- 频率: %s" % (sub.get("frequency") or "未填"))
    lines.append("- 时间窗: [%s, %s] (tz=%s, policy=%s)" % (
        win.get("start") or "?", win.get("end") or "?",
        win.get("timezone") or "UTC", win.get("policy") or "closed_inclusive"))
    lines.append("- 快照水位: %s (已见 canonical_id 数=%d)" % (
        snap.get("last_window_end") or "?",
        len(snap.get("canonical_ids") or [])))
    lines.append("")
    counts = {}
    for key in ("search_results", "evidence", "tracking", "dedup"):
        v = result.get(key)
        if isinstance(v, list):
            counts[key] = len(v)
        elif isinstance(v, dict):
            counts[key] = sum(len(x) for x in v.values()
                              if isinstance(x, list))
        else:
            counts[key] = 0
    lines.append("## 计数")
    lines.append("")
    for k in ("search_results", "evidence", "tracking", "dedup"):
        lines.append("- %s: %d" % (k, counts[k]))
    lines.append("")
    for section in ("uncertainty", "pending_human", "errors", "degradations"):
        v = result.get(section) or []
        lines.append("## %s (%d)" % (section, len(v)))
        lines.append("")
        if v:
            for item in v:
                lines.append("- %s" % (item if isinstance(item, str)
                                       else json.dumps(item, ensure_ascii=False)))
        else:
            lines.append("- 无")
        lines.append("")
    lines.append("## 变更日志")
    lines.append("")
    for ev in result.get("change_log") or []:
        if isinstance(ev, dict):
            lines.append("- [%s] %s: %s" % (
                ev.get("time", "?"), ev.get("event", "?"), ev.get("detail", "")))
        else:
            lines.append("- %s" % ev)
    lines.append("")
    return "\n".join(lines)
