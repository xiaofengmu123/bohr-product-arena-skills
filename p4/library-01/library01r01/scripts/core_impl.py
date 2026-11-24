#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Document library repair — core implementation.

Pure Python standard library, deterministic (sorted traversal, no random
sources), cross-platform (POSIX and Windows). The module:

  1. inventories /input — files, directories, permissions, symlinks and
     hard-link aliases — without moving or modifying any source file;
  2. computes SHA-256 content hashes with a configurable size limit;
  3. deduplicates by content hash (never by filename);
  4. builds heuristic version candidates from a normalised stem plus
     content similarity and modification time, and parses administrative
     version notes;
  5. parses library catalogues (JSON / CSV / BibTeX / RIS / SQLite);
  6. reconciles catalogue records against the inventory into
     matches / mismatches / missing / orphaned / metadata_only;
  7. rebuilds a searchable index with per-term locators and quotes;
  8. answers common mechanical retrieval questions;
  9. writes editable artifacts and a traceable result.json.

Every conclusion in result.json carries a locator plus a verbatim quote,
organised in a fine-grained evidence_map (see references/evidence-schema.md).
Nothing is fabricated: unreadable, too-large, non-regular or broken entries
are recorded as such rather than silently dropped.

The module is invoked through scripts/main.py or directly:

    python3 -B scripts/core_impl.py --case-json /input/case.json \
        --task-path /input/task.md --input-root /input \
        --output-dir /app/output --work-dir /app/work

Modes:
    (default)           full pipeline
    --skeleton-only     write only a legal partial result skeleton
    --update PATCH.json merge a semantic patch into an existing result.json
    --check RESULT.json validate a result.json against the case contract
"""

from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import os
import platform
import re
import sqlite3
import stat
import sys
import time

VERSION = "1.0.0"
SKILL_NAME = "library-document-repair"

DEFAULT_SIZE_LIMIT = 64 * 1024 * 1024   # 64 MiB content-hash ceiling
MAX_TEXT_READ = 1024 * 1024             # 1 MiB text read ceiling for quotes

# Contract keys that the runtime may use for the per-case output contract.
CONTRACT_KEYS = ("output_contract", "output", "result_schema", "contract")

# Reserved keys in result.json that must not be silently overwritten by a
# contract field with the same name; such collisions are stored under
# computed._contract_<field>.
RESERVED_KEYS = frozenset({
    "case_id", "status", "sources", "evidence_map", "uncertainty",
    "pending_human", "change_log", "errors", "degradations", "runtime",
    "limitations", "computed", "artifacts",
})

ALLOWED_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")

# File-name hints for administrative version notes / change descriptions.
_VERSION_NOTE_PATTERN = re.compile(
    r"(version|versions|changelog|change_log|changes|note|notes|说明|版本|"
    r"更新|修订|变更|readme|history)", re.IGNORECASE)
_VERSION_LINE_PATTERN = re.compile(
    r"(replace|replaces|supersede|supersedes|替代|取代|旧版|新版|v\d|"
    r"version\s*\d|rev\s*\d|deprecat|废弃|不再使用)", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Small utilities
# --------------------------------------------------------------------------- #

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _epoch_ms() -> int:
    return int(time.time() * 1000)


def _default_input() -> str:
    if os.environ.get("INPUT_ROOT"):
        return os.environ["INPUT_ROOT"]
    if os.name != "nt" and os.path.isdir("/input"):
        return "/input"
    return "input"


def _default_output() -> str:
    if os.environ.get("OUTPUT_DIR"):
        return os.environ["OUTPUT_DIR"]
    if os.name != "nt":
        return "/app/output"
    return "output"


def _default_work() -> str:
    if os.environ.get("WORK_DIR"):
        return os.environ["WORK_DIR"]
    if os.name != "nt":
        return "/app/work"
    return "work"


def _rel(full: str, root_abs: str) -> str:
    try:
        rel = os.path.relpath(full, root_abs)
    except ValueError:
        rel = full
    return rel.replace(os.sep, "/")


def _mode_octal(mode: int) -> str:
    return "0o%03o" % (mode & 0o777)


def _iso_mtime(st_mtime: float) -> str:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st_mtime))
    except (OverflowError, OSError, ValueError):
        return "not_reported"


def _human_size(n: int) -> str:
    if n is None:
        return "n/a"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return "%d %s" % (int(size), unit)
            return "%.1f %s" % (size, unit)
        size /= 1024
    return str(n)


def read_text_robust(path: str, max_bytes: int = MAX_TEXT_READ):
    """Read at most max_bytes bytes and decode robustly.

    Returns (text, encoding_used, degraded_bool, note). latin-1 is a lossless
    last resort so the call never raises on bad bytes.
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read(max_bytes + 1)
    except OSError as exc:
        raise
    truncated = len(data) > max_bytes
    data = data[:max_bytes]
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = data.decode(enc)
            return text, enc, False, ("truncated" if truncated else "ok")
        except (UnicodeDecodeError, UnicodeError):
            continue
    text = data.decode("latin-1")
    return text, "latin-1", True, ("decode_fallback; truncated" if truncated
                                   else "decode_fallback")


def first_text_line(path: str, max_bytes: int = 4096):
    """Return the first non-empty line of a text file (verbatim, ≤400 chars)."""
    text, _enc, degraded, note = read_text_robust(path, max_bytes)
    for line in text.splitlines():
        if line.strip():
            return line[:400], degraded, note
    return "", degraded, note


def sha256_file(path: str, size_limit: int):
    """Compute SHA-256 of a regular file; returns (hex, status, note)."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, "unreadable", "getsize: %s" % exc
    if size > size_limit:
        return None, "too_large", "size %d > limit %d" % (size, size_limit)
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
    except OSError as exc:
        return None, "unreadable", "read: %s" % exc
    return digest.hexdigest(), "ok", ""


def binary_hex_preview(path: str, n: int = 32) -> str:
    """Hex preview of the first n bytes for binary files (not verbatim text)."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(n)
    except OSError as exc:
        return "<unreadable: %s>" % exc
    return "<hex-preview> " + " ".join("%02x" % b for b in data)


def _looks_binary(text: str) -> bool:
    return "\x00" in text


# --------------------------------------------------------------------------- #
# Inventory
# --------------------------------------------------------------------------- #

