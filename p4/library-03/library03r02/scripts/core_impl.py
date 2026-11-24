#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — sci-library-organizer 的核心实现（纯标准库）。

被 main.py 分阶段调用：scan_inventory → analyze → plan_operations / apply_operations。
设计原则（与 references/rules.md 一致）：
  1. 先发现再假设：真实遍历输入目录、排序保证确定性，不臆测文件名/键名/元数据；
  2. 证据优先：每个元数据字段带 locator + 逐字 quote；缺证据写 not_reported；
  3. 非破坏：只读 /input；复制件只落 /app/output（可回滚），绝不修改 /input；
  4. 字节级重复用 SHA-256；版本组用 DOI/arXiv/归一化标题+年份聚合。
仅依赖 Python 3.8+ 标准库。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict

assert sys.version_info >= (3, 8), "需要 Python >= 3.8"

MAX_TEXT_BYTES = 2_000_000
NOT_REPORTED = "not_reported"
SPEC_NAMES = {"case.json", "task.md"}

TEXT_EXTS = {
    "txt", "md", "rst", "csv", "tsv", "json", "bib", "ris", "enw",
    "xml", "yaml", "yml", "log", "tex", "html", "htm", "ini", "cfg",
}

TOPIC_VOCAB = {
    "machine_learning": ("deep learning", "neural network", "reinforcement learning",
                         "transformer", "机器学习", "神经网络", "深度学习"),
    "life_sciences": ("protein", "genome", "rna", "dna", "cell", "gene",
                      "蛋白质", "基因组", "细胞"),
    "materials": ("material", "nanoparticle", "catalys", "polymer", "alloy",
                  "材料", "纳米", "催化"),
    "physics": ("quantum", "superconduct", "semiconductor", "optics", "量子",
                "半导体", "光学"),
    "chemistry": ("synthesis", "molecule", "reaction", "ligand", "合成", "分子", "反应"),
    "medicine": ("clinical", "trial", "patient", "disease", "临床", "患者", "疾病"),
    "environment": ("climate", "aqueous", "pollut", "soil", "气候", "污染", "土壤"),
    "robotics": ("robot", "autonomous", "slam", "manipulat", "机器人", "自主"),
}


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def forward(p: str) -> str:
    return p.replace(os.sep, "/")


def read_json(path: str):
    if not os.path.isfile(path):
        return None, "missing"
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f), None
    except Exception as exc:
        return None, "unreadable or invalid: %s" % exc


def write_json(path: str, data) -> str:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=False)
            f.write("\n")
        os.replace(tmp, path)
        return ""
    except OSError as exc:
        return "%s: %s" % (type(exc).__name__, exc)


def sha256_file(path: str, chunk: int = 1 << 16):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for b in iter(lambda: f.read(chunk), b""):
                h.update(b)
        return h.hexdigest()
    except OSError:
        return None


