# -*- coding: utf-8 -*-
"""Deterministic conservation checks for academic-language polishing.

Pure standard library; no network, no randomness. Every function is a pure
transform, so the same edits.json always yields the same report.

What is checked (hard errors -> must fix):
  * numbers, units, citation markers, figure/table pointers must be conserved
    between ``original`` and ``revised`` of every edit unit;
  * every number / citation / figure-table reference must be covered by a
    ``facts[]`` entry carrying a locator and a verbatim quote.

What is checked (warnings -> revert or justify in the unit):
  * hedge strength drift (a hedge word in the original missing from revised);
  * glossary canonical-form drift (a listed variant still present in revised);
  * low-confidence edits and unresolved material gaps.

The tokenizer is deliberately conservative. Ranges with en-dash, author-year
citations, chemical formulas and SI units not in UNITS are left to human
review rather than falsely flagged; see references/preservation-rules.md.
"""

import json
import re

# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------

NUMBER_RE = re.compile(
    r"(?<![\w.])[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?:[eE][-+]?\d+)?(?![\w.])"
    r"|(?<![\w.])[-+]?\d+\.\d+(?:[eE][-+]?\d+)?(?![\w.])"
    r"|(?<![\w.])[-+]?\d+(?:[eE][-+]?\d+)?(?![\w.])"
)

CITATION_RE = re.compile(r"\[\s*\d+(?:\s*[,\-–]\s*\d+)*\s*\]")

FIGTAB_RE = re.compile(
    r"(?:Fig(?:ure)?s?\.?|Table|图|表)\s*\d+(?:[A-Za-z]|\s*[A-Za-z])?",
    re.IGNORECASE,
)

UNIT_PATTERN = (
    r"%|‰|°C|℃|°F|nm|μm|µm|mm|cm|km|kg|mg|μg|µg|ng|"
    r"L|mL|μL|µL|nL|mol|mmol|μmol|µmol|nmol|M|mM|μM|µM|nM|"
    r"Da|kDa|Hz|kHz|MHz|GHz|min|bp|kbp|kb|Mb|GB|MB|kB|"
    r"×|·|/|x"
)
UNIT_RE = re.compile(r"(?:" + UNIT_PATTERN + r")", re.IGNORECASE)

HEDGE_ZH = [
    "可能", "或许", "也许", "大概", "大致", "初步", "有望", "预计", "推测",
    "假设", "提示", "似乎", "看来", "一般认为", "尚不清楚", "尚未明确",
    "尚需", "需要进一步", "有限", "可能表明", "暗示", "不排除",
]
HEDGE_EN = [
    "may", "might", "could", "possibly", "perhaps", "probably", "likely",
    "suggests", "suggested", "indicates", "appears", "appeared", "seems",
    "seemed", "potentially", "presumably", "tentatively", "preliminary",
    "preliminarily", "expected", "estimated", "proposed", "hypothesized",
    "to our knowledge", "unclear", "remains unclear", "further studies",
    "further investigation", "suggests that", "not exclude",
]


def _norm_number(raw):
    """Normalize a number token to a value-preserving canonical string."""
    s = raw.strip().replace(",", "")
    if not s:
        return raw.strip()
    try:
        return repr(float(s))
    except ValueError:
        return s


def _norm_citation(match):
    """Return frozenset of ints referenced by a bracketed citation marker."""
    body = match.group(0).strip("[]")
    parts = re.split(r"[,;\s]+", body)
    nums = set()
    for p in parts:
        if not p:
            continue
        if "-" in p or "–" in p:
            a, b = re.split(r"[-–]", p)
            try:
                nums.update(range(int(a), int(b) + 1))
            except ValueError:
                nums.add(p)
        else:
            try:
                nums.add(int(p))
            except ValueError:
                nums.add(p)
    return frozenset(nums)


