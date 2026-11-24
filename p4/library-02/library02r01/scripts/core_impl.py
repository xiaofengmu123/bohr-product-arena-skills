"""core_impl.py - evidence-grounded document library repair engine.

Pure standard library. Deterministic: sorted traversal, stable hashes,
no randomness. Read-only on input; writes only to work-dir and output-dir.
"""
import argparse
import csv
import difflib
import hashlib
import io
import json
import os
import re
import sqlite3
import stat as statmod
import sys
import time
import zipfile
import zlib
import xml.etree.ElementTree as ET
from collections import Counter, OrderedDict, defaultdict
from datetime import datetime, timezone
from pathlib import Path

VERSION = "2.0.0"
HASH_SIZE_LIMIT = 2 * 1024 * 1024  # 2 MiB
QUOTE_LEN = 160  # max verbatim quote length stored per locator

RESERVED_KEYS = {
    "case_id", "status", "sources", "evidence_map", "uncertainty",
    "pending_human", "change_log", "errors", "degradations", "runtime",
    "limitations", "computed", "contract",
}

TEXT_EXTS = {
    ".md", ".markdown", ".txt", ".csv", ".tsv", ".json", ".jsonl", ".ndjson",
    ".yaml", ".yml", ".bib", ".ris", ".enw", ".xml", ".html", ".htm", ".r",
    ".py", ".tex", ".log", ".cfg", ".ini", ".toml", ".sh", ".c", ".cpp",
    ".h", ".hpp", ".java", ".js", ".ts", ".sql", ".rst", ".org", ".rdf",
}
OFFICE_EXTS = {".docx", ".xlsx", ".pptx"}
ARCHIVE_EXTS = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z"}
PDF_EXT = ".pdf"


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(step, detail, change_log, actor="scripts/core_impl.py"):
    change_log.append({"time": now_iso(), "actor": actor, "step": step,
                       "detail": detail})


def forward_slash(p):
    return p.replace(os.sep, "/")


def relpath_from(root, p):
    try:
        return forward_slash(os.path.relpath(p, root))
    except ValueError:
        return forward_slash(p)


def sha256_of_bytes(data):
    return hashlib.sha256(data).hexdigest()


def read_file_bytes(path):
    """Return bytes or None; record reason on failure via exception."""
    with open(path, "rb") as f:
        return f.read()


def decode_text(data):
    """Return (text, degraded_flag). utf-8-sig first, then utf-16, gb18030,
    latin-1 last. latin-1 never fails, so flag non-utf8/16 as degraded."""
    if b"\x00" in data[:4096]:
        for enc in ("utf-16", "utf-16-le", "utf-16-be"):
            try:
                return data.decode(enc), False
            except UnicodeError:
                continue
    for enc, flag in (("utf-8-sig", False), ("utf-8", False), ("gb18030", True)):
        try:
            return data.decode(enc), flag
        except UnicodeError:
            continue
    return data.decode("latin-1", errors="replace"), True


def pdf_unescape(s):
    s = s.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")
    s = s.replace(r"\n", "\n").replace(r"\r", "\r").replace(r"\t", "\t")
    # octal \ddd and hex \xhh best-effort
    s = re.sub(r"\\([0-7]{1,3})", lambda m: chr(int(m.group(1), 8) & 0xFF), s)
    return s


def pdf_text_from_content(b):
    s = b.decode("latin-1", errors="replace")
    parts = []
    for m in re.finditer(r"\(((?:\\.|[^()\\])*)\)\s*Tj", s):
        parts.append(pdf_unescape(m.group(1)))
    for m in re.finditer(r"\[((?:[^\[\]]|\((?:\\.|[^()\\])*\))*)\]\s*TJ", s):
        inner = m.group(1)
        for t in re.finditer(r"\(((?:\\.|[^()\\])*)\)", inner):
            parts.append(pdf_unescape(t.group(1)))
    return "".join(parts)


def pdf_extract_text(data):
    chunks = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        raw = m.group(1)
        try:
            decoded = zlib.decompress(raw)
        except Exception:
            decoded = raw
        txt = pdf_text_from_content(decoded)
        if txt.strip():
            chunks.append(txt)
    text = "\n".join(chunks)
    return text.strip()


def xml_text(xml_bytes):
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return ""
    out = []
    for t in root.iter():
        tag = t.tag.split("}")[-1]
        if tag in ("t", "text") and t.text:
            out.append(t.text)
    return "".join(out)


def office_extract_text(path):
    ext = os.path.splitext(path)[1].lower()
    names = []
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        return "", "office_zip_error"
    try:
        if ext == ".docx":
            names = ["word/document.xml"]
        elif ext == ".xlsx":
            names = ["xl/sharedStrings.xml"]
        elif ext == ".pptx":
            names = sorted(
                n for n in z.namelist()
                if re.match(r"ppt/slides/slide\d+\.xml$", n))
        text = []
        for n in names:
            try:
                text.append(xml_text(z.read(n)))
            except KeyError:
                continue
        joined = "\n".join(t for t in text if t.strip())
        if not joined:
            return "", "office_no_text"
        return joined, None
    finally:
        z.close()


def extract_text(path, data):
    """Return (text, method, error). text is '' when unavailable."""
    ext = os.path.splitext(path)[1].lower()
    if ext == PDF_EXT:
        try:
            text = pdf_extract_text(data)
            if not text:
                return "", "pdf", "pdf_no_text_layer"
            return text, "pdf", None
        except Exception as e:  # noqa: BLE001
            return "", "pdf", "pdf_parse_error:%s" % type(e).__name__
    if ext in OFFICE_EXTS:
        text, err = office_extract_text(path)
        if err:
            return "", "office", err
        return text, "office", None
    if ext in ARCHIVE_EXTS:
        return "", "archive", "archive_skipped"
    try:
        text, degraded = decode_text(data)
        return text, "text", ("bad_encoding" if degraded else None)
    except Exception as e:  # noqa: BLE001
        return "", "binary", "undecodable:%s" % type(e).__name__


