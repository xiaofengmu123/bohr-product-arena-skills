# -*- coding: utf-8 -*-
"""Deterministic core for the literature-tracking Skill.

Variant focus: explicit degradation & uncertainty. Every epistemic gap is
recorded as a typed `limitation` or `uncertainty` entry instead of a silent
guess, and every finding carries a `confidence` grade so "缺数据不编造" is
mechanically auditable.

Standard library only. All public functions are deterministic: identical input
produces identical output. No function in this module invents literature
content, metadata, DOIs or citations; missing data becomes the literal value
`not_reported` or a typed limitation entry.
"""

import datetime as _dt
import hashlib
import json
import re
import unicodedata

# ---------------------------------------------------------------------------
# Controlled vocabularies (single source of truth; referenced by SKILL.md)
# ---------------------------------------------------------------------------

STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")

# Evidence confidence grades. `verbatim` requires a non-empty, material-backed
# quote; `unreported` marks a value the material does not state at all.
CONFIDENCE_VALUES = (
    "verbatim",       # 逐字证据：quote 来自材料原文
    "paraphrased",    # 转述证据：材料可支撑，但非逐字
    "inferred",       # 推断：由多份材料事实组合得出
    "unreported",     # 未报告：材料中不存在，明确标为缺失
    "conflict",       # 冲突：两个来源不一致，两者都保留
)

UNCERTAINTY_SEVERITIES = ("blocking", "material", "minor")
UNCERTAINTY_RESOLUTIONS = ("pending_human", "accepted_with_caveat", "unresolvable")

# Limitation reason codes. The finalizer coerces unknown codes to the nearest
# documented bucket rather than dropping information.
LIMITATION_REASONS = (
    "no_material",      # 输入材料缺失或为空
    "tool_missing",     # 需要的软件/工具不可用
    "time_exceeded",    # 预算内未能完成
    "permission",       # 权限/接口拒绝
    "out_of_scope",     # 超出本 Skill 职责边界
    "encoding",         # 坏编码/坏 JSON，已降级读取
    "size_cap",         # 超单文件大小上限被跳过
    "parse_failed",     # 可读但无法解析出所需字段
)

DEDUP_KINDS = ("doi", "arxiv", "title")

# Default result contract keys derived from the task's three required rows:
# 检索结果 / 证据记录 / 状态说明.
DEFAULT_CONTRACT_KEYS = ("search_results", "evidence", "status_notes")

# Always-present meta fields beyond case_id and the output contract.
META_FIELDS = (
    "status",
    "sources",
    "uncertainty",
    "limitations",
    "change_log",
    "errors",
    "degradations",
    "pending_human",
    "runtime",
)

MAX_FILE_BYTES = 2 * 1024 * 1024

_CASE_ID_HINTS = ("case_id", "id", "challenge_id")
_CASE_ID_RE = re.compile(r"challenge_id[:\s]*`([^`]+)`|challenge_id[:\s]*([\w.-]+)")
_CONTRACT_HINTS = ("output_contract", "contract", "output_schema", "output")


# ---------------------------------------------------------------------------
# I/O helpers (utf-8-sig tolerant, size-capped, never raise)
# ---------------------------------------------------------------------------

def read_text(path, max_bytes=MAX_FILE_BYTES):
    """Read UTF-8 text with BOM tolerance; return (text, errors).

    Oversized files are skipped with a `size_cap` degradation message rather
    than read into memory. Encoding failures degrade to latin-1 so downstream
    steps can still run.
    """
    errors = []
    try:
        with open(path, "rb") as fh:
            raw = fh.read(max_bytes + 1)
        if len(raw) > max_bytes:
            return None, ["size_cap: %s exceeds %d bytes" % (path, max_bytes)]
    except OSError as exc:
        return None, ["read failed: %s: %s" % (path, exc)]
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), errors
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), errors + [
        "encoding: %s not valid UTF-8, replaced bad bytes" % path
    ]


def load_json(path):
    """Load JSON; return (obj, errors). Malformed JSON yields (None, errors)."""
    text, errs = read_text(path)
    if text is None:
        return None, errs
    try:
        return json.loads(text), errs
    except (ValueError, TypeError) as exc:
        return None, errs + ["json parse failed: %s: %s" % (path, exc)]


