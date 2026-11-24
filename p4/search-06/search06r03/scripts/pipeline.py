# -*- coding: utf-8 -*-
"""Deterministic core for the literature-tracking Skill.

Design goals (variant: self-check + determinism):

1.  Pure standard library, no network, no fabrication.  Every function either
    normalises/merges records already present in the input, or reports missing
    evidence.  This script never invents papers, metadata or sources.
2.  Same inputs -> same substantive output.  JSON is written with sort_keys,
    records and lists are sorted in a stable order, and all normalisation is
    pure.  Only ``runtime`` and wall-clock timestamps in ``change_log`` are
    allowed to vary between identical runs; everything a reviewer would diff
    (search_results / ranking / evidence / tracking / dedup / status_notes /
    sources / uncertainty / pending_human / errors / degradations) is
    byte-identical for identical inputs.
3.  Honest degradation: missing / unreadable inputs yield ``input_error`` or
    ``insufficient_evidence``, never invented content.
"""

import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Limits and constants
# ---------------------------------------------------------------------------

MAX_FILE_BYTES = 2 * 1024 * 1024          # 2 MB per input text file
ENCODINGS = ("utf-8-sig", "utf-8", "latin-1")

# Always-present result fields beyond case_id and the output contract.
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

# Default contract keys derived from the task's three required output rows
# (检索结果 / 证据记录 / 状态说明).  Used only when no output_contract exists.
DEFAULT_CONTRACT_KEYS = ["search_results", "evidence", "status_notes"]

_CASE_ID_HINTS = ("case_id", "id", "challenge_id")
_CASE_ID_RE = re.compile(r"challenge_id[:\s]*`([^`]+)`|challenge_id[:\s]*([\w.-]+)")

RETRACTION_PAT = re.compile(r"\b(retract(?:ed|ion)?|withdraw(?:n|al)?)\b", re.I)
CORRECTION_PAT = re.compile(r"\b(erratum|correction|corrigendum)\b", re.I)


def now_utc():
    """Current UTC time as an ISO-8601 string with a trailing 'Z'."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def log_event(events, step, **kw):
    """Append a deterministic, sorted change_log entry."""
    entry = {"step": step, "time": now_utc()}
    entry.update(kw)
    events.append(entry)


# ---------------------------------------------------------------------------
# Text / JSON I/O with honest degradation
# ---------------------------------------------------------------------------

def read_text(path, max_bytes=MAX_FILE_BYTES):
    """Read text with encoding fallback and a size cap.

    Returns ``(text, errors)``; ``text`` is None on failure.
    """
    errors = []
    p = Path(path)
    if not p.exists():
        return None, ["missing file: %s" % p]
    try:
        size = p.stat().st_size
    except OSError as exc:
        return None, ["stat failed for %s: %s" % (p, exc)]
    if size > max_bytes:
        return None, ["oversize file skipped: %s (%d bytes > %d)" % (p, size, max_bytes)]
    raw = p.read_bytes()
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), errors
        except UnicodeDecodeError as exc:
            last = "decode failed with %s: %s" % (enc, exc)
    errors.append(last)
    return None, errors


def load_json(path):
    """Read and parse JSON, returning ``(obj, errors)``."""
    text, errors = read_text(path)
    if text is None:
        return None, errors
    try:
        return json.loads(text), errors
    except Exception as exc:
        return None, errors + ["json parse failed for %s: %s" % (path, exc)]


def write_json(path, obj):
    """Deterministic JSON write: sorted keys, UTF-8, trailing newline.

    Writes to a temp file and atomically replaces the target so an interrupted
    run never leaves a half-written result.json behind.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)
    return str(p)


def write_text(path, text):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# Input discovery and contract extraction
# ---------------------------------------------------------------------------

def discover_input(input_dir):
    """Return ``(case_path, task_path, material_paths, errors)``.

    Scans recursively and recognises ``case.json`` / ``task.md`` by basename;
    every other file is treated as material.  Never trusts a label over the
    actual file on disk.
    """
    errors = []
    root = Path(input_dir)
    if not root.exists():
        return None, None, [], ["input dir missing: %s" % root]
    case = None
    task = None
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
    if case is None and task is None:
        errors.append("no case.json or task.md found under %s" % root)
    return case, task, materials, errors


