# -*- coding: utf-8 -*-
"""Deterministic core for the literature-tracking skill.

This module contains only reproducible, side-effect-light transformations:
input discovery, output_contract extraction, skeleton assembly, canonical-key
normalization, dedup/version-chain grouping, snapshot diffing, reminder
suppression and result/report finalization.

Substantive reading and scientific judgment are performed by the calling
Agent. Its conclusions are supplied as a ``findings.json`` document and are
merged deterministically here. Nothing in this file invents literature
content, DOI, metadata, or sources: missing data becomes ``null``,
``not_reported``, or an honest ``insufficient_evidence`` status.

Standard library only. All paths are resolved relative to the call site.
"""

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone

MAX_FILE_BYTES = 2 * 1024 * 1024  # per-material read ceiling

# Meta fields always appended to a result beyond case_id + contract fields.
META_FIELDS = [
    "status",
    "sources",
    "uncertainty",
    "change_log",
    "errors",
    "degradations",
    "pending_human",
    "runtime",
]

# Default domain fields used when no output_contract is present. These cover
# the task's three required output rows (检索结果 / 证据记录 / 状态说明) plus
# the tracking-specific state the closed loop requires.
DEFAULT_CONTRACT_KEYS = ["search_results", "ranking", "evidence", "status_notes",
                         "tracking", "dedup"]

