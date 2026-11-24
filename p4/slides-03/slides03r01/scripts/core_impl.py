#!/usr/bin/env python3
"""Core implementation for evidence-traced-talk-organizer.

Standard library only. Provides input discovery, a partial result skeleton,
incremental merge, fragment-level evidence validation, and output validation.
All functions are deterministic and never fabricate data: missing values are
recorded as explicit nulls / empty lists, not guessed.
"""
from __future__ import annotations

import copy
import datetime
import json
import os

VERSION = "1.0.0"
TOOL_NAME = "evidence-traced-talk-organizer"
MAX_FILE_BYTES = 2_000_000
VALID_STATUS = {"complete", "partial", "failed", "insufficient_evidence", "input_error"}
VALID_STRENGTH = {"high", "medium", "low"}
VALID_EVIDENCE_STATUS = {"verified", "conflict", "missing", "source_conflict"}

# Fields that every result.json must carry on top of the case output_contract.
DEFAULT_CONTRACT = {
    "report_goal": None,
    "audience": None,
    "narrative": None,
    "pages": [],
    "editable_deck": None,
    "evidence_map": [],
    "qa_prep": [],
    "rendering": None,
}


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text_safe(path: str, max_bytes: int = MAX_FILE_BYTES):
    """Read UTF-8 text tolerating BOM and bad encoding. Returns (text, error)."""
    try:
        if os.path.getsize(path) > max_bytes:
            return None, "file exceeds {:,} bytes, skipped".format(max_bytes)
        with open(path, "rb") as fh:
            raw = fh.read()
        for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
            try:
                return raw.decode(enc), None
            except (UnicodeDecodeError, LookupError):
                continue
        return raw.decode("utf-8", errors="replace"), None
    except OSError as exc:
        return None, str(exc)


def read_json_safe(path: str):
    """Read JSON tolerating BOM, trailing commas are not tolerated (strict)."""
    text, err = read_text_safe(path)
    if err is not None:
        return None, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, "bad JSON in {}: {}".format(path, exc)