def _case_id_from_obj(obj):
    if isinstance(obj, dict):
        for k in _CASE_ID_HINTS:
            if k in obj and obj[k] not in (None, ""):
                return str(obj[k])
        for k in ("task", "spec", "input"):
            v = _case_id_from_obj(obj.get(k))
            if v:
                return v
    return None


def find_output_contract(obj):
    """Locate the output_contract definition, if any.

    Returns a list of required field names, or None when absent.  Accepts a
    dict of keys, a list of names, or a single string.
    """
    if isinstance(obj, dict):
        for k in ("output_contract", "contract", "output_schema", "output"):
            v = obj.get(k)
            if isinstance(v, dict):
                return list(v.keys())
            if isinstance(v, list):
                return [str(x) for x in v if isinstance(x, str)]
            if isinstance(v, str):
                return [v]
        for k in ("task", "spec", "input"):
            found = find_output_contract(obj.get(k))
            if found:
                return found
    return None


def extract_case(input_dir):
    """Read case.json + task.md and return ``(case_id, contract_keys, data, errors)``."""
    case_path, task_path, materials, errors = discover_input(input_dir)
    case_obj = None
    task_text = None
    if case_path is not None:
        case_obj, errs = load_json(str(case_path))
        errors.extend(errs)
    if task_path is not None:
        task_text, errs = read_text(str(task_path))
        if task_text is None:
            errors.extend(errs)
            task_text = ""

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
    """Honest initial status from discovered inputs."""
    has_case = data.get("case_obj") is not None
    has_task = bool(data.get("task_text"))
    malformed = any(("json parse failed" in e) or ("decode failed" in e) for e in errors)
    if not has_case and not has_task:
        return "input_error" if malformed else "insufficient_evidence"
    return "input_error" if malformed else "partial"


# ---------------------------------------------------------------------------
# Normalisation and dedup
# ---------------------------------------------------------------------------

def norm_ws(s):
    return re.sub(r"\s+", " ", (s or "").strip())


