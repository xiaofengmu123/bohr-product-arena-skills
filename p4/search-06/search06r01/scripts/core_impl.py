# -*- coding: utf-8 -*-
"""core_impl.py — deterministic core for the literature-tracking Skill.

Standard library only. No network access. This module never fabricates
literature records: it only discovers inputs, normalizes, deduplicates,
classifies, validates evidence and assembles data supplied by the agent or
the case materials. Missing input yields honest statuses
("insufficient_evidence" / "input_error"), never invented records.
"""

import hashlib
import json
import re
import time
from pathlib import Path

MAX_FILE_BYTES = 2 * 1024 * 1024  # single-file cap, 2MB

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

DEFAULT_CONTRACT_KEYS = ["search_results", "ranking", "evidence", "status_notes"]

# Always-present result fields beyond case_id and the output contract.
META_FIELDS = [
    "status",
    "sources",
    "uncertainty",
    "evidence_map",
    "change_log",
    "errors",
    "degradations",
    "pending_human",
    "runtime",
]

VALID_STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}
VALID_CONFIDENCE = {"established", "inferred", "uncertain"}

_CASE_ID_HINTS = ("case_id", "id", "challenge_id", "armiId")
_CASE_ID_RE = re.compile(r"challenge_id[:\s]*`([^`]+)`|challenge_id[:\s]*([\w.-]+)")

_CONTRACT_KEY_HINTS = (
    "output_contract",
    "contract",
    "output_schema",
    "result_schema",
    "output",
)


# ---------------------------------------------------------------------------
# Time & IO
# ---------------------------------------------------------------------------

def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)
    return path


def read_text(path):
    path = Path(path)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise OSError("file exceeds %d bytes: %s" % (MAX_FILE_BYTES, path))
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def load_json(path, errors=None):
    if errors is None:
        errors = []
    try:
        return json.loads(read_text(path)), errors
    except Exception as exc:
        errors.append("json parse failed %s: %s" % (path, exc))
        return None, errors


def dump_json(path, obj):
    text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path is None:
        return text
    Path(path).write_text(text, encoding="utf-8")
    return text


def write_text(path, text):
    Path(path).write_text(text, encoding="utf-8")
    return path


def write_result(output_dir, result):
    ensure_dir(output_dir)
    return dump_json(str(Path(output_dir) / "result.json"), result)


def file_info(path):
    p = Path(path)
    size = p.stat().st_size
    data = p.read_bytes() if size <= MAX_FILE_BYTES else b""
    return {
        "path": str(p),
        "bytes": size,
        "sha1": hashlib.sha1(data).hexdigest() if data else None,
        "truncated": size > MAX_FILE_BYTES,
    }


# ---------------------------------------------------------------------------
# Input discovery & contract extraction
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


def _case_id_from_obj(obj):
    if isinstance(obj, dict):
        for k in _CASE_ID_HINTS:
            if k in obj and obj[k] not in (None, ""):
                return str(obj[k])
        for k in ("task", "spec", "input", "case"):
            v = _case_id_from_obj(obj.get(k))
            if v:
                return v
    return None


def find_output_contract(obj):
    """Return a list of required field names, or None when absent."""
    if isinstance(obj, dict):
        for k in _CONTRACT_KEY_HINTS:
            v = obj.get(k)
            if isinstance(v, dict):
                return list(v.keys())
            if isinstance(v, list):
                return [str(x) for x in v if isinstance(x, str)]
            if isinstance(v, str):
                return [v]
        for k in ("task", "spec", "input", "case"):
            found = find_output_contract(obj.get(k))
            if found:
                return found
    return None


def extract_case(input_dir):
    """Read case.json + task.md. Return (case_id, contract_keys, data, errors)."""
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
    if not contract:
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
    malformed = any(
        ("parse failed" in e) or ("read failed" in e) or ("exceeds" in e)
        for e in errors
    )
    if not has_case and not has_task:
        return "input_error" if malformed else "insufficient_evidence"
    return "input_error" if malformed else "partial"


# ---------------------------------------------------------------------------
# Skeleton assembly
# ---------------------------------------------------------------------------

