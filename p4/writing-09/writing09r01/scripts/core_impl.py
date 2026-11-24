#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Core implementation for the results-and-discussion writing skill.

Standard library only. Imported by main.py and selftest.py.
Responsibilities:
  * tolerant input discovery and normalization (input_summary.json)
  * early valid result.json skeleton (guarantees a partial deliverable)
  * deterministic patch merge for incremental filling
  * structural + evidence + consistency + overclaim checks
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB per input file

# Default output-contract fields used when /input/case.json declares none.
DEFAULT_CONTRACT = {
    "deliverable": None,
    "facts_sources": [],
    "version_boundaries": {},
}

# Overclaim / unsupported-wording lexicon (results-and-discussion context).
FORBIDDEN_ZH = [
    "首次", "国际领先", "填补空白", "颠覆性", "革命性", "世界第一",
    "全球首个", "史无前例", "独一无二", "无与伦比", "开创性", "里程碑式",
    "重大突破", "遥遥领先", "前所未有",
]
FORBIDDEN_EN = [
    "first ever", "unprecedented", "world-leading", "world's first",
    "game-changing", "revolutionary", "groundbreaking", "breakthrough",
    "paradigm-shifting", "state of the art",
]

CLAIM_TYPES = {
    "result_fact", "result_trend", "result_comparison", "result_negative",
    "discussion_interpretation", "discussion_literature",
    "discussion_consistency", "discussion_inconsistency",
    "discussion_limitation", "causal_claim", "speculation",
}

CONFIDENCE_LEVELS = {"direct", "inferred", "unverified"}
STRENGTH_LEVELS = {"strong", "moderate", "weak", "speculative"}
CAUSAL_BOUNDARIES = {"correlational", "predictive", "causal", "speculative"}
STATUS_LEVELS = {"complete", "partial", "failed", "insufficient_evidence", "input_error"}

_TEXT_EXT = {".md", ".txt", ".markdown", ".rst", ".csv", ".tsv", ".json", ".jsonl", ".xml"}
_BINARY_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".svg", ".docx", ".xlsx", ".pptx"}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Reading helpers
# --------------------------------------------------------------------------- #
def read_text_safe(path, max_bytes=MAX_FILE_BYTES):
    """Read a text file tolerantly. Returns (text_or_None, error_or_None)."""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, {"path": path, "error": "unreadable", "detail": str(e)}
    if size > max_bytes:
        return None, {"path": path, "error": "too_large", "size": size}
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        return None, {"path": path, "error": "unreadable", "detail": str(e)}
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), {"path": path, "error": "bad_encoding"}


def load_json_tolerant(path):
    """Parse JSON; on failure return (None, error) instead of raising."""
    text, err = read_text_safe(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except (json.JSONDecodeError, TypeError) as e:
        return None, {"path": path, "error": "bad_json", "detail": str(e)}


def kind_of(name):
    ext = os.path.splitext(name)[1].lower()
    if ext == ".pdf":
        return "pdf"
    if ext in _BINARY_EXT and ext != ".pdf":
        return "binary"
    if ext in _TEXT_EXT or ext == "":
        return "text"
    return "unknown"


def _guess_case_id(obj):
    if isinstance(obj, dict):
        for k in ("case_id", "caseId", "id"):
            if obj.get(k):
                return str(obj[k])
    return None


# --------------------------------------------------------------------------- #
# Input discovery & normalization
# --------------------------------------------------------------------------- #
def discover_files(input_dir):
    """Walk input dir without assuming fixed names. Skips parser caches."""
    found = []
    if not os.path.isdir(input_dir):
        return found, [{"path": input_dir, "error": "missing_input_dir"}]
    errs = []
    for root, dirs, names in os.walk(input_dir):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".parser")]
        for n in names:
            found.append(os.path.join(root, n))
    return found, errs


def identify_inputs(input_dir):
    """Pick case.json / task.md / materials by name then by content fallback."""
    files, errs = discover_files(input_dir)
    case_path = task_path = None
    materials = []
    # Name-first pass
    for p in files:
        base = os.path.basename(p).lower()
        if base == "case.json":
            case_path = p
        elif base == "task.md":
            task_path = p
    # Content-fallback pass
    if case_path is None:
        for p in files:
            if kind_of(p) != "text":
                continue
            obj, err = load_json_tolerant(p)
            if obj is not None and _guess_case_id(obj):
                case_path = p
                break
    if task_path is None:
        for p in files:
            if p == case_path or kind_of(p) != "text":
                continue
            base = os.path.basename(p).lower()
            if base.endswith(".md") and base not in ("readme.md",):
                text, _ = read_text_safe(p)
                if text and any(k in text for k in ("题目", "challenge", "评分", "task")):
                    task_path = p
                    break
    for p in files:
        if p in (case_path, task_path):
            continue
        materials.append(p)
    return {"case": case_path, "task": task_path, "materials": materials, "all": files, "errors": errs}