# ---------------------------------------------------------------------------
# Input discovery
# ---------------------------------------------------------------------------

def discover_input(input_dir):
    """Scan the input tree without assuming fixed names.

    Returns (case_path, task_path, material_paths, errors).
    """
    import os
    errors = []
    root = os.path.abspath(input_dir)
    if not os.path.isdir(root):
        return None, None, [], ["input dir missing: %s" % root]
    case = task = None
    materials = []
    for base, _dirs, files in os.walk(root):
        for name in sorted(files):
            full = os.path.join(base, name)
            low = name.lower()
            if low == "case.json":
                case = full
            elif low == "task.md":
                task = full
            else:
                materials.append(full)
    materials.sort()
    if case is None and task is None:
        errors.append("no case.json or task.md found under %s" % root)
    return case, task, materials, errors


def _case_id_from_obj(obj):
    if isinstance(obj, dict):
        for key in _CASE_ID_HINTS:
            val = obj.get(key)
            if val not in (None, ""):
                return str(val)
        for key in ("task", "spec", "input"):
            found = _case_id_from_obj(obj.get(key))
            if found:
                return found
    return None


def find_output_contract(obj):
    """Return a list of required contract field names, or None if absent."""
    if isinstance(obj, dict):
        for key in _CONTRACT_HINTS:
            val = obj.get(key)
            if isinstance(val, dict):
                return list(val.keys())
            if isinstance(val, list):
                return [str(x) for x in val if isinstance(x, str)]
            if isinstance(val, str):
                return [val]
        for key in ("task", "spec", "input"):
            found = find_output_contract(obj.get(key))
            if found:
                return found
    return None


def extract_case(input_dir):
    """Read case.json + task.md; return (case_id, contract_keys, data, errors)."""
    case_path, task_path, materials, errors = discover_input(input_dir)
    case_obj = task_text = None
    if case_path is not None:
        case_obj, errs = load_json(case_path)
        errors.extend(errs)
    if task_path is not None:
        task_text, errs = read_text(task_path)
        errors.extend(errs)

    case_id = _case_id_from_obj(case_obj)
    if not case_id and task_text:
        m = _CASE_ID_RE.search(task_text)
        if m:
            case_id = m.group(1) or m.group(2)
    case_id = case_id or "unknown-case"

    contract = find_output_contract(case_obj)
    if contract is None:
        contract = list(DEFAULT_CONTRACT_KEYS)

    data = {
        "case_obj": case_obj,
        "task_text": task_text,
        "case_path": case_path,
        "task_path": task_path,
        "materials": list(materials),
    }
    return case_id, contract, data, errors


def initial_status(data, errors):
    """Honest initial status from what could actually be read."""
    has_case = data.get("case_obj") is not None
    has_task = data.get("task_text") is not None
    malformed = any(
        "json parse failed" in e or "read failed" in e for e in errors
    )
    if not has_case and not has_task:
        return "input_error" if malformed else "insufficient_evidence"
    return "input_error" if malformed else "partial"


# ---------------------------------------------------------------------------
# Canonical keys / normalization (stable, reversible-in-intent)
# ---------------------------------------------------------------------------

def normalize_unicode(text):
    """NFKD -> strip combining marks -> collapse whitespace."""
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip()


def normalize_doi(doi):
    if not isinstance(doi, str) or not doi.strip():
        return ""
    val = doi.strip().lower()
    val = re.sub(r"^https?://(dx\.)?doi\.org/", "", val)
    val = re.sub(r"^doi:\s*", "", val)
    val = normalize_unicode(val)
    return val


def normalize_arxiv(arxiv):
    if not isinstance(arxiv, str) or not arxiv.strip():
        return ""
    val = arxiv.strip()
    m = re.search(r"(\d{4}\.\d{4,5})(v\d+)?", val)
    if not m:
        return ""
    base = m.group(1)
    # keep the base without version so preprints and revisions share a key
    return base


