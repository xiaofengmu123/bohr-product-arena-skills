# -*- coding: utf-8 -*-
"""Deterministic core for the literature-tracking Skill (standard library only).

Every function here is a pure function of its arguments: no network, no random
state, no hidden global mutable configuration. Re-running the same input gives
byte-for-byte identical dedup / normalize / classify results.
"""

import hashlib
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

MAX_FILE_BYTES = 2 * 1024 * 1024
DEFAULT_CONTRACT_KEYS = ["search_results", "evidence", "status_notes"]
META_FIELDS = ["status", "sources", "uncertainty", "change_log",
               "errors", "degradations", "pending_human", "runtime"]
STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
_CASE_ID_HINTS = ("case_id", "id", "challenge_id")
_CASE_ID_RE = re.compile(r"challenge_id[:\s]*`?([A-Za-z0-9_.-]+)`?")


# ---------------------------------------------------------------------------
# Input discovery (先发现，再假设)
# ---------------------------------------------------------------------------

def read_text(path, cap=MAX_FILE_BYTES):
    """Read UTF-8 text with BOM and fallback encodings; return (text, error)."""
    p = Path(path)
    try:
        if p.stat().st_size > cap:
            return None, "size cap exceeded: %s" % p
    except OSError as exc:
        return None, "stat failed %s: %s" % (p, exc)
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return p.read_text(encoding=enc), None
        except (UnicodeDecodeError, UnicodeError):
            continue
    try:
        return p.read_bytes().decode("utf-8", "replace"), None
    except Exception as exc:  # pragma: no cover - last resort
        return None, "read failed %s: %s" % (p, exc)


def load_json(path):
    text, err = read_text(path)
    if err:
        return None, [err]
    try:
        return json.loads(text), []
    except Exception as exc:
        return None, ["json parse failed %s: %s" % (path, exc)]


def discover_input(input_dir):
    """Return (case_path, task_path, material_paths, errors)."""
    errors = []
    root = Path(input_dir)
    case = task = None
    materials = []
    if root.exists():
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            name = p.name.lower()
            if name == "case.json" and case is None:
                case = p
            elif name == "task.md" and task is None:
                task = p
            else:
                materials.append(p)
    return case, task, materials, errors


def find_output_contract(obj):
    """Locate output_contract keys; returns list of field names or None."""
    if isinstance(obj, dict):
        for key in ("output_contract", "contract", "output_schema", "output"):
            value = obj.get(key)
            if isinstance(value, dict):
                return list(value.keys())
            if isinstance(value, list):
                return [str(x) for x in value if isinstance(x, str)]
            if isinstance(value, str):
                return [value]
        for key in ("task", "spec", "input"):
            found = find_output_contract(obj.get(key))
            if found:
                return found
    return None


def _case_id_from_obj(obj):
    if isinstance(obj, dict):
        for key in _CASE_ID_HINTS:
            if obj.get(key) not in (None, ""):
                return str(obj[key])
        for key in ("task", "spec", "input", "meta"):
            value = _case_id_from_obj(obj.get(key))
            if value:
                return value
    return None


def extract_case(input_dir):
    """Read case.json + task.md; return (case_id, contract_keys, data, errors)."""
    case_path, task_path, materials, errors = discover_input(input_dir)
    case_obj, task_text = None, None
    if case_path is not None:
        case_obj, errs = load_json(str(case_path))
        errors.extend(errs)
    if task_path is not None:
        task_text, err = read_text(str(task_path))
        if err:
            errors.append(err)

    case_id = _case_id_from_obj(case_obj)
    if not case_id and task_text:
        m = _CASE_ID_RE.search(task_text)
        if m:
            case_id = m.group(1)
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
    """Honest initial status from discovered inputs (never optimistic)."""
    has_case = data.get("case_obj") is not None
    has_task = data.get("task_text") is not None
    malformed = any(tok in e for tok in
                    ("json parse failed", "read failed", "stat failed")
                    for e in errors)
    if not has_case and not has_task:
        return "input_error" if malformed else "insufficient_evidence"
    return "input_error" if malformed else "partial"