def _parse_figtab(match):
    """Return (kind, number, suffix) for a figure/table pointer."""
    text = match.group(0)
    m = re.search(r"(Fig(?:ure)?s?\.?|Table|图|表)", text, re.IGNORECASE)
    kind = m.group(1).lower()
    if kind.startswith("fig"):
        kind = "fig"
    elif kind == "table":
        kind = "table"
    elif kind == "图":
        kind = "图"
    else:
        kind = "表"
    num_m = re.search(r"(\d+)([A-Za-z]?)", text)
    num = int(num_m.group(1))
    suffix = (num_m.group(2) or "").lower()
    return (kind, num, suffix)


def _numbers(text):
    out = []
    for m in NUMBER_RE.finditer(text):
        raw = m.group(0)
        # extend over a trailing unit so "3.5 mg/kg" is one conserved quantity
        end = m.end()
        rest = text[end:]
        um = UNIT_RE.match(rest.lstrip())
        unit = ""
        if um:
            span = len(rest) - len(rest.lstrip())
            unit_raw = um.group(0)
            unit = unit_raw.lower()
            end = end + span + len(unit_raw)
        out.append({"raw": raw, "norm": _norm_number(raw),
                    "unit": unit, "span": (m.start(), end)})
    return out


def extract(text):
    """Return token multiset dicts for a piece of text."""
    text = text or ""
    nums = _numbers(text)
    return {
        "numbers": [n["norm"] for n in nums],
        "numbers_raw": [n["raw"] for n in nums],
        "quantities": sorted(
            (n["norm"], n["unit"]) for n in nums if n["unit"]),
        "citations": [_norm_citation(m) for m in CITATION_RE.finditer(text)],
        "citations_raw": [m.group(0) for m in CITATION_RE.finditer(text)],
        "figtab": [_parse_figtab(m) for m in FIGTAB_RE.finditer(text)],
        "figtab_raw": [m.group(0) for m in FIGTAB_RE.finditer(text)],
        "hedges_zh": [h for h in HEDGE_ZH if h in text],
        "hedges_en": [h for h in HEDGE_EN if re.search(r"\b" + re.escape(h)
                                                       + r"\b", text,
                                                       re.IGNORECASE)],
    }


def _mset(items):
    """Canonical multiset string (sorted) for comparison."""
    return sorted(str(i) for i in items)


def _diff(kind, orig_items, rev_items):
    o = list(orig_items)
    r = list(rev_items)
    dropped, added = [], []
    for x in o:
        if x in r:
            r.remove(x)
        else:
            dropped.append(x)
    added = list(r)
    return dropped, added


# ---------------------------------------------------------------------------
# Report building
# ---------------------------------------------------------------------------

def _unit_errors(unit, facts):
    """Hard conservation errors for one edit unit."""
    errors = []
    orig = extract(unit.get("original", ""))
    rev = extract(unit.get("revised", ""))
    loc = unit.get("locator", unit.get("id", "?"))

    for label, o_items, r_items in (
        ("number", orig["numbers"], rev["numbers"]),
        ("quantity", orig["quantities"], rev["quantities"]),
        ("citation", orig["citations"], rev["citations"]),
        ("figtab", orig["figtab"], rev["figtab"]),
    ):
        dropped, added = _diff(label, o_items, r_items)
        for d in dropped:
            errors.append({"unit": unit.get("id", "?"),
                           "locator": loc,
                           "type": f"{label}_dropped",
                           "detail": str(d)})
        for a in added:
            errors.append({"unit": unit.get("id", "?"),
                           "locator": loc,
                           "type": f"{label}_added",
                           "detail": str(a)})

    # coverage: every conserved token must be traceable to a quoted fact
    quoted = "\n".join((f.get("quote", "") or "") + "\n" +
                       (f.get("fact", "") or "") for f in facts)
    for token in (orig["numbers_raw"] + orig["citations_raw"]
                  + orig["figtab_raw"]):
        if token not in quoted:
            errors.append({"unit": unit.get("id", "?"),
                           "locator": loc,
                           "type": "fact_missing_quote",
                           "detail": token})
    return errors