def normalize_stem(name):
    stem = re.sub(r"\.[^.]+$", "", name)
    stem = re.sub(
        r"[_\-.]?(v|ver|version|rev|r|draft|final|release)[_\-.]?\d+(\.\d+)*",
        "", stem, flags=re.I)
    stem = re.sub(
        r"[_\-.]?(copy|副本|扫描|scan|scanned|最终|定稿|修订|revised|"
        r"old|new|最新|updated)[_\-.]?\d*",
        "", stem, flags=re.I)
    stem = re.sub(r"[\s_\-\.]+", " ", stem).strip().lower()
    return stem


def similarity(a, b):
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def norm_tokens(s):
    return re.findall(r"[0-9A-Za-z]+", s.lower())


def cjk_bigrams(s):
    s = re.sub(r"[\s\W_]+", "", s, flags=re.UNICODE)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def search_score(query, text):
    q = query.strip().lower()
    toks = norm_tokens(q)
    bi = cjk_bigrams(q)
    score = 0.0
    if toks:
        text_toks = set(norm_tokens(text))
        score += 2.0 * len(toks.intersection(text_toks)) / max(1, len(toks))
    if bi:
        text_bi = cjk_bigrams(text)
        score += 3.0 * len(bi.intersection(text_bi)) / max(1, len(bi))
    return score


# ---------------------------------------------------------------- inventory

def inventory(root):
    """Walk input root deterministically. Return entries + errors/degradations."""
    entries = []
    errors = []
    degradations = []
    dirs = []
    if not os.path.isdir(root):
        errors.append({"item": root, "kind": "input_root_missing",
                       "detail": "input root not a directory"})
        return entries, dirs, errors, degradations

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        rel_dir = relpath_from(root, dirpath)
        dirs.append(rel_dir)
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = relpath_from(root, full)
            entry = {"path": rel, "name": fn, "ext": os.path.splitext(fn)[1].lower(),
                     "dir": rel_dir, "size": None, "mtime": None, "mode": None,
                     "is_symlink": False, "link_target": None, "is_dir": False,
                     "sha256": None, "nlink": None, "inode": None, "dev": None,
                     "text_method": None, "text_error": None, "kind": "file"}
            try:
                st = os.lstat(full)
                entry["size"] = st.st_size
                entry["mtime"] = int(st.st_mtime)
                entry["mode"] = oct(statmod.S_IMODE(st.st_mode))[2:].zfill(3)
                entry["nlink"] = getattr(st, "st_nlink", None)
                entry["inode"] = getattr(st, "st_ino", None)
                entry["dev"] = getattr(st, "st_dev", None)
                if statmod.S_ISLNK(st.st_mode):
                    entry["is_symlink"] = True
                    try:
                        entry["link_target"] = forward_slash(os.readlink(full))
                    except OSError as e:
                        entry["link_target"] = "<readlink_error:%s>" % e
                        degradations.append(
                            {"item": rel, "kind": "symlink_read_error",
                             "detail": str(e)})
                    entry["kind"] = "symlink"
                elif statmod.S_ISDIR(st.st_mode):
                    entry["is_dir"] = True
                    entry["kind"] = "dir"
                elif not statmod.S_ISREG(st.st_mode):
                    entry["kind"] = "non_regular"
                    degradations.append(
                        {"item": rel, "kind": "non_regular",
                         "detail": "special file (fifo/socket/device)"})
            except OSError as e:
                entry["kind"] = "unreadable"
                errors.append({"item": rel, "kind": "stat_error",
                               "detail": str(e)})
            entries.append(entry)
    return entries, dirs, errors, degradations


def hash_and_text(entries, input_root):
    errors = []
    degradations = []
    for e in entries:
        if e["kind"] != "file":
            if e["kind"] == "symlink":
                # content evidence for symlink = link target (verbatim)
                e["text"] = e.get("link_target") or ""
                e["text_method"] = "symlink"
            else:
                e["text"] = ""
                e["text_method"] = e["kind"]
            continue
        full = os.path.join(input_root, e["path"])
        try:
            data = read_file_bytes(full)
        except OSError as ex:
            e["kind"] = "unreadable"
            e["text"] = ""
            e["text_method"] = None
            errors.append({"item": e["path"], "kind": "unreadable",
                           "detail": str(ex)})
            continue
        if e["size"] is not None and e["size"] > HASH_SIZE_LIMIT:
            e["sha256"] = None
            e["text"] = ""
            e["text_method"] = "skipped"
            e["text_error"] = "too_large"
            degradations.append(
                {"item": e["path"], "kind": "too_large",
                 "detail": "size %d > %d" % (e["size"], HASH_SIZE_LIMIT)})
            continue
        e["sha256"] = sha256_of_bytes(data)
        text, method, err = extract_text(full, data)
        e["text"] = text
        e["text_method"] = method
        e["text_error"] = err
        if err:
            degradations.append({"item": e["path"], "kind": err,
                                 "detail": "text extraction degraded"})
    return errors, degradations


# ---------------------------------------------------------------- dedup/version

def find_duplicates(entries):
    groups = defaultdict(list)
    for e in entries:
        if e["sha256"]:
            groups[e["sha256"]].append(e["path"])
    dup_groups = []
    for h, paths in sorted(groups.items(), key=lambda kv: kv[1][0]):
        if len(paths) > 1:
            dup_groups.append({"sha256": h, "paths": sorted(paths)})
    return dup_groups


def version_candidates(entries, texts_by_path):
    by_stem = defaultdict(list)
    for e in entries:
        if e["kind"] != "file" or not e.get("text"):
            continue
        stem = normalize_stem(e["name"])
        if stem:
            by_stem[stem].append(e["path"])
    candidates = []
    for stem in sorted(by_stem):
        paths = sorted(by_stem[stem])
        if len(paths) < 2:
            continue
        sims = []
        for i in range(len(paths)):
            for j in range(i + 1, len(paths)):
                sims.append({"a": paths[i], "b": paths[j],
                             "similarity": round(
                                 similarity(texts_by_path.get(paths[i], ""),
                                            texts_by_path.get(paths[j], "")), 4)})
        candidates.append({"normalized_stem": stem, "paths": paths,
                           "similarities": sims, "heuristic": True})
    return candidates


