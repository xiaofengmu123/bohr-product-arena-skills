"""Deterministic engine for reference & citation management.

Pure standard library. No semantic judgments are made here: this module only
parses, normalizes, deduplicates (with explainable evidence), renumbers,
formats and cross-checks. Claim-level citation placement and source
verification are the Agent's job (see SKILL.md / references/checklist.md).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
import zipfile
from pathlib import Path
from typing import Any, Iterable

# --------------------------------------------------------------------------
# Text / IO helpers
# --------------------------------------------------------------------------

MAX_FILE_BYTES = 2 * 1024 * 1024


def read_text(path: Path) -> str:
    """Read text with BOM/encoding tolerance. Returns '' on failure."""
    if not path.exists():
        return ""
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if len(data) > MAX_FILE_BYTES:
        return ""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def load_json_file(path: Path) -> Any:
    text = read_text(path)
    if not text:
        return None
    return load_json_text(text, path.name)


def load_json_text(text: str, name: str = "json") -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Tolerate a trailing BOM char or common wrapping artifacts.
        stripped = text.lstrip("\ufeff \t\r\n")
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return None


def docx_text(path: Path) -> str:
    """Minimal .docx -> plain text using only zipfile + regex."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "word/document.xml" not in names:
                return ""
            xml = zf.read("word/document.xml").decode("utf-8", "replace")
    except (OSError, zipfile.BadZipFile):
        return ""
    xml = re.sub(r"<w:p[ >]", "\n", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    return _unescape_xml(xml)


def xlsx_text(path: Path) -> str:
    """Minimal .xlsx -> shared strings joined by newline."""
    try:
        with zipfile.ZipFile(path) as zf:
            if "xl/sharedStrings.xml" not in zf.namelist():
                return ""
            xml = zf.read("xl/sharedStrings.xml").decode("utf-8", "replace")
    except (OSError, zipfile.BadZipFile):
        return ""
    parts = re.findall(r"<t[^>]*>(.*?)</t>", xml, flags=re.S)
    return "\n".join(_unescape_xml(p) for p in parts)


def _unescape_xml(s: str) -> str:
    return (
        s.replace("&lt;", "<").replace("&gt;", ">")
        .replace("&quot;", '"').replace("&apos;", "'")
        .replace("&amp;", "&")
    )


def read_doc(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".docx":
        return docx_text(path)
    if ext == ".xlsx":
        return xlsx_text(path)
    if ext == ".pdf":
        return ""  # PDF text extraction is delegated to runtime pdf-text-tool.
    return read_text(path)


# --------------------------------------------------------------------------
# Entry normalization
# --------------------------------------------------------------------------

FIELD_ALIASES = {
    "title": ("title", "ti", "标题", "题名", "title_zh"),
    "authors": ("author", "authors", "au", "作者"),
    "year": ("year", "py", "date", "year_published", "出版年", "年份"),
    "journal": ("journal", "jo", "journaltitle", "source", "期刊", "刊名"),
    "booktitle": ("booktitle", "proceedings", "会议", "论文集"),
    "publisher": ("publisher", "pb", "出版社"),
    "school": ("school", "institution", "机构", "学校"),
    "volume": ("volume", "vl", "卷"),
    "issue": ("issue", "number", "is", "期"),
    "pages": ("pages", "sp", "ep", "page", "页", "页码"),
    "doi": ("doi", "dio", "DOI"),
    "url": ("url", "link", "网址"),
    "type": ("type", "entrytype", "pt", "文献类型", "entry_type"),
    "abstract": ("abstract", "ab", "摘要"),
}


def _clean_key(k: Any) -> str:
    return str(k or "").strip().lower().lstrip("@").replace("-", "_").replace(" ", "_")


def _pick(d: dict, *keys: str) -> str:
    for k in keys:
        for dk, dv in d.items():
            if _clean_key(dk) == k or _clean_key(dk) in FIELD_ALIASES.get(k, ()):
                v = dv
                if isinstance(v, (list, tuple)):
                    v = "; ".join(str(x) for x in v if str(x).strip())
                s = str(v or "").strip()
                if s:
                    return s
    return ""


def normalize_entry(raw: dict, source: str) -> dict:
    """Return a canonical entry dict; key is a stable content hash."""
    title = _pick(raw, "title")
    authors = _parse_authors(_pick(raw, "authors"))
    year = _year_from(_pick(raw, "year"))
    doi = _norm_doi(_pick(raw, "doi"))
    entry = {
        "entry_type": _pick(raw, "type") or "misc",
        "title": title,
        "authors": authors,
        "first_author": (authors[0] if authors else ""),
        "year": year,
        "journal": _pick(raw, "journal"),
        "booktitle": _pick(raw, "booktitle"),
        "publisher": _pick(raw, "publisher"),
        "school": _pick(raw, "school"),
        "volume": _pick(raw, "volume"),
        "issue": _pick(raw, "issue"),
        "pages": _pick(raw, "pages"),
        "doi": doi,
        "url": _pick(raw, "url"),
        "abstract": _pick(raw, "abstract"),
        "source": source,
        "raw": {k: v for k, v in raw.items() if str(v).strip()},
    }
    entry["key"] = _entry_key(entry)
    return entry


def _entry_key(e: dict) -> str:
    basis = f"{_norm_title(e.get('title', ''))}|{_norm_author(e.get('first_author', ''))}|{e.get('year', '')}"
    return "e" + hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()[:12]


def _parse_authors(author_str: str) -> list[str]:
    """Parse an author field into a list of full names.

    Supports BibTeX ' and ' separators and RIS/EndNote ';' separators.
    A single 'Last, First' entry is converted to 'First Last'.
    """
    if not author_str.strip():
        return []
    if re.search(r"\band\b", author_str, flags=re.I):
        parts = re.split(r"\s+and\s+", author_str, flags=re.I)
    elif ";" in author_str:
        parts = [p for p in author_str.split(";") if p.strip()]
    else:
        parts = [author_str]
    out = []
    for p in parts:
        p = p.strip().rstrip(",.")
        if not p:
            continue
        if "," in p:
            last, _, first = p.partition(",")
            out.append(f"{first.strip()} {last.strip()}".strip())
        else:
            out.append(p)
    return out


def _year_from(s: str) -> str:
    m = re.search(r"(1[89]\d{2}|20\d{2})", s or "")
    return m.group(1) if m else ""


def _norm_doi(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = re.sub(r"^doi:\s*", "", s)
    return s.strip()


def _norm_title(s: str) -> str:
    s = (s or "").lower()
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[\s\-_:/\[\]{}()«»“”\"',.;:!?]+", " ", s)
    s = re.sub(r"\{[^}]*\}", " ", s)  # strip BibTeX braces
    return re.sub(r"\s+", " ", s).strip()


def _norm_author(s: str) -> str:
    s = (s or "").lower()
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", " ", s).strip(" ,.")


# --------------------------------------------------------------------------
# Library parsing (BibTeX / RIS / JSON / CSV / TSV)
# --------------------------------------------------------------------------

def _split_top_level(text: str, sep: str = ",") -> list[str]:
    """Split on a separator, ignoring separators inside braces/quotes."""
    parts, depth, quote, buf = [], 0, None, []
    for c in text:
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            continue
        if c in '"{':
            quote = c if c == '"' else None
            if c == "{":
                depth += 1
            buf.append(c)
            continue
        if c == "}":
            depth = max(0, depth - 1)
            buf.append(c)
            continue
        if c == sep and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(c)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _strip_quotes(val: str) -> str:
    val = val.strip()
    if len(val) >= 2 and val[0] == "{" and val[-1] == "}":
        return val[1:-1].strip()
    if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
        return val[1:-1].strip()
    return val


def parse_bibtex(text: str, source: str = "bib") -> list[dict]:
    entries = []
    # Split balanced @type{...} records.
    records, i, n = [], 0, len(text)
    while i < n:
        j = text.find("@", i)
        if j == -1:
            break
        k = text.find("{", j)
        if k == -1:
            break
        depth, m = 0, k
        while m < n:
            if text[m] == "{":
                depth += 1
            elif text[m] == "}":
                depth -= 1
                if depth == 0:
                    break
            m += 1
        if m >= n:
            break
        records.append(text[j:m + 1])
        i = m + 1
    for rec in records:
        head = re.match(r"@(\w+)\s*\{\s*([^,]+),\s*(.*)\}\s*$", rec, flags=re.S)
        if not head:
            continue
        etype = head.group(1).lower()
        body = head.group(3)
        raw = {"type": etype}
        for field in _split_top_level(body, ","):
            if "=" not in field:
                continue
            fname, _, fval = field.partition("=")
            raw[fname.strip().lower()] = _strip_quotes(fval)
        if any(raw.get(k) for k in ("title", "author", "doi", "year", "journal")):
            entries.append(normalize_entry(raw, source))
    return entries


def parse_ris(text: str, source: str = "ris") -> list[dict]:
    entries, cur = [], {}
    ris_type = {"JOUR": "article", "BOOK": "book", "CHAP": "inproceedings",
                "CONF": "inproceedings", "THES": "thesis", "ELEC": "misc", "GEN": "misc"}
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if re.match(r"^ER\b", line.strip(), flags=re.I):
            if cur:
                entries.append(normalize_entry(cur, source))
                cur = {}
            continue
        m = re.match(r"^([A-Za-z0-9]{2})\s+-\s?(.*)$", line)
        if not m:
            continue
        tag, val = m.group(1).upper(), m.group(2).strip()
        if tag == "TY":
            cur["type"] = ris_type.get(val.upper(), "misc")
        elif tag in ("TI", "T1"):
            cur["title"] = (cur.get("title", "") + " " + val).strip()
        elif tag in ("AU", "A1"):
            cur["author"] = (cur.get("author", "") + "; " + val).strip("; ")
        elif tag in ("PY", "Y1"):
            cur["year"] = val
        elif tag in ("JF", "JO", "T2"):
            cur["journal"] = val
        elif tag == "VL":
            cur["volume"] = val
        elif tag == "IS":
            cur["issue"] = val
        elif tag == "SP":
            cur["pages"] = val
        elif tag == "EP":
            cur["pages"] = (cur.get("pages", "") + "-" + val).strip("-")
        elif tag == "DO":
            cur["doi"] = val
        elif tag == "UR":
            cur["url"] = val
        elif tag == "PB":
            cur["publisher"] = val
        elif tag == "AB":
            cur["abstract"] = val
    if cur:
        entries.append(normalize_entry(cur, source))
    return entries


def parse_reference_json(data: Any, source: str = "json") -> list[dict]:
    if isinstance(data, dict):
        # Either {id: entry} map or a single entry or {"entries": [...]}.
        if isinstance(data.get("entries"), list):
            data = data["entries"]
        elif isinstance(data.get("references"), list):
            data = data["references"]
        elif any(isinstance(v, dict) for v in data.values()):
            data = [v for v in data.values() if isinstance(v, dict)]
        else:
            data = [data]
    if not isinstance(data, list):
        return []
    return [normalize_entry(d, source) for d in data if isinstance(d, dict)]


def parse_table(text: str, source: str = "table") -> list[dict]:
    if not text.strip():
        return []
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    except Exception:
        return []
    if not reader.fieldnames:
        return []
    entries = []
    for row in reader:
        item = {str(k or "").strip(): (v or "").strip() for k, v in row.items()}
        if any(item.values()):
            entries.append(normalize_entry(item, source))
    return entries


# --------------------------------------------------------------------------
# Deduplication with explainable evidence
# --------------------------------------------------------------------------

def deduplicate(entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """Merge duplicate records; return (unique_entries, merge_evidence)."""
    buckets: dict[str, list[dict]] = {}
    order: list[str] = []
    for e in entries:
        doi = e.get("doi", "")
        if doi:
            bkey = "doi:" + doi
        else:
            bkey = "t:" + _entry_key(e)
        if bkey not in buckets:
            buckets[bkey] = []
            order.append(bkey)
        buckets[bkey].append(e)

    merged, evidence = [], []
    for bkey in order:
        group = buckets[bkey]
        if len(group) == 1:
            merged.append(group[0])
            continue
        # Prefer the record with most populated fields as canonical.
        canonical = max(group, key=lambda e: sum(1 for v in e.values() if v not in ("", [], None)))
        merged.append(_merge_group(group, canonical))
        evidence.append({
            "entry_key": canonical["key"],
            "criterion": "doi" if bkey.startswith("doi:") else "normalized_title+first_author+year",
            "source_files": sorted({e.get("source", "?") for e in group}),
            "merged_count": len(group),
            "note": "同一条文献的重复记录已合并，字段以最完整者为底、缺失字段互补。",
        })
    # Second pass: title+author+year clusters that escaped DOI/entry-key match.
    merged, evidence2 = _cluster_title_author(merged)
    return merged, evidence + evidence2


def _merge_group(group: list[dict], canonical: dict) -> dict:
    out = dict(canonical)
    for e in group:
        for k, v in e.items():
            if k in ("key", "raw", "source"):
                continue
            if v in ("", [], None) and out.get(k) in ("", [], None):
                out[k] = v if v not in (None, [], "") else out.get(k)
            elif v not in ("", [], None) and (out.get(k) in ("", [], None)):
                out[k] = v
    out["source"] = "; ".join(sorted({e.get("source", "?") for e in group}))
    return out


def _cluster_title_author(entries: list[dict]) -> tuple[list[dict], list[dict]]:
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for e in entries:
        gkey = f"{_norm_title(e.get('title', ''))}|{_norm_author(e.get('first_author', ''))}|{e.get('year', '')}"
        if gkey not in groups:
            groups[gkey] = []
            order.append(gkey)
        groups[gkey].append(e)
    merged, evidence = [], []
    for gkey in order:
        group = groups[gkey]
        if len(group) == 1:
            merged.append(group[0])
            continue
        canonical = max(group, key=lambda e: sum(1 for v in e.values() if v not in ("", [], None)))
        merged.append(_merge_group(group, canonical))
        evidence.append({
            "entry_key": canonical["key"],
            "criterion": "normalized_title+first_author+year",
            "source_files": sorted({e.get("source", "?") for e in group}),
            "merged_count": len(group),
            "note": "题名规范化 + 第一作者 + 年份一致时合并。",
        })
    return merged, evidence


# --------------------------------------------------------------------------
# Citation marker detection & renumbering (numeric style)
# --------------------------------------------------------------------------

CITE_RE = re.compile(r"\[\s*(\d+(?:\s*[-–]\s*\d+)?(?:\s*,\s*\d+(?:\s*[-–]\s*\d+)?)*)\s*\]")


def detect_citations(text: str) -> list[dict]:
    """Return in-text markers in order of appearance."""
    out = []
    for m in CITE_RE.finditer(text):
        nums = _expand_numbers(m.group(1))
        if nums:
            out.append({"marker": m.group(0), "numbers": nums, "start": m.start(), "end": m.end()})
    return out


def _expand_numbers(s: str) -> list[int]:
    nums: list[int] = []
    for part in re.split(r",", s):
        part = part.strip()
        m = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                a, b = b, a
            nums.extend(range(a, b + 1))
        elif part.isdigit():
            nums.append(int(part))
    return sorted(set(nums))


def render_numbers(nums: Iterable[int]) -> str:
    nums = sorted(set(int(n) for n in nums))
    if not nums:
        return "[]"
    ranges, start, prev = [], nums[0], nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        ranges.append((start, prev))
        start = prev = n
    ranges.append((start, prev))
    parts = [f"{a}" if a == b else f"{a}-{b}" for a, b in ranges]
    return "[" + ",".join(parts) + "]"


def renumber_numeric(text: str, assignment: dict[int, str], entries: dict[str, dict]) -> tuple[str, list[str], list[dict]]:
    """Rewrite numeric markers using assignment {old_number: entry_key}.

    Returns (new_text, ordered_entry_keys, unresolved[]).
    Final numbering follows order of first appearance of each old number.
    """
    ordered: list[str] = []
    seen: dict[int, str] = {}
    unresolved: list[dict] = []

    def _resolve(n: int) -> int | None:
        key = assignment.get(n)
        if not key:
            return None
        if n not in seen:
            seen[n] = key
            if key not in ordered:
                ordered.append(key)
        return ordered.index(key) + 1

    def _repl(m: re.Match) -> str:
        nums = _expand_numbers(m.group(1))
        mapped = []
        for n in nums:
            newn = _resolve(n)
            if newn is None:
                unresolved.append({"old_number": n, "locator": f"@{m.start()}"})
            else:
                mapped.append(newn)
        if not mapped:
            return m.group(0)
        return render_numbers(mapped)

    new_text = CITE_RE.sub(_repl, text)
    return new_text, ordered, unresolved


# --------------------------------------------------------------------------
# Formatting: GB/T 7714-2015 (numeric) and APA
# --------------------------------------------------------------------------

def _author_gbt(a: str) -> str:
    """'John Smith' -> 'SMITH J'; Chinese name kept as-is."""
    a = a.strip()
    if not a:
        return ""
    if re.search(r"[\u4e00-\u9fff]", a):
        return a
    if "," in a:
        last, _, first = a.partition(",")
        last, first = last.strip(), first.strip()
    else:
        parts = a.split()
        last, first = parts[-1], " ".join(parts[:-1])
    initials = "".join(w[0].upper() for w in first.split() if w)
    return f"{last.upper()} {initials}".strip()


def _author_apa(a: str) -> str:
    a = a.strip()
    if not a:
        return ""
    if re.search(r"[\u4e00-\u9fff]", a):
        return a
    if "," in a:
        last, _, first = a.partition(",")
        last, first = last.strip(), first.strip()
    else:
        parts = a.split()
        last, first = parts[-1], " ".join(parts[:-1])
    initials = " ".join(f"{w[0].upper()}." for w in first.split() if w)
    return f"{last}, {initials}".strip()


def _author_list_zh(authors: list[str]) -> str:
    if not authors:
        return "[佚名]"
    names = [_author_gbt(a) for a in authors if _author_gbt(a)]
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]}, {names[1]}"
    if len(names) == 3:
        return f"{names[0]}, {names[1]}, {names[2]}"
    return f"{names[0]}, {names[1]}, {names[2]}, 等"


def _author_list_apa(authors: list[str]) -> str:
    if not authors:
        return ""
    names = [_author_apa(a) for a in authors if _author_apa(a)]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]}, & {names[1]}"
    if len(names) <= 7:
        return ", ".join(names[:-1]) + ", & " + names[-1]
    return ", ".join(names[:6]) + ", ... " + names[-1]