def inventory_entry_file(full: str, rel: str, size_limit: int) -> dict:
    entry = {
        "path": rel,
        "kind": "file",
        "link_target": None,
        "broken": False,
        "size": None,
        "mode": None,
        "mtime": None,
        "sha256": None,
        "hash_status": None,
        "note": "",
        "inode": None,
        "nlink": 1,
        "is_hardlink_group": False,
    }
    try:
        st = os.lstat(full)
    except OSError as exc:
        entry["kind"] = "unreadable"
        entry["note"] = "lstat: %s" % exc
        entry["hash_status"] = "unreadable"
        return entry

    entry["mode"] = _mode_octal(st.st_mode)
    entry["mtime"] = _iso_mtime(st.st_mtime)
    entry["nlink"] = int(getattr(st, "st_nlink", 1) or 1)
    try:
        dev = int(getattr(st, "st_dev", 0) or 0)
        ino = int(getattr(st, "st_ino", 0) or 0)
        if ino:
            entry["inode"] = [dev, ino]
    except (AttributeError, ValueError):
        pass

    if stat.S_ISLNK(st.st_mode) or os.path.islink(full):
        entry["kind"] = "symlink"
        try:
            target = os.readlink(full)
            entry["link_target"] = target
        except OSError as exc:
            entry["link_target"] = None
            entry["note"] = "readlink: %s" % exc
        entry["broken"] = not os.path.exists(full)
        if entry["broken"]:
            entry["note"] = (entry["note"] + " broken_symlink").strip()
        entry["hash_status"] = "skipped_symlink"
        return entry

    if not stat.S_ISREG(st.st_mode):
        entry["kind"] = "non_regular"
        entry["note"] = "not a regular file (mode %s)" % _mode_octal(st.st_mode)
        entry["hash_status"] = "skipped_non_regular"
        return entry

    entry["size"] = int(st.st_size)
    digest, status, note = sha256_file(full, size_limit)
    entry["sha256"] = digest
    entry["hash_status"] = status
    entry["note"] = note
    return entry


def inventory_entry_dir(full: str, rel: str) -> dict:
    entry = {
        "path": rel,
        "kind": "dir",
        "link_target": None,
        "broken": False,
        "size": None,
        "mode": None,
        "mtime": None,
        "sha256": None,
        "hash_status": None,
        "note": "",
        "inode": None,
        "nlink": 1,
        "is_hardlink_group": False,
        "empty": False,
    }
    try:
        st = os.lstat(full)
        entry["mode"] = _mode_octal(st.st_mode)
        entry["mtime"] = _iso_mtime(st.st_mtime)
        entry["nlink"] = int(getattr(st, "st_nlink", 1) or 1)
        if os.path.islink(full):
            entry["kind"] = "symlink_dir"
            try:
                entry["link_target"] = os.readlink(full)
            except OSError as exc:
                entry["note"] = "readlink: %s" % exc
            entry["broken"] = not os.path.exists(full)
    except OSError as exc:
        entry["kind"] = "unreadable_dir"
        entry["note"] = "lstat: %s" % exc
    return entry


CONTROL_NAMES = frozenset({"case.json", "task.md"})


def walk_input(root: str, size_limit: int):
    """Walk the input root deterministically (sorted, no symlink following).

    Files named exactly case.json / task.md are control files (the runtime
    contract), not library materials: they are returned separately and are
    excluded from dedup / version / catalogue / reconciliation logic.
    Returns (materials, dirs, control_files, errors).
    """
    files, dirs, control, errors = [], [], [], []
    root_abs = os.path.abspath(root)
    if not os.path.isdir(root):
        errors.append({
            "locator": root,
            "quote": "%s is not a directory or is not readable" % root,
            "kind": "input",
            "note": "input_root_missing",
        })
        return files, dirs, control, errors

    for dirpath, dirnames, filenames in os.walk(root, topdown=True,
                                                followlinks=False):
        dirnames.sort()
        filenames.sort()
        for d in dirnames:
            full = os.path.join(dirpath, d)
            dirs.append(inventory_entry_dir(full, _rel(full, root_abs)))
        for f in filenames:
            full = os.path.join(dirpath, f)
            entry = inventory_entry_file(full, _rel(full, root_abs),
                                         size_limit)
            if f in CONTROL_NAMES:
                entry["kind"] = "control"
                entry["hash_status"] = None
                control.append(entry)
            else:
                files.append(entry)

    # Mark empty directories (no regular file and no sub-directory beneath).
    file_paths = {f["path"] for f in files}
    for d in dirs:
        if d["kind"] not in ("dir", "symlink_dir"):
            continue
        p = d["path"]
        has_child = any(
            fp.startswith(p + "/") or (fp != p and fp.startswith(p + "/"))
            for fp in file_paths)
        has_subdir = any(
            (o["path"] != p and o["path"].startswith(p + "/"))
            for o in dirs)
        d["empty"] = (not has_child and not has_subdir)
    return files, dirs, control, errors


# --------------------------------------------------------------------------- #
# Catalogue parsing (JSON / CSV / BibTeX / RIS / SQLite)
# --------------------------------------------------------------------------- #

def _is_record(obj) -> bool:
    return isinstance(obj, dict) and len(obj) > 0


def _extract_fields(rec: dict) -> dict:
    """Normalise common bibliographic / manifest fields into one vocabulary."""
    out = {}
    flat = {}
    for k, v in rec.items():
        if isinstance(v, (str, int, float)):
            flat[str(k).lower()] = v
        if isinstance(v, list):
            flat[str(k).lower()] = v
        elif isinstance(v, dict):
            for kk, vv in v.items():
                flat["%s.%s" % (str(k).lower(), str(kk).lower())] = vv

    def get(*names):
        for n in names:
            for k, v in flat.items():
                if k == n or k.endswith("." + n):
                    return v
        return None

    out["title"] = get("title", "ti")
    out["author"] = get("author", "au", "authors", "creators")
    out["year"] = get("year", "py", "date", "publication_year")
    out["file"] = get("file", "path", "filename", "attachment", "relative_path")
    out["hash"] = get("sha256", "sha-256", "hash", "checksum", "md5")
    out["doi"] = get("doi")
    out["tags"] = get("tags", "keywords", "keyword")
    return {k: v for k, v in out.items() if v is not None}


def _record_json(rec: dict, raw: str) -> dict:
    return {
        "fields": _extract_fields(rec),
        "raw": rec,
        "quote": raw,
    }


def parse_json_catalog(path: str):
    """Return (records, error). Treats top-level list or dict-of-list as rows."""
    try:
        text, _enc, degraded, note = read_text_robust(path, MAX_TEXT_READ)
        if degraded and _looks_binary(text):
            return [], "json_binary_or_bad_encoding"
        try:
            data = json.loads(text)
        except ValueError as exc:
            return [], "json_parse_error: %s" % exc
        rows = None
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, list):
                    rows = value
                    break
        if rows is None:
            return [], "json_not_a_catalog"
        records = []
        for item in rows:
            if _is_record(item):
                raw = json.dumps(item, ensure_ascii=False)
                records.append(_record_json(item, raw))
        return records, None
    except OSError as exc:
        return [], "json_open_error: %s" % exc


