#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — sci-library-organizer 可复用实现（仅标准库）。

每个语义字段为声明对象 {value, confidence, method, evidence[], alternatives[]}。
confidence ∈ certain|inferred|not_reported|conflict；无证据绝不填值。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import unicodedata
import zlib

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".bib", ".ris", ".csv", ".tsv",
                   ".json", ".jsonl", ".yaml", ".yml", ".xml", ".html", ".htm",
                   ".tex", ".py", ".r", ".ipynb"}
PDF_MAGIC = b"%PDF-"
MAX_TEXT_BYTES = 2 * 1024 * 1024
CONFIDENCE_LEVELS = ("certain", "inferred", "not_reported", "conflict")
STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
ARXIV_RE = re.compile(r"(?:arXiv:|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5})(?:v\d+)?", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")

TOPIC_KEYWORDS = ("deep learning", "machine learning", "genomics", "neuroscience",
                  "material", "protein", "quantum", "climate", "cancer", "robot",
                  "nlp", "computer vision", "drug", "immun", "optimi", "graph",
                  "bayesian", "reinforcement")


def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, UnicodeDecodeError, ValueError):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None


def write_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def read_text_file(path: str):
    for enc in ("utf-8-sig", "utf-8"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read(MAX_TEXT_BYTES), enc, False
        except (OSError, UnicodeDecodeError):
            continue
    try:
        with open(path, "r", encoding="latin-1", errors="replace") as f:
            return f.read(MAX_TEXT_BYTES), "latin-1", True
    except OSError:
        return "", "none", True


def normalize_title(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^0-9a-z]+", "", s.lower())


def sanitize_filename(name: str, max_len: int = 120) -> str:
    if not name:
        return "unnamed"
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", name)
    name = re.sub(r"\s+", " ", name).strip(" ._")
    name = re.sub(r"_+", "_", name)
    if not name:
        return "unnamed"
    if len(name) > max_len:
        stem, dot, ext = name.rpartition(".")
        if dot and 1 < len(ext) <= 6 and len(stem) > 8:
            return stem[: max_len - len(dot) - len(ext)] + dot + ext
        return name[:max_len]
    return name


def _has_replacement_char(text: str) -> bool:
    return "\ufffd" in text


def claim(value, confidence, method, evidence=None, alternatives=None):
    c = {"value": value, "confidence": confidence, "method": method, "evidence": evidence or []}
    if alternatives:
        c["alternatives"] = alternatives
        c["confidence"] = "conflict"
    return c


def claim_not_reported(method="none", evidence=None):
    return {"value": None, "confidence": "not_reported", "method": method, "evidence": evidence or []}


def evidence_item(locator: str, quote: str):
    return {"locator": locator, "quote": quote}


def _try_pdftotext(path: str) -> str:
    exe = shutil.which("pdftotext")
    if not exe:
        return ""
    try:
        proc = subprocess.run([exe, path, "-"], capture_output=True, timeout=45)
        if proc.returncode == 0:
            return proc.stdout.decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _decompress_streams(data: bytes):
    out = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.DOTALL):
        raw = m.group(1).rstrip(b"\r\n")
        dec = None
        for attempt in (raw, raw + b"\n", raw + b"\r\n"):
            try:
                dec = zlib.decompress(attempt)
                break
            except zlib.error:
                continue
        out.append(dec if dec is not None else raw)
    return out


def _extract_pdf_strings(data: bytes):
    hits = []
    for key in (b"/Title", b"/Author", b"/Subject", b"/Keywords"):
        m = re.search(re.escape(key) + rb"\s*\((.*?)\)", data, re.DOTALL)
        if m:
            hits.append(m.group(1))
    for stream in _decompress_streams(data):
        for m in re.finditer(rb"\((.*?)\)\s*T[jJ]", stream, re.DOTALL):
            hits.append(m.group(1))
        for m in re.finditer(rb"\[(.*?)\]\s*TJ", stream, re.DOTALL):
            for s in re.findall(rb"\((.*?)\)", m.group(1), re.DOTALL):
                hits.append(s)

    def _unescape(b):
        b = b.replace(rb"\(", b"(").replace(rb"\)", b")").replace(rb"\\", b"\\")
        return b.decode("latin-1", errors="replace")

    return [_unescape(h) for h in hits if len(h) > 1]


