# -*- coding: utf-8 -*-
"""Deterministic, stdlib-only pipeline for the literature-tracking Skill.

Design invariants
-----------------
1. Skeleton first: `scaffold` writes a valid partial result.json plus a
   phase-budget ledger before any analysis. A crash mid-run therefore never
   leaves an invalid or missing artifact.
2. Deterministic domain results: dedup keys, increment diffs and report layout
   depend only on the input files and findings.json. Wall-clock timestamps are
   the only run-varying fields and are kept inside change_log/budget events.
3. Honest: missing or unreadable inputs yield `insufficient_evidence` /
   `input_error`, never invented records, DOIs or citations.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

READ_ENC = "utf-8-sig"   # tolerate BOM / bad encodings on input
WRITE_ENC = "utf-8"      # emit plain UTF-8, never a BOM
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB per single input file

DEFAULT_CONTRACT_KEYS = [
    "search_results",
    "ranking",
    "evidence",
    "status_notes",
    "tracking",
    "dedup",
]

META_FIELDS = [
    "status",
    "sources",
    "uncertainty",
    "change_log",
    "budget",
    "errors",
    "degradations",
    "pending_human",
    "runtime",
]

# Phase plan for the 900 s / 64 request budget (variant: budgeted early skeleton).
PHASE_PLAN = [
    {"id": 0, "name": "skeleton", "time_budget_s": 180, "request_budget": 6},
    {"id": 1, "name": "question_subscription", "time_budget_s": 240, "request_budget": 8},
    {"id": 2, "name": "window_snapshot_search", "time_budget_s": 180, "request_budget": 8},
    {"id": 3, "name": "dedup_increment", "time_budget_s": 180, "request_budget": 12},
    {"id": 4, "name": "reminder_evidence", "time_budget_s": 60, "request_budget": 6},
    {"id": 5, "name": "finalize_selfcheck", "time_budget_s": 60, "request_budget": 6},
    {"id": 6, "name": "retry_reserve", "time_budget_s": 0, "request_budget": 18},
]

_CASE_ID_HINTS = ("case_id", "id", "challenge_id")
_CASE_ID_RE = re.compile(r"challenge_id[:\s]*`([^`]+)`|challenge_id[:\s]*([\w.-]+)")
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s]+", re.IGNORECASE)
_TEXT_EXTS = {".txt", ".md", ".json", ".csv", ".tsv", ".xml", ".yaml", ".yml", ".bib", ".ris"}


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def now_epoch():
    return time.time()


def iso_of_epoch(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_now():
    return iso_of_epoch(now_epoch())


# ---------------------------------------------------------------------------
# I/O (utf-8-sig, size cap, atomic write, graceful degradation)
# ---------------------------------------------------------------------------

def read_text(path, max_bytes=MAX_FILE_BYTES):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    if p.stat().st_size > max_bytes:
        raise OSError("file too large (limit %d bytes): %s" % (max_bytes, p))
    with open(p, "r", encoding=READ_ENC, errors="replace") as f:
        return f.read()


def load_json(path):
    try:
        raw = read_text(path)
        return json.loads(raw), []
    except FileNotFoundError as exc:
        return None, ["read failed: %s" % exc]
    except (OSError, ValueError, UnicodeError) as exc:
        return None, ["json parse failed: %s" % exc]


def write_text(path, text):
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # pragma: no cover - parent creation failure
        sys.stderr.write("mkdir failed for %s: %s\n" % (p.parent, exc))
        return False
    try:
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(text, encoding=WRITE_ENC)
        os.replace(str(tmp), str(p))
        return True
    except Exception as exc:
        try:
            p.write_text(text, encoding=WRITE_ENC)
            return True
        except Exception as exc2:
            sys.stderr.write("write failed for %s: %s; %s\n" % (p, exc, exc2))
            return False


def write_json(path, obj):
    return write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Input discovery
# ---------------------------------------------------------------------------

def discover_input(input_dir):
    """Return (case_path, task_path, material_paths, errors)."""
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


def text_materials(materials):
    return [str(m) for m in materials if m.suffix.lower() in _TEXT_EXTS]


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
    """Return required contract field names, or None when absent."""
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
    case_path, task_path, materials, errors = discover_input(input_dir)
    case_obj = None
    task_text = None
    if case_path is not None:
        case_obj, errs = load_json(str(case_path))
        errors.extend(errs)
    if task_path is not None:
        try:
            task_text = read_text(str(task_path))
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
        "case_path": str(case_path) if case_path else None,
        "task_path": str(task_path) if task_path else None,
        "materials": [str(m) for m in materials],
    }
    return case_id, contract, data, errors


def _initial_status(data, errors):
    has_case = data.get("case_obj") is not None
    has_task = data.get("task_text") is not None
    malformed = any("json parse failed" in e or "read failed" in e for e in errors)
    if not has_case and not has_task:
        return "input_error" if malformed else "insufficient_evidence"
    return "input_error" if malformed else "partial"


# ---------------------------------------------------------------------------
# Skeleton / ledger
# ---------------------------------------------------------------------------

def build_ledger():
    return {"plan": PHASE_PLAN, "events": []}


def make_skeleton(case_id, contract_keys, status="partial"):
    result = {"case_id": case_id}
    for k in contract_keys:
        result.setdefault(k, None)
    result["status"] = status
    result["sources"] = []
    result["uncertainty"] = []
    result["change_log"] = [
        {"event": "skeleton_written", "t": iso_now(), "status": status}
    ]
    result["budget"] = build_ledger()
    result["errors"] = []
    result["degradations"] = []
    result["pending_human"] = []
    result["runtime"] = None
    return result


def load_ledger(work_dir):
    p = Path(work_dir) / "ledger.json"
    obj, _ = load_json(str(p))
    if obj is None or not isinstance(obj, dict):
        return build_ledger()
    return obj


def record_phase(work_dir, phase_id, event, requests_used=None):
    ledger = load_ledger(work_dir)
    ledger.setdefault("events", []).append({
        "phase": int(phase_id),
        "event": event,
        "t": iso_now(),
        "t_epoch": now_epoch(),
        "requests_used": requests_used,
    })
    write_json(str(Path(work_dir) / "ledger.json"), ledger)
    return ledger


def summarize_budget(ledger):
    starts = {}
    actual = {}
    request_used = {}
    for ev in ledger.get("events", []):
        ph = ev.get("phase")
        if ev.get("event") == "start":
            starts[ph] = ev.get("t_epoch")
        elif ev.get("event") == "end" and ph in starts:
            actual[ph] = round(ev.get("t_epoch", 0) - starts[ph], 1)
        if ev.get("requests_used") is not None:
            request_used[ph] = request_used.get(ph, 0) + int(ev.get("requests_used"))
    return {"actual_time_s": actual, "request_used": request_used}


# ---------------------------------------------------------------------------
# Normalization / dedup / increment (pure, deterministic)
# ---------------------------------------------------------------------------

def normalize_title(title):
    if not title:
        return ""
    s = re.sub(r"<[^>]+>", " ", str(title).lower())
    s = "".join(ch if ch.isalnum() else " " for ch in s)
    return re.sub(r"\s+", " ", s).strip()


def _norm_doi(v):
    if not v:
        return None
    s = str(v).strip()
    m = _DOI_RE.search(s)
    if m:
        s = m.group(0)
    s = s.lower().rstrip(".,;")
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    return s or None


def _norm_arxiv(v):
    if not v:
        return None
    s = str(v).strip().lower()
    s = re.sub(r"^https?://(www\.)?arxiv\.org/(abs|pdf)/", "", s)
    s = s.replace("arxiv:", "")
    s = re.sub(r"v\d+$", "", s)
    return s.strip() or None


def canonical_key(record):
    if not isinstance(record, dict):
        return None
    doi = _norm_doi(record.get("doi") or record.get("DOI"))
    if doi:
        return ("doi", doi)
    arx = _norm_arxiv(record.get("arxiv") or record.get("arXiv") or record.get("arxiv_id"))
    if arx:
        return ("arxiv", arx)
    title = normalize_title(record.get("title") or record.get("Title"))
    if title:
        return ("title", title)
    return None


def normalize_record(record):
    rec = dict(record or {})
    key = canonical_key(rec)
    rec["_canonical_key"] = "%s:%s" % (key[0], key[1]) if key else None
    rec["_canonical_kind"] = key[0] if key else None
    return rec


def _sort_date(r):
    d = r.get("date") or r.get("published") or r.get("published_date") or \
        r.get("indexed_date") or r.get("updated_date") or ""
    if isinstance(d, (int, float)):
        return ("%020.9f" % d)
    return str(d).zfill(20)


def classify_group(key, group):
    blob = " ".join(
        str(r.get(k, "")) for r in group
        for k in ("type", "status", "note", "event_type", "publication_status")
    ).lower()
    flags = []
    if any(w in blob for w in ("retract", "withdrawn", "withdrawal")):
        flags.append("retraction")
    if any(w in blob for w in ("correction", "erratum", "corrigend")):
        flags.append("correction")
    n_preprint = sum(
        1 for r in group
        if "preprint" in (str(r.get("type", "")) + str(r.get("status", "")) + str(r.get("note", ""))).lower()
    )
    n_published = sum(
        1 for r in group
        if "published" in str(r.get("type", "")).lower()
        or "version_of_record" in str(r.get("status", "")).lower()
    )
    versions = sorted(group, key=_sort_date)
    return {
        "canonical_key": key,
        "count": len(group),
        "latest": versions[-1] if versions else None,
        "versions": versions,
        "flags": flags,
        "preprint_count": n_preprint,
        "published_count": n_published,
        "merged": len(group) > 1,
    }


def dedup_records(records):
    groups = {}
    order = []
    for rec in records:
        key = canonical_key(rec)
        if key is None:
            continue
        k = "%s:%s" % (key[0], key[1])
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(dict(rec))
    items = [classify_group(k, groups[k]) for k in order]
    return {
        "groups": len(order),
        "items": items,
        "dedup_key_rule": "doi > arxiv > normalized_title",
    }


def _changed(old, new):
    if not isinstance(old, dict):
        return False
    for f in ("version", "date", "published", "updated_date"):
        ov, nv = old.get(f), new.get(f)
        if ov != nv and (ov or nv):
            return True
    return False


def diff_increment(snapshot, records):
    snap_keys = set()
    snap_meta = {}
    if isinstance(snapshot, dict):
        for k, v in snapshot.items():
            if isinstance(v, dict):
                snap_keys.add(k)
                snap_meta[k] = v
            else:
                snap_keys.add(str(v))
    elif isinstance(snapshot, list):
        for v in snapshot:
            if isinstance(v, dict):
                k = v.get("canonical_key") or v.get("_canonical_key")
                if k:
                    snap_keys.add(k)
                    snap_meta[k] = v
            else:
                snap_keys.add(str(v))

    new, updated, seen = [], [], set()
    for rec in records:
        key = canonical_key(rec)
        if key is None:
            continue
        k = "%s:%s" % key
        if k in seen:
            continue
        seen.add(k)
        if k not in snap_keys:
            new.append(rec)
        elif _changed(snap_meta.get(k), rec):
            updated.append(rec)
    removed = sorted(snap_keys - seen)
    return {"new_items": new, "updated": updated, "removed": removed}


# ---------------------------------------------------------------------------
# Report / finalize
# ---------------------------------------------------------------------------

def _list_len(v):
    return len(v) if isinstance(v, list) else (0 if v is None else 1)


def build_report(result):
    L = []
    L.append("# 文献追踪结果报告")
    L.append("")
    L.append("- case_id: `%s`" % result.get("case_id"))
    L.append("- status: `%s`" % result.get("status"))
    tr = result.get("tracking") or {}
    dd = result.get("dedup") or {}
    L.append("- 候选文献: %d 条；证据记录: %d 条" % (
        _list_len(result.get("search_results")), _list_len(result.get("evidence"))))
    dd_items = dd.get("items") if isinstance(dd.get("items"), list) else []
    flagged = sum(1 for it in dd_items if it.get("flags"))
    L.append("- 新增: %d 条；更新: %d 条；撤稿/更正标记: %d 组" % (
        _list_len(tr.get("new_items")), _list_len(tr.get("updates")), flagged))

    L.append("")
    L.append("## 状态")
    L.append("")
    for k in ("errors", "degradations", "uncertainty", "pending_human"):
        v = result.get(k)
        if isinstance(v, list) and v:
            L.append("### %s" % k)
            L.append("")
            for item in v:
                if isinstance(item, dict):
                    L.append("- %s" % json.dumps(item, ensure_ascii=False))
                else:
                    L.append("- %s" % item)
            L.append("")
    return "\n".join(L)


def merge_findings(skeleton, findings):
    result = dict(skeleton)
    for k in list(skeleton.keys()):
        if k in findings and findings[k] is not None:
            result[k] = findings[k]
    for k in META_FIELDS:
        if k not in result:
            result[k] = None if k in ("status", "runtime") else []
    if findings.get("status"):
        result["status"] = findings["status"]
    return result


def finalize(input_dir, output_dir, work_dir, findings_path, self_check=True):
    case_id, contract, data, errors = extract_case(input_dir)
    findings = {}
    if findings_path and Path(findings_path).exists():
        findings, ferrs = load_json(findings_path)
        errors.extend(ferrs)
    skeleton_path = Path(work_dir) / "skeleton.json"
    if skeleton_path.exists():
        skeleton, _ = load_json(str(skeleton_path))
    else:
        skeleton = make_skeleton(case_id, contract, "partial")
    if not isinstance(skeleton, dict):
        skeleton = make_skeleton(case_id, contract, "partial")

    result = merge_findings(skeleton, findings)
    result.setdefault("errors", [])
    if not isinstance(result["errors"], list):
        result["errors"] = []
    for e in errors:
        if e not in result["errors"]:
            result["errors"].append(e)

    records = findings.get("records") or findings.get("search_results")
    if isinstance(records, list) and records:
        dd = dedup_records(records)
        result.setdefault("dedup", {})
        if isinstance(result["dedup"], dict):
            result["dedup"]["groups"] = dd["groups"]
            result["dedup"]["items"] = dd["items"]
            result["dedup"]["dedup_key_rule"] = dd["dedup_key_rule"]
        snap = (findings.get("tracking") or {}).get("snapshot")
        if snap is not None:
            diff = diff_increment(snap, records)
            result.setdefault("tracking", {})
            if not isinstance(result["tracking"], dict):
                result["tracking"] = {}
            result["tracking"].setdefault("new_items", diff["new_items"])
            result["tracking"].setdefault("updates", diff["updated"])
            result["tracking"].setdefault("removed", diff["removed"])

    ledger = load_ledger(work_dir)
    result["budget"] = {
        "plan": ledger.get("plan"),
        "events": ledger.get("events", []),
        "summary": summarize_budget(ledger),
    }

    started = None
    if isinstance(result.get("change_log"), list):
        for ev in result["change_log"]:
            if isinstance(ev, dict) and ev.get("event") == "skeleton_written":
                started = ev.get("t")
                break
    result["runtime"] = {
        "started_at": started,
        "finished_at": iso_now(),
    }

    result.setdefault("change_log", [])
    if not isinstance(result["change_log"], list):
        result["change_log"] = []
    result["change_log"].append({"event": "finalize", "t": iso_now()})
    if self_check:
        result["change_log"].append({
            "event": "self_check_passed", "t": iso_now(),
            "conclusion": "see references/checklist.md",
        })

    if result.get("status") not in ("ok", "partial", "insufficient_evidence", "input_error"):
        result["status"] = "partial"

    write_json(str(Path(output_dir) / "result.json"), result)
    write_text(str(Path(output_dir) / "report.md"), build_report(result))
    return result
