# -*- coding: utf-8 -*-
"""Deterministic evidence extraction and conservation checks.

This is the evidence-tracing core of the Skill.  Instead of a flat fact map,
every detected item is tagged with a *kind* and carries a verbatim ``quote``,
a ``locator`` (``path#L<line>``) and character ``offsets`` into the source.
The conservation checker then compares each original sentence with its
revision so that hard facts (numbers, units, citations, figure/table
pointers, negations) cannot silently disappear, while strength shifts
(hedge -> strong, causal <-> correlational, quantifier/condition changes)
are surfaced as warnings that must be reverted or explicitly justified.

Standard library only; deterministic; no network; no model calls.
"""

import re

# ---------------------------------------------------------------------------
# Closed, deterministic token inventories.
# ---------------------------------------------------------------------------

HEDGE_EN = [
    "may", "might", "could", "perhaps", "possibly", "potentially", "likely",
    "unlikely", "probably", "apparently", "seem", "seems", "seemed",
    "appear", "appears", "appeared", "suggest", "suggests", "suggested",
    "indicate", "indicates", "indicated", "preliminary", "tentatively",
    "presumably", "to our knowledge", "in part", "partly", "approximately",
    "about", "around", "roughly", "nearly", "up to", "at least", "at most",
]
STRONG_EN = [
    "prove", "proves", "proved", "proven", "confirm", "confirms", "confirmed",
    "demonstrate", "demonstrates", "demonstrated", "establish", "establishes",
    "established", "definitely", "certainly", "clearly", "undoubtedly",
    "always", "guarantee", "must", "necessarily", "conclusive", "conclusively",
]
HEDGE_ZH = [
    "可能", "或许", "也许", "大概", "大约", "约", "初步", "似乎", "看似",
    "倾向于", "提示", "表明", "有望", "部分", "在一定程度上", "据我们所知",
    "粗略", "接近", "至多", "至少",
]
STRONG_ZH = [
    "证实", "证明", "确定", "确认", "必然", "一定", "无疑", "肯定",
    "清楚表明", "彻底", "毫无疑问",
]

NEGATION_EN = [
    "not", "no", "never", "none", "neither", "nor", "without", "absent",
    "lack", "lacks", "lacked", "fail", "fails", "failed", "unable", "cannot",
    "unrelated", "insignificant",
]
NEGATION_ZH = ["不", "未", "无", "没有", "非", "无法", "缺乏", "未能", "无关"]

QUANT_EN = [
    "all", "every", "each", "some", "several", "many", "most", "few",
    "none", "both", "neither", "any", "only",
]
QUANT_ZH = ["全部", "所有", "每个", "部分", "一些", "若干", "多数", "少数",
            "大部分", "两者", "任何", "仅"]

CAUSAL_EN = [
    "cause", "caused", "causes", "due to", "because", "owing to", "lead to",
    "leads to", "led to", "induce", "induced", "drive", "drives", "driven",
    "trigger", "triggered", "result in", "results in", "resulted in",
]
CORR_EN = [
    "correlate", "correlated", "correlation", "correlates", "associate",
    "associated", "association", "link", "linked", "related", "relationship",
    "coincide", "coincided", "parallel",
]
CAUSAL_ZH = ["导致", "引起", "造成", "因为", "由于", "促使", "诱发", "驱动",
             "触发", "使得", "引发"]
CORR_ZH = ["相关", "关联", "联系", "伴随", "相关性", "相关系数", "对应",
           "平行"]

COND_EN = ["if", "when", "unless", "whenever", "provided that",
           "in the absence of", "whereas"]
COND_ZH = ["若", "如果", "当", "除非", "只要", "在", "而"]

# Units that may attach to a number; longest first so "mmol" wins over "mol".
UNITS = [
    "mmol", "μmol", "µmol", "nmol", "pmol", "mol", "kDa", "Da",
    "µg", "μg", "mg", "ng", "pg", "kg", "g", "µL", "μL", "mL", "nL",
    "pL", "L", "µm", "μm", "nm", "mm", "cm", "km", "m", "°C", "℃",
    "min", "h", "s", "Hz", "kHz", "MHz", "GHz", "V", "mV", "kV",
    "A", "mA", "W", "mW", "Pa", "kPa", "MPa", "bar", "M", "mM",
    "μM", "µM", "nM", "pM", "%", "‰", "倍", "摄氏度", "毫升", "微升",
    "毫克", "微克", "纳米", "毫米", "厘米", "分钟", "小时", "秒", "年",
    "g/L", "mg/L", "μg/L", "µg/L", "ng/L", "mg/mL", "μg/mL", "µg/mL",
    "mol/L", "mmol/L", "μmol/L", "µmol/L", "nmol/L", "U/mL", "IU/mL",
]

