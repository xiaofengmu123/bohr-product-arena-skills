# -*- coding: utf-8 -*-
"""Deterministic engine for the citation-integrity skill.

This module is pure standard library and deliberately free of any network or
model calls.  It supplies the reproducible backbone of the pipeline:

    discovery -> parsing -> deduplication -> renumbering -> formatting
               -> consistency check -> artifact writing

Semantic judgments (which sentence needs which reference, and whether a claim
really matches a source) are *not* computed here.  They are recorded by the
Agent into ``evidence_map.json`` and ``change_log``.  This module only
produces the deterministic skeleton and honest machine-checkable facts.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import time
from pathlib import Path
from typing import Any, Optional

DEFAULT_MAX_BYTES = 2 * 1024 * 1024  # 2 MB per text file

# Encoding fallback order.  utf-8-sig handles a UTF-8 BOM; gb18030 covers
# GBK/GB2312 CNKI exports; latin-1 never fails and guarantees a string back.
_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")

MANUSCRIPT_EXTS = {".md", ".markdown", ".txt", ".tex", ".docx", ".pdf"}
LIBRARY_EXTS = {".bib", ".ris", ".enw", ".json", ".csv", ".tsv", ".xlsx"}


# --------------------------------------------------------------------------- #
# Ledger: honest, structured accumulation of problems without crashing.
# --------------------------------------------------------------------------- #
class Ledger:
    """Collect errors / degradations / pending items through the pipeline."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.degradations: list[str] = []
        self.pending: list[str] = []
        self.uncertainty: list[str] = []
        self.sources: list[dict] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def degrade(self, msg: str) -> None:
        self.degradations.append(msg)

    def pend(self, msg: str) -> None:
        self.pending.append(msg)

    def unsure(self, msg: str) -> None:
        self.uncertainty.append(msg)

    def to_result_fields(self) -> dict:
        return {
            "errors": list(self.errors),
            "degradations": list(self.degradations),
            "pending_human": list(self.pending),
            "uncertainty": list(self.uncertainty),
            "sources": list(self.sources),
        }


# --------------------------------------------------------------------------- #
# Robust file reading / JSON loading.
# --------------------------------------------------------------------------- #
def read_text(path: Path, ledger: Ledger, max_bytes: int = DEFAULT_MAX_BYTES) -> Optional[str]:
    """Read a text file with size cap and layered encoding fallback.

    Returns ``None`` (and records a degradation) when the file is absent,
    oversized, unreadable, or cannot be decoded at all.
    """
    try:
        if not path.exists():
            ledger.degrade(f"{path.name}: file does not exist")
            return None
        size = path.stat().st_size
        if size > max_bytes:
            ledger.degrade(
                f"{path.name}: {size} bytes exceeds {max_bytes} byte cap; skipped"
            )
            return None
        raw = path.read_bytes()
    except OSError as exc:
        ledger.error(f"{path.name}: read failed ({exc})")
        return None

    last_err: Optional[Exception] = None
    for enc in _ENCODINGS:
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError) as exc:  # noqa: BLE001
            last_err = exc
            continue
        if enc not in ("utf-8-sig", "utf-8"):
            ledger.degrade(f"{path.name}: decoded with fallback encoding {enc}")
        return text
    ledger.error(f"{path.name}: undecodable ({last_err})")
    return None


def load_json_text(text: str, name: str, ledger: Ledger) -> Optional[Any]:
    """Parse JSON text with a best-effort repair for common real-world defects.

    Never raises: malformed JSON is recorded and ``None`` returned.
    """
    if text is None:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    repaired = _repair_json(text)
    try:
        return json.loads(repaired)
    except (json.JSONDecodeError, ValueError) as exc:
        ledger.error(f"{name}: JSON parse failed after repair ({exc})")
        return None


def load_json_file(path: Path, ledger: Ledger, max_bytes: int = DEFAULT_MAX_BYTES) -> Optional[Any]:
    text = read_text(path, ledger, max_bytes)
    if text is None:
        return None
    return load_json_text(text, path.name, ledger)