def extract_pdf(path: str):
    info = {"text": "", "encrypted": False, "has_text_layer": False, "method": "none", "error": None}
    try:
        data = open(path, "rb").read(8 * 1024 * 1024)
    except OSError as e:
        info["error"] = str(e)
        return info
    if not data.startswith(PDF_MAGIC):
        info["error"] = "not a PDF"
        return info
    if re.search(rb"/Encrypt\b", data):
        info["encrypted"] = True
        info["error"] = "encrypted PDF"
        return info
    txt = _try_pdftotext(path)
    if txt.strip():
        info["text"] = txt
        info["has_text_layer"] = bool(re.search(r"[A-Za-z0-9\u4e00-\u9fff]{3,}", txt))
        info["method"] = "pdftotext"
        return info
    joined = "\n".join(p for p in _extract_pdf_strings(data) if p.strip())
    info["text"] = joined[:MAX_TEXT_BYTES]
    info["has_text_layer"] = bool(re.search(r"[A-Za-z0-9\u4e00-\u9fff]{3,}", joined))
    info["method"] = "minimal" if info["has_text_layer"] else "none"
    return info


def detect_file_kind(relpath: str, is_pdf: bool, has_text: bool):
    low = relpath.lower()
    name = os.path.basename(low)
    ext = os.path.splitext(low)[1]
    if any(k in name for k in ("supplement", "supplementary", "supporting", "_si", "si.", "appendix", "esm", "extra")):
        return "supplementary"
    if any(k in name for k in ("thesis", "dissertation", "phd", "master")):
        return "thesis"
    if any(k in name for k in ("preprint", "arxiv", "working_paper", "working-paper")):
        return "preprint"
    if ext in {".csv", ".tsv", ".parquet", ".h5", ".hdf5", ".nc", ".npz", ".mat", ".xlsx", ".xls", ".sav", ".dta"} or \
            any(k in name for k in ("dataset", "data_", "_data")):
        return "dataset"
    if ext in {".md", ".markdown", ".txt", ".docx", ".doc", ".rtf", ".odt"} or \
            any(k in name for k in ("notes", "readme", "note_", "README")):
        return "notes"
    if any(k in name for k in ("conference", "proceedings", "proc.", "workshop", "iclr", "neurips",
                               "cvpr", "icml", "acl", "emnlp", "iccv", "aaai", "nips", "ijcai")):
        return "conference_paper"
    if is_pdf:
        return "scan" if not has_text else "journal_article"
    if ext in {".bib", ".ris"}:
        return "bibliography"
    return "unknown"


def _extract_doi(text: str, locator: str):
    m = DOI_RE.search(text)
    if m:
        v = m.group(0).rstrip(".,;")
        return claim(v, "certain", "regex", [evidence_item(locator, v)])
    return claim_not_reported("regex")


def _extract_arxiv(text: str, locator: str):
    m = ARXIV_RE.search(text)
    if m:
        return claim(m.group(1), "certain", "regex", [evidence_item(locator, m.group(0))])
    return claim_not_reported("regex")


def _extract_year(text: str, filename: str, locator: str):
    m = YEAR_RE.search(text)
    if m:
        y = m.group(1)
        return claim(int(y), "certain", "regex", [evidence_item(locator, y)])
    m = YEAR_RE.search(filename)
    if m:
        y = m.group(1)
        return claim(int(y), "inferred", "filename", [evidence_item("filename:" + filename, y)])
    return claim_not_reported("regex")


def _extract_title_from_text(text: str, locator: str):
    for ln in text.splitlines():
        ln = ln.strip()
        if len(ln) < 4 or re.match(r"^(doi|arxiv|http|www|received|accepted|copyright|©)", ln, re.IGNORECASE):
            continue
        return claim(ln[:300], "inferred", "first_text_line", [evidence_item(locator, ln[:300])])
    return claim_not_reported("text")


