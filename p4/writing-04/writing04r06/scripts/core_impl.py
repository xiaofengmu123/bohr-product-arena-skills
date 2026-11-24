"""Deterministic citation/reference engine (pure standard library).

This module is the backbone of the citation-management skill.  It never
fetches anything from the network, never parses PDFs, and never invents
semantic judgments.  It extracts mechanical facts from the input materials,
maintains a structured intermediate representation (IR), merges the agent's
semantic decisions, and renders the editable artifacts deterministically.

Design principle
----------------
The IR file ``citation_model.json`` is the single source of truth.  The
editable ``manuscript.md`` and ``references.md`` are *projections* rendered
from the IR, so body markers and the reference list can never drift apart.
The agent writes its semantic judgments only into ``agent_edits.json``; every
deterministic step (numbering, dedup proposal, style rendering, consistency
check) is re-computed by this module.
"""
from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from pathlib import Path

IR_SCHEMA_VERSION = "1.0"
DEFAULT_STYLE = "gbt7714"
STYLES = {"gbt7714", "apa"}
MAX_FILE_BYTES = 2 * 1024 * 1024

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

# Numeric marker: [1], [1,2], [1-3], [1, 2, 5-7], [1，2], [1–3]
NUMERIC_MARKER_RE = re.compile(r"\[(\d+(?:\s*[,，\-–]\s*\d+)*)\]")

# Author-year marker: (Smith, 2020), (Smith et al., 2020), (Smith, 2020; Doe, 2021)
AUTHOR_YEAR_RE = re.compile(r"\(([^()]*?(?:19|20)\d{2}[a-z]?[^()]*)\)")

SENT_SPLIT_RE = re.compile(r"(?<=[.!?\u3002\uff01\uff1f])\s+")


# --------------------------------------------------------------------------- #
# Ledger: accumulate errors / degradations without ever crashing.
# --------------------------------------------------------------------------- #
class Ledger:
    def __init__(self):
        self.errors = []
        self.degradations = []

    def error(self, msg):
        self.errors.append(msg)

    def degrade(self, msg):
        self.degradations.append(msg)

    def lists(self):
        return {"errors": list(self.errors), "degradations": list(self.degradations)}


# --------------------------------------------------------------------------- #
# Robust file I/O (utf-8-sig first, size cap, atomic write).
# --------------------------------------------------------------------------- #
def read_text_safe(path, ledger):
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError as exc:
        ledger.error(f"{p.name}: unreadable ({exc})")
        return ""
    if len(data) > MAX_FILE_BYTES:
        ledger.degrade(f"{p.name}: exceeds {MAX_FILE_BYTES // 1048576} MB limit, skipped")
        return ""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    ledger.error(f"{p.name}: undecodable text, skipped")
    return ""


def load_json_safe(path, ledger):
    txt = read_text_safe(path, ledger)
    if not txt.strip():
        return None
    try:
        return json.loads(txt)
    except json.JSONDecodeError as exc:
        ledger.error(f"{Path(path).name}: bad JSON ({exc})")
        return None


def write_text_atomic(path, text):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)