def make_skeleton(case_id, contract_keys, status="partial"):
    result = {"case_id": case_id}
    for k in contract_keys:
        result.setdefault(k, None)
    result["status"] = status
    result["sources"] = []
    result["uncertainty"] = []
    result["evidence_map"] = {}
    result["change_log"] = [
        {
            "event": "skeleton_written",
            "time": utc_now(),
            "detail": {"status": status, "contract_keys": contract_keys},
        }
    ]
    result["errors"] = []
    result["degradations"] = []
    result["pending_human"] = []
    result["runtime"] = utc_now()
    return result


# ---------------------------------------------------------------------------
# Normalization & dedup
# ---------------------------------------------------------------------------

def normalize_doi(value):
    if value is None:
        return None
    s = str(value).strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = s.strip().strip(".,;")
    return s or None


def normalize_arxiv(value):
    if value is None:
        return None
    s = str(value).strip().lower()
    s = re.sub(r"^https?://(www\.)?arxiv\.org/(abs|pdf)/", "", s)
    s = re.sub(r"^arxiv:", "", s)
    s = re.sub(r"v\d+$", "", s)
    s = s.strip().strip(".,;")
    return s or None


def normalize_title(value):
    if value is None:
        return None
    s = str(value).strip().lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def canonical_id(record):
    """Stable key: DOI > arXiv > normalized title."""
    rec = record or {}
    doi = normalize_doi(rec.get("doi"))
    if doi:
        return "doi:" + doi
    arxiv = normalize_arxiv(rec.get("arxiv"))
    if arxiv:
        return "arxiv:" + arxiv
    title = normalize_title(rec.get("title"))
    if title:
        return "title:" + title
    return None


def _parse_date(value):
    if not value:
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(value))
    return m.group(0) if m else None


def first_nonnull(seq):
    for x in seq:
        if x:
            return x
    return None


def _is_retraction(m):
    t = str(m.get("type") or "").lower()
    st = str(m.get("status") or "").lower()
    return t in ("retraction", "retracted") or st == "retracted" or "retract" in t


def _is_correction(m):
    t = str(m.get("type") or "").lower()
    return t in ("correction", "corrigendum", "erratum", "corrected")


def _is_published(m):
    t = str(m.get("type") or "").lower()
    return t == "published" or (bool(m.get("doi")) and bool(m.get("journal")))


def _is_preprint(m):
    t = str(m.get("type") or "").lower()
    return t == "preprint" or (bool(m.get("arxiv")) and not bool(m.get("doi")))


def _record_type(m):
    if _is_retraction(m):
        return "retraction"
    if _is_correction(m):
        return "correction"
    if _is_published(m):
        return "published"
    if _is_preprint(m):
        return "preprint"
    return "unknown"


def _member_sort_key(m):
    d = _parse_date(m.get("published_date") or m.get("date"))
    v = str(m.get("version") or "")
    return (d or "9999-99-99", v)


def classify_group(cid, members):
    members = sorted(members, key=_member_sort_key)
    latest = members[-1]
    has_retraction = any(_is_retraction(m) for m in members)
    has_correction = any(_is_correction(m) for m in members)
    has_published = any(_is_published(m) for m in members)
    has_preprint = any(_is_preprint(m) for m in members)

    versions = [
        {
            "record_id": m.get("record_id"),
            "index": m.get("_index"),
            "type": _record_type(m),
            "doi": m.get("doi"),
            "arxiv": m.get("arxiv"),
            "journal": m.get("journal"),
            "published_date": _parse_date(m.get("published_date") or m.get("date")),
            "version": m.get("version"),
            "source": m.get("source"),
        }
        for m in members
    ]

    kind = (
        "retraction" if has_retraction else
        "correction" if has_correction else
        "published" if has_published else
        "preprint" if has_preprint else
        "unknown"
    )

    return {
        "canonical_id": cid,
        "canonical_title": latest.get("title") or members[0].get("title"),
        "doi": first_nonnull(m.get("doi") for m in members),
        "arxiv": first_nonnull(m.get("arxiv") for m in members),
        "member_count": len(members),
        "has_preprint": has_preprint,
        "has_published": has_published,
        "has_correction": has_correction,
        "has_retraction": has_retraction,
        "kind": kind,
        "latest": {
            "record_id": latest.get("record_id"),
            "index": latest.get("_index"),
            "published_date": _parse_date(latest.get("published_date") or latest.get("date")),
            "version": latest.get("version"),
            "type": _record_type(latest),
        },
        "versions": versions,
    }