def make_skeleton(case_id, contract_keys, status="partial"):
    """Build the minimal valid result object with all contract fields present."""
    result = {"case_id": case_id, "status": status}
    for key in contract_keys or []:
        if key not in result:
            result[key] = None
    list_fields = ("sources", "uncertainty", "change_log", "errors",
                   "degradations", "pending_human")
    for key in META_FIELDS:
        if key not in result:
            result[key] = [] if key in list_fields else None
    return result


# ---------------------------------------------------------------------------
# Normalization and dedup keys (DOI > arXiv > normalized title)
# ---------------------------------------------------------------------------

def _fold(text):
    out = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in out if not unicodedata.combining(c)).lower()


def normalize_title(title):
    if title is None:
        return None
    s = re.sub(r"[^\w]+", " ", _fold(str(title)))
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def normalize_doi(doi):
    if doi is None:
        return None
    s = str(doi).strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = re.sub(r"^doi:\s*", "", s)
    s = s.strip()
    return s or None


def normalize_arxiv(raw):
    if raw is None:
        return None
    s = str(raw).strip().lower()
    s = re.sub(r"^https?://(www\.)?arxiv\.org/(abs|pdf)/", "", s)
    s = re.sub(r"^arxiv:\s*", "", s)
    s = s.strip()
    s = re.sub(r"v\d+$", "", s)  # strip version suffix for identity
    return s or None


def canonical_id(record):
    """Stable identity: DOI > arXiv > normalized title."""
    rec = record if isinstance(record, dict) else {}
    doi = normalize_doi(rec.get("doi"))
    if doi:
        return "doi:" + doi
    arx = normalize_arxiv(rec.get("arxiv") or rec.get("arxiv_id"))
    if arx:
        return "arxiv:" + arx
    title = normalize_title(rec.get("title"))
    if title:
        return "title:" + title
    return None


VERSION_ORDER = {"unknown": -1, "preprint": 0, "accepted": 1,
                 "published": 2, "correction": 3, "retracted": 4}


def version_label(record):
    """Classify the version stage of one record, precedence retraction > correction."""
    rec = record if isinstance(record, dict) else {}
    status = str(rec.get("version") or rec.get("status") or rec.get("type") or "").lower()
    if rec.get("retracted") or "retract" in status:
        return "retracted"
    if any(t in status for t in ("corrigendum", "erratum", "correction", "corrigenda")):
        return "correction"
    if any(t in status for t in ("preprint", "pre-print", "pre print")):
        return "preprint"
    if any(t in status for t in ("publish", "version of record", "journal")):
        return "published"
    if "accept" in status:
        return "accepted"
    if rec.get("journal") or rec.get("volume"):
        return "published"
    if rec.get("arxiv") or rec.get("arxiv_id"):
        return "preprint"
    return "unknown"


def version_mark(record):
    label = record.get("version_label", "unknown")
    v = str(record.get("version_number") or record.get("version") or "").strip()
    d = str(record.get("date") or record.get("indexed") or "").strip()
    return "%s|%s|%s" % (label, v, d)