def write_json_atomic(path, obj):
    write_text_atomic(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


# --------------------------------------------------------------------------- #
# Normalization helpers.
# --------------------------------------------------------------------------- #
def is_cjk(s):
    return bool(s) and bool(CJK_RE.search(s))


def norm_key(s):
    s = unicodedata.normalize("NFKC", str(s or ""))
    return "".join(ch for ch in s.lower() if ch.isalnum())


def norm_title(t):
    return norm_key(t)


def norm_doi(d):
    s = str(d or "").strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = re.sub(r"^doi:\s*", "", s)
    return s.strip()


def arxiv_id_of(entry):
    f = entry.get("fields", entry)
    for key in ("arxiv_id", "eprint"):
        v = f.get(key)
        if v:
            return re.sub(r"^(arxiv:|https?://arxiv\.org/abs/)", "", str(v)).strip()
    return ""


# --------------------------------------------------------------------------- #
# Author parsing.
# --------------------------------------------------------------------------- #
def parse_authors(raw):
    if raw is None:
        return []
    if isinstance(raw, list):
        items = [str(x) for x in raw]
    else:
        items = re.split(r"\s+(?:and|&)\s+|\s*[;；]\s*", str(raw))
    out = []
    for a in items:
        a = str(a).strip().strip(",").strip()
        if not a or a.lower() in ("n/a", "none", "-"):
            continue
        if "," in a:
            family, _, given = a.partition(",")
            family = family.strip()
            given = given.strip()
        else:
            toks = a.split()
            if len(toks) >= 2:
                family, given = toks[-1], " ".join(toks[:-1])
            else:
                family, given = a, ""
        out.append({"family": family, "given": given, "raw": a})
    return out


def _given_initials(given):
    toks = re.findall(r"[A-Za-z\u4e00-\u9fff]+", given or "")
    if not toks:
        return ""
    if any(is_cjk(t) for t in toks):
        return "".join(toks)
    return "".join(t[0].upper() + "." for t in toks)


def _name_gbt(a):
    fam = a.get("family", "").strip()
    if not fam:
        fam = a.get("raw", "").strip()
    if is_cjk(fam):
        return fam + (a.get("given", "") or "")
    ini = _given_initials(a.get("given", ""))
    return fam + ((" " + ini) if ini else "")


def format_authors_gbt(authors):
    authors = [a for a in (authors or []) if a.get("family")]
    names = [_name_gbt(a) for a in authors]
    if not names:
        return ""
    if len(names) <= 3:
        return ", ".join(names)
    suffix = "\u7b49" if any(is_cjk(a["family"]) for a in authors) else "et al"
    return ", ".join(names[:3]) + ", " + suffix


def _name_apa(a):
    fam = a.get("family", "").strip()
    if not fam:
        fam = a.get("raw", "").strip()
    giv = a.get("given", "")
    if is_cjk(fam):
        return fam + (giv or "")
    ini = _given_initials(giv)
    return fam + ((", " + ini) if ini else "")


def format_authors_apa(authors):
    authors = [a for a in (authors or []) if a.get("family")]
    names = [_name_apa(a) for a in authors]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]}, & {names[1]}"
    if len(names) <= 20:
        return ", ".join(names[:-1]) + ", & " + names[-1]
    return ", ".join(names[:19]) + ", \u2026 " + names[-1]


# --------------------------------------------------------------------------- #
# Library parsing: BibTeX / RIS / JSON / CSV / TSV.
# --------------------------------------------------------------------------- #
def _split_top_commas(s):
    parts, depth, in_q, cur = [], 0, False, []
    for ch in s:
        if in_q:
            cur.append(ch)
            if ch == '"':
                in_q = False
        elif ch == '"':
            in_q = True
            cur.append(ch)
        elif ch == "{":
            depth += 1
            cur.append(ch)
        elif ch == "}":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return parts


def _parse_bib_fields(body):
    fields = {}
    for part in _split_top_commas(body):
        m = re.match(r"\s*([A-Za-z][\w-]*)\s*=\s*(.*?)\s*$", part, re.S)
        if not m:
            continue
        name, val = m.group(1).lower(), m.group(2).strip()
        if len(val) >= 2 and val[0] == "{" and val[-1] == "}":
            val = val[1:-1]
        elif len(val) >= 2 and val[0] == '"' and val[-1] == '"':
            val = val[1:-1]
        fields[name] = val.strip()
    return fields


def parse_bibtex(text):
    entries = []
    n = len(text)
    i = 0
    while True:
        j = text.find("@", i)
        if j < 0:
            break
        m = re.match(r"@([A-Za-z]+)\s*[{(]", text[j:])
        if not m:
            i = j + 1
            continue
        etype = m.group(1).lower()
        i = j + m.end()
        if etype in ("string", "comment", "preamble"):
            continue
        depth, key_end, k = 1, None, i
        while k < n:
            ch = text[k]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
            elif ch == "," and depth == 1:
                key_end = k
                break
            k += 1
        if key_end is None:
            continue
        key = text[i:key_end].strip()
        depth, close_pos, k = 1, None, key_end + 1
        while k < n:
            ch = text[k]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    close_pos = k
                    break
            k += 1
        if close_pos is None:
            continue
        body = text[key_end + 1:close_pos]
        fields = _parse_bib_fields(body)
        fields["_type"] = etype
        fields["_key"] = key
        entries.append(fields)
        i = close_pos + 1
    return entries


RIS_TYPE_MAP = {
    "JOUR": "article", "JFULL": "article", "BOOK": "book", "CHAP": "incollection",
    "CONF": "inproceedings", "CPAPER": "inproceedings", "THES": "thesis",
    "RPRT": "report", "ELEC": "misc", "GEN": "misc", "DATA": "misc",
    "MGZN": "article", "NEWS": "article",
}