def dedup_records(records):
    records = list(records or [])
    groups = {}
    order = []
    for i, rec in enumerate(records):
        cid = canonical_id(rec)
        if cid is None:
            cid = "record:%d" % i
        rec2 = dict(rec)
        rec2.setdefault("_index", i)
        rec2.setdefault("_canonical_id", cid)
        if cid not in groups:
            groups[cid] = []
            order.append(cid)
        groups[cid].append(rec2)

    result_groups = []
    duplicate_count = 0
    unkeyed = 0
    for cid in order:
        members = groups[cid]
        if len(members) > 1:
            duplicate_count += len(members) - 1
        if cid.startswith("record:"):
            unkeyed += 1
        result_groups.append(classify_group(cid, members))
    result_groups.sort(key=lambda g: g["canonical_id"])

    return {
        "groups": result_groups,
        "canonical_id_count": len(result_groups),
        "record_count": len(records),
        "duplicate_count": duplicate_count,
        "unkeyed_count": unkeyed,
        "suppressed_duplicates": duplicate_count,
    }


def classify_increment(dedup_result, snapshot_ids):
    """Deterministic default incremental labels; agent findings are authoritative."""
    snapshot_ids = set(snapshot_ids or [])
    out = []
    for g in dedup_result.get("groups", []):
        cid = g["canonical_id"]
        known = cid in snapshot_ids
        kind = "unchanged" if known else "new"
        if g.get("has_retraction"):
            kind = "retraction"
        elif g.get("has_correction"):
            kind = "correction"
        elif known and g.get("member_count", 1) > 1:
            kind = "updated"
        out.append(dict(g, incremental_kind=kind, in_snapshot=known))
    return out


# ---------------------------------------------------------------------------
# Evidence-map validation (the evidence-tracing focus)
# ---------------------------------------------------------------------------

def validate_evidence_map(evidence_map):
    """Return a list of issues; empty list means the map satisfies the contract.

    Contract per claim: locator (precise position) + verbatim quote are
    mandatory. Absence / not_reported claims may use an empty quote only when
    a `basis` explains why no verbatim text exists. Conflicts must each carry
    locator + quote. Optional quote_hash is checked when present.
    """
    issues = []
    entries = evidence_map or {}
    if not isinstance(entries, dict):
        return ["evidence_map must be an object keyed by claim_id"]
    for cid, e in entries.items():
        if not isinstance(e, dict):
            issues.append("evidence[%s] is not an object" % cid)
            continue
        claim = (e.get("claim") or "").strip()
        locator = (e.get("locator") or "").strip()
        quote = e.get("quote")
        basis = (e.get("basis") or "").strip()
        kind = str(e.get("kind") or "").lower()
        if not claim:
            issues.append("evidence[%s] missing claim" % cid)
        if not locator:
            issues.append("evidence[%s] missing locator" % cid)
        if quote is None or (isinstance(quote, str) and not quote.strip()):
            if kind not in ("absence", "not_reported") or not basis:
                issues.append("evidence[%s] missing verbatim quote" % cid)
        elif isinstance(quote, str) and e.get("quote_hash"):
            actual = hashlib.sha1(quote.encode("utf-8")).hexdigest()
            if actual != str(e.get("quote_hash")):
                issues.append("evidence[%s] quote_hash mismatch" % cid)
        if "confidence" in e and e.get("confidence") not in VALID_CONFIDENCE:
            issues.append("evidence[%s] invalid confidence" % cid)
        for ci, c in enumerate(e.get("conflicts") or []):
            if not isinstance(c, dict) or not (c.get("locator") and c.get("quote")):
                issues.append("evidence[%s].conflicts[%d] missing locator/quote" % (cid, ci))
    return issues


# ---------------------------------------------------------------------------
# Result / report assembly
# ---------------------------------------------------------------------------

def _change_key(entry):
    if isinstance(entry, dict):
        return (entry.get("event") or "", entry.get("time") or "")
    return str(entry)