def _repair_json(text: str) -> str:
    """Remove common JSON defects: BOM, trailing commas before ] or }."""
    t = text.lstrip("\ufeff").strip()
    t = re.sub(r",\s*([}\]])", r"\1", t)
    return t


# --------------------------------------------------------------------------- #
# Atomic writing (crash-safe partial delivery).
# --------------------------------------------------------------------------- #
def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
    )


# --------------------------------------------------------------------------- #
# Library parsing (BibTeX / RIS / JSON / CSV / TSV).
# --------------------------------------------------------------------------- #
def normalize_entry(raw: dict, source_file: str, index: int) -> dict:
    """Project arbitrary dict onto a stable, defensive internal schema."""
    def s(key: str) -> str:
        v = raw.get(key)
        if v is None:
            return ""
        return str(v).strip()

    return {
        "id": None,  # assigned later by engine
        "source_file": source_file,
        "source_index": index,
        "entry_type": s("entry_type") or s("type") or s("ENTRYTYPE") or "misc",
        "authors": _parse_authors(s("authors") or s("author") or s("AU")),
        "title": s("title") or s("TI") or s("题名") or "",
        "year": s("year") or s("PY") or s("年份") or "",
        "journal": s("journal") or s("booktitle") or s("JO") or s("JF") or s("刊名") or "",
        "volume": s("volume") or s("VL") or s("卷") or "",
        "issue": s("number") or s("issue") or s("IS") or s("期") or "",
        "pages": s("pages") or s("SP") or s("EP") or s("页码") or "",
        "doi": s("doi") or s("DOI") or s("DI") or "",
        "url": s("url") or s("UR") or s("链接") or "",
        "publisher": s("publisher") or s("PB") or s("出版社") or "",
        "raw": json.dumps(raw, ensure_ascii=False, sort_keys=True),
    }


def _parse_authors(text: str) -> list[str]:
    if not text:
        return []
    parts = re.split(r"\s+and\s+|\s*;\s*|[,，]", text)
    out = []
    for p in parts:
        p = p.strip().strip("{}")
        if p:
            out.append(p)
    return out


def parse_bibtex(text: str) -> list[dict]:
    """Parse ``@type{key, field={...}, ...}`` entries without external deps.

    The scan starts *inside* the entry's outer delimiter (depth 1) so that the
    braces of field values are balanced correctly and only the entry-closing
    delimiter ends the record.
    """
    entries: list[dict] = []
    pos = 0
    opener = re.compile(r"@\s*([A-Za-z]+)\s*[{(]")
    while True:
        m = opener.search(text, pos)
        if not m:
            break
        etype = m.group(1).lower()
        open_char = text[m.end() - 1]
        start = m.end()

        body_start = start
        if open_char == "{":
            key_match = re.match(r"\s*([^,\s]+)\s*,", text[start:start + 200])
            if key_match:
                body_start = start + key_match.end()

        depth = 1
        i = start
        end = len(text)
        while i < len(text):
            ch = text[i]
            if open_char == "{":
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            else:
                # Parenthesized form: skip balanced {...} groups, close on ")".
                if ch == "{":
                    d = 1
                    i += 1
                    while i < len(text) and d:
                        if text[i] == "{":
                            d += 1
                        elif text[i] == "}":
                            d -= 1
                        i += 1
                    continue
                if ch == ")":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            i += 1

        body = text[body_start:end]
        fields = _parse_bib_fields(body)
        fields["entry_type"] = etype
        entries.append(fields)
        pos = end + 1
    return entries


def _parse_bib_fields(body: str) -> dict:
    fields: dict = {}
    for m in re.finditer(r"([A-Za-z][A-Za-z0-9_-]*)\s*=\s*", body):
        key = m.group(1).lower()
        rest = body[m.end():].lstrip()
        if not rest:
            break
        val = ""
        if rest[0] == "{":
            depth = 0
            j = 0
            while j < len(rest):
                if rest[j] == "{":
                    depth += 1
                elif rest[j] == "}":
                    depth -= 1
                    if depth == 0:
                        val = rest[1:j]
                        break
                j += 1
        elif rest[0] == '"':
            j = rest.find('"', 1)
            val = rest[1:j] if j != -1 else rest[1:]
        else:
            j = rest.find(",")
            val = rest[:j] if j != -1 else rest
        fields[key] = val.strip()
    return fields