def parse_csv_catalog(path: str):
    """Return (records, error)."""
    try:
        text, _enc, degraded, note = read_text_robust(path, MAX_TEXT_READ)
        if degraded and _looks_binary(text):
            return [], "csv_binary_or_bad_encoding"
        reader = csv.DictReader(text.splitlines())
        if reader.fieldnames is None:
            return [], "csv_no_header"
        records = []
        for row in reader:
            clean = {k: v for k, v in row.items() if k is not None}
            if not clean:
                continue
            records.append(_record_json(clean, json.dumps(clean,
                                                          ensure_ascii=False)))
        return records, None
    except (OSError, csv.Error) as exc:
        return [], "csv_error: %s" % exc


_BIB_FIELD = re.compile(r'(\w+)\s*=\s*[{"](.*?)[}"],?', re.DOTALL)


def parse_bibtex(path: str):
    """Lightweight BibTeX parser: @type{key, field={...}, ...} entries."""
    try:
        text, _enc, degraded, note = read_text_robust(path, MAX_TEXT_READ)
        if degraded and _looks_binary(text):
            return [], "bib_binary_or_bad_encoding"
        records = []
        entry_re = re.compile(r'@(\w+)\s*\{', re.IGNORECASE)
        for m in entry_re.finditer(text):
            start = m.end()
            # find matching close brace roughly
            depth = 1
            i = start
            while i < len(text) and depth > 0:
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                i += 1
            body = text[start:i - 1]
            fields = {}
            for fm in _BIB_FIELD.finditer(body):
                fields[fm.group(1).lower()] = fm.group(2)
            raw = "@%s{%s}" % (m.group(1), body[:200])
            records.append(_record_json(fields, raw))
        return records, None
    except OSError as exc:
        return [], "bib_open_error: %s" % exc


def parse_ris(path: str):
    """RIS parser: two-letter tag + space + hyphen + value; 'ER' ends record."""
    try:
        text, _enc, degraded, note = read_text_robust(path, MAX_TEXT_READ)
        if degraded and _looks_binary(text):
            return [], "ris_binary_or_bad_encoding"
        records = []
        cur = {}
        for raw_line in text.splitlines():
            if len(raw_line) < 6 or raw_line[2:4] != "  ":
                continue
            tag = raw_line[:2].upper()
            value = raw_line[6:].strip()
            if tag == "ER":
                if cur:
                    records.append(_record_json(cur,
                                                json.dumps(cur,
                                                           ensure_ascii=False)))
                cur = {}
                continue
            if tag == "TY":
                cur["type"] = value
            elif tag == "TI":
                cur["title"] = (cur.get("title") or "") + value
            elif tag == "AU":
                cur.setdefault("authors", []).append(value)
            elif tag == "PY":
                cur["year"] = value
            elif tag == "DO":
                cur["doi"] = value
            elif tag == "L1":
                cur["file"] = value
            else:
                cur[tag.lower()] = value
        if cur:
            records.append(_record_json(cur, json.dumps(cur,
                                                        ensure_ascii=False)))
        return records, None
    except OSError as exc:
        return [], "ris_open_error: %s" % exc


def parse_sqlite(path: str):
    """Read a SQLite database read-only, returning one record per row."""
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"),
                              uri=True, timeout=5)
    except sqlite3.Error as exc:
        return [], "sqlite_open_error: %s" % exc
    try:
        cur = con.cursor()
        tables = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
        records = []
        for t in tables:
            try:
                rows = cur.execute(
                    'SELECT * FROM "%s" LIMIT 2000' % t).fetchall()
                cols = [d[0] for d in cur.description]
                for row in rows:
                    rec = dict(zip(cols, row))
                    records.append(_record_json(
                        rec, json.dumps(rec, ensure_ascii=False,
                                        default=str)))
            except sqlite3.Error as exc:
                records.append({
                    "fields": {"table": t},
                    "raw": {},
                    "quote": "sqlite_query_error on table %s: %s" % (t, exc),
                    "error": "sqlite_query_error",
                })
        return records, None
    except sqlite3.Error as exc:
        return [], "sqlite_query_error: %s" % exc
    finally:
        try:
            con.close()
        except Exception:
            pass


_CATALOG_HANDLERS = {
    ".json": parse_json_catalog,
    ".csv": parse_csv_catalog,
    ".bib": parse_bibtex,
    ".bibtex": parse_bibtex,
    ".ris": parse_ris,
    ".db": parse_sqlite,
    ".sqlite": parse_sqlite,
    ".sqlite3": parse_sqlite,
}


def parse_catalogs(files, base_dir):
    """Parse every catalogue-shaped file; returns records, catalog_errors."""
    records = []
    errors = []
    for f in files:
        if f["kind"] != "file":
            continue
        ext = os.path.splitext(f["path"])[1].lower()
        handler = _CATALOG_HANDLERS.get(ext)
        if handler is None:
            continue
        recs, err = handler(os.path.join(base_dir, f["path"]))
        f["catalog_kind"] = ext.lstrip(".")
        if err:
            errors.append({
                "locator": f["path"],
                "quote": err,
                "kind": "catalog",
                "note": err,
            })
            f["catalog_error"] = err
            continue
        for r in recs:
            r["locator"] = f["path"]
        records.extend(recs)
        f["catalog_records"] = len(recs)
    return records, errors


# --------------------------------------------------------------------------- #
# Deduplication, version candidates, aliases, version notes
# --------------------------------------------------------------------------- #

def dedup_groups(files):
    groups = {}
    for f in files:
        if f.get("sha256"):
            groups.setdefault(f["sha256"], []).append(f["path"])
    out = []
    for digest in sorted(groups):
        members = sorted(groups[digest])
        if len(members) > 1:
            out.append({
                "sha256": digest,
                "count": len(members),
                "members": members,
                "sizes": sorted({f["size"] for f in files
                                 if f["path"] in members}),
            })
    return out


_VERSION_TOKEN = re.compile(
    r"(^|[_\-\.\s])(v|ver|version|rev|r)?\d+(\.\d+)*"
    r"|(^|[_\-\.\s])(final|draft|copy|copia|backup|bak|old|new|orig|original)"
    r"|\(\d+\)|\[\d+\]",
    re.IGNORECASE)


def normalize_stem(name: str) -> str:
    base = os.path.splitext(name)[0]
    stem = _VERSION_TOKEN.sub(" ", base)
    stem = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", stem.lower())
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem


def _text_ratio(path_a, path_b):
    """Similarity ratio of two text files (1.0 identical, 0.0 unrelated)."""
    try:
        a, _e1, d1, _n1 = read_text_robust(path_a, 64 * 1024)
        b, _e2, d2, _n2 = read_text_robust(path_b, 64 * 1024)
        if _looks_binary(a) or _looks_binary(b):
            return None
        return round(difflib.SequenceMatcher(None, a, b).ratio(), 4)
    except OSError:
        return None


def version_candidates(files, base_dir):
    stem_map = {}
    for f in files:
        if f["kind"] != "file":
            continue
        stem = normalize_stem(os.path.basename(f["path"]))
        if not stem:
            stem = "<empty_stem>"
        stem_map.setdefault(stem, []).append(f)

    candidates = []
    for stem in sorted(stem_map):
        group = stem_map[stem]
        if len(group) < 2:
            continue
        group.sort(key=lambda f: (f.get("mtime") or "", f["path"]))
        chain = []
        for i, f in enumerate(group):
            node = {
                "path": f["path"],
                "mtime": f.get("mtime"),
                "size": f.get("size"),
                "sha256": f.get("sha256"),
            }
            if i > 0:
                node["supersedes"] = group[i - 1]["path"]
                prev_abs = os.path.join(base_dir, group[i - 1]["path"])
                cur_abs = os.path.join(base_dir, f["path"])
                node["similarity_to_prev"] = _text_ratio(prev_abs, cur_abs)
                if node["similarity_to_prev"] is None:
                    node["similarity_note"] = "binary_or_unreadable"
            chain.append(node)
        candidates.append({
            "stem": stem,
            "count": len(group),
            "chain": chain,
            "heuristic": True,
            "basis": "normalized_stem + mtime + content_similarity",
        })
    return candidates


def alias_groups(files):
    """Symlinks (including broken) and hard-link groups as aliases."""
    symlinks = []
    hardlinks = {}
    for f in files:
        if f.get("kind") == "symlink":
            symlinks.append({
                "path": f["path"],
                "link_target": f.get("link_target"),
                "broken": bool(f.get("broken")),
            })
        if f.get("inode"):
            key = tuple(f["inode"])
            if f.get("nlink", 1) > 1:
                hardlinks.setdefault(key, []).append(f["path"])
    hard_groups = []
    for key in sorted(hardlinks, key=str):
        members = sorted(hardlinks[key])
        if len(members) > 1:
            hard_groups.append({"inode": list(key), "members": members})
    return {"symlinks": symlinks, "hardlink_groups": hard_groups}


def parse_version_notes(files, size_limit, base_dir):
    notes = []
    for f in files:
        if f["kind"] != "file":
            continue
        name = os.path.basename(f["path"])
        if not _VERSION_NOTE_PATTERN.search(name):
            continue
        abs_path = os.path.join(base_dir, f["path"])
        if f.get("hash_status") == "too_large":
            notes.append({
                "locator": f["path"],
                "quote": "<too large to parse for version notes>",
                "matched_files": [],
                "note": "too_large",
            })
            continue
        try:
            text, _enc, degraded, note = read_text_robust(abs_path,
                                                          MAX_TEXT_READ)
        except OSError as exc:
            notes.append({
                "locator": f["path"],
                "quote": "<unreadable: %s>" % exc,
                "matched_files": [],
                "note": "unreadable",
            })
            continue
        if _looks_binary(text):
            notes.append({
                "locator": f["path"],
                "quote": binary_hex_preview(abs_path),
                "matched_files": [],
                "note": "binary",
            })
            continue
        for line in text.splitlines():
            if not _VERSION_LINE_PATTERN.search(line):
                continue
            matched = []
            for g in files:
                base = os.path.basename(g["path"])
                if base and base in line:
                    matched.append(g["path"])
            notes.append({
                "locator": f["path"],
                "quote": line[:400],
                "matched_files": sorted(set(matched)),
                "note": "parsed_version_note",
            })
    return notes


# --------------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------------- #

def reconcile(inventory_files, records):
    file_by_path = {f["path"]: f for f in inventory_files}
    matches, mismatches, missing, metadata_only, orphaned = [], [], [], [], []

    for idx, rec in enumerate(records):
        fp = rec.get("fields", {}).get("file")
        rh = rec.get("fields", {}).get("hash")
        rec_id = "catalog.%d" % idx
        if not fp:
            metadata_only.append({
                "catalog_record": rec_id,
                "locator": rec.get("locator"),
                "quote": rec.get("quote"),
                "reason": "no file path in record",
            })
            continue
        # normalise path to posix relative
        norm = str(fp).replace("\\", "/").lstrip("./")
        target = file_by_path.get(norm)
        if target is None:
            missing.append({
                "catalog_record": rec_id,
                "path": norm,
                "locator": rec.get("locator"),
                "quote": rec.get("quote"),
            })
            continue
        ok_hash = True
        if rh and target.get("sha256"):
            ok_hash = (str(rh).lower().replace("-", "")
                       == target["sha256"])
        if ok_hash:
            matches.append({
                "catalog_record": rec_id,
                "path": norm,
                "locator": rec.get("locator"),
                "quote": rec.get("quote"),
            })
        else:
            mismatches.append({
                "catalog_record": rec_id,
                "path": norm,
                "expected_hash": str(rh) if rh else None,
                "actual_sha256": target.get("sha256"),
                "locator": rec.get("locator"),
                "quote": rec.get("quote"),
            })

    referenced = set()
    for rec in records:
        fp = rec.get("fields", {}).get("file")
        if fp:
            referenced.add(str(fp).replace("\\", "/").lstrip("./"))
    for f in inventory_files:
        if f.get("kind") == "file" and f["path"] not in referenced:
            orphaned.append({"path": f["path"], "reason": "not referenced"})

    return {
        "matches": matches,
        "mismatches": mismatches,
        "missing": missing,
        "metadata_only": metadata_only,
        "orphaned": orphaned,
        "counts": {
            "matches": len(matches),
            "mismatches": len(mismatches),
            "missing": len(missing),
            "metadata_only": len(metadata_only),
            "orphaned": len(orphaned),
        },
    }


# --------------------------------------------------------------------------- #
# Searchable index
# --------------------------------------------------------------------------- #

_TOKEN = re.compile(r"[0-9a-zA-Z\u4e00-\u9fff]{1,}")


def tokenize(text: str):
    return _TOKEN.findall(text.lower())


