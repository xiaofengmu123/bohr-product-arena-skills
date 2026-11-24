#!/usr/bin/env python3
"""Shared, dependency-free helpers for the academic-translation Skill.

Design goal: every read/write of /app/output/result.json goes through this
module so the declared output_contract can never drift out of sync with what
the runtime actually persisted. Only the Python standard library is used, so
the package runs in a bare CPython image.

The translation workflow is built around four locks, each of which maps to a
deterministic, re-runnable check implemented here or in main.py:

  * term lock   -- glossary.json keeps one canonical target term per source term
                   and records allowed variants; the checker flags mixed usage.
  * number lock -- numeric values are compared after normalising 万/亿 and
                   million/billion so 350万 == 3,500,000 == 3.5 million.
  * structure lock -- sentence/segment alignment must be contiguous, carry a
                   source locator, and keep formula/table/caption/footnote
                   markers in place.
  * boundary lock -- negation scope, causal strength, hedge strength and
                   citation markers are preserved, never silently rewritten.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def skill_root() -> str:
    """Directory of the installed Skill (parent of scripts/)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_skill_path(rel: str) -> str:
    """Resolve a script-relative path (e.g. scripts/check_consistency.py)
    against the Skill root even when the process CWD is /app/work."""
    if os.path.isabs(rel):
        return rel
    return os.path.normpath(os.path.join(skill_root(), rel))


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Tolerant I/O (BOM, bad encoding, bad JSON must degrade, never crash)
# ---------------------------------------------------------------------------

def read_json(path, default=None):
    if not path or not os.path.exists(path):
        return default
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return json.load(fh)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, OSError):
            continue
    return default


def read_text(path, max_bytes=2 * 1024 * 1024):
    """Read text with an upper size cap; oversize returns "" so the caller
    records a truncation instead of loading a 500 MB dump into memory."""
    if not path or not os.path.exists(path):
        return ""
    try:
        if os.path.getsize(path) > max_bytes:
            return ""
    except OSError:
        return ""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except UnicodeDecodeError:
            continue
    return ""


def atomic_write_json(path, obj):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def atomic_write_text(path, text):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Output contract
# ---------------------------------------------------------------------------

# Required top-level fields. deliverables / fact_source_map / version_boundary
# are the three fields named by the task's "Skill 运行后的输出要求"; the rest
# are the runtime-contract fields (case_id, status, sources, uncertainty,
# pending_human, change_log). limitations and runtime are populated as well.
CONTRACT_REQUIRED = [
    "case_id",
    "status",
    "deliverables",
    "fact_source_map",
    "version_boundary",
    "sources",
    "uncertainty",
    "pending_human",
    "change_log",
]
CONTRACT_OPTIONAL = ["limitations", "runtime"]
VERSION_BOUNDARY_KEYS = ["modifications", "human_confirm", "material_gaps",
                         "anomalies", "editable"]
FACT_KINDS = {"number", "term", "symbol", "citation", "figure", "claim"}
VALID_STATUS = {"success", "partial", "failed", "in_progress",
                "insufficient_evidence", "input_error"}


def new_result(case_id, status="in_progress"):
    result = {
        "case_id": case_id,
        "status": status,
        "deliverables": [],
        "fact_source_map": [],
        "version_boundary": {
            "modifications": [],
            "human_confirm": [],
            "material_gaps": [],
            "anomalies": [],
            "editable": True,
        },
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "limitations": [],
        "runtime": {"started_at": utc_now(), "ended_at": None},
    }
    append_change_log(result, "init", "result.json",
                      "skeleton created; all output_contract fields aligned")
    return result


def append_change_log(result, action, key, detail):
    result.setdefault("change_log", []).append({
        "time": utc_now(),
        "action": action,
        "key": key,
        "detail": detail,
    })


def load_result(out_dir):
    return read_json(os.path.join(out_dir, "result.json"))


def save_result(out_dir, result):
    atomic_write_json(os.path.join(out_dir, "result.json"), result)


