# -*- coding: utf-8 -*-
"""Deterministic consistency and provenance checks for long-document writing.

The Agent writes the prose; this module does the mechanical, repeatable part:
term-variant detection, numbering/cross-reference checks, citation extraction,
quote-to-source localization, and output-contract validation. Every check is
heuristic and reports candidate issues, never hard failures, so a human or the
Agent can adjudicate.
"""
import json
import re
from collections import Counter, defaultdict

import fileio as io_util  # sibling module (same directory)

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}

# ---- output contract ------------------------------------------------------

def find_output_contract(case):
    """Return (contract_fields, contract_source). contract_fields is a list of
    field names the runtime case requires in result.json; contract_source
    records where they were found. If absent, returns a safe default."""
    if isinstance(case, dict):
        for key in ("output_contract", "outputContract", "required_output"):
            val = case.get(key)
            if isinstance(val, dict):
                fields = [k for k in val.keys() if isinstance(k, str)]
                return fields, key
            if isinstance(val, list):
                fields = [str(v) for v in val]
                return fields, key
    # Default for this writing track: deliverable artifacts + evidence.
    return ["deliverables", "evidence_map"], "default"


def extract_case_id(case, task_text, input_dir):
    """Best-effort case_id extraction, ordered by reliability."""
    if isinstance(case, dict):
        for key in ("case_id", "caseId", "id"):
            if isinstance(case.get(key), str) and case[key].strip():
                return case[key].strip()
    if task_text:
        m = re.search(r"challenge_id[：:]\s*`?([A-Za-z0-9_\-]+)`?", task_text)
        if m:
            return m.group(1)
    if input_dir:
        base = os_basename(input_dir)
        if base and base not in ("input", "/"):
            return "input:" + base
    return "unknown"


def os_basename(path):
    import os
    return os.path.basename(path.rstrip("/\\"))


def validate_result(result):
    """Check the required envelope. Returns list of problem strings (empty = ok)."""
    problems = []
    if not isinstance(result, dict):
        return ["result_json_is_not_object"]
    if not result.get("case_id"):
        problems.append("missing_or_empty_case_id")
    status = result.get("status")
    if status not in ALLOWED_STATUS:
        problems.append("invalid_or_missing_status:%r" % status)
    for key in ("sources", "uncertainty", "change_log"):
        if key not in result:
            problems.append("missing_%s" % key)
        elif not isinstance(result[key], list):
            problems.append("%s_not_list" % key)
    return problems


def merge_contract_fields(result, contract_fields):
    """Ensure every contract field exists at top level. Missing fields are set
    to null so the JSON always matches the runtime contract shape."""
    for f in contract_fields:
        if f not in result:
            result[f] = None
    return result


# ---- term variants --------------------------------------------------------

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\.\-\_]{2,}")
_STOP_KEYWORDS = {"the", "and", "for", "with", "from", "that", "this", "are",
                  "was", "were", "have", "has", "had", "not", "but", "all",
                  "can", "may", "will", "would", "should", "each", "than",
                  "then", "also", "such", "which", "into", "its", "their"}


def term_variants(text):
    """Find ASCII tokens whose normalized key has more than one surface form.
    Catches hyphenation/spacing/case inconsistencies across chapters, e.g.
    'RNN' vs 'rnn', 'pre-training' vs 'pretraining' vs 'pre training'.
    Returns a list of {"key", "forms", "counts"} sorted by variant count desc."""
    if not text:
        return []
    groups = defaultdict(Counter)
    for tok in TOKEN_RE.findall(text):
        if len(tok) < 3:
            continue
        key = tok.lower().lstrip(".").replace("-", "").replace("_", "").rstrip(".")
        if key in _STOP_KEYWORDS or len(key) < 3:
            continue
        groups[key][tok] += 1
    out = []
    for key, counter in groups.items():
        forms = dict(counter)
        if len(forms) > 1 and sum(counter.values()) >= 2:
            out.append({"key": key, "forms": forms,
                        "counts": sum(counter.values())})
    out.sort(key=lambda r: (-len(r["forms"]), -r["counts"], r["key"]))
    return out


# ---- numbering ------------------------------------------------------------

NUM_PATTERNS = {
    "figure_zh": re.compile(r"图\s*(\d{1,3})(?:[\.\-](\d{1,3}))?"),
    "figure_en": re.compile(r"\b(?:Fig(?:ure)?\.?)\s*(\d{1,3})"),
    "table_zh": re.compile(r"表\s*(\d{1,3})(?:[\.\-](\d{1,3}))?"),
    "table_en": re.compile(r"\b(?:Table)\s+(\d{1,3})"),
    "equation_zh": re.compile(r"式\s*[（(](\d{1,3})(?:[\.\-](\d{1,3}))?[)）]"),
    "equation_en": re.compile(r"Eq(?:uation)?\.?\s*\(?(\d{1,3})\)?"),
    "section": re.compile(r"\b(\d{1,2}(?:\.\d{1,2}){1,3})\b"),
}


