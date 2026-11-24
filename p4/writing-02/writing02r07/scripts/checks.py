# -*- coding: utf-8 -*-
"""Deterministic conservation checks for academic-language polishing.

The checks are pure and rule-based: identical inputs always produce an
identical report. They compare the ``original`` and ``revised`` text of each
edit unit and flag potential violations of the preservation rules. A flag is
a *candidate* problem, not a verdict: the model must either revert the edit
or record an explicit justification in the unit's ``reason`` field.

Design trade-offs (robustness variant)
--------------------------------------
* Nothing here depends on a working tree, model output, network, or time.
* Token extraction is tolerant of harmless formatting changes (e.g. "10 mL"
  vs "10mL", "1,000" vs "1000") but strict about dropped/added facts.
* Hedge changes are warnings, not errors: they always require a justification
  but never silently pass, so no conclusion is strengthened/weakened unnoticed.
"""

import re
from collections import Counter

# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------

_NUMBER = r"[-+]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?:[eE][-+]?\d+)?"
_UNIT = (
    r"(?:%|‰|°C|℃|kDa|mM|mmHg|kHz|MHz|GHz|THz|MPa|GPa|kPa|"
    r"μM|µM|μmol|µmol|mmol|nmol|pmol|mg|μg|µg|ng|pg|kg|"
    r"mL|μL|µL|nL|mm|cm|μm|µm|nm|pm|km|mol|"
    r"ms|min|mW|kW|MW|mV|kV|mA|kJ|MJ|cal|kcal|eV|keV|MeV|GeV|"
    r"bar|atm|bpm|rpm|bp|kb|Mb|Gb|IU|pH|"
    r"K|s|h|hr|d|yr|y|g|M|L|l|m|Pa|Hz|W|V|A|Ω|ohm|J|N|Da|U|×|x)"
)
_MEASURE_RE = re.compile(r"(" + _NUMBER + r")\s*(" + _UNIT + r")")
_PLAIN_RE = re.compile(r"(?<![\w.\-])" + _NUMBER + r"(?![\w.\-])")

_CITE_BRACKET_RE = re.compile(r"\[\s*(\d+(?:\s*[,，\-–]\s*\d+)*)\s*\]")
_CITE_AUTHYEAR_RE = re.compile(
    r"\(([A-Z][A-Za-z\-]+(?:\s+(?:et al\.?|and|&)\s+[A-Z][A-Za-z\-]+)?"
    r"(?:,\s*\d{4}[a-z]?)?)\)"
)

_FIGREF_RE = re.compile(
    r"(?i)\b(?:Fig\.?|Figure|Table|Eq\.?|Equation|Scheme|Chart|Algorithm)"
    r"\s*\.?\s*\d+[a-z]?"
    r"|(?:图|表|式|方程|图表|示意图)\s*\.?\s*\d+(?:[a-zA-Z]|-\d+)?"
)

# Chinese hedge markers are matched by plain substring (\\b does not work well
# with CJK text), English markers by word boundary.
_HEDGE_STRONG_EN = [
    "demonstrat", "confirm", "prove", "establish", "show", "verifi",
    "proven", "showing", "showed", "proved", "confirmed",
]
_HEDGE_WEAK_EN = [
    "suggest", "indicat", "may", "might", "could", "likely", "probably",
    "possibly", "perhaps", "potentially", "appear", "seem", "presum",
    "tentativ", "hint", "point to", "consistent with",
]
_HEDGE_STRONG_ZH = ["证实", "证明", "确立", "显示", "表明", "确认", "验证"]
_HEDGE_WEAK_ZH = ["初步表明", "初步显示", "可能", "或许", "似乎", "有望", "提示", "暗示",
                 "可能表明", "倾向于", "或可", "也许"]


def _norm_number(raw):
    """Normalise a numeric literal for tolerant comparison."""
    s = raw.strip().replace(",", "").replace("，", "")
    try:
        f = float(s)
    except ValueError:
        return s
    if f == int(f):
        return str(int(f))
    return ("%.8g" % f).replace("+", "")


def extract_measurements(text):
    """Return Counter of normalised ``number+unit`` tokens."""
    out = Counter()
    for m in _MEASURE_RE.finditer(text):
        num = _norm_number(m.group(1))
        unit = (m.group(2) or "").strip()
        out[num + " " + unit] += 1
    return out


def extract_plain_numbers(text):
    """Return Counter of normalised bare numeric literals (no unit)."""
    out = Counter()
    for m in _PLAIN_RE.finditer(text):
        out[_norm_number(m.group(0))] += 1
    return out


def extract_citations(text):
    """Return Counter of canonical citation markers."""
    out = Counter()
    for m in _CITE_BRACKET_RE.finditer(text):
        inner = m.group(1)
        nums = [n for n in re.split(r"[,，\-–]", inner) if n.strip()]
        parts = sorted(set(int(n) for n in nums if n.isdigit()))
        if parts:
            out["[" + ",".join(str(p) for p in parts) + "]"] += 1
    for m in _CITE_AUTHYEAR_RE.finditer(text):
        out["(" + m.group(1) + ")"] += 1
    return out


def extract_figrefs(text):
    """Return Counter of canonical figure/table/equation references."""
    out = Counter()
    for m in _FIGREF_RE.finditer(text):
        out[re.sub(r"\s+", " ", m.group(0)).strip()] += 1
    return out


def _hedge_tokens(text, tokens, whole_word):
    found = Counter()
    lower = text.lower()
    for t in tokens:
        if whole_word:
            for m in re.finditer(r"(?<![A-Za-z])" + re.escape(t) + r"[A-Za-z]*",
                                 lower):
                found[t] += 1
        else:
            found[t] += lower.count(t)
    return found


