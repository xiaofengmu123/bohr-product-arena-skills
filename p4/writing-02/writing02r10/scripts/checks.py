# -*- coding: utf-8 -*-
"""Feature-conservation engine for the academic-polish Skill.

Design rationale (the architectural difference from a "re-scan the revised
text at verify time" approach): the *baseline feature register* is frozen
once, at `scaffold` time, straight from the original draft. Verification
never re-derives "what the original said" from the revised text or from
memory; it recomputes the revised sentence's features and diffs them against
the frozen baseline. A number, unit, citation or figure reference that the
checker can still see in the original therefore always participates in the
comparison, even if its wording changed so much that a naive re-scan would
have missed it.

The engine is deliberately *asymmetric about risk*:
  * numbers / units / citations / figures -> strict  (dropped => error)
  * hedge / strong-claim strength            -> error unless explicitly
                                                justified on the edit unit
  * negations / quantifiers / conditionals   -> warnings (heuristic)

Everything here is deterministic and standard-library only.
"""

import re
from collections import Counter

FIELD_ORDER = [
    "numbers", "units", "citations", "figures",
    "negations", "quantifiers", "conditionals", "hedges", "strong",
]
STRICT_FIELDS = {"numbers", "units", "citations", "figures"}
WARN_FIELDS = {"negations", "quantifiers", "conditionals"}


# ---------------------------------------------------------------------------
# Tokenizers
# ---------------------------------------------------------------------------

_NUM = r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?(?![\w])"
NUMBER_RE = re.compile(_NUM)

_UNIT_WORDS = [
    "%", "‰", "°C", "°F", "℃", "℉", "K", "Da", "kDa", "bp", "kb", "Mb", "Gb",
    "mol", "mmol", "μmol", "umol", "nmol", "pmol", "M", "mM", "μM", "uM", "nM",
    "pM", "kg", "g", "mg", "μg", "ug", "ng", "pg", "L", "mL", "ml", "μL", "uL",
    "dL", "dl", "cm", "mm", "μm", "um", "nm", "pm", "km", "s", "sec", "min", "h",
    "hr", "hrs", "day", "days", "week", "weeks", "month", "months", "year", "yr",
    "yrs", "rpm", "×g", "xg", "rcf", "g/L", "mg/mL", "μg/mL", "ug/mL", "mg/ml",
    "μg/ml", "ug/ml", "mmol/L", "μmol/L", "umol/L", "nmol/L", "U", "IU", "U/mL",
    "IU/mL", "cells/mL", "CFU", "cfu", "Pa", "kPa", "MPa", "GPa", "bar", "atm",
    "V", "mV", "kV", "A", "mA", "W", "kW", "mW", "Hz", "kHz", "MHz", "GHz", "eV",
    "keV", "MeV", "GeV", "J", "kJ", "MJ", "cal", "kcal", "N", "pH", "AU", "OD",
    "a.u.", "log10", "cfu/mL",
]

_unit_esc = sorted({re.escape(w) for w in _UNIT_WORDS}, key=len, reverse=True)
WORD_UNIT_RE = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(_unit_esc) + r")(?![A-Za-z0-9])")
SYMBOL_UNIT_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|‰|°C|°F|℃|℉)")

BRACKET_CITE_RE = re.compile(r"\[[\d\s,;\-–—]*\d[\d\s,;\-–—]*\]")
AUTHYEAR_RE = re.compile(
    r"\((?:[A-Z][\w'’\-]+(?:[ ,;]*(?:and|&|et al\.?|[A-Z][\w'’\-]+)){0,4})\s*,?\s*(?:19|20)\d{2}[a-z]?\)"
)

FIGURE_RE = re.compile(
    r"\b(?:Fig(?:ure)?s?|Figure|Scheme|Table|Chart|Panel)\s*\.?\s*\d+(?:[a-z]?)\s*(?:[,\-–—]+\s*\d+[a-z]?)*",
    re.I,
)
EQ_RE = re.compile(r"\bEq(?:uation)?s?\.?\s*\(?\d+(?:[a-z]?)\)?", re.I)
ZH_FIG_RE = re.compile(r"(?:图|表|式|附图|附表)\s*\(?\d+(?:[a-z]?)(?:\s*[-\-–—]\s*\d+)?\)?")

