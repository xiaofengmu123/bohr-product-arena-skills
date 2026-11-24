#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic, stdlib-only consistency checks for academic translation.

Pure functions: text in -> structured findings out. No network, no randomness,
no side effects, so repeated runs produce identical reports. Used by main.py
(extract / checks subcommands) and reusable as a standalone library.

Scope (best-effort, honest heuristics; findings are warnings for human review):
  - numbers: extract digits with magnitude words (万/亿 vs million/billion) and
    units, normalize to a canonical value, compare source vs target.
  - citations: bracket-numeric, author-year parenthetical/naked, et al.
  - terms: abbreviation expansions and candidate acronyms.
  - structure: segment text into paragraphs/captions/tables/formulas/references
    with stable line locators, then compare segment-type counts.
"""
import re

# ---------------------------------------------------------------------------
# Number extraction with magnitude handling
# ---------------------------------------------------------------------------
# Magnitudes map to base-10 multipliers. Single-letter "m"/"k" are intentionally
# excluded to avoid ambiguity with the SI unit metre (e.g. "5 m" = 5 metres).
MAG_FACTORS = {
    "thousand": 1e3, "千": 1e3,
    "万": 1e4, "萬": 1e4,
    "十万": 1e5, "百万": 1e6, "million": 1e6,
    "千万": 1e7, "亿": 1e8, "億": 1e8,
    "十亿": 1e9, "billion": 1e9,
    "万亿": 1e12, "兆": 1e12, "trillion": 1e12,
}
_MAG_WORDS = "|".join(re.escape(w) for w in sorted(MAG_FACTORS, key=len, reverse=True))

# Units ordered longest-first so "mL"/"mmHg" beat "m"/"mg" etc.
_COMPOUND_UNITS = [
    "mg/kg", "mg/dL", "μg/mL", "ug/mL", "ng/mL", "pg/mL", "fg/mL",
    "mmol/L", "μmol/L", "umol/L", "nmol/L", "pmol/L", "mol/L", "mEq/L",
    "IU/mL", "U/mL", "IU/L", "U/L", "mL/min", "L/min", "mL/h", "L/h",
    "km/h", "m/s", "cm/s", "mm/s", "m/min", "rpm", "W/m2", "W/m²",
    "kg/m3", "kg/m³", "g/cm3", "g/cm³", "g/mL", "cells/μL", "cells/uL",
    "cells/mL", "copies/mL", "cfu/mL", "μmol/m2/s", "mmol/kg",
]
_UNIT_LIST = [
    "mmHg", "mmol", "μmol", "nmol", "pmol", "μg", "ug", "µm", "um", "nm",
    "mm", "cm", "km", "mg", "kg", "mL", "ml", "kDa", "Da", "ms", "min",
    "hrs", "hr", "days", "day", "wk", "yrs", "years", "year", "kPa", "MPa",
    "GPa", "kHz", "MHz", "GHz", "Hz", "kW", "MW", "GW", "eV", "keV", "MeV",
    "GeV", "TeV", "kJ", "bp", "nt", "kb", "Mb", "Gb", "IU", "fold", "times",
    "°C", "°F", "%", "‰", "°", "K", "g", "m", "s", "h", "L", "l", "J", "N",
    "V", "A", "W", "Ω", "×", "x",
] + _COMPOUND_UNITS
_UNIT_WORDS = "|".join(re.escape(u) for u in sorted(set(_UNIT_LIST), key=len, reverse=True))

# ASCII-only word boundaries so numbers adjacent to CJK characters still match
# (e.g. "约120万"), while "a123"/"3D" do not split inside alphanumerics.
_LEFT = r"(?<![A-Za-z0-9_])"
_RIGHT = r"(?![A-Za-z0-9_])"

_NUM_RE = re.compile(
    _LEFT
    + r"(\d[\d,]*(?:\.\d+)?)"           # group 1: number
    + r"(?:\s*([eE][+-]?\d+))?"         # group 2: exponent
    + r"(?:\s*(" + _MAG_WORDS + r"))?"  # group 3: magnitude word
    + r"(?:\s*(" + _UNIT_WORDS + r"))?"  # group 4: unit
    + _RIGHT,
    re.IGNORECASE,
)


def canonical_number(num_str, mag_word=None, exponent=None):
    """Return a comparable canonical string for a number token.

    Strips thousands separators, applies an explicit exponent and any magnitude
    word, then formats with %.15g so that "1.2 million", "1.2e6", "120万" and
    "1200000" all normalize to the same canonical form.
    """
    try:
        f = float(num_str.replace(",", ""))
    except (ValueError, TypeError):
        return (num_str or "").replace(",", "")
    if exponent:
        try:
            f *= 10 ** int(exponent[1:])
        except (ValueError, TypeError):
            pass
    if mag_word:
        f *= MAG_FACTORS.get(mag_word.lower(), 1.0)
    return format(f, ".15g")


def extract_numbers(text):
    if not text:
        return []
    out = []
    for m in _NUM_RE.finditer(text):
        raw = m.group(0).strip()
        num = m.group(1)
        mag = (m.group(3) or "").strip() or None
        unit = (m.group(4) or "").strip() or None
        out.append({
            "raw": raw,
            "value": num,
            "exponent": (m.group(2) or "").strip() or None,
            "magnitude": mag,
            "unit": unit,
            "canonical": canonical_number(num, mag, m.group(2)),
        })
    return out


# ---------------------------------------------------------------------------
# Citation extraction
# ---------------------------------------------------------------------------
_BRACKET_CITE = re.compile(r"\[\s*\d+(?:\s*[,，;；、\-–]\s*\d+)*\s*\]")
_AUTHOR_YEAR_PAREN = re.compile(r"\(([^()]{0,120}?(?:19|20)\d{2}[a-z]?[^()]{0,40})\)")
_AUTHOR_YEAR_NAKED = re.compile(
    r"\b([A-Z][A-Za-z\-']+(?:\s+(?:and|&)\s+[A-Z][A-Za-z\-']+)?"
    r"(?:\s+et\s+al\.?)?)\s*\(((?:19|20)\d{2}[a-z]?)\)"
)
_ET_AL = re.compile(r"\bet\s+al\.?", re.IGNORECASE)


def extract_citations(text):
    if not text:
        return []
    out = []
    for m in _BRACKET_CITE.finditer(text):
        out.append({"raw": m.group(0), "kind": "bracket_numeric"})
    for m in _AUTHOR_YEAR_PAREN.finditer(text):
        out.append({"raw": m.group(0), "kind": "author_year_paren"})
    for m in _AUTHOR_YEAR_NAKED.finditer(text):
        out.append({"raw": m.group(0), "kind": "author_year_naked"})
    for m in _ET_AL.finditer(text):
        out.append({"raw": m.group(0), "kind": "et_al"})
    return out


# ---------------------------------------------------------------------------
# Term extraction (English abbreviations / expansions; Chinese handled via
# the human-confirmed glossary, not guessed here)
# ---------------------------------------------------------------------------
_TERM_PAREN = re.compile(r"\b([A-Z][A-Za-z][A-Za-z \-]{1,60}?)\s*\(([A-Z][A-Z]{1,20})\)")
_ABBR = re.compile(r"\b([A-Z][A-Za-z0-9\-]{1,20}(?:/[A-Z][A-Za-z0-9\-]{1,20})?)\b")


def extract_terms(text):
    if not text:
        return []
    out = []
    for m in _TERM_PAREN.finditer(text):
        out.append({"expansion": m.group(1).strip(), "abbr": m.group(2).strip(),
                    "kind": "abbr_expansion"})
    seen = set()
    for m in _ABBR.finditer(text):
        a = m.group(1)
        if a in seen or a.isdigit():
            continue
        seen.add(a)
        out.append({"abbr": a, "expansion": None, "kind": "candidate_abbr"})
    return out


# ---------------------------------------------------------------------------
# Segmentation with stable line locators
# ---------------------------------------------------------------------------
_CAPTION = re.compile(r"^(图|表|Fig\.?|Figure|Table)\s*(\d+)", re.IGNORECASE)
_HEADING_REF = re.compile(r"^(参考文献|References?|Bibliography|REFERENCES)", re.IGNORECASE)
_MATH_OPEN = re.compile(r"(\$\$|\\\[|\\begin\{equation|\\begin\{align)")
_MATH_CLOSE = re.compile(r"(\$\$|\\\]|\\end\{equation|\\end\{align)")


def _looks_like_table(body):
    lines = [l for l in body.split("\n") if l.strip()]
    if len(lines) >= 2:
        if sum(1 for l in lines if l.count("|") >= 2) >= 2:
            return True
    return False


def _classify(body, start, end, filename, in_refs):
    first = body.strip().split("\n")[0]
    if in_refs:
        kind = "reference"
    elif _CAPTION.match(first):
        kind = "caption"
    elif _MATH_OPEN.search(body):
        kind = "formula"
    elif _looks_like_table(body):
        kind = "table"
    else:
        kind = "paragraph"
    return {
        "id": f"{filename}#L{start}-L{end}",
        "type": kind,
        "start_line": start,
        "end_line": end,
        "text": body,
    }


def segment_text(text, filename="<text>"):
    """Split text into translatable units with stable line-number locators."""
    if text is None:
        return []
    lines = text.split("\n")
    segs = []
    cur = []
    cur_start = None
    in_refs = False
    in_math = False

    def flush():
        nonlocal cur, cur_start
        if not cur:
            return
        body = "\n".join(cur).strip("\n")
        if body.strip():
            segs.append(_classify(body, cur_start, cur_start + len(cur) - 1,
                                  filename, in_refs))
        cur = []
        cur_start = None

    for i, line in enumerate(lines):
        n = i + 1
        if in_math:
            cur.append(line)
            if _MATH_CLOSE.search(line):
                in_math = False
                flush()
            continue
        if _MATH_OPEN.search(line):
            if cur:
                flush()
            in_math = True
            cur_start = n
            cur.append(line)
            if _MATH_CLOSE.search(line):
                in_math = False
                flush()
            continue
        if line.strip() == "":
            flush()
            continue
        if _HEADING_REF.search(line.strip()):
            if cur:
                flush()
            in_refs = True
            cur_start = n
            cur.append(line)
            continue
        if cur_start is None:
            cur_start = n
        cur.append(line)
    flush()
    return segs


# ---------------------------------------------------------------------------
# Comparisons
# ---------------------------------------------------------------------------
def compare_numbers(src_text, tgt_text):
    s = extract_numbers(src_text)
    t = extract_numbers(tgt_text)
    s_keys = {}
    for x in s:
        s_keys.setdefault(x["canonical"], []).append(x["raw"])
    t_keys = {}
    for x in t:
        t_keys.setdefault(x["canonical"], []).append(x["raw"])
    missing = sorted(k for k in s_keys if k not in t_keys)
    added = sorted(k for k in t_keys if k not in s_keys)
    return {
        "source_count": len(s),
        "target_count": len(t),
        "missing_in_target": missing,
        "added_in_target": added,
        "sample_source": [x["raw"] for x in s[:50]],
        "sample_target": [x["raw"] for x in t[:50]],
    }


def _norm_cite(raw):
    return re.sub(r"\s+", "", raw).lower()


def compare_citations(src_text, tgt_text):
    s = extract_citations(src_text)
    t = extract_citations(tgt_text)
    s_norm = {_norm_cite(x["raw"]): x for x in s if x["kind"] != "et_al"}
    t_norm = {_norm_cite(x["raw"]): x for x in t if x["kind"] != "et_al"}
    missing = sorted(k for k in s_norm if k not in t_norm)
    added = sorted(k for k in t_norm if k not in s_norm)
    return {
        "source_count": len(s),
        "target_count": len(t),
        "missing_in_target": missing,
        "added_in_target": added,
    }


def compare_structure(src_text, tgt_text, src_name="source", tgt_name="target"):
    ss = segment_text(src_text, src_name)
    ts = segment_text(tgt_text, tgt_name)
    s_counts, t_counts = {}, {}
    for seg in ss:
        s_counts[seg["type"]] = s_counts.get(seg["type"], 0) + 1
    for seg in ts:
        t_counts[seg["type"]] = t_counts.get(seg["type"], 0) + 1
    mismatched = {
        k: {"source": s_counts.get(k, 0), "target": t_counts.get(k, 0)}
        for k in set(s_counts) | set(t_counts)
        if s_counts.get(k, 0) != t_counts.get(k, 0)
    }
    return {
        "source_segments": len(ss),
        "target_segments": len(ts),
        "type_counts_source": s_counts,
        "type_counts_target": t_counts,
        "mismatched_types": mismatched,
    }
