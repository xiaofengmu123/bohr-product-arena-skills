#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.py — sci-library-curator 核心库（盘点/分类/命名/去重/版本）。

仅依赖 Python 3 标准库（>=3.8）。全部函数确定性执行（固定排序、不读 wall-clock、
不随机），错误写入返回值而非抛到顶层；供 main.py 与 selftest.py 复用。
约定：跨平台相对路径一律用 "/" 分隔，保证同一输入在不同机器上产物一致。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import unicodedata

TEXT_MAX_BYTES = 2 * 1024 * 1024  # 文本类单文件读取上限（2MB）

# ---------------------------------------------------------------------------
# 文本 / JSON 读写
# ---------------------------------------------------------------------------

def read_text(path):
    """utf-8-sig -> utf-8 -> latin-1+replace 回退；超限截断并注明。"""
    if not os.path.isfile(path):
        return None, "missing"
    size = os.path.getsize(path)
    truncated = size > TEXT_MAX_BYTES
    try:
        with open(path, "rb") as f:
            raw = f.read(TEXT_MAX_BYTES)
    except OSError as exc:
        return None, "unreadable: %s" % exc
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), ("truncated" if truncated else "ok")
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace"), ("latin1-fallback" if truncated else "latin1")


def read_json(path):
    txt, note = read_text(path)
    if txt is None:
        return None, note
    try:
        return json.loads(txt), note
    except (json.JSONDecodeError, ValueError) as exc:
        return None, "invalid-json: %s" % exc


def write_json(path, obj):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def file_sha256(path, chunk=1024 * 1024):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                block = f.read(chunk)
                if not block:
                    break
                h.update(block)
        return h.hexdigest(), None
    except OSError as exc:
        return None, "unreadable: %s" % exc


def inspect_permissions(path):
    info = {"readable": os.access(path, os.R_OK),
            "writable": os.access(path, os.W_OK), "stat_ok": True}
    try:
        st = os.stat(path)
        info["size_bytes"] = st.st_size
        info["mode_octal"] = oct(st.st_mode & 0o777)
    except OSError as exc:
        info["stat_ok"] = False
        info["size_bytes"] = None
        info["error"] = str(exc)
    return info


# ---------------------------------------------------------------------------
# 文件类型
# ---------------------------------------------------------------------------

EXT_KIND = {
    ".pdf": "pdf", ".txt": "text", ".md": "markdown", ".markdown": "markdown",
    ".csv": "csv", ".tsv": "tsv", ".json": "json", ".jsonl": "jsonl",
    ".xml": "xml", ".bib": "bibtex", ".ris": "ris", ".yaml": "yaml", ".yml": "yaml",
    ".doc": "doc", ".docx": "docx", ".xls": "xls", ".xlsx": "xlsx",
    ".ppt": "pptx-viewer", ".pptx": "pptx", ".odt": "opendoc",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image",
    ".tif": "image", ".tiff": "image", ".bmp": "image", ".svg": "svg",
    ".zip": "archive", ".tar": "archive", ".gz": "archive", ".7z": "archive",
    ".py": "code", ".ipynb": "notebook", ".r": "code", ".rtf": "rtf",
    ".html": "html", ".htm": "html", ".tex": "tex",
}

TEXT_LIKE = {"text", "markdown", "csv", "tsv", "json", "jsonl", "xml",
             "bibtex", "ris", "yaml", "code", "notebook", "tex", "html", "rtf", "svg"}


def file_kind(name):
    return EXT_KIND.get(os.path.splitext(name)[1].lower(), "other")


def kind_is_text(kind):
    return kind in TEXT_LIKE


# ---------------------------------------------------------------------------
# 文件名强信号（弱信号不猜）
# ---------------------------------------------------------------------------

DOC_TYPE_HINTS = [
    ("supplementary", ["supplement", "supplementary", "supporting", "appendix",
                       "补充材料", "附录", "data_s1", "_si", "si_"]),
    ("thesis", ["dissertation", "thesis", "学位论文", "博士论文", "硕士论文", "phd"]),
    ("preprint", ["preprint", "pre-print", "arxiv", "预印本"]),
    ("conference_paper", ["conference", "conf_", "proceeding", "会议", "会议论文"]),
    ("dataset", ["dataset", "data_set", "数据集", "_data", "data-", "data_"]),
    ("notes", ["notes", "note_", "笔记", "memo", "_notes"]),
    ("book", ["book", "monograph", "专著", "_chapter", "chapter_", "图书"]),
    ("report", ["report", "techreport", "技术报告", "_report"]),
    ("scan", ["scan", "scanned", "扫描", "扫描件"]),
]