ACRONYM_RE = re.compile(r"(?<![\w])(?:[A-Z][a-z]?[\w]*[\s-]*){1,3}\(([A-Z]{2,8})\)")
STANDALONE_ACRONYM_RE = re.compile(r"(?<![\w])[A-Z]{2,6}(?![\w])")


# ---------------------------------------------------------------------------
# Lexical lists (heuristic, warning level)
# ---------------------------------------------------------------------------

HEDGE_EN = [
    "may", "might", "could", "would", "suggest", "suggests", "suggested",
    "suggesting", "indicate", "indicates", "indicated", "indicating", "appear",
    "appears", "appeared", "appearing", "seem", "seems", "seemed", "seeming",
    "likely", "presumably", "possibly", "potentially", "probably", "perhaps",
    "tentatively", "preliminary", "preliminarily", "approximately", "approx",
    "about", "around", "estimated", "estimate", "putative", "candidate",
    "candidates", "may be", "might be", "is likely", "are likely",
]

STRONG_EN = [
    "demonstrate", "demonstrates", "demonstrated", "demonstrating", "prove",
    "proves", "proved", "proven", "confirm", "confirms", "confirmed",
    "confirming", "establish", "establishes", "established", "establishing",
    "conclusively", "unequivocally", "definitively",
]

NEG_EN = [
    "not", "no", "never", "none", "neither", "nor", "without", "unable",
    "cannot", "can not", "fails to", "fail to", "absent", "lack of", "lacks",
]

QUANT_EN = [
    "all", "every", "each", "some", "most", "many", "few", "several", "none",
    "both", "neither", "any", "majority", "minority", "the majority",
    "approximately all",
]

COND_EN = [
    "if", "when", "unless", "provided that", "provided", "in the case", "upon",
    "whereas", "given that", "once",
]

HEDGE_ZH = [
    "可能", "或许", "也许", "似乎", "看来", "大概", "大约", "约为", "左右",
    "初步", "有望", "提示", "或可", "可望", "潜在", "候选", "推测", "估计",
    "近似",
]

STRONG_ZH = [
    "证实", "证明", "确立", "充分证明", "明确表明", "确证", "毋庸置疑",
    "确切地表明", "证实了", "证明了",
]

NEG_ZH = [
    "不", "未", "无", "非", "没", "难以", "无法", "不能", "并不", "并非",
    "尚未", "缺乏", "没有", "未能", "不予",
]

QUANT_ZH = [
    "所有", "全部", "一切", "每个", "一些", "某些", "多数", "大多数", "少数",
    "极少", "部分", "若干", "均", "皆", "大部分", "绝大多数", "约半数",
]

COND_ZH = [
    "若", "如果", "倘若", "当", "除非", "只要", "一旦", "假如", "在……条件下",
    "在…条件下", "如果…那么", "在…时",
]


# ---------------------------------------------------------------------------
# Normalization and matching
# ---------------------------------------------------------------------------

def _norm(tok):
    return re.sub(r"\s+", "", tok).lower().replace("–", "-").replace("—", "-").replace("−", "-")


def _match_list(text, words, boundary):
    found = []
    for w in words:
        pat = re.compile(r"(?<![\w])" + re.escape(w) + r"(?![\w])", re.I) if boundary \
            else re.compile(re.escape(w), re.I)
        for m in pat.finditer(text):
            found.append(_norm(m.group(0)))
    return found