def parse_ris(text: str) -> list[dict]:
    """Parse RIS records: lines of ``TY  -`` tags, ``ER  -`` ends a record."""
    entries: list[dict] = []
    current: dict = {}
    for line in text.splitlines():
        if line.startswith("TY  -"):
            current = {}
        elif line.startswith("ER  -"):
            if current:
                entries.append(current)
                current = {}
        elif line.startswith("  - ") or line.startswith("- "):
            tag = line[:2].strip()
            value = line[6:] if line.startswith("  - ") else line[2:]
            key = _RIS_TAGS.get(tag, tag.lower())
            current[key] = (current.get(key, "") + " " + value.strip()).strip()
    if current:
        entries.append(current)
    return entries


_RIS_TAGS = {
    "TY": "entry_type", "TI": "title", "T1": "title", "AU": "authors",
    "A1": "authors", "PY": "year", "Y1": "year", "JO": "journal",
    "JF": "journal", "JA": "journal", "VL": "volume", "IS": "issue",
    "SP": "pages", "EP": "pages", "DO": "doi", "DI": "doi",
    "UR": "url", "PB": "publisher", "SN": "issn",
}


def parse_reference_json(data: Any) -> list[dict]:
    if isinstance(data, dict):
        for key in ("entries", "references", "items", "data", "records"):
            if isinstance(data.get(key), list):
                return [d for d in data[key] if isinstance(d, dict)]
        return [data] if _looks_like_entry(data) else []
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    return []


def _looks_like_entry(d: dict) -> bool:
    keys = {k.lower() for k in d.keys()}
    return bool(keys & {"title", "ti", "authors", "author", "doi", "year"})


def parse_table(text: str, ledger: Ledger) -> list[dict]:
    """CSV/TSV -> entries when a header row names known fields."""
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    except Exception:  # noqa: BLE001
        ledger.degrade("table header not detected; skipped as library")
        return []
    if not reader.fieldnames:
        return []
    out = []
    for row in reader:
        out.append({str(k or "").strip(): (v or "").strip() for k, v in row.items()})
    return out


def parse_library_file(path: Path, ledger: Ledger, max_bytes: int) -> list[dict]:
    text = read_text(path, ledger, max_bytes)
    if text is None:
        return []
    ext = path.suffix.lower()
    try:
        if ext == ".bib":
            parsed = parse_bibtex(text)
        elif ext == ".ris" or ext == ".enw":
            parsed = parse_ris(text)
        elif ext == ".json":
            data = load_json_text(text, path.name, ledger)
            parsed = parse_reference_json(data) if data else []
        elif ext in {".csv", ".tsv"}:
            parsed = parse_table(text, ledger)
        else:
            parsed = []
    except Exception as exc:  # noqa: BLE001
        ledger.error(f"{path.name}: library parse failed ({exc})")
        return []
    return [normalize_entry(r, str(path), i) for i, r in enumerate(parsed)]


# --------------------------------------------------------------------------- #
# Deduplication with explainable evidence.
# --------------------------------------------------------------------------- #
def entry_signature(entry: dict) -> str:
    """Canonical identity key: DOI first, else normalized title+first author+year."""
    doi = (entry.get("doi") or "").strip().lower()
    if doi:
        return "doi:" + re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    title = _norm_title(entry.get("title") or "")
    first_author = (entry.get("authors") or [None])[0] or ""
    first_author = re.sub(r"[^a-z\u4e00-\u9fff]", "", first_author.lower())
    year = re.sub(r"\D", "", entry.get("year") or "")
    if title or first_author or year:
        return f"tay:{title}|{first_author}|{year}"
    return "raw:" + hashlib.sha1((entry.get("raw") or "").encode("utf-8")).hexdigest()