def read_text_file(path: str):
    try:
        size = os.path.getsize(path)
    except OSError:
        return "", "unreadable"
    degradation = None
    if size > MAX_TEXT_BYTES:
        degradation = "text larger than %d bytes; truncated" % MAX_TEXT_BYTES
    try:
        with open(path, "rb") as f:
            raw = f.read(MAX_TEXT_BYTES)
    except OSError as exc:
        return "", "unreadable: %s" % exc
    text = None
    for enc in ("utf-8-sig", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", "replace")
        degradation = degradation or "binary or non-utf8 text; decoded with replacement"
    if "\ufffd" in text and degradation is None:
        degradation = "invalid byte sequences replaced with U+FFFD"
    return text, degradation


def _ext_of(name: str) -> str:
    return os.path.splitext(name)[1].lstrip(".").lower()


def scan_inventory(input_dir: str) -> dict:
    out = {"inputs_exist": False, "files": [], "dirs": [], "degradations": [], "errors": []}
    if not os.path.isdir(input_dir):
        out["errors"].append("input directory missing: %s" % input_dir)
        return out
    out["inputs_exist"] = True
    for root, dnames, fnames in os.walk(input_dir):
        dnames.sort()
        fnames.sort()
        rel_root = os.path.relpath(root, input_dir)
        if rel_root == ".":
            rel_root = ""
        for d in dnames:
            out["dirs"].append(forward(os.path.join(rel_root, d)))
        for fn in fnames:
            if rel_root == "" and fn in SPEC_NAMES:
                continue
            abs_p = os.path.join(root, fn)
            rel = forward(os.path.join(rel_root, fn))
            entry = {"source": rel, "abs": abs_p, "name": fn,
                     "ext": _ext_of(fn), "bytes": None, "mtime": None,
                     "sha256": None, "errors": []}
            try:
                st = os.stat(abs_p)
                entry["bytes"] = st.st_size
                entry["mtime"] = int(st.st_mtime)
            except OSError as exc:
                entry["errors"].append("stat failed: %s" % exc)
            entry["permissions"] = {
                "readable": os.access(abs_p, os.R_OK),
                "writable": os.access(abs_p, os.W_OK),
                "stat_ok": entry["bytes"] is not None,
            }
            if entry["permissions"]["stat_ok"] and entry["permissions"]["readable"]:
                entry["sha256"] = sha256_file(abs_p)
            elif entry["permissions"]["stat_ok"]:
                entry["errors"].append("file unreadable")
            out["files"].append(entry)
    if not out["files"]:
        out["degradations"].append("no files found under input directory")
    return out


def _pdf_header_ok(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(1024).lstrip()[:5] == b"%PDF-"
    except OSError:
        return False


def _pdf_text_external(path: str):
    for exe_name in ("pdftotext", "pdf-text-tool"):
        exe = shutil.which(exe_name)
        if not exe:
            continue
        cmd = [exe, path]
        if exe_name == "pdftotext":
            cmd += ["-"]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=120)
        except Exception:
            continue
        if r.returncode == 0 and r.stdout:
            return r.stdout.decode("utf-8", "replace"), exe_name
    return None, None


def extract_text_for(entry: dict):
    abs_p = entry.get("abs")
    ext = entry.get("ext")
    if ext in TEXT_EXTS:
        text, note = read_text_file(abs_p)
        return text, bool(text), "utf8-text", note
    if ext == "pdf":
        if not _pdf_header_ok(abs_p):
            return "", False, None, "pdf-invalid-header: not a well-formed PDF"
        text, method = _pdf_text_external(abs_p)
        if text and text.strip():
            return text, True, method, None
        if text is not None and not text.strip():
            return "", False, method, "pdf has no extractable text layer (scanned or empty)"
        return "", False, None, "pdf text extraction unavailable (no pdftotext/pdf-text-tool)"
    return "", False, None, None


_BIB_FIELD = re.compile(
    r"(?P<name>[A-Za-z][\w-]*)\s*=\s*(?P<val>\{[^{}]*\}|\"[^\"]*\"|[^,\s\}]+)", re.S)
_DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
_ARXIV_RE = re.compile(r"(?:\barXiv\s*[:/\s]*)(\d{4}\.\d{4,5})(?:v\d+)?", re.I)


def parse_bibtex(text: str):
    entries = []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text, re.I):
        etype, key = m.group(1).lower(), m.group(2)
        open_brace = text.find("{", m.end() - 1)
        end = _match_brace(text, open_brace) if open_brace != -1 else m.end()
        body = text[m.end():end]
        fields = {}
        for fm in _BIB_FIELD.finditer(body):
            name = fm.group("name").lower()
            val = fm.group("val").strip()
            if val and val[0] in "{\"":
                val = val[1:-1].strip()
            fields[name] = val
        entries.append({"type": etype, "key": key, "fields": fields})
    return entries


def _match_brace(text: str, start: int):
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return len(text)


def _find_doi(text: str):
    m = _DOI_RE.search(text or "")
    return m.group(0).rstrip(".,);") if m else None


def _find_arxiv(text: str):
    m = _ARXIV_RE.search(text or "")
    return m.group(1) if m else None


def _norm_key(s):
    if s is None:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def _first_author_of(authors: str):
    if not authors:
        return None
    parts = [p.strip() for p in re.split(r"\s+and\s+", authors, flags=re.I) if p.strip()]
    if not parts:
        return None
    first = parts[0]
    if "," in first:
        return first.split(",", 1)[0].strip()
    toks = first.split()
    return toks[-1] if toks else first