def alias_groups(entries):
    symlinks = []
    for e in entries:
        if e["is_symlink"]:
            full_target = e.get("link_target")
            broken = full_target is None or (
                not os.path.isabs(full_target) and
                "<" in str(full_target) or
                str(full_target).startswith("<"))
            symlinks.append({"path": e["path"], "target": e["link_target"],
                             "broken": broken})
    hard_groups = defaultdict(list)
    for e in entries:
        if e.get("nlink") and e["nlink"] > 1 and e.get("inode") is not None:
            hard_groups[(e["dev"], e["inode"])].append(e["path"])
    hardlink_groups = [{"dev": k[0], "inode": k[1], "paths": sorted(v)}
                       for k, v in sorted(hard_groups.items())
                       if len(v) > 1]
    return symlinks, hardlink_groups


# ---------------------------------------------------------------- version notes

def parse_version_notes(task_text, entries):
    """Extract administrative version notes from task.md and material texts.
    Looks for sections headed by version/版本/修订/change keywords."""
    notes = []
    sources = [("task.md", task_text)]
    for e in entries:
        if e.get("text") and e["kind"] == "file" and e["ext"] in (".md", ".txt"):
            sources.append((e["path"], e["text"]))
    pat = re.compile(
        r"^[#\*\s]{0,6}(版本说明|行政版本说明|版本记录|修订记录|版本历史|"
        r"version\s*(note|history|log)?|change\s*log|revision\s*history)\s*[:：]?\s*$",
        re.I | re.M)
    for src, text in sources:
        for m in pat.finditer(text):
            seg = text[m.end():m.end() + 1200]
            line = text.count("\n", 0, m.start()) + 1
            notes.append({"source": src, "line": line, "heading": m.group(0).strip(),
                          "excerpt": seg.strip().split("\n")[0:8]})
    return notes


# ---------------------------------------------------------------- catalogs

def parse_json_catalog(text):
    try:
        obj = json.loads(text)
    except ValueError:
        return None, "bad_json"
    if isinstance(obj, list):
        return obj, None
    if isinstance(obj, dict):
        for key in ("files", "items", "records", "entries", "documents"):
            if isinstance(obj.get(key), list):
                return obj[key], None
        return [obj], None
    return None, "unknown_shape"


def parse_csv_catalog(text):
    rows = list(csv.DictReader(io.StringIO(text)))
    return rows, None


def parse_markdown_table(text):
    lines = [ln for ln in text.splitlines() if "|" in ln]
    if not lines:
        return [], None
    # header = first line with pipes, separator = next
    def cells(ln):
        return [c.strip() for c in ln.strip().strip("|").split("|")]
    if len(lines) < 2:
        return [], None
    header = cells(lines[0])
    if all(re.fullmatch(r":?-{3,}:?", c) for c in header if c):
        if len(lines) < 3:
            return [], None
        header = cells(lines[1])
        data_lines = lines[2:]
    else:
        data_lines = lines[1:]
    rows = []
    for ln in data_lines:
        cs = cells(ln)
        if len(cs) == len(header):
            rows.append(dict(zip(header, cs)))
    return rows, None


def parse_bibtex(text):
    entries = []
    for m in re.finditer(r"@(\w+)\s*\{([^,]+),", text):
        body = text[m.end():m.end() + 2000]
        key = m.group(2).strip()
        fields = {}
        for fm in re.finditer(r"(\w+)\s*=\s*[{\"]([^}\"]*)[}\"]", body):
            fields[fm.group(1).lower()] = fm.group(2)
        entries.append({"type": m.group(1), "key": key, **fields})
    return entries, None


def parse_ris(text):
    entries = []
    cur = {}
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        m = re.match(r"^([A-Z0-9]{2})\s*-\s*(.*)$", ln)
        if m:
            tag, val = m.group(1), m.group(2).strip()
            if tag == "TY" and cur:
                entries.append(cur)
                cur = {}
            cur[tag] = val
    if cur:
        entries.append(cur)
    return entries, None


def catalog_of(entry, text):
    ext = entry["ext"]
    if ext == ".json":
        return parse_json_catalog(text)
    if ext == ".csv":
        return parse_csv_catalog(text)
    if ext == ".md":
        return parse_markdown_table(text)
    if ext == ".bib":
        return parse_bibtex(text)
    if ext == ".ris":
        return parse_ris(text)
    return None, "unsupported_catalog_format"


def catalog_keys(row):
    """Return list of candidate path-ish keys in a catalog row."""
    if not isinstance(row, dict):
        return []
    keys = []
    for k, v in row.items():
        kl = k.lower()
        if kl in ("path", "file", "filename", "file_path", "filepath", "name",
                  "locator", "location") and isinstance(v, str):
            keys.append(v)
    return keys


def parse_sqlite(path):
    tables = []
    rows = []
    try:
        conn = sqlite3.connect(path)
    except sqlite3.Error as e:
        return [], [], "sqlite_open_error:%s" % type(e).__name__
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [r[0] for r in cur.fetchall()]
        for t in tables[:20]:
            try:
                cur = conn.execute("SELECT * FROM %s LIMIT 50" % t)
                cols = [d[0] for d in cur.description]
                for r in cur.fetchall():
                    rows.append({"table": t, "columns": cols,
                                 "values": [str(x) for x in r]})
            except sqlite3.Error:
                continue
        conn.close()
        return tables, rows, None
    except sqlite3.Error as e:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        return tables, rows, "sqlite_query_error:%s" % type(e).__name__


# ---------------------------------------------------------------- reconcile

