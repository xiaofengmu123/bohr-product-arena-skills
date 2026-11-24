#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic evidence extraction and text utilities for the compliance audit.

Design principle
----------------
This module NEVER reaches a verdict. It discovers input files, reads them
robustly, attempts a first-pass PDF text extraction, and emits *candidate
observations* (regex flags) together with verbatim context windows. A reviewing
agent must verify every candidate against the original text before any finding
is promoted into result.json. This keeps "machine flags" and "human verdicts"
separately auditable and suppresses false positives.

Standard library only. No third-party dependencies.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

TEXT_SUFFIXES = {
    ".txt", ".md", ".json", ".csv", ".tsv", ".xml", ".html", ".htm",
    ".tex", ".rst", ".yaml", ".yml", ".py", ".log", ".bib", ".ris",
}
PDF_SUFFIXES = {".pdf"}
MAX_TEXT_BYTES = 2 * 1024 * 1024  # single text file cap; larger files are skipped

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
        items.append({
            "path": p,
            "relpath": _relpath(p, root),
            "name": p.name,
            "role": role,
            "size": size,
        })
    return items


def _relpath(p, root):
    """Return p relative to root; works on Python < 3.9 too."""
    try:
        if p.is_relative_to(root):
            return p.relative_to(root).as_posix()
    except AttributeError:
        pass
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.as_posix()


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
# PDF first-pass text extraction (best effort, never fatal)
# ---------------------------------------------------------------------------

def pdf_to_text(pdf_path, out_txt_path):
    """Try `pdftotext`; return (text, error). Fallback leaves a recorded note."""
    pdftotext = shutil_which("pdftotext")
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


def shutil_which(name):
    import shutil
    return shutil.which(name)


# ---------------------------------------------------------------------------
# Candidate observation rules (flags only, no verdicts)
# ---------------------------------------------------------------------------

# Each rule maps a named regex to a category. The reviewing agent decides
# whether a candidate is a real finding, an excluded false positive, or a
# downgraded suggestion.
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

_COMPILED = []
for _rid, _cat, _pat in RULES:
    try:
        _COMPILED.append((_rid, _cat, re.compile(_pat, re.IGNORECASE)))
    except re.error:
        _COMPILED.append((_rid, _cat, re.compile(r"", re.IGNORECASE)))


def extract_observations(text_items):
    """Scan parsed texts and return candidate observations.

    `text_items` is a list of dicts with relpath, role, text. Each candidate
    carries a verbatim context window (quote) so it can later be independently
    re-located in the source file.
    """
    obs = []
    n = 0
    for item in text_items:
        rel = item.get("relpath", "?")
        text = item.get("text", "")
        if not text:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            for rid, cat, pattern in _COMPILED:
                for m in pattern.finditer(line):
                    n += 1
                    start, end = m.start(), m.end()
                    lo = max(0, start - 40)
                    hi = min(len(line), end + 60)
                    window = line[lo:hi]
                    obs.append({
                        "id": f"obs-{n:04d}",
                        "category": cat,
                        "rule": rid,
                        "file": rel,
                        "locator": f"{rel}#L{line_no}:C{start}-{end}",
                        "match": line[start:end],
                        "quote": window,
                    })
    return obs


# ---------------------------------------------------------------------------
# Quote re-location (independent verification)
# ---------------------------------------------------------------------------

def normalize(text):
    return re.sub(r"\s+", " ", str(text)).strip()


def locate_quote(file_path, quote):
    """Return a locator string if `quote` appears verbatim in `file_path`,
    else None. Normalises whitespace; falls back to fuzzy line matching."""
    text, err = read_text_robust(file_path)
    if err:
        return None
    q = normalize(quote)
    if not q:
        return None
    if q in normalize(text):
        # report the first line containing the quote
        for line_no, line in enumerate(text.splitlines(), start=1):
            if q in normalize(line):
                idx = normalize(line).find(q)
                return f"{file_path.name}#L{line_no}:C{max(0, idx)}"
        return "located"
    # fuzzy fallback: best line-level similarity
    import difflib
    best, best_line = 0.0, None
    for line_no, line in enumerate(text.splitlines(), start=1):
        ratio = difflib.SequenceMatcher(None, normalize(line), q).ratio()
        if ratio > best:
            best, best_line = ratio, line_no
    if best >= 0.85:
        return f"{file_path.name}#L{best_line} (fuzzy {best:.2f})"
    return None