_CASE_ID_HINTS = ("case_id", "id", "challenge_id", "task_id")
_CASE_ID_RE = re.compile(
    r"challenge_id[:\s]*`([^`]+)`|challenge_id[:\s]*([A-Za-z0-9_.\-]+)"
)
_CONTRACT_KEY_HINTS = (
    "output_contract", "contract", "output_schema", "output", "expected_output",
    "result_schema", "schema",
)
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>\[\]()]+", re.IGNORECASE)
_ARXIV_RE = re.compile(
    r"(?:arxiv\s*:\s*)?(\d{4}\.\d{4,5})(?:[vV]\d+)?", re.IGNORECASE
)
_ARXIV_LEGACY_RE = re.compile(
    r"(?:arxiv\s*:\s*)?([a-zA-Z\-]+\.[A-Za-z\-]+/\d{7})(?:[vV]\d+)?", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def now_utc_iso():
    """UTC ISO-8601 timestamp with second precision."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_utc_iso(value):
    """Best-effort normalization of a date/datetime string to UTC ISO-8601.

    Returns the normalized string when a date can be parsed, otherwise the
    stripped original string, otherwise ``None``. Never raises.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # ISO-8601 with explicit offset or 'Z'
    m = re.match(
        r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?"
        r"(\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?$", s
    )
    if m:
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            h, mi = int(m.group(4)), int(m.group(5))
            sec = int(m.group(6) or 0)
            dt = datetime(y, mo, d, h, mi, sec, tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass
    # Plain date
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if m:
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            dt = datetime(y, mo, d, tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass
    return s


# ---------------------------------------------------------------------------
# Robust text / JSON I/O
# ---------------------------------------------------------------------------

def read_text(path):
    """Read UTF-8 text tolerantly: utf-8-sig first, then latin-1 fallback.

    Raises ``UnicodeDecodeError`` only when both fail; callers decide how to
    degrade. Returns ``(text, encoding, errors)``.
    """
    raw = open(path, "rb").read()
    if len(raw) > MAX_FILE_BYTES:
        raise OverflowError("file exceeds %d bytes: %s" % (MAX_FILE_BYTES, path))
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), enc, []
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace"), "utf-8(replace)", ["decode replaced"]


def load_json(path):
    """Load JSON, returning ``(obj, errors)``. Never raises on bad JSON."""
    try:
        text, _enc, _errs = read_text(path)
    except Exception as exc:
        return None, ["read failed: %s: %s" % (path, exc)]
    try:
        return json.loads(text), []
    except Exception as exc:
        return None, ["json parse failed: %s: %s" % (path, exc)]


# ---------------------------------------------------------------------------
# Input discovery
# ---------------------------------------------------------------------------

def discover_input(input_dir):
    """Return ``(case_path, task_path, material_paths, errors)``.

    Discovers files on disk rather than assuming fixed names. Materials are
    every file that is not ``case.json`` / ``task.md`` (matched case-insensitively).
    """
    errors = []
    root = input_dir
    import os
    if not os.path.isdir(root):
        return None, None, [], ["input dir missing: %s" % root]
    case = None
    task = None
    materials = []
    for base, _dirs, files in os.walk(root):
        for fn in files:
            p = os.path.join(base, fn)
            low = fn.lower()
            if low == "case.json":
                case = p
            elif low == "task.md":
                task = p
            else:
                materials.append(p)
    materials.sort()
    if case is None and task is None:
        errors.append("no case.json or task.md found under %s" % root)
    return case, task, materials, errors


def _case_id_from_obj(obj):
    if isinstance(obj, dict):
        for k in _CASE_ID_HINTS:
            v = obj.get(k)
            if isinstance(v, (str, int)) and str(v).strip() not in ("", "None"):
                return str(v).strip()
        for k in ("task", "spec", "input", "meta", "metadata"):
            v = _case_id_from_obj(obj.get(k))
            if v:
                return v
    return None


def find_output_contract(obj):
    """Recursively locate an output_contract definition.

    Returns a list of required field names (deduplicated, order-preserving),
    or ``None`` when no contract is found. Accepts dict, list-of-strings,
    list-of-field-defs, and nested ``fields``/``required`` forms.
    """
    if isinstance(obj, dict):
        for k in _CONTRACT_KEY_HINTS:
            v = obj.get(k)
            found = _contract_keys_from_value(v)
            if found:
                return found
        for k in ("task", "spec", "input", "meta", "metadata"):
            found = find_output_contract(obj.get(k))
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = find_output_contract(item)
            if found:
                return found
    return None


def _contract_keys_from_value(v):
    if v is None:
        return None
    keys = []
    if isinstance(v, dict):
        # Direct field-name mapping, or {"fields": [...], "required": [...]}
        if "fields" in v:
            keys.extend(_keys_from_list(v.get("fields")))
        if "required" in v:
            keys.extend(_keys_from_list(v.get("required")))
        if not keys:
            keys = [str(k) for k in v.keys() if k not in ("fields", "required")]
    elif isinstance(v, list):
        keys = _keys_from_list(v)
    elif isinstance(v, str):
        keys = [v]
    if not keys:
        return None
    # Deduplicate, preserving order, dropping empty values.
    seen = set()
    out = []
    for k in keys:
        k = str(k).strip()
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out or None


def _keys_from_list(lst):
    keys = []
    if not isinstance(lst, list):
        return keys
    for item in lst:
        if isinstance(item, str):
            keys.append(item)
        elif isinstance(item, dict):
            for k in ("name", "field", "key", "id"):
                v = item.get(k)
                if isinstance(v, str) and v.strip():
                    keys.append(v)
                    break
    return keys


def extract_case(input_dir):
    """Read case.json + task.md; return ``(case_id, contract_keys, data, errors)``."""
    case_path, task_path, materials, errors = discover_input(input_dir)
    case_obj = None
    task_text = None
    if case_path is not None:
        case_obj, errs = load_json(case_path)
        errors.extend(errs)
    if task_path is not None:
        try:
            task_text, _enc, _errs = read_text(task_path)
        except Exception as exc:
            errors.append("task.md read failed: %s" % exc)

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
        "case_path": case_path,
        "task_path": task_path,
        "materials": materials,
    }
    return case_id, contract, data, errors


def _initial_status(data, errors):
    has_case = data.get("case_obj") is not None
    has_task = data.get("task_text") is not None
    malformed = any("parse failed" in e or "read failed" in e for e in errors)
    if not has_case and not has_task:
        return "input_error" if malformed else "insufficient_evidence"
    return "input_error" if malformed else "partial"


# ---------------------------------------------------------------------------
# Skeleton / result assembly
# ---------------------------------------------------------------------------

def make_skeleton(case_id, contract_keys, status="partial", errors=None):
    """Build an honest skeleton that backfills every contract field.

    Contract fields are filled with a type-appropriate empty value (``[]`` for
    clearly list-shaped names, else ``null``); unknown contract fields are
    preserved, never renamed or dropped.
    """
    result = {"case_id": case_id}
    for k in contract_keys:
        if k == "case_id":
            continue
        result.setdefault(k, _empty_for_field(k))
    result["status"] = status
    result["sources"] = []
    result["uncertainty"] = []
    result["change_log"] = []
    result["errors"] = list(errors or [])
    result["degradations"] = []
    result["pending_human"] = []
    result["runtime"] = {
        "started_at": now_utc_iso(),
        "budget_seconds": 900,
        "budget_model_requests": 64,
        "model_requests_used": 0,
        "status": status,
    }
    append_change_log(result, "skeleton_written", {"status": status,
                                                   "contract_fields": contract_keys})
    return result


def _empty_for_field(name):
    name = str(name).lower()
    plural_hints = ("results", "items", "records", "entries", "list", "sources",
                    "evidence", "notes", "errors", "reminders", "updates",
                    "retractions", "conflicts", "log", "candidates", "papers",
                    "literature", "publications", "documents", "articles",
                    "references", "citations", "hits", "matches", "ranking",
                    "rankings", "rank", "fields", "changes", "new_items")
    if name in ("status_notes", "tracking", "dedup", "snapshot", "subscription",
                "window", "meta", "metadata", "runtime", "info"):
        return {}
    if name.endswith("s") or name in plural_hints or "list" in name:
        return []
    return None


def append_change_log(result, event, detail=None):
    entry = {"ts": now_utc_iso(), "event": event}
    if detail is not None:
        entry["detail"] = detail
    result.setdefault("change_log", []).append(entry)
    return entry


def set_status(result, status, reason=None):
    result["status"] = status
    if result.get("runtime") is not None and isinstance(result["runtime"], dict):
        result["runtime"]["status"] = status
    if reason:
        append_change_log(result, "status_set", {"status": status, "reason": reason})


# ---------------------------------------------------------------------------
# Canonical keys and normalization
# ---------------------------------------------------------------------------

def normalize_doi(value):
    """Return a normalized lower-case DOI, or None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^doi\s*:\s*", "", s, flags=re.IGNORECASE)
    s = s.strip().strip(".,;)]}").strip()
    s = s.lower()
    return s or None


def extract_doi(record):
    """Pull a DOI from any plausible field, including URLs."""
    if not isinstance(record, dict):
        return None
    for k in ("doi", "DOI", "id_doi", "doi_url", "url", "link", "identifier"):
        v = record.get(k)
        if isinstance(v, str):
            m = _DOI_RE.search(v)
            if m:
                return normalize_doi(m.group(0))
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, str):
                    m = _DOI_RE.search(item)
                    if m:
                        return normalize_doi(m.group(0))
    return None


def normalize_arxiv(value):
    """Return a normalized arXiv ID without version suffix, or None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = re.sub(r"^https?://arxiv\.org/(abs|pdf)/", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^arxiv\s*:\s*", "", s, flags=re.IGNORECASE)
    m = _ARXIV_RE.search(s)
    if m:
        return m.group(1).lower()
    m2 = _ARXIV_LEGACY_RE.search(s)
    if m2:
        return m2.group(1).lower()
    s = s.strip().strip(".,;)]}")
    return s.lower() or None


def extract_arxiv(record):
    if not isinstance(record, dict):
        return None
    for k in ("arxiv", "arxiv_id", "eprint", "id_arxiv", "identifier", "url"):
        v = record.get(k)
        if isinstance(v, str):
            n = normalize_arxiv(v)
            if n:
                return n
    return None


def normalize_title(value):
    """Normalize a title for comparison (case, punctuation, whitespace)."""
    if value is None:
        return None
    s = str(value)
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[^\w\u4e00-\u9fff]+", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def _record_get(record, *names):
    for n in names:
        if isinstance(record, dict) and record.get(n) not in (None, ""):
            return record.get(n)
    return None


def canonical_key(record):
    """Return ``(kind, key)`` for a record.

    Priority: DOI > arXiv > normalized-title hash. Returns ``None`` when the
    record carries no usable identity.
    """
    if not isinstance(record, dict):
        return None
    doi = extract_doi(record)
    if doi:
        return ("doi", doi)
    arx = extract_arxiv(record)
    if arx:
        return ("arxiv", arx)
    title = normalize_title(_record_get(record, "title", "Title", "name"))
    if title:
        return ("title", "sha1:" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:16])
    return None


def version_marker(record):
    """Return a comparable tuple describing a record's publication stage.

    ``(type_rank, date_iso, version_string)``. Deterministic and collision-free
    for the common preprint→published→correction→retraction progression.
    """
    rtype = classify_type(record)
    # Ascending advancement order: unknown < preprint < published < correction
    # < retraction, so that ``max`` yields the most advanced stage.
    rank = {"unknown": -1, "preprint": 0, "published": 1,
            "correction": 2, "retraction": 3}.get(rtype, -1)
    date = to_utc_iso(_record_get(record, "date", "published", "pub_date",
                                  "published_date", "issued", "deposited",
                                  "created")) or ""
    version = ""
    for k in ("version", "revision", "v"):
        v = _record_get(record, k)
        if v not in (None, ""):
            version = str(v)
            break
    if not version:
        m = re.search(r"[vV](\d+)", str(_record_get(record, "arxiv", "eprint", "url")) or "")
        if m:
            version = "v" + m.group(1)
    return (rank, date, version)


def classify_type(record):
    """Classify a record into preprint/published/correction/retraction/unknown.

    Uses explicit type/status fields first, then venue heuristics. The Agent's
    findings may override a wrong guess; this default is deterministic.
    """
    if not isinstance(record, dict):
        return "unknown"
    blob = " ".join(
        str(record.get(k) or "")
        for k in ("type", "pub_type", "publication_type", "version_type", "kind",
                  "status", "update_type", "event_type")
    ).lower()
    if re.search(r"retract|withdraw|withdrawal|retraction", blob):
        return "retraction"
    if re.search(r"erratum|correction|corrigend|comment|addendum|expression of concern",
                 blob):
        return "correction"
    if re.search(r"preprint|pre-print|working paper|working_paper|arxiv|biorxiv|medrxiv|ssrn",
                 blob):
        return "preprint"
    if re.search(r"journal|article|published|epub|version of record|vor", blob):
        return "published"
    if record.get("is_retracted") or record.get("retracted"):
        return "retraction"
    venue = str(_record_get(record, "venue", "journal", "container_title",
                            "source", "publisher") or "").lower()
    if re.search(r"arxiv|biorxiv|medrxiv|ssrn|preprint", venue):
        return "preprint"
    return "unknown"


# ---------------------------------------------------------------------------
# Dedup / version-chain grouping
# ---------------------------------------------------------------------------

def dedup_records(records):
    """Group records by canonical key and merge duplicate/reprint variants.

    Returns a list of groups (dicts). Each group carries:

    - ``canonical_id``: the stable key string
    - ``key_kind``: doi | arxiv | title
    - ``representative``: the highest-stage record (deterministic choice)
    - ``members``: all raw records sharing the key
    - ``versions``: sorted distinct version markers
    - ``types``: distinct classification labels
    - ``suppressed``: list of suppression entries (duplicate/reprint merges)
    """
    buckets = {}
    for idx, rec in enumerate(records):
        if not isinstance(rec, dict):
            continue
        ck = canonical_key(rec)
        if ck is None:
            # Un-keyable records still surface for human review.
            ck = ("unkeyed", "unkeyed-%06d" % idx)
        kind, key = ck
        cid = "%s:%s" % (kind, key)
        buckets.setdefault(cid, []).append(rec)

    groups = []
    for cid, members in buckets.items():
        members.sort(key=lambda r: json.dumps(r, sort_keys=True, ensure_ascii=False))
        kind = cid.split(":", 1)[0]
        # Representative: most advanced stage (highest version_marker), with a
        # deterministic JSON tie-break so identical input yields identical output.
        rep = max(members, key=lambda r: (
            version_marker(r),
            json.dumps(r, sort_keys=True, ensure_ascii=False),
        ))
        distinct_types = sorted({classify_type(r) for r in members})
        distinct_versions = sorted({version_marker(r) for r in members})
        suppressed = []
        # Duplicate/reprint merge entries: all but the representative that share
        # identical identity but different source labels.
        if len(members) > 1:
            for r in members:
                if r is not rep:
                    suppressed.append({
                        "kind": "duplicate",
                        "reason": "merged into canonical group",
                        "source": _record_get(r, "source", "source_name", "origin")
                                   or "unknown",
                    })
        groups.append({
            "canonical_id": cid,
            "key_kind": kind,
            "representative": rep,
            "members": members,
            "member_count": len(members),
            "types": distinct_types,
            "version_markers": distinct_versions,
            "latest_version": distinct_versions[-1] if distinct_versions else None,
            "suppressed": suppressed,
        })

    # Stable order: unkeyed last, then by canonical id.
    groups.sort(key=lambda g: (g["key_kind"] == "unkeyed", g["canonical_id"]))
    return groups


def snapshot_diff(groups, snapshot):
    """Diff grouped records against a snapshot of previously seen versions.

    ``snapshot``: dict mapping canonical_id -> latest_version_marker (list/string
    form as stored). Returns ``(new_items, updates, unchanged, missing_ids)``.
    """
    snapshot = snapshot or {}
    new_items, updates, unchanged, missing = [], [], [], []
    current_ids = set()
    for g in groups:
        cid = g["canonical_id"]
        current_ids.add(cid)
        latest = g["latest_version"]
        if cid not in snapshot:
            new_items.append(g)
        elif _marker_str(snapshot[cid]) != _marker_str(latest):
            updates.append(g)
        else:
            unchanged.append(g)
    for cid in snapshot:
        if cid not in current_ids:
            missing.append(cid)
    return new_items, updates, unchanged, missing


def _marker_str(marker):
    return json.dumps(marker, sort_keys=True, ensure_ascii=False)


def build_snapshot(groups):
    """Derive a snapshot dict from current groups (canonical_id -> latest marker)."""
    snap = {}
    for g in groups:
        snap[g["canonical_id"]] = g["latest_version"]
    return snap


def build_reminders(new_items, updates, groups):
    """Derive reminder list with suppression ledger.

    Returns ``(reminders, suppressed)``. True ``new`` items and version advances
    (published/correction/retraction) generate reminders; duplicates/reprints
    merged during dedup are recorded as suppressed, never re-pushed.
    """
    reminders = []
    suppressed = []
    for g in new_items:
        if g["key_kind"] == "unkeyed":
            suppressed.append({"canonical_id": g["canonical_id"],
                               "reason": "unkeyed_item_not_reminded"})
            continue
        types = set(g["types"])
        if "retraction" in types:
            kind = "retraction"
        elif "correction" in types:
            kind = "correction"
        else:
            kind = "new"
        reminders.append({
            "canonical_id": g["canonical_id"],
            "kind": kind,
            "title": _record_get(g["representative"], "title", "Title"),
        })
    for g in updates:
        types = set(g["types"])
        if "retraction" in types:
            kind = "retraction"
        elif "correction" in types:
            kind = "correction"
        else:
            kind = "version_update"
        reminders.append({
            "canonical_id": g["canonical_id"],
            "kind": kind,
            "title": _record_get(g["representative"], "title", "Title"),
        })
    # Record merged duplicates / reprints as suppressed.
    for g in groups:
        for s in g.get("suppressed", []):
            suppressed.append({"canonical_id": g["canonical_id"],
                               "reason": s.get("reason", "duplicate"),
                               "source": s.get("source", "unknown")})
    return reminders, suppressed


# ---------------------------------------------------------------------------
# Finalize / report
# ---------------------------------------------------------------------------

def merge_findings(skeleton, findings):
    """Merge Agent findings into the skeleton, preserving contract fields.

    Findings keys override skeleton keys only for non-meta, non-contract
    domain fields; ``case_id`` is always protected. Meta fields are merged by
    appending lists and preferring the findings' status.
    """
    findings = findings or {}
    for k, v in findings.items():
        if k in ("case_id",):
            continue
        if k in META_FIELDS:
            continue
        skeleton[k] = v
    # Status: findings wins over partial; keep skeleton only if findings absent.
    if findings.get("status"):
        skeleton["status"] = findings["status"]
        if isinstance(skeleton.get("runtime"), dict):
            skeleton["runtime"]["status"] = findings["status"]
    for k in ("sources", "uncertainty", "errors", "degradations", "pending_human"):
        base = skeleton.get(k) or []
        extra = findings.get(k) or []
        if isinstance(extra, list):
            skeleton[k] = base + extra
    if isinstance(findings.get("change_log"), list):
        skeleton["change_log"] = (skeleton.get("change_log") or []) + findings["change_log"]
    # Runtime counters
    rt = skeleton.get("runtime")
    if isinstance(rt, dict):
        rt["model_requests_used"] = findings.get("model_requests_used",
                                                 rt.get("model_requests_used", 0))
        rt["finished_at"] = now_utc_iso()
        rt["status"] = skeleton["status"]
    return skeleton


def enrich_findings(findings):
    """Run dedup/snapshot/reminder enrichment on findings' candidate records.

    Returns an enriched copy with ``dedup``, ``tracking`` (new/updates/reminders)
    and a per-item ``canonical_id`` added where derivable.
    """
    enriched = dict(findings) if isinstance(findings, dict) else {}
    records = enriched.get("search_results") or []
    if isinstance(records, list) and records:
        groups = dedup_records(records)
        enriched["dedup"] = {
            "groups": [g for g in groups],
            "group_count": len(groups),
            "record_count": len(records),
            "canonical_keys": [g["canonical_id"] for g in groups],
        }
        snapshot = enriched.get("tracking", {}).get("snapshot") or None
        new_items, updates, _unchanged, missing = snapshot_diff(groups, snapshot)
        reminders, suppressed = build_reminders(new_items, updates, groups)
        tracking = dict(enriched.get("tracking") or {})
        tracking["new_items"] = [g["representative"] for g in new_items]
        tracking["updates"] = [g["representative"] for g in updates]
        tracking["reminders"] = reminders
        tracking["suppressed"] = suppressed
        tracking["missing_in_current"] = missing
        tracking["snapshot_derived"] = build_snapshot(groups)
        enriched["tracking"] = tracking
        # Attach canonical_id to each raw record for replayability.
        for rec in records:
            if isinstance(rec, dict) and "canonical_id" not in rec:
                ck = canonical_key(rec)
                if ck:
                    rec["canonical_id"] = "%s:%s" % ck
    return enriched


def write_json(path, obj):
    """Write deterministic UTF-8 JSON (sorted keys, ensure_ascii off)."""
    text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.write("\n")


def build_report(result):
    """Render a human-readable Markdown report consistent with result.json."""
    lines = []
    lines.append("# 文献发现与追踪报告\n")
    lines.append("- case_id: `%s`" % result.get("case_id", ""))
    lines.append("- status: `%s`" % result.get("status", ""))
    lines.append("- 生成时间: %s" % now_utc_iso())
    rt = result.get("runtime") or {}
    lines.append("- 运行预算: %s 秒 / %s 次模型请求（已用 %s）" % (
        rt.get("budget_seconds"), rt.get("budget_model_requests"),
        rt.get("model_requests_used", 0)))
    lines.append("")

    # Tracking block
    tracking = result.get("tracking") or {}
    if tracking:
        lines.append("## 追踪闭环\n")
        lines.append("- 订阅配置: %s" % _md(tracking.get("subscription")))
        lines.append("- 时间窗: %s" % _md(tracking.get("window")))
        lines.append("- 新增条目: %d" % len(tracking.get("new_items") or []))
        lines.append("- 版本更新: %d" % len(tracking.get("updates") or []))
        lines.append("- 提醒: %d" % len(tracking.get("reminders") or []))
        lines.append("- 抑制(重复/转载): %d" % len(tracking.get("suppressed") or []))
        lines.append("")

    # Search results
    sr = result.get("search_results")
    if isinstance(sr, list):
        lines.append("## 检索结果（%d 条）\n" % len(sr))
        for i, rec in enumerate(sr, 1):
            lines.append("- %d. %s" % (i, _md(_record_get(rec, "title", "Title")
                                            or "(无标题)")))
            doi = extract_doi(rec) or _record_get(rec, "doi", "DOI")
            if doi:
                lines.append("  - DOI/标识: `%s`" % doi)
        lines.append("")

    # Evidence
    ev = result.get("evidence")
    if isinstance(ev, list):
        lines.append("## 证据记录（%d 条）\n" % len(ev))
        for i, e in enumerate(ev, 1):
            if isinstance(e, dict):
                lines.append("- %d. %s" % (i, _md(e.get("claim") or e.get("item")
                                                or "")))
                if e.get("locator"):
                    lines.append("  - locator: `%s`" % e.get("locator"))
                if e.get("quote"):
                    lines.append("  - quote: %s" % _md(e.get("quote")))
            else:
                lines.append("- %d. %s" % (i, _md(e)))
        lines.append("")

    # Uncertainty / errors / pending
    for key, title in (("uncertainty", "不确定性"), ("errors", "错误"),
                       ("degradations", "降级"), ("pending_human", "待人工处理")):
        v = result.get(key)
        if v:
            lines.append("## %s\n" % title)
            for item in v:
                lines.append("- %s" % _md(item))
            lines.append("")
    return "\n".join(lines)


def _md(value):
    if value is None:
        return "`null`"
    if isinstance(value, (dict, list)):
        return "`%s`" % json.dumps(value, ensure_ascii=False)
    return str(value).replace("\n", " ")


def write_result_and_report(output_dir, result):
    """Write result.json and report.md; return paths."""
    import os
    os.makedirs(output_dir, exist_ok=True)
    result_path = os.path.join(output_dir, "result.json")
    report_path = os.path.join(output_dir, "report.md")
    write_json(result_path, result)
    try:
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write(build_report(result))
    except Exception as exc:
        # Report failure must not lose the JSON result.
        result.setdefault("errors", []).append("report write failed: %s" % exc)
        write_json(result_path, result)
    return result_path, report_path


def finalize(input_dir, output_dir, work_dir, findings_path):
    """Full deterministic finalize: skeleton → findings merge → enrich → write.

    Returns ``(result, result_path, report_path, errors)``. Idempotent: the
    same inputs + findings produce byte-identical result.json.
    """
    errors = []
    case_id, contract_keys, data, errs = extract_case(input_dir)
    errors.extend(errs)

    skeleton = make_skeleton(case_id, contract_keys,
                             status=_initial_status(data, errors), errors=errors)
    skeleton["_contract_fields"] = list(contract_keys)
    append_change_log(skeleton, "contract_discovered",
                      {"fields": contract_keys, "source": data.get("case_path")})

    findings = {}
    if findings_path and __import__("os").path.isfile(findings_path):
        findings, ferrs = load_json(findings_path)
        errors.extend(ferrs)
        if ferrs:
            findings = {}

    merged = merge_findings(skeleton, findings)
    enriched = enrich_findings(merged)
    # Re-apply meta merge after enrichment (enrich_findings may add tracking/dedup).
    result = merged
    if isinstance(enriched.get("dedup"), dict):
        result["dedup"] = enriched["dedup"]
    if isinstance(enriched.get("tracking"), dict):
        result["tracking"] = enriched["tracking"]
    # Re-attach per-record canonical ids
    if isinstance(enriched.get("search_results"), list):
        result["search_results"] = enriched["search_results"]

    # Final status: if findings absent and status is partial, keep partial.
    if not findings and result.get("status") == "partial":
        set_status(result, "insufficient_evidence" if not data.get("materials")
                   else "partial",
                   "no findings supplied by agent")

    append_change_log(result, "finalized", {"contract_fields": list(contract_keys)})
    result.pop("_contract_fields", None)

    result_path, report_path = write_result_and_report(output_dir, result)
    return result, result_path, report_path, errors