def _has_any(text: str, name: str, keys):
    hay = " " + (text or "").lower() + " " + (name or "").lower() + " "
    return any(k in hay for k in keys)


def _meta_raw_bib(text: str, field: str):
    m = re.search(r"\b%s\s*=\s*(\{[^{}]*\}|\"[^\"]*\"|[^,\s\}]+)" % re.escape(field),
                  text, re.I | re.S)
    return m.group(1) if m else None


def _ris_raw(text: str, tag: str):
    m = re.search(r"^%s  - (.*?)$" % re.escape(tag), text, re.M | re.I)
    return m.group(1).strip() if m else None


def extract_metadata(entry: dict, text: str):
    meta = {"title": None, "authors": None, "first_author": None, "year": None,
            "journal": None, "doi": None, "arxiv_id": None}
    evidence = {}
    pending = []
    source = entry.get("source")
    ext = entry.get("ext")

    def _set(field, value, locator, quote):
        if value in (None, ""):
            return
        meta[field] = value
        evidence.setdefault(field, []).append({"locator": locator, "quote": quote[:240]})

    if ext == "bib" and text:
        for bib in parse_bibtex(text):
            f = bib["fields"]
            _set("title", f.get("title"), source, _meta_raw_bib(text, "title") or "")
            _set("journal", f.get("journal"), source, _meta_raw_bib(text, "journal") or "")
            if f.get("year"):
                _set("year", f.get("year"), source, _meta_raw_bib(text, "year") or "")
            doi = f.get("doi") or f.get("url")
            if doi and (doi.startswith("10.") or "doi.org" in doi):
                m = _DOI_RE.search(doi)
                _set("doi", m.group(0).rstrip(".,);") if m else doi,
                     source, _meta_raw_bib(text, "doi") or doi)
            if f.get("author"):
                meta["authors"] = f["author"]
                meta["first_author"] = _first_author_of(f["author"])
                evidence["authors"] = [{"locator": source,
                                        "quote": _meta_raw_bib(text, "author")
                                        or "author=" + f["author"][:200]}]
    elif ext == "ris" and text:
        ris_fields = {}
        for line in text.splitlines():
            if len(line) >= 3 and line[2:3] in (" ", "-"):
                ris_fields[line[:2].upper()] = line[3:].strip()

        def ris_field(*tags):
            for t in tags:
                if ris_fields.get(t):
                    return ris_fields[t]
            return None
        _set("title", ris_field("TI", "T1"), source, _ris_raw(text, "TI") or _ris_raw(text, "T1") or "")
        _set("year", ris_field("PY", "Y1"), source, _ris_raw(text, "PY") or _ris_raw(text, "Y1") or "")
        _set("journal", ris_field("JO", "JF", "T2"), source, _ris_raw(text, "JO") or "")
        _set("doi", ris_field("DO"), source, _ris_raw(text, "DO") or "")
        au = ris_field("AU", "A1")
        if au:
            meta["authors"] = au
            meta["first_author"] = _first_author_of(au)
            evidence["authors"] = [{"locator": source, "quote": _ris_raw(text, "AU") or ("AU - " + au)[:200]}]

    doi_in_text = _find_doi(text) or _find_doi(entry.get("name") or "")
    if doi_in_text and not meta.get("doi"):
        _set("doi", doi_in_text, source, "doi:" + doi_in_text)
    arx = _find_arxiv(text) or _find_arxiv(entry.get("name") or "")
    if arx and not meta.get("arxiv_id") and re.fullmatch(r"\d{4}\.\d{4,5}", arx):
        _set("arxiv_id", arx, source, "arXiv:" + arx)

    for field in ("title", "authors", "year", "journal"):
        if meta.get(field) in (None, "") and ext in ("pdf", ""):
            pending.append({"source": source, "field": field,
                            "reason": "no structured metadata; set via decisions.json"})
    return meta, evidence, pending


