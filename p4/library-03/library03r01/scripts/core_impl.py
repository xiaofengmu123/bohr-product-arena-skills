#!/usr/bin/env python3
"""core_impl.py — sci-library-organizer 核心实现（仅标准库）。

职责：
  1. 盘点输入目录：文件、大小、权限、SHA-256、类型、编码与文本。
  2. 抽取 PDF 文本层（pdftotext/mutool/gs；不可用则如实降级）与 Office 文本。
  3. 依据「文件名/文本/BibTeX/PDF 元数据」做分类、元数据、去重、版本分组、命名。
  4. 生成细粒度 evidence_map：每条结论都带 locator + 逐字 quote。
  5. 规划并（可选）落盘 /app/output/library/，写回滚清单。

设计取舍（本版：证据溯源强化）：
  - 所有结论先写 claim，再反向挂到 evidence_map[source][field]；每条 quote 非空，
    否则置信度降为 unverified，绝不虚构。
  - 冲突不隐藏：同名字段两处来源不一致时，两条 evidence 都保留。
  - 确定性：所有遍历、分组、命名均按字典序排序，同一输入重跑结果一致。
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import unicodedata
import zipfile

__version__ = "2.0.0"

DEFAULT_CONTRACT = {
    "library": {}, "evidence": {}, "operations": [], "failures": {},
}
EXTRAS = ("status", "sources", "uncertainty", "change_log",
          "errors", "degradations", "pending_human", "runtime")
STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")

TEXT_EXT = {".txt", ".md", ".markdown", ".bib", ".ris", ".enw", ".json",
            ".csv", ".tsv", ".xml", ".yaml", ".yml", ".tex", ".html", ".htm"}
PDF_EXT = {".pdf"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
OFFICE_EXT = {".docx", ".xlsx", ".pptx"}
ARCHIVE_EXT = {".zip", ".tar", ".gz", ".tgz"}
BIB_EXT = {".bib", ".ris", ".enw"}
DATA_EXT = {".csv", ".tsv", ".xlsx"}

MAX_TEXT_BYTES = 2 * 1024 * 1024

TAG_VOCAB = [
    ("machine learning", "machine_learning"),
    ("deep learning", "deep_learning"),
    ("neural network", "neural_network"),
    ("reinforcement learning", "reinforcement_learning"),
    ("natural language", "nlp"),
    ("computer vision", "computer_vision"),
    ("large language model", "large_language_model"),
    ("generative", "generative_model"),
    ("transformer", "transformer"),
    ("genomics", "genomics"),
    ("transcriptomic", "transcriptomics"),
    ("proteomic", "proteomics"),
    ("single-cell", "single_cell"),
    ("crystallography", "crystallography"),
    ("spectroscopy", "spectroscopy"),
    ("synthesis", "synthesis"),
    ("catalysis", "catalysis"),
    ("electrochem", "electrochemistry"),
    ("battery", "battery"),
    ("photovolt", "photovoltaics"),
    ("microscopy", "microscopy"),
    ("climate", "climate_science"),
    ("ocean", "oceanography"),
    ("epidemiolog", "epidemiology"),
    ("clinical trial", "clinical_trial"),
    ("randomized", "randomized_trial"),
    ("bayesian", "bayesian"),
    ("optimization", "optimization"),
    ("simulation", "simulation"),
    ("quantum", "quantum"),
]
TAG_SYNONYMS = {"ml": "machine_learning", "ai": "artificial_intelligence",
                "nn": "neural_network", "llm": "large_language_model",
                "cv": "computer_vision"}

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
ARXIV_RE = re.compile(r"(?:arXiv\s*:?\s*|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5})(?:v\d+)?",
                      re.IGNORECASE)
YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-2]\d)\b")


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_bytes(path: str, max_bytes: int = None) -> bytes:
    with open(path, "rb") as f:
        return f.read() if max_bytes is None else f.read(max_bytes)


def decode_text(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def safe_read_text(path: str, max_bytes: int = MAX_TEXT_BYTES) -> tuple:
    try:
        raw = read_bytes(path, max_bytes + 1)
        truncated = len(raw) > max_bytes
        raw = raw[:max_bytes]
        text = decode_text(raw)
        enc = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "auto"
        return text, enc, truncated, None
    except OSError as e:
        return "", None, False, str(e)


def read_json(path: str):
    try:
        raw = read_bytes(path)
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        return json.loads(decode_text(raw))
    except Exception:
        return None


def write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def rel_path(path: str, base: str) -> str:
    try:
        return os.path.relpath(path, base).replace(os.sep, "/")
    except ValueError:
        return path.replace(os.sep, "/")


def sanitize_name(s: str, max_bytes: int = 180) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"[\x00-\x1f<>:\"/\\|?*]", "_", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    s = s or "unnamed"
    b = s.encode("utf-8")
    if len(b) > max_bytes:
        s = b[:max_bytes].decode("utf-8", errors="ignore").rstrip(" .")
        s = s or "unnamed"
    return s


def short_title(title: str, words: int = 10) -> str:
    if not title:
        return ""
    parts = re.split(r"[\s:：\-–—,，]+", title.strip())
    return "_".join([p for p in parts if p][:words])


def normalize_key(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def magic_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    try:
        head = read_bytes(path, 16)
    except OSError:
        head = b""
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"PK\x03\x04"):
        if ext in OFFICE_EXT:
            return "office/" + ext[1:]
        return "zip_archive"
    if head.startswith(b"\x7fELF"):
        return "binary/elf"
    if ext in PDF_EXT:
        return "pdf"
    if ext in IMAGE_EXT:
        return "image"
    if ext in TEXT_EXT:
        return "text"
    if ext in OFFICE_EXT:
        return "office/" + ext[1:]
    if ext in ARCHIVE_EXT:
        return "archive"
    return "other"


def permission_snapshot(path: str) -> dict:
    snap = {"readable": os.access(path, os.R_OK),
            "writable": os.access(path, os.W_OK), "stat_ok": True}
    try:
        st = os.stat(path)
        snap["mode"] = oct(st.st_mode & 0o777)
        snap["size"] = st.st_size
        snap["mtime"] = datetime.datetime.fromtimestamp(
            st.st_mtime, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except OSError as e:
        snap["stat_ok"] = False
        snap["error"] = str(e)
    return snap


def discover_files(input_dir: str):
    out = []
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for fn in sorted(files):
            full = os.path.join(root, fn)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            out.append(full)
    return sorted(out)


def _run(cmd, timeout=60):
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=timeout)
        return p.stdout if p.returncode == 0 else b""
    except Exception:
        return b""


def extract_pdf_text(path: str):
    for tool, cmd in (
        ("pdftotext", ["pdftotext", "-enc", "UTF-8", "-q", path, "-"]),
        ("mutool", ["mutool", "draw", "-F", "txt", "-o", "-", path]),
        ("gs", ["gs", "-q", "-sDEVICE=txtwrite", "-o", "-", path]),
    ):
        out = _run(cmd)
        if out:
            return decode_text(out), tool, True
    return "", None, False


def extract_pdf_meta_raw(path: str) -> dict:
    try:
        raw = read_bytes(path, MAX_TEXT_BYTES)
    except OSError:
        return {}
    meta = {}
    for key, pat in (
        ("title", rb"/Title\s*\(([^)]*)\)"),
        ("author", rb"/Author\s*\(([^)]*)\)"),
        ("creation_date", rb"/CreationDate\s*\(([^)]*)\)"),
        ("subject", rb"/Subject\s*\(([^)]*)\)"),
    ):
        m = re.search(pat, raw)
        if m:
            try:
                meta[key] = decode_text(m.group(1)).strip()
            except Exception:
                pass
    head_txt = decode_text(raw[:20000])
    m = DOI_RE.search(head_txt)
    if m:
        meta["doi"] = m.group(0).rstrip(".,;")
    m = ARXIV_RE.search(head_txt)
    if m:
        meta["arxiv_id"] = m.group(1)
    return meta


def extract_office_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    target = None
    if ext == ".docx":
        target = "word/document.xml"
    elif ext == ".xlsx":
        target = "xl/sharedStrings.xml"
    elif ext == ".pptx":
        return ""
    try:
        with zipfile.ZipFile(path) as z:
            if target and target in z.namelist():
                xml = z.read(target).decode("utf-8", errors="replace")
            else:
                return ""
        texts = re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml) if ext == ".docx" else \
            re.findall(r"<t[^>]*>([^<]*)</t>", xml)
        return " ".join(texts)[:MAX_TEXT_BYTES]
    except Exception:
        return ""


def parse_bibtex(text: str):
    entries = []
    text = re.sub(r"@comment\{[^}]*\}", " ", text, flags=re.I)
    for m in re.finditer(
            r"@\s*([A-Za-z]+)\s*\{\s*([^,]+)\s*,\s*(.*?)\}\s*$",
            text, re.M | re.S):
        etype = m.group(1).lower()
        key = m.group(2).strip()
        body = m.group(3)
        fields = {}
        for fm in re.finditer(r"([A-Za-z]+)\s*=\s*[{\"]([^}{\"]*)[}\"]", body):
            fields[fm.group(1).lower()] = fm.group(2).strip()
        if key or fields:
            entries.append({"type": etype, "key": key, "fields": fields})
    return entries


def bibtex_to_metadata(entry: dict):
    f = entry.get("fields", {})
    etype = entry.get("type", "")
    authors = [a.strip() for a in re.split(r"\s+and\s+", f.get("author", ""), flags=re.I)
               if a.strip()]
    doc_type = {
        "article": "journal_article", "inproceedings": "conference_paper",
        "conference": "conference_paper", "phdthesis": "thesis",
        "mastersthesis": "thesis", "thesis": "thesis", "incollection": "book_chapter",
        "book": "book", "unpublished": "preprint", "misc": "other",
    }.get(etype, "other")
    arx = ARXIV_RE.search(f.get("eprint", "") or "")
    return {
        "title": f.get("title"), "authors": authors, "year": f.get("year"),
        "journal": f.get("journal") or f.get("booktitle"), "doi": f.get("doi"),
        "arxiv_id": arx.group(1) if arx else None,
        "document_type": doc_type, "note": f.get("note"),
    }


class Evidence:
    def __init__(self):
        self.map = {}

    def add(self, source, field, value, locator, quote, confidence="medium"):
        quote = (quote or "").strip()
        if not quote:
            confidence = "unverified"
        e = self.map.setdefault(source, {})
        rec = e.get(field)
        if rec is None:
            rec = {"value": value, "confidence": confidence, "evidence": []}
            e[field] = rec
        if rec["value"] is None and value is not None:
            rec["value"] = value
        if quote and not any(x.get("quote") == quote for x in rec["evidence"]):
            rec["evidence"].append({"locator": locator, "quote": quote[:500]})
        order = {"unverified": 0, "low": 1, "medium": 2, "high": 3}
        if order.get(confidence, 1) > order.get(rec["confidence"], 1):
            rec["confidence"] = confidence
        return rec


def first_author_lastname(authors):
    if not authors:
        return None
    a = authors[0].strip()
    if "," in a:
        return a.split(",", 1)[0].strip()
    parts = a.split()
    return parts[-1] if parts else a


def analyze_file(src_rel, full_path, text, bib_entries, ev: Evidence, catalog_entry):
    loc = src_rel
    meta = catalog_entry.setdefault("metadata", {})
    low = text[:4000].lower()
    title = None
    authors = None
    year = None
    journal = None
    doi = None
    arxiv_id = None
    doc_type = None
    tags = []
    ext = os.path.splitext(src_rel)[1].lower()
    is_bib = ext in BIB_EXT

    if bib_entries and not is_bib:
        bm = bibtex_to_metadata(bib_entries[0])
        for k in ("title", "journal", "doi"):
            if bm.get(k) and not meta.get(k):
                meta[k] = bm[k]
                ev.add(src_rel, k, bm[k], loc + "#bibtex", str(bm[k]), "high")
        if bm.get("authors"):
            meta["authors"] = bm["authors"]
            ev.add(src_rel, "authors", bm["authors"], loc + "#bibtex",
                   "; ".join(bm["authors"]), "high")
        if bm.get("year"):
            meta["year"] = bm["year"]
            ev.add(src_rel, "year", bm["year"], loc + "#bibtex", str(bm["year"]), "high")
        if bm.get("arxiv_id"):
            meta["arxiv_id"] = bm["arxiv_id"]
            ev.add(src_rel, "arxiv_id", bm["arxiv_id"], loc + "#bibtex", str(bm["arxiv_id"]), "high")
        if bm.get("document_type"):
            doc_type = bm["document_type"]
            ev.add(src_rel, "document_type", doc_type, loc + "#bibtex",
                   bib_entries[0].get("type", ""), "high")

    if catalog_entry.get("file_type") == "pdf":
        pmeta = extract_pdf_meta_raw(full_path)
        if pmeta.get("title") and not meta.get("title"):
            meta["title"] = pmeta["title"]
            ev.add(src_rel, "title", pmeta["title"], loc + "#pdfmeta", pmeta["title"], "medium")
        if pmeta.get("author") and not meta.get("authors"):
            meta["authors"] = [pmeta["author"]]
            ev.add(src_rel, "authors", meta["authors"], loc + "#pdfmeta", pmeta["author"], "medium")
        if pmeta.get("doi") and not meta.get("doi"):
            meta["doi"] = pmeta["doi"]
            ev.add(src_rel, "doi", pmeta["doi"], loc + "#pdfmeta", pmeta["doi"], "high")
        if pmeta.get("arxiv_id") and not meta.get("arxiv_id"):
            meta["arxiv_id"] = pmeta["arxiv_id"]
            ev.add(src_rel, "arxiv_id", pmeta["arxiv_id"], loc + "#pdfmeta",
                   pmeta["arxiv_id"], "high")

    title = meta.get("title")
    authors = meta.get("authors") or []
    year = meta.get("year")
    journal = meta.get("journal")
    doi = meta.get("doi")
    arxiv_id = meta.get("arxiv_id")

    m = DOI_RE.search(text)
    if m and not doi:
        doi = m.group(0).rstrip(".,;")
        meta["doi"] = doi
        ev.add(src_rel, "doi", doi, loc + "#text", doi, "high")
    m = ARXIV_RE.search(text)
    if m and not arxiv_id:
        arxiv_id = m.group(1)
        meta["arxiv_id"] = arxiv_id
        ev.add(src_rel, "arxiv_id", arxiv_id, loc + "#text", m.group(0), "high")
    m = YEAR_RE.search(text[:3000])
    if m and not year:
        year = m.group(1)
        meta["year"] = year
        ev.add(src_rel, "year", year, loc + "#text", m.group(0), "medium")

    if not title and not is_bib:
        base = os.path.basename(src_rel)
        stem = os.path.splitext(base)[0]
        stem2 = re.sub(r"^[0-9A-Za-z]*-s2\.0-", "", stem)
        title = re.sub(r"[_\-]+", " ", stem2).strip()
        if title:
            meta["title"] = title
            ev.add(src_rel, "title", title, loc + "#filename", stem, "low")

    lowtext = text[:6000].lower()
    if doc_type is None:
        if re.search(r"\barxiv\b|preprint|manuscript|not peer.?reviewed", lowtext):
            doc_type = "preprint"
            m2 = re.search(r"\barxiv\b|preprint|manuscript", lowtext)
            ev.add(src_rel, "document_type", doc_type, loc + "#text", m2.group(0), "high")
        elif re.search(r"supplementary|supporting information|\bsi\b|appendix", lowtext):
            doc_type = "supplementary"
            m2 = re.search(r"supplementary|supporting information|\bsi\b|appendix", lowtext)
            ev.add(src_rel, "document_type", doc_type, loc + "#text", m2.group(0), "medium")
        elif re.search(r"\bthesis\b|dissertation|ph\.?d", lowtext):
            doc_type = "thesis"
            m2 = re.search(r"\bthesis\b|dissertation", lowtext)
            ev.add(src_rel, "document_type", doc_type, loc + "#text", m2.group(0), "high")
        elif re.search(r"proceedings|conference|workshop|symposium", lowtext):
            doc_type = "conference_paper"
            m2 = re.search(r"proceedings|conference|workshop|symposium", lowtext)
            ev.add(src_rel, "document_type", doc_type, loc + "#text", m2.group(0), "medium")
        elif journal:
            doc_type = "journal_article"
            ev.add(src_rel, "document_type", doc_type, loc + "#metadata", str(journal), "medium")
        elif catalog_entry.get("file_type") == "pdf":
            doc_type = "journal_article" if doi else "other"
            ev.add(src_rel, "document_type", doc_type, loc + "#heuristic",
                   "doi present" if doi else "no strong signal", "low")
        else:
            doc_type = "other"

    if doc_type in (None, "other"):
        if ext in BIB_EXT:
            doc_type = "bibliography"
            ev.add(src_rel, "document_type", doc_type, loc + "#filename", src_rel, "high")
        elif ext in DATA_EXT:
            doc_type = "dataset"
            ev.add(src_rel, "document_type", doc_type, loc + "#filename", src_rel, "high")
        elif ext in (".md",):
            doc_type = "notes"
            ev.add(src_rel, "document_type", doc_type, loc + "#filename", src_rel, "medium")

    meta["document_type"] = doc_type

    for kw, tag in TAG_VOCAB:
        if len(tags) >= 8:
            break
        pos = low.find(kw)
        if pos >= 0:
            snippet = text[max(0, pos - 20):pos + len(kw) + 20]
            if tag not in tags:
                tags.append(tag)
                ev.add(src_rel, "label:" + tag, tag, loc + "#text", snippet.strip(), "medium")
    merged = [TAG_SYNONYMS.get(t, t) for t in tags]
    tags = sorted(set(merged))

    surname = first_author_lastname(authors)
    surname = surname or "unknown_author"
    yy = year or "nodate"
    st = short_title(title, 8) or "untitled"
    stem = sanitize_name(f"{surname}{yy}_{st}", max_bytes=160)
    ext2 = os.path.splitext(src_rel)[1].lower() or ""
    suggested = stem + ext2

    catalog_entry.update({
        "document_type": doc_type, "labels": tags, "suggested_name": suggested,
        "category_path": doc_type, "version_role": "unknown",
        "duplicate_of": None, "version_group": None,
    })
    return meta, tags, suggested, doc_type


class UnionFind:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def group_versions(catalog):
    uf = UnionFind()
    for src in catalog:
        uf.find(src)
    keys = {}
    for src, e in catalog.items():
        m = e.get("metadata", {})
        doi = (m.get("doi") or "").strip().lower()
        arx = (m.get("arxiv_id") or "").strip().lower()
        nk = normalize_key(m.get("title") or "")
        yr = (m.get("year") or "").strip()
        for k in ("doi:" + doi, "arxiv:" + arx, f"t:{nk}#{yr}"):
            if k.split(":", 1)[1].replace("#", "").strip():
                if k in keys:
                    uf.union(src, keys[k])
                else:
                    keys[k] = src
    groups = {}
    for src in catalog:
        root = uf.find(src)
        groups.setdefault(root, []).append(src)
    out = []
    for i, members in enumerate(sorted(groups.values(), key=lambda x: sorted(x))):
        members = sorted(members)
        gid = "vg-%03d" % (i + 1)
        basis = []
        for m in members:
            mm = catalog[m].get("metadata", {})
            if mm.get("doi"):
                basis.append("doi:" + mm["doi"])
            if mm.get("arxiv_id"):
                basis.append("arxiv:" + mm["arxiv_id"])
        out.append({"group_id": gid, "members": members, "basis": sorted(set(basis))})
    by_src = {}
    for g in out:
        for m in g["members"]:
            by_src[m] = g["group_id"]
    for src, e in catalog.items():
        e["version_group"] = by_src.get(src)
    return out


def decide_status(catalog, failures, degradations, pending, has_input_files):
    if not has_input_files:
        return "insufficient_evidence"
    if failures.get("permission_errors") or failures.get("skipped"):
        return "partial"
    if pending:
        return "partial"
    if degradations:
        return "partial"
    for e in catalog.values():
        m = e.get("metadata", {})
        if any(m.get(k) in (None, "") for k in ("title", "year", "document_type")):
            return "partial"
    return "ok"


def deep_placeholder(v):
    if isinstance(v, dict):
        return {k: deep_placeholder(x) for k, x in v.items()}
    if isinstance(v, list):
        return []
    return None


def empty_data():
    return {
        "library": {"catalog": [], "hierarchy": {"dimensions": [], "tree": []},
                    "metadata": {}, "versions": []},
        "evidence": {"hashes": {}, "permissions": {}, "locators": [], "evidence_map": {}},
        "operations": [],
        "failures": {"skipped": [], "conflicts": [], "permission_errors": [],
                     "duplicates": [], "recoveries": []},
        "status": "partial", "sources": [],
        "uncertainty": {"unclassified": 0, "missing_fields": 0,
                        "no_text_layer": 0, "source_conflicts": 0},
        "change_log": [], "errors": [], "degradations": [], "pending_human": [],
        "runtime": {},
    }


def build_tree(catalog):
    tree = {}
    for src in sorted(catalog):
        e = catalog[src]
        path = (e.get("category_path") or "other").split("/")
        node = tree
        for part in path:
            node = node.setdefault(part, {})
        node.setdefault("_files", []).append(src)
    return tree
