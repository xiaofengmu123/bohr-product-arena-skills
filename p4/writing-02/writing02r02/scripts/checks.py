# -*- coding: utf-8 -*-
"""Deterministic conservation checks for academic-language polishing.

Every rule here is local, side-effect free and deterministic: no network,
no model calls, no random state. The module reads the work-directory
artifacts written by the agent (``edits.json``, ``glossary.json``) and emits
a ``check_report.json``-compatible dict with ``errors`` (must fix or document)
and ``warnings`` (must revert or justify).

The agent, not this script, decides the final wording. This script only flags
evidence-level drifts — numbers, units, citations, figure/table pointers,
hedge strength, sentence coverage, traceability and term consistency — so a
silent strengthening or weakening of a claim cannot slip through review.
"""

import json
import os
import re
from collections import Counter

# ---------------------------------------------------------------------------
# Curated vocabularies. Kept deliberately small and explicit: an over-broad
# list produces false positives and erodes the reviewer's trust in the report.
# ---------------------------------------------------------------------------

STRONG_MARKERS = (
    "prove", "proves", "proved", "demonstrate", "demonstrates", "demonstrated",
    "confirm", "confirms", "confirmed", "establish", "establishes",
    "established", "show", "shows", "shown", "reveal", "reveals",
    "证明", "证实", "表明", "确定", "显示", "揭示",
)

WEAK_MARKERS = (
    "may", "might", "could", "can", "suggest", "suggests", "suggested",
    "indicate", "indicates", "indicated", "appear", "appears", "likely",
    "probably", "possibly", "potentially", "seem", "seems",
    "可能", "或许", "似乎", "大概", "初步", "提示", "有望", "或可",
)

NEGATION_MARKERS = (
    "not", "no", "never", "without", "cannot", "unable", "absence",
    "不", "未", "无", "没有", "缺乏",
)

# Units kept only when >= 2 characters (plus %, ‰, °C, ℃) to avoid single-letter
# false positives such as "g" in "e.g." or "s" in a possessive.
UNIT_TOKENS = (
    "%", "‰", "°C", "℃", "kDa", "Da", "μM", "mM", "nM", "pM", "μmol", "mmol",
    "mol", "MPa", "kPa", "Pa", "bar", "kHz", "MHz", "GHz", "Hz", "kW", "kV",
    "mA", "Ω", "mg", "μg", "ng", "kg", "mL", "μL", "L", "mm", "cm", "μm",
    "nm", "pm", "min", "hr", "yr", "ms", "a.u.", "AU",
)

# Longest-first alternation so "kDa" wins over "Da", "mM" over "m", etc.
_UNIT_ALT = "|".join(re.escape(u) for u in sorted(UNIT_TOKENS, key=len, reverse=True))

NUMBER_RE = re.compile(
    r"(?<![\w.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\w])"
)
MEASURE_RE = re.compile(
    r"(?<![\w.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*("
    + _UNIT_ALT
    + r")?(?![A-Za-z0-9])"
)
UNIT_TOKEN_RE = re.compile(r"(?<![\w])(" + _UNIT_ALT + r")(?![\w])")
CITATION_RE = re.compile(r"\[(\d+(?:\s*[,\-\u2013\u2014]\s*\d+)*)\]")
AUTHOR_YEAR_RE = re.compile(
    r"\(([A-Za-z][A-Za-z.\- ]*?,?\s+(?:19|20)\d{2}[a-z]?)\)"
)
FIGTAB_RE = re.compile(r"(?i)\b(?:fig(?:ure)?s?|table)s?\s*\.?\s*(\d+)")
CN_FIGTAB_RE = re.compile(r"(图|表)\s*(\d+)")

# Sentence splitting protects decimal points and common dotted abbreviations
# before looking for sentence terminators, so "22.5", "e.g." and "Fig. 1" are
# not cut in half. Coverage is a warning-level heuristic, not an exact parser.
_SENT_ABBR = ("e.g.", "i.e.", "et al.", "vs.", "cf.", "Fig.", "fig.", "Eq.",
              "Ref.", "Sec.", "Dr.", "Mr.", "Ms.")

_PLACEHOLDER = "\ue001"  # private-use char, never expected in source text