def reconcile(entries, catalogs):
    actual = {e["path"] for e in entries if e["kind"] in ("file", "symlink")}
    actual_hash = {e["path"]: e["sha256"] for e in entries}
    matches, mismatches, missing, orphaned = [], [], [], []
    seen_catalog = set()
    for cat in catalogs:
        for row in cat.get("rows", []):
            for p in catalog_keys(row):
                if not p or p in seen_catalog:
                    continue
                seen_catalog.add(p)
                p = forward_slash(p.lstrip("./"))
                if p in actual:
                    chash = None
                    for hv in ("sha256", "hash", "checksum"):
                        if isinstance(row, dict) and row.get(hv):
                            chash = str(row[hv]).lower()
                            break
                    if chash and actual_hash.get(p) and chash != actual_hash[p]:
                        mismatches.append({"path": p, "catalog_hash": chash,
                                           "actual_hash": actual_hash[p]})
                    else:
                        matches.append({"path": p, "source": cat["path"]})
                else:
                    missing.append({"path": p, "source": cat["path"]})
    orphaned = sorted(actual - seen_catalog)
    return {"matches": matches, "mismatches": mismatches,
            "missing": missing, "orphaned": orphaned,
            "catalog_files": [c["path"] for c in catalogs]}


# ---------------------------------------------------------------- index

def build_index(entries):
    index = []
    for e in entries:
        text = e.get("text") or ""
        if not text:
            continue
        paragraphs = re.split(r"\n\s*\n|\r\n\s*\r\n", text)
        offset = 0
        for pi, para in enumerate(paragraphs):
            if not para.strip():
                offset += len(para) + 2
                continue
            start = offset
            # line number within file text
            line = text.count("\n", 0, start) + 1
            index.append({
                "path": e["path"], "sha256": e.get("sha256"),
                "page": None, "line": line, "paragraph": pi,
                "text": para.strip(),
                "quote": para.strip()[:QUOTE_LEN],
                "quote_span": [start, start + min(len(para.strip()), QUOTE_LEN)],
            })
            offset += len(para) + 2
    return index


def search_index(index, query, top=10):
    scored = []
    for it in index:
        s = search_score(query, it["text"])
        if s > 0:
            scored.append((s, it))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [{"score": round(s, 3), **it} for s, it in scored[:top]]


# ---------------------------------------------------------------- evidence

def qspan(text, start, length=QUOTE_LEN):
    end = min(start + length, len(text))
    return [start, end], text[start:end]


def make_locator(source_file, source_sha256, text, line=None, page=None,
                 offset=None):
    if offset is None and line is not None:
        offset = 0
        for _ in range(max(0, line - 1)):
            nl = text.find("\n", offset)
            if nl < 0:
                break
            offset = nl + 1
    if offset is None:
        offset = 0
    span, quote = qspan(text, offset)
    loc = {
        "locator": "%s#%s" % (source_file,
                              ("L%d" % line if line else "p%d" % page if page
                               else "o%d" % offset)),
        "quote": quote,
        "quote_span": span,
        "source_file": source_file,
        "source_sha256": source_sha256,
        "page": page,
        "line": line,
    }
    return loc


def evidence_for_file(e):
    text = e.get("text") or ""
    loc = make_locator(e["path"], e.get("sha256"), text, line=1)
    return loc


def build_evidence_map(entries, dup_groups, version_cands, symlinks,
                       hardlink_groups, notes, recon, answers, index):
    concl = []
    cid = 0
    for e in entries:
        cid += 1
        loc = evidence_for_file(e)
        concl.append({
            "id": "C%04d" % cid, "claim": "盘点文件 %s（kind=%s）" % (
                e["path"], e["kind"]),
            "type": "inventory", "verdict": "verified", "status": "ok",
            "confidence": "high", "method": "os.lstat+read",
            "locators": [loc],
        })
    for g in dup_groups:
        cid += 1
        e = next((x for x in entries if x["path"] == g["paths"][0]), None)
        loc = evidence_for_file(e) if e else None
        concl.append({
            "id": "C%04d" % cid, "claim": "重复组（内容 SHA-256 相同）",
            "type": "duplicate", "verdict": "verified", "status": "ok",
            "confidence": "high", "method": "sha256_content_match",
            "paths": g["paths"], "locators": [loc] if loc else [],
        })
    for v in version_cands:
        cid += 1
        first = v["paths"][0]
        e = next((x for x in entries if x["path"] == first), None)
        loc = evidence_for_file(e) if e else None
        concl.append({
            "id": "C%04d" % cid, "claim": "版本候选链（归一化主干 %s，启发式）"
            % v["normalized_stem"],
            "type": "version", "verdict": "inferred", "status": "partial",
            "confidence": "medium", "method": "normalized_stem+difflib",
            "paths": v["paths"], "locators": [loc] if loc else [],
        })
    for s in symlinks:
        cid += 1
        text = s.get("target") or ""
        loc = make_locator(s["path"], None, text, line=None)
        concl.append({
            "id": "C%04d" % cid, "claim": "符号链接别名 %s -> %s（broken=%s）"
            % (s["path"], s["target"], s["broken"]),
            "type": "alias", "verdict": "verified", "status": "ok",
            "confidence": "high", "method": "os.readlink",
            "locators": [loc],
        })
    for hg in hardlink_groups:
        cid += 1
        concl.append({
            "id": "C%04d" % cid, "claim": "硬链接组（同一 inode）",
            "type": "alias", "verdict": "verified", "status": "ok",
            "confidence": "high", "method": "st_ino+st_nlink",
            "paths": hg["paths"], "locators": [],
        })
    for n in notes:
        cid += 1
        src = n["source"]
        e = next((x for x in entries if x["path"] == src), None)
        text = e.get("text") if e else ""
        loc = make_locator(src, e.get("sha256") if e else None, text,
                           line=n["line"]) if text else None
        concl.append({
            "id": "C%04d" % cid, "claim": "行政版本说明：%s" % n["heading"],
            "type": "version_note", "verdict": "verified", "status": "ok",
            "confidence": "high", "method": "heading_regex",
            "locators": [loc] if loc else [],
        })
    for kind in ("matches", "mismatches", "missing"):
        for r in recon.get(kind, []):
            cid += 1
            e = next((x for x in entries if x["path"] == r["path"]), None)
            loc = evidence_for_file(e) if e else None
            concl.append({
                "id": "C%04d" % cid, "claim": "对账-%s：%s" % (kind, r["path"]),
                "type": "reconciliation", "verdict":
                "verified" if kind != "missing" else "unverified",
                "status": "ok" if kind == "matches" else "partial",
                "confidence": "high" if kind == "matches" else "medium",
                "method": "catalog_vs_inventory",
                "locators": [loc] if loc else [],
            })
    for a in answers:
        cid += 1
        concl.append({
            "id": "C%04d" % cid, "claim": "机械问答：%s" % a["question"],
            "type": "answer", "verdict": "verified", "status": "ok",
            "confidence": "high", "method": "computed",
            "answer": a["answer"],
            "locators": a.get("locators", []),
        })
    return {"conclusions": concl}