def write_json_atomic(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def normalize_case_id(case: dict):
    if not isinstance(case, dict):
        return None
    for key in ("id", "armiId", "case_id", "challenge_id"):
        val = case.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def get_output_contract(case: dict) -> dict:
    contract = case.get("output_contract") if isinstance(case, dict) else None
    if isinstance(contract, dict):
        return copy.deepcopy(contract)
    return {}


def build_skeleton(case: dict, task_text, output_path: str, work_dir=None) -> dict:
    """Write a valid partial result.json immediately. Never fabricates content."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    contract = get_output_contract(case)
    fields = copy.deepcopy(DEFAULT_CONTRACT)
    # Preserve every original output_contract key; unknown keys stay untouched.
    for key, value in contract.items():
        if key not in fields:
            fields[key] = copy.deepcopy(value)
    result = {
        "case_id": normalize_case_id(case),
        "status": "partial",
        "output_contract": contract,
        **fields,
        "primary_artifact": None,
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "change_log": [
            {
                "timestamp": now_iso(),
                "action": "scaffold",
                "scope": "result.json initialized as partial skeleton",
            }
        ],
        "runtime": {
            "tool": TOOL_NAME,
            "version": VERSION,
            "budget_seconds": 900,
            "budget_requests": 64,
        },
    }
    write_json_atomic(output_path, result)
    return result


def deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        elif isinstance(value, list) and isinstance(out.get(key), list):
            out[key] = copy.deepcopy(value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def update_result(result_path: str, patch: dict, action: str = "update") -> dict:
    result, err = read_json_safe(result_path)
    if err is not None:
        raise SystemExit("cannot read result.json: {}".format(err))
    merged = deep_merge(result, patch)
    log = merged.setdefault("change_log", [])
    if not isinstance(log, list):
        log = []
        merged["change_log"] = log
    log.append({"timestamp": now_iso(), "action": action, "scope": list(patch.keys())})
    write_json_atomic(result_path, merged)
    return merged


def iter_pages(result: dict):
    pages = result.get("pages")
    if not isinstance(pages, list):
        return
    for page in pages:
        if isinstance(page, dict):
            yield page


def validate_evidence(result: dict):
    """Return list of evidence issues. error-level issues block a 'complete' status."""
    issues = []
    evidence_map = result.get("evidence_map")
    pages = result.get("pages")
    if not isinstance(pages, list):
        pages = []
    page_indexes = {
        p.get("page_index")
        for p in pages
        if isinstance(p, dict) and p.get("page_index") is not None
    }
    if not isinstance(evidence_map, list):
        return [{"level": "error", "message": "evidence_map is not a list"}]

    for idx, entry in enumerate(evidence_map):
        prefix = "evidence_map[{}]".format(idx)
        if not isinstance(entry, dict):
            issues.append({"level": "error", "message": prefix + " is not an object"})
            continue
        pi = entry.get("page_index")
        if pi is None:
            issues.append({"level": "error", "message": prefix + " missing page_index"})
        elif page_indexes and pi not in page_indexes:
            issues.append({"level": "warn", "message": prefix + " page_index {} not in pages".format(pi)})
        if not entry.get("claim"):
            issues.append({"level": "error", "message": prefix + " missing claim"})
        ev = entry.get("evidence")
        if not isinstance(ev, list) or not ev:
            issues.append({"level": "error", "message": prefix + " evidence is empty"})
            continue
        for j, item in enumerate(ev):
            ip = prefix + ".evidence[{}]".format(j)
            if not isinstance(item, dict):
                issues.append({"level": "error", "message": ip + " is not an object"})
                continue
            loc = item.get("locator")
            if not isinstance(loc, dict):
                issues.append({"level": "error", "message": ip + " missing locator object"})
            else:
                filled = [k for k in ("file", "page", "section", "figure_or_table") if loc.get(k)]
                if len(filled) < 2:
                    issues.append({"level": "error", "message": ip + " locator must fill >=2 of file/page/section/figure_or_table"})
            if not isinstance(item.get("quote"), str) or not item["quote"].strip():
                issues.append({"level": "error", "message": ip + " missing verbatim quote"})
            strength = item.get("strength")
            if strength not in VALID_STRENGTH:
                issues.append({"level": "warn", "message": ip + " strength {} not in {}".format(strength, sorted(VALID_STRENGTH))})
            status = item.get("status")
            if status not in VALID_EVIDENCE_STATUS:
                issues.append({"level": "warn", "message": ip + " status {} not in {}".format(status, sorted(VALID_EVIDENCE_STATUS))})
            if status == "missing" and (item.get("quote") or "").strip():
                issues.append({"level": "warn", "message": ip + " status=missing but quote present"})
    return issues


def validate_result(result_path: str, output_dir: str) -> list:
    """Structural validation of result.json and referenced artifacts."""
    issues = []
    result, err = read_json_safe(result_path)
    if err is not None:
        return [{"level": "error", "message": err}]
    if not result.get("case_id"):
        issues.append({"level": "error", "message": "missing case_id"})
    status = result.get("status")
    if status not in VALID_STATUS:
        issues.append({"level": "error", "message": "status {} not in {}".format(status, sorted(VALID_STATUS))})
    for field in ("report_goal", "audience", "narrative", "editable_deck", "rendering", "primary_artifact"):
        if field not in result:
            issues.append({"level": "warn", "message": "missing top-level field {}".format(field)})
    # Single claim discipline: exactly one non-empty single_claim per page.
    for page in iter_pages(result):
        pi = page.get("page_index", "?")
        claim = page.get("single_claim")
        if not isinstance(claim, str) or not claim.strip():
            issues.append({"level": "error", "message": "page {} missing single_claim".format(pi)})
        if "single_claim" not in page:
            issues.append({"level": "error", "message": "page {} has no single_claim key".format(pi)})
    issues.extend(validate_evidence(result))
    # Referenced editable artifact and previews must exist if declared.
    def _check(path_field, kind):
        val = result.get(path_field)
        if not val:
            issues.append({"level": "warn", "message": "{} not declared".format(kind)})
            return
        paths = val if isinstance(val, list) else [val]
        for p in paths:
            if isinstance(p, str):
                if not os.path.isabs(p):
                    p = os.path.join(output_dir, p)
                if not os.path.exists(p):
                    issues.append({"level": "error", "message": "{} file missing: {}".format(kind, p)})
    _check("primary_artifact", "primary artifact")
    deck = result.get("editable_deck")
    if isinstance(deck, dict):
        dp = deck.get("path")
        if dp:
            if not os.path.isabs(dp):
                dp = os.path.join(output_dir, dp)
            if not os.path.exists(dp):
                issues.append({"level": "error", "message": "editable_deck file missing: {}".format(dp)})
    render = result.get("rendering")
    if isinstance(render, dict):
        for key in ("source", "pdf", "png", "preview"):
            rp = render.get(key)
            if isinstance(rp, str) and rp:
                if not os.path.isabs(rp):
                    rp = os.path.join(output_dir, rp)
                if not os.path.exists(rp):
                    issues.append({"level": "error", "message": "rendering.{} missing: {}".format(key, rp)})
    return issues


def discover_input(input_dir: str, max_files: int = 200):
    """Discover case.json, task.md and material files. Returns structured list."""
    found = []
    if not os.path.isdir(input_dir):
        return found, ["input dir not found: {}".format(input_dir)]
    errors = []
    for root, dirs, files in os.walk(input_dir):
        for name in files:
            p = os.path.join(root, name)
            rel = os.path.relpath(p, input_dir)
            lower = name.lower()
            if lower in ("case.json", "task.md"):
                kind = "case" if lower == "case.json" else "task"
            elif lower.endswith((".pdf", ".png", ".jpg", ".jpeg", ".svg", ".tex", ".md", ".pptx", ".docx", ".html", ".odp")):
                kind = "material"
            else:
                continue
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            found.append({"path": p, "rel": rel, "type": kind, "bytes": size})
            if len(found) >= max_files:
                errors.append("material discovery truncated at {} files".format(max_files))
                break
        if len(found) >= max_files:
            break
    return found, errors


def quote_offset_in(text: str, quote: str):
    """Return start index of quote in extracted text, or None when absent."""
    if not quote or text is None:
        return None
    idx = text.find(quote)
    return idx if idx >= 0 else None