def extract_hedges(text):
    """Return (strong, weak) Counters of strength markers."""
    strong = _hedge_tokens(text, _HEDGE_STRONG_EN, True)
    weak = _hedge_tokens(text, _HEDGE_WEAK_EN, True)
    strong += _hedge_tokens(text, _HEDGE_STRONG_ZH, False)
    weak += _hedge_tokens(text, _HEDGE_WEAK_ZH, False)
    return strong, weak


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def _diff(orig, rev):
    dropped = orig - rev
    added = rev - orig
    dropped = {k: v for k, v in dropped.items() if v > 0}
    added = {k: v for k, v in added.items() if v > 0}
    return dropped, added


def compare_unit(original, revised):
    """Compare one original/revised sentence pair.

    Returns a list of issue dicts:
        {code, severity, detail}
    where severity is "error" or "warning".
    """
    issues = []
    o = original or ""
    r = revised or ""

    om = extract_measurements(o)
    rm = extract_measurements(r)
    dropped, added = _diff(om, rm)
    for tok, n in sorted(dropped.items()):
        issues.append({"code": "measurement_dropped", "severity": "error",
                       "detail": f"丢失测量值 {tok!r} ×{n}"})
    for tok, n in sorted(added.items()):
        issues.append({"code": "measurement_added", "severity": "error",
                       "detail": f"新增测量值 {tok!r} ×{n}"})

    op = extract_plain_numbers(o)
    rp = extract_plain_numbers(r)
    dropped, added = _diff(op, rp)
    for tok, n in sorted(dropped.items()):
        issues.append({"code": "plain_number_dropped", "severity": "warning",
                       "detail": f"裸数值 {tok!r} ×{n} 消失"})
    for tok, n in sorted(added.items()):
        issues.append({"code": "plain_number_added", "severity": "warning",
                       "detail": f"裸数值 {tok!r} ×{n} 新增"})

    oc = extract_citations(o)
    rc = extract_citations(r)
    dropped, added = _diff(oc, rc)
    for tok, n in sorted(dropped.items()):
        issues.append({"code": "citation_dropped", "severity": "error",
                       "detail": f"引用 {tok} ×{n} 消失"})
    for tok, n in sorted(added.items()):
        issues.append({"code": "citation_added", "severity": "error",
                       "detail": f"引用 {tok} ×{n} 新增"})

    of = extract_figrefs(o)
    rf = extract_figrefs(r)
    dropped, added = _diff(of, rf)
    for tok, n in sorted(dropped.items()):
        issues.append({"code": "figref_dropped", "severity": "error",
                       "detail": f"图表指向 {tok} ×{n} 消失"})
    for tok, n in sorted(added.items()):
        issues.append({"code": "figref_added", "severity": "error",
                       "detail": f"图表指向 {tok} ×{n} 新增"})

    os_, ow = extract_hedges(o)
    rs_, rw = extract_hedges(r)
    strong_lost = os_ - rs_
    strong_gained = rs_ - os_
    weak_lost = ow - rw
    weak_gained = rw - ow
    for tok, n in sorted(strong_lost.items()):
        if n > 0:
            issues.append({"code": "hedge_strong_lost", "severity": "warning",
                           "detail": f"强限定词 {tok!r} ×{n} 被移除，可能削弱结论"})
    for tok, n in sorted(strong_gained.items()):
        if n > 0:
            issues.append({"code": "hedge_strong_gained", "severity": "warning",
                           "detail": f"强限定词 {tok!r} ×{n} 新增，可能加强结论"})
    for tok, n in sorted(weak_lost.items()):
        if n > 0:
            issues.append({"code": "hedge_weak_lost", "severity": "warning",
                           "detail": f"弱限定词 {tok!r} ×{n} 被移除，可能加强结论"})
    for tok, n in sorted(weak_gained.items()):
        if n > 0:
            issues.append({"code": "hedge_weak_gained", "severity": "warning",
                           "detail": f"弱限定词 {tok!r} ×{n} 新增，可能削弱结论"})
    return issues


def classify_issues(issues):
    """Return counts for {errors, warnings} and a summary list."""
    errors = [i for i in issues if i["severity"] == "error"]
    warnings = [i for i in issues if i["severity"] == "warning"]
    return errors, warnings


def coverage_check(units, paragraph_count=None):
    """Flag original sentences that have no corresponding edit unit."""
    issues = []
    for u in units:
        if not (u.get("original") or "").strip():
            issues.append({"code": "empty_unit", "severity": "error",
                           "detail": f"unit #{u.get('id')} 原文为空"})
        if not (u.get("revised") or "").strip():
            issues.append({"code": "unrevised_unit", "severity": "warning",
                           "detail": f"unit #{u.get('id')} 尚无改文（视为未处理）"})
    return issues


def provenance_check(fact_source_map):
    """Validate every fact entry has a locator and a verbatim quote."""
    issues = []
    for i, f in enumerate(fact_source_map):
        if not isinstance(f, dict):
            issues.append({"code": "fact_not_object", "severity": "error",
                           "detail": f"fact_source_map[{i}] 不是对象"})
            continue
        if not str(f.get("fact") or "").strip():
            issues.append({"code": "fact_missing", "severity": "error",
                           "detail": f"fact_source_map[{i}] 缺少 fact"})
        if not str(f.get("locator") or "").strip():
            issues.append({"code": "locator_missing", "severity": "error",
                           "detail": f"fact_source_map[{i}] 缺少 locator"})
        if not str(f.get("quote") or "").strip():
            issues.append({"code": "quote_missing", "severity": "error",
                           "detail": f"fact_source_map[{i}] 缺少逐字 quote"})
    return issues
