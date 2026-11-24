# -*- coding: utf-8 -*-
"""core_impl.py — deterministic document-library inventory, repair, retrieval scaffolding.

Standard-library only. Every conclusion carries a confidence level and an evidence
basis; anything the data does not support is reported explicitly instead of guessed.
"""
import argparse
import csv
import difflib
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import unicodedata
from collections import Counter, OrderedDict, defaultdict
from datetime import datetime, timezone

VERSION = "1.1.0"

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_CONTENT_HASH_BYTES = 20 * 1024 * 1024   # 20 MB
MAX_TEXT_READ_BYTES = 4 * 1024 * 1024       # 4 MB for text parsing
MAX_INDEX_CONTENT_BYTES = 512 * 1024        # 512 KB content indexed per text file
MAX_INDEX_TERMS_PER_FILE = 2000
MAX_SNIPPET_LEN = 320

CONTRACT_KEYS = (
    "output_contract", "output", "result_schema", "contract",
    "expected_output", "output_schema", "result_contract", "output_format",
)
RESERVED_KEYS = {
    "case_id", "status", "sources", "uncertainty", "change_log", "computed",
    "errors", "degradations", "runtime", "limitations", "pending_human",
}
STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
CONFIDENCE_LEVELS = ("certain", "high", "medium", "low", "unknown")

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".json", ".csv", ".tsv", ".bib", ".ris",
    ".xml", ".html", ".htm", ".py", ".yaml", ".yml", ".ini", ".cfg",
    ".log", ".rst", ".tex", ".csv", ".tsv",
}
OFFICE_EXTENSIONS = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".bmp", ".svg", ".webp"}

VER_DIGITS_RE = re.compile(r"(?i)(?:v|ver|version|rev|r)?\s*\d+(?:\.\d+){0,3}")
VER_WORD_RE = re.compile(r"(?i)\b(?:draft|final|copy|edit|old|new|latest|clean|updated|revised|review)\b")
DATE_TOKEN_RE = re.compile(r"(19|20)\d{2}[-_]?\d{2}[-_]?\d{2}")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def log(entry_kind, detail):
    """Append one deterministic (except timestamp) change-log line to the work dir."""
    work = os.environ.get("SKILL_WORK_DIR")
    if not work:
        return
    try:
        os.makedirs(work, exist_ok=True)
        path = os.path.join(work, "operation_log.jsonl")
        rec = {
            "time": now_iso(),
            "actor": "scripts/core_impl.py",
            "step": entry_kind,
            "detail": detail,
        }
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Reading and hashing
# --------------------------------------------------------------------------- #

def read_text(path, max_bytes=MAX_TEXT_READ_BYTES):
    """Return (text, ok, reason). Never raises on encoding problems."""
    try:
        size = os.path.getsize(path)
        if size > max_bytes:
            with open(path, "rb") as fh:
                head = fh.read(max_bytes)
            return head.decode("utf-8", errors="replace"), True, "truncated_too_large"
    except OSError as exc:
        return "", False, "unreadable:%s" % exc
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), True, "ok"
        except (UnicodeDecodeError, UnicodeError):
            continue
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        return raw.decode("utf-8", errors="replace"), True, "decoded_replace"
    except OSError as exc:
        return "", False, "unreadable:%s" % exc


def file_sha256(path, max_bytes=MAX_CONTENT_HASH_BYTES):
    """Return (hexdigest, ok, reason)."""
    try:
        st = os.stat(path)
        if not stat.S_ISREG(st.st_mode):
            return None, False, "non_regular"
        if st.st_size > max_bytes:
            return None, False, "too_large"
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest(), True, "ok"
    except OSError as exc:
        return None, False, "unreadable:%s" % exc


def sniff_kind(path, head):
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        ext = os.path.splitext(path)[1].lower()
        if ext in (".docx", ".xlsx", ".pptx", ".odt", ".ods"):
            return "office_zip"
        return "zip_archive"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "office_ole"
    if head.startswith(b"SQLite format 3\x00"):
        return "sqlite"
    if head.startswith(b"\x89PNG"):
        return "image_png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image_jpeg"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "image_gif"
    if head.startswith(b"%!"):
        return "postscript"
    if head.startswith(b"\x7fELF"):
        return "binary_elf"
    return "unknown"


def classify_file(path, head):
    ext = os.path.splitext(path)[1].lower()
    kind = sniff_kind(path, head)
    if kind == "unknown":
        if ext in TEXT_EXTENSIONS:
            kind = "text"
        elif ext in OFFICE_EXTENSIONS:
            kind = "office"
        elif ext in IMAGE_EXTENSIONS:
            kind = "image"
        elif ext:
            kind = "binary_" + ext.lstrip(".")
    return kind


def mode_octal(mode):
    return format(stat.S_IMODE(mode), "04o")


# --------------------------------------------------------------------------- #
# Inventory
# --------------------------------------------------------------------------- #