def classify_entry(entry: dict, text: str, meta: dict) -> str:
    ext = entry.get("ext")
    name = entry.get("name") or ""
    if any("pdf-invalid-header" in e for e in entry.get("errors", [])):
        return "corrupt_file"
    if not entry.get("permissions", {}).get("stat_ok"):
        return "unreadable"
    if ext in ("bib", "ris", "enw"):
        return "bibliography"
    if ext in ("csv", "tsv", "xlsx", "xls", "parquet"):
        return "dataset"
    if ext in ("md", "txt", "rst"):
        return "note"
    if ext == "ipynb":
        return "notebook"
    if ext in ("png", "jpg", "jpeg", "tif", "tiff", "gif", "bmp", "webp"):
        return "image"
    if ext == "pdf" or ext == "":
        if not entry.get("has_text_layer"):
            return "scanned_pdf"
        tl = text.lower()
        if _has_any(tl, name, ("supporting information", "supplementary material", "补充材料", "附录")):
            return "supplementary"
        if _has_any(tl, name, ("thesis", "dissertation", "博士学位论文", "硕士学位论文", "学位论文")):
            return "thesis"
        if meta.get("arxiv_id") or _has_any(tl, name, ("preprint", "预印本", "biorxiv", "medrxiv", "arxiv")):
            return "preprint"
        if _has_any(tl, name, ("proceedings", "conference", "会议论文", "国际会议", "届会议")):
            return "conference_paper"
        if meta.get("doi") or meta.get("journal") or _has_any(tl, name, ("journal of", "volume", "vol.", "issn")):
            return "journal_article"
        return "document_pdf"
    return "other"


def topic_of(entry: dict, text: str, meta: dict):
    hay = " " + (text or "").lower() + " " + (entry.get("name") or "").lower() + " "
    if meta.get("title"):
        hay += " " + meta["title"].lower() + " "
    for topic, keys in TOPIC_VOCAB.items():
        for k in keys:
            if k in hay:
                return topic
    return None


def _version_kind(entry: dict) -> str:
    text = (entry.get("text") or "").lower()
    name = (entry.get("name") or "").lower()
    if _has_any(text, name, ("supporting information", "supplementary", "补充材料", "附录")):
        return "supplementary"
    if _has_any(text, name, ("revision", "revised", "修订", "v2", "version")):
        return "revision"
    if _has_any(text, name, ("preprint", "预印本", "arxiv", "biorxiv", "medrxiv")):
        return "preprint"
    if _has_any(text, name, ("published", "accepted for publication", "正式版", "final version")):
        return "published"
    return "unknown"


class DSU:
    def __init__(self):
        self.p = {}

    def add(self, x):
        self.p.setdefault(x, x)

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


_UNSAFE = re.compile(r"[^\w\u4e00-\u9fff\-]+", re.UNICODE)


def sanitize_segment(s, maxlen=60):
    if s is None:
        return ""
    s = _UNSAFE.sub(" ", str(s))
    s = re.sub(r"\s+", " ", s).strip(" -")
    return s[:maxlen].rstrip(" -")


def template_name(meta: dict, entry: dict, category: str):
    if category in ("bibliography", "dataset", "note", "notebook", "image",
                    "corrupt_file", "unreadable", "other"):
        return None
    fa, year, title = meta.get("first_author"), meta.get("year"), meta.get("title")
    ext = entry.get("ext") or "bin"
    if not (fa and year and title):
        return None
    return "%s-%s-%s.%s" % (sanitize_segment(fa, 40), sanitize_segment(year, 8),
                            sanitize_segment(title, 48), sanitize_segment(ext, 16))


def _with_version_kind(name: str, kind: str):
    if not name or kind in (None, "", "unknown"):
        return name
    stem, dot, ext = name.rpartition(".")
    return ("%s-%s.%s" % (stem, sanitize_segment(kind, 20), ext)) if dot else name


