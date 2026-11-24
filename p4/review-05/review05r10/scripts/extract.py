#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic utilities for the source-bound academic compliance audit.

Design principle
----------------
This module NEVER reaches a verdict. It discovers inputs, reads text robustly,
attempts a first-pass PDF text extraction, resolves the case-declared review
taxonomy, and emits two kinds of machine evidence:

  * ``observations``   — candidate regex flags with verbatim windows (never
                         conclusions; the reviewing agent must disprove each);
  * ``counter_hints``  — passages that already *resolve* a candidate (granted
                         permission, licence, de-identification, ethics waiver),
                         forcing an adversarial re-check before confirmation.

Standard library only. No third-party dependencies.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

TEXT_SUFFIXES = {
    ".txt", ".md", ".json", ".csv", ".tsv", ".xml", ".html", ".htm",
    ".tex", ".rst", ".yaml", ".yml", ".py", ".log", ".bib", ".ris",
}
PDF_SUFFIXES = {".pdf"}
MAX_TEXT_BYTES = 2 * 1024 * 1024  # single text file cap; larger files are skipped

DEFAULT_FACETS = [
    "academic_integrity", "citation", "privacy", "copyright", "ethics",
    "submission", "quality", "statistics", "method", "logic", "norm",
]
DEFAULT_FINDING_TYPES = ["fact_error", "insufficient_evidence", "norm_violation", "suggestion"]
DEFAULT_SEVERITIES = ["critical", "major", "minor", "info"]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Robust reading helpers
# ---------------------------------------------------------------------------

def read_text_robust(path, max_bytes=MAX_TEXT_BYTES):
    """Return (text, error). Tries utf-8-sig, utf-8, then GB18030/latin-1.

    Never raises on decode failures: a broken file degrades to a recorded
    error instead of crashing the run.
    """
    p = Path(path)
    if not p.exists():
        return "", f"not found: {p}"
    try:
        size = p.stat().st_size
    except OSError as exc:
        return "", f"stat failed: {exc}"
    if size > max_bytes:
        return "", f"skipped: file exceeds {max_bytes} bytes"
    raw = p.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(encoding), None
        except (UnicodeDecodeError, LookupError):
            continue
    return "", "undecodable text"