def normalize_number(s):
    """Canonicalize a numeric token so 50 / 50.0 / 050 compare equal."""
    s = s.replace(",", "")
    if s.startswith("0") and len(s) > 1 and s[1] == ".":
        pass  # keep 0.5
    else:
        s = s.lstrip("0") or "0"
    if "." in s:
        s = s.rstrip("0").rstrip(".") or "0"
    return s


def extract_numbers(text):
    """Numbers after stripping bracket citations ([1], [2,3], [7-9])."""
    t = re.sub(r"\[[0-9,\s\u2013\u2014\-]+\]", " ", text)
    return [normalize_number(m) for m in NUMBER_RE.findall(t)]


def extract_unit_tokens(text):
    """Curated multi-character unit tokens, each bounded by non-alphanumerics."""
    return UNIT_TOKEN_RE.findall(text)


def extract_citations(text):
    """Citation keys: [1], [2,3], [7-9] and (Smith, 2020)."""
    keys = []
    for m in CITATION_RE.finditer(text):
        keys.append("[" + m.group(1) + "]")
    for m in AUTHOR_YEAR_RE.finditer(text):
        keys.append("(" + m.group(1) + ")")
    return keys


def extract_figtab(text):
    """Figure/table pointer tuples, e.g. ('Figure', '1')."""
    out = []
    for m in FIGTAB_RE.finditer(text):
        out.append(("figtable", m.group(1)))
    for m in CN_FIGTAB_RE.finditer(text):
        out.append((m.group(1), m.group(2)))
    return out


def split_sentences(text):
    if not text:
        return []
    t = text
    t = re.sub(r"(\d)\.(\d)", lambda m: m.group(1) + _PLACEHOLDER + m.group(2), t)
    for abbr in _SENT_ABBR:
        t = t.replace(abbr, abbr.replace(".", _PLACEHOLDER))
    parts = re.split(r"(?<=[。．.！!？?])(?=\s|$|[A-Za-z0-9\u4e00-\u9fff])", t)
    out = []
    for p in parts:
        p = p.replace(_PLACEHOLDER, ".")
        p = p.strip()
        if p:
            out.append(p)
    return out


def has_fact(text):
    """True if a sentence carries a number, citation, figure/table pointer
    or a claim-strength marker (strong/weak/negation)."""
    if bool(
        NUMBER_RE.search(text or "")
        or CITATION_RE.search(text or "")
        or AUTHOR_YEAR_RE.search(text or "")
        or FIGTAB_RE.search(text or "")
        or CN_FIGTAB_RE.search(text or "")
    ):
        return True
    return any(_markers(text))


def extract_facts(text, locator_prefix="draft"):
    """Deterministic fact pool: every fact-bearing sentence with locator+quote."""
    facts = []
    for i, sent in enumerate(split_sentences(text), 1):
        if not has_fact(sent):
            continue
        facts.append(
            {
                "fact": sent[:240],
                "locator": f"{locator_prefix}#s{i}",
                "quote": sent,
            }
        )
    return facts


def _markers(text):
    """Return (strong, weak, negation) marker lists present in *text*."""
    lower = (text or "").lower()
    strong, weak, neg = [], [], []
    for name, pool, dst in (
        ("strong", STRONG_MARKERS, strong),
        ("weak", WEAK_MARKERS, weak),
        ("neg", NEGATION_MARKERS, neg),
    ):
        for w in pool:
            if any("\u4e00" <= c <= "\u9fff" for c in w):
                if w in text:
                    dst.append(w)
            else:
                if re.search(r"\b" + re.escape(w) + r"\b", lower):
                    dst.append(w)
    return strong, weak, neg


def _norm(s):
    """Loose normalization for sentence-coverage matching."""
    return re.sub(r"[\W_]+", "", (s or "").lower())


# ---------------------------------------------------------------------------
# Individual checks. Each returns (errors, warnings).
# ---------------------------------------------------------------------------