def extract_features(text):
    """Return a dict {category: sorted list of normalized tokens}."""
    feats = {f: [] for f in FIELD_ORDER}
    feats["numbers"] = sorted({_norm(m.group(0)) for m in NUMBER_RE.finditer(text)})
    units = [_norm(m.group(0)) for m in WORD_UNIT_RE.finditer(text)]
    units += [_norm(m.group(0)) for m in SYMBOL_UNIT_RE.finditer(text)]
    feats["units"] = sorted(set(units))
    cites = [_norm(m.group(0)) for m in BRACKET_CITE_RE.finditer(text)]
    cites += [_norm(m.group(0)) for m in AUTHYEAR_RE.finditer(text)]
    feats["citations"] = sorted(set(cites))
    figs = [_norm(m.group(0)) for m in FIGURE_RE.finditer(text)]
    figs += [_norm(m.group(0)) for m in EQ_RE.finditer(text)]
    figs += [_norm(m.group(0)) for m in ZH_FIG_RE.finditer(text)]
    feats["figures"] = sorted(set(figs))
    feats["hedges"] = sorted(set(_match_list(text, HEDGE_EN, True) + _match_list(text, HEDGE_ZH, False)))
    feats["strong"] = sorted(set(_match_list(text, STRONG_EN, True) + _match_list(text, STRONG_ZH, False)))
    feats["negations"] = sorted(set(_match_list(text, NEG_EN, True) + _match_list(text, NEG_ZH, False)))
    feats["quantifiers"] = sorted(set(_match_list(text, QUANT_EN, True) + _match_list(text, QUANT_ZH, False)))
    feats["conditionals"] = sorted(set(_match_list(text, COND_EN, True) + _match_list(text, COND_ZH, False)))
    return feats


def extract_terms(text):
    """Seed glossary candidates: acronyms introduced in parentheses + standalone acronyms."""
    return sorted(set(ACRONYM_RE.findall(text)) | set(STANDALONE_ACRONYM_RE.findall(text)))


# ---------------------------------------------------------------------------
# Sentence splitting (deterministic scanner, zh/en mixed)
# ---------------------------------------------------------------------------

_PROTECTED = {"Fig", "Eq", "vs", "al", "cf", "ca", "approx", "e.g", "i.e", "et",
              "No", "Vol", "pp", "St", "Dr", "Prof", "Ref", "et al"}


def split_sentences(text):
    """Split mixed zh/en text into sentences without re-order or randomness."""
    if not text:
        return []
    out = []
    buf = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        buf.append(ch)
        if ch in "。！？!?":
            out.append("".join(buf))
            buf = []
            i += 1
            continue
        if ch == ".":
            j = i + 1
            while j < n and text[j] in " \t\n":
                j += 1
            if j < n:
                nxt = text[j]
                starts = (nxt.isupper() or nxt.isdigit() or nxt in "[("
                          or "\u4e00" <= nxt <= "\u9fff")
                k = i - 1
                while k >= 0 and (text[k].isalnum() or text[k] in ".-"):
                    k -= 1
                tok = text[k + 1:i]
                if j > i + 1 and starts and tok not in _PROTECTED:
                    out.append("".join(buf))
                    buf = []
                    i = j
                    continue
            i += 1
            continue
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return [s.strip() for s in out if s.strip()]