TOPIC_HINTS = [
    ("neuroscience", ["neuro", "brain", "cortex", "神经元", "脑"]),
    ("genomics", ["genom", "genetic", "gene", "dna", "基因组", "基因"]),
    ("materials", ["material", "polymer", "alloy", "材料", "合金", "聚合物"]),
    ("climate", ["climate", "warming", "气候", "气象"]),
    ("cancer", ["cancer", "tumor", "oncology", "肿瘤", "癌症"]),
    ("ml", ["machine-learning", "deep-learning", "neural", "机器学习", "深度学习"]),
    ("chemistry", ["chem", "化学", "synthesis", "合成"]),
    ("physics", ["phys", "物理", "quantum", "量子"]),
    ("medicine", ["medic", "clinical", "医学", "临床"]),
]


def filename_hints(name):
    low = unicodedata.normalize("NFKC", name).lower()
    dtype = next((c for c, subs in DOC_TYPE_HINTS if any(s in low for s in subs)), None)
    topic = next((c for c, subs in TOPIC_HINTS if any(s in low for s in subs)), None)
    tags = [c for c, subs in TOPIC_HINTS if any(s in low for s in subs)]
    return dtype, topic, tags


YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")


def extract_year(text):
    m = YEAR_RE.search(text or "")
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# BibTeX / RIS 解析
# ---------------------------------------------------------------------------

def _strip_braces(s):
    s = (s or "").strip()
    s = re.sub(r"^[\{\"\']+|[\{\"\']+$", "", s)
    s = re.sub(r"[{}\\]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_bib(path):
    txt, _ = read_text(path)
    if txt is None:
        return []
    entries = []
    pattern = re.compile(r"@\s*([a-zA-Z]+)\s*\{\s*([^,\n]+)\s*,", re.S)
    for m in pattern.finditer(txt):
        entry_type = m.group(1).lower()
        cite_key = m.group(2).strip()
        end = txt.find("\n@", m.end())
        if end == -1:
            end = len(txt)
        block = txt[m.end():end]
        out = {"cite_key": cite_key, "entry_type": entry_type}
        for field in ("title", "author", "journal", "year", "doi", "eprint"):
            fm = re.search(r"\b%s\s*=\s*[\{\"]?(.*?)[\}\"]?[,}\n]" % field, block, re.S | re.I)
            if fm:
                out[field] = _strip_braces(fm.group(1))
        entries.append(out)
    return entries


def parse_ris_all(path):
    txt, _ = read_text(path)
    if txt is None:
        return []
    entries = []
    cur = None
    for line in txt.splitlines():
        if line.startswith("TY  -"):
            if cur is not None:
                entries.append(cur)
            cur = {"entry_type": line[6:].strip().lower(), "cite_key": None}
        elif cur is not None:
            if line.startswith("TI  -"):
                cur["title"] = (cur.get("title") or "") + " " + line[6:].strip()
            elif line.startswith("AU  -"):
                cur.setdefault("authors_list", []).append(line[6:].strip())
            elif line.startswith("PY  -"):
                cur["year"] = line[6:].strip()
            elif line.startswith(("JO  -", "JF  -", "T2  -")):
                cur["journal"] = line[6:].strip()
            elif line.startswith("DO  -"):
                cur["doi"] = line[6:].strip()
    if cur is not None:
        entries.append(cur)
    for e in entries:
        e.setdefault("title", "")
        e["authors"] = e.pop("authors_list", [])
    return entries


# ---------------------------------------------------------------------------
# 命名与归一化
# ---------------------------------------------------------------------------

def slugify(s, maxlen=80):
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = re.sub(r"[^\w\-.\s]+", "_", s, flags=re.UNICODE)
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"_+", "_", s)
    s = s.replace("/", "_").replace("\\", "_")
    s = s.strip("._ \t")
    return s[:maxlen].rstrip("._")


def normal_stem(s):
    s = unicodedata.normalize("NFKC", (s or "").lower())
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s)