def merge_result(case_id, contract_keys, findings, errors=None, degradations=None,
                 prev_change_log=None):
    findings = findings or {}
    result = {"case_id": case_id}
    oc = findings.get("output_contract") if isinstance(findings.get("output_contract"), dict) else {}
    for k in contract_keys:
        if k in findings:
            result[k] = findings[k]
        elif k in oc:
            result[k] = oc[k]
        else:
            result[k] = None

    status = findings.get("status") or "partial"
    if status not in VALID_STATUSES:
        status = "partial"
    result["status"] = status
    result["sources"] = findings.get("sources") if isinstance(findings.get("sources"), list) else []
    result["uncertainty"] = findings.get("uncertainty") if isinstance(findings.get("uncertainty"), list) else []
    result["evidence_map"] = findings.get("evidence_map") if isinstance(findings.get("evidence_map"), dict) else {}
    result["errors"] = list(errors or []) + list(findings.get("errors") or [])
    result["degradations"] = list(degradations or []) + list(findings.get("degradations") or [])
    result["pending_human"] = findings.get("pending_human") if isinstance(findings.get("pending_human"), list) else []
    result["runtime"] = findings.get("runtime") or utc_now()

    change_log = []
    seen = set()
    for entry in list(prev_change_log or []) + list(findings.get("change_log") or []):
        key = _change_key(entry)
        if key not in seen:
            change_log.append(entry)
            seen.add(key)
    result["change_log"] = change_log
    return result


def ensure_change_log(change_log, event, time=None, **fields):
    change_log = list(change_log or [])
    if not any(isinstance(e, dict) and e.get("event") == event for e in change_log):
        entry = {"event": event, "time": time or utc_now()}
        if fields:
            entry["detail"] = fields
        change_log.append(entry)
    return change_log


def _fmt_quote(q):
    q = str(q)
    if len(q) > 200:
        q = q[:200] + "\u2026"
    return q.replace("\n", " ")


def _fmt_item(x):
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        return json.dumps(x, ensure_ascii=False)
    return str(x)


def render_report(result):
    L = []
    add = L.append
    add("# 文献追踪结果报告")
    add("")
    add("- case_id: `%s`" % result.get("case_id"))
    add("- status: `%s`" % result.get("status"))
    add("- runtime: %s" % (result.get("runtime") or ""))
    add("")
    for key in ("search_results", "ranking", "evidence"):
        if key in result:
            v = result.get(key)
            n = len(v) if isinstance(v, list) else (0 if v is None else 1)
            add("- `%s`: %d 条" % (key, n))
    add("")
    add("## 证据映射（evidence_map）")
    em = result.get("evidence_map") or {}
    add("- 结论条目数: %d" % len(em))
    for cid, e in em.items():
        if not isinstance(e, dict):
            add("- `%s`: %s" % (cid, _fmt_item(e)))
            continue
        add("- **%s** `[%s]` locator=%s" % (cid, e.get("kind", ""), e.get("locator", "")))
        if e.get("quote"):
            add("  - quote: %s" % _fmt_quote(e.get("quote")))
        if e.get("conflicts"):
            add("  - 冲突 %d 处（原文见 result.json）" % len(e["conflicts"]))
    add("")
    add("## 状态说明")
    add("- sources: %d" % len(result.get("sources") or []))
    add("- uncertainty: %d" % len(result.get("uncertainty") or []))
    add("- pending_human: %d" % len(result.get("pending_human") or []))
    add("- errors: %d" % len(result.get("errors") or []))
    add("- degradations: %d" % len(result.get("degradations") or []))
    for u in result.get("uncertainty") or []:
        add("- uncertainty: %s" % _fmt_item(u))
    for p in result.get("pending_human") or []:
        add("- pending_human: %s" % _fmt_item(p))
    for e in result.get("errors") or []:
        add("- error: %s" % _fmt_item(e))
    add("")
    add("## 变更记录（change_log）")
    for e in result.get("change_log") or []:
        if isinstance(e, dict):
            add("- %s %s %s" % (e.get("time", ""), e.get("event", ""), _fmt_item(e.get("detail", ""))))
        else:
            add("- %s" % e)
    add("")
    add("> 本报告与 result.json 一致；逐字引用与定位以 result.json 的 evidence_map 为准。")
    return "\n".join(L) + "\n"