def validate_contract(result):
    """Return (ok, problems). Verifies the whole declared output contract."""
    problems = []
    if not isinstance(result, dict):
        return False, ["result is not a JSON object"]
    for key in CONTRACT_REQUIRED:
        if key not in result:
            problems.append("missing top-level field: %s" % key)
    if result.get("status") not in VALID_STATUS:
        problems.append("unknown status: %r" % (result.get("status"),))
    vb = result.get("version_boundary")
    if isinstance(vb, dict):
        for key in VERSION_BOUNDARY_KEYS:
            if key not in vb:
                problems.append("missing version_boundary field: %s" % key)
        if not isinstance(vb.get("editable"), bool):
            problems.append("version_boundary.editable must be boolean")
    else:
        problems.append("version_boundary must be an object")
    if not isinstance(result.get("deliverables"), list):
        problems.append("deliverables must be a list")
    if not isinstance(result.get("fact_source_map"), list):
        problems.append("fact_source_map must be a list")
    if not isinstance(result.get("sources"), list):
        problems.append("sources must be a list")
    if not isinstance(result.get("uncertainty"), list):
        problems.append("uncertainty must be a list")
    if not isinstance(result.get("pending_human"), list):
        problems.append("pending_human must be a list")
    if not isinstance(result.get("change_log"), list):
        problems.append("change_log must be a list")
    for entry in result.get("change_log", []):
        for f in ("time", "action", "key", "detail"):
            if f not in entry:
                problems.append("change_log entry missing field: %s" % f)
    for item in result.get("fact_source_map", []):
        if not isinstance(item, dict):
            problems.append("fact_source_map item not an object")
            continue
        if item.get("kind") not in FACT_KINDS:
            problems.append("fact_source_map item has unknown kind: %r" % (item.get("kind"),))
        if "source" not in item and "locator" not in item:
            problems.append("fact_source_map item lacks source/locator: %r" % (item.get("item"),))
    return (len(problems) == 0), problems


# ---------------------------------------------------------------------------
# Case discovery (do not assume fixed file names)
# ---------------------------------------------------------------------------

def discover_case(input_dir):
    brief = {
        "case_id": None,
        "output_contract_fields": [],
        "has_task_md": False,
        "materials": [],
        "missing": [],
    }
    case_path = os.path.join(input_dir, "case.json")
    case = read_json(case_path)
    if isinstance(case, dict):
        brief["case_id"] = (case.get("case_id") or case.get("id")
                            or case.get("caseId") or case.get("challenge_id"))
        oc = case.get("output_contract")
        if isinstance(oc, dict):
            brief["output_contract_fields"] = list(oc.keys())
        elif isinstance(oc, list):
            brief["output_contract_fields"] = [str(x) for x in oc]
        else:
            # Task-default contract when case.json does not declare one.
            brief["output_contract_fields"] = ["deliverables",
                                               "fact_source_map",
                                               "version_boundary"]
    brief["has_task_md"] = os.path.isfile(os.path.join(input_dir, "task.md"))
    if os.path.isdir(input_dir):
        for name in sorted(os.listdir(input_dir)):
            full = os.path.join(input_dir, name)
            if not os.path.isfile(full):
                continue
            if name in ("case.json", "task.md"):
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                size = None
            brief["materials"].append({"path": full, "name": name, "size": size})
    if not brief["has_task_md"]:
        brief["missing"].append("task.md")
    if not isinstance(case, dict):
        brief["missing"].append("case.json")
    return brief


# ---------------------------------------------------------------------------
# Number lock: 万/亿 and million/billion normalisation
# ---------------------------------------------------------------------------

_MAGNITUDE = {
    "万": 10 ** 4, "萬": 10 ** 4, "千": 10 ** 3,
    "亿": 10 ** 8, "億": 10 ** 8,
    "thousand": 10 ** 3, "million": 10 ** 6,
    "billion": 10 ** 9, "trillion": 10 ** 12,
}
# Sort longest first so "million" wins over a bare "m" never present here.
_MAG_SORTED = sorted(_MAGNITUDE.items(), key=lambda kv: -len(kv[0]))

_NUM_RE = re.compile(r"\d[\d,，\s\u00a0]*(?:\.\d+)?")