def check_numbers(units):
    errors, warnings = [], []
    for i, u in enumerate(units, 1):
        orig_nums = Counter(extract_numbers(u.get("original", "")))
        rev_nums = Counter(extract_numbers(u.get("revised", "")))
        missing = orig_nums - rev_nums
        added = rev_nums - orig_nums
        if missing:
            errors.append(
                {
                    "code": "number_missing",
                    "unit": i,
                    "detail": "数值在改文中消失或改变: " + ", ".join(sorted(missing)),
                    "severity": "error",
                }
            )
        if added:
            warnings.append(
                {
                    "code": "number_added",
                    "unit": i,
                    "detail": "改文新增数值(需确认非杜撰): " + ", ".join(sorted(added)),
                    "severity": "warning",
                }
            )
    return errors, warnings


def check_units(units):
    warnings = []
    for i, u in enumerate(units, 1):
        orig_units = Counter(extract_unit_tokens(u.get("original", "")))
        rev_units = Counter(extract_unit_tokens(u.get("revised", "")))
        missing = orig_units - rev_units
        if missing:
            warnings.append(
                {
                    "code": "unit_missing",
                    "unit": i,
                    "detail": "单位在改文中消失: " + ", ".join(sorted(missing))
                    + "（若改为全拼请写明理由）",
                    "severity": "warning",
                }
            )
    return [], warnings


def check_citations(units):
    errors, warnings = [], []
    for i, u in enumerate(units, 1):
        orig_c = Counter(extract_citations(u.get("original", "")))
        rev_c = Counter(extract_citations(u.get("revised", "")))
        missing = orig_c - rev_c
        added = rev_c - orig_c
        if missing:
            errors.append(
                {
                    "code": "citation_missing",
                    "unit": i,
                    "detail": "引用在改文中消失: " + ", ".join(sorted(missing)),
                    "severity": "error",
                }
            )
        if added:
            warnings.append(
                {
                    "code": "citation_added",
                    "unit": i,
                    "detail": "改文新增引用(需溯源): " + ", ".join(sorted(added)),
                    "severity": "warning",
                }
            )
        # Order drift matters when the same bracket holds multiple numbers.
        o = extract_citations(u.get("original", ""))
        r = extract_citations(u.get("revised", ""))
        if o and o != r and sorted(o) == sorted(r):
            warnings.append(
                {
                    "code": "citation_reordered",
                    "unit": i,
                    "detail": "引用顺序变化，确认是否改变指向",
                    "severity": "warning",
                }
            )
    return errors, warnings


def check_figtab(units):
    errors = []
    for i, u in enumerate(units, 1):
        o = Counter(extract_figtab(u.get("original", "")))
        r = Counter(extract_figtab(u.get("revised", "")))
        missing = o - r
        if missing:
            errors.append(
                {
                    "code": "figtab_missing",
                    "unit": i,
                    "detail": "图表指向在改文中消失: " + ", ".join(
                        f"{k}#{v}" for k, v in sorted(missing)
                    ),
                    "severity": "error",
                }
            )
    return errors, []


def check_hedges(units):
    warnings = []
    for i, u in enumerate(units, 1):
        o_s, o_w, o_n = _markers(u.get("original", ""))
        r_s, r_w, r_n = _markers(u.get("revised", ""))
        if o_w and not o_s and r_s and not r_w:
            warnings.append(
                {
                    "code": "hedge_strengthened",
                    "unit": i,
                    "detail": "疑似加强结论: 弱限定词消失 %s → 新增强词 %s"
                    % (o_w, r_s),
                    "severity": "warning",
                }
            )
        if o_s and not o_w and r_w and not r_s:
            warnings.append(
                {
                    "code": "hedge_weakened",
                    "unit": i,
                    "detail": "疑似削弱结论: 强词消失 %s → 新增弱限定词 %s"
                    % (o_s, r_w),
                    "severity": "warning",
                }
            )
        if o_n and not r_n:
            warnings.append(
                {
                    "code": "negation_removed",
                    "unit": i,
                    "detail": "否定标记消失: %s" % o_n,
                    "severity": "warning",
                }
            )
        if r_n and not o_n:
            warnings.append(
                {
                    "code": "negation_added",
                    "unit": i,
                    "detail": "改文新增否定标记: %s" % r_n,
                    "severity": "warning",
                }
            )
    return [], warnings