def parse_ris(text):
    records, cur = [], {}
    for line in text.splitlines():
        line = line.rstrip("\r")
        if not line.strip():
            continue
        if len(line) >= 2 and (len(line) < 6 or line[2:4] == "  "):
            tag = line[:2].strip().upper()
            val = line[6:].strip() if len(line) > 6 else ""
            if tag == "TY":
                cur = {"TY": val}
            elif tag == "ER":
                if cur:
                    records.append(cur)
                cur = {}
            else:
                cur.setdefault(tag, []).append(val)
    if cur:
        records.append(cur)
    out = []
    for rec in records:
        raw = {}
        for tag, vals in rec.items():
            if tag == "TY":
                raw["_type"] = vals if isinstance(vals, str) else (vals[0] if vals else "")
            elif isinstance(vals, list) and len(vals) == 1:
                raw[tag] = vals[0]
            elif isinstance(vals, list):
                raw[tag] = vals
        out.append(raw)
    return out


def parse_reference_json(data):
    records = []
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        for key in ("references", "items", "entries", "records", "results", "data"):
            v = data.get(key)
            if isinstance(v, list):
                records = v
                break
        else:
            records = [data]
    else:
        return []
    return [dict(r) if isinstance(r, dict) else {} for r in records]


def parse_table(text):
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        return []
    return [dict(r) for r in reader]


# --------------------------------------------------------------------------- #
# Canonical field mapping.
# --------------------------------------------------------------------------- #
FIELD_ALIASES = {
    "title": ("title", "ti", "题名", "篇名"),
    "authors": ("author", "au", "authors", "creators", "作者"),
    "year": ("year", "py", "pubyear", "date", "da", "出版年", "年"),
    "doi": ("doi", "do"),
    "journal": ("journal", "jo", "jf", "t2", "journaltitle", "刊名", "期刊"),
    "volume": ("volume", "vl", "卷"),
    "issue": ("issue", "is", "number", "num", "期"),
    "pages": ("pages", "page", "sp", "ep", "pp", "页码", "页"),
    "publisher": ("publisher", "pb", "出版社", "出版者"),
    "address": ("address", "cy", "place", "location", "出版地"),
    "school": ("school", "university", "学位授予单位"),
    "institution": ("institution", "机构"),
    "booktitle": ("booktitle", "bt", "会议名", "书名"),
    "url": ("url", "ur", "链接"),
    "arxiv_id": ("arxiv_id", "eprint", "arxiv"),
    "abstract": ("abstract", "ab", "摘要"),
    "note": ("note", "n2", "notes", "备注"),
    "edition": ("edition", "版本"),
}

ENTRY_TYPE_MAP = {
    "article": "article", "book": "book", "booklet": "book", "inbook": "book",
    "incollection": "incollection", "inproceedings": "inproceedings",
    "conference": "inproceedings", "mastersthesis": "thesis", "phdthesis": "thesis",
    "techreport": "report", "online": "misc", "electronic": "misc",
    "preprint": "misc", "unpublished": "unpublished", "misc": "misc",
    "jour": "article", "jfull": "article", "chap": "incollection",
    "conf": "inproceedings", "cpaper": "inproceedings", "thes": "thesis",
    "rprt": "report", "elec": "misc", "gen": "misc",
}


def normalize_entry(raw):
    raw = {str(k).lower(): v for k, v in (raw or {}).items() if k is not None}
    fields = {}
    for canon, aliases in FIELD_ALIASES.items():
        val = None
        for a in aliases:
            if a in raw and raw[a] not in (None, ""):
                val = raw[a]
                break
        if val is not None:
            fields[canon] = val
    fields["authors"] = parse_authors(raw.get("author") or raw.get("au") or raw.get("authors"))
    y = fields.get("year")
    if y:
        m = re.search(r"(19|20)\d{2}", str(y))
        fields["year"] = m.group(0) if m else str(y)[:4]
    else:
        fields["year"] = ""
    if not fields.get("pages"):
        sp = raw.get("sp") or raw.get("start_page")
        ep = raw.get("ep") or raw.get("end_page")
        if sp:
            fields["pages"] = f"{sp}-{ep}" if ep else str(sp)
    et = str(raw.get("_type") or raw.get("ty") or raw.get("entry_type") or raw.get("type") or "misc").lower()
    fields["_entry_type"] = ENTRY_TYPE_MAP.get(et, "misc")
    url = str(fields.get("url", "") or "").lower()
    if "arxiv.org" in url and not fields.get("arxiv_id"):
        m = re.search(r"arxiv\.org/abs/([\w.\-]+)", url)
        if m:
            fields["arxiv_id"] = m.group(1)
    return {"fields": fields, "raw": {str(k): v for k, v in (raw or {}).items()}}