def mechanical_answers(entries, dup_groups, version_cands, symlinks,
                       hardlink_groups, recon, index, texts_by_path):
    answers = []
    n_files = sum(1 for e in entries if e["kind"] in ("file", "symlink"))
    answers.append({"question": "文件总数", "answer": n_files, "locators": []})
    answers.append({"question": "重复文件组数",
                    "answer": len(dup_groups),
                    "locators": []})
    answers.append({"question": "版本候选链数",
                    "answer": len(version_cands),
                    "locators": []})
    answers.append({"question": "断链符号链接数",
                    "answer": sum(1 for s in symlinks if s["broken"]),
                    "locators": []})
    answers.append({"question": "硬链接组数",
                    "answer": len(hardlink_groups),
                    "locators": []})
    answers.append({"question": "对账：matches/mismatches/missing/orphaned",
                    "answer": {"matches": len(recon["matches"]),
                               "mismatches": len(recon["mismatches"]),
                               "missing": len(recon["missing"]),
                               "orphaned": len(recon["orphaned"])},
                    "locators": []})
    # unregistered = files whose basename not mentioned anywhere in catalogs
    unregistered = recon.get("orphaned", [])
    answers.append({"question": "未登记文件", "answer": unregistered,
                    "locators": []})
    return answers


# ---------------------------------------------------------------- assemble

def detect_contract(case_obj):
    if isinstance(case_obj, dict):
        for key in ("output_contract", "output", "result_schema", "contract",
                    "result_schema"):
            if key in case_obj and case_obj[key] is not None:
                return key, case_obj[key]
        # nested under content JSON string?
        content = case_obj.get("content")
        if isinstance(content, str):
            for marker in ("output_contract", "result_schema"):
                idx = content.find('"%s"' % marker)
                if idx >= 0:
                    try:
                        obj = json.loads(content)
                    except ValueError:
                        obj = None
                    if isinstance(obj, dict):
                        return marker, obj.get(marker)
    return None, None


def detect_case_id(case_obj, task_text):
    if isinstance(case_obj, dict):
        for key in ("case_id", "id", "challenge_id", "armiId"):
            if case_obj.get(key):
                return str(case_obj[key])
    m = re.search(r"(?:case_id|challenge_id|案例[号编]?|case)\s*[:：]\s*([A-Za-z0-9_\-]+)",
                  task_text or "")
    if m:
        return m.group(1)
    m = re.search(r"challenge-([A-Za-z0-9]+)", task_text or "")
    if m:
        return m.group(0)
    return "unknown_case"


def fill_contract(contract):
    """Recursively produce placeholder values matching contract shape."""
    if isinstance(contract, dict):
        return {k: fill_contract(v) for k, v in contract.items()}
    if isinstance(contract, list):
        return []
    if contract is None:
        return None
    # schema-like default by type name
    t = str(contract).lower()
    if t in ("string", "str", "text"):
        return ""
    if t in ("integer", "int", "number", "float"):
        return None
    if t in ("boolean", "bool"):
        return None
    if t in ("array", "list"):
        return []
    if t in ("object", "dict", "map"):
        return {}
    return None


def assemble_result(case_id, status, contract, contract_key, computed, sources,
                    uncertainty, pending_human, change_log, errors,
                    degradations, runtime, limitations, evidence_map):
    result = OrderedDict()
    result["case_id"] = case_id
    result["status"] = status
    # backfill contract fields at top level; collision-safe
    if contract is not None:
        for k, v in contract.items():
            if k in RESERVED_KEYS or k == contract_key:
                continue
            result[k] = v
        result["_contract_key"] = contract_key
    else:
        result["_contract_key"] = None
    result["sources"] = sources
    result["evidence_map"] = evidence_map
    result["uncertainty"] = uncertainty
    result["pending_human"] = pending_human
    result["change_log"] = change_log
    result["errors"] = errors
    result["degradations"] = degradations
    result["runtime"] = runtime
    result["limitations"] = limitations
    result["computed"] = computed
    return result


def deep_merge(base, patch):
    """Recursive merge; lists are replaced except append-keys."""
    APPEND = {"sources", "uncertainty", "pending_human", "change_log",
              "errors", "degradations", "limitations"}
    for k, v in patch.items():
        if k in APPEND and isinstance(v, list) and isinstance(base.get(k), list):
            base[k] = base[k] + v
        elif isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


# ---------------------------------------------------------------- artifacts

def write_manifest_csv(entries, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["path", "name", "ext", "dir", "kind", "size", "mtime",
                    "mode", "nlink", "is_symlink", "link_target", "sha256",
                    "text_method", "text_error"])
        for e in sorted(entries, key=lambda x: x["path"]):
            w.writerow([e["path"], e["name"], e["ext"], e["dir"], e["kind"],
                        e["size"], e["mtime"], e["mode"], e["nlink"],
                        e["is_symlink"], e["link_target"] or "",
                        e["sha256"] or "", e["text_method"] or "",
                        e["text_error"] or ""])