def inventory(input_root):
    files = []
    dirs = []
    hardlink_groups = defaultdict(list)
    inode_map = {}
    errors = []
    degradations = []
    if not os.path.isdir(input_root):
        return {
            "files": [], "dirs": [], "hardlink_groups": [],
            "errors": [{"code": "input_missing", "detail": "input root not a directory: %s" % input_root}],
            "degradations": [],
        }
    for dirpath, dirnames, filenames in os.walk(input_root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        for dn in dirnames:
            full = os.path.join(dirpath, dn)
            rel = os.path.relpath(full, input_root)
            try:
                st = os.stat(full, follow_symlinks=False)
                dirs.append({
                    "path": rel, "absolute": full,
                    "mode": mode_octal(st.st_mode),
                    "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                    "is_symlink": stat.S_ISLNK(st.st_mode),
                })
            except OSError as exc:
                errors.append({"code": "stat_error", "path": rel, "detail": str(exc)})
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, input_root)
            entry = {
                "path": rel, "absolute": full, "name": fn,
                "parent": os.path.relpath(dirpath, input_root),
                "size": None, "mode": None, "mtime": None,
                "kind": "unknown", "sha256": None,
                "is_symlink": False, "link_target": None, "broken": None,
                "inode": None, "nlink": None,
                "hash_status": None, "hash_reason": None,
            }
            try:
                st = os.lstat(full)
                entry["mode"] = mode_octal(st.st_mode)
                entry["mtime"] = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
                entry["inode"] = int(getattr(st, "st_ino", 0) or 0)
                entry["nlink"] = int(getattr(st, "st_nlink", 0) or 0)
                if stat.S_ISLNK(st.st_mode):
                    entry["is_symlink"] = True
                    try:
                        target = os.readlink(full)
                    except OSError as exc:
                        target = None
                        errors.append({"code": "readlink_error", "path": rel, "detail": str(exc)})
                    entry["link_target"] = target
                    if target is not None:
                        resolved = target if os.path.isabs(target) else os.path.join(dirpath, target)
                        entry["broken"] = not os.path.exists(resolved)
                    entry["kind"] = "symlink"
                    entry["hash_status"] = "symlink"
                    entry["hash_reason"] = "symlink_not_hashed"
                elif stat.S_ISREG(st.st_mode):
                    entry["size"] = st.st_size
                    try:
                        with open(full, "rb") as fh:
                            head = fh.read(4096)
                    except OSError as exc:
                        head = b""
                        errors.append({"code": "read_error", "path": rel, "detail": str(exc)})
                    entry["kind"] = classify_file(full, head)
                    digest, ok, reason = file_sha256(full)
                    entry["sha256"] = digest
                    entry["hash_status"] = "ok" if ok else reason.split(":")[0]
                    entry["hash_reason"] = reason
                    if not ok:
                        degradations.append({
                            "code": entry["hash_status"], "path": rel,
                            "severity": "warning", "detail": reason,
                            "action": "content_hash_skipped",
                        })
                    if entry["nlink"] > 1 and entry["inode"]:
                        key = (st.st_dev, entry["inode"])
                        hardlink_groups[key].append(rel)
                        inode_map[rel] = key
                else:
                    entry["kind"] = "non_regular"
                    entry["hash_status"] = "non_regular"
                    entry["hash_reason"] = "special_file_not_hashed"
            except OSError as exc:
                errors.append({"code": "stat_error", "path": rel, "detail": str(exc)})
                entry["kind"] = "unreadable"
                entry["hash_status"] = "unreadable"
            files.append(entry)

    hardlink_aliases = []
    for key, paths in hardlink_groups.items():
        if len(paths) > 1:
            hardlink_aliases.append({
                "inode": key[1], "dev": key[0], "paths": sorted(paths),
                "note": "same_inode_hardlinks",
            })
    hardlink_aliases.sort(key=lambda x: x["paths"][0])
    return {
        "files": files, "dirs": dirs,
        "hardlink_groups": hardlink_aliases,
        "errors": errors, "degradations": degradations,
    }


# --------------------------------------------------------------------------- #
# Deduplication (content SHA-256)
# --------------------------------------------------------------------------- #

def dedup(files):
    groups = defaultdict(list)
    for f in files:
        if f.get("sha256"):
            groups[f["sha256"]].append(f["path"])
    result = []
    for digest, paths in sorted(groups.items()):
        if len(paths) > 1:
            result.append({
                "sha256": digest,
                "paths": sorted(paths),
                "size": next((f["size"] for f in files if f["path"] == paths[0]), None),
                "basis": "content_sha256",
                "confidence": "certain",
            })
    return result


# --------------------------------------------------------------------------- #
# Version-chain candidates (normalized stem + content similarity)
# --------------------------------------------------------------------------- #

def normalize_stem(name):
    base = os.path.splitext(name)[0]
    base = unicodedata.normalize("NFKC", base).lower()
    base = DATE_TOKEN_RE.sub(" ", base)
    base = VER_DIGITS_RE.sub(" ", base)
    base = VER_WORD_RE.sub(" ", base)
    base = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", base)
    base = re.sub(r"\s+", " ", base).strip()
    return base


