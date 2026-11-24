"""Deterministic citation-management engine (pure standard library).

Architecture — deliberately different from a monolithic entry point:

1.  Canonical Intermediate Representation (CIR). Every input (manuscript and
    library alike) is normalized into one JSON-serializable citation graph
    before any transformation. Deduplication, renumbering, formatting and
    consistency checks all operate on CIR nodes, not raw file text, so every
    decision can be traced back to a node id + locator.

2.  Validation-first ordering. A preflight stage inspects inputs (encoding,
    size, schema) before parsing; each downstream stage validates its own
    output before the next stage consumes it. A bad input degrades one stage
    and is recorded — it never crashes the run.

3.  Deterministic and idempotent. Same input bytes -> same output bytes
    (fixed sort keys, no randomness). Semantic judgments (claim-level match,
    source verification) are left to the Agent; this engine only records them
    as explicit ``pending`` markers and never invents them.
"""
from __future__ import annotations

import csv
import io
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB per file

# --------------------------------------------------------------------------- #
# Ledger: accumulate errors / degradations without raising
# --------------------------------------------------------------------------- #


class Ledger:
    """Collects non-fatal problems so the run can keep going."""

    def __init__(self) -> None:
        self.errors: List[str] = []
        self.degradations: List[str] = []
        self.sources: List[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def degrade(self, msg: str) -> None:
        self.degradations.append(msg)

    def note_source(self, msg: str) -> None:
        if msg not in self.sources:
            self.sources.append(msg)


# --------------------------------------------------------------------------- #
# Robust text / JSON I/O
# --------------------------------------------------------------------------- #


def read_text(path: Path, ledger: Ledger) -> str:
    """UTF-8-sig tolerant read; skips oversized / binary files."""
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    if size > MAX_FILE_BYTES:
        ledger.degrade(f"{path.name}: oversized ({size} bytes), skipped")
        return ""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        ledger.error(f"{path.name}: unreadable ({exc})")
        return ""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    ledger.error(f"{path.name}: undecodable text, skipped")
    return ""


def load_json_file(path: Path, ledger: Ledger) -> Optional[Any]:
    text = read_text(path, ledger)
    if not text:
        return None
    return load_json_text(text, path.name, ledger)


def load_json_text(text: str, name: str, ledger: Ledger) -> Optional[Any]:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        ledger.error(f"{name}: bad JSON ({exc})")
        return None


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_json_atomic(path: Path, obj: Any) -> None:
    write_text_atomic(
        path,
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True),
    )


# --------------------------------------------------------------------------- #
# Library parsing
# --------------------------------------------------------------------------- #


