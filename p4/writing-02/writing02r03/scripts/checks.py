# -*- coding: utf-8 -*-
"""Deterministic conservation checks for academic-language polishing.

Pure standard library. No network, no randomness, no model calls.
Every function is a pure function of its inputs: same input -> same output.
Reports use a stable ordering so two runs over identical work files are equal.

Design (v2 upgrade over naive diffing):
  * numbers + units are extracted as (normalized_number, canonical_unit) pairs,
    so "1,234" vs "1234" and "μg" vs "ug" do not produce false positives, while
    a real change ("mg" -> "g", "32.4" -> "32.0") is still caught.
  * citation markers, figure/table/equation pointers and hedge tokens are
    normalized before multiset comparison.
  * errors block finalization; warnings require an explicit resolution note
    (never silently strengthened/weakened).
"""

import re
from collections import Counter

# ---------------------------------------------------------------------------
# Numbers and units
# ---------------------------------------------------------------------------

_NUM_SCI = r"\d[\d,]*(?:\.\d+)?\s*[×xX]\s*10\s*\^?\s*[-+]?\d+"
_NUM_PLAIN = r"\d[\d,]*(?:\.\d+)?(?:[eE][+-]?\d+)?"
NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_.])(" + _NUM_SCI + r"|" + _NUM_PLAIN + r")(?![A-Za-z0-9_])"
)

# Unit literals, including case variants, unicode variants and common compound
# units. Sorted longest-first at match time so "mg/mL" is not truncated to "mg".
UNIT_LITERALS = [
    "%", "‰", "°C", "℃", "°F", "Å", "Ω", "s.e.m.", "s.d.", "S.D.", "a.u.", "u.a.",
    "mg/mL", "mg/ml", "μg/mL", "µg/mL", "ug/mL", "ng/mL", "pg/mL",
    "g/L", "mol/L", "mmol/L", "μmol/L", "µmol/L", "U/mL", "IU/mL", "IU/L",
    "cells/mL", "cells/μL", "cells/µL", "kg/m2", "kg/m²", "kg/m3", "kg/m³",
    "g/cm3", "g/cm³", "mg/m3", "μg/m3", "mg/kg", "μg/kg", "µg/kg",
    "cm-1", "s-1", "M-1", "m/s", "km/h", "mL/min", "μL/min", "µL/min",
    "Angstrom", "minutes", "minute", "seconds", "second", "months", "month",
    "weeks", "week", "hours", "hour", "years", "year", "days", "day",
    "kPa", "MPa", "GPa", "μmol", "mmol", "kHz", "MHz", "GHz", "THz",
    "keV", "MeV", "GeV", "kDa", "MDa", "μM", "µM", "nM", "pM", "mM",
    "μL", "µL", "nL", "mL", "ml", "μg", "µg", "mg", "ng", "pg", "kg",
    "nm", "mm", "cm", "km", "ms", "min", "hrs", "hr", "Hz", "atm",
    "bar", "mol", "rpm", "ohm", "bp", "kb", "Mb", "nt", "aa", "sec",
    "secs", "yr", "rad", "sr", "Bq", "Gy", "Sv", "Da", "eV", "kJ", "MJ",
    "kW", "MW", "GW", "mV", "mA", "Pa", "J", "W", "V", "A", "K", "M",
    "m", "g", "s", "h", "L", "l",
]

_UNIT_ALT = "|".join(
    re.escape(u) for u in sorted(set(UNIT_LITERALS), key=len, reverse=True)
)
UNIT_RE = re.compile(r"\s*(" + _UNIT_ALT + r")(?![A-Za-z0-9])")

# Canonical spelling: collapse case/unicode synonyms that do NOT change meaning,
# while preserving case that DOES change meaning (M molar vs m metre, K vs k).
_UNIT_CANON = {
    "l": "L", "ml": "mL", "ul": "μL", "µl": "μL", "μl": "μL",
    "ug": "μg", "µg": "μg", "um": "μm", "µm": "μm", "µM": "μM",
    "µL": "μL", "µmol": "μmol", "hr": "h", "hrs": "h", "sec": "s",
    "secs": "s", "℃": "°C", "ohm": "Ω", "u.a.": "a.u.", "S.D.": "s.d.",
    "mg/ml": "mg/mL", "ug/mL": "μg/mL", "µg/mL": "μg/mL",
    "kg/m²": "kg/m2", "kg/m³": "kg/m3", "g/cm³": "g/cm3",
    "days": "day", "years": "year", "yr": "year", "months": "month",
    "weeks": "week", "hours": "hour", "minutes": "minute", "seconds": "second",
}