def _norm_title(text: str) -> str:
    t = text.lower()
    t = re.sub(r"\s+", " ", t)
    for ch in "{}[]()<>\"'.,;:!?/\\-—_–":
        t = t.replace(ch, " ")
    return " ".join(t.split())


def deduplicate(entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """Group entries by signature; return (merged entries, merge evidence)."""
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for e in entries:
        sig = entry_signature(e)
        if sig not in groups:
            groups[sig] = []
            order.append(sig)
        groups[sig].append(e)

    merged: list[dict] = []
    evidence: list[dict] = []
    for i, sig in enumerate(order):
        group = groups[sig]
        base = dict(group[0])
        base["id"] = f"ref{i + 1}"
        if len(group) > 1:
            base["_duplicate_count"] = len(group)
            base["_duplicate_sources"] = [g.get("source_file") for g in group]
            evidence.append({
                "merged_id": base["id"],
                "signature": sig,
                "criterion": "doi" if sig.startswith("doi:") else "title+first-author+year",
                "member_sources": [g.get("source_file") for g in group],
                "member_titles": [g.get("title") for g in group],
                "rationale": (
                    "Entries share the same DOI" if sig.startswith("doi:")
                    else "Entries share normalized title, first author and year"
                ),
            })
        merged.append(base)
    return merged, evidence


# --------------------------------------------------------------------------- #
# Citation markers and renumbering.
# --------------------------------------------------------------------------- #
CITE_NUM_RE = re.compile(r"\[(\d+(?:\s*[-–,，]\s*\d+)*)\]")
CITE_TEX_RE = re.compile(r"\\(?:cite|citep|citet)\s*(\[[^\]]*\])?\s*\{([^}]+)\}")


def find_citation_markers(text: str) -> list[dict]:
    """Return ordered numeric citations found in manuscript text."""
    markers = []
    for m in CITE_NUM_RE.finditer(text):
        markers.append({
            "kind": "bracket",
            "start": m.start(),
            "end": m.end(),
            "token": m.group(0),
            "numbers": _expand_numbers(m.group(1)),
        })
    return markers


def _expand_numbers(spec: str) -> list[int]:
    out: list[int] = []
    for part in re.split(r"[,，]", spec):
        part = part.strip()
        if not part:
            continue
        rng = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", part)
        if rng:
            a, b = int(rng.group(1)), int(rng.group(2))
            if a <= b:
                out.extend(range(a, b + 1))
        elif part.isdigit():
            out.append(int(part))
    return out


def renumber(text: str) -> tuple[str, dict]:
    """Renumber bracket citations by order of first appearance.

    Returns (new_text, mapping) where mapping maps old number -> new number.
    """
    markers = find_citation_markers(text)
    seen: dict[int, int] = {}
    next_new = 1
    for mk in markers:
        for n in mk["numbers"]:
            if n not in seen:
                seen[n] = next_new
                next_new += 1

    def repl(m: re.Match) -> str:
        nums = _expand_numbers(m.group(1))
        mapped = sorted({seen.get(n, n) for n in nums})
        return "[" + ",".join(str(x) for x in mapped) + "]"

    new_text = CITE_NUM_RE.sub(repl, text)
    return new_text, seen


# --------------------------------------------------------------------------- #
# Reference formatting (GB/T 7714-2015 and APA 7th).
# --------------------------------------------------------------------------- #
def format_reference(entry: dict, style: str = "gbt7714") -> str:
    authors = entry.get("authors") or []
    year = entry.get("year") or ""
    title = entry.get("title") or ""
    journal = entry.get("journal") or ""
    volume = entry.get("volume") or ""
    issue = entry.get("issue") or ""
    pages = entry.get("pages") or ""
    doi = entry.get("doi") or ""

    author_str = _format_authors(authors, style)

    if style == "apa":
        parts = []
        parts.append(author_str if author_str else "(no author)")
        parts.append(f"({year})." if year else "(n.d.).")
        parts.append(title if title else "")
        if journal:
            parts.append(f"{journal}, {volume}" if volume else journal)
            if issue:
                parts[-1] += f"({issue})"
            if pages:
                parts.append(pages)
        if doi:
            parts.append(f"https://doi.org/{doi}")
        return " ".join(p for p in parts if p)

    # GB/T 7714-2015 sequential style
    parts = []
    if author_str:
        parts.append(author_str)
    if title:
        parts.append(title + ("[J]" if journal else ""))
    if journal:
        parts.append(journal)
    if year:
        parts.append(year)
    if volume:
        parts.append(volume)
    if issue:
        parts.append(f"({issue})")
    if pages:
        parts.append(pages)
    if doi:
        parts.append(f"DOI:{doi}.")
    return ". ".join(p for p in parts if p) + ("." if parts and not parts[-1].endswith(".") else "")


def _format_authors(authors: list[str], style: str) -> str:
    if not authors:
        return ""
    if style == "apa":
        out = list(authors)
        if len(out) == 1:
            return out[0]
        if len(out) == 2:
            return f"{out[0]} & {out[1]}"
        return f"{out[0]}, {out[1]}, et al."
    out = authors[:3]
    s = ", ".join(out)
    if len(authors) > 3:
        s += ", et al"
    return s


# --------------------------------------------------------------------------- #
# Consistency checks (machine-checkable facts only).
# --------------------------------------------------------------------------- #
def consistency_check(renumbered_text: str, merged_entries: list[dict],
                      mapping: dict, original_text: str) -> dict:
    markers = find_citation_markers(renumbered_text)
    cited_numbers: set[int] = set()
    for mk in markers:
        cited_numbers.update(mk["numbers"])

    total_refs = len(merged_entries)
    uncited = [i + 1 for i in range(total_refs) if (i + 1) not in cited_numbers]

    unresolved: list[str] = []
    for n in sorted(cited_numbers):
        if n < 1 or n > total_refs:
            unresolved.append(f"[{n}]")

    gaps: list[int] = []
    if cited_numbers:
        for n in range(1, max(cited_numbers) + 1):
            if n <= total_refs and n not in cited_numbers:
                gaps.append(n)

    return {
        "citation_count": len(markers),
        "cited_numbers": sorted(cited_numbers),
        "total_references": total_refs,
        "uncited_references": uncited,
        "unresolved_citations": unresolved,
        "numbering_gaps": gaps,
        "renumber_mapping": mapping,
        "manuscript_chars_before": len(original_text),
        "manuscript_chars_after": len(renumbered_text),
    }


# --------------------------------------------------------------------------- #
# File discovery.
# --------------------------------------------------------------------------- #
def discover(input_dir: Path, ledger: Ledger, max_bytes: int) -> dict:
    """Scan ``input_dir`` (recursively) and classify files. Never raises."""
    result = {
        "manuscripts": [],
        "libraries": [],
        "case": None,
        "task": None,
        "others": [],
    }
    try:
        paths = sorted([p for p in input_dir.rglob("*") if p.is_file()])
    except OSError as exc:
        ledger.error(f"cannot scan {input_dir}: {exc}")
        return result

    for p in paths:
        try:
            size = p.stat().st_size
        except OSError:
            size = -1
        if p.name == "case.json":
            result["case"] = p
            continue
        if p.name == "task.md":
            result["task"] = p
            continue
        ext = p.suffix.lower()
        if ext in LIBRARY_EXTS:
            result["libraries"].append(p)
        elif ext in MANUSCRIPT_EXTS:
            result["manuscripts"].append(p)
        else:
            result["others"].append(p)
        if 0 <= size <= max_bytes:
            ledger.sources.append({
                "path": str(p),
                "kind": "manuscript" if ext in MANUSCRIPT_EXTS else
                        ("library" if ext in LIBRARY_EXTS else "other"),
                "bytes": size,
                "sha256": _file_hash(p),
            })
    return result


def _file_hash(path: Path) -> str:
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def case_id_of(case: Any) -> str:
    if not isinstance(case, dict):
        return ""
    for key in ("case_id", "id", "challenge_id", "armiId", "armi_id", "caseId"):
        if case.get(key):
            return str(case[key])
    return ""