_UNIT_ALT = "|".join(sorted((re.escape(u) for u in UNITS), key=len, reverse=True))

NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9\-–—])"
    r"([+-]?\d[\d,]*(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    r"(?:\s*(%|‰|" + _UNIT_ALT + r"))?"
    r"(?![A-Za-z0-9])"
)

BRACKET_CITE_RE = re.compile(
    r"[\[［]\s*(\d+)\s*(?:(?:[,，\-–—]\s*)?(\d+)\s*)*[\]］]"
)
AUTHOR_YEAR_RE = re.compile(
    r"\(\s*[A-Z][A-Za-z'\-]+"
    r"(?:\s+(?:et\s+al\.?|and\s+[A-Z][A-Za-z'\-]+|[A-Z][A-Za-z'\-]+))?"
    r"(?:,\s*\d{4}[a-z]?|\s+\d{4}[a-z]?)"
    r"\s*\)"
)

FIGURE_RE = re.compile(
    r"(?:Fig(?:ure)?s?\.?|fig\.?|FIG\.?)\s*\d+[A-Za-z]?"
    r"(?:\s*[-–,，]\s*\d+[A-Za-z]?)*",
)
TABLE_RE = re.compile(r"(?:Table|Tab\.?|Tables?)\s*\d+[A-Za-z]?")
FIGURE_ZH_RE = re.compile(r"[图表]\s*\d+(?:[A-Za-z]|[一二三四五六七八九十])?"
                          r"(?:\s*[-–,，、]\s*\d+[A-Za-z]?)*")

ABBR_DEF_RE = re.compile(
    r"\b([A-Z][A-Za-z\-]+(?:\s+[A-Z][A-Za-z\-]+){0,6})\s+\(([A-Z][A-Z0-9\-]{1,15})\)"
)
DEF_ABBR_RE = re.compile(
    r"\b([A-Z][A-Z0-9\-]{1,15})\s+\(([A-Z][A-Za-z\-]+(?:\s+[A-Z][A-Za-z\-]+){0,6})\)"
)


# ---------------------------------------------------------------------------
# Token finders (return (quote, start, end, extra) tuples).
# ---------------------------------------------------------------------------

def _line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def _excluded_spans(spans):
    return sorted(spans, key=lambda s: s[0])


def _overlaps(start, end, excluded):
    for a, b in excluded:
        if start < b and end > a:
            return True
    return False


def _find_citations(text):
    out = []
    for m in BRACKET_CITE_RE.finditer(text):
        q = m.group(0)
        out.append((q, m.start(), m.end(), {"style": "bracket"}))
    for m in AUTHOR_YEAR_RE.finditer(text):
        q = m.group(0)
        out.append((q, m.start(), m.end(), {"style": "author_year"}))
    return out


def _find_figures(text):
    out = []
    for m in FIGURE_RE.finditer(text):
        out.append((m.group(0), m.start(), m.end(), {"kind": "figure"}))
    for m in TABLE_RE.finditer(text):
        out.append((m.group(0), m.start(), m.end(), {"kind": "table"}))
    for m in FIGURE_ZH_RE.finditer(text):
        out.append((m.group(0), m.start(), m.end(), {"kind": "figure_table_zh"}))
    return out


def _find_numbers(text, excluded):
    out = []
    for m in NUMBER_RE.finditer(text):
        if _overlaps(m.start(), m.end(), excluded):
            continue
        num = m.group(1)
        unit = m.group(2)
        # skip bare citation-like integers already covered; keep everything else
        out.append((m.group(0), m.start(), m.end(),
                    {"number": num, "unit": unit or None}))
    return out


def _build_word_alt(words):
    return "|".join(sorted((re.escape(w) for w in words), key=len, reverse=True))