def format_entry(e: dict, style: str) -> str:
    """Best-effort single-entry renderer; not a full style implementation."""
    style = (style or "gbt7714").lower()
    if style in ("apa", "author-year", "authoryear"):
        return _format_apa(e)
    return _format_gbt(e)


def _format_gbt(e: dict) -> str:
    et = e.get("entry_type", "misc").lower()
    authors = _author_list_zh(e.get("authors", []))
    title = e.get("title", "") or "[无题名]"
    year = e.get("year", "") or "[年份不详]"
    vol = e.get("volume", "")
    issue = e.get("issue", "")
    pages = e.get("pages", "")
    if et in ("article", "jour", "j"):
        loc = f"{vol}" + (f"({issue})" if issue else "") + (f": {pages}" if pages else "")
        return f"{authors}. {title}[J]. {e.get('journal', '[刊名不详]')}, {year}, {loc}.".rstrip(", .") + "."
    if et in ("book", "m"):
        pub = e.get("publisher", "[出版社不详]")
        return f"{authors}. {title}[M]. {pub}, {year}."
    if et in ("inproceedings", "conf", "c", "conference"):
        pub = e.get("publisher", "[出版社不详]")
        return f"{authors}. {title}[C]//{e.get('booktitle', '[会议名不详]')}. {pub}, {year}: {pages}.".rstrip(": .") + "."
    if et in ("thesis", "d"):
        return f"{authors}. {title}[D]. {e.get('school', '[机构不详]')}, {year}."
    doi = e.get("doi", "")
    tail = f" https://doi.org/{doi}" if doi else ""
    return f"{authors}. {title}[Z]. {e.get('publisher', e.get('journal', ''))}, {year}.{tail}"