def normalize_input(input_dir, work_dir):
    """Produce a normalized input_summary.json dict."""
    os.makedirs(work_dir, exist_ok=True)
    ident = identify_inputs(input_dir)
    errors = list(ident["errors"])
    case_id = None
    contract = None
    case_keys = []
    if ident["case"]:
        obj, err = load_json_tolerant(ident["case"])
        if err:
            errors.append(err)
        if isinstance(obj, dict):
            case_id = _guess_case_id(obj)
            case_keys = sorted(obj.keys())
            if isinstance(obj.get("output_contract"), dict):
                contract = obj["output_contract"]
    if contract is None:
        contract = dict(DEFAULT_CONTRACT)
        errors.append({"path": ident["case"] or "<none>", "error": "output_contract_missing",
                       "detail": "using default contract"})

    task_text = ""
    if ident["task"]:
        text, err = read_text_safe(ident["task"])
        if err:
            errors.append(err)
        else:
            task_text = text

    material_rows = []
    for p in ident["materials"]:
        k = kind_of(p)
        if k == "pdf":
            text, err = read_text_safe(p)  # raw PDF bytes usually decode badly; handled below
            material_rows.append({
                "path": p, "kind": k, "size": os.path.getsize(p) if os.path.exists(p) else 0,
                "preview": "", "parse_note": "PDF: parse with platform pdf-text-tool / pdf-render-tool",
                "error": (err or {}).get("error"),
            })
        elif k == "binary":
            material_rows.append({"path": p, "kind": k,
                                  "size": os.path.getsize(p) if os.path.exists(p) else 0,
                                  "preview": "", "parse_note": "binary artifact; render/parse before quoting"})
        else:
            text, err = read_text_safe(p)
            if err:
                errors.append(err)
            material_rows.append({
                "path": p, "kind": k,
                "size": os.path.getsize(p) if os.path.exists(p) else 0,
                "preview": (text[:800] if text else ""),
                "error": (err or {}).get("error") if err else None,
            })

    summary = {
        "case_id": case_id,
        "output_contract": contract,
        "case_keys": case_keys,
        "task_text": task_text,
        "materials": material_rows,
        "errors": errors,
        "normalized_at": now_iso(),
    }
    out_path = os.path.join(work_dir, "input_summary.json")
    write_json(summary, out_path)
    return summary, out_path


# --------------------------------------------------------------------------- #
# Result skeleton & patch merge
# --------------------------------------------------------------------------- #
def _placeholder(v):
    if isinstance(v, list):
        return []
    if isinstance(v, dict):
        return {}
    return None


def build_skeleton(summary, output_dir):
    """Create a minimal honest result.json with all contract fields present."""
    os.makedirs(output_dir, exist_ok=True)
    result = {"case_id": summary.get("case_id") or ""}
    contract = summary.get("output_contract") or {}
    for k, v in contract.items():
        result[k] = v if (v is not None and not isinstance(v, (list, dict))) else _placeholder(v)
    for k, v in DEFAULT_CONTRACT.items():
        result.setdefault(k, _placeholder(v))
    result.setdefault("status", "partial")
    result.setdefault("sources", [])
    result.setdefault("uncertainty", [])
    result.setdefault("pending_human", [])
    result.setdefault("evidence_map", [])
    result.setdefault("change_log", [{
        "time": now_iso(), "action": "skeleton_written",
        "detail": "valid minimal result.json landed before analysis; fields are placeholders",
    }])
    out_path = os.path.join(output_dir, "result.json")
    write_json(result, out_path)
    return result, out_path


def _deep_merge(base, patch):
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def apply_patch(result_path, patch_path):
    """Merge a JSON patch into result.json deterministically; lists replace wholesale."""
    result, err = load_json_tolerant(result_path)
    if err:
        return None, err
    patch, err = load_json_tolerant(patch_path)
    if err:
        return result, err
    _deep_merge(result, patch)
    write_json(result, result_path)
    return result, None


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def write_json(obj, path):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def scan_forbidden(text):
    hits = []
    if not text:
        return hits
    low = text.lower()
    for w in FORBIDDEN_ZH:
        if w in text:
            hits.append(w)
    for w in FORBIDDEN_EN:
        if w in low:
            hits.append(w)
    return hits


def _require(checks, ok, level, code, msg):
    checks.append({"level": level, "code": code, "ok": ok, "message": msg})