def version_candidates(files):
    by_stem = defaultdict(list)
    for f in files:
        if f.get("kind") == "symlink":
            continue
        stem = normalize_stem(f["name"])
        if stem:
            by_stem[stem].append(f)
    candidates = []
    for stem, items in sorted(by_stem.items()):
        if len(items) < 2:
            continue
        items = sorted(items, key=lambda x: x["path"])
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i], items[j]
                sim = None
                if a.get("sha256") and b.get("sha256"):
                    sim = 1.0 if a["sha256"] == b["sha256"] else None
                if sim is None and a.get("size") is not None and b.get("size") is not None:
                    # only text files get content-similarity scoring
                    ta, oka, _ = read_text(a["absolute"]) if a["kind"] in ("text",) else ("", False, "")
                    tb, okb, _ = read_text(b["absolute"]) if b["kind"] in ("text",) else ("", False, "")
                    if oka and okb and ta and tb:
                        sim = round(difflib.SequenceMatcher(None, ta[:60000], tb[:60000]).ratio(), 4)
                candidates.append({
                    "stem": stem,
                    "a": a["path"], "b": b["path"],
                    "similarity": sim,
                    "basis": "content_sha256" if sim == 1.0 else "normalized_stem+text_similarity",
                    "heuristic": sim is None or sim < 1.0,
                    "confidence": "certain" if sim == 1.0 else ("medium" if sim is not None else "low"),
                })
    return candidates


# --------------------------------------------------------------------------- #
# Aliases (symlinks incl. broken + hardlinks)
# --------------------------------------------------------------------------- #

def aliases(files, hardlink_groups):
    symlinks = []
    for f in files:
        if f.get("is_symlink"):
            symlinks.append({
                "path": f["path"], "target": f.get("link_target"),
                "broken": f.get("broken"),
                "confidence": "certain",
                "evidence": "lstat+readlink",
            })
    symlinks.sort(key=lambda x: x["path"])
    return {"symlinks": symlinks, "hardlinks": hardlink_groups}


# --------------------------------------------------------------------------- #
# Administrative version notes / library manifests parsing
# --------------------------------------------------------------------------- #

def _looks_like_manifest(obj):
    if isinstance(obj, dict):
        keys = {str(k).lower() for k in obj.keys()}
        if keys & {"files", "entries", "items", "documents", "library", "records", "versions"}:
            return True
    if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
        sample_keys = {str(k).lower() for d in obj[:5] for k in d.keys()}
        if sample_keys & {"path", "file", "filename", "title", "version", "hash", "sha256", "sha1"}:
            return True
    return False


def parse_json_manifest(text, source):
    try:
        data = json.loads(text)
    except Exception as exc:
        return {"source": source, "type": "json", "parse_ok": False, "error": str(exc)}
    if _looks_like_manifest(data):
        return {"source": source, "type": "json", "parse_ok": True, "entries": data}
    return {"source": source, "type": "json", "parse_ok": True, "entries": [], "note": "no_manifest_shape"}


def parse_csv_manifest(text, source):
    try:
        rows = list(csv.DictReader(io_string(text)))
    except Exception as exc:
        return {"source": source, "type": "csv", "parse_ok": False, "error": str(exc)}
    return {"source": source, "type": "csv", "parse_ok": True, "entries": rows}


def parse_bibtex(text, source):
    entries = []
    pattern = re.compile(r"@\s*(\w+)\s*\{\s*([^,]+)\s*,", re.I)
    for m in pattern.finditer(text):
        etype, key = m.group(1), m.group(2).strip()
        entries.append({"type": etype, "key": key})
    return {"source": source, "type": "bibtex", "parse_ok": True, "entries": entries}


def parse_ris(text, source):
    entries = []
    cur = None
    for line in text.splitlines():
        if len(line) < 2:
            continue
        tag = line[:2].strip()
        if tag and len(line) >= 6 and line[2:6] == "  - ":
            val = line[6:].strip()
            if tag == "TY":
                cur = {"type": val, "fields": {}}
                entries.append(cur)
            elif cur is not None:
                cur["fields"][tag] = val
    return {"source": source, "type": "ris", "parse_ok": True, "entries": entries}


def parse_md_manifest(text, source):
    # extract markdown tables and any "version"/"library" heading blocks
    tables = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if "|" in lines[i] and i + 1 < len(lines) and re.match(r"^\s*\|?[\s:\-|]+\|?\s*$", lines[i + 1]):
            header = [c.strip() for c in lines[i].strip("|").split("|")]
            rows = []
            j = i + 2
            while j < len(lines) and "|" in lines[j]:
                cells = [c.strip() for c in lines[j].strip("|").split("|")]
                rows.append(dict(zip(header, cells)))
                j += 1
            tables.append({"header": header, "rows": rows})
            i = j
        else:
            i += 1
    headings = [ln.strip() for ln in lines if re.match(r"^#{1,6}\s+", ln)]
    return {
        "source": source, "type": "markdown", "parse_ok": True,
        "tables": tables, "headings": headings,
        "entries": [r for t in tables for r in t["rows"]],
    }


def parse_sqlite(path, source):
    try:
        con = sqlite3.connect(path)
        cur = con.cursor()
        cur.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('table','view') ORDER BY name")
        schema = [{"name": n, "sql": s} for n, s in cur.fetchall()]
        con.close()
        return {"source": source, "type": "sqlite", "parse_ok": True, "schema": schema}
    except Exception as exc:
        return {"source": source, "type": "sqlite", "parse_ok": False, "error": str(exc)}