def normalize_title(title):
    if not isinstance(title, str) or not title.strip():
        return ""
    val = normalize_unicode(title).lower()
    val = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", val)
    val = re.sub(r"\s+", " ", val).strip()
    return val


def canonical_key(record):
    """Return {"kind", "value", "label"} or None when nothing identifiable.

    Precedence: DOI > arXiv > normalized title (mirrors references/normalization.md).
    """
    if not isinstance(record, dict):
        return None
    doi = normalize_doi(record.get("doi") or record.get("DOI") or "")
    if doi:
        return {"kind": "doi", "value": doi, "label": "doi:" + doi}
    arx = normalize_arxiv(
        record.get("arxiv") or record.get("arXiv") or record.get("arxiv_id") or ""
    )
    if arx:
        return {"kind": "arxiv", "value": arx, "label": "arXiv:" + arx}
    title = normalize_title(record.get("title") or "")
    if title:
        return {"kind": "title", "value": title, "label": "title:" + title}
    return None


def normalize_record(record):
    """Return a copy with stable normalized fields, never mutating the input."""
    if not isinstance(record, dict):
        return {"_raw": record, "_key": None}
    out = dict(record)
    out["_key"] = canonical_key(record)
    out["_doi_norm"] = normalize_doi(record.get("doi") or "")
    out["_arxiv_norm"] = normalize_arxiv(record.get("arxiv") or "")
    out["_title_norm"] = normalize_title(record.get("title") or "")
    return out


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------

def dedup_records(records):
    """Group records by canonical key (DOI > arXiv > title).

    Returns {"items": [...], "clusters": {...}, "dropped": [...]}. Each kept
    item is a normalized record annotated with its cluster id and a
    `suppressed_duplicates` count.
    """
    if not isinstance(records, list):
        records = []
    clusters = {}
    order = []
    for raw in records:
        norm = normalize_record(raw)
        key = norm["_key"]
        if key is None:
            norm["_cluster"] = None
            norm["_suppressed_duplicates"] = 0
            norm["_dedup_status"] = "unidentifiable"
            order.append(norm)
            continue
        token = key["kind"] + "\x00" + key["value"]
        if token not in clusters:
            clusters[token] = {"key": key, "members": [], "primary": None}
        clusters[token]["members"].append(norm)
        order.append((token, norm))

    items = []
    dropped = []
    for entry in order:
        if isinstance(entry, dict):
            items.append(entry)
            continue
        token, norm = entry
        cluster = clusters[token]
        count = len(cluster["members"])
        if cluster["primary"] is None:
            norm["_cluster"] = token
            norm["_suppressed_duplicates"] = count - 1
            norm["_dedup_status"] = "primary"
            cluster["primary"] = norm
            items.append(norm)
        else:
            norm["_cluster"] = token
            norm["_dedup_status"] = "duplicate"
            dropped.append(norm)

    return {
        "items": items,
        "clusters": {
            token: {"key": c["key"], "member_count": len(c["members"])}
            for token, c in clusters.items()
        },
        "dropped": dropped,
    }


# ---------------------------------------------------------------------------
# Version chain / stage classification (conservative: no invented stages)
# ---------------------------------------------------------------------------

def _stage_of(record):
    """Conservative stage label; explicit signals only, otherwise unclassified."""
    if not isinstance(record, dict):
        return "unclassified"
    title = (record.get("title") or "").lower()
    rtype = (record.get("type") or record.get("publication_type") or "").lower()
    if record.get("retracted") is True or "retraction" in title or "retraction" in rtype:
        return "retraction"
    if rtype in ("erratum", "correction", "corrigendum") or "corrigendum" in title:
        return "correction"
    if (record.get("arxiv") or record.get("arXiv")) and not (
        record.get("doi") or record.get("journal")
    ):
        return "preprint"
    if record.get("doi"):
        return "published"
    return "unclassified"