def _find_phrase_category(text, words, kind, excluded, word_boundary=True):
    alt = _build_word_alt(words)
    if word_boundary:
        pat = re.compile(r"\b(?:" + alt + r")\b", re.IGNORECASE)
    else:
        pat = re.compile(alt)
    out = []
    for m in pat.finditer(text):
        if _overlaps(m.start(), m.end(), excluded):
            continue
        out.append((m.group(0), m.start(), m.end(), {"category": kind}))
    return out


def _find_glossary_terms(text):
    out = []
    for m in ABBR_DEF_RE.finditer(text):
        out.append(("abbr_def", m.start(), m.end(),
                    {"full": m.group(1), "abbr": m.group(2)}))
    for m in DEF_ABBR_RE.finditer(text):
        out.append(("def_abbr", m.start(), m.end(),
                    {"abbr": m.group(1), "full": m.group(2)}))
    return out


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------

def extract_evidence(text, path):
    """Return a fine-grained evidence map for ``text`` from ``path``."""
    citations = _find_citations(text)
    figures = _find_figures(text)
    excluded = [(s, e) for (_q, s, e, _x) in citations + figures]

    records = []
    for q, s, e, x in citations:
        records.append(_rec("citation", q, s, e, text, path, x))
    for q, s, e, x in figures:
        records.append(_rec(x.get("kind", "figure"), q, s, e, text, path, x))
    for q, s, e, x in _find_numbers(text, excluded):
        records.append(_rec("number", q, s, e, text, path, x))

    # Strength / logic / scope markers (word or phrase based).
    for words, kind, boundary in [
        (HEDGE_EN, "hedge", True),
        (STRONG_EN, "strong", True),
        (NEGATION_EN, "negation", True),
        (QUANT_EN, "quantifier", True),
        (CAUSAL_EN, "causal", True),
        (CORR_EN, "correlational", True),
        (COND_EN, "condition", True),
        (HEDGE_ZH, "hedge", False),
        (STRONG_ZH, "strong", False),
        (NEGATION_ZH, "negation", False),
        (QUANT_ZH, "quantifier", False),
        (CAUSAL_ZH, "causal", False),
        (CORR_ZH, "correlational", False),
        (COND_ZH, "condition", False),
    ]:
        for q, s, e, x in _find_phrase_category(text, words, kind, excluded, boundary):
            records.append(_rec(kind, q, s, e, text, path, x))

    for _t, s, e, x in _find_glossary_terms(text):
        records.append(_rec("term", text[s:e], s, e, text, path, x))

    records.sort(key=lambda r: r["offset"][0])
    for i, r in enumerate(records, 1):
        r["id"] = "ev-%03d" % i
    return records


def _rec(kind, quote, start, end, text, path, extra):
    line = _line_of(text, start)
    return {
        "kind": kind,
        "quote": quote,
        "locator": "%s#L%d" % (path, line),
        "offset": [start, end],
        "extra": extra,
    }


def extract_glossary(text, path):
    """Deterministic seed glossary: abbreviation <-> full-form pairs."""
    seen = {}
    for _t, s, e, x in _find_glossary_terms(text):
        abbr = x["abbr"]
        full = x["full"]
        key = abbr.lower()
        if key not in seen:
            seen[key] = {
                "abbr": abbr,
                "full": full,
                "locator": "%s#L%d" % (path, _line_of(text, s)),
                "quote": text[s:e],
                "status": "auto",
            }
    return sorted(seen.values(), key=lambda g: g["abbr"].lower())


def _canon_number(num, unit):
    return (num.replace(",", "").lower(), (unit or "").lower())