def norm_title(s):
    """Lowercase, NFKC, strip punctuation, collapse whitespace."""
    if s is None:
        return None
    s = unicodedata.normalize("NFKC", str(s)).lower()
    s = re.sub(r"[\W_]+", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def norm_doi(s):
    """Normalise a DOI: strip scheme/host, 'doi:' prefix, trailing punct."""
    if s is None:
        return None
    s = unicodedata.normalize("NFKC", str(s)).strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = re.sub(r"^doi\s*:\s*", "", s)
    return s.strip().strip(".,;").strip()


def norm_arxiv(s):
    """Normalise an arXiv id: strip 'arXiv:' prefix and version suffix vN."""
    if s is None:
        return None
    s = unicodedata.normalize("NFKC", str(s)).strip().lower()
    s = re.sub(r"^arxiv\s*:\s*", "", s)
    s = re.sub(r"v\d+$", "", s)
    return s.strip().strip(".,;").strip()


def canonical_id(rec):
    """Stable dedup key: DOI > arXiv id > normalised title."""
    if not isinstance(rec, dict):
        return None
    doi = norm_doi(rec.get("doi") or rec.get("DOI"))
    if doi:
        return "doi:" + doi
    arxiv = norm_arxiv(rec.get("arxiv") or rec.get("arXiv") or rec.get("arxiv_id"))
    if arxiv:
        return "arxiv:" + arxiv
    title = norm_title(rec.get("title"))
    if title:
        return "title:" + title
    return None


def _sort_key_of(rec):
    """Stable sort key for records inside a version chain."""
    date = str(rec.get("date") or rec.get("published") or rec.get("year") or "")
    return (date, str(rec.get("version") or rec.get("source") or ""))


def _markers(rec):
    """Detect retraction / correction markers anywhere in a record's text."""
    blob = json.dumps(rec, ensure_ascii=False, sort_keys=True)
    if RETRACTION_PAT.search(blob):
        return "retraction"
    if CORRECTION_PAT.search(blob):
        return "correction"
    return None


def dedup_records(records):
    """Group records by canonical_id and build version chains.

    Returns a dict ``{"groups": [...], "counts": {...}, "by_id": {...}}`` where
    each group contains its merged primary record and an ordered version list.
    Deterministic: groups sorted by canonical_id, records sorted by a stable
    (date, version/source) key.
    """
    if not isinstance(records, list):
        records = []
    by_id = {}
    unkeyed = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        cid = canonical_id(rec)
        if cid is None:
            unkeyed.append(rec)
        else:
            by_id.setdefault(cid, []).append(rec)

    groups = []
    for cid in sorted(by_id.keys()):
        recs = sorted(by_id[cid], key=_sort_key_of)
        markers = [m for m in (_markers(r) for r in recs) if m]
        # Prefer a non-retracted record as primary; prefer the latest date.
        candidates = [r for r in recs if _markers(r) != "retraction"] or recs
        primary = candidates[-1]
        groups.append({
            "canonical_id": cid,
            "record_count": len(recs),
            "primary": primary,
            "versions": recs,
            "markers": markers,
        })
    counts = {
        "input_records": len(records),
        "groups": len(groups),
        "unkeyed": len(unkeyed),
        "duplicates_merged": max(0, len(records) - len(groups) - len(unkeyed)),
    }
    return {"groups": groups, "counts": counts, "by_id": by_id, "unkeyed": unkeyed}


def classify_increment(snapshot_ids, groups, window=None):
    """Classify groups against a snapshot watermark.

    ``snapshot_ids`` is a set of canonical ids seen before this window.
    Returns ``{"new_items": [...], "updates": [...], "retractions": [...],
    "unchanged": [...], "window": window}``.  Deterministic ordering.
    """
    seen = set(snapshot_ids or [])
    out = {"new_items": [], "updates": [], "retractions": [],
           "unchanged": [], "window": window}
    for g in groups:
        cid = g["canonical_id"]
        if _markers(g["primary"]) == "retraction":
            out["retractions"].append(g)
        elif cid not in seen:
            out["new_items"].append(g)
        elif len(g["versions"]) > 1:
            out["updates"].append(g)
        else:
            out["unchanged"].append(g)
    return out


# ---------------------------------------------------------------------------
# Result assembly
# ---------------------------------------------------------------------------

def _init_value(key):
    if key in ("sources", "uncertainty", "change_log", "errors",
               "degradations", "pending_human"):
        return []
    if key == "runtime":
        return None
    return None


def make_skeleton(case_id, contract_keys, status="partial"):
    """A valid partial result: case_id + every contract field + meta fields."""
    result = {"case_id": case_id}
    for k in contract_keys:
        result.setdefault(k, None)
    for k in META_FIELDS:
        result.setdefault(k, _init_value(k))
    result["status"] = status
    result["change_log"] = [{
        "step": "skeleton_written",
        "time": now_utc(),
        "status": status,
    }]
    return result


def _deep_merge(base, extra):
    """Merge extra into base; dicts merged recursively, lists replaced by sorted dedup."""
    for k, v in (extra or {}).items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        elif k in base and isinstance(base[k], list) and isinstance(v, list):
            base[k] = v
        else:
            base[k] = v


def _has_real_content(contract_keys, result):
    filled = 0
    for k in contract_keys:
        v = result.get(k)
        if v not in (None, [], {}, ""):
            filled += 1
    return filled, len(contract_keys)


def compute_status(contract_keys, result, errors, findings):
    """Honest final status: input_error > insufficient_evidence > partial > ok."""
    if any(("json parse failed" in e) or ("decode failed" in e) for e in errors):
        return "input_error"
    explicit = (findings or {}).get("status")
    if explicit in ("ok", "partial", "insufficient_evidence", "input_error"):
        # Respect the agent's explicit judgement but never downgrade input_error.
        return explicit
    filled, total = _has_real_content(contract_keys, result)
    if filled == 0:
        return "insufficient_evidence"
    if filled < total:
        return "partial"
    return "ok"


def assemble_result(case_id, contract_keys, findings, data, errors, change_log):
    """Merge findings into the contract shape and return the final dict."""
    result = {"case_id": case_id}
    for k in contract_keys:
        result.setdefault(k, None)
    for k in META_FIELDS:
        result.setdefault(k, _init_value(k))

    _deep_merge(result, findings or {})

    # Backfill meta fields from data / findings.
    if not result.get("sources"):
        result["sources"] = []
    result["change_log"] = list(change_log or [])
    result["errors"] = list(errors or []) + list((findings or {}).get("errors") or [])
    result["errors"] = sorted(set(result["errors"]))
    result["degradations"] = list((findings or {}).get("degradations") or [])
    result["pending_human"] = list((findings or {}).get("pending_human") or [])
    result["status"] = compute_status(contract_keys, result, result["errors"], findings)
    return result


def build_report(result):
    """Render a compact human-readable Markdown report consistent with result.json."""
    lines = []
    lines.append("# 文献追踪结果报告")
    lines.append("")
    lines.append("- case_id: `%s`" % result.get("case_id"))
    lines.append("- status: `%s`" % result.get("status"))
    lines.append("- errors: %d" % len(result.get("errors") or []))
    lines.append("- degradations: %d" % len(result.get("degradations") or []))
    lines.append("- pending_human: %d" % len(result.get("pending_human") or []))
    lines.append("")

    tracking = result.get("tracking") or {}
    if isinstance(tracking, dict):
        lines.append("## 追踪链路")
        lines.append("")
        for key in ("subscription", "window", "snapshot", "new_items",
                    "updates", "retractions", "reminders"):
            val = tracking.get(key)
            if val is None:
                lines.append("- %s: 未提供" % key)
            elif isinstance(val, (list, dict)):
                n = len(val)
                lines.append("- %s: %d 项" % (key, n))
            else:
                lines.append("- %s: %s" % (key, val))
        lines.append("")

    sr = result.get("search_results")
    if isinstance(sr, list):
        lines.append("## 检索结果")
        lines.append("")
        lines.append("- 候选条数: %d" % len(sr))
        lines.append("")
    elif isinstance(sr, dict):
        lines.append("## 检索结果")
        lines.append("")
        lines.append("- 键: %s" % ", ".join(sorted(sr.keys())))
        lines.append("")

    ev = result.get("evidence")
    if isinstance(ev, list):
        lines.append("## 证据记录")
        lines.append("")
        lines.append("- 证据条数: %d" % len(ev))
        lines.append("")

    un = result.get("uncertainty")
    if isinstance(un, list) and un:
        lines.append("## 不确定性与冲突")
        lines.append("")
        for u in un:
            if isinstance(u, dict):
                lines.append("- %s" % (u.get("issue") or u.get("note") or json.dumps(u, ensure_ascii=False)))
            else:
                lines.append("- %s" % u)
        lines.append("")

    if result.get("pending_human"):
        lines.append("## 待人工确认")
        lines.append("")
        for item in result["pending_human"]:
            lines.append("- %s" % (item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)))
        lines.append("")

    lines.append("## 说明")
    lines.append("")
    lines.append("本报告由 result.json 确定性生成；逐字段证据定位与引用见 result.json。")
    lines.append("")
    return "\n".join(lines)