def split_document(text):
    """Return a list of paragraphs, each a list of sentences."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    paras = re.split(r"\n\s*\n", text)
    out = []
    for p in paras:
        p = re.sub(r"\s*\n\s*", " ", p)
        sents = split_sentences(p)
        if sents:
            out.append(sents)
    return out


def compute_register(draft, locator_prefix=""):
    """Freeze the baseline register: paragraph + sentence + frozen features."""
    paras = split_document(draft)
    sentences = []
    for pi, psents in enumerate(paras):
        for si, s in enumerate(psents):
            sentences.append({
                "id": len(sentences),
                "paragraph": pi,
                "pos": si,
                "original": s,
                "locator": f"{locator_prefix}#p{pi + 1}s{si + 1}" if locator_prefix else f"#p{pi + 1}s{si + 1}",
                "features": extract_features(s),
            })
    return {"schema": "conservation-register/1",
            "paragraphs": len(paras), "sentences": sentences}


# ---------------------------------------------------------------------------
# Conservation diff
# ---------------------------------------------------------------------------

def diff_sentence(base_feats, revised_feats, hedge_justified=False):
    """Diff frozen baseline features against recomputed revised features."""
    errors, warnings = [], []

    for cat in sorted(STRICT_FIELDS):
        b = Counter(base_feats.get(cat, []))
        r = Counter(revised_feats.get(cat, []))
        for tok, cnt in sorted(b.items()):
            if r.get(tok, 0) < cnt:
                errors.append({"category": cat, "kind": "dropped", "token": tok,
                               "baseline": cnt, "revised": r.get(tok, 0)})
        for tok, cnt in sorted(r.items()):
            if cnt > b.get(tok, 0):
                warnings.append({"category": cat, "kind": "added", "token": tok,
                                 "baseline": b.get(tok, 0), "revised": cnt})

    for cat in sorted(WARN_FIELDS):
        b = Counter(base_feats.get(cat, []))
        r = Counter(revised_feats.get(cat, []))
        if b != r:
            warnings.append({"category": cat, "kind": "changed",
                             "baseline": dict(b), "revised": dict(r)})

    hb = len(base_feats.get("hedges", []))
    hr = len(revised_feats.get("hedges", []))
    sb = len(base_feats.get("strong", []))
    sr = len(revised_feats.get("strong", []))

    if hr < hb:
        item = {"category": "hedges", "kind": "strengthened",
                "baseline": hb, "revised": hr}
        (warnings if hedge_justified else errors).append(item)
    elif hr > hb:
        warnings.append({"category": "hedges", "kind": "weakened",
                         "baseline": hb, "revised": hr})

    if sr > sb:
        item = {"category": "strong", "kind": "strong_added",
                "baseline": sb, "revised": sr}
        (warnings if hedge_justified else errors).append(item)
    elif sr < sb:
        warnings.append({"category": "strong", "kind": "strong_removed",
                         "baseline": sb, "revised": sr})

    return errors, warnings


def _ws_norm(s):
    return re.sub(r"\s+", "", s or "")


def run_checks(register, edits):
    """Run the full conservation + coverage check.

    register: {"sentences": [{id, original, features, ...}]}
    edits:    {"units": [{sentence_id, original, revised, hedge_justified, ...}]}
    """
    errors, warnings = [], []
    sent_map = {s["id"]: s for s in (register or {}).get("sentences", [])}
    units = (edits or {}).get("units", [])
    seen = set()

    for u in units:
        sid = u.get("sentence_id")
        if sid is None:
            errors.append({"sentence_id": None, "category": "coverage",
                           "kind": "missing_sentence_id", "token": ""})
            continue
        if sid in seen:
            warnings.append({"sentence_id": sid, "category": "coverage",
                             "kind": "duplicate_unit", "token": ""})
        seen.add(sid)

        base = sent_map.get(sid)
        if base is None:
            errors.append({"sentence_id": sid, "category": "coverage",
                           "kind": "unknown_sentence_id", "token": str(sid)})
            continue

        if _ws_norm(base["original"]) != _ws_norm(u.get("original", "")):
            errors.append({"sentence_id": sid, "category": "coverage",
                           "kind": "original_mismatch", "token": ""})

        revised = u.get("revised")
        if revised is None or not str(revised).strip():
            errors.append({"sentence_id": sid, "category": "coverage",
                           "kind": "missing_revised", "token": ""})
            continue

        rfeat = extract_features(str(revised))
        justified = bool(u.get("hedge_justified")) or bool(u.get("justification"))
        e2, w2 = diff_sentence(base.get("features", {}), rfeat, justified)
        for e in e2:
            e["sentence_id"] = sid
        for w in w2:
            w["sentence_id"] = sid
        errors.extend(e2)
        warnings.extend(w2)

    for sid in sorted(sent_map):
        if sid not in seen:
            errors.append({"sentence_id": sid, "category": "coverage",
                           "kind": "missing_unit", "token": ""})

    return {"ok": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "summary": {"errors": len(errors), "warnings": len(warnings),
                        "sentences": len(sent_map), "units": len(units)}}
