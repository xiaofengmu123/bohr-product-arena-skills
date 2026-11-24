"""Deterministic citation engine for the reference & citation management skill.

This module contains only deterministic, explainable transformations. Semantic
judgments (does this manuscript claim actually match this reference's thesis?)
are recorded by the calling Agent into the evidence map; this module supplies
the mechanical backbone:

* library parsing (BibTeX / RIS / reference JSON / CSV / TSV),
* entry normalisation and deduplication with explainable merge evidence,
* citation-marker extraction and first-appearance renumbering,
* claim extraction with per-claim locators and verbatim quotes,
* GB/T 7714-2015 and APA reference formatting,
* manuscript <-> reference-list consistency checks.

Pure standard library. Idempotent: same input yields the same output.
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Text / file helpers
# --------------------------------------------------------------------------- #

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB per-file cap


def read_text_bytes(path: Any, ledger: Dict[str, List[str]]) -> str:
    """Read a UTF-8 text file with BOM tolerance and encoding fallback.

    Never raises on bad encodings: records a degradation and returns the
    best-effort text (or empty string for oversized/binary files).
    """
    from pathlib import Path

    p = Path(path)
    try:
        data = p.read_bytes()
    except Exception as exc:  # noqa: BLE001
        _record(ledger, "errors", "read failed %s: %s" % (p.name, exc))
        return ""
    if len(data) > MAX_FILE_BYTES:
        _record(ledger, "degradations", "skipped oversized file %s" % p.name)
        return ""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    _record(ledger, "degradations", "binary/non-text file %s skipped" % p.name)
    return ""


def load_json_file(path: Any, ledger: Dict[str, List[str]]) -> Optional[Any]:
    text = read_text_bytes(path, ledger)
    if not text:
        return None
    return load_json_text(text, _name(path), ledger)


def load_json_text(text: str, name: str, ledger: Dict[str, List[str]]) -> Optional[Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        _record(ledger, "errors", "bad JSON in %s: %s" % (name, exc))
        return None


def _name(path: Any) -> str:
    from pathlib import Path
    return Path(path).name


def _record(ledger: Dict[str, List[str]], key: str, message: str) -> None:
    ledger.setdefault(key, [])
    if message not in ledger[key]:
        ledger[key].append(message)


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #

def _strip(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_title(title: str) -> str:
    """Canonical title key: lowercase, collapse whitespace, drop punctuation."""
    t = _strip(title).lower()
    t = re.sub(r"[\[\]{}<>()\u201c\u201d\u2018\u2019\u300a\u300b\u3008\u3009]", "", t)
    t = re.sub(r"[^\w\u4e00-\u9fff]+", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def normalize_doi(doi: str) -> str:
    """Normalise a DOI to its bare lowercase form, tolerating URLs."""
    d = _strip(doi).lower().replace("%20", "")
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    d = re.sub(r"^doi:\s*", "", d, flags=re.IGNORECASE)
    return d.strip()


def _split_author_string(text: str) -> List[str]:
    """Split an author field into individual authors.

    Author separators are ' and ', ';', '；' only; the comma WITHIN an author
    ("Smith, John") separates surname from given name and must not be treated
    as an author separator.
    """
    return [p.strip() for p in re.split(r"\s*(?:and|;|；)\s*", text, flags=re.IGNORECASE) if p.strip()]


def first_author_surname(authors: Any) -> str:
    """Best-effort first author surname, tolerant of dict/list/string forms."""
    if authors is None:
        return ""
    if isinstance(authors, dict):
        fam = authors.get("family") or authors.get("surname") or authors.get("last") or ""
        if _strip(fam):
            return _strip(fam).lower()
        given = authors.get("given") or authors.get("first") or authors.get("forename") or ""
        return _strip(given).lower()
    if isinstance(authors, (list, tuple)):
        return first_author_surname(authors[0]) if authors else ""
    s = _strip(str(authors))
    if not s:
        return ""
    first = _split_author_string(s)[0]
    if "," in first:
        return first.split(",", 1)[0].strip().lower()
    tokens = first.split()
    if not tokens:
        return ""
    # "Smith J" -> Smith; "John Smith" -> Smith
    if len(tokens) >= 2 and len(tokens[-1]) == 1:
        return tokens[0].lower()
    return tokens[-1].lower()


def _normalize_json_entry(item: Any) -> Dict[str, Any]:
    """Coerce a JSON/table record into a flat reference dict."""
    if not isinstance(item, dict):
        return {}
    out: Dict[str, Any] = {}
    aliases = {
        "title": ["title", "题名", "ti", "name", "文献题名"],
        "authors": ["authors", "author", "au", "作者", "creator", "creators"],
        "year": ["year", "pubyear", "publicationyear", "date", "出版年", "py", "year_pub"],
        "journal": ["journal", "journaltitle", "source", "刊名", "jtitle", "publication", "jo"],
        "volume": ["volume", "卷", "vl"],
        "issue": ["issue", "number", "期", "no", "is"],
        "pages": ["pages", "page", "页码", "sp", "ep", "pp"],
        "doi": ["doi", "id_doi"],
        "url": ["url", "link", "网址"],
        "type": ["type", "entrytype", "文献类型", "ty"],
        "publisher": ["publisher", "出版社", "pb"],
        "abstract": ["abstract", "摘要", "ab"],
        "isbn": ["isbn"],
        "issn": ["issn"],
    }
    low = {_strip(k).lower(): v for k, v in item.items()}
    for canon, keys in aliases.items():
        for k in keys:
            if k in low:
                out[canon] = _strip(low[k])
                break
            if k in item:
                out[canon] = _strip(item[k])
                break
    used_keys = {_strip(a) for keys in aliases.values() for a in keys}
    for k, v in item.items():
        if _strip(k).lower() not in used_keys and isinstance(v, (str, int, float)) and _strip(v):
            out.setdefault(_strip(k).lower(), _strip(v))
    return out


# --------------------------------------------------------------------------- #
# Library parsing
# --------------------------------------------------------------------------- #

def parse_bibtex(text: str) -> List[Dict[str, Any]]:
    """Parse a .bib file into a list of flat reference dicts (brace-matched)."""
    entries: List[Dict[str, Any]] = []
    pos = 0
    n = len(text)
    entry_re = re.compile(r"@\s*([A-Za-z]+)\s*\{\s*([^,\s]+)\s*,")
    while pos < n:
        m = entry_re.search(text, pos)
        if not m:
            break
        etype = m.group(1).lower()
        key = m.group(2).strip()
        body_start = m.end()
        open_idx = text.find("{", m.start())
        depth = 1
        end = -1
        for q in range(open_idx + 1, n):
            ch = text[q]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = q
                    break
        if end == -1:
            break
        body = text[body_start:end]
        entry = _parse_bibtex_fields(etype, key, body)
        if entry:
            entries.append(entry)
        pos = end + 1
    return entries


def _parse_bibtex_fields(etype: str, key: str, body: str) -> Optional[Dict[str, Any]]:
    entry: Dict[str, Any] = {"entrytype": etype, "citekey": key, "type": etype}
    field_re = re.compile(
        r"([A-Za-z0-9_-]+)\s*=\s*(\{([^{}]*)\}|\"([^\"]*)\"|([^,\n]+))"
    )
    for fm in field_re.finditer(body):
        fname = fm.group(1).lower()
        value = fm.group(3)
        if value is None:
            value = fm.group(4)
        if value is None:
            value = fm.group(5)
        value = (value or "").strip().strip('{}"').strip()
        _set_bib_field(entry, fname, value)
    if entry.get("title") or entry.get("doi"):
        return entry
    return None


def _set_bib_field(entry: Dict[str, Any], fname: str, value: str) -> None:
    if fname in ("author", "authors", "editor"):
        entry.setdefault("authors", value)
    elif fname == "year":
        entry["year"] = _year_from_field(value)
    elif fname == "date":
        entry["year"] = entry.get("year") or _year_from_field(value)
    elif fname == "title":
        entry["title"] = value
    elif fname in ("journal", "journaltitle", "booktitle"):
        entry["journal"] = value
    elif fname == "volume":
        entry["volume"] = value
    elif fname in ("number", "issue"):
        entry["issue"] = value
    elif fname == "pages":
        entry["pages"] = value
    elif fname == "doi":
        entry["doi"] = value
    elif fname == "url":
        entry["url"] = value
    elif fname == "publisher":
        entry["publisher"] = value
    elif fname == "abstract":
        entry["abstract"] = value
    elif fname == "isbn":
        entry["isbn"] = value
    elif fname == "issn":
        entry["issn"] = value
    else:
        entry.setdefault(fname, value)


def parse_ris(text: str) -> List[Dict[str, Any]]:
    """Parse an RIS file into flat reference dicts."""
    entries: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    authors: List[str] = []

    def _flush() -> None:
        nonlocal current, authors
        if current is None:
            return
        if authors:
            current.setdefault("authors", authors)
        if current.get("title") or current.get("doi"):
            entries.append(current)
        current = None
        authors = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("TY  - "):
            _flush()
            current = {"entrytype": line[6:].strip().lower(), "type": line[6:].strip().lower()}
            continue
        if current is None:
            continue
        if line.startswith("ER  -"):
            _flush()
            continue
        tag = line[0:2].upper()
        value = line[6:].strip() if len(line) > 6 else ""
        mapping = {
            "TI": "title", "T1": "title", "JO": "journal", "JF": "journal",
            "JA": "journal", "T2": "journal", "VL": "volume", "IS": "issue",
            "SP": "pages_start", "EP": "pages_end", "DO": "doi", "UR": "url",
            "PY": "year", "Y1": "year", "PB": "publisher", "AB": "abstract",
            "SN": "issn", "AU": "author", "A1": "author", "AD": "author",
        }
        if tag in ("AU", "A1", "AD"):
            authors.append(value)
        elif tag in mapping:
            field = mapping[tag]
            if field == "year":
                current["year"] = _year_from_field(value)
            else:
                current[field] = value
    _flush()
    for e in entries:
        ps = e.pop("pages_start", "")
        pe = e.pop("pages_end", "")
        if ps or pe:
            e["pages"] = (ps + ("-" + pe if pe and pe != ps else "")) if ps else pe
    return entries


def _looks_like_reference(item: Dict[str, Any]) -> bool:
    """True when a record has at least one reference-identifying field.

    Guards against treating an unrelated JSON dict (e.g. case.json's
    output_contract) as a bibliographic entry.
    """
    signals = ("title", "doi", "authors", "year", "journal", "volume", "pages", "issn", "isbn")
    return any(_strip(item.get(k, "")) for k in signals)


def parse_reference_json(data: Any) -> List[Dict[str, Any]]:
    """Normalise reference JSON into flat entry dicts.

    Accepts: a list of records, a dict with a list under common keys
    (references/entries/items/data/results), or a single record that actually
    looks like a reference. Unrelated dicts (input config) yield [].
    """
    if data is None:
        return []
    if isinstance(data, list):
        return [e for e in (_normalize_json_entry(x) for x in data) if _looks_like_reference(e)]
    if isinstance(data, dict):
        for key in ("references", "entries", "items", "data", "results", "refs", "bibliography"):
            v = data.get(key)
            if isinstance(v, list):
                return [e for e in (_normalize_json_entry(x) for x in v) if _looks_like_reference(e)]
            if isinstance(v, dict):
                e = _normalize_json_entry(v)
                return [e] if _looks_like_reference(e) else []
        e = _normalize_json_entry(data)
        return [e] if _looks_like_reference(e) else []
    return []


def parse_table(text: str, ledger: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    """Best-effort CSV/TSV -> entries when a header row exists."""
    entries: List[Dict[str, Any]] = []
    try:
        sample = text[:8192]
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    except Exception:  # noqa: BLE001
        _record(ledger, "degradations", "table header not detected; skipped as library")
        return entries
    if not reader.fieldnames:
        return entries
    for row in reader:
        entries.append(_normalize_json_entry({k: (v or "").strip() for k, v in row.items()}))
    return entries


def _year_from_field(value: str) -> str:
    m = re.search(r"(1[89]\d{2}|20\d{2})", _strip(value))
    return m.group(1) if m else _strip(value)


# --------------------------------------------------------------------------- #
# Deduplication with explainable merge evidence
# --------------------------------------------------------------------------- #

def entry_dedup_key(entry: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """Return (kind, key) used for grouping duplicate records.

    DOI is the strongest signal; otherwise normalised title + first author
    surname + year. Returns None when neither signal is available.
    """
    doi = normalize_doi(entry.get("doi", ""))
    if doi:
        return ("doi", doi)
    title = normalize_title(entry.get("title", ""))
    author = first_author_surname(entry.get("authors", ""))
    year = _strip(entry.get("year", ""))
    if title and (author or year):
        return ("title", "|".join([title, author, year]))
    if title:
        return ("title", title)
    return None


def deduplicate(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Group duplicate entries; returns merged entries plus merge evidence.

    Merge rule (explainable, conservative):
    * DOI match groups records outright.
    * title match groups records when the first-author surname and year agree,
      or when exactly one of {author, year} is missing on either side and the
      other agrees.
    * Same author + year with different titles are NEVER merged (different works).
    """
    groups: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for idx, e in enumerate(entries):
        key = entry_dedup_key(e)
        if key is None:
            gid = "loose:%d" % idx
            groups[gid] = {"entries": [e], "kind": "none", "basis": []}
            order.append(gid)
            continue
        kind, k = key
        gid = "%s:%s" % (kind, k)
        if gid not in groups:
            groups[gid] = {"entries": [], "kind": kind, "basis": []}
            order.append(gid)
        groups[gid]["entries"].append(e)

    merged: List[Dict[str, Any]] = []
    merge_evidence: List[Dict[str, Any]] = []
    for gid in order:
        g = groups[gid]
        members = g["entries"]
        if len(members) == 1:
            merged.append(members[0])
            continue
        canonical = _merge_members(members)
        canonical["_merged_from"] = len(members)
        basis = _merge_basis(members)
        g["basis"] = basis
        merged.append(canonical)
        merge_evidence.append({
            "group_id": gid,
            "kind": g["kind"],
            "member_count": len(members),
            "members": [_entry_locator(m) for m in members],
            "basis": basis,
            "merged_into": _entry_locator(canonical),
        })
    return {"entries": merged, "merge_evidence": merge_evidence}


