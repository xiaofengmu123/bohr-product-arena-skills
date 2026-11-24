#!/usr/bin/env python3
"""Shared, deterministic helpers for the academic-format-adaptation Skill.

Design contract
---------------
* Python standard library only; no third-party imports.
* All JSON is serialized canonically (sorted keys, ``ensure_ascii=False``,
  2-space indent, trailing newline), so identical inputs produce
  byte-identical outputs except for the explicitly volatile ``runtime`` block.
* Identifiers are content-hashed (SHA-256, first 12 hex), never sequence
  numbers, so re-ordering or re-running cannot silently change IDs.
* Every function degrades instead of raising; errors are collected and
  returned so callers can write an honest ``status``.
"""

import copy
import hashlib
import json
import os
import re

DEFAULT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")
MAX_FILE_BYTES = 2 * 1024 * 1024  # skip files larger than 2 MB

# Keys that may legitimately change between two runs on the same input.
VOLATILE_KEYS = {"runtime", "content_digest"}

# Fallback output contract when /input/case.json carries no output_contract.
FALLBACK_CONTRACT = {
    "deliverable": "目标产物：满足题目指定体裁、受众与格式的可编辑稿件或结构化写作产物",
    "fact_source_map": "事实与来源映射：数字、术语、引用、图表与关键论断的来源/修改记录",
    "version_boundary": "版本与边界：修改差异、人工确认项、材料缺口与异常",
}

FALLBACK_DEFAULTS = {
    "deliverable": {
        "content": None,
        "artifact_paths": [],
        "format_spec_applied": [],
    },
    "fact_source_map": [],
    "version_boundary": {
        "modifications": [],
        "pending_human": [],
        "material_gaps": [],
        "anomalies": [],
    },
}

# Reserved keys the Skill always appends on top of the output_contract.
RESERVED_KEYS = (
    "status",
    "sources",
    "uncertainty",
    "pending_human",
    "change_log",
    "errors",
    "degradations",
    "runtime",
    "content_digest",
)

ALLOWED_STATUS = {"partial", "complete", "failed", "insufficient_evidence", "input_error"}

TEXT_EXTS = {".txt", ".md", ".tex", ".csv", ".bib", ".xml", ".json", ".html", ".rst", ".yaml", ".yml"}
MATERIAL_EXTS = TEXT_EXTS | {".pdf", ".docx", ".doc", ".odt", ".rtf", ".png", ".jpg", ".jpeg", ".svg"}


def canonical_json(obj):
    """Serialize deterministically: sorted keys, UTF-8, no ASCII escaping."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2,
                      separators=(",", ": ")) + "\n"


def sha12(text):
    """First 12 hex chars of SHA-256; stable, collision-resistant suffix."""
    if not isinstance(text, str):
        text = canonical_json(text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def stable_id(kind, text):
    """Deterministic ID = ``kind`` + content hash (no counters, no timestamps)."""
    return "%s-%s" % (kind, sha12(text))


def now_iso():
    """Volatile wall-clock timestamp; only ever written inside ``runtime``."""
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def read_json_safe(path):
    """Read a JSON file tolerantly; returns (obj, error_or_none). Never raises."""
    if not path or not os.path.exists(path):
        return None, "missing: %s" % (path,)
    text, err = read_text_safe(path)
    if text is None:
        return None, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, "invalid JSON: %s" % (e,)


def read_text_safe(path):
    """Read text with encoding fallback; returns (text_or_none, error_or_none)."""
    if not os.path.exists(path):
        return None, "missing: %s" % (path,)
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, "stat failed: %s" % (e,)
    if size > MAX_FILE_BYTES:
        return None, "oversize %d > %d bytes" % (size, MAX_FILE_BYTES)
    raw = None
    last_err = None
    for enc in DEFAULT_ENCODINGS:
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
            return raw.decode(enc), None
        except (UnicodeDecodeError, OSError) as e:
            last_err = "%s: %s" % (enc, e)
    if raw is not None:
        return raw.decode("utf-8", errors="replace"), "replaced decoding errors (%s)" % (last_err,)
    return None, "unreadable: %s" % (last_err,)


def write_json(path, obj):
    """Write canonical JSON atomically-ish (write then replace). Returns error or None."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(canonical_json(obj))
        os.replace(tmp, path)
        return None
    except OSError as e:
        return "write failed: %s" % (e,)


