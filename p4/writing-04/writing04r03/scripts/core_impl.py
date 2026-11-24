"""Deterministic reference & citation engine (pure standard library).

Design contract (the package's distinguishing guarantee):

1.  Determinism — every public function is pure.  The same ``/input`` tree
    produces byte-identical ``/app/output`` artifacts.  There is no randomness,
    no wall-clock dependence inside artifacts, stable ordering everywhere
    (``sorted`` / ``sort_keys=True`` / document order for markers).
2.  Robustness — bad JSON, bad encoding, oversized files, missing fields and
    empty inputs degrade gracefully; they are recorded in ``errors`` /
    ``degradations`` / ``pending_human`` and never crash the process.
3.  Honesty — this module only does the deterministic backbone (parse,
    deduplicate, renumber, format, consistency check).  Semantic judgments
    (claim <-> reference matching, cross-language title equivalence, source
    verification) are left to the Agent and are *never* invented here.

The module is importable by ``main.py`` (thin CLI) and ``selftest.py``
(determinism & degradation self-test), so the guarantee is testable.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Text & key normalization
# --------------------------------------------------------------------------- #

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\u4e00-\u9fff\u3400-\u4dbf]+", re.UNICODE)
_YEAR = re.compile(r"(?<!\d)(\d{4})(?!\d)")
_CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def norm_space(s: Any) -> str:
    return _WS.sub(" ", str(s or "")).strip()


def norm_key(s: Any) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    return _PUNCT.sub("", s.lower())


def first_year(s: Any) -> str:
    m = _YEAR.search(str(s or ""))
    return m.group(1) if m else ""


def has_cjk(s: Any) -> bool:
    return bool(_CJK.search(str(s or "")))


def norm_doi(s: Any) -> str:
    if not s:
        return ""
    v = str(s).strip().lower()
    v = re.sub(r"^https?://(dx\.)?doi\.org/", "", v)
    v = re.sub(r"^doi:\s*", "", v)
    return v.strip()


# --------------------------------------------------------------------------- #
# Entry model & field aliasing
# --------------------------------------------------------------------------- #

_FIELD_ALIASES: dict[str, list[str]] = {
    "title": ["title", "booktitle", "article-title", "titel", "name"],
    "author": ["author", "authors", "creator", "creators", "editor", "editors"],
    "year": ["year", "date", "pubdate", "publication-year", "py", "issued"],
    "doi": ["doi"],
    "journal": ["journal", "journal-title", "publication", "source", "periodical",
                "container-title", "booktitle", "secondary-title"],
    "volume": ["volume", "vol"],
    "issue": ["issue", "number", "no"],
    "pages": ["pages", "page", "spage", "start-page", "epage"],
    "publisher": ["publisher", "publisher-name", "institution"],
    "type": ["type", "reference-type", "ref-type", "entry-type", "doctype", "refdoc"],
    "url": ["url", "link", "web", "uri"],
    "abstract": ["abstract", "summary", "notes"],
    "isbn": ["isbn"],
    "issn": ["issn"],
}

_ENTRY_TYPE_HINT = {
    "journal": "journal", "article": "journal", "j": "journal",
    "conference": "conference", "inproceedings": "conference", "c": "conference",
    "book": "book", "m": "book", "monograph": "book",
    "thesis": "thesis", "phdthesis": "thesis", "mastersthesis": "thesis", "d": "thesis",
    "report": "report", "techreport": "report", "r": "report",
    "webpage": "web", "electronic": "web", "online": "web",
    "patent": "patent",
}


def _pick(raw: dict[str, Any], key: str) -> Any:
    lk = str(key).lower().strip()
    if lk in raw:
        return raw[lk]
    for alias in _FIELD_ALIASES.get(key, []):
        if alias in raw:
            return raw[alias]
    return None


def normalize_json_entry(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    data: dict[str, Any] = {str(k).lower().strip(): v for k, v in raw.items()}
    entry: dict[str, Any] = {}
    for canonical in _FIELD_ALIASES:
        v = _pick(data, canonical)
        if v is not None and str(v).strip() != "":
            entry[canonical] = v
    for k in ("doi", "url", "isbn", "issn"):
        if k in entry and isinstance(entry[k], str):
            entry[k] = entry[k].strip()
    t = _pick(data, "type")
    if t:
        hint = _ENTRY_TYPE_HINT.get(str(t).lower().strip(), None)
        if hint:
            entry.setdefault("doc_type", hint)
        else:
            entry.setdefault("doc_type", str(t).lower().strip())
    for k in ("key", "id", "cite-key", "citation-key", "refid", "record-number"):
        if k in data:
            entry.setdefault("source_key", str(data[k]).strip())
            break
    return entry


def _authors_from_value(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        parts = [p.strip() for p in re.split(r"\s*(?:;|；|\||\s+and\s+)\s*", v, flags=re.IGNORECASE) if p.strip()]
        return parts
    if isinstance(v, (list, tuple)):
        out: list[str] = []
        for a in v:
            if isinstance(a, str):
                out.append(a.strip())
            elif isinstance(a, dict):
                fam = a.get("family") or a.get("last") or a.get("lastname") or a.get("surname") or ""
                given = a.get("given") or a.get("first") or a.get("firstname") or ""
                if fam:
                    out.append((fam + (", " + given if given else "")).strip())
                elif given:
                    out.append(given)
            else:
                out.append(str(a))
        return [x for x in out if x]
    return []


def entry_authors(entry: dict[str, Any]) -> list[str]:
    return _authors_from_value(entry.get("author"))


def first_author_family(entry: dict[str, Any]) -> str:
    al = entry_authors(entry)
    if not al:
        return ""
    first = al[0]
    if "," in first:
        fam = first.split(",", 1)[0]
    else:
        parts = first.split()
        fam = parts[-1] if len(parts) > 1 else first
    return norm_key(fam)


def entry_title_norm(entry: dict[str, Any]) -> str:
    return norm_key(entry.get("title"))


def entry_year(entry: dict[str, Any]) -> str:
    y = entry.get("year")
    if y is None:
        y = entry.get("date")
    return first_year(y)


def entry_doi(entry: dict[str, Any]) -> str:
    return norm_doi(entry.get("doi"))


def entry_key(entry: dict[str, Any]) -> tuple[str, str, str, str]:
    return (entry_doi(entry), entry_title_norm(entry),
            first_author_family(entry), entry_year(entry))


def entry_fullness(entry: dict[str, Any]) -> int:
    score = 0
    for f in ("title", "author", "year", "doi", "journal", "volume",
              "issue", "pages", "publisher", "url", "abstract"):
        v = entry.get(f)
        if v:
            score += 1 if not isinstance(v, str) or v.strip() else 0
    return score


# --------------------------------------------------------------------------- #
# Parsers
# --------------------------------------------------------------------------- #

def parse_bibtex(text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    text = re.sub(r"(?m)^\s*%.*$", "", text)
    i = 0
    n = len(text)
    while i < n:
        at = text.find("@", i)
        if at < 0:
            break
        m = re.match(r"@([A-Za-z]+)\s*[\(\{]", text[at:])
        if not m:
            i = at + 1
            continue
        etype = m.group(1).lower()
        if etype in ("comment", "preamble", "string"):
            open_ch = text[at + m.end() - 1]
            close_ch = ")" if open_ch == "(" else "}"
            depth = 1
            j = at + m.end()
            while j < n and depth > 0:
                if text[j] == open_ch:
                    depth += 1
                elif text[j] == close_ch:
                    depth -= 1
                j += 1
            i = j
            continue
        open_pos = at + m.end() - 1
        open_ch = text[open_pos]
        close_ch = ")" if open_ch == "(" else "}"
        body_start = at + m.end()
        comma = body_start
        depth = 0
        while comma < n:
            ch = text[comma]
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
            elif ch == "," and depth == 0:
                break
            comma += 1
        cite_key = text[body_start:comma].strip()
        # Scan from the entry's own opening delimiter so nested braces inside
        # field values do not terminate the block early.
        depth = 0
        j = open_pos
        while j < n:
            ch = text[j]
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if j >= n:
            break
        fields_text = text[comma + 1:j]
        i = j + 1
        fields = _parse_bibtex_fields(fields_text)
        entry = normalize_json_entry({**fields, "type": etype})
        if cite_key:
            entry.setdefault("source_key", cite_key)
        if entry.get("title") or entry.get("doi") or entry.get("author"):
            entries.append(entry)
    return entries


def _read_balanced(text: str, start: int, open_ch: str, close_ch: str):
    depth = 0
    j = start
    n = len(text)
    while j < n:
        ch = text[j]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:j], j + 1
        j += 1
    return text[start:j], j


def _parse_bibtex_fields(s: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    i = 0
    n = len(s)
    while i < n:
        eq = s.find("=", i)
        if eq < 0:
            break
        name = s[i:eq].strip()
        name = re.sub(r"^[\s,]+", "", name)
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_\-]*", name):
            i = eq + 1
            continue
        j = eq + 1
        while j < n and s[j] in " \t\r\n":
            j += 1
        value = ""
        if j < n and s[j] in "{(\"":
            open_ch = s[j]
            close_ch = "}" if open_ch == "{" else (")" if open_ch == "(" else "\"")
            if open_ch == "\"":
                k = j + 1
                buf = []
                while k < n and s[k] != "\"":
                    buf.append(s[k])
                    k += 1
                value = "".join(buf)
                i = k + 1
            else:
                val_txt, i = _read_balanced(s, j, open_ch, close_ch)
                # _read_balanced returns text[j:close] (includes the opening
                # delimiter, excludes the closing one), so strip only the lead.
                value = val_txt[1:] if val_txt and val_txt[0] == open_ch else val_txt
        else:
            k = j
            while k < n and s[k] != ",":
                k += 1
            value = s[j:k].strip()
            i = k
        while i < n and s[i] in " \t\r\n,":
            i += 1
        value = re.sub(r"\s+", " ", value).strip()
        value = value.strip("{}")
        fields[name.lower()] = value
    return fields


def parse_ris(text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    lines = text.splitlines()
    cur: dict[str, list[str]] = {}
    cur_type = ""

    def flush():
        nonlocal cur, cur_type
        if cur:
            entry = normalize_json_entry(_ris_to_dict(cur, cur_type))
            if entry.get("title") or entry.get("doi") or entry.get("author"):
                entries.append(entry)
        cur = {}
        cur_type = ""

    for raw in lines:
        line = raw.rstrip("\n\r")
        if not line.strip():
            continue
        if len(line) < 2:
            continue
        tag = line[:2].strip()
        value = line[2:].lstrip(" -")
        if tag == "TY":
            flush()
            cur_type = value.strip()
        elif tag == "ER":
            flush()
        elif tag:
            cur.setdefault(tag.lower(), []).append(value)
    flush()
    return entries


def _ris_to_dict(cur: dict[str, list[str]], cur_type: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    def one(tag: str) -> str:
        v = cur.get(tag, [])
        return v[0] if v else ""
    def many(*tags: str) -> list[str]:
        vals: list[str] = []
        for t in tags:
            vals.extend(cur.get(t, []))
        return vals
    out["type"] = cur_type
    out["title"] = one("TI") or one("T1")
    authors = many("AU", "A1", "A2", "A3")
    if authors:
        out["author"] = authors
    for tag, field in (("PY", "year"), ("Y1", "year"), ("DA", "date"),
                       ("DO", "doi"), ("UR", "url"), ("VL", "volume"),
                       ("IS", "issue")):
        if tag in cur:
            out[field] = cur[tag][0]
    if "SP" in cur and "EP" in cur:
        out["pages"] = f"{cur['SP'][0]}-{cur['EP'][0]}"
    for tag, field in (("JO", "journal"), ("JF", "journal"), ("T2", "journal"),
                       ("PB", "publisher"), ("SN", "issn"), ("N1", "notes"),
                       ("AB", "abstract")):
        if tag in cur:
            out[field] = cur[tag][0]
    return out


def parse_reference_json(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        for k in ("references", "items", "entries", "records", "data", "results"):
            if k in data and isinstance(data[k], list):
                data = data[k]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        return []
    entries = []
    for item in data:
        if isinstance(item, dict):
            e = normalize_json_entry(item)
            if e.get("title") or e.get("doi") or e.get("author"):
                entries.append(e)
    return entries


def _parse_table(text: str, ledger: "Ledger") -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except Exception:
        ledger.degrade("table header not detected; skipped as library")
        return entries
    try:
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    except Exception as exc:  # pragma: no cover
        ledger.degrade(f"table parse failed ({exc})")
        return entries
    if not reader.fieldnames:
        return entries
    for row in reader:
        item = {str(k).lower(): (v or "").strip() for k, v in row.items() if k is not None}
        e = normalize_json_entry(item)
        if e.get("title") or e.get("doi") or e.get("author"):
            entries.append(e)
    return entries


# --------------------------------------------------------------------------- #
# Deduplication (explainable criteria)
# --------------------------------------------------------------------------- #

def deduplicate(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_key: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    order: list[tuple[str, str, str, str]] = []
    for e in entries:
        k = entry_key(e)
        if k not in by_key:
            by_key[k] = []
            order.append(k)
        by_key[k].append(e)

    merged: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    for k in order:
        group = by_key[k]
        if len(group) == 1:
            merged.append(group[0])
            continue
        canonical = max(group, key=entry_fullness)
        for src in group:
            if src is canonical:
                continue
            for f in ("title", "author", "year", "doi", "journal", "volume",
                      "issue", "pages", "publisher", "url", "abstract"):
                if not canonical.get(f) and src.get(f):
                    canonical[f] = src.get(f)
        criterion = "doi" if k[0] else "title+author+year"
        evidence.append({
            "criterion": criterion,
            "kept_source_key": canonical.get("source_key"),
            "merged_source_keys": [g.get("source_key") for g in group],
            "doi": canonical.get("doi"),
            "title": canonical.get("title"),
            "count": len(group),
        })
        merged.append(canonical)

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            a, b = entries[i], entries[j]
            if first_author_family(a) and first_author_family(a) == first_author_family(b) \
               and entry_year(a) and entry_year(a) == entry_year(b) \
               and entry_title_norm(a) != entry_title_norm(b) \
               and (has_cjk(a.get("title")) != has_cjk(b.get("title"))):
                key = tuple(sorted((entry_key(a)[1], entry_key(b)[1])))
                if key in seen:
                    continue
                seen.add(key)
                candidates.append({
                    "kind": "cross_language_or_variant",
                    "a": {"source_key": a.get("source_key"), "title": a.get("title"),
                          "doi": a.get("doi"), "year": entry_year(a)},
                    "b": {"source_key": b.get("source_key"), "title": b.get("title"),
                          "doi": b.get("doi"), "year": entry_year(b)},
                    "basis": "same first-author family + year, one CJK and one Latin title",
                })
    return merged, (evidence + candidates)


# --------------------------------------------------------------------------- #
# Formatting (GB/T 7714-2015, APA)
# --------------------------------------------------------------------------- #

def _author_gbt(entry: dict[str, Any]) -> str:
    authors = entry_authors(entry)
    if not authors:
        return ""
    cjk = has_cjk(" ".join(authors))
    if len(authors) <= 3:
        return ", ".join(authors)
    if cjk:
        return ", ".join(authors[:3]) + ", 等"
    return ", ".join(authors[:3]) + ", et al"


def _author_apa(entry: dict[str, Any]) -> str:
    authors = entry_authors(entry)
    if not authors:
        return ""
    parts: list[str] = []
    for a in authors[:7]:
        if "," in a:
            fam, _, given = a.partition(",")
        else:
            toks = a.split()
            fam = toks[-1] if len(toks) > 1 else a
            given = " ".join(toks[:-1]) if len(toks) > 1 else ""
        initials = " ".join(p[0].upper() + "." for p in given.split() if p)
        parts.append((fam.strip() + (", " + initials if initials else "")).strip())
    if not parts:
        return ""
    if len(authors) > 7:
        return ", ".join(parts[:6]) + ", ... " + parts[-1]
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return parts[0] + " & " + parts[1]
    return ", ".join(parts[:-1]) + ", & " + parts[-1]


def _doc_type(entry: dict[str, Any]) -> str:
    return entry.get("doc_type") or "journal"


def format_gbt7714(entry: dict[str, Any], number: int | None = None) -> str:
    authors = _author_gbt(entry)
    title = norm_space(entry.get("title")) or "[未标注题名]"
    year = entry_year(entry) or "[未标注年份]"
    dt = _doc_type(entry)
    if dt in ("journal", "article"):
        journal = norm_space(entry.get("journal")) or "[未标注刊名]"
        vol = norm_space(entry.get("volume"))
        issue = norm_space(entry.get("issue"))
        pages = norm_space(entry.get("pages"))
        seg = journal + ", " + year
        if vol:
            seg += ", " + vol
            if issue:
                seg += f"({issue})"
        if pages:
            seg += ": " + pages
        s = f"{authors + '. ' if authors else ''}{title}[J]. {seg}."
    elif dt == "conference":
        s = f"{authors + '. ' if authors else ''}{title}[C]. {norm_space(entry.get('publisher')) or '[未标注出版地]'}, {year}."
    elif dt == "book":
        pub = norm_space(entry.get("publisher"))
        s = f"{authors + '. ' if authors else ''}{title}[M]. {pub + ', ' if pub else ''}{year}."
    elif dt == "thesis":
        s = f"{authors + '. ' if authors else ''}{title}[D]. {norm_space(entry.get('publisher')) or '[未标注单位]'}, {year}."
    elif dt == "report":
        s = f"{authors + '. ' if authors else ''}{title}[R]. {norm_space(entry.get('publisher')) or '[未标注机构]'}, {year}."
    elif dt in ("web", "online", "electronic"):
        url = norm_space(entry.get("url"))
        s = f"{authors + '. ' if authors else ''}{title}[EB/OL]. ({year})" + (f"[{url}]" if url else " [未标注 URL]") + "."
    else:
        s = f"{authors + '. ' if authors else ''}{title}[Z]. {year}."
    doi = norm_space(entry.get("doi"))
    if doi:
        s += f" DOI:{doi}."
    return s


def format_apa(entry: dict[str, Any]) -> str:
    authors = _author_apa(entry)
    year = entry_year(entry)
    title = norm_space(entry.get("title")) or "[Untitled]"
    dt = _doc_type(entry)
    if dt in ("journal", "article"):
        journal = norm_space(entry.get("journal")) or "[Journal missing]"
        vol = norm_space(entry.get("volume"))
        issue = norm_space(entry.get("issue"))
        pages = norm_space(entry.get("pages"))
        s = f"{authors + ' ' if authors else ''}({year or 'n.d.'}). {title}. {journal}"
        if vol:
            s += f", {vol}"
            if issue:
                s += f"({issue})"
        if pages:
            s += f", {pages}"
        s += "."
    elif dt == "book":
        pub = norm_space(entry.get("publisher"))
        s = f"{authors + ' ' if authors else ''}({year or 'n.d.'}). {title}. {pub + '. ' if pub else ''}"
    else:
        s = f"{authors + ' ' if authors else ''}({year or 'n.d.'}). {title}."
    doi = norm_space(entry.get("doi"))
    if doi:
        s += f" https://doi.org/{doi}"
    return s


# --------------------------------------------------------------------------- #
# Citation marker scanning & renumbering
# --------------------------------------------------------------------------- #

_PANDOC = re.compile(r"\[@([^\]]+)\]")
_LATEX = re.compile(r"\\(?:cite|citet|citep|citealp|citeauthor)\*?\{([^{}]+)\}")
_NUMERIC = re.compile(r"(?<![\w@])\[(\d+(?:\s*[,\uFF0C-]\s*\d+)*)\]")
_KEY_SPLIT = re.compile(r"[\s;,，]+")


def _clean_keys(raw: str) -> list[str]:
    return [k.strip().lstrip("@") for k in _KEY_SPLIT.split(raw) if k.strip()]


def scan_citation_markers(text: str) -> dict[str, Any]:
    keyed: list[dict[str, Any]] = []
    for m in _PANDOC.finditer(text):
        keys = _clean_keys(m.group(1))
        keyed.append({"kind": "pandoc", "keys": keys, "raw": m.group(0),
                      "span": [m.start(), m.end()]})
    for m in _LATEX.finditer(text):
        keys = _clean_keys(m.group(1))
        keyed.append({"kind": "latex", "keys": keys, "raw": m.group(0),
                      "span": [m.start(), m.end()]})
    keyed.sort(key=lambda d: d["span"][0])
    numeric: list[dict[str, Any]] = []
    for m in _NUMERIC.finditer(text):
        nums = [int(x) for x in re.split(r"[,\uFF0C-]", m.group(1)) if x.strip()]
        numeric.append({"kind": "numeric", "nums": nums, "raw": m.group(0),
                        "span": [m.start(), m.end()]})
    return {"keyed": keyed, "numeric": numeric}


def assign_numbers(keys_in_order: list[list[str]]) -> tuple[dict[str, int], dict[int, str]]:
    key_num: dict[str, int] = {}
    num_key: dict[int, str] = {}
    nxt = 1
    for keys in keys_in_order:
        for k in keys:
            if k not in key_num:
                key_num[k] = nxt
                num_key[nxt] = k
                nxt += 1
    return key_num, num_key


def render_numeric_marker(keys: list[str], key_num: dict[str, int]) -> str:
    nums = sorted({key_num[k] for k in keys if k in key_num})
    if not nums:
        return ""
    if len(nums) == 1:
        return f"[{nums[0]}]"
    return "[" + ",".join(str(n) for n in nums) + "]"


def renumber_text(text: str, keyed: list[dict[str, Any]], key_num: dict[str, int]) -> str:
    if not keyed:
        return text
    pieces: list[str] = []
    last = 0
    for m in keyed:
        start, end = m["span"]
        pieces.append(text[last:start])
        pieces.append(render_numeric_marker(m["keys"], key_num))
        last = end
    pieces.append(text[last:])
    return "".join(pieces)


def renumber_all_markers(text: str, ordered: list[dict[str, Any]],
                         key_num: dict[str, int]) -> str:
    """Rewrite keyed AND numeric markers in document order to new numbers.

    Unresolved markers (no assigned number) are left untouched, so the output
    never invents a number for a citation the library cannot account for.
    """
    if not ordered:
        return text
    pieces: list[str] = []
    last = 0
    for m in ordered:
        start, end = m["span"]
        pieces.append(text[last:start])
        rendered = render_numeric_marker(m["keys"], key_num)
        pieces.append(rendered if rendered else m["raw"])
        last = end
    pieces.append(text[last:])
    return "".join(pieces)


# --------------------------------------------------------------------------- #
# Consistency checks
# --------------------------------------------------------------------------- #

def consistency_check(cited_keys: list[str], entries: list[dict[str, Any]],
                      num_key: dict[int, str]) -> dict[str, Any]:
    entry_keys: set[str] = set()
    key_entries: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        k = e.get("source_key") or entry_key(e)[1]
        entry_keys.add(k)
        key_entries.setdefault(k, []).append(e)

    cited = set(cited_keys)
    unresolved = sorted(k for k in cited if k not in entry_keys)
    uncited = sorted(k for k in entry_keys if k not in cited)
    duplicate_keys = {k: len(v) for k, v in key_entries.items() if len(v) > 1}
    if num_key:
        numbers = sorted(num_key)
        gaps = [i for i in range(1, max(numbers) + 1) if i not in num_key]
    else:
        gaps = []
    return {
        "cited_key_count": len(cited),
        "unresolved_citations": unresolved,
        "uncited_entries": uncited,
        "duplicate_source_keys": duplicate_keys,
        "number_gaps": gaps,
    }


# --------------------------------------------------------------------------- #
# Ledger & IO helpers
# --------------------------------------------------------------------------- #

class Ledger:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.degradations: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def degrade(self, msg: str) -> None:
        self.degradations.append(msg)


MAX_FILE_BYTES = 2 * 1024 * 1024


def read_text_safe(path: Path, ledger: Ledger) -> str:
    try:
        size = path.stat().st_size
    except OSError as exc:
        ledger.error(f"{path.name}: cannot stat ({exc})")
        return ""
    if size > MAX_FILE_BYTES:
        ledger.degrade(f"{path.name}: {size} bytes exceeds {MAX_FILE_BYTES} limit; skipped")
        return ""
    raw = None
    try:
        raw = path.read_bytes()
    except OSError as exc:
        ledger.error(f"{path.name}: read failed ({exc})")
        return ""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1", errors="replace")


def load_json_safe(path: Path, ledger: Ledger) -> Any:
    text = read_text_safe(path, ledger)
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception as exc:
        ledger.error(f"{path.name}: invalid JSON ({exc})")
        return None


def load_json_text(text: str, name: str, ledger: Ledger) -> Any:
    try:
        return json.loads(text)
    except Exception as exc:
        ledger.error(f"{name}: invalid JSON ({exc})")
        return None


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def write_json_atomic(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(_dumps(obj), encoding="utf-8")
    os.replace(tmp, path)


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2)


def content_hash(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths, key=lambda x: x.name):
        h.update(p.name.encode("utf-8"))
        h.update(b"\x00")
        h.update(p.read_bytes())
        h.update(b"\x00")
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #

MANUSCRIPT_EXT = {".md", ".markdown", ".txt", ".tex", ".docx", ".pdf"}
LIBRARY_EXT = {".bib", ".ris", ".enw", ".json", ".csv", ".tsv", ".xlsx"}


def discover(input_dir: Path) -> list[Path]:
    if not input_dir.exists():
        return []
    skip_names = {"case.json", "task.md"}
    out = []
    for p in input_dir.rglob("*"):
        if not p.is_file():
            continue
        if p.parent == input_dir and p.name in skip_names:
            continue
        out.append(p)
    return sorted(out)


def classify(paths: list[Path]) -> tuple[list[Path], list[Path], list[Path]]:
    manuscript, library, other = [], [], []
    for p in paths:
        ext = p.suffix.lower()
        if ext in LIBRARY_EXT:
            library.append(p)
        if ext in MANUSCRIPT_EXT:
            manuscript.append(p)
        if ext not in LIBRARY_EXT and ext not in MANUSCRIPT_EXT:
            other.append(p)
    return manuscript, library, other


def find_case_and_task(input_dir: Path, ledger: Ledger) -> tuple[dict[str, Any], str, Path | None]:
    case_path = input_dir / "case.json"
    case: dict[str, Any] = {}
    if case_path.exists():
        case = load_json_safe(case_path, ledger) or {}
    else:
        ledger.degrade("case.json not found under input root")
    task_text = ""
    task_path = input_dir / "task.md"
    if task_path.exists():
        task_text = read_text_safe(task_path, ledger)
    else:
        ledger.degrade("task.md not found under input root")
    return case, task_text, (task_path if task_path.exists() else None)


def parse_library(files: list[Path], ledger: Ledger) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for p in files:
        text = read_text_safe(p, ledger)
        if not text:
            continue
        ext = p.suffix.lower()
        try:
            if ext == ".bib":
                parsed = parse_bibtex(text)
            elif ext == ".ris":
                parsed = parse_ris(text)
            elif ext == ".enw":
                parsed = parse_ris(text)
            elif ext == ".json":
                data = load_json_text(text, p.name, ledger)
                parsed = parse_reference_json(data) if data is not None else []
            elif ext in (".csv", ".tsv"):
                parsed = _parse_table(text, ledger)
            else:
                ledger.degrade(f"{p.name}: library format '{ext}' not parsed (no text extraction)")
                parsed = []
        except Exception as exc:  # noqa: BLE001
            ledger.error(f"{p.name}: parse failed ({exc})")
            parsed = []
        entries.extend(parsed)
    return entries


# --------------------------------------------------------------------------- #
# Skeleton & result assembly
# --------------------------------------------------------------------------- #

RESULT_FIELDS = ["status", "sources", "uncertainty", "change_log",
                 "errors", "degradations", "pending_human", "artifacts", "runtime"]

# Map output_contract key names (as commonly written by case authors) to the
# artifact slots above, so determinable contract fields are filled with real
# paths instead of being left null.
CONTRACT_FILL = {
    "manuscript": "manuscript",
    "manuscript_path": "manuscript",
    "manuscript.md": "manuscript",
    "references": "references",
    "references_path": "references",
    "reference_list": "references",
    "evidence_map": "evidence_map",
    "evidence_map_path": "evidence_map",
    "consistency_report": "consistency_report",
    "manifest": "manifest",
    "manifest_path": "manifest",
}


def build_skeleton(case: dict[str, Any]) -> dict[str, Any]:
    case_id = ""
    if isinstance(case, dict):
        case_id = str(case.get("case_id") or case.get("id")
                      or case.get("challenge_id") or case.get("armiId") or "")
    contract: dict[str, Any] = {}
    if isinstance(case, dict):
        raw = case.get("output_contract", case.get("outputContract", {}))
        if isinstance(raw, dict):
            contract = {str(k): None for k in raw.keys()}
        elif isinstance(raw, list):
            contract = {str(k): None for k in raw if isinstance(k, str)}
    skeleton: dict[str, Any] = {"case_id": case_id, **contract}
    skeleton["status"] = "partial"
    skeleton["sources"] = []
    skeleton["uncertainty"] = []
    skeleton["change_log"] = []
    skeleton["errors"] = []
    skeleton["degradations"] = []
    skeleton["pending_human"] = []
    skeleton["artifacts"] = {}
    skeleton["runtime"] = {"seconds": 0.0,
                           "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    return skeleton


def _placeholder(kind: str) -> str:
    return (f"<!-- 占位：{kind} 尚未由语义阶段回填。"
            f"请按 SKILL.md 流程增量更新；本文件不是真实结果。 -->\n")


def _empty_evidence() -> dict[str, Any]:
    return {
        "_schema": "evidence-map-v1",
        "entries": [],
        "_note": ("每条引用项须包含 locator、quote、matched_source_key、basis。"
                  "语义阶段由 Agent 回填；脚本不臆造匹配结论。"),
    }


def _manifest(case: dict[str, Any], task_text: str) -> str:
    parts = []
    title = (isinstance(case, dict) and (case.get("title") or case.get("title_zh")))
    if title:
        parts.append(f"# 稿件处理清单\n\n- 案例：{title}")
    parts.append("\n## 输入材料\n\n")
    parts.append("`case.json` 与 `task.md` 的原文由证据映射定位，不在此重复粘贴。\n")
    if task_text:
        parts.append("\n## 任务说明（原文保留）\n\n```\n" + task_text[:4000] + "\n```\n")
    return "".join(parts)


def _sources(manuscript: list[Path], library: list[Path], other: list[Path]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in manuscript:
        out.append({"path": str(p), "type": "manuscript", "role": "稿件"})
    for p in library:
        out.append({"path": str(p), "type": "library", "role": "文献库"})
    for p in other:
        out.append({"path": str(p), "type": "other", "role": "其他材料"})
    return out


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #

def run_pipeline(input_dir: str | Path, output_dir: str | Path,
                 work_dir: str | Path, style: str = "gbt7714",
                 citation_map: dict[str, Any] | None = None) -> dict[str, Any]:
    inp = Path(input_dir)
    out = Path(output_dir)
    work = Path(work_dir)
    started = time.time()
    ledger = Ledger()

    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    case, task_text, _ = find_case_and_task(inp, ledger)
    result = build_skeleton(case)
    # Skeleton-first rule: persist a legal partial result before any parsing,
    # so a later interruption never leaves an empty delivery.
    write_json_atomic(out / "result.json", result)

    paths = discover(inp)
    manuscript_files, library_files, other_files = classify(paths)
    result["sources"] = _sources(manuscript_files, library_files, other_files)

    raw_entries = parse_library(library_files, ledger)
    entries, merge_evidence = deduplicate(raw_entries)
    dedup_removed = len(raw_entries) - len(entries)

    manuscript_text = ""
    for p in manuscript_files:
        ext = p.suffix.lower()
        if ext in (".md", ".markdown", ".txt", ".tex"):
            manuscript_text += read_text_safe(p, ledger) + "\n\n"
        else:
            ledger.degrade(f"{p.name}: {ext} needs runtime pdf-text-tool/office tool; left for Agent")

    markers = scan_citation_markers(manuscript_text)
    keyed = markers["keyed"]
    numeric = markers["numeric"]

    key_num: dict[str, int] = {}
    num_key: dict[int, str] = {}
    numeric_pending: list[dict[str, Any]] = []

    # Resolve old numeric [n] -> entry key. Prefer an Agent-supplied
    # citation_map.json; otherwise fall back to library order (n -> n-th
    # original entry) and record that assumption explicitly in uncertainty.
    old_to_key: dict[int, str] = {}
    if citation_map and isinstance(citation_map, dict):
        occ = citation_map.get("occurrences") or []
        by_index: dict[int, list[str]] = {}
        for item in occ:
            if isinstance(item, dict) and isinstance(item.get("index"), int):
                keys = [str(k) for k in item.get("keys", [])]
                by_index[item["index"]] = keys
        for idx, nm in enumerate(numeric):
            keys = by_index.get(idx, [])
            if keys:
                for n, k in zip(nm["nums"], keys):
                    old_to_key[n] = k
            else:
                numeric_pending.append({"index": idx, "raw": nm["raw"], "reason": "无映射"})
    else:
        for m in numeric:
            for n in m["nums"]:
                idx = n - 1
                if 0 <= idx < len(raw_entries):
                    e = raw_entries[idx]
                    old_to_key[n] = e.get("source_key") or entry_key(e)[1]
                else:
                    numeric_pending.append({"raw": m["raw"], "num": n,
                                            "reason": "数值超出文献库范围"})

    # Unified marker sequence in document order (keyed + numeric).
    ordered: list[dict[str, Any]] = []
    for m in keyed:
        ordered.append({"span": m["span"], "keys": m["keys"], "raw": m["raw"],
                        "kind": m["kind"]})
    for m in numeric:
        keys = [old_to_key[n] for n in m["nums"] if n in old_to_key]
        ordered.append({"span": m["span"], "keys": keys, "raw": m["raw"],
                        "kind": "numeric"})
    ordered.sort(key=lambda d: d["span"][0])

    cited_keys_in_order: list[list[str]] = [m["keys"] for m in ordered]
    if cited_keys_in_order:
        key_num, num_key = assign_numbers(cited_keys_in_order)

    processed_text = renumber_all_markers(manuscript_text, ordered, key_num)

    cited_keys = [k for keys in cited_keys_in_order for k in keys]
    consistency = consistency_check(cited_keys, entries, num_key)

    entry_by_key: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        k = e.get("source_key") or entry_key(e)[1]
        entry_by_key.setdefault(k, []).append(e)

    cited_entries: list[tuple[int, dict[str, Any]]] = []
    for n in sorted(num_key):
        k = num_key[n]
        for e in entry_by_key.get(k, []):
            cited_entries.append((n, e))

    uncited_keys = consistency["uncited_entries"]
    uncited_entries: list[dict[str, Any]] = []
    for k in uncited_keys:
        uncited_entries.extend(entry_by_key.get(k, []))

    formatter = format_apa if style == "apa" else format_gbt7714
    ref_lines: list[str] = [f"# 参考文献表（{style}）\n"]
    if cited_entries:
        ref_lines.append("## 正文引用（按编号）\n")
        for n, e in cited_entries:
            ref_lines.append(f"{n}. {formatter(e, n)}")
    if uncited_entries:
        ref_lines.append("\n## 未在正文中引用的条目\n")
        for e in uncited_entries:
            ref_lines.append(f"- {formatter(e, None)}")

    evidence = _empty_evidence()
    ev_entries: list[dict[str, Any]] = []
    for m in ordered:
        ev_entries.append({
            "citation": m["raw"],
            "locator": f"manuscript char {m['span'][0]}-{m['span'][1]}",
            "quote": m["raw"],
            "keys": m["keys"],
            "assigned_numbers": sorted({key_num[k] for k in m["keys"] if k in key_num}),
            "matched_source_key": None,
            "basis": "待语义匹配",
            "status": "pending_semantic",
        })
    for nm in numeric_pending:
        ev_entries.append({
            "citation": nm.get("raw", ""),
            "locator": "numeric (unmapped)",
            "quote": nm.get("raw", ""),
            "keys": [],
            "assigned_numbers": [],
            "matched_source_key": None,
            "basis": "待语义匹配",
            "status": "unresolved",
            "reason": nm.get("reason", ""),
        })
    evidence["entries"] = ev_entries

    write_text_atomic(out / "manuscript.md", processed_text if processed_text.strip() else _placeholder("manuscript"))
    write_text_atomic(out / "references.md", "\n".join(ref_lines) if len(ref_lines) > 1 else _placeholder("reference list"))
    write_json_atomic(out / "evidence_map.json", evidence)
    write_json_atomic(out / "consistency_report.json", {
        **consistency,
        "dedup_removed": dedup_removed,
        "merge_evidence": merge_evidence,
    })
    write_text_atomic(out / "manifest.md", _manifest(case, task_text))
    write_json_atomic(work / "library_snapshot.json", {
        "entries": entries,
        "raw_entry_count": len(raw_entries),
        "canonical_entry_count": len(entries),
    })

    artifacts = {
        "manuscript": str(out / "manuscript.md"),
        "references": str(out / "references.md"),
        "evidence_map": str(out / "evidence_map.json"),
        "consistency_report": str(out / "consistency_report.json"),
        "manifest": str(out / "manifest.md"),
        "library_snapshot": str(work / "library_snapshot.json"),
    }
    result["artifacts"] = artifacts
    # Echo output_contract keys with actual artifact paths where determinable;
    # unknown keys stay null. Never rename or delete unknown contract keys.
    for key, art in CONTRACT_FILL.items():
        if key in result and result.get(key) is None:
            result[key] = artifacts.get(art)
    result["errors"] = ledger.errors
    result["degradations"] = ledger.degradations
    uncertainty = list(merge_evidence)
    if not citation_map and numeric and old_to_key:
        uncertainty.append({
            "kind": "assumption",
            "detail": ("数值引用按文献库原始顺序映射（[n] -> 第 n 条原始条目）；"
                       "如与稿件原参考文献顺序不符，请 Agent 用 citation_map.json 覆盖"),
            "mapped": sorted(old_to_key.items()),
        })
    result["uncertainty"] = uncertainty
    result["pending_human"] = []
    if numeric_pending:
        result["pending_human"].append({"kind": "numeric_citation_unmapped", "items": numeric_pending})
    for cand in merge_evidence:
        if isinstance(cand, dict) and cand.get("kind") == "cross_language_or_variant":
            result["pending_human"].append({"kind": "dedup_candidate", "detail": cand})
    result["change_log"] = [{
        "step": "deterministic-backbone",
        "action": "parse/dedupe/renumber/format/consistency",
        "detail": (f"library raw={len(raw_entries)} canonical={len(entries)} "
                   f"dedup_removed={dedup_removed}; "
                   f"keyed_citations={len(keyed)} numeric_citations={len(numeric)}"),
    }]

    hash_paths = [out / n for n in ("manuscript.md", "references.md",
                                    "evidence_map.json", "consistency_report.json")]
    result["content_hash"] = content_hash(hash_paths)

    if not manuscript_files and not library_files:
        result["status"] = "insufficient_evidence"
        result["pending_human"].append({"kind": "missing_material", "detail": "未发现稿件或文献库文件"})
    else:
        result["status"] = "partial"

    result["runtime"]["seconds"] = round(time.time() - started, 3)
    write_json_atomic(out / "result.json", result)
    return result