def first_author_surname(md):
    authors = md.get("authors") or md.get("author") or []
    if isinstance(authors, str):
        authors = [a.strip() for a in re.split(r"\s+and\s+|;|,", authors) if a.strip()]
    if not authors:
        return None
    a = _strip_braces(authors[0])
    parts = a.split()
    return parts[-1] if parts else None


def build_filename(md, orig_stem, ext, name_template="author-year-title"):
    surname = first_author_surname(md)
    year = md.get("year")
    title = md.get("title")
    doi = md.get("doi")
    if name_template == "doi" and doi:
        base = "DOI_" + slugify(doi, 60)
    elif surname and year and title:
        base = "%s_%s_%s" % (surname, year, slugify(title, 60))
    elif surname and year:
        base = "%s_%s" % (surname, year)
    elif title:
        base = slugify(title, 80)
    elif doi:
        base = "DOI_" + slugify(doi, 60)
    else:
        base = slugify(orig_stem, 80) or "unknown"
    base = base.strip("._-") or "unknown"
    if ext:
        ext = ext if ext.startswith(".") else "." + ext
        return base + ext, base
    return base, base


# ---------------------------------------------------------------------------
# 版本组（并查集：DOI / arXiv / 标题+年份）
# ---------------------------------------------------------------------------

def assign_version_groups(entries):
    parent = {e["source"]: e["source"] for e in entries}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    keymap = {}
    for e in entries:
        keys = []
        if e.get("doi"):
            keys.append(("doi", normal_stem(e["doi"])))
        if e.get("arxiv_id"):
            keys.append(("arxiv", normal_stem(e["arxiv_id"])))
        if e.get("norm_title_year"):
            keys.append(("title-year", e["norm_title_year"]))
        for k in keys:
            if k in keymap:
                union(e["source"], keymap[k])
            else:
                keymap[k] = e["source"]
    groups = {}
    for e in entries:
        groups.setdefault(find(e["source"]), []).append(e["source"])
    out = {}
    idx = 1
    for root in sorted(groups):
        members = groups[root]
        if len(members) >= 2:
            gid = "v%d" % idx
            for m in members:
                out[m] = gid
            idx += 1
    return out


# ---------------------------------------------------------------------------
# 盘点
# ---------------------------------------------------------------------------

def inventory(input_dir):
    manifest = {
        "input_root": os.path.abspath(input_dir), "exists": os.path.isdir(input_dir),
        "files": [], "directories": [], "root_permissions": None,
        "total_files": 0, "total_bytes": 0, "pending": [],
    }
    if not manifest["exists"]:
        return manifest
    manifest["root_permissions"] = inspect_permissions(input_dir)
    for root, dirs, files in sorted(os.walk(input_dir), key=lambda t: t[0].replace("\\", "/")):
        dirs.sort()
        for d in dirs:
            manifest["directories"].append(os.path.relpath(os.path.join(root, d), input_dir).replace("\\", "/"))
        for name in sorted(files):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir).replace("\\", "/")
            sha, sha_err = file_sha256(full)
            kind = file_kind(name)
            perm = inspect_permissions(full)
            perr = []
            if sha is None:
                perr.append(sha_err)
            manifest["files"].append({
                "source": rel, "abs_path": full, "basename": name,
                "ext": os.path.splitext(name)[1].lower(), "kind": kind, "sha256": sha,
                "size_bytes": perm.get("size_bytes"), "permissions": perm,
                "has_text_layer": True if kind_is_text(kind) else None,
                "is_text": kind_is_text(kind), "errors": perr,
            })
            if sha:
                manifest["total_bytes"] += (perm.get("size_bytes") or 0)
    manifest["total_files"] = len(manifest["files"])
    manifest["files"] = sorted(manifest["files"], key=lambda d: d["source"])
    return manifest


# ---------------------------------------------------------------------------
# 分类 / 去重 / 版本 / 命名 -> 计划
# ---------------------------------------------------------------------------

def _decision_field(dec, field):
    if not isinstance(dec, dict) or field not in dec:
        return None, False
    ev = dec.get("evidence") or []
    return dec.get(field), bool(ev) or bool(dec.get("locator") and dec.get("quote"))


def match_bib_entry(file_stem_norm, bib_entries):
    best, best_score = None, 0
    for e in bib_entries:
        key = normal_stem(e.get("cite_key") or "")
        title = normal_stem(e.get("title") or "")
        score = 0
        if key and len(key) >= 4 and (key in file_stem_norm or file_stem_norm in key):
            score = 10
        elif title and len(title) >= 8 and (title in file_stem_norm or file_stem_norm in title):
            score = 7
        if score > best_score:
            best, best_score = e, score
    return best if best_score >= 7 else None