def _safe_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def _classify(manifest, path):
    base = os.path.basename(path).lower()
    ext = os.path.splitext(base)[1].lower()
    entry = {"name": os.path.basename(path), "path": path, "ext": ext,
             "size": _safe_size(path)}
    manifest["files"].append(entry)
    if base == "case.json":
        manifest["case_path"] = path
    elif base in ("task.md", "task.txt"):
        manifest["task_path"] = path
    elif ext in MATERIAL_EXTS:
        manifest["materials"].append(entry)


def discover_input(input_dir):
    """Scan the input directory one level deep; never raises."""
    manifest = {"root": input_dir, "files": [], "case_path": None,
                "task_path": None, "materials": [], "errors": [], "degradations": []}
    if not os.path.isdir(input_dir):
        manifest["errors"].append("input directory missing: %s" % (input_dir,))
        return manifest
    try:
        names = sorted(os.listdir(input_dir))
    except OSError as e:
        manifest["errors"].append("listdir failed: %s" % (e,))
        return manifest
    for name in names:
        p = os.path.join(input_dir, name)
        if os.path.isdir(p):
            try:
                subs = sorted(os.listdir(p))
            except OSError as e:
                manifest["degradations"].append("subdir listdir failed %s: %s" % (p, e))
                continue
            for sub in subs:
                sp = os.path.join(p, sub)
                if os.path.isfile(sp):
                    _classify(manifest, sp)
        elif os.path.isfile(p):
            _classify(manifest, p)
    return manifest


def load_case(case_path):
    """Load case.json tolerantly; returns (obj_or_none, errors_list)."""
    errors = []
    if not case_path or not os.path.exists(case_path):
        return None, ["case.json not found at %s" % (case_path,)]
    text, err = read_text_safe(case_path)
    if err:
        errors.append("case.json read degraded: %s" % (err,))
    if text is None:
        return None, errors
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        return None, errors + ["case.json invalid JSON: %s" % (e,)]
    if not isinstance(obj, dict):
        return None, errors + ["case.json root is not an object"]
    return obj, errors


def case_id_of(case):
    if not isinstance(case, dict):
        return None
    for key in ("case_id", "id", "challenge_id", "armiId"):
        value = case.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return None


def output_contract_of(case):
    """Return the case's output_contract, or the documented three-field fallback."""
    if isinstance(case, dict):
        oc = case.get("output_contract")
        if isinstance(oc, dict) and oc:
            return {str(k): v for k, v in oc.items()}
    return copy.deepcopy(FALLBACK_CONTRACT)


def default_for_key(key):
    if key in FALLBACK_DEFAULTS:
        return copy.deepcopy(FALLBACK_DEFAULTS[key])
    if key.endswith(("_map", "_list", "_maps", "_lists")):
        return []
    if key.endswith(("_boundary", "_meta", "_info", "_options")):
        return {}
    return None


def build_skeleton(case_obj, started_at):
    case_id = case_id_of(case_obj) or "unknown"
    contract = output_contract_of(case_obj)
    result = {"case_id": case_id, "status": "partial"}
    for key in contract:
        result[key] = default_for_key(key)
    result.update({
        "sources": [],
        "uncertainty": {
            "claims_requiring_confirmation": [],
            "conflicting_evidence": [],
            "material_gaps": [],
            "limitations": [],
        },
        "pending_human": [],
        "change_log": [{
            "step": 0,
            "action": "scaffold",
            "detail": "partial result skeleton written before analysis",
        }],
        "errors": [],
        "degradations": [],
        "runtime": {"started_at": started_at, "finished_at": None, "elapsed_seconds": None},
        "content_digest": None,
    })
    return result