def adopt_decisions(meta: dict, evidence: dict, decision, source: str, pending: list):
    if not isinstance(decision, dict):
        return
    for field in ("title", "authors", "year", "journal", "doi", "arxiv_id", "topic", "kind_hint"):
        if field not in decision:
            continue
        spec = decision[field]
        if isinstance(spec, str):
            pending.append({"source": source, "field": field,
                            "reason": "decision without evidence; not adopted"})
            continue
        if isinstance(spec, dict):
            value, quote = spec.get("value"), spec.get("quote")
            locator = spec.get("locator") or source
            if value and quote:
                if field == "authors":
                    meta["authors"] = value
                    meta["first_author"] = _first_author_of(value)
                elif field == "topic":
                    meta["_topic_override"] = value
                    continue
                elif field == "kind_hint":
                    meta["_kind_hint"] = value
                    continue
                else:
                    meta[field] = value
                evidence.setdefault(field, []).append({"locator": locator, "quote": quote[:240]})
            else:
                pending.append({"source": source, "field": field,
                                "reason": "decision lacks quote/locator; not adopted"})
            continue
        pending.append({"source": source, "field": field,
                        "reason": "unrecognized decision value shape; not adopted"})


def load_decisions(path: str):
    if not path or not os.path.isfile(path):
        return {}, None
    obj, err = read_json(path)
    if err or not isinstance(obj, dict):
        return {}, "decisions.json unreadable or invalid: %s" % (err or "not an object")
    d = obj.get("decisions", obj)
    if not isinstance(d, dict):
        return {}, "decisions.json must be an object keyed by source path"
    return d, None


def analyze(scan: dict, decisions: dict = None, hierarchy: tuple = ("document_type",)):
    decisions = decisions or {}
    files = scan["files"]

    for e in files:
        if not e.get("permissions", {}).get("stat_ok") or not e.get("permissions", {}).get("readable"):
            e["text"] = ""
            e["text_len"] = 0
            e["text_preview"] = ""
            e["has_text_layer"] = False
            e["text_method"] = None
            e["meta"] = {}
            e["evidence_fields"] = {}
            e["_pending"] = []
            e["category"] = "unreadable"
            e["topic"] = None
            continue
        text, has_layer, method, note = extract_text_for(e)
        e["text"] = text or ""
        e["text_len"] = len(text or "")
        e["text_preview"] = (text or "")[:200]
        e["has_text_layer"] = has_layer
        e["text_method"] = method
        if note:
            e.setdefault("errors", []).append(note)
        meta, evid, pend = extract_metadata(e, text or "")
        adopt_decisions(meta, evid, decisions.get(e["source"]), e["source"], pend)
        e["meta"] = meta
        e["evidence_fields"] = evid
        e["_pending"] = pend
        e["category"] = classify_entry(e, text or "", meta)
        e["topic"] = topic_of(e, text or "", meta)
        e["evidence_fields"].setdefault("document_type", []).append({
            "locator": e["source"], "quote": e["name"],
            "basis": "extension=%s; has_text_layer=%s" % (e["ext"] or "", e.get("has_text_layer")),
        })

    by_hash = {}
    for e in files:
        h = e.get("sha256")
        if h:
            by_hash.setdefault(h, []).append(e)
    for group in by_hash.values():
        group.sort(key=lambda x: x["source"])
        for i, e in enumerate(group):
            e["keep"] = i == 0
            e["duplicate_of"] = group[0]["source"] if i else None

    dsu = DSU()
    for e in files:
        dsu.add("file:" + e["source"])
    for e in files:
        m = e.get("meta") or {}
        if m.get("doi"):
            k = "doi:" + _norm_key(m["doi"])
            dsu.add(k); dsu.union("file:" + e["source"], k)
        if m.get("arxiv_id"):
            k = "arxiv:" + _norm_key(m["arxiv_id"])
            dsu.add(k); dsu.union("file:" + e["source"], k)
        if m.get("title") and m.get("year"):
            k = "title:" + _norm_key(m["title"]) + ":" + str(m["year"])
            dsu.add(k); dsu.union("file:" + e["source"], k)
    roots = defaultdict(list)
    for e in files:
        roots[dsu.find("file:" + e["source"])].append(e)
    version_groups = []
    counter = 0
    for members in roots.values():
        members.sort(key=lambda x: x["source"])
        if len(members) == 1:
            members[0]["version_group"] = None
            members[0]["version_kind"] = None
            continue
        counter += 1
        gid = "vg-%02d" % counter
        basis = []
        for e in members:
            m = e.get("meta") or {}
            if m.get("doi"):
                basis.append("doi:" + m["doi"])
            if m.get("arxiv_id"):
                basis.append("arxiv:" + m["arxiv_id"])
            e["version_group"] = gid
            e["version_kind"] = _version_kind(e)
        version_groups.append({
            "group_id": gid, "basis": sorted(set(basis)),
            "members": [{"source": e["source"], "version_kind": e["version_kind"],
                         "keep": e.get("keep", True), "duplicate_of": e.get("duplicate_of")}
                        for e in members],
        })

    for e in files:
        ext = e["ext"]
        stem = sanitize_segment(os.path.splitext(e["name"])[0], 60) or "file"
        default_name = ("%s.%s" % (stem, ext)) if ext else stem
        tn = template_name(e.get("meta") or {}, e, e.get("category"))
        name = tn or default_name
        if e.get("version_group") and e.get("version_kind") not in (None, "unknown"):
            name = _with_version_kind(name, e["version_kind"])
        e["suggested_name"] = name

    for e in files:
        parts = []
        for dim in hierarchy:
            if dim == "document_type":
                parts.append(e.get("category") or "unclassified")
            elif dim == "topic":
                parts.append(e.get("topic") or "unclassified")
            elif dim == "ext":
                parts.append(e.get("ext") or "noext")
            elif dim == "year":
                parts.append(str(e.get("meta", {}).get("year") or "unknown-year"))
            elif dim == "source":
                parts.append(sanitize_segment(e["source"], 30) or "file")
        e["category_path"] = "/".join([p for p in parts if p]) or "unclassified"

    return _assemble_analysis(files, version_groups)