def build_index(files, records):
    inverted = {}
    for f in files:
        if f["kind"] != "file":
            continue
        tokens = tokenize(os.path.basename(f["path"]))
        for t in tokens:
            inverted.setdefault(t, []).append({
                "path": f["path"],
                "field": "filename",
                "quote": os.path.basename(f["path"]),
            })
    for idx, rec in enumerate(records):
        fields = rec.get("fields", {})
        for field in ("title", "author", "year", "doi", "tags"):
            value = fields.get(field)
            if value is None:
                continue
            if isinstance(value, list):
                value = " ".join(str(v) for v in value)
            text = str(value)
            for t in tokenize(text):
                inverted.setdefault(t, []).append({
                    "path": rec.get("locator"),
                    "field": field,
                    "quote": text[:200],
                })
    index = {
        "terms": {
            t: {
                "count": len(inverted[t]),
                "locations": sorted(inverted[t],
                                    key=lambda x: (x.get("path") or "",
                                                   x.get("field") or "",
                                                   x.get("quote") or "")),
            }
            for t in sorted(inverted)
        },
        "term_count": len(inverted),
    }
    return index


def answer_questions(files, dirs, dedup, versions, aliases, recon, index):
    """Mechanical retrieval answers, each with evidence locators + quotes."""
    answers = []
    n_files = len([f for f in files if f["kind"] == "file"])
    n_dirs = len([d for d in dirs if d["kind"] in ("dir", "symlink_dir")])
    total_size = sum(f.get("size") or 0 for f in files
                     if f.get("kind") == "file")
    broken = [s for s in aliases["symlinks"] if s["broken"]]
    n_broken = len(broken)

    def add(q, answer, evidence, confidence="mechanical"):
        answers.append({
            "question": q,
            "answer": answer,
            "confidence": confidence,
            "evidence": evidence,
        })

    add("文件总数", "%d 个文件" % n_files,
        [{"locator": "computed.inventory", "quote": "counted from sorted "
          "os.walk of input root"}])
    add("目录总数", "%d 个目录" % n_dirs,
        [{"locator": "computed.inventory",
          "quote": "counted directories in sorted walk"}])
    add("总大小", _human_size(total_size),
        [{"locator": "computed.inventory",
          "quote": "sum of regular-file sizes"}])
    add("重复文件组数", "%d 组内容重复" % len(dedup),
        [{"locator": d["members"][0], "quote": "sha256=%s" % d["sha256"][:16]}
         for d in dedup[:10]])
    add("断链符号链接数", "%d 个" % n_broken,
        [{"locator": s["path"], "quote": "link -> %s" % s["link_target"]}
         for s in broken[:10]])
    add("版本候选链数", "%d 条" % len(versions),
        [{"locator": v["chain"][0]["path"],
          "quote": "stem=%s" % v["stem"]} for v in versions[:10]])
    add("目录缺失文件数", "%d 个" % recon["counts"]["missing"],
        [{"locator": m.get("locator") or m["path"],
          "quote": (m.get("quote") or "")[:200]} for m in recon["missing"][:10]])
    add("孤儿文件数", "%d 个" % recon["counts"]["orphaned"],
        [{"locator": o["path"], "quote": "not referenced by any catalog"}
         for o in recon["orphaned"][:10]])
    add("索引词条数", "%d 个" % index["term_count"],
        [{"locator": "computed.index", "quote": "inverted index term count"}])
    return answers


# --------------------------------------------------------------------------- #
# Directory tree rendering
# --------------------------------------------------------------------------- #

def build_tree_lines(files, dirs, root_label="input"):
    root = {}
    for d in dirs:
        if d["kind"] not in ("dir", "symlink_dir"):
            continue
        if d["path"] == ".":
            continue
        node = root
        for part in d["path"].split("/"):
            node = node.setdefault(part, {})
        node.setdefault("<dir>", d)
    for f in files:
        parts = f["path"].split("/")
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        leaf = parts[-1]
        if leaf in node and "<dir>" in node[leaf]:
            node[leaf]["<file>"] = f
        else:
            node[leaf] = {"<file>": f}

    lines = [root_label + "/"]

    def rec(node, prefix, is_last, name):
        fentry = node.get("<file>")
        dmark = node.get("<dir>")
        child_keys = sorted(k for k in node
                            if k not in ("<file>", "<dir>"))
        if fentry is not None:
            label = name
            if fentry.get("kind") == "symlink":
                label += " -> %s" % (fentry.get("link_target") or "?")
                if fentry.get("broken"):
                    label += " [断链]"
            if fentry.get("hash_status") in ("too_large", "unreadable"):
                label += " [%s]" % fentry["hash_status"]
        elif dmark is not None and not child_keys:
            label = name + " [空目录]"
        else:
            label = name + "/"
        lines.append(prefix + ("└── " if is_last else "├── ") + label)
        for i, k in enumerate(child_keys):
            rec(node[k], prefix + ("    " if is_last else "│   "),
                (i == len(child_keys) - 1), k)

    keys = sorted(k for k in root if k not in ("<file>", "<dir>"))
    for i, k in enumerate(keys):
        rec(root[k], "", (i == len(keys) - 1), k)
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Result assembly
# --------------------------------------------------------------------------- #