def extract_number_values(text):
    """Return a list of (normalised_value, raw_token) pairs.

    Handles 3,500,000 / 3 500 000 / 350万 / 3.5 million / 3.5 billion.
    Ranges, scientific notation and signed tolerances are intentionally left
    for human confirmation: this checker never pretends to resolve them.
    """
    out = []
    if not text:
        return out
    for m in _NUM_RE.finditer(text):
        raw = m.group(0)
        tail = text[m.end():m.end() + 12].lstrip().lower()
        mag = 1
        matched_word = ""
        for word, factor in _MAG_SORTED:
            if tail.startswith(word):
                mag = factor
                matched_word = word
                break
        cleaned = raw.replace(",", "").replace("，", "").replace(" ", "").replace("\u00a0", "")
        try:
            val = float(cleaned)
        except ValueError:
            continue
        out.append((val * mag, raw + matched_word))
    return out


def values_match(a, b, rel_tol=1e-9):
    if a == b:
        return True
    denom = max(abs(a), abs(b), 1e-12)
    return abs(a - b) / denom <= rel_tol


def fact_number_fidelity(fact_source_map, translation_text):
    """Authoritative check: every critical number recorded in fact_source_map
    must survive into the translation. Returns list of problems."""
    problems = []
    tgt_vals = [v for v, _ in extract_number_values(translation_text)]
    for item in fact_source_map:
        if item.get("kind") != "number":
            continue
        value = item.get("value")
        if value is None:
            problems.append({"severity": "error", "item": item.get("item", "?"),
                             "detail": "number fact has no value",
                             "locator": item.get("locator", ""),
                             "quote": item.get("quote", "")})
            continue
        vals = []
        if isinstance(value, (int, float)):
            vals = [float(value)]
        else:
            vals = [v for v, _ in extract_number_values(str(value))]
        if not vals:
            problems.append({"severity": "warning", "item": item.get("item", "?"),
                             "detail": "number value not parseable; needs human check",
                             "locator": item.get("locator", ""),
                             "quote": item.get("quote", "")})
            continue
        missing = [v for v in vals
                   if not any(values_match(v, t) for t in tgt_vals)]
        if missing:
            problems.append({"severity": "error", "item": item.get("item", "?"),
                             "detail": "critical number missing from translation",
                             "locator": item.get("locator", ""),
                             "quote": item.get("quote", "")})
    return problems


def heuristic_number_diff(source_text, translation_text):
    """Lossy whole-document scan. Warnings only: this is a safety net, not the
    authoritative record (fact_source_map is authoritative)."""
    problems = []
    src = [v for v, _ in extract_number_values(source_text)]
    tgt = [v for v, _ in extract_number_values(translation_text)]
    for v in src:
        if not any(values_match(v, t) for t in tgt):
            problems.append({"severity": "warning", "item": repr(v),
                             "detail": "numeric value appears in source but not in translation; confirm it was not dropped"})
    return problems


# ---------------------------------------------------------------------------
# Term lock: glossary consistency
# ---------------------------------------------------------------------------

def check_glossary_terms(translation_text, glossary):
    problems = []
    if not isinstance(glossary, dict):
        return problems
    terms = glossary.get("terms", [])
    if not isinstance(terms, list):
        return problems
    for term in terms:
        if not isinstance(term, dict):
            continue
        canonical = term.get("canonical", "")
        variants = term.get("variants", [])
        if not isinstance(variants, list) or not variants:
            variants = [canonical]
        present = [v for v in variants if v and v in translation_text]
        if len(set(present)) > 1:
            problems.append({"severity": "warning", "item": canonical,
                             "detail": "multiple variants used in translation: " + ", ".join(sorted(set(present)))})
        if term.get("require_canonical") and canonical and canonical not in translation_text:
            problems.append({"severity": "warning", "item": canonical,
                             "detail": "canonical term missing from translation"})
    return problems


# ---------------------------------------------------------------------------
# Citation lock: keep every citation marker, or register the omission
# ---------------------------------------------------------------------------

_BRACKET_CITE = re.compile(r"\[\d+(?:\s*[,，、]\s*\d+)*\]|\[\d+\s*[-–—]\s*\d+\]")
_AUTHOR_YEAR = re.compile(r"\([A-Z][A-Za-z]+(?:\s+et\s+al\.?)?[,\s]+\d{4}[a-z]?\)"
                          r"|\([A-Z][A-Za-z]+\s+et\s+al\.[,\s]+\d{4}[a-z]?\)")


