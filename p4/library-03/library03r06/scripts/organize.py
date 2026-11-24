#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""organize.py — 确定性整理引擎：分类 · 去重 · 版本 · 别名 · 索引 · 计划 · 落盘。

属于 sci-library-organizer Skill 的核心模块，仅依赖 Python 标准库。
设计要点：
1. 中间表示优先：先由原始清单推导出结构化 entries，再固化为 plan.json / graph.json /
   index.json；落盘动作只由 plan.json 驱动，审查者可在不触碰文件的前提下核对全过程。
2. 确定性：所有输出排序稳定、时间戳只落在事件层，同一输入重跑结果（除时间戳）逐字节一致。
3. 诚实性：每个被断言的语义字段都带 evidence[{locator, quote}]；无证据写 not_reported，
   绝不臆造作者/年份/DOI。
4. 可回滚：只在 --apply 下复制到输出库；/input 永不修改；rollback 由 main 收集列出。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import unicodedata

SCHEMA_VERSION = "1.0"


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def read_json(path):
    """容错读取 JSON：utf-8-sig -> utf-8 -> latin-1；返回 (obj, error)。"""
    try:
        raw = open(path, "rb").read()
    except OSError as e:
        return None, f"cannot read {path}: {e}"
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return json.loads(raw.decode(enc)), None
        except UnicodeDecodeError:
            continue
        except json.JSONDecodeError as e:
            return None, f"invalid JSON ({e})"
    try:
        return json.loads(raw.decode("latin-1")), None
    except Exception as e:  # noqa: BLE001
        return None, f"invalid JSON: {e}"


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def read_text_robust(path, max_bytes=2 * 1024 * 1024):
    """容错读文本：返回 (text, encoding, errors)；控制单文件体积。"""
    try:
        b = open(path, "rb").read(max_bytes + 1)
    except OSError as e:
        return "", "", [f"read error: {e}"]
    truncated = len(b) > max_bytes
    b = b[:max_bytes]
    errors = []
    enc = "utf-8-sig"
    try:
        text = b.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = b.decode("utf-8")
            enc = "utf-8"
        except UnicodeDecodeError:
            text = b.decode("latin-1")
            enc = "latin-1"
    if "\ufffd" in text:
        errors.append("replacement characters present; re-read as latin-1")
        text = b.decode("latin-1")
        enc = "latin-1"
    if truncated:
        errors.append(f"text truncated at {max_bytes} bytes")
    return text, enc, errors