def parse_library_files(paths, ledger):
    entries = []
    for p in paths:
        p = Path(p)
        text = read_text_safe(p, ledger)
        if not text.strip():
            continue
        ext = p.suffix.lower()
        try:
            if ext == ".bib":
                parsed = parse_bibtex(text)
            elif ext in (".ris", ".enw"):
                # EndNote .enw is a tagged format structurally close to RIS.
                parsed = parse_ris(text)
            elif ext == ".json":
                data = json.loads(text)
                parsed = parse_reference_json(data)
            elif ext in (".csv", ".tsv"):
                parsed = parse_table(text)
            elif ext == ".xlsx":
                ledger.degrade(f"{p.name}: .xlsx needs a runtime spreadsheet tool; "
                               "export to CSV/TSV or provide .bib/.ris/.json")
                continue
            else:
                continue
        except Exception as exc:  # noqa: BLE001
            ledger.error(f"{p.name}: library parse failed ({exc})")
            continue
        for raw in parsed:
            entry = normalize_entry(raw)
            entry["source"] = p.name
            entry["locator"] = f"{p.name}:entry@{raw.get('_key', '')}"
            entries.append(entry)
    return entries


# --------------------------------------------------------------------------- #
# Manuscript parsing: blocks -> claims with char spans and marker extraction.
# --------------------------------------------------------------------------- #
ABBREV_GUARD = [
    ("et al.", "et al\x00"),
    ("e.g.", "e.g\x00"),
    ("i.e.", "i.e\x00"),
    ("etc.", "etc\x00"),
    ("cf.", "cf\x00"),
    ("vs.", "vs\x00"),
    ("Fig.", "Fig\x00"),
    ("Eq.", "Eq\x00"),
    ("Ref.", "Ref\x00"),
    ("No.", "No\x00"),
    ("Vol.", "Vol\x00"),
]


def _protect_abbrevs(text):
    for a, b in ABBREV_GUARD:
        text = text.replace(a, b)
    return text


def _restore_abbrevs(text):
    for a, b in ABBREV_GUARD:
        text = text.replace(b, a)
    return text


def split_sentences(text):
    protected = _protect_abbrevs(text)
    parts = SENT_SPLIT_RE.split(protected)
    out = []
    for p in parts:
        p = p.strip()
        if p:
            out.append(_restore_abbrevs(p))
    return out


def classify_line(line):
    s = line.strip()
    if re.match(r"^#{1,6}\s", s):
        return "heading"
    if re.match(r"^([-*+]|\d+[.)])\s", s):
        return "list"
    if re.match(r"^(图|表|Figure|Table|Fig\.|Tab\.)\b", s, re.I):
        return "caption"
    return "prose"


def extract_markers(text):
    markers = []
    for m in NUMERIC_MARKER_RE.finditer(text):
        nums = [int(x) for x in re.findall(r"\d+", m.group(1))]
        markers.append({"raw": m.group(0), "style": "numeric", "numbers": nums,
                        "start": m.start(), "end": m.end()})
    for m in AUTHOR_YEAR_RE.finditer(text):
        inner = m.group(1)
        if any(ch.isdigit() for ch in inner):
            markers.append({"raw": m.group(0), "style": "author_year",
                            "numbers": [], "start": m.start(), "end": m.end()})
    markers.sort(key=lambda x: x["start"])
    return markers


def strip_markers(text, markers):
    if not markers:
        return re.sub(r"\s+", " ", text).strip()
    out, pos = [], 0
    for mk in markers:
        out.append(text[pos:mk["start"]])
        pos = mk["end"]
    out.append(text[pos:])
    return re.sub(r"\s+", " ", "".join(out)).strip()


def heuristic_needs_citation(text, markers):
    if markers:
        return True
    if re.search(r"\b(report(ed)?|show(n|ed)?|demonstrat(e|ed)|found|find|reveal|confirm|suggest|propose|claim|state)\b",
                 text, re.I):
        return True
    if re.search(r"(发现|表明|显示|证实|报道|认为|提出|证实|揭示)", text):
        return True
    if re.search(r"\d+(\.\d+)?\s*%", text) or re.search(r"\b\d{2,}\b", text):
        return True
    return False


def extract_claims(text):
    claims = []
    lines = text.splitlines(keepends=True)
    blocks = []
    i, offset, n = 0, 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            offset += len(line)
            i += 1
            continue
        kind = classify_line(line)
        if kind == "prose":
            block_start = offset
            buf = []
            while i < n and lines[i].strip() and classify_line(lines[i]) == "prose":
                buf.append(lines[i])
                offset += len(lines[i])
                i += 1
            block_text = "".join(buf).rstrip("\r\n")
            blocks.append((block_start, block_text, "prose"))
        else:
            # heading / list / caption: single-line block (headings are passthrough)
            block_start = offset
            block_text = line.rstrip("\r\n")
            blocks.append((block_start, block_text, kind))
            offset += len(line)
            i += 1
    cid = 0
    for block_start, block_text, kind in blocks:
        if kind == "heading":
            continue  # never a citation target; kept verbatim in the gap
        sentences = split_sentences(block_text) if kind == "prose" else [block_text]
        search_from = block_start
        for stext in sentences:
            idx = text.find(stext, search_from)
            if idx < 0:
                idx = search_from
            start, end = idx, idx + len(stext)
            markers = extract_markers(stext)
            clean = strip_markers(stext, markers)
            cid += 1
            claims.append({
                "id": f"c{cid:03d}",
                "start": start,
                "end": end,
                "kind": kind,
                "text": clean,
                "original": stext,
                "markers": markers,
                "needs_citation": heuristic_needs_citation(stext, markers),
                "decision": "pending",
                "ref_ids": [],
                "note": "",
            })
            search_from = end
    return claims


