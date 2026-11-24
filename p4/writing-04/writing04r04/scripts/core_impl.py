# -*- coding: utf-8 -*-
"""Deterministic engine for the reference & citation management skill.

Design contract
---------------
* Standard library only; no network, no randomness, no filesystem access here
  (all IO lives in ``main.py``). Every function is pure: identical input
  produces identical output, so repeated runs are reproducible.
* This module owns the *deterministic* part of the pipeline: library parsing,
  deduplication with an explainable key, numeric renumbering by order of first
  appearance, GB/T 7714--2015 / APA formatting, and the cross-reference
  consistency check.
* Semantic judgments (which sentence needs a citation, whether a claim matches
  a reference, whether a DOI is genuine) are **not** performed here. Those are
  the Agent's responsibility per ``SKILL.md``; this module only supplies the
  traceable backbone and marks unresolved items ``pending_agent`` /
  ``not_verified`` instead of inventing an answer.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Tuple

__all__ = [
    "norm_text",
    "first_author_surname",
    "entry_year",
    "entry_title",
    "entry_doi",
    "entry_key",
    "deduplicate",
    "parse_bibtex",
    "parse_ris",
    "parse_reference_json",
    "normalize_entry",
    "format_gbt7714",
    "format_apa",
    "expand_marker",
    "collapse_nums",
    "find_citation_markers",
    "renumber_citations",
    "consistency_check",
    "citation_locators",
]

_PUNCT_RE = re.compile(r"[^\w]+", re.UNICODE)
_CITE_RE = re.compile(r"\[(\d+(?:\s*[,，\-–—]\s*\d+)*)\]")


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def norm_text(value: Any) -> str:
    """Lowercase, strip diacritics, collapse non-alphanumerics to one space."""
    if value is None:
        return ""
    s = str(value)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = _PUNCT_RE.sub(" ", s)
    return s.strip()


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " and ".join(str(v) for v in value)
    return str(value)


def first_author_surname(entry: Dict[str, Any]) -> str:
    """Best-effort surname of the first author, normalized."""
    raw = _as_str(entry.get("author") or entry.get("authors") or "")
    if not raw:
        return ""
    parts = re.split(r"\s+and\s+|;", raw, flags=re.IGNORECASE)
    first = parts[0].strip()
    if "," in first:  # "Surname, Given"
        first = first.split(",", 1)[0].strip()
    else:  # "Given Surname" -> take last whitespace token
        toks = first.split()
        first = toks[-1] if toks else first
    return norm_text(first)


def entry_year(entry: Dict[str, Any]) -> str:
    src = " ".join(str(entry.get(k) or "") for k in ("year", "date", "PY", "Y1", "DA"))
    m = re.search(r"(1[89]\d{2}|20\d{2}|21\d{2})", src)
    return m.group(0) if m else ""


def entry_title(entry: Dict[str, Any]) -> str:
    return _as_str(entry.get("title") or entry.get("T1") or entry.get("TI") or "")


def entry_doi(entry: Dict[str, Any]) -> str:
    doi = _as_str(entry.get("doi") or entry.get("DOI") or "").strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    return doi.strip()


def entry_key(entry: Dict[str, Any]) -> str:
    """Explainable dedup key: DOI if present, else normalized title +
    first-author surname + year, else a stable field hash."""
    doi = entry_doi(entry)
    if doi:
        # DOI-specific key: case-fold, keep punctuation so distinct DOIs never
        # collide, and drop a trailing period from sentence punctuation.
        key_doi = doi.lower().strip().rstrip(".")
        return "doi:" + key_doi
    title = norm_text(entry_title(entry))
    if title:
        surname = first_author_surname(entry)
        year = entry_year(entry)
        return "title:" + "|".join([title, surname, year])
    blob = repr(sorted((k, _as_str(v)) for k, v in entry.items() if k != "_key"))
    return "hash:" + hashlib_key(blob)


def hashlib_key(blob: str) -> str:
    import hashlib

    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Field normalization
# ---------------------------------------------------------------------------

_JSON_FIELD_ALIASES = {
    "title": ("title", "ti", "t1", "title-zh", "title_en"),
    "author": ("author", "authors", "au", "a1", "creator"),
    "year": ("year", "date", "py", "y1", "da", "pubyear", "published_year"),
    "doi": ("doi", "do"),
    "journal": ("journal", "jo", "jf", "t2", "journaltitle", "source"),
    "volume": ("volume", "vl"),
    "issue": ("issue", "is", "number", "no"),
    "pages": ("pages", "sp", "ep", "page", "startpage", "page_range"),
    "publisher": ("publisher", "pb"),
    "booktitle": ("booktitle", "bt", "conference", "proceedings"),
    "entry_type": ("type", "entrytype", "pt", "ty", "_type"),
    "url": ("url", "ur", "link"),
    "abstract": ("abstract", "ab", "n2"),
    "keywords": ("keywords", "kw"),
}


def normalize_entry(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Map heterogeneous source fields onto canonical names (case-insensitive)."""
    lowered = {}
    for k, v in raw.items():
        if isinstance(k, str):
            lowered[k.strip().lower()] = v
    out: Dict[str, Any] = {}
    for canon, aliases in _JSON_FIELD_ALIASES.items():
        for alias in aliases:
            if alias in lowered and lowered[alias] not in (None, ""):
                out[canon] = lowered[alias]
                break
    # keep anything not mapped (best effort), excluding the aliased raw keys
    for k, v in lowered.items():
        if k not in out and k not in ("_key", "_type"):
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Library parsers
# ---------------------------------------------------------------------------

