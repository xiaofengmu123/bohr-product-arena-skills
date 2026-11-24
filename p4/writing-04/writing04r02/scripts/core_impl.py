"""Deterministic backbone for the citation-management skill.

This module contains ONLY deterministic logic: file discovery helpers, robust
UTF-8 I/O, library parsing (BibTeX/RIS/JSON/CSV/TSV), normalization,
explainable deduplication, numeric-citation renumbering, formatting
(GB/T 7714-2015 numeric / APA author-year), and consistency checks.

Semantic judgments (claim-level citation placement, claim<->reference
matching, external source verification) are intentionally NOT implemented
here: they are done by the Agent following SKILL.md and written back into
evidence_map.json / result.json.  This module never fabricates metadata,
DOIs, or scores.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import time
from pathlib import Path

# --------------------------------------------------------------------------- #
# Robust I/O
# --------------------------------------------------------------------------- #

TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB


class Ledger:
    """Collects errors / degradations / notes without ever raising."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.degradations: list[str] = []
        self.notes: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def degrade(self, msg: str) -> None:
        self.degradations.append(msg)

    def note(self, msg: str) -> None:
        self.notes.append(msg)

    def as_dict(self) -> dict:
        return {
            "errors": list(self.errors),
            "degradations": list(self.degradations),
            "notes": list(self.notes),
        }


