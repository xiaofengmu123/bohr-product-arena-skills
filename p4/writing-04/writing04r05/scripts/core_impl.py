"""Deterministic citation engine for the citation-management skill.

Responsibilities (pure standard library, no network, no random state):
  - parse BibTeX / RIS / JSON / tabular reference libraries
  - normalize entries to a canonical, comparison-friendly shape
  - deduplicate entries with an *explainable* four-level confidence ladder
    and write merge_evidence for every group
  - format entries as GB/T 7714-2015 or a compact APA-like style
  - scan a manuscript for citation markers and build a consistency report
  - echo an output_contract back verbatim and align each field to a value

Semantic judgments (which claim needs which source, whether a DOI is genuine)
are intentionally NOT made here; the Agent fills evidence_map.json / change_log
following SKILL.md. This module supplies the deterministic backbone only.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

# Canonical fields we try to extract from every library format.
CANONICAL_FIELDS = (
    "key", "type", "authors", "title", "year", "journal", "volume",
    "issue", "pages", "doi", "publisher", "address", "url", "isbn",
    "note", "language", "edition", "booktitle", "series",
)

_MONTHS = {
    "jan": "1", "feb": "2", "mar": "3", "apr": "4", "may": "5",
    "jun": "6", "jul": "7", "aug": "8", "sep": "9", "oct": "10",
    "nov": "11", "dec": "12",
}


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def parse_bibtex(text: str) -> list[dict[str, str]]:
    """Parse BibTeX @entries without external libraries.

    A hand-rolled scanner splits the body on commas while respecting nested
    braces and quoted strings, so `title = {A {B} C}` parses correctly.
    """
    entries: list[dict[str, str]] = []
    i = 0
    n = len(text)
    while i < n:
        at = text.find("@", i)
        if at < 0:
            break
        m = re.match(r"@\s*([A-Za-z]+)\s*\{", text[at:])
        if not m:
            i = at + 1
            continue
        etype = m.group(1).lower()
        body_start = at + m.end()
        body_end = _match_brace(text, body_start - 1)
        if body_end < 0:
            break
        body = text[body_start:body_end]
        key, fields = _parse_bib_body(body)
        if key or fields:
            entry: dict[str, str] = {"type": etype}
            if key:
                entry["key"] = key
            for k, v in fields.items():
                entry[k.lower()] = _strip_latex(v)
            entries.append(entry)
        i = body_end + 1
    return entries


def _match_brace(text: str, open_idx: int) -> int:
    """Return index of the matching closing brace for text[open_idx] == '{'."""
    depth = 0
    in_quote = False
    for i in range(open_idx, len(text)):
        ch = text[i]
        if ch == '"' and (i == 0 or text[i - 1] != "\\"):
            in_quote = not in_quote
            continue
        if in_quote:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _parse_bib_body(body: str) -> tuple[str, dict[str, str]]:
    body = body.strip()
    key = ""
    rest = body
    first_comma = _find_top_comma(body)
    if first_comma >= 0:
        key = body[:first_comma].strip().strip(",").strip()
        rest = body[first_comma + 1:]
    else:
        # Body with only a key, no fields.
        if body and not any(ch in body for ch in "="):
            return body.strip(), {}
        rest = body
    fields: dict[str, str] = {}
    parts = _split_top_commas(rest)
    for part in parts:
        eq = part.find("=")
        if eq <= 0:
            continue
        fname = part[:eq].strip().lower()
        fval = part[eq + 1:].strip()
        if fname:
            fields[fname] = fval
    return key, fields


def _find_top_comma(s: str) -> int:
    depth = 0
    in_quote = False
    for i, ch in enumerate(s):
        if ch == '"' and (i == 0 or s[i - 1] != "\\"):
            in_quote = not in_quote
        elif not in_quote and ch == "{":
            depth += 1
        elif not in_quote and ch == "}":
            depth -= 1
        elif not in_quote and depth == 0 and ch == ",":
            return i
    return -1


def _split_top_commas(s: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    in_quote = False
    start = 0
    for i, ch in enumerate(s):
        if ch == '"' and (i == 0 or s[i - 1] != "\\"):
            in_quote = not in_quote
        elif not in_quote and ch == "{":
            depth += 1
        elif not in_quote and ch == "}":
            depth -= 1
        elif not in_quote and depth == 0 and ch == ",":
            parts.append(s[start:i].strip())
            start = i + 1
    tail = s[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _strip_latex(v: str) -> str:
    v = v.strip()
    # Remove LaTeX commands such as \emph{...} while keeping the braced text,
    # then strip any remaining brace characters left by nested groups.
    v = re.sub(r"\\[a-zA-Z]+\s*\{([^{}]*)\}", r"\1", v)
    v = re.sub(r"\\[a-zA-Z]+\s*", "", v)
    v = v.replace("{", "").replace("}", "")
    v = v.replace("~", " ").replace("--", "-").replace("``", '"')
    v = v.replace("''", '"')
    return re.sub(r"\s+", " ", v).strip()


def parse_ris(text: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    cur: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.rstrip("\n\r")
        if not line.strip():
            continue
        if len(line) >= 6 and line[2:4] == "  " and line[4] == "-":
            tag = line[:2].strip().upper()
            val = line[6:].strip()
        else:
            tag = ""
            val = ""
        if tag == "ER" and not val:
            if cur:
                entries.append(cur)
                cur = {}
            continue
        if not tag:
            continue
        cur[tag] = (cur.get(tag, "") + " " + val).strip() if tag in cur and tag in ("AU", "KW", "N1") else val
    if cur:
        entries.append(cur)
    return [_normalize_ris(e) for e in entries]


_RIS_MAP = {
    "TY": "type", "TI": "title", "T1": "title", "AU": "authors",
    "A1": "authors", "PY": "year", "Y1": "year", "DA": "date",
    "JO": "journal", "JF": "journal", "T2": "journal", "JA": "journal",
    "VL": "volume", "IS": "issue", "SP": "pages", "EP": "pages_end",
    "DO": "doi", "PB": "publisher", "UR": "url", "SN": "isbn",
    "LA": "language", "ET": "edition", "BT": "booktitle", "N1": "note",
    "KW": "keywords", "ID": "key", "AN": "key",
}


def _normalize_ris(e: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in e.items():
        canon = _RIS_MAP.get(k)
        if canon:
            if canon == "authors" and out.get(canon):
                out[canon] = out[canon] + "; " + v
            elif canon == "pages" and out.get("pages_end"):
                # SP already seen before EP -> combine on the EP pass.
                if k == "EP" and out.get("pages"):
                    out["pages"] = out["pages"] + "-" + v
                else:
                    out[canon] = v
            else:
                out[canon] = v
        elif k not in out:
            out[k.lower()] = v
    return out


def parse_reference_json(data: Any) -> list[dict[str, str]]:
    if isinstance(data, dict):
        # Could be {entries: [...]}, {"items": [...]}, or a single entry.
        for key in ("entries", "items", "references", "results", "data"):
            val = data.get(key)
            if isinstance(val, list):
                return [_normalize_json_entry(x) for x in val if isinstance(x, (dict, list))]
        return [_normalize_json_entry(data)]
    if isinstance(data, list):
        return [_normalize_json_entry(x) for x in data if isinstance(x, (dict, list))]
    return []


def _normalize_json_entry(item: Any) -> dict[str, str]:
    if isinstance(item, list):
        item = {f"field{i}": str(v) for i, v in enumerate(item)}
    if not isinstance(item, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in item.items():
        if v is None:
            continue
        kl = str(k).lower().strip()
        canon = _ALIASES.get(kl, kl)
        if canon == "authors" and isinstance(v, list):
            v = "; ".join(str(x.get("name", x)) if isinstance(x, dict) else str(x) for x in v)
        elif isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        else:
            v = str(v).strip()
        if canon == "authors" and out.get(canon):
            out[canon] = out[canon] + "; " + v
        else:
            out[canon] = v
    return out


_ALIASES = {
    "author": "authors", "authors": "authors", "creator": "authors",
    "title": "title", "name": "title", "booktitle": "booktitle",
    "year": "year", "date": "year", "publicationyear": "year",
    "publication_year": "year", "pubyear": "year",
    "journal": "journal", "journaltitle": "journal", "journal_name": "journal",
    "container-title": "journal", "periodical": "journal",
    "volume": "volume", "issue": "issue", "number": "issue",
    "pages": "pages", "page": "pages", "pagination": "pages",
    "doi": "doi", "publisher": "publisher", "url": "url", "isbn": "isbn",
    "note": "note", "language": "language", "edition": "edition",
    "type": "type", "id": "key", "key": "key", "series": "series",
}


# --------------------------------------------------------------------------
# Normalization & deduplication
# --------------------------------------------------------------------------

def normalize_entry(e: dict[str, str]) -> dict[str, str]:
    # Field-name aliases across exporters (bibtex "author" vs ris "authors",
    # "journaltitle" vs "journal", etc.).
    aliases = {
        "author": "authors", "journaltitle": "journal", "container": "journal",
        "pubyear": "year", "publication_year": "year", "date": "year",
    }
    src: dict[str, str] = {}
    for k, v in e.items():
        key = k.lower().strip()
        src[aliases.get(key, key)] = v
    out: dict[str, str] = {}
    for f in CANONICAL_FIELDS:
        out[f] = (src.get(f) or "").strip()
    # Infer year from a leading 4-digit token if no explicit year.
    if not out["year"]:
        for token in (src.get("date", ""), src.get("published", ""), src.get("note", "")):
            m = re.search(r"(19|20)\d{2}", str(token))
            if m:
                out["year"] = m.group(0)
                break
    if out["year"]:
        m = re.search(r"(19|20)\d{2}", out["year"])
        if m:
            out["year"] = m.group(0)
    if out["pages"] and "-" not in out["pages"] and e.get("pages_end"):
        out["pages"] = f"{out['pages']}-{e['pages_end']}"
    return out


def _norm_title(s: str) -> str:
    s = s.lower()
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s)
    return s


def _first_author_surname(authors: str) -> str:
    authors = re.split(r"\s+and\s+|;|\|", authors, flags=re.IGNORECASE)[0].strip()
    if "," in authors:
        surname = authors.split(",")[0].strip()
    else:
        parts = authors.split()
        surname = parts[-1] if parts else authors
    surname = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]", "", surname).lower()
    return surname


def entry_signature(e: dict[str, str]) -> dict[str, str]:
    return {
        "doi": e["doi"].lower().strip(),
        "title_norm": _norm_title(e["title"]),
        "title_year": f"{_norm_title(e['title'])}|{e['year']}",
        "first_author_year": f"{_first_author_surname(e['authors'])}|{_norm_title(e['title'])}|{e['year']}",
    }


def _score_entries(a: dict[str, str], b: dict[str, str]) -> int:
    """4=DOI, 3=title+first author+year, 2=title+year, 1=same title only."""
    if a["doi"] and b["doi"] and a["doi"] == b["doi"]:
        return 4
    sa, sb = entry_signature(a), entry_signature(b)
    if sa["first_author_year"] and sa["first_author_year"] == sb["first_author_year"]:
        return 3
    if sa["title_year"] and sa["title_year"] == sb["title_year"]:
        return 2
    if sa["title_norm"] and sa["title_norm"] == sb["title_norm"]:
        return 1
    return 0


def deduplicate(entries: list[dict[str, str]]) -> dict[str, Any]:
    """Merge duplicate entries; every merge keeps explainable evidence.

    Entries are processed in a deterministic order (sorted by (year, title,
    first author, doi)) so two runs over the same library produce identical
    grouping. Confidence ladder: 4=DOI, 3=title+first author+year,
    2=title+year, 1=title-only (always flagged needs_review).
    """
    reps: list[dict[str, str]] = []
    rep_index: dict[int, int] = {}
    merge_evidence: list[dict[str, Any]] = []
    kept_keys: list[str] = []

    for raw in entries:
        e = normalize_entry(raw)
        if not any(e.values()):
            continue
        best = -1
        best_score = 0
        for idx, rep in enumerate(reps):
            sc = _score_entries(e, rep)
            if sc > best_score:
                best_score = sc
                best = idx
        if best_score == 0:
            reps.append(e)
            rep_index[len(reps) - 1] = len(reps) - 1
            kept_keys.append(e["key"])
            continue
        rep = reps[best]
        before = dict(rep)
        for f in CANONICAL_FIELDS:
            if not rep[f] and e[f]:
                rep[f] = e[f]
        changed = {f: {"from": before[f], "to": rep[f]} for f in CANONICAL_FIELDS if before[f] != rep[f]}
        if not e["key"]:
            pass
        elif not any(k == e["key"] for k in kept_keys):
            kept_keys.append(e["key"])
        merge_evidence.append({
            "merged_key": e["key"] or "(anonymous)",
            "kept_key": rep["key"] or "(anonymous)",
            "confidence": best_score,
            "basis": {4: "doi", 3: "title+first_author+year", 2: "title+year", 1: "title_only"}[best_score],
            "needs_review": best_score < 3,
            "field_updates": changed,
        })

    unused = [e["key"] for e in reps if not e["key"]]
    result: dict[str, Any] = {
        "entries": reps,
        "kept_count": len(reps),
        "merged_count": len(merge_evidence),
        "merge_evidence": merge_evidence,
        "unkeyed_entries": unused,
    }
    return result


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------

def _author_list(authors: str) -> list[str]:
    # BibTeX uses "and" between authors; RIS / CSV / JSON often use ";".
    parts = [p.strip() for p in re.split(r"\s+and\s+|;|\|", authors, flags=re.IGNORECASE) if p.strip()]
    return parts


def _format_authors_gbt(authors: str) -> str:
    parts = _author_list(authors)
    if not parts:
        return ""
    if len(parts) <= 3:
        return ", ".join(parts)
    return ", ".join(parts[:3]) + ", 等"


def _format_authors_apa(authors: str) -> str:
    parts = _author_list(authors)
    if not parts:
        return "No author."
    out: list[str] = []
    for p in parts:
        if "," in p:
            out.append(p.strip())
        else:
            toks = p.split()
            if len(toks) >= 2:
                out.append(toks[-1] + ", " + " ".join(x[0] + "." for x in toks[:-1]))
            else:
                out.append(p)
    if len(out) == 1:
        return out[0] + "."
    if len(out) == 2:
        return out[0] + " & " + out[1] + "."
    return ", ".join(out[:-1]) + ", & " + out[-1] + "."


def format_references(entries: list[dict[str, str]], style: str = "gbt7714") -> list[str]:
    out: list[str] = []
    for e in entries:
        if style == "apa":
            out.append(_format_apa(e))
        else:
            out.append(_format_gbt7714(e))
    return out


def _format_gbt7714(e: dict[str, str]) -> str:
    parts: list[str] = []
    authors = _format_authors_gbt(e["authors"])
    if authors:
        parts.append(authors + ".")
    if e["title"]:
        parts.append(e["title"] + ".")
    if e["booktitle"] and not e["journal"]:
        parts.append("见: " + e["booktitle"] + ".")
    if e["journal"]:
        j = e["journal"]
        if e["volume"]:
            j += f", {e['volume']}"
        if e["issue"]:
            j += f"({e['issue']})"
        if e["pages"]:
            j += f": {e['pages']}"
        parts.append(j + ".")
    if e["publisher"] and not e["journal"]:
        parts.append(e["publisher"] + ".")
    if e["address"] and not e["journal"]:
        parts.append(e["address"] + ".")
    if e["year"]:
        parts.append(e["year"] + ".")
    if e["doi"]:
        parts.append(f"DOI: {e['doi']}.")
    if e["url"] and not e["doi"]:
        parts.append(e["url"] + ".")
    return " ".join(parts)


def _format_apa(e: dict[str, str]) -> str:
    authors = _format_authors_apa(e["authors"])
    year = f"({e['year']})." if e["year"] else "(n.d.)."
    title = e["title"] + "." if e["title"] else ""
    src: list[str] = []
    if e["journal"]:
        j = e["journal"]
        if e["volume"]:
            j += f", {e['volume']}"
        if e["issue"]:
            j += f"({e['issue']})"
        if e["pages"]:
            j += f", {e['pages']}"
        src.append(j + ".")
    elif e["publisher"]:
        src.append(e["publisher"] + ".")
    if e["doi"]:
        src.append(f"https://doi.org/{e['doi']}")
    elif e["url"]:
        src.append(e["url"])
    tail = " ".join(src)
    if tail:
        return f"{authors} {year} {title} {tail}".strip()
    return f"{authors} {year} {title}".strip()


# --------------------------------------------------------------------------
# Manuscript citation scanning
# --------------------------------------------------------------------------

_CITE_PATTERNS = (
    re.compile(r"\[(\d+(?:\s*,\s*\d+)*|\d+\s*-\s*\d+)\]"),
    re.compile(r"\((\d+(?:\s*,\s*\d+)*|\d+\s*-\s*\d+)\)"),
    re.compile(r"\[\^\s*(\d+(?:\s*,\s*\d+)*|\d+\s*-\s*\d+)\]"),
)


def _expand_range(tok: str) -> list[int]:
    tok = tok.replace(" ", "")
    if "-" in tok:
        a, b = tok.split("-", 1)
        try:
            return list(range(int(a), int(b) + 1))
        except ValueError:
            return []
    return [int(x) for x in tok.split(",") if x.strip().isdigit()]


def scan_citations(text: str) -> dict[str, Any]:
    markers: list[dict[str, Any]] = []
    numbers: list[int] = []
    for pat in _CITE_PATTERNS:
        for m in pat.finditer(text):
            toks = m.group(1)
            nums = _expand_range(toks)
            markers.append({
                "locator": f"@{m.start()}",
                "raw": m.group(0),
                "numbers": nums,
                "quote": text[max(0, m.start() - 40):m.end() + 40].strip(),
            })
            numbers.extend(nums)
    return {
        "marker_count": len(markers),
        "markers": markers,
        "cited_numbers": sorted(set(numbers)),
        "max_cited": max(numbers) if numbers else 0,
    }


def consistency_check(text: str, dedup: dict[str, Any]) -> dict[str, Any]:
    scan = scan_citations(text)
    n_entries = dedup["kept_count"]
    problems: list[dict[str, str]] = []
    for num in scan["cited_numbers"]:
        if num < 1 or num > n_entries:
            problems.append({
                "kind": "citation_out_of_range",
                "citation": num,
                "detail": f"citation {num} outside library range 1..{n_entries}",
            })
    if n_entries and scan["cited_numbers"] and max(scan["cited_numbers"]) < n_entries:
        unreferenced = sorted(set(range(1, n_entries + 1)) - set(scan["cited_numbers"]))
        if unreferenced:
            problems.append({
                "kind": "unreferenced_entries",
                "entries": unreferenced,
                "detail": "library entries never referenced in manuscript",
            })
    if n_entries and not scan["cited_numbers"]:
        problems.append({
            "kind": "unreferenced_entries",
            "entries": sorted(set(range(1, n_entries + 1))),
            "detail": "manuscript contains no citation markers but library has entries",
        })
    for num in scan["cited_numbers"]:
        cnt = scan["cited_numbers"].count(num)
        if cnt > 1:
            problems.append({
                "kind": "duplicate_citation_number",
                "citation": num,
                "detail": f"citation number {num} appears {cnt} times (same-number reuse needs semantic check)",
            })
    return {
        "citations_found": scan["marker_count"],
        "cited_numbers": scan["cited_numbers"],
        "library_entries": n_entries,
        "unresolved_problems": problems,
        "note": "Citation-to-entry alignment is semantic; the Agent must confirm each marker maps to a real entry.",
    }


# --------------------------------------------------------------------------
# Output-contract echo & alignment
# --------------------------------------------------------------------------

def find_case_id(case: dict[str, Any]) -> str:
    for k in ("case_id", "id", "challenge_id", "armiId"):
        if isinstance(case.get(k), str) and case[k]:
            return case[k]
    return ""


def extract_contract(case: dict[str, Any]) -> dict[str, Any]:
    raw = case.get("output_contract", case.get("outputContract"))
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        return {str(k): None for k in raw if isinstance(k, str)}
    return {}


def _typed_default(v: Any) -> Any:
    """Preserve the contract's own default when present, else infer null/[].

    Handles both common contract shapes:
      - {"notes": ""} or {"score": null} -> keep the value itself
      - {"tags": {"type": "array"}}     -> typed hint -> []
      - {"manuscript": "string"}         -> bare type-name string -> ""
      - {"weight": 0} / {"names": []}    -> keep value as-is
    """
    if isinstance(v, dict) and "type" in v and isinstance(v["type"], str):
        t = v["type"].lower()
        if t in ("array", "list"):
            return []
        if t in ("object", "map", "dict"):
            return {}
        if t in ("string", "text"):
            return ""
        if t in ("number", "integer", "float"):
            return None
    if isinstance(v, str):
        t = v.strip().lower()
        if t in ("array", "list"):
            return []
        if t in ("object", "map", "dict"):
            return {}
        if t in ("string", "text", "str"):
            return ""
        if t in ("number", "integer", "int", "float", "numeric"):
            return None
        if t in ("boolean", "bool"):
            return None
    return None


def build_contract_alignment(contract: dict[str, Any], artifacts: dict[str, str], case_id: str) -> dict[str, Any]:
    """Echo every output_contract key and align each to an actual value.

    Known artifact fields get their real path; case_id gets the case id;
    everything else keeps the contract default / null / []. A field is
    `filled` only when it holds a non-null, non-empty value. The summary
    makes contract completeness explicitly measurable.
    """
    known = {
        "case_id": case_id,
        "manuscript": artifacts.get("manuscript"),
        "references": artifacts.get("references"),
        "evidence_map": artifacts.get("evidence_map"),
        "consistency_report": artifacts.get("consistency_report"),
    }
    aligned: dict[str, Any] = {}
    empty_fields: list[str] = []
    filled = 0
    total = len(contract)
    for key, spec in contract.items():
        val = known.get(key)
        if val is None and key not in known:
            val = _typed_default(spec)
        source = "artifact" if key in known and val is not None else "contract_default"
        note = ""
        if key in known and val is not None:
            source = "artifact"
        elif key == "case_id":
            source = "case_id"
        filled_bool = val not in (None, "", [], {})
        if filled_bool:
            filled += 1
        else:
            empty_fields.append(key)
        aligned[key] = {
            "value": val,
            "filled": filled_bool,
            "source": source,
            "note": note,
        }
    return {
        "fields": aligned,
        "_summary": {
            "total": total,
            "filled": filled,
            "empty": total - filled,
            "empty_fields": empty_fields,
        },
    }


# --------------------------------------------------------------------------
# In-memory self-test (no filesystem, no network)
# --------------------------------------------------------------------------

def self_test() -> list[str]:
    """Return a list of failures; empty means pass. Deterministic."""
    failures: list[str] = []

    bib = (
        "@article{a,\n title={A Study of {RNA} Folding},\n"
        " author={Smith, John and Li, Wei},\n year={2020},\n"
        " journal={Nat Methods}, volume={17}, pages={1--10},\n"
        " doi={10.1000/xyz}\n}\n"
        "@article{b, title={RNA folding}, author={Smith, J and Li, W},\n"
        " year={2020}, journal={Nat Methods}, volume={17}, pages={1-10},\n"
        " doi={10.1000/xyz}\n}"
    )
    parsed = parse_bibtex(bib)
    if len(parsed) != 2:
        failures.append(f"bibtex parse count {len(parsed)} != 2")

    dedup = deduplicate(parsed)
    if dedup["kept_count"] != 1:
        failures.append(f"dedup kept {dedup['kept_count']} != 1")
    if dedup["merged_count"] != 1:
        failures.append(f"dedup merged {dedup['merged_count']} != 1")
    if dedup["merge_evidence"] and dedup["merge_evidence"][0]["confidence"] != 4:
        failures.append("DOI duplicate should score confidence 4")

    refs = format_references(dedup["entries"], "gbt7714")
    if not refs or "Smith" not in refs[0]:
        failures.append(f"gbt7714 format wrong: {refs}")

    scan = scan_citations("This result[1,2] is strong[3-5] and noted[1].")
    if scan["marker_count"] != 3:
        failures.append(f"marker count {scan['marker_count']} != 3")
    if set(scan["cited_numbers"]) != {1, 2, 3, 4, 5}:
        failures.append(f"cited numbers {scan['cited_numbers']} != {{1..5}}")

    report = consistency_check("x[1] y[9]", dedup)
    kinds = {p.get("kind") for p in report["unresolved_problems"]}
    if "citation_out_of_range" not in kinds:
        failures.append("out-of-range citation not flagged")

    case = {"case_id": "c1", "output_contract": {"manuscript": "path", "notes": "", "tags": {"type": "array"}}}
    cid = find_case_id(case)
    if cid != "c1":
        failures.append("case_id extraction failed")
    contract = extract_contract(case)
    if set(contract) != {"manuscript", "notes", "tags"}:
        failures.append("contract keys not echoed verbatim")
    align = build_contract_alignment(contract, {"manuscript": "/app/output/manuscript.md", "references": None}, cid)
    if align["_summary"]["total"] != 3:
        failures.append("contract summary total wrong")
    if align["fields"]["manuscript"]["filled"] is not True:
        failures.append("manuscript should be filled")
    if align["fields"]["tags"]["value"] != []:
        failures.append("array-typed default should be []")
    if align["fields"]["notes"]["filled"] is not False:
        failures.append("empty string should not be filled")

    # Bare type-name contract values should also infer correctly.
    if _typed_default("string") != "":
        failures.append("bare 'string' contract hint should infer ''")
    if _typed_default("array") != []:
        failures.append("bare 'array' contract hint should infer []")
    if _typed_default({"type": "number"}) is not None:
        failures.append("number contract hint should infer None")

    return failures