def check_coverage(units, source_text):
    warnings = []
    sentences = split_sentences(source_text)
    norm_units = [_norm(u.get("original", "")) for u in units]
    for si, sent in enumerate(sentences, 1):
        ns = _norm(sent)
        if not ns:
            continue
        covered = any(
            (nu and (ns in nu or nu in ns)) for nu in norm_units if nu
        )
        if not covered:
            warnings.append(
                {
                    "code": "sentence_uncovered",
                    "unit": si,
                    "detail": "疑似未处理的句子: " + sent[:80],
                    "severity": "warning",
                }
            )
    if units and len(sentences) > len(units):
        warnings.append(
            {
                "code": "sentence_merge",
                "unit": 0,
                "detail": "改文句子数(%d)少于原文(%d)，若为合并请在理由中说明"
                % (len(units), len(sentences)),
                "severity": "warning",
            }
        )
    return [], warnings


def check_traceability(units):
    errors = []
    for i, u in enumerate(units, 1):
        if not (u.get("original") or "").strip():
            errors.append(
                {"code": "empty_original", "unit": i,
                 "detail": "原文为空", "severity": "error"}
            )
        if not (u.get("revised") or "").strip():
            errors.append(
                {"code": "empty_revised", "unit": i,
                 "detail": "改文为空", "severity": "error"}
            )
        if not (u.get("edit_type") or "").strip():
            errors.append(
                {"code": "missing_edit_type", "unit": i,
                 "detail": "缺少修改类型", "severity": "error"}
            )
        if not (u.get("reason") or "").strip():
            errors.append(
                {"code": "missing_reason", "unit": i,
                 "detail": "缺少修改理由", "severity": "error"}
            )
        ev = u.get("evidence") or {}
        has_loc_quote = bool(
            (ev.get("locator") or "").strip() and (ev.get("quote") or "").strip()
        )
        has_note = bool((ev.get("note") or "").strip())
        if has_fact(u.get("original", "")) and not has_loc_quote and not has_note:
            errors.append(
                {
                    "code": "missing_evidence",
                    "unit": i,
                    "detail": "含数字/引用/图表的句子缺少 evidence(locator+quote) 或 note",
                    "severity": "error",
                }
            )
    return errors, []


def check_glossary(units, glossary):
    errors, warnings = [], []
    terms = (glossary or {}).get("terms", []) or []
    canonicals = Counter(t.get("canonical", "") for t in terms)
    for canon, cnt in canonicals.items():
        if cnt > 1:
            errors.append(
                {
                    "code": "glossary_conflict",
                    "unit": 0,
                    "detail": "术语表重复规范写法: %s (%d 次)" % (canon, cnt),
                    "severity": "error",
                }
            )
    revised_blob = "\n".join(u.get("revised", "") for u in units)
    for t in terms:
        canon = t.get("canonical", "")
        for v in (t.get("variants") or []):
            if v and v != canon and v in revised_blob:
                warnings.append(
                    {
                        "code": "term_variant_used",
                        "unit": 0,
                        "detail": "改文仍使用术语变体 %s（规范写法 %s），若非首次定义请统一"
                        % (v, canon),
                        "severity": "warning",
                    }
                )
    return errors, warnings


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_checks(work_dir):
    """Run all checks over the work-dir artifacts. Returns a report dict."""
    edits = _read_json(os.path.join(work_dir, "edits.json")) or {}
    glossary = _read_json(os.path.join(work_dir, "glossary.json")) or {}
    units = edits.get("units", []) or []
    source_text = edits.get("source_text", "")

    errors, warnings = [], []
    checks_to_run = (
        (check_numbers, (units,)),
        (check_units, (units,)),
        (check_citations, (units,)),
        (check_figtab, (units,)),
        (check_hedges, (units,)),
        (check_coverage, (units, source_text)),
        (check_traceability, (units,)),
        (check_glossary, (units, glossary)),
    )
    for fn, args in checks_to_run:
        e, w = fn(*args)
        errors.extend(e)
        warnings.extend(w)

    stats = {
        "units": len(units),
        "sentences": len(split_sentences(source_text)),
        "errors": len(errors),
        "warnings": len(warnings),
        "fact_units": sum(1 for u in units if has_fact(u.get("original", ""))),
    }
    return {"errors": errors, "warnings": warnings, "stats": stats}


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