def numbering_report(text, chapter_label="第"):
    """Collect defined numeric labels and referenced labels for figures/tables/
    equations/sections. Returns {"defined": {kind: [nums]},
    "referenced": {kind: [nums]}, "issues": [str]}."""
    defined = {"figure": [], "table": [], "equation": [], "section": []}
    referenced = {"figure": [], "table": [], "equation": [], "section": []}
    issues = []

    def num(kind, m):
        main = int(m.group(1))
        return main

    # Figures: definition vs reference by surrounding words is ambiguous;
    # "图 X" used as a caption marker is a definition, "见图 X" is a reference.
    # We report both lists and let the caller interpret.
    for m in NUM_PATTERNS["figure_zh"].finditer(text):
        defined["figure"].append(num("figure", m))
    for m in NUM_PATTERNS["figure_en"].finditer(text):
        defined["figure"].append(num("figure", m))
    for m in NUM_PATTERNS["table_zh"].finditer(text):
        defined["table"].append(num("table", m))
    for m in NUM_PATTERNS["table_en"].finditer(text):
        defined["table"].append(num("table", m))
    for m in NUM_PATTERNS["equation_zh"].finditer(text):
        defined["equation"].append(num("equation", m))
    for m in NUM_PATTERNS["equation_en"].finditer(text):
        defined["equation"].append(num("equation", m))

    # Cross references explicitly introduced by a lead word.
    for m in re.finditer(r"(?:见|参见|如图|如表|见图|见表)\s*图?\s*(\d{1,3})", text):
        referenced["figure"].append(int(m.group(1)))
    for m in re.finditer(r"(?:见|参见)\s*表\s*(\d{1,3})", text):
        referenced["table"].append(int(m.group(1)))
    for m in re.finditer(r"(?:见|参见)\s*式?\s*[（(](\d{1,3})[)）]", text):
        referenced["equation"].append(int(m.group(1)))

    for kind in ("figure", "table", "equation"):
        refset = set(referenced[kind])
        defset = set(defined[kind])
        for r in sorted(refset):
            if r not in defset:
                issues.append("crossref_%s_undefined:%d" % (kind, r))
        nums = sorted(defset)
        if nums:
            for i, n in enumerate(nums):
                if i > 0 and n > nums[i - 1] + 1:
                    issues.append("numbering_%s_gap_after:%d" % (kind, nums[i - 1]))
            if nums[0] != 1:
                issues.append("numbering_%s_starts_at:%d" % (kind, nums[0]))

    return {"defined": {k: sorted(set(v)) for k, v in defined.items()},
            "referenced": {k: sorted(set(v)) for k, v in referenced.items()},
            "issues": issues}


# ---- citations ------------------------------------------------------------

CITE_RE = re.compile(r"\[(\d{1,3}(?:\s*[-,，]\s*\d{1,3})*)\]")
AUTHOR_YEAR_RE = re.compile(r"\(([A-Z][A-Za-z\-]+(?:\s+(?:et\s+al\.?|and)\s+[A-Z][A-Za-z\-]+)?,?\s+\d{4}[a-z]?)\)")


def citation_report(text):
    """Extract numeric and author-year citations. Returns {"numeric": [nums],
    "author_year": [str], "count": int, "issues": [str]}."""
    numeric = []
    author_year = []
    for m in CITE_RE.finditer(text):
        for part in re.split(r"[,\s，]+", m.group(1)):
            part = part.strip()
            if not part or part == "-":
                continue
            if "-" in part:
                a, _, b = part.partition("-")
                try:
                    numeric.extend(range(int(a), int(b) + 1))
                except ValueError:
                    continue
            else:
                try:
                    numeric.append(int(part))
                except ValueError:
                    continue
    for m in AUTHOR_YEAR_RE.finditer(text):
        author_year.append(m.group(1))
    issues = []
    if numeric:
        seen = set(numeric)
        mx = max(numeric)
        missing = [i for i in range(1, mx + 1) if i not in seen]
        if missing:
            issues.append("citation_gap_missing_refs:" + ",".join(map(str, missing[:20])))
    return {"numeric": sorted(set(numeric)), "author_year": author_year,
            "count": len(set(numeric)) + len(author_year), "issues": issues}


# ---- evidence localization ------------------------------------------------

def locate_in_text(needle, text, context=160, max_hits=5):
    """Case-insensitive, whitespace-insensitive substring search. Returns list
    of {"offset", "quote"} with surrounding context. Deterministic."""
    if not text or not needle:
        return []
    hay = re.sub(r"\s+", " ", text)
    nd = re.sub(r"\s+", " ", needle).strip()
    if len(nd) < 3:
        return []
    low_hay = hay.lower()
    low_nd = nd.lower()
    hits = []
    pos = 0
    while True:
        i = low_hay.find(low_nd, pos)
        if i < 0 or len(hits) >= max_hits:
            break
        s = max(0, i - context)
        e = min(len(hay), i + len(nd) + context)
        hits.append({"offset": i, "quote": hay[s:e].strip()})
        pos = i + 1
    return hits


def locate_evidence(material_texts, claims, context=160):
    """For each claim string, find matching quotes across material files.
    material_texts: dict {locator -> text}. claims: list of strings.
    Returns {"claims": [{"claim", "matches": [{locator, quote}]}]}."""
    out = []
    for claim in claims:
        matches = []
        for loc, text in material_texts.items():
            for hit in locate_in_text(claim, text, context=context):
                matches.append({"locator": loc, "quote": hit["quote"]})
        out.append({"claim": claim, "matches": matches[:10]})
    return {"claims": out}


# ---- top-level check runner ----------------------------------------------

def run_checks(material_texts, draft_texts, claims):
    """Run all checks over material + draft text. Returns a JSON-serializable
    report. draft_texts: dict {name -> text}."""
    all_text = "\n\n".join(list(material_texts.values()) + list(draft_texts.values()))
    report = {
        "term_variants": term_variants(all_text)[:30],
        "numbering": numbering_report(all_text),
        "citations": citation_report(all_text),
        "evidence": locate_evidence(material_texts, claims),
        "stats": {
            "material_files": len(material_texts),
            "draft_files": len(draft_texts),
            "material_chars": sum(len(t) for t in material_texts.values()),
            "draft_chars": sum(len(t) for t in draft_texts.values()),
        },
    }
    return report
