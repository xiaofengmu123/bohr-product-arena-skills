#!/usr/bin/env python3
"""core_impl — typed intermediate representation, contract assembly, evidence checks.

This module is the engine behind the thin entry point ``main.py``. It only
depends on the Python standard library and is kept separately so that:

* the entry point stays small and auditable (<30 KB);
* the contract/IR engine is independently importable and unit-testable
  (see ``selftest.py``);
* content decisions (page claims, verbatim quotes, audience adaptation) are
  cleanly separated from deterministic contract compliance (schema
  validation, evidence-locator checks, result assembly).

The architectural principle is **validation-first gating**: an honest partial
``result.json`` is written first, and only after the intermediate
representation ``plan.json`` passes schema + evidence checks is it merged into
the final result. Repeated runs are deterministic because ``result.json`` is
always assembled from the same IR + runtime metadata, never hand-written.
"""
from __future__ import annotations

import copy
import json
import os
import time

VERSION = "1.1.0"

STATUSES = ("complete", "partial", "insufficient_evidence", "input_error", "failed")

# Output-contract fields required when ``case.json`` provides no
# ``output_contract``. If one is provided, its keys are preserved verbatim and
# unknown keys are added to this set, never dropped or renamed.
DEFAULT_CONTRACT_KEYS = (
    "report_goal",
    "audience",
    "narrative",
    "pages",
    "editable_deck",
    "evidence_map",
    "qa_prep",
    "rendering",
)

PAGE_KEYS = (
    "page_index",
    "title",
    "single_claim",
    "evidence_strength",
    "figures",
    "speaker_notes",
    "source_refs",
)

EVIDENCE_STRENGTHS = ("high", "medium", "low")
QA_CATEGORIES = ("mechanism", "data", "control", "limitation", "other")

# Metadata appended to every result regardless of the case contract.
META_KEYS = ("case_id", "status", "sources", "uncertainty",
             "pending_human", "change_log", "runtime")

MAX_TEXT_BYTES = 2 * 1024 * 1024

MATERIAL_EXTS = (
    ".pdf", ".png", ".jpg", ".jpeg", ".pptx", ".ppt", ".odp", ".html",
    ".htm", ".docx", ".md", ".txt", ".svg", ".gif", ".webp", ".tiff",
    ".tif", ".csv", ".json", ".xlsx",
)

# Heuristic words used to flag a possible strength/claim mismatch. This is a
# warning, not a verdict: scientific hedging is legitimate and the author may
# keep "high" after re-checking.
HEDGE_WORDS = (
    "可能", "也许", "或许", "初步", "推测", "推断", "假设", "猜想",
    "暗示", "尚未", "有待", "suggest", "suggests", "may", "might",
    "perhaps", "likely", "preliminary", "indicate", "indicates", "potentially",
)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def log(action: str, scope: str = "result.json") -> dict:
    return {"timestamp": now_iso(), "action": action, "scope": scope}


def default_contract() -> dict:
    """Return the default output-contract field schema with empty values."""
    return {
        "report_goal": {
            "objective": None,
            "checkable_outcomes": [],
            "minutes": None,
            "max_slides": None,
        },
        "audience": {
            "prior": None,
            "concerns": [],
            "likely_questions": [],
            "term_depth": None,
        },
        "narrative": {
            "spine": None,
            "choices": [],
            "excluded": [],
        },
        "pages": [],
        "editable_deck": {"path": None, "format": None, "template": None},
        "evidence_map": [],
        "qa_prep": [],
        "rendering": {
            "source_files": [],
            "export_files": [],
            "fonts": [],
            "template": None,
            "change_log": [],
        },
    }


# --------------------------------------------------------------------------- #
# Input discovery and loading (utf-8-sig first, graceful fallbacks)
# --------------------------------------------------------------------------- #

def load_text(path: str, max_bytes: int = MAX_TEXT_BYTES):
    """Return (text, error) for a text file; never raises on missing/bad data.

    Encoding order: utf-8-sig, utf-8, gb18030, latin-1. The last two are
    fallbacks for files saved on non-UTF-8 systems; the chosen fallback is
    reported so the caller can record it honestly.
    """
    if not path or not os.path.isfile(path):
        return None, "missing_file"
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, "stat_error:%s" % exc
    if size > max_bytes:
        return None, "too_large:%d" % size
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, "read_error:%s" % exc
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace"), "fallback_latin1"