def load_case_meta(case_json_path):
    """Return (case_id, contract_dict, errors)."""
    errors = []
    if not case_json_path:
        case_json_path = os.path.join(os.environ.get("INPUT_ROOT",
                                                     _default_input()),
                                      "case.json")
    if not os.path.isfile(case_json_path):
        return None, None, [{
            "locator": case_json_path,
            "quote": "case.json missing",
            "kind": "input",
            "note": "missing_case_json",
        }]
    try:
        with open(case_json_path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, None, [{
            "locator": case_json_path,
            "quote": "case.json parse error: %s" % exc,
            "kind": "input",
            "note": "bad_case_json",
        }]
    if not isinstance(data, dict):
        return None, None, [{
            "locator": case_json_path,
            "quote": "case.json is not a JSON object",
            "kind": "input",
            "note": "bad_case_json",
        }]
    case_id = None
    for key in ("case_id", "caseId", "id", "challenge_id", "challengeId"):
        v = data.get(key)
        if v:
            case_id = str(v)
            break
    if case_id is None:
        for key in ("armiId",):
            if data.get(key):
                case_id = str(data[key])
                break
    contract = None
    for key in CONTRACT_KEYS:
        if key in data and isinstance(data[key], dict):
            contract = data[key]
            break
    return case_id, contract, errors


def load_task_lines(task_path):
    try:
        with open(task_path, "r", encoding="utf-8-sig") as fh:
            return fh.read().splitlines()
    except OSError:
        return []


def _contract_default(value):
    if isinstance(value, dict):
        if str(value.get("type", "")).lower() == "array":
            return []
        if value.get("default") is not None:
            return value["default"]
    return None


def build_skeleton(case_id, contract):
    result = {
        "case_id": case_id or "",
        "status": "partial",
    }
    collision = {}
    if contract:
        for k, v in contract.items():
            if k in RESERVED_KEYS:
                collision[k] = v
            else:
                result[k] = _contract_default(v)
    result.update({
        "sources": [],
        "evidence_map": {
            "schema_version": "1.0",
            "conclusions": {},
        },
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
        "runtime": {},
        "limitations": [],
        "computed": {},
        "artifacts": [],
    })
    if collision:
        result["computed"]["_contract_collisions"] = collision
    return result


def _mk_conclusion(evidence_map, cid, claim, strength, kind, evidence,
                   uncertainty=None, method=None):
    entry = {
        "id": cid,
        "claim": claim,
        "strength": strength,
        "kind": kind,
        "evidence": evidence,
    }
    if method:
        entry["method"] = method
    if uncertainty:
        entry["uncertainty"] = uncertainty
    evidence_map.setdefault("conclusions", {})
    evidence_map["conclusions"][cid] = entry


def _evidence(locator, quote, role="basis"):
    return {"locator": locator, "quote": quote, "role": role}


def assemble_full(case_id, contract, task_lines, files, dirs, control,
                  errors, dedup, versions, aliases, version_notes,
                  catalog_records, catalog_errors, recon, index, answers,
                  runtime, ops, case_meta_errors):
    """Assemble the complete result.json payload."""
    result = build_skeleton(case_id, contract)
    em = result["evidence_map"]

    # Inventory conclusions
    n_files = len([f for f in files if f["kind"] == "file"])
    n_dirs = len([d for d in dirs if d["kind"] in ("dir", "symlink_dir")])
    total_size = sum(f.get("size") or 0 for f in files
                     if f.get("kind") == "file")
    skipped = [f for f in files if f.get("hash_status") not in
               ("ok", None, "skipped_symlink")]
    _mk_conclusion(em, "inventory.summary", "%d 文件 / %d 目录 / 总大小 %s"
                   % (n_files, n_dirs, _human_size(total_size)),
                   "established", "inventory",
                   [_evidence("computed.inventory",
                              "sorted os.walk; no symlink following")])
    if control:
        _mk_conclusion(em, "inventory.control", "%d 个运行契约控制文件"
                       % len(control), "established", "inventory",
                       [_evidence(c["path"], "runtime contract file")
                        for c in control])
    for f in files:
        if f.get("hash_status") in ("too_large", "unreadable",
                                    "skipped_non_regular"):
            _mk_conclusion(
                em, "inventory.skip.%s" % f["path"], "%s 未取内容哈希"
                % f["path"], "established", "inventory",
                [_evidence(f["path"], f.get("note") or f["hash_status"],
                           "basis")],
                uncertainty="记录为 %s，未静默丢弃" % f["hash_status"])

    # Dedup conclusions
    for i, g in enumerate(dedup):
        quote = "sha256=%s, %d members" % (g["sha256"][:16], g["count"])
        _mk_conclusion(em, "dedup.%d" % i,
                       "按内容 SHA-256 判定 %d 个文件重复" % g["count"],
                       "established", "dedup",
                       [_evidence(m, quote) for m in g["members"]],
                       method="content_sha256")

    # Version conclusions (heuristic)
    for i, v in enumerate(versions):
        chain_paths = [n["path"] for n in v["chain"]]
        _mk_conclusion(em, "version.%d" % i,
                       "版本候选链（stem=%s，%d 个文件）" % (v["stem"],
                                                             v["count"]),
                       "preliminary", "version",
                       [_evidence(p, "member of stem group %s" % v["stem"])
                        for p in chain_paths],
                       uncertainty="按归一化主干+内容相似度+mtime 的启发式判定",
                       method=v["basis"])

    # Alias conclusions
    for s in aliases["symlinks"]:
        _mk_conclusion(em, "alias.symlink.%s" % s["path"],
                       "符号链接 %s -> %s" % (s["path"], s["link_target"]),
                       "established", "alias",
                       [_evidence(s["path"], "readlink target %s"
                                  % s["link_target"])])
    for i, hg in enumerate(aliases["hardlink_groups"]):
        _mk_conclusion(em, "alias.hardlink.%d" % i,
                       "硬链接组 inode=%s" % hg["inode"],
                       "established", "alias",
                       [_evidence(m, "same inode (%s)" % hg["inode"])
                        for m in hg["members"]])

    # Version notes conclusions
    for i, n in enumerate(version_notes):
        _mk_conclusion(em, "version_note.%d" % i,
                       "行政版本说明：%s" % (n["quote"][:80]),
                       "established", "version_note",
                       [_evidence(n["locator"], n["quote"])],
                       uncertainty=("引用原文；若 matched_files 为空则未与"
                                    "库内文件链接"))

    # Reconciliation conclusions
    for kind in ("matches", "mismatches", "missing", "orphaned"):
        items = recon.get(kind, [])
        for j, item in enumerate(items[:50]):
            loc = item.get("locator") or item.get("path") or "catalog"
            quote = item.get("quote") or item.get("reason") or ""
            _mk_conclusion(em, "reconcile.%s.%d" % (kind, j),
                           "对账 %s：%s" % (kind, item.get("path")
                                            or item.get("catalog_record")),
                           "established", "reconcile",
                           [_evidence(loc, quote[:400])])

    # Index conclusion
    _mk_conclusion(em, "index.summary", "重建可检索索引，%d 个词条"
                   % index["term_count"], "established", "index",
                   [_evidence("computed.index", "inverted index term count")])

    # Answer conclusions
    for i, a in enumerate(answers):
        _mk_conclusion(em, "answer.%d" % i, "Q: %s → %s"
                       % (a["question"], a["answer"]),
                       "established" if a["confidence"] == "mechanical"
                       else "preliminary", "answer",
                       a["evidence"])

    # Flat sources list (item = conclusion id)
    sources = []
    for cid in sorted(em["conclusions"]):
        for ev in em["conclusions"][cid]["evidence"]:
            sources.append({
                "item": cid,
                "locator": ev["locator"],
                "quote": ev["quote"],
                "role": ev.get("role", "basis"),
            })

    result["sources"] = sources
    result["evidence_map"] = em
    result["computed"] = {
        "summary": {
            "files": n_files,
            "directories": n_dirs,
            "control_files": len(control),
            "total_size_bytes": total_size,
            "hashed_files": len([f for f in files if f.get("sha256")]),
            "skipped_files": len(skipped),
        },
        "inventory": files + dirs,
        "control_files": control,
        "duplicate_groups": dedup,
        "version_candidates": versions,
        "version_notes": version_notes,
        "aliases": aliases,
        "catalog_records": catalog_records,
        "reconciliation": recon,
        "answers": answers,
        "index": index,
        "_contract_collisions": result["computed"].get("_contract_collisions",
                                                       {}),
    }
    result["errors"] = case_meta_errors + errors + catalog_errors
    result["degradations"] = [
        f for f in files if f.get("hash_status") not in (None, "ok")
    ]
    result["uncertainty"] = [
        {"item": "version_candidates", "reason": "版本链为启发式，按主干+mtime"
         "排序，非权威替代关系", "severity": "medium"},
        {"item": "metadata_extraction", "reason": "目录/元数据字段从文件名与"
         "目录清单抽取，字段出处以 locator+quote 保留", "severity": "low"},
    ]
    result["pending_human"] = [
        {"item": "version_chain_authority", "reason": "若题目提供权威版本说明，"
         "需人工确认替代方向"},
        {"item": "orphaned_files", "reason": "未被目录引用的文件需人工判断是否"
         "保留"},
    ]
    result["runtime"] = runtime
    result["change_log"] = ops
    result["limitations"] = [
        "内容级语义理解由 Agent 在脚本候选上补充，脚本只做机械层",
        "超过 %d 字节的文件跳过内容哈希，仅保留大小/权限/mtime" % DEFAULT_SIZE_LIMIT,
        "二进制文件只提供 hex 预览，不提供逐字文本 quote",
    ]
    result["artifacts"] = [
        "library_manifest.csv", "directory_tree.txt", "dedup_version.json",
        "recovery_report.json", "library_index.json", "library_manifest.md",
        "operation_log.jsonl",
    ]

    # Determine status honestly. case.json / task.md are control files and do
    # not count as library material.
    if case_meta_errors and n_files == 0:
        result["status"] = "input_error"
    elif n_files == 0:
        result["status"] = "insufficient_evidence"
    elif result["errors"] or skipped:
        result["status"] = "partial"
    else:
        result["status"] = "ok"
    return result


# --------------------------------------------------------------------------- #
# Artifact writers
# --------------------------------------------------------------------------- #

def _dump_json(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)


def write_artifacts(output_dir, files, dirs, control, dedup, versions,
                    aliases, version_notes, recon, index, answers, result,
                    catalog_errors, task_lines):
    os.makedirs(output_dir, exist_ok=True)

    # library_manifest.csv
    manifest_path = os.path.join(output_dir, "library_manifest.csv")
    fieldnames = ["path", "kind", "size", "mode", "mtime", "sha256",
                  "hash_status", "link_target", "broken", "note"]
    with open(manifest_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames,
                                extrasaction="ignore")
        writer.writeheader()
        for f in control + files + dirs:
            writer.writerow(f)

    # directory_tree.txt
    tree_path = os.path.join(output_dir, "directory_tree.txt")
    tree_text = build_tree_lines(files, dirs)
    with open(tree_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(tree_text)

    # dedup_version.json
    dedup_path = os.path.join(output_dir, "dedup_version.json")
    _dump_json(dedup_path, {
        "duplicate_groups": dedup,
        "version_candidates": versions,
        "version_notes": version_notes,
        "aliases": aliases,
        "heuristic": True,
        "note": "version chains are heuristic; see evidence_map for quotes",
    })

    # recovery_report.json
    recovery_path = os.path.join(output_dir, "recovery_report.json")
    _dump_json(recovery_path, {
        "reconciliation": recon,
        "catalog_errors": catalog_errors,
        "skipped_files": [f for f in files
                          if f.get("hash_status") not in (None, "ok")],
    })

    # library_index.json
    index_path = os.path.join(output_dir, "library_index.json")
    _dump_json(index_path, index)

    # library_manifest.md (handoff report)
    md_path = os.path.join(output_dir, "library_manifest.md")
    md_lines = [
        "# 科研资料库修复交接报告",
        "",
        "- case_id: `%s`" % result.get("case_id"),
        "- status: `%s`" % result.get("status"),
        "- 文件数: %d；目录数: %d" % (result["computed"]["summary"]["files"],
                                      result["computed"]["summary"][
                                          "directories"]),
        "- 内容去重组: %d；版本候选链: %d；断链符号链接: %d"
        % (len(dedup), len(versions),
           len([s for s in aliases["symlinks"] if s["broken"]])),
        "- 对账: %s" % json.dumps(recon["counts"], ensure_ascii=False),
        "",
        "## 目录树",
        "```text",
        tree_text.rstrip(),
        "```",
        "",
        "## 证据与溯源",
        "每条结论的 locator + 逐字 quote 见 `result.json` 的 `sources` 与",
        "`evidence_map`；操作时间线见 `operation_log.jsonl`。",
        "",
        "## 不确定与待人工确认",
    ]
    for u in result["uncertainty"]:
        md_lines.append("- %s：%s" % (u["item"], u["reason"]))
    for p in result["pending_human"]:
        md_lines.append("- %s：%s" % (p["item"], p["reason"]))
    md_lines.append("")
    with open(md_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(md_lines))

    return [manifest_path, tree_path, dedup_path, recovery_path, index_path,
            md_path]


# --------------------------------------------------------------------------- #
# Operation log
# --------------------------------------------------------------------------- #

def append_op(ops, step, detail, actor="scripts/core_impl.py"):
    ops.append({
        "time": _now_iso(),
        "actor": actor,
        "step": step,
        "detail": detail,
    })


# --------------------------------------------------------------------------- #
# Modes: skeleton / update / check
# --------------------------------------------------------------------------- #

def run_skeleton(args):
    os.makedirs(args.output_dir, exist_ok=True)
    case_id, contract, errors = load_case_meta(args.case_json)
    result = build_skeleton(case_id, contract)
    ops = []
    append_op(ops, "skeleton", "wrote partial result skeleton; case_id=%s"
              % (case_id or "unknown"))
    result["change_log"] = ops
    result["errors"] = errors
    if errors:
        result["status"] = "input_error"
    _dump_json(os.path.join(args.output_dir, "result.json"), result)
    return 0


def run_update(args):
    result_path = args.result
    patch_path = args.update
    if not os.path.isfile(result_path):
        print("result.json not found: %s" % result_path, file=sys.stderr)
        return 2
    with open(result_path, "r", encoding="utf-8-sig") as fh:
        result = json.load(fh)
    with open(patch_path, "r", encoding="utf-8-sig") as fh:
        patch = json.load(fh)
    for key, value in patch.items():
        if key in RESERVED_KEYS and key in result:
            result.setdefault("computed", {})
            result["computed"]["_contract_%s" % key] = value
        elif key in RESERVED_KEYS:
            # reserved fields come from the patch only when absent
            if result.get(key) in (None, "", [], {}):
                result[key] = value
        else:
            result[key] = value
    append_op(result["change_log"], "update", "merged patch %s" % patch_path,
              actor="scripts/core_impl.py --update")
    _dump_json(result_path, result)
    return 0


def run_check(args):
    problems = []
    try:
        with open(args.check, "r", encoding="utf-8-sig") as fh:
            result = json.load(fh)
    except (OSError, ValueError) as exc:
        print("FAIL: cannot parse result.json: %s" % exc)
        return 1
    if not result.get("case_id"):
        problems.append("case_id empty")
    if result.get("status") not in ALLOWED_STATUS:
        problems.append("status invalid: %r" % result.get("status"))
    case_id, contract, errors = load_case_meta(args.case_json)
    if contract:
        for key in contract:
            if key in RESERVED_KEYS:
                if "computed" not in result or \
                   "_contract_%s" % key not in result.get("computed", {}):
                    problems.append("contract collision field %s not stored "
                                    "under computed" % key)
            elif key not in result:
                problems.append("missing contract field %s" % key)
    if not result.get("sources"):
        problems.append("sources empty")
    if not result.get("evidence_map", {}).get("conclusions"):
        problems.append("evidence_map.conclusions empty")
    if not result.get("change_log"):
        problems.append("change_log empty")
    for p in problems:
        print("FAIL:", p)
    if problems:
        return 1
    print("ok", result["status"], result["case_id"])
    return 0


# --------------------------------------------------------------------------- #
# Full pipeline
# --------------------------------------------------------------------------- #

def run_full(args):
    os.makedirs(args.output_dir, exist_ok=True)
    os.makedirs(args.work_dir, exist_ok=True)
    ops = []
    started = time.time()
    append_op(ops, "start", "full pipeline start")

    case_id, contract, case_errors = load_case_meta(args.case_json)
    append_op(ops, "read_case", "case_id=%s; contract=%s"
              % (case_id, "present" if contract else "absent"))

    task_lines = load_task_lines(args.task_path)
    append_op(ops, "read_task", "task.md lines=%d" % len(task_lines))

    files, dirs, control, walk_errors = walk_input(args.input_root,
                                                   args.size_limit)
    input_abs = os.path.abspath(args.input_root)
    append_op(ops, "inventory", "files=%d dirs=%d control=%d errors=%d"
              % (len(files), len(dirs), len(control), len(walk_errors)))

    dedup = dedup_groups(files)
    versions = version_candidates(files, input_abs)
    aliases = alias_groups(files)
    version_notes = parse_version_notes(files, args.size_limit, input_abs)
    append_op(ops, "dedup_version_alias",
              "dup_groups=%d version_chains=%d symlinks=%d hardlink_groups=%d "
              "version_notes=%d" % (len(dedup), len(versions),
                                    len(aliases["symlinks"]),
                                    len(aliases["hardlink_groups"]),
                                    len(version_notes)))

    catalog_records, catalog_errors = parse_catalogs(files, input_abs)
    append_op(ops, "catalog", "records=%d errors=%d"
              % (len(catalog_records), len(catalog_errors)))

    recon = reconcile(files, catalog_records)
    index = build_index(files, catalog_records)
    answers = answer_questions(files, dirs, dedup, versions, aliases, recon,
                               index)
    append_op(ops, "reconcile_index_answer",
              "reconcile=%s terms=%d answers=%d"
              % (json.dumps(recon["counts"]), index["term_count"],
                 len(answers)))

    runtime = {
        "started_at": _now_iso(),
        "duration_s": round(time.time() - started, 3),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "size_limit_bytes": args.size_limit,
    }
    append_op(ops, "finalize", "runtime=%s" % json.dumps(runtime))

    result = assemble_full(case_id, contract, task_lines, files, dirs,
                           control, walk_errors, dedup, versions, aliases,
                           version_notes, catalog_records, catalog_errors,
                           recon, index, answers, runtime, ops, case_errors)

    _dump_json(os.path.join(args.work_dir, "inventory.json"), {
        "files": files, "dirs": dirs, "control": control})
    _dump_json(os.path.join(args.work_dir, "library.json"), {
        "dedup": dedup, "versions": versions, "aliases": aliases,
        "reconciliation": recon})

    write_artifacts(args.output_dir, files, dirs, control, dedup, versions,
                    aliases, version_notes, recon, index, answers, result,
                    catalog_errors, task_lines)

    # operation_log.jsonl
    with open(os.path.join(args.output_dir, "operation_log.jsonl"), "w",
              encoding="utf-8", newline="\n") as fh:
        for op in ops:
            fh.write(json.dumps(op, ensure_ascii=False) + "\n")

    _dump_json(os.path.join(args.output_dir, "result.json"), result)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scripts/core_impl.py",
        description="Document library repair (stdlib only, deterministic).")
    p.add_argument("--input-root", default=None)
    p.add_argument("--output-dir", default=None)
    p.add_argument("--work-dir", default=None)
    p.add_argument("--case-json", default=None)
    p.add_argument("--task-path", default=None)
    p.add_argument("--size-limit", type=int, default=DEFAULT_SIZE_LIMIT)
    p.add_argument("--skeleton-only", action="store_true",
                   help="write only a legal partial result skeleton")
    p.add_argument("--update", metavar="PATCH.json", default=None,
                   help="merge a semantic patch into an existing result")
    p.add_argument("--result", metavar="RESULT.json", default=None,
                   help="result file target for --update")
    p.add_argument("--check", metavar="RESULT.json", default=None,
                   help="validate a result.json against the case contract")
    p.add_argument("--version", action="version",
                   version="%s %s" % (SKILL_NAME, VERSION))
    return p


def main(argv=None):
    args = _build_parser().parse_args(argv)
    if args.input_root is None:
        args.input_root = _default_input()
    if args.output_dir is None:
        args.output_dir = _default_output()
    if args.work_dir is None:
        args.work_dir = _default_work()
    if args.case_json is None:
        args.case_json = os.path.join(args.input_root, "case.json")
    if args.task_path is None:
        args.task_path = os.path.join(args.input_root, "task.md")

    if args.check:
        return run_check(args)
    if args.update:
        return run_update(args)
    if args.skeleton_only:
        return run_skeleton(args)
    return run_full(args)


if __name__ == "__main__":
    raise SystemExit(main())