def parse_bibtex(text: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    text = re.sub(r"^\s*@comment\b.*?(?=^@|\Z)", "", text,
                  flags=re.IGNORECASE | re.MULTILINE | re.DOTALL)
    pattern = re.compile(
        r"@\s*([A-Za-z]+)\s*\{([^,]*),(.*?)\}\s*(?=@|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    for m in pattern.finditer(text):
        entry_type = m.group(1).strip().lower()
        cite_key = m.group(2).strip()
        fields = _parse_bibtex_fields(m.group(3))
        entry = normalize_entry(dict(fields))
        entry.setdefault("entry_type", entry_type)
        entry.setdefault("_key", cite_key)
        entries.append(entry)
    return entries


def _parse_bibtex_fields(body: str) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    for m in re.finditer(
        r"([A-Za-z0-9_\-]+)\s*=\s*(\{.*?\}|\".*?\"|[^,]+)", body, re.DOTALL
    ):
        name = m.group(1).strip().lower()
        value = m.group(2).strip()
        if value.startswith("{") and value.endswith("}"):
            value = value[1:-1]
        elif value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        value = re.sub(r"\{([^{}]*)\}", r"\1", value)
        fields[name] = value.strip()
    return fields


_RIS_ALIASES = {
    "TY": "entry_type", "T1": "title", "TI": "title", "CT": "title",
    "AU": "author", "A1": "author", "PY": "year", "Y1": "year",
    "DA": "date", "DO": "doi", "JO": "journal", "JF": "journal",
    "T2": "journal", "VL": "volume", "IS": "issue", "SP": "pages",
    "EP": "pages", "PB": "publisher", "UR": "url", "N2": "abstract",
    "KW": "keywords", "BT": "booktitle",
}


def parse_ris(text: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {}
    for line in text.splitlines():
        stripped = line.rstrip()
        if not stripped.strip():
            continue
        if stripped.upper().startswith("ER"):
            if current:
                entries.append(normalize_entry(current))
            current = {}
            continue
        m = re.match(r"^([A-Z0-9]{2})\s*-\s*(.*)$", stripped)
        if not m:
            continue
        tag, value = m.group(1).upper(), m.group(2).strip()
        if tag in ("AU", "A1", "AD", "A2", "A3", "A4", "KW"):
            current.setdefault("_list_" + tag, []).append(value)
        else:
            current[tag] = value
    if current:
        entries.append(normalize_entry(current))
    # merge multi-author lists after normalize
    out: List[Dict[str, Any]] = []
    for e in entries:
        lists = {k: v for k, v in e.items() if k.startswith("_list_")}
        for k in lists:
            e.pop(k, None)
        for tag, vals in lists.items():
            canon = _RIS_ALIASES.get(tag.replace("_list_", ""), "")
            if canon:
                e[canon] = " and ".join(vals)
        out.append(e)
    return out


def parse_reference_json(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("references", "items", "entries", "records", "refs",
                    "bibliography", "library", "data"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            if _looks_like_entry(data):
                data = [data]
            else:
                data = []
    if not isinstance(data, list):
        return []
    return [normalize_entry(d) for d in data if isinstance(d, dict)]


def _looks_like_entry(d: Dict[str, Any]) -> bool:
    return any(k in d for k in ("title", "doi", "author", "authors", "T1", "TI", "PY"))


def parse_table(text: str) -> List[Dict[str, Any]]:
    """Best-effort CSV/TSV -> entries when a header row names known fields."""
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except Exception:
        return []
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        return []
    entries = []
    for row in reader:
        item = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k is not None}
        entries.append(normalize_entry(item))
    return entries


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def deduplicate(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge duplicates by ``entry_key`` with an explainable merge record.

    Returns ``{"entries": [...], "merge_evidence": [...], "removed": N}``.
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []
    for e in entries:
        key = entry_key(e)
        groups.setdefault(key, []).append(e)
        if key not in order:
            order.append(key)

    merged: List[Dict[str, Any]] = []
    merge_evidence: List[Dict[str, Any]] = []
    removed = 0
    for key in order:
        members = groups[key]
        if len(members) == 1:
            merged.append(members[0])
            continue
        removed += len(members) - 1
        keep = _merge_members(members)
        merged.append(keep)
        merge_evidence.append({
            "criterion": key.split(":", 1)[0],
            "dedup_key": key,
            "merged_count": len(members),
            "kept": {
                "title": entry_title(keep),
                "doi": entry_doi(keep),
                "year": entry_year(keep),
            },
            "field_conflicts": _field_conflicts(members),
        })
    return {"entries": merged, "merge_evidence": merge_evidence, "removed": removed}


def _merge_members(members: List[Dict[str, Any]]) -> Dict[str, Any]:
    keep = dict(members[0])
    for other in members[1:]:
        for k, v in other.items():
            if k.startswith("_"):
                continue
            cur = keep.get(k)
            if cur in (None, ""):
                keep[k] = v
            elif v not in (None, "") and str(cur) != str(v):
                # preserve both: prefer the non-empty richer value
                keep[k] = cur  # first wins; conflicts recorded separately
    return keep


def _field_conflicts(members: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    conflicts = []
    fields = set()
    for m in members:
        fields.update(k for k, v in m.items() if v not in (None, "") and not k.startswith("_"))
    for f in sorted(fields):
        vals = []
        for m in members:
            v = m.get(f)
            if v not in (None, "") and str(v) not in vals:
                vals.append(str(v))
        if len(vals) > 1:
            conflicts.append({"field": f, "values": vals})
    return conflicts


# ---------------------------------------------------------------------------
# Formatting (GB/T 7714--2015 numeric and APA)
# ---------------------------------------------------------------------------

def _fmt_authors_gbt(authors: str) -> str:
    if not authors:
        return "[作者缺失]"
    names = [n.strip() for n in re.split(r"\s+and\s+|;", authors, flags=re.IGNORECASE) if n.strip()]
    if not names:
        return "[作者缺失]"
    if len(names) <= 3:
        return ", ".join(names) + "."
    return ", ".join(names[:3]) + ", 等."


def _fmt_authors_apa(authors: str) -> str:
    if not authors:
        return "[No author]"
    names = [n.strip() for n in re.split(r"\s+and\s+|;", authors, flags=re.IGNORECASE) if n.strip()]
    if not names:
        return "[No author]"
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} & {names[1]}"
    return f"{names[0]} et al."


def _type_tag(entry: Dict[str, Any]) -> str:
    t = str(entry.get("entry_type") or entry.get("type") or "").upper()
    if t in ("ARTICLE", "JOUR", "JOURNAL", "J"):
        return "[J]"
    if t in ("BOOK", "M"):
        return "[M]"
    if t in ("INPROCEEDINGS", "CONFERENCE", "CPAPER", "C"):
        return "[C]"
    if t in ("THESIS", "PHDTHESIS", "MASTERSTHESIS", "D"):
        return "[D]"
    if t in ("MISC", "WEB", "ELECTRONIC", "EB"):
        return "[EB/OL]"
    return "[Z]"


def format_gbt7714(entry: Dict[str, Any], number: int) -> str:
    """Single GB/T 7714--2015 numeric-style line. Missing fields are shown as
    ``[字段缺失]`` rather than guessed."""
    authors = _fmt_authors_gbt(_as_str(entry.get("author") or ""))
    title = entry_title(entry).strip() or "[题名缺失]"
    tag = _type_tag(entry)
    year = entry_year(entry) or "[年份缺失]"
    journal = _as_str(entry.get("journal") or "").strip()
    volume = _as_str(entry.get("volume") or "").strip()
    issue = _as_str(entry.get("issue") or "").strip()
    pages = _as_str(entry.get("pages") or "").strip()
    doi = entry_doi(entry)

    if journal:
        vol_issue = volume
        if issue:
            vol_issue = f"{volume}({issue})" if volume else f"({issue})"
        tail = f"{journal}, {year}, {vol_issue}: {pages}." if vol_issue or pages else f"{journal}, {year}."
        if pages and not vol_issue:
            tail = f"{journal}, {year}: {pages}."
    else:
        tail = f"{year}."

    s = f"[{number}] {authors} {title}{tag} {tail}"
    if doi:
        s += f" DOI: {doi}."
    return s


def format_apa(entry: Dict[str, Any], number: Optional[int] = None) -> str:
    """Single APA-style line. ``number`` is retained only as an optional lead."""
    authors = _fmt_authors_apa(_as_str(entry.get("author") or ""))
    title = entry_title(entry).strip() or "[Title missing]"
    year = entry_year(entry)
    year_part = f"({year})." if year else "(n.d.)."
    journal = _as_str(entry.get("journal") or "").strip()
    volume = _as_str(entry.get("volume") or "").strip()
    issue = _as_str(entry.get("issue") or "").strip()
    pages = _as_str(entry.get("pages") or "").strip()

    if journal:
        vol_issue = volume
        if issue:
            vol_issue = f"{volume}({issue})" if volume else f"({issue})"
        rest = f"{journal}, {vol_issue}, {pages}." if vol_issue or pages else f"{journal}."
    else:
        rest = ""

    base = f"{authors} {year_part} {title}. {rest}".strip()
    if number is not None:
        base = f"[{number}] " + base
    return base


# ---------------------------------------------------------------------------
# Citation markers and renumbering
# ---------------------------------------------------------------------------

def expand_marker(inner: str) -> List[int]:
    nums: List[int] = []
    for part in re.split(r"[,，]", inner):
        part = part.strip()
        m = re.match(r"^(\d+)\s*[-–—]\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            lo, hi = (a, b) if a <= b else (b, a)
            nums.extend(range(lo, hi + 1))
        else:
            m2 = re.match(r"^(\d+)$", part)
            if m2:
                nums.append(int(m2.group(1)))
    return nums


def collapse_nums(nums: Iterable[int]) -> str:
    nums = sorted(set(int(n) for n in nums))
    parts: List[str] = []
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        if j - i >= 2:
            parts.append(f"{nums[i]}-{nums[j]}")
        elif j - i == 1:
            parts.append(f"{nums[i]},{nums[j]}")
        else:
            parts.append(str(nums[i]))
        i = j + 1
    return ",".join(parts)


def find_citation_markers(text: str) -> List[Dict[str, Any]]:
    out = []
    for m in _CITE_RE.finditer(text):
        out.append({
            "raw": m.group(0),
            "numbers": expand_marker(m.group(1)),
            "start": m.start(),
            "end": m.end(),
        })
    return out


def renumber_citations(text: str) -> Tuple[str, Dict[int, int], List[Dict[str, Any]]]:
    """Assign new numbers by order of first appearance; collapse runs to ranges."""
    mapping: Dict[int, int] = {}
    next_num = 1
    spans: List[Dict[str, Any]] = []

    def repl(match: "re.Match[str]") -> str:
        nonlocal next_num
        nums = expand_marker(match.group(1))
        if not nums:
            return match.group(0)
        new_nums = []
        for n in nums:
            if n not in mapping:
                mapping[n] = next_num
                next_num += 1
            new_nums.append(mapping[n])
        spans.append({
            "old_numbers": nums,
            "new_numbers": new_nums,
            "old_raw": match.group(0),
            "new_raw": "[" + collapse_nums(new_nums) + "]",
            "start": match.start(),
            "end": match.end(),
        })
        return "[" + collapse_nums(new_nums) + "]"

    new_text = _CITE_RE.sub(repl, text)
    return new_text, mapping, spans


# ---------------------------------------------------------------------------
# Consistency check and locators
# ---------------------------------------------------------------------------

def consistency_check(text: str, ref_indices: List[int]) -> Dict[str, Any]:
    used: List[int] = []
    for m in _CITE_RE.finditer(text):
        used.extend(expand_marker(m.group(1)))
    used = sorted(set(used))
    ref_set = set(int(n) for n in ref_indices)
    orphan = [n for n in used if n not in ref_set]
    uncited = [n for n in sorted(ref_set) if n not in used]
    gaps: List[int] = []
    if used:
        expect = set(range(1, max(used) + 1))
        gaps = sorted(expect - set(used))
    return {
        "cited_numbers": used,
        "orphan_citations": orphan,
        "uncited_entries": uncited,
        "numbering_gaps": gaps,
        "ok": not orphan and not uncited and not gaps,
    }


def citation_locators(text: str, spans: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach a stable line/column locator and a verbatim context window to each
    renumbered citation. The context is a raw slice of the source, not a
    paraphrase."""
    lines = text.splitlines()
    out = []
    for span in spans:
        start = span.get("start", 0)
        # map char offset -> line/column
        seen = 0
        line_no = 1
        col = 1
        for idx, ln in enumerate(lines):
            if start < seen + len(ln) + 1:
                line_no = idx + 1
                col = start - seen + 1
                break
            seen += len(ln) + 1
        lo = max(0, start - 120)
        hi = min(len(text), start + 120)
        quote = text[lo:hi]
        out.append({
            "new_raw": span.get("new_raw"),
            "old_raw": span.get("old_raw"),
            "locator": f"manuscript.md:L{line_no}:C{col}",
            "quote": quote,
        })
    return out