def parse_bibtex(text: str, locator: str):
    out = {k: claim_not_reported("bibtex") for k in ("authors", "title", "year", "journal", "doi")}
    if not re.search(r"@\w+\s*\{", text, re.IGNORECASE):
        return out
    for field, key in (("title", "title"), ("author", "authors"), ("year", "year"),
                       ("journal", "journal"), ("doi", "doi")):
        m = re.search(rf"\b{field}\s*=\s*[\{{\"]([^}}\"]+)", text, re.IGNORECASE)
        if m:
            out[key] = claim(m.group(1).strip(), "certain", "bibtex",
                             [evidence_item(locator, m.group(1).strip())])
    return out


def extract_metadata(path: str, relpath: str, text: str, pdf_info: dict):
    loc = "file:" + relpath + ":text"
    base = os.path.basename(relpath)
    if text.strip():
        meta = {
            "doi": _extract_doi(text, loc),
            "arxiv_id": _extract_arxiv(text, loc),
            "year": _extract_year(text, base, loc),
            "title": _extract_title_from_text(text, loc),
            "authors": claim_not_reported("none"),
            "journal": claim_not_reported("none"),
        }
    else:
        meta = {k: claim_not_reported("no_text_layer")
                for k in ("doi", "arxiv_id", "year", "title", "authors", "journal")}
    if base.lower().endswith(".bib"):
        bib = parse_bibtex(text or read_text_file(path)[0], "file:" + relpath + ":bib")
        for k, v in bib.items():
            if v.get("confidence") == "certain":
                meta[k] = v
    if meta["doi"]["confidence"] == "not_reported":
        m = DOI_RE.search(base)
        if m:
            meta["doi"] = claim(m.group(0).rstrip(".,;"), "inferred", "filename",
                                [evidence_item("filename:" + base, m.group(0).rstrip(".,;"))])
    if meta["authors"]["confidence"] == "not_reported" and meta["year"].get("value"):
        seg = base.split(str(meta["year"]["value"]))[0].strip(" -_.")
        if seg and len(seg) <= 60:
            meta["authors"] = claim(seg, "inferred", "filename", [evidence_item("filename:" + base, seg)])
    return meta


def primary_label(meta, relpath):
    t = meta.get("title", {}).get("value") if isinstance(meta.get("title"), dict) else None
    for src in (t, os.path.basename(relpath)):
        if not src:
            continue
        low = src.lower()
        for kw in TOPIC_KEYWORDS:
            if kw in low:
                return [kw]
    return []