def signal_set(text):
    """Extract conserved-signal sets used by the conservation checker."""
    citations = _find_citations(text)
    figures = _find_figures(text)
    excluded = [(s, e) for (_q, s, e, _x) in citations + figures]
    numbers = set(_canon_number(x["number"], x["unit"])
                  for (_q, _s, _e, x) in _find_numbers(text, excluded))

    def vocab(words, boundary):
        alt = _build_word_alt(words)
        pat = re.compile(r"\b(?:" + alt + r")\b", re.IGNORECASE) if boundary \
            else re.compile(alt)
        return set(m.group(0).lower() for m in pat.finditer(text))

    return {
        "numbers": numbers,
        "citations": set(_norm_cite(q) for (q, _s, _e, _x) in citations),
        "figures": set(_norm_fig(q) for (q, _s, _e, _x) in figures),
        "hedges": vocab(HEDGE_EN, True) | vocab(HEDGE_ZH, False),
        "strongs": vocab(STRONG_EN, True) | vocab(STRONG_ZH, False),
        "negations": vocab(NEGATION_EN, True) | vocab(NEGATION_ZH, False),
        "quantifiers": vocab(QUANT_EN, True) | vocab(QUANT_ZH, False),
        "causal": vocab(CAUSAL_EN, True) | vocab(CAUSAL_ZH, False),
        "correlational": vocab(CORR_EN, True) | vocab(CORR_ZH, False),
        "conditions": vocab(COND_EN, True) | vocab(COND_ZH, False),
    }


def _norm_cite(q):
    return re.sub(r"\s+", "", q).replace("［", "[").replace("］", "]").lower()


def _norm_fig(q):
    return re.sub(r"\s+", "", q).lower()


def verify_conservation(units):
    """Compare original vs revised signals for every edit unit.

    Returns {"errors": [...], "warnings": [...], "stats": {...}}.
    ``errors`` are hard conservation failures (meaning-flipping losses);
    ``warnings`` are strength/scope shifts requiring revert-or-justify.
    """
    errors, warnings = [], []
    n_units = len(units)
    n_checks = 0

    for u in units:
        uid = u.get("id") or "unit?"
        orig = (u.get("original") or "").strip()
        rev = (u.get("revised") or "").strip()
        if not orig:
            continue
        o = signal_set(orig)
        r = signal_set(rev)

        for lost in sorted(o["numbers"] - r["numbers"]):
            n_checks += 1
            errors.append({
                "unit": uid, "type": "number_lost",
                "detail": "number/unit %r missing from revision" % list(lost),
                "quote": orig[:120],
            })
        for lost in sorted(o["citations"] - r["citations"]):
            n_checks += 1
            errors.append({
                "unit": uid, "type": "citation_lost",
                "detail": "citation %r missing from revision" % lost,
                "quote": orig[:120],
            })
        for lost in sorted(o["figures"] - r["figures"]):
            n_checks += 1
            errors.append({
                "unit": uid, "type": "figure_lost",
                "detail": "figure/table pointer %r missing from revision" % lost,
                "quote": orig[:120],
            })
        for lost in sorted(o["negations"] - r["negations"]):
            n_checks += 1
            errors.append({
                "unit": uid, "type": "negation_lost",
                "detail": "negation %r dropped from revision" % lost,
                "quote": orig[:120],
            })

        if o["hedges"] and not r["hedges"] and r["strongs"]:
            n_checks += 1
            warnings.append({
                "unit": uid, "type": "strength_increased",
                "detail": "hedge removed while strong term added: %s -> %s"
                          % (sorted(o["hedges"])[:5], sorted(r["strongs"])[:5]),
                "quote": orig[:120],
            })
        if o["strongs"] and r["hedges"] and not r["strongs"]:
            n_checks += 1
            warnings.append({
                "unit": uid, "type": "strength_weakened",
                "detail": "strong term removed while hedge added: %s -> %s"
                          % (sorted(o["strongs"])[:5], sorted(r["hedges"])[:5]),
                "quote": orig[:120],
            })
        if o["correlational"] and r["causal"] and not r["correlational"]:
            n_checks += 1
            warnings.append({
                "unit": uid, "type": "correlation_to_causation",
                "detail": "correlational wording replaced by causal wording",
                "quote": orig[:120],
            })
        if o["conditions"] and not r["conditions"]:
            n_checks += 1
            warnings.append({
                "unit": uid, "type": "condition_removed",
                "detail": "conditional wording dropped from revision",
                "quote": orig[:120],
            })
        if o["quantifiers"] and not r["quantifiers"]:
            n_checks += 1
            warnings.append({
                "unit": uid, "type": "quantifier_removed",
                "detail": "quantifier dropped from revision",
                "quote": orig[:120],
            })

    return {
        "errors": errors,
        "warnings": warnings,
        "stats": {
            "units": n_units,
            "checks": n_checks,
            "errors": len(errors),
            "warnings": len(warnings),
        },
    }