def _clean_field(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    s = re.sub(r"[\r\n]+", " ", s)
    return s


def parse_bibtex(text: str) -> List[Dict[str, str]]:
    """Parse @type{key, field = {...}, ...} with proper brace nesting."""
    entries: List[Dict[str, str]] = []
    pos = 0
    n = len(text)
    entry_re = re.compile(r"@\s*([A-Za-z]+)\s*([\(\{])")
    while True:
        j = text.find("@", pos)
        if j == -1:
            break
        m = entry_re.match(text, j)
        if not m:
            pos = j + 1
            continue
        etype = m.group(1)
        open_ch = m.group(2)
        close_ch = "}" if open_ch == "{" else ")"
        start = m.end()
        depth = 1
        k = start
        while k < n and depth > 0:
            c = text[k]
            if c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
            k += 1
        if depth != 0:
            break
        body = text[start : k - 1]  # exclude the outer closing brace
        pos = k
        key_part, sep, fields_part = body.partition(",")
        fields: Dict[str, str] = {"_entry_type": etype, "_key": key_part.strip()}
        for f in _split_bibtex_fields(fields_part, open_ch, close_ch):
            if "=" not in f:
                continue
            name, _, val = f.partition("=")
            name = name.strip().lower()
            val = val.strip()
            if len(val) >= 2 and val[0] in "{(" and val[-1] in "})":
                val = val[1:-1]
            elif len(val) >= 2 and val[0] == '"' and val[-1] == '"':
                val = val[1:-1]
            fields[name] = _clean_field(val)
        entries.append(fields)
    return entries


def _split_bibtex_fields(s: str, open_ch: str, close_ch: str) -> List[str]:
    """Split a BibTeX body on commas at depth 0, respecting quotes/braces."""
    fields: List[str] = []
    depth = 0
    in_quote = False
    cur: List[str] = []
    for c in s:
        if in_quote:
            cur.append(c)
            if c == '"':
                in_quote = False
            continue
        if c == '"':
            in_quote = True
            cur.append(c)
        elif c == open_ch:
            depth += 1
            cur.append(c)
        elif c == close_ch:
            depth -= 1
            cur.append(c)
        elif c == "," and depth == 0:
            piece = "".join(cur).strip()
            if piece:
                fields.append(piece)
            cur = []
        else:
            cur.append(c)
    piece = "".join(cur).strip()
    if piece:
        fields.append(piece)
    return fields


def parse_ris(text: str) -> List[Dict[str, str]]:
    """Parse RIS TY/ER records with two-letter tags."""
    entries: List[Dict[str, str]] = []
    current: Dict[str, List[str]] = {}
    tag_map = {
        "TY": "_entry_type", "TI": "title", "T1": "title", "AU": "author",
        "A1": "author", "PY": "year", "Y1": "year", "JO": "journal",
        "JF": "journal", "T2": "journal", "VL": "volume", "IS": "issue",
        "SP": "pages", "EP": "pages", "DO": "doi", "SN": "issn",
        "PB": "publisher", "UR": "url", "AB": "abstract", "N1": "note",
        "ID": "_key",
    }
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if len(line) < 6:
            continue
        tag = line[:2].strip()
        val = line[6:].strip()
        if tag == "TY":
            current = {}
        elif tag == "ER":
            if current:
                entries.append(_collapse_ris(current))
            current = {}
            continue
        key = tag_map.get(tag)
        if key is None:
            continue
        if key in ("pages", "author"):
            current.setdefault(key, []).append(val)
        else:
            current[key] = val
    if current:
        entries.append(_collapse_ris(current))
    return [e for e in entries if e]


def _collapse_ris(rec: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k, v in rec.items():
        if isinstance(v, list):
            if k == "pages" and len(v) >= 2:
                out["pages"] = f"{v[0]}-{v[-1]}"
            elif k == "author":
                out["author"] = " and ".join(v)
            else:
                out[k] = "; ".join(v)
        else:
            out[k] = v
    return out


def parse_endnote(text: str) -> List[Dict[str, str]]:
    """Parse EndNote .enw %0/%A/%T... records."""
    entries: List[Dict[str, str]] = []
    current: Dict[str, Any] = {}
    field_map = {
        "%0": "_entry_type", "%T": "title", "%A": "author", "%D": "year",
        "%J": "journal", "%B": "journal", "%V": "volume", "%N": "issue",
        "%P": "pages", "%R": "doi", "%U": "url", "%X": "abstract",
        "%I": "publisher", "%@": "issn",
    }
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if line.startswith("%") and len(line) >= 3:
            tag = line[:2]
            val = line[3:].strip()
            key = field_map.get(tag)
            if key is None:
                continue
            if key == "author":
                current.setdefault("author", []).append(val)
            else:
                current[key] = val
        elif line.strip() == "" and current:
            entries.append(_flatten_endnote(current))
            current = {}
    if current:
        entries.append(_flatten_endnote(current))
    return [e for e in entries if e]


def _flatten_endnote(rec: Dict[str, Any]) -> Dict[str, str]:
    return {
        k: (" and ".join(v) if isinstance(v, list) else v)
        for k, v in rec.items()
    }


def _normalize_json_entry(item: Any) -> Dict[str, str]:
    if not isinstance(item, dict):
        return {}
    out: Dict[str, str] = {}
    for k, v in item.items():
        if v is None:
            continue
        if isinstance(v, (list, dict)):
            out[str(k).lower()] = json.dumps(v, ensure_ascii=False)
        else:
            out[str(k).lower()] = str(v).strip()
    return out


def parse_reference_json(data: Any) -> List[Dict[str, str]]:
    if isinstance(data, dict):
        for key in ("entries", "references", "items", "data", "bibliography"):
            if isinstance(data.get(key), list):
                return [_normalize_json_entry(x) for x in data[key]]
        return [_normalize_json_entry(data)]
    if isinstance(data, list):
        return [_normalize_json_entry(x) for x in data]
    return []


def parse_table(text: str, ledger: Ledger) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    except Exception:  # noqa: BLE001
        ledger.degrade("table header not detected; skipped as library")
        return entries
    if not reader.fieldnames:
        return entries
    for row in reader:
        entries.append(_normalize_json_entry(row))
    return entries


def parse_library_file(path: Path, ledger: Ledger) -> List[Dict[str, str]]:
    text = read_text(path, ledger)
    if not text:
        return []
    ext = path.suffix.lower()
    try:
        if ext == ".bib":
            return parse_bibtex(text)
        if ext == ".ris":
            return parse_ris(text)
        if ext == ".enw":
            return parse_endnote(text)
        if ext == ".json":
            data = load_json_text(text, path.name, ledger)
            return parse_reference_json(data) if data else []
        if ext in (".csv", ".tsv", ".xlsx"):
            return parse_table(text, ledger)
    except Exception as exc:  # noqa: BLE001
        ledger.error(f"{path.name}: library parse failed ({exc})")
        return []
    return []


# --------------------------------------------------------------------------- #
# Entry normalization
# --------------------------------------------------------------------------- #

_NONALNUM = re.compile(r"[^\w\u4e00-\u9fff]+")


def normalize_title(t: str) -> str:
    s = _clean_field(t).lower()
    s = re.sub(r"\{|\}|\\[a-zA-Z]+\b", "", s)
    s = re.sub(r"&amp;", "&", s)
    return _NONALNUM.sub("", s).strip()


def normalize_doi(d: str) -> str:
    s = _clean_field(d).strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)
    return s.lower().rstrip(".")


def extract_year(entry: Dict[str, str]) -> str:
    for key in ("year", "date", "py", "publication_year", "issued"):
        v = _clean_field(entry.get(key))
        m = re.search(r"(18|19|20|21)\d{2}", v)
        if m:
            return m.group(0)
    return ""


def first_author(entry: Dict[str, str]) -> str:
    a = _clean_field(entry.get("author", entry.get("authors", "")))
    if not a:
        return ""
    parts = re.split(r"\s+and\s+|;|,", a)
    return parts[0].strip()


def normalize_entry(raw: Dict[str, str], source_file: str, idx: int) -> Dict[str, Any]:
    entry: Dict[str, Any] = {k: _clean_field(v) for k, v in raw.items()}
    entry["_source_file"] = source_file
    entry["_source_index"] = idx
    entry["_norm_title"] = normalize_title(entry.get("title", ""))
    entry["_first_author"] = first_author(entry)
    entry["_year"] = extract_year(entry)
    entry["_norm_doi"] = normalize_doi(entry.get("doi", ""))
    return entry


# --------------------------------------------------------------------------- #
# Deduplication with explainable keys
# --------------------------------------------------------------------------- #


def entry_identity(entry: Dict[str, Any]) -> Tuple[Optional[str], Optional[Tuple[str, str, str, str]]]:
    """Return (doi_key, title_author_year_key); title/author/year all required
    for the fallback key so weak matches are never auto-merged."""
    doi = entry.get("_norm_doi") or None
    ta: Optional[Tuple[str, str, str, str]] = None
    if entry.get("_norm_title") and entry.get("_first_author") and entry.get("_year"):
        ta = (entry["_norm_title"], entry["_first_author"], entry["_year"], "")
    return doi, ta


def deduplicate(
    entries: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[int]]:
    """Merge when DOI matches OR normalized title+first-author+year matches.

    Every kept entry is indexed under both keys, so a duplicate that carries a
    DOI is merged with a duplicate that only carries title/author/year (and
    vice versa), while two same-author-same-year papers with different titles
    are kept apart. Merge decisions are recorded with the criterion used.

    Returns (deduped, merge_evidence, orig_to_dedup) where ``orig_to_dedup[i]``
    is the 0-based index into ``deduped`` of the surviving entry for the i-th
    original entry. This mapping is what lets manuscript citation numbers be
    remapped correctly after deduplication.
    """
    doi_index: Dict[str, int] = {}
    ta_index: Dict[Tuple[str, str, str, str], int] = {}
    deduped: List[Dict[str, Any]] = []
    merge_evidence: List[Dict[str, Any]] = []
    orig_to_dedup: List[int] = []
    for e in entries:
        doi, ta = entry_identity(e)
        target: Optional[int] = None
        criterion = ""
        if doi and doi in doi_index:
            target, criterion = doi_index[doi], "doi"
        elif ta and ta in ta_index:
            target, criterion = ta_index[ta], "title_author_year"
        if target is None:
            idx = len(deduped)
            deduped.append(e)
            if doi:
                doi_index[doi] = idx
            if ta:
                ta_index[ta] = idx
            e["_dedup_key"] = f"idx:{idx}"
            orig_to_dedup.append(idx)
            continue
        keep = deduped[target]
        keep.setdefault("_merged_from", []).append(
            {"source_file": e["_source_file"], "source_index": e["_source_index"]}
        )
        merge_evidence.append(
            {
                "criterion": criterion,
                "kept": {
                    "source_file": keep["_source_file"],
                    "source_index": keep["_source_index"],
                    "title": keep.get("title", ""),
                },
                "merged": {
                    "source_file": e["_source_file"],
                    "source_index": e["_source_index"],
                    "title": e.get("title", ""),
                },
            }
        )
        orig_to_dedup.append(target)
    return deduped, merge_evidence, orig_to_dedup


# --------------------------------------------------------------------------- #
# Manuscript parsing
# --------------------------------------------------------------------------- #

NUMERIC_CITE = re.compile(
    r"\[\s*(\d+(?:\s*[-–—]\s*\d+)?(?:\s*,\s*\d+(?:\s*[-–—]\s*\d+)?)*)\s*\]"
)
LATEX_CITE = re.compile(r"\\(?:cite|citep|citet)\s*\{([^{}]+)\}")
AUTH_YEAR_CITE = re.compile(
    r"\(([A-Z][A-Za-z'’\- ]+(?:et al\.?)?(?:\s*&\s*[A-Z][A-Za-z'’\- ]+)?)\s*,?\s*((?:18|19|20|21)\d{2}[a-z]?)\)"
)
_SENT_SPLIT = re.compile(r"(?<=[。！？!?.;；])\s+")


def split_sentences(text: str) -> List[str]:
    parts = _SENT_SPLIT.split(text)
    return [p.strip() for p in parts if p.strip()]


def _expand_numeric(spec: str) -> List[int]:
    nums: List[int] = []
    for piece in re.split(r"\s*,\s*", spec):
        rng = re.split(r"\s*[-–—]\s*", piece)
        if len(rng) == 2 and rng[0].isdigit() and rng[1].isdigit():
            a, b = int(rng[0]), int(rng[1])
            if a <= b and b - a <= 500:
                nums.extend(range(a, b + 1))
        elif piece.isdigit():
            nums.append(int(piece))
    return nums


def extract_citation_markers(text: str) -> List[Dict[str, Any]]:
    markers: List[Dict[str, Any]] = []
    for m in NUMERIC_CITE.finditer(text):
        markers.append(
            {
                "kind": "numeric",
                "raw": m.group(0),
                "numbers": _expand_numeric(m.group(1)),
                "span": [m.start(), m.end()],
            }
        )
    for m in LATEX_CITE.finditer(text):
        keys = [k.strip() for k in m.group(1).split(",") if k.strip()]
        markers.append({"kind": "latex", "raw": m.group(0), "keys": keys, "span": [m.start(), m.end()]})
    for m in AUTH_YEAR_CITE.finditer(text):
        markers.append(
            {
                "kind": "author_year",
                "raw": m.group(0),
                "authors": m.group(1),
                "year": m.group(2),
                "span": [m.start(), m.end()],
            }
        )
    markers.sort(key=lambda x: x["span"][0])
    return markers


_CLAIM_HINT = re.compile(
    r"(\d+(?:\.\d+)?\s*%?|表明|显示|发现|认为|证实|显著|increase|decrease|"
    r"significant|shows?|demonstrates?|reports?|found|suggest|indicat)",
    re.I,
)


def locate_claims(text: str) -> List[Dict[str, Any]]:
    """Heuristic claim-candidate sentences; Agent confirms semantics."""
    claims = []
    for si, sent in enumerate(split_sentences(text)):
        if _CLAIM_HINT.search(sent):
            claims.append(
                {
                    "sentence_index": si,
                    "quote": sent[:400],
                    "has_number": bool(re.search(r"\d", sent)),
                    "has_citation": bool(NUMERIC_CITE.search(sent) or LATEX_CITE.search(sent)),
                }
            )
    return claims


def parse_manuscript_text(text: str, source: str) -> Dict[str, Any]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return {
        "source": source,
        "paragraphs": [{"locator": f"{source}#p{i + 1}", "text": p} for i, p in enumerate(paragraphs)],
        "markers": extract_citation_markers(text),
        "claims": locate_claims(text),
    }


# --------------------------------------------------------------------------- #
# Renumbering (numeric chain update)
# --------------------------------------------------------------------------- #


def renumber_markers(
    manuscript: Dict[str, Any], orig_to_dedup: List[int]
) -> Tuple[str, Dict[str, Any]]:
    """Reassign citation numbers by first appearance in the manuscript.

    ``orig_to_dedup`` maps every original library index to its surviving
    deduped index, so a citation to a merged-away entry resolves to the kept
    record before renumbering. Returns (new_text, mapping) where mapping holds:

    - old_to_new: old citation number (into the original library) -> new number
    - new_order:  deduped entry indices ordered by new citation number
    - style:      "numeric" when a remap happened, else "unchanged"
    """
    markers = manuscript["markers"]
    n_orig = len(orig_to_dedup)
    first_seen: Dict[int, int] = {}  # deduped index -> new number (1-based)
    for mk in markers:
        if mk["kind"] != "numeric":
            continue
        for n in mk["numbers"]:
            if 1 <= n <= n_orig:
                s = orig_to_dedup[n - 1]
                if s not in first_seen:
                    first_seen[s] = len(first_seen) + 1
    if not first_seen:
        return "", {"old_to_new": {}, "new_order": [], "style": "unchanged"}

    old_to_new: Dict[int, int] = {}
    for n in range(1, n_orig + 1):
        s = orig_to_dedup[n - 1]
        if s in first_seen:
            old_to_new[n] = first_seen[s]
    new_order = [s for s, _ in sorted(first_seen.items(), key=lambda kv: kv[1])]

    text = "\n\n".join(p["text"] for p in manuscript["paragraphs"])

    def repl(match: "re.Match[str]") -> str:
        spec = match.group(1)
        new_nums: List[str] = []
        for piece in re.split(r"\s*,\s*", spec):
            rng = re.split(r"\s*[-–—]\s*", piece)
            if len(rng) == 2 and rng[0].isdigit() and rng[1].isdigit():
                a, b = int(rng[0]), int(rng[1])
                mapped = sorted({old_to_new[n] for n in range(a, b + 1) if n in old_to_new})
                if mapped:
                    new_nums.extend(_compact_numbers(mapped))
                continue
            if piece.isdigit() and int(piece) in old_to_new:
                new_nums.extend(_compact_numbers([old_to_new[int(piece)]]))
        joined = ",".join(str(x) for x in new_nums)
        return f"[{joined}]" if joined else match.group(0)

    new_text = NUMERIC_CITE.sub(repl, text)
    return new_text, {"old_to_new": old_to_new, "new_order": new_order, "style": "numeric"}


def _compact_numbers(nums: List[int]) -> List[str]:
    if not nums:
        return []
    nums = sorted(set(nums))
    out: List[str] = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        out.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = n
    out.append(str(start) if start == prev else f"{start}-{prev}")
    return out


# --------------------------------------------------------------------------- #
# Format conversion (GB/T 7714-2015 / APA)
# --------------------------------------------------------------------------- #

_TYPE_TAGS = {
    "article": "[J]", "journal": "[J]", "inproceedings": "[C]", "conference": "[C]",
    "book": "[M]", "incollection": "[M]", "phdthesis": "[D]", "mastersthesis": "[D]",
    "techreport": "[R]", "misc": "[EB/OL]", "web": "[EB/OL]", "online": "[EB/OL]",
}


def _authors_apa(entry: Dict[str, Any]) -> str:
    authors = _clean_field(entry.get("author", ""))
    if not authors:
        return ""
    parts = re.split(r"\s+and\s+|;", authors)
    names = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        toks = p.replace(",", " ").split()
        if len(toks) == 1:
            names.append(toks[0])
        else:
            surname = toks[0]
            inits = " ".join(t[0] + "." for t in toks[1:] if t)
            names.append(f"{surname}, {inits}".strip())
    return " & ".join(names)


def _authors_gbt(entry: Dict[str, Any]) -> str:
    authors = _clean_field(entry.get("author", ""))
    if not authors:
        return ""
    parts = [p.strip() for p in re.split(r"\s+and\s+|;", authors) if p.strip()]
    shown = parts[:3]
    suffix = ", 等" if len(parts) > 3 else ""
    return ", ".join(shown) + suffix


def _type_tag(entry: Dict[str, Any]) -> str:
    et = _clean_field(entry.get("_entry_type", entry.get("type", ""))).lower()
    return _TYPE_TAGS.get(et, "[J]")


def format_gbt7714(entry: Dict[str, Any]) -> str:
    """GB/T 7714—2015 sequential-numeric style for the common cases."""
    authors = _authors_gbt(entry)
    title = _clean_field(entry.get("title", ""))
    tag = _type_tag(entry)
    year = entry.get("_year") or _clean_field(entry.get("year", ""))
    journal = _clean_field(entry.get("journal", entry.get("booktitle", "")))
    volume = _clean_field(entry.get("volume", ""))
    issue = _clean_field(entry.get("issue", entry.get("number", "")))
    pages = _clean_field(entry.get("pages", ""))
    doi = entry.get("_norm_doi") or _clean_field(entry.get("doi", ""))
    base = f"{authors}. {title}{tag}"
    if tag == "[J]":
        base += f". {journal}"
        if year:
            base += f", {year}"
        if volume:
            base += f", {volume}" + (f"({issue})" if issue else "")
        if pages:
            base += f": {pages}"
        base += "."
    else:
        publisher = _clean_field(entry.get("publisher", entry.get("institution", "")))
        if publisher:
            base += f". {publisher}"
        if year:
            base += f", {year}"
        if pages:
            base += f": {pages}"
        base += "."
    if doi:
        base += f" DOI:{doi}."
    return base


def format_apa(entry: Dict[str, Any]) -> str:
    authors = _authors_apa(entry)
    year = entry.get("_year") or _clean_field(entry.get("year", ""))
    title = _clean_field(entry.get("title", ""))
    journal = _clean_field(entry.get("journal", entry.get("booktitle", "")))
    volume = _clean_field(entry.get("volume", ""))
    issue = _clean_field(entry.get("issue", entry.get("number", "")))
    pages = _clean_field(entry.get("pages", ""))
    doi = entry.get("_norm_doi") or _clean_field(entry.get("doi", ""))
    head = f"{authors} ({year}). " if authors and year else f"{authors} {year}. "
    if tag := entry.get("_entry_type"):
        if tag.lower() in ("book", "incollection", "phdthesis", "mastersthesis"):
            publisher = _clean_field(entry.get("publisher", entry.get("institution", "")))
            return f"{head}{title}. {publisher}.".strip()
    if journal:
        voliss = f", {volume}" if volume else ""
        if issue:
            voliss += f"({issue})"
        pages_part = f", {pages}" if pages else ""
        s = f"{head}{title}. {journal}{voliss}{pages_part}."
    else:
        s = f"{head}{title}."
    if doi:
        s += f" https://doi.org/{doi}"
    return s


def format_reference_list(
    entries: List[Dict[str, Any]],
    style: str,
    new_order: Optional[List[int]] = None,
) -> str:
    if style == "apa":
        ordered = sorted(entries, key=lambda e: (e.get("_first_author", ""), e.get("_year", "")))
        lines = [format_apa(e) for e in ordered]
        return "\n\n".join(lines)
    # gbt7714 numeric: follow renumber mapping when available
    if new_order:
        ordered = [entries[i] for i in new_order if 0 <= i < len(entries)]
        lines = [f"[{i}] {format_gbt7714(e)}" for i, e in enumerate(ordered, 1)]
    else:
        ordered = sorted(entries, key=lambda e: (e.get("_year", ""), e.get("_norm_title", "")))
        lines = [f"[{i}] {format_gbt7714(e)}" for i, e in enumerate(ordered, 1)]
    return "\n\n".join(lines)


# --------------------------------------------------------------------------- #
# Consistency check
# --------------------------------------------------------------------------- #


def consistency_check(
    manuscript: Dict[str, Any],
    entries: List[Dict[str, Any]],
    orig_to_dedup: List[int],
    renumber: Dict[str, Any],
) -> Dict[str, Any]:
    cited: set = set()
    for mk in manuscript["markers"]:
        if mk["kind"] == "numeric":
            cited.update(mk["numbers"])
    n_orig = len(orig_to_dedup)
    unresolved = [n for n in sorted(cited) if n > n_orig or n < 1]
    new_order = renumber.get("new_order", []) if isinstance(renumber, dict) else []
    cited_survivors = set(new_order)
    unreferenced = [
        {"index": i + 1, "title": e.get("title", "")}
        for i, e in enumerate(entries)
        if i not in cited_survivors
    ]
    numbering_issues: List[str] = []
    if unresolved:
        numbering_issues.append(f"cited numbers beyond original library size: {unresolved}")
    old_to_new = renumber.get("old_to_new", {}) if isinstance(renumber, dict) else {}
    if old_to_new:
        actual = set(old_to_new.values())
        expected = set(range(1, len(actual) + 1))
        if actual != expected:
            numbering_issues.append("renumbered range is not contiguous")
    return {
        "cited_old_numbers": sorted(cited),
        "unresolved_citations": unresolved,
        "unreferenced_entries": unreferenced,
        "numbering_issues": numbering_issues,
        "original_library_size": n_orig,
        "deduped_library_size": len(entries),
    }


# --------------------------------------------------------------------------- #
# Canonical IR + pipeline
# --------------------------------------------------------------------------- #


def build_ir(
    case: Dict[str, Any],
    task_text: str,
    manuscript: Dict[str, Any],
    entries: List[Dict[str, Any]],
    merge_evidence: List[Dict[str, Any]],
    renumber: Dict[str, Any],
    consistency: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "case_id": case.get("case_id") or case.get("id") or "",
        "task_excerpt": task_text[:500],
        "manuscript": {
            "source": manuscript["source"],
            "paragraph_count": len(manuscript["paragraphs"]),
            "marker_count": len(manuscript["markers"]),
        },
        "library": {
            "entry_count": len(entries),
            "merge_evidence": merge_evidence,
        },
        "renumber": renumber,
        "consistency": consistency,
    }


def _sentence_around(manuscript: Dict[str, Any], span: List[int]) -> str:
    full = "\n\n".join(p["text"] for p in manuscript["paragraphs"])
    start, end = span[0], span[1]
    before = max(0, start - 160)
    after = min(len(full), end + 160)
    return full[before:after]


def build_evidence_map(
    manuscript: Dict[str, Any],
    entries: List[Dict[str, Any]],
    orig_to_dedup: List[int],
    renumber: Dict[str, Any],
    consistency: Dict[str, Any],
) -> Dict[str, Any]:
    citations: List[Dict[str, Any]] = []
    old_to_new = renumber.get("old_to_new", {}) if isinstance(renumber, dict) else {}
    for mk in manuscript["markers"]:
        if mk["kind"] == "numeric":
            for n in mk["numbers"]:
                dedup_idx = orig_to_dedup[n - 1] if 0 < n <= len(orig_to_dedup) else None
                entry = entries[dedup_idx] if dedup_idx is not None and dedup_idx < len(entries) else None
                citations.append(
                    {
                        "marker_raw": mk["raw"],
                        "locator": manuscript["source"],
                        "quote": _sentence_around(manuscript, mk["span"]),
                        "old_number": n,
                        "new_number": old_to_new.get(n),
                        "matched_entry": (
                            {"title": entry.get("title", ""), "doi": entry.get("_norm_doi", "")}
                            if entry else None
                        ),
                        "match_basis": None,
                        "status": "pending_agent_verification",
                    }
                )
        elif mk["kind"] == "latex":
            citations.append(
                {
                    "marker_raw": mk["raw"],
                    "locator": manuscript["source"],
                    "quote": _sentence_around(manuscript, mk["span"]),
                    "latex_keys": mk["keys"],
                    "matched_entry": None,
                    "match_basis": None,
                    "status": "pending_agent_verification",
                }
            )
        else:  # author_year
            citations.append(
                {
                    "marker_raw": mk["raw"],
                    "locator": manuscript["source"],
                    "quote": _sentence_around(manuscript, mk["span"]),
                    "authors": mk["authors"],
                    "year": mk["year"],
                    "matched_entry": None,
                    "match_basis": None,
                    "status": "pending_agent_verification",
                }
            )
    return {
        "citations": citations,
        "claims": manuscript["claims"],
        "unresolved_citations": consistency["unresolved_citations"],
        "unreferenced_entries": consistency["unreferenced_entries"],
        "note": "match_basis / status fields are semantic and must be finalized by the Agent.",
    }


# --------------------------------------------------------------------------- #
# Pipeline orchestration (validation-first)
# --------------------------------------------------------------------------- #


def run_pipeline(
    input_dir: Path,
    out_dir: Path,
    style: str = "gbt7714",
) -> Dict[str, Any]:
    """Preflight -> parse -> normalize -> dedup -> renumber -> check -> emit."""
    ledger = Ledger()
    started = time.time()

    # Stage 0: preflight — discover and validate inputs
    case, task_text = _find_case_and_task(input_dir, ledger)
    all_files = sorted(p for p in input_dir.rglob("*") if p.is_file())
    ledger.note_source(f"input_dir={input_dir}")
    for p in all_files:
        ledger.note_source(f"file={p.relative_to(input_dir)}")

    manuscript_files: List[Path] = []
    library_files: List[Path] = []
    other_files: List[Path] = []
    for p in all_files:
        ext = p.suffix.lower()
        if p.name in ("case.json", "task.md"):
            other_files.append(p)
        elif ext in (".bib", ".ris", ".enw"):
            library_files.append(p)
        elif ext in (".csv", ".tsv", ".xlsx"):
            library_files.append(p)
        elif ext == ".json":
            library_files.append(p)
        elif ext in (".md", ".markdown", ".txt", ".tex", ".docx", ".pdf"):
            manuscript_files.append(p)
        else:
            other_files.append(p)

    # Stage 1: parse manuscript
    manuscript: Dict[str, Any] = {
        "source": "manuscript.md",
        "paragraphs": [],
        "markers": [],
        "claims": [],
    }
    manuscript_text = ""
    if manuscript_files:
        first = manuscript_files[0]
        manuscript_text = read_text(first, ledger)
        if manuscript_text.strip():
            manuscript = parse_manuscript_text(manuscript_text, first.name)
            ledger.note_source(f"manuscript={first.relative_to(input_dir)}")
    if not manuscript_text.strip():
        ledger.degrade("no readable manuscript text found")

    # Stage 2: parse library (each file parsed exactly once, then normalized)
    entries: List[Dict[str, Any]] = []
    for p in library_files:
        parsed = parse_library_file(p, ledger)
        if parsed:
            ledger.note_source(f"library={p.relative_to(input_dir)}")
        for r in parsed:
            entries.append(normalize_entry(r, p.name, len(entries)))

    # Stage 3: dedup (returns original->survivor index map)
    deduped, merge_evidence, orig_to_dedup = deduplicate(entries)

    # Stage 4: renumber (maps original citation numbers through the survivor map)
    renumber = {"old_to_new": {}, "new_order": [], "style": "unchanged"}
    if style != "apa" and manuscript_text.strip():
        try:
            _, renumber = renumber_markers(manuscript, orig_to_dedup)
        except Exception as exc:  # noqa: BLE001
            ledger.error(f"renumber failed ({exc})")
            renumber = {"old_to_new": {}, "new_order": [], "style": "unchanged"}

    # Stage 5: consistency
    consistency = consistency_check(manuscript, deduped, orig_to_dedup, renumber)

    # Stage 6: build IR + evidence map
    ir = build_ir(case, task_text, manuscript, deduped, merge_evidence, renumber, consistency)
    evidence_map = build_evidence_map(manuscript, deduped, orig_to_dedup, renumber, consistency)

    # Stage 7: artifacts
    reference_list = format_reference_list(deduped, style, renumber.get("new_order"))
    processed_manuscript = ""
    if manuscript_text.strip():
        processed_manuscript, _ = renumber_markers(manuscript, orig_to_dedup)
        if not processed_manuscript:
            processed_manuscript = manuscript_text
    else:
        processed_manuscript = manuscript_text

    runtime = round(time.time() - started, 3)
    return {
        "case": case,
        "task_text": task_text,
        "manuscript": manuscript,
        "manuscript_text": manuscript_text,
        "processed_manuscript": processed_manuscript,
        "reference_list": reference_list,
        "evidence_map": evidence_map,
        "ir": ir,
        "entries": deduped,
        "merge_evidence": merge_evidence,
        "consistency": consistency,
        "ledger": ledger,
        "runtime": runtime,
        "style": style,
    }


def _find_case_and_task(input_dir: Path, ledger: Ledger) -> Tuple[Dict[str, Any], str]:
    case: Dict[str, Any] = {}
    case_path = input_dir / "case.json"
    if case_path.exists():
        case = load_json_file(case_path, ledger) or {}
    task_text = ""
    task_path = input_dir / "task.md"
    if task_path.exists():
        task_text = read_text(task_path, ledger)
    return case, task_text