def classify_and_plan(manifest, decisions, hierarchy_dims, name_template):
    files = manifest["files"]
    decision_files = {}
    if isinstance(decisions, dict):
        decision_files = decisions.get("files") or decisions.get("decisions") or decisions.get("items") or {}
    if not isinstance(decision_files, dict):
        decision_files = {}

    bib_entries = []
    for f in files:
        if f["kind"] == "bibtex":
            bib_entries.extend(parse_bib(f["abs_path"]))
        elif f["kind"] == "ris":
            bib_entries.extend(parse_ris_all(f["abs_path"]))

    plan = []
    pending = list(manifest.get("pending") or [])
    for f in files:
        src = f["source"]
        dec = decision_files.get(src) or {}
        md = {}
        evid = []
        dtype_hint, topic_hint, tags = filename_hints(f["basename"])
        stem_norm = normal_stem(os.path.splitext(f["basename"])[0])

        if dtype_hint:
            evid.append({"field": "document_type", "locator": "filename:%s" % src, "quote": f["basename"]})
        if topic_hint:
            evid.append({"field": "topic", "locator": "filename:%s" % src, "quote": f["basename"]})

        bib = match_bib_entry(stem_norm, bib_entries)
        if bib:
            for k in ("title", "author", "authors", "journal", "year", "doi", "eprint"):
                if bib.get(k):
                    md[k] = bib[k]
            evid.append({"field": "metadata_from", "locator": "bibliography:cite_key=%s" % bib.get("cite_key"),
                         "quote": (bib.get("title") or bib.get("cite_key") or "")})

        dtype, dtype_ev = _decision_field(dec, "document_type")
        topic, topic_ev = _decision_field(dec, "topic")
        tags_d, tags_ev = _decision_field(dec, "tags")
        md_d, md_ev = _decision_field(dec, "metadata")
        htl, htl_ev = _decision_field(dec, "has_text_layer")

        if dtype is not None:
            if dtype_ev:
                dtype_hint = dtype
                evid.extend(dec.get("evidence") or [])
            else:
                pending.append({"source": src, "field": "document_type", "reason": "decision without evidence; ignored"})
        if topic is not None:
            if topic_ev:
                topic_hint = topic
                evid.extend(dec.get("evidence") or [])
            else:
                pending.append({"source": src, "field": "topic", "reason": "decision without evidence; ignored"})
        if isinstance(tags_d, list) and tags_ev:
            for t in tags_d:
                if t not in tags:
                    tags.append(t)
            evid.extend(dec.get("evidence") or [])
        elif isinstance(tags_d, list) and not tags_ev:
            pending.append({"source": src, "field": "tags", "reason": "decision without evidence; ignored"})
        if isinstance(md_d, dict) and md_ev:
            for k, v in md_d.items():
                if v not in (None, ""):
                    md[k] = v
            evid.extend(dec.get("evidence") or [])
        elif isinstance(md_d, dict) and not md_ev:
            pending.append({"source": src, "field": "metadata", "reason": "decision without evidence; ignored"})
        if isinstance(htl, bool):
            if htl_ev:
                f["has_text_layer"] = htl
                evid.extend(dec.get("evidence") or [])
            else:
                pending.append({"source": src, "field": "has_text_layer", "reason": "decision without evidence; ignored"})

        document_type = dtype_hint
        if document_type is None and bib and bib.get("entry_type"):
            mapping = {"article": "journal_article", "inproceedings": "conference_paper",
                       "phdthesis": "thesis", "mastersthesis": "thesis", "book": "book",
                       "incollection": "book_chapter", "misc": "misc", "techreport": "report",
                       "preprint": "preprint", "online": "misc"}
            document_type = mapping.get(bib["entry_type"], "misc")
            evid.append({"field": "document_type", "locator": "bibliography:cite_key=%s" % bib.get("cite_key"),
                         "quote": "@%s{%s" % (bib.get("entry_type"), bib.get("cite_key"))})

        topic = topic_hint or "uncategorized"
        if md.get("year") is None:
            md["year"] = extract_year(f["basename"])
        if md.get("eprint"):
            md["arxiv_id"] = md["eprint"]
        doi = md.get("doi") or None
        arxiv_id = md.get("arxiv_id") or None
        tags = sorted(set(tags))
        name, _ = build_filename(md, os.path.splitext(f["basename"])[0],
                                 f["ext"], name_template)

        levels = []
        for dim in hierarchy_dims:
            if dim in ("document_type", "type"):
                levels.append(slugify(document_type or "unclassified", 40))
            elif dim == "topic":
                levels.append(slugify(topic, 40))
            elif dim in ("year", "date"):
                levels.append(str(md.get("year") or "nodate"))
            else:
                levels.append("any")
        category_path = "/".join([x for x in levels if x])

        if document_type == "supplementary":
            role = "supplementary"
        elif document_type == "preprint" or (arxiv_id and not doi):
            role = "preprint"
        elif doi:
            role = "published"
        else:
            role = "unknown"

        norm_title_year = None
        if md.get("title") and md.get("year"):
            norm_title_year = "%s_%s" % (normal_stem(md["title"])[:60], md["year"])

        plan.append({
            "source": src, "abs_path": f["abs_path"], "sha256": f["sha256"],
            "size_bytes": f["size_bytes"], "kind": f["kind"], "document_type": document_type,
            "topic": topic, "tags": tags, "category_path": category_path,
            "suggested_name": name, "keep": True, "duplicate_of": None,
            "version_group": None, "version_role": role, "metadata": md, "doi": doi,
            "arxiv_id": arxiv_id, "norm_title_year": norm_title_year,
            "permissions": f["permissions"], "has_text_layer": f["has_text_layer"],
            "errors": f["errors"], "evidence": evid,
        })

    first_by_hash = {}
    for e in plan:
        if e["sha256"] and e["sha256"] in first_by_hash:
            e["duplicate_of"] = first_by_hash[e["sha256"]]
            e["keep"] = False
        elif e["sha256"]:
            first_by_hash[e["sha256"]] = e["source"]

    vgroups = assign_version_groups(plan)
    for e in plan:
        e["version_group"] = vgroups.get(e["source"])

    plan.sort(key=lambda d: d["source"])
    return {"plan": plan, "bib_count": len(bib_entries), "bib_entries": bib_entries}, pending