def _format_apa(e: dict) -> str:
    authors = _author_list_apa(e.get("authors", [])) or "Anonymous"
    year = e.get("year", "") or "n.d."
    title = e.get("title", "") or "Untitled"
    journal = e.get("journal", "")
    vol = e.get("volume", "")
    issue = e.get("issue", "")
    pages = e.get("pages", "")
    doi = e.get("doi", "")
    if journal:
        vol_issue = vol + (f"({issue})" if issue else "")
        ref = f"{authors} ({year}). {title}. {journal}"
        if vol_issue:
            ref += f", {vol_issue}"
        if pages:
            ref += f", {pages}"
        ref += "."
    else:
        ref = f"{authors} ({year}). {title}."
        if e.get("publisher"):
            ref += f" {e['publisher']}."
    if doi:
        ref += f" https://doi.org/{doi}"
    return ref


# --------------------------------------------------------------------------
# Consistency check
# --------------------------------------------------------------------------

def consistency_check(manuscript: str, cited_order: list[str], entries: dict[str, dict]) -> dict:
    markers = detect_citations(manuscript)
    cited_numbers = sorted({n for m in markers for n in m["numbers"]})
    cited_keys = [k for k in cited_order if k]
    entry_keys = set(entries.keys())
    unresolved = [k for k in cited_keys if k not in entry_keys]
    uncited = sorted(entry_keys - set(cited_keys))
    gaps = []
    if cited_numbers:
        expected = list(range(1, max(cited_numbers) + 1))
        gaps = [n for n in expected if n not in cited_numbers]
    return {
        "marker_count": len(markers),
        "distinct_citation_numbers": cited_numbers,
        "cited_entry_keys": cited_keys,
        "unresolved_citations": unresolved,
        "uncited_entries": uncited,
        "numbering_gaps": gaps,
        "numbering_consistent": not gaps,
    }
