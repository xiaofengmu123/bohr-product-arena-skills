# -*- coding: utf-8 -*-
"""Deterministic conservation checks for the academic-polish Skill.

The checks compare each edit unit's ``original`` and ``revised`` text and
flag two severities:

* ``errors``   — hard failures: numbers/units/citations/figures lost, empty
                 revised text, missing provenance. These must be fixed to zero
                 before finalizing.
* ``warnings`` — claim-strength drift (hedges dropped or strong verbs added),
                 terminology inconsistency. Each warning must be reverted or
                 given an explicit justification in the unit's ``reason``.

Standard library only; no network; no randomness; same input -> same report.
"""

import json
import re

# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------

# Number with optional unit suffix (verbatim preservation requires the whole
# token to survive editing, so "3.2 mM" -> "3.2 uM" is also caught).
_UNIT = (r"(?:%|‰|％|°C|℃|K|kg|g|mg|μg|ug|ng|mol|mmol|M|mM|μM|uM|nM|"
         r"L|mL|μL|uL|s|ms|min|h|hr|Hz|kHz|MHz|GHz|V|mV|kV|A|mA|W|kW|"
         r"J|kJ|Pa|kPa|MPa|bar|m|cm|mm|μm|um|nm|Å|Da|kDa|bp|kb|rpm|"
         r"g/mL|mg/mL|μg/mL|µg/mL)")
NUM_UNIT_RE = re.compile(
    r"[+-]?(?:\d+\.\d+|\.\d+|\d+)(?:[eE][+-]?\d+)?\s*" + _UNIT)

# Citation markers, figure/table/equation pointers.
CITE_RE = re.compile(
    r"\[\d+(?:\s*(?:[,，;；]|[-–—])\s*\d+)*\]"            # [1], [1,2], [1–3]
    r"|\((?:[A-Z][A-Za-z\-']+|\S{2,8}?)(?:\s+(?:et\s+al\.?|等))?"
    r"(?:,?\s*\d{4}[a-z]?)*(?:\s*;\s*[^()]*)*\)"          # (Wang, 2020)
    r"|(?:Fig\.?|Figure|Table|Eq\.?|Equation)\s*\d+[a-z]?"
    r"|[图図]\s*\d+|[表]\s*\d+|[式]\s*\(?\s*\d+\s*\)?")


def extract_numbers(text):
    """Return unique number(+unit) tokens present in *text*."""
    return sorted(set(m.group(0).strip() for m in NUM_UNIT_RE.finditer(text)))


def extract_citations(text):
    """Return unique citation / figure / table / equation markers."""
    return sorted(set(m.group(0).strip() for m in CITE_RE.finditer(text)))


def extract_tokens(text, tokens):
    """Substring match with ASCII word boundaries; CJK matched literally."""
    found = []
    for t in tokens:
        if t.isascii():
            pat = r"(?<![A-Za-z])" + re.escape(t) + r"(?![A-Za-z])"
            if re.search(pat, text, re.IGNORECASE):
                found.append(t)
        elif t in text:
            found.append(t)
    return found


# ---------------------------------------------------------------------------
# Claim-strength vocabulary
# ---------------------------------------------------------------------------

HEDGES = [
    # Chinese
    "可能", "或许", "也许", "大概", "似乎", "倾向于", "初步", "有待",
    "尚需", "未明确", "潜在", "可推测", "推测", "未证实",
    # English
    "may", "might", "could", "possibly", "perhaps", "likely", "probably",
    "suggest", "suggesting", "suggests", "indicate", "indicates",
    "appear", "appears", "preliminary", "tentative", "potentially",
    "seem", "seems", "to our knowledge", "speculate", "may be",
]

STRONG = [
    # Chinese
    "证明", "证实", "确立", "必然", "毫无疑问", "无疑", "显著优于",
    # English
    "prove", "proves", "proved", "proven", "demonstrate", "demonstrates",
    "demonstrated", "confirm", "confirms", "confirmed", "establish",
    "established", "definitely", "undoubtedly", "certainly", "conclusively",
]