# ---------------------------------------------------------------------------
# 落盘（非破坏、幂等、可回滚）
# ---------------------------------------------------------------------------

def _resolve_target(directory, filename, sha256, used):
    target = os.path.join(directory, *filename.split("/"))
    base, ext = os.path.splitext(target)
    cand = target
    i = 2
    while True:
        if os.path.exists(cand):
            existing_sha, _ = file_sha256(cand)
            if existing_sha == sha256:
                return cand, "already_present"
            cand = "%s-%d%s" % (base, i, ext)
            i += 1
        elif cand in used:
            cand = "%s-%d%s" % (base, i, ext)
            i += 1
        else:
            break
    used[cand] = sha256
    return cand, "to_copy"


def apply_plan(org, output_dir, library_name="library"):
    library_root = os.path.join(output_dir, library_name)
    created = []
    operations = []
    used = {}
    for entry in org["plan"]:
        src = entry["abs_path"]
        op = {"source": entry["source"], "sha256": entry["sha256"],
              "action": "copy", "status": None, "target": None, "note": None}
        if not entry.get("keep", True):
            op["status"] = "skipped-duplicate"
            op["note"] = "duplicate_of=%s" % entry.get("duplicate_of")
            operations.append(op)
            continue
        if not entry["sha256"]:
            op["status"] = "skipped-unreadable"
            op["note"] = "; ".join(entry.get("errors") or []) or "no sha256"
            operations.append(op)
            continue
        rel_target = "/".join([entry["category_path"], entry["suggested_name"]]).lstrip("/")
        final_target, status = _resolve_target(library_root, rel_target, entry["sha256"], used)
        op["target"] = os.path.relpath(final_target, output_dir).replace("\\", "/")
        op["status"] = status
        if status == "to_copy":
            op["status"] = "copied"
            try:
                os.makedirs(os.path.dirname(final_target), exist_ok=True)
                shutil.copy2(src, final_target)
                created.append({"source": entry["source"], "target": op["target"],
                                "sha256": entry["sha256"], "reason": "copy"})
            except OSError as exc:
                op["status"] = "copy-failed"
                op["note"] = str(exc)
        operations.append(op)
    operations.sort(key=lambda d: (d["source"], d["action"]))
    return operations, created