def inventory(input_dir: str):
    if not os.path.isdir(input_dir):
        return {"root": input_dir, "dirs": [], "files": [], "error": "input directory not found"}
    dirs, files = [], []
    for dirpath, dirnames, filenames in os.walk(input_dir, topdown=True):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".git"))
        rel_dir = os.path.relpath(dirpath, input_dir).replace(os.sep, "/")
        if rel_dir != ".":
            dirs.append(rel_dir)
        for name in sorted(filenames):
            abs_path = os.path.join(dirpath, name)
            rel = os.path.relpath(abs_path, input_dir).replace(os.sep, "/")
            f = {"source": rel, "size": None, "mtime": None, "sha256": None, "permissions": {},
                 "errors": [], "kind": "unknown", "is_pdf": False, "has_text_layer": None,
                 "text_extraction": "none", "encrypted": False, "metadata": {}}
            try:
                st = os.stat(abs_path)
                f["size"] = st.st_size
                f["mtime"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime))
                f["permissions"] = {"readable": os.access(abs_path, os.R_OK),
                                    "writable": os.access(abs_path, os.W_OK), "stat_ok": True}
                f["sha256"] = sha256_file(abs_path) if st.st_size > 0 else hashlib.sha256(b"").hexdigest()
            except OSError as e:
                f["errors"].append("stat/unreadable: %s" % e)
                f["permissions"]["stat_ok"] = False
                f["permissions"]["readable"] = False
            is_pdf = False
            try:
                is_pdf = open(abs_path, "rb").read(5).startswith(PDF_MAGIC)
            except OSError:
                is_pdf = False
            f["is_pdf"] = is_pdf
            ext = os.path.splitext(name)[1].lower()
            text = ""
            if is_pdf:
                pdf_info = extract_pdf(abs_path)
                f["has_text_layer"] = pdf_info["has_text_layer"]
                f["text_extraction"] = pdf_info["method"]
                f["encrypted"] = pdf_info["encrypted"]
                if pdf_info["error"]:
                    f["errors"].append(pdf_info["error"])
                text = pdf_info["text"]
            elif ext in TEXT_EXTENSIONS:
                text, _enc, had_err = read_text_file(abs_path)
                f["has_text_layer"] = bool(text.strip())
                f["text_extraction"] = "direct"
                if had_err:
                    f["errors"].append("bad encoding; fallback latin-1 with replace")
                if _has_replacement_char(text):
                    f["errors"].append("replacement characters present (possible mojibake)")
            f["kind"] = detect_file_kind(rel, is_pdf, f["has_text_layer"])
            f["metadata"] = extract_metadata(abs_path, rel, text, {
                "encrypted": f["encrypted"], "has_text_layer": f["has_text_layer"],
                "method": f["text_extraction"]})
            files.append(f)
    return {"root": input_dir, "dirs": sorted(dirs), "files": files, "error": None}


def build_catalog(files):
    catalog = {}
    for f in files:
        src = f["source"]
        catalog[src] = {
            "source": src, "sha256": f["sha256"], "size": f.get("size"), "mtime": f.get("mtime"),
            "kind": f["kind"], "is_pdf": f.get("is_pdf", False),
            "has_text_layer": f.get("has_text_layer"), "text_extraction": f.get("text_extraction"),
            "encrypted": f.get("encrypted", False), "permissions": f.get("permissions", {}),
            "errors": list(f.get("errors", [])), "metadata": f.get("metadata", {}),
            "labels": primary_label(f.get("metadata", {}), src),
            "duplicate_of": None, "version_group": None, "version_role": "unknown",
            "category": None, "category_path": None, "suggested_name": None, "keep": True}
    return catalog


def assign_categories(catalog, dims):
    for src, e in catalog.items():
        e["category"] = e["kind"]
        parts = []
        for dim in dims:
            if dim == "document_type":
                parts.append(e["kind"])
            elif dim == "topic":
                parts.append(sanitize_filename((e["labels"] or ["untagged"])[0], 40).lower())
            elif dim == "year":
                y = e.get("metadata", {}).get("year", {})
                parts.append(str(y.get("value")) if isinstance(y, dict) and y.get("value") else "unknown_year")
            else:
                parts.append(sanitize_filename(str(dim), 30))
        e["category_path"] = "/".join(p for p in parts if p) or "unknown"


def _version_keys(e):
    keys = []
    m = e.get("metadata", {})
    doi = m.get("doi", {})
    if isinstance(doi, dict) and doi.get("value"):
        keys.append("doi:" + str(doi["value"]))
    arx = m.get("arxiv_id", {})
    if isinstance(arx, dict) and arx.get("value"):
        keys.append("arxiv:" + str(arx["value"]))
    title = m.get("title", {})
    if isinstance(title, dict) and title.get("value"):
        nt = normalize_title(title["value"])
        if nt:
            yv = m.get("year", {})
            y = yv.get("value") if isinstance(yv, dict) else None
            keys.append("title:" + nt + ("#" + str(y) if y else ""))
    return keys


def _guess_version_role(e):
    name = os.path.basename(e["source"]).lower()
    doi = e.get("metadata", {}).get("doi", {})
    if isinstance(doi, dict) and doi.get("value"):
        return "preprint" if any(k in name for k in ("preprint", "arxiv")) else "published"
    if any(k in name for k in ("supplement", "supporting", "appendix", "esm")):
        return "supplementary"
    if re.search(r"v\d+", name):
        return "revision"
    return "unknown"