def io_string(text):
    import io
    return io.StringIO(text)


def parse_manifests(input_root, files):
    notes = []
    degradations = []
    for f in files:
        if f.get("kind") == "symlink":
            continue
        ext = os.path.splitext(f["name"])[1].lower()
        if ext == ".sqlite" or ext == ".db" or ext == ".sqlite3":
            res = parse_sqlite(f["absolute"], f["path"])
            notes.append(res)
            if not res["parse_ok"]:
                degradations.append({
                    "code": "sqlite_open_error", "path": f["path"],
                    "severity": "warning", "detail": res.get("error"),
                })
        elif ext in (".json", ".csv", ".tsv", ".md", ".markdown", ".bib", ".ris"):
            text, ok, reason = read_text(f["absolute"])
            if not ok:
                degradations.append({
                    "code": "unreadable_manifest", "path": f["path"],
                    "severity": "warning", "detail": reason,
                })
                continue
            if ext == ".json":
                notes.append(parse_json_manifest(text, f["path"]))
            elif ext in (".csv", ".tsv"):
                notes.append(parse_csv_manifest(text, f["path"]))
            elif ext in (".md", ".markdown"):
                notes.append(parse_md_manifest(text, f["path"]))
            elif ext == ".bib":
                notes.append(parse_bibtex(text, f["path"]))
            elif ext == ".ris":
                notes.append(parse_ris(text, f["path"]))
    return notes, degradations


# --------------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------------- #

def _extract_listed_paths(notes):
    """Best-effort: pull candidate file identifiers out of parsed manifests."""
    listed = []
    for n in notes:
        entries = n.get("entries") or []
        for e in entries:
            if isinstance(e, dict):
                for k in ("path", "file", "filename", "file_path", "relpath", "title"):
                    if e.get(k):
                        listed.append(str(e[k]))
                        break
    return listed


def reconcile(files, notes):
    actual = {f["path"] for f in files}
    actual_names = {os.path.basename(f["path"]) for f in files}
    listed = _extract_listed_paths(notes)
    listed_set = set(listed)
    listed_norm = {os.path.basename(p) for p in listed}
    matches = [p for p in listed if p in actual]
    mismatches = [p for p in listed if p not in actual and os.path.basename(p) in actual_names]
    missing = [p for p in listed if p not in actual and os.path.basename(p) not in actual_names]
    orphaned = sorted((actual - listed_set) - {"case.json", "task.md"})
    return {
        "matches": sorted(set(matches)),
        "mismatches": sorted(set(mismatches)),
        "missing": sorted(set(missing)),
        "orphaned": orphaned,
        "listed_count": len(set(listed)),
        "actual_count": len(actual),
        "confidence": "medium" if listed else "low",
        "note": "reconciliation is heuristic when manifests are partial or non-machine-readable",
    }


# --------------------------------------------------------------------------- #
# Index + mechanical retrieval
# --------------------------------------------------------------------------- #

def tokenize(text):
    text = unicodedata.normalize("NFKC", text or "").lower()
    tokens = re.findall(r"[a-z0-9]+", text)
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    bigrams = ["".join(cjk[i:i + 2]) for i in range(max(0, len(cjk) - 1))]
    return tokens + bigrams + cjk


def build_index(files, dedup_groups, version_cands, aliases_rec, notes, recon):
    terms = defaultdict(list)
    entries = {}
    for f in files:
        name_tokens = tokenize(f["name"])
        meta_tokens = tokenize(f.get("kind", "") + " " + (f.get("parent", "")))
        terms_set = set(name_tokens) | set(meta_tokens)
        if f.get("kind") in ("text",) and f.get("absolute"):
            text, ok, _ = read_text(f["absolute"], max_bytes=MAX_INDEX_CONTENT_BYTES)
            if ok and text:
                content_tokens = tokenize(text)[:MAX_INDEX_TERMS_PER_FILE]
                terms_set |= set(content_tokens)
        entry = {
            "path": f["path"], "name": f["name"], "kind": f["kind"],
            "absolute": f.get("absolute"), "parent": f.get("parent"),
            "size": f.get("size"), "mtime": f.get("mtime"), "mode": f.get("mode"),
            "sha256": f.get("sha256"), "is_symlink": f.get("is_symlink"),
            "terms": sorted(terms_set),
        }
        entries[f["path"]] = entry
        for t in terms_set:
            terms[t].append(f["path"])
    for key in terms:
        terms[key] = sorted(set(terms[key]))
    return {
        "files": entries,
        "terms": {k: v for k, v in sorted(terms.items())},
        "duplicate_groups": dedup_groups,
        "version_candidates": version_cands,
        "aliases": aliases_rec,
        "reconciliation": recon,
        "version_notes": notes,
        "generated_at": now_iso(),
        "indexer": "core_impl.py@%s" % VERSION,
    }


def search_terms(index, query_terms):
    scores = Counter()
    for t in query_terms:
        for p in index["terms"].get(t, []):
            scores[p] += 1
    return scores