# --------------------------------------------------------------------------- #
# Deduplication (proposals only; the agent can override).
# --------------------------------------------------------------------------- #
def dedup_key(entry):
    f = entry.get("fields", {})
    doi = norm_doi(f.get("doi"))
    if doi:
        return ("doi", doi)
    ax = arxiv_id_of(entry)
    if ax:
        return ("arxiv", ax.lower())
    title = norm_title(f.get("title"))
    authors = f.get("authors") or []
    fam = authors[0].get("family", "") if authors else ""
    year = str(f.get("year", ""))[:4]
    if title and fam and year:
        return ("title_author_year", f"{title}\x1f{norm_key(fam)}\x1f{year}")
    return None


def deduplicate(entries):
    """Return (entries, merge_proposals)."""
    groups = {}
    proposals = []
    for idx, e in enumerate(entries):
        key = dedup_key(e)
        if key is None:
            key = ("unique", id(e))
        groups.setdefault(key, []).append((idx, e))
    for key, members in groups.items():
        if len(members) > 1 and key[0] != "unique":
            member_indexes = [idx for idx, _ in members]
            values = [m.get("fields", {}).get("doi") or m.get("fields", {}).get("title", "")
                      for _, m in members]
            proposals.append({
                "id": f"m{len(proposals) + 1:03d}",
                "criterion": key[0],
                "confidence": "confirmed" if key[0] in ("doi", "arxiv") else "candidate",
                "canonical_index": members[0][0],
                "member_indexes": member_indexes,
                "evidence": {"criterion": key[0], "values": values},
            })
    return entries, proposals


# --------------------------------------------------------------------------- #
# Numbering (order of first citation) and marker compression.
# --------------------------------------------------------------------------- #
def compress_numbers(nums):
    nums = sorted(set(int(n) for n in nums))
    if not nums:
        return ""
    runs = []
    start = prev = nums[0]
    for x in nums[1:]:
        if x == prev + 1:
            prev = x
        else:
            runs.append((start, prev))
            start = prev = x
    runs.append((start, prev))
    parts = []
    for s, e in runs:
        if s == e:
            parts.append(str(s))
        elif e == s + 1:
            parts.append(f"{s},{e}")
        else:
            parts.append(f"{s}-{e}")
    return ",".join(parts)


def compute_numbering(citations, claim_order):
    order, seen = [], {}
    for claim_id in claim_order:
        for cit in citations:
            if cit.get("claim_id") == claim_id:
                for rid in cit.get("ref_ids", []):
                    if rid not in seen:
                        seen[rid] = len(order) + 1
                        order.append(rid)
    return {"order": order, "id_to_number": seen, "algorithm": "order_of_first_citation"}


# --------------------------------------------------------------------------- #
# Style rendering (best-effort mapping; never fabricates missing fields).
# --------------------------------------------------------------------------- #
def _clean(s):
    s = str(s or "").strip()
    s = s.replace("--", "-").replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", " ", s).strip()