def normalize_number(raw):
    """Normalize a numeric literal for conservation comparison.

    1,234 -> 1234; 3.2×10^5 -> 3.2e5; 0.50 -> 0.5. Scientific and plain forms
    that are spelled differently remain distinct (representation change is a
    signal the agent must justify, not silently accept).
    """
    s = raw.strip()
    s = re.sub(r"\s*[×xX]\s*10\s*\^?\s*", "e", s)
    s = s.replace(",", "")
    m = re.fullmatch(r"(-?\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?", s)
    if not m:
        return s.lower()
    intp, frac, exp = m.group(1), m.group(2), m.group(3)
    intp = intp.lstrip("0") or "0"
    if frac is not None:
        frac = frac.rstrip("0")
    mant = intp + ("." + frac if frac else "")
    if exp is not None:
        return "%se%s" % (mant, str(int(exp)))
    return mant


def extract_measurements(text):
    """Return Counter of (normalized_number, canonical_unit) pairs."""
    if not text:
        return Counter()
    out = Counter()
    for m in NUMBER_RE.finditer(text):
        num = normalize_number(m.group(0))
        tail = text[m.end():m.end() + 16]
        um = UNIT_RE.match(tail)
        unit = um.group(1) if um else ""
        unit = _UNIT_CANON.get(unit, unit)
        out[(num, unit)] += 1
    return out


# ---------------------------------------------------------------------------
# Citations, figure/table/equation pointers
# ---------------------------------------------------------------------------

CIT_RE = re.compile(
    r"\[\s*\d+(?:\s*[-–—,，;；]\s*\d+)*\s*\]"
    r"|\([12][0-9]{3}[a-z]?(?:\s*[,;，；]\s*[12][0-9]{3}[a-z]?)*\)"
    r"|\(\s*[A-Z][A-Za-z&.\-']*(?:\s+[A-Za-z&.\-']+)*\s*,\s*[12][0-9]{3}[a-z]?\)"
    r"|\b(?:ref|Ref|Refs|reference|Reference)\.\s*\d+"
)


def extract_citations(text):
    if not text:
        return Counter()
    return Counter(re.sub(r"\s+", "", x) for x in CIT_RE.findall(text))


FIG_TABLE_RE = re.compile(
    r"\b(?:Fig|Figs|Figure|Figures)\s*\.?\s*\d+\s*[a-z]?"
    r"|\b(?:Table|Tables)\s*\.?\s*\d+\s*[a-z]?"
    r"|\b(?:Eq|Eqs|Equation|Equations)\s*\.?\s*(?:\(\s*\d+\s*\)|\d+)"
    r"|\b(?:Sect|Section|Sections|Sec)\s*\.?\s*\d+"
)


def normalize_figref(s):
    s = s.lower()
    s = re.sub(r"\b(figs?|figures?)\s*\.?\s*", "fig", s)
    s = re.sub(r"\b(tables?)\s*\.?\s*", "table", s)
    s = re.sub(r"\b(eqs?|equations?)\s*\.?\s*", "eq", s)
    s = re.sub(r"\b(sects?|sections?|secs?)\s*\.?\s*", "sec", s)
    s = s.replace("(", "").replace(")", "")
    s = re.sub(r"\s+", "", s)
    return s


def extract_figrefs(text):
    if not text:
        return Counter()
    return Counter(normalize_figref(x) for x in FIG_TABLE_RE.findall(text))


# ---------------------------------------------------------------------------
# Hedges (限定词) — modal hedges and quantity hedges, kept separate to reduce
# false positives ("about the method" is not a hedge; "about 30%" is).
# ---------------------------------------------------------------------------