def snippet_around(text, term, radius=80):
    low = text.lower()
    pos = low.find(term.lower())
    if pos < 0:
        return ""
    start = max(0, pos - radius)
    end = min(len(text), pos + len(term) + radius)
    return text[start:end].replace("\n", " ").strip()


def mechanical_answers(case_json, files, index, dedup_groups, version_cands, recon):
    answers = []
    query_keys = ("questions", "queries", "search", "asks", "prompts", "search_questions")
    queries = []
    if isinstance(case_json, dict):
        for k in query_keys:
            v = case_json.get(k)
            if isinstance(v, str):
                queries.append(v)
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, str):
                        queries.append(item)
                    elif isinstance(item, dict):
                        for qk in ("question", "query", "text", "prompt"):
                            if item.get(qk):
                                queries.append(str(item[qk]))
                                break
    for q in queries:
        terms = tokenize(q)
        scored = search_terms(index, terms)
        hits = []
        for path, score in scored.most_common(10):
            entry = index["files"].get(path, {})
            snippet = ""
            locator = path
            if entry.get("kind") in ("text",) and entry.get("absolute"):
                text, ok, _ = read_text(entry["absolute"])
                if ok and terms:
                    for t in terms:
                        snip = snippet_around(text, t)
                        if snip:
                            snippet = snip[:MAX_SNIPPET_LEN]
                            break
            hits.append({
                "path": path, "score": score,
                "locator": locator, "snippet": snippet or None,
            })
        answers.append({
            "query": q,
            "hits": hits,
            "method": "token_overlap_mechanical",
            "confidence": "low" if hits else "unknown",
            "note": "mechanical pre-filter; verify semantically with pdftotext/OCR before finalizing",
        })
    return answers


# --------------------------------------------------------------------------- #
# Contract echo
# --------------------------------------------------------------------------- #

def echo_contract(case_json):
    """Discover the output contract and return (source_key, fields_dict, meta)."""
    if not isinstance(case_json, dict):
        return None, {}, {"found": False}
    for key in CONTRACT_KEYS:
        if key in case_json:
            val = case_json[key]
            if isinstance(val, dict):
                if "properties" in val and isinstance(val["properties"], dict):
                    fields = {}
                    for name, spec in val["properties"].items():
                        t = spec.get("type") if isinstance(spec, dict) else None
                        fields[name] = _placeholder_for_type(t)
                    return key, fields, {"found": True, "schema": True}
                fields = {}
                for name, hint in val.items():
                    fields[name] = _placeholder_for_type(hint)
                return key, fields, {"found": True, "schema": False}
            if isinstance(val, list):
                return key, {str(v): None for v in val if isinstance(v, (str, int))}, {"found": True, "schema": False}
            if isinstance(val, str):
                return key, {val: None}, {"found": True, "schema": False}
    return None, {}, {"found": False}


def _placeholder_for_type(t):
    if isinstance(t, str):
        if t in ("array", "list"):
            return []
        if t in ("object", "dict"):
            return {}
    return None


# --------------------------------------------------------------------------- #
# Result assembly
# --------------------------------------------------------------------------- #

def make_skeleton(case_json, task_text):
    case_id = _find_case_id(case_json, task_text)
    ckey, cfields, cmeta = echo_contract(case_json)
    result = OrderedDict()
    result["case_id"] = case_id
    result["status"] = "partial"
    for name, placeholder in cfields.items():
        if name in RESERVED_KEYS:
            result.setdefault("computed", OrderedDict())["_contract_%s" % name] = placeholder
        else:
            result[name] = placeholder
    result.setdefault("computed", OrderedDict())["_skeleton"] = {
        "stage": "skeleton", "contract_echo": cmeta, "contract_key": ckey,
    }
    result["sources"] = []
    result["uncertainty"] = [{
        "item": "skeleton", "reason": "initial partial skeleton written before analysis",
        "severity": "info", "evidence_basis": "none_yet",
    }]
    result["pending_human"] = []
    result["errors"] = []
    result["degradations"] = []
    result["limitations"] = [{
        "item": "skeleton_only",
        "detail": "inventory/analysis not yet run; treat computed data as absent",
        "confidence": "certain",
    }]
    result["change_log"] = [{
        "time": now_iso(), "actor": "scripts/core_impl.py", "step": "skeleton",
        "detail": "partial skeleton written (contract echoed with placeholders)",
    }]
    result["computed"] = result.get("computed", OrderedDict())
    return result, ckey, cfields


def _find_case_id(case_json, task_text):
    if isinstance(case_json, dict):
        for k in ("case_id", "id", "challenge_id", "caseId", "case"):
            if case_json.get(k):
                return str(case_json[k])
        content = case_json.get("content")
        if isinstance(content, str):
            m = re.search(r"challenge_id[:\s`]+([A-Za-z0-9\-_]+)", content)
            if m:
                return m.group(1)
    if task_text:
        m = re.search(r"challenge_id[:\s`]+([A-Za-z0-9\-_]+)", task_text)
        if m:
            return m.group(1)
    return "unknown"


