#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — robustness-first implementation for the academic-deck-organizer skill.

This module is the single source of truth for input discovery, contract
normalization, result scaffolding, incremental merge, validation and evidence
checks.  Design rule: *no function raises on bad input*.  Every failure (empty
directory, malformed JSON, wrong encoding, missing parameter, oversized file)
is captured as a structured diagnostic so the orchestrator can still write a
legal partial `/app/output/result.json` and exit with code 0.

Standard library only; compatible with Python 3.9+.
"""

from __future__ import annotations

import datetime
import json
import os


# --------------------------------------------------------------------------- #
# Tunables
# --------------------------------------------------------------------------- #

MAX_TEXT_BYTES = 2 * 1024 * 1024          # cap for text files we attempt to parse
MAX_JSON_BYTES = 8 * 1024 * 1024          # cap for JSON; oversized -> skipped
TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")

VALID_STATUSES = (
    "complete",
    "partial",
    "failed",
    "insufficient_evidence",
    "input_error",
)

CASE_FILE_NAMES = ("case.json", "case.yaml", "case.yml")
TASK_FILE_NAMES = ("task.md", "task.txt")
MATERIAL_EXTS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif", ".tif", ".tiff",
    ".pptx", ".ppt", ".odp", ".docx", ".doc", ".md", ".txt", ".tex", ".csv",
    ".tsv", ".xlsx", ".html", ".htm", ".bib",
}
TEMPLATE_EXTS = {".pptx", ".ppt", ".odp", ".potx", ".thmx", ".html", ".css", ".pptm"}

# Default output contract.  These are the topic-specific fields required by the
# task's output-requirements table; they are flattened into result.json at the
# top level.  When /input/case.json carries its own "output_contract", that
# object wins field-by-field and every unknown key is preserved verbatim.
DEFAULT_CONTRACT = {
    "report_goal": None,      # checkable goal: audience, duration, page count, acceptance
    "audience": None,         # prior knowledge, concerns, likely objections
    "narrative": [],          # ordered page plan with inclusion/drop rationale
    "pages": [],              # [{page_index,title,single_claim,evidence_strength,figures,speaker_notes,source_refs}]
    "editable_deck": None,    # {path,format,template,notes}
    "evidence_map": [],       # [{page_index,claim,locator,quote,source_file,confidence}]
    "qa_prep": [],            # [{question,answer_keypoints,referenced_pages,backup_pages}]
    "rendering": None,        # {source_files,exports,fonts,template,change_record}
}

CASE_ID_KEYS = ("id", "case_id", "armiId", "challenge_id", "caseId", "task_id")


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return -1


def read_text_robust(path: str, max_bytes: int = MAX_TEXT_BYTES) -> dict:
    """Read a text file trying several encodings.  Never raises.

    Returns {"text", "encoding", "bytes", "skipped", "error"}.  Oversized files
    are truncated at ``max_bytes`` and flagged ``skipped=True``; unreadable
    files return an empty text with an error string.
    """
    size = _file_size(path)
    if size < 0:
        return {"text": "", "encoding": None, "bytes": 0, "skipped": False,
                "error": "cannot stat file"}
    if size > max_bytes:
        skip = True
        read_len = max_bytes + 4          # +4 avoids splitting the final code point
    else:
        skip = False
        read_len = max(size, 0)
    try:
        with open(path, "rb") as fh:
            raw = fh.read(read_len) if read_len else b""
    except OSError as exc:
        return {"text": "", "encoding": None, "bytes": 0, "skipped": False,
                "error": "read error: %s" % exc}

    for enc in TEXT_ENCODINGS:
        try:
            text = raw.decode(enc)
            return {"text": text, "encoding": enc, "bytes": size, "skipped": skip,
                    "error": None}
        except (UnicodeDecodeError, LookupError):
            continue
    text = raw.decode("latin-1", errors="replace")
    return {"text": text, "encoding": "latin-1(replace)", "bytes": size,
            "skipped": skip, "error": "no clean encoding; latin-1 fallback used"}


def load_json_robust(path: str, max_bytes: int = MAX_JSON_BYTES) -> dict:
    """Parse a JSON file; bad JSON or oversized files return a diagnostic.

    Returns {"data", "error", "encoding", "skipped"}.  ``data`` is None on any
    failure; ``error`` explains why.  BOM / encoding issues are handled by
    read_text_robust.
    """
    size = _file_size(path)
    if size > max_bytes:
        return {"data": None,
                "error": "file too large (%d bytes > %d); skipped" % (size, max_bytes),
                "encoding": None, "skipped": True}
    rd = read_text_robust(path, max_bytes)
    if rd["error"] and not rd["text"]:
        return {"data": None, "error": rd["error"], "encoding": None, "skipped": False}
    text = rd["text"]
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        return {"data": None, "error": "bad json: %s" % exc,
                "encoding": rd["encoding"], "skipped": rd["skipped"]}
    return {"data": data, "error": None, "encoding": rd["encoding"],
            "skipped": rd["skipped"]}


def list_files_recursive(root: str) -> list:
    """Return a sorted flat list of file metadata under ``root`` (no crash)."""
    if not root or not os.path.isdir(root):
        return []
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            p = os.path.join(dirpath, fn)
            out.append({"path": p, "name": fn,
                        "ext": os.path.splitext(fn)[1].lower(),
                        "bytes": _file_size(p)})
    return out


# --------------------------------------------------------------------------- #
# Input discovery: scan first, assume later
# --------------------------------------------------------------------------- #

def discover_inputs(input_dir: str) -> dict:
    files = list_files_recursive(input_dir)
    disc = {
        "input_dir": input_dir,
        "exists": bool(input_dir) and os.path.isdir(input_dir),
        "case_files": [], "task_files": [], "materials": [],
        "templates": [], "other": [],
    }
    for f in files:
        base = f["name"].lower()
        if base in CASE_FILE_NAMES:
            disc["case_files"].append(f)
        elif base in TASK_FILE_NAMES:
            disc["task_files"].append(f)
        elif f["ext"] in TEMPLATE_EXTS:
            disc["templates"].append(f)
        elif f["ext"] in MATERIAL_EXTS:
            disc["materials"].append(f)
        else:
            disc["other"].append(f)
    return disc


def _prefer(files: list, names) -> dict | None:
    for wanted in names:
        for f in files:
            if f["name"].lower() == wanted:
                return f
    return files[0] if files else None


# --------------------------------------------------------------------------- #
# Contract handling
# --------------------------------------------------------------------------- #

def extract_case_id(case) -> str | None:
    if not isinstance(case, dict):
        return None
    for key in CASE_ID_KEYS:
        val = case.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, (int, float)):
            return str(val)
    return None


def extract_contract(case) -> tuple:
    """Return (merged_contract_dict, provided_keys_list).

    ``case["output_contract"]`` is authoritative when present; its unknown keys
    are preserved verbatim.  Known topic fields fall back to DEFAULT_CONTRACT so
    the result skeleton is always well-formed.
    """
    provided = {}
    if isinstance(case, dict) and isinstance(case.get("output_contract"), dict):
        provided = case["output_contract"]
    merged = {}
    for key, default in DEFAULT_CONTRACT.items():
        merged[key] = provided[key] if key in provided else default
    for key, value in provided.items():
        if key not in merged:
            merged[key] = value
    return merged, sorted(provided.keys())


# --------------------------------------------------------------------------- #
# Result skeleton + incremental merge
# --------------------------------------------------------------------------- #

def build_sources_list(discovered: dict) -> list:
    out = []
    for kind in ("case_files", "task_files", "materials", "templates"):
        for f in discovered.get(kind, []):
            out.append({"path": f["path"], "type": kind, "bytes": f["bytes"],
                        "read": False, "status": "listed", "note": ""})
    return out


def mark_source(sources: list, path: str, read: bool, status: str, note: str = ""):
    for s in sources:
        if s.get("path") == path:
            s["read"] = read
            s["status"] = status
            if note:
                s["note"] = note
            return True
    return False


def build_skeleton(case_obj, task_text: str, discovered: dict, contract: dict,
                   errors: list, degradations: list) -> dict:
    """Build a legal *partial* result.json.  This is what gets written within the
    first 3 minutes; every later step only deep-merges improvements into it."""
    case_id = extract_case_id(case_obj) or "unknown"
    result = dict(contract)
    result["case_id"] = case_id
    result["status"] = _derive_status(discovered, case_obj, task_text, errors)
    result["sources"] = build_sources_list(discovered)
    result["uncertainty"] = []
    result["pending_human"] = []
    result["errors"] = list(errors)
    result["degradations"] = list(degradations)
    result["change_log"] = [{
        "timestamp": now_iso(),
        "action": "scaffold",
        "scope": "partial result skeleton written; incremental updates follow",
    }]
    result["runtime"] = {"started_at": now_iso(),
                         "generator": "academic-deck-organizer/core_impl.py"}
    return result


def _derive_status(discovered: dict, case_obj, task_text: str, errors: list) -> str:
    if not discovered.get("exists"):
        return "input_error"
    has_inputs = bool(discovered.get("case_files") or discovered.get("task_files")
                      or discovered.get("materials") or discovered.get("templates"))
    if not has_inputs:
        return "insufficient_evidence"
    if case_obj is None and not (task_text or "").strip():
        return "insufficient_evidence"
    if errors:
        return "partial"
    return "partial"


def deep_merge(base, updates):
    """Recursively merge ``updates`` into ``base`` (dicts merge, lists replace)."""
    if not isinstance(base, dict) or not isinstance(updates, dict):
        return updates
    out = dict(base)
    for key, value in updates.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def save_json(path: str, obj) -> str | None:
    """Atomic JSON write; returns an error string on failure, else None."""
    directory = os.path.dirname(path)
    if directory:
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as exc:
            return "mkdir error: %s" % exc
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        return "write error: %s" % exc
    return None


# --------------------------------------------------------------------------- #
# Validation + evidence checks (report only; never crash)
# --------------------------------------------------------------------------- #

def validate_result(result: dict, output_dir: str) -> list:
    """Return a list of {level, msg} diagnostics for the result object."""
    issues = []
    if not isinstance(result, dict):
        return [{"level": "error", "msg": "result.json is not a JSON object"}]
    for key in ("case_id", "status", "sources", "uncertainty", "change_log"):
        if key not in result:
            issues.append({"level": "error", "msg": "missing required field: %s" % key})
    status = result.get("status")
    if status not in VALID_STATUSES:
        issues.append({"level": "error",
                       "msg": "invalid status %r; must be one of %s"
                              % (status, ", ".join(VALID_STATUSES))})
    pages = result.get("pages")
    if pages is None:
        issues.append({"level": "warning", "msg": "pages is null (not yet produced)"})
    elif not isinstance(pages, list):
        issues.append({"level": "error", "msg": "pages must be a list"})
    else:
        for i, page in enumerate(pages):
            if not isinstance(page, dict):
                issues.append({"level": "error", "msg": "pages[%d] is not an object" % i})
                continue
            for field in ("page_index", "title", "single_claim"):
                if not page.get(field):
                    issues.append({"level": "warning",
                                   "msg": "pages[%d] missing %s" % (i, field)})
    # evidence map must be a list of objects with locator + quote
    emap = result.get("evidence_map")
    if emap is not None and not isinstance(emap, list):
        issues.append({"level": "error", "msg": "evidence_map must be a list"})
    # editable artifacts: warn when a complete status claims artifacts that are missing
    deck = result.get("editable_deck")
    if isinstance(deck, dict) and deck.get("path"):
        p = os.path.join(output_dir, deck["path"]) if not os.path.isabs(deck["path"]) \
            else deck["path"]
        if not os.path.exists(p):
            issues.append({"level": "warning",
                           "msg": "editable_deck.path does not exist: %s" % deck["path"]})
    return issues


def evidence_check(result: dict, sources_manifest: dict | None = None) -> list:
    """Structural + (optional) verbatim-quote checks.  Returns diagnostics."""
    issues = []
    emap = result.get("evidence_map")
    if emap is None:
        return [{"level": "warning", "msg": "evidence_map is null"}]
    if not isinstance(emap, list):
        return [{"level": "error", "msg": "evidence_map must be a list"}]
    for i, entry in enumerate(emap):
        if not isinstance(entry, dict):
            issues.append({"level": "error", "msg": "evidence_map[%d] not an object" % i})
            continue
        locator = entry.get("locator")
        quote = entry.get("quote")
        if not locator:
            issues.append({"level": "error",
                           "msg": "evidence_map[%d] missing locator" % i})
        if not quote:
            issues.append({"level": "warning",
                           "msg": "evidence_map[%d] missing verbatim quote" % i})
            continue
        if sources_manifest:
            found = _quote_found(quote, sources_manifest, entry.get("source_file"))
            if found is False:
                issues.append({"level": "warning",
                               "msg": "evidence_map[%d] quote not found in manifest: %r"
                                      % (i, quote[:80])})
    return issues


def _quote_found(quote: str, manifest: dict, source_file) -> bool | None:
    """Return True/False if a decisive match is possible, None if not checkable."""
    if not isinstance(manifest, dict) or not quote:
        return None
    needle = quote.strip()
    if not needle:
        return None
    if source_file:
        text = manifest.get(source_file)
        if isinstance(text, str):
            return needle in text
        return None
    for text in manifest.values():
        if isinstance(text, str) and needle in text:
            return True
    return False


# --------------------------------------------------------------------------- #
# Self-test: run the full CLI against degenerate inputs end-to-end
# --------------------------------------------------------------------------- #

def run_selftests(main_py: str, python: str) -> tuple:
    """Run degenerate-input scenarios against main.py.  Returns (passed, report).

    Used by scripts/selftest.py; kept here so main.py can also expose
    `--selftest` without duplicating logic.
    """
    import subprocess
    import tempfile

    def run_case(name, files, expect_status):
        tmp = tempfile.mkdtemp(prefix="adt-selftest-")
        out = os.path.join(tmp, "output")
        if files is None:
            # deliberately never create the input directory
            inp = os.path.join(tmp, "nonexistent-input")
        else:
            inp = os.path.join(tmp, "input")
            os.makedirs(inp, exist_ok=True)
            for rel, content in files.items():
                p = os.path.join(inp, rel)
                parent = os.path.dirname(p)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(p, "wb") as fh:
                    fh.write(content if isinstance(content, bytes) else content.encode("utf-8"))
        proc = subprocess.run(
            [python, main_py, "--input", inp, "--output", out, "--scaffold-only"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60)
        result_path = os.path.join(out, "result.json")
        ok = proc.returncode == 0 and os.path.exists(result_path)
        status = None
        detail = "stdout: %s | stderr: %s" % (proc.stdout[:200], proc.stderr[:200])
        if ok:
            try:
                with open(result_path, "r", encoding="utf-8") as fh:
                    obj = json.load(fh)
                status = obj.get("status")
                ok = (expect_status is None or status == expect_status)
            except (OSError, json.JSONDecodeError) as exc:
                ok = False
                detail += " | result read fail: %s" % exc
        return {"name": name, "ok": ok, "status": status, "expected": expect_status,
                "detail": detail}

    cases = [
        ("empty input dir", {}, "insufficient_evidence"),
        ("missing input dir (nonexistent)", None, "input_error"),
        ("bad JSON case.json", {"case.json": "not a valid json document",
                                "task.md": "# task\nmaterials follow\n"},
         "partial"),
        ("oversized case.json (skipped)", {"case.json": b"{" + b" " * (MAX_JSON_BYTES + 1) + b"}",
                                           "task.md": "# task\n"},
         "partial"),
        ("valid minimal case", {"case.json": '{"id": "case-demo", "output_contract": {"pages": []}}',
                                "task.md": "# task\nStudy X: method M gives result R.\n"},
         "partial"),
    ]
    report = []
    passed = 0
    for name, files, expect in cases:
        entry = run_case(name, files, expect)
        report.append(entry)
        if entry["ok"]:
            passed += 1
    return passed, report