def read_text(path: Path, ledger: Ledger) -> str:
    """Read a UTF-8-ish text file with BOM / bad-encoding fallbacks."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        ledger.error(f"{path.name}: cannot read ({exc})")
        return ""
    if len(data) > MAX_FILE_BYTES:
        ledger.degrade(f"{path.name}: exceeds {MAX_FILE_BYTES} bytes; skipped")
        return ""
    for enc in TEXT_ENCODINGS:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    ledger.degrade(f"{path.name}: undecodable bytes; partial latin-1 used")
    return data.decode("latin-1", errors="replace")


def load_json_file(path: Path, ledger: Ledger) -> dict | list | None:
    text = read_text(path, ledger)
    if not text:
        return None
    return load_json_text(text, path.name, ledger)


def load_json_text(text: str, name: str, ledger: Ledger) -> dict | list | None:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        ledger.error(f"{name}: invalid JSON ({exc})")
        return None


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def write_json_atomic(path: Path, obj) -> None:
    write_text_atomic(
        path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    )


# --------------------------------------------------------------------------- #
# Field normalization helpers
# --------------------------------------------------------------------------- #

def _get(fields: dict, *names) -> str:
    for n in names:
        v = fields.get(n)
        if v is None:
            v = fields.get(n.lower())
        if v is None:
            v = fields.get(n.upper())
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value)
    # BibTeX separates authors with " and "; RIS/JSON often with ";" or "|".
    text = re.sub(r"\s+and\s+", "|", text, flags=re.IGNORECASE)
    text = re.sub(r"[;\n]", "|", text)
    return [p.strip() for p in text.split("|") if p.strip()]


def normalize_title(title: str) -> str:
    t = (title or "").lower()
    t = t.replace("{", "").replace("}", "")
    return "".join(ch for ch in t if ch.isalnum())


def normalize_doi(doi: str) -> str:
    d = (doi or "").strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    d = re.sub(r"^doi\s*:\s*", "", d)
    d = d.strip("/ ")
    return d


def first_author_surname(authors: list) -> str:
    if not authors:
        return ""
    a = authors[0].strip()
    if "," in a:
        return a.split(",", 1)[0].strip()
    parts = a.split()
    if not parts:
        return a
    return parts[-1].strip()


def _extract_year(fields: dict) -> str:
    year = _get(fields, "year", "date", "issued")
    if year:
        m = re.search(r"(19|20)\d{2}", year)
        if m:
            return m.group(0)
    return ""


TYPE_ALIAS = {
    "article": "article", "journal": "article", "j": "article",
    "book": "book", "m": "book", "monograph": "book",
    "inproceedings": "conference", "conference": "conference",
    "proceedings": "conference", "c": "conference", "incollection": "incollection",
    "phdthesis": "thesis", "mastersthesis": "thesis", "thesis": "thesis", "d": "thesis",
    "techreport": "report", "report": "report", "r": "report",
    "misc": "misc", "electronic": "misc", "eb/ol": "misc", "web": "misc",
    "standard": "standard", "s": "standard",
    "patent": "patent", "p": "patent",
}


def normalize_type(raw: str) -> str:
    t = (raw or "").strip().lower()
    return TYPE_ALIAS.get(t, t or "misc")


def _strip_braces(s: str) -> str:
    return (s or "").replace("{", "").replace("}", "")


def normalize_entry(fields: dict, index: int, source_file: str) -> dict:
    authors = _as_list(_get(fields, "author", "authors", "au", "creator"))
    title = _strip_braces(_get(fields, "title", "ti", "primary_title"))
    doi = _get(fields, "doi", "do")
    year = _extract_year(fields)
    return {
        "index": index,
        "key": _get(fields, "id", "key", "citekey", "entry_key") or f"e{index:04d}",
        "type": normalize_type(_get(fields, "type", "ty", "entrytype")),
        "title": title,
        "title_norm": normalize_title(title),
        "authors": authors,
        "first_author_surname": first_author_surname(authors),
        "year": year,
        "doi": doi,
        "doi_norm": normalize_doi(doi),
        "journal": _get(fields, "journal", "jo", "journaltitle", "booktitle",
                        "publication", "container", "source"),
        "volume": _get(fields, "volume", "vl"),
        "issue": _get(fields, "number", "issue", "is"),
        "pages": _get(fields, "pages", "sp", "page"),
        "publisher": _get(fields, "publisher", "pb", "institution", "school"),
        "place": _get(fields, "address", "cy", "place", "location"),
        "url": _get(fields, "url", "ur", "link"),
        "abstract": _get(fields, "abstract", "ab", "n2"),
        "source_file": source_file,
        "locator": f"{source_file}#entry{index}",
        "raw": {str(k): str(v) for k, v in fields.items()},
    }


# --------------------------------------------------------------------------- #
# Library parsers
# --------------------------------------------------------------------------- #

def parse_bibtex(text: str) -> list:
    entries: list = []
    i, n = 0, len(text)
    while i < n:
        at = text.find("@", i)
        if at == -1:
            break
        m = re.match(r"\s*([A-Za-z]+)\s*\{", text[at + 1:])
        if not m:
            i = at + 1
            continue
        etype = m.group(1).lower()
        open_brace = at + 1 + m.end() - 1
        depth, k = 1, open_brace + 1
        while k < n and depth > 0:
            if text[k] == "{":
                depth += 1
            elif text[k] == "}":
                depth -= 1
            k += 1
        body = text[open_brace + 1:k - 1]
        key, fields_text = _split_top_comma(body)
        entries.append({
            "type": etype,
            "key": key.strip(),
            **_parse_bib_fields(fields_text),
        })
        i = k
    return entries


def _split_top_comma(body: str) -> tuple:
    depth = 0
    for i, ch in enumerate(body):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "," and depth == 0:
            return body[:i], body[i + 1:]
    return body, ""


def _parse_bib_fields(text: str) -> dict:
    fields: dict = {}
    i, n = 0, len(text)
    while i < n:
        while i < n and (text[i].isspace() or text[i] in ",;"):
            i += 1
        j = i
        while j < n and (text[j].isalnum() or text[j] in "-_"):
            j += 1
        name = text[i:j].lower()
        if not name:
            i += 1
            continue
        k = j
        while k < n and text[k].isspace():
            k += 1
        if k >= n or text[k] != "=":
            i = j
            continue
        k += 1
        while k < n and text[k].isspace():
            k += 1
        if k >= n:
            break
        if text[k] == "{":
            depth, m = 1, k + 1
            while m < n and depth > 0:
                if text[m] == "{":
                    depth += 1
                elif text[m] == "}":
                    depth -= 1
                m += 1
            fields[name] = text[k + 1:m - 1].strip()
            i = m
        elif text[k] == '"':
            m = k + 1
            while m < n and text[m] != '"':
                m += 1
            fields[name] = text[k + 1:m].strip()
            i = m + 1
        else:
            m = k
            while m < n and text[m] != ",":
                m += 1
            fields[name] = text[k:m].strip()
            i = m
    return fields


_RIS_TAG_MAP = {
    "TI": "title", "T1": "title", "CT": "title",
    "AU": "author", "A1": "author",
    "PY": "year", "Y1": "year", "DA": "date",
    "DO": "doi", "DI": "doi",
    "JO": "journal", "JF": "journal", "T2": "journal", "JA": "journal",
    "VL": "volume", "IS": "issue", "SP": "pages", "EP": "pages",
    "SN": "issn", "UR": "url", "L1": "url", "L2": "url",
    "AB": "abstract", "N2": "abstract",
    "PB": "publisher", "CY": "place", "ET": "edition",
}


def parse_ris(text: str) -> list:
    records: list = []
    current: dict | None = None
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        m = re.match(r"^([A-Z0-9]{2})\s*-\s?(.*)$", line)
        if not m:
            continue
        tag, value = m.group(1).upper(), m.group(2).strip()
        if tag == "TY":
            current = {"type": value, "raw": {"ty": value}}
            records.append(current)
        elif current is None:
            continue
        elif tag == "ER":
            current = None
        elif tag in _RIS_TAG_MAP:
            key = _RIS_TAG_MAP[tag]
            if key == "author":
                current.setdefault("author", []).append(value)
            elif key == "pages" and tag == "EP":
                current["pages_end"] = value
            else:
                current[key] = value
    out = []
    for r in records:
        fields = {k: v for k, v in r.items() if k != "raw"}
        if r.get("author"):
            fields["author"] = r["author"]
        if r.get("pages_end") and r.get("pages"):
            fields["pages"] = f"{r['pages']}-{r['pages_end']}"
        if "pages_end" in fields:
            del fields["pages_end"]
        if not fields:
            continue
        out.append(fields)
    return out


_REF_CONTAINER_KEYS = (
    "references", "refs", "items", "entries", "papers", "articles",
    "records", "bibliography", "data", "results", "list",
)


def parse_reference_json(data) -> list:
    items = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for key in _REF_CONTAINER_KEYS:
            if key in data and isinstance(data[key], list):
                items = data[key]
                break
        else:
            items = [data]
    return [_normalize_json_entry(it) for it in items if isinstance(it, dict)]


def _normalize_json_entry(item: dict) -> dict:
    fields = {str(k).lower(): v for k, v in item.items()}
    # Flatten a few common nested shapes.
    for k in ("author", "authors", "creator"):
        v = fields.get(k)
        if isinstance(v, list):
            names = []
            for a in v:
                if isinstance(a, dict):
                    names.append(str(a.get("name") or a.get("fullName")
                                      or f"{a.get('given', '')} {a.get('family', '')}".strip()))
                else:
                    names.append(str(a))
            fields[k] = names
    return fields


def parse_table(text: str, ledger: Ledger) -> list:
    entries = []
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    except Exception:  # noqa: BLE001
        ledger.degrade("table header not detected; skipped as library")
        return entries
    for row in reader:
        item = {str(k).lower(): (v or "").strip() for k, v in row.items() if k is not None}
        entries.append(item)
    return entries


# --------------------------------------------------------------------------- #
# Deduplication (explainable, exact-match only)
# --------------------------------------------------------------------------- #

def deduplicate(entries: list) -> tuple:
    """Merge exact DOI or exact (normalized title + first-author surname +
    year) matches.  Returns (canonical, merge_map).  No fuzzy matching, so
    ambiguous near-duplicates are left for the Agent to confirm."""
    canonical: list = []
    merge_map: list = []
    doi_index: dict = {}
    title_ay_index: dict = {}

    for e in entries:
        matched, reason = None, None
        if e["doi_norm"] and e["doi_norm"] in doi_index:
            matched = doi_index[e["doi_norm"]]
            reason = "doi_equal"
        elif e["title_norm"] and e["first_author_surname"] and e["year"]:
            key = (e["title_norm"], e["first_author_surname"], e["year"])
            if key in title_ay_index:
                matched = title_ay_index[key]
                reason = "title_author_year_equal"
        if matched is not None:
            canonical[matched]["merged_from"].append(e["index"])
            merge_map[matched]["entry_indexes"].append(e["index"])
            merge_map[matched]["reasons"].append(reason)
            _fill_missing(canonical[matched], e)
        else:
            idx = len(canonical)
            canonical.append(dict(e, merged_from=[e["index"]]))
            merge_map.append({"entry_indexes": [e["index"]], "reasons": []})
            if e["doi_norm"]:
                doi_index.setdefault(e["doi_norm"], idx)
            if e["title_norm"] and e["first_author_surname"] and e["year"]:
                title_ay_index.setdefault(
                    (e["title_norm"], e["first_author_surname"], e["year"]), idx
                )
    return canonical, merge_map


def _fill_missing(target: dict, source: dict) -> None:
    for key in ("title", "doi", "journal", "volume", "issue", "pages",
                "publisher", "place", "url", "abstract", "year"):
        if not target.get(key) and source.get(key):
            target[key] = source[key]
    if not target.get("title_norm") and source.get("title_norm"):
        target["title_norm"] = source["title_norm"]
    if not target.get("doi_norm") and source.get("doi_norm"):
        target["doi_norm"] = source["doi_norm"]
    if not target.get("authors") and source.get("authors"):
        target["authors"] = source["authors"]
        target["first_author_surname"] = source["first_author_surname"]


# --------------------------------------------------------------------------- #
# Citation detection & renumbering (numeric, plus light author-year)
# --------------------------------------------------------------------------- #

NUMERIC_CITE_RE = re.compile(
    r"\[(\d+(?:\s*[-–—]\s*\d+)?(?:\s*[,，]\s*\d+(?:\s*[-–—]\s*\d+)?)*)\]"
)
AUTHOR_YEAR_RE = re.compile(
    r"[\(（]\s*([A-Za-z\u4e00-\u9fff][^()（）]{0,80}?)\s*[,，]\s*"
    r"((?:19|20)\d{2}[a-z]?)\s*[\)）]"
)


def _expand_numbers(token: str) -> list:
    nums = []
    for part in re.split(r"[,，]", token):
        part = part.strip()
        m = re.fullmatch(r"(\d+)\s*[-–—]\s*(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a <= b:
                nums.extend(range(a, b + 1))
            else:
                nums.extend(range(b, a + 1))
        else:
            m2 = re.fullmatch(r"(\d+)", part)
            if m2:
                nums.append(int(m2.group(1)))
    return nums


def detect_citations(text: str) -> list:
    citations = []
    for m in NUMERIC_CITE_RE.finditer(text):
        citations.append({
            "style": "numeric",
            "marker": m.group(0),
            "numbers": _expand_numbers(m.group(1)),
            "start": m.start(),
            "end": m.end(),
            "line": text.count("\n", 0, m.start()) + 1,
        })
    for m in AUTHOR_YEAR_RE.finditer(text):
        citations.append({
            "style": "author-year",
            "marker": m.group(0),
            "text": m.group(1).strip(),
            "year": m.group(2),
            "numbers": [],
            "start": m.start(),
            "end": m.end(),
            "line": text.count("\n", 0, m.start()) + 1,
        })
    citations.sort(key=lambda c: c["start"])
    return citations


def build_renumber_plan(citations: list) -> dict:
    """Map old numeric ids to sequential new ids in order of first appearance."""
    order = []
    seen = set()
    for c in citations:
        if c["style"] != "numeric":
            continue
        for n in c["numbers"]:
            if n not in seen:
                seen.add(n)
                order.append(n)
    return {old: i + 1 for i, old in enumerate(order)}


def _collapse(nums: list) -> str:
    nums = sorted(set(nums))
    parts = []
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        if j > i:
            parts.append(f"{nums[i]}-{nums[j]}")
        else:
            parts.append(f"{nums[i]}")
        i = j + 1
    return ",".join(parts)


def render_citation(numbers: list, style: str = "numeric") -> str:
    if style == "numeric":
        return "[" + _collapse(numbers) + "]"
    return "[" + _collapse(numbers) + "]"


def apply_renumber(text: str, citations: list, mapping: dict) -> str:
    out = text
    for c in sorted(citations, key=lambda x: -x["start"]):
        if c["style"] != "numeric":
            continue
        new_numbers = [mapping.get(n, n) for n in c["numbers"]]
        rendered = render_citation(new_numbers, c["style"])
        out = out[: c["start"]] + rendered + out[c["end"]:]
    return out


# --------------------------------------------------------------------------- #
# Formatting (GB/T 7714-2015 numeric, APA author-year) — best-effort subset
# --------------------------------------------------------------------------- #

_G7714_LETTER = {
    "article": "J", "book": "M", "conference": "C", "incollection": "M",
    "thesis": "D", "report": "R", "misc": "EB/OL", "standard": "S",
    "patent": "P",
}


def _author_str(authors: list, style: str) -> str:
    if not authors:
        return ""
    if style == "gbt7714":
        if len(authors) > 3:
            return ", ".join(authors[:3]) + ", 等"
        return ", ".join(authors)
    # APA
    parts = []
    for a in authors:
        if "," in a:
            parts.append(a.strip())
        else:
            toks = a.split()
            if len(toks) >= 2:
                initials = "".join(t[0].upper() + ". " for t in toks[:-1])
                parts.append(f"{toks[-1]}, {initials.strip()}")
            else:
                parts.append(a.strip())
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} & {parts[1]}"
    return ", ".join(parts[:-1]) + f", & {parts[-1]}"


def _format_gbt7714(e: dict) -> str:
    letter = _G7714_LETTER.get(e["type"], "EB/OL")
    authors = _author_str(e["authors"], "gbt7714")
    head = f"{authors}. {e['title']}" if e["title"] else authors
    if letter == "J":
        s = f"{head}[J]. {e['journal']}, {e['year']}"
        if e["volume"]:
            s += f", {e['volume']}"
            if e["issue"]:
                s += f"({e['issue']})"
        if e["pages"]:
            s += f": {e['pages']}"
        s += "."
    elif letter == "M":
        s = f"{head}[M]. "
        if e["place"]:
            s += f"{e['place']}: "
        s += f"{e['publisher'] or '[出版者不详]'}, {e['year']}."
    elif letter == "EB/OL":
        s = f"{head}[EB/OL]. ({e['year'] or '无日期'}). "
        if e["url"]:
            s += f"{e['url']}. "
        s += f"DOI: {e['doi']}." if e["doi"] else ""
        s = s.rstrip() + "."
    else:
        s = f"{head}[{letter}]. {e['publisher'] or e['journal'] or ''}, {e['year']}."
    if e["doi"] and letter in ("J", "M"):
        s = s.rstrip(".") + f". DOI: {e['doi']}."
    return s


def _format_apa(e: dict) -> str:
    authors = _author_str(e["authors"], "apa")
    year = f"({e['year']})" if e["year"] else "(n.d.)"
    title = e["title"] or "[无题名]"
    if e["type"] == "article":
        s = f"{authors} {year}. {title}. {e['journal'] or '[刊名不详]'}"
        if e["volume"]:
            s += f", {e['volume']}"
            if e["issue"]:
                s += f"({e['issue']})"
        if e["pages"]:
            s += f", {e['pages']}"
        s += "."
    elif e["type"] == "book":
        s = f"{authors} {year}. {title}. {e['publisher'] or '[出版者不详]'}."
    else:
        s = f"{authors} {year}. {title}. {e['publisher'] or e['journal'] or '[来源不详]'}."
    if e["doi"]:
        s = s.rstrip(".") + f". https://doi.org/{e['doi']}"
    return s


def format_entry(e: dict, style: str) -> str:
    if style == "apa":
        return _format_apa(e)
    return _format_gbt7714(e)


def format_reference_list(canonical: list, style: str, order: list | None = None) -> str:
    if order is None:
        order = list(range(len(canonical)))
    lines = []
    for pos, idx in enumerate(order, start=1):
        entry = canonical[idx]
        if style == "apa":
            lines.append(f"{_format_apa(entry)}")
        else:
            lines.append(f"[{pos}] {_format_gbt7714(entry)}")
    return "\n\n".join(lines)


# --------------------------------------------------------------------------- #
# Consistency checks
# --------------------------------------------------------------------------- #

def consistency_check(canonical: list, citations: list, mapping: dict) -> dict:
    report = {
        "unresolved_citations": [],
        "uncited_entries": [],
        "numbering_gaps": [],
        "duplicate_citation_notes": [],
    }
    n = len(canonical)
    cited_old = set()
    for c in citations:
        if c["style"] == "numeric":
            for num in c["numbers"]:
                if num < 1 or num > n:
                    report["unresolved_citations"].append(
                        {"marker": c["marker"], "line": c["line"], "number": num}
                    )
                else:
                    cited_old.add(num)
    report["uncited_entries"] = [
        i + 1 for i in range(n) if canonical[i]["index"] not in cited_old
    ]
    new_numbers = sorted(mapping.values())
    if new_numbers:
        expected = list(range(1, new_numbers[-1] + 1))
        report["numbering_gaps"] = sorted(set(expected) - set(new_numbers))
    # Duplicate numeric appearances that are not ranges are informational only.
    seen_markers = {}
    for c in citations:
        if c["style"] == "numeric":
            seen_markers.setdefault(c["marker"], 0)
            seen_markers[c["marker"]] += 1
    report["duplicate_citation_notes"] = [
        f"marker {m!r} appears {k} times" for m, k in seen_markers.items() if k > 1
    ]
    return report


# --------------------------------------------------------------------------- #
# Discovery / classification
# --------------------------------------------------------------------------- #

LIBRARY_EXTS = {".bib", ".ris", ".enw", ".csv", ".tsv", ".xlsx"}
MANUSCRIPT_EXTS = {".md", ".markdown", ".txt", ".tex", ".docx", ".pdf"}


def discover_files(input_dir: Path, ledger: Ledger) -> dict:
    manuscript, library, others = [], [], []
    skip_root_names = {"case.json", "task.md"}
    for p in sorted(input_dir.rglob("*")):
        if not p.is_file():
            continue
        if p.parent == input_dir and p.name.lower() in skip_root_names:
            continue  # task specification files, not case materials
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size > MAX_FILE_BYTES:
            ledger.degrade(f"{p.name}: exceeds {MAX_FILE_BYTES} bytes; skipped")
            continue
        ext = p.suffix.lower()
        if ext in {".bib", ".ris", ".enw"}:
            library.append(p)
        elif ext == ".json":
            # Could be a reference library; inspected on parse.
            library.append(p)
            others.append(p)
        elif ext in MANUSCRIPT_EXTS:
            manuscript.append(p)
        elif ext in {".csv", ".tsv", ".xlsx"}:
            library.append(p)
        else:
            others.append(p)
    return {"manuscript": manuscript, "library": library, "others": others}


def parse_library(files: list, ledger: Ledger) -> list:
    entries = []
    for p in files:
        text = read_text(p, ledger)
        if not text:
            continue
        ext = p.suffix.lower()
        try:
            if ext == ".bib":
                parsed = parse_bibtex(text)
            elif ext in {".ris", ".enw"}:
                parsed = parse_ris(text)
            elif ext == ".json":
                data = load_json_text(text, p.name, ledger)
                parsed = parse_reference_json(data) if data is not None else []
            elif ext in {".csv", ".tsv"}:
                parsed = parse_table(text, ledger)
            elif ext == ".xlsx":
                ledger.degrade(f"{p.name}: .xlsx requires runtime office-reader; skipped")
                parsed = []
            else:
                parsed = []
        except Exception as exc:  # noqa: BLE001
            ledger.error(f"{p.name}: library parse failed ({exc})")
            parsed = []
        for fields in parsed:
            entries.append(normalize_entry(fields, len(entries) + 1, p.name))
    return entries


def timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