def _assemble_analysis(files, version_groups):
    catalog, evidence_hashes, evidence_permissions = [], {}, {}
    evidence_text, locators, pending = {}, [], []
    skipped, permission_errors, duplicates = [], [], []
    files_sorted = sorted(files, key=lambda x: x["source"])
    for e in files_sorted:
        m = e.get("meta") or {}
        catalog.append({
            "source": e["source"], "sha256": e["sha256"], "bytes": e["bytes"],
            "type": e["ext"] or "unknown", "document_type": e.get("category"),
            "category_path": e.get("category_path"), "suggested_name": e.get("suggested_name"),
            "keep": e.get("keep", True), "duplicate_of": e.get("duplicate_of"),
            "version_group": e.get("version_group"), "version_kind": e.get("version_kind"),
            "has_text_layer": e.get("has_text_layer", False), "permissions": e.get("permissions"),
            "metadata": {k: m.get(k) for k in
                         ("title", "authors", "first_author", "year", "journal", "doi", "arxiv_id")},
            "errors": list(e.get("errors", [])),
        })
        evidence_hashes[e["source"]] = e["sha256"]
        evidence_permissions[e["source"]] = e.get("permissions")
        evidence_text[e["source"]] = {"has_text_layer": e.get("has_text_layer", False),
                                      "char_count": len(e.get("text") or ""),
                                      "method": e.get("text_method")}
        for field, evs in (e.get("evidence_fields") or {}).items():
            for ev in evs:
                locators.append({"source": e["source"], "field": field,
                                 "locator": ev.get("locator"), "quote": ev.get("quote"),
                                 "basis": ev.get("basis")})
        pending.extend(e.get("_pending") or [])
        if not e.get("permissions", {}).get("stat_ok") or not e.get("permissions", {}).get("readable"):
            permission_errors.append({"source": e["source"],
                                      "error": "unreadable or stat failed",
                                      "permissions": e.get("permissions")})
        for err in e.get("errors", []):
            skipped.append({"source": e["source"], "error": err})
        if e.get("duplicate_of"):
            duplicates.append({"source": e["source"], "duplicate_of": e["duplicate_of"],
                               "sha256": e["sha256"]})

    fields_missing = {"title": 0, "authors": 0, "year": 0, "journal": 0, "doi": 0}
    for e in files_sorted:
        m = e.get("meta") or {}
        for k in fields_missing:
            if (m.get(k) in (None, "")) and e.get("ext") in ("pdf", ""):
                fields_missing[k] += 1
    uncertainty = {
        "unclassified_files": sum(1 for e in files_sorted
                                  if e.get("category") in ("other", "unclassified", "document_pdf")),
        "files_without_text_layer": sum(1 for e in files_sorted if not e.get("has_text_layer")),
        "missing_metadata_fields": fields_missing,
        "version_relationships_unknown": sum(1 for g in version_groups
                                             if any(mm["version_kind"] == "unknown"
                                                    for mm in g["members"])),
        "pending_human_count": len(pending),
    }

    hierarchy_map = {}
    for e in files_sorted:
        hierarchy_map.setdefault(e.get("category_path"), []).append(e["source"])

    return {
        "catalog": catalog,
        "hierarchy": {k: {"files": v, "count": len(v)}
                      for k, v in sorted(hierarchy_map.items())},
        "metadata": {e["source"]: e.get("meta") for e in files_sorted},
        "versions": version_groups,
        "evidence": {"hashes": evidence_hashes, "permissions": evidence_permissions,
                     "text": evidence_text, "locators": locators},
        "failures": {"skipped": skipped, "permission_errors": permission_errors,
                     "duplicates": duplicates},
        "uncertainty": uncertainty,
        "pending_human": pending,
    }