def dedup(records):
    """Merge duplicate records into one representative per canonical work.

    Merge rules (union-find, deterministic):
      1. same normalized DOI          -> same work (always);
      2. same normalized arXiv base   -> same work (always);
      3. same normalized title        -> bridge, EXCEPT when both records carry
         a DOI and the two DOIs differ (two independently identified works with
         a coincidentally identical title).

    The representative is the highest version stage; all variants are retained
    so the preprint→published→correction chain stays reconstructible.
    """
    recs = []
    for rec in records or []:
        if isinstance(rec, dict):
            r = dict(rec)
            r["version_label"] = version_label(r)
            recs.append(r)
    n = len(recs)
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

    doi_map, arx_map, title_map = {}, {}, {}
    for i, r in enumerate(recs):
        d = normalize_doi(r.get("doi"))
        a = normalize_arxiv(r.get("arxiv") or r.get("arxiv_id"))
        t = normalize_title(r.get("title"))
        if d:
            if d in doi_map:
                union(i, doi_map[d])
            else:
                doi_map[d] = i
        if a:
            if a in arx_map:
                union(i, arx_map[a])
            else:
                arx_map[a] = i
        if t:
            if t in title_map:
                j = title_map[t]
                dj = normalize_doi(recs[j].get("doi"))
                if not (d and dj and d != dj):
                    union(i, j)
            else:
                title_map[t] = i

    groups, order = {}, []
    for i, r in enumerate(recs):
        root = find(i)
        if root not in groups:
            groups[root] = []
            order.append(root)
        groups[root].append(r)

    merged = []
    for root in order:
        variants = sorted(groups[root],
                          key=lambda r: VERSION_ORDER.get(r["version_label"], -1))
        cid = None
        for r in variants:
            d = normalize_doi(r.get("doi"))
            if d:
                cid = "doi:" + d
                break
        if cid is None:
            for r in variants:
                a = normalize_arxiv(r.get("arxiv") or r.get("arxiv_id"))
                if a:
                    cid = "arxiv:" + a
                    break
        if cid is None:
            for r in variants:
                t = normalize_title(r.get("title"))
                if t:
                    cid = "title:" + t
                    break
        if cid is None:
            blob = json.dumps(variants, sort_keys=True, ensure_ascii=False)
            cid = "raw:" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]

        rep = dict(variants[-1])
        rep["canonical_id"] = cid
        rep["variants"] = [dict(r) for r in variants]
        rep["version_marks"] = sorted({version_mark(r) for r in variants})
        merged.append(rep)
    return merged


# ---------------------------------------------------------------------------
# Incremental classification vs previous snapshot
# ---------------------------------------------------------------------------

def _snapshot_seen(snapshot):
    """Return (seen_map, list_form) where seen_map maps canonical id -> marks."""
    if isinstance(snapshot, dict):
        seen = snapshot.get("seen")
        if isinstance(seen, dict):
            return seen, False
        if isinstance(seen, list):
            return {c: [] for c in seen}, True
        return {}, False
    if isinstance(snapshot, list):
        return {c: [] for c in snapshot}, True
    return {}, False


def classify(records, snapshot):
    """Split dedup'd records into new / updated / retracted / corrected / unchanged."""
    seen, list_form = _snapshot_seen(snapshot)
    out = {"new_items": [], "updates": [], "retractions": [],
           "corrections": [], "unchanged": []}
    for rec in records or []:
        cid = rec.get("canonical_id")
        label = rec.get("version_label", "unknown")
        in_seen = cid in seen
        marks = set(rec.get("version_marks", []))
        prev = set(seen.get(cid, []))
        if label == "retracted":
            out["retractions"].append(rec)
        elif label == "correction":
            out["corrections"].append(rec)
        elif not in_seen:
            out["new_items"].append(rec)
        elif (marks - prev) and not list_form:
            out["updates"].append(rec)
        else:
            out["unchanged"].append(rec)
    return out


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path, obj):
    """Atomic-ish JSON write with a minimal-legal fallback; returns error or None."""
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(str(tmp), str(p))
        return None
    except Exception as exc:
        try:
            minimal = {
                "case_id": (obj or {}).get("case_id", "unknown-case"),
                "status": (obj or {}).get("status", "input_error"),
                "errors": list((obj or {}).get("errors", [])) + ["write failed: %s" % exc],
            }
            p.write_text(json.dumps(minimal, ensure_ascii=False, indent=2),
                         encoding="utf-8")
        except Exception:
            pass
        return "write failed: %s" % exc