def load_json(path: str, max_bytes: int = MAX_TEXT_BYTES):
    """Return (data, errors) for a JSON file; empty list means success."""
    text, err = load_text(path, max_bytes)
    if text is None:
        return None, [err or "missing_file:%s" % path]
    try:
        return json.loads(text), []
    except Exception as exc:  # noqa: BLE001 - must not crash on bad JSON
        return None, ["invalid_json:%s" % type(exc).__name__]


def guess_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return "pdf"
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".tiff", ".tif"):
        return "image"
    if ext in (".pptx", ".ppt", ".odp"):
        return "deck_template"
    if ext == ".docx":
        return "document"
    if ext in (".html", ".htm"):
        return "html"
    if ext in (".md", ".txt"):
        return "text"
    if ext == ".json":
        return "json"
    if ext in (".csv", ".xlsx"):
        return "data"
    return "other"


def discover_files(base: str, recursive: bool = True):
    out = []
    if not base or not os.path.isdir(base):
        return out
    for root, _dirs, files in os.walk(base):
        for name in sorted(files):
            path = os.path.join(root, name)
            if path.lower().endswith(MATERIAL_EXTS):
                out.append(path)
        if not recursive:
            break
    return out


def resolve_case_id(case) -> str:
    if not isinstance(case, dict):
        return None
    for key in ("id", "case_id", "armiId"):
        value = case.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def resolve_contract(case):
    """Return (ordered_keys, provided_values) from the case output_contract.

    All keys supplied by the case are preserved; keys missing from the case
    fall back to the default contract schema, and any key the case provides
    that is not in the default schema is appended (preserved) as well.
    """
    keys = list(DEFAULT_CONTRACT_KEYS)
    provided = {}
    if isinstance(case, dict) and isinstance(case.get("output_contract"), dict):
        for key, value in case["output_contract"].items():
            if key not in keys:
                keys.append(key)
            provided[key] = value
    return keys, provided


def build_skeleton(case) -> dict:
    """Build a legal partial result containing every contract field."""
    keys, provided = resolve_contract(case)
    defaults = default_contract()
    result = {"case_id": resolve_case_id(case) or "unknown"}
    for key in keys:
        if key in provided:
            result[key] = copy.deepcopy(provided[key])
        elif key in defaults:
            result[key] = copy.deepcopy(defaults[key])
        else:
            result[key] = None
    result.update({
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [log("skeleton_emitted")],
        "runtime": {
            "generator": "academic-talk-organizer",
            "generator_version": VERSION,
            "started": now_iso(),
            "elapsed_s": 0,
        },
    })
    return result


# --------------------------------------------------------------------------- #
# Intermediate representation (plan.json) validation
# --------------------------------------------------------------------------- #

def validate_plan(plan) -> list:
    """Return a list of issue dicts: {level, field, message}.

    levels: "error" (blocks a complete status) or "warning" (needs review).
    """
    issues = []
    if not isinstance(plan, dict):
        return [{"level": "error", "field": "plan",
                 "message": "plan must be a JSON object"}]
    for key in ("report_goal", "audience", "narrative", "pages", "qa_prep"):
        if key not in plan:
            issues.append({"level": "warning", "field": key,
                           "message": "missing field"})
    pages = plan.get("pages")
    if not isinstance(pages, list):
        issues.append({"level": "error", "field": "pages",
                       "message": "pages must be a list"})
        return issues
    for i, page in enumerate(pages):
        if not isinstance(page, dict):
            issues.append({"level": "error", "field": "pages[%d]" % i,
                           "message": "page is not an object"})
            continue
        for key in PAGE_KEYS:
            if key not in page:
                issues.append({"level": "warning", "field": "pages[%d].%s" % (i, key),
                               "message": "missing field"})
        claim = page.get("single_claim")
        if not isinstance(claim, str) or not claim.strip():
            issues.append({"level": "error", "field": "pages[%d].single_claim" % i,
                           "message": "single_claim must be a non-empty string"})
        if page.get("evidence_strength") not in EVIDENCE_STRENGTHS:
            issues.append({"level": "warning", "field": "pages[%d].evidence_strength" % i,
                           "message": "must be one of high|medium|low"})
    return issues