def write_tree(entries, dirs, path):
    tree = {}
    for d in dirs:
        node = tree
        if d == ".":
            continue
        for part in [p for p in d.split("/") if p]:
            node = node.setdefault(part, {})
    for e in entries:
        parts = [p for p in e["path"].split("/")]
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = e  # leaf marker (dict with 'path')
    lines = []

    def render(node, prefix, name):
        if "path" in node:  # leaf
            e = node
            mark = ""
            if e.get("is_symlink"):
                mark = " -> %s" % e.get("link_target", "")
            lines.append("%s%s (%s, %s bytes)%s" % (
                prefix, name, e["kind"], e["size"] or "?", mark))
            return
        lines.append("%s%s/" % (prefix, name))
        child_prefix = prefix + "    "
        for child in sorted(node, key=lambda x: (("path" not in node[x]), x)):
            render(node[child], child_prefix, child)

    for top in sorted(tree, key=lambda x: ("path" not in tree[x], x)):
        render(tree[top], "", top)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def write_json(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)


def write_jsonl(items, path):
    with open(path, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False, sort_keys=True) + "\n")


def write_manifest_md(entries, summary, path):
    lines = ["# 资料库交接报告（示例运行产物）", "",
             "- 状态: %s" % summary.get("status", "?"),
             "- 文件数: %d" % summary.get("files", 0),
             "- 目录数: %d" % summary.get("dirs", 0),
             "- 重复组: %d" % summary.get("dup_groups", 0),
             "- 版本候选: %d" % summary.get("version_candidates", 0),
             "- 符号链接: %d（断链 %d）" % (summary.get("symlinks", 0),
                                     summary.get("broken_symlinks", 0)),
             "- 硬链接组: %d" % summary.get("hardlink_groups", 0),
             "", "## 文件清单（节选，完整见 library_manifest.csv）", ""]
    for e in sorted(entries, key=lambda x: x["path"])[:50]:
        lines.append("- %s | %s | %s" % (e["path"], e["kind"], e["sha256"] or "-"))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------- main flow

def run_skeleton(case_json_path, task_path, output_dir, work_dir):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    case_obj = {}
    errors = []
    case_id = "unknown_case"
    contract, contract_key = None, None
    if os.path.isfile(case_json_path):
        try:
            with open(case_json_path, "rb") as f:
                data = f.read()
            text, degraded = decode_text(data)
            case_obj = json.loads(text) if text else {}
            if degraded:
                errors.append({"item": case_json_path, "kind": "bad_encoding",
                               "detail": "case.json decoded with fallback"})
        except (OSError, ValueError) as e:
            errors.append({"item": case_json_path, "kind": "bad_json",
                           "detail": str(e)})
            case_obj = {}
    else:
        errors.append({"item": case_json_path, "kind": "case_json_missing",
                       "detail": "no /input/case.json"})
    task_text = ""
    if os.path.isfile(task_path):
        try:
            with open(task_path, "rb") as f:
                task_text, _ = decode_text(f.read())
        except OSError as e:
            errors.append({"item": task_path, "kind": "task_read_error",
                           "detail": str(e)})
    contract_key, contract = detect_contract(case_obj)
    if contract is None and isinstance(case_obj, dict):
        # fall back: contract may be top-level keys we must echo
        pass
    case_id = detect_case_id(case_obj, task_text)
    placeholder = fill_contract(contract) if contract is not None else None
    status = "input_error" if not os.path.isfile(case_json_path) else "partial"
    result = assemble_result(
        case_id=case_id, status=status, contract=placeholder,
        contract_key=contract_key, computed={}, sources=[], uncertainty=[],
        pending_human=[], change_log=[], errors=errors, degradations=[],
        runtime={"stage": "skeleton"}, limitations=[], evidence_map={})
    if contract is None:
        result["uncertainty"].append({
            "item": "output_contract", "severity": "medium",
            "reason": "case.json 未提供 output_contract，仅产出元数据字段"})
    log("skeleton", "wrote partial skeleton", result["change_log"])
    write_json(result, os.path.join(output_dir, "result.json"))
    return result


