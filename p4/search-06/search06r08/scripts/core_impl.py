# -*- coding: utf-8 -*-
"""Deterministic core for the literature-tracking Skill (standard library only).

All functions here are pure or idempotent file operations: re-running with the
same inputs produces identical outputs, which is what makes "same snapshot +
same window + same sources -> same new/updated/removed" verifiable.

The Agent (model) performs the actual retrieval and judgement; this module
turns its findings into a normalized, replayable tracking state. It never
fetches from the network and never invents records.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

MAX_FILE_BYTES = 2 * 1024 * 1024


class OversizeFileError(Exception):
    """Raised when a single input file exceeds the size cap."""


class InputFormatError(Exception):
    """Raised when a file cannot be read or parsed; callers record and continue."""


# ---------------------------------------------------------------------------
# Honest I/O helpers
# ---------------------------------------------------------------------------

def read_text(path, max_bytes=MAX_FILE_BYTES):
    """Read text with utf-8-sig tolerance and a hard size cap.

    Falls back to utf-8, then latin-1 (lossy) so a bad-encoding file still
    yields *something* instead of crashing. Callers must record degradations.
    """
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError as exc:
        raise InputFormatError("cannot stat %s: %s" % (path, exc))
    if size > max_bytes:
        raise OversizeFileError("file %s exceeds %d bytes (size=%d)" % (path, max_bytes, size))
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def load_json(path, max_bytes=MAX_FILE_BYTES):
    """Load JSON with BOM tolerance; raises InputFormatError on malformed input."""
    text = read_text(path, max_bytes).lstrip("\ufeff")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputFormatError("JSON parse failed for %s: %s" % (path, exc))


def write_text_atomic(path, text):
    """Write text atomically so a crash mid-write never leaves a torn file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.parent / (p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(p))


def write_json_atomic(path, obj):
    """Deterministic JSON write: sorted keys, no BOM, atomic replace."""
    write_text_atomic(path, json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))


# ---------------------------------------------------------------------------
# Normalization (stable keys)
# ---------------------------------------------------------------------------

_DOI_URL_RE = re.compile(r"^https?://(?:dx\.)?doi\.org/", re.I)
_DOI_PREFIX_RE = re.compile(r"^doi:\s*", re.I)


def normalize_doi(value):
    """Normalize a DOI string; return None when it is absent or not DOI-like."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    d = value.strip()
    d = _DOI_URL_RE.sub("", d)
    d = _DOI_PREFIX_RE.sub("", d)
    d = d.strip().strip("\"'[]()").rstrip(".,;")
    d = d.lower()
    d = re.sub(r"\s+", "", d)
    if not d or "/" not in d or len(d) < 6:
        return None
    return d


_ARXIV_URL_RE = re.compile(r"^https?://(?:www\.)?arxiv\.org/abs/", re.I)
_ARXIV_PREFIX_RE = re.compile(r"^arxiv\s*:\s*", re.I)


def normalize_arxiv(value):
    """Normalize an arXiv identifier; return None when it is not arXiv-like."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    a = value.strip()
    a = _ARXIV_URL_RE.sub("", a)
    a = _ARXIV_PREFIX_RE.sub("", a)
    a = re.sub(r"(v\d+)$", "", a, flags=re.I).strip()
    a = a.lower()
    if not a:
        return None
    # New style: 2001.01234 ; old style: hep-th/9901001
    if re.match(r"^\d{4}\.\d{4,5}$", a):
        return a
    if re.match(r"^[a-z.-]+/\d{7}$", a):
        return a
    return None