def render_entry_gbt(ref):
    f = ref.get("fields", {})
    etype = f.get("_entry_type", "misc")
    auth = format_authors_gbt(f.get("authors"))
    title = _clean(f.get("title"))
    year = _clean(f.get("year"))
    parts = []
    if auth:
        parts.append(auth + ("" if auth.rstrip().endswith(".") else "."))
    if title:
        parts.append(title)
    if etype == "article":
        if title:
            parts[-1] += "[J]"
        if f.get("journal"):
            parts.append(_clean(f.get("journal")))
        if year:
            parts.append(year)
        vol, iss, pages = _clean(f.get("volume")), _clean(f.get("issue")), _clean(f.get("pages"))
        voliss = vol + (f"({iss})" if iss else "") if vol else ""
        if voliss:
            parts.append(voliss)
        if pages:
            parts.append(": " + pages)
    elif etype == "inproceedings":
        if title:
            parts[-1] += "[C]"
        if f.get("booktitle"):
            parts.append("//" + _clean(f.get("booktitle")))
        if f.get("address"):
            parts.append(_clean(f.get("address")))
        if f.get("publisher"):
            parts.append(_clean(f.get("publisher")))
        if year:
            parts.append(year)
        if f.get("pages"):
            parts.append(": " + _clean(f.get("pages")))
    elif etype == "incollection":
        if title:
            parts[-1] += "[M]"
        if f.get("booktitle"):
            parts.append("//" + _clean(f.get("booktitle")))
        if f.get("address"):
            parts.append(_clean(f.get("address")))
        if f.get("publisher"):
            parts.append(_clean(f.get("publisher")))
        if year:
            parts.append(year)
        if f.get("pages"):
            parts.append(": " + _clean(f.get("pages")))
    elif etype == "book":
        if title:
            parts[-1] += "[M]"
        if f.get("edition"):
            parts.append(_clean(f.get("edition")))
        if f.get("address"):
            parts.append(_clean(f.get("address")))
        if f.get("publisher"):
            parts.append(_clean(f.get("publisher")))
        if year:
            parts.append(year)
    elif etype == "thesis":
        if title:
            parts[-1] += "[D]"
        if f.get("address"):
            parts.append(_clean(f.get("address")))
        if f.get("school"):
            parts.append(_clean(f.get("school")))
        if year:
            parts.append(year)
    elif etype == "report":
        if title:
            parts[-1] += "[R]"
        if f.get("address"):
            parts.append(_clean(f.get("address")))
        if f.get("institution"):
            parts.append(_clean(f.get("institution")))
        if year:
            parts.append(year)
    elif etype == "unpublished":
        if title:
            parts[-1] += "[Z]"
        if year:
            parts.append(year)
    else:
        if title:
            parts[-1] += "[EB/OL]"
        if year:
            parts.append(f"({year})")
        if f.get("url"):
            parts.append(_clean(f.get("url")))
    text = " ".join(x for x in parts if x)
    text = text.replace(" :", ":")
    text = text.rstrip(":. ")
    doi = _clean(f.get("doi"))
    if doi:
        text += f". DOI: {doi}"
    return text + "."


def render_entry_apa(ref):
    f = ref.get("fields", {})
    auth = format_authors_apa(f.get("authors"))
    year = _clean(f.get("year"))
    title = _clean(f.get("title"))
    head = ""
    if auth:
        head = auth + (f" ({year}). " if year else ". ")
    elif year:
        head = f"({year}). "
    out = head + title
    etype = f.get("_entry_type", "misc")
    if etype == "article":
        j = _clean(f.get("journal"))
        if j:
            out += ". " + j
        vol, iss, pages = _clean(f.get("volume")), _clean(f.get("issue")), _clean(f.get("pages"))
        if vol:
            out += ", " + vol + (f"({iss})" if iss else "")
        if pages:
            out += ", " + pages
    elif etype in ("book", "incollection", "inproceedings", "thesis", "report"):
        pub = _clean(f.get("publisher")) or _clean(f.get("school")) or _clean(f.get("institution"))
        if pub:
            out += ". " + pub
    doi = _clean(f.get("doi"))
    if doi:
        out += ". https://doi.org/" + doi
    elif f.get("url"):
        out += ". " + _clean(f.get("url"))
    return out + "."


def render_reference_list(references, numbering, style):
    if style == "apa":
        lines = [render_entry_apa(r) for r in references if r.get("status") != "duplicate"]
        return "\n\n".join(sorted(lines))
    id_to_number = numbering.get("id_to_number", {})
    cited, uncited = [], []
    for r in references:
        if r.get("status") == "duplicate":
            continue
        if r["id"] in id_to_number:
            cited.append(r)
        else:
            uncited.append(r)
    cited.sort(key=lambda r: id_to_number[r["id"]])
    lines = [f"[{id_to_number[r['id']]}] " + render_entry_gbt(r) for r in cited]
    if uncited:
        lines.append("")
        lines.append("## 未引用条目（保留，未编号）")
        lines.extend("- " + render_entry_gbt(r) for r in uncited)
    return "\n\n".join(lines)


# --------------------------------------------------------------------------- #
# Manuscript rendering (in-place, single marker per claim at sentence end).
# --------------------------------------------------------------------------- #
def _insert_marker_before_terminal(text, marker):
    m = re.search(r"([。！？.!?]+)\s*$", text)
    if m:
        head = text[:m.start()].rstrip()
        sep = "" if (head and is_cjk(head[-1])) else " "
        return head + sep + marker + text[m.start():]
    head = text.rstrip()
    sep = "" if (head and is_cjk(head[-1])) else " "
    return head + sep + marker