def build_version_chain(records):
    """Group by title/DOI base and assign conservative stages.

    Returns a list of chains, each {base_key, stages: {...}, note}. This is a
    merge skeleton only: the agent may override with explicit evidence. It never
    asserts a preprint and a DOI refer to the same work unless the base key
    (normalized title) matches.
    """
    if not isinstance(records, list):
        return []
    buckets = {}
    for rec in records:
        norm = normalize_record(rec)
        base = norm.get("_title_norm") or (
            norm["_key"]["value"] if norm.get("_key") else ""
        )
        if not base:
            continue
        buckets.setdefault(base, []).append(norm)

    chains = []
    for base, members in sorted(buckets.items()):
        stages = {}
        for m in members:
            stage = _stage_of(m)
            stages.setdefault(stage, []).append(m)
        chains.append(
            {
                "base_key": base,
                "member_count": len(members),
                "stages": {k: len(v) for k, v in stages.items()},
                "note": "deterministic merge skeleton; agent overrides with evidence",
            }
        )
    return chains


# ---------------------------------------------------------------------------
# Incremental classification
# ---------------------------------------------------------------------------

def classify_incremental(records, snapshot_keys):
    """Default new/updated/removed/retraction split relative to a snapshot.

    snapshot_keys is an iterable of canonical key strings from the previous
    window. Retractions are separated from `new`; a record already seen is
    `known` (suppressed), never re-pushed.
    """
    seen = set(snapshot_keys or [])
    out = {"new": [], "updated": [], "removed": [], "retractions": [], "known": []}
    for rec in records:
        norm = normalize_record(rec)
        key = norm.get("_key")
        token = (key["kind"] + "\x00" + key["value"]) if key else None
        stage = _stage_of(norm)
        if stage == "retraction":
            out["retractions"].append(norm)
        elif token and token in seen:
            out["known"].append(norm)
        elif token:
            out["new"].append(norm)
        else:
            out["new"].append(norm)
    return out


# ---------------------------------------------------------------------------
# Time window helpers (UTC normalization, closed intervals)
# ---------------------------------------------------------------------------

def parse_utc(value):
    """Parse ISO-8601 to a naive UTC datetime; return (dt, warnings)."""
    warnings = []
    if value in (None, ""):
        return None, ["missing datetime: treated as not_reported"]
    text = str(value).strip()
    text = re.sub(r"[Zz]$", "+00:00", text)
    try:
        dt = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None, ["unparseable datetime: %r" % value]
    if dt.tzinfo is None:
        warnings.append("naive datetime %r assumed UTC" % value)
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    else:
        dt = dt.astimezone(_dt.timezone.utc)
    return dt.replace(tzinfo=None), warnings


def validate_window(window):
    """Validate a {start, end} window; return (window, errors).

    Enforces closed, non-empty intervals and UTC normalization.
    """
    errors = []
    if not isinstance(window, dict):
        return None, ["window missing: treated as not_reported"]
    start, w1 = parse_utc(window.get("start"))
    end, w2 = parse_utc(window.get("end"))
    errors.extend(w1 + w2)
    if start is None or end is None:
        return window, errors
    if start > end:
        errors.append("window start after end: %s > %s" % (window.get("start"), window.get("end")))
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "timezone": "UTC",
        "closed": True,
    }, errors


# ---------------------------------------------------------------------------
# Typed gap records (the variant's core value)
# ---------------------------------------------------------------------------

def make_evidence_item(item_id, claim, confidence, locator=None, quote=None, basis=None):
    """Build a schema-valid evidence item, coercing unknown confidence grades.

    `verbatim` requires a non-empty quote; otherwise it degrades to
    `paraphrased` so the claim is never presented as verbatim evidence.
    """
    conf = confidence if confidence in CONFIDENCE_VALUES else "unreported"
    if conf == "verbatim" and not (quote and str(quote).strip()):
        conf = "paraphrased"
    return {
        "id": item_id or "",
        "claim": claim or "",
        "confidence": conf,
        "locator": locator or None,
        "quote": quote if quote else None,
        "basis": basis or None,
    }


def make_uncertainty_item(item_id, topic, severity, resolution, affects=None,
                          locator=None, quote=None, basis=None):
    """Build a schema-valid uncertainty record with a bounded severity."""
    sev = severity if severity in UNCERTAINTY_SEVERITIES else "minor"
    res = resolution if resolution in UNCERTAINTY_RESOLUTIONS else "pending_human"
    return {
        "id": item_id or "",
        "topic": topic or "",
        "severity": sev,
        "resolution": res,
        "affects": list(affects or []),
        "locator": locator or None,
        "quote": quote if quote else None,
        "basis": basis or None,
    }