def slugify(s, maxlen=80):
    s = unicodedata.normalize("NFKD", str(s if s is not None else ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return (s[:maxlen].rstrip("-") or "untitled")


def norm_key(s):
    s = unicodedata.normalize("NFKD", str(s if s is not None else "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def ev(locator, quote):
    return {"locator": locator, "quote": str(quote)}


def deep_placeholder(v):
    if isinstance(v, dict):
        return {k: deep_placeholder(x) for k, x in v.items()}
    if isinstance(v, list):
        return []
    return None


DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"'<>)\]]+", re.I)
ARXIV_RE = re.compile(r"(?:arXiv\s*:\s*|arxiv\.org/abs/)(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-2]\d)\b")


def _clean_doi(m):
    return m.strip(".,;)]}")


def extract_light_metadata(text, source, has_text_layer=True):
    """从文本头部抽取 title/year/doi/arxiv，全部带逐字 quote；不确定则空缺。"""
    loc = f"text:{source}"
    meta = {"title": None, "year": None, "doi": None, "arxiv_id": None,
            "has_text_layer": bool(has_text_layer)}
    evidence = []
    if not text:
        return meta, evidence
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines:
        cand = lines[0][:200]
        meta["title"] = cand
        evidence.append(ev(loc, cand))
    m = YEAR_RE.search(text)
    if m:
        meta["year"] = int(m.group(1))
        evidence.append(ev(loc, m.group(1)))
    md = DOI_RE.search(text)
    if md:
        doi = _clean_doi(md.group(0))
        meta["doi"] = doi
        evidence.append(ev(loc, doi))
    ma = ARXIV_RE.search(text)
    if ma:
        meta["arxiv_id"] = ma.group(1)
        evidence.append(ev(loc, ma.group(1)))
    return meta, evidence


DOC_TYPE_SIGNALS = [
    (r"(?i)(supplement|supplementary|supporting[_-]?(material|info)|\bsi[_-]|\bappendix\b|_supp\b)", "supplementary"),
    (r"(?i)(thesis|dissertation|\bphd\b|doctoral|\bmsc\b|master[_-]?thesis|\u535a\u58eb|\u7855\u58eb|\u5b66\u4f4d)", "thesis"),
    (r"(?i)(proceedings|conference|\bconf\b|\bworkshop\b|\bsymposium\b|\bicml\b|\bneurips\b|\bcvpr\b|\biccv\b|\beccv\b|\bacl\b|\biclr\b|\baaai\b|\bkdd\b|\bchi\b|\bsigir\b|\bsiggraph\b)", "conference_paper"),
    (r"(?i)(preprint|draft|arxiv|submitted|under[_-]?review|manuscript)", "preprint"),
    (r"(?i)(scan|scanned|ocr|\u626b\u63cf)", "scanned_document"),
    (r"(?i)(slides|presentation|poster|lecture)", "presentation"),
    (r"(?i)(readme|^notes?[_.-]|^todo[_.-]|^memo[_.-])", "note"),
]

TOPIC_KW = {
    "materials_science": ["material", "alloy", "polymer", "ceramic", "composite", "nanomaterial",
                          "nanoparticle", "thin film", "perovskite", "graphene"],
    "chemistry": ["chem", "molecule", "catalyst", "synthesis", "reaction", "spectroscopy",
                  "chromatograph", "compound"],
    "biology": ["biolog", "cell", "gene", "protein", "dna", "rna", "genom", "microbiome",
                "molecular"],
    "medicine": ["medic", "clinical", "patient", "disease", "drug", "cancer", "therapy",
                 "cohort", "epidemiolog"],
    "computer_science": ["comput", "algorithm", "machine learning", "deep learning",
                         "neural network", "neural", "software", "dataset", "language model",
                         "optimization"],
    "physics": ["physic", "quantum", "optic", "laser", "semiconductor", "particle",
                "condensed matter", "superconduct"],
    "engineering": ["engineer", "robot", "sensor", "device", "manufactur", "circuit",
                    "mechanical", "aerospace"],
    "mathematics": ["math", "theorem", "proof", "statistic", "numerical", "linear algebra"],
    "earth_science": ["geolog", "climate", "ocean", "atmospheric", "remote sensing", "gis"],
    "economics_social": ["econom", "market", "policy", "soci", "education", "psycholog"],
}

SYNONYMS = {
    "ml": "machine_learning", "machine-learning": "machine_learning",
    "deep-learning": "deep_learning", "nn": "neural_network",
    "neural": "neural_network", "nlp": "natural_language_processing",
    "nanoparticles": "nanoparticle", "nanomaterials": "nanomaterial",
    "quantum-mechanics": "quantum", "lm": "language_model",
}


def classify_type(filename, ext):
    base = filename.lower()
    ext = ext.lower()
    for pat, t in DOC_TYPE_SIGNALS:
        if re.search(pat, base):
            return t
    if ext in (".bib", ".ris", ".enw"):
        return "reference_data"
    if ext in (".csv", ".tsv", ".xlsx", ".xls", ".json", ".db", ".sqlite",
               ".h5", ".hdf5", ".npy", ".npz", ".parquet", ".dat"):
        return "dataset"
    if ext in (".txt", ".md", ".markdown", ".rst", ".org"):
        return "note"
    if ext in (".tex", ".sty", ".cls"):
        return "manuscript_source"
    if ext in (".docx", ".doc", ".pptx", ".ppt", ".odt", ".odp"):
        return "office_document"
    if ext in (".py", ".sh", ".r", ".jl", ".cpp", ".c", ".java", ".go", ".js"):
        return "code"
    if ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".svg", ".gif"):
        return "image"
    if ext in (".zip", ".tar", ".gz", ".7z", ".rar"):
        return "archive"
    if ext == ".pdf":
        return "journal_article"
    return "other"


def collect_topics_tags(filename, text):
    hay = (str(filename) + "\n" + (str(text)[:20000] or "")).lower()
    topics = []
    for topic, kws in sorted(TOPIC_KW.items()):
        hits = [kw for kw in kws if kw in hay]
        if hits:
            topics.append(topic)
    tags = list(topics)
    for kw, canon in sorted(SYNONYMS.items()):
        if kw in hay and canon not in tags:
            tags.append(canon)
    phrase_extra = [p for p in ("machine learning", "deep learning", "single cell",
                                "gene expression", "crystal structure", "phase transition")
                    if p in hay]
    for p in phrase_extra:
        if slugify(p) not in tags:
            tags.append(slugify(p))
    topics = topics[:3]
    tags = tags[:8]
    return topics, sorted(tags), [{"topic": t} for t in topics]


def parse_bibtex(text):
    """宽容解析 BibTeX：返回 [{"key","type","fields","raw"}, ...]。"""
    entries = []
    pat = re.compile(r"@\s*(\w+)\s*[{(]\s*([^,{]+)\s*,", re.I)
    for m in pat.finditer(text):
        at = m.start()
        nxt = text.find("@", at + 1)
        body = text[m.end():nxt if nxt != -1 else len(text)]
        fields = {}
        for fm in re.finditer(r"(\w+)\s*=\s*[{\"]((?:[^{}]|\{[^{}]*\})*)[}\"]", body, re.S):
            fields[fm.group(1).lower()] = fm.group(2).strip()
        key = m.group(2).strip()
        line_end = text.find("\n", at)
        raw = text[at:line_end if line_end != -1 else at + 240][:240]
        entries.append({"key": key, "type": m.group(1).lower(), "fields": fields, "raw": raw})
    return entries


def record_from_bib(e):
    f = e.get("fields", {})
    raw_auth = f.get("author") or ""
    authors = [a.strip() for a in re.split(r"\s+and\s+", raw_auth, flags=re.I) if a.strip()]
    return {
        "title": f.get("title"),
        "authors": authors,
        "year": int(f["year"]) if (f.get("year") or "").isdigit() else None,
        "journal": f.get("journal") or f.get("booktitle"),
        "doi": (f.get("doi") or "").strip() or None,
        "arxiv_id": (f.get("eprint") or "").strip() or None,
        "venue": f.get("journal") or f.get("booktitle") or e.get("type"),
    }


def meta_record_like(obj):
    """把 JSON 元数据文件里的记录规整成统一 dict；不匹配返回 None。"""
    if not isinstance(obj, dict):
        return None
    title = obj.get("title") or obj.get("Title") or obj.get("name")
    if not title:
        return None
    auth = obj.get("authors") or obj.get("author") or obj.get("Authors")
    if isinstance(auth, str):
        auth = [a.strip() for a in auth.split(";") if a.strip()]
    if not isinstance(auth, list):
        auth = []
    year = obj.get("year") or obj.get("Year")
    if isinstance(year, str) and year.isdigit():
        year = int(year)
    return {
        "title": title,
        "authors": [str(a) for a in auth],
        "year": year if isinstance(year, int) else None,
        "journal": obj.get("journal") or obj.get("Journal"),
        "doi": (obj.get("doi") or obj.get("DOI") or "").strip() or None,
        "arxiv_id": (obj.get("arxiv") or obj.get("eprint") or obj.get("arXiv") or "").strip() or None,
        "venue": obj.get("journal") or obj.get("Journal"),
    }


def new_entry(source, abs_path, fmeta):
    return {
        "source": source,
        "abs_path": abs_path,
        "sha256": fmeta.get("sha256"),
        "size": fmeta.get("size"),
        "ext": fmeta.get("ext", ""),
        "family": fmeta.get("family", "other"),
        "document_type": None,
        "topics": [],
        "tags": [],
        "metadata": {"title": None, "authors": [], "year": None, "journal": None,
                     "doi": None, "arxiv_id": None, "venue": None},
        "suggested_name": None,
        "category_path": None,
        "target": None,
        "keep": True,
        "duplicate_of": None,
        "version_group": None,
        "version_role": "unknown",
        "alias_of": None,
        "has_text_layer": fmeta.get("has_text_layer"),
        "encoding": fmeta.get("encoding"),
        "permissions": fmeta.get("permissions") or {},
        "evidence": [],
        "confidence": "low",
        "errors": list(fmeta.get("errors") or []),
        "already_present": False,
    }


def _merge_meta(target, src):
    changed = False
    for k in ("title", "journal", "doi", "arxiv_id", "year", "venue"):
        v = src.get(k)
        if v not in (None, "", []):
            target["metadata"][k] = v
            changed = True
    for a in src.get("authors") or []:
        if a and a not in target["metadata"]["authors"]:
            target["metadata"]["authors"].append(a)
            changed = True
    return changed


def build_entries(inv, decisions):
    """inv.files -> entries。合并 BibTeX/元数据/轻量文本/decisions 四层信号。"""
    entries = []
    warnings = []
    bib_entries = []

    for f in inv.get("files", []):
        src = f["source"]
        if f.get("family") in ("bibtex",):
            parsed = parse_bibtex(f.get("sample_text") or "")
            for pe in parsed:
                pe["entry_path"] = src
            bib_entries.extend(parsed)

    for f in inv.get("files", []):
        src = f["source"]
        e = new_entry(src, f["abs_path"], f)
        ext = f.get("ext", "")
        base = os.path.basename(src)
        e["document_type"] = classify_type(base, ext)

        name_ev = []
        my = YEAR_RE.search(base)
        if my:
            e["metadata"]["year"] = int(my.group(1))
            name_ev.append(ev(f"filename:{src}", my.group(1)))
        md = DOI_RE.search(base)
        if md:
            e["metadata"]["doi"] = _clean_doi(md.group(0))
            name_ev.append(ev(f"filename:{src}", _clean_doi(md.group(0))))
        ma = ARXIV_RE.search(base)
        if ma:
            e["metadata"]["arxiv_id"] = ma.group(1)
            name_ev.append(ev(f"filename:{src}", ma.group(1)))
        e["evidence"].extend(name_ev)
        if name_ev:
            e["confidence"] = "medium"

        txt = f.get("sample_text") or ""
        if txt:
            lm, lme = extract_light_metadata(txt, src, f.get("has_text_layer"))
            if lm.get("title") and not e["metadata"]["title"]:
                e["metadata"]["title"] = lm["title"]
                e["confidence"] = "medium"
            for k in ("year", "doi", "arxiv_id"):
                if lm.get(k) and not e["metadata"].get(k):
                    e["metadata"][k] = lm[k]
            e["evidence"].extend(lme)
            e["has_text_layer"] = bool(lm.get("has_text_layer"))
        else:
            e["has_text_layer"] = f.get("has_text_layer")

        topics, tags, _tev = collect_topics_tags(base, txt)
        e["topics"] = topics
        e["tags"] = tags

        matched_bib = _match_bib_to_file(base, bib_entries)
        if matched_bib:
            rec = record_from_bib(matched_bib)
            loc = f"bibtex:{matched_bib.get('entry_path', 'unknown')}#{matched_bib['key']}"
            if _merge_meta(e, rec):
                e["evidence"].append(ev(loc, matched_bib.get("raw") or json.dumps(rec, ensure_ascii=False)[:240]))
                e["confidence"] = "high"
            if e["document_type"] in ("journal_article", "reference_data", "other"):
                type_map = {"article": "journal_article", "inproceedings": "conference_paper",
                            "incollection": "book_chapter", "phdthesis": "thesis",
                            "mastersthesis": "thesis", "techreport": "tech_report"}
                e["document_type"] = type_map.get(matched_bib["type"], e["document_type"])

        dec = _get_decision(decisions, src)
        if dec:
            _apply_decision(e, dec, warnings, src)

        entries.append(e)

    return entries, warnings


def _parse_csv_meta(txt):
    out = []
    if not txt:
        return out
    try:
        reader = csv.DictReader(io.StringIO(txt))
        for row in reader:
            r = meta_record_like(row)
            if r:
                out.append(r)
    except Exception:  # noqa: BLE001
        return out
    return out


def _match_bib_to_file(base, bib_entries):
    if not bib_entries:
        return None
    base_l = base.lower()
    for b in bib_entries:
        if b.get("key", "").lower() in base_l or base_l in b.get("key", "").lower():
            return b
    for b in bib_entries:
        t = (b.get("fields", {}).get("title") or "").lower()
        tb = slugify(t, 60)
        if tb and (tb[:40] in slugify(base_l, 200) or slugify(base_l, 120) in tb):
            return b
    return None


def _get_decision(decisions, src):
    if not isinstance(decisions, dict):
        return None
    files = decisions.get("files") or decisions.get("entries")
    if isinstance(files, dict):
        return files.get(src) or files.get(src.replace("\\", "/"))
    if isinstance(files, list):
        for d in files:
            if isinstance(d, dict) and d.get("source") in (src, src.replace("\\", "/")):
                return d
    return None


def _apply_decision(e, dec, warnings, src):
    doc = dec.get("document_type")
    if doc:
        e["document_type"] = doc
    if isinstance(dec.get("topics"), list):
        e["topics"] = dec["topics"][:3]
    if isinstance(dec.get("tags"), list):
        e["tags"] = dec["tags"][:8]
    meta = dec.get("metadata") or {}
    evd = dec.get("evidence") or dec.get("quotes") or []
    if evd:
        _merge_meta(e, meta)
        e["evidence"].extend([{"locator": q.get("locator") or q.get("source") or src,
                               "quote": q.get("quote") or q.get("text") or ""}
                              for q in evd if isinstance(q, dict)])
        e["confidence"] = "high"
    else:
        warnings.append({"source": src, "reason": "decision without evidence; not adopted",
                         "field": "decision", "pending": meta})


def detect_duplicates(entries):
    groups = {}
    for e in sorted(entries, key=lambda x: (x.get("sha256") or "", x.get("source", ""))):
        h = e.get("sha256")
        if h:
            groups.setdefault(h, []).append(e)
    dups = []
    for h, members in groups.items():
        members = sorted(members, key=lambda x: x["source"])
        if len(members) < 2:
            continue
        keep = members[0]
        dups.append({"sha256": h, "keep": keep["source"], "copies": [m["source"] for m in members[1:]]})
        for m in members[1:]:
            m["keep"] = False
            m["duplicate_of"] = keep["source"]
            m["version_role"] = "duplicate"
            m["evidence"].append(ev(f"sha256:{h[:16]}", f"identical bytes to {keep['source']}"))
            m["confidence"] = "high"
    return dups


def detect_version_groups(entries):
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

    keys = {}
    for e in entries:
        eid = e["source"]
        for k in _version_keys(e):
            members = keys.setdefault(k, [])
            members.append(eid)
            if len(members) >= 2:
                for m in members:
                    union(members[0], m)
    groups = {}
    for e in entries:
        root = find(e["source"])
        groups.setdefault(root, []).append(e)
    out = []
    gid = 0
    used = set()
    for root, members in sorted(groups.items(), key=lambda kv: min(x["source"] for x in kv[1])):
        if len(members) < 2:
            continue
        basis_new = sorted({b for e in members for b in _version_basis(e)})
        title_year_only = (not basis_new and
                           any(len(norm_key((e.get("metadata") or {}).get("title") or "")) > 12
                               for e in members))
        if not basis_new and not title_year_only:
            continue
        gid += 1
        gname = f"v{gid}"
        for e in members:
            if e["source"] in used:
                continue
            used.add(e["source"])
            e["version_group"] = gname
        out.append({"group_id": gname, "members": sorted(m["source"] for m in members),
                    "basis": basis_new})
    _resolve_version_roles([g["members"] for g in out], entries)
    return out


def _version_keys(e):
    ks = []
    meta = e.get("metadata") or {}
    if meta.get("doi"):
        ks.append("doi:" + norm_key(meta["doi"]))
    if meta.get("arxiv_id"):
        ks.append("arxiv:" + norm_key(meta["arxiv_id"]))
    t = norm_key(meta.get("title") or "")
    y = meta.get("year")
    if len(t) > 12 and y:
        ks.append("title:" + t + ":" + str(y))
    return ks


def _version_basis(e):
    meta = e.get("metadata") or {}
    b = []
    if meta.get("doi"):
        b.append("doi:" + meta["doi"])
    if meta.get("arxiv_id"):
        b.append("arxiv:" + meta["arxiv_id"])
    return b


def _resolve_version_roles(member_lists, entries):
    by_src = {e["source"]: e for e in entries}
    for members in member_lists:
        srcs = [m for m in members]
        roles = {}
        for s in srcs:
            e = by_src.get(s)
            if not e:
                continue
            dt = (e.get("document_type") or "").lower()
            has_pre = any(x in dt for x in ("preprint", "draft")) or bool((e.get("metadata") or {}).get("arxiv_id"))
            has_pub = bool(dt.startswith("journal") or dt.startswith("conference"))
            if has_pre and not has_pub:
                roles[s] = "preprint"
            elif has_pub:
                roles[s] = "published"
            else:
                roles[s] = "unknown"
        pubs = [s for s, r in roles.items() if r == "published"]
        if len(pubs) == 1:
            for s in srcs:
                if roles.get(s) == "unknown":
                    roles[s] = "revision_or_related"
        for s in srcs:
            e = by_src.get(s)
            if e:
                e["version_role"] = roles.get(s, "unknown")


def find_alias_records(inv):
    """在输入里发现显式别名/链接记录（manifest/alias/links 文件或 JSON 键）。"""
    records = []
    for f in inv.get("files", []):
        name = os.path.basename(f["source"]).lower()
        if not re.search(r"alias|link|manifest|symlink", name):
            continue
        obj, err = read_json(f["abs_path"])
        if obj is not None:
            for key in ("aliases", "links", "alias", "symlinks"):
                block = obj.get(key) if isinstance(obj, dict) else None
                if isinstance(block, list):
                    for item in block:
                        if isinstance(item, dict) and item.get("name"):
                            records.append({"name": item["name"], "target": item.get("target"),
                                            "locator": f"file:{f['source']}#{key}",
                                            "quote": json.dumps(item, ensure_ascii=False)[:240]})
            continue
        txt = f.get("sample_text") or ""
        for ln in txt.splitlines():
            ln = ln.strip()
            m = re.match(r"(?:alias\s+)?([\w.\-/]+)\s*(?:->|=>|=)\s*([\w.\-/]+)", ln, re.I)
            if m:
                records.append({"name": m.group(1), "target": m.group(2),
                                "locator": f"file:{f['source']}",
                                "quote": ln[:240]})
    return records


def detect_alias_relation(entries, alias_records, symlink_map):
    alias_list = []
    by_src = {e["source"]: e for e in entries}
    for e in entries:
        if e.get("duplicate_of"):
            alias_list.append({"source": e["source"], "target": e["duplicate_of"],
                               "relation": "bytes_identical",
                               "basis": ["sha256:" + (e["sha256"] or "")[:16]],
                               "evidence": [ev("sha256", (e["sha256"] or "")[:16])]})
            e.setdefault("alias_of", e["duplicate_of"])
    for link, tgt in sorted(symlink_map.items()):
        if link in by_src:
            alias_list.append({"source": link, "target": tgt or "unknown", "relation": "symlink",
                               "basis": ["symlink"], "evidence": [ev(f"symlink:{link}", tgt or "")]})
            by_src[link].setdefault("alias_of", tgt)
    for r in alias_records:
        name = r.get("name")
        tgt = r.get("target")
        if not name:
            continue
        alias_list.append({"source": name, "target": tgt or "unknown", "relation": "alias",
                           "basis": ["manifest"], "evidence": [ev(r["locator"], r["quote"])]})
        if name in by_src:
            by_src[name].setdefault("alias_of", tgt)
    return alias_list


def build_index(entries):
    idx = {"by_title": {}, "by_doi": {}, "by_arxiv": {}, "by_author": {},
           "by_tag": {}, "by_hash": {}, "by_source": {}}
    for e in entries:
        src = e["source"]
        idx["by_source"][src] = {
            "document_type": e["document_type"], "topics": e["topics"], "tags": e["tags"],
            "metadata": e["metadata"], "sha256": e["sha256"], "target": e.get("target"),
        }
        if e["sha256"]:
            idx["by_hash"].setdefault(e["sha256"], []).append(src)
        m = e.get("metadata") or {}
        if m.get("title"):
            idx["by_title"].setdefault(norm_key(m["title"]), []).append(src)
        if m.get("doi"):
            idx["by_doi"].setdefault(m["doi"].lower(), []).append(src)
        if m.get("arxiv_id"):
            idx["by_arxiv"].setdefault(m["arxiv_id"], []).append(src)
        for a in m.get("authors") or []:
            last = slugify(a.split()[-1] if a.split() else a, 40)
            if last:
                idx["by_author"].setdefault(last, []).append(src)
        for t in e.get("tags") or []:
            idx["by_tag"].setdefault(t, []).append(src)
    for k in idx:
        if isinstance(idx[k], dict):
            idx[k] = dict(sorted(idx[k].items()))
    return idx


def build_graph(entries, version_groups, alias_list):
    nodes = []
    for e in entries:
        nodes.append({"id": "src:" + e["source"], "label": e["source"], "kind": e["document_type"],
                      "sha256": e["sha256"], "keep": e["keep"],
                      "metadata": e["metadata"], "version_group": e["version_group"],
                      "version_role": e["version_role"], "alias_of": e["alias_of"]})
    edges = []
    for e in entries:
        if e.get("duplicate_of"):
            edges.append({"source": "src:" + e["source"], "target": "src:" + e["duplicate_of"],
                          "relation": "duplicate_of", "basis": ["sha256"]})
        if e.get("version_group"):
            basis = _version_basis(e)
            for other in entries:
                if other["source"] != e["source"] and other.get("version_group") == e["version_group"]:
                    edges.append({"source": "src:" + e["source"], "target": "src:" + other["source"],
                                  "relation": "version_of", "basis": basis or ["title+year"]})
                    break
    for a in alias_list:
        edges.append({"source": "name:" + str(a["source"]), "target": "name:" + str(a["target"]),
                      "relation": a["relation"], "basis": a["basis"], "evidence": a["evidence"]})
    return {"schema_version": SCHEMA_VERSION, "nodes": nodes, "edges": edges,
            "version_groups": version_groups, "aliases": alias_list}


_PLAN_KEYS = ("source", "sha256", "ext", "document_type", "topics", "tags", "metadata",
              "category_path", "suggested_name", "target", "keep", "duplicate_of",
              "version_group", "version_role", "alias_of", "has_text_layer", "confidence",
              "evidence", "errors")


def build_suggested_name(e):
    m = e.get("metadata") or {}
    ext = (e.get("ext") or "").lower()
    first = ""
    if m.get("authors"):
        parts = str(m["authors"][0]).replace(",", " ").split()
        first = parts[0] if parts else ""
    first = slugify(first, 30) or "anon"
    year = str(m["year"]) if m.get("year") else "noyear"
    title = m.get("title") or os.path.splitext(os.path.basename(e["source"]))[0]
    title_slug = slugify(title, 80)
    name = f"{first}-{year}-{title_slug}{ext}"
    if title_slug in ("", "untitled") and first == "anon":
        h = (e.get("sha256") or "")[:10] or "nosha"
        name = f"{h}-{slugify(os.path.basename(e['source']), 80)}{ext}"
    if len(name) > 180:
        head = f"{first}-{year}-"
        name = head + slugify(m.get("title") or "", 180 - len(head) - len(ext)) + ext
    return name


def _add_suffix(name, n):
    stem, dot, ext = name.rpartition(".")
    if dot and stem:
        return f"{stem}-{n}{dot}{ext}"
    return f"{name}-{n}"


def build_plan(entries, hierarchy, opts=None):
    opts = opts or {}
    dims = [d for d in (opts.get("hierarchy") or hierarchy or ["document_type", "topic"]) if d]
    plan = {"schema_version": SCHEMA_VERSION, "strategy": "copy_not_move",
            "dimensions": dims, "entries": [], "invariants": [], "stats": {}}
    used_targets = {}
    for e in sorted(entries, key=lambda x: x["source"]):
        parts = []
        for d in dims:
            if d == "document_type":
                parts.append(slugify(e.get("document_type") or "unknown", 40))
            elif d == "topic":
                parts.append(slugify(e.get("topics")[0], 40) if e.get("topics") else "untagged")
            elif d == "year":
                parts.append(str(e.get("metadata", {}).get("year") or "noyear"))
            elif d == "type":
                parts.append(slugify(e.get("family") or "other", 20))
        category = "/".join(parts) if parts else "unsorted"
        e["category_path"] = category
        base = build_suggested_name(e)
        if not e.get("keep"):
            e["suggested_name"] = base
            e["target"] = None
            plan["entries"].append({k: e[k] for k in _PLAN_KEYS})
            continue
        target = f"{category}/{base}"
        seen = used_targets.get(target)
        if seen is None:
            used_targets[target] = [(e["source"], e["sha256"])]
        else:
            same_content = any(h == e["sha256"] for _, h in seen)
            if not same_content:
                n = 2
                while f"{category}/{_add_suffix(base, n)}" in used_targets:
                    n += 1
                base = _add_suffix(base, n)
                target = f"{category}/{base}"
            used_targets.setdefault(target, []).append((e["source"], e["sha256"]))
        e["suggested_name"] = base
        e["target"] = target
        plan["entries"].append({k: e[k] for k in _PLAN_KEYS})
    plan["stats"] = {
        "total_files": len(entries),
        "kept": sum(1 for e in entries if e.get("keep")),
        "duplicates": sum(1 for e in entries if e.get("duplicate_of")),
        "version_groups": len({e.get("version_group") for e in entries if e.get("version_group")}),
        "missing_metadata": sum(1 for e in entries if not (e.get("metadata") or {}).get("title")),
    }
    return plan


def verify_plan(entries, plan, inv):
    report = []
    inv_srcs = {f["source"] for f in inv.get("files", [])}
    plan_srcs = {e["source"] for e in entries}
    report.append(_check("coverage", inv_srcs == plan_srcs,
                         f"{len(plan_srcs)}/{len(inv_srcs)} input files in plan"))
    targets = [e.get("target") for e in entries if e.get("target")]
    dup_targets = {t for t in targets if targets.count(t) > 1}
    report.append(_check("target_uniqueness", not dup_targets,
                         f"no duplicate target name (conflicts {len(dup_targets)})"))
    bad_targets = [t for t in targets if t and (t.startswith("/") or ".." in t.split("/"))]
    report.append(_check("target_path_safety", not bad_targets,
                         "all targets are output-relative paths"))
    for e in entries:
        if e.get("duplicate_of"):
            peer = next((x for x in entries if x["source"] == e["duplicate_of"]), None)
            if peer is None or peer.get("sha256") != e.get("sha256"):
                report.append(_check(f"duplicate_hash:{e['source']}", False, "duplicate_of hash mismatch"))
        if e.get("version_group") and not _version_basis(e):
            report.append(_check(f"version_basis:{e['source']}", False, "version group lacks DOI/arXiv basis"))
    no_ev = [e["source"] for e in entries
             if ((e.get("metadata", {}).get("doi") or (e.get("metadata", {}).get("authors") or []))
                 and not e.get("evidence"))]
    report.append(_check("evidence_presence", not no_ev,
                         f"asserted fields have locator+quote (exceptions {len(no_ev)})"))
    report.append(_check("rollbackable", plan.get("strategy") == "copy_not_move",
                         "copy_not_move: delete output library to roll back"))
    return report


def _check(name, ok, detail):
    return {"check": name, "passed": bool(ok), "detail": detail}


def apply_plan(plan, out_dir):
    library_root = os.path.join(out_dir, "library")
    operations = []
    rollback = []
    for e in plan.get("entries", []):
        src = e.get("abs_path")
        target = e.get("target")
        if not e.get("keep") or not target:
            operations.append({"op": "skip", "source": e["source"], "reason": "duplicate_or_archived",
                               "duplicate_of": e.get("duplicate_of"), "sha256": e.get("sha256"),
                               "result": "not_copied"})
            continue
        if not os.path.isfile(src or ""):
            operations.append({"op": "fail", "source": e["source"], "reason": "source missing",
                               "result": "missing_source"})
            continue
        dst = os.path.join(library_root, *target.split("/"))
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.exists(dst):
                if sha256_file(dst) == e["sha256"]:
                    operations.append({"op": "already_present", "source": e["source"], "target": target,
                                       "sha256": e["sha256"], "result": "idempotent_skip"})
                    e["already_present"] = True
                else:
                    operations.append({"op": "skip", "source": e["source"], "target": target,
                                       "reason": "path busy with different content", "result": "skipped"})
            else:
                shutil.copyfile(src, dst)
                operations.append({"op": "copy", "source": e["source"], "target": target,
                                   "sha256": e["sha256"], "result": "copied"})
                rollback.append({"target": target, "abs_target": dst})
        except OSError as ex:
            operations.append({"op": "fail", "source": e["source"], "target": target,
                               "reason": str(ex), "result": "copy_error"})
    return operations, rollback


def answer_queries(entries, index, query_records):
    results = []
    for q in query_records:
        text = (q.get("text") or q.get("question") or "").strip()
        qid = q.get("id") or f"q{len(results)+1}"
        matches, method, evidence = _lookup(text, index)
        results.append({"id": qid, "text": text, "method": method,
                        "matches": matches, "evidence": evidence,
                        "status": "answered" if matches else "unanswered"})
    return results


def _lookup(text, index):
    t = norm_key(text)
    md = DOI_RE.search(text)
    if md:
        doi = _clean_doi(md.group(0)).lower()
        srcs = index["by_doi"].get(doi) or []
        return srcs, "doi", [ev(f"query:doi:{doi}", doi)] if srcs else []
    ma = ARXIV_RE.search(text)
    if ma:
        aid = ma.group(1)
        srcs = index["by_arxiv"].get(aid) or []
        return srcs, "arxiv", [ev(f"query:arxiv:{aid}", aid)] if srcs else []
    for title_key, srcs in sorted(index["by_title"].items()):
        if title_key and (title_key in t or (len(title_key) > 20 and t in title_key)):
            return srcs, "title", [ev(f"index:by_title:{title_key[:40]}", title_key[:80])]
    for tag, srcs in sorted(index["by_tag"].items()):
        if tag and tag in t:
            return srcs, "tag", [ev(f"index:by_tag:{tag}", tag)]
    return [], "none", []