def _unit_warnings(unit, glossary):
    warnings = []
    orig = extract(unit.get("original", ""))
    rev = extract(unit.get("revised", ""))
    loc = unit.get("locator", unit.get("id", "?"))
    reason = (unit.get("reason") or "") + " " + (unit.get(
        "uncertainty_reason") or "")
    justified = bool(unit.get("hedge_justified")) or ("hedge" in reason.lower()
                                                      or "限定" in reason
                                                      or "强度" in reason)

    hedges = orig["hedges_zh"] + [h.lower() for h in orig["hedges_en"]]
    rev_hedges = rev["hedges_zh"] + [h.lower() for h in rev["hedges_en"]]
    for h in hedges:
        if h not in rev_hedges and not justified:
            warnings.append({"unit": unit.get("id", "?"),
                             "locator": loc,
                             "type": "hedge_drift",
                             "detail": h})

    # glossary drift: a registered variant still appears in revised text
    for g in glossary:
        for v in g.get("variants", []):
            if v and v in (unit.get("revised") or ""):
                warnings.append({"unit": unit.get("id", "?"),
                                 "locator": loc,
                                 "type": "term_drift",
                                 "detail": f"{v} -> {g.get('canonical')}"})
    return warnings


def build_report(edits):
    """Run all checks over edits.json content; return a report dict."""
    if not isinstance(edits, dict):
        return {"errors": [{"type": "edits_not_dict", "detail": ""}],
                "warnings": [], "summary": {}}
    units = edits.get("units", [])
    facts = edits.get("facts", [])
    glossary = edits.get("glossary", [])

    errors, warnings = [], []
    for u in units:
        errors.extend(_unit_errors(u, facts))
        warnings.extend(_unit_warnings(u, glossary))

    # fact entries must themselves be locatable and quoted
    for i, f in enumerate(facts):
        if not (f.get("locator") or "").strip():
            errors.append({"unit": None, "locator": None,
                           "type": "fact_unlocated", "detail": f"facts[{i}]"})
        if not (f.get("quote") or "").strip():
            errors.append({"unit": None, "locator": None,
                           "type": "fact_unquoted", "detail": f"facts[{i}]"})

    low_conf = [u.get("id") for u in units if u.get("confidence") == "low"]
    unverified = [f.get("fact") for f in facts
                  if f.get("certainty") in ("inferred", "unverified")]
    gaps = edits.get("gaps", [])

    warnings.extend({"unit": None, "locator": g.get("locator"),
                     "type": "gap_unresolved", "detail": g.get("detail") or
                     g.get("type")} for g in gaps)

    unjustified = [w for w in warnings
                   if not (w.get("detail") and
                           ("hedge" in str(w.get("detail")).lower()))]
    summary = {
        "units": len(units),
        "facts": len(facts),
        "errors": len(errors),
        "warnings": len(warnings),
        "unjustified_warnings": len(unjustified),
        "low_confidence_units": len(low_conf),
        "unverified_facts": len(unverified),
        "gaps": len(gaps),
    }
    return {"errors": errors, "warnings": warnings, "summary": summary}


# ---------------------------------------------------------------------------
# Small helpers used by main.py
# ---------------------------------------------------------------------------

def safe_json_load(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return {"_read_error": str(e)}


if __name__ == "__main__":
    # lightweight manual smoke test (no real cases)
    demo = {
        "units": [{
            "id": "u1", "locator": "draft.md#L1",
            "original": "The result may suggest a 3.5 mg/kg dose [12] (Fig. 1).",
            "revised": "The result may suggest a 3.5 mg/kg dose [12] (Fig. 1).",
            "edit_type": "grammar", "reason": "unchanged", "confidence": "high",
        }],
        "facts": [{"fact": "3.5 mg/kg dose", "locator": "draft.md#L1",
                   "quote": "3.5 mg/kg dose [12] (Fig. 1)", "certainty":
                   "verified"}],
        "glossary": [],
        "gaps": [],
    }
    rep = build_report(demo)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