def check_evidence(pages) -> list:
    """Every page needs at least one locator + verbatim quote (hard rule)."""
    problems = []
    if not isinstance(pages, list):
        return [{"level": "error", "field": "pages",
                 "message": "pages missing for evidence check"}]
    for i, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        refs = page.get("source_refs")
        if not isinstance(refs, list) or not refs:
            problems.append({"level": "error",
                             "field": "pages[%d].source_refs" % i,
                             "message": "missing source_refs; every claim needs a locator + quote"})
            continue
        for j, ref in enumerate(refs):
            if not isinstance(ref, dict) or not ref.get("locator"):
                problems.append({"level": "error",
                                 "field": "pages[%d].source_refs[%d].locator" % (i, j),
                                 "message": "missing locator"})
            if not isinstance(ref, dict) or not ref.get("quote") or not str(ref.get("quote")).strip():
                problems.append({"level": "error",
                                 "field": "pages[%d].source_refs[%d].quote" % (i, j),
                                 "message": "missing verbatim quote"})
    return problems


def hedge_check(pages) -> list:
    """Warn when a claim marked high-strength contains hedging wording."""
    out = []
    for i, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        claim = page.get("single_claim")
        if not isinstance(claim, str) or page.get("evidence_strength") != "high":
            continue
        lowered = claim.lower()
        hits = [w for w in HEDGE_WORDS if w.lower() in lowered]
        if hits:
            out.append({
                "level": "warning",
                "field": "pages[%d].single_claim" % i,
                "message": "evidence_strength=high but claim contains hedging "
                           "wording %s; re-check the strength label" % hits[:3],
            })
    return out


def derive_evidence_map(pages) -> list:
    """Normalize page source_refs into a per-page evidence map (deterministic)."""
    out = []
    for page in pages or []:
        if not isinstance(page, dict):
            continue
        out.append({
            "page_index": page.get("page_index"),
            "single_claim": page.get("single_claim"),
            "source_refs": copy.deepcopy(page.get("source_refs", [])),
        })
    return out


# --------------------------------------------------------------------------- #
# Assembly and status
# --------------------------------------------------------------------------- #

def add_note(result: dict, key: str, text: str) -> None:
    if key not in result or not isinstance(result.get(key), list):
        result[key] = []
    if text not in result[key]:
        result[key].append(text)


def merge_sources(script_sources, plan_sources):
    """Union by path; a plan-provided entry overrides the script's read flag."""
    merged = {}
    for item in script_sources or []:
        if isinstance(item, dict) and item.get("path"):
            merged[item["path"]] = dict(item)
    for item in plan_sources or []:
        if isinstance(item, dict) and item.get("path"):
            merged[item["path"]] = dict(item)
    return list(merged.values())


def assemble(skeleton: dict, plan: dict) -> dict:
    """Merge the IR into the skeleton deterministically."""
    result = copy.deepcopy(skeleton)
    for key in ("report_goal", "audience", "narrative", "pages", "qa_prep",
                "evidence_map", "editable_deck", "rendering",
                "sources", "uncertainty", "pending_human"):
        if key in plan and plan[key] is not None:
            result[key] = copy.deepcopy(plan[key])
    # Derive a normalized evidence map if the author did not provide one.
    if not result.get("evidence_map") and isinstance(result.get("pages"), list):
        result["evidence_map"] = derive_evidence_map(result["pages"])
    if isinstance(plan.get("change_log"), list) and plan["change_log"]:
        result["change_log"] = list(result.get("change_log", [])) + copy.deepcopy(plan["change_log"])
    return result


def finalize_status(result: dict, case_ok: bool, issues: list,
                    has_deck: bool, has_export: bool) -> dict:
    """Set an honest status from the strongest available evidence."""
    errors = [i for i in issues if i.get("level") == "error"]
    pages = result.get("pages")
    n_pages = len(pages) if isinstance(pages, list) else 0

    if not case_ok:
        result["status"] = "insufficient_evidence"
        add_note(result, "uncertainty",
                 "case.json 缺失或不可读，无法确定案例标识与输出契约")
    elif errors:
        result["status"] = "partial"
        add_note(result, "uncertainty",
                 "%d 个阻断性问题未解决（详见 validation_report.json）" % len(errors))
    elif n_pages == 0:
        result["status"] = "partial"
        add_note(result, "uncertainty", "尚未产出任何页面（pages 为空）")
    elif not has_deck:
        result["status"] = "partial"
        add_note(result, "pending_human", "缺少可编辑演示文件，无法完成交付")
    else:
        result["status"] = "complete"
    return result


def write_json(path: str, data) -> str:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    return path