def run_full(args):
    input_root = args.get("input_root", DEFAULT_INPUT)
    output_dir = args.get("output_dir", DEFAULT_OUTPUT)
    work_dir = args.get("work_dir", DEFAULT_WORK)
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    os.environ["SKILL_WORK_DIR"] = work_dir

    case_json = _load_json(args.get("case_json", os.path.join(input_root, "case.json")))
    task_text = _load_text(args.get("task_path", os.path.join(input_root, "task.md")))

    result, ckey, cfields = make_skeleton(case_json, task_text)
    result["status"] = "partial"

    inv = inventory(input_root)
    dup_groups = dedup(inv["files"])
    ver_cands = version_candidates(inv["files"])
    alias_rec = aliases(inv["files"], inv["hardlink_groups"])
    notes, note_degs = parse_manifests(input_root, inv["files"])
    recon = reconcile(inv["files"], notes)
    index = build_index(inv["files"], dup_groups, ver_cands, alias_rec, notes, recon)
    answers = mechanical_answers(case_json, inv["files"], index, dup_groups, ver_cands, recon)

    result["errors"] = inv["errors"]
    result["degradations"] = inv["degradations"] + note_degs
    material_files = [f for f in inv["files"] if f["path"] not in ("case.json", "task.md")]
    if not material_files:
        result["status"] = "insufficient_evidence"
    summary = {
        "stage": "inventory",
        "file_count": len(inv["files"]),
        "material_file_count": len(material_files),
        "dir_count": len(inv["dirs"]),
        "duplicate_group_count": len(dup_groups),
        "version_candidate_count": len(ver_cands),
        "broken_symlink_count": sum(1 for s in alias_rec["symlinks"] if s.get("broken")),
        "reconciliation": {
            "matches": len(recon["matches"]),
            "mismatches": len(recon["mismatches"]),
            "missing": len(recon["missing"]),
            "orphaned": len(recon["orphaned"]),
        },
    }
    result["computed"]["files"] = inv["files"]
    result["computed"]["directories"] = inv["dirs"]
    result["computed"]["duplicate_groups"] = dup_groups
    result["computed"]["version_candidates"] = ver_cands
    result["computed"]["aliases"] = alias_rec
    result["computed"]["version_notes"] = notes
    result["computed"]["reconciliation"] = recon
    result["computed"]["index"] = index
    result["computed"]["answers"] = answers
    result["computed"]["summary"] = summary

    result["limitations"] = _initial_limitations(inv)
    result["uncertainty"] = _initial_uncertainty(inv, recon, notes)

    artifacts = write_artifacts(output_dir, work_dir, result, inv, dup_groups, ver_cands, alias_rec, notes, recon, index)
    result["computed"]["artifacts"] = artifacts
    result["change_log"].append({
        "time": now_iso(), "actor": "scripts/core_impl.py", "step": "inventory",
        "detail": "inventory+dedup+version+alias+notes+reconcile+index+answers complete",
    })
    write_result(output_dir, result)
    log("inventory", "files=%d dup=%d ver=%d broken_symlink=%d" % (
        len(inv["files"]), len(dup_groups), len(ver_cands),
        sum(1 for s in alias_rec["symlinks"] if s.get("broken"))))
    return result, ckey, cfields


def _initial_limitations(inv):
    lims = [{
        "item": "scope", "detail": "deterministic layer only; semantic retrieval/chapter localization "
        "is finalized by the agent on top of computed candidates", "confidence": "certain",
    }]
    if not inv["files"]:
        lims.append({
            "item": "no_files", "detail": "input tree contains no regular files; no content-level conclusions possible",
            "confidence": "certain",
        })
    return lims


def _initial_uncertainty(inv, recon, notes):
    un = []
    if not inv["files"]:
        un.append({
            "item": "inventory", "reason": "empty or unreadable input tree",
            "severity": "high", "evidence_basis": "none", "mitigation": "request materials or mark insufficient_evidence",
        })
    if not notes:
        un.append({
            "item": "version_notes", "reason": "no JSON/CSV/MD/BibTeX/RIS/SQLite manifest detected",
            "severity": "medium", "evidence_basis": "none", "mitigation": "report manifests as not_reported",
        })
    if recon["confidence"] == "low":
        un.append({
            "item": "reconciliation", "reason": "no machine-readable manifest found; orphaned/missing is approximate",
            "severity": "medium", "evidence_basis": "none",
        })
    return un


# --------------------------------------------------------------------------- #
# Artifacts
# --------------------------------------------------------------------------- #