def render_claim(claim, numbering, citations_by_claim):
    ref_ids = citations_by_claim.get(claim["id"], [])
    if not ref_ids:
        # No semantic decision yet: preserve the original sentence verbatim
        # (including any unresolved markers) rather than silently dropping them.
        return claim.get("original", claim["text"])
    text = claim["text"]
    id_to_number = numbering.get("id_to_number", {})
    nums = [id_to_number[r] for r in ref_ids if r in id_to_number]
    marker = "[" + compress_numbers(nums) + "]" if nums else "[?]"
    return _insert_marker_before_terminal(text, marker)


def render_manuscript(original_text, claims, numbering, citations_by_claim, style):
    ordered = sorted(claims, key=lambda c: c["start"])
    out, pos = [], 0
    for c in ordered:
        out.append(original_text[pos:c["start"]])
        out.append(render_claim(c, numbering, citations_by_claim))
        pos = c["end"]
    out.append(original_text[pos:])
    return "".join(out)


# --------------------------------------------------------------------------- #
# Consistency check.
# --------------------------------------------------------------------------- #
def run_consistency(model):
    claims = {c["id"]: c for c in model.get("claims", [])}
    refs = {r["id"]: r for r in model.get("references", [])}
    citations = model.get("citations", [])
    issues = {
        "unresolved_citations": [],
        "uncited_references": [],
        "pending_claims": [],
        "missing_citation_claims": [],
        "numbering_gaps": [],
        "duplicate_still_cited": [],
        "field_missing": [],
        "unconfirmed_merges": [],
    }
    valid_ids = set(refs)
    cited_ids = set()
    for cit in citations:
        for rid in cit.get("ref_ids", []):
            cited_ids.add(rid)
            if rid not in valid_ids:
                issues["unresolved_citations"].append({"claim_id": cit.get("claim_id"), "ref_id": rid})
    for cid, c in claims.items():
        if c.get("decision") == "pending":
            issues["pending_claims"].append(cid)
        if c.get("needs_citation") and not c.get("ref_ids") and c.get("decision") != "not_needed":
            issues["missing_citation_claims"].append(cid)
    id_to_number = model.get("numbering", {}).get("id_to_number", {})
    nums = sorted(id_to_number.values())
    for a, b in zip(nums, nums[1:]):
        if b != a + 1:
            issues["numbering_gaps"].append([a, b])
    for rid, r in refs.items():
        if r.get("status") == "duplicate" and rid in cited_ids:
            issues["duplicate_still_cited"].append(rid)
        if rid not in cited_ids and r.get("status") != "duplicate":
            issues["uncited_references"].append(rid)
        f = r.get("fields", {})
        missing = [k for k in ("title", "year") if not _clean(f.get(k))]
        if missing:
            issues["field_missing"].append({"ref_id": rid, "missing": missing})
    for prop in model.get("merge_proposals", []):
        if prop.get("confidence") == "candidate":
            issues["unconfirmed_merges"].append({
                "id": prop.get("id", ""),
                "criterion": prop.get("criterion", ""),
                "member_indexes": prop.get("member_indexes", []),
            })
    total = sum(len(v) for v in issues.values())
    issues["issue_count"] = total
    issues["clean"] = total == 0
    return issues


# --------------------------------------------------------------------------- #
# IR construction and agent-edit overlay merge.
# --------------------------------------------------------------------------- #
def assign_ref_ids(entries):
    refs = []
    for idx, e in enumerate(entries):
        refs.append({
            "id": f"r{idx + 1:03d}",
            "source": e.get("source", ""),
            "locator": e.get("locator", ""),
            "key": e.get("raw", {}).get("_key", ""),
            "entry_type": e.get("fields", {}).get("_entry_type", "misc"),
            "fields": e.get("fields", {}),
            "status": "canonical",
            "canonical_of": [],
            "duplicate_of": None,
            "merge_evidence": None,
            "agent_note": "",
        })
    return refs


def apply_merge_proposals(model):
    refs = model["references"]
    by_index = {i: refs[i]["id"] for i in range(len(refs))}
    for prop in model.get("merge_proposals", []):
        # Only auto-apply high-confidence merges (DOI / arXiv).  Title+author+year
        # matches are left as *candidates* because same-name/different-work
        # collisions are a documented failure mode; the agent confirms them.
        if prop.get("confidence", "confirmed") != "confirmed":
            continue
        canon_id = by_index.get(prop["canonical_index"])
        members = [by_index.get(i) for i in prop["member_indexes"] if i in by_index]
        if not canon_id or not members:
            continue
        canon = next(r for r in refs if r["id"] == canon_id)
        canon["status"] = "canonical"
        canon["canonical_of"] = members
        canon["merge_evidence"] = prop
        for mid in members:
            if mid == canon_id:
                continue
            r = next((x for x in refs if x["id"] == mid), None)
            if r:
                r["status"] = "duplicate"
                r["duplicate_of"] = canon_id