def group_versions(catalog):
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent.setdefault(a, a)
        parent.setdefault(b, b)
        parent[find(a)] = find(b)

    keys = {}
    for s in sorted(catalog):
        for k in _version_keys(catalog[s]):
            if k:
                keys.setdefault(k, []).append(s)
    for members in keys.values():
        for s in members[1:]:
            union(members[0], s)
    groups = {}
    for s in sorted(catalog):
        groups.setdefault(find(s), []).append(s)
    gid = 0
    for members in sorted(groups.values()):
        if len(members) < 2:
            continue
        gid += 1
        g = "g%03d" % gid
        basis = sorted({k for s in members for k in _version_keys(catalog[s])})
        for s in members:
            catalog[s]["version_group"] = g
            catalog[s]["version_group_basis"] = basis
            catalog[s]["version_role"] = _guess_version_role(catalog[s])


def resolve_duplicates(catalog):
    seen = {}
    for src in sorted(catalog):
        h = catalog[src]["sha256"]
        if h and h in seen:
            catalog[src]["duplicate_of"] = seen[h]
            catalog[src]["keep"] = False
        else:
            seen[h] = src


def build_naming(catalog):
    for src, e in catalog.items():
        meta = e.get("metadata", {})
        au = meta.get("authors", {})
        yv = meta.get("year", {})
        tt = meta.get("title", {})
        first_au = str(au.get("value")).split(",")[0].split(" and ")[0].strip() \
            if isinstance(au, dict) and au.get("value") else None
        y = yv.get("value") if isinstance(yv, dict) else None
        t = tt.get("value") if isinstance(tt, dict) else None
        if first_au and y:
            stem = "%s-%s" % (first_au, y)
        elif y:
            stem = "unknown-%s" % y
        elif t:
            stem = sanitize_filename(str(t), 60)
        else:
            stem = "unnamed-%s" % e["sha256"][:8]
        if t and first_au and y:
            stem = "%s-%s" % (stem, sanitize_filename(str(t), 60))
        ext = os.path.splitext(e["source"])[1].lower() or ""
        e["suggested_name"] = sanitize_filename(stem) + ext


def build_plan(catalog, dims):
    plan = []
    name_counter = {}
    for src in sorted(catalog):
        e = catalog[src]
        base_name = e["suggested_name"] or "unnamed"
        target_rel = (e["category_path"] or "unknown") + "/" + base_name
        if not e["keep"]:
            plan.append({"source": src, "action": "skip_duplicate", "duplicate_of": e["duplicate_of"]})
            e["planned_target"] = target_rel
            continue
        n = name_counter.get(target_rel, 0)
        name_counter[target_rel] = n + 1
        if n > 0:
            stem, dot, ext = base_name.rpartition(".")
            base_name = (stem or "unnamed") + ("-%d" % (n + 1)) + (dot + ext if dot else "")
            e["suggested_name"] = base_name
            target_rel = (e["category_path"] or "unknown") + "/" + base_name
        plan.append({"source": src, "action": "plan_copy", "target": target_rel, "category": e["category"]})
        e["planned_target"] = target_rel
    return plan


def apply_plan(input_dir, output_library, catalog, plan):
    rollback = []
    for op in plan:
        if op["action"] != "plan_copy":
            continue
        src = op["source"]
        dst = os.path.join(output_library, *op["target"].split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        abs_src = os.path.join(input_dir, *src.split("/"))
        if os.path.exists(dst) and sha256_file(dst) == catalog[src]["sha256"]:
            op["action"] = "already_present"
            continue
        try:
            shutil.copy2(abs_src, dst)
            rollback.append({"created": dst, "from": src})
            op["action"] = "copied"
        except OSError as e:
            op["action"] = "copy_failed"
            op["error"] = str(e)
    return rollback