def write_artifacts(output_dir, work_dir, result, inv, dup_groups, ver_cands, alias_rec, notes, recon, index):
    artifact_paths = {}
    # library_manifest.csv
    csv_path = os.path.join(output_dir, "library_manifest.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["path", "name", "parent", "kind", "size", "mtime", "mode",
                    "sha256", "is_symlink", "link_target", "broken", "nlink", "hash_status"])
        for f in inv["files"]:
            w.writerow([
                f["path"], f["name"], f["parent"], f["kind"], f.get("size"),
                f.get("mtime"), f.get("mode"), f.get("sha256"),
                f.get("is_symlink"), f.get("link_target"), f.get("broken"),
                f.get("nlink"), f.get("hash_status"),
            ])
    artifact_paths["library_manifest.csv"] = csv_path

    # directory_tree.txt
    tree_path = os.path.join(output_dir, "directory_tree.txt")
    lines = []
    for d in sorted(inv["dirs"], key=lambda x: x["path"]):
        children = [f for f in inv["files"] if f["parent"] == d["path"]]
        marker = " [空目录]" if not children else ""
        lines.append("%s/%s" % (d["path"], marker))
    for f in sorted(inv["files"], key=lambda x: x["path"]):
        lines.append(f["path"])
    with open(tree_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + ("\n" if lines else ""))
    artifact_paths["directory_tree.txt"] = tree_path

    # dedup_version.json
    dv_path = os.path.join(output_dir, "dedup_version.json")
    dv = {
        "duplicate_groups": dup_groups,
        "version_candidates": ver_cands,
        "version_notes": notes,
        "aliases": alias_rec,
    }
    _dump_json(dv_path, dv)
    artifact_paths["dedup_version.json"] = dv_path

    # recovery_report.json
    rr_path = os.path.join(output_dir, "recovery_report.json")
    rr = {
        "reconciliation": recon,
        "errors": inv["errors"],
        "degradations": inv["degradations"],
    }
    _dump_json(rr_path, rr)
    artifact_paths["recovery_report.json"] = rr_path

    # library_index.json
    idx_path = os.path.join(output_dir, "library_index.json")
    _dump_json(idx_path, index)
    artifact_paths["library_index.json"] = idx_path

    # library_manifest.md
    md_path = os.path.join(output_dir, "library_manifest.md")
    _write_md(md_path, result, inv, dup_groups, ver_cands, recon)
    artifact_paths["library_manifest.md"] = md_path

    # operation_log.jsonl (copy of work log)
    op_path = os.path.join(output_dir, "operation_log.jsonl")
    work_log = os.path.join(work_dir, "operation_log.jsonl")
    if os.path.exists(work_log):
        with open(work_log, "r", encoding="utf-8") as src, open(op_path, "w", encoding="utf-8") as dst:
            dst.write(src.read())
    else:
        open(op_path, "w", encoding="utf-8").close()
    artifact_paths["operation_log.jsonl"] = op_path

    return artifact_paths


def _write_md(path, result, inv, dup_groups, ver_cands, recon):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# Library manifest (交接报告)\n\n")
        fh.write("- case_id: `%s`\n- status: `%s`\n- generated: %s\n\n" % (
            result.get("case_id"), result.get("status"), now_iso()))
        fh.write("## Summary\n\n")
        s = result.get("computed", {}).get("summary", {})
        for k, v in s.items():
            fh.write("- %s: %s\n" % (k, v))
        fh.write("\n## Files (%d)\n\n" % len(inv["files"]))
        for f in inv["files"]:
            fh.write("- `%s` (%s, %s bytes, sha256=%s)\n" % (
                f["path"], f["kind"], f.get("size"), (f.get("sha256") or "")[:12]))
        fh.write("\n## Duplicate groups (%d)\n\n" % len(dup_groups))
        for g in dup_groups:
            fh.write("- %s\n" % "; ".join(g["paths"]))
        fh.write("\n## Version candidates (%d)\n\n" % len(ver_cands))
        for c in ver_cands:
            fh.write("- %s ~ %s (similarity=%s)\n" % (c["a"], c["b"], c["similarity"]))
        fh.write("\n## Reconciliation\n\n")
        for k in ("matches", "mismatches", "missing", "orphaned"):
            fh.write("- %s: %d\n" % (k, len(recon.get(k, []))))


def _dump_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=False)


