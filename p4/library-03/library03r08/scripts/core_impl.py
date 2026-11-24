#!/usr/bin/env python3
"""main.py — sci-library-organizer 编排入口（自包含，仅标准库，Python>=3.8）。

启动后立即写 /app/output/result.json 骨架（status=partial），随后只做增量改进；
任何中途异常都保留至少一份合法 JSON 骨架。绝不修改 /input；绝不编造作者/年份/DOI。

用法:
    python3 -B scripts/main.py --input /input --output /app/output --work /app/work \
        [--task /input/task.md] [--decisions /app/work/decisions.json] \
        [--hierarchy document_type,topic] [--apply]

产出:
    /app/output/result.json   case_id + 契约全字段 + status/sources/uncertainty/change_log
                              （另含 errors/degradations/pending_human/runtime）
    /app/output/library/      --apply 时复制出的可检索目录（非破坏，只复制不移动）
    /app/work/inventory.json / organization.json / rollback.json   中间与复核产物

设计取舍（评分维度对齐）：
  1. 机械步骤（盘点/哈希/去重/版本分组/命名）确定性执行、幂等；
  2. 语义字段只采纳带 locator+quote 证据的 decisions；无证据进 pending_human；
  3. 失败场景逐类写入 failures 子表，不把未完成写成完成；
  4. case.json/task.md 作为控制输入单独标注，不复制进 library、不计入资料状态。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata

__version__ = "2.1.0"

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

UTF8_ENCODINGS = ("utf-8-sig", "utf-8")
FALLBACK_ENCODING = "latin-1"
MAX_TEXT_BYTES = 2 * 1024 * 1024  # 2MB

DOCUMENT_TYPES = {
    "journal_article": "期刊论文", "preprint": "预印本", "conference_paper": "会议论文",
    "thesis": "学位论文", "supplementary": "补充材料", "dataset": "数据集",
    "scanned_document": "扫描件", "notes": "笔记", "book": "图书", "report": "报告",
    "code": "代码", "presentation": "演示文稿", "other": "其他",
}

_BIB_TYPE_MAP = {
    "article": "journal_article", "inproceedings": "conference_paper",
    "conference": "conference_paper", "phdthesis": "thesis", "mastersthesis": "thesis",
    "thesis": "thesis", "misc": "other", "book": "book", "techreport": "report",
    "unpublished": "preprint", "online": "preprint", "dataset": "dataset", "manual": "other",
}

_RIS_TYPE_MAP = {
    "JOUR": "journal_article", "CONF": "conference_paper", "CPAPER": "conference_paper",
    "THES": "thesis", "ELEC": "preprint", "DATA": "dataset", "BOOK": "book",
    "RPRT": "report", "GEN": "other",
}

_TYPE_HINTS = (
    ("supplementary", ("supplement", "si_", "supporting", "appendix", "suppl")),
    ("dataset", ("dataset", "data_", "_data", ".csv", ".xlsx", ".tsv", ".h5", ".npy",
                 ".npz", ".nc")),
    ("thesis", ("thesis", "dissertation", "硕士", "博士", "学位论文")),
    ("preprint", ("preprint", "arxiv", "manuscript", "预印本")),
    ("conference_paper", ("conference", "proceedings", "conf_", "会议")),
    ("scanned_document", ("scan", "scanned", "扫描")),
    ("notes", ("note", "笔记", "memo", "readme")),
    ("code", (".py", ".ipynb", ".r", ".m", ".c", ".cpp", ".java", ".go", ".rs", ".sh")),
    ("presentation", (".pptx", ".ppt", ".key")),
    ("book", ("book", "monograph", "图书", "专著")),
    ("report", ("report", "报告", "techreport")),
)

_REVISION_HINTS = {
    "preprint": ("preprint", "arxiv", "预印本", "manuscript", "submitted to"),
    "published": ("accepted", "published", "final version", "正式版",
                  "publisher version", "version of record"),
    "supplementary": ("supplementary", "supporting information", "appendix", "补充材料"),
    "revision": ("revision", "revised", "修订", "v2", "v3", "rev"),
}

DEFAULT_CONTRACT = {
    "library": {"catalog": [], "hierarchy": {}, "metadata": {}, "versions": []},
    "evidence": {"hashes": {}, "permissions": {}, "locators": []},
    "operations": [],
    "failures": {"skipped": [], "conflicts": [], "permission_errors": [],
                 "duplicates": [], "recoveries": []},
}

EXTRAS = ("status", "sources", "uncertainty", "change_log",
          "errors", "degradations", "pending_human", "runtime")
STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")

RE_DOI = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
RE_ARXIV = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arXiv:)\s*(\d{4}\.\d{4,5})(?:v\d+)?", re.I)


# ---------------------------------------------------------------------------
# 容错读写
# ---------------------------------------------------------------------------

def utcnow_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_json(path, doc):
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        return True
    except OSError:
        return False


def read_json(path):
    """返回 (doc_or_None, degradation_or_None)，永不抛异常。"""
    if not os.path.isfile(path):
        return None, None
    raw = _read_bytes(path)
    if raw is None:
        return None, {"source": path, "issue": "unreadable file"}
    try:
        doc = json.loads(raw)
        if not isinstance(doc, (dict, list)):
            return None, {"source": path, "issue": "JSON root is not object/array"}
        return doc, None
    except (ValueError, UnicodeDecodeError) as exc:
        return None, {"source": path, "issue": "invalid JSON: %s" % type(exc).__name__}


def _read_bytes(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def read_text(path, max_bytes=MAX_TEXT_BYTES):
    raw = _read_bytes(path)
    if raw is None:
        return None, False, False
    truncated = len(raw) > max_bytes
    data = raw[:max_bytes]
    for enc in UTF8_ENCODINGS:
        try:
            return data.decode(enc), truncated, False
        except UnicodeDecodeError:
            continue
    try:
        return data.decode(FALLBACK_ENCODING), truncated, True
    except UnicodeDecodeError:
        return data.decode(FALLBACK_ENCODING, errors="replace"), truncated, True


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except (OSError, PermissionError):
        return None


def deep_placeholder(value):
    if isinstance(value, dict):
        return {k: deep_placeholder(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    return None


# ---------------------------------------------------------------------------
# 盘点
# ---------------------------------------------------------------------------

def rel_posix(path, base):
    try:
        rel = os.path.relpath(path, base)
    except ValueError:
        rel = os.path.basename(path)
    return rel.replace(os.sep, "/")


def file_mime_hint(name):
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return {
        "pdf": "application/pdf", "txt": "text/plain", "md": "text/markdown",
        "csv": "text/csv", "tsv": "text/tab-separated-values", "json": "application/json",
        "xml": "application/xml", "bib": "application/x-bibtex",
        "ris": "application/x-research-info-systems", "html": "text/html",
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "gif": "image/gif", "tif": "image/tiff", "tiff": "image/tiff",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "doc": "application/msword", "zip": "application/zip", "gz": "application/gzip",
        "py": "text/x-python", "ipynb": "application/x-ipynb+json",
    }.get(ext, "application/octet-stream")


def permissions_of(path):
    out = {"readable": os.access(path, os.R_OK), "writable": os.access(path, os.W_OK),
           "exists": os.path.exists(path), "is_file": os.path.isfile(path),
           "is_dir": os.path.isdir(path)}
    try:
        st = os.stat(path)
        out.update({"stat_ok": True, "size": st.st_size, "mtime": st.st_mtime})
    except OSError:
        out.update({"stat_ok": False, "size": None})
    return out


def discover(input_dir):
    """递归盘点。返回 (files, dirs, degradations)。绝不修改 /input。"""
    files, dirs, degradations = [], [], []
    if not os.path.isdir(input_dir):
        return files, dirs, [{"source": input_dir, "issue": "input directory missing"}]
    for root, subdirs, names in os.walk(input_dir):
        rel_root = rel_posix(root, input_dir)
        for d in sorted(subdirs):
            ap = os.path.join(root, d)
            dirs.append({"path": rel_posix(ap, input_dir) if rel_root != "." else d,
                         "abs": ap, "permissions": permissions_of(ap)})
        for n in sorted(names):
            ap = os.path.join(root, n)
            rel = rel_posix(ap, input_dir)
            perm = permissions_of(ap)
            rec = {"name": n, "path": rel, "source": rel, "abs": ap,
                   "permissions": perm, "size": perm.get("size"),
                   "control": rel in ("case.json", "task.md")}
            if perm.get("is_file") is False or perm.get("stat_ok") is False:
                rec["type"] = "unreadable"
                rec["errors"] = ["unreadable or stat failed"]
            else:
                rec["sha256"] = sha256_file(ap)
                rec["mime_hint"] = file_mime_hint(n)
                rec["type"] = "file"
                if rec["sha256"] is None:
                    rec["type"] = "unreadable"
                    rec["errors"] = ["sha256 failed (unreadable)"]
                    degradations.append({"source": rel, "issue": "sha256 failed"})
            files.append(rec)
    files.sort(key=lambda r: r["path"])
    dirs.sort(key=lambda r: r["path"])
    return files, dirs, degradations


# ---------------------------------------------------------------------------
# 文本抽取
# ---------------------------------------------------------------------------

def extract_text_layer(f):
    name = f["name"]
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if f.get("type") == "unreadable":
        return None, False, False, False, None
    textish = (".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml",
               ".bib", ".ris", ".enw", ".xml", ".html", ".htm", ".tex", ".rst")
    if ext in textish:
        text, truncated, degraded = read_text(f["abs"])
        has_layer = text is not None and len(text.strip()) > 0
        return text, truncated, degraded, has_layer, None
    if ext == ".pdf":
        try:
            proc = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", f["abs"], "-"],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20)
            if proc.returncode == 0 and proc.stdout.strip():
                text = proc.stdout.decode("utf-8", errors="replace")[:MAX_TEXT_BYTES]
                return text, len(proc.stdout) > MAX_TEXT_BYTES, False, True, None
        except (OSError, subprocess.SubprocessError):
            pass
        return None, False, False, False, {
            "source": f["path"],
            "issue": "no text layer (scanned/encrypted pdf or pdftotext unavailable)"}
    return None, False, False, False, None


# ---------------------------------------------------------------------------
# 强信号抽取（BibTeX / RIS / 文件名）
# ---------------------------------------------------------------------------

def _strip_braces(s):
    return re.sub(r"[{}\"]", "", s or "").strip()


def extract_bibtex_decisions(text, locator_base):
    out = []
    if not text:
        return out
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\n{]+),", text):
        etype = m.group(1).lower()
        citekey = m.group(2).strip()
        # 取该条目的成对大括号体
        i = m.end() - 1
        depth = 0
        start = i
        body = ""
        while i < len(text):
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    body = text[start + 1:i]
                    break
            i += 1
        fields = {}
        for fm in re.finditer(r"(\w+)\s*=\s*[{\"]([^}\"]*)[}\"]", body or ""):
            fields[fm.group(1).lower()] = _strip_braces(fm.group(2))
        authors = fields.get("author")
        first_author = None
        if authors:
            parts = [p.strip() for p in re.split(r"\band\b|;|,", authors, flags=re.I) if p.strip()]
            first_author = (parts[0].split(",")[0]).strip() if parts else authors
        outc = {
            "document_type": _BIB_TYPE_MAP.get(etype, "other"),
            "title": fields.get("title"),
            "authors": authors,
            "first_author": first_author,
            "year": fields.get("year"),
            "journal": fields.get("journal") or fields.get("booktitle"),
            "doi": fields.get("doi"),
            "evidence": [{"locator": "%s#bibtex:%s" % (locator_base, citekey or etype),
                          "quote": (body or "")[:400].strip(), "extractor": "bibtex"}],
        }
        out.append({k: v for k, v in outc.items() if v not in (None, "")})
    return out


def extract_ris_decisions(text, locator_base):
    out = []
    if not text:
        return out
    for block in re.split(r"ER\s*-\s*\n?", text):
        if not block.strip():
            continue
        fields = {}
        for line in block.splitlines():
            if len(line) >= 6 and line[:2].isalpha() and line[2] == " " and line[3] == "-" and line[4] == " ":
                fields.setdefault(line[:2].upper(), []).append(line[6:].strip())
        if not fields:
            continue
        get = lambda t: fields.get(t, [None])[0]
        authors = fields.get("AU", []) or fields.get("A1", [])
        outc = {
            "document_type": _RIS_TYPE_MAP.get((get("TY") or "GEN").upper(), "other"),
            "title": get("TI") or get("T1"),
            "authors": "; ".join(authors) if authors else None,
            "first_author": (authors[0].split(",")[0].strip() if authors else None),
            "year": (get("PY") or get("Y1") or "")[:4] or None,
            "journal": get("JO") or get("JF") or get("T2"),
            "doi": get("DO"),
            "evidence": [{"locator": locator_base + "#ris", "quote": block.strip()[:400],
                          "extractor": "ris"}],
        }
        out.append({k: v for k, v in outc.items() if v not in (None, "")})
    return out


def extract_filename_hints(name):
    hints = {"year": None, "arxiv_id": None, "doi": None}
    m = re.search(r"\b(19|20)\d{2}\b", name)
    if m:
        hints["year"] = m.group(0)
    m = re.search(r"\b(\d{4})\.(\d{4,5})(?:v\d+)?\b", name)
    if m:
        hints["arxiv_id"] = "%s.%s" % (m.group(1), m.group(2))
    m = re.search(r"\b(10\.\d{4,9}/[^\s\\/]+)\b", name)
    if m:
        hints["doi"] = m.group(1).rstrip(".,;")
    return hints


# ---------------------------------------------------------------------------
# 分类 / 命名
# ---------------------------------------------------------------------------

def sanitize_token(token, max_len=60):
    if token is None:
        return ""
    s = unicodedata.normalize("NFKC", str(token))
    s = "".join(ch for ch in s if unicodedata.category(ch)[0] != "C")
    for ch in ("/", "\\", ":", "*", "?", '"', "<", ">", "|", "\n", "\r", "\t"):
        s = s.replace(ch, "_" if ch not in ("\n", "\r", "\t") else " ")
    s = re.sub(r"\s+", " ", s).strip(" ._-")
    return s[:max_len].rstrip(" ._-").strip()


def normalize_title_like(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", str(s)).lower()
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def suggest_name(meta, fallback_stem):
    author = meta.get("first_author")
    year = meta.get("year")
    title = meta.get("title")
    doi = meta.get("doi")
    if author and year and title:
        base = "%s-%s-%s" % (sanitize_token(author, 40), year, sanitize_token(title, 60))
    elif author and year:
        base = "%s-%s" % (sanitize_token(author, 60), year)
    elif title and year:
        base = "%s-%s" % (year, sanitize_token(title, 60))
    elif author and title:
        base = "%s-%s" % (sanitize_token(author, 40), sanitize_token(title, 60))
    elif title:
        base = sanitize_token(title, 80)
    elif doi:
        base = sanitize_token(doi.replace("/", "_"), 80)
    else:
        base = sanitize_token(fallback_stem, 80)
    return base or sanitize_token(fallback_stem, 80) or "unnamed"


def detect_doc_type(name, relpath, text_head=None):
    low_name = name.lower()
    low_path = relpath.lower()
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext in (".csv", ".xlsx", ".xls", ".tsv", ".h5", ".npy", ".npz", ".nc", ".fcs", ".hdf5"):
        return "dataset"
    if text_head:
        head = text_head[:4000].lower()
        if any(k in head for k in ("supporting information", "supplementary material")):
            return "supplementary"
        if "submitted to" in head or "under review" in head:
            return "preprint"
    for kind, hints in _TYPE_HINTS:
        if ext and ext in hints:
            return kind
    for kind, hints in _TYPE_HINTS:
        for h in hints:
            if h in low_name or h in low_path:
                return kind
    if ext == ".pdf":
        return "journal_article"  # 保守默认，需文本核验
    return "other"


# ---------------------------------------------------------------------------
# 决策合并与 catalog 合成
# ---------------------------------------------------------------------------

def merge_decision(f_entry, decision, idx):
    applied = []
    ev = decision.get("evidence") or []
    verified = bool(ev) and all(
        isinstance(e, dict) and e.get("locator") and e.get("quote") for e in ev)
    scope_fields = ("document_type", "topic", "version_role")
    for field in ("title", "authors", "author", "first_author", "year", "journal",
                  "doi", "arxiv_id", "document_type", "topic", "tags", "version_role"):
        if field not in decision:
            continue
        val = decision[field]
        if val is None or val == "" or val == []:
            continue
        if not verified:
            applied.append({"source": f_entry["path"], "field": field, "reason": "no evidence"})
            f_entry.setdefault("_unverified", []).append(field)
            continue
        if field in ("authors", "author"):
            f_entry["authors"] = val
        elif field == "tags":
            f_entry["tags"] = val if isinstance(val, list) else [val]
        elif field in scope_fields:
            f_entry[field] = val
        else:
            f_entry.setdefault("metadata", {})
            f_entry["metadata"][field] = val
        f_entry.setdefault("evidence", [])
        for e in ev:
            f_entry["evidence"].append({"field": field, **e, "decision_index": idx})
    return f_entry, applied


def build_catalog(files, decisions_by_path, degrade):
    catalog = {}
    applied_unverified = []
    for f in files:
        e = dict(f)
        e.setdefault("metadata", {})
        e.setdefault("tags", [])
        e.setdefault("evidence", [])
        e.setdefault("document_type", None)
        e.setdefault("version_role", None)
        e.setdefault("has_text_layer", None)
        text, truncated, degraded_enc, has_layer, deg = extract_text_layer(f)
        e["has_text_layer"] = has_layer
        e["text_truncated"] = truncated
        if degraded_enc:
            degrade.append({"source": f["path"], "issue": "replaced/fallback encoding"})
            e.setdefault("errors", []).append("fallback encoding used")
        if deg:
            degrade.append(deg)
            e.setdefault("errors", []).append(deg["issue"])
        _img = (".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".bmp")
        if has_layer is False and f["name"].lower().endswith((".pdf",) + _img):
            if e["document_type"] in (None, "other", "journal_article"):
                e["document_type"] = "scanned_document"
            e["evidence"].append({"field": "has_text_layer", "locator": "file:" + f["path"],
                                  "quote": "no extractable text layer",
                                  "derivation": "pdftotext/direct read returned empty"})
        decisions = decisions_by_path.get(f["path"], [])
        if not decisions and text is not None and has_layer:
            if f["name"].lower().endswith(".bib"):
                decisions = extract_bibtex_decisions(text, f["path"])
            elif f["name"].lower().endswith(".ris"):
                decisions = extract_ris_decisions(text, f["path"])
        for idx, decision in enumerate(decisions):
            e, au = merge_decision(e, decision, idx)
            applied_unverified.extend(au)
        fn_hints = extract_filename_hints(f["name"])
        if e["document_type"] is None:
            e["document_type"] = detect_doc_type(f["name"], f["path"], text_head=text)
            e["evidence"].append({"field": "document_type", "locator": "filename:" + f["path"],
                                  "quote": f["name"],
                                  "derivation": "extension/filename/path heuristic; conservative default"})
        if "year" not in e["metadata"] and fn_hints["year"] and not decisions:
            e["metadata"]["year"] = fn_hints["year"]
            e["metadata"]["year_derivation"] = "filename-hint"
            e["evidence"].append({"field": "year", "locator": "filename:" + f["path"],
                                  "quote": fn_hints["year"], "derivation": "filename-hint"})
        if "doi" not in e["metadata"] and fn_hints["doi"] and not decisions:
            e["metadata"]["doi"] = fn_hints["doi"]
            e["metadata"]["doi_derivation"] = "filename-hint"
            e["evidence"].append({"field": "doi", "locator": "filename:" + f["path"],
                                  "quote": fn_hints["doi"], "derivation": "filename-hint"})
        if "arxiv_id" not in e["metadata"] and fn_hints["arxiv_id"] and not decisions:
            e["metadata"]["arxiv_id"] = fn_hints["arxiv_id"]
            e["metadata"]["arxiv_id_derivation"] = "filename-hint"
            e["evidence"].append({"field": "arxiv_id", "locator": "filename:" + f["path"],
                                  "quote": fn_hints["arxiv_id"], "derivation": "filename-hint"})
        catalog[f["path"]] = e
    return catalog, applied_unverified


# ---------------------------------------------------------------------------
# 去重 / 版本
# ---------------------------------------------------------------------------

def deduce_version_roles(catalog):
    for k, e in catalog.items():
        if e.get("version_role"):
            continue
        text = " ".join(ev.get("quote", "") for ev in e.get("evidence", []))
        lowt = (e.get("name", "") + " " + text).lower()
        for role, hints in _REVISION_HINTS.items():
            if any(h in lowt for h in hints):
                e["version_role"] = role
                break
        else:
            e["version_role"] = "unknown"


def compute_duplicates(catalog):
    by_hash = {}
    for k in sorted(catalog.keys()):
        h = catalog[k].get("sha256")
        if h:
            by_hash.setdefault(h, []).append(k)
    for k in sorted(catalog.keys()):
        h = catalog[k].get("sha256")
        if not h:
            catalog[k]["duplicate_of"] = None
            catalog[k]["keep"] = True
            continue
        first = min(by_hash[h])
        if k == first:
            catalog[k]["duplicate_of"] = None
            catalog[k]["keep"] = True
        else:
            catalog[k]["duplicate_of"] = first
            catalog[k]["keep"] = False


def build_version_groups(catalog):
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    keys = sorted(catalog.keys())
    doi_map, arx_map, title_map = {}, {}, {}
    for k in keys:
        m = catalog[k].get("metadata", {})
        if m.get("doi"):
            doi_map.setdefault(m["doi"].lower(), []).append(k)
        if m.get("arxiv_id"):
            arx_map.setdefault(m["arxiv_id"], []).append(k)
        title = normalize_title_like(m.get("title"))
        year = m.get("year")
        if title and year:
            title_map.setdefault((title, year), []).append(k)
    for group in list(doi_map.values()) + list(arx_map.values()) + list(title_map.values()):
        for k in group[1:]:
            union(group[0], k)
    groups = {}
    for k in keys:
        groups.setdefault(find(k), []).append(k)
    versions = []
    for members in sorted(groups.values(), key=lambda m: m[0]):
        if len(members) < 2:
            continue
        members.sort()
        basis = []
        for m in members:
            md = catalog[m].get("metadata", {})
            if md.get("doi"):
                basis.append("doi:" + md["doi"].lower())
            if md.get("arxiv_id"):
                basis.append("arxiv:" + md["arxiv_id"])
            t = normalize_title_like(md.get("title"))
            if t and md.get("year"):
                basis.append("title+year:%s|%s" % (t[:48], md["year"]))
        versions.append({
            "group_id": "vg-%d" % len(versions),
            "members": members,
            "basis": sorted(set(basis)),
            "relationships": {m: catalog[m].get("version_role") or "unknown" for m in members},
        })
    return versions


def choose_keep_dedupe(catalog):
    priority = {"published": 3, "supplementary": 2, "revision": 2, "preprint": 1, "unknown": 0}
    groups = {}
    for k, e in catalog.items():
        vg = e.get("version_group_id")
        if vg:
            groups.setdefault(vg, []).append(k)
    for members in groups.values():
        keep = max(members, key=lambda k: priority.get(catalog[k].get("version_role"), 0))
        for k in members:
            if k != keep and not catalog[k].get("duplicate_of") and catalog[k].get("version_role") != "supplementary":
                catalog[k]["archived_version_of"] = keep
                catalog[k]["keep"] = False


# ---------------------------------------------------------------------------
# 目录层级 / 操作计划 / 落盘
# ---------------------------------------------------------------------------

def plan_hierarchy(entry, dims):
    parts = []
    for dim in dims:
        if dim == "document_type":
            parts.append(entry.get("document_type") or "other")
        elif dim in ("topic", "subject"):
            topic = entry.get("topic") or entry.get("metadata", {}).get("topic")
            parts.append(sanitize_token(topic, 40) if topic else "uncategorized")
        elif dim in ("year", "publication_year"):
            year = entry.get("metadata", {}).get("year") or "no_year"
            parts.append(str(year)[:4])
        elif dim == "first_author":
            fa = entry.get("metadata", {}).get("first_author") or entry.get("authors")
            parts.append(sanitize_token(fa, 40) if fa else "unknown_author")
        elif dim == "source":
            parts.append(sanitize_token(entry.get("source", "input"), 40))
        else:
            parts.append(sanitize_token(str(entry.get(dim, "unknown")), 40))
    return "/".join(p for p in parts if p)


def plan_operations(catalog, dims):
    ops = []
    used_names = {}
    for path in sorted(catalog.keys()):
        e = catalog[path]
        if e.get("control"):
            ops.append({"source": path, "action": "skip",
                        "reason": "control input (case.json/task.md), not a library material"})
            continue
        if e.get("type") == "unreadable":
            ops.append({"source": path, "action": "skip", "reason": "unreadable"})
            continue
        if e.get("duplicate_of") and not e.get("keep"):
            ops.append({"source": path, "action": "dedupe", "duplicate_of": e["duplicate_of"],
                        "sha256": e.get("sha256")})
            continue
        if e.get("archived_version_of"):
            ops.append({"source": path, "action": "archive_version",
                        "archived_version_of": e["archived_version_of"]})
            continue
        category_path = plan_hierarchy(e, dims)
        stem = suggest_name(e.get("metadata", {}), os.path.splitext(e["name"])[0])
        ext = ("." + e["name"].rsplit(".", 1)[-1]) if "." in e["name"] else ""
        target_name = (stem + ext)[:200]
        key = (category_path, target_name)
        if key in used_names and used_names[key] != e.get("sha256"):
            base, ext2 = os.path.splitext(target_name)
            counter = 2
            while (category_path, "%s-%d%s" % (base, counter, ext2)) in used_names:
                counter += 1
            target_name = "%s-%d%s" % (base, counter, ext2)
            e["conflict_suffix"] = counter
        used_names[key] = e.get("sha256")
        ops.append({"source": path, "action": "copy", "category_path": category_path,
                    "target_name": target_name})
        e["suggested_name"] = target_name
        e["category_path"] = category_path
    return ops


def apply_operations(ops, catalog, output_dir):
    rollback, applied, degradations = [], [], []
    library_root = os.path.join(output_dir, "library")
    try:
        os.makedirs(library_root, exist_ok=True)
    except OSError as exc:
        return rollback, applied, [{"source": library_root, "issue": "mkdir failed: %s" % exc}]
    for op in ops:
        src_rel = op["source"]
        e = catalog.get(src_rel, {})
        src_abs = e.get("abs")
        if op.get("action") != "copy":
            applied.append(op)
            continue
        if not src_abs or not os.path.isfile(src_abs):
            degradations.append({"source": src_rel, "issue": "source missing at apply"})
            op["state"] = "failed"
            applied.append(op)
            continue
        dest_rel = op["category_path"] + "/" + op["target_name"]
        dest_abs = os.path.join(library_root, *dest_rel.split("/"))
        dest_dir = os.path.dirname(dest_abs)
        try:
            os.makedirs(dest_dir, exist_ok=True)
        except OSError as exc:
            degradations.append({"source": dest_dir, "issue": "mkdir failed: %s" % exc})
            op["state"] = "failed"
            applied.append(op)
            continue
        if os.path.exists(dest_abs):
            if sha256_file(dest_abs) == e.get("sha256"):
                op["state"] = "already_present"
                applied.append(op)
                continue
            base, ext2 = os.path.splitext(op["target_name"])
            counter = 2
            while os.path.exists(os.path.join(dest_dir, "%s-%d%s" % (base, counter, ext2))):
                counter += 1
            dest_abs = os.path.join(dest_dir, "%s-%d%s" % (base, counter, ext2))
            op["resolved_name"] = os.path.basename(dest_abs)
        try:
            shutil.copy2(src_abs, dest_abs)
        except (OSError, shutil.SameFileError) as exc:
            degradations.append({"source": src_rel, "issue": "copy failed: %s" % exc})
            op["state"] = "failed"
            applied.append(op)
            continue
        op["state"] = "copied"
        op["dest"] = os.path.relpath(dest_abs, os.path.dirname(output_dir)).replace(os.sep, "/")
        rollback.append({"dest": op["dest"], "created": True, "source": src_rel})
        applied.append(op)
    return rollback, applied, degradations


# ---------------------------------------------------------------------------
# 契约与结果组装
# ---------------------------------------------------------------------------

def build_result_document(case_id, contract, data):
    result = {"case_id": case_id}
    for key, value in contract.items():
        result[key] = data.get(key) if key in data else deep_placeholder(value)
    for extra in EXTRAS:
        result.setdefault(extra, data.get(extra))
    return result


def read_case_context(input_dir, task_arg):
    case, deg = read_json(os.path.join(input_dir, "case.json"))
    case_id = "unknown"
    contract = None
    task_excerpt = None
    if isinstance(case, dict):
        case_id = (case.get("case_id") or case.get("id") or case.get("challenge_id")
                   or case.get("armiId") or "unknown")
        contract = (case.get("output_contract") or case.get("outputContract")
                    or case.get("output_schema") or case.get("result_contract")
                    or case.get("contract"))
        if isinstance(case.get("task"), str):
            task_excerpt = case["task"][:500]
    if not isinstance(contract, dict) or not contract:
        contract = DEFAULT_CONTRACT
    task_path = task_arg or os.path.join(input_dir, "task.md")
    if os.path.isfile(task_path):
        raw = _read_bytes(task_path)
        if raw:
            for enc in UTF8_ENCODINGS:
                try:
                    task_excerpt = task_excerpt or raw.decode(enc)[:500]
                    break
                except UnicodeDecodeError:
                    continue
            else:
                task_excerpt = task_excerpt or raw.decode(FALLBACK_ENCODING, errors="replace")[:500]
    return case_id, contract, task_excerpt, case, (deg or [])


def read_decisions(path):
    doc, _ = read_json(path)
    if isinstance(doc, dict):
        by_path = doc.get("decisions") or doc.get("items") or {}
        if isinstance(by_path, dict):
            return by_path, None
        return {}, {"source": path, "issue": "decisions.items not an object"}
    return {}, {"source": path, "issue": "decisions missing/invalid"}


def build_library(catalog, versions, hierarchy_dims):
    catalog_list = []
    for path in sorted(catalog.keys()):
        e = dict(catalog[path])
        e.pop("_unverified", None)
        e.pop("abs", None)
        e.pop("permissions", None)
        e["source"] = path
        catalog_list.append(e)
    return {
        "catalog": catalog_list,
        "hierarchy": {"root": "library", "dimensions": hierarchy_dims},
        "metadata": {p: catalog[p].get("metadata", {}) for p in sorted(catalog.keys())},
        "versions": versions,
    }


def build_evidence(catalog, dirs):
    hashes = {p: catalog[p].get("sha256") for p in sorted(catalog.keys())}
    permissions = {d["path"]: d["permissions"] for d in dirs}
    permissions.update({p: catalog[p].get("permissions", {}) for p in sorted(catalog.keys())})
    locators = []
    for p in sorted(catalog.keys()):
        for ev in catalog[p].get("evidence", []):
            locators.append({"source": p, "field": ev.get("field"), "locator": ev.get("locator"),
                             "quote": ev.get("quote")})
    return {"hashes": hashes, "permissions": permissions, "locators": locators}


def compute_failures(files, catalog, ops, rollback, applied_unverified):
    skipped, conflicts, perm_errors, duplicates, recoveries = [], [], [], [], []
    for f in files:
        perm = f.get("permissions") or {}
        if f.get("type") == "unreadable" or perm.get("stat_ok") is False or perm.get("readable") is False:
            perm_errors.append({"source": f.get("path"),
                                "error": (f.get("errors") or ["unreadable"])[0] if f.get("errors") else "unreadable",
                                "permissions": perm})
        if f.get("errors"):
            skipped.append({"source": f.get("path"), "errors": f["errors"]})
    for path in sorted(catalog.keys()):
        e = catalog[path]
        if e.get("duplicate_of"):
            duplicates.append({"source": path, "duplicate_of": e["duplicate_of"],
                               "sha256": e.get("sha256")})
    for op in ops:
        if op.get("conflict_suffix"):
            conflicts.append({"target": (op.get("category_path") or "") + "/" + op.get("target_name", ""),
                              "sources": [op["source"]],
                              "note": "same target name, different content; resolved by -N suffix"})
        if op.get("action") == "skip" or op.get("state") == "failed":
            skipped.append({"source": op.get("source"), "reason": op.get("reason") or op.get("state")})
    for u in applied_unverified:
        skipped.append({"source": u.get("source"), "reason": "decision without evidence",
                        "field": u.get("field")})
    recoveries = list(rollback) if rollback else [
        {"note": "dry-run: no files copied; /input untouched; nothing to roll back"}]
    return skipped, conflicts, perm_errors, duplicates, recoveries


def determine_status(library_files, degradations, input_exists, unverified):
    if not input_exists:
        return "input_error"
    if not library_files:
        return "insufficient_evidence"
    if degradations or unverified:
        return "partial"
    return "ok"


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------

def run(args):
    start = time.time()
    change_log = []
    errors, degradations, pending_human = [], [], []
    runtime = {"started": utcnow_iso(), "version": __version__}

    input_dir = args.input
    output_dir = args.output
    work_dir = args.work
    input_exists = os.path.isdir(input_dir)

    case_id, contract, task_excerpt, case, case_deg = read_case_context(input_dir, args.task)
    degradations.extend(case_deg)
    if not input_exists:
        degradations.append({"source": input_dir, "issue": "input directory missing"})
    elif not isinstance(case, dict) and os.path.isfile(os.path.join(input_dir, "case.json")):
        degradations.append({"source": "case.json",
                             "issue": "unreadable or invalid; contract fell back to default"})

    empty_lib = {"catalog": [], "hierarchy": {}, "metadata": {}, "versions": []}
    empty_ev = {"hashes": {}, "permissions": {}, "locators": []}
    empty_fail = {"skipped": [], "conflicts": [], "permission_errors": [],
                  "duplicates": [], "recoveries": []}
    skeleton_data = {
        "library": empty_lib, "evidence": empty_ev, "operations": [], "failures": empty_fail,
        "status": "partial",
        "sources": [{"source": "case.json", "locator": "case_id", "quote": None}] if input_exists else [],
        "uncertainty": {"uncategorized": 0, "missing_fields": 0, "no_text_layer": 0},
        "change_log": [{"ts": utcnow_iso(), "event": "skeleton written"}],
        "errors": [], "degradations": degradations, "pending_human": pending_human,
        "runtime": dict(runtime),
    }
    skeleton = build_result_document(case_id, contract, skeleton_data)
    os.makedirs(output_dir, exist_ok=True)
    write_json(os.path.join(output_dir, "result.json"), skeleton)
    os.makedirs(work_dir, exist_ok=True)
    change_log.append({"ts": utcnow_iso(), "event": "skeleton committed to result.json"})

    if not input_exists:
        runtime.update({"finished": utcnow_iso(), "seconds": round(time.time() - start, 3)})
        final = build_result_document(case_id, contract, {
            "library": empty_lib, "evidence": empty_ev, "operations": [], "failures": empty_fail,
            "status": "input_error", "sources": skeleton_data["sources"],
            "uncertainty": skeleton_data["uncertainty"], "change_log": change_log,
            "errors": errors, "degradations": degradations, "pending_human": pending_human,
            "runtime": runtime,
        })
        write_json(os.path.join(output_dir, "result.json"), final)
        return 0, "input_error"

    files, dirs, inv_deg = discover(input_dir)
    degradations.extend(inv_deg)
    library_files = [f for f in files if not f.get("control")]
    change_log.append({"ts": utcnow_iso(),
                       "event": "inventory: %d files (%d materials), %d dirs"
                       % (len(files), len(library_files), len(dirs))})

    decisions_by_path, decision_deg = read_decisions(args.decisions) if args.decisions else ({}, None)
    if decision_deg:
        degradations.append(decision_deg)

    catalog, applied_unverified = build_catalog(files, decisions_by_path, degradations)
    deduce_version_roles(catalog)
    compute_duplicates(catalog)
    versions = build_version_groups(catalog)
    for vg in versions:
        for m in vg["members"]:
            catalog[m]["version_group_id"] = vg["group_id"]
    choose_keep_dedupe(catalog)

    hierarchy_dims = [d for d in (args.hierarchy or "document_type,topic").split(",") if d]
    ops = plan_operations(catalog, hierarchy_dims)

    write_json(os.path.join(work_dir, "inventory.json"),
               {"case_id": case_id, "files": files, "dirs": dirs, "generated_at": utcnow_iso()})
    write_json(os.path.join(work_dir, "organization.json"),
               {"case_id": case_id, "hierarchy_dims": hierarchy_dims,
                "catalog": build_library(catalog, versions, hierarchy_dims),
                "operations": ops, "sources": skeleton_data["sources"], "generated_at": utcnow_iso()})

    rollback = []
    if args.apply:
        rollback, ops, apply_deg = apply_operations(ops, catalog, output_dir)
        degradations.extend(apply_deg)
        write_json(os.path.join(work_dir, "rollback.json"),
                   {"case_id": case_id, "created": rollback, "generated_at": utcnow_iso()})
        change_log.append({"ts": utcnow_iso(), "event": "apply: %d files copied" % len(rollback)})

    skipped, conflicts, perm_errors, duplicates, recoveries = compute_failures(
        files, catalog, ops, rollback, applied_unverified)

    material_paths = [p for p in catalog if not catalog[p].get("control")]
    uncategorized = sum(1 for p in material_paths
                        if (catalog[p].get("document_type") or "other") == "other")
    missing_fields = sum(1 for p in material_paths
                         if not any(catalog[p].get("metadata", {}).get(f)
                                    for f in ("title", "authors", "year")))
    no_text_layer = sum(1 for p in material_paths if catalog[p].get("has_text_layer") is False)
    uncertainty = {"uncategorized": uncategorized, "missing_fields": missing_fields,
                   "no_text_layer": no_text_layer}
    for u in applied_unverified:
        pending_human.append({"source": u.get("source"), "field": u.get("field"),
                              "reason": u.get("reason", "no evidence")})
    for p in sorted(catalog.keys()):
        for fname in catalog[p].get("_unverified", []):
            pending_human.append({"source": p, "field": fname, "reason": "decision without evidence"})

    sources = [{"source": "case.json", "locator": "case_id", "quote": str(case_id)}]
    if task_excerpt:
        sources.append({"source": "task.md", "locator": "task.md:1", "quote": task_excerpt[:200]})

    failures = {"skipped": skipped, "conflicts": conflicts, "permission_errors": perm_errors,
                "duplicates": duplicates, "recoveries": recoveries}

    status = determine_status(library_files, degradations, input_exists, applied_unverified)

    library = build_library(catalog, versions, hierarchy_dims)
    evidence = build_evidence(catalog, dirs)

    runtime.update({"finished": utcnow_iso(), "seconds": round(time.time() - start, 3),
                    "files": len(files), "materials": len(library_files), "copied": len(rollback)})
    change_log.append({"ts": utcnow_iso(), "event": "final result committed; status=%s" % status})

    data = {
        "library": library, "evidence": evidence, "operations": ops, "failures": failures,
        "status": status, "sources": sources, "uncertainty": uncertainty, "change_log": change_log,
        "errors": errors, "degradations": degradations, "pending_human": pending_human,
        "runtime": runtime,
    }
    write_json(os.path.join(output_dir, "result.json"), build_result_document(case_id, contract, data))
    return 0, status


def build_parser():
    p = argparse.ArgumentParser(prog="main.py",
                                description="科研资料整理编排入口：盘点、分类、命名、去重、版本区分。")
    p.add_argument("--input", default="/input", help="输入目录（只读，绝不修改）")
    p.add_argument("--output", default="/app/output", help="最终产物目录")
    p.add_argument("--work", default="/app/work", help="中间产物目录")
    p.add_argument("--task", default=None, help="task.md 路径，缺省 /input/task.md")
    p.add_argument("--decisions", default=None, help="语义决策 JSON（含逐字段证据）")
    p.add_argument("--hierarchy", default="document_type,topic", help="目录层级维度，逗号分隔")
    p.add_argument("--apply", action="store_true", help="实际复制整理结果到 output/library；缺省干跑")
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        code, _status = run(args)
    except Exception as exc:
        output_dir = args.output
        try:
            os.makedirs(output_dir, exist_ok=True)
            doc = {"case_id": "unknown", "status": "input_error",
                   "errors": [{"type": type(exc).__name__, "message": str(exc)}],
                   "degradations": [], "pending_human": [], "sources": [],
                   "uncertainty": {}, "change_log": [{"ts": utcnow_iso(), "event": "exception fallback"}],
                   "runtime": {"finished": utcnow_iso()},
                   "library": {}, "evidence": {}, "operations": [], "failures": {}}
            write_json(os.path.join(output_dir, "result.json"), doc)
        except OSError:
            pass
        sys.stderr.write("%s: %s\n" % (type(exc).__name__, exc))
        return 0
    return code


if __name__ == "__main__":
    sys.exit(main())