# Neutral reporting verbs are intentionally excluded from STRONG so routine
# "显示/show/indicate" replacements do not fire false positives.


# ---------------------------------------------------------------------------
# Per-unit checks
# ---------------------------------------------------------------------------

def check_unit(unit):
    """Return (errors, warnings) for one edit unit."""
    errors, warnings = [], []
    uid = unit.get("id", "?")
    orig = unit.get("original") or ""
    rev = unit.get("revised") or ""

    # 1. Coverage: a source sentence must produce a revision or an explicit
    #    no-change marker; never an empty revision.
    if orig.strip() and not rev.strip():
        if unit.get("edit_type") not in ("no_change", "pending_human", "skip"):
            errors.append(
                {"unit": uid, "check": "coverage",
                 "detail": "original non-empty but revised empty"})

    # 2. Numbers and units must survive verbatim.
    missing = [t for t in extract_numbers(orig) if t not in extract_numbers(rev)]
    if missing:
        errors.append(
            {"unit": uid, "check": "number_unit",
             "detail": "tokens missing from revised", "tokens": missing})

    # 3. Citations / figure / table pointers must survive verbatim.
    missing_c = [t for t in extract_citations(orig)
                 if t not in extract_citations(rev)]
    if missing_c:
        errors.append(
            {"unit": uid, "check": "citation_ref",
             "detail": "markers missing from revised", "tokens": missing_c})

    # 4. Claim-strength drift: hedges dropped or strong verbs added.
    o_hedge = set(extract_tokens(orig, HEDGES))
    r_hedge = set(extract_tokens(rev, HEDGES))
    dropped = sorted(o_hedge - r_hedge)
    if dropped:
        warnings.append(
            {"unit": uid, "check": "hedge_dropped",
             "detail": "qualifier possibly removed; revert or justify",
             "tokens": dropped})

    o_strong = set(extract_tokens(orig, STRONG))
    r_strong = set(extract_tokens(rev, STRONG))
    added = sorted(r_strong - o_strong)
    if added:
        warnings.append(
            {"unit": uid, "check": "claim_strengthened",
             "detail": "strong verb introduced; revert or justify",
             "tokens": added})

    # 5. Provenance: any fact-bearing sentence needs a traceable locator+quote.
    facts = unit.get("facts") or []
    if facts and not unit.get("evidence"):
        errors.append(
            {"unit": uid, "check": "provenance",
             "detail": "fact-bearing sentence lacks evidence locator/quote"})

    return errors, warnings


# ---------------------------------------------------------------------------
# Whole-run checks
# ---------------------------------------------------------------------------

def check_glossary(edits, glossary):
    """Warn when a revised text uses a non-canonical variant of a term."""
    warnings = []
    terms = (glossary or {}).get("terms") or []
    variants = []
    for t in terms:
        canonical = t.get("canonical") or ""
        for v in t.get("variants") or []:
            if v and v != canonical:
                variants.append((canonical, v))
    for unit in edits.get("units") or []:
        rev = unit.get("revised") or ""
        for canonical, variant in variants:
            if variant in rev:
                warnings.append(
                    {"unit": unit.get("id", "?"), "check": "terminology",
                     "detail": "non-canonical variant used; prefer canonical",
                     "tokens": [variant, "->", canonical]})
    return warnings


def check_all(edits, glossary):
    """Return {"errors": [...], "warnings": [...], "stats": {...}}."""
    errors, warnings = [], []
    units = (edits or {}).get("units") or []
    for u in units:
        e, w = check_unit(u)
        errors.extend(e)
        warnings.extend(w)
    warnings.extend(check_glossary(edits, glossary))
    return {
        "errors": errors,
        "warnings": warnings,
        "stats": {
            "units": len(units),
            "errors": len(errors),
            "warnings": len(warnings),
        },
    }


def write_report(report, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return report


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        return None