MODAL_EN = [
    "may", "might", "could", "suggest", "suggests", "suggested", "suggesting",
    "indicate", "indicates", "indicated", "indicating", "appear", "appears",
    "appeared", "appearing", "likely", "unlikely", "possibly", "probably",
    "potentially", "tend to", "tends to", "tended to", "seem", "seems",
    "seemed", "preliminary", "preliminarily", "reportedly", "to our knowledge",
    "to the best of our knowledge", "in some cases", "often", "typically",
    "generally", "frequently", "may be", "might be", "could be",
    "appears to", "appeared to", "is thought", "are thought", "is believed",
    "are believed", "tentative", "tentatively", "putative", "putatively",
    "presumably", "perhaps", "arguably",
]
QUANT_PRE = [
    "about", "around", "approximately", "approx.", "roughly", "nearly",
    "up to", "at least", "at most",
]
QUANT_POST = ["左右", "上下"]
MODAL_ZH = [
    "可能", "或许", "似乎", "初步", "初步表明", "有待", "有待进一步", "可能表明",
    "提示", "倾向于", "往往", "通常", "大体", "大体上", "在一定程度上", "部分",
    "疑似", "尚需", "仍需", "仍需进一步", "或提示", "或表明", "较", "相对",
    "推测", "暂", "暂未", "一般来说", "一般而言", "总体而言", "一定程度上",
]
QUANT_PRE_ZH = ["大约", "约", "将近", "至少", "至多", "不足", "超过"]

# Fold inflectional forms so tense changes do not look like hedge removal.
_HEDGE_CANON = {
    "suggests": "suggest", "suggested": "suggest", "suggesting": "suggest",
    "indicates": "indicate", "indicated": "indicate", "indicating": "indicate",
    "appears": "appear", "appeared": "appear", "appearing": "appear",
    "seems": "seem", "seemed": "seem",
    "tends to": "tend to", "tended to": "tend to",
    "may be": "may", "might be": "might", "could be": "could",
}


def _word_alt(words):
    return "|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True))


_MODAL_RE = re.compile(
    r"(?<![A-Za-z])(" + _word_alt(MODAL_EN) + r")(?![A-Za-z])", re.IGNORECASE
)
_QUANT_PRE_RE = re.compile(
    r"(?<![A-Za-z])(" + _word_alt(QUANT_PRE) + r")(?![A-Za-z])\s*(?=\d)",
    re.IGNORECASE,
)
_QUANT_POST_RE = re.compile(r"(?<=\d)\s*(" + _word_alt(QUANT_POST) + r")")
_MODAL_ZH_RE = re.compile("(" + _word_alt(MODAL_ZH) + ")")
_QUANT_PRE_ZH_RE = re.compile("(" + _word_alt(QUANT_PRE_ZH) + r")\s*(?=\d)")


def extract_hedges(text):
    if not text:
        return Counter()
    c = Counter()
    for rx, fold in (
        (_MODAL_RE, True), (_QUANT_PRE_RE, True), (_QUANT_POST_RE, False),
        (_MODAL_ZH_RE, False), (_QUANT_PRE_ZH_RE, False),
    ):
        for m in rx.finditer(text):
            tok = m.group(1)
            if fold:
                tok = tok.lower()
            c[_HEDGE_CANON.get(tok, tok)] += 1
    return c


# ---------------------------------------------------------------------------
# Claim detection (for soft evidence suggestions, not a hard gate)
# ---------------------------------------------------------------------------

CLAIM_HINTS = [
    "表明", "证实", "显示", "揭示", "证明", "发现", "提示",
    "demonstrat", "show", "shown", "showed", "reveal", "confirm", "prove",
    "found", "find", "indicate", "suggest", "conclude", "support", "correlat",
    "associat", "significan", "higher", "lower", "increase", "decrease",
    "reduc", "improv", "greater", "consistent", "inconsistent", "robust",
    "markedly", "strongly",
]


def has_claim(text):
    tl = (text or "").lower()
    return any(k in tl for k in CLAIM_HINTS)


# ---------------------------------------------------------------------------
# Per-unit and whole-document checks
# ---------------------------------------------------------------------------