def write_result(output_dir, result):
    path = os.path.join(output_dir, "result.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2, sort_keys=False)
    return path


def _load_json(path):
    if not path or not os.path.exists(path):
        return None
    text, ok, _ = read_text(path)
    if not ok:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _load_text(path):
    if not path or not os.path.exists(path):
        return ""
    text, ok, _ = read_text(path)
    return text if ok else ""


# --------------------------------------------------------------------------- #
# Patch merge (agent semantic conclusions)
# --------------------------------------------------------------------------- #

def apply_patch(result, patch):
    if not isinstance(patch, dict):
        return result, ["patch_not_dict"]
    issues = []
    for key in ("computed",):
        if key in patch and isinstance(patch[key], dict):
            result.setdefault("computed", OrderedDict())
            for sub, val in patch[key].items():
                result["computed"][sub] = val
    for key in ("summary", "status", "case_id", "sources", "uncertainty",
                "pending_human", "errors", "degradations", "limitations", "artifacts", "answers"):
        if key in patch:
            if key in ("sources", "uncertainty", "pending_human", "errors",
                       "degradations", "limitations", "artifacts") and isinstance(patch[key], list):
                result[key] = patch[key]
            else:
                result[key] = patch[key]
    # contract fields: any top-level key not reserved and not computed
    for key, val in patch.items():
        if key in RESERVED_KEYS or key == "computed":
            continue
        result[key] = val
    if "change_log" not in patch:
        result.setdefault("change_log", []).append({
            "time": now_iso(), "actor": "scripts/core_impl.py", "step": "update",
            "detail": "agent patch merged",
        })
    else:
        result["change_log"] = patch["change_log"]
    return result, issues


# --------------------------------------------------------------------------- #
# Check
# --------------------------------------------------------------------------- #

def check_result(result, case_json):
    issues = []
    if not isinstance(result, dict):
        return ["result_not_object"]
    if not result.get("case_id") or result.get("case_id") == "unknown":
        issues.append("missing_case_id")
    if result.get("status") not in STATUSES:
        issues.append("invalid_status:%r" % result.get("status"))
    for key in ("sources", "uncertainty", "change_log", "errors", "degradations", "limitations", "pending_human"):
        if key not in result:
            issues.append("missing_key:%s" % key)
    if "computed" not in result:
        issues.append("missing_computed")
    ckey, cfields, cmeta = echo_contract(case_json)
    if cmeta.get("found"):
        for name in cfields:
            target = "_contract_%s" % name if name in RESERVED_KEYS else name
            if target not in result:
                issues.append("missing_contract_field:%s" % name)
    for s in result.get("sources", []):
        if not isinstance(s, dict) or "locator" not in s or "quote" not in s:
            issues.append("bad_source_entry")
            break
    return issues


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser():
    p = argparse.ArgumentParser(prog="main.py", description="document library repair skill")
    p.add_argument("--input", dest="input_root", default=DEFAULT_INPUT)
    p.add_argument("--output", dest="output_dir", default=DEFAULT_OUTPUT)
    p.add_argument("--work", dest="work_dir", default=DEFAULT_WORK)
    p.add_argument("--case-json", dest="case_json", default=None)
    p.add_argument("--task-path", dest="task_path", default=None)
    p.add_argument("--skeleton-only", action="store_true")
    p.add_argument("--update", dest="update_patch", default=None, help="patch.json to merge")
    p.add_argument("--result", dest="result_path", default=None)
    p.add_argument("--check", dest="check_path", default=None)
    p.add_argument("--selftest", action="store_true")
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args_dict = vars(args)
    input_root = args.input_root
    output_dir = args.output_dir
    work_dir = args.work_dir
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    os.environ["SKILL_WORK_DIR"] = work_dir
    args_dict["input_root"] = input_root
    args_dict["output_dir"] = output_dir
    args_dict["work_dir"] = work_dir

    if args.selftest:
        from selftest import main as selftest_main
        raise SystemExit(selftest_main())

    if args.check_path:
        result = _load_json(args.check_path)
        case_json = _load_json(args.case_json or os.path.join(input_root, "case.json"))
        issues = check_result(result or {}, case_json)
        print("check:", "OK" if not issues else "ISSUES")
        for i in issues:
            print("  -", i)
        raise SystemExit(0 if not issues else 1)

    if args.update_patch:
        result_path = args.result_path or os.path.join(output_dir, "result.json")
        result = _load_json(result_path)
        if result is None:
            print("update: cannot read result.json at", result_path, file=sys.stderr)
            raise SystemExit(1)
        patch = _load_json(args.update_patch)
        if patch is None:
            print("update: cannot read patch at", args.update_patch, file=sys.stderr)
            raise SystemExit(1)
        result, issues = apply_patch(result, patch)
        write_result(output_dir, result)
        log("update", "patch merged; issues=%s" % issues)
        print("update: merged into", os.path.join(output_dir, "result.json"))
        raise SystemExit(0)

    case_json = _load_json(args.case_json or os.path.join(input_root, "case.json"))
    task_text = _load_text(args.task_path or os.path.join(input_root, "task.md"))

    if args.skeleton_only:
        result, ckey, cfields = make_skeleton(case_json, task_text)
        write_result(output_dir, result)
        log("skeleton", "skeleton result written")
        print("skeleton: result.json written (status=%s, contract_key=%s)" % (result["status"], ckey))
        raise SystemExit(0)

    if not os.path.isdir(input_root):
        result, ckey, cfields = make_skeleton(case_json, task_text)
        result["status"] = "input_error"
        result["errors"] = [{"code": "input_root_missing", "detail": "input root not a directory: %s" % input_root}]
        write_result(output_dir, result)
        print("input_error: input root not found; result.json written")
        raise SystemExit(0)

    if case_json is None:
        result, ckey, cfields = make_skeleton(None, task_text)
        result["status"] = "input_error"
        result["errors"] = [{
            "code": "case_json_missing",
            "detail": "no parseable case.json at %s; cannot echo output_contract" % (
                args.case_json or os.path.join(input_root, "case.json")),
        }]
        write_result(output_dir, result)
        print("input_error: case.json missing; result.json written")
        raise SystemExit(0)

    result, ckey, cfields = run_full(args_dict)
    print("finalize: files=%d dup=%d version_candidates=%d broken_symlinks=%d -> %s" % (
        len(result["computed"].get("files", [])),
        len(result["computed"].get("duplicate_groups", [])),
        len(result["computed"].get("version_candidates", [])),
        sum(1 for s in result["computed"].get("aliases", {}).get("symlinks", []) if s.get("broken")),
        os.path.join(output_dir, "result.json"),
    ))
    raise SystemExit(0)


if __name__ == "__main__":
    main()