def _target_rel(e: dict) -> str:
    return "/".join([e.get("category_path") or "unclassified", e.get("suggested_name")])


def _copy_with_suffix(src_abs, target_dir, name, src_sha):
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, name)
    if os.path.exists(target):
        if sha256_file(target) == src_sha:
            return name, "already_present"
        stem, dot, ext = name.rpartition(".")
        for i in range(2, 1000):
            cand = ("%s-%d.%s" % (stem, i, ext)) if dot else ("%s-%d" % (name, i))
            full = os.path.join(target_dir, cand)
            if not os.path.exists(full):
                shutil.copy2(src_abs, full)
                return cand, "copied_conflict"
            if sha256_file(full) == src_sha:
                return cand, "already_present"
        return name, "copy_failed_too_many_conflicts"
    shutil.copy2(src_abs, target)
    return name, "copied"


def plan_operations(files, output_dir):
    operations = []
    for e in sorted(files, key=lambda x: x["source"]):
        if not e.get("keep"):
            operations.append({"action": "deduplicated", "source": e["source"],
                               "duplicate_of": e.get("duplicate_of"), "target": None})
            continue
        if e.get("category") in ("corrupt_file", "unreadable"):
            operations.append({"action": "skipped", "source": e["source"],
                               "reason": "; ".join(e.get("errors", []) or ["unusable"])})
            continue
        operations.append({"action": "plan_copy", "source": e["source"],
                           "target": _target_rel(e), "sha256": e["sha256"]})
    return operations, {"note": "dry-run: no files were modified or created"}


def apply_operations(files, output_dir, work_dir):
    operations, rollback, conflicts = [], [], []
    for e in sorted(files, key=lambda x: x["source"]):
        if not e.get("keep"):
            operations.append({"action": "deduplicated", "source": e["source"],
                               "duplicate_of": e.get("duplicate_of")})
            continue
        if e.get("category") in ("corrupt_file", "unreadable"):
            operations.append({"action": "skipped", "source": e["source"],
                               "reason": "; ".join(e.get("errors", []) or ["unusable"])})
            continue
        src_abs = e["abs"]
        if not os.path.isfile(src_abs):
            operations.append({"action": "skipped", "source": e["source"],
                               "reason": "source missing"})
            continue
        target_dir = os.path.join(output_dir, e.get("category_path").replace("/", os.sep))
        final_name, action = _copy_with_suffix(src_abs, target_dir,
                                               e["suggested_name"], e["sha256"])
        target_abs = os.path.join(target_dir, final_name)
        op = {"action": action, "source": e["source"],
              "target": forward(os.path.relpath(target_abs, output_dir)),
              "sha256": e["sha256"]}
        if action == "copied_conflict":
            conflicts.append({"target": _target_rel(e), "source": e["source"],
                              "note": "same target name, different content; resolved by -N suffix"})
        if action in ("copied", "copied_conflict"):
            rollback.append(forward(target_abs))
        operations.append(op)
    write_json(os.path.join(work_dir, "rollback.json"),
               {"created_files": rollback, "note": "delete listed files to roll back this apply"})
    return operations, rollback, conflicts