def run_full(args):
    t0 = time.time()
    output_dir, work_dir = args.output_dir, args.work_dir
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    case_obj = {}
    errors = []
    if os.path.isfile(args.case_json):
        try:
            with open(args.case_json, "rb") as f:
                text, degraded = decode_text(f.read())
            case_obj = json.loads(text) if text else {}
            if degraded:
                errors.append({"item": args.case_json, "kind": "bad_encoding",
                               "detail": "case.json fallback decoding"})
        except (OSError, ValueError) as e:
            errors.append({"item": args.case_json, "kind": "bad_json",
                           "detail": str(e)})
    else:
        errors.append({"item": args.case_json, "kind": "case_json_missing",
                       "detail": "no /input/case.json"})
    task_text = ""
    if os.path.isfile(args.task_path):
        try:
            with open(args.task_path, "rb") as f:
                task_text, _ = decode_text(f.read())
        except OSError as e:
            errors.append({"item": args.task_path, "kind": "task_read_error",
                           "detail": str(e)})

    contract_key, contract = detect_contract(case_obj)
    case_id = detect_case_id(case_obj, task_text)
    contract_basenames = {os.path.basename(args.case_json),
                          os.path.basename(args.task_path)}

    entries, dirs, inv_errors, inv_deg = inventory(args.input_root)
    errors += inv_errors
    degradations = inv_deg[:]
    hash_errors, hash_deg = hash_and_text(entries, args.input_root)
    errors += hash_errors
    degradations += hash_deg

    texts_by_path = {e["path"]: e.get("text") or "" for e in entries}
    dup_groups = find_duplicates(entries)
    version_cands = version_candidates(entries, texts_by_path)
    symlinks, hardlink_groups = alias_groups(entries)
    notes = parse_version_notes(task_text, entries)

    catalogs = []
    for e in entries:
        if e["kind"] == "file" and e.get("text") and e["ext"] in (
                ".json", ".csv", ".md", ".bib", ".ris"):
            rows, err = catalog_of(e, e["text"])
            if err:
                degradations.append({"item": e["path"], "kind": "catalog_%s" % err,
                                     "detail": "catalog parse degraded"})
                continue
            if rows:
                catalogs.append({"path": e["path"], "rows": rows})
    sqlite_tables, sqlite_rows, sqlite_err = [], [], None
    for e in entries:
        if e["kind"] == "file" and e["ext"] in (".sqlite", ".db", ".sqlite3"):
            tables, rows, err = parse_sqlite(
                os.path.join(args.input_root, e["path"]))
            if err:
                degradations.append({"item": e["path"], "kind": err,
                                     "detail": "sqlite parse degraded"})
            sqlite_tables += tables
            sqlite_rows += rows

    recon = reconcile(entries, catalogs)
    index = build_index(entries)
    answers = mechanical_answers(entries, dup_groups, version_cands, symlinks,
                                 hardlink_groups, recon, index, texts_by_path)
    # attach locators to answer entries where a relevant file exists
    for a in answers:
        if a["question"] == "未登记文件" and a["answer"]:
            locs = []
            for p in a["answer"][:20]:
                e = next((x for x in entries if x["path"] == p), None)
                if e:
                    locs.append(evidence_for_file(e))
            a["locators"] = locs

    evidence_map = build_evidence_map(entries, dup_groups, version_cands,
                                      symlinks, hardlink_groups, notes, recon,
                                      answers, index)
    # flat sources list for compatibility
    sources = []
    for c in evidence_map["conclusions"]:
        for loc in c.get("locators", []):
            if loc:
                sources.append({"item": c["claim"], "locator": loc["locator"],
                                "quote": loc["quote"]})

    summary = {
        "status": "partial", "files": sum(
            1 for e in entries
            if e["kind"] in ("file", "symlink")
            and e["name"] not in contract_basenames),
        "dirs": len([d for d in dirs if d != "."]),
        "dup_groups": len(dup_groups), "version_candidates": len(version_cands),
        "symlinks": len(symlinks),
        "broken_symlinks": sum(1 for s in symlinks if s["broken"]),
        "hardlink_groups": len(hardlink_groups), "version_notes": len(notes),
        "catalogs": len(catalogs), "sqlite_tables": len(sqlite_tables),
        "index_chunks": len(index),
        "matches": len(recon["matches"]), "mismatches": len(recon["mismatches"]),
        "missing": len(recon["missing"]), "orphaned": len(recon["orphaned"]),
    }

    artifacts = [
        "result.json", "library_manifest.csv", "directory_tree.txt",
        "dedup_version.json", "recovery_report.json", "library_index.json",
        "evidence_index.json", "library_manifest.md", "operation_log.jsonl",
    ]
    for a in artifacts:
        p = os.path.join(output_dir, a)
        try:
            with open(p, "w", encoding="utf-8") as f:
                pass
        except OSError as e:
            errors.append({"item": a, "kind": "write_error", "detail": str(e)})

    computed = {
        "files": entries, "summary": summary,
        "duplicate_groups": dup_groups,
        "version_candidates": version_cands, "version_notes": notes,
        "aliases": {"symlinks": symlinks, "hardlink_groups": hardlink_groups},
        "reconciliation": recon, "answers": answers,
        "index": {"chunks": len(index), "file": "library_index.json"},
        "artifacts": artifacts, "catalog_files": [c["path"] for c in catalogs],
    }
    if sqlite_rows:
        computed["sqlite"] = {"tables": sqlite_tables, "rows": sqlite_rows}

    runtime = {
        "python": sys.version.split()[0], "skill_version": VERSION,
        "start_ts": now_iso(), "duration_s": round(time.time() - t0, 3),
        "input_root": forward_slash(args.input_root),
        "output_dir": forward_slash(output_dir),
        "hash_size_limit": HASH_SIZE_LIMIT,
    }

    uncertainty = []
    if contract is None:
        uncertainty.append({"item": "output_contract", "severity": "medium",
                            "reason": "case.json 未提供 output_contract"})
    if any(e["kind"] == "unreadable" for e in entries):
        uncertainty.append({"item": "unreadable_files", "severity": "high",
                            "reason": "存在无权限/不可读文件，已记录到 errors"})
    if sqlite_err:
        uncertainty.append({"item": "sqlite", "severity": "medium",
                            "reason": sqlite_err})

    limitations = []
    if any(e.get("text_error") == "pdf_no_text_layer" for e in entries):
        limitations.append("部分 PDF 无文本层，需平台 pdf-text-tool/ocr-tool 补抽")
    if any(e.get("text_error") == "too_large" for e in entries):
        limitations.append("超大文件跳过内容哈希，保留路径/大小/权限")

    pending_human = []
    if recon["mismatches"]:
        pending_human.append("对账存在 mismatch，需人工确认以哪个版本为准")
    if recon["missing"]:
        pending_human.append("目录清单中存在缺失文件，需人工确认位置或来源")

    status = "insufficient_evidence" if (
        summary["files"] == 0 and not errors) else "partial"
    result = assemble_result(
        case_id=case_id, status=status, contract=contract,
        contract_key=contract_key, computed=computed, sources=sources,
        uncertainty=uncertainty, pending_human=pending_human,
        change_log=[], errors=errors, degradations=degradations,
        runtime=runtime, limitations=limitations, evidence_map=evidence_map)
    log("inventory", "walked %d files, %d dirs" % (
        summary["files"], summary["dirs"]), result["change_log"])
    log("hash", "hashed regular files; %d dup groups" % len(dup_groups),
        result["change_log"])
    log("dedup_version_alias", "%d version candidates, %d symlinks, %d hardlink groups" % (
        len(version_cands), len(symlinks), len(hardlink_groups)),
        result["change_log"])
    log("catalog_reconcile", "%d catalogs; matches=%d mismatches=%d missing=%d orphaned=%d" % (
        len(catalogs), len(recon["matches"]), len(recon["mismatches"]),
        len(recon["missing"]), len(recon["orphaned"])), result["change_log"])
    log("index_answers_evidence", "index chunks=%d, evidence conclusions=%d" % (
        len(index), len(evidence_map["conclusions"])), result["change_log"])

    # write artifacts
    write_manifest_csv(entries, os.path.join(output_dir, "library_manifest.csv"))
    write_tree(entries, dirs, os.path.join(output_dir, "directory_tree.txt"))
    write_json({"duplicate_groups": dup_groups,
                "version_candidates": version_cands,
                "version_notes": notes,
                "aliases": {"symlinks": symlinks,
                            "hardlink_groups": hardlink_groups}},
               os.path.join(output_dir, "dedup_version.json"))
    write_json({"matches": recon["matches"], "mismatches": recon["mismatches"],
                "missing": recon["missing"], "orphaned": recon["orphaned"],
                "catalog_files": [c["path"] for c in catalogs]},
               os.path.join(output_dir, "recovery_report.json"))
    write_json(index, os.path.join(output_dir, "library_index.json"))
    evidence_index = {}
    for c in evidence_map["conclusions"]:
        for loc in c.get("locators", []):
            key = loc["quote"][:80]
            evidence_index.setdefault(key, []).append(
                {"conclusion_id": c["id"], "claim": c["claim"],
                 "locator": loc["locator"], "source_file": loc["source_file"],
                 "source_sha256": loc["source_sha256"],
                 "quote_span": loc["quote_span"]})
    write_json(evidence_index, os.path.join(output_dir, "evidence_index.json"))
    write_manifest_md(entries, summary,
                      os.path.join(output_dir, "library_manifest.md"))
    write_jsonl(result["change_log"],
                os.path.join(output_dir, "operation_log.jsonl"))
    log("finalize", "%d files, %d dup groups, %d version candidates, %d broken symlinks, %d reconcile mismatches -> %s" % (
        summary["files"], len(dup_groups), len(version_cands),
        summary["broken_symlinks"], len(recon["mismatches"]),
        os.path.join(output_dir, "result.json")), result["change_log"])
    write_jsonl(result["change_log"],
                os.path.join(output_dir, "operation_log.jsonl"))
    write_json(result, os.path.join(output_dir, "result.json"))
    return result