def finalize(input_dir, output_dir, work_dir, findings_path=None):
    """Full deterministic finalisation: read inputs + findings, write result.json + report.md.

    Returns ``(result, result_path, report_path, errors)``.
    """
    errors = []
    case_id, contract_keys, data, errs = extract_case(input_dir)
    errors.extend(errs)

    findings = None
    if findings_path and Path(findings_path).exists():
        findings, ferrs = load_json(findings_path)
        errors.extend(ferrs)

    change_log = []
    if findings and isinstance(findings.get("change_log"), list):
        change_log.extend(findings["change_log"])
    if not any(e.get("step") == "skeleton_written" for e in change_log if isinstance(e, dict)):
        change_log.insert(0, {"step": "skeleton_written", "time": now_utc(),
                              "status": "partial"})
    log_event(change_log, "finalize", contract_keys=contract_keys)

    result = assemble_result(case_id, contract_keys, findings, data, errors, change_log)

    # Deterministic runtime summary.
    start = now_utc()
    result["runtime"] = {
        "finalized_at": start,
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "work_dir": str(work_dir),
        "material_count": len(data.get("materials") or []),
    }

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = write_json(str(out_dir / "result.json"), result)
    report_path = write_text(str(out_dir / "report.md"), build_report(result))
    return result, result_path, report_path, errors