def normalize_title(value):
    """NFKC-normalize, lowercase, strip punctuation; used as fallback key."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    t = unicodedata.normalize("NFKC", value)
    t = t.lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t or None


def canonical_key(record):
    """Stable identity key: DOI > arXiv > normalized title (in that priority)."""
    doi = normalize_doi(record.get("doi"))
    if doi:
        return ("doi", doi)
    arxiv = normalize_arxiv(record.get("arxiv") or record.get("arxiv_id") or record.get("eprint"))
    if arxiv:
        return ("arxiv", arxiv)
    title = normalize_title(record.get("title"))
    if title:
        return ("title", title)
    return None


# ---------------------------------------------------------------------------
# Version classification and chains
# ---------------------------------------------------------------------------

VERSION_ORDER = {"preprint": 0, "journal": 1, "correction": 2, "retraction": 3, "unknown": 4}

_RETRACT_WORDS = ("retracted", "retraction", "retract", "withdrawn")
_CORRECT_WORDS = ("erratum", "corrigendum", "correction", "corrected", "addendum")
_PREPRINT_WORDS = ("preprint", "arxiv", "working paper", "workingpaper", "pre-print", "preprint version")


def classify_version(record):
    """Classify a record's version stage from its text fields.

    Order of precedence: retraction > correction > preprint > journal.
    """
    text = " ".join(
        str(record.get(k) or "")
        for k in ("type", "version", "subtype", "title", "note", "status", "message", "content", "abstract")
    ).lower()
    if any(w in text for w in _RETRACT_WORDS):
        return "retraction"
    if any(w in text for w in _CORRECT_WORDS):
        return "correction"
    if any(w in text for w in _PREPRINT_WORDS):
        return "preprint"
    if record.get("doi") and (record.get("journal") or record.get("container_title")
                              or record.get("container-title") or "journal" in text):
        return "journal"
    return "unknown"


def dedup_records(records):
    """Group records by canonical key; return (groups, unkeyed).

    Within a group, members are ordered by version stage so the last member is
    the most authoritative current state (retraction > correction > journal >
    preprint). Records without any stable key are returned separately so they
    are never silently dropped.
    """
    groups = {}
    unkeyed = []
    for idx, rec in enumerate(records):
        key = canonical_key(rec)
        if key is None:
            unkeyed.append({"index": idx, "record": rec})
            continue
        groups.setdefault(key, []).append(rec)
    result = []
    for key in sorted(groups, key=lambda kv: kv[1]):
        items = groups[key]
        items = sorted(items, key=lambda r: VERSION_ORDER.get(classify_version(r), 4))
        result.append({
            "key_type": key[0],
            "key": key[1],
            "count": len(items),
            "representative": items[-1],
            "members": items,
        })
    return result, unkeyed


def build_version_chains(records):
    """Link preprint -> journal -> correction/retraction across identity keys.

    Work identity is the normalized title (preprint and published versions
    usually share the title while DOIs may differ).
    """
    chains = {}
    for rec in records:
        title = normalize_title(rec.get("title"))
        if not title:
            continue
        chains.setdefault(title, []).append(rec)
    out = []
    for work, members in chains.items():
        members = sorted(members, key=lambda r: VERSION_ORDER.get(classify_version(r), 4))
        stages = [classify_version(m) for m in members]
        out.append({
            "work_key": work,
            "stages": stages,
            "is_version_chain": len(set(stages)) > 1,
            "members": members,
        })
    out.sort(key=lambda c: c["work_key"])
    return out


# ---------------------------------------------------------------------------
# Time / window logic
# ---------------------------------------------------------------------------

def parse_date(value):
    """Parse many common date encodings into an aware UTC datetime, or None."""
    if value is None:
        return None
    if isinstance(value, dict):
        # Crossref / EuropePMC style {"date-parts": [[2024, 3, 15]]}
        for k in ("date-parts", "date_parts"):
            parts = value.get(k)
            if isinstance(parts, list) and parts:
                dp = parts[0]
                if isinstance(dp, list) and dp:
                    try:
                        y = int(dp[0])
                        m = int(dp[1]) if len(dp) > 1 and dp[1] else 1
                        d = int(dp[2]) if len(dp) > 2 and dp[2] else 1
                        return datetime(y, m, d, tzinfo=timezone.utc)
                    except (ValueError, TypeError):
                        pass
        for k in ("date-time", "date_time", "timestamp", "$date"):
            r = parse_date(value.get(k))
            if r is not None:
                return r
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    s = str(value).strip()
    if not s:
        return None
    s2 = s.replace("Z", "+00:00")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S%z"):
        try:
            return datetime.strptime(s2, fmt)
        except ValueError:
            continue
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    m = re.match(r"^(\d{4})$", s)
    if m:
        return datetime(int(m.group(1)), 1, 1, tzinfo=timezone.utc)
    return None


def to_utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def record_date(record):
    """Best-effort publication/indexing date from a record, in UTC."""
    for k in ("published", "publication_date", "date_published", "date", "issued",
              "deposited", "indexed", "created", "posted"):
        d = parse_date(record.get(k))
        if d is not None:
            return d
    for k in ("published-print", "published-online", "published_print", "published_online"):
        d = parse_date(record.get(k))
        if d is not None:
            return d
    return None


def in_time_window(record, window):
    """Closed-interval membership [start, end] in UTC.

    Returns True/False, or None when the record date or the window is missing.
    """
    d = record_date(record)
    if d is None:
        return None
    start = parse_date(window.get("start")) if isinstance(window, dict) else None
    end = parse_date(window.get("end")) if isinstance(window, dict) else None
    if start is None or end is None:
        return None
    d, start, end = to_utc(d), to_utc(start), to_utc(end)
    return start <= d <= end


# ---------------------------------------------------------------------------
# Increment classification against a snapshot
# ---------------------------------------------------------------------------

def diff_snapshot(candidates, snapshot, window):
    """Classify each candidate against the previous snapshot.

    Returns five buckets, each entry = {"record": ..., "reason": ...}:
      * new_items      — genuinely new work inside the current window
      * updates        — correction/erratum on a work already seen
      * retractions    — retraction/withdrawal notices
      * late_indexed   — old work that only just got indexed (suppressed from "new")
      * suppressed     — duplicates of already-seen work, or date/window unknown
    """
    seen = set((snapshot or {}).get("canonical_ids") or []) if isinstance(snapshot, dict) else set()
    result = {
        "new_items": [],
        "updates": [],
        "retractions": [],
        "late_indexed": [],
        "suppressed": [],
    }
    for rec in candidates:
        key = canonical_key(rec)
        kid = key[1] if key else None
        cls = classify_version(rec)
        in_win = in_time_window(rec, window)
        if cls == "retraction":
            result["retractions"].append({"record": rec, "reason": "retraction_notice"})
        elif cls == "correction":
            if kid is not None and kid in seen:
                result["updates"].append({"record": rec, "reason": "correction_of_seen_work"})
            else:
                result["new_items"].append({"record": rec, "reason": "correction_of_new_work"})
        elif kid is not None and kid in seen:
            result["suppressed"].append({"record": rec, "reason": "already_seen_duplicate"})
        elif in_win is True:
            result["new_items"].append({"record": rec, "reason": "in_window_new"})
        elif in_win is False:
            result["late_indexed"].append({"record": rec, "reason": "late_indexed_old_item"})
        else:
            result["suppressed"].append({"record": rec, "reason": "missing_date_or_window"})
    return result


def build_reminders(diff_result):
    """Merge the diff buckets into a notification list and a suppressed list.

    Duplicate notifications within a single run are collapsed; late-indexed and
    already-seen items are suppressed with an explicit reason so the suppression
    is itself auditable.
    """
    reminders = []
    suppressed = []
    emitted = set()
    for kind in ("retractions", "updates", "new_items"):
        for entry in diff_result.get(kind, []):
            rec = entry["record"]
            key = canonical_key(rec)
            kid = key[1] if key else None
            if kid is not None and kid in emitted:
                suppressed.append(dict(rec, suppress_reason="duplicate_notification"))
                continue
            reminders.append({"kind": kind, "reason": entry["reason"], "record": rec})
            if kid is not None:
                emitted.add(kid)
    for entry in diff_result.get("late_indexed", []):
        suppressed.append(dict(entry["record"], suppress_reason="late_indexed_old_item"))
    for entry in diff_result.get("suppressed", []):
        suppressed.append(dict(entry["record"], suppress_reason=entry["reason"]))
    return {"reminders": reminders, "suppressed": suppressed}


# ---------------------------------------------------------------------------
# Small validation helpers
# ---------------------------------------------------------------------------

def validate_evidence(evidence):
    """Flag evidence entries that lack a locator or a verbatim quote."""
    problems = []
    for i, ev in enumerate(evidence or []):
        if not isinstance(ev, dict):
            problems.append({"index": i, "issue": "not_an_object"})
            continue
        if not (ev.get("locator") or "").strip():
            problems.append({"index": i, "issue": "missing_locator"})
        if not (ev.get("quote") or "").strip():
            problems.append({"index": i, "issue": "missing_quote"})
    return problems


def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