def merge_incremental(existing, skeleton):
    """Merge a fresh skeleton into an existing result without destroying progress."""
    if not isinstance(existing, dict):
        return skeleton
    merged = copy.deepcopy(existing)
    # case_id is authoritative from case.json
    merged["case_id"] = skeleton.get("case_id", merged.get("case_id"))
    # ensure contract keys exist, never overwrite existing non-null progress
    for key, value in skeleton.items():
        if key in RESERVED_KEYS:
            continue
        if key not in merged:
            merged[key] = value
        elif merged[key] is None and value is not None:
            merged[key] = value
    # ensure reserved keys exist
    for key in RESERVED_KEYS:
        if key not in merged:
            merged[key] = skeleton[key]
    if not isinstance(merged.get("uncertainty"), dict):
        merged["uncertainty"] = skeleton["uncertainty"]
    if not isinstance(merged.get("change_log"), list):
        merged["change_log"] = skeleton["change_log"]
    if not isinstance(merged.get("sources"), list):
        merged["sources"] = []
    if not isinstance(merged.get("pending_human"), list):
        merged["pending_human"] = []
    if not isinstance(merged.get("errors"), list):
        merged["errors"] = []
    if not isinstance(merged.get("degradations"), list):
        merged["degradations"] = []
    # append scaffold entry once (idempotent, fixed step)
    if not any(isinstance(e, dict) and e.get("action") == "scaffold"
               for e in merged.get("change_log", [])):
        merged.setdefault("change_log", []).append(skeleton["change_log"][0])
    return merged


def append_change(result, step, action, detail):
    """Append a change_log entry idempotently (dedupe by step+action)."""
    log = result.setdefault("change_log", [])
    if not isinstance(log, list):
        log = []
        result["change_log"] = log
    for entry in log:
        if isinstance(entry, dict) and entry.get("step") == step and entry.get("action") == action:
            entry["detail"] = detail
            return result
    log.append({"step": step, "action": action, "detail": detail})
    return result


def content_digest(result):
    """Deterministic digest over all fields except volatile runtime/digest."""
    body = {k: v for k, v in result.items()
            if k not in VOLATILE_KEYS and k != "content_digest"}
    return sha12(canonical_json(body))


def normalize_text(text):
    """Collapse all whitespace and strip zero-width/soft-hyphen characters."""
    if text is None:
        return ""
    text = text.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
    text = text.replace("\u00ad", "").replace("\ufeff", "")
    return re.sub(r"\s+", " ", text).strip()


def material_texts(material_roots):
    """Yield (path, normalized_text) for readable text materials under roots."""
    seen = set()
    for root in (material_roots or []):
        if not root or not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in sorted(filenames):
                ext = os.path.splitext(fn)[1].lower()
                if ext not in TEXT_EXTS:
                    continue
                path = os.path.join(dirpath, fn)
                rp = os.path.realpath(path)
                if rp in seen:
                    continue
                seen.add(rp)
                text, _err = read_text_safe(path)
                if text is not None:
                    yield path, normalize_text(text)


def find_quote(quote, texts):
    """Return the first material path containing ``quote`` (whitespace-normalized),
    or None. ``texts`` is a list of (path, normalized_text)."""
    needle = normalize_text(quote)
    if not needle:
        return None
    for path, hay in texts:
        if needle in hay:
            return path
    return None


def finalize_result(result, finished_at):
    """Deterministic finalization: sort, dedupe, set status hints, compute digest."""
    # stable ordering
    if isinstance(result.get("sources"), list):
        result["sources"] = sorted(result["sources"],
                                   key=lambda s: (s.get("id", "") if isinstance(s, dict) else ""))
    fsmap = result.get("fact_source_map")
    if isinstance(fsmap, list):
        result["fact_source_map"] = sorted(
            fsmap, key=lambda c: (c.get("claim_id", "") if isinstance(c, dict) else ""))
    # contract fields defaulting for the three-field fallback
    for key, value in FALLBACK_DEFAULTS.items():
        if key in result and result[key] is None:
            result[key] = copy.deepcopy(value)
    if not isinstance(result.get("runtime"), dict):
        result["runtime"] = {"started_at": None, "finished_at": None, "elapsed_seconds": None}
    result["runtime"]["finished_at"] = finished_at
    started = result["runtime"].get("started_at")
    if isinstance(started, str) and started:
        try:
            import datetime
            s = datetime.datetime.fromisoformat(started.replace("Z", "+00:00"))
            f = datetime.datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
            result["runtime"]["elapsed_seconds"] = round(max(0.0, (f - s).total_seconds()), 3)
        except (ValueError, TypeError):
            result["runtime"]["elapsed_seconds"] = None
    result["content_digest"] = content_digest(result)
    return result