def extract_citations(text):
    out = []
    for m in _BRACKET_CITE.finditer(text or ""):
        out.append(m.group(0))
    for m in _AUTHOR_YEAR.finditer(text or ""):
        out.append(m.group(0))
    return out


def check_citation_fidelity(source_text, translation_text):
    problems = []
    src = extract_citations(source_text)
    tgt = " ".join(extract_citations(translation_text))
    for c in src:
        if c not in tgt:
            problems.append({"severity": "warning", "item": c,
                             "detail": "citation marker present in source but not found in translation"})
    return problems


# ---------------------------------------------------------------------------
# Structure lock: bilingual alignment integrity
# ---------------------------------------------------------------------------

def check_alignment(alignment):
    problems = []
    if isinstance(alignment, dict):
        segs = alignment.get("segments") or alignment.get("items") or []
    else:
        segs = alignment or []
    if not isinstance(segs, list) or not segs:
        return [{"severity": "error", "item": "alignment",
                 "detail": "no alignment segments found"}]
    ids = []
    for i, seg in enumerate(segs):
        if not isinstance(seg, dict):
            problems.append({"severity": "error", "item": "seg#%d" % i,
                             "detail": "segment is not an object"})
            continue
        sid = seg.get("src_id") if seg.get("src_id") is not None else (seg.get("id") if seg.get("id") is not None else i)
        ids.append(sid)
        if not seg.get("src"):
            problems.append({"severity": "error", "item": str(sid),
                             "detail": "source segment empty"})
        if not seg.get("tgt") and not seg.get("preserved"):
            problems.append({"severity": "warning", "item": str(sid),
                             "detail": "target segment empty; set preserved=true if a formula/table is kept as-is"})
        if not seg.get("locator"):
            problems.append({"severity": "warning", "item": str(sid),
                             "detail": "segment has no source locator"})
    numeric = [x for x in ids if isinstance(x, int)]
    if numeric and sorted(numeric) != list(range(min(numeric), max(numeric) + 1)):
        problems.append({"severity": "warning", "item": "alignment",
                         "detail": "segment ids not contiguous (duplicate or gap)"})
    return problems


# ---------------------------------------------------------------------------
# Self-check of the whole package (reused by selftest.py)
# ---------------------------------------------------------------------------

def run_full_checks(out_dir, work_dir, source_file=None, translation_file=None,
                    glossary_file=None, alignment_file=None):
    """Run every deterministic check and return a report dict."""
    result = load_result(out_dir) or {}
    problems = []
    source_text = read_text(source_file) if source_file else ""
    if not source_text and os.path.isdir(work_dir):
        for cand in ("source_text.txt", "source.txt", "source_extracted.txt"):
            p = os.path.join(work_dir, cand)
            if os.path.exists(p):
                source_text = read_text(p)
                break
    translation_text = read_text(translation_file) if translation_file else ""

    if not translation_text:
        # Fall back to the first editable text deliverable in /app/output.
        for d in result.get("deliverables", []):
            p = d.get("path", "")
            if not os.path.isabs(p):
                p = os.path.join(out_dir, os.path.basename(p))
            if os.path.isfile(p) and str(p).lower().endswith((".md", ".txt", ".tex", ".json")):
                translation_text = read_text(p)
                if translation_text:
                    break

    contract_ok, contract_problems = validate_contract(result)
    if not contract_ok:
        problems.extend({"severity": "error", "item": "contract",
                         "detail": p} for p in contract_problems)

    problems.extend(fact_number_fidelity(result.get("fact_source_map", []),
                                         translation_text))
    if source_text and translation_text:
        problems.extend(heuristic_number_diff(source_text, translation_text))
        problems.extend(check_citation_fidelity(source_text, translation_text))
    if glossary_file:
        problems.extend(check_glossary_terms(translation_text,
                                             read_json(glossary_file) or {}))
    if alignment_file:
        problems.extend(check_alignment(read_json(alignment_file) or []))

    errors = [p for p in problems if p.get("severity") == "error"]
    warnings = [p for p in problems if p.get("severity") == "warning"]
    report = {
        "time": utc_now(),
        "contract_ok": contract_ok,
        "contract_problems": contract_problems,
        "n_errors": len(errors),
        "n_warnings": len(warnings),
        "errors": errors,
        "warnings": warnings,
    }
    return report