def _merge_members(members: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Prefer non-empty fields; keep the first non-empty value per field."""
    out: Dict[str, Any] = {}
    for m in members:
        for k, v in m.items():
            if k == "_merged_from":
                continue
            if v not in ("", None, []) and k not in out:
                out[k] = v
    return out


def _merge_basis(members: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Field-level provenance: which member supplied which canonical field."""
    canonical = _merge_members(members)
    basis = []
    for field, value in canonical.items():
        if field == "_merged_from":
            continue
        sources = [i for i, m in enumerate(members) if _strip(m.get(field, "")) == _strip(value)]
        basis.append({"field": field, "value": value, "from_member_indices": sources})
    return basis


def _entry_locator(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "citekey": entry.get("citekey", ""),
        "title": entry.get("title", ""),
        "doi": entry.get("doi", ""),
        "year": entry.get("year", ""),
    }


# --------------------------------------------------------------------------- #
# Claim extraction (sentence-level, with locators and verbatim quotes)
# --------------------------------------------------------------------------- #

_CITATION_MARK_RE = re.compile(
    r"\[\d+(?:\s*[-–—,，]\s*\d+)*\]"          # [1], [2-4], [1,3]
    r"|\(\s*[A-Z\u4e00-\u9fff][^()]{0,80}?\s*,\s*\d{4}\s*[a-z]?\s*\)"  # (Smith, 2020)
    r"|\(\s*[A-Z\u4e00-\u9fff][^()]{0,80}?等?,\s*\d{4}\s*\)"           # (张伟 等, 2020)
    r"|[¹²³⁴⁵⁶⁷⁸⁹⁰]+"                        # superscript citations
)


def split_sentences(text: str) -> List[Dict[str, Any]]:
    """Split text into sentences keeping 1-based line numbers and char offsets."""
    lines = text.splitlines()
    sentences: List[Dict[str, Any]] = []
    buf = ""
    start_line = 1
    start_col = 0
    abs_offset = 0
    buf_start_abs = 0

    def _flush(end_abs: int) -> None:
        nonlocal buf, start_line, start_col, buf_start_abs
        s = buf.strip()
        if s:
            sentences.append({
                "text": s,
                "line": start_line,
                "col": start_col,
                "char_start": buf_start_abs,
                "char_end": end_abs,
            })
        buf = ""
        buf_start_abs = end_abs

    for lineno, line in enumerate(lines, start=1):
        col = 0
        for ch in line:
            if not buf:
                start_line = lineno
                start_col = col
                buf_start_abs = abs_offset
            buf += ch
            if ch in "。！？；!?;\n":
                _flush(abs_offset + 1)
            elif ch == "." and _looks_like_sentence_end(line, col):
                _flush(abs_offset + 1)
            abs_offset += 1
            col += 1
        if buf:
            _flush(abs_offset)
        abs_offset += 1  # newline
    _flush(abs_offset)
    return sentences


def _looks_like_sentence_end(line: str, col: int) -> bool:
    nxt = line[col + 1] if col + 1 < len(line) else " "
    return nxt.isspace()


def extract_claims(text: str, source_name: str = "manuscript") -> Dict[str, Any]:
    """Extract claim-level provenance: sentences with citation markers plus
    factual candidate sentences (containing numbers/dates/percentages).

    Every claim carries locator {file, line, col, char_start, char_end} and a
    verbatim quote (the exact sentence substring). Nothing semantic is guessed.
    """
    sentences = split_sentences(text)
    cited = []
    candidates = []
    number_re = re.compile(r"\d")
    for s in sentences:
        has_citation = bool(_CITATION_MARK_RE.search(s["text"]))
        has_number = bool(number_re.search(s["text"]))
        rec = {
            "claim_id": "",  # assigned by caller
            "text": s["text"],
            "quote": s["text"],  # verbatim
            "locator": {
                "file": source_name,
                "line": s["line"],
                "col": s["col"],
                "char_start": s["char_start"],
                "char_end": s["char_end"],
            },
            "citation_markers": list(_CITATION_MARK_RE.findall(s["text"])),
        }
        if has_citation:
            cited.append(rec)
        elif has_number:
            candidates.append(rec)
    return {
        "cited_claims": cited,
        "candidate_claims": candidates,
        "total_sentences": len(sentences),
    }


# --------------------------------------------------------------------------- #
# Citation marker mapping / renumbering
# --------------------------------------------------------------------------- #

def _expand_marker_numbers(marker: str) -> List[int]:
    """Return all numbers a citation marker denotes, expanding a-b ranges.

    '[3-5]' -> [3, 4, 5]; '[1,2]' -> [1, 2]; '[3]' -> [3].
    """
    numbers: List[int] = []
    for part in re.split(r"[，,]", marker.strip("[]")):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"(\d+)\s*[-–—]\s*(\d+)", part)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            numbers.extend(range(lo, hi + 1))
        else:
            for d in re.findall(r"\d+", part):
                numbers.append(int(d))
    return numbers