def validate_evidence(entry, checks, cid=""):
    problems = []
    if not isinstance(entry, dict):
        return ["evidence entry is not an object"]
    for k in ("claim_id", "claim", "locator", "quote"):
        if not entry.get(k):
            problems.append("missing/empty %s" % k)
    if entry.get("claim_type") not in CLAIM_TYPES:
        problems.append("invalid claim_type %r" % entry.get("claim_type"))
    if entry.get("confidence") not in CONFIDENCE_LEVELS:
        problems.append("invalid confidence %r" % entry.get("confidence"))
    ct = entry.get("claim_type")
    if ct in ("discussion_interpretation", "discussion_literature",
              "discussion_consistency", "discussion_inconsistency",
              "discussion_limitation", "causal_claim", "speculation"):
        if entry.get("strength") not in STRENGTH_LEVELS:
            problems.append("discussion/causal claim %s missing strength" % cid)
    if ct in ("causal_claim", "speculation"):
        if entry.get("causal_boundary") not in CAUSAL_BOUNDARIES:
            problems.append("causal/speculation claim %s missing causal_boundary" % cid)
        if not entry.get("basis"):
            problems.append("causal/speculation claim %s missing basis" % cid)
    if entry.get("status") not in ("verified", "unverified", "conflict", None):
        problems.append("invalid evidence status %r" % entry.get("status"))
    return problems


def check_result(result_path, summary_path=None):
    """Validate structure, contract completeness, evidence map, overclaim words."""
    checks = []
    result, err = load_json_tolerant(result_path)
    if err:
        _require(checks, False, "hard", "result_unreadable", str(err))
        return {"ok": False, "checks": checks, "fatal": True}
    if not isinstance(result, dict):
        _require(checks, False, "hard", "result_not_object", "result.json must be a JSON object")
        return {"ok": False, "checks": checks, "fatal": True}

    _require(checks, bool(result.get("case_id")), "hard", "missing_case_id", "case_id required")
    _require(checks, result.get("status") in STATUS_LEVELS, "hard", "bad_status",
             "status must be one of %s" % sorted(STATUS_LEVELS))

    contract = DEFAULT_CONTRACT
    if summary_path and os.path.exists(summary_path):
        summary, _ = load_json_tolerant(summary_path)
        if isinstance(summary, dict) and isinstance(summary.get("output_contract"), dict):
            contract = summary["output_contract"]
    for k in contract:
        _require(checks, k in result, "hard", "missing_contract_field",
                 "output_contract field %r absent" % k)

    for k in ("sources", "uncertainty", "pending_human", "change_log", "evidence_map"):
        _require(checks, isinstance(result.get(k), list), "hard", "field_not_list",
                 "%s must be a list" % k)

    # Evidence map: per-entry traceability.
    ev = result.get("evidence_map") or []
    ev_problems = 0
    for i, entry in enumerate(ev):
        cid = entry.get("claim_id") if isinstance(entry, dict) else "#%d" % i
        for p in validate_evidence(entry, checks, cid):
            ev_problems += 1
            _require(checks, False, "hard", "evidence_invalid", "evidence[%d] %s: %s" % (i, cid, p))
    _require(checks, ev_problems == 0, "hard", "evidence_invalid_total",
             "%d evidence problem(s)" % ev_problems)

    # Overclaim wording scan over deliverable text + claims.
    texts = []
    dv = result.get("deliverable")
    if isinstance(dv, dict):
        for v in dv.values():
            if isinstance(v, str):
                texts.append(v)
    for entry in ev:
        if isinstance(entry, dict) and isinstance(entry.get("claim"), str):
            texts.append(entry["claim"])
    forhits = []
    for t in texts:
        forhits.extend(scan_forbidden(t))
    _require(checks, not forhits, "warn", "overclaim_words",
             "unsupported wording found: %s" % ", ".join(sorted(set(forhits))))

    # Numbers vs. evidence coverage heuristic (only warns, never fabricates).
    numeric_tokens = set()
    for t in texts:
        numeric_tokens.update(re.findall(r"\d+(?:\.\d+)?", t))
    numeric_evidence = 0
    for entry in ev:
        if isinstance(entry, dict) and entry.get("value"):
            numeric_evidence += 1
    if numeric_tokens and not ev:
        _require(checks, False, "warn", "no_evidence_map",
                 "manuscript contains %d numeric tokens but evidence_map is empty" % len(numeric_tokens))
    if numeric_tokens and ev and numeric_evidence == 0:
        _require(checks, False, "warn", "numbers_without_evidence",
                 "%d numeric tokens found; no evidence entry carries a 'value'" % len(numeric_tokens))

    hard = [c for c in checks if c["level"] == "hard" and not c["ok"]]
    warns = [c for c in checks if c["level"] == "warn" and not c["ok"]]
    return {"ok": len(hard) == 0, "checks": checks,
            "hard_errors": len(hard), "warnings": len(warns), "fatal": False}