def resolve_duplicate_citations(model):
    """Remap any citation pointing at a duplicate entry to its canonical entry.

    Keeps the numbering/reference list consistent when the agent (or an old
    marker) references a merged entry; resolves chains transitively.
    """
    refs = {r["id"]: r for r in model.get("references", [])}

    def canonical(rid):
        seen = set()
        while (rid in refs and refs[rid].get("status") == "duplicate"
               and refs[rid].get("duplicate_of") and rid not in seen):
            seen.add(rid)
            rid = refs[rid]["duplicate_of"]
        return rid

    for cit in model.get("citations", []):
        out = []
        for rid in cit.get("ref_ids", []):
            c = canonical(rid)
            if c not in out:
                out.append(c)
        cit["ref_ids"] = out
    for c in model.get("claims", []):
        out = []
        for rid in c.get("ref_ids", []):
            cc = canonical(rid)
            if cc not in out:
                out.append(cc)
        c["ref_ids"] = out
    return model


def merge_agent_edits(model, edits):
    edits = edits or {}
    refs = {r["id"]: r for r in model["references"]}
    overrides = edits.get("merge_overrides", {}) or {}
    for pair in overrides.get("merge", []):
        if len(pair) >= 2 and all(p in refs for p in pair):
            canon_id = pair[0]
            refs[canon_id]["status"] = "canonical"
            for mid in pair[1:]:
                refs[mid]["status"] = "duplicate"
                refs[mid]["duplicate_of"] = canon_id
                if mid not in refs[canon_id]["canonical_of"]:
                    refs[canon_id]["canonical_of"].append(mid)
    for pair in overrides.get("split", []):
        pair_set = set(pair)
        for mid in pair:
            if mid in refs:
                refs[mid]["status"] = "canonical"
                refs[mid]["duplicate_of"] = None
                refs[mid]["merge_evidence"] = None
        for r in refs.values():
            if r.get("canonical_of"):
                r["canonical_of"] = [x for x in r["canonical_of"] if x not in pair_set]
    for rid, note in (edits.get("reference_notes") or {}).items():
        if rid in refs:
            refs[rid]["agent_note"] = str(note)
    claims = {c["id"]: c for c in model["claims"]}
    for cid, d in (edits.get("claims") or {}).items():
        if cid in claims and isinstance(d, dict):
            claims[cid]["decision"] = d.get("decision", "matched")
            claims[cid]["ref_ids"] = d.get("ref_ids", [])
            if d.get("note"):
                claims[cid]["note"] = str(d["note"])
    model["citations"] = []
    for cit in edits.get("citations", []):
        cid = cit.get("claim_id")
        rids = cit.get("ref_ids", [])
        # Resolve duplicates to their canonical entry so a citation never
        # points at an entry that the reference list will omit.
        resolved = []
        for rid in rids:
            target = rid
            seen_chain = set()
            while refs.get(target, {}).get("duplicate_of") and target not in seen_chain:
                seen_chain.add(target)
                target = refs[target]["duplicate_of"]
            resolved.append(target)
        if cid and resolved:
            model["citations"].append({
                "id": f"x{len(model['citations']) + 1:03d}",
                "claim_id": cid,
                "ref_ids": resolved,
                "match_basis": cit.get("match_basis", ""),
                "confidence": cit.get("confidence", "medium"),
                "position": cit.get("position", "sentence_end"),
                "style": cit.get("style", model.get("target_style", DEFAULT_STYLE)),
            })
            if cid in claims:
                claims[cid]["ref_ids"] = resolved
                if claims[cid]["decision"] == "pending":
                    claims[cid]["decision"] = "matched"
    model["evidence"] = []
    for ev in edits.get("evidence", []):
        model["evidence"].append({
            "id": f"e{len(model['evidence']) + 1:03d}",
            "claim_id": ev.get("claim_id", ""),
            "ref_id": ev.get("ref_id", ""),
            "locator": ev.get("locator", ""),
            "quote": ev.get("quote", ""),
            "match_basis": ev.get("match_basis", ""),
            "status": ev.get("status", "not_verified"),
        })
    model["conflicts"] = edits.get("conflicts", [])
    model["pending"] = edits.get("pending", [])
    model["agent_notes"] = edits.get("notes", "")
    model["semantic_verified"] = bool(edits.get("semantic_verified", False))
    return model