def extract_numbered_citations(text: str) -> List[int]:
    """Return the ordered first-appearance sequence of numeric citation ids,
    with a-b ranges expanded so every cited number is accounted for."""
    seen: List[int] = []
    for m in re.finditer(r"\[(\d+(?:\s*[-–—,，]\s*\d+)*)\]", text):
        for n in _expand_marker_numbers(m.group(0)):
            if n not in seen:
                seen.append(n)
    return seen


def build_renumber_map(text: str) -> Dict[int, int]:
    """Map old citation numbers -> new numbers by first appearance order."""
    order = extract_numbered_citations(text)
    return {old: new for new, old in enumerate(order, start=1)}


def _collapse_ranges(numbers: List[int]) -> str:
    """Collapse a sorted, de-duplicated number list into 'a-b,c' notation."""
    if not numbers:
        return ""
    nums = sorted(set(numbers))
    parts: List[str] = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
        else:
            parts.append(str(start) if start == prev else "%d-%d" % (start, prev))
            start = prev = n
    parts.append(str(start) if start == prev else "%d-%d" % (start, prev))
    return ",".join(parts)


def renumber_text(text: str, mapping: Dict[int, int]) -> str:
    """Rewrite citation markers [n] / [a,b] / [c-d] per mapping.

    Scoped strictly to bracket content so non-citation numerals are untouched.
    Ranges are expanded, mapped element-wise, and re-collapsed, so a range whose
    renumbered values stay consecutive remains a range, otherwise it becomes a
    comma list. One pass with callbacks avoids renumber collisions.
    """
    def _num_repl(m: "re.Match") -> str:
        return str(mapping.get(int(m.group(0)), int(m.group(0))))

    def _bracket_repl(m: "re.Match") -> str:
        inner = m.group(1)
        out_parts: List[str] = []
        for part in re.split(r"[，,]", inner):
            part = part.strip()
            rng = re.match(r"(\d+)\s*[-–—]\s*(\d+)", part)
            if rng:
                lo, hi = int(rng.group(1)), int(rng.group(2))
                mapped = [mapping.get(n, n) for n in range(lo, hi + 1)]
                out_parts.append(_collapse_ranges(mapped))
            else:
                out_parts.append(re.sub(r"\d+", _num_repl, part))
        return "[" + ",".join(p for p in out_parts if p) + "]"

    return re.sub(r"\[([\d\s,，\-–—;；]+)\]", _bracket_repl, text)