def check_unit(unit):
    """Return (errors, warnings, resolved) for one edit unit."""
    errors, warnings, resolved = [], [], []
    orig = (unit.get("original") or "").strip()
    rev = (unit.get("revised") or "").strip()
    edit_type = (unit.get("edit_type") or "").strip().lower()

    if edit_type in ("no_change", "unchanged", "no-change"):
        return [], [], []
    if not rev and edit_type not in ("delete", "deletion", "remove"):
        errors.append({"code": "empty_revised",
                       "message": "revised 为空且未声明删除"})
        return errors, warnings, resolved

    om, rm = extract_measurements(orig), extract_measurements(rev)
    if om != rm:
        errors.append({"code": "measurement_mismatch",
                       "removed": sorted((om - rm).elements()),
                       "added": sorted((rm - om).elements())})

    oc, rc = extract_citations(orig), extract_citations(rev)
    if oc != rc:
        errors.append({"code": "citation_mismatch",
                       "removed": sorted((oc - rc).elements()),
                       "added": sorted((rc - oc).elements())})

    of, rf = extract_figrefs(orig), extract_figrefs(rev)
    if of != rf:
        errors.append({"code": "figure_mismatch",
                       "removed": sorted((of - rf).elements()),
                       "added": sorted((rf - of).elements())})

    oh, rh = extract_hedges(orig), extract_hedges(rev)
    removed_hedges = sorted((oh - rh).elements())
    if removed_hedges:
        item = {"code": "hedge_removed", "detail": removed_hedges}
        if (unit.get("hedge_note") or "").strip():
            resolved.append(item)
        else:
            warnings.append(item)

    ev = unit.get("evidence") or {}
    has_loc = bool((ev.get("locator") or "").strip())
    has_quote = bool((ev.get("quote") or "").strip())
    need_ev = bool(om or oc or of)
    if need_ev and not (has_loc and has_quote):
        errors.append({"code": "missing_evidence",
                       "message": "含数字/引用/图表指向的句子必须有 locator + 逐字 quote"})
    elif (not need_ev) and has_claim(orig) and not (has_loc and has_quote):
        warnings.append({"code": "claim_evidence_suggested",
                         "message": "含关键论断动词的句子建议补 locator + quote"})

    return errors, warnings, resolved


def check_terms(glossary, full_text):
    """Detect non-canonical term variants and unexpanded abbreviations."""
    warnings = []
    for t in (glossary.get("terms") or []):
        canon = (t.get("canonical") or "").strip()
        abbrev = (t.get("abbrev") or t.get("abbreviation") or "").strip()
        variants = [v for v in (t.get("variants") or []) if v and v != canon]
        for v in variants:
            if re.search(r"(?<![A-Za-z0-9])" + re.escape(v) + r"(?![A-Za-z0-9])",
                         full_text or ""):
                warnings.append({"code": "term_variant", "term": canon,
                                 "found_variant": v,
                                 "message": "全文使用了非规范变体 %s，应统一为 %s" % (v, canon)})
        if abbrev and canon:
            has_abbrev = re.search(r"(?<![A-Za-z0-9])" + re.escape(abbrev) +
                                   r"(?![A-Za-z0-9])", full_text or "")
            if has_abbrev and not re.search(re.escape(canon), full_text or ""):
                warnings.append({"code": "abbrev_unexpanded", "term": canon,
                                 "abbrev": abbrev,
                                 "message": "缩写 %s 出现但未见全称 %s 的首次展开" % (abbrev, canon)})
    return warnings


def _needs_evidence(text):
    return bool(extract_measurements(text) or extract_citations(text)
                or extract_figrefs(text))


def run_checks(edits, glossary=None, fact_map=None):
    """Run all checks and return a deterministic report."""
    glossary = glossary or {}
    fact_map = fact_map or []
    units = (edits.get("units") or [])
    errors, warnings, resolved = [], [], []
    for i, u in enumerate(units):
        e, w, r = check_unit(u)
        for item in e:
            errors.append(dict(item, unit_index=i))
        for item in w:
            warnings.append(dict(item, unit_index=i))
        for item in r:
            resolved.append(dict(item, unit_index=i))

    full = (edits.get("full_revised") or "")
    warnings.extend(check_terms(glossary, full))

    need_ev_count = sum(1 for u in units
                        if _needs_evidence(u.get("original") or ""))
    if need_ev_count and not fact_map:
        errors.append({"code": "fact_map_empty",
                       "message": "有 %d 个需溯源单元，但 fact_source_map 为空" % need_ev_count})
    elif fact_map and len(fact_map) < need_ev_count:
        warnings.append({"code": "fact_map_partial",
                         "message": "fact_source_map 条目(%d)少于需溯源单元(%d)"
                         % (len(fact_map), need_ev_count)})

    key = lambda x: (x.get("unit_index", -1), x.get("code", ""))
    errors.sort(key=key)
    warnings.sort(key=key)
    resolved.sort(key=key)
    return {
        "errors": errors,
        "warnings": warnings,
        "resolved": resolved,
        "stats": {
            "units": len(units),
            "errors": len(errors),
            "warnings": len(warnings),
            "resolved": len(resolved),
            "need_evidence_units": need_ev_count,
        },
    }