def run_update(patch_path, result_path):
    with open(result_path, encoding="utf-8") as f:
        base = json.load(f)
    with open(patch_path, encoding="utf-8") as f:
        patch = json.load(f)
    merged = deep_merge(base, patch)
    log("update", "merged patch %s" % patch_path, merged.get("change_log", []))
    write_json(merged, result_path)
    return merged


def run_check(result_path, case_json_path):
    problems = []
    if not os.path.isfile(result_path):
        problems.append("result.json missing")
        print("FAIL: " + "; ".join(problems))
        return 1
    try:
        with open(result_path, encoding="utf-8") as f:
            r = json.load(f)
    except (OSError, ValueError) as e:
        problems.append("result.json unreadable: %s" % e)
        print("FAIL: " + "; ".join(problems))
        return 1
    if not r.get("case_id"):
        problems.append("case_id empty")
    if r.get("status") not in ("ok", "partial", "insufficient_evidence",
                               "input_error"):
        problems.append("status invalid: %r" % r.get("status"))
    for key in ("sources", "uncertainty", "change_log", "errors",
                "degradations", "runtime", "limitations", "computed",
                "evidence_map"):
        if key not in r:
            problems.append("missing key %s" % key)
    # contract fields check
    if os.path.isfile(case_json_path):
        try:
            with open(case_json_path, encoding="utf-8") as f:
                case = json.load(f)
        except (OSError, ValueError):
            case = {}
        ckey, contract = detect_contract(case)
        if contract is not None:
            for k in contract:
                if k not in r and k not in RESERVED_KEYS:
                    problems.append("contract field missing: %s" % k)
    out_dir = os.path.dirname(result_path)
    for art in ("library_manifest.csv", "directory_tree.txt",
                "dedup_version.json", "recovery_report.json",
                "library_index.json", "evidence_index.json",
                "library_manifest.md", "operation_log.jsonl"):
        p = os.path.join(out_dir, art)
        if not os.path.isfile(p) or os.path.getsize(p) == 0:
            problems.append("artifact empty/missing: %s" % art)
    if problems:
        print("FAIL: " + "; ".join(problems))
        return 1
    print("OK: case_id=%s status=%s" % (r.get("case_id"), r.get("status")))
    return 0


def run_search(query, index_path, top):
    with open(index_path, encoding="utf-8") as f:
        index = json.load(f)
    hits = search_index(index, query, top=top)
    print(json.dumps(hits, ensure_ascii=False, indent=2))
    return 0


def build_argparser():
    p = argparse.ArgumentParser(prog="main.py",
                                description="library evidence repair engine")
    p.add_argument("--case-json", default="/input/case.json")
    p.add_argument("--task-path", default="/input/task.md")
    p.add_argument("--input-root", default="/input")
    p.add_argument("--output-dir", default="/app/output")
    p.add_argument("--work-dir", default="/app/work")
    p.add_argument("--skeleton-only", action="store_true")
    p.add_argument("--update", metavar="PATCH.json")
    p.add_argument("--result", metavar="RESULT.json", default="/app/output/result.json")
    p.add_argument("--check", metavar="RESULT.json")
    p.add_argument("--search", metavar="QUERY")
    p.add_argument("--index", metavar="INDEX.json",
                   default="/app/output/library_index.json")
    p.add_argument("--top", type=int, default=10)
    return p


def main(argv=None):
    args = build_argparser().parse_args(argv)
    if args.search is not None:
        return run_search(args.search, args.index, args.top)
    if args.check:
        return run_check(args.check, args.case_json)
    if args.update:
        run_update(args.update, args.result)
        return 0
    if args.skeleton_only:
        run_skeleton(args.case_json, args.task_path, args.output_dir,
                     args.work_dir)
        return 0
    run_full(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