# --------------------------------------------------------------------------- #
# Formatting: GB/T 7714-2015 and APA (7th)
# --------------------------------------------------------------------------- #

def _authors_list(entry: Dict[str, Any]) -> List[Dict[str, str]]:
    authors = entry.get("authors", "")
    if isinstance(authors, str):
        parts = _split_author_string(authors)
    elif isinstance(authors, (list, tuple)):
        parts = [a for a in authors]
    else:
        parts = []
    out: List[Dict[str, str]] = []
    for p in parts:
        p = _strip(p)
        if not p:
            continue
        if isinstance(p, dict):
            fam = p.get("family") or p.get("surname") or p.get("last") or ""
            giv = p.get("given") or p.get("first") or p.get("forename") or ""
            out.append({"family": _strip(fam), "given": _strip(giv)})
        else:
            if "," in p:
                fam, _, giv = p.partition(",")
                out.append({"family": _strip(fam), "given": _strip(giv)})
            else:
                tokens = p.split()
                if len(tokens) >= 2:
                    out.append({"family": tokens[-1], "given": " ".join(tokens[:-1])})
                else:
                    out.append({"family": p, "given": ""})
    return out


def format_authors(entry: Dict[str, Any], style: str) -> str:
    authors = _authors_list(entry)
    if not authors:
        return ""
    if style == "apa":
        names = []
        for a in authors:
            fam = a["family"]
            initials = "".join(t[0].upper() + "." for t in a["given"].split() if t) if a["given"] else ""
            names.append(fam + ((", " + initials) if initials else ""))
        if len(names) == 1:
            return names[0]
        if len(names) <= 20:
            return ", ".join(names[:-1]) + ", & " + names[-1]
        return ", ".join(names[:19]) + ", ... " + names[-1]
    # gbt7714: surname uppercase (Latin) / Chinese as-is, initials after surname
    names = []
    for a in authors:
        fam = a["family"]
        if re.search(r"[A-Za-z]", fam):
            fam = fam.upper()
        initials = "".join(t[0].upper() for t in a["given"].split() if t) if a["given"] else ""
        names.append(fam + ((" " + initials) if initials else ""))
    if len(names) <= 3:
        return ", ".join(names)
    return ", ".join(names[:3]) + ", 等"