def read_json_robust(path):
    """Return (obj, error). Bad JSON is reported, never raised."""
    text, err = read_text_robust(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"


def atomic_write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Input discovery
# ---------------------------------------------------------------------------

def discover(root):
    """Classify files under `root`. Returns a list of dicts."""
    items = []
    root = Path(root)
    if not root.exists():
        return items
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        name = p.name.lower()
        if name == "case.json" or (name.startswith("case") and name.endswith(".json")):
            role = "case"
        elif name == "task.md" or (name.startswith("task") and name.endswith(".md")):
            role = "task"
        elif p.suffix.lower() in TEXT_SUFFIXES:
            role = "material"
        elif p.suffix.lower() in PDF_SUFFIXES:
            role = "pdf"
        else:
            role = "artifact"
        rel = p.relative_to(root).as_posix() if p.is_relative_to(root) else p.as_posix()
        items.append({"path": p, "relpath": rel, "name": p.name, "role": role, "size": size})
    return items


def input_fingerprint(root):
    """Deterministic digest over discovered file relpaths + sizes. Two runs
    over the same input tree yield the same fingerprint, which proves that a
    later rerun saw identical material."""
    h = hashlib.sha256()
    for it in discover(root):
        h.update(it["relpath"].encode("utf-8"))
        h.update(b"\x00")
        h.update(str(it["size"]).encode("ascii"))
        h.update(b"\x00")
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Output-contract defaulting
# ---------------------------------------------------------------------------

def default_for(value):
    """Recursively produce an empty-but-typed default for a contract schema."""
    if isinstance(value, dict):
        return {k: default_for(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    return None


def flatten_leafs(contract):
    """Flatten a contract dict into leaf key -> value pairs for top-level
    convenience, so graders can read contract fields without nesting."""
    out = {}
    stack = [(contract, "")]
    while stack:
        node, prefix = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                full = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
                stack.append((value, full))
        elif prefix:
            out[prefix] = node
    return out


# ---------------------------------------------------------------------------
# Taxonomy resolution (case-declared facets/types override built-in defaults)
# ---------------------------------------------------------------------------

def _as_str_list(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return []


def _facet_name(entry):
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        for key in ("name", "id", "key", "facet"):
            if entry.get(key):
                return str(entry[key])
    return None


def _pick(case_obj, contract, keys):
    for key in keys:
        val = case_obj.get(key) if isinstance(case_obj, dict) else None
        if val:
            return val
        val = contract.get(key) if isinstance(contract, dict) else None
        if val:
            return val
    return None


def resolve_taxonomy(case_obj, contract):
    """Return the authoritative review taxonomy.

    Facets may be declared as plain names or as dicts carrying per-facet
    ``finding_types`` and ``evidence_required`` constraints. The latter are
    kept in ``facets_schema`` so the validator can enforce typed findings.
    """
    raw_facets = _pick(case_obj, contract, [
        "review_facets", "facets", "audit_facets", "review_dimensions", "checklist"])
    facets_schema = {}
    facet_names = []
    if raw_facets:
        for entry in raw_facets:
            name = _facet_name(entry)
            if not name:
                continue
            if name not in facets_schema:
                facet_names.append(name)
            if isinstance(entry, dict):
                facets_schema[name] = {
                    "finding_types": _as_str_list(entry.get("finding_types")) or None,
                    "evidence_required": _as_str_list(entry.get("evidence_required")) or None,
                    "revision_required": bool(entry.get("revision_required", True)),
                }
            else:
                facets_schema.setdefault(name, {
                    "finding_types": None, "evidence_required": None, "revision_required": True})
    if not facet_names:
        facet_names = list(DEFAULT_FACETS)
        facets_schema = {n: {"finding_types": None, "evidence_required": None,
                             "revision_required": True} for n in facet_names}

    finding_types = _as_str_list(_pick(case_obj, contract, ["finding_types"]))
    if not finding_types:
        finding_types = list(DEFAULT_FINDING_TYPES)
    categories = _as_str_list(_pick(case_obj, contract, ["categories"]))
    if not categories:
        categories = list(facet_names)
    severities = _as_str_list(_pick(case_obj, contract, ["severities", "severity_levels"]))
    if not severities:
        severities = list(DEFAULT_SEVERITIES)
    return {
        "facet_names": facet_names,
        "facets_schema": facets_schema,
        "finding_types": finding_types,
        "categories": categories,
        "severities": severities,
    }


# ---------------------------------------------------------------------------
# PDF first-pass text extraction (best effort, never fatal)
# ---------------------------------------------------------------------------

def pdf_to_text(pdf_path, out_txt_path):
    """Try `pdftotext`; return (text, error). Fallback leaves a recorded note."""
    pdftotext = shutil.which("pdftotext")
    if pdftotext is None:
        return "", "pdftotext not found; parse with the platform pdf-text-tool"
    try:
        proc = subprocess.run(
            [pdftotext, "-layout", str(pdf_path), str(out_txt_path)],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", f"pdftotext failed: {exc}"
    if proc.returncode != 0:
        return "", (proc.stderr or "pdftotext non-zero exit").strip()[:300]
    if not Path(out_txt_path).exists():
        return "", "pdftotext produced no output"
    text, err = read_text_robust(out_txt_path)
    return text, err


# ---------------------------------------------------------------------------
# Candidate observation rules (flags only, no verdicts)
# ---------------------------------------------------------------------------

RULES = [
    ("citation_bracket", "citation", r"\[\d+(?:[,\-–]\s*\d+)*\]"),
    ("citation_author_year", "citation",
     r"\([A-Z][A-Za-z'’-]+(?:\s+(?:et\s+al\.?|and|&))?\s*,?\s*(?:19|20)\d{2}[a-z]?\)"),
    ("doi_occurrence", "citation", r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+"),
    ("ethics_consent", "ethics",
     r"(?:informed\s+consent|知情同意|伦理|ethics\s+(?:approval|committee|review)|IRB|IACUC|批件|审查委员会)"),
    ("privacy_identifier", "privacy",
     r"[\w.+-]+@[\w-]+\.[A-Za-z.]+|(?:\+?\d[\d\s()\-]{7,}\d)|\b\d{17}[\dXx]\b|\b\d{15}(?:\d{2}[0-9Xx])?\b"),
    ("privacy_participant", "privacy",
     r"(?:patient|participant|subject|受试者|患者|姓名|identifiable|可识别|脱敏)"),
    ("funding_conflict", "academic_integrity",
     r"(?:funding|grant|supported\s+by|资助|基金|conflict\s+of\s+interest|competing\s+interest|利益冲突|declaration)"),
    ("copyright_reuse", "copyright",
     r"(?:reproduced|adapted\s+from|permission|copyright|©|license|CC\s+BY|licensed|授权|转载|许可)"),
    ("statistical_claim", "statistics",
     r"(?:p\s*[<>=]\s*0?\.\d+|p\s*=\s*0?\.\d+|n\s*=\s*\d+|±|confidence\s+interval|\bCI\b|odds\s+ratio|hazard\s+ratio|significant|显著|标准差|标准误)"),
    ("method_reporting", "method",
     r"(?:Materials\s+and\s+Methods|方法|sample\s+size|randomi[sz]ed|blinded|双盲|对照|control\s+group)"),
    ("figure_table", "copyright", r"(?:Figure\s+\d+|Table\s+\d+|图\s*\d+|表\s*\d+)"),
    ("submission_hint", "submission",
     r"(?:journal|conference|submission|投稿|期刊|会议|word\s+limit|page\s+limit|作者指南|author\s+guideline)"),
    ("author_contribution", "academic_integrity",
     r"(?:author\s+contribution|CRediT|contributions|作者贡献)"),
    ("duplication_hint", "academic_integrity",
     r"(?:reuse|duplicate|prior\s+publication|已发表|重复发表|self-plagiarism|复用)"),
    ("logic_hedge", "logic",
     r"(?:therefore|thus|hence|因此|所以|consequently|implies|表明|由此)"),
]

# Passages that already resolve a candidate. When a reuse/privacy/ethics flag
# co-occurs with one of these, the agent must search for and weigh the
# counter-evidence before confirming a violation.
COUNTER_RULES = [
    ("permission_granted", "copyright", r"(?:permission\s+(?:granted|obtained)|已获(?:得)?授权|经授权|授权许可|with\s+permission)"),
    ("license_stated", "copyright", r"(?:CC\s+BY[-NCSAD]*|MIT\s+license|Apache\s*2\.0|GPL|BSD|公共领域|public\s+domain|open\s+access)"),
    ("deidentified", "privacy", r"(?:de-?identified|anonymi[sz]ed|脱敏|匿名化|去标识)"),
    ("consent_waived", "ethics", r"(?:waiver|豁免|exempt)"),
    ("irb_approved", "ethics", r"(?:IRB|ethics\s+committee|IACUC|伦理委员会|审批编号|批件号|approval\s+number)"),
]


def _compile(rule_list):
    compiled = []
    for rid, cat, pat in rule_list:
        try:
            compiled.append((rid, cat, re.compile(pat, re.IGNORECASE)))
        except re.error:
            compiled.append((rid, cat, re.compile(r"", re.IGNORECASE)))
    return compiled


_COMPILED = _compile(RULES)
_COUNTER_COMPILED = _compile(COUNTER_RULES)


def _scan(text_items, rule_list):
    results = []
    n = 0
    for item in text_items:
        rel = item.get("relpath", "?")
        text = item.get("text", "")
        if not text:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            for rid, cat, pattern in rule_list:
                for m in pattern.finditer(line):
                    n += 1
                    start, end = m.start(), m.end()
                    lo = max(0, start - 40)
                    hi = min(len(line), end + 60)
                    results.append({
                        "id": f"obs-{n:04d}",
                        "category": cat,
                        "rule": rid,
                        "file": rel,
                        "locator": f"{rel}#L{line_no}:C{start}-{end}",
                        "match": line[start:end],
                        "quote": line[lo:hi],
                    })
    return results


def extract_observations(text_items):
    return _scan(text_items, _COMPILED)


def extract_counter_hints(text_items):
    return _scan(text_items, _COUNTER_COMPILED)


# ---------------------------------------------------------------------------
# Quote re-location (independent verification)
# ---------------------------------------------------------------------------

def normalize(text):
    return re.sub(r"\s+", " ", str(text)).strip()


def locate_quote(text, quote):
    """Return (locator, mode, ratio) if `quote` appears in `text`, else
    (None, None, 0.0). Normalises whitespace; falls back to fuzzy matching."""
    q = normalize(quote)
    if not q:
        return None, "empty", 0.0
    lines = text.splitlines()
    for i, line in enumerate(lines, start=1):
        nl = normalize(line)
        if q in nl:
            idx = nl.find(q)
            return f"L{i}:C{max(0, idx)}", "exact", 1.0
    best, best_i = 0.0, None
    for i, line in enumerate(lines, start=1):
        ratio = difflib.SequenceMatcher(None, normalize(line), q).ratio()
        if ratio > best:
            best, best_i = ratio, i
    if best >= 0.85:
        return f"L{best_i}", "fuzzy", round(best, 3)
    return None, None, 0.0