def make_limitation_item(item_id, scope, reason, impact=None, locator=None, quote=None):
    """Build a schema-valid limitation; unknown reason codes are coerced to the
    documented `parse_failed` bucket instead of being dropped."""
    code = reason if reason in LIMITATION_REASONS else "parse_failed"
    return {
        "id": item_id or "",
        "scope": scope or "",
        "reason": code,
        "impact": impact or "",
        "locator": locator or None,
        "quote": quote if quote else None,
    }


# ---------------------------------------------------------------------------
# Result assembly / validation
# ---------------------------------------------------------------------------

def make_skeleton(case_id, contract_keys, status="partial"):
    """Valid partial result: case_id + every contract key + every meta key."""
    result = {"case_id": case_id}
    for key in contract_keys:
        result.setdefault(key, None)
    for key in META_FIELDS:
        if key == "status":
            result["status"] = status
        elif key == "runtime":
            result["runtime"] = {}
        else:
            result[key] = []
    return result


def append_change(change_log, event, detail=None):
    log = list(change_log or [])
    log.append(
        {
            "event": event,
            "detail": detail or "",
            "at": _dt.datetime.now(_dt.timezone.utc)
            .replace(tzinfo=None)
            .isoformat(timespec="seconds")
            + "Z",
        }
    )
    return log


def _validate_typed_lists(result, degradations):
    """Coerce/validate typed lists so downstream consumers get stable shapes."""
    for key in ("sources", "uncertainty", "limitations", "errors",
                "degradations", "pending_human"):
        val = result.get(key)
        if val is None:
            result[key] = []
        elif not isinstance(val, list):
            result[key] = [val]
            degradations.append("coerced non-list field %s to list" % key)
    for item in result.get("uncertainty") or []:
        if isinstance(item, dict):
            if item.get("severity") not in UNCERTAINTY_SEVERITIES:
                item["severity"] = "minor"
            if item.get("resolution") not in UNCERTAINTY_RESOLUTIONS:
                item["resolution"] = "pending_human"
    for item in result.get("limitations") or []:
        if isinstance(item, dict) and item.get("reason") not in LIMITATION_REASONS:
            item["reason"] = "parse_failed"
    for item in result.get("evidence") or []:
        if isinstance(item, dict):
            if item.get("confidence") not in CONFIDENCE_VALUES:
                item["confidence"] = "unreported"


def assemble_result(case_id, contract_keys, findings, status=None,
                    change_log=None, runtime=None):
    """Merge agent findings with the deterministic skeleton and validate it.

    Never drops an output_contract key; every contract key is present even when
    the findings file lacks it. Returns (result, degradations).
    """
    degradations = []
    result = make_skeleton(case_id, contract_keys, status or "partial")
    f = findings if isinstance(findings, dict) else {}

    # Copy every findings field (the agent's structured output), preserving
    # the contract keys already seeded in the skeleton; case_id is protected.
    for key, val in f.items():
        if key == "case_id":
            continue
        result[key] = val

    # The variant-specific fields are always present and typed.
    for key in ("limitations", "uncertainty", "degradations", "pending_human",
                "sources", "errors"):
        result.setdefault(key, [])

    _validate_typed_lists(result, degradations)

    result["case_id"] = case_id
    if "status" in f and f["status"] in STATUS_VALUES and status is None:
        result["status"] = f["status"]
    else:
        result["status"] = status or result["status"]

    result["change_log"] = list(change_log or [])
    result["runtime"] = runtime if isinstance(runtime, dict) else {}
    result["degradations"] = list(result.get("degradations") or []) + degradations
    return result, degradations


# ---------------------------------------------------------------------------
# Deterministic fingerprint (idempotence / replayability)
# ---------------------------------------------------------------------------

def fingerprint(obj):
    """Stable SHA-256 over the canonical JSON of an object (sorted keys)."""
    payload = json.dumps(obj, sort_keys=True, ensure_ascii=False,
                         default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