def format_reference(entry: Dict[str, Any], style: str = "gbt7714") -> str:
    """Format one entry into a readable reference string."""
    etype = _strip(entry.get("type") or entry.get("entrytype") or "").lower()
    authors = format_authors(entry, style)
    title = _strip(entry.get("title", ""))
    year = _strip(entry.get("year", ""))
    journal = _strip(entry.get("journal", ""))
    volume = _strip(entry.get("volume", ""))
    issue = _strip(entry.get("issue", ""))
    pages = _strip(entry.get("pages", ""))
    doi = _strip(entry.get("doi", ""))
    url = _strip(entry.get("url", ""))
    publisher = _strip(entry.get("publisher", ""))

    if style == "apa":
        parts = []
        if authors:
            parts.append(authors + ("" if authors.endswith(".") else "."))
        if year:
            parts.append("(" + year + ").")
        if title:
            parts.append(title + ("" if title.endswith((".", "?", "!")) else "."))
        if journal:
            parts.append("*" + journal + "*" + ("" if journal.endswith(".") else "."))
        if volume:
            v = volume
            if issue:
                v += "(" + issue + ")"
            parts.append(v + ("." if not pages else ","))
        if pages:
            parts.append(pages + ".")
        if doi:
            parts.append("https://doi.org/" + normalize_doi(doi))
        elif url:
            parts.append(url)
        return " ".join(parts)

    # gbt7714 numeric style (default)
    if etype in ("article", "journalarticle", "j"):
        marker = "[J]"
    elif etype in ("book", "monograph", "m"):
        marker = "[M]"
    elif etype in ("conference", "inproceedings", "proceedings", "c"):
        marker = "[C]"
    elif etype in ("thesis", "phdthesis", "mastersthesis", "d"):
        marker = "[D]"
    elif etype in ("preprint", "z"):
        marker = "[Z/OL]"
    elif etype in ("online", "web", "electronic", "eb"):
        marker = "[EB/OL]"
    elif etype in ("report", "techreport", "r"):
        marker = "[R]"
    elif etype in ("patent", "p"):
        marker = "[P]"
    else:
        marker = "[Z]"

    parts = []
    if authors:
        parts.append(authors + ".")
    if title:
        parts.append(title + marker)
    if etype in ("book", "monograph", "m"):
        if publisher:
            parts.append(publisher + ",")
        if year:
            parts.append(year + ".")
    else:
        if journal:
            parts.append(journal + ",")
        if year:
            parts.append(year + ("," if (volume or issue or pages) else "."))
        if volume:
            parts.append(volume + (("(" + issue + ")") if issue else ""))
        if pages:
            parts.append(":" + pages + ".")
        elif volume and not year.endswith("."):
            parts[-1] = parts[-1] + "."
        if doi and etype != "preprint":
            parts.append("DOI:" + normalize_doi(doi) + ".")
        elif url:
            parts.append(url + ".")
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# Consistency checks (deterministic)
# --------------------------------------------------------------------------- #

def consistency_check(
    manuscript_text: str,
    reference_entries: List[Dict[str, Any]],
    renumber_map: Optional[Dict[int, int]] = None,
) -> Dict[str, Any]:
    """Detect unresolved citations, uncited entries, and numbering gaps."""
    cited_numbers = extract_numbered_citations(manuscript_text)
    if renumber_map is None:
        renumber_map = build_renumber_map(manuscript_text)
    cited_new = sorted({renumber_map.get(n, n) for n in cited_numbers})
    max_ref = len(reference_entries)
    unresolved = [n for n in cited_new if n > max_ref]
    uncited = [i for i in range(1, max_ref + 1) if i not in cited_new]
    gaps = [i for i in range(1, max_ref + 1) if i not in cited_new]
    return {
        "cited_numbers_old": cited_numbers,
        "cited_numbers_new": cited_new,
        "reference_count": max_ref,
        "unresolved_citations": unresolved,
        "uncited_entries": uncited,
        "numbering_gaps": gaps,
        "renumber_map": renumber_map,
    }


def build_evidence_map(
    claims: Dict[str, Any],
    reference_entries: List[Dict[str, Any]],
    renumber_map: Dict[int, int],
) -> Dict[str, Any]:
    """Assemble the granular evidence map skeleton.

    For each cited claim we pre-fill deterministic provenance:
    * claim locator + verbatim quote (from the manuscript), and
    * for each numeric marker, the matched reference's locator + verbatim
      metadata quotes (title / authors / doi / year) pulled from the library.

    The semantic fields (basis, confidence, rationale, match_decision) are left
    for the Agent to complete; nothing here is fabricated.
    """
    cited = claims.get("cited_claims", [])
    claim_records = []
    for idx, c in enumerate(cited, start=1):
        cid = "C%03d" % idx
        markers = []
        for m in c["citation_markers"]:
            nums = _expand_marker_numbers(m)
            for n in nums:
                new = renumber_map.get(n, n)
                if 1 <= new <= len(reference_entries):
                    markers.append(_evidence_for_reference(reference_entries[new - 1], new))
        claim_records.append({
            "claim_id": cid,
            "claim_text": c["text"],
            "claim_quote": c["quote"],
            "claim_locator": c["locator"],
            "citation_markers": c["citation_markers"],
            "cited_references": markers,
            "match_decision": "pending",  # Agent sets matched/partial/conflict/insufficient
            "basis": "",
            "confidence": "not_verified",
            "rationale": "",
            "notes": "",
        })
    return {
        "schema_version": "2.0",
        "provenance_model": "claim-level-locator-plus-verbatim-quote",
        "claims": claim_records,
        "candidate_claims": [
            {
                "claim_id": "K%03d" % i,
                "claim_text": c["text"],
                "claim_quote": c["quote"],
                "claim_locator": c["locator"],
                "citation_markers": c["citation_markers"],
                "status": "candidate_needs_citation_decision",
            }
            for i, c in enumerate(claims.get("candidate_claims", []), start=1)
        ],
        "unresolved": [],
        "conflicts": [],
    }


def _evidence_for_reference(ref: Dict[str, Any], number: int) -> Dict[str, Any]:
    """Per-reference evidence: locator + verbatim quote for each metadata field."""
    loc = _entry_locator(ref)
    return {
        "citation_number": number,
        "reference_locator": {
            "citekey": loc["citekey"],
            "doi": loc["doi"],
            "year": loc["year"],
        },
        "evidence": [
            {"field": "title", "locator": loc["citekey"] + "#title",
             "quote": _strip(ref.get("title", ""))},
            {"field": "authors", "locator": loc["citekey"] + "#authors",
             "quote": _strip(ref.get("authors", ""))},
            {"field": "year", "locator": loc["citekey"] + "#year",
             "quote": _strip(ref.get("year", ""))},
            {"field": "doi", "locator": loc["citekey"] + "#doi",
             "quote": _strip(ref.get("doi", ""))},
            {"field": "journal", "locator": loc["citekey"] + "#journal",
             "quote": _strip(ref.get("journal", ""))},
        ],
        "match_basis": "",
        "confidence": "not_verified",
        "rationale": "",
    }
